# Notebook Execution RAG 面试与简历材料

对应设计：[04_NOTEBOOK_SOURCE_RAG.md](../sources/04_NOTEBOOK_SOURCE_RAG.md)

## 1. 当前状态声明

### FINAL_REPOSITORY_STATUS（2026-07-29）

- N0–N6 仓库内工程 `COMPLETE`：40-case Golden、typed revision/execution/cell、
  parameter/output/error、def-use/dependency、专用 retrieval/rerank、compare/context、
  isolated store、portable baseline 和 release evaluator 均已实现；
- baseline artifact：
  `evaluation-run://project-notebook-nb0-v1/b9278f79dc7d42a8a61c25c45b1d7d3e`，
  set `sha256:d59f54f98af4bf156d0dccbc7350ceb3b337b4cc46ff0f1cfad463e4438d2e28`；
- 当前结论仍为 `VERIFIED_NON_QUALIFIED / HOLD_DEFAULT_V1`；远程 dense、正式库
  migration/backfill、production replay/shadow/canary 未完成。

### HISTORICAL_MEASURED（未在本轮重测）

- NotebookTemplate/Run/Cell/Output 当前均为 0；
- 已有 `.ipynb` parser、Raw Object 和结构化 Cell/Output；
- 搜索只拼接 Cell source 后返回 NotebookRun；
- 不搜索 Output/Error/Parameter；
- status 是 any error→failed；
- compare 按 ordinal；
- 图像 binary 省略；
- 无跨 Cell dependency graph。

### 2026-07-29 早期中间实现（已被 FINAL_REPOSITORY_STATUS 覆盖）

- Notebook 已从 Experiment family 中拆为 Platform/Query 的显式 `notebook` 来源；
- `.ipynb` 的 Cell/Output 仍由现有 typed Notebook store 拥有，统一 evidence block 使用
  portable locator、secret redaction、稳定 citation 和确定性预算；
- Query Evidence Pack 同时保留 retrieval context 与只从选中证据派生的
  comprehension context；
- 没有重读正式库，所以上述旧计数只作历史基线；本次未实现 Revision/Execution
  migration、def-use graph、远程 reranker 或 production quality Run。

### ORIGINAL_TARGET（仓库内工程已完成，production qualification 未完成）

- Revision/Execution 分离；
- typed Cell/Output/Error Retrieval Unit；
- parameter typed parsing；
- execution/dataflow graph；
- stale output；
- identity-aware comparison；
- Notebook reranker/context。

## 2. 15 秒定位

> Notebook RAG 既不是文档 RAG，也不是代码 RAG：它同时需要 Markdown 叙事、Code Cell、
> 跨 Cell 状态、执行顺序、参数、Output 和 Error。我将 NotebookRevision 和
> NotebookExecution 分离，用 Cell/Output 专用检索和 def-use 执行图恢复结果的产生路径。

## 3. 1 分钟讲解

> 当前系统已经能解析 Cell、execution_count、Output 和 Papermill 参数，但搜索只把所有
> Cell source 拼起来返回 NotebookRun，错误和输出找不到，比较也按 ordinal，所以插入
> 一个 Cell 会引起后续大量误报。
>
> 我的设计把逻辑 Template、文件 Revision 和一次 Execution 分开；Cell 使用 nbformat
> ID/source hash 建版本链，Code Cell 再做 AST 与跨 Cell defines/reads/writes，Output
> 保留 producer 和 MIME 类型。检索按 Markdown、code、parameter、error、output 分通道，
> 通过 execution/dataflow graph 找上游 Cell；Context 按参数→依赖 Cell→目标 Cell→
> Output/Error 组装，并显式标记 stale output 和未知执行状态。

## 4. 白板

```text
.ipynb
→ Template / Revision / Execution
→ Markdown Cell | Code Cell | Output | Error
→ Parameter + Cell Symbol + Artifact
→ Exact/Sparse/Dense
→ Execution Order + Def-Use Graph
→ Notebook Reranker
→ Execution Context
```

## 5. 核心决策

### Revision 与 Execution 分离

Notebook source 和保存的 output 可能不对齐。必须标记 output alignment/stale，而不是假设
文件里有 output 就可复现。

### Display order 与 execution order 分离

Cell ordinal 是展示顺序，execution_count 是执行线索。依赖解析优先使用执行顺序，缺失时
降级并降低 confidence。

### Notebook Output 不自动等于 Experiment Metric

文本 `0.82` 缺 metric definition、unit、split、Run binding。只能生成候选关系，确认后才
进入实验事实。

## 6. 高频追问

### 跨 Cell dataflow 怎么做？

静态提取每个 Cell 的 definitions/reads/writes，在执行顺序上为 read 选择最近 prior
writer；kernel restart、dynamic exec、magic 和无 execution count 会降低 confidence 或
标 unresolved。

### 如何识别 stale output？

如果有 Papermill/执行 provenance，比较 source hash 与 execution source；普通 ipynb 缺少
完整信息时只能用 execution_count、output metadata 和 revision change 推断，标记
unknown 而不是强判。

### 为什么比较不能按 ordinal？

插入或移动 Cell 会导致后续错位。优先 nbformat cell.id，其次 source hash/AST similarity
和邻居，再 ordinal fallback。

### 图像怎么检索？

