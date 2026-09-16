# G7 容量、可靠性、安全、恢复与可观测性可执行规格

版本：2026-08-06 V1  
状态：`G7D DESIGN_ENGINEERING_PASS / G6_GATE_HOLD / REAL_LOAD_AND_RECOVERY_EVIDENCE_MISSING / BENCHMARK_NOT_STARTED`  
上位目标：`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`  
质量前置：`39_G6_REAL_MULTISOURCE_WIKI_ANSWER_QUALITY_EXECUTION_SPEC.md`  
默认发布状态：`DEFAULT_V1 / OPERATIONS_HOLD`

## 0. 本规格解决什么问题

仓库已有exact in-memory vector baseline、content-addressed embedding cache、ACL/generation scoped cache、
bounded sanitized spans、P50/P95/P99 dashboard、ANN evidence authority/attestation、Wiki S级capacity runner、
72-case六源security matrix和静态七步rollback rehearsal。这些资产很好地证明了工程合同、可移植验证和
fail-closed边界，但没有证明真实S/M/L数据量、真实索引/模型/硬件、并发争用、长时稳定性、外部依赖
退化、恢复时限或生产观测。

本规格冻结G7唯一目标：在G6 exact candidate上，用owner-authorized、脱敏、规模可核验的production-mirror
工作负载，建立六源→融合→Wiki→raw read→答案全链容量基线；执行并发、峰值、soak、chaos、安全、备份/
恢复/re-index/tombstone传播演练；让每个SLO、RTO、RPO、error budget和alert都能由原始sample重算。

本轮只完成代码/artifact只读审计和实施规格；没有连接正式服务、没有产生真实负载、没有注入正式故障、
没有执行恢复、没有改变任何route或发布状态。

## 1. Entry、Exit 与责任边界

### 1.1 G7 runtime Entry

1. G0–G6 exact decision全部`QUALIFIED`且仍有效；
2. production-mirror环境、硬件、依赖、数据授权和负载回放授权已签署；
3. candidate、active generation set、模型、配置、预算与G6完全绑定；
4. benchmark operator、fault commander、security tester、recovery owner、observability owner和reviewer分离；
5. SLO/SLA假设、S/M/L规模、并发曲线、warm/cold cache、sample floors、RTO/RPO、停止阈值预注册；
6. 正式生产数据库和流量不在G7默认范围；任何production-like资源有隔离边界与cost cap；
7. fault注入target、blast radius、automatic abort和cleanup验证已批准；
8. 监控、trace、clock sync、resource metrics在负载前验证可用。

### 1.2 G7 `QUALIFIED`

G7 PASS要求以下六面同时合格：

- capacity：S/M/L与峰值负载下质量guard、latency、throughput、resource和cost过门；
- reliability：timeout、partial、crash、corruption、dependency loss均诚实降级且不破坏authority；
- concurrency：reader/publish/delete/rollback/re-index并发没有混代、脏读、复活或重复副作用；
- security：真实攻击语料和端到端执行路径零ACL/secret/path/tool/prompt泄漏；
- recovery：backup/restore/re-index/tombstone/rollback达到RTO/RPO并可证明数据守恒；
- observability：SLI从可用trace/metric/log重算，告警及时、低泄漏、可指向runbook。

G7 PASS只允许进入G8受控replay/shadow准备，不授权真实流量切换。

## 2. 当前工程事实

### 2.1 性能/runtime

1. `performance_runtime_v2.py`自述`bounded, in-memory`，exact index、embedding cache、scoped cache均进程内；
2. span buffer默认最多2048条，dashboard只发布allowlisted聚合字段；
3. hardware profile允许`repository-runtime-unqualified`与`production-attested`，当前仓库运行属于前者；
4. performance evidence verifier要求trusted authority、attestation、dataset/query membership、五类sample
   denominator和`production_observation=true`；缺失时正确返回UNAVAILABLE；
5. ANN admission只在active vector count或dense P95触发条件后考虑，并要求recall@k≥0.95且P95优于dense；
6. 仓库没有真实ANN/backend/model/hardware qualification artifact。

