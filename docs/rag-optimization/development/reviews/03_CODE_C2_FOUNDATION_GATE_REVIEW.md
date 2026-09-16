# Code C2 Foundation Gate Review

## C-B1 第三次真实 Run 最终审计（2026-07-27，当前结论）

复审时间：2026-07-27 19:24:57 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`  
Run：`evaluation-run://project-code-golden-v2/33f76ed55a3d4c9ab1060a54bfa13d35`

### 最终裁决

| Gate | 结论 | 独立审计依据 |
| --- | --- | --- |
| C2 Foundation / ingestion | **PASS（保持）** | Parser IR、Store、Unit Builder、dual-write 与失败清理的既有 P0/P1 Gate 保持关闭 |
| C2-04 Dual-write | **PASS（保持）** | 第三 Run 由 production `IngestionService` 物化 3 个隔离 source；425 Units/425 FTS、3 个 active publication 完整一致 |
| C2-05 Exact/Sparse | **PASS（保持）** | production retriever identity、exact/identifier guardrail、真实 symbol entity projection 与 entity-aware diversification 均被 artifact 观察到 |
| C2 entity projection | **PASS（保持）** | publication 记录 407 个 CodeSymbol unit、18 个 FileVersion unit；Run 返回稳定 `#symbol=` entity |
| C2 entity-aware diversification | **PASS（保持）** | 相比第二 Run，duplicate@10 明显下降且 locator 回升；修复按设计生效 |
| C-B1 treatment | **NOT QUALIFIED** | overall locator `0.840909 → 0.590909`，delta `-0.25`；唯一失败 acceptance Gate 为 locator non-regression |
| 下一工程阶段 | **C3-01 Embedding/Profile AUTHORIZED** | artifact 无 P0/P1；剩余 zero-result、low-overlap 与排序覆盖是下一阶段的真实输入 |

```text
C2 engineering overall: PASS
C2 Foundation / ingestion: PASS
C2-04 Dual-write: PASS
C2-05 Exact/Sparse: PASS
C2 entity projection / diversification: PASS
C-B1 treatment: NOT QUALIFIED
C3-01 Embedding/Profile: AUTHORIZED
```

这里的授权只表示 C2 工程 Gate 已完成、可以进入下一工程阶段；**不表示 C-B1 合格，也不
得声称相对 C-B0 的整体检索质量提升**。

### Production identity、固定分母与 C-B0 锚

独立执行 artifact `verify-only` 返回 `status=verified`。SQLite 内唯一
`evaluation_runs` 行、manifest、attempt audit 与 33 个 result 的 Run ID 完全一致：

```text
run_id:
  evaluation-run://project-code-golden-v2/33f76ed55a3d4c9ab1060a54bfa13d35
status: completed
execution_mode: qualified
treatment_qualified: false

runner: code-c-b1-runner-v1
evaluation_runner: code-evaluation-v2.1
retriever:
  evidence_rag.rag.sources.code.retrieval_v2.CodeExactSparseRetriever
  code-exact-sparse-v1
fusion: deterministic-weighted-rrf-v1
unit_builder: ast-v2
dense / graph: disabled
```

production input fingerprint 的 before/after 完全一致且
`input_fingerprint_matched=true`：

```text
HEAD: bc3326edc761e3bdb42ed78726a21f314ab44974
tree: 2fe240ebdf1f3d04c252c32b505ca923394171da
implementation_input_hash:
  sha256:bbdcc396f09ba072f7e1ddeed14ff5b0d18e3c431ad23819871fb0f5d525a9f2
cb1_treatment_input_hash:
  sha256:fec13834c7146d95df387e377760ea995ece58596b742e50daee785889f388b1
```

固定 Golden 与 denominator：

```text
dataset: code-golden-v2
total / eligible / ineligible / results: 50 / 33 / 17 / 33
package_hash:
  sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61
eligible membership_hash:
  sha256:a9551e3b77822fd67f11c32dffa04e6b0069cd88e44adbfabfb6cd12bd343aaa
```

C-B0 锚为
`evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`；
baseline artifact hash、canonical manifest hash 与 qualification record hash 均由第三
Run manifest 固定。metrics 的 baseline/treatment membership 相同，locator 与 entity
denominator 均为 44，没有换分母或静默丢弃 17 个 ineligible case。

### 指标、真实改善与不合格结论

| 指标 | C-B0 | 第二 Run | 第三 Run | 第三 vs 第二 | 第三 vs C-B0 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `expected_locator_recall@10` | 0.840909 (37/44) | 0.477273 (21/44) | 0.590909 (26/44) | +0.113636 | **-0.250000** |
| `entity_recall@10` | 0.840909 (37/44) | 0.522727 (23/44) | 0.613636 (27/44) | +0.090909 | -0.227273 |
| `entity_duplicate_rate@10` | 0.000000 (0/224) | 0.477679 (107/224) | 0.129464 (29/224) | **-0.348214** | +0.129464（更差） |

因此 entity projection/diversification 的局部工程改善是真实的：相对第二 Run，
duplicate@10 大幅下降，locator 与 entity recall 都回升。但第三 Run 仍显著落后合格
C-B0；acceptance 明确为 `not-qualified`：

```text
exact entity_recall@10:       PASS, 5/6, delta 0
identifier entity_recall@10:  PASS, 10/10, delta 0
overall locator recall@10:    FAIL, 26/44, delta -0.25
```

所以不能把“比第二次失败 Run 更好”表述为“质量提升”或 C-B1 PASS。

### 剩余真实失败

独立从 33 个 case result 重算：

- `entity_recall@10` 尚有 17/44 个 expected-entity 判断未命中，分布在 11 个 case。
- 这些判断涉及 8 个版本化 entity URI；按 qualified symbol identity 合并同一符号的
  不同版本后，正好是 7 个目标实体：
  `calculate_total`、`apply_discount`、`apply_coupon`、`renderCheckout`、
  `formatMoney`、`RepositoryResolver.resolve`、`RepositoryResolver._local`。
- `error-analysis.json` 有 10 个 zero-result：
  `001/008/013/014/016/019/025/030/034/035`。其中
  `014/035` 是 ACL forbidden/unanswerable 的正确拒答；其余 8 个是 direct-answer
  失败。
- 未命中集中体现为中文或自然语言 query、dirty-current、low lexical overlap、
  graph-required 以及有候选但目标未进 top-10 的排序覆盖。C3-01 可以处理
  embedding/profile 与候选覆盖；graph-required 的完整能力仍属于后续 graph 阶段，
  本授权不虚构该能力。

