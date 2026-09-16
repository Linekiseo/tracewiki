# 六源协同 Evidence RAG 面试与简历材料

对应设计：[02_MULTI_SOURCE_FUSION_RAG_DESIGN.md](../02_MULTI_SOURCE_FUSION_RAG_DESIGN.md)

## 1. 当前状态声明

### CURRENT_MEASURED

下表是设计阶段留下的**历史观测**，本轮没有读取
`EXTERNAL_MUTABLE_SERVICE_OWNED` 正式库，也不声称这些数量仍代表当前线上状态：

| 来源 | 历史活跃数据 | 当时检索状态 |
| --- | --- | --- |
| Code | 1 repo、192 FileVersion、1,406 CodeSymbol、4,712 graph edges | 已有多路基础，但 graph/版本/未解析关系仍需增强 |
| Codex | 5 Thread、127 Turn、6,178 Item/View | Item 噪声高，Episode/时序验证需增强 |
| Experiment | 0 Experiment/Run/Metric/Artifact | adapter/schema 存在，无可测正式数据 |
| Notebook | 0 Template/Run/Cell/Output | parser/schema 存在，无可测正式数据 |
| Document | 4 文档、279 Section、48 Table、1,270 Cell、32 reported Claim | 当前只搜 Document/Claim/Table substring |
| Workspace | 1 Project、2 Topic、0 Iteration、1 cancelled WorkItem、0 Link | 当前只搜 Topic/Iteration |

### CURRENT_IMPLEMENTED

- 六个来源各自的 typed contract、隔离持久层、专用检索/重排、context、ACL/version
  与 release evaluator；默认仍为 V1，新增主路只允许显式 opt-in；
- 六源 capability/watermark registry、确定性 Query Planner、sub-question、required
  roles、source budget 与 path template；
- exact 60-case、11-slice、content-addressed 跨源 Golden 和冻结 denominator；
- 来源内 reviewed calibration（ECE/Brier）、role-aware fusion、root provenance dedup、
  counter evidence 与七态 source status；
- typed evidence graph、≤4 hop bounded beam、ACL/version/review/cycle/budget gate，以及
  最多两轮缺口驱动 corrective retrieval；
- retrieval/comprehension 分层 Evidence Pack、claim-citation verifier、grounded
  refusal、portable evaluation bundle；
- exact generation baseline、embedding/cache contract、P50/P95/P99 dashboard、
  可重算 ANN benchmark/admission；
- 六源 72-case security matrix、全内容多轮 decode 扫描、八阶段 no-skip release
  evaluator 与固定 rollback rehearsal。

### TARGET_DESIGNED

上述原设计的仓库内工程合同已经实现。剩余目标不是再补一个同名骨架，而是用真实受控
production 数据和外部服务执行 replay、benchmark、shadow 与 canary，从而决定是否具备
质量资格和默认 V2 发布条件。

### NOT_YET_PROVEN

- 六个单源虽然已有各自 Golden/evaluator/隔离 artifact 或明确失败证据，但现有真实质量
  结论仍为 `HOLD / NON_QUALIFIED / UNAVAILABLE`，不能写成已发布；
- 60-case Golden 与离线 evaluator 已建立，但尚无真实六源 production replay，因此
  routing/role/path/citation 等联合指标仍为 `UNAVAILABLE / NOT_QUALIFIED`；
- calibration 与 ANN 都只有可重算工程 harness，没有远程模型、真实 backend、硬件和
  production traffic 的 qualification 数据；
- 联合 P95、成本、ACL 故障注入、shadow/canary 和默认 V2 均未形成 production Run；
- 所有质量门槛仍是发布条件，不是当前成绩；系统继续 `DEFAULT_V1 /
  HOLD_DEFAULT_V1`。

## 2. 15 秒定位

> 这是面向研发证据的六源 RAG：Code、Codex、Experiment、Notebook、Document 和
> Workspace 各自使用最适合的检索方式；联合层不相加原始分数，而是按查询计划、证据角色、
> 版本、权威性和事实状态选择证据，再沿 typed graph 补全 Claim→Run→Commit→Code→
> Validation 路径，最终只让模型消费经过冲突裁决的 Evidence Pack。

