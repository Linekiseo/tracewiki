# Codex Source RAG 开发计划与完成证据

状态：X0-01 Foundation `PASS`；X-B0 `FIXED BASELINE CONTROL ACCEPTED —
NON_QUALIFIED`；CX1-01/CX1-02 engineering `COMPLETE`；X1 engineering
`COMPLETE`、production treatment `UNAVAILABLE`；X-T1
`NO_RESULT / NOT_QUALIFIED / UNAVAILABLE`，retry `NOT_AUTHORIZED`；X2–X5
仓库内完整 engineering `COMPLETE / OPT_IN / QUALITY UNAVAILABLE`；正式库 migration、
production treatment 与发布仍 `EXTERNAL_BLOCKED`，默认 V1  
前序状态：Code C0–C7 overall engineering `COMPLETE`；Release
`HOLD_DEFAULT_V1 / NOT RELEASED / default V1`  
开发方案版本：codex-source-development-v1  
日期：2026-07-29  
统一开发契约：[00_SINGLE_SOURCE_DEVELOPMENT_CONTRACT.md](00_SINGLE_SOURCE_DEVELOPMENT_CONTRACT.md)  
目标设计：[02_CODEX_SOURCE_RAG.md](../sources/02_CODEX_SOURCE_RAG.md)  
面试材料：[02_CODEX_SOURCE_INTERVIEW.md](../interview/02_CODEX_SOURCE_INTERVIEW.md)

## 1. 状态真值与执行边界

本文件把 Codex 单源设计拆成可独立评审的 Issue/PR，并同步已形成的 X0/X-B0
Evidence。X0 Foundation 已通过；X-B0 已被接受为固定负向 baseline control，但质量
`NON_QUALIFIED`。X1 engineering 已完成，但唯一 production attempt 在 artifact 发布前
失败，未形成 treatment evidence；旧 treatment 不重试。2026-07-29 用户改为单会话
完整开发后，另行完成了不依赖该 treatment 的仓库内 V2 contracts、isolated store、
retrieval、context 与 governance。它不是新的 quality authorization：

| Milestone | 当前状态 | 已有实现/Run |
| --- | --- | --- |
| X0 Golden/Evaluation/Foundation | Foundation `PASS` | released Golden `codex-golden-v1/v1`；X-B0 fixed control accepted、`NON_QUALIFIED` |
| X1 Event Normalization | engineering `COMPLETE`；production treatment `UNAVAILABLE` | CX1-01/CX1-02 COMPLETE；X-T1 `NO_RESULT / NOT_QUALIFIED / UNAVAILABLE` |
| X2 Episode V2 + Retrieval Unit | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | goal-aware episode、versioned units/temporal edges、isolated additive store、atomic publish/rollback |
| X3 Retriever/Reranker | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | exact/sparse/local dense cache/temporal graph、deterministic rerank/calibration/adaptive-k |
| X4 Context/Comparison | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | four task templates、timeline/comparison、stable citation、missing/uncertainty |
| X5 Incremental/Privacy/Release | `ENGINEERING COMPLETE / HOLD_DEFAULT_V1` | append cursor、privacy tombstone、shadow aggregator、stable routing、release/rollback evaluator |

必须同时保留以下边界：

- 设计文档中的 5 个 Thread、127 个 Turn、6,178 个 Item/View 是既有设计的历史观察；
  本轮没有在当前工作区重新测量这些数字。
- 当前正式库继续标记为 `EXTERNAL_MUTABLE_SERVICE_OWNED`。本次 Evidence Sync 不以其
  当前内容、哈希或 sidecar 状态为证据，也不对其是否变化作声明。
- 所有测试、Golden、Baseline、Treatment、shadow/canary rehearsal 只能使用
  `tmp_path`/临时目录、内存对象、临时 SQLite 和仓库内 immutable synthetic fixtures。
- Foundation Gate 前只允许新增 Codex evaluation 包、测试和 fixtures；不得修改现有
  `codex_adapter.py`、`codex_ingestion.py`、`codex_retrieval.py`、
  `codex_store.py`、`db_schema.py`、`models.py`、`config.py`、`runtime.py` 或 API。
- 工程 Gate 与质量结论分开。Treatment 未达到质量门槛时，保留可验证 artifact 和失败
  slice，仍可继续后续工程；安全/ACL/事实状态硬门失败则阻塞后续授权。
- released Golden 与 X-B0/correction 已有真实证据；X-T1 只有
  `FAILED_BEFORE_PUBLISH` attempt audit，没有 Run/artifact/metrics；不填写任何优化提升
  数字。
- 本文件后续 X2–X5 的原始 Gate/Run/DDL 章节已同步仓库内工程实现。只有正式库
  migration/backfill、production treatment、真实 shadow/canary 和 default switch 仍未做。

## 2. 当前真实实现映射

### 2.1 Runtime 与摄取链

当前 `create_runtime()` 初始化单一 `SQLiteStore`、`LocalHashEmbedding`、
`CodexSessionAdapter`、`CodexIngestionService` 和 `CodexHybridRetriever`：

```text
POST /v1/ingestion/codex
→ CodexIngestionService.enqueue/run
→ CodexSessionAdapter.discover/matches_project/parse
→ RawSourceService.accept/persist_bytes
→ CodexStoreMixin.prepare_codex_generation
→ 每个 CodexItem 生成一个 CodexSearchView
→ local-hash-v2 embedding
→ CodexStoreMixin.publish_codex_generation
→ active_generation_id 原子切换
```

真实行为：

- `discover()` 支持 Codex home、sessions 目录或单个公开 JSONL；archived 默认关闭；
- 项目过滤先读取 metadata 前缀，单文件受 `max_codex_session_bytes` 限制；
- `parse()` 逐行容错，排除 `reasoning`、`agent_reasoning`、`token_count` 和
  system/developer role；
- 文本在持久化和 embedding 前清洗、secret redaction、字符上限截断；
- Thread/Turn/Item URI 稳定；ToolCall/Command/Patch 与 ToolResult 按 `call_id` 建
  `OUTPUT_OF`；
- 验证命令必须命中 allowlist 且观察到整数 exit code 才派生 `ValidationResult` 和
  `VALIDATED_BY`；
- 每个 Turn 固定派生一个 `DevelopmentEpisode`，并非 Goal-aware Episode；
- 空重建失败时保留 last-known published generation，但当前摄取仍是全 generation 重建。

### 2.2 检索链

```text
POST /v1/codex/search
→ CodexSearchRequest
→ CodexHybridRetriever.search
├── FTS5 lexical candidate（limit × 8）
├── active generation 全量 dense candidate
├── local-hash-v2 cosine
└── identifier-aware 固定权重融合
→ Top-K
→ optional 一跳 edges
```

当前融合 trace 为 `codex-weighted-hybrid-v2`。所有 View 使用同一 embedding profile；
没有字段化 sparse、专用 dense profile、temporal/event graph candidate、Codex
reranker、calibration、adaptive-k 或 task-aware quota。date scope 只是过滤，不是事件
顺序检索。

加速实现新增 `rag/codex_v2.py`：它只调用上述生产 V1 retriever 一次，在返回候选上派生
稳定 episode、task-aware temporal rerank、negative reasons 与
`codex-timeline-context-v2`；Direct/Platform/Query 仅在显式
`X-RAG-Codex-Engine: v2` 时使用，默认仍执行原 V1。它没有声称替代本计划的专用
fielded/dense/event graph store，也没有新的 treatment quality 结果。

### 2.3 Store、Schema 与索引

当前 DDL owner 是 `SQLiteStore.initialize()` 调用的 `BASE_SCHEMA`。Codex 表为：

- `codex_sources`、`codex_generations`；
- `codex_threads`、`codex_turns`、`codex_items`；
- `codex_edges`；
- `codex_search_views` 与外部内容 FTS5 表/触发器。

`CodexStoreMixin` 负责 generation prepare/publish/fail、列表/detail/timeline/audit、
lexical/dense candidate 和 edge lookup。读取均以 `active_generation_id` 为主，但旧
generation 数据保留。当前没有 episode version、derived fact、retrieval unit、event
link、append cursor、tombstone、calibration 或 release ledger 表。

