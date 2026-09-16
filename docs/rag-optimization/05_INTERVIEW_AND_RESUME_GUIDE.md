# 多源 RAG 项目简历与面试指南

> **当前真值（2026-07-29）**
>
> 原始路线图要求的仓库内工程已经完成并通过隔离测试；详细状态见
> [原始路线图实施矩阵](development/05_ORIGINAL_ROADMAP_IMPLEMENTATION_MATRIX.md)。
> 这只允许描述 contracts、Golden/evaluator、隔离持久化、专用检索、联合 planner/
> fusion/graph/Evidence Pack、安全与 release evaluator 已实现。现有真实质量证据仍是
> `HOLD / NON_QUALIFIED / UNAVAILABLE`，没有 production 多源 replay、远程模型/ANN
> benchmark、shadow/canary 或默认 V2 发布，所以本文所有收益占位符仍不得填写或推断。

## 1. 使用规则

这份文档从设计阶段开始维护，但所有数字默认是占位符。只有同时具备以下材料时才能写入
简历：

- Golden Dataset 版本；
- baseline Evaluation Run；
- treatment Evaluation Run；
- 代码 commit；
- 模型/索引/Prompt 版本；
- 可解释的评测切片；
- 回归与失败案例。

论文中的提升、公开榜单分数和设计目标不能写成本项目收益。

本文件只提供统一表达框架。各来源的当前数据、详细设计、失败实验、指标与简历 bullet
以同步文档为准：

- [Code](interview/01_CODE_SOURCE_INTERVIEW.md)
- [Codex](interview/02_CODEX_SOURCE_INTERVIEW.md)
- [Experiment](interview/03_EXPERIMENT_SOURCE_INTERVIEW.md)
- [Notebook](interview/04_NOTEBOOK_SOURCE_INTERVIEW.md)
- [Document](interview/05_DOCUMENT_SOURCE_INTERVIEW.md)
- [Workspace](interview/06_WORKSPACE_SOURCE_INTERVIEW.md)
- [六源联合](interview/07_MULTISOURCE_INTERVIEW.md)

## 2. 项目定位

### 2.1 一句话版本

> 设计并实现面向研发知识的多源证据 RAG，将代码仓库、研发会话、实验、Notebook 和
> 科研文档统一为版本化证据图，并通过单源专用检索、异构分数校准、证据角色约束和
> 引用校验生成可追溯回答。

### 2.2 30 秒版本

> 普通 RAG 通常把所有内容切块后放进同一个向量库，但这个项目的数据源差异很大：
> 代码需要 AST 和调用图，研发会话需要目标—Patch—验证的时序链，实验需要数值、配置和
> 数据版本校验，Notebook 需要 Cell 执行顺序和 stale output，文档需要 Claim、表格和
> 页面定位，Workspace 需要当前/as-of 状态。我先分别优化六类单源 Retriever，
> 再把来源内分数校准为可比较的相关概率，通过 Query Planner 和 required roles 做多源
> 融合，最后形成带版本、反证、缺失项和 Citation 的 Evidence Pack。

### 2.3 2 分钟版本

> 项目最初已经有稳定实体、Generation、ACL 和基础 FTS/本地向量，但检索是固定权重，
> Graph 主要在 Top-K 后展示，实验和文档也没有来源专用检索。我把优化拆成三层。
>
> 第一层是单源最优。代码使用稳定 Symbol 加可重建 AST Retrieval Unit，结合标识符、
> sparse、代码向量和类型化 Graph traversal；Codex 会话按 Episode 建模 Goal、Decision、
> Patch、Command 和真实 Validation；实验使用安全的结构化 Filter 和 Run 可比性检查；
> 文档使用 Claim、Section、TableCell、Figure 的层级多表示检索。
>
> 第二层是多源联合。不同来源的 BM25、余弦和 SQL 分数不直接相加，而是在来源内经过
> rerank 和校准。Query Planner 解析 required roles、版本和来源预算，融合层最大化角色
> 覆盖、版本一致性和证据路径完整性，再沿 Claim→Run→Commit→Code→Validation 等模板
> 补全没有查询词但必需的证据。
>
> 第三层是生成与工程闭环。生成模型只消费经过冲突和版本裁决的 Evidence Pack，回答后
> 校验 claim-citation；系统同时维护分阶段 Golden Set、Trace、P95、成本、ACL 和拒答
> 指标，所有模块都可以 shadow、canary 和独立回滚。

