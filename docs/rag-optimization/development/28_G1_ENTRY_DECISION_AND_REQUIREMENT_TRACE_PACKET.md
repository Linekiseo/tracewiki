# G1 Entry Decision Packet 与 BF-01–15 可执行追踪矩阵

版本：2026-08-06 V3  
状态：`OFFLINE_VERIFIER_ENGINEERING_PASS / ENTRY_NOT_SATISFIED / OWNER_INPUT_REQUIRED`  
上位 Gate：`10_RAG_WIKI_MATURITY_PROGRAM.md`、`14_RAG_WIKI_STAGE_OBJECTIVE_BREAKDOWN.md`  
执行规格：`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` V2  
实施就绪审计：`27_G1_BINDING_FOUNDATION_IMPLEMENTATION_READINESS_AUDIT.md`  
当前证据：由 durable current-state record 解析的 reviewed revision + exact admission + external receipts
默认边界：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE / FORMAL_DB_NOT_AUTHORIZED`

## 1. 目的与判定

本文把“等待 owner/architecture review”变成可签署、可复核、不可由仓库作者自行伪造的 Entry
Packet，并把 BF-01–15 从最低测试描述展开到测试文件、fixture、命令、artifact 字段和失败副作用。

V2 已实现 `src/evidence_rag/evaluation/maturity_g1_entry_v1.py` 和
`tests/test_maturity_g1_entry_v1.py`。实现可以离线重算 E-01–10、验证 Ed25519 receipt、校验带外 keyset
信任锚、调用 G0 原生完整 manifest verifier，并拒绝 packet 聚合字段伪造。实现通过不等于当前 Entry
通过：测试中的全绿 packet 是确定性的合成协议夹具，不是项目 owner authority。

当前判定不在本文硬编码。执行时必须从 durable state 解析同一 reviewed revision、exact admission、remote
run、generated/supply disposition、D1–D5、data authorization、independent review、keyset trust 与 receipts；
任一缺失即 `ENTRY_NOT_SATISFIED`。PENDING packet、local reviewed ref/bundle、本地全绿夹具或仓库测试私钥
都不是 E-01–E-10 external authority，也不授权 T1.1.1。

## 2. Entry Gate 的单一解释

G1 代码入口采用最严格上位条件：`G0 QUALIFIED`，而不只是“某次本地 cleanroom 通过”。必须同时
满足：

| Entry ID | 条件 | 权威证据 | 当前 |
|---|---|---|---|
| E-01 | owner-reviewed immutable revision | protected review/attestation + commit/tree SHA | RESOLVE / REQUIRE PROTECTED |
| E-02 | revision bytes 与 resolved source snapshot 完全一致 | admission exact verify | RESOLVE / RECOMPUTE |
| E-03 | fresh checkout Python 3.12/3.13 full | remote immutable job records | RESOLVE / REQUIRE REMOTE |
| E-04 | admission/cleanroom/frontend/release-ready remote chain | remote admission job + artifacts | RESOLVE / REQUIRE REMOTE |
| E-05 | tracked generated output 边界关闭 | clean Git tree + release blocker=0 | RESOLVE / RECOMPUTE |
| E-06 | G0 供应链 blocker 已修复或有明确、限时、分阶段例外 | SBOM/audit + security-owner disposition | RESOLVE / REQUIRE REMOTE ARTIFACT |
| E-07 | D1–D5 架构决定已签署 | decision attestations | RESOLVE / REQUIRE EXTERNAL |
| E-08 | 首个数据输入和正式 DB 禁止边界已签署 | data authorization | RESOLVE / REQUIRE EXTERNAL |
| E-09 | independent reviewer 确认首任务只含 T1.1.1 C1–C10 | review attestation | RESOLVE / REQUIRE EXTERNAL |
| E-10 | 默认 runtime/release 权限不变 | config/release registry diff = 0 | RESOLVE / RECOMPUTE |

E-06 的例外只能允许隔离 feature development，不能把 G0/G1 标记为 production qualified，也不能
授权 shadow/canary/default V2。例外必须有 scope、理由、mitigation、owner、expiry、关闭条件和
禁止事项；永久或无 owner 的 waiver 无效。

## 3. Entry Packet canonical contract

### 3.1 Artifact envelope

未来 artifact schema 固定为 `rag-g1-entry-decision-v1`：

```text
schema_version
artifact_version
status = PENDING|APPROVED|REJECTED|EXPIRED
reviewed_revision
g0_admission
remote_ci
generated_output_boundary
supply_chain_disposition
architecture_decisions[]
data_authorization
independent_review
runtime_boundary
requirements[]
limitations[]
blockers[]
attestation_set_sha256
content_sha256
```

所有 authority payload 使用 `15` 文档的 canonical-json-v1/H；unknown field、duplicate key、非 NFC、
无时区时间、float/NaN/Infinity、absolute local path 和 secret 一律拒绝。

`APPROVED` 必须是以下合取：

```text
all(E-01..E-10 == PASS)
AND all(D1..D5 == ACCEPTED or ACCEPTED_WITH_REVIEWED_ADR)
AND remote_ci.revision == reviewed_revision.commit_sha
AND g0_admission.source_snapshot_sha256 == remote_ci.source_snapshot_sha256
AND generated_output_boundary.release_blocker_count == 0
AND runtime_boundary.default_engine == "v1"
AND runtime_boundary.production_database_accessed == false
AND runtime_boundary.release_authorized == false
AND blockers == []
AND attestations are externally verifiable and unexpired
```

仓库内作者自行提交 `status=APPROVED`、reviewer 名称字符串或本地生成签名都不是 owner authority。
至少需要以下一种外部可验证来源：protected PR approval、受信签名身份、受控变更系统 approval ID，
或组织定义的等价审查记录；packet 只保存其安全 ID/digest，不保存 token、cookie 或个人隐私数据。

### 3.2 离线信任与 receipt 合同

verifier 输入由四个独立对象构成：Entry Packet、attestation bundle、trusted keyset、G0 admission
manifest；调用方还必须显式提供 canonical verification time 和通过独立渠道获得的 keyset digest。
keyset digest 不能从 packet、bundle、同一仓库文件或待验证 CI 输出中反推，否则仍是自签。

```text
keyset = {
  schema_version = rag-g1-entry-keyset-v1,
  keys: [{key_id, algorithm=ed25519, public_key_base64, roles[],
          valid_from, expires_at, revoked}],
  content_sha256
}

