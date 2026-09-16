# RAG + Wiki 完善执行日志

规则：追加物质性动作与结果；不覆盖失败；不把计划写成已执行事实。

## 2026-08-29 — EC-00 建立执行控制与基线

### 输入

- 项目成熟度总纲、持续执行台账、全阶段原子 Runbook；
- G1 raw authority、G2 grounded answer、G3 canonical planner、G6 quality、G7 capacity、G8 release 规格；
- 当前源代码审计与 live Wiki 复现结果；
- 当前未清洁工作树。

### 已执行

1. 创建持续目标 `OBJ-RW-L5` 对应的当前 Codex goal；
2. 建立阶段计划列表，第一阶段为文档控制，后续按 G0→G9 排队；
3. 读取并核对既有文档 authority、当前停止点和唯一允许动作；
4. 统计当前工作树：69 个 tracked changes、172 个 untracked paths，共 241 个 status entries；
5. 将七项当前发现 F-01–F-07 映射到既有 T0/T1/T2/T3/T7 原子目标；
6. 新建本目录六份执行控制文档。

### 代码与数据改动

- RAG/Wiki 业务代码：无；
- 数据库、index、generation、release 状态：无；
- 新增文档：`README.md`、`MASTER_PLAN.md`、`TASK_STATE.md`、`EXECUTION_LOG.md`、
  `DECISIONS.md`、`VERIFICATION.md`。

### 只读基线事实

- 针对 Wiki/query/multisource/EvidencePack/E2E 的上一轮定向后端验证：129 passed；
- 针对 Wiki Workbench、TrustedQueryPanel、queryContract 的上一轮前端验证：33 passed；
- 这些结果只说明定向工程基线，不是 clean checkout、真实六源质量或发布资格；
- 当前 Wiki quality fixture hard gates 为 120/120，但 artifact 明确为非 qualification / QUALITY_HOLD；
- 当前容量 artifact 只覆盖 S 层（约 362 pages / 950 refs），不能外推 M/L。

### 结果

- EC-00：`ENGINEERING_PASS`；
- 当前 active task：`T0.28.2 / QUALITY_HOLD / OWNER_ACTION_REQUIRED`；
- 本轮不启动业务实现，避免绕过现有 G0/G1 前置 Gate。

### 文档自检

- 文档数量：6；
- Markdown 相对链接：PASS；
- `active_atomic_id` 数量：1，PASS；
- whitespace/diff check：PASS；
- 本轮新增状态范围：仅 `docs/rag-optimization/development/execution-control/`；
- VC-00：PASS。

### 残余风险

- V24/packet V5 是 2026-08-06 记录的 current candidate，但当前工作树已有后续变化；需先证明其与当前 bytes
  的关系，不能直接沿用历史 current 声明；
- 129/33 定向测试没有替代全仓、双 Python、cleanroom、远程 CI、LIVE-02 或真实数据 Gate；
- F-01 已复现但其修复依赖 G1/G2/G3 合同，提前局部改字符串可能制造新的语义漂移。

### 下一步

按 `TASK_STATE.md` 恢复并执行 T0.28.2；任何状态变化追加新日志，不改写本节。

## 2026-08-29 — T0.28.1R V24/current STATE_DRIFT

### 强制读取

已按执行协议重新读取 README、TASK_STATE、MASTER_PLAN、原子 Runbook、DECISIONS、VERIFICATION 和
最近执行日志；确认原 active task 为 T0.28.2，前置要求是 V24/current bytes 一致。

### 原生验证结果

1. `make g0-admission-verify`：FAILED，`current source set does not match admission manifest`；
2. `make g0-owner-review-verify`：FAILED，packet 重建时被 admission native verifier 拒绝，
   `admission manifest verification failed`。

这两个失败是 verifier 的正确 fail-closed 行为，不是通过放宽断言处理的测试回归。

### 精确 drift denominator

- V24 admitted：1,124；当前 admitted：1,134；
- 新增：10；修改：0；删除：0；Git-state 变化：0；
- scope delta：`documentation +10`；
- exclusions：旧/新计数完全一致，新增/删除 exclusion 均为 0；
- V24 base HEAD 与 current HEAD 均为 `bc3326edc761e3bdb42ed78726a21f314ab44974`。

新增路径由两组组成：

- G5–G8 执行规格：`38_...md`、`39_...md`、`40_...md`、`41_...md`；
- execution-control：README、MASTER_PLAN、TASK_STATE、EXECUTION_LOG、DECISIONS、VERIFICATION。

### 状态转换

- 原 T0.28.2 暂停，未发生 owner review；
- 新 active task：`T0.28.1R / IN_PROGRESS`；
- 决定：保留 V24/packet V5，不覆盖；刷新下一不可覆盖 candidate/packet 后再恢复 T0.28.2；
- RAG/Wiki runtime、数据库、Git history、release 状态均未修改。

## 2026-08-29 — T0.28.1R V25 Intermediate Capture

### Artifact 结果

- admission V25：1,135 files、29,022 excluded denominator、83 listed exclusions、1 release blocker；
- V25 exact verify：PASS，current exclusions match/approved，worktree clean=false，release-ready=false；
- packet V6：1,135 items、10 review groups、83 listed exclusions；
- packet authority：`PENDING_OWNER_REVIEW`、owner=false、reviewed revision=null、remote=false；
- packet canonical verify：PASS。

### 定向验证

- Ruff：admission/owner-review 实现与测试文件 PASS；
- pytest：`test_maturity_g0_admission_v2.py` + `test_maturity_g0_owner_review_v1.py`，15 PASS。

### 文档闭合

