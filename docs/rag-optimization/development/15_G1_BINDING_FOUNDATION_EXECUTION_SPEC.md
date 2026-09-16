# G1I-01 Raw Binding Foundation 可执行规格与迁移 Dry-run 合同

版本：2026-08-06 V2  
状态：`EXECUTION_SPEC_V2_ENGINEERING_PASS / OWNER_REVIEW_HOLD / RUNTIME_NOT_STARTED`  
上位设计：`13_G1_RAW_EVIDENCE_AUTHORITY_DESIGN.md`  
阶段目标：`14_RAG_WIKI_STAGE_OBJECTIVE_BREAKDOWN.md`  
前置门：`G0 owner-reviewed revision + clean checkout + remote CI`  
默认边界：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. 目的、完成定义与非目标

本文把 `WP-G1I-01 Binding foundation` 从方向性设计拆成可以逐项实现、失败、回滚和审计的
执行合同。它冻结以下内容：

1. V2 logical raw object、source event、binding、blocked entity 与 migration run 的表级边界；
2. ID、digest、ACL partition、时间和 JSON 的规范化算法；
3. V1→V2 的只读盘点、分类、复制、对账、shadow、切读和回滚状态机；
4. 不能从 V1 可靠恢复的历史如何 quarantine/re-ingest，禁止猜测；
5. dry-run artifact、失败码、计数守恒和测试证据；
6. 每个原子实现任务允许修改的范围、输入、验收和停止条件。

`G1I-01` 的完成不是“建出几张表”，而是：同一来源内容在不同 project/ACL 下拥有不同逻辑
authority；调用方不能伪造 digest；V1 歧义不会被静默迁移；所有读写/删除在 project、ACL、state、
version、generation 和 digest 上 fail closed；且可以在不破坏 V1 的情况下完整回滚。

本文不实现六源 selector/gateway、WikiSourceRefV2、grounded answer、planner/as-of 或生产迁移；这些
分别属于 `G1I-03–10`、G2、G3 和 owner 授权后的迁移执行。

## 2. 当前实现约束与为何不能原地修表

当前事实以以下实现为准：

- `sources/schema.py` 中 `raw_objects` 的 unique key 缺 project/source type/ACL；
- `sources/service.py` 的 raw ID 和 event idempotency 缺 project/ACL，且调用方 `content_hash` 可覆盖
  实际 bytes digest；
- `sources/store.py` 的 conflict 回读、by-id、tombstone 和 blocked entity 不是完整 project scope；
- `raw_derivations` 主键缺 generation/retrieval unit/selector；
- 当前 SQLite 初始化使用幂等 `CREATE TABLE IF NOT EXISTS`，没有可审计 migration ledger；
- 当前物理存储 path 写入 DB，并可能由 API 原样返回；
- V1 已经被 Wiki、检索和删除路径引用，原地重写 ID 会破坏历史 generation 的可解释性。

因此 G1 使用**并列表 + 复制迁移**，不 `ALTER/RENAME/DROP` V1 表，不更新 V1 主键，不把 V1
Wiki ref 原地升级。V2 reader 切换前，V1 仍是默认；V2 出现问题时只需关闭 V2 reader/dual-write，
不需要反向改写 V1 数据。

## 3. 规范化与内容身份合同

### 3.1 Canonical JSON V1

所有 authority digest 使用同一个 `canonical-json-v1`：

- 输入先经版本化 Pydantic contract 验证；未知字段拒绝；
- UTF-8、Unicode NFC、对象 key 按 Unicode code point 排序；
- 分隔符为 `,` 和 `:`，不输出多余空白，`ensure_ascii=false`，`allow_nan=false`；
- authority payload 禁止 binary float；数值使用整数或规范化十进制字符串；
- set 在进入合同前去重排序；tuple/list 保留语义顺序；
- `None` 只有字段合同明确允许时保留为 JSON `null`，不得用“缺字段”和 `null` 混写；
- timestamp 统一为 UTC RFC3339 微秒精度、`Z` 后缀；原始来源时间另存，不能覆盖 observed time；
- digest 输入带 domain separator，格式为 `UTF8(domain) + NUL + canonical_json`。

可进入 canonicalizer 的运行时值域固定为 JSON object、array、string、integer、boolean 和合同明确
允许的 `null`。`bytes`、float、Decimal、datetime、Enum、set、tuple、任意对象和 Pydantic model
都必须先由调用合同显式投影；canonicalizer 不调用 `str()`、`default=` 或隐式类型转换。object key
必须是 string；key 与 string value 都先做 NFC。若两个原 key 归一化后相同，返回
`canonical_duplicate_key`。artifact verifier 的 JSON parser 还必须在结构化解析前拒绝重复原始 key。

