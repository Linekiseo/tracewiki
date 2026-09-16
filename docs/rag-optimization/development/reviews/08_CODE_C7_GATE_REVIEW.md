# Code C7-01 Task-specific Context Builder Gate Review

独立 Gate 时间：2026-07-28 21:59 +0800

共享工作目录：`/Users/example/project/rag`

分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

## 裁决

```text
C7-01 engineering FAIL
P0 findings: 0
P1 findings: 6
C7-02 NOT AUTHORIZED
```

C7-01 已形成四个冻结模板、独立 Retrieval/Comprehension contracts、只读
`CodeSourceResult.context_blocks` 的正文装配、稳定 candidate citation、关系路径解释、
确定性预算/整行 Unicode 裁剪以及明确 refusal/missing。但以下六条问题处在本 Gate
点名的 project/ACL/watermark scope、原文真值、stable citation、parent/source 去重、
exact version/locator 和 context metrics 主流程上，不能降为 P2/P3。

## P1-01：必填 project scope 从未验证，ACL/watermark 自洽伪造也没有上游授权绑定

位置：

- `src/evidence_rag/rag/sources/code/context_builder_v2.py:306-344`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1089-1161`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1725-1747`
- `src/evidence_rag/rag/sources/code/contracts.py:600-624`
- `src/evidence_rag/rag/sources/code/contracts.py:848-863`

`ContextBuildScope.project_id` 只经过字符串规范化，之后没有任何读取或比较。
`CodeRetrievalCandidate`、`CodeContextBlock` 和 `CodeSourceResult` 也均不携带可供验证的
project identity。repository/version/generation/ACL 只与调用方同时提供的 scope
比较，index/watermark 也只在同一 caller-attested scope 与 result 间比较；没有
retrieval request、publication 或授权 envelope 将这些字段共同绑定。

纯内存最小复现对同一 result 分别传入：

```text
project_id=project://gate-a -> accepted
project_id=project://gate-b -> accepted
two canonical outputs are byte-identical: true
```

再把 candidate、context block、relation terminal、result watermark 和 scope 一起改为
`acl://forged` / `watermark://forged`，builder 仍接受并输出该 ACL。相反，仅 candidate
ACL 与 scope 冲突、或仅 result watermark 与 scope 冲突时会正确 fail closed；这说明
当前只能发现不自洽，不能证明 project 或授权 scope 的真实来源。

影响：调用方可把另一个 project 的同 repo/version 数据或整体伪造的 ACL/watermark
包装成自洽输入，C7 citation 会把它作为已授权 evidence 输出。必填 `project_id`
事实上是无效安全参数。

最小关闭条件：

1. 输入必须增加不可由 builder 调用方任意拼装的 governed scope/publication identity，
   精确绑定 project、repository、index、watermark、stable version、generation、ACL
   和允许路径；
2. 每个 candidate/context block 必须可追溯到该 scope identity；project 缺失时拒绝，
   不能仅接受 `ContextBuildScope` 自我声明；
3. 增加 wrong project，以及“scope ACL/watermark 与伪造 candidate 一致”的负向回归。

## P1-02：输入 source block 的正文未绑定 source identity，同 block ID 正文替换会被接受

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:679-733`
- `src/evidence_rag/rag/sources/code/contracts.py:1020-1059`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:960-968`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1164-1198`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1421-1426`

`CodeContextBlock` 没有由 source publication 提供的 content identity/manifest binding。
`_validate_source_block()` 只比较 entity/unit/repository/role/version/generation/path/
locator/ACL，不比较正文 identity。builder 在收到正文后自行计算
`source_content_identity`；因此这个 hash 只能描述“收到的字符串”，不能证明它是
candidate locator 对应的原文。

纯内存最小复现仅执行：

```python
replaced = original_block.model_copy(update={"content": "forged_body = True\n"})
```

保持原 `block_id`、candidate、locator、version、generation、ACL 不变，将 replaced block
放回严格 `CodeSourceResult` 后，result 和 builder 均接受，最终
`source_text == "forged_body = True\n"`。单独只替换 block locator 会被现有 contract
拒绝，但正文替换不需要替换任何 provenance 字段。

影响：metadata/name/summary 虽不会由 builder 主动冒充 snippet，但上游或中间层把同一
block ID 的正文替换成 summary/错误内容时，C7 会为替换内容生成一个新的“source
identity”并发出稳定 citation。这不满足原文真值和 stable citation 绑定。

最小关闭条件：由正文生产/publication 层发布不可变 content identity，并将其绑定到
project/repository/version/generation/path/locator/ACL/watermark；builder 必须复算并
核对。现有输入没有该证明时应明确拒绝或 unavailable，不能在消费后自行创建证明。

## P1-03：parent/content 去重会合并不同 child provenance，丢正文并把角色绑定到错误 citation

位置：

- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1383-1448`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1450-1479`

第一层去重只按 repository/version/generation/ACL/entity 分组，不含 retrieval unit、
source block、locator 或 content identity；随后选第一个 source block，却 union 全组
roles 和 retrieval unit IDs。第二层又只按正文 hash 合并不同 stable entities，并继续
保留单一 canonical citation。

独立构造同一 stable entity 的两个 child：

```text
unit://first  TARGET  src/shared.py#L1-L1
  content: def target(): pass

unit://second TEST    tests/shared_test.py#L20-L20
  content: def test_target(): pass
```

实际输出：

```text
comprehension blocks: 1
rendered source: def target(): pass
citation candidate_role: target
roles include: related_tests
retrieval_unit_ids: [unit://first, unit://second]
pruned_block_ids: []
```

第二个 test 正文被静默丢弃，`RELATED_TESTS` 却标在第一个 target 正文和 target
citation 上；trace 也没有把丢失 source 记为 pruned/missing。不同 entity 但正文相同
时也会发生单 citation 合并，无法保持 entity/unit/role 逐证据绑定。

影响：RetrievalContext 的两个原始 candidates 尚在，但 ComprehensionContext 会把一个
candidate 的角色解释成另一个 candidate 的正文，直接破坏 stable citation、
candidate-complete provenance、parent/source truth 和诚实 missing/pruned。

最小关闭条件：只有 source identity、locator、parent contract 和 citation provenance
全部等价时才可折叠；不同 child/source 必须保留各自 citation，或使用显式、可验证的
parent window contract。不得把被去重 candidate 的角色或 unit ID 合并到无法引用它的
单一 citation；被舍弃正文必须显式 pruned/missing。

## P1-04：畸形 dirty stable version 被静默解释为 clean

位置：

- `src/evidence_rag/rag/sources/code/context_builder_v2.py:43-49`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:383-424`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:991-1020`

`_version()` 将所有不匹配 `_DIRTY_VERSION` 的值直接当作 clean version，而不是区分
真正 clean identity 与“包含 dirty 标记但格式畸形”的值。`ContextBuildScope` 本身也
只要求非空控制文本。

最小复现：

```text
stable_version =
bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb+dirty.Manifest!
```

candidate、locator、result watermark 和 scope 使用同值后，builder 接受并输出：

```text
dirty=false
display=stable_version=...+dirty.Manifest!; generation=...; dirty=false
```

影响：畸形或被篡改的 dirty manifest 会被明确误报为 clean，丢失 base/manifest 真值，
使 dirty version citation 与测试/历史证据的 exact manifest 对齐失效。

最小关闭条件：冻结 clean 与 dirty version grammar；任何含 dirty namespace/marker 但
不满足精确 `<base>+dirty.<manifest>` 的值都 fail closed。增加空 manifest、非法大小写/
字符、额外 suffix、错误 base 的负向测试。

## P1-05：非规范 locator path 与过宽行号可作为 exact untruncated citation

位置：

