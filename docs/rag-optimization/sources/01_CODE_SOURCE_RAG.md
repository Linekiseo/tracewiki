# Code / Git / Validation 单源 RAG 详细设计

状态：`REPOSITORY_L3_ENGINEERING_COMPLETE / DEFAULT_V1 / QUALITY_HOLD`  
设计版本：code-source-rag-v2  
基线日期：2026-07-27  
对应面试文档：[01_CODE_SOURCE_INTERVIEW.md](../interview/01_CODE_SOURCE_INTERVIEW.md)
对应开发方案：[01_CODE_SOURCE_DEVELOPMENT_PLAN.md](../development/01_CODE_SOURCE_DEVELOPMENT_PLAN.md)

> **2026-07-30 实现覆盖层**
>
> 本文正文仍是功能与退出门基线。当前 Code 的 exact/sparse/dense/graph/history/test/context
> 完整管线已由 Runtime 构造，并可经显式 V2 请求从 production API 到达；默认仍为 V1，
> 请求级 fallback 与 `HOLD_DEFAULT_V1` 保留。历史 C-B1 及其后固定的组件摘要已被后续
> 合法 P0 实现变更触发 fail-closed，因此它们不是当前版本的质量资格证明。缺少重新授权
> 的真实 replay、reviewed calibration、SLO、shadow/canary，故这里只表示仓库 L3 工程
> 完成，不表示 release candidate 或生产发布。

## 1. 来源定位

Code Source 不是“源码文本集合”，而是一个带版本和验证事实的代码证据域：

```text
Repository Snapshot
├── Current Worktree / Commit
│   ├── FileVersion
│   ├── CodeSymbol
│   └── Static Relations
├── Git History
│   ├── Commit / Parent
│   ├── Branch / Tag（目标）
│   └── DiffHunk / Rename
├── Validation
│   ├── TestResult
│   ├── Lint / Typecheck
│   └── CI Result（目标）
└── Cross-source Bindings
    ├── Codex FileChange → FileVersion/Symbol
    └── Codex Patch → Commit
```

Code Source 的权威问题是：

- 某个版本实际包含什么代码；
- 某个 Symbol 在哪里定义、被谁引用；
- 某次变更修改了什么；
- 哪些测试对哪个版本产生了什么真实结果。

“为什么这么改”不属于 Code Source 的完整答案，由 Codex/Workspace 补充；但 Commit
message 和 Diff 可以作为有限的代码历史上下文。

## 2. 物理入口与边界

### 2.1 当前入口

| 入口 | 当前支持 | 内容 |
| --- | --- | --- |
| Local Git | 是 | 当前工作树、Git metadata、历史对象 |
| Remote Git URL | 是 | clone 后的 snapshot/history |
| Plain Folder | 是 | 无 Git 的 worktree snapshot |
| Manual TestResult | 是 | command、status、exit code、duration、stdout/stderr ref |
| Codex Binding Scan | 是 | FileChange/Patch 到 File/Commit 的候选 |
| CI Webhook | 否 | 未来作为 Validation Adapter |
| SCIP/LSP Index | 否 | 未来作为精确语义关系 Adapter |
| Coverage Report | 否 | 未来作为 TESTS/COVERS Adapter |

### 2.2 不属于 Code Source 的内容

- Codex 对代码的自然语言讨论：属于 Codex Source；
- Run 的模型指标：属于 Experiment Source；
- Notebook Cell：属于 Notebook Source；
- 报告中的代码描述：属于 Document Source；
- Topic/Iteration 状态：属于 Workspace Source。

跨源绑定只能建立关系，不能复制后伪装成 Code 的确定性事实。

## 3. 当前实现审计

### 3.1 代码入口

