# G1 Entry Packet 与 Requirement Trace 对抗性复核

日期：2026-08-06  
复核类型：`FIRST-PARTY ADVERSARIAL ENTRY-GATE REVIEW`  
复核对象：`28_G1_ENTRY_DECISION_AND_REQUIREMENT_TRACE_PACKET.md`  
结论：`ENTRY_CONTRACT_PASS / OWNER_INPUT_REQUIRED / IMPLEMENTATION_NOT_STARTED`  
运行边界：`READ-ONLY / NO GIT REMOTE / FORMAL DB NOT ACCESSED / DEFAULT_V1 / NO_RELEASE`

后续：该 V1 review 保留“当时 verifier 尚未实现”的历史判定；实现、负向验证和新的 Gate 结论由
`30_G1_ENTRY_OFFLINE_VERIFIER_EXECUTION_RECORD.md` 及 review 30 接续，不回写成本 review 当时已完成。

## 1. 结论

Entry Packet 已把 owner revision、remote CI、generated output、supply chain、D1–D5、data authorization、
independent review 和 runtime boundary 收敛为 E-01–10 合取；BF-01–15 也已经映射到具体未来测试文件、
fixture 形式、核心断言、失败副作用和 artifact row。

当前不能批准 Entry：本地没有 Git remote，HEAD 仍为旧 revision，工作树非 clean，正式 release gate
存在 generated-output blocker，D1–D5 与数据/安全决定没有外部 attestation。供应链 finding 的本地
工程修复见 `29` 文档，但同 revision remote artifact 仍缺失。本文只关闭 gate contract，不关闭 gate。

## 2. 对抗性 Findings

### F-01：目标卡与执行规格对 G0 入口强度不一致

目标卡要求 `G0 QUALIFIED`，旧 Entry 列表只要求 owner revision/remote CI，可能遗漏供应链 HOLD。

处置：采用更严格的 `G0 QUALIFIED`；E-06 要求 finding 修复或有 owner/security、expiry、mitigation、
forbidden stages 的受限例外。受限例外不授权生产、shadow、canary 或 default V2。

后续 `WP-G0-05J` 已移除 vulnerable dependency，本地 audit=0；E-06 更新为
`LOCAL_PASS / REMOTE_ARTIFACT_PENDING`。该后续证据不改变本 review 的 Entry PENDING 结论。

判定：`CONTRACT_FIXED / LOCAL_FINDING_CLOSED / REMOTE_ATTESTATION_PENDING`。

### F-02：仓库作者可以伪造 approval JSON

风险：只检查 `status=APPROVED` 或 reviewer 字符串，无法证明 owner authority。

处置：至少绑定 protected review、受信签名身份或受控变更系统 approval；packet 保存安全 ID/digest，
离线 verifier 使用预提供 receipt/keyset；本地自签无效。

判定：`CONTRACT_FIXED / EXTERNAL_ATTESTATION_PENDING`。

### F-03：不同 revision 的绿色 job 可被拼接

风险：3.12、3.13、frontend/admission 各自成功，但不属于同一 source snapshot。

处置：remote job 全部绑定 run attempt、revision 和 source snapshot；APPROVED 要求精确相等，rerun
不覆盖失败 attempt。

判定：`CONTRACT_FIXED / REMOTE_RUN_PENDING`。

### F-04：截图、badge 或 URL 可能替代可复核日志

处置：要求 workflow source、command set、log 和 artifact-set digest；截图/badge/URL 单独无效。

判定：`CONTRACT_FIXED / REMOTE_ARTIFACT_PENDING`。

### F-05：BF 测试只断言 exception，不检查副作用

风险：错误正确抛出，但已经写入 blob/row/audit 或泄漏 response field。

处置：每项 BF 必须记录 before/after table/row/blob/audit/public-field digest；side-effect invariant 是
PASS 的必要条件。

判定：`TRACE_FIXED / IMPLEMENTATION_PENDING`。

### F-06：fixture 授权可能被请求/环境变量升级为 production

处置：data authorization 明确 mode/allowed/forbidden/expiry；production 需要独立外部 attestation，
必须在 connect 前验证。

判定：`CONTRACT_FIXED / PRODUCTION_NOT_AUTHORIZED`。

## 3. 完整性检查

| 检查 | 结果 | 当前限制 |
|---|---|---|
| E-01–10 有单一 authority evidence | PASS | evidence 尚未产生 |
| Entry APPROVED 是显式合取 | PASS | 当前 PENDING |
| owner approval 不可仓库自签 | PASS | keyset/receipt 尚无 |
| remote run 同 revision/snapshot/attempt | PASS | 无 remote |
| supply-chain finding 未被隐藏 | PASS | 本地已关闭；same-revision remote audit/SBOM 未产生 |
| D1–D5 choices/tests/attestation | PASS | 全部 PENDING |
| data mode fail closed | PASS | fixture only |
| BF-01–15 test/fixture/command trace | PASS | 测试尚未实现 |
| BF side-effect evidence | PASS | 运行证据尚未产生 |
| negative verifier matrix | PASS | verifier 尚未实现且未解锁 |
| runtime/default/DB/release 未改变 | PASS | 本轮仅文档 |

## 4. Gate 决定

```text
WP-G1D-04 Entry/requirement trace: ENGINEERING_PASS
G0 QUALIFIED: NO
Entry Packet: PENDING / OWNER INPUT REQUIRED
D1-D5: PENDING
remote CI: MISSING
formal database authorization: ABSENT
T1.1.1: NOT STARTED
G1 runtime: NOT STARTED
release: DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE
```

本 review 的 PASS 只证明 Entry 合同已经足够严格，不是 Entry 本身通过。后续只有外部可验证 packet
使 E-01–10 全部 PASS，才允许开始 T1.1.1 C1；不能用本 review、自填 JSON 或本地 cleanroom 替代。
