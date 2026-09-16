# RAG 完成度复核与交互优化计划

- 日期：2026-07-30
- 状态：`REPOSITORY_L0_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`
- 主控任务：`019f9f27-8d88-7b01-b686-3d4acbc5dcf2`
- 执行方式：一个主控维护过程记录，多个独立任务按互斥文件锁实现/审计
- 数据边界：不得读取、hash、checkpoint 或修改
  `EXTERNAL_MUTABLE_SERVICE_OWNED` 正式库及 sidecars

## 1. 为什么重新复核

此前把“模块存在、隔离测试通过、文档矩阵标为 PROVEN”写成了“RAG 完全开发”。从产品
角度该判定不充分。完成必须同时满足：

1. 原始设计合同存在实现；
2. 实现被当前 `Runtime` 构造；
3. production API 能到达实现；
4. 真实请求不会退回旧骨架或 synthetic-only adapter；
5. 端到端测试覆盖入口、失败和回滚；
6. 质量结论来自可信 replay，而不是 self-reported fixture；
7. 默认、opt-in、shadow、canary 和 rollback 状态真实一致。

本轮以原始设计文档为合同：

- `sources/01_CODE_SOURCE_RAG.md`～`sources/06_WORKSPACE_SOURCE_RAG.md`
- `02_MULTI_SOURCE_FUSION_RAG_DESIGN.md`
- `03_GLOBAL_RAG_HARDENING.md`
- `04_IMPLEMENTATION_ROADMAP.md`

`05_ORIGINAL_ROADMAP_IMPLEMENTATION_MATRIX.md` 只记录复核结果，不能作为完成证明。

## 2. 五级完成度

| 等级 | 含义 | 可否称为完成 |
|---|---|---|
| L0 | 只有设计/接口 | 否 |
| L1 | pure contract 或 synthetic harness | 否 |
| L2 | isolated implementation + unit tests | 只能称工程组件完成 |
| L3 | Runtime/API 可达 + 端到端回归 + 默认/回滚正确 | 可称仓库产品工程完成 |
| L4 | 可信 production-like replay + 质量/性能/安全门 | 可称 release candidate |
| L5 | shadow/canary/production observation + rollback 演练 | 可称生产发布完成 |

目标不是把所有项强行标为 L5，而是为每项给出真实等级，并把仍能在仓库内完成的
L2→L3 缺口实现掉。

## 3. 独立审计结论

首次审计发现的“完整非 Code facade 不可达、显式全局 V2 只能回退、回答事实可越级”
均已按原始设计合同修复。当前真实调用链是：

```text
/v1/query
→ UnifiedQueryService.answer
→ PlatformService.search
→ default V1
  | explicit source V2
  | → ProductionSourceRuntimeRegistryV2
  | → complete Code/Codex/Experiment/Notebook/Document/Workspace facade
  | explicit global V2 + reviewed calibration bundle
    → MultiSourceRuntimeV2
    → governed two-wave retrieval / fusion / graph / context
→ trusted interaction + claim/citation verification
→ grounded | retrieval_only | partial | refusal
```

Runtime 默认 V1 不会创建派生 V2 根目录。显式非 Code V2 才惰性创建进程内、可丢弃的
派生索引；正式 store/service 始终是 authority。每个完整来源操作串行化，Runtime
shutdown 会立即拒绝新操作；已开始的操作结束后再关闭派生 SQLite 和清理临时根目录，
不会因为忽略 cooperative cancellation 的旧同步驱动而无限阻塞停机。

六来源、多源和回答主链的仓库内结论如下：

