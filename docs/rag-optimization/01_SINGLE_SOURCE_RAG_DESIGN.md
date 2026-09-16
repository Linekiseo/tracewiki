# 单源 RAG 专项优化设计

> 状态说明：本文件是早期单源总览，保留用于快速阅读。六个来源已经拆成独立、可执行的
> 详细设计，后续实现和评审以
> [sources/00_SOURCE_DESIGN_CONTRACT.md](sources/00_SOURCE_DESIGN_CONTRACT.md)、
> [Code](sources/01_CODE_SOURCE_RAG.md)、
> [Codex](sources/02_CODEX_SOURCE_RAG.md)、
> [Experiment](sources/03_EXPERIMENT_SOURCE_RAG.md)、
> [Notebook](sources/04_NOTEBOOK_SOURCE_RAG.md)、
> [Document](sources/05_DOCUMENT_SOURCE_RAG.md) 和
> [Workspace](sources/06_WORKSPACE_SOURCE_RAG.md) 为准。本总览中合并的
> “Experiment / Notebook” 不再作为目标边界。

## 1. 阶段目标

多源系统的上限由最弱的单源 Retriever 决定。本阶段不追求跨源“看起来丰富”，而是让
每一类来源独立完成：

```text
Query → Source Query Rewrite → Source Retrieval
      → Source Rerank → Parent/Relation Expansion
      → Source Context → Source Evaluation
```

单源优化的完成标准不是 API 可用，而是：

- 有至少一组版本化 Golden Query；
- 能区分检索单元和引用实体；
- 有可诊断的多路召回 Trace；
- 有来源专用重排；
- 有 hard negative；
- Recall、MRR、版本、locator、延迟均达到阶段门槛；
- 有失败案例和回滚开关。

## 2. 单源公共基础

### 2.1 Retrieval Unit 与 Entity 分离

现有稳定实体继续作为知识和引用主键，新建可重建的 Retrieval Unit：

| 字段 | 说明 |
| --- | --- |
| `unit_id` | 与 chunker/model/version 相关，可重建 |
| `entity_id` | 稳定领域实体 |
| `parent_unit_id` | 层级检索的父单元 |
| `unit_type` | `ast_block`、`episode_summary`、`claim_context` 等 |
| `content` | 用于检索的压缩表示 |
| `context_ref` | 命中后加载的完整理解上下文 |
| `embedding_model` | 模型和版本 |
| `sparse_terms` | 可选领域词项 |
| `metadata` | 路径、语言、时间、状态、版本等 |

这样可做到“小单元召回、大上下文理解”，同时避免重建向量时改变 Citation URI。

### 2.2 来源内多路召回

每个来源至少支持三类通道：

1. 精确/结构化通道；
2. sparse lexical 通道；
3. neural dense 或 multi-vector 通道。

图结构明显的来源再增加 graph 通道。来源内候选先使用 RRF 组合，避免在没有训练数据时
直接比较不同原始分数：

```text
rrf(e) = Σ channel_weight[c] / (k + rank_c(e))
```

积累标注后，RRF 只用于 candidate union，最终使用来源专用 reranker 输出
`P(relevant | query, candidate, source)`。

### 2.3 Parent Expansion 后置

embedding 只针对检索表示；候选确定后再加载：

- 父 Section / File / Turn；
- 相邻结构块；
- 图邻居；
- 表格标题、表头和单位；
- 版本信息。

禁止把所有父上下文提前拼进 embedding，防止向量被通用背景稀释。

### 2.4 单源评测公共切片

每个来源的 Golden Set 至少覆盖：

- 中文自然语言；
- 英文自然语言；
- 中文 + 英文标识符；
- 精确 ID/路径/版本查询；
- 低词面重合查询；
- 同名实体 hard negative；
- 当前/历史版本 hard negative；
- 可回答/不可回答；
- 简单查询/多跳查询；
- ACL 拒绝场景。

