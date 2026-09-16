# Jupyter Notebook 单源 RAG 详细设计

状态：`REPOSITORY_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD`  
设计版本：notebook-source-rag-v2  
基线日期：2026-07-27  
对应面试文档：[04_NOTEBOOK_SOURCE_INTERVIEW.md](../interview/04_NOTEBOOK_SOURCE_INTERVIEW.md)

> **2026-07-30 实现覆盖层**
>
> 本文正文仍是功能与退出门基线。显式 Notebook V2 已从 Runtime/Platform 到达完整
> revision/execution/cell/output/error/parameter、依赖、比较、retrieval 与 context
> facade；正式 Notebook/Workspace/RawSource 数据为 authority，派生索引仅在显式 V2
> 时于系统临时目录惰性构建并由 Runtime 清理。默认 V1、ACL/scope 与回退不变。没有
> reviewed production replay、校准或发布观测，因此只判仓库 L3，不判 L4/L5。

## 1. 来源定位

Notebook Source 是“可执行叙事文档 + 有状态代码执行”的混合证据域：

```text
Notebook Revision / Execution
├── Markdown Cell
├── Code Cell
│   ├── Source
│   ├── Execution Order
│   ├── Definitions / Reads / Writes
│   └── Output(s)
│       ├── Stream
│       ├── ExecuteResult
│       ├── DisplayData
│       └── Error
├── Parameter Snapshot
├── Kernel / Environment
└── Artifact
```

它同时具有：

- 文档结构；
- 代码结构；
- 跨 Cell 状态；
- 显示顺序；
- 执行顺序；
- 参数化运行；
- 文本、表格、图像和错误输出。

因此不能作为 Experiment Source 中一个 `NotebookRun` 文本片段处理。

## 2. 物理入口与边界

### 2.1 当前入口

- 本地 `.ipynb`；
- 50 MB 上限；
- allowed local roots；
- 可显式绑定 Experiment/Run；
- `version` 由用户提供；
- Papermill metadata/parameters 部分支持。

### 2.2 未来入口

- executed notebook artifact from MLflow；
- nbconvert HTML；
- Jupyter Server API；
- Papermill output storage；
- parameterized pipeline execution；
- Quarto/other notebook formats（非 P0）。

### 2.3 边界

- Notebook 内引用的 Run 指标仍以 Experiment Source 为权威；
- Notebook 中的代码片段不是 Repository 当前实现；
- Notebook 中复制的文档段落仍是 Notebook 叙事，不替代正式 Document；
- Output 只证明该 Notebook snapshot 保存了该输出，不一定证明可复现；
- execution_count 不等于文件中的 Cell ordinal。

## 3. 当前实现审计

### 3.1 代码入口

| 能力 | 当前实现 |
| --- | --- |
| 模型 | [notebooks/models.py](../../../src/evidence_rag/notebooks/models.py) |
| Schema | [notebooks/schema.py](../../../src/evidence_rag/notebooks/schema.py) |
| Parser | [notebooks/adapter.py](../../../src/evidence_rag/notebooks/adapter.py) |
| 摄取/比较 | [notebooks/service.py](../../../src/evidence_rag/notebooks/service.py) |
| Store | [notebooks/store.py](../../../src/evidence_rag/notebooks/store.py) |
| 跨源搜索 | [platform/store.py](../../../src/evidence_rag/platform/store.py) |

### 3.2 当前实体

| 实体 | 内容 |
| --- | --- |
| NotebookTemplate | source URI、current content hash、kernel/language、metadata、ACL |
| NotebookRun | version、status、time、parameters、raw object、content hash、Run binding |
| NotebookCell | ordinal、type、source/hash、execution count、tags、locator |
| NotebookOutput | ordinal、type、text、MIME types、error、artifact ref |

### 3.3 当前解析

- JSON `cells` 数组；
- Markdown/Code/Raw source；
- execution_count；
- tags/metadata；
- Output text 或 `text/plain`；
- image MIME 只记录 `binary_payload_omitted`；
- error name/value；
- Papermill start/end；
- 第一个 `parameters` tag Cell 的 `key=value` 文本解析。