| 范围 | 当前等级 | 结论 |
|---|---:|---|
| Code | L3 | 完整 exact/sparse/dense/graph/history/test/context 由 Runtime/API 可达 |
| Codex | L3 | 显式 V2 到达完整 observable event/episode/facts/context facade |
| Experiment | L3 | governed source 到达完整 facade；无 publication 的手工记录保留兼容投影 |
| Notebook | L3 | 完整 typed cell/output/error/dependency/compare/context facade 可达 |
| Document | L3 | 完整 typed unit/claim/table/hierarchy/context facade 可达 |
| Workspace | L3 | 完整 state/as-of/planning/coverage/audit/context facade 可达 |
| 多源与回答 | L3 | 显式 reviewed-bundle V2 成功；无 bundle fail-closed；可信回答/降级可达 |
| 默认与发布 | 未达 L4/L5 | 继续 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE` |

已关闭的仓库 P0/P1 包括 sparse publication、对象 ACL、MLflow URI policy、Experiment
history/tombstone、完整 facade wiring、来源无关配额、root provenance、graph
ACL/review、未知 metric direction、claim fact-status、置信度、数字/单位/否定/实体
校验、异常脱敏、clarification/follow-up、timeout/partial/refusal、前端可信 query 主链
和派生索引生命周期。最终复审额外关闭：

- Platform/Workspace relation 对 `request.as_of` 与 `[valid_from, valid_to)` 的严格过滤；
- governed Experiment 多 ready source 的 `partial`、全失败和脱敏错误状态；
- Global V2 对 `fact_status`、`review_status`、`derivation` 和已接受 typed relation 的保真投影；
- `/v1/codex/search` 显式 V2 与 Platform 共用完整 `CodexSourceRuntimeV2` facade；
- Codex Direct、Platform 与 global 对 `item_types`、`statuses`、时区感知
  `date_from/date_to` 使用同一权威过滤路径；naive、逆序或畸形时间 fail-closed；
- cooperative cancellation、I/O 边界 deadline 检查、有界 worker drain 与非阻塞 shutdown；
- `RuntimePerformanceV2` 使 exact vector baseline、embedding/scoped cache 和
  P50/P95/P99 dashboard 从 Runtime 可达；观测只保留摘要、闭集状态和聚合计数，
  单 token secret/query/ACL 与未审 generation/scale label 均被拒绝；
- 七步 rollback 对应七个严格布尔运行时开关；任一开关关闭都在 V2 pipeline 前
  fail-closed 并回退 V1，默认 V1 请求不产生 V2 observation；
- `release_admission_v2.py` 使用固定六来源 production authority 和显式白名单
  immutable artifact refs 统一判定；`multisource_release_package_v2.py` 生成 exact
  canonical portable outer package。两者均为纯 verify-only，不扫描整个 `evals`，
  promotion 还必须匹配代码内受审的 fixed artifact/gate authority registry；该 registry
  当前诚实为空，caller/package 自签或重复传入 `trusted_*` 都不能成为信任根，只会得到
  `QUALITY_HOLD / NON_QUALIFIED / DEFAULT_V1`；
- 前端 partial/脱敏错误呈现，以及 production build 经 ASGI `/v1/query` 进入真实
  clarification 交互链；12 个主要页面已 route-level lazy split，最大 JS chunk 从约
  `812 kB` 降至 `217.57 kB`。

同一独立审计任务的最终限定复审结论为
`REPOSITORY_L3_ENGINEERING PASS / P0=0 / P1=0`。主控验证证据：

- 1424 项非历史质量工件的后端测试通过；
- 本轮新增收口后，675 项 `test_*_v2.py` 合同/运行时测试、95 项
  API/Platform/Codex/global/release 集成测试和 170 项 release/performance/package
  专项再次通过；
- 前端 66 项测试、TypeScript typecheck 和 production build 通过；
- production build 由 FastAPI 静态挂载后，浏览器真实完成页面加载、两次
  `POST /v1/query` 和 clarification 呈现；
- 本轮变更文件 Ruff、format、`git diff --check` 与内存 compile 通过；
- Code C-B1～C-B6 与 Experiment E0/E-B0 绑定旧组件摘要的历史质量测试继续
  fail-closed，未被重签或冒充为当前 qualification。

以上数量与结论保留为 2026-07-30 历史收口证据；2026-07-31 最终 Evidence Sync 不
删除它们，但仓库完成度的 current authority 已由
[最终独立 Gate](reviews/13_RAG_FINAL_COMPLETENESS_GATE_REVIEW.md) 的 superseding
section 取代。最终主控证据为：

- 安全 backend `1833/1833` 通过四文件分片验证；补充的单进程运行在约 90% 前无断言
  失败，随后卡在 Python 3.13 线程退出钩子，因此不冒充为单进程全绿；
- 299 个 changed Python 文件 Ruff、format、diff、in-memory compile 全部通过，
  static/compile 通过；
- frontend `95` 项测试、TypeScript typecheck 与 production build 通过，build SHA
  `ce9d7c…b6e2`；
- Codex current authority v3 `4e63852f…39e9`，历史 v1/Run/correction authority
  冻结；Experiment Foundation v3 `26363ae0…211a7`、parity
  `5c605c94…8764`、E-B0 v4 `2c82f1d2…d891`，历史 authority 冻结；
- production registry v3 component-set `ee64f94d…f28d`、content
  `43f3fcfe…fba6`，六来源 `all_current=True`；
- 最终 Gate 关键路径 `115/115`，`P0/P1/P2=0`，结论为
  `RAG REPOSITORY L0-L3 ENGINEERING COMPLETENESS GATE PASS`。

这些证据只证明仓库 L0–L3 工程完成。Codex X-T1 仍为
`FAILED_BEFORE_PUBLISH / NO_RESULT`；Codex/Experiment current control 与既有各来源
baseline 的 `NON_QUALIFIED`/`VERIFIED_NON_QUALIFIED` 继续有效，不能被工程 Gate
提升为 release 或 quality qualified。

真正的外部阻塞仅包括真实脱敏请求与人工 truth、可信身份/ACL 映射、获准 production
snapshot、生产模型/ANN/硬件、shadow/canary/rollback 权限。这些阻塞不再被用于解释仓库
内 wiring、安全和数据正确性缺口。

七个仓库运行时 rollback 开关默认均为 `true`，只影响显式 global V2：

`RAG_GLOBAL_GENERATOR_PROMPT_V2`、`RAG_GLOBAL_CONTEXT_PACKER_V2`、
`RAG_GLOBAL_FUSION_V2`、`RAG_GLOBAL_RERANKER_V2`、
`RAG_GLOBAL_EMBEDDING_GENERATION_V2`、`RAG_GLOBAL_SOURCE_RETRIEVERS_V2`、
`RAG_GLOBAL_PLANNER_V2`。环境值只接受精确的 `true` 或 `false`。

## 4. 当前交互优化逻辑

### 4.1 交互状态机

```text
RECEIVED
→ SCOPE_RESOLVED
→ INTENT_AND_COMPLEXITY_RESOLVED
→ NEEDS_CLARIFICATION | PLAN_READY
→ RETRIEVING_WAVE_1
→ COVERAGE_CHECK
→ RETRIEVING_WAVE_2 | GRAPH_VERIFY
→ CORRECTIVE_1
→ CORRECTIVE_2
→ EVIDENCE_READY | PARTIAL | REFUSAL
→ GENERATING
→ CLAIM_CITATION_VERIFY
→ GROUNDED_ANSWER | RETRIEVAL_ONLY_FALLBACK
```

每个状态都必须输出机器可读 `interaction_state`、`reason_code`、`missing_roles`、
`source_status`、`next_actions`，不能只返回一段文本。

### 4.2 请求理解顺序

1. 先锁定 project/repository/thread/run/document/as-of/ACL，不允许 rewrite 改写显式 ID。
2. 再判定 task、answer mode、复杂度和 required roles。
3. scope 缺失或歧义会造成不同答案时进入 `NEEDS_CLARIFICATION`，不盲搜全局。
4. 简单 exact/status 请求优先 deterministic；只有需要综合解释时才进入 grounded
   generation。
5. 用户 follow-up 必须显式引用前次 `interaction_id` 和选择的 scope/evidence
   digest，不隐式信任自然语言历史。

### 4.3 检索与纠错

1. Wave 1：exact/structured，低延迟获得 identity 和 scope。
2. coverage check：按 typed role contract 检查，不以“某来源出现过”代替角色满足。
3. Wave 2：只为缺失角色启用 sparse/dense/source graph。
4. typed graph：只走注册边，ACL/version/review 前置。
5. 最多两轮 corrective retrieval；每轮必须记录新增证据和停止原因。
6. timeout/unavailable/unauthorized/not-indexed/no-match 分开，不把超时写成无证据。

### 4.4 回答与反馈

回答固定包含：

- `answer_mode`：deterministic / grounded / retrieval_only / partial / refusal；
- `answer` 与逐 claim citation；
- `applicable_scope` 和 consistency watermark；
- `counter_evidence`、`missing_roles`、`source_status`；
- `confidence_basis`，不输出无依据概率；
- `next_actions`：补 scope、同步来源、运行测试/实验、人工确认；
- `trace_id` 和可用于 follow-up 的 `interaction_id`。

LLM 失败、citation 校验失败或 scope/version 不一致时，必须自动降级为
`retrieval_only`，而不是整个 API 返回 502。

## 5. 已实现的 additive 接口

`QueryRequest` 已增加可选字段：

```text
interaction_id
parent_interaction_id
clarification_answer
engine: v1 | v2
latency_budget_ms
allow_generation
```

响应已增加：

```text
interaction_state
reason_code
answer_mode
required_roles / satisfied_roles / missing_roles
source_status
corrective_rounds
consistency_watermark
next_actions
claim_verification
fallback
```

所有新增字段保持 additive；默认仍 V1。V2 只能显式 opt-in，直到可信 replay 和 release
gate 通过。

## 6. 多任务分工与文件锁

第一阶段三个独立任务完成只读审计：

1. 原始设计到 runtime/API 的可达性审计；
2. 当前交互、Query/Answer 状态机审计；
3. 评测、性能、安全与发布证据审计。

第二阶段按互斥写锁实施并由主控集成：

- A：Notebook ACL、MLflow URI policy、Experiment history/tombstone；
- B：`models.py`、`query/interaction_v2.py`、answerability/citation/fallback；
- C：planner routing、全局融合、graph ACL/review、source partial/deadline；
- 主控：Code sparse publication、`runtime.py`/`api.py` V2 opt-in 接线、前端与最终回归。

所有实现必须保留 `DEFAULT_V1 / QUALITY_HOLD`，并通过 production API 可达性测试后才从
L2 升为 L3。

## 7. 验收门

### L3 P0（已通过）

- [x] governed M5/M6 path 能由显式 V2 production API 到达；
- [x] ACL/scope/version/watermark fail-closed；
- [x] relation 的 as-of/validity、review、ACL 与 generation 过滤 fail-closed；
- [x] 多 ready source 的 partial/all-fail 状态不伪装 complete/no-match；
- [x] multi-source adapters 使用真实 store/retriever，不从 Golden label 生成候选；
- [x] Global projection 保留事实/复核/派生状态与已接受 typed relation；
- [x] LLM/citation failure 有 retrieval-only fallback；
- [x] 请求、回答、trace 不泄漏 secret/ACL/raw absolute path。

### L3 P1（已通过）

- [x] clarification/follow-up contract；
- [x] wave/coverage/corrective 状态可解释；
- [x] timeout/cancel/partial 状态真实；
- [x] cooperative cancellation、deadline 边界和非阻塞 Runtime shutdown；
- [x] candidate/context budget、来源状态和 cache identity 可追踪；
- [x] direct Codex V2 与 Platform 共用完整 facade，默认 V1 零 V2 物化；
- [x] production frontend build 经 ASGI 到达真实 query/clarification 主链；
- [x] Codex Direct/Platform/global 对 type/status/time scope 等价且非法时间 fail-closed；
- [x] M7 exact index/cache/dashboard 由 Runtime 所有，观测字段有界且脱敏；
- [x] 七个 rollback component switches 在 V2 pipeline 前生效；
- [x] 六来源 authority freshness、统一 release admission 与 multisource exact-files
  portable outer package 可 verify-only，且不得扫描 whole `evals`；
- [x] 主要页面 route-level code splitting，生产构建无超大 chunk warning；
- [x] 默认 V1 零 V2 物化，显式 V2/失败回退和 Runtime shutdown 生命周期可验证。

### L4/L5 发布门（未通过）

仓库内 P0/P1 完成后仍只允许标为 L3。历史 Code C-B1+ 与 Experiment E-B0 的固定
production identity 已被后续合法 P0 变更触发 fail-closed，它们不能作为当前质量证据，
也不能直接重签。没有重新授权的真实 production replay、reviewed calibration、
exact 60 membership 联合 replay、P50/P95/P99/SLO、shadow/canary 和 owner 授权时，
继续 `DEFAULT_V1 / QUALITY_HOLD`。

## 8. 2026-07-30 收口记录

本节是后续任务恢复时的最小过程记录，不能替代上面的设计合同或测试。

1. 先按 source design、multi-source design、global hardening 和 roadmap 做只读差距审计；
2. 修复唯一来源主路缺口：Codex V2 公共 type/status/time scope；
3. 将 M7 性能合同接入 Runtime，并补七组件 pre-pipeline rollback switches；
4. 增加固定 production authority 的统一 release admission 和显式 artifact allowlist；
5. 增加 multisource exact canonical portable outer package，明确 `NON_QUALIFIED`；
6. 对性能观测做独立隐私审计，发现单 token secret/ACL/query 可进入公开 API 后，改为
   trace 摘要、source/error 闭集、generation 摘要和 dashboard key 闭集；复审
   `P0=0 / P1=0`；
7. 前端完成 route-level lazy split；66 tests、typecheck、build 通过，最大 JS chunk
   `217.57 kB`；
8. 后端 675 项 `test_*_v2.py`、95 项关键集成、170 项 release/performance/package
   定向回归通过；最终独立设计复审未发现剩余仓库内 L0–L3 P0/P1。

当前停止线经最终 Gate 更新为：
`REPOSITORY_L0_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`。
下一步若要进入 L4/L5，必须由外部 owner 提供获准 production-like
replay、人工 truth、正式身份/ACL、模型/ANN/硬件观测以及 shadow/canary/rollback 权限；
不得在仓库内伪造或重签。

## 9. 2026-07-31 多会话重新核验与开发记录

用户要求恢复“多会话、计划驱动、主会话验收”的开发方式，并明确不能仅依据历史
`COMPLETE` 标签停止。因此本节保留当时把 `REPOSITORY_L3_ENGINEERING_COMPLETE`
视为**待重新核验历史结论**的 reopen/audit 记录。下列审计发现当时有效并触发了真实
修复；它们不是当前未完成项。2026-07-31 的最终主控回归与独立 Gate 已完成，当前结论由
第 9.6 节及 [review 13](reviews/13_RAG_FINAL_COMPLETENESS_GATE_REVIEW.md) 的
superseding section 取代。

### 9.1 总目标与停止条件

- 总目标：对照原始路线图、单源设计、多源设计和交互优化计划，完成所有仍可在仓库内
  实现的开发项；
- 执行模式：`COORDINATED_MULTI_SESSION / SHARED_DIRTY_MAIN /
  EXPLICIT_NON_OVERLAPPING_WRITE_LOCKS / NO_COMMIT / NO_PUSH`；
- 主控职责：维护任务清单和写锁、复核子任务证据、处理交叉集成、执行最终回归；
- 停止条件：仓库内 L0–L3 设计项不存在未解释的 `MISSING/PARTIAL`，后端、前端、安全、
  静态和 isolated 数据测试通过，过程记录与实施矩阵同步；
- 非停止条件：仅模块存在、仅定向测试通过、仅文档标记完成、或以外部阻塞掩盖仓库内
  可实现缺口；
- 外部边界继续保留：正式库与 sidecars 不访问，production replay、远程模型/ANN、
  shadow/canary、默认 V2 发布不得伪造，默认继续 `V1 / QUALITY_HOLD`。

### 9.2 第一轮独立只读审计任务

| 任务 | Task ID | 范围 | 写锁 | 状态 |
|---|---|---|---|---|
| 六类单源实现审计 | `019fb727-3086-7191-bb94-1e6b1459889c` | Code/Codex/Experiment/Notebook/Document/Workspace 的设计到生产可达实现 | 无，只读 | `COMPLETE / P0=0 / P1=8 / P2=3` |
| 多源与发布工程审计 | `019fb727-6074-70a3-bf50-d38049a1bcb7` | M5–M7、Runtime/API、性能、安全、回滚、release package | 无，只读 | `COMPLETE / RELEASE P0=1 / P1=6 / P2=3` |
| 前端交互与交付面审计 | `019fb727-9837-7cc2-870d-31ad97a9f111` | 查询交互、trace/citation/fallback、dashboard/switches、构建与可达性 | 无，只读 | `COMPLETE / P0=2 / P1=8 / P2=4` |

审计任务只产生带文件/行证据的 `P0/P1/P2` 差距表。主控收到结果后，按互斥文件锁
创建实现任务；实现任务不得自行把结果标为完成，必须回到主控统一回归和最终验收。

### 9.3 已完成的确认缺口修复与最终证据同步

| 任务 | Task ID | 写锁 | 验收目标 | 状态 |
|---|---|---|---|---|
| Workspace V2 历史/日期主路 | `019fb72d-a93d-7892-8cc0-b2ddea43426d` | `sources/workspace/{runtime_v2,state_v2,retriever}.py` 与对应两项测试 | 不再丢弃 `date_from/date_to`；使用可证明 audit transition 做诚实 as-of 投影；非法或不可证明历史 fail-closed | `COMPLETE / TASK TESTS PASS / MAIN SHARDED REGRESSION PASS / FINAL GATE PASS` |
| Code C5/C6 生产主路 | `019fb72f-e34f-7013-9e01-db882468ac5f` | `config.py`、`runtime.py`、`ingestion.py`、Code SCIP/semantic/history/diff/test/dense 模块及定向测试 | 配置真实生效；SCIP/semantic 与 history/diff/test hook 进入显式 V2 ingestion/query 主路 | `COMPLETE / 426 RELATED TESTS PASS / API EDGE PROJECTION CLOSED / FINAL GATE PASS` |
| Codex CX2/CX5 store/governance | `019fb730-132b-7042-a5f2-ff602803d214` | `production_sources_v2.py`、Codex runtime/pipeline/store/governance 与定向测试 | 显式 V2 才创建 isolated store；跨请求 publication/cache；append/privacy/rollback/LKG 可达 | `COMPLETE / 62 TASK TESTS PASS / MAIN API ASSERTIONS CLOSED / FINAL GATE PASS` |
| Experiment E3 + Notebook N4/N5 typed API | `019fb730-40b6-7be0-b3ad-618e64d7c492` | Platform models/service、`api.py`、Experiment analysis/query、Notebook retrieval/compare 与测试 | typed compare/filter 从 API 可达，歧义/错版本 fail-closed，旧 schema/V1 兼容 | `COMPLETE / 57 TASK TESTS PASS / GLOBAL GRAPH INTEGRATION PASS / FINAL GATE PASS` |
| Document D5 parser | `019fb730-6395-7303-801b-de2b3ab3297d` | 正式 document adapter/service、Document V2 adapter/runtime/contracts、parser fixture/tests | 扫描检测、OCR/layout provider/provenance、真实 bbox 与诚实三态进入 ingestion→query | `COMPLETE / 66 TASK TESTS PASS / GLOBAL GRAPH INTEGRATION PASS / FINAL GATE PASS` |
| 前端可信查询交互 | `019fb731-0889-7730-822e-96755cd0cbbd` | Search/TrustedQueryPanel/query contract/client types/API、AppShell、`app.css` | 关闭跨项目 scope 漂移和未知状态 grounded；补完整 trace/fallback/history/source/feedback/citation/state | `COMPLETE / FINAL 95 FRONTEND TESTS + TYPECHECK + BUILD PASS / FINAL GATE PASS` |
| Shell 与构建交付 | `019fb731-4139-7a80-a8cf-cfba0c0e361f` | `App.tsx`、`main.tsx`、`ui.tsx`、Vite/clean/package、Dockerfile、Makefile、web contract test | ErrorBoundary/a11y；源码到部署产物 deterministic；构建链不盲拷 stale `web` | `COMPLETE / HISTORICAL 85+19 TESTS PASS / FINAL 95 + TYPECHECK + BUILD PASS / FINAL GATE PASS` |
| M7 canonical release authority 核心 | `019fb734-6fe7-7d23-ba1c-c8457d352190` | release admission/package/global governance、新 control-plane core 与测试 | caller-authored promotion 不可旁路；空 reviewed registry 必须 HOLD；integrity 与 authority 分离 | `COMPLETE / 105 TASK TESTS PASS / RUNTIME API CLI INTEGRATION PASS / FINAL GATE PASS` |
| M5/M6 typed semantics 核心 | `019fb734-9ade-7b60-ab4d-3504b9a5529f` | multisource foundation、evidence graph、answer core 与测试 | source/task capability、真实 entity type/edge、rerank/calibration core、retrieval-only/answer authority 合同 | `COMPLETE / 120 TASK TESTS PASS / RUNTIME PIPELINE INTEGRATION PASS / FINAL GATE PASS` |
| M7 canonical authority Runtime/API/CLI | `019fb750-3e10-70e3-8e1f-b281cf958976` | `config.py`、`runtime.py`、`api.py`、`cli.py`、release control/admission 与测试 | canonical empty reviewed registry 成为唯一 admission；只读 status/verify；默认 V1/QUALITY_HOLD | `COMPLETE / TASK CHECKS PASS / GLOBAL SWITCH + OPERATIONAL SNAPSHOT PASS / FINAL GATE PASS` |
| 五来源 release + M5/M6/M7 query-path | `019fb750-781e-7260-ba37-913549acd0bc` | multisource runtime/pipeline/foundation/graph/answer/performance/switches、production source registry、五来源 runtime/release/governance、Platform/Query service 与测试 | stage/LKG/fallback；typed semantics；单一回答权威；独立开关；实际 index/cache/dashboard 接线 | `COMPLETE / 285 ISOLATED TESTS PASS / STATIC PASS / STATUS + DIRECT API BRIDGE PASS / FINAL GATE PASS` |
| M7 只读运维状态前端 | `019fb74f-4cf0-7041-bffd-e5cf4749739f` | 全新 ops feature、前端 API/types、App/Shell/style 与测试 | 显示 release/source/switch/performance/calibration 真值；未知/错误 fail-closed；无切换/发布写操作 | `COMPLETE / 93 TASK TESTS + TYPECHECK + TMP BUILD PASS / BACKEND FIELDS INTEGRATED / FINAL GATE PASS` |
| Release status + source direct API 最终桥接 | `019fb76e-ba89-74e3-9136-7b8f0fd58c05` | release control plane、Runtime/API/CLI、必要 source direct routers、ops contract 与定向测试 | canonical release authority 与脱敏 operational snapshot 合并；direct/Platform/global 共用 registry；status 零 materialization/零 DB | `COMPLETE / 37 BACKEND + 4 FRONTEND TESTS PASS / STATIC + TYPECHECK + BUILD PASS / FINAL GATE PASS` |
| Code C5/C6 edge identity 最终投影 | `019fb78d-c238-7580-ab10-c78be52816fa` | Code contracts/graph retrieval/fusion/platform projection 与定向测试 | canonical edge ID 从 traversal 保留至 API；scope/generation/ACL/review/type fail-closed；默认 V1/无 graph 兼容 | `COMPLETE / 327 TESTS PASS / STATIC PASS / FINAL GATE PASS` |
| Notebook typed tasks 真实 enum 主路 | `019fb7d2-52d3-7eb1-9a3f-11aa65d9b506` | Notebook typed task marker、models/service/API/query/interaction 主路与测试 | 删除历史 marker；真实 enum 端到端贯穿，不再以字符串或文档标记替代 typed contract | `COMPLETE / MARKER REMOVED / REAL ENUM END-TO-END / FINAL GATE PASS` |
| 最终只读 drift audit | `019fb7f2-45f8-7d10-8b4a-e3b77ce10970` | 六来源、typed 主路、authority、release 与交付证据只读复核 | 识别并关闭 current authority 与文档状态漂移，不读取正式库/sidecars | `COMPLETE / NO REPOSITORY L0-L3 DRIFT / FINAL GATE INPUT` |
| Release authority 最终修复 | `019fb7fa-2e97-7131-ade8-2a7ce4d7521d` | production registry v3 与 release admission current authority | component-set/content 固定且六来源 current；caller-authored promotion 仍不可旁路 | `COMPLETE / ee64f94d…f28d / 43f3fcfe…fba6 / all_current=True` |
| Codex authority 复用 | `019faa29-b37b-7360-8dcc-a57607ae9a17` | Codex current authority 与历史 authority 冻结 | current v3 可复用；历史 v1/Run/correction 不重签、不冒充 current | `COMPLETE / CURRENT v3 4e63852f…39e9 / HISTORICAL FROZEN` |
| Experiment authority 复用 | `019fb7a3-e322-7d72-a03d-32509aaf49fc` | Experiment Foundation/parity/E-B0 current authority 与历史冻结 | Foundation v3、parity、E-B0 v4 成为 current；历史 artifacts 不重签 | `COMPLETE / 26363ae0…211a7 / 5c605c94…8764 / 2c82f1d2…d891` |
| 最终独立 completeness Gate | `019fb7a0-ac28-7ac1-9235-1168d6b6b142` | 原路线图 L0–L3、六来源 current authority、关键路径、静态与前端证据 | 独立给出 superseding conclusion，并保持 L4/L5 与质量发布边界 | `COMPLETE / 115/115 / P0=0 / P1=0 / P2=0 / GATE PASS` |

这些任务来自第一轮审计确认的实际生产路径缺口，均已实现、集成、主控回归并进入最终
Gate。表内历史定向测试数量保留作为过程证据；`MAIN REGRESSION PENDING`、API assertion、
graph integration、bridge、backend fields 和 build pending 均已由最终 backend/static/
frontend/Gate 证据关闭。历史 Docker daemon unavailable 只代表当时环境未提供 daemon，
不改变当前仓库 deterministic build contract 与最终 production build 通过结论。

### 9.4 第一轮审计结论

本节保留第一轮审计时点的结论。彼时历史
`REPOSITORY_L3_ENGINEERING_COMPLETE / P0=0 / P1=0` 被当时代码反证，因此文档顶部
曾降为 `REOPENED_IMPLEMENTATION_IN_PROGRESS`：

- 六类单源审计：`P0=0 / P1=8 / P2=3`；
- 多源/Runtime/发布审计：release authority `P0=1`，M5–M7 生产接线
  `P1=6`，部署/表述 `P2=3`；
- 前端/交付审计：可信 scope 与未知状态提升 `P0=2`，交互/可观测/构建/M8
  `P1=8`，可访问性/交付 `P2=4`。

当时据此启动的任务覆盖 Workspace、Code、Codex、Experiment、Notebook、Document、
前端可信查询和 Shell/构建，并安排以下共享 hot-file 工作串行完成：

1. `[x]` 单源五来源 release router 与可验证 LKG/fallback；
2. `[x]` canonical release admission/API/CLI/package 入口，旧 caller-authored promotion
   不得旁路；
3. `[x]` M7 index/cache/dashboard 真实 query-path 接线和七组件独立 rollback；
4. `[x]` calibration reviewed authority、M5 capability/typed entity/reranker、M6 单一回答权威；
5. `[x]` 只读 M7 运维状态 API/UI、部署面和最终证据同步。

以上 reopen/audit 记录继续保留，以说明修复为何发生；其差距数量与 pending 队列已被
第 9.3 节完成证据和第 9.6 节最终 Gate supersede，不再代表 current repository status。

### 9.5 主控最终验收协议（已执行）

所有实现任务释放写锁后，主控已按同一工作区现状执行验收，没有把各任务自己的定向结果
简单相加后宣称完成：

1. `[x]` 299 个 changed Python 文件 Ruff、format、diff 与 in-memory compile 通过；
   static/compile 通过；
2. `[x]` 六来源、M5–M7、Runtime/API/CLI、Query/Platform、安全和 product-e2e 的
   安全 backend `1833/1833` 由四文件分片全部通过；全程未观察正式库或 sidecars；
3. `[x]` frontend `95` 项测试、TypeScript typecheck 和 production build 通过，build
   SHA `ce9d7c…b6e2`；
4. `[x]` 独立 completeness Gate 对照原路线图完成 `115/115` 关键路径复核，六来源
   `all_current`，仓库内 `P0/P1/P2=0`；
5. `[x]` `05_ORIGINAL_ROADMAP_IMPLEMENTATION_MATRIX.md` 与本文件状态已同步为
   `REPOSITORY_L0_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`。

### 9.6 最终 superseding Gate 与当前边界

[review 13](reviews/13_RAG_FINAL_COMPLETENESS_GATE_REVIEW.md) 的最终 superseding
section 是当前完成度 authority，Gate 任务为
`019fb7a0-ac28-7ac1-9235-1168d6b6b142`。最终结论是：

`RAG REPOSITORY L0-L3 ENGINEERING COMPLETENESS GATE PASS`

- 仓库内 L0–L3 队列完成，没有剩余可实现项，`P0/P1/P2=0`；
- 关键路径 `115/115`，六来源 authority `all_current=True`，static/compile 与 frontend
  验收通过；
- Notebook typed tasks 历史 marker 已删除，真实 enum 贯穿主路；
- Codex、Experiment 与 production registry current authority 使用第 9.3 节所列 v3/v4
  digest，历史 v1/Run/correction/parity/baseline authority 按各自规则冻结；
- L4/L5 production-like replay、shadow/canary、production observation/rollback、默认
  V2、正式库 migration/backfill 和外部服务继续 `EXTERNAL_DEFERRED`，不是仓库开发缺口；
- 正式库为 `EXTERNAL_MUTABLE_SERVICE_OWNED`；仍禁止 observe/open/hash/checkpoint/copy
  以及检查 sidecars；
- Codex X-T1 `NO_RESULT`、Codex/Experiment 与各来源 `NON_QUALIFIED`、全局
  `QUALITY_HOLD / DEFAULT_V1 / NO_RELEASE` 均保持不变。

因此，本文件早期的 reopen 与第一轮 audit 结论是必要且保留的历史过程证据，但已被最终
Gate supersede；supersede 只更新仓库工程完成度，不删除负质量证据，也不授予 release。

### 9.7 2026-07-31 Live Startup Acceptance（已执行）

第 9.5 节记录的是最终工程 Gate 前的历史主控验收数字；本节记录在最终静止源码上重新执行
的真实启动验收，并以
[review 14](reviews/14_RAG_LIVE_STARTUP_GATE_REVIEW.md) 作为独立运行证据。它补强 L3
“Runtime/API/产品页面真实可达”，不改变第 9.6 节的 L4/L5 外部边界。

主控先在 `/tmp/rag-live-qa.*` 启动 development backend 与 Vite UI，随后把同一最终前端
源码构建为 production bundle 并由后端直接提供；独立 Gate 再从新的
`/tmp/rag-live-gate.*` 根、production 配置和独立端口从零复现。两轮均只使用隔离数据、
Codex 与 allowed roots，未访问正式库或 sidecars。

已完成并观察到的产品主路：

1. `[x]` 真实进程 startup、health、117-path OpenAPI、release status、production index、
   hashed JS/CSS 与鉴权/ACL fail-closed；
2. `[x]` 六来源通过 HTTP 摄取并进入同一全局检索与 `/v1/query`，不是仅创建 fixture；
3. `[x]` 默认 V1；显式全源 V2 在当前空 reviewed release authority 下通过 global、
   per-source authoritative 与 nested trace 诚实回退 V1；
4. `[x]` desktop/mobile 浏览器覆盖项目总览、可信查询、运维状态、六来源 override、
   citation/interaction/source status；无 console warning/error 与 mobile 横向溢出；
5. `[x]` 实际运行发现 source routing 展示缺口后完成修复：类型系统显式声明
   `source_engine_routing`/`release_route`，呈现优先级为 legacy → source routing →
   release route → platform governed routing；两条权威路径使用独立回归；
6. `[x]` development frontend/backend 与独立 production backend 均有序 shutdown，
   PID 与监听端口退出。

最终静止源码验证：

| 类别 | 结果 |
|---|---|
| Safe backend selection | `1823/1823 PASS`；继续排除正式库 observer 文件与两个 formal-path node |
| Frontend | `22 files / 97 tests PASS`；typecheck PASS |
| Production build | isolated 与 `web/` contract 相同；source `7740b898…61cf3`、artifacts `3ba21944…088f`、build `21b9f585…2a34e` |
| Frontend independent review | `P0=0 / P1=0 / P2=0` |
| Ruff / Python compile | `src tests` PASS / `369 files PASS` |
| Live startup Gate | `RAG LIVE STARTUP ACCEPTANCE GATE PASS / P0=0 / P1=0` |

因此当前已经有“源码合同 + 安全全量 + production bundle + 真实进程 + 六来源 HTTP + 浏览器
交互 + shutdown”的 L3 证据。该证据不是 production traffic replay、质量 qualification、
shadow/canary 或 production rollback drill，不授权默认 V2。状态继续为：

`REPOSITORY_L0_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

