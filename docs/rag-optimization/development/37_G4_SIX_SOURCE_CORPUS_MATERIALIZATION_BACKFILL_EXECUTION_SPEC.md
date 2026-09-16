# G4 六源真实语料授权、分代物化、Backfill、删除传播与回滚可执行规格

版本：2026-08-06 V1  
状态：`G4D DESIGN_ENGINEERING_PASS / G3_GATE_HOLD / DATA_AUTHORIZATION_MISSING / MATERIALIZATION_NOT_STARTED`  
上位目标：`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`  
规划前置：`36_G3_CANONICAL_QUERY_PLAN_SCOPE_ASOF_EXECUTION_SPEC.md`  
默认发布状态：`DEFAULT_V1 / QUALITY_HOLD`  

## 0. 本规格解决什么问题

仓库已经分别实现 Code、Codex、Experiment、Notebook、Document、Workspace 的解析、结构化、检索或
V2 隔离索引，并有 raw object、derivation、tombstone、generation、release evaluator 等局部能力。但
“六个来源都有实现”不等于“真实多源数据已经成熟”：当前缺少一个 owner 授权、语料成员可枚举、输入
不可变、六源分代可协调、失败可重入、删除可传播、回滚不复活被删数据、结果可独立复核的物化协议。

本规格把 G4 冻结为一个数据与索引控制面问题，而不是一次性导入脚本：先获得真实数据授权，再冻结
`QualificationCorpusManifestV1`，以不可变 staging generation 构建六源，完成逐项对账后才通过 CAS
激活一个 `ActiveGenerationSetV1`。任何单源失败都保留旧 generation set；任何删除、授权撤销或保留期
到期都必须沿 raw binding、派生实体、检索单元、Wiki 依赖和缓存传播并产生闭环收据。

本轮只完成代码现状审计、治理合同、状态机、实施序列、测试矩阵和 Gate artifact 设计。没有读取、连接
或改写正式数据库，没有拉取真实语料，没有创建数据授权，没有执行 backfill，也没有改变 V1 默认路由。

## 1. 前置门禁与完成边界

### 1.1 G4 runtime 解锁条件

以下条件必须同时满足，才允许执行 T4.1.1 之后的正式语料物化：

1. G0 exact revision、remote CI、release evidence 与 LIVE-02 已由独立 owner 判定 `QUALIFIED`；
2. G1 六源 raw binding/read authority 已 `QUALIFIED`；
3. G2 raw-verified Wiki grounded answer 已 `QUALIFIED`；
4. G3 canonical plan、Scope、as-of 与 pinned snapshot 已 `QUALIFIED`；
5. 数据 owner、security/privacy、项目 owner 已对 exact authorization digest 签署或提供可验证 receipt；
6. production-mirror dry-run 使用的是相同 schema/migrator/adapter revision，且证明不会访问正式写端点；
7. 默认路由仍为 V1，G4 物化不得隐式发布、shadow、canary 或 default V2；
8. 正式操作必须由部署环境 secret/identity 注入，不得把凭据、原文或隐私字段写入 portable artifact。

### 1.2 本规格的完成定义

`G4D DESIGN_ENGINEERING_PASS` 仅表示：

- 六源当前 ingest/store/runtime 的真实边界已映射到代码；
- 临时 V2 派生索引、legacy 正式存储和 source-specific store 的职责差异已显式化；
- 授权、语料、物化 run、generation set、coverage、删除传播、重入和 rollback 合同已冻结；
- 后续实现、运行和独立评审不需要再猜“哪些数据允许进入、何时算发布、失败后保留什么”。

它不表示真实数据已授权、六源已非空、正式 backfill 已运行、数据库 schema 已迁移或 G4 已通过质量门。

## 2. 当前代码事实图

```text
正式应用存储（legacy/formal）
  ├─ Code IngestionService
  │    ├─ raw_objects/source_events
  │    ├─ index_generations + repositories.active_generation_id
  │    └─ CodeDualWriteCoordinator → Code V2 units/publication（同正式 SQLite）
  ├─ CodexIngestionService
  │    └─ codex legacy generation + raw derivations
  ├─ ExperimentService.sync_mlflow
  │    └─ mutable imported runs + raw derivation(generation_id=NULL)
  ├─ NotebookService.ingest
  │    └─ mutable template/run/cell + raw derivation(generation_id=NULL)
  ├─ DocumentService.ingest
  │    └─ document/version/section/table/... + raw derivation(generation_id=NULL)
  └─ WorkspaceService
       └─ mutable rows → separate audit write

explicit V2 request
  └─ ProductionSourceRuntimeRegistryV2
       ├─ Code 不在 registry（继续使用正式 store 的 Code V2）
       └─ Codex/Experiment/Notebook/Document/Workspace
            ├─ tempfile.TemporaryDirectory
            ├─ 每进程、按需 source-specific SQLite
            ├─ 从正式 legacy 行重新 materialize
            └─ close 时清理全部 V2 派生索引
```

`production_sources_v2.py` 的模块说明明确把五类非 Code V2 store 定义为 disposable、process-local derived
indexes；registry 名称中的 production 只表示查询 facade，不能证明存在持久 production V2 generation。

### 2.1 六源代码权威映射

| 来源 | 当前正式写路径 | 当前 V2 路径 | generation/rollback 事实 | G4 结论 |
|---|---|---|---|---|
| Code | `ingestion.py` + `storage.py` | `rag/sources/code/dual_write.py` 与正式 store | legacy generation 原子 publish；V2 dual-write 在 legacy publish 后执行，失败时清理 V2 并把 legacy generation 标 failed | 有局部分代，但 active pointer 已先切换；需要 stage/validate/activate 重排与恢复证明 |
| Codex | `codex_ingestion.py` | `codex/pipeline_v2.py` + 临时 `CodexV2Store` | legacy 有 generation；V2 store 有 bundle publish/rollback/tombstone/cursor，但正式 ingest 未持续双写 V2 | 两套 generation 真值未统一；临时索引不能作为 backfill 结果 |
| Experiment | `experiments/service.py::sync_mlflow` | `experiment/pipeline_v2.py` + 临时 store | legacy upsert 可变；raw derivation 的 `generation_id=NULL`；V2 有 prepare/publish/fail/rollback/cursor | V2 能力完整度较高，但尚未绑定真实正式 generation 和 corpus membership |
| Notebook | `notebooks/service.py::ingest` | `notebook/adapter_v2.py` + 临时 store | legacy 直接 save；raw derivation `generation_id=NULL`；V2 publication/tombstone，无 generation-set rollback API | revision 可表达但正式 backfill、active pointer、rollback 语义缺失 |
| Document | `documents/service.py::ingest` | `document/adapter_v2.py` + 临时 store | legacy 直接 create；raw derivation `generation_id=NULL`；V2 version/latest/tombstone，无 generation-set rollback API | version family 不等于可协调 generation；需显式 staging 与 pointer |
| Workspace | `workspace/service.py` | `workspace/runtime_v2.py` + 临时 store | legacy mutation 后另写 audit；V2 publish/rollback/tombstone | audit 可缺失或与行状态分离；历史重建会报告不可证状态，不能把当前行冒充 exact history |

