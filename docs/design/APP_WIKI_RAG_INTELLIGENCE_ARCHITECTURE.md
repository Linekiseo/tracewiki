# Evidence Wiki + RAG 智能查询与桌面端技术架构

状态：`IMPLEMENTED / ENGINEERING_VERIFIED / QUALITY_HOLD`  
更新：2026-08-03  
产品边界：macOS / Windows 原生桌面应用；`frontend/` 是 Tauri 内嵌 UI 资产和自动化验收面，不作为独立 Web 产品发布。

## 1. 目标与非目标

系统要解决的不是“把若干文本塞给模型”，而是把代码、Codex 会话、实验、Notebook、文档和 Workspace
中的工程证据组织为可持续演化的 Wiki，再依据问题主动寻找、核验、组合证据，最后生成可逐项追溯的回答。

完整在线链路是：

```text
自然语言问题
  → 混合查询理解
  → 证据义务与子问题图
  → 受治理的来源规划
  → exact / sparse / dense / structural / temporal 多路召回
  → 跨查询 RRF + 跨来源校准 + 重排
  → 有预算的关系导航和纠错检索
  → raw evidence 核验
  → Evidence Pack
  → 结构化生成
  → claim / citation / fact verifier
  → grounded answer / retrieval-only / refusal
```

非目标：

- LLM 不负责 ACL、project、repository、commit、generation 或时间范围裁决；
- Wiki 页面不是最终事实权威，最终引用必须能落回 raw evidence；
- 未配置模型时不伪装“智能模型已运行”，而是明确展示本地统计算法与不确定性；
- 不用无限 Agent loop 掩盖证据不足；
- 不把浏览器 localStorage 当 LLM secret 存储。

## 2. 原生 App 运行与信任边界

```text
┌──────────────── macOS / Windows App ────────────────┐
│ Tauri native host                                   │
│ ├─ OS Keychain / Credential Manager：LLM API key   │
│ ├─ public provider profile：base URL + model only   │
│ ├─ native commands：register / test / clear         │
│ └─ embedded React workbench                         │
└──────────────────────┬──────────────────────────────┘
                       │ loopback / configured HTTPS
┌──────────────────────▼──────────────────────────────┐
│ Evidence RAG backend                                │
│ ├─ process-memory provider registry                 │
│ ├─ QueryUnderstandingEngine                         │
│ ├─ Wiki + six governed source runtimes              │
│ ├─ Evidence Pack / verifier                         │
│ └─ no durable LLM secret                            │
└──────────────────────┬──────────────────────────────┘
                       │ optional HTTPS
                 OpenAI-compatible API
```

App 设置只把 `base_url`、`model` 等公开配置写入 App 配置目录；API key 写入 macOS Keychain 或 Windows
Credential Manager。启动后由 native host 将 secret 注册到后端进程内存，WebView 永远拿不到 key。远程 provider
必须是 HTTPS；仅 loopback 允许 HTTP。删除、覆盖和失败回滚都由 native host 管理。

## 3. 查询理解不是关键词规则

查询理解采用 `query-understanding-hybrid-v2`，由两条互补算法路径组成。

### 3.1 主路径：结构化 LLM 语义规划

配置模型后，系统向 OpenAI-compatible Chat Completions 发送受约束规划请求。模型只允许输出一个 JSON：

- `intent`：九类冻结研发意图之一；
- `source_hints`：请求已允许来源的子集；
- `required_roles`：已知 evidence roles；
- `subqueries`：1–4 个可独立检索的子问题；
- `entities`、`temporal_scope`、`ambiguity` 和 `confidence`。

响应经 schema、长度、枚举、finite number、路径、secret 和 outbound-safety 校验。模型无法扩大来源、ACL、
repository、commit、as-of 或 generation；这些范围仍由服务端 scope resolver 注入。非法、超时或不可用时自动退到
本地统计模型，且对外只暴露固定 fallback reason。

### 3.2 离线路径：冻结 TF-IDF n-gram 质心分类

未配置模型时，系统不是执行按顺序排列的正则规则，而是运行一个可复现的小型统计分类器：

1. 冻结、版本化的中英文标注 utterance 形成训练集；
2. 提取 ASCII word unigram/bigram 与中文 character bigram/trigram；
3. 计算平滑 IDF：

   `idf(t) = log((N + 1) / (df(t) + 1)) + 1`

4. 使用 sublinear TF：

   `w(t,d) = (1 + log(tf(t,d))) × idf(t)`

5. 每一意图和来源对其训练向量求质心；查询与各质心计算 cosine similarity；
6. 以 temperature softmax 得到完整后验概率，不按第一条命中决定；
7. 用归一化熵表达不确定性，并结合 clause 数、实体数和来源熵估算问题复杂度。

显式 intent 是用户约束，因此 posterior 固定为该 intent；否则选择最高后验，并把 top distribution、熵、复杂度
和算法版本返回 App。低置信结果会进入 ambiguity，不会被包装成确定结论。