这些是下一阶段应消费的真实失败输入，不构成继续修改 C2 exact/sparse 基础工程的 P1；
也不能用 exact/identifier 两个 guardrail PASS 掩盖 overall locator regression。

### Artifact、隔离、安全与 SQLite

第三 Run artifact 的 10 个文件均通过 manifest checksum/canonical reconstruction：

```text
artifact_set_hash:
  sha256:8804f5338a3284e4bd877e3b3e28365c0a67553e2a9db18a1dede3e644778f50
manifest canonical hash:
  sha256:868a89a906e176cc9a94ac6a7d151266ae73291bb9e6d4b9fa7386c1f3a85355
```

隔离物化与 SQLite 只读检查：

```text
database_isolated: true
production_database_accessed: false
AST units / FTS rows / vectors: 425 / 425 / 0
publications: 3 published
embedding / graph publication state: not-built / not-built
PRAGMA integrity_check / quick_check: ok
PRAGMA foreign_key_check: empty
evaluation.sqlite3-wal / -shm: absent
repository.local_path: <redacted-temp-path>（3/3）
```

安全 verifier 扫描全部 874 个 SQLite TEXT column、37,149 个值以及 9 个 JSON 文件，
临时绝对路径与高置信 credential assignment 命中均为 0。artifact 是隔离、可移植且
可由 manifest 重构的；没有 dense、graph 或 production DB 访问的虚假声明。

### 旧 Run、正式库与 Git 状态

审计开始时保存三个 Run 的完整 10-file SHA-256 集，verify 后逐文件复核全部一致。
两个旧失败 Run 仍保持不可覆盖的失败审计：

```text
ef293bbe574543e0a4224250b0dad0c9
  manifest SHA-256:
    8d6fa328f95a3962606bf7f6f6a1eb0ba53a7b2ded53c1456c4c6e1d102d591a
  size / mtime_ns:
    9720 / 1785144437053501540

ee42ee27441a4361a2ec40f483c26be1
  manifest SHA-256:
    e7238578a9ab2c9539548bbd2ac1e2a1be27e2e4ad8849f39609554fc922472b
  size / mtime_ns:
    9607 / 1785146114923372444

third Run manifest:
  SHA-256:
    631d1aa31df3ef545bd8b910b3e529beddc90b8c17d84a6a12368ce5a78f4ce1
  size / mtime_ns:
    9577 / 1785150589618772495

formal SQLite（仅 stat，未打开）:
  size / mtime_ns:
    1915490304 / 1785136263772927732
```

`git diff --check` 为 PASS，worktree 列表只有 `main`，HEAD 未变。本轮没有创建
branch/worktree，没有 commit/push，没有运行会写 artifact 的命令，没有打开正式库。
唯一持久化写入为本报告；未发现 artifact P0/P1。

## C-B1 same-entity child 拥挤 / locator P1 关闭复审（2026-07-27，当前结论）

复审时间：2026-07-27 18:27:13 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

### Gate 裁决

| Gate | 最终结论 | 依据 | 后续授权 |
| --- | --- | --- | --- |
| C2 Foundation / ingestion | **PASS（保持）** | 本轮只改 RRF 后选择，不改变 Parser/dual-write/store | 完成 |
| C2-05 Exact/Sparse | **PASS（保持）** | entity-aware distinct-first、canonical representative、真实 locator 与原 rank trace 均通过临时 SQLite 验证 | 完成 |
| C-B1 same-entity child / locator P1 | **CLOSED** | top-10 先 distinct entity；CodeSymbol/FileVersion canonical unit 优先；扩大 top_k 后才补 child | 关闭 |
| C-B1 third run | **AUTHORIZED** | 无剩余 P0/P1 主流程问题 | 允许第三次真实 Run |

```text
C2 Foundation / ingestion: PASS
C2-05 Exact/Sparse: PASS
C-B1 same-entity child / locator P1: CLOSED
C-B1 THIRD RUN AUTHORIZED
```

本轮只按第二次真实 C-B1 暴露的 P1 主流程复审，没有以 P2/P3 或低价值极端边界阻塞。

### C2-05 修复复核

- exact/sparse 原始候选先按既有 weighted RRF 排序；随后以
  `(repository_id, generation_id, entity_id)` 分组，不跨 scope 合并 entity。
- 每个 `CodeSymbol` 优先选真实 definition/declaration 的
  `symbol.ast_block`；每个 `FileVersion` 优先选 `file.surface`。canonical unit 不存在时
  才使用组内最佳可用 Unit。
- distinct-entity pass 按该 entity 在原 fused list 的最佳 rank 排序；只有所有 distinct
  representatives 已进入后，才按原 fused 顺序补 same-entity branch/loop/其他 child。
- candidate 的 `source_fused_score`、exact/sparse raw score/rank、matched fields 和真实
  Unit locator 均不重算；trace 另外记录 `original_fused_rank`、
  `diversified_rank`、representative 与原因，诊断与实际选择一致。

### 临时 SQLite 独立探针

构造 13 个命中 Unit：

```text
primary CodeSymbol:
  definition symbol.ast_block  L10-L80
  branch child                 L20-L30
  loop child                   L40-L60

FileVersion:
  file.surface                 L1-L200
  branch child                 L25-L27

related CodeSymbol entities:   8
```

结果：

```text
top_k=10:
  candidates=10
  distinct entities=10
  entity_duplicate@10=0.0
  primary canonical=definition symbol.ast_block
  primary locator=src/primary.py#L10-L80
  file canonical=file.surface
  file locator=src/file.py#L1-L200
  branch/loop/file-child absent

top_k=13:
  first 10 remain distinct entities
  positions 10..12 append file-child / branch / loop

repeated top_k=10:
  result（除 latency）与 trace 完全相同

top_k=10 → 13:
  channel raw rank/score、matched fields、original fused rank 完全相同
  仅 diversified rank/selection 按输出预算变化
```

### 回归与严格契约

```text
17 passed
  tests/test_code_rag_retrieval_v2.py

独立临时 SQLite diversification probe: PASS
git diff --check: PASS
worktree: only main
formal SQLite size/mtime: unchanged
```

17 项专项同时重放 weighted RRF/top_k/`complete_pruned`、determinism、真实 trace、
same-name 不静默、project/repository/generation/version/ACL/publication fail-closed 与
`CodeSourceResult` JSON round-trip；没有发现主流程回归。

### 两个旧失败 Run 保留

