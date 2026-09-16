# G3 Canonical Query Plan、Scope、as-of 与一致快照可执行规格

版本：2026-08-06 V1  
状态：`G3D DESIGN_ENGINEERING_PASS / G2_GATE_HOLD / RUNTIME_NOT_STARTED`  
上位目标：`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`  
G2 authority candidate：`35_G2_LIVE_WIKI_GROUNDED_ANSWER_EXECUTION_SPEC.md`  
默认发布状态：`DEFAULT_V1 / QUALITY_HOLD`  

## 0. 本规格解决什么问题

当前 `/v1/query`、legacy/global multi-source 和 Wiki opt-in 都能接收相近的查询字段，但实际由不同
规划器、不同意图词表和不同 Scope 逻辑解释。同一个 `QueryRequest` 可能因入口、engine header、是否
配置 LLM、是否落入 V2 fallback 而得到不同来源、不同 required roles、不同时间点和不同回退行为。

本规格冻结 G3 的唯一目标：所有入口先产生同一个不可变 `CanonicalQueryPlanV3` 和
`SnapshotManifestV3`；普通、global V1/V2、Wiki V2 只做薄适配，不再各自拥有来源、Scope、as-of、
required-role 或 fallback 真值。

本轮仅完成代码现状审计、字段合同、状态机、迁移顺序、测试矩阵与 Gate artifact 设计。没有新增
runtime、没有访问或改写正式数据库、没有改变 V1 默认路由，也没有宣称 G3 通过独立评审。

## 1. 前置门禁与完成边界

### 1.1 解锁条件

G3 runtime 只有在以下条件同时成立后才允许开始：

1. G0 已由授权 owner 对 exact revision、排除项、remote CI 和 release evidence 作出 `QUALIFIED`；
2. G1 六源 raw binding/read contract 已 `QUALIFIED`；
3. G2 Wiki raw-verified grounded-answer Gate 已 `QUALIFIED`；
4. G3 implementation owner 明确接受本规格或其后继 reviewed revision；
5. 所有变更继续保持显式 opt-in，默认 V1 不变。

### 1.2 本规格的完成定义

`G3D DESIGN_ENGINEERING_PASS` 只表示：

- 当前 planner、Scope、snapshot、as-of、fallback 和 generation 分支已映射到代码；
- 语义漂移可由明确 case 复现；
- V3 合同、合并矩阵、算法、实施顺序、验证分母和 rollback 已冻结；
- 后续实现不需要再猜字段优先级或拒答语义。

它不表示 `CanonicalQueryPlanV3` 已编码、三入口已统一、真实 as-of 可用或 production 已获准。

## 2. 当前代码事实图

```text
QueryRequest
  ├─ UnifiedQueryService.preview
  │    ├─ QueryUnderstandingEngine.local
  │    └─ planner_v2.build_query_plan              # dict plan / preview only
  ├─ UnifiedQueryService.answer
  │    ├─ QueryUnderstandingEngine.understand      # local 或外部 structured LLM
  │    ├─ PlatformStore.resolve_scope              # 主要解析 code commit
  │    └─ PlatformService.search
  │         ├─ planner_v2.build_query_plan          # 每个 expanded query 再规划
  │         └─ optional MultiSourceRuntimeV2
  │              ├─ plan_multisource_query_v2       # typed plan，另一套 intent/role vocabulary
  │              ├─ 六源 snapshot/capability
  │              └─ V2 unavailable → legacy callback
  └─ X-RAG-Wiki-Engine: wiki_v1
       └─ WikiIntelligentQueryV1
            ├─ QueryUnderstandingEngine
            ├─ classify_wiki_query_v1
            ├─ required_roles=()
            └─ WikiNavigatorV1 → active manifest
```

### 2.1 权威代码入口

| 语义 | 当前入口 | 已观察事实 |
|---|---|---|
| public request | `models.py::QueryRequest/SearchScope` | `include` 与 `scope.source_types` 可同时存在；`as_of` 在 Scope 外；无冲突 validator |
| preview | `query/service.py::preview` | 使用 local understanding，`include or source_types`，硬编码 fallback project |
| ordinary answer | `query/service.py::answer` | 可由外部 LLM 改 intent/roles/subqueries；随后 platform 再规划 |
| legacy planner | `rag/planner_v2.py::build_query_plan` | dict contract；marker/intent 路由；两波 fallback |
| typed planner | `rag/multisource_foundation_v2.py::plan_multisource_query_v2` | typed contract；另一套 intent/role/task 表 |
| V2 snapshot | `rag/multisource_runtime_v2.py::_source_snapshot/prepare` | 六源逐个读取；单源 before/after generation check |
| legacy Scope | `platform/store.py::resolve_scope` | `as_of` 主要用于单仓库 Git commit；否则只是回显时间 |
| Wiki plan | `rag/wiki/query_v1.py`、`navigator_v1.py::_planned_roles` | 只读 `include`；理解结果 roles 被清空；按 query class 另行规划 |
| Wiki snapshot | `navigator_v1.py::navigate` | 始终选择 `active_manifest(project_id)`，请求 `as_of` 不参与选择/拒绝 |
| fallback | `platform/service.py`、`multisource_pipeline_v2.py`、`multisource_runtime_v2.py` | source wave、corrective retrieval、pipeline fallback、legacy callback 分层决定 |

