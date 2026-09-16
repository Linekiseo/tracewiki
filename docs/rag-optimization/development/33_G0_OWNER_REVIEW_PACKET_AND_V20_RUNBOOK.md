# G0 Owner Review Packet 与 V20 准入 Runbook

版本：2026-08-06 V1  
工作包：`WP-G0-05L Owner review packet`  
状态：`LOCAL_ENGINEERING_PASS / OWNER_DECISION_PENDING / REMOTE_RUN_PENDING`  
前置候选：V19  
边界：`PENDING ONLY / NO SIGNING / NO GIT MUTATION / NO DATABASE / NO RELEASE`

## 1. 问题与结论

V19 已能逐文件验证 1,116-file source snapshot，但 owner handoff 仍主要依赖人工阅读 manifest 和
runbook。manifest 证明“这些 bytes 是什么”，却没有单独冻结“owner 要逐项审什么、排除项如何处置、
哪些动作仍缺外部 authority”。在大范围 modified/untracked 工作树中，这会留下整目录盲目 stage、漏审
scope、忽略 exclusion denominator 或把本地 cleanroom 当成 owner approval 的操作风险。

`maturity_g0_owner_review_v1.py` 新增一个确定性、始终 PENDING 的 review packet：

```text
native admission verify
  → exact path review_items
  → per-scope review_groups + domain digest
  → listed exclusions + complete exclusion denominator
  → release blocker actions
  → seven external completion actions
  → canonical PENDING packet
```

它只改善交接和可审计性，不产生审批。任何 owner decision、受控 revision、远程 CI、LIVE-02 或 G1
receipt 仍必须由外部 authority 产生。

## 2. Canonical packet 合同

Schema 固定为 `rag-maturity-g0-owner-review-v1`，核心字段为：

```text
schema_version
status = PENDING_OWNER_REVIEW
g0_admission {
  path, schema_version, base_head,
  content_sha256, source_snapshot_sha256, scope_policy_sha256,
  file_count, release_blocker_count
}
file_count
review_items[]
review_groups[]
excluded_file_count
listed_exclusion_count
exclusion_counts
exclusion_items[]
required_actions[]
reviewed_revision = null
owner_reviewed = false
remote_ci_observed = false
constraints
review_subject_sha256
content_sha256
```

### 2.1 Path review items

每个 admitted file 恰有一行：`path`、`scope`、`role`、`byte_length`、`content_sha256`、捕获时
`git_state` 和固定 `review_status=PENDING`。packet 的 `file_count` 必须同时等于 review item 数和
全部 scope group 的 file count 总和；排序继承 admission 的 canonical path order。

### 2.2 Scope review groups

每个 admission scope 恰有一组：file/byte/role/Git-state denominator、scope content digest 和独立
`review_subject_sha256`。scope subject 覆盖该 scope 的完整 path/role/size/content set，而不是只签一个
可手填计数。整体 `review_subject_sha256` 再覆盖 admission、全部 path/scope、exclusion 和 action。

digest 使用独立 domain：

```text
SHA256("rag-maturity-g0-owner-review-v1\0" + label + "\0" + canonical_json)
```

scope 与整体 subject 使用不同 label，防止跨用途替换。

### 2.3 Exclusion 与 blocker

`excluded_file_count` 保留包括 cache 在内的完整 denominator；`listed_exclusion_count` 只计 manifest
逐项列出的非 cache 路径，二者禁止混写。每个 listed exclusion 带固定 expected disposition：

| Reason | Expected disposition |
|---|---|
| generated output | rebuild，不纳入 source revision |
| database sidecar | 拒绝 transient DB state |
| sensitive path | 拒绝并进入安全复核 |
| symlink | 拒绝不安全路径 |
| unmanifested document asset | owner 明确决定留在 source admission 之外 |

当前 `web/index.html` 继续作为 `G0-OWNER-03` 的 release blocker；packet 不删除文件，也不替 owner
执行版本控制操作。

## 3. 七项外部动作

| ID | Authority | 完成证据 |
|---|---|---|
| G0-OWNER-01 | repository owner | 全部 path 与 scope 外部审阅 |
| G0-OWNER-02 | repository owner | 全部 listed exclusion 被明确处置，无 broad staging |
| G0-OWNER-03 | repository owner | tracked runtime output 取消跟踪并可重建 |
| G0-OWNER-04 | protected revision control | commit/tree bytes 等于 source snapshot |
| G0-OWNER-05 | remote CI | 同 revision 双 Python、exact、cleanroom、frontend、supply、release-ready |
| G0-OWNER-06 | UI reviewer | LIVE-02 identity/console/hash/back-forward |
| G0-OWNER-07 | independent Gate reviewer | 新 G0 package 与 Gate Review |

所有 action 固定为 PENDING。packet 没有 `approve`、`sign`、`commit`、`push`、`fetch`、网络、数据库或
release 子命令；外部结果后续进入 G1 Entry 的受信 receipt 流程，不能直接回写本 packet。

## 4. Build 与 verify

当前 Make 入口：

```text
make g0-owner-review-build
make g0-owner-review-verify
```

底层命令均要求显式 admission/root。Build 顺序固定为：G0 原生 manifest canonical 验证 → 当前 source
exact 验证 → packet 重算 → 不覆盖写入。Verify 不只校验 packet 外层 digest，而是重新调用 G0 原生
verifier，并从指定 manifest 完整重建预期 packet 后逐字段比较。因此以下情况都 fail closed：

- source bytes 漂移；
- admission path substitution，即使内容相同；
- scope/file/exclusion/action denominator 改写；
- status 改为 APPROVED 后重算外层 digest；
- owner/revision/remote 字段伪造；
- 非 canonical JSON、unknown/tampered content 或已有输出覆盖。

## 5. 验证矩阵

| ID | 场景 | 预期 |
|---|---|---|
| OR-01 | 同 manifest 重算两次 | packet 完全相同 |
| OR-02 | path/scope/file denominator | 精确守恒 |
| OR-03 | blocker/exclusion denominator | tracked HTML 与完整/列出计数均保留 |
| OR-04 | write/verify/copy | portable canonical PASS |
| OR-05 | overwrite/outside-root | 拒绝 |
| OR-06 | APPROVED/revision escalation + rehash | 拒绝 |
| OR-07 | scope tamper + rehash | 拒绝 |
| OR-08 | equivalent manifest path substitution | 拒绝 |
| OR-09 | current source drift | G0 native verify 拒绝 |
| OR-10 | CLI build/verify | canonical summary PASS |

实现测试文件：`tests/test_maturity_g0_owner_review_v1.py`。V20 的准确 file/scope/digest 以其
admission artifact 和 owner-review packet 为权威，本文不复制自引用摘要。

## 6. Gate 影响与下一步

```text
WP-G0-05L owner review tooling: LOCAL_ENGINEERING_PASS
V20 source exact/self-contained cleanroom: ENGINEERING_PASS
owner review packet: VERIFIED_PENDING_OWNER_REVIEW
owner-reviewed revision: MISSING
same-revision remote CI: MISSING
LIVE-02: NOT OBSERVED
G0: VERSION_ADMISSION_HOLD
G1 runtime: NOT STARTED
release: DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE
```

V20 已纳入本工具、测试、runbook 和独立 review，并完成 exact/cleanroom 与 PENDING packet canonical
verify。真正关闭 G0 的下一步仍由 owner 按 G0-OWNER-01–07 执行。任何 admitted bytes 变化必须新建
admission，不得覆盖 V19/V20。
