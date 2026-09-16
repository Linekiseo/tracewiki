# RAG + Wiki 全阶段目标分解与验收卡

版本：2026-08-06  
状态：`EXECUTION_AUTHORITY / ACTIVE_GOAL_RESET / DEFAULT_V1 / QUALITY_HOLD`  
上位总纲：`10_RAG_WIKI_MATURITY_PROGRAM.md`  
状态台账：`11_RAG_WIKI_MATURITY_EXECUTION_LEDGER.md`  
G1 详细设计：`13_G1_RAW_EVIDENCE_AUTHORITY_DESIGN.md`
G1 binding 执行规格：`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md`
G0 portable Python 执行记录：`16_G0_PORTABLE_PYTHON_IDENTITY_AND_V6_EXECUTION_RECORD.md`
G0 owner/CI handoff：`17_G0_OWNER_ADMISSION_AND_REMOTE_CI_HANDOFF.md`
G0 clean-checkout full replay：`18_G0_CLEAN_CHECKOUT_FULL_REPLAY.md`
G0 portable exclusion semantics：`19_G0_PORTABLE_EXCLUSION_AND_RELEASE_READY_SEMANTICS.md`
G1 binding 实施就绪审计：`27_G1_BINDING_FOUNDATION_IMPLEMENTATION_READINESS_AUDIT.md`
G1 Entry/requirement packet：`28_G1_ENTRY_DECISION_AND_REQUIREMENT_TRACE_PACKET.md`
G0 frontend router/supply-chain closure：`29_G0_FRONTEND_ROUTER_SUPPLY_CHAIN_CLOSURE.md`
G1 Entry offline verifier：`30_G1_ENTRY_OFFLINE_VERIFIER_EXECUTION_RECORD.md`
G1 Entry signing handoff/assembly：`31_G1_ENTRY_SIGNING_HANDOFF_AND_ASSEMBLY_RUNBOOK.md`
G0 npm install-script admission：`32_G0_NPM_INSTALL_SCRIPT_ADMISSION.md`
G0 owner-review packet：`33_G0_OWNER_REVIEW_PACKET_AND_V20_RUNBOOK.md`
全阶段原子目标图：`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`
G2 grounded-answer 执行规格：`35_G2_LIVE_WIKI_GROUNDED_ANSWER_EXECUTION_SPEC.md`
G3 canonical planner 执行规格：`36_G3_CANONICAL_QUERY_PLAN_SCOPE_ASOF_EXECUTION_SPEC.md`
G4 materialization/backfill 执行规格：`37_G4_SIX_SOURCE_CORPUS_MATERIALIZATION_BACKFILL_EXECUTION_SPEC.md`

## 1. 本文解决的问题

总纲定义“为什么、按什么顺序、最终达到什么水平”，本文把每一阶段进一步拆成可以执行、
验证和停止的目标卡。任何目标不再用“完善 RAG”“优化 Wiki”作为完成描述，而必须同时具备：

```text
Objective = Outcome + Inputs + Mutations + Evidence + Exit gate + Rollback + Owner boundary
```

每个执行工作包必须满足以下约束：

- 一次只拥有一个主结果；
- 输入版本、项目/ACL、generation 和数据集必须固定；
- 代码变更、迁移、运行或外部发布必须明确区分；
- 测试通过不自动等于真实质量通过；
- 依赖外部 owner/数据/流量时保留 `QUALITY_HOLD`，不伪造完成；
- 失败证据保留，修复产生新 artifact，不覆盖旧 run；
- 前置 Gate 未通过时只允许设计、只读盘点和隔离测试，不允许越级发布或正式迁移。

## 2. 目标层级与状态推进

### 2.1 四层目标

| 层级 | 标识 | 定义 | 关闭条件 |
|---|---|---|---|
| Mission | `OBJ-RW-L5` | 从当前工程 Alpha 到 L5 Production Qualified | G0–G9 全部 QUALIFIED |
| Gate | `G0`–`G9` | 一个不可跳过的能力/证据阶段 | 本 Gate 全部 hard exit 通过 |
| Work package | `WP-Gx-*` | 可在一次或数次受控迭代完成的结果 | 产物、测试、artifact、台账齐全 |
| Atomic task | `Tx.y.z` | 单一代码/迁移/评测/评审动作 | 预期 diff 和单项验证完成 |

### 2.2 状态机

```text
NOT_STARTED
  → IN_PROGRESS
  → ENGINEERING_PASS
  → QUALITY_HOLD          # 需要真实数据、外部 owner、生产观测或评审
  → QUALIFIED

任一步执行失败 → FAILED
同一外部条件连续确认三轮仍无法推进 → BLOCKED_EXTERNAL
```

`ENGINEERING_PASS` 不能直接跳到 `QUALIFIED`；涉及真实数据、发布或生产流量的阶段必须经过
相应 quality/review evidence。

## 3. Mission 目标卡：OBJ-RW-L5

### Outcome

六源均能从真实版本化来源产生可回读证据；Wiki 只做组织导航；所有回答 claim 可追溯到
满足 ACL/version/as-of 的 Evidence Fact；单源、多源、Wiki、生成、性能、安全和发布均由
可复现 artifact 授权；默认 V2 可按 project/intent 回滚。

