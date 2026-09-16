# G8 生产 Replay、Shadow、Canary、降级与回滚可执行规格

版本：2026-08-06 V1  
状态：`G8D DESIGN_ENGINEERING_PASS / G7_GATE_HOLD / PRODUCTION_AUTHORITY_MISSING / TRAFFIC_EXECUTION_NOT_STARTED`  
上位目标：`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`  
运行前置：`40_G7_CAPACITY_RELIABILITY_SECURITY_OBSERVABILITY_EXECUTION_SPEC.md`  
默认发布状态：`DEFAULT_V1 / NO_TRAFFIC_CHANGE / RELEASE_HOLD`

## 0. 本规格解决什么问题

仓库已有稳定V1默认、显式V2 opt-in、shadow route、canary route、七个component kill switches、exactly-once
fallback、production binding authority、六源/四类global gate和portable release admission verifier。其实现有意
fail closed：当前reviewed release evidence registry为空，默认V1，promotion不执行；旧evaluator、调用方
boolean、自签artifact和静态rollback都不能获得发布authority。

这套控制面证明“不会误发”，但没有产生任何真实replay、shadow、canary、线上SLO或实际rollback证据。
本规格冻结G8唯一目标：在G7 qualified candidate上，经正式数据/流量/变更授权，以严格no-skip阶段推进
replay→shadow plan→shadow retrieval→EvidencePack compare→internal canary→low-risk canary→multi-hop
canary；每阶段都有固定entry、exposure、guard、observation window、decision、abort和rollback receipt。

G8不执行default V2；最终默认切换属于G9。当前只完成只读审计与设计，没有读取生产流量，没有发请求、
写配置、改路由、触发回滚或创建自签authority。

## 1. Authority 与职责分离

### 1.1 必需角色

| 角色 | 权限 | 禁止兼任 |
|---|---|---|
| release owner | 批准stage目标与窗口 | candidate唯一实现者、唯一reviewer |
| data/privacy owner | 批准replay/shadow数据与保留 | benchmark operator |
| traffic owner | 执行route/config变更 | evidence reviewer |
| observability owner | 验证SLI/alerts | release signer |
| security owner | 审查安全与事件 | candidate实现者 |
| rollback commander | 有权立即降级/回滚 | promotion唯一批准者 |
| independent reviewer | 重算证据、签stage decision | traffic/config executor |
| incident commander | 处理实际异常 | 不受正常promotion节奏约束 |

身份以key/principal receipt验证，不以字符串姓名判定。break-glass rollback可以单人执行，但事后必须完整审计；
promotion始终需要预注册quorum。

### 1.2 `ProductionChangeAuthorizationV1`

```text
authorization_id / environment / project_scope
candidate_sha256 / g7_decision_sha256
allowed_stage / traffic_or_replay_scope
allowed_data_fields / retention / redaction
max_exposure / max_cost / window_start / window_end
abort_thresholds_sha256 / rollback_plan_sha256
approver_receipts[] / expires_at / revoked_at
content_sha256
```

authorization是硬上限，不是建议。stage、时间、项目、比例或数据字段超界即拒绝执行。

## 2. 当前代码事实

1. `ReleaseStageV2`已经枚举offline、shadow_plan、shadow_retrieval、compare_evidence_pack、internal_canary、
   low_risk、multi_hop和default_v2；
2. shadow阶段返回V1 response，同时可执行V2；显式V1不执行V2；显式V2 opt-in可直接走V2；
3. internal/low-risk canary只对eligible request生效，其他请求保持V1；
4. `GlobalComponentSwitchesV2`提供planner、source、materialization、performance等组件级preflight关闭；测试证明
   disabled component在任何V2工作前fallback且V1恰好执行一次；
5. release admission绑定固定production exports/bindings、exact artifact allowlist、source authority和verifier；
6. caller-supplied trust、自签receipt、复制/重签package、篡改default或production observation均被拒绝；
7. 当前fixed reviewed release evidence authority registry为空，所以即使构造看似合格输入仍default V1；
8. legacy package writer显式不能封装promotion authority；
9. 静态rollback rehearsal为七步固定计划，但未执行真实配置、流量、LKG或运行时恢复；
10. 现有状态/准入检查不访问database或network，适合作为安全只读control-plane baseline。

