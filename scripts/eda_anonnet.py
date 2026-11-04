"""Profile the local AnonNet CSVs without writing to Neo4j or changing data files."""

import argparse
import csv
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path

import networkx as nx


def read_csv(path: Path, expected_columns: list[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != expected_columns:
            raise ValueError(f"Unexpected header in {path}: {reader.fieldnames}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"Malformed CSV row in {path}")
    return rows


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def duplicate_rows(values: list) -> int:
    return sum(count - 1 for count in Counter(values).values() if count > 1)


def degree_summary(degrees: list[tuple[str, int]]) -> dict:
    values = sorted(value for _, value in degrees)
    return {
        "zero": values.count(0),
        "median": statistics.median(values),
        "p90_floor_index": values[int(0.90 * (len(values) - 1))],
        "p99_floor_index": values[int(0.99 * (len(values) - 1))],
        "max": values[-1],
        "top_five": sorted(degrees, key=lambda pair: (-pair[1], pair[0]))[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("dataset"))
    args = parser.parse_args()

    node_path = args.data_dir / "nodes_anon.csv"
    edge_path = args.data_dir / "edges_anon.csv"
    nodes = read_csv(node_path, ["id", "location", "products"])
    edges = read_csv(edge_path, ["source", "target"])
    if not nodes:
        raise ValueError("Node CSV is empty")

    ids = [row["id"].strip() for row in nodes]
    id_set = set(ids)
    edge_pairs = [(row["source"].strip(), row["target"].strip()) for row in edges]
    missing_source = sum(source not in id_set for source, _ in edge_pairs)
    missing_target = sum(target not in id_set for _, target in edge_pairs)

    graph = nx.DiGraph()
    graph.add_nodes_from(ids)
    graph.add_edges_from((s, t) for s, t in edge_pairs if s in id_set and t in id_set)
    weak = sorted((len(c) for c in nx.weakly_connected_components(graph)), reverse=True)
    strong = sorted((len(c) for c in nx.strongly_connected_components(graph)), reverse=True)
    no_self_loops = graph.copy()
    no_self_loops.remove_edges_from(list(nx.selfloop_edges(no_self_loops)))
    undirected = graph.to_undirected()

    # This is a structural illustration: remove the highest source-side degree
    # node in memory, then compare undirected connected components.
    highest_source = sorted(graph.out_degree(), key=lambda pair: (-pair[1], pair[0]))[0]
    scenario = undirected.copy()
    scenario.remove_node(highest_source[0])
    after = sorted((len(c) for c in nx.connected_components(scenario)), reverse=True)

    locations = Counter(row["location"].strip() for row in nodes if row["location"].strip())
    out = {
        "files": {
            "nodes": {"bytes": node_path.stat().st_size, "sha256": sha256(node_path), "rows": len(nodes)},
            "edges": {"bytes": edge_path.stat().st_size, "sha256": sha256(edge_path), "rows": len(edges)},
        },
        "quality": {
            "blank_node_ids": sum(not value for value in ids),
            "duplicate_node_id_rows": duplicate_rows(ids),
            "blank_location": sum(not row["location"].strip() for row in nodes),
            "blank_products": sum(not row["products"].strip() for row in nodes),
            "blank_both_location_and_products": sum(not r["location"].strip() and not r["products"].strip() for r in nodes),
            "location_values_with_surrounding_whitespace": sum(r["location"] != r["location"].strip() for r in nodes),
            "duplicate_directed_edge_rows": duplicate_rows(edge_pairs),
            "missing_source_endpoint_rows": missing_source,
            "missing_target_endpoint_rows": missing_target,
            "self_loop_rows": [s for s, t in edge_pairs if s == t],
        },
        "attributes": {
            "top_location_labels_excluding_blank": locations.most_common(10),
            "node_rows_with_comma_in_location": sum("," in r["location"] for r in nodes),
            "node_rows_with_comma_in_products": sum("," in r["products"] for r in nodes),
            "example_comma_in_location": next((r["location"] for r in nodes if "," in r["location"]), None),
            "example_repeated_product_string": next((r["products"] for r in nodes if r["id"] == "n9"), None),
        },
        "graph": {
            "node_count": graph.number_of_nodes(),
            "distinct_directed_edge_count": graph.number_of_edges(),
            "source_side_degree": degree_summary(list(graph.out_degree())),
            "target_side_degree": degree_summary(list(graph.in_degree())),
            "weak_component_count": len(weak),
            "largest_weak_component": weak[0],
            "strong_component_count": len(strong),
            "largest_strong_component": strong[0],
            "nontrivial_strong_component_count": sum(size > 1 for size in strong),
            "reciprocal_directed_edge_fraction_excluding_self_loops": nx.reciprocity(no_self_loops),
            "undirected_articulation_nodes": len(list(nx.articulation_points(undirected))),
            "example_undirected_node_removal": {
                "node": highest_source[0],
                "source_side_degree": highest_source[1],
                "components_after_removal": len(after),
                "largest_component_after_removal": after[0] if after else 0,
                "other_nodes_after_removal": sum(after[1:]),
            },
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
