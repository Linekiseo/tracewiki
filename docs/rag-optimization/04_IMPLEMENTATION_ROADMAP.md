# 实施路线、任务拆分与验收门

> **2026-07-30 状态覆盖层**
>
> 本文件保留最初的 16–22 周设计估算和依赖顺序，作为“为什么这样拆”的设计记录；它
> 不是当前进度表。仓库内实现已完成 Code C0–C7、Codex X0–X5、Experiment E0–E5、
> Notebook N0–N6、Document D0–D6、Workspace W0–W6，以及 M5–M7 在仓库内可安全
> 实现的 contracts、隔离存储、Golden/evaluator、retrieval/rerank/context、治理、
> 安全、性能 admission 与显式 opt-in 主路。逐项证据和剩余外部阻塞以
> [原始路线图实施矩阵](development/05_ORIGINAL_ROADMAP_IMPLEMENTATION_MATRIX.md)
> 为准。
>
> 这不是“16–22 周生产计划被几十分钟完成”：生产 migration/backfill、远程模型/ANN
> 硬件 benchmark、真实单源与 60-case 联合 replay、shadow/canary、质量 qualification
> 和默认 V2 切换均未完成，继续
> `EXTERNAL_BLOCKED / QUALITY_HOLD / DEFAULT_V1`。仓库内工程回归通过不能替代这些
> 生产退出门。
>
> 2026-07-30 以六份 source design、multi-source design、global hardening 和本路线图为
> 合同完成最终独立复审：`REPOSITORY_L3_ENGINEERING PASS / P0=0 / P1=0`。该结论只
> 证明当前 Runtime/API/frontend 的仓库内生产可达工程链；不改变下文 L4/L5 所需真实
> replay、性能、安全、shadow/canary 和发布授权。
>
> 同日追加收口了 Codex 全公共 scope 的 Direct/Platform/global 等价过滤、
> Runtime-owned exact index/cache/dashboard、七组件 pre-pipeline rollback switches、
> 固定六来源 authority 的统一 release admission，以及仅打包显式白名单 artifact 的
> multisource portable outer package。所有新增发布判定继续返回
> `QUALITY_HOLD / NON_QUALIFIED / DEFAULT_V1`，没有执行 replay、shadow、canary 或默认切换。

## 1. 总体顺序

```text
M0 基线
→ M1 RAG V2 公共协议
→ M2 Code RAG
→ M3 Codex RAG
→ M4-A Experiment RAG
→ M4-B Notebook RAG
→ M4-C Document RAG
→ M4-D Workspace RAG
→ M5 多源联合
→ M6 上下文与生成
→ M7 性能、安全、发布
→ M8 简历与面试验收
```

M2–M4 都属于单源阶段。M5 之前，每个来源必须有独立 Golden Set 和独立评测结果。

以下工期按单人主导、可复用当前代码基础估算，约 16–22 周；它用于规划依赖，不是承诺。
各单源应严格串行通过质量门，开发准备可以交叉，但不能用联合效果掩盖单源失败。

## 2. M0：基线与数据准备

参考工期：1 周  
优先级：P0  

### 2.1 任务

- 固化当前活跃 Generation、代码 commit 和配置；
- 为六个来源建立 Golden Case schema 扩展；
- 先提交每源最小 smoke set：Code 15、Codex 12、Experiment 12、Notebook 10、
  Document 15、Workspace 10；
- 逐源开发期间扩成正式集合：Code 50、Codex 45、Experiment 45、Notebook 40、
  Document 50、Workspace 40；
- Experiment 至少接入一个有多 Run/多 seed 的可复现实验样例；
- Notebook 至少接入 success、failed、stale、parameterized 样例；
- Document 覆盖 Markdown/PDF/DOCX/HTML/scanned PDF；
- Workspace 覆盖同名 scope、dependency、blocker、acceptance、as-of；
- 编写跨源 20 个先导问题，但暂不作为门禁，M5 扩至 60；
- 加入 hard negative、错误版本、不可回答和 ACL 样本；
- 运行 V1，保存 Evaluation Run；
- 记录 P50/P95、候选数量、索引大小和零结果率；
- 修复 `max_hops` 参数与实际 Trace 不一致，或在 V1 明确标记未支持。

