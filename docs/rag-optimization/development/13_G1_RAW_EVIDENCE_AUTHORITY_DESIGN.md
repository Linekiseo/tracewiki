# G1 六源原始证据权威详细设计

版本：2026-08-06 V2  
状态：`DESIGN_V2_ENGINEERING_PASS / OWNER_REVIEW_HOLD / IMPLEMENTATION_BLOCKED_BY_G0_VERSION_ADMISSION`  
执行总纲：`10_RAG_WIKI_MATURITY_PROGRAM.md`  
执行台账：`11_RAG_WIKI_MATURITY_EXECUTION_LEDGER.md`  
Binding foundation 执行规格：`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md`  
实施就绪审计：`27_G1_BINDING_FOUNDATION_IMPLEMENTATION_READINESS_AUDIT.md`  
前置 Gate：`G0.2 owner-reviewed revision + clean checkout + remote CI`  
默认运行：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. 目的与不可妥协边界

G1 的唯一目的，是让任何被称为“已验证”的 Wiki/Answer 事实都能回到六类来源中某个
不可变原始对象或由该对象可确定性重建的精确片段。Wiki 是派生组织层，不是事实原件。

以下对象一律不能单独充当 raw authority：

- `MultiSourceCandidateV2.title/snippet`；
- `wiki_source_candidates_v1.candidate_json`；
- Wiki page、fact、link 或摘要文本；
- 重新执行一次可变搜索得到的相似结果；
- 未绑定 parser/adapter/version/input digest 的派生数据库行；
- LLM 摘要、reranker 分数、融合分数或人工改写的 citation label。

允许成为 raw authority 的对象只有：

1. `raw_objects_v2` 中 `active`、project/ACL/visibility 匹配、受控 blob bytes digest 复核成功的对象；
2. 由上述 bytes 经冻结的 deterministic selector/adapter 得到、且 selected digest 一致的片段；
3. 对二进制文档，由原始 bytes 与冻结 parser environment 生成并内容寻址的 parse artifact；
4. Workspace 新增的 append-only canonical state event，且与正式状态版本绑定。

G1 不改变默认 V1，不授权生产，不修改既有 Wiki generation，也不把历史 V1 source-ref
原地升级成“已验证”。

## 2. 当前实现审查结论

### 2.1 已有基础

- `raw_objects` 已记录 project/source/object/version/hash/media/path/ACL/state/adapter/schema/time；
- `source_events` 已记录来源事件和 `raw_object_id`；
- `raw_derivations` 已支持 raw object 到 derived entity 的 generation/version 关联与失效；
- tombstone 会将 raw object 置为 tombstoned、失效 derivation 并加入 blocked entities；
- Code、Codex、Experiment、Notebook、Document 的 ingestion 已产生部分 raw object 和 derivation；
- Notebook runtime 已实现 managed-root、absolute path、symlink、regular-file、digest 校验；
- Code V2 unit metadata 已保留 `raw_object_id`、snapshot raw ID 和精确 line/byte range。

### 2.2 P0 缺口

