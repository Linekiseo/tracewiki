# RAG 项目面试 QA

## 一、项目与架构

### 1. 这个项目解决什么问题？

它把 Code、Codex 会话、Experiment、Notebook、Document 和 Workspace 六类工程证据编译成 Agent-Native Wiki，在项目、ACL、版本和 generation 边界内主动导航、回查原始证据、构造 Evidence Pack，并完成可验证问答或明确拒答。

### 2. 它与普通向量 RAG 有什么不同？

普通 RAG 常把所有内容切成文本块。这里保留文件、符号、run、cell、claim、工作项等来源语义；先编译路径化 Wiki，再组合 exact、sparse、dense、structural、graph、state 和 temporal 检索，由 Navigator 根据证据缺口多轮 search/read/follow/verify。

### 3. 为什么不直接使用一个统一索引？

不同来源的正确性条件不同。代码需要符号和 commit，实验需要 unit/seed/dataset，Notebook 需要执行顺序，Workspace 需要状态历史。统一 Wiki 可以组织和导航候选，但不能替代来源内真值，最终 citation 仍要落回 raw source。

### 4. 整体链路是什么？

Source Adapter → Immutable Raw Object → Typed Entity/Event → Wiki Compiler → Immutable Wiki Generation → Hybrid Search + Evidence Obligations → Navigator → Raw Verify → Evidence Pack → Claim/Citation Verifier → Grounded Answer 或拒答。

### 5. 为什么先保存 raw object？

它提供可追溯原始证据，使索引和派生事实可以重建，也避免后续解析器升级后无法解释旧结果。

### 6. generation 和 publication 有什么作用？

generation 表示一批一致的索引状态，publication 表示该状态已被批准用于查询。查询不能把不同 generation 的实体偷偷混在一起。

### 7. 什么是 typed task？

planner 为来源生成明确任务，如 code lookup、notebook output、experiment compare。typed task 必须真实传到来源 runtime，不能统一改成 search 再靠 query marker 伪装。

### 8. 多源 planner 的职责是什么？

解析意图、选择来源、分配角色和预算、生成 typed tasks，并约束 corrective retrieval。它不负责替来源伪造结果。

### 9. Evidence Pack 是什么？

它是经过 scope、ACL、generation、内容 identity 和 citation 校验的证据集合，包含来源、角色、locator、冲突、陈旧性和 trace，供回答器与审阅 UI 使用。

### 10. 系统何时拒答？

scope 不唯一、来源超时、证据不足、关键角色缺失、ACL/identity 不一致、冲突未解决或质量门不可用时，系统返回明确拒答而不是猜测。

## 二、数据建模与来源实现

### 11. Code 来源如何建模？

以 repository/ref/commit/generation 为边界，建模文件、符号、调用、导入、diff、提交和测试，并提供 canonical path 与 exact line span。

### 12. Codex 会话为什么不能只按 message 存？

因为命令调用、结果、文件修改和验证状态需要严格 pairing。纯 message 模型容易把“计划执行”或“声称成功”误当成已观察事实。

### 13. 如何判断验证真正通过？

必须有允许的 no-shell argv、明确 target、正确 call/result pairing 和可信整数 exit。Plan、AgentMessage 或文字 claim 不能升级为 passed。

### 14. Experiment 的核心实体是什么？

Experiment、Run、MetricDefinition、MetricSeries 和 Observation。每个观测点绑定 run、series、definition、unit、direction、split、step、time 和 value。

### 15. 为什么实验比较需要 unit 和 direction？

相同数值在 accuracy 与 latency 中含义不同；单位不同也不能直接比较。direction 决定“更高”还是“更低”更好。

### 16. Notebook 的关键难点是什么？

执行顺序、输出/错误、参数、环境和 lineage。相同代码在不同前置 cell 或环境中可能产生不同结果。

### 17. Document 如何保证引用准确？

引用绑定文档版本、页码、结构块、bbox 和文本范围。OCR 或版面不确定时不能给满分精确定位。

### 18. Workspace 如何避免把计划当成事实？

区分 current state、state history、proposed work 和 completed evidence，并要求状态变化有可追踪来源。

### 19. canonical locator 为什么重要？

它让 UI、API、评估器和离线 verifier 对同一证据使用稳定地址，并阻止绝对路径、遍历路径或跨实体 locator 注入。

### 20. 如何处理删除和更新？