| 能力 | 当前实现 |
| --- | --- |
| Repository 解析 | [repository.py](../../../src/evidence_rag/repository.py) |
| Tree-sitter 解析 | [parser.py](../../../src/evidence_rag/parser.py) |
| 实体、边和 Search View | [ingestion.py](../../../src/evidence_rag/ingestion.py) |
| FTS/dense 混合检索 | [retrieval.py](../../../src/evidence_rag/retrieval.py) |
| Generation 和 Scope | [storage.py](../../../src/evidence_rag/storage.py) |
| Commit/Diff/Test | [code_history](../../../src/evidence_rag/code_history) |
| Codex→Code 候选 | [bindings](../../../src/evidence_rag/bindings) |

### 3.2 当前稳定实体

| 实体 | 当前主键/版本 | 核心内容 |
| --- | --- | --- |
| Repository | repo identity | name、source、branch、active generation、ACL |
| Commit Snapshot Entity | repository + snapshot ref | 当前 commit 或 dirty worktree |
| FileVersion | repository + commit/worktree + path | 完整文件、language、blob hash、lines |
| CodeSymbol | FileVersion + qualified name/signature discriminator | kind、body、lines、calls/references name |
| GitCommit | repository + SHA | parents、author hash、time、message、tree |
| GitBranch | repository + name | head SHA、default |
| DiffHunk | commit + path + hunk | patch、old/new lines、change type |
| TestResult | repository + commit + event | command、status、exit code、duration、output refs |
| BindingCandidate | source change + repo/path/target | derivation、confidence、review status |

### 3.3 当前边

当前 snapshot 图：

- HAS_COMMIT；
- CONTAINS；
- DEFINES；
- IMPORTS；
- CALLS；
- REFERENCES。

当前历史/平台图：

- parent；
- contains_diff；
- 人工确认的 `changed_path_maps_to` / `applied_as` 等绑定关系。

### 3.4 当前 Search View

- `file.raw`：完整文件；
- `symbol.raw`：kind、qualified name、path、language、完整 Symbol。

当前召回：

- FTS5；
- `local-hash-v2`；
- name/path 规则；
- 固定权重；
- Top-K 后附加边。

### 3.5 当前活跃规模

| 指标 | 当前值 |
| --- | ---: |
| Repository | 1 |
| FileVersion | 192 |
| CodeSymbol | 1,406 |
| Search View | 1,598 |
| Code Edge | 4,712 |
| CALLS | 2,052 |
| REFERENCES | 815 |
| IMPORTS | 246 |
| 未解析 CALLS | 约 6,273 |
| 未解析 REFERENCES | 约 15,845 |
| 未解析 IMPORTS | 约 531 |

### 3.6 当前优点

- Stable URI 带 repository、commit/worktree、path、symbol；
- Dirty Worktree 不伪装成 clean commit；
- Generation 构建失败不覆盖 active index；
- `.gitignore` 和 secret isolation 已进入摄取；
- Symbol 行号和 locator 可回跳；
- CALLS/REFERENCES 使用保守唯一性，不把常见同名方法强行绑定；
- Patch→Commit 需要显式 SHA 或 Diff 证据，人工确认后才成为关系；
- TestResult 记录真实 exit code，助手文本不等于验证。

### 3.7 当前主要失败模式

| 失败 | 原因 | 影响 |
| --- | --- | --- |
| 低词面重合召回失败 | 本地 hash 特征，不是代码语义模型 | 自然语言找实现弱 |
| File/Symbol 重复 | 两种 raw view 内容重叠 | Top-K 浪费 |
| 长 Symbol 稀释 | 整个函数/类一个向量 | 局部逻辑难召回 |
| Graph 不能救回候选 | Graph 在 Top-K 后使用 | 跨文件依赖漏召 |
| 调用图覆盖低 | 简单 name + local/import unique | 多态、receiver、跨包失败 |
| 历史代码不可正常检索 | active generation Scope | as-of/旧实现不完整 |
| Diff 与 Symbol 弱关联 | `affected_symbols_json` 未形成完整物化 | 影响分析弱 |
| Test 与 Symbol 无路径 | TestResult 主要挂 commit | 相关测试定位弱 |
| `max_hops` 无真实检索效果 | 查询参数未接入 traversal | 配置与行为不一致 |
| 无代码 reranker | 固定 fusion 后直接 Top-K | hard negative 难消除 |

