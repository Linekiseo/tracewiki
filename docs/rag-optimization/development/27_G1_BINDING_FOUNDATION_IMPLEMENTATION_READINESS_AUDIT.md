# G1 Binding Foundation 实施就绪审计、代码映射与原子目标

版本：2026-08-06 V1  
状态：`READINESS_CONTRACT_PASS / ENTRY_HOLD / RUNTIME_NOT_STARTED`  
上位目标：`10_RAG_WIKI_MATURITY_PROGRAM.md`、`14_RAG_WIKI_STAGE_OBJECTIVE_BREAKDOWN.md`  
权威设计：`13_G1_RAW_EVIDENCE_AUTHORITY_DESIGN.md`  
执行规格：`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` V2  
Entry/requirement packet：`28_G1_ENTRY_DECISION_AND_REQUIREMENT_TRACE_PACKET.md` V2  
Entry verifier evidence：`30_G1_ENTRY_OFFLINE_VERIFIER_EXECUTION_RECORD.md`  
当前默认：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`  
数据边界：`READ-ONLY SOURCE AUDIT / FORMAL DATABASE NOT ACCESSED`

## 1. 审计结论

当前 RAG、Wiki 和多源能力不能判定为 production mature。

- 六个 source runtime、multi-source candidate/planner/evaluation、Wiki compile/store/search/navigation 和
  grounded-answer verifier 已有大量仓库内工程实现与测试，属于“工程路径存在”；
- Wiki 当前能固定 project、generation、ACL 和 candidate envelope digest，能证明编译快照自洽；
- Wiki 的所谓 raw read 实际回读 `wiki_source_candidates_v1.candidate_json` 中的 candidate snippet，
  不是 `raw_objects` bytes，也没有 selector/selected digest/binding；
- V1 raw/event identity、by-id/tombstone、blocked entity 和 public projection 不能满足跨项目、路径隐藏、
  精确绑定与失败关闭要求；
- 因而“页面可导航”“candidate digest 正确”和“答案引用存在”都不能替代 raw-grounded claim。

多源方向是合理的：六源域、generation、ACL、typed locator、role/task/planner 和冲突/陈旧性政策已经
形成结构化框架。但当前数据平面仍以 candidate/derived record 为中心，缺少共同 raw authority；在
G1/G2 完成前，多源召回的丰富度不能提升事实权威等级。

当前精确判定：

| 层 | 已证明 | 尚未证明 | 判定 |
|---|---|---|---|
| 单源仓库工程 | 六源 runtime/fixture/evaluation 路径 | 真实 corpus、正式迁移、长期运行 | `ENGINEERING_COMPLETE / QUALITY_HOLD` |
| 多源规划与融合 | typed candidate、planner、role/budget、冲突/陈旧性测试 | 统一 raw binding、统一 as-of、生产校准 | `DESIGN_REASONABLE / AUTHORITY_INCOMPLETE` |
| Wiki 组织与导航 | generation/ACL/candidate snapshot 自洽 | 六源 raw read-back、V2 ref、strict refusal E2E | `NAVIGABLE / NOT_RAW_GROUNDED` |
| Grounded answer | claim/citation verifier 框架 | citation→binding→bytes→selector 的完整证明 | `FRAMEWORK_PRESENT / NOT_QUALIFIED` |
| 可复现交付 | V16 本地 cleanroom、双 Python 2104、Entry verifier；frontend 184 | owner revision、远程 CI/audit/SBOM、页面交互补证 | `LOCAL_ENGINEERING_PASS / G0 HOLD` |
| 生产发布 | 默认 V1，release gate 保持关闭 | shadow/canary/SLO/rollback/owner approval | `NO_RELEASE` |

## 2. 审计边界与方法

本轮只读取源码和文档，未连接任何 SQLite 数据库、正式数据源、远程服务或 Git remote；未创建 V2
表、未运行 migration、未改变 runtime/config/API/default pointer。

审计问题固定为：

1. `15` 文档的字段、ID、事务、migration 和 reason 是否能直接映射到代码；
2. 现有同名 binding 是否会误导实现边界；
3. Wiki ref 是否真正绑定 raw bytes；
4. reference-only、ACL visibility、event idempotency 和 public projection 是否自洽；
5. 解锁后的第一个任务是否可以在不做隐含架构选择的情况下独立完成；
6. 每个阶段的输入、输出、验收、负向用例、停止和回滚是否明确。

## 3. 规格到当前代码的精确映射

### 3.1 Raw object 与 event

| 规格要求 | 当前实现证据 | 缺口/风险 | 后续唯一任务 |
|---|---|---|---|
| project/source/ACL 进入 logical identity | `sources/service.py` raw ID 只含 instance/object/version/digest | 跨 project/ACL 同 ID | T1.1.1–2、T1.1.4 |
| raw unique project-scoped | `sources/schema.py` unique 缺 project/source/ACL | conflict 回读可拿到别项目 metadata | T1.1.3–4 |
| bytes digest 由 service 计算 | `persist_bytes(content_hash=...)` 接受覆盖值 | 伪内容地址、错误 byte_length | T1.1.4 / BF-02 |
| reference-only 不可 binding | payload_ref 无 bytes 仍产生 active object | “active”不可读 | T1.1.3–4 / BF-03 |
| exact event idempotency | V1 key 缺 project/ACL/content/mutation；conflict `DO NOTHING` | different payload 被报 duplicate | T1.1.1、T1.1.5 / BF-04 |
| project-scoped get/tombstone | `get_object(id)`、`tombstone(id,...)` 不带 project SQL predicate | 先读后授权、跨 project identity 风险 | T1.1.7–8 |
| public projection | router 直接返回 store dict | `storage_path/payload_ref/source_uri/metadata` 出站 | T1.1.8 / BF-05 |
| project-scoped blocked stats | blocked entity 为 global PK；stats 全表计数 | 跨项目 block/计数泄漏 | T1.1.7 / BF-09 |

### 3.2 Raw derivation 与“现有 Binding”命名冲突

`src/evidence_rag/bindings/` 当前实现的是 Codex change → repository/file/entity 的候选关系与人工复核，
其表为 `binding_scans/binding_candidates/unmatched_changes`。它不是 G1 的 raw evidence binding：没有
raw object、selector、selected digest、visibility、valid interval 或 exact locator resolve。

因此：

- 不在现有 `bindings/` 上追加 G1 字段；
- G1 foundation 固定进入新 `src/evidence_rag/sources/v2/`；
- `binding_candidates` 的 reviewed relation 未来可成为 derived/review 输入，但不能直接成为
  `raw_evidence_bindings_v2`；
- 文档、类名和 API 必须使用 `raw evidence binding`，避免把关系候选误报为 raw authority。

V1 `raw_derivations` 的主键不含 generation/retrieval unit/selector，且 store 没有 exact resolve API；
它只能作为 migration scanner 的输入事实，不能原地升级。

### 3.3 Wiki source ref 与 raw read

当前链路为：

```text
MultiSourceCandidateV2
  → compiler 计算整个 candidate envelope SHA-256
  → WikiSourceRefV1.raw_content_sha256
  → wiki_source_candidates_v1.candidate_json
  → StoreRawEvidenceGatewayV1
  → CandidateRawEvidenceReaderV1
  → candidate.snippet
