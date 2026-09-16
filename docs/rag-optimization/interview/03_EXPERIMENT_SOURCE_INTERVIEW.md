# Experiment Structured RAG 面试与简历材料

对应设计：[03_EXPERIMENT_SOURCE_RAG.md](../sources/03_EXPERIMENT_SOURCE_RAG.md)

## 1. 当前状态声明

### HISTORICAL_BASELINE

- 早期正式数据记录为 Experiment、Run、Metric、Artifact 均为 0；该数字没有在本轮重新
  读取，正式库仍由外部服务拥有；
- 已有 Manual API、MLflow File/REST adapter；
- 已有 Run→Commit、Run→Metric 关系；
- 当前搜索是 SQL token substring；
- 当前比较检查 dataset/branch/environment/config 和简单 metric delta；
- MLflow Metric 默认 `higher_is_better=True`、unit/split 为空；
- FileStore 当前只保留 latest metric。

### CURRENT_PLAN

- 开发计划：[03_EXPERIMENT_SOURCE_DEVELOPMENT_PLAN.md](../development/03_EXPERIMENT_SOURCE_DEVELOPMENT_PLAN.md)；
- E0 Foundation 已 `PASS / COMPLETE`，released Golden exact 45；
- E-B0 fixed baseline 已发布并离线验证，状态 `NON_QUALIFIED`；
- E1–E3 仓库内工程已完成：typed content-addressed entities、isolated additive store、
  atomic generation、typed/parameterized query、unit normalization、strict comparability、
  RunGroup aggregation 与 seven-role reproduction；
- E4 仓库内工程已完成：versioned semantic surfaces、exact/structured pinned、本地
  deterministic semantic tail/rerank、missing/negative reasons 与 task context；
- Experiment V2 只通过 `X-RAG-Experiment-Engine: v2` 显式启用，默认仍为 V1；
- E5 governed pipeline、atomic generation、path/SSRF、sanitized trace、stable routing、
  shadow/canary evaluator 与 rollback 已完成；release 固定 `HOLD_DEFAULT_V1`。尚无
  E1–E5 treatment Run 或质量提升结论，远程 semantic model、production
  shadow/canary、默认 V2 release 外部阻塞。

### TARGET

- Metric Registry；
- Dataset/Config/Environment Snapshot；
- safe structured Query DSL；
- deterministic numeric evaluator；
- strict comparability；
- semantic surface retrieval；
- Experiment Golden Set。

## 2. 15 秒定位

> Experiment RAG 的关键不是 embedding，而是把自然语言问题转换为安全的结构化条件，
> 精确查找 Run、Metric、Dataset、Config 和 Commit，再经过可比性检查和确定性数值计算；
> 语义召回只用于映射概念，不能决定实验结论。

## 3. 1 分钟讲解

> 当前系统已经能从 MLflow 和手工 API 接入 Run、Metric、Artifact，并绑定 commit；V1
> 搜索仍是字符串匹配。更重要的是，MLflow 指标默认全部
> “越大越好”，unit/split 缺失，比较也没有检查 MetricDefinition、step、seed 和
> aggregation，所以即使召回到了相似 Run，也可能得出错误结论。
>
> 我的设计把 MetricDefinition 与 MetricObservation 分开，建立 DatasetVersion、
> ConfigSnapshot、EnvironmentSnapshot 和 RunGroup。Query Parser 把“dataset v2 中
> Recall@10 最高的 completed Run”编译为白名单 Filter DSL，数值排序和 delta 由确定性
> evaluator 完成。现在显式 V2 主路已经实现这些核心能力；任何 Run 比较先过
> Comparability Gate，检查 Metric、unit、split、
> dataset、commit、config、environment、seed 和 status；条件不足时返回不可比或缺失
> 元数据，而不是让 LLM 猜。

## 4. 白板

```text
Manual / MLflow
→ Raw Snapshot + Version
→ Experiment / Run
→ DatasetVersion / Config / Environment
→ MetricDefinition + MetricObservation
→ Structured Query Parser
→ Exact Filter + Numeric Evaluator
→ Comparability Gate
→ Structured Evidence Context
```

## 5. 三个核心决策

### 5.1 结构化优先

ID、status、config、dataset、value/operator 用参数化查询；dense 只解决自然语言 alias 和
描述，不从 embedding 还原数字。

### 5.2 Metric 定义与观测分离

同名 Metric 可能 unit、方向、split、aggregation 不同。Definition 管语义，Observation
管一次 Run 的值，未知 Metric 不默认方向。

### 5.3 比较先于结论

