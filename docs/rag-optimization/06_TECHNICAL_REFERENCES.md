# 技术依据与选型映射

## 1. 使用原则

这些论文用于提出候选设计和实验假设，不代表其公开收益会在本项目复现。最终是否采用，
由项目 Golden Set 的消融、延迟、成本和安全约束决定。

## 2. Code RAG

### cAST：AST 递归结构化切块

- 论文：[cAST: Enhancing Code Retrieval-Augmented Generation with Structural Chunking via Abstract Syntax Tree](https://aclanthology.org/2025.findings-emnlp.430/)
- 启发：按 AST 递归切分，同时保留结构边界。
- 项目映射：`CodeSymbol` 保持稳定引用；新增可重建 `AST Retrieval Unit`。
- 需要验证：不同语言、长函数、配置文件的 Recall、重复率和索引成本。

### Late Code Chunking：检索上下文与理解上下文分离

- 论文：[Late Code Chunking: A Code Chunking Strategy for Repository-Level Code Completion](https://aclanthology.org/2026.acl-short.64/)
- 启发：用适合相似度检索的表示召回，再做面向代码理解的后置上下文增强。
- 项目映射：AST block 命中后补 signature、父作用域、类型、caller/callee 和 test。
- 需要验证：late enrichment 是否改善答案，同时控制 token。

### DraCo：数据流引导的仓库级上下文

- 论文：[DraCo: Dataflow-Guided Retrieval Augmentation for Repository-Level Code Completion](https://arxiv.org/abs/2405.19782)
- 启发：数据流和跨文件依赖比纯语义相似更适合部分仓库级任务。
- 项目映射：补充 READS/WRITES/FLOWS_TO/TYPE_OF，并作为任务化图模板。
- 风险：多语言静态分析成本和不完整类型信息。

### RepoCoder：迭代检索—生成

- 论文：[RepoCoder: Repository-Level Code Completion Through Iterative Retrieval and Generation](https://arxiv.org/abs/2303.12570)
- 启发：初次生成或理解结果可以反向改进下一轮查询。
- 项目映射：只在 required role/路径缺失时启用最多一轮 Code corrective retrieval。
- 风险：无限迭代、延迟和错误自强化。

### Repoformer：选择性检索

- 论文：[Repoformer: Selective Retrieval for Repository-Level Code Completion](https://arxiv.org/abs/2403.10059)
- 启发：不是每个代码问题都需要昂贵仓库召回。
- 项目映射：Planner 把精确符号问题路由为浅检索，把影响分析路由为图检索。
- 需要验证：检索触发准确率与节省的 P95/成本。

### CodeRAG-Bench：代码检索诊断

- 论文：[CodeRAG-Bench: Can Retrieval Augment Code Generation?](https://aclanthology.org/2025.findings-naacl.176/)
- 启发：代码检索在低词面重合场景困难，生成模型也可能不能正确利用检索上下文。
- 项目映射：Golden Set 单独设置低词面重合和 context utilization 切片。

### CodeRAG：多路径召回和生成偏好重排

- 论文：[CodeRAG: Finding Relevant and Necessary Knowledge for Retrieval-Augmented Repository-Level Code Completion](https://aclanthology.org/2025.emnlp-main.1187/)
- 启发：query construction、多路径 retrieval 和与生成需求对齐的 rerank 需要联合考虑。
- 项目映射：identifier/sparse/dense/graph/history 多路 union + code reranker。

### CoRet：代码语义、仓库结构和调用依赖

- 论文：[CoRet: A Code Retriever with Repository Structure for Bug Localization](https://aclanthology.org/2025.acl-short.62/)
- 启发：Bug 定位不能只依靠代码文本相似度。
- 项目映射：单独建立 Bug localization task profile 和 call/dependency path 指标。

### RepoGraph：仓库代码图作为软件工程 Agent 插件

- 论文：[RepoGraph: Enhancing AI Software Engineering with Repository-level Code Graph](https://openreview.net/forum?id=dw9VUsSHGB)
- 启发：代码图可作为现有软件工程流程的可插拔上下文源。
- 项目映射：GraphRetriever 是 Source Retriever 的候选通道，不把整个系统绑定到图数据库。

## 3. Query 路由与自适应检索

### Adaptive-RAG

- 论文：[Adaptive-RAG: Learning to Adapt Retrieval-Augmented Large Language Models through Question Complexity](https://aclanthology.org/2024.naacl-long.389/)
- 启发：不同复杂度问题选择 no-retrieval、single-step 或 multi-step。
- 项目映射：Planner 的 simple/single-source/multi-source/multi-hop 四档。
- 差异：本项目第一版以规则和显式 Scope 为主，数据足够后再训练路由器。

### MBA-RAG

- 论文：[MBA-RAG: a Bandit Approach for Adaptive Retrieval-Augmented Generation through Question Complexity](https://aclanthology.org/2025.coling-main.218/)
- 启发：路由需要同时优化准确率和检索成本。
- 项目映射：Trace 收集 route reward；后期可评审 bandit，不作为 P0。

### Adaptive-k

- 论文：[Efficient Context Selection for Long-Context QA: No Tuning, No Iteration, Just Adaptive-k](https://aclanthology.org/2025.emnlp-main.1017/)
- 启发：固定 K 容易浪费上下文或遗漏证据。
- 项目映射：按校准相关概率、required roles 和边际效用停止选择。

## 4. Graph 与跨文档推理

### Microsoft GraphRAG

- 论文：[From Local to Global: A Graph RAG Approach to Query-Focused Summarization](https://www.microsoft.com/en-us/research/publication/from-local-to-global-a-graph-rag-approach-to-query-focused-summarization/)
- 启发：全局总结和局部事实查询需要不同的图/摘要策略。
- 项目映射：Document RAG 的 global summary 与 local Claim/Table 检索分开。
- 不直接照搬：本项目代码和实验已有确定性结构，不应全部通过 LLM 抽取实体图。

### HippoRAG

- 论文：[HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models](https://proceedings.neurips.cc/paper_files/paper/2024/hash/6ddc001d07ca4f319af96a3024f6dbd1-Abstract-Conference.html)
- 启发：图上的关联和多跳检索可以补充孤立 passage similarity。
- 项目映射：以高相关实体作为 seed，使用受类型约束的路径扩展。
- 差异：本项目优先使用领域确定性关系，不让开放式实体抽取替代版本化事实。

### MultiRAG

- 论文：[MultiRAG: A Knowledge-guided Framework for Mitigating Hallucination in Multi-source Retrieval Augmented Generation](https://arxiv.org/abs/2508.03553)
- 启发：多源系统必须建模跨源逻辑关系和来源/节点可靠性。
- 项目映射：关系 derivation、review status、source authority 和 node relevance 分离。
- 注意：只作为候选思路，需在项目证据链场景独立验证。

### 来源可靠性估计

- 论文：[Retrieval-Augmented Generation with Estimation of Source Reliability](https://aclanthology.org/2025.emnlp-main.1738/)
- 启发：多来源不能默认同等可靠。
- 项目映射：`source_authority` 与 `retrieval_relevance` 分开；权威度依赖 intent。

## 5. 文档结构与上下文

### Landmark Embedding

- 论文：[Landmark Embedding: A Chunking-Free Embedding Method for Retrieval Augmented Long-Context Large Language Models](https://aclanthology.org/2024.acl-long.180/)
- 启发：切块可能破坏长文语义，检索单元应保留全局位置和父上下文。
- 项目映射：Document Retrieval Unit 保存 heading path 和 parent context ref。

### RAG 最佳实践系统分析

- 论文：[Searching for Best Practices in Retrieval-Augmented Generation](https://aclanthology.org/2024.emnlp-main.981/)
- 启发：chunk、embedding、query rewrite、rerank 和 generation 需要做组合消融。
- 项目映射：实施路线要求每次只改变一个主要变量。

## 6. Embedding、Sparse 与 Reranker 候选

### Qwen3 Embedding / Reranker

- 报告：[Qwen3 Embedding: Advancing Text Embedding and Reranking Through Foundation Models](https://arxiv.org/abs/2506.05176)
- 启发：同一系列提供 0.6B、4B、8B 的 embedding 和 reranker，覆盖多语言、跨语言和代码
  检索，可用于构建不同成本档位。
- 项目映射：0.6B 作为本地通用强基线，4B 作为效果上界候选；必须单独测中文研发问题和
  代码 slice。

### BGE-M3

- 论文：[M3-Embedding: Multi-Linguality, Multi-Functionality, Multi-Granularity](https://aclanthology.org/2024.findings-acl.137/)
- 启发：一个模型支持 dense、sparse 和 multi-vector 表示，适合做多通道消融。
- 项目映射：作为 multilingual hybrid baseline；是否上线取决于索引复杂度和实际收益。

### Code-specific Embedding

- 官方技术说明：[Jina Code Embeddings: Code Retrieval at 0.5B and 1.5B](https://jina.ai/news/jina-code-embeddings-sota-code-retrieval-at-0-5b-and-1-5b/)
- 启发：代码检索可使用从代码生成模型构建的专用 embedding，而不是默认沿用通用文本模型。
- 项目映射：进入 Code Golden Set 候选；重点验证自然语言→代码、同名 hard negative、
  中文查询和多语言代码。

### Late Interaction

- 论文：[ColBERTv2: Effective and Efficient Retrieval via Lightweight Late Interaction](https://aclanthology.org/2022.naacl-main.272/)
- 多语言候选：[Jina-ColBERT-v2: A General-Purpose Multilingual Late Interaction Retriever](https://aclanthology.org/2024.mrl-1.11/)
- 启发：token-level late interaction 能保留比单向量更细的匹配信号。
- 项目映射：只有 bi-encoder + cross-encoder 仍无法满足召回或延迟目标时才进入 P1 实验，
  不作为 P0 基础设施前提。

## 7. 评测

### RAGChecker

- 论文：[RAGChecker: A Fine-grained Framework for Diagnosing Retrieval-Augmented Generation](https://arxiv.org/abs/2408.08067)
- 启发：分别诊断 retrieval 和 generation，而不是只看最终答案。
- 项目映射：Retrieval、Graph/Evidence、Context、Answer、System 五层指标。

### RAGAS

- 论文：[RAGAs: Automated Evaluation of Retrieval Augmented Generation](https://aclanthology.org/2024.eacl-demo.16/)
- 启发：context relevance、faithfulness 和 answer relevance 可以自动化辅助评测。
- 项目映射：作为语义指标补充，不替代版本、数值、路径和 ACL 的确定性指标。

### ARES

- 论文：[ARES: An Automated Evaluation Framework for Retrieval-Augmented Generation Systems](https://aclanthology.org/2024.naacl-long.20/)
- 启发：少量人工标注可用于校准自动 Judge，需关注领域迁移。
- 项目映射：Judge 抽样人工校准和版本化。

### UAEval4RAG

- 论文：[Unanswerability Evaluation for Retrieval Augmented Generation](https://aclanthology.org/2025.acl-long.415/)
- 启发：RAG 评测不能只覆盖可回答问题。
- 项目映射：建立 knowledge missing、wrong scope、unavailable、unauthorized、conflict、
  missing validation、underspecified 等拒答切片。

### RAGVUE

- 论文：[RAGVUE: A Diagnostic View for Explainable and Automated Evaluation of Retrieval-Augmented Generation](https://aclanthology.org/2026.eacl-demo.35/)
- 启发：把 retrieval、answer completeness、claim faithfulness 和 Judge 校准分别解释。
- 项目映射：Trace 与评测结果都必须能定位模块级失败。

### 面向 LLM 消费者的 Retrieval Utility

- 论文：[Redefining Retrieval Evaluation in the Era of LLMs](https://aclanthology.org/2026.eacl-long.391/)
- 启发：传统 rank metrics 不衡量某些 passage 对生成的负面干扰。
- 项目映射：Golden Candidate 标注 `-1 harmful/distracting`，增加 distractor ratio 和
  context utility 指标。

## 8. 本项目的组合创新边界

项目不声称发明 AST chunk、RRF、GraphRAG 或自动 RAG 评测。可成立的工程贡献是：

1. 将这些方法按研发证据源的不同语义边界组合，而不是使用统一文档 RAG；
2. 保留稳定实体、版本、ACL、relation derivation 和人工复核；
3. 用 required roles 和证据路径约束异构多源融合；
4. 把代码、会话、实验、Notebook、Document Claim 和 Workspace Requirement 连接成
   可验证、可追溯到 root provenance 的研发证据链；
5. 通过分阶段 Golden Set 和消融证明每一层的实际价值。