### 3.3 混合校准

LLM 结果不是孤立使用。模型 confidence 与本地 posterior 融合：

`P_final(i) = c_llm · 1[i = i_llm] + (1 - c_llm) · P_local(i)`

来源概率也用本地 posterior 与模型选择做归一化融合。这样既保留模型的语义改写能力，也让系统能显示稳定先验和
冲突/不确定性；模型不可用时合同仍完整。

## 4. 子问题分解与证据义务

模型路径按“证据义务”分解，不是让模型直接回答。例如“某项优化是否有效且能否复现”会分成：

1. 实现和 commit；
2. 实验 run、metric 和 baseline；
3. dataset/version/seed/environment；
4. counter evidence 或 stale evidence。

离线路径先做 bounded clause segmentation，再结合实体和 required roles 产生候选查询，使用 Maximal Marginal
Relevance 选择最多四条：

`MMR(q) = 0.72 · sim(q, original) - 0.28 · max sim(q, selected)`

短单焦点问题保持 one-shot，避免为了“看起来智能”增加无收益检索。复杂问题按 deadline 最多执行三条检索，
每条复用同一可信 scope。所有子查询结果进入多查询融合，原始用户问题仍是可观测主查询。

## 5. Wiki 与六源知识组织

六个来源保留各自的事实语义和状态机：

| 来源 | 核心事实 | 典型 hard negative |
|---|---|---|
| Code | repository/commit/file/symbol/diff/test | 旧提交、同名符号、未发布 generation |
| Codex | thread/turn/action/result/patch/validation | plan-only、claim-only、failed result |
| Experiment | run/metric/series/observation/dataset/seed | unit/direction 不同、failed run、不可比较 |
| Notebook | cell/code/output/error/parameter/lineage | mentioned-only、stale output、跨运行结果 |
| Document | section/claim/table/figure/citation | 相似段落、错误页码、未绑定 claim |
| Workspace | topic/iteration/task/status/link | 计划冒充完成、旧状态、跨 scope 任务 |

Compiler 将受治理事实编译成 path/page/fact/typed-link/source-ref，而不是重新切成无语义 chunk。发布采用 immutable
generation + staging verifier + atomic active pointer；读请求固定一个 generation，杜绝 mixed generation。

## 6. 多路召回与排序

### 6.1 召回通道

- exact：ID、URI、symbol、commit、run、path 等硬标识；
- sparse：FTS5/BM25，覆盖术语、错误、命令和长尾 token；
- dense：可替换 embedding provider，当前未通过生产资格时不作为正确性依赖；
- structural：目录、typed links、反链、角色和局部邻接；
- temporal/state：as-of、validity、active/stale/contradicted；
- source-local structured retrieval：指标比较、代码图、Notebook lineage 等不能被通用文本检索替代的查询。

### 6.2 多查询 RRF

每个子查询先得到独立排名，再用 reciprocal-rank fusion 合并：

`RRF(d) = Σ_q 1 / (60 + rank_q(d))`

同时记录 `query_appearances`，跨多个子问题反复出现的证据优先；entity ID 去重，citation map、relations、conflicts、
generation watermarks 从全部成功查询重建。原始异构分数不直接相加。

### 6.3 跨来源校准和二阶段重排

排序按以下信号组合，而不是只看向量相似度：

- query / source posterior；
- exact match 与 identifier coverage；
- evidence-role gain；
- source authority 与 observation state；
- version/currentness alignment；
- relation/path proximity；
- reviewed calibration；
- hard-negative、stale、contradiction penalty。

可替换 cross-encoder/late-interaction reranker 只处理召回后的 bounded candidates，并受 timeout、privacy 和 quality
gate 控制。缺少远程模型不影响 exact+sparse+structural 主路。

## 7. Corrective / Agentic Retrieval

首轮检索后计算 required role coverage。缺口驱动的动作只有 search、read、follow、raw-verify 和 stop；每种动作
都有次数、候选、hops、token 和 deadline 上限。补检不是自由循环：

- 缺 role → 有限改写或改源；
- 发现 stale/conflict → 查 current version/counter evidence；
- Wiki 无入口 → governed raw-source fallback；
- 仍不足 → `partial` 或 fixed refusal。

这吸收了 Corrective RAG 和 Agentic Retrieval 的核心思想，但不允许模型把“自我感觉充分”当作证据充分。

## 8. Context Engineering 与回答核验

Evidence Pack 保存：included facts、状态、角色覆盖、缺失/未授权/过期、counter evidence、版本差异、canonical
citations、raw digests、token accounting 和 answerability decision。生成器只接收 pack，不直接读数据库。

`answer` 模式下，生成器输出结构化 claims；每条必须声明 citation IDs 和 required fact IDs。verifier 检查：

- citation/fact 是否属于同一 pack；
- raw digest 与 version/locator 是否绑定；
- required evidence 是否齐全；
- claim 是否被引用材料支持；
- ACL/scope/generation 是否一致。