## 3. 30 秒版本

> 普通 RAG 把所有内容切成文本块放进一个向量库，但研发数据差异很大：代码关心 Symbol
> 和 commit，实验关心 metric definition、unit、split 与可比性，Notebook 关心 Cell
> 执行顺序和 stale output，文档关心 Claim 和表格行列，Workspace 关心当前状态和证据缺口。
> 我先把六个源分别做到可独立评测，再用 capability-aware Planner 路由子问题；每个源
> 先内部融合和校准，联合层通过 required roles 与证据图做约束选择，并把支持、反证、
> 版本差异、缺失角色和来源状态封装成 EvidencePackV2。

## 4. 2 分钟版本

> 当前项目不是从零开始，已经有稳定实体、原始对象、Generation、ACL，以及六源各自的
> typed retrieval、隔离存储、Golden 与评测合同。早期版本曾存在结构化来源共享
> substring、Notebook 并入 Experiment API、异构分数直接混用、graph 只做结果扩展等
> 问题；仓库内 V2 工程已经针对这些问题实现了独立来源域、校准融合、typed graph 和
> Evidence Pack，但尚未用 production replay 证明质量资格。
>
> 我把演进分三步。第一步是六个单源最优：Code 做 AST/typed graph，Codex 做 goal-aware
> temporal Episode，Experiment 做 deterministic structured/numeric，Notebook 做
> revision/execution/dataflow，Document 做 paragraph/claim/table typed retrieval，
> Workspace 做 structured/temporal control-plane retrieval。
>
> 第二步是联合层。Planner 根据 intent、scope、ACL 和 capability registry 生成
> sub-question、required roles、source budget、path templates 和 stop conditions。每个
> Retriever 返回校准后的相关性、版本、水位和状态；Fusion 最大化尚未满足角色的边际效用，
> 同时单独考虑 source authority、fact status、version alignment、root provenance 和
> counter evidence。Graph 只走注册过的 predicate，并受 max hops、ACL、版本、review 和
> budget 约束。
>
> 第三步是 EvidencePackV2 和生产闭环。支持、反证、qualifier、版本差异、缺失角色、
> source timeout/unauthorized 分开；必要时只针对缺口 corrective retrieval 两轮。生成模型
> 不能把候选关系写成已验证事实。评测则从六个单源 Golden 到 60 条跨源 Query，再做
> timeout、stale generation、ACL middle-node、duplicate provenance 等故障注入和消融。

## 5. 白板图

```text
Question + Scope + ACL + As-of
                ↓
     Capability-aware Query Planner
                ↓
┌──────┬───────┬──────────┬──────────┬──────────┬───────────┐
│ Code │ Codex │Experiment│ Notebook │ Document │ Workspace │
│AST/  │Time/  │Filter/   │Cell/Exec/│Claim/    │State/Time/│
│Graph │Goal   │Numeric   │Dataflow  │Table     │Dependency │
└──────┴───────┴──────────┴──────────┴──────────┴───────────┘
                ↓ per-source rerank + calibration
        Required-role Constrained Fusion
                ↓
 Typed Evidence Graph + Version/Conflict Verifier
                ↓
 EvidencePackV2
 facts | counter | qualifier | missing | paths | citations
                ↓
 deterministic / grounded / partial / refusal / action-candidate
```

## 6. 六源为什么必须分开

| 来源 | 不能只做文本向量的原因 |
| --- | --- |
| Code | 标识符、作用域、调用/引用、commit/dirty state 决定正确性 |
| Codex | 相同文本在 Goal、Patch、Command、失败结果中语义完全不同 |
| Experiment | 数值相同但 metric/unit/split/step/aggregation 可不同 |
| Notebook | display order 与 execution order 不同，output 可能 stale |
| Document | TableCell 要保留 header path，Claim 要保留 qualifier/source span |
| Workspace | running/owner/due/as-of 是结构化状态，不是相似度 |

## 7. 最核心的五个联合设计

### 7.1 Capability-aware Planner

每个 SourceRetriever 注册：

- tasks；
- channels；
- entity types；
- filters；
- time/version semantics；
- numeric/graph/as-of 能力；
- calibration/index/health。

