# G6 真实多源、Wiki 导航、原文回读与答案质量可执行规格

版本：2026-08-06 V1  
状态：`G6D DESIGN_ENGINEERING_PASS / G5_GATE_HOLD / REAL_JOINT_GOLDEN_MISSING / QUALITY_RUN_NOT_STARTED`  
上位目标：`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`  
数据前置：`37_G4_SIX_SOURCE_CORPUS_MATERIALIZATION_BACKFILL_EXECUTION_SPEC.md`  
质量前置：`38_G5_SIX_SOURCE_REAL_QUALITY_CALIBRATION_EXECUTION_SPEC.md`  
默认发布状态：`DEFAULT_V1 / QUALITY_HOLD`

## 0. 本规格解决什么问题

仓库已经具备六源 planner、source-local retriever、calibration、role-aware fusion、typed graph、
EvidencePack、答案引用、Wiki build/search/navigation/raw-read 和离线 evaluator。现有代码还冻结了 60 条
多源 Golden 与 120 条 Wiki Golden，工程质量包可以被 native verifier 重算。这证明执行合同和失败语义
已经相当完整，但不能证明真实联合问答成熟：两组 Golden 都由程序生成，输入实体、答案义务和期望 ID
共享同一个生成逻辑；Wiki runner 又为每个 case 构造满分 candidate/page/fact，并在没有独立 claim 审查的
情况下写入 `unsupported_claim_count=0`。

本规格冻结 G6 唯一目标：在 G4 owner-authorized generation 和 G5 六源独立质量 Gate 上，用与训练、
校准隔离的真实联合 blind test，分别证明 planner、路由、单源候选、跨源融合、typed graph、Wiki 导航、
原文回读、EvidencePack、逐声明答案与拒答；通过受控单变量消融说明每一层真实增益，并由独立人审和经过
校准的 judge 复核。任一层失败都不得被最终答案总体分数掩盖。

本轮只完成代码/artifact 只读审计、真实联合数据/标注/指标/运行/消融/审查合同和实施顺序。没有访问
正式数据库，没有读取真实内容，没有创建 Gold answer，没有运行真实联合测试，没有改变 V1/V2 路由。

## 1. Entry、Exit 与 authority 边界

### 1.1 G6 runtime Entry

以下全部满足才允许解封真实 joint test：

1. G0–G5 均有 exact、未过期、未撤销的 `QUALIFIED` decision；
2. `ActiveGenerationSetV1`、六个 source quality candidate、calibration profile 与 test split完全绑定；
3. G5 test roots不被复用为G6 train/calibration roots；同一root的跨源投影保持同一split；
4. 数据授权允许跨源关联、Wiki derived projection、raw read、答案标注、人审和portable aggregate；
5. planner/fusion/Wiki/answer candidate、模型、prompt、预算、cache、seed、并发和failure policy已冻结；
6. joint Gold annotator、answer reviewer、judge calibrator、test custodian和release reviewer角色分离；
7. test labels与Gold answer对开发者、prompt作者、ranker作者不可见；
8. baseline、candidate和所有ablation在同一snapshot、query order、预算、执行环境下成对运行。

### 1.2 G6 `QUALIFIED` 的含义

G6只有下列七层全部通过才可 `QUALIFIED`：

- planner/route：选对来源、任务、角色、版本和as-of；
- retrieval/fusion：召回正确实体、路径、反证、冲突和多版本证据；
- Wiki：正确构建、搜索、导航、停止并回读raw authority；
- evidence：证据义务完整、无ACL泄漏、无重复root伪增益；
- answer：每个可验证claim都被正确、完整、精确的raw citation支持；
- abstention：证据不足、来源失败、授权不足和冲突未决时正确拒答或部分回答；
- evidence authority：盲评、judge calibration、消融和artifact可独立重算。

G6 PASS不授权shadow、canary、正式流量或默认V2，这些属于G8/G9。

## 2. 当前代码与 artifact 事实

### 2.1 多源工程基线

1. `multisource_foundation_v2.py` 在源码中构造固定60条case，question为
   `Frozen cross-source question ...`，required entity/version/forbidden ID也按index生成；
