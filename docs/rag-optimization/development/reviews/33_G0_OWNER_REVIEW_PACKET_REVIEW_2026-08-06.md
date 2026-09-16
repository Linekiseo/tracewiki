# G0 Owner Review Packet 对抗性复核

日期：2026-08-06  
复核类型：`FIRST-PARTY ADVERSARIAL HANDOFF REVIEW`  
复核对象：`maturity_g0_owner_review_v1.py`、测试、Make 入口、文档 33  
结论：`LOCAL_ENGINEERING_PASS / OWNER_DECISION_PENDING`  
边界：`PENDING ONLY / NO SIGNING / NO GIT MUTATION / NO DATABASE / NO RELEASE`

## 1. 结论

新工具把 admission manifest 转为逐路径、逐 scope、逐 exclusion、逐外部 action 的确定性审阅队列，
关闭了“manifest 可验证但 owner handoff 仍靠口头/整目录操作”的工程缺口。packet 只能保持 PENDING；
即使攻击者重算外层摘要，也不能在 verifier 中伪造 APPROVED、revision 或 remote evidence。

本 review 不代表 owner 已审阅，也不授权 commit、remote run、G1 runtime 或 release。

## 2. Findings

### F-01：owner 可能对大目录执行盲目纳管

处置：复制 admission 的每个 path/role/scope/size/content identity 为独立 PENDING review item，并以
scope denominator 和 subject digest 二次约束。

判定：`FIXED_AND_POSITIVE_TESTED`。

### F-02：排除项的列出数量可能冒充完整 denominator

处置：分开 `excluded_file_count` 与 `listed_exclusion_count`，保留完整 reason counts；非 cache 路径
逐项给出 expected disposition。

判定：`FIXED_AND_REGRESSION_TESTED`。

### F-03：本地工具可能成为自批通道

处置：status、owner、revision、remote、production 字段固定为 PENDING/false/null；无 approve/sign/keygen
命令。伪造字段并重算 content digest 仍被 authority boundary 拒绝。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-04：只校验 packet digest 无法阻止自洽篡改

处置：verify 重新执行 G0 原生 manifest/source 验证，再从指定 admission 完整重建 packet 并全对象比较。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-05：相同 manifest bytes 可从另一逻辑路径替换

处置：review subject 绑定 repository-relative admission path；equivalent copy substitution 被拒绝。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-06：packet 可能泄漏本机路径或产生隐式副作用

处置：只保存 repository-relative paths；输出必须位于 repository 内且不得覆盖；constraints 明确
network/database/Git mutation/signature/release 均为 false。

判定：`BOUNDARY_EXPLICIT`。

### F-07：当前 source 漂移后旧 packet 仍可能被复用

处置：每次 verify 必须对当前 root 执行 source exact；任一 admitted path/role/scope/size/content 变化
都先由 G0 verifier 拒绝。

判定：`FIXED_AND_NEGATIVE_TESTED`。

## 3. Evidence

| 检查 | 结果 |
|---|---|
| owner-review unit/CLI tests | 8/8 PASS |
| deterministic build | PASS |
| path/scope denominator conservation | PASS |
| blocker/exclusion preservation | PASS |
| copy verify | PASS |
| overwrite/outside-root rejection | PASS |
| authority escalation + rehash | PASS（拒绝） |
| scope tamper + rehash | PASS（拒绝） |
| manifest substitution | PASS（拒绝） |
| current source drift | PASS（拒绝） |
| Python 3.12.13 / 3.13.12 full | 2,126/2,126 PASS |
| V20 source exact / cleanroom | PASS / PASS |
| V20 owner-review packet build/verify | PASS / PASS；保持 PENDING |
| external owner review | MISSING |
| remote CI/LIVE-02 | MISSING |

## 4. Gate 决定

```text
WP-G0-05L: LOCAL_ENGINEERING_PASS
V20 candidate: SOURCE_EXACT_AND_CLEANROOM_PASS
owner packet: VERIFIED_PENDING_OWNER_REVIEW
owner review: PENDING
G0: VERSION_ADMISSION_HOLD
G1 runtime: NOT STARTED
formal database: NOT ACCESSED
release: DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE
```

V20 与正式 PENDING review packet 已生成并验证；下一步只能由 G0-OWNER-01–07 对应的外部 authority
关闭剩余条件。
