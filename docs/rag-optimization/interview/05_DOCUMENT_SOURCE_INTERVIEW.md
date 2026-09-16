# Scientific Document RAG 面试与简历材料

对应设计：[05_DOCUMENT_SOURCE_RAG.md](../sources/05_DOCUMENT_SOURCE_RAG.md)

## 1. 当前状态声明

### FINAL_REPOSITORY_STATUS（2026-07-29）

- D0–D6 仓库内工程 `COMPLETE`：50-case Golden、hierarchical units、typed
  exact/sparse/dense/table/graph retrieval、table fact、claim validation、layout/OCR/
  figure/citation、version/release 均已实现；
- baseline artifact：
  `evaluation-run://project-document-db0-v1/b6c5c5e8f21446d5a0038d23ffed5acf`，
  set `sha256:add47a0d55c82d98071a3b6bc4857446f61fd3cacc51a355c27865ffb46cc2a4`；
- 当前结论仍为 `VERIFIED_NON_QUALIFIED / HOLD_DEFAULT_V1`；正式库
  migration/backfill、production parser diversity、shadow/canary 未完成。

### HISTORICAL_MEASURED（未在本轮重测）

- 正式库有 4 篇 Markdown 文档；
- 279 个 Section、48 个 Table、1,270 个 TableCell；
- Page/Figure/Citation 当前均为 0；
- 32 个 Claim 均由 `numeric_rule_v1` 抽取，类型为 performance，状态为 reported；
- ClaimEvidence、Claim Match、Table Metric Match、Review、Aggregation 均为 0；
- 搜索只返回 Document、Claim、Table；
- Document snippet 固定全文前 1,200 字；
- Table 将 Cell 直接拼接；
- 无 BM25、dense、document reranker、version alignment。

### CURRENT_IMPLEMENTED

- Markdown/TXT/PDF/DOCX/HTML parser；
- RawObject/content hash/adapter version；
- Section/Page/Table/Cell/Figure/Citation/Claim schema；
- support/refute/qualify evidence；
- TableCell↔Metric candidate/review；
- 多 Metric 聚合复算骨架；
- ACL scope、path allowlist、secret quarantine。

### 2026-07-29 早期中间实现（已被 FINAL_REPOSITORY_STATUS 覆盖）

- Document 已进入 Code/Codex/Experiment/Notebook/Workspace 共用的统一 evidence
  block/context、ACL、portable locator、citation 与 relation expansion 主路；
- Query 的 retrieval context 与 comprehension context 已分层，文档 Claim/Table 只有
  在最终选中后才进入理解层，不会把隐藏推理写成事实；
- 没有重读正式库，因此上面的数量只作历史基线；本次没有冒充完成 BM25/dense、
  layout-aware OCR、document-specific reranker 或新的 quality Run。

### ORIGINAL_TARGET（仓库内工程已完成，production qualification 未完成）

- typed Paragraph/Claim/Table/Figure/Formula/Citation Retrieval Unit；
- exact+sparse+dense+table+graph；
- layout-aware PDF/OCR；
- source/derived/reviewed fact 分离；
- Claim candidate/accepted/validation levels；
- header-aware Table Fact；
- Family/Version alignment 和 stale propagation；
- document-specific reranker/context。

## 2. 15 秒定位

> 我没有把科学文档 RAG 设计成统一 chunk 检索，而是将段落、原子 Claim、表格事实、
> 图、公式和引用作为不同证据类型。系统先命中精确 Retrieval Unit，再按文档层级扩展，
> 并通过 TableCell→Metric 与 Claim→Evidence 的受审关系，把“文档报告了什么”和
> “当前证据真的支持什么”分开。

## 3. 1 分钟讲解

> 当前系统已经能摄取 Markdown、PDF、DOCX、HTML，也有 Section、Table、Cell、Figure、
> Citation 和 Claim schema，甚至有 TableCell 对齐实验 Metric、聚合复算和人工评审的
> 骨架。但搜索仍是字符串匹配：整篇文档只展示开头，表格全部拼接，Section、Cell、
> Figure 和 Citation 无法直接检索；现有 32 条 Claim 也全部是规则抽取、尚无证据。
>
> 我的优化分三层。第一层是文档结构，把 Family、Version、Section、Paragraph 和 page/bbox
> 建成稳定层级，做小单元检索和父结构扩展。第二层是 typed retrieval：表格按 header
> path、单位和 typed value 检索，Claim 按 subject、metric、dataset、split、qualifier
> 检索，Figure/Formula/Citation 各有专用视图。第三层是证据治理：自动抽取只产生
> candidate，source-authored、model-derived 和 reviewed facts 分开；Claim 验证分为原文、
> 身份、数值、实验设计和当前有效五层，并保留 support/refute/qualify 冲突集合。

