# RAG 原始路线图实施矩阵

- 建立时间：2026-07-29
- 工作目录：`/Users/example/project/rag`
- 状态：`REPOSITORY_L0_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`
- 执行模式：`COORDINATED_MULTI_TASK / DIRTY_MAIN / NO_COMMIT / NO_PUSH`
- 完成判定：本文件优先于 `04_ACCELERATED_SINGLE_SESSION_WORKLOG.md` 中历史性的
  “最小闭环”“全套安全工程收口”等表述。
- 最终判定：以
  [最终独立 Gate](reviews/13_RAG_FINAL_COMPLETENESS_GATE_REVIEW.md) 的
  superseding section 为当前 authority；结论为
  `RAG REPOSITORY L0-L3 ENGINEERING COMPLETENESS GATE PASS`，`P0/P1/P2=0`。

## 1. 为什么重新打开

此前单会话加速阶段把“已有测试全绿、统一只读 V2 包装、显式 opt-in 主路存在”误写成了
“整套仓库内工程完成”。这不能证明原始设计里的来源专用 schema、持久化、Golden、
retriever/reranker、版本治理、隔离回填、联合评测和发布证据已经完成。

本矩阵恢复原始口径：

- `PROVEN`：设计项有对应实现、定向测试和可定位证据。
- `PARTIAL`：存在可复用实现，但没有覆盖原设计的完整合同。
- `MISSING`：原设计要求仍无实现或无独立验收。
- `EXTERNAL_DEFERRED`：只有生产数据、外部服务、正式库 owner 或发布授权才能完成；
  不是仓库开发缺口。
- `QUALITY_HOLD`：工程可以继续，但现有真实质量证据不允许宣称 qualified/default V2。

测试数量、模块存在和回归通过只能作为证据，不能自动把 `PARTIAL` 提升为 `PROVEN`。

## 2. 全局不可越界条件

| 条件 | 当前判定 | 证据/说明 |
|---|---|---|
| 共享 dirty main | `PROVEN` | 保留用户和既有任务修改；不 reset/checkout 覆盖 |
| 协调方式 | `PROVEN` | 主控维护共享过程记录；互斥文件锁的实现/审计任务可并行，最终由主控集成 |
| Git 发布动作 | `PROVEN` | 不 branch/worktree/commit/push |
| 正式库所有权 | `EXTERNAL_DEFERRED` | `var/evidence-rag.sqlite3` 为 `EXTERNAL_MUTABLE_SERVICE_OWNED` |
| 正式库验证 | `FORBIDDEN` | 不 open/hash/checkpoint/copy，不查看或删除 WAL/SHM |
| 默认引擎 | `PROVEN` | 默认继续 V1；新增路径必须显式 opt-in |
| 质量发布 | `QUALITY_HOLD` | Code release HOLD；Codex/Experiment 真实 control 均 NON_QUALIFIED |
| 研究工作台写回 | `EXTERNAL_DEFERRED` | 当前没有可调用的 research work item/write-back 工具；只维护本地证据 |

## 3. 已证明且不重做的基础

| 路线 | 状态 | 已证明内容 | 未包含内容 |
|---|---|---|---|
| Code C0–C7 | `PROVEN` | contracts、双轨存储、AST/SCIP/dense/graph/rerank/context、platform opt-in、release evaluator | production quality qualification、默认 V2 |
| Code release | `QUALITY_HOLD` | release evaluator 决策 `HOLD_DEFAULT_V1` | shadow/canary 真实生产质量放量 |
| Codex X0 | `PROVEN` | 45-case Golden、评测合同、X-B0 correction authority | 高质量 control |
| Codex X1 | `PROVEN` | observable event normalizer、state truth、output window、隔离 fact store | released treatment result；X-T1 在发布前失败 |
| Experiment E0 | `PROVEN` | 45-case Golden、真实 MLflow/manual fixture、稳定 definition/series/observation identity | E1+ 持久化和 treatment |
| Experiment E-B0 | `PROVEN / QUALITY_HOLD` | immutable 10-file artifact、portable verify、45-case 真实 V1 baseline | quality qualification；整体指标为 NON_QUALIFIED |

