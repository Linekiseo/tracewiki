# Experiment / MLflow 单源 RAG 详细设计

状态：`REPOSITORY_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD`  
设计版本：experiment-source-rag-v2  
基线日期：2026-07-27  
对应面试文档：[03_EXPERIMENT_SOURCE_INTERVIEW.md](../interview/03_EXPERIMENT_SOURCE_INTERVIEW.md)

> **2026-07-30 实现覆盖层**
>
> 本文正文仍是功能与退出门基线。存在 ready governed source 时，显式 Experiment V2
> 会从 Runtime/Platform 到达完整 source facade，使用正式 Experiment service 作为
> authority，并在进程内惰性构建可丢弃派生索引；没有 governed publication 的历史手工
> Run 继续走兼容 typed projection。默认 V1、scope/ACL fail-closed 和回退不变。旧
> E-B0 固定组件摘要已因后续合法实现变化失效，必须重新授权评测；不得据此刷新或宣称
> qualified。正式库是否为空不再由本文推断，也不会为验收读取正式库。

## 1. 来源定位

Experiment Source 是“实验计划—运行条件—结果观测—制品—比较”的结构化事实域：

```text
Experiment
├── Objective / Hypothesis
└── Run
    ├── Code Version
    ├── Dataset Version
    ├── Config Snapshot
    ├── Environment Snapshot
    ├── Command / Status / Time
    ├── Metric Observation(s)
    └── Artifact Version(s)
```

它不应被当成文本语料。核心正确性来自：

- 精确 Run 身份；
- 数据集和代码版本；
- 配置与环境；
- Metric 定义、单位、方向、split、step；
- 数值计算；
- Run 是否可比较；
- Artifact 是否可定位和校验。

Dense retrieval 只负责把自然语言映射到 Experiment/Run/Metric 概念，不能替代结构化过滤和
数值计算。

## 2. 物理入口

### 2.1 当前入口

| 入口 | 当前支持 | 内容 |
| --- | --- | --- |
| Manual Experiment/Run API | 是 | Experiment、Run、Metric、Artifact |
| MLflow FileStore | 是 | experiment meta、run meta、params、tags、latest metric |
| MLflow REST | 是 | experiment、run、metrics、tags、artifact root |
| Notebook | 另一个独立 Source | 只通过关系挂 Run |
| DVC | 否 | 目标 DatasetVersion/Artifact lineage |
| OpenLineage | 否 | 目标 Dataset/Job lineage |
| CI benchmark | 否 | 可作为未来 Run adapter |
| W&B/others | 否 | 非 P0 |

### 2.2 边界

- Notebook Cell/Output 不在本源；
- 文档表格中的数值不在本源；
- Code 版本正文不在本源；
- Run 的 commit/dataset/artifact 只保存引用和关系；
- Artifact URI 不代表其内容已经下载、解析或校验。

## 3. 当前实现审计

### 3.1 代码入口

| 能力 | 当前实现 |
| --- | --- |
| API 模型 | [experiments/models.py](../../../src/evidence_rag/experiments/models.py) |
| Schema | [experiments/schema.py](../../../src/evidence_rag/experiments/schema.py) |
| 摄取、比较和关系 | [experiments/service.py](../../../src/evidence_rag/experiments/service.py) |
| Store | [experiments/store.py](../../../src/evidence_rag/experiments/store.py) |
| MLflow Adapter | [experiments/adapters/mlflow.py](../../../src/evidence_rag/experiments/adapters/mlflow.py) |
| 跨源结构化搜索 | [platform/store.py](../../../src/evidence_rag/platform/store.py) |

### 3.2 当前实体

| 实体 | 内容 |
| --- | --- |
| Experiment | title、objective、hypothesis、owner、status、tags、iteration |
| ExperimentRun | status、repo/commit/branch、dataset、config、environment、command、time |
| Metric | name、value、unit、split、step、higher_is_better |
| Artifact | name、URI、kind、checksum、media type、metadata |
| Comparison | baseline、candidates、controls、config diff、metric delta |
| ExperimentSource | MLflow URI、status、stats |
| ExternalMapping | external experiment → internal Experiment |

