# Codex 研发会话单源 RAG 详细设计

状态：`REPOSITORY_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD`  
设计版本：codex-source-rag-v2  
基线日期：2026-07-27  
对应面试文档：[02_CODEX_SOURCE_INTERVIEW.md](../interview/02_CODEX_SOURCE_INTERVIEW.md)

> **2026-07-30 实现覆盖层**
>
> 本文正文仍是功能与退出门基线。当前显式 Codex V2 已从 Runtime/Platform 到达完整
> source facade，保留原始 Item，使用可观察事件、Episode、temporal retrieval、facts 与
> context；默认 V1 和回退不变。X-B0 权威 correction 仍是
> `NON_QUALIFIED`，X-T1 在 artifact 发布前失败且没有结果，不能据此宣称质量提升。
> 当前结论仅为仓库 L3 工程完成；真实 session replay、reviewed calibration、
> shadow/canary 仍属 L4/L5。

## 1. 来源定位

Codex Source 不是普通聊天记录，而是“研发目标—操作—变更—验证—结果”的可追溯事件域：

```text
Codex Source
└── Thread
    ├── Turn
    │   ├── UserGoal
    │   ├── AgentMessage
    │   ├── Plan
    │   ├── ToolCall
    │   ├── ToolResult
    │   ├── CommandExecution
    │   ├── Patch / FileChange
    │   ├── ValidationResult
    │   └── DevelopmentEpisode
    └── Thread-level Goal / Outcome / Open Issues（目标）
```

它回答：

- 用户想完成什么；
- Agent 采取了什么可观察操作；
- 哪些文件/补丁被修改；
- 哪些命令真实执行；
- 哪些验证有可观察退出码；
- 哪次尝试成功、失败或未完成；
- 显式消息中记录了什么决策和取舍。

内部 reasoning/agent reasoning 不属于可检索证据，系统不能通过摘要或推断重新构造隐藏
推理。

## 2. 物理入口与边界

### 2.1 当前入口

| 入口 | 当前支持 | 边界 |
| --- | --- | --- |
| `~/.codex/sessions/**/*.jsonl` | 是 | rollout 会话 |
| archived sessions | 可选 | 显式开关 |
| `codex exec --json` 公开事件 | 是 | 非交互执行事件 |
| Codex Bridge executions | 控制面已实现 | 只有形成可摄取事件后才进入 Source |
| app-server stream | 否/预留 | 未来 adapter |
| Other agent logs | 否 | 需独立适配和隐私契约 |

### 2.2 项目过滤

当前先读取小段 metadata，根据：

- cwd；
- project path；
- workspace root；
- session metadata。

匹配项目后才完整读取会话。大于 `RAG_MAX_CODEX_SESSION_BYTES` 的文件跳过并计数。

### 2.3 安全边界

- reasoning 类型排除；
- ignored roles 排除；
- secret redaction；
- Item 最大长度截断；
- 只读取 session JSONL/archived sessions；
- 不读取 auth.json、全局 history 或任意日志。

## 3. 当前实现审计

### 3.1 代码入口

| 能力 | 当前实现 |
| --- | --- |
| 会话发现、过滤和标准化 | [codex_adapter.py](../../../src/evidence_rag/codex_adapter.py) |
| Generation/Raw/Views | [codex_ingestion.py](../../../src/evidence_rag/codex_ingestion.py) |
| FTS/dense 召回 | [codex_retrieval.py](../../../src/evidence_rag/codex_retrieval.py) |
| 持久化、timeline、audit | [codex_store.py](../../../src/evidence_rag/codex_store.py) |
| Schema | [db_schema.py](../../../src/evidence_rag/db_schema.py) |
| 多会话对照 | [codex_comparisons](../../../src/evidence_rag/codex_comparisons) |
| Codex→Code 绑定 | [bindings](../../../src/evidence_rag/bindings) |
| 执行控制面 | [codex_bridge](../../../src/evidence_rag/codex_bridge) |

### 3.2 当前稳定实体

| 实体 | 核心字段 |
| --- | --- |
| CodexSource | source path、project path、active generation、ACL、status |
| CodexGeneration | adapter version、counts、validation、status |
| CodexThread | raw thread ID、title、cwd、status、time、source hash |
| CodexTurn | ordinal、status、start/end、goal、summary |
| CodexItem | sequence、type、role、status、timestamp、name、content、locator、metadata |
| DevelopmentEpisode | 当前每 Turn 一个派生 Item |