### 2.4 API 与数据模型

现有 V1 公共接口必须保持兼容：

| Endpoint | 当前契约 |
| --- | --- |
| `GET /v1/codex/config` | source/project defaults |
| `GET /v1/codex/stats` | ready source 聚合计数 |
| `POST /v1/ingestion/codex` | `CodexIngestRequest`，异步 workflow |
| `GET /v1/codex/projects/{project_id}/sync-status` | discovered/indexed/new/changed 等 |
| `POST /v1/codex/projects/{project_id}/sync` | force/archive/max_sessions |
| `GET /v1/codex/sources` | ACL 过滤来源 |
| `GET /v1/codex/sessions` | project/status/query/subagent/limit/offset |
| `GET /v1/codex/sessions/{thread_id}` | 原始 Thread/Turn/Item/edge detail |
| `GET /v1/codex/sessions/{thread_id}/timeline` | compact timeline，默认无 operations |
| `GET /v1/codex/sessions/{thread_id}/turn-audit` | 单 Turn operations/file changes |
| `POST /v1/codex/search` | query/scope/limit/include_edges |
| `/v1/codex/comparisons*` | 两到八个 Thread 的持久化确定性对照 |

`CodexSearchScope` 当前只有 project、thread、item type、status、date 和内部 ACL 字段。
任何 V2 request field 都必须 opt-in 且有 V1 fallback；现有 response 字段不得删除、
改名或改变类型。

### 2.5 现有 comparison、binding 与多源耦合

- `CodexComparisonService` 从当前 Thread detail 计算 Goal、command、path、validation、
  keyword decision candidate 和 outcome；它不是检索结构，也没有 Episode V2 对齐。
- `BindingService` 使用 Codex FileChange/Patch path 生成 Codex→Code 候选；只有人工确认
  才形成 confirmed relation。Codex 单源不能自行宣称 patch 已 commit。
- `PlatformService` 把 Codex search result 投影到统一结果并参与多源排序；多源 query
  planning/calibration/fusion 留到多源阶段。
- `RawSourceService` 持久化已清洗 Item payload 和 derivation；V2 必须继承 ACL 与
  provenance，不能绕过该边界。
- `UnifiedQueryService` 可选择 Codex source，但本轮不重写最终 LLM answer prompt。

### 2.6 当前测试与缺口

现有测试已覆盖：

- rollout 与公开 `codex exec --json` 的摄取；
- reasoning exclusion、secret redaction、transport noise 清洗；
- ToolCall/ToolResult、ValidationResult、patch detail；
- project sync、空重建保留旧 generation；
- search/API/timeline/turn audit/graph；
- deterministic comparison 和 Codex→Code binding。

X0 Foundation 已补齐：

- 严格 45-case Codex Golden；
- Codex evaluation schema/loader/runner；
- X-B0 immutable Run 与权威 offline correction overlay；
- eligible denominator、slice/error 与 portable/security evidence；

当前仍没有：

- X1 released treatment quality evidence；
- Episode boundary、temporal path、reranker 或 timeline context 的离线质量测试；
- V2 flags、shadow/canary/release 证据。

## 3. 本轮负责、不负责与依赖

### 3.1 本计划负责

- Codex 单源的 Golden、evaluation、baseline 与 portable artifact；
- observable event state truth；
- Goal-aware Episode V2 与 Retrieval Unit；
- exact/sparse/dense/temporal candidate、Codex reranker/calibration；
- timeline context 与 normalized comparison；
- append sync、privacy propagation、shadow/canary/release controller；
- 每阶段工程 Gate、隔离 Run、rollback 和面试证据同步。

### 3.2 留到多源层

- Code/Codex/Experiment/Notebook/Document/Workspace 的统一 query planner；
- 跨源分数 calibration 与全局 reranker；
- Codex patch→commit 的自动最终裁决；
- Claim→Run→Code 联合证据；
- 全局 answer prompt 和跨源 token budget。

### 3.3 本轮不做

- 不读取或迁移正式库；
- 不原地改 stable Thread/Turn/Item URI；
- 不删除旧 Search View、表或 generation；
- 不把 AgentMessage/Plan 当作执行或验证事实；
- 不重建隐藏 reasoning；
- 不引入图数据库、向量数据库、外部守护进程；
- X0–X2 不增加远程模型或网络依赖；
- learned embedding/cross-encoder 只能在 CX3 独立 benchmark 后 opt-in，不能成为默认
  或工程 Gate 的必需依赖。

## 4. 全局兼容与迁移契约

### 4.1 Stable entity 与原始证据

- `CodexThread`、`CodexTurn`、`CodexItem` 和现有 URI 语法不变；
- 原始 Item 不随 Episode builder、embedding 或 reranker 升级而改写；
- 新 Episode、fact、unit、event link 都带 version、generation、ACL、source locator；
- derived decision/alternative 必须标为 candidate 并引用用户可见文本；
- `committed` 只能来自 confirmed 跨源 binding。

### 4.2 Additive-only migration

P0 阶段只允许 additive DDL。计划表：

| 首次使用 | 新表/索引 | Owner | Backfill/兼容 | Rollback |
| --- | --- | --- | --- | --- |
| CX1 | `codex_derived_facts`、`codex_event_links`、诊断索引 | Codex V2 store | active generation opt-in shadow backfill；V1 不读取 | 关 flag，保留表 |
| CX2 | `codex_episode_versions`、`codex_episode_members`、`codex_retrieval_units` | Codex V2 store | 新 builder generation；Turn Episode 仍可读 | 切回 turn-v1/item-v1 |
| CX3 | temporal/unit indexes、`codex_retrieval_calibration` | Codex V2 store | shadow publish，model/profile version 并存 | 关 graph/dense/reranker |
| CX5 | append cursor、session tombstone、release/shadow audit | Codex V2 store | cursor 缺失时全 rebuild fallback | 关 append，使用 last-known-good |

规则：

- DDL 必须独立 schema module，由 `SQLiteStore.initialize()` 的 additive hook 创建；
- 不在正式库运行 migration；工程 Gate 仅在空临时库和从 immutable V1 fixture 构造的临时
  兼容库上验证；
- backfill 分批、有界、幂等；active generation 切换仍为原子操作；
- dual-read/write 期间 V1 为真值 fallback，新表异常不得污染 V1 response；
- cleanup 只在稳定观察期后另行授权，本计划不删除旧数据；
- integrity check 至少验证 generation membership、FK/唯一性、ACL 一致性、孤儿边、
  version/profile 完整性和 rollback 后 V1 可读。

### 4.3 API compatibility

后续可新增的 opt-in 字段必须先经 contract test：

| 对象 | Additive field | Default/回退 |
| --- | --- | --- |
| `CodexSearchRequest` | `engine: v1\|v2\|auto`、`task`、`order`、`context_mode` | 默认 `v1`；非法值 422；V2 不可用时按 policy 回退 |
| `CodexSearchScope` | paths/tools/frameworks/require_exit_code/episode_ids | 空值保持当前 scope |
| search response | `engine`、`fallback`、`candidate_trace`、`context`、`warnings` | 保留全部 V1 字段与类型 |
| sync request/status | append policy/cursor diagnostics | 默认 full-rebuild-compatible |
| comparison create/result | `schema_version`、V2 normalized snapshot | 默认 V1 comparison |

客户端未发送新字段时，结果必须与当前 V1 contract 等价。deprecation 在 V2 稳定观察期前
禁止启动。

### 4.4 Feature flags

计划 flags 与默认值：

```text
RAG_CODEX_ENGINE=v1
RAG_CODEX_EPISODE=turn-v1
RAG_CODEX_UNIT=item-v1
RAG_CODEX_EMBEDDING=local-hash-v2
RAG_CODEX_EVENT_GRAPH=false
RAG_CODEX_RERANKER=off
RAG_CODEX_CONTEXT=snippet-v1
RAG_CODEX_SYNC=full-v1
RAG_CODEX_SHADOW=false
RAG_CODEX_CANARY_PERCENT=0
```