时间 canonical form 固定为 `YYYY-MM-DDTHH:MM:SS.ffffffZ`；输入可接受的时区格式由 Pydantic
合同解析，但 digest 只使用上述 UTC 形式。无时区、闰秒、超出 datetime 支持范围或不能无损转换的
输入返回 `timestamp_invalid`。

本文中的摘要函数精确定义为：

```text
CJSON(value) = canonical-json-v1 UTF-8 bytes
H_HEX(domain, value) = lowercase_hex(SHA256(UTF8(domain) + NUL + CJSON(value)))
H(domain, value) = "sha256:" + H_HEX(domain, value)
```

摘要字段保存 `H(...)`；带类型前缀的 ID 保存 `<prefix> + H_HEX(...)`。domain 必须为本文冻结的
ASCII literal，不能由请求提供。exact payload bytes 的 digest 不使用 canonical JSON，固定为
`"sha256:" + lowercase_hex(SHA256(payload_bytes))`。

SHA-256 字段统一为小写 `sha256:<64 hex>`。任何大小写、前缀、编码或规范化不一致都返回
`canonicalization_mismatch`，不做宽松修正。

### 3.2 Project、ACL 与 visibility partition

- `project_id` 始终参与 logical identity；即使 `acl_ref=public` 也不允许跨 project 合并 metadata；
- object authority 当前绑定一个由 server policy adapter 返回的规范化 `acl_ref`；requester ACL 是
  排序去重后的集合，只用于授权，
  不进入 object identity；
- `visibility_partition_sha256 = H("visibility-partition-v2", [project_id, acl_ref])`；
- 不允许调用方直接声明 visibility digest；service 从 project/ACL policy 计算并比较；
- 物理 blob 可跨 project 按 bytes digest 去重，但 blob 表不含 source identity、ACL 或 tombstone；
- public API 不返回 raw blob key、storage root、ACL 集或其他 project 的内容相等性信息。

`visibility_partition_sha256` 是单个 logical object 的 project + ACL partition，不等于 Wiki/request
scope 对 requester ACL 集计算的 visibility digest。二者必须使用不同 domain/version，禁止字段复用。
`project_id`、`acl_ref`、source/version identity 统一为 NFC、非空、无 C0 control、长度受限的 opaque
identifier；source URI、absolute path、display name 不得作为这些字段的替代值。

### 3.3 固定 ID 算法

```text
blob_sha256 = SHA256(exact payload bytes)

logical_identity_sha256 = H("raw-logical-identity-v2", {
  project_id, source_domain, source_type, source_instance_id,
  source_object_id, source_version, stable_version,
  raw_content_sha256, acl_ref, visibility_partition_sha256
})
raw_object_id = "raw-v2:" + logical_identity_sha256.hex

event_idempotency_sha256 = H("source-event-idempotency-v2", {
  project_id, source_domain, source_type, source_instance_id,
  source_object_id, source_version, event_type,
  raw_content_sha256, acl_ref, visibility_partition_sha256, mutation_id
})
event_id = "source-event-v2:" + event_idempotency_sha256.hex

selector_sha256 = H("raw-selector-v2", selector_contract)

binding_sha256 = H("raw-evidence-binding-v2", {
  project/source/raw/version/generation/derived/retrieval identities,
  selector_sha256, raw_content_sha256, selected_content_sha256,
  parser_artifact_sha256, valid_from, valid_to, observed_at,
  acl_ref, visibility_partition_sha256, adapter/schema versions
})
locator_id = "raw-locator-v2:" + binding_sha256.hex
```

上式中的 `.hex` 表示去掉 `sha256:` 后的 64 位 lowercase hex；它不是语言对象属性。对
`reference_only`，`raw_content_sha256` 和 `blob_sha256` 均为 JSON `null`，其 logical/event identity
仍由 project、source identity、version、ACL、visibility 和 mutation identity 唯一确定。取得 bytes
时必须创建新的 object/event；不得在原 reference-only row 上补 digest 并改变其身份。

`source_domain` 与当前 V1 `source_type` 的默认映射冻结如下；任何未列来源必须由受审 adapter 注册，
不得按名称猜测：

| V1/source adapter type | V2 source_domain | 默认处置 |
|---|---|---|
| `git` | `code` | 可进入分类 |
| `codex` | `codex` | 可进入分类 |
| `document` | `document` | 可进入分类 |
| `mlflow` | `experiment` | 可进入分类 |
| `notebook` | `notebook` | 可进入分类 |
| `workspace` | `workspace` | 仅新 V2 event；V1 当前不存在 |
| `dvc` / `manual` / unknown | 无默认映射 | `owner_mapping_required` 或 re-ingest |

`source_instance_id` 是 adapter 输出的 server-side opaque ID，不得直接使用 URL/absolute path；
`stable_version` 也只能由已注册 adapter 从 source version 产生。二者缺失时不能创建 active V2 object。

