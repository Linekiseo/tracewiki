# Codex X1 / CX1-01 Event Normalization 独立 Engineering Gate Review

日期：2026-07-29  
Gate 来源任务：`019f9f27-8d88-7b01-b686-3d4acbc5dcf2`  
CX1-01 实施任务：`019fab06-1ff3-7440-9434-4c122d6ff86c`  
评审范围：CX1-01 Observable Event Normalizer + State Machine  
工作区：共享 dirty `main`，无 branch/worktree、无 commit/push  
正式库边界：`EXTERNAL_MUTABLE_SERVICE_OWNED`

## 1. Gate 结论

`CODEX X1 CX1-01 ENGINEERING FAIL`

- `P0 findings: 0`
- `P1 findings: 4`
- `CX1-02 NOT_AUTHORIZED`
- `X-T1 NOT_AUTHORIZED`
- 当前尚无 X-T1 Treatment Run 或指标。
- X2 仍为 `NOT_AUTHORIZED`。

X-B0 fixed baseline control 的最终 correction Gate 已接受
`FIXED BASELINE CONTROL ACCEPTED — NON_QUALIFIED`，并已授权 X1；该低质量基线不是本
Gate 的阻塞理由。目标六文件的专项测试、既有 Codex/adapter 回归、ruff、format、
whitespace/diff audit 和 in-memory compile 也全部通过。

但独立对抗 probes 证明，当前 normalizer 仍可：

1. 把未由 command argv 执行的自报 target 标成 `passed`；
2. 把 assistant-role claim 伪装的 `ToolResult` 当作 observed result 并标成 `passed`；
3. 把同一 `PatchResult` 的 `success=true + outcome=failed` 标成 `applied`；
4. 接受并向 derived evidence 回显嵌入式绝对路径 locator。

这些都是 CX1-01 state truth、False Validated 或安全主流程缺陷，不是模块体量、P2/P3、
既有 `rag_ui_v2` lint 或 X-B0 质量问题。因此本 Gate 不能授权 CX1-02 或 X-T1。

## 2. P1 findings

### P1-01 — Validation target 只做语法检查，未绑定 canonical argv，可产生 false `passed`

`_canonical_argv()` 与 `_canonical_targets()` 分别验证两个调用方提供的字段
（`event_normalizer.py:256-358`），但没有证明 `targets` 是从本次 canonical argv
实际执行的路径/selector 派生。Validation 分支只要求：

- argv 命中 allowlist；
- `targets` 非空且语法安全；
- 配对 result 中有精确整数 exit code；
- exit code 为 0。

随后直接把自报 targets 写入 `NormalizedValidationEvent` 并标为 `passed`
（`event_normalizer.py:924-997`）。`false_validated_count` 的内部断言也只检查 argv、
targets 和 result evidence 是否非空，不检查 target 是否真的在 argv 中
（`event_normalizer.py:1110-1130`）。

纯内存最小复现：

```python
command = ObservableCodexItem(
    item_type="CommandExecution",
    call_id="call-target",
    command_argv=("pytest", "tests/test_beta.py::test_beta"),
    targets=("tests/test_alpha.py::test_alpha",),
    target_project_id="project-a",
    # 同一合法 scope/locator，其余字段省略
)
result = ObservableCodexItem(
    item_type="CommandResult",
    call_id="call-target",
    call_type="command",
    payload={"execution": {"exit_code": 0}},
    # 同一合法 scope，observable_order 晚于 command
)
normalized = normalize_codex_events_v1([command, result])
```

实际结果：

```text
validation.state = passed
validation.targets = ["tests/test_alpha.py::test_alpha"]
diagnostics = []
summary.false_validated_count = 0
```

更弱的 `command_argv=("pytest", "-q")` 加同一个自报 target 也得到完全相同的 `passed`。
因此现有 400 组合 truth table 虽然是 `false positives=0`，但矩阵只改变
pairing/exit/target-shape/allowlist/shell，没有覆盖 argv-target mismatch 或 target
完全不在 argv 的分支。

影响：

- 可以把测试 B 的 exit 0 归因成测试 A 通过；
- 可以把没有显式执行目标的命令归因成某个已知 target 通过；
- False Validated Rate 的内部 `0` 不再可信，直接阻塞 X-T1。

最小修复范围：

- 在 `contracts.py` / `event_normalizer.py` 内把 command target 从 canonical argv
  确定性派生或验证；
- 对无法由 argv 精确证明的 target 保持 `target_unknown`，不得 `passed`；
- 增加 argv-target mismatch、target absent from argv、selector mismatch、runner option
  伪装等对抗测试；
- 不需要也不应扩展到 CX1-02 store/schema/runtime。

### P1-02 — Result item_type 未与 observable role/provenance 绑定，assistant claim 可升级为 passed

`ObservableCodexItem` 同时允许任意 `item_type` 和可选 `role`
（`contracts.py:433-457`），没有 validator 拒绝
`item_type=ToolResult, role=assistant` 这类矛盾证据。

Normalizer 只排除 reasoning/token/system/developer 类型和 system/developer roles
（`event_normalizer.py:85-95, 517-530`）；assistant role 不被排除。只要 item_type
写成 `ToolResult`，它就进入 result pairing（`event_normalizer.py:532-669`），可提供
exit code 并升级 action/validation。

纯内存最小复现：

```text
call:
  item_type = CommandExecution
  argv = ["pytest", "tests/test_alpha.py"]
  targets = ["tests/test_alpha.py"]
result:
  item_type = ToolResult
  role = assistant
  call_id/type/scope = exact match
  payload.exit_code = 0
```

实际结果：

```text
call/result links = 1
validation.state = passed
summary.false_validated_count = 0
```

这违反 Plan/AgentMessage/assistant claim 不得成为 observed completion/validation truth
的主合同。当前测试只验证 `item_type=AgentMessage` 的正常弱证据路径，没有覆盖
role/type contradiction。

影响：

- 错误或被污染的 adapter mapping 可把 assistant 自报文本封装成 observed result；
- action completion 和 validation pass 的 evidence provenance 不再可靠；
- 后续 CX1-02 持久化会固化伪造事实。

最小修复范围：

- 增加 item_type/role/evidence provenance 一致性 validator；
- assistant-role result 必须 fail-closed 为 observation/diagnostic，不能参与 pairing；
- 增加 assistant-role ToolResult/CommandResult/PatchResult 与 tool-role AgentMessage 的
  对抗测试；
- 保持现有真实 adapter 兼容所需的明确 `None` role 策略，但不能把显式 assistant role
  当 tool evidence。

### P1-03 — 单个 PatchResult 的互斥终态信号按字段优先级择一，failed result 可变 applied

Patch 分支先信任 `result.success`，仅当该字段缺失时才检查 exit code 或
`result.outcome`（`event_normalizer.py:771-803`）。因此同一个 result 同时声明
`success=True` 和 `outcome=FAILED` 时，代码直接设置 `observed_success=True`，没有
terminal-conflict diagnostic。

纯内存最小复现：

```text
PatchApply:
  call_id = patch-1
  targets = ["src/alpha.py"]
PatchResult:
  call_id/type/scope = exact match
  success = true
  outcome = failed
```

实际结果：

```text
patch.state = applied
patch.reason_code = patch_success_observed
diagnostics = []
```

现有 multiple-result conflict 测试能拒绝两个结果的一真一假，但没有覆盖同一个结果内部
多个结构化终态字段互相冲突。

影响：

- 明确包含 `failed` 的 observed result 可生成成功 Patch fact；
- Patch Status Accuracy 和后续 change/validation path 不可信；
- 违反“failed result 不得成功、ambiguous 不择一边”。

最小修复范围：

- 汇总 success/outcome/exit 的所有适用结构化信号；
- 只有全部已提供信号一致时才推进 `applied` 或 `failed`；
- 任一冲突保持 `apply_invoked`/unknown truth 并发 bounded terminal-conflict diagnostic；
- 增加 success/outcome/exit 的全冲突矩阵测试。

### P1-04 — Locator canonicalization 可被嵌入式绝对路径绕过并原样泄漏

`_EVENT_LOCATOR_RE` 只要求 `codex://thread/.../item/...#event=N`
（`contracts.py:36`）。`_FILESYSTEM_ABSOLUTE_RE` 只在绝对路径前一字符是字符串开头、
`=`、`:` 或空白时命中（`contracts.py:41-44`）；放在 `/item//Users/...` 后的
`/Users/...` 前一字符是 `/`，因此绕过。