## 3. 差距与处置清单

| ID | 当前事实 | 风险 | G8处置 |
|---|---|---|---|
| RC-01 | 有stage enum，无production executor | 可构造decision但不改变/验证真实状态 | owner-authorized stage executor |
| RC-02 | reviewed registry固定为空 | 无合法live evidence ingress | immutable reviewed-evidence admission workflow |
| RC-03 | explicit V2 opt-in存在 | 未授权用户可能扩大暴露 | production opt-in allowlist/quota/kill switch |
| RC-04 | replay合同缺失 | 离线到shadow无真实分布桥梁 | sanitized immutable replay package |
| RC-05 | shadow sampled population未定义 | selection bias/高风险遗漏 | deterministic cohort manifest |
| RC-06 | shadow V2可能有外部副作用 | 隐形写入/调用/成本 | side-effect firewall与egress allowlist |
| RC-07 | V1/V2 request身份未配对 | 差异不可归因 | `PairedObservationV1` |
| RC-08 | stage观察窗未冻结 | 好时段提前结束 | min duration/sample/event floors |
| RC-09 | traffic比例/eligible规则无receipt | 实际暴露不可核验 | route decision/exposure conservation |
| RC-10 | stage guard未统一 | 每阶段凭主观判断 | pre-registered stage policy |
| RC-11 | quality label延迟 | canary可能在质量未知时扩大 | delayed-label debt与max pending guard |
| RC-12 | V1 fallback可掩盖V2失败 | 用户成功但V2不可靠 | primary/fallback分层SLI |
| RC-13 | exactly-once仅工程测试 | 外部调用/生成可能重复 | live idempotency/side-effect receipts |
| RC-14 | component switch只验证本地preflight | 配置传播延迟/实例分歧未知 | fleet convergence monitor |
| RC-15 | 无LKG catalog | 回滚目标可能过期/被删 | verified LKG with deletion watermark |
| RC-16 | 静态rollback不执行 | RTO、回滚失败和残留未知 | staged live-isolated rollback drill |
| RC-17 | 回滚只关组件可能留下缓存/任务 | 后台V2继续运行 | drain/cancel/cache fence/post-scan |
| RC-18 | 无stage-specific incident policy | 何时HOLD/rollback不明确 | severity→automatic action map |
| RC-19 | 无多地域/多实例一致性证据 | 部分实例仍V2 | config version/quorum/route audit |
| RC-20 | artifact freshness未绑定window | 旧quality/security证据被复用 | freshness/invalidation verifier |
| RC-21 | 人工review可看聚合不看失败case | 尾部风险隐藏 | critical sample packet与all-failure disclosure |
| RC-22 | shadow保留敏感问题/答案风险 | 新数据面 | field minimization/tokenization/TTL/delete |
| RC-23 | canary cohort可能固定少数用户 | 外部效度不足/不公平 | cohort coverage/fairness slices |
| RC-24 | 成本guard未绑定exposure | 小比例高成本可失控 | absolute+per-request cost caps |
| RC-25 | 无stage rollback后的稳定观察 | 一回V1即宣称恢复 | recovery soak + G7/G6 smoke |
| RC-26 | G8可能误含default切换 | 跳过最终governance | G8最大stage=multi-hop canary；G9才default |

## 4. 不可变原则