V25 验证后写入本日志会改变 admitted documentation bytes，因此 V25 保留为 intermediate evidence；
current pointers 与任务状态直接换代到 V26/V7。V26 build/verify 的最终事实不再回写 admitted source，
而记录在排除于 source admission 的
`artifacts/rag-maturity/g0/admission-20260829-v26/EXECUTION_RECORD.md`，避免再次制造自引用 drift。

## 2026-08-29 — T0.28.1R V26 本地完整回放与供应链 Finding

### 强制读取与边界

进入本批次前重新读取六份执行控制文档、G0 原子 Runbook、owner handoff 与供应链规格；只执行 G0
本地回放、finding 处置和 candidate refresh，不修改 RAG/Wiki 运行时、数据库、release 或 Git history。

### 后端回放

1. 安装 uv managed Python 3.12.13；首次新 `.venv` 因未同步 `test` extra 而缺 Ruff，测试前失败；
2. 按 CI 合同同步 frozen test extra 后，Python 3.12.13 Ruff PASS、完整 pytest 2,126 PASS，449.17s；
3. 恢复 Python 3.13.12 frozen test extra，Ruff PASS、完整 pytest 2,126 PASS，408.68s；
4. 两个解释器均只有同一条已知第三方 Starlette TestClient deprecation warning。

### 前端环境与失败保留

1. 项目要求 npm 11.17.0，而外层 npm 为 10.9.8；全局安装因用户已有 Hermes npm link 以 EEXIST
   停止，没有使用 force；
2. 改用任务临时目录的 npm 11.17.0，不改变用户外层运行时；
3. 修复前证据目录已存在，非覆盖保护停止首次写入；新证据写入 `frontend-local-replay-v2`；
4. V26 lock fresh audit 发现 nanoid 3.3.16 的 1 个 high finding。

### 最小修复与验证

- 在两个隔离副本确认只需把 lock 中 nanoid 3.3.16 更新到 3.3.18；首个错误使用绝对 prefix 的试验
  产生临时路径污染，已丢弃；第二个 cwd 隔离试验证明只有 version/resolved/integrity 三字段变化；
- 使用 `apply_patch` 只修改 `frontend/package-lock.json` 的三个字段；package manifest 不变；
- 严格安装脚本策略 PASS：npm 11.17.0、pending 0、reviewed 2；
- audit 0；CycloneDX 1.5 / 265 components；8 项 SHA-256 全部通过；
- Vitest 33 files/191 tests PASS；typecheck PASS；production build PASS，1,925 modules，1.23s；
- 正式 admission delta：V26/current 都是 1,135 files，added 0、removed 0、changed 仅
  `frontend/package-lock.json`。

### 状态转换

V26 exact 因 admitted lock bytes 变化而按设计失效。V26/V7 不覆盖，转为历史证据；current pointer、
authority 和任务恢复点换代到 V27/PENDING packet V8。新增文档 43 固定回放与修复事实，V27 的最终
exact/canonical/cleanroom 结果只写 source-admission 外的 post-build receipt。

### 资格限制

本地完整回放和 audit 0 不能替代 repository owner、受保护 revision、same-revision remote CI、LIVE-02
或 G1 Entry receipts。程序继续 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`；业务问题 F-01–F-06 未启动。

## 2026-08-31 — T0.28.10D Current-state control synchronization

### Forced pickup and baseline

- 按 stable current state → persistent ledger/log → T0.28.10D plan/matrix/decisions/verification 顺序回读；
- exact owner-reviewed base、owner decision、portable bundle 与 main conservation 均核验；
- 15 个 source baseline SHA 和历史 V27 execution-record SHA 精确匹配；worktree 初始 diff 0；
- characterization 证明 operational prose 仍含过期 active/current snapshot，历史批次文本另行保留。

### Mutation boundary

- 仅修改冻结的 15 个 Markdown；执行控制核心改为 stable resolver + missing-state fail closed；
- 上位/专项文档只同步 operational current/recovery blocks，历史 lineage、run facts、G0→G9 依赖不改；
- workflow/test/runtime/dependency/schema/database/generated output/release mutation 0；
- 旧 DEC-001–008 和本文件此前 sections 均按 append boundary 保留。

### Qualification boundary

本任务只修控制面一致性。protected remote、same-revision CI、external UI reviewer、independent G0 decision、
G0 `QUALIFIED` 与 G1 Entry `PASS` 仍缺失；T1.1.1 和业务问题 F-01–F-06 的 runtime implementation 未启动。
最终 diff/static/link/history/targeted tests、bundle restore 与下一 admission/packet 结果在本轮 candidate evidence
中记录，未完成前不标记 `ENGINEERING_PASS`。

### Pre-commit verification

- exact changed-file allowlist：15/15 Markdown，runtime/workflow/test/dependency 0；`git diff --check` PASS；
- Markdown local links：15 documents / 0 missing；
- operational stale assertions：active T0.28.2=0、V27/V8 current=0；历史 sections 和 append-only logs 保留；
- prior DEC-001–008 与 EC-00/earlier execution bytes 为 exact prefix；
- historical V27 execution record SHA 保持 `b5bc857f...`；
- resolver path + missing-state HOLD 在 README/TASK_STATE/master program/atomic runbook 全部存在；
- G0 `QUALIFIED` + G1 Entry `PASS` 仍为 T1.1.1 前置；
- locked Python 3.13 targeted admission V2 + owner-review：15 PASS。

portable bundle restore、frontend build 和 successor admission/packet 必须在 commit 后完成，因此仍为 pending；
不会为 prose-only diff 扩张到 full backend/full frontend tests。
