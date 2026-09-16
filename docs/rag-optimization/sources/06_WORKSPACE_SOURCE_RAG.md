# Research Workspace 单源 RAG 详细设计

状态：`REPOSITORY_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD`  
设计版本：workspace-source-rag-v2  
基线日期：2026-07-27  
对应面试文档：[06_WORKSPACE_SOURCE_INTERVIEW.md](../interview/06_WORKSPACE_SOURCE_INTERVIEW.md)

> **2026-07-30 实现覆盖层**
>
> 本文正文仍是功能与退出门基线。显式 Workspace V2 已从 Runtime/Platform 到达完整
> project/topic/iteration/work-item、state/as-of、planning relation、evidence coverage、
> audit 与 context facade；只把已确认关系投影给回答层，相关实体不会因此被升级为已
> 验证领域事实。正式 Workspace store 为 authority，派生索引惰性且可丢弃。默认 V1
> 与回退不变；真实 IdP/ACL 映射、40-case replay、校准和发布观测仍是 L4/L5 阻塞。

## 1. 来源定位

Workspace Source 是多源研发证据系统的**研究控制面**，不是又一个通用文本语料库：

```text
Project
└── Research Topic
    └── Research Iteration
        ├── Work Item
        ├── Decision / Risk / Blocker
        ├── Evidence Requirement
        ├── Iteration Link → external source entity
        └── Outcome / Review
```

它回答的是：

- 现在在解决什么问题；
- 当前处于哪个研究阶段；
- 谁负责、何时到期；
- 哪些任务正在执行、阻塞或等待评审；
- 当前结论还缺哪类证据；
- 哪个证据属于哪轮迭代、扮演什么角色；
- 哪些状态是人工事实，哪些是系统推断；
- 下一步建议为什么产生。

Workspace 的检索原则是：

> 结构化状态与审计事件优先，文本相关性辅助；先求“状态正确”，再求“语义相似”。

## 2. 物理入口与边界

### 2.1 当前入口

- Project CRUD；
- ResearchTopic CRUD/soft archive；
- ResearchIteration CRUD/soft archive；
- ResearchWorkItem CRUD/transition/soft cancel；
- IterationLink；
- generic PlatformEdge/review；
- AuditEvent；
- Dashboard；
- rule-based ResearchIntelligence。

WorkItem 支持：

- research/development/experiment/analysis/review；
- human/codex assignee；
- acceptance criteria；
- Repository/workspace path/base ref；
- priority/due date；
- optimistic concurrency version。

### 2.2 未来入口

P0：

- 显式 Dependency/Blocker/Risk/Decision；
- EvidenceRequirement 与 coverage；
- WorkItem transition event；
- status snapshot/as-of；
- derived IntelligenceRun；
- current view materialization；
- structured/filter/semantic hybrid retrieval。

P1：

- GitHub/Atlassian/Notion 双向同步；
- capacity/estimate；
- milestone；
- notification/escalation；
- richer approval workflow。

外部系统同步不是单源优化 P0；先确保本地控制面语义稳定。

### 2.3 来源边界

- Workspace 记录“计划/归属/状态”，不替代 Code、Run、Document 的原始事实；
- `WorkItem done` 不等于 acceptance criteria 已满足；
- `Iteration progress=80` 是用户/规则记录，不等于 80% 证据完备；
- IterationLink 表示“归入本轮”，不自动表示支持 Claim；
- `PlatformEdge unreviewed` 不是正式关系；
- Intelligence 的 stage/readiness/blocker/next action 是派生判断，不是用户写下的事实；
- AuditEvent 证明发生过 API/状态事件，不证明业务结果正确；
- Workspace 的 owner/assignee 与 Codex 日志 actor 需要显式绑定，不能靠名字相同推断。

## 3. 当前实现审计

### 3.1 代码入口

| 能力 | 当前实现 |
| --- | --- |
| 请求与状态枚举 | [workspace/models.py](../../../src/evidence_rag/workspace/models.py) |
| Schema | [workspace/schema.py](../../../src/evidence_rag/workspace/schema.py) |
| CRUD/状态/关系 | [workspace/service.py](../../../src/evidence_rag/workspace/service.py) |
| Store | [workspace/store.py](../../../src/evidence_rag/workspace/store.py) |
| Research Intelligence | [workspace/intelligence.py](../../../src/evidence_rag/workspace/intelligence.py) |
| Router | [workspace/router.py](../../../src/evidence_rag/workspace/router.py) |
| 跨源搜索/图 | [platform/store.py](../../../src/evidence_rag/platform/store.py) |