任一失败退到 retrieval-only；pack 本身不可回答则 refusal。`evidence` 模式完全不构造外部模型客户端。

## 9. App 中的可解释交互

智能查询界面必须展示“系统做了什么”，而不是只有一个旋转图标和答案：

- 本轮是结构化模型还是本地统计算法；
- 模型是否已配置；
- top intent posterior、置信度和不确定性；
- query complexity、实体、来源建议；
- Q1–Qn 子查询；
- source waves、预算和状态；
- Evidence Pack 的已满足/缺失 roles；
- corrective rounds、citations、raw locator、version；
- grounded/retrieval-only/refusal 与固定原因。

设置页提供 LLM provider、base URL、model 和 keychain secret 管理；测试连接只做无证据 health request，不泄漏
项目问题、ACL 或 evidence。

## 10. 性能设计

- 模型理解预算不超过总 deadline 的 20%，且上限 8 秒；失败立即走本地算法；
- deadline < 10 秒只执行一个查询；否则最多三个；
- 子查询按同一预算分摊，每个来源仍有独立上限；
- RRF、统计分类和 verifier 都是本地有界计算；
- Wiki search-first、batch page read、bounded link/raw read；
- cache key 包含 project/generation/visibility/path/kind，不能跨 ACL 或混代；
- dense/reranker/provider 必须独立 timeout，不允许拖垮 deterministic path；
- trace 记录耗时、算法和计数，不记录原始 query、ACL 或 secret。

后续生产性能资格需要在获授权的只读镜像上测并发、M/L 容量、远程模型成本和冷启动，不能用 isolated fixture
数据外推。

## 11. 评估矩阵

查询理解不能只测“intent 是否等于标签”。至少需要：

| 层 | 指标 |
|---|---|
| 理解 | intent accuracy/F1、ECE、entropy、source recall、decomposition coverage |
| 召回 | Recall@k、MRR、hard-negative hit、role coverage、current-version precision |
| 融合 | multi-query gain、cross-source calibration、duplicate/noise rate |
| 导航 | obligation completion、premature stop、raw citation binding、correct refusal |
| 回答 | claim support、citation precision/recall、unsupported claim、answerability |
| 安全 | ACL/secret/path/reasoning leakage 必须为 0 |
| 性能 | planner/retrieval/rerank/navigation/generation P50/P95、成本、timeout |

本仓库已有来源 Golden、portable verifier、Wiki quality/capacity 和 release evidence；远程 LLM/dense/reranker 仍需
真实 provider、隐私审查和同 membership benchmark 才能从 `QUALITY_HOLD` 升级。

## 12. 代码落点

- 查询理解与 RRF：`src/evidence_rag/query/intelligence.py`
- LLM runtime provider：`src/evidence_rag/query/provider.py`
- 统一查询编排：`src/evidence_rag/query/service.py`
- Wiki 智能查询：`src/evidence_rag/rag/wiki/query_v1.py`
- 多源 planner/runtime：`src/evidence_rag/rag/planner_v2.py`、`multisource_runtime_v2.py`
- Evidence Pack / answer verifier：`src/evidence_rag/rag/answer_v2.py`
- App secret bridge：`frontend/src-tauri/src/lib.rs`
- App provider lifecycle：`frontend/src/desktop/runtime.tsx`
- 智能查询可解释 UI：`frontend/src/features/search/TrustedQueryPanel.tsx`

## 13. 与主流方案的关系

- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/query/overview/)：吸收 Local/Global/DRIFT
  工作负载区分；本系统用 typed evidence graph 和 raw citation 作为 authority，不强制为所有问题生成社区摘要。
- [Anthropic Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)：吸收检索前补充上下文的
  思路；本系统把上下文固化为 source-aware fact/page metadata，避免在线自由改写 raw evidence。
- [Corrective RAG](https://arxiv.org/abs/2401.15884)：按 role coverage 和 evidence quality 触发有限补检；补检失败
  后拒答。
- [Self-RAG](https://arxiv.org/abs/2310.11511)：吸收检索/生成/批判分阶段思想；最终判定由冻结 verifier 完成，
  不接受模型自评分作为事实。
- [Query decomposition（EACL 2026）](https://aclanthology.org/2026.eacl-long.322/)：使用 bounded semantic
  decomposition；本系统额外约束 scope firewall、证据义务、预算和版本一致性。

## 14. 当前真实边界

已实现：本地统计理解、结构化 LLM 规划、OS keychain 配置、子问题、RRF、受治理多源检索、Wiki Navigator、
Evidence Pack、claim verifier 和 App 可解释展示。

尚未宣称：任意远程模型均达到生产质量、Windows 真机已完成运行时验收、正式 service-owned 数据已迁移、默认 V2
已发布。当前正确发布状态仍是 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`；这不否认功能存在，而是避免把工程
fixture 结果冒充生产质量。
