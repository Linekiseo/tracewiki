# Experiment E0-01 Golden/Evaluation Foundation 独立 Gate Review

日期：2026-07-29  
Gate 来源任务：`019f9f27-8d88-7b01-b686-3d4acbc5dcf2`  
评审范围：Experiment E0-01 Golden/Evaluation Foundation；不执行 E-B0  
工作区：共享 dirty `main`，无 branch/worktree、无 commit/push  
正式库边界：`EXTERNAL_MUTABLE_SERVICE_OWNED`

## 1. Gate 结论

`EXPERIMENT E0-01 FOUNDATION FAIL`

- `P0 findings: 0`
- `P1 findings: 3`
- `E-B0 HARNESS PREPARATION NOT AUTHORIZED`
- 当前没有 E-B0 Evaluation Run、baseline/treatment identity、metrics、artifact 或
  qualification。
- E1 仍为 `NOT_AUTHORIZED / NOT_STARTED`；E2–E5 同样未实现、未授权。
- `experiment-golden-v1` 的 content-addressed package、exact 45 membership、八个 slice、
  frozen denominators 和安全 verifier 本身通过独立验证。
- 但 production component identity、reviewed candidate 与 case truth 的绑定，以及
  MetricDefinition/series/per-point Observation identity 仍存在 P1 主流程缺口，E-B0
  artifact 目前不能成为可信固定基线。

38 项固定测试、16 项兼容回归及 ruff/format/diff/compile 均通过。Foundation Gate 仍失败，
原因不是 P2/P3、模块体量、正式库外部状态或低质量结果，而是三个可最小复现的 E-B0
可信性缺陷。

## 2. 审查边界与证据方法

- 只读审查 E0 implementation/tests/released fixture、旧 Experiment API/adapter/service
  和治理文档。
- 所有执行型 probes 仅使用 Python in-memory、pytest `tmp_path` 或 system temp；生成的
  SQLite 仅位于 system temp。
- 未打开、hash、checkpoint、复制或删除
  `var/evidence-rag.sqlite3` 及其 sidecars；没有命令引用该路径。
- 未创建或修改 `evals/experiment`；审查结束时该目录仍不存在。
- 未改实现、测试、config、dependencies、fixtures、其他 docs、evaluation、evals 或 Git。
- `review-research-evidence` 要求的 research workbench context/evidence API
  （`project_get_context`、`research_get_work`、`evidence_search`、`execution_get`、
  `review_list_pending`）经工具发现与全工具枚举均不可调用。因此本 Gate 使用仓库内可定位
  source、released package 和独立 probes；没有把缺失的 workbench approval 当作技术
  PASS。
- 本报告是工程 Gate 判定，不替代最终科学验收或人类 release approval。

## 3. P1 findings

### P1-01 — Production component identity 可被 subclass/wrapper/model export 替换，结果仍自报官方组件

`_mlflow_lane()` 只检查 runtime service instance 的 class 是当前绑定的
`ExperimentService`（`fixture_v1.py:1003-1004`）。随后直接实例化模块全局
`MLflowAdapter` 并调用 runtime 的 `sync_mlflow()`（`fixture_v1.py:1005-1019`），但没有
冻结或验证：

- adapter public export/class 的 exact identity；
- `ExperimentService.sync_mlflow` 的 method identity/code digest；
- `ExperimentCreate`、`RunCreate` public model identity；
- `create_experiment`、`create_run` method identity/code digest。

Manual lane 同样只检查 service instance class（`fixture_v1.py:1050-1054`），然后消费可替换
的 model/method bindings（`fixture_v1.py:1055-1127`）。

更严重的是，两条 lane 返回的 `production_components` 只是固定字符串
（`fixture_v1.py:1025-1030,1128-1135`），不是由已验证 runtime objects 派生的 identity
authority。

独立 system-temp 子进程 probe 同时完成以下替换：

```python
class FakeAdapterSubclass(OriginalAdapter):
    pass

class FakeExperimentCreateSubclass(OriginalExperimentCreate):
    pass

class FakeRunCreateSubclass(OriginalRunCreate):
    pass

def fake_sync_wrapper(self, request):
    return OriginalSync(self, request)

def fake_create_experiment_wrapper(self, request):
    return OriginalCreateExperiment(self, request)

def fake_create_run_wrapper(self, request):
    return OriginalCreateRun(self, request)

adapter_module.MLflowAdapter = FakeAdapterSubclass
service_module.MLflowAdapter = FakeAdapterSubclass
fixture.MLflowAdapter = FakeAdapterSubclass
fixture.ExperimentCreate = FakeExperimentCreateSubclass
fixture.RunCreate = FakeRunCreateSubclass
ExperimentService.sync_mlflow = fake_sync_wrapper
ExperimentService.create_experiment = fake_create_experiment_wrapper
ExperimentService.create_run = fake_create_run_wrapper

result = build_production_lane_parity(system_temp_root)
```

