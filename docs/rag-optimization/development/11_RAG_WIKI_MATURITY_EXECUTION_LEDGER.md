# RAG + Wiki 成熟化持续执行台账

版本：2026-08-06  
执行权威：`10_RAG_WIKI_MATURITY_PROGRAM.md`  
当前项目状态：`G0 LOCAL_REVIEWED_PROGRESS / REMOTE_AND_INDEPENDENT_GATE_HOLD / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`
当前任务解析：`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md`；缺失/矛盾时 `NO_SOURCE_MUTATION`
最近完成的可执行工作包：`WP-G4D-01 Six-source corpus/materialization execution design`、
`WP-G3D-01 Canonical planning execution design`、
`WP-G2D-01 Grounded-answer execution design`、
`WP-G0-05M Atomic objective reset`、
`WP-G0-05L Owner review packet`、
`WP-G0-05K npm install-script admission`、
`WP-G1D-06 Signing handoff and assembly`、
`WP-G1D-05 Offline Entry verifier`、
`WP-G0-05J Frontend router supply-chain closure`、
`WP-G1D-04 Entry/requirement trace`、
`WP-G1D-03 Implementation readiness reconciliation`

## 1. 台账使用规则

本文件记录执行状态，不重写设计。每次推进必须更新：

1. 工作项状态；
2. 实际变更范围；
3. 验证命令与结果；
4. 新发现、风险和决策；
5. 对应不可变 evidence package；
6. 下一工作项及其前置条件。

状态只允许：`NOT_STARTED`、`IN_PROGRESS`、`ENGINEERING_PASS`、`QUALITY_HOLD`、
`QUALIFIED`、`BLOCKED_EXTERNAL`、`FAILED`。

## 2. 2026-08-06 初始审查基线

### 2.1 仓库与验证

| 项目 | 审查结果 |
|---|---|
| Git 工作树 | 69 个 modified/staged、157 个 untracked |
| `src/evidence_rag/rag/**` tracked files | 0 |
| `src/evidence_rag/rag/wiki/**` tracked files | 0 |
| tracked Wiki/multisource tests | 0 |
| Ruff | PASS |
| 前端 | 31 files / 178 tests PASS，typecheck PASS |
| 多源 + Wiki 专项 | 68/68 PASS |
| 完整后端 | 1 failed / 12 errors；C-B1/C-B2/C-B5 publication fixture 漂移 |

### 2.2 当前运行配置

| 配置 | 当前值/状态 |
|---|---|
| deployment | development；ACL enforcement 未启用 |
| Code | engine=v1，builder=raw-v1，graph=false，reranker=off，context=snippet-v1 |
| Global V2 calibration | 未配置 |
| Wiki dense/reranker | 未配置 |
| Wiki external inference | 禁止 |
| Active Wiki DB | 不存在 |
| Release | DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE |

### 2.3 正式库只读覆盖快照

| 来源 | 有效覆盖 | 关键缺口 |
|---|---|---|
| Code | 3 active legacy generations；5812 entities | V2 units/publications/calibrations=0 |
| Codex | 67 threads；752 turns | 最新 generation validations=0 |
| Experiment | 0 experiments/runs/metrics | 来源为空 |
| Notebook | 0 notebooks/runs/cells/outputs | 来源为空 |
| Document | 4 docs；279 sections；48 tables | citations/figures/claim-evidence=0 |
| Workspace | 2 topics；1 cancelled work item | iterations=0，关系覆盖不足 |
| Evaluation | 0 formal evaluation runs/cases/judgments | 无真实联合资格证据 |

## 3. 项目级 Gate 台账

| Gate | 名称 | 状态 | 前置 | 退出证据 |
|---|---|---|---|---|
| G0 | 可复现与真值基线 | QUALITY_HOLD | 无 | local owner-reviewed ref/bundle 与 exact-source CI transport 已有证据；protected remote、same-revision CI、external UI、independent decision 仍缺 |
| G1 | 六源原始证据权威 | QUALITY_HOLD | G0 `QUALIFIED` + G1 Entry `PASS` | 详细设计与 readiness 已完成；runtime/schema/gateway 尚未解锁 |
| G2 | Live Wiki grounded answer | NOT_STARTED | G1 | G2D execution spec 已完成；runtime 仍等 G1；live E2E positive/refusal/conflict/as-of/ACL |
| G3 | Planner/Scope/as-of 统一 | NOT_STARTED | G2 | G3D execution spec 已完成；runtime 仍等 G2；canonical plan parity、capability violation=0 |
| G4 | 真实六源 materialization | NOT_STARTED | G3 + owner scope | G4D execution spec 已完成；runtime 仍等 G3/数据授权；六源非空 generation、backfill/delete/rollback evidence |
| G5 | 单源质量与校准 | NOT_STARTED | G4 | 六源独立 Golden、holdout calibration、source gates |
| G6 | 多源/Wiki/回答质量 | NOT_STARTED | G5 | 60-case、多源指标、真实 Wiki quality artifact |
| G7 | 容量/可靠性/安全/观测 | NOT_STARTED | G6 | S/M/L、fault/security/recovery/ops artifacts |
| G8 | Replay/Shadow/Canary/Rollback | NOT_STARTED | G7 + production authority | 分阶段生产观测和演练证据 |
| G9 | 默认 V2 与持续运营 | NOT_STARTED | G8 | reviewed registry、portable release package |

## 4. G0 详细工作分解

| ID | 工作项 | 状态 | 变更范围 | 验收 |
|---|---|---|---|---|
| G0.1 | 冻结 sanitized baseline inventory | ENGINEERING_PASS | `maturity_g0_v1.py` + baseline artifact | canonical verify PASS；未读取正式库 |
| G0.2 | 明确版本范围和未跟踪实现归属 | ENGINEERING_PASS | Git scope + exact source manifests + owner decisions | 逐文件 path/size/hash/state 可验证；本地 reviewed revisions 可恢复；remote protection 另行待办 |
| G0.3 | 新增 Code dense-index 显式开关 | ENGINEERING_PASS | config/runtime/evaluation/tests | `rag_code_dense_index` 默认兼容、C-B1 显式 false |
| G0.4 | 修复 C-B1 回归 | ENGINEERING_PASS | evaluation/runtime tests | C-B1 7/7 PASS；旧 v1 artifact 精确兼容 |
| G0.5 | 验证 C-B2/C-B5 连锁恢复 | ENGINEERING_PASS | evaluation tests | C-B1/B2/B5 23/23 PASS |
| G0.6 | 建立后端 CI | ENGINEERING_PASS | `.github/workflows/backend-ci.yml` | Python 3.12/3.13 locked matrix；所有 PR/main 触发；待 GitHub 实跑 |
| G0.7 | 建立统一验证入口 | ENGINEERING_PASS | Makefile/README | smoke/integration/evaluation/full 四层可执行 |
| G0.8 | 运行完整产品回归 | ENGINEERING_PASS | backend/frontend | V11 源码 Python 3.12/3.13 backend 各 2077 PASS；frontend 31 files/179 PASS；无排除 |
| G0.9 | 生成 G0 evidence package | ENGINEERING_PASS | artifacts/rag-maturity | 6/6 commands；9-file package；copy verify PASS |
| G0.10 | 编写 G0 Gate Review | QUALITY_HOLD | reviews/20_* | 工程 PASS；等待 version admission/远程 CI |
| G0.11 | 冻结 V1 逐文件 candidate | ENGINEERING_PASS | v1 builder/tests/artifact | 保留历史负证据；因生成物过宽、eval anchor 不完整而被 V2 取代 |
| G0.12 | 构建 V2 refined reproducible candidate | ENGINEERING_PASS | v2 builder/tests/policy/materialize | 1078 files；12 opaque SQLite anchors；隔离 release-ready；2060/179 PASS |
| G0.13 | 构建 V3 self-contained history cleanroom | ENGINEERING_PASS | complete Git bundle/manifest/harness/tests | 1083 files；431 objects；ordinary clone；Code baseline 40；完整 backend 暴露 4 项路径误报 |
| G0.14 | 构建 V4 portable runtime-authority candidate | ENGINEERING_PASS | Experiment authority/relocation regression/V4 admission | Foundation v4、E-B0 v7；正式工作树 backend 2067 PASS；最终 cleanroom 重放按 admission 验收 |
| G0.15 | Owner revision + clean checkout + remote CI | IN_PROGRESS | local reviewed revision/bundle 已完成；受保护 remote/GitHub runs/new package 待办 | generated HTML 已按 owner 处置；3.12/3.13 remote CI 未发生 |
| G0.16 | Portable Python identity + V6–V8 admission | ENGINEERING_PASS | shared identity/Codex/Experiment/Release/CB6/Notebook/tests/docs/V6–V8 | persistent identity 跨 minor；runtime tamper strict；V6 runtime evidence；V8 1089 files cleanroom release-ready |
| G0.17 | Owner admission full-chain CI + V9 | ENGINEERING_PASS | workflow/3 contract tests/handoff/review/V9 | 双 Python后 exact/cleanroom/frontend/release-ready；1092 files；remote run HOLD |
| G0.18 | Clean checkout full replay + V10 | ENGINEERING_PASS | source-entry fix/rehearsal/review/V10 | V9 2074/1 负证据；V10 无 generated HTML 前置；1094 files |
| G0.19 | Portable exclusion semantics + V11 | ENGINEERING_PASS | verifier/2 adversarial tests/docs/review/V11 | exact vs approved 分层；ignored secret 拒绝；1096 files |
| G0.20 | G1 readiness documentation reconciliation + V12 | ENGINEERING_PASS | spec/design/readiness/review/index/V12 | 1098 files；cleanroom PASS；保留 current-candidate 索引中间状态 |
| G0.21 | Current-authority reconciliation + V13 | ENGINEERING_PASS | 历史/current 引用修正/Makefile/V13 | 1098 files；产品源码与 V11 相同；exact + cleanroom release-ready |
| G0.22 | Frontend router supply-chain closure + V14 | ENGINEERING_PASS | bounded first-party router/4 tests/lock/audit-SBOM CI/docs/review/V14 | 1104 files；frontend 183；audit 0；保留中间证据 |
| G0.23 | External route target fail-closed + V15 | ENGINEERING_PASS | protocol/scheme-relative negative test/docs/pointer/V15 | 1104 files；frontend 184；audit 0；exact + cleanroom release-ready；remote/browser pending |
| G0.24 | G1 Entry offline verifier + V16 | ENGINEERING_PASS | verifier/27 tests/CLI/Make/docs/review/V16 | 1108 files；双 Python backend 2104；Entry+workflow 30；exact + cleanroom；真实 Entry HOLD |
| G0.25 | G1 Entry signing handoff + V17 | ENGINEERING_PASS | draft/requests/assembly/40 tests/Make/docs/review/V17 | 1111 files；双 Python backend 2117；Entry+workflow 43；exact + cleanroom；真实 authority HOLD |
| G0.26 | npm install-script admission + V18 | ENGINEERING_PASS | exact npm/strict allowScripts/lock graph+integrity/pending/hidden `.npmrc`/workflow/docs/V18 | 1116 files；frontend 191；双 Python backend 2118；audit 0/SBOM；exact + cleanroom；remote authority HOLD |
| G0.27 | npm admission documentation-final + V19 | ENGINEERING_PASS | current pointers/post-run wording/owner handoff/V19 | 1116 files；产品代码与 V18 相同；exact + cleanroom；owner/remote/LIVE-02 HOLD |
| G0.28 | Owner review packet + V20 | ENGINEERING_PASS | PENDING packet builder/verifier/8 tests/Make/docs/review/V20 | 1120 files；逐 path/scope/exclusion/action；双 Python 2126；exact + cleanroom；真实 owner HOLD |
| G0.29 | Full-stage atomic objective reset + V21 | ENGINEERING_PASS | G0–G9 atomic graph/current queue/LIVE-02 evidence boundary/V21/packet V2 | 1121 files；每项依赖/结果/证据/owner/exit/stop；不越 G0；真实 owner/remote/UI HOLD |
| G0.30 | G2 grounded-answer execution spec + V22 | ENGINEERING_PASS | current code map/V2 refs+receipts/page-raw obligations/EvidencePackV3/32-case Gate/V22 | 1122 files；candidate/snippet 假 raw 显式关闭；G1 前 runtime 锁定 |
| G0.31 | G3 canonical planning execution spec + V23 | ENGINEERING_PASS | four-planner code map/Scope+TemporalTarget+Snapshot+adapter+fallback/42-case Gate/V23 | 1123 files；silent as-of/current 与 intent/filter 漂移显式关闭；G2 前 runtime 锁定 |
| G0.32 | G4 corpus/materialization/backfill execution spec + V24 | ENGINEERING_PASS | six-source code map/authorization+corpus+generation-set+coverage+resume+delete+rollback/54-case Gate/V24 | 1124 files；临时 V2 与正式 generation 分层；G3/真实数据授权前 runtime 锁定 |
| G0.33 | G5–G8 specs + execution control + V24 drift refresh | ENGINEERING_PASS | V25/V6 intermediate exact/canonical；V26/V7 当时的 documentation-complete candidate；15 targeted tests | 1135 files；10 scopes；83 listed exclusions；packet 始终 PENDING；后由 G0.34 换代 |
| G0.34 | V26 local full replay + supply-chain lock refresh | ENGINEERING_PASS | Python 3.12/3.13 各 2,126；frontend 191/audit 0；V27/V8 post-build receipts | 1,136 files；packet 始终 PENDING；owner/remote/LIVE 未产生 |
| G0.35 | Owner-reviewed local progression、exact-source CI transport 与 current-state resolver | IN_PROGRESS | immutable owner decisions/local refs/bundles；transport workflow；docs-only control sync | remote/protected/independent Gate 不自授；current task 从 durable record 解析 |