通过 generation、tombstone、active publication 和 content-addressed identity 处理。旧实体可审计，但不能混入当前 active 结果。

## 三、检索、排序与上下文

### 21. 为什么同时使用 exact、sparse 和 dense？

exact 擅长 ID/符号/路径，sparse 擅长术语/错误/命令，dense 擅长语义表达。组合能减少单一检索方式的盲区。

### 22. graph retrieval 适合什么问题？

调用链、依赖、代码到会话、实验 lineage、Notebook cell 依赖和 Workspace 状态关系。

### 23. 如何合并不同来源分数？

先在来源内部校准和排序，再按 planner 角色、预算和 provenance 融合。不能直接比较未经校准的原始相似度。

### 24. 什么是 bounded corrective retrieval？

当首次证据缺少必要角色时，只允许有限次数、有限来源和有限预算的补检索，防止无限循环和成本失控。

### 25. 如何避免 hard negative？

Golden 显式冻结 hard negatives，评估完整 top-k 命中；排序时使用状态、时间、scope、kind 和来源专属规则降权或排除。

### 26. context builder 做什么？

验证上游 attestation 和正文 identity，去重证据，分配 token 预算，生成 canonical citations，并计算只基于实际 selected blocks 的质量指标。

### 27. 没有正文时能否给 precision 满分？

不能。相关指标必须是 `UNAVAILABLE` 或按真实 denominator 计算，不能因为没有 block 就把精确度设置为 1。

### 28. 如何处理冲突证据？

保留各自 provenance，判断版本和陈旧性，要求冲突角色覆盖；无法可靠消解时在答案中声明或直接拒答。

## 四、安全与可信性

### 29. ACL 在哪一层执行？

请求 scope 解析、来源检索、publication、context builder 和最终投影都会校验。它不是只靠前端过滤。

### 30. 什么是 governed attestation？

由生产 pipeline 私有签发的不可伪造声明，绑定 request scope、ACL、generation、完整 result 和 content manifest。builder 只接受正确 issuer 和 digest。

### 31. 为什么大量使用 component identity 和 digest？

它阻止测试替身、wrapper、subclass、monkeypatch 或同代码不同 globals 的函数冒充生产组件，也让 artifact 能证明其执行路径。

### 32. 如何防止路径泄漏？

locator 使用相对 canonical 格式；scanner 在截断前检查 POSIX、Windows、UNC、编码和多层解码路径，并避免在 diagnostic 中回显恶意值。

### 33. 如何处理 reasoning？

reasoning 不进入可检索证据和发布 artifact，只保留用户可观察的输入、输出、命令、结果和文件变化。

### 34. 智能体为何默认只读？

当前没有完整凭据和 privileged lifecycle 审批。只读状态能展示同步与健康信息，但不会伪造执行权限。

### 35. fail-closed 的代价是什么？

回答率会降低，例如来源超时时直接拒答；但它避免了不完整证据产生高置信度错误答案，是工程证据系统必要的取舍。

## 五、评估与发布

### 36. 为什么 evaluation-first？

先冻结 Golden、membership、truth 和 denominator，才能判断后续实现是否真的提升，防止边开发边修改评分标准。

### 37. 三态指标是什么？

`AVAILABLE` 表示证据充分可评分；`PROVISIONAL` 表示有结果但资格不足；`UNAVAILABLE` 表示无法诚实计算。三者不能用 0 或 1 混淆。

### 38. 为什么 verifier 必须 verify-only？

离线验证不能重新检索，否则 artifact 的结果会随环境变化。它只使用 artifact 内容重算 checksum、truth、denominator 和 metrics。

### 39. portable artifact 包含什么？

运行身份、组件和配置 identity、Golden authority、predictions、metrics、slice、errors、latency、security 与 checksums，且不能包含绝对路径、数据库或 secret。

### 40. 质量低是否阻塞工程？

不阻塞继续开发，但阻塞 qualification 和默认发布。低质量 artifact 应作为固定负基线保留，而不是删除或美化。

### 41. 发布阶段是什么？

OFFLINE → SHADOW_INTERNAL_100 → CANARY_5 → CANARY_25 → OPT_IN_100 → DEFAULT_V2。每一步都需要完整不可变证据，不能跳级。

### 42. 哪些是发布硬门？