## 4. Codex 原始 X0–X5

| 里程碑 | 当前状态 | 已有证据 | 仍需完成 |
|---|---|---|---|
| X0 Foundation/Baseline | `PROVEN / QUALITY_HOLD` | `sources/codex/evaluation_v1.py`、`baseline_v1.py`、Golden/correction artifact | 无 |
| X1 Observable Events | `PROVEN` | `contracts.py`、`event_normalizer.py`、`state_machine.py`、`facts_v1.py` | 无 |
| CX2-01 Goal-aware Episode | `PROVEN (engineering)` | `sources/codex/episode_v2.py`：goal/temporal boundary、failure/recovery、跨 turn membership、manual override、content-addressed version | production treatment quality |
| CX2-02 Retrieval Unit Store | `PROVEN (engineering)` | `units_v2.py`、`store_v2.py`：episode/member/unit/edge、isolated additive SQLite、atomic publish/rollback/idempotency | 正式库 migration/backfill |
| CX3-01 Exact/Sparse | `PROVEN (engineering)` | `retrieval_v2.py`：task profile、fielded exact/sparse、独立 channel trace、scope/generation gate | production treatment quality |
| CX3-02 Dense Profiles | `PROVEN (engineering)` | versioned local dense profiles、content-addressed isolated cache、profile/generation rollback；remote provider 默认 unavailable | 远程模型 benchmark/quality |
| CX3-03 Temporal/Event Graph | `PROVEN (engineering)` | typed temporal/event graph candidates、bounded traversal、uncertainty与 negative reasons | production treatment quality |
| CX3-04 Rerank/Calibration | `PROVEN (engineering)` | deterministic feature rerank、reviewed calibration artifact、adaptive-k、channel ablation tests | production treatment artifact |
| CX4 Timeline Context | `PROVEN (engineering)` | `context_v2.py`：task templates、timeline/comparison、stable citation、missing contract、reasoning exclusion | production quality |
| CX5 Append/Privacy/Release | `PROVEN (engineering) / HOLD` | `governance_v2.py`：append cursor、privacy tombstone、shadow aggregator、stable routing、six-stage release evaluator/rollback；default V1 | production shadow/canary/default switch |
| Codex treatment | `EXTERNAL_DEFERRED / QUALITY_HOLD` | X-T1 `FAILED_BEFORE_PUBLISH / NO_RESULT` | 未授权重试；不能伪造质量 |

## 5. Experiment 原始 E0–E5

| 里程碑 | 当前状态 | 已有证据 | 仍需完成 |
|---|---|---|---|
| E0 Foundation | `PROVEN` | `sources/experiment/{fixture,evaluation}_v1.py`、45-case Golden | 无 |
| E-B0 | `PROVEN / QUALITY_HOLD` | published immutable artifact；verify-only 通过 | NON_QUALIFIED |
| E1-01 Snapshot/Schema | `PROVEN (engineering)` | `contracts_v2.py`、`store_v2.py`：content-addressed snapshot、isolated additive schema/ledger、generation publish/fail/rollback | 正式库 migration/backfill |
| E1-02 Metric Registry | `PROVEN (engineering)` | definition/alias/direction 三态、observation history、unit normalization、generation/version persistence | production history sync quality |
| E1-03 Dataset/Config/Env/Group | `PROVEN (engineering)` | typed dataset/config/environment/artifact/RunGroup entities、links 与 isolated store | production treatment quality |
| E2 Typed Query | `PROVEN (engineering)` | `query_v2.py`：typed AST/parser、allowlisted operators、parameterized SQL、scope-first in-memory evaluator、numeric/unit truth | production treatment artifact |
| E3 Compare/Aggregate/Reproduce | `PROVEN (engineering)` | `analysis_v2.py`：四态 comparability、deterministic mean/median/std/CI、七类 reproduction roles、strict comparison | production quality |
| E4 Semantic Surface | `PROVEN (engineering)` | `semantic_v2.py`：versioned surfaces、exact/structured pinned、semantic tail、negative features、task context | remote dense/reranker benchmark |
| E5 Sync/Security/Release | `PROVEN (engineering) / HOLD` | `pipeline_v2.py`、`governance_v2.py`：atomic generation、path/SSRF、trace、stable routing、shadow/canary evaluator、rollback；default V1 | production backfill/shadow/canary/default switch |

