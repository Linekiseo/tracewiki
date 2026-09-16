# Experiment E-B0 Baseline Harness Preparation 独立 Gate Review

Harness Gate 来源任务：`019fac2b-517a-79b3-8c1a-3a0625cd7ee5`

复审日期：2026-07-29  
复审范围：Experiment E-B0 baseline harness preparation；禁止执行 released45 baseline。  
工作方式：共享 dirty main；未建 branch/worktree，未 commit/push；只读实现、测试、
released fixture 与 Foundation review。唯一写入是本 review；未修改 11 review、实现、
测试、config、dependencies、其他 docs、evaluation、evals 或 fixtures。

正式库 `var/evidence-rag.sqlite3` 继续按 `EXTERNAL_MUTABLE_SERVICE_OWNED` 处理。本轮未
打开、hash、checkpoint、复制、删除或接触其 WAL/SHM；动态执行只使用 in-memory 与
system temp。未调用 `run_experiment_baseline_v1()` 的 authorized request 分支，未执行
released45，未创建或发布 Evaluation Run、official metrics、official artifact 或
qualification。

## 1. Gate 决定摘要

**FAIL。**

Harness preparation、smoke、portable artifact verifier、路径安全、current-V1 production
正向主路及固定回归均通过；但存在两个可独立复现的 P1：

1. canonical Harness Gate parser 无法绑定真实 Codex UUIDv7 来源任务，反而接受任意
   伪造 UUIDv4；Foundation/Harness 最新 section 中 final decision block 之外的重复
   exact decision token 也会被忽略并接受。
2. 21-component verifier 会接受在 import harness 前注入的同-code function clone；
   clone 可使用不同 `__globals__`，从而改变 `ExperimentService.compare` 或
   `PlatformStore.structured_search` 行为，同时继续命中固定 source/code/component
   digest。

这两项都影响 future isolated production Run 的 Gate authenticity/production evidence，
属于 P1 主流程阻塞，而不是 P2/P3、模块体量、smoke 不可用或正式库外部状态。

## 2. 冻结审查 identity

本轮实际核对的 Foundation/Harness authority：

```text
release: release-e7de1be4caf88af99c3b36de7e13c4a2
package: sha256:e7de1be4caf88af99c3b36de7e13c4a2ba3d15d4e40e5e5e0459ecf3d583a81f
authority: sha256:cad053ebb0686c6a413a743fe9d0e142f2d61546cb18a2bb0d2a9b82af8d29e1
recipe: sha256:4ff2eff2b2261f80f775d9ba6b8cce80e6b56972de459b50c1c198cc0d2fea97
foundation-component: sha256:0c961eb90c9a30795debc25d4d9c04eb6f38d4214a2ae3d71f03f54f812f405d
harness-component: sha256:4c776f72bfa1f383a71eb64e1804077c4d659d0f0624e53b91f5be0c741bb808
parity: sha256:df0d6a7f745bfda76bb4e68ce52063c0010ef474ee8afe584d4f20e2fd92ceb9
artifact: experiment-e-b0-portable-artifact-v1
```

Foundation ordered membership、slice、denominator 与 logical fixture 均重新核对：

```text
ordered cases = experiment-v1-001..experiment-v1-045
slices = 5/7/8/8/6/4/4/3
eligible denominators = 5/28/20/9/6/45/10/2
logical fixture = 1 Experiment / 12 Runs / 3 MetricDefinitions /
                  18 MetricSeries / 47 MetricObservations / 2 artifacts
```

## 3. P0/P1 findings

### P1-01 — Harness Gate task identity 与 decision-token uniqueness 未冻结

#### 最小复现 A：真实独立任务 identity 被拒绝，伪造 UUIDv4 被接受

当前 task metadata 返回：

```text
threadId = 019fac2b-517a-79b3-8c1a-3a0625cd7ee5
thread_source = subagent
UUID version nibble = 7
```