两个 artifact 均经 verify-only 重新验证为 `execution_mode=qualified`、
`treatment_qualified=false`、`acceptance=not-qualified`：

```text
ef293bbe574543e0a4224250b0dad0c9
  manifest SHA-256:
    8d6fa328f95a3962606bf7f6f6a1eb0ba53a7b2ded53c1456c4c6e1d102d591a
  manifest mtime:
    1785144437.053501540

ee42ee27441a4361a2ec40f483c26be1
  manifest SHA-256:
    e7238578a9ab2c9539548bbd2ac1e2a1be27e2e4ad8849f39609554fc922472b
  manifest mtime:
    1785146114.923372444

both full artifact byte sets: unchanged
```

runner 对已存在目标 Run 目录 fail closed；第三次 Run 必须生成新的 identity/artifact，
不得覆盖上述两个失败审计。

主控提供的上游证据保持记录但未冒充本轮独立执行：

```text
实施专项 / code 相关 / 全量: 17 / 340 / 399 passed
主控独立相关 / 全量: 247 / 399 passed
ruff src/tests、2 文件 format/diff/compile: PASS
only main: PASS
正式库与两个失败 Run mtime: unchanged
```

本轮没有执行第三次 C-B1 Run，没有修改或写入 eval/artifact/数据库，没有创建
branch/worktree，没有 commit/push，没有修改代码、测试、配置、依赖或其他文档。唯一
持久化写入是本报告。

> 以下为 entity projection P1 的关闭记录；其授权已被第二次真实 C-B1 Run 消费，
> 本轮仅保留历史审计。

## C-B1 entity projection P1 关闭复审（2026-07-27，历史通过记录）

复审时间：2026-07-27 17:51:38 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

### Gate 裁决

| Gate | 最终结论 | 依据 | 后续授权 |
| --- | --- | --- | --- |
| C2 Foundation / ingestion | **PASS（保持）** | entity projection 修复没有破坏 Parser、legacy ingestion、scope/ACL/generation 或 rollback | 完成 |
| C2-05 Exact/Sparse | **PASS（保持）** | `CodeSourceResult` 可见正确 symbol/file entity，并保留严格版本、generation、ACL 与 relation-node provenance | 完成 |
| C-B1 entity projection P1 | **CLOSED** | definition/declaration 与嵌套 unit 投影到稳定 `CodeSymbol`；file/fallback 保持 `FileVersion`；same-name 按 span 隔离 | 关闭 |
| C-B1 rerun | **AUTHORIZED** | 无剩余 P0/P1 主流程问题 | 允许重新执行 |

```text
C2 Foundation / ingestion: PASS
C2-05 Exact/Sparse: PASS
C-B1 entity projection P1: CLOSED
C-B1 RERUN AUTHORIZED
```

本轮只按真实 C-B1 暴露的 entity projection P1 复审，没有以 P2/P3 或低价值极端
边界阻塞。

### 修复链复核

- 真实 `CodeParser` 的同次 `ParsedFile` IR 先进入现有 legacy `_build_records()`，
  生成稳定 `FileVersion` 与 `CodeSymbol`；dual-write 不再把所有 Unit 固定挂到文件实体。
- `file` / parser `fallback` Unit 明确投影到同 path `FileVersion`。
- definition/declaration Unit 优先按 exact span，再以 qualified name 与首行 signature
  消歧到同 path `CodeSymbol`；嵌套 branch/block 投影到包含其 span 的最小 symbol。
- 同 qualified name、不同 span 的 overload 各自保留不同稳定 `#symbol=` entity，
  其嵌套 block 不跨绑定；无法唯一解析的候选直接抛错，不做猜测。
- 投影同步更新 Unit `entity_id`、`qualified_name`、context
  `parent_entity_id`、source-lineage attributes 与 publication projection 统计；
  入库前再次校验 entity path/scope/span/lineage。

### 临时 SQLite 真实端到端

独立探针使用真实：

```text
CodeParser
→ IngestionService legacy Repository/Commit/FileVersion/CodeSymbol
→ CodeDualWriteCoordinator
→ Code V2 FTS publication（仅临时库）
→ CodeExactSparseRetriever
→ CodeSourceResult
```

11 个语义 case 全部通过：

```text
file root                         → FileVersion
class definition                 → stable #symbol= CodeSymbol
function definition              → stable #symbol= CodeSymbol
nested if/block                  → containing function #symbol=
top-level branch                 → FileVersion
parser text fallback             → FileVersion
same qualified name / two spans  → two distinct #symbol= entities, no cross-binding
ACL denied                       → zero candidates
wrong project                    → UNAVAILABLE, zero candidates
active generation               → old symbol excluded
explicit historical generation  → exact old symbol/generation restored
```

所有返回值均严格为 `CodeSourceResult`；candidate 与 terminal relation node 的
`entity_id` 一致，且 repository/generation/stable version/ACL provenance 完整。

### 回归与失败审计保护

```text
26 passed
  tests/test_code_rag_dual_write.py       10
  tests/test_code_rag_retrieval_v2.py     16

真实临时端到端语义探针             11 passed
git diff --check                         PASS
worktree                                 only main
formal SQLite size/mtime                 unchanged
```

专项覆盖同 generation 幂等、旧 generation 非 active、publication 内部失败、
published 后 derivation/finalize 失败全量 cleanup，以及 cleanup 异常不遮蔽原错；
projection 修复未回归 rollback 语义。

旧失败审计
`evals/code/runs/ef293bbe574543e0a4224250b0dad0c9` 已只读重新验证：

```text
verification status: verified
execution_mode: qualified
treatment_qualified: false
acceptance decision: not-qualified
manifest SHA-256:
  8d6fa328f95a3962606bf7f6f6a1eb0ba53a7b2ded53c1456c4c6e1d102d591a
artifact file bytes: unchanged
manifest mtime: 1785144437.053501540 (unchanged)
```

runner 在目标 Run 目录已存在时 fail closed，不会覆盖既有审计；因此 rerun 必须生成
新的 Run identity/artifact，旧 `ef293...` 永久保留为失败记录。

主控提供的上游证据保持记录但未冒充本轮独立执行：

```text
实施专项: 10 passed
相关回归: 275 passed
全量 pytest: 398 passed
主控独立复跑相关/全量: 275 / 398 passed
ruff src/tests、2 文件 format/diff/compile: PASS
only main: PASS
正式库 mtime、旧失败 Run manifest mtime: unchanged
```

本轮没有执行 C-B1 rerun，没有修改或写入任何 eval/artifact/数据库，没有创建
branch/worktree，没有 commit/push，没有修改代码、测试、配置、依赖或其他文档。唯一
持久化写入是本报告。

