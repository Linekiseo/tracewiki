# RAG + Wiki 全阶段成熟化执行总纲

版本：2026-08-06  
适用仓库：`/Users/example/project/rag`  
文档状态：`EXECUTION_AUTHORITY / QUALITY_HOLD / DEFAULT_V1 / NO_RELEASE`  
上游设计权威：`08_PRODUCTION_RAG_CORE_COMPLETION_PLAN.md`  
持续执行台账：`11_RAG_WIKI_MATURITY_EXECUTION_LEDGER.md`
全阶段目标卡：`14_RAG_WIKI_STAGE_OBJECTIVE_BREAKDOWN.md`
G1 原始证据设计：`13_G1_RAW_EVIDENCE_AUTHORITY_DESIGN.md`
G1 binding 执行规格：`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md`
G0 V6–V11 跨 Python 执行记录：`16_G0_PORTABLE_PYTHON_IDENTITY_AND_V6_EXECUTION_RECORD.md`
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
G5 real source quality 执行规格：`38_G5_SIX_SOURCE_REAL_QUALITY_CALIBRATION_EXECUTION_SPEC.md`
G6 real joint/Wiki answer quality 执行规格：`39_G6_REAL_MULTISOURCE_WIKI_ANSWER_QUALITY_EXECUTION_SPEC.md`
G7 capacity/reliability/security/observability 执行规格：`40_G7_CAPACITY_RELIABILITY_SECURITY_OBSERVABILITY_EXECUTION_SPEC.md`
G8 replay/shadow/canary/rollback 执行规格：`41_G8_REPLAY_SHADOW_CANARY_ROLLBACK_EXECUTION_SPEC.md`
历史 candidate refresh 记录：`42_G0_V25_V26_CANDIDATE_REFRESH_EXECUTION_RECORD.md`
历史本地回放与 V27 refresh 记录：`43_G0_V27_SUPPLY_CHAIN_AND_LOCAL_REPLAY_EXECUTION_RECORD.md`
当前执行控制：`execution-control/README.md`

## 0. 文档权威与当前真值

本文不替代既有单源、多源和 Wiki 架构设计，而是把设计、2026-08-06 独立成熟度审查、
真实数据库盘点和当前测试状态收敛为唯一的后续执行顺序。发生冲突时：

1. 安全、ACL、版本、原始证据和发布 fail-closed 合同优先；
2. 本文的阶段依赖与退出门优先于历史 `ENGINEERING COMPLETE` 描述；
3. 实际测试、Evaluation Run、不可变 artifact 和生产观测优先于文档声明；
4. `production_authorized=false` 不得由 fixture、调用方参数或手工修改状态跨越。

稳定的阶段状态是：

```text
架构：合理，主要合同已存在
仓库工程：本地可重放、owner-reviewed revision 与 exact-source CI transport 已有不可变证据；受保护 remote、same-revision remote CI、external UI reviewer、独立 Gate 与 release authority 仍未证明
真实六源：Code/Codex/Document 部分有数据，Experiment/Notebook 为空，Workspace 稀疏
Wiki：编译/存储/搜索/导航工程链存在，但 raw read-back 和 live grounded answer 未闭环
运行：V1 默认；V2/Wiki 显式 opt-in；缺发布授权时回退 V1
发布：QUALITY_HOLD / DEFAULT_V1 / NO_RELEASE
```

易变的 reviewed/pending/active/next 真值不在本文硬编码。执行前必须读取并核对
`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md`；记录缺失、无效或与 Git/receipts 矛盾时，状态为
`CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`。历史 execution/admission 文本不得用作 fallback。

## 1. 2026-08-06 审查勘误

历史文档中以下表述必须按本节收窄：

