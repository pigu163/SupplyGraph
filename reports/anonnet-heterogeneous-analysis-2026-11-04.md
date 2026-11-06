# AnonNet 异质图：NetworkX 构建与分析

数据来源：本地 AnonNet 分类 CSV 与原始关系 CSV；源网络快照产生日期未知。分析不连接 Neo4j。

## 图如何构建

使用 `networkx.MultiDiGraph`；节点 ID 有类型前缀，地点和产品描述节点使用对应文本作为标识。
每个公司保留原始字段；地点标签使用节点文件的 `country` 列并去除首尾空格，边保留原始地点文本；产品描述保留整个原始 CSV 单元格，不按逗号拆分。
缺失字段不建立未知共享节点。供应关系按 CSV 的 source → target 保存，业务上下游方向尚未证实；自连接、反向边和重复输入行均保留。

| 节点类型 | 数量 | 含义 |
|---|---:|---|
| Company | 7,245 | 匿名公司 |
| LocationLabel | 102 | `country` 文本标签 |
| ProductDescription | 3,706 | 整个原始产品字段，不是单个标准产品 |
| ProductCategory | 17 | 模型预测类别，不是已核实产品 |

| 关系类型 | 数量 | 含义 |
|---|---:|---|
| REPORTED_SUPPLY_LINK | 43,926 | 公司 → 公司，保留文件方向 |
| REPORTED_LOCATION | 5,755 | 公司 → 地点标签 |
| REPORTED_DESCRIPTION | 6,708 | 公司 → 产品描述原文 |
| PREDICTED_CATEGORY | 6,708 | 公司 → 模型预测类别；不表示供应关系 |

合计 **11,070 节点、63,097 条关系**。GraphML 文件为 `dataset/derived/anonnet-heterogeneous-categorized.graphml`；它由原始数据生成，保存在 Git 忽略的 `dataset/derived/` 下。

## 方法与结果

### 1. 公司间网络的结构

只投影 `REPORTED_SUPPLY_LINK`：7,245 个公司、43,926 条不同有向边，合并的重复有向记录 0 条。弱连通分量 1 个，最大强连通分量 3,871 家公司。
公司网络转为无向简单图时，反向边合并、移除 4 条自连接，得到 41,063 条边和 419 个割点。割点仅说明图连通性，不等于真实的单点故障。

**无向连接数最高的公司**（同一对公司的双向边计一次）：

| 公司 ID | 邻居数 |
|---|---:|
| `n4859` | 499 |
| `n5063` | 459 |
| `n6013` | 455 |
| `n6978` | 396 |
| `n6000` | 388 |

**介数中心性**：在该无向简单图上用 `nx.betweenness_centrality(k=32, seed=42, normalized=True)` 抽样估计；它衡量采样最短路径经过节点的程度。仅用单个种子抽样 32 个源点，排名用于探索，后续需做采样规模和种子敏感性分析。

| 公司 ID | 近似介数 |
|---|---:|
| `n6195` | 0.053346 |
| `n6301` | 0.049248 |
| `n5063` | 0.046011 |
| `n6956` | 0.041117 |
| `n6978` | 0.039397 |

连接数与抽样介数的前五名有 2 家重合；它们衡量不同结构特征，介数排名还受抽样影响。

### 2. 地点标签与公司关系

公司—地点子图为二部图：5,755/7,245 家公司有标签，涉及 102 个不同标签。来自 `country` 列、去除首尾空格后，出现最多的标签如下：

| 地点标签 | 公司数 |
|---|---:|
| Japan | 1,099 |
| China | 1,086 |
| United States | 720 |
| South Korea | 481 |
| Taiwan | 300 |

在排除自连接的公司关系中，**33,098** 条关系两端都有地点标签；其中 **15,900** 条（48.04%）标签相同，**17,198** 条标签不同。分母仅为双方地点均已知的有向关系行；反向关系分别计数，未知地点不算同地也不算异地。
这些是文本标签的比较，不能证明实际工厂位置、物流路线或地理风险。

### 3. 相同产品描述形成的两跳路径

公司—产品描述子图为二部图：6,708 家公司有描述，共 3,706 种不同的完整原文。其中 **462** 个原文被至少两家公司共享；**333** 个原文对应至少两个已知地点标签。

| 完整产品描述原文 | 公司数 |
|---|---:|
| Asset Management | 188 |
| Steel | 115 |
| Industrial Support Services | 108 |
| Software | 78 |
| Various power semiconductor devices for Power ECU Part of Vehicle | 72 |

例如：`n1085`（Indonesia）— `Asset Management` — `n1131`（South Korea）。这是公司—相同原文—公司两跳路径，两个公司之间在原始公司关系中没有直接边。
相同完整描述只是候选线索，不能证明它们生产相同规格、存在交易关系或可以相互替代。

### 4. 模型预测类别：覆盖、地点与网络结构

6,708/7,245 家公司有预测类别；537 家没有。每条非空描述被强制分配一个 top-1 类别，模型并未验证类别正确性。

