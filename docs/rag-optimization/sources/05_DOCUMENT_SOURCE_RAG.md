# Scientific Document 单源 RAG 详细设计

状态：`REPOSITORY_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD`  
设计版本：document-source-rag-v2  
基线日期：2026-07-27  
对应面试文档：[05_DOCUMENT_SOURCE_INTERVIEW.md](../interview/05_DOCUMENT_SOURCE_INTERVIEW.md)

> **2026-07-30 实现覆盖层**
>
> 本文正文仍是功能与退出门基线。显式 Document V2 已从 Runtime/Platform 到达完整
> typed section/claim/table/figure/formula/citation、层级 expansion、retrieval 与
> context facade；正式 Document/Workspace 数据为 authority，派生索引惰性、可丢弃且
> 生命周期由 Runtime 管理。正式 claim identity、parent/document locator 在 Platform
> projection 中保留。默认 V1 与回退不变；OCR/vision、真实 50-case replay、reviewed
> calibration、shadow/canary 尚未完成，所以这里只表示仓库 L3。

## 1. 来源定位

Document Source 不是“把 PDF 或 Markdown 切块后做向量检索”，而是承载以下异构证据的
科学文档域：

```text
Document Family
└── Document Version
    ├── Page / Layout Block
    ├── Section / Paragraph
    ├── Atomic Claim
    ├── Table
    │   ├── Header / Dimension
    │   ├── Row
    │   └── Cell Fact
    ├── Figure / Caption
    ├── Formula
    ├── Citation / Reference
    └── Footnote / Appendix
```

该来源至少包含五种不同语义：

- 叙事：背景、方法、分析与限制；
- 声明：可以被支持、反驳或限定的原子 Claim；
- 数值事实：带行列语义、单位、split 和脚注的表格值；
- 多模态证据：图、公式、caption 与邻近段落；
- 引用链：文中 citation context 与参考文献目标。

它们不能共用同一种 Chunk、同一套相似度分数和同一种 Context 模板。

## 2. 物理入口与边界

### 2.1 当前入口

| 格式 | 当前 parser | 结构能力 |
| --- | --- | --- |
| Markdown/inline text | heading/line parser | Section、Markdown Table、Citation regex |
| TXT | line parser | Section、Citation regex |
| PDF | `pypdf` text | Page 及 page-size metadata |
| DOCX | `python-docx` | Heading Section、Table、inline shape |
| HTML | `HTMLParser` text | 扁平文本 Section |

当前摄取还提供：

- local root allowlist；
- `content` 5,000,000 字符上限；
- title/version 幂等；
- RawObject、content hash、adapter/schema version；
- secret quarantine；
- Project/Iteration 归属；
- stable URI 风格实体 ID。

### 2.2 目标入口

P0：

- 保持现有五类格式；
- PDF 增加 layout-aware parser；
- scanned PDF 增加 OCR fallback；
- Markdown/DOCX/HTML 保留 block hierarchy；
- 解析 reference list、footnote、formula；
- 表格保留 header tree、merged cell、单位和脚注；
- figure 提取 caption、artifact、page/bbox。

P1：

- LaTeX/TeX；
- arXiv/JATS XML；
- Office PPTX 中的正式报告页；
- Crossref/OpenAlex metadata enrichment；
- 受控视觉模型生成 FigureDescription。

P2：

- 网页递归抓取；
- 通用企业 Wiki；
- 音视频转录。

P2 不应混入 P0，因为它们的 ACL、版本、采集和引用语义不同。

### 2.3 来源边界

- 文档中引用的代码片段不是 Repository 当前代码；
- 文档表格报告的数值不是 Experiment Metric 原始观测；
- 文档中的 Notebook 截图不是 Notebook Execution；
- 作者写下的实验配置不是 Run 实际配置；
- 文档 Claim 是“报告的事实”，验证后才是“被证据支持的事实”；
- 引用 `[12]` 只证明存在引用标记，不证明被引论文支持当前句子；
- OCR/视觉描述属于派生事实，必须带 parser/model version 和 confidence；
- Figure caption 属于作者内容，FigureDescription 属于派生内容，两者不可混淆。

## 3. 当前实现审计

### 3.1 代码入口