## 3. 现状差异与风险清单

| ID | 代码事实 | 风险 | G3 处置 |
|---|---|---|---|
| QP-01 | ordinary 使用 `request.include or request.scope.source_types` | 两者冲突时 `include` 静默覆盖 | Scope resolver 显式合并；不一致 fail closed |
| QP-02 | Wiki 只使用 `request.include` | 同一请求在普通与 Wiki 路由来源不同 | 两入口消费同一 canonical source selection |
| QP-03 | Wiki 将 `required_roles=()` 传给 navigator | understanding roles 被丢弃，改用 Wiki class roles | canonical minimum roles 必须下传且不可被清空 |
| QP-04 | preview 只用 local understanding，answer 可用外部模型 | preview digest 不代表真实执行计划 | understanding receipt 固定后再生成唯一 plan；preview 返回同一 plan 类型 |
| QP-05 | platform 对每个 expanded query 再调用 legacy planner | 一次用户请求可产生多个彼此不同的 route plan | subquery 是 plan 内任务，不允许下游重规划来源 |
| QP-06 | API intent 含 `historical_implementation/change_trace` | typed V2 不识别并 fallback `global_synthesis` | 冻结单一九项 intent taxonomy 和明确 mapping |
| QP-07 | typed V2 含 `historical_change/notebook_debug/project_status` | public API 无法直接表达，adapter 语义不对称 | 额外任务变为 source task，不再冒充 canonical intent |
| QP-08 | `REQUIRED_ROLES`、typed `_INTENT`、Wiki class 各有 role 表 | coverage、corrective retrieval、拒答标准漂移 | EvidenceRoleV3 registry + intent minimum obligation set |
| QP-09 | `supports_as_of` 仅被构建，没有消费方 | 不支持来源仍被路由并收到 `as_of` | capability preflight 必须拒绝/剔除，禁止 silent current |
| QP-10 | legacy `resolve_scope` 仅在单仓库尝试由 `as_of` 解 commit | 多仓库/无 commit 时 as-of 可看似成功但未固定版本 | per-source TemporalTarget + VersionTarget；无法解析即 typed refusal |
| QP-11 | document V2 调用未下传 as-of；workspace 标记 temporal unavailable；Wiki 忽略 as-of | capability 声明与真实执行不一致 | capability 来自可验证 adapter contract，而非静态布尔声明 |
| QP-12 | V2 runtime 丢失 repository/thread/document/date/language/entity filters | V2 比 legacy Scope 更宽，可能越界召回 | ScopeResolutionV3 保留全部字段；adapter 必须证明无损投影 |
| QP-13 | 默认 project 多处硬编码 `project-rag` | 多项目下可能选择错误 project | 缺省只在唯一授权 active project 时解析；记录 provenance |
| QP-14 | 六源 snapshots 逐源、跨连接读取 | 可得到每源自洽但跨源不同时刻的混合快照 | coordinator/lease/consistent-read receipt + capture interval/skew gate |
| QP-15 | V2 只校验每源检索前后 snapshot 未变 | 查询展开、Wiki manifest 与其他来源仍可能跨代 | 整个 query 共享一次 snapshot manifest，所有 candidate/receipt 绑定它 |
| QP-16 | fallback 在 planner wave、pipeline、runtime、generator 多层存在 | 重复检索、证据丢弃后重跑、状态混淆 | 单一 FallbackPolicyV3；仅 preflight 可 exactly-once engine fallback |
| QP-17 | first-wave sufficiency 主要看 candidate count | required roles 缺失时可能不触发适合来源 | source wave 由 obligation coverage 决定，不以数量替代语义覆盖 |
| QP-18 | source status 在不同层使用不同字符串/别名 | timeout、no-match、not-indexed、unauthorized 可能被折叠 | PlanningDispositionV3 与 SourceOutcomeV3 两阶段 typed enums |
| QP-19 | ordinary 与 Wiki generation policy 分开定义 | 相同 Evidence Pack 状态可能一个生成、一个 retrieval-only | GenerationPolicyV3 是 canonical plan 的一部分 |
| QP-20 | legacy plan digest 不覆盖 Scope/snapshot/ACL | digest 相同不能证明执行相同 | V3 digest 覆盖 semantic request、resolved Scope、registry、snapshot、fallback/generation policy |

## 4. G3 不可变原则

