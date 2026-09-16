# G2 Live Wiki Raw-verified Grounded Answer 可执行规格

版本：2026-08-06 V1  
状态：`G2D DESIGN_ENGINEERING_PASS / G1_ENTRY_HOLD / RUNTIME_NOT_STARTED`  
上位目标：`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`  
Raw authority：`13_G1_RAW_EVIDENCE_AUTHORITY_DESIGN.md`  
Binding foundation：`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` V2  
当前默认：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`  
数据边界：`SOURCE READ-ONLY AUDIT / FORMAL DATABASE NOT ACCESSED`

## 1. 目的与结论

G2 的唯一结果不是“Wiki 能搜索”“页面能显示 citation”或“LLM 返回带引用文字”，而是：

```text
受控来源 raw object
  → G1 exact binding + selector
  → shared raw read receipt
  → Wiki page/navigation obligation
  → raw-observed / externally-verified Evidence Fact
  → claim-level authority check
  → grounded answer 或 typed refusal
```

当前实现已有 compilation、generation、ACL partition、search、navigation、Evidence Pack、claim/citation
verifier、refusal 与 answer contract，方向合理；但没有完成以上链路。当前 `raw_verify_count` 的名称比
实际 authority 更强，不能作为 G2 通过证据。

本规格冻结 G2 的 V2 source ref、compiler input、obligation 状态机、raw read receipt、Evidence Pack、
claim authority、API、迁移、测试和回滚。它不创建表、不迁移数据、不修改默认 engine，也不授权正式
数据库访问。

## 2. 当前实现的精确证据链与断点

### 2.1 当前链路

```text
MultiSourceCandidateV2
  → compiler_v1._source_ref()
      raw_content_sha256 = SHA256(candidate envelope)
  → WikiSourceRefV1
  → WikiStoreV1.wiki_source_candidates_v1.candidate_json
  → StoreRawEvidenceGatewayV1.read()
  → CandidateRawEvidenceReaderV1.read()
  → EvidenceFactV2(text = candidate.snippet)
  → WikiNavigatorV1._build_evidence_pack()
  → build_grounded_answer_v2()
