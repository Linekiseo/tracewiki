# Research Workspace RAG 面试与简历材料

对应设计：[06_WORKSPACE_SOURCE_RAG.md](../sources/06_WORKSPACE_SOURCE_RAG.md)

## 1. 当前状态声明

### FINAL_REPOSITORY_STATUS（2026-07-29）

- W0–W6 仓库内工程 `COMPLETE`：40-case Golden、typed current/history views、
  transition/as-of、planning graph、evidence coverage、decision/intelligence、
  retrieval/context/governance 均已实现；
- baseline artifact：
  `evaluation-run://project-workspace-wb0-v1/363181f5d05847a0aa98fbbee83c4373`，
  set `sha256:2fc7a0c56c55e311986aa33107751646417175857068e2890bcf36d64c73a47c`；
- 当前结论仍为 `VERIFIED_NON_QUALIFIED / HOLD_DEFAULT_V1`；正式库
  migration/backfill、production cross-source observation、shadow/canary 未完成。

### HISTORICAL_MEASURED（未在本轮重测）

- 1 个 active Project；
- 2 个同名“rag系统设计”Topic，均 backlog；
- 0 个 Iteration；
- 1 个 cancelled WorkItem；
- 0 个 IterationLink；
- 1,611 个 confirmed PlatformEdge，主要是其他来源写入的 `contains_diff` 与 `parent`；
- 61 个 AuditEvent；
- 0 PendingApproval、0 DriftAssessment。

### HISTORICAL_IMPLEMENTED

- Project/Topic/Iteration/WorkItem 控制面；
- WorkItem criteria、assignee、due、repository/base ref；
- WorkItem optimistic concurrency；
- soft archive/cancel；
- generic relation + human review；
- IterationLink；
- AuditEvent；
- deterministic Research Intelligence：stage/readiness/blocker/next action/source health。

### HISTORICAL_GAPS（仓库内合同现已实现）

- Search 只返回 Topic/Iteration，不返回 Project/WorkItem；
- 无 status/owner/date/as-of 检索；
- `research graph` 当前主要是 Document+Experiment，不是真正 Workspace graph；
- 无 transition matrix、dependency、acceptance check、decision、risk；
- linked evidence 无 requirement/role coverage/version pin；
- Intelligence 不持久化，无 policy/input lineage/expiry；
- authority facts 与 derived suggestions 未在 RAG 中严格分层。

### 2026-07-29 早期中间实现（已被 FINAL_REPOSITORY_STATUS 覆盖）

- Workspace 已作为显式来源进入六来源统一检索、ACL、stable citation、relation
  expansion 与 bounded context；
- Query comprehension context 只消费最终 verified/supporting/counter evidence，
  控制面实体与 derived recommendation 不会因一次 RAG 查询而被写回；
- 没有重读正式库，因此上面的数量只作历史基线；本次没有新增 transition/snapshot
  schema、持久 IntelligenceRun、as-of backfill 或 production quality Run。

### ORIGINAL_TARGET（仓库内工程已完成，production qualification 未完成）

- control-plane typed retrieval；
- structured/filter/temporal first；
- dependency/blocker/acceptance graph；
- EvidenceRequirement coverage；
- StateTransition+Snapshot；
- persistent explainable IntelligenceRun；
- safe read/action boundary。

## 2. 15 秒定位

> Workspace RAG 不是再给任务描述做向量检索，而是研究控制面：我用结构化 current state、
> temporal snapshot 和 dependency/evidence graph 回答“现在做到哪、为什么阻塞、还缺什么、
> 下一步是什么”。同时严格区分用户确认的状态、外部观测、规则推断和 LLM 建议，避免
> 推荐反过来篡改事实。

## 3. 1 分钟讲解

