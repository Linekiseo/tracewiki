# 整体 RAG 打磨与生产化设计

状态：`REPOSITORY_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD`

> **2026-07-30 实现覆盖层**
>
> 本文正文仍是 L4/L5 生产化退出门。仓库内已完成显式 V2 的 trusted query
> interaction、clarification/follow-up、两波与最多两轮 corrective retrieval、typed
> relation、claim/citation fail-closed、retrieval-only/partial/refusal、secret/ACL/path
> 防护、请求级 V1 fallback，以及前端可信 `/v1/query` 主链。没有 reviewed production
> replay、真实模型/ANN 硬件 SLO、shadow/canary 和回滚演练，因而“仓库 L3 工程完成”
> 不等于本文件定义的“整体 RAG 生产完成”。

## 1. 阶段定位

整体打磨必须发生在单源召回和多源融合达标之后。否则调整 Prompt、换更大的 LLM 或增加
上下文窗口，只会掩盖检索错误。

本阶段覆盖：

- Query 理解；
- Evidence Pack 与上下文；
- Grounded Generation；
- 生成后校验；
- 评测；
- 可观测性；
- 索引与性能；
- 安全；
- 发布与回滚。

## 2. Query 理解

### 2.1 规则优先、模型增强

保留现有 9 类 Intent 作为稳定契约，在其上补充：

- multi-label source route；
- query complexity；
- time/version expression；
- identifiers/path/commit/run/metric extraction；
- required roles；
- sub-question decomposition；
- answer format；
- risk level。

第一版：

```text
显式 API 参数 > 确定性实体/版本解析 > 规则意图 > 小模型分类 > LLM 规划
```

越靠左权威度越高。LLM 不得覆盖用户显式指定的 commit、repository、document 或 ACL。

### 2.2 Query Rewrite

保留原问题，同时生成来源专用 rewrite：

| 来源 | Rewrite 重点 |
| --- | --- |
| Code | identifier、error、API、自然语言→代码概念 |
| Codex | goal、action、file、command、validation |
| Experiment | metric、operator、dataset、config、commit |
| Notebook | revision、cell type、parameter、output/error、execution/dataflow |
| Document | claim、section、table/figure、citation |
| Workspace | topic、iteration、status、owner、due、as-of、work item |

每个 rewrite 记录来源和规则/模型版本。Rewrite 不能删除否定词、版本和比较方向。

### 2.3 Query Decomposition

只在复杂问题中启用。每个子问题必须声明：

- 可回答的来源；
- required roles；
- 与其他子问题的依赖；
- 结果合并方式。

避免把一个多源问题拆成多个互不关联的语义搜索；子问题结果最终仍要通过实体关系和
版本 Scope 对齐。

## 3. Evidence Pack v2

### 3.1 数据结构

```python
class EvidencePackV2:
    query_plan: QueryPlan
    decision: str
    confidence: float

    verified_facts: list[EvidenceFact]
    supporting_evidence: list[EvidenceCard]
    counter_evidence: list[EvidenceCard]
    version_differences: list[VersionDifference]
    unresolved_inferences: list[RelationPath]
    missing_evidence: list[EvidenceGap]

    relation_paths: list[RelationPath]
    citation_map: dict[str, Citation]
    coverage: RoleCoverage
    source_health: dict[str, SourceStatus]
    trace_ref: str
```

### 3.2 Evidence Fact

Evidence Pack 不只传 snippet，而是先抽取可引用的最小事实：

```text
fact_id
statement
status
source_entity_ids
citation_ids
version
valid_time
derivation
```

数值事实必须包含：

- value；
- unit；
- metric definition；
- aggregation；
- dataset/version；
- run。

代码事实必须包含：

- repository；
- commit/worktree；
- symbol；
- file/lines；
- static/dynamic derivation。

### 3.3 事实合并

多个来源表达同一事实时：

1. 基于稳定实体和规范化字段合并；
2. 保留所有 Citation；
3. 标记来源一致/冲突；
4. 不用 LLM 摘要覆盖原始证据；
5. 对数值使用确定性比较。

## 4. Context Packer

### 4.1 检索上下文与理解上下文分离

检索使用小而有区分度的 Unit；生成使用命中后的父结构、关系和必要原文。这个“late
enrichment”适用于：

