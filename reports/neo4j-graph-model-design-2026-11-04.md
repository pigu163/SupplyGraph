# SupplyGraph 首版 Neo4j 图模型设计

设计日期：2026-11-04。更新：2026-11-06。状态：**Neo4j 异质图已导入；首轮 Cypher/GDS 结构分析已完成，动态查询服务待实现**。
本地 NetworkX 结果和 Neo4j 导入数据都来自 AnonNet 文件；n0 路径样例既有 CSV 结果，也已通过 Neo4j Cypher 复现。

## 1. 目标与现状

首版要回答：某个匿名公司有哪些文件方向的直接关系？沿该方向最多三跳可到哪些不同公司，各自的一条证据路径是什么？不同方向假设和节点移除会如何改变结构？

2026-11-06 将本地 AnonNet 快照导入 Neo4j **5.22 Community**。同一快照运行导入两次，计数保持为 7,245 家公司、43,926 条公司关系、102 个地点、5,755 条地点关系、3,706 个产品描述、6,708 条描述关系、17 个预测类别和 6,708 条类别关系。脚本与导入命令见项目 README。

## 2. 图模型

```text
(:Company:AnonNetCompany)-[:REPORTED_SUPPLY_LINK]->(:Company:AnonNetCompany)
          ├─[:REPORTED_LOCATION]──> (:LocationLabel)
          ├─[:REPORTED_DESCRIPTION]─> (:ProductDescription)
          └─[:PREDICTED_CATEGORY]──> (:ProductCategory)
```

### 节点

| 标签 | 属性 | 来源及含义 | 身份或缺失处理 |
|---|---|---|---|
| `Company:AnonNetCompany` | `anonnetId: STRING` | `nodes_anon.csv.id`，如 `n0` | AnonNet 命名空间内唯一；不能为空，先校验再导入。不同数据源的同名 ID 不能自动合并。 |
| 同上 | `locationRaw: STRING` | `nodes_anon.csv.location` 原文 | 空白对应缺失属性；不是完整工厂或经营地点清单。 |
| 同上 | `productsRaw: STRING` | `nodes_anon.csv.products` 整个单元格原文 | 空白对应缺失属性；不按逗号拆分，也不视为已核实的标准产品。 |
| 同上 | `snapshotId: STRING`, `nodeFileSha256: STRING`, `sourceRecord: INTEGER` | 输入快照、节点文件哈希和数据记录序号 | `sourceRecord` 从 1 开始，不含表头；用于回溯，不作为公司唯一键。 |
| `LocationLabel` | `label: STRING` | 分类 CSV 中的 `country` 标签 | 每个不同标签一个节点；未知标记不建立节点。 |
| `ProductDescription` | `descriptionId: STRING`, `text: STRING` | 产品描述原文和其 SHA-256 | 描述原文作为节点内容保存，哈希作为稳定键；不拆成单个产品。 |
| `ProductCategory` | `categoryId: STRING`, `label: STRING` | DeBERTa 预测的项目类别 | 17 个共享类别节点；它们表示模型分类，不代表已核实供货能力。 |

公司到地点、描述和类别的关系分别为 `REPORTED_LOCATION`、`REPORTED_DESCRIPTION` 和 `PREDICTED_CATEGORY`。类别关系保存模型名称、版本、taxonomy 版本和 top-1 分数。

本方案只容纳一个活动的 AnonNet 输入快照：`snapshotId` 定义为 `anonnet:<nodes文件SHA-256>:<edges文件SHA-256>`。两个文件的完整哈希见[来源记录](../dataset/README.md)。如要同时保存多个快照，需要先另行设计公司属性的版本化；不得用新快照静默覆盖旧属性。

导入器在写入前检查已有公司和供应关系的快照 ID；若发现不同或缺失的 ID，则拒绝导入。导入任务应串行运行，当前不提供自动替换快照的操作。

### 关系