### 3.4 当前状态判定

只要任一 Output 有 error_name，整个 NotebookRun 为 `failed`，否则 `completed`。

缺少：

- Cell 未执行；
- stale output；
- partially executed；
- error 后恢复；
- execution order gap；
- kernel restart；
- cancelled/timeout；
- parameter injection provenance。

### 3.5 当前比较

按 ordinal 比较：

- source hash；
- outputs 完整对象；
- execution count；
- output count；
- parameter dict。

在中间插入一个 Cell 会导致后续 ordinal 错位；没有使用 nbformat Cell ID、move detection 或
source matching。

### 3.6 当前搜索

Notebook 没有专用 Search View。跨源搜索：

- 搜 Template name；
- 或任意 Cell source；
- 返回 NotebookRun；
- snippet 为所有 Cell source 各取最多 500 字符后 group_concat；
- 不搜索 Output/Error/Parameter；
- 不返回命中的 Cell；
- 不处理 execution order、dependency 或 artifact。

### 3.7 当前关系

- ExperimentRun `has_notebook` NotebookRun；
- RawObject `DERIVED_INTO` Run/Cell/Output。

缺少 Notebook 内部显式关系图。

### 3.8 当前活跃规模

NotebookTemplate、Run、Cell、Output 均为 0。

### 3.9 当前优点

- 真实 `.ipynb` parser；
- raw immutable object 和 derivation；
- content hash + version 幂等；
- Cell/Output stable locator；
- local path allowlist 和 size limit；
- kernel/language/tags/parameters；
- binary payload 不直接写文本；
- 可绑定 Experiment Run；
- 结构比较已经有基础。

### 3.10 当前失败模式

| 失败 | 原因 |
| --- | --- |
| 找不到具体 Cell/Output | 只返回 NotebookRun |
| 错误输出不能检索 | search 只查 Cell source |
| 代码与 Markdown 同处理 | 无类型专用 Unit |
| 跨 Cell 变量关系缺失 | 无 def-use/dataflow |
| 执行顺序丢失 | 主要按 ordinal 展示 |
| stale output 不可识别 | source/output execution provenance 不完整 |
| 参数解析弱 | 简单 `line.split("=")`，值为字符串 |
| 比较大量误报 | ordinal 对齐 |
| 图像/HTML 丢语义 | binary omitted，无安全渲染/描述 |
| status 过粗 | any error → failed |
| Notebook 与 Run 关系弱 | 只保存 has_notebook |
| 当前无数据 | 无法评测 |

## 4. 查询任务目录

### 4.1 Notebook/Cell 定位

- 哪个 Notebook 定义了 `build_index`？
- 参数 Cell 在哪里？
- 第 12 个 Cell 做什么？

必需：

- exact Notebook revision；
- exact Cell；
- cell type/locator。

### 4.2 代码逻辑

- 这个 Notebook 如何加载和处理数据？
- 哪个 Cell 计算最终指标？

必需：

- target code cell/block；
- imports/definitions；
- upstream/downstream cells；
- execution state。

### 4.3 参数与配置

- learning_rate 在哪里设置？
- baseline/candidate Notebook 参数有什么不同？

必需：

- ParameterDefinition/Value；
- injected/default；
- run/revision；
- typed value。

### 4.4 输出和结果

- 哪个 Cell 输出了 0.82？
- 图表由哪个 Cell 产生？

必需：

- Output；
- producer CellExecution；
- output type/unit/context；
- linked Artifact/Metric if confirmed。

Notebook 输出文本不是 Experiment Metric，除非存在确认关系。

### 4.5 错误与调试

- Notebook 为什么失败？
- 哪个 Cell 首先报错？
- 错误发生后是否重新执行成功？

必需：

- ErrorOutput；
- CellExecution；
- execution order；
- traceback/error；
- retry/later output；
- final status。

