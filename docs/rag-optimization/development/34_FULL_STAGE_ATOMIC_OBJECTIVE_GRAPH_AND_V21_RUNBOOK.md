# RAG + Wiki 全阶段原子目标图与 V21+ 执行 Runbook

版本：2026-08-06 V1  
状态：`EXECUTION_AUTHORITY / ACTIVE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`  
上位约束：`10_RAG_WIKI_MATURITY_PROGRAM.md`  
阶段验收卡：`14_RAG_WIKI_STAGE_OBJECTIVE_BREAKDOWN.md`  
当前准入：由 `artifacts/rag-maturity/control/CURRENT_TASK_STATE.md` 解析并核对 Git/receipts
目的：把“完善 RAG/Wiki”重设为有依赖、有证据、有停止条件的全阶段原子目标图。

## 1. 本轮目标重设

### 1.1 Mission 不变，执行目标改为可关闭单元

长期 Mission 为 `OBJ-RW-L5`：六类真实来源均可从受控版本回读原始证据；Wiki 只负责组织和导航；
所有回答 claim 均能绑定 project、ACL、source version、generation、as-of 和 raw evidence；质量、性能、
安全与发布由独立、可复现 evidence package 授权；V2 默认切换可按项目与 intent 回滚。

当前执行目标由 durable record 选择，但必须满足以下稳定合同：

```text
OBJ-RESOLVED-G0
  outcome:
    让 resolved reviewed bytes 获得受保护 revision、同 revision remote CI、
    external UI evidence 和独立 G0 Gate decision；在此之前不进入 G1 runtime 写入。
  fixed inputs:
    durable state 绑定的 admission + PENDING packet + independent owner decision + G0-OWNER-01..07 receipts
  allowed mutations:
    仅 active task plan 的 exact allowlist；owner 明确授权的 Git/source/generated 边界
  forbidden mutations:
    正式数据库、未授权 push/sign/approve、伪造远程或人工证据、提前发布 V2
  exit:
    G0 = QUALIFIED，且 G1 Entry E-01..E-10 offline verifier PASS
```

durable record 缺失、无效或与 Git/receipts 矛盾时，结果必须是
`CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`。历史 candidate 只保留不可变 evidence，不因
record 缺失而成为 fallback。

### 1.2 目标层级

| 层级 | 标识 | 最大范围 | 必需关闭证据 |
|---|---|---:|---|
| Mission | `OBJ-RW-L5` | G0–G9 | L5 Review + release package |
| Stage gate | `G0`–`G9` | 单个成熟度阶段 | stage review decision |
| Work package | `WP-Gx-nn` | 一个可独立交付结果 | artifact + tests + review |
| Atomic objective | `Tx.y.z` | 一个主要 mutation 或一个外部 decision | 精确 diff/receipt + 单项 verify |
| Run | `RUN-*` | 一次不可覆盖执行 | inputs/results/failures/digests |

原子目标只允许以下状态：`NOT_STARTED → IN_PROGRESS → ENGINEERING_PASS → QUALITY_HOLD → QUALIFIED`；
失败使用 `FAILED`，同一外部条件连续三个目标轮次无变化且没有其他可推进工作时才使用
`BLOCKED_EXTERNAL`。

## 2. 所有任务统一完成合同

每个 `Tx.y.z` 必须在台账中回答八个问题：

1. **Outcome**：完成后新增了什么可观察能力；
2. **Frozen input**：revision、manifest、dataset、generation、config、model 和时间点；
3. **Mutation set**：允许改哪些文件、表、索引、运行状态或外部系统；
4. **Negative boundary**：本任务明确不做什么；
5. **Verification**：正向、负向、故障、回滚和 portability 测试；
6. **Evidence**：不可覆盖 artifact、canonical digest、环境与失败摘要；
7. **Exit**：可机器判断的通过条件与需要的人类/外部 authority；
8. **Stop/Rollback**：哪些信号触发停止，如何恢复到上一 generation/default。

以下任一缺失时，不得把任务标成 `QUALIFIED`：输入不可重放、denominator 不完整、失败样本被删除、
测试与正式运行不是同一 revision、审批由代码作者自签、或只证明“能运行”却声称“质量成熟”。

