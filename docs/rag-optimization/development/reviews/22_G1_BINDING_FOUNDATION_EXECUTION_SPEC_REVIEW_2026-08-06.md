# G1I-01 Binding Foundation 可执行规格复核

日期：2026-08-06  
复核类型：`FIRST-PARTY ADVERSARIAL EXECUTION-SPEC REVIEW`  
复核对象：`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md`  
结论：`SPEC ENGINEERING_PASS / OWNER_DECISION_HOLD / IMPLEMENTATION_BLOCKED_BY_G0`  
运行边界：`DESIGN ONLY / FORMAL DB NOT ACCESSED / DEFAULT_V1 / NO_RELEASE`

> 历史边界：本 review 对应 execution spec V1。后续只读 contract→code 审计发现并关闭了
> reference-only nullable、H/event ID、visibility/source mapping 等 residual gap；current authority 为
> `15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` V2、`27` readiness audit 与 review 27。

## 1. 复核问题

本次复核不重新评价 G1 的总体方向，而是检查规格能否让实现者在不猜测的情况下回答：

1. 新表到底保存什么，哪些字段参与 authority；
2. 同 bytes 在不同 project/ACL 下是否会错误共享 metadata；
3. writer、binding、tombstone 的原子边界是什么；
4. V1 已经造成的信息丢失能否被诚实识别；
5. migration dry-run 怎样证明全分类、守恒、可重入和可回滚；
6. 哪些 owner 决策必须在代码前完成；
7. 失败时是否可能回退到 V1/Wiki snippet 并伪装成功。

只读证据来自 `sources/schema.py`、`sources/models.py`、`sources/store.py`、
`sources/service.py`、`sources/router.py`、`storage.py` 和当前 G1/Wiki 设计。未连接任何正式数据库，
未执行 DDL，未修改 runtime/config/API。

## 2. 结论摘要

执行规格已把原设计中仍需实现者自行决定的关键部分冻结：

- V2 采用并列表，不原地改写 V1 ID/表；
- physical blob dedupe 与 project/ACL logical authority 分离；
- ID/digest 使用 domain-separated canonical JSON；
- active object 必须有可验证 blob，reference-only 永不成为 binding；
- event 同 key 不同 payload 是冲突，不是 duplicate success；
- binding resolve 只能 exact/ambiguous，不能 latest；
- tombstone、binding invalidation、blocked entity 和 event 是 project-scoped 原子操作；
- V1 global UNIQUE 可能吞掉的历史意图明确判为不可恢复，只能 re-ingest；
- dry-run 有固定 artifact、守恒式、P0 语义、无正式 I/O 默认和 rollback 证据；
- 15 条基础需求均映射到测试和 evidence。

当前规格可以进入 owner/architecture 决策，但不能进入实现：G0 正式 version admission 未关闭，
且 D1–D5 尚未获 owner 批准。

## 3. 对抗性 Findings

### F-01：只把 project 加入 raw ID 仍会泄漏 physical/logical 权威

风险：若同一 blob row 同时保存 project/ACL/source metadata，跨项目 dedupe 会让 tombstone、ACL 或
metadata 相互污染。

处置：规格拆分 `raw_blobs_v2` 与 `raw_objects_v2`；blob 只保存 digest/length/internal storage key，
logical object 独立绑定 project/source/ACL。public 对象即使内容相同也不跨 project 合并。

判定：`SPEC_RESOLVED / TEST_BF-01_PENDING`。

### F-02：V1 scan 未发现 collision 不能证明历史安全

风险：V1 global UNIQUE + `DO NOTHING` 可能已经让第二个 project 的写入返回第一个 project row；
数据库中未必存在第二次尝试的完整证据。

处置：只有 source manifest/event/project/ACL 唯一可证明的 row 才 safe-copy；无法重建的写入意图
分类为 `history_unrecoverable`，必须 re-ingest，禁止选 first/latest/current owner。

判定：`SPEC_RESOLVED / MIGRATION_FIXTURE_PENDING`。

### F-03：事件幂等成功可能掩盖 payload 冲突

风险：只依赖 unique key + `DO NOTHING` 会把相同 key、不同 bytes/ACL/version 的请求报告为合法
duplicate。

处置：V2 event key project-scoped；冲突回读必须逐字段 canonical compare，不同返回
`idempotency_payload_conflict`，且无新 row/blob/成功 audit。