### Hard invariants

1. unauthorized evidence leakage = 0；
2. secret leakage = 0；
3. wrong-version citation rate 不超过冻结门槛；
4. page/snippet/candidate 不得冒充 raw authority；
5. `production_authorized` 只来自 reviewed canonical control plane；
6. 任一来源失败必须表现为 typed partial/refusal/fallback，不得表现为伪成功；
7. 默认 V1 保留到 G9 授权切换，并在观察期内可请求级回退。

### Mission evidence

- 同一受审 revision；
- 六源 dataset/generation manifests；
- G0–G9 portable evidence packages；
- reviewed source/gate/release registries；
- production observation 和 rollback drill；
- 最终 L5 Review。

## 4. G0 目标卡：可复现工程与版本准入

### Entry

当前工作树可读取；不要求 clean，但必须量化 dirty/untracked 边界；禁止批量 stage/commit/push。

### Work packages

| WP | 原子任务 | 产物 | 单项验证 |
|---|---|---|---|
| WP-G0-01 Regression recovery | dense 开关；旧 artifact 精确兼容；专项/全量回归 | config/runtime/eval/tests | ruff；C-B1/B2/B5；backend full；frontend |
| WP-G0-02 Baseline inventory | 版本/config/schema/artifact/scope 清单；路径脱敏 | sanitized baseline | canonical build/verify；无正式 DB I/O |
| WP-G0-03 Validation surface | smoke/integration/evaluation/full；Python matrix CI | Makefile/workflow/docs | 本地命令与 CI 命令同源 |
| WP-G0-04 Portable package | 环境/输入/命令/结果/失败/安全/限制/checksum | 9-file package | 原目录和临时复制 verify |
| WP-G0-05 Version admission | V1 负证据；V2 source/eval/generated policy；V3 complete-history cleanroom；V4 portable runtime authority；V5 execution-doc admission；owner revision；clean checkout | V5 content-addressed manifest + revision | bytes-only materialize；ordinary clone；full regression；exact/release-ready；3.12/3.13 CI |
| WP-G0-05J Frontend supply chain | 移除 vulnerable router；冻结第一方 route contract；remote audit/SBOM | router/test/lock/workflow/review | frontend full/type/build；audit 0；CI artifact contract |
| WP-G0-05K npm install scripts | exact npm；strict allowScripts；lock graph/integrity；pending=0；隐藏配置纳管 | package/.npmrc/verifier/tests/workflow/review | fresh strict install；frontend 191；audit/SBOM；双 Python 2,118 |
| WP-G0-05L Owner review packet | path/scope/exclusion/action PENDING queue；domain digests；native recompute | builder/verifier/8 tests/Make/runbook/review | tamper/substitution/drift 拒绝；双 Python 2,126；V20 exact/cleanroom |
| WP-G0-05M Atomic objective reset | G0 当前外部队列；G1–G9 原子依赖/证据/owner/stop；LIVE-02 事实边界 | objective graph/V21/packet V2 | 1,121 paths；不越 G0；owner/remote/UI 继续 PENDING |
| WP-G0-06 Gate review | 复核所有证据与限制 | G0 review | reviewer decision |

### Exit

- 当前已完成 WP-G0-01–04 的本地工程证据；
- WP-G0-05B/C/D/E/F/G/H/J 本地隔离、自包含、CI handoff、clean full、exclusion 与供应链 gate 已完成；V11
  保存产品全链结果；V12/V13 收口 G1 readiness；V14/V15 关闭前端供应链与外部 target；V16 纳入
  Entry verifier/handoff；V18/V19 关闭 install-script authority 并收口文档；V20 新增 PENDING owner
  packet；V21 纳入 atomic graph；V22–V24 纳入 G2–G4 execution specs；V25/V26 纳入 G5–G8 specs、
  执行控制和 candidate refresh；V26 后续本地 full 发现并关闭 nanoid high finding 后，当时的 V27 候选为
  1,136 files、12 opaque eval anchors、431 Git objects、1,136-item PENDING owner packet V8；frontend 191、
  backend 双 Python各 2,126 与 audit 0 是本地工程回放，remote same-revision 仍未产生；V3 的 4 项路径误报已在
  V4 显式换代修复，V5 的 Python minor identity 漂移已在 V6 修复；
- 当前未完成 WP-G0-05：owner-reviewed revision、取消跟踪 `web/index.html`、clean checkout、remote CI；
- workflow 已覆盖所有 PR/main push、双 Python、exact/cleanroom/audit/SBOM/frontend/release-ready，但 remote run
  未发生，不能用定义完整替代运行证据；
- V9 clean full 的 2,074/1 generated HTML 失败保留为负证据；V10 已在无预生成 Web 产物时完成
  backend-before-build 全量；
- V10 exclusion exact=false/release=true 保留为负证据；V11 要求 approved=true、unapproved
  additions=0；
- WP-G0-05/06 通过前 G0 保持 `QUALITY_HOLD`；
- 不允许为关闭 G0 自动纳入用户全部工作树、创建提交或推送。