### 3.2 当前实体

| 实体 | 当前字段 |
| --- | --- |
| Project | name/description/owner/ACL/classification/status/settings |
| ResearchTopic | problem/objective/status/priority/owner/tags |
| ResearchIteration | goal/hypothesis/status/progress/time/summary/owner |
| ResearchWorkItem | objective/criteria/kind/status/priority/assignee/repo/path/ref/due/summary/version |
| IterationLink | linked entity/type/source/role/status/metadata |
| PlatformEdge | subject/predicate/object/evidence/derivation/confidence/review/validity/rule |
| AuditEvent | actor/action/resource/detail/trace/time |

缺失的控制面一等实体：

- Decision；
- Risk；
- Blocker；
- Dependency；
- EvidenceRequirement；
- AcceptanceCheck；
- Outcome；
- StateTransition；
- IntelligenceRun；
- Milestone。

### 3.3 当前状态模型

Topic：

```text
backlog | active | blocked | completed | archived
```

Iteration：

```text
planned | active | validating | completed | blocked | archived
```

WorkItem：

```text
backlog | ready | running | review | blocked | done | cancelled
```

当前 `transition_work_item` 没有合法 transition matrix，理论上可从任意状态跳到任意状态。
Topic/Iteration update 没有 optimistic concurrency；WorkItem 有 `expected_version`。

Soft delete：

- Topic → archived，同时 archive iterations、cancel work items；
- Iteration → archived，同时 cancel其 work items；
- WorkItem → cancelled。

这是正确方向，但缺少：

- transition event 独立表；
- cancel/archived reason；
- dependency gate；
- acceptance gate；
- completed 后 reopen；
- status effective time；
- state as-of 重建。

### 3.4 当前 Iteration Link

`IterationLink` 已经可以把 Code/Codex/Experiment/Document/Workspace 实体归入研究迭代，
并保存 role/status/metadata。

问题：

- `source_type` 枚举没有 notebook；
- role 为自由字符串，无 ontology；
- linked 不等于 relevant/supported/accepted；
- 没有 requirement→evidence coverage；
- 没有 created_by/reviewed_by；
- 删除为 hard delete，只有 audit；
- entity 必须当前存在于 active generation，历史链接可能受 source generation 切换影响；
- 没有 snapshot/version pin；
- 相同实体可以用不同 role 重复链接，但缺 role policy。

### 3.5 当前 PlatformEdge

优点：

- source/predicate/target；
- optional evidence；
- derivation；
- confidence；
- review status；
- valid_from/valid_to；
- rule_version；
- reviewer/note；
- audit。

问题：

- predicate 为自由文本；
- 没有 edge schema/allowed endpoints；
- deterministic edge 也可能带错误逻辑；
- edge 更新与 source generation 生命周期未统一；
- unreviewed/confirmed/rejected 三态过粗；
- 没有 relation supersession/tombstone；
- 当前 1,611 条 confirmed edge 主要是其他来源写入的 `contains_diff`/`parent`，不代表
  Workspace 自身有 1,611 条研究关系；
- Workspace 不能以共享图总边数冒充研究计划成熟度。

### 3.6 当前审计

所有 Workspace mutation 会写 AuditEvent，部分 API 也有通用 audit。

当前 AuditEvent：

- detail 是变化字段，不一定保存 before/after；
- actor 默认 `RAG Core`；
- trace_id 每次新建；
- 没有 causation/correlation ID；
- 没有 entity version；
- 不保证可由 events 重放状态；
- API action 与业务 action 混合；
- 没有 tamper-evident hash chain。

### 3.7 当前 Research Intelligence

`ResearchIntelligenceService` 用数据库事实产生：

- selected Topic/Iteration；
- stage；
- readiness score/level/dimensions；
- work summary；
- evidence coverage summary；
- source health；
- blockers；
- next actions；
- headline。

当前 stage：

```text
define → plan → execute → validate → review → complete
```

当前 readiness 100 分：

- definition 35；
- execution 25；
- evidence 25；
- review 15。

当前规则包含：