实际结果：

```text
attack_passed = true
parity_digest =
  sha256:fa6802b06129e5073e186dc1404d444634d1d69985080caddd44a935ee931c69

actual_adapter = __main__.FakeAdapterSubclass
actual_sync = __main__.fake_sync_wrapper
actual_experiment_model = __main__.FakeExperimentCreateSubclass
actual_run_model = __main__.FakeRunCreateSubclass
actual_create_experiment = __main__.fake_create_experiment_wrapper
actual_create_run = __main__.fake_create_run_wrapper

declared_mlflow_components =
  evidence_rag.experiments.adapters.mlflow.MLflowAdapter
  evidence_rag.experiments.service.ExperimentService.sync_mlflow
```

因此 declared parity hash 只证明替换后的对象产生相同 normalized output，不能证明
“exact production `MLflowAdapter` → exact `ExperimentService.sync_mlflow()` + exact Manual
public models/service”被调用。wrapper 目前委托了原实现，但同一个缺口允许替换逻辑产生
预构造 store output；literal component strings 仍会宣称官方组件。

影响：

- E-B0 无法把该 parity evidence 用作 fixed production control；
- fake/subclass/wrapper/pre-import injection/public export replacement 无法被 verifier
  区分；
- 直接阻塞 E-B0 harness authorization。

最小修复锁：

- `src/evidence_rag/rag/sources/experiment/fixture_v1.py`
- `tests/test_experiment_foundation.py`

最小修复要求：

1. 在任何 temp directory、adapter、service 或 SQLite I/O 之前验证固定 production
   authority；
2. 对 adapter class、service methods、Manual models/methods冻结
   module/export/qualname/source digest/runtime code digest/component-set digest；
3. 检查 runtime/bound objects 与 public exports 都是 exact object，不接受 subclass、
   wrapper 或替换 alias；
4. parity artifact 记录验证后的 structured component identities，而不是 literal names；
5. 增加 after-import monkeypatch、pre-import injection、subclass、wrapper、public export
   replacement probes，并断言失败发生在创建任何 SQLite 之前。

可复用既有 Codex fixed production authority 的实现模式；无需修改 production
Experiment adapter/service/model。

### P1-02 — Reviewed candidates 未绑定 case positives；42 个返回行全部替换为错误候选时七类指标仍满分

`_validate_reviewed_rows()` 只验证候选属于整个 dataset 的全局 authority、locator 与该
全局 entity 一致、ACL 与 case 一致（`evaluation_v1.py:2246-2270`）。它没有验证候选属于
当前 case 的：

- `expected_entities`；
- `acceptable_alternative_ids`；
- task-specific allowed candidate membership。

`_case_correctness()` 对 exact 仅使用：

```python
bool(expected_ids) and expected_ids <= candidate_ids
```

（`evaluation_v1.py:2291-2292`）。因此 expected entity 之外的错误 candidate 不影响
exact，只要该 candidate 不是当前 case 唯一枚举的 hard negative。

对 predicate/numeric/comparability/reproduction，代码只比较 reviewed row 的
`observed_*` 与 released truth（`evaluation_v1.py:2293-2301`），完全不要求返回的
candidates 包含当前 case positives。Hard-negative avoidance 只排除当前 case 的
`forbidden_candidate_ids`（`evaluation_v1.py:2302-2303`）；其他已知错误 entity 可以
自由进入。

#### 最小复现 A：exact case 接受额外错误 entity

对 `experiment-v1-002`（exact Run）保留正确 Run，再加入同 ACL 的 Experiment entity；
该 Experiment 既不是 expected，也不是本 case 唯一 hard negative：

```text
extra_id = experiment://fixture/experiment-v1-main
before exact_entity_accuracy = 5/5
after  exact_entity_accuracy = 5/5
```

#### 最小复现 B：全部 returned rows 改成已知错误 candidate

对 42 个 `outcome=returned` rows：

1. 保留 reviewed row 自带的 `observed_predicate/metric/comparability/reproduction`；
2. 删除所有当前 case expected candidates；
3. 选择一个属于全局 authority、同 ACL，但既不是当前 expected、也不是当前 hard
   negative 的错误 entity 作为唯一 candidate。

Evaluator 接受全部 rows，结果为：

```text
returned_rows_replaced_with_known_wrong_candidate = 42

exact_entity_accuracy                 0/5
predicate_accuracy                   28/28
numeric_unit_direction_accuracy      20/20
comparability_accuracy                9/9
reproduction_accuracy                 6/6
hard_negative_avoidance              45/45
unanswerable_accuracy                10/10
zero_result_accuracy                  2/2
```

这不是“答案正确但检索 citation 不同”的正常 alternative：选入的 entity 明确不在
`expected_entities` 或 `acceptable_alternative_ids`。特别是 structured-filter cases
可以返回完全错误的 Run/Experiment，仍同时获得 predicate 与 hard-negative 满分。