`mutation_id` 对 Workspace 必填；其他可重放来源由 adapter 生成稳定 mutation/event identity。若来源
无法提供稳定 mutation identity，event 必须显式标记 `non_idempotent_rejected`，不能退回随机 UUID
后声称可重放。

### 3.4 状态与证据资格

| object state | blob | 可建 binding | 可 raw read | 说明 |
|---|---|---:|---:|---|
| `active` | 必须 available | 是 | 是 | 唯一正常证据态 |
| `reference_only` | 必须为空；digest/length 为 null | 否 | 否 | 只有 URI/ref，未取得 bytes |
| `quarantined` | 可隔离保存但不可普通读取 | 否 | 否 | secret/integrity/policy finding |
| `tombstoned` | 可按 retention 保留 | 否 | 否 | binding 必须失效 |
| `corrupt` | 不可信 | 否 | 否 | digest/length/storage 不一致 |

`active` 只表示 bytes 可校验，不等于 `OBSERVED/VERIFIED`；后者还需要 binding/selector 和事实状态
政策。`reference_only` 不能通过“读取最新 URI”自动转为 active，必须产生新的 V2 object/event。

## 4. V2 SQLite 逻辑 Schema

以下是实施时必须等价满足的逻辑 DDL。它是设计合同，不在 G0 HOLD 期间执行。字段长度上限同时
在 Pydantic 和 service 层验证；SQLite `CHECK` 是第二道防线。

### 4.1 `raw_blobs_v2`

```sql
CREATE TABLE raw_blobs_v2 (
  blob_sha256 TEXT PRIMARY KEY,
  byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
  storage_key TEXT,
  storage_state TEXT NOT NULL
    CHECK(storage_state IN ('available','quarantined','missing','corrupt')),
  first_verified_at TEXT,
  last_verified_at TEXT,
  created_at TEXT NOT NULL,
  CHECK(
    (storage_state='available' AND storage_key IS NOT NULL)
    OR storage_state!='available'
  )
);
```

`storage_key` 只能是受控 root 下的相对内容地址 key，如 `ab/cd/<digest>`；禁止绝对 path、`..`、
URL、symlink alias。root 仅来自 server config。写入采用同 root 临时文件、fsync、atomic rename；DB
失败后的 orphan blob 由只按 digest 工作的 GC 处理，不能自动删除仍被任何 V2 object 引用的 blob。

### 4.2 `raw_objects_v2`

```sql
CREATE TABLE raw_objects_v2 (
  raw_object_id TEXT PRIMARY KEY,
  logical_identity_sha256 TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL,
  source_domain TEXT NOT NULL
    CHECK(source_domain IN ('code','codex','experiment','notebook','document','workspace')),
  source_type TEXT NOT NULL,
  source_instance_id TEXT NOT NULL,
  source_object_id TEXT NOT NULL,
  source_version TEXT NOT NULL,
  stable_version TEXT NOT NULL,
  acl_ref TEXT NOT NULL,
  visibility_partition_sha256 TEXT NOT NULL,
  raw_content_sha256 TEXT,
  blob_sha256 TEXT,
  media_type TEXT NOT NULL,
  byte_length INTEGER CHECK(byte_length >= 0),
  state TEXT NOT NULL
    CHECK(state IN ('active','reference_only','quarantined','tombstoned','corrupt')),
  adapter_version TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  tombstoned_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(project_id, raw_object_id),
  FOREIGN KEY(blob_sha256) REFERENCES raw_blobs_v2(blob_sha256),
  CHECK(
    (state='active' AND blob_sha256 IS NOT NULL
                    AND raw_content_sha256 IS NOT NULL AND byte_length IS NOT NULL)
    OR state!='active'
  ),
  CHECK(
    (state='reference_only' AND blob_sha256 IS NULL
                            AND raw_content_sha256 IS NULL AND byte_length IS NULL)
    OR state!='reference_only'
  ),
  CHECK(
    blob_sha256 IS NULL OR raw_content_sha256 IS NULL
    OR blob_sha256=raw_content_sha256
  )
);
CREATE INDEX idx_raw_v2_lookup
  ON raw_objects_v2(project_id, source_domain, source_instance_id,
                    source_object_id, source_version, state);
CREATE INDEX idx_raw_v2_visibility
  ON raw_objects_v2(project_id, visibility_partition_sha256, state, observed_at);
```

`metadata_json` 只允许 versioned allowlist projection，禁止保存 raw content、secret、absolute path 或
未脱敏 URI。`stable_version` 与 source_version 的映射由 per-source adapter 注册，不能由 API 用户
任意声明。

### 4.3 `source_events_v2`