第一阶段每个来源建议 20–30 条，稳定后扩充到 50 条以上。

---

## 3. Code RAG：类型化代码图驱动检索

### 3.1 目标问题

Code Retriever 必须分别支持：

1. 定义与用法查找；
2. 当前实现解释；
3. 跨文件调用链；
4. Bug 定位；
5. 变更影响分析；
6. 相关测试定位；
7. 历史实现与版本差异；
8. 代码补全/修改所需上下文。

这些任务需要不同的检索和图遍历模板，不能只调整同一个 Top-K。

### 3.2 现状差距

- 文件正文和完整 Symbol 正文直接向量化，长符号语义稀释且 File/Symbol 重复。
- `local-hash-v2` 更接近可解释词项特征，不具备充分的代码语义泛化能力。
- CALLS/REFERENCES 依赖简单名称与局部唯一性，覆盖率低。
- Graph 在文本 Top-K 之后附加，不能召回低词面重合依赖。
- 缺少类型、继承、override、def-use、路由、配置、测试和依赖关系。
- 没有代码专用 reranker。
- 当前上下文只是代码 snippet，缺少签名、父作用域、caller/callee、测试和 Diff。

### 3.3 AST 结构化 Retrieval Unit

采用递归 AST chunk，保留现有 CodeSymbol 实体：

```text
CodeSymbol
├── symbol_signature
├── symbol_doc
├── ast_block_1
├── ast_block_2
└── ast_block_n
```

切分规则：

1. 函数/类低于 token 上限时作为一个单元。
2. 超长函数按语句块、分支、内部函数递归切分。
3. 每个子块的检索文本包含：
   - qualified name；
   - 签名；
   - 父类/父函数；
   - docstring；
   - 当前块正文；
   - 直接使用的类型和标识符摘要。
4. import 只保留当前块实际引用的子集。
5. 生成模型使用的理解上下文在命中后再补父作用域，不提前污染 embedding。
6. Retrieval Unit 记录 AST path 和精确行号，Citation 仍指向 CodeSymbol/FileVersion。

建议初始 token 范围：

| 单元 | 目标范围 | 上限行为 |
| --- | ---: | --- |
| Signature/Doc | 32–256 | 与首个实现块合并 |
| Function/Method | 128–600 | 超限递归切分 |
| Class Summary | 128–400 | 方法体不全部内联 |
| AST Block | 80–400 | 小邻块可合并 |
| File Summary | 100–300 | 只用于文件级召回 |

阈值必须通过本项目 Golden Set 调优，不作为永久常量。

### 3.4 代码语义图

#### 第一层：现有确定性图

- CONTAINS
- DEFINES
- IMPORTS
- CALLS
- REFERENCES

#### 第二层：SCIP/LSP/编译器补强

- IMPLEMENTS
- OVERRIDES
- TYPE_OF
- ACCEPTS
- RETURNS
- INSTANTIATES
- RESOLVES_TO

#### 第三层：数据流与工程关系

- READS / WRITES
- FLOWS_TO
- ROUTES_TO
- CONFIGURES
- DEPENDS_ON
- TESTS / COVERS
- VALIDATED_BY
- CHANGED_BY

边必须携带：

```text
derivation       tree_sitter / scip / lsp / compiler / coverage / rule / human
confidence       0..1
review_status    confirmed / unreviewed / rejected
version          commit or worktree snapshot
locator          产生该边的源码位置或外部报告
```

不能把所有静态边无条件映射成“人工确认关系”。确定性解析事实可以标记
`machine_confirmed`，推断边必须与人工确认分开。

### 3.5 任务化图检索模板