1. 一个用户 interaction 只允许一个 canonical plan identity。
2. 入口 header、adapter 名称和 UI 页面不得改变 semantic plan。
3. 外部 LLM 只能提出 bounded understanding；不能绕过 canonical validator。
4. explicit intent 优先于 classifier，但仍必须通过 taxonomy 和 capability 校验。
5. explicit source allowlist 是上界；模型/source marker 不能擅自扩大。
6. intent minimum required roles 只能增加，不能由模型删除、重命名或降为 optional。
7. Scope 冲突必须在任何检索、远程推理或数据库快照写入前失败。
8. server 注入的 project/ACL authority 高于客户端内容，且不进入可伪造字段。
9. `as_of` 的唯一含义是“不得使用目标时点之后才有效或才可观察的证据”。
10. 不支持 exact as-of 的来源不能把 current 结果标成历史结果。
11. `as_of`、commit/revision、valid time、observed time、system capture time 必须分字段保存。
12. 每个 routed source 在执行前必须有 generation/watermark/capability receipt。
13. candidate、relation、raw-read receipt、Wiki manifest、citation 必须绑定同一 snapshot manifest。
14. active pointer 在 query 中途变化不得改变已 pin 的 query 结果。
15. no-match、not-indexed、unavailable、timeout、unauthorized、scope mismatch 不可互相替代。
16. fallback 不能因“结果少”或“答案不好”触发；它不是第二次机会式重跑。
17. 任一 V2 source 开始读取证据后，不得丢弃证据并隐式重跑 V1。
18. generation 只能消费已裁决 Evidence Pack，不能直接消费 planner candidate。
19. preview、execution、history、trace 必须暴露同一 plan/snapshot digest。
20. V3 迁移期间默认 V1、V1 schema 与 V1 active pointer 均不改变。

## 5. Canonical taxonomy

### 5.1 IntentV3

V3 只保留 public API 已有的九项 intent：

```text
current_implementation
historical_implementation
change_trace
rationale
experiment_validation
claim_verification
reproduction
staleness_check
global_synthesis
```

兼容映射：

| 旧值 | V3 值 | 说明 |
|---|---|---|
| `historical_change` | `change_trace` | `historical_implementation` 由是否要求差分区分，不能合并成一个旧值 |
| `notebook_debug` | 由 intent + notebook source task 表达 | 默认映射 `reproduction`；若只是错误定位则 `current_implementation` |
| `project_status` | `global_synthesis` + workspace `current` task | 不新增同义 public intent |
| unknown | 无自动 semantic fallback | 返回 `UNSUPPORTED_INTENT`；只允许 classifier 在未显式指定时选择合法 intent |

### 5.2 EvidenceRoleV3 最小集合

角色名是 evidence obligation，不是 source entity type。每个 intent 的 minimum roles：

| Intent | minimum required roles |
|---|---|
| current implementation | `implementation`, `current_version`, `validation` |
| historical implementation | `historical_implementation`, `target_version`, `version_provenance` |
| change trace | `change_delta`, `source_version`, `target_version`, `development_context`, `validation` |
| rationale | `decision_or_goal`, `alternatives`, `implementation` |
| experiment validation | `run_identity`, `metric_definition`, `metric_observation`, `dataset_version`, `code_version` |
| claim verification | `claim`, `source_location`, `support_or_counter`, `qualifier` |
| reproduction | `run_identity`, `code_version`, `configuration`, `dataset_version`, `environment`, `execution_command` |
| staleness check | `claim`, `original_version`, `current_version`, `diff_or_revalidation`, `counter_evidence` |
| global synthesis | `implementation`, `decision`, `validation`, `authoritative_state`, `source_diversity` |

Source adapter 通过 versioned role projection 把旧角色投影到 V3。投影表必须是 registry artifact 的一
部分；unknown role 不得用字符串相似度猜测。

## 6. `CanonicalQueryPlanV3` 合同

```text
CanonicalQueryPlanV3
  schema_version = canonical-query-plan-v3
  planner_version
  request_identity
    request_id
    interaction_id?
    question_sha256
    normalized_question_sha256
    semantic_request_sha256
  understanding
    intent
    resolution = explicit | local_reviewed | external_reviewed
    confidence
    ambiguity_codes[]
    entities[]
    temporal_expression?
    understanding_receipt_sha256
  scope: ScopeResolutionV3
  temporal_target: TemporalTargetV3
  capability_registry_sha256
  snapshot: SnapshotManifestV3
  obligations[]: EvidenceObligationPlanV3
  source_routes[]: SourceTaskPlanV3
  excluded_sources[]: SourcePlanningDispositionV3
  graph_templates[]
  budgets: QueryBudgetV3
  fallback_policy: FallbackPolicyV3
  generation_policy: GenerationPolicyV3
  stop_conditions[]
  planning_gaps[]
  release_route_receipt_sha256
  content_sha256
```

### 6.1 Plan identity

`content_sha256` 覆盖除自身外全部字段，包括 server-resolved ACL/project、source-specific filters、
temporal disposition、snapshot membership、fallback/generation policy。原始 question 可保留在进程内
execution envelope，但 portable plan 默认只保存 safe normalized representation 和 digest。

`request_id` 不是 semantic identity；重试可有新 transport ID，但相同 semantic request、authority 和
snapshot 必须产生相同 `semantic_request_sha256`。execution attempt 另有 attempt digest。