receipt = {
  schema_version = rag-g1-entry-attestation-v1,
  attestation_id,
  kind,
  subject_sha256,
  revision,
  issuer_key_id,
  issuer_role,
  issued_at,
  expires_at,
  decision = APPROVED,
  external_reference_sha256,
  signature_base64
}

bundle = {
  schema_version = rag-g1-entry-attestation-bundle-v1,
  attestations[],
  content_sha256
}
```

允许的 role 为 `owner/ci/architecture/rollback_owner/data_owner/independent_reviewer/release_owner/security`；
receipt kind 与 role 一一映射。每个 receipt 必须在 key 和 receipt 的有效期内、未撤销、绑定同一
reviewed revision，并对 domain-separated canonical subject 签名。packet 的引用集合必须与 bundle
集合完全相同，不能漏收据或夹带未引用收据。

职责分离是机器判定条件：architecture 与 rollback owner 不得使用同一 key；independent reviewer
不得与 owner 使用同一 key；runtime boundary signer 不得与 independent reviewer 使用同一 key。
测试 key 即使被赋予多个 role，也不能绕过这些不相等约束。

### 3.3 Revision 与 G0 evidence

```text
reviewed_revision = {
  repository_logical_id,
  commit_sha,
  tree_sha,
  owner_review_attestation_id,
  owner_review_attestation_sha256,
  reviewed_at,
  protected_ref
}