```sql
CREATE TABLE source_events_v2 (
  event_id TEXT PRIMARY KEY,
  idempotency_sha256 TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL,
  source_domain TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_instance_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  mutation_id TEXT NOT NULL,
  source_object_id TEXT NOT NULL,
  source_version TEXT NOT NULL,
  raw_object_id TEXT,
  raw_content_sha256 TEXT,
  acl_ref TEXT NOT NULL,
  visibility_partition_sha256 TEXT NOT NULL,
  event_time TEXT,
  observed_at TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  status TEXT NOT NULL
    CHECK(status IN ('persisted','reference_only','quarantined','rejected','tombstone')),
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK(
    (status IN ('persisted','quarantined') AND raw_content_sha256 IS NOT NULL)
    OR (status='reference_only' AND raw_content_sha256 IS NULL)
    OR status IN ('rejected','tombstone')
  ),
  FOREIGN KEY(project_id, raw_object_id)
    REFERENCES raw_objects_v2(project_id, raw_object_id)
);
CREATE INDEX idx_events_v2_project_time
  ON source_events_v2(project_id, observed_at, event_id);
CREATE INDEX idx_events_v2_object
  ON source_events_v2(project_id, source_domain, source_instance_id,
                      source_object_id, source_version);
```

重复 idempotency 只有在 canonical event payload 逐字段相等时返回既有 event；相同 key、不同 payload
是 `idempotency_payload_conflict`，不得伪装为成功 duplicate。

### 4.4 `raw_evidence_bindings_v2`

```sql
CREATE TABLE raw_evidence_bindings_v2 (
  locator_id TEXT PRIMARY KEY,
  binding_sha256 TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL,
  source_domain TEXT NOT NULL,
  raw_object_id TEXT NOT NULL,
  source_version TEXT NOT NULL,
  stable_version TEXT NOT NULL,
  generation_id TEXT NOT NULL,
  derived_entity_id TEXT NOT NULL,
  retrieval_unit_id TEXT NOT NULL,
  derivation_kind TEXT NOT NULL,
  derivation_version TEXT NOT NULL,
  selector_kind TEXT NOT NULL,
  selector_json TEXT NOT NULL,
  selector_sha256 TEXT NOT NULL,
  raw_content_sha256 TEXT NOT NULL,
  selected_content_sha256 TEXT NOT NULL,
  parser_artifact_sha256 TEXT,
  acl_ref TEXT NOT NULL,
  visibility_partition_sha256 TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  observed_at TEXT NOT NULL,
  binding_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  invalidated_at TEXT,
  invalidation_reason TEXT,
  FOREIGN KEY(project_id, raw_object_id)
    REFERENCES raw_objects_v2(project_id, raw_object_id),
  CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_from < valid_to)
);
CREATE INDEX idx_bindings_v2_entity
  ON raw_evidence_bindings_v2(project_id, derived_entity_id,
                              generation_id, invalidated_at);
CREATE INDEX idx_bindings_v2_unit
  ON raw_evidence_bindings_v2(project_id, retrieval_unit_id,
                              generation_id, invalidated_at);
CREATE INDEX idx_bindings_v2_raw
  ON raw_evidence_bindings_v2(project_id, raw_object_id, invalidated_at);
```

G1 新 binding 的 `generation_id`、`retrieval_unit_id` 均必填。历史来源没有 generation 时不能写空值
冒充 V2；必须先生成 governed migration generation。一个 entity 多 binding 合法；resolve 请求不足以
唯一定位时返回 `binding_ambiguous`，禁止按 created/observed time 取最新。

### 4.5 `blocked_entities_v2` 与 migration ledger

```sql
CREATE TABLE blocked_entities_v2 (
  project_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  raw_object_id TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  blocked_at TEXT NOT NULL,
  actor_digest TEXT NOT NULL,
  PRIMARY KEY(project_id, entity_id),
  FOREIGN KEY(project_id, raw_object_id)
    REFERENCES raw_objects_v2(project_id, raw_object_id)
);

CREATE TABLE raw_migration_runs_v2 (
  migration_run_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL CHECK(mode IN ('fixture','mirror','authorized-production')),
  source_revision_sha256 TEXT NOT NULL,
  source_schema_sha256 TEXT NOT NULL,
  plan_sha256 TEXT NOT NULL,
  status TEXT NOT NULL
    CHECK(status IN ('planned','scanning','classified','copied','verified',
                     'shadow_ready','failed','rolled_back')),
  checkpoint_json TEXT NOT NULL,
  counters_json TEXT NOT NULL,
  findings_sha256 TEXT,
  report_sha256 TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE raw_migration_findings_v2 (
  migration_run_id TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('P0','P1','P2')),
  category TEXT NOT NULL,
  source_row_fingerprint TEXT NOT NULL,
  resolution TEXT NOT NULL
    CHECK(resolution IN ('reingest_required','owner_mapping_required',
                         'repair_required','safe_to_copy','excluded_by_policy')),
  detail_digest TEXT NOT NULL,
  PRIMARY KEY(migration_run_id, finding_id),
  FOREIGN KEY(migration_run_id) REFERENCES raw_migration_runs_v2(migration_run_id)
);
```