| 预测类别 | 公司数 | 已知地点标签公司数 | 最常见地点标签（公司数） | 无向邻居数均值 | 中位数 | 相对分低于 0.3 / 有分数 |
|---|---:|---:|---|---:|---:|---:|
| METALS | 1,555 | 1,208 | China: 285, Japan: 240, South Korea: 116 | 10.68 | 3.0 | 254/1,555 |
| VEHICLES_TRANSPORT_EQUIPMENT | 1,443 | 1,285 | Japan: 250, China: 212, United States: 183 | 13.34 | 3.0 | 206/1,443 |
| ELECTRONICS | 869 | 751 | Japan: 160, China: 119, United States: 96 | 10.72 | 3.0 | 225/869 |
| CHEMICALS | 626 | 416 | China: 104, Japan: 76, United States: 40 | 12.36 | 4.0 | 116/626 |
| ENERGY_FUELS | 422 | 332 | China: 99, United States: 54, Japan: 26 | 16.83 | 5.0 | 86/422 |
| PLASTICS_RUBBER | 384 | 343 | Japan: 83, United States: 49, China: 42 | 10.28 | 2.0 | 350/384 |
| FINANCIAL_SERVICES | 333 | 295 | United States: 54, China: 28, Japan: 23 | 11.91 | 4.0 | 20/333 |
| BUSINESS_SERVICES | 262 | 226 | Japan: 49, China: 28, South Korea: 22 | 13.25 | 4.0 | 15/262 |
| MACHINERY_EQUIPMENT | 237 | 200 | Japan: 67, China: 35, United States: 28 | 8.71 | 3.0 | 89/237 |
| SOFTWARE_IT | 205 | 152 | United States: 41, China: 24, Japan: 17 | 11.40 | 2.0 | 35/205 |
| FORESTRY_PAPER | 103 | 81 | Japan: 22, China: 14, United States: 11 | 14.16 | 4.0 | 6/103 |
| MINERALS_BUILDING | 92 | 74 | China: 15, Japan: 7, Indonesia: 6 | 7.61 | 2.0 | 12/92 |
| AGRICULTURE_FOOD | 77 | 61 | Japan: 18, China: 15, United States: 11 | 7.29 | 3.0 | 9/77 |
| HEALTHCARE_PRODUCTS | 51 | 42 | China: 7, Japan: 5, United States: 5 | 6.06 | 3.0 | 51/51 |
| ELECTRICAL_EQUIPMENT | 22 | 20 | China: 4, United States: 4, Canada: 2 | 11.91 | 4.5 | 5/22 |
| TEXTILES_APPAREL | 20 | 17 | Japan: 7, China: 3, South Korea: 2 | 10.50 | 3.5 | 4/20 |
| LOGISTICS_SERVICES | 7 | 6 | China: 2, Japan: 2, Sweden: 1 | 4.14 | 2.0 | 4/7 |

按公司间有向简单图统计，排除自连接后，两端类别均已知的关系有 **40,903** 条；其中同类别 **8,325** 条（20.35%）。按这些边的 source/target 类别边际频率独立配对得到的描述性参照为 14.16%；这不是随机置换检验，也不控制公司度数或抽样偏差。反向边分别计数，未知类别不参与分母。

| 文件 source 类别 | 文件 target 类别 | 关系数 |
|---|---|---:|
| VEHICLES_TRANSPORT_EQUIPMENT | VEHICLES_TRANSPORT_EQUIPMENT | 3,401 |
| METALS | METALS | 2,614 |
| METALS | VEHICLES_TRANSPORT_EQUIPMENT | 2,058 |
| ELECTRONICS | VEHICLES_TRANSPORT_EQUIPMENT | 1,410 |
| VEHICLES_TRANSPORT_EQUIPMENT | METALS | 1,187 |
| ENERGY_FUELS | METALS | 1,100 |
| ENERGY_FUELS | VEHICLES_TRANSPORT_EQUIPMENT | 979 |
| METALS | ELECTRONICS | 949 |
| ELECTRONICS | METALS | 845 |
| CHEMICALS | METALS | 820 |

按文件 source 类别分组，下表分母为该类别指向类别已知目标的非自连接关系数；百分比用于描述该文件方向的结构，不能解释成真实供货比例。