- 无 Topic → 建 Topic；
- 无 problem/objective → 补研究问题；
- 无 Iteration → 建 Iteration；
- blocked work/iteration；
- code indexing failure；
- Codex execution failure；
- contradicted Claim；
- evidence drift；
- pending approvals/reviews；
- finished work 但无 linked evidence；
- 单一 source evidence；
- insufficient Claim。

这是有价值的可解释控制逻辑，但仍有问题：

- 默认选择第一个 active Topic，否则列表第一个；同优先级/更新时间可能不是用户目标；
- score 权重是固定启发式，没有 policy version 暴露；
- `evidence.total > 0` 不等于 evidence roles 完整；
- `source_types >= 2` 不等于独立交叉验证；
- progress 与 readiness 可矛盾；
- due date 不参与 overdue/blocker；
- WorkItem dependency 不存在；
- acceptance criteria 只统计是否为空，不检查是否满足；
- next actions 未保存为 IntelligenceRun，无法解释历史答案为何变化；
- derived action 没有 confidence/inputs；
- source health 是 project-wide，可能与 selected iteration 无关；
- Notebook 没有独立 health/coverage；
- 无 as-of 语义；
- 规则输出可能被统一 RAG 当成事实。

### 3.8 当前搜索

Workspace 统一结构化搜索只返回：

- ResearchTopic：title/problem/objective；
- ResearchIteration：title/goal/hypothesis。

不返回：

- Project；
- WorkItem；
- acceptance criteria；
- blocker/summary/due/assignee；
- IterationLink；
- Decision/Risk；
- AuditEvent；
- Intelligence；
- relation review。

搜索方式仍是 token substring coverage，缺：

- exact display key；
- status/owner/date/filter；
- sparse/dense；
- time-aware current view；
- workspace reranker；
- task-specific Context。

`graph_metadata(domain=research)` 和 `_research_graph` 当前主要组合 Document 与 Experiment
graph，并不是 Workspace planning graph；这是命名和能力边界不一致。

### 3.9 当前活跃数据规模

2026-07-27 正式库：

| 实体 | 数量/状态 |
| --- | --- |
| Project | 1 active |
| ResearchTopic | 2，均 backlog、priority=3、标题均为“rag系统设计” |
| ResearchIteration | 0 |
| ResearchWorkItem | 1 cancelled |
| IterationLink | 0 |
| PlatformEdge | 1,611 confirmed |
| 主要 Edge | `contains_diff` 1,069；`parent` 542 |
| AuditEvent | 61 |
| PendingApproval | 0 |
| DriftAssessment | 0 |

因此当前 Workspace 有管理骨架，但没有真实 Iteration→Work→Evidence 闭环。

### 3.10 当前优点

- 研究层级 Project→Topic→Iteration→Work 已存在；
- WorkItem acceptance criteria、assignee、repo/base ref、due date 字段较完整；
- WorkItem optimistic concurrency；
- soft delete；
- generic reviewed relation；
- IterationLink 可承载跨源归属；
- mutation audit；
- Research Intelligence 是确定性、可解释规则，而非黑盒 LLM；
- source health、blocker、next action 已有控制面雏形。

### 3.11 当前失败模式

| 失败 | 根因 |
| --- | --- |
| “有哪些任务”搜不到 | Search 不返回 WorkItem |
| “谁负责/何时到期”搜不到 | 无 structured filter/view |
| 同名 Topic 选错 | 默认 first active/first row |
| 已取消任务影响历史理解 | 无 as-of/current 语义 |
| done 被理解为已验收 | 无 AcceptanceCheck |
| 证据数量多却仍不充分 | 无 EvidenceRequirement/role coverage |
| 1,611 edge 被误读为研究关系丰富 | shared graph 与 workspace edge 未分域 |
| 推荐为何变化不可解释 | Intelligence 不持久化 |
| overdue 不成为 blocker | due_at 未进入规则 |
| 任意状态跳转 | 无 transition matrix |
| 关系 predicate 漂移 | 无 ontology/schema |
| 多源事实被控制面覆盖 | source authority 边界不清 |

## 4. Workspace 查询任务目录

### 4.1 精确对象定位

示例：

- 打开 `TASK-92BC4C25`；
- “rag系统设计”有几个 Topic？
- 当前 Project 的 owner 是谁？

优先：

- display key/exact ID；
- exact title；
- entity type；
- duplicate disambiguation。

若同名必须列出 display key/status/updated time，不能静默选一个。

### 4.2 当前状态

示例：

- 当前研究进行到哪里？
- 本轮迭代状态和完成度是什么？
- 现在有哪些 running/review/blocked 任务？