### 6.2 `ScopeResolutionV3`

```text
project_id
project_resolution = explicit | unique_authorized_default
project_authority_sha256
acl_refs[]
acl_enforced = true
source_selection_mode = auto | explicit
requested_sources[]
repository_targets[]: {repository_id, branch?, commit?}
thread_ids[]
experiment_ids[]
document_ids[]
languages[]
entity_types[]
date_interval?: {from?, to?}
source_task_parameters[]
scope_conflicts[]
content_sha256
```

V3 不使用一个全局 `commit` 代表多仓库。每个 repository target 都有独立版本。无法把 legacy commit
无歧义映射到唯一仓库时，resolver 返回 `AMBIGUOUS_VERSION_TARGET`，不执行检索。

### 6.3 `TemporalTargetV3`

```text
mode = current | as_of
requested_as_of?
normalized_as_of_utc?
valid_at?
observed_no_later_than?
system_capture_started_at
future_time_policy = reject
source_dispositions[]
content_sha256
```

每源 disposition 必须是：

```text
EXACT_BITEMPORAL
EXACT_VALID_TIME
EXACT_OBSERVED_CUTOFF
EXACT_VERSION_RESOLUTION
CURRENT_ONLY
UNSUPPORTED
```

`supports_as_of=true` 不能单独产生前四种状态；adapter 必须提供 reviewed capability test、实际 selector
和 snapshot receipt。`CURRENT_ONLY/UNSUPPORTED` 在显式 as-of 查询中不可执行。

### 6.4 `SourceCapabilityV3`

除 V2 字段外，至少增加：

```text
source_instance
supported_intents[]
supported_tasks[]
role_projections[]
filter_capabilities[]
temporal_capability
version_target_types[]
snapshot_strategy
snapshot_lease_supported
raw_read_authority_version
authorization_state
quality_state
calibration_receipt_sha256?
contract_test_receipt_sha256
valid_from / expires_at
content_sha256
```

registry 是被 reviewer/部署策略认可的 runtime evidence，不是静态 Python dict。过期、未授权、未测试或
quality hold 的 capability 不得被 planner 当成 ready。

### 6.5 `SnapshotManifestV3`

```text
snapshot_id
project_id
capture_started_at / capture_completed_at
consistency_mode = shared_transaction | source_leases | reviewed_manifest
max_capture_skew_ms
source_snapshots[]
  source / source_instance
  generation_id
  watermark
  observed_through?
  valid_interval?
  version_targets[]
  capability_sha256
  lease_or_manifest_receipt_sha256
  state = pinned | unavailable | unsupported | unauthorized
wiki_manifest_sha256?
content_sha256
```

同一 SQLite/database backend 的来源必须在同一 read transaction 中获取；外部来源必须提供 bounded
lease 或 immutable generation receipt。只记录六个“读取时看到的值”而无一致性证明，不满足 G3。

### 6.6 `EvidenceObligationPlanV3`

```text
obligation_id
role
required = true | false
acceptable_sources[]
minimum_authority
temporal_requirement
version_requirement
raw_or_reviewed_policy
completion_rule
```

### 6.7 `SourceTaskPlanV3`

```text
source
source_instance
planning_disposition = ROUTED
task
subquestions[]
required_obligation_ids[]
optional_obligation_ids[]
filters
temporal_disposition
snapshot_member_sha256
candidate_budget
token_budget
deadline_budget_ms
corrective_round_budget
adapter_contract_version
```

### 6.8 `FallbackPolicyV3`

```text
engine_fallback = disabled | preflight_only
fallback_target?
exactly_once_key
eligible_preflight_codes[]
forbidden_after = snapshot_acquired | source_started | evidence_observed
source_wave_policy = obligation_driven
generator_failure_policy = retrieval_only
content_sha256
```

### 6.9 `GenerationPolicyV3`

```text
requested = true | false
mode = grounded_only | disabled
answer_format
minimum_obligation_coverage
allowed_fact_authorities[]
counter_evidence_policy
as_of_claim_policy
provider_policy_receipt_sha256?
prompt_policy_sha256?
failure_policy = retrieval_only | refusal
```

## 7. Scope 合并与冲突矩阵