- `src/evidence_rag/rag/sources/code/context_builder_v2.py:285-303`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1023-1072`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1193-1198`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1482-1532`

locator path 被 `unquote()` 后只校验 decoded path，没有核对原编码是否 canonical。
因此 `%73rc/encoded.py` 被接受为 `src/encoded.py`，但 citation/rendered locator 仍保留
非规范 `%73rc`。行号校验只拒绝“正文行数大于 locator span”，不要求未截断正文与 span
精确一致。

两个纯内存复现：

```text
code://repository-gate@<version>/%73rc/encoded.py#L1-L1
  accepted repository_path=src/encoded.py
  rendered locator still contains %73rc

one-line source + locator src/wide.py#L10-L20
  accepted as untruncated
  rendered_line_count=1
  rendered locator remains #L10-L20
```

绝对路径、NUL、L0、反向 span 和 path-prefix 越界会被现有代码正确拒绝，但不能关闭
上述 canonical/exact locator 缺口。

影响：同一路径可产生多种 stable citation identity；未截断的一行原文可声称覆盖十一行，
locator correctness 因而不是真实 exact locator correctness。

最小关闭条件：对 owner/ref/path/fragment 做单一 canonical round-trip 校验并拒绝
unreserved percent encoding、重复/多余编码及非规范 fragment；未截断 block 的正文
行数必须与 exact span 一致。若上游允许 broader parent window，必须显式携带 block
offset/span，不能把 candidate 的宽 locator 当作正文 exact locator。

## P1-06：无任何原文 block 时仍报告 context precision 和 locator correctness 为 1.0

位置：

- `src/evidence_rag/rag/sources/code/context_builder_v2.py:741-834`
- `src/evidence_rag/rag/sources/code/context_builder_v2.py:1857-1887`

`mapped_candidates` 来自“candidate 被启发式映射到任一角色”，
`locator_validations` 固定等于所有 input candidates；两者都不要求 source block 被
选入 ComprehensionContext。对应 properties 再除以 input candidate 数量。

纯内存最小复现给一个 TARGET candidate、`context_blocks=()`：

```text
comprehension.status=unavailable
selected_source_blocks=0
mapped_candidates=1
context_precision=1.0
locator_correctness=1.0
```

影响：完全没有可读 context 时会产生满分“context precision/locator correctness”，
可计算指标没有从真实 blocks/roles 导出，后续 evaluation 或发布 Gate 会被结构计数
冒充质量。`answer_utility_status=unavailable` 当前保持正确。

最小关闭条件：结构 coverage/efficiency 只从实际 selected blocks、真实 fulfilled roles
和 rendered locators 计算；需要 label 才成立的 precision/recall 在无 label 时保持
`UNAVAILABLE`。无 block 时不得输出 1.0 context/locator quality。

## 已直接支持的主流程

以下结论由源码、专项测试和独立正向 probes 直接支持；它们不抵消上述 P1：

- builder module 的 26 个公开 exports 与 package lazy exports 精确对应且唯一；
  contracts 为 frozen/slotted、显式 versioned，canonical bytes/hash 稳定。
- 四个模板只覆盖 `IMPLEMENTATION`、`BUG_LOCALIZATION`、`IMPACT_ANALYSIS`、
  `CHANGE_CONTEXT`，role 集合/顺序与 C7-01 计划一致；unknown、CALL_PATH 等未授权
  task 和 upstream refused fusion 均明确 refusal，不静默套模板。
- RetrievalContext 保留原 candidate 对象、within-source rank、raw channels/ranks、
  relation path 和原 fusion trace；ComprehensionContext 独立生成，没有执行 retrieval。
- 只有 `CodeSourceResult.context_blocks` 会成为 source text；无 block 时明确
  unavailable/missing，不会从 entity name、metadata 或 summary 造正文。注意该正向
  边界不解决 P1-02 的 input block 正文身份缺失。
- 现有 relation path 才生成 explanation；无 path 时
  `path_explanation=None` 且 GRAPH_PATH 明确 missing，没有推断路径。
- target/exact hard signal 先于普通候选；zero budget 会显式 pruned/unavailable。
  多字节 `café` / `你好🙂` 探针按完整行裁剪，输出精确收窄 `#L7-L7`，没有破坏
  Unicode；相同输入两次输出稳定、幂等。
- candidate 与 scope 的 repository/version/generation/ACL 直接冲突、result 与 scope
  的 index/watermark 直接冲突、non-exact alignment、绝对/越界路径、L0/反向 locator
  均 fail closed。
- package 外没有 builder runtime 调用；未发现 API、runtime、store、schema、DB
  persistence 或 Platform 接线。C7-02/C7-03 均未实现，本 Gate 未审查或授权它们。

## 独立测试、静态检查与 probes

全部 pytest 设置 `PYTHONDONTWRITEBYTECODE=1`、`UV_NO_SYNC=1` 并禁用 pytest cache。
专项和固定相关集合直接复跑：

```text
tests/test_code_context_builder_v2.py: 29
tests/test_code_rag_contracts.py: 86
tests/test_code_query_profile_v2.py: 23
tests/test_code_graph_v2.py: 8
tests/test_code_graph_retrieval_v2.py: 18
tests/test_code_history_v2.py: 22
tests/test_code_diff_symbol_v2.py: 10
tests/test_code_test_validation_v2.py: 145

specialized: 29 passed
related: 312 passed
direct total: 341 passed
```

现有测试自身需要 store 的部分仅使用 pytest 管理的隔离临时目录；本 Gate 自定义语义
probes 全部为纯对象/内存构造，不读取任何 repository fixture、evaluation artifact 或
数据库。

```text
ruff check（3 个目标文件）: PASS
ruff format --check（3 个目标文件）: 3 files already formatted
in-memory compile（3 个目标文件）: PASS
trailing whitespace: 0 findings
module exports: 26, unique, package identity exact
package __all__: 373, unique
```

独立 probes 覆盖：

1. project A/B scope 对同一 result：均接受且输出 byte-identical；
2. scope/result/candidate 同步伪造 ACL 与 watermark：接受；
3. 同 block ID 替换正文：接受并输出替换正文；仅 block locator 冲突会拒绝；
4. 同 stable parent 的 target/test 两 child、不同 locator/正文：错误折叠并角色串绑；
5. 无 context block：unavailable，但两个 context 指标错误为 1.0；
6. zero/tiny budget 与多字节 Unicode：整行安全、locator 收窄、pruned 明确；
7. unknown/CALL_PATH：分别 `unknown_task` / `unsupported_task`；
8. dirty 直接 mismatch：拒绝；畸形 dirty marker：错误接受为 clean；
9. relation path 缺失：explanation unavailable、GRAPH_PATH missing；
10. 非规范 percent path 与未截断宽 locator：均错误接受。

## C4/C6、质量 Run、正式 DB 与写锁边界

- 固定相关 312 回归通过，未发现本 C7-01 文件对 C4/C6 既有测试造成回归；该结果不消除
  C7 自身六个 P1。
- C-B6 继续保持 `VERIFIED — PROVISIONAL NOT QUALIFIED`。
- C-B0
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
  仍是唯一 qualified global baseline；本 Gate 没有创建、修改或验证新的 quality Run。
- `/Users/example/project/rag/var/evidence-rag.sqlite3` 及 WAL/SHM 全程按
  `EXTERNAL_MUTABLE_SERVICE_OWNED` 处理：没有打开、哈希、写入、checkpoint 或删除；
  外部服务导致的漂移不归因于本 Gate。
- 本轮保持 `main`、上述 HEAD、单 worktree；没有创建 branch/worktree，没有
  commit/push，没有修改实现、测试、config/deps、其他文档、evaluation/evals/Run、
  fixtures、正式库或 Git。唯一仓库写入是本 review 文件。
- C7-02/C7-03 仍为 `NOT_STARTED / NOT_AUTHORIZED`；修复并复审 C7-01 六个 P1 前，
  不得进入 runtime/API/store/Platform integration。

## C7-01 第二轮精简复审

独立复审时间：2026-07-28 22:45 +0800

本节是定向修复后的最新 Gate 裁决，取代本文件首轮 `FAIL / 6 P1` 的当前状态；首轮内容
继续保留为失败证据与关闭审计。