必需：

- resolved Project/Topic/Iteration；
- authoritative current fields；
- status effective time；
- active WorkItems；
- linked evidence coverage；
- source health；
- generated Intelligence 单独标 derived。

### 4.3 阻塞与风险

示例：

- 当前为什么被阻塞？
- 哪些任务过期？
- 哪个依赖没完成？

必需：

- explicit Blocker/Risk；
- blocked WorkItem/Iteration；
- Dependency path；
- overdue calculation；
- source health issue；
- owner/due/action；
- observed_at。

不得仅凭“很久没更新”自动断言 blocked；可以输出 `stale_activity_candidate`。

### 4.4 下一步行动

示例：

- 下一步最应该做什么？
- 哪个任务应该优先？

返回：

- recommended Action；
- rule/policy version；
- priority；
- reasons；
- input fact IDs；
- blockers/dependencies；
- confidence；
- alternative；
- needs human decision。

Action 是推荐，不是已经批准的任务变更。

### 4.5 证据覆盖

示例：

- 这一轮还缺什么证据？
- 结论是否有 Code、Run、Document 三类支撑？
- 哪些 evidence links 还没 review？

必需：

- EvidenceRequirement；
- required roles；
- satisfied/missing；
- linked evidence；
- validation/review/freshness；
- independence group；
- source type；
- pinned version。

只统计来源数量不够。

### 4.6 任务与验收

示例：

- 哪些 acceptance criteria 未满足？
- 这个 WorkItem 能否进入 done？
- Codex 回传了什么结果？

必需：

- WorkItem；
- criteria；
- per-criterion AcceptanceCheck；
- linked output/evidence；
- reviewer；
- transition history；
- unresolved blocker。

### 4.7 决策与理由

示例：

- 为什么本轮选择方案 B？
- 这个决定由哪些实验和讨论支持？

必需：

- Decision；
- alternatives；
- rationale；
- decided_by/time；
- evidence links；
- status/superseded；
- relation confidence/review。

当前没有 Decision 实体，P0 必须补。

### 4.8 时间与审计

示例：

- 7 月 20 日时项目处于什么状态？
- 谁把任务从 running 改成 review？
- 当前状态与昨天有什么差异？

必需：

- StateTransition/Snapshot；
- actor；
- before/after；
- effective/event time；
- causation/trace；
- as-of projection。

当前 AuditEvent 不足以保证可靠重建，需要 transition/snapshot。

### 4.9 迭代比较

示例：

- Iteration 1 和 2 的目标、实验和结果有什么差别？
- 哪轮引入了当前结论？

必需：

- exact iterations；
- goals/hypotheses；
- work outcomes；
- evidence requirement coverage；
- decisions；
- linked source versions；
- outcome/summary；
- unresolved risks。

## 5. 目标领域模型

### 5.1 Control Plane Graph

```text
Project
├── HAS_TOPIC → Topic
│   └── HAS_ITERATION → Iteration
│       ├── HAS_WORK_ITEM → WorkItem
│       │   ├── DEPENDS_ON → WorkItem
│       │   ├── BLOCKED_BY → Blocker
│       │   └── HAS_ACCEPTANCE → AcceptanceCriterion → AcceptanceCheck
│       ├── HAS_REQUIREMENT → EvidenceRequirement
│       │   └── SATISFIED_BY → EvidenceLink → ExternalEntityVersion
│       ├── HAS_DECISION → Decision
│       ├── HAS_RISK → Risk
│       └── PRODUCES_OUTCOME → Outcome
└── HAS_POLICY → WorkspacePolicyVersion
```

### 5.2 原始与派生

**Authoritative control facts**

- 用户创建/修改的 goal、status、owner、due；
- approved transition；
- reviewed relation；
- accepted evidence link；
- acceptance check。

**Observed external facts**

- source health；
- run status；
- current generation；
- claim validation；
- pending approval。

**Derived intelligence**

- stage；
- readiness；
- inferred blocker；
- recommended next action；
- evidence gap；
- stale activity；
- risk score。

Derived intelligence 必须保存 input snapshot、policy/rule version、confidence、generated_at 和
expiration，不得回写 authoritative status，除非用户/自动化明确批准。

### 5.3 Evidence Requirement