> 以下为 entity projection 修复前的 C2-05 Gate 记录；其 C2-05 PASS 结论由本轮复审
> 保持，但原先的首次 C-B1 执行授权已经产生 `ef293...` 失败审计。

## C2-05 Exact/Sparse 精简 Gate（2026-07-27，历史通过记录）

复审时间：2026-07-27 17:22:01 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

### Gate 裁决

| Gate | 最终结论 | 依据 | 后续授权 |
| --- | --- | --- | --- |
| C2 Foundation / ingestion | **PASS（保持）** | C2-01/02/03/04 的既有 Gate 结论未发现回归 | 完成 |
| C2-05 Exact/Sparse | **PASS** | publication 下 exact + fielded sparse、确定性 weighted RRF、严格结果/trace 与全 scope fail-closed 均通过临时 SQLite 验证 | 完成 |
| C-B1 harness | **PREPARED** | 固定 Golden v2 50/33/17、qualified C-B0 锚、隔离数据库、真实 retriever 边界与 fake 限制成立；没有生成 Run artifact | 可执行 |
| C-B1 execution | **AUTHORIZED** | C2-05 无剩余 P0/P1 主流程问题 | 允许正式执行 |

```text
C2-05 Exact/Sparse: PASS
C-B1 harness: PREPARED
C-B1 EXECUTION AUTHORIZED
```

本轮按功能优先标准只审 P0/P1 主流程，没有以 P2/P3 或低价值极端边界阻塞。

### C2-05 主流程复核

- Exact locator 顺序明确且可复现：Code URI 为最高优先级；full/short SHA 先唯一约束
  published generation；随后依次为 qualified name、exact path、exact symbol/error code、
  basename。查询同时含多个 locator 时，只执行最高优先级的可识别 exact locator，
  version locator 独立约束 generation。
- 同名 symbol/basename 不做静默唯一选择：所有 scope 内同名路径硬负例均保留，并在
  exact channel trace 标记 `ambiguous ... without silent selection`，raw rank 稳定。
- query normalization 为 NFC、camel/snake、error code、CJK bigram 与 operator alias
  生成有界 token；sparse channel 只调用现有
  `fielded_code_unit_search()` / FTS5，不创建第二套 lexical index。
- exact 与 sparse 以 `k=60, exact=2, sparse=1` 的固定 weighted RRF 融合；
  raw score/rank、matched fields、fusion policy 和被 top-k 裁剪后的
  `complete_pruned` 状态均进入真实 trace/contract，稳定 tie-break 可重复。
- `search()` 严格返回 `CodeSourceResult`；`search_with_trace()` 返回 frozen
  `CodeExactSparseSearchResult`，其中 result 可 JSON round-trip，candidate 保留
  unit/entity/repository/generation/version/ACL/locator provenance。
- project、repository、generation、active/显式 version、ACL 与 publication 均在
  store 查询前后双重约束；错误 project/repository、ACL denied、generation 非
  published、publication missing/building/sparse-not-built 均 fail closed，不返回
  越 scope candidate。
- 实现没有调用 V1 retrieval、vector/dense、graph/context expansion；未实现 channel
  在 `CodeSourceResult` 中明确为 disabled，不存在 fallback 或能力偷跑。

### C-B1 PREPARED 只读复核

- `preparation_status()` 返回 `PREPARED`、`execution_status=pending`、
  `treatment_qualified=false`；固定 release 为 `code-golden-v2-release-001`，
  membership 与版本化 package hash 校验一致。
- denominator 固定为 50 total / 33 eligible / 17 ineligible；C-B0 锚固定为
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`，
  并经既有 qualified artifact 与只读 SQLite Run 双重验证。
- harness 把 runtime database 固定在临时 work root，拒绝正式
  `var/evidence-rag.sqlite3`；qualified mode 禁止 retriever factory 注入，只允许公开
  `CodeExactSparseRetriever`，fake 仅可在 repository runs 之外的 test mode 使用且永远
  不能标记 treatment qualified。
- 本轮调用 PREPARED 前后 `evals/code/runs` 全文件 SHA-256 清单完全一致，没有写 fake
  或 qualified Run artifact；正式库 size/mtime 也完全一致。

### 独立验证

全部命令均设置 `PYTHONDONTWRITEBYTECODE=1`：

```text
19 passed
  tests/test_code_rag_retrieval_v2.py                              16
  C-B1 PREPARED / fake-qualified guard / real retriever boundary   3

Golden v2: 50 / 33 / 17
C-B0 anchor: verified
repository Run artifacts: unchanged
formal SQLite size/mtime: unchanged
git diff --check: PASS
worktree: only main
```

主控提供的上游证据保持记录但未冒充本轮独立执行：

```text
C2/C-B1/Golden/Evaluation 相关专项: 199 passed
全量 pytest: 396 passed（按进度输出计数）
ruff src/tests、5 文件 format/diff/in-memory compile: PASS
only main: PASS
正式库 mtime: unchanged
新增 Run artifact: none
```

本轮没有打开或写正式库，没有执行 C-B1 Run，没有创建 branch/worktree，没有
commit/push，没有修改代码、测试、config、依赖、其他文档、evals 或 Golden。唯一
持久化写入是本报告。

> 以下为 C2-04 最终关闭记录；其授权已由上面的 C2-05 PASS 消费，不代表当前待办。

## C2-04 最终极简关闭结论（2026-07-27，历史通过记录）

复审时间：2026-07-27 16:36:04 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

### Gate 裁决

| Gate | 最终结论 | 依据 | 后续授权 |
| --- | --- | --- | --- |
| C2 Foundation | **PASS** | C2-01/02/03 与 C1 兼容门保持通过 | 已完成 |
| C2-04 Dual-write | **PASS** | 原唯一 P1 已关闭；off、成功、published 后 derivation/finalize 失败、cleanup-error 语义均通过临时 SQLite 验证 | 完成 |
| C2 overall foundation / ingestion | **PASS** | Foundation 与 opt-in ingestion dual-write 主流程无剩余 P0/P1 | 完成 |
| C2-05 Exact/Sparse | **AUTHORIZED** | C2-04 已通过 | 可开始 |
| C-B1 Run | **AUTHORIZED** | Foundation/ingestion Gate 已通过 | 可运行 |

```text
C2 Foundation: PASS
C2-04 Dual-write: PASS
C2 overall foundation / ingestion: PASS
C2-05 Exact/Sparse: AUTHORIZED
C-B1 Run: AUTHORIZED
```

本轮只重放原 P1 关闭路径与最小 off/成功对照；没有扩张到 P2/P3 或低价值极端边界。

### P1-C2-04-01：CLOSED

修复复核确认：

- `CodeDualWriteCoordinator.rollback()` 是按
  `(repository_id, generation_id)` 精确调用 `cleanup_code_generation()` 的幂等边界。
- `IngestionService.run()` 唯一外层 `except` 在 `ast-v2` 且 repo/generation 已确定时，
  无论失败发生在 coordinator 内、published 后 derivation，还是 workflow finalize，
  都先 rollback 本 generation 的 C2 units/vectors/diagnostics/publication，再沿用既有
  legacy `fail_generation()` 与 failed workflow 语义。
- cleanup 自身异常不会替换原 ingestion 错误；原错误仍是 message 首段，只追加固定
  `Code V2 cleanup failed: ...` 诊断。
- `raw-v1` 的 coordinator 为 `None`，外层失败路径没有 C2 rollback 工作。

### 临时 SQLite 最终重放

精确执行 5 个 case：

```text
published → derivation failure:
  workflow/generation failed
  units/vectors/diagnostics/publications = 0
  active/non-active publication = None
  original derivation error retained