影响：

- E-B0 可用错误 candidates 产生 7/8 类满分或看似健康的 slice；
- hard-negative denominator 只证明避开一个枚举 decoy，不证明命中 positive；
- exact accuracy 也不是 exact candidate membership；
- 后续 baseline/treatment 质量比较不可信。

最小修复锁：

- `src/evidence_rag/rag/sources/experiment/evaluation_v1.py`
- `tests/test_experiment_evaluation_v1.py`

最小修复要求：

1. 从 released case authority 计算 case-specific allowed candidate set；
2. exact 至少要求 `expected ⊆ candidates ⊆ expected ∪ acceptable_alternatives`，并按最终
   metric contract 冻结 rank/order 规则；
3. answerable/insufficient-metadata task metrics 必须绑定 positive candidate correctness，
   或增加覆盖全部 applicable cases 的 frozen positive-retrieval metric；不得仅靠
   `observed_*` echo 获得满分；
4. 未枚举为 hard negative 的已知错误 entity 也不能被当作正确 candidate；
5. 增加 extra false positive、all-positive-removed、wrong entity kind、correct truth +
   wrong candidates、alternative membership/rank 等对抗测试；
6. 不改变既有 frozen denominator，除非重新 release 并明确变更 identity。

### P1-03 — “47 metric observations”只有计数；缺 MetricDefinition、series 和 per-point Observation identities/locators

实施声明和 recipe `logical_counts` 报告：

```text
metric_definitions = 3
metric_series = 18
metric_observations = 47
```

独立读取 released recipe 后，实际 schema 为：

- `EntityKind` 只有 `experiment/run/metric_observation/artifact`
  （`evaluation_v1.py:207-212`），没有 MetricDefinition 或 MetricSeries；
- `FixtureMetric` 每个 series 只有一个 `observation_id`
  （`fixture_v1.py:164-187`）；
- `FixtureMetricPoint` 只有 `observed_at_ms/value/step`
  （`fixture_v1.py:151-162`），没有自己的 Observation ID 或 locator；
- `MetricTruth` 只有一个 series-level `observation_id` 和裸 `series: tuple[float]`
  （`evaluation_v1.py:401-438`），没有每个 point 的 identity/locator/time truth；
- metric entity locator 只指向 latest/final step
  （`evaluation_v1.py:607-626`）。

独立结构 probe：

```text
distinct metric names                  = 3
series records                         = 18
series-level observation IDs           = 18
point records counted as observations  = 47

point identity fields =
  observed_at_ms, step, value

entity kinds =
  experiment, run, metric_observation, artifact
```

因此“3 definitions / 18 series / 47 observations”中的后两组没有各自完整 stable
identity；47 是 `sum(len(metric.points))` 的派生计数，不是 47 个可定位 Observation
authorities。Golden cases 也无法冻结设计/本 Gate 要求的 exact
MetricDefinition/MetricSeries/per-point Observation IDs 和 locators。

影响：

- aggregation/trend truth 能比较裸 values，却不能逐点证明 observation provenance、
  timestamp 或 locator；
- series-level final observation ID 被用来代表整条 time series，无法区分 47 个
  observations；
- E-B0 的 time-series/metric evidence 不能成为后续 E1 immutable sample foundation 的
  可重放 authority；
- package hash 虽能防当前 bytes 被改写，不能补充 schema 中不存在的 identity truth。

最小修复锁：

- `src/evidence_rag/rag/sources/experiment/fixture_v1.py`
- `src/evidence_rag/rag/sources/experiment/evaluation_v1.py`
- `tests/fixtures/experiment_golden/`
- `tests/test_experiment_foundation.py`
- `tests/test_experiment_golden.py`
- `tests/test_experiment_evaluation_v1.py`

最小修复要求：

1. 为 3 个 MetricDefinition、18 个 MetricSeries、47 个 MetricObservation 建立 canonical
   stable IDs；
2. 每个 observation 冻结 run/definition/series、unit、direction、split、step、time、
   value 和 logical locator；
3. cases/MetricTruth 中明确引用需要的 definition/series/observation authorities；
4. loader 校验 cross-run/cross-series/cross-definition、step/time/order 和 locator
   consistency；
5. evaluator 从这些 released identities 重算 time-series/aggregation truth；
6. 重新生成 exact package、release ID、package hash 和 authority hash，并重新 Gate；
   不得沿用当前 hashes。

## 4. P2/P3 非阻塞记录

### P2-01 — 现有 security mutation test 未真正进入 scanner 分支

`test_loader_fails_closed_for_package_mutations` 对 path/secret/NaN 直接改
`cases.jsonl`，但未同步 manifest file digest/size。Loader 会先在
`evaluation_v1.py:1941-1943` 因 digest/size mismatch 退出，测试不能证明后续
`_parse_json_bytes()` / `_security_scan()` 的行为。