将该真实来源任务写入一份其余 identity/token 全部正确的 future Harness Gate text：

```python
parse_experiment_harness_gate_v1(text_with_real_task)
```

结果：

```text
ExperimentBaselineGateError:
Harness Gate task must be a canonical UUID
```

原因是 `_UUID_RE` 只允许 UUIDv1–v5：

```text
^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab]...
```

Codex 当前 task identity 与 Foundation task identity 均为 UUIDv7。相反，将来源任务改成
任意格式正确、与 Foundation 不同的伪造 UUIDv4：

```text
aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee
```

parser 返回有效 `ExperimentHarnessGateDecision`。实现没有固定 expected Harness task
identity，也没有外部 task provenance 对照，因此“真实 task 被拒绝、任意伪 task 被接受”。

#### 最小复现 B：latest section 中重复 exact decision token 被接受

在 Foundation latest PASS section 或 future Harness section 的 final fenced decision
block 之前，再加入一个相同 exact conclusion token；final block 本身保持完整。

结果：

```text
Foundation duplicate exact token outside final block: ACCEPTED
Harness duplicate exact token outside final block: ACCEPTED
```

`_require_exact_token()` 只检查 `_latest_exact_decision_block()` 的局部文本，不检查 latest
relevant top-level section 的全量 exact token membership。它能拒绝 final block 内的
重复项，但不能拒绝同一 authoritative section 中的重复/冲突 token。

#### 影响

- 本 review 无法同时做到“写真实来源 task identity”和“被 canonical parser 接受”。
- forged task identity 可被 canonical 12 review 冒充为独立审查来源。
- duplicated whole-section decision text 不能 fail closed。
- future `E-B0` authorization provenance 不可信，直接阻塞 production Run authorization。

#### 最小修复锁

仅需：

```text
src/evidence_rag/rag/sources/experiment/baseline_v1.py
tests/test_experiment_baseline_v1.py
```

最小要求：

1. 支持并严格校验 Codex UUIDv7；
2. 将 Harness decision 绑定到本独立 review 的 exact expected task identity
   `019fac2b-517a-79b3-8c1a-3a0625cd7ee5`，不能接受任意其他 UUID；
3. 对 latest relevant top-level section 的 pass/fail、P0、P1、authorization、E1 token
   做全 section 唯一性检查，而不只检查最后一个局部 block；
4. 保留 historical section 规则、canonical path/copy/symlink/identity/hash fail closed。

### P1-02 — pre-import same-code clone 可绕过 21-component identity

普通 identity 攻击表现正常：

```text
21 fresh pre-import wrapper/subclass replacements rejected = 21/21
21 fresh after-import replacements rejected = 21/21
after-import exact-code clone rejected
public export alias rejected
wrong fixed digest rejected
I/O/path/runtime/SQLite bombs called = 0
attack roots left empty = 42/42
```

但以下 fresh-process 攻击被接受：

```python
original = PlatformStore.structured_search
forged_globals = dict(original.__globals__)
forged_globals["len"] = malicious_len
clone = types.FunctionType(
    original.__code__,
    forged_globals,
    original.__name__,
    original.__defaults__,
    original.__closure__,
)
clone.__module__ = original.__module__
clone.__qualname__ = original.__qualname__
PlatformStore.structured_search = clone

import evidence_rag.rag.sources.experiment.baseline_v1 as harness
harness.verify_experiment_baseline_production_authority_v1()
```

独立结果：

```text
pre-import PlatformStore.structured_search same-code/different-globals: ACCEPTED
pre-import ExperimentService.compare same-code/different-globals: ACCEPTED
component digest:
sha256:4c776f72bfa1f383a71eb64e1804077c4d659d0f0624e53b91f5be0c741bb808
```

`_CANONICAL_CURRENT_V1_OBJECTS` 在 harness import 时从已经被替换的 runtime export 学习
object identity；source digest 和 runtime code digest 不包含 function global binding。
因此 clone 可保持相同 code/source/module/qualname，同时把 `compare` 使用的 `uuid4` 或
structured search 使用的 global/builtin binding 指向不同对象。