支持层级为 request override → governed project override → process default。request override
只能选择已发布且调用者有权使用的 profile；未授权 profile 必须 fail-closed 或回退并在
trace 记录，不能静默升级。

## 5. Evaluation 与 artifact 基础契约

### 5.1 Released Golden v1 — Foundation PASS

权威 released identity：

```text
Golden: codex-golden-v1/v1
SHA-256: sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1
Released cases: 45
Adapter materialization: 8 threads / 54 items
Judgments: 100
Negative routes: 10
False-validation categories: 5
```

首版必须严格发布 45 条，slice 数量固定：

| Slice | 数量 |
| --- | ---: |
| `thread_goal_location` | 6 |
| `process_trace` | 6 |
| `rationale_decision` | 6 |
| `patch_file_change` | 6 |
| `command_validation` | 7 |
| `failure_retry` | 5 |
| `multi_thread_comparison` | 4 |
| `unresolved_unanswerable_privacy` | 5 |
| **合计** | **45** |

Foundation evidence 已确认 exact 45 与
6/6/6/6/7/5/4/5。Loader contract 继续拒绝 44/46 条、slice 漂移、重复 case ID、缺
source locator、越权 expected evidence、未知 metric 或未固定 fixture version。

每条 case 至少包含：

```text
case_id / slice / query / task
project_id / ACL principal / scope
answerability
expected thread/episode/item/evidence role
required event order/path
hard_negative_ids + negative_reason
eligible_metrics
expected zero-result semantics
source locators
fixture version
```

### 5.2 真实 programmatic tmp JSONL fixture

Golden 不是直接伪造 retriever rows。测试/runner 必须从 immutable scenario recipe 在临时
目录生成真实：

```text
tmp/.codex/sessions/YYYY/MM/DD/*.jsonl
tmp/.codex/archived_sessions/*.jsonl
tmp/.codex/session_index.jsonl
```

随后通过真实 `CodexSessionAdapter → CodexIngestionService → SQLiteStore(tmp DB) →
CodexHybridRetriever` 路径运行。fixture 场景至少包含：

- 多 Turn 同 Goal、同 Turn 多子目标、跨 Turn retry；
- 同路径不同 Thread、同 Goal 旧失败尝试；
- Plan 提及但未执行、AgentMessage 声称通过但无 exit code；
- Patch 调用失败、只读文件未修改；
- 同命令多次运行且状态不同；
- archived/current duplicate、subagent 同主题；
- truncated ToolResult 的 error tail；
- reasoning 事件、synthetic secret sentinel；
- 两个 project ACL、public ACL 和越权 Thread；
- malformed/partial JSONL 与超限文件的独立非 released robustness fixture。

Scenario recipe 可以包含 synthetic secret sentinel，但 portable artifact 不得保存未脱敏
运行时输入；runner 完成后必须证明 sentinel 未出现在 SQLite 文本、predictions、trace、
errors 或 artifact。

### 5.3 Hard negatives 与 eligible denominator

- answerable case 返回零结果是失败；unanswerable/privacy case 的正确零结果单独计分；
- hard negative 被召回必须计 harmful error，不能从分母删除；
- 每个 metric 的 denominator 来自 case 的 `eligible_metrics`；
- `unavailable` 只允许用于能力确实不适用或 provider 未配置，必须有 reason code；
- 报告同时保存 `released=45`、`eligible`、`evaluated`、`unavailable`、numerator 和
  denominator；
- 不得用 0 填充 unavailable，也不得只报告成功子集；
- False Validated Rate 的 eligible negative cases 必须包括 claim-only、mentioned-only、
  invoked-no-exit、failed exit 和 target-unknown；任何 false positive 都阻塞安全 Gate。

### 5.4 X-B0 flat baseline — fixed negative control

X-B0 只能运行当前 V1：

- 每 Item 一个 View；
- `local-hash-v2`；
- FTS5 + dense full candidate；
- `codex-weighted-hybrid-v2`；
- no Episode V2、no event graph、no reranker、snippet-v1。

原始 immutable production Run：

```text
evaluation-run://project-codex-xb0-v1/6971b8cb1702a9ece8a894915c6b6de7
```

该 Run 的 identity/security/portable checks 为 `PASS`，但历史 truth audit 为 `FAIL`。
因此原 artifact 中的指标不得作为权威完整 truth；原 Run 保持 immutable，不原地修改。

权威 offline correction overlay：

```text
Correction:
evaluation-correction://project-codex-xb0-v1/1ccecfa1c9667d0f716c709d1175d570
Correction set:
sha256:f5da3d0b0d9d29bed3daff0a230cd1aa131d19385c20e9784b9b7a44a089ca6d
Canonical files: exact 10
Verdict: VERIFIED_CORRECTION_NON_QUALIFIED
Execution: zero re-retrieval / zero DB access
```

Correction 只覆盖 truth/metrics 解释，不改变原 Run。权威 baseline truth：

| Metric | Authoritative correction truth |
| --- | ---: |
| Thread Recall@5 | 23 / 40 = 0.575 |
| Episode Recall@5 | 21 / 40 = 0.525 |
| Item Recall@10 | 26 / 40 = 0.65 |
| MRR | 16.2972222222 / 40 = 0.4074305556 |
| Goal Recall | 7 / 7 |
| Decision Recall | 5 / 7 |
| Harmful old/negative retrieval | 5 / 26 |
| Event-order truth | 1 / 3 |
| Call/result truth | 1 / 2 |
| Patch status truth | 3 / 4 |
| Validation truth | 7 / 10 |
| False Validated | 1 / 5 |
| Outcome truth | 1 / 3 |
| Duplicate context | 0 / 45 |
| Tool noise | 8 / 45 |
| Hard-negative route | 8 / 45 |
| Correct zero | 0 / 5 |
| Refusal failure | 5 / 5 |

最终 Gate 结论：

```text
X-B0 PERSISTENT CORRECTION AUDIT PASS
P0/P1 = 0
X-B0 FIXED BASELINE CONTROL ACCEPTED — NON_QUALIFIED
X1 EVENT NORMALIZATION AUTHORIZED
```

X-B0 是后续 treatment 的固定负向比较锚，不是 qualified quality baseline，不存在可声明
的优化提升。Correction 的 False Validated 1/5、correct-zero 0/5、refusal-failure 5/5
等真实失败必须保留。

### 5.5 Portable artifact

每个 Run 保存一个自包含目录：

```text
run.json
manifest.json
golden_cases.jsonl
predictions.jsonl
metrics.json
slice_report.json
errors.jsonl
latency.json
security_report.json
checksums.json
```

要求：

- 相对 locator，无工作区绝对路径；
- 固定 code/fixture/golden/adapter/embedding/runner version；
- 记录配置、seed、eligible denominator、fallback/unavailable；
- checksum 覆盖所有 canonical 文件，verify-only 不重新执行检索；
- artifact 不包含临时 SQLite、WAL/SHM、raw secret fixture 或正式库内容；
- 复制到另一个临时目录后能 verify；
- artifact 不通过 verify 时不能用于 Gate 或面试。

## 6. 任务 DAG 与授权规则

```text
Code C0–C7 engineering COMPLETE
└── Release HOLD_DEFAULT_V1
    └── X0-01 Foundation PASS
        ├── released Golden codex-golden-v1/v1 exact 45
        └── X-B0 immutable Run
             ├── identity/security/portable PASS
             └── historical truth audit FAIL
                  └── offline correction overlay VERIFIED
                       └── FIXED BASELINE CONTROL ACCEPTED — NON_QUALIFIED
                            └── CX1-01/CX1-02 engineering COMPLETE
                                 └── X1 engineering Gate P0/P1=0
                                      └── X-T1 final reprepare AUTHORIZED
                                           └── sole production attempt
                                                └── FAILED_BEFORE_PUBLISH
                                                     ├── attempt audit VERIFIED / P0/P1=0
                                                     ├── X-T1 NO_RESULT / NOT_QUALIFIED / UNAVAILABLE
                                                     ├── retry NOT_AUTHORIZED
                                                     └── repository X2–X5 engineering COMPLETE
                                                          └── production treatment QUALITY UNAVAILABLE
                                                               └── X-B2 external/quality hold
                                                                    └── CX3 retrieval
                                                                         ├── X-B1 exact/sparse
                                                                         ├── X-B3 dense
                                                                         ├── X-B4 temporal
                                                                         └── X-B5 reranker
                                                                              └── CX4 context
                                                                                   └── X-B6
                                                                                        └── CX5 release engineering
                                                                                             └── isolated shadow/canary audit
```

