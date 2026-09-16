# G1 六源原始证据权威设计复核

日期：2026-08-06  
复核类型：`FIRST-PARTY ADVERSARIAL DESIGN REVIEW`  
复核对象：`13_G1_RAW_EVIDENCE_AUTHORITY_DESIGN.md`  
结论：`DESIGN ENGINEERING_PASS / OWNER_REVIEW_HOLD / IMPLEMENTATION_HOLD`  
默认运行：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. 复核范围

本次只读检查以下真实实现边界，不执行 schema/runtime 修改，不读取或写入正式数据库：

- raw schema/store/service/router/models；
- Code、Codex、Experiment、Notebook、Document、Workspace ingestion 和 runtime binding；
- Wiki source-ref/compiler/store/evidence gateway/navigator；
- EvidenceFact 与 claim citation verifier；
- G1 locator/selector/shared authority/迁移/测试设计。

本复核证明设计候选已覆盖当前已知工程风险，不构成 owner 的版本准入、数据授权、安全审批或
生产发布批准。

后续表级、事务级、迁移状态机和 dry-run artifact 已在
`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` 冻结，并由
`reviews/22_G1_BINDING_FOUNDATION_EXECUTION_SPEC_REVIEW_2026-08-06.md` 独立复核；该补充不改变
本文的 owner/G0 implementation hold。

## 2. 结论摘要

原始 G1 方向正确，但若只新增 `RawEvidenceLocatorV2` 和六个 gateway 仍不安全。shared raw
底座本身存在跨项目 identity、事件幂等、内部路径出站、claimed digest、reference-only、
projectless tombstone/blocked entity 等问题。设计已修订为先做 project-scoped raw/binding
foundation，再实现六源 gateway；这是 G1 实现的 mandatory critical path。

当前准确结论：

```text
Wiki navigation engineering: available
Wiki raw authority: not available
Six-source raw design: engineering reviewed
Raw foundation implementation: not started
G0 version admission: not complete
G1 implementation: locked
Release: no release
```

## 3. Findings

### P0-01：Wiki V1 的 raw read 实际只读 candidate envelope

证据：

- `rag/wiki/evidence_gateway_v1.py:44-53` 用 `source_ref.raw_content_sha256` 读取
  `wiki_source_candidates_v1`，然后调用 `CandidateRawEvidenceReaderV1`；
- `rag/wiki/compiler_v1.py:382-398` 把整个 `MultiSourceCandidateV2` 的 digest 写入名为
  `raw_content_sha256` 的字段。

影响：无法检测来源 ingestion、parser、formal projection 或 inventory summary 在进入 Wiki 前
已经产生的错误；“raw verify”这个名字会造成错误质量声明。

设计处置：V1 ref 只用于导航；strict raw 模式返回 `legacy_unbound`；V2 ref 绑定 protected raw
locator、raw digest 和 selected digest；不原地升级旧 generation。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

### P0-02：raw object logical identity 可跨项目/ACL 碰撞

证据：

- `sources/schema.py:23` 的 unique key 不含 project/source type/ACL；
- `sources/service.py:110-113` 的 raw ID 只含 source instance/object/version/digest；
- `sources/store.py:36-37,58-68` 的 conflict 与回读条件同样不含 project。

影响：两个项目以相同 source identity 和内容写入时，后者可能得到前者的 raw row、project 和
ACL。任何基于该 row 的 locator 都无法证明项目隔离。

设计处置：G1I.1 引入 project/visibility-scoped V2 logical identity；物理 blob 去重与 metadata
authority 分离；歧义 V1 row quarantine，不自动选 first/latest。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

### P0-03：source event idempotency 可跨项目错误去重

证据：`sources/service.py:55-64` 的 hash 不含 project/source type/ACL；
`sources/store.py:81,105-108` 按这个全局 key 冲突并返回既有 event。

影响：不同项目的相同来源事件可能被视为 duplicate，并返回另一项目 event/raw binding。

设计处置：V2 idempotency 加入 project/source type/ACL；跨项目 collision 和 migration tests 为
hard gate。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

### P0-04：Raw/Event API 直接出站内部文件系统路径

证据：

- `sources/service.py:80` 默认把 raw `storage_path` 写为 event `payload_ref`；
- `sources/service.py:87` 返回完整 event/raw store row；
- `sources/router.py:19,29-33,44-48,57-60,72-75` 直接返回这些 dict；
- raw row 自身在 `sources/schema.py:15` 保存 `storage_path`。

影响：授权 API 用户可以看到服务器 absolute path 和内部 payload reference；store schema 与
public response 没有边界。

设计处置：内部/public Pydantic models 分离；所有 endpoint 使用 allowlist projection；移除
storage path、内部 payload ref、本地 URI 和未脱敏 metadata；增加 response contract tests。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

### P0-05：Writer 接受未复核的 claimed content hash

证据：`sources/service.py:107-109` 在传入 `content_hash` 时直接采用；随后以该 digest 命名 path
并写入 payload，未比较实际 bytes digest。

影响：内部 ingestion bug 或恶意调用可产生“内容地址正确、实际 bytes 错误”的伪 raw object。
Notebook 当前读路径会发现，但其他路径没有统一 reader。

