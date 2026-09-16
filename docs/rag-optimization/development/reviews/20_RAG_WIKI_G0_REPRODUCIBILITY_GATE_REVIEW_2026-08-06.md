# RAG + Wiki G0 可复现基线 Gate Review

日期：2026-08-06  
Gate：`G0`  
判定：`ENGINEERING_PASS / VERSION_ADMISSION_HOLD / QUALITY_HOLD`  
发布边界：`DEFAULT_V1 / NO_RELEASE / production_authorized=false`

## 1. 结论

当前工作树的代码、配置、评估合同和本地完整回归已经恢复为工程绿色，并形成可复制、
verify-only 的 G0 证据包。后续 V3 cleanroom 证明 admission bytes 自带完整 Code Golden 历史，
但完整后端重放又暴露 Experiment authority 对 `.venv` 绝对路径的绑定；V4 显式换代 authority 并
把模块来源规范化为逻辑身份；V6 又关闭 Python minor identity/Notebook AST 漂移，V9 在补入
3 项 CI 合同后双 Python 达到 2,075；V11 新增 2 项 exclusion 对抗测试后，当前完整分母均为
2,077。G0
尚不能判 `QUALIFIED`：当前实现没有进入 owner-reviewed revision；正式
工作树仍跟踪应由构建生成的 `web/index.html`；GitHub CI workflow 尚无远程运行证据；前端
React Router RSC advisory 在本 review 时仍是显式质量例外；后续处置见 review 29。

本 Gate 不放宽 C-B1 断言、不扩大测试排除、不读取正式数据库内容、不改变默认 V1，也不
授权 G1 实现或任何生产放量。

## 2. 修复内容

### 2.1 Code dense publication 配置意图

- 新增 `Settings.rag_code_dense_index` 和 `RAG_CODE_DENSE_INDEX`；默认 `true` 保持既有行为；
- Runtime 只有开关为 true 时才向 `CodeDualWriteCoordinator` 注入 dense publisher/profile；
- C-B1 显式设置 false，AST + exact/sparse treatment 保持 `embedding=not-built`；
- 配置类型、环境变量、默认值和非法值均有合同测试。

### 2.2 不可变 C-B1 artifact 兼容

- 新 C-B1 配置快照升级为 `code-c-b1-config-snapshot-v2`，必须记录
  `rag_code_dense_index=false`；
- 历史 v1 artifact 不被重写，只允许与精确重建的旧 v1 合同逐字段相等；
- 任意字段修改、新 schema 缺字段或伪 legacy 仍 fail closed；
- C-B2/C-B5 固定 anchor 因此可以继续验证历史 C-B1，而新运行保留新配置真值。

### 2.3 验证入口和 CI

- `make backend-smoke`：快速公共合同；
- `make backend-integration`：多源/Wiki 运行路径；
- `make backend-evaluation`：C-B1/C-B2/C-B5 不可变评估链；
- `make backend-full`：ruff + 不带排除项的全量 pytest；
- `.github/workflows/backend-ci.yml`：Ubuntu、Python 3.12/3.13、locked `uv`、只读权限。

## 3. 可验证证据

### 3.1 Sanitized baseline

- 位置：`artifacts/rag-maturity/g0/baseline-20260806-v1/baseline.json`；
- schema：`rag-maturity-g0-baseline-v1`；
- content：`sha256:94c47fd83aea81b60e2fc75b53679d8c2ca07aa934e98ceec6057b398a37d9c5`；
- verify：`verified-quality-hold-baseline`；
- 内容只含相对路径/聚合哈希/开关/计数；`formal_database_accessed=false`。

### 3.2 G0 portable package

- 位置：`artifacts/rag-maturity/g0/g0-engineering-20260806-v1`；
- schema：`rag-maturity-g0-package-v1`；
- exact file set：9；
- manifest：`sha256:87b34e9b58d30404984944f358c58ec6e0e07dda213b363a0efe5f40bc81b7f4`；
- commands：6/6 passed；failed commands：0；
- 原目录 verify：PASS；system-temp portable copy verify：PASS，manifest digest 相同；
- security findings：0；正式 DB/Wiki DB size+mtime 状态前后一致；
- read access 未做系统调用级 instrumentation，因此没有宣称“绝对未读”；
- package 决策：`ENGINEERING_PASS / QUALITY_HOLD / g0_qualified=false`。