| 能力 | 当前实现 |
| --- | --- |
| 请求模型 | [documents/models.py](../../../src/evidence_rag/documents/models.py) |
| Schema | [documents/schema.py](../../../src/evidence_rag/documents/schema.py) |
| Parser | [documents/adapters/structured.py](../../../src/evidence_rag/documents/adapters/structured.py) |
| 摄取/Claim 验证 | [documents/service.py](../../../src/evidence_rag/documents/service.py) |
| Table↔Metric 候选 | [documents/table_evidence.py](../../../src/evidence_rag/documents/table_evidence.py) |
| 指标聚合验证 | [documents/aggregations.py](../../../src/evidence_rag/documents/aggregations.py) |
| Store | [documents/store.py](../../../src/evidence_rag/documents/store.py) |
| 跨源搜索 | [platform/store.py](../../../src/evidence_rag/platform/store.py) |

### 3.2 当前实体

| 实体 | 已保存信息 | 当前缺口 |
| --- | --- | --- |
| ScientificDocument | title/version/content/hash/source | family、supersedes、parser quality |
| DocumentSection | level/title/content/line locator | parent section、paragraph/block |
| DocumentPage | page/text/size | bbox block、reading order、OCR |
| DocumentTable | title/caption/shape/confidence | header tree、unit、footnote |
| DocumentTableCell | row/column/value/header | row/column semantic path、typed value |
| DocumentFigure | caption/artifact ref | bbox、image hash、description、producer context |
| DocumentCitation | marker/context/target | resolved reference、citation intent |
| Claim | type/status/confidence/reported values | span、subject/predicate/object、qualifier |
| ClaimEvidence | support/refute/qualify | temporal scope、evidence role、validation version |
| MatchCandidate/Review | score/signals/reviewer | calibrated model、negative feedback version |
| MetricAggregation | function/value/variance | metric definition compatibility、CI |

### 3.3 当前 Claim 抽取

当前按 Section 切句，仅保留：

- 显式 `Claim:` / `结论：`；
- 或包含百分比、提升/降低/优于、improve/outperform 等信号的句子。

并通过 regex 提取：

- 数字；
- 类似 Run ID 的字符串；
- `performance`/`other` 粗粒度类型；
- 0.98 或 0.72 固定抽取置信度。

当前局限：

- 数字会混入年份、表号、样本量；
- 一个复合句可能包含多个 Claim；
- subject、metric、baseline、candidate、dataset、split、unit、direction 未结构化；
- limitation/method/comparison 等类型覆盖弱；
- 没有否定、条件、置信区间、显著性；
- 没有 span offset；
- 自动抽取后直接进入 `reported`，没有 candidate/accepted 两阶段。

### 3.4 当前表格证据

系统已有很有价值的骨架：

- Markdown/DOCX Table 与 Cell；
- Cell↔Metric match candidate；
- 人工 confirmed/rejected；
- confirmed 后建立 `reports` 关系；
- Claim 可以链接 TableCell；
- 多个 Metric 可以 mean/median/sum/min/max 后对照 reported cell；
- 记录 variance、sample count 和 evidence event。

但当前匹配依赖：

- 字符串 metric name；
- 简单 numeric parse；
- 数值等价；
- Claim 文本是否出现 metric/cell。

缺少：

- 表头层级和行语义；
- 百分比、区间、±、科学计数、千分位；
- 单位换算；
- higher/lower direction；
- split/dataset/model/seed；
- mean±std、CI、best/average；
- 粗体/下划线含义；
- 脚注和排除项；
- MetricDefinition compatibility gate。

### 3.5 当前验证语义

Claim 状态：

- reported；
- verified；
- partially_supported；
- contradicted；
- superseded；
- potentially_stale；
- insufficient_evidence。

当前验证规则：

- 任一 refuting evidence → contradicted；
- 无 support → insufficient_evidence；
- 所有 checks 通过 → verified；
- 其他 → partially_supported。

Run 证据检查：

- completed；
- commit_entity_id；
- metrics；
- dataset ID/version；
- Claim 数值与 metric name/value。

Table 证据检查：

- Cell 存在；
- 有 confirmed `reports` Metric 关系；
- Claim reported value 与 Cell 值匹配。

问题：

- 一个低置信 refute 会覆盖多个高质量 supports；
- qualifier 不参与状态决策；
- evidence freshness/authority/independence 未进入判定；
- verified 不保存 validator/rule version；
- 文档版本更新不会系统性触发 stale；
- validation 不区分“数值一致”和“实验设计足以支持因果/比较结论”。

### 3.6 当前搜索

Document 没有专用 Search View。统一结构化搜索只返回：

- ScientificDocument：title + 全文，snippet 固定取全文前 1200 字；
- Claim：Claim content；
- Table：title/caption/全部 Cell `group_concat`。