本次独立 probe 在每次 mutation 后更新 declared digest/size，使 loader 真正进入 parser
和 scanner；raw/encoded POSIX、Windows、UNC、`file://`、secret、email、payment、
NaN/+Inf/-Inf 均正确 REJECT。因此这是专项测试覆盖质量问题，不是当前 runtime P1。

最小后续锁：`tests/test_experiment_golden.py`。

### P2-02 — 开发计划与 interview current-state 已版本漂移

`03_EXPERIMENT_SOURCE_DEVELOPMENT_PLAN.md` 和
`03_EXPERIMENT_SOURCE_INTERVIEW.md` 仍写“尚无 released Golden/package hash、E0-01
IN_PROGRESS”。当前工作区已经存在可验证的 package/release identities。

由于本 Gate 为 FAIL，不能把文档更新为 Foundation PASS，也不能授权 E-B0。本轮唯一写锁
不允许改其他 docs，故仅记录为 version-mismatched/stale evidence。

### P3-01 — 兼容回归存在一项非本 Gate deprecation warning

FastAPI/TestClient 回归产生 `StarletteDeprecationWarning`，提示未来安装 `httpx2`。所有
相关测试通过；该 warning 与 E0 Foundation 主流程无关。

## 5. 独立通过项

### 5.1 Released exact 45、manifest 与 content authority

独立 loader/recompute 得到：

```text
dataset_id =
  experiment-golden-v1
release_record_id =
  release-09873f5a3b7781a40be3bd34884783fe
package_hash =
  sha256:09873f5a3b7781a40be3bd34884783fe9193f6b172b15aefb33cdc7f336e865b
authority_hash =
  sha256:b41188e1b92c724b6cfcd4122ee8e4f31c1eb20ca9d487f92bf2495c70548737
case_count = 45
```

Canonical membership 精确为 `experiment-v1-001` 至 `experiment-v1-045`，顺序固定。

八个 slice：

```text
exact                              5
structured_filter                  7
metric_numeric                     8
comparison                         8
reproduction                       6
failure_status                     4
aggregation_trend                  4
unanswerable_incomparable_acl      3
```

Manifest 只声明：

```text
cases.jsonl
fixture_recipe.json
```

加 `manifest.json` 后 released package 共 3 个文件。file digests/sizes、case membership
digest、hard-negative membership digest、recipe digest、release ID 和 package identity
相互一致。

两次 clean system-temp rebuild 逐字节一致；深层 relocated portable copy verify 得到同一
package hash。

### 5.2 Package/label/denominator/security fail-closed

独立 probes 通过：

- tamper、missing、undeclared extra、manifest/file digest/size mismatch；
- case reorder、delete/duplicate/unknown membership；
- unknown label、cross-run locator、cross-experiment/ACL model leakage；
- per-metric eligible denominator 删除、hard-negative membership mismatch；
- same-content declared-file symlink；
- benign case 内容重签与 recipe 内容重签；
- raw/多层 encoded POSIX/temp path、Windows drive、UNC、`file://`；
- credential/secret、email、payment-like identifier；
- JSON NaN、+Infinity、-Infinity。

完全重签的 benign case tamper 产生新 hash：

```text
sha256:69b16973b85651d35497cf6cc2115c7b11ae28c67535c8e889afb70facec94be
```

Loader 在 authority pin 处拒绝。完全重签的 recipe tamper 产生：

```text
sha256:751608cad86be69824c05e593516d281e27c10f889bb2584cb2513de39d0faf1
```

Loader 在 frozen package pin 处拒绝。当前 package 不能通过“修改内容 + 更新 manifest +
重签 package”绕过 frozen authority。

### 5.3 Strict contracts 与 evaluator frozen denominator

Pydantic contracts 均为 `extra="forbid" / frozen / validate_default /
allow_inf_nan=False`，并使用 strict scalar types。

Row 注入以下 authority fields 均被拒绝：

```text
truth
status
numerator
denominator
eligible_metrics
```

显式 membership 的 empty/delete/reorder/duplicate/unknown 均抛
`ExperimentEvaluationError`。

从 released eligible case IDs 重算 overall denominators：

```text
exact_entity_accuracy                 5
predicate_accuracy                   28
numeric_unit_direction_accuracy      20
comparability_accuracy                9
reproduction_accuracy                 6
hard_negative_avoidance              45
unanswerable_accuracy                10
zero_result_accuracy                  2
```

各 slice total 精确为 `5/7/8/8/6/4/4/3`。

以下 authority distinctions 均正确扣分而不改变 denominator：