## 4. Code Source 查询任务目录

### 4.1 精确定位

示例：

- `HybridRetriever` 定义在哪里？
- `src/evidence_rag/retrieval.py` 有哪些 Symbol？
- commit `abc123` 是否存在？

必需角色：

- exact entity；
- exact version；
- locator。

通道：

- exact identifier/path/SHA；
- FTS；
- 不需要 graph/dense，除非 exact 失败。

拒答：

- entity 不在目标 Scope；
- commit 不存在或未同步；
- 多个同名 Symbol 且用户未指定。

### 4.2 当前实现解释

示例：

- 当前混合检索如何计算分数？
- 当前图关系怎样构建？

必需角色：

- target symbol/block；
- enclosing scope；
- direct dependencies；
- current version。

允许路径：

- DEFINES；
- IMPORTS；
- CALLS；
- TYPE_OF/RETURNS（目标）。

### 4.3 使用与调用链

示例：

- 谁调用 `PlatformService.search`？
- 从 API 到 HybridRetriever 的路径是什么？

必需角色：

- target；
- callers；
- route/entry；
- path explanation。

允许路径：

- CALLS reverse；
- REFERENCES reverse；
- ROUTES_TO；
- RESOLVES_TO。

### 4.4 Bug 定位

示例：

- `wrong version` 评测失败可能位于哪些实现？
- 这个 stack trace 对应哪个 Symbol？

必需角色：

- failure/error；
- suspect symbol；
- call/data path；
- related test；
- version。

通道：

- error/stack exact；
- test name；
- sparse/dense；
- TESTS/COVERS reverse；
- CALLS/REFERENCES；
- recent Diff。

### 4.5 影响分析

示例：

- 修改 `EdgeRecord` 会影响哪些模块和测试？
- 删除字段后哪些调用者需要更新？

必需角色：

- changed target；
- reverse dependencies；
- public/API boundary；
- tests；
- unresolved/uncertain paths。

允许路径：

- CALLS reverse；
- REFERENCES reverse；
- TYPE_OF reverse；
- IMPORTS reverse；
- TESTS reverse；
- CONFIGURES reverse。

### 4.6 代码修改上下文

示例：

- 为检索增加 reranker 需要看哪些代码？
- 应在哪些文件实现 `VectorIndex`？

必需角色：

- target implementation；
- interface/type；
- callers/callees；
- configuration；
- tests；
- local conventions。

### 4.7 历史与 Diff

示例：

- `HybridRetriever` 何时改成 v2？
- 当前实现和 `abc123` 有什么差异？

必需角色：

- target commit；
- parent/current commit；
- relevant DiffHunk；
- affected symbol；
- symbol lineage。

### 4.8 测试与验证

示例：

- 当前 commit 执行过哪些测试？
- 哪些测试覆盖检索模块？

必需角色：

- test command/result；
- exact commit；
- target code relation；
- output locator。

必须区分：

- 测试被执行；
- 测试通过；
- 测试覆盖目标代码；
- 测试适用于当前 commit。

## 5. 目标领域模型

### 5.1 Stable Entity 保留

现有 Repository、FileVersion、CodeSymbol、GitCommit、DiffHunk、TestResult 保留，不因
chunk 或 embedding 改变 ID。

### 5.2 新增或补强实体

| 实体 | 目的 |
| --- | --- |
| CodeRetrievalUnit | AST 小块检索，不替代 CodeSymbol |
| SymbolVersionLineage | 同一逻辑 Symbol 跨 commit 的版本链 |
| TypeEntity | 规范化类型、接口、外部依赖类型 |
| Package/Module | 跨语言统一包/模块层 |
| Route/EntryPoint | HTTP/CLI/job/consumer 入口 |
| ConfigKey | 配置项定义和使用 |
| Dependency | package/module 外部依赖 |
| ValidationTarget | 测试选择器或验证目标 |
| CIExecution | 未来 CI job/suite 事实 |

