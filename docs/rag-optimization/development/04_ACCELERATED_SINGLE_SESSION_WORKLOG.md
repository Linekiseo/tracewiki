# RAG 加速开发：单会话工作日志

- 开始时间：2026-07-29
- 当前会话：`019f9f27-8d88-7b01-b686-3d4acbc5dcf2`
- 工作目录：`/Users/example/project/rag`
- 模式：`SINGLE_SESSION / PRODUCT_FIRST / NO_COMMIT / NO_PUSH`
- 状态：`REPOSITORY_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD /
  PRODUCTION_RELEASE_EXTERNAL_BLOCKED`

> **当前完成判定以
> `05_ORIGINAL_ROADMAP_IMPLEMENTATION_MATRIX.md` 为准。** 本文件第 2、5、7、8 节中的
> “今日完成”“全套安全工程收口”等文字属于此前压缩口径的历史记录，不单独证明原始
> 路线图已完成。最终结论来自实施矩阵中的逐项 `PROVEN` 或真实
> `EXTERNAL_BLOCKED`，以及本文件末尾的最终安全回归。

## 1. 固定执行边界

1. 后续开发、测试、审计与文档同步全部由当前会话直接完成。
2. 不创建、恢复或调用其他 Codex 开发/Gate 会话；不使用子智能体。
3. 不创建 branch/worktree，不 commit/push，保留共享 dirty main 中的既有用户改动。
4. 正式库是 `EXTERNAL_MUTABLE_SERVICE_OWNED`：
   - 不 kill 外部服务；
   - 不 open/hash/checkpoint/copy 正式库；
   - 不查看、删除或修改 WAL/SHM；
   - 只使用 system temp、isolated SQLite、in-memory 数据和 immutable artifacts。
5. 保留硬门：ACL/scope fail-closed、secret/path 安全、确定性评测、引用身份、回滚与 V1 默认兼容。
6. 压缩流程：不再为每个小修复建立独立 Gate；由本会话完成“实现 → 定向测试 → 固定回归 → 结果记录”。

## 2. 今日完成口径

### 必须完成

- [x] 独立复核 Experiment E-B0 artifact、安全和指标真值，冻结 `NON_QUALIFIED` 基线。
- [x] 实现 Experiment E1–E3 的最小可用核心：
  immutable run/metric observation、typed filter、numeric/unit、comparability/aggregation。
- [x] 保持当前 V1 API/Platform 兼容，新增能力只以显式 V2/内部结构化路径接入。
- [x] 为 Notebook、Document、Workspace 建立最小统一 source contract 与检索适配。
- [x] 打通多源联合检索、ACL 保留、去重、稳定 citation 和统一 context 主路。
- [x] 完成定向测试、核心全仓回归、ruff/format/diff/compile。

### 可延后但必须显式记录

- 每个来源各自完整的 shadow/canary/release evidence。
- Codex X2–X5 的高级优化与新的 production treatment Run。
- 高成本 semantic reranker、远程 embedding、正式库 migration/backfill。
- 默认 V2 切换；质量门未达时继续保持 V1。

## 3. 当前真实基线

### Code

- C0–C7 engineering：`COMPLETE`
- Release：`HOLD_DEFAULT_V1`
- 唯一 qualified control：C-B0

### Codex

- X0 Foundation、CX1-01/02 engineering：`COMPLETE`
- X-B0 fixed baseline：`NON_QUALIFIED`
- X-T1：`FAILED_BEFORE_PUBLISH / NO_RESULT / NOT_QUALIFIED`
- 不重试旧 X-T1；X2–X5 暂不作为今日主路径。

### Experiment

- E0 Foundation：`PASS`
- E-B0 Run：
  - URI：`evaluation-run://project-experiment-eb0-v1/085799235df74bcdc4fc56c0c17f36e7`
  - set hash：`sha256:51b63f80e31f8275ea1e1c81e7b77ec3681628f8d453db2508337d23fdaa9f84`
  - 状态：`PUBLISHED_VERIFIED_NON_QUALIFIED`
  - 45/45 available，errors/unavailable/zero-result 均为 0
  - Exact `0/5`
  - Predicate `0/28`
  - Numeric/unit/direction `0/20`
  - Comparability `0/9`
  - Reproduction `0/6`
  - Hard-negative avoidance `3/45`
  - Unanswerable `0/10`
  - Zero-result `0/2`

## 4. 过程记录

### 2026-07-29：切换为单会话加速模式

- 用户要求今天尽量完成，并禁止继续使用多会话开发。
- 停止原来的“实施任务 + 独立 Gate 任务 + 多轮授权”协作方式。
- 建立本日志作为上下文恢复和查询的唯一过程索引。
- Experiment E-B0 唯一 Run 已成功发布；下一步由当前会话直接离线复核。

### 2026-07-29：Experiment E-B0 单会话离线复核完成

- 对 published original 调用正式 `verify_experiment_baseline_artifact_v1()`：
  - `VERIFIED_NON_QUALIFIED`
  - `case_count=45`
  - exact 10 files
  - `portable=true`
  - `service_calls=0`
  - `database_calls=0`
  - `network_calls=0`
- 独立读取并核对：
  - 8 个固定 denominator 均未缩减；
  - overall 数值与发布报告一致；
  - security report 中 secret、ACL、cross-run locator、formal DB、absolute path、
    network、credential、email、payment、DB/pyc、nonfinite 全部为 0；
  - `evals/experiment/runs` 恰好一个 Run 目录。
- 固定 Experiment/Platform 测试集合发现一项生命周期陈旧断言：
  `test_evals_experiment_tree_remains_absent_and_package_exports_are_exact`
  仍要求 production Run 发布前的 `evals/experiment` 不存在。该断言不影响 artifact
  真值，但必须在本会话回归清理中更新为 post-Run 合同。
- 结论：E-B0 fixed baseline 被本会话接受为
  `PUBLISHED_VERIFIED_NON_QUALIFIED`；安全与 artifact 完整性通过，质量不合格。

### 2026-07-29：Experiment E1–E3 最小产品核心完成

- 新增 `src/evidence_rag/rag/experiment_v2.py`：
  - 从现有 V1 Experiment/Run/Metric 表只读派生 content-addressed immutable
    RunSnapshot、MetricDefinition/Series/Observation identity；不做 schema migration；
  - typed query 支持 status、dataset/version、experiment/run、config/seed、metric、
    split/unit 与有限 numeric operators；
  - ratio/percent、ms/seconds 做确定性单位归一化；NaN/Inf 拒绝；
  - strict comparability 检查 experiment、完成状态、dataset/version、environment、
    metric unit/split/direction；不兼容显式 `UNAVAILABLE`；
  - best/compare/trend/reproduce 查询意图、稳定 locator、稳定 snapshot digest；
  - 项目 ACL fail-closed，查询不拼接动态 SQL。