## 3. 阶段依赖图与全局硬门

```mermaid
flowchart LR
  G0["G0 可复现与版本准入"] --> G1["G1 六源 Raw Authority"]
  G1 --> G2["G2 Live Wiki Grounded Answer"]
  G2 --> G3["G3 Canonical Planner / Scope / as-of"]
  G3 --> G4["G4 真实六源 Materialization"]
  G4 --> G5["G5 单源质量与独立校准"]
  G5 --> G6["G6 多源、Wiki 与回答质量"]
  G6 --> G7["G7 性能、可靠性、安全、观测"]
  G7 --> G8["G8 Replay / Shadow / Canary / Rollback"]
  G8 --> G9["G9 默认 V2 与持续运营"]
```

全程硬门：

- `ACL leakage = 0`、`secret leakage = 0`；
- page、snippet、candidate、summary 不得冒充 raw authority；
- 不支持 as-of 的来源必须拒绝或显式 partial，不能回退到 current；
- source timeout、source error、no match、unauthorized 必须保持不同 failure code；
- 每次查询固定 generation/watermark，执行中不得混用新旧快照；
- 任何发布 authority 都不能由仓库内自填布尔值产生；
- G8 通过前 `DEFAULT_V1`，G9 授权后仍保留请求级 V1 rollback。

## 4. G0 — 可复现工程与版本准入

### Stage outcome

同一受审 source revision 能在 clean checkout 和远程 CI 中用 Python 3.12/3.13、严格前端供应链和
相同 Gate 命令完整重放；owner、UI reviewer 和独立 Gate reviewer 的权威边界清楚。

### Historical G0 atomic ledger and stable objective classes

下表保存早期原子拆分与当时状态；其“当前状态”列不是恢复入口。实际 active/next 由 durable record 解析，
并受相同依赖/Owner/Exit/Stop 约束。

| ID | 依赖 | 动作与唯一结果 | 验证/证据 | Owner | 当前状态 |
|---|---|---|---|---|---|
| T0.28.1 | V24 | 原生 verify V24 与 1,124-item packet V5 | exact + canonical packet verify | agent | HISTORICAL_ENGINEERING_PASS |
| T0.28.1R | V24/V26 drift | V25/V6 intermediate、V26/V7 documentation capture、lock-only security fix 与 V27/V8 refresh | exact + canonical packet + 双 Python 2,126 + frontend 191/audit 0 + excluded receipts | agent | ENGINEERING_PASS |
| T0.28.2 | T0.28.1 | 逐 path/scope 审阅 admitted source | 10 scope receipts；无跳项 | repository owner | QUALITY_HOLD |
| T0.28.3 | T0.28.1 | 逐项处置 83 个 listed exclusions，并核对 29,022 denominator | exclusion disposition receipt | repository owner | QUALITY_HOLD |
| T0.28.4 | T0.28.2–3 | 取消跟踪可重建的 `web/index.html`，source 保留 `frontend/index.html` | clean build 重建；source tree 无 tracked runtime HTML | repository owner | QUALITY_HOLD |
| T0.28.5 | T0.28.2–4 | 创建受保护 owner-reviewed revision | commit/tree bytes 与 admission source digest 一致 | protected revision control | QUALITY_HOLD |
| T0.28.6 | T0.28.5 | 从普通 clone/materialize 执行 clean full replay | backend-before-build、frontend、exact、cleanroom、release-ready | agent/CI | NOT_STARTED |
| T0.28.7 | T0.28.5 | 同 revision 运行双 Python 后端矩阵 | 3.12/3.13 全收集、全通过、无排除 | remote CI | NOT_STARTED |
| T0.28.8 | T0.28.5 | 同 revision 运行严格前端供应链 | exact npm、allowScripts、pending=0、audit=0、SBOM | remote CI | NOT_STARTED |
| T0.28.9 | 本地/远程可访问 UI | 真实浏览器完成 LIVE-02 | identity、非空页面、console error=0、hash nav、back/forward | UI reviewer | QUALITY_HOLD |
| T0.28.10 | T0.28.5–9 | 汇总 G0-OWNER-01..07 为受信 receipts | keyset、time、subject、run URL、artifact digest | external authorities | NOT_STARTED |
| T0.29.1 | T0.28.10 | 独立复核 G0 package | negative evidence、限制、rollback 全可见 | independent reviewer | NOT_STARTED |
| T0.29.2 | T0.29.1 | 形成唯一 G0 decision | `QUALIFIED` 或带 reason 的 `HOLD/FAIL` | independent reviewer | NOT_STARTED |
| T0.30.1 | G0 QUALIFIED | 装配 G1 Entry E-01..E-10 | offline verifier + native G0 recompute PASS | entry assembler | NOT_STARTED |

