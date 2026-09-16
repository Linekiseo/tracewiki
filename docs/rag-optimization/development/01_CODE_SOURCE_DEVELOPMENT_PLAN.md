# Code Source RAG 开发优化方案

状态：REPOSITORY_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD；C0、C1 已验收
PASS；C2 engineering PASS / C-B1 NOT QUALIFIED；C3 engineering COMPLETE /
C-B2、C-B5 NOT QUALIFIED；C4-01/C4-02
engineering COMPLETE / C-B3 NOT QUALIFIED；C4-03 engineering COMPLETE；C4 overall
engineering COMPLETE；C5-01/C5-02 engineering PASS；C5 overall engineering COMPLETE /
C-B4 VERIFIED — NOT QUALIFIED；C6-01 engineering PASS；C6-02 engineering PASS /
COMPLETE；C-B6 VERIFIED — PROVISIONAL NOT QUALIFIED；C6-03 engineering PASS /
COMPLETE；C6 overall engineering COMPLETE；C7-01 engineering PASS / COMPLETE；
C7-02 engineering PASS / COMPLETE；C7-03 engineering PASS / COMPLETE；C7 overall
engineering COMPLETE；Release decision: HOLD_DEFAULT_V1 / NOT RELEASED / default V1  
开发方案版本：code-source-development-v1  
对应目标设计：[01_CODE_SOURCE_RAG.md](../sources/01_CODE_SOURCE_RAG.md)  
对应面试材料：[01_CODE_SOURCE_INTERVIEW.md](../interview/01_CODE_SOURCE_INTERVIEW.md)  
统一开发契约：[00_SINGLE_SOURCE_DEVELOPMENT_CONTRACT.md](00_SINGLE_SOURCE_DEVELOPMENT_CONTRACT.md)  
最终 C0 Gate：[01_CODE_C0_GATE_REVIEW.md](reviews/01_CODE_C0_GATE_REVIEW.md)  
最终 C1 Gate：[02_CODE_C1_GATE_REVIEW.md](reviews/02_CODE_C1_GATE_REVIEW.md)  
最终 C3 Gate：[04_CODE_C3_GATE_REVIEW.md](reviews/04_CODE_C3_GATE_REVIEW.md)  
最终 C4 overall engineering 与 C-B3 Gate：
[05_CODE_C4_GATE_REVIEW.md](reviews/05_CODE_C4_GATE_REVIEW.md)  
最终 C5 overall engineering 与 C-B4 Gate：
[06_CODE_C5_GATE_REVIEW.md](reviews/06_CODE_C5_GATE_REVIEW.md)  
最终 C6-01/C6-02/C6-03、C6 overall engineering 与 C-B6 Gate：
[07_CODE_C6_GATE_REVIEW.md](reviews/07_CODE_C6_GATE_REVIEW.md)  
最终 C7-01/C7-02/C7-03 与 C7 overall engineering Gate：
[08_CODE_C7_GATE_REVIEW.md](reviews/08_CODE_C7_GATE_REVIEW.md)  
基线日期：2026-07-27；C7 最终证据同步：2026-07-29

## 1. 本轮结果与边界

本方案把 Code Source 从当前：

```text
File/Symbol raw view
→ FTS5 + local-hash full scan
→ 固定权重
→ Top-K 后附带一跳边
```

演进为：

```text
Stable Code Entity
→ AST Retrieval Unit
→ Exact + Fielded Sparse + Dense Profile
→ Typed Graph / History / Test Candidate
→ Code Reranker + Calibration
→ Task-specific Code Context
```

本轮只负责 Code Source 内部能力，不实现：

- Codex Episode 检索；
- Experiment/Notebook/Document/Workspace 检索；
- 跨源 Query Planner；
- 跨源 calibration/fusion；
- Claim→Run→Code 联合裁决；
- 最终 LLM answer prompt；
- 为规模尚未达到阈值而强行迁移图数据库或 ANN。

完成后向多源层交付 `CodeSourceRetrieverV2`、统一候选、SourceResult、Code Context 和
可复查 Evaluation Run。

截至 2026-07-28，C0 的 Evaluation V2、Code Golden v2、V1 graph-off baseline，以及
C1 的 Code Source contract、V1 adapter 与 bounded shadow skeleton 均已通过最终 Gate。
C2 的 Parser IR、Schema/Store、AST Unit Builder、opt-in dual-write、Exact + Fielded
Sparse、symbol entity projection 与 entity-aware diversification 已实现并通过工程
Gate；默认用户路径仍为 V1，`ast-v2` 只经显式开关启用。三次真实 C-B1 均完成生产路径、
33 eligible 分母、隔离 artifact 与 verify-only 审计，但都为
`treatment_qualified=false`，因此 C2 只能标记 engineering PASS，不能声称相对 C-B0
总体质量提升。C3-01 Embedding Provider/Profile、C3-02 Dense/Hybrid 与 C3-03
Deterministic Reranker/Calibration 的工程 Gate 均已完成，C3 engineering
`COMPLETE`；但真实 C-B2、C-B5 production Run 都是 verified 33/33 且
`treatment_qualified=false`，没有可声明的整体质量提升。C-B0 `5a92...` 仍是唯一
qualified baseline，C-B1/C-B2/C-B5 只作 audit。C4-01 Edge Ontology/Diagnostics 与
C4-02 Typed Graph Retriever 已完成 engineering contract；真实四臂 C-B3 已
verified 33/33，但 `treatment_qualified=false`，没有 Graph Recall 收益。C4-03
engineering COMPLETE，C4 overall engineering `COMPLETE`；这不改变 C-B3
`NOT QUALIFIED`。C5-01 SCIP Consumer/Safe Execution 与 C5-02 Python Semantic Edge
Treatment 均已通过 engineering Gate，C5 overall engineering `COMPLETE`；唯一 C-B4
production audit
`evaluation-run://project-code-golden-v2/a22b322a141741ae9a710aa347fbf032`
已 verified，但因真实 label records 为 0，precision、coverage、unresolved reduction、
graph noise 与 harmful quality 均为 `UNAVAILABLE`，最终 `NOT QUALIFIED`。C6-01
engineering `PASS`，已验收 exact ref→SHA/Git object materialization、真实
Parser→Unit、current/dirty/history namespace、memory/临时 SQLite 原子
publication/cache/rollback、TTL/LRU pins 与 confirmed lineage；C6-02 engineering
`PASS / COMPLETE`，Diff→Symbol 主路和 exact production method identity Gate 已通过。
C-B6
`evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e`
artifact 已 verified，但因 8 个 labeled cases 小于 30、precision `0.888889` 小于
`0.9`、false affected `0.111111` 大于 `0.1`，结论为
`PROVISIONAL NOT QUALIFIED`。C6-03 engineering `PASS / COMPLETE`，C6 overall
engineering `COMPLETE`。C7-01/C7-02 engineering `PASS / COMPLETE`；C7-03
engineering `PASS / COMPLETE`，C7 overall engineering `COMPLETE`。真实 release
decision 为 `HOLD_DEFAULT_V1`：默认用户路径仍为 V1，`NOT RELEASED`，且没有新
quality Run。

## 2. 当前实现与数据基线

### 2.1 当前执行链

```text
POST /v1/ingestion/repositories
→ IngestionService.run
→ RepositoryResolver.resolve/discover_files/read_text
→ RawSourceService
→ CodeParser.parse
→ IngestionService._build_records
→ SQLiteStore.publish_generation
→ entities / edges / search_views / FTS / vector

EvidenceSearchRequest
→ HybridRetriever.search
→ SQLiteStore.lexical_search
→ SQLiteStore.dense_candidates
→ weighted-hybrid-v2
→ optional edges_for_entities

GlobalSearchRequest
→ PlatformService.search
→ HybridRetriever
→ authority/version factor
→ cross-source-diversified-v2
→ one-hop relation expansion
```

### 2.2 当前代码映射

| 能力 | 当前入口 | 开发判断 |
| --- | --- | --- |
| Runtime | [runtime.py](../../../src/evidence_rag/runtime.py) | direct API 与 Platform 共用一个 `CodePlatformIntegration`；默认仍为 V1 |
| API | [api.py](../../../src/evidence_rag/api.py) | 保持 V1 主字段；`X-RAG-Code-Engine` 支持显式 v1/v2，非法值返回 422 |
| 请求模型 | [models.py](../../../src/evidence_rag/models.py) | `SearchScope` 可过滤 repo/commit/language/type |
| Repository | [repository.py](../../../src/evidence_rag/repository.py) | local/remote/dirty snapshot、ignore 已有 |
| Parser | [parser.py](../../../src/evidence_rag/parser.py) / [models.py](../../../src/evidence_rag/models.py) | C2 Parser IR 已实现；同次 Tree-sitter parse 保留旧 Symbol 并产生可序列化 Unit IR |
| Ingest | [ingestion.py](../../../src/evidence_rag/ingestion.py) / [dual_write.py](../../../src/evidence_rag/rag/sources/code/dual_write.py) | 默认 `raw-v1`；`ast-v2` 显式启用时执行 C2 dual-write、publication 与失败 cleanup |
| 主 Schema | [db_schema.py](../../../src/evidence_rag/db_schema.py) | entity/edge/search view/generation 已有 |
| 主 Store | [storage.py](../../../src/evidence_rag/storage.py) | 已过于集中，新能力不继续堆入 |
| Code V2 Schema/Store | [schema.py](../../../src/evidence_rag/rag/sources/code/schema.py) / [store.py](../../../src/evidence_rag/rag/sources/code/store.py) | additive schema、Unit/FTS/vector/cache/publication 与严格 project/repository/generation/ACL scope 已通过 C2 Gate |
| AST Unit Builder | [unit_builder.py](../../../src/evidence_rag/rag/sources/code/unit_builder.py) | frozen Unit、稳定 identity、source lineage、真实 locator、partial/fallback 已通过 C2 Gate |
| C2 Retriever | [retrieval_v2.py](../../../src/evidence_rag/rag/sources/code/retrieval_v2.py) | Exact + Fielded Sparse、weighted RRF、symbol projection 后的 entity-aware diversification 已实现；尚未取得 qualified treatment |
| V1 Retriever | [retrieval.py](../../../src/evidence_rag/retrieval.py) | FTS + full-scan hash vector + fixed weights |
| Code contract | [contracts.py](../../../src/evidence_rag/rag/sources/code/contracts.py) | C1-01 已通过；统一 candidate/path/context/channel/status/版本与 ACL provenance |
| V1 Adapter | [v1_adapter.py](../../../src/evidence_rag/rag/sources/code/v1_adapter.py) | C1-02 已通过；验证真实 legacy execution evidence 后做纯转换 |
| Shadow | [shadow.py](../../../src/evidence_rag/rag/sources/code/shadow.py) | C1-02 已通过；bounded async AES-GCM snapshot 与固定脱敏 telemetry |
| Embedding Profile/Cache | [embedding_v2.py](../../../src/evidence_rag/rag/sources/code/embedding_v2.py) | C3-01 PASS；frozen/versioned Profile、batch Provider、content-addressed cache、provenance/dimension/secret fail-closed；默认仅离线 `local-hash-v2` |
| Dense Publisher/Hybrid | [dense_v2.py](../../../src/evidence_rag/rag/sources/code/dense_v2.py) | C3-02 engineering PASS；profile building/ready/unavailable 发布边界、scope/ACL/provenance 校验、Exact/Sparse/Dense deterministic weighted RRF 与 channel trace |
| Reranker/Calibration | [rerank_v2.py](../../../src/evidence_rag/rag/sources/code/rerank_v2.py) | C3-03 engineering PASS；scope/version hard gate、deterministic EvidenceCard 排序、deadline/exception 保序 fallback、版本绑定 provisional calibration 与解释 trace |
| Typed Edge Registry/Diagnostics | [graph_v2.py](../../../src/evidence_rag/rag/sources/code/graph_v2.py) | C4-01 engineering COMPLETE；typed/exhaustive immutable registry、derivation/version/generation/review 约束，以及有界 unresolved diagnostics |
| Typed Graph Retriever | [graph_retrieval_v2.py](../../../src/evidence_rag/rag/sources/code/graph_retrieval_v2.py) | C4-02 engineering COMPLETE；SQLite read-only adjacency、publication/version/双端 ACL 校验、deterministic beam/cycle/budget/deadline 与完整 path trace |
| C-B3 Harness | [cb3.py](../../../src/evidence_rag/evaluation/cb3.py) | 唯一 production artifact 已 verified；authorization snapshot 可移植且 fail closed；quality **NOT QUALIFIED** |
| Query Profile/Fusion | [query_profile_v2.py](../../../src/evidence_rag/rag/sources/code/query_profile_v2.py) | C4-03 engineering COMPLETE；8 个 frozen/versioned profiles、`CodeSourceFusionPipeline`、exact fast path、graph + optional history/test hooks、rerank/calibration/adaptive-k 与 strict trace；缺 provider/artifact 诚实 unavailable/degrade/refuse |
| SCIP Consumer/Safe Runner | [scip_v1.py](../../../src/evidence_rag/rag/sources/code/scip_v1.py) | C5-01 engineering PASS；runner 默认 off，bounded protobuf parser 与 resolver 均 fail closed；Gate 未下载、未联网、未实际执行 indexer |
| Python Semantic Edge | [semantic_edges_v1.py](../../../src/evidence_rag/rag/sources/code/semantic_edges_v1.py) | C5-02 engineering PASS；Python `SUPPORTED`，JS/TS `UNAVAILABLE`；严格 occurrence evidence、relation、merge/conflict/provenance 与零标签质量语义 |
| C-B4 Harness | [cb4.py](../../../src/evidence_rag/evaluation/cb4.py) | 唯一 production artifact 已 verified；三臂 structural/performance evidence 可审计，但质量 **NOT QUALIFIED** |
| Historical Materialization/Lineage | [history_v2.py](../../../src/evidence_rag/rag/sources/code/history_v2.py) | C6-01 engineering PASS；safe exact Git ref/SHA/object read、Parser→Unit、namespace、memory/临时 SQLite publication/cache/rollback、TTL/LRU pins 与 confirmed/review-only lineage 已验收 |
| Diff→Symbol | [diff_symbol_v2.py](../../../src/evidence_rag/rag/sources/code/diff_symbol_v2.py) | C6-02 engineering PASS / COMPLETE；old/new exact version/path mapping、typed CONTAINS/AFFECTS、ambiguous candidate no-edge 与 lineage citation 已验收 |
| Test/Validation→Symbol | [test_validation_v2.py](../../../src/evidence_rag/rag/sources/code/test_validation_v2.py) | C6-03 engineering PASS / COMPLETE；注册 CONTAINS/has_test_result、validation truth table、confirmed/candidate Test→Symbol 与 historical/missing/scope truth 已验收 |
| C-B6 Harness | [cb6.py](../../../src/evidence_rag/evaluation/cb6.py) | exact `DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2` identity Gate 已通过；artifact verified，但 quality **PROVISIONAL NOT QUALIFIED** |
| Task-specific Context Builder | [context_builder_v2.py](../../../src/evidence_rag/rag/sources/code/context_builder_v2.py) | C7-01 engineering PASS / COMPLETE；四模板、Retrieval/Comprehension 分离、production attestation、原文/citation/dedup/version/locator/metrics fail-closed 已验收 |
| Platform Opt-in Integration | [platform_v2.py](../../../src/evidence_rag/rag/sources/code/platform_v2.py) | C7-02 engineering PASS / COMPLETE；shared direct/Platform routing、governed exact-commit V2、V1 projection/fallback、shadow reuse 与 mixed-source 已验收；默认仍为 V1 |
| Release Evidence Evaluator | [release_v2.py](../../../src/evidence_rag/rag/sources/code/release_v2.py) | C7-03 engineering PASS / COMPLETE；C-B6 exact URI、trusted deployed-stage severity、stage+parent-bound child evidence 与固定 rollback plan 已验收；decision 为 **HOLD_DEFAULT_V1**，未 release |
| Legacy Embedding | [embeddings.py](../../../src/evidence_rag/embeddings.py) | `local-hash-v2`；C3 production audit 仍只使用该 deterministic offline baseline，不是 neural/learned 模型 |
| History | [code_history/service.py](../../../src/evidence_rag/code_history/service.py) | Commit/Diff/历史读文件已有 |
| History Store | [code_history/store.py](../../../src/evidence_rag/code_history/store.py) | 13 Commit、500 DiffHunk 已保存 |
| History Schema | [code_history/schema.py](../../../src/evidence_rag/code_history/schema.py) | Commit/Diff/Test 基础足够 |
| Platform | [platform/service.py](../../../src/evidence_rag/platform/service.py) | 共用 `CodePlatformIntegration`；支持 code 与 mixed-source 投影，默认响应仍走 V1 |
| Evaluation | [evaluation/service.py](../../../src/evidence_rag/evaluation/service.py) / [evaluation/code.py](../../../src/evidence_rag/evaluation/code.py) | C0-01 direct Code Evaluation V2 已通过；Unit treatment 仍 unavailable |
| Parser tests | [test_parser.py](../../../tests/test_parser.py) | 可扩 AST fixture |
| Ingest/retrieval tests | [test_ingestion_retrieval.py](../../../tests/test_ingestion_retrieval.py) | 当前主要回归入口 |
| History tests | [test_code_history_refs.py](../../../tests/test_code_history_refs.py) | dirty/current/historical 基础已有 |
| Vector tests | [test_retrieval_quality.py](../../../tests/test_retrieval_quality.py) | 只验证 hash embedding 行为 |