### 2.2 Wiki S级容量artifact

当前`wiki-s-capacity-20260803-v4`：

- 362 pages、950 source refs、SQLite约48.3 MB；
- warmup=1、每operation measured iteration=5；
- path/directory/search/navigation/deep-navigation/compile/publish/rollback均报告P50/P95/P99；
- compile/publish/rollback P95约267.92/169.02/228.92 ms；
- artifact为portable且security scan通过；
- 结论明确是`ENGINEERING_S_CAPACITY_EVIDENCE`且`production_authorized=false`。

五次单进程S fixture不能估计稳定尾延迟、并发吞吐、M/L数据规模、线上硬件或故障恢复。

### 2.3 安全与rollback

1. `global_governance_v2.py`源码构造72 case，每源12条；覆盖prompt injection、secret、POSIX/Windows/
   UNC path、ACL、control char和safe control；
2. 默认evaluation origin为`SYNTHETIC_TEST_ONLY`，即使72/72通过也`production_authorized=false`；
3. 扫描器可做NFKC和最多5轮URL decode，但规则集不代表完整对抗面；
4. rollback rehearsal固定七个component和动作，但`evidence_origin=STATIC_TEST_ONLY`、side effect=0；
5. legacy global evaluator始终加入`canonical_release_authority_required`，不能promotion，边界正确；
6. 仓库没有真实fault injection、进程/机器故障、backup restore或线上rollback计时证据。

## 3. 差距与处置清单

| ID | 当前事实 | 风险 | G7处置 |
|---|---|---|---|
| OR-01 | exact vector/index/cache在内存 | 重启丢失，无法外推生产backend | 真实backend qualification；exact保留oracle |
| OR-02 | span buffer bounded且进程内 | 长窗/多实例样本缺失 | 外部telemetry sink+delivery/retention contract |
| OR-03 | S Wiki仅362页/950 refs | 规模不足 | owner-authorized S/M/L profiles |
| OR-04 | 每operation仅5样本 | P95/P99统计无意义 | 预注册最低样本/持续窗口 |
| OR-05 | 单进程串行 | 无并发/排队/锁争用 | open/closed model并发曲线 |
| OR-06 | 硬件信息只有OS/arch/Python | 无CPU/RAM/disk/backend/topology | immutable environment bill |
| OR-07 | throughput由单次duration推算 | 不是真实steady-state throughput | wall-clock completed/error/rate accounting |
| OR-08 | cache状态不完整 | warm result可掩盖cold start | cold/warm/mixed各自Gate |
| OR-09 | 无质量-性能联合guard | 快速但召回下降可能过门 | G6 locked subset在线recheck |
| OR-10 | 无负载shape | 平均QPS掩盖burst/大query | workload class与arrival distribution |
| OR-11 | 无soak | 内存/句柄/cache/DB膨胀不可见 | ≥24h mirror soak或owner预注册窗口 |
| OR-12 | 无资源/成本预算 | 质量提升可能不可运营 | CPU/RAM/disk/network/token/cost guard |
| OR-13 | 72 security case为规则自产自测 | 同检测器生成truth | 独立red-team corpus与真实execution path |
| OR-14 | 安全类别较窄 | 编码/多模态/indirect/tool链攻击缺失 | threat model扩展与mutation corpus |
| OR-15 | 静态rollback无runtime side effect | 不证明可恢复 | 实际隔离环境kill switch/restore rehearsal |
| OR-16 | 无RTO/RPO | 恢复“成功”无法量化 | per-scenario RTO/RPO/data loss floor |
| OR-17 | 无backup authority | restore可来自错误/过期备份 | backup catalog/hash/encryption/restore point |
| OR-18 | 无并发generation一致性压力 | reader可能混代 | pinned generation-set consistency tests |
| OR-19 | 删除与cache/export传播未计时 | 撤权后残留可见 | deletion SLO/end-to-end residue scan |
| OR-20 | fault taxonomy未绑定真实dependency | 异常可能被算普通zero result | typed dependency/fault/outcome matrix |
| OR-21 | retry/circuit breaker未负载验证 | 重试风暴/级联故障 | retry budget、backoff、breaker chaos |
| OR-22 | telemetry可能高基数或泄密 | 观测系统成为数据出口 | cardinality/privacy budgets与scan |
| OR-23 | dashboard没有SLO burn-rate | 无法触发及时行动 | multi-window burn alerts |
| OR-24 | alarm未演练 | 指标有但无人响应 | alert→page→ack→mitigate drill |
| OR-25 | artifact分散 | 无统一capacity/recovery authority | `OperationalQualificationPackageV1` |
| OR-26 | G7结果可能被用于直接发布 | 跳过真实shadow | decision仅允许G8 Entry，不改变route |