原始 binary 作为 Artifact，不内联向量；使用 caption、producer Cell、metadata，可选安全
视觉描述。HTML sanitize，JS 不执行。

### 为什么不直接执行 Notebook 建依赖？

执行不安全、昂贵且会产生副作用。RAG 摄取默认静态解析已有执行证据；复现实验是受控的
独立工作流。

## 7. 必做消融

| Run | 变量 |
| --- | --- |
| N-B0 | run-level substring |
| N-B1 | typed Cell/Output Unit |
| N-B2 | separate embeddings |
| N-B3 | execution/dataflow graph |
| N-B4 | Notebook reranker |
| N-B5 | identity-aware compare |
| N-B6 | execution context |

## 8. 失败案例占位

### ordinal compare 大量误报

- Fixture：中间插入一个 Cell；
- V1：后续 Cells 全部 modified；
- V2：识别 added + moved/unchanged；
- 指标：Cell Match Accuracy；
- Run：TBD。

### stale output 被当作结果

- Fixture：修改 source 未重新执行；
- 改进：Revision/Execution alignment；
- 指标：Stale Output Detection；
- Run：TBD。

## 9. 简历 Bullet

### 设计阶段

> 设计 Notebook Execution RAG，将 Template、Revision 与 Execution 分离，对 Markdown、
> Code Cell、Parameter、Output、Error 建多表示索引，并通过执行顺序和跨 Cell def-use 图
> 还原结果产生路径与 stale output 风险。

### 实现后

> 实现 Jupyter Notebook Execution RAG，在 `[N]` 条 Golden Query 上将 Cell/Output
> Recall@10 提升至 `[A]/[B]`，跨 Cell Dependency Path Recall 达到 `[C]`，Cell Version
> Match Accuracy 达到 `[D]`，Stale Output Detection 达到 `[E]`。

## 10. 证据表

| 证据 | 当前 | 完成后 |
| --- | --- | --- |
| Design | notebook-source-rag-v2 | 已有 |
| Sample | 0 | TBD |
| Golden | 无 | TBD |
| Baseline | 无 | TBD |
| Dependency Run | 无 | TBD |
| Compare Run | 无 | TBD |
| Parser | jupyter-ipynb-v1 | v2 file |
| Tests | notebook tests | 新测试 |
| P95 | 无 | TBD |

## 11. 历史边界与当前不能夸大

- 当前 Notebook 数据为空；
- 当前搜索不到具体 Output/Error；
- 当前没有跨 Cell dataflow；
- stale output 很多情况下只能判 unknown；
- 静态 dataflow 不是运行时完整 lineage；
- 当前 compare 仍按 ordinal；
- output 数值不是已确认 Metric；
- 设计目标不是实测结果。

## 12. 2026-07-29 早期中间工程落地（历史记录）

- 新增 `rag/notebook_v2.py`，在现有 `NotebookStore` 上只读派生
  NotebookRun/Parameter/Cell/Output/Error 检索单元，不修改原始 Item 或 schema。
- Python Code Cell 使用静态 AST 提取 definitions/reads，构建保守的 observable def-use；
  不执行 Notebook，解析失败显式 `parse_unavailable`。
- execution count、缺 output 和 error output 进入执行状态与 stale 提示；运行间比较按
  template/run/content/source identity 对齐，不再把 ordinal 当成唯一事实。
- 输出 `notebook-execution-context-v2`，包含执行状态、依赖、比较、缺失角色和稳定
  content digest；secret/绝对路径清洗，reasoning 永不进入。
- Platform/Query 仅在 `X-RAG-Notebook-Engine: v2` 时启用；缺省、回滚均为 V1，
  release trace 为 `HOLD_DEFAULT_V1 / quality_qualified=false`。
- 新增专项覆盖正常执行、参数、输出、错误、def-use、ACL fail-closed、非法 header、
  deterministic identity 与路径/secret 清洗。

真实边界：没有 production Golden/quality Run，没有 runtime kernel lineage，没有正式库
Revision migration，因此工程状态是 `COMPLETE / DEFAULT_V1 / QUALITY_UNAVAILABLE`，
不能填写 Recall、dependency accuracy 或 stale detection 的实测提升。

## 13. 2026-07-29 原始路线图最终工程状态

- N0：released 40-case content-addressed Golden，8 slices=`5/6/5/6/6/5/4/3`，
  programmatic fixture、冻结 denominator 与三态 evaluator；
- N1：Template/Revision/Execution/CellVersion/CellExecution typed contract，ACL/
  generation identity、isolated atomic/idempotent store 与 tombstone；
- N2/N3：typed parameter/output/error、display/exec order、restart/stale、AST def-use、
  redefinition、external dependency 和 uncertainty；
- N4：exact/structured/sparse/graph/local retrieval、task profile、source-local rerank、
  adaptive limits；remote dense 明确 `UNAVAILABLE`；
- N5：identity-aware compare、change taxonomy、parameter/output comparison、8 类
  task-specific context、stable citation 和缺证据三态；
- N6：portable 10-file baseline verifier、P50/P95、六阶段 no-skip release evaluator、
  ACL/generation/tombstone/rollback。

当前质量短板必须照实保留：integrated recall `35/38`、cell matcher `2/4`、
reproduction `0/1`，所以默认 V1，不得声称已上线或已达到 production qualification。