```text
unknown direction -> higher             numeric 20/20 -> 19/20
missing -> numeric zero                  numeric 20/20 -> 19/20
percent -> ratio                         numeric 20/20 -> 19/20
not_comparable -> insufficient mismatch  comparison 9/9 -> 8/9
URI-only -> verified artifact            reproduction 6/6 -> 5/6
failed high score candidate              hard-negative 45/45 -> 44/45
```

`unsupported_v1`、`system_error`、`zero_result`、`unavailable` 和 available counts 保持
独立状态；P1-02 仅涉及 candidates 与 positives 的绑定，不否定这些已通过的状态区分。

### 5.4 Programmatic recipe 正向事实

Released recipe 独立读取结果：

```text
Experiment                         1
Runs                              12
Metric definitions                3
Metric series                     18
Point records                     47
Artifacts                          2

baseline seeds                11/22/33
treatment seeds               11/22/33
completed/failed/running       10/1/1
worker value types             int/string
units                          ms/percent/ratio/score
directions                     higher/lower/unknown
series lengths                 1x3, 2x1, 3x14
artifact states                verified_checksum/located_unverified
```

不同 dataset、percent same-name metric、failed high score、running、missing
commit/environment、string `"1"` vs numeric `1`、time series 与两种 artifact state 都
存在。12 个 logical/external Run IDs 唯一；started times 和 seeds 固定。Released package
不含 host absolute path、secret、email、credential 或 payment data。

该正向内容覆盖通过；P1-03 只指出 47 个 point records 没有各自 stable observation
identity/locator。

### 5.5 未替换时的 production lane 正向结果

使用未 monkeypatch 的工作区代码和 system temp 独立执行：

```text
parity = true
differences = []
parity_digest =
  sha256:fa6802b06129e5073e186dc1404d444634d1d69985080caddd44a935ee931c69

MLflow production evidence digest =
  sha256:16da9d330fa5a5e477bb0f0069ef5c5a0ff3fa4e8a0d1347dd493060ff622a08

Manual production evidence digest =
  sha256:ac231378afbe851567f87fb4fe6ac0d7f829bf43ede6051d489a8b95b3c6cd68
```

实际 SQLite 仅有：

```text
mlflow-lane/mlflow-runtime/data/fixture.sqlite3
manual-lane/manual-runtime/data/fixture.sqlite3
```

二者均在同一 system-temp review root 下，互相隔离。未替换路径确实经过当前
MLflow FileStore adapter、`sync_mlflow()` 和 Manual service，normalized logical output
一致；P1-01 是 identity authority 可绕过，不是否定该次正向输出。

已明确记录 capability gaps：

- MLflow FileStore 只有 latest metric；
- MLflow V1 不提供 metric unit/split/direction；
- MLflow params 是 strings；
- MLflow artifact checksum 不进入当前 store projection；
- Manual V1 只有 summary metric；
- Manual V1 不能表示 unknown direction。

### 5.6 Baseline preparation、Gate token 与 verify-only

`prepare_experiment_baseline_v1()`：

```text
status = PREPARED
execution_status = NOT_AUTHORIZED
foundation_gate = FOUNDATION_GATE_NOT_REVIEWED
all smoke statuses = SMOKE_UNAVAILABLE
all smoke values = None
all Run/metric/artifact/qualification flags = false
```

以下 token 全部在检查 path/adapter/service/DB 之前拒绝：

```text
None
"official-looking E0 foundation pass text"
"historical PASS"
object()
```

把 `MLflowAdapter`、service methods 和 `sqlite3.connect` 全部替换为 bomb 后，
`verify_experiment_golden_v1()` 仍成功返回官方 package hash，证明 verify-only 不执行
adapter/service/DB。

### 5.7 兼容、wiring 与阶段边界

- 旧 `/v1/experiments*` create/run/comparison/stats、Manual models/service、MLflow
  FileStore sync/idempotency 回归通过；
- project/ACL/history、evaluation governance、Platform/API 回归通过；
- E0 package 外的 `src/evidence_rag` 没有 production import/wiring reference；
- E0 只新增 isolated `rag/sources/experiment/{fixture,evaluation,baseline}_v1.py` 与
  package export；
- 没有 E1 registry/conditions/query/compiler/retrieval/comparability/aggregation/semantic/
  reranker/context/release implementation；
- `evals/experiment` 不存在；
- 未发布 E-B0 Run/metrics/artifact/qualification；
- shared dirty main 上的其他 API/runtime/config/schema/platform 变更属于既有用户工作，
  本 Gate 未覆盖、未改写，也未将其归因于 E0。

## 6. 命令与 probe 证据

### 6.1 固定 38 pytest

```bash
uv run pytest -q \
  tests/test_experiment_foundation.py \
  tests/test_experiment_golden.py \
  tests/test_experiment_evaluation_v1.py \
  tests/test_experiment_baseline_v1.py \
  tests/test_experiments.py \
  tests/test_real_source_adapters.py
```

结果：

```text
38 passed
```

### 6.2 额外兼容回归