### Rollback/stop

CI 读取正式 DB、出现测试排除、artifact 含 secret/绝对敏感路径、clean checkout 与工作树结果不等时
立即停止准入并产生新失败 artifact。

## 5. G1 目标卡：六源原始证据权威

六源字段和测试以 `13_G1_RAW_EVIDENCE_AUTHORITY_DESIGN.md` 为准；binding foundation 的表、ID、
事务、迁移、dry-run 和 rollback 以 `15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` 为准。

### Entry

实施入口为 G0 QUALIFIED；G0 HOLD 期间只允许 G1D 设计工作。

### Work packages

| WP | 原子任务 | 产物 | 单项验证 |
|---|---|---|---|
| WP-G1D-01 Authority design | 六源盘点；locator/selector；failure codes；迁移；tests | G1 detailed design | 文件字段 trace；design review |
| WP-G1D-02 Binding execution spec | V2 并列表；canonical ID；M0–M8；dry-run；BF-01–15；rollback | G1I-01 execution spec | adversarial execution-spec review |
| WP-G1D-03 Implementation readiness | contract→code trace；reference-only/hash/event/visibility/source mapping reconciliation；C1–C10 | readiness audit + spec V2 | adversarial readiness review |
| WP-G1D-04 Entry/requirement trace | E-01–10；external attestation；D1–D5；data authorization；BF-01–15 side effects | Entry Packet + exact test/evidence matrix | adversarial entry review |
| WP-G1D-05 Offline Entry verifier | Ed25519/keyset/trust digest；G0 native verify；CI aggregate receipt；E-01–10 recompute | offline verifier + CLI + 27-case matrix | Python 3.12/3.13 + no-I/O spy |
| WP-G1D-06 Signing handoff and assembly | PENDING draft；fact-ready requests；receipt 一一匹配；时序防重放；双重验证 | offline handoff CLI/Make + runbook | Entry+handoff 40；与 workflow 合计 43；双 Python full 2,117 |
| WP-G1I-01 Binding foundation | project-scoped raw/event identity；binding schema；store resolve/invalidate；public projection | raw/binding v2 | migration/isolation/rollback/ambiguity tests |
| WP-G1I-02 Secure reader | managed root；symlink；digest；selector；audit | shared service | storage/security/fault matrix |
| WP-G1I-03 Code | raw file + UTF-8 line/byte range | Code gateway/backfill | wrong commit/blob/range matrix |
| WP-G1I-04 Codex | item raw + ordered thread manifest | Codex gateway/backfill | redaction/order/tombstone matrix |
| WP-G1I-05 Experiment | run JSON pointer；metric/unit/dataset/artifact | Experiment gateway | numeric/unit/checksum matrix |
| WP-G1I-06 Notebook | revision/cell/output selector | Notebook gateway | stale/order/producer matrix |
| WP-G1I-07 Document | parse artifact；span/table/figure/citation selector | Document gateway | parser/OCR/table/version matrix |
| WP-G1I-08 Workspace | append-only state event；valid time；outbox | Workspace gateway | as-of/version/delete matrix |
| WP-G1I-09 Wiki ref migration | SourceRefV2；protected binding；generation rebuild | Wiki V2 store/compiler | V1 navigation-only/refusal tests |
| WP-G1I-10 Gate package | 六源 E2E；backfill dry-run；rollback；review | G1 artifact/review | digest 100%；leakage 0 |

### Exit

六源各自 raw read-back 成功率 100%；wrong project/ACL/version/generation/state/digest/selector 全部
fail closed；旧 ref 不静默升级；缺 raw 不退回 snippet；默认仍为 V1。

### Rollback/stop

任一 gateway 需要调用方提供 storage path/selector 覆盖存储 binding、出现“取最新”猜测、跨项目
对象存在性泄漏或 Workspace mutation 无 durable event 时停止 G1。

## 6. G2 目标卡：Live Wiki grounded-answer 闭环

### Entry

G1 QUALIFIED，至少每源有一个可回读 locator fixture 和一个故障 fixture。

### Work packages

| WP | 原子任务 | 产物 | 单项验证 |
|---|---|---|---|
| WP-G2D-01 Execution design | candidate 假 raw code map；V2 ref/receipt/obligation/pack/claim；32-case Gate | G2 execution spec | page/raw 分层；六源 ingestion E2E；no fallback |
| WP-G2-01 Status policy | raw/observed/reported/inferred/verified；review authority | versioned policy/ADR | transition/property tests |
| WP-G2-02 Live organizer | inventory item 保留真实 raw/derived/status/binding | organizer v2 | 禁止统一派生降级/升级 |
| WP-G2-03 Compiler/store | SourceRefV2、binding membership、generation isolation | compiler/store v2 | publish atomicity/migration tests |
| WP-G2-04 Navigator | page found 与 raw verified 分离；reason trace | navigator v2 | budget/deadline/missing raw tests |
| WP-G2-05 Claim verifier | strict claim authority；counter evidence；derived policy | verifier v3 | unsupported claim 永不渲染 |
| WP-G2-06 Per-source E2E | 每源 positive + refusal | 12 case artifact | live ingestion，不手造 raw fact |
| WP-G2-07 Cross-source E2E | consistent/conflict/as-of/ACL/generation change | joint artifact | citation membership/wrong version |
| WP-G2-08 Gate review | 证据、限制、回滚 | G2 review | reviewer PASS |