- AST block → Symbol/Class/File；
- Claim → Section；
- TableCell → Header/Caption/Footnote；
- Codex Item → Episode；
- Metric → Run/Config/Dataset；
- Notebook Output/Error → producer Cell/Execution/Parameter/Dependency；
- Document TableCellFact → HeaderPath/Caption/Footnote；
- Workspace WorkItem → Dependency/Acceptance/EvidenceRequirement。

### 4.2 Token 预算

预算由角色和边际效用分配，不按来源平均：

```text
system/query/plan             10%
verified facts               20%
required primary evidence    40%
counter/version evidence     15%
relation/path explanation    10%
output reserve                5%
```

这是初始比例。实际以模型上下文和 Golden Set 消融为准。

### 4.3 排序

上下文顺序：

1. 问题和 Scope；
2. 结论所需 verified facts；
3. 每个事实紧邻其原始 Citation；
4. 反证与版本警告；
5. 辅助背景；
6. 明确缺口和禁止推断项。

避免把最关键证据放在超长上下文中部。重复 parent/child 只保留一次。

### 4.4 压缩

压缩优先级：

1. 删除重复；
2. 删除不补角色、不补路径的候选；
3. ToolResult/日志只保留相关窗口；
4. 代码保留签名与命中 AST block；
5. 表格保留相关行、表头、单位和 footnote；
6. 必要时生成可回溯摘要，摘要必须引用原实体。

## 5. Grounded Generation

### 5.1 生成约束

生成模型收到：

- Query Plan；
- decision；
- verified facts；
- counter evidence；
- missing evidence；
- citation map；
- 输出格式。

模型必须：

- 每个外部事实带 Citation ID；
- 区分事实和推断；
- 不将未确认关系写成确定事实；
- 不跨版本合并代码；
- 不在证据不足时给出确定结论；
- 数值只使用 Evidence Fact 中的规范化值。

### 5.2 生成模式

| 模式 | 使用场景 |
| --- | --- |
| deterministic | 精确位置、状态、数值列表 |
| retrieval_only | 无 LLM 或用户只需要证据 |
| grounded_summary | 有完整角色的总结 |
| compare | 多版本/Run/会话对比 |
| refusal_with_gaps | required roles 缺失 |

精确 SQL/代码位置问题优先 deterministic response，避免不必要的生成费用和幻觉。

### 5.3 生成后校验

分三层：

1. **Citation Syntax**：Citation ID 存在且可访问。
2. **Claim-Citation Entailment**：每个事实是否被引用片段支撑。
3. **Scope/Version Check**：引用是否属于目标版本和权限范围。

校验失败：

- 删除未支撑句并重写一次；
- 仍失败则返回 retrieval-only Evidence Pack；
- Trace 记录失败类型。

## 6. Corrective RAG 与拒答

### 6.1 Corrective Trigger

- Top candidates 相关概率低；
- required roles 不全；
- Citation entailment 失败；
- 版本冲突；
- 关键关系只有未确认候选；
- source health 为 partial；
- answerability classifier 判断资料不足。

### 6.2 Corrective Actions

按缺口选择：

- 改写来源查询；
- 增加 exact/lexical channel；
- 扩展父上下文；
- 沿目标关系补一跳；
- 切换到历史 Scope；
- 请求同步数据源；
- 请求人工确认关系；
- 建议运行测试/实验。

最多两轮。第二轮仍无法满足 required roles 时明确拒答。

### 6.3 不可回答分类

- knowledge not present；
- wrong scope/version；
- source not indexed；
- source unavailable；
- unauthorized；
- conflicting evidence；
- required validation missing；
- query underspecified。

评测必须覆盖每一类，而不是只测可回答问题。

## 7. Embedding、Reranker 与模型治理

### 7.1 抽象接口

不把具体供应商写死在领域代码中：

```python
EmbeddingProvider.embed(units, model_profile)
SparseEncoder.encode(units, profile)
Reranker.rank(query, candidates, profile)
Generator.generate(evidence_pack, profile)
Judge.evaluate(sample, profile)
```

Model Profile 包含：

- provider/model/revision；
- dimensions；
- normalized；
- max tokens；
- language/domain；
- prompt/template version；
- price/latency；
- data policy。

### 7.2 模型选择流程

1. 使用项目 Golden Set 比较；
2. 分中文、代码、长文档、表格查询切片；
3. 同时测 Recall、rerank gain、P95、成本；
4. 检查 embedding 维度和索引体积；
5. 保留本地离线 fallback；
6. 新模型使用双写索引和 shadow query；
7. 达标后切换 active model generation。