```text
EvidenceRequirement
  iteration_id
  purpose: implementation | validation | rationale | reproduction | publication
  role
  allowed_source_domains
  min_count
  independence_rule
  freshness_rule
  review_rule
  version_rule
  status
```

例如：

```text
claim_verification:
  - role=reported_claim, source=document, count>=1
  - role=completed_run, source=experiment, count>=1
  - role=metric_definition, source=experiment, count>=1
  - role=implementation_commit, source=code, count>=1
  - role=validation_result, source=code|experiment, count>=1
```

Workspace 只保存要求与覆盖；事实本体仍由各源提供。

### 5.4 Evidence Link

替代自由 role 的目标结构：

- requirement_id；
- external entity stable ID；
- pinned entity version/generation；
- source domain；
- role ontology；
- relevance；
- status: proposed/accepted/rejected/superseded/stale；
- linked_by/at；
- reviewed_by/at；
- validity；
- note；
- evidence snapshot hash。

### 5.5 Decision

字段：

- question；
- alternatives；
- selected alternative；
- rationale；
- decision status: proposed/approved/rejected/superseded；
- owner/approver；
- decided_at；
- review_at；
- linked evidence；
- constraints；
- reversible；
- successor decision。

这比从 Codex conversation 中临时总结“为什么”更稳定；Codex Episode 可作为 supporting
evidence，而不是唯一 decision store。

### 5.6 State Event 与 Snapshot

```text
StateTransition
  entity_id/type
  field
  before
  after
  expected_version
  resulting_version
  actor
  event_at
  effective_at
  causation_id
  correlation_id
  reason

WorkspaceSnapshot
  project/topic/iteration scope
  as_of
  state_hash
  source watermarks
  policy_version
```

Event 用于审计，Snapshot 用于快速 as-of 查询；两者都不能由 LLM生成。

## 6. Retrieval Unit 与索引

### 6.1 Retrieval Unit

| Unit | 主要内容 |
| --- | --- |
| ProjectView | identity/description/owner/status |
| TopicView | problem/objective/status/priority |
| IterationView | goal/hypothesis/status/progress/time/outcome |
| WorkItemView | objective/criteria/status/owner/due/summary |
| AcceptanceView | criterion/check/evidence/reviewer |
| DecisionView | question/options/choice/rationale |
| BlockerRiskView | issue/severity/owner/mitigation |
| EvidenceRequirementView | role/coverage/missing |
| EvidenceLinkView | external entity/version/role/status |
| TransitionView | before/after/actor/time |
| IntelligenceView | derived stage/action/reason/policy/input |

### 6.2 索引优先级

1. **Structured current-state index**
   - IDs/display keys；
   - entity/status/owner/assignee；
   - priority/due；
   - Topic/Iteration；
   - acceptance state；
   - evidence coverage；
   - freshness/review。

2. **Temporal index**
   - event/effective time；
   - as-of snapshot；
   - transition；
   - overdue；
   - last activity。

3. **Graph adjacency**
   - hierarchy；
   - dependency/blocker；
   - requirement/evidence；
   - decision/evidence；
   - transition chain。

4. **Sparse**
   - problem/objective/goal/hypothesis；
   - work title/criteria/summary；
   - decision/risk/blocker。

5. **Dense**
   - 模糊目标；
   - 语义相近任务/迭代；
   - rationale；
   - 不用于替代 status/filter。

### 6.3 Search View

每个 view 保存：

- stable entity/version；
- authoritative/observed/derived；
- current/as-of；
- exact fields；
- text surface；
- hierarchy；
- status/time/owner；
- ACL/classification；
- policy/builder/generation；
- source watermarks；
- locator；
- stale/expired。

## 7. 单源检索算法

### 7.1 Query Profile

```text
WorkspaceQueryProfile
  task
  exact keys/titles
  project/topic/iteration/work constraints
  status/owner/assignee/priority
  date/as_of/due window
  current_vs_history
  evidence roles
  include derived intelligence
  mutation intent (read-only by default)
```

### 7.2 Resolve Scope First

顺序：

1. Project；
2. explicit display key/ID；
3. Topic；
4. Iteration；
5. WorkItem/Decision；
6. time/as-of。

同名实体不自动选；返回候选并保留歧义。当前两个同名 Topic 是必测 fixture。

### 7.3 Filter-before-rank

状态型查询：

```text
structured filter
→ exact match
→ temporal projection
→ hierarchy/dependency expansion
→ sparse/dense auxiliary ranking
```

