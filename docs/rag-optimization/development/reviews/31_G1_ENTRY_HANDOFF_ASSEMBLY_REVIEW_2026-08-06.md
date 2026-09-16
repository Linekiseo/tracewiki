# G1 Entry Handoff 与 Assembly 对抗性复核

日期：2026-08-06  
复核类型：`FIRST-PARTY ADVERSARIAL HANDOFF REVIEW`  
复核对象：`maturity_g1_entry_handoff_v1.py`、40-case Entry/handoff 测试、文档 31  
结论：`HANDOFF_ENGINEERING_PASS / EXTERNAL_AUTHORITY_MISSING`  
边界：`NO SIGNING / NO NETWORK / NO DATABASE / DEFAULT_V1 / NO_RELEASE`

## 1. 结论

handoff 工具消除了手工拆 subject、回填 receipt reference 和计算 status 的主要错误面。它只生成
PENDING draft、fact-ready signing requests，或在外部收据完整可信时组装 packet；没有私钥、签名、
在线 provider 或自批路径。

本 review 不批准真实 Entry：所有正向签名仍来自测试夹具，当前仓库没有 owner revision、remote run、
带外 trust anchor 或真实组织收据。

## 2. Findings

### F-01：人工 packet 组装可漏签或错配 subject

处置：从同一 canonical draft 确定性生成 target；assembly 按 kind/role/revision/subject digest 一一匹配，
缺失、多余和多张匹配 receipt 全部拒绝。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-02：外部人员可能被要求签一个事实尚不合格的 packet

处置：requests 前独立检查 G0/remote/generated/supply/D1–D5/data/review/runtime 的非签名事实；任一
不满足不生成请求。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-03：旧 receipt 可在新 request 中重放

处置：request time 不得早于事实完成；receipt issued time 不得早于 request time；receipt 仍需绑定同
revision 和 exact subject，且必须未过期、key 未撤销。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-04：攻击者重算 request bundle digest 后改写 request

处置：assembly 从 draft 完整重建 request bundle 并比较全部字段；自洽但不匹配 draft 的 bundle 被拒绝。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-05：assembly 自己成为第二套宽松 verifier

处置：assembly 复用同一 strict parser/keyset/receipt/G0/E-01–10 primitives，任何 HOLD 都不输出部分批准；
成功输出仍由原 verifier 再验。正向测试执行双重判定。

判定：`FIXED_AND_REGRESSION_TESTED`。

### F-06：CLI 隐式写文件或接触外部系统

处置：三个命令只读显式输入并输出 canonical stdout；I/O spy 证明无 network/database/subprocess；工具
不提供 keygen、sign、fetch、push、DB 或 release 子命令。

判定：`BOUNDARY_EXPLICIT`。

## 3. Evidence

| 检查 | 结果 |
|---|---|
| Entry + handoff cases | 40/40 PASS（Python 3.12.13 / 3.13.12） |
| Entry + G0 workflow 定向 | 43/43 PASS（Python 3.12.13 / 3.13.12） |
| 完整 backend | 2,117/2,117 PASS（Python 3.12.13 / 3.13.12） |
| draft/request/assemble CLI canonical | PASS |
| deterministic 15-request base set | PASS |
| request fact readiness | PASS |
| trust/missing/extra/duplicate receipt | PASS |
| request/receipt temporal replay | PASS |
| assembly → original verifier | ENTRY_QUALIFIED（synthetic fixture only） |
| network/database/subprocess | 0 |
| real external authority | MISSING |

## 4. Gate 决定

```text
WP-G1D-06: LOCAL_ENGINEERING_PASS
handoff protocol: PASS
real G0/Entry: HOLD
G1 runtime: NOT STARTED
formal database: NOT ACCESSED
release: DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE
```

下一步必须由 owner 与远程系统提供真实同 revision facts，再由独立签发者处理 signing requests。不得把
本 review、测试 request 数、synthetic assembly 或仓库中的固定私钥当作批准。