| 起点 → 关系 → 终点 | 属性 | 方向、来源和边界 |
|---|---|---|
| `AnonNetCompany` → `REPORTED_SUPPLY_LINK` → `AnonNetCompany` | `snapshotId: STRING`, `edgeFileSha256: STRING`, `sourceRecord: INTEGER`, `directionMeaning: STRING`, `isSelfLoop: BOOLEAN` | 起点对应 `edges_anon.csv.source`，终点对应 `target`；数据提供方报告公司间供应关系，但供应商/客户方向尚未证实。`sourceRecord` 从 1 开始，不含表头。 |

`directionMeaning` 固定为 `file_source_to_target_business_direction_unverified`。时间、容量、交易量和置信度在源文件中没有提供，不补造。Neo4j 属性不存在表示缺失；生成 CSV 中的文本 `None` 应在未来导入时转换成缺失值，而不是创建一个名为 `None` 的地点或产品。

### 模型预测与异质图的边界

Neo4j 保留 NetworkX 图中的地点、完整产品描述和模型类别。每条非空描述使用 DeBERTa 得到一个 top-1 类别，当前分类输出直接用于探索；Neo4j 中同时保存模型版本和分数，便于解释分类来源。类别与描述都不表示已核实的供货能力或产品替代关系。

## 3. 唯一键、重复导入和自连接

**本次导入已执行；以下记录实现方式与结果。**

1. 导入器读取分类节点 CSV 与原始关系 CSV，计算两份文件哈希并生成快照 ID。源数据包含 7,245 个公司、43,926 条关系、0 条重复有向边和 4 条自连接。
2. 在写入前创建公司 ID、地点标签、产品描述哈希和类别 ID 的唯一性约束。首次导入前检查目标库为空；四项约束均在库中。

   ```cypher
   CREATE CONSTRAINT anonnet_company_id_unique IF NOT EXISTS
   FOR (c:AnonNetCompany) REQUIRE c.anonnetId IS UNIQUE;
   ```

3. Python Driver 使用 `UNWIND` 每批 1,000 行写入。先写节点，再写公司关系；关系 MERGE 键为已绑定的两端、`snapshotId` 和 CSV `sourceRecord`。
4. 地点、原始描述、预测类别分别建节点；类别分数和模型元数据保存在 `PREDICTED_CATEGORY` 上。未知地点和空描述不建立共享的未知节点。
5. 四条自连接保留并标记 `isSelfLoop=true`；反向关系保留为独立有向边。导入器对同一快照再次运行后，所有节点和关系数量保持不变。

Neo4j 的唯一属性约束并不要求所有节点都具有该属性；空 ID 由导入前校验处理。Python 导入器直接读取宿主机上的 CSV，因此 Compose 不再把原始 `dataset/` 挂载进 Neo4j 容器。

## 4. 查询契约与可复现结果

以下路径规则首先由本地 CSV 计算；n0 的正向三跳计数和一条来源路径已经在 Neo4j 5.22 中用 Cypher 复现。CSV 样例也可单独复现：

```bash
uv run python scripts/graph/model_examples.py
```

统一规则：以 `REPORTED_SUPPLY_LINK` 为边，最多 3 跳；分别支持 `forward`（文件 source→target）、`reverse` 和 `undirected`。按最少跳数对公司去重、排除起点，每家公司输出一条最短跳数路径；同跳数路径按公司 ID 字典序确定代表路径。返回路径时附带每条边的源文件、快照哈希和 `sourceRecord`。这些路径表示记录中的结构连接和潜在暴露，不证明真实传导、停产或损失。

| 查询问题 | 输入与实际 CSV 样例结果 | 解释 |
|---|---|---|
| 文件方向的一跳直接关系有哪些？ | `n0` 出边 6 个：`n1494, n2574, n2879, n3915, n536, n5491` | 只是 source→target 方向。 |
| 反方向直接关系有哪些？ | `n0` 入边 5 个：`n1148, n2103, n2349, n3915, n4970` | `n3915` 在两个方向均有记录；不能合并成一条有向记录。 |
| 最多三跳可达多少不同公司？ | `n0` forward 共 **1,023**：最短 1 跳 6、2 跳 127、3 跳 890 | 每个公司只计一次，起点不计；这是完整遍历得到的精确值。 |
| 一条路径的证据是什么？ | `n0 → n2574 → n1018`；边记录号 `4, 14457` | 记录号指 `edges_anon.csv` 的逻辑数据行，从 1 开始、不含表头。 |
| 是否存在三跳路径？ | `n0 → n536 → n5464 → n1001`；边记录号 `5, 2276, 32646` | 每条边都可回查相同哈希的来源文件。 |
| 方向假设是否影响规模？ | `n0` 三跳：forward **1,023**，reverse **751**，undirected **3,340** | 三种结构视图；不能借此确定哪一种是实际供应传播方向。 |
| 原始自连接如何展示？ | `n378 → n378`，边记录号 `1644` | 原始记录保留；不同公司计数与路径扩展不将其视为新的公司。 |