2. 11个slice分别为8/8/7/6/5/6/6/4/4/3/3条，覆盖2源、3源、4–6源、版本冲突、来源失败和ACL；
3. fault injection按固定cycle生成，包括timeout、partial、stale calibration、wrong generation、snapshot
   mismatch、duplicate root、reranker unavailable、graph cycle和ACL denied；
4. evaluator有15项指标：route precision/recall、role coverage、entity/path/counter/version、citation、
   decision、unanswerable、capability violation、timeout honesty和unauthorized leakage；
5. evaluator的阈值写在实现中，`qualified`由这些固定阈值直接计算；
6. evidence bundle允许记录`production_observation`，但仓库没有真实多源quality/ablation artifact；
7. 这套60 case适合contract/fault回归，不是owner-authorized真实联合问题或独立Gold答案。

### 2.2 Wiki工程基线

1. `evaluation_v1.py` 在源码中生成120 case、10个slice，query、path、raw ref和义务都由case id拼接；
2. `quality_runner_v1.py::_fixture()`为每条case构造calibrated relevance=1.0的candidate、page、fact、link；
3. runner在临时SQLite中stage/publish该fixture，再通过真实Navigator执行，适合验证导航合同；
4. answer row的`unsupported_claim_count`直接写0，不来自真实答案claim抽取或独立review；
5. v3 artifact报告120/120 membership、ACL/unsupported/refusal hard gate通过，且明确
   `production_authorized=false`、`ENGINEERING_FIXTURE_NON_QUALIFIED`、`QUALITY_HOLD`；
6. artifact包含golden/rows/executions/overall/slices/security/checksums，可复现性良好但外部效度不足。

### 2.3 可复用资产

- canonical hash、immutable model、完整membership与denominator校验；
- typed source status、planning gap、role obligation、version alignment和fault taxonomy；
- Wiki search/read/follow/raw-read预算与stop reason；
- EvidencePack decision、citation binding、source generation和ACL字段；
- slice evaluator、portable security scan与native artifact verifier；
- 默认V1和`production_authorized=false`的fail-closed边界。

## 3. G6 差距与处置清单

| ID | 当前事实 | 风险 | G6处置 |
|---|---|---|---|
| MJ-01 | 60 case由源码生成 | 实现与truth同源 | 创建独立真实`JointEvaluationUniverseV1` |
| MJ-02 | question/ID/version为模板串 | 指标只测fixture匹配 | 真实任务问题，去标识safe text或protected ref |
| MJ-03 | 120 Wiki case由源码生成 | 导航可记住生成规则 | 独立真实Wiki任务与路径truth |
| MJ-04 | Wiki runner构造完美候选 | 不测真实build/index/retrieval | 从G4 active generations端到端构建 |
| MJ-05 | unsupported count固定0 | 不证明答案faithfulness | claim segmentation + independent citation judgment |
| MJ-06 | 多源evaluator没有Gold answer文本/claim图 | citation分数可由自报分子分母产生 | `JointGoldAnswerV1`与`ClaimJudgmentV1` |
| MJ-07 | route truth只有required source集合 | 忽略可接受替代、代价和不必要来源 | required/optional/forbidden source与route utility |
| MJ-08 | role集合不表达每个claim义务 | role coverage不等于答案完整 | claim→obligation→evidence可追踪映射 |
| MJ-09 | entity recall只看ID | 同root重复/别名可虚增 | root-aware gain与duplicate-root penalty |
| MJ-10 | path recall只看模板名称 | 不验证真实边/方向/时间 | typed edge/path Gold与hop级判断 |
| MJ-11 | version accuracy按selected逐项平均 | 错误关键版本可被大量NA稀释 | target-version critical gate与wrong-version=0 |
| MJ-12 | conflict只用potentially_stale | 不覆盖相互矛盾、权威优先级、未决 | typed conflict truth与disclosure/resolution |
| MJ-13 | 失败注入与真实错误混合 | 无法分离能力/可靠性 | natural failure与controlled injection分层报告 |
| MJ-14 | Wiki build、search、navigation合并 | 无法定位失败层 | 五层独立outcome与end-to-end AND |
| MJ-15 | raw binding只看ID | 可能回读derived/candidate envelope | raw authority receipt重算hash/version/ACL |
| MJ-16 | stop metric只检查既定字段 | 过早或过度搜索不被完整量化 | obligation-aware optimal/acceptable stop envelope |
| MJ-17 | 答案正确性没有数值/单位/时间/关系维度 | 引用存在但语义可能错 | typed claim correctness metrics |
| MJ-18 | judge未校准 | LLM judge偏差可能决定Gate | 人工Gold子集校准、阈值/偏差/置信区间 |
| MJ-19 | 无盲双评和分歧裁决 | 单reviewer偏差不可见 | blind dual review + adjudication |
| MJ-20 | 无正式ablation artifact | 不能归因planner/graph/wiki增益 | M-B0–M-B9单变量paired runs |
| MJ-21 | baseline与candidate配置未统一冻结 | 改了多个变量仍声称单项收益 | `AblationTreatmentManifestV1` |
| MJ-22 | 平均分可掩盖关键slice | 安全/冲突/拒答失败仍总体高 | global+critical slice hard gates |
| MJ-23 | test重复查看未治理 | prompt/ranker可过拟合 | G5 test firewall延伸至joint test |
| MJ-24 | portable包可能含问题/答案/locator | 跨源包扩大敏感面 | protected evidence与portable aggregate分层 |
| MJ-25 | deletion/撤权未联动joint truth | 过期Gold继续授权 | case/answer/judgment/decision级联失效 |
| MJ-26 | 未证明真实零泄漏 | fixture安全不能外推 | owner-authorized ACL/secret/privacy adversarial cases |