### 5.3 新增关系

#### 结构关系

- DECLARES；
- MEMBER_OF；
- NESTED_IN；
- EXPORTS；
- IMPORTS；
- DEPENDS_ON。

#### 类型关系

- TYPE_OF；
- ACCEPTS；
- RETURNS；
- IMPLEMENTS；
- EXTENDS；
- OVERRIDES；
- INSTANTIATES；
- RESOLVES_TO。

#### 行为关系

- CALLS；
- REFERENCES；
- READS；
- WRITES；
- FLOWS_TO；
- RAISES；
- HANDLES；
- ROUTES_TO；
- CONFIGURES。

#### 工程关系

- TESTS；
- COVERS；
- VALIDATED_BY；
- CHANGED_BY；
- AFFECTS；
- RENAMED_TO；
- SAME_SYMBOL_AS；
- INTRODUCED_IN；
- REMOVED_IN。

## 6. Retrieval Unit 设计

### 6.1 Unit 类型

| Unit | 内容 | 典型用途 |
| --- | --- | --- |
| `symbol.signature` | kind、qualified name、signature、doc | 精确/语义定位 |
| `symbol.ast_block` | 父信息 + 当前 AST block | 局部实现 |
| `class.surface` | class doc、fields、method signatures | 类职责 |
| `file.surface` | module doc、imports、exports、top-level symbols | 文件职责 |
| `module.surface` | package path、public symbols、summary | 跨文件导航 |
| `diff.hunk` | commit/path/hunk/changed symbols | 历史查询 |
| `commit.surface` | message、files、symbols、parents | 变更主题 |
| `test.surface` | test name、command、target、status | 验证查询 |
| `error.window` | stack/error + relevant output window | Bug 定位 |

### 6.2 AST 递归切分

1. Symbol 低于 600 tokens：生成 `signature` 和一个 `ast_block`；
2. 超长函数：按 compound statement/branch/loop/try/internal function 递归；
3. 超长类：`class.surface` 不内联全部方法，每个方法独立；
4. 每个 AST block 目标 80–400 tokens；
5. 小于 80 tokens 的连续同父 block 合并；
6. 超过 400 tokens 继续递归，无法递归时按语句边界切分；
7. 代码生成文件、vendor、minified 只生成 file metadata，不做普通 AST chunk；
8. parse error 文件回退到 language-aware line block，并标记质量。

### 6.3 Retrieval Text

```text
language: Python
repository: rag
path: src/evidence_rag/retrieval.py
symbol: evidence_rag.retrieval.HybridRetriever.search
kind: method
signature: def search(self, request: EvidenceSearchRequest) -> dict[str, Any]
parent: HybridRetriever
doc: ...
used_identifiers: lexical_search dense_candidates similarity

<selected AST block>
```

### 6.4 Comprehension Context Ref

Unit 只保存：

- parent symbol ID；
- AST path；
- source lines；
- required imports/types；
- direct relation IDs。

命中后再加载完整理解上下文，避免 embedding 重复父代码。

## 7. 索引设计

### 7.1 Exact Index

字段：

- repository ID/name；
- branch/tag/commit SHA；
- normalized path/basename/extension；
- symbol name/qualified name/signature/kind；
- package/module；
- test name；
- config key；
- error code。

Exact 优先级：

```text
full URI > full SHA > qualified name > exact path > symbol name > basename
```

### 7.2 Sparse Index

字段权重建议作为初始值：

| 字段 | 权重 |
| --- | ---: |
| qualified_name | 4.0 |
| signature | 3.0 |
| path/module | 2.5 |
| identifiers | 2.0 |
| doc/comment | 1.5 |
| body | 1.0 |
| commit message | 1.2 |
| diff added/removed | 1.3 |

代码 tokenizer 需要拆分：

- snake_case；
- camelCase/PascalCase；
- dotted qualified name；
- path segments；
- operator/error tokens；
- 中文 n-gram。

### 7.3 Dense Index

必须使用 Profile，而不是全局 model ID：

