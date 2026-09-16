# G5 六源真实质量、独立标注、校准、防泄漏与单源 Gate 可执行规格

版本：2026-08-06 V1  
状态：`G5D DESIGN_ENGINEERING_PASS / G4_GATE_HOLD / REAL_GOLDEN_MISSING / QUALITY_RUN_NOT_STARTED`  
上位目标：`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`  
数据前置：`37_G4_SIX_SOURCE_CORPUS_MATERIALIZATION_BACKFILL_EXECUTION_SPEC.md`  
默认发布状态：`DEFAULT_V1 / QUALITY_HOLD`  

## 0. 本规格解决什么问题

仓库已有六源 Golden、baseline runner、指标、slice、安全报告和 release evaluator；这些能力证明评测
代码可运行、合同可复现、若干工程回归能被捕获，却不自动证明真实来源检索质量。当前五类非 Code
Golden 均由程序化 fixture 构建；Code V2 也是受控仓库/历史 fixture 为主。没有一份 owner-authorized
真实 evaluation universe、root-grouped train/calibration/test split、独立盲标协议、test access ledger、
预注册阈值和六源统一校准资格。

本规格冻结 G5 的唯一目标：每个 source retriever 分别在 G4 owner-authorized、版本化 generation 上，
用 root provenance 隔离的真实 blind test 证明召回、版本、locator、结构化事实、hard negative、拒答、
ACL 和失败诚实性；只在 calibration split 拟合 source-local probability profile，并以独立 test 单次报告
ECE/Brier/coverage。任一来源不过门就保持该来源 `QUALITY_HOLD`，联合平均分不得掩盖。

本轮仅完成代码/artifact 只读审计、数据/标注/分割/指标/校准/运行/Gate 合同和实施顺序。没有访问
正式数据库、没有读取真实内容、没有创建真实 labels、没有拟合阈值、没有运行 test split，也没有改变
任何 source route 或默认 V1。

## 1. Entry、Exit 与 authority 边界

### 1.1 G5 runtime Entry

以下全部满足才允许生成真实 split 或运行 evaluator：

1. G0–G4 均 `QUALIFIED`，且 exact `ActiveGenerationSetV1` 未过期；
2. `QualificationDataAuthorizationV1` 明确允许质量标注、derived label、reviewer access 和 portable aggregate；
3. G4 corpus membership、root provenance、deletion watermark 与 raw read receipt 可验证；
4. annotation owner、independent adjudicator、calibration operator、test custodian 和 quality reviewer 角色分离；
5. test store/labels 对实现者和 calibration operator不可见；
6. evaluator revision、retriever/index/reranker/context versions 与预算预注册；
7. metric policy、slice floors、threshold policy、statistical plan 和 failure policy 在 test 解封前冻结；
8. 所有执行保持 source-local；G6 前不得用多源结果补单源缺口。

### 1.2 G5 `QUALIFIED` 的含义

一个来源只有同时满足以下四层才可 source-qualified：

- 数据层：真实、授权、root-grouped、盲测、无泄漏；
- 评测层：Gold labels/eligible metrics/denominators/unknown 可独立复核；
- 质量层：绝对 floor、关键 slice floor、V1 non-inferiority/改善、校准和安全全部过门；
- authority 层：independent reviewer 对 exact artifact 给出有效 decision。

六源全部 source-qualified 后，G5 才可整体 `QUALIFIED`。这仍不授权多源质量、生产 shadow、canary 或
default V2。

## 2. 当前代码与 artifact 事实

### 2.1 六源现状

| 来源 | 当前 Golden/evaluator | 当前真实边界 | 当前 release 事实 |
|---|---|---|---|
| Code | V2 50 case；15 smoke；33 treatment eligible；25 hard-negative judgments | 6 current-project snapshot、23 controlled multilingual、21 historical fixture；17/50 machine-ineligible；manifest 自述 baseline not run，另有多个历史 run artifact | C7 evaluator fail closed；缺 release attestation/production observation 时 HOLD |
| Codex | 45 case、8 slice；thread/item/episode/validation 等指标 | 45 case 来自 8 个 deterministic fixture thread；没有真实 root-grouped split | artifact `baseline_qualified=false`；release 需 production observation/treatment evidence |
| Experiment | 45 case；numeric/unit/comparison/reproduction slices | foundation 只有 12 synthetic run、1 experiment、3 metric definitions；当前 artifact `NON_QUALIFIED` | governance 明确 synthetic 或无 production observation 不可发布 |
| Notebook | 40 case；cell/output/parameter/order/stale/dependency | `notebook-programmatic-fixture-v1`；未绑定 G4 真实 notebook universe/split | offline evaluator HOLD；5 个关键 metric 未达 threshold |
| Document | 50 case；paragraph/table/figure/citation/claim/version | `document-programmatic-fixture-v1`；fixture 内 reviewer/claim 由代码构建 | offline evaluator HOLD；claim precision 0.90 < 0.95 |
| Workspace | 40 case；scope/current/temporal/blocker/evidence/ACL | `workspace-programmatic-fixture-v1`；无真实 mutation/audit universe/split | offline metrics通过，但因 `production_observation=false` 保持 HOLD |

