# 多源联合检索与证据融合设计

状态：`REPOSITORY_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD`  
设计版本：multi-source-rag-v3  
基线日期：2026-07-27  
对应面试文档：[07_MULTISOURCE_INTERVIEW.md](interview/07_MULTISOURCE_INTERVIEW.md)

> **2026-07-30 实现覆盖层**
>
> 本文正文仍是联合检索功能与退出门基线。当前显式全局 V2 已从
> Runtime→Platform→六来源 adapter→两波 planner/fusion→typed graph→context/answer
> 主链可达；使用来源内 reviewed calibration bundle 时才允许成功执行，缺失或 digest
> 不匹配会 fail-closed/fallback。默认始终为 V1，trace 明确
> `HOLD_DEFAULT_V1`、`quality_qualified=false`。仓库测试中的受控 bundle 只证明调用链，
> 不替代真实跨源 60-case replay、ECE/Brier、SLO、shadow/canary，故联合层只判 L3。

## 1. 设计目标

在六个单源 Retriever 达到各自质量门槛后，联合层解决五个单源无法独立解决的问题：

1. 一个问题需要哪些来源，不需要哪些来源？
2. 不同来源返回的异构分数如何公平比较？
3. 如何利用跨源关系找到没有查询词、但在证据链上必需的实体？
4. 如何处理版本不一致、状态冲突、反证和证据缺失？
5. 如何在任一来源超时、过期或无权访问时，给出诚实的部分答案或拒答？

联合层不替代单源检索，也不重新解析原文。它的输入是统一候选，输出是结构化
Evidence Pack。

联合层的前置条件不是“接口已存在”，而是每个单源都满足：

- 有自己的 Golden Set；
- 可直接定位自己的最小事实实体；
- 原始事实与派生事实可区分；
- 版本/时间/ACL 可过滤；
- raw score 已完成来源内融合；
- 有 relevance calibration 或明确的 rank fallback；
- SourceResult 能报告 complete/partial/unavailable；
- Citation locator 可回到真实来源。

## 2. 为什么不能使用“全源一个索引”

六类来源具有不同的：

- 文本分布；
- 实体粒度；
- 版本语义；
- 权威度；
- 召回算法；
- 事实状态；
- 更新频率；
- ACL；
- 分数范围。

把它们放进一个向量集合会产生三个直接问题：

1. 数量最大的 Codex ToolResult 或代码片段淹没低频但高权威的 Run/Claim；
2. 语义相似度无法表示实验数值精确性、代码版本或验证状态；
3. 同一个 Top-K 无法保证回答所需角色齐全。

因此采用“来源内最优、协议级联合、证据图扩展”的分层架构。

### 2.1 六个物理来源

| Retrieval Domain | 单源权威对象 | 优先检索 | 不可替代的正确性 |
| --- | --- | --- | --- |
| Code | FileVersion/Symbol/Diff/Test | exact、sparse、code dense、typed graph | commit/dirty state、symbol、line |
| Codex | Goal/Episode/Patch/Command/Validation | event type、temporal、goal、binding graph | 事件顺序、observed outcome |
| Experiment | Run/MetricDefinition/Observation/Dataset/Config | structured filter、numeric、sparse | unit/split/step/aggregation/comparability |
| Notebook | Revision/Execution/Cell/Output/Error/Parameter | cell type、execution、dataflow | display/execution order、stale output |
| Document | Paragraph/Claim/TableFact/Figure/Citation | hierarchy、table、sparse/dense | source span、header path、validation level |
| Workspace | Topic/Iteration/Work/Decision/Requirement | structured、temporal、dependency graph | authoritative current/as-of state |

设计细节以六份单源文档为准：

- [Code Source](sources/01_CODE_SOURCE_RAG.md)
- [Codex Source](sources/02_CODEX_SOURCE_RAG.md)
- [Experiment Source](sources/03_EXPERIMENT_SOURCE_RAG.md)
- [Notebook Source](sources/04_NOTEBOOK_SOURCE_RAG.md)
- [Document Source](sources/05_DOCUMENT_SOURCE_RAG.md)
- [Workspace Source](sources/06_WORKSPACE_SOURCE_RAG.md)

### 2.2 三层来源标识

为解决当前 Notebook 被 API `experiment` 分支合并的问题，V2 明确三层标识：

```text
source_instance
  具体连接器/物理来源，例如 mlflow-prod、repo-main、notebook-dir

retrieval_domain
  code | codex | experiment | notebook | document | workspace

api_source_family
  V1 compatibility enum: code | codex | experiment | document | workspace
```

兼容规则：