### 裁决

```text
C7-01 engineering PASS
P0 findings: 0
P1 findings: 0
C7-02 AUTHORIZED
```

首轮六个 P1 均已在本轮独立源码审查、纯内存反例重放和固定回归中关闭。未发现需要阻塞
C7-02 的新 P0/P1；未展开低价值 P2/P3。

### 首轮 P1 定向关闭

#### P1-01 project/ACL/watermark scope authority：CLOSED

`CodeSourceFusionPipeline.search_with_trace()` 现在只在 production pipeline 最终 result
形成后签发进程内、模块私有 key 的 HMAC-SHA256 attestation。payload 精确绑定：

- request project、repository set、commit/branch/target ref；
- `enforce_acl=true` 与完整 allowed ACL set；
- index version、watermark、stable version、generation；
- requested/publication paths；
- 最终 candidate canonical identities、context publication manifest 和整个
  `CodeSourceResult` canonical digest。

builder 拒绝裸 `CodeSourceResult`，并先验证 HMAC、result/candidate/context digest，
再与必填 `ContextBuildScope` 的 project/repository/ref/ACL/index/watermark/version/
generation/path 逐项比较。

独立重放把 candidate、context terminal/block 和 caller scope 一起改成
`acl://forged`，但保留原 production attestation，实际：

```text
ContextBuildScopeError:
scope attestation does not bind the exact fusion result
```

wrong project、watermark tamper、ACL field replacement 也 fail closed。重新让真实
production request 明确授权一个 ACL 属于新的合法 authority，不再与“复制旧
attestation 后自洽伪造”混同。

#### P1-02 source block 正文/metadata 身份：CLOSED

production publication manifest 为每个 block 绑定 project/repository/version/
generation/entity/unit/path/locator/ACL/watermark/role、正文 SHA-256 和完整
`CodeContextBlock.canonical_json_bytes()` SHA-256。builder 复算正文与完整 block
identity 并与已验签 manifest 精确比较。

独立重放：

```text
same block_id + content replacement:
  ContextBuildScopeError: scope attestation does not bind the exact fusion result

same block_id + token_estimate replacement:
  ContextBuildScopeError: scope attestation does not bind the exact fusion result
```

协调替换 candidate/block locator、替换 manifest content identity、result watermark
或其他 provenance 同样不能复用原签名。

#### P1-03 child/entity 去重与 citation 串绑：CLOSED

去重等价键现包含完整 candidate identity、完整 source block identity、正文 identity、
locator、entity、retrieval unit、candidate role、context roles 和 citation identity。
只有这些字段全部等价才可作为 duplicate；不同 child 或不同 entity 即使正文相同也保持
独立 block/citation。

同一 stable entity 的 target/test 两 child 独立重放：

```text
unit://child-a -> target source
  roles=(target_symbol, signature_doc, relevant_ast_blocks, version_locator)

unit://child-b -> test source
  roles=(related_tests)

comprehension blocks=2
duplicate_candidate_identities=()
```

不同 entity、相同正文也输出两个独立 citations。预算舍弃继续进入
`pruned_block_ids`，相关未满足 role 为 `PRUNED`，不再静默吞 source 或把 role 合并到
另一 candidate。

#### P1-04 clean/dirty version grammar：CLOSED

`ContextBuildScope`、`ContextVersion` 和 `ContextCitation` 统一要求 lowercase full
40/64-hex SHA，或精确 `<full-sha>+dirty.<manifest>`。首轮
`+dirty.Manifest!`、39 位 SHA、uppercase SHA，以及空 manifest/非法字符/额外 suffix
均在 scope construction 时 fail closed；合法 dirty 继续精确显示 base、manifest、
generation 和“not equivalent to a Git commit”。

#### P1-05 canonical path/locator 与 exact span：CLOSED

locator 现要求 NFC canonical IRI、raw repository/ref/path round-trip、唯一
`#L<start>-L<end>` fragment；拒绝 percent alias、非规范 fragment、absolute/traversal、
wrong owner/ref、L0 和反向 span。source-bearing block 必须有 exact line span，且未截断
正文行数与 span 精确相等；budget 截断后只按完整行收窄 rendered locator。

独立重放：

```text
%73rc/pct.py:
  ContextBuildLocatorError: canonical NFC IRI text without percent aliases

one-line source + #L10-L20:
  ContextBuildLocatorError: source line count must be exactly equal to locator span

NFC src/café.py + Unicode content, tiny budget:
  rendered source = complete first line
  rendered locator = #L7-L7
```

#### P1-06 context metrics：CLOSED

metric contract 现在显式携带 `AVAILABLE/UNAVAILABLE + value + reason`。label-dependent
context precision/recall 与 answer utility 始终 unavailable；role coverage 和 token
efficiency 只从 selected source blocks、真实 fulfilled roles 与 rendered tokens
计算；locator correctness 只在存在 selected rendered locator 时 available。

一个 TARGET candidate、`context_blocks=()` 的独立结果：

```text
comprehension.status=unavailable
context_precision=(unavailable, null)
context_recall=(unavailable, null)
locator_correctness=(unavailable, null)
role_coverage=(available, 0.0)
answer_utility=(unavailable, null)
```

已不存在无正文却输出 context/locator 满分的路径。

### Production 正向主路与模板

独立 production pipeline probe 直接经过真实 `CodeSourceFusionPipeline.search_with_trace`
签发 attestation，再交给 builder：

- 正常 source 原文保持 identity，retrieval candidate、raw rank/channel、relation path
  和原 fusion trace 未重写；
- 四个模板仍只映射 `IMPLEMENTATION`、`BUG_LOCALIZATION`、`IMPACT_ANALYSIS`、
  `CHANGE_CONTEXT`，role 集合/顺序与 C7-01 冻结计划完全一致；
- unknown/CALL_PATH 等未授权 task 和 upstream refused trace 继续明确 refusal；
- 现有 relation path 才产生 explanation；无 path 继续明确 unavailable/missing；
- target/exact hard signal、role limit、tiny/zero budget、整行 Unicode 截断、
  duplicate/distractor/pruned trace 和幂等 canonical output 均保持；
- module builder exports 27 个，与 package lazy exports 精确同一；新增 attestation
  四个 public contract/version exports 也与 query-profile module 精确同一。

attestation 负向独立重放还确认：

```text
bare strict result -> rejected
copy valid attestation to another signed result -> rejected
replace allowed_acl_refs without new signature -> rejected
replace result content/metadata/watermark/locator -> rejected
wrong project/ref/ACL/index/watermark/version/generation/path -> rejected
```

### 独立回归与静态证据

所有 pytest 设置 `PYTHONDONTWRITEBYTECODE=1`、`UV_NO_SYNC=1` 并禁用 pytest cache。
本轮直接复跑固定八文件：

```text
tests/test_code_context_builder_v2.py: 46
tests/test_code_query_profile_v2.py: 28
tests/test_code_rag_contracts.py: 86
tests/test_code_graph_v2.py: 8
tests/test_code_graph_retrieval_v2.py: 18
tests/test_code_history_v2.py: 22
tests/test_code_diff_symbol_v2.py: 10
tests/test_code_test_validation_v2.py: 145

direct total: 363 passed
```

自定义反例与正向 probes 全部为纯对象/内存构造。固定测试中已有 store 测试仅使用
pytest 管理的隔离临时位置，不读取正式库。

```text
ruff check（5 个目标文件）: PASS
ruff format --check（5 个目标文件）: 5 files already formatted
in-memory compile（5 个目标文件）: PASS
trailing whitespace: 0 findings
builder exports: 27, unique, package identity exact
query-profile exports: 33, unique
package __all__: 378, unique, attestation identity exact
```

实施与主控提供的 Builder 46、query-profile 28、固定 363、Ruff/format/diff/五文件
compile 是上游独立证据；上述源码审查、首轮六项反例重放、production 四模板 probe、
固定 363 和静态检查是本 Gate 的直接证据。

