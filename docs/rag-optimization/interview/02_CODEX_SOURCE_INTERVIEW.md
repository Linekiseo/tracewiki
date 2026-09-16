# Codex Temporal RAG 面试与简历材料

对应设计：[02_CODEX_SOURCE_RAG.md](../sources/02_CODEX_SOURCE_RAG.md)
对应开发计划：
[02_CODEX_SOURCE_DEVELOPMENT_PLAN.md](../development/02_CODEX_SOURCE_DEVELOPMENT_PLAN.md)

## 1. 当前状态声明

### CURRENT_MEASURED

- X0-01 Foundation `PASS`；
- released Golden 为 `codex-golden-v1/v1`，hash
  `sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1`；
- Golden exact 45，八个 slice 为 6/6/6/6/7/5/4/5；adapter materialization 为
  8 threads/54 items，包含 100 judgments、10 negative routes、5 false-validation
  categories；
- 原 X-B0 production Run
  `evaluation-run://project-codex-xb0-v1/6971b8cb1702a9ece8a894915c6b6de7`
  的 identity/security/portable checks 为 `PASS`，但历史 truth audit 为 `FAIL`；
  原 artifact 指标不是权威完整 truth；
- 权威 offline correction overlay 为
  `evaluation-correction://project-codex-xb0-v1/1ccecfa1c9667d0f716c709d1175d570`，
  set
  `sha256:f5da3d0b0d9d29bed3daff0a230cd1aa131d19385c20e9784b9b7a44a089ca6d`，
  exact 10 files，结论 `VERIFIED_CORRECTION_NON_QUALIFIED`；correction 没有重新检索
  或访问数据库，原 Run 保持 immutable；
- Final Foundation Gate 为 `X-B0 PERSISTENT CORRECTION AUDIT PASS`、P0/P1=0、
  `X-B0 FIXED BASELINE CONTROL ACCEPTED — NON_QUALIFIED`；
- CX1-01 与 CX1-02 engineering `COMPLETE`；X1 engineering Gate P0/P1=0；
- X-T1 final reprepare 曾 `AUTHORIZED`，但唯一 production attempt 授权已消费并在
  artifact 发布前失败：production Adapter 已运行 8 threads/54 items，CX1
  normalizer/facts 已在临时 SQLite 运行，harness 的 `adapter-command:…` 违反严格
  Identifier contract；
- failed-attempt audit Gate 为
  `X-T1 PRODUCTION ATTEMPT AUDIT VERIFIED — FAILED_BEFORE_PUBLISH`、P0/P1=0；这里的
  P0/P1=0 只证明失败边界审计完整；
- 该 attempt 没有 Run ID/URI、set hash、metrics、slices 或 artifact，没有第二
  attempt；temp/sidecars/stage 已清理，正式库、网络、检索未触碰，旧 X-B0
  Run/correction 的 bytes 与 mtime 不变；
- 缺陷随后修为 `adapter-command-…`，synthetic 23 与 static checks `PASS`；released
  treatment 没有重跑，因此这些 PASS 不是 treatment evidence；
- X-T1 treatment 为 `NO_RESULT / NOT_QUALIFIED / UNAVAILABLE`，retry
  `NOT_AUTHORIZED`；
- 用户切换到单会话完整开发后，X2–X5 仓库内工程已完成：Goal-aware Episode、
  content-addressed Unit/temporal edge、isolated additive store、exact/sparse/local dense
  cache/event graph、rerank/calibration、timeline context、append/privacy/release
  governance；默认仍为 V1；
- 该路径没有新的 production treatment Run/metrics，质量仍
  `UNAVAILABLE / NOT_QUALIFIED`；正式库 migration/backfill、远程模型 benchmark、
  production shadow/canary/default V2 仍未执行；
- 设计基线曾记录 5 个活跃 Thread、127 个 Turn、6,178 个 Item/View，ToolCall +
  ToolResult 约占 55.7%；这些是既有设计的历史观察，本轮未在当前工作区重新测量；
- 当前每 Turn 派生一个 DevelopmentEpisode；
- 使用 `local-hash-v2` flat hybrid；
- 有 HAS_ITEM、OUTPUT_OF、HAS_TURN、HAS_EPISODE；
- 既有设计历史观察中的活跃数据没有 ValidationResult；
- reasoning 排除、secret redaction 已实现。

X-B0 是 `NON_QUALIFIED` fixed negative baseline control，不是优化收益。X1 可以表述为
“engineering COMPLETE, production treatment unavailable”；X-T1 没有 published
treatment artifact，因此没有可填写的优化提升数字。