#### 影响

- fixed21 authority 不能证明实际执行的是 reviewed production behavior。
- smoke/runner/artifact 可记录正确 component aggregate，但生产 comparison/search 已被
  pre-import injection 改写。
- artifact 中的同一 authority 只是复制了未识别该替换的固定声明。

#### 最小修复锁

至少：

```text
src/evidence_rag/rag/sources/experiment/baseline_v1.py
tests/test_experiment_baseline_v1.py
```

最小要求：

1. runtime method identity 不得从 harness import 时的可变 export 现场学习；
2. function identity 至少绑定 `__globals__` 的 canonical module dictionary identity，并
   固定所有实际引用的 global/helper binding；不同 globals 的 same-code clone 必须失败；
3. 增加 fresh pre-import same-code/different-globals probes，覆盖
   `ExperimentService.compare`、`PlatformStore.structured_search` 及 Foundation 七组件；
4. authority failure 必须继续位于 path/temp/SQLite/output 前。

## 4. 通过但不覆盖 P1 的证据

### 4.1 Foundation Gate 与 preparation

- canonical 11 review 的 latest complete Foundation block、source task、release/package/
  authority/recipe/component/parity 均成功解析。
- wrong release/component、P0/P1 伪造、substring、final-block duplicate、wrong task、
  latest FAIL、copy 和 symlink 均被拒绝。
- `prepare_experiment_baseline_v1()` 零副作用返回：

```text
status = PREPARED
qualification_status = NON_QUALIFIED
foundation_gate = PREPARATION_AUTHORIZED
execution_status = NOT_AUTHORIZED
released_baseline_executed = false
Run/metrics/artifact/qualification created = false
```

- missing/Foundation PASS/preparation token/伪 Harness token 四类 token-only runner 调用都在
 读取 path-like bomb 前得到 `NOT_AUTHORIZED`。

### 4.2 current-V1 smoke

在 canonical system-temp child 中独立运行一次允许的非 released smoke：

```text
MLflow FileStore -> ExperimentService.sync_mlflow Runs = 12
Manual public models/service Runs = 12
Platform source = experiment only
Platform search candidates = 8
entity kinds = ExperimentRun / Metric only
matched terms include treatment / seed / 11 / ndcg
ExperimentService.compare controls =
  dataset_id / dataset_version / branch / environment
comparison config differences = 1
comparison metric rows = 2
logical counts = 1/12/3/18/47/2
```

结果仍严格为：

```text
SMOKE_UNAVAILABLE
NON_QUALIFIED
production execution = NOT_AUTHORIZED
released45 executed = false
official Run/metrics/artifact = false
```

将 smoke status 改成 available 不能通过 frozen model validation。temp 中只出现两个隔离
smoke SQLite；没有十文件 official artifact member，temp context 结束后清理。

### 4.3 future runner 数据流只读审查

未执行 runner。源码数据流为：

1. canonical Foundation + future Harness Gate；
2. exact Golden/recipe/path/21-component authority；
3. FileStore -> `sync_mlflow()` 与 Manual public service；
4. 每个 released case 的 `question` 进入 current-V1 `PlatformService.search()`，source 固定
   为 `experiment`；
5. comparison/incomparable 输入调用 `ExperimentService.compare()`；
6. production result 映射为 logical candidate order/locator/matched terms/raw fields、
   comparison/error/unavailable/latency；
7. `ExperimentBaselinePrediction.reviewed_row()` 交给 E0 evaluator，按 frozen
   membership/denominator 重算。

runner 没有调用 perfect-row helper，也没有把 expected entities、metric truth、
comparability truth 或 reproduction truth 写入 prediction。Golden 仅提供 query/case
identity、task routing 与 evaluator authority。

### 4.4 exact10 portable artifact 与 offline verifier