`L4/L5: EXTERNAL_DEFERRED`

## 10. 2026-08-01 产品交互与管理能力续建

第 9 节证明的是 RAG 检索、融合、回答、安全和交付主路达到仓库 L3；它不自动证明会话
工作区、关系图人工审阅、智能体管理、Codex 插件控制面或智能查询体验已经产品化。本节
是这些交付面的 current roadmap，完成状态以真实页面和浏览器路径为准。

| 阶段 | 当前状态 | 实现/验收合同 |
|---|---|---|
| UI-S1 会话同步与拖拽 | `COMPLETE / LIVE VERIFIED` | 并发读取不再重复切换 WAL；请求可取消；路由切换无陈旧 turn；面板和节点拖拽使用 pointer capture；隔离服务与浏览器实测通过 |
| UI-G1 关系图谱人工审阅 | `COMPLETE / LIVE VERIFIED` | 导航已有来源、目标、路径和返回上下文；跳转产生可见 selection/history；节点详情显示类型、scope、版本、证据、引用、review 状态和相邻关系；无效/非唯一 relation fail-closed |
| AG-1 智能体控制面 | `SAFE READ-ONLY PHASE COMPLETE` | agent 作为受治理实体显示 connector/capability/scope/ACL、同步/错误和有界审计；stale、unknown、revoked、跨项目状态不显示为可用；高权限生命周期写操作未授权 |
| CP-1 Codex 插件产品化 | `OBSERVABILITY / DISCOVERY PHASE COMPLETE` | Core、MCP token、client authorization、capability snapshot、同步状态和错误被分别表达；不把路径/CLI 或 source RAG 工程完成等同于插件 ready；安装/卸载未授权 |
| IQ-1 智能查询 | `COMPLETE / LIVE VERIFIED` | 已接入 scope/intent composer、零检索计划预览、计划失效保护、六来源证据/反证、图谱联动、follow-up 和项目 ACL 持久审计历史 |

