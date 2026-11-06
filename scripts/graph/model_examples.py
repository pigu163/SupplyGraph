"""Reproduce the read-only examples in the proposed Neo4j model document."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NODE_FILE = ROOT / "dataset" / "nodes_anon.csv"
EDGE_FILE = ROOT / "dataset" / "edges_anon.csv"


def main() -> None:
    with NODE_FILE.open(encoding="utf-8-sig", newline="") as f:
        nodes = list(csv.DictReader(f))
    with EDGE_FILE.open(encoding="utf-8-sig", newline="") as f:
        edges = list(csv.DictReader(f))

    node_by_id = {row["id"]: row for row in nodes}
    if len(node_by_id) != len(nodes) or "n0" not in node_by_id:
        raise ValueError("Node IDs must be unique and include n0")
    outgoing, incoming = defaultdict(set), defaultdict(set)
    edge_record = {}
    for record, row in enumerate(edges, 1):  # Logical data record, excluding the header.
        source, target = row["source"], row["target"]
        if source not in node_by_id or target not in node_by_id or (source, target) in edge_record:
            raise ValueError(f"Invalid or repeated edge at record {record}")
        outgoing[source].add(target)
        incoming[target].add(source)
        edge_record[source, target] = record

    def traverse(direction: str):
        paths = {"n0": ["n0"]}
        queue = deque(["n0"])
        while queue:
            node = queue.popleft()
            path = paths[node]
            if len(path) - 1 == 3:
                continue
            neighbors = (outgoing[node] if direction == "forward" else incoming[node]
                         if direction == "reverse" else outgoing[node] | incoming[node])
            for neighbor in sorted(neighbors):
                if neighbor not in paths:
                    paths[neighbor] = path + [neighbor]
                    queue.append(neighbor)
        return sorted((len(path) - 1, node, path) for node, path in paths.items() if node != "n0")

    traversals = {direction: traverse(direction) for direction in ("forward", "reverse", "undirected")}
    forward = traversals["forward"]
    examples = []
    for depth in (1, 2, 3):
        _, _, path = next(item for item in forward if item[0] == depth)
        examples.append({"hops": depth, "companyIds": path,
                         "edgeRecords": [edge_record[a, b] for a, b in zip(path, path[1:])]})

    limit = 5
    response = {
        "startCompany": node_by_id["n0"],
        "outgoingDirect": sorted(outgoing["n0"]),
        "incomingDirect": sorted(incoming["n0"]),
        "reachability": {
            direction: {"uniqueCompanies": len(rows),
                        "minimumHops": dict(sorted(Counter(row[0] for row in rows).items()))}
            for direction, rows in traversals.items()
        },
        "representativeForwardPaths": examples,
        "limitedForwardResponse": {
            "startCompany": "n0", "direction": "forward", "maxHops": 3,
            "displayLimit": limit,
            "companyIds": [node for _, node, _ in forward[:limit]],
            "companies": [{"companyId": node, "minimumHops": hops, "path": path}
                          for hops, node, path in forward[:limit]],
            "totalUniqueCompanies": len(forward), "countStatus": "exact",
            "outputTruncated": len(forward) > limit, "traversalTruncated": False,
            "truncationReason": "result_limit" if len(forward) > limit else None,
        },
        "n378SelfLoopRecord": edge_record["n378", "n378"],
    }
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