## 6. Notebook 原始 N0–N6

早期 `rag/notebook_v2.py` 只是一层可复用只读原型；它已由
`rag/sources/notebook/` 下的 N0–N6 typed contracts、isolated store、retrieval、
context、evaluation 与 release 实现补全，不能再作为当前完成度判断。

| 里程碑 | 当前状态 | 已有证据 | 仍需完成 |
|---|---|---|---|
| N0 样例/Golden | `PROVEN` | released 40-case content-addressed Golden、8 slices `5/6/5/6/6/5/4/3`、4-publication programmatic fixture、100-entity authority、冻结 denominator/三态 evaluator；package `sha256:49b52b…fecb3` | production quality qualification |
| N1 Revision/Execution/Cell | `PROVEN` | `sources/notebook/{contracts,adapter_v2,schema,store}.py`；2 templates/3 revisions/4 executions/26 cell versions/37 cell executions；isolated atomic/idempotent store；ACL/generation identity、tombstone hide-without-delete | 正式库 migration/backfill；默认 V1 不变 |
| N2 Parameter/Output/Error Parser | `PROVEN` | 独立 typed parameter/output/execution-state modules；display/exec order、gap/duplicate/restart/stale；HTML/JS/secret/path/binary fail-safe；metric 固定 unconfirmed | production treatment quality |
| N3 Code/Dependency Graph | `PROVEN` | versioned symbols、def-use、redefinition、external dependency hash、magic/dynamic/unsupported uncertainty；结构边覆盖 revision/execution/cell/artifact/parameter ownership 和 def-use | production treatment quality |
| N4 Retriever/Reranker | `PROVEN / QUALITY_HOLD` | 独立 exact/structured/sparse/graph candidates、task query profile、adaptive limits、source-local deterministic reranker；dense 明确 `UNAVAILABLE`；40-case integrated baseline recall `35/38` | dense profile/remote embedding 需要独立成本与授权；现有质量未达发布门 |
| N5 Compare/Context | `PROVEN / QUALITY_HOLD` | native/stable/hash/similarity/ordinal fail-closed cell matcher、完整 change taxonomy、parameter/output comparison；8 类 task-specific context、stable citation、预算、缺证据三态 | matcher `2/4`、reproduction `0/1`，需后续质量优化 |
| N6 Security/Release | `PROVEN (engineering) / HOLD (release)` | isolated publish/rollback/idempotency、ACL/generation/tombstone、10-file portable verify-only baseline artifact、p50/p95、六阶段 no-skip release evaluator；guardrail 0 | production shadow/canary/default V2 外部阻塞；当前 `HOLD_DEFAULT_V1` |

Notebook 顺序：`N0/N1 → N2/N3 → N4 → N5 → N6`。正式库 migration 不做，但对应
schema/store 必须先在 isolated SQLite 中实现和验证，不能再以“正式库不可写”为理由省略。

## 7. Document 原始 D0–D6