| 缺口 | 当前事实 | 风险 |
|---|---|---|
| Wiki 假 raw read | `StoreRawEvidenceGatewayV1` 读取 WikiStore candidate，再由 `CandidateRawEvidenceReaderV1` 还原 snippet | 只能证明候选快照自洽，不能证明来源事实 |
| raw digest 含义错误 | `WikiSourceRefV1.raw_content_sha256` 实际是整个 candidate envelope digest | 字段名与安全含义不一致 |
| 无统一安全读接口 | `RawSourceService` 只写；Notebook 私有函数单独读 bytes | 其他来源容易绕过 project/ACL/path/digest 检查 |
| derivation 不能精确反查 | store 没有按 derived entity + generation 解析有效 derivation 的 API | gateway 可能猜测或跨 generation 绑定 |
| selector 未持久化 | `raw_derivations` 没有 line/JSON pointer/cell/page/table selector | 只能回读整对象，无法证明 claim 对应片段 |
| Workspace 无 raw event | `SourceEventInput.source_type` 不含 workspace，Workspace mutation 未写 raw object | 第六源不存在 raw authority |
| Live Wiki 全部派生 | organizer 统一写 `derived_fact/deterministic_derived` | strict claim authority 永远不成立 |
| 历史 ref 无法升级 | V1 ref 不含 raw object/binding/selected digest | 不能安全推断或静默补齐 |
| raw identity 跨项目碰撞 | `raw_objects` unique key 和 raw ID 均不含 project/source type/ACL | 同一 source identity 可让后写项目拿到先写项目的 metadata/ACL |
| event 幂等跨项目碰撞 | idempotency hash 不含 project/source type/ACL | 不同项目事件可能被错误去重并返回另一项目事件 |
| raw API 泄露内部路径 | ingest/list/by-id 直接返回 `storage_path`，event 返回 `payload_ref`；后者默认也是 storage path | 暴露服务器文件系统结构，且 public object 缺 project scope |
| write digest 可由调用者声明 | `persist_bytes(content_hash=...)` 未验证声明 digest 与 payload bytes 一致 | 错误或恶意内部调用可写入伪内容地址对象 |
| reference-only 被标 active | `payload_ref` 请求会生成无 storage bytes 的 active raw object | active 被误解为可回读内容权威 |
| blocked entity 非项目化 | `blocked_entities.entity_id` 为全局主键，stats 也未按 project 过滤 | 非全局唯一 entity ID 会跨项目阻断或泄露计数 |
| derivation generation 不在主键 | 既有 derivation PK 不含 generation，冲突时 `DO NOTHING` | 同 raw/entity/version 跨 generation 的绑定可能保留旧 generation |

### 2.3 六源当前可用性

| 来源 | 当前 raw 根 | 当前 derivation | 当前精确 selector | G1 判定 |
|---|---|---|---|---|
| Code | 每文件 raw bytes + repository snapshot | 文件关联 FileVersion/CodeSymbol；V2 unit metadata 有 raw ID | V2 unit 有 line/byte range，但未形成共享 binding | 可迁移 |
| Codex | 每 item 文本；另有 thread metadata snapshot | item → raw item | item 可整对象；thread 汇总缺有序 manifest | 部分可迁移 |
| Experiment | 每 MLflow run canonical JSON | run/metric/artifact → raw run | 未保存 entity → JSON pointer | 需补 selector；experiment summary 缺 raw |
| Notebook | 整个 `.ipynb` bytes | run/cell/output → raw notebook | 可由 ordinal 建 JSON pointer，尚未持久化 | 可迁移 |
| Document | 原文 bytes | doc/section/page/table/figure/citation/claim → raw document | 解析定位存在于派生层，未形成可重放 binding | 需 parse artifact/binding |
| Workspace | 无 | 无 | 无 | 必须新增事件权威 |

## 3. 术语与事实等级

| 术语 | 精确定义 |
|---|---|
| Raw object | 来源 payload 的内容寻址不可变对象，不等同于当前正式表行 |
| Raw selector | 在 raw object 或受约束 parse artifact 上确定性选择一个片段的类型化参数 |
| Evidence binding | raw object、derived identity、generation、selector、版本和 digest 的不可变绑定 |
| Raw read | 先完成权限/状态/版本/血缘/存储校验，再读取 bytes 和应用 selector |
| Observed | 选中内容直接存在于 raw object，或可由冻结 deterministic parser 重建 |
| Verified | Observed 事实又通过独立 review/validation authority；检索分数不能产生 Verified |
| Reported | 来源明确报告但未独立验证；可引用，但不能在 strict 模式单独支撑强 claim |
| Inferred | 规则、模型、图推导或摘要；必须携带 derivation，不能冒充 raw |

“读取成功”与“事实已验证”必须分离：读取成功最多产生 `OBSERVED`；只有额外的
review/validation binding 才能提升为 `VERIFIED`。

## 4. 冻结合同：RawEvidenceLocatorV2

### 4.1 服务器内部合同

`RawEvidenceLocatorV2` 是服务器内部不可变合同；不得把 `storage_path`、本地路径、未脱敏
source URI 或 secret metadata 写入该合同或返回给客户端。