| Query Task | 种子 | 允许路径 | 默认深度 |
| --- | --- | --- | ---: |
| 定义查找 | identifier/path hits | DEFINES / RESOLVES_TO | 1 |
| 当前实现 | symbol hits | CONTAINS / IMPORTS / CALLS | 1–2 |
| Bug 定位 | error/test/message hits | VALIDATED_BY⁻¹ / CALLS / REFERENCES | 2–3 |
| 影响分析 | changed symbol | CALLS⁻¹ / REFERENCES⁻¹ / TESTS⁻¹ | 2–3 |
| 接口实现 | interface/type | IMPLEMENTS⁻¹ / OVERRIDES⁻¹ | 1–2 |
| 代码修改 | target symbol | CALLS / TYPE_OF / TESTS⁻¹ / CONFIGURES | 2 |
| 历史追踪 | symbol lineage | CHANGED_BY / SAME_SYMBOL_AS | 2–4 |

路径分建议：

```text
path_score =
    seed_relevance
    × Π edge_confidence
    × edge_type_weight(query_task)
    × hop_decay^hop_count
    × version_alignment
```

遍历是有向、带类型、带预算的 Beam Search，不采用无约束 BFS。

### 3.6 代码多路召回与重排

Candidate channels：

1. `identifier_exact`：qualified name、symbol、path、commit。
2. `sparse_code`：代码词法和自然语言注释。
3. `dense_code`：代码/文本双塔或统一代码 embedding。
4. `graph_seed_expansion`：类型化图路径。
5. `history_diff`：commit message、DiffHunk、changed symbol。
6. `test_failure`：失败命令、错误栈、测试名到代码路径。

Reranker 输入：

```text
query + task
candidate signature
candidate retrieval block
path summary
file/module metadata
version
```

Reranker 输出相关概率和原因标签，例如：

- exact_target；
- dependency_context；
- test_context；
- historical_only；
- duplicate_parent；
- irrelevant_same_name。

### 3.7 Code Context Pack

```text
Target Symbol
├── signature + doc + selected implementation blocks
├── enclosing class/module summary
├── required types/imports
├── top callers/callees with path explanation
├── related tests and last validation
├── relevant diff/history
└── unresolved dependencies / version warnings
```

预算规则：

- 目标实现 35%；
- 类型和直接依赖 20%；
- caller/callee 20%；
- 测试 15%；
- Diff/历史 10%；
- 若问题不是历史类，把 Diff 预算让给目标实现。

### 3.8 Code RAG 指标与退出标准

| 指标 | 当前基线 | 第一阶段目标 |
| --- | --- | --- |
| Symbol Recall@10 | 待建立 | ≥ 0.85 |
| File Recall@10 | 待建立 | ≥ 0.90 |
| MRR@10 | 待建立 | ≥ 0.70 |
| Required Path Recall | 待建立 | ≥ 0.75 |
| Exact Commit Accuracy | 待建立 | ≥ 0.95 |
| Wrong-version Rate | 待建立 | ≤ 0.02 |
| Locator Accuracy | 待建立 | ≥ 0.98 |
| P95 单源召回 | 待建立 | ≤ 1.5 s（当前规模） |

目标必须在 Code Golden Set 上实测，可根据首轮人工标注调整，但调整要记录原因。

### 3.9 可讲亮点

> 没有把代码当普通文档切块，而是将稳定 Symbol 与可重建 AST Retrieval Unit 分离，
> 使用精确标识符、代码语义向量和类型化关系图多路召回；针对 Bug 定位、影响分析和
> 代码修改选择不同图遍历模板，再用代码 reranker 和结构化上下文预算组装证据。

---

## 4. Codex RAG：研发会话时序与状态检索

### 4.1 目标问题

- 哪个会话处理过某个目标？
- 为什么采用某个实现方案？
- 哪些文件和 Patch 被修改？
- 实际执行了什么命令？
- 哪个验证通过或失败？
- 某个问题在哪一步卡住？
- 多次尝试之间有什么变化？

### 4.2 现状差距

- 已有 Thread/Turn/Item/Episode，但搜索主要仍以扁平 Item 文本混合排序。
- ToolResult、ToolCall 和 CommandExecution 数量大，容易压过 Goal/Decision。
- 相同命令、Patch、文件内容会产生重复语义。
- 会话中的“助手说已通过”与真实退出码虽已区分，但检索排序尚未充分利用事实状态。
- 缺少时序距离、目标归属和 Patch→Validation 路径评分。