### 10.1 UI-S1 已完成证据

- `storage.py` 仅在初始化设置 WAL，普通读取连接不再产生 journal-mode schema lock；
- 会话 list/sync/workflow/timeline/turn-audit 请求均能随路由变化取消；
- 首次加载、空结果和真实同步结果采用不同 UI 状态，不再以初始空数组冒充“0 个会话”；
- route/thread 切换重置 turn、tab、layout 和 drag transient state；节点把手无嵌套按钮；
- 后端相关回归、前端 10 项、typecheck/build/static 均通过；隔离运行下 80 个并发读取全
  200，浏览器真实拖拽、自动布局、会话切换和 console 检查通过；
- Codex current authority 因 `SQLiteStore` 合法变化刷新为 v4，历史 artifact authority
  保持冻结；不访问正式库或 sidecars。

### 10.2 UI-G1 实施顺序

1. 先冻结 graph selection/navigation/detail/review 的前端状态合同和 URL 合同；
2. 修复“点击后无痕跳转”：在画布、面包屑、浏览器 history 和详情面板同时表达当前节点；
3. 把节点详情从标签摘要升级为 evidence-first inspector，并支持在详情中查看/跳转边；
4. 增加返回前一视图、保持 zoom/pan/filter、focus-visible、移动端抽屉和无动画降级；
5. 用 fixture 图、direct URL、back/forward、跨来源边、无权限/缺失节点和浏览器实测验收。