```text
RawEvidenceLocatorV2
  locator_id                     # raw-locator-v2:<sha256>
  project_id
  source_domain                  # code|codex|experiment|notebook|document|workspace
  source_type                    # raw_objects_v2.source_type
  source_instance_id             # 受控内部 identity；非展示 URI
  raw_object_id
  source_object_id
  source_version
  stable_version
  generation_id                  # G1 新 binding 必填；历史歧义必须 re-ingest
  derived_entity_id
  retrieval_unit_id              # 可为空，但 Wiki 引用必须有值
  derivation_kind
  derivation_version
  selector                       # RawEvidenceSelectorV2 discriminated union
  valid_from / valid_to
  observed_at
  acl_ref
  visibility_partition_sha256    # H(project_id, single object acl_ref)
  media_type
  schema_version
  adapter_version
  raw_content_sha256             # raw bytes/canonical record digest
  selected_content_sha256        # selector 输出 canonical bytes digest
  parser_artifact_sha256         # 仅需 parse artifact 时存在
  binding_sha256                 # 上述所有字段的 canonical digest
  locator_version=raw-evidence-locator-v2
```

约束：

- `locator_id = raw-locator-v2:<binding_sha256 去前缀>`；
- `visibility_partition_sha256` 必须由 server policy adapter 根据 project + object `acl_ref` 计算，
  不能由调用方任意传入；它不等于 Wiki/requester ACL 集的 scope visibility digest；
- locator 的 project/source/raw/version/generation 必须与 raw object 和 derivation 完全相等；
- `raw_content_sha256` 必须等于实际读取 bytes 的 SHA-256；
- `selected_content_sha256` 必须等于 selector 输出的 canonical bytes；
- `valid_to <= valid_from`、跨项目、跨 ACL、跨 generation、无效 derivation 全部拒绝；
- G1 binding 的 generation 必填；历史来源没有唯一 governed generation 时必须 re-index/re-ingest，
  不能用 null 或 latest 冒充 V2；
- locator 只描述证据，不携带 relevance、rank、confidence 或答案文本。

### 4.2 Selector discriminated union

| selector kind | 字段 | 适用来源 |
|---|---|---|
| `whole_object_v2` | 无额外字段 | Codex item、Workspace event、小型 canonical record |
| `utf8_range_v2` | start/end byte、start/end line、newline policy | Code |
| `json_pointer_v2` | RFC 6901 pointer、canonical JSON policy | Experiment、Notebook、Workspace |
| `notebook_cell_v2` | cell index、stable cell id、source/output ordinal | Notebook |
| `document_span_v2` | page、section path、char/token span、parse artifact digest | Document prose/citation |
| `document_table_v2` | page、table id、row/column semantic key、parse artifact digest | Document table |
| `document_figure_v2` | page、figure id、caption/region、parse artifact digest | Document figure |
| `manifest_member_v2` | member identity、member digest、ordinal | Codex thread aggregate、复合来源 manifest |

任何 selector 必须边界检查、确定性序列化并生成自身 digest。禁止 XPath/正则/任意脚本、网络
URL、文件系统 path 或调用方提供的 executable selector。

### 4.3 持久化边界

不修改既有 `raw_derivations` 的历史含义。新增 `raw_evidence_bindings_v2`。以下仅说明语义字段；
完整 DDL、FK、project/ACL/raw/selected digest、JSON 和 CHECK 约束以 `15` 文档 §4.4 为唯一权威：

```text
locator_id PRIMARY KEY
project_id
raw_object_id FOREIGN KEY raw_objects_v2(project_id, raw_object_id)
derived_entity_id
retrieval_unit_id
generation_id
derivation_kind
derivation_version
selector_kind
selector_json
selector_sha256
selected_content_sha256
parser_artifact_sha256 nullable
valid_from nullable
valid_to nullable
created_at
invalidated_at nullable
binding_json
binding_sha256 UNIQUE
```

索引至少覆盖：

- `(derived_entity_id, generation_id, invalidated_at)`；
- `(retrieval_unit_id, generation_id, invalidated_at)`；
- `(raw_object_id, invalidated_at)`；
- `(binding_sha256)`。

一个 derived entity 允许有多个 raw bindings；必须由 retrieval unit + generation + selector
消除歧义。没有唯一匹配时 fail closed，不按时间“猜最新”。raw tombstone 必须在同一受控操作中
失效 bindings；若两表不能原子更新，读取路径必须以 raw object state 为最终否决项。