### 2.2 交付物

- `golden-dataset-v1`；
- `baseline-v1` Evaluation Run；
- 按 source/intent/language/complexity 的切片报告；
- Top 20 失败案例；
- 当前成本和延迟基线。

### 2.3 退出标准

- Golden Case 非空并可重复运行；
- 每条 Case 有 target scope、required entities/roles/paths；
- 任何后续优化都能与 V1 基线比较；
- 简历中仍不写“提升 X%”，只记录基线。

## 3. M1：RAG V2 公共协议与双轨运行

参考工期：1 周  
优先级：P0  
依赖：M0  

### 3.1 任务

- 新建 `src/evidence_rag/rag/`；
- 实现 `QueryPlan`、`RetrievalCandidate`、`SourceRetrievalResult`、`EvidencePackV2`；
- 用 adapter 包装当前 Code/Codex/structured search；
- 增加统一 Trace；
- 增加 Feature Flag；
- 实现 V1/V2 shadow compare；
- 规范 relevance、authority、confidence、status、version 字段；
- 对关系状态做一次数据契约审计，修复“未复核关系被标 confirmed”的语义。

### 3.2 退出标准

- V2 adapter 在关闭新算法时与 V1 结果等价；
- V1/V2 可请求级切换；
- Trace 能看到各来源、各通道和过滤原因；
- 不修改稳定实体 ID 和现有 Citation URI。

## 4. M2：Code Graph RAG

参考工期：2–3 周  
优先级：P0  
依赖：M1  

### 4.1 Sprint A：AST Retrieval Unit

- 实现递归 AST chunk builder；
- 分离 retrieval unit 与 CodeSymbol；
- 建立 content-addressed embedding cache；
- 字段化 sparse index：qualified name、path、signature、doc、body；
- 去除 File/Symbol/AST block 重复；
- 对超长 Symbol、类和配置文件增加测试。

退出：

- Locator 100% 回到稳定 Symbol/File；
- Index 可原子发布和回滚；
- Chunk 消融完成。

### 4.2 Sprint B：语义和 Graph 候选

- 接入可替换 code embedding profile；
- 建立 identifier/sparse/dense 多路召回；
- 让 Graph 进入 candidate generation；
- 实现 task-specific traversal template；
- 让 `max_hops` 真正限制遍历；
- 记录 unresolved 和 graph coverage；
- 评审 SCIP/LSP 适配器，先选当前主要语言做 MVP。

退出：

- Symbol/File Recall@10 达标；
- Required Path Recall 有显著、可复现提升；
- 同名 Symbol hard negative 错误可诊断。

### 4.3 Sprint C：Code Rerank 与 Context

- 实现代码来源 reranker；
- 组装 Target/Dependency/Test/Diff Context；
- adaptive-k；
- 补充影响分析、Bug 定位、测试定位 Golden Cases；
- 完成端到端消融。

退出：

- 达到单源 Code 门槛；
- P95 在单源 SLO；
- 至少形成一条可用于面试的“错误案例→设计→消融→收益”证据链。

## 5. M3：Codex Temporal RAG

参考工期：1.5–2 周  
优先级：P0  
依赖：M1；可在 M2 后半段准备数据，但不跨过单源验收门  

### 5.1 任务

- Episode 边界与 summary builder 版本化；
- 补充 Decision/Alternative/Outcome/Unresolved 派生结构；
- 建立 Item→Episode→Thread 层级 Retrieval Unit；
- 构建 FOLLOWS、VALIDATES、SUPERSEDES 等时序关系；
- Goal/Decision、文件、命令、验证多路召回；
- 实现时序和状态 reranker；
- ToolResult/Command/Patch 去重与按需展开；
- 建立 Patch→Validation、Goal→Outcome Golden paths。