可并行但不可越 Gate：

- CX0-02 与 CX0-03 可在 CX0-01 contract freeze 后由独立文件锁并行准备；
- CX3 dense profile benchmark 与 temporal graph builder 可在 shared candidate contract
  freeze 后并行，但各自 Run 必须独立；
- CX4 comparison projection 可与 context template 单测并行；
- optional X-B7 adaptive-k 为 P1，不阻塞 CX4/CX5 engineering；
- 外部 embedding/cross-encoder provider、真实服务 canary、正式库 migration 都是外部依赖，
  不属于当前授权。

每阶段的 baseline/treatment 锚固定如下：

| 阶段 | 固定控制组 | Treatment | 解释规则 |
| --- | --- | --- | --- |
| X0 | 无前序 Run | X-B0 flat V1 baseline | 已接受为 fixed negative control；`NON_QUALIFIED` |
| X1 | X-B0 的 state-eligible cases | X-T1 event normalization/state truth | 唯一 attempt 发布前失败；`NO_RESULT / UNAVAILABLE` |
| X2 | X-B0；同时引用 X-T1 安全事实 | X-B2 Episode V2 + Unit | Episode/Unit 为唯一主要变量 |
| X3 | X-B0 固定控制；另记直接父臂 | X-B1/B3/B4/B5，X-B7 optional | 父臂未 qualified 也不得替换 X-B0 |
| X4 | 同一 retrieval candidate set 的 snippet-v1 | X-B6 timeline-v2 | 检索输入冻结，只比较 context |
| X5 | V1 default + 最新已审计 V2 candidate | isolated shadow/canary treatment | 未 qualified 只能 HOLD，不得 default-on |

所有成功发布的 treatment artifact 必须同时记录 fixed X-B0 control identity、correction
identity 和直接父臂 identity。X-T1 没有 artifact，不能伪造 identity 或指标；X-B0 与
直接父臂质量失败时仍可用于单变量消融，但不能被写成 qualified baseline。

## 7. X0：Golden、Evaluation、Foundation 与 flat baseline

实际实施将本节原拟 CX0-01/02/03/04 颗粒度合并为 X0-01 Foundation。下列计划细节保留
作审计映射；状态和证据以本节的 actual Gate/Run/correction 为准。

### CX0-01 — Evaluation v1 contract

- **状态**：`PASS`；纳入实际 X0-01 Foundation。
- **目标**：新增 strict schema/loader/metric/result/artifact verifier，不接 runtime/API。
- **依赖**：本计划；无代码前置。
- **最小文件锁**：
  `src/evidence_rag/rag/sources/codex/__init__.py`、
  `src/evidence_rag/rag/sources/codex/evaluation_v1.py`、
  `tests/test_codex_evaluation_v1.py`。
- **Schema/API/数据契约**：纯 Python versioned contract；不建表、不改 Pydantic API。
  case、metric、run、artifact 均 fail-closed；45-case/slice contract 暂由 loader 接口固定。
- **实现**：
  1. 定义 immutable case/result/metric/run identity；
  2. 定义 eligible/unavailable/zero-result 语义；
  3. 定义 deterministic canonical JSON 与 checksum；
  4. 定义 verify-only；
  5. 禁止绝对路径、secret、未声明文件和非有限数值。
- **测试**：round-trip、重复 ID、slice/count drift、unknown metric、denominator 篡改、
  checksum/tamper、absolute path、secret sentinel、NaN/Infinity。
- **Evaluation**：Foundation contract 已通过；released X-B0 随后执行。
- **可观测性**：loader/verifier reason code、released/eligible/unavailable counts。
- **Flag**：无；不得接 `Settings`。
- **回滚**：删除新增未接线模块即可；V1 无行为变化。
- **验收**：新专项测试通过；现有 Codex tests 不回归；diff 不含现有摄取/检索/store。
- **面试同步**：已同步 Foundation PASS、released Golden、X-B0/correction 与
  `NON_QUALIFIED`。

### CX0-02 — 45-case Golden 与 programmatic fixture

- **状态**：`SUPERSEDED`；计划颗粒度已并入 X0-01 Foundation，released Golden 已产出。
- **目标**：严格发布 45 条 case 和能够在 `tmp_path` 生成真实 Codex JSONL 的 scenario。
- **依赖**：CX0-01。
- **最小文件锁**：
  `tests/fixtures/codex_golden_v1/fixture_manifest.json`、
  `tests/fixtures/codex_golden_v1/scenarios.jsonl`、
  `tests/fixtures/codex_golden_v1/golden_cases.jsonl`、
  `tests/test_codex_golden_v1.py`。
- **Schema/API/数据契约**：8 slice 为 6/6/6/6/7/5/4/5；expected ID/locator 必须由
  deterministic fixture identity 推导；不写任何数据库。
- **实现**：
  1. 编写 synthetic multi-thread scenario recipe；
  2. test helper 在临时 Codex home 生成 rollout/index JSONL；
  3. 覆盖 hard negatives、ACL、privacy、partial/archived/subagent；
  4. loader 验证 released membership 和 locator 可达；
  5. fixture manifest 固定版本和 checksum。
- **测试**：精确 slice count、case/fixture membership、JSONL 可被当前 adapter 读取、
  hard negative 覆盖、secret sentinel 与 ACL 场景存在、仓库 fixture 无真实 credential。
- **Evaluation**：Golden 已通过 Foundation Gate 并 released；exact 45。
- **可观测性**：fixture/session/turn/event/case counts 与 hard-negative coverage。
- **Flag**：无。
- **回滚**：移除未发布 fixture；不触碰 V1。
- **验收**：45/45 strict load；真实 adapter programmatic parse；reasoning/secret/ACL
  probes 通过。
- **面试同步**：已写入 Golden version/hash、exact case/slice 与 adapter/judgment evidence。

### CX0-03 — 独立 X-B0 baseline harness prep

- **状态**：`SUPERSEDED`；计划颗粒度已并入 X0-01 Foundation，X-B0/correction 已产出。
- **目标**：用真实 V1 代码路径运行隔离 baseline，并生成 portable artifact。
- **依赖**：CX0-01；集成验收依赖 CX0-02。
- **最小文件锁**：
  `src/evidence_rag/rag/sources/codex/baseline_v1.py`、
  `tests/test_codex_baseline_v1.py`。
- **Schema/API/数据契约**：runner 接收 fixture/golden 路径、显式临时输出目录和固定 seed；
  拒绝默认/正式 DB 路径；不接 runtime/API。
- **实现**：
  1. 构造临时 `Settings`、SQLite、raw object dir；
  2. 真实调用 adapter/ingestion/retriever；
  3. 记录 top-k、locator、channels、latency、errors；
  4. 计算 eligible metrics/slices；
  5. 写 canonical artifact，提供 verify-only；
  6. 退出前关闭连接，artifact 排除 DB/sidecar/raw secret。
- **测试**：路径 guard、正式库路径拒绝、两次 deterministic Run、复制后 verify、tamper、
  zero-result、exception/unavailable、artifact secret/absolute-path 扫描。
- **Evaluation**：runner preparation 后已执行 released X-B0；后续 correction 为零重新
  检索、零 DB 的 offline truth overlay。
