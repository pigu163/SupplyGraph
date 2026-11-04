"""Analyze typed AnonNet GraphML and write a reproducible local report."""

from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import Counter
from pathlib import Path

import networkx as nx
from networkx.algorithms import bipartite


def company_id(node: str) -> str:
    return node.removeprefix("company:")


def project(graph: nx.MultiDiGraph) -> tuple[nx.DiGraph, nx.Graph, nx.Graph]:
    """Create relation-specific views; attribute edges are never supply edges."""
    companies = [n for n, d in graph.nodes(data=True) if d["kind"] == "Company"]
    supply = nx.DiGraph()
    supply.add_nodes_from(companies)
    locations = nx.Graph()
    descriptions = nx.Graph()
    locations.add_nodes_from((n, {"kind": "Company"}) for n in companies)
    descriptions.add_nodes_from((n, {"kind": "Company"}) for n in companies)
    for node, data in graph.nodes(data=True):
        if data["kind"] == "LocationLabel":
            locations.add_node(node, **data)
        elif data["kind"] == "ProductDescription":
            descriptions.add_node(node, **data)
        elif data["kind"] != "Company":
            raise ValueError(f"Unexpected node kind: {data['kind']}")
    expected = {
        "REPORTED_SUPPLY_LINK": ("Company", "Company"),
        "REPORTED_LOCATION": ("Company", "LocationLabel"),
        "REPORTED_DESCRIPTION": ("Company", "ProductDescription"),
    }
    for source, target, data in graph.edges(data=True):
        relation = data["relation"]
        if relation not in expected or (graph.nodes[source]["kind"], graph.nodes[target]["kind"]) != expected[relation]:
            raise ValueError(f"Invalid relation or endpoint kinds: {relation}, {source}, {target}")
        if relation == "REPORTED_SUPPLY_LINK":
            supply.add_edge(source, target)
        elif relation == "REPORTED_LOCATION":
            locations.add_edge(source, target)
        elif relation == "REPORTED_DESCRIPTION":
            descriptions.add_edge(source, target)
    return supply, locations, descriptions


def largest_component_size(graph: nx.Graph) -> int:
    return max((len(c) for c in nx.connected_components(graph)), default=0)


def largest_after_removal(graph: nx.Graph, nodes: list[str]) -> int:
    scenario = graph.copy()
    scenario.remove_nodes_from(nodes)
    return largest_component_size(scenario)