| 历史表述 | 审查后的准确表述 |
|---|---|
| “exact raw read/verify 已完成” | 当前读取 WikiStore 中的候选 envelope，不是六源原始对象；只能证明编译快照内部一致性 |
| “Live Wiki 可 grounded answer” | Live Organizer 全部输出 `derived_fact/deterministic_derived`，读取后为 `REPORTED`，与 claim authority 门不兼容 |
| “六源联合已完成” | 六源代码合同存在，但真实库中 Experiment/Notebook 为空，不能证明真实六源融合质量 |
| “全仓 safe suite 通过” | 初始为 1 failed/12 errors；V9 clean full 暴露 1/2074；V10 修复 generated HTML；V11 双 Python 各 2,077 并关闭 exclusion/release 语义矛盾；远程 run 未证明 |
| “仓库工程完成” | 当前 `rag/`、`rag/wiki/` 和相关测试未进入当前 Git HEAD，无法从 HEAD 重建审查状态 |

这些勘误不否定既有设计成果，但禁止把工程 fixture 的闭环继续表述为生产事实闭环。

## 2. 总目标、边界与成熟度等级

### 2.1 总目标

把系统推进到 `L5 PRODUCTION QUALIFIED`：六个来源均有真实、版本化、可回读的权威证据；
Wiki 只承担派生组织和导航；查询规划、as-of、ACL、generation 和 Evidence Pack 语义统一；
质量、容量、安全、shadow、canary 和回滚均有不可变证据，发布控制面才能授权默认 V2。

### 2.2 不在目标中的捷径

- 不通过降低断言、删除失败用例或扩大排除列表制造绿色测试；
- 不把候选 snippet、Wiki 页面或 LLM 摘要重新命名为“原始证据”；
- 不用 synthetic fixture 替代真实 source-owner 数据与生产流量；
- 不在单源质量未过门时用跨源融合掩盖失败；
- 不直接改 reviewed release registry 或 `production_authorized`；
- 不在同一请求混用不同 generation、ACL visibility 或不兼容的 as-of 快照；
- 不在无独立验证集时宣称校准、reranker 或生成质量提升。

### 2.3 成熟度等级

| 等级 | 定义 | 允许的运行方式 |
|---|---|---|
| L0 Contract | 数据结构和接口可导入 | 仅开发 |
| L1 Isolated | 隔离 fixture、确定性测试通过 | 测试环境 |
| L2 Integrated | 真实运行路径、持久化、故障和回滚闭环 | 本地/集成环境显式 opt-in |
| L3 Reproducible | clean checkout 可重建，完整 CI 通过，证据包可验证 | 内部离线 replay |
| L4 Quality Qualified | 真实数据 Golden、独立校准、质量/安全/容量门通过 | shadow / internal canary |
| L5 Production Qualified | canary、回滚演练、监控和 reviewed release authority 完整 | 审批后默认 V2 |

任何模块的最终等级取其代码、数据、测试、运维和发布证据中的最低等级。

## 3. 全阶段依赖图

```text
G0 可复现与真值基线
  ↓
G1 六源原始证据权威
  ↓
G2 Live Wiki grounded-answer 闭环
  ↓
G3 统一 Planner、Scope 与 as-of
  ↓
G4 真实六源 materialization/backfill
  ↓
G5 单源质量、独立校准与来源发布门
  ↓
G6 多源融合、Wiki 导航与回答质量资格
  ↓
G7 容量、可靠性、安全与可观测性资格
  ↓
G8 Offline replay → Shadow → Canary → Rollback
  ↓
G9 Reviewed authority、默认 V2 与持续运营
```

允许为后续阶段编写设计或测试夹具，但不允许在前置 Gate 未通过时把后续阶段标记为完成。

## 4. G0：可复现与真值基线

目标：从当前不可复现工作树建立 clean checkout 可验证的工程基线。

### G0 工作项