AG-1、CP-1 与 IQ-1 必须复用现有 project/scope/ACL/trace authority，不另建 caller 自报的
权限或结果真值。UI-G1 完成后，先实现 AG-1/CP-1 的只读状态与错误面，再接入 IQ-1；所有
写操作和 agent execution 需在后续单独授权。

### 10.3 当前实施合同与剩余验收

- `IQ-1` 已有零检索 plan preview、project ACL audit history、显式 repository/commit/as-of/
  intent composer 和 graph evidence deep link；完成态仍需在最终隔离服务上验证刷新历史、
  计划预览不触发 retrieval、查询后 audit 可重开。
- `UI-G1` 只有真正的 BindingCandidate edge 可进入 review；无效 relation ID 必须显示错误并
  禁用 mutation。任何 server review actor 必须来自可信服务身份，不能接受前端自报姓名。
- `AG-1/CP-1` 第一阶段只交付权威只读控制面和已有 execution 的安全管理，不开放 agent
  注册、token 签发、插件安装/卸载或批量审批。这些破坏性/权限型动作需独立设计和授权。
- 最终验收必须同时通过：相关后端与前端测试、TypeScript、production build、隔离 backend
  restart、desktop/mobile 浏览器路径、console 与横向溢出检查。未完成上述步骤前，本节状态
  只能是 `IN_PROGRESS`，不能提升为 live complete。