## 4. 不可变原则

1. G5 source test与G6 joint test按root隔离；不能把已调过的单源test换个问题继续用。
2. 一条joint case必须至少需要两个来源；单源问题留在G5。
3. question先冻结，再由独立标注者找证据；不能由candidate top results反推truth。
4. Gold定义可接受的多个证据集合和路径，不能只接受一组偶然ID。
5. required、optional、forbidden来源和角色分别标注。
6. planner不必要扩源同时计质量和成本损失；少路由与多路由都不可忽略。
7. retrieval、fusion、Wiki、raw read和answer各自输出typed outcome，不能只留最终文本。
8. candidate/derived Wiki不是raw authority；最终可验证claim必须能回读原文并重算身份。
9. 每个claim独立评分；一个引用不能自动支持整段答案。
10. citation precision、citation completeness、claim support和claim correctness是四个不同指标。
11. 数值、单位、时间、比较、因果、否定与关系claim需要类型化校验。
12. conflict case必须展示双方证据、版本/authority与未决状态；静默选边为失败。
13. 证据不足、ACL不足、来源失败或时间点不可证时，拒答/部分回答优于编造。
14. unknown/ambiguous Gold保留并报告，不强迫为支持或不支持。
15. 自动judge不得单独决定安全、unsupported、wrong-version或ACL hard gate。
16. judge必须先在人审Gold子集上预注册并通过校准，且版本/prompt/temperature固定。
17. 人审看不到candidate名称、baseline/treatment标识和ranker版本。
18. test访问、失败attempt、重跑与标注修正全部进ledger。
19. ablation一次只改变一个声明中的组件，其余bytes或canonical config必须相同。
20. ablation不允许减少预算、改变模型或换snapshot来制造差异。
21. global PASS不能覆盖任何critical slice、安全或完整membership失败。
22. artifact报告case/root/claim/citation四层denominator和unavailable。
23. 删除、撤权、generation失效会级联使case、judgment、run和review失效。
24. G6 qualification只产生offline candidate authority；不会自动触发流量。

## 5. 真实联合 Evaluation Universe

### 5.1 `JointEvaluationUniverseV1`