findings 表不保存 raw payload、secret、storage path、source URI 或 ACL 明文；可审计详情进入受控、
脱敏、内容寻址 artifact。

### 4.6 字段大小与 JSON 边界

实现不得自行选择无界字段。V2 默认上限冻结为：portable ID 240 UTF-8 bytes；source object ID
2,000 bytes；media type 200 bytes；trace/mutation ID 240 bytes；reason/derivation/adapter/schema version
160 bytes；单个 metadata/selector/binding/checkpoint JSON canonical bytes 64 KiB；public selected output
1 MiB，来源特定更小上限优先。超限在文件或事务 I/O 前返回 `contract_size_exceeded`。

`metadata_json`、`selector_json`、`binding_json` 和 ledger JSON 都必须由 frozen Pydantic projection
生成并以 canonical-json-v1 保存；读取时 strict parse、重新 canonicalize 并比较。每个 source 的
metadata allowlist 在对应 gateway 任务中版本化；allowlist 未注册时只能 `reference_only` 或拒绝，
不能把任意 request metadata 透传到 V2 authority。

## 5. 事务与并发不变量

### 5.1 Writer

1. service 编码 canonical payload 并自行计算 bytes digest；
2. expected digest 若存在必须精确相等，否则在写文件/DB 前失败；
3. canonicalize project/source/ACL/version，计算 visibility 和 logical identity；
4. 写临时 blob、校验 length/digest、fsync、atomic rename；
5. 单个 `BEGIN IMMEDIATE` 内 upsert blob metadata、insert exact raw object、insert exact event；
6. conflict 时读取同 project + logical identity，并逐字段比较；任何不同返回 typed conflict；
7. transaction 失败不返回 object/event ID；orphan blob 只由安全 GC 处理。

复用已存在 storage key 前必须在受控 root 下逐级拒绝 symlink，要求 regular file，并重新校验 length
和 digest；不一致时标记/报告 corrupt，不覆盖原文件。空 bytes 是合法 active payload，digest/length
必须分别为 SHA-256(empty) 与 `0`，不能与 reference-only 混淆。

### 5.2 Binding create

- 在同一事务重新读取 project-scoped raw object，要求 state=active、blob available；
- generation/retrieval unit/derived entity 的 project、ACL 和 source identity 必须与 object 相等；
- selector 在写 binding 前以受控 reader 执行一次，复核 raw/selected digest；
- 相同 binding digest 是幂等成功；相同 locator ID、不同 binding 是不可恢复冲突；
- 不允许先写 binding 后异步补 selected digest。

### 5.3 Tombstone/invalidation

单事务顺序固定为：锁定 exact `(project_id, raw_object_id)` → object tombstoned → active bindings
invalidated → `(project_id, entity_id)` blocked/upsert → tombstone event → audit。Reader 即使已取得 binding，
在返回 selected bytes 前仍须重查 object state 或使用同一一致性快照；并发 tombstone 不能泄漏旧内容。

### 5.4 Reader cutover

Reader 模式只有 `v1`、`v2-shadow`、`v2-required`：

- `v1`：现有行为，不能声称 strict raw authority；
- `v2-shadow`：V1 返回不变，V2 只产生 digest/typed comparison，不返回内容给产品；
- `v2-required`：只接受 V2 binding；失败即 unavailable，不回退 V1/Wiki snippet。

禁止“V2 失败则 V1 成功”的隐式兼容模式。模式必须绑定 reviewed config/revision，不能由普通请求
参数切换。

## 6. V1→V2 迁移状态机

```text
M0 FREEZE_INPUT
  → M1 SCHEMA_PREPARED
  → M2 V1_SCANNED
  → M3 CLASSIFIED
  → M4 SAFE_ROWS_COPIED
  → M5 COUNTS_AND_DIGESTS_VERIFIED
  → M6 DUAL_WRITE_DARK
  → M7 V2_SHADOW_READ
  → M8 OWNER_CUTOVER_DECISION
```

任一阶段失败只允许进入 `FAILED` 或回到前一阶段；不得跳级。M0–M5 可在 fixture/mirror dry-run
执行；M6–M8 必须等待 G0、owner design review、备份恢复演练和独立 Gate。

### M0：冻结输入

- 记录受审 revision、DB logical schema digest、SQLite version/user_version、WAL 状态和 fixture/mirror
  身份；
- 正式库只有 owner 授权后才可读；没有授权时只运行 fixture 或脱敏 mirror；
- 禁止读取 raw payload 到报告；只计算 DB row fingerprint 和受控 blob digest；
- 捕获开始/结束水位，水位变化则本次 scan 失效。