### 4.4 Raw object/event identity migration

G1 不能在现有跨项目 unique/idempotency 约束上建立 authority。`G1I.1` 必须同时提供
project-scoped identity migration：

- raw logical identity 至少绑定 `project_id + source_type + source_instance + source_object_id +
  source_version + content_hash + acl_ref`；
- raw ID 使用上述 canonical payload 生成 V2 identity；物理 bytes 可以按 digest 去重，但 logical
  metadata/ACL/tombstone 必须按 project/visibility 隔离；
- event idempotency 至少绑定 `project_id + source_type + source_instance + source_object_id +
  source_version + event_type + acl_ref`；
- `blocked_entities_v2` 使用 `(project_id, entity_id)` 主键，并由 raw object project 校验；
- 所有 get/tombstone/stats/resolve API 在 SQL 查询中带 project 条件；
- 既有 V1 rows 只读迁移到 V2 identity，遇到同 identity 多 project、ACL 不一致或归属不明时进入
  quarantine report，不自动选第一条；
- migration 先复制/校验/对账，再切换 reader；不得原地重写被 Wiki generation 引用的 V1 raw ID。

`persist_bytes_v2` 必须自行计算 payload digest。调用方提供的 digest 只能作为 expected digest；
不一致时拒绝写入。仅有 `payload_ref` 而没有已取回 bytes/canonical record 的对象标为
`reference_only`，不得成为 raw evidence binding。

## 5. 冻结接口：Shared Raw Source Authority

### 5.1 Store 接口

```text
resolve_binding(project_id, locator_id, derived_entity_id,
                retrieval_unit_id, generation_id) -> exact binding | none

list_active_bindings(project_id, derived_entity_id,
                     generation_id) -> ordered bindings

invalidate_bindings(raw_object_id, reason, actor) -> count
```

所有查询在 SQL 层包含 project 和 invalidated 条件。现有 `get_object(raw_object_id)` 不能直接
作为新 gateway 的权限边界。

现有 ingestion/list/by-id/tombstone API 还必须改用 public projection：移除 `storage_path`、内部
`payload_ref`、本地 source URI 和未脱敏 metadata；by-id/tombstone 明确要求 project ID。API
返回模型与内部 store row 分离，禁止 `dict(row)` 直接出站。

### 5.2 Service 接口

```text
read_evidence_v2(request: RawEvidenceReadRequestV2) -> RawEvidenceReadResultV2
```

请求至少包含：`project_id`、排序去重的 requester ACL、`locator_id`、expected binding digest、
expected source generation、expected stable version、purpose、trace ID。调用方不能提供
`storage_path`、raw digest 或 selector 来覆盖已存 binding。

固定校验顺序：

1. 格式、大小、purpose、请求 ACL canonicalization；
2. 以 project + locator ID 解析 binding；
3. 验证 binding digest 与请求/存储内容；
4. 以 project + raw ID 读取 raw metadata；
5. 验证 source domain/type/instance/object/version；
6. 验证 ACL/visibility partition；
7. 验证 raw state 必须为 active；
8. 验证 derivation/binding 未失效、generation/stable version 精确匹配；
9. 读取 DB 中的 relative content-addressed `storage_key`，root 只来自 server config；
10. 在 root 下安全解析 key，拒绝 absolute/`..`/URL，逐级拒绝 symlink，并要求目标为 regular file；
11. 验证 byte length 和 raw SHA-256；
12. 应用受限 selector/parse artifact；
13. 验证 selected SHA-256；
14. 执行 secret/redaction/output-size policy；
15. 生成审计 trace 和 immutable read result。

任何一步失败都不得退回 Wiki candidate/page/snippet。对外响应不得泄露“对象存在但无权限”；
外部统一为 `evidence_unavailable`，内部审计保留具体 reason code。

### 5.3 结果合同与 reason code

成功结果包含 locator/binding digest、source/version/generation、canonical selected bytes 或安全
文本、raw/selected digest、`OBSERVED` 状态和审计摘要，不返回 storage path。

shared read 的内部 reason code 固定为：