### C4/C6、C7 后续、quality 与正式库边界

- 固定 363 覆盖 C4 contracts/query profile/typed graph 与 C6 history/diff/test
  validation 主路，全部通过；本轮未发现 C4/C6 回归。
- C7-02、C7-03 仍未实现；本轮只授权下一步 C7-02 Platform opt-in integration，
  不等于已发布或默认启用。C7-03 仍 `NOT_STARTED / NOT_AUTHORIZED`。
- package 外没有 `CodeTaskContextBuilder` runtime/API/store/schema 调用点；HMAC
  attestation 只加入既有 production Code source fusion 返回 envelope，没有新增 DB
  persistence、schema 或正式库写路径。
- C-B6 继续保持 `VERIFIED — PROVISIONAL NOT QUALIFIED`；其他负向 quality 结论不因
  C7-01 engineering PASS 改写。
- C-B0
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
  仍是唯一 qualified global baseline。本轮没有创建、修改或验证新的 quality Run，
  也不声明整体 quality、Graph Recall 或 answer utility 提升。
- `/Users/example/project/rag/var/evidence-rag.sqlite3` 及 WAL/SHM 继续按
  `EXTERNAL_MUTABLE_SERVICE_OWNED` 处理：没有打开、哈希、写入、checkpoint 或删除；
  外部服务漂移不归因于本 Gate。
- 本轮保持 `main`、HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`、单
  worktree；没有创建 branch/worktree，没有 commit/push，没有修改实现、测试、
  config/deps、其他文档、evaluation/evals/Run、fixtures、正式库或 Git。唯一仓库写入
  是向本 review 文件追加本节。

## C7-02 Platform Opt-in Integration 独立精简 Gate

审查时间：2026-07-28 23:33 CST

审查分支：`main`

审查 HEAD：`bc3326edc761e3bdb42ed78726a21f314ab44974`

### 裁决

```text
C7-02 engineering FAIL
P0 findings: 0
P1 findings: 1
C7-03 NOT AUTHORIZED
```

C7-02 的 direct/Platform 共享 integration、显式 header、默认 V1、canary、
production V2、V1 fallback、shadow reuse 和 mixed-source 主路均有直接正向证据。
但 production V2 的必填安全前置条件“request + visible repository 唯一解析”未成立：
project-only 请求在同一 project 下存在两个可见、active 且已发布的 repository 时，
当前实现会把两个 repository 一起纳入 governed scope 并真实执行 V2，而不是以固定
`scope_unavailable` 原因 fail-closed 到一次 V1。该问题处在发布选择前的主流程安全
边界，按本 Gate 范围定级为 P1。

### P1-01：project-only scope 可跨两个可见 repository 执行 V2，未唯一解析即 fail-closed

直接源码证据：

- `src/evidence_rag/rag/sources/code/platform_v2.py:288-296` 在调用方未给
  `repository_ids` 时把 store 中全部 repository 作为候选。
- `src/evidence_rag/rag/sources/code/platform_v2.py:297-328` 只要求这些候选最终属于
  一个 project、ACL 可见并至少有一个 active generation；没有要求最终
  `repository_ids` 恰好只有一个。两个同 project 的 ready repository 因而会一起
  进入新 scope。
- `src/evidence_rag/rag/sources/code/platform_v2.py:223-244` 只在
  `_governed_request()` 返回 `None` 时触发 `scope_unavailable` fallback。上述多仓
  scope 非空，因此会继续执行 `search_with_trace()`、验证 attestation 并投影 V2。
- `src/evidence_rag/rag/sources/code/platform_v2.py:465-500` 直到 structured context
  阶段才发现 attestation 含多个 repository，并仅把 context 标为
  `unavailable/scope_unavailable`；此时多仓 V2 检索和响应投影已经完成，不能替代
  routing 层的 fail-closed。
- `tests/test_code_platform_v2.py:217-299` 的 production 正向测试只覆盖显式单
  repository + exact commit；现有测试没有覆盖同 project 下两个 ready repository
  的 project-only 歧义。

#### 最小生产路径复现

本 Gate 在一个新建的隔离临时目录中：

1. 创建并提交包含相同 `SharedSymbol` 的 `repo-one`；
2. 从该仓库本地 clone 出 `repo-two`，使两个 repository 具有相同 full commit SHA；
3. 使用临时 SQLite、临时 repository cache、`ast-v2` 和 `structured-v2` 启动 app；
4. 将两个仓库 ingest 到同一个 project，并分别发布 exact+sparse V2 generation；
5. 向 direct endpoint 发送：

```text
POST /v1/evidence/search
X-RAG-Code-Engine: v2

query = SharedSymbol
scope.project_id = <共同 project>
scope.repository_ids = []
scope.commit = <omitted>
```

实际结果：

```text
HTTP status: 200
visible/ready repository count: 2
routing.engine_requested: v2
routing.engine_selected: v2
routing.selection_reason: header
routing.fallback.status: not_used
routing.fallback.reason: none
code_v2.status: complete
code_v2.scope_attestation: verified
result repositories: repo-one + repo-two
code_v2.context.status: unavailable
code_v2.context.reason: scope_unavailable
```

预期结果是 V2 在调用 pipeline 前拒绝该歧义 scope，只执行一次 legacy V1，并以固定、
清洗后的 `scope_unavailable` 记录 fallback。当前响应证明 scope 与 attestation 可以
对两个仓库保持内部自洽，但这不满足 C7-02 “不能唯一安全解析则固定原因回退 V1”的
明确 Gate 条件。该复现没有伪造 ACL，也不据此声称发生跨 ACL 泄漏；缺陷是安全 scope
未唯一解析仍进入 V2。

#### 最小关闭条件

- `_governed_request()` 在调用 pipeline 前必须把 project/repository/ref/full
  commit/publication/index/watermark/generation/ACL 唯一解析到一个获准 repository；
  project-only 或显式多 repository 不能唯一解析时返回 `None`，由现有路径以固定
  `scope_unavailable` 回退一次 V1。
- 为 direct `/v1/evidence/search` 和 Platform `/v1/search` 增加两个同 project、
  ACL 可见、active 且 V2 published repository 的隔离测试；至少覆盖无
  repository/ref/commit 的 project-only 请求，并断言 V2 pipeline 零次、legacy
  恰好一次、`engine_selected=v1`、fallback reason 固定且结果没有多仓 V2 投影。
- 修复后重放本节 production probe、固定八文件回归及静态检查。不要通过在
  structured builder 阶段继续降级 context 来规避 routing 层的唯一性要求。

### 已直接支持且本轮未发现 P0/P1 回归的主路

- `runtime.py` 只构建一个 `CodePlatformIntegration`，direct API 和
  `PlatformService` 持有同一对象；两个入口的非法 engine header 都返回 422。
- 默认配置仍是 V1；默认和显式 `v1` 都只执行一次 legacy。header 优先于 setting/
  canary；无 override 时 canary bucket 稳定，0/100 边界符合预期，routing trace
  不含 query、ACL 或异常 secret。
- 显式单 repository + exact commit 的 production exact+sparse V2 能验证 publication
  与 scope attestation，返回 V1 主字段集合；V2 扩展只出现在 trace。structured-v2
  只经 `CodeTaskContextBuilder`，无可用 context 时诚实标记 unavailable。
- V2 error/unavailable 路径使用固定清洗 reason 并只执行一次 legacy。shadow 响应仍为
  V1，existing runner 复用同一次 legacy response，同时运行 production V2，不重复
  legacy submit 且不改变用户响应。
- Platform `source=code` 投影和 `code + document` mixed-source/global 请求通过；本轮
  没有发现 header 增加破坏 internal graph/global 调用。
- 没有看到 C7-03、默认 rollout switch 或 persistent quality 实现。

上述正向支持不能关闭 P1-01；唯一 scope 解析是 production V2 readiness 的前置条件，
而不是可由下游 context unavailable 替代的附加能力。

### 独立测试、静态检查与证据边界

所有 pytest 设置 `PYTHONDONTWRITEBYTECODE=1`、`UV_NO_SYNC=1` 并禁用 pytest cache。
本轮直接复跑用户指定的固定八文件：

```text
tests/test_code_platform_v2.py: 6
tests/test_api.py: 3
tests/test_platform.py: 3
tests/test_code_rag_v1_adapter.py: 37
tests/test_code_rag_shadow.py: 86
tests/test_code_query_profile_v2.py: 28
tests/test_code_context_builder_v2.py: 46
tests/test_code_rag_retrieval_v2.py: 17