### 2.3 正式库历史快照与当前边界

下表是 C6 之前已记录的历史快照，不是当前不变性声明。当前正式库分类为
`EXTERNAL_MUTABLE_SERVICE_OWNED`，由外部用户服务拥有并可变；C6 Gate 使用
memory/系统临时目录 SQLite，C-B6 使用 isolated artifact SQLite 与 immutable
artifact 验收，结论不依赖当前正式库连接或 hash/mtime 不变性。外部服务引起的正式库
漂移不归因于 C-B6；本次文档同步也未打开或修改正式库。

| 项目 | 历史记录值 |
| --- | ---: |
| Repository | 1 |
| FileVersion | 192 |
| CodeSymbol | 1,406 |
| Search View | 1,598 |
| Edge | 4,712 |
| Commit | 13 |
| DiffHunk | 500 |
| TestResult | 0 |
| Golden/Evaluation Run | 0（正式库；C0 artifact 使用隔离 SQLite） |

语言分布：

| Language | Symbol |
| --- | ---: |
| Python | 928 |
| JavaScript | 365 |
| TSX | 66 |
| TypeScript | 47 |

当前 View：

| View | 数量 | 平均字符 | 最大字符 |
| --- | ---: | ---: | ---: |
| `file.raw` | 192 | 14,000.9 | 134,175 |
| `symbol.raw` | 1,406 | 1,771.0 | 85,737 |

当前 Generation：

- parser：`tree-sitter-language-pack-1.13`；
- embedding：`local-hash-v2`；
- schema：`code-evidence-v1`；
- dirty snapshot：`bc3326...+dirty.2354b2e8bd3d`；
- parse errors：0；
- quarantined files：3；
- unresolved calls：6,273；
- unresolved references：15,845；
- unresolved imports：531。

### 2.4 当前必须保留的行为

- stable Repository/FileVersion/CodeSymbol URI；
- dirty manifest 版本；
- secret quarantine；
- active Generation 原子发布；
- current code 在 history sync 失败时仍可发布；
- overloaded Symbol distinct ID；
- common method 不错误跨文件绑定；
- exact commit/historical file API；
- ref/path 安全检查；
- ACL prefilter；
- `local-hash-v2` 兼容读取；
- V1 `/v1/search`、`/v1/evidence/search`、`/v1/query` response。

## 3. 已锁定的开发决策

### 3.1 Stable Entity 不变

`Repository`、`Commit`、`FileVersion`、`CodeSymbol`、`DiffHunk`、`TestResult` 继续作为引用
主键。`code_retrieval_units` 是可重建派生索引，不作为外部 Citation ID。

### 3.2 新 Store 独立

新增 `src/evidence_rag/rag/sources/code/` 与 `CodeRagStore`，通过已有 `SQLiteStore`
connection/transaction 访问数据库。不继续向 1,000+ 行的 [storage.py](../../../src/evidence_rag/storage.py)
堆积 Code V2 私有方法。

### 3.3 先 SQLite 正确性，再 ANN

当前只有 1,598 个 View；AST Unit 预计数千到一万量级。P0 继续 SQLite 存事实和向量，
先完成准确性与 Profile abstraction。满足以下任一条件再评审 ANN：

- active code units > 50,000；
- dense full scan P95 > 300 ms；
- 并发目标下 CPU 超预算；
- 内存/数据库读取超过 SLO。

### 3.4 Python 语义解析先行

当前 Python Symbol 占 66%。C5 已按 Python-first 完成 SCIP consumer 与 semantic edge
engineering：Python 为 `SUPPORTED`，Tree-sitter 保留为保守基线与 fallback；
JavaScript 1 case、TypeScript 2 cases 均明确 `UNAVAILABLE`，不得把候选
`scip-typescript` 写成现有支持。安全 runner 默认 off；本轮 Gate 与 C-B4 均未下载、
未联网或实际执行外部 indexer。

### 3.5 模型不在设计阶段锁死

`local-hash-v2` 为 baseline/fallback。Qwen3 Embedding/Reranker、BGE-M3、code-specific
模型只进入 benchmark；以 Code Golden、中文自然语言→代码、延迟、内存和安全结果决定。

### 3.6 Graph 是候选通道

Graph traversal 发生在 source rerank 前，能找回不含 query token 的 caller/test/type
依赖。`SEMANTIC_SIMILAR` 只做候选发现，不进入静态事实路径。

### 3.7 Evaluation-first

以下 Evaluation-first 前置已由 C0 完成并通过最终 Gate：

- 50 条 Code Golden schema；
- 至少 15 条 smoke subset；
- V1 direct Code baseline；
- 当前 P50/P95、index size、zero-result、wrong-version；
- error slice。

这完成了进入 C1 的前置；C1 现已通过。后续三次真实 C-B1、C-B2、C-B5 以及四臂
C-B3 均已有 production quality Run，但全部 NOT QUALIFIED；它们扩展了失败诊断和
工程审计，仍未产生可替代 C-B0 的 qualified treatment。C4-03 engineering Gate 没有
创建新 quality Run。C5 的唯一 C-B4 production audit 已 verified，但 0 条真实 label
records 使所有 label-dependent quality 指标 `UNAVAILABLE`，最终 NOT QUALIFIED；
42/42 只能作为三臂 non-quality structural coverage，不能冒充 edge coverage 或
precision。C6 已有独立 Diff→Symbol quality audit C-B6，但结论为
`PROVISIONAL NOT QUALIFIED`；C6-03 engineering 已完成但没有独立 production
quality Run，C7-01/C7-02/C7-03 engineering 已完成但没有新 production quality Run。

## 4. 目标包结构

```text
src/evidence_rag/rag/
  contracts.py
  flags.py
  sources/
    code/
      __init__.py
      contracts.py
      schema.py
      store.py
      v1_adapter.py
      parser_ir.py
      unit_builder.py
      exact.py
      sparse.py
      embeddings.py
      dense.py
      graph_contracts.py
      graph_builder.py
      graph_retriever.py
      scip_adapter.py
      history.py
      tests_retriever.py
      query_profile.py
      fusion.py
      reranker.py
      calibration.py
      context.py
      retriever.py
      shadow.py
      evaluation.py
      telemetry.py
```

测试：

```text
tests/
  fixtures/code_rag/
  test_code_rag_contracts.py
  test_code_rag_units.py
  test_code_rag_sparse.py
  test_code_rag_dense.py
  test_code_rag_graph.py
  test_code_rag_scip.py
  test_code_rag_history.py
  test_code_rag_validation.py
  test_code_rag_context.py
  test_code_rag_evaluation.py
  test_code_rag_shadow.py
```

## 5. 开发依赖图

```text
C0-01 Evaluation schema/runner
├── C0-02 Fixtures + 50 Golden
└── C0-03 V1 baseline
        ↓
C1-01 Code contracts/flags
└── C1-02 V1 adapter + shadow skeleton
        ↓
C2-01 Code V2 schema/store
├── C2-02 Parser IR
│   └── C2-03 AST Unit Builder
│       └── C2-04 Dual-write/index generation
└────────── C2-05 Exact + fielded sparse
                 ↓
C3-01 Embedding Provider/Profile
├── C3-02 Dense benchmark
└── C3-03 Reranker + calibration
                 ↓
C4-01 Edge ontology + diagnostics
├── C4-02 Typed Graph Retriever
└── C4-03 Query Profile + source fusion
                 ↓
C5-01 SCIP consumer
└── C5-02 Python semantic index MVP
                 ↓
C6-01 History materialization + lineage
├── C6-02 Diff→Symbol
└── C6-03 Test/Validation→Symbol
                 ↓
C7-01 Task Context Builder
→ C7-02 Platform opt-in integration
→ C7-03 Shadow/canary/release
```

规则：

- C0、C1 Gate 均已通过；C2 engineering Gate 也已通过，但三次 C-B1 都不是 qualified
  treatment，工程完成与质量结论必须分开记录；
- C2 exact/sparse 与 C3 profile/cache/dense/hybrid/reranker/calibration 工程实现均已完成；
  C-B1/C-B2/C-B5 均 NOT QUALIFIED，不能把 engineering COMPLETE 写成质量 PASS；
- C4-01 typed edge registry/diagnostics 与 C4-02 read-only Typed Graph Retriever 的
  engineering contract 已完成；C-B3 四臂各 33 case 且 NOT QUALIFIED，不能把 traversal
  扫描量或小幅排序变化写成 Graph Recall 收益；
- C4-03 独立 Gate PASS，C4 overall engineering `COMPLETE`；工程完成不改变 C-B3
  `NOT QUALIFIED`，也不等于离线质量或发布完成；
- C5-01/C5-02 engineering PASS，C5 overall engineering `COMPLETE`；C-B4 verified
  但 `NOT QUALIFIED`，必须坚持 engineering COMPLETE ≠ quality qualified；
- C5 SCIP 是 semantic edge 增强，不是 GraphRetriever 前置；
- C6-01 engineering PASS；C6-02 engineering PASS / COMPLETE；C6-03 engineering
  PASS / COMPLETE；C6 overall engineering `COMPLETE`；C-B6 verified 但
  `PROVISIONAL NOT QUALIFIED`；
- C7-01/C7-02/C7-03 engineering PASS / COMPLETE，C7 overall engineering
  `COMPLETE`；release decision 为 `HOLD_DEFAULT_V1`，默认仍为 V1 且 `NOT RELEASED`；
- 只有后续 Gate 授权的 default switch 才可改变用户可见默认路径。

## 6. 里程碑总表

| Milestone | 主要变量 | PR 数 | 退出物 |
| --- | --- | ---: | --- |
| C0 | 评测 | 3 | released Golden v2 + qualified V1 baseline |
| C1 | 双轨契约 | 2 | V1 adapter + flags |
| C2 | AST/Exact/Sparse | 5 | Unit index + lexical treatment |
| C3 | Dense/Rerank | 3 | model profile + calibration |
| C4 | Graph Retrieval | 3 | typed path candidate |
| C5 | Semantic resolver | 2 | Python SCIP MVP |
| C6 | History/Test | 3 | version/validation context |
| C7 | Context/Release | 3 | CodeSourceRetrieverV2 released |

合计建议 24 个小型 PR。PR 可以合并，但 Evaluation 中的主要变量不能合并。

## 7. C0：Golden、评测与真实基线

### Actual C0 Run Evidence

以下是 C0 已落地且已通过最终门禁的实际证据；它取代本节原始计划中的
`NOT_STARTED`/Golden v1/“无 baseline”快照。这里的数字仍是不可篡改的 C0 baseline，
不因 C1 后续通过而改写；当前里程碑状态见下一节。数字冲突时以
[最终 C0 Gate](reviews/01_CODE_C0_GATE_REVIEW.md)与唯一有效
[Run manifest](../../../evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061/manifest.json)
为准。

#### 验收状态与证据入口