## 5. G1–G9 详细工作包

这些工作项已经识别，但 G0 Gate 前不得进入实现。

### G1 原始证据

| ID | 工作项 | 状态 |
|---|---|---|
| G1D.1 | 六源 raw/derivation/selector 文件级盘点 | ENGINEERING_PASS |
| G1D.2 | RawEvidenceLocatorV2 与 selector union 合同（owner review hold） | ENGINEERING_PASS |
| G1D.3 | Shared authority API、校验顺序与 failure codes（owner review hold） | ENGINEERING_PASS |
| G1D.4 | WikiSourceRefV2 与 V1 安全迁移策略（owner review hold） | ENGINEERING_PASS |
| G1D.5 | shared/per-source/E2E/migration/rollback 测试矩阵（owner review hold） | ENGINEERING_PASS |
| G1D.6 | V2 并列表、canonical identity、事务与 M0–M8 migration 规格 | ENGINEERING_PASS |
| G1D.7 | dry-run artifact、BF-01–15、rollback 与独立 execution-spec review | ENGINEERING_PASS |
| G1D.8 | contract→code readiness、spec V2 reconciliation、T1.1.1 C1–C10 | ENGINEERING_PASS |
| G1D.9 | Entry E-01–10、external attestation、D1–D5、data authorization、BF side-effect trace | ENGINEERING_PASS |
| G1D.10 | Offline verifier：canonical/Ed25519/keyset/G0 native/CI aggregate/E-01–10/CLI | ENGINEERING_PASS |
| G1D.11 | Signing handoff：PENDING draft/fact-ready requests/receipt matching/assembly/双重验证 | ENGINEERING_PASS |
| G1.1 | Binding schema/store/models/migration（G0 HOLD） | NOT_STARTED |
| G1.2 | Code raw blob/range gateway | NOT_STARTED |
| G1.3 | Codex raw item gateway | NOT_STARTED |
| G1.4 | Experiment raw structured gateway | NOT_STARTED |
| G1.5 | Notebook revision/cell/output gateway | NOT_STARTED |
| G1.6 | Document paragraph/table/figure/citation gateway | NOT_STARTED |
| G1.7 | Workspace temporal event gateway | NOT_STARTED |
| G1.8 | Wiki source-ref digest 迁移 | NOT_STARTED |
| G1.9 | 六源 ACL/version/tombstone/digest matrix | NOT_STARTED |
| G1.10 | G1 Gate Review | NOT_STARTED |

### G2 Live Wiki grounded answer

| ID | 工作项 | 状态 |
|---|---|---|
| G2D.1 | 当前 candidate/raw/obligation/claim 代码链映射 | ENGINEERING_PASS |
| G2D.2 | WikiSourceRefV2、read receipt、page/raw 双状态合同 | ENGINEERING_PASS |
| G2D.3 | EvidencePackV3 与 claim authority truth table | ENGINEERING_PASS |
| G2D.4 | 六源 ingestion E2E、32-case Gate、rollback 规格 | ENGINEERING_PASS |
| G2.1 | EvidenceFact status/review/raw-derived 状态机 | NOT_STARTED |
| G2.2 | Live Organizer 权威状态投影 | NOT_STARTED |
| G2.3 | Compiler raw locator 绑定 | NOT_STARTED |
| G2.4 | Navigator raw-verified obligation | NOT_STARTED |
| G2.5 | Claim verifier 派生事实政策 | NOT_STARTED |
| G2.6 | 六源 live positive/refusal E2E | NOT_STARTED |
| G2.7 | 跨源 conflict/as-of/ACL E2E | NOT_STARTED |
| G2.8 | G2 Gate Review | NOT_STARTED |

### G3 Planner/Scope/as-of

| ID | 工作项 | 状态 |
|---|---|---|
| G3D.1 | 普通/legacy/V2/Wiki 四规划链代码与字段差异映射 | ENGINEERING_PASS |
| G3D.2 | IntentV3、EvidenceRoleV3、CanonicalQueryPlanV3 与 Scope/Temporal 合同 | ENGINEERING_PASS |
| G3D.3 | CapabilityRegistryV3、SnapshotManifestV3 与三入口 adapter 合同 | ENGINEERING_PASS |
| G3D.4 | exactly-once fallback、unified generation、42-case Gate 与 rollback 规格 | ENGINEERING_PASS |
| G3.1 | CanonicalQueryPlanV3 | NOT_STARTED |
| G3.2 | Legacy planner adapter | NOT_STARTED |
| G3.3 | Wiki planner adapter | NOT_STARTED |
| G3.4 | include/scope/required_roles 合并 | NOT_STARTED |
| G3.5 | supports_as_of capability gate | NOT_STARTED |
| G3.6 | per-source generation/watermark snapshot | NOT_STARTED |
| G3.7 | planner parity/replay/fallback tests | NOT_STARTED |
| G3.8 | G3 Gate Review | NOT_STARTED |

### G4 真实六源 materialization/backfill

| ID | 工作项 | 状态 |
|---|---|---|
| G4D.1 | 六源 ingest/store/runtime/generation 与临时派生层代码审计 | ENGINEERING_PASS |
| G4D.2 | authorization/corpus/root-provenance/authority 分层合同 | ENGINEERING_PASS |
| G4D.3 | materialization run/source generation/active generation-set CAS 合同 | ENGINEERING_PASS |
| G4D.4 | coverage conservation/resume/fencing/delete/rollback/54-case Gate | ENGINEERING_PASS |
| G4.1.1 | QualificationDataAuthorizationV1 + external receipts | NOT_STARTED |
| G4.1.2 | QualificationCorpusManifestV1 + root provenance | NOT_STARTED |
| G4.1.3 | authorization/corpus exact assembler | NOT_STARTED |
| G4.2.1 | MaterializationRunV1 control schema/state CAS | NOT_STARTED |
| G4.2.2 | SourceGenerationManifestV3 | NOT_STARTED |
| G4.2.3 | ActiveGenerationSetV1 + exactly-six/CAS | NOT_STARTED |
| G4.3.1 | generation-bound raw binding migration | NOT_STARTED |
| G4.4.1 | Code 2 repo×2 version staging adapter | NOT_STARTED |
| G4.4.2 | Codex ≥20 thread staging adapter | NOT_STARTED |
| G4.4.3 | Experiment ≥20 run/5 group staging adapter | NOT_STARTED |
| G4.4.4 | Notebook ≥10×2 staging adapter | NOT_STARTED |
| G4.4.5 | Document ≥20 root staging adapter | NOT_STARTED |
| G4.4.6 | Workspace ≥3 topic×2 iteration staging adapter | NOT_STARTED |
| G4.5.1 | MaterializationCoverageReportV1 + conservation | NOT_STARTED |
| G4.5.2 | six-source stage/validate/set coordinator | NOT_STARTED |
| G4.5.3 | exact-set Wiki builder hook | NOT_STARTED |
| G4.6.1 | checkpoint/resume/lease fencing | NOT_STARTED |
| G4.7.1 | DeletionPropagationRunV1 + SLA closure | NOT_STARTED |
| G4.7.2 | no-resurrection rollback/retention/cleanup | NOT_STARTED |
| G4.8.1 | production-mirror full dry-run；formal mutation=0 | NOT_STARTED |
| G4.9.1 | GM-01–54 portable Gate + independent review | NOT_STARTED |