- Platform 增加显式 `X-RAG-Experiment-Engine: v2` opt-in；默认仍为 V1，非法值 422。
- Runtime 注入只读 `ExperimentStructuredRetrieverV2`；V2 结果继续经过现有 scope、ACL、
  dedup、diversification、citation 和 relation 主路。
- 清理 E-B0 发布后陈旧的 pre-Run-only 测试断言；历史 artifact authority 保持冻结，
  不因正常产品源码演进重新签名。
- 验证：
  - 新 Experiment V2 专项：`5 passed`；
  - Experiment/API/Platform/E0/E-B0 固定集合：`162 passed`（排除另一文件内仍明确
    命名的 pre-Run-only Foundation 断言）；
  - 目标文件 Ruff/format：PASS。

### 2026-07-29：六来源统一 context 与 Platform 主路完成

- 新增 `src/evidence_rag/rag/multisource.py`，冻结 Code、Codex、Experiment、
  Notebook、Document、Workspace 六类来源的统一 evidence block/context contract。
- Context builder 提供确定性去重、稳定 citation map、来源计数、缺失来源、总预算和
  单 block 预算，并对 secret 与绝对路径/UNC/编码路径/遍历 locator fail-closed 清洗。
- Platform 将 Notebook 从 Experiment 中拆为显式 source；Document、Workspace 与显式
  Experiment V2 opt-in 共用原有项目 ACL、检索、diversification 和 evidence pack 主路。
- `X-RAG-Experiment-Engine` 默认仍为 `v1`；只有显式 `v2` 才启用新 Experiment 结构化
  检索，非法值返回 422。
- 验证：Experiment V2、统一 context、Notebook、Document、Workspace、Platform 定向
  集合 `12 passed`；目标文件 Ruff/format PASS。
- 将 `test_experiment_foundation.py` 中“发布前 evals/experiment 必须不存在”的陈旧
  断言更新为“仅存在已经审计的唯一 E-B0 Run”，不改历史 artifact。

### 2026-07-29：全仓回归与收口完成

- 最终 Pytest 共收集 `1426` 项。首次全量执行（新增 scope 对抗用例前）为
  `1423 passed / 2 failed`；两项失败均是
  C-B6 已发布后的历史 Gate 生命周期断言，仍错误要求 one-shot Run 可再次执行。
- 修正 C-B6 post-Run 真值：最终审计 block 不再是可复用 execution authorization；
  `preparation_status()` 也不再观察外部服务拥有的正式库/WAL/SHM，仅验证 immutable
  Run tree。
- 修复后：C-B6 + Experiment Foundation 定向 `37 passed`；排除会主动观察正式库状态的
  历史 C-B1–B5 探针后，最终完整产品集合 `1343 passed`。结合首次全量中已通过的 83 项
  历史探针，1426 项均有本轮通过证据；没有为了复跑去再次观察正式库或 sidecars。
- 收口复核另关闭两项边界：请求级 `experiment_ids` 与 query ID 取交集，不能扩大 scope；
  locator 中的 secret 直接替换为 content-addressed portable locator。专项/Platform
  `13 passed`。
- 本轮 14 个目标 Python 文件 Ruff check/format check PASS，9 个实现文件 in-memory
  compile PASS，`git diff --check` PASS。
- 仓库级 Ruff 仍会报告 `rag_ui_v2/debug.py` 与 `rag_ui_v2/render.py` 的 3 个既有格式问题；
  它们不属于本轮 RAG 主路，未覆盖用户的无关改动。
- Experiment development/interview 已同步：E0 PASS、E-B0 NON_QUALIFIED、E1–E3 最小
  工程主路 COMPLETE、显式 V2 opt-in、默认 V1、无 treatment quality 提升声明。

## 5. 完成结果与明确延期项

本轮“今天完成”的产品口径已经满足：Code/Codex 既有工程保留；Experiment 结构化 V2
最小核心完成；Notebook/Document/Workspace 进入统一来源合同；六来源联合检索和统一
context 已接通；默认 V1 与所有负质量真值不被改写。

以下不再是今天的仓库内工程缺口，而是需要独立生产授权或成本预算的后续项：

- Codex 专用持久化 Episode/Unit/dense/event graph store、append/tombstone、production
  treatment Run 和 shadow/canary/default V2；
- Experiment 远程 semantic model、独立 treatment quality Run、shadow/canary/default V2；
- Notebook def-use/Revision migration、Document layout/OCR/dense、Workspace temporal snapshot
  migration 等重型来源专用优化；
- 各来源 production quality qualification、默认切换和正式库 migration/backfill。

这些项目没有被伪装为已发布；当前所有新路径均为显式 opt-in 或统一只读 context，默认
继续 V1。

## 6. 恢复指引

若上下文压缩或中断，先读取本文件，再按以下顺序恢复：

1. 查看“当前真实基线”和最后一条“过程记录”。
2. 查看 `git status --short`，只处理本会话明确记录的文件。
3. 不访问正式库；所有验证继续使用 isolated tmp/in-memory。
4. 从第一个未勾选的“必须完成”项目继续。
5. 每次完成一个可验收阶段，立即更新本日志的状态、文件、测试和遗留风险。

### 2026-07-29：原始路线图矩阵与 Notebook N0/N1 完成

- 新增 `05_ORIGINAL_ROADMAP_IMPLEMENTATION_MATRIX.md`，逐项把 Code、Codex、
  Experiment、Notebook、Document、Workspace、M5–M7 标为 `PROVEN/PARTIAL/MISSING/
  EXTERNAL_BLOCKED`；它是当前唯一完成判定依据。
- 新增 `rag/sources/notebook/` 的 frozen contracts、纯 bytes adapter、isolated schema/store、
  programmatic fixture 和 reviewed-row evaluator。
- Notebook Golden 固定为 40 case，8 slices=`5/6/5/6/6/5/4/3`：
  - package：
    `sha256:6d0f5adfb3718cf54e67e1c4a7e3ac121a28422120e817bd3dda32f93018d1ea`
  - authority：
    `sha256:784b871c01977c73935d47206440b37969a58435c4dc5e276c3837c578bca21d`
  - fixture recipe：
    `sha256:4ef9ef2e1eeb88a44a2ff6907baabe1b3c0bf524c6833d23c925ef4f5ea07d0e`