### 2.2 可复用的工程资产

1. 每源已有 frozen Pydantic/JSON 合同、canonical package/hash 和 portable verifier；
2. 多数 evaluator 区分 metric availability、denominator、zero-result/error；
3. Golden case 已覆盖多种 source-specific slices 和 hard negatives；
4. release evaluator 普遍保持 side-effect-free，并区分 engineering/offline 与 production observation；
5. Code calibration 有保守的 no-data/small-sample 行为、ECE/Brier 和 context-version mismatch 拒绝；
6. artifacts 通常包含 golden、prediction、metric、slice、latency、security、release 和 checksums；
7. source route/control plane 默认 V1，未把 offline fixture 自动提升为稳定 V2。

这些资产可作为 evaluator contract regression、真实 Golden 的 schema 模板和候选 baseline，但不能直接
升级为真实 test authority。

## 3. G5 差距与处置清单

| ID | 当前事实 | 风险 | G5 处置 |
|---|---|---|---|
| QL-01 | 五类非 Code Golden 由程序化 fixture 构建 | 实现者同时控制输入、truth 与 scorer | fixture 永久标 `ENGINEERING_FIXTURE`；真实 Golden 独立创建 |
| QL-02 | Code 主要为受控 fixture/self snapshot | 不能代表真实 repo 分布、语言和错误模式 | 从 G4 real repos 抽 root-grouped blind cases；fixture保留回归 |
| QL-03 | 无统一 evaluation universe manifest | 样本从哪里来、漏了什么不可证 | `SourceEvaluationUniverseV1` 绑定 G4 corpus/generation |
| QL-04 | 无跨源通用 root provenance split | 同一项目/实验/导出可进入多 split | `FrozenEvaluationSplitManifestV1` 按 root group 原子分配 |
| QL-05 | train/calibration/test 角色不统一 | test 被用于调规则/阈值 | evaluation firewall + role-scoped store/access ledger |
| QL-06 | case/fixture/reviewer 有时同代码生成 | 标签可被实现细节污染 | 独立 annotation packet；实现代码不可生成 Gold outcome |
| QL-07 | 无双盲/分歧/adjudication contract | 单一 reviewer 偏差不可量化 | 双人独立标注 + 分歧裁决 + agreement report |
| QL-08 | 无 label confidence/unknown/insufficient evidence 统一语义 | 强迫标注制造伪 truth | typed label availability；unknown 不进入 accuracy 分母但保留 coverage |
| QL-09 | thresholds 在 source 代码中硬编码 | 阈值来源、成本和 test 时序不可证 | `PreRegisteredSourceGatePolicyV1` 在 test 解封前独立签署 |
| QL-10 | 多次 Code run artifact 共存 | 选择最好 run 或反复看 test 的风险 | attempt registry + test access budget + all-attempt disclosure |
| QL-11 | baseline/treatment 可能使用不同 generation/budget | 不能归因 V2 变化 | paired run manifest 固定 corpus/snapshot/budget/environment |
| QL-12 | 17/50 Code case treatment-ineligible | 可见分母与可比较分母不同 | 总 coverage 与 eligible treatment metrics 分开；不得丢 case |
| QL-13 | Experiment 45 queries只落在12 synthetic runs | query count掩盖 source root 小 | 报 case/root/entity 三重 denominator；真实 run group独立 |
| QL-14 | metric availability 各源命名/规则不同 | UNAVAILABLE 可能被当 PASS 或 0 | `MetricObservationV2` 统一 availability/reason/numerator/denominator |
| QL-15 | 空 denominator 处理不完全统一 | N/A 被误算100%或整体平均 | empty=`UNAVAILABLE/NOT_APPLICABLE`，绝不产生 pass value |
| QL-16 | 只有 Code 有显式 calibration实现 | 多源分数无法解释为相同概率 | 六源 source-local calibration contract；无数据则 unavailable |
| QL-17 | Code 校准说明“empirical score，不是真实概率” | 下游可能当概率融合 | 只有 test-calibrated profile 可名 `relevance_probability`；其他叫 score |
| QL-18 | 无 active slice registry | calibration 对不匹配 task/entity被复用 | profile key=`source×task×entity_type×retriever/index/reranker` |
| QL-19 | 缺 root-level CI/uncertainty | 同 root 多 case虚增显著性 | cluster bootstrap/paired root resampling；报告 CI 与 effective roots |
| QL-20 | slice case floor 未硬化 | 极小 slice 100% 被误报成熟 | critical slice floor；不足即 HOLD，不允许 macro 平均替代 |
| QL-21 | 安全报告多为fixture扫描 | 无真实 ACL/secret/privacy 质量证据 | G4授权真实 negative cases + zero-tolerance security gate |
| QL-22 | latency 与 quality 同 run但环境约束不一 | 质量差异可能来自预算/缓存 | G5只记录bound latency；容量 qualification移交G7 |
| QL-23 | release evaluator可接收构造 evidence object | 调用方可能声称生产观测 | evidence必须绑定外部 run receipt、dataset/split/generation/access ledger |
| QL-24 | source quality可被联合平均掩盖 | 强源补偿弱源 | 六个独立 Gate；G5 aggregate是AND，不是weighted score |
| QL-25 | 删除/撤权后的 Gold case 生命周期未定义 | test保存已撤销内容或结果继续授权 | case tombstone/successor + quality authority invalidation |
| QL-26 | portable artifacts可能含 query/locator/labels | 评测包扩大敏感数据面 | protected raw artifact与portable aggregate分层 |