| Gate | 实际状态 | 可定位的一手证据 | 验收含义 |
| --- | --- | --- | --- |
| C0-01 Evaluation V2 | **PASS** | [最终 Gate §1/§9](reviews/01_CODE_C0_GATE_REVIEW.md)；[metrics.json](../../../evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061/metrics.json)；[slices.json](../../../evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061/slices.json) | direct Code baseline 的指标、分母、slice、unavailable 语义和持久化链路已验收 |
| C0-02 Code Golden v2 | **PASS** | [Golden v2 manifest](../../../evals/code/code-golden-v2.manifest.json)；[release record](../../../evals/code/code-golden-releases.jsonl)；[最终 Gate §5](reviews/01_CODE_C0_GATE_REVIEW.md) | released、不可变、三源、50/33/17、无 Unit treatment 标签 |
| C0-03 V1 graph-off baseline | **PASS** | [qualification record](../../../evals/code/runs/_qualification/records/9ba8081fa67285e5e9d418703b53d1ffc42562f33e24de2028afeaaa223a034f.json)；[Run manifest](../../../evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061/manifest.json)；[最终 Gate §3-§9](reviews/01_CODE_C0_GATE_REVIEW.md) | 唯一有效 baseline 已被 ledger 锚定、可重建、不可变并与正式库隔离 |

最终状态是：

```text
C0-01  PASS
C0-02  PASS
C0-03  PASS
C0     PASS
C0 Gate 当时的下一步授权：C1 AUTHORIZED
```

#### Run 身份、Golden 与不可变锚

| 项目 | 实际值 |
| --- | --- |
| 唯一有效 Run ID | `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061` |
| Display key | `CODE-EVAL-5A92EAFD` |
| Golden | `code-golden-v2` / release `code-golden-v2-release-001` |
| Golden v2 package hash | `sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61` |
| qualification record hash | `sha256:9ba8081fa67285e5e9d418703b53d1ffc42562f33e24de2028afeaaa223a034f` |
| manifest canonical hash | `sha256:6ef5a2f1b3c82ed9007da41fd17d07ad8fd64fd0f12532b2751dc0dd510035ca` |
| artifact-map hash | `sha256:9d9f3bc86a2e9e0419db54a85b89156c7593596a1903a940e2e37f8c1e33ab44` |
| SQLite SHA-256 | `sha256:8d5b9ea0c09bbf33874a55b584d3a0e2aa9f8174eb2d04a3f1a792b44008b8c4` |
| config fingerprint | `sha256:cffeadd4b4f7a0ba22e018b942c69e4ef869817a631d26d911af50c231f5fdc9` |

Golden v2 共 50 case，其中 33 条在当前 V1 能力下进入 baseline 评测分母，17 条因机器
能力边界保留为 ineligible；另有 15 条 smoke。实际 Run 的 isolated SQLite 保存 50 case/profile、
95 judgment、1 completed run、33 result 与 2,240 metric value，membership 与 released
package 精确一致。三类来源均真实物化并完成：controlled multilingual、当前项目
snapshot、historical/error repository。

固定 baseline 配置是：

```text
retriever=HybridRetriever
fusion=weighted-hybrid-v2
embedding=local-hash-v2
views=file.raw,symbol.raw
graph_candidate=false
graph_post_expand=record_separately
top_k=20
```

#### Baseline 实测

| 指标 | numerator / denominator | value |
| --- | ---: | ---: |
| Entity Recall@1 | `12 / 44` | `0.272727` |
| Entity Recall@5 | `33 / 44` | `0.750000` |
| Entity / Symbol Recall@10 | `37 / 44` | `0.840909` |
| Entity Recall@20 | `38 / 44` | `0.863636` |
| MRR@10 | `18.333333 / 29` | `0.632184` |
| nDCG@10 | `17.539576 / 29` | `0.604813` |
| Required Path Recall | `13 / 14` | `0.928571` |
| Exact Commit Accuracy | `32 / 38` | `0.842105` |
| Dirty Snapshot Accuracy | `5 / 6` | `0.833333` |
| Hard Negative Error@10 | `6 / 17` | `0.352941` |
| Harmful Candidate Rate@10 | `7 / 92` | `0.076087` |
| Required Context Coverage@10 | `0 / 43` | `0.0` |
| Unauthorized Leakage Rate@10 | `0 / 224` | `0.0` |
| Returned Locator Validity@10 | `224 / 224` | `1.0` |
| Candidate count | `303 / 33` | `9.181818` |

Latency 为 mean `6.560909 ms`、P50 `5.35 ms`、P95 `15.04 ms`；dense full-scan
没有独立计时，必须保留为 `unavailable`。SQLite 为 `4,956,160` bytes，active code
index logical/physical 分别为 `313,932 / 565,248` bytes。详细一手数据见
[latency.json](../../../evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061/latency.json)与
[storage.json](../../../evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061/storage.json)。

Run summary 的 `3/33=0.090909` 是 case acceptance pass rate，不是 Recall、MRR 或
nDCG。它与弱 baseline 的诊断价值不冲突，也不能被用作优化收益。

#### 失败切片与残余 unavailable

[error-analysis.json](../../../evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061/error-analysis.json)
记录 20 个 top miss、4 个 same-name error、3 个 wrong/missing-version observation、
12 个 graph-required miss、6 个含 harmful candidate 的 case、0 个 duplicate case、
3 个正确零结果和 5 类 candidate channel。`014/027/035` 的零候选分别符合
refuse/missing-evidence/refuse，因此是正确行为而非漏召。

残余 unavailable 必须按能力边界讲：

- 17 条不进入 treatment 分母：10 条 historical non-active CodeSymbol/tag resolution，
  7 条 stable validation artifact/TestResult retrieval；
- 95 个 judgment 全为 entity，versioned Retrieval Unit identity 仍未实现，因此 Unit
  Recall/duplicate 仍为 unavailable；
- paired graph-only recovery 为 29 条 positive unavailable、4 条 not applicable；
- dense full-scan timing unavailable；
- exact commit 有 24 条可计算、5 条 unavailable；missing/wrong version 有 30 条可计算、
  3 条 unavailable；
- `metrics.json` 中 1,511 个 available、729 个 unavailable metric record；所有
  unavailable 的 `value` 均为 `null` 且有非空原因。最终 artifact JSON 递归统计 1,207
  个 unavailable record，同样没有用 `1.0` 或其他数值伪装实现。

#### 隔离、安全、测试与旧 Run 审计

正式库 `var/evidence-rag.sqlite3` 的六个 evaluation 表
`evaluation_cases/evaluation_case_profiles/evaluation_candidate_judgments/evaluation_runs/`
`evaluation_results/evaluation_metric_values` 均为 0；baseline 只写 artifact 内嵌
isolated SQLite，正式库零污染。最终 SQLite 全 TEXT 与 artifact/ledger JSON 扫描均为
0 个临时绝对路径和 0 个真实 credential assignment，最终目录也没有
`-wal/-shm/-journal` sidecar。

最终 Gate 复验结果为 C0 专项 **55 passed**、全量 collection **129 tests**、两次全量
均 **129 passed**，并通过 ruff、C0 相关 format 与 `git diff --check`。旧口径
`130 passed` 在最终 main 不可复现，不得继续引用。

Qualification ledger 的审计口径是 latest decision，而不是目录是否存在：

- `3c63ce5394b643419bab7784c9c6c1b6`：revoked；
- `24fdc3e12de74c4198627b3df40f86f1`：曾 qualified，随后 revoked，latest 为 revoked；
- `d16b4602efed4458b06ac99c5de4bcd3`：从未有 qualification，历史
  `attempt-audit.json` 保留 invalidation；
- `5a92eafdff5d49e6aae8bb55fdc14061`：latest qualified，唯一有效。

旧目录保留用于审计，不得冒充当前 baseline；撤销由 append-only ledger 表达，没有原地
重写旧 terminal SQLite 或报告。

### C0-01：Code Evaluation V2

状态：PASS（最终 C0 Gate）  
依赖：无  
Feature Flag：无，离线能力

#### 目标

让评测直接调用 Code Retriever，并记录 rank、Unit、path、context、version、latency；
不再通过全局 `PlatformService.search` 间接评估 Code。

#### 文件

修改：

- [evaluation/models.py](../../../src/evidence_rag/evaluation/models.py)
- [evaluation/schema.py](../../../src/evidence_rag/evaluation/schema.py)
- [evaluation/store.py](../../../src/evidence_rag/evaluation/store.py)
- [evaluation/service.py](../../../src/evidence_rag/evaluation/service.py)

新增：

- [evaluation/code.py](../../../src/evidence_rag/evaluation/code.py)
- [test_code_evaluation_v2.py](../../../tests/test_code_evaluation_v2.py)

#### Additive Schema

新增：

```text
evaluation_case_profiles
  case_id
  source_domain
  task
  query_profile_json
  expected_unit_ids_json
  expected_entity_types_json
  expected_locators_json
  required_edge_types_json
  expected_context_roles_json
  expected_ref
  expected_answer_mode

evaluation_candidate_judgments
  case_id
  entity_id
  retrieval_unit_id
  relevance_grade     # -1/0/1/2
  necessity_role
  note

evaluation_metric_values
  evaluation_run_id
  case_id nullable
  source_domain
  metric_name
  slice_json
  value
```

不删除现有 `evaluation_cases/results` 字段。

#### 指标

- Entity/Unit Recall@1/5/10；
- File/Symbol Recall@10；
- MRR@10；
- nDCG@10；
- hard-negative error；
- Required Path Recall/Precision；
- graph-only recovery；
- wrong-version rate；
- locator accuracy；
- context role coverage；
- duplicate/harmful candidate rate；
- P50/P95；
- candidate counts/channel；
- index size。

#### 测试

- 无 expected IDs 的 case；
- 多个 acceptable entities；
- relevance `-1/0/1/2`；
- path direction；
- no-result；
- wrong commit；
- aggregate/slice；
- runner exception 写 failed，不留 running；
- config/model/generation 被持久化。

#### 验收

- 同一 snapshot 连续两次运行结果结构可比较；
- MRR/nDCG 用 deterministic implementation；
- run 保存 code commit、DB snapshot、generation、config；
- 现有 Evaluation API 继续工作；
- 不修改生产检索。

### C0-02：Code Golden v2

状态：PASS（最终 C0 Gate）  
依赖：C0-01

#### Fixture

至少三个仓库：

1. **当前项目 snapshot**
   - Python + JS/TS；
   - dirty worktree；
   -真实规模。
2. **可控多语言小仓库**
   - overload；
   - same-name symbols；
   - import alias；
   - nested functions/classes；
   - tests；
   - rename/move；
   - generated/vendor。
3. **历史/错误仓库**
   - 3+ commits；
   - branch/tag；
   - failing/passing validation；
   - stack trace；
   - deleted symbol。

fixture 放在 `tests/fixtures/code_rag/`，禁止依赖开发者主目录。

#### 50 条分布

| Task | 数量 |
| --- | ---: |
| exact location | 7 |
| implementation | 7 |
| call/reference path | 7 |
| bug localization | 7 |
| impact analysis | 7 |
| change context | 5 |
| historical/version | 5 |
| test/validation | 5 |

切片至少覆盖：

- 中文/英文；
- natural language→code；
- identifier-heavy；
- low lexical overlap；
- same-name hard negative；
- wrong version；
- generated/vendor；
- unanswerable；
- ACL forbidden；
- dirty/current；
- graph required；
- graph harmful。

#### 标注流程

1. 标 expected stable entity；
2. 标 acceptable Retrieval Unit；
3. 标 relevance grades；
4. 标 required path/edge direction；
5. 标 expected context roles；
6. 标 forbidden/harmful；
7. 第二人或延迟复审 20%；
8. disagreement 记录在 dataset metadata。

#### 验收

- 50 条完整、15 条 smoke；
- 每个 task 非空；
- hard negatives ≥ 15；
- unanswerable/ACL ≥ 5；
- Golden 版本不可变；
- 无 Treatment 知情偏置修改；修订必须升版本。

### C0-03：V1 Baseline

状态：PASS（最终 C0 Gate；唯一有效 Run 见 Actual C0 Run Evidence）  
依赖：C0-01、C0-02

#### 固化配置

```text
retriever=HybridRetriever
fusion=weighted-hybrid-v2
embedding=local-hash-v2
views=file.raw,symbol.raw
graph_candidate=false
graph_post_expand=record separately
dataset=code-golden-v2
```

#### 输出

- overall 与每 task/language/query-style slice；
- Top 20 miss；
- same-name errors；
- wrong-version；
- graph-required miss；
- harmful context；
- zero result；
- P50/P95；
- dense full-scan time；
- DB/index bytes；
- candidate count。

#### 验收

- 生成不可变 Baseline Run ID；
- 报告中不填 Target 为 current；
- 面试文档 Baseline 从“无”更新为 Run ID；
- C2/C3/C4 的每个 treatment 都可对比 C0。

## 8. C1：契约、Feature Flag 与双轨

### Actual C1 Gate Evidence

C1-01、C1-02 与 C1 overall 均为 **PASS**；该 Gate 当时给出的下一阶段状态是 C2
**AUTHORIZED / NOT_STARTED**，该历史裁决不被后续 C2 实施改写。一手裁决见
[最终 C1 Gate](reviews/02_CODE_C1_GATE_REVIEW.md)最前方“第四轮最小关闭结论”。
本轮使用共享 dirty `main`，没有 branch/worktree、commit/push，且只有一个 main
worktree。

最终关闭证据：

| 检查 | 结果 |
| --- | --- |
| 独立 Gate identity/snapshot/nonce 短 probe | **PASS** |
| 定向 adversarial pytest | **27 passed** |
| C1 三文件专项（contracts / V1 adapter / shadow） | **209 passed** |
| 主控全量 pytest | **338 passed** |
| Golden | **18 passed** |
| ruff / format / `git diff --check` | **PASS** |
| `src/tests` 只读内存 compile | **147 Python sources PASS**；不写 `.pyc` |
| `uv lock` | **PASS** |
| Golden fixture bytecode | **0 `.pyc`** |
| 正式库 | hash / `quick_check` / schema 均通过，未写入；无 C2 schema |
| 工作树 | **only main PASS** |

