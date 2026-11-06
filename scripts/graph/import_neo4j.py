"""Import the AnonNet heterogeneous graph into the local Neo4j database."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

from neo4j import GraphDatabase


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "dataset"
NODES = DATA / "derived" / "nodes_anon_categorized.csv"
EDGES = DATA / "edges_anon.csv"
BATCH_SIZE = 1_000


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def chunks(rows: list[dict], size: int = BATCH_SIZE):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def write_batch(session, query: str, rows: list[dict]) -> None:
    # One managed transaction per batch keeps writes grouped and resumable.
    session.execute_write(lambda tx, batch: tx.run(query, rows=batch).consume(), rows)


def ensure_snapshot_compatible(session, snapshot_id: str, metadata_hash: str, details_hash: str) -> None:
    """Reject a different or unidentifiable input snapshot before writing."""
    result = session.run(
        """
        CALL {
            MATCH (c:AnonNetCompany)
            WHERE c.snapshotId IS NULL OR c.snapshotId <> $snapshot_id
               OR c.categoryMetadataSha256 IS NULL OR c.categoryMetadataSha256 <> $metadata_hash
               OR c.predictionDetailsSha256 IS NULL OR c.predictionDetailsSha256 <> $details_hash
            RETURN count(c) AS incompatible_companies
        }
        CALL {
            MATCH ()-[r:REPORTED_SUPPLY_LINK]->()
            WHERE r.snapshotId IS NULL OR r.snapshotId <> $snapshot_id
            RETURN count(r) AS incompatible_links
        }
        RETURN incompatible_companies, incompatible_links
        """,
        snapshot_id=snapshot_id,
        metadata_hash=metadata_hash,
        details_hash=details_hash,
    ).single()
    if result["incompatible_companies"] or result["incompatible_links"]:
        raise ValueError(
            "The database contains AnonNet data from a different or unknown snapshot or classification input; "
            "import into an empty database or the matching snapshot."
        )


def main() -> None:
    node_rows = read_csv(NODES)
    edge_rows = read_csv(EDGES)
    company_ids: set[str] = set()
    for source_record, row in enumerate(node_rows, start=1):
        company_id = row["id"]
        if not company_id or company_id != company_id.strip():
            raise ValueError(
                f"{NODES}: data record {source_record}: "
                f"blank or whitespace-padded company ID: {company_id!r}"
            )
        if company_id in company_ids:
            raise ValueError(
                f"{NODES}: data record {source_record}: duplicate company ID: {company_id!r}"
            )
        company_ids.add(company_id)
    for source_record, row in enumerate(edge_rows, start=1):
        source, target = row["source"], row["target"]
        if source not in company_ids or target not in company_ids:
            raise ValueError(
                f"{EDGES}: data record {source_record}: "
                f"unknown endpoint {source!r} -> {target!r}"
            )
    metadata_bytes = NODES.with_suffix(".metadata.json").read_bytes()
    details_bytes = NODES.with_suffix(".prediction_details.json").read_bytes()
    metadata = json.loads(metadata_bytes)
    predictions = json.loads(details_bytes)
    metadata_hash = hashlib.sha256(metadata_bytes).hexdigest()
    details_hash = hashlib.sha256(details_bytes).hexdigest()
    node_hash = hashlib.sha256(NODES.read_bytes()).hexdigest()
    edge_hash = hashlib.sha256(EDGES.read_bytes()).hexdigest()
    snapshot_id = f"anonnet:{node_hash}:{edge_hash}"

    companies, locations, descriptions, categories = [], {}, {}, {}
    location_links, description_links, category_links = [], [], []
    for source_record, row in enumerate(node_rows, start=1):
        company_id = row["id"]
        description = row["products"]
        description_key = description.strip()
        category_id = row["product_category"].strip()
        country = row["country"].strip()
        companies.append({
            "anonnetId": company_id,
            "locationRaw": row["location"] or None,
            "productsRaw": row["products"] or None,
            "snapshotId": snapshot_id,
            "nodeFileSha256": node_hash,
            "categoryMetadataSha256": metadata_hash,
            "predictionDetailsSha256": details_hash,
            "sourceRecord": source_record,
        })
        if country and country.casefold() not in {"none", "null", "unknown", "n/a", "na"}:
            locations[country] = {"label": country}
            location_links.append({"company": company_id, "label": country})
        if description_key:
            description_id = hashlib.sha256(description.encode("utf-8")).hexdigest()
            descriptions[description_id] = {"descriptionId": description_id, "text": description}
            description_links.append({"company": company_id, "descriptionId": description_id})
        if category_id and category_id.casefold() not in {"none", "null", "unknown", "n/a", "na"}:
            prediction = predictions.get(description_key)
            if not description_key or not prediction or prediction.get("category") != category_id:
                raise ValueError(f"Prediction details disagree for {company_id}")
            categories[category_id] = {"categoryId": category_id, "label": category_id}
            category_links.append({
                "company": company_id,
                "categoryId": category_id,
                "score": float(prediction["score"]),
                "model": metadata["model"],
                "modelRevision": metadata["modelRevision"],
                "taxonomyVersion": metadata["taxonomyVersion"],
            })

    edges = [
        {
            "source": row["source"],
            "target": row["target"],
            "snapshotId": snapshot_id,
            "edgeFileSha256": edge_hash,
            "sourceRecord": source_record,
            "directionMeaning": "file_source_to_target_business_direction_unverified",
            "isSelfLoop": row["source"] == row["target"],
        }
        for source_record, row in enumerate(edge_rows, start=1)
    ]

    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "supplygraph-local-only")),
    )
    driver.verify_connectivity()
    with driver, driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
        ensure_snapshot_compatible(session, snapshot_id, metadata_hash, details_hash)
        # Stable keys make rerunning this script safe for the same snapshot.
        for query in (
            "CREATE CONSTRAINT anonnet_company_id IF NOT EXISTS FOR (n:AnonNetCompany) REQUIRE n.anonnetId IS UNIQUE",
            "CREATE CONSTRAINT anonnet_location_label IF NOT EXISTS FOR (n:LocationLabel) REQUIRE n.label IS UNIQUE",
            "CREATE CONSTRAINT anonnet_description_id IF NOT EXISTS FOR (n:ProductDescription) REQUIRE n.descriptionId IS UNIQUE",
            "CREATE CONSTRAINT anonnet_category_id IF NOT EXISTS FOR (n:ProductCategory) REQUIRE n.categoryId IS UNIQUE",
        ):
            session.run(query).consume()

        for query, rows in (
            ("UNWIND $rows AS row MERGE (n:Company:AnonNetCompany {anonnetId: row.anonnetId}) SET n += row", companies),
            ("UNWIND $rows AS row MERGE (n:LocationLabel {label: row.label})", list(locations.values())),
            ("UNWIND $rows AS row MERGE (n:ProductDescription {descriptionId: row.descriptionId}) SET n.text = row.text", list(descriptions.values())),
            ("UNWIND $rows AS row MERGE (n:ProductCategory {categoryId: row.categoryId}) SET n.label = row.label", list(categories.values())),
        ):
            for batch in chunks(rows):
                write_batch(session, query, batch)

        # Keep the CSV source/target direction and source line on each edge.
        supply_query = """
        UNWIND $rows AS row
        MATCH (a:AnonNetCompany {anonnetId: row.source})
        MATCH (b:AnonNetCompany {anonnetId: row.target})
        MERGE (a)-[r:REPORTED_SUPPLY_LINK {snapshotId: row.snapshotId, sourceRecord: row.sourceRecord}]->(b)
        SET r += row
        """
        for batch in chunks(edges):
            write_batch(session, supply_query, batch)

        for query, rows in (
            ("UNWIND $rows AS row MATCH (c:AnonNetCompany {anonnetId: row.company}), (l:LocationLabel {label: row.label}) MERGE (c)-[:REPORTED_LOCATION]->(l)", location_links),
            ("UNWIND $rows AS row MATCH (c:AnonNetCompany {anonnetId: row.company}), (d:ProductDescription {descriptionId: row.descriptionId}) MERGE (c)-[:REPORTED_DESCRIPTION]->(d)", description_links),
            ("UNWIND $rows AS row MATCH (c:AnonNetCompany {anonnetId: row.company}), (k:ProductCategory {categoryId: row.categoryId}) MERGE (c)-[r:PREDICTED_CATEGORY]->(k) SET r.score = row.score, r.model = row.model, r.modelRevision = row.modelRevision, r.taxonomyVersion = row.taxonomyVersion", category_links),
        ):
            for batch in chunks(rows):
                write_batch(session, query, batch)

        counts = {}
        for name, query in {
            "companies": "MATCH (n:AnonNetCompany) RETURN count(n) AS count",
            "supplyLinks": "MATCH ()-[r:REPORTED_SUPPLY_LINK]->() RETURN count(r) AS count",
            "locations": "MATCH (n:LocationLabel) RETURN count(n) AS count",
            "locationLinks": "MATCH ()-[r:REPORTED_LOCATION]->() RETURN count(r) AS count",
            "descriptions": "MATCH (n:ProductDescription) RETURN count(n) AS count",
            "descriptionLinks": "MATCH ()-[r:REPORTED_DESCRIPTION]->() RETURN count(r) AS count",
            "categories": "MATCH (n:ProductCategory) RETURN count(n) AS count",
            "categoryLinks": "MATCH ()-[r:PREDICTED_CATEGORY]->() RETURN count(r) AS count",
        }.items():
            counts[name] = session.run(query).single()["count"]
        print(counts)


if __name__ == "__main__":
    main()