published → finalize failure:
  workflow/generation failed
  units/vectors/diagnostics/publications = 0
  active/non-active publication = None
  original finalize error retained

derivation failure + cleanup failure:
  workflow failed
  error starts with original derivation failure
  cleanup failure appended without masking original

raw-v1 off:
  workflow completes
  coordinator is None
  C2 unit/publication tables remain empty

ast-v2 success:
  workflow completes
  real ParsedFile IR reused
  scope/ACL/version/lineage retained
```

独立命令结果：

```text
5 passed
git diff --check: PASS
worktree: only main
```

新增关闭回归将 `tests/test_code_rag_dual_write.py` 从 5 增至 8 个 case；主控提供的证据：

```text
相关专项/回归: 257 passed
全量 pytest: 373 passed
ruff src/tests: PASS
3 文件 format/diff/in-memory compile: PASS
only main: PASS
正式库 mtime: unchanged
```

调用点复核仍确认没有 C2 retrieval/vector/embedding/graph 偷跑；本次授权只允许进入
C2-05 与 C-B1，不把尚未实现的能力写成已完成。

本轮所有数据库验证均使用 pytest `tmp_path`；没有打开或写正式库，没有创建
branch/worktree，没有 commit/push，没有修改代码、测试、config、依赖、其他文档或
Git 状态。唯一持久化写入仍为本报告。

> 以下为 C2-04 首轮 FAIL 历史记录；原 P1 已由上面的最终复审关闭，不代表当前状态。

## C2-04 Dual-write 精简复审（2026-07-27，历史 FAIL）

复审时间：2026-07-27 16:17:32 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

### Gate 裁决

| Gate | 当前结论 | 依据 | 后续授权 |
| --- | --- | --- | --- |
| C2 Foundation | **PASS** | C2-01/02/03 与 C1 兼容门保持通过 | 已完成 |
| C2-04 raw-v1 默认路径 | **PASS** | 默认不构造 coordinator；端到端 legacy workflow/counters 保持兼容；C2 unit/publication 表零写 | 保持默认 |
| C2-04 ast-v2 正常路径 | **PASS** | 复用同次 ParsedFile IR；Unit Builder/Store 幂等、scope/ACL/version/lineage 正确；publication 不声明 sparse/vector/graph | 正常路径通过 |
| C2-04 coordinator 内部失败 | **PASS** | unit/publication 阶段注入失败时，本 generation C2 units/vectors/diagnostics/publication 全部清理，legacy failure handler 保持原语义 | 内部失败关闭 |
| C2-04 ingestion 尾部失败 | **FAIL** | coordinator 已成功发布后，raw derivation/finalize 失败不会触发 C2 cleanup；临时库保留 active `published` C2 generation | 修复并复审 |
| C2-04 overall | **FAIL** | 存在 1 个可复现 P1 generation/publication 一致性问题 | 阻塞 |
| C2-05 Exact/Sparse | **NOT AUTHORIZED** | C2-04 未通过 | 禁止开始 |
| C-B1 Run | **NOT AUTHORIZED** | C2-04 未通过 | 禁止运行 |

```text
C2 Foundation: PASS
C2-04 Dual-write: FAIL
C2-05 Exact/Sparse: NOT AUTHORIZED
C-B1 Run: NOT AUTHORIZED
```

本轮按功能优先的精简标准执行。既有 254 项专项与相关回归全部通过，但测试绿灯不能覆盖
独立复现的 P1 主流程失败窗口；没有把 P2/P3、低价值极端边界或格式问题作为阻塞项。

### P1-C2-04-01：Post-dual-write 失败残留 active published C2 generation

#### 可复现结果

在 `TemporaryDirectory` 中使用与专项相同的真实 `SQLiteStore` +
`IngestionService` 端到端 harness，启用 `ast-v2`，让 coordinator 正常完成 unit build、
integrity validation 和 `published` publication；随后在第一次
`sources.store.link_derivations()` 注入：

```text
RuntimeError: injected post-dual-write derivation failure
```

实际结果：

```text
workflow: failed failed
error_contains_injection: True
generation_status: failed
repository: failed gen-421e6c77-686c-4d2c-84ce-c898a6b1fc7b
c2_units_publications: 5 1
active_c2_publication_status: published
```

UUID 每次运行不同；关键不变量是 workflow/generation/repository 已进入 legacy failure
语义，但相同 active generation 仍保留 C2 units 和 `published` publication，
`get_code_index_publication(..., active_only=True)` 仍可返回。

#### 根因与影响

- `CodeDualWriteCoordinator.write()` 的 cleanup 只覆盖 `_write()` 内部异常。
- coordinator 成功返回后，ingestion 仍会执行 repository/file raw derivation linking 和
  workflow finalize。
- 这些尾部步骤发生异常时，`IngestionService.run()` 外层 handler 只调用
  `fail_generation()` 与 `update_workflow()`，没有清理本 generation 的 C2 artifacts。
- 因而 legacy generation 已是 `failed`，C2 publication 却仍是 active `published`。
  当前尚无 C2 retrieval，所以未即时对用户查询生效；一旦授权 C2-05，active-only 路由
  可以消费这个失败 generation，属于 P1 数据正确性问题。

#### 最小修复

1. 将本 generation 的 C2 cleanup 纳入整个 ingestion 尾部失败边界，而不只包住
   coordinator 内部；最小实现可在外层 `except` 中对非空 generation/repository 做
   精确、幂等的 `cleanup_code_generation(generation_id, repository_id=...)`。
2. 保持现有 legacy failure 语义与原始异常：workflow/generation/repository 的
   status/error 仍按原 handler 写入，不删除或改写 legacy entities/edges/search views。
3. 增加临时 SQLite 回归：coordinator 成功后分别在 derivation/finalize 至少一个真实
   尾部点注入异常，断言本 generation 的 units/vectors/diagnostics/publication 均为 0，
   active publication 为 `None`，且原 legacy failure 状态与错误消息不变。

### 已通过的 C2-04 主路径

#### raw-v1 默认

- `Settings.rag_code_unit_builder` 默认是 `raw-v1`。
- `create_runtime()` 使用短路条件，只在 `ast-v2` 时构造
  `CodeDualWriteCoordinator`；默认 ingestion 的 `code_dual_writer is None`。
- raw-v1 与 ast-v2 对照端到端中，legacy workflow 均完成、counters 完全一致、每个文件
  parser 只调用一次；raw-v1 的 `code_retrieval_units` 与
  `code_index_publications` 均为 0。

#### ast-v2 正常与 coordinator 内部失败

- coordinator 直接消费 ingestion 已生成的 `ParsedFile` 对象，没有第二次 parse；
  同 generation 重试两次 unit count/hash 不变且不增加行。
- FileVersion entity 与 unit 的 project/repository/generation/commit/ACL 一致；metadata
  保留 ref、blob/file hash、raw/snapshot raw IDs、request/snapshot/workflow lineage。
- publication 只声明 `ast_unit_builder`、`unit_store`、`fts_row_integrity`；明确保存：

```text
sparse=not-built
embedding=not-built
graph=not-built
sparse_retrieval=False
dense_retrieval=False
graph_retrieval=False
```

- publication 阶段内部注入失败时，当前 generation 的四类 C2 数据全部清理，workflow、
  generation、repository 按既有 legacy handler 标记失败。

### 独立验证计数

```text
254 passed：
  C2-04 dual-write                                      5
  C1 contracts / V1 adapter / shadow                  209
  C2 Parser IR / Store / Unit Builder                  27
  legacy parser / ingestion                            13