### 10.4 最终实现与验收证据（2026-08-01）

- UI-S1：隔离运行态真实完成会话加载、两次 thread 切换、鼠标 pointer drag、键盘移动与
  layout reset；旧 thread 的 goal/layout 不会污染新 thread。
- UI-G1：图谱 node click 同步 URL/focus/history/inspector，浏览器 back 恢复前一 focus；
  inspector 显示 scope、ACL、citation、version 和相邻关系。非法 relation 显式报错、无默认
  选择、无 mutation；人工 review actor 固定为 server identity。
- AG-1/CP-1：project-scoped 只读 agents/Codex 面板和 capability/sync/audit backend 已接入；
  exact token authority、15 分钟 TTL、UTC expiry、rotation、project ACL、generation/source gate、
  redaction、ordering、retention 经 `40/40` 独立对抗复核。未授权 client 的启动入口禁用。
- IQ-1：`POST /v1/query/preview` 明确 `retrieval_performed=false`；真实 query 返回六来源状态、
  `15` 条 evidence 与 counter evidence；`GET /v1/query/history` 在刷新后可重开该查询，且只保存
  digest、闭集状态、source/entity identity 和 watermark，不保存原始问题/答案/secret。
- 最终静止源码回归为后端 `1855 passed, 2 deselected`、前端 `26 files / 133 tests`，TypeScript、
  production build、Ruff check/format、`git diff --check`、`354` 文件 in-memory compile 全部
  PASS。隔离服务在 `127.0.0.1:8000` 使用 system-temp 数据根运行；当前 bundle 后 console
  无新增应用 warning/error，document 无横向 overflow。