| 里程碑 | 当前状态 | 已有证据 | 仍需完成 |
|---|---|---|---|
| D0 样本/基线 | `PROVEN / QUALITY_HOLD` | released 50-case Golden、8 slices `8/8/10/10/5/4/3/2`、24 hard-negative cases、2 refusal cases；package `sha256:a0e3eb…cf6d0`；唯一 portable baseline `evaluation-run://project-document-db0-v1/b6c5c5e8f21446d5a0038d23ffed5acf` | production quality qualification |
| D1 Hierarchical Units | `PROVEN` | `sources/document/{contracts,adapter_v2,schema,store}.py`；family/version/page/layout/section/summary/paragraph/claim/table/figure/formula/reference/citation；175 entities/206 edges；isolated atomic/idempotent store | 正式库 migration/backfill |
| D2 Dense/Reranker | `PROVEN / QUALITY_HOLD` | exact/sparse/table/graph/local-hash dense、task profile、source-local fusion/rerank、stable trace；recall `49/50`、nDCG `.8581`、MRR `.8333` | remote dense/production treatment 为外部成本；当前质量不允许默认 V2 |
| D3 Table Fact | `PROVEN` | row/header path、typed center/spread/unit、row footnote、stable cell identity、table channel 与 10/10 table/numeric evaluation | production treatment quality |
| D4 Claim | `PROVEN / QUALITY_HOLD` | candidate 与 reviewed accepted/rejected 分层、L0–L4 evidence checks、support/refute/qualify、candidate penalty；unsupported rate 0 | claim validation `9/10=.9` 低于 `.95` release threshold |
| D5 PDF/Figure/Citation | `PROVEN` | layout/OCR fallback、formula/figure/reference/citation resolver、HTML/script/binary/secret/absolute/UNC security；figure `5/5`、citation `4/4` | production parser diversity/quality |
| D6 Version/Release | `PROVEN (engineering) / HOLD (release)` | same-family alignment/change taxonomy/staleness、generation/ACL/tombstone publish/rollback、10-file portable artifact、p50/p95、六阶段 no-skip release evaluator | production shadow/canary/default V2 外部阻塞；当前 `HOLD_DEFAULT_V1` |

## 8. Workspace 原始 W0–W6

| 里程碑 | 当前状态 | 已有证据 | 仍需完成 |
|---|---|---|---|
| W0 样本/基线 | `PROVEN / QUALITY_HOLD` | released 40-case Golden、8 slices `5/6/6/6/6/5/3/3`、7 hard-negative cases；package `sha256:f1849d…be4b5`；唯一 portable baseline `evaluation-run://project-workspace-wb0-v1/363181f5d05847a0aa98fbbee83c4373` | production observation/quality qualification |
| W1 Typed Current Views | `PROVEN` | `sources/workspace/{contracts,fixture_v1,schema,store}.py`；111 typed entities/141 edges/111 units；scope/ACL/generation/current/history、atomic/idempotent isolated store | 正式库 migration/backfill |
| W2 State/Temporal | `PROVEN` | legal transition matrices、optimistic concurrency、done/reopen/restore gates、deterministic as-of projection、timezone overdue、content-addressed snapshots | production treatment quality |
| W3 Planning Graph | `PROVEN` | typed dependencies/blockers/risks/acceptance/checks/decisions/outcomes、bounded dependency path、done gate | production treatment quality |
| W4 Evidence Coverage | `PROVEN` | requirement role/source/min-count/independence/freshness/review/pinned generation/ACL policy，覆盖与 missing/stale/rejected/unauthorized 分离 | production cross-source observation |
| W5 Decision/Intelligence | `PROVEN` | authority A–E 分层、policy/input lineage、persistent/expiring IntelligenceRun、readiness/blocker/next-action explanations、authoritative mutation=false | production observation |
| W6 Semantic/Context/Governance | `PROVEN (engineering) / HOLD (release)` | exact/structured/sparse/local-hash dense/bounded graph/task rerank、stable citation/budget/tri-state context、tombstone/rollback、安全与 10-file portable verifier；hard-negative `38/40`、MRR `.8848`、nDCG `.8982` | production shadow/canary/default V2 外部阻塞；当前 `HOLD_DEFAULT_V1` |

## 9. 多源与整体 RAG M5–M7