def analyze(graph: nx.MultiDiGraph, seed: int = 42, samples: int = 32, trials: int = 20) -> dict:
    if not graph.is_directed() or not graph.is_multigraph():
        raise ValueError("Expected a directed multigraph")
    node_counts = Counter(data["kind"] for _, data in graph.nodes(data=True))
    edge_counts = Counter(data["relation"] for _, _, data in graph.edges(data=True))
    supply, locations, descriptions = project(graph)
    if not supply:
        raise ValueError("The company graph is empty")
    if not bipartite.is_bipartite(locations) or not bipartite.is_bipartite(descriptions):
        raise ValueError("Expected company-attribute graphs to be bipartite")

    # Remove self-loops and collapse directions only for undirected connectivity,
    # betweenness and failure simulations. The source graph remains unchanged.
    undirected = nx.Graph(supply)
    self_loops = list(nx.selfloop_edges(undirected))
    undirected.remove_edges_from(self_loops)
    degrees = sorted(undirected.degree(), key=lambda item: (-item[1], item[0]))
    source_degrees = sorted(supply.out_degree(), key=lambda item: (-item[1], item[0]))
    k = min(samples, len(undirected))
    betweenness = nx.betweenness_centrality(undirected, k=k, normalized=True, seed=seed)
    top_betweenness = sorted(betweenness.items(), key=lambda item: (-item[1], item[0]))[:10]

    # Static top-degree targeting compared with equally many random failures.
    num_failed = min(max(1, int(len(undirected) * 0.01)), len(undirected) - 1)
    ordered_nodes = sorted(undirected.nodes())
    rng = random.Random(seed)
    random_largest = [largest_after_removal(undirected, rng.sample(ordered_nodes, num_failed))
                      for _ in range(trials)]
    targeted_nodes = [node for node, _ in degrees[:num_failed]]
    targeted_largest = largest_after_removal(undirected, targeted_nodes)

    def company_location(company: str) -> str | None:
        neighbors = list(locations.neighbors(company))
        return locations.nodes[neighbors[0]]["label"] if neighbors else None

    known_edges = same_location = 0
    for source, target, data in graph.edges(data=True):
        if data["relation"] != "REPORTED_SUPPLY_LINK" or source == target:
            continue
        a, b = company_location(source), company_location(target)
        if a is not None and b is not None:
            known_edges += 1
            same_location += a == b

    location_nodes = [n for n, d in locations.nodes(data=True) if d["kind"] == "LocationLabel"]
    description_nodes = [n for n, d in descriptions.nodes(data=True) if d["kind"] == "ProductDescription"]
    top_locations = sorted(
        ((locations.nodes[n]["label"], locations.degree(n)) for n in location_nodes),
        key=lambda item: (-item[1], item[0]),
    )[:10]
    ranked_descriptions = sorted(description_nodes, key=lambda n: (-descriptions.degree(n), descriptions.nodes[n]["label"]))
    shared = [n for n in ranked_descriptions if descriptions.degree(n) >= 2]
    cross_location = []
    for desc in shared:
        known = {company_location(c) for c in descriptions.neighbors(desc)} - {None}
        if len(known) >= 2:
            cross_location.append(desc)

    # Find an actual two-hop typed path with an exact, unsplit description and
    # different known location labels. No direct company supply edge is implied.
    example = None
    for desc in cross_location:
        candidates = sorted(descriptions.neighbors(desc))
        for i, left in enumerate(candidates):
            for right in candidates[i + 1:]:
                a, b = company_location(left), company_location(right)
                if a and b and a != b and not supply.has_edge(left, right) and not supply.has_edge(right, left):
                    typed_path = nx.shortest_path(descriptions, left, right)
                    example = {
                        "company_a": company_id(left), "location_a": a,
                        "description": descriptions.nodes[desc]["label"],
                        "company_b": company_id(right), "location_b": b,
                        "typed_path_hops": len(typed_path) - 1,
                        "direct_supply_edge_either_direction": False,
                    }
                    break
            if example:
                break
        if example:
            break

    return {
        "networkx_version": nx.__version__,
        "source_hashes": {"nodes_sha256": graph.graph["nodes_sha256"], "edges_sha256": graph.graph["edges_sha256"]},
        "heterogeneous": {"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges(),
                          "node_types": dict(node_counts), "edge_types": dict(edge_counts),
                          "location_layer_is_bipartite": bipartite.is_bipartite(locations),
                          "description_layer_is_bipartite": bipartite.is_bipartite(descriptions)},
        "supply": {"nodes": len(supply), "unique_ordered_edges": supply.number_of_edges(),
                   "source_rows_collapsed_in_simple_projection": edge_counts["REPORTED_SUPPLY_LINK"] - supply.number_of_edges(),
                   "self_loops_excluded_from_undirected_analyses": len(self_loops),
                   "weak_components": nx.number_weakly_connected_components(supply),
                   "largest_strong_component": max(map(len, nx.strongly_connected_components(supply))),
                   "undirected_edges_excluding_self_loops": undirected.number_of_edges(),
                   "articulation_points": len(list(nx.articulation_points(undirected))),
                   "top_source_degree": [(company_id(n), d) for n, d in source_degrees[:10]],
                   "top_undirected_degree": [(company_id(n), d) for n, d in degrees[:10]],
                   "sampled_betweenness": {"k": k, "seed": seed, "normalized": True,
                                           "top": [(company_id(n), v) for n, v in top_betweenness]}},
        "locations": {"labels": len(location_nodes), "companies_with_label": edge_counts["REPORTED_LOCATION"],
                      "top": top_locations, "non_self_supply_edges_with_both_locations": known_edges,
                      "same_location_edges": same_location, "different_location_edges": known_edges - same_location},
        "descriptions": {"unique_raw_cells": len(description_nodes),
                         "companies_with_description": edge_counts["REPORTED_DESCRIPTION"],
                         "exact_text_groups_at_least_two_companies": len(shared),
                         "exact_text_groups_with_two_known_location_labels": len(cross_location),
                         "top": [(descriptions.nodes[n]["label"], descriptions.degree(n)) for n in ranked_descriptions[:10]],
                         "example_two_hop_path": example},
        "failure_experiment": {"projection": "company-only undirected simple graph; self-loops removed",
                               "failure_count": num_failed, "survivors": len(undirected) - num_failed,
                               "target_strategy": "highest original undirected degree, fixed ranking",
                               "targeted_largest_component": targeted_largest,
                               "random_trials": trials, "seed": seed,
                               "random_largest_component_mean": statistics.mean(random_largest),
                               "random_largest_component_min": min(random_largest),
                               "random_largest_component_max": max(random_largest),
                               "random_largest_component_each_trial": random_largest},
    }


def escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_report(result: dict, graph_path: Path) -> str:
    h, s, l, d, f = (result[k] for k in ("heterogeneous", "supply", "locations", "descriptions", "failure_experiment"))
    total = s["nodes"]
    known_edges = l["non_self_supply_edges_with_both_locations"]
    survivors = f["survivors"]
    out = [
        "# AnonNet 异质图：NetworkX 构建与分析", "",
        "数据来源：本地 AnonNet CSV；源网络快照产生日期未知。分析不连接 Neo4j。", "",
        "## 图如何构建", "",
        "使用 `networkx.MultiDiGraph`；节点 ID 有类型前缀，地点和产品描述节点使用完整文本的 SHA-256 作为稳定标识。",
        "每个公司保留原始字段；地点标签仅去除首尾空格，边保留原始地点文本；产品描述保留整个原始 CSV 单元格，不按逗号拆分。",
        "缺失字段不建立未知共享节点。供应关系按 CSV 的 source → target 保存，业务上下游方向尚未证实；自连接、反向边和重复输入行均保留。", "",
        "| 节点类型 | 数量 | 含义 |", "|---|---:|---|",
        f"| Company | {h['node_types'].get('Company', 0):,} | 匿名公司 |",
        f"| LocationLabel | {h['node_types'].get('LocationLabel', 0):,} | 去除首尾空格后的原始地点标签 |",
        f"| ProductDescription | {h['node_types'].get('ProductDescription', 0):,} | 整个原始产品字段，不是单个标准产品 |", "",
        "| 关系类型 | 数量 | 含义 |", "|---|---:|---|",
        f"| REPORTED_SUPPLY_LINK | {h['edge_types'].get('REPORTED_SUPPLY_LINK', 0):,} | 公司 → 公司，保留文件方向 |",
        f"| REPORTED_LOCATION | {h['edge_types'].get('REPORTED_LOCATION', 0):,} | 公司 → 地点标签 |",
        f"| REPORTED_DESCRIPTION | {h['edge_types'].get('REPORTED_DESCRIPTION', 0):,} | 公司 → 产品描述原文 |", "",
        f"合计 **{h['nodes']:,} 节点、{h['edges']:,} 条关系**。GraphML 文件为 `{graph_path}`；它由原始数据生成，保存在 Git 忽略的 `dataset/derived/` 下。", "",
        "## 方法与结果", "",
        "### 1. 公司间网络的结构", "",
        f"只投影 `REPORTED_SUPPLY_LINK`：{s['nodes']:,} 个公司、{s['unique_ordered_edges']:,} 条不同有向边，"
        f"合并的重复有向记录 {s['source_rows_collapsed_in_simple_projection']} 条。弱连通分量 {s['weak_components']} 个，最大强连通分量 {s['largest_strong_component']:,} 家公司。",
        f"公司网络转为无向简单图时，反向边合并、移除 {s['self_loops_excluded_from_undirected_analyses']} 条自连接，得到 {s['undirected_edges_excluding_self_loops']:,} 条边和 {s['articulation_points']:,} 个割点。割点仅说明图连通性，不等于真实的单点故障。", "",
        "**无向连接数最高的公司**（同一对公司的双向边计一次）：", "",
        "| 公司 ID | 邻居数 |", "|---|---:|",
    ]
    out += [f"| `{escape(n)}` | {v} |" for n, v in s["top_undirected_degree"][:5]]
    out += ["", f"**介数中心性**：在该无向简单图上用 `nx.betweenness_centrality(k={s['sampled_betweenness']['k']}, seed={s['sampled_betweenness']['seed']}, normalized=True)` 抽样估计；它衡量采样最短路径经过节点的程度。仅用单个种子抽样 {s['sampled_betweenness']['k']} 个源点，排名用于探索，后续需做采样规模和种子敏感性分析。", "",
            "| 公司 ID | 近似介数 |", "|---|---:|"]
    out += [f"| `{escape(n)}` | {v:.6f} |" for n, v in s["sampled_betweenness"]["top"][:5]]
    common_top = len({n for n, _ in s["top_undirected_degree"][:5]} & {n for n, _ in s["sampled_betweenness"]["top"][:5]})
    out += ["", f"连接数与抽样介数的前五名有 {common_top} 家重合；它们衡量不同结构特征，介数排名还受抽样影响。"]
    out += ["", "### 2. 地点标签与公司关系", "",
            f"公司—地点子图为二部图：{l['companies_with_label']:,}/{total:,} 家公司有标签，涉及 {l['labels']} 个不同标签。去除首尾空格后，出现最多的标签如下：", "",
            "| 地点标签 | 公司数 |", "|---|---:|"]
    out += [f"| {escape(label)} | {count:,} |" for label, count in l["top"][:5]]
    out += ["", f"在排除自连接的公司关系中，**{known_edges:,}** 条关系两端都有地点标签；其中 **{l['same_location_edges']:,}** 条（{l['same_location_edges'] / known_edges * 100 if known_edges else 0:.2f}%）标签相同，**{l['different_location_edges']:,}** 条标签不同。分母仅为双方地点均已知的有向关系行；反向关系分别计数，未知地点不算同地也不算异地。",
            "这些是文本标签的比较，不能证明实际工厂位置、物流路线或地理风险。", "",
            "### 3. 相同产品描述形成的两跳路径", "",
            f"公司—产品描述子图为二部图：{d['companies_with_description']:,} 家公司有描述，共 {d['unique_raw_cells']:,} 种不同的完整原文。"
            f"其中 **{d['exact_text_groups_at_least_two_companies']:,}** 个原文被至少两家公司共享；"
            f"**{d['exact_text_groups_with_two_known_location_labels']:,}** 个原文对应至少两个已知地点标签。", "",
            "| 完整产品描述原文 | 公司数 |", "|---|---:|"]
    out += [f"| {escape(label)} | {count:,} |" for label, count in d["top"][:5]]
    example = d["example_two_hop_path"]
    if example:
        out += ["", f"例如：`{escape(example['company_a'])}`（{escape(example['location_a'])}）— "
                f"`{escape(example['description'])}` — `{escape(example['company_b'])}`（{escape(example['location_b'])}）。"
                "这是公司—相同原文—公司两跳路径，两个公司之间在原始公司关系中没有直接边。",
                "相同完整描述只是候选线索，不能证明它们生产相同规格、存在交易关系或可以相互替代。"]
    out += ["", "### 4. 结构性故障实验：静态度数定向 vs 随机", "",
            f"在仅含公司关系、已移除自连接的无向简单图上，同时移除 {f['failure_count']} 家公司（约 1%）；"
            "定向组使用原图无向连接数最高的固定排序，随机组不放回抽样。"
            f"随机组重复 {f['random_trials']} 次，种子 {f['seed']}；每次剩余 {survivors:,} 个节点。", "",
            "| 方法 | 最大连通分量 | 占剩余节点 |", "|---|---:|---:|",
            f"| 静态度数定向 | {f['targeted_largest_component']:,} | {f['targeted_largest_component']/survivors*100:.2f}% |",
            f"| 随机移除：平均 | {f['random_largest_component_mean']:,.2f} | {f['random_largest_component_mean']/survivors*100:.2f}% |",
            f"| 随机移除：范围 | {f['random_largest_component_min']:,}–{f['random_largest_component_max']:,} | {f['random_largest_component_min']/survivors*100:.2f}%–{f['random_largest_component_max']/survivors*100:.2f}% |", "",
            "该实验只比较网络拓扑在两种移除规则下的变化；它没有模拟故障概率、供需、库存、产能、替代和真实损失，不能作为企业实际韧性结论。", "",
            "## 解释边界与后续", "",
            "- `source → target` 的供应商/客户含义仍待数据提供方说明；因此入度、出度和有向路径不标注上下游业务角色。",
            "- `ProductDescription` 是原始文本字段，不等同于单个产品；相同地点或描述创建的两跳路径也不是新增供应关系。",
            "- 地点、产品缺失分别影响地理比较与描述匹配；地点字段不代表公司全部工厂和运营地。",
            "- 本次使用 NetworkX，未将图导入 Neo4j，也未运行 GDS。后续若映射到 Neo4j，应先核对版本、目标数据库现状、投影方向和源字段语义。", "",
            "## 复现", "",
            "```bash", "uv sync --locked", "uv run python scripts/graph_construct/construct.py",
            "uv run python scripts/graph_construct/analyze.py", "```", "",
            f"NetworkX 版本：{result['networkx_version']}。节点文件 SHA-256：`{result['source_hashes']['nodes_sha256']}`；"
            f"关系文件 SHA-256：`{result['source_hashes']['edges_sha256']}`。",
            "图构建、GraphML 读取及分析结果均基于本地 CSV；脚本不修改源文件或数据库。", "",
            "NetworkX 官方说明：[二部图及节点集合](https://networkx.org/documentation/stable/reference/algorithms/bipartite.html) · "
            "[介数中心性](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.centrality.betweenness_centrality.html)。", ""]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", type=Path, default=Path("dataset/derived/anonnet-heterogeneous.graphml"))
    parser.add_argument("--report-dir", type=Path, default=Path("report"))
    args = parser.parse_args()
    graph = nx.read_graphml(args.graph, force_multigraph=True)
    result = analyze(graph)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.report_dir / "anonnet-heterogeneous-analysis.json"
    report_path = args.report_dir / "anonnet-heterogeneous-analysis-2026-11-04.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(result, args.graph), encoding="utf-8")
    print(f"Wrote {report_path} and {json_path}")


if __name__ == "__main__":
    main()