## 3. 简历 Bullet 模板

以下 `[ ]` 必须替换为真实结果。

### 3.1 总体架构

> 主导研发证据多源 RAG 架构，将 Code、Codex、Experiment、Notebook、Document 和
> Workspace 六类异构来源抽象为版本化实体与证据图，设计 Source Retriever、
> Query Planner、校准融合和 Evidence Pack 分层链路，支持 `[N]` 类意图与跨源引用回溯。

### 3.2 Code Graph RAG

> 设计 AST-aware Code RAG，将稳定 CodeSymbol 与递归 AST Retrieval Unit 分离，融合
> identifier、sparse、code embedding 和 typed graph traversal，并通过代码专用 reranker
> 组装 caller/callee/test/diff 上下文；在 `[golden version]` 上将 Symbol Recall@10 从
> `[A]` 提升至 `[B]`，Required Path Recall 从 `[C]` 提升至 `[D]`。

### 3.3 多源校准融合

> 解决 BM25、dense cosine、图路径与 SQL exact-match 分数不可比问题：对六类来源分别
> 进行 relevance calibration，使用 required-role marginal utility、source authority、
> version alignment 和 redundancy penalty 联合选证；跨源 Role Coverage 从 `[A]` 提升至
> `[B]`，Wrong-version Rate 降至 `[C]`。

### 3.4 证据图与冲突

> 构建带 derivation、confidence、review status、version 和 ACL 的类型化证据图，使用
> 受限 beam traversal 补全 Claim→Run→Commit→Code→Validation 路径，并区分确定性事实、
> 人工确认、未复核候选和语义邻近；Evidence Path Recall 达到 `[A]`，Conflict Recall
> 达到 `[B]`。

### 3.5 评测与工程化

> 建立 `[N]` 条版本化 Golden Query 和阶段化评测，覆盖 retrieval、path、version、
> citation、unanswerable、ACL 与 latency；实现 query trace、shadow/canary 和请求级回滚，
> 将多源检索 P95 从 `[A]` 降至 `[B]`，Unauthorized Evidence Leakage 保持为 0。

## 4. 六个核心技术亮点

### 4.1 Typed Code Graph RAG

#### 问题

纯向量检索无法稳定发现低词面重合的 caller、implementation、test 和配置依赖；当前简单
名称解析图覆盖率也不足。

#### 设计

- 稳定 Symbol 与 AST Retrieval Unit 分离；
- exact/sparse/dense/graph 多路召回；
- SCIP/LSP/编译器补充类型边；
- 按 Bug 定位、影响分析、代码修改选择不同路径模板；
- 路径分考虑 seed relevance、edge confidence、hop decay 和 version。

#### 面试追问

**为什么不直接把整个仓库放进长上下文？**

长上下文仍受干扰、顺序和成本影响；仓库级任务需要精确版本、符号和依赖路径。RAG 可以
先找到高效候选，再把命中 Symbol 的理解上下文后置扩展。项目会用相同 Golden Set 对比
长上下文、flat RAG 和 graph RAG，而不是假定 Graph 一定更好。

**为什么不直接使用 Neo4j？**

当前主要瓶颈是边质量和检索策略，不是图存储吞吐。实体和边规模仍适合 SQLite；先抽象
GraphRetriever 和路径算法，达到规模/并发阈值再评审图数据库，避免基础设施迁移替代算法
优化。