### Exit

完整链路 `source → raw/binding → retrieval → organize → compile → stage → publish → navigate →
raw verify → Evidence Pack → grounded answer/refusal` 通过；页面 citation 单独不能支撑 claim；
unsupported/ACL/wrong-version claim = 0。

### Rollback/stop

如果 page obligation 在 raw verify 前被标 supported、失败 raw read 仍生成 answer、或 live E2E 使用
手工 candidate 绕过 ingestion，立即停止。

## 7. G3 目标卡：Canonical Planner、Scope 与 as-of

### Entry

G2 QUALIFIED；raw evidence 和 answer trace 已能绑定 source generation。

### Work packages

| WP | 原子任务 | 产物 | 单项验证 |
|---|---|---|---|
| WP-G3D-01 Execution design | 四规划链/Scope/as-of/snapshot/fallback code map；V3 contracts；42-case Gate | G3 execution spec | silent current/filter loss/intent drift/fallback duplication 均有 fail-closed case |
| WP-G3-01 Semantics inventory | 普通/global/wiki 三入口字段和分支盘点 | parity matrix | 同请求差异可复现 |
| WP-G3-02 CanonicalQueryPlanV3 | intent/source/task/role/budget/scope/fallback/digest | frozen contract | canonical/property tests |
| WP-G3-03 Source capability | as-of/numeric/relation/channel/task 能力 | registry v3 | unsupported route rejected |
| WP-G3-04 Adapters | legacy/global/wiki 输入只转为 canonical plan | thin adapters | same input same digest |
| WP-G3-05 Scope merge | include/source types/project/ACL/version/as-of 冲突规则 | scope resolver | conflict fail-closed matrix |
| WP-G3-06 Snapshot coordinator | per-source generation/watermark pinning | snapshot manifest | mid-query swap tests |
| WP-G3-07 Temporal semantics | valid/observed/system time 与 current 定义 | temporal ADR | boundary/history tests |
| WP-G3-08 Fallback | V1 exactly-once + typed blocker | fallback trace | timeout/error/no-match distinction |
| WP-G3-09 Replay/review | 三入口 parity replay | G3 artifact/review | capability violation=0 |

### Exit

同一规范请求产生同一 plan digest；required roles 不丢失；不支持 as-of 的来源不被静默路由；
source timeout 不等于无证据；query→plan→snapshots→pack→citation 可重放。

### Rollback/stop

出现多 planner 继续各自决策、请求执行中 generation 变化仍合并、fallback 两次执行或 ACL 在召回后
过滤时停止。

## 8. G4 目标卡：真实六源 Materialization 与 Backfill

### Entry

G3 QUALIFIED；数据 owner 明确授权用途、保留、脱敏和删除策略。

### Work packages

以下为执行粒度；字段、状态机、困难切片、GM-01–54 和 stop/rollback 以文档 37 为唯一权威。

| 顺序 | WP | 原子任务 | 单项退出证据 |
|---:|---|---|---|
| 1 | WP-G4D-01 / T4.0.1 | 六源 ingest/store/runtime/generation 只读审计 | MAT-01–24 code map；DESIGN ENGINEERING_PASS |
| 2 | WP-G4-01A / T4.1.1 | `QualificationDataAuthorizationV1` 与外部 receipt verifier | forged/expired/role/revision fail closed |
| 3 | WP-G4-01B / T4.1.2 | `QualificationCorpusManifestV1` 与 root provenance | canonical/tamper/drift/duplicate-root tests |
| 4 | WP-G4-01C / T4.1.3 | authorization 与 corpus exact assembler | source/field/time/ACL/exclusion 完全匹配 |
| 5 | WP-G4-02A / T4.2.1 | `MaterializationRunV1` control schema/state CAS | idempotency/side-effect ledger tests |
| 6 | WP-G4-02B / T4.2.2 | `SourceGenerationManifestV3` | state/digest/count/watermark tamper matrix |
| 7 | WP-G4-02C / T4.2.3 | 六源 `ActiveGenerationSetV1` + CAS | exactly six；fault 时旧 pointer 不变 |
| 8 | WP-G4-03 / T4.3.1 | generation-bound raw binding migration | NULL inventory/dry-run/backfill/revert |
| 9 | WP-G4-04A / T4.4.1 | Code staging adapter | no early active；2 repo×2 version reconciliation |
| 10 | WP-G4-04B / T4.4.2 | Codex staging adapter | live/redaction/remove/cursor cases |
| 11 | WP-G4-04C / T4.4.3 | Experiment staging adapter | 20 runs/5 groups；observed version/deletion |
| 12 | WP-G4-04D / T4.4.4 | Notebook staging adapter | 10×2；stale/error/order/tombstone |
| 13 | WP-G4-04E / T4.4.5 | Document staging adapter | 20 roots；multi-version/provider/tombstone |
| 14 | WP-G4-04F / T4.4.6 | Workspace staging adapter | 3×2；audit/state/version/as-of limitation |
| 15 | WP-G4-05A / T4.5.1 | `MaterializationCoverageReportV1` | membership conservation/orphan/ACL/version |
| 16 | WP-G4-05B / T4.5.2 | 六源 stage/validate/generation-set coordinator | 任一来源故障不产生 partial active set |
| 17 | WP-G4-05C / T4.5.3 | exact-set Wiki builder hook | Wiki 与六源 set mismatch fail closed |
| 18 | WP-G4-06 / T4.6.1 | checkpoint/resume/lease fencing | restart/cancel/late worker/input drift matrix |
| 19 | WP-G4-07A / T4.7.1 | `DeletionPropagationRunV1` | raw→index→Wiki→cache/export；SLA 闭环 |
| 20 | WP-G4-07B / T4.7.2 | rollback/retention/cleanup | 新删除阻断旧代复活；cleanup exact dry-run |
| 21 | WP-G4-08 / T4.8.1 | production-mirror full dry-run | schema/index/capacity/fault；formal mutation=0 |
| 22 | WP-G4-09 / T4.9.1 | portable Gate 与独立 review | GM-01–54 全部复核 |