- V1 `include=["experiment"]` 默认扇出到 `experiment + notebook`；
- V2 可以显式 `include_domains=["experiment"]` 或 `["notebook"]`；
- V2 Response 必须返回真实 `retrieval_domain`；
- UI 可继续展示“实验”大类，但筛选和 Trace 必须拆开；
- `IterationLink.source_type` 增加 `notebook`；
- relevance calibration 按 retrieval domain 独立训练，不能把 Notebook 与 Run 混成一组。

### 2.3 来源能力注册表

联合层不硬编码“每个源一定支持 dense/graph”。Retriever 启动时注册：

```yaml
domain: notebook
retriever_version: notebook-source-rag-v2
supported_tasks:
  - cell_location
  - output_trace
  - error_diagnosis
  - parameter_compare
  - dataflow
channels: [exact, sparse, dense, structured, execution_graph]
entity_types: [NotebookRevision, NotebookExecution, Cell, Output, Error, Parameter]
filters: [project, experiment, run, version, cell_type, status, date]
time_semantics: revision_and_execution
version_semantics: content_hash_plus_execution
supports_as_of: false
supports_numeric: true
supports_relation_expansion: true
calibration_version: notebook-cal-v1
index_generation: ...
acl_model: inherited
health: ready
```

Planner 只生成注册表支持的 task/filter/path。能力缺失要进入 `planning_gap`，不能在运行中
静默忽略。

### 2.4 统一候选契约

```text
RetrievalCandidate
  candidate_id
  entity_id
  retrieval_unit_id
  parent_entity_id
  source_instance
  retrieval_domain
  entity_type
  task
  title/snippet
  locator
  stable_version
  source_generation
  event_time / valid_time / observed_at
  raw_fact | derived_fact
  derivation / review_status / fact_status
  channel_scores/ranks
  calibrated_relevance
  calibration_version
  matched_roles
  authority_features
  version_alignment
  acl_ref/classification
  token_estimate
  trace_refs
```

`calibrated_relevance` 只表达“与当前子问题相关的概率/可比分值”，不包含 authority、
truth、version 和 relation confidence。

## 3. Query Planner

### 3.1 输出内容

Planner 从 `question + explicit scope + ACL` 生成：

- intent；
- query complexity；
- 子问题；
- required/optional evidence roles；
- source routes；
- 每来源候选预算；
- graph traversal templates；
- version/time constraints；
- context budget；
- stop conditions。

### 3.2 四档策略

| 复杂度 | 示例 | 策略 |
| --- | --- | --- |
| simple | “`HybridRetriever` 在哪里定义？” | 单源、精确/浅召回 |
| single_source | “当前代码如何计算融合分？” | 单源多路召回 + rerank |
| multi_source | “哪次会话修改了融合逻辑并验证？” | Code + Codex + relation |
| multi_hop | “论文结论在当前代码和实验下仍成立吗？” | 子问题分解 + 六源角色 + 多跳 |

第一版由规则和显式意图驱动。Planner 必须是可重放的 JSON，不能只存在于 LLM 隐式
思考中。

### 3.3 子问题与角色示例

问题：

> 报告中“新 reranker 提升 8%”的结论，在当前代码版本下仍然成立吗？

计划：

```yaml
intent: staleness_check
complexity: multi_hop
sub_questions:
  - id: q1
    question: 报告原始 Claim、表格和指标是什么？
    sources: [document]
    required_roles: [claim, source_location]
  - id: q2
    question: 支撑该 Claim 的 Run、Metric、DatasetVersion 和 Commit 是什么？
    sources: [experiment]
    required_roles: [run, metric, dataset_version, experiment_commit]
  - id: q3
    question: 当前相关 Symbol 和 Commit 是什么？
    sources: [code]
    required_roles: [current_code, current_commit]
  - id: q4
    question: 从实验 Commit 到当前 Commit 的相关 Diff 和验证是什么？
    sources: [code, codex, experiment]
    required_roles: [diff_or_revalidation]
graph_templates:
  - CLAIM_SUPPORT_PATH
  - RUN_REPRODUCTION_PATH
  - VERSION_DRIFT_PATH
stop_conditions:
  - all_required_roles_satisfied
  - explicit_unanswerable
```

### 3.4 路由策略

Planner 应避免“每个查询检索所有来源”。第一阶段使用 intent × source 权威矩阵和显式
关键词；后续从 Evaluation Trace 训练多标签路由器。

路由指标：

- Source Route Precision；
- Source Route Recall；
- Required Role Coverage；
- 平均激活来源数；
- 路由引入的延迟/成本。

### 3.5 Intent→Domain→Role 矩阵

