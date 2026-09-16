# Codex X0-01 Golden/Evaluation Foundation 独立 Gate Review

日期：2026-07-29
实施任务：`019fa9b9-0368-7771-8bcc-7636a8ccd248`
开发计划任务：`019fa9b8-6506-7831-af43-4a8666a0d45a`
评审范围：Codex X0-01 Golden/Evaluation Foundation；不执行 X-B0
正式库边界：`EXTERNAL_MUTABLE_SERVICE_OWNED`

## 1. Gate 结论

`CODEX X0-01 FOUNDATION FAIL`

- `P0 findings: 0`
- `P1 findings: 3`
- `X-B0 HARNESS PREPARATION NOT AUTHORIZED`
- X-B0 尚未执行；没有 Codex baseline metrics、Evaluation Run、Run artifact 或 qualification。
- X1 仍为 `NOT_STARTED / NOT_AUTHORIZED`；X2–X5 同样未授权。
- Code C-B0 及其历史工程证据不属于 Codex X-B0，不能作为本 Gate 的 baseline。
- Release posture 继续为 `HOLD_DEFAULT_V1 / NOT RELEASED / default V1`。
- fixture manifest 的 `released=true` 是包内自报字段；本 Gate 未接受其为已批准的
  Golden release。

现有 29 项测试与四文件静态检查虽然全部通过，但独立对抗性 probes 仍能突破 released
membership、质量真值和 canonical package 边界，因此不能授权下一步。

## 2. P1 findings

### P1-01 — Evaluator 信任 reviewed row 自报字段，质量指标未绑定 released truth

`ReviewedRetrievalRow` 允许调用方直接填写 `episode_id`、`call_id`、`patch_applied`、
`validation_status`、`false_validated`、`outcome_correct` 和 `context_noise`
（`evaluation_v1.py:532-548`）。其 identity validator 只核对 row 内的
`thread_id → item_id → locator` 语法（`evaluation_v1.py:550-562`）；loader 后续只把
`item_id/locator` 绑定到 released fixture（`evaluation_v1.py:974-990`）。

Evaluator 随后直接使用这些自报字段：

- `episode_id` 进入 Episode Recall@5（`evaluation_v1.py:1021-1034`）；
- `call_id` 进入 call/result accuracy（`evaluation_v1.py:1133-1148`）；
- `patch_applied`、`validation_status`、`outcome_correct` 进入状态指标
  （`evaluation_v1.py:1150-1170`）；
- `false_validated` 自己决定 False Validated 的分子和分母
  （`evaluation_v1.py:1215-1224`）；
- `context_noise` 自己决定 context noise 指标（`evaluation_v1.py:1254-1267`）。

此外，released case schema 没有计划合同要求的 `eligible_metrics`
（`evaluation_v1.py:341-364`），所有 45 cases 的该字段实际计数为 0。Evaluator 用
case shape/row presence 临时推导分母，而不是消费 frozen per-case eligibility。显式传入
空 membership 也被 `case_membership or expected_membership` 当作未传入
（`evaluation_v1.py:957-960`）。

最小复现（仅内存/临时 fixture）：

```python
case = dataset.cases[0]
negative = released_items[case.hard_negatives[0].item_id]
row = ReviewedRetrievalRow(
    dataset_id=dataset.dataset_id,
    dataset_version=dataset.dataset_version,
    package_hash=dataset.package_hash,
    case_id=case.case_id,
    rank=1,
    thread_id="codex-golden-archived",
    item_id=negative.id,
    locator=negative.source_locator,
    reviewed=True,
    episode_id=case.expected_threads[0].episode_id,
    false_validated=False,
    context_noise=False,
)
metrics = evaluate_reviewed_codex_retrieval(
    dataset,
    [row],
    case_membership=[],
)
```

实际结果：

```text
explicit_empty_membership_accepted = true
thread_recall_at_5 = 0.0
item_recall_at_10 = 0.0
episode_recall_at_5 = 0.025
false_validated_rate_at_10.status = AVAILABLE
false_validated_rate_at_10.value = 0.0
```

一个 released hard negative 在 Thread/Item 均未命中的情况下，仅靠伪造
`episode_id` 就制造了 Episode 命中；同一 row 又能自行把 False Validated 变成
`AVAILABLE / 0.0`。这会使后续 X-B0 artifact 的状态、时序和 context 质量结论不可信。

补充 fail-closed probe：

```text
cross-thread item identity: REJECT
wrong package hash: REJECT
rank gap: REJECT
duplicate rank: REJECT
unknown item: REJECT
locator mismatch: REJECT
explicit empty case membership: ACCEPT
```

### P1-02 — 45 个 hard-negative entries 未覆盖设计要求的主路和 eligible denominator

真实 `cases.jsonl` 结构计数为：

```text
cases = 45
unique positives = 40
hard-negative entries = 45
unique hard-negative items = 7
event truths = 31
```

45 个 negative entries 中，35 个都复用同一个
`archived_claim / assistant_claim_without_exit`。构建程序的默认 case helper 明确产生
这一复用（`build_fixture.py:519-550`）。实际 reason/item 分布为：

```text
assistant_claim_without_exit = 35 entries / 1 unique item
privacy_sensitive            =  5 entries / 1 unique item
archived_duplicate           =  1 entry   / 1 unique item
failed_attempt               =  1 entry   / 1 unique item
patch_failure                =  1 entry   / 1 unique item
read_only_not_change         =  1 entry   / 1 unique item
subagent_same_topic          =  1 entry   / 1 unique item
```

独立按 released hard-negative membership 检查设计的十条主路：

```text
same path / different thread       covered
old failed attempt                 covered
plan-only negative                 MISSING
assistant claim without exit       covered
failed patch call                  covered
read-only is not change            covered
same command / multiple statuses   MISSING
archived/current duplicate         covered
truncated ToolResult negative      MISSING
subagent same topic                covered
```

fixture 中虽存在 plan、old/new 同命令和 truncated ToolResult 场景，但它们没有进入对应
case 的 hard-negative truth membership，不能作为 harmful-negative denominator。现有测试
只断言每个 case 的 `hard_negatives` 非空（`test_codex_evaluation_v1.py:156`），因此 35 次
重复同一 claim 也会通过。

False Validated 同样没有 frozen eligible set：只有 `codex-v1-035` 带
`state_labels=["false_validated"]`，没有 invoked-no-exit 或 target-unknown eligible cases，
且全部 cases 都没有 `eligible_metrics`。这不满足计划要求 claim-only、mentioned-only、
invoked-no-exit、failed-exit、target-unknown 均进入 False Validated denominator 的合同，
也无法证明调用方不能删改 eligible denominator。

### P1-03 — Listed fixture 可替换为包外 symlink，path alias 未 fail-closed

Loader 对 manifest path 做字符串 canonical 校验，但读取 listed file 时直接
`Path.read_bytes()`，inventory 使用 `rglob(...).is_file()`（`evaluation_v1.py:739-765`）。
它没有拒绝 symlink，也没有用 `lstat`/resolved containment 证明 listed file 的真实对象
仍位于 package root。

最小复现（全部位于一个隔离临时目录）：

```python
shutil.copytree(FIXTURE, alias_root)
listed = alias_root / "sessions" / "plan.jsonl"
outside = temp_root / "outside-plan.jsonl"
outside.write_bytes(listed.read_bytes())
listed.unlink()
listed.symlink_to(outside)
load_codex_golden_v1(alias_root)
```

实际结果：

```text
SYMLINK_ALIAS_ACCEPTED = true
```

hash、size、canonical JSONL、adapter membership 和 pinned package hash 都保持相同，因此
当前 loader 接受这个非自包含、可随包外目标变化的 path alias。该行为违反 canonical
package 的 path-alias fail-closed 与 portable/self-contained 要求。

## 3. 独立正向验证

### 3.1 Released shape 与真实 case 结构

- dataset/version/schema 精确为
  `codex-golden-v1 / v1 / codex-evaluation-foundation-v1`；
- package pin 为
  `sha256:d303061e489c4639bb476bdb6fd24e02c6297eaf7dec6b3e6d5762a69a7c0fbf`；
- case IDs 精确、按顺序固定为 `codex-v1-001` 至 `codex-v1-045`；
- 8 slices 精确为 `6/6/6/6/7/5/4/5`；
- 从 45 行 case 实体计算得到 40 个唯一 positive、45 个 hard-negative entries、
  31 个 event truths；这些数字不是采用 summary 自报；
- 5 个 unanswerable/privacy cases 全部没有 positive thread/item/event truth，并带 refusal
  和 missing/privacy reason。

### 3.2 Manifest、canonical 与 clean rebuild

- manifest 声明 11 个 payload files：1 golden、8 session、1 session index、1 program；
  加 manifest 自身后 package 实际共有 12 个文件；
- 每个 payload file 的 SHA-256、size、kind 以及适用的 case/thread/item membership
  与实际文件匹配；
- tamper、missing、unlisted extra、WAL、SHM、pyc、noncanonical JSONL、duplicate manifest
  path、`..` alias、absolute path、credential、email、payment identifier 均独立复现为
  `REJECT`；
- 把 package 完整复制到干净临时目录并执行复制后的 `build_fixture.py`，重建前后
  12/12 文件逐字节一致；
- 唯一未通过的 canonical probe 是 P1-03 的 symlink alias。

### 3.3 Production CodexSessionAdapter 消费

在临时 Codex home 替换 `${PROJECT_ROOT}` 后，使用生产
`CodexSessionAdapter` 解析：

```text
threads = 8
turns = 8
items = 54
titles = 8
excluded reasoning = 2
```

Item type 实际分布：

```text
AgentMessage 10
CommandExecution 12
DevelopmentEpisode 8
FileChange 2
Patch 2
Plan 3
ToolResult 5
UserGoal 8
ValidationResult 4
```

- 8 个 thread IDs、54 个 item IDs、所有 locator/type/status 与 manifest/expected labels
  逐项对齐；
- archived thread 为 `failed`；current 与 archived goal 文本相同；
- current 同一 pytest command 的 old/new ValidationResult 分别为 failed/passed；
- subagent identity 的 `source.subagent=true`；
- 2 个 reasoning events 被排除，未进入 54 items；
- truncated ToolResult 被生产 adapter 标为 `truncated=true`，released truth 带 warning；
- 10 个 validation event-truth entries 均指向生产 adapter 派生的 ValidationResult，
  observed exit/status 与 truth 相符；
- 31 个 event truths 的 item type 没有 assistant/plan 冒充 tool call/result、patch、
  file change、command 或 validation 的类型错配。

### 3.4 Foundation-only summary 与状态

`probe_codex_evaluation_foundation()` 实际返回：

```text
case_count = 45
fixture_file_count = 11
fixture_thread_count = 8
fixture_item_count = 54
immutable_package_verified = true
formal_database_accessed = false
evaluation_run_created = false
baseline_metrics_run = false
baseline_qualified = false
release_posture = FOUNDATION_ONLY
```

计划/面试状态仍保持：

- Codex X0 `AUTHORIZED / IN_PROGRESS`；
- X-B0 blocked、无 artifact、无指标；
- X1–X5 未授权；
- Code C0–C7 engineering complete 与 `HOLD_DEFAULT_V1` 同时保留。