不依据公开榜单直接决定生产模型。

### 7.3 首轮候选矩阵

以下是需要进入 benchmark 的候选，不是预先指定的最终模型：

| Profile | 候选 | 用途 | 主要取舍 |
| --- | --- | --- | --- |
| deterministic fallback | `local-hash-v2` | 离线回退、词项特征基线 | 速度快、可解释，但语义泛化弱 |
| multilingual bi-encoder | Qwen3-Embedding 0.6B/4B | 中文、英文、跨语言、一般代码文本 | 本地部署规模可选，需要测领域数据 |
| multilingual hybrid | BGE-M3 | dense/sparse/multi-vector 消融 | 能统一实验多种检索表示，索引更复杂 |
| code-specific | Jina Code Embeddings 0.5B/1.5B | 自然语言→代码、代码→代码 | 代码任务针对性强，需验证中文查询 |
| late interaction | ColBERTv2/Jina-ColBERT-v2 | bi-encoder 召回后的高精检索实验 | 质量潜力高，存储和查询成本更高 |
| reranker | Qwen3-Reranker 0.6B/4B 或同级 cross-encoder | 来源内及跨源 Evidence Card 重排 | 改善精排，但增加 P95 和算力 |

首轮建议把 Qwen3-Embedding 0.6B + Qwen3-Reranker 0.6B 作为可本地复现的通用强基线，
把 BGE-M3 作为多表示基线，把 code-specific 模型作为 Code slice 候选。最终选择必须分别
报告中文、英文标识符、代码、文档和跨源切片，不能用一个平均分掩盖来源退化。

## 8. 向量索引与存储

### 8.1 当前阶段

当前 1,598 个活跃代码视图和 6,178 个活跃 Codex 视图仍可使用 SQLite 做正确性基线。
但神经 embedding 引入后应抽象 `VectorIndex`：

```text
upsert(namespace, generation, vectors, metadata)
search(query_vector, filters, k)
delete_generation(generation)
activate_generation(generation)
```

### 8.2 ANN 引入条件

满足任一条件进入 ANN 评审：

- 单项目 active vector > 100k；
- dense search P95 > 500 ms；
- 多租户并发导致数据库锁或 CPU 不可接受；
- embedding 维度和多表示使全扫描成本超过 SLO。

选型重点：

- metadata filter 是否前置；
- generation 原子切换；
- delete/tombstone；
- exact fallback；
- 多租户隔离；
- 本地部署与备份；
- 索引构建时间。

SQLite 仍保留实体、关系、版本和审计主存储；向量库只做派生索引。

### 8.3 增量索引

内容寻址缓存键：

```text
content_hash
+ retrieval_unit_builder_version
+ embedding_model_revision
+ normalization_version
```

只有键变化的 Unit 重新 embedding。新 Generation 构建完整可见性后原子激活，旧索引延迟
回收。

## 9. 缓存

分层缓存：

| 缓存 | Key | 失效 |
| --- | --- | --- |
| Query Plan | normalized query + scope + planner version | scope/model 变化 |
| Query Embedding | query + model revision | model 变化 |
| Source Retrieval | query + scope + active generations + retriever version | generation 变化 |
| Rerank | query + candidate IDs + model | candidate/model 变化 |
| Context Pack | plan + evidence IDs + packer version | evidence/version 变化 |
| Generation | evidence pack hash + model + prompt | 任一输入变化 |

ACL 相关缓存必须将授权主体或等价权限集合纳入 Key，禁止跨用户复用未过滤结果。

## 10. 可观测性

### 10.1 Trace Span

```text
QUERY_RECEIVED
SCOPE_RESOLUTION
QUERY_PLANNING
SOURCE_ROUTE
SOURCE_RETRIEVAL/{source}/{channel}
SOURCE_RERANK/{source}
SCORE_CALIBRATION
GRAPH_EXPANSION
CROSS_SOURCE_FUSION
EVIDENCE_VERIFICATION
CONTEXT_PACKING
GENERATION
CITATION_VERIFICATION
RESPONSE
```

每个 Span 记录：

- trace/query ID；
- model/index/prompt version；
- candidate count；
- filtered count and reason；
- latency；
- token/cost；
- cache hit；
- errors；
- selected IDs；
- ACL decision summary。

### 10.2 Dashboard

至少展示：