### M1：准备并列表

- 所有 V2 DDL 在一个事务创建；
- 对 `sqlite_master` 规范化 SQL 复算 schema digest；
- 初始化 migration run，不添加 V1 trigger，不切换 reader；
- DDL 或 digest 不一致则整体回滚。

### M2–M3：扫描与分类

每个 V1 raw/event/derivation/blocked row 只进入一个分类：

| 分类 | 条件 | 处置 |
|---|---|---|
| `safe_copy` | project/ACL/source/version/blob/digest/generation 可唯一证明 | 进入 M4 |
| `reference_only` | 无可校验 bytes | 保留导航元数据；不可 binding |
| `quarantined_existing` | V1 已 quarantined | 不普通读取；按 owner policy re-ingest |
| `storage_invalid` | missing/outside root/symlink/not regular/length/digest mismatch | repair/re-ingest |
| `project_acl_ambiguous` | row/event/derived project 或 ACL 不一致/缺失 | owner mapping 或 re-ingest |
| `generation_ambiguous` | derivation 无唯一 governed generation | re-index/re-ingest |
| `collision_evidence` | V1 event/derived usage显示同 logical identity 被跨 project/ACL 复用 | P0，禁止复制 |
| `history_unrecoverable` | V1 global UNIQUE 已吞掉后写尝试，现有数据无法重建缺失事件 | 明确记录不可恢复；从 source re-ingest |

特别约束：V1 global UNIQUE 可能已经丢失第二个 project 的写入意图。扫描“未发现冲突”不能证明
历史没有冲突；只有可追溯到原 source manifest/event 且 project/ACL 唯一的对象才可 `safe_copy`。
其他对象必须由来源重新摄取，不能用当前 row 猜测缺失 owner。

### M4：复制安全行

- 按稳定 `(project_id, V1 raw id)` 顺序分页；checkpoint 只记录最后 fingerprint，不记录内容；
- V2 logical ID 从事实字段重新计算，绝不沿用 V1 raw ID；
- blob 重新读取并校验，不信任 V1 `content_hash/byte_length`；
- V1 event 只有 project/ACL/raw identity 全相等且 idempotency payload 可重建时复制；
- V1 derivation 只有 entity project 和 generation 可证明时转换为 whole-object binding candidate；没有
  selector/selected digest 的仍不得生成 final binding；
- 每批独立事务、重跑幂等；相同 ID 不同 payload 立即 P0 fail。

### M5：守恒与完整性验证

至少证明：

```text
v1_raw_total = safe_copy + reference_only + quarantined + storage_invalid
             + project_acl_ambiguous + generation_ambiguous
             + collision_evidence + history_unrecoverable

v2_object_total = copied_safe_rows
active_v2_objects = available_verified_blobs
binding_total = selector_verified_rows
unclassified = 0
cross_project_metadata_reuse = 0
active_without_blob = 0
digest_mismatch = 0
public_path_or_payload_ref_exposure = 0
```

所有计数按 project/source/state 分层，且明细集合的 canonical digest 与聚合计数绑定。P0 finding、
unclassified 或守恒不等式均使 run 为 FAILED。

### M6–M8：双写、shadow 与切换

- dual-write 以 V1 成功为产品行为，V2 失败必须记录并阻止该来源进入 V2 qualification；不能悄悄
  丢弃失败；
- shadow 对相同请求比较 object/binding/state/digest/reason，不比较路径或非规范化时间；
- shadow mismatch 按 project/source/reason 聚合，任何 ACL/state/version 不一致为 P0；
- cutover 只允许按 project/source/intent 白名单，默认 V1 保持；
- G1 结束也只授权 V2 raw foundation，不授权 Wiki/Answer 默认切换。

## 7. Dry-run artifact 合同

artifact schema：`rag-g1-raw-migration-dry-run-v1`。内容至少包含：

```text
schema_version / artifact_version / status
mode = fixture|mirror|authorized-production
production_database_accessed
source_revision_sha256
source_schema_sha256 / target_schema_sha256 / plan_sha256
input_watermark_start / input_watermark_end / stable_input
table_counts_before / classification_counts / projected_counts_after
counts_by_project_source_state
invariant_results[] = {id, passed, observed, expected}
finding_counts_by_severity_category
finding_set_sha256
reingest_required_counts
owner_mapping_required_counts
checkpoint_sha256
commands[] = {id, argv_template, exit_code, stdout_sha256, stderr_sha256}
limitations[] / blockers[] / rollback_ready
content_sha256
```

禁止字段：absolute DB/storage path、source URI、ACL 明文集合、raw bytes、selected text、secret、SQL row
dump、用户名/主机名。项目和来源的细分键使用受控 logical ID 或带 artifact salt 的 digest；salt 只在
受控审计包中保存。

状态只允许：