> 当前系统已经有 Project、Topic、Iteration、WorkItem、跨源 Link、关系评审和审计，
> 还用确定性规则生成 stage、readiness、blocker 与 next action，这是很好的控制面基础。
> 但 Workspace 搜索只覆盖 Topic/Iteration，任务、验收条件、负责人和到期时间都搜不到；
> 状态只有当前字段，无法回答历史时点；证据只看链接数量和来源数量；规则输出也没有保存
> policy 和输入快照。
>
> 我的设计先做 filter-before-rank：display key、status、owner、due、as-of 用结构化和
> 时间索引，模糊目标才进入 sparse/dense。然后补 Dependency、Blocker、Acceptance、
> Decision 和 EvidenceRequirement 图，让系统按 role、review、freshness、independence
> 判断证据缺口。最后把 Intelligence 做成持久化 Run，保存 input entity versions、
> source watermark、policy、reason 和 expiry。回答中 authoritative state 与 derived
> recommendation 分栏，RAG 查询永远不隐式改变任务状态。

## 4. 白板图

```text
Project → Topic → Iteration → WorkItem
                         ├→ Dependency / Blocker
                         ├→ AcceptanceCheck
                         ├→ Decision / Risk
                         └→ EvidenceRequirement → External Evidence Version
                                      ↓
Structured + Temporal + Graph + Sparse/Dense
                                      ↓
Authoritative Snapshot | Derived IntelligenceRun
```

## 5. 核心设计取舍

### 5.1 为什么 structured first

“running 的任务”“7 月 30 日到期”“owner=Alice”是过滤条件，不是语义相似问题。
先 filter 能保证状态正确并降低候选；dense 只处理问题/目标/rationale 的模糊表达。

### 5.2 为什么 Workspace 是独立源

Workspace 的权威信息是计划、归属、状态、负责人和证据要求。Code、Run、Document 提供
事实本体。混在一起会让 `done` 被误解为测试通过，让链接关系被误解为支持关系。

### 5.3 为什么需要 StateTransition + Snapshot

当前表只保存最新状态，Audit detail 又不保证完整 before/after，无法可靠回答 as-of。
Transition 提供可审计事件，Snapshot 提供快速时间投影；两者结合兼顾正确性和查询延迟。

### 5.4 为什么 evidence count 不够

10 个链接可能都来自同一个 Run 的不同视图，也可能全未 review 或已 stale。Evidence
Requirement 按 role、version、freshness、review 和 independence 判断 coverage。

### 5.5 为什么持久化 Intelligence

如果规则权重、源数据或时间变化，下一步建议会变化。保存 IntelligenceRun 才能回答：
“当时为什么给出这个建议？”并区分旧建议和当前建议。

## 6. 最突出的技术点

### Temporal Control-plane Retrieval

- current/as-of 显式；
- transition + snapshot；
- event/effective time；
- source watermark；
- overdue/timezone；
- archived historical retrieval。

### Evidence Coverage Engine

```text
Requirement
→ required roles
→ candidate EvidenceLinks
→ version/review/freshness
→ independence grouping
→ satisfied / missing / stale
```

### Explainable Research Intelligence

- deterministic policy；
- input entity versions；
- source health watermarks；
- readiness dimensions；
- blocker/action reasons；
- confidence/expiry；
- LLM 只负责解释。

### Safe Mutation Boundary

- query 默认 read-only；
- recommendation 不是 command；
- mutation 需 explicit intent；
- expected version；
- transition gate；
- approval/audit。

## 7. 高频追问

### 为什么不直接把 Jira/任务文本做 embedding？

embedding 可解决“哪些任务语义相似”，但不能可靠解决 status、owner、due、dependency 和
as-of。Workspace RAG 的核心正确性来自 structured/temporal/graph；embedding 是补充。

### current state 怎么保证一致？

所有 mutation 产生带 resulting version 的 transition；current view 由事务内更新或 projector
生成。查询绑定 snapshot/source watermark，跨源读使用明确的 consistency timestamp。

### 如何回答“昨天项目什么状态”？

选择不晚于 as-of 的最近 snapshot，再回放到该时间的 state transitions。若事件不完整，
明确拒绝精确结论，不用当前字段倒推。

### readiness 100 分靠谱吗？

它是 policy-derived 指标，不是事实。当前固定 35/25/25/15 只是启发式基线；目标中保存
policy version、各维输入和解释，并用 Golden 做 action/blocker accuracy，而不是把分数
包装成客观真理。

### 如何判断证据独立？

按 root provenance 分组，例如同一 ExperimentRun 的 Metric、Artifact、Notebook 输出属于
一个 independence group；多个文档若都引用同一 Run也不是独立实验。

### WorkItem done 如何控制？

