# G0 Portable Exclusion 与 Release-ready 语义

版本：2026-08-06 V1  
状态：`ENGINEERING_PASS / OWNER_REVISION_HOLD / REMOTE_RUN_HOLD`  
本语义记录候选：`artifacts/rag-maturity/g0/admission-20260806-v11/admission.json`（当前 owner source candidate 见文档 12）  
复核：`reviews/26_G0_PORTABLE_EXCLUSION_REVIEW_2026-08-06.md`

## 1. V10 终检发现

V10 clean checkout 在 backend 2,075、frontend 179、typecheck 和 build 全部通过后，verifier 返回：

```text
current_source_verified=true
current_worktree_clean=true
current_release_ready=true
current_exclusions_match=false
release_blocker_count=0
```

`current_exclusions_match=false` 的精确原因不是新增风险，而是 clean checkout 不再含原工作树的
12 张未登记设计图、6 个 SQLite sidecar、6 个 Tauri/Vite 生成文件，且 `.venv/node_modules` cache
分母从 29,103 变为 11,012。旧 verifier 又没有把 exclusion 状态纳入 release-ready，导致输出语义
表面矛盾。

V10 保留为该问题的不可覆盖证据。

## 2. 两层排除语义

V11 将排除判断拆为：

| 字段 | 含义 | 发布要求 |
|---|---|---|
| `current_exclusions_match` | 当前排除路径与所有分母是否和 capture-time 完全相等 | 审计字段；允许为 false |
| `current_exclusions_approved` | 当前是否没有新增不允许的排除对象 | 必须为 true |
| `current_unapproved_exclusion_addition_count` | 新增不允许排除对象数量 | 必须为 0 |

允许变化的 portable exclusions 只有：

- `cache`：clean checkout 的 `.venv`、`node_modules`、编译 cache 分母天然不同；
- `generated_build_output`：由受控前端/原生构建生成，内容由相应 build contract 验证。

新增以下类别一律不批准：敏感路径、数据库或 SQLite sidecar、symlink、未登记文档资产以及其他未
列入 portable allowlist 的 exclusion。即使 `.gitignore` 让 Git 状态显示 clean，verifier 仍直接扫描
scope 并 fail closed。

## 3. Release-ready 公式

V11 固定：

```text
current_release_ready =
    current_worktree_clean
    AND current_exclusions_approved
    AND current_release_blocker_count == 0
```

`--require-release-ready` 必须执行该完整公式。source-exact 仍独立要求 admitted path/role/scope/size/
digest 全相等；两者不可互相替代。

## 4. 对抗性测试

新增测试覆盖：

1. capture-time 未登记图片在 clean checkout 消失、ignored cache 增长：exact exclusion match=false，
   approved=true，release-ready=true；
2. 新增被 `.gitignore` 隐藏的 `frontend/.env.local`：Git clean=true，但 approved=false、addition=1、
   release-ready=false；
3. tracked generated output 仍由 blocker 拒绝；
4. admitted source byte 漂移仍在 release 判定前拒绝。

## 5. V11 退出证据

V11 必须证明：双 Python 完整分母各 2,077；新 synthetic clean checkout backend-before-build 全量
通过；frontend 179/typecheck/build 通过；构建后 exact source=true、Git clean=true、
exclusions approved=true、unapproved additions=0、blocker=0、release-ready=true。exact exclusion
match 可以因已审排除对象消失或 portable cache 分母变化而为 false。

本节记录 V11 当时状态。后续 V14 已在本地移除 React Router finding；Owner revision、GitHub remote
run、same-revision audit/SBOM 与真实浏览器交互证据仍保持 HOLD，见 `29` 文档。