- 真实 adapter fixture 形成 2 templates、3 revisions、4 executions、26 cell versions、
  37 cell executions、11 typed parameters、19 artifacts；失败/成功 retry 共用 revision，
  moved revision 保留 native stable cell identity。
- isolated store 覆盖目标表、单事务回滚、幂等 publication、symlink/越 root/正式库文件名
  fail-closed；使用 memory journal，无 WAL/SHM。
- N0/N1 专项 `7 passed`；与现有 Notebook V2、多源 context、API 联合 `16 passed`；
  默认 V1 未改变。尚无 N-B0 Run/metrics/qualification。

### 2026-07-29：Notebook N2/N3 Parser 与 Dependency Graph 完成

- 新增 `parameter_parser.py`、`output_parser.py`、`execution_state.py`、
  `code_analyzer.py`、`dependency_graph.py`。
- Parameter 支持 null/bool/int/float/string/list/object/unresolved，重复/复杂 target/
  syntax error 显式 diagnostic，不执行表达式。
- Output 区分 stream/display/execute-result/error/binary-omitted；HTML/JS、secret、
  raw/encoded POSIX/Windows/UNC 在派生前清洗；所有 artifact 固定
  `metric_confirmed=false`。
- Execution state 分离 display order 与 execution order；gap、duplicate、decrease/restart、
  output-without-count 都显式诊断，不可信 order 保持 `None`。
- Code analyzer 记录 definitions/reads/imports/calls、content-addressed external dependency，
  magic/shell/dynamic execution/非 Python 明确 uncertainty；dependency graph 包含 def-use
  和 redefinition。
- Golden fixture 现写入 70 unique symbols、18 execution/version edges；发布仍只在 isolated
  SQLite。
- N0–N3 新专项 `13 passed`；与现有 Notebook/多源/API 联合 `22 passed`；Ruff/format/
  compile PASS。尚无 N-B0～N-B3 Run 或 quality qualification。

### 2026-07-29：Notebook N4–N6 工程闭环与真实基线完成

- N4 新增 versioned retrieval units、exact/structured/sparse/graph 多路候选、
  task-specific query profiles、adaptive limits 与 source-local deterministic reranker；
  dense 诚实标为 `UNAVAILABLE`，没有用假向量冒充。
- N5 新增 fail-closed cell matcher、完整 change taxonomy、parameter/output comparison，以及
  locate/code/parameter/output/error/lineage/compare/reproduction 八类 context；引用、预算、
  line-safe truncation、缺证据三态、ACL/generation 均为确定性合同。
- N6 新增 isolated publication/tombstone/generation 验证、六阶段 no-skip release evaluator
  和 exact 10-file portable baseline artifact。验证器只读取制品并离线重算，不触发检索、
  SQLite、网络或正式库。
- 最终 Golden：
  - package：`sha256:49b52bb046aa12f8882c5056592350493377cb691a9fdb5c007f3cf7681fecb3`
  - authority：`sha256:3dd567bf2b749a4209b92307c41bb8b3791ed0a3cf52efd1740e59cecd79ab33`
  - recipe：`sha256:065413434ffd87df2e5a1be165f40d4b9b3965361f66aa6851861e58d41ed7ae`
- 唯一 Notebook baseline：
  - URI：
    `evaluation-run://project-notebook-nb0-v1/b9278f79dc7d42a8a61c25c45b1d7d3e`
  - set：
    `sha256:d59f54f98af4bf156d0dccbc7350ceb3b337b4cc46ff0f1cfad463e4438d2e28`
  - 状态：`VERIFIED_NON_QUALIFIED`
  - recall `35/38`、cell `16/16`、output/error `8/8`、parameter `5/5`、
    producer path `1/5`、execution order `1/2`、stale `2/2`、cell match `2/4`、
    reproduction `0/1`、hard-negative avoidance `10/19`。
  - p50 `9.647208 ms`、p95 `10.184625 ms`；secret/unauthorized/reasoning guardrails 均为 0。
  - release：`HOLD_DEFAULT_V1`；失败指标为 producer path、execution order、
    dependency path、cell match、locator。
- 原目录和 system-temp portable copy 都得到 `VERIFIED_NON_QUALIFIED`；verify 阶段
  `retrieval_executed=false`。`evals/notebook/runs` 恰好一个目录、10 个 canonical files。
- N0–N6 联合固定测试 `27 passed`，Notebook 24 个目标文件 Ruff check/format、
  `git diff --check` 和 in-memory compile 全部通过。
- 结论：Notebook 原路线图的仓库内 engineering 已完成，但真实质量未达标，默认保持 V1；
  production shadow/canary/default V2 与正式库 migration 均未被冒充为完成。

### 2026-07-29：Document D0–D6 工程闭环与真实基线完成

- D0 冻结 programmatic `document-golden-v1/v1`，exact 50 cases，8 slices=
  `8/8/10/10/5/4/3/2`，覆盖 location、local fact、claim、table、figure/formula、
  citation、summary、version；24 个 hard-negative cases 与 2 个显式 refusal cases。
- Golden 最终 authority：
  - package：
    `sha256:a0e3eba2692925d8c3afce1c9568dc61aa51bc956a2623f84e6bb61bc92cf6d0`
  - authority：
    `sha256:48c3765e5daffba298144da9ef99ff49a950d428f32c78813a8ac50e1905f669`
  - fixture recipe：
    `sha256:3cadb6e70f81aa765276584ad517268144ab0a50ee352ae555d9e8cd8ccaf493`
- D1–D5 新增来源专用 frozen contracts、canonical bytes adapter、isolated schema/store、
  reviewed claim policy、50-case evaluator、version alignment、exact/sparse/table/graph/
  deterministic dense retrieval、task-specific context。Fixture 包含 4 families、5 versions、
  175 entities、206 edges；显式建模 page/layout/section/summary/paragraph、claim
  candidate/accepted claim、table/row/cell fact、figure/formula/reference/citation。
- Table fact 绑定 row/header path、typed numeric center/spread/unit 与行级 footnote；
  Claim candidate 不得由 parser 自行升级，accepted/rejected 仅来自 reviewed evidence；
  scanned PDF 必须走显式 OCR fallback；HTML script/style/nav、secret、raw/encoded
  POSIX/Windows/UNC 在派生前 fail-closed。