由于本 Gate 为 FAIL，package 不能被当作已批准 Golden version，计划/面试也不得写
Baseline Run 或任何优化数字。

## 4. 测试与静态检查

隔离测试命令：

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q \
  tests/test_codex_evaluation_v1.py \
  tests/test_codex_sessions.py \
  tests/test_codex_patch_details.py \
  tests/test_codex_bridge.py
```

结果：

```text
29 passed
```

唯一 warning 来自 FastAPI TestClient 的 Starlette/httpx deprecation，与本 Gate 无关。

四文件检查范围：

```text
src/evidence_rag/rag/sources/codex/evaluation_v1.py
src/evidence_rag/rag/sources/codex/__init__.py
tests/test_codex_evaluation_v1.py
tests/fixtures/codex_golden_v1/build_fixture.py
```

结果：

```text
ruff check            PASS
ruff format --check   PASS
git diff --check      PASS
isolated py_compile   PASS
```

## 5. 边界与停止条件

- 本评审没有修改实现、测试、config、deps、其他 docs、evaluation/evals、fixtures 或 Git；
  唯一新增文件是本报告。
- 所有独立 probes 只使用内存对象、仓库内 immutable synthetic fixture 和系统临时目录。
- 没有打开、哈希、checkpoint、删除或写入正式数据库，也没有接触其 WAL/SHM/journal。
- 没有 kill/open 外部服务，没有创建 branch/worktree，没有 commit/push。
- 当前停止在 Foundation Gate；P1 findings 清零并重新独立 Gate 前，不授权 X-B0 harness
  preparation、X-B0 Run 或 X1。

## 6. 第二轮极简复审

复审日期：2026-07-29

本节保留第 1–5 节首轮 FAIL 历史，只记录首轮 3 个 P1 的关闭重放和主路回归结果。

### 6.1 第二轮 Gate 结论

`CODEX X0-01 FOUNDATION PASS`

- `P0 findings: 0`
- `P1 findings: 0`
- `X-B0 HARNESS PREPARATION AUTHORIZED`
- 本次只批准 Golden/Evaluation Foundation：
  `codex-golden-v1 / v1 / codex-evaluation-foundation-v1`。
- 新 released package pin 为
  `sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1`。
- 除上述 Golden Version/authority 批准外，尚无 Codex baseline。
- X-B0 尚未执行；没有 X-B0 Run、metrics、artifact、Evaluation Run 或 qualification。
- 本授权只允许准备 X-B0 harness，不授权执行 X-B0 released baseline Run。
- X1 仍为 `NOT_STARTED / NOT AUTHORIZED`；X2–X5 同样未授权。
- Code C-B0 仍不属于 Codex X-B0。
- Release posture 仍为 `HOLD_DEFAULT_V1 / NOT RELEASED / default V1`。

### 6.2 首轮 P1-01 关闭：released authority 取代 row 自报 truth

第二轮独立加载得到：

```text
case/item judgments = 100
cases with eligible_metrics = 45 / 45
authority hash = sha256:b5fc258c94375480b8d76cdc2e5dda6fd86ca49ddc2a0048b5879da2b23d1fe5
```

`ReviewedRetrievalRow` 现在只接受 dataset/version/package/case/rank/thread/item/locator 和
`reviewed=true`。首轮可伪造的 7 个字段逐个重放：

```text
episode_id         REJECT
call_id            REJECT
patch_applied      REJECT
validation_status  REJECT
false_validated    REJECT
outcome_correct    REJECT
context_noise      REJECT
```

全部由 extra-forbid 收口为 `invalid reviewed retrieval row`。Evaluator 只用 row 的
released case/item identity 找到冻结的 `ItemJudgment`，Episode、call/result、patch、
validation、false-validation、outcome 和 context noise 不再消费调用方自报 truth。

Membership 重放结果：

```text
case_membership=None        ACCEPT（唯一全量默认）
case_membership=[]          REJECT
删减一个 case               REJECT
重复一个 case               REJECT
交换 case 顺序              REJECT
替换为未知 case             REJECT
```

Per-case `eligible_metrics` 的删除、重复、乱序和未知 metric 替换也全部 `REJECT`。
Eligibility 既与 case shape 确定性复算，又被 released authority hash 固定，调用方不能
删除或改写分母。

五类 False Validated authority 均来自真实 case/item judgment，并且相应 case 明确包含
`false_validated_rate_at_10` eligibility：

```text
codex-v1-041  claim_only       archived AgentMessage claim        false_validated=true
codex-v1-042  mentioned_only   plan-only AgentMessage             false_validated=false
codex-v1-043  invoked_no_exit  truncated CommandExecution        false_validated=true
codex-v1-044  failed_exit      archived failed ValidationResult  false_validated=false
codex-v1-045  target_unknown   truncated outcome                 false_validated=false
```

冻结 denominator 为 5。把五个 authority items 全部作为 reviewed rows 召回时，独立计算为
`numerator=2 / denominator=5`；row 无法把它改写为自报的 0/1 结论。

结论：首轮 P1-01 `CLOSED`。

### 6.3 首轮 P1-02 关闭：十条 negative routes 与五类 False Validated 分母冻结

第二轮从 45 个真实 case memberships 计算，而非采用 summary 自报：

```text
failed_attempt                  5
plan_only                       5
assistant_claim_without_exit    5
patch_failure                   5
read_only_not_change            5
same_command_multiple_statuses  4
truncated_tool_result            4
archived_duplicate               4
subagent_same_topic              4
privacy_sensitive                4
合计                            45
```

- 每个 case 恰有 1 个 hard negative；
- 每条路线覆盖 4–5 个 cases；
- 单个 negative item 的最大复用次数为 5；
- positive、event truth 和 hard negative 不重叠；
- 每个 hard negative 都有同 case 的 frozen `hard_negative/context_noise=true`
  ItemJudgment。

对生产 `CodexSessionAdapter` 的真实实体反查确认：

- `failed_attempt` 指向 observed exit 1 的 failed ValidationResult；
- `plan_only` 指向 Plan；
- `assistant_claim_without_exit` 指向 archived AgentMessage，所在 thread 只有失败验证；
- `patch_failure` 指向 exit 1 的 ToolResult；
- `read_only_not_change` 指向 exit 0 的只读 CommandExecution，不是 FileChange；
- `same_command_multiple_statuses` 同时覆盖 current old/new command 与 failed/passed
  ValidationResult；
- `truncated_tool_result` 指向 `metadata.truncated=true` 的 ToolResult；
- `archived_duplicate` 指向 archived goal；其中 `codex-v1-018/026/038` 与 positive
  membership 同为 `src/parser.py`，覆盖同路径不同 thread；
- `subagent_same_topic` 指向 `source.subagent=true` thread 的 AgentMessage；
- `privacy_sensitive` 指向 privacy thread 的受限 outcome。

五类 False Validated denominator 如 6.2 所列，类别集合和 case/item identity 均由
released authority 与 manifest 双重固定。

结论：首轮 P1-02 `CLOSED`。

### 6.4 首轮 P1-03 关闭：listed file 与路径组件 symlink fail-closed

Loader 现在对 package root、listed file 和每一级路径组件执行 `lstat`，要求：

- root 是真实目录；
- 中间组件是非 symlink 的真实目录；
- listed object 是非 symlink 的 regular file；
- strict resolve 后仍位于 resolved package root；
- inventory 中任何 symlink 或其他 non-regular object 都拒绝。

首轮 same-content alias 最小复现重放：

```text
listed cases.jsonl -> 包外同字节文件 symlink  REJECT
listed cases.jsonl -> 包内同字节文件 symlink  REJECT
listed sessions/ 目录组件 symlink              REJECT
```

三种情况均在读取 package 内容前以 `package symlink alias is forbidden` fail-closed。

结论：首轮 P1-03 `CLOSED`。

### 6.5 主路不回归

独立正向与负向 probes：

```text
dataset/version/schema       codex-golden-v1 / v1 / codex-evaluation-foundation-v1
package pin                  sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1
case IDs                     codex-v1-001..045，顺序固定
slice counts                 6/6/6/6/7/5/4/5
production adapter threads  8
production adapter items    54
case/item judgments          100
hard-negative routes        10
false-validation categories 5
clean rebuild               12/12 files byte-identical
```

以下攻击继续 `REJECT`：

```text
hash/size tamper
credential-shaped content
absolute path
cross-thread row identity
unknown/unjudged case-item identity
duplicate rank / rank gap
unknown package hash / locator mismatch
```

Reasoning exclusion、truncated warning、archived/current identity、same-command failed/passed
状态和 subagent identity 均未回归。Production adapter 仍消费 8 threads/54 items。

Evaluator 回归：

- 零 reviewed rows 的 15 个 released metrics 均不产生满分；
- released denominators 存在时零 rows 为诚实的 `AVAILABLE / 0`；
- `MetricResult` 对 `AVAILABLE / PROVISIONAL / UNAVAILABLE` 的 numerator、denominator、
  value、unit 和 reason 三态约束保持有效；
- unknown/cross-dataset/hash/membership/case/item/locator/rank 继续 fail-closed。

Foundation-only probe：

```text
item_judgment_count = 100
hard_negative_route_count = 10
false_validation_category_count = 5
formal_database_accessed = false
evaluation_run_created = false
baseline_metrics_run = false
baseline_qualified = false
release_posture = FOUNDATION_ONLY
```

### 6.6 第二轮测试与静态检查

隔离相关测试：

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q \
  tests/test_codex_evaluation_v1.py \
  tests/test_codex_sessions.py \
  tests/test_codex_patch_details.py \
  tests/test_codex_bridge.py
```

结果：

```text
36 passed
```

唯一 warning 仍为 FastAPI TestClient 的 Starlette/httpx deprecation，与本 Gate 无关。

四文件检查结果：

```text
ruff check            PASS
ruff format --check   PASS
git diff --check      PASS
isolated py_compile   PASS
```

### 6.7 第二轮边界

- 本轮只向本报告追加第 6 节，首轮 FAIL/P1 历史完整保留。
- 没有修改实现、测试、config、依赖、其他 docs、evaluation/evals、fixtures 或 Git。
- 所有 probes 只使用内存、immutable synthetic fixture 和隔离临时目录。
- 没有打开、哈希、checkpoint、删除或写入正式数据库，也没有触碰其 WAL/SHM/journal。
- 没有创建 branch/worktree，没有 commit/push。
- Gate 在 `X-B0 HARNESS PREPARATION AUTHORIZED` 停止；X-B0 Run 和 X1 均未授权。

## 7. CX0-03 / X-B0 Harness PREPARED 独立 Gate（第三轮）

日期：2026-07-29

被审实现任务：`019faa29-b37b-7360-8dcc-a57607ae9a17`

评审范围：X-B0 runner/harness 工程准备；不执行 released X-B0 baseline

### 7.1 Gate 结论

`X-B0 HARNESS PREPARATION FAIL`

- `P0 findings: 0`
- `P1 findings: 2`
- `X-B0 ISOLATED PRODUCTION RUN NOT AUTHORIZED`
- 尚无 X-B0 Run、baseline metrics、持久 Run artifact 或 qualification。
- 本轮仅运行非资格 smoke 和隔离 verifier 对抗输入；没有执行 released 45-case
  production baseline。