### 5.2 退出标准

- Thread/Episode Recall 达标；
- 事件顺序准确；
- false validated 为 0；
- Patch 未提交时不会生成“已提交”结论；
- Code Binding 候选与确认关系明确区分。

## 6. M4：其余四个来源的单源优化

参考工期：6–9 周  
优先级：P0/P1  
依赖：M1  

### 6.1 M4-A Experiment

- 接入真实样例 Experiment/Run；
- 建立白名单 Filter DSL；
- 分离 MetricDefinition 与 MetricObservation；
- metric/config/dataset/environment/commit 精确检索；
- typed numeric/range/unit/step/aggregation；
- Run comparability gate；
- 数值与聚合确定性计算；
- 45 条 Golden 与消融；
- 详细任务以
  [Experiment Source](sources/03_EXPERIMENT_SOURCE_RAG.md) 为准。

退出：

- exact Run、numeric、unit、comparability 指标达标。

### 6.2 M4-B Notebook

- 接入 `.ipynb` success/failed/stale/parameterized fixtures；
- 分离 Template/Revision/Execution；
- typed Markdown/Code/Parameter/Output/Error Unit；
- display order/execution order；
- 跨 Cell defines/reads/writes dataflow；
- stale output 与 identity-aware comparison；
- Notebook source reranker/context；
- 40 条 Golden 与消融；
- 详细任务以
  [Notebook Source](sources/04_NOTEBOOK_SOURCE_RAG.md) 为准。

退出：

- Cell/Output/Error 可直接定位；
- Dependency Path、Stale Detection、Cell Match 指标达标；
- Notebook Output 不自动成为 Experiment Metric。

### 6.3 M4-C Document

- Family/Version/Paragraph/Claim/TableFact/Figure/Formula/Citation Retrieval Unit；
- layout-aware PDF 与 OCR fallback；
- 标题路径、header path、page/bbox locator；
- source text/derived summary 多表示；
- 局部事实与全局总结双计划；
- parent expansion；
- Claim candidate/accepted 与五级 validation；
- TableCell→MetricDefinition/Observation compatibility；
- PDF/DOCX/Markdown/HTML/scanned 分格式切片；
- 50 条 Golden 与消融；
- 详细任务以
  [Document Source](sources/05_DOCUMENT_SOURCE_RAG.md) 为准。

退出：

- Claim/Table/Numeric+Unit/Locator 指标达标；
- Unsupported Verification 受控；
- parent-child duplicate 受控。

### 6.4 M4-D Workspace

- Project/Topic/Iteration/Work/Decision/Requirement typed views；
- exact/status/owner/due structured filter；
- StateTransition + Snapshot + as-of；
- dependency/blocker/acceptance graph；
- EvidenceRequirement role/freshness/review/independence coverage；
- persistent policy-versioned IntelligenceRun；
- authoritative/observed/derived 分层；
- read query/mutation boundary；
- 40 条 Golden 与消融；
- 详细任务以
  [Workspace Source](sources/06_WORKSPACE_SOURCE_RAG.md) 为准。

退出：

- Scope、Current State、Temporal、Coverage 指标达标；
- unsafe mutation 为 0；
- Workspace 不再作为默认多样化占位来源。

## 7. M5：多源联合 RAG

参考工期：2–3 周  
优先级：P0  
依赖：M2、M3、M4 全部通过  

### 7.1 Sprint A：Planner 与校准

- Query Plan JSON；
- source route 和 sub-question；
- 每来源 relevance calibration；
- calibration dashboard；
- 来源超时/未接入/无证据状态区分；
- 构建跨源 Golden Set 至 60 条；
- V1 `experiment` family 向 Experiment+Notebook 扇出，V2 支持 Notebook 独立 domain；
- capability registry 和 source watermark。

退出：