Planner 只能使用已注册能力，输出 schema-valid JSON，可 deterministic fallback 和重放。

### 7.2 分数语义分离

```text
retrieval relevance
≠ source authority
≠ relation confidence
≠ fact status
≠ version alignment
```

相关但旧版的文档可以高 relevance、低 current applicability；失败测试可以是高相关
counter evidence；未复核关系可以高语义相关但不能成为 verified fact。

### 7.3 Required-role Constrained Fusion

不是每源各取两条，而是根据未满足角色计算边际效用：

```text
utility =
  calibrated relevance
  × role gain
  × authority
  × version alignment
  × freshness
  + path gain
  - redundancy/risk
```

缺角色就输出 missing，不用无关来源凑多样性。

### 7.4 Typed Evidence Graph

- predicate 有 endpoint schema；
- relation 有 owner/derivation/review/valid time；
- traversal 按 intent 注册；
- ACL/version 前置；
- typed beam search；
- path 进入 Trace；
- semantic similar edge 只做发现，不做事实。

### 7.5 Root-provenance Independence + Counter Channel

Document Table、Notebook Output、Experiment Metric 若都来自同一 Run，只算一份独立实验
事实。失败 Run、TestFailure、Notebook Error、contradicted Claim 不因低状态分被过滤，而
进入 counter/risk channel。

## 8. 一个完整案例

问题：

> 报告中“新 reranker 提升 8%”在当前代码下仍成立吗？

### Plan

1. Document：找 atomic Claim、source span、TableCell、qualifier；
2. Experiment：找 MetricDefinition/Observation、Run、DatasetVersion、Config、Commit；
3. Notebook：若绑定，找参数、产生该值的 Cell/Output、stale state；
4. Code：找 experiment commit 和 current commit 间相关 Symbol/Diff/Test；
5. Codex：找当时 Goal/Patch/Validation；
6. Workspace：找本轮 EvidenceRequirement、Decision、current status。

### Required Roles

```text
reported_claim
source_location
metric_definition
metric_observation
comparable_run
dataset_version
experiment_commit
current_code
diff_or_revalidation
counter_check
```

### 可能结果

- 所有角色满足、版本无影响：supported；
- 数值成立但 dataset 不可比：partially_supported；
- 当前相关 Symbol 已变且无复验：potentially_stale；
- 新 Run 反证：contradicted；
- Experiment 源 timeout：partial/source_unavailable；
- 找不到 metric definition：insufficient_evidence。

## 9. 高频追问

### 为什么不把六个源都放一个向量库？

不同源的粒度、事实语义、版本、权威性和分数分布都不同。统一向量池会让高频 ToolResult/
代码块淹没低频高权威 Metric/Claim，也无法表达数值、状态和版本正确性。

### 为什么 calibration 后还需要 authority？

calibration 只回答“候选与问题是否相关”。Document 可能非常相关，但回答当前实现时
current Code 更权威。把 authority 混进 relevance 会使模型难解释、难迁移。

### 如何校准异构分数？

每个 retrieval domain、entity type、task 用 Golden relevance label 做 isotonic/Platt；
小样本阶段用 rank percentile 和分桶统计，明确标 provisional，不伪称 probability。用
ECE、Brier 和 reliability diagram 监控。

### 为什么不用“每个来源至少一条”？

它会把无关 Workspace/Document 塞进简单代码查询。正确目标是 required role coverage；
只有角色需要且候选合格时才选择某来源。

### GraphRAG 具体做了什么？

单源高置信候选作为 seed，按 Query Plan 选择路径模板，例如 Claim→Metric→Run→Commit→
Diff→CurrentValidation；typed beam traversal 受 predicate schema、max hops、ACL、
version、review、confidence 和预算约束，能补回不含查询词但证据链必需的实体。

### 关系置信度高就代表结论真实吗？

不代表。确定性的 `Run REPORTS Metric` 只证明结构关系，不能证明 Claim 成立；端到端结论
还要检查 metric definition、comparability、version 和 fact status。

### 如何避免多来源重复计数？

先按 RetrievalUnit/Entity/Fact 去重，再按 root provenance 分 independence group。
来自同一 Run 的 Metric、Notebook Output 和 Document Table 可以保留不同 locator，但
evidence independence 只计一次。