```text
universe_id / revision / project_id / environment
authorization_sha256
g4_active_generation_set_sha256
g5_source_quality_decision_sha256[6]
g5_calibration_profile_set_sha256
snapshot_at / as_of_range
eligible_root_groups[]
  root_group_id
  source_memberships[]
  cross_source_relation_candidates[]
  acl_class / sensitivity_class
  version_range / deletion_watermark
ineligible_root_groups[] reason_code
sampling_frame / coverage_summary
content_sha256
```

Universe只列可抽样根与关系候选，不含Gold outcome。跨源关系必须能由原文独立验证，例如代码变更↔会话
决策↔实验结果↔notebook复现↔论文claim↔workspace工作项；单纯同名不等于关系。

### 5.2 `JointEvaluationCaseV2`

```text
case_id / split_id / root_group_ids[]
question_protected_ref / question_safe_sha256
intent / complexity / answerability
scope / requester_acl_class / as_of
required_sources[] / optional_sources[] / forbidden_sources[]
required_roles[] / optional_roles[]
required_entities_or_alternatives[]
required_typed_paths_or_alternatives[]
conflict_truth_ref / refusal_truth_ref
gold_answer_ref / annotation_set_sha256
eligible_metrics[] / critical_slice_tags[]
content_sha256
```

不得把Gold IDs、path、labels、review note放入runner输入。runner只看到question、scope、ACL、as-of和冻结预算。

### 5.3 test最低规模

G6正式test不少于60条真实joint cases、30个distinct root groups，并满足：

| Slice | 最低case | 最低root | 关键要求 |
|---|---:|---:|---|
| 2-source direct/bridge | 15 | 8 | 至少覆盖全部六源作为一端 |
| 3-source causal/reproduction | 12 | 6 | entity+path+role完整 |
| 4–6-source synthesis | 8 | 4 | 不是简单并列拼接 |
| conflict/counter-evidence | 8 | 5 | 双方证据与authority/version |
| as-of/wrong-version/stale | 7 | 4 | target version明确 |
| unanswerable/source failure | 5 | 4 | 正确拒答/部分回答 |
| ACL/privacy/adversarial | 5 | 4 | 零泄漏 |

slice可重叠，但exact membership、overlap矩阵和每项分母必须报告。任何critical slice低于floor即
`INSUFFICIENT_JOINT_TEST_COVERAGE`，不得按总体60条宣称成熟。

## 6. 独立联合标注合同

### 6.1 标注包

标注者使用独立evidence browser，可按授权访问raw source，但不可见任何candidate输出、rank、score、route
或版本名。每条case至少两名独立标注者；冲突、ACL、因果、数值和高风险case强制领域裁决。

### 6.2 `RouteJudgmentV1`

记录required/optional/forbidden source、source task、required evidence role、可接受替代及“不需要扩源”的原因。
路由正确不是仅看required recall，还要报告precision、overreach和每个遗漏义务。

### 6.3 `EvidenceSetJudgmentV1`

```text
judgment_id / case_id
acceptable_evidence_sets[]
  raw_authority_ids[]
  source_roles[]
  root_groups[]
  target_versions[]
  typed_paths[]
required_counter_evidence[]
forbidden_or_unsafe_evidence[]
completeness_notes_protected_ref
annotator_receipts[] / adjudication_receipt
content_sha256
```

### 6.4 `JointGoldAnswerV1`与`ClaimJudgmentV1`

Gold answer不是唯一文本字符串，而是claim graph：

```text
claim_id / case_id / claim_type
canonical_meaning / acceptable_paraphrase_policy
required_support_sets[] / counter_support_sets[]
numeric_value / unit / tolerance
valid_time / target_version
relation_subject / predicate / object
answerability / required_disclosure
criticality / eligible_metrics
annotation_agreement / adjudication
content_sha256
```

claim type至少含 factual、numeric、unit、temporal、comparison、causal、negative、relational、limitation、
conflict disclosure与refusal rationale。

## 7. 冻结候选与运行合同

### 7.1 `JointQualityCandidateV1`

必须绑定：