C1 新增运行依赖为 `cryptography>=45,<46`，lockfile 固定 `cryptography 45.0.7`，shadow
实际使用 `AESGCM`。C1 已验收的关键执行语义是：

- lexical/dense execution evidence 在最终 top-k 截断前记录真实 unique-entity raw rank
  与 `hit_count`；有命中但全被剪枝时显式输出 `complete_pruned`，不伪装
  `complete_no_match`；
- V1 adapter 保留 repository/project/commit/generation/locator，并验证 requested/effective
  ACL 与执行证据；用户可见 V1 默认路径和 endpoint contract 保持兼容；
- `rag_code_shadow=false` 时零 submit、零 adapt、零额外 retrieval；即使 engine flag 为
  `v2` 也不会提前切换用户路径；
- shadow 是 bounded async 路径：单次有界 normalize 后以 AES-GCM 密封敏感 worker
  snapshot，限制 inflight/observation/结构/字节预算，固定枚举错误与 keyed-hash
  telemetry，失败、超时或晚到结果均不改变 V1。

这些证据证明 C1 是可信迁移基础，不是 AST/exact/sparse/dense/graph/rerank 等检索质量
提升。后续 C2 工程实现与 C-B1 审计证据见下一节；C1 Gate 本身不支持任何优化百分比。

### C1-01：Code Source Contract

状态：PASS（最终 C1 Gate）  
依赖：C0-03

#### 新增类型

```python
CodeQueryProfile
CodeRetrievalBudget
CodeRetrievalCandidate
CodeRelationPath
CodeContextBlock
CodeSourceStatus
CodeSourceResult
```

`CodeRetrievalCandidate` 必须分开：

- stable `entity_id`；
- rebuildable `retrieval_unit_id`；
- raw channel scores/ranks；
- source-fused score；
- calibrated relevance；
- version alignment；
- fact/derivation/review；
- role；
- path；
- locator；
- token estimate；
- ACL。

#### Settings

在 [config.py](../../../src/evidence_rag/config.py) 集中增加：

```text
rag_code_engine=v1|v2
rag_code_shadow=false|true
rag_code_unit_builder=raw-v1|ast-v2
rag_code_embedding_profile=local-hash-v2
rag_code_graph=false|true
rag_code_semantic_resolver=off|scip-python
rag_code_reranker=off|profile
rag_code_context=snippet-v1|structured-v2
rag_code_canary_percent=0
```

禁止在各模块直接散落 `os.getenv`。

#### 验收

- Pydantic/dataclass schema tests；
- invalid enum/config fail fast；
-默认全 V1；
- config 不包含 secret；
- contracts 不 import Platform/Document/Codex。

### C1-02：V1 Adapter 与 Shadow Skeleton

状态：PASS（最终 C1 Gate）  
依赖：C1-01

#### 实现

`CodeSourceRetrieverV1Adapter` 包装当前 `HybridRetriever`，转换为 Code contract；
`CodeShadowRunner` 接收相同 Query/Profile，并行执行 V1/V2，V2 未实现时只验证 adapter。

Shadow output：

- top entity overlap；
- Kendall/Spearman 可选；
- rank deltas；
- latency；
- result count；
- wrong-version；
- exceptions；
- generation；
- 不保存敏感全文。

#### 接入

- `Runtime` 同时持有 legacy retriever 和 code source adapter；
- `PlatformService` 默认仍调用 legacy；
- shadow 异步/预算内，不影响响应；
- shadow 失败不改变 V1。

#### 验收

- V1 adapter top-k 与 legacy 等价；
- endpoint contract 不变；
- shadow error 可观测；
- ACL/commit scope 一致；
- flag off 无额外工作。

## 9. C2：AST Retrieval Unit、Exact 与 Sparse

### CURRENT_IMPLEMENTED_C2：工程 Gate 与质量审计分离

C2 工程能力已经实现并通过
[C2 Foundation Gate](reviews/03_CODE_C2_FOUNDATION_GATE_REVIEW.md)，但 C-B1 质量
treatment 没有通过。以下 `PASS` 仅指工程主流程、数据边界、兼容与可复验性，不表示
相对 C-B0 总体质量提升。

| 能力 | 工程状态 | 真实证据 |
| --- | --- | --- |
| C2-01 Schema / Store | **PASS** | 11 项 Store 专项；临时 SQLite 验证 additive migration、FTS、transaction、publication identity、generation 与 ACL/scope fail-closed |
| C2-02 Parser IR | **PASS** | 10 项 Parser IR 专项 + 5 项旧 parser 回归；单次 parse、稳定 structural path、UTF-8/CRLF locator、partial/fallback |
| C2-03 AST Unit Builder | **PASS** | 6 项 Builder 专项；frozen record、稳定 Unit identity、版本/ACL/lineage、真实 locator 与无 DB 副作用 |
| C2-04 Dual-write | **PASS** | `raw-v1` off、`ast-v2` success、published 后 derivation/finalize failure 与 cleanup-error 共 5 个临时 SQLite 关闭场景 |
| C2-05 Exact + Fielded Sparse | **PASS** | 17 项 retrieval 专项；真实 Exact/FTS、weighted RRF、scope/ACL/publication fail-closed、strict trace/contract |
| symbol entity projection | **PASS** | 26 项回归 + 11 个临时 SQLite 语义 case；72/72 symbol Units 投影到稳定 `#symbol=` entity |
| entity-aware diversification | **PASS** | 17 项 retrieval 专项 + 独立临时 SQLite probe；Top-10 先 distinct entity，CodeSymbol/FileVersion 使用 canonical representative，扩大 `top_k` 后才补 child |

默认兼容与隔离边界保持不变：

- [config.py](../../../src/evidence_rag/config.py) 默认
  `rag_code_engine=v1`、`rag_code_unit_builder=raw-v1`；C2 dual-write 仅在
  `ast-v2` 显式开关下接入；
- V1 endpoint、adapter/shadow contract 与 flag-off 零额外 retrieval 保持兼容；
- C2 Gate 的数据库探针均使用 `tmp_path` / `TemporaryDirectory`；三次 C-B1 使用各自
  artifact 内隔离 SQLite，正式库未写入；
- publication、project/repository/generation、version、ACL、locator 与 rollback/
  cleanup 继续 fail closed。

#### 三次真实 C-B1 审计链

三次 Run 都使用 production `CodeExactSparseRetriever`、`ast-v2`、同一 released Code
Golden v2（50 total / 33 eligible / 17 ineligible）及 C0 唯一 qualified baseline
`evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`。三者均完成
隔离 artifact 与 verify-only；manifest 中 `execution_mode=qualified` 只表示获授权的
生产执行路径，最终均为 `treatment_qualified=false` / `acceptance=not-qualified`。

| Run | 真实变更与诊断 | Overall entity recall@10 | Expected locator recall@10 | Entity duplicate@10 | 结论 |
| --- | --- | ---: | ---: | ---: | --- |
| [`ef293bbe…`](../../../evals/code/runs/ef293bbe574543e0a4224250b0dad0c9/manifest.json) | 首次生产执行；symbol entity projection 缺失 | `0.000000` | `0.000000` | `0.777778` | 审计证据；**NOT QUALIFIED** |
| [`ee42ee27…`](../../../evals/code/runs/ee42ee27441a4361a2ec40f483c26be1/manifest.json) | 修复 projection；正确 entity 恢复，但 same-entity child 拥挤 | `0.522727` | `0.477273` | `0.477679` | 审计证据；**NOT QUALIFIED** |
| [`33f76ed5…`](../../../evals/code/runs/33f76ed55a3d4c9ab1060a54bfa13d35/manifest.json) | 加入 entity-aware diversification | `0.613636` | `0.590909` | `0.129464` | 审计证据；**NOT QUALIFIED** |

第三次 Run 的 [完整 metrics](../../../evals/code/runs/33f76ed55a3d4c9ab1060a54bfa13d35/metrics.json)
表明 Exact entity recall@10 为 `0.833333`、Identifier entity recall@10 为 `1.000000`，
均与 C-B0 持平；但 overall expected locator recall@10 仍从 C-B0 的 `0.840909` 降至
`0.590909`，所以 locator non-regression Gate 失败。Low-overlap entity recall@10
为 `0.600000`；[error analysis](../../../evals/code/runs/33f76ed55a3d4c9ab1060a54bfa13d35/error-analysis.json)
记录 10 个 zero-result case；独立 Gate 将尚未命中的版本化 identity 合并为 7 个目标
实体。这些是 C3 Dense/Profile 的真实输入切片，不是 C2 相对 C-B0 提升证据。

C0 的 `5a92...` 仍是唯一 qualified baseline；以上三个失败 Run 必须永久作为修复与拒绝
质量结论的审计链保留，不能写入 qualification ledger 或改称 qualified treatment。

### C2-01：Code V2 Schema 与 Store

状态：PASS（C2 engineering Gate；CURRENT_IMPLEMENTED_C2）  
依赖：C1-02

#### 新 Schema Owner

`src/evidence_rag/rag/sources/code/schema.py`

```text
code_retrieval_units
  rowid PK
  id
  entity_id
  parent_unit_id
  repository_id
  generation_id
  project_id
  unit_type
  ast_node_type
  ordinal
  language
  path
  qualified_name
  signature
  content
  context_ref_json
  start_line/end_line
  start_byte/end_byte
  token_count
  content_hash
  builder_version
  quality_status
  acl_ref
  metadata_json
  UNIQUE(id,generation_id)

code_unit_vectors
  unit_id/generation_id/profile/model/dimension/vector/content_hash
  UNIQUE(unit_id,generation_id,profile,model)

code_unit_embedding_cache
  cache_key/profile/model/dimension/vector/created_at/last_used_at

code_relation_diagnostics
  generation/source_entity/relation_type/raw_target/reason/candidates/parser

code_retrieval_calibrations
  id/profile/task/entity_type/model/artifact_ref/metrics/status/time

code_index_publications
  generation/builder/sparse/embedding/graph/status/validation/created_at
```

FTS：

```text
code_retrieval_units_fts(
  qualified_name,
  signature,
  path,
  identifiers,
  doc,
  body
)
```

字段在 Unit 表中可存 metadata 或拆列，但 FTS 输入必须明确。

#### Store API

- insert units/vectors/diagnostics；
- exact lookup；
- fielded sparse；
- dense candidate iterator；
- load entity/context；
- active generation filter；
- publication status；
- cleanup generation；
- integrity stats。

#### Migration

- additive；
- initialize 幂等；
- 空表上线；
- 不 backfill 旧 Generation；
- reingest 或专用 builder 为 active Generation 建 V2；
- rollback 只关 flag，不删表；
- cleanup 在 V2 稳定后另 PR。

#### 验收

- foreign identity/integrity 检查；
- active generation 隔离；
- ACL 字段完整；
- delete repository 清理 V2 表；
- transaction rollback；
- FTS trigger insert/update/delete；
-旧 DB 可启动。

### C2-02：Parser IR

状态：PASS（C2 engineering Gate；CURRENT_IMPLEMENTED_C2）  
依赖：C2-01

#### 目标

当前 `ParsedSymbol` 只保存完整正文、calls、references，无法构建结构化 block。新增：

```python
ParsedCodeUnitIR(
  structural_path,
  node_type,
  role,
  start/end byte,
  start/end line,
  text,
  parent_structural_path,
  identifiers,
  signature,
  doc,
  quality
)
```

#### 实现原则

- 在一次 Tree-sitter parse 内产生 Symbol 与 Unit IR，避免重复解析；
- structural path 使用 node type + sibling ordinal，不依赖绝对 line；
- 记录 grammar/parser version；
- parse tree 不跨线程持久化；
- 不把 Tree-sitter node 对象写入 dataclass；
- 非 structured language 只产 fallback IR；
- syntax error 保留 partial AST，但 Unit quality=`partial`。

#### 测试矩阵

- Python function/class/nested/async/decorator；
- JS/TS function/class/arrow/method；
- TSX component/hook；
- overload/getter/setter；
- long branch/loop/try；
- Unicode；
- CRLF；
- syntax error；
- empty/generated/minified；
- stable structural path。

#### 验收

- 现有 `ParsedSymbol` 行为不回归；
-同文件重复 parse IR deterministic；
- byte/line locator 正确；
- parser exception 不阻塞 repository。

### C2-03：AST Unit Builder

状态：PASS（C2 engineering Gate；CURRENT_IMPLEMENTED_C2）  
依赖：C2-02

#### 规则

1. 每个 Symbol 产 `symbol.signature`；
2. Symbol ≤ 600 tokens，产一个 `symbol.ast_block`；
3. > 600 时按 compound/branch/loop/try/nested declaration 递归；
4. block 目标 80–400 tokens；
5. 小同父 block 合并；
6. 无法继续拆时按 statement boundary；
7. class 产 `class.surface`，只含 doc/fields/method signatures；
8. file 产 `file.surface`，只含 module doc/import/export/top symbols；
9. generated/vendor/minified 只产 metadata surface；
10. parse error fallback 产 line-aware block，quality=`fallback`。

#### Unit ID

```text
unit://sha256(
  entity_id
  + unit_type
  + structural_path
  + content_hash
  + builder_version
)
```

`entity_id` 不因 builder 变化而变化；`unit_id` 可以变化。

#### Retrieval Text

包含：

- language/repository/path；
- qualified name/kind/signature；
- parent；
- doc；
- imports/types；
- used identifiers；
- selected block。

不得对每个 block 重复完整父 Symbol。

#### Context Ref

- parent entity；
- structural path；
- source range；
- required imports/type IDs；
- direct relation IDs；
- neighbor unit IDs。

#### 验收

- Unit 80–400 token 目标分布报告；
- >400 的例外有 reason；
- duplicate source token ratio；
- locator accuracy；
- generated file 不产生大量 Unit；
- stable ID fixture；
- 85K-char Symbol 不产生单个巨大向量。

### C2-04：Dual-write 与 Generation 发布

状态：PASS（C2 engineering Gate；CURRENT_IMPLEMENTED_C2）  
依赖：C2-03

#### Ingest 重构

将 [IngestionService._build_records](../../../src/evidence_rag/ingestion.py) 拆为：