| 输入组合 | 解析 | 结果 |
|---|---|---|
| `include=[]`, `source_types=[]` | AUTO | understanding hints + intent defaults 仅作候选，仍过 capability/role gate |
| 仅 `include` 非空 | EXPLICIT | exact allowlist；canonical source order；不得扩大 |
| 仅 `source_types` 非空 | EXPLICIT | exact allowlist；兼容字段 provenance 保留 |
| 两者非空且集合相同 | EXPLICIT | 接受；顺序 canonical；记录 redundant input |
| 两者非空且集合不同 | conflict | `SOURCE_SCOPE_CONFLICT`；零检索、零外部 LLM |
| project 显式且 authorized | explicit | 使用该 project |
| project 缺失且唯一 authorized active project | resolved default | 使用唯一 project；记录 authority |
| project 缺失且 0/多个候选 | conflict | `PROJECT_SCOPE_UNRESOLVED` |
| server ACL 与 client ACL | server only | client 不可扩大；plan 记录 server authority digest |
| branch + commit | verify relation | commit 不属于 branch → `VERSION_SCOPE_CONFLICT` |
| 单 commit + 多 repository | ambiguous | 除非可唯一解析，否则拒绝 |
| explicit as-of + commit | both constraints | commit time 必须不晚于 as-of；每源还须支持对应 temporal selector |
| date_to 晚于 as-of | conflict | 不自动截断；返回 `TEMPORAL_SCOPE_CONFLICT` |
| model roles 少于 intent minimum | union with minimum | minimum 不可删除；记录 attempted downgrade |
| model source hint 超出 explicit allowlist | ignore + trace | 不扩大来源 |
| source filter adapter 无法表达 | unsupported | explicit source → refusal；auto source → exclude 并重算 role coverage |

Scope resolver 必须先完成纯字段检查，再决定是否允许调用 external understanding。包含冲突、未授权 project
或非法时间的请求不得把 question 发给外部服务。

## 8. Capability 与 as-of 路由规则

### 8.1 explicit source

用户显式要求的来源如果不支持 task/filter/as-of/version/raw-authority 任一 required capability：

- 该来源 disposition 为对应 `UNSUPPORTED_*`；
- plan decision 为 `REFUSE_BEFORE_RETRIEVAL`；
- 不以其他来源偷偷替代，不回退 current，不触发 legacy；
- public 输出使用安全 remediation，不泄漏未授权 source existence。

### 8.2 auto source

自动来源不支持 capability 时可被排除，但必须：

1. 保留 typed disposition；
2. 重算每个 required obligation 是否仍有至少一个 capable source；
3. 若有 obligation 无可用来源，则 `REFUSE_BEFORE_RETRIEVAL`；
4. 若仍可覆盖，允许执行，但答案必须带 completeness qualifier；
5. `source_diversity` 等跨源角色不能由一个来源重复计数满足。

### 8.3 当前六源 provisional temporal 状态

以下只是现状审计，不是 V3 capability 授权：

| Source | 当前声明 | 当前实际证据 | G3 前结论 |
|---|---|---|---|
| Code | supports | 单仓库可按 commit time 解析；多仓库不完整 | 未通过 V3 |
| Codex | unsupported | date filters 不等于 exact historical state | as-of 禁止 |
| Experiment | unsupported | 当前 structured query 无 historical snapshot selector | as-of 禁止 |
| Notebook | unsupported | 当前 query 无 revision-as-of 统一 selector | as-of 禁止 |
| Document | supports | V2 search 调用未下传 as-of | 声明/执行不一致，禁止 |
| Workspace | supports | context 明示 `UNAVAILABLE_WITH_AUDIT_EVIDENCE` | 不可声称 exact as-of |
| Wiki | request carries as-of | navigator 仍选 active manifest | as-of 禁止 |

任何来源只有在新增 reviewed contract tests 后才能从 provisional 状态升级。

## 9. Canonical planning algorithm

```text
1. 解析 transport request，验证大小、格式、重复 ID 和非法时间。
2. 注入 server project/ACL/release authority；执行 pre-understanding Scope conflict check。
3. 归一化 question；若 explicit intent，验证 IntentV3；否则产生 bounded understanding receipt。
4. 合并 intent minimum roles 与经 allowlist 校验的附加 roles。
5. 合并 include/source_types/project/repository/source-specific filters，生成 ScopeResolutionV3。
6. 解析 TemporalTargetV3；验证 future、date interval、commit/branch/repository constraints。
7. 加载 exact reviewed CapabilityRegistryV3；拒绝过期/未授权 capability。
8. 为候选来源计算 task/filter/temporal/version/role dispositions。
9. explicit unsupported → pre-retrieval refusal；auto unsupported → exclude 后重算 obligation feasibility。
10. 协调并 pin SnapshotManifestV3；验证 capture consistency、generation、watermark 和 Wiki manifest。
11. 为每个 obligation 建 source task/subquestion/budget；下游不得再改变 sources/roles/scope。
12. 固定 obligation-driven waves、corrective budget、exactly-once fallback 和 generation policy。
13. canonical serialize，计算 plan digest；写 immutable planning receipt。
14. ordinary/global/Wiki adapter 仅投影 plan；投影必须通过 lossless roundtrip validator。
15. execution 所有 source batch/candidate/raw receipt/citation 回绑 plan+snapshot digest。
16. 输出 typed source outcomes，执行 answerability；generator 只消费裁决后的 pack。
```

步骤 10 失败时不能保留半个 snapshot 继续执行。若某外部 source lease 在 plan freeze 前过期，整体重新规划
为一个新 attempt；不得复用旧 plan digest。

## 10. 三入口 adapter 合同

### 10.1 Public/ordinary adapter