```

该链只能证明“发布 generation 中的 candidate envelope 没变”，不能证明 snippet 与 Code blob、Codex
item、Experiment run、Notebook revision、Document bytes/parse artifact 或 Workspace event 一致。

### 2.2 文件级断点

| ID | 当前代码证据 | 当前行为 | G2 风险 |
|---|---|---|---|
| GAP-G2-01 | `compiler_v1.py::_source_ref` | `raw_content_sha256` 写 candidate envelope digest | 字段名冒充 raw bytes digest |
| GAP-G2-02 | `store_v1.py::stage_generation` | 持久化 `candidate_json`，没有 locator/binding/selector | page 无法回到 G1 authority |
| GAP-G2-03 | `evidence_gateway_v1.py` | 从 WikiStore 读 candidate，不调用 shared reader | source state/tombstone/digest 不可见 |
| GAP-G2-04 | `navigator_v1.py::CandidateRawEvidenceReaderV1` | 用 `candidate.snippet` 生成 fact | adapter/parser/summary 错误不可检测 |
| GAP-G2-05 | `navigator_v1.py::_obligation_support` | 见到带 role/source 的 page 就标 `SATISFIED` | raw read 前 obligation 假完成 |
| GAP-G2-06 | `navigator_v1.py` | `verified_source_ref_ids` 只表示 candidate read 成功 | `verified` 术语越权 |
| GAP-G2-07 | `live_organization_v1.py` | 所有 inventory item 都变成 `derived_fact/deterministic_derived` | live Wiki 无 raw claim authority |
| GAP-G2-08 | `answer_v2.py::verify_claim_citations_v2` | 接受 `OBSERVED/VERIFIED`；derived 还需 review string | review string 无受信 receipt 绑定 |
| GAP-G2-09 | `query_v1.py` | tests 使用手造 `raw_fact` candidate | 证明 candidate 闭环，不证明 live ingestion 闭环 |
| GAP-G2-10 | `runtime_v1.py::publish_generation` | reviewer 只需格式正确的 SHA 字符串 | 不能证明外部 reviewer authority |
| GAP-G2-11 | `WikiNavigationRequestV1.as_of` | 字段进入 request digest，但导航未做 capability/temporal 校验 | as-of 可能只被记录、不被执行 |
| GAP-G2-12 | source status build | 未命中统一写 `no_matching_evidence` | timeout/unauthorized/not-indexed 无法区分 |

### 2.3 已有可复用基础

| 可复用 | 保留内容 | 必须换代的内容 |
|---|---|---|
| Wiki immutable generation | project/generation/manifest/CAS publish/rollback | V1 embedded source ref authority |
| ACL partition | project ACL、visibility partition、404 non-disclosure | object visibility 与 request visibility 的 G1 V2 绑定 |
| search/navigation budgets | bounded search/read/follow/raw/deadline trace | page/raw obligation 分层与 typed failure |
| Evidence Pack V2 | fact/citation exact membership、token accounting、digest | raw read receipt membership、support level |
| GroundedAnswerV2 | unsupported claim 不渲染、refusal 最终 authority | reviewed-derived receipt 与 raw authority policy |
| source-specific rendering | typed source headings/locator/version | 不得把渲染文本当 evidence authority |

## 3. G2 不可妥协的不变量

1. `page_found`、`source_ref_bound`、`raw_observed`、`externally_verified` 和 `claim_supported` 是五个不同状态；
2. Wiki page/fact/link/candidate/snippet 永远不是 raw authority；
3. Navigator 只能使用 G1 store 中已持久化的 locator；请求方不能提交 selector、storage key 或 raw digest；
4. raw read 失败后禁止退回 candidate/page text；
5. `OBSERVED` 只来自 shared reader 对 raw/selected digest 的成功复核；
6. `VERIFIED` 只来自 `OBSERVED + reviewed validation receipt`，不能由分数、字符串或 compiler 自填；
7. reviewed derived fact 必须有受信 review receipt、derivation input membership 和适用 claim type；
8. 每个 Evidence Fact 恰好绑定一个 raw read receipt或一个 reviewed-derived receipt；
9. page generation、source generation、binding generation、raw read expected generation 必须完全一致；
10. unauthorized、timeout、not-indexed、tombstoned、digest mismatch、no-match 必须保持不同内部 reason；
11. 外部 unauthorized 响应不泄漏对象是否存在；
12. 同一 query 全程固定 Wiki manifest 与六源 snapshot，执行中 active pointer 改变不能影响本次结果；
13. as-of 在 G3 统一前只允许 exact supported capability；不支持时明确拒绝，不能解释成 current；
14. answer generator 只能读取安全渲染后的已准入 facts/citations，不能访问 raw storage 或 Wiki page 全文；
15. 默认 V1、production registry、formal DB 与 active V1 Wiki generation 在 G2 隔离实现期间不变。

## 4. 冻结合同

### 4.1 `WikiSourceRefV2`

V2 与 V1 并列，禁止原地增加可空字段后把历史 ref 视为已绑定：

```text
WikiSourceRefV2
  source_ref_id                 # wiki-source-ref-v2:<subject hex>
  project_id
  source_domain
  entity_type
  entity_id
  retrieval_unit_id
  stable_version
  source_generation_id
  source_watermark
  acl_ref
  object_visibility_sha256
  observed_at
  locator_id                    # G1 RawEvidenceLocatorV2
  binding_sha256
  selector_sha256
  raw_content_sha256
  selected_content_sha256
  parser_artifact_sha256?       # Document only when required
  binding_version
  content_sha256
  ref_version=wiki-source-ref-v2
