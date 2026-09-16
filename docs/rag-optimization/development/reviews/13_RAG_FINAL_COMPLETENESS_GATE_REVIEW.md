# 13 — RAG Final Completeness Gate Review

审阅日期：2026-07-31（Asia/Shanghai）  
审阅对象：共享 dirty main 的最终静止工作树（不是某个 Git commit）  
审阅范围：RAG repository L0–L3 engineering completeness  
审阅角色：最终独立完整性 Gate

## 1. Canonical verdict

`RAG REPOSITORY L0-L3 ENGINEERING COMPLETENESS GATE PASS`

`P0 findings: 0`

`P1 findings: 0`

`P2 findings: 0`

`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

`L4/L5: EXTERNAL_DEFERRED`

## 9. Authority versioning 后的最终 superseding revalidation

本节是对 2026-07-31 当时共享 dirty main **精确静止源码**的独立复核，明确 supersede 第 1、5、7 节中作为更早快照记录的旧 authority digest、`1823` 项全量数字及其对应最终性；前述设计对照、里程碑分析、observer 排除边界和历史修复轨迹继续保留为审计记录。**本节是本文 L0–L3 engineering authority 的最终结论；其 runtime/frontend build currentness 后续由第 9.6 节与 Review 14 覆盖。** Gate 执行当时没有同步 `05_ORIGINAL_ROADMAP_IMPLEMENTATION_MATRIX.md` 或 `06_RAG_COMPLETENESS_AND_INTERACTION_OPTIMIZATION_PLAN.md`；后续已完成同步。

### 9.1 当前 authority 真值

fresh interpreter 对 immutable repository code identity 重新计算并验证得到：

| Authority | 当前真值 | 历史冻结边界 | 独立结果 |
|---|---|---|---|
| Codex X-B0 | `codex-x-b0-production-authority-v3`；`sha256:4e63852f27170dccb72bd6d082ec1bf39116c13bf8274059842f60d4826739e9` | historical v1 保持 `sha256:a3c75915caa4b228eca6b7d187b1fe5d1c3cf0135884d8130f709ad832c16c75`；current rows 与 frozen v1 tuple 分离 | current aggregate、historical artifact、alternate globals/builtins/helpers、pre-import copied builtins、API cold-import SQLite/mkdir bombs 全部通过。[baseline_v1.py](../../../../src/evidence_rag/rag/sources/codex/baseline_v1.py#L82-L89) [test_codex_baseline_v1.py](../../../../tests/test_codex_baseline_v1.py#L730-L769) [test_codex_baseline_v1.py](../../../../tests/test_codex_baseline_v1.py#L875-L933) |
| Experiment Foundation | `experiment-current-production-authority-v4`；aggregate `sha256:4fe60c499a0f3c4550ca1140bb9fa719bc526a78e559a951be9258883bb2a22d`；parity `sha256:641c191619852db9c394331ce32eba8e3d5daf9824aa27b1993026f7d4736ac2` | released Foundation v1 仍为 aggregate `sha256:0c961eb90c9a30795debc25d4d9c04eb6f38d4214a2ae3d71f03f54f812f405d`、parity `sha256:df0d6a7f745bfda76bb4e68ce52063c0010ef474ee8afe584d4f20e2fd92ceb9` | current verifier 正向通过；runtime replacement/side-effect bombs 负向通过；模块来源摘要不绑定 checkout 根目录。[fixture_v1.py](../../../../src/evidence_rag/rag/sources/experiment/fixture_v1.py#L76-L92) [test_experiment_foundation.py](../../../../tests/test_experiment_foundation.py#L221-L276) |
| Experiment E-B0 | `experiment-e-b0-current-production-authority-v4`；`sha256:2c82f1d2b1ec4948715371f66a14aa0b8685e2b80bab07409a837781e163d891` | historical E-B0 v2 保持 `sha256:689b77964fb99fc72f7f93631eba0063dab8af4ab8e0d121364ee0df7fd5f845`，historical rows 不被 current refresh 重签 | frozen historical、current static identity、fresh verifier 无 I/O、pre-import wrapper 拒绝全部通过。[baseline_v1.py](../../../../src/evidence_rag/rag/sources/experiment/baseline_v1.py#L124-L135) [baseline_v1.py](../../../../src/evidence_rag/rag/sources/experiment/baseline_v1.py#L710-L717) [test_experiment_baseline_v1.py](../../../../tests/test_experiment_baseline_v1.py#L398-L495) |
| 六来源 production-code registry | `rag-production-authority-v3`；component-set `sha256:ee64f94d763948d6f262d0de6be109f927dcf9dfcce829d4f4ffeb6cd0d0f28d`；content `sha256:43f3fcfedc100fc54c49d9c7f47941b553e857155ca387dfdd16c2183673fba6` | reviewed source rows静态固定，不从当前对象自学习 | `code/codex/experiment/notebook/document/workspace` 顺序固定，六项均 `CURRENT/current`，`all_current=True`，`verify_current_production_authority_v2() == report`；Code/Notebook/Document v3 pins 和 Workspace descriptor/helper/class binding 攻击均通过。[release_admission_v2.py](../../../../src/evidence_rag/rag/release_admission_v2.py#L205-L286) [release_admission_v2.py](../../../../src/evidence_rag/rag/release_admission_v2.py#L295-L314) [release_admission_v2.py](../../../../src/evidence_rag/rag/release_admission_v2.py#L1070-L1150) [test_release_admission_v2.py](../../../../tests/test_release_admission_v2.py#L162-L210) |

这里的 **production-code authority registry** 只证明仓库内六来源实现与 reviewed code identity 当前一致，不等于 release promotion authority。独立 fresh control-plane 观察显示 canonical **reviewed release-evidence registry** 仍为 `sources=0`、`gates=0`；六来源 runtime stage 集合只有 `NO_RELEASE`，quality state 集合只有 `QUALITY_HOLD`，默认引擎为 `v1`。caller flag、环境变量或合成 envelope 不能跨越这两个 registry 的边界。[release_admission_v2.py](../../../../src/evidence_rag/rag/release_admission_v2.py#L1274-L1292) [release_control_plane_v2.py](../../../../src/evidence_rag/rag/release_control_plane_v2.py#L897-L918)

### 9.2 逐里程碑再确认

| 里程碑 | 当前结论 | 本次 versioning 后复核 |
|---|---|---|
| M0 基线与数据准备 | **PROVEN** | Codex/Experiment historical artifacts 冻结，current authority 另起版本；没有 runtime 学习或重签历史证据。 |
| M1 公共协议与双轨 | **PROVEN** | 默认 V1、显式 V2 authority preflight、exactly-once legacy fallback、冷公开 status 与并发 lazy Runtime 合同通过。 |
| M2 Code Graph RAG | **PROVEN** | 本轮 registry 的 Code Platform v3 pin 为 CURRENT；第 3–4 节既有 edge identity/SCIP 专项结论未被 authority refresh 推翻。 |
| M3 Codex Temporal RAG | **PROVEN** | Codex current v3 与 historical v1 分离；Direct/Platform authority 成对路径、冷 API I/O bombs 通过。 |
| M4-A Experiment | **PROVEN** | Foundation current v3 与 E-B0 current v4 同步 current contract；historical Foundation/E-B0 保持冻结。 |
| M4-B Notebook | **PROVEN** | 八类 typed task、Direct/Platform registry bridge、Notebook v3 registry pin 与无-authority fail-closed 通过。 |
| M4-C Document | **PROVEN** | Document v3 registry pin CURRENT；真实 Document Platform/Product 正向 V2 与无 authority fallback 通过。 |
| M4-D Workspace | **PROVEN** | Workspace v3 registry pin CURRENT；descriptor walker 对 classmethod/`types.GenericAlias`、wrapper/subclass/unsupported binding fail-closed。 |
| M5 多源联合 | **PROVEN** | typed Experiment/Notebook contract、reviewed calibration、source registry、全来源 unavailable exactly-once fallback 通过。 |
| M6 上下文与生成 | **PROVEN** | typed task 不能由 generic result 贴标签，retrieval-only/单一 answer authority 的既有证明未被 current authority 变更破坏。 |
| M7 性能、安全与发布（仓库工程） | **PROVEN** | 七开关 pre-materialization 独立降级、status 脱敏、canonical empty release registry、default V1/NO_RELEASE 通过。 |
| M8 简历/面试验收 | **PARTIAL** | 仅缺 L4/L5 外部 production evidence；不把未执行的 migration、remote model/ANN、shadow/canary/default V2 写成已完成结果。 |

`MISSING` 里程碑：**0（L0–L3 scope）**。

### 9.3 本次独立实际执行

以下为本节对 authority-versioned 当前树实际执行的结果；不把主控口头测试数冒充为本 Gate 独立执行：

| 验证 | 本次独立结果 |
|---|---|
| Codex/Experiment current+historical authority、六来源 registry、descriptor/builtin/helper 攻击、API cold import/status I/O bombs | **26/26 PASS** |
| typed Experiment/Notebook、Direct/API 正反 authority、七开关、release control、default-V1 | **62/62 PASS** |
| 五来源 runtime registry、六来源 Product/API 可达、Document/Workspace、Code/Codex Direct/Platform/fallback | **27/27 PASS** |
| Backend 关键集（上三行，互不重复） | **115/115 PASS** |
| Frontend isolated Vitest | **22 files / 95 tests PASS** |
| Frontend isolated TypeScript typecheck | **PASS** |
| Frontend isolated production build | **PASS**；`buildSha256 = ce9d7c935a7d373033382c0fff967bb69282fac294e610b25c3111d85c34b6e2` |
| `ruff check src tests` | **PASS** |
| 当前 dirty `src/tests` Python 集的 `ruff check` + `ruff format --check` | **298/298 files PASS** |
| Python in-memory `compile()` | **369 files PASS** |

主控提供的补充全量证据为四分片 safe backend **1833/1833 PASS**、frontend **95/95 + typecheck + build PASS**、299 个 changed Python 静态检查 PASS；本 Gate 将其作为交叉证据，但最终判断主要依赖上表的独立关键路径复放和第 1–8 节已完成的完整设计对照。主控报告的单进程 Python 3.13 线程退出钩子卡住没有断言失败，且后段/卡点相关组另行通过；本 Gate 没有把该未正常退出的单进程尝试计为 PASS。

一次扩展到所有历史 `src/tests` 的 `ruff format --check` 报出 11 个 formatter 差异；对这 11 个精确路径的只读 Git 状态检查为空，确认它们不是当前 dirty 变更。它们不影响运行语义、authority 或本轮修改完整性，故不构成 P2；本 Gate 没有格式化这些文件。首次前端隔离命令因包含临时目录删除钩子而在进程启动前被安全策略拒绝，未执行测试且未改工作树；随后保留 `/tmp` 隔离目录的正式运行即为上表 95/95/typecheck/build 结果。

### 9.4 正式库安全边界与开放 findings

第 6 节列出的 C-B1～B5 五个整文件及两个精确 node 继续排除；本节没有执行它们，也没有新增任何 formal observer。正式库 `/Users/example/project/rag/var/evidence-rag.sqlite3` 与 WAL/SHM 始终作为 `EXTERNAL_MUTABLE_SERVICE_OWNED`：本次没有 open、hash、checkpoint、copy、delete、sidecar stat 或“不变”声明。所有动态数据库/文件系统路径均来自 pytest `tmp_path`、`/tmp` 前端副本、in-memory compile 或 immutable repository artifacts。

`P0 findings: 0`

`P1 findings: 0`

`P2 findings: 0`

### 9.5 Superseding canonical verdict

`RAG REPOSITORY L0-L3 ENGINEERING COMPLETENESS GATE PASS`

`P0 findings: 0`

`P1 findings: 0`

`P2 findings: 0`

`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