```text
binding_not_found
binding_ambiguous
binding_digest_mismatch
project_mismatch
acl_denied
visibility_mismatch
raw_not_found
raw_quarantined
raw_tombstoned
raw_state_invalid
derivation_invalidated
generation_mismatch
stable_version_mismatch
source_identity_mismatch
storage_unavailable
storage_outside_root
storage_symlink
storage_not_regular
byte_length_mismatch
raw_digest_mismatch
selector_invalid
selector_out_of_bounds
parse_artifact_missing
parse_artifact_mismatch
selected_digest_mismatch
unsafe_content
unsupported_media
```

canonical/writer/binding/migration 基础 reason 以 `15` 文档 §7.1 为准；相同语义必须复用同一个
字符串。`legacy_unbound` 属于 Wiki V1→V2 compatibility reason。任何新增 reason 必须先更新 registry、
contract test 和 public projection，不得在 store/router 内临时拼接。

## 6. 六源 gateway 设计

### 6.1 CodeRawEvidenceGatewayV2

权威输入：文件 raw object；可选 repository snapshot 只证明仓库水位，不能代替文件内容。

绑定来源：Code V2 unit 的 `source_lineage.attributes.raw_object_id`、`generation_id`、`ref`、
`file_content_hash/blob_hash` 和 `context_ref.source_range`。

必须校验：

- repository/project/ACL/generation；
- raw source object path 对应 unit file path；
- source version 等于 target commit/ref；
- raw file digest 与 lineage file digest；
- UTF-8 byte range 与 line range交叉一致；
- selector 输出覆盖 unit body；symbol/line claim 不允许自动扩大到整个仓库。

迁移：对现有 Code V2 unit 逐行生成 binding；若 metadata 缺 raw ID、范围或 digest，标记
`legacy_unbound`，不回退到 legacy `entities.content`。

### 6.2 CodexRawEvidenceGatewayV2

权威输入：单个经过现有 redaction 处理的 raw item bytes。原始 reasoning、secret 或已删除项
不得因为 Wiki 引用重新暴露。

必须校验：source/thread/turn/item/generation/ACL、item ordinal/order、redaction marker、blocked
entity 和 tombstone。

thread summary 不是单一 raw fact。两种合法方式：

1. Wiki claim 引用组成摘要的多个 item binding；或
2. ingestion 新增内容寻址的 thread manifest，按 ordinal 绑定 item IDs/digests，gateway 逐成员校验。

当前仅有 thread metadata event 而没有完整有序内容 manifest；因此旧 thread inventory 只能是
`derived/reported`，不能升级为 `observed`。

### 6.3 ExperimentRawEvidenceGatewayV2

权威输入：MLflow run canonical JSON raw object。run、metric、artifact 已有 raw derivation，但需
补充精确 JSON pointer binding。

必须校验：run external/formal identity、source version、dataset ID/version、metric raw value、单位、
step、split/dimensions、aggregation role 和 artifact checksum 状态。

规则：

- 数值必须从 raw string 重新解析，不能回读格式化 snippet；
- 单位未知或不兼容时返回 observed raw value，但比较 claim 必须拒绝；
- artifact 只有 checksum verified 才能产生 verified artifact fact；URI 存在不等于内容 verified；
- experiment-level summary 要么引用多个 run binding，要么新增 experiment snapshot raw event；
- 现有仅 derived formal experiment row 的摘要不得冒充 raw。

### 6.4 NotebookRawEvidenceGatewayV2

权威输入：已保存 `.ipynb` bytes。共享 reader 取代 runtime 私有 `_safe_raw_bytes` 的重复实现。

selector 由 formal cell/output ordinal 与适配器生成的 stable cell ID 共同绑定；必须校验 revision、
execution、cell/output producer、执行顺序、stale/unexecuted 状态。

规则：

- source cell 直接来自 raw notebook，可为 observed；
- output 只有确实存在于对应 execution/cell outputs 时可为 observed；
- 参数推断、lineage、比较和 reproduction completeness 仍是 derived，除非另有 reviewed authority；
- stale output 不得支撑 current claim；
- oversize/binary output 只返回摘要和 digest，不直接返回内容。

### 6.5 DocumentRawEvidenceGatewayV2

权威输入：原始 document bytes。Markdown/text 可直接 selector；PDF/DOCX 等二进制格式必须经
内容寻址 parse artifact：