| ID | 工作项 | 交付物 | 验收证据 |
|---|---|---|---|
| G0.1 | 冻结当前组件、配置、DB schema 和 artifact inventory | sanitized baseline snapshot | 内容哈希、版本、生成时间、无 secret/path 泄漏 |
| G0.2 | 处理未跟踪/修改文件的版本边界 | reviewed scope manifest | `rag/`、Wiki、测试、CI、docs 均可从同一 revision 重建 |
| G0.3 | 修复 C-B1/C-B2/C-B5 publication 合同漂移 | 显式 Code dense-index 开关及迁移说明 | exact/sparse 评估不再隐式构建 dense；失败断言不放宽 |
| G0.4 | 建立后端 CI | Python install、ruff、专项和全量 pytest workflow | clean checkout CI 全绿 |
| G0.5 | 建立一条统一验证命令和测试分层 | `smoke/integration/evaluation/full` 说明 | 本地与 CI 命令一致，排除项有 owner、原因、到期条件 |
| G0.6 | 生成新的基线报告 | G0 gate review | 测试数量、失败为 0、耗时、环境、revision 全部记录 |
| G0.16 | 关闭支持 Python minor 的持久身份漂移 | portable code/AST identity + V6 review | 3.12/3.13 full parity、memory tamper fail closed、历史 artifact 不回写 |
| G0.17 | 补齐 owner admission 全链 CI | workflow + contract tests + handoff/review | 所有 PR/main 触发；双 Python 后串行 exact/cleanroom/frontend/release-ready；remote run 不误报 |
| G0.18 | 在无预生成 Web 产物的 clean checkout 重放 full | source-entry authority fix + V10/review | backend-before-build 2075；frontend 179；build 后 release-ready |
| G0.19 | 拆分 exact exclusion 与 portable approval | verifier + adversarial tests + V11/review | ignored secret fail closed；cache/generated 可移植；release=`clean∧approved∧blocker=0` |
| G0.20 | 关闭前端 router 供应链 high finding | first-party bounded router + route contract + audit/SBOM CI | dependency removed；frontend 184；audit 0；external target fail closed；remote artifact gate |
| G0.21 | 关闭 npm 安装期脚本 authority 缺口 | exact npm + strict allowScripts + lock identity verifier + V18 | frontend 191；pending=0；audit/SBOM；双 Python 2118；remote artifact gate |
| G0.22 | 将 owner 准入变为逐路径可验证审阅队列 | PENDING review packet + build/verify CLI + V20 | file/scope/exclusion/action 守恒；伪批准/替换/漂移拒绝；owner authority 仍外部 |
| G0.23 | 将粗粒度路线重设为全阶段原子目标图 | G0–G9 atomic graph + current queue + V21/packet V2 | 每项有依赖、结果、证据、owner、exit/stop；不越过 G0 |
| G0.24 | 冻结 G2 raw-verified grounded-answer 执行规格 | code map + V2 refs/receipts/obligations + claim policy + V22 | candidate/snippet 不再冒充 raw；G1 前不实现 runtime |
| G0.25 | 冻结 G3 canonical planning 执行规格 | four-planner code map + plan/scope/temporal/snapshot/fallback contracts + V23 | G2 前不实现 runtime；as-of 不再只靠布尔声明 |
| G0.26 | 冻结 G4 六源 corpus/materialization/backfill 执行规格 | six-source code map + authorization/corpus/generation-set/coverage/delete/rollback contracts + V24 | G3 前不实现 runtime；临时 V2 store 不冒充正式 generation |
| G0.27 | 冻结 G5 六源真实质量与校准执行规格 | real universe/split/annotation/calibration/source gates | G4 前只读设计；synthetic 不冒充真实 quality |
| G0.28 | 冻结 G6 real joint/Wiki answer quality 执行规格 | joint Gold/claim/human review/ablation gates | G5 前只读设计；fixture 60/120 不升级为资格 |
| G0.29 | 冻结 G7 capacity/reliability/security/observability 执行规格 | S/M/L、concurrency/fault/recovery/trace gates | G6 前不运行 production-like load |
| G0.30 | 冻结 G8 replay/shadow/canary/rollback 执行规格 | R0–R6、exposure/guard/rollback contracts | G7/authority 前不改流量 |
| G0.31 | 收口 V24 admitted documentation drift | V25 intermediate + V26 documentation candidate + PENDING packet V7 | 历史不覆盖；owner/release authority 不自授 |
| G0.32 | V26 本地完整回放与供应链修复后换代 | 双 Python 各 2,126；frontend 191/audit 0；historical V27 + PENDING packet V8 | lock-only 最小改动；owner/release authority 不自授 |

### G0 退出门