### 3.3 当前关系

- Run `uses` Commit；
- Run `reports` Metric；
- Experiment 可挂接 ResearchIteration；
- Raw MLflow object `DERIVED_INTO` Run/Metric/Artifact。

### 3.4 当前 MLflow 规范化

从 tags 中提取：

- repository URL；
- commit SHA；
- branch；
- dataset ID/version；
- environment/container/hardware tags；
- entry point/source name。

当前 MLflow Metric 默认：

- `unit=None`；
- `split=None`；
- `higher_is_better=True`。

FileStore 只读取每个 metric 文件的最后一条有效记录；时间序列和最优 step 未完整保留。

### 3.5 当前搜索

Experiment/Run/Metric 没有独立 Search View 或专用 Retriever。跨源搜索使用 SQL
`evidence_match`：

- Experiment：title/objective/hypothesis；
- Run：name/command/config/commit/external ID/dataset；
- Metric：name/value。

这是 token substring 规则，不是字段化查询、数值查询或语义检索。

### 3.6 当前比较

限制：

- Runs 必须属于同一 Experiment；
- baseline 默认第一个；
- controls 只检查 dataset ID/version、branch、完整 environment JSON；
- config 做 key diff；
- Metric 只按 `(name, split)` 对齐；
- delta = candidate - baseline；
- 使用 baseline 的 higher_is_better；
- 未校验 unit、definition、step、aggregation、seed、样本量和状态；
- 缺失 candidate metric 会得到空列表，但没有明确不可比状态。

### 3.7 当前活跃规模

| 表 | 当前值 |
| --- | ---: |
| Experiment | 0 |
| Run | 0 |
| Metric | 0 |
| Artifact | 0 |
| Source | 0 |
| Comparison | 0 |

所以任何 Experiment RAG 质量结论目前都没有真实数据支持。

### 3.8 当前优点

- MLflow File/REST 都有真实 adapter；
- Raw Object、Source Event 和 derivation 已接通；
- imported Run 幂等 upsert；
- secrets 在 MLflow payload 规范化前脱敏；
- commit tag 可解析到版本实体；
- Run→Metric 和 Run→Commit 是确定性关系；
- 手工 Run 和外部 Run 使用统一领域模型；
- Comparison 结果持久化。

### 3.9 当前失败模式

| 失败 | 原因 |
| --- | --- |
| 不能回答自然语言指标问题 | 无专用 Retriever/Query parser |
| 数值比较可能误导 | unit/direction/definition/step 未校验 |
| 所有 MLflow Metric 默认越大越好 | adapter 缺 Metric Registry |
| Dataset 只是两个字符串 | 无 DatasetVersion 实体和校验 |
| Config/Environment 是 JSON blob | 无规范化 key/type/hash |
| 时间序列丢失 | FileStore 只取 latest |
| Artifact 内容未知 | URI/metadata 不等于已验证制品 |
| Run 状态未进入比较门 | failed/running 仍可被比较 |
| Commit 绑定不完整 | tag 缺失或历史未索引时无实体 |
| structured search 混合文本与数字 | 无 operator/range/aggregation |
| 当前无数据 | 无法建立 Golden/校准 |

## 4. 查询任务目录

### 4.1 Experiment/Run 精确定位

示例：

- `RUN-ABCD1234` 是什么？
- MLflow run `xyz` 是否同步？

必需角色：

- exact Run；
- status；
- source locator；
- observed version。

### 4.2 条件筛选

示例：

- 使用 dataset v2、learning_rate=1e-4 的 completed Runs；
- commit abc123 上运行的实验。

必需角色：

- filter parse；
- exact structured matches；
- complete Run conditions。

### 4.3 指标查询

示例：

- 哪个 Run 的 Recall@10 最高？
- latency_p95 小于 2 秒的 Runs。

必需角色：

- Metric definition；
- direction/unit；
- split/step/aggregation；
- exact value；
- Run。

### 4.4 Run 比较

示例：

- candidate 比 baseline 提升多少？
- 两个 Run 可以直接比较吗？

必需角色：

- baseline/candidate；
- comparability result；
- aligned Metric；
- deterministic delta/relative delta；
- changed controls。