g0_admission = {
  manifest_schema_version,
  manifest_content_sha256,
  source_snapshot_sha256,
  file_count,
  scope_counts,
  exact_verified,
  exclusions_approved,
  unapproved_exclusion_addition_count,
  release_blocker_count,
  cleanroom_receipt_sha256,
  cleanroom_release_ready
}
```

固定要求：`file_count` 和 scope count 必须从 resolved artifact 重算，不能信任手填聚合；任何历史或本地
值只作为 review input，不自动成为 E-01/E-03/E-04 的远程证据。

verifier 不是只检查 G0 外层摘要：它调用 G0 V2 原生 verifier，重算完整 schema、scope policy、逐文件
source identity、scope/state denominator、exclusion/finding、history fixture 和 database boundary；随后
再把 manifest digest/source/file/scope/release blocker 与 Entry Packet 逐项交叉验证。

### 3.4 Remote CI evidence

```text
remote_ci = {
  provider,
  repository_logical_id,
  workflow_logical_id,
  workflow_source_sha256,
  run_id,
  run_attempt,
  trigger,
  protected_ref,
  revision,
  source_snapshot_sha256,
  started_at,
  completed_at,
  conclusion,
  attempt_history: [
    {run_attempt, conclusion, started_at, completed_at,
     log_sha256, artifact_set_sha256}
  ],
  jobs: [
    {job_id, purpose, python_version, conclusion,
     stages[], command_set_sha256, log_sha256, artifact_set_sha256}
  ],
  ci_attestation_id,
  ci_attestation_sha256
}
```

最低 job 集：Python 3.12 full、Python 3.13 full、matrix 后 admission job。admission job 必须依次包含
exact verify、bytes-only cleanroom、frontend locked test/typecheck/build、最终 release-ready；run 被
rerun 时以 `(run_id, run_attempt)` 区分，不覆盖失败 attempt。

禁止证据：截图、复制到文档的绿色 badge、只给 run URL、不同 revision 的多个 job 拼接、本地日志
冒充 remote、跳过/neutral/cancelled 当成功。

`REMOTE_CI` receipt 的 subject 不是只签 run id。它原子绑定以下四块：

```text
g0_admission
generated_output_boundary
remote_ci（去除自身 receipt 引用）
supply_chain_disposition
```

因此改写 cleanroom receipt、生成物 build contract、audit finding set 或任一 job/log/artifact digest，
即使重新计算 packet `content_sha256`，也会使 E-03/E-04 失败。attempt history 必须严格为
`1..run_attempt` 连续序列，旧 failure/cancelled 不能被 rerun 覆盖。

### 3.5 Generated output 与供应链 disposition

```text
generated_output_boundary = {
  tracked_generated_path_count,
  release_blocker_count,
  git_clean,
  build_reconstruction_passed,
  build_contract_sha256
}

supply_chain_disposition = {
  npm_version,
  install_policy_sha256,
  install_script_pending_count,
  lockfile_sha256,
  sbom_sha256,
  audit_tool_version,
  finding_set_sha256,
  unaccepted_critical_count,
  unaccepted_high_count,
  exceptions: [{finding_id, scope, mitigation, owner_attestation_id,
                owner_attestation_sha256, expires_at, closure_condition,
                forbidden_release_stages}]
}
```

历史 React Router RSC advisory 不能因产品为 client-only 而从报告中删除。`WP-G0-05J` 已移除
`react-router-dom/react-router`，以受限第一方 Hash/Memory router 替代实际使用面；fresh audit 为
0，路由闭包前端 32 files/184 tests、typecheck/build 和 workflow contract 均通过。`WP-G0-05K`
进一步冻结 npm 11.17.0、strict exact-version install-script approval、registry/integrity 与
pending=0；最新聚合前端为 33 files/191 tests。详细工程证据与限制见
`29_G0_FRONTEND_ROUTER_SUPPLY_CHAIN_CLOSURE.md`、`32_G0_NPM_INSTALL_SCRIPT_ADMISSION.md`。

E-06 目前只达到 `LOCAL PASS`。远程 admission 必须在同一 reviewed revision 上重新验证 exact npm、
install policy 和 pending=0，并生成 audit JSON、CycloneDX SBOM 和 SHA256SUMS artifact；在 remote
artifact 未出现前不得把 E-06 写为 externally
verified PASS。历史 `7.18.2` finding、`8.3.0` E404 和 `7.11.0` 隔离降级负证据继续保留，不回写成
“当时不存在漏洞”。

## 4. D1–D5 决策合同

每项记录使用：

```text
decision_id
choice
status = ACCEPTED|ACCEPTED_WITH_REVIEWED_ADR|REJECTED|PENDING
rationale_sha256
constraints[]
required_tests[]
rollback_owner_attestation_id
architecture_attestation_id
effective_revision
expires_at nullable
```

### D1：并列表

- 推荐 `PARALLEL_V2_TABLES_COPY_MIGRATION`；
- required tests：V1 schema/data digest unchanged、M1–M7 rollback、old ref unchanged；
- 若选择原地迁移，必须提供独立 ADR、所有 V1 consumer rewrite、备份恢复和无法回滚时的业务接受；
- 当前：`PENDING`。

### D2：project-scoped public object

- 推荐 `PROJECT_ISOLATED_LOGICAL_AUTHORITY_PHYSICAL_BLOB_DEDUPE`；
- required tests：2 projects × public/private ACL、metadata/tombstone/existence side channel=0；
- 当前：`PENDING`。

### D3：不可恢复历史

- 推荐 `REINGEST_NO_OWNER_ACL_GENERATION_GUESS`；
- required tests：simulated swallowed collision → `history_unrecoverable/reingest_required`；
- 当前：`PENDING`。

### D4：storage key

- 推荐 `RELATIVE_CONTENT_ADDRESS_KEY_CONFIGURED_ROOT`；
- required tests：absolute/`..`/URL/symlink/relocation/ordinary restore；
- 当前：`PENDING`。

### D5：cutover 粒度

- 推荐 `PROJECT_SOURCE_INTENT_ALLOWLIST`；
- required tests：one-scope failure 不影响其他 scope、request 不能切换、V2 failure 不 fallback；
- 当前：`PENDING`。

## 5. Data authorization contract

T1.1.1 不需要数据库；T1.1.3–8 只允许临时 fixture DB。T1.1.9 首次 migration scan 前另需：

```text
mode = fixture|sanitized-mirror|authorized-production
dataset_logical_id
snapshot_sha256
schema_sha256
watermark
stable_input
owner_attestation_id
allowed_operations = [read-schema, read-metadata, hash-controlled-blob]
forbidden_operations = [write-v1, connect-production, export-row, export-path,
                        export-uri, export-acl, export-payload]