必须通过 acceptance gate：每条 criterion pass/waived、blocking dependency 清空、所需
output/evidence linked，并满足 reviewer policy。否则 transition 被拒绝或进入 review。

### 关系图中 1,611 条 confirmed edge 能说明什么？

只能说明共享 PlatformEdge 中存在这些关系；当前主要是 Code 的 parent/contains_diff，
不能说明 Workspace 已形成研究证据网络。必须按 domain、predicate 和 owner 分域统计。

### 推荐下一步会自动执行吗？

不会。IntelligenceRun 只生成 ActionCandidate。执行需要显式 mutation 请求、权限、
expected version 和必要 approval，使用单独 trace。

### 两个同名 Topic 怎么处理？

exact title 产生歧义时返回 display key、status、owner、updated_at 供消歧；不得默认选择
数据库第一行。Golden 中专门测试。

## 8. 必做消融

| Run | 变量 | 预期观察 |
| --- | --- | --- |
| W-B0 | Topic/Iteration substring | 当前下限 |
| W-B1 | typed views | WorkItem/Decision 可检索 |
| W-B2 | structured filter | 状态与 scope 正确 |
| W-B3 | temporal snapshot | as-of 正确 |
| W-B4 | dependency graph | blocker path |
| W-B5 | requirement coverage | missing role |
| W-B6 | persistent Intelligence | explanation/audit |
| W-B7 | sparse+dense | 模糊目标 recall |
| W-B8 | reranker/context | overall nDCG |

## 9. 失败案例占位

### 失败一：向量优先检索状态

- 查询：“当前 running 的高优任务”；
- 失败：语义相关但 done/cancelled 的任务排前；
- 修正：structured filter-before-rank；
- 指标：Current State Accuracy；
- Run：TBD。

### 失败二：来源数作为证据充分性

- 场景：Document、Notebook、Metric 都源自同一个 Run；
- 失败：source_types=3，被认为交叉验证；
- 修正：Requirement role + root provenance independence；
- 指标：Evidence Coverage Precision；
- Run：TBD。

### 失败三：不持久化推荐

- 场景：source health 更新后 next action 改变；
- 失败：无法解释前一次建议；
- 修正：IntelligenceRun 保存 input/policy/watermark；
- 指标：Explanation Completeness；
- Run：TBD。

## 10. 性能与成本取舍

| 决策 | 收益 | 成本 |
| --- | --- | --- |
| structured current view | 高正确/低延迟 | schema 维护 |
| snapshots | as-of 快 | 存储、projector |
| graph expansion | blocker/coverage 可解释 | edge ontology |
| persistent Intelligence | 可重现 | invalidation/recompute |
| dense auxiliary | 模糊召回 | embedding 成本 |
| external preview | token 可控 | 需要二阶段召回 |

绝大多数状态查询无需 LLM reranker；精确 filter 和 materialized view 是最低成本路径。

## 11. 指标证据表

| 指标 | Current | Target/完成后实测 |
| --- | ---: | ---: |
| Projects/Topics | 1 / 2 | TBD |
| Iterations | 0 | TBD |
| WorkItems | 1 cancelled | TBD |
| IterationLinks | 0 | TBD |
| Golden | 0 | 40 |
| Scope Resolution Accuracy | 未测 | TBD |
| Current State Accuracy | 未测 | TBD |
| Temporal Accuracy | 未测 | TBD |
| Blocker Precision | 未测 | TBD |
| Coverage Precision | 未测 | TBD |
| Unsafe Mutation Rate | 未测 | 0 |
| Query P95 | 未测 | TBD |

## 12. 简历 Bullet

### 设计阶段

> 设计 Research Workspace Control-plane RAG，采用 structured/temporal/graph-first
> 检索管理 Topic、Iteration、WorkItem、Decision 与 EvidenceRequirement，并将权威状态、
> 外部观测、规则 Intelligence 和 LLM 建议分层，保证研究状态与行动建议可解释、可审计。

### 基础实现后

> 实现 Workspace Temporal RAG，通过 StateTransition+Snapshot 支持 current/as-of 查询，
> 在 `[N]` 条 Golden 上将 Scope Resolution Accuracy 提升至 `[A]`、Current State
> Accuracy 达 `[B]`、Temporal Accuracy 达 `[C]`。