临时 SQLite 额外端到端：
  post-dual-write derivation failure                  REPRODUCED P1

git diff --check:                                     PASS
worktree:                                             only main
```

专项文件计数：

```text
tests/test_code_rag_dual_write.py         5
tests/test_code_rag_contracts.py         86
tests/test_code_rag_v1_adapter.py        37
tests/test_code_rag_shadow.py            86
tests/test_code_parser_ir.py             10
tests/test_code_rag_store.py             11
tests/test_code_unit_builder.py           6
tests/test_parser.py                      5
tests/test_ingestion_retrieval.py          8
```

静态调用点确认：

- C2 write 只从 opt-in coordinator 接入 ingestion；
- exact/FTS/vector/context Store API 没有生产 retrieval 调用者；
- coordinator 没有写 vector、embedding 或 graph 数据；
- C2-04 没有偷跑 C2 retrieval/embedding/graph。

主控提供的全量 370 项、ruff src/tests、5 文件 format/diff/in-memory compile、only main
及正式库 mtime 未变化证据均为 PASS；本精简 Gate 独立重复了 254 项专项、调用点审阅与
临时 SQLite 失败重放，没有打开或写正式库。

本轮没有创建 branch/worktree，没有 commit/push，没有修改代码、测试、config、依赖或
其他文档。唯一持久化写入仍为本报告。

> 以下第二轮 Foundation PASS 与 C2-04 初始授权为历史状态；C2-04 当前实现 Gate 以上面
> 的 FAIL 为准。

## 第二轮最小关闭结论（2026-07-27，Foundation 历史结论）

复审时间：2026-07-27 15:47:29 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

### Gate 裁决

| Gate | 第二轮结论 | 依据 | 后续授权 |
| --- | --- | --- | --- |
| C2-01 Schema / Store | **PASS** | 两个 Foundation P1 均由临时 SQLite 独立反例确认关闭；合法 context 与 publication 路径保持可用 | 完成 |
| C2-02 Parser IR | **PASS** | 10 项 Parser IR 专项及 5 项旧 parser 回归通过 | 完成 |
| C2-03 Unit Builder | **PASS** | 6 项 Unit Builder 专项通过；纯构建与 Store record 契约保持稳定 | 完成 |
| C1 adapter / shadow / contracts | **PASS** | C1 三文件 209 项通过，无 contract/adapter/shadow 回归 | 保持通过 |
| C2 Foundation overall | **PASS** | C2-01/02/03 与 C1 兼容门全部通过；未发现新 P0/P1 主流程问题 | 完成 |
| C2-04 Dual-write | **AUTHORIZED** | Foundation 已通过 | 可开始 |

```text
C2-01 Schema / Store: PASS
C2-02 Parser IR: PASS
C2-03 Unit Builder: PASS
C1 compatibility: PASS
C2 Foundation overall: PASS
C2-04 Dual-write: AUTHORIZED
```

本轮继续按功能优先的精简 Gate 执行，只因 P0/P1 主流程、数据正确或 C1 兼容问题阻塞；
没有把 P2/P3、低价值极端边界或既有整文件格式差异升级为阻塞。

### P1-C2-01-01：Context ACL / scope 扩展泄漏 CLOSED

实现复核确认：

- `load_code_unit()` 新增显式 `project_id` scope，并与 repository/generation/ACL/active
  generation 一起进入 root lookup。
- `load_code_unit_context()` 对 parent entity、parent unit 和 children 均继续绑定当前
  unit 的 project/repository/generation/ACL；首次 root 授权后不再无 scope 扩展。

独立临时 SQLite 重放同时建立：

- 当前 project/repository/active generation/ACL 的 target、合法 parent/child/neighbor；
- 同 generation 的 restricted parent/child/neighbor；
- 同 repository/generation 但不同 project 的 child/neighbor；
- 同 project/repository 但旧 generation 的 neighbor；
- 同 project 但不同 repository/generation 的 neighbor。

以完整当前 scope 读取公开 target，结果为：

```text
context_entity: entity://target
context_parent: None
context_children: ['unit://allowed-child']
neighbor_expansion_visible: ['unit://allowed-neighbor']
valid_parent: unit://valid-parent
```

其中 restricted parent 被 fail-closed；children 只返回合法同 scope 单元；按
`context_ref.neighbor_unit_ids` 使用同一 scope 逐一扩展时，restricted、异 project、
旧 generation、异 repository/generation 均返回 `None`。单独构造的合法同 scope parent
正常返回。

结论：原 P1 的未授权 unit/context 泄漏已关闭。第二轮明确验收目标是 read/expansion
fail-closed；额外关系写入规范化不作为本功能优先 Gate 的 P1 阻塞。

### P1-C2-01-02：Publication repository/project identity CLOSED

实现复核确认 `upsert_code_index_publication()` 在同一 `BEGIN IMMEDIATE` 事务中先读取
repository owner project；repository 不存在或 project 不一致时，在任何
insert/upsert 前固定抛出：

```text
ValueError: code index publication repository/project mismatch
```

独立临时 SQLite 重放先写入合法 `building` publication，再尝试以错误 project 更新为
`published`。错误更新被拒绝，原行完全保持：

```text
publication_bad_update_rolled_back: building {'phase': 'before'}
publication_good_update: published project://alpha
```

随后使用正确 repository/project 更新成功，表内仍只有一行。错误 project insert/update
均不存在部分写入。

结论：原 publication project identity P1 已关闭。

### 第二轮独立回归

```text
249 passed：
  C1 contracts / V1 adapter / shadow                 209
  C2 Parser IR / Store / Unit Builder                 27
  legacy parser / ingestion                           13