### Exit

六源均有 active、非空、版本化 generation；raw→derived→retrieval→Wiki binding 覆盖率可计算；
失败/partial 真实暴露；backfill 可重入恢复回滚。

### Rollback/stop

缺 owner 授权、无法证明删除传播、任何 backfill 覆盖旧 generation、或数据集将同一 provenance 泄漏
到独立测试切片时停止。

## 9. G5 目标卡：单源质量与独立校准

### Entry

G4 QUALIFIED；六源 corpus membership 冻结；评审者与训练/校准/测试边界明确。

### Work packages

| WP | 原子任务 | 产物 | 单项验证 |
|---|---|---|---|
| WP-G5-01 Dataset split | root-provenance grouped train/calibration/test | membership artifact | overlap/leakage=0 |
| WP-G5-02 Code gate | 50 case；version/location/graph/hard negative | source report | frozen thresholds |
| WP-G5-03 Codex gate | 45 case；sequence/goal/failure/privacy | source report | frozen thresholds |
| WP-G5-04 Experiment gate | 45 case；numeric/unit/compare/reproduce | source report | exact denominators |
| WP-G5-05 Notebook gate | 40 case；cell/output/order/stale/reproduce | source report | exact denominators |
| WP-G5-06 Document gate | 50 case；paragraph/table/figure/citation/claim | source report | exact denominators |
| WP-G5-07 Workspace gate | 40 case；state/as-of/blocker/dependency | source report | exact denominators |
| WP-G5-08 Calibration | fit calibration only；test ECE/Brier once | profiles/reports | slice support floor |
| WP-G5-09 Ops profile | P50/P95/P99、cost、zero/partial/error、top failures | source cards | environment bound |
| WP-G5-10 Source authorities | reviewer verifies exact evaluator output | registry candidates | fail-closed evaluator |
| WP-G5-11 Gate review | compare all source holds | G5 review | every routed source PASS |

### Exit

六源分别通过冻结门；校准指标来自独立 test；无来源可由联合分数掩盖未达标；每个 slice 的样本量、
失败和置信区间可见。

### Rollback/stop

测试集参与阈值选择、同根跨 split、删除 hard negative、缺少 denominator 或把 synthetic 成绩写成
真实质量时停止并废弃该 run。

## 10. G6 目标卡：多源融合、Wiki 导航与回答质量

### Entry

G5 QUALIFIED；所有参与联合路由的来源都有有效 source authority。

### Work packages

| WP | 原子任务 | 产物 | 单项验证 |
|---|---|---|---|
| WP-G6-01 Joint membership | ≥60 real cases；intent/roles/conflict/as-of/unanswerable | frozen dataset | root/source leakage=0 |
| WP-G6-02 Planner/routing | routing F1、capability、timeout/no-match | report | macro + slice metrics |
| WP-G6-03 Fusion/graph | entity/path/role/counter evidence/version | report | candidate membership audit |
| WP-G6-04 Wiki layers | build/search/navigation/raw/stop 分层 | report | denominators separated |
| WP-G6-05 Answer quality | citation precision/completeness、unsupported、refusal | report | claim-level verifier |
| WP-G6-06 Ablations | M-B0–M-B9；相同数据/预算/seed | ablation artifact | one-variable changes |
| WP-G6-07 Builder guard | affected improvement + guard no regression | report | same starting generation |
| WP-G6-08 Human/Judge | blind sample；rubric；versioned judge；disagreement | review artifact | evidence accessible |
| WP-G6-09 Portable package | config/model/index/prompt/dataset/revision | quality package | temp-copy verify |
| WP-G6-10 Gate review | fixed metrics and top failures | G6 review | all hard metrics PASS |

### Exit

达到总纲 §10 的全部固定指标；raw read-back 100%；false-supported、unauthorized、planner capability
violation 和 timeout-as-no-evidence 均为 0；P95 报告绑定规模/硬件/cache/并发。