### G5 单源质量与独立校准

| ID | 工作项 | 状态 |
|---|---|---|
| G5.1 | 冻结六源 Golden membership 与 root-grouped split | NOT_STARTED |
| G5.2 | Code 50-case source gate | NOT_STARTED |
| G5.3 | Codex 45-case source gate | NOT_STARTED |
| G5.4 | Experiment 45-case source gate | NOT_STARTED |
| G5.5 | Notebook 40-case source gate | NOT_STARTED |
| G5.6 | Document 50-case source gate | NOT_STARTED |
| G5.7 | Workspace 40-case source gate | NOT_STARTED |
| G5.8 | calibration split 拟合与独立 test ECE/Brier | NOT_STARTED |
| G5.9 | per-source latency/cost/failure/top-error report | NOT_STARTED |
| G5.10 | G5 Gate Review | NOT_STARTED |

### G6 多源、Wiki 与回答质量

| ID | 工作项 | 状态 |
|---|---|---|
| G6.1 | 冻结真实 60-case 多源 Golden 和困难切片 | NOT_STARTED |
| G6.2 | route/role/entity/path/counter/version/conflict 评测 | NOT_STARTED |
| G6.3 | citation/claim/refusal/partial honesty 评测 | NOT_STARTED |
| G6.4 | Wiki path/search/navigation/raw-read/answer 分层评测 | NOT_STARTED |
| G6.5 | M-B0–M-B9 消融与成本收益报告 | NOT_STARTED |
| G6.6 | Builder affected/guard/hard-gate 评测 | NOT_STARTED |
| G6.7 | 独立人工抽样与版本化 Judge 校准 | NOT_STARTED |
| G6.8 | 真实质量 portable artifact | NOT_STARTED |
| G6.9 | G6 Gate Review | NOT_STARTED |

### G7 容量、可靠性、安全与可观测性

| ID | 工作项 | 状态 |
|---|---|---|
| G7.1 | S/M/L 数据规模、硬件、并发和 cache 基线 | NOT_STARTED |
| G7.2 | 单源/联合/Wiki/生成 P50/P95/P99 | NOT_STARTED |
| G7.3 | reader/publish/rollback/long-navigation 并发测试 | NOT_STARTED |
| G7.4 | timeout/exception/corruption/partial-publish 故障矩阵 | NOT_STARTED |
| G7.5 | ACL/cache/secret/prompt/path/tool injection 安全矩阵 | NOT_STARTED |
| G7.6 | backup/restore/re-index/tombstone propagation 演练 | NOT_STARTED |
| G7.7 | 全链 trace、dashboard 和 alert | NOT_STARTED |
| G7.8 | 容量/安全/恢复 portable artifacts | NOT_STARTED |
| G7.9 | G7 Gate Review | NOT_STARTED |

### G8 Replay、Shadow、Canary 与 Rollback

| ID | 工作项 | 状态 |
|---|---|---|
| G8.1 | 预注册流量、样本量、时长、停止和成功门 | NOT_STARTED |
| G8.2 | Offline V1/V2/Wiki replay | NOT_STARTED |
| G8.3 | Shadow plan | NOT_STARTED |
| G8.4 | Shadow retrieval/Evidence Pack | NOT_STARTED |
| G8.5 | Shadow answer/refusal | NOT_STARTED |
| G8.6 | Internal low-risk canary | NOT_STARTED |
| G8.7 | Expanded project/intent canary | NOT_STARTED |
| G8.8 | 七组件请求级 rollback drill | NOT_STARTED |
| G8.9 | generation/database recovery drill | NOT_STARTED |
| G8.10 | G8 Gate Review | NOT_STARTED |

### G9 发布与持续运营

| ID | 工作项 | 状态 |
|---|---|---|
| G9.1 | 汇总 G0–G8 evidence freshness 与完整性 | NOT_STARTED |
| G9.2 | reviewed six-source authority registry | NOT_STARTED |
| G9.3 | reviewed quality/security/capacity/canary registry | NOT_STARTED |
| G9.4 | portable release package 与独立 verify | NOT_STARTED |
| G9.5 | 分项目/intent 可逆默认 V2 切换 | NOT_STARTED |
| G9.6 | V1 fallback 稳定观察周期 | NOT_STARTED |
| G9.7 | drift/freshness/citation/ACL/cost 持续监控 | NOT_STARTED |
| G9.8 | 自动降级、回滚和事件证据 | NOT_STARTED |
| G9.9 | L5 Production Qualification Review | NOT_STARTED |

## 6. 风险台账

| ID | 风险 | 级别 | 缓解措施 | 状态 |
|---|---|---:|---|---|
| R-001 | 未跟踪实现无法从 HEAD 重建 | P0 | G0.2 版本范围和 clean checkout 验证 | OPEN |
| R-002 | Wiki candidate snapshot 被误称 raw evidence | P0 | G1 六源 gateway；历史文档勘误 | OPEN |
| R-003 | Live derived fact 永远无法通过 claim authority | P0 | G2 状态机和 live E2E | OPEN |
| R-004 | 三套 planner 语义漂移 | P1 | G3 canonical plan + adapters | OPEN |
| R-005 | as-of 跨源混时态 | P0 | capability gate + generation/watermark snapshot | OPEN |
| R-006 | Experiment/Notebook 空源导致虚假六源结论 | P0 | G4 qualification corpus | OPEN |
| R-007 | in-sample calibration 指标过度乐观 | P1 | root-grouped holdout | OPEN |
| R-008 | S fixture 性能外推到生产 | P1 | G7 实测 S/M/L 和并发 | OPEN |
| R-009 | 历史 COMPLETE 声明覆盖新失败 | P1 | Gate 状态取最低证据等级 | MITIGATED |
| R-010 | raw object identity/unique key 不含 project/ACL | P0 | G1I.1 project-scoped V2 identity；迁移歧义 quarantine | OPEN |
| R-011 | source event idempotency 不含 project/ACL | P0 | G1I.1 V2 idempotency；跨项目 collision tests | OPEN |
| R-012 | raw/event API 直接返回 storage path/payload ref | P0 | 内外模型分离；public projection；project-scoped by-id/tombstone | OPEN |
| R-013 | 调用方声明 content hash 未与 payload 复核 | P0 | writer 自算 digest；claimed digest 仅作 expected value | OPEN |
| R-014 | blocked entity/global stats 未按 project 隔离 | P0 | `(project_id, entity_id)` V2 key 与 project-scoped query | OPEN |
| R-015 | Codex callable digest 绑定 checkout 绝对路径 | P0 | path-independent structural code digest + relocation test | MITIGATED |
| R-016 | Evaluation snapshot 持久化绝对 repository root | P0 | 固定 `<repository-root>` projection + baseline regression | MITIGATED |
| R-017 | React Router RSC-mode CSRF advisory 的 patched 版本尚不可从 npm registry 安装 | P1 | 删除第三方 router；bounded first-party Hash/Memory router；184 regression；external target rejection；remote audit/SBOM Gate | CLOSED_LOCAL_ENGINEERING / REMOTE_ATTESTATION_PENDING |
| R-018 | 已跟踪 `web/index.html` 与 source/generated 边界冲突 | P0 | owner revision 取消跟踪；build 后重建；V3 release blocker | OPEN |
| R-019 | Synthetic materialize 依赖原仓库提供 Code Golden 固定 Git object | P0 | 内容寻址 complete bundle；安全复扫；ordinary clone cleanroom | MITIGATED |
| R-020 | Experiment runtime authority 摘要绑定 checkout/.venv 绝对路径 | P0 | module origin 逻辑投影；Foundation v4/E-B0 v7；跨平台路径回归与 cleanroom full | MITIGATED |
| R-021 | G1I-01 仍需实现者临场决定 schema/迁移/回滚，易在旧 identity 上形成不安全兼容 | P0 | 独立执行规格；V2 并列表；M0–M8；BF-01–15；owner D1–D5 | MITIGATED_DESIGN / IMPLEMENTATION_PENDING |
| R-022 | 持久 callable authority 绑定 CPython minor bytecode/stdlib 私有路径 | P0 | portable source/AST identity + same-interpreter source recompile；双 Python full matrix | MITIGATED_V6 |
| R-023 | Notebook external ref 依赖 `ast.dump` 次版本空字段格式 | P0 | 固定 empty-field-free AST dump；历史 package 在 3.13 不变、3.12 对齐 | MITIGATED_V6 |
| R-024 | path-filtered backend CI 可被 docs/admission/frontend 变更绕过，且缺少最终 release-ready | P0 | 所有 PR/main 触发；matrix 后串行全链 Gate；3 项合同测试 | MITIGATED_V9 / REMOTE_RUN_PENDING |
| R-025 | backend test 读取被排除的 `web/index.html`，脏工作树全绿掩盖 clean checkout 缺文件 | P0 | 源入口改为 `frontend/index.html`；backend-before-build clean replay；V9 负证据 | MITIGATED_V10 |
| R-026 | exclusion exact=false 仍可 release，且 cache/缺失排除对象与新增敏感对象未分层 | P0 | exact/approved/addition 三字段；release 合取；ignored secret 测试 | MITIGATED_V11 |
| R-027 | 同一 checkout 并行运行两套 evaluation full 会互相改变 workspace fingerprint | P1 | 本地 full 串行；GitHub matrix 独立 checkout；不共享 run workspace | MITIGATED_EXECUTION |
| R-028 | G1 V1 spec 要求 reference-only 无 bytes，却强制 digest/length 非空；H/event ID/visibility/source mapping 仍可多解 | P0 | spec V2 nullable CHECK；H/H_HEX；deterministic event ID；visibility 分层；显式 source registry | MITIGATED_DESIGN / IMPLEMENTATION_PENDING |
| R-029 | 现有 `bindings/` 关系候选被误当成 raw evidence binding 扩展 | P0 | G1 固定新 `sources/v2` 边界；candidate 仅作 derived/review input；readiness trace | MITIGATED_DESIGN / IMPLEMENTATION_PENDING |
| R-030 | G1 spec 只要求 owner revision/CI，而上位目标要求 G0 QUALIFIED，可能漏过供应链 HOLD | P0 | Entry E-01–10 采用更严格 G0 QUALIFIED；供应链例外必须限时且禁止发布阶段 | MITIGATED_DESIGN / EXTERNAL_DISPOSITION_PENDING |
| R-031 | 仓库作者可用自填 `approved=true`/reviewer 字符串伪造 owner authority | P0 | protected review/受信签名/变更系统 attestation；offline receipt/keyset；local self-sign 无效 | MITIGATED_DESIGN / EXTERNAL_ATTESTATION_PENDING |
| R-032 | BF 负向测试只断言 exception，仍可能留下 blob/row/audit/response 泄漏 | P0 | 每条 BF 固定 before/after state digest 与 side-effect invariant | MITIGATED_DESIGN / IMPLEMENTATION_PENDING |
| R-033 | 本地浏览器安全策略不可验证，HTTP/test 证据可能被误报为真实页面交互验收 | P1 | LIVE-02 单列；禁止绕过；remote/人工补 page identity、console、hash/navigation/back | OPEN_EVIDENCE_GAP |