使用 query token substring coverage 产生 `structured_multi_term` 分数。

没有直接召回：

- Section/Paragraph；
- Page/LayoutBlock；
- Table Row/Cell Fact；
- Figure；
- Citation；
- Formula；
- Claim Evidence；
- validation/event；
- version diff。

也没有：

- BM25；
- dense/multi-vector；
- table-specific retrieval；
- Claim reranker；
- parent/neighbor expansion；
- source-internal calibration；
- query-specific context。

### 3.7 当前活跃数据规模

2026-07-27 本地正式库：

| 实体 | 数量 |
| --- | ---: |
| ScientificDocument | 4 |
| Markdown Document | 4 |
| Section | 279 |
| Page | 0 |
| Table | 48 |
| TableCell | 1,270 |
| Figure | 0 |
| Citation | 0 |
| Claim | 32 |
| reported performance Claim | 32 |
| ClaimEvidence | 0 |
| Claim Match Candidate/Review | 0 / 0 |
| Table Metric Candidate/Review | 0 / 0 |
| MetricAggregation | 0 |

这意味着“结构摄取存在”，但“文档证据 RAG”尚未形成闭环。

### 3.8 当前优点

- 源文件格式多于普通 Markdown RAG；
- RawObject 与 derivation lineage 已存在；
- Document/Section/Page/Table/Cell/Figure/Citation/Claim 已有 schema；
- stable locator 与 content hash 基础较好；
- Claim support/refute/qualify 模型正确；
- 人工 review 和 evidence event 已有治理入口；
- TableCell↔Metric 与 aggregation 是可讲的差异化基础；
- secret quarantine、path allowlist、Project/Iteration scope 已存在。

### 3.9 当前失败模式

| 失败 | 根因 | 风险 |
| --- | --- | --- |
| 命中词在文末却展示开头 | Document snippet 固定前 1200 字 | 答案看不到证据 |
| 查询 Section 返回整篇文档 | 无 Section Retrieval Unit | 上下文浪费 |
| 大表一次拼接 | Table 全 Cell group_concat | 丢行列含义、超长 |
| 同一个数字错配 Metric | 只看名字/数值 | 错误验证 Claim |
| PDF 双栏阅读顺序错 | pypdf text only | 句子与表格破坏 |
| 扫描 PDF 空文本 | 无 OCR fallback | 完全不可检索 |
| Figure 只有空 caption | 无图像/邻近文本处理 | 图中结论丢失 |
| `[12]` 找到但 reference 未解析 | regex 只取 marker/context | 引用不可追溯 |
| 复合 Claim 未拆分 | 规则句级抽取 | 支持关系含糊 |
| 旧版 Claim 仍显示 verified | 无版本失效传播 | 陈旧答案 |
| refute 一票否决 | 验证规则过粗 | 误判 contradicted |
| 自动抽取当作正式 Claim | 无 candidate gate | 噪声污染 |

## 4. 查询任务目录

每类任务都必须有独立 Query Profile。

### 4.1 文档/章节定位

示例：

- 哪份设计文档定义了 `EvidencePack`？
- “失败恢复”在哪一节？
- v1.0 报告的第三部分是什么？

必需证据：

- exact DocumentVersion；
- Section/Paragraph；
- hierarchy path；
- line/page locator。

召回：

- exact title/version；
- heading sparse；
- paragraph dense；
- section parent expansion。

拒答：

- 只命中文档标题但目标段落不存在；
- 请求指定版本但只找到其他版本。

### 4.2 局部事实问答

示例：

- 文档对 ACL 的要求是什么？
- 作者列出的局限有哪些？

必需证据：

- Paragraph；
- Section path；
- 相邻 qualifier/footnote；
- author text provenance。

Context：

```text
[DOC/version/page-or-line]
Section path
Matched paragraph
Previous/next paragraph only when needed
Footnote
```

### 4.3 Claim 定位与验证

示例：

- “Recall 提升 8%”是谁报告的？
- 这个结论有实验支持吗？
- 有无反例或限定条件？

必需证据角色：

- atomic Claim；
- exact source span；
- support/refute/qualify links；
- linked Run/Metric/TableCell；
- validation status/version/time；
- missing role。

禁止：

- 仅凭同义句相似度把 Run 视为 support；
- 仅数值相同就验证；
- 将 `reported` 显示成 `verified`。

### 4.4 表格数值查询

示例：

- Table 3 中 candidate 在 test split 的 F1 是多少？
- baseline 与 candidate 差多少？
- 这个均值是否能由三个 seed 重算？