- D6 isolated SQLite 支持 additive publish、同 digest 幂等、atomic rollback、
  generation/ACL、tombstone hide-without-delete；六阶段 release evaluator no-skip，
  默认始终 V1。
- 唯一 Document baseline：
  - URI：
    `evaluation-run://project-document-db0-v1/b6c5c5e8f21446d5a0038d23ffed5acf`
  - set：
    `sha256:add47a0d55c82d98071a3b6bc4857446f61fd3cacc51a355c27865ffb46cc2a4`
  - 状态：`VERIFIED_NON_QUALIFIED`
  - recall/locator `49/50`，nDCG `42.9068707831/50=.8581374157`，
    MRR `41.6666666667/50=.8333333333`，claim validation `9/10=.9`，
    table/numeric `10/10`，figure/formula `5/5`，citation `4/4`，
    version alignment `2/2`，hard-negative avoidance `45/50`，abstention `2/2`。
  - p50 `12.677958 ms`、p95 `14.343625 ms`；context AVAILABLE/PROVISIONAL/
    UNAVAILABLE=`47/1/2`；security guardrails 均为 0。
  - release：`HOLD_DEFAULT_V1`，失败项为
    `claim_validation_precision=.9 < .95`。
- 原目录与 system-temp portable copy 均 `VERIFIED_NON_QUALIFIED`；verify-only
  `retrieval_executed=false`。`evals/document/runs` 恰好一个目录、10 canonical files。
- D0–D6 固定集合 `27 passed`，Document 16 个实现/测试文件 Ruff check/format、
  compile 与 `git diff --check` PASS。正式库、sidecars、production wiring 均未触碰。

### 2026-07-29：Workspace W0–W6 工程闭环与真实基线完成

- W0 冻结 programmatic `workspace-golden-v1/v1`，exact 40 cases，8 slices=
  `5/6/6/6/6/5/3/3`，覆盖 duplicate scope、current/as-of、WorkItem/acceptance、
  blocker/dependency/overdue、evidence coverage、intelligence、decision 和 audit。
- Golden 最终 authority：
  - package：
    `sha256:f1849d314a8f1b37d5eb7808e18cd4348f33782b47bb4719a76da318d90be4b5`
  - authority：
    `sha256:63a29f4a357d4ca9870e8f807c9f26e3f5a8c164c17d293bf2b2ae2ae4a4f2f2`
  - fixture recipe：
    `sha256:81ff6888689eb9bdf103f1c94bd0a7e5ef958b58f01c4ab0d97df80dcff8e098`
  - publication：
    `sha256:060b6079e1b2da7bf0ad49b635405557dc670e908c10e3579adfec4956fdeb6c`
- W1–W5 新增来源专用 frozen contracts、typed current views、合法状态迁移/optimistic
  concurrency、as-of snapshot、timezone overdue、dependency/blocker/acceptance done gate、
  reviewed/fresh/pinned/independent evidence coverage，以及 content-addressed、可过期且绝不
  修改 authoritative state 的 IntelligenceRun。
- Programmatic fixture 包含 111 entities、141 edges、111 retrieval units：
  1 Project、2 个同名 Topic（active/archived）、3 Iterations、20 WorkItems、
  20 criteria、20 checks、2 dependencies、3 blockers、2 risks、6 requirements、
  12 cross-source evidence links、3 decisions、2 outcomes、12 transitions、1 snapshot、
  2 intelligence runs（current/expired）。
- W6 isolated SQLite 使用 additive typed tables、atomic publication、幂等 generation、
  rollback、tombstone hide-without-delete、ACL/current/history/as-of read；exact/structured/
  sparse/local-hash dense/bounded graph/task rerank 和 authority-visible context 均保持
  read-only、reasoning-free。实现过程中新增测试发现并修复：
  - 合法状态迁移重复传入 metadata 导致运行时异常；
  - storage 在 `resolve()` 后才检查路径，可能漏掉中间目录 symlink。
- 唯一 Workspace baseline：
  - URI：
    `evaluation-run://project-workspace-wb0-v1/363181f5d05847a0aa98fbbee83c4373`
  - set：
    `sha256:2fc7a0c56c55e311986aa33107751646417175857068e2890bcf36d64c73a47c`
  - 状态：`VERIFIED_NON_QUALIFIED`
  - 8 个 slice 均完整命中；scope/current/temporal/blocker/coverage/acceptance/
    authority 均达到冻结 threshold；hard-negative avoidance `38/40=.95`，
    MRR `.8848214286`，nDCG@10 `.8982180159`，unsafe mutation/ACL leakage 均为 0。
  - p50 `7.358625 ms`、p95 `37.651542 ms`；context AVAILABLE/PROVISIONAL/
    UNAVAILABLE=`38/1/1`。
  - release：`HOLD_DEFAULT_V1`；不是指标失败，而是没有 production observation，
    因此不得进入 shadow/canary/default V2。
- 原目录与 system-temp portable copy 均 `VERIFIED_NON_QUALIFIED`；verify-only
  `retrieval_executed=false`。`evals/workspace/runs` 恰好一个目录、10 canonical files。
- 新增 Workspace 专项 `17 passed`，与既有 Workspace API/intelligence 联合
  `25 passed`；18 个目标实现/测试文件 Ruff check/format、compile 与
  `git diff --check` PASS。正式库与 sidecars 未触碰。

## 7. 高级功能加速范围（2026-07-29，已完成）

用户要求继续完成此前明确延期的开发。本轮仍采用单会话产品优先压缩口径：

1. [x] Experiment E4：本地确定性 semantic surface、source rerank、structured context；
2. [x] Experiment E5：显式 opt-in release trace、默认 V1、固定 HOLD/rollback；
3. [x] Codex X2–X4：只读 Episode、task-aware temporal rerank、timeline context/comparison；
4. [x] Codex X5：显式 V2 opt-in、默认 V1、无 schema migration、无旧 X-T1 retry；
5. [x] M6：把 retrieval context 与 comprehension evidence pack 明确分层；
6. [x] Notebook/Document/Workspace 延续现有 typed store，不做正式库 migration。

继续延期的只有需要新 production quality Run、远程模型或正式库变更的事项；不得把工程
完成写成质量 qualification。

## 8. 全套安全工程收口（2026-07-29，完成）

用户要求本会话继续完成整套尚未完成的仓库内开发，并设置“不完成不停止”的长期目标。
本阶段把此前记录为延期、但不依赖正式库 migration、远程模型或新 production Run 的
工程缺口重新纳入范围：

