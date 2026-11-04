"""Construct a typed AnonNet MultiDiGraph without importing into Neo4j."""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path

import networkx as nx


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_rows(path: Path, columns: list[str]) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != columns:
            raise ValueError(f"{path}: unexpected CSV header {reader.fieldnames!r}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{path}: malformed CSV row")
    return rows


def build_graph(nodes_path: Path, edges_path: Path) -> nx.MultiDiGraph:
    """Keep original edge direction, raw attributes, self-loops, and repeated rows."""
    nodes = read_rows(nodes_path, ["id", "location", "products"])
    edges = read_rows(edges_path, ["source", "target"])
    graph = nx.MultiDiGraph(
        dataset="AnonNet",
        nodes_sha256=hashlib.sha256(nodes_path.read_bytes()).hexdigest(),
        edges_sha256=hashlib.sha256(edges_path.read_bytes()).hexdigest(),
        supply_direction="CSV source to target; business meaning unverified",
        location_normalization="strip leading and trailing whitespace only",
        product_normalization="none; entire raw field is one description",
    )
    seen: set[str] = set()
    for row in nodes:
        node_id = row["id"]
        if not node_id or node_id != node_id.strip() or node_id in seen:
            raise ValueError(f"Blank, whitespace-padded, or duplicate company ID: {node_id!r}")
        seen.add(node_id)
        company = f"company:{node_id}"
        graph.add_node(company, kind="Company", source_id=node_id,
                       location_raw=row["location"], products_raw=row["products"])

        location = row["location"].strip()
        if location:
            location_id = f"location:{digest(location)}"
            graph.add_node(location_id, kind="LocationLabel", label=location)
            graph.add_edge(company, location_id, key="location", relation="REPORTED_LOCATION",
                           source_file=nodes_path.name, raw_value=row["location"])

        # One raw CSV cell is one description. It may contain multiple products.
        description = row["products"]
        if description.strip():
            product_id = f"description:{digest(description)}"
            graph.add_node(product_id, kind="ProductDescription", label=description)
            graph.add_edge(company, product_id, key="description", relation="REPORTED_DESCRIPTION",
                           source_file=nodes_path.name, raw_value=description)

    for line, row in enumerate(edges, start=2):
        source, target = row["source"], row["target"]
        if source not in seen or target not in seen:
            raise ValueError(f"{edges_path}:{line}: unknown endpoint {source!r} -> {target!r}")
        graph.add_edge(f"company:{source}", f"company:{target}", key=f"supply:{line}",
                       relation="REPORTED_SUPPLY_LINK", source_file=edges_path.name,
                       source_line=line, direction_meaning="unverified")
    return graph


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("dataset"))
    parser.add_argument("--output", type=Path, default=Path("dataset/derived/anonnet-heterogeneous.graphml"))
    args = parser.parse_args()
    graph = build_graph(args.data_dir / "nodes_anon.csv", args.data_dir / "edges_anon.csv")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml_xml(graph, args.output, prettyprint=False)
    print(f"Saved {graph.number_of_nodes():,} nodes and {graph.number_of_edges():,} edges to {args.output}")


if __name__ == "__main__":
    main()
