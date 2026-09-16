# G0 V26 本地完整回放、供应链修复与 V27 Refresh 执行记录

版本：2026-08-29 V1  
工作项：`T0.28.1R`  
状态：`LOCAL_FULL_REPLAY_PASS / V27 CURRENT_ON_EXCLUDED_RECEIPT / OWNER_REVIEW_PENDING`  
边界：`DEFAULT_V1 / QUALITY_HOLD / NO RELEASE / NO OWNER IMPERSONATION`

## 1. Outcome 与冻结输入

本工作包只完成三件事：

1. 对 V26 源码在 Python 3.12/3.13 与严格前端供应链环境中做本地完整回放；
2. 发现真实 high finding 时，以已验证的最小 lock-only 改动关闭它；
3. 因 admitted bytes 改变，保留 V26/V7 并生成新的不可覆盖 V27/PENDING packet V8。

冻结输入为 HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`、V26 admission、packet V7、
uv 0.11.14、Python 3.12.13/3.13.12、Node 22.23.2 和项目固定 npm 11.17.0。

允许改动仅为受供应链 finding 影响的 `frontend/package-lock.json`、准入 current pointer、执行控制与
相应记录。没有修改 RAG/Wiki 业务逻辑、数据库、generation、release registry 或 Git history。

## 2. 执行前失败与环境处置

- 新建 Python 3.12 `.venv` 首次未包含 `test` extra，导致 `ruff` 在测试前不可用；按 CI 合同执行
  frozen test-extra sync 后重跑，不降低断言。
- 尝试全局安装 npm 11.17.0 时，发现用户现有 `/Users/example/.local/bin/npm` 由 Hermes 占用；安装以
  `EEXIST` 停止。没有使用 `--force` 覆盖用户环境，改用任务临时目录的 npm 11.17.0。
- 首次供应链证据目录已存在，非覆盖保护正确停止写入；修复后的证据写入新目录
  `frontend-local-replay-v2`，历史 finding 不被覆盖。

这些是环境或证据生命周期失败，不是用绕过或防御性分支隐藏的产品回归。

## 3. 后端本地完整回放

| Runtime | Ruff | 完整 pytest | 结果 |
|---|---|---:|---|
| Python 3.12.13 | PASS | 2,126 passed；1 warning；449.17s | PASS |
| Python 3.13.12 | PASS | 2,126 passed；1 warning；408.68s | PASS |

两个解释器只有同一条 Starlette/FastAPI TestClient 第三方弃用提示。回放后恢复 Python 3.13 frozen
test-extra 环境。

## 4. 供应链 finding 与最小修复

V26 lock 固定 `vite 6.4.3 -> postcss 8.5.25 -> nanoid 3.3.16`。npm 11.17.0 fresh audit 报告一个
high finding。先在隔离副本验证 lock 更新，确认把 nanoid 升到 3.3.18 即可关闭 finding，且：

- `frontend/package.json` 不变化；
- package-lock 仅变更 nanoid 的 version、resolved、integrity；
- 不出现临时绝对路径或其他依赖图漂移；
- 修复后 audit 为 0。

实际源修改严格限于上述三个 lock 字段。V26 与修复后 source 的正式 admission delta 为 added 0、
removed 0、changed 1：`frontend/package-lock.json`。因此 V26 exact failure 是预期 fail-closed 结果，
不允许继续把 V26/V7 交给 owner 审阅。

## 5. 前端严格回放结果

| 检查 | 结果 |
|---|---|
| npm identity | 11.17.0 |
| strict allow-scripts | PASS；pending 0；reviewed 2 |
| npm audit | 0 total / 0 high / 0 critical |
| CycloneDX SBOM | 1.5；265 components |
| Vitest | 33 files / 191 tests PASS |
| typecheck | PASS |
| production build | PASS；1,925 modules；1.23s |
| receipt SHA-256 | 8/8 PASS |

不可覆盖的详细本地收据：

- `artifacts/rag-maturity/g0/admission-20260829-v26/LOCAL_REPLAY_RESULT.md`
- `artifacts/rag-maturity/g0/admission-20260829-v26/frontend-local-replay-v2/`

这两处均位于 source admission 外；其中保存命令环境、失败事实、audit JSON、policy、pending、SBOM
和校验和。artifact 自身内容是权威，本文件不复制其摘要散列，避免自引用。

## 6. V27/V8 换代规则

1. V24/V5、V25/V6、V26/V7 全部保留，不覆盖；
2. Make 与 current authority 指向 `admission-20260829-v27/admission.json` 和
   `owner-review-20260829-v8/review-packet.json`；
3. V27 manifest 和 V8 packet 构建后，以各自 source-admission 外的执行收据记录 file/scope/exclusion、
   source/content/review digest 与 verifier 结果；
4. packet 必须继续为 `PENDING_OWNER_REVIEW`，owner/revision/remote 字段不得由本地执行者填写；
5. V27 exact/canonical/cleanroom 的实际结果由 post-build receipt 固定，不再回写本文。

## 7. 验收矩阵

| ID | 检查 | 状态 |
|---|---|---|
| LR-01 | Python 3.12.13 Ruff + full | PASS；2,126 |
| LR-02 | Python 3.13.12 Ruff + full | PASS；2,126 |
| LR-03 | task-local exact npm 11.17.0 | PASS |
| LR-04 | install policy/pending/audit/SBOM checksums | PASS |
| LR-05 | frontend test/type/build | PASS；191 |
| LR-06 | V26 delta 仅一个 lock path | PASS |
| LR-07 | V27 admission exact | POST-BUILD RECEIPT |
| LR-08 | V8 PENDING packet canonical verify | POST-BUILD RECEIPT |
| LR-09 | V27 self-contained cleanroom | POST-BUILD RECEIPT |

## 8. 停止点

本地完整回放和供应链修复不产生 owner authority。V27/V8 通过工程验证后，唯一 active task 仍是
`T0.28.2`：repository owner 逐 path/scope/exclusion 审阅。没有真实 owner-reviewed protected revision、
same-revision remote CI、LIVE-02 与 G1 Entry 外部 receipts 前，不启动 T1.1.1，不修改 F-01–F-06
对应运行时，不访问正式数据库，不发布或切换默认 V2。