### 一个来源超时怎么办？

SourceResult 明确区分 timeout、unavailable、unauthorized、not indexed 与 no match。
若超时来源承担 required role，则 partial 或 refusal；绝不能写成“该来源没有证据”。

### Notebook 为什么必须独立？

Notebook 有 Revision、Execution、Cell、Output、Error、Parameter、display/execution order
和跨 Cell dataflow；这些都不是普通 Run 字段。V1 `experiment` 只作为兼容 family，V2
单独路由和校准 Notebook。

### 如何处理 current 与 historical？

请求开始解析 consistency watermark。每个源返回自己的 generation/version/observed_at，
通过 Run↔Commit、NotebookExecution↔Run、Document validation time、Workspace pinned
version 等 binding 判断 exact/compatible/historical/mismatch/unknown。

### Corrective retrieval 会不会无限循环？

最多两轮；query 由 missing role + known IDs + scope 生成；相同 signature、不增加 coverage、
source unavailable、ACL denied、预算耗尽时停止。

### 生成模型如何防止幻觉？

模型只消费 EvidencePackV2；已验证、reported、observed、inferred、counter、missing 分开。
最终 claim-citation 校验，不满足角色时 partial/refusal。模型无权将 candidate relation
改为 confirmed。

### Workspace 建议会不会自动改任务？

不会。`action_candidate` 是回答模式，mutation 需要显式用户意图、expected version、
权限和 approval，使用独立 trace。

## 10. 必做联合消融

| Run | 变量 | 证明什么 |
| --- | --- | --- |
| M-B0 | 当前统一检索 | 真实 baseline |
| M-B1 | 六源独立 retriever | 单源拆分收益 |
| M-B2 | calibration | 跨源排序 |
| M-B3 | source routing | 成本与精度 |
| M-B4 | required roles | 证据完整性 |
| M-B5 | typed graph | path recall |
| M-B6 | version/conflict | wrong-version/conflict |
| M-B7 | source context | citation/faithfulness |
| M-B8 | corrective | missing role |
| M-B9 | cross-source reranker | nDCG/role utility |

每个 Run 保存：

- Golden version；
- code commit；
- source generations；
- model/index/calibration/policy versions；
- configuration；
- per-slice metrics；
- latency/cost；
- failures。

## 11. 失败实验占位

### 11.1 原始分数直接加权

- 假设：归一化 BM25/cosine/SQL score 后线性融合；
- 失败：分布随 source/query/entity 变化，SQL exact 被稀释；
- 修正：per-domain/task calibration + role utility；
- 指标：ECE、Role Coverage、nDCG；
- Run：TBD。

### 11.2 每源固定配额

- 假设：每源两条可保证多样性；
- 失败：简单 Code query 被无关文档/Workspace 污染；
- 修正：required roles + marginal utility；
- 指标：Context Precision、Activated Domains；
- Run：TBD。

### 11.3 无限制 Graph 扩展

- 假设：多 hop 能补更多信息；
- 失败：高 degree 节点、环和低置信 edge 造成噪声/延迟；
- 修正：typed path template、beam、hop/budget/review/ACL；
- 指标：Path Precision、P95；
- Run：TBD。

### 11.4 多源即独立证据

- 假设：三种来源一致等于三份支持；
- 失败：三者共享同一 Run；
- 修正：root provenance independence；
- 指标：Evidence Independence Accuracy；
- Run：TBD。

### 11.5 Source timeout 当作 zero result

- 假设：空结果统一处理；
- 失败：系统把 unavailable 误写为“没有反证”；
- 修正：typed SourceStatus + answer policy；
- 指标：Partial-answer Honesty；
- Run：TBD。

## 12. 指标看板