### 2.2 已有可复用能力

1. shared raw store 已有 content identity、source event idempotency、derivation 与 tombstone/block list；
2. Code/Codex legacy 有 generation 和“失败保留上一 active”部分语义；
3. Code dual-write 有 generation-scoped cleanup；
4. Codex V2 有 atomic bundle、append cursor、rollback、tombstone；
5. Experiment V2 有 prepare/publish/fail/rollback/backfill cursor；
6. Notebook/Document V2 有 content-addressed publication 与 tombstone guard；
7. Workspace V2 有 publication pointer、tombstone 和 rollback；
8. 五类非 Code runtime 可在隔离目录中重建，适合 dry-run、parity 和 shadow evidence；
9. 各 source release evaluator 可用于 G5/G9，但不能代替 G4 数据授权与完整性对账。

## 3. 现状差异与风险清单

| ID | 代码事实 | 风险 | G4 处置 |
|---|---|---|---|
| MAT-01 | 五类非 Code V2 store 在临时目录 | 进程重启后 generation 消失；无法复核或 pin | 新增持久、受控的 derived root 与 generation catalog；临时路径只准 dry-run |
| MAT-02 | Code 不在五源 registry | “六源 registry”实际只有五源；协调状态不完整 | 六源统一注册表纳入 Code adapter，不要求共用物理 store |
| MAT-03 | Code legacy active pointer 在 V2 dual-write 前切换 | dual-write 失败时 active generation 可能已指向被标 failed 的代 | 改为 immutable stage→双写 validate→CAS activate；旧 pointer 不变 |
| MAT-04 | Codex 正式 ingest 不持续写 CodexV2Store | query-time 重建与 ingest-time generation 可能不一致 | 新 backfill adapter 绑定 raw snapshot、legacy source generation 与 V2 generation |
| MAT-05 | Experiment/Notebook/Document raw derivation 无 generation | 无法证明某次物化使用哪个 raw→derived 集 | G1 binding 后强制 generation-bound derivation；旧 NULL 仅 migration input |
| MAT-06 | Experiment legacy 以 upsert 表示外部 run | 源数据更新会覆盖可观察历史；as-of 与重放不可证 | immutable observed versions + source cursor + generation membership |
| MAT-07 | Notebook legacy 按 path/version/content 构造实体但无 active generation | 多 revision 的 current、回滚和语料边界不统一 | template/revision membership + project source pointer |
| MAT-08 | Document family latest pointer 与 generation 解耦 | latest version 可跨 qualification corpus 或授权边界 | generation 内 version selection；active set 决定可见 publication |
| MAT-09 | Workspace mutation 与 audit 使用两个 store transaction | 行写成功而 audit 失败会让历史不可重建 | transactional outbox/audit envelope；物化前 reconciliation HOLD |
| MAT-10 | raw tombstone 只 invalidates derivations/blocks entity | source V2 index、Wiki、cache、评测 artifact 没有统一传播闭环 | DeletionPropagationRunV1 + 每层 receipt + SLA/overdue gate |
| MAT-11 | 各 V2 store tombstone scope/identity不同 | 删除可能遗漏子实体或无法跨 generation 生效 | canonical deletion target + source projection + no-resurrection ledger |
| MAT-12 | 各 source rollback 行为不同 | rollback 可能复活已 tombstone 或 authorization 已撤销的旧代 | rollback eligibility verifier；新删除水位高于目标时必须 rebuild |
| MAT-13 | 无六源 active generation set | 查询可看到六个不同构建时刻的任意组合 | `ActiveGenerationSetV1` + CAS pointer + G3 snapshot pin |
| MAT-14 | 跨多个 SQLite/store 无分布式事务 | 逐源 activate 中途失败会产生半切换 | 所有代先 immutable ready；只发布一个 control-plane set pointer，不逐源切 active |
| MAT-15 | 无统一 run/cursor/checkpoint | backfill 重试可能重复、覆盖或跳过对象 | content-addressed run identity、stage checkpoints、source-native cursor |
| MAT-16 | source cursor 未统一绑定输入 snapshot | 旧 cursor 可误用于新授权/新语料/新 adapter | cursor 必须绑定 auth/corpus/adapter/schema/source snapshot digests |
| MAT-17 | corpus 最低数量存在，但成员、root provenance、困难切片未冻结 | 数量达标仍可能重复同根、只含容易样本或泄漏 test | immutable corpus membership + provenance group + slice denominator |
| MAT-18 | 无 owner/use/retention/delete/export manifest | 真实数据可能被超范围收集、保留或传给外部模型 | `QualificationDataAuthorizationV1` 是任何 read/materialize 的前置 hard gate |
| MAT-19 | 无 expected→discovered→admitted→published 守恒 | “非空”掩盖漏数、隔离、解析失败或 orphan | `MaterializationCoverageReportV1` 记录精确分母和 typed gap |
| MAT-20 | failure/counter 字段各源自定义 | 不同来源的 partial/skip/quarantine 无法比较 | common stage outcome + source-specific reason registry |
| MAT-21 | release evaluator 与 materialization authority 可混用 | 工程评测 PASS 被误当成数据授权或发布许可 | 四类 authority 分离：data、build、quality、release |
| MAT-22 | portable artifact 若复制原文会扩大数据面 | 评审包本身形成二次泄漏 | portable package 仅保存 digest、统计、safe locator projection 和 receipt |
| MAT-23 | 资格语料与后续 G5 test split 尚未隔离 | backfill 调试可提前看 test，形成评测泄漏 | G4 只物化 qualification corpus；G5 再按 root provenance 冻结 split |
| MAT-24 | cleanup 生命周期未绑定保留与 legal hold | 过早删除旧代破坏回滚；过晚保留违反授权 | retention scheduler 同时校验 hold、rollback window、revocation 和 deletion SLA |

## 4. G4 不可变原则