```bash
uv run pytest -q \
  tests/test_experiments.py \
  tests/test_real_source_adapters.py \
  tests/test_source_history_acl.py \
  tests/test_evaluation_governance.py \
  tests/test_platform.py \
  tests/test_api.py
```

结果：

```text
16 passed
1 StarletteDeprecationWarning
```

### 6.3 静态检查

```bash
uv run ruff check \
  src/evidence_rag/rag/sources/experiment \
  tests/test_experiment_foundation.py \
  tests/test_experiment_golden.py \
  tests/test_experiment_evaluation_v1.py \
  tests/test_experiment_baseline_v1.py

uv run ruff format --check \
  src/evidence_rag/rag/sources/experiment \
  tests/test_experiment_foundation.py \
  tests/test_experiment_golden.py \
  tests/test_experiment_evaluation_v1.py \
  tests/test_experiment_baseline_v1.py

git diff --check -- \
  src/evidence_rag/rag/sources/experiment \
  tests/fixtures/experiment_golden \
  tests/test_experiment_foundation.py \
  tests/test_experiment_golden.py \
  tests/test_experiment_evaluation_v1.py \
  tests/test_experiment_baseline_v1.py
```

结果：

```text
ruff check PASS
ruff format --check PASS (8 files already formatted)
git diff --check PASS
```

Compile 使用 `PYTHONPYCACHEPREFIX=<system-temp>/pycache`：

```bash
uv run python -m compileall -q \
  src/evidence_rag/rag/sources/experiment \
  tests/test_experiment_foundation.py \
  tests/test_experiment_golden.py \
  tests/test_experiment_evaluation_v1.py \
  tests/test_experiment_baseline_v1.py
```

结果：`PASS`；未在仓库写 `__pycache__`。

### 6.4 独立 probe 类别

全部通过 `uv run python - <<'PY'` 在 in-memory/system temp 执行：

1. 官方 package/authority/slice/denominator/recipe/parity 独立重算；
2. 未替换 production lane 正向执行和 temp SQLite containment；
3. adapter/model/method subclass/wrapper identity attack；
4. exact extra false positive 与 42-row all-wrong-candidate attack；
5. row authority-field/membership/label/locator/ACL/hard-negative attacks；
6. unknown direction、missing-zero、percent-ratio、comparison state、artifact verification、
   failed/running hard negatives；
7. refreshed-digest security scanner probes；
8. fully re-signed case/recipe tamper；
9. same-content symlink 与 portable deep copy；
10. forged/missing/textual-history Gate token；
11. verify-only adapter/service/sqlite bombs；
12. MetricDefinition/series/per-point identity structure audit。

## 7. Gate criteria matrix

| Gate criterion | 结果 | 说明 |
| --- | --- | --- |
| 1. exact45、canonical membership、slice、manifest/rebuild/hash | PASS | 官方 identities/counts/bytes/tamper 均独立复核 |
| 2. strict frozen truth、IDs/locators/labels/eligible denominator | **FAIL** | P1-03：MetricDefinition/series/per-point Observation identity 缺失 |
| 3. 1 Experiment/12 Runs programmatic recipe | PARTIAL | 场景/counts/values PASS；47 observations 的 identity authority FAIL |
| 4. exact production MLflow + Manual lanes、identity、parity | **FAIL** | P1-01：subclass/wrapper/export replacement 仍宣称官方组件 |
| 5. evaluator overall/8 slices/frozen metrics/status distinctions | **FAIL** | P1-02：candidate positives 未绑定，七类指标可被错误候选保持满分 |
| 6. adversarial package/path/secret/ACL/tamper/portable copy | PASS | refreshed-digest 与 fully re-signed probes 均 fail-closed |
| 7. baseline PREPARED/NOT_AUTHORIZED/smoke/verify-only | PASS | forged token、历史 PASS 文本、execution bombs 均 fail-closed |
| 8. V1 compatibility/no wiring/no formal DB/E1–E5 | PASS | fixed + extra compatibility PASS；无 E0 production wiring |

## 8. 修复后最小重审条件

按以下独立文件锁修复，不扩展到 production schema/runtime/config/API/正式库：

1. production component authority：
   `fixture_v1.py + test_experiment_foundation.py`；
2. candidate-positive scoring：
   `evaluation_v1.py + test_experiment_evaluation_v1.py`；
3. metric/series/observation identity：
   `fixture_v1.py + evaluation_v1.py + experiment_golden fixture +
   foundation/golden/evaluation tests`。

P1-03 会改变 released package 内容，因此修复后必须提交新的：

```text
release_record_id
package_hash
authority_hash
production parity hash（若 normalized identity shape 改变）
```

重审至少重复：