## 4. 不可变原则

1. 所有benchmark绑定exact G6 candidate和active generation set。
2. production-mirror不等于production observation，必须明确environment class。
3. benchmark corpus membership、query membership、scale与硬件必须可核验。
4. S/M/L按raw/entity/unit/vector/page/edge/bytes多维定义，不能只看一个计数。
5. 性能与质量同测；任何quality guard下降会使性能run无资格。
6. cold、warm、mixed cache分开报告，禁止选择最好状态。
7. P95/P99必须来自足量独立sample和稳定窗口，不能由5次调用宣称。
8. latency报告client-observed端到端和server stage breakdown。
9. timeout/error/cancel/retry不从latency denominator消失。
10. throughput按实际完成、错误、丢弃、排队和backpressure共同报告。
11. benchmark启动、warmup、测量、cooldown和cleanup边界固定。
12. 每个run独占或记录资源干扰；环境变化即新run。
13. fault注入必须有批准target、blast radius、abort threshold和恢复确认。
14. chaos失败不能被自动重跑隐藏；所有attempt保留。
15. reader在请求开始pin exact generation set，整个请求不可混代。
16. publish/rollback/delete并发不得复活tombstone或返回跨代组合。
17. backup必须加密、校验、catalogued且可在隔离环境恢复。
18. RPO按已确认写入与恢复后状态差计算；RTO从触发到服务/质量恢复计算。
19. re-index必须从authoritative raw重新产生，不从可能损坏的derived副本复制。
20. security test贯穿ingest、index、cache、retrieval、Wiki、raw、prompt、answer、trace和artifact。
21. 安全负例也要测false positive，不能通过全拦截取得零泄漏。
22. telemetry只含不可逆ID、allowlisted维度和必要聚合，不记录raw query/evidence/answer。
23. SLI、SLO、alert、runbook action和owner一一对应。
24. G7 reviewer独立复算sample、percentile、error budget、RTO/RPO和Gate。

## 5. S/M/L 规模合同

### 5.1 `OperationalScaleProfileV1`

```text
profile_id / tier(S|M|L) / revision
authorization_sha256 / corpus_manifest_sha256
active_generation_set_sha256
per_source
  root_count / entity_count / unit_count
  raw_bytes / index_bytes / vector_count
wiki
  page_count / fact_count / edge_count / source_ref_count / database_bytes
workload
  query_class_counts / source_route_counts / complexity_counts
  input_token_bins / expected_output_token_bins
content_sha256
```

### 5.2 规模目标

真实数值由owner依据计划容量冻结；没有owner数据前只冻结相对关系：

- S：一个最小但完整项目，六源均非空、所有关键query class可执行；
- M：计划典型项目P50–P75规模，覆盖常态并发与增量publish；
- L：计划近期容量上界或P95项目，覆盖高fan-in、长导航、大artifact和版本历史；
- 超L stress：只用于找拐点/保护阈值，不作为承诺容量。

profile必须在测试前给出exact counts，禁止测试后把失败规模重新标为“超范围”。

## 6. Workload 与负载模型

### 6.1 `OperationalWorkloadManifestV1`