### 3.3 命令结果

| 命令 | 结果 | 计数 | 耗时 |
|---|---|---:|---:|
| backend-smoke | PASS | 105 | 1.766 s |
| backend-integration | PASS | 50，1 warning | 4.965 s |
| backend-evaluation | PASS | 23 | 116.929 s |
| backend-full | PASS | 2051，1 warning | 422.507 s |
| frontend-typecheck | PASS | deterministic typecheck | 3.258 s |
| frontend-tests | PASS | 31 files / 178 tests | 3.775 s |

唯一 warning 是当前 FastAPI TestClient 依赖的 Starlette deprecation，不影响本 Gate，后续应在
依赖升级工作包中单独消除。

### 3.4 逐文件 Version-admission candidate

- 历史工具：`src/evidence_rag/evaluation/maturity_g0_admission_v1.py`；
- 历史 artifact：`artifacts/rag-maturity/g0/admission-20260806-v1/admission.json`；
- scope：backend/tests/docs/evals/frontend/native/plugins/web/delivery/G0 evidence；
- 每文件记录 repository-relative path、scope、byte length、SHA-256 和 capture-time Git state；
- 排除 node_modules、Tauri/Cargo target、build/dist/cache、tsbuildinfo、SQLite/WAL 和本地生成物；
- source snapshot identity 不含 Git state，因此 owner commit 后可在 clean checkout 验证同一内容；
- 支持独立 canonical verify、当前 source exact compare、`--require-clean` 和隔离 materialize；
- 不 stage/commit/push，不读取正式数据库，不将当前 candidate 误判为 owner-reviewed revision。

为避免自引用，本文不复制 admission digest；artifact 内的 canonical `content_sha256` 和
`source_snapshot_sha256` 为唯一权威。

V1 经后续对抗性复核确认不能作为最终 clean-checkout 边界：它收录前端生成物和未登记设计
图片，却遗漏 evaluator 所需的 SQLite anchors。因此 V1 被保留为不可覆盖的历史负证据，不再是
Makefile 默认 admission。

### 3.5 V2 refined reproducible candidate

- 工具：`src/evidence_rag/evaluation/maturity_g0_admission_v2.py`；
- artifact：`artifacts/rag-maturity/g0/admission-20260806-v2/admission.json`；
- source set：1,078 files，覆盖 backend/tests/docs/evals/frontend/native/plugins/web/delivery/G0
  evidence；
- database policy：12 个批准的 `evaluation.sqlite3` 只按 opaque bytes 计算摘要；正式应用库和
  6 个 sidecar 均不准入、不打开；
- generated policy：65 个 Web/Tauri/Vite 运行时输出排除；任何已跟踪生成物均阻断 release；
- document policy：12 个未进入资产清单的设计图片排除并保留人工审阅 finding；
- isolated verification：source exact + synthetic clean + blocker 0；Code baseline/evaluation 55；
  frontend 31 files/179 tests + typecheck + build；backend 2,060；ruff 全部 PASS；
- replay order：materialize → locked install → frontend test/typecheck/build → backend full →
  release-ready verify；构建后的 generated files 不能改变 source identity 或污染 worktree。

V2 验证发现并修复两项此前未被正式路径暴露的问题：Codex callable digest 不再包含 checkout
绝对路径；evaluation implementation snapshot 不再持久化物理 repository root。两项均有回归
测试，且旧 artifact/评审记录未被回写。

### 3.6 前端安全例外

- `postcss=8.5.25`，moderate 告警已关闭；
- `react-router-dom=7.18.2` 精确锁定；
- `npm audit` 仍为 2 high / 0 moderate / 0 critical，两个条目来自同一 RSC-mode CSRF advisory
  `GHSA-qwww-vcr4-c8h2`；
- 当前只允许 client-only `HashRouter`，并用测试禁止 RSC/SSR/static-router/server-action 入口。

该缓解不等于漏洞关闭，也不构成 production security pass；等待合适的上游修复版本后必须重新
audit、回归和安全复核。

### 3.7 V3 self-contained cleanroom