| 里程碑 | 当前状态 | 已有证据 | 仍需完成 |
|---|---|---|---|
| M5-A Planner/Calibration | `PROVEN (engineering) / QUALITY UNAVAILABLE` | `multisource_foundation_v2.py`：六源 capability/watermark registry、content-addressed exact 60-case Golden、deterministic plan/subquestion/budget、reviewed calibration ECE/Brier | 真实 60-case replay/qualification |
| M5-B Role-aware Fusion | `PROVEN (engineering) / QUALITY UNAVAILABLE` | independent calibrated score、required-role marginal utility、root provenance dedup、counter channel、timeout/partial/unauthorized/not-indexed/no-match 七态、adaptive budget | 真实 M-B1～M-B4 消融 |
| M5-C Evidence Graph | `PROVEN (engineering) / QUALITY UNAVAILABLE` | `evidence_graph_v2.py`：typed registry/edge、bounded beam ≤4 hop、ACL/version/type/review/cycle/budget gate、counter/corrective trace | 真实 path/counter evaluation |
| M6 Context/Generation | `PROVEN (engineering) / QUALITY UNAVAILABLE` | `answer_v2.py`：fact normalization、complexity-aware packer、retrieval/comprehension 分离、claim-citation deterministic verifier、grounded refusal、unanswerable taxonomy | LLM/Judge 与真实 answer quality |
| M7 Performance | `PROVEN (engineering) / EXTERNAL BENCHMARK HOLD` | `performance_v2.py` + `performance_runtime_v2.py`：Runtime-owned exact generation baseline、content-addressed embedding cache、ACL-scoped six-layer cache、有界脱敏 span、P50/P95/P99/cost dashboard；production authority/attestation 固定 runner/component/dataset/membership/artifact 与五类诚实分母，ANN admission 缺证明即 unavailable | 真实 ANN/backend/model/hardware benchmark 与 production observation |
| M7 Security | `PROVEN (engineering)` | `global_governance_v2.py`：六来源 72-case injection/ACL/secret/path/UNC/control matrix、full-content multidecode scan、sanitized trace、content-addressed report | production attack observation |
| M7 Release | `PROVEN (engineering) / QUALITY_HOLD` | 八阶段 no-skip evidence、stable default-V1/explicit opt-in/shadow/canary routing、request fallback；七步 isolated rollback rehearsal 已映射为七个 runtime kill switches；`release_admission_v2.py` 固定六来源 current authority 与显式 artifact allowlist，`multisource_release_package_v2.py` 提供 exact-files portable verify-only outer package；禁止 whole-`evals` 扫描，当前只能 `NON_QUALIFIED / DEFAULT_V1` | production shadow/canary、60-case qualified evidence、default V2 |

联合主路由 `multisource_pipeline_v2.py` 执行 plan → governed source batch →
source-local calibration → role fusion → typed graph → 最多两轮 corrective retrieval →
EvidencePack → claim/citation verification；source batch 绑定 plan/scope/index generation/watermark，
危险内容在校准、融合与 pack 前过滤，默认 V1 分支不会调用 V2 retriever。

`multisource_evaluation_v2.py` 冻结 exact 60 membership 和逐 slice denominator，直接从
plan/fusion/traversal/pack/answer 重算 routing、role、entity、path、counter、version、
citation、unanswerable、timeout 与 leakage；当前没有真实 replay，报告必须
`UNAVAILABLE / NOT_QUALIFIED`。

## 10. 外部 deferred 与仓库内完成边界

最终 Gate 已确认仓库内 L0–L3 实现队列完成，没有剩余可实现项。只有以下内容可标
`EXTERNAL_DEFERRED`，且它们不是仓库开发缺口：

1. `EXTERNAL_MUTABLE_SERVICE_OWNED` 正式库的 migration/backfill；正式库、WAL/SHM
   sidecars 以及任何 checkpoint/open/hash/观察继续禁止；
2. L4 production-like replay、人工 truth、可信身份/ACL 映射和 reviewed quality
   qualification；