## 7. 决策记录

### ADR-MAT-001：维持 V1 默认

- 日期：2026-08-06
- 决策：G0–G8 未全部通过前保持 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`。
- 理由：真实六源、raw authority、全量回归和生产资格均不完整。

### ADR-MAT-002：修复实现，不放宽 C-B1 断言

- 日期：2026-08-06
- 决策：为 Code dense materialization 增加显式配置意图。
- 理由：C-B1 的 treatment 是 AST + exact/sparse，运行时不应因为 `ast-v2` 隐式启用 dense。
- 禁止方案：把 C-B1 的 `embedding == not-built` 断言改成接受任意值。

### ADR-MAT-003：raw authority 必须来自 source store

- 日期：2026-08-06
- 决策：WikiStore candidate envelope 只作为编译 provenance，不作为最终 raw authority。
- 理由：它不能检测 adapter/parser/summary 在写入 Wiki 前产生的错误。

### ADR-MAT-004：G0 HOLD 期间只冻结 G1 设计

- 日期：2026-08-06
- 决策：允许只读盘点和 `WP-G1D-01` 设计；禁止 binding schema、gateway、Wiki V2 runtime
  和正式 backfill 实现。
- 理由：当前实现尚未进入 owner-reviewed revision；继续叠加 runtime 变更会扩大不可复现范围。
- 设计权威：`13_G1_RAW_EVIDENCE_AUTHORITY_DESIGN.md`。

### ADR-MAT-005：Workspace 必须新增 append-only raw state event

- 日期：2026-08-06
- 决策：Workspace 正式表当前状态不能单独作为历史 raw authority；create/update/transition/delete
  必须生成版本化 canonical event，并与 raw binding、valid time 和 tombstone 协同。
- 理由：当前 `SourceEventInput` 不支持 workspace，Workspace mutation 也没有 raw derivation，无法
  满足六源 raw read-back 或真实 as-of。

### ADR-MAT-006：Document 二进制原文使用内容寻址 parse artifact

- 日期：2026-08-06
- 决策：PDF/DOCX 等证据必须绑定 raw bytes digest、adapter/parser environment 和 canonical
  parse output digest；不得只回读 formal DB snippet。
- 理由：二进制原文无法用简单 byte range 证明解析文本，且 parser 漂移会改变 page/table/figure
  定位。

### ADR-MAT-007：Raw identity、event idempotency 和 tombstone 必须项目化

- 日期：2026-08-06
- 决策：G1 binding foundation 同时迁移 raw logical identity、event idempotency、blocked entity、
  get/tombstone/stats API 到 project/visibility scope；物理 bytes 去重不等于 metadata authority 共享。
- 理由：当前 raw unique/raw ID/event idempotency 均缺 project/ACL，无法证明跨项目隔离；API 还会
  直接返回 storage path 和默认指向该路径的 payload ref。
- 附加约束：writer 自算 payload digest；reference-only object 不能成为 raw evidence。

### ADR-MAT-008：G0 V2 区分 source、opaque eval anchor 与 generated output

- 日期：2026-08-06
- 决策：V2 准入源码、批准的评测定义/不可变 SQLite bytes anchor 与发布证据；排除 Web/Tauri
  生成物、sidecar、cache、敏感路径和未登记设计图片。
- 理由：V1 同时存在 source set 过宽和评测输入不完整，不能证明 clean reconstruction。
- 发布约束：已跟踪的生成物不是普通排除项，而是 `--require-release-ready` 硬阻断。

### ADR-MAT-009：运行位置不参与 evaluator/callable 身份

- 日期：2026-08-06
- 决策：函数摘要忽略 `co_filename`；评测 implementation snapshot 将物理仓库路径投影为
  `<repository-root>`。
- 理由：源码内容与运行位置正交；绝对路径既破坏跨 checkout 重放，也泄露开发机/CI 信息。

### ADR-MAT-010：前端 RSC advisory 作为显式质量例外

- 日期：2026-08-06
- 决策：精确锁定 `react-router-dom=7.18.2`，保留 client-only HashRouter 防护测试，维持
  `QUALITY_HOLD`，不以降级到含更多已知高危问题的旧版本伪造 audit 绿色。
- 关闭条件：无该 advisory 的合适上游版本 + audit + 前端回归 + 安全复核。
- 后续：被 ADR-MAT-018 取代；本条保留当时 registry 无安全版本且拒绝不安全降级的历史决策。

### ADR-MAT-011：保留 Code Golden V2 身份并单独提供完整历史 bundle

- 日期：2026-08-06
- 决策：不修改 `tests/fixtures/code_golden/v2`、V2 package hash、固定 commit/tree 或 entity ID；
  在独立 G0 fixture 目录保存 34.5 MB complete Git bundle、生成器、安全清单和 cleanroom harness。
- 理由：45 KiB 稀疏 object pack 会被普通 upload-pack 判为缺失 blob；新造提交又会构成同版本
  Golden 漂移。完整 bundle 是保留既有评测身份并实现 bytes-only 重放的最小诚实边界。
- 安全：导入后复扫 328 blobs；7 个高置信命中仅允许出现在 3 个已审 synthetic test 路径。

### ADR-MAT-012：模块权威绑定逻辑来源，不绑定安装根目录

- 日期：2026-08-06
- 决策：module binding 保留模块名、canonical 状态与逻辑 origin；普通源码投影为
  `<module-root>/<module path>`，扩展模块投影为 `<module-file>/<filename>`，`built-in/frozen`
  保留解释器语义；不记录 checkout、虚拟环境或系统库绝对根目录。
- 理由：V3 bytes-only cleanroom 的完整后端回归以 4 个 Experiment authority 误报失败，证明本机
  路径仍是隐藏输入。
- 版本：current Foundation authority 升级 v4，E-B0 current authority 升级 v7；released v1 与
  V2/V3 admission artifact 不回写。

### ADR-MAT-013：G1 raw foundation 使用并列表与复制迁移

- 日期：2026-08-06
- 决策：新增 `raw_blobs_v2/raw_objects_v2/source_events_v2/raw_evidence_bindings_v2/
  blocked_entities_v2` 与 migration ledger，不 ALTER/RENAME/DROP V1，不改历史 raw ID。
- 理由：V1 global unique/idempotency 可能已丢失跨项目写入意图，且 V1 Wiki/generation 正引用旧
  identity；原地修表既不能恢复丢失事实，也无法安全回滚。
- 迁移：M0–M8；只有 project/ACL/source/blob/generation 可唯一证明的 row safe-copy；其他进入
  quarantine/re-ingest，禁止 first/latest/current-owner 猜测。
- 权威：`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` 与 review 22；实施仍受 G0/owner hold。

### ADR-MAT-014：持久 code identity 与运行时 integrity 分层

- 日期：2026-08-06
- 决策：持久 authority 使用 normalized source AST/defaults/logical stdlib identity；`co_code`、stack
  size、filename、Python minor 只允许出现在同解释器即时 source-recompile 比较中，不得写入 artifact。
- 理由：V5 在 Python 3.12 出现 72 个收集错误；直接删除 bytecode 校验又会放过内存篡改。双层合同
  同时满足跨 minor 可移植与同 minor fail-closed。
- 历史边界：released Codex/Experiment/CB6 artifact 不回写，current authority 显式换代。

### ADR-MAT-015：Notebook AST identity 使用冻结的空字段规范

- 日期：2026-08-06
- 决策：external dependency ref 不直接依赖 `ast.dump` 默认格式；使用自有
  empty-field-free renderer，精确保持 Python 3.13 已发布 Golden identity。
- 理由：3.12 输出 `keywords=[]`、3.13 省略该字段，使同一 notebook source 的 publication/package
  identity 漂移；修常量会伪造第二套历史真值。

### ADR-MAT-016：G1 reference-only、canonical ID 与 visibility 使用单一 V2 合同

- 日期：2026-08-06
- 决策：reference-only 的 raw digest/length/blob 必须为 null；empty active payload 是独立合法状态；
  authority digest 使用冻结的 CJSON/H_HEX/H；event ID 由 idempotency digest 确定；object visibility
  只绑定 project + single object ACL，request/Wiki ACL 集使用不同 domain。
- source mapping：git/codex/document/mlflow/notebook 显式映射六域，workspace 只接受新 V2 event；
  dvc/manual/unknown 不猜测，进入 owner mapping 或 re-ingest。
- 实施边界：raw evidence binding 新建于 `sources/v2`，不扩展现有 change-review `bindings/` 表。
- 权威：`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` V2 与 `27` readiness audit。

### ADR-MAT-017：G1 Entry 必须是 G0 QUALIFIED 与外部可验证 attestation 的合取

- 日期：2026-08-06
- 决策：T1.1.1 前要求 E-01–10 全部 PASS；版本/远程 CI/生成物/供应链/D1–D5/数据授权/review/
  runtime boundary 缺一不可。供应链例外只能限时授权隔离开发，不能授权 shadow/canary/production。
- authority：仓库内 `approved=true`、reviewer 字符串、本地自签、截图/badge/URL 均不足；必须绑定
  protected review、受信签名或受控变更系统 approval，并可用离线 receipt/keyset 验证。
- 测试：BF-01–15 每条 PASS 必须含 before/after row/blob/audit/public projection side-effect invariant。
- 权威：`28_G1_ENTRY_DECISION_AND_REQUIREMENT_TRACE_PACKET.md` 与 review 28。

### ADR-MAT-018：移除 vulnerable router，冻结第一方最小路由合同

- 日期：2026-08-06
- 决策：不等待不存在的 registry patch，也不降级到含更多旧高危项的 7.11.0；删除
  `react-router-dom/react-router`，只实现项目已使用的 Hash/Memory、absolute match、Outlet、Link、
  navigation 和 search-param 合同。
- 安全：单 `/` 站内 route、scheme-relative/protocol 拒绝；remote admission 强制 high audit、
  CycloneDX SBOM、SHA256SUMS 和失败 artifact。
- 验证：frontend 32 files/184 PASS、typecheck/build PASS、audit 0、workflow contract 3 PASS；HTTP
  built-product smoke PASS。应用内浏览器 policy check 不可验证，LIVE-02 保持待补。
- 边界：关闭 R-017 本地 finding，不关闭 G0/Entry，不改变 V1/default/release/DB。
- 权威：`29_G0_FRONTEND_ROUTER_SUPPLY_CHAIN_CLOSURE.md` 与 review 29。

### ADR-MAT-019：Entry authority 采用带外 trust anchor 与 CI 原子聚合签名

- 日期：2026-08-06
- 决策：Entry Packet 的内容摘要不构成批准；使用 Ed25519 receipt、精确 role、有效期/撤销和调用方
  带外 keyset digest。packet 引用集合必须与 bundle 完全一致。
- CI subject：原子绑定 G0 admission、generated boundary、remote run/attempt/jobs 和 supply-chain
  disposition，禁止签 run id 后再改写其他 Gate 聚合。
- G0：必须调用 G0 V2 原生完整 verifier，再做 packet file/scope/source/blocker 交叉核对。
- 职责分离：architecture≠rollback、owner≠independent、independent≠runtime signer，以 key identity
  判定，不信任角色字符串。
- 验证：Python 3.12.13/3.13.12 各 27 Entry + 3 G0 workflow PASS；no-I/O spy PASS。
- 边界：关闭 verifier 工程缺口，不生成真实信任锚或审批，不解锁 G1 runtime/DB/release。
- 权威：`28_G1_ENTRY_DECISION_AND_REQUIREMENT_TRACE_PACKET.md` V2、`30` 执行记录与 review 30。

### ADR-MAT-020：Entry 签署交接必须先冻结事实，再由外部 authority 签发

- 日期：2026-08-06
- 决策：proposal 先剥离全部 receipt 并强制为 PENDING；只有 G0、remote CI、generated、supply、
  D1–D5、data、independent review 和 runtime boundary 的非签名事实全部合格，才能生成确定性
  signing requests。
- 时序：request time 不早于事实完成；receipt issued time 不早于 request；kind/role/revision/subject
  必须一一匹配，缺失、多余、重复和跨 revision 全部拒绝。
- 权限：handoff 工具没有 keygen/sign/fetch/network/database/release 能力；assembly 成功仍由原 Entry
  verifier 独立复核，不形成第二套宽松权威。
- 验证：Python 3.12.13/3.13.12 各 Entry+handoff 40、与 G0 workflow 合计 43、完整 backend 2,117；
  Make 四入口契约和 no-I/O 成功路径均通过。
- 边界：关闭人工拆 subject/回填 receipt 的工程风险，不生成真实 proposal、trust anchor、receipt、
  owner revision 或 remote run，不解锁 G1 runtime/DB/release。
- 权威：`31_G1_ENTRY_SIGNING_HANDOFF_AND_ASSEMBLY_RUNBOOK.md` 与 review 31。

### ADR-MAT-021：npm lifecycle script 必须按精确依赖身份 fail closed

- 日期：2026-08-06
- 决策：CVE audit/SBOM 不能替代安装期代码授权；固定 `npm@11.17.0`、
  `strict-allow-scripts=true`，且只批准 `esbuild@0.25.12` 与 `fsevents@2.3.3`。
- 身份：approval 必须同时绑定 package@version、官方 registry URL、lock integrity 和完整
  `hasInstallScript=true` 集合；name-only、semver range、未来版本继承或新增第三项一律失败。
- 独立性：npm pending 输出只作第二信号；离线 verifier 直接扫描 lock graph，避免 optional
  `fsevents` 漏列导致假阴性。
- CI：前端安装前固定并断言 npm；policy verifier → strict locked install → pending/audit/SBOM/
  SHA256SUMS → tests/typecheck/build；隐藏 `frontend/.npmrc` 纳入 G0 source snapshot。
- 验证：policy 1 positive + 6 negative；frontend 33/191；双 Python backend 各 2,118；audit 0；
  SBOM、workflow contract、V18 exact/cleanroom PASS。
- 边界：关闭本地 install-script authority 缺口，不产生 owner revision、远程 artifact、LIVE-02 或
  E-06 外部 receipt；保持 `QUALITY_HOLD / NO_RELEASE`。
- 权威：`32_G0_NPM_INSTALL_SCRIPT_ADMISSION.md` 与 review 32。

### ADR-MAT-022：Owner version admission 使用不可自批的逐路径 PENDING review packet

- 日期：2026-08-06
- 决策：admission manifest 继续作为 source authority；另生成逐 path、逐 scope、逐 exclusion 和逐外部
  action 的确定性 review packet，禁止用目录级 staging 或手填计数代替审阅分母。
- 绑定：scope subject 覆盖完整 path/role/size/content set；整体 subject 再绑定 admission logical path、
  exclusion denominator、release blocker 和 G0-OWNER-01–07。
- 权限：packet 永远为 `PENDING_OWNER_REVIEW`，owner/revision/remote/production 固定 false/null；工具无
  approve/sign/keygen/network/database/Git mutation/release 能力。
- 验证：每次 verify 重新调用 G0 native verifier，并从指定 manifest 重建 packet；外层 rehash、scope
  tamper、manifest copy substitution 和 source drift 全部拒绝。
- 证据：8 owner-review tests、CLI/Make、双 Python backend 各 2,126、V20 exact/cleanroom 与 canonical
  packet verify PASS。
- 边界：关闭交接可执行性缺口，不代表 owner 已审阅，不关闭 remote CI、LIVE-02、G0 或 G1 Entry。
- 权威：`33_G0_OWNER_REVIEW_PACKET_AND_V20_RUNBOOK.md` 与 review 33。

### ADR-MAT-023：当前目标使用全阶段原子图，不再用阶段口号作为完成单位

- 日期：2026-08-06
- 决策：Mission 继续为 G0–G9/L5，但执行只领取一个 `Tx.y.z`；每项必须固定输入、允许 mutation、
  verification、artifact、owner、exit 和 stop/rollback。
- 当时：V27/packet V8 作为 G0 owner 输入；T0.28.2–T0.30.1 是严格关键路径；G0 QUALIFIED 前不执行
  T1.1.1 或任何正式数据库写入。
- LIVE-02：本地服务和 HTML 入口通过；真实浏览器因 admin-enforced policy 不可验证而被拒绝，保持
  `QUALITY_HOLD`，不得用 HTTP 检查冒充页面交互。
- 边界：原子图提升执行精度，不产生 owner/remote/UI/production authority。
- 权威：`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`。

### ADR-MAT-024：Wiki page/candidate 完整性与 raw claim authority 永久分层

- 日期：2026-08-06
- 决策：V1 `raw_verify_count` 只解释为 candidate snapshot read；G2 V2 必须由 G1 shared reader receipt
  产生 `raw_observed/raw_verified`，page found 不能提前满足 obligation。
- 迁移：WikiSourceRefV2 与 V1 并列；`legacy_unbound` 只允许 navigation-only；禁止原地补 nullable raw
  字段或从 locator 猜 binding。
- Claim：token subset 只作最低 lexical guard；最终支持还需 citation→fact→receipt、claim type、typed
  numeric/relation/temporal entailment。
- 测试：六源 qualification 必须从 ingestion/materialization 产生，禁止手造
  `MultiSourceCandidateV2(raw_or_derived="raw_fact")` 绕过来源权威。
- 边界：本 ADR 关闭设计歧义，不产生 G1/G2 runtime、正式数据或发布许可。
- 权威：`35_G2_LIVE_WIKI_GROUNDED_ANSWER_EXECUTION_SPEC.md`。

### ADR-MAT-025：Planner、Scope、as-of、snapshot 与 fallback 只保留一个语义真值

- 日期：2026-08-06
- 决策：public/ordinary、legacy global、typed V2 和 Wiki 在执行前必须消费同一个
  `CanonicalQueryPlanV3` 与 `SnapshotManifestV3`；下游 adapter 不得重新选择来源或角色。
- Scope：`include` 与 `scope.source_types` 不一致时 fail closed；repository/thread/document/date 等
  filter 必须无损投影，不能因进入 V2 被丢弃；project 缺省不得无条件猜 `project-rag`。
- Temporal：`supports_as_of` 静态布尔值不构成能力权威；没有 reviewed selector/snapshot receipt 的来源
  必须 `UNSUPPORTED_AS_OF`，Wiki 不得用 current active manifest 回答历史问题。
- Snapshot/fallback：一个 query 只 pin 一个跨源 snapshot；只有 evidence 读取前的 preflight failure
  可以 exactly-once engine fallback，no-match/timeout/partial/generation failure 不得隐式重跑 V1。
- 边界：本 ADR 关闭设计歧义，不产生 G3 runtime、真实 historical capability 或发布许可。
- 权威：`36_G3_CANONICAL_QUERY_PLAN_SCOPE_ASOF_EXECUTION_SPEC.md`。

### ADR-MAT-026：真实六源用不可变 staging 与单一 generation-set pointer 激活

- 日期：2026-08-06
- 决策：五类非 Code `ProductionSourceRuntimeRegistryV2` 临时 store 只作为 disposable dry-run/shadow
  派生层，不能作为持久 qualification generation；Code 也必须进入统一六源 generation set。
- Governance：任何真实读取前先验证 `QualificationDataAuthorizationV1` 与不可变 corpus membership；
  data、materialization、quality、release 四种 authority 永久分离。
- Activation：各 source 先构建 immutable staging generation 并完成 membership/raw/ACL/version/coverage
  校验；跨多个 store 不伪装分布式事务，只对一个 `ActiveGenerationSetV1` expected-previous digest 做 CAS。
- Recovery：checkpoint/cursor 绑定 authorization、corpus、adapter、schema、source snapshot 和 deletion
  watermark；输入变化时禁止 resume，late worker 用 lease fencing 拒绝。
- Delete/rollback：raw tombstone 必须传播到 source index、Wiki、cache/export；旧代的 deletion watermark
  过时时禁止 pointer rollback，必须构建减去已删成员的 successor，永不复活 tombstone。
- 边界：本 ADR 只关闭数据/物化设计歧义；不产生真实授权、正式数据库 migration/backfill、active
  generation、G4 qualification 或发布许可。
- 权威：`37_G4_SIX_SOURCE_CORPUS_MATERIALIZATION_BACKFILL_EXECUTION_SPEC.md`。

## 8. 执行日志

| 日期 | 工作包 | 行动 | 结果 | 下一步 |
|---|---|---|---|---|
| 2026-08-06 | 审查 | 盘点代码、配置、正式库、质量/容量 artifact 和发布门 | 判定工程 Alpha；维持 HOLD | 建立全阶段执行权威 |
| 2026-08-06 | 文档冻结 | 新增成熟化总纲与持续执行台账 | G0–G9 目标、依赖、退出门已定义 | 完成旧文档勘误和索引更新 |
| 2026-08-06 | WP-G0-01 | 新增 Code dense 显式开关；兼容旧 C-B1 v1 config artifact；恢复评估链 | config/C-B1 94 PASS；C-B1/B2/B5 23 PASS | 完整回归与 CI |
| 2026-08-06 | WP-G0-01 | 新增四层 backend targets 和 Python 3.12/3.13 CI；运行完整回归 | 首轮 backend 2045 PASS/ruff PASS；frontend 178 PASS/typecheck PASS | 生成 G0 package；关闭版本范围 HOLD |
| 2026-08-06 | G0.1 | 生成 sanitized、content-addressed baseline inventory | verify PASS；正式 DB 未访问；HEAD/配置/scope/artifact 已绑定 | 版本范围 manifest |
| 2026-08-06 | G0.2 | 固化必须进入同一 revision 的核心/评估/交付范围和生成物排除项 | scope identified；clean checkout NOT PROVEN | 等待 owner version admission |
| 2026-08-06 | G0.9 | 构建 9-file portable package 并在 system temp 复制验证 | 6/6 commands；backend 2051、frontend 178；verify PASS；QUALITY_HOLD | G0 Gate Review |
| 2026-08-06 | G0.10 | 完成独立 G0 Gate Review | ENGINEERING_PASS / VERSION_ADMISSION_HOLD | clean revision + GitHub CI 后复核 |
| 2026-08-06 | WP-G1D-01 | 盘点 raw schema/store/service、六源 ingestion/runtime、Wiki source-ref/gateway/claim verifier | 确认 Wiki V1 假 raw read、selector 缺失、Workspace 无 raw event、Document parse authority 缺口 | 冻结字段/接口/迁移/测试矩阵 |
| 2026-08-06 | WP-G1D-01 | 新增 G1 详细设计与 G0–G9 全阶段目标卡 | G1 字段/接口级设计候选；所有阶段具备入口/工作包/证据/停止条件；实现仍 HOLD | owner/architecture review；G0 admission 后解锁 G1I.1 |
| 2026-08-06 | WP-G1D-01 | 对 shared raw 基础做对抗性复核 | 发现跨项目 raw/event identity、路径出站、claimed digest、global blocked entity 五类 P0 | 纳入 G1I.1，不在旧 identity 上实现 gateway |
| 2026-08-06 | WP-G1D-01 | 完成第一方 G1 设计复核 | DESIGN ENGINEERING_PASS / OWNER_REVIEW_HOLD / IMPLEMENTATION_HOLD | owner/architecture approval + G0 admission |
| 2026-08-06 | WP-G0-05A | 新增逐文件 version-admission builder/verifier/materializer | canonical file set；排除 node_modules/target/DB/cache；4 tests + ruff PASS | 生成并隔离验证 admission artifact |
| 2026-08-06 | WP-G0-05B | 对 V1 边界做对抗性复核并实现 V2 | 排除生成物/未登记图片；纳入 12 个 opaque eval DB anchors；tracked output fail closed | 隔离材料化与完整回归 |
| 2026-08-06 | WP-G0-05B | 修复两项跨 checkout 可移植性缺陷 | Codex digest 不再含 `co_filename`；evaluation root 固定投影；相关回归 PASS | 重建隔离候选 |
| 2026-08-06 | WP-G0-05B | 按 build-before-backend 顺序验证 synthetic clean candidate | release-ready PASS；Code baseline 55；frontend 179；backend 2060；ruff/typecheck/build PASS | 生成不可覆盖 V2 artifact；等待 owner revision |
| 2026-08-06 | WP-G0-05B | 收紧前端供应链边界 | PostCSS moderate 关闭；React Router RSC advisory 2 high，client-only mitigation 已测 | 保持 QUALITY_HOLD；跟踪上游安全版本 |
| 2026-08-06 | WP-G0-05C | 审计 V2 synthetic cleanroom 的 Git object 依赖 | 确认普通材料化不携带固定 commit；45 KiB sparse pack 无法普通 clone | 构建完整历史 bundle |
| 2026-08-06 | WP-G0-05C | 生成内容寻址 Code Golden history bundle 与安全 manifest | 34,534,047 bytes；431 objects；264 text/64 binary；7 synthetic findings | 实现 cleanroom 状态机 |
| 2026-08-06 | WP-G0-05C | 执行 bytes-only self-contained cleanroom | receipt/digest/security/ordinary clone/release-ready PASS；Code Golden+baseline 40 PASS | 完整产品回归 |
| 2026-08-06 | WP-G0-05C | 完整后端回归并保持 V2 package identity | Ruff PASS；2063 PASS；旧 V2 artifact canonical verify PASS | 生成不可覆盖 V3 admission artifact |
| 2026-08-06 | WP-G0-05C | 在最终 V3 materialized cleanroom 执行完整后端 | 2059 PASS / 4 FAIL；均为 Experiment module origin 绝对 `.venv` 路径误报 | 保留 V3；建立 WP-G0-05D |
| 2026-08-06 | WP-G0-05D | 将 module origin 改为跨 checkout 逻辑身份并显式换代 authority | 123 项 Experiment 专项 PASS；macOS/Linux、Windows、extension、frozen 回归齐全 | 运行正式工作树 full |
| 2026-08-06 | WP-G0-05D | 运行正式工作树完整后端 | Ruff PASS；2067 PASS；1 个既有 warning | 生成不可覆盖 V4 并重放 cleanroom |
| 2026-08-06 | WP-G1D-02 | 将 G1I-01 拆到表、ID、事务、迁移和证据粒度 | 冻结 5 张核心表、canonical identity、M0–M8、dry-run artifact、BF-01–15 | 对抗性复核 |
| 2026-08-06 | WP-G1D-02 | 复核 V1 信息丢失、dedupe/authority、idempotency、ambiguity、tombstone race 和 DB authorization | 7 findings 均在 spec 处理；SPEC ENGINEERING_PASS / OWNER HOLD | 纳入 V5 documentation admission |
| 2026-08-06 | WP-G0-05E | 在 V5 bytes-only cleanroom 安装 CPython 3.12 并执行 full collect | 72 errors；根因是 persisted authority 绑定 CPython minor bytecode | 保留 V5 负证据；设计 portable identity |
| 2026-08-06 | WP-G0-05E | 分离 portable persistent identity 与 same-interpreter runtime integrity；换代 current Codex/Experiment/Release/CB6 authority | 定向 3.12/3.13 authority suites 全绿；released artifacts 未回写 | 完整双版本回归 |
| 2026-08-06 | WP-G0-05E | 完整 3.12 回归发现并修复 Notebook `ast.dump` empty-field 漂移 | 3.13 历史 Golden 摘要不变；3.12 package/authority/recipe 精确对齐 | 最终 matrix |
| 2026-08-06 | WP-G0-05E | 最终源码双 Python full、Ruff 与前端全套验证 | 3.12/3.13 各 2072 PASS；Ruff PASS；frontend 179/typecheck/build PASS | 构建 V6 |
| 2026-08-06 | WP-G0-05E | 构建 1087-file V6 并执行 bytes-only cleanroom | receipt/history/security/ordinary clone/release-ready PASS | owner revision + remote CI |
| 2026-08-06 | WP-G0-05E | 冻结 V6 runtime evidence，将最终执行记录、review、总纲、台账、目标卡和索引纳入 1089-file V7 | source exact；bytes-only cleanroom receipt/history/security/ordinary clone/release-ready PASS | owner revision + remote CI |
| 2026-08-06 | WP-G0-05E | 不覆盖 V7，消除历史/operational 语义歧义并统一索引，生成同分母 V8 | 1089-file source exact；bytes-only cleanroom release-ready PASS | owner revision + remote CI |
| 2026-08-06 | WP-G0-05F | 审计远程 CI 覆盖，发现 path filter bypass 与缺失 admission/frontend/release-ready 链 | 定义缺口确认；Git remote/run 不存在 | 实现全链 workflow |
| 2026-08-06 | WP-G0-05F | 将所有 PR/main 触发、双 Python matrix、exact/cleanroom/frontend/release-ready 串成只读 workflow | YAML parse PASS；3 workflow contract tests PASS | 生成 handoff/review/V9 |
| 2026-08-06 | WP-G0-05F | 双 Python 完整重放后生成 1092-file V9，并执行 exact 与 bytes-only cleanroom | 3.12/3.13 各 2075；source exact；history/security/ordinary clone/release-ready PASS | owner revision + GitHub remote run |
| 2026-08-06 | WP-G0-05G | 在 V9 synthetic clean checkout locked install 后、frontend build 前运行 backend full | 2074 PASS / 1 FAIL；缺失被正确排除的 `web/index.html` | 保留 V9 负证据；修复 source authority |
| 2026-08-06 | WP-G0-05G | 将 SPA source authority 改为 `frontend/index.html`，matrix 增加 exact-before-backend | 定向 22 PASS；行为断言未删除；YAML/Ruff PASS | 生成 V10 cleanroom 并完整重放 |
| 2026-08-06 | WP-G0-05G | 在 1094-file V10 新 clean checkout 重放 backend→frontend→release-ready | 双 Python 2075；clean backend 2075；frontend 179/typecheck/build；终检 PASS | owner revision + GitHub remote run |
| 2026-08-06 | WP-G0-05H | V10 build 后终检发现 exclusions match=false 但 release-ready=true | 差异为缺失本地图片/sidecar/generated 与 cache 分母；语义不充分 | 分层 portable approval |
| 2026-08-06 | WP-G0-05H | 实现 exact/approved/unapproved additions 与 release 合取；新增对抗测试 | 缺失 exclusion/cache 可移植；ignored `.env.local` 在 Git clean 时仍拒绝 | 双 Python full |
| 2026-08-06 | WP-G0-05H | 首次并行双 full 暴露 CB2 workspace fingerprint 干扰，改为同一 checkout 串行 | 3.12 中断为 75 PASS/4 setup errors；不计为代码回归证据 | 串行重放并生成 V11 |
| 2026-08-06 | WP-G0-05H | 生成 1096-file V11 并执行 clean full/frontend/portable release-ready | 3.12/3.13 各 2077；clean backend 2077；frontend 179；approved=true/additions=0 | owner revision + GitHub remote run |
| 2026-08-06 | WP-G1D-03 | 逐文件复核 raw/event/binding/Wiki ref 与 G1 spec 映射 | 确认 Wiki 只回读 candidate snippet；现有 binding 非 raw binding；正式 DB 未访问 | 修正 residual contract gaps |
| 2026-08-06 | WP-G1D-03 | 将 binding spec 升级 V2，并完成 readiness audit/review | 关闭 RF-01–09；冻结 T1.1.1 C1–C10 与 T1.1.2–14 依赖；runtime 未启动 | 生成新版 admission evidence；等待 owner Gate |
| 2026-08-06 | WP-G0-05I | 将 G1 readiness 文档协调纳入 1098-file V12 | runtime/test/eval bytes 与 V11 相同；source exact、self-contained cleanroom/release-ready PASS | owner revision + GitHub remote run |
| 2026-08-06 | WP-G0-05I | 交叉检查发现少量历史 review 仍将 V11 写为当前候选；不覆盖 V12，生成 V13 | V12 保留；V13 收口 current authority 并重放 exact/cleanroom | owner revision + GitHub remote run |
| 2026-08-06 | WP-G1D-04 | 复核 G1 Entry 与 BF-01–15 的可执行证据 | 发现 G0 QUALIFIED/supply-chain 漏项、自签 approval、跨 revision CI 拼接和错误副作用四类 P0 | 冻结 Entry Packet |
| 2026-08-06 | WP-G1D-04 | 冻结 E-01–10、remote CI/attestation/data authorization 与逐 BF test/evidence trace | CONTRACT PASS；当前 remote/owner/data evidence 缺失；runtime/DB 未启动 | owner 产生 externally verifiable packet |
| 2026-08-06 | WP-G0-05J | 隔离复核 registry 与降级候选，删除 vulnerable router 并实现 bounded first-party route contract | targeted 9 PASS；full frontend 32 files/183；type/build PASS；audit 0 | 纳入 remote admission audit/SBOM |
| 2026-08-06 | WP-G0-05J | 为 remote Gate 增加 audit/SBOM/SHA256SUMS/always-upload 合同并执行页面证据分层 | workflow 3 PASS；HTTP 200；browser policy unavailable，LIVE-02 未冒充完成 | 生成 V14；owner remote/browser replay |
| 2026-08-06 | WP-G0-05J | 对抗复核发现 external target 拒绝缺负向用例；V14 不覆盖，增加 protocol/`//host` fail-closed 测试 | targeted router 5；fresh locked frontend 184/audit 0/type/build PASS | 生成 V15；owner remote/browser replay |
| 2026-08-06 | WP-G1D-05 | 实现 strict canonical、Ed25519 receipt/keyset、带外 trust digest 和 E-01–10 离线重算 | 首轮 14/14；无 network/DB/subprocess；真实 Entry 仍 HOLD | 扩展对抗矩阵 |
| 2026-08-06 | WP-G1D-05 | 复核发现 G0/generated/supply 聚合未全部受签名约束，且 G0 只做局部验证 | CI receipt 改为四块原子 subject；调用 G0 原生 verifier | 双 Python回归与文档复核 |
| 2026-08-06 | WP-G1D-05 | 完成 27-case 负向矩阵、CLI/Make 入口和 review | Python 3.12/3.13 各 Entry 27 + workflow 3 PASS；Ruff PASS | 纳入下一不可覆盖 G0 candidate；owner handoff |
| 2026-08-06 | WP-G1D-06 | 实现 PENDING draft、事实就绪 signing requests 和外部 receipt assembly | 15 项基础 request；无 keygen/sign/network/DB/release；assembly 后原 verifier 再验 | 扩展时序/信任/完整性负向矩阵 |
| 2026-08-06 | WP-G1D-06 | 补齐 request/receipt replay、bundle tamper、wrong trust、漏签/重复签和 Make 契约 | Python 3.12/3.13 各 Entry+handoff 40、与 workflow 合计 43 PASS；Ruff PASS | 双版本完整回归 |
| 2026-08-06 | WP-G1D-06 | 串行执行双 Python 完整 backend 并恢复 3.13 环境 | 3.12.13/3.13.12 各 2,117 PASS；各仅 1 条已知第三方 warning | 纳入不可覆盖 V17；恢复真实 owner/remote/receipt 目标 |
| 2026-08-06 | WP-G0-05K | fresh `npm ci` 复核发现 esbuild/fsevents 安装脚本未进入显式审批；audit 仍为 0 | 识别“CVE 绿色≠安装期代码已授权”；记录 npm optional fsevents pending 漏列限制 | 冻结 fail-closed policy |
| 2026-08-06 | WP-G0-05K | 实现 exact npm、strict allowScripts、lock graph/registry/integrity/pending verifier 与 6 项负向测试 | policy PASS；strict fresh install 无未审脚本；pending=0；audit=0；SBOM PASS | 纳入 workflow 与 source admission |
| 2026-08-06 | WP-G0-05K | 对抗复核发现隐藏 `frontend/.npmrc` 被敏感路径通则排除 | 仅为项目 policy 文件建立显式 frontend source 例外；其他 `.npmrc` 继续拒绝；G0 回归 PASS | 完整回归 |
| 2026-08-06 | WP-G0-05K | 执行 frontend、G0/Entry 定向与双 Python 完整回归 | frontend 33/191；workflow 3；Entry+handoff+workflow 43；3.12/3.13 backend 各 2,118；Ruff/type/build PASS | 生成不可覆盖 V18 |
| 2026-08-06 | WP-G0-05K | 生成 1,116-file V18 并执行 exact 与 bytes-only self-contained cleanroom | source exact；history/security/ordinary clone/release-ready PASS；本地 policy digest 固定 | owner revision + same-revision remote CI/LIVE-02 |
| 2026-08-06 | WP-G0-05K | 不覆盖 V18，收口 post-run 文档时态与 current pointer，生成同分母 V19 | 产品代码/测试/lock 与 V18 相同；source exact 与 cleanroom PASS | owner revision + same-revision remote CI/LIVE-02 |
| 2026-08-06 | WP-G0-05L | 审计 owner handoff，发现 manifest 可验证但缺独立 path/scope/exclusion/action 审阅队列 | 定义 always-PENDING packet；不启动 T1.1.1、不改 Git/DB/release | 实现 builder/verifier |
| 2026-08-06 | WP-G0-05L | 实现 canonical packet、domain subjects、7 external actions 与 build/verify CLI/Make | 8/8 tests；重算摘要后的伪批准、scope tamper、manifest substitute、source drift 均拒绝 | 完整回归与 V20 |
| 2026-08-06 | WP-G0-05L | 双 Python full 后生成 1,120-file V20，重放 exact/cleanroom 并生成 owner packet | 3.12/3.13 backend 各 2,126；packet 1,120 items/10 groups、canonical verify PASS | owner 执行 G0-OWNER-01–07 |
| 2026-08-06 | WP-G0-05M | 将 G0–G9 重设为 atomic objective graph，并记录 LIVE-02 安全策略阻断边界 | V21 为 1,121 files；packet V2 为 1,121 items；owner/remote/UI 仍 PENDING | T0.28.2 owner path/scope review |
| 2026-08-06 | WP-G2D-01 | 逐文件审计 compiler/store/gateway/navigator/answer/query，冻结 V2 ref/receipt/状态机/claim/Gate | V22 为 1,122 files；packet V3 为 1,122 items；G2 runtime 仍等待 G1 | owner G0 review；随后 G1→G2 |
| 2026-08-06 | WP-G3D-01 | 审计普通/legacy/V2/Wiki planner、Scope、as-of、snapshot、fallback 和 generation，冻结统一合同与 42-case Gate | V23 为 1,123 files；packet V4 为 1,123 items；G3 runtime 仍等待 G2 | owner G0 review；随后 G1→G2→G3 |
| 2026-08-06 | WP-G4D-01 | 审计六源正式/临时 ingest、store、generation、binding、cursor、tombstone、rollback，冻结授权语料、generation-set、coverage、恢复/删除与 54-case Gate | V24 为 1,124 files；packet V5 为 1,124 items；G4 runtime 仍等待 G3 与真实数据授权 | owner G0 review；随后 G1→G2→G3→G4 |
| 2026-08-29 | T0.28.1R | 复现 V24 exact fail，证明仅新增 10 份 documentation；生成 V25/V6 intermediate 并验证，写回结果后换代 V26/V7 | V25 1,135 files exact/canonical PASS；Ruff PASS；admission/owner-review pytest 15 PASS；V26 post-build receipt 固定当时结果 | 当时计划以 V26/V7 进入 T0.28.2；后由 G0.34 换代 |
| 2026-08-29 | T0.28.1R | 对 V26 做 Python 3.12/3.13 与严格前端本地完整回放；发现 nanoid high finding 后实施最小 lock-only 修复，并换代 V27/V8 | 后端各 2,126；前端 191/type/build；npm 11.17.0 policy PASS、pending 0、audit 0、SBOM/checksum PASS；V26 delta 仅 lock 1 path | 以 V27/V8 exact/canonical/cleanroom receipt 恢复 T0.28.2 |
| 2026-08-31 | T0.28.10D | 以 reviewed local revision 为恢复点，同步 admitted control documents 为 version-neutral resolver | 15-file frozen scope；missing-state fail closed；历史 V27 record 保持 exact；remote/G0/G1 状态不提升 | 完成静态/targeted/bundle/admission/packet 验证后请求新的 owner decision |