`ObservableCodexItem._locator_scope()` 只检查 locator 的 thread/turn 字符串前缀
（`contracts.py:465-479`），没有把 locator 的 item segment 绑定到 `item_id`，也没有
做 canonical URI segment 解析。`_evidence()` 随后把该 locator 原样复制到 derived
event（`event_normalizer.py:177-186`）。

纯内存最小复现：

```text
item_id = plan-safe-id
item_type = Plan
source_locator =
  codex://thread/thread-a/turn/turn-a/item//Users/alice/private/source.py#event=10
```

实际结果：

```text
ObservableCodexItem accepted = true
derived evidence locator contains "/Users/alice/private/source.py" = true
```

影响：

- 违反 derived output 不泄漏绝对 source path 的安全合同；
- locator 未严格绑定 item identity，削弱 source provenance；
- 一旦进入 CX1-02 store/context，绝对路径会被持久化或暴露。

最小修复范围：

- 用结构化 URI/segment 解析替代上下文相关的 absolute-path regex；
- 拒绝 raw/encoded absolute、重复 slash、`.`/`..`、反斜杠和非 canonical segment；
- 把 locator 的 thread/turn/item/observable ordinal 与 scope、item_id、order 明确绑定，
  或仅从这些已验证字段重建 locator；
- 增加 raw/encoded POSIX/Windows absolute、segment mismatch、traversal 和 secret probes。

## 3. 独立正向验证

### 3.1 Contracts/export 与纯函数边界

- 三个版本常量明确：
  `codex-observable-event-contract-v1`、
  `codex-observable-event-normalizer-v1`、
  `codex-observable-event-state-machine-v1`。
- Action/Patch/Validation/Observation、link、diagnostic、summary/result 均基于 frozen
  Pydantic contracts；输出 structural evidence 不复制 raw payload/reasoning。
- `__init__.py` 公开了版本、核心 state/contract 和 `normalize_codex_events_v1`。
- 独立 deepcopy probe 确认 normalizer 前后原始 `ObservableCodexItem` 相等：
  `source item unchanged: True`。
- 目标模块没有 SQLite、store、schema、materialization、runtime flag 或生产 wiring。

### 3.2 Action/call-result/exit truth 的已通过路径

现有专项和独立复跑确认以下路径 fail-closed：

- missing result：action/validation 保持 invoked；
- duplicate item/result、duplicate call、multi-result、ambiguous call：不任意绑定；
- result observable order 不晚于 call：不配对；
- cross-thread/cross-generation/scope/type mismatch：不配对；
- late result：按稳定 observable order 配对；
- nested exact integer exit 0/nonzero：分别 passed/failed；
- bool/string/missing/multiple/depth-bounded exit：不得 passed；
- nonallowlisted/shell/absolute/traversal/cross-project/unknown target：不得 passed；
- Plan、AgentMessage、mentioned-only、existing derived ValidationResult：不得升级；
- timeout/cancel/failed/unknown action 状态均显式注册；
- reasoning/agent_reasoning/token/system/developer 均为 0 units。

这些正向结果不覆盖 P1-01/P1-02 的 provenance/binding 缺口。

### 3.3 Patch truth 的已通过路径

- patch proposal、apply invocation、observed applied、observed failed 分离；
- 无 result 不得 applied；
- diff/text claim 不得 applied；
- failed exit result 得到 failed；
- 多个互相冲突的 results 不择一边；
- unsafe/unknown target 即使 result success 也不得 applied。

这些正向结果不覆盖 P1-03 的单 result 内 terminal conflict。

### 3.4 Determinism/idempotency/bounds

- scrambled input 得到相同 events/links/diagnostics/content digest；
- exact duplicate input 不重复 event/link；
- event/link/diagnostic 使用 stable sort 和 canonical SHA-256；
- diagnostics 上限为 256，超出时生成 bounded truncation diagnostic；
- 无 wall clock 或 randomness dependency；
- 400 组合现有 False Validated truth table：唯一允许路径 1，测试内
  `false positives=0`。

P1-01 与 P1-02 证明该 400 组合 denominator 不完整，不能据此授权 X-T1。

## 4. 测试与证据命令

所有 Python/pytest 命令均设置 `PYTHONDONTWRITEBYTECODE=1`；pytest 禁用
cache provider，ruff 使用 `--no-cache`。正式库及其 WAL/SHM/sidecar 未打开、未 hash、
未 checkpoint；只使用纯内存对象、immutable repository fixtures 和 pytest 临时目录。

### 4.1 CX1-01 专项

```text
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' \
  uv run pytest -q \
  tests/test_codex_event_normalizer_v1.py \
  tests/test_codex_state_machine_v1.py

30 passed
```

其中 `test_false_validated_exhaustive_truth_table_has_zero_false_positives` 实际枚举
`5 × 5 × 4 × 2 × 2 = 400` 组合并断言：

```text
passed combinations = 1
false positives = 0
```

### 4.2 Codex + adapter 固定回归

```text
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' \
  uv run pytest -q $(rg --files tests -g 'test_codex*.py' | sort)

145 passed
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' \
  uv run pytest -q tests/test_real_source_adapters.py

3 passed
```

固定集合合计：`148 passed`。另独立聚焦复跑 existing adapter/normalization 三个用例也为
`3 passed`。唯一 warning 是既有 Starlette/httpx deprecation warning，不属于 Gate
阻塞项。

### 4.3 目标六文件静态检查

```text
uv run ruff check --no-cache <6 target files>
All checks passed!

uv run ruff format --check <6 target files>
6 files already formatted

in-memory compile: 6/6 PASS
whitespace/diff audit: 6/6 PASS
```

目标六文件：

1. `src/evidence_rag/rag/sources/codex/__init__.py`
2. `src/evidence_rag/rag/sources/codex/contracts.py`
3. `src/evidence_rag/rag/sources/codex/event_normalizer.py`
4. `src/evidence_rag/rag/sources/codex/state_machine.py`
5. `tests/test_codex_event_normalizer_v1.py`
6. `tests/test_codex_state_machine_v1.py`

### 4.4 独立对抗 probe 摘要

全部 probe 使用 `uv run python - <<'PY'` 的纯内存对象，没有在仓库留下 probe 文件：

```text
argv_target_mismatch:
  state = passed
  claimed target = tests/test_alpha.py::test_alpha
  diagnostics = []
  false_validated_count = 0

claimed_target_absent_from_argv:
  state = passed
  diagnostics = []
  false_validated_count = 0

assistant_role_tool_result:
  links = 1
  state = passed
  false_validated_count = 0

patch_success_true_outcome_failed:
  state = applied
  diagnostics = []

embedded_absolute_locator:
  accepted = true
  leaked = true
```

## 5. Scope boundary 与未实现确认

本 Gate 只审 CX1-01 六文件。静态 import/identifier audit 与工作区检查确认本实现未新增：

- CX1-02 schema/store/materialization；
- `codex_derived_facts` / `codex_event_links` persistence；
- X-T1 Treatment Run、artifact 或 metrics；
- X2 Episode/Unit；
- runtime/API/config/storage wiring；
- feature flag 或 production opt-in；
- Golden/evaluation/baseline 修改。

共享 main 在 Gate 前已高度 dirty；上述六个 CX1-01 文件均为 untracked，其他既有 dirty
文件不归因于本 Gate。评审没有修改任何实现、测试、config、Golden/evaluation、
fixtures、正式库或 Git。

本 Gate 的唯一仓库写入是：

`docs/rag-optimization/development/reviews/10_CODEX_X1_GATE_REVIEW.md`

## 6. 后续授权状态

在 P1-01 至 P1-04 修复并由新的独立 Gate 复验前：

- `CX1-02 NOT_AUTHORIZED`
- `X-T1 NOT_AUTHORIZED`
- X-T1 harness preparation 也不得用当前 normalizer 产生可发布 truth；
- 当前没有 X-T1 Run/metrics，不得填写优化收益；
- X2 继续 `NOT_AUTHORIZED`。

修复应保持在 CX1-01 contracts/normalizer/state-machine/tests 范围，不应借机实施 store、
schema、runtime 或 production wiring。

## 7. 第二轮极简复审（首轮四个 P1）

复审日期：2026-07-29  
复审对象：实施任务 `019fab06-1ff3-7440-9434-4c122d6ff86c` 的首轮 Gate 修复  
复审边界：只重放首轮 P1-01 至 P1-04 与关键正负主路，不扩展 P2/P3

### 7.1 第二轮 Gate 结论

`CODEX X1 CX1-01 ENGINEERING FAIL`

- `P0 findings: 0`
- `P1 findings: 1`
- `CX1-02 NOT_AUTHORIZED`
- `X-T1 NOT_AUTHORIZED`
- 当前尚无 X-T1 Treatment Run 或 metrics。
- X2 仍为 `NOT_AUTHORIZED`。