- X1 仍为 `NOT AUTHORIZED`；X2–X5 同样未授权。

第 6 节的 `CODEX X0-01 FOUNDATION PASS` 继续成立，Golden-v1 authority 仍为：

```text
dataset/version  codex-golden-v1 / v1
case membership  codex-v1-001..codex-v1-045（固定顺序）
package hash     sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1
```

第 6 节的 `X-B0 HARNESS PREPARATION AUTHORIZED` 只授权了本轮 harness 开发准备，
不构成 released run 授权。本轮 runner 主链、隔离路径、portable artifact 安全扫描和
smoke posture 均通过，但 production identity pin 与 standalone verifier 仍有两个可重复的
P1 fail-open，因此不得进入 X-B0 isolated production run。

### 7.2 P1-01 — Production identity 在导入时自捕获，可接受预注入的替代实现

`baseline_v1.py:130-140` 把当前已导入对象直接保存为 `_EXPECTED_*`；
`component_identities_v1()` 随后对这些对象现场计算 digest
（`baseline_v1.py:556-619`）。`_assert_exact_production_components()` 比较的是当前
module export、当前 local import 和同一次导入捕获的 `_EXPECTED_*`
（`baseline_v1.py:637-696`），没有与 Gate 审核过的固定 module、qualname 和
implementation digest allowlist 比较。

因此，导入 `baseline_v1` 之前替换 production module export 时，替代 class 和替代
`search` method 会同时被捕获为“expected”；之后的 `is`、`unwrap` 和 digest 比较都只是
替代对象与自身比较。

最小复现使用全新 Python 进程和内存 `linecache` source；没有文件或数据库输入：

```python
import evidence_rag.codex_retrieval as production

class InjectedRetriever(production.CodexHybridRetriever):
    def search(self, request):
        return {
            "results": [],
            "trace": {
                "fusion": "codex-weighted-hybrid-v2",
                "embedding_model": "local-hash-v2",
            },
        }

production.CodexHybridRetriever = InjectedRetriever
import evidence_rag.rag.sources.codex.baseline_v1 as baseline

prepared = baseline.probe_codex_baseline_preparation_v1()
```

实际结果：

```text
prepared.status               PREPARED
retriever class module        gate_injected_override
retriever-search module       gate_injected_override
search_is_injected            true
injected_override_accepted    true
```

普通的导入后 local monkeypatch、wrapper 和 subclass 会被现有 guard 拒绝，但预导入
module/export replacement 是 released runner 的真实调用路径替换，仍能被标记
`PREPARED`。这违反“exact production component identity 不能只由现场字符串/自摘要
决定”的 Gate 条件。

最小关闭条件：

- 固定并校验审核过的 production module、qualname、version 和 implementation digest；
- class 与关键 method digest 都必须对固定 authority 值比较，不能在同一次不受信导入中
  同时生成 expected 与 actual；
- preparation、runner 和 artifact component identity 使用同一固定 authority，并对
  canonical public export 路径 fail-closed。

### 7.3 P1-02 — Released verifier 未绑定 Golden authority，也未从 predictions 重算质量

runner 的 released execution path 会调用 `load_codex_golden_v1()`，因此真实 runner
入口对 Gate 通过的 package 做了绑定；问题位于可移植 artifact 的 standalone verifier。

`golden_cases.jsonl` 只携带 case id、slice、query、eligible metrics 和调用方写入的
denominator contributions（`baseline_v1.py:1529-1558`），没有 frozen expected
threads/items、item judgments、hard negatives 或 event truth。Verifier 对 released
artifact 只要求：

- dataset id/version 字符串匹配；
- membership 有 45 个非重复字符串；
- package hash 符合 `sha256:<hex>` 语法；
- denominator、metric 和 slice payload 在 artifact 内部彼此自洽。

它没有把 package hash 与
`sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1`
比较，也没有把 membership 与 `codex-v1-001..045` 固定顺序比较
（`baseline_v1.py:2156-2226`）。对 released metrics，verifier 只验证 MetricResult
schema、artifact denominator 和 slice/overall numerator 相等
（`baseline_v1.py:2339-2469`）；它不从 predictions 加载 released judgments、构造
reviewed rows 并调用 `evaluate_reviewed_codex_retrieval()` 重算 numerator/value。

三个隔离、无数据库的 verifier 最小复现均使用真实 Golden case shape，但没有运行检索：

```text
Probe A: unknown package hash
actual Gate hash   sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1
artifact hash      sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
verifier result    VERIFIED_NON_QUALIFIED

Probe B: rogue membership
artifact cases     rogue-001..rogue-045
verifier result    VERIFIED_NON_QUALIFIED

Probe C: forged quality
predictions        45/45 zero_result；无 results
metric numerator sum before  0
metric numerator sum after   317
mutation           同步改 metrics/slices 并重算 canonical checksums
verifier result    VERIFIED_NON_QUALIFIED
```

三次 probe 的 `database_created=false`；产物只存在于自动清理的临时目录。Probe C 中
security scan、文件 checksum、每 case denominator、slice denominator 和
overall/slice arithmetic 都保持内部自洽，只有质量分子与真实 predictions 相矛盾；
verifier 仍接受。

这意味着一个重新寻址且内部自洽的非 released package、伪 membership 或伪质量 artifact
可以获得 released verifier 状态。虽然返回值仍为 `baseline_qualified=false`，该
`VERIFIED_NON_QUALIFIED` artifact 正是后续 qualification 的输入，因此是 X-B0 主流程
P1。

最小关闭条件：

- released verifier 精确固定 Golden dataset/version/package hash 和 45-case ordered
  membership；
- per-case eligibility 和 denominator 从同一 frozen Golden authority 重建，不信任
  artifact 自报 contribution；
- artifact 必须携带或显式绑定足以重建 reviewed rows 的 released judgments；
- verifier 从 predictions 和 released authority 重新调用真实 evaluator，并重算 overall
  metrics、slice metrics、hard-negative/false-validation 分子与三态，再与 payload
  逐项比较。

### 7.4 已通过的 harness 主路与非回归

独立 programmatic smoke 在全新系统临时根运行，使用 `sys.setprofile` 按精确 code object
记录生产调用链：

```text
CodexSessionAdapter.parse                  1
CodexIngestionService.run                 1
CodexStoreMixin.publish_codex_generation  1
LocalHashEmbedding.embed                  7
CodexHybridRetriever.search               2
network calls                             0
```

smoke 的固定配置与计划一致：

```text
view granularity       item-per-view
embedding              local-hash-v2 / 384
lexical/dense          FTS5 / dense full candidate
fusion                 codex-weighted-hybrid-v2
candidate multiplier   8
episode/graph/reranker off / off / off
context                snippet-v1
top-k/thread top-k     10 / 5
```

正向结果：

- 真实 adapter → ingestion/publication → exact retriever 主链产生 1 个有结果 case 和
  1 个 `zero_result` case；
- ranked item 使用 Codex item identity、相对 locator、真实 lexical/dense channels、
  连续 rank 和生产 latency/candidate trace；
- artifact 精确包含 10 个 canonical files；
- copy 后 verify 成功；
- verify-only 期间把 `sqlite3.connect` 和 `CodexHybridRetriever.search` 替换为
  assertion bomb，仍返回 `VERIFIED_SMOKE_NON_QUALIFIED`，证明没有打开数据库或执行检索；
- smoke metrics 为 `SMOKE_UNAVAILABLE`、空 metrics list，runner/result/artifact/verifier
  的 `baseline_qualified` 均为 `false`；
- smoke 后临时 SQLite、raw materialization、WAL/SHM 均不存在。

现有专项测试与源码检查还确认以下 fail-closed 主路未回归：

```text
missing/default request fields              REJECT
released authorization mismatch             REJECT
outside-root database                       REJECT before sqlite open
database/raw/output reuse                   REJECT
pre-existing WAL/SHM sidecar                 REJECT
isolated-root/fixture/golden symlink alias   REJECT
formal database path                         REJECT before sqlite open
artifact tamper/delete/extra                 REJECT
external or internal symlink artifact alias  REJECT by lstat/regular-file rule
secret/email/payment/absolute path           REJECT
temp DB/WAL/SHM/pyc/nonfinite content        REJECT
cross-file run/component/config/seed         REJECT
denominator/membership inconsistency         REJECT
```

Artifact security findings由 verifier 重新扫描 canonical files，而不是只信任
`security_report.json`；latency、error、zero-result 和 unavailable cross-file 状态也会
重新核对。P1-02 不否定这些安全/结构检查，阻断点只在 released authority 和质量真值。

### 7.5 独立测试与静态检查

相关测试以 `PYTHONDONTWRITEBYTECODE=1`、隔离 `RAG_DATA_DIR` 和自动清理的
`TemporaryDirectory` 运行：

```text
tests/test_codex_baseline_v1.py
tests/test_codex_evaluation_v1.py
tests/test_codex_sessions.py
tests/test_codex_patch_details.py
tests/test_codex_bridge.py
tests/test_source_history_acl.py

65 passed
```

唯一 warning 为 FastAPI TestClient 的 Starlette/httpx deprecation，与本 Gate 无关。

三文件检查：

```text
ruff check                 PASS
ruff format --check        PASS
git diff --no-index --check PASS
in-memory compile          PASS
```

检查对象：

```text
src/evidence_rag/rag/sources/codex/baseline_v1.py
src/evidence_rag/rag/sources/codex/__init__.py
tests/test_codex_baseline_v1.py
```

### 7.6 本轮边界

- 本轮只向本报告追加第 7 节，首轮 FAIL、第二轮 Foundation PASS 历史均保留。
- 没有修改实现、测试、config、依赖、其他 docs、evaluation/evals、fixtures 或 Git。
- 仓库中不存在 `evals/codex`；没有新增 Codex Run、metrics 或 artifact。
- 对抗性 released artifact 只是无检索、无数据库的临时 verifier 输入，退出时自动删除，
  不构成 X-B0 Run 或 baseline artifact。
- 没有打开、哈希、checkpoint、删除或写入正式数据库，也没有触碰其
  WAL/SHM/journal。
- 没有创建 branch/worktree，没有 commit/push。
- 本 Gate 在 `X-B0 ISOLATED PRODUCTION RUN NOT AUTHORIZED` 停止；X1 仍为
  `NOT AUTHORIZED`。

## 8. CX0-03 / X-B0 Harness Gate 第四轮极简复审

日期：2026-07-29

修复实现任务：`019faa29-b37b-7360-8dcc-a57607ae9a17`

复审范围：只重放第 7 节 P1-01、P1-02，并确认必要主路不回归；不执行 released X-B0
production baseline

### 8.1 Gate 结论

`X-B0 HARNESS PREPARATION PASS`

- `P0 findings: 0`
- `P1 findings: 0`
- `X-B0 ISOLATED PRODUCTION RUN AUTHORIZED`
- 第 7 节两个 P1 均已 `CLOSED`。
- 尚无 X-B0 Run、baseline metrics、持久 Run artifact 或 qualification。
- X1 仍为 `NOT_AUTHORIZED`；X2–X5 同样未授权。

