# G0 Clean Checkout Full Replay Gate Review

日期：2026-08-06  
判定：`ENGINEERING_PASS / OWNER_REVISION_HOLD / REMOTE_RUN_HOLD / SUPPLY_CHAIN_HOLD`  
发布：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. 结论

V9 clean checkout 全量重放发现后端合同测试依赖被 admission 正确排除的生成物
`web/index.html`，得到 1 failed / 2,074 passed。V10 将 React/Vite 源入口权威纠正为
`frontend/index.html`，保留全部行为断言，并继续由 clean-build contract 验证发布产物。

V10 新 clean checkout 在无预生成 `web/index.html` 的情况下完成完整后端、前端和构建后
release-ready；该结果关闭本地 clean-checkout 隐含生成物依赖，不覆盖 V9 负证据。

## 2. 对抗性判断

| 问题 | 判断 |
|---|---|
| 是否通过先 build 掩盖测试错误 | 否；backend full 在 frontend build 前通过 |
| 是否删除或放宽 production entry 断言 | 否；只把读取对象从 generated HTML 改为 Vite source HTML |
| generated bundle 是否失去验证 | 否；clean-build tests 与 build contract 继续验证 hash/reference/safety |
| source 漂移是否会在远程完整测试前被发现 | 是；matrix 每个 job exact-before-backend |
| V9 是否被回写 | 否；V9 保留 2,074/1 失败负证据，V10 新建不可覆盖 artifact |
| 是否已证明 owner revision/remote CI | 否；两项继续 HOLD |

## 3. Gate 决定

`WP-G0-05G Clean checkout full replay` 标记 `ENGINEERING_PASS`。`G0.15` 仍为
`IN_PROGRESS / OWNER_ACTION_REQUIRED`；本 review 当时 React Router advisory 仍为供应链 HOLD，
后续本地关闭见 review 29。下一动作更新为 owner 纳管文档 12 指向的 current candidate 与 PENDING
review packet、取消跟踪 `web/index.html` 并取得
远程三 job 证据；本 Review 不授权 G1 runtime、
正式数据库操作或默认 V2。