3. L4/L5 所需的生产模型、ANN、硬件、远程 embedding/LLM/service credential 与性能证据；
4. L5 shadow/canary/production observation、获准 rollback 演练、默认 V2 切换与发布授权。

以下内容曾是仓库内未完成工程，现已全部由本矩阵第 4–9 节的实现与隔离测试覆盖；它们
不能再被列为剩余项：

- isolated SQLite schema/store/publish/rollback；
- deterministic fixtures/Golden/evaluator；
- exact/sparse/typed retriever、dependency/temporal graph；
- context/citation/locator/ACL/security contracts；
- portable verify-only artifacts；
- synthetic/smoke/performance/security tests；
- 默认 V1 下的显式 opt-in 兼容接线。

因此，仓库当前没有 L0–L3 待开发队列；后续队列只有上面的外部 owner/resource
`DEFERRED`。若未来发现新的仓库内合同缺口，必须重新把对应里程碑降级为 `PARTIAL`，
不能用本次完成结论自动豁免。

## 11. 已完成执行顺序与最终证据

1. `COMPLETE`：Notebook N0–N6、Document D0–D6、Workspace W0–W6、Codex X2–X5、
   Experiment E1–E5 与 M5–M7 的仓库内 contracts/store/query/retrieval/context/
   governance/pipeline/runtime/API/frontend 接线均已完成并由最终 Gate 覆盖。
2. `COMPLETE`：Notebook typed tasks 的历史 marker 已删除，真实 enum 已贯穿模型、服务、
   API 与交互主路；任务 `019fb7d2-52d3-7eb1-9a3f-11aa65d9b506`。
3. `COMPLETE`：最终只读 drift audit 已完成；任务
   `019fb7f2-45f8-7d10-8b4a-e3b77ce10970`。
4. `COMPLETE`：release authority 修复、Codex authority 复用与 Experiment authority
   复用完成；任务分别为 `019fb7fa-2e97-7131-ade8-2a7ce4d7521d`、
   `019faa29-b37b-7360-8dcc-a57607ae9a17`、
   `019fb7a3-e322-7d72-a03d-32509aaf49fc`。
5. `COMPLETE`：当前 authority 为 Codex v3 `4e63852f…39e9`；历史 v1、Run 与
   correction authority 冻结。Experiment Foundation v3 `26363ae0…211a7`、parity
   `5c605c94…8764`、E-B0 v4 `2c82f1d2…d891`；历史 authority 冻结。production
   registry v3 component-set `ee64f94d…f28d`、content `43f3fcfe…fba6`，
   `all_current=True`。
6. `COMPLETE`：安全 backend `1833/1833` 通过四文件分片验证。补充的单进程运行在约
   90% 前无断言失败，随后卡在 Python 3.13 线程退出钩子；该运行不被伪写为单进程全绿，
   完整通过结论来自四个明确分片。
7. `COMPLETE`：299 个 changed Python 文件 Ruff、format、diff 与 in-memory compile
   全部通过；static/compile 通过。
8. `COMPLETE`：frontend `95` 项测试、typecheck 与 production build 通过；build SHA
   `ce9d7c…b6e2`。
9. `COMPLETE`：最终独立 Gate 任务 `019fb7a0-ac28-7ac1-9235-1168d6b6b142` 完成
   `115/115` 关键路径、六来源 `all_current`、`P0/P1/P2=0`，并在
   [review 13](reviews/13_RAG_FINAL_COMPLETENESS_GATE_REVIEW.md) 给出 superseding
   `RAG REPOSITORY L0-L3 ENGINEERING COMPLETENESS GATE PASS`。
10. `EXTERNAL_DEFERRED`：L4/L5 production replay、shadow/canary、production
    observation/rollback、默认 V2、正式库与外部服务；这不是仓库 L0–L3 开发缺口。

## 12. 质量与发布真值保持

仓库工程 Gate 通过不等于 release 或 quality qualified。以下负质量证据继续有效，未被
删除、重签或推断为成功：