| source 类别 | 符合条件的关系 | 同类别关系 | 同类别占比 | 最常见 target 类别（关系数） |
|---|---:|---:|---:|---|
| METALS | 8,997 | 2,614 | 29.05% | METALS (2,614) |
| VEHICLES_TRANSPORT_EQUIPMENT | 8,841 | 3,401 | 38.47% | VEHICLES_TRANSPORT_EQUIPMENT (3,401) |
| ELECTRONICS | 5,123 | 707 | 13.80% | VEHICLES_TRANSPORT_EQUIPMENT (1,410) |
| ENERGY_FUELS | 4,882 | 446 | 9.14% | METALS (1,100) |
| CHEMICALS | 3,898 | 708 | 18.16% | METALS (820) |
| FINANCIAL_SERVICES | 1,953 | 111 | 5.68% | VEHICLES_TRANSPORT_EQUIPMENT (506) |
| PLASTICS_RUBBER | 1,877 | 93 | 4.95% | VEHICLES_TRANSPORT_EQUIPMENT (478) |
| BUSINESS_SERVICES | 1,868 | 123 | 6.58% | VEHICLES_TRANSPORT_EQUIPMENT (409) |
| SOFTWARE_IT | 1,043 | 50 | 4.79% | VEHICLES_TRANSPORT_EQUIPMENT (340) |
| MACHINERY_EQUIPMENT | 962 | 25 | 2.60% | VEHICLES_TRANSPORT_EQUIPMENT (288) |
| FORESTRY_PAPER | 614 | 21 | 3.42% | METALS (165) |
| MINERALS_BUILDING | 274 | 20 | 7.30% | METALS (91) |
| AGRICULTURE_FOOD | 221 | 4 | 1.81% | METALS (46) |
| HEALTHCARE_PRODUCTS | 132 | 2 | 1.52% | VEHICLES_TRANSPORT_EQUIPMENT (45) |
| TEXTILES_APPAREL | 132 | 0 | 0.00% | VEHICLES_TRANSPORT_EQUIPMENT (41) |
| ELECTRICAL_EQUIPMENT | 79 | 0 | 0.00% | ENERGY_FUELS (16) |
| LOGISTICS_SERVICES | 7 | 0 | 0.00% | CHEMICALS (2) |

预测明细中有 3,706 条不同描述的分数（对应 6,708 家公司）；其中最高相对分低于 0.3 的分别有 595 条描述、1,487 家公司。0.3 只是用于统计可能需要复核样本的探索阈值，不是经过标注验证的置信阈值；分数不是校准概率。

低分占比较高的类别（仅列有分数公司至少 20 家、低分占比至少 75% 的类别）：`PLASTICS_RUBBER` 350/384（91.1%）；`HEALTHCARE_PRODUCTS` 51/51（100.0%）。这些类别适合优先抽样复核，不能仅凭分数认定预测错误。

示例：`n1000`（South Korea，`Copper anode`）与 `n1011`（United States，`Niobium, Software, T-foundry, Software, Long flame coal, Wire rod, Uranium, Wire rods, Molten metal, Anode slime, Small form and t-foundry, Ferrite strontium powder, Heavy angles, High purity, Steel, Hot-rolled steel`）共同连接预测类别 `METALS`，两公司间没有直接供应边。共同类别与地点差异只提供候选检索线索，不证明产品兼容、实际供货或替代可行。

### 5. 结构性故障实验：静态度数定向 vs 随机

在仅含公司关系、已移除自连接的无向简单图上，同时移除 72 家公司（约 1%）；定向组使用原图无向连接数最高的固定排序，随机组不放回抽样。随机组重复 20 次，种子 42；每次剩余 7,173 个节点。

| 方法 | 最大连通分量 | 占剩余节点 |
|---|---:|---:|
| 静态度数定向 | 6,413 | 89.40% |
| 随机移除：平均 | 7,148.70 | 99.66% |
| 随机移除：范围 | 7,090–7,172 | 98.84%–99.99% |

该实验只比较网络拓扑在两种移除规则下的变化；它没有模拟故障概率、供需、库存、产能、替代和真实损失，不能作为企业实际韧性结论。

## 解释边界与后续

- `source → target` 的供应商/客户含义仍待数据提供方说明；因此入度、出度和有向路径不标注上下游业务角色。
- `ProductDescription` 是原始文本字段，不等同于单个产品；相同地点或描述创建的两跳路径也不是新增供应关系。
- `ProductCategory` 是模型的单标签预测，类别混合了产品和服务；分数未经校准，类别未进行人工准确率评估。类别节点只用于类别分析，绝不参与供应网络投影和故障实验。
- 地点、产品缺失分别影响地理比较与描述匹配；地点字段不代表公司全部工厂和运营地。
- 本次使用 NetworkX，未将图导入 Neo4j，也未运行 GDS。后续若映射到 Neo4j，应先核对版本、目标数据库现状、投影方向和源字段语义。

## 复现

```bash
uv sync --locked
uv run python scripts/graph/construct.py
uv run python scripts/graph/analyze.py
```

NetworkX 版本：3.7。节点文件 `nodes_anon_categorized.csv` SHA-256：`4303aabbae56c5fb5727963d05ea034836025f3ce56994b32c93d19f823f5aeb`；关系文件 SHA-256：`27a141f1ac38588dd30100c362936778248f9fa7cd96163637f81098c46f8088`。
分类前节点文件 SHA-256：`38ef22d13fca6e3950f22748a395d8263fdd2a872e3b61dc776ca1c00b62a693`；原始节点字段与分类 CSV 逐行核对：通过。
分类模型：`MoritzLaurer/deberta-v3-base-zeroshot-v2.0`；版本：`8e7e5af5983a0ddb1a5b45a38b129ab69e2258e8`；taxonomy：`1.0`。
图构建、GraphML 读取及分析结果均基于本地 CSV；脚本不修改源文件或数据库。

NetworkX 官方说明：[二部图及节点集合](https://networkx.org/documentation/stable/reference/algorithms/bipartite.html) · [介数中心性](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.centrality.betweenness_centrality.html)。