- `QueryRequestV1Adapter` 只做字段合法化和兼容投影；
- `UnifiedQueryService.preview` 与 `answer` 调用同一 planner service；
- preview 可以不执行 retrieval，但必须在指定 snapshot mode 下返回与 execution 同类型合同；
- expanded subqueries 已在 `SourceTaskPlanV3` 内，下游 `PlatformService` 不再重新选择 sources；
- history 记录 plan/snapshot digest、decision 和安全 limitations，不保存 secret/raw bytes。

### 10.2 Legacy/global adapter

- `build_query_plan` 降为 V3 plan 的只读兼容 projection；
- V1 platform wave 只能执行 `source_routes` 和预算，不再看 query marker 重新规划；
- `GlobalSearchRequest` 必须携带 plan receipt 或由 trusted internal call 创建；
- repository/thread/experiment/document/date/ACL/version filters 无损下传；
- V2 typed source task 使用同一 V3 task/role projection；unknown mapping 在检索前失败。

### 10.3 Wiki adapter

- Wiki 不再独立用 `classify_wiki_query_v1` 决定 required roles；query class 只影响导航策略；
- `required_sources` 和 `required_roles` 均来自 V3 plan；
- Wiki manifest 必须是 `SnapshotManifestV3.wiki_manifest_sha256` 指定版本，不读 active pointer 决策；
- manifest 的六源 generation 必须与 snapshot 成员一致；
- as-of capability 不满足时在导航前拒绝；
- Wiki page-only、raw/read receipt 和 claim policy继续遵循 G2 V3 Evidence Pack 规格。

### 10.4 Adapter parity

同一 semantic request、authority、capability registry 和 snapshot 输入，经三 adapter 产生的以下字段必须完全
一致：intent、required obligations、source routes、source tasks、Scope、TemporalTarget、snapshot membership、
fallback policy、generation policy、plan digest。入口特有字段只能放 execution projection，不得进入语义差异。

## 11. Snapshot 与执行状态机

### 11.1 Snapshot acquisition

```text
UNRESOLVED
  → AUTHORITY_RESOLVED
  → CAPABILITY_RESOLVED
  → ACQUIRING
  → PINNED | SNAPSHOT_UNAVAILABLE
  → PLAN_FROZEN
```

在 `PINNED` 前不得读取候选。在 `PLAN_FROZEN` 后 active pointer 变化只产生 drift telemetry，不改变本次
query。source adapter 必须在 receipt/lease 边界读取；单纯 before/after 对比仍保留为 tamper guard，但不
替代 lease/transaction consistency。

### 11.2 Planning disposition

```text
ROUTED
NOT_REQUESTED
UNSUPPORTED_INTENT
UNSUPPORTED_TASK
UNSUPPORTED_FILTER
UNSUPPORTED_AS_OF
UNSUPPORTED_VERSION
UNAUTHORIZED
NOT_INDEXED
UNAVAILABLE
QUALITY_HOLD
```

### 11.3 Execution outcome

```text
COMPLETE
PARTIAL
NO_MATCHING_EVIDENCE
TIMEOUT
UNAVAILABLE
UNAUTHORIZED
NOT_INDEXED
SCOPE_MISMATCH
SNAPSHOT_MISMATCH
CONTRACT_FAILURE
CANCELLED
```

Planning disposition 与 execution outcome 不得共用一个模糊 `status` 字符串。`NO_MATCHING_EVIDENCE` 只有
在 source 真正执行了完整、合法、snapshot-bound 查询后才能产生。

## 12. Fallback、corrective retrieval 与 generation

### 12.1 Engine fallback

允许 V2→V1 的唯一时点是：control-plane/preflight 已判断 V2 不可用，且尚未 acquire snapshot、尚未启动
任一 source、尚未观察任何 evidence。fallback 使用一次性 idempotency key；同一 attempt 最多一次。

以下情况禁止 engine fallback：

- no matching evidence；
- required role 缺失；
- source timeout/partial；
- snapshot mid-query mismatch；
- candidate/claim verification failure；
- generation provider failure；
- explicit Wiki engine；
- explicit V2 strict policy。

这些情况返回 typed partial/refusal/retrieval-only，不能用另一个 engine 的 current 数据掩盖。

### 12.2 Source waves 与 corrective retrieval

- 第一波按 obligation/authority/task 选择，不以 candidate 数量决定充分性；
- fallback source 实际是 plan 内的 optional second wave，不是 engine fallback；
- 只有未满足 obligation 且仍有预算时才允许 corrective round；
- corrective query 不得改变 Scope、snapshot、source task 或 required-role semantics；
- 每轮记录新增 eligible facts 和 role gain；零增益立即停止；
- timeout/cancel 后后台 task 不得继续读取或污染 cache。

### 12.3 Generation

普通与 Wiki 使用相同 `GenerationPolicyV3`：