- fixed 38 + compatibility regression；
- ruff/format/diff/temp compile；
- official clean rebuild + portable copy；
- fully re-signed tamper/security scanner；
- pre/after-import component replacement；
- all-wrong-candidate / extra false-positive scoring；
- exact 3 definition / 18 series / 47 observation identity + locator audit；
- baseline token/verify-only bombs；
- `evals/experiment` absence 与 E1 `NOT_AUTHORIZED`。

在三个 P1 关闭前，不得执行 E-B0，不得创建 Evaluation Run、metrics、artifact 或
qualification，也不得授权 E1。

## 9. 最终状态

```text
EXPERIMENT E0-01 FOUNDATION FAIL
P0 findings: 0
P1 findings: 3
E-B0 HARNESS PREPARATION NOT AUTHORIZED
E-B0 Run/metrics/artifact/qualification: NONE
E1: NOT_AUTHORIZED
E2–E5: NOT_IMPLEMENTED / NOT_AUTHORIZED
```

---

# 第二轮极简复审：首轮 3 P1 关闭与主路不回归

复审日期：2026-07-29  
复审范围：只复核首轮 P1-01/P1-02/P1-03 的关闭证据及 E0-01 主路不回归。  
执行约束：共享 dirty main；未建 branch/worktree，未 commit/push；未修改实现、测试、
config、dependencies、fixture 或其他文档。正式库
`var/evidence-rag.sqlite3` 继续按 `EXTERNAL_MUTABLE_SERVICE_OWNED` 处理，本轮未打开、
hash、checkpoint、复制或接触其 sidecar；所有动态执行只使用 system temp/in-memory。

本节是技术 Gate 结论，不替代后续 E-B0 执行审批或人工 release 决策。

## 10. 复审对象

独立加载并核对的新 release：

```text
release:   release-e7de1be4caf88af99c3b36de7e13c4a2
package:   sha256:e7de1be4caf88af99c3b36de7e13c4a2ba3d15d4e40e5e5e0459ecf3d583a81f
authority: sha256:cad053ebb0686c6a413a743fe9d0e142f2d61546cb18a2bb0d2a9b82af8d29e1
component: sha256:0c961eb90c9a30795debc25d4d9c04eb6f38d4214a2ae3d71f03f54f812f405d
parity:    sha256:df0d6a7f745bfda76bb4e68ce52063c0010ef474ee8afe584d4f20e2fd92ceb9
recipe:    sha256:4ff2eff2b2261f80f775d9ba6b8cce80e6b56972de459b50c1c198cc0d2fea97
```

独立重算结果：

```text
cases = 45
canonical membership = experiment-v1-001..experiment-v1-045
slices = 5/7/8/8/6/4/4/3
logical fixture = 1 Experiment / 12 Runs / 3 MetricDefinitions /
                  18 MetricSeries / 47 MetricObservations / 2 artifacts
eligible denominators = 5/28/20/9/6/45/10/2
```

## 11. 首轮 P1 关闭证据

### 11.1 P1-01 — production component identity：关闭

在九个互相隔离的 fresh subprocess 中重放：

1. pre-import `MLflowAdapter` subclass injection；
2. after-import `sync_mlflow` wrapper；
3. after-import exact-code function clone；
4. adapter public export alias；
5. service public export alias；
6. model public export alias；
7. fixture model binding alias；
8. fixed reviewed code digest 篡改；
9. adapter version/digest 篡改。

每个进程同时把 path admission、MLflow materialization、runtime creation 和
`sqlite3.connect` 替换成 bomb。结果为：

```text
attacks rejected = 9/9
I/O-boundary bombs called = 0
attack roots left empty = 9/9
```

正向 lane 独立执行确认：

```text
production component roles = 7
MLflow normalized Runs = 12
Manual normalized Runs = 12
structured authority equal across parity/MLflow/Manual = true
MLflow parity digest == Manual parity digest = true
parity hash = sha256:df0d6a7f745bfda76bb4e68ce52063c0010ef474ee8afe584d4f20e2fd92ceb9
lane SQLite files = 2, both below the dedicated system-temp parity root
```

因此 parity 不再能由 wrapper/subclass/export replacement 冒充，首轮 P1-01 关闭。

### 11.2 P1-02 — candidate-positive scoring：关闭

按首轮复现口径，保留 self-reported observed truth，只把 42 个 RETURNED rows 改成
同 ACL、全局 authority 已知、但不属于 case expected/alternative/hard-negative 的错误
candidate。结果：

```text
changed rows with every applicable metric false = 42/42
eligible denominators remain = 5/28/20/9/6/45/10/2
```

当前 perfect-row helper 实际产生 `43 RETURNED + 1 ZERO_RESULT + 1 UNAVAILABLE`，因此
又额外对全部 43 个 RETURNED rows 做穷尽重放；结果仍为 `43/43` 被修改 case 的全部
适用指标为 false，且
exact/predicate/numeric/comparability/reproduction numerator 为 `0/0/0/0/0`。未修改的
canonical zero-result/ACL rows 只保留其本来应得分数。