- **可观测性**：case timing、candidate counts、fallback、error code、artifact size。
- **Flag**：固定 `v1/local-hash-v2/graph-off/reranker-off/snippet-v1`。
- **回滚**：删除 runner；V1 生产路径无变更。
- **验收**：smoke artifact portable/verify-only；无外部网络；无正式库 I/O。
- **面试同步**：已同步原 Run identity、truth-audit FAIL、correction identity 与权威指标。

### CX0-04 — Foundation integration 与安全 Gate preparation

- **状态**：`SUPERSEDED`；计划颗粒度已并入最终 X0 Foundation Gate。
- **目标**：把 CX0 contract、Golden、runner 与安全证据集成为 Foundation。
- **依赖**：CX0-01、CX0-02、CX0-03。
- **最小文件锁**：
  `tests/test_codex_evaluation_security_v1.py`、
  `tests/test_codex_foundation_v1.py`；只有发现 contract 缺陷时才按 owner 退回原 Issue。
- **Schema/API/数据契约**：不扩展。
- **实现**：端到端 temp JSONL→temp SQLite→V1 search→artifact smoke；验证 fixture、
  Golden、runner、verifier 的版本绑定。
- **测试**：完整专项测试、现有 Codex session/API/patch tests、静态检查。
- **Evaluation**：Foundation Gate 先于 released X-B0；Gate 后 Run 与 correction audit
  均已完成。
- **可观测性**：测试清单、文件锁、无正式库访问证明、artifact verifier output。
- **Flag**：无。
- **回滚**：保持所有新增模块未接 runtime。
- **验收**：Foundation engineering P0/P1=0；45-case loader、identity/security/portable
  contract 通过。后续 correction 发现 False Validated 1/5，因此 X-B0 quality
  `NON_QUALIFIED`，不把 Foundation PASS 写成质量 PASS。
- **面试同步**：已更新 Foundation、Golden、X-B0 与 correction。

### X0 Foundation Engineering Gate — ACTUAL PASS

实际 Gate decision 为 `PASS`；Gate 先审工程准备、后授权 released Run。其验收 contract
包括：

- 修改仅限新增 Codex evaluation 包、测试、fixtures；
- 45 cases 与 8 slices 精确；
- programmatic temp JSONL 走真实 V1 path；
- eligible denominator、hard negative、zero-result、ACL、secret、portable artifact 完整；
- runner 拒绝正式库及其 sidecar；
- 无 runtime/API/config/schema 变更；
- P0/P1=0。

该 Gate 已通过并授权 X-B0；随后 correction audit 最终授权 X1。

### X-B0 — Flat V1 baseline Run — ACTUAL NON_QUALIFIED

- **状态**：`FIXED BASELINE CONTROL ACCEPTED — NON_QUALIFIED`。
- **输入**：released Golden v1 + immutable fixture v1。
- **执行**：原 Run immutable；correction overlay 为零重新检索、零 DB。
- **输出**：原 Run identity/security/portable PASS；历史 truth audit FAIL；权威 correction
  exact 10 files、`VERIFIED_CORRECTION_NON_QUALIFIED`。
- **质量解释**：只使用 correction truth；不使用原 artifact 指标作为完整权威 truth；
  不宣称任何提升。
- **安全硬门**：persistent correction audit PASS，P0/P1=0。
- **回滚**：原 Run 不原地改写；correction 作为独立 immutable overlay。
- **面试同步**：已填写 Run/correction identity、真实 negative baseline 指标与
  `NON_QUALIFIED`。

X-B0 persistent correction audit 已通过并曾授权 X1；目前 X1 engineering 已
`COMPLETE`，但 production treatment `UNAVAILABLE`。X-B0 的真实质量低且
`NON_QUALIFIED`，继续作为 fixed negative baseline control 保留；这不是优化收益。

## 8. X1：Event Normalization 与 state truth

### CX1-01 — Observable event normalizer/state machine

- **状态**：engineering `COMPLETE`。
- **目标**：明确 action/change/validation 状态，不改变原始 Item。
- **依赖**：X-B0 Persistent Correction Audit `PASS`；已满足。
- **最小文件锁**：新增
  `contracts.py`、`event_normalizer.py`、`state_machine.py` 与对应新 tests。
- **Schema/API/数据契约**：纯 derived event contract；状态为
  proposed/invoked/completed/failed/timeout/cancelled/unknown、
  patch_proposed/apply_invoked/applied/failed、
  mentioned/command_invoked/exit_observed/passed/failed/target_unknown。
- **实现**：call/result pairing、nested exit code、patch success、command allowlist、
  target binding、event locator/reason；Plan/AgentMessage 永不升级为 observed action。
- **测试**：missing/duplicate/out-of-order call result、failed patch、claim-only、
  mentioned-only、exit 0/nonzero/missing、target unknown。
- **Evaluation**：engineering Gate 已通过；唯一 X-T1 production attempt 未发布
  artifact，不能提供 treatment 指标。
- **可观测性**：pairing/status reason、unlinked count、unknown count、false validated。
- **Flag**：新增后仍不接生产；后续 `RAG_CODEX_UNIT=event-v2` opt-in。
- **回滚**：不生成 derived facts，继续 V1 Item。
- **验收**：deterministic；False Validated Rate=0；reasoning 0 units。
- **面试同步**：可写 CX1-01 engineering COMPLETE；不得写 production treatment 成功或
  填 X-T1 指标。

### CX1-02 — Output window、dedup 与 additive fact store

- **状态**：engineering `COMPLETE`。
- **目标**：产生 error-aware head/tail、结构字段、content hash dedup 和 versioned facts。
- **依赖**：CX1-01。
- **最小文件锁**：新增 Codex V2 schema/store module、对应 tests；仅在 Gate 授权后对
  `storage.py` 增 additive initialize hook。
- **Schema/API/数据契约**：`codex_derived_facts`/`codex_event_links` additive；继承
  source/generation/ACL/locator；不改 V1 tables。
- **实现**：shadow materialize、integrity check、two-end ACL、generation membership、
  raw locator；同内容只去重 unit，不删除 raw Item。
- **测试**：migration on empty/V1 temp DB、idempotency、orphan/cross-generation/ACL、
  truncation tail、rollback。
- **Evaluation**：engineering evidence 已通过；X-T1 没有 treatment artifact，不能报告
  pairing/status/window production metrics。
- **可观测性**：fact/link/dedup/truncation counts、builder version。
- **Flag**：默认 off。
- **回滚**：关 flag并忽略 additive tables。
- **验收**：V1 contract tests 不变；False Validated=0；secret/ACL leakage=0。
- **面试同步**：可同步 CX1-02 engineering COMPLETE 与 Gate；不得把临时 SQLite 执行写成
  published treatment。

### X1 Engineering Gate 与 X-T1 failed attempt — ACTUAL

CX1-01 与 CX1-02 engineering 均 `COMPLETE`，X1 engineering Gate 为 P0/P1=0。X-T1 final
reprepare 曾 `AUTHORIZED`，但唯一 production attempt 已消费并在 artifact 发布前失败：

- production Adapter 已运行 8 threads / 54 items；
- CX1 normalizer 与 facts 已在临时 SQLite 运行；
- harness 生成的 `adapter-command:…` 违反严格 Identifier contract；
- failure 发生在 artifact publication 之前。

Failed-attempt audit Gate 的最终边界：

```text
X-T1 PRODUCTION ATTEMPT AUDIT VERIFIED — FAILED_BEFORE_PUBLISH
P0/P1 = 0
```

这里的 P0/P1=0 只证明失败边界审计完整，不代表 treatment quality 或 production Run 成功。
该 attempt：

- 没有 Run ID/URI、set hash、metrics、slices 或 artifact；
- 没有第二 attempt；
- temp、sidecars 和 stage 均已清理；
- 正式库、网络和检索未触碰；
- 旧 X-B0 Run/correction 的 bytes 与 mtime 不变。

缺陷随后修正为合法 `adapter-command-…`；synthetic 23 与 static checks `PASS`。但 released
treatment 没有重跑，因此这些证据只能证明定向修复，不能作为 X-T1 treatment evidence。

最终状态：