至少冻结：query membership、arrival model、target QPS/concurrency、burst、duration、think time、query mix、source
mix、as-of/ACL mix、answerability、cache state、publish/delete background rate、generation size、model endpoint、retry/
timeout/budget和seed。

### 6.2 测试类型

| 类型 | 目的 | 必须报告 |
|---|---|---|
| single-user baseline | stage最低开销 | cold/warm P50/P95/P99 |
| step load | 容量曲线/拐点 | QPS、queue、errors、resource、quality |
| spike | 突发保护 | shed/backpressure/recovery time |
| sustained | 稳态SLO | 至少预注册窗口完整样本 |
| soak | 泄漏/膨胀/漂移 | heap/RSS/fd/disk/cache/latency趋势 |
| background mutation | query+publish/delete | consistency与deletion SLO |
| stress | 保护边界 | graceful degradation，不用于SLO宣传 |

open model和closed model分别报告，避免把服务变慢后“发送更少请求”误当稳定。

## 7. SLI/SLO 与质量guard

### 7.1 核心SLI

- availability、goodput、error/timeout/cancel/retry/shed rate；
- end-to-end与planner/source/fusion/graph/Wiki/raw/generation P50/P95/P99/max；
- queue wait、source fan-out、search/read/hop/token counts；
- CPU/RSS/heap/fd/thread、disk IOPS/bytes、network、DB locks、cache hit/eviction；
- index build/publish/delete/rollback/restore/re-index duration；
- cost/query、tokens/query、embedding calls、external dependency calls；
- G6 locked quality subset的route/entity/path/raw/citation/unsupported/refusal guard。

### 7.2 统计规则

1. percentile对成功与all-attempt两套分布报告；错误不从availability中消失；
2. 至少发布sample count、window、clock、histogram boundaries和client/server偏差；
3. 每个scale×cache×query class×concurrency达到预注册floor；
4. CI或多个独立run覆盖环境噪声；
5. SLO threshold、error budget和burn rate在run前冻结；
6. 单点max不替代tail，平均值不替代P95/P99。

### 7.3 `PerformanceQualificationDecisionV1`

每个profile分别判定latency、throughput、resource、cost和quality guard。L失败不能用S PASS补偿；某query
class失败不能用global平均补偿。ANN只有在真实authority/attestation、exact query membership、recall oracle与
完整denominator下可启用，否则保持exact/dense已批准实现。

## 8. 并发一致性矩阵

必须覆盖：

1. 多reader同一active set；
2. reader开始后并发publish新generation set；
3. reader与source-local rebuild；
4. reader与tombstone/ACL revoke；
5. publish与delete/deletion backlog；
6. publish失败/partial source stage；
7. rollback与新删除watermark；
8. two publishers CAS竞争；
9. cache fill与invalidation；
10. Wiki compile/publish与long navigation；
11. backup snapshot与持续写入；
12. restore/re-index与read-only verification。

每条验证snapshot pin、one active set、no mixed generation、no dirty read、no double side effect、no resurrection、
bounded blocking和审计完整。

## 9. Fault/chaos 矩阵

### 9.1 故障域

- source adapter timeout/error/partial/malformed；
- embedding/model/reranker/generator unavailable、slow、rate limit；
- vector/keyword/graph/Wiki store latency、connection loss、corruption；
- disk full/read-only/IO error、memory pressure、CPU saturation；
- process crash/restart、worker loss、clock skew、network partition；
- stale calibration/config/generation、checksum mismatch；
- cache poisoning/stale entry/collision/eviction storm；
- publish failpoint、partial materialization、checkpoint replay；
- backup corruption/restore failure/re-index interruption；
- telemetry sink outage和alert delivery failure。

### 9.2 合格语义

每个fault都有expected typed outcome、retry budget、breaker transition、fallback policy、user-visible behavior、
authority effect、data integrity invariant、alert、RTO/RPO、cleanup和post-recovery quality check。返回零结果冒充
成功、无限重试、跨ACL缓存、静默混代或损坏后继续引用都直接FAIL。

## 10. Security qualification

### 10.1 威胁面