direct total: 226 passed
```

仅有一条第三方 Starlette deprecation warning，无测试失败。按委托边界没有扩大运行
`tests/test_code_*.py`，也没有把已知两个 CB6 Gate parser stale assertions 归因于
C7-02。

```text
ruff check（7 个 C7-02 目标文件）: PASS
ruff format --check（7 个目标文件）: 7 files already formatted
in-memory compile（7 个目标文件）: PASS
platform module __all__: 4，package identity exact
package __all__: 382，unique
trailing whitespace: 0 findings
```

实施与主控提供的 C1-C7 core 510、最终 API/Platform/V1/shadow 135、固定八文件回归及
Ruff/format/diff/compile 是上游独立证据；本节的源码审查、隔离双仓生产复现、固定
226 和静态检查是本 Gate 的直接证据。依据 evidence-review 的独立性要求，正向证据、
直接反证与未覆盖项分别记录，没有用既有全绿回归覆盖 production scope 反例。

### C7 后续、quality、正式库与写锁边界

- 默认 engine 仍为 V1；C7-02 因上述 P1 未完成，C7-03 尚未实现且
  `NOT AUTHORIZED`。本 Gate 不授权 rollout/default switch/persistent quality。
- C-B6 继续保持 `VERIFIED — PROVISIONAL NOT QUALIFIED`；其他负向 quality 结论不因
  C7-02 平台集成正向测试改写。
- C-B0
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
  仍是唯一 qualified global baseline。本轮没有创建、修改或验证新 quality Run。
- `/Users/example/project/rag/var/evidence-rag.sqlite3` 及 WAL/SHM 始终按
  `EXTERNAL_MUTABLE_SERVICE_OWNED` 处理：没有打开、哈希、写入、checkpoint 或删除。
  所有 production probe 与测试只使用新建的隔离临时 SQLite/cache/repository，临时
  资源由其生命周期回收。
- 本轮保持 `main`、HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`；没有创建
  branch/worktree，没有 commit/push，没有修改实现、测试、config/deps、其他文档、
  evaluation/evals/Run、fixtures、正式库或 Git。`tests/test_api.py`、
  `tests/test_platform.py` 等既有 dirty 内容未被本 Gate 修改。唯一仓库写入是向本
  review 文件追加本 C7-02 Gate 节。

## C7-02 第二轮极简复审

审查时间：2026-07-28 23:56 CST

审查分支：`main`

审查 HEAD：`bc3326edc761e3bdb42ed78726a21f314ab44974`

### 裁决

```text
C7-02 engineering PASS
P0 findings: 0
P1 findings: 0
C7-03 AUTHORIZED
```

本轮只复审首轮唯一 P1 的关闭和 C7-02 主路回归。源码前置条件、独立隔离 production
probe、新增双仓测试与固定八文件回归一致：歧义 scope 已在 V2 pipeline 前
fail-closed，单仓 exact-commit attested V2 仍正常。首轮 P1 已关闭。

### 首轮 P1-01：唯一 governed scope 前置条件——CLOSED

`src/evidence_rag/rag/sources/code/platform_v2.py:284-389` 现在仅在全部条件成立时
构造 V2 request：

- 调用方显式提供恰好一个 repository ID 和 project ID；
- branch 必须为空，commit 必须是精确 40/64 位小写十六进制；
- repository ID + project ID 必须在 store 中唯一匹配；
- repository ACL 必须可见；
- active generation 必须唯一，与 repository 的 `active_generation_id` 一致，且
  repository 为 ready、`head_commit` 精确等于 requested commit；
- generation 必须属于该 repository、状态为 published、commit 精确一致；
- active code-index publication 必须绑定同一 project/repository/generation，状态为
  published，并声明可用 sparse retrieval。

project-only、显式多 repository、branch/non-canonical commit、project/repository/ACL/
generation/publication 任一缺失、冲突或不唯一都会返回 `None`。
`platform_v2.py:223-225` 随即在调用 `search_with_trace()` 前产生固定
`scope_unavailable`；`platform_v2.py:177-201` 只执行一次 legacy V1 fallback。
下游 structured context 不再承担 routing 唯一性判定。

新增 `tests/test_code_platform_v2.py:315-431` 同时覆盖 direct 和 Platform：

- 同 project 双仓的 project-only 请求；
- 同 project 双仓的显式 multi-repository 请求；
- 四个请求均断言 V2 pipeline 0 次；
- legacy 每请求恰好 1 次；
- `engine_selected=v1`、fallback reason 为固定 `scope_unavailable`；
- fallback trace 没有 scope attestation，因而没有多仓 V2 投影。

### 独立隔离 production probe

本 Gate 使用新建临时 SQLite、cache 和两个同 project、同 full commit、public、
ready 且 exact+sparse published 的临时 repository，独立计数真实
`search_with_trace()` 与 legacy 调用。

四个负向请求结果：

```text
direct project-only:
  pipeline=0, legacy=1, engine=v1, reason=scope_unavailable, attestation=false
Platform project-only:
  pipeline=0, legacy=2, engine=v1, reason=scope_unavailable, attestation=false
direct explicit multi-repository:
  pipeline=0, legacy=3, engine=v1, reason=scope_unavailable, attestation=false
Platform explicit multi-repository:
  pipeline=0, legacy=4, engine=v1, reason=scope_unavailable, attestation=false
```

随后在同一隔离实例中发送单 repository + project + exact full commit：

```text
direct:
  engine=v2, fallback=not_used, scope_attestation=verified
  result repositories={requested repository}
Platform code+document:
  engine=v2, scope_attestation=verified, projected sources={code}
final counters:
  pipeline=2, legacy=4
```

因此四个负向请求没有进入 V2，两个正向请求各进入一次真实 attested V2，且没有额外
legacy 调用。structured context 在该最小 fixture 没有足够 context 时继续诚实返回
unavailable，不影响已验证的 scope attestation 或 V1 主字段投影。

probe 准备阶段有两个夹具级失败，均在最终产品断言前定位并修正：

- 第一次把 macOS `/var/...` 临时路径直接用于 allowed root，resolved repository 实际
  为 `/private/var/...`，ingestion 按设计拒绝越界；canonicalize 临时根后重放。
- 第二次在构造 response 列表时预先执行了四个请求，导致第一条逐次计数断言看到
  `legacy=4`；当时 `pipeline=0`。改为逐请求发送、逐请求断言后完整通过。

这两项是独立 probe harness 的路径和求值顺序错误，不是产品失败；本报告保留它们，
没有以最终通过隐藏预备失败。

### 主路不回归与静态证据

本轮固定八文件直接回归，设置 `PYTHONDONTWRITEBYTECODE=1`、`UV_NO_SYNC=1` 并禁用
pytest cache：

```text
tests/test_code_platform_v2.py: 7
tests/test_api.py: 3
tests/test_platform.py: 3
tests/test_code_rag_v1_adapter.py: 37
tests/test_code_rag_shadow.py: 86
tests/test_code_query_profile_v2.py: 28
tests/test_code_context_builder_v2.py: 46
tests/test_code_rag_retrieval_v2.py: 17

direct total: 227 passed
```