| Profile | Query/Document |
| --- | --- |
| code_nl | 自然语言问题 ↔ code retrieval unit |
| code_code | code/signature ↔ code |
| code_history | 变更描述 ↔ commit/diff |
| error_code | error/log ↔ code/test |

首轮 benchmark：

- `local-hash-v2` 当前基线；
- Qwen3-Embedding 0.6B；
- BGE-M3；
- code-specific embedding。

### 7.4 Graph Index

SQLite 主存关系，新增派生 adjacency：

```text
(generation/version, source_id, edge_type, direction)
→ target_id, confidence, derivation, locator
```

按 edge type/direction 前置过滤，不在查询时扫描所有边。

### 7.5 Historical Index

当前 snapshot 与历史分开 namespace：

- `code/current/{repo}/{active_generation}`；
- `code/history/{repo}/{commit-range}`。

历史索引按需物化：

1. active/current 永久；
2. 最近 N commits 的 diff/commit surface；
3. 被 Claim/Run/Codex 关系引用的 commit；
4. 用户显式查询的 commit；
5. 热点历史 Symbol lineage。

## 8. 关系构建策略

### 8.1 Derivation 分层

| 层 | 来源 | 默认关系状态 |
| --- | --- | --- |
| D0 | Git/tree/file ownership | deterministic |
| D1 | Tree-sitter syntax | machine_confirmed_syntax |
| D2 | SCIP/LSP/compiler resolution | machine_confirmed_semantic |
| D3 | coverage/test report | machine_confirmed_runtime |
| D4 | path/diff heuristic | candidate |
| D5 | LLM inference | candidate |
| D6 | human review | human_confirmed |

语义邻近不是代码事实关系。

### 8.2 多语言策略

- Python：Tree-sitter + Pyright/Jedi 或 SCIP adapter；
- TypeScript/JavaScript：TypeScript Language Service/SCIP；
- Java/Kotlin：compiler/LSP/SCIP；
- Go：gopls/SCIP；
- Rust：rust-analyzer/SCIP；
- 其他语言保持 syntax layer，不能宣称语义解析。

Adapter 输出统一 `SymbolOccurrence/Relationship`，领域层不直接依赖某语言服务器。

### 8.3 Unresolved 作为一等诊断

每种边记录：

- attempted；
- resolved；
- ambiguous；
- external；
- parse/type unavailable；
- capped；
- resolver version。

Graph recall 的改进必须同时报告 published edge precision 抽样，不能只追求边数量。

## 9. Query Rewrite 与任务路由

### 9.1 实体抽取

确定性识别：

- code URI；
- SHA；
- path；
- stack frame；
- symbol；
- error；
- test selector；
- branch/tag；
- language。

### 9.2 Query Profile

```python
CodeQueryProfile(
    task="impact_analysis",
    target_identifiers=["EdgeRecord"],
    target_paths=[],
    target_ref="current",
    directions=["incoming"],
    edge_types=["REFERENCES", "TYPE_OF", "CALLS", "TESTS"],
    max_hops=3,
    require_tests=True,
    include_history=False,
)
```

### 9.3 路由

| 任务 | exact | sparse | dense | graph | history | test |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| exact location | 高 | 中 | 低 | 低 | 可选 | 否 |
| implementation | 高 | 高 | 高 | 中 | 否 | 可选 |
| call path | 中 | 中 | 中 | 高 | 否 | 否 |
| bug localization | 中 | 高 | 高 | 高 | 中 | 高 |
| impact | 高 | 中 | 中 | 高 | 可选 | 高 |
| change context | 中 | 中 | 高 | 高 | 中 | 高 |
| historical | 高 | 高 | 中 | 中 | 高 | 可选 |

## 10. Candidate Generation

### 10.1 初始预算

复杂任务总候选 120：

- exact 20；
- sparse 40；
- dense 40；
- graph seeds/paths 40；
- history/test 20；
- union 后去重，通道预算可以重叠。

简单查询总候选 30–50。

### 10.2 来源内融合