ACL/secret/reasoning leakage、false validation、artifact 篡改、denominator 错误、必要指标 unavailable、延迟/成本超预算和回滚不可用。

## 六、性能与工程实践

### 43. 当前主要性能问题是什么？

Wiki S 层的 path、directory、hybrid 和 navigation 已达到工程 SLO；当前未证明的是 M/L 数据量、真实并发、远程 embedding/reranker 成本，以及既有 4 MB 级大会话的首屏压力。这些不能从小规模 fixture 外推。

### 44. 如何优化查询超时？

分析各阶段 trace，缩小候选集，预热索引，按来源动态分配预算，缓存稳定 exact 结果，并把 dense/graph 补检索限制在必要角色。

### 45. 如何优化大会话页面？

服务端分页或 window API、按回合延迟加载、前端虚拟列表、详情分片和请求取消；避免一次传输整个 4 MB payload。

### 46. 为什么前端 build 与后端指纹测试不能并行？

二者共享静态输出目录，构建中间态会导致指纹瞬时不一致。应串行化或使用独立构建目录。

### 47. 如何保证幂等摄取？

以 source identity、content digest、generation 和 publication key 去重，在事务内原子发布，失败时回滚并保留 raw evidence。

### 48. 如何扩展到大规模？

拆分 source ingestion worker、publication service 和 query runtime；索引按 project/source/generation 分片；冷热分层；对 graph 和 dense 使用可替换后端，但保持相同合同。

## 七、项目复盘与边界

### 49. 项目最重要的设计决策是什么？

把“证据真实性”和“发布资格”置于回答率之上：所有来源保留原始证据，typed task 真实下传，指标不可用就保持不可用，证据不足就拒答。

### 50. 最难的 bug 类型是什么？

通常不是语法错误，而是“内部自洽但没有生产 authority”的伪真值：调用方可以重签、自报 denominator、复制跨阶段 child 或用 marker 重贴标签。

### 51. 为什么开发过程看起来复杂？

因为系统同时承担检索、数据治理、审计、评估和发布控制。如果目标只是 demo，可以大幅简化；如果目标是可用于工程决策的可信 RAG，这些边界是必要的。

### 52. 当前能否说项目开发完全结束？

可以说六源 RAG 和 Agent-Native Wiki 的仓库工程链路完整，包括 Compiler、Store、Search、Navigator、Builder、智能查询、MCP/Codex 插件和 Workbench。不能说已经生产发布，因为正式数据 qualification、M/L 容量、shadow/canary 和默认 Wiki 引擎尚未完成。

### 53. 下一步最高优先级是什么？

先取得正式库所有者授权并构建只读脱敏镜像，完成 M/L 容量、真实模型和生产形态 reviewed qualification；随后采集 shadow trace、验证 Builder utility 和 rollback，最后进入 canary。

### 54. 如果面试官要求一句话总结项目？

这是一个把六源工程证据编译为 Agent-Native Wiki、由有预算 Navigator 主动找证据并回查原文、再以 Evidence Pack 和 claim-citation verifier 约束生成的可信 RAG；仓库工程已闭合，生产发布仍被质量门主动阻止。

## 八、Agent-Native Wiki 与智能检索

### 55. 项目中的 Wiki 是传统知识库页面吗？

不是。它是机器可导航的 RAG 知识组织层，核心是 compilability、composability 和 evolvability：原始证据可编译成页面/事实/链接，Agent 可 search/read/follow，失败可进入 Error Book 并由受审 Builder 修复。Workbench 只是它的可视化产品面。

### 56. 为什么有了六源 Retriever 还需要 Wiki Compiler？

Retriever 解决“每个来源里找候选”，Compiler 解决“把跨源证据长期组织成稳定的知识空间”。没有 Compiler，每个复杂问题都要从六个孤立来源重新拼接，无法获得稳定路径、别名、反链、能力页、决策页和可增量演化的导航结构。

### 57. Wiki page 为什么不能直接作为最终答案权威？

page 是派生结构，可能压缩、过期或遗漏细节。它可以证明“去哪里找”，不能单独证明“事实一定如此”。最终 claim 必须引用与 page source-ref digest 一致的 raw evidence。

### 58. WikiStore 如何避免发布时读到一半新一半旧？

Compiler 写入 immutable staging generation，完整 record/manifest 验证后原子切 active pointer。Navigator 在开始时固定 generation；缓存键也含 generation，因此 publish/rollback 与并发 reader 不会产生 mixed generation。