仅有一条第三方 Starlette deprecation warning。该集合确认单仓 production V2、
default/explicit header/canary、V1 schema/projection、固定清洗 fallback、shadow
legacy reuse、mixed-source/global、attested builder/context 和 C4-C6 相关主路未回归。
按委托没有扩大到已知含历史 CB6 stale parser assertions 的全量
`tests/test_code_*.py`。

```text
ruff check（7 个 C7-02 目标文件）: PASS
ruff format --check（7 个目标文件）: 7 files already formatted
in-memory compile（7 个目标文件）: PASS
git diff --check（4 个 tracked integration 文件）: PASS
7 个目标文件 trailing whitespace: 0 findings
```

实施提供的 C7 专项 7、固定八文件 227、C1-C7 扩展 511 与主控独立 227 是上游证据；
本节源码核对、最终隔离双仓/单仓 production probe、固定 227 和静态检查是本 Gate 的
直接证据。按 evidence-review 独立性要求，直接支持、夹具失败和排除项分别记录。

### C7 后续、quality、正式库与写锁边界

- 默认 engine 仍为 V1；C7-03 尚未实现。本次 `C7-03 AUTHORIZED` 只授权下一阶段
  engineering，不代表 C7-03 已完成、已发布或默认启用，也不授权未审 rollout 或
  persistent quality。
- C-B6 继续保持 `VERIFIED — PROVISIONAL NOT QUALIFIED`；其他负向 quality 结论不因
  C7-02 engineering PASS 改写。
- C-B0
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
  仍是唯一 qualified global baseline。本轮没有创建、修改或验证新 quality Run。
- `/Users/example/project/rag/var/evidence-rag.sqlite3` 及 WAL/SHM 始终按
  `EXTERNAL_MUTABLE_SERVICE_OWNED` 处理：没有打开、哈希、写入、checkpoint 或删除。
  所有测试和 production probe 只使用生命周期受控的隔离临时 SQLite/cache/
  repositories。
- 本轮保持 `main`、HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`；没有修改
  workspace Git、创建 branch/worktree、commit 或 push，没有修改实现、测试、
  config/deps、其他文档、evaluation/evals/Run、fixtures 或正式库。临时 fixture
  内部 Git 仓库只用于生成可检索 exact commit，并随临时目录回收。唯一仓库写入是向
  本 review 文件追加本第二轮复审节。

## C7-03 Shadow/Canary/Release Evidence 独立精简 Gate

审查时间：2026-07-29 00:37 CST

审查分支：`main`

审查 HEAD：`bc3326edc761e3bdb42ed78726a21f314ab44974`

### 裁决

```text
C7-03 engineering FAIL
P0 findings: 0
P1 findings: 3
C7 overall engineering NOT COMPLETE
Release decision: HOLD_DEFAULT_V1
```

当前 release truth 确实只能 `HOLD_DEFAULT_V1`，默认 runtime 也仍为 V1；本 Gate
没有发现实际 rollout、default switch、persistent release state 或 production quality
claim。但真实 C-B6 immutable identity、canary tamper rollback severity 和逐阶段
component evidence identity 三项明确 Gate 条件未满足。它们均处于 release evidence
主流程，不因专项及十文件回归全绿而降级。

### P1-01：current snapshot 用 placeholder 替换已知 C-B6 immutable Run identity

`src/evidence_rag/rag/sources/code/release_v2.py:1515-1534` 的
`_CURRENT_CB_RUN_IDS` 把 C-B6 记录为：

```text
unavailable://code-evidence/C-B6-artifact-identity
```

但 workspace 已有且反复核验的唯一 C-B6 production audit identity 是：

```text
evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e
```

直接来源包括：

- `docs/rag-optimization/development/01_CODE_SOURCE_DEVELOPMENT_PLAN.md:88-89`；
- `docs/rag-optimization/development/reviews/07_CODE_C6_GATE_REVIEW.md:752-759`；
- `docs/rag-optimization/interview/01_CODE_SOURCE_INTERVIEW.md:231-232`。

纯对象 probe：

```text
current_release_evidence().artifacts[C_B6].artifact_id
  = unavailable://code-evidence/C-B6-artifact-identity
expected exact known identity
  = evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e
identity exact: false
status: UNAVAILABLE
disposition: PROVISIONAL_NOT_QUALIFIED
```

`UNAVAILABLE` 可以诚实表示当前没有满足 release contract 的 source/attestation，但不能
抹去已知 immutable artifact ID。当前测试只精确断言 C-B0 identity
（`tests/test_code_release_v2.py:303-305`），没有断言 C-B6 exact identity，因而让
placeholder 穿过专项。

最小关闭条件：保留 C-B6 的 `UNAVAILABLE` evidence status 和
`PROVISIONAL_NOT_QUALIFIED` disposition，同时把 artifact ID 精确记录为上述真实 URI；
新增 current snapshot exact identity 断言，并继续确认 C-B6 不能满足 required
`QUALIFIED` disposition。

### P1-02：canary evidence 可通过篡改 stage 降级 rollback severity 为 HOLD

`src/evidence_rag/rag/sources/code/release_v2.py:1348-1356` 在 contract/
attestation 验证前直接从不可信 payload 读取 stage；`release_v2.py:1359-1369` 又用该
不可信 stage 决定 `ROLLBACK_REQUIRED` 或 `HOLD_DEFAULT_V1`。`evaluate_release()`
没有单独的 trusted deployed/current stage 输入。

最小纯内存复现：

1. 构造并签发完整 `OFFLINE → SHADOW_INTERNAL_100 → CANARY_5` TEST chain；
2. 只把 CANARY candidate payload 的 `stage` 改为 `OFFLINE`，不重新签名，其他字段和
   previous digest 保持原样；
3. 带完整 prior chain、policy、verifier、`allow_test_evidence=True` 调用 evaluator。

实际结果：

```text
status: HOLD_DEFAULT_V1
evidence_stage: OFFLINE
blockers: (release_contract:invalid_or_tampered)
```

原始部署阶段已进入 CANARY_5；按冻结要求，tamper/verification failure 在 canary 及
以上必须返回固定 rollback plan。攻击者仅改未验证 stage 即可把必回滚严重度降为
HOLD，虽然没有取得 promotion，仍破坏 release safety decision。

最小关闭条件：evaluator 必须接收并验证独立 trusted deployed/current stage，或使用
不能被 candidate payload 降级的 stage authority；fail-closed severity 至少取 trusted
stage 与已验证 chain stage 的最高值。新增 CANARY_5/25、OPT_IN_100、DEFAULT_V2 的
stage downgrade、invalid stage 和 attestation tamper probes，均应
`ROLLBACK_REQUIRED`；OFFLINE/SHADOW 才可 HOLD。

### P1-03：required component attestations 未绑定 stage，跨阶段原样复制仍可 promotion

`EvidenceAttestation` 绑定 component subject digest、source digest/version 和
verifier，但没有 stage 或 parent release-snapshot identity。`ReleaseMetric`、
`ReleaseArtifact`、`ReleaseLatencyEvidence`、`ReleaseCostEvidence` 自身也没有 stage
binding。重新签发一个 stage-specific top-level snapshot 只能证明新 envelope 包含这些
child objects，不能证明每阶段重新产生并签发 required evidence。

专项的完整 promotion fixture 本身暴露了该缺口：

```text
OFFLINE.metrics == SHADOW_INTERNAL_100.metrics: true
all metric attestations exact equal: true
OFFLINE.artifacts == SHADOW_INTERNAL_100.artifacts: true
OFFLINE.latency == SHADOW_INTERNAL_100.latency: true
OFFLINE.cost == SHADOW_INTERNAL_100.cost: true

evaluate SHADOW with OFFLINE prior:
  status: PROMOTION_APPROVED
  next_stage: CANARY_5
  blockers: ()