| Intent | 主来源 | 可选来源 | 必需角色示例 |
| --- | --- | --- | --- |
| current_implementation | Code | Codex, Workspace | current_symbol, current_version, validation |
| historical_change | Code, Codex | Workspace | goal, patch/diff, target symbol, commit |
| rationale | Codex, Workspace | Code, Document | decision/goal, alternatives, implementation |
| experiment_validation | Experiment | Code, Notebook, Document | comparable run, metric definition, observation |
| notebook_debug | Notebook | Experiment, Codex, Code | failing cell, error, parameters, producer |
| claim_verification | Document, Experiment | Code, Notebook | atomic claim, source span, run/metric, qualifier |
| reproduction | Experiment, Code | Notebook, Codex | run, config, env, dataset, commit, command |
| staleness_check | Document, Experiment, Code | Notebook, Codex | claim, original commit, diff, current validation |
| project_status | Workspace | all source health | authoritative state, active work, evidence coverage |
| global_synthesis | Workspace + task sources | all relevant | section coverage, evidence diversity, conflicts |

该矩阵表达默认权威，而不是硬路由。显式 scope 优先；能力注册、ACL、数据可用性会修改计划。

### 3.6 角色契约

每个 role 定义：

```text
role name
allowed retrieval domains/entity types
minimum authority/review
version/freshness
cardinality
independence
required relations
fallback roles
```

例如 `run_or_metric` 过于模糊，V3 拆为：

- `experiment_run_identity`；
- `metric_definition`；
- `metric_observation`；
- `dataset_version`；
- `config_snapshot`；
- `environment_snapshot`。

角色满足不是 `source in results`，而是候选通过类型、版本、状态和关系约束。

### 3.7 Planner 安全边界

- Planner 只决定读路径，不产生外部 mutation；
- 不允许 Planner 放宽用户 scope/ACL；
- 只能使用 capability registry 中的 filter；
- 所有 LLM planner output 经 schema validation；
- 超出已知 task 的问题降级到 conservative search；
- 显式 ID/version 不允许被 query rewrite 丢失；
- 每个 sub-question 保存 parent question 和 rewrite provenance；
- Planner 模型不可用时使用 deterministic intent/role templates。

## 4. 并行来源召回

### 4.1 预算

`candidate_budget` 分成：

```text
source budget
  → channel budget
  → graph expansion budget
  → rerank budget
```

来源预算由 required roles 优先，而不是平均分：

```text
budget(source) =
    base
    + required_role_bonus
    + query_authority_bonus
    + historical_failure_bonus
```

低成本的精确检索先运行；如果已满足简单查询，可跳过 dense、graph 或其他来源。

### 4.2 超时与部分结果

每个 Source Retriever 独立返回：

```text
status: complete / partial / timeout / unavailable
candidates
coverage
index_version
latency
errors
```

联合层不得把来源超时解释为“该来源没有证据”。Evidence Pack 必须区分：

- `no_matching_evidence`；
- `source_unavailable`；
- `unauthorized`；
- `not_indexed`；
- `timeout`。

### 4.3 Orchestrator 执行 DAG

```text
request
→ scope/ACL resolution
→ capability snapshot
→ deterministic/LLM plan
→ wave-1 exact+structured retrieval (parallel)
→ early coverage check
→ wave-2 sparse+dense/source graph (only required)
→ source rerank/calibration
→ seed normalization/dedup
→ typed cross-source expansion
→ role-aware selection
→ conflict/version verification
→ corrective retrieval (0–2)
→ EvidencePackV2
```

并行边界：

- 六个 SourceRetriever 可并行；
- 同一来源 exact/structured 可先于 dense；
- graph expansion 依赖 seeds，不与第一波盲目并行；
- verification 依赖候选关系；
- corrective retrieval 只补已知缺口。

### 4.4 Consistency Watermark

请求开始时解析：

```text
project snapshot
code generation + commit/dirty snapshot
codex generation + last item time
experiment observed_at/run versions
notebook revision/execution versions
document generation/version
workspace snapshot/as_of
```

所有 SourceResult 返回使用的 watermark。跨源关系若连接不同 watermark，必须：

- 验证可兼容；
- 或标 `cross_source_snapshot_mismatch`；
- current 查询不能把旧文档/旧 Run 伪装成当前事实；
- historical 查询不能被 latest 自动覆盖。

### 4.5 取消与早停

- simple exact 查询满足全部角色后取消未启动的高成本通道；
- 已发出的远程请求可 cooperative cancel；
- counter-evidence required 时不能因找到一个 support 就早停；
- global synthesis 需满足 coverage，而非第一个高分；
- 早停原因写 Trace。

## 5. 来源内融合

每个来源先完成自己的：

1. candidate union；
2. RRF 或来源专用轻量融合；
3. entity 去重；
4. source reranker；
5. relevance calibration。

联合层只接收：

```text
relevance_probability ∈ [0, 1]
calibration_version
within_source_rank
```

### 5.1 校准数据

从 Golden Query 构造三元组：

```text
(query, candidate, relevance_label)
```

至少标记：