第一阶段：

- channel-specific filtering；
- weighted RRF；
- exact boost；
- wrong-version hard gate；
- entity/unit 去重。

第二阶段：

- code reranker；
- relevance calibration。

### 10.3 Graph Traversal

Seed：

- exact/sparse/dense top 5–10；
- stack/test 映射；
- explicit target。

Traversal：

```text
beam width: 20
max hops: profile-defined 1–4
cycle: forbidden unless lineage
edge type: whitelist
version: exact/current/as-of
minimum edge confidence: profile-defined
ACL: before expansion
```

路径分：

```text
seed_prob
× product(edge_confidence)
× task_edge_weight
× 0.72^hop
× version_factor
× direction_factor
```

### 10.4 去重

- 同一 AST Unit；
- 同一 CodeSymbol；
- File/Symbol parent overlap；
- current/historical 不混合；
- caller/callee 多路径只保留最强路径，其他路径作为 explanation；
- generated/vendor duplicates。

## 11. Code Reranker

### 11.1 输入

- original query；
- task/profile；
- candidate signature/block；
- path/module；
- matched channels；
- graph path summary；
- version；
- test/history status。

### 11.2 特征

- exact identifier/path；
- semantic relevance；
- symbol kind/task match；
- same module/package；
- graph distance/type；
- edge confidence；
- test relation；
- version match；
- current vs deprecated；
- duplicate parent；
- generated code；
- candidate quality/parse error。

### 11.3 输出

```text
relevance_probability
necessity: target / dependency / test / history / background
negative_reason: wrong_version / same_name / generated / duplicate / unrelated
```

### 11.4 训练/标注

Golden Candidate 四级：

- 2 必需；
- 1 有帮助；
- 0 无关；
- -1 有害。

Hard negatives：

- 同名不同模块；
- 同文件历史版本；
- 接口与错误实现；
- 相似测试名；
- ToolResult 中提到但未修改的文件；
- generated/vendor code；
- commit message 相关但 Diff 无关。

## 12. Code Context Builder

### 12.1 当前实现解释模板

```text
Target
  signature + doc + selected AST blocks
Enclosing
  class/module surface
Dependencies
  required types/imports + top callees
Entrypoints
  top callers/routes
Version
  repository + commit/worktree
Warnings
  unresolved edges / parse errors
```

### 12.2 Bug 定位模板

```text
Failure
  error/test/stack
Suspects
  ranked symbols + reason
Paths
  test/error → caller/data path → suspect
Recent Changes
  relevant diffs only
Validation
  exact commit results
Uncertainty
  ambiguous/unresolved targets
```

### 12.3 影响分析模板

```text
Changed Target
Direct Incoming
Transitive Impact Frontier
Public/API/Config Boundaries
Related Tests
Version
Pruned/Unresolved Paths
```

### 12.4 修改上下文模板

预算初值：

- target 30%；
- interface/types 15%；
- callers 15%；
- callees/data/config 15%；
- tests 15%；
- history/conventions 10%。

Context Builder 不直接复制整个文件。必要时通过 `context_ref` 展开父文件的相关窗口。

## 13. 版本与历史

### 13.1 Scope 语义

- `current`：active Generation，包括 dirty worktree；
- `commit`：精确 Git SHA；
- `branch/tag`：先解析到 SHA；
- `as_of`：按 committed_at 解析；
- `range`：用于 change/impact，不作为单一实现 Scope。

### 13.2 Dirty Worktree

ID：

```text
<base-sha>+dirty.<manifest-hash>
```

回答必须明确：

- 这是本地未提交快照；
- base commit；
- 与 Git history commit 不等价；
- TestResult 是否对应同一 dirty manifest。

### 13.3 Symbol Lineage

匹配信号：

- exact qualified name/path；
- Git rename；
- signature；
- AST/body similarity；
- surrounding symbols；
- human confirmation。

关系：

- SAME_SYMBOL_AS；
- RENAMED_TO；
- MOVED_TO；
- SPLIT_INTO/MERGED_FROM（候选，需复核）。