语义型查询才使用：

```text
scope/filter
→ sparse+dense
→ graph expansion
→ workspace reranker
```

### 7.4 Workspace Reranker

特征：

- exact key/title；
- resolved hierarchy；
- entity type/task；
- status filter；
- current/as-of alignment；
- due/priority；
- owner/assignee；
- authoritative vs derived；
- evidence requirement role；
- link review/freshness；
- source watermarks；
- stale/expired；
- duplicate title ambiguity。

### 7.5 Dependency 与 Coverage Expansion

模板化扩展：

- WorkItem → dependencies/blockers/criteria/checks；
- Iteration → active work/requirements/decisions/risks/outcome；
- EvidenceRequirement → accepted links → external entity summaries；
- Decision → alternatives/evidence/superseding decision；
- Intelligence → input facts/policy；
- Transition → previous/next state。

Workspace 内默认 2 hop；跨到外部来源时只带 lightweight preview，完整证据由多源层按需召回。

### 7.6 Intelligence 运行

```text
authoritative snapshot
+ source health watermarks
+ workspace policy version
→ deterministic rules
→ IntelligenceRun
→ stage/readiness/blockers/actions
```

输出保存：

- result；
- input entity versions；
- source watermark；
- policy version；
- reasons；
- confidence；
- generated/expires；
- superseded_by。

LLM 可以生成自然语言解释，但不能改变 rule output。

### 7.7 降级

- dense 不可用：structured+exact+sparse；
- external source health 超时：使用 last-known watermark，标 stale；
- as-of snapshot 缺失：event replay，仍失败则拒绝精确历史状态；
- graph 关系缺失：返回 hierarchy/current facts 和 missing；
- Intelligence 过期：不作为 current recommendation；
- ambiguous scope：列出候选，不擅自选。

## 8. Context 设计

### 8.1 当前状态 Context

```text
[AUTHORITATIVE SNAPSHOT · as_of=...]
Project / Topic / Iteration
Status / owner / progress / target
Active work:
  TASK-X running owner=... due=...
Blocked/dependencies:
Evidence requirements:
  satisfied / missing / stale

[DERIVED INTELLIGENCE · policy=v... · generated=...]
Stage:
Readiness:
Recommended action:
Reasons:
```

### 8.2 WorkItem Context

```text
[WORK ITEM TASK-... · version=...]
Status: running
Objective:
Acceptance criteria:
  [pass|fail|unknown] criterion → evidence
Dependencies:
Blockers:
Assignee / due:
Latest transition:
Linked outputs:
```

### 8.3 Evidence Coverage Context

```text
[REQUIREMENT ...]
Purpose:
Role:
Policy:

[SATISFIED]
external entity + pinned version + review/freshness

[MISSING / STALE / REJECTED]
reason + required action
```

### 8.4 Token 策略

- authoritative current facts 35%；
- target entity detail 25%；
- dependency/requirement evidence 20%；
- transition/audit 10%；
- derived intelligence 10%。

外部实体只带 preview 和 locator，避免 Workspace Context 吞入所有跨源正文。

## 9. 正确性与治理

### 9.1 权威等级

| 层级 | 内容 |
| --- | --- |
| A | approved authoritative state/transition |
| B | observed external source state |
| C | reviewed relation/evidence link |
| D | rule-derived Intelligence |
| E | LLM-inferred suggestion |

回答必须显示 D/E 为派生；D/E 不能覆盖 A。

### 9.2 状态转换

定义合法 WorkItem matrix：

```text
backlog → ready/cancelled
ready → running/blocked/cancelled
running → review/blocked/cancelled
blocked → ready/running/cancelled
review → running/done/blocked
done → running (reopen, reason required)
cancelled → backlog (restore, approval required)
```

`done` gate：

- acceptance criteria 全部 pass/waived；
- blocking dependency 清空；
- required outputs/evidence linked；
- reviewer/automation policy满足。

Topic/Iteration 也增加 version 与 transition matrix。

### 9.3 时间

- event time 与 effective time 分离；
- overdue 以 Project timezone；
- as-of 使用 snapshot + event replay；
- source health 包含 observed_at；
- external entity link pin version；
- Intelligence 设置 expires_at；
- archived/cancelled 默认不在 current，但在 historical 可查。

### 9.4 ACL