- artifact：`artifacts/rag-maturity/g0/admission-20260806-v3/admission.json`；
- source set：1,083 files；在 V2 基础上新增 complete Git bundle、生成器、安全清单、cleanroom
  harness 和 3 项回归测试；
- bundle：34,534,047 bytes，SHA-256
  `ffbf5daf05139c2af922d66e4bed681ce0417a9cc4f820ebb04dacea38b1ff03`；
- identity：固定 13 commits、90 trees、328 blobs；不改 Code Golden V2 package hash、commit、
  tree、entity ID 或 release record；
- security：导入后扫描 264 text/64 binary blobs；7 个高置信命中仅位于 3 个已审 synthetic
  redaction/secret-detection 测试路径；新增路径/类别 fail closed；
- portability：receipt、bundle digest、431-object 分母、ordinary clone、release-ready PASS；
- replay：cleanroom 内 Code Golden + baseline 40/40 PASS；正式工作区 Ruff 与 2,063/2,063 backend
  PASS；但最终 materialized cleanroom 完整后端为 2,059 PASS / 4 FAIL，失败均来自 Experiment
  module binding origin 的 checkout/.venv 绝对路径；旧 V2 admission 仍可 canonical verify。

曾评估 45 KiB sparse object pack，但普通 `git upload-pack` 会因固定 tree 引用缺失 blob 而拒绝
clone。V3 使用完整 history 是为了满足现有真实 clone 合同，不以测试专用旁路掩盖仓库损坏。

### 3.8 V4 portable runtime authority

- artifact：`artifacts/rag-maturity/g0/admission-20260806-v4/admission.json`；
- 文件范围、34.5 MB bundle、431-object 分母、历史安全 allowlist 和 Code Golden V2 身份均不变；
- Foundation current authority 从 v3 升至 v4，E-B0 current authority 从 v6 升至 v7；旧 released
  v1 和 V2/V3 admission 不回写；
- 普通源码模块 origin 投影为 `<module-root>/<module path>`，扩展模块为
  `<module-file>/<filename>`，built-in/frozen 保留其逻辑值；
- macOS/Linux、Windows、extension、frozen 四类回归 PASS；Experiment 专项 123/123 PASS；
- 正式工作树 Ruff 与 backend 2,067/2,067 PASS；最终候选仍必须按 cleanroom 状态机重放并由
  V4 artifact 的 exact/release-ready verifier 验收。

V4 随后已在最终 materialized cleanroom 完成 frontend 179、typecheck、production build、backend
2,067 和 `--require-release-ready`，blocker=0；正式工作树的 blocker 仍是已跟踪生成物与 dirty scope。

### 3.9 V5 execution-document admission

- artifact：`artifacts/rag-maturity/g0/admission-20260806-v5/admission.json`；
- 新增 G1I-01 binding foundation 执行规格与独立复核，并同步总纲、台账、目标卡和索引；
- V2 schema、canonical ID、transaction、M0–M8 migration、dry-run artifact、BF-01–15 和 rollback
  由文档冻结；不执行 DDL、不连接正式 DB、不修改 runtime/test/frontend/lock；
- V4 保留为不可覆盖的完整 runtime qualification；V5 作为当前 source exact 和 owner 审阅候选，
  必须重新经过 admission materialize、self-contained cleanroom 和 release-ready 验证。

### 3.10 V6 runtime qualification 与 V7–V11 admission candidates

- V5 在 Python 3.12 暴露持久 callable identity 含 CPython minor bytecode，以及 Notebook AST dump
  的空字段差异；V5 保留为负证据；
- V6 将跨支持版本的持久 identity 与 same-interpreter strict memory integrity 分层，current
  authorities 显式换代，released artifacts 不回写；
- Python 3.12.13 与 3.13.12 对同一 source snapshot 均为 2,072/2,072，Ruff、前端 179、typecheck、
  production build 和 V6 self-contained cleanroom 均 PASS；
- V6 在本执行记录和独立 review 写入前形成，保留为 runtime qualification evidence；V7/V8 完成
  文档收口；V9 补齐 owner/CI handoff，但 clean full 暴露 generated HTML 隐含依赖；V10 纠正
  source authority，并完成 backend-before-build clean checkout 全链；V11 再分层 exact/approved
  exclusion 并把 release-ready 固定为合取。V11 是产品全链记录；当前 owner-review source candidate
  由文档 12 的 current authority 接续；