test-only artifact fixture 仅在 system temp 构建，没有发布或保留。exact membership：

```text
manifest.json
golden_cases.jsonl
predictions.jsonl
comparison_results.jsonl
metrics.json
slice_report.json
errors.jsonl
latency.json
security_report.json
checksums.json
```

深层 portable copy verification：

```text
status = VERIFIED_NON_QUALIFIED
case_count = 45
service calls = 0
database calls = 0
network calls = 0
adapter/service/platform/runtime/sqlite bombs called = 0
```

以下同步刷新 checksum/run identity 的攻击均被拒绝：

- fake denominator/metric；
- missing prediction membership；
- rogue candidate outside released authority；
- 45 个 zero-result predictions + 旧伪 metrics；
- secret credential；
- NaN latency。

专项测试另覆盖 extra/missing/symlink、formal DB/WAL/SHM/pyc、outside/reuse/existing
output、absolute/temp/encoded path、email/payment/credential、cross-file identity、
component identity、error ledger、latency candidate count 和 portable copy。

### 4.5 compatibility/no-wiring

- `evals/experiment` 不存在。
- 没有 E-B0 Evaluation Run、official metrics、official artifact 或 qualification。
- E1 仍为 `NOT_AUTHORIZED`。
- 未观察到 Experiment/E0/Platform/API/ACL/governance production wiring 或兼容回归。

## 5. 独立命令与回归证据

专项：

```bash
uv run pytest -q tests/test_experiment_baseline_v1.py
```

结果：`73 passed`。

独立主相关固定集合：

```text
Experiment E-B0/E0/API/adapter/platform/ACL = 133 passed
evaluation governance = 2 passed
total = 135 passed
```

静态检查：

```text
ruff check = PASS
ruff format --check = PASS (3 files already formatted)
git diff --check = PASS
3-file compileall with system-temp PYTHONPYCACHEPREFIX = PASS
```

独立 `uv run python - <<'PY'` probes：

1. Foundation/Harness Gate parser tamper/task/copy/symlink/token matrix；
2. 21 roles × fresh pre/after-import replacement matrix；
3. pre/after same-code clone、public alias、wrong digest；
4. current-V1 real temp smoke；
5. exact10 portable verify-only 与 production/DB bombs；
6. re-signed membership/denominator/zero45/rogue/security/nonfinite attacks；
7. preparation/no-Run/no-artifact/no-qualification assertions。

## 6. Gate matrix

| Gate criterion | 结果 | 说明 |
| --- | --- | --- |
| 1. exact Foundation latest Gate/source/hash | FAIL | 正常主路通过；whole-section duplicate exact token 被接受 |
| 2. future canonical Harness Gate/task identity | **FAIL** | P1-01：真实 UUIDv7 被拒，任意伪 UUIDv4 被接受 |
| 3. fixed21 current-V1 production identity | **FAIL** | P1-02：pre-import same-code/different-globals clone 被接受 |
| 4. released45 runner production data flow | PASS（只读） | current V1 search/compare -> predictions -> E0 evaluator；未执行 |
| 5. isolated smoke/nonqualification/zero publication | PASS | 真实 temp smoke；仍 SMOKE_UNAVAILABLE/NON_QUALIFIED |
| 6. exact10/offline verifier/portable copy | PASS | 十文件、重算与 bombs 通过 |
| 7. path/security/nonfinite/cross-file/cleanup | PASS | 专项与独立重签 probes fail closed |
| 8. no released45/no E1/no wiring/compatibility | PASS | 135 tests；`evals/experiment` absent |

## 7. 最终状态

本 Gate 只因上述两个 P1 主流程缺口失败；不因 smoke 不可用、P2/P3、dirty main 或正式库
外部状态失败。在 P1-01/P1-02 关闭并由新的独立复审确认前，不得调用 released45 runner，
不得创建 Evaluation Run、official metrics/artifact/qualification，也不得授权 E1。