```text
X1 engineering COMPLETE
X-T1 treatment = NO_RESULT / NOT_QUALIFIED / UNAVAILABLE
X-T1 retry = NOT_AUTHORIZED
X2–X5 repository engineering = COMPLETE / OPT_IN / DEFAULT_V1
X2–X5 production treatment/release = EXTERNAL_BLOCKED / QUALITY UNAVAILABLE
```

X1 可表述为“engineering COMPLETE, production treatment unavailable”。不得为 failed
attempt 生成 Run identity、指标或 slice。后续仓库内 X2–X5 工程来自用户单会话完整开发
授权，不得反向表述为 X-T1 treatment 成功。

## 9. X2：Episode V2 与 Retrieval Unit

### CX2-01 — Deterministic Goal-aware Episode builder

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；实现见
  `sources/codex/episode_v2.py`。
- **目标**：允许跨 Turn 合并和同 Turn 切分，保留 boundary reason/confidence。
- **依赖**：X1 Engineering Gate；X-T1 安全硬门通过。
- **最小文件锁**：新增 `episode_builder.py` 与专项 tests。
- **Schema/API/数据契约**：builder input 为 ordered normalized events；output 为 immutable
  episode version/member/boundary，不改 Turn。
- **实现**：new UserGoal、cwd/project、completion/failure、time gap、file/identifier
  overlap、continuation；人工 split/merge 只创建新 version。
- **测试**：multi-turn same goal、same-turn subgoal、retry、compaction、cwd split、
  deterministic rebuild、manual override audit。
- **Evaluation**：Gate 后 X-B2；boundary F1、Episode Recall@5、detail loss/distractor。
- **可观测性**：boundary reason distribution、confidence、merge/split/fallback count。
- **Flag**：`RAG_CODEX_EPISODE=turn-v1|goal-temporal-v2`，默认 turn-v1。
- **回滚**：切回 turn-v1，V2 episode 保留审计。
- **验收**：原始 Item/Turn hash 不变；ACL/generation 完整。
- **面试同步**：只在工程 Gate 后说 Episode V2 已实现；质量按真实 X-B2。

### CX2-02 — Versioned Retrieval Unit builder/store

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；实现见
  `sources/codex/units_v2.py` 与 `store_v2.py`，仅正式库 migration/backfill 未执行。
- **目标**：生成 thread/episode/goal/decision/action/change/validation/failure/plan/outcome
  专用 Unit。
- **依赖**：CX1-02、CX2-01。
- **最小文件锁**：新增 `unit_builder.py`、扩展 Codex V2 additive schema/store、专项 tests。
- **Schema/API/数据契约**：`codex_episode_versions`、`codex_episode_members`、
  `codex_retrieval_units`；每 Unit 有 evidence role、state、version、ACL、raw refs。
- **实现**：candidate-only decision/alternative；failure window；validation fact；
  duplicate raw/summary 关联；shadow publish。
- **测试**：unit membership、raw refs、claim/plan exclusion、reasoning exclusion、
  split/merge version、dual-read、rollback。
- **Evaluation**：X-B2 只把 Episode/Unit 作为变量；保留 Item fallback。
- **可观测性**：units by type、raw refs per unit、duplicate ratio、missing evidence。
- **Flag**：`RAG_CODEX_UNIT=item-v1|event-v2`，默认 item-v1。
- **回滚**：切回 item-v1；不删 V2 unit。
- **验收**：V1 API/schema contract 全通过；无孤儿/越权 Unit。
- **面试同步**：真实 file/schema/test/Run ID。

### X2 Engineering Gate 与 X-B2

Gate 后 X-B2 在相同 45-case fixture 上比较 X-B0，主要观察 Episode Recall@5、Item
Recall@10、boundary、detail loss 和 distractor。X-B2 不 qualified 时保留 audit，X3
仍可获工程授权；安全/ACL/provenance 失败则不可。

## 10. X3：Exact/Sparse/Dense/Temporal、Reranker 与 Calibration

### CX3-01 — Query profile、exact 与 fielded sparse

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；实现见
  `sources/codex/retrieval_v2.py`。
- **目标**：按任务生成 scope/paths/tools/framework/status/order，提供 exact fast path 与
  type-aware sparse/RRF。
- **依赖**：X2 Engineering Gate。
- **最小文件锁**：新增 `query_profile.py`、`exact_index.py`、`sparse_index.py`、
  `retriever.py` 初始 contract 与 tests。
- **Schema/API/数据契约**：frozen query profile version；candidate 必带 channel、rank、
  evidence role/state/scope proof。
- **实现**：identifier/path/command/thread exact；Goal/Decision 权重；task quota；不能以
  非 ValidationFact 填满 validation quota。
- **测试**：exact collision、same path other thread、scope/ACL、quota、zero-result、
  deterministic RRF、deadline。
- **Evaluation**：Gate 后 X-B1，仅 exact/sparse/type-aware 变量。
- **可观测性**：per-channel candidates/contribution/drop reason。
- **Flag**：engine v2 opt-in；V1 default。
- **回滚**：request-level V1 fallback。
- **验收**：bounded candidate；no scope leak；V1 projection compatible。
- **面试同步**：X-B1 真实结果，不把 candidate 数当 Recall 提升。

### CX3-02 — Versioned dense profiles

- **状态**：`ENGINEERING COMPLETE / EXTERNAL MODEL HOLD`；versioned local profile/cache
  已实现，remote provider/真实 benchmark 未授权。
- **目标**：goal/rationale/episode/failure/change profile 与 content-addressed cache。
- **依赖**：CX2-02 与 shared candidate contract。
- **最小文件锁**：新增 `dense_index.py`、`embedding_profile.py` 与 tests；不与 CX3-03
  共享文件。
- **Schema/API/数据契约**：model/profile/dimension/normalization version 并存；remote
  provider 默认 unavailable。
- **实现**：先 local/offline benchmark；secret 再脱敏；shadow index 原子 publish；
  cache key 绑定 unit content/profile。
- **测试**：profile mismatch、cache tamper、model unavailable、dimension、redaction、
  ACL/generation、rollback。
- **Evaluation**：Gate 后 X-B3，单独比较 X-B1。
- **可观测性**：profile contribution、cache hit、latency/storage/cost/unavailable。
- **Flag**：`RAG_CODEX_EMBEDDING=local-hash-v2|<profile>`。
- **回滚**：切 local-hash-v2 或 sparse-only。
- **验收**：provider failure 可控；无网络为默认可运行状态。
- **面试同步**：只有真实 benchmark 可写模型/延迟/效果。

### CX3-03 — Temporal/event graph candidate

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；typed temporal/event channel
  与 bounded traversal 已实现。
- **目标**：按 event order、time window 和 typed links 恢复 retry/validation path。
- **依赖**：CX1-02、CX2-01 与 shared candidate contract。
- **最小文件锁**：新增 `temporal_index.py`、`event_graph.py` 与 tests。
- **Schema/API/数据契约**：FOLLOWS/EXECUTES/OUTPUT_OF/APPLIES_CHANGE/VALIDATES/RETRIES/
  SUPERSEDES 等 allowlisted typed edge；双端 ACL、generation、evidence locator。
- **实现**：bounded deterministic traversal、cycle/deadline/budget、first/latest/before/
  after order；graph 只是 candidate channel。
- **测试**：cycle、cross-ACL、cross-generation、superseded attempt、path direction、
  deadline、no-edge correct zero。
- **Evaluation**：Gate 后 X-B4，比较 graph off/on。
- **可观测性**：accepted/rejected paths、hop/budget、drop reason、path evidence。
- **Flag**：`RAG_CODEX_EVENT_GRAPH=false|true`。
- **回滚**：graph off；exact/sparse/dense 保留。
- **验收**：unauthorized traversal 0；path 可引用；无 silent partial path。
- **面试同步**：只有 required path Run 可说 graph recovery。

### CX3-04 — Deterministic reranker、calibration 与 adaptive-k

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；deterministic rerank、
  reviewed calibration 与 adaptive-k 已实现。
- **目标**：使用 task/type/state/order/completeness/supersede/truncation 特征重排并输出
  negative reason。