- Source Routing Macro-F1 达标；
- ECE/Brier 有基线；
- 原始 BM25/余弦/SQL 分数不再跨源相加。

### 7.2 Sprint B：Role-aware Fusion

- required role marginal utility；
- authority/version/status factors；
- 实体、事实、parent-child、版本去重；
- adaptive candidate/context budget；
- cross-source reranker；
- 记录选择和过滤解释。

退出：

- Required Role Coverage、Entity Recall、Version Accuracy 达标；
- 无关来源不会因“多样性配额”进入结果。
- 同一 root provenance 的多源转述不会被当作独立证据重复计数。

### 7.3 Sprint C：Evidence Graph

- 实现跨源路径模板；
- typed beam traversal；
- relation derivation/review/confidence gating；
- ACL/version 前置；
- conflict and staleness resolver；
- counter-evidence channel；
- missing role corrective retrieval。

退出：

- Evidence Path Recall、Conflict Recall 达标；
- `max_hops`、边类型和预算都在 Trace 中生效；
- 未确认关系不会被写成 verified fact。

## 8. M6：整体上下文和生成

参考工期：1.5–2 周  
优先级：P1  
依赖：M5  

### 8.1 任务

- Evidence Fact 规范化；
- retrieval context / comprehension context 分离；
- adaptive context packer；
- code/table/episode 专用 rendering；
- grounded generation prompt；
- deterministic/retrieval-only/grounded/refusal 多模式；
- claim-citation entailment；
- corrective retrieval 最多两轮；
- unanswerable taxonomy 和测试。

### 8.2 退出标准

- Citation precision/completeness 达标；
- Unsupported Claim Rate 达标；
- 不可回答 acceptable rate 达标；
- 对比 V1 证明收益来自 retrieval/context/generation 的哪一层。

## 9. M7：性能、安全与发布

参考工期：1–2 周  
优先级：P1  
依赖：M6  

### 9.1 任务

- VectorIndex 抽象与 ANN 压测；
- content-addressed incremental embedding；
- query/embedding/retrieval/rerank/context cache；
- P50/P95/P99 和成本 dashboard；
- prompt injection、ACL、secret leakage 测试；
- shadow traffic；
- canary；
- request-level fallback；
- generation/index/model 回滚演练。

### 9.2 退出标准

- Unauthorized/Secret Leakage 为 0；
- 当前规模 P95 达标；
- 任何 V2 模块可独立回滚；
- shadow/canary 报告完成；
- 索引重建失败不影响 active generation。

## 10. M8：简历和面试验收

参考工期：持续更新，最终 2–3 天集中整理  
依赖：所有阶段  

### 10.1 任务

- 为每个技术亮点建立 Evidence Card；
- 为六个单源分别更新 `interview/01` 至 `interview/06`；
- 更新六源联合 `interview/07_MULTISOURCE_INTERVIEW.md`；
- 填入真实 Evaluation Run ID；
- 整理三张图：
  - 单源 Code Graph RAG；
  - 一个非 Code 单源专项（Experiment/Notebook/Document/Workspace）；
  - 多源 Query Plan/Fusion；
- 整体 Evaluation/Observability 图可作为附图；
- 准备 30 秒、2 分钟、5 分钟版本；
- 准备两个失败案例；
- 准备一个性能/成本取舍；
- 准备一个一致性/安全取舍；
- 检查所有简历数字可复现；
- 标注个人负责边界。

### 10.2 退出标准

- 每条简历 bullet 都能指向代码、设计决策和 Evaluation Run；
- 面试中可解释为何不使用一个统一向量索引；
- 可解释 RRF、校准、reranker、Graph traversal、context packing 的边界；
- 可解释一个优化失败或收益不显著的实验；
- 不把论文结果写成本项目结果。

## 11. PR 完成定义

任何 RAG 优化 PR 至少包含：