## 9. Historical target snapshot（superseded）

### 项目目标

严格执行 `34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md` 的 atomic graph；总纲的
G0→G9 仍是阶段 authority，但每轮只领取一个具有 frozen input、mutation、verification、evidence、
owner、exit/stop 的 `Tx.y.z`。在 G8 完成前维持默认 V1 和 QUALITY_HOLD。

### 当时的外部准入目标：WP-G0-05

owner 审阅版本范围，形成受控 revision，并从 clean checkout 完成 locked build、Python
3.12/3.13 remote CI 和 portable package verify；前端安装必须使用 npm 11.17.0、strict exact-version
script approval，并保存 policy/pending/audit/SBOM/SHA256SUMS。当前为
`IN_PROGRESS / OWNER_ACTION_REQUIRED`。
owner 必须先以 V27 的 PENDING review packet V8 逐 path/scope/exclusion 审阅并完成 G0-OWNER-01–07；
packet 自身不能作为 approval。
同时按 `28`/`31` 文档完成供应链 same-revision remote artifact、D1–D5、数据授权、PENDING
draft/request、external attestation、assembly 和原 verifier 复核，使 E-01–10 全部 PASS；当前无 Git
remote，不能由本地替代。

### 当时最近完成的设计/准入工具目标：WP-G1D-01–06 / WP-G2D-01 / WP-G3D-01 / WP-G4D-01