可比性不是结果展示字段，而是生成结论的 Gate。DatasetVersion、MetricDefinition、
split、step policy、config controls、environment、seed 和 status 不齐时，不输出确定提升。

## 6. 高频追问

### 为什么不直接 Text-to-SQL？

任意 SQL 有安全、正确性和 schema drift 风险。项目使用白名单领域 DSL，字段和 operator
类型化，再编译为参数化 SQL；低置信条件回退为候选搜索并展示解析结果。

### Embedding 在实验源还有什么用？

用于把“召回率”“recall@10”映射 Metric alias，或者按 objective/hypothesis 找 Experiment。
精确过滤、排序、聚合和 delta 不依赖 embedding。

### 怎么定义最佳 Run？

必须先确定 MetricDefinition、higher/lower、split、step/aggregation，过滤 completed 且
条件可比的 Run，再确定性排序。无法确定方向时不回答“最佳”。

### 为什么 dataset_id 不够？

同名数据集可能内容、split、过滤或预处理变化。复现至少需要 version/digest 和 split/
preprocessing；只有 ID 时标记缺失。

### 相同环境 JSON 才可比吗？

不是。使用 compatibility policy：某些 runtime/dependency/hardware 字段是关键，另一些
非关键。完整 JSON 相等过严，完全忽略又过松。

### LLM 能计算指标差异吗？

不应依赖。delta、relative delta、mean/std/CI 用确定性代码；LLM 只解释已经计算并引用的
结果。

## 7. 必做消融

| Run | 变量 |
| --- | --- |
| E-B0 | string evidence_match |
| E-B1 | structured parser |
| E-B2 | Metric Registry |
| E-B3 | comparability gate |
| E-B4 | semantic surface |
| E-B5 | structured context |

## 8. 失败案例占位

### 所有指标默认越大越好

- 当前风险：latency/loss 可能被错误排序；
- 修复：Metric Registry + unknown direction；
- 指标：direction accuracy；
- Run：TBD。

### 语义相似但实验不可比

- 风险：不同 dataset version/split 的 Run 被比较；
- 修复：Comparability Gate；
- 代价：回答率下降但正确性上升；
- Run：TBD。

## 9. 简历 Bullet

### 设计阶段

> 设计结构化 Experiment RAG，将 MetricDefinition/Observation、DatasetVersion、
> ConfigSnapshot、EnvironmentSnapshot 和 RunGroup 分离，使用安全 Filter DSL、确定性
> 数值计算和 Comparability Gate 回答筛选、比较与复现问题。

### 实现后

> 实现 MLflow/Manual Experiment Structured RAG，在 `[N]` 条 Golden Query 上实现
> Predicate Accuracy `[A]`、Numeric/Unit/Direction Accuracy `[B]`，将不可比实验识别
> Recall 提升至 `[C]`，复现证据角色覆盖达到 `[D]`，P95 为 `[E]`。

## 10. 证据表

| 证据 | 当前 | 完成后 |
| --- | --- | --- |
| Design | experiment-source-rag-v2 | 已有 |
| Sample Data | 0 | fixture/source |
| Golden | `experiment-golden-v1`, exact 45 | 已发布 |
| Baseline | E-B0 `NON_QUALIFIED` | immutable artifact |
| Structured Run | 无 treatment Run | 不虚构提升 |
| Comparability Run | 无 treatment Run | 不虚构提升 |
| Metric Registry | versioned definition/alias/direction + observation history | `sources/experiment/contracts_v2.py` / `store_v2.py` |
| Query Parser | typed AST + allowlisted parameterized compiler + numeric evaluator | `sources/experiment/query_v2.py` |
| Comparability/Aggregation | four states + deterministic group statistics/reproduction | `sources/experiment/analysis_v2.py` |
| Semantic/Context | versioned surfaces + pinned structured candidates + task context | `sources/experiment/semantic_v2.py` |
| Governance | default V1 + opt-in/shadow/canary evaluator/rollback | `sources/experiment/governance_v2.py` |
| Tests | Experiment V2 + multi-source + full regression | PASS |
| P95 | 无 | TBD |

## 11. 不能夸大

- 不把早期正式库 0 条记录冒充本轮实时测量；
- 当前有本地 deterministic semantic tail/reranker；没有远程 neural embedding/reranker
  benchmark 或 production qualification；
- V1 比较仍较宽松；strict comparability 只在显式 V2 主路生效；
- unknown Metric direction 保持 unknown，不得作为 best/improvement；
- isolated V2 store 支持 Observation history；正式库尚未 backfill；
- Artifact URI 与 verification state 已分离；URI 本身仍不等于内容已验证；
- Target 数值不能写进简历；
- Experiment 相关论文收益不等于项目收益。