临时 SQLite 独立关闭场景：
  context root/entity/parent/child/neighbor scope     PASS
  context 合法同 scope 扩展                           PASS
  publication 错误 project 对既有行原子拒绝           PASS
  publication 正确归属 insert/update                  PASS

git diff --check:                                    PASS
worktree:                                            only main
```

专项文件计数：

```text
tests/test_code_rag_contracts.py        86
tests/test_code_rag_v1_adapter.py       37
tests/test_code_rag_shadow.py           86
tests/test_code_parser_ir.py            10
tests/test_code_rag_store.py            11
tests/test_code_unit_builder.py          6
tests/test_parser.py                     5
tests/test_ingestion_retrieval.py         8
```

静态调用点复核仍只看到 Code V2 Store 自身方法与 Unit Builder 定义；生产 ingestion、
retrieval、runtime/platform 没有 C2 unit 写入、publication 或 retrieval 调用者。因此
本轮修复没有引入 dual-write/C2 retrieval 偷跑，C2-04 是“已授权开始”，不是“已实现”。

本轮只读取共享 dirty `main`，所有数据库验证均使用 `TemporaryDirectory`；没有打开或写
正式库，没有创建 branch/worktree，没有 commit/push，也没有修改代码、测试、配置、
依赖或其他文档。唯一持久化写入仍为本报告。

> 以下为第一轮 Foundation FAIL 的历史审查记录；两个 P1 已由上面的第二轮证据关闭，
> 不代表当前 Gate 状态。

审查日期：2026-07-27  
复审时间：2026-07-27 15:31:15 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

## Gate 裁决

| Gate | 结论 | 依据 | 后续授权 |
| --- | --- | --- | --- |
| C2-02 Parser IR | **PASS** | 单次 Tree-sitter parse 同时生成旧 Symbol 与可序列化 IR；structural path、UTF-8 byte/line span、signature/doc/identifiers、partial/fallback 与旧流程专项均通过 | 完成 |
| C2-01 Schema / Store | **FAIL** | exact/FTS/vector/active generation、批量事务、cleanup 主路径通过，但临时 SQLite 独立复现 2 个 P1：context 跨 ACL 返回子单元；publication 接受与 repository 不一致的 project | 修复并复审 |
| C2-03 Unit Builder | **PASS** | IR 到 frozen record 的映射确定；unit/content identity、版本/ACL/lineage、DEFINES/CONTAINS/context refs、fallback、无 DB 副作用均通过；真实 `as_store_record()` 可批量写入 C2 Store | 完成 |
| C1 adapter / shadow / contracts 回归 | **PASS** | C1 三文件 209 项通过；旧 parser/ingestion 13 项通过；未发现 C2 dual-write 或 C2 retrieval 接入 | 保持通过 |
| C2 Foundation overall | **FAIL** | C2-01 存在可复现的 ACL 泄漏与 publication project identity 错写 | 阻塞 |
| C2-04 Dual-write | **NOT AUTHORIZED** | Foundation 未通过；不得开始 dual-write / generation publication 接线 | 禁止 |

```text
C2-02 Parser IR: PASS
C2-01 Schema / Store: FAIL
C2-03 Unit Builder: PASS
C1 compatibility: PASS
C2 Foundation overall: FAIL
C2-04 Dual-write: NOT AUTHORIZED
```

本 Gate 按功能优先的精简标准执行，只把 P0/P1 主流程、数据正确或 C1 兼容性问题作为
阻塞。没有把 token 分布、极端输入或既有 `storage.py` 整文件格式差异升级为阻塞项。

## P1-C2-01-01：Context 子单元查询绕过 ACL 边界

### 可复现结果

在 `TemporaryDirectory` 中初始化全新 `SQLiteStore`，建立同一
project/repository/generation 下的两个合法 Entity：

```text
entity://public       acl_ref=public
entity://restricted   acl_ref=acl://restricted
```

随后写入：

```text
unit://public       entity=entity://public       acl=public
unit://restricted   entity=entity://restricted   acl=acl://restricted
                    parent_unit_id=unit://public
```

写入成功。以仅公开 ACL 调用：

```python
store.load_code_unit_context("unit://public", allowed_acl_refs=[])
```

实际输出：

```text
public_unit_loaded: True
child_acl_refs: ['acl://restricted']
child_ids: ['unit://restricted']
```

### 根因与影响

- `schema.py` 的 parent FK 只绑定
  `(parent_unit_id, generation_id)`，没有绑定 repository/project/ACL。
- `load_code_unit_context()` 首个 unit lookup 使用了 `allowed_acl_refs`，但随后读取 parent
  和 children 时只过滤 unit id / generation / repository，没有继续过滤 project/ACL。
- 因此 Store 接受的普通合法行可以让已授权的公开 context 返回未授权子单元正文。这直接
  违反 C1 的 relation endpoint ACL boundary 与 context provenance 契约，属于 P1 数据
  隔离问题，不是极端攻击面。

### 最小修复

1. 在 schema 或同一写事务中保证 parent 与 child 至少共享
   generation/repository/project/ACL；跨 ACL parent 关系必须拒绝并整批回滚。
2. `load_code_unit_context()` 的 parent/children 查询继续施加当前 unit 的
   project/ACL 边界，不能只依赖首次 unit lookup。
3. 增加临时 SQLite 回归：公开 parent + restricted child 必须写入失败，或 context
   fail-closed 且绝不返回 restricted 正文；同时保留同 ACL parent/child 正常读取。

## P1-C2-01-02：Publication project identity 未与 Repository 绑定

### 可复现结果

临时库中 repository 的真实 project 为 `project://probe`，generation 也正确属于该
repository。以下 publication 被成功接受：