```text
EXPERIMENT E-B0 HARNESS FAIL
P0 findings: 0
P1 findings: 2
E-B0 ISOLATED PRODUCTION RUN NOT AUTHORIZED
E-B0 Run/metrics/artifact/qualification: NONE
E-B0 production Run executed: false
E1: NOT_AUTHORIZED
```

---

# Experiment E-B0 Harness 第二轮极简复审：首轮 2 P1 关闭

Harness Gate 来源任务：`019fac2b-517a-79b3-8c1a-3a0625cd7ee5`

复审日期：2026-07-29  
复审范围：仅首轮 P1-01、P1-02 关闭与 E-B0 Harness 主路不回归。  
执行约束：共享 dirty main；未建 branch/worktree，未 commit/push；未执行 released45，
未创建或发布 Run、metrics、artifact、qualification；未访问
`var/evidence-rag.sqlite3` 或其 sidecars。所有动态验证仅使用 in-memory/system temp、
released fixture、test-only artifact 与只读代码审查。

## 8. 第二轮结论摘要

首轮两个主流程缺口均已关闭：

- **P1-01（Gate task identity 与 latest-section 唯一性）关闭。**
  Harness parser 现在严格校验 UUIDv7，并硬绑定本节来源任务；当前首轮 canonical section
  在追加本节前精确解析为 FAIL、0 个 P0、2 个 P1、未授权。真实来源 UUIDv7 的 synthetic
  下一节可进入 PASS 分支；伪 UUIDv4、其他 UUIDv7、Foundation/self task、copy、
  symlink、duplicate heading、历史拼接，以及 final block 外重复/冲突的 conclusion、
  findings、authorization、E1 或 identity 均 fail closed。
- **P1-02（same-code/different-globals authority）关闭。**
  current-V1 production authority 已升级为 v2，固定 21 个 component，并绑定 canonical
  module globals mapping identity、builtins、closure 和实际 `co_names` 引用的 global/helper，
  同时保留 exact module/export/public object/qualname/source/runtime code/default/closure
  约束。fresh subprocess 对 `ExperimentService.compare()`、
  `PlatformStore.structured_search()` 与 Foundation 7 的 different-globals、copied-builtins、
  helper、`uuid4`、`len`、wrapper/subclass/alias 攻击全部在路径、temp、SQLite、output
  之前被拒绝；独立矩阵 12/12 rejected，I/O bombs 为 0，攻击 root 为空。

本轮没有新增阻塞级发现。P2/P3 未扩展，且不用于阻塞本 Gate。

## 9. 主路不回归证据

### 9.1 Foundation、authority 与 preparation

- released Golden 仍为 ordered exact45；slice counts 为 `5/7/8/8/6/4/4/3`。
- frozen eligible denominators 仍为 `5/28/20/9/6/45/10/2`。
- logical fixture counts 仍为 `1 Experiment / 12 Runs / 3 definitions / 18 series /
  47 observations / 2 artifacts`。
- 21 个 official production components 全部通过 v2 runtime binding 校验；aggregate
  与本节 canonical identity 一致，Foundation component identity 未漂移。
- `prepare_experiment_baseline_v1()` 返回
  `PREPARED / NON_QUALIFIED / NOT_AUTHORIZED`；released execution、Evaluation Run、
  metrics、artifact、qualification 与 E1 authorization 均保持 false/none。
- 伪造的历史 PASS/authorization 文本作为 gate token 时，在任何 path-like 值求值前
  得到 `NOT_AUTHORIZED`。

### 9.2 current-V1 smoke

允许的 system-temp smoke 真实经过 MLflow FileStore ->
`ExperimentService.sync_mlflow()`、Manual public models/service、Experiment-only
Platform search 与 `ExperimentService.compare()`：