**静态 CALLS 可靠吗？**

它是保守静态证据，不等于运行时调用。每条边保留 derivation 和 confidence；简单
Tree-sitter 边、SCIP/LSP 边、coverage 动态边分层处理，未解析或多态目标不伪造唯一调用。

### 4.2 Codex Temporal RAG

#### 问题

把会话当聊天文本检索会混淆计划、执行、助手陈述和真实验证。

#### 设计

- Thread→Episode→Item 层级；
- Goal、Decision、Patch、Command、Validation 状态图；
- 目标相关性 + 时间距离 + 事实状态 rerank；
- Patch created、committed、validated 明确分离；
- 只把真实退出码固化为验证事实。

#### 面试追问

**Episode 如何切分？**

使用 Goal 切换、文件集合变化、命令阶段、时间间隔和 Turn 状态的确定性信号；LLM 只生成
可重建摘要，不能改变原始事件。边界算法版本化，可由人工修订并保留审计。

### 4.3 Heterogeneous Score Calibration

#### 问题

BM25、余弦、图路径和 SQL 分值不在同一尺度，直接加权既不可解释也容易被某来源分布支配。

#### 设计

1. 来源内 candidate union；
2. 来源专用 reranker；
3. 在 Golden Query 上校准为 relevance probability；
4. 联合层再考虑 role、authority、version、status 和 redundancy。

#### 面试追问

**为什么不直接用 RRF？**

RRF 很适合没有标注时做来源内 rank fusion，但它只利用排名，不表达不同来源的概率和
事实权威，也不能解决 required roles。项目先用 RRF 建候选，积累标注后增加来源校准和
role-aware selection。

**authority 和 relevance 有什么区别？**

Codex 会话可能与“当前实现”高度相关，但当前代码才是实现事实的权威来源。相关性决定
候选是否回答问题，authority 决定在冲突时该来源对该 intent 的证据权重，两者不能混为
一个分数。

### 4.4 Evidence-role Constrained Fusion

#### 问题

全局 Top-K 可能全部是相似代码，却缺少 Run、DatasetVersion 或验证，导致回答看似丰富但
证据不完整。

#### 设计

Query Planner 先给出 required roles，候选选择最大化边际角色覆盖和路径闭合，而不是
“每个来源强塞一条”。缺失角色触发一次定向 corrective retrieval，仍缺失则拒答。

#### 面试追问

**这和普通 source diversification 有什么区别？**

多样化按来源分散结果，role-aware fusion 按回答需要的证据职责选择结果。一个
reproduction 查询需要 run、commit、config、dataset、environment，即使其中多个都来自
Experiment，也比无关的六源各一条更正确。

### 4.5 Version-aware Evidence Graph

#### 问题

研发知识高度时变：旧会话、旧代码、旧实验和旧文档都可能语义相关，但不适用于当前版本。

#### 设计

- 每个实体和关系带版本/有效时间；
- Query Scope 先解析 commit/branch/as-of；
- 当前事实、历史事实和版本差异分通道；
- 跨版本 Symbol lineage 和 changed-by 路径；
- 文档 Claim 的 supporting Run commit 与当前 commit 做 drift 检查。

#### 面试追问

**为什么不能只给新数据更高 freshness？**

历史查询的目标就是旧版本；同时较新的会话也可能只是讨论，较旧的 Commit 才是事实。
freshness 只在 intent 要求当前状态时参与，版本匹配和事实状态优先。

### 4.6 Stage-wise Evaluation Flywheel

#### 问题

只看最终答案分数无法判断是 chunk、retriever、reranker、fusion、context 还是 generator
发生变化。

#### 设计

- Retrieval：Recall/MRR/hard negative；
- Graph：path/role/version/conflict；
- Context：precision/recall/duplicate/distractor/token；
- Answer：faithfulness/citation/correctness/refusal；
- System：latency/cost/ACL/index freshness。