1. 未经 exact data authorization，不读取真实源、不保存成员清单、不启动 adapter。
2. 数据授权、构建权限、质量判定和生产发布是四种不同 authority，不得互相替代。
3. corpus membership 必须在第一次正式读取前冻结；变更成员必须生成 successor manifest。
4. corpus manifest 只包含安全 locator/digest；portable artifact 不复制 raw bytes、秘密、正文或个人内容。
5. 一个 raw root provenance group 不得通过别名、导出或不同 adapter 重复计数。
6. 六源都必须有非空、可枚举、owner-authorized 的 expected denominator；联合非空不能掩盖单源空白。
7. 每个 derived entity/retrieval unit 必须可回绑 exact raw object/version、adapter 和 generation。
8. staging generation 不可查询为 active；构建完成后也须先验证守恒、ACL、raw read 与 schema。
9. 跨 store 不伪装分布式原子事务；用 immutable source generations + 单一 generation-set pointer 实现可恢复切换。
10. active generation set 只能通过 expected previous digest 的 CAS 更新。
11. 任一来源未 ready、未授权、为空或有未分类 gap，整个 generation set 不得 activate。
12. active pointer 切换失败时旧 generation set 必须仍是唯一可见集合。
13. retry/resume 的 identity 和 cursor 必须绑定全部输入；不相等时不得续跑。
14. 同一输入重复执行必须返回同一结果或显式 successor，不得生成重复实体和重复关系。
15. quarantine、skip、parse failure、not found、unauthorized、tombstoned 不得折叠为同一个 missing。
16. 删除/撤权优先级高于 rollback、retention 和 LKG；任何回滚不得复活被禁对象。
17. 原始删除必须传播到 source index、Wiki dependency、query cache 和导出索引；每层都有收据。
18. ACL 必须在 capture、stage、query、raw read 和 portable projection 五个边界保持等价或更严格。
19. Workspace 无完整 audit chain 时，只允许 current qualification；禁止声称 exact historical corpus。
20. G4 只证明数据与物化成熟度，不用 G4 语料调 G5 test 阈值，不提前授权 shadow/canary。
21. 失败 artifact、quarantine 清单和 gap denominator 必须保留；修复后生成新 run，禁止覆盖。
22. 默认 V1 在整个 G4 保持不变；G4 Gate PASS 也不能自动改变任何 release route。

## 5. Authority 模型

### 5.1 `QualificationDataAuthorizationV1`

```text
schema_version = qualification-data-authorization-v1
authorization_id
project_id / environment
data_owner / data_controller / technical_operator
purpose
allowed_source_instances[]
  source
  source_instance_safe_id
  source_instance_sha256
  allowed_collection_window
  allowed_object_types[]
  excluded_paths_or_labels[]
classification
allowed_fields[] / prohibited_fields[]
required_redactions[]
acl_policy_sha256
cross_source_linkage_policy
external_llm_policy = prohibited | digest_only | reviewed_fields
export_policy
retention_policy
  raw_retention
  derived_retention
  audit_retention
  rollback_window
  legal_hold_policy
deletion_sla
revocation_mode
valid_from / expires_at
owner_receipt_sha256
security_receipt_sha256
privacy_receipt_sha256?
revision_sha256
content_sha256
```

字段中不保存签署者私钥、access token 或原始个人标识。receipt 由独立 verifier 解析 trust/keyset、角色、
时效、revision 和 content digest；本地作者不能自行把 PENDING draft 升级成授权。

### 5.2 authority 分层

| Authority | 决定什么 | 不能决定什么 |
|---|---|---|
| data authorization | 哪些源、字段、时段、用途可读取/保存/链接/导出 | adapter 正确、质量达标、可生产发布 |
| materialization authority | exact revision/config/schema 可在指定环境执行哪一次 run | 数据授权、质量、默认路由 |
| quality authority | G5/G6 的真实独立评测是否过门 | 数据超范围使用、生产切换 |
| release authority | shadow/canary/default V2 的范围和回滚策略 | 重写数据 owner 决策或忽略删除 |

任何 authority 过期或撤销都不得用 LKG、缓存或旧 artifact 继续服务。

## 6. 资格语料合同

### 6.1 `QualificationCorpusManifestV1`

```text
schema_version = qualification-corpus-manifest-v1
corpus_id / revision
project_id / environment
authorization_sha256
purpose = G4_QUALIFICATION
frozen_at
membership_policy_sha256
source_memberships[]
  source
  source_instance_sha256
  expected_root_count
  expected_object_count
  members[]
    member_id
    safe_locator
    locator_sha256
    source_object_id_sha256
    expected_source_version
    raw_content_sha256?
    root_provenance_group
    slice_tags[]
    acl_class
    inclusion_reason
    exclusion_reason?
    expected_children?
cross_source_links[]
  link_id / predicate / endpoint_member_ids / derivation / reviewed
exclusions[]
provenance_group_digest
content_sha256
```

成员 identity 使用 source-native immutable version。对于读取前无法知道 content hash 的源，可先保存
provider version/ETag/commit/run observed version；capture 后生成 `ObservedCorpusManifestV1` successor，
但不得静默替换原 manifest。成员缺失、版本变化或 ACL 变化都产生 typed drift，并停止正式 stage。

### 6.2 root provenance 与重复规则

- Git fork、镜像、同 commit 导出包属于同一 root，除非 reviewer 证明独立变化链；
- Codex 原 rollout 与其导出的 Markdown/summary 属于同一 root；
- MLflow run、Notebook 执行和 Document 报告若来自同一次实验，可跨源关联，但各源成员仍独立计数；
- PDF、DOCX 和由同一文件转换的文本属于同一 document root；
- Workspace audit 与从其导出的页面属于同一 control-plane root；
- 同 root 可用于跨源一致性 case，但不能在独立样本 denominator 中重复加权。

### 6.3 G4 与 G5 数据边界

G4 corpus 用于证明授权、摄取、版本、覆盖、恢复和删除，不用于选择 ranking/generation 阈值。G5 必须
从 G4 已授权 universe 生成独立的 train/calibration/test split manifest，并按 root provenance 分组；
G4 调试日志不得暴露未来 test labels 或人工 gold answer。

## 7. 六源 corpus 最低边界与困难切片

下表的数量是最低资格边界，不是质量样本量保证。每个来源还必须保存 exact expected denominator 和
root provenance；同一 root 的多个版本可满足版本覆盖，但不虚增独立 root 数。

### 7.1 Code

- 至少 2 个 owner-authorized repository，每个至少 2 个 immutable commit/version；
- 至少覆盖 1 次 rename/move、1 次删除、1 个 broken/unresolved edge、1 个 wrong-commit hard negative；
- 包含 file、symbol、diff、history、graph 和 raw line-range 可读成员；
- 每个 repo 对账 discovered files、admitted files、secret quarantine、parse error、units、edges、raw bindings；
- submodule/LFS/binary/oversized/ignored path 必须有明确 disposition，不得从分母消失。

### 7.2 Codex

- 至少 20 个 thread，来自至少 3 类开发 workflow；
- 至少 5 个包含失败/重试，5 个包含 validation，3 个有 redaction，3 个 tombstone/deletion 演练；
- 覆盖 live/incomplete session、oversized/parse error、subagent 或多 agent、命令、文件变更、tool result；
- reasoning/private payload 必须按授权排除，redacted content 不得通过 raw/cache/trace 泄漏；
- thread→turn→item→episode→retrieval unit 与 source sequence 可完整对账。