```python
store.upsert_code_index_publication(
    {
        "generation_id": "generation://probe",
        "repository_id": "repository://probe",
        "project_id": "project://foreign",
        "builder": "probe",
        "sparse": "probe",
        "embedding": "probe",
        "graph": "probe",
        "status": "published",
        "validation": {},
    }
)
```

实际输出：

```text
publication_project_id: project://foreign
```

### 根因与影响

- `code_index_publications` 只用 FK 绑定 generation/repository；`project_id` 是不受约束的
  独立文本列。
- `upsert_code_index_publication()` 原样插入/更新调用方提供的 `project_id`，没有在同一
  事务中验证 repository 的真实 project。
- publication 已建立 `(project_id, repository_id, status, created_at)` scope index，并将
  在 C2-04 承担 generation 发布状态。错误 project 会造成发布元数据跨项目错标，破坏
  C2-04 的 project/repository/generation 数据正确性。

### 最小修复

1. 将 publication 的 project identity 与 repository 真实 project 在数据库约束或同一
   原子写事务中绑定；不一致时拒绝，不能落下旧值或部分更新。
2. 增加临时 SQLite 回归：foreign project insert/upsert 均失败且原 publication 保持
   不变；匹配 project 的 building → published upsert 继续通过。

## 已通过的 Foundation 能力

### C2-02 Parser IR

- `ParsedFile.units` 与旧 `ParsedFile.symbols/imports/parse_error` 并存；Tree-sitter parser
  的计数代理确认同次文件 parse 只构建一次 parse tree。
- structural path 使用 node type + 同类型 sibling ordinal，前置空行不改变函数路径。
- Python nested/async/decorator/branch/loop/try、JS/TS class/function/arrow/getter/setter/
  overload、TSX component/hook 均产生稳定 IR。
- Unicode + CRLF 的 byte slice、start/end line 与 IR text 精确一致。
- syntax error 保留 partial AST，parser exception 与非 structured language 产
  line-aware fallback；空 structured file 仍产生 file IR。
- 旧 ParsedSymbol calls/references 与 ingestion 主流程回归通过。

### C2-01 Schema / Store 的非阻塞主路径

- initialize 幂等；旧 BASE_SCHEMA 库可 additive 初始化且不 backfill。
- 六字段 FTS 与 insert/update/delete trigger 正常。
- unit/vector/diagnostic 批量写入、foreign identity 整批 rollback 正常。
- exact、fielded FTS、vector iterator 的 project/repository/generation/active generation
  与直接 unit ACL 过滤正常。
- publication 正常 upsert、embedding cache、calibration、context 同 ACL 主路径、
  generation cleanup、repository/generation cascade 与 integrity stats 正常。
- 所有数据库验证均使用 pytest `tmp_path` 或 `TemporaryDirectory`；没有打开或写正式库。

### C2-03 Unit Builder

- 每个 Parser IR 确定映射为 frozen `CodeUnitRecord`；重复 build 完全相等。
- unit ID 包含 entity/unit type/structural path/content hash/builder version；正文或
  builder version 改变会 rekey，ref/generation/ACL 作为 occurrence provenance 不造成
  false rekey。
- parser/builder version、ACL、ref、blob/file hash、source URI 与 lineage attributes
  完整保留。
- 根 unit 使用 DEFINES，嵌套 unit 使用 CONTAINS；direct relation IDs 与 neighbor refs
  稳定。
- partial/fallback quality、line/byte span 与 parse error 保留；Builder 测试以
  monkeypatch 禁止 `sqlite3.connect`，仍正常构建。
- 独立正向集成把真实 Builder 产出的 5 个 `as_store_record()` 批量写入临时 C2 Store：

```text
records_relations: 5 5
written_sparse_hits: 5 1
integrity_healthy: True
```

### C1 兼容与未偷跑

- C1 contracts / V1 adapter / shadow 共 209 项通过。
- `rg` 确认 C2 Store 写入、exact/FTS/vector/context API 和 Unit Builder 在生产
  `src/evidence_rag` 中没有调用者；`storage.py` 只接入 additive schema 初始化和 Store
  mixin。
- 未发现 ingestion dual-write、C2 publication 接线或 C2 retrieval 路由；V1 默认路径
  保持不变。

## 独立验证计数

```text
247 个不同 pytest：
  C1 contracts / adapter / shadow       209 passed
  C2 Parser IR / Store / Unit Builder    25 passed
  legacy parser / ingestion              13 passed

C2 三文件前置单跑：
  25 passed（已包含在上面的 247 个不同测试中）

pytest 成功执行次数：
  272 passed（247 + 重复确认的 25）

临时 SQLite / stdin 独立场景：
  1 个 Builder → Store 正向集成 PASS
  1 个 context ACL 反例 REPRODUCED
  1 个 publication project 反例 REPRODUCED

独立检查总数：
  250 个不同检查（247 pytest + 3 stdin 场景）
```

第一次直接调用系统 `pytest` 因未加载仓库 `src` 环境，在 collection 前得到
`ModuleNotFoundError: evidence_rag`，收集/执行数为 0；改用仓库现有 `.venv/bin/pytest`
后上述 272 次均通过。这是 harness 路径校准，不是产品失败。

附加只读检查：

```text
ruff check（10 个 Gate 相关实现/测试文件）: PASS
ruff format --check: 仅 storage.py 为已知既存整文件格式差异
git diff --check: PASS
SQLiteStore mixin method collision: []
worktree: 仅共享 main
```

## 写锁与工作区说明

本审直接读取共享 dirty `main`，没有创建 branch/worktree/子任务，没有 commit/push，
没有修改代码、测试、配置、依赖、其他文档、evals、fixtures 或数据库。验证工具产生的
忽略缓存未保留。唯一持久化审查输出是本报告：

```text
docs/rag-optimization/development/reviews/03_CODE_C2_FOUNDATION_GATE_REVIEW.md
```