## 4. 不可变原则

1. 工程 fixture 只能证明 evaluator/runtime contract，不证明真实质量。
2. 一个真实 source case 必须来自 G4 active generation 的 owner-authorized member。
3. raw/case/label/answer 每一层都继承 ACL；portable artifact不得扩大可见性。
4. split 以 root provenance group为原子，不以 case row随机切分。
5. 同 root 的版本、导出、镜像、跨源投影不得跨 train/calibration/test。
6. test labels 对开发者、训练/规则作者、calibration operator 在冻结前不可见。
7. Gold question不能包含 target identifier/path/答案串等非自然泄漏，除非 slice明确评估 exact locator。
8. evaluator 输入中禁止出现 label、expected IDs、fixture alias、review note或case metadata。
9. unknown、ambiguous、insufficient evidence是合法 Gold状态，不得强迫二元标签。
10. annotation disagreement必须保留，不以事后删除难例提高分数。
11. test解封前冻结 revision、generation、index、model、budget、metric、threshold和failure policy。
12. test run失败不能选择性重跑部分case；infra invalidation需独立批准并全量重跑同配置。
13. 看到test结果后任何实现/阈值变化都要求新 candidate和新的、未污染test version。
14. calibration split只能拟合概率映射/阈值；不能修改检索特征、query重写或Gold。
15. test split只做一次正式qualification read；所有attempt包括失败都进入ledger。
16. metric必须带 numerator、denominator、availability、unit、direction、CI和slice。
17. macro、micro、root-weighted结果同时报告；不允许只选最高的聚合。
18. 关键安全、wrong-version、unsupported和ACL gate零容忍，不能被平均。
19. source-local raw score不能跨源比较；只有匹配profile的calibrated probability可供G6使用。
20. 校准profile版本必须绑定retriever/index/reranker/task/entity/data split和fit code。
21. 一个profile缺正例/负例、样本不足或drift时返回UNAVAILABLE，不回退全局曲线。
22. 六源分别PASS；联合分数、Wiki命中或答案质量不能替代单源失败。
23. quality reviewer不能是该candidate的唯一实现者、唯一标注者或test custodian。
24. 删除/授权撤销会使相关case、split、calibration、test和quality decision级联失效。
25. G5 PASS不自动改变release route；只产生source quality authority candidate。

## 5. 真实 Evaluation Universe

### 5.1 `SourceEvaluationUniverseV1`

```text
universe_id / revision
source / project_id / environment
authorization_sha256
corpus_manifest_sha256
active_generation_set_sha256
source_generation_manifest_sha256
snapshot_at
eligible_roots[]
  root_provenance_group
  member_ids_sha256
  source_instance_sha256
  versions[]
  acl_class
  observable_slice_tags[]
  deletion_watermark
ineligible_roots[]
  reason_code
sampling_frame
coverage_summary
content_sha256
```

Universe是抽样框，不是已标注case。ineligible必须有reason，如authorization禁止、删除、无法raw read、
source drift、insufficient version。不得只从“检索能成功”的对象中抽样。

### 5.2 query/case创建