权威 X-B0 correction truth：

| Metric | Authoritative correction truth |
| --- | ---: |
| Thread Recall@5 | 23 / 40 = 0.575 |
| Episode Recall@5 | 21 / 40 = 0.525 |
| Item Recall@10 | 26 / 40 = 0.65 |
| MRR | 16.2972222222 / 40 = 0.4074305556 |
| Goal Recall | 7 / 7 |
| Decision Recall | 5 / 7 |
| Harmful old/negative retrieval | 5 / 26 |
| Event-order truth | 1 / 3 |
| Call/result truth | 1 / 2 |
| Patch status truth | 3 / 4 |
| Validation truth | 7 / 10 |
| False Validated | 1 / 5 |
| Outcome truth | 1 / 3 |
| Duplicate context | 0 / 45 |
| Tool noise | 8 / 45 |
| Hard-negative route | 8 / 45 |
| Correct zero | 0 / 5 |
| Refusal failure | 5 / 5 |

### DEVELOPMENT_STATUS（状态，不是质量结果）

| Milestone | 状态 | 证据/尚未实现 |
| --- | --- | --- |
| X0 Golden/Evaluation/Foundation | Foundation `PASS` | Golden released；X-B0 fixed control accepted、`NON_QUALIFIED` |
| X1 Event Normalization | engineering `COMPLETE`；production treatment `UNAVAILABLE` | CX1-01/CX1-02 COMPLETE；X-T1 failed before publish |
| X2 Episode V2 + Unit | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | Goal-aware episode、versioned units/edges、isolated store |
| X3 Retriever/Reranker | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | exact/sparse/local dense/temporal graph、rerank/calibration/adaptive-k |
| X4 Context/Comparison | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | task timeline/comparison、stable citation、missing/uncertainty |
| X5 Incremental/Release | `ENGINEERING COMPLETE / HOLD_DEFAULT_V1` | append cursor、privacy tombstone、shadow/release/rollback evaluator |

## 2. 15 秒定位

> 我正在把 Codex 会话问题定义为研发事件 RAG：目标是用 Goal、Decision、Tool、
> Command、Patch、Validation 和 Outcome 组成时序 Episode，并让检索同时考虑语义、
> 事件状态和时间路径。当前 X0 Foundation 已通过，X-B0 作为
> `NON_QUALIFIED` fixed negative baseline 被接受；X1 engineering 已完成，但唯一
> production attempt 在 artifact 发布前失败，所以 treatment unavailable。X2–X5 的
> 仓库内工程随后已独立完成，但没有把该失败 attempt 改写成质量结果。

完整实施后的准确补充是：旧 X-T1 仍未授权重试，也没有质量结果；独立于 treatment 的
V2 contracts/store/retrieval/context/governance 工程已经完成，可以在 isolated/显式
opt-in 路径验证，不能把它表述为质量提升或正式发布。

## 3. 1 分钟讲解

> 当前系统已能安全解析 JSONL，排除 reasoning、脱敏 secret，并把 Thread/Turn/Item 和
> ToolCall→ToolResult 关系持久化。但既有设计历史快照的 6,178 个 Item 中大部分是工具
> 流量，每个 Item 用同一种 hybrid 分数，而且 Episode 固定等于 Turn，所以“为什么改、
> 实际改了什么、是否验证”容易被噪声和状态混淆；本轮没有重测该规模数字。
>
> X1 engineering 已实现 normalizer 与 facts 的工程链路，并保留原始 Item；production
> treatment 在发布前失败，所以没有可用质量结果。随后完成的 V2 工程会从 production
> observable items 构建 Goal-aware Episode、versioned Unit 和 temporal graph，在
> isolated additive store 原子发布，并提供 exact/sparse/local dense、state-aware
> rerank、timeline context 与 append/privacy/release governance。它仍只显式启用，
> 不能表述为 production quality 或正式发布。

## 4. 白板

```text
JSONL
→ Safe Normalizer
→ Thread / Turn / Raw Item
→ Event State Machine
→ Goal-aware Episode
→ Exact + Sparse + Dense + Temporal Graph
→ Codex Reranker
→ Timeline Context
```

事实链：

```text
Goal
→ Tool/Command invoked
→ ToolResult observed
→ Patch applied
→ Validation exit observed
→ Outcome
```

## 5. 核心决策

### 5.1 原始事件与派生知识分离

