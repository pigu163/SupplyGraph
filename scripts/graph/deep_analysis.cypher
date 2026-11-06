// Run read queries separately in Neo4j Browser. The final section creates only
// in-memory GDS projections; drop them after the algorithms finish.

// 1. Company degree distributions in the CSV source -> target direction.
MATCH (c:AnonNetCompany)
OPTIONAL MATCH (c)-[r:REPORTED_SUPPLY_LINK]->()
WITH c, count(r) AS outDegree
RETURN count(c) AS companies,
       avg(outDegree) AS mean,
       percentileCont(outDegree, 0.5) AS median,
       percentileCont(outDegree, 0.9) AS p90,
       percentileCont(outDegree, 0.99) AS p99,
       max(outDegree) AS max;

MATCH (c:AnonNetCompany)
OPTIONAL MATCH ()-[r:REPORTED_SUPPLY_LINK]->(c)
WITH c, count(r) AS inDegree
RETURN count(c) AS companies,
       avg(inDegree) AS mean,
       percentileCont(inDegree, 0.5) AS median,
       percentileCont(inDegree, 0.9) AS p90,
       percentileCont(inDegree, 0.99) AS p99,
       max(inDegree) AS max;

// Separate zero-degree counts avoid multiplying incoming and outgoing rows.
MATCH (c:AnonNetCompany)
OPTIONAL MATCH (c)-[r:REPORTED_SUPPLY_LINK]->()
WITH c, count(r) AS outDegree
WHERE outDegree = 0
RETURN count(c) AS zeroOutDegreeCompanies;

MATCH (c:AnonNetCompany)
OPTIONAL MATCH ()-[r:REPORTED_SUPPLY_LINK]->(c)
WITH c, count(r) AS inDegree
WHERE inDegree = 0
RETURN count(c) AS zeroInDegreeCompanies;

// 2. Hubs at each endpoint of the recorded direction.
MATCH (c:AnonNetCompany)
OPTIONAL MATCH (c)-[r:REPORTED_SUPPLY_LINK]->()
WITH c, count(r) AS outDegree
RETURN c.anonnetId AS company, outDegree
ORDER BY outDegree DESC, company
LIMIT 15;

MATCH (c:AnonNetCompany)
OPTIONAL MATCH ()-[r:REPORTED_SUPPLY_LINK]->(c)
WITH c, count(r) AS inDegree
RETURN c.anonnetId AS company, inDegree
ORDER BY inDegree DESC, company
LIMIT 15;

// Mutual dyads; each unordered company pair is counted once.
MATCH (a:AnonNetCompany)-[:REPORTED_SUPPLY_LINK]->(b:AnonNetCompany)
WHERE a <> b
  AND a.anonnetId < b.anonnetId
  AND EXISTS { MATCH (b)-[:REPORTED_SUPPLY_LINK]->(a) }
RETURN count(*) AS mutualDyads;

// 3. n0's unique reachable companies by minimum hop in the file direction.
MATCH (s:AnonNetCompany {anonnetId: 'n0'})
OPTIONAL MATCH (s)-[:REPORTED_SUPPLY_LINK]->(one:AnonNetCompany)
WITH s, collect(DISTINCT one) AS hop1
CALL {
  WITH hop1
  UNWIND hop1 AS a
  MATCH (a)-[:REPORTED_SUPPLY_LINK]->(b:AnonNetCompany)
  RETURN collect(DISTINCT b) AS hop2
}
CALL {
  WITH hop2
  UNWIND hop2 AS b
  MATCH (b)-[:REPORTED_SUPPLY_LINK]->(c:AnonNetCompany)
  RETURN collect(DISTINCT c) AS hop3
}
WITH s,
     [x IN hop1 WHERE x <> s] AS onlyHop1,
     [x IN hop2 WHERE x <> s AND NOT x IN hop1] AS onlyHop2,
     [x IN hop3 WHERE x <> s AND NOT x IN hop1 AND NOT x IN hop2] AS onlyHop3
RETURN size(onlyHop1) AS hop1,
       size(onlyHop2) AS hop2,
       size(onlyHop3) AS hop3,
       size(onlyHop1) + size(onlyHop2) + size(onlyHop3) AS uniqueWithin3;

// One shortest-path evidence example, including source CSV row numbers.
MATCH p = (s:AnonNetCompany {anonnetId: 'n0'})-[r1:REPORTED_SUPPLY_LINK]->
          (a:AnonNetCompany {anonnetId: 'n2574'})-[r2:REPORTED_SUPPLY_LINK]->
          (b:AnonNetCompany {anonnetId: 'n1018'})
RETURN [n IN nodes(p) | n.anonnetId] AS path,
       [r IN relationships(p) | r.sourceRecord] AS sourceRecords;

// 4. Location-label mixing for links whose two companies have a label.
MATCH (a:AnonNetCompany)-[:REPORTED_SUPPLY_LINK]->(b:AnonNetCompany)
WHERE a <> b
MATCH (a)-[:REPORTED_LOCATION]->(la:LocationLabel)
MATCH (b)-[:REPORTED_LOCATION]->(lb:LocationLabel)
RETURN count(*) AS knownBoth,
       sum(CASE WHEN la.label = lb.label THEN 1 ELSE 0 END) AS sameLabel,
       sum(CASE WHEN la.label <> lb.label THEN 1 ELSE 0 END) AS differentLabel;