```text
EntityBuilder
RelationBuilderV1
CodeUnitBuilder
CodeIndexPublisher
```

但第一步保持原输出等价。

#### Dual-write

```text
entities/edges/search_views v1
+ code_retrieval_units/code indexes v2
→ validate both
→ publish repository active generation
```

V2 publication 单独保存状态，防止 entity Generation 已发布但 V2 index 半成。

#### Integrity

- entity/unit IDs unique；
- all Unit parent entities exist；
- all Unit locator in entity range；
- all ACL equal/stricter than parent；
- FTS row count；
- vector profile completeness；
- no quarantined raw derivative；
- builder/parser versions；
- smoke exact queries；
- publication hash。

#### 回滚

- V2 build 失败：保持当前 V1 active；
- V1 成功 V2 失败：Repository 可 ready，但 V2 status partial，不能路由 V2；
- V2 flag off 立即回 V1；
- 不删除 last-known-good V2 publication。

### C2-05：Exact + Fielded Sparse Retriever

状态：PASS（C2 engineering Gate；C-B1 quality NOT QUALIFIED）  
依赖：C2-04

#### Exact

解析：

- code URI；
- full/short SHA；
- qualified symbol；
- exact path/basename；
- stack frame；
- test selector；
- error code；
- branch/tag。

优先级：

```text
URI > full SHA > qualified name > exact path > exact symbol > basename
```

同名不静默选择，保留 module/path hard negative。

#### Sparse

预规范化：

- snake/camel/Pascal 拆分；
- dotted qualified name；
- path segments；
- Unicode/CJK bigram；
- error/operator token；
-保留原 identifier。

初始 FTS 权重：

```text
qualified_name 4.0
signature      3.0
path           2.5
identifiers    2.0
doc            1.5
body           1.0
```

最终权重由 C2 treatment 消融决定。

#### Fusion

Exact + sparse 先用 weighted RRF，不与 V1 raw score 直接相加。返回 channel rank 和
explanation。

#### Evaluation

```text
C-B0 V1
vs
C-B1 AST Unit + exact/sparse
```

必须报告：

- exact slice；
- identifier slice；
- low-overlap slice；
- same-name hard negative；
- duplicate；
- index bytes；
- ingest time；
- retrieval P95。

#### 验收

- qualified C-B1 Run（当前三次均未满足）；
- exact identifier regression 为 0；
- Locator Accuracy 不下降（第三次仍未满足）；
- harmful/duplicate 改善或解释；
- V1 endpoint 默认不变。

## 10. C3：Dense、Reranker 与 Calibration

### Actual C3 Engineering / Production Evidence

C3 的工程实现已经封板，但两个真实 treatment 都未通过质量门。最终工程裁决见
[C3 Gate Review](reviews/04_CODE_C3_GATE_REVIEW.md)，production artifacts 为：

| Run | Production treatment | 固定分母 / 验证 | 关键真实结果 | 裁决 |
| --- | --- | --- | --- | --- |
| [C-B2 `6fad2dd1…`](../../../evals/code/runs/6fad2dd1c67846668ba38045ddae0064/manifest.json) | AST + Exact/Sparse + local-hash Dense | verified 33/33 | entity `0.7273`、locator `0.5682`；27/33 有 dense 贡献，恢复 C-B1 10 个 zero-result 中 4 个 | **NOT QUALIFIED** |
| [C-B5 `baf5b9eb…`](../../../evals/code/runs/baf5b9eb49464ca79920030fd1c772de/manifest.json) | C-B2 pipeline + deterministic reranker + provisional calibration | verified 33/33 | locator `0.636364` vs C-B0 `0.840909`，delta `-0.204545`；65 observations，ECE `0.285`，Brier `0.307029` | **NOT QUALIFIED** |

C-B0
`evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
仍是唯一 qualified baseline；C-B1、C-B2、C-B5 均为不可冒充 baseline 的 audit
controls。C-B2 的局部 dense recovery 和 C-B5 的 locator 相对 C-B2 回升都不能扩张为
相对 C-B0 的整体质量提升。Qwen3/BGE/code-specific/remote learned embedding 与
cross-encoder 均 unavailable/not-run。

### C3-01：Embedding Provider/Profile

状态：PASS（engineering；不等于 embedding quality PASS）  
依赖：C2-05

#### 已实现 Protocol

```python
EmbeddingProfile(
  id,
  purpose,          # code_nl/code_code/history/error
  model,
  revision,
  dimension,
  instruction,
  max_tokens,
  batch_size,
  locality,
  redaction_policy
)

EmbeddingProvider.embed_batch(texts, profile)
```

`EmbeddingProfile` 为 frozen/versioned canonical snapshot，除上述字段外还绑定
instruction revision、normalization revision 与 locality/redaction policy；
`EmbeddingProvider` 返回 model/revision/dimension/locality provenance。

#### 已实现工程合同

- content-addressed cache 与 per-profile/model namespace；
- cache key 覆盖 content、profile、instruction、normalization、model/provider revision；
- batch 完整性、dimension、finite vector 与 provenance 严格校验；
- secret/quarantined/max-token 内容在 provider/cache 之前 fail closed；
- remote 默认 off；未注入 provider 时只允许离线 `local-hash-v2`；
- provider unavailable/partial batch/mixed dimension 在任何 cache/vector 写前失败；
- 仅显式 `allow_local_fallback` 才可降级到 local-hash，并记录真实 fallback provenance；
- 相同内容可跨 Unit 命中 cache，profile/model revision 变化产生 cache miss。

#### Cache Key

```text
unit content hash
+ profile id/revision
+ actual provider model/revision
+ instruction hash/revision
+ normalization revision
+ cache schema revision
```

#### Gate 结果

以上 cache、mixed dimension、secret/quarantine、partial failure、fallback/provenance 与
临时 SQLite 隔离合同均已通过 C3-01 Gate。工程 PASS 不代表 learned embedding 已运行。

### C3-02：Dense Model Benchmark

状态：engineering PASS；C-B2 local-hash treatment **NOT QUALIFIED**  
依赖：C3-01

#### 实际模型边界

- `code_nl/local-hash-v2@1` 是唯一实际 ready/运行的离线 deterministic profile，
  provider 为 `local-hash/local-hash-v2@2`、384 维；
- Qwen3-Embedding-0.6B、BGE-M3、code-specific embedding 与 remote upper bound
  均 unavailable/not-run；未下载模型、未使用网络，不得补写 learned-model 结果。

#### 已实现 Dense/Hybrid

- `CodeDenseProfilePublisher` 以 building → ready/unavailable 状态封住部分发布；
  vector verification 或 publication validation 失败会清理该 generation/profile
  vectors，并保持 `dense_retrieval=false`；cache 只作可复用中间结果；
- dense query 严格绑定 project/repository/generation/ACL、完整 Profile snapshot、
  provider provenance、dimension 与 content hash，异常返回显式 unavailable/error；
- `CodeHybridV2Retriever` 合并 exact/sparse/dense 真实通道，固定 weighted RRF
  exact `2.0`、sparse `1.0`、dense `1.0`、`k=60`；exact hard signal 优先并执行
  entity-aware diversification；
- trace 保留各通道真实 rank/score、disabled/no-match/pruned/with-hits/
  unavailable/error outcome、dense provenance 与 fallback/失败原因。

#### Profile 规划边界

- `code_nl`：本轮只有 local-hash 实际运行；
- `code_code`、`code_history`、`error_code`：仅保留 profile/purpose 边界，模型未选择、
  benchmark 未运行。

#### 选择规则

先满足：

- low-overlap Recall；
- no regression exact/identifier；
- 内存；
- ingest throughput；
- query P95；
- 模型 license/deployment；
- remote security。

再考虑平均分。公开 benchmark 不代替本项目 Golden。

#### 输出

- 已产出并 verified：
  `evaluation-run://project-code-golden-v2/6fad2dd1c67846668ba38045ddae0064`；
- overall entity recall@10 `0.7273`，locator recall@10 `0.5682`；
- 27/33 case 有 dense contribution，恢复 C-B1 10 个 zero-result 中 4 个；
- locator 相对唯一 qualified C-B0 `0.8409` 回退 `-0.2727`，low-overlap
  `0.6000` 未严格提升，query P95 `550.13 ms`；
- 因此 C-B2 **NOT QUALIFIED**；局部 recovery 只能作诊断事实。

### C3-03：Code Reranker 与 Calibration

状态：engineering PASS；C-B5 deterministic reranker/calibration treatment
**NOT QUALIFIED**  
依赖：C3-02

#### 已实现 Reranker

输入 Evidence Card：

- query/profile；
- Unit text；
- symbol/path/signature；
- exact/sparse/dense ranks；
- version；
- parse/generated quality；

输出：

- deterministic feature score/rank；
- necessity role；
- negative reason；
- explanation code。

当前实现以 version/generation/ACL hard gate 先于排序，随后用 exact hard signal、
exact/sparse/dense rank、path/qualified name、same-name、child duplicate、
parse/generated/partial 等受控特征形成确定性全序。输入预算 30–60；deadline 或异常时
保留原 hybrid RRF 顺序、fused score 与真实 identity。成功和 fallback 都重建严格
`CodeSourceResult`，trace 记录 feature contribution、accepted/rejected、原/新 rank、
fallback reason/type/count；不输出 truth/verified。cross-encoder
**unavailable/not-run**。

#### 已实现 Calibration

- artifact 绑定 reranker/profile/model/index version 并使用 canonical hash；
- 无样本为 unavailable；样本不足使用 rank-percentile empirical calibration 并标
  provisional；足量标注支持固定分箱/PAVA；
- apply 时任一版本不匹配即 fail closed，并记录 unavailable reason；
- C-B5 只有 65 个 run judged observations，ECE `0.285`、Brier `0.307029`，仍为
  provisional，`probability_claim_allowed=false`。

#### Evaluation

- C-B2 dense；
- C-B5 reranker；
- no-rerank fallback；
- hard-negative error；
- harmful candidate；
- ECE/Brier；
- P95/成本。

#### Production 结果

- 已产出并 verified：
  `evaluation-run://project-code-golden-v2/baf5b9eb49464ca79920030fd1c772de`；
- locator `0.636364` vs C-B0 `0.840909`，delta `-0.204545`，因此唯一 quality
  Gate 失败，treatment **NOT QUALIFIED**；
- 真实 Run fallback/timeout 为 `0/0 of 33`：可以证明未出现 fallback 错误，不能宣称
  production timeout 演练已发生；exception/deadline 保序 fallback 仅有工程 Gate 证据；
- query P95 `693.645 ms`，相对 C-B0/C-B2 均回退；cross-encoder 未运行；
- wrong-version hard gate、fallback contract、解释 trace、calibration versioning 与
  claim honesty 均通过工程 Gate，C3 engineering 至此 `COMPLETE`。

## 11. C4：Typed Graph Retrieval

### Actual C4 Engineering 与 C-B3 Evidence

[最终 C4 Gate](reviews/05_CODE_C4_GATE_REVIEW.md) 的当前裁决为：

```text
C4-01 PASS
C4-02 engineering COMPLETE
C4-03 Query Profile / Fusion PASS
C4 overall engineering COMPLETE
C5-01 AUTHORIZED（C4 Gate 当时）
C-B3 production artifact audit PASS
C-B3 NOT QUALIFIED
```

工程亮点与质量裁决必须分开：

- C4-01 实现 typed、exhaustive、immutable edge registry 与有界 unresolved
  diagnostics；edge assertion 对 endpoint、derivation、version/generation 与 review
  requirement fail closed；
- C4-02 使用 SQLite `mode=ro` adjacency，并在存储查询与 retriever 两层校验
  publication、stable version、双端 ACL、registered type/direction/confidence；遍历具有
  deterministic beam、cycle prevention、node/edge budget、deadline 与完整 path trace；
- C-B3 authorization 使用自包含、可移植的 normalized authorization snapshot；既有
  artifact 通过 `strict-legacy-v1` 独立校验，不依赖当前可追加 Gate 文本；
- C4-03 实现 8 个 frozen/versioned task profiles 与
  `CodeSourceFusionPipeline`：profile/rewrite → exact → sparse+dense hybrid → seed
  fusion → typed graph → optional history/test hooks → entity/unit/version dedup →
  deterministic reranker → calibration → adaptive-k → strict `CodeSourceResult`；
- simple exact fast path 会明确跳过 dense/graph；graph-required profile 的真实 graph
  hit 可进入 candidate generation。缺 hook/provider/artifact 时诚实记录
  skipped/unavailable；timeout/error/fallback/degrade/refuse 进入 typed trace，refuse
  会清空候选，不留下伪成功结果；
- C4-03 独立 Gate P0/P1=`0`，C4 overall engineering `COMPLETE`。该 Gate 当时只授权
  C5-01；后续真实状态已由
  [最终 C5 Gate](reviews/06_CODE_C5_GATE_REVIEW.md)推进为 C5 overall engineering
  `COMPLETE`、C-B4 `VERIFIED — NOT QUALIFIED` 与 C6-01 `AUTHORIZED`。

唯一 C-B3 production artifact 已验证：
[manifest](../../../evals/code/runs/92f8d8e190f449bab9ba253227473419/manifest.json) /
[metrics](../../../evals/code/runs/92f8d8e190f449bab9ba253227473419/metrics.json) /
[graph analysis](../../../evals/code/runs/92f8d8e190f449bab9ba253227473419/graph-analysis.json) /
[latency](../../../evals/code/runs/92f8d8e190f449bab9ba253227473419/latency.json)。
Run ID 为
`evaluation-run://project-code-golden-v2/92f8d8e190f449bab9ba253227473419`；
`status=verified`、`treatment_qualified=false`。四个 arms
`graph_off / graph_post_only / typed_graph / graph_reranker` 各 33 case，并共享一个
immutable membership。C-B0 `5a92...` 仍是唯一 qualified baseline；C-B1 `33f7...`、
C-B2 `6fad...`、C-B5 `baf5...` 仅作 audit controls。

| treatment | Entity Recall@10 | Locator Recall@10 | MRR@10 | nDCG@10 | Query P95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| graph off | `0.7273` | `0.5682` | `0.3505` | `0.4209` | `570.803` |
| graph + reranker | `0.7273` | `0.5682` | `0.3975` | `0.4537` | `692.525` |
| treatment − graph off | `0` | `0` | `+0.0470` | `+0.0328` | `+121.7` |