case来源可包括真实用户任务的授权safe projection、领域专家基于raw evidence创建、规则化覆盖抽样；不得
把现有retriever top result反向写成Gold。每个case先冻结question和scope，再由annotator在独立evidence
browser中标注；annotator不能看到candidate排名/score/channel。

### 5.3 `SourceEvaluationCaseV2`

```text
case_id / source / task / intent
question_safe_text_or_protected_ref
question_sha256
scope / temporal_target
root_provenance_groups[]
required_slice_tags[]
answerability = ANSWERABLE | UNANSWERABLE | AMBIGUOUS | AUTHORITY_UNAVAILABLE
gold_entities[]
gold_relations[]
required_versions[]
required_locators[]
forbidden_entities[] / forbidden_versions[]
numeric_truth[] / unit_truth[] / temporal_truth[]
minimum_evidence_set[]
eligible_metrics[]
annotation_packet_sha256
acl_class
content_sha256
```

case portable projection只保留safe question或digest、slice和aggregate identity；敏感query/gold locator保留
在受保护评测环境。

## 6. Root-grouped Split 与评测防火墙

### 6.1 `FrozenEvaluationSplitManifestV1`

```text
split_id / version
universe_sha256
split_algorithm_version / random_seed
stratification_targets[]
group_assignments[]
  root_provenance_group
  split = TRAIN | CALIBRATION | TEST
  case_ids_sha256
  source/task/slice counts
train_manifest_sha256
calibration_manifest_sha256
test_manifest_sha256
overlap_report_sha256
sealed_test_store_receipt_sha256
content_sha256
```

### 6.2 分割硬规则

- 相同root group只能出现在一个split；
- 跨源同一实验链可作为G6 joint root，但G5每源split仍共享全局root registry以防跨源泄漏；
- 时间相关source优先使用time-blocked holdout，避免future version泄漏到历史任务；
- rare critical slice不足时增加采样或保持HOLD，不能复制case；
- calibration必须含该active profile key足够正负例；否则profile unavailable；
- test floor是独立reviewed case，不含train/calibration。

### 6.3 最低 blind test floor

| 来源 | 最低独立 test cases | 最低 distinct root groups | 必须有的critical slices |
|---|---:|---:|---|
| Code | 50 | 10 | locator/version/wrong-version/hard-negative/graph/history/unanswerable/ACL |
| Codex | 45 | 15 threads | goal/event-order/failure-retry/validation/privacy/truncation/unanswerable |
| Experiment | 45 | 15 run groups | numeric/unit/comparison/reproduction/failure/dataset-code-version/unanswerable |
| Notebook | 40 | 10 templates | cell/output/order/stale/parameter/artifact/error/version/unanswerable |
| Document | 50 | 20 document roots | paragraph/table/figure/citation/claim/numeric/version/parse failure/conflict |
| Workspace | 40 | 12 topic/iteration roots | current/state/as-of/blocker/dependency/decision/ACL/delete/unanswerable |

每个critical slice至少5个eligible test case；安全/ACL/secret/privacy slice至少3个且违规必须为0。若真实
root不足，Gate为`INSUFFICIENT_TEST_COVERAGE`，不通过case复制或多个query共享一个root来凑effective N。

### 6.4 test access ledger

记录谁、何时、为哪个candidate、读取了哪个sealed test digest、执行原因、输出artifact和是否看到了
case-level结果。开发者默认只能看到aggregate+预注册slice；case-level error analysis由独立custodian先
脱敏并在decision后发布。任何非授权访问使test version污染并需 successor。

## 7. Annotation 与 adjudication

### 7.1 角色

```text
data curator       冻结universe/sampling，不标candidate优劣
annotator A/B      独立读取raw evidence并标Gold
adjudicator        只处理分歧，不能看到candidate score
calibration owner  只访问calibration labels
test custodian     保管sealed test与执行器
quality reviewer   复核artifact/denominator/decision
implementation owner 不得兼任唯一annotator/adjudicator/custodian/reviewer
```

### 7.2 `AnnotationPacketV1`

至少包括：annotator role receipt、case/question/scope digest、evidence access receipt、raw versions、selected
Gold/forbidden/unknown、numeric/unit/temporal derivation、confidence、ambiguity、created_at、annotation policy
version和content digest。自由文本reason只能留在protected artifact，portable只输出reason code。

### 7.3 agreement

报告case-level exact agreement、set overlap、graded relevance agreement、numeric/unit/version agreement和
answerability agreement。可使用Cohen's kappa或Krippendorff alpha作为辅助，但必须同时报告原始
numerator/denominator和prevalence；低prevalence时不以单一kappa判定。critical slice分歧未裁决为HOLD。

