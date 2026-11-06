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


ROOT = Path(__file__).resolve().parents[2]


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
        elif data["kind"] == "ProductCategory":
            pass  # Categories are analyzed separately from observed supply links.
        elif data["kind"] != "Company":
            raise ValueError(f"Unexpected node kind: {data['kind']}")
    expected = {
        "REPORTED_SUPPLY_LINK": ("Company", "Company"),
        "REPORTED_LOCATION": ("Company", "LocationLabel"),
        "REPORTED_DESCRIPTION": ("Company", "ProductDescription"),
        "PREDICTED_CATEGORY": ("Company", "ProductCategory"),
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
    """Summarize supply connections and compare targeted with random node failures."""
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

    # Categories are model predictions, not observed products or supply links.
    categories = {}
    for source, target, data in graph.edges(data=True):
        if data["relation"] == "PREDICTED_CATEGORY":
            if source in categories:
                raise ValueError(f"Multiple category predictions for {source}")
            categories[source] = graph.nodes[target]["label"]
    grouped = {}
    for company, category in categories.items():
        grouped.setdefault(category, []).append(company)
    category_rows = []
    for category, members in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        loc = Counter(company_location(c) for c in members if company_location(c) is not None)
        member_degrees = [undirected.degree(c) for c in members]
        scores = [float(graph.nodes[c]["category_score"]) for c in members if "category_score" in graph.nodes[c]]
        category_rows.append({"category": category, "companies": len(members),
                              "known_location": sum(loc.values()),
                              "top_locations": sorted(loc.items(), key=lambda x: (-x[1], x[0]))[:3],
                              "scored_companies": len(scores), "scores_below_0_3": sum(s < 0.3 for s in scores),
                              "mean_undirected_degree": statistics.mean(member_degrees),
                              "median_undirected_degree": statistics.median(member_degrees)})
    category_pairs = Counter((categories[a], categories[b]) for a, b in supply.edges()
                             if a != b and a in categories and b in categories)
    category_edge_total = sum(category_pairs.values())
    same_category = sum(count for (a, b), count in category_pairs.items() if a == b)
    left_counts = Counter(); right_counts = Counter()
    for (a, b), count in category_pairs.items():
        left_counts[a] += count; right_counts[b] += count
    mixing_by_source = []
    for category, total in sorted(left_counts.items(), key=lambda item: (-item[1], item[0])):
        targets = sorted(((b, count) for (a, b), count in category_pairs.items() if a == category),
                         key=lambda item: (-item[1], item[0]))
        mixing_by_source.append({"source_category": category, "eligible_edges": total,
                                 "same_category_edges": category_pairs[category, category],
                                 "top_target_category": targets[0][0], "top_target_edges": targets[0][1]})
    marginal_baseline = (sum(left_counts[c] * right_counts[c] for c in grouped) / category_edge_total**2
                         if category_edge_total else 0.0)
    scored = {c: float(graph.nodes[c]["category_score"]) for c in categories
              if "category_score" in graph.nodes[c]}
    unique_scores = {}
    for company, score in scored.items():
        text = graph.nodes[company]["products_raw"].strip()
        if text in unique_scores and unique_scores[text] != score:
            raise ValueError(f"Conflicting scores for description: {text}")
        unique_scores[text] = score
    category_example = None
    for category, members in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        candidates = sorted(members)
        for i, left in enumerate(candidates):
            for right in candidates[i + 1:]:
                a, b = company_location(left), company_location(right)
                if a and b and a != b and not supply.has_edge(left, right) and not supply.has_edge(right, left):
                    category_example = {"category": category, "company_a": company_id(left), "location_a": a,
                                        "description_a": graph.nodes[left]["products_raw"],
                                        "company_b": company_id(right), "location_b": b,
                                        "description_b": graph.nodes[right]["products_raw"]}
                    break
            if category_example:
                break
        if category_example:
            break

    return {
        "networkx_version": nx.__version__,
        "source_hashes": {"nodes_sha256": graph.graph["nodes_sha256"], "edges_sha256": graph.graph["edges_sha256"],
                          "nodes_file": graph.graph.get("nodes_file", "unknown"),
                          "node_input_kind": graph.graph.get("node_input_kind", "raw"),
                          "raw_nodes_sha256": graph.graph.get("raw_nodes_sha256", "unknown"),
                          "raw_nodes_verified": graph.graph.get("raw_nodes_verified", False)},
        "category_provenance": {"model": graph.graph.get("category_model", "unknown"),
                                "revision": graph.graph.get("category_model_revision", "unknown"),
                                "taxonomy_version": graph.graph.get("category_taxonomy_version", "unknown")},
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
        "predicted_categories": {"companies_with_category": len(categories),
                                 "companies_without_category": len(supply) - len(categories),
                                 "categories": category_rows,
                                 "known_category_nonself_supply_edges": category_edge_total,
                                 "same_category_edges": same_category,
                                 "expected_same_category_from_endpoint_marginals": marginal_baseline,
                                 "mixing_by_source_category": mixing_by_source,
                                 "top_directed_category_pairs": [(a, b, count) for (a, b), count in
                                     sorted(category_pairs.items(), key=lambda item: (-item[1], item[0]))[:10]],
                                 "scored_companies": len(scored), "unique_scored_descriptions": len(unique_scores),
                                 "company_scores_below_0_3": sum(s < 0.3 for s in scored.values()),
                                 "unique_scores_below_0_3": sum(s < 0.3 for s in unique_scores.values()),
                                 "example_same_category_cross_location": category_example},
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
    c = result["predicted_categories"]
    categorized_input = result['source_hashes']['node_input_kind'] == 'categorized'
    location_source = '`country`' if categorized_input else '`location`'
    total = s["nodes"]
    known_edges = l["non_self_supply_edges_with_both_locations"]
    survivors = f["survivors"]
    out = [
        "# AnonNet 异质图：NetworkX 构建与分析", "",
        ("数据来源：本地 AnonNet 分类 CSV 与原始关系 CSV；源网络快照产生日期未知。分析不连接 Neo4j。"
         if categorized_input else "数据来源：本地 AnonNet 原始节点与关系 CSV；源网络快照产生日期未知。分析不连接 Neo4j。"), "",
        "## 图如何构建", "",
        "使用 `networkx.MultiDiGraph`；节点 ID 有类型前缀，地点和产品描述节点使用对应文本作为标识。",
        f"每个公司保留原始字段；地点标签使用节点文件的 {location_source} 列并去除首尾空格，边保留原始地点文本；产品描述保留整个原始 CSV 单元格，不按逗号拆分。",
        "缺失字段不建立未知共享节点。供应关系按 CSV 的 source → target 保存，业务上下游方向尚未证实；自连接、反向边和重复输入行均保留。", "",
        "| 节点类型 | 数量 | 含义 |", "|---|---:|---|",
        f"| Company | {h['node_types'].get('Company', 0):,} | 匿名公司 |",
        f"| LocationLabel | {h['node_types'].get('LocationLabel', 0):,} | {location_source} 文本标签 |",
        f"| ProductDescription | {h['node_types'].get('ProductDescription', 0):,} | 整个原始产品字段，不是单个标准产品 |",
        f"| ProductCategory | {h['node_types'].get('ProductCategory', 0):,} | 模型预测类别，不是已核实产品 |", "",
        "| 关系类型 | 数量 | 含义 |", "|---|---:|---|",
        f"| REPORTED_SUPPLY_LINK | {h['edge_types'].get('REPORTED_SUPPLY_LINK', 0):,} | 公司 → 公司，保留文件方向 |",
        f"| REPORTED_LOCATION | {h['edge_types'].get('REPORTED_LOCATION', 0):,} | 公司 → 地点标签 |",
        f"| REPORTED_DESCRIPTION | {h['edge_types'].get('REPORTED_DESCRIPTION', 0):,} | 公司 → 产品描述原文 |",
        f"| PREDICTED_CATEGORY | {h['edge_types'].get('PREDICTED_CATEGORY', 0):,} | 公司 → 模型预测类别；不表示供应关系 |", "",
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
            f"公司—地点子图为二部图：{l['companies_with_label']:,}/{total:,} 家公司有标签，涉及 {l['labels']} 个不同标签。来自 {location_source} 列、去除首尾空格后，出现最多的标签如下：", "",
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
    out += ["", "### 4. 模型预测类别：覆盖、地点与网络结构", "",
            f"{c['companies_with_category']:,}/{total:,} 家公司有预测类别；{c['companies_without_category']:,} 家没有。每条非空描述被强制分配一个 top-1 类别，模型并未验证类别正确性。", "",
            "| 预测类别 | 公司数 | 已知地点标签公司数 | 最常见地点标签（公司数） | 无向邻居数均值 | 中位数 | 相对分低于 0.3 / 有分数 |",
            "|---|---:|---:|---|---:|---:|---:|"]
    out += [f"| {escape(row['category'])} | {row['companies']:,} | {row['known_location']:,} | "
            f"{escape(', '.join(f'{name}: {count}' for name, count in row['top_locations']))} | "
            f"{row['mean_undirected_degree']:.2f} | {row['median_undirected_degree']:.1f} | {row['scores_below_0_3']:,}/{row['scored_companies']:,} |"
            for row in c['categories']]
    eligible = c['known_category_nonself_supply_edges']
    same = c['same_category_edges']
    out += ["", f"按公司间有向简单图统计，排除自连接后，两端类别均已知的关系有 **{eligible:,}** 条；"
            f"其中同类别 **{same:,}** 条（{same / eligible * 100 if eligible else 0:.2f}%）。"
            f"按这些边的 source/target 类别边际频率独立配对得到的描述性参照为 {c['expected_same_category_from_endpoint_marginals'] * 100:.2f}%；"
            "这不是随机置换检验，也不控制公司度数或抽样偏差。反向边分别计数，未知类别不参与分母。", "",
            "| 文件 source 类别 | 文件 target 类别 | 关系数 |", "|---|---|---:|"]
    out += [f"| {escape(a)} | {escape(b)} | {n:,} |" for a, b, n in c['top_directed_category_pairs']]
    out += ["", "按文件 source 类别分组，下表分母为该类别指向类别已知目标的非自连接关系数；"
            "百分比用于描述该文件方向的结构，不能解释成真实供货比例。", "",
            "| source 类别 | 符合条件的关系 | 同类别关系 | 同类别占比 | 最常见 target 类别（关系数） |",
            "|---|---:|---:|---:|---|"]
    out += [f"| {escape(row['source_category'])} | {row['eligible_edges']:,} | {row['same_category_edges']:,} | "
            f"{row['same_category_edges']/row['eligible_edges']*100:.2f}% | "
            f"{escape(row['top_target_category'])} ({row['top_target_edges']:,}) |"
            for row in c['mixing_by_source_category']]
    out += ["", f"预测明细中有 {c['unique_scored_descriptions']:,} 条不同描述的分数（对应 {c['scored_companies']:,} 家公司）；"
            f"其中最高相对分低于 0.3 的分别有 {c['unique_scores_below_0_3']:,} 条描述、{c['company_scores_below_0_3']:,} 家公司。"
            "0.3 只是用于统计可能需要复核样本的探索阈值，不是经过标注验证的置信阈值；分数不是校准概率。"]
    review_groups = [row for row in c['categories'] if row['scored_companies'] >= 20
                     and row['scores_below_0_3'] / row['scored_companies'] >= 0.75]
    if review_groups:
        out += ["", "低分占比较高的类别（仅列有分数公司至少 20 家、低分占比至少 75% 的类别）：" +
                "；".join(f"`{escape(row['category'])}` {row['scores_below_0_3']}/{row['scored_companies']}"
                         f"（{row['scores_below_0_3']/row['scored_companies']*100:.1f}%）" for row in review_groups) +
                "。这些类别适合优先抽样复核，不能仅凭分数认定预测错误。"]
    ce = c['example_same_category_cross_location']
    if ce:
        out += ["", f"示例：`{escape(ce['company_a'])}`（{escape(ce['location_a'])}，`{escape(ce['description_a'])}`）与 "
                f"`{escape(ce['company_b'])}`（{escape(ce['location_b'])}，`{escape(ce['description_b'])}`）"
                f"共同连接预测类别 `{escape(ce['category'])}`，两公司间没有直接供应边。"
                "共同类别与地点差异只提供候选检索线索，不证明产品兼容、实际供货或替代可行。"]
    out += ["", "### 5. 结构性故障实验：静态度数定向 vs 随机", "",
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
            "- `ProductCategory` 是模型的单标签预测，类别混合了产品和服务；分数未经校准，类别未进行人工准确率评估。类别节点只用于类别分析，绝不参与供应网络投影和故障实验。",
            "- 地点、产品缺失分别影响地理比较与描述匹配；地点字段不代表公司全部工厂和运营地。",
            "- 本次使用 NetworkX，未将图导入 Neo4j，也未运行 GDS。后续若映射到 Neo4j，应先核对版本、目标数据库现状、投影方向和源字段语义。", "",
            "## 复现", "",
            "```bash", "uv sync --locked", "uv run python scripts/graph/construct.py",
            "uv run python scripts/graph/analyze.py", "```", "",
            f"NetworkX 版本：{result['networkx_version']}。节点文件 `{result['source_hashes']['nodes_file']}` SHA-256：`{result['source_hashes']['nodes_sha256']}`；"
            f"关系文件 SHA-256：`{result['source_hashes']['edges_sha256']}`。",
            f"分类前节点文件 SHA-256：`{result['source_hashes']['raw_nodes_sha256']}`；"
            f"原始节点字段与分类 CSV 逐行核对：{'通过' if result['source_hashes']['raw_nodes_verified'] else '未核对（原始文件或来源元数据不可用）'}。",
            f"分类模型：`{result['category_provenance']['model']}`；版本：`{result['category_provenance']['revision']}`；taxonomy：`{result['category_provenance']['taxonomy_version']}`。",
            "图构建、GraphML 读取及分析结果均基于本地 CSV；脚本不修改源文件或数据库。", "",
            "NetworkX 官方说明：[二部图及节点集合](https://networkx.org/documentation/stable/reference/algorithms/bipartite.html) · "
            "[介数中心性](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.centrality.betweenness_centrality.html)。", ""]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--graph",
        type=Path,
        default=ROOT / "dataset/derived/anonnet-heterogeneous-categorized.graphml",
    )
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
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