### 3.3 当前 Item 类型与内容

| Item | 内容 | 当前事实强度 |
| --- | --- | --- |
| UserGoal | 用户显式消息 | 强 |
| AgentMessage | 助手显式回复 | 陈述，不等于操作事实 |
| Plan | todo/plan update | 计划，不等于执行 |
| ToolCall | 工具名与参数的清洗文本 | 调用意图 |
| ToolResult | 工具输出 | 可观察输出，状态需解析 |
| CommandExecution | 命令及可能输出/exit code | 有 exit code 时为执行事实 |
| Patch | apply_patch/patch 调用 | 补丁请求，需结合结果 |
| FileChange | file_change/patch_apply_end | 有 success 时为变更事件 |
| ValidationResult | 验证命令 + 真实 exit code 派生 | 强验证事实 |
| DevelopmentEpisode | Goal/Commands/Files/Validation/Outcome 拼接 | 派生摘要 |

### 3.4 当前边

- HAS_TURN；
- HAS_ITEM；
- HAS_EPISODE；
- OUTPUT_OF；
- VALIDATED_BY（代码支持，但当前活跃库没有该边）。

### 3.5 当前 Search View

每个 Item 一个 View：

- `episode.summary`；
- `message.goal`；
- `message.agent`；
- `execution.command`；
- `change.file`；
- `change.patch`；
- `tool.result`；
- `tool.call`；
- `validation.result`。

全部使用同一个 `local-hash-v2` 和同一套固定融合权重。

### 3.6 当前活跃规模

| 指标 | 当前值 |
| --- | ---: |
| Active Thread | 5 |
| Active Turn | 127 |
| Active Item/Search View | 6,178 |
| ToolResult | 2,184 |
| ToolCall | 1,257 |
| CommandExecution | 996 |
| Patch | 539 |
| FileChange | 536 |
| AgentMessage | 308 |
| UserGoal | 231 |
| DevelopmentEpisode | 127 |
| HAS_ITEM | 6,051 |
| OUTPUT_OF | 2,463 |
| HAS_TURN/HAS_EPISODE | 127 / 127 |
| ValidationResult | 0 |

ToolCall + ToolResult 占约 55.7%，加上 CommandExecution 后约 71.8%。如果不按任务和状态
重排，工具噪声会主导会话召回。

### 3.7 当前优点

- reasoning 不入库；
- secret 在持久化和 embedding 前脱敏；
- 项目路径过滤；
- stable Thread/Turn/Item URI；
- Generation 原子发布；
- ToolCall 与 ToolResult 通过 call_id 关联；
- 只有验证类命令且存在真实 exit code 才派生 ValidationResult；
- Patch/FileChange 与 committed code 明确区分；
- Timeline 和 Turn audit 可回到 JSONL 行；
- 多会话对照已有持久化能力。

### 3.8 当前失败模式

| 失败 | 原因 | 后果 |
| --- | --- | --- |
| 工具噪声淹没 Goal/Outcome | 一 Item 一 View、同权重 | rationale/summary 召回偏 |
| Episode 过粗/过窄 | 每 Turn 一个 Episode | 跨 Turn 任务断裂或同 Turn 多目标混合 |
| 没有显式 Decision/Alternative | 只索引 AgentMessage | “为什么”问题弱 |
| Plan 和执行混淆 | 状态未进入排序主逻辑 | 可能把计划当完成 |
| Patch 和成功变更混淆 | Patch/ToolResult/FileChange 状态链不足 | 可能把请求当落盘 |
| 无 ValidationResult 活跃数据 | exit code/command 识别未命中当前语料 | 无法验证“测试通过” |
| 时间关系未参与候选 | 仅 date scope | 无时序路径检索 |
| 同命令/输出重复 | ToolCall、Command、ToolResult 多表示 | Context 浪费 |
| 长 ToolResult 截断 | 固定最大字符 | 关键尾部错误可能丢失 |
| 无 Codex reranker | 固定 lexical/dense | 状态、类型、时间未充分利用 |
| 比较是独立功能 | 比较快照未成为检索结构 | 多次尝试问答弱 |

## 4. 查询任务目录

### 4.1 会话/目标定位

示例：

- 哪个会话优化过 reranker？
- 用户在哪次 Turn 提出 Graph RAG？

必需角色：

- Thread/Episode；
- UserGoal；
- time/cwd。

通道：

- goal exact/sparse/dense；
- title；
- path/identifier；
- time。