### 7.4 Gold变更

发现标注错误后不原地改test：创建annotation correction record、影响case/root、旧/新Gold digest、发现
来源、是否由candidate output触发、independent approval和successor split。若修正由test错误分析触发，旧
candidate仍按原pre-registered decision记录，不能追溯改成PASS。

## 8. Run、candidate 与对照合同

### 8.1 `SourceQualityCandidateV1`

```text
candidate_id / source
revision_sha256
active_generation_set_sha256 / source_generation_sha256
retriever/index/embedding/reranker/context versions
query understanding/task adapter versions
calibration_profile_sha256?
configuration_sha256
resource_budget
feature_flags
content_sha256
```

### 8.2 `SourceEvaluationRunV2`

```text
run_id / attempt_id
candidate_sha256 / baseline_candidate_sha256
split_sha256 / test_seal_receipt_sha256
gate_policy_sha256
evaluator_revision_sha256
environment_sha256
seed / deterministic_policy
cache_mode / warmup_policy
case_order_sha256
started_at / completed_at
case_outcomes[]
metrics_sha256 / slices_sha256 / security_sha256
failure_ledger_sha256
test_access_ledger_entry_sha256
status = VALID | INVALID_INFRA | FAILED_QUALITY | PASS_CANDIDATE
content_sha256
```

### 8.3 paired baseline/treatment

V1 baseline与V2 candidate必须使用相同case order、scope、raw snapshot、resource budget、timeout、cache
policy和evaluator。若source contract无法为V1表达某task，则该case报告baseline unavailable，不从coverage
消失；新增能力用absolute floor判定，shared能力还需paired non-inferiority。

## 9. Metric 合同

### 9.1 `MetricObservationV2`

```text
name / version / source / task / slice
status = AVAILABLE | UNAVAILABLE | NOT_APPLICABLE | INVALID
direction = MIN | MAX | EXACT
numerator / denominator / value / unit
root_denominator / effective_sample_size
confidence_interval
aggregation = MICRO | MACRO | ROOT_WEIGHTED
threshold / threshold_policy_sha256
baseline_value / paired_delta / non_inferiority_margin?
failure_case_count
reason_code?
content_sha256
```

`INVALID`使run无效；`UNAVAILABLE`使required gate HOLD；`NOT_APPLICABLE`只能由预注册policy声明。错误、
timeout、unauthorized、no-match分别计数，不能全部当0 relevance。

### 9.2 shared retrieval metrics

- Recall@K / success@K：Gold minimum set或required entity是否覆盖；
- MRR/nDCG：只对有graded judgment且answerable case；
- hard-negative avoidance：forbidden/wrong-version/unauthorized实体未进入eligible context；
- locator/version accuracy：top eligible evidence的exact source location/version；
- unanswerable/refusal accuracy：无权威证据时不返回伪positive；
- coverage：eligible/available/error/timeout/unknown/unsupported各分母；
- duplicate/root diversity：同root重复不能虚增Recall；
- raw-read validity：selected evidence能通过G1/G2 receipt回读。

### 9.3 source-specific metrics

| Source | Required metrics |
|---|---|
| Code | symbol/file locator、commit/version、change pair、typed path、wrong-version、generated/vendor exclusion、validation link |
| Codex | thread/goal/item/episode、event order、call-result、failure retry、false-validated、harmful old attempt、privacy/refusal |
| Experiment | run/entity、predicate/filter、numeric value/unit/direction、comparability、dataset/config/code binding、reproduction、failed/deleted status |
| Notebook | template/revision/cell/output、producer-output path、execution order、dependency path、stale detection、parameter type、artifact/error |
| Document | paragraph/page/table header/figure/citation locator、numeric/unit、claim support/counter/unsupported、version/parse authority |
| Workspace | project/topic/iteration/work item scope、current state、as-of limitation、blocker/dependency、decision/evidence coverage、unsafe mutation/ACL |

### 9.4 confidence intervals

case-level metrics使用root-cluster bootstrap或适当exact binomial interval；paired baseline/candidate使用root
paired resampling。报告随机seed、replicate count、method和effective roots。CI不替代hard zero gate；样本
少到CI无解释力时返回coverage HOLD。

## 10. Source-local Calibration

### 10.1 profile key

```text
source
task
entity_type
retriever_version
index_schema/generation family
embedding_profile?
reranker_version
feature_set_sha256
calibration_split_sha256
fit_code_sha256
```

任何key不匹配都不可apply。禁止把Code curve应用到Document，或把claim profile应用到paragraph。

### 10.2 `CalibrationProfileV2`