- 详细证据见 `16_G0_PORTABLE_PYTHON_IDENTITY_AND_V6_EXECUTION_RECORD.md` 与 review 23。

## 4. G0 工作项判定

| 工作项 | 判定 | 说明 |
|---|---|---|
| G0.1 baseline inventory | ENGINEERING_PASS | canonical/content-addressed/verify-only |
| G0.2 version scope | IN_PROGRESS | 范围已识别；owner revision 和 clean checkout 未证明 |
| G0.3 dense switch | ENGINEERING_PASS | 默认兼容、C-B1 显式关闭 |
| G0.4 C-B1 | ENGINEERING_PASS | 新 v2 + 精确旧 v1 compatibility |
| G0.5 C-B2/C-B5 | ENGINEERING_PASS | 23/23 |
| G0.6 backend CI | ENGINEERING_PASS_LOCAL | workflow 已定义，远程 run 未观察 |
| G0.7 unified verification | ENGINEERING_PASS | 四层 target 可执行 |
| G0.8 complete regression | ENGINEERING_PASS | backend/full + frontend，无排除 |
| G0.9 portable evidence | ENGINEERING_PASS | exact set、checksum、copy verify、tamper tests |
| G0.10 Gate Review | QUALITY_HOLD | 本文；等待 G0.2/远程 CI 后复核 |
| G0.11 V1 admission candidate | ENGINEERING_PASS（已取代） | 工具可验证，但 scope policy 被 V2 取代 |
| G0.12 V2 refined candidate | ENGINEERING_PASS | source/eval/generated policy；隔离 build/full/release-ready PASS |
| G0.13 V3 self-contained history | ENGINEERING_PASS / CANDIDATE_SUPERSEDED | history/security/ordinary clone PASS；full backend 暴露 4 项路径误报 |
| G0.14 V4/V5 portable candidate | ENGINEERING_PASS | V4 路径独立 authority + final cleanroom；V5 纳入 execution-doc contract |
| G0.16 V6–V8 portable Python candidate | ENGINEERING_PASS | 双 Python 2,072；strict tamper；V6 runtime evidence；V8 文档最终候选 |
| G0.17 V9 owner/CI handoff | ENGINEERING_PASS / REMOTE_RUN_HOLD | 3 workflow contracts；全链 CI；1092-file cleanroom；GitHub run 未观察 |
| G0.18 V10 clean-full candidate | ENGINEERING_PASS / OWNER_HOLD | V9 2074/1 负证据；1094-file clean full；frontend 179；release-ready |
| G0.19 V11 portable exclusions | ENGINEERING_PASS / OWNER_HOLD | 2077 dual Python；ignored secret fail closed；1096-file portable release-ready |
| G0.15 owner admission | IN_PROGRESS | 正式 revision、tracked generated cleanup、clean CI 尚未完成 |

## 5. 剩余 P0 与下一步

### P0-01：Version admission 与生成物边界

当前 HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974` 对应的工作树仍有 69 个
modified/staged 和大量 untracked 实现；`rag/`、Wiki、测试和文档不能从 HEAD 重建。
详细范围见 `12_G0_VERSION_SCOPE_MANIFEST.md`。

关闭条件：

1. owner 审阅现有大范围用户变更；
2. 形成包含核心实现、测试、lock、CI、docs 和固定 anchors 的一致 revision；
3. 从版本跟踪中移除 `web/index.html`，但由前端 build 稳定重建；
4. 新 clean checkout 按 cleanroom → frontend build → backend full → release-ready verify 顺序重放；
5. 至少一次 GitHub Python 3.12/3.13 CI 通过；
6. 生成新 G0 package，不覆盖当前 QUALITY_HOLD 负证据。

### P1-01：前端供应链安全例外

本段保留 review 当时状态：React Router RSC-mode advisory 由 client-only 架构和防回归测试降低可达性，但 audit 仍为
high；官方 patched 版本当前尚不可由 npm registry 安装。关闭条件为可安装的安全上游版本、locked upgrade、audit 归零或经 owner 批准的正式风险接受、完整
前端回归与安全评审；在此之前不提升为 production security pass。

在这些条件满足前，G0 保持 `QUALITY_HOLD`，G1 只允许设计，不开始实现。