### 4.6 数据/变量 lineage

- `df_clean` 从哪里产生并被哪里使用？
- 这个图依赖哪些输入 Cell？

必需：

- definitions/reads/writes；
- execution order；
- external input/artifact；
- dependency path。

### 4.7 版本比较

- 两个 Notebook Run 改了哪些参数、Cell 和输出？
- 是移动 Cell、修改代码还是只重新执行？

必需：

- robust Cell matching；
- source diff；
- execution diff；
- output diff；
- parameter/kernel/environment diff。

### 4.8 复现检查

- 这个 Notebook 是否可复现？
- 缺少哪些外部文件、环境或顺序？

必需：

- parameters；
- kernel/environment；
- code commit if linked；
- data/artifact inputs；
- execution order；
- outputs/status；
- missing dependencies。

## 5. 目标领域模型

### 5.1 实体

| 实体 | 说明 |
| --- | --- |
| NotebookTemplate | 逻辑 Notebook |
| NotebookRevision | 文件内容版本 |
| NotebookExecution | 一次执行状态 |
| NotebookCellVersion | 一个 revision 中的 Cell |
| CellExecution | Cell 在一次 NotebookExecution 中的执行 |
| ParameterDefinition | 参数名/type/default |
| ParameterValue | execution 中的值和 provenance |
| NotebookSymbol | Cell 中定义的变量/函数/class/import |
| NotebookOutput | 原始输出 |
| NotebookError | 规范化 error/traceback |
| NotebookArtifact | image/table/file/html 等制品 |
| KernelEnvironment | kernel/runtime/dependencies |

### 5.2 关系

- Template HAS_REVISION；
- Revision HAS_CELL；
- Execution EXECUTES Revision；
- Execution HAS_CELL_EXECUTION；
- CellExecution EXECUTES_CELL；
- CellExecution PRODUCES Output/Artifact；
- Cell DEFINES/READS/WRITES NotebookSymbol；
- CellExecution RUNS_BEFORE CellExecution；
- Output DERIVED_FROM CellExecution；
- ParameterValue INSTANCE_OF ParameterDefinition；
- Cell USES_PARAMETER；
- Cell READS_ARTIFACT / WRITES_ARTIFACT；
- Cell DEPENDS_ON Cell；
- Execution USES_ENVIRONMENT；
- ExperimentRun EXECUTED_BY NotebookExecution；
- Notebook MetricCandidate MAPS_TO Experiment Metric（需复核）。

## 6. 身份、版本和执行状态

### 6.1 Cell Identity

优先级：

1. nbformat `cell.id`；
2. stable source hash + cell type；
3. semantic/source similarity + neighborhood；
4. ordinal fallback。

Cell move 不应变成 remove+add。

### 6.2 Revision vs Execution

`.ipynb` 文件同时存 source 和 output。需要区分：

- NotebookRevision：当前 Cell source/metadata；
- NotebookExecution：保存的 execution counts/outputs；
- Source after execution 是否发生修改；
- output 是否与当前 source 对应。

没有足够 provenance 时标记：

- `output_alignment=unknown`；
- `execution_completeness=partial/unknown`。

### 6.3 状态

NotebookExecution：

- never_executed；
- partially_executed；
- completed；
- completed_with_errors；
- failed；
- interrupted；
- stale_outputs；
- unknown。

CellExecution：

- not_executed；
- succeeded；
- failed；
- stale；
- unknown。

## 7. Parser V2

### 7.1 Cell

保留：

- nbformat ID；
- type；
- source；
- source hash；
- display ordinal；
- execution count；
- tags；
- collapsed/hidden；
- papermill metadata；
- trusted state。

### 7.2 Parameter

优先：

1. Papermill parameter metadata；
2. tagged parameter cell；
3. Python AST assignment；
4. language-specific parser；
5. raw string fallback。

保留 typed value：

- bool/int/float/string/list/dict/null/expression；
- secret redaction；
- source/injected/default。

不执行任意 Cell 来解析参数。