Raw Item 永久保留；Episode、DecisionCandidate、Outcome 都带 builder/extractor version 和
DERIVED_FROM。更新摘要不会改变原始证据。

### 5.2 状态优先于文本措辞

“我将运行测试”“已执行 pytest”“exit code 0”是三个不同状态。只有最后一个能形成通过
事实；AssistantMessage 只能是显式陈述。

### 5.3 Episode 不等于 Turn

Turn 是产品/协议边界，Episode 是研发目标边界。任务可跨 Turn，同 Turn 也可能有子目标。
第一版用确定性 Goal 和连续性信号，后续才评审 classifier。

## 6. 高频追问

### 为什么不直接总结每个会话？

摘要会丢失时序、状态、错误细节和 Citation。项目使用小 Unit 召回，命中后按事件链加载
原始 Item；摘要只是一个候选通道。

### 如何避免把思维链存入系统？

adapter 明确排除 reasoning 类型；Decision 只能从用户可见的显式消息或可观察操作抽取，
并引用来源。系统不尝试推断或重构隐藏 reasoning。

### 如何识别验证？

命令必须命中测试/lint/typecheck allowlist，并且 Command 或关联 ToolResult 中有可观察
exit code。没有 exit code 就不派生 ValidationResult。

### ToolResult 为什么不能全部降权？

错误定位依赖 ToolResult。不是全局降权，而是按任务分配：rationale 查询低权重，failure/
validation 查询高权重；同时使用 error-aware window 和 reranker。

### Episode 切错怎么办？

边界记录 reasons/confidence，支持人工 split/merge；原始 Item 不变。评测 Episode Recall
和 boundary F1，并保留 Turn fallback。

### 如何处理同一任务多次失败？

用 RETRIES/SUPERSEDES 连接 Attempt；默认当前结果优先，但历史失败作为过程证据。查询
“第一次为什么失败”则按目标时间和事件顺序取旧 Attempt。

## 7. 必做消融

| Run | 变量 | 关注 |
| --- | --- | --- |
| X-B0 | flat Item hybrid | correction verified；fixed negative control；`NON_QUALIFIED` |
| X-T1 | event normalization/state truth | sole attempt `FAILED_BEFORE_PUBLISH`；`NO_RESULT / NOT_QUALIFIED / UNAVAILABLE` |
| X-B1 | type-aware fusion | tool noise；暂无 Run |
| X-B2 | Episode V2 | task recall/detail loss；暂无 Run |
| X-B3 | dense profile | semantic recovery；暂无 Run |
| X-B4 | temporal graph | retry/path；暂无 Run |
| X-B5 | Codex reranker | state hard negatives；暂无 Run |
| X-B6 | timeline context | answer faithfulness；暂无 Run |

## 8. 失败案例占位

### Episode 过度合并

- 假设：跨 Turn 合并改善上下文；
- 风险：不同目标被混合；
- 评测：boundary F1、Episode Recall、distractor；
- Run：TBD。

### ToolResult 统一降权

- 假设：降低工具噪声；
- 风险：错误和验证证据漏召；
- 改进：task-aware quota + reranker；
- Run：TBD。

## 9. 简历 Bullet

### 设计阶段

> 设计 Codex Temporal RAG，将研发会话建模为 Goal—Action—Patch—Validation—Outcome
> 时序 Episode，区分 plan、invocation、result、applied、validated 状态，并设计
> Goal/Decision、failure 和 validation 专用检索及 Context。

### 实现后

> 实现研发会话 Temporal RAG，在 `[N]` 条 Golden Query 上通过 Goal-aware Episode、
> event graph 和 state-aware reranker，将 Episode Recall@5 从 `[A]` 提升至 `[B]`、
> Patch→Validation Path Recall 达到 `[C]`，False Validated Rate 为 `[D]`，P95 为
> `[E]`。

当前禁止使用“实现后”Bullet；占位符只能在真实、可验证的 Treatment artifact 产生后替换。

## 10. 证据表