- Project ACL 是最低边界；
- external linked evidence 仍使用其自身 ACL；
- Workspace summary 不得泄露未授权 external title/snippet；
- evidence coverage 可以显示“存在但无权访问”，不显示内容；
- derived Intelligence 只基于授权输入；
- ACL 变化触发 view rebuild 和 cached Intelligence invalidation。

### 9.5 Mutation Boundary

RAG 查询默认 read-only：

- “下一步做什么”只返回建议；
- 不自动创建/transition WorkItem；
- mutation 需要显式命令、expected version 与权限；
- 高风险状态变更需要 approval；
- 推荐和执行使用不同 trace/action。

### 9.6 删除与版本

- WorkItem/Topic/Iteration soft delete；
- EvidenceLink 改为 status=superseded/rejected，不 hard delete；
- relations 有 tombstone/supersedes；
- search generation 原子切换；
- active/current view 可回滚；
- audit/transition 保留合规期限。

## 10. 评测

### 10.1 Golden Set：40 条

| 类别 | 数量 |
| --- | ---: |
| exact/歧义定位 | 5 |
| 当前状态 | 6 |
| WorkItem/验收 | 6 |
| blocker/dependency/overdue | 6 |
| evidence coverage | 6 |
| next action/intelligence | 5 |
| decision/rationale | 3 |
| as-of/audit/iteration compare | 3 |

### 10.2 Hard Negatives

- 两个同名 Topic；
- done 但 criterion fail；
- running 但 dependency blocked；
- due date 在不同时区边界；
- linked evidence 已 superseded；
- 两个来源其实引用同一 Run；
- unreviewed edge；
- expired Intelligence；
- archived iteration；
- current vs yesterday；
- unauthorized evidence；
- high progress but zero evidence；
- multiple active iterations；
- stale source health。

### 10.3 指标

Retrieval：

- Scope Resolution Accuracy；
- Exact Entity Accuracy；
- Current State Accuracy；
- Temporal/As-of Accuracy；
- WorkItem Recall@5；
- Graph Path Recall；
- Duplicate Disambiguation Accuracy。

Control-plane：

- Status Accuracy；
- Overdue Accuracy；
- Blocker Precision/Recall；
- Dependency Path Accuracy；
- Acceptance Gate Accuracy；
- Evidence Coverage Precision/Recall；
- Missing Role Accuracy；
- Next Action Rule Accuracy；
- Intelligence Explanation Completeness；
- authoritative/derived classification accuracy；
- unsafe mutation rate。

System：

- current query P95；
- as-of query P95；
- Intelligence recompute P95；
- stale source rate；
- cache invalidation latency；
- audit completeness；
- ACL leakage。

### 10.4 消融

| Run | 改动 |
| --- | --- |
| W-B0 | 当前 Topic/Iteration substring |
| W-B1 | Project/Work/Decision typed views |
| W-B2 | structured filters/exact |
| W-B3 | temporal snapshot |
| W-B4 | dependency/blocker graph |
| W-B5 | EvidenceRequirement coverage |
| W-B6 | persistent IntelligenceRun |
| W-B7 | sparse+dense semantic |
| W-B8 | workspace reranker/context |

### 10.5 Release Gate

- Scope Resolution Accuracy ≥ 0.98；
- Current State Accuracy ≥ 0.99；
- Temporal Accuracy ≥ 0.97；
- Blocker Precision ≥ 0.95；
- Evidence Coverage Precision ≥ 0.95；
- Acceptance Gate Accuracy = 1.0；
- authoritative/derived classification = 1.0；
- unsafe mutation rate = 0；
- ACL leakage = 0；
- P95 满足预算。

## 11. 实施方案

### 11.1 Schema Migration

新增：

- `workspace_state_transitions`；
- `workspace_snapshots`；
- `work_item_dependencies`；
- `workspace_blockers`；
- `workspace_risks`；
- `workspace_decisions`；
- `acceptance_criteria` / `acceptance_checks`；
- `evidence_requirements`；
- `evidence_requirement_links`；
- `workspace_intelligence_runs`；
- `workspace_search_views`；
- `workspace_policy_versions`；
- `relation_tombstones`。

变更：

- Topic/Iteration 增加 version；
- IterationLink source domain 增加 notebook；
- role 改为 ontology；
- linked entity pin generation/version；
- AuditEvent 增加 before/after/version/causation/effective time。

### 11.2 新模块