## 4. 白板图

```text
PDF / DOCX / Markdown / HTML
             ↓
      Family → Version
             ↓
Paragraph | Claim | Table Fact | Figure | Formula | Citation
             ↓
 Exact + Sparse + Dense + Table + Graph
             ↓
 Document Reranker → Parent/Evidence Expansion
             ↓
 Author Text + Derived Fact + Review/Validation
```

## 5. 关键设计取舍

### 5.1 为什么不统一 Chunk

段落检索追求语义相关，表格查询追求行列与单位精确，Claim 验证追求 evidence roles，
Citation 查询追求 marker→reference resolution。统一 Chunk 会同时损失结构、精度和可解释性。

### 5.2 为什么是“小单元检索，父结构理解”

直接嵌入大 Section 会稀释关键词和数值；只返回小 Cell/Claim 又缺上下文。先用小 Unit
提高召回精度，再按 parent、neighbor、header、footnote 定向扩展，可同时控制 token 和语义。

### 5.3 为什么自动 Claim 不能直接成为事实

规则或模型抽取可能拆错复合句、漏掉否定和条件。它只能是 ClaimCandidate；保留 source
span、confidence 和 extractor version，经接受后成为 `reported`，再通过证据验证。

### 5.4 为什么数值相同仍不能验证

`0.82` 可能对应不同 metric、dataset、split、unit 或 aggregation。必须同时对齐
MetricDefinition、DatasetVersion、Run、单位和聚合口径；数字相同只是 candidate signal。

### 5.5 为什么不能让 refute 一票否决

refute 可能低置信、旧版本或设计不可比。系统保存冲突集合，按 authority、freshness、
independence 和 applicability 判断，并明确 qualifier，而不是简单多数投票或一票否决。

## 6. 最突出的技术点

### Typed Scientific Retrieval

- Paragraph/Claim/Table/Figure/Formula/Citation 多类型 Unit；
- source-text 与 derived-summary multi-vector；
- task-specific reranker；
- precise locator。

### Header-aware Table Fact

- row/column semantic path；
- `mean ± std`、百分比、unit 和 footnote；
- MetricDefinition compatibility；
- TableCell→Metric→Run 的可审计链；
- 多 seed aggregation 复算。

### Layered Claim Validation

```text
L0 文档确实报告
L1 证据身份正确
L2 数值/单位/split 一致
L3 实验设计可比
L4 当前版本仍有效
```

不再使用一个模糊的 `verified` 覆盖所有用途。

### Document Version Staleness

Section、Claim、Table 跨版本对齐；新文档版本、支撑 Run 或代码版本变化会产生
`potentially_stale`，需要重新验证。

## 7. 高频追问

### 双栏和扫描 PDF 怎么处理？

先用 layout-aware parser 产出 block、reading order、page/bbox；文本质量低时启用 OCR。
parser/OCR 输出是 derived fact，带版本和 confidence。若降级到 pypdf text，会在结果和
trace 中标 `parse_quality=degraded`。

### Table 怎么向量化？

不嵌入一个巨大的整表字符串。建 TableSchemaView、TableRowView 和 TableCellFactView；
表头 path 与 typed fields 走 structured/exact，语义名走 sparse/dense，数值走 typed
numeric index。

### 如何处理 merged cell？

解析为 header tree，将跨行/跨列 header 传播到叶子 Cell 的 semantic path，同时保留原始
span。渲染时显示 path，不让模型按坐标猜。

### Figure 没有 caption 怎么办？

使用邻近段落、OCR 和可选视觉描述生成 candidate FigureView，但明确标记 derived。
若无法可靠解释，则返回 artifact 和 missing evidence，不编造趋势。

### Citation 被解析出来就代表支持吗？