### 4.3 分层 Retrieval Unit

```text
Thread Summary
└── Development Episode
    ├── Goal Unit
    ├── Decision Unit
    ├── Change Unit
    ├── Command Unit
    ├── Validation Unit
    └── Outcome / Unresolved Unit
```

Episode 的边界由以下信号联合确定：

- UserGoal 切换；
- 文件修改集合显著变化；
- 命令阶段切换（探索→修改→验证）；
- 长时间间隔；
- Turn 完成/失败；
- 人工修订。

Episode Summary 只做派生 Retrieval Unit，不能覆盖原始 Item。

### 4.4 时序事件图

补充或规范关系：

- PURSUES：Episode → Goal
- CONSIDERS：Episode → Alternative
- DECIDES：Episode → Decision
- EXECUTES：Turn/Episode → Command
- PRODUCES：Command/Turn → Patch
- CHANGES：Patch → FileChange
- TARGETS：FileChange → CodeSymbol/FileVersion
- VALIDATES：Validation → Patch/Commit
- FAILS_ON：Command → Error/Entity
- SUPERSEDES：Decision/Patch → Earlier Decision/Patch
- FOLLOWS：Item → Previous Item

查询时使用“事件状态”而不是文本措辞：

```text
planned ≠ executed
assistant_claimed_pass ≠ validation_passed
patch_created ≠ committed
candidate_binding ≠ confirmed_binding
```

### 4.5 Codex 多路召回

1. Goal/Decision lexical+dense；
2. 文件路径、Symbol、命令精确匹配；
3. Episode Summary dense；
4. 时间窗口过滤；
5. Code Binding 反向召回；
6. 时序路径召回。

初始候选配额不按 Item 类型平均，而按查询角色分配。例如 rationale 查询优先 Goal、
Decision、Alternative；验证查询优先 CommandExecution、ValidationResult、ToolResult。

### 4.6 Codex Reranker

主要特征：

- 与 Goal 的相似度；
- 与目标 Code Entity 的绑定强度；
- Item 类型先验；
- 同 Episode 距离；
- 是否具有真实退出码；
- Patch 是否被后续验证；
- 状态是否被更晚事件 supersede；
- 时间和目标 Scope 是否匹配。

### 4.7 Codex Context Pack

```text
Episode Goal
→ Relevant Decision / Alternative
→ Commands and Findings
→ Patch / Changed Files
→ Validation Facts
→ Outcome / Unresolved Issues
```

只保留能构成因果或验证链的 Item，ToolResult 原文按需展开。

### 4.8 指标与退出标准

| 指标 | 第一阶段目标 |
| --- | ---: |
| Correct Thread Recall@5 | ≥ 0.90 |
| Correct Episode Recall@5 | ≥ 0.85 |
| Event-order Accuracy | ≥ 0.98 |
| Patch→Validation Path Recall | ≥ 0.85 |
| False “validated” Rate | 0 |
| Duplicate Context Ratio | ≤ 0.15 |
| Locator Accuracy | ≥ 0.98 |

### 4.9 可讲亮点

> 将会话从聊天文本升级为研发事件时序图，用 Goal、Decision、Patch、Command 和真实
> Validation 构造 Episode；检索时把目标相关性、时间距离和事实状态联合排序，能够回答
> “为什么改、改了什么、是否真正验证”，而不是只搜索聊天关键词。

---

## 5. Experiment / Notebook RAG：结构化事实优先

### 5.1 目标问题

- 哪个 Run 使用了某个数据集/commit/config？
- 指标提升是否成立，单位和聚合方式是什么？
- 如何复现最佳 Run？
- 哪些 Run 可直接比较？
- Notebook 输出对应哪个参数、Cell 和 Artifact？

### 5.2 核心原则