- `DRY_RUN_PASS`：全部守恒和 hard invariant 通过，P0=0；不等于可 cutover；
- `DRY_RUN_HOLD`：可分类但需 owner mapping/re-ingest；
- `DRY_RUN_FAIL`：schema/input/digest/ACL/state/守恒失败；
- `NOT_AUTHORIZED`：尝试读取未授权正式库，在 I/O 前拒绝。

artifact verifier 必须支持 canonical verify、copy verify、tamper negative、输入 snapshot compare，且
默认 verify-only 不连接任何数据库。

### 7.1 Typed reason code registry

G1I-01 基础层至少冻结以下内部 reason；对外仍统一投影为 `evidence_unavailable` 或受控 4xx，不泄漏
对象存在性：

```text
canonicalization_mismatch
canonical_duplicate_key
timestamp_invalid
contract_size_exceeded
expected_digest_mismatch
visibility_mismatch
source_adapter_unregistered
stable_version_unavailable
non_idempotent_rejected
idempotency_payload_conflict
logical_identity_payload_conflict
reference_only_unavailable
binding_not_found
binding_ambiguous
binding_digest_mismatch
raw_state_invalid
blob_unavailable
blob_corrupt
selector_invalid
selected_digest_mismatch
project_mismatch
acl_denied
migration_not_authorized
migration_input_changed
migration_unclassified
migration_count_mismatch
```

后续 shared reader 的 storage/parse/source-specific reason 继续以 `13` 文档 §5.3 为准；同一语义不得
换字符串。异常类型、HTTP 状态、audit severity 与 reason 的映射属于 T1.1.1 合同 fixture，不能在
router 中临时决定。

## 8. 原子实施任务

| ID | 单一结果 | 允许变更 | 必须验证 | 停止条件 |
|---|---|---|---|---|
| T1.1.1 | canonical JSON/digest/ID/reason 合同 | 新 `sources/v2/contracts.py` + tests | round-trip、Unicode/NFC collision、null、float/NaN、duplicate key、domain separation、event ID | 任一运行位置/locale 影响 digest |
| T1.1.2 | ACL/visibility contract | contracts + project policy adapter | project/public/private/cross-project matrix | requester 可声明 partition |
| T1.1.3 | V2 schema 独立初始化 | 新 `sources/v2/schema.py` | exact sqlite_master、FK/check/index、re-init | 修改/删除 V1 表 |
| T1.1.4 | blob/object store | 新 V2 store | project collision、physical dedupe、exact conflict | active 无 verified blob |
| T1.1.5 | event store | V2 store | cross-project idempotency、same-key payload conflict | duplicate 掩盖不同 payload |
| T1.1.6 | binding store | V2 store | exact resolve、multi-binding ambiguity、generation scope | 以 latest 消歧 |
| T1.1.7 | project tombstone | V2 store/service | atomic invalidation/block/event、concurrent read | 跨 project block 或旧 binding 可读 |
| T1.1.8 | public projection | V2 models/router | path/ref/URI/secret/ACL side-channel=0 | store row 直接出站 |
| T1.1.9 | migration scanner | 新 `migration_v1.py` | fixture-only default、stable watermark、全分类 | 未授权正式 DB I/O |
| T1.1.10 | safe copier/checkpoint | migration | batch retry、same/different payload conflict | 覆盖 V1 或非幂等重跑 |
| T1.1.11 | reconciliation/verifier | migration/evaluation | 守恒、P0、tamper/copy verify | unclassified > 0 |
| T1.1.12 | dual-write/shadow config | config/runtime，仅 owner 解锁后 | default off、reviewed scope、typed mismatch | request 可开关或默认变化 |
| T1.1.13 | rollback drill | fixture/mirror | disable reader/write、drop V2 fixture、V1 unchanged | 需反写 V1 才能恢复 |
| T1.1.14 | independent review | docs/artifact | requirement→test→artifact trace 100% | 自评替代 Gate review |

一次变更最多关闭一个表格主结果。`T1.1.1–8` 可在同一 feature branch 顺序实现，但不得把 migration
cutover、六源 gateway 或 Wiki active pointer 混入。

## 9. Requirement→Test→Evidence 矩阵