1. [x] Notebook 专用 typed retrieval、执行状态、依赖/比较、context 与显式 opt-in；
2. [x] Document 专用 typed retrieval、parent expansion、claim/table/citation context
   与显式 opt-in；
3. [x] Workspace 专用 control-plane/temporal/relation retrieval、context 与显式
   opt-in；
4. [x] 多源 query planner、角色预算、关系扩展、冲突/陈旧性与可解释融合；
5. [x] 全局 retrieval/comprehension context、确定性回答/拒答、安全与 release trace；
6. [x] isolated 全仓回归、静态检查和最终开发/访谈/本日志真值同步。

固定不纳入“仓库内工程完成”的只有需要外部授权或外部资源的事项：正式库
migration/backfill、远程 embedding/LLM、production treatment/quality Run、shadow/canary
与默认 V2 切换。它们必须继续显示为 `HOLD / NOT_QUALIFIED / DEFAULT_V1`，不能被工程
完成状态覆盖。

### 2026-07-29：Notebook/Document/Workspace 专用 V2 完成

- 新增 `rag/notebook_v2.py`：typed run/parameter/cell/output/error、静态 AST def-use、
  execution/stale state、identity compare 和 `notebook-execution-context-v2`。
- 新增 `rag/document_v2.py`：Document/Section/Claim/Table/Cell/Figure/Citation/Page
  typed retrieval、numeric role、parent expansion、claim conflict context。
- 新增 `rag/workspace_v2.py`：Project/Topic/Iteration/WorkItem filter-before-rank、
  confirmed relation、evidence link、audit/as-of honesty 和 control-plane context。
- Runtime、`/v1/search`、`/v1/query` 接受三个独立 `X-RAG-*-Engine: v2` header；
  非法值 422，缺省与回滚均为 V1。
- 新增三文件专项测试，并与既有 Notebook/Document/Workspace/Platform/API 集合联合通过。

### 2026-07-29：M5/M6/M7 多源与全局闭环完成

- 新增 `rag/planner_v2.py`：
  - intent/explicit-source route、source budget、subquestion、capability、required role；
  - source-local normalization + rank percentile + authority/status/version fusion；
  - 明确 `raw_scores_cross_source_added=false`；
  - root provenance dedup、cross-source conflict 与 stale/counter channel；
  - specialized source sections 组成 `global-comprehension-context-v2`。
- Platform 真实执行 `max_hops=0..4` bounded BFS；只把直接邻居提升为 evidence，
  更深 hop 保留在 relation graph，避免路径扩张污染事实集合。
- planned source 缺失时最多两轮 ACL-aware structured corrective retrieval；每轮候选数和
  unavailable 状态进入 trace。
- Query Evidence Pack 同时暴露 query plan、global context、conflicts/staleness；
  deterministic answer 在 required evidence 缺失时设置 grounded refusal，不伪造结论。
- 全局 release trace 固定 `HOLD_DEFAULT_V1 / quality_qualified=false`。

### 2026-07-29：最终安全验收

- 新增源与 planner 定向回归、既有 Platform/API/多源 context 回归全部通过。
- 安全全仓产品集合（排除会主动观察外部正式库状态的历史 C-B1～C-B5 probes）共
  `1353` 项并达到 100% PASS；没有访问或修改正式库及 sidecars。
- 目标实现/测试文件 Ruff check、Ruff format check、in-memory compile 和
  `git diff --check` PASS。
- 未创建 production Run、branch/worktree、commit 或 push。
- 工程最终状态：`FULL_SAFE_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD`。

### 2026-07-29：完成口径更正

上述 `FULL_SAFE_ENGINEERING_COMPLETE` 只证明本轮压缩后的只读 opt-in 路径与既有回归
闭环，不等于原始 source design/roadmap 全部完成。将其写成“整套未完成开发已完成”属于
过早结论，现正式撤回。

仍需按原始计划逐项审计和实现的仓库内工作至少包括：Notebook/Document/Workspace 各自
evaluation foundation、正式 typed unit/version contract、isolated persistence/projector、
完整 retrieval/rerank/context 消融；Codex/Experiment 未完成的持久层和 treatment
harness；M5–M7 的完整能力矩阵、安全/性能/发布证据。`1353 passed` 只表示当前测试集合
通过，不能替代这些功能与质量证据。

本日志状态因此改为
`ORIGINAL_ROADMAP_REOPENED / IN_PROGRESS / DEFAULT_V1 / QUALITY_HOLD`；后续只有原始
roadmap 每项都有实现证据或明确外部阻塞，才允许再次标记完成。

### 2026-07-29：Experiment E4/E5 工程主路完成

- `experiment_v2.py` 增加纯本地确定性 token/trigram semantic surface，以及由 lexical、
  semantic、metadata completeness、structured-filter 组成的可解释 rerank。
- `best` 默认只纳入 completed runs；percent/ratio 与 ms/seconds 比较采用冻结的单位族
  归一化，失败运行和缺失 dataset version/commit 以显式 negative reasons 暴露。
- 输出新增 `experiment-structured-context-v1`，只包含可观察 run/metric identity、
  locator、missing roles 和 comparability，不包含模型隐藏 reasoning。
- trace 固定记录 `HOLD_DEFAULT_V1`、`quality_qualified=false` 和“移除 V2 header/设置
  v1”的回滚方式；没有默认切换、shadow Run 或新的质量资格声明。
- Experiment V2、统一多源 context 与 Platform 定向回归：`14 passed`；目标文件
  Ruff check/format PASS。

### 2026-07-29：Codex X2–X5 最小工程主路完成

- 新增 `rag/codex_v2.py`，以单次现有 `CodexHybridRetriever` 调用为候选 authority；
  不写 store、不加 schema、不调用旧 X-T1 runner。
- X2：按 thread/turn/content version 生成稳定 episode identity，并为每条证据标注
  goal/action/result/change/validation/reported outcome 等可观察角色。
- X3：根据 validation/failure/change/rationale/history/search 任务做确定性 temporal
  rerank；失败尝试、plan-only、claim-only、truncated 与 supporting subagent 以显式
  negative reasons 降权，不删除事实。
- X4：构建 `codex-timeline-context-v2`，只包含已检索的可观察事件、状态、locator、
  thread comparison 和 missing contract；reasoning 始终排除，secret/绝对路径被清洗。
- X5：Direct Codex 与 Platform 支持显式 `X-RAG-Codex-Engine: v2`；默认及回滚均为
  V1，非法值 422，release trace 固定 `HOLD_DEFAULT_V1`，不声明质量 qualification。