必需：

- Table；
- row semantic path；
- column semantic path；
- typed value；
- unit/split/dataset/model；
- footnote；
- source locator；
- confirmed Metric/Aggregation when validation is requested。

检索必须先识别 schema，再定位 row/cell，不允许把整表纯文本交给生成模型猜行列。

### 4.5 Figure/Formula

示例：

- Figure 2 展示了什么趋势？
- 公式中的 λ 如何定义？

必需：

- Figure/Formula exact entity；
- caption/equation；
- surrounding paragraph；
- page/bbox；
- derived description status；
- referenced entities。

若只有模型生成的图像描述而无作者 caption，应标 `derived_description`，不能伪装成原文。

### 4.6 Citation 查询

示例：

- 该方法引用了哪些工作？
- `[12]` 在这里被用来支持什么？
- DOI 对应哪条 reference？

必需：

- in-text CitationMention；
- local sentence/paragraph；
- resolved ReferenceEntry；
- DOI/title/author/year；
- resolution confidence。

“被引用”不等于“支持当前 Claim”，需要 citation intent 或人工确认。

### 4.7 文档全局总结

示例：

- 总结这篇长报告的主要设计、证据和限制；
- 多篇报告对 graph retrieval 的共识和冲突是什么？

流程：

1. 先召回 SectionSummary/Claim；
2. 按文档结构覆盖；
3. 回到原始 Paragraph/Table/Figure；
4. 对关键结论执行 citation coverage；
5. 分离 author claim、verified evidence 与模型综合。

不能对全部 chunks 做 top-k 后直接总结，否则长文前部/高频词会支配答案。

### 4.8 版本比较与陈旧性

示例：

- v1 与 v2 的性能结论有什么变化？
- 当前文档的结论是否已被新 Run 推翻？

必需：

- DocumentFamily；
- old/new version；
- aligned Section/Claim/Table；
- added/removed/modified/superseded；
- supporting evidence version；
- validation time；
- current experiment/code state。

Document 内部先完成版本对齐；是否被当前实验/代码推翻属于后续多源验证。

## 5. 目标领域模型

### 5.1 稳定实体与版本实体

```text
DocumentFamily (logical identity)
├── HAS_VERSION → DocumentVersion
│   ├── HAS_PAGE → Page
│   ├── HAS_BLOCK → LayoutBlock
│   ├── HAS_SECTION → SectionVersion
│   │   └── HAS_PARAGRAPH → Paragraph
│   ├── REPORTS → ClaimVersion
│   ├── HAS_TABLE → TableVersion
│   │   ├── HAS_ROW → TableRow
│   │   └── HAS_CELL_FACT → TableCellFact
│   ├── HAS_FIGURE → FigureVersion
│   ├── HAS_FORMULA → Formula
│   └── CITES → CitationMention → RESOLVES_TO → ReferenceWork
└── LATEST_VERSION → DocumentVersion
```

稳定 ID：

- DocumentFamily：project + canonical source/title；
- DocumentVersion：family + content hash/version；
- Section：explicit anchor，fallback 为 normalized heading path + content fingerprint；
- Paragraph：section stable ID + paragraph fingerprint；
- Table/Figure/Formula：explicit label，fallback 为 page/ordinal + fingerprint；
- Claim：document family + normalized atomic proposition fingerprint；
- Cell Fact：table version + row semantic key + column semantic key。

版本链接：

- `SUPERSEDES`；
- `DERIVED_FROM`；
- `SAME_LOGICAL_SECTION`；
- `SAME_LOGICAL_CLAIM`；
- `ADDED`/`REMOVED`/`MODIFIED`/`MOVED`。

### 5.2 原始事实与派生事实

| 类别 | 示例 | 权威 |
| --- | --- | --- |
| source-authored | paragraph/caption/table value | 原文 |
| parser-derived | layout block/header tree | parser + version |
| model-derived | summary/claim candidate/figure description | model + prompt version |
| reviewed-derived | accepted claim/relation | reviewer decision |
| cross-source-confirmed | Claim↔Metric support | confirmed relation |

每个派生事实保存：

- derivation type；
- parser/model/version；
- input entity IDs/content hashes；
- confidence；
- created_at；
- review status；
- generation_id。

### 5.3 Claim 模型

Claim Candidate 与 Accepted Claim 分离：

```text
ClaimCandidate
  text/span
  subject
  predicate
  object/value
  metric
  direction
  baseline/candidate
  dataset/split
  unit
  uncertainty
  condition/qualifier
  extraction confidence

AcceptedClaim
  authored/reviewer accepted
  status
  temporal validity
  support/refute/qualify evidence
```