首轮 P1-01、P1-02、P1-04 已关闭；P1-03 的原始浅层
`success=true + outcome=failed` 复现也已关闭。但 P1-03 的“所有提供的 patch terminal
信号必须一致、无法判定时 fail-closed”仍未完整实现：bounded exit payload 和显式
`outcome=unknown` 可被忽略，并在同时存在 `success=true` 时无诊断地产生 `applied`。

该问题仍是首轮 P1-03 的 patch truth 主流程，不是新扩展的 P2/P3。虽然专项 74、固定
Codex + real-source adapter 192、800 组合 False Validated 和六文件静态检查均通过，
仍不能授权 CX1-02 或 X-T1 harness preparation。

### 7.2 已关闭项

#### P1-01 — CLOSED

Validation target 现从 canonical、allowlisted、no-shell argv 的实际 positional targets
确定性抽取，再与声明 targets 做 exact-set 比较
（`event_normalizer.py:475-584, 1202-1248`）。

独立重放结果：

```text
argv B + declared target A     -> target_unknown / target_mismatch
pytest -q + declared target    -> target_unknown / target_absent
selector mismatch              -> target_unknown / target_mismatch
multi-target subset mismatch   -> target_unknown / target_mismatch
-k option value impersonation  -> target_unknown / target_absent
unknown option=value           -> target_unknown / target_argument_unsupported
exact single target + exit 0   -> passed
exact multiple targets + exit 0-> passed
exact target + nonzero exit    -> failed
```

Action event 的 target 字段也只使用成功绑定后的 targets
（`event_normalizer.py:1151-1155`）；summary invariant 重新从 event argv 抽取 targets
并核对 exact equality（`event_normalizer.py:1388-1409`）。

#### P1-02 — CLOSED

Invocation/result 的显式 role provenance 已参与候选资格：

- `role=None/tool` 允许进入 observable call/result pairing；
- assistant/user/system/developer role 的 ToolResult/CommandResult/PatchResult 均被排除；
- 矛盾项只生成 `contradictory_provenance` observation 和
  `role_provenance_conflict` diagnostic；
- assistant-role command invocation、tool-role AgentMessage/Plan 不升级 action、
  patch 或 validation。

关键实现位于 `event_normalizer.py:144-167, 769-804, 982-994`。独立枚举
`3 result types × 4 forbidden roles = 12`，结果为：

```text
forbidden role cases fail-closed = 12/12
call/result links = 0
completed/applied/passed = 0
contradictory provenance observations = present
```

#### P1-04 — CLOSED

`canonical_codex_locator_v1()` 现从 scope/thread/turn、item_id 与 observable_order 重建
唯一 locator（`contracts.py:524-538`）；`ObservableCodexItem` 要求输入 locator 与该值
全字符串相等（`contracts.py:573-582`）。

结构化 locator validator 同时拒绝非 canonical scheme/netloc/path/query/fragment、
重复 slash、raw/encoded slash、Windows/backslash、dot/dotdot、control、credential
和非 canonical percent encoding（`contracts.py:101-194`）。

第二轮独立重放 raw/encoded POSIX absolute、Windows、traversal、thread mismatch 和
observable-order mismatch：

```text
malicious/mismatched locators rejected = 6/6
normalization reached = 0
derived locator leakage = 0
canonical locator positive path = accepted
```

### 7.3 剩余 P1 finding

#### P1-R2-01 — Patch terminal aggregation 忽略 bounded/unknown 信号，仍可错误 applied

`_patch_terminal_truth()` 只把精确 integer exit 转成布尔信号，并只对
`AMBIGUOUS_EXIT_CODE` 强制 conflict（`event_normalizer.py:646-670`）：

- `EXIT_PAYLOAD_BOUNDED` 和 `INVALID_EXIT_CODE` 被忽略；
- 显式 `ObservedOutcome.UNKNOWN` 被忽略；
- 若同一 result 另有 `success=True`，剩余 signals 只有 `[True]`，函数返回成功。

Patch 主分支随后把该结果直接推进 `applied`，且不产生
`patch_terminal_conflict`/`patch_result_unknown`/exit diagnostic
（`event_normalizer.py:1053-1095`）。

纯内存最小复现一：

```python
payload = {"exit_code": 1}
for index in range(8):
    payload = {f"layer_{index}": payload}

call = ObservableCodexItem(
    item_type="PatchApply",
    call_id="patch-bounded",
    call_type="patch_apply",
    targets=("src/alpha.py",),
    # canonical locator + exact scope，其余字段省略
)
result = ObservableCodexItem(
    item_type="ToolResult",
    call_id="patch-bounded",
    call_type="patch_apply",
    success=True,
    payload=payload,
    # canonical later locator + exact scope
)
```

该 payload 的 exit scan 因深度上限返回 `EXIT_PAYLOAD_BOUNDED`；深层实际 integer
exit 为 1，与 `success=True` 冲突，但实际 normalizer 输出：

```text
patch.state = applied
diagnostics = []
```

纯内存最小复现二：

```text
ToolResult.success = true
ToolResult.outcome = unknown
```

实际输出同样是：

```text
patch.state = applied
diagnostics = []
```

补充 malformed exit probe：

```text
success = true
payload.exit_code = "1"
patch.state = applied
diagnostics = []
```

影响：

- bounded payload 可隐藏与 success 相反的真实 integer exit；
- result 明确声明 outcome unknown 时仍产生确定的 applied fact；
- 下游 Patch Status、change path 和后续 CX1-02 materialization 会把未证明成功的 patch
  固化成成功事实；
- 违反“所有提供终态一致才 applied、无法判定不择一边、unknown 诚实”的首轮修复合同。

现有 44-case matrix
（`tests/test_codex_event_normalizer_v1.py:784-839`）只枚举：

```text
success: None/True/False
outcome: None/completed/failed/timeout/cancelled
exit:    None/0/1
```

它没有纳入 `outcome=unknown`、bounded/invalid/ambiguous exit parser outcomes；因此
`44/44` 通过不能关闭该 P1。

最小修复范围仍只在 CX1-01：

- `_patch_terminal_truth()` 对显式 outcome unknown 保持未知，不得与 success/exit 合并成
  applied；
- 对 `EXIT_PAYLOAD_BOUNDED`、`INVALID_EXIT_CODE`、`AMBIGUOUS_EXIT_CODE` 等“存在但无法
  形成可信 integer terminal truth”的结果 fail-closed；
- 冲突或不可判定时保持 `apply_invoked`，发固定 bounded diagnostic；
- 扩展 matrix 覆盖 outcome unknown、bounded/invalid/ambiguous exit 与
  success/outcome 的交叉组合；
- 保留 success-only、outcome-only、integer-exit-only 以及全部一致的正常
  applied/failed 路径；
- 不需要也不得实施 CX1-02、store/schema/runtime。

### 7.4 第二轮固定验证

所有命令继续设置 `PYTHONDONTWRITEBYTECODE=1`；pytest 禁用 cache provider，ruff 使用
`--no-cache`。没有打开、hash、checkpoint 或写入正式库及其 sidecars。

```text
CX1-01 专项：
tests/test_codex_event_normalizer_v1.py
tests/test_codex_state_machine_v1.py
74 passed
```

专项内 False Validated truth table 实际为：

```text
pairing 5 × exit 5 × target 8 × allowlist 2 × shell 2 = 800
legal passed combinations = 1
false positives = 0
```

固定兼容集合：

```text
all test_codex*.py = 189 passed
test_real_source_adapters.py = 3 passed
total = 192 passed
```

唯一 warning 仍是既有 Starlette/httpx deprecation warning。

目标六文件：

```text
ruff check --no-cache       PASS
ruff format --check         6 files already formatted
in-memory compile           6/6 PASS
whitespace/diff audit       6/6 PASS
```

主流程无回退项：

- exact late result、missing/duplicate/out-of-order/multiple/ambiguous 继续 fail-closed；
- scope/project/thread/turn/generation/ACL/type equality 继续生效；
- exact integer exit 0/nonzero 与 bool/string/missing 拒绝继续生效；
- deterministic order、exact duplicate idempotency、stable digest 继续通过；
- reasoning/agent_reasoning/token/system/developer units 继续为 0；
- diagnostics 继续 bounded 为 256。

### 7.5 第二轮 scope 与授权状态

第二轮复审确认当前仍未实施：

- CX1-02 schema/store/materialization；
- X-T1 Treatment Run、artifact 或 metrics；
- X2 Episode/Unit；
- runtime/API/config/storage wiring 或 production flag；
- 正式数据库访问或写入。