- Codex X-T1 仍是 `FAILED_BEFORE_PUBLISH / NO_RESULT`；Codex current control 仍
  `NON_QUALIFIED`；
- Experiment E-B0 以及 current control 仍 `NON_QUALIFIED`；
- Notebook N6、Document D0–D6、Workspace W0–W6 的既有 portable baseline 仍为
  `VERIFIED_NON_QUALIFIED`；
- M5/M6 真实联合 replay 仍 `UNAVAILABLE / NOT_QUALIFIED`，M7 release 仍
  `NON_QUALIFIED / DEFAULT_V1`；
- Code release 继续 `HOLD_DEFAULT_V1`；全局状态继续
  `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`。

因此，整体 RAG 的**发布完成定义仍未满足**；不得从仓库 L0–L3 工程完成推导 L4/L5、
quality qualification、release candidate 或默认 V2。

## 13. 2026-07-31 真实启动验收

[最终工程完整性 Gate](reviews/13_RAG_FINAL_COMPLETENESS_GATE_REVIEW.md) 继续是路线图
L0–L3 完成度 authority；[独立 Live Startup Gate](reviews/14_RAG_LIVE_STARTUP_GATE_REVIEW.md)
补足了此前只由合同、pytest 与 ASGI 测试证明、但没有在本文档中记录的真实进程运行证据。
它不覆盖或重签第 12 节的质量与发布真值。

主控与独立 Gate 分别使用新的 system-temp 数据、Codex 与 allowed roots 启动真实后端；
production Web bundle 由后端直接提供。实际验证包括：

- health、117-path OpenAPI、release status、production index 与 hashed assets 均可经 HTTP
  访问；production token/ACL 入口保持 fail-closed；
- Code、Codex、Experiment、Notebook、Document、Workspace 六来源通过公开 HTTP 入口摄取，
  并在同一次全局检索与 `/v1/query` 中全部出现；
- 默认请求继续执行 V1；显式全源 V2 在缺少 reviewed release authority 时诚实记录
  `requested=v2 / selected=v1 / served=v1`，不得写成 V2 已发布；
- 真实浏览器覆盖 desktop、mobile、项目总览、可信查询、运维状态与六来源引擎 override；
  页面无 console warning/error，也无 mobile 横向溢出；
- Live QA 发现并关闭一个真实前端展示缺口：legacy source trace 的
  `engine_requested=v1` 曾遮蔽 governed V2 请求。当前类型合同和呈现逻辑优先使用
  `trace.platform.source_engine_routing`，缺席时使用 `trace.sources[*].release_route`；
  platform-only 与 release-route-only 回归已拆分，独立前端复核 `P0/P1/P2=0`；
- 后端与前端开发进程、独立 production Gate 进程均收到一次有序停止信号，完成 application
  shutdown，最终相关端口无监听者；这证明本地 L3 生命周期，不冒充 production rollback
  drill。

当前静止源码的补充验收证据为：

| 验证 | 当前结果 |
|---|---|
| 经 Review 13 定义的 safe backend selection | `1823/1823 PASS`；排除正式库 observer 文件与两个 formal-path node |
| Frontend Vitest | `22 files / 97 tests PASS` |
| Frontend TypeScript | typecheck PASS |
| Production build | isolated 与 `web/` 两份 contract 逐字节一致；`buildSha256=21b9f585a893f4920ad1f7c29baa30d1f0d679e145e73527f60cc1d57a62a34e` |
| Ruff | `src tests` PASS |
| Python in-memory compile | `369 files PASS` |
| Independent live Gate | `RAG LIVE STARTUP ACCEPTANCE GATE PASS / P0=0 / P1=0` |

本轮所有运行数据库均位于隔离临时根；正式库及 sidecars 未访问，也不声明它们不变。
最终姿态仍是：

`REPOSITORY_L0_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

`L4/L5: EXTERNAL_DEFERRED`