一个复合句可拆为多个 Claim，但每个 Claim 都保留原句 span 与 shared qualifier。

### 5.4 表格事实模型

TableCellFact 不只是 `(row_index, column_index, value)`：

```text
row_path: [Model=Candidate, Variant=Large]
column_path: [Dataset=ABC, Split=Test, Metric=F1]
raw_value: "82.1 ± 0.4"
numeric:
  center: 82.1
  spread: 0.4
  spread_type: std
unit: percent
sample_count: unknown
formatting: bold
footnotes: ["* p<0.05"]
```

建立：

- TableSchemaView；
- TableRowView；
- TableCellFactView；
- NumericRangeIndex；
- metric/dataset/model canonical alias；
- header-to-cell adjacency。

### 5.5 Figure 与 Formula

FigureView 包含：

- author caption；
- label；
- page/bbox；
- neighbor paragraph；
- OCR text；
- artifact hash；
- optional visual description；
- referenced Claims；
- explicit/derived 标识。

FormulaView 包含：

- LaTeX/MathML/raw text；
- formula label；
- variable definitions；
- neighbor paragraph；
- cited/referenced by；
- parser confidence。

## 6. Retrieval Unit 与索引

### 6.1 Retrieval Unit

| Unit | 用途 | 推荐大小 |
| --- | --- | --- |
| DocumentSummaryView | 文档路由 | 150–300 tokens |
| SectionSurfaceView | 长文导航 | 180–450 |
| ParagraphView | 局部事实 | 80–300 |
| ClaimView | 原子声明 | 30–180 |
| TableSchemaView | 表格路由 | 80–220 |
| TableRowView | 行比较 | 50–300 |
| TableCellFactView | 精确数值 | 30–140 |
| FigureView | 图/趋势 | 80–300 |
| FormulaView | 数学定义 | 40–220 |
| CitationContextView | 引用意图 | 40–180 |
| VersionDiffView | 版本变化 | 60–300 |

Retrieval Unit 小而精确；Comprehension Context 再扩父级和相邻结构。

### 6.2 多表示

每个 Unit 至少保存：

- exact keys；
- sparse text；
- dense semantic text；
- source-authored text；
- derived summary（可选）；
- entity type；
- document/version/section/page；
- language；
- fact/derivation/review status；
- ACL；
- builder/model/generation version。

不要将 derived summary 覆盖原文 embedding；两者作为 multi-vector 独立通道。

### 6.3 索引通道

1. **Exact**
   - title/version；
   - section heading；
   - Table/Figure/Formula label；
   - DOI/citation marker；
   - metric/model/dataset alias；
   - exact number/range。

2. **Sparse**
   - Paragraph/Claim/caption/citation；
   - code/config identifiers；
   - rare terminology。

3. **Dense**
   - narrative paraphrase；
   - Claim semantic match；
   - SectionSummary；
   - cross-lingual query。

4. **Table**
   - header-aware schema match；
   - row/column filter；
   - typed numeric/range；
   - unit-aware comparison。

5. **Graph**
   - parent/child/neighbor；
   - Claim↔evidence；
   - Figure/Table↔Paragraph；
   - Citation↔Reference；
   - version chain。

6. **Structured**
   - author/tag/type/status/version/date；
   - validation state；
   - extraction confidence；
   - review status。

## 7. 单源检索算法

### 7.1 Query Profile

```text
DocumentQueryProfile
  task
  document/title/version constraints
  requested entity types
  labels/numbers/DOIs
  metric/dataset/model/split/unit
  claim verification requirement
  temporal requirement
  summary coverage requirement
  language
```

### 7.2 Candidate Budget

默认局部查询：

| 通道 | Top-K |
| --- | ---: |
| exact/label/number | 20 |
| sparse | 40 |
| dense source text | 40 |
| dense derived summary | 20 |
| table structured | 30 |
| graph seed | 20 |

先在每个 Unit 类型内部融合与校准，再进入共同重排。不能直接比较 raw BM25、cosine 与
numeric match。

### 7.3 Source-internal Fusion

基础使用 RRF，附加不可替代的结构信号：

```text
document_score =
  calibrated_retrieval
  + exact_label_bonus
  + hierarchy_match
  + requested_type_match
  + source_text_bonus
  + version_alignment
  + reviewed_fact_bonus
  - derivation_uncertainty
  - wrong_version
  - duplicate_penalty
```