本轮未修改实现、测试、config、Golden/evaluation、其他 docs、fixtures、正式库或 Git；
只向本报告追加本节。

在 P1-R2-01 修复并经下一轮独立复验前，授权状态保持：

- `CX1-02 NOT_AUTHORIZED`
- `X-T1 NOT_AUTHORIZED`
- X-T1 harness preparation 不得使用当前 patch terminal truth；
- 当前仍无 X-T1 Run/metrics；
- X2 仍为 `NOT_AUTHORIZED`。

## 8. 第三轮极简复审（仅 P1-R2-01）

复审日期：2026-07-29  
复审对象：第二轮唯一剩余的 patch unknown/bounded terminal truth  
复审边界：只重放 P1-R2-01 指定正负路径，不扩展 P2/P3

### 8.1 第三轮 Gate 结论

`CODEX X1 CX1-01 ENGINEERING PASS`

- `P0 findings: 0`
- `P1 findings: 0`
- `CX1-02 AUTHORIZED`
- `X-T1 HARNESS PREPARATION AUTHORIZED`
- 当前尚无 X-T1 Treatment Run 或 metrics。
- X2 仍为 `NOT_AUTHORIZED`。

本节是当前有效的最终 Engineering Gate 结论；前两轮 FAIL 与授权关闭状态作为历史审计
保留。第三轮只复审第二轮 P1-R2-01，没有因模块体量、P2/P3、X-B0
`NON_QUALIFIED`、既有 warning 或非目标 lint 阻塞。

### 8.2 P1-R2-01 — CLOSED

#### Exit scan 不再混淆 missing 与不可信 evidence

`_observed_exit_code()` 现先记录 exit evidence 的真实 scan 状态
（`event_normalizer.py:587-634`）：

- ToolResult/CommandResult 中唯一 exact integer `exit_code` 返回可信整数；
- 字段真正不存在返回 `MISSING_EXIT_CODE`；
- string、bool、null、PatchResult 中的 `exit_code` 返回 `INVALID_EXIT_CODE`；
- 多个 exit candidates 返回 `AMBIGUOUS_EXIT_CODE`；
- 超出 depth/node bound 返回 `EXIT_PAYLOAD_BOUNDED`；
- `exit`、`exitCode`、`returnCode`、`statusCode` 等 unsupported aliases 被识别为
  invalid evidence，而不是当作字段不存在。

因此 success-only 的空 payload 仍可保持原正向语义，但“字段存在且不可信”不再被
伪装成 missing。

#### Patch terminal truth 显式建模 unknown signal

`_patch_terminal_truth()` 现把以下情况建模为 unknown signal
（`event_normalizer.py:650-684`）：

- 显式 `outcome=unknown`；
- `INVALID_EXIT_CODE`；
- `AMBIGUOUS_EXIT_CODE`；
- `EXIT_PAYLOAD_BOUNDED`。

聚合规则为：

1. unknown signal 与任一可信 success/outcome/integer-exit signal 共存：
   返回无终态 + `patch_terminal_conflict`；
2. unknown signal 单独存在：
   返回无终态 + `patch_result_unknown`；
3. 没有 unknown signal，但可信 signals 互相冲突：
   返回无终态 + `patch_terminal_conflict`；
4. 没有 unknown signal，所有可信 signals 全部一致：
   唯一允许推进 `applied` 或 `failed`；
5. 所有 signals 真正缺失：
   保持 `apply_invoked`，并给 `patch_result_unknown`。

Patch 主分支消费该固定 diagnostic 并保持无终态
（`event_normalizer.py:1067-1103`），不再按字段优先级择一。

### 8.3 独立 P1-R2-01 对抗重放

全部 probes 使用 canonical scope/locator、exact call/result pairing、safe bound target
和纯内存 records。

#### Unknown/conflict 与可信 signal 共存

独立重放：

```text
bounded deep exit 1 + success true
invalid string "1" + success true
bool exit + success true
unsupported exitCode + success true
ambiguous exit 0/1 + success true
outcome unknown + success true
outcome unknown + success false
```

七条实际结果全部为：

```text
patch.state = apply_invoked
diagnostic = patch_terminal_conflict
applied/failed = 0
```

第二轮最小复现现已关闭：

```text
bounded deep exit 1 + success true:
  before = applied / no diagnostic
  now    = apply_invoked / patch_terminal_conflict

outcome unknown + success true:
  before = applied / no diagnostic
  now    = apply_invoked / patch_terminal_conflict

invalid exit "1" + success true:
  before = applied / no diagnostic
  now    = apply_invoked / patch_terminal_conflict
```

#### Unknown-only

独立重放：

```text
outcome unknown only
invalid exit only
bounded exit only
unsupported returnCode only
```

四条实际结果全部为：

```text
patch.state = apply_invoked
diagnostic = patch_result_unknown
applied/failed = 0
```

#### 正向与 all-missing 不回退

独立重放实际结果：

```text
success true only             -> applied
success false only            -> failed
known completed outcome only  -> applied
known failed outcome only     -> failed
integer exit 0 only           -> applied
integer exit 1 only           -> failed
success/completed/exit0 一致  -> applied
failure/failed/exit1 一致     -> failed
all terminal signals missing  -> apply_invoked / patch_result_unknown
```

只有可信且一致的 observed terminal evidence 能产生成功/失败事实。

### 8.4 新增矩阵与既有主路

第三轮专项新增：

```text
outcome unknown × success(None/True/False) ×
exit(none/0/1/string/bool/ambiguous/bounded) = 21 cases

untrusted exit(string/bool/ambiguous/bounded/null/unsupported) ×
result type(ToolResult/PatchResult) = 12 cases
```

原 44-case success/outcome/integer-exit matrix 继续通过；success-only、known
outcome-only、integer-only、全一致 success/failed 与 all-missing 均有正向/诚实路径
覆盖。

前两轮其他关闭项未回退：

- argv-derived validation target 与 declared target exact-set binding；
- forbidden result role provenance 不参与 pairing；
- canonical locator 从 scope/item/order 重建并 exact equality；
- missing/duplicate/out-of-order/multiple/ambiguous/cross-scope pairing fail-closed；
- scope/project/thread/turn/generation/ACL/type equality；
- deterministic ordering、exact duplicate idempotency 与 stable digest；
- reasoning/agent_reasoning/token/system/developer units 为 0；
- diagnostics bound 为 256。

专项内 False Validated truth table 仍为：

```text
pairing 5 × exit 5 × target 8 × allowlist 2 × shell 2 = 800
legal passed combinations = 1
false positives = 0
```

### 8.5 第三轮固定验证

所有 Python/pytest 命令设置 `PYTHONDONTWRITEBYTECODE=1`；pytest 禁用 cache provider，
ruff 使用 `--no-cache`。

```text
CX1-01 专项：
tests/test_codex_event_normalizer_v1.py
tests/test_codex_state_machine_v1.py
107 passed
```

```text
all test_codex*.py = 222 passed
test_real_source_adapters.py = 3 passed
fixed total = 225 passed
```

唯一 warning 是既有 Starlette/httpx deprecation warning，不属于本 Gate 阻塞项。

目标六文件质量检查：

```text
ruff check --no-cache       PASS
ruff format --check         6 files already formatted
in-memory compile           6/6 PASS
whitespace/diff audit       6/6 PASS
```

### 8.6 Scope boundary 与授权

第三轮确认当前仍未实施：

- CX1-02 schema/store/materialization；
- X-T1 Treatment Run、artifact 或 metrics；
- X2 Episode/Unit；
- runtime/API/config/storage wiring 或 production flag；
- 正式数据库访问或写入。

正式库继续是 `EXTERNAL_MUTABLE_SERVICE_OWNED`；本轮未打开、hash、checkpoint 或写入正式
库及其 WAL/SHM/sidecars。没有修改实现、测试、config、Golden/evaluation、其他 docs、
fixtures 或 Git；只向本报告追加本节。

当前授权边界为：

- `CX1-02 AUTHORIZED`
- `X-T1 HARNESS PREPARATION AUTHORIZED`
- 尚无 X-T1 Run/metrics，不得把 Engineering PASS 写成 treatment quality 或优化收益；
- X2 仍为 `NOT_AUTHORIZED`。

## 9. CX1-02 + X-T1 harness 联合 Engineering Gate

复审日期：2026-07-29  
CX1-02 实施任务：`019fab58-b0d0-7c52-8526-dd268c877a84`  
X-T1 harness 实施任务：`019fab59-24d7-7a20-8395-b2568dbb192a`  
复审范围：CX1-02 facts/window 与 X-T1 harness `PREPARED`，只判定 P0/P1