```text
parse_artifact_sha256 = hash(raw_content_sha256, adapter_version,
                             parser_environment_sha256, canonical_parse_output)
```

gateway 必须读取并校验 raw bytes，再验证 parse artifact input digest、parser environment 和输出
digest，最后应用 page/section/table/figure/citation selector。

规则：

- source-authored paragraph/table cell/caption/citation 可为 observed；
- extractive summary、模型 claim、reviewed claim 保持各自 derivation/review 状态；
- OCR/解析失败返回 typed unavailable，不使用 formal table/claim snippet 兜底；
- table 使用 row/column semantic key，不以显示顺序猜测；
- document version、page 和 parse artifact 必须同时匹配。

### 6.6 WorkspaceRawEvidenceGatewayV2

Workspace 当前没有 raw authority，必须先建立 append-only state event：

- `SourceEventInput.source_type` 增加 `workspace`；
- project/topic/iteration/work-item/relation 的 create/update/status transition 写 canonical event；
- payload 包含 entity type/id、version、before digest、after canonical state、actor authority、
  effective time、observed time、mutation id；
- raw event 写入与 formal mutation 通过同事务接口或 durable outbox 协调；
- binding 未完成前该 workspace row 不得成为 strict raw claim；
- delete/retention 产生 tombstone event，并使旧 binding 按 policy 失效，而不是覆盖历史 bytes。

as-of 读取使用 `valid_from <= as_of < valid_to`，再以 observed time 和 generation 水位验证；禁止用
当前表行回答过去状态。owner 和 acceptance 等字段只有真实存在于 canonical event 时才能引用。

## 7. Wiki Source Ref V2 与迁移

### 7.1 WikiSourceRefV2

公开 source-ref 只保留安全导航信息与不可变绑定：

```text
source_ref_id
source
entity_type
entity_id
display_locator
source_generation
watermark
observed_at
stable_version
raw_locator_id
raw_locator_sha256
selected_content_sha256
evidence_status
content_sha256
```

ACL 仍在 Wiki page scope 和服务器私有 binding 中执行；若为审计需要保留 ACL ref，API 只能返回
visibility digest，不返回其他项目 ACL。`content_sha256` 绑定全部字段。

### 7.2 版本策略

- `WikiSourceRefV1`、V1 page 和 V1 generation 保持只读兼容；
- V1 ref 可用于导航和 provenance 展示；raw verify 返回 `legacy_unbound`；
- `require_raw_evidence=true` 时含 V1 ref 的请求必须 partial/refusal；
- 不依据 entity ID、locator 或“最新 raw”自动补绑历史 ref；
- 新 compiler 只生成 V2 ref；一个 Wiki generation 内禁止混用 V1/V2；
- migration 通过重新从固定 source generation 编译新的 Wiki generation，不更新旧行；
- active pointer 只有在 G2 Gate 通过后才能指向 V2 generation。

### 7.3 Store 与 Navigator

WikiStore 继续保存 candidate envelope 作为编译 provenance，但 `StoreRawEvidenceGatewayV1` 不再
承担 raw authority。新增 gateway 只通过 `raw_locator_id + digest` 调用 shared authority。

Navigator 的计数改为：

- `source_ref_read_count`：读取 Wiki ref；
- `raw_attempt_count`：尝试回读；
- `raw_verified_count`：完整 authority 校验成功；
- `raw_failed_by_reason`：内部 reason 聚合；
- `claim_authority_count`：通过事实状态和 review policy 的数量。

页面 obligation 被找到不等于事实已验证；只有 raw read 成功并满足 claim policy 才能进入
supported obligation。

## 8. 状态转换矩阵

| 来源证据 | raw read | derivation/review | EvidenceFact 状态 | strict claim authority |
|---|---|---|---|---|
| raw selector 直接命中 | PASS | 无 | OBSERVED | 是 |
| raw + 独立 validation | PASS | accepted/confirmed | VERIFIED | 是 |
| 来源自报记录 | PASS | reported | REPORTED | 否，除非 claim 明确为“来源报告” |
| deterministic derived | PASS | 未 review | INFERRED | 否 |
| deterministic derived | PASS | reviewed/confirmed | VERIFIED 或 OBSERVED，按 policy 版本 | 是 |
| model derived | PASS | 未 review | INFERRED | 否 |
| Wiki candidate/page | 不适用 | 任意 | 不生成 raw fact | 否 |
| V1 legacy ref | FAIL legacy_unbound | 任意 | 不生成 raw fact | 否 |
| stale/wrong version | FAIL | 任意 | 不生成 raw fact | 否 |
| tombstone/quarantine/ACL denied | FAIL | 任意 | 不生成 raw fact | 否 |