- candidate id/revision/source commit/admission manifest；
- six active generations + G5 quality/calibration decisions；
- planner/capability/router/query rewrite版本；
- six retriever/index/reranker/context versions；
- fusion/entity resolution/typed graph/conflict policy；
- Wiki builder/store/search/navigator/raw verifier/stop policy；
- EvidencePack/answer model/prompt/decoding/claim segmenter/citation binder；
- ACL/secret/redaction policy；
- per-source、Wiki和answer token/time/search/read budgets；
- cache mode、seed、concurrency、environment image、dependency lock；
- failure/timeout/retry/partial-answer policy。

任一字段变化都是新candidate，不得覆盖旧run。

### 7.2 `JointEvaluationRunV1`

每case保存阶段化receipt：

```text
case_receipt
  input_sha256 / execution_order
  plan_receipt
  source_execution_receipts[6]
  candidate_set_receipt
  fusion_receipt / graph_receipt
  wiki_build_snapshot / navigation_receipt
  raw_read_receipts[]
  evidence_pack_receipt
  generated_answer_protected_ref
  claim_segmentation_receipt
  automated_judgments[]
  human_review_receipts[]
  latency_cost_resource_receipt
  failure_outcome
```

所有阶段使用同一plan/snapshot身份。缺一个receipt不是0分结果，而是typed `UNAVAILABLE/ERROR`并计入coverage。

## 8. 分层指标体系

### 8.1 Planner/route层

- source route precision/recall/F1；
- required/optional/forbidden source accuracy；
- task accuracy、required role recall、obligation coverage；
- unnecessary source rate与source miss rate；
- as-of/scope/ACL propagation accuracy；
- planner capability violation=0。

### 8.2 Retrieval/fusion/graph层

- root-aware entity recall@20、nDCG@20和duplicate-root rate；
- required evidence set recall、counter-evidence recall；
- typed path recall/precision、hop direction、edge authority和cycle safety；
- target version accuracy、wrong-version critical count、stale evidence rate；
- conflict discovery/disclosure/resolution accuracy；
- calibrated role utility与source balance；
- timeout/partial/unavailable truthfulness。

### 8.3 Wiki层

Wiki必须分别报告：

1. build：source coverage、fact/link/source-ref lineage、generation一致性；
2. search：page/path/source/root recall与forbidden leakage；
3. navigation：path recall、hop correctness、义务完成、loop/duplicate action；
4. raw read：raw authority成功率、hash/version/ACL匹配、derived-as-raw=0；
5. stop：evidence complete时停止、未完成不提前停止、预算耗尽诚实性；
6. end-to-end：上述五层AND，不用最终page hit替代。

### 8.4 Evidence与答案层

每个系统claim映射到0..N条citation，再由人审/校准judge判断：

- claim identification recall；
- claim support precision与support completeness；
- citation precision、citation completeness和locator correctness；
- raw authority coverage；
- unsupported claim rate和critical unsupported count；
- numeric/unit/temporal/version/relational/causal correctness；
- conflict disclosure与limitation completeness；
- answer relevance、conciseness和instruction adherence；
- unanswerable/refusal/partial-answer accuracy；
- answer coverage，明确区分未评、judge unavailable和generation error。

### 8.5 聚合规则

同时发布case-micro、claim-micro、root-weighted和case-macro；先报告各层，再报告end-to-end AND。禁止只发布
一个“答案正确率”或加权总分。所有指标含numerator、denominator、eligible、evaluated、unavailable、方向、
CI和critical slice。

## 9. 人工盲评与 judge 校准

### 9.1 Blind review

baseline/candidate/ablation答案随机化和去标识；review UI不展示系统名称、排序分、latency或其他reviewer结果。
至少20% test case双人复核，所有critical case双人复核。分歧不得删除，交独立adjudicator。

### 9.2 `JudgeCalibrationReportV1`

自动judge只能在独立人审Gold子集上启用，报告：

- judge model/prompt/decoding/schema/version；
- 人审Gold membership、root隔离和类别分布；
- claim support/correctness/conflict/refusal的precision、recall、F1；
- calibration curve、ECE/Brier、混淆矩阵与slice偏差；
- abstain率、解析失败率、重试率和成本；
- 预注册acceptance threshold与允许使用范围。