### LIVE-02 本轮事实

2026-08-06 本地隔离服务在 `127.0.0.1:8765` 成功启动，`GET /` 返回 200，HTML title 为
`TraceWiki · 循证知识工作台`。但 in-app browser 的 admin-enforced policy 校验不可用，连续两次拒绝
本机页面访问。该结果只能证明启动和静态入口，不能证明渲染、console、点击、hash 或历史导航。
禁止改用 HTTP 抓取冒充 LIVE-02；T0.28.9 继续为 `QUALITY_HOLD`。

### G0 exit / stop

- Exit：T0.28.2–10、T0.29.1–2 全部有外部真实证据；same-revision Gate PASS；无 tracked generated
  blocker；G1 Entry verifier PASS。
- Stop：owner scope 与 manifest bytes 不一致、远程 run revision 不同、浏览器未实际交互、或任一供应链
  步骤未执行时维持 `VERSION_ADMISSION_HOLD`。
- Rollback：不覆盖 V20/V21；产生新 candidate 和新 packet；默认 V1 不变。

## 5. G1 — 六源 Raw Evidence Authority

### Stage outcome

Code、Codex、Experiment、Notebook、Document、Workspace 六源都由 project-scoped durable binding 定位
raw authority；调用方不能传 storage path 或覆盖 selector；错误 project/ACL/version/digest/tombstone 全部
fail closed。

| ID | 依赖 | 原子结果 | 验证与证据 |
|---|---|---|---|
| T1.1.1 | G1 Entry PASS | 新建并列表/约束/index，不改旧表语义 | BF-01 schema + migration dry-run |
| T1.1.2 | T1.1.1 | writer 自算 raw digest，claimed digest 只作 expected | tamper/mismatch/side-effect=0 |
| T1.1.3 | T1.1.1 | project + ACL + source identity 唯一键 | cross-project collision matrix |
| T1.1.4 | T1.1.1 | internal storage model 与 public projection 分离 | path/payload-ref non-disclosure tests |
| T1.1.5 | T1.1.2–4 | binding resolve/invalidate/tombstone API | ambiguity、stale、delete、audit tests |
| T1.1.6 | T1.1.5 | M0–M8 migration 与 quarantine | dry-run denominator、resume、rollback |
| T1.2.1 | T1.1 | shared secure reader：managed root/symlink/size/digest | traversal/symlink/TOCTOU matrix |
| T1.2.2 | T1.2.1 | selector union 验证与 bounded read | invalid range/pointer/cell/span fail closed |
| T1.2.3 | T1.2.1–2 | typed failure + privacy-safe audit | unauthorized existence non-disclosure |
| T1.3.1 | T1.2 | Code commit/blob/UTF-8 line-byte gateway | wrong commit/blob/range matrix |
| T1.3.2 | T1.3.1 | Code backfill + old-ref quarantine | rename/delete/history fixtures |
| T1.4.1 | T1.2 | Codex raw item + ordered thread manifest gateway | order/redaction/tombstone matrix |
| T1.4.2 | T1.4.1 | Codex backfill | missing item/duplicate event/privacy fixtures |
| T1.5.1 | T1.2 | Experiment run JSON-pointer + artifact gateway | numeric/unit/dataset/checksum matrix |
| T1.5.2 | T1.5.1 | Experiment backfill | failed run/drift/artifact absence fixtures |
| T1.6.1 | T1.2 | Notebook revision/cell/output gateway | stale/order/producer/output matrix |
| T1.6.2 | T1.6.1 | Notebook backfill | revision mutation/parameter/error fixtures |
| T1.7.1 | T1.2 | Document parse artifact/span/table/figure/citation gateway | parser/OCR/version/selector matrix |
| T1.7.2 | T1.7.1 | Document backfill | conflict/parse failure/deletion fixtures |
| T1.8.1 | T1.2 | Workspace append-only state-event gateway | valid/observed/system time matrix |
| T1.8.2 | T1.8.1 | Workspace outbox/idempotency/backfill | delete/dependency/as-of/collision fixtures |
| T1.9.1 | T1.3–8 | WikiSourceRefV2 protected binding migration | V1 navigation-only；无静默升级 |
| T1.9.2 | T1.9.1 | generation rebuild 与 rollback | membership/digest/atomic publish tests |
| T1.10.1 | T1.1–9 | 六源 positive + wrong-* 故障包 | raw read-back 100%；leakage=0 |
| T1.10.2 | T1.10.1 | 独立 G1 Gate review | portable package + rollback rehearsal |