### 9.1 联合 Gate 结论

`CODEX X1 CX1-02 ENGINEERING FAIL`

`X-T1 HARNESS PREPARATION FAIL`

- `P0 findings: 0`
- `P1 findings: 3`
- `CX1-02 NOT_AUTHORIZED`
- `X-T1 NOT_AUTHORIZED`
- `X-T1 ISOLATED PRODUCTION RUN NOT_AUTHORIZED`
- CX1-01 Engineering 保持 complete；本轮不推翻第 8.1 节的 CX1-01 结论。
- 当前仍无 X-T1 Treatment Run、released metrics 或 qualification artifact。
- X2 仍为 `NOT_AUTHORIZED`。

三个 finding 都直接阻断本轮要求的主流程：facts 输入权威、output-window
证据/隐私，以及 Gate 后 exact-CX1-02 harness readiness。未因模块体量、P2/P3、
X-B0 `NON_QUALIFIED`、既有 warning 或非目标 lint 阻塞。

### 9.2 P1-X1-JOINT-01 — 自洽伪造 normalization 可升级 source truth

#### 最小复现

从同一 canonical scope、call/result locator 和 observed result evidence 生成真实
`exit_code=7` 的 CX1-01 result，原始 action/validation 分别为 `failed/failed`。
随后只在 `CodexEventNormalizationResult` payload 内完成以下自洽重写：

```text
action:
  failed / exit 7
  -> completed / exit 0

validation:
  failed / exit 7
  -> passed / exit 0

同时重算：
  event_id
  summary action/validation state counts
  summary.content_sha256
```

把该 exact frozen result 交给 `build_codex_fact_publication_v1()`，纯内存探针实际输出：

```text
FAKE_NORMALIZATION_ACCEPTED
[('validation', 'passed', 0), ('action', 'completed', 0)]
```

#### 原因

`_verify_normalization_result()` 会重算 result 内部的 digest、event/link identity、
truth shape、summary counts 和 locator，但输入没有 immutable source Items 或其内容
摘要（`facts_v1.py:1025-1117`）。`_coerce_normalization_result()` 因而只能证明
result 内部自洽，不能证明它等于 exact CX1-01 normalizer 对权威 source Items 的输出
（`facts_v1.py:1140-1159`）。

`build_codex_fact_publication_v1()` 随后直接 materialize 这个自洽 result
（`facts_v1.py:1496-1546`）。原始 result evidence 的 locator/item id 保持不变时，
facts 层无法发现终态和 exit 已被调用方一起改写。

#### 影响

调用方可把真实 failed result 升级成 completed/passed derived facts；这正是 CX1-02
输入 authority 主流程上的 false truth，不是单纯防御性 hardening。

#### 最小修复范围

facts builder 必须绑定不可变 source authority，而不只是 result 自摘要。可接受的最小
闭环是：

1. 接收 exact immutable source Items/content digest，并用固定 public CX1-01
   normalizer 重新执行；
2. exact equality 比较重新生成的完整 result（events/links/diagnostics/summary/digest）；
3. 将 source authority digest 和 exact normalizer identity 纳入 publication identity。

只增加更多 result 内部一致性字段或允许调用方重算的 digest 不能关闭该 finding。

### 9.3 P1-X1-JOINT-02 — output window 未绑定 raw content 且允许绝对路径泄漏

#### 最小复现 A：任意 window 可绑定真实 result locator

构造一个 frozen `CodexOutputWindow`：

```text
head = ""
tail = "fabricated traceback tail"
total_chars = 100
retained/omitted/truncated = 内部自洽
content_sha256 = sha256:000...000
error_tail = true
```

把它放入 `CodexLocatedOutputWindow` 并引用真实 failed result 的 exact
`source_item_id/source_locator`。纯内存 publication 成功，实际输出：

```text
FORGED_WINDOW_ACCEPTED True
```

`CodexOutputWindow._metadata_is_exact()` 只检查 retained/omitted 的算术一致性和
retained UTF-8 byte 下界，不能用缺失的 raw content 验证 total/hash/head/tail
（`facts_v1.py:180-220`）。`_validate_scope_and_windows()` 只证明 locator 是某个
observed result endpoint，并信任调用方设置的 `error_tail` 布尔值
（`facts_v1.py:1181-1211`）。

#### 最小复现 B：绝对路径进入 derived publication

对以下 observed error text 调用 public window builder：

```text
Traceback:
  File "/Users/example/private/project/secret.py", line 1
FAIL
```

再绑定真实 failed result locator，实际输出：

```text
ABS_PATH_WINDOW_ACCEPTED True
```

该绝对路径出现在 publication/facts 的 derived output window 中。当前 secret regex
会拒绝若干 credential 形态，但没有 absolute POSIX/Windows path 的拒绝或清洗
（`facts_v1.py:267-335`）。

#### 影响

- `content_sha256`、total、omitted、error-tail coverage 可由调用方伪造；
- 假 traceback/错误尾部能以“observed output”进入派生事实；
- 本机绝对路径可进入 window/publication，违反本 Gate 的 locator/privacy 边界。

#### 最小修复范围

output window 必须由 builder 在 raw observed result content 或不可伪造的 source
content authority 上内部构建；publication 不应接受未绑定 raw content 的外部 window
作为已验证 evidence。需要 exact 验证 full-content hash、字符/UTF-8 byte 总量、
head/tail 算法结果、error classification，并在 truncation 前对完整 content 做
credential/absolute POSIX/Windows path 拒绝或确定性清洗。

### 9.4 P1-X1-JOINT-03 — harness 无法在本轮 Gate 后重新 prepare exact facts identity

#### 三个稳定阻塞点

1. 本轮要求的精确授权 token 是：

   ```text
   X-T1 ISOLATED PRODUCTION RUN AUTHORIZED
   ```

   harness 却只注册：

   ```text
   X-T1 PRODUCTION EXECUTION AUTHORIZED
   ```

   （`codex_x1.py:71-75`、`codex_x1.py:964-1015`）。

2. `prepare_codex_x1_v1()` 把整份旧 review 的 SHA256 和旧结论编号 `8.1` 固定在代码
   中（`codex_x1.py:1490-1513`）。因此向同一 review 合法追加任何 CX1-02 Gate
   结论后，whole-file digest 必然变化，re-prepare 固定拒绝。

3. preparation contract 把 CX1-02 component set 永久限制为 `None`，第三臂永久限制
   为 `UNAVAILABLE`（`codex_x1.py:518-565`）；当前只有 CX1-01 component identities，
   没有 facts/window module/function/class/export/version/source/code identity。
   `run_codex_x1_v1()` 也无条件抛出 `NOT_AUTHORIZED`
   （`codex_x1.py:2372-2379`）。

#### 最小复现

在 tmp 中复制当前 review 文本并仅追加本轮若通过时要求的精确正向结论，不写仓库：

```text
REQUIRED_AUTH_TOKEN_REJECTED CodexX1VerificationError
POST_APPEND_REPREPARE_REJECTED CodexX1VerificationError
CURRENT_FACTS_ARM UNAVAILABLE None
```

这意味着即使 CX1-02 另外两个 finding 不存在，报告追加后仍不能完成用户明确要求的
“re-prepare 确认 exact facts identity ready 且零 artifact”。

#### 影响

harness 不能从 `PREPARED / third arm UNAVAILABLE` 进入 Gate 后的 exact facts-ready
状态，也不能消费本轮规定的授权语言；因此当前不能授权唯一 isolated production
Run。

#### 最小修复范围

- 注册并冻结 CX1-02 facts/window 的 module、public exports、contract/builder
  versions、source/code digests 与 component-set hash；
- final-decision parser 必须消费本轮规定的 exact token，并只认最后一个 CX1-02
  Engineering conclusion，不能被历史 FAIL/`NOT_AUTHORIZED` 子串误导；
- 不再用“会被同文件追加自然改变”的旧 whole-file hash + 固定 `8.1` 作为未来
  conclusion 的不可更新锁；应绑定 exact final section identity/内容和任务身份；
- re-prepare 在且仅在 Gate PASS、P0/P1=0、exact facts identities 全匹配时把第三臂
  标为 ready，同时继续保持 `run_created=False`、`metrics_created=False`；
- future runner 仍须保留 explicit tmp root/output/raw SQLite/seed、no network、
  no symlink/reuse/outside-root 的 fail-closed 边界。

### 9.5 通过项与独立固定验证

以下通过项成立，但不能覆盖三个 P1：

#### CX1-02 既有专项

```text
tests/test_codex_facts_v1.py = 30 passed
```