MRR/nDCG 的同轮排序变化没有带来 Entity 或 Locator Recall 增益，且 P95 增加
`121.7 ms`，所以 C-B3 为 **NOT QUALIFIED**，不得声称 Graph 提升召回。

33 个 case 中 12 个有 released typed path truth，另 21 个保持 unavailable；12 个可评
case 的 Required Path Recall/Precision 均为 `0`，accepted paths=`0`，
graph-only recovery=`0`。graph + reranker 的 27 条 production traces 扫描
`4101` edges、扩展 `719` nodes，并记录 `cycle=1001`、`budget=1095` prunes，但
accepted path 仍为 `0`；这些计数只证明 traversal、cycle/budget contract 真实执行，
不是有效路径、召回或质量收益。

### C4-01：Edge Ontology 与 Diagnostics

状态：engineering COMPLETE（C4-01 PASS）  
依赖：C2-05

#### Registry

每条 Code edge 定义：

- source/target type；
- direction；
- inverse；
- owner；
- derivation；
- confidence meaning；
- version/generation；
- allowed tasks；
- default traversal cost；
- review requirement。

P0 edge：

```text
DEFINES
CONTAINS
IMPORTS
CALLS
REFERENCES
PARENT_OF
TYPE_OF
IMPLEMENTS
OVERRIDES
TESTS
COVERS
AFFECTS
VALIDATED_BY
FAILED_VALIDATION
SAME_SYMBOL_AS
RENAMED_TO
MOVED_TO
```

并非全部在 C4 构建；Registry 先定义语义。

#### Diagnostics

Engineering 实现按以下 schema 对 unresolved 采样/聚合：

- source entity；
- raw target name；
- relation type；
- reason：no candidate/ambiguous/external/dynamic/limit；
- candidate IDs；
- parser/resolver version。

为控制数据量：

- 每 source/relation 上限；
- aggregate count 永久；
- detail 可采样；
-不把 local variable 变成 entity。

#### 验收

-自由 edge type 不能进入 traversal；
- deterministic/static/semantic/human 分层；
- unresolved reason 可按语言/模块聚合；
- semantic edge 不伪装 CALLS。

### C4-02：Typed Graph Retriever

状态：engineering COMPLETE；C-B3 quality NOT QUALIFIED  
依赖：C4-01

#### 输入

```python
GraphTraversalRequest(
  seed_candidates,
  task,
  directions,
  edge_types,
  max_hops,
  beam_width,
  min_confidence,
  version,
  acl,
  node_budget,
  edge_budget
)
```

#### 算法

1. exact/sparse/dense top 5–10 seeds；
2. task edge whitelist；
3. DB 前置按 generation/edge/direction 查 adjacency；
4. 两端 ACL；
5. cycle prevention；
6. beam traversal；
7. path scoring；
8. 只返回补 target/dependency/test/history role 的节点；
9. path explanation；
10. relation-only candidate 进入 reranker。

初始：

```text
beam=20
hop_decay=0.72
max_hops=profile 1–4
```

#### 必测

- `max_hops=0/1/2/3` 结果不同；
- incoming/outgoing；
- edge whitelist；
- low confidence；
- cycle；
- high-degree helper；
- version mismatch；
- ACL hidden middle node；
- node/edge budget；
- timeout；
- empty seeds。

#### Trace

- expanded nodes/edges；
- pruned by type/direction/version/ACL/confidence/cycle/budget；
- accepted paths；
- graph-only recovered；
- latency。

### C4-03：Query Profile 与 Code Source Fusion

状态：engineering COMPLETE（独立 Gate PASS；P0/P1=0；不等于 quality qualified）  
依赖：C3-03、C4-02

#### Actual Engineering Evidence

- registry 以 immutable mapping 覆盖 8 个 `CodeTask`，每类各有一个
  frozen/versioned `CodeQueryProfileV2`，并固定 rewrite、channel/budget、
  edge/direction/hops、required roles、context template、adaptive-k 与
  degrade/refuse policy；
- `CodeSourceFusionPipeline` 组合现有 exact/sparse/dense、typed graph、
  deterministic reranker 与 calibration contract；history/test 仅经显式 optional
  hooks 接入，不另造 resolver、store 或写路径；
- simple exact identifier/location 走 exact fast path，dense/graph 明确为 `skipped`；
  graph-required profile 的 graph candidates 可真实进入候选生成，最终再次执行
  repository/version/generation/ACL governance 与 entity/unit/version dedup；
- rerank、calibration、adaptive-k 与严格 `CodeSourceResult`/typed trace 均已闭环；
  skip/no-match/timeout/error/fallback/degrade/refuse 全部可审计；
- 缺 hybrid/graph/calibration provider 或 history/test artifact 时返回诚实
  `unavailable`/`skipped`；缺 required role 时按 profile 明确 degrade 或 refuse，
  refuse 清空候选；
- 独立 Gate 的 production-component probe 证明 exact fast path 为 strict result；
  impact/zero-path 场景会诚实 refuse。该工程证据不覆盖 C-B3 的 0 path/0 recovery
  负向质量结论。

#### Deterministic Profile

任务：

- exact location；
- implementation；
- call/reference path；
- bug localization；
- impact；
- change context；
- historical；
- test/validation。

每个 profile 定义：

- rewrite；
- channels/budgets；
- edge types/directions/hops；
- required roles；
- version；
- adaptive-k；
- context template；
- refusal/missing。

#### Source Pipeline

```text
parse CodeQueryProfile
→ exact
→ sparse+dense parallel
→ source seed fusion
→ typed graph/history/test expansion
→ entity/unit/version dedup
→ code rerank
→ calibration
→ adaptive-k
→ CodeSourceResult
```

#### Evaluation

C4-03 engineering 已完成，但独立 Gate 未创建新 quality Run；C-B3 四臂仍是当前
同分母离线质量证据且 NOT QUALIFIED。以下保留为 resolver 等后续变量接入后的复跑合同：

- C-B3 graph candidate；
- graph off；
- graph post-only；
- typed graph；
- graph+reranker；
- Required Path Recall；
- graph-only recovery；
- graph noise/harmful；
- P95。

#### 验收

- Graph 真正进入 candidate generation；
- path metrics 有 treatment；
- 简单 exact query 可跳过 graph/dense；
- profile 规则版本化；
-所有 stop/fallback 写 trace。

## 12. C5：Python SCIP 语义关系 MVP

### Actual C5 Engineering 与 C-B4 Evidence

[最终 C5 Gate](reviews/06_CODE_C5_GATE_REVIEW.md) 的当前裁决为：

```text
C5-01 SCIP Consumer / Safe Execution PASS
C5-02 Python Semantic Edge Treatment engineering PASS
C5 overall engineering COMPLETE
P0/P1 = 0
C-B4 production audit VERIFIED — NOT QUALIFIED
C6-01 AUTHORIZED
```

这里的 `engineering COMPLETE` 只表示 consumer、safe runner、semantic treatment、
artifact/verifier 和 fail-closed 质量语义已经验收；不表示 semantic edge quality
qualified，也不表示用户默认路径或 Code RAG V2 已发布。

### C5-01：SCIP Consumer 与安全执行

状态：engineering PASS  
依赖：C4-01

#### 已落地选择

SCIP 是语言无关 code intelligence protocol，可表达 definition/reference/implementation。
本系统作为 SCIP consumer，不自行修改 indexer。

#### 输入模式

工程合同保留两种输入：

1. 消费用户/CI 提供的 `index.scip`；
2. 显式 opt-in 后才允许安全 runner 执行 exact-pinned indexer。

无 SCIP 时返回受控 diagnostics 并保留 Tree-sitter fallback。runner 默认 off；最终
Gate 使用固定 fixture、程序化输出、临时假 runtime 与注入 executor 验证安全边界，
**没有实际下载、联网或执行外部 indexer**。

#### 已验收安全边界

- external tool 默认 off，显式 opt-in 与 exact command/package/container pin；
- shell/interpreter escape、response file、仓库脚本、repo read-write 与缺失 network
  isolation 均 fail closed；
- repository root/cwd 与 clean env 固定，no shell；容器网络关闭并限制
  CPU/memory/pids/output/timeout；
- stdout/stderr、`index.scip` content hash、RawObject 与 derivation 受控；
- 环境发现、resolver error/timeout 或缺失 isolation 时返回 partial/unavailable，不阻塞
  current code，也不生成伪 resolved evidence。

#### 已验收 Consumer

- bounded protobuf parser 对 file/message/depth/field/cardinality/occurrence 等独立设限；
- 解析并规范化 SCIP metadata/document/symbol/occurrence/relationship；
- canonicalize path/position；
- resolver 严格 link 到同 scope 的 FileVersion/CodeSymbol，unresolved/ambiguous/
  unavailable 全部 fail closed；
- external symbols 单独标 external；
- provenance=`scip`；
- unresolved/ambiguous diagnostics；
- indexer/protocol version。

#### Schema

新增或复用：

```text
code_symbol_occurrences
code_external_symbols
code_semantic_index_runs
```

#### 测试

- 固定小 `index.scip` fixture；
- definition/reference；
- external dependency；
- bad path；
- invalid protobuf；
- duplicate occurrence；
- timeout/unavailable；
- ACL/generation；
- malicious oversized index。

### C5-02：Python Semantic Edge Treatment

状态：engineering PASS；C-B4 quality NOT QUALIFIED  
依赖：C5-01

#### 实际支持范围

Python 928/1,406 Symbol，占当前最大份额，因此 C5 以 Python-first 完成：

- Python：`SUPPORTED`；
- JavaScript：`UNAVAILABLE`（Golden 中 1 case）；
- TypeScript：`UNAVAILABLE`（Golden 中 2 cases）；
- treatment 比较 Tree-sitter conservative、SCIP semantic 与 merged policy 三臂。

#### 已验收关系与证据语义

- resolved internal occurrence 默认只产生 `REFERENCES`；
- `CALLS` 需要明确、可靠的 call occurrence evidence，不能由普通 reference 推断；
- `IMPLEMENTS` / `OVERRIDES` 需要严格 relationship、已解析 internal occurrence 与显式
  role evidence，未解析/local/external occurrence 不能授权内部边；
- definition/occurrence 不直接等同 runtime call；
- same canonical edge 合并 Tree-sitter/SCIP provenance set；
- 冲突保留 Tree-sitter baseline，并把 SCIP challenger 记录为 bounded diagnostics；
- scope/generation/locator/provenance 不完整或冲突时 fail closed；
- evaluation 只消费真实 label records；0 labels 必须保持 `UNAVAILABLE`，不能从结构计数
  生成 precision、coverage、unresolved reduction、noise 或 harmful quality。

#### 唯一 C-B4 production audit

唯一正式审计对象为
[`evaluation-run://project-code-golden-v2/a22b322a141741ae9a710aa347fbf032`](../../../evals/code/runs/a22b322a141741ae9a710aa347fbf032/manifest.json)。
离线 verifier 返回 `status=verified`、`treatment_qualified=false`，并精确绑定
`SemanticEdgeTreatment` / `c5-python-semantic-edges-v1`、Golden Python30、三臂
profile/generation 与 30 cases / 42 outputs 的 production-output attestation。

结构与性能证据如下；它们可证明执行、合并和资源边界，不是质量提升：

| Arm | Cases / edges | Provenance | Treatment P95 | Peak memory |
| --- | ---: | --- | ---: | ---: |
| Tree-sitter conservative | 30 / 42 `REFERENCES` | Tree-sitter 42 | `0.960875 ms` | `13,766 B` |
| SCIP semantic | 30 / 42 `REFERENCES` | SCIP 42 | `2.358417 ms` | `19,170 B` |
| merged policy | 30 / 42 `REFERENCES` | 42 条均为 Tree-sitter + SCIP 双 provenance | `3.228958 ms` | `18,984 B` |

三臂共 90 treatment rows；artifact-local SQLite 的 126 semantic-edge rows 逐行对账
无差异，`quick_check=ok`。artifact 恰为 9 个只读文件，无 symlink、WAL/SHM、
pyc/pycache、secret 或本机绝对/临时路径。详见
[treatments.json](../../../evals/code/runs/a22b322a141741ae9a710aa347fbf032/treatments.json)、
[performance.json](../../../evals/code/runs/a22b322a141741ae9a710aa347fbf032/performance.json)与
[security.json](../../../evals/code/runs/a22b322a141741ae9a710aa347fbf032/security.json)。

#### 质量裁决与停止条件

- 真实 label records=`0`、label status=`unavailable`、precision gate=`false`；
- edge precision、edge coverage、quality unresolved reduction、graph noise 与 graph
  harmful quality 全部为 `UNAVAILABLE` / `null`，不可用于 acceptance；
- 三臂 `42/42` target coverage、edge/provenance/conflict/unresolved 只能写成
  `non_quality_structural_counts` / `non_quality_structural_observation`；
- 因此 C-B4 的真实结论是 **VERIFIED — NOT QUALIFIED**，不能虚构 precision、人工
  labels 或 semantic quality 收益；