Exit：每源至少一个真实 positive 与完整 negative fixture；raw read-back 100%；旧 V1 ref 缺 raw 时只能拒绝，
不得退回 snippet。Stop：任一 API 接受任意 storage path、按“最新”猜版本、跨项目泄漏存在性或 mutation
没有 durable event。

## 6. G2 — Live Wiki Grounded Answer

| ID | 依赖 | 原子结果 | 验证与证据 |
|---|---|---|---|
| T2.1.1 | G1 QUALIFIED | 冻结 raw/observed/reported/inferred/verified 状态机 | transition/property tests |
| T2.1.2 | T2.1.1 | 明确 reviewer authority 与禁止升级规则 | adversarial policy review |
| T2.2.1 | T2.1 | Live Organizer 保留 raw/derived/status/binding | projection + downgrade tests |
| T2.3.1 | T2.2 | Compiler 写入 V2 ref 与 generation membership | atomic publish/isolation tests |
| T2.3.2 | T2.3.1 | Store 拒绝 orphan/stale/wrong-generation ref | migration/rollback tests |
| T2.4.1 | T2.3 | Navigator 区分 page-found 与 raw-verified | budget/deadline/missing-raw matrix |
| T2.5.1 | T2.4 | Claim verifier 只接受允许的 authority | unsupported claim 不渲染 |
| T2.5.2 | T2.5.1 | counter evidence 与 partial/refusal 政策 | conflict/refusal tests |
| T2.6.1 | T2.1–5 | 六源各 1 positive + 1 refusal live E2E | 12-case ingestion-to-answer artifact |
| T2.7.1 | T2.6 | consistent/conflict/as-of/ACL/generation E2E | citation membership + wrong-version=0 |
| T2.8.1 | T2.7 | 独立 G2 Gate review | full-chain replay + rollback |

Exit：`source → raw binding → retrieve → organize → compile → publish → navigate → raw verify → Evidence
Pack → answer/refusal` 全链可重放。Stop：page obligation 在 raw verify 前标 supported，或失败 raw read 仍生成
肯定答案。

## 7. G3 — Canonical Planner、Scope 与 as-of

| ID | 依赖 | 原子结果 | 验证与证据 |
|---|---|---|---|
| T3.0.1 | 只读审计 | 四规划链/Scope/as-of/snapshot/fallback code map 与 execution spec | 20-gap matrix + 42-case Gate design；ENGINEERING_PASS |
| T3.1.1 | G2 QUALIFIED | IntentV3/EvidenceRoleV3 registry | 九 intent 无 fallback 漂移；角色投影显式 |
| T3.1.2 | T3.1.1 | ScopeResolutionV3 contract | merge/conflict property tests |
| T3.1.3 | T3.1.2 | TemporalTargetV3 contract | UTC/future/interval/version matrix |
| T3.2.1 | T3.1 | CapabilityRegistryV3 | unsupported/expired/forged fail closed |
| T3.3.1 | T3.2 | SnapshotManifestV3 coordinator | transaction/lease/skew/mid-swap tests |
| T3.4.1 | T3.3 | CanonicalQueryPlanV3 builder | canonical/tamper/copy/property tests |
| T3.4.2 | T3.4.1 | immutable planning receipt/store | CAS/idempotency/ACL tests |
| T3.5.1 | T3.4 | public request adapter | lossless field projection |
| T3.5.2 | T3.5.1 | legacy/global adapter | no downstream replan；filters preserved |
| T3.5.3 | T3.5.1 | typed V2 adapter | historical/change intents不退化 |
| T3.5.4 | T3.5.1 | Wiki adapter | roles/sources/pinned manifest/as-of parity |
| T3.6.1 | T3.5 | SourceOutcomeV3 + preflight-only exactly-once fallback | fault matrix；post-start fallback=0 |
| T3.6.2 | T3.6.1 | obligation-driven corrective policy | zero-gain stop；no count-only sufficiency |
| T3.7.1 | T3.6 | ordinary/Wiki unified generation policy | same pack same generation decision |
| T3.8.1 | T3.1–7 | 三入口 parity replay | same request/authority/snapshot same plan digest |
| T3.9.1 | T3.8 | portable Gate 与独立 G3 review | CP-01–42 + plan/snapshot/pack/citation replay |

