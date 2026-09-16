# G0 V24 Drift 与 V25/V26 Candidate Refresh 执行记录

版本：2026-08-29 V1
工作项：`T0.28.1R`
状态：`V25 INTERMEDIATE_PASS / V26 HISTORICAL_ON_EXCLUDED_RECEIPT / SUPERSEDED_BY_V27`
边界：`NO OVERWRITE / PENDING OWNER REVIEW / NO GIT MUTATION / NO DATABASE / NO RELEASE`

## 1. 目的

V24 admission 与 packet V5 是有效、不可覆盖的 2026-08-06 历史证据，但不再覆盖当前完整 source set。
本工作项只刷新 owner review 输入，不产生 owner decision、受保护 revision、远程 CI、LIVE-02 或发布授权。

## 2. V24 原生失败证据

2026-08-29 按 current Make 默认入口执行：

```text
make g0-admission-verify
  → current source set does not match admission manifest

make g0-owner-review-verify
  → admission manifest verification failed
```

verifier 正确地在 source exact 边界 fail closed；没有放宽断言、改写 manifest 或删除新增文档。

## 3. 精确 Drift

以 `maturity_g0_admission_v2._collect` 的正式范围策略对 V24 manifest 与当前 source 进行只读比较：

| 项目 | V24 | 当前/差异 |
|---|---:|---:|
| admitted files | 1,124 | 1,134（+10） |
| added | 0 | 10 |
| modified | 0 | 0 |
| removed | 0 | 0 |
| Git-state changed | 0 | 0 |
| documentation scope delta | 0 | +10 |
| exclusion added/removed | 0 | 0 |
| base/current HEAD | `bc3326edc761e3bdb42ed78726a21f314ab44974` | 相同 |

新增项为 G5–G8 四份执行规格和 `execution-control/` 六份执行文档。旧/新 exclusion denominator 完全一致：
cache 28,939、database sidecar 6、generated build output 65、unmanifested document asset 12。

## 4. 两阶段不可变刷新

控制文档必须记录真实验证结果，但任何验证后的受控文档更新都会改变 source digest。因此本工作项采用已有
V7/V8、V18/V19 同类模式：

1. **V25 intermediate capture**
   - 捕获当前 source 与 `T0.28.1R IN_PROGRESS` 记录；
   - 生成不可覆盖 admission V25 和 PENDING packet V6；
   - 执行 exact/canonical verify 与 admission/owner-review 定向测试；
2. **V26 documentation-complete candidate（当时 current）**
   - 将 V25 实际结果、current pointer 和任务恢复点写回受控文档；
   - 生成不可覆盖 admission V26 和 PENDING packet V7；
   - 再执行 exact/canonical verify 与定向测试；
   - V26/V7 成为新的 owner review 输入，T0.28.2 恢复为唯一 active task。

V25 是可验证的中间证据，不会被描述为 owner-reviewed current revision；V26 也只会保持
`PENDING_OWNER_REVIEW`。

## 5. 预定 artifact

```text
artifacts/rag-maturity/g0/admission-20260829-v25/admission.json
artifacts/rag-maturity/g0/owner-review-20260829-v6/review-packet.json
artifacts/rag-maturity/g0/admission-20260829-v26/admission.json
artifacts/rag-maturity/g0/owner-review-20260829-v7/review-packet.json
```

所有路径均为新目录；禁止覆盖 V24/V5 或任一已生成的 V25/V6/V26/V7。

## 5.1 后续换代说明

V26 生成后的本地完整回放发现其 lock 中存在 nanoid high finding；最小 lock-only 修复使 V26 exact
按设计失效。V26/V7 继续作为不可覆盖历史证据，current candidate 已由
`43_G0_V27_SUPPLY_CHAIN_AND_LOCAL_REPLAY_EXECUTION_RECORD.md` 接续到 V27/V8。本说明不改写本文件
记录的 V25/V26 当时执行事实。

## 6. 验证矩阵

| ID | 检查 | 当前状态 |
|---|---|---|
| CR-01 | V24/current exact failure 可复现 | PASS |
| CR-02 | added/modified/removed/exclusion/HEAD denominator | PASS |
| CR-03 | V25 admission build + exact verify | PASS；1,135 files |
| CR-04 | V25 packet V6 build + canonical verify | PASS；1,135 items / 10 groups / PENDING |
| CR-05 | admission/owner-review tests | PASS；Ruff + pytest 15 |
| CR-06 | 当时 current pointer 与 V26/V7 一致 | HISTORICAL_PASS |
| CR-07 | V26 admission build + exact verify | POST-BUILD RECEIPT |
| CR-08 | V26 packet V7 build + canonical verify | POST-BUILD RECEIPT |
| CR-09 | V26 post-build excluded execution receipt | authority path：`artifacts/rag-maturity/g0/admission-20260829-v26/EXECUTION_RECORD.md` |

## 7. 停止条件

- source delta 不再仅是已记录的受控文档变化；
- exclusion/secret/symlink/database boundary 出现未解释变化；
- builder 需要覆盖历史 artifact；
- packet 不再保持 PENDING 或产生 owner/revision/remote 字段；
- 任何命令尝试 stage、commit、push、访问正式数据库或切换 release。

触发后保留已有 artifact 和失败日志，使用新的版本号恢复，不修改历史证据。