六源 raw authority 设计的精确完成条件为：

1. 六源 raw/derivation/selector 缺口有文件字段证据；
2. locator/selector/binding/read result 合同冻结；
3. shared reader 的 ACL/path/state/version/generation/digest 校验顺序和 failure code 冻结；
4. Code/Codex/Experiment/Notebook/Document/Workspace gateway 权威来源无空白；
5. V1 ref 只兼容导航，strict raw 模式安全拒绝；
6. shared + per-source + E2E + migration + rollback 测试矩阵完整；
7. 设计评审通过后才进入实现。
8. Binding foundation 的并列表、canonical identity、transaction、M0–M8、dry-run、BF-01–15 和
   rollback 已形成可执行规格与独立复核。
9. reference-only nullability、H/H_HEX/event ID、object/request visibility、source mapping、字段上限和
   reason registry 已统一，现有 relation binding 与 raw evidence binding 边界已冻结。
10. Entry E-01–10、remote CI attempt/revision/snapshot、external attestation、供应链和数据授权已冻结。
11. BF-01–15 已映射到具体测试文件、fixture、命令、artifact row 和失败副作用 digest。
12. Entry offline verifier 已实现 Ed25519/keyset/trust digest、G0 native verify、CI aggregate binding、
    职责分离和 27-case fail-closed matrix；合成夹具不属于真实 authority。