本授权只允许下一任务按现有合同执行一次明确、全新临时根下的 isolated X-B0 production
run；不表示本轮已经执行该 Run，不批准 qualification，也不扩大正式库例外或 X1
授权。

### 8.2 第三轮 P1-01 关闭：production identity 改为固定 authority

当前 authority 不是从本次 runtime import 对象生成 expected 值。实现内固定 13 条
Gate-reviewed authority rows，每条同时固定：

```text
role / kind
module / public export / qualname
version
source_sha256
code_sha256
```

覆盖：

```text
adapter class / parse
ingestion class / run
publication class / method
embedding class / embed
retriever class / search
store class / connect
evaluator function
```

固定 aggregate：

```text
sha256:a3c75915caa4b228eca6b7d187b1fe5d1c3cf0135884d8130f709ad832c16c75
```

`_fixed_production_authority()` 先验证固定 rows 自身的 aggregate；
`_assert_exact_production_components()` 再解析 canonical public export，逐项比较
module、qualname、object kind、source digest 和 runtime code digest，并核对 baseline
binding 与固定 version。Module import、preparation probe、runner 和 verifier 都经过该
authority。

独立 fresh-subprocess 原复现：

```text
正常 exact runtime
component count       13
aggregate             sha256:a3c75915caa4b228eca6b7d187b1fe5d1c3cf0135884d8130f709ad832c16c75
runtime == fixed      true
probe status          PREPARED

import baseline 前：
替换 production retriever class
替换 search implementation
伪造 class/method module + qualname
结果                  REJECT during baseline import
```

导入后的独立矩阵：

```text
retriever.search wrapper  probe     REJECT
retriever.search wrapper  runner    REJECT before SQLite/output
retriever.search wrapper  verifier  REJECT
public export subclass              REJECT
future embedding version            REJECT
```

正常 authority、版本和双 digest 全部通过。Expected identity 现在不从当前 import
对象学习；未来 production source/code/version/export 变化会在重新 Gate 固定 authority
前 fail-closed。

结论：第三轮 P1-01 `CLOSED`。

### 8.3 第三轮 P1-02 关闭：released authority 与质量由 verifier 离线重建

Verifier 当前固定并逐项核对：

```text
Golden package
sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1

ordered membership
codex-v1-001..codex-v1-045

per-case authority
sha256:b5fc258c94375480b8d76cdc2e5dda6fd86ca49ddc2a0048b5879da2b23d1fe5

full dataset authority
sha256:3e6645ef64daff7d82096e376b84755138480a5640cc751fbc7148ada67b046e
```

Released `golden_cases.jsonl` 现在携带每 case 完整 frozen authority 及其
`authority_sha256`。Verifier：

1. 精确核对 dataset/version/schema/package、45-case ordered membership 和三个 authority
   digests；
2. 从每 case authority 重建 `CodexGoldenCase`，复算 query、slice、eligibility、
   denominator 和 case digest；
3. 用 fixture thread/item/locator memberships 重建完整 `GoldenDataset`，复算 full dataset
   digest；
4. 从 predictions 的 frozen judgment prefix 构造严格 `ReviewedRetrievalRow`，核对
   thread/item/locator 与 reviewed count；
5. 调用真实 `evaluate_reviewed_codex_retrieval()` 重算 overall metrics；
6. 重算 per-case contributions、slice metrics、status、numerator、denominator 和 value，
   然后与 artifact 逐项精确比较。

第四轮用正常 released-shaped、45 个 zero-result predictions 的临时输入先验证正路：

```text
normal verifier result                    VERIFIED_NON_QUALIFIED
_reviewed_rows_from_predictions calls     1
evaluate_reviewed_codex_retrieval calls   1
CodexHybridRetriever.search calls         0
_execute_cases calls                      0
SQLiteStore.connect calls                 0
database created                          false
```

随后对每个攻击副本同步重写 canonical files 和 checksums；unknown package 与 rogue
membership 还同步重算 run id 和全部跨文件 identity：

```text
unknown bbbb... package hash              REJECT
rogue-001..rogue-045 ordered membership   REJECT
per-case authority query tamper           REJECT
treatment judgment tamper                 REJECT
eligible_metrics tamper                   REJECT
full dataset authority tamper             REJECT
overall + slice numerator 0 -> 317         REJECT
```

其中 0→317 攻击保持 overall/slice 算术和 checksums 内部自洽，仍以
`released metrics differ from exact X0 evaluator recomputation` 拒绝。专项参数化测试还分别
重放 denominator、status、value、harmful old-attempt、false-validation 和 context-noise
伪造，均 `REJECT`。因此 overall、slice、三态和 treatment metrics 已由 frozen authority
与真实 evaluator 决定，不再由 artifact 自报质量决定。

结论：第三轮 P1-02 `CLOSED`。

### 8.4 主路不回归

正常小型 programmatic smoke 仍走精确生产主链；按 code object 记录：

```text
CodexSessionAdapter.parse                  1
CodexIngestionService.run                 1
CodexStoreMixin.publish_codex_generation  1
LocalHashEmbedding.embed                  7
CodexHybridRetriever.search               2
network calls                             0
```

主路结果：

- 固定 `item-per-view / local-hash-v2 / FTS5 + dense full candidate /
  codex-weighted-hybrid-v2 / graph+episode+reranker off / snippet-v1` 配置未回归；
- artifact 仍精确 10 files，canonical、安全扫描、copy/verify 均通过；
- smoke 含正常 retrieved 与 `zero_result` 两种 case；
- metrics 继续为 `SMOKE_UNAVAILABLE` 和空 metrics list；
- runner、artifact 和 verifier 的 `baseline_qualified` 均为 `false`；
- 临时 SQLite、raw、WAL/SHM 均完成清理；
- verifier-only 对 normal smoke 和 released-shaped 输入均未执行 runner、检索或
  `SQLiteStore.connect`。

仓库仍不存在 `evals/codex`，没有 released 45-case Run、metrics 或持久 artifact。

### 8.5 独立测试与静态检查

专项 collection：

```text
tests/test_codex_baseline_v1.py: 42
```

以 `PYTHONDONTWRITEBYTECODE=1`、隔离 `RAG_DATA_DIR` 和自动清理临时根复跑六文件：

```text
tests/test_codex_baseline_v1.py
tests/test_codex_evaluation_v1.py
tests/test_codex_sessions.py
tests/test_codex_patch_details.py
tests/test_codex_bridge.py
tests/test_source_history_acl.py

82 passed
```

唯一 warning 为 FastAPI TestClient 的 Starlette/httpx deprecation，与本 Gate 无关。

三文件静态检查：

```text
ruff check                  PASS
ruff format --check         PASS
git diff --no-index --check PASS
in-memory compile           PASS
```

### 8.6 第四轮边界

- 本轮只向本报告追加第 8 节；前三轮历史完整保留。
- 没有修改实现、测试、config、依赖、其他 docs、evaluation/evals、fixtures 或 Git。
- 所有运行与对抗输入均位于自动清理的系统临时目录；没有保留 probe artifact。
- 没有打开、哈希、checkpoint、删除或写入正式数据库，也没有触碰其
  WAL/SHM/journal。
- 没有创建 branch/worktree，没有 commit/push。
- 当前状态为 `X-B0 ISOLATED PRODUCTION RUN AUTHORIZED`；X-B0 尚未执行且
  X1 仍为 `NOT_AUTHORIZED`。

## 9. X-B0 Harness Gate 第五轮 Scanner Fix 极简复审

日期：2026-07-29

修复任务：`019faa29-b37b-7360-8dcc-a57607ae9a17`

复审范围：只审首次 authorized attempt 暴露的 security scanner finite-number
false-positive 修复与必要主路回归；不执行 released45 retry

### 9.1 Gate 结论

`X-B0 SCANNER FIX PASS`

- `P0 findings: 0`
- `P1 findings: 0`
- `X-B0 ISOLATED PRODUCTION RETRY AUTHORIZED`
- 首次 authorized attempt 没有发布 artifact、metrics 或 Run。
- retry 尚未执行；仓库 `evals/codex/runs` 仍为 0。
- X1 仍为 `NOT_AUTHORIZED`；X2–X5 同样未授权。

首次 attempt 已完成真实 released45 检索，但旧 scanner 对 canonical
`metrics.json`/`slice_report.json` 中合法有限浮点数的 raw JSON 字符命中 payment regex，
在 artifact publication 前 fail。Staging 已清理，因此该 attempt 不构成 X-B0 Run 或
baseline artifact。本轮只验证 scanner 修复并授权下一次 isolated production retry。

### 9.2 修复结构

Scanner 当前顺序为：

1. `_read_json()` / `_read_jsonl()` 严格解析 JSON；
2. 拒绝 nonfinite、blank JSONL row 和非 canonical byte representation；
3. 对解析后的 object tree 递归；
4. 只扫描 string keys 和 string values；
5. number、bool 和 null 不进入文本 regex；
6. 再由完整 artifact verifier 执行 exact-key、schema、identity、checksum 和跨文件合同。

因此 `0.058823529411764705`、`1/17`、large/small finite number 等数值不会因为其 JSON
字符中存在 13–19 位连续数字而被误判为 payment data；相同字符若是 query、locator、
error、nested string 或 string key，仍进入全部文本规则。

Payment 豁免只适用于严格字段与格式：

```text
declared sha256 fields + exact sha256:<64 lowercase hex>
run_id                + exact codex-xb0-<32 lowercase hex>
declared *_id         + exact RFC-shaped lowercase UUID
```

该豁免只跳过 payment regex，不跳过 credential、email、absolute-path 或
temporary/generated rules；完整 verifier 仍通过 exact keys 和 Pydantic contracts 验证字段
是否被允许。

### 9.3 合法 finite numeric 重放

独立 canonical JSON 输入覆盖 `metrics.json`、`slice_report.json` 和
`security_report.json`：

```text
1/17                     0.058823529411764705
5/17                     0.29411764705882354
1/13                     0.07692307692307693
long decimal             0.12345678901234567
small finite             1.2345678901234567e-18
large finite             9.876543210987654e18
large integer            4111111111111111
negative finite          -1234567890123.5
nested list/dict          numbers + bool + null
```

三个文件的 raw text 都能被旧 `_PAYMENT_RE` 命中；结构化 scanner 结果均为空。
`_read_json()` 返回值与写入前 object 逐项相等，数值没有被删除、替换、字符串化或舍弃。

实际 artifact 主路：

- 正常 smoke artifact security scan `PASS`；
- 正常临时 released-shaped 45-case artifact security scan `PASS`；
- 两者仍为精确 10 个 canonical files。

结论：首次 attempt 的 finite-number false-positive 已关闭。

### 9.4 字符串安全规则与 canonical reader 不回归

把同一 payment-shaped 数字 `4111111111111111` 放入以下位置：

```text
query          REJECT
locator        REJECT
error          REJECT
nested string  REJECT
string key     REJECT
```

其他独立负向重放：

```text
email                         REJECT
credential                    REJECT
absolute path                 REJECT
formal-DB-shaped path string  REJECT
SQLite WAL                    REJECT
SQLite SHM                    REJECT
__pycache__ / pyc             REJECT
NaN / Infinity / -Infinity    REJECT
overflowing 1e9999            REJECT
undeclared artifact field     REJECT
```

身份边界：