### 4.2 研发过程追踪

示例：

- 这次任务按什么步骤执行？
- 修改前做了哪些调查？

必需角色：

- Goal；
- ordered actions；
- findings；
- changes；
- outcome。

关键是有序事件，不是无序 Top-K。

### 4.3 设计理由与取舍

示例：

- 为什么没有迁移 Neo4j？
- 哪些候选方案被考虑和否决？

必需角色：

- explicit decision statement；
- alternative；
- supporting observation；
- resulting action。

限制：

- 只能使用显式 User/Agent 消息、Plan、可观察结果；
- 不从 reasoning 推断；
- 没有显式依据时回答“会话未记录理由”。

### 4.4 变更追踪

示例：

- 哪次会话修改了 `retrieval.py`？
- 这次 Turn 实际修改了哪些文件？

必需角色：

- Patch/FileChange；
- success/result；
- paths；
- linked code/commit if confirmed。

### 4.5 命令与验证

示例：

- 哪些测试真实运行并通过？
- 修改后有没有跑 typecheck？

必需角色：

- CommandExecution；
- linked ToolResult；
- exit code；
- ValidationResult；
- target patch/version if known。

禁止：

- 把 AgentMessage“测试通过”当验证；
- 把命令文本出现 `pytest` 当已执行成功。

### 4.6 失败诊断

示例：

- 哪次尝试失败，错误是什么？
- 第一次失败后做了什么修复？

必需角色：

- failed command/tool；
- error window；
- following action；
- later outcome；
- superseded status。

### 4.7 多次尝试比较

示例：

- 三次会话中哪种方案最终成功？
- 两个 Thread 修改文件和验证有何不同？

必需角色：

- normalized goal；
- decisions；
- changed files；
- commands；
- validations；
- outcomes；
- unresolved。

### 4.8 未完成事项

示例：

- 哪些任务还没完成？
- 哪些 Plan 没有对应执行？

必需角色：

- plan/goal；
- absence of completion relation；
- thread/turn status；
- explicit unresolved statement。

“未找到执行”只能表述为当前证据缺失，不能证明绝对未执行。

## 5. 目标事件模型

### 5.1 原始实体不变

CodexThread、CodexTurn、CodexItem 保留为原始证据。

### 5.2 派生实体

| 实体 | 说明 |
| --- | --- |
| DevelopmentEpisodeV2 | 可跨 Turn 的单一研发目标片段 |
| Goal | 规范化但引用原 UserGoal |
| DecisionCandidate | 显式文本抽取的候选决策 |
| AlternativeCandidate | 显式候选方案 |
| Observation | Tool/Command 的可观察结果 |
| ChangeSet | 同一 Episode 的 Patch/FileChange 集合 |
| ValidationFact | exit code 驱动的验证事实 |
| FailureEvent | failed/timeout/error 的规范事件 |
| Outcome | completed/partial/failed/unresolved |
| OpenIssue | 显式未完成项 |

Decision/Alternative 默认是 `derived_candidate`，除非来源文本本身明确且可引用；不能伪装为
隐藏思维链。

### 5.3 状态机

#### Action

```text
proposed → invoked → completed
                   ↘ failed / timeout / cancelled / unknown
```

#### Change

```text
patch_proposed → apply_invoked → applied
                             ↘ failed
applied → committed（跨源确认后）
```

#### Validation

```text
mentioned
→ command_invoked
→ exit_observed
→ passed / failed
→ target_bound / target_unknown
```

### 5.4 关系

- HAS_TURN；
- HAS_ITEM；
- HAS_EPISODE；
- FOLLOWS；
- PURSUES；
- CONSIDERS；
- DECIDES；
- EXECUTES；
- OUTPUT_OF；
- OBSERVES；
- PRODUCES_PATCH；
- APPLIES_CHANGE；
- CHANGES_FILE；
- VALIDATES；
- FAILS_ON；
- RETRIES；
- RESOLVES；
- SUPERSEDES；
- LEAVES_OPEN；
- DERIVED_FROM。

每条派生边记录 event/item locator。

## 6. Episode V2

### 6.1 为什么不能固定每 Turn 一个 Episode

- 一个任务可能跨多个 Turn；
- 一个 Turn 可能包含多个独立子目标；
- retry/修复可能在下一 Turn；
- compaction/summary 可能改变消息边界；
- subagent/child thread 可能属于同一目标。

### 6.2 边界信号

硬信号：