在现有72-case基础上增加独立来源的：多重编码/Unicode混淆、indirect prompt injection、跨文档指令拼接、
工具调用诱导、URL/SSRF、symlink/path traversal、archive bomb、parser bomb、formula/HTML/Markdown injection、
secret variants、PII、ACL inference、cache-key confusion、generation replay、citation spoofing、artifact/trace exfiltration。

### 10.2 执行路径

每个case必须实际经过适用的ingest→parse→index→cache→retrieve→fusion→Wiki→raw→prompt→answer→trace→
artifact路径，记录首次拦截点和后续副作用。只调用正则函数不构成端到端安全证明。

### 10.3 Gate

- unauthorized/secret/tool execution/data exfiltration=0；
- forbidden network/file/db mutation=0；
- safe-control false positive低于预注册floor且关键正常任务不被全拦；
- payload、query、evidence和secret不进入embedding/cache/trace/artifact；
- scanner/parser失败为typed quarantine，不是放行；
- security tester与规则实现者分离。

## 11. Backup、恢复、re-index 与 deletion 演练

### 11.1 `RecoveryScenarioV1`

```text
scenario_id / target_component / failure_type
pre_state_sha256 / active_generation_set_sha256
confirmed_write_watermark / deletion_watermark
backup_id / backup_sha256 / encryption_key_ref
fault_started_at / detected_at / recovery_started_at / recovered_at
expected_rto / actual_rto
expected_rpo / actual_rpo
post_state_sha256 / conservation_report_sha256
quality_recheck_sha256 / residual_scan_sha256
decision / content_sha256
```

### 11.2 必做演练

1. application DB backup/restore到隔离环境；
2. 六源derived store丢失后从raw+manifest重建；
3. vector/keyword/graph/Wiki单层损坏与全重建；
4. publish前/中/后crash恢复；
5. checkpoint resume与stale worker fencing；
6. cache全失效与cold recovery；
7. tombstone/ACL revoke全链传播；
8. rollback target早于deletion watermark时强制successor rebuild；
9. telemetry outage期间业务与审计行为；
10. restore后G6 locked smoke和membership/conservation复核。

## 12. 可观测性与告警

### 12.1 Trace contract

全链同一trace包含不可逆project/request/scope/plan/generation IDs、route reason、source statuses、stage latency、
cache状态、candidate/filtered counts、stop/fallback/error code和cost；禁止raw query、content、answer、locator、ACL
成员或secret。

### 12.2 Dashboard

至少包含：traffic、availability/error budget、latency、quality guard、source dependency、materialization/delete、
cache/index、Wiki navigation、model/token/cost、resource saturation、security quarantine、fallback/rollback和artifact
freshness。每张图固定dimension allowlist与cardinality cap。

### 12.3 Alert/runbook

每条alert绑定owner、severity、multi-window burn policy、dedup/suppression、dashboard、first checks、safe mitigation、
rollback trigger、communication和close criteria。通过synthetic alert drill记录detect→page→ack→diagnose→mitigate
时间；未触发、误路由或runbook无效均HOLD。

## 13. 22项原子实施序列