- 2：回答必需；
- 1：有帮助但非必需；
- 0：无关或干扰；
- -1：错误版本、错误实体或会误导。

第一版使用 isotonic regression 或 Platt scaling 对来源 reranker 输出做校准；样本不足时
使用 rank percentile + 分桶统计，不伪造概率。

### 5.2 校准检查

- Expected Calibration Error；
- Brier Score；
- reliability diagram；
- 按 source/entity_type/query_slice 分桶；
- 模型或索引升级后的漂移。

### 5.3 小样本校准

单源 Golden 尚小时：

1. 不把分数命名为 probability；
2. 使用 source/entity/task 分桶的 rank percentile；
3. 保留 exact deterministic tier；
4. 用 bootstrapped confidence interval；
5. calibration version 标 `provisional`；
6. 数据足够后再采用 isotonic/Platt/temperature scaling。

不允许通过手写“BM25×0.4 + cosine×0.6”制造跨源可比性。

## 6. 跨源候选效用

相关性不是最终排序的唯一标准。联合候选效用：

```text
utility(e, q) =
    P_relevance(e, q)
    × role_gain(e, unmet_roles)
    × source_authority(source, intent)
    × version_alignment(e, scope)
    × fact_status_factor(e)
    × freshness_factor(e, intent)
    + path_gain(e)
    - redundancy_penalty(e, selected)
    - risk_penalty(e)
```

其中：

- `role_gain`：候选是否补齐尚未满足的 required role；
- `source_authority`：来源对当前意图的权威度，不等于相关性；
- `version_alignment`：当前、目标历史版本或不适用；
- `fact_status_factor`：失败/反证不会被删除，而是进入 counter evidence 通道；
- `path_gain`：候选是否完成关键证据路径；
- `risk_penalty`：未确认关系、低质量解析、敏感信息等。

### 6.1 角色感知选择

使用约束式 greedy selection：

```python
selected = []
while budget_remains:
    candidate = argmax(marginal_utility(candidate, selected, unmet_roles))
    if candidate.utility < threshold:
        break
    selected.append(candidate)
    update(unmet_roles, token_budget, source_coverage)
```

这比“每个来源至少一条”的硬配额更合理：没有相关文档时，不应为了多样性塞入无关 Claim。

### 6.2 去重层次

依次去重：

1. Retrieval Unit；
2. Entity；
3. 同一事实的多来源表达；
4. parent-child 内容；
5. 历史版本与当前版本。

历史版本不能简单删除，而是进入 `version_differences` 或 `historical_context`。

### 6.3 事实共指与独立性

联合层需要区分：

- 同一实体的多个 Retrieval Unit；
- 同一事实在多个来源的转述；
- 不同来源但共同 root provenance；
- 真正独立的复现实验或观察。

示例：

```text
Document TableCell = 0.82
Notebook Output = 0.82
Experiment Metric = 0.82
```

若三者都绑定同一 Run，它们提供三种**呈现/定位证据**，但只是一份独立实验事实。选择器
可保留 Document 和 Notebook 作为 provenance path，却不能把 evidence_count 算成 3。

### 6.4 Counter-evidence 通道

失败、反证和错误版本不是简单低分：

- failed TestResult；
- failed/aborted Run；
- Notebook Error/StaleOutput；
- contradicted Claim；
- rejected relation；
- superseded Decision；
- blocked WorkItem。

它们在 relevance 合格时进入专门的 counter/risk channel。选择器对需要验证的 intent
设置 counter coverage，避免支持性证据占满全部预算。

## 7. 跨源证据图

### 7.1 关系分层

| 层 | 示例 | 使用方式 |
| --- | --- | --- |
| L0 确定性结构 | Run REPORTS Metric | 可直接参与路径 |
| L1 机器确认 | SCIP CALLS、真实退出码 Validation | 带 derivation 参与路径 |
| L2 人工确认 | Claim SUPPORTED_BY Run、Patch TARGETS Symbol | 高权重参与路径 |
| L3 未确认候选 | CANDIDATE_BINDING、LLM inferred | 只作为待复核或低权重线索 |
| L4 语义邻近 | SEMANTIC_SIMILAR | 只用于候选发现，不作为事实边 |

### 7.2 关键路径模板

#### 代码变更链

```text
CodexGoal
→ Episode
→ Patch
→ FileChange
→ CodeSymbol
→ Commit
→ ValidationResult
```

#### 实验结论链

```text
Claim
→ TableCell
→ MetricAggregation
→ Metric
→ Run
→ Commit / DatasetVersion / ConfigSnapshot
```

#### 版本失效链

```text
Claim
→ SupportingRun
→ ExperimentCommit
→ DiffHunk
→ ChangedSymbol
→ CurrentCommit
→ CurrentValidation or MissingRevalidation
```

#### 研发决策链