- 新 UserGoal；
- thread boundary；
- explicit task completion/failure；
- cwd/project change；
- 长时间间隔。

软信号：

- changed file set Jaccard；
- identifier/topic similarity；
- plan continuation；
- command phase；
- previous unresolved；
- call/result continuity。

### 6.3 Episode Builder

第一版确定性：

1. 每个 UserGoal 开新 Episode；
2. 后续无新 Goal 的 Turn 合并到最近 Episode；
3. cwd/project 改变强制切分；
4. 间隔超过阈值且无 continuation 切分；
5. File/identifier overlap 为合并信号；
6. builder 输出 confidence 和 boundary reasons；
7. 支持人工 split/merge，保留原始版本。

第二版可训练 boundary classifier，但仍不能改写原始事件。

## 7. Retrieval Unit

| Unit | 检索内容 | Context Ref |
| --- | --- | --- |
| `thread.surface` | title、goals、time、cwd、outcomes | Thread |
| `episode.surface` | goal、decisions、files、validation、outcome | Episode |
| `goal.atomic` | 单个 UserGoal | UserGoal |
| `decision.atomic` | explicit decision + alternatives | source messages |
| `action.command` | normalized command、status、tool | call/result pair |
| `change.surface` | paths、patch summary、success | Patch/FileChange |
| `validation.fact` | command、framework、exit、status | raw command/result |
| `failure.window` | error head/tail、command、next action | ordered events |
| `plan.delta` | plan item + later status | plan/action links |
| `outcome.atomic` | completed/partial/open | evidence items |

### 7.1 ToolResult 处理

不对所有 ToolResult 全文同等建索引：

- 命令输出：command + error-aware head/tail；
- 文件读取：path + relevant content window；
- search result：query + result titles/snippets；
- structured result：优先保留字段；
- duplicate output：content hash 去重；
- truncated output：保留 truncation flag 和 raw locator。

## 8. 索引

### 8.1 Exact/Structured

- thread ID；
- turn ordinal；
- time range；
- cwd/project；
- item type/status；
- tool name；
- call ID；
- command/framework/exit；
- changed path；
- code symbol/commit binding；
- model/agent/subagent metadata。

### 8.2 Sparse

字段：

- Goal/Decision 权重最高；
- paths/commands/identifiers；
- Episode outcome；
- error text；
- ToolResult 正文最低。

### 8.3 Dense Profile

| Profile | 用途 |
| --- | --- |
| goal_semantic | 自然语言目标 |
| rationale_semantic | 显式决定/方案 |
| episode_semantic | 任务级召回 |
| failure_semantic | 错误与修复 |
| change_semantic | 自然语言→变更 |

### 8.4 Temporal Index

```text
thread/episode/item
→ started_at/completed_at/timestamp
→ previous/next
→ time bucket
```

时间不是简单 freshness boost：

- “最近”查询需要 freshness；
- “第一次”按正序；
- “修改后”需要 event order；
- “历史某天”需要 target window。

## 9. Query Profile

```python
CodexQueryProfile(
    task="validation_trace",
    thread_ids=[],
    time_range=None,
    paths=["src/evidence_rag/retrieval.py"],
    item_types=["CommandExecution", "ValidationResult"],
    statuses=["passed", "failed"],
    require_exit_code=True,
    traversal=["PRODUCES_PATCH", "VALIDATES"],
    order="event",
)
```

## 10. Candidate Generation

### 10.1 通道

- thread/turn/item exact；
- path/command/tool exact；
- Goal/Decision sparse+dense；
- Episode sparse+dense；
- failure/error sparse+dense；
- temporal window；
- event graph；
- Code binding reverse lookup；
- comparison-derived candidates。

### 10.2 任务配额

#### Rationale

- Goal/Decision/Alternative 50%；
- Observation/Outcome 25%；
- Change/Command 15%；
- ToolResult 10%。

#### Change Trace

- Patch/FileChange 40%；
- Goal/Episode 20%；
- Command/ToolResult 20%；
- Code/Commit bindings 20%。

#### Validation

- ValidationFact 40%；
- Command/ToolResult pair 35%；
- ChangeSet 15%；
- Goal/Episode 10%。

没有 ValidationFact 时不能用其他类型填满“验证”配额。

### 10.3 来源内融合

第一版：

- exact filters；
- type-aware RRF；
- event status gate；
- episode/item dedup。

第二版：

- Codex reranker；
- relevance calibration；
- adaptive-k。

## 11. Codex Reranker

