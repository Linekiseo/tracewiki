# G1 Binding Foundation 实施就绪复核

日期：2026-08-06  
复核类型：`FIRST-PARTY ADVERSARIAL CONTRACT-TO-CODE REVIEW`  
复核对象：`13`、`15` V2、`27` 与当前 raw/binding/Wiki 源码  
结论：`READINESS_CONTRACT_PASS / ENTRY_HOLD / IMPLEMENTATION_NOT_STARTED`  
运行边界：`READ-ONLY / FORMAL DB NOT ACCESSED / DEFAULT_V1 / NO_RELEASE`

## 1. 复核结论

原 G1 方向与并列表迁移成立，但 V1 execution spec 仍有会迫使实现者临场决定的合同矛盾。本轮已在
V2 规格中关闭 reference-only nullable、canonical hash/ID、event ID、visibility 语义、source mapping、
field size 和 reason registry，并把上位设计统一到 relative storage key、V2 raw object 和 mandatory
generation。

现在 T1.1.1 已有独立输入、允许文件、十个子目标、portable vectors、负向用例和停止条件。该结论
只证明“解锁后可开始实现”，不证明实现、迁移、G1 qualification 或发布。

## 2. 对抗性 Findings

### F-01：reference-only 的 schema 不可满足

旧规格同时要求 reference-only 无 bytes/blob，又要求 raw digest 和 byte length 非空。实现者只能伪造
empty digest/zero length，或违反 state 合同。

处置：digest/length 改为 nullable；active 必填，reference-only 必须 null；empty active payload 单独
固定为 SHA-256(empty) + length 0。

判定：`SPEC_FIXED / IMPLEMENTATION_PENDING`。

### F-02：摘要与 ID 表达未唯一

旧公式混用 `H(...)`、`sha256:` 与 `.hex`，event 只有 idempotency digest 没有 event ID 算法。

处置：定义 CJSON/H_HEX/H，digest field 使用前缀、typed ID 使用裸 hex；event ID 由 idempotency
digest 确定。

判定：`SPEC_FIXED / PORTABLE_VECTOR_PENDING`。

### F-03：两种 visibility 被混为同一语义

object authority 绑定单个 ACL，Wiki/request scope 使用 requester ACL 集；复用 digest 会把授权集合写入
object identity 或遗漏 project。

处置：object visibility 固定为 project + single object ACL；request/Wiki scope 使用不同 domain/version。

判定：`SPEC_FIXED / OWNER_D2_AND_TEST_PENDING`。

### F-04：source domain 可能被字符串猜测

V1 还有 dvc/manual，V2 只有六域；没有映射表会导致实现者把未知来源强塞到相似域。

处置：冻结五项 V1 映射和 workspace 新事件；dvc/manual/unknown 必须 owner mapping 或 re-ingest。

判定：`SPEC_FIXED / ADAPTER_PENDING`。

### F-05：Wiki raw read 的命名可能继续制造误判

当前 gateway 读取 WikiStore candidate envelope 并输出 candidate snippet。candidate digest 自洽不等于
raw bytes 可回读。

处置：在 readiness trace 中冻结真实链路；V1 只保留 navigation/provenance，strict raw 必须
`legacy_unbound`；G1/G2 不允许 candidate fallback。

判定：`DESIGN_FIXED / G1I-09_AND_G2_PENDING`。

### F-06：现有 Binding 模块可能被错误扩展

`src/evidence_rag/bindings` 保存 Codex change → Code entity 的关系候选，不具备 raw/selector/digest。

处置：G1 raw foundation 固定为新 `sources/v2` 边界；现有 reviewed candidate 只能是未来 derived input。

判定：`BOUNDARY_FIXED / IMPLEMENTATION_PENDING`。

## 3. Requirement 检查

| 检查 | 结果 | 限制 |
|---|---|---|
| 当前成熟度结论有代码证据 | PASS | 不等于生产运行审计 |
| V1 raw/event/project 风险逐文件映射 | PASS | 未扫描正式 DB rows |
| Wiki candidate 与 raw authority 区分 | PASS | V2 reader 未实现 |
| schema/ID/reference-only 自洽 | PASS | 仅合同 |
| source mapping fail closed | PASS | adapter 未实现 |
| T1.1.1 可独立关闭 | PASS | 入口未满足 |
| T1.1.2–14 依赖单向 | PASS | owner Gate 仍有效 |
| 正式 DB/runtime/default 未改变 | PASS | 本轮只改文档 |
| D1–D5 | HOLD | 必须 owner/architecture 明确决定 |
| G0 revision/remote CI | HOLD | 当前无 Git remote/run |

## 4. 目标判定

```text
WP-G1D-03 readiness reconciliation: ENGINEERING_PASS
WP-G0-05 owner version admission: OWNER_ACTION_REQUIRED
D1-D5: OWNER/ARCHITECTURE_ACTION_REQUIRED
T1.1.1: NOT_STARTED
G1I-01 runtime: NOT_STARTED
formal database access: NOT AUTHORIZED / NOT PERFORMED
default engine: V1
release: QUALITY_HOLD / NO_RELEASE
```

只有 owner revision、remote CI 和 D1–D5 同时关闭后，才可按 `27` 文档从 T1.1.1 C1 开始；不得以
本 review 的 PASS 绕过 Entry。