覆盖 deterministic head/tail、Unicode/tiny budget、registered secret forms、
scope/ACL/generation、dedup evidence links、weak observation、tmp/:memory:
SQLite additive schema、idempotent active generation，以及 failpoint transaction
rollback。tmp SQLite 测试未遗留 WAL/SHM。

#### Harness 既有专项

```text
tests/test_codex_x1_run.py = 18 passed
```

当前旧 Gate preparation 独立重算：

```text
status = PREPARED / NON_QUALIFIED / NOT_AUTHORIZED
run_created = false
metrics_created = false
eligible cases = 23
membership sha256 =
  sha256:65fec3b1a3afc74709a3502b03f9b22893a63ce440d056cc787473d8622c29b6
denominators =
  event 3 / call 2 / patch 4 / validation 10 /
  false-validation 5 / output-window 1 / error-tail 2
X-B0 recomputed anchors =
  Event 1/3 / Call 1/2 / Patch 3/4 / Validation 7/10 /
  FalseValidated 1/5
arms = AVAILABLE / PROVISIONAL / UNAVAILABLE
cx1_02_component_set_hash = None
```

Golden `aa293...12d1` 与 correction `f5da...ca6d` 均由 immutable authority
重新验证；未信任 summary。

#### 固定联合回归

所有 Python/pytest 命令设置 `PYTHONDONTWRITEBYTECODE=1`；pytest 禁用 cache
provider：

```text
tests/test_codex*.py
tests/test_real_source_adapters.py
tests/test_evaluation_governance.py
tests/test_source_history_acl.py

279 passed, 1 existing Starlette/httpx deprecation warning
```

目标十文件静态检查：

```text
ruff check --no-cache       PASS
ruff format --check         10 files already formatted
in-memory compile           10/10 PASS
whitespace/diff audit       10/10 PASS
```

### 9.6 Scope boundary 与写锁审计

本轮没有执行 X-T1 released treatment，没有创建 Run、metrics、qualification
artifact 或 smoke artifact。没有实现 X2，也没有 production/runtime/config/API/store
wiring。

正式库继续是 `EXTERNAL_MUTABLE_SERVICE_OWNED`：本轮没有打开、hash、checkpoint、
写入正式库或触碰其 WAL/SHM/sidecars。所有新增对抗探针只在纯内存或自动清理的 tmp
目录执行，没有在仓库留下 probe 文件。

本轮没有修改任何实现、测试、config、Golden/evaluation authority、其他 docs、
fixtures、evals、正式库或 Git；唯一写入是向本报告追加第 9 节。共享 dirty main
保持原状，没有 branch/worktree、commit 或 push。

在 P1-X1-JOINT-01/02/03 全部修复并经同一独立 Gate 重放前：

- `CX1-02 NOT_AUTHORIZED`
- `X-T1 NOT_AUTHORIZED`
- 不得执行 isolated production X-T1 Run；
- 当前仍无 X-T1 Run/metrics；
- X2 仍为 `NOT_AUTHORIZED`。

## 10. CX1-02 + X-T1 harness 联合 Engineering Gate

复审日期：2026-07-29  
CX1-02 实施任务：`019fab58-b0d0-7c52-8526-dd268c877a84`  
X-T1 harness 实施任务：`019fab59-24d7-7a20-8395-b2568dbb192a`  
复审范围：第二轮极简复审，只重放联合首轮三个 P1 与关键主路，只判定 P0/P1

### 10.1 第二轮联合 Gate 结论

`CODEX X1 CX1-02 ENGINEERING FAIL`

`X-T1 HARNESS PREPARATION FAIL`

- `P0 findings: 0`
- `P1 findings: 1`
- `CX1-02 NOT_AUTHORIZED`
- `X-T1 ISOLATED PRODUCTION RUN NOT_AUTHORIZED`
- CX1-01 Engineering 保持 complete；P1-X1-JOINT-01 与 P1-X1-JOINT-03 已关闭。
- 当前仍无 X-T1 Treatment Run、released metrics 或 qualification artifact。
- X2 仍为 `NOT_AUTHORIZED`。

唯一剩余 finding 是首轮 P1-X1-JOINT-02 的 Windows UNC 绝对路径残余，直接阻断
output-window 隐私主路。未因模块体量、P2/P3、X-B0 `NON_QUALIFIED`、既有 warning
或非目标 lint 阻塞。

### 10.2 首轮三个 P1 重放结果

#### P1-X1-JOINT-01 — CLOSED

独立纯内存探针从 canonical immutable `ObservableCodexItem` source set 构造真实
`exit_code=7` 结果，再把 caller result 自洽改写为 completed/passed，同时重算
event ids、summary 和 digest。`build_codex_fact_publication_v1()` 通过固定绑定的
CX1-01 normalizer 从 source items 内部重建，并 exact compare，伪造结果被拒绝：

```text
FAKE_NORMALIZATION_REJECTED True
REAL_SOURCE_TRUTH [('action', 'failed', 7), ('validation', 'failed', 7)]
SOURCE_TAMPER_REJECTED True
NORMALIZER_WRAPPER_REJECTED True
```

source item content/scope/locator tamper、normalizer wrapper/monkeypatch/export identity
mismatch 均 fail-closed；真实 source 正常生成。source authority digest 与固定
normalizer identity 已进入 publication/fact/SQLite identity。cross-ACL/generation
不 dedup，raw source ids/evidence union 保留。

#### P1-X1-JOINT-02 — PARTIALLY CLOSED，残余见 10.3

caller supplied/fabricated/cross-result window 已被拒绝；window 只从 authoritative
normalization payload 的 raw content 内部重建。missing raw 不接受外部补窗且诚实
计数；正常 Unicode/error-tail 的 char/byte/hash/head-tail/truncation 元数据一致：

```text
EXTERNAL_WINDOW_REJECTED True
FABRICATED_WINDOW_REJECTED True
CROSS_RESULT_WINDOW_REJECTED True
MISSING_RAW_WINDOW 0 1
UNICODE_ERROR_WINDOW True True 40 43 40
```

在 omitted middle/tail 中带分隔边界的普通 POSIX absolute、double-encoded POSIX
absolute、drive-letter Windows absolute 与 credential marker 都在截断前被拒绝。
但 UNC/network-share 形式仍可进入 derived tail，故该首轮 finding 尚未完全关闭。

#### P1-X1-JOINT-03 — CLOSED

Gate parser 只选择 canonical parent 下 latest exact decision block，绑定 canonical
path、Gate source task、两个实施任务与 decision-block digest；历史 FAIL/
NOT_AUTHORIZED、旧 token、无关追加和 final decision tamper 均不能误授权。独立探针：

```text
CURRENT_GATE 9.1 NOT_AUTHORIZED UNAVAILABLE MATCH 26
FACTS_IDENTITIES 26 sha256:3115b8cb88af51b8eec2024b5703aa0dc297d03c4272dd90e729c98e9d249d7c
SYNTHETIC_READY 10.1 AUTHORIZED AVAILABLE False False ['gate.md']
UNRELATED_APPEND_STABLE True
FINAL_TAMPER_AUTHORIZED False
OLD_TOKEN_REJECTED True
WRAPPER_READY MISMATCH NOT_AUTHORIZED UNAVAILABLE
```

精确 synthetic final decision 可使第三臂进入 admission-ready，但仍保持零
Run/metrics/artifact；facts 26 identities 的 wrapper/pre-import injection/export/
version/digest mismatch 均使第三臂 `UNAVAILABLE`。本报告最终决定仍为 FAIL，不能进入
isolated production Run。

### 10.3 P1-X1-R2-01 — UNC 绝对路径可泄漏到 authoritative error tail

#### 最小复现

对 canonical failed `ToolResult` 的 authoritative raw stderr 构造超长前缀，把下列
marker 放在应保留的 error tail；publication 完全由真实 source items 内部重建：

```text
\\server\share\private\secret.py
%5C%5Cserver%5Cshare%5Cprivate%5Csecret.py
//server/share/private/secret.py
```

纯内存探针的三个输入都被接受，且均满足：

```text
publication accepted
window.truncated == True
window.error_tail == True
marker present in derived tail == True
```

作为边界核对，`%2F%2Fserver...` 当前会被拒绝；本 finding 不把该形式计为绕过。

#### 原因

`facts_v1.py` 的 raw absolute-path matcher 覆盖 single-slash POSIX 与 drive-letter
Windows path，encoded matcher 覆盖 single `%2f`、encoded drive 与 file URI；两者
都没有覆盖 Windows UNC/network-share 的 `\\server\share`、常见 alternate
`//server/share`，以及 percent-encoded backslash prefix `%5C%5C`。bounded decode
之后的 UNC backslash 仍落在同一缺口。