### 7.3 Code

按 kernel language：

- Tree-sitter/AST；
- imports；
- function/class definitions；
- top-level variable definitions；
- identifier reads/writes；
- file/data access calls；
- plotting/display calls；
- metric logging calls；
- shell magic；
- SQL magic；
- package install。

### 7.4 Output

按 output type：

- stream：stdout/stderr；
- execute_result；
- display_data；
- error；
- update_display_data。

MIME：

- text/plain；
- text/html 安全清洗；
- application/json；
- dataframe/table；
- image：保存 Artifact ref、尺寸/checksum，可选安全描述；
- JS 不执行；
- binary 存 Raw/Artifact，不内联 Search View。

### 7.5 Error

保留：

- ename/evalue；
- traceback text（ANSI 清理）；
- first relevant frame；
- file/path/line；
- producer Cell；
- later retry relation。

## 8. Cell Dependency Graph

### 8.1 静态依赖

```text
Cell A DEFINES x
Cell B READS x
→ B DEPENDS_ON A
```

同名变量选择：

- 在 execution order 上最近的 prior writer；
- 无 execution count 时按 display order 但低置信；
- kernel restart 切断状态；
- wildcard/dynamic execution 标记 unresolved。

### 8.2 外部依赖

- file path；
- URL；
- database/table；
- package；
- environment；
- dataset/artifact；
- Repository module。

### 8.3 不确定性

动态语言、`exec/eval`、魔法命令和 notebook state 会导致不完整。每条 DEPENDS_ON 记录：

- derivation；
- confidence；
- order basis；
- unresolved reason。

## 9. Retrieval Unit

| Unit | 内容 |
| --- | --- |
| `notebook.surface` | title、Markdown headings、parameters、status |
| `markdown.section` | heading path + narrative cells |
| `code.cell.surface` | cell ID、definitions、imports、source summary |
| `code.ast_block` | Cell 内 AST block |
| `parameter.fact` | name/type/value/provenance |
| `output.text` | producer + normalized output |
| `output.table` | schema/header/selected rows |
| `output.figure` | caption/metadata/producer |
| `error.window` | error + traceback + producer |
| `execution.episode` | parameter→cells→outputs→status |
| `comparison.surface` | matched Cell/parameter/output diffs |

## 10. 索引

### 10.1 Exact/Structured

- template/revision/execution ID；
- cell ID/ordinal/type/tag；
- execution count/status；
- parameter name/value/type；
- symbol/import；
- output type/MIME/error name；
- artifact/file path；
- kernel/language；
- experiment/run binding。

### 10.2 Sparse

不同字段：

- Markdown heading/narrative；
- code identifier；
- error/traceback；
- output text；
- parameter；
- path/table column。

### 10.3 Dense

Profiles：

- notebook_narrative；
- notebook_code；
- notebook_error；
- notebook_output；
- notebook_table/figure caption。

不要把二进制和整本 Notebook 拼接成一个向量。

### 10.4 Graph

索引：

- display tree；
- execution order；
- def-use dependency；
- producer-output；
- parameter use；
- external artifact；
- version/cell lineage。

## 11. Query Profile

```python
NotebookQueryProfile(
    task="error_trace",
    template_ids=[],
    execution_ids=[],
    cell_types=["code"],
    statuses=["failed"],
    parameters={},
    symbols=[],
    output_types=["error"],
    traversal=["PRODUCES", "DEPENDS_ON", "RETRIES"],
    order="execution",
)
```

## 12. Candidate Generation

### 12.1 通道

- exact Notebook/Cell/parameter；
- structured cell/output/error filter；
- Markdown sparse/dense；
- code exact/sparse/dense；
- error sparse/dense；
- output/table search；
- dependency graph；
- version/comparison；
- Experiment binding。

### 12.2 任务策略

| Task | 主通道 |
| --- | --- |
| cell location | exact/sparse |
| code logic | code + dependency graph |
| parameter | structured |
| output | output + producer |
| error | error + execution graph |
| lineage | def-use graph |
| comparison | cell lineage + structural diff |
| reproduce | role/path coverage |

