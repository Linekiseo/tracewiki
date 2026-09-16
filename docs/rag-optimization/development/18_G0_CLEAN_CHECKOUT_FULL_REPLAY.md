# G0 Clean Checkout 全链重放与生成物权威修复

版本：2026-08-06 V1  
状态：`ENGINEERING_PASS / OWNER_REVISION_HOLD / REMOTE_RUN_HOLD`  
本记录最终候选：`artifacts/rag-maturity/g0/admission-20260806-v11/admission.json`（当前 owner source candidate 见文档 12）  
复核：`reviews/25_G0_CLEAN_CHECKOUT_FULL_REPLAY_REVIEW_2026-08-06.md`

## 1. V9 负证据

V9 已证明 source-exact、bytes-only materialize、431-object history、安全复扫、ordinary clone 和
cleanroom `release_ready=true`。但在同一 synthetic clean checkout 完成 locked Python/Node install
后，完整后端得到：

```text
1 failed / 2,074 passed / 1 warning
```

唯一失败为 `test_new_project_shell_is_the_production_entry_point` 在前端构建前读取
`web/index.html`。该文件按 admission policy 明确是生成物，不进入 source snapshot；正式工作树之所以
通过，是因为旧的 tracked/generated 文件仍存在。

因此 V9 保留为不可覆盖的“manifest/cleanroom prepare 通过，但全量 clean checkout 失败”证据。

## 2. 根因与权威边界

生产前端有两类不得混写的对象：

| 对象 | 权威 | 验证方式 |
|---|---|---|
| React/Vite HTML 源入口 | `frontend/index.html` | source contract 测试，必须受版本管理 |
| 发布 HTML 与 hashed assets | `web/index.html`、`web/app/**` | `clean-build.mjs` 在隔离/交付阶段生成并建立 build contract |

旧测试把第二类生成物当成第一类源码权威，造成 clean checkout 隐含前置条件。修复将
`SPA_INDEX` 指向 `frontend/index.html`；仍严格断言 `<div id="root"></div>`、路由、shell、项目中心、
Research Map 与样式合同，不删除或放宽任何行为断言。生成物 hash、引用完整性、本地后端泄漏和
sourcemap 禁止继续由 frontend clean-build tests 验证。

## 3. CI 顺序加固

双 Python matrix 的每个 job 在 `backend-full` 前新增 `g0-admission-verify`。因此 admitted bytes
漂移会在昂贵的完整回归前 fail closed；matrix 全绿后，admission job 再防御性复验 exact，并依次
执行 cleanroom、frontend tests/typecheck/build 和最终 release-ready。

`tests/test_g0_ci_workflow.py` 同步冻结 exact-before-backend 顺序，防止未来重新引入漂移源码运行。

## 4. V10 验证合同

V10 必须同时满足：

1. 新 synthetic clean checkout 初始 Git clean，receipt 与 V10 digest 精确相等；
2. locked Python/Node install 不需要原工作树 `.venv` 或 `node_modules`；
3. Python 3.12.13 与 3.13.12 对同一 V10 源码各 2,075/2,075；
4. V10 clean checkout 在前端 build 前执行 backend full 仍为 2,075/2,075；
5. 前端 31 files / 179 tests、typecheck、production build PASS；
6. build 后 admitted source exact、Git clean、release blocker=0；
7. 当前正式脏工作树继续因 tracked `web/index.html` 和未纳管范围 fail closed。

共同允许的唯一 warning 仍是已登记的 Starlette/FastAPI TestClient 第三方弃用提示。

## 5. 发布边界

V10 关闭本地 clean-checkout 全量重放缺口；V11 再关闭 exclusion/release-ready 语义矛盾。两者都
不自动形成 owner-reviewed revision，也不证明 GitHub remote run。Owner 仍必须按
`17_G0_OWNER_ADMISSION_AND_REMOTE_CI_HANDOFF.md` 纳管 V11、取消跟踪
生成物并保存同一 revision 的远程 job 证据。在此之前保持
`QUALITY_HOLD / DEFAULT_V1 / NO_RELEASE`，G1 runtime 不解锁。