#### 面试追问

**LLM Judge 可靠吗？**

精确 ID、版本、数值、路径和 ACL 使用确定性指标；语义完整性才用 LLM Judge，并通过
人工样本校准、版本化和不同指标交叉检查。Judge 不作为唯一发布门槛。

## 5. STAR 故事模板

### 5.1 故事一：Code Graph 只是展示，不参与检索

**Situation**

当前系统能展示 CALLS/REFERENCES 图，但首轮仍按文本和本地哈希向量 Top-K，导致低词面
重合的依赖代码召回失败。

**Task**

让 Graph 真正进入仓库级代码候选生成，同时控制错误边和多跳噪声。

**Action**

- 建 AST Retrieval Unit；
- 多路召回；
- 按任务限制边类型和跳数；
- 引入路径分和代码 reranker；
- 设计 same-name、wrong-version hard negative；
- 做 flat vs graph vs graph+rerank 消融。

**Result**

填写真实 Recall、Path Recall、P95 和图扩展成本。

### 5.2 故事二：多源原始分数不可比较

**Situation**

Code/Codex 使用混合检索，Experiment/Document 使用结构化匹配，原始分数被直接排序，
导致数量大的来源占据结果。

**Task**

建立既可解释又能满足证据职责的跨源融合。

**Action**

- 来源内 rerank；
- relevance calibration；
- required roles；
- authority/version/status 分离；
- marginal utility selection；
- calibration 和 role coverage 评测。

**Result**

填写 Role Coverage、Citation、Wrong-version 和 P95。

### 5.3 故事三：回答有引用但证据不完整

**Situation**

Top-K 有 Citation 不代表结论完整，例如实验提升缺 DatasetVersion 或当前代码证据。

**Task**

让系统知道何时能回答、何时需要补检索或拒答。

**Action**

- Query Plan required roles；
- Evidence Pack；
- conflict/version resolver；
- corrective retrieval；
- claim-citation verifier；
- unanswerable taxonomy。

**Result**

填写 Required Role Coverage、Unsupported Claim、Unanswerable Acceptable Rate。

## 6. 高频面试问题

### 6.1 “你的项目为什么需要 RAG？”

知识由私有代码、会话、实验和文档持续更新，LLM 参数无法保证时效、版本和可引用性。
项目不仅需要“知道内容”，还要回答“哪个版本、来自哪里、是否验证、是否有反证”，这些
必须由外部证据系统提供。

### 6.2 “为什么是多源，不做六个独立搜索？”

很多研发问题天然跨源：代码说明现在怎么实现，会话说明为什么改，实验说明效果，文档
说明对外 Claim。独立搜索无法保证它们属于同一 commit/dataset，也无法形成验证路径。

### 6.3 “chunk size 如何选择？”

不使用全局 chunk size。代码按 AST，Codex 按 Episode/Item，文档按结构层级，表格按
schema/row/cell。通过 retrieval unit 与 parent context 分离，在 Golden Set 上做 chunk
消融，观察 Recall、duplicate、context distractor 和 token cost。

### 6.4 “为什么需要 sparse + dense？”

代码标识符、路径、commit、metric ID 适合精确和 sparse；自然语言描述、同义概念适合
dense。二者的失败模式互补，先 union 再 rerank 比单独使用更稳。

### 6.5 “reranker 放在哪里？”

来源内 reranker 负责理解来源特性并输出可校准相关分；跨源 reranker 使用压缩 Evidence
Card，负责角色、路径和跨源比较。对全部语料直接 cross-encode 成本过高，所以只重排
candidate union。

### 6.6 “GraphRAG 与普通知识图谱问答的区别？”

本项目的图不是唯一知识载体，文本、代码、数值和图关系共同召回。Graph 用于候选发现、
路径约束和上下文组织；语义相似边不当作事实边，最终仍回到原始证据 Citation。

### 6.7 “如何防止 hallucination？”