### 11.1 特征

- query-task/item-type match；
- Goal similarity；
- Episode membership；
- event distance/order；
- explicit path/command/tool match；
- call/result completeness；
- observed exit code；
- patch apply success；
- later superseded；
- current vs archived；
- Code binding status；
- duplicate/raw-vs-summary；
- truncated/redacted。

### 11.2 负面标签

- plan_only；
- assistant_claim_only；
- call_without_result；
- patch_without_success；
- validation_without_exit；
- superseded_attempt；
- duplicate_tool_output；
- different_project；
- unrelated_same_path；
- truncated_missing_evidence。

### 11.3 输出

```text
relevance_probability
evidence_role
event_status
necessity
negative_reason
```

## 12. Context Builder

### 12.1 过程追踪

```text
Thread / Episode
Goal
Ordered Actions
  time → action → observable result
Changes
Validations
Outcome
Open Issues
```

### 12.2 Rationale

```text
Question
Explicit Goal
Explicit Decision/Alternative Statements
Observations Cited by Those Statements
Resulting Change
Missing Rationale Warning
```

不输出隐藏 reasoning。

### 12.3 Validation

```text
Change/Patch
Command
ToolResult
Exit Code
Framework
Target Binding
Status
Version/Target Unknown Warning
```

### 12.4 Failure/Retry

```text
Attempt N
Command/Tool
Failure Window
Next Action
Later Validation/Outcome
Superseded?
```

### 12.5 多会话比较

每个 Thread 使用相同 schema：

- normalized goal；
- decisions；
- files；
- commands；
- validations；
- outcome；
- open issues。

先结构对齐再生成差异，不能直接让 LLM 对比全部聊天文本。

## 13. 事实和推断

| 内容 | 状态 |
| --- | --- |
| UserGoal 原文 | observed |
| ToolCall 记录 | observed invocation |
| ToolResult 原文 | observed output |
| exit code | observed fact |
| ValidationResult | derived deterministic |
| AgentMessage 声称完成 | explicit claim |
| Episode Summary | derived summary |
| Decision/Alternative 抽取 | derived candidate |
| Plan 未找到执行 | insufficient linkage |
| Patch 已提交 | 只有跨源 confirmed binding 才成立 |

## 14. 增量摄取

### 14.1 Session Append

当前全 generation 重建可保留作为正确性 fallback；V2 增加：

- source hash/size/last line；
- append-only cursor；
- last complete JSONL event；
- changed Thread/Turn/Episode；
- deleted/archived session tombstone；
- adapter/cleaning/episode builder versions。

### 14.2 派生重建

原始 Item 未变化时：

- cleaning version 变：重建 content/view；
- Episode builder 变：只重建 Episode；
- embedding 变：只重建 vector；
- Decision extractor 变：重建 candidate；
- Code binding 变：关系更新，不复制 Item。

## 15. ACL、隐私和安全

- Source/Thread/Item 继承 ACL；
- graph traversal 两端授权；
- remote embedding/generation 发送前再脱敏；
- internal reasoning 永不建 Unit；
- redacted/truncated 必须进入 Context warning；
- shell output 中 secret 不写 Trace；
- source_file 仅内部 locator，不向无权用户暴露绝对路径；
- archived session 遵循 retention；
- deletion/tombstone 传播到 summaries、vectors、comparison。

## 16. Golden Set

### 16.1 首版 45 条

| Slice | 数量 |
| --- | ---: |
| thread/goal location | 6 |
| process trace | 6 |
| rationale/decision | 6 |
| patch/file change | 6 |
| command/validation | 7 |
| failure/retry | 5 |
| multi-thread comparison | 4 |
| unresolved/unanswerable/privacy | 5 |

### 16.2 Hard Negatives

- 同路径不同 Thread；
- 同一 Goal 的失败旧尝试；
- Plan 中提及但未执行；
- AgentMessage 声称通过但无 exit code；
- Patch 调用但 ToolResult 失败；
- 只读取文件但未修改；
- 同一 command 多次运行、状态不同；
- archived/current duplicate；
- truncated ToolResult；
- subagent 与主 Thread 同主题。

### 16.3 指标

#### Retrieval

- Thread Recall@5；
- Episode Recall@5；
- Item Recall@10；
- MRR；
- Goal/Decision Recall；
- harmful old-attempt rate。

#### Temporal/State

- event order accuracy；
- call/result pairing；
- patch apply status accuracy；
- validation fact precision/recall；
- false validated rate；
- retry/supersede path recall；
- outcome accuracy。