### 4.5 复现

示例：

- 如何复现最佳 Run？
- 该结果缺哪些复现条件？

必需角色：

- command；
- code commit；
- dataset version；
- config snapshot；
- environment snapshot；
- artifacts/dependencies；
- seed。

### 4.6 失败诊断

示例：

- 哪些 Run 失败？
- 失败 Run 和成功 Run 的配置差异？

必需角色：

- status/failure；
- config/environment diff；
- log/artifact locator；
- comparable successful peer。

当前没有 log 实体，必须明确缺口。

### 4.7 趋势和聚合

示例：

- 五次 seed 的平均值和方差？
- 指标随 step 如何变化？

必需角色：

- repeated Run group；
- raw observations；
- aggregation function；
- sample count；
- variance/confidence；
- exclusions。

### 4.8 Experiment 设计与假设

示例：

- 这个 Experiment 要验证什么？
- 哪些 Run 覆盖了假设？

必需角色：

- objective/hypothesis；
- planned controls；
- Runs；
- outcome evidence。

Experiment status/description 不是结果事实。

## 5. 目标领域模型

### 5.1 实体拆分

| 实体 | 目的 |
| --- | --- |
| Experiment | 研究计划和假设 |
| RunAttempt | 一次可观察执行 |
| MetricDefinition | 名称、语义、单位、方向、合法范围 |
| MetricObservation | Run、value、step、time、split |
| MetricAggregation | mean/std/CI/sample/exclusion |
| Dataset | 逻辑数据集 |
| DatasetVersion | digest、schema、split、source |
| ConfigDefinition | key、type、semantics |
| ConfigSnapshot | 规范化参数和值 |
| EnvironmentSnapshot | runtime/dependency/hardware/container |
| ArtifactVersion | URI、checksum、size、availability、media |
| RunGroup | seeds/folds/repeats 的可比较集合 |
| Comparison | 可比性判定和确定性差异 |

### 5.2 关系

- Experiment HAS_RUN；
- Run USES_CODE；
- Run USES_DATASET_VERSION；
- Run USES_CONFIG；
- Run USES_ENVIRONMENT；
- Run OBSERVES MetricObservation；
- MetricObservation INSTANCE_OF MetricDefinition；
- Run PRODUCES ArtifactVersion；
- MetricAggregation AGGREGATES MetricObservation；
- Comparison COMPARES Run；
- Run REPEATS RunGroup；
- Run SUPERSEDES/RETRIES Run；
- Artifact DERIVED_FROM Run；
- Run EXECUTED_BY NotebookRun（跨源）。

## 6. Metric Registry

### 6.1 MetricDefinition

```python
MetricDefinition(
    canonical_name="recall_at_10",
    aliases=["Recall@10", "recall10"],
    description="...",
    unit="ratio",
    higher_is_better=True,
    valid_min=0.0,
    valid_max=1.0,
    required_dimensions=["split"],
    default_aggregation="mean",
    registry_version="metrics-v1",
)
```

### 6.2 未知 Metric

MLflow 新 Metric 若无法匹配 Registry：

- 仍保存 Observation；
- `definition_status=unknown`；
- 不默认 higher_is_better；
- 不做“最佳”或方向性结论；
- 允许人工映射 alias/definition；
- Registry 变更版本化，不改原始值。

### 6.3 数值规范化

- 百分数与 ratio 分开；
- ms/s 转换需显式 unit；
- NaN/Inf 标记 invalid；
- step/time 不丢失；
- float 显示保留原始 representation；
- relative delta 处理 baseline=0；
- direction-aware improvement：

```text
raw_delta = candidate - baseline
improvement = raw_delta if higher_is_better else -raw_delta
relative = improvement / abs(baseline)  # baseline != 0
```

## 7. Dataset / Config / Environment

### 7.1 DatasetVersion

字段：

- dataset ID；
- version/digest；
- source URI；
- schema hash；
- row/sample count；
- split definition；
- filters；
- preprocessing；
- license/access；
- observed time。

仅有 `dataset_id` 无 version 时，复现角色不满足。

### 7.2 ConfigSnapshot