```text
profile_id / profile_key
method = ISOTONIC | PLATT | HISTOGRAM | IDENTITY_REVIEWED
training_observation_count
positive_count / negative_count
root_count
score_range / bins_or_parameters
fit_seed / fit_trace_sha256
calibration_metrics_on_calibration
availability / unavailable_reason
valid_from / expires_at
drift_bounds
content_sha256
```

active key至少50个labeled candidate observations、至少10个positive和10个negative、至少5个root；否则
profile `UNAVAILABLE_SMALL_SAMPLE`。这只是最低可拟合边界，统计计划可要求更高。禁止通过重复同case top-K
候选把root不足伪装为独立样本。

### 10.3 test calibration report

test只评估已冻结profile，报告Brier、ECE（同时保存bin counts）、log loss、coverage、over/under-confidence
slice和reliability table。不能在test上重新定bin/温度/阈值。没有profile的candidate输出`retrieval_score`，
不得命名`relevance_probability`。

### 10.4 threshold

decision threshold从calibration split和业务成本矩阵预注册，必须同时约束false positive、wrong-version、
refusal和coverage。G5 test验证threshold，不选择threshold。每次变更生成successor gate policy和新的unseen
test；不能在同test上网格搜索。

## 11. Pre-registered Source Gate

### 11.1 `PreRegisteredSourceGatePolicyV1`

至少绑定：source、candidate class、split/test seal、required metrics/slices、absolute threshold、paired
non-inferiority margin、zero-tolerance guardrail、minimum denominators/effective roots、CI rule、missing/invalid
policy、maximum infra reruns、test access roles、decision algorithm、expires_at和external reviewer receipt。

### 11.2 gate结构

```text
DATA_GATE
  authorization/split/root overlap/test seal/access
EVALUATOR_GATE
  revision/contract/denominator/metric recompute/tamper
QUALITY_GATE
  global + critical slices + baseline paired comparison
CALIBRATION_GATE
  profile match + ECE/Brier/coverage/drift
SECURITY_GATE
  ACL/secret/privacy/reasoning/wrong-version/unsafe mutation = 0
OPERABILITY_CARD
  bound latency/error/cost only; G7 before production qualification
REVIEW_GATE
  independent exact decision
```

任何一层HOLD/FAIL则该source不qualified。六源gate以逻辑AND聚合，不允许weighted average或“5/6通过”。

## 12. Test Run 控制与防止挑结果

1. custodian为candidate分配唯一attempt并锁定test digest；
2. evaluator先做environment/snapshot preflight；失败在读取labels前可标`INVALID_PREFLIGHT`；
3. 一旦读取任何test label，attempt计入access ledger；
4. timeout/worker crash等infra failure保存已读case范围、side effects和logs；
5. 只有预注册infra reason且candidate bytes不变时可批准全量rerun；
6. 所有attempt进入artifact，decision使用预注册选择规则（默认最后一个valid full run，不是最佳分）；
7. case-levelerror analysis在decision冻结后由custodian脱敏发布；
8. 实现/阈值/Gold变化进入新candidate/split version，旧run不覆盖。

## 13. Security、privacy 与 deletion

- Gold authoring界面执行G1 raw reader ACL；annotator只见其授权case；
- query、candidate content、raw locator、labels与review notes默认protected；
- portable package保存digest、aggregate counts、safe error codes和必要的匿名case IDs；
- secret/privacy发现触发case quarantine、incident和universe/split successor；
- raw member tombstone后，相关case、annotation、calibration observation和test decision进入invalidated ledger；
- 若删除影响active quality floor或critical slice，source quality authority自动过期，不以剩余case静默重算；
- reviewer不能从artifact推断未授权source/object是否存在。

## 14. 原子实施顺序