```text
ResearchIteration
→ UserGoal
→ Decision
→ Patch
→ CodeSymbol
→ TestResult
```

#### Notebook 结果链

```text
NotebookRevision
→ NotebookExecution
→ ParameterSnapshot
→ CodeCell
→ Output / Error
→ confirmed Metric / Artifact
→ ExperimentRun
```

#### 实验复现链

```text
ExperimentRun
→ ConfigSnapshot / EnvironmentSnapshot / DatasetVersion
→ Commit
→ CodeSymbol
→ CommandExecution or NotebookExecution
→ MetricObservation
```

#### 文档表格验证链

```text
AtomicClaim
→ TableCellFact
→ confirmed MetricDefinition/Observation
→ ExperimentRun
→ DatasetVersion / Commit
```

#### Workspace 证据覆盖链

```text
ResearchIteration
→ EvidenceRequirement
→ EvidenceLink
→ pinned ExternalEntityVersion
→ review/freshness/independence
```

#### 失败诊断链

```text
Workspace WorkItem
→ Codex Goal/Episode
→ CommandExecution
→ Error/TestFailure
→ CodeSymbol/Diff
→ retry Validation or unresolved
```

### 7.3 图扩展流程

1. 使用单源高置信候选作为 seeds；
2. 根据 Query Plan 选择路径模板；
3. 在 ACL 和版本 Scope 内执行有类型的 beam traversal；
4. 对每条路径计算路径分；
5. 只加入能补 required role、完成路径或提供反证的节点；
6. relation-only 节点进入 reranker；
7. Evidence Pack 保留完整 path IDs 和每条边 derivation。

`max_hops` 必须真实限制遍历，Trace 中记录：

- expanded nodes；
- traversed edges；
- pruned by ACL/version/type/confidence/budget；
- accepted paths；
- graph latency。

### 7.4 Edge Contract

每种 predicate 注册：

- allowed source/target types；
- owner domain；
- direction；
- derivation；
- evidence requirement；
- valid-time semantics；
- default review requirement；
- inverse predicate；
- traversal intents；
- cost。

例如：

```yaml
predicate: SUPPORTED_BY
source: AcceptedClaim
target: [ExperimentRun, MetricObservation, MetricAggregation]
owner: document
requires_review: true
allowed_derivation: [human_confirmed, deterministic_after_review]
inverse: SUPPORTS
traversal_intents: [claim_verification, staleness_check]
```

自由 predicate 不进入生产 traversal；只能作为未注册关系展示/待治理。

### 7.5 路径评分

```text
path_score =
  seed_relevance
  × min(edge_applicability)
  × geometric_mean(edge_confidence)
  × version_alignment
  × role_completion_gain
  × independence_factor
  × freshness
  - hop_penalty
  - unreviewed_penalty
  - cross_snapshot_penalty
```

结构性 deterministic edge 的 confidence 不代表端到端 Claim 为真；路径最终 authority 仍由
目标 role 和 source fact status 判断。

## 8. 多源重排

跨源重排不直接输入所有原文，而输入压缩 Evidence Card：

```text
[source/entity_type/status/version]
title
source-specific summary
matched roles
relation path summary
locator
```

候选数量建议：

- 来源内 union：每源 30–80；
- 来源 rerank：每源 15–30；
- 跨源 rerank：总计 30–60；
- 最终 Evidence Pack：8–20 个实体，按问题复杂度动态选择。

可以采用：

1. 第一版：规则 utility + 校准概率；
2. 第二版：cross-encoder pairwise/listwise reranker；
3. 第三版：从人工选择和答案引用中训练 preference/LTR 模型。

每次升级必须保留无 reranker、规则 reranker 和新模型三个消融组。

### 8.1 Evidence Card 必需字段

不同来源先渲染短 Card：

| 来源 | Card 中不可缺少 |
| --- | --- |
| Code | symbol/signature/path/commit/dirty/test binding |
| Codex | goal/event/outcome/order/observed status |
| Experiment | run/metric definition/value/unit/split/dataset/config/commit |
| Notebook | revision/execution/cell/output/parameters/stale state |
| Document | source span/section/table header path/claim status |
| Workspace | authoritative state/as-of/requirement/derived label |

### 8.2 Cross-source Reranker

输入：

- query/sub-question；
- Query Plan/required roles；
- Evidence Cards；
- candidate paths；
- conflicts；
- selected set summary。

输出：

- relevance；
- role applicability；
- relation/path usefulness；
- redundancy group；
- risk/counter relevance；
- explanation code。

模型不能直接输出 `verified`；truth status 由 verifier/policy 决定。

### 8.3 选择约束

- 每个 required role 满足 cardinality；
- counter-required intent 至少检查 counter channel；
- token/latency budget；
- source/entity duplicate；
- root provenance independence；
- ACL；
- target version；
- relation review；
- source availability。