- Codex V2、既有 session API、Platform 和统一 context 相关回归：`13 passed`；
  目标文件 Ruff check/format PASS。

### 2026-07-29：Notebook/Document/Workspace 与 M6 context 分层完成

- `SearchScope.source_types` 与 `QueryRequest.include` 正式纳入 `notebook`；显式问题中的
  notebook/ipynb/cell 也成为 required source role，缺失时会诚实进入 missing evidence。
- Notebook、Document、Workspace 继续复用各自现有 typed store 和 Platform ACL 主路，
  不新增正式库 migration/backfill；统一 context 仍使用 portable locator、secret
  redaction、content-addressed block 和确定性预算。
- Query Evidence Pack 新增两层：
  - `retrieval-context-bundle-v1`：保留 Platform multisource context 与各来源的结构化
    source context；
  - `comprehension-context-v1`：只从最终 verified/supporting/counter evidence 与
    missing roles 派生，带稳定 citation/content digest，明确不包含 reasoning。
- `/v1/query` 支持显式透传 Code/Codex/Experiment engine header；非法 override 返回
  422，未传 header 时所有来源仍走原 V1 默认。
- Experiment、Codex、Notebook、Document、Workspace、Platform、Query/API 相关定向
  回归：`19 passed`；目标文件 Ruff check/format PASS。

### 2026-07-29：高级功能最终验收完成

- 安全全仓产品回归（排除会观察外部正式库状态的历史 C-B1～C-B5 probes）共收集
  `1346` 项，全部 `PASS`。
- 本轮 12 个目标 Python 文件 Ruff check/format check `PASS`，`git diff --check`
  `PASS`；所有实现均已被 Pytest 导入执行。
- 没有访问、hash、checkpoint、复制正式库，也没有查看或删除 WAL/SHM；没有创建新的
  production evaluation Run、commit、branch、worktree 或 push。
- 最终状态：
  - Code C0–C7 engineering `COMPLETE`，Release `HOLD_DEFAULT_V1`；
  - Codex X1 与当时的 X2–X5 薄兼容路径被记为 `COMPLETE`，X-B0
    `NON_QUALIFIED`，X-T1 `NO_RESULT`，默认 V1；
  - Experiment E0–E5 当时的薄工程路径被记为 `COMPLETE`，E-B0
    `PUBLISHED_VERIFIED_NON_QUALIFIED`，默认 V1；
  - Notebook/Document/Workspace 六来源统一检索、ACL、citation、context 与 Query
    comprehension 主路 `COMPLETE`；
  - production quality qualification、远程模型和正式库 migration 仍明确 `DEFERRED`。

### 2026-07-29：原始路线图仓库内工程最终闭环

此前“高级功能最终验收”仍把 Codex X2–X5、Experiment E1–E5 和 M5–M7 压缩为较薄的
兼容层，不能满足用户要求的原始设计口径。本阶段继续在同一会话内实现并核对完整合同：

- Codex X2–X5：
  - `sources/codex/episode_v2.py`、`observable_adapter_v2.py`、
    `units_v2.py`、`store_v2.py`；
  - `retrieval_v2.py`、`context_v2.py`、`governance_v2.py`、
    `pipeline_v2.py`；
  - 覆盖 goal-aware episode、versioned retrieval unit、isolated additive store、
    exact/sparse/local dense/temporal graph、rerank/calibration、timeline context、
    append/privacy/shadow/release/rollback；
  - Codex 定向集合 `191` 项通过。X-T1 仍
    `FAILED_BEFORE_PUBLISH / NO_RESULT / NOT_QUALIFIED`，没有制造 treatment 结果。
- Experiment E1–E5：
  - `sources/experiment/contracts_v2.py`、`store_v2.py`、`query_v2.py`、
    `analysis_v2.py`；
  - `semantic_v2.py`、`governance_v2.py`、`pipeline_v2.py`；
  - 覆盖 immutable snapshot/metric registry、typed dataset/config/environment、
    safe typed query/parameterized SQL、comparability/aggregation/reproduction、
    semantic surface/rerank/context、atomic sync/security/shadow/release；
  - Experiment 定向集合 `187` 项通过。E-B0 保持
    `PUBLISHED_VERIFIED_NON_QUALIFIED`。
- M5–M7：
  - `multisource_foundation_v2.py`：六源 capability/watermark、exact 60-case
    content-addressed Golden、planner、reviewed calibration 和 role fusion；
  - `evidence_graph_v2.py`：typed registry/edge、≤4 hop bounded beam、ACL/version/
    review/cycle/budget gate；
  - `answer_v2.py`：retrieval/comprehension 分层 Evidence Pack、claim-citation
    verifier 与 grounded refusal；
  - `performance_v2.py`：VectorIndex contract、exact generation baseline、embedding/
    六层 cache、P50/P95/P99 dashboard、可重算 ANN benchmark/admission；
  - `global_governance_v2.py`：六源 72-case security matrix、八阶段 release evaluator
    与固定 rollback rehearsal；
  - `multisource_pipeline_v2.py`、`multisource_evaluation_v2.py`：完整受治理联合主路、
    exact 60 membership evaluator 和 portable bundle；
  - M5–M7 定向集合 `29` 项通过。

最终独立验证使用的安全全仓集合排除了会主动观察外部正式库的历史 C-B1～C-B5 probes，
共收集 `1484` 项并全部通过。排除仅用于遵守
`EXTERNAL_MUTABLE_SERVICE_OWNED` 边界，不是隐藏产品失败。新增目标文件 Ruff
check/format check、`git diff --check` 和 `222` 个 Python 文件 in-memory compile
全部通过；唯一 warning 为既有 FastAPI/Starlette TestClient 弃用提示。

本阶段没有读取、hash、checkpoint、复制正式库，也没有查看或删除其 WAL/SHM；没有
创建新的 production Run、远程模型调用、branch/worktree、commit 或 push。最终口径是：

`REPOSITORY_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD /
PRODUCTION_RELEASE_EXTERNAL_BLOCKED`

### 2026-08-01：会话工作区同步与拖拽缺陷修复

- 用户实测指出两个当前产品缺陷：会话列表加载状态与真实同步结果错位；会话详情的横向
  面板/节点拖拽不稳定。该问题属于 L3 产品交付面的后续缺陷修复，不改变
  `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`。
- 后端根因是每个请求连接都重复执行持久化的 SQLite WAL mode 切换；会话页面同时轮询
  列表、同步状态、工作流和时间线时，会把普通读取变成 schema-lock 竞争。现改为只在
  isolated store 初始化时设置 WAL，普通 `connect()` 不再重复切换 journal mode。