```

即 required metrics、artifact evidence、latency 和 cost 连同原 attestation 均可跨
stage 复制；只有 top-level snapshot、shadow aggregate 和按 sample count 构造的
guardrails 改变。previous snapshot digest/full chain/no-skip 检查不能弥补 child
component 缺少 stage binding。这与“每阶段重新验证全部门”和“cross-stage copy
fail-closed”的明确条件冲突。

最小关闭条件：每个 stage-required component attestation 必须绑定当前
`ReleaseStage` 及 parent snapshot/stage-evidence identity（或等价的唯一 stage
issuance identity），evaluator 拒绝从 prior stage 复制的 component/source/
attestation。为 metric、guardrail、artifact、latency、cost、shadow 分别增加跨阶段
copy probes；重新签 top-level envelope 不应使旧 stage child evidence 合法。

### 已直接支持且未发现其他 P0/P1 的范围

- frozen stage order 精确为
  `OFFLINE → SHADOW_INTERNAL_100 → CANARY_5 → CANARY_25 → OPT_IN_100 → DEFAULT_V2`；
  previous digest、完整 prior chain、no-skip 和逐 snapshot 重验已实现。
- 12 个 required metric 的 operator/threshold/unit 与计划一致；AVAILABLE、
  numerator/denominator 一致性、minimum denominator、精确边界通过和越界失败均有
  专项覆盖。PROVISIONAL/UNAVAILABLE 阻止 promotion。
- production evidence 拒绝内置 test verifier；TEST evidence 只有显式 TEST、
  `allow_test_evidence=True` 且 test verifier 可用时才能走 synthetic promotion。
  source/digest/version/signature、stale profile、artifact version、latency denominator、
  timeout 等反例在未触发上述 stage downgrade 时均 fail-closed。
- required artifact kinds、latency P50/P95/P99、cost、security/canary/rollback evidence
  都有 frozen contract；C-B0 disposition 仍是唯一 QUALIFIED，C-B1~C-B5 为
  NOT_QUALIFIED、C-B6 为 PROVISIONAL_NOT_QUALIFIED，当前缺失 release attestation 的
  artifacts 均诚实标 UNAVAILABLE。
- 八项 guardrail 全部 required；missing/UNKNOWN/FAIL 阻止 promotion。正常 CANARY
  leakage 与 verification-timeout probes 返回 `ROLLBACK_REQUIRED`。
- shadow counter model 不含 query/ACL/path/identity 字段且 extra-forbid；secret/PII/
  absolute-path safe-text 验证、batch/sample bound、zero-sample UNAVAILABLE、partial
  denominator PROVISIONAL、nearest-rank P50/P95/P99 与 deterministic aggregation
  均有直接证据。quality metrics 不由 shadow scan counters 推导。
- rollback action 顺序与计划一致，最后明确 preserve stable entity generation；
  evaluator 只返回 frozen plan，`runtime_engine=v1`、`configuration_changed=false`，
  不执行 configuration/publication/DB mutation。
- `release_v2` 只被 code package lazy-export；runtime/config/platform 没有 C7-03
  接线。`config.py` 默认仍为 engine V1、shadow false、canary 0。

这些正向证据不能关闭三个 P1；engineering contracts 全绿也不等于 release quality。

### 独立回归与静态证据

全部 pytest 使用 `PYTHONDONTWRITEBYTECODE=1`、`UV_NO_SYNC=1` 并禁用 pytest cache。
本 Gate 直接复跑专项及 C7/platform/query/context/contracts 十文件：

```text
tests/test_code_release_v2.py: 27
tests/test_code_platform_v2.py: 7
tests/test_api.py: 3
tests/test_platform.py: 3
tests/test_code_rag_v1_adapter.py: 37
tests/test_code_rag_shadow.py: 86
tests/test_code_query_profile_v2.py: 28
tests/test_code_context_builder_v2.py: 46
tests/test_code_rag_retrieval_v2.py: 17
tests/test_code_rag_contracts.py: 86

direct total: 340 passed
```

仅有一条第三方 Starlette deprecation warning。

```text
ruff check（release_v2.py、code/__init__.py、test_code_release_v2.py）: PASS
ruff format --check（三文件）: 3 files already formatted
in-memory compile（三文件）: PASS
三文件 trailing whitespace: 0 findings
release module exports: 61，unique，package identity exact
package __all__: 443，unique
```

实施与主控提供的专项 27、十文件 340 和三文件静态检查是上游独立证据；上述源码审查、
三项纯对象反例、直接 340 与静态检查是本 Gate 证据。按 evidence-review 独立性要求，
支持项、直接反证和缺失测试分别记录，没有以全绿回归覆盖反例。

### Release、quality、正式库与写锁边界

- 当前 snapshot 缺少全部 required metrics/guardrails、release-qualified artifacts、
  latency/cost/shadow evidence 和 attestation；真实 release decision 必须继续为
  `HOLD_DEFAULT_V1`。本轮没有 release，默认仍为 V1。
- C7-03 engineering 未通过，因此 `C7 overall engineering NOT COMPLETE`；不得写
  `C7 overall engineering COMPLETE`，也不得据此执行 actual rollout/default switch。
- C-B0
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
  仍是唯一 qualified global baseline。C-B1~C-B5 继续 NOT QUALIFIED，C-B6
  `evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e`
  继续 `VERIFIED — PROVISIONAL NOT QUALIFIED`；其他负向 quality 不变。
- `/Users/example/project/rag/var/evidence-rag.sqlite3` 及 WAL/SHM 始终按
  `EXTERNAL_MUTABLE_SERVICE_OWNED` 处理：没有打开、哈希、写入、checkpoint 或删除。
  自定义 probes 为纯内存对象；pytest 只使用其隔离临时位置。
- 本轮保持 `main`、HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`；没有修改
  workspace Git、创建 branch/worktree、commit 或 push，没有修改实现、测试、
  config/deps、其他文档、evaluation/evals/Run、fixtures 或正式库。唯一仓库写入是向
  本 review 文件追加本 C7-03 Gate 节。

## C7-03 第二轮极简复审

审查时间：2026-07-29 01:04 CST

审查分支：`main`

审查 HEAD：`bc3326edc761e3bdb42ed78726a21f314ab44974`

### 裁决

```text
C7-03 engineering PASS
P0 findings: 0
P1 findings: 0
C7 overall engineering COMPLETE
Release decision: HOLD_DEFAULT_V1
```

本轮只复审首轮三个 P1 的定向关闭与 C7 主路不回归。三个 finding 均由源码、专项和
独立纯内存 probes 关闭；未发现新增 P0/P1。`C7 overall engineering COMPLETE` 只表示
C7-01/C7-02/C7-03 engineering Gate 全部完成，不表示 release 或 quality Gate 已通过。
当前真实 decision 继续为 `HOLD_DEFAULT_V1`。

### 首轮 P1-01：C-B6 exact immutable identity——CLOSED

`src/evidence_rag/rag/sources/code/release_v2.py:51` 冻结：

```text
CB6_PROVISIONAL_RUN_ID =
evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e
```

`release_v2.py:1663-1683` 的 current CB map 精确使用该常量。
`current_release_evidence()` 的直接 probe 确认：

```text
C-B6 artifact_id:
  evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e
evidence_status: UNAVAILABLE
disposition: PROVISIONAL_NOT_QUALIFIED
current release decision: HOLD_DEFAULT_V1
blocker contains:
  artifact:c_b6:disposition_provisional_not_qualified
```

因此 current truth 既保留已知 immutable identity，又没有把 verified artifact
冒充 release-qualified evidence。`tests/test_code_release_v2.py:350-398` 已加入 exact
URI、status、disposition 和 HOLD 断言。

### 首轮 P1-02：trusted deployed-stage rollback severity——CLOSED

`evaluate_release()` 在 `release_v2.py:1506-1515` 将 `deployed_stage` 设为 required
keyword-only 参数。`release_v2.py:1471-1479` 独立解析 trusted authority；
invalid authority 不使用 candidate 推断，而在 `release_v2.py:1519-1528` 保守采用
`DEFAULT_V2` severity 并返回 `ROLLBACK_REQUIRED`。缺失该参数直接产生 Python
`TypeError`，不能静默解释为 OFFLINE。