历史 Code Retrieval Unit 按 lineage 回跳目标 commit 的实体，不能返回当前正文替代旧版本。

## 14. Validation 设计

### 14.1 Validation Fact

```python
ValidationFact(
    repository_id,
    target_version,
    command,
    selector,
    framework,
    status,
    exit_code,
    duration_ms,
    stdout_ref,
    stderr_ref,
    observed_at,
    environment_ref,
    coverage_ref,
)
```

### 14.2 关系

- Commit/Worktree VALIDATED_BY TestResult；
- TestCase TESTS CodeSymbol；
- Coverage COVERS CodeSymbol/line；
- CIJob EXECUTES TestResult；
- Patch/ChangeSet VALIDATED_BY TestResult，仅有真实 target 对齐时。

### 14.3 新鲜度

Validation 只对其 target version 有效。即使代码只有一行无关变化，也不能默认沿用旧
TestResult；可以显示“历史最近验证”，但 fact status 为 `historical_validation`。

## 15. 增量摄取与索引

### 15.1 Builder Versions

- parser version；
- retrieval unit builder；
- relation resolver；
- embedding profile；
- sparse analyzer；
- summary builder。

### 15.2 Content Cache Key

```text
blob_hash
+ symbol identity
+ AST path
+ builder version
+ embedding profile revision
```

未变化 blob 的 Unit/embedding 可复用；关系解析在 import/type graph 变化时按受影响模块
增量重算。

### 15.3 Generation 发布

1. ingest raw snapshot；
2. entities；
3. retrieval units；
4. sparse/dense indexes；
5. graph；
6. validation；
7. integrity checks；
8. shadow query；
9. atomic activate；
10. delayed cleanup。

## 16. ACL、安全与数据质量

- Repository ACL 继承到 File/Symbol/Unit/Edge；
- 图遍历两个端点均需授权；
- remote embedding 前执行 secret scan/redaction；
- `.env`、key、private key、高置信 token 不进入 embedding；
- symlink、path traversal、submodule 边界显式处理；
- generated/vendor/binary 标记；
- parse error 不隐藏；
- external symbol 作为受控 stub，不抓取未授权外部源码；
- query trace 不输出完整敏感代码。

## 17. Code Golden Set

### 17.1 首版 50 条

| Slice | 数量 |
| --- | ---: |
| exact symbol/path/SHA | 8 |
| current implementation | 8 |
| cross-file call/reference | 8 |
| bug localization | 6 |
| impact analysis | 6 |
| related tests/validation | 5 |
| history/diff | 5 |
| unanswerable/ambiguous/ACL | 4 |

### 17.2 标注

- required entity；
- acceptable entity；
- forbidden hard negative；
- required path；
- target version；
- required context roles；
- answerability；
- expected locator。

### 17.3 指标

#### Retrieval

- Unit Recall@5/10/20；
- Symbol Recall@10；
- File Recall@10；
- MRR@10；
- nDCG@10；
- harmful candidate rate。

#### Graph

- edge precision sample；
- edge resolution coverage；
- required path recall；
- correct direction/type；
- graph-only gain；
- graph noise rate。

#### Context

- target coverage；
- dependency/test role coverage；
- duplicate ratio；
- distractor ratio；
- tokens per useful fact。

#### Version/Validation

- exact commit accuracy；
- wrong-version rate；
- dirty snapshot accuracy；
- false validation rate；
- locator accuracy。

#### System

- indexing duration；
- embedding cache hit；
- storage per 1k symbols；
- P50/P95/P99；
- graph expansion nodes；
- rerank cost。

## 18. 消融矩阵

| Experiment | 目的 |
| --- | --- |
| V1 local-hash | 当前基线 |
| + AST Unit | 测 chunk 独立收益 |
| + neural dense | 测低词面重合 |
| + exact/sparse analyzer | 测标识符和路径 |
| + graph candidate | 测 graph-only gain/noise |
| + semantic resolver | 测 SCIP/LSP |
| + code reranker | 测 hard negative |
| + late context | 测生成上下文 |
| flat vs graph vs graph+rerank | 证明 Graph 的真实作用 |
| fixed-k vs adaptive-k | 质量/成本 |

