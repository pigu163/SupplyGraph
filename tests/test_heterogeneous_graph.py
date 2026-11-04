"""Check provenance and relation semantics using a small, disposable CSV fixture."""

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

import networkx as nx


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


construct = load_module("construct", ROOT / "scripts/graph_construct/construct.py")
analysis = load_module("analysis", ROOT / "scripts/graph_construct/analyze.py")


class HeterogeneousGraphTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.nodes = Path(self.temp.name) / "nodes.csv"
        self.edges = Path(self.temp.name) / "edges.csv"
        self.write(self.nodes, ["id", "location", "products"], [
            ["n1", "Virgin Islands, British", "Steel, Wire"],
            ["n2", " Virgin Islands, British ", "Steel, Wire"],
            ["n3", "", ""],
        ])
        self.write(self.edges, ["source", "target"], [
            ["n1", "n2"], ["n1", "n2"], ["n2", "n1"], ["n1", "n1"],
        ])

    @staticmethod
    def write(path, columns, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)

    def test_typed_nodes_and_source_edges_survive_graphml_roundtrip(self):
        graph = construct.build_graph(self.nodes, self.edges)
        self.assertEqual((graph.number_of_nodes(), graph.number_of_edges()), (5, 8))
        locations = [n for n, d in graph.nodes(data=True) if d["kind"] == "LocationLabel"]
        products = [n for n, d in graph.nodes(data=True) if d["kind"] == "ProductDescription"]
        self.assertEqual(len(locations), 1)
        self.assertEqual(graph.nodes[locations[0]]["label"], "Virgin Islands, British")
        self.assertEqual(graph.nodes[products[0]]["label"], "Steel, Wire")
        self.assertEqual(graph.nodes["company:n2"]["location_raw"], " Virgin Islands, British ")
        self.assertEqual(graph.number_of_edges("company:n1", "company:n2"), 2)
        self.assertEqual(graph.number_of_edges("company:n2", "company:n1"), 1)
        self.assertEqual(graph.number_of_edges("company:n1", "company:n1"), 1)
        self.assertEqual(graph.out_degree("company:n3"), 0)

        path = Path(self.temp.name) / "graph.graphml"
        nx.write_graphml_xml(graph, path)
        restored = nx.read_graphml(path, force_multigraph=True)
        self.assertIsInstance(restored, nx.MultiDiGraph)
        self.assertEqual((restored.number_of_nodes(), restored.number_of_edges()), (5, 8))
        self.assertEqual(restored.number_of_edges("company:n1", "company:n2"), 2)
        self.assertEqual(restored.nodes[products[0]]["label"], "Steel, Wire")

        supply, location_layer, description_layer = analysis.project(restored)
        self.assertEqual(supply.number_of_edges(), 3)  # parallel identical rows collapse only here
        self.assertEqual(location_layer.number_of_edges(), 2)
        self.assertEqual(description_layer.number_of_edges(), 2)
        self.assertTrue(nx.is_bipartite(location_layer))
        self.assertTrue(nx.is_bipartite(description_layer))
        first = analysis.analyze(restored, samples=3, trials=3, seed=42)
        second = analysis.analyze(restored, samples=3, trials=3, seed=42)
        self.assertEqual(first, second)
        self.assertEqual(first["supply"]["source_rows_collapsed_in_simple_projection"], 1)

    def test_unknown_endpoint_is_an_error_not_an_implicit_company(self):
        self.write(self.edges, ["source", "target"], [["n1", "missing"]])
        with self.assertRaisesRegex(ValueError, "unknown endpoint"):
            construct.build_graph(self.nodes, self.edges)

    def test_duplicate_company_is_rejected(self):
        self.write(self.nodes, ["id", "location", "products"], [["n1", "", ""], ["n1", "", ""]])
        with self.assertRaisesRegex(ValueError, "duplicate company ID"):
            construct.build_graph(self.nodes, self.edges)

    def test_invalid_typed_edge_is_rejected(self):
        graph = construct.build_graph(self.nodes, self.edges)
        graph.add_edge("company:n1", "company:n2", relation="REPORTED_LOCATION")
        with self.assertRaisesRegex(ValueError, "endpoint kinds"):
            analysis.project(graph)

    def test_failure_scenario_does_not_change_original_graph(self):
        graph = nx.path_graph(5)
        self.assertEqual(analysis.largest_after_removal(graph, [2]), 2)
        self.assertEqual(graph.number_of_nodes(), 5)
        self.assertEqual(graph.number_of_edges(), 4)


if __name__ == "__main__":
    unittest.main()