- qualification ledger 的唯一有效 qualified 决定仍是 C-B0
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`；
- JS/TS 保持 `UNAVAILABLE`；没有新的真实质量证据就不得扩展支持或上线叙事。

## 13. C6：History、Diff、Test 与版本

### C6-01：Historical Materialization 与 Symbol Lineage

状态：engineering PASS  
依赖：C2-03、C4-01

[最终 C6 Gate](reviews/07_CODE_C6_GATE_REVIEW.md) 已关闭首轮 4 个 P1，并验收：

- safe exact ref→full SHA，使用 bounded argv / `shell=False` 的 `git ls-tree` /
  `git show <sha>:<path>` 只读 immutable Git object；
- 真实复用 `CodeParser → CodeUnitBuilder`，历史正文、locator、generation、ACL 与
  target SHA 绑定；
- current/dirty/history namespace 严格分离，clean/dirty base SHA mismatch
  fail closed；
- memory copy-on-write 与仅限系统临时目录的 SQLite 原子
  publication/cache/rollback，selection identity 与 exact authorized read 闭环；
- TTL/LRU 保留 explicit/referenced/hot pins，后置 pin 单调 union，Commit fact 不随
  派生 publication 驱逐；
- `SAME_SYMBOL_AS` / `RENAMED_TO` / `MOVED_TO` 为 confirmed；
  split/merge 仅保持 review-required candidate。

#### 原问题

当前历史 API 可按 ref 读取/解析文件，但 Code Retriever 的 active `search_views` 只含当前
Generation。历史查询容易召回 Commit/Diff，却不能检索目标 commit 的 Symbol 正文。

#### 已落地策略

不全量索引所有历史：

- current 永久；
- 最近 N commit 的 commit/diff surface；
- 被 Claim/Run/Codex 引用的 commit；
- 用户显式查询 commit；
- 热点历史 Unit。

namespace：

```text
current: code/current/{repo}/{generation}
history: code/history/{repo}/{commit}
```

#### Materialization

1. resolve exact ref→SHA；
2. `git show sha:path`；
3. parse + Unit build；
4. content cache；
5. save historical publication；
6. exact version filter；
7. TTL/LRU 只清理派生 Unit，不删 Commit fact。

#### Lineage

信号：

- exact qualified name/path；
- Git rename；
- signature；
- AST/body similarity；
- neighbor symbols。

关系：

- SAME_SYMBOL_AS；
- RENAMED_TO；
- MOVED_TO；
- SPLIT/MERGE 只 candidate/review。

#### 验收

- historical query 返回目标正文；
- current body 不贴旧 SHA；
- dirty snapshot 与 clean commit 分开；
- rename/move fixture；
- explicit SHA accuracy；
- materialization cache/rollback。

### C6-02：Diff→Symbol

状态：engineering PASS / COMPLETE  
依赖：C6-01

#### 已实现

`DiffSymbolMapper.map_hunk` 已按 parent/target exact version/path 构建主路：

- hunk old range→old Symbol；
- hunk new range→new Symbol；
- rename path；
- add/delete；
- line overlap；
- AST enclosing node；
- confidence/ambiguity。

关系：

- Commit CONTAINS DiffHunk；
- DiffHunk AFFECTS SymbolVersion；
- introduced/removed/modified 作为 typed match role 保留，不伪造额外关系；
- old/new Symbol lineage 只引用 C6-01 confirmed evidence。

#### 测试

- function body；
- signature；
- class-level；
- file top-level；
- rename；
- delete；
- hunk跨多个 symbols；
- whitespace-only；
- binary；
- wrong parent。

#### 评测

- Diff→Symbol Precision/Recall；
- change context Recall；
- historical/impact path；
- false affected symbol。

#### C-B6 Production Run

唯一 C-B6 production audit：
[`evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e`](../../../evals/code/runs/06eea9107f274fe3b13d77bd68b8a00e/manifest.json)
已通过 immutable artifact verifier。C6-02 Gate 同时确认 exact production method
identity：

```text
evidence_rag.rag.sources.code.diff_symbol_v2.DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2
```

artifact membership 为 12 cases、8 labeled cases、16 canonical symbol labels；merged
arm 的真实指标为 precision/recall/F1=`0.888889/1/0.941176`，false affected
rate=`0.111111`。qualification 未通过：

```text
sample size: 8 < 30
precision: 0.888889 < 0.9
false affected rate: 0.111111 > 0.1
```

因此工程与 artifact 结论为 verified，但质量结论必须保持
**PROVISIONAL NOT QUALIFIED**。C-B0
`evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
仍是唯一 qualified global baseline，C-B6 不替代它。

C6 Gate 使用 memory/系统临时目录 SQLite，C-B6 使用 isolated artifact SQLite 与
immutable artifact 验收，结论不依赖当前正式库连接或 hash/mtime 不变性。正式库由
外部用户服务拥有并可变；该外部漂移不归因于 C-B6，本次文档同步也未打开或修改正式库。

### C6-03：Test/Validation→Symbol

状态：engineering PASS / COMPLETE  
依赖：C4-01、C6-02

[最终 C6 Gate](reviews/07_CODE_C6_GATE_REVIEW.md) 已关闭首轮
reported/observed status contradiction P1；C6-03 最终 P0/P1=`0`，C6 overall
engineering `COMPLETE`。

独立 fail-closed oracle 穷举 120 组
observation × status × exit code × reported status：

```text
VALIDATED_BY: 2
FAILED_VALIDATION: 6
OBSERVATION_ONLY: 112
False Validation: 0
```

C6-03 专项 `145 passed`，C6 五文件固定集合 `208 passed`。

#### 已修正语义

原实现只要 TestResult 绑定 Commit 就写 `validated_by` edge，即使 status=failed。V2
现已实现：

```text
注册 CONTAINS + role=has_test_result → 事实归属
唯一一致 observed passed + exit_code=0 → VALIDATED_BY
一致 observed failed/error/nonzero → FAILED_VALIDATION
reported-only / missing exit / status contradiction → observation only
```

只有 status/exit/reported status 一致时才允许 validation edge；reported/observed
矛盾、缺失 exit code 或仅 reported 的结果只保留 CONTAINS、diagnostic 与观察事实，
不得产生 `VALIDATED_BY` 或 `FAILED_VALIDATION`。

#### 扩展 TestResult

- selector/test cases；
- environment ref；
- target dirty manifest；
- stdout/stderr RawObject；
- framework parser version；
- coverage artifact/ref；
- observed/reported；
- status consistency。

#### Test→Symbol

优先：

1. coverage line/function；
2. exact test selector/target；
3. SCIP/import/call；
4. path/name heuristic candidate。

coverage function/line 与 exact selector/target 可 confirmed 并产生注册
`COVERS`/`TESTS`；SCIP/import/call/path-name 等弱证据只作 review-required candidate，
不物化弱证据边，也不宣称 coverage。

historical TestResult 只作 historical truth；wrong version/scope/generation/dirty
manifest fail closed；无 TestResult 时明确 `MISSING_CONTEXT`。

#### 验收

- False Validation Rate=0；
- test result 只适用 exact target version；
- current change 后旧测试标 historical；
- failed/passed/error fixtures；
- dirty manifest mismatch；
- ACL；
- no TestResult 时 Context 明确 missing。

## 14. C7：Context、集成与发布

状态：C7-01/C7-02/C7-03 engineering PASS / COMPLETE；C7 overall engineering
COMPLETE；Release decision: HOLD_DEFAULT_V1 / NOT RELEASED / default V1

### C7-01：Task-specific Code Context Builder

状态：engineering PASS / COMPLETE  
依赖：C4-03、C6-03

[最终 C7 Gate](reviews/08_CODE_C7_GATE_REVIEW.md) 第二轮已关闭首轮 6 个 P1，最终
P0/P1=`0`，并直接复跑固定八文件 `363 passed`；主控与独立 Gate 均为 PASS。

#### 实际功能

- 四个冻结模板：`IMPLEMENTATION`、`BUG_LOCALIZATION`、`IMPACT_ANALYSIS`、
  `CHANGE_CONTEXT`；
- RetrievalContext 保留原 candidate/rank/channel/relation path/fusion trace，
  ComprehensionContext 只从已证明的 `CodeSourceResult.context_blocks` 装配原文，
  不重新执行 retrieval，也不从 metadata/summary 造正文；
- stable citation、关系路径解释、确定性 role/budget、整行 Unicode 截断，以及明确的
  refusal/missing/pruned/distractor trace；
- C7-01 本身只交付 builder contract；runtime/API/Platform 接线由 C7-02 完成，但这
  仍不等于默认切换或发布。

#### 首轮 6 P1 关闭证据

1. production fusion 对 project/repository/ref/ACL/index/watermark/version/generation/
   paths、candidate/context manifest 与完整 result digest 签发模块私有
   HMAC-SHA256 scope/publication attestation；builder 拒绝裸结果或篡改/replay；
2. publication content manifest 绑定 block metadata、正文 SHA-256 与 canonical block
   identity，正文或 metadata 替换 fail closed；
3. child/citation dedup 只有 candidate、source block、正文、locator、entity/unit、
   roles 与 citation provenance 全部等价才折叠，不再串绑角色或静默丢正文；
4. clean/dirty stable version 使用严格 lowercase 40/64-hex full-SHA 与
   `<base>+dirty.<manifest>` grammar，畸形 dirty fail closed；
5. locator 要求 canonical NFC IRI/唯一 fragment/owner-ref-path round trip，未截断正文
   行数必须等于 exact span，截断只按完整行收窄 locator；
6. metrics 使用 `AVAILABLE / UNAVAILABLE + value + reason` 三态；label-dependent
   precision/recall/answer utility 保持 unavailable，role/token/locator 只从实际选中
   blocks 与 rendered locator 计算，无正文不再产生满分质量指标。

#### 模板

**Implementation**

```text
Target Symbol
Signature/Doc
Relevant AST blocks
Required imports/types
Direct callers/callees
Related tests
Version/locator
```

**Bug localization**

```text
Error/stack
Suspect target
Graph path
Relevant block
Recent diff
Failed/passed validation
Alternative suspects
```

**Impact**

```text
Changed Symbol
Incoming references/calls
Implementations/overrides
Tests/coverage
Affected files/modules
Unresolved/unknown
```

**Modification**

```text
Editable target
Contract/type/parent
Callers/dependencies
Nearby tests
Recent diff
Validation requirement
Do-not-edit/generated
```

#### 规则

- Retrieval Context 与 Comprehension Context 分开；
- parent content 只出现一次；
- source code 原文不可被 summary 替代；
- Unit 命中映射 stable entity Citation；
- 版本/dirty 显示；
- path explanation；
- unresolved/missing；
- token budget；
- 不跨 ACL。

#### Evaluation

- Context Precision/Recall；
- required role coverage；
- duplicate/distractor；
- token efficiency；
- locator correctness；
- optional answer utility。

### C7-02：Platform Opt-in Integration

状态：engineering PASS / COMPLETE  
依赖：C7-01

[最终 C7 Gate](reviews/08_CODE_C7_GATE_REVIEW.md) 的 C7-02 第二轮复审（Gate task
`019fa8fd-4d83-7022-8a45-f68610db1a11`）已关闭首轮唯一 P1，最终 P0/P1=`0`，并
授权 C7-03。

#### 实际接入

1. direct Code API 与 `PlatformService` 共享同一 `CodePlatformIntegration`；
2. 默认与显式 v1 均只执行一次 legacy；显式 v1/v2 header 非法值返回 422；
3. header 优先于 setting/canary；无 override 时使用稳定 canary bucket，但默认仍为
   V1；
4. 只有 single repository + exact full commit + 唯一 publication/index/watermark/
   generation/ACL scope 才进入 governed production V2，并验证 attestation 后调用
   builder；
5. V2 保留 V1 主字段投影；error/unavailable 使用固定清洗 reason 且只 fallback 一次；
6. shadow 复用同一次 legacy response，同时运行真实 production V2，不重复 legacy，
   也不改变用户响应；
7. Platform `source=code` 与 code + document mixed-source/global 投影已通过。

#### 首轮 1 P1 关闭证据

- 首轮发现 project-only 或显式多 repository scope 可在未唯一解析时进入 V2；
- 修复后 routing 在调用 V2 pipeline 前要求 project/repository/ref/publication/ACL
  全部唯一；歧义或缺失固定 `scope_unavailable`，V2 pipeline 为 0 次且 legacy 恰好
  1 次；
- direct 与 Platform 双仓隔离 probe 均验证上述 fail-closed；同一隔离实例的单仓
  exact-commit 请求仍进入一次 attested V2，无额外 legacy 调用。

#### Gate 证据

- 固定八文件最终 `227 passed`；
- C1-C7 扩展集合 `511 passed`；
- Ruff/format/diff/compile 等静态检查 PASS；
- 没有创建或修改 quality Run，没有默认切换，也没有 C7 release。

#### API

保持：

- V1 result fields；
- stable entity/citation；
- `source="code"`；
- scope/ACL；
- existing endpoints。

新增仅在 trace/opt-in：

- source status；
- retrieval unit；
- profile/task；
- channel ranks；
- relation paths；
- calibration；
- builder/index/model versions；
- fallback。

#### Compatibility Tests

- golden response schema；
- V1 client；
- empty/no result；
- include edges；
- commit scope；
- language/entity filters；
- ACL；
- Platform/global search；
- UnifiedQuery roles；
- citation map。

### C7-03：Shadow、Canary 与 Release

状态：engineering PASS / COMPLETE  
依赖：C7-02

[最终 C7 Gate](reviews/08_CODE_C7_GATE_REVIEW.md) 的 C7-03 第二轮复审（Gate task
`019fa8fd-4d83-7022-8a45-f68610db1a11`）已关闭首轮 3 个 P1，最终 P0/P1=`0`；
C7 overall engineering `COMPLETE`。

#### 实际能力与 3 P1 关闭证据

1. current release evidence 使用 C-B6 exact immutable URI
   `evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e`，并保持
   `PROVISIONAL_NOT_QUALIFIED` / unavailable，不能冒充 release-qualified artifact；
2. rollback severity 由必填、可信的 `deployed_stage` 决定，不再信任 candidate stage；
   OFFLINE/SHADOW 的 tamper/timeout 保持 HOLD，CANARY 及以上返回
   `ROLLBACK_REQUIRED`，invalid trusted authority 也保守 rollback；
3. metric、guardrail、artifact、latency、cost、shadow 等 child evidence 同时绑定
   stage 与 parent snapshot digest；跨 stage 复制即使重签 top envelope 也 fail closed，
   只有逐阶段重新生成、签发的 TEST chain 能通过 evaluator contract。

#### Gate 证据与发布边界

- C7-03 专项 `41 passed`；
- C7/platform/query/context/contracts 十文件固定集合 `354 passed`；
- Ruff/format/compile/export/trailing-whitespace 等静态检查 PASS；
- 当前 decision 为 `HOLD_DEFAULT_V1`，默认 engine 仍为 V1，`NOT RELEASED`；
- evaluator 只产生 decision/rollback plan；没有 runtime/config/Platform release wiring，
  没有 production canary、actual rollout、default switch、persistent release state，
  也没有执行 config 或 rollback；