- `ruff check src tests` 通过；
- 前端 typecheck 和测试通过；
- 完整后端测试无失败、无 setup error；
- 当前 RAG/Wiki 实现与测试均受版本管理；
- CI 不读取或修改正式数据库；
- 所有临时 artifact 写入隔离目录；
- 形成可从 clean checkout 重放的 G0 evidence package。

## 5. G1：六源原始证据权威

目标：把“raw evidence”从候选快照提升为可验证的来源原始对象，并保持 Wiki 为派生层。

### G1.1 统一原始证据引用合同

新增或升级 `RawEvidenceLocatorV2`，至少绑定：

- `project_id`、`source_domain`、`source_instance`；
- `entity_id`、`raw_object_id`、`locator`；
- `stable_version`、`source_generation`、`valid_time/observed_time`；
- `acl_ref/visibility_partition`；
- `media_type`、`schema_version`、`adapter_version`；
- 原始 bytes 或 canonical record 的 `content_sha256`；
- 派生对象到原始对象的 derivation identity。

Wiki source-ref 的 digest 必须来自上述权威对象，不得来自整个候选 envelope 的 digest。

### G1.2 六源 gateway

| 来源 | 必须回读的权威对象 | 必须验证的特有语义 |
|---|---|---|
| Code | raw file/blob + exact line/symbol range | repository、commit/blob、path、range |
| Codex | raw thread/turn/item | turn/item 顺序、redaction、tombstone |
| Experiment | run/config/metric/dataset record | 数值、单位、step、aggregation、dataset version |
| Notebook | notebook revision/cell/output/artifact | revision、execution order、stale、output producer |
| Document | paragraph/table cell/figure/citation | document version、page/section、table semantic path |
| Workspace | topic/iteration/work-item event | state history、valid time、owner、acceptance |

### G1.3 G1 退出门

- 每个 gateway 都从正式 source store/raw object store 回读，不依赖 Wiki page/snippet；
- 100% source-ref digest 与回读内容一致；
- wrong project/version/generation/ACL/tombstone 全部 fail closed；
- raw object 缺失时只能 partial/refusal，禁止退回页面正文冒充事实；
- 六源各有成功、陈旧、删除、ACL 拒绝、digest 不一致和迁移兼容测试；
- 旧 source-ref 有显式版本兼容或安全失效策略。

## 6. G2：Live Wiki grounded-answer 闭环

目标：真实 Live Organizer 输出能够经过 Compiler、Store、Navigator、raw gateway 和
claim verifier 形成可支持或正确拒答的最终回答。

### G2 工作项

| ID | 工作项 | 关键要求 |
|---|---|---|
| G2.1 | 修正 raw/derived/status/review 状态机 | raw fact、deterministic derived、reviewed derived、reported 不再混用 |
| G2.2 | Live Organizer 保留来源权威 | 不把所有 inventory item 无条件降为同一种 `derived_fact` |
| G2.3 | Compiler 绑定真实 raw locator | page fact 和 source-ref 可回到 G1 gateway |
| G2.4 | Navigator 分离“找到页面”和“事实已验证” | obligation 只有 raw verify 成功后才能满足 claim authority |
| G2.5 | Claim verifier 明确派生事实政策 | deterministic derivation 的允许条件、review authority 和反证处理版本化 |
| G2.6 | 端到端测试 | 六源各 1 条 live positive、1 条 refusal；跨源 positive/conflict/as-of/ACL |

### G2 退出门

- `live source → organize → compile → stage → publish → search/read/follow → raw read-back → Evidence Pack → answer/refusal` 全链通过；
- answer 中每个 claim 都有 raw citation membership；
- Wiki 页面 citation 只能作为 navigation trace，不能单独满足 claim；
- unsupported claim、ACL leakage、wrong-version citation 均为 0；
- live E2E 不使用手工构造的 `raw_fact` 绕过 Organizer。

## 7. G3：统一 Planner、Scope 与 as-of

目标：普通 Query、Global V2、Wiki Query 使用同一个规范化计划合同和一致的能力门控。

当前设计 authority candidate 为 `36_G3_CANONICAL_QUERY_PLAN_SCOPE_ASOF_EXECUTION_SPEC.md`；
状态是 `G3D DESIGN_ENGINEERING_PASS / G2_GATE_HOLD / RUNTIME_NOT_STARTED`。