设计处置：V2 writer 总是自行计算 bytes digest；调用方 digest 只作为 expected value，不同立即
拒绝。迁移扫描 existing path/raw digest 并报告 mismatch。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

### P0-06：Reference-only object 被标为 active/persisted

证据：`sources/models.py:27-31` 接受 payload 或 payload_ref；`sources/service.py:24-30` 在无 payload
时 hash reference string，`persist_bytes` 产生无 storage 的 active raw object，event 状态仍为
persisted。

影响：active/persisted 被误解为可回读内容；引用字符串 digest 不能证明外部 payload。

设计处置：V2 增加 `reference_only` 语义；只有已取回 bytes 或 canonical record 能创建 evidence
binding；read 返回 `storage_unavailable`，绝不回退 Wiki snippet。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

### P0-07：get/tombstone/blocked/stats 没有完整 project scope

证据：

- `sources/store.py:142-176,178-189` 按 raw ID 操作，不带 project；
- `sources/router.py:51-75` by-id/tombstone 请求也不要求 project；
- `sources/schema.py:71-76` blocked entity 以 `entity_id` 全局主键；
- raw stats 的 blocked count 在 store 中没有 project 过滤。

影响：共享 ACL、非全局 entity ID 或 raw ID collision 下可能跨项目读取/阻断/统计泄漏。

设计处置：所有 store/API 在 SQL 层带 project；blocked V2 主键为 `(project_id, entity_id)`；
public ACL object 也不能绕过 project membership。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

### P0-08：Workspace 不存在 raw source authority

证据：`sources/models.py:10` 的 source type 不含 workspace；Workspace service/mutation 路径没有
调用 `RawSourceService` 或 `link_derivations`。

影响：Workspace current/as-of/state claim 无法从第六类 raw source 回读；当前表行会覆盖历史。

设计处置：append-only canonical state event + valid/observed time + transaction/durable outbox +
project-scoped binding；binding 未完成时 strict claim 不可用。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

### P1-09：既有 derivation 主键不能表达精确 generation/selector

证据：`sources/schema.py:57-69` 的 PK 不含 generation，且没有 retrieval unit 或 selector；
`sources/store.py:123-128` 冲突时 `DO NOTHING`。

影响：同 raw/entity/derivation version 跨 generation 可能保留旧 generation；无法从 entity 唯一
定位 line/cell/page/table 片段。

设计处置：不改变 V1 表语义，新增 `raw_evidence_bindings_v2`，包含 retrieval unit、generation、
selector 和 selected digest；歧义拒绝。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

### P1-10：Document/Codex/Experiment 的聚合事实缺可重放原件

证据：Document 从 binary raw 派生文本但没有 parser artifact binding；Codex thread inventory 只有
metadata/count summary；Experiment-level inventory 来自 formal aggregate，现有 raw derivation 主要
绑定 run/metric/artifact。

影响：summary 可导航但不能直接产生 observed fact；parser/aggregation 漂移无法检测。

设计处置：Document 内容寻址 parse artifact；Codex ordered thread manifest 或多 item citation；
Experiment snapshot 或多 run citation。未具备者保持 derived/reported。

状态：`DESIGN_RESOLVED / IMPLEMENTATION_PENDING`。

## 4. 设计完整性检查

| 检查项 | 结果 | 备注 |
|---|---|---|
| 六源均有明确 raw root 或明确新增路径 | PASS | Workspace 需新 event；Document 需 parse artifact |
| raw/candidate/page 权威分层 | PASS | V1 navigation-only |
| project/ACL/version/generation/state 校验 | PASS | 固定校验顺序和错误码 |
| storage path/symlink/digest 防护 | PASS | 抽取 Notebook 逻辑为 shared reader |
| selector 类型和边界 | PASS | 禁止任意脚本/regex/path selector |
| legacy migration | PASS | rebuild new generation；不 in-place |
| positive/negative/fault/security tests | PASS | shared + 每源 14 negatives + E2E |
| rollback/partial migration | PASS | 复制/对账/切 reader；歧义 quarantine |
| 默认 V1 与 release boundary | PASS | G1 不授权 active V2 |
| clean revision prerequisite | HOLD | G0 owner action 未完成 |

## 5. Mandatory implementation order

```text
G0 version admission
  → owner/architecture review of G1 design
  → G1I.1 project-scoped raw/event/binding schema + migration
  → G1I.2 secure reader/writer + public projection
  → six source gateways
  → WikiSourceRefV2/compiler/store/navigator
  → backfill dry-run/rollback
  → G1 portable package and independent Gate Review
```

禁止把六源 gateway 建在旧 raw identity 上；禁止先改 Wiki 字段再补 raw foundation；禁止一次提交
同时包含 identity migration、六源 gateway 和 active Wiki pointer 切换。

## 6. 复核判定

`WP-G1D-01` 达到 first-party `ENGINEERING_PASS`：当前实现缺口、目标合同、六源 authority、迁移、
失败模式、测试和 Gate 已形成可实施设计，且对抗性复核中新发现的 P0 已纳入 critical path。

仍有两个 hard hold：

1. `WP-G0-05` owner-reviewed revision + clean checkout + remote CI 未完成；
2. G1 design 尚无 owner/architecture approval。

因此不得开始 `WP-G1I-01`，不得改变默认 V1，不得更新 production release authority。