每次只改变一个主要变量。

## 19. 发布门槛

| 指标 | Target |
| --- | ---: |
| Symbol Recall@10 | ≥ 0.85 |
| File Recall@10 | ≥ 0.90 |
| MRR@10 | ≥ 0.70 |
| Required Path Recall | ≥ 0.75 |
| Exact Commit Accuracy | ≥ 0.95 |
| Wrong-version Rate | ≤ 0.02 |
| False Validation Rate | 0 |
| Locator Accuracy | ≥ 0.98 |
| Harmful Candidate Rate@10 | ≤ 0.05 |
| P95（当前规模，含本地 rerank） | ≤ 1.5 s |
| Unauthorized/Secret Leakage | 0 |

Target 在首轮人工标注后可评审，但必须保留原目标和调整原因。

## 20. 实施映射

### 20.1 新模块

```text
src/evidence_rag/rag/sources/code/
  contracts.py
  unit_builder.py
  exact_index.py
  sparse_index.py
  dense_index.py
  graph_builder.py
  graph_retriever.py
  history_retriever.py
  test_retriever.py
  reranker.py
  context_builder.py
  evaluator.py
```

### 20.2 Schema

- `code_retrieval_units`；
- `code_unit_vectors` 或 VectorIndex namespace；
- `symbol_occurrences`；
- `symbol_lineage`；
- `code_relation_diagnostics`；
- `validation_targets`；
- `code_retrieval_calibration`。

### 20.3 Feature Flags

```text
RAG_CODE_UNIT_BUILDER=raw-v1|ast-v2
RAG_CODE_EMBEDDING=local-hash-v2|profile-name
RAG_CODE_GRAPH_RETRIEVAL=false|true
RAG_CODE_SEMANTIC_RESOLVER=off|scip|lsp
RAG_CODE_RERANKER=off|profile-name
RAG_CODE_CONTEXT=snippet-v1|structured-v2
```

### 20.4 Sprint

#### C0：Golden 与基线

- 50 条 Code Golden；
- 当前 V1 Evaluation Run；
- hard negative；
- latency/storage。

#### C1：Retrieval Unit

- AST builder；
- unit/entity 分离；
- cache/generation；
- exact/sparse。

#### C2：Dense 与 Rerank

- model benchmark；
- code reranker；
- calibration。

#### C3：Graph Retrieval

- typed traversal；
- real max_hops；
- diagnostics；
- graph ablation。

#### C4：SCIP/LSP MVP

- 主语言 adapter；
- semantic edge；
- precision/coverage。

#### C5：History/Test

- historical materialization；
- Symbol lineage；
- Diff→Symbol；
- Test→Symbol。

#### C6：Context/Release

- task context builders；
- shadow/canary；
- SLO/security；
- rollback。

## 21. 测试计划

- AST unit boundary golden files；
- overload/nested/anonymous symbol ID；
- same-name hard negative；
- import alias/multimodule；
- max_hops 0/1/2/3 results differ；
- ACL graph neighbor leakage；
- current vs historical body；
- dirty manifest vs clean commit；
- Diff rename/move；
- TestResult wrong commit；
- vector generation rollback；
- parse error fallback；
- remote embed redaction；
- P95 benchmark。

## 22. 完成定义

Code Source 完成必须同时满足：

1. 八类查询有 Golden Case；
2. Stable Entity 与 Retrieval Unit 分离；
3. Graph 参与 candidate generation；
4. `max_hops`、edge whitelist 和版本真实生效；
5. 至少一个主语言有语义解析 MVP；
6. historical query 返回目标版本正文；
7. TestResult 不跨版本误用；
8. reranker 和 calibration 有消融；
9. Context 按任务组装；
10. 指标、SLO、安全和回滚通过；
11. 对应面试文档填写真实 Run。