| 顺序 | ID | 唯一结果 | 退出证据 |
|---:|---|---|---|
| 1 | T5.0.1 | 六源 evaluator/fixture/artifact/release 只读审计 | QL-01–26；ENGINEERING_PASS |
| 2 | T5.1.1 | SourceEvaluationUniverseV1 | G4 membership/auth/generation/tombstone verify |
| 3 | T5.1.2 | global RootProvenanceRegistryV1 | alias/export/cross-source duplicate tests |
| 4 | T5.1.3 | SourceEvaluationCaseV2 + protected/portable projection | canonical/ACL/leakage/tamper tests |
| 5 | T5.2.1 | annotation policy/roles/packet | blind/double-label/unknown/adjudication tests |
| 6 | T5.2.2 | annotation agreement/correction ledger | disagreement/correction/no-retroactive-pass |
| 7 | T5.3.1 | FrozenEvaluationSplitManifestV1 | group/time/slice/overlap property tests |
| 8 | T5.3.2 | sealed test store + access ledger | role/replay/unauthorized/read-once tests |
| 9 | T5.4.1 | MetricObservationV2 + shared scorer | denominator/availability/CI/root weighting |
| 10 | T5.4.2 | Code真实50-case scorer/Gate | locator/version/graph/history/security |
| 11 | T5.4.3 | Codex真实45-case scorer/Gate | sequence/goal/failure/privacy |
| 12 | T5.4.4 | Experiment真实45-case scorer/Gate | numeric/unit/compare/reproduce/version |
| 13 | T5.4.5 | Notebook真实40-case scorer/Gate | cell/output/order/stale/path |
| 14 | T5.4.6 | Document真实50-case scorer/Gate | page/table/figure/citation/claim/version |
| 15 | T5.4.7 | Workspace真实40-case scorer/Gate | current/as-of/blocker/dependency/ACL |
| 16 | T5.5.1 | CalibrationProfileV2 fit/apply registry | key/sample/monotonic/tamper/drift tests |
| 17 | T5.5.2 | PreRegisteredSourceGatePolicyV1 | pre-test signing/threshold/missing policy tests |
| 18 | T5.6.1 | paired baseline/candidate runner | same snapshot/budget/order/all attempts |
| 19 | T5.6.2 | six source quality cards + portable package | metrics/slices/CI/calibration/security/limitations |
| 20 | T5.7.1 | independent per-source review + G5 aggregate | six decisions all QUALIFIED or exact HOLD/FAIL |

## 15. G5 验证矩阵（SQ-01–SQ-54）

### 15.1 Universe/split/leakage（SQ-01–12）

| Case | Hard exit |
|---|---|
| SQ-01 valid G4 auth/corpus/generation universe | exact PASS |
| SQ-02 stale/revoked/tombstoned universe | zero split；HOLD |
| SQ-03 same root aliases in different splits | rejected |
| SQ-04 cross-source export of same root across splits | rejected |
| SQ-05 time-blocked source future leakage | rejected |
| SQ-06 critical slice below floor | `INSUFFICIENT_TEST_COVERAGE` |
| SQ-07 case question leaks target ID/path unintentionally | quarantine |
| SQ-08 evaluator input contains Gold/label/review metadata | hard fail |
| SQ-09 portable package contains protected query/locator/content | leakage=0 |
| SQ-10 split reorder/copy | same digest |
| SQ-11 semantic membership change with same version | tamper fail |
| SQ-12 deleted case after split freeze | authority invalidated; successor required |

### 15.2 Annotation（SQ-13–20）

| Case | Hard exit |
|---|---|
| SQ-13 two independent annotations agree | agreement counted |
| SQ-14 disagreement retained and adjudicated | no silent majority/delete |
| SQ-15 unknown/ambiguous/authority unavailable | typed, not forced positive/negative |
| SQ-16 annotator sees candidate rank/score | annotation invalid |
| SQ-17 unauthorized annotator/source access | zero content leak + incident |
| SQ-18 numeric/unit/version derivation recompute | exact |
| SQ-19 correction after candidate output | old decision preserved; new split version |
| SQ-20 reviewer role collision | qualification HOLD |

### 15.3 Metric/runner（SQ-21–32）

| Case | Hard exit |
|---|---|
| SQ-21 metric canonical/tamper/recompute | exact |
| SQ-22 empty denominator | UNAVAILABLE/NA, never 100% |
| SQ-23 timeout/error/no-match/unauthorized | distinct outcomes/counts |
| SQ-24 case/root/micro/macro denominator parity | exact |
| SQ-25 root-cluster CI deterministic seed | reproducible |
| SQ-26 baseline/candidate snapshot mismatch | run invalid |
| SQ-27 budget/cache/order mismatch | run invalid |
| SQ-28 first test attempt fails after labels read | attempt retained |
| SQ-29 unauthorized selective case rerun | rejected |
| SQ-30 approved infra full rerun same bytes | both attempts disclosed |
| SQ-31 best-of-N attempt selection | rejected |
| SQ-32 test result used to alter threshold then same test reused | polluted test; successor required |

### 15.4 Calibration（SQ-33–40）

| Case | Hard exit |
|---|---|
| SQ-33 fit only calibration split | PASS |
| SQ-34 test/train observation enters fit | rejected |
| SQ-35 profile positive/negative/root below floor | UNAVAILABLE |
| SQ-36 profile source/task/entity mismatch | apply rejected |
| SQ-37 retriever/index/reranker/version drift | apply rejected |
| SQ-38 ECE/Brier/bin denominator recompute | exact |
| SQ-39 test-time rebin/refit | rejected |
| SQ-40 uncalibrated score named relevance_probability | contract failure |