### 7.3 Experiment

- 至少 20 个 run、5 个 experiment/group，包含至少 3 个 seed 或等价重复观测；
- 覆盖 success、failed、cancelled/deleted、missing metric、unit mismatch、dataset/config/environment drift；
- run 必须绑定 source version、metric definition/observation、artifact、dataset/config/environment 和可用 code commit；
- 外部 run 更新必须形成 observed version，不覆盖历史；
- artifact 只按授权读取，URI 不代表内容已验证。

### 7.4 Notebook

- 至少 10 个 template，每个至少 2 个 revision/execution；
- 覆盖 success/error、stale output、参数差异、artifact、out-of-order execution、空输出与被删 template；
- notebook raw JSON、cell source、output、execution order、kernel/environment 和 external artifact binding 分开；
- 输出含 secret/个人数据时须 quarantine/redact，不能只清理 cell source；
- template/revision/execution/current 的选择规则必须显式，不以文件 mtime 冒充 source version。

### 7.5 Document

- 至少 20 个 document root，至少 2 个有多版本；
- 覆盖 paragraph、table、figure、citation、claim、conflict/counter-evidence、parse/provider failure；
- 至少包含两种实际使用格式，格式集合由授权语料决定，不为凑数转换同一 root；
- raw bytes、parser output、page/section/table/figure/citation locator 和 parse provenance 可回绑；
- OCR/provider 推断必须与 observed raw 分权，低 parse quality 进入 quarantine 或 limitation。

### 7.6 Workspace

- 至少 3 个 topic，每个至少 2 个 iteration；
- 覆盖 planned/in-progress/completed/failed/cancelled/archived 状态、dependency/blocker、decision 与 evidence link；
- 至少 2 个删除/归档传播 case、2 个 optimistic version conflict、2 个 as-of boundary case；
- mutation 与 audit chain 必须对账；缺 audit 的 row 标为 `HISTORICAL_AUTHORITY_UNAVAILABLE`；
- 自动 relation 只有 reviewed/deterministic policy 允许时才进入 qualification generation。

### 7.7 跨源 linkage

至少建立可审计的真实链路：

```text
Workspace topic/iteration
  → Codex development episode
  → Code commit/diff/symbol
  → Experiment run/metric/dataset/config
  → Notebook execution/cell/output
  → Document claim/table/figure/citation
```

每条 link 必须有 exact endpoints、predicate、derivation、review state、ACL intersection、valid/observed time
和 raw/reviewed evidence。字符串相似、标题相同或模型猜测不得自动成为 confirmed edge。

## 8. 物化合同

### 8.1 `SourceMaterializationPlanV1`

```text
source / source_instance_sha256
authorization_sha256
corpus_manifest_sha256
observed_corpus_manifest_sha256?
adapter_revision_sha256
adapter_contract_version
schema_version / migrator_sha256
raw_binding_contract_sha256
input_source_snapshot_sha256
expected_members[]
source_native_cursor_start?
resource_budget
timeout_policy
quarantine_policy
output_generation_id
idempotency_key
content_sha256
```

`output_generation_id` 由以上语义输入 canonical digest 派生；transport attempt、hostname、开始时间不进入
identity。相同输入再次运行必须命中相同 generation 或得到 `ALREADY_MATERIALIZED`。

### 8.2 `SourceGenerationManifestV3`

```text
source / source_instance_sha256
generation_id
state = STAGING | READY | ACTIVE_MEMBER | FAILED | RETIRED | TOMBSTONED
materialization_run_sha256
authorization_sha256 / corpus_manifest_sha256
input_source_snapshot_sha256
adapter_revision_sha256 / schema_version
started_at / completed_at
raw_watermark / deletion_watermark
member_counts
  expected / discovered / admitted / quarantined / staged / published / tombstoned
entity_counts / retrieval_unit_counts / edge_counts
raw_binding_coverage
acl_summary_sha256
failure_summary_sha256
coverage_report_sha256
index_artifact_sha256
content_sha256
```

manifest 只记录计数和 digest，不嵌入 raw content。`READY` 需通过 source verifier；单源 runtime 不得自行把
状态改为 `ACTIVE_MEMBER`，该状态由 generation-set activation receipt 投影。

### 8.3 `MaterializationRunV1`

```text
run_id / attempt_id
state
requested_by_authority_sha256
revision_sha256 / environment_sha256
authorization_sha256 / corpus_manifest_sha256
source_plans[] / source_plan_digests[]
expected_previous_generation_set_sha256
checkpoints[]
source_outcomes[]
side_effect_ledger_sha256
failure_ledger_sha256
coverage_bundle_sha256
activation_receipt_sha256?
rollback_receipt_sha256?
content_sha256
```

### 8.4 run 状态机

```text
DRAFT
  → AUTHORIZED
  → PREFLIGHTED
  → STAGING
  → SOURCE_READY
  → RECONCILED
  → ACTIVATION_READY
  → ACTIVATED

任意前置状态 → HELD | FAILED
ACTIVATED → ROLLBACK_REQUESTED → ROLLED_BACK | ROLLBACK_HELD
任意已物化状态 → DELETION_PENDING → DELETION_RECONCILED
```

状态推进使用 expected-state CAS。`FAILED` 记录真实副作用与 cleanup eligibility；不能把已创建 staging 行
抹掉后伪装成“未运行”。

## 9. 六源协调与 active generation set

### 9.1 `ActiveGenerationSetV1`

```text
generation_set_id
project_id / environment
authorization_sha256
corpus_manifest_sha256
canonical_plan_compatibility_sha256
source_members[6]
  source
  source_instance_sha256
  generation_id
  generation_manifest_sha256
  raw_watermark / deletion_watermark
  schema_version / adapter_revision_sha256
wiki_dependency_state
activated_at
previous_generation_set_sha256?
activation_receipt_sha256
content_sha256
```

六个 source member 必须恰好为 Code、Codex、Experiment、Notebook、Document、Workspace；缺一、重复、
为空、过期或 authorization 不一致都拒绝。G3 `SnapshotManifestV3` pin 的就是 exact generation-set digest
及其六个成员，不再按请求临时拼六个 active pointer。

### 9.2 不伪装跨库原子事务

多个 source store 无共同事务时采用：

1. 每源写 immutable staging generation；
2. 每源完成 schema、membership、ACL、raw binding、digest、query smoke 与 tombstone watermark 验证；
3. 所有六源 manifest 进入 `READY`，但对 active queries 不可见；
4. control plane 以 expected previous set digest 做一次 CAS，写入新的 immutable set pointer；
5. query 只读取 set pointer，再按 exact generation IDs 读取；
6. 若 CAS 或后续 projection 失败，旧 set pointer 不变，新代保持 READY/HELD；
7. recovery 依据 activation intent/receipt 重放幂等 projection，不能逐源猜当前状态。