| Req | 需求 | 最低测试 | 证据 |
|---|---|---|---|
| BF-01 | project/ACL 参与 raw identity | 同 bytes/source 跨 2 project × 3 ACL | distinct IDs + no metadata reuse |
| BF-02 | bytes digest 不可声明覆盖 | expected correct/wrong/empty/large | pre-write reject + no row/blob |
| BF-03 | reference-only 不可 binding | URI/ref variants | typed unavailable |
| BF-04 | event idempotency project-scoped | same mutation cross project；same key diff payload | separate events/conflict |
| BF-05 | API 无内部路径 | create/list/by-id/tombstone/error | serialized-field allowlist |
| BF-06 | exact binding resolve | entity/unit/generation permutations | unique or ambiguous，不 latest |
| BF-07 | raw/selected digest 双校验 | blob/selector/parse tamper | typed fail before output |
| BF-08 | tombstone 原子传播 | reader/tombstone race | no post-tombstone bytes |
| BF-09 | blocked entity project scope | same entity ID cross project | only target project blocked |
| BF-10 | V1 数据全分类 | every category fixture | count conservation/unclassified=0 |
| BF-11 | 不可恢复历史不猜测 | simulated swallowed collision | reingest_required |
| BF-12 | dry-run 默认无正式 I/O | missing authority/production marker | NOT_AUTHORIZED before connect |
| BF-13 | migration 可重入 | crash each batch boundary | same final digests/counts |
| BF-14 | rollback 不改 V1 | fail after each M1–M7 stage | V1 schema/data digests equal |
| BF-15 | portable artifact | relocated checkout + copied artifact | same canonical verification |

每条测试必须同时断言无额外 row/blob/audit 泄漏。只断言 exception 类型而不检查副作用不足以关闭
安全需求。

## 10. Rollback 与恢复

### 10.1 M1–M5

- reader/writer 未使用 V2，rollback 只删除本次 migration run 创建的 fixture/mirror V2 rows；
- 正式环境不自动 drop 表，标记 run `rolled_back` 并停止后续写入；
- V1 schema/data digest 必须与 M0 相等；
- blob GC 只删除 `raw_blobs_v2` 无引用且由本 run 新建的 key，并再次校验 root/regular/no-symlink。

### 10.2 M6 dual-write

- 首先关闭 V2 write flag，V1 产品行为不变；
- 保留 V2 mismatch artifact，不补写/覆盖失败 row；
- 受影响 source/project 必须重新从 checkpoint 或 source re-ingest；
- V2 写失败率、P0 mismatch 或 audit 丢失任一超过 0，禁止进入 shadow-ready。

### 10.3 M7 shadow/read

- 关闭 V2 shadow/read，V1 指针不变；
- 已创建 V2 binding 不获得产品 authority；
- 若发生 unauthorized output，按安全事件处理，不能仅视为测试失败；
- 重新开启前需新的 reviewed artifact 和 revision。

## 11. Owner/Architecture 必须明确决定的五点

| 决策 | 推荐默认 | 不决定的影响 |
|---|---|---|
| D1 V2 并列表而非原地改 V1 | 接受并列表 | 无安全 rollback |
| D2 project-scoped public object | public 仍 project 隔离 | 暴露跨项目内容相等性/metadata |
| D3 V1 不可恢复历史 | re-ingest，不猜测 | 伪造 owner/ACL/generation |
| D4 storage key | DB 仅相对内容地址 key，root 配置化 | path 出站和 root 搬迁失败 |
| D5 cutover 粒度 | project + source + intent 白名单 | 无法局部回滚 |

任何决策若偏离推荐默认，必须新增 ADR、威胁模型、负向测试和 rollback 证明；不能只修改 schema
文字。

## 12. Entry、Exit 与当前下一步

### Entry

实施 T1.1.1 前必须同时满足：

1. G0 达到 `QUALIFIED`：owner-reviewed revision 已形成，且未接受的版本、生成物、供应链 blocker
   均为 0；受限例外必须有 owner、expiry、mitigation 和禁止发布阶段；
2. 同 revision fresh checkout 的 Python 3.12/3.13 CI 通过；
3. `web/index.html` 等生成物版本边界关闭；
4. owner/architecture 明确 D1–D5；
5. 默认 V1、production authorization 和正式 DB 写入权限均不改变；
6. `28_G1_ENTRY_DECISION_AND_REQUIREMENT_TRACE_PACKET.md` 的 E-01–10 全部 PASS，Entry Packet 由
   外部可验证 owner/architecture/security/data attestation 支撑；仓库内自填 approval 无效。

此外须以 `27_G1_BINDING_FOUNDATION_IMPLEMENTATION_READINESS_AUDIT.md` 的 readiness checklist 为
准；其结论只能收紧本 Entry，不能绕过上述六项。

### Exit

`G1I-01` 只有 BF-01–15 全部有测试与 artifact、fixture/mirror migration dry-run 可重入、rollback
不改变 V1、P0=0、独立 review PASS 才为 `ENGINEERING_PASS`。这仍不等于 G1 `QUALIFIED`；后续还
需 shared secure reader、六源 gateway、Wiki V2、E2E 与 G1 Gate。

### 当前下一步

当前只完成本文设计冻结，不创建 V2 表、不连接正式 DB、不修改 runtime。外部下一步仍是 owner
关闭 G0 version admission；解锁后的唯一代码目标是 `T1.1.1 canonical contract`，不是六源 gateway
或 Wiki 字段。