若约束不可满足，选择器返回 missing role，而不是用低质量候选凑数。

## 9. 冲突与版本裁决

### 9.1 冲突类型

- 同一 Claim 被支持和反驳；
- Assistant 声称验证通过，但命令退出码失败；
- Patch 存在但没有 Commit；
- 文档引用旧 Commit；
- Run 使用不同 DatasetVersion；
- 相同指标名称但定义/方向不同；
- 当前代码与历史会话不一致；
- workspace 状态与真实来源事件不一致。

### 9.2 裁决顺序

```text
ACL eligibility
→ identity correctness
→ version alignment
→ fact status
→ deterministic/human-confirmed relation
→ source authority for intent
→ recency when intent requires current state
→ semantic relevance
```

“更新”不必然比“更旧”权威。例如用户问历史实现时，目标 Commit 的旧代码优先于当前代码。

### 9.3 不确定性输出

联合层输出：

- `supported`；
- `partially_supported`；
- `contradicted`；
- `potentially_stale`；
- `insufficient_evidence`；
- `source_unavailable`。

任何 `insufficient_evidence` 必须给出缺失角色和可执行补救：

- 同步某来源；
- 指定 commit；
- 补接实验；
- 确认候选关系；
- 执行复验。

### 9.4 Source Authority 不是固定全局权重

同一实体在不同问题上的权威性不同：

| 问题 | 高权威 | 辅助 |
| --- | --- | --- |
| 当前实现 | current Code/Test | Codex/Document |
| 当时为何修改 | Codex Goal/Decision + Workspace Decision | Diff/Code |
| 实验数值 | Experiment MetricObservation | Notebook/Document |
| Notebook 为什么失败 | Notebook Error/Execution | Codex/Code |
| 文档声称什么 | source-authored Document span | 其他来源 |
| 项目当前状态 | Workspace authoritative snapshot | source health |

authority 按 `(intent, role, source, entity_type, fact_status)` 配置并版本化，而不是每源一个
常量。

### 9.5 版本与时间适用性

统一输出：

- `exact`；
- `compatible`；
- `historical_target`；
- `mismatch`；
- `unknown`；
- `not_applicable`。

不同 domain 的 version 不强行转为一个字符串，而是通过 binding：

```text
Run.commit_sha ↔ Code Commit
NotebookExecution.run_id ↔ ExperimentRun
DocumentVersion reported_at ↔ Claim validation time
Workspace EvidenceLink.pinned_version ↔ External Entity Version
Codex Patch observed worktree ↔ Commit or dirty snapshot
```

## 10. Adaptive Context Budget

固定 Top-K 改成动态预算：

```text
simple query       2–5 evidence units
single source      5–10
multi source       8–15
multi-hop          12–20
```

停止条件：

- required roles 全部满足；
- 关键路径闭合；
- 新候选边际效用低于阈值；
- token/latency/cost 达上限；
- 发现明确不可回答条件。

同一个 Evidence Entity 的原文只出现一次，其他来源通过 path 引用，减少重复上下文。

### 10.1 Evidence Pack V2

```yaml
query:
  original:
  intent:
  scope:
  as_of:
plan:
  version:
  sub_questions:
  required_roles:
source_status:
  code: {status, watermark, latency, coverage}
  codex: ...
  experiment: ...
  notebook: ...
  document: ...
  workspace: ...
facts:
  verified: []
  reported: []
  observed: []
  inferred: []
counter_evidence: []
qualifiers: []
version_differences: []
paths: []
role_coverage:
  satisfied: []
  missing: []
  stale: []
  unauthorized: []
citations: {}
decision:
  status:
  answer_mode:
  reasons:
trace:
  query_id:
  generations:
  policy_versions:
```

### 10.2 Source-specific Rendering

- Code 保留签名、行号、调用/测试关系；
- Codex 按时间线压缩，ToolResult 仅按需展开；
- Experiment 用表格/键值，数值由确定性计算；
- Notebook 展示参数→依赖 Cell→目标 Cell→Output/Error；
- Document 展示作者原文、header path、caption/footnote；
- Workspace 将 authoritative snapshot 与 derived Intelligence 分栏。

### 10.3 Answer Mode

- `deterministic`：精确状态/数值/filter 可模板回答；
- `retrieval_only`：返回证据列表；
- `grounded_generation`：需要综合叙述；
- `partial`：部分源失败但可回答已满足部分；
- `refusal`：关键角色缺失/冲突无法裁决；
- `action_candidate`：只给建议，不执行 Workspace mutation。

## 11. Corrective / Iterative Retrieval

只有出现以下情况才进行第二轮：

- required role 未满足但对应来源可用；
- 冲突需要查版本或验证事实；
- 首轮只召回父摘要，缺少原文；
- graph path 缺一个中间节点；
- reranker 判断高分候选仍不足以回答。