安全、ACL、secret、critical unsupported、wrong-version与删除传播必须由确定性检查或人审确认；judge只能
辅助，不能单独把FAIL改成PASS。judge不达门时相关指标`UNAVAILABLE`，不会回退自评。

## 10. M-B0–M-B9 单变量消融

### 10.1 固定条件

所有treatment共享exact query、split、generation set、candidate模型、上下文/输出预算、cache策略、执行顺序、
seed和environment。若组件会改变候选数量，则在manifest中预注册等价budget规则，不能事后调参。

| ID | 唯一变化 | 对照 | 主要归因指标 |
|---|---|---|---|
| M-B0 | 当前统一检索真实baseline | default V1 | end-to-end基线 |
| M-B1 | 六源独立retriever | B0 | source/entity/root recall |
| M-B2 | source-local calibration | B1 | nDCG、role utility、ECE |
| M-B3 | source routing | B2全源固定检索 | route F1、cost、latency |
| M-B4 | required role obligations | B3无role约束 | evidence/claim completeness |
| M-B5 | typed graph traversal | B4无graph | path/counter/causal recall |
| M-B6 | version/conflict policy | B5 | wrong-version/conflict disclosure |
| M-B7 | source context + raw read | B6 derived-only | raw coverage、citation、faithfulness |
| M-B8 | corrective retrieval | B7单轮 | missing role recovery、cost |
| M-B9 | cross-source reranker | B8无rerank | nDCG、answer quality |

### 10.2 `AblationTreatmentManifestV1`

每一对run保存baseline/candidate config hash、allowed delta JSON pointer、unexpected deltas、paired membership、执行
receipts和statistical plan。unexpected delta非空即run invalid。报告paired root bootstrap CI、effect size、win/tie/
loss、regression slices和成本变化；不能只报平均提升。

## 11. 预注册 Gate

数值阈值由独立quality owner在test解封前写入`PreRegisteredJointGatePolicyV1`。最低语义要求：

1. planner route/role/as-of/ACL均满足global和critical slice floor；
2. entity/path/counter/version/conflict均满足floor，critical wrong-version count=0；
3. Wiki五层各自PASS，raw authority substitution=0；
4. citation precision与completeness分别过门；critical claim completeness=1.0；
5. unsupported critical claims=0，ACL/secret/privacy leakage=0；
6. numeric/unit/time/relation/conflict correctness各自过门；
7. refusal/partial answer在unanswerable/source failure/ACL slice过门；
8. evaluator/judge/human coverage达到预注册floor；
9. candidate相对B0 non-inferior，且预注册关键目标存在可信改善；
10. M-B1–B9无未解释critical regression或预算越界。

任何阈值在看到test结果后修改，必须新建candidate和未污染successor test。

## 12. 20项原子实施序列

| # | Work package | 交付 | 完成证据 |
|---:|---|---|---|
| 1 | T6.0.1 | 当前多源/Wiki/evaluator/artifact审计 | MJ-01–26逐项有代码证据 |
| 2 | T6.0.2 | G6 policy/owner/role matrix | Entry、SoD、stop签署 |
| 3 | T6.1.1 | JointEvaluationUniverseV1 | G4/G5/root/auth绑定 |
| 4 | T6.1.2 | real joint sampling plan | ≥60 cases/≥30 roots/slice floors |
| 5 | T6.1.3 | protected case store + test seal | label leakage=0 |
| 6 | T6.2.1 | RouteJudgmentV1 annotation UI | candidate blind |
| 7 | T6.2.2 | EvidenceSetJudgmentV1 | alternatives/counter/forbidden完整 |
| 8 | T6.2.3 | Gold answer/claim graph | typed claims/obligations/citations |
| 9 | T6.2.4 | dual review/adjudication | agreement/correction ledger |
| 10 | T6.3.1 | frozen JointQualityCandidateV1 | all component/config hashes |
| 11 | T6.3.2 | stage receipt runner | typed stage outcome/membership |
| 12 | T6.3.3 | real Wiki build/search/navigation runner | no synthetic candidates/pages |
| 13 | T6.3.4 | raw authority verifier | hash/version/ACL/read receipt |
| 14 | T6.4.1 | claim segmenter + deterministic scorers | claim/citation/type metrics重算 |
| 15 | T6.4.2 | blind human review workflow | de-identified randomized packets |
| 16 | T6.4.3 | judge calibration | human parity/bias/abstain report |
| 17 | T6.5.1 | B0 and M-B1–B9 paired runner | exact allowed delta checks |
| 18 | T6.5.2 | statistical/slice/cost analysis | root CI、effect、regression |
| 19 | T6.6.1 | portable/protected quality package | native verify/security/checksums |
| 20 | T6.6.2 | independent review + G6 decision | exact QUALIFIED or HOLD/FAIL |