| # | Work package | 交付 | 完成证据 |
|---:|---|---|---|
| 1 | T7.0.1 | current performance/capacity/security audit | OR-01–26代码/artifact证据 |
| 2 | T7.0.2 | owner/SLO/fault/security role policy | Entry/SoD/stop签署 |
| 3 | T7.1.1 | S/M/L OperationalScaleProfile | exact multi-dimensional counts |
| 4 | T7.1.2 | query/workload membership | G6 quality subset+traffic shape |
| 5 | T7.1.3 | immutable environment bill | hardware/backend/model/network/locks |
| 6 | T7.2.1 | benchmark harness | open/closed load、sample completeness |
| 7 | T7.2.2 | cold/warm/mixed single-user baseline | stage distributions |
| 8 | T7.2.3 | step/spike/sustained load | capacity curve/backpressure |
| 9 | T7.2.4 | soak run | leak/drift/resource trends |
| 10 | T7.2.5 | quality/cost guards | locked G6 subset parity |
| 11 | T7.3.1 | concurrency harness | reader/publish/delete/rollback matrix |
| 12 | T7.3.2 | generation/cache consistency verifier | no mixed/stale/resurrection |
| 13 | T7.4.1 | fault injector + scenario catalog | bounded target/abort/cleanup |
| 14 | T7.4.2 | dependency/storage/process chaos | typed outcomes/RTO/RPO |
| 15 | T7.5.1 | independent security corpus | expanded threat/safe controls |
| 16 | T7.5.2 | end-to-end security execution | zero leakage/mutation |
| 17 | T7.6.1 | backup/restore drills | state conservation/RTO/RPO |
| 18 | T7.6.2 | re-index/delete/rollback drills | no resurrection/residue |
| 19 | T7.7.1 | trace/metric/dashboard implementation | SLI recompute/privacy/cardinality |
| 20 | T7.7.2 | alerts/runbooks/drills | detect/page/ack/mitigate receipts |
| 21 | T7.8.1 | operational qualification package | portable verify/checksums |
| 22 | T7.9.1 | independent review + G7 decision | exact QUALIFIED or HOLD/FAIL |

## 14. 验证矩阵（OR-01–OR-60）

### 14.1 Scale/workload/performance（OR-01–16）

| Case | Hard exit |
|---|---|
| OR-01 S/M/L exact membership/profile | PASS |
| OR-02 claimed scale与实际counts不符 | invalid |
| OR-03 environment/hardware/backend缺字段 | HOLD |
| OR-04 query membership与G6 locked set不符 | invalid |
| OR-05 cold/warm/mixed状态误标 | invalid |
| OR-06 sample低于预注册floor | UNAVAILABLE |
| OR-07 error/timeout从latency denominator删除 | report fail |
| OR-08 open/closed model混报 | report fail |
| OR-09 client/server clock或duration异常 | quarantine samples |
| OR-10 throughput不守恒 | invalid |
| OR-11 step load找到饱和点 | curve可复算 |
| OR-12 spike触发backpressure | 无级联故障 |
| OR-13 sustained窗口SLO | exact burn budget |
| OR-14 soak RSS/fd/disk持续增长 | HOLD |
| OR-15 latency过门但quality guard下降 | FAIL |
| OR-16 cost/resource超guard | HOLD |

### 14.2 Concurrency/fault（OR-17–32）

| Case | Hard exit |
|---|---|
| OR-17 reader并发publish | request只见一个set |
| OR-18 two publishers CAS | one wins, loser fenced |
| OR-19 publish与delete | deleted member不可见 |
| OR-20 rollback早于delete watermark | rejected/successor rebuild |
| OR-21 cache fill/invalidate race | no stale ACL/generation |
| OR-22 Wiki compile与long navigation | pinned snapshot |
| OR-23 source timeout | typed partial/refusal |
| OR-24 malformed/corrupt response | quarantined |
| OR-25 model/reranker rate limit | bounded retry/breaker |
| OR-26 disk full/read-only | no partial authority |
| OR-27 process crash during stage | resume or fail clean |
| OR-28 crash during active-set CAS | old or new, never mixed |
| OR-29 telemetry sink outage | bounded local behavior+alert |
| OR-30 retry storm | budget enforced |
| OR-31 fault abort threshold | automatic stop works |
| OR-32 post-fault G6 smoke | quality restored |

### 14.3 Security（OR-33–44）

| Case | Hard exit |
|---|---|
| OR-33 multiencoding/Unicode injection | blocked before unsafe use |
| OR-34 cross-document indirect injection | blocked/isolated |
| OR-35 tool/network/file诱导 | zero side effect |
| OR-36 SSRF/path/symlink/archive bomb | zero escape |
| OR-37 secret/PII variants | no embed/cache/trace/artifact |
| OR-38 ACL cache-key confusion | zero cross-scope hit |
| OR-39 generation replay/citation spoof | authority rejected |
| OR-40 parser/scanner failure | quarantine, not allow |
| OR-41 safe controls | false-positive floor PASS |
| OR-42 six-source coverage | each threat/source floor |
| OR-43 security tester role collision | HOLD |
| OR-44 production-mirror external side effect | immediate abort/incident |