### 59. ACL 为什么要做到 page fragment 和 cache key？

如果先把不同 ACL 的证据合成一个 page，再在回答前过滤，标题、摘要、别名、反链或缓存命中仍可能泄漏不可见信息。因此 visibility partition 在编译、索引、目录、link、cache、read 全链路一致。

### 60. 什么是 Evidence Obligation Graph？

它把问题所需的证据角色显式化，例如 implementation、validation、rationale、current version 和 counter evidence。Navigator 的下一步由哪些 obligation 尚未满足决定，而不是盲目追加 top-k。

### 61. Navigator 与普通 agentic RAG 有何不同？

它是确定的 bounded 状态机，不是无限自由 Agent。search/read/follow/raw-verify、每轮 top-k、最大 hops、token budget、empty-search patience 和 deadline 都有硬上限；停止原因也进入可审计 trace。

### 62. 如何防止查询无关但“看起来很像”的页面支持答案？

显式 ID、版本、日期和路径 token 是硬约束；普通 query terms 经过 bounded lexical relevance admission。只有 query-anchored page 才能继续 graph expansion。全部候选不相关时记录 `query_relevance_not_met` 并最终拒答。

### 63. 为什么搜索 query 不直接拼接 obligation role？

role 用于候选过滤和证据充分性，而不是篡改用户问题。把 `implementation` 等 role 拼进 query 会让所有实现页获得虚假相关性，尤其会破坏 unanswerable truth。

### 64. exact、BM25、dense、structural 和 reranker 如何分工？

exact 保住硬标识，BM25/FTS5 处理术语与错误，dense 处理语义改写，structural 提供目录/link/role 信号，RRF 融合异构排名，reranker 做候选二次判断。dense/reranker 是可替换 provider，缺失时系统仍可确定性运行。

### 65. 系统如何做 Corrective RAG？

首轮后按 missing obligations 生成有限补查询、link follow 或 raw fallback。补检受来源、次数、深度、token 和 deadline 约束；仍不足时返回 fixed refusal，而不是继续循环或让模型补全缺口。

### 66. Evidence Pack 比“拼接上下文”多了什么？

它保存事实状态、角色覆盖、stale/unauthorized、counter evidence、版本差异、raw citations、rendered evidence、token accounting 和决策。其 membership 与 digest 可离线验证，因此生成器不能悄悄添加未检索事实。

### 67. 智能生成如何防 hallucination？

生成器只能返回结构化 claims，每项声明 citation IDs 和 required fact IDs。verifier 精确检查引用、fact authority、词项蕴含和完整性；unsupported claim 不进入 rendered answer。不可回答的 pack 即使没有 claims 也返回 final refusal。

### 68. Error Book 与普通日志有什么区别？

日志记录发生了什么；Error Book 记录错误类型、影响 generation/query、source refs、修复约束、生命周期和关闭证据，并会约束下一次 Compiler/Builder。这使失败能跨 batch 转化为知识结构改进。

### 69. Builder 会不会让系统在线自我修改失控？

不会。patch 是 content-addressed proposal，只能在 staging 上用 affected queries、guard queries 和 hard gates 比较 before/after。没有人工/权威 review、正 utility 和安全门就不能发布；在线查询没有修改 active Wiki 的权限。

### 70. Codex 插件接入做了什么？

插件提供真实 manifest、MCP 配置和四个 skills；通过 token 连接 0.2.0 MCP server。Agent 可查询状态、搜索、读页、沿关系、运行多跳导航、读原始证据和提出 patch，但所有动作仍服从项目、ACL、generation、预算和审批合同。

### 71. 为什么插件不能直接给 Agent 全部写权限？

知识读取和 privileged mutation 风险不同。默认导航应可审计且最小权限；compile/publish/rollback/token/execution 等写操作需要独立凭据、人工审批和 audit。插件 skill 不能绕过服务端 authority。

### 72. Wiki 缓存为什么不能只按 path？

相同 path 在不同 project、generation、visibility 和 record kind 中内容可能不同。正确 key 是 `(project, generation, visibility, logical_path, kind)`，否则会出现跨项目、跨 ACL 或混代泄漏。

### 73. 质量评测发现过什么真正的 RAG 问题？