Exit：同一规范请求产生同一 plan digest；不支持 as-of 的来源显式拒绝；单次 query 使用固定快照。
Stop：多个 planner 继续各自决策、召回后才做 ACL、fallback 重复执行或混用 generation。

## 8. G4 — 真实六源 Materialization 与 Backfill

详细字段、状态机、source-specific difficult slices、GM-01–54 和 Gate artifact 以
`37_G4_SIX_SOURCE_CORPUS_MATERIALIZATION_BACKFILL_EXECUTION_SPEC.md` 为权威。

| ID | 依赖 | 原子结果 | 验证与证据 |
|---|---|---|---|
| T4.0.1 | 只读审计 | 六源 ingest/store/runtime/generation code map | MAT-01–24；ENGINEERING_PASS |
| T4.1.1 | G3 QUALIFIED | `QualificationDataAuthorizationV1` + external receipts | forged/expired/role/revision fail closed |
| T4.1.2 | T4.1.1 | `QualificationCorpusManifestV1` + root provenance | canonical/tamper/drift/duplicate-root tests |
| T4.1.3 | T4.1.2 | authorization/corpus exact assembler | source/field/time/ACL/exclusion match |
| T4.2.1 | T4.1 | `MaterializationRunV1` control schema | state CAS/idempotency/side-effect ledger |
| T4.2.2 | T4.2.1 | `SourceGenerationManifestV3` | digest/count/watermark tamper matrix |
| T4.2.3 | T4.2.2 | `ActiveGenerationSetV1` + CAS | exactly six；fault 时旧 pointer 不变 |
| T4.3.1 | T4.2 | generation-bound raw binding migration | NULL inventory/dry-run/backfill/revert |
| T4.4.1 | T4.3 | Code staging adapter | no early active；2 repos×2 versions |
| T4.4.2 | T4.3 | Codex staging adapter | ≥20 threads；live/redaction/remove/cursor |
| T4.4.3 | T4.3 | Experiment staging adapter | ≥20 runs/5 groups；observed version/delete |
| T4.4.4 | T4.3 | Notebook staging adapter | ≥10×2；stale/error/order/tombstone |
| T4.4.5 | T4.3 | Document staging adapter | ≥20；multi-version/provider/tombstone |
| T4.4.6 | T4.3 | Workspace staging adapter | ≥3×2；audit/state/version/as-of limitation |
| T4.5.1 | T4.4 | `MaterializationCoverageReportV1` | membership conservation/orphan/ACL/version |
| T4.5.2 | T4.4–5.1 | 六源 coordinator | single-source fault→partial active set=0 |
| T4.5.3 | T4.5.2 | exact-set Wiki builder hook | Wiki/set mismatch fail closed |
| T4.6.1 | T4.5 | checkpoint/resume/lease fencing | restart/cancel/late worker/input drift |
| T4.7.1 | T4.6 | `DeletionPropagationRunV1` | raw→index→Wiki→cache/export；SLA |
| T4.7.2 | T4.7.1 | rollback/retention/cleanup | deletion watermark blocks resurrection |
| T4.8.1 | T4.1–7 | production-mirror full dry-run | schema/index/capacity/fault；formal writes=0 |
| T4.9.1 | T4.8 | portable Gate 与独立 G4 review | GM-01–54 + governance/coverage/rollback |