#### Context

- Goal→Action→Change→Validation path coverage；
- duplicate ratio；
- tool noise ratio；
- missing-rationale correctness；
- token per event chain。

#### System

- incremental sync latency；
- session parsing throughput；
- P95 retrieval；
- vector/storage per 1k items；
- redaction leakage；
- unauthorized leakage。

## 17. 消融

| Run | 变量 |
| --- | --- |
| X-B0 | 当前 Item flat hybrid |
| X-B1 | type-aware weights/RRF |
| X-B2 | Episode V2 |
| X-B3 | neural dense profiles |
| X-B4 | temporal/event graph |
| X-B5 | Codex reranker |
| X-B6 | state-aware Context |
| X-B7 | adaptive-k |

重点报告：

- Episode 是否提升任务召回但丢失细节；
- ToolResult 降权是否损害错误定位；
- temporal graph 是否恢复 retry；
- reranker 是否排除 assistant-claim-only。

## 18. 发布门槛

| 指标 | Target |
| --- | ---: |
| Thread Recall@5 | ≥ 0.90 |
| Episode Recall@5 | ≥ 0.85 |
| Event-order Accuracy | ≥ 0.98 |
| Call/Result Pair Accuracy | ≥ 0.98 |
| Patch Status Accuracy | ≥ 0.98 |
| Validation Precision | 1.00 |
| Patch→Validation Path Recall | ≥ 0.85 |
| False Validated Rate | 0 |
| Duplicate Context Ratio | ≤ 0.15 |
| Redaction/Unauthorized Leakage | 0 |
| P95（当前规模） | ≤ 1.5 s |

## 19. 实施映射

### 19.1 新模块

```text
src/evidence_rag/rag/sources/codex/
  contracts.py
  event_normalizer.py
  episode_builder.py
  state_machine.py
  unit_builder.py
  exact_index.py
  temporal_index.py
  retriever.py
  reranker.py
  context_builder.py
  evaluator.py
```

### 19.2 Schema

- `codex_episode_versions`；
- `codex_episode_members`；
- `codex_derived_facts`；
- `codex_retrieval_units`；
- `codex_event_links`；
- `codex_relation_diagnostics`；
- `codex_retrieval_calibration`。

### 19.3 Feature Flags

```text
RAG_CODEX_EPISODE=turn-v1|goal-temporal-v2
RAG_CODEX_UNIT=item-v1|event-v2
RAG_CODEX_EMBEDDING=local-hash-v2|profile
RAG_CODEX_EVENT_GRAPH=false|true
RAG_CODEX_RERANKER=off|profile
RAG_CODEX_CONTEXT=snippet-v1|timeline-v2
```

### 19.4 Sprint

#### X0：Golden 和状态基线

- 45 条 Golden；
- 当前 flat baseline；
- 验证/patch hard negatives。

#### X1：Event Normalization

- call/result/change/validation state；
- output window；
- dedup。

#### X2：Episode V2

- boundary builder；
- split/merge audit；
- Retrieval Unit。

#### X3：Retriever/Reranker

- exact/sparse/dense/temporal；
- event graph；
- rerank/calibration。

#### X4：Context/Comparison

- timeline context；
- rationale；
- failure/retry；
- normalized comparison。

#### X5：Incremental/Release

- append sync；
- privacy；
- shadow/canary/rollback。

## 20. 测试计划

- reasoning event exclusion；
- secret redaction before embedding；
- JSONL malformed/partial line；
- max file skip；
- project path mismatch；
- ToolCall/Result call_id pairing；
- command exit code nested formats；
- validation command allowlist；
- AgentMessage false validation；
- patch apply success/failure；
- multi-turn Episode；
- new Goal split；
- retry/supersede；
- archived/current duplicate；
- truncated error tail；
- ACL timeline/graph leakage；
- incremental append idempotency。

## 21. 完成定义

1. 八类 Codex 查询有 Golden Case；
2. Episode 不再固定等于 Turn；
3. Plan/claim/invocation/result/validation 状态分开；
4. Goal/Decision、change、failure、validation 有专用 Unit；
5. temporal/event graph 参与召回；
6. Codex reranker 有 hard-negative 消融；
7. false validated 为 0；
8. rationale 不推断隐藏 reasoning；
9. Context 是有序事件链；
10. 增量、隐私、SLO、回滚通过；
11. 面试文档填入真实 Run。