| 指标 | Current | 完成后实测 |
| --- | ---: | ---: |
| 单源 Golden | 0/未完整 | Code 50、Codex 45、Experiment 45、Notebook 40、Document 50、Workspace 40 |
| 多源 Golden | 0 | 60 |
| Source Routing Macro-F1 | 未测 | TBD |
| Required Role Coverage | 未测 | TBD |
| Cross-source Recall@20 | 未测 | TBD |
| Evidence Path Recall | 未测 | TBD |
| Counter-evidence Recall | 未测 | TBD |
| Version Accuracy | 未测 | TBD |
| Wrong-version Rate | 未测 | TBD |
| Citation Precision/Completeness | 未测 | TBD |
| Unanswerable Acceptable Rate | 未测 | TBD |
| Calibration ECE/Brier | 未测 | TBD |
| P95 / cost | 未测 | TBD |
| Unauthorized Leakage | 未测 | 0 |

## 13. 简历 Bullet

### 当前可安全写

> 完成研发证据六源 RAG 优化设计：针对 Code、Codex、Experiment、Notebook、Document
> 与 Workspace 定义独立 Retrieval Unit、检索算法和 Release Gate，并设计
> capability-aware Planner、异构相关性校准、required-role fusion、typed evidence
> graph 与 EvidencePackV2。

### 联合层实现并实测后

> 实现六源 Evidence RAG，对 Code/Codex/Experiment/Notebook/Document/Workspace
> 分别进行来源内召回、重排与 calibration，使用 required-role marginal utility 和
> typed evidence graph 补全跨源证据；在 `[dataset/version, N]` 条 Golden Query 上将
> Role Coverage 从 `[A]` 提升至 `[B]`、Evidence Path Recall 达 `[C]`，Wrong-version
> Rate 降至 `[D]`。

### 生产工程完成后

> 构建 consistency watermark、counter-evidence、corrective retrieval 与
> partial/refusal 策略，在 timeout/stale-generation/ACL 故障注入下将 Partial-answer
> Honesty 提升至 `[E]`，Citation Precision/Completeness 达 `[F]/[G]`，联合检索 P95
> 控制在 `[H]`，未授权证据泄漏为 0。

## 14. STAR 叙事

### Situation

系统已有多个数据适配器和统一检索骨架，但不同来源被粗略合并：结构化搜索共用 substring，
Notebook 缺独立路由，分数与关系语义容易混淆。

### Task

在保留现有 stable entity、Generation、ACL 和 API 兼容性的前提下，设计可逐源验证、可
灰度迁移的多源 RAG。

### Action

- 六源分别建 Retrieval Unit/Golden/Release Gate；
- capability registry 和 Query Planner；
- 来源内校准；
- role-constrained fusion；
- typed graph/version/conflict；
- EvidencePackV2/corrective；
- shadow/canary/rollback。

### Result

当前结果是仓库内六源与联合层工程合同完成，安全隔离回归通过，但没有 production
60-case replay、远程模型/ANN benchmark 或发布流量证据。面试中主动区分
`CURRENT_MEASURED`、`CURRENT_IMPLEMENTED`、`QUALITY_HOLD` 和
`TARGET_DESIGNED`，不能把工程完成转换成收益数字。

## 15. 架构面试白板顺序

1. 先写六个源及其最小事实单位；
2. 画每源内部 exact/sparse/dense/structured/graph；
3. 画 capability-aware Planner；
4. 强调 per-source rerank/calibration；
5. 写 required roles 和 marginal utility；
6. 画一个 Claim→Run→Commit→Code→Validation path；
7. 写 counter、version、root provenance；
8. 画 EvidencePackV2；
9. 写 corrective 0–2 和 partial/refusal；
10. 最后写 Golden/ablation/P95/ACL。

## 16. 不能夸大

- V2 的仓库内工程已经实现，但没有上线，默认仍是 V1；
- 历史文档曾记录 Experiment/Notebook 正式数据为空；本轮没有读取正式库，不能把该历史
  数字写成当前测量；
- 六个单源与跨源 60-case Golden 已建立，但不等于 production quality Run；
- calibration、cross-source fusion/rerank 和 ANN admission 已有确定性实现与测试，
  仍没有 production/remote-model qualification；
- Evidence Path Recall 等没有当前实测值；
- Release Gate 门槛是目标，不是成绩；
- 历史共享边数量不等于当前完整证据图，本轮也没有重测正式库；
- Notebook 已有独立 V2 source domain；默认 V1 兼容行为仍保留；
- 未经 Evaluation Run 支持的收益不得写入简历。