### 证据闭环后

> 构建 Evidence Requirement Coverage Engine，按 role、review、freshness、version 与
> root-provenance independence 识别证据缺口，使 Coverage Precision/Recall 达
> `[D]/[E]`；持久化 IntelligenceRun 以复现 blocker 和 next-action 决策。

## 13. STAR 叙事

### Situation

系统已有研究工作台和规则推荐，但检索只看 Topic/Iteration 文本，任务状态、证据覆盖和
历史时点无法可靠回答。

### Task

将 Workspace 从 CRUD Dashboard 升级为正确、可解释、可追溯的研究控制面 RAG。

### Action

- structured/filter-before-rank；
- state transition/snapshot；
- dependency/blocker/acceptance；
- evidence requirement coverage；
- persistent policy-versioned Intelligence；
- recommendation/mutation 隔离。

### Result

当前为设计成果；后续用 Scope、Temporal、Blocker、Coverage 和 Unsafe Mutation 等真实
指标填充，禁止把 readiness 规则分数当作模型效果。

## 14. 历史边界与当前不能夸大

- 当前没有 Iteration；
- 当前没有 active WorkItem；
- 当前没有 IterationLink；
- 当前 Search 找不到 WorkItem；
- 当前 research graph 不是 Workspace planning graph；
- 当前没有 dependency/acceptance check/decision；
- 当前不能可靠回答 as-of；
- 当前 Intelligence 不持久化；
- 当前两个同名 Topic 存在 scope 歧义；
- 1,611 条 PlatformEdge 主要属于其他来源；
- 目标门槛不是实测结果。

## 15. 2026-07-29 早期中间工程落地（历史记录）

- 新增 `rag/workspace_v2.py`，只读检索 Project、Topic、Iteration、WorkItem，修复 V1
  structured search 对 Project/WorkItem 覆盖不足的问题。
- query parser 支持 status/owner/assignee/topic/iteration/due/key filter，并按
  current/blocker/next/coverage/audit/compare task 做 filter-before-rank。
- 只把 `review_status=confirmed` 的关系当作事实；IterationLink 作为 evidence coverage，
  authoritative state、confirmed relation、audit observation 分层输出。
- `as_of` 请求只有审计事件时明确返回
  `UNAVAILABLE_WITH_AUDIT_EVIDENCE`，不从当前 row 伪造历史快照。
- 输出 `workspace-control-plane-context-v2`，包含当前状态、confirmed relations、
  evidence links、audit evidence、缺失角色与稳定 digest；无 mutation side effect。
- Platform/Query 仅在 `X-RAG-Workspace-Engine: v2` 时启用，project ACL fail-closed，
  默认及回滚为 V1。

真实边界：没有正式库 StateTransition/Snapshot migration、persistent Intelligence Run
或 production quality Run；因此状态是
`ENGINEERING COMPLETE / DEFAULT_V1 / TEMPORAL_HISTORY_PARTIAL / QUALITY_UNAVAILABLE`。

## 16. 2026-07-29 原始路线图最终工程状态

- W0：released 40-case Golden，8 slices=`5/6/6/6/6/5/3/3`，7 hard-negative
  cases 和 portable baseline；
- W1：Project/Topic/Iteration/WorkItem typed current/history entities、scope/ACL/
  generation、isolated atomic/idempotent store；
- W2：legal transition matrix、optimistic concurrency、done/reopen/restore gate、
  deterministic as-of projection 与 timezone-aware overdue；
- W3：dependency/blocker/risk/acceptance/check/decision/outcome graph 与 bounded path；
- W4：EvidenceRequirement role/source/min-count/independence/freshness/review/pinned
  generation/ACL policy；
- W5：authority A–E、policy/input lineage、persistent/expiring IntelligenceRun、readiness/
  blocker/next-action explanation，且 `authoritative_mutation=false`；
- W6：exact/structured/sparse/local-hash dense/bounded graph/rerank、stable citation/
  context、tombstone/rollback、安全与 portable verifier。

当前 hard-negative `38/40`、MRR `.8848`、nDCG `.8982` 仍来自隔离 baseline，
不是 production qualification；默认 V1，不能宣称线上 as-of/coverage 已通过真实流量。