## 13. G6 验证矩阵（MJ-01–MJ-54）

### 13.1 Universe/split（MJ-01–10）

| Case | Hard exit |
|---|---|
| MJ-01 valid G4/G5/auth/generation universe | exact PASS |
| MJ-02 G5未全六源QUALIFIED | joint test不解封 |
| MJ-03 G5与G6同root跨split | rejected |
| MJ-04 同root跨源投影被拆split | rejected |
| MJ-05 joint case只需要单源 | 移交G5，不计G6 |
| MJ-06 critical slice低于case/root floor | HOLD |
| MJ-07 Gold ID/path泄漏runner | case quarantine |
| MJ-08 tombstoned/revoked root | authority invalidated |
| MJ-09 protected query进入portable包 | leakage fail |
| MJ-10 membership/hash reorder/tamper | native verify fail |

### 13.2 Annotation/Gold（MJ-11–20）

| Case | Hard exit |
|---|---|
| MJ-11 dual route judgment agree | agreement计入 |
| MJ-12 source optional/forbidden分歧 | adjudication required |
| MJ-13 multiple valid evidence sets | 均被scorer接受 |
| MJ-14 counter-evidence遗漏 | Gold incomplete/HOLD |
| MJ-15 numeric/unit/time derivation | independent recompute |
| MJ-16 causal claim只有相关性证据 | 不得标causal supported |
| MJ-17 conflict无法裁决 | 标unresolved并要求disclosure |
| MJ-18 annotator看到rank/system id | annotation invalid |
| MJ-19 post-result label correction | successor dataset/new decision |
| MJ-20 reviewer/implementer角色冲突 | qualification HOLD |

### 13.3 Planner/fusion/Wiki（MJ-21–34）

| Case | Hard exit |
|---|---|
| MJ-21 exact required route | precision/recall正确 |
| MJ-22 不必要全源路由 | overreach与成本记错即fail |
| MJ-23 required role missing | end-to-end不得PASS |
| MJ-24 alias/duplicate same root | 只计一次gain |
| MJ-25 wrong target version selected | critical fail |
| MJ-26 counter-evidence被authority排序压掉 | conflict gate fail |
| MJ-27 graph edge方向/类型错误 | path不计中 |
| MJ-28 graph cycle/预算耗尽 | typed stop，无静默成功 |
| MJ-29 Wiki build漏source ref/lineage | build fail |
| MJ-30 Wiki search命中但navigation漏义务 | navigation fail |
| MJ-31 premature stop | stop gate fail |
| MJ-32 完整证据后继续过度搜索 | efficiency regression |
| MJ-33 derived/candidate冒充raw | raw authority fail |
| MJ-34 raw hash/version/ACL不匹配 | zero citation authority |

### 13.4 Answer/judge（MJ-35–44）

| Case | Hard exit |
|---|---|
| MJ-35 claim segmentation漏critical claim | completeness fail |
| MJ-36 citation存在但不蕴含claim | support precision fail |
| MJ-37 一个citation错误支持多claim | 逐claim独立判断 |
| MJ-38 数值对但单位错 | numeric-unit fail |
| MJ-39 当前事实引用历史版本 | temporal/version fail |
| MJ-40 冲突存在但答案静默选边 | disclosure fail |
| MJ-41 证据不足仍给确定答案 | unsupported hard fail |
| MJ-42 ACL不足时引用可推断私有内容 | leakage hard fail |
| MJ-43 judge不达校准门 | judge metrics UNAVAILABLE |
| MJ-44 judge与人审critical分歧 | 以adjudicated human为准并调查 |