```text
src/evidence_rag/workspace/
  state/
    transitions.py
    snapshots.py
    projection.py
  planning/
    dependencies.py
    blockers.py
    acceptance.py
    decisions.py
  evidence/
    requirements.py
    coverage.py
    freshness.py
  retrieval/
    builder.py
    structured.py
    temporal.py
    sparse.py
    dense.py
    reranker.py
    context.py
  intelligence/
    policies.py
    runner.py
    explanations.py
  evaluation/
    dataset.py
    metrics.py
```

当前 [workspace/intelligence.py](../../../src/evidence_rag/workspace/intelligence.py) 逐步拆分，
保留旧 API compatibility。

### 11.3 Feature Flags

- `workspace_views_v2`；
- `workspace_state_events_v2`；
- `workspace_temporal_queries`；
- `workspace_evidence_requirements`；
- `workspace_dependencies_v2`；
- `workspace_intelligence_runs_v2`；
- `workspace_reranker_v2`。

### 11.4 分阶段交付

#### Sprint W0：真实样本与基线

- 清理/明确两个同名 Topic，但不擅自删除；
- 建至少 2 Topic、3 Iteration、20 WorkItem fixture；
- 覆盖 running/review/blocked/done/cancelled；
- 建 40 条 Golden；
- 跑 W-B0。

#### Sprint W1：Typed Current Views

- Project/Topic/Iteration/WorkItem；
- exact display key；
- status/owner/due filters；
- ambiguity handling；
- hit-centered snippet；
- W-B1/W-B2。

#### Sprint W2：State/Temporal

- transition matrix；
- before/after/version；
- snapshot/projector；
- as-of；
- overdue/timezone；
- W-B3。

#### Sprint W3：Planning Graph

- dependency/blocker/risk；
- acceptance criteria/check；
- done gate；
- path retrieval；
- W-B4。

#### Sprint W4：Evidence Coverage

- requirements；
- role ontology；
- pinned external versions；
- freshness/review/independence；
- missing role；
- W-B5。

#### Sprint W5：Decision/Intelligence

- Decision；
- policy version；
- persistent IntelligenceRun；
- input lineage/expiry；
- source watermarks；
- W-B6。

#### Sprint W6：Semantic/Context/Governance

- sparse+dense；
- reranker；
- source preview；
- ACL；
- generation/rollback；
- W-B7/W-B8；
- release gate。

### 11.5 测试

Unit：

- transition matrix；
- optimistic concurrency；
- timezone/overdue；
- requirement coverage；
- independence/freshness；
- readiness policy；
- ambiguity resolver；
- authority labels。

Integration：

- Project→Topic→Iteration→Work→Evidence；
- blocked dependency→next action；
- criterion→check→done gate；
- external source generation update→stale link；
- snapshot→as-of；
- ACL-hidden evidence；
- Intelligence input lineage/expiry。

Regression：

- existing CRUD/router；
- dashboard；
- soft delete；
- PlatformEdge review；
- audit；
- unified query compatibility。

## 12. 单源完成定义

Workspace Source 只有满足以下条件才能进入多源联调：

- 40 条 Golden；
- Project/Topic/Iteration/WorkItem/Decision/Requirement 可直接检索；
- duplicate scope 不静默误选；
- status/owner/due/as-of 走 structured/temporal；
- dependency/blocker/acceptance graph；
- WorkItem done gate；
- EvidenceRequirement role coverage；
- external evidence pin version/generation；
- authoritative、observed、derived 分层；
- IntelligenceRun 持久化、可解释、可过期；
- read query 不产生 mutation；
- ACL、soft deletion、rollback 通过；
- 面试指标有真实 Run。

## 13. 最能讲的技术点

1. **Control-plane RAG**：把 Workspace 与内容语料分开，结构化状态和时间投影优先。
2. **Temporal State Retrieval**：Transition + Snapshot 支持 current/as-of，避免历史状态被
   最新字段覆盖。
3. **Evidence Requirement Coverage**：不按“链接数量/来源数量”粗略判断，而按 role、
   review、freshness、independence 与 pinned version 判定。
4. **Authority Separation**：authoritative state、observed source fact、rule-derived
   intelligence、LLM suggestion 分层。
5. **Persistent Explainable Intelligence**：stage/blocker/action 保存 policy、输入版本、
   reason、expiry，可复现“为什么当时推荐这一步”。
6. **Safe Action Boundary**：RAG 建议与状态变更隔离，mutation 需要显式意图、version 与
   approval。
