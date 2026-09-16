# G0 Owner Admission 与 CI Handoff Gate Review

日期：2026-08-06  
判定：`ENGINEERING_PASS / OWNER_REVISION_HOLD / REMOTE_RUN_HOLD / SUPPLY_CHAIN_HOLD`  
发布：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. 复核结论

`WP-G0-05F` 已关闭“CI 文件存在但不能证明完整 G0”的定义缺口。当前 workflow 对任意 PR 和 main
push 运行，先完成 Python 3.12/3.13 全量后端，再串行完成 exact admission、self-contained
cleanroom、前端测试/类型/build 和最终 release-ready verify。权限、凭据、锁定安装与顺序均有
仓库内合同测试；后续 V10 关闭 V9 clean-full generated HTML 隐含依赖，V11 再以两个 Python
版本各 2,077/2,077 和 portable exclusion Gate 成为当前候选。

该结论只证明 CI 定义达到工程可执行性；没有 Git remote、owner revision 或 GitHub run 证据，因此
不得升级为远程 CI PASS。

## 2. 对抗性检查

| 风险 | 处置与结果 |
|---|---|
| 文档/准入/前端变更绕过 backend path filter | 移除 path filters；所有 PR/main push 触发 |
| 一个 Python 版本失败后被另一个版本掩盖 | matrix 精确 3.12/3.13，`fail-fast=false`，admission `needs` 整个 matrix |
| 只测 checkout、不验证 admission bytes | 在 frontend/build 前执行 `g0-admission-verify` 与 cleanroom |
| build 后污染或 tracked generated output 未发现 | 最后执行 `g0-admission-release-verify` |
| workflow 获取写权限或遗留 token | `contents: read`，checkout `persist-credentials=false` |
| lock/toolchain 漂移 | uv 0.11.14、Node 22、Python 3.13 admission 环境和 locked install 固定 |
| YAML/步骤后续退化 | YAML 解析通过；3 项 workflow contract tests PASS |

## 3. 剩余阻断

1. 当前 Git worktree 仍不是 owner-reviewed revision；
2. `web/index.html` 仍被当前基线跟踪；
3. 仓库没有可观察的 Git remote，GitHub run 未发生；
4. 本 review 当时 React Router RSC advisory 仍为 high；后续本地工程关闭见 review 29，remote artifact 仍待补；
5. V11 之后若任何 admitted bytes 改变，必须生成新 run。

## 4. Gate 决定

`G0.17 / WP-G0-05F` 标记 `ENGINEERING_PASS`，但 `G0.15 Owner revision + clean checkout + remote
CI` 保持 `IN_PROGRESS / OWNER_ACTION_REQUIRED`。下一步严格按
`17_G0_OWNER_ADMISSION_AND_REMOTE_CI_HANDOFF.md` 第 4 节执行；本 Review 不授权 commit、push、
正式数据库访问、G1 runtime 或默认 V2。