因此 UI-S1、UI-G1、IQ-1 以及 AG-1/CP-1 的首个安全只读产品阶段已完成。不会把这一定义
扩大为生产 release 或高权限管理完成：agent 注册、插件安装/卸载、token 生命周期 UI、批量
审批、正式库 migration/backfill、远程 quality replay、shadow/canary 和默认 V2 仍需单独
授权或外部 owner 执行。全局结论保持：

`REPOSITORY_L0_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

### 10.5 真实项目数据纠偏与交互再验收（2026-08-01）

第 10.4 节使用的小型会话 fixture 不能外推为当前项目的完整会话识别，且当时没有在同一
隔离数据根重建真实代码仓库。用户用真实页面发现缺口后，本阶段把 UI-S1/UI-G1 的数据
规模与人工审阅路径重新打开，并以下列证据取代“小样本即完整”的推论：

| 项目 | 真实隔离验收结果 |
|---|---|
| Codex discovery | `609` candidates；`111` project matches；`498` other-project；`110` indexable；`1` oversized；complete=true |
| Codex publication | `110` indexed；`105` main；`5` subagent；`52,497` items；`75,833` edges；`291` redacted；`0` parse errors |
| Code repository | `1` ready repo；`704` files；`8,021` symbols；`28,135` edges；`8,725` views；`13` commits；`500` diff hunks |
| Live concurrency | sync 中 24 个 workflow/repository/graph 请求全 `200`；avg `0.67s`；max `1.91s` |
| Graph UX | desktop 三段审阅；可见 focus/path/source→target/back；完整 authority 摘要；mobile drawer 保留返回路径 |
| Verification | backend safe `1861 passed, 2 deselected`；frontend `28 files / 144 tests`；typecheck/build/static PASS |

实现边界如下：

1. discovery 必须 complete-walk → project binding → safety/size → selection limit，不能先截断；
2. partial traversal、unsafe symlink、unreadable、oversized、other-project 和 unindexed 都是不同
   诊断，UI 不得合并成“0 个会话”；
3. SQLite runtime 只串行化同一 store 的 connection open/close，避免 Python 3.13 WAL
   reusable-fd deadlock；查询/事务不被全局串行化；
4. 同步状态只把 source 当前 `active_generation_id` 的 building generation 视为活动构建，
   历史中断留下的非活动 building generation 不得把已发布的 110 个会话永久显示为“同步中”；
5. repository identity 对同一物理仓库保持稳定，同时返回 project binding scope、ref、commit、
   generation 和统计；总览与代码页不能把空列表静默呈现为“项目没有代码”；
6. subagent 的权威标记是 `metadata.source.subagent` 的对象或兼容 boolean，默认列表必须包含，
   类型筛选必须精确；
7. graph review 必须同时表达当前焦点、审阅路径、方向和证据详情；只有带真实
   `reviewCandidateId` 的唯一候选才能进入 mutation。

最终同一隔离数据根已切换到 `127.0.0.1:8000`：同步状态为
`ready / indexed_sessions_available`，浏览器桌面与 390px 窄屏均完成会话、仓库和图谱路径；
子任务筛选为 `5 / 110`，窄屏无横向 document overflow，页面日志为空。最终静态补充为
状态边界相关 `32` 项测试、`374` 个 Python 文件内存编译、Prettier 与 `git diff --check` PASS。

这次纠偏完成的是会话、仓库和图谱的真实项目可用性，不改变仓库 L0–L3 与生产发布边界。
正式库仍为 `EXTERNAL_MUTABLE_SERVICE_OWNED`，L4/L5、远程质量 replay、shadow/canary 和
默认 V2 继续 `EXTERNAL_DEFERRED / QUALITY_HOLD / NO_RELEASE`。