| 证据 | 当前 | 后续 |
| --- | --- | --- |
| Design | codex-source-rag-v2 | 已有 |
| Development Status | X1–X5 repository engineering `COMPLETE`；production treatment `UNAVAILABLE` | 正式 migration/production release external blocked |
| Golden Version | `codex-golden-v1/v1`；`sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1`；exact 45 | 固定 control membership |
| Original X-B0 Run | `evaluation-run://project-codex-xb0-v1/6971b8cb1702a9ece8a894915c6b6de7`；identity/security/portable PASS；truth audit FAIL | 保持 immutable |
| Authoritative Correction | `evaluation-correction://project-codex-xb0-v1/1ccecfa1c9667d0f716c709d1175d570`；exact 10；`VERIFIED_CORRECTION_NON_QUALIFIED` | 保持 fixed control |
| X-B0 Gate | persistent correction audit PASS；P0/P1=0；fixed control accepted、`NON_QUALIFIED` | 不得写成质量 PASS |
| X-T1 production attempt | audit verified `FAILED_BEFORE_PUBLISH`；无 Run/artifact/metrics/slices | `NO_RESULT / NOT_QUALIFIED / UNAVAILABLE`；retry 未授权 |
| Episode Run | 无 | X-B2 |
| Temporal Run | 无 | X-B4 |
| Rerank Run | 无 | X-B5 |
| Episode Builder | turn-boundary v1 | 文件/版本 |
| Validation | correction truth 7/10；False Validated 1/5 | X-T1 Precision/Recall |
| X1 Engineering | CX1-01/CX1-02 COMPLETE；Gate P0/P1=0 | 不等于 treatment quality |
| Identifier Fix | `adapter-command-…`；synthetic 23/static PASS | 非 released treatment evidence |
| P95 | correction 未提供权威 P95；X-T1 无 metrics | 无 |

## 11. 开发状态真值

- X0-01 Foundation 已 `PASS`，released Golden 与 X-B0/correction evidence 已产生；
- 原 X-B0 Run 只保留 identity/security/portable PASS 和 truth-audit FAIL；其原指标不得
  作为权威完整 truth；
- correction overlay 是权威 baseline truth，结论
  `VERIFIED_CORRECTION_NON_QUALIFIED`；
- X-B0 是 accepted fixed negative control，不是 qualified baseline 或优化收益；
- CX1-01、CX1-02 与 X1 engineering 已 `COMPLETE`；Gate P0/P1=0；
- 唯一 X-T1 production attempt 已被审计为
  `X-T1 PRODUCTION ATTEMPT AUDIT VERIFIED — FAILED_BEFORE_PUBLISH`；
- failed attempt 没有 Run ID/URI、set hash、metrics、slices 或 artifact；P0/P1=0 只适用
  于失败边界审计；
- `adapter-command-…` 修复后的 synthetic 23/static PASS 不是 released treatment
  evidence；
- X-T1 为 `NO_RESULT / NOT_QUALIFIED / UNAVAILABLE`，retry `NOT_AUTHORIZED`；
- X2–X5 仓库内工程 `COMPLETE / OPT_IN / DEFAULT_V1`；production treatment/release
  仍 `EXTERNAL_BLOCKED / QUALITY UNAVAILABLE`；
- 任何 engineering Gate 只证明工程契约，不自动证明 quality；
- quality treatment 不达标时必须保留负结果，不得填入提升话术；
- 当前正式库继续标记为 `EXTERNAL_MUTABLE_SERVICE_OWNED`；本次 Evidence Sync 不以其
  当前内容、哈希或 sidecar 状态为证据，也不对其是否变化作声明；5/127/6,178 只能作为
  既有设计历史观察。

## 12. 不能夸大

- Goal-aware boundary 与 Unit/edge 是 deterministic engineering contract，但没有
  production boundary quality Run；
- Decision/Alternative 仍按 observable/reviewed state 分层，不能把 reported claim
  当作 verified fact；
- 既有设计历史观察中的活跃数据没有 ValidationResult；本轮未重测正式库；
- 已有 isolated 专用 Unit/temporal graph/dense cache store；正式库尚未 migration；
- 已有确定性 task/temporal rerank/calibration，但没有独立 production quality
  qualification 或 learned reranker；
- 已有 released Codex Golden，但 X-B0 `NON_QUALIFIED`；
- 已有 X-B0/correction，不等于 X-T1 有 Treatment；
- False Validated 真实 correction truth 是 1/5，不得说保持 0；
- correct-zero 是 0/5、refusal-failure 是 5/5，不能隐藏；
- X1 engineering COMPLETE 不等于 production treatment 成功或质量通过；
- failed attempt 的临时 Adapter/normalizer/facts 执行不等于 published artifact；
- synthetic/static PASS 不得替代 released treatment；
- X2–X5 engineering 已实现；production treatment、remote model 与 release 未授权；
- 显式会话文本不等于隐藏 rationale；
- “未找到执行”不等于绝对没有执行。