### 9.3 Wiki 构建顺序

Wiki 不是第七个独立数据 authority。它必须在六源 set 激活候选已冻结后，由 G2/G3 合同读取 exact 六源
generation，生成 candidate Wiki manifest；raw receipt/claim coverage 验证后再随 activation receipt 绑定。
Wiki 构建失败时六源 generation 可保留 READY，但 generation set 不激活。

## 10. Dry-run、Migration 与正式 Backfill

### 10.1 production-mirror dry-run

dry-run 必须：

- 使用 production-mirror schema、索引、SQLite/外部服务版本和 migrator；
- 使用复制后经授权的 digest-only/sanitized snapshot，不读正式写端点；
- 拦截并记录所有 filesystem/database/network target；
- 生成预期 DDL、row/index size、迁移时间、锁范围、磁盘峰值、checkpoint 数与失败注入结果；
- 证明 `formal_mutation_count=0`、`unapproved_network_call_count=0`；
- 不把 mirror 结果标为真实质量或 production observation。

### 10.2 migration 顺序

```text
M0  authority/corpus verifier + no-op dry-run
M1  materialization control tables / side-effect ledger
M2  generation-bound raw binding schema
M3  six source immutable generation catalogs
M4  source-specific staging writers/readers
M5  coverage/reconciliation/deletion ledger
M6  generation-set pointer + CAS + G3 snapshot adapter
M7  Wiki exact-set builder/projection
M8  backfill runner/checkpoint/resume/recovery
M9  rollback/deletion/retention scheduler
M10 portable Gate/export verifier
```

每步先 schema expand、后 backfill、再 read opt-in；未经过 shadow parity 前不删除旧列/旧表/旧 reader。

### 10.3 正式运行前 preflight

逐项验证：authorization 时效、operator identity、revision、source endpoints、只读 capture 权限、write target、
free space、schema version、migration lock budget、旧 active set、corpus members、secret policy、retention、
deletion backlog、source cursor 和 rollback target。任一不一致在任何 raw read 前 `HELD`。

## 11. Reentrant、Checkpoint 与 Resume

### 11.1 checkpoint contract

每个 checkpoint 至少记录：

```text
run_id / source / stage
input_identity_sha256
cursor_type / cursor_payload_sha256
last_complete_member_id
processed / succeeded / quarantined / failed counts
side_effect_range
output_partial_digest
created_at / expires_at
content_sha256
```

cursor payload 可保留在受保护 store，portable artifact 只暴露 digest。checkpoint 只能在一项 source-native
事务完整后推进，不能先写 cursor 后写数据。

### 11.2 resume 规则

- authorization、corpus、adapter、schema、source snapshot、ACL 或 deletion watermark 任一变化，旧 cursor 失效；
- 失效 cursor 返回 `RESUME_INPUT_MISMATCH`，不得从相似位置继续；
- 已成功 immutable member 以 identity lookup 跳过，并再次验证 content/ACL；
- incomplete member 从其原子边界重做，不合并未知 partial rows；
- attempt timeout/cancel 后后台 worker 必须有 lease fencing，旧 worker 不得继续写；
- 连续 retry 需要 bounded attempt policy，避免永久 poison member 阻塞且无 artifact。

### 11.3 idempotency

至少验证三层：source event、raw object/binding、derived generation publication。相同 source-native object 的
同 version/content 在同 generation 只能有一个 canonical identity；不同 version 不得被 ON CONFLICT 吞成
同一行。重复 run 的 generation digest、member counts 和 coverage digest 必须一致。

## 12. Coverage 与 reconciliation

### 12.1 `MaterializationCoverageReportV1`

每源及总计必须包含：

```text
expected_roots / expected_objects
discovered / source_missing / source_drifted
authorized / unauthorized / expired
admitted / excluded / quarantined / parse_failed / skipped
raw_persisted / raw_read_verified
bindings_expected / bindings_valid / bindings_missing / bindings_invalid
entities_staged / entities_published / retrieval_units / edges
duplicate_roots / duplicate_objects / identity_collisions
orphan_raw / orphan_derived / orphan_units / dangling_edges
acl_match / acl_narrowed / acl_mismatch
version_match / version_mismatch
tombstone_expected / tombstone_applied / deletion_overdue
typed_failure_counts
content_sha256
```

所有 rate 同时保存 numerator/denominator。空 denominator 不按 100% 处理；它是 `NOT_APPLICABLE` 或
`INVALID_CORPUS`。`skipped`、`quarantined` 和 `parse_failed` 都必须属于 expected membership 的守恒式。

### 12.2 hard reconciliation

```text
expected
  = source_missing + source_drifted + unauthorized + admitted + excluded

admitted
  = quarantined + parse_failed + skipped + staged

staged
  = published + materialization_failed
```

若某来源允许一个 object 产生多个版本/子实体，守恒以 member/root 层计算，derived counts 单独报告，不能
把 entity 数量拿来补 expected object 缺口。所有差异必须有 versioned reason code；unknown=hard fail。

### 12.3 raw read sample

G4 Gate 对 corpus 成员执行分层 sample，并要求 sample 内 100%：selector 精确、ACL fail-closed、版本/内容
digest 一致、line/page/cell/item locator 可读、receipt 回绑 generation。样本必须覆盖六源和困难切片；若
corpus 总量低于 sample 上限则全量读取。任何 sample failure 使该来源 generation 不得 READY。

## 13. 删除、撤权、保留与传播

### 13.1 `DeletionPropagationRunV1`

```text
deletion_id
trigger = owner_request | authorization_revoked | retention_expired | source_tombstone
authority_sha256
project/source/source_instance
raw_object_ids_or_member_ids_sha256
effective_at / deadline_at
raw_tombstone_receipts[]
binding_invalidation_receipts[]
source_index_receipts[]
generation_impact[]
wiki_dependency_receipts[]
cache_invalidation_receipts[]
export/downstream_receipts[]
overdue_items[]
completed_at
content_sha256
```

### 13.2 传播顺序

1. 验证删除 authority 和 exact target，不泄漏未授权对象是否存在；
2. 写 raw tombstone/revocation watermark；
3. invalidate raw binding 和 block derived identity；
4. source-specific store 关闭对应 unit/entity/edge，不只隐藏顶层对象；
5. 标记受影响 generation/set 不再 eligible，必要时构建 successor generation；
6. 使 Wiki page/claim dependency stale 或 tombstone，禁止继续生成引用；
7. 清除 query/vector/rerank/answer cache 和导出索引；
8. 对外部 downstream 发送可验证 deletion request/receipt；
9. reconciliation 证明 active query 无可见 target，且未到期项为零；
10. 保留最小审计 digest 和删除收据，按政策清理内容。