```text
严格 declared sha256/run_id/UUID 字段  PASS scanner；随后由 contract 验证
相同 sha256-like 字符放入 query       REJECT
相同 run-id-like 字符放入 locator     REJECT
UUID-like payment 放入普通 note       REJECT
未知 mystery_hash 字段                REJECT payment / 后续 contract
格式错误的 declared sha256 字段       REJECT
```

Raw-text 绕过重放：

```text
非 canonical whitespace JSON      REJECT
Unicode-escaped payment digits     REJECT noncanonical
duplicate JSON key                 REJECT noncanonical
long decimal 伪装为 JSON string    REJECT payment
blank/noncanonical JSONL           REJECT
```

Canonical/finite reader 先建立唯一 object representation，再递归扫描字符串，因此 raw JSON
escape、重复键、格式变化或 number-as-string 不能绕过。

### 9.5 Harness 主路不回归

正常 programmatic smoke 继续走精确生产链：

```text
CodexSessionAdapter.parse                  1
CodexIngestionService.run                 1
CodexStoreMixin.publish_codex_generation  1
LocalHashEmbedding.embed                  7
CodexHybridRetriever.search               2
network calls                             0
```

Smoke 回归：

- retrieved 与 `zero_result` case 均存在；
- artifact 精确 10 files，scanner、copy 和 verify 全部通过；
- metrics 仍为 `SMOKE_UNAVAILABLE`，无 qualification 数值；
- runner/artifact/verifier 的 `baseline_qualified=false`；
- 临时 SQLite、raw、WAL/SHM 完成清理；
- undeclared canonical field 即使重算 checksums 仍被 verifier 拒绝。

临时 released-shaped verifier 回归：

```text
case count                             45
Golden package                         sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1
fixed component authority              sha256:a3c75915caa4b228eca6b7d187b1fe5d1c3cf0135884d8130f709ad832c16c75
verification                           VERIFIED_NON_QUALIFIED
_reviewed_rows_from_predictions calls  1
evaluate_reviewed_codex_retrieval calls 1
CodexHybridRetriever.search calls      0
SQLiteStore.connect calls              0
runner calls                           0
database created                       false
```

Fixed production authority aggregate 未变化，Golden offline reconstruction 和真实 X0
evaluator 均未回归；verify-only 仍不执行检索或打开数据库。

### 9.6 独立测试与静态检查

Scanner 专项 collection：

```text
tests/test_codex_baseline_v1.py -k structured_security_scan: 15
```

以 `PYTHONDONTWRITEBYTECODE=1`、隔离 `RAG_DATA_DIR` 和自动清理临时根复跑六文件：

```text
tests/test_codex_baseline_v1.py
tests/test_codex_evaluation_v1.py
tests/test_codex_sessions.py
tests/test_codex_patch_details.py
tests/test_codex_bridge.py
tests/test_source_history_acl.py

97 passed
```

唯一 warning 为 FastAPI TestClient 的 Starlette/httpx deprecation，与本 Gate 无关。

三文件静态检查：

```text
ruff check                  PASS
ruff format --check         PASS
git diff --no-index --check PASS
in-memory compile           PASS
```

### 9.7 第五轮边界

- 本轮只向本报告追加第 9 节；前四轮历史完整保留。
- 没有修改实现、测试、config、依赖、其他 docs、evaluation/evals、fixtures 或 Git。
- 没有执行 released45 retry；所有 smoke、scanner 和 released-shaped verifier 输入均位于
  自动清理的系统临时目录。
- 首次 attempt 没有 artifact；本轮也没有创建持久 Run、metrics 或 artifact。
- `evals/codex/runs` 为 0。
- 没有打开、哈希、checkpoint、删除或写入正式数据库，也没有触碰其
  WAL/SHM/journal。
- 没有创建 branch/worktree，没有 commit/push。
- 当前状态为 `X-B0 ISOLATED PRODUCTION RETRY AUTHORIZED`；retry 尚未执行且 X1 仍为
  `NOT_AUTHORIZED`。

## 10. X-B0 Production Run Audit Gate

日期：2026-07-29

评审范围：只读审计唯一 X-B0 production Run 的 artifact、security 和事实真值；不修改
Run，不执行新的 production retrieval

### 10.1 Gate 结论

`X-B0 PRODUCTION BASELINE AUDIT FAIL`

- `P0 findings: 0`
- `P1 findings: 2`
- `X1 EVENT NORMALIZATION NOT_AUTHORIZED`
- 唯一 Run 仍保持原始 `COMPLETED / NON_QUALIFIED / baseline_qualified=false` 状态；
  本 Gate 不接受其 metrics/slices 为完整 top-10 baseline truth。
- 本结论不是因为 retrieval quality 高低，而是因为 artifact 对已有 immutable predictions
  的 released truth 计算不完整，并缺少计划要求的 correct-zero/refusal 质量合同。
- 首次 scanner 失败 attempt 没有 artifact，不属于 Run；本轮没有创建第二个 Run。

被审 Run：

```text
directory    evals/codex/runs/6971b8cb1702a9ece8a894915c6b6de7
run_id       codex-xb0-6971b8cb1702a9ece8a894915c6b6de7
URI          evaluation-run://project-codex-xb0-v1/6971b8cb1702a9ece8a894915c6b6de7
set hash     sha256:85a52db4afbe4f06630600982212767d9365cbff466c7c26acf15967a988d2f6
```

### 10.2 P1-01 — reviewed-prefix 截断使完整 top-10 Golden truth 被漏计

Runner 和 verifier 都把“reviewed”错误定义为从 rank 1 开始、直到第一个无该 case
judgment 的连续前缀：

- `_execute_cases()` 在首个非 authority item 上 `break`
  （`baseline_v1.py:1293-1298`），并把此前缀长度写为 `reviewed_result_count`；
- `_reviewed_rows_from_predictions()` 离线重建时执行同一 `break`
  （`baseline_v1.py:2329-2333`），随后只与 artifact 自报的 prefix count 比较
  （`baseline_v1.py:2353-2354`）；
- verifier 再用这个截断后的 rows 调真实 evaluator 并比对 metrics/slices
  （`baseline_v1.py:2844-2880`）。

因此 verifier 的确在“重算”，但重算的是 artifact 自报前缀，而不是完整 top-10。当前
evaluator 又要求 reviewed rank 必须从 1 连续（`evaluation_v1.py:1273-1279`），这正好
掩盖了 rank 之后仍有 released judgment 的事实。

本 Run 有 45 个 predictions、450 个 top-10 results，但 artifact 的
`reviewed_result_count` 合计仅为 14。直接遍历全部 top-10，并仅选取确有 frozen
case/item judgment 的结果、保留其原始 rank，得到 41 个 judged retrievals，分布在
31 个 cases。后排 judgment 不能被第一个 unjudged result 删除。

#### Hard-negative 直接重放

完整 top-10 命中 8/45 个 released hard negatives：

| Case | Rank | Route | old_attempt | artifact reviewed prefix |
| --- | ---: | --- | --- | ---: |
| `codex-v1-004` | 4 | `truncated_tool_result` | false | 1 |
| `codex-v1-011` | 5 | `failed_attempt` | true | 0 |
| `codex-v1-018` | 10 | `archived_duplicate` | true | 0 |
| `codex-v1-023` | 5 | `assistant_claim_without_exit` | true | 1 |
| `codex-v1-026` | 9 | `archived_duplicate` | true | 0 |
| `codex-v1-038` | 6 | `archived_duplicate` | true | 1 |
| `codex-v1-039` | 8 | `subagent_same_topic` | false | 0 |
| `codex-v1-043` | 9 | `truncated_tool_result` | false | 0 |

真实命中路线计数：

```text
truncated_tool_result            2
failed_attempt                   1
archived_duplicate               3
assistant_claim_without_exit     1
subagent_same_topic              1
```

其中 5 个是 `old_attempt=true`，8 个均有
`hard_negative / context_noise=true` frozen judgment；`codex-v1-043` 还带
`invoked_no_exit / false_validated=true`。Artifact 却报告：

```text
harmful_old_attempt_rate_at_10 = 0 / 26
false_validated_rate_at_10     = 0 / 5
context_noise_rate_at_10       = 0 / 45
```

完整 frozen judgments 的直接结果应至少为：

```text
harmful_old_attempt_rate_at_10 = 5 / 26 = 0.19230769230769232
false_validated_rate_at_10     = 1 / 5  = 0.2
context_noise_rate_at_10       = 8 / 45 = 0.17777777777777778
```

受影响的 slice truth：

```text
thread/goal location          harmful 0/4; false-validated 0/0; noise 1/6
process trace                 harmful 1/2; false-validated 0/0; noise 1/6
rationale/decision            harmful 1/6; false-validated 0/0; noise 1/6
patch/file change             harmful 1/3; false-validated 0/0; noise 1/6
command/validation            harmful 1/4; false-validated 0/0; noise 1/7
failure/retry                 harmful 0/3; false-validated 0/0; noise 0/5
multi-thread comparison       harmful 1/1; false-validated 0/0; noise 2/4
unresolved/unanswerable/privacy harmful 0/3; false-validated 1/5; noise 1/5
```

Artifact 的八个 slices 对 harmful/context noise 均报告 0；privacy slice 还把
false-validation 报为 0/5。这不是“质量差但诚实”，而是 immutable predictions 中已经
存在的 harmful evidence 没有进入分子。

#### 其余指标也受同一截断影响

Artifact 中的 `Thread 13/40=.325`、`Episode 12/40=.3`、`Item 12/40=.3`、
`MRR 12/40=.3` 等字段与当前 verifier 的 prefix 重算完全一致；但这只能证明内部自洽，
不能证明完整 top-10 truth。按“只计 frozen judgments、保留 production 原始 rank”的
保守重算如下：

| Metric | Artifact prefix | 完整 frozen judgments |
| --- | ---: | ---: |
| Thread Recall@5 | 13/40 = 0.325 | 23/40 = 0.575 |
| Episode Recall@5 | 12/40 = 0.3 | 21/40 = 0.525 |
| Item Recall@10 | 12/40 = 0.3 | 26/40 = 0.65 |
| MRR@10 | 12/40 = 0.3 | 16.2972222222/40 = 0.4074305556 |
| Goal Recall@10 | 5/7 | 7/7 |
| Decision Recall@10 | 1/7 | 5/7 |
| Harmful Old Attempt@10 | 0/26 | 5/26 |
| Event Order Accuracy@10 | 1/3 | 1/3 |
| Call/Result Link Accuracy@10 | 1/2 | 1/2 |
| Patch Accuracy@10 | 1/4 | 3/4 |
| Validation Accuracy@10 | 2/10 | 7/10 |
| False Validated Rate@10 | 0/5 | 1/5 |
| Outcome Accuracy@10 | 1/3 | 1/3 |
| Context Duplicate Rate@10 | 0/45 | 0/45 |
| Context Noise Rate@10 | 0/45 | 8/45 |

这些重算值只用于证明 artifact truth 不一致，不构成新的 qualified baseline metrics 或
替代 artifact。质量数值升降本身不影响 Gate；同一 immutable top-10 得出不同结果才是
P1。

结论：`P1-01 OPEN`。

