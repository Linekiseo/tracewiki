# G0 Portable Exclusion Gate Review

日期：2026-08-06  
判定：`ENGINEERING_PASS / OWNER_REVISION_HOLD / REMOTE_RUN_HOLD / SUPPLY_CHAIN_HOLD`  
发布：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. 结论

V11 关闭 V10 暴露的 exclusion/release-ready 语义矛盾。排除对象完全相等保留为审计字段；发布门
改为要求没有新增不批准排除对象。cache 和受控 generated output 可跨 clean checkout 变化，新增
敏感文件、数据库/sidecar、symlink 或未登记文档资产一律 fail closed。

## 2. 关键判断

| 问题 | 结论 |
|---|---|
| clean checkout 少了本地排除垃圾是否应失败 | 否；缺失 exclusion 降低风险，不改变 admitted source |
| cache 数量变化是否应改变 source identity | 否；cache 不进入 source identity |
| 新增 ignored `.env.local` 是否能绕过 Git clean | 否；scope scan 得到 unapproved addition 并拒绝 |
| 新 generated output 是否任意可信 | 否；仅 admission policy 识别其类别，内容仍须通过 frontend/native build contract |
| release-ready 是否仍可忽略 exclusion 风险 | 否；必须 `clean AND approved AND blocker=0` |
| V10 是否被回写 | 否；V10 保留 match=false/release=true 的历史输出证据 |

## 3. Gate 决定

`WP-G0-05H Portable exclusion semantics` 标记 `ENGINEERING_PASS`。V11 是 portable-exclusion
产品全链记录；当前 owner-review source candidate 由文档 12 的 current authority 接续。
G0 仍因 owner revision、remote run 与供应链 advisory 保持 QUALITY_HOLD。本 Review
不授权 commit、push、正式数据库访问、G1 runtime 或默认 V2。