- 由于 `SQLiteStore` 是 Codex current production authority 的组成部分，current authority
  合法刷新为 v4；历史 v1 authority、X-B0 Run 和 correction artifact 均保持冻结，未重签、
  未修改。final completeness Gate 中的 v3 是 2026-07-31 静止源码快照，不再作为此次
  hotfix 后的 current source digest。
- 前端所有会话查询使用 React Query 的 abort signal；路由切换时重置 turn/tab/layout 和
  临时拖拽状态；列表首次读取不再先显示“0 个会话”。横向面板使用 pointer capture，
  节点拖拽入口不再嵌套交互控件，键盘移动保留在外层节点按钮；面板禁用 smooth scroll，
  纵向触摸滚动和横向自定义拖拽不再争抢。
- 新增后端 WAL 生命周期回归和前端 pending-list、route-switch、pointer-capture 回归。
  验收结果：后端相关 `96` 项通过；会话前端 `10` 项通过；TypeScript、production build、
  Ruff/format/compile 通过。隔离服务在 `127.0.0.1:8000` 真实启动，自动同步 8-thread
  fixture 后展示 6 个可见会话；80 个并发 health/session/timeline 读取全部 200，最大
  观测延迟约 `0.348s`。浏览器真实完成两次会话切换、节点鼠标拖动和自动布局复位，
  console 无 warning/error。
- 正式库和 sidecars 未被读取、hash、checkpoint、复制、删除或声明不变；所有运行数据
  继续位于 system temp isolated root。

### 2026-08-01：后续产品交互阶段队列

1. `[x] UI-S1` 会话列表同步、路由竞态和会话面板拖拽稳定性；
2. `[ ] UI-G1` 关系图谱导航、来源/目标高亮、路径面包屑、返回上下文、节点详情与人工审阅；
3. `[ ] AG-1` 智能体管理控制面：注册、能力、权限、任务/会话、运行状态和审计；
4. `[ ] CP-1` Codex 插件产品化：安装/连接状态、能力发现、会话同步、错误与权限呈现；
5. `[ ] IQ-1` 智能查询：范围解析、自然语言意图、可解释计划、证据/图谱联动和 follow-up。

上述 UI/控制面项目是仓库工程 Gate 之后的产品体验与管理能力，不应被历史
`L0-L3 COMPLETE` 自动视为已实现。恢复时从 `UI-G1` 开始，仍采用“实现 → 定向回归 →
隔离启动 → 浏览器人工路径验收 → 更新本日志”的闭环。

### 2026-08-01：真实项目会话、代码仓库与图谱交互纠偏

此前 UI-S1 的 8-thread fixture / 6-session 页面验收只证明了小样本合同，不能证明当前
`rag` 项目的真实会话发现完整，也没有证明代码仓库在重启后的项目工作台仍然存在。用户
在真实页面指出“会话明显缺失、代码仓库消失、图谱详情难以审阅”后，本阶段重新打开该
交付面，并以真实 `~/.codex` 元数据和当前仓库做隔离运行验收；本节 supersede 小样本的
“已覆盖真实项目数据”推论，但不删除早期测试证据。

- 会话发现改为先完成全量候选遍历和项目绑定，再应用选择上限；项目匹配优先读取权威
  `session_meta` / `thread.started` 绑定，并只解码可能包含 cwd/workspace binding 的记录。
  本机真实扫描由一分钟以上降到约 `1.49s`，得到 `609` 个候选、`111` 个当前项目会话、
  `498` 个其他项目会话、`110` 个可索引会话和 `1` 个超大文件。最终原子发布为
  `110` 个会话，其中 `5` 个是 `metadata.source.subagent` 子任务、`105` 个主会话；页面
  默认显示全部，并支持主会话/子任务精确筛选。
- 会话发现状态新增 complete/partial、unreadable/symlink/traversal、project-match、limit、
  oversized 和 repository visibility 诊断。扫描不完整时同步 fail-closed，不再把截断结果
  当作“全部会话”；前端把“正在读取”“0 个会话”“明确过滤”“尚未索引”“同步缺口”分开
  表达。
- 真实运行进一步暴露 Python 3.13 SQLite WAL 连接在一个请求 close、另一个请求 open 时
  会卡在 reusable-fd mutex。仅把 journal mode 移到 initialize 仍不充分；现在 runtime 对
  同一个 isolated store 只串行化 connection open/close，查询与事务本身仍可并发。修复后
  在会话发布过程中并发执行 `24` 个 workflow/repository/graph 请求全部 `200`，平均
  `0.67s`、最慢 `1.91s`，没有再次锁死页面。
- 当前仓库通过真实摄取恢复为项目一级入口：`704` 文件、`8,021` symbols、`28,135` edges、
  `8,725` views、`13` commits、`500` diff hunks，`parse_errors=0`。总览和代码页显示仓库、
  branch/ref、dirty head、active generation、索引状态和文件树；代码页已真实打开 README。
- 图谱改为桌面三段式“画布 / 审阅路径与关系 / evidence-first 详情”，点击节点会显式更新
  当前焦点、审阅路径、来源→目标和上一焦点；详情显示 Identity、Type、Source、Scope、
  ACL、Citation、Version、层级、上下游和包含实体。普通图边不会伪装成可人工复核候选。
  窄屏使用节点详情抽屉并保留可见的返回路径，不再发生无痕跳转。
- 真实浏览器发现并修复了最后一个合同偏差：后端子任务标记是对象
  `metadata.source.subagent`，不是仅有 boolean `true`。修复后“子任务”筛选真实显示
  `5 / 110`，并为每项展示子任务标识。
- 最终切换到 `127.0.0.1:8000` 时又关闭了一项状态误判：只有 source 的
  `active_generation_id` 对应的 building generation 才能令页面显示“同步中”；历史中断留下的
  非活动 building generation 不再覆盖当前 published generation。修复后状态为
  `ready / indexed_sessions_available`、`active_workflow=false`，并新增定向回归覆盖该边界。
- 同一 system-temp 数据根已在最终 8000 服务完成桌面和 390px 窄屏浏览器复核：会话列表
  `110 / 110`、子任务筛选 `5 / 110`、仓库 `704 / 8,021`、图谱审阅路径和 evidence-first
  drawer 均可操作；窄屏 `documentWidth=375 <= innerWidth=390`，页面日志为空。