1. 阶段严格单调、一次前进一步，禁止skip。
2. 每次stage change只改变流量暴露，candidate/config/generation保持冻结。
3. 每stage使用独立、未过期的change authorization。
4. replay数据最小化、去标识、授权、不可变，禁止把response写回生产。
5. shadow用户响应始终来自V1；V2不能执行外部副作用。
6. shadow/canary的V1/V2使用同一request/scope/as-of快照和可比较预算。
7. route eligibility是确定性、版本化和可审计的；不能手工挑“容易”请求。
8. exposure按eligible、assigned、executed、responded、fallback、excluded守恒。
9. primary success与fallback success分别报告；fallback不洗掉V2错误。
10. 所有失败attempt和abort都保留，不能选择最好的window。
11. 观察窗同时满足最短时间、最小请求、distinct users/projects和critical events。
12. delayed quality labels达到债务上限时停止扩大。
13. 任一security/ACL/unsupported/wrong-version/deletion问题自动rollback，不等待平均指标。
14. component/fleet config未收敛不得增加exposure。
15. LKG必须已验证、仍授权、generation/deletion水位兼容。
16. rollback优先于promotion quorum；任何on-call可break-glass降级。
17. rollback包括route、component、drain、cancel、cache fence和post-check，不只是改flag。
18. rollback不得恢复被删除或撤权的generation。
19. 每个stage完成后由独立reviewer签exact artifact；executor不能自签。
20. G8不写default V2，不改长期release registry。

## 5. Stage State Machine

```text
R0 OFFLINE_REPLAY
 → R1 SHADOW_PLAN
 → R2 SHADOW_RETRIEVAL
 → R3 COMPARE_EVIDENCE_PACK
 → R4 INTERNAL_CANARY
 → R5 LOW_RISK_CANARY
 → R6 MULTI_HOP_CANARY
 → G8 QUALIFIED_FOR_G9
```

任何stage可转`HOLD`；R1–R6可转`ROLLBACK_IN_PROGRESS→V1_STABLE_RECOVERY→HOLD`。不能在同一个decision中
rollback后立即re-promote。default V2不属于本状态机。

### 5.1 通用 `StagePolicyV1`

```text
stage / predecessor
candidate_sha256 / authorization_sha256
cohort_policy_sha256 / max_exposure
min_duration / min_requests / min_projects / min_users
required_quality_labels / max_pending_label_debt
slo_quality_security_cost_guards[]
automatic_abort_conditions[]
rollback_target_lkg_sha256
review_quorum / expiry
content_sha256
```

## 6. R0 Production Replay

### Entry

- G7 qualified；replay authorization和field minimization通过；
- exact historical request snapshot与label availability可核验；
- no-network/no-write/side-effect firewall实测；
- current candidate能在隔离环境重建需要的as-of generation。

### 执行

按时间/项目/用户/intent/复杂度/ACL/answerability分层抽样。原始问题优先存protected ref，runner输入通过临时
授权读取；portable包只留hash与aggregate。V1/V2 paired replay共享输入、seed与预算。

### Exit

membership完整、side effect=0、G6 quality guards、G7 latency/cost guards和security全部过门；distribution
coverage达到floor；所有unreplayable记录原因。FAIL/HOLD不会进入R1。

## 7. R1 Shadow Plan

只运行scope/intention/route/role/as-of planner，不触发source retrieval、model generation或external call。
比较V1实际使用信息与V2计划的required/extra/missing source/role；记录planner latency和capability violation。

硬门：response仍V1；V2 downstream call=0；route/ACL/as-of关键错误=0；fleet config一致；shadow开销不超过guard。

## 8. R2 Shadow Retrieval

允许执行六源只读检索和fusion前候选生成，禁止Wiki build、raw content进入生成器、答案生成和外部副作用。
检索使用shadow namespace/cache partition，避免污染V1和未来canary缓存。

硬门：source availability、latency、entity/root/version/ACL、cost和fallback诚实性过门；禁止production write、
cache cross-scope和background task残留。

## 9. R3 EvidencePack Compare

允许fusion、typed graph、Wiki shadow projection/navigation、raw read验证和EvidencePack构建；不向用户返回V2
答案。若需生成答案用于盲评，必须在isolated generator、禁止工具/网络/写入，并存protected result。

硬门：G6层级质量、raw authority、citation、conflict、unsupported、security、resource和delayed review debt过门。
V1/V2差异包由独立reviewer抽检，不能只看aggregate。

## 10. R4 Internal Canary