### 15.5 六源 hard slices（SQ-41–48）

| Case | Hard exit |
|---|---|
| SQ-41 Code wrong-version/locator/graph/history | each critical floor PASS |
| SQ-42 Codex event/failure/validation/privacy | each critical floor PASS |
| SQ-43 Experiment numeric/unit/compare/reproduce/version | each critical floor PASS |
| SQ-44 Notebook cell/output/order/stale/artifact | each critical floor PASS |
| SQ-45 Document locator/table/figure/citation/claim/version | each critical floor PASS |
| SQ-46 Workspace current/as-of/blocker/dependency/ACL | each critical floor PASS |
| SQ-47 any source ACL/secret/privacy/unsafe mutation violation | source FAIL |
| SQ-48 combined average passes while one source fails | aggregate FAIL |

### 15.6 Artifact/review（SQ-49–54）

| Case | Hard exit |
|---|---|
| SQ-49 protected artifact完整、portable aggregate无敏感内容 | PASS |
| SQ-50 source quality card binds candidate/split/policy/run/profile | exact |
| SQ-51 copy/native verifier/checksum | PASS |
| SQ-52 expired/revoked reviewer receipt | HOLD |
| SQ-53 reviewer recomputes all metrics/CI/Gate | parity |
| SQ-54 six exact decisions | all QUALIFIED or G5 not qualified |

## 16. Artifact 与 Source Quality Card

```text
artifacts/rag-maturity/g5/<run-id>/
├── manifest.json
├── environment.json
├── authorization-and-generation.json
├── universe.json
├── root-provenance.json
├── split-manifest.json
├── annotation-policy.json
├── annotation-agreement.json
├── test-seal-and-access-ledger.json
├── candidates.json
├── gate-policies.json
├── attempts.jsonl
├── metrics.json
├── slices.json
├── calibration-profiles.json
├── calibration-test.json
├── security.json
├── failures.jsonl
├── source-quality-cards.json
├── reviewer-decisions.json
├── limitations.json
└── checksums.json
```

每张 `SourceQualityCardV1` 至少报告数据/root/case、candidate、baseline、global/slice metrics、CI、failure
taxonomy、calibration availability/ECE/Brier、security、bound latency/cost、known limitations、validity window、
invalidation conditions和review decision。不能只展示一个macro score。

## 17. Hard Gate 与停止条件

### 17.1 G5 hard exits

1. 六源各自满足真实test case和distinct root floors；
2. train/calibration/test root overlap=0，test access无违规；
3. annotation独立、分歧/unknown/修正完整，critical slice Gold authority可证；
4. evaluator/metric/denominator/CI可从case outcome重算；
5. 每源global与critical slice绝对floor和paired policy全部PASS；
6. wrong-version、ACL、secret/privacy、unsafe mutation、unsupported false positive违反数=0；
7. active calibration keys全部有匹配profile，test ECE/Brier/coverage过预注册门；
8. 所有attempt、infra invalidation和test access完整披露；
9. portable package无protected内容且native verify通过；
10. 六个独立source reviewer decisions均为QUALIFIED。

### 17.2 立即停止

发现split/root泄漏、test label提前访问、Gold进入evaluator input、实现者自标自审、test上调阈值、挑最好
attempt、source generation drift、ACL/secret leak、case deletion未级联、portable content泄漏、任一来源用
联合分数补偿时，立即停止并保留artifact。默认V1不变；不自行编辑release registry或重新命名为PASS。

## 18. 当前目标重设

当前完成 `WP-G5D-01 / T5.0.1`：冻结六源fixture/evaluator/release事实、QL-01–26、真实Universe、
root-grouped split、annotation firewall、MetricObservationV2、CalibrationProfileV2、20项实施序列和
SQ-01–54。本结果为`DESIGN_ENGINEERING_PASS`，不是source quality pass。

严格关键路径：

```text
G0→G4 QUALIFIED
  → T5.1 real universe/root registry
  → T5.2 blind annotation/adjudication
  → T5.3 sealed split/test firewall
  → T5.4 six real source scorers
  → T5.5 calibration + pre-registered gates
  → T5.6 paired runs/cards
  → T5.7 six independent decisions
```

G4前允许继续G6–G9只读设计与fixture contract tests；禁止创建伪真实Gold、读取正式数据、生成自签review、
拟合/查看test、改变route、执行shadow/canary或发布。