历史 quality v2 的 110 个 answerable case 全通过，但 10 个 unanswerable case 全错，因为泛化页面被当作相关证据。新增 query relevance admission 和 final refusal authority 后，v3 correct refusal 达到 10/10。负 artifact 被保留而不是删除。

### 74. 当前性能证据能证明什么？

能证明 362 pages/950 refs 的 S 层 point：hybrid P95 10.893 ms，standard/deep navigation P95 80.346/86.229 ms，compile/publish/rollback 均过工程 SLO。它不能证明 50k pages、生产并发或远程模型成本。

### 75. 这个系统相对主流 RAG 的独到点是什么？

它没有押注单一向量模型，而是把 Evidence Compiler、Agent-Native Wiki、hybrid/graph/temporal retrieval、obligation Navigator、raw verification、Evidence Pack、refusal verifier、Error Book/Builder 和 release evidence 放进统一治理边界。创新点是这些能力之间的 authority 分工和可审计闭环。

## 九、查询理解算法与原生 App

### 76. 查询理解是不是一组关键词规则？

不是。主路径是 constrained structured LLM：输出冻结 intent、source hints、evidence roles、1–4 个子问题、实体、时间和不确定性。离线兜底是冻结训练样本的 TF-IDF word/character n-gram centroid classifier，通过 cosine 和 temperature softmax 输出完整意图/来源 posterior。规则只承担安全校验和硬范围约束，不承担最终语义分类。

### 77. 本地统计模型为什么有必要？

远程模型可能未配置、超时、隐私不允许或输出不合合同。本地模型没有网络和下载依赖，延迟稳定、结果可复现，并能给出 posterior 和 entropy；它保证检索能力持续可用，但不会假装达到大模型的语义能力。

### 78. LLM 查询规划如何避免越权？

模型只返回枚举合同，source hints 必须是用户已允许来源的子集；project、repository、commit、as-of、generation 和 ACL 由服务端 scope resolver 注入，模型没有这些字段的写权限。模型生成的子问题还要通过 secret、绝对路径、长度和 outbound-safety 校验。

### 79. 模型结果与本地结果如何融合？

将模型 confidence 视作语义规划权重：`P_final(i)=c·1[i=i_llm]+(1-c)·P_local(i)`，再归一化来源 posterior。这样模型的判断有本地可观测先验，低置信或冲突可以显式呈现，而不是隐藏在 prompt 里。

### 80. 子查询如何生成，为什么不会无限扩张？

模型按 evidence obligations 做受约束分解；离线路径做 clause segmentation，再对实体/role 候选用 MMR 选择。最多四条计划、在线最多执行三条，短问题保持一条；每条复用同一 scope，并共享 deadline 和来源预算。

### 81. 多查询结果如何融合？

使用 RRF：`Σ 1/(60+rank)`。它只依赖排名，不直接比较 BM25、dense 或不同来源的异构原始分数；同时记录 query appearances，重建 citation、relations、conflicts 和 generation watermarks，再进入来源 authority/version/role 重排。

### 82. 为什么 App 要显示 posterior、entropy 和子查询？

智能检索必须可人工审阅。只展示答案无法判断模型是否误解了问题。App 显示算法路径、top intent posterior、不确定性、复杂度、来源建议、Q1–Qn、role coverage 和纠错轮次，用户能在回答前后检查系统的理解与证据路线。

### 83. LLM API key 如何保存？

最终产品是 macOS/Windows 原生 Tauri App。公开 base URL/model 写 App 配置，API key 写 macOS Keychain 或 Windows Credential Manager。native host 启动时将 key 注册到后端进程内存；WebView、项目数据库、证据库和响应都拿不到 key。远程 provider 必须 HTTPS。

### 84. 没有 LLM 时系统还是 RAG 吗？

是。查询理解走本地统计模型，检索仍使用 exact/sparse/structural/temporal 和受治理的 source-local pipeline，Navigator、raw verify、Evidence Pack 与 refusal verifier 都工作；区别是不能做远程语义规划和生成，因此返回 retrieval-only，而不是伪造自然语言答案。

### 85. 这套查询理解如何评估？

不能只测 intent accuracy。还要测 macro-F1、calibration/ECE、entropy、source recall、decomposition coverage、角色完成率、multi-query gain、hard-negative hit、correct refusal、claim support、ACL/secret leakage 和各阶段 P50/P95。远程模型必须在同 membership 数据上与本地基线比较后才能通过质量门。