// Category pairs along recorded links; categories are model predictions.
MATCH (a:AnonNetCompany)-[:REPORTED_SUPPLY_LINK]->(b:AnonNetCompany)
WHERE a <> b
MATCH (a)-[:PREDICTED_CATEGORY]->(ca:ProductCategory)
MATCH (b)-[:PREDICTED_CATEGORY]->(cb:ProductCategory)
RETURN count(*) AS classifiedEdges,
       sum(CASE WHEN ca.categoryId = cb.categoryId THEN 1 ELSE 0 END) AS sameCategoryEdges;

MATCH (a:AnonNetCompany)-[:REPORTED_SUPPLY_LINK]->(b:AnonNetCompany)
WHERE a <> b
MATCH (a)-[:PREDICTED_CATEGORY]->(ca:ProductCategory)
MATCH (b)-[:PREDICTED_CATEGORY]->(cb:ProductCategory)
RETURN ca.categoryId AS sourceCategory,
       cb.categoryId AS targetCategory,
       count(*) AS edges
ORDER BY edges DESC
LIMIT 15;

// Product descriptions are exact raw text values, not standardized products.
MATCH (d:ProductDescription)<-[:REPORTED_DESCRIPTION]-(c:AnonNetCompany)
WITH d, count(c) AS companies
WHERE companies > 1
RETURN d.text AS description, companies
ORDER BY companies DESC
LIMIT 10;

// The score is the model's top-1 score, not a calibrated correctness probability.
MATCH (c:AnonNetCompany)-[r:PREDICTED_CATEGORY]->(k:ProductCategory)
RETURN k.categoryId AS category,
       count(c) AS companies,
       avg(r.score) AS meanScore,
       percentileCont(r.score, 0.5) AS medianScore,
       sum(CASE WHEN r.score < 0.3 THEN 1 ELSE 0 END) AS scoreBelow03
ORDER BY companies DESC;

// 5. GDS: PageRank on file direction, WCC on the same company-only projection,
// and Louvain on an undirected projection. Choose fresh graph names per run.
CALL gds.graph.project(
  'anonnetDirectedAnalysis',
  'AnonNetCompany',
  {REPORTED_SUPPLY_LINK: {orientation: 'NATURAL'}}
)
YIELD graphName, nodeCount, relationshipCount, projectMillis;

CALL gds.pageRank.stats('anonnetDirectedAnalysis', {
  maxIterations: 100,
  tolerance: 0.000001,
  dampingFactor: 0.85
})
YIELD ranIterations, didConverge, computeMillis, centralityDistribution
RETURN ranIterations, didConverge, computeMillis, centralityDistribution;

CALL gds.pageRank.stream('anonnetDirectedAnalysis', {
  maxIterations: 100,
  tolerance: 0.000001,
  dampingFactor: 0.85
})
YIELD nodeId, score
RETURN gds.util.asNode(nodeId).anonnetId AS company, score
ORDER BY score DESC
LIMIT 15;

CALL gds.wcc.stats('anonnetDirectedAnalysis')
YIELD componentCount, componentDistribution, computeMillis
RETURN componentCount, componentDistribution, computeMillis;

CALL gds.graph.project(
  'anonnetUndirectedAnalysis',
  'AnonNetCompany',
  {REPORTED_SUPPLY_LINK: {orientation: 'UNDIRECTED'}}
)
YIELD graphName, nodeCount, relationshipCount, projectMillis;

CALL gds.louvain.stats('anonnetUndirectedAnalysis', {concurrency: 1})
YIELD communityCount, modularity, modularities, computeMillis, communityDistribution
RETURN communityCount, modularity, modularities, computeMillis, communityDistribution;

CALL gds.louvain.stream('anonnetUndirectedAnalysis', {concurrency: 1})
YIELD communityId
WITH communityId, count(*) AS companies
RETURN communityId, companies
ORDER BY companies DESC;

// Leading predicted categories inside each topology community.
CALL gds.louvain.stream('anonnetUndirectedAnalysis', {concurrency: 1})
YIELD nodeId, communityId
WITH communityId, gds.util.asNode(nodeId) AS c
OPTIONAL MATCH (c)-[:PREDICTED_CATEGORY]->(k:ProductCategory)
WITH communityId, count(DISTINCT c) AS companies,
     [category IN collect(k.categoryId) WHERE category IS NOT NULL] AS categories
UNWIND categories AS category
WITH communityId, companies, category, count(category) AS categoryCompanies
ORDER BY communityId, categoryCompanies DESC
WITH communityId, companies,
     collect({category: category, companies: categoryCompanies})[0..3] AS topCategories
RETURN communityId, companies, topCategories
ORDER BY companies DESC;

CALL gds.graph.drop('anonnetDirectedAnalysis');
CALL gds.graph.drop('anonnetUndirectedAnalysis');

// Single-node failure scenario. This procedure is deprecated in GDS 2.10.1,
// but is still available there for filtered Cypher projections.
CALL gds.graph.project.cypher(
  'anonnetWithoutN4859',
  "MATCH (n:AnonNetCompany) WHERE n.anonnetId <> 'n4859' RETURN id(n) AS id",
  "MATCH (a:AnonNetCompany)-[:REPORTED_SUPPLY_LINK]->(b:AnonNetCompany) WHERE a.anonnetId <> 'n4859' AND b.anonnetId <> 'n4859' AND a <> b RETURN id(a) AS source, id(b) AS target"
)
YIELD graphName, nodeCount, relationshipCount, projectMillis;

CALL gds.wcc.stream('anonnetWithoutN4859')
YIELD componentId
WITH componentId, count(*) AS componentSize
RETURN count(*) AS componentCount,
       max(componentSize) AS largestComponent,
       sum(CASE WHEN componentSize = 1 THEN 1 ELSE 0 END) AS singletonComponents;

CALL gds.graph.drop('anonnetWithoutN4859');