### G3 工作项

| ID | 工作项 | 关键要求 |
|---|---|---|
| G3.1 | 冻结 CanonicalQueryPlanV3 | intent、sources、tasks、roles、budgets、scope、as-of、fallback、digest |
| G3.2 | Legacy/Wiki 仅作为 adapter | 禁止继续拥有独立、互相漂移的路由真值 |
| G3.3 | 合并 `include` 与 `scope.source_types` | 冲突时 fail closed，trace 显式解释 |
| G3.4 | required roles 真实下传 | QueryUnderstanding 结果不得在 Wiki request 中被清空 |
| G3.5 | capability gate | task/channel/as-of/numeric/relation 不支持时不路由或标记 partial |
| G3.6 | 一致快照 | 查询记录每源 generation/watermark，禁止中途混代 |
| G3.7 | temporal semantics | valid time、observed time、commit/revision 和“current”含义统一 |

### G3 退出门

- Planner Capability Violation = 0；
- 所有入口对相同请求生成同 digest 的 canonical plan；
- 不支持 as-of 的来源不会被静默纳入；
- source timeout 不会被报告为“无证据”；
- plan、source snapshots、Evidence Pack 和最终 citation 可完整重放；
- V1 fallback 恰好执行一次，保留明确 blocker。

## 8. G4：真实六源 materialization/backfill

目标：建立足以验证来源语义和跨源关系的、经 owner 授权的真实 qualification corpus。

当前设计权威为 `37_G4_SIX_SOURCE_CORPUS_MATERIALIZATION_BACKFILL_EXECUTION_SPEC.md`。该规格冻结
24 项代码差距、四类 authority 分离、语料成员与 root provenance、22 项原子实施序列、跨 store
generation-set CAS、重入 fencing、删除传播、no-resurrection rollback 和 GM-01–54；G3 QUALIFIED 与
真实数据授权 receipt 到达前，禁止读取正式源或执行 materialization。

### G4.1 首轮 qualification corpus 最低覆盖

| 来源 | 最低真实覆盖 | 必备困难切片 |
|---|---:|---|
| Code | ≥2 repositories，≥2 versions/repository | rename、same-name、broken test、wrong commit |
| Codex | ≥20 threads | tool failure、validation、redaction、goal change、tombstone |
| Experiment | ≥20 runs，≥5 run groups | multi-seed、failed run、unit mismatch、dataset drift |
| Notebook | ≥10 notebooks，≥2 revisions/notebook | stale、out-of-order、error、parameterized、artifact |
| Document | ≥20 documents | table、figure、citation、claim conflict、scanned/parsed failure |
| Workspace | ≥3 topics，≥2 iterations/topic | blocker、dependency、cancelled/completed、as-of history |

以上只是首轮 qualification floor，不是 production 规模证明。数据 owner、授权、保留周期和
可用于评测的范围必须写入 dataset manifest。

### G4.2 退出门

- 六源各至少一个 active、非空、版本化 generation；
- 每源有 freshness、entity/unit/raw-object/edge/validation 计数；
- source adapter → raw object → derived unit → Wiki ref 的覆盖率可计算；
- backfill 可重入、可恢复、可回滚，不覆盖旧 generation；
- schema migration 在生产镜像上 dry-run 通过；
- 缺失、部分、失败来源在 API/trace 中保持真实状态。

## 9. G5：单源质量、独立校准与来源发布门

目标：六个 source retriever 分别达标后，才允许进入联合质量资格。

### G5.1 固定数据集规模

- Code 50；Codex 45；Experiment 45；Notebook 40；Document 50；Workspace 40；
- 每源包含 hard negative、错误版本、不可回答、ACL/security、故障注入；
- train/calibration/test 按 root provenance 分组切分，禁止同根泄漏；
- calibration 只能在 calibration split 拟合，在独立 test split 报 ECE/Brier；
- threshold 在看到 test 结果前冻结，变更时保留旧基线和理由。

### G5.2 来源 Gate