13. Entry handoff 已实现强制 PENDING draft、事实门禁、确定性 requests、时序防重放、外部 receipts
    一一匹配、assembly→原 verifier；工具无 keygen/sign/network/DB/release 权限。

当前状态：六源设计、binding execution spec V2、implementation readiness 与 Entry/requirement trace
的第一方对抗性复核已达
`ENGINEERING_PASS`，
owner/architecture/security/data review 保持
`QUALITY_HOLD`。G0 admission 与设计评审都通过
后，下一唯一实现目标为 `WP-G1I-01 / T1.1.1 C1–C10 Binding foundation canonical contract`；
不得同时改六源 gateway 和 Wiki。

G2 grounded-answer 执行规格的本地设计完成条件也已满足：

1. V1 candidate snapshot read 与 G2 raw authority 的代码边界可定位；
2. V2 source ref、read receipt、page/raw obligation 和 EvidencePackV3 合同冻结；
3. raw observed、raw verified、reviewed derived、navigation-only 的 claim authority 真值表冻结；
4. 六源 ingestion E2E、32-case matrix、portable Gate 和 rollback 合同完整；
5. G1 QUALIFIED 前不启动 G2 runtime。

当前为 `DESIGN_ENGINEERING_PASS / G1_ENTRY_HOLD / RUNTIME_NOT_STARTED`。