- 设计或 ADR；
- Feature Flag；
- 单元和集成测试；
- Golden Case 影响；
- Evaluation Run；
- P50/P95 和索引成本；
- 失败案例；
- 数据/模型/Prompt 版本；
- 回滚路径；
- 面试 Evidence Card 更新。

## 12. ADR 记录模板

```markdown
# ADR-NNN：决策标题

状态：proposed / accepted / rejected / superseded

## 问题
要解决什么可观测失败？

## 约束
数据规模、版本、ACL、延迟、成本、部署限制。

## 候选
至少两个可行方案。

## 决策
选择什么，为什么。

## 未选择方案
为什么没有选择，什么条件下重新评审。

## 评测
Golden Dataset、baseline、主要指标、guardrail。

## 发布与回滚
Feature Flag、shadow、canary、rollback。

## 面试证据
我负责的部分、最难问题、最终结果、可演示入口。
```

## 13. 实验记录模板

```markdown
# RAG Experiment：名称

- Run ID：
- Date：
- Dataset/Golden Version：
- Code Commit：
- Index Generation：
- Embedding/Reranker/Generator：
- Planner/Fusion/Prompt Version：

## 假设

## 唯一主要变量

## Baseline

## Treatment

## Metrics

## Result

## Slice Analysis

## Regressions / Failure Cases

## Decision

## Resume-safe Statement
只填写真实测量结果；没有显著结果时写“未采用”。
```

## 14. 首批可直接转为 Issue 的任务

### P0

- `eval: 建立六个单源 smoke set 和正式 Golden Set`
- `experiment: 接入可复现实验样例和 Experiment 45 条 Golden`
- `notebook-rag: revision/execution/cell/output/error 与 40 条 Golden`
- `rag-v2: 新增统一 contracts 与 V1 adapters`
- `trace: 增加 source/channel/candidate/filter spans`
- `code-rag: AST recursive retrieval units`
- `code-rag: identifier+sparse+dense+graph candidate union`
- `code-rag: typed graph traversal and max_hops`
- `codex-rag: episode hierarchy and temporal path retrieval`
- `document-rag: hierarchical multi-representation units`
- `document-rag: header-aware table facts and layered claim validation`
- `experiment-rag: safe filter DSL and comparability gate`
- `workspace-rag: transition/snapshot/dependency/evidence requirement`
- `fusion: relevance calibration and role-aware utility`
- `graph-rag: cross-source path templates`
- `fusion: notebook domain compatibility and root-provenance independence`

### P1

- `rerank: source-specific rerank profiles`
- `context: late enrichment and adaptive token budget`
- `answer: claim-citation verifier`
- `rag: corrective retrieval and refusal taxonomy`
- `perf: vector index abstraction and ANN benchmark`
- `security: prompt injection and leakage Golden Cases`
- `release: shadow/canary/request fallback`

## 15. 2026-07-29 仓库内安全工程闭环

以下 roadmap 项已通过单会话只读/isolated 实现闭环：

- Notebook typed Cell/Output/Error/Parameter、静态 def-use、执行 context、identity compare；
- Document hierarchical typed units、table cell、claim status、parent expansion、context；
- Workspace Project/Topic/Iteration/WorkItem structured retrieval、confirmed relation、
  evidence coverage、audit/as-of honesty；
- 六来源 deterministic query plan、source budgets/subquestions/capabilities；
- source-local calibration 与 role/authority/status/version fusion，不跨源累加 raw score；
- bounded corrective retrieval（最多两轮）、root provenance dedup、`max_hops` 图展开；
- conflict/staleness/counter channel、全局 retrieval/comprehension context、grounded refusal；
- 六来源 request opt-in、非法 override 422、默认 V1、统一 HOLD/rollback trace。

仍需外部条件的 roadmap 项：production Golden/treatment/quality Run、远程 embedding/LLM、
ANN/OCR、正式库 migration/backfill、shadow/canary 和默认 V2 发布。它们保持
`NOT_QUALIFIED / HOLD_DEFAULT_V1`，不影响仓库内安全工程闭环。