retention
expires_at
```

默认 packet 固定 `mode=fixture`、`connect-production` 禁止。请求参数、环境变量或 CLI flag 不能把
fixture 授权升级为 production；production authorization 必须是独立外部 attestation，并在 connect
前验证。

## 6. BF-01–15 Requirement→Test→Artifact 追踪

### 6.1 测试文件边界

未来测试文件固定为：

```text
tests/test_raw_v2_contracts.py
tests/test_raw_v2_policy.py
tests/test_raw_v2_schema.py
tests/test_raw_v2_object_event_store.py
tests/test_raw_v2_binding_store.py
tests/test_raw_v2_tombstone.py
tests/test_raw_v2_public_projection.py
tests/test_raw_v2_migration.py
tests/test_raw_v2_migration_rollback.py
tests/test_raw_v2_artifact.py
```

fixture 定义进入 `tests/fixtures/raw_v2/`，只保存 canonical JSON/JSONL/SQL schema text 和受审小型
payload；SQLite DB 在 pytest temp 目录从 schema/rows 构造，不把运行 sidecar 或机器路径纳入 fixture。

每项测试必须捕获 before/after：V1/V2 table counts、row-set digest、blob tree digest、audit count、
public response fields 和 exception/reason。只断言抛错而不证明无副作用，不算 PASS。

### 6.2 逐需求矩阵

| Req | 主要任务 | 测试文件与 fixture | 核心断言 | 失败副作用断言 | Evidence 字段 |
|---|---|---|---|---|---|
| BF-01 | T1.1.1/2/4 | contracts/policy/store；2 project × 3 ACL | IDs distinct；physical blob 可同 | metadata/tombstone/existence reuse=0 | identity matrix digest |
| BF-02 | T1.1.4 | object store；empty/small/large bytes | service digest exact；wrong expected pre-write reject | new files/rows/audit success=0 | before/after blob+row digest |
| BF-03 | T1.1.3/4/6 | schema/binding；reference URI variants | digest/length/blob null；binding typed unavailable | binding/blob=0 | state matrix + reason |
| BF-04 | T1.1.1/5 | event store；same mutation cross project/diff payload | separate event or exact duplicate；conflict typed | conflict new rows/blob=0 | event payload-set digest |
| BF-05 | T1.1.8 | public projection；all endpoints/errors | exact response allowlist | path/ref/URI/secret/ACL side channel=0 | serialized field-set digest |
| BF-06 | T1.1.6 | binding store；entity/unit/generation permutations | exact one or `binding_ambiguous` | no latest query/write=0 | resolve case-set digest |
| BF-07 | T1.1.6 + G1I-02 | binding/read fixture；raw/selector/parse tamper | raw and selected digest both verified | selected bytes/audit success=0 | tamper reason histogram |
| BF-08 | T1.1.7 | tombstone controlled race fixture | output-before-return state recheck；post tombstone bytes=0 | partial invalidation/block/event=0 | schedule trace digest |
| BF-09 | T1.1.7 | same entity ID in 2 projects | only target project blocked | other project row/count change=0 | project row-set digests |
| BF-10 | T1.1.9/11 | migration fixture containing every category | conservation equation；unclassified=0 | source rows changed=0 | classification set/count digests |
| BF-11 | T1.1.9 | swallowed-collision fixture + source manifest gap | `history_unrecoverable/reingest_required` | inferred owner/ACL/generation=0 | finding-set digest |
| BF-12 | T1.1.9 | production marker + missing/invalid authority | `NOT_AUTHORIZED` before connect | connect/open/read count=0 | I/O spy trace digest |
| BF-13 | T1.1.10/11 | fault after every batch boundary | retry final rows/counts/digests equal clean run | duplicate/conflict overwrite=0 | per-boundary replay digest |
| BF-14 | T1.1.13 | injected failure M1–M7 | V1 schema/data digest identical M0 | reverse write/drop formal table=0 | rollback stage matrix |
| BF-15 | T1.1.11/14 | copied artifact + relocated checkout | canonical/copy verify same | DB/network I/O=0 | artifact/copy verification digest |

### 6.3 Exact command progression

任务解锁后，每个主结果只执行其最小命令，然后再执行累计回归：

```text
T1.1.1  pytest -p no:cacheprovider tests/test_raw_v2_contracts.py
T1.1.2  pytest -p no:cacheprovider tests/test_raw_v2_policy.py
T1.1.3  pytest -p no:cacheprovider tests/test_raw_v2_schema.py
T1.1.4-5 pytest -p no:cacheprovider tests/test_raw_v2_object_event_store.py
T1.1.6  pytest -p no:cacheprovider tests/test_raw_v2_binding_store.py
T1.1.7  pytest -p no:cacheprovider tests/test_raw_v2_tombstone.py
T1.1.8  pytest -p no:cacheprovider tests/test_raw_v2_public_projection.py
T1.1.9-11 pytest -p no:cacheprovider tests/test_raw_v2_migration.py tests/test_raw_v2_artifact.py
T1.1.13 pytest -p no:cacheprovider tests/test_raw_v2_migration_rollback.py
```

每轮还必须 `ruff` 对当前变更文件全绿；关闭 T1.1.14 前运行完整 Python 3.12/3.13 backend、frontend
locked test/typecheck/build、G0 admission exact/cleanroom 和 G1 artifact copy/tamper verify。命令失败或
测试数减少时保留失败 artifact，不修改断言或排除项。

### 6.4 Requirement evidence row

G1 artifact 中每条 BF row 固定包含：

```text
requirement_id
spec_sha256
implementation_paths[]
implementation_set_sha256
test_ids[]
fixture_set_sha256
command_ids[]
result = PASS|FAIL|HOLD|NOT_RUN
assertion_count
negative_case_count
before_state_sha256
after_state_sha256
side_effect_invariant_passed
evidence_sha256
review_status
limitations[]
```

`PASS` 只有 test/command/evidence/review 都存在，且 side-effect invariant 通过。缺任何字段只能
`HOLD/NOT_RUN`；文档中的“设计完成”不能填 PASS。

## 7. Entry Packet verifier 与负向合同

当前实现入口：

```text
python -m evidence_rag.evaluation.maturity_g1_entry_v1 \
  ENTRY_PACKET --at UTC_MICROSECOND_Z \
  --attestations BUNDLE --keyset KEYSET \
  --trusted-keyset-sha256 OUT_OF_BAND_DIGEST \
  --g0-admission G0_MANIFEST