### 13.3 删除 SLA

SLA 从 effective trigger receipt 开始，不能从 worker 实际启动时重置。暂停、失败、第三方等待都进入
overdue ledger；达到 deadline 仍有可见副本时 hard fail，相关项目自动降级为 V1/拒答（由后续 release
control plane 执行），不能仅发告警继续服务。

## 14. Rollback 与 cleanup

### 14.1 rollback eligibility

回滚目标必须同时满足：

- immutable generation set 和六源 manifest 都可验证；
- data authorization 仍有效且覆盖目标全部成员；
- 目标 schema/runtime 仍受支持；
- 目标的 deletion watermark 不早于所有已生效删除；
- 不包含已 tombstone/revoked/expired member；
- G3 snapshot adapter 能 pin exact set；
- independent operator authority 和 change record 有效。

若目标早于某次删除，禁止直接 pointer rollback；必须从目标语义输入减去删除成员，构建新的 successor
generation set。rollback 不是恢复物理旧文件或删除新 generation。

### 14.2 rollback 执行

1. 冻结新 materialization 与 activation；
2. 验证目标 eligibility 和当前 expected set digest；
3. 运行 read-only compatibility/raw/deletion smoke；
4. CAS 指向 eligible target 或新建 successor；
5. 验证六源、Wiki、cache 和 G3 snapshot 一致；
6. 写 immutable rollback receipt；
7. 当前失败代标为 RETIRED/HELD，保留以供调查；
8. 独立复核后才恢复任何后续 rollout。

### 14.3 cleanup

旧代只有同时满足 retention、rollback window、legal hold、open incident、deletion/export receipt 五类条件才
可清理。cleanup 先生成 exact target manifest 和 dry-run counts，再删除 derived content；raw/audit 的清理
分别遵循授权。禁止按 glob、目录年龄或“不是 active”批量删除。

## 15. Source-specific 实施要求

### 15.1 Code adapter

- 将 legacy generation 和 Code V2 unit/publication 纳入同一 staging intent；
- dual-write/semantic/dense/graph 全部验证后才 CAS repository/source generation pointer；
- 若一个项目多 repo，generation set 保存每 repo exact generation map 及 aggregate digest；
- rename/delete/history depth/SCIP unavailable 都有 typed coverage；
- `delete_repository` 的物理删除路径迁移为受 authority 控制的传播 run，不可绕过 raw/Wiki/cache receipts。

### 15.2 Codex adapter

- legacy session discovery 与 V2 episode/unit publication共享 observed source snapshot；
- live session 不进入 immutable generation，或在 quiet/stable watermark 后作为新 version；
- append cursor 绑定 rollout inode/path-safe identity、byte/sequence watermark 和 corpus digest；
- removed session 不是静默 absent，必须进入 deletion/tombstone reconciliation；
- reasoning/private/redacted fields 在 raw、episode、unit、trace 和 portable report 全链验证。

### 15.3 Experiment adapter

- 将 MLflow mutable view 转成 immutable run observed version；
- V2 prepare/publish/rollback/cursor 接入共享 MaterializationRun；
- metric 的 name/split/step/unit/definition identity 完整，重复 step 不互相覆盖；
- artifact URI 与 artifact content authority 分开；未读取 artifact 不声称 raw verified；
- deleted lifecycle stage 触发 tombstone propagation，而非只把 experiment status 设为 deleted。

### 15.4 Notebook adapter

- legacy raw derivation 必须绑定 generation；
- template/revision/execution/cell/output 的 membership 和 current projection 分离；
- V2 store 增加 generation catalog、active-set projection 和 no-resurrection rollback verifier；
- stale output、execution order、error、parameter、artifact 与 environment 进入 coverage；
- source file update/删除以 observed version/tombstone 处理，不按 path upsert 覆盖。

### 15.5 Document adapter

- raw bytes、parser provider/version、parse result、page/table/figure/citation locator 绑定 exact generation；
- family `latest_version_id` 只在 generation 内/active set projection中可见，不作为全局未授权 current；
- V2 store 增加 generation catalog、active-set projection 与 rollback guard；
- provider/OCR failure 保留 raw 与 typed limitation，不生成伪结构；
- family/version deletion 传播到 claim/evidence edge、Wiki claim/citation 与 cache。

### 15.6 Workspace adapter

- mutation、version check、audit/outbox 在同一事务或可证明的 exactly-once outbox 完成；
- backfill 先检测 unaudited row、truncated audit、unreconstructable transition；
- 当前态可物化不代表 historical-as-of 可用，capability 分开；
- V2 rollback 校验全局 deletion watermark，不能只检查 target publication 存在；
- archive/delete/relation review 的 effective time 与 actor authority 可审计。

## 16. 原子实施顺序

| 顺序 | ID | 唯一主结果 | 允许范围 | 退出证据 |
|---:|---|---|---|---|
| 1 | T4.0.1 | 当前六源 code/data path audit | 只读代码/文档 | MAT-01–24 + code map；ENGINEERING_PASS |
| 2 | T4.1.1 | QualificationDataAuthorizationV1 | contract/verifier/PENDING draft | forged/expired/role/revision tests；真实 receipt 前 HOLD |
| 3 | T4.1.2 | QualificationCorpusManifestV1 | safe membership/provenance only | canonical/tamper/drift/duplicate-root tests |
| 4 | T4.1.3 | Corpus authorization assembler | authorization + membership join | source/field/time/ACL/exclusion exact match |
| 5 | T4.2.1 | MaterializationRun/control schema | expand-only migration | state CAS/idempotency/side-effect ledger tests |
| 6 | T4.2.2 | SourceGenerationManifestV3 | shared contract/verifier | state/digest/count/watermark tamper matrix |
| 7 | T4.2.3 | ActiveGenerationSetV1 | control-plane pointer/CAS | exactly six/old pointer survives fault |
| 8 | T4.3.1 | generation-bound raw binding migration | G1 contract only | NULL inventory/dry-run/backfill/revert |
| 9 | T4.4.1 | Code staging adapter | code ingest/dual-write projection | no early active；2×2 corpus reconciliation |
| 10 | T4.4.2 | Codex staging adapter | legacy+V2 exact snapshot | live/redaction/remove/cursor cases |
| 11 | T4.4.3 | Experiment staging adapter | immutable observed versions | 20/5 + metric/artifact/deletion cases |
| 12 | T4.4.4 | Notebook staging adapter | generation catalog/pointer | 10×2 + stale/error/order/tombstone |
| 13 | T4.4.5 | Document staging adapter | generation catalog/pointer | 20 + multi-version/provider/tombstone |
| 14 | T4.4.6 | Workspace staging adapter | audit/outbox reconciliation | 3×2 + state/version/as-of limitations |
| 15 | T4.5.1 | MaterializationCoverageReportV1 | counters/reason registry | conservation/orphan/ACL/version properties |
| 16 | T4.5.2 | six-source coordinator | stage/validate/CAS only | one-source fault never yields partial active set |
| 17 | T4.5.3 | exact-set Wiki builder hook | G2/G3 manifest projection | Wiki set mismatch fail closed |
| 18 | T4.6.1 | checkpoint/resume/fencing | batch runner | restart/cancel/late worker/input drift matrix |
| 19 | T4.7.1 | DeletionPropagationRunV1 | raw→index→Wiki→cache/export | SLA/no-existence-leak/no-resurrection |
| 20 | T4.7.2 | rollback/retention/cleanup | pointer + policy scheduler | newer delete blocks old rollback；dry-run cleanup |
| 21 | T4.8.1 | production-mirror full dry-run | mirror only；formal mutation forbidden | schema/index/capacity/fault/zero-mutation package |
| 22 | T4.9.1 | portable G4 Gate + independent review | artifact/verifier only | GM-01–54；`QUALIFIED` 或明确 HOLD/FAIL |