### 10.3 P1-02 — 5 个 correct-zero/refusal failures 没有进入质量合同

Golden 的 `codex-v1-041..045` 均为：

```text
unanswerable=true
refusal_expected=true
expected_threads/items/event_truth = empty
```

Run 对五个 cases 均返回 10 个结果：

```text
retrieved = 5 / 5
zero_result = 0 / 5
error = 0 / 5
unavailable = 0 / 5
```

因此按开发计划“unanswerable/privacy case 的正确零结果单独计分”
（`02_CODEX_SOURCE_DEVELOPMENT_PLAN.md:332`），本 Run 的 correct-zero/refusal 是
`0/5 correct、5/5 failures`。其中 `codex-v1-043` 还在 rank 9 命中
`truncated_tool_result` hard negative。

Artifact 只保存全局描述字段 `zero_result_count=0`，没有 correct-zero/refusal metric、
eligible denominator、failure numerator 或 slice status。现有 `CodexMetric`
（`evaluation_v1.py:203-218`）也没有该合同。Privacy slice 把一般 recall 指标记为
0-denominator `UNAVAILABLE`，同时又因 P1-01 把已发生的 false-validation/noise 报成 0，
不能替代计划要求的 correct-zero 计分。

五次非零 retrieval 是实际低质量；不把这 5/5 failures 明确记录，则是必需质量合同缺失，
属于 artifact truth P1。

结论：`P1-02 OPEN`。

### 10.4 Artifact identity、portable security 与 execution facts

除上述事实真值问题外，Run 的身份、可移植性和安全边界通过：

```text
dataset/version/schema
codex-golden-v1 / v1 / codex-evaluation-foundation-v1

Golden package
sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1

case authority
sha256:b5fc258c94375480b8d76cdc2e5dda6fd86ca49ddc2a0048b5879da2b23d1fe5

full dataset authority
sha256:3e6645ef64daff7d82096e376b84755138480a5640cc751fbc7148ada67b046e

component authority
sha256:a3c75915caa4b228eca6b7d187b1fe5d1c3cf0135884d8130f709ad832c16c75

seed / ordered membership
701 / codex-v1-001..045
```

- 13 个 fixed components 和
  `item-per-view / local-hash-v2 / FTS5+dense-full /
  codex-weighted-hybrid-v2 / graph+episode+reranker off / snippet-v1`
  配置精确匹配；
- Run 目录精确 10 个 non-symlink regular files，无 extra；全部 JSON/JSONL canonical、
  finite，45 Golden rows、45 prediction rows、0 error rows；
- 9 个 payload files 的 SHA-256/size 与 `checksums.json` 逐项一致，复算 artifact set hash
  与声明值一致；
- 独立扫描未发现 credential、email/payment、absolute/temp/formal DB path、WAL/SHM、
  pyc、raw fixture 或 nonfinite；artifact 没有 ACL principal/token；
- 450/450 results 的 item、thread、locator 均属于冻结的 8 threads/54 items/54 locators；
  locator 与 item identity 一致，没有跨 fixture/ACL scope 的结果；
- 45/45 outcome 为 `retrieved`，共 450 results；zero/error/unavailable/fallback 均为 0；
- channels 为 447 个 dense+lexical、3 个 lexical-only；每 case dense candidates=46；
- latency 全部有限且非负，`p50=3.37 ms / p95=3.95 ms / max=4.72 ms`，与 45 条 case
  trace 重算一致；
- `evals/codex/runs` 只有该目录。

原件与系统临时目录副本分别 verify：

```text
status                 VERIFIED_NON_QUALIFIED
case_count             45
canonical_file_count   10
artifact_set_hash      sha256:85a52db4afbe4f06630600982212767d9365cbff466c7c26acf15967a988d2f6
portable               true
retrieval_executed     false
baseline_qualified     false

CodexHybridRetriever.search       0
SQLiteStore.connect               0
run_codex_baseline_v1             0
_execute_cases                    0
evaluate_reviewed_codex_retrieval 1
_reviewed_rows_from_predictions   1
```

原件与复制件结果逐项相同。该正向结果证明 portable/checksum/verify-only 主路成立；它不能
消除 10.2 中 verifier 自身采用错误 prefix truth 的 P1。

### 10.5 最小修复与再审路径

不需要重新执行 production retrieval。唯一 Run 已经 checksummed 保存完整 45×top-10
predictions、原始 rank、thread/item/locator/channels/latency，以及完整 Golden authority，
足以离线修复审计。

最小路径：

1. 版本化修复 evaluator/verifier：遍历每个 prediction 的完整 top-10，选择所有 frozen
   case/item judgments，保留原始 rank/gap；不要在首个 unjudged item 处 `break`，不要信任
   artifact 自报 `reviewed_result_count`；
2. 对完整 top-10 重新计算 overall、八 slices、hard-negative、false-validation 和
   context metrics，并让 verifier 独立派生 reviewed count；
3. 增加 unanswerable/privacy correct-zero/refusal 的 5-case eligibility、numerator、
   denominator、status 和 slice 报告；
4. 从 immutable original set hash 派生一个新的、版本化 correction/audit artifact；不得
   覆盖或改写本 Run；
5. 对 correction artifact 重新独立 Audit Gate。通过前 X1 保持
   `EVENT NORMALIZATION NOT_AUTHORIZED`。

只有 immutable predictions 缺失或无法验证时才需要新 execution；本 Run 不属于该情况。

### 10.6 回归与边界

以 `PYTHONDONTWRITEBYTECODE=1` 和全新临时 `RAG_DATA_DIR` 独立复跑：

```text
tests/test_codex_baseline_v1.py
tests/test_codex_evaluation_v1.py
tests/test_codex_sessions.py
tests/test_codex_patch_details.py
tests/test_codex_bridge.py
tests/test_source_history_acl.py

97 passed
```

唯一 warning 为 FastAPI TestClient 的 Starlette/httpx deprecation，与本 Gate 无关。

静态检查：

```text
ruff check              PASS
ruff format --check     PASS
git diff --check        PASS
in-memory compile       PASS
```

- 本轮唯一写入是向本报告追加第 10 节；第 1–9 节历史完整保留。
- 没有修改实现、测试、config、依赖、其他 docs、evaluation/evals、Run、fixtures 或 Git。
- 没有执行新的 released retrieval，没有创建或修改 Run/metrics/artifact。
- 所有 copy/probe/test 输出只位于自动清理的系统临时目录。
- 没有打开、哈希、checkpoint、删除或写入正式数据库，也没有触碰其
  WAL/SHM/journal；正式库仍为 `EXTERNAL_MUTABLE_SERVICE_OWNED`。
- 没有创建 branch/worktree，没有 commit/push。
- 本 Gate 只审 P0/P1；未扩展 P2/P3。

## 11. X-B0 Offline Correction Harness 独立 Gate

日期：2026-07-29

评审范围：只审 X-B0 offline correction harness；只读 immutable source Run，只在系统临时
目录生成和复制 correction；不创建持久 correction，不重新执行 retrieval

### 11.1 Gate 结论

`X-B0 OFFLINE CORRECTION HARNESS PASS`

- `P0 findings: 0`
- `P1 findings: 0`
- `X-B0 PERSISTENT CORRECTION ARTIFACT AUTHORIZED`
- 当前尚无持久 correction artifact；`evals/codex/corrections` 不存在。
- 本轮没有重新检索，没有创建新 Run；唯一 source Run 保持 byte/mtime immutable。
- 本授权只允许从固定 source Run 生成持久 correction artifact，不授权修改或覆盖原 Run。
- X1 仍为 `NOT_AUTHORIZED`；必须在持久 correction artifact 独立 Gate 通过后另行决定。
- 原第 10 节 `X-B0 PRODUCTION BASELINE AUDIT FAIL` 历史不被改写；本轮 PASS 的对象是
  correction harness，而不是原错误 metrics/slices。

### 11.2 Source Run authority 与 immutable 边界

Harness 固定绑定：

```text
source URI
evaluation-run://project-codex-xb0-v1/6971b8cb1702a9ece8a894915c6b6de7

source run id
codex-xb0-6971b8cb1702a9ece8a894915c6b6de7

source artifact set
sha256:85a52db4afbe4f06630600982212767d9365cbff466c7c26acf15967a988d2f6

Golden package
sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1

case authority
sha256:b5fc258c94375480b8d76cdc2e5dda6fd86ca49ddc2a0048b5879da2b23d1fe5

full dataset authority
sha256:3e6645ef64daff7d82096e376b84755138480a5640cc751fbc7148ada67b046e

component authority
sha256:a3c75915caa4b228eca6b7d187b1fe5d1c3cf0135884d8130f709ad832c16c75

ordered membership
codex-v1-001..codex-v1-045
```

源 10 文件的实际 SHA-256/size 与 harness 固定 allowlist 逐项一致：