```

代码证据：

- `rag/wiki/compiler_v1.py::_source_ref` 把 candidate envelope digest 写入
  `raw_content_sha256`；
- `rag/wiki/store_v1.py::read_source_candidate` 只从 Wiki 专用库读取 `candidate_json`；
- `rag/wiki/evidence_gateway_v1.py` 的 reader 以 candidate digest 查询；
- `rag/wiki/navigator_v1.py::CandidateRawEvidenceReaderV1` 最终使用 `candidate.snippet` 生成 fact。

该链路验证了 Wiki snapshot/candidate integrity，但没有验证 source raw bytes。V1 ref 还缺
`raw_object_id`、binding/selector digest、selected digest、stable version 和 exact retrieval unit
membership。因此 G1 不修改 V1 ref；G2 之前也不允许把现有 read 命名为 strict raw authority。

### 3.4 可复用基础

| 基础 | 可复用内容 | 不可直接复用内容 |
|---|---|---|
| `SQLiteStore` | FK on、busy timeout、`BEGIN IMMEDIATE` transaction | V1 schema/identity/API projection |
| Notebook safe reader | root containment、symlink/regular/digest 经验 | 私有接口、source-specific path 字段 |
| WikiStoreV1 | project/generation/ACL candidate snapshot、自校验 | candidate snippet 作为 raw bytes |
| 六源 V2 runtimes | source generation、typed unit、部分 lineage | 不完整 raw selector/binding/共同 reader |
| answer verifier | claim/citation/fact membership 框架 | 未经 raw binding read-back 的 authority 升级 |

## 4. 本轮发现并关闭的规格缺口

| ID | 原规格缺口 | 风险 | V2 处置 | 状态 |
|---|---|---|---|---|
| RF-01 | reference-only 无 bytes，但 raw digest/length `NOT NULL` | 实现者伪造 empty digest 或把 ref 标 active | digest/length nullable；active 与 reference-only CHECK 分离 | `SPEC_FIXED` |
| RF-02 | `H(...).hex` 未定义 | 不同实现保存有/无 `sha256:` 前缀 | 冻结 CJSON/H_HEX/H 和 ID 规则 | `SPEC_FIXED` |
| RF-03 | event idempotency 有 digest，无 event ID 算法 | 随机 UUID 破坏重放 | `source-event-v2:<idempotency hex>` | `SPEC_FIXED` |
| RF-04 | object visibility 与 requester ACL visibility 混写 | digest 复用、跨 scope 误判 | object=project+single ACL；request scope 使用不同 domain | `SPEC_FIXED` |
| RF-05 | V1 source type → V2 domain 未冻结 | dvc/manual 被按名字猜测 | 六项显式映射；其余 owner mapping/re-ingest | `SPEC_FIXED` |
| RF-06 | canonicalizer 可隐式 `str()`/对象转换 | 跨运行时 digest 漂移 | 限定 JSON value domain、NFC collision/duplicate-key 拒绝 | `SPEC_FIXED` |
| RF-07 | 字段/JSON 大小未给具体值 | DoS 与各层上限漂移 | 冻结默认 byte limits；source 更小上限优先 | `SPEC_FIXED` |
| RF-08 | reason 分散在设计文字 | store/router 临时造字符串 | 新增 foundation reason registry，shared reader 沿用设计 registry | `SPEC_FIXED` |
| RF-09 | 上位设计仍引用 V1 raw table/absolute storage path/nullable generation | 实施取错权威 | 对齐 V2 table、relative key、mandatory generation | `SPEC_FIXED` |

这些修正只更新合同，没有创建实现证据。原 review 22 的 `SPEC ENGINEERING_PASS` 应读取为“V1 设计
方向通过”；V2 readiness 结论由本文和 review 27 接续。

## 5. Owner/Architecture 决策包

五项都必须对明确选项签署；无回复不等于接受推荐值。

| 决策 | 推荐选项 | 必须确认的影响 | 偏离时额外证据 |
|---|---|---|---|
| D1 schema 迁移 | 并列 V2 表，不改 V1 | 可独立回滚；旧 ref 不重写 | ADR + V1 引用修复 + rollback proof |
| D2 public object | 仍按 project 隔离 logical authority | 不泄漏跨项目内容相等性/metadata | 威胁模型 + side-channel matrix |
| D3 不可恢复历史 | source re-ingest，不猜 owner/ACL/generation | coverage 可能暂时下降但证据真实 | 数据修复规则 + independent review |
| D4 blob location | DB 只存 relative content key，root 配置化 | public/API/artifact 不含 path | 搬迁/路径泄漏/恢复证明 |
| D5 cutover | project + source + intent 白名单 | 可局部 shadow/rollback | 更细或更粗粒度的 fault isolation proof |

签署记录至少包含 decision、approver role、reviewed revision、date、constraints、rollback owner 和
evidence URI/digest；不得把个人姓名或外部链接写进 portable artifact 的 authority payload。

## 6. G1I-01 原子实施序列

### 6.1 解锁条件

以下任一不满足，代码目标保持 `NOT_STARTED`：

1. V16 或后续同内容进入 owner-reviewed revision，并使 G0 达到 `QUALIFIED`；
2. fresh checkout 的 Python 3.12/3.13 remote CI 与 admission exact verify 通过；
3. tracked generated output 边界由 owner 正式关闭；
4. D1–D5 全部有受审结论；
5. 第一个迁移输入明确为 fixture 或受授权脱敏 mirror；
6. 供应链 blocker 已修复且 same-revision remote audit/SBOM 通过，或有 owner/security 签署、限时且禁止发布的受限例外；
7. Entry Packet E-01–10 全部 PASS，且 runtime/default/API/正式数据库授权不随“开始 G1”自动改变。

### 6.2 T1.1.1 canonical contract 的细分目标

一次 revision 只关闭这一主结果，不创建表、不写 blob、不接 router。

| 子目标 | 允许文件 | 完成条件 | 必须负向验证 |
|---|---|---|---|
| C1 frozen JSON value model | `sources/v2/contracts.py` | extra forbid；只接受冻结值域 | float/Decimal/bytes/object/set/tuple |
| C2 recursive NFC | 同上 | key/value NFC；排序稳定 | normalized duplicate key |
| C3 strict JSON loader | 同上 | duplicate raw key、NaN/Infinity 拒绝 | parser 与 direct model parity |
| C4 canonical time | 同上 | UTC microsecond `Z` 唯一输出 | naive/invalid/offset boundary |
| C5 domain digest | 同上 | H/H_HEX 与 golden vectors 一致 | domain swap、prefix/case mismatch |
| C6 identity models | 同上 | visibility/logical/event/selector/binding payload extra forbid | missing/null/unknown field |
| C7 typed IDs | 同上 | raw/event/locator deterministic | random UUID、double prefix |
| C8 source-domain registry | 同上 | 六项默认映射；unknown 拒绝 | dvc/manual 不猜测 |
| C9 size/reason registry | 同上 | byte limits 与 reason enum 冻结 | multibyte boundary、unknown reason |
| C10 portable vectors | `tests/test_raw_v2_contracts.py` + fixture | 3.12/3.13、locale/checkout 独立相同 | copied fixture tamper |

T1.1.1 evidence 至少包含 contract version、golden vector set digest、Python versions、test command/result、
源码 digest 和 limitation；不得包含本机绝对路径。任一 digest 跨 Python/locale/checkout 不同，整个
任务失败，后续任务不得开始。

### 6.3 T1.1.2–14 主结果与依赖

| 顺序 | 主结果 | 依赖 | 单一关闭证据 | 禁止混入 |
|---:|---|---|---|---|
| 2 | ACL/visibility policy adapter | T1.1.1 + D2 | 2 project × public/private/denied matrix | schema/store |
| 3 | V2 schema initializer | T1.1.2 + D1/D4 | exact sqlite_master/FK/CHECK/index/re-init | V1 ALTER/runtime |
| 4 | blob/object writer | schema | expected digest、dedupe、collision、orphan tests | event/binding router |
| 5 | event writer | object writer | cross-project/same-key conflict/no side effect | dual-write |
| 6 | exact binding store | object/event | unique/ambiguous/generation/selector digest | source gateways |
| 7 | tombstone propagation | binding store | controlled read race + project block | production retention |
| 8 | public projection | store/service | create/list/get/tombstone/error allowlist | direct row output |
| 9 | fixture-only scanner | T1.1.1–8 + D3 | every V1 category + NOT_AUTHORIZED-before-connect | safe copy |
| 10 | safe copier/checkpoint | scanner | crash/retry/equal-vs-conflict | V1 overwrite |
| 11 | reconciliation/verifier | copier | conservation/P0/tamper/copy verify | cutover claim |
| 12 | dual-write/shadow config | G0/owner + separate gate | default off/reviewed scope/typed mismatch | request toggle/default change |
| 13 | rollback drill | M1–M7 fixtures/mirror | V1 schema/data digest unchanged | reverse write to V1 |
| 14 | independent review | all BF evidence | requirement→test→artifact 100% | self-review substitute |

每一主结果完成后先复核副作用：new row/blob/audit 泄漏必须为 0；再进入下一项。T1.1.9–11 的
fixture/mirror migration 仍不授权 M6–M8。

## 7. G1 到 G9 的目标衔接

全阶段完成定义仍以 `14` 文档为准；本审计增加以下硬衔接：

| 阶段 | 不可替代的输入 | 交给下一阶段的 authority | 禁止提前声称 |
|---|---|---|---|
| G1 Raw authority | G0 admission + D1–D5 | 六源 locator/binding/read refusal package | Wiki grounded |
| G2 Live Wiki | G1 六源 positive/refusal | V2 ref + raw-verified Evidence Pack | planner/as-of unified |
| G3 Planner/as-of | G2 trace | canonical plan + pinned snapshots | real corpus quality |
| G4 Real materialization | owner-governed corpus | six non-empty governed generations | metric qualification |
| G5 Independent evaluation | frozen real corpus | independent baseline + CI regression | fusion calibrated |
| G6 Fusion calibration | independent metrics | calibrated joint policy + abstention | performance/SLO |
| G7 Performance | qualified quality policy | capacity/concurrency/fault evidence | production canary |
| G8 Shadow/canary | release artifact + owner window | rollback-tested scoped qualification | default V2 |
| G9 Operations | G8 qualification | freshness/drift/deletion/audit operations | permanent completion |

任何阶段只有代码或只有文档都不算完成；必须同时具备 contract、tests、运行 artifact、review 和
rollback evidence。

## 8. 当前目标重设

### 当前内部目标（已由本审计关闭）

`WP-G1D-03 Implementation readiness reconciliation`：

1. 逐文件确认 V1 raw/binding/Wiki 的真实边界；
2. 关闭 reference-only、H/event ID、visibility、source mapping、canonical value、size/reason 矛盾；
3. 冻结 T1.1.1 的十个子目标和 T1.1.2–14 的单结果序列；
4. 不连接数据库、不修改 runtime、不开始 T1.1.1。

### 当前外部目标（唯一 Gate）

`WP-G0-05 Owner version admission`：形成受审 revision，执行 fresh checkout remote CI，并签署
D1–D5。该目标需要 owner/远程环境，当前仓库不能伪造完成证据。

### 解锁后的唯一代码目标

`T1.1.1 C1–C10 canonical JSON/digest/ID/reason contract`。完成前不得创建 V2 schema；完成后也只
进入 T1.1.2，不并行实现六源 gateway、WikiSourceRefV2、dual-write 或 cutover。

## 9. Readiness checklist

| 检查 | 当前 |
|---|---|
| V1 逐文件风险可定位 | PASS |
| Wiki candidate read 与 raw read 已区分 | PASS |
| existing binding 与 raw evidence binding 已区分 | PASS |
| reference-only schema 自洽 | PASS（spec）/ NOT IMPLEMENTED |
| canonical hash/ID/event 形式唯一 | PASS（spec）/ NOT IMPLEMENTED |
| object/request visibility 分层 | PASS（spec）/ OWNER D2 HOLD |
| source mapping fail closed | PASS（spec）/ adapter 未实现 |
| reason/size boundary 冻结 | PASS（spec）/ NOT IMPLEMENTED |
| D1–D5 | HOLD |
| G0 owner revision/remote CI | HOLD |
| 正式数据库授权 | NOT AUTHORIZED |
| T1.1.1 runtime | NOT STARTED |

最终判定：规格已达到“入口条件满足后可以开始第一个原子任务”的粒度；当前入口条件尚未满足，
继续保持 `ENTRY_HOLD / DEFAULT_V1 / NO_RELEASE`。

后续 `WP-G1D-05` 已把 Entry 合同实现为离线 fail-closed verifier，并关闭 packet 聚合自报与 G0
局部验证缺口；这提升的是 Gate 可判定性，不改变本审计的 `ENTRY_HOLD`，也没有启动 T1.1.1。