- **依赖**：CX3-01；可消费 CX3-02/03 channel。
- **最小文件锁**：新增 `reranker.py`、`calibration.py` 与 tests。
- **Schema/API/数据契约**：relevance probability、evidence role/status、necessity、
  negative reason；calibration artifact version/denominator 固定。
- **实现**：先 deterministic profile；plan-only、assistant-claim-only、
  call-without-result、patch-without-success、validation-without-exit、superseded、
  duplicate/truncated 等 hard negative。
- **测试**：stable ties、hard scope gate、deadline/exception fallback、calibration
  tamper、reason completeness、adaptive-k bounds。
- **Evaluation**：Gate 后 X-B5；X-B7 adaptive-k 为独立 P1 Run。
- **可观测性**：feature/reason、rank delta、calibration bins、fallback/timeout。
- **Flag**：`RAG_CODEX_RERANKER=off|profile`；adaptive-k 独立 off。
- **回滚**：reranker off、fixed-k；保留 candidate trace。
- **验收**：hard negative 不升格为验证事实；fallback 不跨 scope。
- **面试同步**：真实 hard-negative slice 与失败例。

### X3 Gate 与 Run 顺序

每个组件先过独立 engineering Gate，再运行对应 treatment，顺序固定：

```text
X-B1 exact/sparse
→ X-B3 dense
→ X-B4 temporal/event graph
→ X-B5 reranker/calibration
→ optional X-B7 adaptive-k
```

不得一次同时开启多个新变量。某 Run 不 qualified 时保留 artifact/错误分析并继续下一工程
Issue；默认仍 V1。任何 ACL/secret/reasoning/False Validated 硬门失败立即停止后续 Run。

## 11. X4：Timeline Context 与 normalized comparison

### CX4-01 — Task-specific timeline context builder

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；实现见
  `sources/codex/context_v2.py`。
- **目标**：按 Goal→Action→Change→Validation→Outcome 生成有序 context。
- **依赖**：X3 engineering complete；至少有各 channel audit。
- **最小文件锁**：新增 `context_builder.py` 与 tests。
- **Schema/API/数据契约**：process/rationale/validation/failure-retry 四模板；每 block
  绑定原始 locator、fact status、ACL、redaction/truncation warning。
- **实现**：task-aware budget、event-chain dedup、missing-rationale warning、target
  unknown warning；不输出隐藏 reasoning。
- **测试**：event order、claim vs fact、duplicate call/result、token budget、citation、
  missing rationale、redaction/truncation、ACL。
- **Evaluation**：Gate 后 X-B6；path coverage、duplicate/tool noise、faithfulness、
  token/event chain。
- **可观测性**：selected/dropped block、budget、warning、citation coverage。
- **Flag**：`RAG_CODEX_CONTEXT=snippet-v1|timeline-v2`。
- **回滚**：snippet-v1；retrieval results 保留。
- **验收**：citation 可定位；False Validated=0；无 reasoning/ACL leak。
- **面试同步**：只有真实 context Run 可写覆盖/faithfulness。

### CX4-02 — V2 normalized comparison 与 API opt-in

- **状态**：`ENGINEERING COMPLETE / OPT_IN`；normalized comparison 由 V2 context/pipeline
  投影，默认 V1 response contract 不变。
- **目标**：在现有 comparison/API 上提供相同 schema 的 Goal/Decision/files/commands/
  validations/outcome/open issues 对齐。
- **依赖**：CX2-02、CX4-01。
- **最小文件锁**：新增 comparison projector；最小修改 comparison models/service/router、
  `models.py`、`api.py` 与 contract tests，禁止无关重构。
- **Schema/API/数据契约**：现有 comparison V1 为默认；V2 `schema_version` opt-in；
  response 只 additive；ACL 逐 Thread 与逐证据检查。
- **实现**：复用 V2 facts/episodes/context，不直接比较全部聊天文本；保存 builder/profile
  version；旧 comparison 可读。
- **测试**：V1 snapshot、V2 opt-in、mixed availability fallback、two-end ACL、missing
  validation、rollback。
- **Evaluation**：X-B6 comparison slice 使用相同 4 个 released cases。
- **可观测性**：schema version、fallback、missing roles、thread alignment。
- **Flag**：context/comparison profile opt-in。
- **回滚**：V1 deterministic comparison。
- **验收**：V1 response contract 不变；V2 evidence locator 完整。
- **面试同步**：同步真实 API、fallback 和 comparison failure case。

### X4 Engineering Gate 与 X-B6

Gate 后运行 X-B6；只把 timeline context 作为主要变量。质量不达保留 audit，不阻塞 X5
工程；citation/ACL/secret/reasoning/false validated 任一硬门失败则阻塞。

## 12. X5：Append、Privacy、Shadow、Canary 与 Release

### CX5-01 — Append-only sync 与 last-known-good

- **状态**：`ENGINEERING COMPLETE / PRODUCTION SYNC EXTERNAL_BLOCKED`；append cursor、
  last-known-good 与 generation contract 已实现。
- **目标**：记录 source size/hash/last complete line，只重建变化 Thread/Episode/Unit。
- **依赖**：X4 Engineering Gate。
- **最小文件锁**：新增 append sync module/store tests；最小修改 ingestion wiring 和
  additive schema hook。
- **Schema/API/数据契约**：cursor 绑定 source identity、adapter/cleaning/builder version；
  partial line 不推进；deleted/archive session 生成 tombstone。
- **实现**：append fast path、changed session rebuild、version-triggered derived rebuild、
  full rebuild fallback、atomic publish。
- **测试**：append idempotency、partial/malformed line、truncate/replace detection、
  crash recovery、tombstone、version change、rollback。
- **Evaluation**：Gate 后运行 isolated sync treatment，报告 latency/throughput/storage，
  不声称 retrieval quality。
- **可观测性**：bytes/lines/events processed、cursor reason、rebuilt entities、fallback。
- **Flag**：`RAG_CODEX_SYNC=full-v1|append-v2`。
- **回滚**：full-v1 + last-known-good generation。
- **验收**：同 fixture full/append 结果等价；无丢 event；失败不切 active generation。
- **面试同步**：真实 latency/throughput 才可写。

### CX5-02 — Privacy deletion/tombstone propagation

- **状态**：`ENGINEERING COMPLETE / PRODUCTION PROPAGATION EXTERNAL_BLOCKED`；privacy
  tombstone、derived invalidation 与 fail-closed contract 已实现。
- **目标**：Source/Thread/Item ACL、redaction、retention/deletion 向 facts、units、vectors、
  comparison/context 传播。
- **依赖**：CX5-01 与所有 V2 stores。
- **最小文件锁**：新增 privacy coordinator 与 tests；各 V2 store 只接受 owner 接口修改。
- **Schema/API/数据契约**：双端授权、tombstone state、deletion audit；绝对 source path
  仅内部 locator。
- **实现**：pre-embedding redaction、remote send 再脱敏、derived purge/tombstone、
  comparison invalidation、blocked entity enforcement。
- **测试**：ACL downgrade、thread delete、archived retention、vector/context/comparison
  stale evidence、synthetic secret、graph traversal。
- **Evaluation**：privacy treatment 只报告 leakage/propagation completeness。
- **可观测性**：tombstone/purge counts、stale reference scan、unauthorized attempts。
- **Flag**：V2 engine 不允许绕过；失败 fail-closed。
- **回滚**：停用 V2 read；保留 tombstone/audit，不恢复已授权删除的数据。
- **验收**：unauthorized/secret leakage=0；无 stale derived reference。
- **面试同步**：只同步真实 rehearsal 证据。

### CX5-03 — Shadow/canary/release controller

- **状态**：`ENGINEERING COMPLETE / HOLD_DEFAULT_V1`；shadow aggregation、stable
  project/request routing、six-stage release evaluator 与 rollback 已实现。
- **目标**：实现 off/shadow/canary/on、request/project override、fallback、LKG 和 rollback
  rehearsal；不直接改变正式服务默认。