| Source file | SHA-256 | Size |
| --- | --- | ---: |
| `checksums.json` | `sha256:c75bde8affd713cf9ab683c05f470e66916d393e07568961fcac0e4ad1cabd9a` | 1,259 |
| `errors.jsonl` | `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | 0 |
| `golden_cases.jsonl` | `sha256:0ffd297b7f4c403d607756f817307f81746ad8454cce71d5005d7d1f18fdb57c` | 194,426 |
| `latency.json` | `sha256:bf50de2b852c0a688c707148fec9ac1e9f12d750ffa95da119d08b5ae41bac9a` | 8,010 |
| `manifest.json` | `sha256:ddbbd6b673530b23228411c0e4e2dc1211ce88a07f21fed8e0eb3e90da56cd58` | 18,639 |
| `metrics.json` | `sha256:4b10afec22bb92c98f8ce2c073c49baa691232519b06aafa271eff2db3a82a6c` | 2,909 |
| `predictions.jsonl` | `sha256:1a56ab42a8d646fee31fef9748fa5f805b479a3b3c91992bd489dd88eba3b16d` | 218,173 |
| `run.json` | `sha256:5f3c6863c74d2c8a5d2c4865a7c2acdce1460748393d566b010d02a7dc9ccf47` | 2,506 |
| `security_report.json` | `sha256:67c2ea186c51517a6482f8650c1f9179370432ed8febafcad716b192727b32b6` | 529 |
| `slice_report.json` | `sha256:05aac74a5c05e883931478dcd54d55c50041e8e65d8ec4bb73d37170d7c6e1cf` | 17,628 |

Builder 先执行 source 10-file canonical/checksum/Golden/component/ordered-membership 验证，再
逐项比较上述固定 file records（`baseline_v1.py:4004-4017`）。它不从现场 source
自摘要来更新 expected authority。

独立在 build 前、build 后和 copy verifier 后分别记录源目录的 file type、bytes、size、
SHA-256 和 `mtime_ns`：

```text
10/10 bytes unchanged       true
10/10 SHA-256 unchanged     true
10/10 mtime_ns unchanged    true
10/10 regular non-symlink   true
```

对隔离 source copy 的 `metrics.json` 追加一个空格后，builder 以
`canonical artifact checksum or size mismatch` 拒绝，且没有创建 output。

### 11.3 Complete truth 独立重算

Correction evaluator authority：

```text
version  codex-x-b0-complete-truth-v1
digest   sha256:f48cb5ee7a11f4455a3b0952ba269dd3e5afd367c32ecb5915b4d7a13344dbe4
policy   all-frozen-judgments-at-original-top10-ranks
```

`_derive_complete_correction_evidence()` 对每个 prediction 的完整 top-10 遍历：

- unjudged item 使用 `continue`，不再使用旧 prefix `break`；
- 每个 judged result 必须与 frozen case/item/thread/locator authority 对齐；
- 保留 production 原始 rank 和 gap；
- 不读取 `prediction.trace.reviewed_result_count` 作为 truth。

临时 correction 从 byte-exact `golden_cases.jsonl` 和 `predictions.jsonl` 得到：

```text
released/evaluated cases   45 / 45
frozen judged retrievals   41
cases with reviewed gaps   22
codex-v1-004 ranks         [1, 4]，source reviewed_count=1
codex-v1-043 ranks         [4, 9]，source reviewed_count=0
```

18 个 overall metrics 独立重放结果：

| Metric | Numerator | Denominator | Value |
| --- | ---: | ---: | ---: |
| Thread Recall@5 | 23 | 40 | 0.575 |
| Episode Recall@5 | 21 | 40 | 0.525 |
| Item Recall@10 | 26 | 40 | 0.65 |
| MRR@10 | 16.29722222222222 | 40 | 0.4074305555555555 |
| Goal Recall@10 | 7 | 7 | 1.0 |
| Decision Recall@10 | 5 | 7 | 0.7142857142857143 |
| Harmful Old Attempt@10 | 5 | 26 | 0.19230769230769232 |
| Event Order Accuracy@10 | 1 | 3 | 0.3333333333333333 |
| Call/Result Link Accuracy@10 | 1 | 2 | 0.5 |
| Patch Accuracy@10 | 3 | 4 | 0.75 |
| Validation Accuracy@10 | 7 | 10 | 0.7 |
| False Validated Rate@10 | 1 | 5 | 0.2 |
| Outcome Accuracy@10 | 1 | 3 | 0.3333333333333333 |
| Context Duplicate Rate@10 | 0 | 45 | 0.0 |
| Context Noise Rate@10 | 8 | 45 | 0.17777777777777778 |
| Hard-negative Hit Rate@10 | 8 | 45 | 0.17777777777777778 |
| Correct-zero Rate | 0 | 5 | 0.0 |
| Refusal-failure Rate | 5 | 5 | 1.0 |

这些值由 per-case frozen denominators/numerators 聚合，不在实现中硬编码最终输出。八个
slice 的 available/unavailable、numerator 和 denominator 逐项汇总后与 overall
ledger 完整对账。

### 11.4 Hard-negative 与 refusal truth

从 source predictions 和 Golden memberships 直接计算的 8 个命中为：

| Case | Rank | Route | old_attempt |
| --- | ---: | --- | --- |
| `codex-v1-004` | 4 | `truncated_tool_result` | false |
| `codex-v1-011` | 5 | `failed_attempt` | true |
| `codex-v1-018` | 10 | `archived_duplicate` | true |
| `codex-v1-023` | 5 | `assistant_claim_without_exit` | true |
| `codex-v1-026` | 9 | `archived_duplicate` | true |
| `codex-v1-038` | 6 | `archived_duplicate` | true |
| `codex-v1-039` | 8 | `subagent_same_topic` | false |
| `codex-v1-043` | 9 | `truncated_tool_result` | false |

十条 route 的 denominator/hits 均从 45 个 source cases 重算：

```text
archived_duplicate               4 / 3
assistant_claim_without_exit     5 / 1
failed_attempt                   5 / 1
patch_failure                    5 / 0
plan_only                        5 / 0
privacy_sensitive                4 / 0
read_only_not_change             5 / 0
same_command_multiple_statuses   4 / 0
subagent_same_topic              4 / 1
truncated_tool_result            4 / 2
合计                            45 / 8
```

Refusal ledger 固定为 `codex-v1-041..045`：

```text
eligible cases       5
returned top-10      5 / 5
correct zero         0 / 5
refusal failures     5 / 5
```

`codex-v1-043` 继续保存
`truncated_tool_result / hard_negative_hit=true / rank=9`；没有被 refusal 聚合吞掉。

### 11.5 Temporary correction artifact 与 copy-only verifier

独立临时 build 产生：

```text
correction id
codex-xb0-correction-1ccecfa1c9667d0f716c709d1175d570

ephemeral artifact set
sha256:d06c2a2231c432dc1afde5646acbf83b385cf8841957ad825021850881e57c26

status
CORRECTION_PREPARED / NON_QUALIFIED
```

Artifact 精确 10 个 top-level regular files：

```text
correction.json
manifest.json
golden_cases.jsonl
predictions.jsonl
corrected_metrics.json
corrected_slice_report.json
hard_negative_report.json
refusal_report.json
security_report.json
checksums.json
```

- `golden_cases.jsonl` 和 `predictions.jsonl` 与 source 逐字节相同；
- manifest 固定 source 10-file hashes、run/URI/set hash、Golden/component authority、
  ordered45、evaluator version/digest；
- correction/manifest/verifier 均固定
  `source_run_immutable=true / retrieval_executed=false /
  persistent_artifact=false / baseline_qualified=false`；
- 9 个 payload file hashes/size 与 correction checksums 逐项一致；
- security 对 absolute/temp/formal DB、credential/email/payment、WAL/SHM、pyc、
  nonfinite、extra 和 source-byte-exact 均为 clean。

把 artifact 完整复制到另一临时目录后，只传入 copy directory 运行 verifier：

```text
status                  VERIFIED_CORRECTION_NON_QUALIFIED
canonical_file_count    10
case_count              45
portable                true
retrieval_executed      false
source_run_immutable    true
baseline_qualified      false
source Run file reads   0
```

Copy artifact 10/10 bytes 与原临时 artifact 一致；audit hook 证明 copy verifier 没有回读
仓库 source Run。

Builder 与 verifier 都用 code/c-call bomb 包围，下列调用计数全部为 0：

```text
run_codex_baseline_v1
_execute_cases
CodexSessionAdapter.parse
CodexIngestionService.run
CodexStoreMixin.publish_codex_generation
LocalHashEmbedding.embed
CodexHybridRetriever.search
SQLiteStore.connect
sqlite3.connect
```

临时根退出后自动清理；仓库没有新增 correction 或 Run。

### 11.6 重签 checksum 的 fail-closed probes

每个攻击均在独立临时副本修改 payload，并在适用时重新计算 correction file hashes 和
artifact set hash；不是只依赖旧 checksum 拒绝。

以下 28 类全部 `REJECT`：

```text
source artifact-set hash
source run identity
source per-file hash
evaluator digest
cross-file correction identity

prediction rank
prediction item
prediction locator
source reviewed_result_count

Golden item judgment
Golden eligible_metrics

corrected overall metric
corrected slice metric
hard-negative report
refusal report
security self-report

credential
payment identifier
email
absolute path
temporary/formal DB path
WAL
SHM
pyc
nonfinite

deleted canonical file
undeclared extra file
tampered source Run copy
```

具体 fail-closed 层次：

- source/Golden/prediction 攻击由固定 source authority 或 byte-exact copy 检查拒绝；
- metrics/slices/hard-negative/refusal 即使重签 checksums，仍由完整 truth 重算拒绝；
- security self-report 与 verifier 期望不一致时拒绝；
- unsafe strings/nonfinite 由结构化 canonical/security scanner 拒绝；
- delete/extra 由精确 10-file membership 拒绝；
- tampered source copy 在 publication 前失败，output 不存在。

### 11.7 回归、静态检查与停止边界

使用 `PYTHONDONTWRITEBYTECODE=1` 和全新临时 `RAG_DATA_DIR` 独立复跑固定 Codex 集合：

```text
tests/test_codex_baseline_v1.py       79
tests/test_codex_evaluation_v1.py     24
tests/test_codex_sessions.py           8
tests/test_codex_patch_details.py      2
tests/test_codex_bridge.py             2
tests/test_source_history_acl.py       4
合计                                  119 passed
```

唯一 warning 为 FastAPI TestClient 的 Starlette/httpx deprecation，与本 Gate 无关。

三文件静态检查：

```text
ruff check            PASS
ruff format --check   PASS
git diff --check      PASS
in-memory compile     PASS
```

- 本轮唯一写入是向本报告追加第 11 节；第 1–10 节历史完整保留。
- 没有修改实现、测试、config、Golden/evaluation、生产组件、其他 docs/evaluation/evals、
  fixtures、原 Run、正式库或 Git。
- 没有创建 branch/worktree，没有 commit/push。
- 所有 correction、copy、tamper 和测试输出只位于自动清理的系统临时目录。
- 没有打开、哈希、checkpoint、删除或写入正式数据库，也没有触碰其
  WAL/SHM/journal；正式库仍为 `EXTERNAL_MUTABLE_SERVICE_OWNED`。
- 本 Gate 只审 P0/P1；未扩展 P2/P3。
- 当前停止在 `X-B0 PERSISTENT CORRECTION ARTIFACT AUTHORIZED`；持久 artifact 尚未
  生成，X1 仍为 `NOT_AUTHORIZED`。

## 12. X-B0 Persistent Offline Correction Artifact 最终只读 Audit

日期：2026-07-29

评审范围：只读审计唯一持久 X-B0 correction artifact；不修改 source Run、correction、
实现、测试、配置、Golden/evaluation 或正式库，不执行 retrieval

### 12.1 最终 Gate 结论

`X-B0 PERSISTENT CORRECTION AUDIT PASS`

- `P0 findings: 0`
- `P1 findings: 0`
- `X-B0 FIXED BASELINE CONTROL ACCEPTED — NON_QUALIFIED`
- `X1 EVENT NORMALIZATION AUTHORIZED`
- 接受的固定 control identity 精确包括 correction URI 与 artifact set hash，不接受其他
  readdressed set。
- 原 source Run 继续保留第 10 节 `X-B0 PRODUCTION BASELINE AUDIT FAIL` 的历史结论；
  其原始错误 metrics/slices 不被追认。
- 本 correction 是 source Run 的 authoritative offline audit overlay，不覆盖、不修改
  source Run。
- 没有重新执行 retrieval，没有创建第二个 Run 或第二个 correction。
- X-B0 继续为 `NON_QUALIFIED / baseline_qualified=false`。质量较低是已诚实记录的 fixed
  control 事实，不再是 X1 工程启动的 P0/P1 blocker。
- 本授权只启动 X1 event normalization engineering，不授权改变默认 V1、发布 treatment
  或把 X-B0 写成 quality-qualified baseline。

固定 correction：

```text
directory
evals/codex/corrections/1ccecfa1c9667d0f716c709d1175d570

correction id
codex-xb0-correction-1ccecfa1c9667d0f716c709d1175d570

URI
evaluation-correction://project-codex-xb0-v1/1ccecfa1c9667d0f716c709d1175d570