Exact number 只能产生候选，不能单独产生高 authority。

### 7.4 Document Reranker

特征：

- query↔unit relevance；
- entity/task compatibility；
- label/heading/metric exactness；
- hierarchy path；
- source vs derived；
- version；
- page/line proximity；
- Claim subject/metric/dataset/split/unit compatibility；
- Table header path compatibility；
- review/validation；
- evidence completeness；
- duplicate/near-duplicate；
- OCR/parser quality。

表格数值查询使用 table reranker；Claim 验证使用 claim-evidence reranker；不与普通段落共用
一个 feature set。

### 7.5 Expansion

按任务定向扩展：

- Paragraph → Section path + 邻近 1 段；
- Claim → source span + qualifier + evidence；
- TableCell → header path + row label + caption + footnote；
- Figure → caption + neighbor paragraph + artifact；
- Citation → mention context + reference entry；
- VersionDiff → old/new aligned entity。

限制：

- 默认 1 hop；
- Claim 验证可 2 hop；
- 每条边必须给 derivation/review；
- relation-only node 不自动成为主要证据；
- 总扩展 token 设硬上限。

### 7.6 Duplicate 与版本

- content hash 相同：同内容副本，只保留一个主要结果；
- 同 family 多版本：默认 latest，指定 version 时严格过滤；
- 版本比较：成对保留；
- 引用/复制段落：标 near-duplicate，但保留 provenance；
- Claim 语义相同但条件不同：不能去重；
- superseded Claim 默认不作当前答案，但可用于历史查询。

### 7.7 Adaptive-k 与降级

- exact label/DOI 命中且结构完整：减小 k；
- 全局总结：按 Section coverage 增大 k；
- 表格查询 schema 不确定：先返回候选表再二阶段检索；
- dense 不可用：exact+sparse+structured；
- layout parser 失败：fallback text，但 `parse_quality=degraded`；
- OCR 失败：保留 raw/page artifact 并报告 missing；
- reranker 超时：RRF + structure heuristic；
- 所有降级写 trace。

## 8. Context 设计

### 8.1 两阶段 Context

Retrieval Context 用于重排，短且规范化；Comprehension Context 用于回答，保留作者原文和
结构。

### 8.2 模板

局部事实：

```text
[DOC DOC-... · v2 · p.8 / §3.2]
Source type: author_text
Section: Method > Retrieval
Matched paragraph: ...
Qualifier/footnote: ...
```

Claim 验证：

```text
[CLAIM CLM-... · reported]
Atomic claim: ...
Source span: ...
Conditions: dataset=..., split=..., metric=..., unit=...

[SUPPORT / REFUTE / QUALIFY]
Evidence: ...
Relation: confirmed|candidate
Validation checks: ...
Version alignment: ...
Missing: ...
```

Table：

```text
[TABLE 3 · row Candidate/Large · col ABC/Test/F1]
Raw value: 82.1 ± 0.4
Typed value: center=82.1, std=0.4, unit=percent
Caption: ...
Footnote: ...
Locator: p.9 bbox=...
Metric relation: confirmed|candidate|none
```

### 8.3 Token 分配

局部事实默认：

- matched source text 45%；
- hierarchy/neighbor 20%；
- table/figure/footnote 15%；
- relation/evidence 15%；
- metadata 5%。

全局总结改为：

- SectionSummary 35%；
- representative source paragraphs 30%；
- Claims/Tables/Figures 25%；
- conflicts/limitations 10%。

### 8.4 引用

Citation locator 优先级：

1. page + bbox；
2. page + paragraph/block；
3. section anchor + line；
4. source URI + char span；
5. document/version only（只允许摘要级信息）。

答案中的数字必须引用到 Cell Fact 或明确 source span，不能只引用整篇文档。

## 9. 正确性与治理

### 9.1 Claim 状态机

```text
candidate
→ accepted/rejected
accepted → reported
reported → partially_supported / verified / contradicted / insufficient_evidence
verified → potentially_stale
potentially_stale → revalidated / superseded / contradicted
```

状态由证据集合与 validation policy 产生，不由生成模型自由填写。

### 9.2 验证分层

| 层级 | 证明内容 |
| --- | --- |
| L0 source | 文档确实报告了该 Claim |
| L1 identity | Run/Metric/Table/Code 身份正确 |
| L2 numeric | value/unit/split/aggregation 一致 |
| L3 design | baseline/dataset/config/seed 可比 |
| L4 current | 当前 code/data/version 仍支持 |