- evidence mode 永不构建外部 client；
- required obligation 或 as-of authority 不完整时不调用 generator；
- generator unavailable/malformed/citation failure → retrieval-only，不重跑 retrieval；
- 生成 claim 必须引用同一 plan/snapshot 的 Evidence Pack；
- external provider/model/prompt policy receipt 进入 trace 和 answer authority；
- provider 不得改 intent、Scope、source selection 或 evidence membership。

## 13. 原子实施顺序

| 顺序 | ID | 唯一主结果 | 允许范围 | 退出证据 |
|---:|---|---|---|---|
| 1 | T3.1.1 | IntentV3/EvidenceRoleV3 registry | 新 contract + mapping tests | 九 intent 无 fallback 漂移；角色投影显式 |
| 2 | T3.1.2 | ScopeResolutionV3 contract | models/resolver pure functions | merge/conflict property tests |
| 3 | T3.1.3 | TemporalTargetV3 contract | parsing/normalization only | UTC/future/interval/version matrix |
| 4 | T3.2.1 | CapabilityRegistryV3 | registry/receipt verifier | unsupported/expired/forged fail closed |
| 5 | T3.3.1 | SnapshotManifestV3 coordinator | isolated store/lease adapters | atomic/lease/skew/mid-swap tests |
| 6 | T3.4.1 | CanonicalQueryPlanV3 builder | pure plan module | canonical/tamper/copy/property tests |
| 7 | T3.4.2 | planning receipt/store | immutable audit store | CAS/idempotency/retention/ACL tests |
| 8 | T3.5.1 | public request adapter | QueryRequest compatibility only | lossless field projection |
| 9 | T3.5.2 | legacy/global adapter | planner/platform projection | no downstream replan；filters preserved |
| 10 | T3.5.3 | typed V2 adapter | source task projection | historical/change intents不退化 |
| 11 | T3.5.4 | Wiki adapter | roles/sources/manifest/as-of projection | no independent semantic plan |
| 12 | T3.6.1 | SourceOutcomeV3 + exactly-once fallback | execution coordinator | preflight-only/fault matrix |
| 13 | T3.6.2 | obligation-driven corrective policy | wave coordinator | zero-gain stop；no count-only sufficiency |
| 14 | T3.7.1 | unified generation policy | ordinary/Wiki answer adapters | same pack same generation decision |
| 15 | T3.8.1 | three-entry parity replay | fixtures/isolated runtimes | same request same plan/snapshot digest |
| 16 | T3.9.1 | portable Gate + independent review | artifact/verifier only | `QUALIFIED` 或明确 HOLD/FAIL |

每一任务只关闭一个主结果。T3.1–T3.4 不接 API、不建正式表；T3.5 不切默认；T3.8 不访问未授权
production 数据；T3.9 前不进行 shadow/canary。

## 14. 验证矩阵

| ID | 场景 | 必需断言 |
|---|---|---|
| CP-01 | Canonical plan roundtrip | bytes/digest portable、顺序稳定 |
| CP-02 | plan 字段 tamper | verify FAIL |
| CP-03 | explicit `change_trace` | V2 intent 不得变 global synthesis |
| CP-04 | explicit `historical_implementation` | 保持该 intent；正确 historical roles |
| CP-05 | old `historical_change` adapter | 显式映射且有 migration trace |
| CP-06 | model 删除 minimum role | role 仍 required；记录 downgrade attempt |
| CP-07 | empty include/source_types | AUTO 且 capability-gated |
| CP-08 | include only | exact explicit allowlist |
| CP-09 | source_types only | exact explicit allowlist |
| CP-10 | 两字段同集合不同顺序 | canonical PASS |
| CP-11 | 两字段集合冲突 | pre-retrieval refusal；source calls=0 |
| CP-12 | model hint 扩大 explicit source | 不扩大；trace 可审计 |
| CP-13 | project missing/唯一 authorized | 明确 default provenance |
| CP-14 | project missing/多个 authorized | refusal；不猜 `project-rag` |
| CP-15 | client ACL 扩权 | server ACL 胜出；无 existence leak |
| CP-16 | repository/thread/document/date filters | ordinary/V1/V2/Wiki 投影无损或 typed unsupported |
| CP-17 | branch/commit 不一致 | `VERSION_SCOPE_CONFLICT` |
| CP-18 | 多仓库 + 单 commit ambiguous | pre-retrieval refusal |
| CP-19 | invalid/future as-of | pre-retrieval refusal |
| CP-20 | date_to > as-of | temporal conflict；不自动截断 |
| CP-21 | explicit unsupported-as-of source | `UNSUPPORTED_AS_OF`；不回退 current/V1 |
| CP-22 | auto unsupported source仍有角色覆盖 | exclude + completeness qualifier |
| CP-23 | auto exclude 后角色无来源 | pre-retrieval refusal |
| CP-24 | capability 声明支持但 contract receipt 缺失 | QUALITY_HOLD/unsupported |
| CP-25 | Wiki as-of + current active manifest | 不得读取；需 exact reviewed manifest |
| CP-26 | snapshot shared transaction | 六源 membership 同一 receipt boundary |
| CP-27 | external lease 过期 | plan freeze 失败；source calls=0 |
| CP-28 | active generation mid-query swap | pinned result不变；drift recorded |
| CP-29 | source candidate wrong generation | `SNAPSHOT_MISMATCH`；candidate=0 |
| CP-30 | cross-source capture skew 超限 | snapshot acquisition FAIL |
| CP-31 | preview vs execution | same authority/snapshot → same plan digest |
| CP-32 | ordinary/global/Wiki parity | semantic fields与digest一致 |
| CP-33 | V2 preflight unavailable | V1 callback exactly once |
| CP-34 | V2 source 已开始后 timeout | V1 callback=0；typed partial/refusal |
| CP-35 | no matching evidence | fallback=0；`NO_MATCHING_EVIDENCE` |
| CP-36 | required-role missing | corrective only within same plan/snapshot |
| CP-37 | corrective zero gain | 立即停止；round count精确 |
| CP-38 | generator unavailable | retrieval-only；retrieval不重跑 |
| CP-39 | evidence mode | external understanding/generator policy按合同禁用；client构建=0 |
| CP-40 | portable artifact copy/tamper | copy verify PASS；tamper FAIL |
| CP-41 | default request without V3 opt-in | V1 behavior/bytes unchanged |
| CP-42 | rollback rehearsal | V3 stores关闭；V1 schema/data/pointer digest不变 |