不是。CitationMention 只证明作者在该位置引用某工作。还需解析 reference、判断 citation
intent，Claim 支持关系必须另行确认。

### 如何避免长文 top-k 偏置？

全局总结先取 SectionSummary/Claim 做结构覆盖，再回原段落和表/图验证；使用 coverage
约束，而不是从全部段落直接取一个全局 top-k。

### Claim 怎么拆？

抽取 subject、predicate、value、metric、baseline/candidate、dataset、split、unit、
uncertainty 与 qualifier。一个复合句拆成多个 atomic claims，但共享的条件和原句 span
必须保留。

### 如何评估 Claim 验证？

核心不是 answer BLEU，而是 relation precision、validation precision、unsupported
verification rate、numeric+unit accuracy、conflict recall 和 abstention。

## 8. 必做消融

| Run | 变量 | 要回答的问题 |
| --- | --- | --- |
| D-B0 | 当前 substring | 当前真实下限 |
| D-B1 | typed Unit | 类型拆分是否有效 |
| D-B2 | sparse+dense | 语义召回增益 |
| D-B3 | hierarchy expansion | 上下文完整性 |
| D-B4 | table retrieval | 行列/单位精度 |
| D-B5 | Claim schema/evidence | 验证精度 |
| D-B6 | layout/OCR | PDF 定位与召回 |
| D-B7 | document reranker | nDCG/Recall |
| D-B8 | version policy | 陈旧结论控制 |

## 9. 失败实验占位

### 失败一：整表 embedding

- 假设：一个 Table 一个向量实现简单且足够；
- 现象：大表 header 与具体 Cell 被稀释，相同数字 hard negative 多；
- 指标：Cell Recall@10、Header Path Accuracy；
- 修正：Schema/Row/Cell Fact 多级视图；
- Run：TBD。

### 失败二：自动 Claim 直接入库

- 假设：LLM 抽取比规则准，可直接创建 Claim；
- 现象：否定、复合句、条件漏失造成高风险 false verification；
- 指标：Claim Atomicity、Unsupported Verification；
- 修正：Candidate→Accept→Validate 状态机；
- Run：TBD。

### 失败三：所有 refute 一票否决

- 假设：出现反例就 contradicted；
- 现象：旧版本/不可比 dataset/低置信关系导致误判；
- 指标：Claim Status Macro-F1；
- 修正：applicability、authority、freshness、independence；
- Run：TBD。

## 10. 性能与成本取舍

| 决策 | 收益 | 成本/约束 |
| --- | --- | --- |
| typed views | 精确、可解释 | 索引数量增加 |
| source+summary multi-vector | recall 提升 | embedding 成本增加 |
| layout/OCR | PDF 覆盖 | ingest 延迟、外部模型风险 |
| table structured index | 数值可靠 | parser/schema 复杂 |
| parent expansion | context 完整 | token 增加 |
| model reranker | 排序提升 | P95/推理成本 |
| generation publish | 可回滚 | 双份索引存储 |

推荐优先 exact/sparse/table，dense 与 reranker shadow rollout；OCR 仅在质量阈值触发。

## 11. 指标证据表

| 指标 | Current | Target/完成后实测 |
| --- | ---: | ---: |
| Documents | 4 | TBD |
| Sections | 279 | TBD |
| Tables/Cells | 48 / 1,270 | TBD |
| Claims | 32 reported | TBD |
| Evidence links | 0 | TBD |
| Golden queries | 0 | 50 |
| Entity Recall@10 | 未测 | TBD |
| Locator Accuracy | 未测 | TBD |
| Table Header Path Accuracy | 未测 | TBD |
| Numeric+Unit Accuracy | 未测 | TBD |
| Claim Validation Precision | 未测 | TBD |
| Unsupported Verification Rate | 未测 | TBD |
| P95 | 未测 | TBD |

## 12. 简历 Bullet

### 设计完成、实现未完成

> 设计 Scientific Document RAG，将 Paragraph、Atomic Claim、Table Fact、Figure、
> Formula 与 Citation 拆为 typed retrieval units，采用小单元召回与层级上下文扩展，
> 并设计 TableCell→Metric 与分层 Claim Validation 的可审计证据链。

### 完成基础实现后

