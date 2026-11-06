"""Regression checks for Neo4j snapshot isolation and raw descriptions."""

import csv
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from neo4j import GraphDatabase


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("neo4j_import", ROOT / "scripts/graph/import_neo4j.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


class InputFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.nodes = Path(self.temp.name) / "nodes.csv"
        self.edges = Path(self.temp.name) / "edges.csv"
        self.write_csv(self.nodes, ["id", "location", "products", "country", "product_category"], [
            ["n1", "Japan", "Steel", "Japan", "METALS"],
            ["n2", "Japan", " Steel ", "Japan", "METALS"],
            ["n3", "", "   ", "None", "None"],
        ])
        self.write_csv(self.edges, ["source", "target"], [["n1", "n2"]])
        self.nodes.with_suffix(".metadata.json").write_text(json.dumps({
            "model": "test-model", "modelRevision": "test-revision", "taxonomyVersion": "1.0",
        }), encoding="utf-8")
        self.nodes.with_suffix(".prediction_details.json").write_text(
            json.dumps({"Steel": {"score": 0.7, "category": "METALS"}}), encoding="utf-8"
        )
        self.nodes_patch = patch.object(importer, "NODES", self.nodes)
        self.edges_patch = patch.object(importer, "EDGES", self.edges)
        self.nodes_patch.start()
        self.edges_patch.start()
        self.addCleanup(self.nodes_patch.stop)
        self.addCleanup(self.edges_patch.stop)

    @staticmethod
    def write_csv(path, fields, rows):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(fields)
            writer.writerows(rows)


class ImportPreflightTest(InputFixture):
    def test_incompatible_or_missing_snapshot_is_rejected_before_writes(self):
        for companies, links in ((1, 0), (0, 1)):
            with self.subTest(companies=companies, links=links):
                session = MagicMock()
                session.__enter__.return_value = session
                session.run.return_value.single.return_value = {
                    "incompatible_companies": companies, "incompatible_links": links,
                }
                driver = MagicMock()
                driver.__enter__.return_value = driver
                driver.session.return_value = session
                with patch.object(importer.GraphDatabase, "driver", return_value=driver):
                    with self.assertRaisesRegex(ValueError, "different or unknown snapshot"):
                        importer.main()
                self.assertEqual(session.run.call_count, 1)
                session.execute_write.assert_not_called()
                driver.__exit__.assert_called_once()


@unittest.skipUnless(os.getenv("NEO4J_TEST_URI"), "requires a disposable Neo4j test database")
class Neo4jImportIntegrationTest(InputFixture):
    def test_same_snapshot_is_repeatable_and_different_snapshot_is_rejected(self):
        uri = os.environ["NEO4J_TEST_URI"]
        password = os.environ["NEO4J_TEST_PASSWORD"]
        with patch.dict(os.environ, {
            "NEO4J_URI": uri, "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": password, "NEO4J_DATABASE": "neo4j",
        }):
            importer.main()
            with GraphDatabase.driver(uri, auth=("neo4j", password)) as driver:
                with driver.session(database="neo4j") as session:
                    def snapshot():
                        companies = [dict(record) for record in session.run(
                            "MATCH (c:AnonNetCompany) RETURN c.anonnetId AS id, c.snapshotId AS snapshot ORDER BY id"
                        )]
                        descriptions = [dict(record) for record in session.run(
                            "MATCH (c:AnonNetCompany)-[:REPORTED_DESCRIPTION]->(d:ProductDescription) "
                            "RETURN c.anonnetId AS company, d.descriptionId AS id, d.text AS text ORDER BY company"
                        )]
                        links = session.run("MATCH ()-[r:REPORTED_SUPPLY_LINK]->() RETURN count(r) AS n").single()["n"]
                        return companies, descriptions, links

                    before = snapshot()
                    self.assertEqual([row["text"] for row in before[1]], ["Steel", " Steel "])
                    self.assertEqual(
                        [row["id"] for row in before[1]],
                        [hashlib.sha256(value.encode()).hexdigest() for value in ("Steel", " Steel ")],
                    )
                    self.assertEqual(before[2], 1)
                    importer.main()
                    self.assertEqual(snapshot(), before)
                    original_edges = self.edges.read_bytes()
                    self.write_csv(self.edges, ["source", "target"], [["n2", "n1"]])
                    with self.assertRaisesRegex(ValueError, "different or unknown snapshot"):
                        importer.main()
                    self.assertEqual(snapshot(), before)
                    self.edges.write_bytes(original_edges)
                    original_nodes = self.nodes.read_bytes()
                    self.nodes.write_bytes(original_nodes + b"\n")
                    with self.assertRaisesRegex(ValueError, "different or unknown snapshot"):
                        importer.main()
                    self.assertEqual(snapshot(), before)
                    self.nodes.write_bytes(original_nodes)
                    session.run("CREATE (:AnonNetCompany {anonnetId: 'legacy'})").consume()
                    with_unknown = snapshot()
                    with self.assertRaisesRegex(ValueError, "different or unknown snapshot"):
                        importer.main()
                    self.assertEqual(snapshot(), with_unknown)


if __name__ == "__main__":
    unittest.main()