以下攻击也分别 fail-closed：

- exact positive 后追加 undeclared extra；
- multi-positive 删除一个 positive；
- multi-positive 乱序；
- correct observed truth + wrong candidate；
- wrong entity kind；
- declared alternative 单独出现或排在 expected 之前；
- 非连续 rank 与 duplicate candidate。

正确主路仍为满分：

```text
exact/predicate/numeric/comparability/reproduction/hard-negative/
unanswerable/zero-result
= 5/5, 28/28, 20/20, 9/9, 6/6, 45/45, 10/10, 2/2
```

因此首轮 P1-02 关闭。

### 11.3 P1-03 — definition/series/per-point identity：关闭

released authority 独立计数为：

```text
MetricDefinition identities = 3 unique
MetricSeries identities = 18 unique
MetricObservation identities = 47 unique
```

对复制到 system temp 的 package 修改 recipe，并同步刷新 recipe file digest、size、
fixture recipe digest、package hash 和 release record，重放 15 类 fully re-signed
语义攻击：

```text
definition: ID / locator
series:     ID / cross-run / cross-definition / locator / order
point:      observation ID / cross-run / cross-series / cross-definition /
            step / time / order / locator
```

结果为 `15/15` 被 loader 拒绝；其中 14 类在 `ExperimentFixtureRecipe` identity contract
处拒绝，series order 在 released Golden authority 对照处拒绝。另将 reviewed
`raw_series`/aggregation truth 改写时，evaluator 从 released per-observation authority
重算并判 numeric truth 为 false。

因此首轮 P1-03 关闭。

## 12. 主路与安全不回归

- 两次 clean rebuild 与 released 三文件逐字节相同；深层 portable copy verify PASS。
- exact45、ordered membership、slice counts、declared files、package/authority/recipe hash
  全部独立重算命中。
- recipe 场景继续覆盖 multi-seed baseline/treatment、different dataset、percent/ratio、
  failed high score、running、missing commit/environment、string `"1"` vs numeric `1`、
  time series、verified checksum vs URI-only artifact。
- unknown direction 冒充 higher、percent 冒充 ratio、failed/running candidate 进入结果、
  URI-only artifact 冒充 verified、`not_comparable`/`insufficient_metadata` 混淆均被独立
  probe 判错。
- released verify-only 在 adapter/service/runtime/materializer/SQLite bombs 下完成，
  bomb 调用数为 `0`。
- missing/forged/当前 PASS 文本/历史 PASS 文本四类 Gate token 全部在读取 path 前得到
  `NOT_AUTHORIZED`；smoke 仍严格为 `SMOKE_UNAVAILABLE`。
- `evals/experiment` 不存在；当前没有 E-B0 Evaluation Run、baseline/treatment
  identity、metrics、artifact 或 qualification。
- 未发现 production wiring/schema/runtime/config/platform 或旧
  Experiment/API/adapter/ACL/governance 回归。

## 13. 命令与测试证据

独立固定集合：

```bash
uv run pytest -q \
  tests/test_experiment_foundation.py \
  tests/test_experiment_golden.py \
  tests/test_experiment_evaluation_v1.py \
  tests/test_experiment_baseline_v1.py \
  tests/test_experiments.py \
  tests/test_real_source_adapters.py \
  tests/test_evaluation_governance.py \
  tests/test_source_history_acl.py
```

结果：

```text
63 passed
= E0-01 + Experiment/API/real adapters 57
+ governance/ACL 6
```

静态检查：

```text
ruff check: PASS
ruff format --check: PASS (8 files already formatted)
git diff --check: PASS
compileall with PYTHONPYCACHEPREFIX under system temp: PASS
```

另以 `uv run python - <<'PY'` 完成 identity、candidate scoring、fully re-signed
definition/series/point tamper、positive parity、portable rebuild、semantic hard-negative、
verify-only 和 baseline token probes；这些 probe 只使用 released fixture、in-memory 与
system temp。

## 14. 第二轮 findings 与 Gate 决定

```text
P0 findings: 0
P1 findings: 0
P2 findings: 0
P3 findings: 0
```

首轮三个 P1 均有独立关闭证据，且未观察到 E0-01 主流程回归。Foundation Gate 技术
条件满足；授权范围只到 E-B0 harness preparation，不授权执行 E-B0，不创建或发布
任何 Run/metrics/artifact/qualification，也不授权 E1。

```text
EXPERIMENT E0-01 FOUNDATION PASS
P0 findings: 0
P1 findings: 0
E-B0 HARNESS PREPARATION AUTHORIZED
E-B0 production execution: NOT_AUTHORIZED
E-B0 Run/metrics/artifact/qualification: NONE
E1: NOT_AUTHORIZED
E2–E5: NOT_IMPLEMENTED / NOT_AUTHORIZED
```
