# SupplyGraph roadmap

Status reviewed 2026-11-06.

## Implemented

- Audited the AnonNet CSV snapshot and recorded provenance and checksums.
- Added DeBERTa zero-shot predictions using the project's fixed 17-category taxonomy. The labels are used in analysis as model predictions; no independent expert-labelled validation set is available.
- Built and analyzed a typed NetworkX graph, including structural node-removal comparisons.
- Imported the snapshot into Neo4j with source-row provenance and repeatable same-snapshot writes. Reproduced bounded path examples and ran baseline PageRank, WCC, Louvain, and a node-removal comparison with GDS.

## Next

1. Turn the Neo4j path examples into reusable bounded queries. Compare forward, reverse, and undirected results with CSV baselines and return edge provenance and clear truncation status.
2. Benchmark centrality rankings and random versus targeted structural removals under matched graph and sampling assumptions.
3. Build supplier candidate retrieval from descriptions and predicted categories, while reporting that category matches do not prove compatibility.
4. Evaluate capacity-aware disruption only after adding capacity, demand, and recovery inputs. If these are synthetic, keep them in a separate fixed-seed layer and report sensitivity to the assumptions.

## Interpretation limits

- The CSV arrow `source → target` is preserved; its supplier/customer business meaning is not confirmed.
- DeBERTa categories have not been independently evaluated and do not establish production capability or substitutability.
- Graph reachability and node-removal results describe network structure. The data has no verified capacity, demand, inventory, lead-time, or cost values, so these results are not estimates of actual losses.