- 每源满足其 source design 中的 Recall/MRR/nDCG、版本、数值或时序门；
- calibration profile 覆盖 `source × task × entity_type` 的已评审切片；
- 不能用 in-sample ECE/Brier 作为资格证据；
- 每源独立报告延迟、索引成本、zero-result、partial、错误分类和 Top failures；
- 任一来源未过门时，联合层只可把它标为 unavailable/partial，不得补造分数。

## 10. G6：多源融合、Wiki 导航与回答质量资格

目标：以真实 60-case 多源集和真实 Wiki 导航集验证规划、融合、冲突、证据与回答。

### G6.1 固定退出指标

| 指标 | 门槛 |
|---|---:|
| Source Routing Macro-F1 | ≥0.90 |
| Required Role Coverage | ≥0.90 |
| Cross-source Entity Recall@20 | ≥0.85 |
| Evidence Path Recall | ≥0.80 |
| Counter-evidence Recall | ≥0.90 |
| Version Accuracy | ≥0.97 |
| Wrong-version Rate | ≤0.02 |
| Conflict Recall | ≥0.90 |
| Citation Precision / Completeness | 各 ≥0.95 |
| Unanswerable Acceptable Rate | ≥0.90 |
| Unauthorized Evidence Leakage | 0 |
| Planner Capability Violation | 0 |
| Source timeout 误报为无证据 | 0 |
| P95 联合检索，无生成 | ≤3 s（记录规模、硬件、并发、cache） |

### G6.2 Wiki 额外门

- raw citation read-back success = 100%；
- premature-stop、missing-obligation、false-supported 各自独立报告；
- 页面结构、搜索质量、导航质量、raw authority 和 answer quality 分开评分；
- Builder before/after 使用同一初始 generation，guard query 不回归；
- synthetic 120-case 仅保留为工程回归，正式资格使用独立真实集；
- 至少一次盲审/人工抽样，Judge 版本化且可访问被评判证据。

## 11. G7：容量、可靠性、安全与可观测性资格

目标：证明系统在目标数据量和并发下不会混代、泄漏、失控或不可恢复。

### G7 工作项

| 类别 | 必测内容 |
|---|---|
| Capacity | S/M/L 三档 page/ref/vector/edge 数量，冷/热 cache，索引/重建/发布/回滚 |
| Concurrency | reader+publish、长导航、超时、连接耗尽、锁竞争、进程重启 |
| Fault | 单源 timeout/exception、损坏 artifact、stale pointer、partial publish、provider failure |
| Security | ACL pre-filter、cache isolation、secret scan、prompt injection、path/URL/tool injection |
| Observability | query/plan/source/fusion/wiki/raw/answer spans，版本、候选、过滤、延迟、成本 |
| Recovery | generation rollback、component rollback、DB backup/restore、re-index、tombstone propagation |

### G7 退出门

- 单源 exact P95 ≤600 ms；单源 hybrid+rerank P95 ≤1.5 s；联合无生成 P95 ≤3 s；
- corrective 两轮耗时 ≤单轮预算 2.2 倍；
- unauthorized/secret leakage = 0；混 generation = 0；
- 所有故障返回 typed partial/refusal/fallback，不产生伪成功；
- dashboard 能按 source/intent/language/complexity/ACL/version slice 定位回归；
- M/L 数据点必须真实测量，禁止由 S fixture 外推。

## 12. G8：Offline replay、Shadow、Canary 与 Rollback

目标：在不改变默认答案的前提下积累真实运行证据，再逐步放量。

### G8 阶段

1. Offline replay：固定真实 query log，比较 V1/V2/Wiki Evidence Pack；
2. Shadow plan：只运行 planner，核对 source/role/capability；
3. Shadow retrieval：比较 Top-K、角色、路径、反证、延迟和成本；
4. Shadow answer：生成但不返回，验证 claim/citation/refusal；
5. Internal canary：内部项目、低风险 intent、小比例；
6. Expanded canary：按项目和 intent 逐步放量；
7. Rollback drill：分别回滚 planner/retriever/embedding/reranker/fusion/context/prompt；
8. 稳定观察窗口结束后提交 release authority review。