### Rollback/stop

任何指标改变 denominator、混用不同 generation、用 synthetic 代替 real qualification、或 Judge 无法
访问原证据时停止。

## 11. G7 目标卡：容量、可靠性、安全与可观测性

### Entry

G6 QUALIFIED；目标规模、并发模型、SLO、成本预算和故障边界冻结。

### Work packages

| WP | 原子任务 | 产物 | 单项验证 |
|---|---|---|---|
| WP-G7-01 Capacity matrix | S/M/L page/ref/vector/edge；cold/warm cache | benchmark plan | real measured points |
| WP-G7-02 Latency/cost | source/joint/wiki/answer P50/P95/P99 | report | load generator manifest |
| WP-G7-03 Concurrency | read+publish、long nav、locks、restart | report | invariant checks |
| WP-G7-04 Fault injection | timeout/exception/corruption/stale pointer/partial publish | report | typed outcomes |
| WP-G7-05 Security | ACL/cache/secret/prompt/path/URL/tool injection | security artifact | leakage=0 |
| WP-G7-06 Recovery | backup/restore/reindex/generation rollback/tombstone | drill artifact | RPO/RTO measured |
| WP-G7-07 Tracing | query→answer spans、versions/counts/latency/cost/reason | schema/runtime | trace completeness |
| WP-G7-08 Dashboard/alerts | source/intent/ACL/version slices；SLO/quality alerts | ops assets | replayed incidents |
| WP-G7-09 Gate review | capacity/security/recovery packages | G7 review | all hard SLO PASS |

### Exit

达到总纲 §11 指标；混 generation、unauthorized、secret leakage 为 0；所有故障为 typed outcome；
M/L 真实测量；恢复演练有 RPO/RTO 和数据一致性证据。

### Rollback/stop

S fixture 外推为 L、未记录硬件/cache、故障吞掉后报 success、监控保存 query/raw secret、或恢复演练
破坏正式数据时停止。

## 12. G8 目标卡：Replay、Shadow、Canary 与 Rollback

### Entry

G7 QUALIFIED；生产 owner、安全/隐私/成本/值班授权；默认仍 V1。

### Work packages

| WP | 原子任务 | 产物 | 单项验证 |
|---|---|---|---|
| WP-G8-01 Preregistration | cohort/traffic/duration/sample/success/stop | signed plan | owner review |
| WP-G8-02 Offline replay | V1/V2/Wiki same logged requests | comparison | deterministic membership |
| WP-G8-03 Shadow plan | route/role/capability only | shadow artifact | user response unchanged |
| WP-G8-04 Shadow retrieval | Top-K/path/counter/version/latency/cost | shadow artifact | response unchanged |
| WP-G8-05 Shadow answer | claims/citations/refusal，不返回用户 | shadow artifact | unsupported/leakage audit |
| WP-G8-06 Internal canary | internal project/low-risk intent/small % | canary artifact | auto-stop wired |
| WP-G8-07 Expanded canary | project/intent/5→25% | canary artifact | cohort parity |
| WP-G8-08 Component rollback | planner/retriever/embed/rerank/fusion/context/prompt | 7 drills | request fallback once |
| WP-G8-09 Generation recovery | pointer/DB/index restore | recovery drill | no mixed generation |
| WP-G8-10 Observation window | preregistered stable period | ops artifact | alerts/quality stable |
| WP-G8-11 Gate review | all production evidence | G8 review | reviewer PASS |

### Exit

每阶段达到预注册时长、样本量和门槛；ACL/secret/wrong-version 任一事件自动停止；V1 请求级
fallback、generation rollback 和 DB recovery 均演练成功。

### Rollback/stop

不得事后改 cohort/threshold/stop rule；任何 hard safety event 立即停止，不等统计显著性；没有值班和
恢复权限时不进入 canary。

## 13. G9 目标卡：默认 V2 与持续运营

### Entry

G0–G8 全部 QUALIFIED，证据未过期，reviewed registries 完整。

### Work packages

| WP | 原子任务 | 产物 | 单项验证 |
|---|---|---|---|
| WP-G9-01 Evidence freshness | 校验所有 package checksum/age/dependency | freshness report | independent verify |
| WP-G9-02 Source registry | 六源 authority | reviewed registry | evaluator re-run exact |
| WP-G9-03 Gate registry | quality/security/capacity/canary/rollback | reviewed registry | all required gates |
| WP-G9-04 Release package | revision/datasets/config/model/index/prompt/routing | portable package | isolated copy verify |
| WP-G9-05 Progressive default | project/intent allowlist；可逆切换 | routing change | 0→5→25→100 evidence |
| WP-G9-06 V1 observation | 保留 fallback 一个稳定窗口 | observation report | fallback drill |
| WP-G9-07 Continuous ops | drift/freshness/citation/version/ACL/cost monitors | dashboards/alerts | synthetic incidents |
| WP-G9-08 Auto-degrade | hard gate→component/project/intent rollback | control rules | chaos validation |
| WP-G9-09 L5 review | 独立审查能力、证据、运行与残余风险 | final decision | reviewer authority |

### Exit