Exit：六源都有 owner-authorized、非空、版本化 generation；backfill 可重入、恢复、回滚。Stop：缺授权、
删除传播不可证、覆盖旧 generation 或 qualification/test 根来源泄漏。

## 9. G5 — 单源质量与独立校准

| ID | 依赖 | 原子结果 | 验证与证据 |
|---|---|---|---|
| T5.1.1 | G4 QUALIFIED | 按 root provenance 冻结 train/calibration/test | overlap/leakage=0 |
| T5.2.1 | T5.1 | Code 50-case source gate | version/location/graph/hard-negative |
| T5.3.1 | T5.1 | Codex 45-case source gate | sequence/goal/failure/privacy |
| T5.4.1 | T5.1 | Experiment 45-case source gate | numeric/unit/compare/reproduce |
| T5.5.1 | T5.1 | Notebook 40-case source gate | cell/output/order/stale/reproduce |
| T5.6.1 | T5.1 | Document 50-case source gate | paragraph/table/figure/citation/claim |
| T5.7.1 | T5.1 | Workspace 40-case source gate | state/as-of/blocker/dependency |
| T5.8.1 | T5.2–7 | 只在 calibration split 拟合阈值 | frozen profile + fit trace |
| T5.8.2 | T5.8.1 | test split 单次 ECE/Brier/coverage | slice floor + confidence intervals |
| T5.9.1 | T5.2–8 | 每源 P50/P95/P99/cost/failure cards | bound environment + exact denominator |
| T5.10.1 | T5.9 | reviewer 复核 evaluator 原始输出 | source authority candidates |
| T5.11.1 | T5.10 | 独立 G5 review | 所有可路由来源分别 PASS |

Exit：六源独立通过冻结门；联合分数不能掩盖单源失败；test 不参与选阈值。Stop：同根跨 split、删除
hard negative、缺 denominator、重复查看 test 调参或用 synthetic 冒充真实质量。

## 10. G6 — 多源融合、Wiki 导航与回答质量

| ID | 依赖 | 原子结果 | 验证与证据 |
|---|---|---|---|
| T6.1.1 | G5 QUALIFIED | 冻结 ≥60 real joint cases | intent/role/conflict/as-of/unanswerable；leakage=0 |
| T6.2.1 | T6.1 | planner/routing macro + slice report | routing F1/capability/timeout/no-match |
| T6.3.1 | T6.1 | fusion/graph report | entity/path/role/counter/version audit |
| T6.4.1 | T6.1 | Wiki build/search/nav/raw/stop 分层指标 | denominators separated |
| T6.5.1 | T6.1 | claim-level answer quality | citation precision/completeness/unsupported/refusal |
| T6.6.1 | T6.2–5 | M-B0–M-B9 one-variable ablations | same data/budget/seed |
| T6.7.1 | T6.6 | builder affected/guard/hard-gate | same starting generation |
| T6.8.1 | T6.5 | blind human review + versioned judge calibration | disagreement + evidence access |
| T6.9.1 | T6.1–8 | portable quality package | config/model/index/prompt/dataset/revision |
| T6.10.1 | T6.9 | 独立 G6 review | frozen gates all PASS |

Exit：真实 joint 集上 routing、fusion、Wiki、claim、refusal 分层过门；unsupported claim=0；收益高于冻结
成本/延迟预算。Stop：人工样本不盲、judge 版本不固定、消融多变量同时变化或把页面命中当回答正确。

## 11. G7 — 容量、可靠性、安全与可观测性