每个目标只能领取一行；source adapters 可在共享合同稳定后并行实现，但 T4.5.2 不得在六个 adapter 各自
通过前开始，正式 corpus run 不得在 T4.1 真实 receipt 前开始。

## 17. 验证矩阵（GM-01–GM-54）

### 17.1 Authorization 与 corpus（GM-01–10）

| Case | 验证 | Hard exit |
|---|---|---|
| GM-01 | valid owner/security/privacy receipts bind exact authorization/revision | PASS |
| GM-02 | missing/forged/unknown/expired receipt | zero source read；HELD |
| GM-03 | source instance 不在 allowlist | zero source read；UNAUTHORIZED |
| GM-04 | field/time/purpose/export 超授权 | zero source read；UNAUTHORIZED |
| GM-05 | corpus canonical copy/reorder | same digest；semantic change new digest |
| GM-06 | member source version drift | no stage；SOURCE_DRIFTED |
| GM-07 | duplicate root via alias/export | denominator 不重复；trace 保留 |
| GM-08 | exclusion 缺 reason 或 matched=false | corpus invalid |
| GM-09 | portable manifest 扫描 raw/secret/PII content | zero leakage |
| GM-10 | authorization revoked mid-preflight | fencing prevents all later writes |

### 17.2 Shared control plane（GM-11–20）

| Case | 验证 | Hard exit |
|---|---|---|
| GM-11 | MaterializationRun canonical/tamper/copy | exact verify |
| GM-12 | illegal state transition/double activation | CAS reject |
| GM-13 | same input repeated | same generation/result；duplicate=0 |
| GM-14 | changed adapter/schema/corpus/auth | old cursor/generation identity rejected |
| GM-15 | exactly six distinct source members | missing/duplicate/empty reject |
| GM-16 | one source READY failure | old active set unchanged |
| GM-17 | activation process crash before CAS | old set active；candidate recoverable |
| GM-18 | crash after CAS before projection receipt | deterministic recovery；one set visible |
| GM-19 | concurrent activation with same expected previous | exactly one wins |
| GM-20 | query pins set while new set activates | query remains on pinned set |

### 17.3 六源 ingestion（GM-21–38）

| Case | 验证 | Hard exit |
|---|---|---|
| GM-21 | Code 2 repo×2 version expected/admitted/published conservation | unexplained gap=0 |
| GM-22 | Code rename/delete/wrong-commit/broken edge | typed slices; raw binding exact |
| GM-23 | Code dual-write/dense/graph fault | no early active；old generation readable |
| GM-24 | Codex 20 thread membership and sequence | all stable members accounted |
| GM-25 | Codex live/oversized/parse error | typed exclusion/hold；not silently absent |
| GM-26 | Codex reasoning/redaction/raw/cache/trace scan | leakage=0 |
| GM-27 | Experiment 20 runs/5 groups observed-version identity | update does not overwrite history |
| GM-28 | Experiment metric unit/step/definition/artifact | exact structured identities |
| GM-29 | Experiment failed/deleted/source cursor resume | tombstone/cursor exact |
| GM-30 | Notebook 10×2 revision/execution membership | current/history projection exact |
| GM-31 | Notebook stale/error/out-of-order/parameter/artifact | typed facts/limitations |
| GM-32 | Notebook tombstone then rollback attempt | no resurrection |
| GM-33 | Document 20 roots + multi-version | root/version denominator exact |
| GM-34 | Document table/figure/citation/claim locator raw read | 100% sampled receipt valid |
| GM-35 | Document parser/provider failure + tombstone | raw retained per policy；no fabricated structure |
| GM-36 | Workspace 3 topic×2 iteration/current state | membership/state exact |
| GM-37 | Workspace unaudited/truncated/conflicting history | historical authority refused |
| GM-38 | Workspace delete/version/relation review | effective state and tombstone exact |

### 17.4 Coverage、resume 与故障（GM-39–46）

| Case | 验证 | Hard exit |
|---|---|---|
| GM-39 | expected/discovered/admitted/staged/published equations | all conservation equations hold |
| GM-40 | orphan raw/derived/unit/dangling edge | zero unclassified orphan |
| GM-41 | ACL widen/narrow/mismatch | widen=0；mismatch hard fail |
| GM-42 | raw read stratified sample across six sources/slices | success=100% |
| GM-43 | worker crash at every checkpoint boundary | resume exact；duplicate=0 |
| GM-44 | cancelled worker resumes writes after lease expiry | fenced write=0 |
| GM-45 | source timeout/rate limit/partial page | typed partial；cursor at last complete item |
| GM-46 | rerun completed corpus from empty mirror | generation/coverage digest parity |

### 17.5 删除、回滚与 portable Gate（GM-47–54）

| Case | 验证 | Hard exit |
|---|---|---|
| GM-47 | raw tombstone propagates to source unit/entity/edge | active visibility=0 |
| GM-48 | affected Wiki page/claim/citation and all caches | stale visible claim=0 |
| GM-49 | downstream export deletion receipt | overdue=0 or Gate FAIL |
| GM-50 | deletion target unauthorized/not found | no existence leak；safe receipt |
| GM-51 | rollback to generation older than deletion watermark | rejected；requires successor rebuild |
| GM-52 | eligible pointer rollback during reader load | pinned reads consistent；one active set |
| GM-53 | retention cleanup dry-run/hold/window/target exactness | no broad/glob delete；audit preserved |
| GM-54 | portable copy/native verifier/independent reviewer replay | no raw bytes；all digests/denominators reproduce |

## 18. G4 Gate artifact

