# 多源研发证据 RAG 系统：开源参考项目调研

> **Versioned R&D Evidence RAG**
>
> 各环节复用地图、项目卡片、选型矩阵、许可证与 PoC 建议

> **覆盖代码智能、Agent 会话、科研文档、实验谱系、知识图、搜索、评测和安全。**
>
> 为每个项目给出复用等级、集成边界、替代项、MVP 决策和许可证提示。
>
> 结论：模块化组合，统一 Ontology、Cross-source Linker 和 Evidence Pack 必须自研。

| **文档类型**    | 技术调研 / 开源选型 |
|-----------------|---------------------|
| **版本 / 状态** | v1.0 / 选型评审稿   |
| **日期**        | 2026-07-21          |
| **责任方**      | 研发知识系统项目组  |
| **密级**        | 内部设计资料        |

**文档用途**

用于产品、架构、数据、算法、安全和平台团队的方案评审、PoC 规划与实施拆解。文中开源项目和许可证信息应在锁定具体版本时重新核验。

<a id="toc"></a>
## 目录

- [1. 调研说明与选型结论](#section-1)
  - [1.1 调研目标](#section-1-1)
  - [1.2 复用等级定义](#section-1-2)
  - [1.3 总体选型结论](#section-1-3)
  - [1.4 必须自研的五个模块](#section-1-4)
- [2. 评估框架](#section-2)
  - [2.1 评估维度](#section-2-1)
  - [2.2 选型原则](#section-2-2)
  - [2.3 PoC 评分模板](#section-2-3)
- [3. 推荐总体复用组合](#section-3)
  - [3.1 组件职责图](#section-3-1)
  - [3.2 为什么不选单一通用 RAG 平台做核心](#section-3-2)
  - [3.3 组件替换原则](#section-3-3)
- [4. 代码仓库与代码智能](#section-4)
  - [4.1 Tree-sitter](#section-4-1)
  - [4.2 SCIP Code Intelligence Protocol](#section-4-2)
  - [4.3 Zoekt](#section-4-3)
  - [4.4 Aider](#section-4-4)
  - [4.5 Joern](#section-4-5)
  - [4.6 Sourcebot](#section-4-6)
  - [4.7 Universal Ctags](#section-4-7)
  - [4.8 代码侧选型小结](#section-4-8)
- [5. Codex 与 Agent 会话](#section-5)
  - [5.1 Codex App Server / JSONL / SDK](#section-5-1)
  - [5.2 OpenHands](#section-5-2)
  - [5.3 SWE-agent](#section-5-3)
  - [5.4 Continue](#section-5-4)
  - [5.5 OpenInference](#section-5-5)
  - [5.6 Agent 会话侧小结](#section-5-6)
- [6. 文档解析与科研问答](#section-6)
  - [6.1 Docling](#section-6-1)
  - [6.2 GROBID](#section-6-2)
  - [6.3 MinerU](#section-6-3)
  - [6.4 PaperQA2](#section-6-4)
  - [6.5 Unstructured](#section-6-5)
  - [6.6 Papermill](#section-6-6)
  - [6.7 nbdime](#section-6-7)
  - [6.8 RO-Crate](#section-6-8)
  - [6.9 文档与科研侧小结](#section-6-9)
- [7. 实验管理、数据版本和谱系](#section-7)
  - [7.1 MLflow](#section-7-1)
  - [7.2 DVC](#section-7-2)
  - [7.3 OpenLineage](#section-7-3)
  - [7.4 lakeFS](#section-7-4)
  - [7.5 in-toto](#section-7-5)
  - [7.6 Pachyderm / Kubeflow Pipelines 等](#section-7-6)
  - [7.7 实验与谱系侧小结](#section-7-7)
- [8. 元数据、知识图与时间图](#section-8)
  - [8.1 OpenMetadata](#section-8-1)
  - [8.2 DataHub](#section-8-2)
  - [8.3 Graphiti](#section-8-3)
  - [8.4 Microsoft GraphRAG](#section-8-4)
  - [8.5 LightRAG](#section-8-5)
  - [8.6 Apache Atlas](#section-8-6)
  - [8.7 图与元数据侧小结](#section-8-7)
- [9. RAG 编排、搜索与存储](#section-9)
  - [9.1 Haystack](#section-9-1)
  - [9.2 LlamaIndex](#section-9-2)
  - [9.3 LangChain / LangGraph](#section-9-3)
  - [9.4 RAGFlow](#section-9-4)
  - [9.5 Dify](#section-9-5)
  - [9.6 PostgreSQL + pgvector](#section-9-6)
  - [9.7 Qdrant](#section-9-7)
  - [9.8 OpenSearch](#section-9-8)
  - [9.9 Neo4j 等图数据库](#section-9-9)
  - [9.10 编排与存储侧小结](#section-9-10)
- [10. 工作流、可观测性与评测](#section-10)
  - [10.1 Temporal](#section-10-1)
  - [10.2 Langfuse](#section-10-2)
  - [10.3 Arize Phoenix](#section-10-3)
  - [10.4 Ragas](#section-10-4)
  - [10.5 promptfoo](#section-10-5)
  - [10.6 OpenTelemetry Collector](#section-10-6)
  - [10.7 评测工具与自定义指标分工](#section-10-7)
- [11. 身份、授权与安全](#section-11)
  - [11.1 Keycloak](#section-11-1)
  - [11.2 OpenFGA](#section-11-2)
  - [11.3 Open Policy Agent](#section-11-3)
  - [11.4 Gitleaks](#section-11-4)
  - [11.5 TruffleHog](#section-11-5)
  - [11.6 安全侧小结](#section-11-6)
- [12. 产品与 UI 参考](#section-12)
  - [12.1 RAGFlow：文档证据体验](#section-12-1)
  - [12.2 Sourcebot：代码证据体验](#section-12-2)
  - [12.3 Renku：科研项目工作区](#section-12-3)
  - [12.4 OpenHands：Agent 运行体验](#section-12-4)
  - [12.5 OpenMetadata：组织级上下文体验](#section-12-5)
  - [12.6 推荐 UI 信息架构](#section-12-6)
- [13. 项目对比矩阵](#section-13)
  - [13.1 代码、会话与文档](#section-13-1)
  - [13.2 实验、元数据、图与搜索](#section-13-2)
  - [13.3 工作流、可观测、评测与安全](#section-13-3)
- [14. 许可证与供应链尽调](#section-14)
  - [14.1 基本原则](#section-14-1)
  - [14.2 重点审查项目](#section-14-2)
  - [14.3 供应链治理](#section-14-3)
- [15. PoC 复用计划](#section-15)
  - [15.1 PoC 目标](#section-15-1)
  - [15.2 第一批组件](#section-15-2)
  - [15.3 三条必须跑通的纵向切片](#section-15-3)
  - [15.4 PoC 决策门槛](#section-15-4)
  - [15.5 建议实施顺序](#section-15-5)
- [16. 项目卡片索引与官方链接](#section-16)
  - [16.1 直接复用优先](#section-16-1)
  - [16.2 适配与算法参考](#section-16-2)
  - [16.3 后期基础设施候选](#section-16-3)
- [17. 最终推荐](#section-17)

<a id="section-1"></a>
## 1. 调研说明与选型结论

[返回目录](#toc)

<a id="section-1-1"></a>
### 1.1 调研目标

本文面向“多源研发证据 RAG 系统”，整理各环节可参考、可复用的开源项目，并给出明确的复用边界。调研范围不局限于通用 RAG，覆盖代码智能、Agent 会话、科研文档、实验管理、数据版本、运行谱系、元数据图、时间知识图、搜索存储、工作流、可观测、评测、身份授权、安全和产品交互。

调研结论是：目前没有一个单体开源项目能完整覆盖以下闭环：

```text
代码仓库版本与 Symbol
+ Codex 会话、命令与 Patch
+ 科研文档、表格和公式
+ Experiment Run、配置和数据版本
+ 跨源时间关系
+ 证据权威度、失效、引用和 ACL
```

因此不建议直接 Fork 一个通用知识库作为系统核心。推荐采用**模块化组合 + 自研证据内核**：成熟项目承担解析、搜索、实验记录、谱系、工作流和评测；项目自身掌握统一 Ontology、稳定 ID、Cross-source Linker、时间版本、Evidence Pack、冲突裁决和结论有效性。

![图 1 开源项目分层复用图](多源研发证据RAG系统_开源参考项目调研_v1.0_assets/image1.png)

图 1 开源项目分层复用图

<a id="section-1-2"></a>
### 1.2 复用等级定义

| **等级**         | **含义**                                          | **行动**                             |
|------------------|---------------------------------------------------|--------------------------------------|
| A：直接复用      | 能以服务、库或协议稳定承担目标职责                | 进入 MVP 技术栈，编写薄 Adapter      |
| B：适配复用      | 核心能力可用，但需领域模型、权限或事件适配        | 建立隔离层，避免领域模型被其绑定     |
| C：算法/产品参考 | 适合借鉴算法、数据模型或 UI，不宜直接作为核心依赖 | 阅读源码、做小规模实验、重写关键部分 |
| D：后期候选      | 有价值但对 MVP 过重或需求未验证                   | 设置引入门槛，暂不部署               |
| R：需审慎        | 许可证、架构锁定、维护状态或安全边界需重点审查    | 法务/安全/供应链评估后决定           |

<a id="section-1-3"></a>
### 1.3 总体选型结论

<a id="mvp-推荐组合"></a>
#### MVP 推荐组合

```text
查询编排 Haystack + Python API
代码结构与关系 Tree-sitter + SCIP
代码精确检索 Zoekt
Codex 会话摄取 Codex App Server / JSONL / SDK Adapter
科研文档解析 Docling + GROBID
实验事实与数据版本 MLflow + DVC
运行谱系 OpenLineage-compatible event model
事务与向量 PostgreSQL + pgvector
原始文件 S3 / MinIO
长任务工作流 Temporal
Trace 语义与后端 OpenInference + Langfuse 或 Phoenix（核验许可）
身份与资源权限 Keycloak + OpenFGA
评测与回归 Ragas + promptfoo + 自定义领域指标
Secret 扫描 Gitleaks
```

<a id="后期按瓶颈引入"></a>
#### 后期按瓶颈引入

```text
大规模向量过滤 Qdrant
大规模全文与聚合 OpenSearch
时间知识和推断关系 Graphiti
全局文档主题问题 Microsoft GraphRAG / LightRAG
组织级元数据治理 OpenMetadata
深层代码数据流 Joern
数据湖版本 lakeFS
科研成果交换 RO-Crate
```

> **核心判断**
>
> 直接复用组件的前提是它只承担清晰职责。任何外部框架都不应成为统一实体 ID、Evidence Edge、Claim 有效性或 ACL 的唯一事实源。

<a id="section-1-4"></a>
### 1.4 必须自研的五个模块

> **1. Unified Ontology & Stable URI**：三类来源共享的实体、关系、时间和版本规范。
>
> **2. Cross-source Linker**：Session↔Patch↔Commit、Run↔Commit↔Dataset、Claim↔Run。
>
> **3. Temporal & Version Resolver**：按时间解析 Branch、事实有效区间和当前版本。
>
> **4. Evidence Authority & Conflict Engine**：依据查询类型裁决来源强度、反证和缺口。
>
> **5. Evidence Pack & Citation Protocol**：稳定的模型输入、引用映射和回答约束。

<a id="section-2"></a>
## 2. 评估框架

[返回目录](#toc)

<a id="section-2-1"></a>
### 2.1 评估维度

| **维度**   | **核心问题**                                      |
|------------|---------------------------------------------------|
| 能力匹配   | 是否真正解决本环节问题，还是仅提供相邻能力？      |
| 可集成性   | API、SDK、事件、文件格式和部署方式是否清晰？      |
| 数据模型   | 是否能映射到自有 Ontology，还是强迫使用内部抽象？ |
| 版本与时间 | 是否保留 Commit、Run、版本、历史和有效区间？      |
| 权限       | 是否支持检索前过滤、租户隔离和删除传播？          |
| 可观测性   | 是否能导出 Trace、指标、错误和成本？              |
| 运维成本   | 组件数量、资源消耗、升级和备份复杂度如何？        |
| 扩展性     | 是否有插件机制、协议或稳定的序列化格式？          |
| 许可证     | 是否允许目标商业模式和部署方式？是否有混合目录？  |
| 社区风险   | 维护活跃度、单一厂商依赖和兼容性风险如何？        |

<a id="section-2-2"></a>
### 2.2 选型原则

- 协议和数据格式优先于平台锁定，例如优先采用 SCIP、OpenLineage、OpenTelemetry 语义。

- 小而专的组件优先于“包揽全栈”但难以约束的数据模型。

- 确定性事实源优先于 LLM 自动抽取图。

- MVP 优先减少一致性边界，后续才拆独立搜索、向量和图服务。

- 许可证以锁定的 Tag/Commit 为准，README 中的简单标注不能替代法律审查。

- 对用户会话、代码和实验数据，必须验证自托管、数据保留和遥测行为。

<a id="section-2-3"></a>
### 2.3 PoC 评分模板

```text
project: example
version_or_commit: x.y.z / sha
capability_fit: 1-5
integration_effort: 1-5 # 分数越高越容易
operational_cost: 1-5 # 分数越高越低成本
license_fit: 1-5
security_fit: 1-5
benchmark_result:
quality: ...
latency: ...
resources: ...
blocking_issues: []
decision: adopt | adapt | reference | defer | reject
owner: ...
review_date: ...
```

<a id="section-3"></a>
## 3. 推荐总体复用组合

[返回目录](#toc)

<a id="section-3-1"></a>
### 3.1 组件职责图

| **自研服务**       | **首选复用项目**                | **集成边界**                                           |
|--------------------|---------------------------------|--------------------------------------------------------|
| Code Ingestion     | Tree-sitter、SCIP、Zoekt        | 自研 Git/ACL/Entity Adapter，项目只输出结构和搜索结果  |
| Codex Adapter      | Codex App Server、JSONL、SDK    | 原始事件进入自有 Event Store，不把会话平台当长期知识库 |
| Research Parser    | Docling、GROBID                 | 统一映射 Document/Table/Figure/Claim，保留原始页坐标   |
| Experiment Adapter | MLflow、DVC、OpenLineage        | Run/Metric/Artifact 为事实，映射到统一 ID              |
| Query Pipeline     | Haystack                        | 仅作编排容器，Evidence Pack 和领域规则自研             |
| Metadata Store     | PostgreSQL、pgvector            | 统一事务、关系、ACL 和向量 Generation                  |
| Workflow           | Temporal                        | 工作流定义自研，Activity 调用各解析器和索引器          |
| Observability      | OpenInference、Langfuse/Phoenix | 语义标准与后端分离，敏感正文默认不出 Trace             |
| Authorization      | Keycloak、OpenFGA               | 身份与关系授权分工，检索器接收授权过滤                 |
| Evaluation         | Ragas、promptfoo                | 通用指标和 CI；领域指标、Golden Set 自研               |

<a id="section-3-2"></a>
### 3.2 为什么不选单一通用 RAG 平台做核心

RAGFlow、Dify、LlamaIndex、LangChain 等项目可以加快知识库、工作流或 UI 原型，但其默认对象通常是 Document/Node/Chunk，难以天然表达：

```text
Patch applied_as Commit
Run uses Commit
Claim supported_by Run
Fact valid_from / valid_to
Evidence derivation / confidence / review_status
```

把这些关系塞进 Metadata 字段会导致查询、权限、删除和评测逐渐失控。更合理做法是将通用平台作为外围参考或可替换编排层，自有 Evidence Graph 为核心。

<a id="section-3-3"></a>
### 3.3 组件替换原则

| **层次**           | **可替换**                                 | **不可随意替换**                 |
|--------------------|--------------------------------------------|----------------------------------|
| Embedding/Reranker | 可以多版本灰度和回滚                       | Entity ID、Evidence Locator      |
| Vector/Search 后端 | pgvector 可演进为 Qdrant/OpenSearch        | ACL 和版本过滤语义               |
| 编排框架           | Haystack 可替换为自研/LlamaIndex           | Evidence Pack Schema             |
| 图后端             | PostgreSQL Edge 可演进为 Graphiti/图数据库 | 确定性边和来源证据               |
| 文档 Parser        | Docling/GROBID/MinerU 可组合               | DocumentVersion/Page/Object 定位 |
| LLM                | 可按数据等级切换云端或本地                 | 引用、冲突和生成约束             |

<a id="section-4"></a>
## 4. 代码仓库与代码智能

[返回目录](#toc)

<a id="section-4-1"></a>
### 4.1 Tree-sitter

**定位**：增量语法解析系统，用于按语言语法结构识别类、函数、方法、声明和语句块。

**可复用内容**：

- 多语言 Grammar 和统一 Parser API。

- 在代码不完整或包含语法错误时仍生成可用语法树。

- 增量解析能力，适合活跃仓库。

- Query 机制，用于提取 Symbol、Import、Comment 和配置结构。

**在本项目中的位置**：Git Blob → AST → Symbol Candidate / Structured Chunk。

**需要自研**：Git 版本关联、稳定 Symbol ID、跨文件引用、ACL、摘要、Embedding 和 Parser Version 管理。

**建议**：A，MVP 直接使用。许可证通常为 MIT，需按具体 Grammar 仓库分别核验。

**官方资料**：[tree-sitter/tree-sitter](https://github.com/tree-sitter/tree-sitter)

<a id="section-4-2"></a>
### 4.2 SCIP Code Intelligence Protocol

**定位**：语言无关的代码智能索引协议，表达 Symbol、定义、引用、实现和文档。

**可复用内容**：

- 统一 Symbol 标识和跨语言索引格式。

- 多语言 Indexer 生态。

- Definition/Reference/Implementation 等结构关系。

- 与编辑器/代码导航工具相近的数据语义。

**在本项目中的位置**：代码 Evidence Graph 的结构事实来源，尤其适合 Symbol references Symbol 和 Symbol implements Symbol。

**需要自研**：Commit/FileVersion 包装、Symbol Lineage、与 Tree-sitter 结果对齐、仓库权限和查询 API。

**建议**：A，直接采用协议；Indexer 按目标语言验证覆盖和准确度。许可证通常为 Apache-2.0。

**官方资料**：[scip-code/scip](https://github.com/scip-code/scip)

<a id="section-4-3"></a>
### 4.3 Zoekt

**定位**：面向源代码的快速三元组索引和文本搜索，支持子串、正则、布尔、多仓库和多分支搜索。

**可复用内容**：

- 标识符、错误码、配置键和字符串的低延迟精确召回。

- 路径、仓库、Branch 等过滤。

- 代码相关排序信号，例如 Symbol 命中。

- 独立索引服务，可与向量检索并行。

**在本项目中的位置**：代码侧 lexical retriever，不承担统一实体和版本裁决。

**集成方式**：自研 Zoekt Adapter 将结果转换为 Code Candidate，补充 Commit、Symbol、ACL 和 Evidence Locator；查询前限制允许访问的仓库/Branch。

**建议**：A，MVP 独立服务。许可证通常为 Apache-2.0。

**官方资料**：[sourcegraph/zoekt](https://github.com/sourcegraph/zoekt)

<a id="section-4-4"></a>
### 4.4 Aider

**定位**：面向 Git 仓库的 AI Pair Programming 工具，具有 Repo Map、上下文选择、代码编辑、Lint/Test 和 Git 工作流。

**最值得参考**：

- Repo Map 如何将大仓库压缩为高价值 Symbol 上下文。

- 如何结合引用关系、文件相关性和 Token 预算选取上下文。

- 代码修改—测试—修正—Commit 的交互循环。

**不建议直接承担**：多租户索引、长期证据存储、实验事实或 ACL。

**建议**：C，研究 Repo Map 和上下文预算算法，必要时在自有 Code Context Builder 中实现。许可证通常为 Apache-2.0。

**官方资料**：[Aider-AI/aider](https://github.com/Aider-AI/aider)

<a id="section-4-5"></a>
### 4.5 Joern

**定位**：Code Property Graph 与静态分析平台，统一 AST、控制流和数据流，适合安全分析和复杂影响查询。

**可复用场景**：

- 用户输入到敏感 Sink 的数据流。

- 变量经过的转换链路。

- 安全检查是否覆盖所有路径。

- 关键 Symbol 变化的深层行为影响。

**成本与限制**：索引重、语言支持和查询学习成本高，不适合在 MVP 对所有仓库全量启用。

**建议**：D，在失效检测或安全场景证明需要数据流后按仓库启用。许可证通常为 Apache-2.0。

**官方资料**：[joernio/joern](https://github.com/joernio/joern)

<a id="section-4-6"></a>
### 4.6 Sourcebot

**定位**：多仓库代码搜索、文件浏览、定义/引用导航和带代码引用的问答产品。

**最值得参考**：

- 代码搜索结果与文件浏览器的联动。

- 引用侧栏、路径/行号和跳转交互。

- 多仓库 Scope、Branch 和代码问答体验。

- 搜索与 LLM 问答之间的产品边界。

**风险**：其主体许可证并非简单宽松许可证，企业目录也可能有不同条款；商业集成前必须核验具体版本和使用方式。

**建议**：C/R，主要作为 Evidence UI 和代码搜索产品参考，不默认作为商业核心依赖。

**官方资料**：[sourcebot-dev/sourcebot](https://github.com/sourcebot-dev/sourcebot)

<a id="section-4-7"></a>
### 4.7 Universal Ctags

**定位**：多语言 Symbol 标签提取。

**价值**：对没有成熟 SCIP Indexer 的语言，可作为低成本 Symbol 候选来源；部署轻量、语言覆盖广。

**限制**：关系语义弱于 SCIP，难以准确表达跨文件引用和实现；许可证及各 Parser 文件需单独核验。

**建议**：B/D，作为语言覆盖回退，不作为首选关系源。

**官方资料**：[universal-ctags/ctags](https://github.com/universal-ctags/ctags)

<a id="section-4-8"></a>
### 4.8 代码侧选型小结

| **项目**        | **主要能力**         | **复用等级** | **MVP 决策** | **关键风险**      |
|-----------------|----------------------|--------------|--------------|-------------------|
| Tree-sitter     | AST、结构化切块      | A            | 采用         | Grammar 质量不一  |
| SCIP            | Symbol/引用/实现协议 | A            | 采用         | 语言 Indexer 覆盖 |
| Zoekt           | 代码精确搜索         | A            | 采用         | ACL/Commit 需适配 |
| Aider           | Repo Map、上下文预算 | C            | 借鉴         | 非索引服务        |
| Joern           | CPG、控制流/数据流   | D            | 后期         | 资源和复杂度高    |
| Sourcebot       | 产品与 UI            | C/R          | 参考         | 许可证需审查      |
| Universal Ctags | 多语言标签           | B/D          | 回退         | 关系粒度有限      |

<a id="section-5"></a>
## 5. Codex 与 Agent 会话

[返回目录](#toc)

<a id="section-5-1"></a>
### 5.1 Codex App Server / JSONL / SDK

**定位**：Codex 的官方深度集成、非交互事件和编程接口。

**可复用内容**：

- Thread、Turn、Item 等结构化对象。

- 消息、命令执行、文件变化、审批、错误和流式状态。

- 会话恢复、产品集成和 Schema 化事件。

- 非交互任务中的 JSONL 输出，便于 CI 和后台摄取。

**在本项目中的位置**：Codex Source Adapter 的正式入口。原始事件进入自有 Append-only Event Store，再构建 Development Episode 和证据关系。

**关键边界**：

- 不把页面抓取或未公开私有本地格式作为长期依赖。

- 中间增量事件不能替代完成事件的最终状态。

- 会话陈述不能直接升级为 Commit/Test/Run 事实。

- 对接方式和产品条款按官方文档与部署模式核验。

**建议**：A，作为 Codex 会话摄取首选。

**官方资料**：

- [Codex App Server](https://developers.openai.com/codex/app-server)

- [Non-interactive mode](https://developers.openai.com/codex/noninteractive)

- [Codex SDK](https://developers.openai.com/codex/sdk)

<a id="section-5-2"></a>
### 5.2 OpenHands

**定位**：开源代码 Agent 平台，包含 Agent Server、Workspace、运行控制、会话和工具执行。

**值得参考**：

- Agent Workspace 生命周期和隔离执行。

- 会话控制面、API 和前后端分层。

- 命令、文件变化、状态和自动化任务的组织方式。

- 多 Agent/远程执行的运维边界。

**不建议承担**：统一研发证据图或科研实验事实源。

**风险**：仓库可能包含不同许可证目录或企业功能，需按实际使用路径核验。

**建议**：C/R，作为 Agent 控制面和事件模型参考；若未来支持多 Agent，可做集成 PoC。

**官方资料**：[OpenHands/OpenHands](https://github.com/OpenHands/OpenHands)

<a id="section-5-3"></a>
### 5.3 SWE-agent

**定位**：面向软件工程任务的 Agent 研究与执行框架。

**值得参考**：

- Issue 到代码修改的任务轨迹。

- Agent-Computer Interface 设计。

- 轨迹评测、补丁和测试结果组织。

- 可重复的软件工程 Benchmark 工作流。

**建议**：C，参考会话轨迹、补丁评测和 Agent 行为分析，不作为长期知识平台。

**官方资料**：[SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent)

<a id="section-5-4"></a>
### 5.4 Continue

**定位**：开源 IDE AI 助手与模型/上下文配置框架。

**值得参考**：

- IDE 中的上下文提供器和代码引用交互。

- 模型、规则和工具配置。

- 本地/远程模型切换与开发者体验。

**建议**：C/B，作为 IDE Evidence 插件、上下文接口和交互参考；是否直接集成按目标 IDE 和许可证版本核验。

**官方资料**：[continuedev/continue](https://github.com/continuedev/continue)

<a id="section-5-5"></a>
### 5.5 OpenInference

**定位**：基于 OpenTelemetry 的 AI/LLM/Agent 语义约定和 Instrumentation。

**可复用内容**：

- LLM、RETRIEVER、EMBEDDING、TOOL、AGENT 等 Span 语义。

- 与不同 Trace 后端解耦。

- 统一记录 Codex 事件、RAG 检索和生成链路。

**在本项目中的位置**：Trace 语义标准，而不是知识存储。Span 只保存必要 ID、分数、延迟、成本和错误，敏感正文默认不记录。

**建议**：A，直接采用语义和适配器。许可证通常为 Apache-2.0。

**官方资料**：[Arize-ai/openinference](https://github.com/Arize-ai/openinference)

<a id="section-5-6"></a>
### 5.6 Agent 会话侧小结

| **项目**                   | **主要价值**                        | **复用等级** | **决策**      |
|----------------------------|-------------------------------------|--------------|---------------|
| Codex App Server/JSONL/SDK | 官方结构化会话和事件入口            | A            | 采用          |
| OpenHands                  | Workspace、Agent Server、会话控制面 | C/R          | 参考/后期适配 |
| SWE-agent                  | 软件工程轨迹与补丁评测              | C            | 参考          |
| Continue                   | IDE 上下文和交互                    | C/B          | IDE 阶段评估  |
| OpenInference              | Agent/RAG Trace 语义                | A            | 采用          |

<a id="section-6"></a>
## 6. 文档解析与科研问答

[返回目录](#toc)

<a id="section-6-1"></a>
### 6.1 Docling

**定位**：面向生成式 AI 的通用文档解析和结构化框架。

**可复用内容**：

- PDF、DOCX、PPTX、XLSX、HTML 和图片等格式。

- 页面布局、阅读顺序、表格、图片、代码和公式识别。

- 统一 DoclingDocument 表示和本地执行能力。

- 向 Markdown、JSON 等结构化格式转换。

**在本项目中的位置**：通用 Research Document Parser。输出映射到 DocumentVersion、Page、Section、Table、Figure、Formula 和 Chunk。

**需要自研**：Claim、Run/Metric 关联、ACL、版本、坐标引用、解析质量评分和人工校正。

**建议**：A，作为主解析器。许可证通常为 MIT。

**官方资料**：[docling-project/docling](https://github.com/docling-project/docling)

<a id="section-6-2"></a>
### 6.2 GROBID

**定位**：面向技术和科研论文的机器学习结构解析服务。

**可复用内容**：

- 标题、作者、机构、摘要和章节结构。

- 参考文献、Citation Context 和书目信息。

- 图、表、公式及 PDF 坐标信息。

- TEI XML 等学术结构输出。

**在本项目中的位置**：与 Docling 并行，增强论文语义、引文和学术元数据。二者按页码、文本和坐标对齐。

**建议**：A，论文类文档采用。许可证通常为 Apache-2.0。

**官方资料**：[grobidOrg/grobid](https://github.com/grobidOrg/grobid)

<a id="section-6-3"></a>
### 6.3 MinerU

**定位**：复杂 PDF、扫描件、公式、表格和中文文档解析工具。

**价值**：对 Docling/GROBID 解析效果不佳的中文或复杂版面文档，可作为候选 Adapter 做基准对比。

**风险**：其许可证可能包含基于宽松许可证增加的额外条件；模型权重、服务方式和版本也需单独核验。

**建议**：B/R，不作为默认唯一解析器；以真实文档集比较质量、速度、资源和许可后决定。

**官方资料**：[opendatalab/MinerU](https://github.com/opendatalab/MinerU)

<a id="section-6-4"></a>
### 6.4 PaperQA2

**定位**：面向科研论文的检索、证据组织和引用式问答系统。

**值得参考**：

- 论文元数据增强和问题相关上下文摘要。

- 迭代检索、LLM 重排和文内引用。

- 对大量论文进行问题驱动证据组织。

- 科研问答的回答结构和文献引用验证。

**不建议承担**：Experiment Run 的权威事实存储、代码版本关系和组织级 ACL。

**建议**：C/B，借鉴科研检索和引用算法，可在论文语料子系统做 PoC。许可证通常为 Apache-2.0。

**官方资料**：[future-house/paper-qa](https://github.com/future-house/paper-qa)

<a id="section-6-5"></a>
### 6.5 Unstructured

**定位**：通用非结构化文档摄取和分区组件。

**价值**：连接器和文档 Partition 生态丰富，适合作为某些文件格式或外部内容源的补充。

**限制**：科研论文、公式和表格语义需与 Docling/GROBID 对比；不同组件和托管能力的许可证/条款需核验。

**建议**：D/B，当 Docling 无法覆盖特定来源或已有 Unstructured 基础设施时评估。

**官方资料**：[Unstructured-IO/unstructured](https://github.com/Unstructured-IO/unstructured)

<a id="section-6-6"></a>
### 6.6 Papermill

**定位**：参数化和执行 Jupyter Notebook。

**可复用内容**：

- 向 Notebook 注入参数并生成一次具体执行结果。

- 将 Notebook Template 与 Notebook Run 分离。

- 适合形成可复现的实验执行入口。

**在本项目中的位置**：NotebookRun 事实采集；记录输入参数、执行状态、输出 Notebook 和 Artifact。

**建议**：A/B，使用 Notebook 的团队直接集成。许可证通常为 BSD-3-Clause。

**官方资料**：[nteract/papermill](https://github.com/nteract/papermill)

<a id="section-6-7"></a>
### 6.7 nbdime

**定位**：Jupyter Notebook 的结构化 Diff 和 Merge。

**可复用内容**：

- 区分代码 Cell、Markdown Cell、输出和元数据变化。

- 避免把 .ipynb 当成大块 JSON 比较。

- 支持实验 Notebook 的版本变化解释。

**建议**：A/B，作为 Notebook Diff 和 UI 渲染能力。许可证通常为 BSD-3-Clause。

**官方资料**：[jupyter/nbdime](https://github.com/jupyter/nbdime)

<a id="section-6-8"></a>
### 6.8 RO-Crate

**定位**：基于 JSON-LD 的 Research Object 打包规范，将数据、代码、文档、人员、工具和上下文组织成可交换科研成果。

**价值**：适合导入/导出“实验复现包”或长期归档，而不必让内部数据库结构成为交换协议。

**建议**：B/D，在跨团队交付、论文复现和归档需求明确后采用。Python 实现的许可证和规范版本需按仓库核验。

**官方资料**：[ResearchObject/ro-crate-py](https://github.com/ResearchObject/ro-crate-py)

<a id="section-6-9"></a>
### 6.9 文档与科研侧小结

| **项目**     | **主要能力**            | **复用等级** | **MVP 决策**      | **风险/备注**   |
|--------------|-------------------------|--------------|-------------------|-----------------|
| Docling      | 通用 Layout、表格、公式 | A            | 采用              | 需真实语料基准  |
| GROBID       | 论文结构、引文、坐标    | A            | 采用              | 与 Docling 对齐 |
| MinerU       | 中文/复杂 PDF           | B/R          | 候选              | 许可证与资源    |
| PaperQA2     | 科研检索和引用          | C/B          | 算法参考          | 非实验事实源    |
| Unstructured | 连接器和通用 Partition  | D/B          | 按需              | 与主解析器重叠  |
| Papermill    | 参数化 Notebook Run     | A/B          | Notebook 场景采用 | 执行环境需隔离  |
| nbdime       | Notebook Diff           | A/B          | 采用              | 大输出需裁剪    |
| RO-Crate     | 科研成果交换            | B/D          | 后期              | 需定义内部映射  |

<a id="section-7"></a>
## 7. 实验管理、数据版本和谱系

[返回目录](#toc)

<a id="section-7-1"></a>
### 7.1 MLflow

**定位**：实验、Run、参数、指标、模型、Artifact 和评测管理平台。

**可复用内容**：

- Experiment/Run 的稳定对象和 API。

- Parameters、Metrics、Tags、Artifacts 和模型关联。

- 运行历史、比较和可视化。

- 适合作为 ExperimentRun 的主事实源。

**在本项目中的位置**：Run Adapter 读取并标准化 run_id、Commit、Dataset、Config、Metric 和 Artifact。RAG 只复制必要元数据和授权引用，不替代 MLflow Tracking Store。

**强制扩展字段建议**：

```text
repository_url / branch / commit_sha / dirty_state
config_hash / dataset_version / environment_hash / seed
```

**建议**：A，实验事实首选。许可证通常为 Apache-2.0。

**官方资料**：[mlflow/mlflow](https://github.com/mlflow/mlflow)

<a id="section-7-2"></a>
### 7.2 DVC

**定位**：与 Git 集成的数据、模型、参数和实验版本管理。

**可复用内容**：

- Git 中保存数据和模型指针。

- 数据远端、Pipeline、参数和实验比较。

- 代码 Commit 与数据/模型版本的自然关联。

**适用条件**：中小到中型数据、项目级使用、团队熟悉 Git 工作流。

**建议**：A/B，MVP 数据版本首选之一。许可证通常为 Apache-2.0。

**官方资料**：[iterative/dvc](https://github.com/iterative/dvc)

<a id="section-7-3"></a>
### 7.3 OpenLineage

**定位**：开放的运行谱系元数据标准，核心对象为 Job、Run、Dataset 和可扩展 Facet。

**可复用内容**：

- 跨编排器、计算平台和实验系统的统一运行事件。

- 输入/输出 Dataset 关系。

- Source Code Location、Schema、Data Quality 等 Facet 扩展。

- 事件化、可流式接入的谱系模型。

**在本项目中的位置**：采用其事件和 Facet 思路，统一表示实验 Pipeline、Run 和 Dataset；不必在 MVP 部署完整后端。

**建议**：A/B，采用标准模型并编写 Adapter。许可证通常为 Apache-2.0。

**官方资料**：[OpenLineage/OpenLineage](https://github.com/OpenLineage/OpenLineage)

<a id="section-7-4"></a>
### 7.4 lakeFS

**定位**：为对象存储提供类似 Git 的 Branch、Commit、Merge、回滚和时间旅行。

**适用场景**：数据主要位于 S3 兼容对象存储、规模大、需要团队级数据湖版本和隔离实验。

**与 DVC 的选择**：

```text
项目级/本地/中小数据 → DVC
对象存储/数据湖/团队级分支 → lakeFS
```

**建议**：D/B，MVP 不与 DVC 同时全面引入；当对象存储数据版本成为瓶颈时评估。许可证通常为 Apache-2.0。

**官方资料**：[treeverse/lakeFS](https://github.com/treeverse/lakeFS)

<a id="section-7-5"></a>
### 7.5 in-toto

**定位**：软件供应链的步骤、材料、产物和签名验证框架。

**可借鉴内容**：

| Dataset → Preprocess → Train → Evaluate → Report |
|--------------------------------------------------|

每一步记录执行命令、输入材料、输出产物和签名，适合高可信、受监管或论文复现实验。

**建议**：C/D，借鉴可验证产物链；高价值场景再做正式集成。许可证通常为 Apache-2.0。

**官方资料**：[in-toto/in-toto](https://github.com/in-toto/in-toto)

<a id="section-7-6"></a>
### 7.6 Pachyderm / Kubeflow Pipelines 等

此类平台可以承担数据 Pipeline、容器化执行和血缘，但部署和模型较重。若组织已有平台，应写 Adapter，而不是由 RAG 项目重新选择并替换实验执行基础设施。

**建议**：B/D，仅做现有平台连接器；RAG 内部仍映射到统一 Job/Run/Dataset/Artifact 模型。

<a id="section-7-7"></a>
### 7.7 实验与谱系侧小结

| **项目**           | **职责**                       | **复用等级** | **决策**     |
|--------------------|--------------------------------|--------------|--------------|
| MLflow             | Experiment/Run/Metric/Artifact | A            | 采用         |
| DVC                | 项目级数据/模型版本            | A/B          | MVP 候选     |
| OpenLineage        | Run/Job/Dataset 谱系标准       | A/B          | 采用模型     |
| lakeFS             | 对象存储数据版本               | D/B          | 后期按需     |
| in-toto            | 可验证材料-步骤-产物链         | C/D          | 高可信场景   |
| 现有 Pipeline 平台 | 实际实验执行                   | B            | Adapter 接入 |

<a id="section-8"></a>
## 8. 元数据、知识图与时间图

[返回目录](#toc)

<a id="section-8-1"></a>
### 8.1 OpenMetadata

**定位**：面向数据与 AI 的开放元数据和上下文平台，覆盖资产、Owner、术语、谱系、质量、治理、对话和搜索。

**最值得参考**：

- Entity、Relationship、Lineage、Ownership、Domain、Tag 和 Policy 的统一模型。

- 大量连接器、事件、API、SDK、搜索和治理能力。

- 将技术资产、业务语义和协作上下文放到同一控制面。

- 组织级知识、MCP/Agent 上下文和可信数据资产的产品方向。

**两种用法**：

> **1. MVP 仅参考模型**：借鉴 Entity、Lineage、Owner、Domain、Policy，自己建立 Code/Codex/Experiment/Claim 实体。
>
> **2. 组织级控制面**：将仓库、实验、文档和数据注册为资产，RAG 作为独立 Evidence 应用。

**限制**：部署和二次开发较重；其原生实体并不完全覆盖 Code Symbol、Codex Item 和 Claim Validity。

**建议**：C/D，MVP 深入参考，组织级阶段做集成 PoC。许可证通常为 Apache-2.0。

**官方资料**：[open-metadata/OpenMetadata](https://github.com/open-metadata/OpenMetadata)

<a id="section-8-2"></a>
### 8.2 DataHub

**定位**：企业级元数据图、搜索、谱系、Owner、治理和连接器平台。

**价值**：元数据摄取、GraphQL/API、实时事件和大规模组织资产治理经验成熟。

**限制**：完整栈组件较多；同样需要扩展代码、会话和 Claim 领域实体。

**建议**：D/C，已有 DataHub 的组织优先适配；从零 MVP 不建议同时评估多个大型 Catalog。许可证通常为 Apache-2.0。

**官方资料**：[datahub-project/datahub](https://github.com/datahub-project/datahub)

<a id="section-8-3"></a>
### 8.3 Graphiti

**定位**：面向动态和时间上下文的知识图框架，关注事实有效时间、Episode 来源和增量更新。

**可复用价值**：

- 事实何时成立、何时被替代。

- Episode 到事实的来源追溯。

- 语义、全文和图查询组合。

- 会话长期记忆和历史状态查询。

**在本项目中的位置**：后期的 Temporal Inference Graph，用于设计决策变化、长期研发记忆和推断关系。

**关键边界**：Commit SHA、Run ID、Metric Value、Dataset Version 等确定性事实仍保存在事务证据层。

**建议**：D/B，阶段 3–4 做 PoC。许可证通常为 Apache-2.0。

**官方资料**：[getzep/graphiti](https://github.com/getzep/graphiti)

<a id="section-8-4"></a>
### 8.4 Microsoft GraphRAG

**定位**：从非结构化文本抽取实体关系、构建社区层级和摘要，用于全局主题问题。

**适合问题**：

- 整个项目有哪些技术路线？

- 过去半年最常见的失败模式是什么？

- 多份报告围绕哪些概念形成分歧？

**不适合**：代替 Git、Run、Metric 等确定性关系；索引和 Prompt 成本也需评估。

**建议**：C/D，作为 Document Concept Graph 和 global_synthesis 的候选，不进入 MVP 核心。许可证通常为 MIT。

**官方资料**：[microsoft/graphrag](https://github.com/microsoft/graphrag)

<a id="section-8-5"></a>
### 8.5 LightRAG

**定位**：结合实体关系、文本 Chunk 和向量检索的轻量图 RAG 实现。

**价值**：适合快速比较 Local/Global/Hybrid/Mix 等图文本检索策略，验证图增强是否改善真实问题。

**限制**：自动抽取关系仍然是推断知识，不能当作确定性 Evidence Graph。

**建议**：C/D，作为算法实验分支。许可证通常为 MIT。

**官方资料**：[HKUDS/LightRAG](https://github.com/HKUDS/LightRAG)

<a id="section-8-6"></a>
### 8.6 Apache Atlas

**定位**：数据治理、分类和谱系平台。

**价值**：可参考 Type System、Classification 和数据资产 Lineage；若组织已经使用 Hadoop 生态，可作为外部 Metadata Source。

**建议**：D/B，不作为从零研发证据系统的首选控制面。许可证通常为 Apache-2.0。

**官方资料**：[apache/atlas](https://github.com/apache/atlas)

<a id="section-8-7"></a>
### 8.7 图与元数据侧小结

| **项目**     | **强项**                      | **复用等级** | **适用阶段**         |
|--------------|-------------------------------|--------------|----------------------|
| OpenMetadata | 统一元数据、谱系、Owner、治理 | C/D          | 模型参考；组织级集成 |
| DataHub      | 企业元数据图和连接器          | C/D          | 已有平台时适配       |
| Graphiti     | 时间事实、Episode 和增量图    | B/D          | 失效/长期记忆阶段    |
| GraphRAG     | 文档社区和全局主题            | C/D          | 全局综合问题         |
| LightRAG     | 轻量图文本检索实验            | C/D          | 算法对比             |
| Apache Atlas | 数据治理与分类                | B/D          | Hadoop/既有生态      |

<a id="section-9"></a>
## 9. RAG 编排、搜索与存储

[返回目录](#toc)

<a id="section-9-1"></a>
### 9.1 Haystack

**定位**：组件化的 Python LLM/RAG Pipeline 框架。

**可复用内容**：

- Retriever、Reranker、Router、Prompt、Generator 和自定义 Component。

- 显式 DAG/Pipeline，便于观察每个检索和裁决步骤。

- 服务化和评测扩展能力。

**在本项目中的位置**：Query Pipeline 的可替换编排容器。领域实体、权限、关系规则和 Evidence Pack 仍由自有组件实现。

**建议**：A/B，MVP 首选。许可证通常为 Apache-2.0。

**官方资料**：[deepset-ai/haystack](https://github.com/deepset-ai/haystack)

<a id="section-9-2"></a>
### 9.2 LlamaIndex

**定位**：数据连接器、索引、Retriever、Query Engine 和 Agent 生态丰富的 RAG 框架。

**价值**：快速接入来源、验证索引和 Retriever 组合；部分 Graph/Agent 功能可用于原型。

**风险**：不要让其 Document/Node 抽象成为唯一领域模型；版本演进和扩展 API 需锁定。

**建议**：B/C，作为替代编排或连接器来源；不与 Haystack 同时深度绑定。核心通常为 MIT。

**官方资料**：[run-llama/llama_index](https://github.com/run-llama/llama_index)

<a id="section-9-3"></a>
### 9.3 LangChain / LangGraph

**定位**：LLM 应用组件和有状态 Agent 图编排。

**价值**：工具调用、状态图、检查点和 Agent 工作流生态广。

**适用边界**：后期若 Evidence Investigation Agent 需要循环调查、审批和状态恢复，可评估 LangGraph；MVP 的确定性 Query Pipeline 用 Haystack/自研流程更直观。

**建议**：C/D，Agent 阶段候选。许可证按具体包和版本核验。

**官方资料**：[langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)

<a id="section-9-4"></a>
### 9.4 RAGFlow

**定位**：面向文档知识库的解析、摄取、检索、引用和应用 UI。

**最值得参考**：

- 文档解析任务和知识库管理。

- 分块结果可视化与人工干预。

- 引用式回答和文档证据交互。

- 混合检索和应用配置体验。

**不建议承担**：Code Symbol 版本、Codex Event、Run 事实和 Claim 有效性核心模型。

**建议**：C/B，文档侧产品和 Parser 编排参考；直接复用前评估与自有 Ontology 的耦合。许可证通常标为 Apache-2.0，需锁定版本核验。

**官方资料**：[infiniflow/ragflow](https://github.com/infiniflow/ragflow)

<a id="section-9-5"></a>
### 9.5 Dify

**定位**：LLM 应用、工作流、Agent、知识库和模型管理平台。

**价值**：低代码 Workflow、应用发布、模型配置和运营 UI 可用于快速原型。

**风险**：许可证含附加条件，不等同于无附加条件的 Apache-2.0；其知识库模型也不是本项目的证据内核。

**建议**：C/R，仅作为产品交互和原型参考，商业使用前法务审查。

**官方资料**：[langgenius/dify](https://github.com/langgenius/dify)

<a id="section-9-6"></a>
### 9.6 PostgreSQL + pgvector

**定位**：事务数据库与向量扩展。

**可复用内容**：

- Entity、Edge、ACL、版本和双时间在一个事务边界内。

- JSONB、全文、递归 CTE 和关系查询。

- pgvector 的精确、HNSW、IVFFlat 向量检索。

- 备份、复制、监控和成熟运维生态。

**建议**：A，MVP 核心存储。PostgreSQL 与 pgvector 均采用宽松许可证体系，具体版本核验。

**官方资料**：

- [PostgreSQL](https://www.postgresql.org/)

- [pgvector/pgvector](https://github.com/pgvector/pgvector)

<a id="section-9-7"></a>
### 9.7 Qdrant

**定位**：独立向量数据库，支持 Dense、Sparse、Multivector、Payload Filter 和混合查询。

**引入条件**：向量数量、过滤复杂度、吞吐或水平扩展超过 pgvector 能力，且已解决 ACL/删除同步方案。

**建议**：D/B，后期候选。许可证通常为 Apache-2.0。

**官方资料**：[qdrant/qdrant](https://github.com/qdrant/qdrant)

<a id="section-9-8"></a>
### 9.8 OpenSearch

**定位**：全文搜索和分析平台，支持 BM25、过滤、聚合和神经/混合检索。

**引入条件**：文档与会话全文规模大、复杂聚合和独立搜索集群成为明确需求。

**建议**：D/B，后期替代 PostgreSQL FTS 的候选；必须设计 Index Generation、ACL 和删除一致性。许可证通常为 Apache-2.0。

**官方资料**：[opensearch-project/OpenSearch](https://github.com/opensearch-project/OpenSearch)

<a id="section-9-9"></a>
### 9.9 Neo4j 等图数据库

图数据库适合复杂、多跳和路径查询，但本项目初期最难的问题不是图查询性能，而是关系是否正确、版本是否一致、权限是否安全。MVP 使用 PostgreSQL Edge Table 足够支撑 1–3 跳模板化扩展。

**建议**：D，只有图查询瓶颈被量化后再选；同时核验 Community/Enterprise 许可差异。

<a id="section-9-10"></a>
### 9.10 编排与存储侧小结

| **项目**              | **主要职责**           | **复用等级** | **决策**      |
|-----------------------|------------------------|--------------|---------------|
| Haystack              | 显式 RAG Pipeline      | A/B          | MVP 采用      |
| LlamaIndex            | 连接器、Retriever 生态 | B/C          | 替代/补充     |
| LangGraph             | 有状态 Agent 图        | C/D          | Agent 阶段    |
| RAGFlow               | 文档摄取与引用 UI      | C/B          | 参考/局部复用 |
| Dify                  | 工作流和应用 UI        | C/R          | 原型参考      |
| PostgreSQL + pgvector | 事务、关系、向量       | A            | MVP 采用      |
| Qdrant                | 独立大规模向量         | B/D          | 后期          |
| OpenSearch            | 大规模全文/聚合        | B/D          | 后期          |
| 图数据库              | 多跳路径查询           | D            | 需求驱动      |

<a id="section-10"></a>
## 10. 工作流、可观测性与评测

[返回目录](#toc)

<a id="section-10-1"></a>
### 10.1 Temporal

**定位**：持久化执行和长任务工作流平台。

**可复用内容**：

- 代码克隆、PDF 解析、Embedding 和索引构建的自动重试与恢复。

- Workflow/Activity 的幂等和状态持久化。

- 长任务超时、取消、补偿和可观测。

- Parser/模型升级后的批量重建编排。

**在本项目中的位置**：摄取与重建控制面，不承担业务 Entity 存储。

**建议**：A，MVP 采用；简单 PoC 可先减少 Workflow 数量，但生产前引入。许可证通常为 MIT。

**官方资料**：[temporalio/temporal](https://github.com/temporalio/temporal)

<a id="section-10-2"></a>
### 10.2 Langfuse

**定位**：LLM 应用 Trace、Prompt、数据集、评测和调试平台。

**可复用内容**：

- Query/Retrieval/Generation Trace 可视化。

- Prompt Version、Dataset 和 Eval 管理。

- 自托管与团队协作界面。

**风险**：核心与企业目录的许可证范围可能不同；部署遥测、数据保留和敏感正文策略需核验。

**建议**：B/R，与 OpenInference 组合，后端可替换。锁定版本后做许可证与数据安全审查。

**官方资料**：[langfuse/langfuse](https://github.com/langfuse/langfuse)

<a id="section-10-3"></a>
### 10.3 Arize Phoenix

**定位**：LLM/AI 可观测、Trace、评测和实验分析。

**价值**：与 OpenInference 生态协同，适合检索和生成调试、数据集比较和评测。

**风险**：许可证和企业/云功能边界需按具体版本核验，不应只依据旧资料判断。

**建议**：B/R，与 Langfuse 二选一做 PoC；OpenInference 保证埋点层可迁移。

**官方资料**：[Arize-ai/phoenix](https://github.com/Arize-ai/phoenix)

<a id="section-10-4"></a>
### 10.4 Ragas

**定位**：RAG/LLM 应用评测框架和测试数据方法。

**可复用内容**：

- Faithfulness、Answer Relevance、Context 相关评测。

- 测试集和评测 Pipeline。

- 与模型/应用实验结合。

**限制**：不理解 Commit、Branch、Run、Dataset Version 和 Evidence Path，必须补充自定义指标。

**建议**：A/B，作为通用评测工具。许可证通常为 Apache-2.0。

**官方资料**：[vibrantlabsai/ragas](https://github.com/vibrantlabsai/ragas)

<a id="section-10-5"></a>
### 10.5 promptfoo

**定位**：Prompt、模型、RAG 和 Agent 的自动化对比、回归、红队与 CI 工具。

**可复用内容**：

- 在 CI 中运行固定用例和断言。

- 多模型、Prompt 和配置横向比较。

- 安全测试和红队场景。

- 自定义 JavaScript/Python 断言，可实现 Commit/Run/Citation 检查。

**建议**：A/B，作为发布门禁和红队工具。许可证需按当前版本核验，通常采用宽松许可证。

**官方资料**：[promptfoo/promptfoo](https://github.com/promptfoo/promptfoo)

<a id="section-10-6"></a>
### 10.6 OpenTelemetry Collector

**定位**：标准 Trace、Metric、Log 收集和转发控制面。

**价值**：将 OpenInference Span 输出到 Langfuse、Phoenix 或企业可观测平台；支持采样、过滤和敏感字段清洗。

**建议**：A/B，生产环境采用。许可证通常为 Apache-2.0。

**官方资料**：[open-telemetry/opentelemetry-collector](https://github.com/open-telemetry/opentelemetry-collector)

<a id="section-10-7"></a>
### 10.7 评测工具与自定义指标分工

| **能力**                 | **开源工具**                     | **必须自研**                           |
|--------------------------|----------------------------------|----------------------------------------|
| 通用 Faithfulness/相关性 | Ragas                            | 领域阈值与人工样本                     |
| CI 多配置对比            | promptfoo                        | Commit/Run/ACL/Citation 断言           |
| Trace/调试               | OpenInference + Langfuse/Phoenix | Evidence Pack、Scope、版本字段         |
| 线上指标                 | OTel + 监控后端                  | Wrong-Version、Stale Evidence、Leakage |
| Golden Set               | 可使用评测框架承载               | 问题、证据路径和预期状态定义           |

<a id="section-11"></a>
## 11. 身份、授权与安全

[返回目录](#toc)

<a id="section-11-1"></a>
### 11.1 Keycloak

**定位**：开源身份与访问管理，支持 OIDC、OAuth、SAML、用户、组和联邦身份。

**在本项目中的位置**：认证“用户是谁”、颁发 Token 和组织身份；不承担每个 Code Symbol 或 Run 的关系授权。

**建议**：A/B，组织已有 IdP 时可改为适配现有系统。许可证通常为 Apache-2.0。

**官方资料**：[keycloak/keycloak](https://github.com/keycloak/keycloak)

<a id="section-11-2"></a>
### 11.2 OpenFGA

**定位**：基于关系的细粒度授权系统。

**可复用内容**：

- user → team → project → repository/experiment/document 权限继承。

- 高层 Source Object 授权，Chunk 和派生实体继承。

- Check/ListObjects 等查询，适合检索前生成允许对象范围。

**在本项目中的位置**：Authorization Service；缓存必须绑定授权模型版本。

**建议**：A，MVP 采用或至少采用其模型思想。许可证通常为 Apache-2.0。

**官方资料**：[openfga/openfga](https://github.com/openfga/openfga)

<a id="section-11-3"></a>
### 11.3 Open Policy Agent

**定位**：通用策略决策引擎，使用 Rego 表达环境、数据等级、导出和操作策略。

**适合补充**：

- 高敏数据是否允许调用外部模型。

- 某角色是否允许导出 Evidence Pack。

- 是否允许某个 Agent 执行写操作。

**与 OpenFGA 分工**：OpenFGA 负责“用户与资源关系”，OPA 负责“上下文与条件策略”。

**建议**：D/B，规则复杂后引入。许可证通常为 Apache-2.0。

**官方资料**：[open-policy-agent/opa](https://github.com/open-policy-agent/opa)

<a id="section-11-4"></a>
### 11.4 Gitleaks

**定位**：在 Git、文件、目录和 stdin 中检测 Secret。

**在本项目中的位置**：代码、Codex 事件、日志和 Notebook 入库前的轻量 Secret 扫描；命中时进入隔离工作流。

**建议**：A，MVP 采用。许可证通常为 MIT。

**官方资料**：[gitleaks/gitleaks](https://github.com/gitleaks/gitleaks)

<a id="section-11-5"></a>
### 11.5 TruffleHog

**定位**：多来源 Secret 检测，并可验证部分凭证是否仍有效。

**价值**：对高风险来源进行深度扫描和凭证验证。

**风险**：当前许可证通常为 AGPL-3.0；验证真实凭证还涉及网络、安全和审计边界。

**建议**：R/D，作为隔离安全服务候选，法务和安全评估后引入。

**官方资料**：[trufflesecurity/trufflehog](https://github.com/trufflesecurity/trufflehog)

<a id="section-11-6"></a>
### 11.6 安全侧小结

| **项目**   | **职责**                | **复用等级** | **决策**         |
|------------|-------------------------|--------------|------------------|
| Keycloak   | 用户身份、Token、联邦   | A/B          | 采用或接现有 IdP |
| OpenFGA    | 关系式资源授权          | A            | 采用             |
| OPA        | 条件和环境策略          | B/D          | 后期             |
| Gitleaks   | 入库前 Secret 扫描      | A            | 采用             |
| TruffleHog | 深度/验证型 Secret 扫描 | R/D          | 隔离评估         |

<a id="section-12"></a>
## 12. 产品与 UI 参考

[返回目录](#toc)

<a id="section-12-1"></a>
### 12.1 RAGFlow：文档证据体验

重点参考：文档上传和解析进度、Chunk/版面结果可视化、知识库配置、引用式回答、检索调试和用户纠错。不要照搬其 Document/Chunk 为中心的数据模型。

<a id="section-12-2"></a>
### 12.2 Sourcebot：代码证据体验

重点参考：多仓库搜索、文件浏览、路径与行号跳转、定义/引用导航、代码问答引用侧栏和搜索 Scope。作为商业依赖前需处理许可证问题。

<a id="section-12-3"></a>
### 12.3 Renku：科研项目工作区

**定位**：将 Git、数据连接、Jupyter/VS Code/RStudio、计算环境和协作项目整合为可复现科研空间。

重点参考：

- 项目主页如何组织代码、数据、环境和成员。

- 研究会话和计算资源生命周期。

- 可复现项目和团队协作产品形态。

**建议**：C，科研工作台和项目导航参考。许可证通常为 Apache-2.0。

**官方资料**：[SwissDataScienceCenter/renku](https://github.com/SwissDataScienceCenter/renku)

<a id="section-12-4"></a>
### 12.4 OpenHands：Agent 运行体验

重点参考：Workspace、任务状态、终端/文件/对话联动、审批和会话恢复。RAG Evidence UI 应与 Agent 执行 UI 分离，避免用户误把“回答”当成“已执行”。

<a id="section-12-5"></a>
### 12.5 OpenMetadata：组织级上下文体验

重点参考：资产详情页、Owner、Domain、Tag、Lineage、质量、对话和搜索。后期可以将 Evidence Timeline 作为资产页的扩展视图。

<a id="section-12-6"></a>
### 12.6 推荐 UI 信息架构

```text
Project Overview
├── Repositories
├── Codex Episodes
├── Experiments & Runs
├── Documents & Claims
├── Evidence Timeline
├── Open Conflicts / Stale Claims
└── Search / Ask
Answer Workspace
├── Answer + Scope + Status
├── Evidence Sidebar
├── Code / Diff
├── Run / Metric / Config
├── Document Page / Table / Figure
└── Session Timeline
```

> **产品原则**
>
> 证据 UI 的目标不是展示“模型知道很多”，而是让用户在最少跳转中核验版本、原始事实和不确定性。

<a id="section-13"></a>
## 13. 项目对比矩阵

[返回目录](#toc)

<a id="section-13-1"></a>
### 13.1 代码、会话与文档

| **项目**         | **环节**     | **核心价值**                 | **复用等级** | **MVP** | **许可证提示**        |
|------------------|--------------|------------------------------|--------------|---------|-----------------------|
| Tree-sitter      | 代码解析     | 增量 AST、语法结构切块       | A            | 是      | MIT；Grammar 单独核验 |
| SCIP             | 代码关系     | Symbol、定义、引用、实现协议 | A            | 是      | Apache-2.0            |
| Zoekt            | 代码检索     | 多仓库子串/正则/布尔搜索     | A            | 是      | Apache-2.0            |
| Aider            | 代码上下文   | Repo Map、上下文预算         | C            | 参考    | Apache-2.0            |
| Joern            | 静态分析     | CPG、控制流、数据流          | D            | 否      | Apache-2.0            |
| Sourcebot        | 产品/UI      | 代码搜索与引用问答           | C/R          | 参考    | FSL/企业目录需审查    |
| Codex App Server | 会话接入     | Thread/Turn/Item 与事件      | A            | 是      | 官方产品接口/条款核验 |
| OpenHands        | Agent 控制面 | Workspace、运行和会话        | C/R          | 否      | 目录许可需核验        |
| SWE-agent        | Agent 轨迹   | Issue、Patch、测试评测       | C            | 参考    | 按版本核验            |
| Continue         | IDE          | 上下文提供器和交互           | C/B          | 后期    | 按版本核验            |
| Docling          | 文档解析     | Layout、表格、公式、图片     | A            | 是      | MIT                   |
| GROBID           | 学术解析     | 引文、参考文献、坐标         | A            | 是      | Apache-2.0            |
| MinerU           | 复杂 PDF     | 中文、扫描、公式、表格       | B/R          | 候选    | 自定义/附加条件需审查 |
| PaperQA2         | 科研问答     | 迭代检索、重排、引用         | C/B          | 参考    | Apache-2.0            |
| Papermill        | Notebook     | 参数化执行                   | A/B          | 按需    | BSD-3-Clause          |
| nbdime           | Notebook     | 结构化 Diff/Merge            | A/B          | 按需    | BSD-3-Clause          |

<a id="section-13-2"></a>
### 13.2 实验、元数据、图与搜索

| **项目**     | **环节**     | **核心价值**                 | **复用等级** | **MVP**    | **许可证提示**     |
|--------------|--------------|------------------------------|--------------|------------|--------------------|
| MLflow       | 实验事实     | Run、参数、指标、Artifact    | A            | 是         | Apache-2.0         |
| DVC          | 数据版本     | Git 关联的数据/模型/实验版本 | A/B          | 是         | Apache-2.0         |
| OpenLineage  | 谱系标准     | Job、Run、Dataset、Facet     | A/B          | 是（模型） | Apache-2.0         |
| lakeFS       | 数据湖版本   | 对象存储 Branch/Commit       | D/B          | 否         | Apache-2.0         |
| RO-Crate     | 科研交换     | Research Object 打包         | B/D          | 否         | 按实现核验         |
| in-toto      | 可验证链     | 材料、步骤、产物和签名       | C/D          | 否         | Apache-2.0         |
| OpenMetadata | 元数据控制面 | Entity、Lineage、Owner、治理 | C/D          | 参考       | Apache-2.0         |
| DataHub      | 元数据平台   | 企业元数据图和连接器         | C/D          | 否         | Apache-2.0         |
| Graphiti     | 时间知识图   | 有效时间、Episode、增量图    | B/D          | 否         | Apache-2.0         |
| GraphRAG     | 文档概念图   | 社区层级和全局综合           | C/D          | 否         | MIT                |
| LightRAG     | 图检索实验   | Local/Global/Hybrid          | C/D          | 否         | MIT                |
| Haystack     | RAG 编排     | 显式组件 Pipeline            | A/B          | 是         | Apache-2.0         |
| PostgreSQL   | 事务存储     | Entity/Edge/ACL/版本         | A            | 是         | PostgreSQL License |
| pgvector     | 向量         | 同库向量和 ANN               | A            | 是         | PostgreSQL License |
| Qdrant       | 向量服务     | 过滤、Sparse、Multivector    | B/D          | 否         | Apache-2.0         |
| OpenSearch   | 全文/分析    | BM25、聚合、混合检索         | B/D          | 否         | Apache-2.0         |

<a id="section-13-3"></a>
### 13.3 工作流、可观测、评测与安全

| **项目**      | **环节**   | **核心价值**                 | **复用等级** | **MVP**       | **许可证提示**   |
|---------------|------------|------------------------------|--------------|---------------|------------------|
| Temporal      | 工作流     | 重试、恢复、长任务           | A            | 是            | MIT              |
| OpenInference | Trace 语义 | LLM/Retriever/Agent Span     | A            | 是            | Apache-2.0       |
| Langfuse      | 可观测后端 | Trace、Prompt、Dataset、Eval | B/R          | 二选一        | 核心/EE 目录核验 |
| Phoenix       | 可观测后端 | OpenInference、Trace、Eval   | B/R          | 二选一        | 当前版本许可核验 |
| Ragas         | RAG 评测   | 通用检索/生成指标            | A/B          | 是            | Apache-2.0       |
| promptfoo     | CI/红队    | 多配置回归和安全测试         | A/B          | 是            | 当前版本核验     |
| Keycloak      | 身份       | OIDC/OAuth/SAML              | A/B          | 是/接现有 IdP | Apache-2.0       |
| OpenFGA       | 授权       | 关系式细粒度 ACL             | A            | 是            | Apache-2.0       |
| OPA           | 策略       | 条件和环境策略               | B/D          | 否            | Apache-2.0       |
| Gitleaks      | Secret     | 入库前静态检测               | A            | 是            | MIT              |
| TruffleHog    | Secret     | 深度检测和凭证验证           | R/D          | 否            | AGPL-3.0         |
| RAGFlow       | 产品参考   | 文档摄取与引用 UI            | C/B          | 参考          | 按版本核验       |
| Renku         | 产品参考   | 科研项目和可复现空间         | C            | 参考          | Apache-2.0       |

<a id="section-14"></a>
## 14. 许可证与供应链尽调

[返回目录](#toc)

<a id="section-14-1"></a>
### 14.1 基本原则

本文许可证信息用于技术预筛，不构成法律意见。最终使用前必须锁定具体版本、Tag 或 Commit，并对以下内容做正式审查：

- 根目录 LICENSE、NOTICE、COPYING 和依赖清单。

- ee/、enterprise/、commercial/ 等混合许可目录。

- 模型权重、训练数据、容器镜像和预置插件的独立许可。

- Source-available、AGPL、SSPL、Elastic License、FSL 或附加条款。

- 网络服务、SaaS、托管和竞争性产品限制。

- 修改、分发、署名、NOTICE 和源码提供义务。

<a id="section-14-2"></a>
### 14.2 重点审查项目

| **项目**   | **重点风险**                        | **建议**                             |
|------------|-------------------------------------|--------------------------------------|
| Sourcebot  | FSL/企业目录与竞争性使用限制        | 先作 UI 参考；直接集成需法务书面结论 |
| MinerU     | 自定义或附加许可条件、模型权重      | 与 Docling 做能力比较后再审查        |
| Dify       | 开源许可证附加条件                  | 不作为证据核心；商业使用先审查       |
| Langfuse   | 核心与企业目录许可边界              | 只使用明确许可路径并建立 SBOM        |
| OpenHands  | 不同目录和企业功能边界              | 按实际构建路径核验                   |
| Phoenix    | 当前版本许可可能变化                | 锁定版本后核验，不依赖历史印象       |
| TruffleHog | AGPL 网络服务和衍生义务             | 隔离部署或选择 Gitleaks 为默认       |
| Neo4j      | Community/Enterprise 功能与许可差异 | 需求明确后再评估                     |

<a id="section-14-3"></a>
### 14.3 供应链治理

推荐在 CI 中建立：

```text
SBOM（SPDX 或 CycloneDX）
License Policy Scan
Container/Image Vulnerability Scan
Dependency Pinning + Checksum
NOTICE Aggregation
Provenance / Signature Verification
Critical CVE Response Process
```

解析器和模型服务应固定镜像摘要；任何新依赖进入生产前需通过许可证、安全和数据外发评审。

<a id="section-15"></a>
## 15. PoC 复用计划

[返回目录](#toc)

<a id="section-15-1"></a>
### 15.1 PoC 目标

PoC 不是验证“组件能否启动”，而是验证真实证据问题能否被正确回答。建议选一个具备以下数据的项目：

```text
1–2 个代码仓库
20–50 个相关 Commit/PR
20–100 个 Codex Thread
50–200 个 Experiment Run
10–30 份 PDF/Markdown/Notebook
10–20 个可追溯数值 Claim
```

<a id="section-15-2"></a>
### 15.2 第一批组件

| **组件**              | **PoC 验证任务**                    | **通过标准**                          |
|-----------------------|-------------------------------------|---------------------------------------|
| Tree-sitter           | 目标语言 Symbol 切块                | 关键 Symbol 边界准确，失败可检测      |
| SCIP                  | Definition/Reference/Implementation | 关键跨文件关系覆盖率满足样本要求      |
| Zoekt                 | 标识符/路径/正则召回                | 低延迟且能做仓库/Branch Scope         |
| Codex Adapter         | Thread/Turn/Item 事件               | 完整重放，Patch/命令状态不丢失        |
| Docling + GROBID      | PDF 表格/引文/坐标                  | 关键页、表、章节可定位                |
| MLflow + DVC          | Run/Commit/Dataset/Config           | 选定 Run 全部形成确定性版本链         |
| PostgreSQL + pgvector | Entity/Edge/Search View             | 删除、ACL 和 Generation 一致          |
| Haystack              | 分源并行和 Evidence Pack            | Pipeline 可追踪、可替换               |
| Temporal              | 摄取和重建                          | 失败重试、幂等、断点恢复              |
| OpenFGA               | 检索前授权                          | 越权样本全部阻断                      |
| Ragas/promptfoo       | 回归测试                            | 能运行自定义 Commit/Run/Citation 断言 |

<a id="section-15-3"></a>
### 15.3 三条必须跑通的纵向切片

<a id="切片-a-会话到代码"></a>
#### 切片 A：会话到代码

| Codex Thread → FileChange/Patch → Commit → Changed Symbol → TestResult |
|------------------------------------------------------------------------|

验收问题：该会话提出的改动是否真正提交并通过测试？

<a id="切片-b-文档到实验"></a>
#### 切片 B：文档到实验

| Document Claim → Table/Figure → Run → Metric → Config/Dataset → Commit |
|------------------------------------------------------------------------|

验收问题：报告中的数值来自哪些 Run，条件是否一致？

<a id="切片-c-旧结论到当前版本"></a>
#### 切片 C：旧结论到当前版本

```text
Claim → Original Run → Commit A → Current Commit B
→ Diff/Dependency → Current Revalidation → Validity Status
```

验收问题：当前 main 是否仍然支持旧结论，为什么？

<a id="section-15-4"></a>
### 15.4 PoC 决策门槛

- 不因为某项目“功能多”就选择；以真实 Golden Questions 的质量、延迟、资源和适配成本评分。

- 任何组件若无法满足检索前 ACL 或删除传播，应被隔离在上游并由自研层过滤。

- 文档解析器必须以真实中文、英文、扫描、公式和表格样本对比。

- 向量后端在 pgvector 不能满足已测负载前，不引入独立集群。

- 图后端在 Edge Table 的真实查询出现瓶颈前，不引入。

<a id="section-15-5"></a>
### 15.5 建议实施顺序

```text
第 1 步：统一 Schema、ID、事件和 Golden Questions
第 2 步：Git + Codex + MLflow 的确定性链
第 3 步：Docling/GROBID + Claim/Run 对齐
第 4 步：分源检索、Evidence Pack 和引用 UI
第 5 步：ACL、删除、Trace 和回归门禁
第 6 步：失效检测和冲突处理
第 7 步：根据瓶颈选择 Qdrant/OpenSearch/Graphiti/OpenMetadata
```

<a id="section-16"></a>
## 16. 项目卡片索引与官方链接

[返回目录](#toc)

<a id="section-16-1"></a>
### 16.1 直接复用优先

- [Haystack](https://github.com/deepset-ai/haystack)

- [Tree-sitter](https://github.com/tree-sitter/tree-sitter)

- [SCIP](https://github.com/scip-code/scip)

- [Zoekt](https://github.com/sourcegraph/zoekt)

- [Codex App Server](https://developers.openai.com/codex/app-server)

- [Docling](https://github.com/docling-project/docling)

- [GROBID](https://github.com/grobidOrg/grobid)

- [MLflow](https://github.com/mlflow/mlflow)

- [DVC](https://github.com/iterative/dvc)

- [OpenLineage](https://github.com/OpenLineage/OpenLineage)

- [PostgreSQL](https://www.postgresql.org/)

- [pgvector](https://github.com/pgvector/pgvector)

- [Temporal](https://github.com/temporalio/temporal)

- [OpenInference](https://github.com/Arize-ai/openinference)

- [OpenFGA](https://github.com/openfga/openfga)

- [Keycloak](https://github.com/keycloak/keycloak)

- [Ragas](https://github.com/vibrantlabsai/ragas)

- [promptfoo](https://github.com/promptfoo/promptfoo)

- [Gitleaks](https://github.com/gitleaks/gitleaks)

<a id="section-16-2"></a>
### 16.2 适配与算法参考

- [Aider](https://github.com/Aider-AI/aider)

- [Joern](https://github.com/joernio/joern)

- [OpenHands](https://github.com/OpenHands/OpenHands)

- [SWE-agent](https://github.com/SWE-agent/SWE-agent)

- [Continue](https://github.com/continuedev/continue)

- [PaperQA2](https://github.com/future-house/paper-qa)

- [Papermill](https://github.com/nteract/papermill)

- [nbdime](https://github.com/jupyter/nbdime)

- [RO-Crate Python](https://github.com/ResearchObject/ro-crate-py)

- [OpenMetadata](https://github.com/open-metadata/OpenMetadata)

- [DataHub](https://github.com/datahub-project/datahub)

- [Graphiti](https://github.com/getzep/graphiti)

- [Microsoft GraphRAG](https://github.com/microsoft/graphrag)

- [LightRAG](https://github.com/HKUDS/LightRAG)

- [RAGFlow](https://github.com/infiniflow/ragflow)

- [Renku](https://github.com/SwissDataScienceCenter/renku)

<a id="section-16-3"></a>
### 16.3 后期基础设施候选

- [Qdrant](https://github.com/qdrant/qdrant)

- [OpenSearch](https://github.com/opensearch-project/OpenSearch)

- [lakeFS](https://github.com/treeverse/lakeFS)

- [Open Policy Agent](https://github.com/open-policy-agent/opa)

- [Langfuse](https://github.com/langfuse/langfuse)

- [Arize Phoenix](https://github.com/Arize-ai/phoenix)

- [TruffleHog](https://github.com/trufflesecurity/trufflehog)

- [in-toto](https://github.com/in-toto/in-toto)

<a id="section-17"></a>
## 17. 最终推荐

[返回目录](#toc)

> **推荐方案**
>
> 采用“Haystack + PostgreSQL/pgvector + Zoekt + Tree-sitter/SCIP + Codex 官方事件 + Docling/GROBID + MLflow/DVC/OpenLineage + Temporal + OpenInference + OpenFGA + Ragas/promptfoo”的轻量组合。OpenMetadata、Graphiti、OpenSearch、Qdrant 和 Joern 仅在真实瓶颈被量化后引入。

项目的长期壁垒不在于安装了多少开源组件，而在于能否稳定、准确地连接：

```text
讨论与决策
→ Agent 执行和 Patch
→ Git Commit 与代码 Symbol
→ Experiment Run、Config 和 Dataset
→ 科研 Claim、表格和图
→ 当前版本的有效性、冲突与证据缺口
```

本文项目描述和链接基于截至 2026-07-21 可访问的官方文档或公开仓库。任何生产采用都应记录具体版本、内部评测结果、许可证结论、安全配置和替换方案。