最终安全全仓选择为 `1861 passed, 2 deselected`；deselect 仍只有两条会解析正式路径的
节点，历史 C-B1～C-B5 正式库 observer 文件继续整文件排除。前端 `28 files / 144 tests`、
TypeScript、production build、最终状态修复相关 `32` 项测试、目标 Ruff check/format、
`374` 个 Python 文件内存编译、`git diff --check` 均通过。所有真实
数据继续位于 system-temp isolated root；正式库及其 sidecars 没有被读取、open、hash、
checkpoint、复制或删除。全局发布状态继续是 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`。

### 2026-08-01：产品交互续建 checkpoint（执行中）

- 当前单一目标仍覆盖 `UI-G1 / AG-1 / CP-1 / IQ-1`，没有把历史 L0–L3 Gate 当作这些
  产品能力已经完成。多会话只承担互斥写锁的子任务，最终集成、启动与结论仍由主会话统一
  验收；本文件作为上下文压缩后的过程 authority。
- `IQ-1` 后端已经新增纯解析、零检索的 `POST /v1/query/preview`，返回 scope、intent、
  two-wave sources、required roles、budget 和 generation policy；新增 project ACL 管理的
  `GET /v1/query/history`，只把 query digest、terminal state、source/evidence identity 和
  watermark 写入既有 audit event，不保存原始问题、答案正文、snippet、ACL、secret、绝对
  路径或 raw trace。
- `IQ-1` 前端已接入 repository/commit/as-of/intent 显式范围、执行前计划预览、持久审计
  历史与 evidence/citation → graph deep link；API/面板相关 `15` 项测试与 TypeScript 在当时
  静止前端上通过。最终完整前端验收仍等待 UI-G1 与 AG-1/CP-1 释放共享样式/路由写锁。
- `UI-G1` 正在关闭两项真实误审阅 P0：generic/synthetic edge 不得伪装为
  BindingCandidate；不存在的 relation deep link 不得静默选择队列第一项。随后补移动端、
  reduced-motion、evidence/scope/ACL inspector 与确认式人工复核。
- `AG-1/CP-1` 正在把现有 Codex execution alpha 收束为 project-scoped 只读 agent 面与真实
  connector availability/capability/error contract；审批、claim/report 必须同时满足对象归属
  和 project ACL，默认 sandbox 为 read-only，不能再用路径/CLI 存在性冒充 ready。
- 隔离服务仍运行在 `127.0.0.1:8000`；当前 checkpoint 尚未重建最终 bundle 或重启，因此
  不把正在编辑的功能冒充为 live verified。所有任务释放写锁后才执行一次完整 build、隔离
  重启、API/浏览器 desktop+mobile 路径与全量回归。

### 2026-08-01：产品交互续建最终 checkpoint

用户在本阶段明确恢复多会话开发，因此 2026-07-29 的“单会话”约束只保留为历史记录。
本轮采用三个互斥写锁任务分别复核前端路由交互、运行时/文档事实和隔离启动 Gate；主会话
负责合并、完整回归、真实浏览器路径与最终文档。子任务均已释放写锁，没有 branch、worktree、
commit 或 push。

队列最终状态：

1. `[x] UI-S1 COMPLETE / LIVE VERIFIED`：会话首次加载、同步状态、路由切换取消、节点与
   面板真实 pointer drag、键盘微调均已在隔离服务复现；切换 thread 后不残留旧目标或布局。
2. `[x] UI-G1 COMPLETE / LIVE VERIFIED`：图谱点击会更新 URL、selection、history、路径和
   evidence-first inspector；浏览器 back 能恢复上一个 focus。只有唯一、真实的
   `BindingCandidate` 可进入人工审阅；无效 relation 显式报错、无默认选中且不能提交。
3. `[x] AG-1 SAFE READ-ONLY PHASE COMPLETE`：新增项目级智能体面板、受治理 capability
   snapshot、同步状态和有界审计；stale/unknown/revoked/跨项目状态 fail-closed，未授权时不
   显示为可启动。注册、安装、token 签发/轮换 UI、批量审批等权限型写操作不在本阶段授权内。
4. `[x] CP-1 OBSERVABILITY/DISCOVERY PHASE COMPLETE`：Codex connector 真实区分 Core 可读、
   client 未授权、MCP token 缺失、同步有数据、capability 未验证等状态；不再用 CLI/路径存在性
   冒充 ready，不输出绝对路径、query、ACL、secret 或原始错误。插件安装/卸载仍需单独授权。
5. `[x] IQ-1 COMPLETE / LIVE VERIFIED`：显式 project/repository/commit/as-of/intent composer、
   零检索 plan preview、执行后 6 来源状态/证据/反证、graph deep link、本地 follow-up 和项目
   ACL 管理的持久审计历史均已接入。输入变化会立即使旧 plan 失效，迟到的异步响应也不能
   回填旧计划；mixed-source history 不会伪造单一 entity focus。

最终证据：

- 安全后端全仓集合：`1855 passed, 2 deselected`；两条 deselect 仅用于避开正式路径解析，
  历史 C-B1～C-B5 外部正式库 observer 文件按既有边界排除。
- 前端：`26 files / 133 tests`、TypeScript、production build 全部 PASS；当前 bundle 为
  `web/app/index-B6z7XwIc.js`。
- Codex bridge 独立对抗复核：既有 `19` 项加 `21` 项 authority/ACL/expiry/redaction/order/
  retention 攻击共 `40/40 PASS`。
- 目标后端 Ruff check/format、`git diff --check` 和 `354` 个 Python 文件 in-memory compile
  全部 PASS。
- 隔离服务使用 system-temp 数据根在 `127.0.0.1:8000` 启动；`/health`、query preview/history、
  agent status/sync/list 均返回当前合同。浏览器真实验证会话 load/switch/drag、图谱 focus/back/
  inspector、非法 relation、Agents/Codex fail-closed、query preview/invalidation/retrieval/history；
  当前 bundle 加载后 console 无新增应用 warning/error，页面无横向 document overflow。

本 checkpoint 表示上述安全产品阶段已经工程完成并在隔离运行态验证，不等于生产发布。
系统仍为 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`；正式库 migration/backfill、远程质量 replay、
shadow/canary、默认 V2，以及智能体/插件高权限生命周期操作继续是外部或需单独授权的工作。

完成的是原始路线图中能在仓库和隔离环境安全实现的工程，不是生产发布。剩余工作只有：
正式库 owner 执行 migration/backfill、远程 neural/ANN/LLM benchmark、真实单源与
60-case production replay、shadow/canary 和默认 V2 发布授权。