## 9. 测试矩阵

### 9.1 Shared authority 合同测试

| 类别 | 必测用例 |
|---|---|
| Identity | canonical round-trip、字段篡改、binding digest、selected digest、重复/歧义 |
| Project isolation | raw identity/event idempotency 跨项目同值、public ACL、project-scoped tombstone/blocked/stats |
| Scope | wrong project、空 ACL、未排序 ACL、ACL denied、visibility mismatch |
| Version | wrong source version、stable version、generation、valid-time boundary |
| State | active、quarantined、tombstoned、invalidated derivation、blocked entity |
| Storage | missing、relative path、outside root、symlink each component、directory、unreadable |
| Integrity | byte length、raw digest、selector digest、selected digest、parse artifact digest |
| Selector | 每种 selector positive、empty、out-of-bounds、wrong media、oversize |
| Security | secret、control chars、path/URL/tool injection、error side-channel |
| API projection | storage path/payload ref/local URI/secret metadata 不出站，by-id/tombstone 必须带 project |
| Write integrity | claimed digest mismatch、reference-only、跨项目逻辑对象/物理 blob 去重 |
| Concurrency | read during tombstone、generation swap、binding invalidation、partial write |
| Audit | trace 无 raw bytes/path/ACL 泄漏，reason code 和 digest 完整 |

### 9.2 每源最低测试集

每源至少 1 个成功整对象、1 个成功精确片段，以及以下 14 个负向用例：

1. wrong project；2. wrong ACL；3. wrong raw ID；4. wrong source identity；
5. wrong stable version；6. wrong generation；7. derivation missing；8. derivation invalidated；
9. quarantined；10. tombstoned；11. missing storage；12. raw digest mismatch；
13. selector out-of-bounds；14. selected digest mismatch。

来源特有用例：

- Code：UTF-8 multibyte range、line/byte disagreement、wrong commit/blob、dirty snapshot；
- Codex：redacted item、reasoning excluded、ordinal reorder、thread manifest member missing；
- Experiment：numeric parse、unit mismatch、step/split、dataset drift、artifact unverified；
- Notebook：stale/unexecuted、wrong output producer、cell reorder、binary/oversize output；
- Document：parser environment mismatch、OCR failure、table semantic coordinate、citation/figure；
- Workspace：before/after version、same timestamp ordering、as-of boundary、outbox incomplete、delete。

### 9.3 E2E 资格用例

每源固定两条 live 用例：

- positive：正式 ingestion → raw/binding → source retrieval → Wiki compile/publish → Navigator
  raw read → Evidence Pack → supported claim；
- refusal：在 raw read 前注入 ACL/version/tombstone/digest 故障，最终必须 partial/refusal，且页面
  仍可能可导航但不得支撑 claim。

另加跨源：同事实一致、冲突、wrong-version、as-of 分歧、单源 timeout、一个 V1 legacy ref、
一个 source generation 中途变更。

## 10. 分阶段实施顺序

G0 version admission 前只执行 G1D（设计），不得执行 schema/code migration。