第二轮 query 来自显式缺口：

```text
missing role + known entity IDs + scope
```

而不是让 LLM 无限制“再搜索一下”。最多两轮，超过后拒答。

### 11.1 缺口驱动模板

| 缺口 | Corrective Query |
| --- | --- |
| metric definition 缺失 | metric name + run + dataset/split exact |
| commit binding 缺失 | run/codex patch external ID + repository |
| current validation 缺失 | changed symbol + current commit + tests |
| Notebook producer 缺失 | output/error ID + execution order/def-use |
| Claim source span 缺失 | document/version + claim fingerprint |
| Workspace evidence role 缺失 | requirement + allowed domains + freshness |
| conflict | exact IDs + compared versions + counter channel |

### 11.2 循环停止

- 相同 query/signature 不重复；
- 新增 role coverage=0 时停止；
- 超过 2 轮停止；
- cost/latency budget 超限停止；
- source unavailable 不重复请求；
- ACL denied 不尝试绕过；
- unresolved relation 需要人工 review 时停止并给 action。

## 12. 联合层接口与模块建议

为了不与现有 `retrieval.py` 冲突，新增：

```text
src/evidence_rag/rag/
  contracts.py
  planner.py
  orchestrator.py
  calibration.py
  fusion.py
  graph_retriever.py
  reranker.py
  evidence_verifier.py
  context_packer.py
  telemetry.py
  sources/
    code.py
    codex.py
    experiment.py
    notebook.py
    document.py
    workspace.py
```

迁移方式：

- 现有 `HybridRetriever` 包装成 `CodeSourceRetrieverV1`；
- 现有 `CodexHybridRetriever` 包装成 `CodexSourceRetrieverV1`；
- `PlatformStore.structured_search` 逐步拆成 Experiment、Notebook、Document、Workspace
  四个 Retriever；
- 现有 `PlatformService.search` 在 Feature Flag 下切换到 `RagOrchestrator`；
- 现有 `UnifiedQueryService._pack` 逐步下沉到 `EvidenceVerifier + ContextPacker`。

建议 Feature Flags：

```text
RAG_ENGINE_VERSION=v1|v2
RAG_CODE_RETRIEVER=v1|ast-graph-v2
RAG_FUSION=diversified-v2|calibrated-role-v3
RAG_RERANKER=off|local|remote
RAG_CORRECTIVE_RETRIEVAL=false|true
```

### 12.1 核心接口

```python
class SourceRetriever(Protocol):
    def capabilities(self) -> SourceCapabilities: ...
    def retrieve(
        self,
        sub_query: PlannedSubQuery,
        budget: RetrievalBudget,
        consistency: ConsistencyWatermark,
    ) -> SourceRetrievalResult: ...

class EvidenceGraph(Protocol):
    def expand(
        self,
        seeds: list[RetrievalCandidate],
        templates: list[PathTemplate],
        constraints: TraversalConstraints,
    ) -> GraphExpansionResult: ...
```

SourceRetriever 不返回最终答案；Orchestrator 不读取来源私有表。

### 12.2 Feature Flag 依赖

```text
rag_v2_contracts
→ per-source-v2 flags
→ planner_v2
→ calibration_v2
→ role_fusion_v2
→ evidence_graph_v2
→ verifier_v2
→ context_v2
```

任何下游 flag 开启前验证依赖；请求级 fallback 到上一稳定组合。

### 12.3 Trace

至少记录：

- resolved scope/ACL/as-of；
- capability registry snapshot；
- plan/sub-queries/roles；
- per-source/channel budget、latency、status、watermark；
- raw ranks，不记录敏感全文；
- calibration version；
- dedup/reject reason；
- traversal edges/pruning；
- selected marginal utility；
- conflicts/role coverage；
- corrective rounds；
- context token allocation；
- final answer mode/citations；
- cost/cache/fallback。

Trace 遵守 ACL 与敏感数据策略，不能成为旁路泄漏。

## 13. 多源评测集

至少 60 条跨源 Golden Query，建议切片：

| 类型 | 数量 |
| --- | ---: |
| Code + Codex | 8 |
| Experiment + Document | 8 |
| Experiment + Notebook | 7 |
| Code + Experiment | 6 |
| Notebook + Code/Codex | 5 |
| Workspace + 任意证据源 | 6 |
| 三源证据链 | 6 |
| 四至六源证据链 | 4 |
| 版本冲突/过期 | 4 |
| 不可回答/来源缺失/超时 | 3 |
| ACL/敏感信息 | 3 |

每条 Query 标注：

- required sources；
- required roles；
- required entity IDs；
- acceptable alternatives；
- required paths；
- target version；
- forbidden entities；
- expected decision；
- claim-level citations。