不能承诺完全消除。项目通过前置 Scope/ACL、required roles、事实状态、版本裁决、反证
通道、claim-level citation 和生成后 entailment 降低风险；证据不足时拒答并给出缺口。

### 6.8 “如何处理更新？”

Raw Object 内容寻址，派生 Retrieval Unit、embedding、关系都带 builder/model version；
新 Generation 完整构建后原子激活，旧 Generation 可回滚。只有内容或模型键变化的 Unit
重建。

### 6.9 “如何评价 RAG？”

按 retrieval、graph/evidence、context、answer、system 五层评测；同时保留可回答、
不可回答、错误版本、冲突、ACL 和 hard negative 切片。最终答案分只是一层。

### 6.10 “最失败的一次优化是什么？”

必须从真实实验选择。建议优先讲：

- Graph 无约束扩展提升 Recall 却降低 context precision；
- 更大 chunk 没有提升或引入重复；
- 强制来源多样化带来无关证据；
- 更大 embedding/reranker 成本高但收益小。

回答结构是：假设 → 实验 → 反直觉结果 → 诊断 → 新决策，不要编造“所有优化都成功”。

## 7. 架构白板顺序

面试现场按以下顺序画：

1. 左侧六个 Source Adapter；
2. 每个来源的专用实体/检索方法；
3. 中间统一 Candidate Contract；
4. Query Planner 和并行 Source Retriever；
5. 来源内 rerank/calibration；
6. role-aware fusion；
7. typed evidence graph；
8. Evidence Pack；
9. generator + citation verifier；
10. 下方 evaluation/trace/generation。

不要一开始画所有数据库表。先讲查询路径，再下钻一个 Code Graph 和一个跨源路径。

## 8. 面试证据卡模板

每个里程碑完成后复制一份：

```markdown
# Interview Evidence Card：标题

## 我负责的边界

## 业务/技术问题

## 初始系统和失败样例

## 约束

## 候选方案与取舍

## 最终设计

## 关键代码
- 文件：
- 类/函数：
- 测试：

## 实验
- Golden Version：
- Baseline Run：
- Treatment Run：
- 主要变量：
- 指标：

## 结果

## 回归/失败

## 线上或系统代价

## 如果重做

## 30 秒表达

## 可安全写入简历的句子
```

## 9. 项目指标看板

完成每个里程碑后更新，不填估算值：

| 指标 | V1 | V2 | Run ID |
| --- | ---: | ---: | --- |
| Code Symbol Recall@10 | TBD | TBD | TBD |
| Code Required Path Recall | TBD | TBD | TBD |
| Codex Episode Recall@5 | TBD | TBD | TBD |
| Experiment Numeric Accuracy | TBD | TBD | TBD |
| Document Claim Recall@10 | TBD | TBD | TBD |
| Source Routing Macro-F1 | TBD | TBD | TBD |
| Required Role Coverage | TBD | TBD | TBD |
| Cross-source Path Recall | TBD | TBD | TBD |
| Version Accuracy | TBD | TBD | TBD |
| Citation Precision | TBD | TBD | TBD |
| Citation Completeness | TBD | TBD | TBD |
| Unanswerable Acceptable Rate | TBD | TBD | TBD |
| Multi-source P95 | TBD | TBD | TBD |
| Unauthorized Leakage | TBD | TBD | TBD |

## 10. 简历发布前检查

- [ ] 所有数字来自 Evaluation Run；
- [ ] 说明数据集大小和场景，不只写百分比；
- [ ] 没有把设计目标写成已完成；
- [ ] 没有把论文收益写成本项目收益；
- [ ] 能解释 baseline；
- [ ] 能解释消融；
- [ ] 能解释一次失败；
- [ ] 能解释延迟/成本；
- [ ] 能解释个人负责范围；
- [ ] 能现场定位关键代码和测试；
- [ ] 不泄露私有数据、路径、密钥或内部会话内容。