| ID | 工作项 | 输入 | 输出 | 完成证据 |
|---|---|---|---|---|
| G1D.1 | 六源 raw/derivation/selector 盘点 | 当前 schema/service/runtime | 本文 §2/§6 | 文件/字段级证据 |
| G1D.2 | Locator/selector 合同冻结 | 盘点 | §4 合同 | 评审签字/ADR |
| G1D.3 | Shared authority API 冻结 | Notebook 安全读实现 | §5 接口/错误码 | 威胁模型评审 |
| G1D.4 | Wiki V2 迁移冻结 | V1 ref/store/navigator | §7 迁移 | compatibility review |
| G1D.5 | 测试与 Gate 冻结 | 六源风险 | §9/§11 | traceability matrix |
| G1D.6 | Binding foundation 执行规格 | shared P0 + SQLite/V1 migration | V2 schema/identity/M0–M8/dry-run/BF-01–15 | execution-spec review |
| G1I.1 | 新增 binding schema/store | G1D + G0 PASS | migration/store API | schema/rollback tests |
| G1I.2 | 抽取 shared secure reader | Notebook implementation | service API | storage/security tests |
| G1I.3 | Code gateway/backfill | V2 lineage | Code bindings | Code matrix PASS |
| G1I.4 | Codex gateway/manifest | item raw | Codex bindings | Codex matrix PASS |
| G1I.5 | Experiment gateway/selectors | run raw | Experiment bindings | Experiment matrix PASS |
| G1I.6 | Notebook gateway/selectors | ipynb raw | Notebook bindings | Notebook matrix PASS |
| G1I.7 | Document parse artifact/gateway | document raw | Document bindings | Document matrix PASS |
| G1I.8 | Workspace event authority | mutation APIs | event raw/bindings | temporal matrix PASS |
| G1I.9 | WikiSourceRefV2/compiler/store | six gateways | new generation | migration tests PASS |
| G1I.10 | Navigator/read traces | V2 refs | fail-closed raw reads | E2E refusal matrix PASS |
| G1I.11 | Backfill/dry-run/rollback | production mirror | coverage report | no overwrite/rollback PASS |
| G1I.12 | G1 portable package/review | all above | immutable evidence | G1 Gate decision |

并行限制：六个 source gateway 的实现可在 `G1I.1–G1I.2` 后并行，但 Wiki V2 集成必须等待
六源接口冻结；Workspace event 和 Document parse artifact 不得被“临时 adapter”跳过。

## 11. G1 退出 Gate

只有以下条件全部成立，G1 才能标记 `QUALIFIED`：

1. G0 已从 clean checkout 和远端 Python 3.12/3.13 CI 通过；
2. locator、selector、binding、read result 和 reason code 均有版本化合同；
3. 六源都从 raw object 或受约束 parse artifact 回读；
4. 六源各自完整正负/故障/ACL/version/tombstone 矩阵通过；
5. V1 legacy ref 在 strict 模式安全拒绝；
6. raw citation read-back digest success = 100%；
7. unauthorized/secret/path leakage = 0；
8. raw 缺失、状态错误或 selector 错误绝不降级到 Wiki/candidate snippet；
9. migration dry-run、重入、失败恢复、tombstone propagation 和 rollback 通过；
10. portable evidence package 绑定 revision、schema、fixture/real inputs、命令和结果；
11. 独立 Gate Review 明确 `PASS`，并保持默认 V1。

## 12. 不进入 G1 的事项

- claim verifier 的最终派生事实政策属于 G2；
- 三套 planner 与 as-of 能力统一属于 G3；
- 真实六源 qualification corpus 的规模补齐属于 G4；
- 检索/融合指标与校准属于 G5/G6；
- shadow/canary 和默认切换属于 G8/G9。

G1 可以为上述阶段保留字段和 trace，但不能提前声称其质量或发布 Gate 已通过。

## 13. 当前决策与下一步

当前判定：`G1 DESIGN ENGINEERING_PASS / OWNER_REVIEW_HOLD / IMPLEMENTATION HOLD`。第一方
对抗性复核见 `reviews/21_G1_RAW_EVIDENCE_DESIGN_REVIEW_2026-08-06.md`。
Binding foundation 的表级/事务级/迁移级补充设计见
`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md`，独立复核见
`reviews/22_G1_BINDING_FOUNDATION_EXECUTION_SPEC_REVIEW_2026-08-06.md`。

下一步严格顺序：

1. owner 完成 G0 version admission 并触发远端 CI；
2. 审阅本文的 locator、selector、Workspace event，以及执行规格 D1–D5 决策点；
3. 通过后先实现 `G1I.1 binding schema/store` 与 `G1I.2 shared secure reader`；
4. 再实现六源 gateway；
5. 最后接入 WikiSourceRefV2 和 Navigator，生成 G1 package。

在第 1–2 步完成前，不开始 G1 schema 或 runtime 代码变更。