#### 影响

server/share、内部目录、用户名或资源定位信息可从 authoritative raw output 进入
derived facts window，并因 error-tail retention 被稳定保留。这违反本轮明确要求的
“raw/encoded POSIX/Windows abs 在 truncation 前拒绝”，属于 output-window
隐私主流程上的 P1，而不是 P2/P3 hardening。

#### 最小修复范围

在 truncation 前的统一 privacy scan 中 fail-closed 识别 canonical UNC/network-share
prefix，至少覆盖 raw `\\server\share`、alternate `//server/share`、encoded
`%5C%5Cserver%5Cshare` 及 bounded decode 后的等价形式；补充 omitted-middle 与
error-tail 回归，确认 marker 不进入任何 window 或 diagnostic。无需修改
normalizer、publication schema、harness、runtime 或正式库。

### 10.4 独立证据命令与数字

专项固定集：

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' -q \
  tests/test_codex_facts_v1.py tests/test_codex_x1_run.py

70 passed
```

当前相关固定集的独立超集（包含所有 Codex tests、real adapters、evaluation
governance 与 source-history ACL）：

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' -q \
  tests/test_codex*.py tests/test_real_source_adapters.py \
  tests/test_evaluation_governance.py tests/test_source_history_acl.py

301 passed, 1 existing Starlette/httpx deprecation warning
```

实施/主控提供的联合固定集数字为 290 PASS；本 Gate 使用当前显式相关超集重放，
未发现除 10.3 之外的回退。

目标十文件静态检查：

```text
ruff check --no-cache       PASS
ruff format --check         10 files already formatted
in-memory compile           10/10 PASS
whitespace/diff audit       10/10 PASS
```

独立对抗探针均以 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY' ... PY`
在纯内存或自动清理 tmp 目录执行，没有把 probe 文件写入仓库。

### 10.5 主路、边界与写锁审计

除 10.3 的 UNC 缺口外，CX1-02 source re-normalization、source-authority identity、
dedup scope/ACL/generation、raw evidence union、atomic rollback、link validation 与
V1-like table preservation 主路没有发现 P0/P1 回退。fixed-23 membership/
denominators、metrics/security/portable contracts 与 CX1-01 truth 主路也没有发现
P0/P1 回退。

本轮没有调用 released X-T1 treatment runner，没有生成 Run、metrics、artifact，
也没有执行 production/runtime/config/API/store wiring。`evals/codex/runs` 文件数
仍为 10，与首轮快照一致；没有新 Run tree 内容。

正式库继续是 `EXTERNAL_MUTABLE_SERVICE_OWNED`：本轮没有打开、hash、checkpoint、
写入正式库或触碰其 WAL/SHM/sidecars。SQLite 检查仅使用 `:memory:` 或自动清理的
tmp fixture。

本轮没有修改任何实现、测试、config、Golden/evaluation authority、其他 docs、
fixtures、evals、正式库或 Git；唯一写入是向本报告追加第 10 节。共享 dirty main
保持原状，没有 branch/worktree、commit 或 push。

## 11. CX1-02 + X-T1 harness 联合 Engineering Gate

复审日期：2026-07-29  
CX1-02 实施任务：`019fab58-b0d0-7c52-8526-dd268c877a84`  
X-T1 harness 实施任务：`019fab59-24d7-7a20-8395-b2568dbb192a`  
复审范围：第三轮极简复审，仅关闭 10.3 的 UNC P1 并复核 facts identity freshness

### 11.1 第三轮联合 Gate 结论

`CODEX X1 CX1-02 ENGINEERING PASS`

`X-T1 HARNESS PREPARATION PASS`

- `P0 findings: 0`
- `P1 findings: 0`
- `CX1-02 AUTHORIZED`
- `X-T1 ISOLATED PRODUCTION RUN AUTHORIZED`
- CX1-01 与 CX1-02 Engineering 均 complete。
- 当前仍无 X-T1 Treatment Run、released metrics 或 qualification artifact。
- X2 仍为 `NOT_AUTHORIZED`。

第二轮唯一 P1-X1-R2-01 已关闭，facts 的合法源码变化也已由 harness compile-time
reviewed identities 精确吸收。本轮没有因模块体量、P2/P3、X-B0
`NON_QUALIFIED`、既有 warning 或非目标 lint 阻塞。

### 11.2 P1-X1-R2-01 — CLOSED

#### Truncation 前 full-content privacy scan

`facts_v1.py` 现对 authoritative raw content 在任何 head/tail 截断前执行统一
privacy scan：

- 对原文与每一轮结果应用 NFKC；
- percent decode 最多四轮，保留每一轮 scan view；
- 同时检测 POSIX/drive path、raw/alternate/mixed UNC、extended UNC、device path
  与 network share；
- privacy exception 只返回固定错误，不回显 marker。

独立纯内存 publication 探针把 13 类 marker 分别放入 omitted-middle 与
error-tail：

```text
raw \\server\share
alternate //server/share
extended \\?\UNC\server\share
device \\.\pipe\codex
mixed \/server\share and /\server/share
%5C%5C and %2F%2F
mixed-case percent encoding
two/three/four-round percent encoding
NFKC full-width separators
```

结果：

```text
UNSAFE_PUBLICATIONS_REJECTED 26 / 26
DIAGNOSTIC_MARKER_LEAKS 0
```

所有输入都在 publication/window 产生前 fail-closed；没有 derived window 或
diagnostic 含 marker。

#### 负控与原主路

独立负控包含 normal HTTPS URL、percent-encoded URL、整数除法、普通 `//`
comment、token 内双斜线、relative target 与合法日志：

```text
NEGATIVE_PUBLICATIONS_ACCEPTED 7 / 7
```

原有 POSIX absolute、drive-letter Windows absolute 与 secret 继续拒绝：

```text
LEGACY_PRIVACY_REJECTED 3 / 3
```

Unicode/error-tail 元数据与 source truth 没有回退：

```text
UNICODE_WINDOW_EXACT True True 1200 3280
SOURCE_TRUTH [('action', 'failed', 7), ('validation', 'failed', 7)]
```

source re-normalization、window authority、head/tail/hash/char/UTF-8 byte metadata、
scope/ACL/generation/dedup、atomic publish/link rollback 与 V1-like preservation
主路未发现新增 P0/P1。

### 11.3 Harness identity freshness 与 latest Gate

当前 facts/window 的 26 个 reviewed identities 从静态 allowlist 重算并 exact
MATCH：

```text
FACTS_IDENTITIES 26
sha256:3630d5375f85d9bfd5ab156b00168d096d20ef9766f5460d3825993c122bbaf0
```

独立 fake replay：

```text
wrapper/callable digest fake      rejected
package export fake               rejected
version fake                      rejected
pre-import/module injection fake  rejected
IDENTITY_FAKES_BLOCKED 4 / 4
```

追加本节前，真实 latest 10.1 仍诚实保持：

```text
CURRENT 10.1 NOT_AUTHORIZED MATCH UNAVAILABLE
```

以当前 major 动态计算 next section 的纯 tmp synthetic exact PASS，不固定历史编号，
parser 选择 11.1，facts arm 进入 ready，且没有 Run、metrics、artifact 或 output：

```text
SYNTHETIC_NEXT 11.1 AUTHORIZED MATCH AVAILABLE
run_created=False
metrics_created=False
released_treatment_executed=False
tmp entries=('gate.md',)
RUN_TREE_UNCHANGED 10 True
```

这证明 readiness 绑定 latest exact decision 与当前 reviewed facts identities，不依赖
whole-file hash、旧结论编号或 runtime 现场学习。

### 11.4 独立测试与静态证据

Facts 专项：

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' -q tests/test_codex_facts_v1.py

65 passed
```

Facts + harness 专项：

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' -q \
  tests/test_codex_facts_v1.py tests/test_codex_x1_run.py

87 passed
```

联合相关固定超集：

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' -q \
  tests/test_codex*.py tests/test_real_source_adapters.py \
  tests/test_evaluation_governance.py tests/test_source_history_acl.py