规范化：

- flatten key path；
- preserve type；
- canonical JSON；
- secret redaction；
- config hash；
- default vs explicit；
- source locator。

比较需要区分：

- changed；
- missing；
- type_changed；
- default_unknown。

### 7.3 EnvironmentSnapshot

至少：

- OS/arch；
- language/runtime；
- dependency lock digest；
- container image digest；
- hardware/device；
- accelerator/driver；
- environment variables allowlist；
- random seeds。

Environment JSON 完全相等不是唯一可比判据；使用字段化 compatibility policy。

## 8. Retrieval Unit

Experiment Source 主要使用结构实体，Retrieval Unit 用于自然语言映射：

| Unit | 内容 |
| --- | --- |
| `experiment.surface` | title、objective、hypothesis、tags |
| `run.surface` | name、status、dataset、commit、command、主要 config |
| `metric.definition` | canonical/alias/description/unit/direction |
| `config.surface` | key/value/schema/meaning |
| `dataset.surface` | dataset/version/schema/split |
| `artifact.surface` | name/kind/media/metadata |
| `failure.surface` | status/error/log summary（未来） |
| `comparison.surface` | baseline/candidates/controls/metrics |

原始数值仍来自结构化表，不从 embedding text 解析回数值。

## 9. Query Parser 与安全 Filter DSL

### 9.1 目标结构

```python
ExperimentQuery(
    task="rank_runs",
    experiment_ids=[],
    run_ids=[],
    statuses=["completed"],
    metric=MetricPredicate(
        name="recall_at_10",
        operator="max",
        split="test",
        aggregation="final",
    ),
    dataset=DatasetPredicate(id=None, version="v2"),
    config=[FieldPredicate("learning_rate", "eq", 0.0001)],
    commit=None,
    time_range=None,
    group_by=[],
    limit=10,
)
```

### 9.2 安全

- 白名单字段；
- 白名单 operator；
- 参数化 SQL；
- limit；
- 禁止任意 SQL；
- numeric/string/bool 类型校验；
- unit conversion 明确；
- parser confidence；
- 低置信 predicate 保留为文本条件并提示。

### 9.3 运算

支持：

- eq/ne/gt/gte/lt/lte/in；
- max/min/top/bottom；
- latest/final/best step；
- mean/median/std/count；
- delta/relative delta；
- group by seed/dataset/config key。

所有运算在确定性 evaluator 中执行。

## 10. Candidate Generation

顺序：

1. exact ID/external ID；
2. structured filters；
3. Metric Registry alias；
4. lexical/dense surface；
5. relation expansion；
6. deterministic numeric sort/aggregation；
7. comparability gate；
8. source reranker 只处理语义候选。

### 10.1 通道

- exact；
- structured SQL；
- metric alias；
- sparse；
- dense；
- graph；
- comparison；
- artifact metadata。

### 10.2 不同任务

| Task | 主通道 |
| --- | --- |
| Run lookup | exact/structured |
| best metric | metric registry + numeric |
| compare | exact + comparability |
| reproduce | graph/role coverage |
| hypothesis | experiment semantic + runs |
| failure | status + log/artifact |

## 11. Comparability Gate

### 11.1 必查字段

- Experiment/Task；
- MetricDefinition；
- unit；
- split；
- step/final/best policy；
- DatasetVersion；
- preprocessing；
- code commit；
- Config controls；
- Environment compatibility；
- seed/repeat；
- run status；
- sample/aggregation。

### 11.2 结果

```text
comparable
comparable_with_caveats
not_comparable
insufficient_metadata
```

每个差异分：

- controlled variable；
- treatment variable；
- unknown；
- disqualifying difference。

### 11.3 比较输出

```text
Metric definition
Baseline condition/value
Candidate condition/value
Raw delta
Direction-aware improvement
Relative improvement
Comparability decision
Caveats
Raw observation citations
```

## 12. Experiment Reranker

Reranker 只用于文本/语义候选，不覆盖结构化结果。

特征：

- query task/entity type；
- exact structured match；
- objective/hypothesis relevance；
- metric alias match；
- dataset/config match；
- status；
- version completeness；
- comparability；
- metadata completeness；
- source freshness；
- artifact availability。

