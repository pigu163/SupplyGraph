"""Construct a typed AnonNet MultiDiGraph without importing into Neo4j."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import networkx as nx


ROOT = Path(__file__).resolve().parents[2]


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
    with nodes_path.open(encoding="utf-8-sig", newline="") as handle:
        columns = next(csv.reader(handle))
    if columns not in (["id", "location", "products"],
                       ["id", "location", "products", "country", "product_category"]):
        raise ValueError(f"Unexpected node CSV header: {columns}")
    nodes = read_rows(nodes_path, columns)
    edges = read_rows(edges_path, ["source", "target"])
    categorized = "product_category" in columns
    metadata_path = nodes_path.with_suffix(".metadata.json")
    details_path = nodes_path.with_suffix(".prediction_details.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if categorized and metadata_path.exists() else {}
    details = json.loads(details_path.read_text(encoding="utf-8")) if categorized and details_path.exists() else {}
    raw_path = nodes_path.parent.parent / "nodes_anon.csv"
    raw_verified = False
    if metadata.get("inputSha256") and raw_path.exists():
        if hashlib.sha256(raw_path.read_bytes()).hexdigest() != metadata["inputSha256"]:
            raise ValueError("Categorized CSV metadata does not match the raw nodes CSV")
        raw_rows = read_rows(raw_path, ["id", "location", "products"])
        if len(raw_rows) != len(nodes) or any(
            any(raw[key] != derived[key] for key in ("id", "location", "products"))
            for raw, derived in zip(raw_rows, nodes, strict=True)
        ):
            raise ValueError("Categorized CSV changed or reordered original node fields")
        raw_verified = True
    graph = nx.MultiDiGraph(
        dataset="AnonNet",
        nodes_sha256=hashlib.sha256(nodes_path.read_bytes()).hexdigest(),
        edges_sha256=hashlib.sha256(edges_path.read_bytes()).hexdigest(),
        supply_direction="CSV source to target; business meaning unverified",
        location_normalization=("country field, stripped; unknown markers omitted" if categorized
                                else "location field, stripped; unknown markers omitted"),
        product_normalization="none; entire raw field is one description",
        nodes_file=nodes_path.name,
        node_input_kind="categorized" if categorized else "raw",
        raw_nodes_sha256=metadata.get("inputSha256", "unknown"),
        raw_nodes_verified=raw_verified,
        category_model=metadata.get("model", "unknown"),
        category_model_revision=metadata.get("modelRevision", "unknown"),
        category_taxonomy_version=metadata.get("taxonomyVersion", "unknown"),
        category_metadata_sha256=hashlib.sha256(metadata_path.read_bytes()).hexdigest() if metadata else "unavailable",
        category_details_sha256=hashlib.sha256(details_path.read_bytes()).hexdigest() if details else "unavailable",
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

        location = row["country"].strip() if categorized else row["location"].strip()
        if location.casefold() in {"none", "null", "unknown", "n/a", "na"}:
            location = ""
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

        if categorized:
            category = row["product_category"].strip()
            if category.casefold() in {"none", "null", "unknown", "n/a", "na", ""}:
                category = ""
            if category and not description.strip():
                raise ValueError(f"Category without product description: {node_id}")
            if category:
                if metadata.get("categories") and category not in metadata["categories"]:
                    raise ValueError(f"Unknown category for {node_id}: {category}")
                if details:
                    prediction = details.get(description.strip())
                    if not prediction or prediction.get("category") != category:
                        raise ValueError(f"Prediction details disagree for {node_id}")
                    graph.nodes[company]["category_score"] = float(prediction["score"])
                category_id = f"category:{category}"
                graph.add_node(category_id, kind="ProductCategory", label=category)
                graph.add_edge(company, category_id, key="category", relation="PREDICTED_CATEGORY",
                               source_file=nodes_path.name, derivation="model_prediction")

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
    parser.add_argument("--data-dir", type=Path, default=ROOT / "dataset")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dataset/derived/anonnet-heterogeneous-categorized.graphml",
    )
    args = parser.parse_args()
    graph = build_graph(args.data_dir / "derived" / "nodes_anon_categorized.csv", args.data_dir / "edges_anon.csv")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml_xml(graph, args.output, prettyprint=False)
    print(f"Saved {graph.number_of_nodes():,} nodes and {graph.number_of_edges():,} edges to {args.output}")


if __name__ == "__main__":
    main()