`n0` 的原始节点记录为 `location=Japan`、`products=Fertilizer`。它们是来源标签和文本描述，不能据此推断完整经营足迹或已经核实的产能。

### 截断与计数

当完整 BFS 已完成但页面最多显示 5 家公司时，结果示例为（省略每家公司的路径明细）：

```json
{
  "startCompany": "n0",
  "direction": "forward",
  "maxHops": 3,
  "displayLimit": 5,
  "companyIds": ["n1494", "n2574", "n2879", "n3915", "n536"],
  "totalUniqueCompanies": 1023,
  "countStatus": "exact",
  "outputTruncated": true,
  "traversalTruncated": false,
  "truncationReason": "result_limit"
}
```

展示顺序为 `(minimumHops, anonnetId)`；`outputTruncated` 仅说明展示列表截断，上例的总数仍为精确值。未来如果遍历因节点/边预算或超时提前结束，必须另行返回 `traversalTruncated=true`、原因以及 `countStatus=lower_bound`；已发现的不同公司数只是下界，未发现的公司为 `not_examined`，不能断言不存在。若一次 Neo4j 查询超时且没有安全保留部分结果，则返回超时/数量未知，不虚构下界。

查询不存在的公司应返回 `not_found`；存在但没有相应方向邻居的公司返回空列表及精确计数 0。`LIMIT` 限制返回行数，并不保证数据库只探索同样数量的路径；未来实现应采用有节点/边预算和超时的分层遍历，不直接枚举高连接节点的所有路径。Neo4j 默认限制路径中关系重复，但节点可以重复，因此应用层仍需显式的 visited/去重规则。[Neo4j 5 路径语义](https://neo4j.com/docs/cypher-manual/5/patterns/unique-relationship-paths/) · [可变长度路径](https://neo4j.com/docs/cypher-manual/5/patterns/variable-length-paths/)

## 5. 能支持什么 graph reasoning

| 能力 | 首版可支持的推理 | 限制及扩展 |
|---|---|---|
| 关系与多跳路径 | 直接关系、1–3 跳去重可达公司、最短跳数及路径证据 | 只能解释为文件方向的潜在结构暴露；不是已确认的上游/下游传播。 |
| 结构关键性 | 在明确投影方向、自连接处理和快照后比较度数、PageRank、介数、WCC | 排名是结构启发式，不等于实际损失或公司破产概率。 |
| 中断情景 | 在独立内存视图中模拟移除，比较连通性与可达性 | 不删除源图；容量、库存、替代和恢复时间目前未知。 |
| 产品候选检索 | 原始文本及模型类别可作为待评估的检索线索 | 等人工评估分类后再扩展图模型；同一大类不证明规格兼容、产能或可替代性。 |
| GDS | 已运行公司关系 PageRank、WCC、Louvain 和移除 n4859 的 WCC 对照 | 结果与参数见[Neo4j 深度分析](neo4j-deep-analysis-2026-11-06.md)；投影均为临时内存图，分析后删除，未向 Neo4j 节点写入算法属性。 |

需要优先解决的顾虑：供应商/客户方向未证实；快照日期、关系有效期和历史变化未知；地点与产品字段缺失且语义有限；分类尚未验证；数据许可再分发条款未明确；高连接节点使路径数量快速增长。容量与需求若用于后续模拟，应放在独立、固定种子且明确标注的合成层，不能冒充观测事实。