```

约束：

- `project_id/source_domain/entity/retrieval_unit/version/generation/acl` 必须与 binding 完全一致；
- `raw_content_sha256` 与 `selected_content_sha256` 分开，禁止再次使用 candidate envelope digest；
- object visibility 使用 G1 domain，不复用 Wiki request ACL 集 digest；
- locator/binding/selector/digest 只能由 G1 authority projection 构建；
- page 可展示 portable typed locator，但 V2 ref 不包含 storage key/path/payload；
- V1 ref 的唯一安全兼容状态为 `legacy_unbound`，只能导航或显示，不能进入 strict raw Evidence Pack。

### 4.2 `WikiCompilationEvidenceV2`

Compiler 不再直接接受裸 `MultiSourceCandidateV2` 作为 source authority，而接受成对 envelope：

```text
WikiCompilationEvidenceV2
  candidate                     # 只负责检索/组织/展示建议
  source_ref_v2                 # 只负责 raw authority
  binding_projection_sha256
  evidence_roles
  compilation_disposition       # admitted | navigation_only | rejected
  reason_code?
  content_sha256
```

编译前必须逐字段交叉验证 candidate 与 source ref。`derived_fact` 若无 reviewed-derived receipt，只能
以 `navigation_only` 进入页面；它的 ref 不进入 claim-support membership。`legacy_unbound` 可生成带明确
badge 的 V1 导航页面，但不能生成 `raw-evidence` tag。

### 4.3 `WikiEvidenceObligationV2`

V1 单一 `status` 被拆为两个状态轴：

```text
page_status = pending | found | missing | unavailable
raw_status  = not_attempted | observed | verified | unavailable | unauthorized |
              timeout | not_indexed | tombstoned | invalid
support_level = none | navigation_only | raw_observed | raw_verified
supporting_page_paths
supporting_source_ref_ids
supporting_fact_ids
reason_codes
```

状态推进固定为：

```text
PENDING
  → PAGE_FOUND
  → SOURCE_REF_BOUND
  → RAW_READ_ATTEMPTED
  → RAW_OBSERVED
  → RAW_VERIFIED              # 可选，需要受信 validation receipt
  → CLAIM_SUPPORT_EVALUATED
```

页面命中最多到 `PAGE_FOUND`。只有 supporting ref 的 read receipt 成功且 role/source 要求由该 fact
实际满足，obligation 才能进入 `raw_observed/raw_verified`。任一步失败必须保留已完成的导航事实，但
support level 回到 `navigation_only/none`。

### 4.4 `WikiRawReadReceiptV2`

```text
WikiRawReadReceiptV2
  read_id
  request_trace_id
  project_id
  wiki_generation_id
  source_ref_id
  locator_id
  binding_sha256
  source_generation_id
  stable_version
  raw_content_sha256
  selected_content_sha256
  status                     # observed | verified | failed
  validation_receipt_sha256?
  internal_reason_code?
  public_reason_code?
  read_authority_sha256
  observed_at
  content_sha256