## 13. Notebook Reranker

特征：

- task/unit type；
- exact cell/symbol/parameter；
- heading context；
- producer-output completeness；
- execution status/order；
- dependency distance；
- current revision match；
- output alignment；
- stale/unknown；
- Run/Experiment binding；
- duplicate code/output；
- binary omitted/truncated。

负面：

- stale_output；
- wrong_revision；
- markdown_mention_only；
- unexecuted_cell；
- same_symbol_other_notebook；
- ordinal_shift；
- output_without_producer；
- copied_metric_unbound。

## 14. Context Builder

### 14.1 代码逻辑

```text
Notebook Revision / Kernel
Markdown Heading
Target Cell / AST Block
Upstream Definitions
Downstream Uses
Execution Order
Outputs
State/Alignment Warnings
```

### 14.2 Error

```text
Parameter Snapshot
Prior Dependency Cells
Failed Cell
Traceback
Later Retry/Success
Final Notebook Status
```

### 14.3 Output

```text
Question-matched Output
Producer Cell
Relevant Parameters
Input Dependencies
Output Type/Unit
Artifact Locator
Experiment Metric Binding or "unconfirmed"
```

### 14.4 Compare

```text
Template/Revisions
Parameter Diff
Matched Cells
  moved / source changed / only re-executed / output changed
Execution Order Diff
Error/Output Diff
Kernel/Environment Diff
```

## 15. Notebook Compare V2

### 15.1 Matching

1. cell.id exact；
2. source hash；
3. AST/Markdown similarity；
4. neighbor context；
5. ordinal fallback。

### 15.2 Change Types

- added；
- removed；
- moved；
- source_modified；
- metadata_modified；
- reexecuted_same_source；
- output_changed；
- execution_order_changed；
- stale_output_changed；
- parameter_injected。

Output comparison uses normalized content/checksum，不直接比较数据库 ID/created_at。

## 16. 增量与版本

- Raw `.ipynb` content-addressed；
- Template 逻辑 identity；
- Revision content hash；
- Execution output hash；
- Cell stable ID/source hash；
- Parser/unit/embedding/dependency builder versions；
- unchanged Cell vectors reuse；
- changed upstream definition triggers dependency recompute；
- tombstone source file deletion；
- old Revision/Execution 仍可按 version 查。

## 17. ACL、安全和质量

- Notebook/Cell/Output 继承 ACL；
- secret scan 覆盖 source、parameter、output、traceback；
- HTML sanitize；
- JS 不执行；
- image/binary 只做安全 Artifact；
- local paths 不向无权用户泄露；
- notebook trust metadata 不作为平台信任；
- shell magic/package install 只作为代码文本；
- 远程 URL/DB 不自动访问；
- output stale/unknown 显式；
- Notebook output 不自动固化为 Experiment Metric。

## 18. 样例数据

当前正式库为空，至少创建：

- 1 个 Papermill Notebook；
- 2 个参数化 executions；
- Markdown + code + table + figure + stream；
- 1 个 error execution；
- 1 个 retry success；
- 中间插入/move Cell 的 revision；
- stale output case；
- execution order 与 display order 不同；
- external file/data dependency；
- linked Experiment Run；
- unconfirmed metric-like output。

## 19. Golden Set

### 19.1 首版 40 条

| Slice | 数量 |
| --- | ---: |
| Notebook/Cell location | 5 |
| code logic | 6 |
| parameter/config | 5 |
| output/table/figure | 6 |
| error/retry | 6 |
| dataflow/lineage | 5 |
| version compare | 4 |
| reproduction/unanswerable/security | 3 |

### 19.2 Hard Negative

- 同名变量不同 Notebook；
- Markdown 提到变量但未定义；
- stale output；
- unexecuted Cell；
- ordinal shift；
- same source, different output；
- metric-like text 未绑定；
- error 后成功 retry；
- current revision vs old execution；
- parameter string vs typed number。