- 每来源 Recall proxy/zero-result rate；
- source routing distribution；
- reranker gain；
- required role coverage；
- corrective retrieval rate；
- refusal rate；
- citation verification failure；
- wrong-version and conflict rate；
- P50/P95/P99；
- embedding/rerank/generation cost；
- active generation freshness；
- evaluation trend。

## 11. 评测体系

### 11.1 四层评测

| 层 | 指标 |
| --- | --- |
| Retrieval Unit | Recall@K、MRR、nDCG、hard-negative error |
| Evidence Graph | path recall、role coverage、version accuracy、conflict recall |
| Context | context precision、context recall、duplicate/distractor ratio、token efficiency |
| Answer | claim faithfulness、citation precision/completeness、answer correctness、拒答质量 |

传统 MRR/nDCG 仍用于诊断，但不能单独代表生成效用；增加 distractor/harmful context 标注。

### 11.2 数据集组成

正式 Release Gate 建议：

- Code 50；
- Codex 45；
- Experiment 45；
- Notebook 40；
- Document 50；
- Workspace 40；
- 多源 60；
- unanswerable、ACL/security、conflict/version 和故障注入作为上述集合的必备切片，
  不必另行重复计数；
- 可用同一 fixture 生成多个 query，但每条 query 的标注和评测目标独立。

每次 PR 跑小型 smoke set；每日或发布前跑全量。

### 11.3 Judge 使用

- 关键身份、版本、路径、数值使用确定性评测；
- 语义完整性和 faithfulness 可使用 LLM Judge；
- Judge 必须版本化；
- 抽样人工校准；
- 至少两个不同机制的指标交叉验证；
- 不用 Judge 评判它无法访问或解析的原始证据。

## 12. 安全与治理

### 12.1 检索前 ACL

ACL 必须在以下操作之前应用：

- lexical/dense candidate 返回；
- graph traversal；
- relation neighbor resolve；
- cache reuse；
- reranker；
- LLM context。

禁止“先检索全部，再在 UI 隐藏”。

### 12.2 Prompt Injection

所有来源内容按不可信数据处理：

- 原文与系统指令结构隔离；
- 文档/代码中的指令不提升权限；
- Tool/URL 操作不由 RAG 内容自动触发；
- 对可疑指令片段标记；
- 输出引用不执行；
- Golden Set 加入 prompt injection 文档与代码注释。

### 12.3 数据泄漏

- Secret scan 在 Raw→Derived 前后均执行；
- embedding 前脱敏；
- 日志不记录原始高敏上下文；
- remote reranker/generator 按 data policy 路由；
- unauthorized/secret leakage 必须为 0 才能发布。

## 13. 性能 SLO

当前规模的建议目标：

| 场景 | P50 | P95 |
| --- | ---: | ---: |
| 单源精确查询 | 200 ms | 600 ms |
| 单源 hybrid + rerank | 600 ms | 1.5 s |
| 多源检索，无生成 | 1.2 s | 3 s |
| 一轮生成 | 视模型建立基线 | 视模型建立基线 |
| Corrective 两轮 | 不设固定低值 | ≤ 单轮预算的 2.2 倍 |

SLO 必须同时记录数据规模、硬件、并发、模型和 cache 状态。

## 14. 发布策略

### 14.1 Shadow

V2 与 V1 同时处理真实查询，但只返回 V1。记录：

- Top-K overlap；
- Golden/online implicit relevance；
- role/path coverage；
- latency/cost；
- V2 新增/丢失证据。

### 14.2 Canary

按项目或 actor 小流量启用，支持请求级 fallback。

### 14.3 回滚

必须可独立回滚：

- planner；
- source retriever；
- embedding generation；
- reranker；
- fusion；
- context packer；
- generator prompt。

不做需要回滚整个数据库的“大爆炸”升级。

## 15. 完成定义

整体 RAG 只有同时满足以下条件才完成：

1. 单源和跨源 Golden Set 达标；
2. 关键回答具有 claim-level citation；
3. 版本与 ACL 错误为零或满足严格门槛；
4. 不可回答场景能正确拒答；
5. Trace 能定位每个候选为何进入/被过滤；
6. P95 和成本在 SLO 内；
7. 模型、索引、Planner、Prompt 均版本化；
8. 可以请求级回滚 V1；
9. 面试材料中的每个指标都有 Evaluation Run 或 Trace 证据。