Parity cases 必须固定 public request、server authority、capability registry 和 snapshot fixture；否则“同请求”
没有可比较的完整前提。故障注入至少覆盖 timeout、unauthorized、not-indexed、capability expiry、generation
swap、lease expiry、LLM invalid JSON、adapter field loss 和 fallback callback exception。

## 15. G3 Gate artifact 合同

`rag-maturity-g3-canonical-plan-v1` 至少包含：

```text
schema_version
reviewed_revision
g2_package_sha256
intent_role_registry_sha256
scope_temporal_contract_sha256
capability_registry_fixture_sha256
planner_authority_sha256
adapter_authorities[]
case_membership_sha256
case_count / passed / failed / skipped
parity_case_count
parity_mismatch_count
scope_conflict_case_count
unsupported_capability_route_count
silent_current_fallback_count
snapshot_mismatch_accepted_count
cross_source_skew_accepted_count
post_start_engine_fallback_count
duplicate_fallback_execution_count
filter_projection_loss_count
required_role_downgrade_count
default_v1_drift_count
portable_copy_verification
rollback_receipt_sha256
test_environments/results
limitations
independent_review_decision
content_sha256
```

Hard exit：

- CP-01–42 全部执行且 `failed=0`、`skipped=0`；
- 三入口 parity mismatch = 0；
- unsupported capability route、silent current fallback、filter projection loss = 0；
- snapshot mismatch/skew accepted = 0；
- post-start/duplicate engine fallback = 0；
- required-role downgrade = 0；
- default V1 drift = 0；
- portable copy verify PASS、tamper FAIL；
- independent reviewer 明确 `QUALIFIED`。

## 16. Stop 与 rollback

出现以下任一情况立即停止 G3 实现：

- 为兼容旧 planner 继续保留两个可独立决定 sources/roles 的真值；
- 看到 `as_of` 后仍路由 `CURRENT_ONLY/UNSUPPORTED` source；
- Wiki 以 active manifest 代替 V3 pinned manifest；
- typed V2 丢弃 repository/thread/document/date filter；
- 模型输出可以移除 minimum roles 或扩大 explicit sources；
- snapshot 未固定就读取 candidate，或中途换代后继续接受；
- V2 已读 evidence 后隐式调用 V1；
- no-match、timeout、unauthorized 被当作同一个 fallback reason；
- plan digest 不覆盖 Scope、ACL authority、snapshot 或 generation policy；
- V3 opt-in 测试改变 V1 default、schema、active pointer 或历史 artifact。

Rollback：关闭 V3 planner/project allowlist 和 V3 writers，所有入口恢复原 V1 release route；停止新的 V3
planning/snapshot receipt 写入，保留已有 receipts、failure artifacts 和 telemetry 供审计；不删除、不反向改写
V1/V2 数据。若 V3 plan store 损坏，从 immutable request-authority/capability/snapshot receipts 重建新的 plan
attempt，不就地修补原 digest。

## 17. 当前判定与下一步

本轮关闭的是 `WP-G3D-01 Canonical planning execution design` 的本地设计部分：完成四套 planner/Scope/
as-of/fallback 代码映射，冻结 V3 taxonomy、contracts、merge matrix、snapshot/adapter/fallback/generation
算法、16 个原子实施任务、42-case Gate 和 rollback。

当前关键路径仍是：

```text
current G0 owner review candidate
  → G0 QUALIFIED
  → G1 QUALIFIED
  → G2 QUALIFIED
  → T3.1.1
  → T3.9.1 independent G3 review
```

G2 未通过前不得开始 T3.1.1。本规格是 implementation authority candidate，不是运行证据、独立评审、
正式 as-of capability 声明或发布许可。