| ID | 依赖 | 原子结果 | 验证与证据 |
|---|---|---|---|
| T7.1.1 | G6 QUALIFIED | 冻结 S/M/L 数据、硬件、并发、cache profile | capacity manifest |
| T7.2.1 | T7.1 | 单源检索 latency/cost | P50/P95/P99 + saturation |
| T7.2.2 | T7.1 | 联合/Wiki/生成全链 latency/cost | stage attribution |
| T7.3.1 | T7.2 | reader/publish/rollback/navigation 并发测试 | consistency/race evidence |
| T7.4.1 | T7.3 | timeout/exception/corruption/partial-publish fault matrix | typed failure + no false success |
| T7.5.1 | T7.3 | ACL/cache/secret/prompt/path/tool injection matrix | leakage=0 |
| T7.6.1 | T7.4–5 | backup/restore/re-index drill | RPO/RTO evidence |
| T7.6.2 | T7.6.1 | tombstone/delete propagation drill | bounded completion time |
| T7.7.1 | T7.2–6 | end-to-end trace fields | query→plan→source→pack→claim |
| T7.7.2 | T7.7.1 | dashboard/alerts/runbook | alert fire + acknowledgement drill |
| T7.8.1 | T7.1–7 | portable perf/security/recovery packages | copy verify |
| T7.9.1 | T7.8 | 独立 G7 review | SLO/security/recovery gates |

Exit：冻结环境下容量与 SLO 可复现；故障不伪成功；恢复和删除传播完成；告警可操作。Stop：用 S fixture
外推生产、cache 跨 ACL、partial publish 可见、恢复丢 raw binding 或 trace 含 secret。

## 12. G8 — Replay、Shadow、Canary 与 Rollback

| ID | 依赖 | 原子结果 | 验证与证据 |
|---|---|---|---|
| T8.1.1 | G7 QUALIFIED | 预注册流量、样本量、时长、成功/停止门 | approved experiment plan |
| T8.2.1 | T8.1 | Offline V1/V2/Wiki replay | fixed membership + paired metrics |
| T8.3.1 | T8.2 | Shadow plan only | no user-visible effect |
| T8.4.1 | T8.3 | Shadow retrieval/Evidence Pack | leakage/latency/coverage gates |
| T8.5.1 | T8.4 | Shadow answer/refusal | claim diff + refusal honesty |
| T8.6.1 | T8.5 | internal low-risk canary | stop-rule automation |
| T8.7.1 | T8.6 | expanded project/intent canary | segment guardrails |
| T8.8.1 | T8.6–7 | 七组件请求级 rollback drill | planner/source/wiki/answer/config/model/index |
| T8.9.1 | T8.8 | generation/database recovery drill | RPO/RTO + audit continuity |
| T8.10.1 | T8.1–9 | 独立 G8 review | observation windows complete |

Exit：预注册门全部通过，无 P0/P1 安全事件；停止规则和请求级/数据级 rollback 均真实演练。Stop：样本未满
提前宣布、改门后不重启实验、shadow 影响用户、canary 缺 kill switch 或 rollback 只在单元测试中存在。

## 13. G9 — 默认 V2、发布资格与持续运营

| ID | 依赖 | 原子结果 | 验证与证据 |
|---|---|---|---|
| T9.1.1 | G8 QUALIFIED | 汇总 G0–G8 evidence freshness/completeness | stale/missing evidence=0 |
| T9.2.1 | T9.1 | reviewed six-source authority registry | protected owner approvals |
| T9.3.1 | T9.1 | quality/security/capacity/canary registry | reviewed immutable entries |
| T9.4.1 | T9.1–3 | portable release package | independent temp-copy verify |
| T9.5.1 | T9.4 | 按 project/intent 可逆切换默认 V2 | change record + exact target scope |
| T9.6.1 | T9.5 | V1 fallback 稳定观察窗口 | no-regression + rollback readiness |
| T9.7.1 | T9.5 | drift/freshness/citation/ACL/cost monitors | thresholds + ownership |
| T9.8.1 | T9.7 | 自动降级/回滚/incident evidence | game-day drill |
| T9.9.1 | T9.1–8 | L5 Production Qualification Review | final decision + known limitations |

Exit：默认 V2 的授权、切换、观察、降级、回滚和持续监控全部受保护且可审计。Stop：任何 registry 可由
本地作者自改、release package 不可独立重放、V1 fallback 被提前删除或监控没有明确 owner。

## 14. 当前执行队列与并行边界

### 14.1 严格顺序的 resolved 关键路径