只有 canonical release evaluator 输出授权，才允许目标 project/intent 默认 V2。L5 不是永久状态；
任何 hard gate 回归都必须自动降级并产生事件证据。

### Rollback/stop

缺过期检查、registry 可由请求参数覆盖、一次性全量切换、V1 提前删除或告警无法触发自动降级时
禁止默认切换。

## 14. 跨阶段证据可追溯矩阵

| 风险/能力 | 首次建立 | 真实资格 | 生产证明 | 最终授权 |
|---|---|---|---|---|
| Clean reproducibility | G0 | G0 | CI 持续 | G9 freshness |
| Raw authority | G1 | G4/G6 | G8 shadow/canary | G9 source registry |
| Grounded answer | G2 | G6 | G8 shadow answer | G9 gate registry |
| Planner/as-of | G3 | G6 | G8 shadow plan | G9 release package |
| Real source coverage | G4 | G5/G6 | G8 | G9 freshness |
| Calibration/source quality | G5 | G5/G6 | G8 drift | G9 monitoring |
| Joint/Wiki quality | G6 | G6 | G8 | G9 gate registry |
| Capacity/security/recovery | G7 | G7 | G8 drills | G9 gate registry |
| Rollout/rollback | G8 | G8 | G8 | G9 routing authority |

## 15. Historical target snapshot（superseded）

### 15.1 项目主目标