判定：`SPEC_RESOLVED / TEST_BF-04_PENDING`。

### F-04：binding 多值时“最新一条”是隐性错误版本

风险：同 entity 在多 generation、retrieval unit 或 selector 下合法存在多个 raw binding；按时间选
最新会产生 wrong-version citation。

处置：generation/retrieval unit 必填；resolve 条件不足时 typed ambiguous；不得使用 observed/
created time 消歧。

判定：`SPEC_RESOLVED / TEST_BF-06_PENDING`。

### F-05：tombstone 与正在进行的 read 存在竞态

风险：reader 先取 binding，tombstone 后仍返回已经读取的 bytes。

处置：tombstone 在一个 project-scoped transaction 内改变 object、binding、blocked、event；reader
在输出前使用一致性快照或重查 state。测试必须控制并发边界，不能只顺序调用。

判定：`SPEC_RESOLVED / TEST_BF-08_PENDING`。

### F-06：migration 工具本身可能未经授权读取正式库

风险：所谓 dry-run 仍可能连接生产 DB、泄漏 absolute path/raw row/ACL/secret。

处置：artifact mode 明确 fixture/mirror/authorized-production；缺正式授权时在 connect 前返回
`NOT_AUTHORIZED`；报告禁止路径、URI、ACL 明文、row dump 和 raw bytes。

判定：`SPEC_RESOLVED / TEST_BF-12_PENDING`。

### F-07：双写失败被忽略会制造“已迁移”假象

风险：V1 成功、V2 失败后继续把来源标记 shadow-ready，导致 coverage 和安全结论虚高。

处置：dual-write 可保持 V1 产品行为，但 V2 失败必须阻断该 project/source 的 qualification；
P0 mismatch 或 audit 丢失任一大于 0 都禁止进入 shadow-ready。

判定：`SPEC_RESOLVED / IMPLEMENTATION_PENDING`。

## 4. 完整性检查

| 检查项 | 结果 | 证据/限制 |
|---|---|---|
| logical/physical authority 分离 | PASS | blob/object 并表合同 |
| project/ACL identity | PASS | visibility + logical ID 算法 |
| caller digest 防伪 | PASS | expected-only + pre-write reject |
| reference-only fail closed | PASS | state CHECK + binding prohibition |
| event exact idempotency | PASS | same-key payload conflict |
| binding exact resolve | PASS | unit/generation required；ambiguous typed |
| tombstone 原子与竞态 | PASS | transaction + output-before-state check |
| V1 不可恢复历史 | PASS | explicit re-ingest，不猜测 |
| migration 全分类/守恒 | PASS | M2–M5 categories/equations |
| dry-run 无正式 I/O 默认 | PASS | mode + NOT_AUTHORIZED |
| public path/ACL side-channel | PASS | artifact/API 禁止字段 |
| rollback 不依赖反写 V1 | PASS | parallel tables + reader flags |
| requirement/test/evidence trace | PASS | BF-01–15 |
| 当前代码实现 | NOT_STARTED | G0/owner hold，符合边界 |
| owner D1–D5 决策 | HOLD | 必须显式批准或 ADR 偏离 |

## 5. 实施前必须补齐的外部证据

1. V4 或后续同内容候选进入 owner-reviewed revision；
2. fresh checkout 的 Python 3.12/3.13 CI 与 source snapshot 一致；
3. tracked `web/index.html` 生成物边界关闭；
4. owner/architecture 对 D1–D5 给出明确结论；
5. 确定首个迁移输入仅为 fixture 或脱敏 mirror；正式 DB 没有授权时不得读取；
6. 独立 reviewer 确认 `T1.1.1` 只实现 canonical contract，不混入 gateway/Wiki/cutover。

## 6. 最终判定

`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` 达到 `SPEC ENGINEERING_PASS`：schema、canonical
identity、事务、迁移、dry-run、rollback、owner boundary 与测试证据均已达到可实施粒度；现有已知
P0 不再留给实现者临场决定。

这不是 runtime PASS、migration PASS 或 G1 qualification。当前继续保持：

```text
G0 formal admission: OWNER_ACTION_REQUIRED
G1I-01 specification: ENGINEERING_PASS
G1I-01 implementation: NOT_STARTED
formal database access: NOT AUTHORIZED / NOT PERFORMED
default engine: V1
release: QUALITY_HOLD / NO_RELEASE
```