实验数据不是普通文本。精确字段、数值、单位、数据版本、配置差异和运行状态的优先级
高于 embedding 相似度。

### 5.3 Query Plan

解析为结构化约束：

```python
ExperimentQuery(
    experiment_ids=[],
    run_status=[],
    metric_name=None,
    metric_operator=None,
    metric_value=None,
    metric_direction=None,
    dataset_id=None,
    dataset_version=None,
    commit=None,
    config_filters={},
    time_range=None,
    aggregation=None,
)
```

解析失败的字段保留为文本召回条件，不让 LLM 直接生成并执行任意 SQL。第一版使用白名单
AST/Filter DSL 转换为参数化 SQL。

### 5.4 检索顺序

1. 精确 ID、metric、config、dataset、commit 和状态过滤；
2. Experiment/Run 描述 lexical+dense；
3. Notebook Cell/Output/Artifact 多表示召回；
4. Run→Metric→Artifact→Commit 关系扩展；
5. 可比性检查；
6. 数值计算和排序。

### 5.5 可比性门

两个 Run 比较前必须检查：

- metric definition；
- unit 和 direction；
- dataset ID/version/split；
- seed/repetition；
- config control variables；
- commit/environment；
- aggregation function；
- run status。

不满足时输出“不可直接比较”和缺失字段，不能给出确定的优劣结论。

### 5.6 Experiment Context Pack

```text
Experiment Definition
├── Selected Run(s)
├── Config Diff
├── Dataset Version
├── Commit / Environment
├── Metric Definition + Raw Values + Aggregation
├── Artifact / Notebook Locator
└── Comparability Warnings
```

### 5.7 指标与退出标准

| 指标 | 第一阶段目标 |
| --- | ---: |
| Exact Run Recall@5 | ≥ 0.95 |
| Metric/Config Filter Accuracy | ≥ 0.98 |
| Numeric Answer Accuracy | ≥ 0.99 |
| Unit/Direction Accuracy | 1.00 |
| Comparability Warning Recall | ≥ 0.95 |
| Reproduction Role Coverage | ≥ 0.90 |

当前正式库没有 Experiment/Run，因此开始本阶段前必须接入一个可复现实验样例集，至少
包含 baseline、改进 Run、失败 Run、不同 dataset version 和不可比较 Run。

### 5.8 可讲亮点

> 对实验来源采用“结构化约束优先、语义召回补充”的混合方案；在生成结论前做 metric、
> dataset、commit、config 和 aggregation 的可比性检查，避免向量相似但实验条件不同的
> Run 被错误比较。

---

## 6. Document RAG：层级、多表示、Claim 中心

### 6.1 目标问题

- 某个结论在哪一节、哪一页提出？
- 表格中的数值如何支撑 Claim？
- 哪些段落支持或反驳某个结论？
- 某 Figure、Formula、Citation 的上下文是什么？
- 多版本报告中的结论是否变化？
- 整篇文档的主题和证据结构是什么？

### 6.2 Retrieval Unit

```text
Document Summary
└── Section Summary
    ├── Paragraph / Semantic Block
    ├── Claim Unit
    ├── Table Unit
    │   ├── Row Unit
    │   └── Cell Fact Unit
    ├── Figure Unit
    └── Citation Context Unit
```

每个小单元都携带标题路径：

```text
Document Title > H1 > H2 > H3
```

PDF/DOCX 的 page/bbox、表格坐标和 Figure caption 保留在 locator，不把页面 OCR 顺序当作
可靠文档层级。

### 6.3 多表示索引

同一个实体可以有多种 Retrieval Unit：

- 原文表示；
- 标题路径 + 原文；
- Claim 原子命题；
- Table schema + row；
- Figure caption + surrounding paragraph；
- 引用上下文；
- Section summary；
- Document community/global summary。

这些 Unit 命中后合并到稳定实体，避免同一段以多个表示占满 Top-K。

### 6.4 层级检索