contract/stage/top attestation/child attestation tamper 和 verification timeout 的
fail severity 全部由 trusted deployed stage 决定：

```text
trusted OFFLINE:
  candidate mismatch / invalid stage / top tamper / child tamper / timeout -> HOLD_DEFAULT_V1
trusted SHADOW_INTERNAL_100:
  candidate mismatch / invalid stage / top tamper / child tamper / timeout -> HOLD_DEFAULT_V1
trusted CANARY_5:
  all five failures -> ROLLBACK_REQUIRED
trusted CANARY_25:
  all five failures -> ROLLBACK_REQUIRED
trusted OPT_IN_100:
  all five failures -> ROLLBACK_REQUIRED
trusted DEFAULT_V2:
  all five failures -> ROLLBACK_REQUIRED
invalid trusted authority:
  ROLLBACK_REQUIRED, blocker=trusted_deployed_stage:invalid
```

candidate 与 trusted stage 不同还会产生固定
`trusted_deployed_stage:candidate_stage_mismatch` blocker。首轮仅改 candidate stage
即可降低 severity 的反例现已关闭。

### 首轮 P1-03：stage/parent-bound child evidence——CLOSED

v2 contracts 将 stage 与 parent snapshot digest 同时冻结到三层：

- `EvidenceSource`：`release_v2.py:436-451`；
- `EvidenceAttestation` 及签名 payload：`release_v2.py:454-545`；
- 所有 `_AttestedReleaseModel` child component：`release_v2.py:548-576`；
- evaluator 的 component/source/attestation 三层重验：
  `release_v2.py:1128-1167`。

release snapshot 自身还要求 `release_stage == stage` 且
`parent_evidence_digest == previous_evidence_digest`
（`release_v2.py:1004-1040`）。Source、attestation、release evidence、metric、
guardrail、artifact、decision、evaluator、latency、cost 和 shadow 的变更 contract
均使用 v2 version；policy/rollback 等未变 schema 保持原 version。

独立把 OFFLINE child 连同其 source 和 attestation 复制到 SHADOW，且只重新签 top
envelope，六类均 fail-closed：

```text
metric: HOLD_DEFAULT_V1
  component/source/attestation_stage_parent_mismatch
guardrail: HOLD_DEFAULT_V1
  component/source/attestation_stage_parent_mismatch
artifact: HOLD_DEFAULT_V1
  component/source/attestation_stage_parent_mismatch
latency: HOLD_DEFAULT_V1
  component/source/attestation_stage_parent_mismatch
cost: HOLD_DEFAULT_V1
  component/source/attestation_stage_parent_mismatch
shadow: HOLD_DEFAULT_V1
  component/source/attestation_stage_parent_mismatch
```

相反，每阶段重新生成和签发全部 child 的完整 TEST chain 仍严格逐级通过：

```text
OFFLINE -> PROMOTION_APPROVED -> SHADOW_INTERNAL_100
SHADOW_INTERNAL_100 -> PROMOTION_APPROVED -> CANARY_5
CANARY_5 -> PROMOTION_APPROVED -> CANARY_25
CANARY_25 -> PROMOTION_APPROVED -> OPT_IN_100
OPT_IN_100 -> PROMOTION_APPROVED -> DEFAULT_V2
DEFAULT_V2 -> PROMOTION_APPROVED -> no next stage
```

每个 synthetic stage 仍必须显式 `EvidenceClass.TEST`、
`allow_test_evidence=True` 和 test verifier；所有 decision 都保持
`runtime_engine=v1`、`configuration_changed=false`。正常 TEST promotion 只验证
evaluator contract，不构成 production release。

### 主路不回归与独立证据

首轮已支持的 metric thresholds、AVAILABLE/PROVISIONAL/UNAVAILABLE、numerator/
denominator/unit、artifact disposition、全部 guardrails、shadow sanitized/bounded/
deterministic aggregation、fixed rollback order、production/test verifier 分离、
previous digest/no-skip/full chain 和 current HOLD 主路均继续通过。

本轮纯内存 probes 还确认：

- C-B0 仍是唯一 QUALIFIED global baseline，C-B1~C-B5 继续 NOT QUALIFIED，
  C-B6 继续 PROVISIONAL_NOT_QUALIFIED；
- missing/invalid trusted stage 不会默认为 OFFLINE；
- canary 及以上所有指定 tamper/timeout failure 返回 frozen rollback plan；
- OFFLINE/SHADOW 同类失败仅 HOLD；
- six child cross-stage copy 即使重签 top 也不能 promotion；
- 正常逐阶段重新签发仍保持固定 order 和 idempotent decision。

审计过程中曾基于截断输出提出 latency/cost/shadow contract 可能仍为 v1 的假设，并在
probe 中故意断言 `endswith("-v1")`。该审计断言失败，因为实际常量分别是：

```text
c7-code-latency-evidence-v2
c7-code-cost-evidence-v2
c7-code-shadow-aggregate-v2
```

随后用 v2 常量、required `release_stage` 和存在 `parent_evidence_digest` 的正向 schema
断言重跑通过。这是审计假设被源码证伪，不是产品失败；按 evidence-review 独立性要求
保留记录，没有把它计为 finding。

### 独立回归与静态检查

全部 pytest 使用 `PYTHONDONTWRITEBYTECODE=1`、`UV_NO_SYNC=1` 并禁用 pytest cache。
本轮直接复跑专项及 C7/platform/query/context/contracts 十文件：

```text
tests/test_code_release_v2.py: 41
tests/test_code_platform_v2.py: 7
tests/test_api.py: 3
tests/test_platform.py: 3
tests/test_code_rag_v1_adapter.py: 37
tests/test_code_rag_shadow.py: 86
tests/test_code_query_profile_v2.py: 28
tests/test_code_context_builder_v2.py: 46
tests/test_code_rag_retrieval_v2.py: 17
tests/test_code_rag_contracts.py: 86

direct total: 354 passed
```

仅有一条第三方 Starlette deprecation warning。

```text
ruff check（release_v2.py、code/__init__.py、test_code_release_v2.py）: PASS
ruff format --check（三文件）: 3 files already formatted
in-memory compile（三文件）: PASS
三文件 trailing whitespace: 0 findings
release module exports: 63，unique，package identity exact
package __all__: 445，unique
```

实施与主控提供的专项 41、十文件 354 和三文件静态检查是上游证据；本节源码核对、
trusted severity matrix、six-child copy/full-chain probes、直接 354 和静态检查是本
Gate 的独立证据。

### Engineering complete、release、quality、正式库与写锁边界

- `C7 overall engineering COMPLETE` 不等于 release。C7-03 evaluator 只返回 decision/
  rollback plan；没有 runtime/config/platform wiring、actual rollout、default switch
  或 persistent release state。
- 当前 snapshot 仍缺少 required production metrics、guardrails、release-qualified
  artifacts、latency/cost/shadow evidence 和 production attestations，质量门未达；
  `Release decision: HOLD_DEFAULT_V1`，默认 engine 仍为 V1。
- C-B0
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
  仍是唯一 qualified global baseline。C-B1~C-B5 继续 NOT QUALIFIED，C-B6
  `evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e`
  继续 `VERIFIED — PROVISIONAL NOT QUALIFIED`；其他负向 quality 不变。
- `/Users/example/project/rag/var/evidence-rag.sqlite3` 及 WAL/SHM 始终按
  `EXTERNAL_MUTABLE_SERVICE_OWNED` 处理：没有打开、哈希、写入、checkpoint 或删除。
  自定义 probes 全为纯内存对象；pytest 只使用其隔离临时位置。
- 本轮保持 `main`、HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`；没有修改
  workspace Git、创建 branch/worktree、commit 或 push，没有修改实现、测试、
  config/deps、其他文档、evaluation/evals/Run、fixtures 或正式库。唯一仓库写入是向
  本 review 文件追加本 C7-03 第二轮复审节。