### 13.5 Ablation/run/artifact（MJ-45–54）

| Case | Hard exit |
|---|---|
| MJ-45 B0/B1 snapshot或membership不同 | paired run invalid |
| MJ-46 treatment有非预期config delta | invalid |
| MJ-47 预算/model/prompt随ablation变化 | invalid |
| MJ-48 best-of-N或隐藏失败attempt | qualification fail |
| MJ-49 root bootstrap与case bootstrap混淆 | report fail |
| MJ-50 macro提升但critical slice回归 | HOLD |
| MJ-51 quality提升但成本越预注册guard | HOLD |
| MJ-52 protected/portable artifacts分层 | exact PASS |
| MJ-53 copy/native/checksum/recompute | PASS |
| MJ-54 independent exact review decision | QUALIFIED或保持HOLD |

## 14. Artifact 包

```text
artifacts/rag-maturity/g6/<run-id>/
├── manifest.json
├── environment.json
├── authorization-generation-quality-bindings.json
├── universe.json
├── split-manifest.json
├── test-seal-access-ledger.json
├── annotation-policy.json
├── annotation-agreement.json
├── candidates.json
├── gate-policy.json
├── attempts.jsonl
├── stage-receipts.jsonl
├── planner-route-metrics.json
├── retrieval-fusion-graph-metrics.json
├── wiki-layer-metrics.json
├── raw-read-verification.json
├── claim-citation-metrics.json
├── human-review.json
├── judge-calibration.json
├── ablation-manifests.json
├── ablation-results.json
├── slices.json
├── latency-cost.json
├── security.json
├── failures.jsonl
├── reviewer-decision.json
├── limitations.json
└── checksums.json
```

portable包只含不可逆ID、aggregate、版本、hash、计数、指标、failure code和decision；真实question、answer、raw
locator、review note留在protected store，并通过content hash和authorized read receipt关联。

## 15. Hard Gate 与停止条件

### 15.1 G6 hard exits

1. 真实joint test达到case/root/critical slice floors且与G5 test无root泄漏；
2. planner、retrieval/fusion/graph、Wiki五层、raw read、evidence、answer和refusal分别PASS；
3. critical wrong-version、unsupported claim、ACL/secret/privacy leakage、derived-as-raw均为0；
4. citation precision/completeness、raw coverage、typed claim correctness达到预注册门；
5. blind human review完整，judge通过校准且未越权替代hard gate；
6. B0与M-B1–B9均是合法单变量paired run，关键收益/回归/成本可归因；
7. 所有attempt、test access、label correction、unavailable/error完整披露；
8. protected/portable artifact、native verifier与independent reviewer exact decision有效。

### 15.2 立即停止

发现test/root泄漏、Gold进入runner、程序生成“真实”答案、unsupported自报0、复用derived作为raw、静默选择冲突、
选择性隐藏attempt、judge未经校准决定Gate、ablation多变量变化、ACL/secret泄漏、删除未级联或阈值看完test后
修改时立即停止并保留artifact。默认V1不变。

## 16. 当前目标重设

当前完成`WP-G6D-01 / T6.0.1`：冻结多源60-case/Wiki120-case工程fixture事实、MJ-01–26差距、真实
joint universe/Gold answer/claim图、分层指标、盲评/judge、M-B0–M-B9、20项实施序列与MJ-01–54。
本结果为`DESIGN_ENGINEERING_PASS`，不是joint quality pass。

严格关键路径：

```text
G0→G5 QUALIFIED
  → T6.1 real joint universe/sealed split
  → T6.2 route/evidence/claim blind Gold
  → T6.3 frozen candidate + real Wiki/raw runner
  → T6.4 claim scoring + human/judge calibration
  → T6.5 B0/M-B1…B9 paired ablation
  → T6.6 portable evidence + independent decision
```

G5前允许继续G7–G9只读设计和fixture contract tests；禁止把60/120程序化fixture重命名为真实质量证据，
禁止访问正式数据、执行shadow/canary、变更默认route或自行签署qualification。