1. 查询局部事实时召回 Paragraph/Claim/Cell；
2. 命中后补 Section 父上下文；
3. 全局总结问题先查 Section/Document Summary，再下钻代表性原文；
4. Claim 验证沿 SUPPORTED_BY/CONTRADICTED_BY 路径扩展；
5. 表格问题先做 schema/row/cell 检索，再回到 caption、header、footnote 和单位。

### 6.5 Document Reranker

输入特征：

- query 与 unit；
- 标题路径；
- unit type；
- Claim/表格/图类型匹配；
- 原文位置；
- 文档版本；
- 关系状态；
- 是否重复父子内容。

全局主题问题与局部事实问题使用不同 rerank profile。

### 6.6 Document Context Pack

```text
Question-matched Claim / Passage
├── Heading Path + Page
├── Minimal Original Text
├── Parent Section Context
├── Table/Figure/Formula Context
├── Supporting / Contradicting Evidence
└── Document Version / Citation
```

### 6.7 指标与退出标准

| 指标 | 第一阶段目标 |
| --- | ---: |
| Passage/Claim Recall@10 | ≥ 0.90 |
| Table Cell Recall@10 | ≥ 0.90 |
| Section Parent Accuracy | ≥ 0.98 |
| Page/Cell Locator Accuracy | ≥ 0.98 |
| Citation Precision | ≥ 0.95 |
| Claim Support/Contradiction Recall | ≥ 0.90 |
| Parent-child Duplicate Ratio | ≤ 0.15 |

### 6.8 可讲亮点

> 文档采用层级与多表示索引：用小粒度 Claim/Cell 召回，用 Section 和表格上下文理解；
> 局部事实与全局总结走不同检索计划，并将 Claim 的支持、反证和原文 locator 一起交给
> 生成层。

---

## 7. Workspace RAG：状态和关系优先

### 7.1 定位

Workspace 不是大规模知识语料，而是研究项目的控制面。它主要回答：

- 当前研究处于哪个 Topic/Iteration？
- 有哪些进行中、阻塞或待复核任务？
- 哪些证据已经挂接？
- 下一步行动的依据是什么？

### 7.2 检索设计

优先级：

1. project/topic/iteration/work-item 精确状态过滤；
2. 当前状态和时间排序；
3. 目标/假设/任务文本 sparse+dense；
4. 关系扩展到挂接证据；
5. Research Intelligence 只作为派生建议，不作为事实替代。

Workspace 结果默认不参与所有查询。只有以下情况路由：

- 用户明确询问计划、阶段、任务、阻塞；
- rationale/global synthesis 需要研究上下文；
- 其他来源结果需要解析所属 iteration。

### 7.3 指标与退出标准

| 指标 | 第一阶段目标 |
| --- | ---: |
| Current Status Accuracy | 1.00 |
| Topic/Iteration Recall@5 | ≥ 0.95 |
| Attached Evidence Path Recall | ≥ 0.90 |
| Stale Work-item Rate | 0 |

### 7.4 可讲亮点

> 将工作区作为 RAG 的查询控制面，而不是与代码、文档竞争 Top-K；它提供研究阶段、任务
> 状态和证据归属，为跨源 Query Planner 补充 Scope 和 required roles。

---

## 8. 单源统一验收流程

每个来源按以下顺序验收：

1. 固化数据快照和 Golden Query；
2. 运行当前基线；
3. 只替换 chunk/retrieval unit，做消融；
4. 加入 sparse/dense 多路召回，做消融；
5. 加入来源专用 reranker，做消融；
6. 加入 parent/graph expansion，做消融；
7. 检查版本、ACL、locator；
8. 检查 P50/P95、索引耗时、存储增量；
9. 记录失败查询和 hard negative；
10. 通过门槛后再接入跨源联合层。

每个实验只改变一个主要变量，结果写入项目 Evaluation Run。禁止同时更换 chunker、
embedding、reranker 和 prompt 后声称知道收益来自哪里。