```text
artifacts/rag-maturity/g4/<run-id>/
├── manifest.json
├── environment.json
├── revision-and-migrator.json
├── authorization-receipts.json
├── corpus-manifest.json
├── observed-corpus-manifest.json
├── source-plans.json
├── materialization-run.json
├── source-generations.json
├── active-generation-set.json
├── coverage-reports.json
├── checkpoints.jsonl
├── side-effects.jsonl
├── failures.jsonl
├── deletion-drill.json
├── rollback-drill.json
├── production-mirror-dry-run.json
├── security.json
├── limitations.json
├── reviewer-decision.json
└── checksums.json
```

portable 副本不得包含 raw bytes、数据库、访问 token、未脱敏 locator 或可逆个人标识。正式 verifier 在
受保护环境内验证 raw/sample；portable package 只验证相应 receipt/digest 和 safe aggregate。

### 18.1 hard exits

G4 只有以下全部成立才可 `QUALIFIED`：

1. exact authorization receipts 有效、未过期、未撤销，且职责分离成立；
2. corpus 六源分别达到最低成员与困难切片，root duplicate/leakage=0；
3. 六源 active generation 全部非空，manifest、schema、adapter、raw/deletion watermark 可验证；
4. expected membership 守恒，无 unclassified gap/orphan/dangling edge；
5. raw binding/read 分层样本成功率 100%，ACL/version/content/generation 全匹配；
6. failed/cancelled/retried run 不改变旧 active set，重复运行 digest parity；
7. generation-set CAS、query pin 和 Wiki exact-set parity 通过；
8. deletion drill 无过期可见副本，rollback 无 tombstone/revocation resurrection；
9. production-mirror full dry-run 正式 mutation=0，schema/index/capacity/fault evidence 完整；
10. portable copy 原生验证通过，independent reviewer 对 exact revision/artifact 给出 `QUALIFIED`。

以下任一项为 hard HOLD/FAIL：authorization PENDING、任一来源空、临时 V2 store 被当正式 generation、
unknown gap、raw binding NULL 未处理、partial active set、删除逾期、旧代复活、portable raw 泄漏、正式库
未授权写入、作者自签 data/release authority。

## 19. 观测与安全要求

运行时至少记录 aggregate telemetry：run/state/source/stage duration、member counts、bytes/units、cursor lag、
quarantine/failure reason、generation set CAS、deletion backlog、rollback outcome。project/source identity 使用
受控 hash；日志不保存 question、raw content、secret、完整 path、thread 文本、metric artifact content。

所有 operator endpoint 要求 project/environment/operation allowlist、short-lived identity、request replay guard
和审计。dry-run、stage、activate、rollback、delete、cleanup 是六种不同权限；能 stage 的身份不自动能
activate，能 activate 的身份不自动能删除。

## 20. 停止、回退与事故规则

立即停止当前 run 的条件：

- authorization/receipt/corpus/revision/source snapshot 任一 digest 不匹配；
- 发现未授权字段、秘密、ACL widening、portable raw 泄漏；
- old active set 在 staging/validation 中发生非预期变化；
- source member drift、unknown failure、守恒失败、orphan 或 identity collision；
- checkpoint/lease fencing 失效，旧 worker 仍可写；
- deletion overdue 或 rollback 可能复活 target；
- production-mirror dry-run触及正式写端点；
- 任一来源没有足够磁盘/锁预算/恢复路径。

事故期间：冻结 activation/cleanup，保留失败代和 side-effect ledger，默认 V1 不变；若已有 opt-in V2，按
后续 release control-plane authority 降级，不由 backfill runner 自行改路由。修复必须生成新 run/revision，
不得修改失败 artifact 或直接编辑 active pointer。

## 21. Current-path resolution

### 21.1 当前已完成的原子目标

`WP-G4D-01 / T4.0.1`：完成六源 ingestion/store/runtime、generation、临时派生层、raw derivation、audit、
tombstone、cursor 与 rollback 的只读审计，冻结 MAT-01–24、authority/corpus/materialization contracts、
22 项实施序列、GM-01–54 和 G4 Gate。本结果为 `DESIGN_ENGINEERING_PASS`。

### 21.2 当前严格关键路径

```text
durable current state → resolved reviewed/admission/packet/active-task bindings
  → protected revision + same-revision remote gates + UI/independent review
  → G0 QUALIFIED + G1 Entry PASS
  → G1 binding/raw authority QUALIFIED
  → G2 raw-verified grounded answer QUALIFIED
  → G3 canonical plan/snapshot QUALIFIED
  → T4.1.1 real data authorization receipts
  → T4.1.2 exact corpus manifest
  → T4.2 shared control plane
  → T4.4 six source staging adapters
  → T4.5 reconciliation/generation set/Wiki
  → T4.6 resume/fencing
  → T4.7 deletion/rollback
  → T4.8 production-mirror dry-run
  → T4.9 independent G4 review
```

### 21.3 当前允许与禁止

当前 reviewed/pending/active/next 只从
`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md` 解析；record 缺失、无效或矛盾时使用
`CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`，不从早期 revision snapshot 恢复。

允许：执行 resolved active task 的精确 allowlist、继续 G5+ 只读设计审计和 fixture/test 计划、准备不含真实
成员的 authorization/corpus PENDING 模板。

禁止：访问正式数据库或真实源、拉取未授权语料、填写伪 owner receipt、运行正式 migration/backfill、把
临时 V2 store 标成 active generation、改变默认路由、执行 shadow/canary、删除/清理任何正式数据。

durable record 不能解锁 G4：G4 runtime 仍严格依赖 G0→G1→G2→G3 全部 qualification 与真实数据
authorization receipts。current local docs/version-control 工作不改变本规格 MAT/GM 合同、实现顺序或 Gate。

## 22. 独立评审问题

reviewer 必须逐项回答并引用 artifact path/digest：

1. authorization 是否覆盖 exact source instance、字段、时段、链接、外部模型、导出、保留和删除？
2. corpus 成员是否可枚举、root provenance 去重、困难切片足够且 portable 不泄漏？
3. 每个 derived unit 是否能回到 exact raw/version/generation/adapter/ACL？
4. 五源临时 store 是否已明确与正式持久 generation 分离？Code 是否纳入六源 set？
5. 跨库 activation 是否只切单一 generation-set pointer，失败时旧 set 是否唯一可见？
6. resume/cursor/fencing 是否能处理 crash、cancel、input drift 和 late worker？
7. coverage 是否以 expected denominator 守恒，而不是只报告 published 非空？
8. 删除是否到 raw、source index、Wiki、cache、export；SLA 与 overdue 是否可证？
9. rollback 是否验证新删除/撤权，且不会恢复被 tombstone 的对象？
10. mirror dry-run 是否证明没有正式 mutation，独立 verifier 是否能 portable replay？

任一问题无法由不可变证据回答，结论只能为 `QUALITY_HOLD` 或 `FAILED`，不能按经验判定成熟。
