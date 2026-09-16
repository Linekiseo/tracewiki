# G1 Entry 离线验证器对抗性复核

日期：2026-08-06  
复核类型：`FIRST-PARTY ADVERSARIAL TRUST-BOUNDARY REVIEW`  
复核对象：`maturity_g1_entry_v1.py`、测试、文档 28 V2 与文档 30  
结论：`VERIFIER_ENGINEERING_PASS / REAL_ENTRY_HOLD`  
边界：`OFFLINE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE / FORMAL_DB_NOT_ACCESSED`

## 1. 复核结论

实现能够对 E-01–10 做确定性、离线、fail-closed 判定；canonical、签名、信任锚、revision、有效期、
职责分离、G0 原生验证、CI attempt 和 aggregate binding 均有正负测试。Python 3.12.13/3.13.12 各
30 个定向与相邻 Gate case 通过，完整 backend 各 2,104/2,104 通过。

该 PASS 只属于验证器工程，不属于真实 Entry。仓库没有真实外部 keyset/receipt/remote run，测试私钥
公开且确定，只能用于夹具；任何人用它们生成的 packet 都不是项目 authority。

## 2. Findings 与处置

### F-01：packet content digest 被误当 authority

风险：攻击者可重写 JSON 并重算 SHA-256。

处置：逐 receipt Ed25519 验签；packet reference set 与 bundle 精确相等；keyset 必须匹配调用方带外
digest；签发 role、key/receipt validity、revocation 和 decision 全部 fail closed。

判定：`FIXED_IN_VERIFIER / REAL_TRUST_ANCHOR_PENDING`。

### F-02：CI receipt 只签 remote run，其他 Gate 聚合可伪造

风险：G0 exact/cleanroom、generated boundary、supply count 可在签名后被改写。

处置：`REMOTE_CI` subject 原子包含 G0、generated、remote CI 和 supply 四块；三类 aggregate rewrite
测试证明重算 packet digest 仍被拒绝。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-03：Entry 层只验证 G0 外层 digest

风险：攻击者可构造自洽但不符合 G0 schema/policy 的伪 manifest。

处置：调用 G0 V2 原生完整 verifier，再重算 file/scope 并与 packet 交叉核对；伪 scope policy 测试被
拒绝。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-04：多 role key 绕过独立复核

风险：同一主体同时扮演 owner、reviewer 或 architecture、rollback owner。

处置：除了 role 授权，还比较 issuer key identity，冻结三组职责分离约束。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-05：rerun 删除失败历史

处置：attempt 必须连续 `1..run_attempt`，结尾与当前 conclusion 一致，历史日志和 artifact 均为内容
digest；覆盖旧失败测试被拒绝。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-06：本地双 Python 并发污染共享环境

处置：本地串行重建/运行，或按版本隔离 venv；远程 matrix 保持独立 runner。未把收集前环境竞态计为
代码回归，也未删除任何 Python 版本。

判定：`RUNBOOK_FIXED / PRODUCT_NOT_AFFECTED`。

## 3. Evidence 复核

| 检查 | 结果 |
|---|---|
| Python 3.12 Entry + G0 workflow | 30/30 PASS |
| Python 3.13 Entry + G0 workflow | 30/30 PASS |
| Python 3.12/3.13 backend full | 2,104/2,104 each |
| strict canonical/CLI output | PASS |
| Ed25519 tamper/expiry/revocation boundary | PASS |
| cross revision/snapshot/attempt | PASS |
| aggregate rewrite | PASS |
| G0 native verifier | PASS |
| duties and production self-claim | PASS |
| network/database/subprocess side effects | 0 |
| real owner/keyset/remote evidence | MISSING |

## 4. Gate 决定

```text
WP-G1D-05: LOCAL_ENGINEERING_PASS
Entry verifier contract: PASS
real E-01..E-10: NOT SATISFIED
G0 QUALIFIED: NO
G1 runtime: NOT STARTED
database authorization: ABSENT
release: DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE
```

下一动作只能是纳入新的不可覆盖 G0 candidate，并由 owner/远程系统生成真实同 revision 证据；不得用
本 review、合成夹具、仓库 keyset 或本地 CI 冒充 Entry approval。