G3 canonical planning 执行规格的本地设计完成条件也已满足：

1. public/ordinary、legacy platform、typed V2、Wiki 四套语义真值可逐字段定位；
2. IntentV3、EvidenceRoleV3、ScopeResolutionV3、TemporalTargetV3 与 CanonicalQueryPlanV3 冻结；
3. capability receipt、跨源 SnapshotManifestV3 和三入口无损 adapter 合同冻结；
4. preflight-only exactly-once fallback、统一 generation policy、42-case matrix、portable Gate 与 rollback 完整；
5. G2 QUALIFIED 前不启动 G3 runtime。

当前为 `G3D DESIGN_ENGINEERING_PASS / G2_GATE_HOLD / RUNTIME_NOT_STARTED`。

G4 corpus/materialization/backfill 执行规格的本地设计完成条件也已满足：

1. 六源 legacy/formal、Code dual-write 与五类 disposable V2 store 边界可逐文件定位；
2. data/materialization/quality/release authority、authorization 与 corpus membership 合同冻结；
3. MaterializationRun、SourceGenerationManifest 和 ActiveGenerationSet CAS 状态机冻结；
4. expected membership 守恒、checkpoint/fencing、删除传播和 no-resurrection rollback 完整；
5. 22 项原子实施顺序、GM-01–54、portable Gate 与独立评审问题已冻结；
6. G3 QUALIFIED 与真实数据 authorization receipts 到达前不读取正式源、不启动 G4 runtime。

当前为 `G4D DESIGN_ENGINEERING_PASS / G3_GATE_HOLD / DATA_AUTHORIZATION_MISSING /
MATERIALIZATION_NOT_STARTED`。

### 当时已完成的内部准入目标：WP-G0-05B / C / D / E / F / G / H / I / J / K / L

`maturity_g0_admission_v2.py` 已完成 source/evaluation/generated/sensitive 四类边界、隔离
materialize、build-before-backend 完整重放和 release-ready 复核；V3 进一步加入 complete history
bundle、导入后安全复扫和普通 clone 证明；V4 关闭完整 cleanroom 暴露的 Experiment module-origin
绝对路径依赖；V5 纳入 G1I-01 执行文档但暴露 Python 3.12 authority 缺陷；V6 关闭 CPython minor
code identity 与 Notebook AST 漂移；V7/V8 完成文档收口；V9 补齐 owner/CI handoff 并保留
clean full 的 generated HTML 失败；V10 修复 source authority；V11 分层 portable exclusion 与
release-ready；V12 保留 readiness 文档索引中间状态；V13 收口 current authority；V14 保留 G1 Entry
与 supply-chain 中间证据；V15 加入 external target fail-closed；V16 纳入 Entry offline verifier、
27-case adversarial matrix 和双 Python 2,104 full；V17 纳入 signing handoff/assembly、40-case
Entry/handoff、43-case 相邻定向和双 Python 2,117 full；V18 纳入 npm install-script
fail-closed policy、隐藏 `.npmrc` source authority、frontend 191 和双 Python 2,118 full；V19 只收口
post-run 文档时态和当前指针；V20 纳入逐 path/scope/exclusion/action 的 always-PENDING owner-review
packet、8-case 对抗矩阵和双 Python 2,126 full；V21 纳入全阶段 atomic objective graph；V22 纳入
G2 raw-verified grounded-answer execution spec；V23 纳入 G3 canonical planning execution spec；V24
纳入 G4 corpus/materialization/backfill execution spec 与 packet V5；V25/V26 再纳入 G5–G8 specs、
执行控制与 refresh record；V27 纳入最小 nanoid lock 修复、本地完整回放记录和当时的控制更新。
历史 V27 记录 1,136 个
path/role/scope/size/SHA-256 与 capture-time Git state；source identity 不含
Git state，因此相同内容进入受审 revision 后仍可验证。

V5–V10、V24/V5、V25/V6、V26/V7 历史 evidence 均固定且不覆盖；当前 admission 固定位置：
`artifacts/rag-maturity/g0/admission-20260829-v27/admission.json`，packet 为
`artifacts/rag-maturity/g0/owner-review-20260829-v8/review-packet.json`；V5、V9、V10、V14 分别保留 Python
3.12、generated HTML、exclusion/release 语义负证据。
当前不越权的设计工作已完成；下一步只能由 owner 审阅并形成正式 revision、取消跟踪
`web/index.html`，再由
fresh clean checkout 和 Python 3.12/3.13 远程 CI 关闭 WP-G0-05。
具体 digest 由 artifact 自身 canonical 字段作为权威，避免文档自引用导致 snapshot 漂移。

## 10. Current-state resolution and queue

本节取代 §9 的 operational 作用；§9 只保留当时的候选沿革与执行语境。当前 reviewed/pending/active/next
对象必须从稳定 durable record 解析，核对 Git refs、immutable receipts 与 artifact hashes；本文不复制易变
版本号作为恢复入口。记录缺失、无效或矛盾时使用
`CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`。

解析后的任务仍受以下队列约束：

1. 先关闭当前 G0 文档/版本控制任务的 exact allowlist、测试、portable replay 与 owner review；
2. remote 可用后取得 protected same-revision 3.12/3.13、exact admission、cleanroom、frontend supply-chain
   和 release-ready evidence；
3. external UI reviewer、owner/architecture/security/data receipts 与 independent G0 decision 必须真实存在；
4. G0 只有在全部 hard exits 后才能 `QUALIFIED`；
5. G1 Entry E-01–E-10 只有原生 verifier `PASS` 才能解锁 T1.1.1 C1–C10；
6. 之后严格按 G1→G2→G3→G4→G5→G6→G7→G8→G9 推进。

本地 reviewed ref/bundle 不能替代 protected remote；local tests 不能替代 remote run；PENDING packet 不能替代
owner decision；任何文档状态也不能替代 Gate/release authority。F-01–F-06 的实现顺序和验收标准保持不变，
不会在 G0/G1 Gate 前用“明显问题”名义越权启动 runtime mutation。