负面：

- failed_for_best；
- unknown_metric_direction；
- wrong_dataset_version；
- missing_commit；
- different_split；
- different_unit；
- incomplete_run；
- stale_snapshot。

## 13. Context Builder

### 13.1 Run 查询

```text
Run Identity / Status / Time
Experiment Objective
Code Commit
Dataset Version
Config Snapshot
Environment Snapshot
Metric Observations
Artifacts
Missing Metadata
```

### 13.2 比较

```text
Comparability Decision
Control Matrix
Treatment Differences
Metric Definition
Aligned Values and Deterministic Deltas
Run Status
Version/Metadata Caveats
```

### 13.3 复现

```text
Command/Entry Point
Code Version
Dataset Version
Config
Environment
Seed
Required Artifacts
Missing Roles
```

### 13.4 聚合

```text
Run Group Definition
Included/Excluded Runs
Raw Observations
Aggregation Function
N / Mean / Std / CI
Version/Condition Consistency
```

## 14. 摄取与增量

### 14.1 MLflow Snapshot

- Source Event 使用 run version hash；
- Run 外部 ID 幂等；
- Metric Observation 不能删除历史 step 后只留 latest；
- completed Run 默认 immutable，源发生变化时记录新 observed version；
- running Run 允许更新；
- deleted lifecycle 产生 tombstone/status，不映射为 completed；
- Artifact 单独同步 availability/checksum。

### 14.2 Generation

Experiment 当前不是 Generation 模型。V2 增加：

- source sync run；
- observed source version；
- active derived version；
- partial/complete；
- row counts；
- mapping diagnostics；
- atomic visibility for a sync batch。

## 15. ACL、安全和质量

- Experiment/Run/Metric/Artifact 继承项目或 source ACL；
- Artifact URI 访问前独立授权；
- config/environment secret redaction；
- remote MLflow SSRF/allowed URI policy；
- local FileStore path allowlist；
- deleted Run/Artifact tombstone；
- unknown Metric 不做方向性结论；
- incomplete version 不伪装可复现；
- failed/running Run 不参与 best 默认排序；
- NaN/Inf/invalid range 报错；
- raw value 和 normalized value同时保留。

## 16. 样例数据集

正式库当前为空，M0 必须创建最小真实样例：

- 1 个 Experiment；
- 1 个 baseline；
- 2 个 valid candidates；
- 1 个 failed Run；
- 1 个 running Run；
- 1 个不同 DatasetVersion；
- 1 个不同 Metric unit；
- 1 个 missing commit；
- 每个 completed Run 至少 2 个 seed 或明确 single-run；
- time-series metric；
- Artifact checksum；
- 可复现命令/config/environment。

样例同时通过 Manual API 和 MLflow adapter 验证一致性。

## 17. Golden Set

### 17.1 首版 45 条

| Slice | 数量 |
| --- | ---: |
| exact Run/Experiment | 5 |
| structured filter | 7 |
| metric ranking/range | 8 |
| Run comparison | 8 |
| reproduction | 6 |
| failure/status | 4 |
| aggregation/trend | 4 |
| unanswerable/incomparable/ACL | 3 |

### 17.2 Hard Negative

- 相同 Metric 名、不同 unit；
- 相同 Dataset ID、不同 version；
- higher/lower direction 相反；
- test/validation split 不同；
- latest/best/final step 不同；
- failed Run 指标更高；
- config value 字符串 `"1"` vs 数字 `1`；
- missing seed/environment；
- artifact URI 存在但不可访问；
- commit tag 无法解析。

### 17.3 指标

#### Retrieval/Parsing

- exact Run Recall@5；
- structured predicate accuracy；
- Metric alias accuracy；
- entity Recall；
- filter precision。

#### Numeric

- numeric answer accuracy；
- unit accuracy；
- direction accuracy；
- delta/relative delta；
- aggregation accuracy；
- included/excluded Run accuracy。

#### Comparability/Reproduction

- incomparable recall/precision；
- caveat recall；
- reproduction role coverage；
- version completeness accuracy。