`OBJ-RW-L5`：严格按 G0→G9 推进至 L5；任何阶段以最低证据等级作为真实状态；在 G9 授权前
保持 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`。

### 15.2 当时的外部准入目标

`WP-G0-05 Version admission`：由 owner 审阅当前大范围工作树并形成受控 revision，再从 clean
checkout 完成 locked build、Python 3.12/3.13 remote CI 和 portable verify。自动化代理不自行
stage/commit/push，因此该工作包保持 `IN_PROGRESS / OWNER_ACTION_REQUIRED`。

### 15.3 当时最近完成的可执行目标

`WP-G4D-01 Six-source corpus/materialization design`、`WP-G3D-01 Canonical planning design`、
`WP-G2D-01 Grounded-answer design`、`WP-G1D-01 Authority design`、`WP-G1D-02 Binding execution spec`、
`WP-G1D-03 Implementation readiness`、`WP-G1D-04 Entry/requirement trace`、
`WP-G1D-05 Offline Entry verifier` 与 `WP-G1D-06 Signing handoff and assembly`：在不实施 schema/runtime、
不改变默认 V1 的前提下，完成六源
raw authority、locator/selector、shared reader、Workspace event、Document parse artifact、V1
migration、failure code、测试矩阵和 Gate 定义，并进入设计评审。

完成条件：

1. 六源当前 raw/derivation/selector 缺口逐项有代码证据；
2. `RawEvidenceLocatorV2` 与 selector union 字段冻结；
3. shared authority 的校验顺序和 reason code 冻结；
4. 六源 gateway 不存在未定义的 authority 来源；
5. V1/V2 Wiki migration 不静默升级；
6. 测试覆盖 shared + per-source + E2E + migration + rollback；
7. 设计文档和台账互相引用，下一工作包入口明确。
8. V2 schema、canonical identity、事务、M0–M8 migration、dry-run artifact、BF-01–15 和 rollback
   达到不需实现者临场猜测的粒度。
9. reference-only、H/H_HEX/event ID、object/request visibility、source mapping、size/reason registry
   自洽，T1.1.1 拆为 C1–C10。
10. Entry E-01–10 可由离线 verifier 以带外 trust anchor、Ed25519 receipt、G0 原生验证和 CI 原子
    聚合签名重算；合成 fixture 与真实 authority 严格分离。
11. Entry handoff 以 PENDING draft、事实就绪 signing requests、外部 receipt 一一匹配、assembly 和原
    verifier 复核执行；仓库工具不能生成私钥、签名或自批。

G4D 又完成五类临时 V2 store、Code 正式双写、六源 generation/rollback/tombstone/cursor 的代码审计，
冻结 24-gap、四类 authority、语料 membership、22 项实施序列和 GM-01–54。当前结果已形成
字段/表/事务/迁移/代码映射级设计与可执行 Gate，并完成第一方
对抗性复核，状态为
`ENGINEERING_PASS / OWNER_REVIEW_HOLD`。

### 15.4 当时的准入目标

`WP-G0-05B Refined reproducible candidate` 已完成：V2 明确 source、opaque evaluation anchor、
generated output、sensitive path 和未登记文档资产边界。`WP-G0-05C Self-contained cleanroom`
以 V3 complete bundle 保留 Code Golden V2 身份，但最终 full 暴露 4 项绝对路径误报；
`WP-G0-05D Portable runtime authority` 已在 V4 修复并完成 ordinary clone、40 项 Code
Golden/baseline、前端 179 和后端 2,067 项重放。`WP-G0-05E Portable Python identity` 随后关闭
V5 在 Python 3.12 暴露 callable/AST 漂移，V6 关闭；V7/V8 完成文档收口；V9 补齐 owner/CI
handoff，却在 clean full 暴露 generated HTML 隐含前置。V10 修复 source authority，又暴露
exclusion/release 语义矛盾；`WP-G0-05H` 以 V11 分层 exact/approved/addition，保持双 Python
2,077，并完成 backend-before-build、frontend 和 portable release-ready 全链。V12/V13 收口 G1
readiness/current authority；V14/V15 关闭 router advisory 与 external target；V16/V17 纳入 Entry
verifier 和 signing handoff。`WP-G0-05K` 以 V18 补齐 exact npm、strict install-script approval、
lock registry/integrity、pending=0 与隐藏 `.npmrc` source authority；V19 仅收口 post-run 文档与当前
指针。`WP-G0-05L` 以 V20 新增始终 PENDING 的逐 path/scope/exclusion/action owner-review packet，
使 broad staging、伪批准、manifest substitution 和 source drift 均可机器拒绝。`WP-G0-05M` 以 V21
纳入全阶段 atomic objective graph；V22 再纳入 G2 raw-verified grounded-answer execution spec；
V23 纳入 G3 canonical planning execution spec；V24 纳入 G4 corpus/materialization/backfill execution
spec 和 packet V5；V25/V26 纳入 G5–G8 specs 与 execution control，packet 换代到 V7。V25 source exact、
packet verify、Ruff 和 15 项定向测试通过；V26 由 excluded receipt 固定。随后 V26 本地双 Python 各
2,126 与严格前端 191 回放通过，但 nanoid high finding 触发最小 lock-only 修复和 V27/packet V8 换代。
V24 的 frontend/backend/audit/SBOM/cleanroom 仍是历史证据，不能冒充 V27 same-revision remote full。

当前剩余目标是 `WP-G0-05 Version admission` 的 owner 部分，原子顺序固定为：

1. 验证 V27 admission 与 owner-review packet V8，逐项审阅 1,136 个文件、10 个 scope、exclusion、
   34.5 MB history bundle、npm install policy 与当前 release-evidence；
2. 形成一致 revision，并取消跟踪 `web/index.html`；
3. 新 clean checkout 执行 self-contained cleanroom；
4. locked install；
5. frontend audit/SBOM artifact、tests/typecheck/build，并补做真实浏览器 hash/navigation/back 验收；
6. backend full；
7. `--require-release-ready`；
8. Python 3.12/3.13 与 admission job 的 remote CI，保存同一 revision 的 run URL/conclusion；
9. 新 G0 package 与复核；
10. 按文档 31 生成真实 draft/request，收集组织外部 receipts 与带外 trust digest，assembly 后由原
    verifier 输出 `ENTRY_QUALIFIED`。

当前状态：`IN_PROGRESS / OWNER_ACTION_REQUIRED / QUALITY_HOLD`。自动化代理不自行
stage/commit/push，不能用 synthetic clean candidate 代替 owner revision。

### 15.5 当时的下一目标（未解锁）

`WP-G1I-01 Binding foundation`：只有 `WP-G0-05` 通过且 G1D owner review 完成后解锁。第一项
原子实现固定为 `T1.1.1 C1–C10 canonical JSON/digest/ID/reason contract`；之后才是 ACL、schema、
store、migration。
不得同时修改六源 gateway、Wiki 或 active pointer。

## 16. 每轮执行更新模板

每轮必须在台账追加：

```text
work_package:
entry_gate:
fixed_inputs:
files_changed:
mutations_performed:
tests_and_results:
artifact_path_and_sha256:
formal_data_read_or_written:
new_risks_or_decisions:
status_after_run:
next_unlocked_atomic_task:
owner_action_required:
```

若没有 artifact，不得写质量结论；若没有受审 revision，不得写 clean reproducible；若没有生产
观测，不得写 production qualified。

## 17. Current-stage resolution and external actions

§15 保留为 dated historical snapshot，不再承担恢复职责。当前 reviewed/pending/active/next 角色从
`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md` 解析，并核对 Git refs、owner decisions、bundle 与
admission/packet 文件 SHA。record 缺失、无效或矛盾时：

```text
CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION
```

解析出的当前任务必须属于本文件已有 objective card，且 mutation/verification/exit/stop 不得被 durable record
弱化。当前阶段的一般动作顺序保持：

1. 完成 durable record 指定的一个 G0 local candidate，并取得新的 owner decision；
2. remote 存在且 owner 授权后，绑定 protected revision、exact-source authority manifest 与 same-run artifacts；
3. 取得 same-revision Python 3.12/3.13、cleanroom、strict frontend supply-chain、release-ready、UI reviewer
   和 independent Gate evidence；
4. 只有 G0 `QUALIFIED` 且 G1 Entry E-01–E-10 `PASS`，才可把 T1.1.1 C1–C10 设为 active；
5. 后续依次 G1→G2→G3→G4→G5→G6→G7→G8→G9，不并行启动未解锁 runtime。

remote URL、保护分支/PR 规则、push 授权、protected authority values、run/attempt/artifact/attestation 是外部
handoff 所需真实输入；缺失时保持 `QUALITY_HOLD`，但不把它误报为当前 local task 的 blocker，也不要求 owner
运行本地 Git 命令。