`L4/L5: EXTERNAL_DEFERRED`

该 PASS 明确基于 Codex current v3、Experiment Foundation current v3、Experiment E-B0 current v4、canonical production-code registry v3 及六来源 `all_current=True` 后的当前静止树；它不授权正式 migration、production replay、remote model/ANN、shadow/canary 或默认 V2。

本结论只证明当前仓库的 L0–L3 工程合同、接线、回退、安全与可验证性闭环。它不代表完成正式库 migration/backfill、远程模型或 ANN 质量证明、真实硬件 SLO、production replay、shadow/canary、真实回滚演练或默认 V2 发布授权。

当前 canonical reviewed release registry 保持为空；控制面因此只能给出 `QUALITY_HOLD`、默认 `v1` 和六来源 `NO_RELEASE`。这是正确的发布边界，不是把仓库内 wiring 缺口外推的理由。[release_control_plane_v2.py](../../../../src/evidence_rag/rag/release_control_plane_v2.py#L1-L10) [release_control_plane_v2.py](../../../../src/evidence_rag/rag/release_control_plane_v2.py#L897-L918)

### 9.6 2026-08-01 current-state / live-startup evidence addendum

第 1–9.5 节继续作为 L0–L3 设计完整性、authority versioning 与正式库 observer 边界的历史 Gate 证据。第 9.3 节的 frontend `22 files / 95 tests` 与 `buildSha256=ce9d7c935a7d373033382c0fff967bb69282fac294e610b25c3111d85c34b6e2` 是当时静止快照，不删除、不改写，也不再表示当前 build。

后续 [Review 14](14_RAG_LIVE_STARTUP_GATE_REVIEW.md) supersede 的仅是 runtime/frontend build 证据的 currentness。2026-08-01 主控与独立复核得到：safe backend selection `1823/1823 PASS`；backend 关键、Golden/evaluator 路径 `710/710 PASS`；frontend `22 files / 97 tests PASS` 且 typecheck PASS；`ruff check src tests` PASS；Python in-memory compile `369 files PASS`。隔离 production build 与仓库 `web/` 的 build contract 一致，当前 `buildSha256=21b9f585a893f4920ad1f7c29baa30d1f0d679e145e73527f60cc1d57a62a34e`。Review 14 另外记录了隔离临时根上的六来源 HTTP、默认 V1、显式 V2 到 V1 authoritative fallback、hashed production bundle 与有序关停实证。`05_ORIGINAL_ROADMAP_IMPLEMENTATION_MATRIX.md` 和 `06_RAG_COMPLETENESS_AND_INTERACTION_OPTIMIZATION_PLAN.md` 已同步该 current evidence。

本 addendum 关闭的是证据时态歧义，不改变本文 L0–L3 verdict，也不构成 production replay、quality qualification、shadow/canary、production rollback drill 或默认 V2 授权。当前状态仍为 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`，`L4/L5: EXTERNAL_DEFERRED`。

## 2. 审阅基线与判定方法

逐项对照了以下原始设计与路线图，而没有把当前进度文档的 `COMPLETE/PROVEN` 声明当成证据：

- [Code Source RAG](../../sources/01_CODE_SOURCE_RAG.md)、[Codex Source RAG](../../sources/02_CODEX_SOURCE_RAG.md)、[Experiment Source RAG](../../sources/03_EXPERIMENT_SOURCE_RAG.md)、[Notebook Source RAG](../../sources/04_NOTEBOOK_SOURCE_RAG.md)、[Document Source RAG](../../sources/05_DOCUMENT_SOURCE_RAG.md)、[Workspace Source RAG](../../sources/06_WORKSPACE_SOURCE_RAG.md)；
- [Multi-source Fusion RAG Design](../../02_MULTI_SOURCE_FUSION_RAG_DESIGN.md)；
- [Global RAG Hardening](../../03_GLOBAL_RAG_HARDENING.md)；
- [Implementation Roadmap](../../04_IMPLEMENTATION_ROADMAP.md)；
- 待验证声明：[Original Roadmap Implementation Matrix](../05_ORIGINAL_ROADMAP_IMPLEMENTATION_MATRIX.md) 与 [Completeness and Interaction Plan](../06_RAG_COMPLETENESS_AND_INTERACTION_OPTIMIZATION_PLAN.md)。

判定原则：P0/P1 阻塞 L0–L3 PASS；P2 记录但不阻塞；L4/L5 外部证据保持 deferred，严禁以测试 authority、自签 envelope 或合成 production observation 晋级。

## 3. 逐里程碑结论

| 里程碑 | L0–L3 状态 | 独立证据与边界 |
|---|---|---|
| M0 基线与数据准备 | **PROVEN** | 六来源有冻结合同、Golden/evaluation 资产与可重复的 isolated 测试；最终安全选择集包含来源基线、Golden、evaluation、security 与 C-B6。正式服务库 observer 被明确排除，不以“库未变”作为证据。 |
| M1 公共协议与双轨 | **PROVEN** | Runtime 同时拥有 V1、来源 V2 registry、global V2、performance 与统一 QueryService；默认/显式 V1 直接 V1，显式 V2 受 authority、开关和 exactly-once fallback 约束。[runtime.py](../../../../src/evidence_rag/runtime.py#L88-L103) [multisource_runtime_v2.py](../../../../src/evidence_rag/rag/multisource_runtime_v2.py#L1821-L1833) |
| M2 Code Graph RAG | **PROVEN** | AST/SCIP/semantic/dense/history/diff/test/graph 路径均在最终全量；stored edge ID 从邻接查询进入 traversal path，逐 hop 保留并按 scope/generation/ACL/review/type/locator 校验。[graph_retrieval_v2.py](../../../../src/evidence_rag/rag/sources/code/graph_retrieval_v2.py#L148-L166) [graph_retrieval_v2.py](../../../../src/evidence_rag/rag/sources/code/graph_retrieval_v2.py#L438-L466) |
| M3 Codex Temporal RAG | **PROVEN** | Temporal store、episode/fact/state、FOLLOWS/VALIDATES/SUPERSEDES、type/status/time/scope 与 governance 均在最终全量；Direct/Platform/global 统一受 registry 管控。 |
| M4-A Experiment | **PROVEN** | typed aggregate/compare/reproduce、numeric/unit/comparability、Direct/API registry bridge 与 current authority verifier 均通过；历史 released authority 与 current authority 分离。[baseline_v1.py](../../../../src/evidence_rag/rag/sources/experiment/baseline_v1.py#L124-L135) |
| M4-B Notebook | **PROVEN** | 八任务闭集显式贯穿 Direct/Platform/multisource/source retriever；output/error/reproduction/code/lineage/parameter 不再降成 search + 文本 marker。[models.py](../../../../src/evidence_rag/platform/models.py#L176-L203) [service.py](../../../../src/evidence_rag/platform/service.py#L1086-L1103) |
| M4-C Document | **PROVEN** | layout block、bbox、parser provenance、OCR 状态/文本绑定、Table/Figure/Formula/Claim 与 parent expansion 进入运行时和测试。[adapter_v2.py](../../../../src/evidence_rag/rag/sources/document/adapter_v2.py#L67-L83) [adapter_v2.py](../../../../src/evidence_rag/rag/sources/document/adapter_v2.py#L130-L180) |
| M4-D Workspace | **PROVEN** | typed project/work/decision/requirement、日期范围、as-of、snapshot/state transition、dependency/blocker/acceptance、coverage 与 read/mutation boundary 在最终全量。 |
| M5 多源联合 | **PROVEN** | 真实 fact/entity type、capability/plan hash、reviewed calibration、deterministic rerank、role-aware fusion、typed graph/counter/corrective retrieval 均在同一运行链；缺 calibration/type/role fail-closed。[multisource_foundation_v2.py](../../../../src/evidence_rag/rag/multisource_foundation_v2.py#L1343-L1405) [evidence_graph_v2.py](../../../../src/evidence_rag/rag/evidence_graph_v2.py#L433-L459) |
| M6 上下文与生成 | **PROVEN** | retrieval EvidencePack 只能声明 `retrieval_only`，global runtime 输出 answer handoff 而不输出最终 answer；`UnifiedQueryService` 是唯一最终回答权威，避免 double answer。[answer_v2.py](../../../../src/evidence_rag/rag/answer_v2.py#L145-L165) [multisource_runtime_v2.py](../../../../src/evidence_rag/rag/multisource_runtime_v2.py#L2124-L2137) [query/service.py](../../../../src/evidence_rag/query/service.py#L571-L580) |
| M7 性能、安全与发布（仓库工程） | **PROVEN** | exact in-memory index、embedding/scoped cache 真 query read/write、有界脱敏 dashboard、七开关 pre-pipeline 回退、canonical release control plane、status/API/CLI 均在最终全量。[performance_runtime_v2.py](../../../../src/evidence_rag/rag/performance_runtime_v2.py#L70-L80) [performance_runtime_v2.py](../../../../src/evidence_rag/rag/performance_runtime_v2.py#L137-L175) [global_switches_v2.py](../../../../src/evidence_rag/rag/global_switches_v2.py#L7-L55) |
| M8 简历/面试验收 | **PARTIAL** | 仓库内设计、实现、测试和 Gate 证据可追溯；真实 production 指标、shadow/canary、默认 V2 收益数字仍属 L4/L5，必须继续不写成项目已取得的生产结果。此项不阻塞 L0–L3 PASS。 |

`MISSING` 里程碑：**0（L0–L3 scope）**。

## 4. 八项独立对抗结论

### 4.1 六来源 Runtime/API、authority 与默认 V1

**PROVEN。** `ProductionSourceRuntimeRegistryV2` 安装 authority 时重跑来源 evaluator；authority 变化会使旧 LKG 无效；facade 仅按需 materialize，并公开有界的 materialized/LKG 状态。[production_sources_v2.py](../../../../src/evidence_rag/rag/production_sources_v2.py#L340-L371) [production_sources_v2.py](../../../../src/evidence_rag/rag/production_sources_v2.py#L474-L503) [production_sources_v2.py](../../../../src/evidence_rag/rag/production_sources_v2.py#L531-L575)

Platform source routing 明确规定 caller boolean 只能避免重复 legacy 工作，不能授权 V2；无 registry/authority 时 Direct 路径只返回 V1 或 governed unavailable。[service.py](../../../../src/evidence_rag/platform/service.py#L736-L769) [service.py](../../../../src/evidence_rag/platform/service.py#L771-L825)

Experiment/Notebook Direct API 通过 Platform 的同一 `_retrieve_source` registry bridge；有 verifier 认可的 isolated authority 时 Direct 与 Platform 同 authority digest 并 served V2，无 authority 时 exactly-once V1 且零 V2 materialization。[api.py](../../../../src/evidence_rag/api.py#L605-L641) [service.py](../../../../src/evidence_rag/platform/service.py#L141-L180) [service.py](../../../../src/evidence_rag/platform/service.py#L182-L245)

### 4.2 Code graph edge identity

**PROVEN。** Graph edge 保存真实 stored `edge_id`、type、direction、derivation/review/fact status 与 locator；DB 邻接查询投影真实 `edges.id`，不以 node/locator 临时伪造。[graph_retrieval_v2.py](../../../../src/evidence_rag/rag/sources/code/graph_retrieval_v2.py#L148-L175) [graph_retrieval_v2.py](../../../../src/evidence_rag/rag/sources/code/graph_retrieval_v2.py#L749-L765)

Traversal 要求 `edge_ids` 与 hop 顺序逐项相等、禁止路径内重复；相同 edge ID 若出现不同完整 identity 会被拒绝。[graph_retrieval_v2.py](../../../../src/evidence_rag/rag/sources/code/graph_retrieval_v2.py#L438-L466) [graph_retrieval_v2.py](../../../../src/evidence_rag/rag/sources/code/graph_retrieval_v2.py#L929-L989)

API projection 只在 governed graph candidate 中读取并验证 stored edges，按 ID 去重；无 graph、关闭 include、默认 V1 或 fallback 均输出 `edges: []`。[platform_v2.py](../../../../src/evidence_rag/rag/sources/code/platform_v2.py#L490-L521) [platform_v2.py](../../../../src/evidence_rag/rag/sources/code/platform_v2.py#L804-L811)

### 4.3 Direct/API 不绕 registry；typed task 不绕 contract

**PROVEN。** Codex、Experiment、Notebook Direct API 均进入受控 source runtime；Document/Workspace 的 Platform V2 路径也通过 registry operation。[service.py](../../../../src/evidence_rag/platform/service.py#L1016-L1084) [service.py](../../../../src/evidence_rag/platform/service.py#L1086-L1103) [service.py](../../../../src/evidence_rag/platform/service.py#L1123-L1159)

Notebook task 是冻结闭集；Platform 在来源调用前重新验证 spec，source runtime 在 project/store I/O 前解析 task 与 compare shape，真实 task 直接作为 retriever `task_override`。[models.py](../../../../src/evidence_rag/platform/models.py#L176-L203) [service.py](../../../../src/evidence_rag/platform/service.py#L1086-L1103) [runtime_v2.py](../../../../src/evidence_rag/rag/sources/notebook/runtime_v2.py#L156-L178) [runtime_v2.py](../../../../src/evidence_rag/rag/sources/notebook/runtime_v2.py#L287-L293)

独立动态攻击的最终结果：逻辑 `output` 下游 `task=output`，query 不含 `governed_notebook_task`；逻辑 `output` 配默认 `search` spec 时 source 调用数为 0，返回 `typed_spec_task_mismatch`。正向/篡改测试同时断真实下游 task 和无 marker，而不是只看 trace 标签。[test_multisource_typed_execution_v2.py](../../../../tests/test_multisource_typed_execution_v2.py#L358-L377) [test_multisource_typed_execution_v2.py](../../../../tests/test_multisource_typed_execution_v2.py#L459-L479)

### 4.4 M5 true types、calibration、graph/counter/corrective；M6 单一回答权威

**PROVEN。** 候选和 EvidenceFact 带 `fact_type`/`entity_type`，calibration/rerank 对 source/task/entity slice 与版本进行严格匹配；Evidence Graph 同时绑定 source/target fact/entity type，counter/corrective 最多两轮。[multisource_foundation_v2.py](../../../../src/evidence_rag/rag/multisource_foundation_v2.py#L562-L601) [multisource_foundation_v2.py](../../../../src/evidence_rag/rag/multisource_foundation_v2.py#L1343-L1405) [multisource_pipeline_v2.py](../../../../src/evidence_rag/rag/multisource_pipeline_v2.py#L583-L631)

M6 retrieval stage 被模型验证器禁止声称最终回答权威；QueryService 在最终响应中清空 upstream rendered answer 并声明自身为 final authority。[answer_v2.py](../../../../src/evidence_rag/rag/answer_v2.py#L145-L165) [query/service.py](../../../../src/evidence_rag/query/service.py#L571-L580)

### 4.5 M7 index/cache、七开关、release/LKG、fallback 与 trace

**PROVEN。** `RuntimePerformanceV2` 在 query path 实际读写 scoped cache、content-addressed embedding cache 与 exact index，并只暴露 allowlisted、聚合后的 operational snapshot。[performance_runtime_v2.py](../../../../src/evidence_rag/rag/performance_runtime_v2.py#L70-L80) [performance_runtime_v2.py](../../../../src/evidence_rag/rag/performance_runtime_v2.py#L98-L124) [performance_runtime_v2.py](../../../../src/evidence_rag/rag/performance_runtime_v2.py#L137-L175) [performance_runtime_v2.py](../../../../src/evidence_rag/rag/performance_runtime_v2.py#L347-L373)

七个开关均为 strict frozen bool；任一 false 产生精确 `component_disabled:<name>`，Runtime 和 pipeline 都在 plan/source/fusion/context/answer/perf/materialization 前回退，disabled snapshot 报 `v1_fallback`。[global_switches_v2.py](../../../../src/evidence_rag/rag/global_switches_v2.py#L7-L55) [multisource_pipeline_v2.py](../../../../src/evidence_rag/rag/multisource_pipeline_v2.py#L406-L421) [multisource_runtime_v2.py](../../../../src/evidence_rag/rag/multisource_runtime_v2.py#L1821-L1833)

独立参数化测试逐个关闭七开关，给 prepare/run/plan/pipeline/source/performance/materialization 安装 forbidden spy，断言全部 0、legacy 恰好 1、零 materialization/性能 span，并覆盖 pipeline 直调防御。[test_global_switches_v2.py](../../../../tests/test_global_switches_v2.py#L58-L127) [test_global_switches_v2.py](../../../../tests/test_global_switches_v2.py#L128-L200) [test_multisource_pipeline_v2.py](../../../../tests/test_multisource_pipeline_v2.py#L230-L306)

### 4.6 `/v1/rag/status` 与 CLI

**PROVEN。** 公共 `app` 使用 `defer_runtime=True`；middleware 对 status 特判，首个非 status 请求在进程锁内构造一次 Runtime；冷 status 直接从 canonical release truth 返回，不调用 Runtime/SQLite/mkdir/V2。[api.py](../../../../src/evidence_rag/api.py#L189-L215) [api.py](../../../../src/evidence_rag/api.py#L238-L250) [api.py](../../../../src/evidence_rag/api.py#L294-L301) [api.py](../../../../src/evidence_rag/api.py#L793)

Runtime status 只把有界内存 observation 合并到 canonical release truth；observer 失败时 status 仍可用且不泄漏异常对象。[runtime.py](../../../../src/evidence_rag/runtime.py#L109-L124)

CLI `status` 和 `package verify` 在 `create_runtime()` 前提前返回，属于 read-only/verify-only 路径。[cli.py](../../../../src/evidence_rag/cli.py#L61-L72) [cli.py](../../../../src/evidence_rag/cli.py#L92-L115)

status/UI 合同固定六来源、七开关，以及 index/cache/dashboard/latency/calibration 的 bounded tri-state；未知或非法值按 `UNKNOWN` fail-closed。[ragStatus.ts](../../../../frontend/src/features/ops/ragStatus.ts#L50-L84) [ragStatus.ts](../../../../frontend/src/features/ops/ragStatus.ts#L170-L188)

### 4.7 前端可信查询、ops、构建

**PROVEN。** 查询必须显式选择有效 project 和至少一个 source；不会静默选第一个项目，实际请求显式发送 `project_id` 和 `sources`。[TrustedQueryPanel.tsx](../../../../frontend/src/features/search/TrustedQueryPanel.tsx#L673-L697) [TrustedQueryPanel.tsx](../../../../frontend/src/features/search/TrustedQueryPanel.tsx#L726-L747) [TrustedQueryPanel.tsx](../../../../frontend/src/features/search/TrustedQueryPanel.tsx#L798-L848) [TrustedQueryPanel.tsx](../../../../frontend/src/features/search/TrustedQueryPanel.tsx#L933-L949)

请求使用 AbortController、serial 防 stale response、有界 history、feedback/correction chain、citation/trace/fallback 展示和 route error boundary。[TrustedQueryPanel.tsx](../../../../frontend/src/features/search/TrustedQueryPanel.tsx#L622-L670) [TrustedQueryPanel.tsx](../../../../frontend/src/features/search/TrustedQueryPanel.tsx#L699-L770) [TrustedQueryPanel.tsx](../../../../frontend/src/features/search/TrustedQueryPanel.tsx#L978-L1069) [App.tsx](../../../../frontend/src/app/App.tsx#L79-L165)

六来源展示是穷尽映射；Notebook 有独立 label/icon；unknown 保留经安全过滤的原始来源并显示“来源未识别”，不会伪装 Workspace 或展示伪 verified 状态。[SearchPage.tsx](../../../../frontend/src/features/search/SearchPage.tsx#L29-L57) [SearchPage.tsx](../../../../frontend/src/features/search/SearchPage.tsx#L230-L260)

三个 isolated production build 的 contract 逐字节一致：

- `sourceSha256 = ae747867ab073ea18bf76a54b35d31edf2d21886af9af0c778c3cd8e059ee374`
- `artifactsSha256 = e9c94f39a66fdc26e42f67fcf2bf05227539e74dc2c3068d91786b4d8e14dab5`
- `buildSha256 = ce9d7c935a7d373033382c0fff967bb69282fac294e610b25c3111d85c34b6e2`
- contract file SHA-256：`d382380100b06588f8f467ea786fcbd605c2011a61f6b4de602999952bfe9f17`

### 4.8 static/import/collection、安全与 V1 compatibility

**PROVEN。** 最终静止源码安全选择集 1823/1823 通过；Ruff 全仓通过；369 个 Python 文件用内存 `compile()` 通过；前端 95/95、typecheck、production build 通过。默认 V1、显式 V1、无 authority fallback、安全/ACL/secret/tamper 与 V1 schema compatibility 均包含在最终选择集。

无-authority 测试没有通过删除正向能力或降低断言来变绿：Experiment 与 Notebook 均成对保留“无 authority → exactly-once V1/零 materialization”以及“真实 ready source + evaluator authority → Direct/Platform 同 V2 authority digest/零 V1”。[test_experiment_v2.py](../../../../tests/test_experiment_v2.py#L181-L280) [test_experiment_v2.py](../../../../tests/test_experiment_v2.py#L307-L442) [test_notebook_v2.py](../../../../tests/test_notebook_v2.py#L117-L172) [test_notebook_v2.py](../../../../tests/test_notebook_v2.py#L185-L259)

## 5. Findings

### P0 findings: 0

未发现阻断数据安全、默认引擎或 canonical authority 的 P0。

### P1 findings: 0

本轮曾独立复现并在最终快照关闭的 P1，不再计入 open findings：

| 已关闭风险 | 原独立复现 | 最终证据 |
|---|---|---|
| Notebook typed task 被 `search` + query marker 伪装 | `logical_task=output` 时曾观察到 `downstream task=search`、marker=true、候选却标 output | 最终 `downstream task=output`、marker=false；search spec mismatch 在 source 前 0 调用 fail-closed；typed tests 与 1823 全量通过。 |
| planner/fusion/context_packer 开关只是状态标签 | 固定夹具中三个 disabled 与 all-on 的 candidates/context/serialized bytes 完全相同 | 七开关逐个 preflight，forbidden spies 全 0、legacy=1、pipeline 直调也 fail-closed。 |
| Notebook/unknown 前端错误展示 | Notebook 曾回落 Workspace，unknown 曾继承可信样式 | 六来源穷尽映射、Notebook 独立展示、unknown 保留原值且隐藏 status；前端 95/95 + typecheck + 三次确定性 build。 |
| Experiment current authority 随 Platform 合同变化陈旧 | 最终全量第一次运行有 2 项 `platform-search-model` digest mismatch | 只新增 current v3 静态 digest/aggregate；历史 E-B0 v2 与 released Foundation/parity 保持分离；authority 119 项及最终 1823 项通过。 |

### P2 findings: 0

未发现需要作为仓库完整性 P2 记录的开放项。测试环境唯一输出为 Starlette/httpx deprecation warning，不改变产品合同或 Gate 结论；后续可随依赖维护处理，但本 Gate 不把外部依赖告警伪装成 RAG 功能缺口。

## 6. 正式库 observer 排除清单

正式库 `/Users/example/project/rag/var/evidence-rag.sqlite3` 及 sidecars 是 external mutable service-owned state。本 Gate 没有打开、hash、checkpoint、复制、删除或观察其 sidecars，也不作任何“不变”声明。

以下五个文件被整文件排除；其中 helper 会对正式库执行 `exists/stat`，C-B3/C-B4 还会 hash，随后再比较 before/after：

| 排除项 | 静态原因 |
|---|---|
| `tests/test_code_cb1_run.py::*` | `_file_state()` 在 [test_code_cb1_run.py](../../../../tests/test_code_cb1_run.py#L31-L35) 调用 `exists/stat`；正式路径在 86–96 行被观察。 |
| `tests/test_code_cb2_run.py::*` | `_file_state()` 在 [test_code_cb2_run.py](../../../../tests/test_code_cb2_run.py#L32-L36) 调用 `exists/stat`；正式路径在 179–191 行被观察。 |
| `tests/test_code_cb3_run.py::*` | `_file_state()` 在 [test_code_cb3_run.py](../../../../tests/test_code_cb3_run.py#L24-L28) 调用 `exists/stat/hash`；fixture 在 41–45 行观察正式路径。 |
| `tests/test_code_cb4_run.py::*` | `_file_state()` 在 [test_code_cb4_run.py](../../../../tests/test_code_cb4_run.py#L28-L32) 调用 `exists/stat/hash`；fixture 在 56–60 行观察正式路径。 |
| `tests/test_code_cb5_run.py::*` | `_file_state()` 在 [test_code_cb5_run.py](../../../../tests/test_code_cb5_run.py#L31-L35) 调用 `exists/stat`；正式路径在 222–236 行被观察。 |

另排除两个精确 node：

- `tests/test_code_baseline.py::test_prepare_uses_three_real_sources_and_an_isolated_database`：119 行对正式路径调用 `.resolve()`。[test_code_baseline.py](../../../../tests/test_code_baseline.py#L113-L120)
- `tests/test_codex_baseline_v1.py::test_runner_rejects_symlink_alias_and_formal_database_without_access`：668–679 行构造正式路径并送入 path normalization/admission；即使 connect 被 spy，路径解析仍可能观察外部状态。[test_codex_baseline_v1.py](../../../../tests/test_codex_baseline_v1.py#L668-L680)

C-B6 **没有排除**。其 `_file_state()` 在任何 `exists/stat` 前对正式 DB 与 sidecar 路径直接返回未观察值，因此安全包含在 1823 项最终选择集。[test_code_cb6_run.py](../../../../tests/test_code_cb6_run.py#L21-L29)

## 7. 实际执行的独立验证

以下数字相互重叠，不相加；均由本 Gate 在 isolated tmp/in-memory 或 immutable repository artifacts 上实际执行，不采用子任务口头测试数。

| 验证 | 结果 |
|---|---|
| Safe collection：排除 C-B1～B5 文件后收集，再 deselect 两个精确 node | `1823/1825 tests collected (2 deselected)` |
| 最终静止源码 safe full pytest | **1823/1823 PASS** |
| 第一次 full（authority 刷新前，诊断性） | 1821 PASS、2 FAIL；两项均为 current E-B0 `platform-search-model` digest mismatch；修复后没有 ignore/deselect，而是重跑全量至 1823 PASS |
| Final typed-task + seven switches + pipeline + Experiment/Notebook Direct/API authority 专项 | **61/61 PASS** |
| Experiment Foundation + current/historical E-B0 authority 攻击集 | **119/119 PASS** |
| Code graph/SCIP/semantic/history/diff/test/platform 专项 | **314/314 PASS** |
| M5/M6 foundation/planner/pipeline/context/answer/query/graph 专项 | **52/52 PASS** |
| status/release/performance/registry 专项 | **59/59 PASS** |
| V1/security/source hardening 专项 | **444/444 PASS** |
| 公开冷 status：零 Runtime/SQLite/mkdir + 首个非 status 并发一次构造 | **4/4 PASS** |
| Frontend Vitest | **22 files / 95 tests PASS** |
| Frontend TypeScript typecheck | **PASS** |
| Frontend isolated production builds | **3 次 PASS；contract 逐字节相同** |
| `ruff check src tests` | **PASS** |
| Python in-memory compile | **369 files PASS** |

关键独立动态复现：

1. typed Notebook 正向：`sent_task=output`、`synthetic_marker=False`、batch complete、candidate task=output；负向 search spec：source calls=0、unavailable、`typed_spec_task_mismatch`。
2. 七开关：每个 disabled 都在 plan/source/pipeline/perf/materialization 前停止，legacy exactly once，零 V2 materialization/span。
3. Direct/API authority：无 authority 走一次 V1 且零 V2；有 evaluator-approved isolated authority 时 Direct 与 Platform served V2、同 authority digest、无 V1。
4. Code graph：1-hop/multi-hop、fusion merge、SCIP exact cross-file、edge direction/type/review/ACL/generation/ref/full commit、dedup/order、无 graph/default V1 空边均在专项和最终全量通过。
5. Frontend：Notebook 独立 label/icon、unknown 原值保留且无伪 verified；最终 build contract 与首次两个 isolated build 完全一致。

## 8. 外部 deferred 边界

以下事项不是 open P0/P1，但仍明确未完成，且不得伪造：

- 正式库 migration/backfill 与 service-owned DB observer；
- 获准的 production replay、人工 truth 与真实 production observation；
- 远程 embedding/reranker/generator、ANN 与真实硬件 P50/P95/P99；
- shadow traffic、canary、production rollback drill；
- 将 reviewed external authority 写入 canonical registry；
- 默认 V2 发布与真实简历收益数字。

因此最终姿态必须继续是：

`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

`L4/L5: EXTERNAL_DEFERRED`