### 13.1 系统性故障注入

- 一个 Retriever timeout；
- 一个 Retriever 返回 partial；
- stale calibration；
- wrong index generation；
- cross-source snapshot mismatch；
- edge review status 降级；
- duplicate root provenance；
- reranker unavailable；
- graph path cycle；
- ACL denied middle node；
- corrective retrieval zero gain；
- Notebook 被 V1 `experiment` route 遗漏。

### 13.2 联合层指标

Routing：

- Intent Accuracy；
- Source Route Precision/Recall/Macro-F1；
- Sub-question Sufficiency；
- Planner Schema/Capability Violation；
- average activated domains。

Retrieval/Fusion：

- Required Role Coverage；
- Cross-source Entity Recall@20；
- nDCG；
- Evidence Path Recall/Precision；
- Counter-evidence Recall；
- Root-provenance Diversity；
- Wrong-version Rate；
- Duplicate Context Rate。

Calibration：

- ECE；
- Brier；
- reliability per domain/task/entity；
- calibration drift。

Answer：

- Citation Precision/Completeness；
- Claim Support/Entailment；
- Conflict Recall；
- Missing Role Accuracy；
- Unanswerable Acceptable Rate；
- Partial-answer Honesty；
- Unsupported Claim Rate。

System：

- per-source/end-to-end P50/P95/P99；
- timeout/partial/fallback；
- tokens/cost/cache hit；
- index generation mismatch；
- Unauthorized Evidence Leakage；
- trace completeness。

### 13.3 消融

| Run | 变化 |
| --- | --- |
| M-B0 | 当前 unified structured/diversified search |
| M-B1 | 六个 source retrievers，仅 rank fusion |
| M-B2 | per-source calibration |
| M-B3 | intent/source routing |
| M-B4 | required-role selection |
| M-B5 | typed evidence graph |
| M-B6 | version/conflict verifier |
| M-B7 | source-specific context |
| M-B8 | corrective retrieval |
| M-B9 | cross-source reranker |

## 14. 多源退出标准

| 指标 | 目标 |
| --- | ---: |
| Source Routing Macro-F1 | ≥ 0.90 |
| Required Role Coverage | ≥ 0.90 |
| Cross-source Entity Recall@20 | ≥ 0.85 |
| Evidence Path Recall | ≥ 0.80 |
| Counter-evidence Recall | ≥ 0.90 |
| Version Accuracy | ≥ 0.97 |
| Wrong-version Rate | ≤ 0.02 |
| Conflict Recall | ≥ 0.90 |
| Citation Precision | ≥ 0.95 |
| Citation Completeness | ≥ 0.95 |
| Unanswerable Acceptable Rate | ≥ 0.90 |
| Unauthorized Evidence Leakage | 0 |
| P95 联合检索（当前规模、无生成） | ≤ 3 s |
| Planner Capability Violation | 0 |
| Source timeout 误报为无证据 | 0 |

这些数值是设计门槛，首次 Golden Set 建成后可评审调整；任何调整必须保留原始基线。

### 14.1 上线顺序

1. offline replay；
2. shadow plan/retrieval；
3. compare EvidencePack，不影响答案；
4. internal canary；
5. low-risk intents；
6. multi-hop intents；
7. default V2；
8. V1 fallback 保留一个稳定周期。

每个阶段按 domain、intent、language、complexity、ACL slice 审核。

### 14.2 联合层完成定义

- 六个源已通过单源门槛；
- Notebook 是独立 retrieval domain；
- capability registry 与 V1 compatibility 生效；
- Planner/roles/paths 可重放；
- raw score 不跨源比较；
- relation ontology/version/ACL gating；
- root provenance independence；
- source timeout/partial/unauthorized 语义正确；
- counter evidence 不被过滤；
- EvidencePackV2 完整；
- 60 条 Golden/故障注入/消融；
- shadow/canary/rollback；
- 面试文档只使用真实 Run。

## 15. 可讲的核心设计

> 我没有把异构来源的向量分数直接相加，而是先在代码、会话、实验、Notebook、文档和
> 工作区内部
> 完成召回与重排，再把结果校准为来源内相关概率。联合层根据查询意图、required roles、
> 版本和来源权威度计算边际效用，并通过有类型的跨源证据图补全 Claim→Run→Commit→
> Code→Validation 路径。最终生成层只消费经过冲突和版本裁决的 Evidence Pack。

最有区分度的五点：

1. **六源各自最优，而非统一向量池**；
2. **Capability-aware Planner + Required-role constrained fusion**；
3. **相关性、权威性、关系置信、事实状态、版本适用性五维分离**；
4. **Typed Evidence Graph + Root-provenance independence + Counter channel**；
5. **Consistency watermark、Corrective retrieval 和诚实 partial/refusal**。