只有满足任务所需层级才显示 `verified_for_<purpose>`。例如 numeric 一致不等于因果 Claim
被完全验证。

### 9.3 冲突

- support/refute/qualify 分开；
- 保留每条证据的 authority、freshness、independence；
- 多个文档引用同一 Run 不算多个独立实验；
- 不自动多数投票；
- 输出 conflict set；
- 低置信 refute 不再一票否决；
- policy/version 可审计。

### 9.4 安全

- ACL 从 DocumentVersion 继承到全部 Unit；
- reference metadata enrichment 不能放宽 ACL；
- PDF/HTML parser 在隔离进程执行；
- HTML 不执行脚本；
- OCR/视觉服务禁止上传未授权内容；
- secret/PII 检测覆盖原文和派生文本；
- binary artifact 单独权限；
- 删除 DocumentVersion 产生 tombstone 并撤销 search generation。

### 9.5 索引发布

```text
raw accepted
→ parse candidate
→ quality checks
→ entity generation
→ search-view generation
→ retrieval smoke test
→ atomic publish
```

任何 parser/model 变化都新建 generation；保留 last-known-good 指针，可回滚，不原地覆盖。

## 10. 评测

### 10.1 Golden Set：50 条

| 类别 | 数量 |
| --- | ---: |
| 文档/章节定位 | 8 |
| 局部事实 | 8 |
| Claim/验证 | 10 |
| Table/数值 | 10 |
| Figure/Formula | 5 |
| Citation | 4 |
| 全局总结 | 3 |
| 版本/陈旧性 | 2 |

每条标注：

- expected DocumentVersion；
- expected Unit/entity；
- exact locator；
- required evidence roles；
- allowed graph paths；
- hard negatives；
- expected refusal/missing；
- version/ACL。

### 10.2 必备 Fixture

- 双栏 PDF；
- scanned PDF；
- Markdown nested headings；
- DOCX merged table；
- HTML noise/navigation；
- 同名 Section；
- 82.1 vs 0.821；
- `82.1 ± 0.4`；
- 相同值但不同 split；
- Figure 无 caption；
- citation marker 重复；
- v1/v2 Claim 修改；
- copied paragraph；
- composite Claim；
- low-confidence OCR number。

### 10.3 指标

Retrieval：

- Entity Recall@5/10；
- Locator Accuracy；
- nDCG@10；
- MRR；
- Unit Type Accuracy；
- Version Accuracy；
- Section Coverage。

Document-specific：

- Claim Span F1；
- Claim Atomicity/Qualifier Accuracy；
- Table Header Path Accuracy；
- Numeric+Unit Accuracy；
- Figure/Formula Grounding；
- Citation Resolution Accuracy；
- Version Alignment Accuracy；
- Support/Refute/Qualify Relation Precision；
- Claim Validation Precision；
- unsupported verification rate。

Context/answer：

- Context Precision/Recall；
- Citation Completeness/Correctness；
- Numeric Faithfulness；
- Conflict Recall；
- Missing Evidence Accuracy；
- Abstention Precision/Recall。

系统：

- ingest P50/P95 by format/page；
- OCR fallback rate；
- parse quality distribution；
- retrieval/rerank P95；
- index bytes per page；
- embedding/model cost；
- generation rollback success。

### 10.4 消融

| Run | 改动 |
| --- | --- |
| D-B0 | 当前 Document/Claim/Table substring |
| D-B1 | typed Retrieval Unit |
| D-B2 | sparse+dense |
| D-B3 | hierarchy/parent expansion |
| D-B4 | table structured retrieval |
| D-B5 | Claim schema + evidence reranker |
| D-B6 | layout/OCR |
| D-B7 | document-specific reranker |
| D-B8 | version/validation policy |

### 10.5 Release Gate

- Locator Accuracy ≥ 0.95；
- Table Header Path Accuracy ≥ 0.92；
- Numeric+Unit Accuracy ≥ 0.98；
- Claim Validation Precision ≥ 0.95；
- unsupported verification rate ≤ 0.01；
- Version Accuracy ≥ 0.98；
- Citation Correctness ≥ 0.95；
- p95 满足预算；
- 0 个 ACL 泄漏；
- parser degradation 能正确暴露。

门槛可在 baseline 后调整，但禁止没有 baseline 就声称达成。

## 11. 实施方案

### 11.1 Schema Migration

新增：