- **依赖**：CX5-01/02、X-B6 audit。
- **最小文件锁**：新增 release/shadow module；最小修改 config/runtime/API wiring 与 tests。
- **Schema/API/数据契约**：governed engine/profile identity、deterministic canary assignment、
  shadow response 不影响用户、release decision artifact。
- **实现**：V1/V2 dual read、bounded shadow、canary percent、request fallback、
  circuit-breaker、LKG、rollback command/rehearsal。
- **测试**：0/1/100 canary、stable assignment、override auth、timeout/exception fallback、
  LKG corruption、rollback、V1 response compatibility。
- **Evaluation**：工程 Gate 后只在 temp/in-memory/immutable fixtures 上做 shadow/canary；
  正式服务 canary 需要另行取得 service owner 授权。
- **可观测性**：engine/profile、shadow delta、fallback reason、latency/error、decision。
- **Flag**：默认 `engine=v1, shadow=false, canary=0`。
- **回滚**：一键 v1、canary 0、shadow off、LKG 恢复；演练必须保存证据。
- **验收**：默认 V1 完全不变；V2 异常 request-level fallback；无越权 override。
- **面试同步**：engineering complete 不等于 released；只写真实 decision。

### CX5-04 — 单源 Release Gate

Release review 必须汇总：

- Golden/fixture/baseline/treatment 的 verified artifact；
- 45-case released membership 与所有 eligible denominator；
- slice/error、latency/storage/cost、fallback、rollback；
- False Validated、reasoning、secret、ACL 均为 0；
- X-B1/B2/B3/B4/B5/B6 和 optional B7 的真实 qualified/not-qualified；
- shadow/canary rehearsal；
- 默认切换的 service-owner authorization。

目标门槛沿用设计：

| 指标 | Release Target |
| --- | ---: |
| Thread Recall@5 | ≥ 0.90 |
| Episode Recall@5 | ≥ 0.85 |
| Event-order Accuracy | ≥ 0.98 |
| Call/Result Pair Accuracy | ≥ 0.98 |
| Patch Status Accuracy | ≥ 0.98 |
| Validation Precision | 1.00 |
| Patch→Validation Path Recall | ≥ 0.85 |
| False Validated Rate | 0 |
| Duplicate Context Ratio | ≤ 0.15 |
| Redaction/Unauthorized Leakage | 0 |
| P95（fixture 固定规模） | ≤ 1.5 s |

这些是 `TARGET`，不是当前结果。任一质量门未过时可以把 X5 engineering 标为 complete，
但 release decision 必须为 `HOLD_DEFAULT_V1`；安全硬门未过还必须停止 canary。当前正式库
和真实服务 rollout 不在本计划已授权范围内。

## 13. 每阶段 Gate、Run 与 rollback 统一规则

固定顺序：

```text
实现
→ unit/contract/integration/security tests
→ Engineering Gate
→ 被授权的 isolated Run
→ verify-only + slice/error audit
→ treatment qualified/not-qualified
→ 下一工程阶段授权
```

禁止：

- Engineering Gate 前执行 released Baseline/Treatment；
- 用 smoke fixture 结果冒充 released Run；
- 把工程 PASS 写成 quality PASS；
- quality 不达时覆盖或删除旧 artifact；
- 只报告总体指标而隐藏 slice、unavailable 或 hard negative；
- 把历史 5/127/6,178 快照当作本轮 measurement。

每阶段 rollback 必须在 temp environment 演练：

1. 关该阶段 flag；
2. 切 V1/上一个 qualified profile；
3. 验证现有 V1 endpoint/response；
4. 验证 last-known-good generation；
5. 记录 fallback reason；
6. 保留失败 treatment artifact。

## 14. 最小文件锁总表

| 阶段 | 默认 owner 文件 | 禁止顺带修改 |
| --- | --- | --- |
| CX0 | 新 `rag/sources/codex/evaluation_v1.py`、`baseline_v1.py`、最小 init、新 tests/fixtures | adapter/ingestion/retrieval/store/API/config/schema |
| CX1 | 新 contracts/normalizer/state/schema/store + 专项 tests | Episode/retriever/context |
| CX2 | 新 episode/unit + additive V2 store + 专项 tests | dense/graph/reranker/API |
| CX3 | exact/sparse、dense、temporal、reranker 各自独立文件锁 | context/comparison/release |
| CX4 | context 与 comparison projector 分锁；API 只作 additive opt-in | ingestion/release |
| CX5 | append、privacy、release controller 分锁；wiring 单独 PR | 正式库、旧表 cleanup |

文档 owner 每阶段只同步本文件和对应面试文档；不把实现证据写入其他来源文档。实际 dirty
worktree 中其他修改均视为用户所有，不覆盖、不格式化、不顺手清理。

## 15. 面试证据同步点

| Gate/Run | 可同步内容 | 仍禁止 |
| --- | --- | --- |
| 当前 | X0 Foundation PASS；Golden/Run/correction identity；X-B0 correction truth 与 `NON_QUALIFIED` | 把 X-B0 写成 qualified 或提升 |
| X0 Foundation Gate | released Golden version/hash、exact counts | 把设计 Target 写成结果 |
| X-B0 Audit | 原 Run truth-audit FAIL、correction、真实 baseline/slice/error | 使用原 artifact 指标作完整权威 truth |
| X1 | CX1-01/CX1-02 与 X1 engineering `COMPLETE`；attempt audit `FAILED_BEFORE_PUBLISH` | 把 engineering COMPLETE 写成 treatment 成功 |
| X-T1 | `NO_RESULT / NOT_QUALIFIED / UNAVAILABLE`；retry `NOT_AUTHORIZED` | 生成 Run identity、metrics、slices 或 artifact |
| X2 | Episode/Unit 代码、boundary/X-B2 | 未 qualified 的收益话术 |
| X3 | 各独立 Run、负结果、latency/cost | 合并变量、Graph scan 冒充收益 |
| X4 | context/comparison 代码、citation/faithfulness | 隐藏 reasoning 叙事 |
| X5 | append/privacy/release rehearsal、真实 decision | engineering complete 冒充 release |

简历只允许使用已 verify 的 `CURRENT_MEASURED` 或 `RESULT_MEASURED`。当前可使用
released Golden、correction 后的 X-B0 negative baseline 和 X1 engineering COMPLETE
事实；X-T1 没有 published treatment artifact，因此没有可填写的优化提升数字。

## 16. 本计划完成定义

本开发计划文档完成，不等于 Codex 实现完成。文档完成需满足：

- 当前 runtime/API/model/adapter/ingestion/retrieval/store/schema/tests 映射真实；
- X0–X5 都有 Issue/PR 级依赖、最小文件锁、contract、tests、Run、observability、
  flag、rollback、acceptance 与面试同步；
- 45-case 8-slice Golden、programmatic tmp JSONL、hard negative、eligible denominator、
  secret/ACL、portable artifact 和 X-B0 明确；
- Event Normalization/facts 与 X2–X5 仓库内工程均如实标为 engineering COMPLETE；
  production treatment/release 仍保持 unavailable/HOLD；
- 正式库 `EXTERNAL_MUTABLE_SERVICE_OWNED` 例外被全程保留，未对其当前状态作声明；
- Code 前序工程完成与 `HOLD_DEFAULT_V1` 同时保留；
- X0 Foundation 与 X-B0 correction audit 已通过；X1–X5 repository engineering
  COMPLETE，但 X-T1 unavailable、retry 未授权，production treatment/release blocked。

实施完成的最终定义仍以真实 verified Run、release Gate 和面试证据为准。当前结论保持：
X0-01 Foundation `PASS`；X-B0 fixed negative control accepted、`NON_QUALIFIED`；
CX1-01/CX1-02 与 X1 engineering `COMPLETE`；X-T1
`NO_RESULT / NOT_QUALIFIED / UNAVAILABLE`、retry `NOT_AUTHORIZED`；X2–X5
repository engineering `COMPLETE / DEFAULT_V1 / QUALITY UNAVAILABLE`，无提升结论。