- 没有创建或修改新 quality Run，C7 overall engineering COMPLETE 不等于 quality 或
  release。

#### 阶段

```text
offline
→ shadow 100% internal
→ canary 5%
→ canary 25%
→ opt-in 100%
→ default V2
```

#### Gate

采用 [Code Source 设计门槛](../sources/01_CODE_SOURCE_RAG.md#19-发布门槛)：

- Symbol Recall@10 ≥ 0.85；
- File Recall@10 ≥ 0.90；
- MRR@10 ≥ 0.70；
- Required Path Recall ≥ 0.75；
- Exact Commit Accuracy ≥ 0.95；
- Wrong-version ≤ 0.02；
- False Validation=0；
- Locator ≥ 0.98；
- Harmful Candidate@10 ≤ 0.05；
- P95 ≤ 1.5s；
- Unauthorized/Secret Leakage=0。

#### Guardrail

- exact identifier 不回归；
- V1 API compatibility；
- ingest failure 不影响 last-known-good；
- shadow exception rate；
- index size budget；
- model unavailable fallback；
- external semantic index failure fallback；
- ACL/security suite。

#### Rollback

1. `rag_code_engine=v1`；
2. disable reranker；
3. disable graph；
4. switch embedding profile；
5. switch V2 publication pointer；
6.保留数据排查；
7. 不回滚 stable entity generation。

#### Release Evidence

- Code Golden version；
- C-B0~C-B6 Runs；
- selected model/SCIP ADR；
- P50/P95/P99；
- index/ingest cost；
- security report；
- canary report；
- rollback rehearsal；
- interview update。

## 15. Schema Migration 顺序

### Migration 1：Evaluation

- 新增 evaluation profile/judgment/metric tables；
- 不改生产 read；
- rollback：停止 runner。

### Migration 2：Code Unit

- 新增 Unit/FTS/vector/cache/publication；
- active Generation dual build；
- rollback：flag V1；
- cleanup：无。

### Migration 3：Diagnostics/Semantic

- 新增 diagnostics/occurrences/external symbol/index run；
- SCIP off 时空表；
- rollback：disable resolver。

### Migration 4：History/Lineage

- 新增 historical publication/lineage；
- 不改 Git facts；
- rollback：disable historical retriever。

### Migration 5：Validation

- additive Test fields/relations；
- 旧 `validated_by` edge 迁移为 v1 legacy，不用于 V2 truth；
- rollback：V2 verifier off，但不恢复错误语义为 verified。

迁移原则：

- 每个 migration 有 schema version；
- initialize 幂等；
- migration test 从旧 DB copy 执行；
- 大 backfill 使用 workflow，不在服务启动事务内做；
-索引 pointer 原子切换；
-删除/cleanup 单独审批。

## 16. Feature Flag 矩阵

| Flag | Off | Shadow | On | Fallback |
| --- | --- | --- | --- | --- |
| code engine | V1 | V1 answer + V2 trace | V2 | V1 |
| AST units | raw view | dual build | AST | raw |
| dense profile | hash | compare | selected | sparse/hash |
| graph | no candidate | path trace | candidate | no graph |
| SCIP | tree-sitter | build/compare | semantic merge | tree-sitter |
| reranker | RRF | compare | rerank | RRF |
| context | snippet | compare | structured | snippet |

Flag snapshot 写入 Evaluation Run 和 Trace。

## 17. 测试执行矩阵

### Unit

- parser/IR/unit/token；
- exact/tokenizer/FTS；
- embedding/cache；
- score/fusion/calibration；
- graph/path；
- SCIP parser；
- history/lineage；
- test status policy；
- context/dedup。

### Integration

- repository→raw→entity→unit→index→retrieve；
- generation failure/rollback；
- V1/V2 dual；
- current/dirty/history；
- SCIP optional；
- Diff→Symbol→Test；
- Platform adapter；
- Evaluation runner。

### Security

- secret quarantine；
- remote embedding deny；
- ACL lexical/dense/graph/context；
- malicious path/ref；
- SCIP oversized/invalid；
- prompt-like code content；
- trace redaction。

### Performance

- 1.6K current views baseline；
- AST expanded units；
- 10K/50K synthetic units；
- dense batch/full scan；
- high-degree graph；
- P50/P95/P99；
- ingest throughput；
- cache hit；
- DB/index bytes。

### 回归命令

实际实现时至少执行：

```text
pytest tests/test_parser.py
pytest tests/test_ingestion_retrieval.py
pytest tests/test_code_history_refs.py
pytest tests/test_retrieval_quality.py
pytest tests/test_code_rag_*.py
ruff check src tests
```

命令是计划，不代表当前已运行。

## 18. Observability

### Ingest

- discovered/skipped/quarantined；
- parse/unit counts；
- unit token distribution；
- parse fallback；
- unresolved by reason；
- SCIP run status；
- embeddings built/cache hit；
- per-stage duration；
- publication validation；
- active/last-good generation。

### Query

- resolved task/ref/scope；
- channel budgets/candidates；
- exact/sparse/dense ranks；
- graph expanded/pruned；
- reranker/calibration；
- selected roles；
- version alignment；
- context tokens；
- latency per span；
- fallback；
- no-result/missing。

### Dashboard

- per task/language Recall；
- graph-only recovery/noise；
- wrong-version；
- false validation；
- model/calibration drift；
- P95/index bytes；
- shadow disagreement；
- exception/fallback。

## 19. 风险与处置

| 风险 | 信号 | 处置 |
| --- | --- | --- |
| AST Unit 激增 | units/file、index bytes | generated filter、merge、上限 |
| Dense 收益小 | low-overlap Recall 无提升 | 拒绝模型，保留 sparse |
| Reranker 延迟 | P95 超标 | smaller model/top-N/cache/fallback |
| Graph 噪声 | path precision/harmful | edge/task whitelist、beam |
| SCIP 环境失败 | run error/OOM | opt-in、resource cap、fallback |
| 历史索引膨胀 | materialized commits | pin/hot/LRU |
| 测试误验证 | false validation | status/exit/version hard policy |
| ACL 图泄漏 | hidden middle node | pre-expansion ACL |
| Migration 锁库 | startup latency | additive schema、async backfill |
| Dirty version混淆 | wrong-version | exact manifest binding |
| 模型供应变化 | artifact missing | pinned revision、last-good profile |

## 20. Issue 模板

```markdown
# CODE-Cx-yy：标题

Status: NOT_STARTED
Parent milestone:
Depends on:
Feature flag:

## Observable problem

## Scope / Non-goals

## Current code

## Contract / Schema

## Implementation

## Tests

## Evaluation
- Dataset:
- Baseline:
- Treatment:
- Primary metric:
- Guardrails:

## Telemetry

## Rollout / Rollback

## Acceptance

## Interview sync
```

## 21. 开发顺序与每项停止条件

1. **C0 Evaluation**
   - 已完成并通过 Gate；唯一有效 baseline 为 `5a92...`，不因后续 C1 通过而改写。
2. **C1 Contract**
   - 已完成并通过 Gate；V1 等价、执行证据、flag-off 零工作与 bounded shadow 已关闭，
     但不产生检索质量提升结论。
3. **C2 AST/exact/sparse**
   - 工程实现与 Gate 已完成；三次真实 C-B1 依次暴露 entity projection、same-entity
     child 拥挤与剩余语义/locator 缺口，最终仍未 qualified，因此不声明质量提升。
4. **C3 Dense/rerank**
   - C3-01/02/03 engineering 已完成。C-B2 的 local-hash dense 恢复 4/10
     zero-result，但 locator `0.5682`；C-B5 locator `0.636364`，仍比 C-B0 低
     `0.204545`，且 calibration 只有 65 observations、仍为 provisional。两个
     treatment 均按停止条件拒绝 qualification；learned embedding/cross-encoder 未运行。
5. **C4 Graph**
   - C4-01/02/03 与 C4 overall engineering COMPLETE；8 profiles、fusion pipeline、
     exact fast path、graph/hooks、rerank/calibration/adaptive-k 和 strict trace 已通过
     工程 Gate。但 C-B3 Required Path Recall/Precision、accepted paths 与 graph-only
     recovery 均为 0，且 P95 增加 `121.7 ms`，所以 quality 仍 NOT QUALIFIED。
6. **C5 SCIP**
   - C5-01/C5-02 engineering PASS，C5 overall engineering COMPLETE；runner 默认 off，
     本轮未下载/联网/实际执行 indexer。唯一 C-B4 verified，但 0 labels 使质量指标全部
     UNAVAILABLE，最终 NOT QUALIFIED；42/42 与三臂耗时/内存只作结构/性能证据。
7. **C6 History/Test**
   - C6-01 engineering PASS；C6-02/C6-03 engineering PASS / COMPLETE；C6 overall
     engineering COMPLETE；C-B6 verified 但 `PROVISIONAL NOT QUALIFIED`。C6-03
     120 组 truth probe False Validation=`0`。
8. **C7 Context/Release**
   - C7-01 engineering PASS / COMPLETE，首轮 6 P1 已关闭且固定回归 363 passed；
     C7-02 engineering PASS / COMPLETE，首轮 1 P1 已关闭，固定八文件 227 与 C1-C7
     扩展 511 passed；C7-03 engineering PASS / COMPLETE，首轮 3 P1 已关闭，专项 41
     与十文件 354 passed；C7 overall engineering COMPLETE，但 release decision 为
     `HOLD_DEFAULT_V1`，默认仍为 V1 且 `NOT RELEASED`。

## 22. 面试证据同步点

| Milestone | 面试材料更新 |
| --- | --- |
| C0 | Golden/Baseline Run、真实失败 Top 3 |
| C1 | contract/adapter/shadow 代码入口、兼容/安全/并发 Gate 与 209/338 测试证据 |
| C2 | **已同步：工程 Gate PASS；三次 C-B1 审计均 NOT QUALIFIED，不宣称总体提升** |
| C3 | **已同步：C3 engineering COMPLETE；C-B2/C-B5 verified 33/33 但均 NOT QUALIFIED；同步 profile/cache/dense/reranker/calibration/fallback/trace 与真实负向 latency** |
| C4 | **已同步：C4-01/02/03 与 C4 overall engineering COMPLETE；C-B3 四臂各 33 case、verified 但 NOT QUALIFIED**；同步 8 profiles/fusion/fast path/hooks/rerank/calibration/adaptive-k/strict trace，并明确 0 path/0 recovery、扫描量不作收益 |
| C5 | **已同步：C5-01/C5-02 engineering PASS；C5 overall engineering COMPLETE；C-B4 verified 但 NOT QUALIFIED**；0 labels 与 42/42 non-quality structural coverage 边界已同步 |
| C6 | **已同步：C6-01/C6-02/C6-03 engineering PASS；C6 overall engineering COMPLETE；C-B6 verified 但 PROVISIONAL NOT QUALIFIED**；同步 False Validation=0 与 Test→Symbol 证据等级 |
| C7 | **已同步：C7-01/C7-02/C7-03 engineering PASS / COMPLETE；C7 overall engineering COMPLETE**；C7-01 关闭 6 P1 / 363，C7-02 关闭 1 P1 / 227 / 511，C7-03 关闭 3 P1 / 41 / 354；`HOLD_DEFAULT_V1` / `NOT RELEASED` |

实现/工程 Gate 证据与质量 Run 分开同步：C2/C3/C4-01/C4-02/C4-03/C5-01/C5-02
可凭工程 Gate 移到 `CURRENT_IMPLEMENTED`，但 C-B1/C-B2/C-B3/C-B4/C-B5 失败 Run
只能作为审计证据；任何优化收益仍必须等待 qualified treatment/ablation Run。

## 23. Code Source 开发方案完成定义

本开发方案文档完成需满足：

- 当前实现、正式库历史快照与外部服务可变边界已核对；
- 24 个建议 PR 有依赖和边界；
- Evaluation-first；
- stable entity/V1 API 兼容；
- additive migration；
- AST/exact/sparse/dense/rerank/graph/SCIP/history/test/context 均有开发项；
- Python-first 语义解析有现实依据；
- 模型通过 benchmark 选择而非拍板；
- 每阶段 tests/evaluation/flag/rollback；
- 单源门槛与停止条件；
- 面试同步点；
- 未提前展开 Codex 开发方案。

本方案整体为 `IMPLEMENTATION_IN_PROGRESS`；C0、C1 已通过，C2 engineering 已实现并
通过 Gate，但三次 C-B1 均未 qualified。C3-01/02/03 engineering 已完成，C-B2/C-B5
均 verified 33/33 但 NOT QUALIFIED；C4-01/02/03 与 C4 overall engineering
COMPLETE，四臂 C-B3 已 verified 33/33 但 NOT QUALIFIED。C5-01/C5-02 engineering
PASS，C5 overall engineering COMPLETE；唯一 C-B4 production audit 已 verified，但
0 labels 使质量指标全部 UNAVAILABLE，最终 NOT QUALIFIED。C6-01/C6-02/C6-03
engineering PASS，C6 overall engineering COMPLETE；C-B6 artifact verified 但
`PROVISIONAL NOT QUALIFIED`。C7-01/C7-02/C7-03 engineering PASS / COMPLETE，C7
overall engineering COMPLETE；release decision 为 `HOLD_DEFAULT_V1`，用户可见默认
检索仍为 V1，Code RAG V2 `NOT RELEASED`。没有可声明的整体优化收益、Graph Recall
或 semantic quality 提升。

## 24. 实施选型的一手资料

- [SCIP Protocol](https://github.com/scip-code/scip)：语言无关的 Code Intelligence
  Protocol，作为 definition/reference/implementation 等语义事实的交换格式；
- [scip-python](https://github.com/sourcegraph/scip-python)：Python 语义索引器候选，
  safe runner 仅允许 pinned、opt-in、resource-capped 执行；C5 Gate 未下载、联网或实际
  执行该 indexer；
- [scip-typescript](https://github.com/sourcegraph/scip-typescript)：JS/TS 后续候选；
  当前 JavaScript/TypeScript 均为 `UNAVAILABLE`；
- [Qwen3 Embedding 官方说明](https://qwenlm.github.io/blog/qwen3-embedding/)：dense 与
  reranker 候选之一；最终是否采用只由 C3 benchmark、延迟与部署约束决定。