只对明示internal principal、独立canary项目/ACL、低blast radius和已批准intent返回V2。必须提供用户可见的
fallback/feedback channel；任何写操作仍禁止或走完全隔离sandbox。

硬门：零P0/P1、primary V2 SLO/quality/security过门、fallback与complaint在guard内、rollback drill通过、
观察窗完整。internal用户反馈不能替代objective metrics。

## 11. R5 Low-risk Canary

### Cohort资格

只包含预注册低风险、可轻易复核、无高敏、非多跳、非关键决策intent；用户/project选择采用稳定hash且有
holdout，禁止手工挑请求。比例从最小批准值开始，任何增加都视为新的stage step与decision。

### 硬门

用户影响、quality、unsupported、ACL、latency、availability、fallback、cost、fairness、complaint和label debt
全过门。高风险、跨ACL、复杂冲突、多跳请求仍V1。

## 12. R6 Multi-hop Canary

扩大到已批准的真实多源、多跳、conflict/as-of任务，但仍有stable V1 holdout和最大暴露上限。要求G6关键
slice在真实流量有足够reviewed observations；未获得label的请求只计coverage debt，不得默认PASS。

G8 Exit只能给`QUALIFIED_FOR_DEFAULT_REVIEW`，不能返回`DEFAULT_V2`。

## 13. Pairing、Exposure 与观测合同

### 13.1 `PairedObservationV1`

```text
observation_id / stage / request_sha256
project_cohort_sha256 / scope_sha256 / as_of
route_assignment / candidate_sha256 / v1_revision
v1_execution_receipt / v2_execution_receipt
response_engine / fallback_receipt
quality_label_status / review_receipt
latency_cost_resource / security_outcome
user_feedback_status / content_sha256
```

### 13.2 Exposure conservation

```text
eligible = assigned_v1_holdout + assigned_v2 + excluded_after_assignment
assigned_v2 = executed_v2 + preflight_fallback
executed_v2 = responded_v2 + runtime_fallback + failed_without_response
all_received = eligible + ineligible + malformed_before_eligibility
```

各项需exact count和request membership hash。unknown、telemetry loss、late event不能被归零。

### 13.3 Stage observation window

窗口至少绑定start/end、deployment/config revisions、fleet convergence、traffic mix、incident/change calendar和label
cutoff。跨candidate、generation、model、policy或重大外部故障的窗口必须分段，不得拼接。

## 14. Guard 与决策

### 14.1 Guard类别

- safety：ACL/secret/unsupported critical/wrong-version/deletion residue=0；
- quality：G6 metrics与critical slice non-inferiority/floors；
- reliability：primary V2 error/timeout/fallback/circuit breaker；
- performance：end-to-end/stage P95/P99、queue、resource；
- cost：absolute/day、per request、model/tool/network调用；
- user：complaint、negative feedback、task abandonment（有可用分母才报告）；
- operations：fleet convergence、alert coverage、on-call ack、artifact freshness；
- exposure：实际比例/范围不超authorization。

### 14.2 Decision

`StageDecisionV1`只能为`ADVANCE_ONE_STAGE`、`EXTEND_WINDOW`、`HOLD`或`ROLLBACK`。decision绑定exact inputs、
all blockers、review quorum、expiry和next maximum stage。任何缺失/UNAVAILABLE关键guard默认HOLD。

## 15. 实际回滚合同

### 15.1 触发

P0/P1 safety、ACL/secret、critical unsupported、wrong-version、数据损坏、route超界、fleet split brain、持续SLO
burn、异常成本、删除复活、monitoring blind或manual commander触发。

### 15.2 `RollbackExecutionV1`

固定但可验证的顺序：

1. freeze promotion与新assignment；
2. route所有新请求到V1；
3. preflight关闭planner/source/reranker/fusion/graph/Wiki/raw/generator等相关switch；
4. drain/cancel在途V2和background work；
5. fence V2 cache/namespace/config version；
6. 选择verified、deletion-safe LKG或保持V1，不直接恢复过期generation；
7. 验证fleet convergence和V2 execution=0；
8. 执行V1 SLO/G6 smoke、安全/残留scan；
9. 保留事件、request membership、config diff和timeline；
10. 进入recovery soak与independent close review。