```

也可使用 `make g1-entry-verify` 并显式传入五个 `G1_ENTRY_*` 变量和
`G0_ADMISSION_MANIFEST`。验证器只读这些文件，既不获取 Git provider 数据，也不连接数据库、网络或
生产系统；成功输出 `rag-g1-entry-verification-v1` canonical JSON，并始终记录
`network_accessed=false`、`database_accessed=false`、`production_database_accessed=false`、
`release_authorized=false`。

至少覆盖：

1. local run 冒充 remote；
2. 3.12/3.13 来自不同 revision；
3. rerun attempt 覆盖旧失败；
4. screenshot/badge 无日志摘要；
5. `approved=true` 无外部 attestation；
6. attestation 过期或 revision 不同；
7. D1–D5 有 PENDING/REJECTED；
8. source snapshot 与 admission/CI 不同；
9. tracked generated blocker 大于 0；
10. supply-chain exception 无 expiry/owner/forbidden stage；
11. production DB authorization 由请求或环境变量自声明；
12. packet 含 absolute path、token、cookie、secret、raw row/ACL/URI；
13. canonical JSON duplicate key/NFC collision/tamper；
14. default engine/release authority 被改变；
15. copied packet 与原 packet digest 不同。

当前 27 个 pytest case 已覆盖以上风险的协议核心，并额外覆盖：带外 keyset digest 不匹配、Ed25519
签名篡改、同钥 owner/independent review、G0 原生 schema/policy 伪造、G0/generated/supply 聚合字段
改写、已签名 source snapshot 不一致、已签名 generated blocker、过期 supply exception、完整 release
stage 禁止集合、生产授权自报、runtime/default 改变以及 CLI canonical 输出。测试还用 I/O spy 证明成功
verify 不发起 network、database 或 subprocess 调用。

外部 attestation 的真实性只由预先提供的受控 receipt/keyset 和带外 trust digest 离线验证。在线获取
receipt 是独立、显式、只读采集步骤，不能隐藏在 verify。测试中固定私钥只用于协议夹具，不是准入
信任材料，不得复制到真实 keyset。

后续 handoff 由 `31_G1_ENTRY_SIGNING_HANDOFF_AND_ASSEMBLY_RUNBOOK.md` 接续：proposal 先被强制转换为
无 receipt 的 PENDING draft；只有非签名事实全部就绪且 request time 不早于事实完成，才生成
`rag-g1-entry-signing-request-bundle-v1`；assembly 要求每个 request 恰有一张 request 后签发的外部
receipt，并在输出后再次调用本 verifier。handoff 工具没有 keygen/sign/fetch 权限。

## 8. Historical target snapshot（superseded）

### 已关闭的设计与工具结果

`WP-G1D-04 Entry and BF trace packet`：Entry E-01–10、artifact envelope、D1–D5、data authorization、
BF-01–15 test/evidence/side-effect matrix 和 negative tests 已冻结。

`WP-G1D-05 Offline Entry verifier`：canonical parser、domain digest、Ed25519/keyset、receipt set、职责
分离、G0 原生验证、CI aggregate binding、E-01–10 重算、CLI 和 fail-closed 负向测试达到
`LOCAL_ENGINEERING_PASS`。这只关闭“能否机器判定”，不生成任何真实审批。

`WP-G1D-06 Signing handoff and assembly`：PENDING draft、fact-readiness、15 项基础 signing request、
request/receipt 时间防重放、一一 receipt matching、assembly→verifier 双重判定达到
`LOCAL_ENGINEERING_PASS`；真实 request/receipt/trust anchor 仍未产生。

`WP-G0-05K npm install-script admission`：exact npm、strict approval、lock graph/registry/integrity、
pending=0、隐藏 `.npmrc` source admission 与远程 artifact 合同达到 `LOCAL_ENGINEERING_PASS`；这只
补强 E-06 的本地事实，不产生 same-revision remote receipt。

`WP-G0-05L Owner review packet`：逐 path/scope/exclusion/action 的 canonical PENDING queue、native G0
重算和 substitution/drift/tamper 拒绝达到 `LOCAL_ENGINEERING_PASS`；packet 的 false/null/PENDING
边界不能被填写成 E-01，真实 protected revision approval 仍缺失。

### 当时的唯一外部目标

owner/architecture/security/data owner 使用本文合同产生可外部验证的 Entry Packet，并让当前最新 G0
candidate bytes 进入受控 revision、远程 CI。当前 packet 必须保持 `PENDING`；仓库不能自签
APPROVED，也不能把合成全绿测试夹具当成 authority。

### 当时解锁后的唯一实现目标

仍是 `T1.1.1 C1–C10`。Entry Packet 只解锁该任务，不解锁 schema、DB、gateway、Wiki V2、
dual-write、shadow、canary 或 release。

## 9. Current Entry input resolution

当前 Entry input 从 `artifacts/rag-maturity/control/CURRENT_TASK_STATE.md` 解析；该 record 只负责定位对象，不能
把缺失的 protected/remote/external authority 改成 PASS。解析后必须以本 verifier 原生重算 exact admission、
revision/tree、generated boundary、supply artifact、receipt/keyset/trust、D1–D5、data authorization、independent
review 与 runtime boundary。

record 缺失、无效、对象不可达或绑定矛盾时，当前开发状态为
`CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`，Entry 结论为 `ENTRY_NOT_SATISFIED`；不得回退
到 §8 或任一历史 candidate。只有 G0 `QUALIFIED` 且 E-01–E-10 全部由真实 evidence `PASS`，才解锁
T1.1.1 C1–C10；该 Entry 不解锁其后的 schema/DB/gateway/Wiki/dual-write/shadow/canary/release。