```text
logical counts = 1/12/3/18/47/2
MLflow synced Runs = 12
Manual created Runs = 12
comparison metric rows = 2
status = SMOKE_UNAVAILABLE
qualification = NON_QUALIFIED
production execution = NOT_AUTHORIZED
released45 executed = false
official artifact members = 0
```

smoke 只在 system temp 创建隔离数据并随 context 清理；未把 smoke 冒充 released
结果，未生成 official metrics、qualification 或 exact10 artifact。

### 9.3 future dataflow、exact10 verifier、path/security

未调用 future released runner。只读审查与专项回归再次确认：future runner 的
predictions/comparison 来自 exact current-V1 production search/compare 结果，保留
candidate order、locator、matched terms、raw structured fields、error/unavailable 与
latency；E0 evaluator 从 predictions 与 observation authority 重建指标并使用 frozen
membership/denominators，不从 Golden labels 伪造 prediction。

exact10 合同未变：

```text
manifest.json
golden_cases.jsonl
predictions.jsonl
comparison_results.jsonl
metrics.json
slice_report.json
errors.jsonl
latency.json
security_report.json
checksums.json
```

portable copy 的 verify-only 路径保持零 adapter/service/platform/runtime/SQLite/network
调用。专项复验覆盖 extra/missing/symlink、formal DB/WAL/SHM/pyc、outside/reuse/existing
output、raw/encoded absolute/temp 路径、secret/email/credential/payment、NaN/Inf、
cross-file identity，以及重新签名后的 denominator/member/metric/checksum、zero/rogue45、
smoke AVAILABLE 篡改；均 fail closed。

### 9.4 独立命令证据

```bash
uv run pytest -q \
  tests/test_experiment_baseline_v1.py \
  tests/test_experiment_foundation.py \
  tests/test_experiment_golden.py \
  tests/test_experiment_evaluation_v1.py \
  tests/test_experiments.py \
  tests/test_real_source_adapters.py \
  tests/test_platform.py \
  tests/test_source_history_acl.py \
  tests/test_api.py \
  tests/test_evaluation_governance.py
```

结果：`166 passed`。其中 E-B0 专项参数化集合为 104 项；Gate/parser、identity、smoke、
exact10 verifier、path/security、Experiment/E0/API/adapter/platform/ACL/governance
相关集合无回归。

静态与仓库状态检查：

```text
ruff check = PASS
ruff format --check = PASS (3 files already formatted)
git diff --check = PASS
3-file py_compile with system-temp outputs = PASS
evals/experiment = absent
```

## 10. Canonical identities

release:   release-e7de1be4caf88af99c3b36de7e13c4a2
package:   sha256:e7de1be4caf88af99c3b36de7e13c4a2ba3d15d4e40e5e5e0459ecf3d583a81f
authority: sha256:cad053ebb0686c6a413a743fe9d0e142f2d61546cb18a2bb0d2a9b82af8d29e1
recipe:    sha256:4ff2eff2b2261f80f775d9ba6b8cce80e6b56972de459b50c1c198cc0d2fea97
foundation-component: sha256:0c961eb90c9a30795debc25d4d9c04eb6f38d4214a2ae3d71f03f54f812f405d
harness-component: sha256:689b77964fb99fc72f7f93631eba0063dab8af4ab8e0d121364ee0df7fd5f845
parity:    sha256:df0d6a7f745bfda76bb4e68ce52063c0010ef474ee8afe584d4f20e2fd92ceb9
artifact:  experiment-e-b0-portable-artifact-v1

## 11. 最终状态

本结论只授权下一步隔离 production run 的能力；它不声称 production Run 已执行，
不构成 qualification，也不授权 E1。当前仍无 E-B0 Run、metrics、artifact 或
qualification。

```text
EXPERIMENT E-B0 HARNESS PASS
P0 findings: 0
P1 findings: 0
E-B0 ISOLATED PRODUCTION RUN AUTHORIZED
E-B0 Run/metrics/artifact/qualification: NONE
E-B0 production Run executed: false
E1: NOT_AUTHORIZED
```