- `document_families`；
- `document_versions` 或给现表增加 family/version lineage；
- `document_blocks` / `document_paragraphs`；
- `document_formulas`；
- `reference_works` / `citation_mentions`；
- `claim_candidates` / `claim_versions`；
- `table_rows` / `table_cell_facts`；
- `document_search_views`；
- `document_version_links`；
- `document_validation_runs`。

现有表保持兼容，通过 backfill 建 family、paragraph 和 views。

### 11.2 新模块

```text
src/evidence_rag/documents/
  parsing/
    layout_pdf.py
    ocr_fallback.py
    block_normalizer.py
    table_normalizer.py
    citation_resolver.py
  claims/
    extractor.py
    normalizer.py
    validator.py
  retrieval/
    builder.py
    exact.py
    sparse.py
    dense.py
    table.py
    reranker.py
    context.py
  versions/
    aligner.py
    staleness.py
  evaluation/
    dataset.py
    metrics.py
```

### 11.3 Feature Flags

- `document_views_v2`；
- `document_layout_parser_v2`；
- `document_ocr_fallback`；
- `document_claim_candidates_v2`；
- `document_table_facts_v2`；
- `document_reranker_v2`；
- `document_version_alignment_v2`。

每个 flag 支持 shadow mode、per-project enable 和 fallback。

### 11.4 分阶段交付

#### Sprint D0：样本与基线

- 固化当前 4 篇文档 snapshot；
- 补 PDF/DOCX/HTML/scanned fixtures；
- 建 50 条 Golden；
- 跑 D-B0；
- 记录零数据项，不伪造验证结果。

#### Sprint D1：层级 Retrieval Unit

- Family/Version/Paragraph；
- exact+sparse；
- hit-centered snippet；
- Section parent expansion；
- locator tests。

#### Sprint D2：Dense 与 Reranker

- source/summary multi-vector；
- duplicate/version；
- document reranker；
- trace/calibration；
- D-B1~D-B3。

#### Sprint D3：Table Fact

- header tree；
- row/cell typed values；
- unit/range/±；
- table retriever/reranker；
- MetricDefinition compatibility；
- D-B4。

#### Sprint D4：Claim

- candidate/accepted；
- atomic schema；
- qualifier/negation；
- evidence policy；
- validation levels；
- D-B5/D-B8。

#### Sprint D5：PDF/Figure/Citation

- layout blocks；
- OCR fallback；
- figure/formula；
- citation resolution；
- parse quality；
- D-B6。

#### Sprint D6：版本与发布

- entity alignment；
- stale propagation；
- atomic index generation；
- rollback；
- performance/security test；
- release gate。

### 11.5 测试

Unit：

- parser/block/header/value/claim/citation；
- stable ID/version alignment；
- validation state machine；
- unit conversion；
- locator serialization。

Integration：

- format→RawObject→entities→views→retrieval；
- Claim↔TableCell↔Metric review；
- v1→v2 stale propagation；
- ACL/deletion/rebuild；
- fallback/rollback。

Regression：

- current API；
- existing four Markdown docs；
- evidence events；
- table aggregation；
- unified search compatibility。

## 12. 单源完成定义

Document Source 只有满足以下条件才能进入多源联调：

- 50 条 Golden 与 hard negatives 已提交；
- Section/Paragraph/Claim/Table/Figure/Citation 至少各有可测 fixture；
- exact/sparse/dense/table/graph 通道可独立消融；
- Claim 不再由自动抽取直接晋升为已接受事实；
- 表格数值能保留 header path、unit、footnote；
- hit-centered source context 和精确 locator；
- DocumentFamily/Version 对齐与 stale 传播；
- source/derived/reviewed 状态显式；
- 关键验证 precision 达门槛；
- ACL、删除、generation rollback 通过；
- 面试文档中的指标均来自可复查 Run。

## 13. 最能讲的技术点

1. **Scientific Document 不是统一 Chunk**：对 Paragraph、Claim、Table Fact、
   Figure、Formula、Citation 使用 typed retrieval。
2. **小单元检索、父结构理解**：命中精确事实后按 hierarchy 定向扩展。
3. **Table-to-Metric 可验证链**：表头语义、typed value 与正式 MetricDefinition 对齐，
   支持多 seed 聚合复算。
4. **Claim Validation 分层**：区分原文报告、身份匹配、数值一致、实验设计可比和当前有效。
5. **Source Fact 与 Derived Fact 分离**：OCR、summary、figure description、关系推断均带
   lineage、confidence 与 review。
6. **文档版本陈旧性**：Claim/Section/Table 跨版本对齐，证据更新触发 stale/revalidation。