### 15.3 成功标准

从trigger到route V1、V2 drain、fleet convergence、用户SLO恢复分别计时；全部在预注册RTO内，零新V2执行、
零残留副作用、零删除复活，V1质量/可用性恢复。改一个flag但实例未收敛不算成功。

## 16. 22项原子实施序列

| # | Work package | 交付 | 完成证据 |
|---:|---|---|---|
| 1 | T8.0.1 | control-plane/authority/router audit | RC-01–26证据 |
| 2 | T8.0.2 | role/change/privacy authority policy | quorum/break-glass/stop |
| 3 | T8.1.1 | replay dataset/authorization | membership/redaction/TTL |
| 4 | T8.1.2 | side-effect-free paired replay runner | writes/network=0 |
| 5 | T8.1.3 | replay review/Gate | exact R0 decision |
| 6 | T8.2.1 | cohort/route assignment service | deterministic/exposure conservation |
| 7 | T8.2.2 | paired observation/label debt store | V1/V2 identity完整 |
| 8 | T8.2.3 | fleet config convergence monitor | no split brain |
| 9 | T8.3.1 | R1 shadow plan | downstream V2=0 |
| 10 | T8.3.2 | R2 shadow retrieval | no answer/write/cache pollution |
| 11 | T8.3.3 | R3 EvidencePack compare | raw/answer blind review |
| 12 | T8.3.4 | three shadow independent decisions | no stage skip |
| 13 | T8.4.1 | canary eligibility/holdout policy | risk/ACL/project caps |
| 14 | T8.4.2 | R4 internal canary | internal only/feedback/fallback |
| 15 | T8.4.3 | R5 low-risk canary | stable hash/holdout/guard |
| 16 | T8.4.4 | R6 multi-hop canary | critical slice labels |
| 17 | T8.5.1 | LKG catalog/verifier | deletion-safe/current |
| 18 | T8.5.2 | runtime rollback executor | route/drain/fence/post-check |
| 19 | T8.5.3 | authorized rollback drills | RTO/fleet/no residue |
| 20 | T8.6.1 | stage evidence packages | protected+portable/native verify |
| 21 | T8.6.2 | reviewed evidence admission workflow | no self-sign/copy promotion |
| 22 | T8.7.1 | independent G8 review | QUALIFIED_FOR_G9 or HOLD/FAIL |

## 17. 验证矩阵（RC-01–RC-60）

### 17.1 Authority/replay（RC-01–14）

| Case | Hard exit |
|---|---|
| RC-01 expired/revoked/wrong-stage authorization | no execution |
| RC-02 role/quorum collision | HOLD |
| RC-03 replay membership/hash exact | PASS |
| RC-04 replay includes unauthorized field | leakage fail |
| RC-05 replay external write/network attempt | abort |
| RC-06 unreplayable case | typed, denominator retained |
| RC-07 V1/V2 snapshot/budget mismatch | pair invalid |
| RC-08 distribution slice floor missing | R0 HOLD |
| RC-09 G6/G7 guard regression | R0 FAIL |
| RC-10 caller self-signed evidence | rejected |
| RC-11 copied/resigned package | rejected |
| RC-12 stale production binding authority | rejected |
| RC-13 artifact outside allowlist | rejected |
| RC-14 missing attempt/test access | HOLD |

### 17.2 Shadow（RC-15–28）

| Case | Hard exit |
|---|---|
| RC-15 R1 executes retriever | abort |
| RC-16 R2 generates/returns answer | abort |
| RC-17 R3 V2 response reaches user | abort |
| RC-18 shadow write/tool/network side effect | rollback/incident |
| RC-19 V2 cache pollutes V1/canary | fail |
| RC-20 route assignment non-deterministic | invalid |
| RC-21 exposure conservation mismatch | HOLD |
| RC-22 fleet config disagreement | no advance |
| RC-23 primary V2 error hidden by V1 success | report fail |
| RC-24 pending label debt over cap | stop/extend |
| RC-25 observation window below floor | extend, no advance |
| RC-26 critical failure sample omitted | qualification fail |
| RC-27 R1→R3 skip | rejected |
| RC-28 exact stage review absent/expired | HOLD |