### G8 退出门

- 每一阶段都有预注册时长、流量、样本量、成功门和自动停止条件；
- V2 新增/丢失证据、false refusal、unsupported claim、P95/cost 均有对照；
- canary 期间 ACL/secret/wrong-version 一次即自动停止；
- 请求级 V1 fallback 和 generation rollback 均完成演练；
- 没有 blocker 被人工从 trace 删除或改名为成功。

## 13. G9：Reviewed authority、默认 V2 与持续运营

目标：只有不可变证据包经过评审后，才由 canonical control plane 授权生产切换。

### G9 退出门

- G0–G8 Gate 全部 `PASS`，没有过期证据；
- reviewed source registry 覆盖六源，reviewed gate registry 覆盖质量/安全/容量/canary/rollback；
- release package 可 portable verify，绑定 revision、dataset、config、model、index、prompt；
- 默认切换按项目/intent 可逆，不进行大爆炸迁移；
- V1 fallback 至少保留一个稳定观察周期；
- 上线后持续监测校准漂移、索引 freshness、citation failure、wrong-version、ACL 和成本；
- 任何 hard gate 回归自动降级或回滚，并生成新的事件证据。

## 14. 所有阶段统一的 Definition of Done

每个工作项只有同时满足以下四类证据才可标记 `COMPLETE`：

1. **实现证据**：代码和迁移进入受控 revision，公开合同有版本；
2. **验证证据**：正向、负向、故障、ACL、版本测试通过；
3. **运行证据**：如果涉及真实路径，必须有隔离或真实环境运行 artifact；
4. **文档证据**：台账记录输入、命令、结果、限制、风险和下一步。

状态词固定为：

- `NOT_STARTED`：尚未开始；
- `IN_PROGRESS`：实现或证据未齐；
- `ENGINEERING_PASS`：隔离工程 Gate 通过；
- `QUALITY_HOLD`：工程可用但真实质量/发布证据不足；
- `QUALIFIED`：本阶段全部真实 Gate 通过；
- `BLOCKED_EXTERNAL`：需要明确的 owner、数据、成本或生产授权；
- `FAILED`：已执行但 Gate 未通过。

禁止使用不带范围的 `COMPLETE`。

## 15. 证据包与持续更新协议

每个 Gate 使用不可变目录：

```text
artifacts/rag-maturity/<gate-id>/<run-id>/
├── manifest.json
├── environment.json
├── inputs.json
├── commands.jsonl
├── results.json
├── failures.jsonl
├── security.json
├── limitations.json
└── checksums.json
```

台账只引用证据包，不复制或改写结果。失败 artifact 保留；修复后生成新 run，不覆盖旧结果。
任何门槛、数据集或评审策略变更必须新增 decision record。

## 16. 文档冻结后的目标重设

本总纲冻结后，执行采用两层目标：

### 项目总目标

按 G0→G9 顺序把系统推进到 `L5 PRODUCTION QUALIFIED`，全过程保持 raw authority、ACL、
version/as-of、真实质量和可回滚发布边界，不以 fixture 或文档声明替代运行证据。

### 当前双轨解析

外部准入轨保持：reviewed bytes 必须进入受保护 revision，并由同 revision remote 3.12/3.13、exact
admission、cleanroom、严格 frontend supply-chain、release-ready、UI reviewer 与独立 Gate 证明。任何本地
bundle/replay 都不能改名为 remote authority。

内部开发轨只执行 durable current-state record 所选择、且已被上位依赖与精确 mutation allowlist 允许的一个
atomic objective。当前记录缺失或矛盾时不自动领取旧任务。T1.1.1 仍要求 G0 `QUALIFIED` 与 G1 Entry
E-01–E-10 `PASS`；此前只允许明确计划的 G0 收口、只读审计和测试/fixture 设计，不实施 G1 schema/runtime，
不改变默认 V1。

精确入口、产物与停止条件仍由阶段卡和 atomic runbook 定义；durable state 只能选择其中合法任务，不能改写
G0→G9 依赖、raw authority、ACL、as-of、质量、回滚或 release hard gates。