```

成功 receipt 不携带 storage path、secret、raw bytes 或完整请求 ACL。失败 receipt 的 portable
artifact 只保留脱敏 reason、subject digest 和计数；详细内部审计按安全策略留在受控日志。

### 4.5 Evidence Fact 与 claim authority

`EvidenceFactV3` 可以复用 V2 展示字段，但新增不可为空的 authority binding：

```text
authority_kind = raw_observed | raw_verified | reviewed_derived | navigation_only
authority_receipt_sha256
locator_id?
binding_sha256?
selected_content_sha256?
review_receipt_sha256?
claim_type_allowlist
```

| Fact authority | 可进入 Evidence Pack | 可支撑普通事实 claim | 可支撑“已验证”claim | 可支撑推断/建议 |
|---|---:|---:|---:|---:|
| raw_observed | 是 | 是，必须表述为 observed | 否 | 否 |
| raw_verified | 是 | 是 | 是 | 否 |
| reviewed_derived | 是 | 仅 receipt allowlist 内 | 仅 receipt 明确授权 | 是，需标注 derivation |
| navigation_only | 仅导航 trace | 否 | 否 | 否 |
| reported/inferred 无 receipt | 可作上下文或 counter lead | 否 | 否 | 否 |

不得只检查 `review_status in {accepted, reviewed}` 字符串。受支持判定必须验证 receipt subject、signer
role、trust anchor、expiry、claim type、source/binding/derivation membership 和当前 generation。

### 4.6 `EvidencePackV3`

V3 在 V2 之上增加：

```text
wiki_manifest_sha256
wiki_generation_id
source_snapshot_receipts[]
raw_read_receipts[]
reviewed_derived_receipts[]
navigation_only_ref_ids[]
fact_authority_map[]
typed_failure_counts[]
as_of_disposition
```

守恒：

- `included_fact_ids = raw receipt facts ∪ reviewed-derived receipt facts`；
- `navigation_only_ref_ids` 与 `included_fact_ids` 不相交；
- 每个 citation 的 fact、locator/binding/selected digest 与 receipt 完全一致；
- 任一 required role 只有 page 支持而无 raw/reviewed fact 时仍为 missing；
- token budget 丢弃 fact 时，同时丢弃 citation 和 receipt membership，但保留 drop reason；
- typed failure 不能被折叠为 `no_matching_evidence`；
- content digest 覆盖完整 membership、decision、snapshot 和 authority map。

## 5. Compiler、Store 与发布

### 5.1 Compiler V2

1. 输入固定 project、Wiki generation、六源 source snapshots 和 `WikiCompilationEvidenceV2`；
2. 先调用 G1 binding projection verifier，不读取 raw bytes；
3. 将 admitted/refused/navigation-only 分开计数；
4. 页面 title/summary/fact/link 仍是 derived organization，不改变 source authority；
5. page 上的 source ref 使用 V2，V1 ref 单独存 legacy section；
6. dependency index 同时维护 `locator→pages`、`page→locators` 和 `binding→generation`；
7. manifest 固定 compiler、binding projection、source snapshot、policy、failure registry digest；
8. 任一 binding ambiguity、generation mismatch、ACL mismatch 或 selected digest 缺失在 staging 前拒绝。

### 5.2 Store V2

新增并列表，不改写 `wiki_*_v1`：

```text
wiki_generations_v2
wiki_records_v2
wiki_page_fts_v2
wiki_source_refs_v2
wiki_compilations_v2
wiki_active_generations_v2
wiki_navigation_receipts_v2     # 可选，若开启必须有 retention/ACL policy
```

`wiki_source_candidates_v1` 可继续保存 V1 兼容/调试输入，但不得被 V2 gateway 读取。V2 staging 事务
必须同时写 records、refs、dependency、compilation manifest；active pointer 在完整 verify 前不变。

### 5.3 发布 authority

V2 publish/rollback 不接受任意格式正确的 SHA 字符串作为 reviewer。调用方传入的是受保护 decision
receipt ID；服务端根据带外 trust policy 验证：

- subject = exact Wiki manifest + source snapshots + compiler/binding policy；
- signer role 有 `wiki_generation_reviewer`；
- receipt 未过期/撤销，时序晚于 staging；
- project、environment、generation 一致；
- same subject 不存在冲突 decision。

默认 active V1 pointer 和 V2 pointer 分开；G8 前 V2 只能显式 opt-in。

## 6. Navigator V2 固定算法

```text
1. 解析并 pin exact Wiki V2 manifest；若无 V2，typed unavailable
2. 校验 request project/ACL/as-of/capability/budget/deadline
3. 规划 role/source obligations（仅导航候选）
4. search → read → follow；记录 PAGE_FOUND，不标 satisfied
5. 收集与 obligation 精确相关的 V2 refs，禁止把页面全部 refs 扩大为支持
6. 对每个 ref 调 shared read_evidence_v2：
     project + locator + expected binding + source generation + stable version