### 17.3 Canary（RC-29–44）

| Case | Hard exit |
|---|---|
| RC-29 internal cohort includes external principal | rollback |
| RC-30 low-risk request misclassified high-risk | rollback/HOLD |
| RC-31 canary exposure exceeds cap | automatic rollback |
| RC-32 no stable V1 holdout | stage invalid |
| RC-33 user/project sticky assignment | exact |
| RC-34 fallback executes V1 more than once | fail |
| RC-35 V2 side effect after fallback | fail |
| RC-36 P0/P1 safety event | immediate rollback |
| RC-37 critical unsupported/wrong-version | immediate rollback |
| RC-38 SLO/error budget burn | policy action exact |
| RC-39 cost cap exceeded | stop/rollback |
| RC-40 fairness/coverage slice unavailable | no advance |
| RC-41 multi-hop labels below floor | extend/HOLD |
| RC-42 complaint/feedback denominator unknown | no false pass |
| RC-43 change window overlaps unrelated incident | segment/invalid |
| RC-44 R6 decision attempts DEFAULT_V2 | rejected；转G9 |

### 17.4 Rollback/artifact（RC-45–60）

| Case | Hard exit |
|---|---|
| RC-45 LKG expired/revoked/stale deletion | target rejected |
| RC-46 new assignment frozen | exact |
| RC-47 route convergence to V1 | all instances within RTO |
| RC-48 in-flight/background V2 drain | zero after deadline |
| RC-49 component switch preflight | downstream work=0 |
| RC-50 cache/namespace fenced | no stale reads |
| RC-51 rollback tries deleted generation | rejected/rebuild |
| RC-52 V1 smoke/SLO recovery | PASS |
| RC-53 post-rollback security/residue scan | zero findings |
| RC-54 monitoring outage during rollback | conservative hold/escalate |
| RC-55 break-glass action | allowed + post-review |
| RC-56 rollback后立即re-promote | rejected |
| RC-57 complete timeline/config/request membership | exact |
| RC-58 portable package无敏感内容 | PASS |
| RC-59 native verifier/recompute/copy | PASS |
| RC-60 independent exact G8 decision | QUALIFIED_FOR_G9或HOLD |

## 18. Artifact 与停止条件

```text
artifacts/rag-maturity/g8/<stage>/<run-id>/
├── manifest.json
├── candidate-and-authorizations.json
├── stage-policy.json
├── cohort-and-exposure.json
├── replay-or-live-window.json
├── paired-observations.jsonl
├── route-decisions.jsonl
├── fleet-convergence.json
├── quality-security-slo-cost.json
├── label-debt.json
├── incidents-and-attempts.jsonl
├── lkg-verification.json
├── rollback-execution.json
├── post-rollback-validation.json
├── reviewer-decision.json
├── limitations.json
└── checksums.json
```

发现未授权production访问/写入、用户收到shadow响应、exposure超界、ACL/secret/critical unsupported、mixed
fleet、side effect重复、删除复活、监控盲区、回滚不收敛或自签authority时立即停止并回到V1。

## 19. 当前目标重设

当前完成`WP-G8D-01 / T8.0.1`：冻结现有fail-closed control plane、空reviewed registry、stage/router、
component preflight和静态rollback事实；定义RC-01–26、R0–R6、22项序列与RC-01–60。本结果仅为
`DESIGN_ENGINEERING_PASS`，没有真实流量或发布授权。

```text
G0→G7 QUALIFIED
  → R0 replay
  → R1 plan shadow
  → R2 retrieval shadow
  → R3 EvidencePack compare
  → R4 internal canary
  → R5 low-risk canary
  → R6 multi-hop canary
  → QUALIFIED_FOR_G9（仍DEFAULT_V1）
```

G7与production change authority未满足前，所有R0–R6 runtime工作均锁定；G9设计可只读继续。