```text
durable state → exact reviewed/pending/admission/packet bindings
  → active G0 local candidate exact allowlist + owner decision
  → protected revision
  → exact-source authority transport + remote same-revision gates
  → external UI review
  → independent G0 review
  → G0 QUALIFIED
  → G1 Entry E-01..E-10
  → T1.1.1
```

### 14.2 可并行但不能越 Gate 的工作

- owner 可并行审阅不同 scope，但每个 scope 必须有独立 denominator/digest；
- remote backend 3.12/3.13 可并行，但最终 release-ready 必须等待全部 matrix 与前端证据；
- LIVE-02 可与 remote CI 并行，但不能由 HTTP/static check 替代；
- G1 设计、威胁模型、fixture 计划可继续只读完善；G1 migration/runtime 写入必须等待 G0 QUALIFIED；
- G4 数据授权准备可提前沟通，但不得拉取或物化正式数据。

## 15. 目标切片与每轮工作规则

每轮只领取一个能形成独立结果的 atomic objective，并在开始前记录：

```text
active_atomic_id
parent_gate
frozen_inputs
allowed_mutations
verification_commands
expected_artifacts
external_authority_needed
stop_condition
```

完成后只允许三类结论：

- `ENGINEERING_PASS`：本地可控验证已通过，但外部/真实质量证据仍缺；
- `QUALITY_HOLD`：本地结果可复现，明确等待 owner、数据、UI、远程运行或观察窗口；
- `QUALIFIED`：本阶段所有 hard exit 和外部 authority 均已满足。

“代码写完”“测试大多通过”“已有模拟数据”“页面 HTML 可访问”“CI workflow 已定义”都不是
`QUALIFIED` 的同义词。

## 16. Historical candidate lineage and current generation rules

本文件最初产生 V21；G2/G3/G4 execution specs 随后产生 V22/V23/V24；G5–G8 specs 与 execution
control 使 V24 exact fail closed，因此用 V25 intermediate 和 V26 documentation-complete candidate 换代；
V26 后续本地严格回放发现并以 lock-only 修复关闭供应链 high finding，当时再换代 V27/V8，
不得改写 V20–V26：

1. 更新 development/root README、总纲、台账、scope manifest、阶段卡和 owner handoff 的 current pointer；
2. 构建 `admission-20260829-v27/admission.json`，执行 native exact verify；
3. V27 cleanroom replay 当时作为本地验证目标，不复用 V24 历史运行冒充；
4. 构建 `owner-review-20260829-v8/review-packet.json`；
5. packet 必须保持 `PENDING_OWNER_REVIEW`，不得把本地作者写成 owner；
6. 记录 file/scope/exclusion/blocker denominator、source/content/review subject digest；
7. 保留 V26 后续双 Python backend、严格 frontend/audit/SBOM 的实际回放证据，并运行 V27 admission/packet tests；
8. 所有 remote、LIVE、owner、revision 字段继续为空或 PENDING，直到真实 receipt 到达。

V27 只是包含 G2–G8 规格、执行控制、供应链 lock 修复和最新 source bytes 的候选，不会因为本地回放
或文档更完整自动关闭 G0。

以上 V20–V27 流程是 historical lineage。新的 candidate 不覆盖任何历史对象，并从 durable state 解析出的
exact reviewed base 建立独立 branch/worktree；先冻结 plan/allowlist/verification，再形成一个最小 commit、
complete-history bundle、ordinary restore、successor admission/PENDING packet 和独立 owner interaction。
source 不把 successor version 写成永久 current pointer；packet 不自批，local bundle 不冒充 protected remote。

## 17. 当前停止点

允许的本地动作只来自 durable record 中 active task 的 exact plan/allowlist。若该 record 或其绑定无法核对，
停止 source mutation；若 diff 越界、历史 evidence 被改写、remote/Gate/release 被本地结果冒充，也在 commit
前停止。外部 authority 缺失必须明确列出所需输入/交互，但不能静默跳过，也不能把尚可推进的 local task误写
为外部阻塞。

无论 current local task 如何变化，G0 `QUALIFIED` + G1 Entry `PASS` 前都不执行 T1.1.1，不写正式数据库，
不创建自签 approval，不发布或切换默认 V2。protected remote、remote CI、UI reviewer、independent Gate
和 release authority 各自保持独立证据边界。
