# AGENTS.md — SupplyGraph

## Project Overview
**SupplyGraph** is a portfolio-oriented, knowledge-graph-based supply chain risk and resilience project.

The goal is to build a credible, reproducible system that represents multi-tier supply chain dependencies in **Neo4j**, analyzes disruption impact, and recommends risk-aware alternative suppliers. The project should demonstrate **knowledge graph engineering, graph analytics, explainable reasoning, simulation, and decision support**, not merely visualize connected data.

**Working title:** SupplyGraph — Knowledge Graph-Based Supply Chain Resilience & Risk Simulation.

## Core Objectives
1. **Supply Chain Knowledge Graph:** Model companies, suppliers, products/components, factories, locations, and supply relationships with clear semantics, stable identifiers, constraints, and provenance.
2. **Dependency & Impact Analysis:** Trace multi-hop upstream/downstream dependencies and identify potentially affected entities when a supplier, factory, or route becomes unavailable.
3. **Disruption & Resilience Simulation:** Simulate node/edge failures, bottlenecks, cascading impacts, and recovery under explicitly stated assumptions. Evaluate single points of failure and network robustness.
4. **Risk-Aware Alternative Supplier Recommendation:** Find candidate suppliers based on product/component compatibility, geographical risk diversification, dependency concentration, and—where data supports it—capacity, lead time, cost, and demand fulfillment.
5. **Explainability & Evaluation:** Return evidence paths, assumptions, and risk factors for each result. Compare algorithms and scenarios quantitatively rather than relying on visual demos alone.

## Data Strategy
- **Primary candidate:** AnonNet (University of Cambridge / Supply Chain AI Lab), for an anonymized, real-world supplier network and multi-tier relationship analysis.
- **Optional small demonstrator:** Sectivia, for explainable examples involving named companies, subject to dataset quality and licensing checks.
- **Optional enrichment:** Open Supply Hub, for facility/location information where licensing, attribution, entity matching, and actual relationship coverage permit.
- Inspect the **actual dataset schema and license before finalizing** graph labels, relationships, and analytics.
- Public datasets rarely contain complete factory-level dependencies, inventory, demand, capacities, contract terms, or lead times. Where required for simulation, generate a **separate, reproducible synthetic layer** with fixed seeds and clearly documented assumptions.
- Never present inferred, synthetic, or hypothetical supplier relationships and financial impact estimates as verified real-world facts.

## Knowledge Graph Principles
- **Neo4j Property Graph** is the primary representation and query engine.
- Candidate entities: `Company`, `Factory`, `Product`, `Component`/`Material`, `Location`, `DisruptionEvent`, plus evidence/source records when useful.
- Candidate relations: `SUPPLIES_TO`, `OWNS`, `PRODUCES`, `REQUIRES`, `LOCATED_IN`, `AFFECTS`. Implement only those justified by available data.
- Define relationship direction and meaning precisely; distinguish observed relationships from inferred ones.
- Preserve source, timestamp/validity when available, and confidence/derivation for inferred facts.
- Include entity resolution when combining sources; prevent duplicate entities and avoid unverified joins.
- Treat a **dependency path** as potential exposure, not proof of actual loss or stoppage.

## Main Analytical Capabilities
### 1. Dependency Reasoning
- Multi-hop supplier/customer and component dependency traversal using Cypher.
- Explainable impact paths and dependency depth.
- Identification of concentrated dependencies and potential single points of failure.

### 2. Supply Chain Risk & Resilience
- Compare degree, centrality, articulation-point or other structural heuristics where appropriate.
- Run random-failure versus targeted-failure scenarios.
- Evaluate reachability, unmet demand, delivery/service levels, recovery or rerouting cost **only when the simulation has enough relevant inputs**.
- Prefer capacity-aware simulation over claiming that every reachable downstream company will suffer actual disruption.

### 3. Alternative Supplier Discovery
- Start with compatible product/component coverage and graph-based candidate retrieval.
- Rank candidates by interpretable factors: compatibility, geographic diversification, shared upstream exposure, and concentration risk.
- Introduce capacity, demand allocation, lead time, cost, and constrained optimization only when grounded in real or explicitly simulated inputs.
- Explain *why* each candidate is recommended and what information is missing.

### 4. Reproducible Benchmarking
Suggested comparisons:
- Degree vs. PageRank vs. betweenness for criticality ranking.
- Structural reachability vs. capacity-aware disruption simulation.
- Product-match baseline vs. risk-aware supplier ranking.
- Random node failure vs. targeted removal.

Possible metrics include affected reachability, unmet demand, service level, recovery fraction, extra cost, computation time, and uncertainty/sensitivity under different synthetic assumptions. Report assumptions and limitations beside every result.

## Preferred Technology Stack
- **Graph database:** Neo4j, Cypher; Neo4j Graph Data Science (GDS) where applicable.
- **Core language:** Python.
- **Data ingestion/transformation:** Polars (plus other Python tools as justified).
- **Graph/simulation experiments:** Neo4j GDS, NetworkX where a local simulation representation is useful.
- **Optional optimization:** SciPy or OR-Tools for constrained supplier allocation.
- **Backend/API:** FastAPI.
- **Interactive visualization:** React + Cytoscape.js, once core functionality is validated.
- **Environment:** Docker Compose; `uv` for Python dependency and environment management.
- **Quality:** pytest, type checking, GitHub Actions, documentation, reproducible sample runs.
- **Optional later:** RDF/ontology mapping, event feeds, GraphRAG/LLM interface, or GNN link prediction—but none is necessary for the first usable version.

## Development Priorities
1. Validate an accessible dataset and its provenance, schema, license, and limitations.
2. Create a usable Neo4j knowledge graph with reliable ingestion and Cypher queries.
3. Implement multi-hop impact analysis and a transparent failure-simulation baseline.
4. Implement interpretable supplier alternatives, with explicit assumptions.
5. Benchmark disruption and recovery methods on repeatable scenarios.
6. Add a small API and compelling visualization for a GitHub demonstration.

## Non-Goals / Guardrails
- Do **not** begin with an LLM agent or GraphRAG demo; core graph reasoning and risk analysis come first.
- Do **not** add GNNs only to make the project sound more sophisticated.
- Do **not** over-engineer cloud infrastructure, microservices, or folder architecture at this planning stage.
- Do **not** claim real-time monitoring, production scalability, proven supplier compatibility, or financial-loss accuracy without evidence.
- Keep features modular, testable, and explainable; prioritize a working end-to-end slice over a long list of planned capabilities.
- Distinguish **implemented**, **experimental**, and **planned** features clearly in the README.

## Intended GitHub Outcome
A hiring manager or engineer should be able to:
- Understand the supply chain question and graph schema quickly.
- Reproduce data ingestion and at least one disruption scenario.
- Inspect the exact paths and assumptions behind impact estimates and supplier recommendations.
- See quantitative comparisons against sensible baselines.
- Recognize hands-on **Neo4j, Cypher, knowledge graph modeling, graph analytics, simulation, and engineering** skills.