artifact set
sha256:f5da3d0b0d9d29bed3daff0a230cd1aa131d19385c20e9784b9b7a44a089ca6d
```

### 12.2 Persistent artifact inventory、checksum 与 security

目录精确包含 10 个 top-level、non-symlink regular files，总大小 478,060 bytes：

| File | SHA-256 | Size |
| --- | --- | ---: |
| `correction.json` | `sha256:b6e59a1d5288db9c49cc949d750f3c7f19f91944a549b435293896bb5b0a428b` | 1,885 |
| `manifest.json` | `sha256:97b6e9055dfa0df486ce6648cf7fcdbdf2f0427f1b3f854028cdbff09fc4e9e4` | 15,446 |
| `golden_cases.jsonl` | `sha256:0ffd297b7f4c403d607756f817307f81746ad8454cce71d5005d7d1f18fdb57c` | 194,426 |
| `predictions.jsonl` | `sha256:1a56ab42a8d646fee31fef9748fa5f805b479a3b3c91992bd489dd88eba3b16d` | 218,173 |
| `corrected_metrics.json` | `sha256:83913f220c699defe69c48d57fb4f76f4be81fae68db205c5ddcb4d23aa0c46e` | 2,871 |
| `corrected_slice_report.json` | `sha256:cab9d8e558d9a428dde395a0935092487054660fa665a9e1d215a45258bb8171` | 26,812 |
| `hard_negative_report.json` | `sha256:21255488cfaaf7b68b3ed59b74fdc3dc052415e89a49c27d17e0830abc8d3984` | 15,035 |
| `refusal_report.json` | `sha256:0e230af5d2f4d8855f5ded4f353c9aef8a35659fb2ee843dd12fcf067daff971` | 1,452 |
| `security_report.json` | `sha256:9a572c6671ab09f9514b037aafa6fc9ce36d05cc55fb7447cfaed3ce4de5c118` | 627 |
| `checksums.json` | `sha256:632aa741e5adedfddc7c829b1d58f3d937bd5072c011c14a1c2e65bea6d35ec7` | 1,333 |

独立复算：

```text
declared payload hashes/sizes   9 / 9 exact
artifact set hash               sha256:f5da3d0b0d9d29bed3daff0a230cd1aa131d19385c20e9784b9b7a44a089ca6d
JSON/JSONL canonical            true
duplicate keys                  0
nonfinite values                0
Golden rows                     45
prediction rows                 45
undeclared files                0
```

结构化 verifier 与独立文本扫描均为 clean：

```text
credential / email / payment       0
absolute or temporary path         0
formal DB / SQLite artifact        0
WAL / SHM                          0
pyc / __pycache__                  0
nonfinite                          0
security findings                  0
```

`correction.json` 精确记录：

```text
status                 CORRECTION_PUBLISHED
release_posture        NON_QUALIFIED
source_run_immutable   true
retrieval_executed     false
persistent_artifact    true
baseline_qualified     false
```

### 12.3 Source Run 与 authority binding

Correction 固定绑定：

```text
source URI
evaluation-run://project-codex-xb0-v1/6971b8cb1702a9ece8a894915c6b6de7

source set
sha256:85a52db4afbe4f06630600982212767d9365cbff466c7c26acf15967a988d2f6

Golden package
sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1

case authority
sha256:b5fc258c94375480b8d76cdc2e5dda6fd86ca49ddc2a0048b5879da2b23d1fe5

full dataset authority
sha256:3e6645ef64daff7d82096e376b84755138480a5640cc751fbc7148ada67b046e

component authority
sha256:a3c75915caa4b228eca6b7d187b1fe5d1c3cf0135884d8130f709ad832c16c75

correction evaluator
codex-x-b0-complete-truth-v1
sha256:f48cb5ee7a11f4455a3b0952ba269dd3e5afd367c32ecb5915b4d7a13344dbe4
```

- Manifest 的 source 10-file hash/size records 与原 Run 逐项 10/10 一致；
- correction 内 `golden_cases.jsonl`、`predictions.jsonl` 与原 Run 逐字节相同；
- dataset/version/schema、8 threads/54 items/54 locators、完整 judgments、eligibility 和
  `codex-v1-001..045` ordered membership 均由 copied Golden authority 重建；
- correction 原件/复制件 verifier 期间没有回读 source Run；
- 审计前后分别记录 source Run 与 correction 的 bytes、SHA-256、size 和 `mtime_ns`，
  两个目录均 10/10 不变；
- `evals/codex/runs` 只有 source Run 一个目录；
- `evals/codex/corrections` 只有本 correction 一个目录；
- 没有遗留 staging、第二个 correction、第二个 Run 或 SQLite sidecar。

### 12.4 完整 truth 与 cross-file ledger

独立公式直接消费 copied Golden authorities 和 45×top-10 predictions，只选择有 frozen
case/item judgment 的结果并保留原始 rank/gap：

```text
reviewed evidence        41
rank-gap cases           22
prefix break             false
trusted reviewed_count   false
```

不读取 corrected payload 的自报数值，重算得到：

| Metric | Numerator | Denominator | Value |
| --- | ---: | ---: | ---: |
| Thread Recall@5 | 23 | 40 | 0.575 |
| Episode Recall@5 | 21 | 40 | 0.525 |
| Item Recall@10 | 26 | 40 | 0.65 |
| MRR@10 | 16.29722222222222 | 40 | 0.4074305555555555 |
| Goal Recall@10 | 7 | 7 | 1.0 |
| Decision Recall@10 | 5 | 7 | 0.7142857142857143 |
| Harmful Old Attempt@10 | 5 | 26 | 0.19230769230769232 |
| Event Order Accuracy@10 | 1 | 3 | 0.3333333333333333 |
| Call/Result Link Accuracy@10 | 1 | 2 | 0.5 |
| Patch Accuracy@10 | 3 | 4 | 0.75 |
| Validation Accuracy@10 | 7 | 10 | 0.7 |
| False Validated Rate@10 | 1 | 5 | 0.2 |
| Outcome Accuracy@10 | 1 | 3 | 0.3333333333333333 |
| Context Duplicate Rate@10 | 0 | 45 | 0.0 |
| Context Noise Rate@10 | 8 | 45 | 0.17777777777777778 |
| Hard-negative Hit Rate@10 | 8 | 45 | 0.17777777777777778 |
| Correct-zero Rate | 0 | 5 | 0.0 |
| Refusal-failure Rate | 5 | 5 | 1.0 |

18/18 overall rows 与 `corrected_metrics.json` 的 numerator/denominator/value/status 精确一致。
八个 slice 的 per-case frozen denominators/numerators 独立复算后也逐项一致；所有 available
slice 分子/分母汇总与 overall ledger 完整对账，0-denominator metrics 均诚实为
`UNAVAILABLE`。

这些数值由 immutable source predictions 与 Golden labels 计算，不在 verifier 中硬编码
最终 metric 输出。

### 12.5 Hard-negative 与 refusal

完整 top-10 命中精确为：

```text
codex-v1-004 @ 4   truncated_tool_result
codex-v1-011 @ 5   failed_attempt
codex-v1-018 @ 10  archived_duplicate
codex-v1-023 @ 5   assistant_claim_without_exit
codex-v1-026 @ 9   archived_duplicate
codex-v1-038 @ 6   archived_duplicate
codex-v1-039 @ 8   subagent_same_topic
codex-v1-043 @ 9   truncated_tool_result
```

Route denominator/hits：

```text
archived_duplicate               4 / 3
assistant_claim_without_exit     5 / 1
failed_attempt                   5 / 1
patch_failure                    5 / 0
plan_only                        5 / 0
privacy_sensitive                4 / 0
read_only_not_change             5 / 0
same_command_multiple_statuses   4 / 0
subagent_same_topic              4 / 1
truncated_tool_result            4 / 2
合计                            45 / 8
```

`codex-v1-041..045` 均 returned 10 results：

```text
correct zero       0 / 5
refusal failures   5 / 5
```

`codex-v1-043` 的 `truncated_tool_result` rank 9 同时保留在 refusal 和 hard-negative
ledger。低质量和 false validation 没有再被 prefix 或 unavailable 状态隐藏。

### 12.6 Portable verify-only 与 fail-closed

原件和 fresh system-temp copy 分别得到完全相同结果：

```text
status                  VERIFIED_CORRECTION_NON_QUALIFIED
correction id           codex-xb0-correction-1ccecfa1c9667d0f716c709d1175d570
artifact set            sha256:f5da3d0b0d9d29bed3daff0a230cd1aa131d19385c20e9784b9b7a44a089ca6d
case_count              45
canonical_file_count    10
source_run_immutable    true
retrieval_executed      false
persistent_artifact     true
portable                true
baseline_qualified      false
```

Copy 10/10 bytes 与 persistent original 相同。Audit hook 证明 copy verifier 的 source Run
file reads 为 0。

使用 code/c-call bomb 包围原件和 copy verifier，下列调用均为 0：

```text
run_codex_baseline_v1
_execute_cases
CodexSessionAdapter.parse
CodexIngestionService.run
CodexStoreMixin.publish_codex_generation
LocalHashEmbedding.embed
CodexHybridRetriever.search
SQLiteStore.connect
sqlite3.connect
```

在独立临时副本对 payload 修改并重签全部 correction checksums 后，以下 27 类均由
verifier `REJECT`：

```text
source set/run/per-file hash
correction URI
evaluator digest
prediction rank/item/locator/reviewed_count
Golden judgment/eligibility
overall metric / slice / hard-negative / refusal / security report
credential / payment / email
absolute/temp/formal DB path
WAL / SHM / pyc / nonfinite
delete / extra
```

把 `persistent_artifact=true / CORRECTION_PUBLISHED` 显式改成 harness 支持的
`false / CORRECTION_PREPARED` 会被 verifier 识别为另一种 ephemeral posture，并产生不同
artifact set；它不会冒充 persistent，且不匹配本 Gate 固定的
`sha256:f5da...ca6d` control identity，因此不能替换本次接受的 artifact。

### 12.7 回归、静态检查与边界

以 `PYTHONDONTWRITEBYTECODE=1` 和全新临时 `RAG_DATA_DIR` 独立复跑：

```text
tests/test_codex_baseline_v1.py       79
tests/test_codex_evaluation_v1.py     24
tests/test_codex_sessions.py           8
tests/test_codex_patch_details.py      2
tests/test_codex_bridge.py             2
tests/test_source_history_acl.py       4
合计                                  119 passed
```

唯一 warning 为 FastAPI TestClient 的 Starlette/httpx deprecation，与本 Gate 无关。

三文件静态检查：

```text
ruff check            PASS
ruff format --check   PASS
git diff --check      PASS
in-memory compile     PASS
```

- 本轮唯一写入是向本报告追加第 12 节；第 1–11 节历史完整保留。
- 没有修改实现、测试、config、Golden/evaluation、生产组件、其他 docs/evaluation/evals、
  fixtures、原 Run、correction artifact、正式库或 Git。
- 没有创建 branch/worktree，没有 commit/push。
- 所有 portable copy 和 tamper probes 只位于自动清理的系统临时目录。
- 没有打开、哈希、checkpoint、删除或写入正式数据库，也没有触碰其
  WAL/SHM/journal；正式库仍为 `EXTERNAL_MUTABLE_SERVICE_OWNED`。
- 本 Gate 只审 P0/P1；未扩展 P2/P3。
- 当前停止在 `X1 EVENT NORMALIZATION AUTHORIZED`；X-B0 仍为
  `FIXED CONTROL / NON_QUALIFIED`。