318 passed, 1 existing Starlette/httpx deprecation warning
```

目标十文件静态检查：

```text
ruff check --no-cache       PASS
ruff format --check         10 files already formatted
in-memory compile           10/10 PASS
whitespace/diff audit       10/10 PASS
```

fixed-23 membership、Event 3 / Call 2 / Patch 4 / Validation 10 /
FalseValidation 5 / output-window 1 / error-tail 2 denominators、record-derived metrics、
security hard gates 与 portable contracts 没有发现 P0/P1 回退。

### 11.5 Scope boundary 与写锁审计

本轮没有执行 X-T1 treatment Run；没有创建 Run、metrics、artifact、output 或 DB。
`evals/codex/runs` 文件数始终为 10，与前轮快照一致。

正式库继续是 `EXTERNAL_MUTABLE_SERVICE_OWNED`：本轮没有打开、hash、checkpoint、
写入正式库或触碰其 WAL/SHM/sidecars。所有探针只使用纯内存、immutable fixtures
或自动清理 tmp；没有在仓库留下 probe 文件。

本轮没有修改任何实现、测试、config、Golden/evaluation authority、其他 docs、
fixtures、evals、正式库或 Git；唯一写入是向本报告追加第 11 节。共享 dirty main
保持原状，没有 branch/worktree、commit 或 push。

## 12. X-T1 唯一 Production Attempt Failed-Attempt Audit

审计日期：2026-07-29  
执行任务：`019fab59-24d7-7a20-8395-b2568dbb192a`  
审计范围：唯一 released production attempt 的失败阶段、持久边界、清理与授权消费

### 12.1 审计结论

`X-T1 PRODUCTION ATTEMPT AUDIT VERIFIED — FAILED_BEFORE_PUBLISH`

- `P0 findings: 0`
- `P1 findings: 0`
- `X-T1 TREATMENT: NO_RESULT / NOT_QUALIFIED`
- `X-T1 RETRY: NOT_AUTHORIZED`
- `X2: NOT_AUTHORIZED`
- `CX1-01 ENGINEERING: COMPLETE`
- `CX1-02 ENGINEERING: COMPLETE`

这里的 P0/P1=0 只表示本轮对失败边界、零持久结果、清理与单次授权消费的审计没有
发现不一致；它不表示 X-T1 treatment 通过，也不提供任何修复后的 released
treatment 质量证据。

X1 Gate 11.1 对唯一 attempt 的授权在执行时有效，绑定：

```text
Gate decision block:
sha256:573facfc03a99587c628c8ad3b5b7dad56ed9bbe96a583528d398b97df192431

Preparation:
sha256:c4cc777bd24271c7a57401811145f9cea3d6139f3d8cb39b1d8d8c70a4bc4792

Facts component set:
sha256:3630d5375f85d9bfd5ab156b00168d096d20ef9766f5460d3825993c122bbaf0
```

该授权已由唯一 production attempt 消费。第 11.1 节的 CX1-01/02 Engineering
PASS 不倒写为失败；但它不能再次授权 X-T1，也不能把 X2 推进为 authorized。

### 12.2 唯一 attempt 与真实失败阶段

独立读取执行任务的原始 session evidence，筛选同时包含 `execute=True` 与
`run_codex_x1_v1(request)` 的实际 exec call：

```text
PRODUCTION_EXECUTE_CALLS 1
timestamp 2026-07-29T03:33:37.737Z
call_id call_IINHxEOAnvDOwvkA5nNmzYlz
```

同一 call 在 `2026-07-29T03:33:39.205Z` 返回唯一 matching traceback：

```text
run_codex_x1_v1
  -> _treatment_records_from_results
  -> CodexX1LinkPrediction(call_id=link.call_id)

pydantic_core.ValidationError:
call_id
  Value error, identifier is not canonical
  input_value='adapter-command:archived-test'
```

纯内存合同复现确认冒号形式被拒绝、连字符形式合法：

```text
IDENTIFIER_REPRO True adapter-command-archived-test
```

当前 runner 的严格执行顺序与 traceback 对齐：

1. production `CodexSessionAdapter` 从 isolated released fixture 读取并校验
   `8 threads / 54 items`；
2. exact CX1 normalizer 对 8 个 thread source sets 执行；
3. isolated temporary SQLite fact store 对 8 个 publication 执行并验证 active
   publication；
4. `_treatment_records_from_results()` 开始把 normalized links 转换为严格
   per-case `CodexX1LinkPrediction`；
5. 只有 records 全部生成之后，才会调用
   `_build_codex_x1_production_artifact(stage, ...)`、portable-copy verify 和最终
   persistent copy。

traceback 发生在步骤 4 的第一个非法 direct-command call ID；步骤 5 未到达。
`persistent_started` 只会在 stage 与 portable copy 全部验证后、最终 copy 前变为
true，因此该 attempt 精确属于 `FAILED_BEFORE_PUBLISH`，不是 published Run failure。

### 12.3 零隐藏结果与清理审计

仓库持久树的独立复核：

```text
evals/codex/runs directories:
  6971b8cb1702a9ece8a894915c6b6de7

Run files:        10
Correction files: 10
new X-T1 Run directories: 0
X-T1 Run URI matches:     0
```

不存在新 Run ID、URI、artifact set hash、treatment records、metrics、slices、
security report、latency report 或 portable artifact。唯一目录仍是旧 X-B0 Run，
不是 X-T1 结果。

使用执行前完全相同的 `file sha256 + path + size + integer mtime` 聚合算法重新计算：

```text
old Run bytes+mtime:
1cc7e563daa49891cc6f9fd96d27db3a65e3320adb2e1233b3dc0c3de09e7a4a

correction bytes+mtime:
8b66e6d4c56433ff714b49e3ebeb41fbe4bf537828b115cb7bdc49bf1640c765
```

两值与 attempt 前及 attempt 后的原始 task snapshots 逐字一致。旧 Run 与
correction 的 bytes、size、path 和 mtime 均未变化。

清理复核：

```text
repository SQLite/DB/WAL/SHM/journal/pyc residues: 0
repository stage/verify-copy residues:            0
system codex-xt1-once-* roots:                    0
```

执行脚本外层只会在 isolated root 为空时删除该 root，原始输出没有
`ISOLATED_ROOT_NOT_EMPTY`；runner `finally` 同时删除 fixture copy、temporary
SQLite、WAL/SHM/journal、stage 与 portable copy。当前 filesystem scan 与该清理
路径一致。

production path 在 attempt 期间把 `socket.socket` 替换为 forbidden bomb，并把
`sqlite3.connect` 限制为 exact isolated `facts.sqlite3` 或 `:memory:`；runner
没有调用 retrieval。原始执行和收尾证据没有 network、retrieval 或 formal/default
SQLite access。正式库 `EXTERNAL_MUTABLE_SERVICE_OWNED` 未被打开、hash、
checkpoint 或触碰 sidecars。

### 12.4 Harness-only 修复与 post-fix 证据边界

原始 task 的 exact repair patch 只修改两个既有授权文件：

```text
src/evidence_rag/evaluation/codex_x1.py
  adapter-command:{item.item_id}
  -> adapter-command-{item.item_id}

tests/test_codex_x1_run.py
  synthetic adapter -> Observable -> CX1 -> facts -> tmp SQLite preflight
  additionally validates every normalized link with CodexX1LinkPrediction
```

当前代码位置为 `codex_x1.py:2940`；synthetic coverage 位于
`test_codex_x1_run.py:719-795`。未修改 CX1-01、CX1-02 facts、production adapter、
Golden、baseline/correction authority、正式库、runtime 或 X2。

本 Gate 独立重跑：

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider -o addopts='' -q tests/test_codex_x1_run.py

23 passed

ruff check --no-cache       PASS
ruff format --check         2 files already formatted
in-memory compile           2/2 PASS
whitespace/diff audit       2/2 PASS
```

测试文件不存在 `execute=True`；其中两次 `run_codex_x1_v1()` 都是零副作用 admission。
原始执行 task 在 repair 之后也没有第二个 production execute call。所以上述
23 PASS 只证明合同修复、synthetic CX1/facts/tmp-SQLite 路径与静态质量，不能冒充
released 23-case treatment evidence。

### 12.5 最终状态与写锁审计

最终状态固定为：

```text
CX1-01 Engineering: COMPLETE
CX1-02 Engineering: COMPLETE
X-T1 attempt:       FAILED_BEFORE_PUBLISH
X-T1 treatment:     NO_RESULT / NOT_QUALIFIED / UNAVAILABLE
X-T1 retry:         NOT_AUTHORIZED
X2:                 NOT_AUTHORIZED
```

没有 X-T1 Run、指标或 artifact 可用于 qualification；不得从 synthetic tests、
临时 facts publication、已修复源码或第 11.1 节的历史 authorization 推导 treatment
质量，也不得授权第二 attempt。

本轮没有执行 preparation、admission 或 runner，没有创建 Run/metrics/artifact/DB，
没有修改实现、测试、config、Golden/evaluation authority、其他 docs、fixtures、
evals、正式库或 Git。唯一写入是向本报告追加第 12 节；共享 dirty main 保持原状，
没有 branch/worktree、commit 或 push。