#### System

- sync idempotency；
- source drift；
- P95；
- rows/scanned candidates；
- artifact fetch cost；
- leakage。

## 18. 消融

| Run | 变量 |
| --- | --- |
| E-B0 | 当前 `evidence_match` |
| E-B1 | structured parser/filter |
| E-B2 | Metric Registry |
| E-B3 | semantic surface retrieval |
| E-B4 | comparability gate |
| E-B5 | Experiment reranker |
| E-B6 | structured context |

关键是证明：

- 语义模型不应替代数值/过滤；
- Comparability Gate 可能降低“回答率”，但提高正确性；
- Metric Registry 对方向和 unit 的收益；
- structured-first 的延迟和精度。

## 19. 发布门槛

| 指标 | Target |
| --- | ---: |
| Exact Run Recall@5 | ≥ 0.95 |
| Predicate Accuracy | ≥ 0.98 |
| Numeric Accuracy | ≥ 0.99 |
| Unit/Direction Accuracy | 1.00 |
| Aggregation Accuracy | ≥ 0.99 |
| Incomparability Recall | ≥ 0.95 |
| Reproduction Role Coverage | ≥ 0.90 |
| Version Accuracy | ≥ 0.98 |
| Secret/Unauthorized Leakage | 0 |
| P95 structured query | ≤ 500 ms（当前样例规模） |

## 20. 实施映射

### 20.1 新模块

```text
src/evidence_rag/rag/sources/experiment/
  contracts.py
  metric_registry.py
  query_parser.py
  filter_compiler.py
  numeric_evaluator.py
  comparability.py
  unit_builder.py
  retriever.py
  reranker.py
  context_builder.py
  evaluator.py
```

### 20.2 Schema

- `metric_definitions`；
- `metric_aliases`；
- `metric_observations`；
- `dataset_versions`；
- `config_snapshots/config_values`；
- `environment_snapshots`；
- `artifact_versions`；
- `run_groups`；
- `experiment_sync_generations`；
- `experiment_retrieval_units`。

### 20.3 Feature Flags

```text
RAG_EXPERIMENT_QUERY=text-v1|structured-v2
RAG_EXPERIMENT_METRIC_REGISTRY=off|v1
RAG_EXPERIMENT_COMPARABILITY=basic-v1|strict-v2
RAG_EXPERIMENT_EMBEDDING=off|profile
RAG_EXPERIMENT_CONTEXT=snippet-v1|structured-v2
```

### 20.4 Sprint

#### E0：样例与 Golden

- 真实 MLflow fixture；
- Manual parity；
- 45 条 Golden；
- current baseline。

#### E1：Metric/Data/Config 模型

- Metric Registry；
- Observation time series；
- DatasetVersion；
- snapshot hashes。

#### E2：Structured Query

- parser；
- safe DSL；
- numeric evaluator；
- exact/structured retrieval。

#### E3：Comparability

- control policy；
- aggregation；
- comparison v2。

#### E4：Semantic/Context

- surface Unit；
- optional embedding/rerank；
- structured context。

#### E5：Sync/Release

- source generation；
- artifact policy；
- security/SLO/rollback。

## 21. 测试计划

- MLflow File/REST parity；
- run upsert idempotency；
- running→completed；
- deleted lifecycle；
- metric step history；
- unknown direction；
- unit conversion；
- NaN/Inf；
- dataset version mismatch；
- config type difference；
- environment compatibility；
- failed Run best exclusion；
- baseline zero relative delta；
- missing candidate metric；
- artifact checksum/availability；
- commit prefix ambiguity；
- secret in config；
- unsafe tracking URI/path；
- ACL artifact leakage。

## 22. 完成定义

1. 有真实 Experiment/Run 数据；
2. MetricDefinition 与 Observation 分离；
3. Dataset/Config/Environment 版本化；
4. Query 先结构化再语义；
5. numeric answer 确定性计算；
6. Comparability Gate 生效；
7. unknown Metric 不默认越大越好；
8. time series 不丢失；
9. structured Context 和缺失角色明确；
10. Golden/SLO/security/rollback 通过；
11. 面试文档填入真实 Run。