> 实现多格式 Scientific Document RAG，在 Markdown/PDF/DOCX/HTML 上构建
> exact+sparse+dense+table+graph 混合检索，将 `[N]` 条 Golden Query 的 Entity
> Recall@10 从 `[A]` 提升至 `[B]`，Locator Accuracy 达 `[C]`。

### 完成证据验证后

> 构建 header-aware Table Fact 与五级 Claim Validation，将 Numeric+Unit Accuracy
> 提升至 `[D]`、Claim Validation Precision 达 `[E]`，Unsupported Verification Rate
> 控制在 `[F]`，支持文档版本更新后的 stale detection 与可审计 revalidation。

## 13. STAR 叙事

### Situation

系统支持多格式文档并有 Claim/表格 schema，但真实搜索仍是字符串匹配，结构化实体没有转化
为可检索、可验证的证据。

### Task

将科学文档从“全文搜索”升级为能准确定位、解释表格、验证 Claim、处理版本的单源 RAG。

### Action

- 建 typed Retrieval Unit；
- 做 layout hierarchy 与精确 locator；
- 将 Table 转为 header-aware facts；
- 将 Claim 改为 candidate/accepted；
- 建分层 validation 与 conflict set；
- 通过 generation、ACL、lineage 管理派生内容。

### Result

当前只能写“完成设计”；实现后必须用 Golden、消融、P95 和 unsupported verification 等
真实 Run 填写，不能用目标值冒充结果。

## 14. 历史边界与当前不能夸大

- 当前只有 Markdown 文档，没有正式 PDF/DOCX/HTML 活跃样本；
- 当前没有 Page/Figure/Citation 数据；
- 当前 32 条 Claim 都未验证；
- 当前没有 ClaimEvidence 和 review；
- 当前 Table↔Metric 候选/聚合为空；
- 当前不是向量 Document RAG；
- 当前没有 layout/OCR；
- 当前没有 version alignment/stale propagation；
- 设计指标不是实测结果；
- “有 schema”不等于“检索和证据链已可用”。

## 15. 2026-07-29 早期中间工程落地（历史记录）

- 新增 `rag/document_v2.py`，从现有 DocumentStore 只读派生
  ScientificDocument/Section/Claim/Table/TableCell/Figure/Citation/Page typed units。
- query task 区分 claim、numeric/table、citation、figure、conflict；lexical/exact 与
  source-specific role/status 进行确定性 rerank。
- TableCell 保留 row/column、numeric values 与 parent table；Claim 保留 section parent、
  status、claim type、extraction method 和 evidence count，所有 locator 可回溯。
- 最终 context 才做 parent expansion，避免同一父文档覆盖 child citation；冲突/陈旧
  Claim 独立进入 conflict channel。
- 输出 `document-evidence-context-v2`，带 content digest、父级摘要、缺失角色、
  secret/path 清洗和 reasoning exclusion。
- Platform/Query 仅在 `X-RAG-Document-Engine: v2` 时启用，document scope 与 ACL
  fail-closed；默认及回滚为 V1。

真实边界：当前工程没有新增 OCR/layout/dense 模型或 production quality Run，也没有把
reported Claim 自动升级为 verified。状态为
`ENGINEERING COMPLETE / DEFAULT_V1 / QUALITY_UNAVAILABLE`。

## 16. 2026-07-29 原始路线图最终工程状态

- D0：released 50-case Golden，8 slices=`8/8/10/10/5/4/3/2`，24 hard-negative
  cases、2 refusal cases 和 portable baseline；
- D1：family/version/page/layout/section/summary/paragraph/claim/table/figure/formula/
  reference/citation typed units，isolated atomic/idempotent store；
- D2：exact/sparse/table/graph/local-hash dense、task profile、source-local fusion/rerank；
- D3：row/header path、typed center/spread/unit、row footnote 与 stable cell identity；
- D4：candidate/reviewed claim 分层、L0–L4 evidence checks、support/refute/qualify；
- D5：layout/OCR fallback、formula/figure/reference/citation resolver 及完整内容安全扫描；
- D6：same-family version alignment、staleness、generation/ACL/tombstone、rollback、
  portable artifact 与六阶段 release evaluator。

当前质量短板必须照实保留：claim validation `9/10=.9` 低于 `.95` release threshold；
因此不允许默认 V2 或宣称 production qualified。