### 19.3 指标

- Cell Recall@10；
- Output/Error Recall@10；
- exact parameter accuracy；
- producer-output path recall；
- def-use path precision/recall；
- execution order accuracy；
- stale output detection；
- Cell matching/change classification；
- reproduction role coverage；
- locator accuracy；
- duplicate/distractor；
- P95/index/storage。

## 20. 消融

| Run | 变量 |
| --- | --- |
| N-B0 | 当前 NotebookRun substring |
| N-B1 | typed Cell/Output Units |
| N-B2 | code/markdown/error profiles |
| N-B3 | execution + dependency graph |
| N-B4 | Notebook reranker |
| N-B5 | compare v2 |
| N-B6 | structured context |

## 21. 发布门槛

| 指标 | Target |
| --- | ---: |
| Cell Recall@10 | ≥ 0.90 |
| Output/Error Recall@10 | ≥ 0.90 |
| Parameter Accuracy | ≥ 0.98 |
| Producer Path Recall | ≥ 0.95 |
| Execution Order Accuracy | ≥ 0.98 |
| Dependency Path Recall | ≥ 0.80 |
| Stale Output Detection | ≥ 0.95 |
| Cell Match Accuracy | ≥ 0.95 |
| Locator Accuracy | ≥ 0.98 |
| Secret/Unauthorized Leakage | 0 |
| P95 | ≤ 1 s（样例规模） |

## 22. 实施映射

### 22.1 新模块

```text
src/evidence_rag/rag/sources/notebook/
  contracts.py
  adapter_v2.py
  parameter_parser.py
  code_analyzer.py
  output_parser.py
  execution_state.py
  dependency_graph.py
  cell_matcher.py
  unit_builder.py
  retriever.py
  reranker.py
  context_builder.py
  evaluator.py
```

### 22.2 Schema

- `notebook_revisions`；
- `notebook_executions`；
- `notebook_cell_versions`；
- `notebook_cell_executions`；
- `notebook_parameters`；
- `notebook_symbols`；
- `notebook_artifacts`；
- `notebook_retrieval_units`；
- `notebook_edges`；
- `notebook_comparisons_v2`。

### 22.3 Feature Flags

```text
RAG_NOTEBOOK_ADAPTER=jupyter-v1|jupyter-v2
RAG_NOTEBOOK_SEARCH=run-v1|cell-output-v2
RAG_NOTEBOOK_DEPENDENCY=false|true
RAG_NOTEBOOK_RERANKER=off|profile
RAG_NOTEBOOK_COMPARE=ordinal-v1|identity-v2
RAG_NOTEBOOK_CONTEXT=snippet-v1|execution-v2
```

### 22.4 Sprint

#### N0：样例/Golden

#### N1：Revision/Execution/Cell 模型

#### N2：Parameter/Output/Error Parser

#### N3：Code/Dependency Graph

#### N4：Retriever/Reranker

#### N5：Compare/Context

#### N6：Security/Release

## 23. 测试计划

- invalid/malformed notebook；
- size/path limit；
- nbformat cell ID；
- parameter typed parsing；
- secret parameter/output；
- stream/display/error；
- HTML/JS sanitization；
- image artifact omission；
- execution_count gaps；
- display vs execution order；
- stale output；
- kernel restart；
- def-use shadowing/redefinition；
- magic/exec unresolved；
- inserted/moved Cell；
- output normalized diff；
- Experiment binding；
- metric-like output unconfirmed；
- ACL Cell/Output leakage。

## 24. 完成定义

1. Notebook 有独立 Golden 和 Retriever；
2. Revision 与 Execution 分开；
3. Cell/Output/Error 可独立召回；
4. display/execution order 分开；
5. parameter typed；
6. Cell dependency graph 有不确定性；
7. stale output 显式；
8. compare 不再只按 ordinal；
9. binary/HTML/secret 安全；
10. Experiment Metric 不自动从输出推断；
11. 面试文档填入真实 Run。