7. 验证 read receipt、selected digest、fact role/source 与 pinned snapshot
8. optional validation receipt 将 OBSERVED 提升 VERIFIED
9. 由 raw/reviewed facts 重算 obligation support
10. 构建 EvidencePackV3；navigation-only page/ref 只进 trace
11. 对 timeout/unauthorized/not-indexed/tombstone/digest/no-match 分别决定 partial/refusal
12. 生成 STOP action；冻结完整 trace digest
```

任何 active pointer 在第 1 步后改变都不影响本 query。raw read budget 按真实 shared reader 调用计数；
cache hit 仍必须验证 receipt subject，不能绕过 state/tombstone check。

## 7. Query 与 Grounded Answer V3

### 7.1 生成前门禁

只有以下条件全满足才调用 generator：

- Evidence Pack 没有 unanswerable reason；
- required roles 均由 `raw_observed/raw_verified/reviewed_derived` facts 满足；
- source snapshot 与 Wiki manifest 完整；
- counter evidence/temporal conflict 已进入 prompt；
- 生成 provider、model、prompt policy 和 external inference approval 固定；
- safe rendering 已移除内部 path、secret 和未授权 metadata。

### 7.2 Claim verifier V3

每个 claim 必须：

1. 引用已知 citation；
2. 覆盖全部 `required_fact_ids`；
3. citation→fact→authority receipt membership 精确；
4. claim type 在 fact allowlist；
5. 对 raw observed 使用“观察到/来源报告”语义，不升级成“已验证”；
6. 对 numeric/date/version/identifier 使用类型化 exact comparison，而非只做 token subset；
7. 对关系 claim 验证 typed relation path 与端点；
8. 对否定/比较/因果 claim 要求相应 counter/baseline/decision roles；
9. as-of 与 fact valid interval/source snapshot 一致；
10. unsafe、unknown、missing、stale、contradicted claim 不渲染。

V2 token subset verifier 可保留为最低 lexical guard，但不能单独产生 `supported=true`。

### 7.3 输出语义

| 条件 | answer mode | final authority | 输出 |
|---|---|---:|---|
| 所有 claim 支持 | grounded | 是 | 只渲染支持 claim |
| 有 claim 失败 | retrieval_only | 否 | 不渲染部分答案；保留内部 verification |
| required role/raw authority 缺失 | refusal | 是 | typed reason + safe remediation |
| 部分非必需 source 超时 | partial 或 grounded-with-qualifier | 是 | 明确缺失 source 与影响 |
| unauthorized | refusal | 是 | 外部统一 evidence unavailable |
| generator 不可用但 pack 可回答 | retrieval_only | 否 | evidence handoff，不伪造 deterministic prose |

## 8. 六源 G2 读取与状态要求

| Source | G1 selector/root | G2 positive | G2 refusal |
|---|---|---|---|
| Code | file blob + UTF-8 line/byte | exact commit/symbol span | wrong commit/blob/range/tombstone |
| Codex | raw item / ordered thread manifest | exact item sequence | redacted/missing/reordered/tombstoned |
| Experiment | canonical run JSON pointer/artifact | metric+unit+dataset/run | wrong unit/dataset/pointer/checksum |
| Notebook | revision/cell/output selector | exact cell/output producer | stale revision/order/output absent |
| Document | bytes + parse artifact span/table/figure/citation | exact parser/version/selector | parse drift/OCR/table/version mismatch |
| Workspace | append-only state event / JSON pointer | exact valid-time state transition | missing event/as-of/delete/dependency |

每源至少需要：1 positive、1 unavailable、1 wrong-version、1 wrong-ACL、1 tombstone/invalidated 和 1
digest/selector failure。所有 case 必须从 ingestion/materialization 产生，不允许直接手造
`MultiSourceCandidateV2(raw_or_derived="raw_fact")` 作为 G2 qualification evidence。

## 9. API 与兼容策略

- V1 `/v1/wiki/*` 和 `X-RAG-Wiki-Engine: wiki_v1` 保持现状和 `QUALITY_HOLD`；
- V2 使用显式 `/v2/wiki/*` 或 exact `wiki_v2` engine，默认关闭；
- V2 evidence response 返回 public locator、version、selected digest、receipt digest、status，不返回 raw
  bytes、storage path、payload ref 或内部 reason；
- `legacy_unbound` 返回 navigation-only badge 与迁移 remediation，不返回伪 raw fact；
- V1/V2 generation、cache、active pointer、metrics 和 audit namespace 分开；
- 不允许请求 header 在没有 reviewed project/intent allowlist 时启用 V2；
- G8 前不改变 `/v1/query` 默认行为。

## 10. 原子实施序列

### 解锁条件

G2 runtime 只有在 G1 `QUALIFIED` 后解锁。G1 必须提供六源 locator/read positive+negative package、V2
source ref migration input 和 shared reason registry。G0/G1 任一退回 HOLD 时停止 G2 实现。

| 顺序 | ID | 唯一主结果 | 允许范围 | 退出证据 |
|---:|---|---|---|---|
| 1 | T2.1.1 | authority/status/receipt contract | 新 `wiki/v2/contracts.py` + tests | canonical/portable/tamper matrix |
| 2 | T2.1.2 | claim authority policy | `answer_v3.py` policy only | raw/reviewed/legacy truth table |
| 3 | T2.2.1 | Live Organizer V2 保留 raw/binding lineage | organizer v2 + fixture tests | 不再统一 derived；无 binding→navigation-only |
| 4 | T2.3.1 | Compiler V2 input/ref/dependency | compiler v2 + tests | candidate/ref cross-check；legacy隔离 |
| 5 | T2.3.2 | Store V2 stage/verify/publish/rollback | store v2 + isolated schema tests | atomicity/CAS/receipt authority |
| 6 | T2.4.1 | Shared-reader gateway adapter | gateway v2 + no-fallback tests | 只调用 G1 service；no candidate read |
| 7 | T2.4.2 | Navigator page/raw 双状态机 | navigator v2 + tests | page 不提前满足；typed failure |
| 8 | T2.5.1 | EvidencePackV3 + claim verifier | answer v3 + property tests | receipt membership/typed entailment |
| 9 | T2.5.2 | Query/API V2 opt-in | query/router/models + tests | default V1；generation前门禁 |
| 10 | T2.6.1 | 六源 positive/refusal E2E | ingestion fixtures + 36-case minimum | 禁止手造 raw candidate |
| 11 | T2.7.1 | cross-source E2E | consistent/conflict/as-of/ACL/generation | wrong-version/unsupported=0 |
| 12 | T2.7.2 | migration + rollback rehearsal | V1 nav-only → V2 generation | V1 bytes/pointer unchanged |
| 13 | T2.8.1 | portable Gate package | evaluator/artifact/verifier | temp-copy verify；all denominators |
| 14 | T2.8.2 | independent G2 review | reviewed decision | `QUALIFIED` 或明确 HOLD/FAIL |

每一项只关闭一个主结果。不得在 contracts 任务中顺便建表，不得在 store 任务中切默认，不得在 E2E
任务中访问未授权正式数据。

## 11. 验证矩阵

| ID | 场景 | 必需断言 |
|---|---|---|
| GA-01 | V2 ref canonical roundtrip | digest/ID portable |
| GA-02 | candidate/ref project mismatch | reject before stage；row=0 |
| GA-03 | source/generation/version mismatch | reject；无 page/ref/index |
| GA-04 | ACL/object visibility mismatch | reject；外部不泄漏存在性 |
| GA-05 | missing/ambiguous binding | navigation-only 或 refusal；不猜最新 |
| GA-06 | raw tombstoned/quarantined | typed failure；candidate fallback=0 |
| GA-07 | raw/selected/parser digest mismatch | typed failure；fact/citation=0 |
| GA-08 | page found/raw missing | page_found=true；support_level≠raw；claim=0 |
| GA-09 | page refs 含无关 role | obligation 不被扩大满足 |
| GA-10 | raw budget/deadline | precise attempted/succeeded/failure counts |
| GA-11 | active pointer mid-query swap | pinned generation result不变 |
| GA-12 | wrong requester project/ACL | 404/evidence unavailable；无 existence leak |
| GA-13 | V1 legacy ref | navigation-only；strict read拒绝 |
| GA-14 | live item 无 G1 binding | navigation-only；不标 raw-evidence |
| GA-15 | raw observed fact | 允许 observed 语义 claim；禁止 verified 语义 |
| GA-16 | raw verified fact | receipt/trust有效才允许 verified claim |
| GA-17 | reviewed derived | subject/allowlist/trust/expiry 全验证 |
| GA-18 | 自填 review_status | 不产生 authority |
| GA-19 | unknown/missing citation | claim 不渲染 |
| GA-20 | token-subset 假 entailment | typed verifier 拒绝 |
| GA-21 | numeric unit/dataset mismatch | reject/contradicted |
| GA-22 | temporal interval/as-of mismatch | refuse；不回退 current |
| GA-23 | counter evidence | conflict visible；无单边肯定答案 |
| GA-24 | required source timeout | timeout ≠ no-match；refusal/partial按政策 |
| GA-25 | generator malformed/unsafe | retrieval-only；内部 failure code |
| GA-26 | source text 含 secret/injection | reject before prompt |
| GA-27 | store stage partial failure | transaction rollback；active不变 |
| GA-28 | publish forged SHA string | receipt authority拒绝 |
| GA-29 | rollback CAS race | stale request拒绝；active不变 |
| GA-30 | 六源 qualification | 每源 6 类 case；raw read success=100%；leakage=0 |
| GA-31 | cross-source generation mismatch | pack build 拒绝 |
| GA-32 | artifact copy/tamper | portable copy PASS；tamper FAIL |

## 12. Gate artifact 合同

`rag-maturity-g2-grounded-answer-v1` 至少包含：

```text
schema_version
reviewed_revision
g1_package_sha256
contract/policy/compiler/store/navigator/answer digests
source_generation_manifests[6]
case_membership_sha256
per_source_denominators[6]
raw_read_attempt/success/failure counts
typed_failure_counts
page_found_without_raw_count
legacy_navigation_only_count
unsupported_claim_count
wrong_version_claim_count
unauthorized_leakage_count
secret_leakage_count
refusal/partial/grounded counts
rollback_receipt_sha256
test commands/results/environments
limitations
review_decision
content_sha256
```

Hard exit：

- 六源 positive raw read success = 100%；
- wrong project/ACL/version/generation/state/digest/selector 全部 fail closed；
- `unsupported_claim_count = 0`、`wrong_version_claim_count = 0`；
- unauthorized/secret leakage = 0；
- page-found-without-raw cases 100% 不生成 claim；
- V1 rollback 后 schema/data/active pointer digest 不变；
- portable copy verify PASS；
- independent reviewer 明确 `QUALIFIED`。

## 13. Stop 与 rollback

立即停止 G2 的条件：

- 实现尝试把 `wiki_source_candidates_v1` 政名为 raw store；
- request 可以覆盖 locator/selector/digest/storage path；
- page/ref 在 raw read 前标 satisfied；
- shared reader 失败后退回 snippet；
- `review_status` 字符串直接产生 VERIFIED；
- as-of unsupported 被解释为 current；
- V2 写入或测试改变 V1 active pointer/default engine；
- qualification case 手造 raw candidate 绕过 ingestion；
- raw bytes/secret/internal path 进入 API、prompt、trace 或 portable artifact。

Rollback：关闭 project/intent V2 allowlist，停止 V2 writer，恢复 V1 request routing；保留 V2 generation、
read receipts 和 failure artifact 供审计，不反向改写 V1，不删除失败证据。若 V2 store corruption，使用
同 manifest 的 raw/binding source snapshots 重建新 generation，不就地修补 active generation。

## 14. 当前目标与下一步

本轮已关闭 `WP-G2D-01 Grounded-answer execution design` 的本地设计部分：现状代码映射、V2 contracts、
状态机、claim authority、六源矩阵、原子实现序列和 Gate artifact 已冻结。

当前仍不能开始 T2.1.1：前置 G1 runtime 未实现且 G0 仍在 owner/remote/LIVE-02 HOLD。当前可执行关键
路径保持：

```text
V24 owner review
  → G0 QUALIFIED
  → G1 Entry E-01..E-10
  → G1 raw binding/readers/gateways QUALIFIED
  → T2.1.1
```

本规格是 implementation authority candidate，不是独立 review、运行 evidence 或发布许可。