### 14.4 Recovery/observability/artifact（OR-45–60）

| Case | Hard exit |
|---|---|
| OR-45 backup checksum/encryption/catalog | PASS |
| OR-46 restore exact confirmed watermark | RPO PASS |
| OR-47 restore within target | RTO PASS |
| OR-48 corrupt backup | rejected, next valid selected |
| OR-49 derived store rebuild from raw | conservation exact |
| OR-50 re-index interrupted/resumed | no duplicate/missing |
| OR-51 tombstone propagation | within SLO, residue=0 |
| OR-52 restore/rollback resurrects deletion | FAIL |
| OR-53 trace跨全链stage | membership完整 |
| OR-54 trace含raw/secret/ACL/path | leakage FAIL |
| OR-55 metric从sample重算 | parity |
| OR-56 alert在burn/fault触发 | timely |
| OR-57 page/ack/mitigate drill | within target |
| OR-58 runbook动作无效/危险 | HOLD |
| OR-59 portable package native verify | PASS |
| OR-60 independent exact decision | QUALIFIED或保持HOLD |

## 15. Artifact 包

```text
artifacts/rag-maturity/g7/<run-id>/
├── manifest.json
├── environment-bill.json
├── authorization-and-candidate.json
├── scale-profiles.json
├── workload-manifests.json
├── sample-policy.json
├── samples.jsonl
├── latency-throughput.json
├── resource-cost.json
├── quality-guards.json
├── concurrency-results.json
├── fault-scenarios.json
├── fault-attempts.jsonl
├── security-corpus-manifest.json
├── security-results.json
├── backup-catalog.json
├── recovery-scenarios.json
├── deletion-propagation.json
├── observability-coverage.json
├── alerts-and-runbook-drills.json
├── failures.jsonl
├── reviewer-decision.json
├── limitations.json
└── checksums.json
```

## 16. Hard Gate、停止条件与目标重设

### 16.1 G7 hard exits

1. S/M/L exact profiles、query/workload/environment和sample floors完整；
2. 所有必需scale/cache/query/concurrency的SLO、quality、resource与cost guard通过；
3. concurrency/fault下不混代、不脏读、不重复副作用、不复活删除；
4. end-to-end security零泄漏/越权/危险副作用，safe-control可用性过门；
5. backup/restore/re-index/delete/rollback各场景RTO/RPO和守恒通过；
6. trace/metric/dashboard/alert/runbook端到端有效且无敏感内容；
7. 所有attempt/failure/unavailable完整披露，artifact可重算；
8. independent reviewer对exact package给出`QUALIFIED`。

### 16.2 立即停止

正式环境或未授权数据被触达、blast radius越界、数据损坏、ACL/secret泄漏、删除复活、质量guard下降、资源
失控、停止阈值无效、故障后未清理、选择性隐藏attempt或修改测试后阈值时立即停止。默认V1不变。

### 16.3 当前目标

当前完成`WP-G7D-01 / T7.0.1`：冻结in-memory性能、Wiki S/5-sample artifact、72-case synthetic security、
static rollback事实，定义OR-01–26差距、S/M/L、负载/soak/concurrency/fault/security/recovery/observability、
22项序列和OR-01–60。本结果为`DESIGN_ENGINEERING_PASS`，不是operations qualification。

```text
G0→G6 QUALIFIED
  → T7.1 scale/workload/environment
  → T7.2 capacity/soak/quality guard
  → T7.3 concurrency
  → T7.4 fault/chaos
  → T7.5 security
  → T7.6 recovery/deletion
  → T7.7 observability/alerts
  → T7.8 package → T7.9 independent decision
```

G6前允许继续G8/G9只读设计；禁止将S fixture、synthetic 72-case或static rollback称为production evidence。
