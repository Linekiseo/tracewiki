# Experiment Source RAG 单源开发计划

- 状态：E0 Foundation `PASS`；E-B0 fixed baseline
  `PUBLISHED_VERIFIED_NON_QUALIFIED`；E1–E5 仓库内完整 engineering `COMPLETE`；
  正式库 migration、production treatment/shadow/canary/default switch
  `EXTERNAL_BLOCKED`；release `HOLD_DEFAULT_V1`
- 开发方案版本：experiment-source-development-v1
- 日期：2026-07-29
- 统一开发契约：[00_SINGLE_SOURCE_DEVELOPMENT_CONTRACT.md](00_SINGLE_SOURCE_DEVELOPMENT_CONTRACT.md)
- 目标设计：[03_EXPERIMENT_SOURCE_RAG.md](../sources/03_EXPERIMENT_SOURCE_RAG.md)
- 路线图：[04_IMPLEMENTATION_ROADMAP.md](../04_IMPLEMENTATION_ROADMAP.md) M4-A
- 面试材料：[03_EXPERIMENT_SOURCE_INTERVIEW.md](../interview/03_EXPERIMENT_SOURCE_INTERVIEW.md)

## 1. 状态真值与执行边界

本文件最初把 Experiment 单源设计拆成 evaluation-first、可独立评审的 Issue/PR。
截至 2026-07-29，E0–E5 的仓库内工程已经完成；这仍不代表 production quality 或发布
完成。

前序状态按统一顺序固定为：

- Code C0–C7 engineering `COMPLETE`；
- Codex X1 engineering `COMPLETE`；
- Codex X-T1 唯一 production attempt
  `FAILED_BEFORE_PUBLISH / NO_RESULT / NOT_QUALIFIED / UNAVAILABLE`；
- Codex X-T1 retry 仍 `NOT_AUTHORIZED`；之后用户以单会话产品优先方式完成了独立于
  X-T1 treatment 的 X2–X5 完整仓库内工程路径，质量仍 unavailable；
- 当前顺序切到 Experiment，不循环或重试 X-T1。

当前 Experiment 状态（2026-07-29 单会话加速实施后）：

| Milestone | 授权状态 | 当前证据 |
| --- | --- | --- |
| E0-01 Golden/Evaluation Foundation | `PASS / COMPLETE` | released Golden exact 45；8 slices=`5/7/8/8/6/4/4/3` |
| E0 Foundation Gate | `PASS` | P0/P1=0；authority 与 production parity 已冻结 |
| E-B0 flat V1 baseline | `PUBLISHED_VERIFIED_NON_QUALIFIED` | Run `085799235df74bcdc4fc56c0c17f36e7`；45/45 available；质量负结论保留 |
| E1 Metric/Observation + immutable foundation | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | typed snapshots/entities、isolated additive schema/ledger、atomic generation publish/rollback |
| E2 typed DSL/exact/numeric | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | versioned AST/parser、allowlisted parameterized compiler、scope-first exact/numeric、unit/NaN truth |
| E3 comparability/aggregation | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | four-state comparability、RunGroup mean/median/std/CI、seven reproduction roles、strict compare |
| E4 semantic/reranker/context | `ENGINEERING COMPLETE / QUALITY UNAVAILABLE` | versioned semantic units、exact/structured pinned、semantic tail、negative rerank、task context；远程模型未做 |
| E5 opt-in/shadow/release | `ENGINEERING COMPLETE / HOLD_DEFAULT_V1` | governed pipeline、atomic sync、path/SSRF/trace、stable routing、shadow/canary evaluator、rollback |

实现与验证索引：

- `src/evidence_rag/rag/sources/experiment/{contracts,store,query,analysis,semantic,governance,pipeline}_v2.py`：
  E1–E5 完整仓库内工程实现；
- `src/evidence_rag/rag/experiment_v2.py`：保持既有 opt-in compatibility projector；
- `src/evidence_rag/rag/multisource.py`：Code/Codex/Experiment/Notebook/Document/Workspace
  统一 context；
- `tests/test_experiment_v2.py`、`tests/test_multisource_context.py`：新主路专项；
- 单会话过程与回归真值见
  [04_ACCELERATED_SINGLE_SESSION_WORKLOG.md](04_ACCELERATED_SINGLE_SESSION_WORKLOG.md)。

必须同时保留以下真值：

- 既有文档记录的正式数据为 Experiment 0、Run 0、Metric 0、Artifact 0；本计划只保留这条
  已知基线，不把它改写成一次新的测量。
- 正式库分类为 `EXTERNAL_MUTABLE_SERVICE_OWNED`。本次计划审计没有打开、查询、hash、
  checkpoint 或复制正式库，也不声称它当前未变化。
- E0/E-B0 及后续工程、测试、评测只允许使用 programmatic isolated fixture、临时
  MLflow FileStore、临时 SQLite、临时 API runtime 和明确的临时输出目录。
- 当前已有 released Golden 与 E-B0 fixed baseline artifact；E-B0 为
  `NON_QUALIFIED`。仍没有 E1–E5 treatment Run、质量提升结论或默认 V2 release decision。
- 本次单会话完成了无远程模型、仅 isolated SQLite 的 E1–E5 工程路径；production
  treatment、真实 shadow/canary、SLO qualification、默认切换和正式库 migration
  仍未执行。
- Engineering Gate、Evaluation Run、quality decision 和 release decision 是四种不同
  事实，不得互相替代。

## 2. 当前真实实现映射

### 2.1 Runtime 与 API

`create_runtime()` 在同一个 `SQLiteStore` 上初始化 `ExperimentStore`，随后构造
`ExperimentService` 并注入 `WorkspaceService`、`RawSourceService`。同一个 Runtime 还
构造 `PlatformStore/PlatformService` 和通用 `EvaluationStore/EvaluationService`：

```text
create_runtime()
├── ExperimentStore.initialize()
├── ExperimentService(store, workspace, raw_sources)
├── PlatformStore.initialize()
├── PlatformService(..., event_store=evaluation_store)
└── EvaluationService(evaluation_store, platform, workspace)
```

当前 API 由 [experiments/router.py](../../../src/evidence_rag/experiments/router.py) 提供：

| Endpoint | 当前行为 |
| --- | --- |
| `GET /v1/experiments/stats` | 返回 experiment/run/completed/version-bound/metric 计数 |
| `GET /v1/experiments/sources` | 列出 MLflow source 状态 |
| `POST /v1/experiments/sources/mlflow/sync` | 同步 FileStore 或 REST |
| `GET/POST/PATCH /v1/experiments` | Manual Experiment CRUD |
| `GET/POST/PATCH /v1/experiments/runs` | Manual Run CRUD |
| `POST /v1/experiments/comparisons` | 持久化 basic comparison |
| `POST /v1/search` | 通过 Platform 的跨源 flat search 检索 Experiment family |

所有 mutation endpoint 受 mutation auth，读取在 router 层做 project access 检查。当前
[config.py](../../../src/evidence_rag/config.py) 没有 Experiment V2、shadow、canary、
registry、DSL、reranker 或 context flag；Experiment 的默认平台行为就是 V1。

### 2.2 Models、Schema、Service 与 Store

当前 [experiments/models.py](../../../src/evidence_rag/experiments/models.py) 定义：

- `ExperimentCreate/Update`；
- `RunCreate/Update`；
- `MetricInput`；
- `ArtifactInput`；
- `ComparisonRequest`；
- `MLflowSyncRequest`。

关键 V1 语义是：

- `MetricInput.higher_is_better` 默认 `True`；
- metric 只有 name/value/unit/split/step，没有 definition、aggregation、observed time；
- Run 的 dataset/config/environment 是字段或 JSON blob；
- Artifact 只有 URI、可选 checksum/media/metadata，URI 本身不证明内容已验证；
- comparison request 只指定同一 Experiment 内的 Run 和 baseline。

当前 [experiments/schema.py](../../../src/evidence_rag/experiments/schema.py) 的 DDL owner 是
`ExperimentStore.initialize()`，包含：

- `experiments`；
- `experiment_runs`；
- `run_metrics`；
- `run_artifacts`；
- `experiment_comparisons`；
- `experiment_sources`；
- `external_experiment_mappings`。

当前没有 MetricDefinition/Observation、DatasetVersion、ConfigSnapshot、
EnvironmentSnapshot、RunGroup、RunAttempt/observed version、ArtifactVersion、
Experiment retrieval unit 或 sync generation 表。

[experiments/service.py](../../../src/evidence_rag/experiments/service.py) 与
[experiments/store.py](../../../src/evidence_rag/experiments/store.py) 当前负责：

- Manual Experiment/Run 创建与 stable display key；
- MLflow external Experiment mapping 与 external Run 幂等 upsert；
- commit tag 解析到 Code Commit entity，并建立 Run `uses` Commit；
- 为每个 metric 建立 Run `reports` Metric；
- Raw MLflow snapshot、derivation 和 secret redaction；
- Run/Metric/Artifact 持久化；
- basic comparison 和 comparison result 持久化。

当前 imported Run 再同步时会更新同一个 `experiment_runs` 行，并删除后重建其
`run_metrics` 和 `run_artifacts`。这不保存完整 observation history，也不保证 completed
Run 的 derived snapshot 不变。

### 2.3 MLflow File 与 REST adapters

[experiments/adapters/mlflow.py](../../../src/evidence_rag/experiments/adapters/mlflow.py)
支持两条生产 adapter 路径：

```text
file/path URI
→ 遍历 Experiment 目录
→ meta.yaml / params / tags / metrics / artifacts
→ normalized Experiment + Run

http(s) URI
→ MLflow experiments/search
→ MLflow runs/search
→ normalized Experiment + Run
```

当前真实限制：

- FileStore metric 文件只保留最后一条可解析记录，完整 time series 丢失；
- REST search payload 只使用返回的 metric summary，不额外读取 history；
- MLflow metric 被规范化为 `unit=None`、`split=None`、
  `higher_is_better=True`；
- FileStore artifact 会产生 locator 和 size metadata，但不等于内容完成 checksum/
  authorization/verification；
- adapter 构造会 resolve 本地路径，但当前没有 E5 要求的显式 FileStore path allowlist；
- remote REST 当前依赖 `httpx`，E5 前仍缺完整 SSRF/host policy；
- service 会在 RawSource 接受前做 secret redaction，但 V2 config/environment 字段化后仍需
  二次 fail-closed 检查。

### 2.4 当前平台搜索与比较

[platform/store.py](../../../src/evidence_rag/platform/store.py) 的 Experiment 搜索不是专用
Retriever。它从 query 提取 token，以 SQLite 注册的 `evidence_match` 做 case-folded
substring 计数：

- Experiment：title/objective/hypothesis；
- ExperimentRun：name/command/config/commit/external ID/dataset；
- Metric：name 和 value 的字符串形式；
- 当前 `sources=["experiment"]` 还会搜索 NotebookRun/Cell text，这是 V1 family
  compatibility 行为，不代表 Notebook Output 属于 Experiment 结构事实。

[platform/service.py](../../../src/evidence_rag/platform/service.py) 随后做全局 scope filter、
authority/version 调整、dedup/diversification、关系扩展和统一 citation map。
`GlobalSearchRequest.experiment_ids` 已存在，可用于结果 scope，但没有 typed predicate、
numeric range、unit conversion、aggregation 或 Experiment 专用 trace。

当前 comparison 的真实规则是：

- 所有 Run 必须属于同一 Experiment；
- baseline 默认 request 中第一个 Run；
- controls 检查 dataset ID/version、branch 和完整 environment JSON 是否相同；
- config 只列 key diff；
- metric 仅按 `(name, split)` 对齐；
- delta 固定为 `candidate - baseline`；
- direction 采用 baseline 的 `higher_is_better`；
- 不校验 unit、definition、step、aggregation、seed、sample count 或 Run status；
- 缺失 candidate metric 只表现为空 candidate list，不形成明确的
  `insufficient_metadata` truth。

E-B0 必须精确记录这些当前行为，不能先修正再称其为 baseline。

### 2.5 Tests、fixtures 与 evaluation

当前直接相关测试：

- [tests/test_experiments.py](../../../tests/test_experiments.py)：Manual Experiment/Run、
  commit binding、Artifact checksum 字段、basic comparison、stats 和 iteration link；
- [tests/test_real_source_adapters.py](../../../tests/test_real_source_adapters.py)：
  programmatic local MLflow FileStore、sync 幂等、latest metric 和 artifact name；
- [tests/test_platform.py](../../../tests/test_platform.py)：Experiment/Metric flat search、
  research graph、relation expansion 和跨源 claim/run 关系；
- [tests/test_api.py](../../../tests/test_api.py)：通用 API/ingestion 回归。

当前通用 evaluation 包提供 case/run/result/metric 表、terminal immutability trigger 和
query event 记录，但现有 released loader/runner 以 Code/Codex 专项为主。Experiment
当前没有：

- released 45-case Golden；
- Experiment case schema/loader；
- FileStore+Manual parity fixture；
- flat V1 baseline runner；
- predicate/numeric/unit/direction/comparability/reproduction evaluator；
- Experiment treatment artifact 或 release ledger。

因此现有 CRUD、adapter 和平台测试只能证明功能存在，不能证明 Experiment RAG 质量。

### 2.6 正式数据与所有权

| 对象 | 已知既有真值 | 本计划是否重新读取 |
| --- | ---: | --- |
| Experiment | 0 | 否 |
| Run | 0 | 否 |
| Metric | 0 | 否 |
| Artifact | 0 | 否 |

该表不是当前时点盘点。正式库仍是 `EXTERNAL_MUTABLE_SERVICE_OWNED`；后续任何正式库
migration、canary 或数据观察都需要独立授权，不能由 E0 fixture 代替。

### 2.7 跨源边界

- **Commit**：Experiment Run 只保存 repository/commit 引用和 `uses` relation；commit
  正文、diff、symbol、test evidence 继续由 Code Source 拥有。
- **Notebook**：NotebookRun/Cell/Output 继续由 Notebook Source 拥有；显式 Run relation
  可以作为 locator。Notebook output 只有经过明确 metric adapter contract、definition
  resolution 和 provenance 校验后，才可能成为 MetricObservation，绝不自动提升为 Metric。
- **Document**：文档表格或正文中的数值继续是 Document fact/candidate；不会写入
  Experiment MetricObservation。
- **Artifact**：URI 是 locator，不是 verified content；内容获取、ACL、checksum、
  availability 和 media validation 必须各自有证据。
- **Multi-source**：Code/Notebook/Document 与 Experiment 的 joint planning、
  cross-source calibration/fusion 留到多源阶段；本计划只定义单源可引用接口。

## 3. 本计划负责、不负责与外部依赖

### 3.1 本计划负责

- exact 45-case Experiment Golden 与 isolated evaluation foundation；
- programmatic multi-run/multi-seed MLflow FileStore 和 Manual parity fixture；
- 当前 V1 `evidence_match`/comparison 的真实 E-B0；
- MetricDefinition/Observation 与未知方向；
- DatasetVersion、ConfigSnapshot、EnvironmentSnapshot、RunGroup 和 immutable observed
  Run foundation；
- safe typed Filter DSL、exact/structured/numeric retrieval；
- strict comparability、reproduction roles 和 deterministic aggregation；
- semantic surface、optional reranker 和 structured context；
- V1-compatible opt-in、shadow、canary、observability、rollback 和 release evidence。

### 3.2 留到其他来源或多源层

- Code 内容、diff/symbol/test retrieval；
- Notebook template/revision/execution/cell/output/error retrieval；
- Document TableFact/Claim validation；
- 跨源 query planner、score calibration、global fusion 和 answer generation；
- Artifact 内容解析器；
- DVC/OpenLineage/W&B 等新生产 adapter。

### 3.3 本轮不做

- 不打开、迁移、hash、checkpoint 或写正式库；
- 不抓取 Artifact 内容；
- 不把 embedding 分数用于数值、unit、direction 或 comparability truth；
- 不删除/重命名旧表、stable ID、endpoint 或 response field；
- 不修改 Code/Codex/Notebook/Document 的 source contract；
- 不增加外部模型或常驻进程作为 E0/E1/E2/E3 的前置；
- 不在 E-B0 前做效果型实现；
- 不执行未授权 E-B0、E1–E5。

## 4. Evaluation Foundation 契约

### 4.1 Released Golden：exactly 45

E0 Foundation 完成时必须 release **恰好 45** 条 Golden；当前尚未 release，也没有可填写
的 Golden Version 或 package hash。

| Primary slice | 固定数量 |
| --- | ---: |
| exact Run/Experiment | 5 |
| structured filter | 7 |
| metric ranking/range | 8 |
| Run comparison | 8 |
| reproduction | 6 |
| failure/status | 4 |
| aggregation/trend | 4 |
| unanswerable/incomparable/ACL | 3 |
| **总计** | **45** |

每个 case 必须冻结：

- canonical case ID、question、task 和一个 primary slice；
- answerability：`answerable`、`unanswerable`、`acl_denied` 或
  `insufficient_metadata`；
- exact expected Experiment/Run/MetricDefinition/Observation IDs；
- logical source locator、Run display key、external ID、metric observation locator；
- typed predicate、operator、value type、unit 和 conversion policy；
- expected comparability state 与 controlled/treatment/unknown/disqualifying labels；
- expected reproduction roles、missing roles 和 artifact verification state；
- hard negatives、forbidden candidates 和 acceptable alternatives；
- 每个 metric 的显式 eligible case ID 集合、numerator/denominator 规则；
- unavailable reason；case 不得因为系统答不出而从 denominator 动态消失。

Golden loader 对 45 条 membership、八个 slice 计数、重复 ID、缺失 locator、未知 label、
denominator drift 和未声明文件 fail closed。

### 4.2 Programmatic FileStore + Manual fixture

fixture recipe 在运行时生成，不提交外部实验库副本。最小场景由一个逻辑 Experiment 和
12 个 Run 组成：

- baseline RunGroup：completed seeds 11/22/33；
- treatment RunGroup：completed seeds 11/22/33；
- 一个不同 DatasetVersion；
- 一个相同 metric name 但 percent unit；
- 一个高分 failed Run；
- 一个 running Run；
- 一个缺 commit/environment 的 Run；
- 一个 config value string `"1"` 与 numeric `1` 的 hard negative；
- completed runs 含 step/time series；
- 同时包含可校验 checksum 的 Artifact 和只有 URI/不可验证的 Artifact。

同一 deterministic recipe 必须生成两条隔离 lane：

1. 临时 MLflow FileStore，由当前 `MLflowAdapter` 和 `sync_mlflow()` 消费；
2. 独立临时 runtime，通过当前 Manual Experiment/Run API 消费。

两条 lane 比较 normalized identity/conditions/metrics/artifacts 的预期 parity，但绝不连接
同一正式库。fixture 还必须支持多次生成 byte-equivalent logical manifest、固定 seeds、
时间和 IDs；本地绝对路径只存在于临时 runtime，不进入 released artifact。

### 4.3 Frozen truth 与 denominator

Evaluation contract 必须区分：

- `retrieved` 与 `answerable`；
- `zero_result` 与 `unanswerable`；
- `not_comparable` 与 `insufficient_metadata`；
- `missing` 与数值零；
- `unsupported_v1` 与系统错误；
- `artifact_located` 与 `artifact_verified`；
- `metric_unknown_direction` 与 `lower/higher`。

所有总体和 slice 指标从 manifest 中冻结的 eligible case IDs 计算。Runner 必须输出 total、
eligible、available、unavailable、zero-result 和 error counts；不允许只输出一个聚合值。

### 4.4 Content-addressed release manifest

release manifest 至少包含：

```text
dataset_id
release_record_id
schema_version
case_count = 45
primary_slice_counts
case_membership_digest
fixture_recipe_digest
file_digests
label_policy_version
denominator_policy_version
locator_policy_version
comparability_policy_version
created_from_code_identity
```

package identity 由 canonical manifest 和声明文件内容计算；version/release ID 在内容冻结与
review 通过后生成，当前不预留一个假的版本号。Verifier 只接受声明文件，拒绝绝对路径、
symlink escape、secret sentinel、NaN/Infinity、额外文件和 digest mismatch。

### 4.5 准备期零 Run/指标

E0-01 可以在测试内短暂创建临时 SQLite/Run 以验证 adapter，但在 Foundation Gate 前：

- 不发布 Evaluation Run；
- 不生成 Baseline/Treatment identity；
- 不填写任何实测 metric 或 qualification；
- 不向正式库写 Experiment/Run/Metric；
- 不把 smoke/fixture unit test 叫作 baseline。

## 5. E-B0：当前 V1 的真实 isolated baseline

E-B0 只有在 E0 Foundation Gate 明确授权后执行。它是实现效果优化之前的唯一 flat
control，不是计划阶段的推演结果。

### 5.1 固定 V1 行为

E-B0 必须 pin 并真实调用：

- 当前 `MLflowAdapter` FileStore 路径；
- 当前 Manual API 路径；
- 当前 `PlatformService.search()`；
- 当前 `PlatformStore.structured_search()` 与 token substring `evidence_match`；
- `sources=["experiment"]` 的当前 Experiment/Run/Metric 及 Notebook family 行为；
- 当前 `ExperimentService.compare()` 的 controls/config/name+split/raw-delta 规则；
- 当前默认配置、无 Experiment V2 flag、无专用 embedding/reranker/context。

如果 baseline runner 发现 V1 crash，只记录 error/unavailable；不得在同一个 Run 中修复
V1 后重跑并复用 identity。

### 5.2 真实但隔离的执行

```text
released Golden + released fixture recipe
→ 显式 temp root
→ temp MLflow FileStore / temp Manual API runtime
→ production adapters/services/stores/platform search
→ predictions + comparison results
→ deterministic evaluation
→ immutable portable artifact
→ verify-only
```

runner 必须拒绝默认数据目录、正式库路径及其 sidecar；只接受新建临时 SQLite 和显式输出
目录。Run 关闭所有连接后再发布 artifact，且记录实际调用的 class/function、配置和代码
identity。

### 5.3 Portable verify-only artifact

最小 artifact：

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

artifact 必须：

- 可复制到另一目录后离线 verify；
- verifier 不重跑检索、不连接数据库、不访问网络、不抓取 Artifact；
- 不包含 SQLite、WAL/SHM、Raw Object、绝对路径、credential 或正式 service locator；
- 保留每 case 的 expected/observed logical locator、candidate order、reason code、
  answerability/comparability truth；
- 将 NaN/Inf、missing、unsupported 和 error 写成显式状态，不写非标准 JSON；
- 以 checksum 覆盖每个声明文件，tamper 时 fail closed。

E-B0 完成后才能填写真实 Run identity、指标、slice/error 和
`QUALIFIED/NON_QUALIFIED/UNAVAILABLE`；当前全部为空。

## 6. Additive Schema、migration 与 rollback

### 6.1 DDL owner 与 stable identity

Experiment V2 DDL 仍由 `experiments/schema.py` 与 `ExperimentStore.initialize()` 所有，
避免把来源专用表塞进通用 `db_schema.py`。P0 只做 additive migration：

- 不改写 `experiments.id`、`experiment_runs.id`、display key 或 external mapping；
- 不删除 `run_metrics`、`run_artifacts` 或 `experiment_comparisons`；
- V1 继续读写旧表；
- V2 新表通过 stable `experiment_id/run_id` 关联；
- cleanup 晚于正式授权与稳定观察期，且不在 E1–E5 内。

目标 additive 表：

```text
experiment_schema_versions
experiment_run_snapshots
metric_definitions
metric_aliases
metric_observations
dataset_versions
config_snapshots
config_values
environment_snapshots
artifact_versions
run_groups
run_group_members
experiment_sync_generations
experiment_retrieval_units
experiment_release_records
```

旧 `run_metrics` 是 V1 compatibility mirror，不再作为 V2 metric truth 的唯一来源。

### 6.2 Dual write/read 与 immutable sample foundation

- Manual/MLflow ingest 先保留 V1 write，再 shadow 写 V2 normalized records；
- completed Run 的 V2 snapshot content-addressed，源变化形成新 observed snapshot，不原地
  覆盖历史 MetricObservation；
- running Run 可产生后续 observed snapshot；completed/failed lifecycle 由新 snapshot
  表达；
- E1 的 sample Experiment/Run foundation 只在 isolated fixture 中发布 immutable
  identity，用于后续 E2–E5 treatment；
- V2 read 只在 opt-in/shadow 使用；default V1 不变；
- parity mismatch 记录 reason 并 fallback V1，不静默选一边。

### 6.3 Backfill 与未知现有数据

虽然既有文档记录正式数据为 0，migration 不能把“0”写成永久假设。实现必须：

- 对任意已有 V1 rows 提供显式、可重入、分批 shadow backfill；
- 不推断未知 metric direction；缺 definition 时写
  `definition_status=unknown/direction=unknown`；
- 保留 raw name/value/unit/split/step 和 provenance；
- 不在 runtime startup 自动扫描或重写全部历史；
- backfill count、orphan、hash/parity 和 failure 可审计；
- 本计划阶段不对正式库执行 backfill。

### 6.4 Migration rollback

rollback 只做：

1. 关闭 V2/dual-write flag；
2. 恢复 V1 read 与 basic comparison；
3. 停止新 generation publish；
4. 保留 additive tables 和失败 generation 供审计；
5. 验证旧 endpoint/response 和 stable ID；
6. 禁止 destructive down migration。

## 7. Metric Registry 与 numeric truth

### 7.1 MetricDefinition

MetricDefinition 至少包含 canonical name、aliases、description、unit、direction、
valid range、required dimensions、default aggregation 和 registry version。

`direction` 是三态：

```text
higher
lower
unknown
```

unknown direction 的 Observation 可以保存和精确返回，但不得回答“最佳”“提升”“退化”。
V1 `higher_is_better=True` 默认值不能自动升级为 V2 `higher`；只有显式用户字段、
versioned registry 或可信 adapter mapping 才能提供方向 provenance。

### 7.2 MetricObservation

Observation 必须保存：

- definition ID/status；
- run snapshot ID；
- raw name/value representation；
- parsed numeric value；
- raw unit 与 canonical unit；
- split/dimensions；
- step、observed time、aggregation role；
- validity 和 invalid reason；
- source locator/version。

FileStore/REST 都必须保留可获得的 time series。若上游只提供 summary，必须标记
`history_availability=summary_only`，不能伪造完整曲线。

### 7.3 Unit 与异常值

- ratio 和 percent 是不同 unit，转换必须显式且可追踪；
- ms/s 等转换只通过 registry allowlist；
- NaN/Inf 作为 invalid observation，不能进入排序、delta 或 aggregation；
- baseline=0 时 relative delta 返回明确 undefined reason；
- raw value 与 normalized value同时保留；
- deterministic sort 使用 normalized value、direction 和 stable Run ID tie-break；
- numeric formatting、rounding 和 aggregation policy 版本化。

## 8. Safe typed Filter DSL

目标 parser 只产生领域 AST，不产生 SQL 文本：

```text
ExperimentQuery
├── task
├── experiment_ids / run_ids / statuses
├── MetricPredicate(name, op, split, step_policy, aggregation, unit)
├── DatasetPredicate(id, version)
├── FieldPredicate(config_key, typed_op, typed_value)
├── commit / time_range / group_by
└── limit
```

安全规则：

- field、operator、aggregation、group-by 和 unit conversion 全部白名单；
- numeric/string/bool/list 类型在 compile 前验证；
- SQL identifier 来自静态 registry，用户值只作为绑定参数；
- 禁止任意 SELECT、JOIN、function、ORDER BY 或 raw SQL fragment；
- limit 有硬上限；
- parser confidence 低时保留 unresolved predicate，返回确认信息或 V1 fallback；
- ACL/project/source scope 在 candidate query 前执行；
- deterministic evaluator 不调用 LLM。

支持的第一阶段 operator：

```text
eq ne gt gte lt lte in
max min top bottom
latest final best
mean median std count
delta relative_delta
```

exact ID/external ID/display key、structured filter 和 numeric evaluator 先于 semantic
candidate；semantic channel 永远不能覆盖结构化结果。

## 9. Strict comparability、aggregation 与 reproduction truth

### 9.1 Comparability Gate

比较至少检查：

- Experiment/task；
- MetricDefinition、unit、split、step/final/best policy；
- DatasetVersion、preprocessing；
- code commit；
- config controls/treatment；
- environment compatibility；
- seed/repeat/fold；
- Run status；
- sample、aggregation 和 exclusion。

输出只允许：

```text
comparable
comparable_with_caveats
not_comparable
insufficient_metadata
```

每个差异必须归类为 controlled、treatment、unknown 或 disqualifying，并引用 raw
snapshot/observation。`not_comparable` 或 `insufficient_metadata` 时，不输出确定提升；
可以返回已知 raw facts、缺失字段和可执行的补证建议。

### 9.2 Deterministic aggregation

RunGroup 明确 seeds/folds/repeats、include/exclude 和 group identity。Aggregation 输出：

- included/excluded Run IDs 与原因；
- raw observations；
- function/policy version；
- N、mean/median/std/CI 或 unavailable；
- unit、split、step policy；
- dataset/config/environment consistency。

同一输入必须生成同一 canonical result；missing、invalid、failed/running Run 不被静默计入。

### 9.3 Reproduction roles

reproduction truth 至少有：

```text
command
code_commit
dataset_version
config_snapshot
environment_snapshot
seed
required_artifacts
```

每个 role 为 `present_verified`、`present_unverified`、`missing` 或 `inaccessible`。
Artifact URI 只能支持 locator role；只有 checksum/availability/authorization 满足时才能
标为 verified content。角色不足时返回 `insufficient_metadata`，不声称可复现。

## 10. Semantic surfaces、reranker 与 context

E4 只能辅助自然语言映射：

- `experiment.surface`：title/objective/hypothesis/tags；
- `run.surface`：name/status/condition summary；
- `metric.definition`：canonical/alias/description/unit/direction；
- `config.surface`、`dataset.surface`、`artifact.surface`；
- `comparison.surface`。

约束：

- raw numeric truth 永远来自结构化 Observation，不从 embedding text 解析；
- exact/structured/numeric candidates 固定保留，reranker 只重排 semantic tail；
- wrong dataset/unit/split、failed-for-best、unknown direction 和 stale snapshot 是负特征；
- embedding/profile 可选且 versioned；无 provider 时 sparse/alias 路径仍可工作；
- context builder 输出 identity、conditions、raw citations、computed result、
  comparability 和 missing roles；
- LLM 只解释已计算事实，不计算 delta、relative delta、aggregation 或 comparability。

## 11. 任务 DAG 与授权规则

```text
Code C0–C7 engineering COMPLETE
└── Codex X1 engineering COMPLETE
    └── X-T1 FAILED_BEFORE_PUBLISH / NO_RESULT
        ├── retry NOT_AUTHORIZED
        └── switch to Experiment
            └── E0-01 Golden/Evaluation Foundation
                PASS / COMPLETE
                └── E0 Foundation Engineering Gate
                    └── E-B0 flat V1 baseline
                        PUBLISHED_VERIFIED_NON_QUALIFIED
                        └── E1-01 contracts/schema/immutable snapshot COMPLETE
                            ├── E1-02 Metric Registry/Observation/adapters
                            └── E1-03 Dataset/Config/Environment/RunGroup
                                └── E1 Gate + E-T1
                                    └── E2-01 typed query/parser
                                        ├── E2-02 safe compiler/exact retrieval
                                        └── E2-03 numeric/unit evaluator
                                            └── E2 Gate + E-T2
                                                └── E3-01 comparability policy
                                                    └── E3-02 aggregation/reproduction/comparison
                                                        └── E3 Gate + E-T3
                                                            └── E4-01 surface units
                                                                ├── E4-02 optional semantic/reranker
                                                                └── E4-03 structured context
                                                                    └── E4 Gate + E-T4
                                                                        └── E5-01 API/flags/platform shadow
                                                                            └── E5-02 sync/security/observability
                                                                                └── E5-03 canary/release/rollback
                                                                                    └── E5 release decision
```

授权规则（完成后真值）：

- E0–E5 仓库内工程已完成；没有新的 production treatment、shadow/canary 或 default
  switch 授权。
- 每个未来 `E-Tn` 仍依赖该阶段独立 Engineering/quality Gate 的显式授权。
- 每个 treatment 必须固定引用 E-B0 和直接父臂；不得用未审计 smoke 代替。
- quality 未达可保留 negative result，但 security/ACL/truth/identity 硬门失败必须阻塞
  发布。
- 可并行只表示文件锁可并行准备，不表示可越过 Gate。
- 正式库 migration、production canary 和外部模型 provider 始终是额外授权。

## 12. Issue/PR 任务卡

### E0-01 — Golden/Evaluation Foundation

- **状态**：`PASS / COMPLETE`。
- **目标**：建立 strict 45-case contract、programmatic FileStore+Manual fixture、
  baseline runner preparation、portable verifier 和 Foundation Gate；不执行 E-B0。
- **依赖**：本计划 `DESIGN_READY`。
- **最小文件锁**：
  `src/evidence_rag/rag/sources/experiment/__init__.py`、
  `evaluation_v1.py`、`fixture_v1.py`、`baseline_v1.py`；
  `tests/fixtures/experiment_golden/`；
  `tests/test_experiment_evaluation_v1.py`、
  `tests/test_experiment_golden.py`、
  `tests/test_experiment_baseline_v1.py`、
  `tests/test_experiment_foundation.py`。
- **Contract**：纯 Python/fixture contract；不改 schema/runtime/API/config/platform；
  exact 45、八 slice、frozen denominator/locator/comparability/unanswerable、content-addressed
  manifest、verify-only。
- **实现**：先 case/result/artifact schema，再 fixture recipe，再 loader/verifier，再
  temp runner；runner 默认拒绝非临时路径。
- **专项测试**：membership/count、adapter parity、multi-run/multi-seed、time series、
  unknown direction、hard negatives、tamper、path escape、secret、NaN/Inf、zero-result、
  unavailable。
- **回归/quality/security**：运行现有 Experiment、real adapter、Platform/API tests；
  quality 只验证 evaluator truth，不产生 baseline 数值；ACL/secret/path hard gate。
- **可观测性**：loader/verifier reason、case/slice/eligible counts、fixture logical counts、
  adapter lane parity、runner path-guard decisions。
- **Flag**：无，不接 production wiring。
- **回滚**：删除未接线新增包/tests/fixtures；V1 无行为变化。
- **验收**：Foundation engineering review P0/P1=0；package 可 release exact 45；
  runner 可走真实生产 adapter/service 但尚无 released Run；正式库零访问。
- **面试证据**：只同步 E0-01 状态；Gate 前不写 Golden Version、Run、指标或结论。

### E-B0 — Flat V1 baseline Run

- **状态**：`PUBLISHED_VERIFIED_NON_QUALIFIED`；唯一 Run
  `evaluation-run://project-experiment-eb0-v1/085799235df74bcdc4fc56c0c17f36e7`。
- **目标**：在 released Foundation 上真实运行当前 V1 string search 和 current comparison。
- **依赖**：E0 Foundation Gate `PASS` 与单独 Run authorization。
- **写锁**：只写新 immutable `evals/experiment/runs/<run-id>/` artifact；禁止改实现、
  Golden 和正式库。
- **Contract**：固定 V1 code/config/fixture/Golden identities；一次 Run 一个 identity；
  failure 也发布 attempt/error truth。
- **执行**：isolated FileStore/Manual lanes→current platform/comparison→evaluation→publish→
  verify-only。
- **专项/回归**：重复 isolated Run determinism、artifact relocation/tamper、V1 endpoint
  response、zero-result/error preservation。
- **Quality/Security**：报告全部 frozen denominator/slices；不因低质量改 truth；绝对路径、
  secret、DB/sidecar、network fetch 为硬门。
- **可观测性**：case latency、candidate count/order、matched terms、comparison fields、
  fallback/error、artifact size。
- **Flag**：固定 default V1。
- **回滚**：Run immutable；失败用独立 correction/attempt audit，不原地重写。
- **验收**：portable verify-only artifact 完整后，才决定 fixed control 是否可接受。
- **面试证据**：只在 verify 后填真实 Run/指标/slices/negative result；当前不得填。

### E1-01 — V2 contracts、additive schema 与 immutable Run snapshot

- **状态**：`ENGINEERING COMPLETE / PRODUCTION MIGRATION EXTERNAL_BLOCKED`；实现见
  `sources/experiment/contracts_v2.py` 与 `store_v2.py`。
- **目标**：稳定 V2 entity/identity/schema/migration contract，completed derived snapshot
  不再被覆盖。
- **依赖**：E-B0 verified。
- **最小文件锁**：
  `rag/sources/experiment/contracts.py`、`experiments/schema.py`、
  `experiments/store.py`、`tests/test_experiment_v2_schema.py`。
- **Contract**：新增 schema version/run snapshot 表；V1 stable ID/表/API 不变；additive
  migration、shadow dual read/write。
- **实现**：migration ledger、content-addressed snapshot、running→completed observed
  versions、orphan/parity checks。
- **专项/回归**：fresh/existing DB、重复 migration、旧 row、completed source drift、
  rollback V1、CRUD/API regression。
- **Quality/Security**：identity/hash deterministic；redacted payload 才进入 snapshot；
  不执行正式 backfill。
- **可观测性**：schema version、migration duration、rows examined/created、parity/orphan、
  snapshot reason。
- **Flag**：`RAG_EXPERIMENT_DUAL_WRITE=off|shadow`，默认 `off`。
- **回滚**：flag off，V1 read/write；保留 additive tables。
- **验收**：旧 endpoint/IDs/rows 无变化；isolated migration/rollback rehearsal 通过。
- **面试证据**：同步实际 schema version/entry point，不写质量提升。

### E1-02 — MetricDefinition/Observation 与 MLflow history

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；registry/history/unit 三态与
  generation persistence 已实现。
- **目标**：建立 registry/alias/unknown direction、Observation history 和 File/REST
  归一化。
- **依赖**：E1-01。
- **最小文件锁**：
  `metric_registry.py`、`experiments/adapters/mlflow.py`、
  `experiments/service.py`、`experiments/store.py`、
  `tests/test_experiment_metric_registry.py`、
  `tests/test_experiment_mlflow_history.py`。
- **Contract**：direction 三态；unit/split/step/time/aggregation/provenance；V1 metric mirror
  继续存在。
- **实现**：显式 registry mapping；未知不默认 higher；File time series；REST history/
  summary availability；NaN/Inf invalid。
- **专项/回归**：unknown/lower metric、ratio-percent、ms-s、duplicate step、out-of-order time、
  summary-only、resync immutability、现有 adapter idempotency。
- **Quality/Security**：raw/normalized parity、secret redaction、invalid observation 不参与
  计算；adapter URI policy 不在本任务扩张。
- **可观测性**：mapped/unknown/invalid counts、history coverage、unit conversion、
  source version。
- **Flag**：依赖 E1 dual-write；V2 read 仍关闭。
- **回滚**：停止 V2 normalization，保留 V1 metric 和已写 Observation。
- **验收**：fixture 两 lane 的 definition/observation truth 对齐；unknown direction 不产生
  best/improvement。
- **面试证据**：同步真实 registry/version/tests；无 treatment 前不写收益。

### E1-03 — Dataset/Config/Environment/Artifact/RunGroup

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；typed condition/artifact/group
  entities 与 isolated store 已实现。
- **目标**：版本化运行条件、Artifact verification state 和 repeated-run grouping。
- **依赖**：E1-01；可与 E1-02 在分离文件锁下准备，E1 Gate 同时依赖二者。
- **最小文件锁**：
  `condition_snapshots.py`、`run_groups.py`、`artifact_versions.py`、
  `experiments/store.py`、
  `tests/test_experiment_conditions_v2.py`、
  `tests/test_experiment_run_groups.py`。
- **Contract**：canonical typed config、dataset digest/split/preprocess、environment
  compatibility fields、seed/fold/repeat、artifact located/verified 分离。
- **实现**：typed flatten/hash、default/missing/type_changed、allowlisted environment、
  explicit group membership。
- **专项/回归**：string/numeric config、missing dataset version、environment key policy、
  seed groups、checksum absent/mismatch、URI inaccessible。
- **Quality/Security**：secret keys永不进入 snapshot/surface；Artifact 不自动 fetch。
- **可观测性**：snapshot completeness、hash/parity、redaction、group/exclusion、artifact
  state counts。
- **Flag**：E1 dual-write shadow。
- **回滚**：V1 JSON fields/Artifact rows继续读取；新 snapshot 保留。
- **验收**：immutable fixture foundation 可被 E2–E5 重用；缺字段显式 missing。
- **面试证据**：同步 condition/RunGroup/Artifact state 实现入口，不称已可复现。

### E2-01 — Typed query contract 与 parser

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；实现见
  `sources/experiment/query_v2.py`。
- **目标**：自然语言/显式 request 只产生 versioned allowlisted `ExperimentQuery`。
- **依赖**：E1 Gate 与被授权 E-T1 evidence。
- **最小文件锁**：
  `query_parser.py`、`contracts.py`、
  `tests/test_experiment_query_parser.py`。
- **Contract**：typed field/operator/unit/aggregation/group/limit；confidence 和 unresolved
  predicate。
- **实现**：先 deterministic explicit syntax/aliases；可选 LLM parser 只能产 schema 且需
  validator，非 P0 依赖。
- **专项/回归**：exact ID、status/dataset/config/metric/range、ambiguous alias、unknown
  field/operator、injection strings、limit。
- **Quality/Security**：predicate accuracy 用 E0 labels；raw SQL 永不进入 AST。
- **可观测性**：parser version、resolved/unresolved predicates、confidence/reason。
- **Flag**：`RAG_EXPERIMENT_QUERY=text-v1|structured-v2`，默认 `text-v1`。
- **回滚**：解析失败或 flag off 回 V1 string query，并显式 trace fallback。
- **验收**：所有 Golden predicates deterministically parse 或给出冻结的 unresolved truth。
- **面试证据**：同步 parser contract/失败例，不写 target 数字。

### E2-02 — Safe compiler 与 exact/structured retriever

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；allowlisted parameterized SQL、
  scope-first filter 与 stable ordering 已实现。
- **目标**：把 validated AST 编译为参数化 SQL，并优先 exact/structured candidate。
- **依赖**：E2-01、E1 schema。
- **最小文件锁**：
  `filter_compiler.py`、`retriever.py`、`experiments/store.py`、
  `tests/test_experiment_filter_compiler.py`、
  `tests/test_experiment_structured_retrieval.py`。
- **Contract**：静态 field registry/joins/operators；bound values；stable ordering；ACL/
  project scope prefilter。
- **实现**：exact ID→structured conditions→metric definition/observation joins；记录 query
  plan hash，不记录 secret values。
- **专项/回归**：all typed operators、null/missing、type mismatch、SQL injection、ACL、
  deterministic tie、scan/limit、V1 fallback。
- **Quality/Security**：exact/filter metrics 对固定 denominator；未经授权 row 不能成为
  candidate。
- **可观测性**：plan hash、rows scanned、candidate counts、index use、fallback reason。
- **Flag**：structured-v2 opt-in/shadow。
- **回滚**：关闭 query flag；不删除索引/表。
- **验收**：参数化 SQL 审计通过；exact/structured 结果不被 semantic 覆盖。
- **面试证据**：同步白名单/参数化证据和 injection test。

### E2-03 — Typed numeric/unit deterministic evaluator

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；unit/direction/missing/NaN/
  zero-baseline truth 已实现。
- **目标**：确定性完成 range/rank/delta/relative/step/unit 运算。
- **依赖**：E1-02、E2-02。
- **最小文件锁**：
  `numeric_evaluator.py`、`tests/test_experiment_numeric_evaluator.py`。
- **Contract**：Decimal/float policy、unit conversion、direction、invalid/missing/zero
  baseline、stable tie-break。
- **实现**：只消费 typed Observation；输出 raw citation、normalized value 和 reason。
- **专项/回归**：higher/lower/unknown、ratio-percent、ms-s、NaN/Inf、baseline zero、
  final/best/latest、多重 tie。
- **Quality/Security**：numeric/unit/direction 使用 frozen cases；unknown 不作方向结论。
- **可观测性**：operator/unit policy、included/excluded observations、invalid reason。
- **Flag**：随 structured-v2；单独 trace `numeric_evaluator_version`。
- **回滚**：fallback 只能返回 raw observations/V1 results，不让 LLM 补算。
- **验收**：同一输入 canonical output 相同；所有 missing/invalid 显式。
- **面试证据**：同步确定性边界和负例；Treatment 前不填准确率。

### E3-01 — Strict comparability policy

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；实现见
  `sources/experiment/analysis_v2.py`。
- **目标**：比较前给出四态 truth 和字段级差异分类。
- **依赖**：E2 Gate 与被授权 E-T2 evidence。
- **最小文件锁**：
  `comparability.py`、`tests/test_experiment_comparability.py`。
- **Contract**：metric/unit/split/step/dataset/code/config/environment/seed/status/sample
  policy version。
- **实现**：controlled/treatment/unknown/disqualifying matrix；missing 进入
  insufficient，不默认 compatible。
- **专项/回归**：每个 hard negative、failed/running、same name/different definition、
  environment caveat、missing seed。
- **Quality/Security**：incomparable precision/recall 与 caveat truth按冻结 denominator；
  ACL-hidden条件不能通过侧信道泄露。
- **可观测性**：decision、policy version、difference classes、missing fields。
- **Flag**：`RAG_EXPERIMENT_COMPARABILITY=basic-v1|strict-v2`，默认 `basic-v1`。
- **回滚**：默认 API 继续 basic-v1；strict result只在 opt-in/shadow。
- **验收**：not/insufficient 不输出 improvement；decision 引用输入 snapshot。
- **面试证据**：同步实际 policy/version/失败 case，不夸大回答率。

### E3-02 — RunGroup aggregation、reproduction 与 comparison V2

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；deterministic group
  aggregation、seven roles 与 strict comparison 已实现。
- **目标**：确定性 group aggregation、reproduction role coverage 和 strict comparison
  projection。
- **依赖**：E3-01、E1-03、E2-03。
- **最小文件锁**：
  `aggregation.py`、`reproduction.py`、`experiments/service.py`、
  `experiments/models.py`、
  `tests/test_experiment_aggregation.py`、
  `tests/test_experiment_comparison_v2.py`。
- **Contract**：ComparisonRequest 只 additive 新增 opt-in mode；V1 default/response fields
  不删改；V2 adds decision/control matrix/roles/citations。
- **实现**：seed/fold include/exclude、mean/std/CI policy、role states、strict result persist。
- **专项/回归**：missing metric/seed/command/commit/artifact、failed high score、N=1、
  unit mismatch、V1 response contract。
- **Quality/Security**：aggregation/included-excluded/reproduction labels；Artifact URI 不作
  verified；ACL 先于 role projection。
- **可观测性**：group identity、N/exclusions、function version、missing roles、decision。
- **Flag**：comparison mode `basic-v1|strict-v2`，default `basic-v1`。
- **回滚**：关闭 strict mode，保留 V2 comparison record；V1 endpoint兼容。
- **验收**：所有结论有 raw observation/snapshot locator；不足时输出 missing truth。
- **面试证据**：同步真实 negative/positive evidence；未 Run 不填指标。

### E4-01 — Versioned semantic surface units

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；实现见
  `sources/experiment/semantic_v2.py`。
- **目标**：为自然语言 alias/objective/hypothesis 建可重建 surface，不承载数值 truth。
- **依赖**：E3 Gate 与被授权 E-T3 evidence。
- **最小文件锁**：
  `unit_builder.py`、`experiments/store.py`、
  `tests/test_experiment_surface_units.py`。
- **Contract**：unit type/source snapshot/text/version/ACL；raw number仅作显示引用。
- **实现**：definition/config/dataset/artifact/experiment/run/comparison surfaces；
  generation prepare/publish/fail。
- **专项/回归**：deterministic ID/text、secret exclusion、ACL、stale generation、
  empty rebuild last-known-good。
- **Quality/Security**：surface 不包含 secret/raw Artifact content；generation atomic。
- **可观测性**：built/skipped/redacted/unit counts、generation status。
- **Flag**：surface build shadow，default V1 search。
- **回滚**：不 publish 或切 last-known-good；V1 flat search不变。
- **验收**：surface→raw snapshot可追溯；没有从 text 反解析 numeric truth。
- **面试证据**：同步 builder/generation 证据，不称 semantic 已发布。

### E4-02 — Optional semantic retriever 与 reranker

- **状态**：`ENGINEERING COMPLETE / EXTERNAL MODEL HOLD`；本地 deterministic semantic
  tail/reranker 已实现，remote embedding 未授权。
- **目标**：改善 alias/description 候选映射，严格位于 structured candidate 之后。
- **依赖**：E4-01、E2 structured retriever。
- **最小文件锁**：
  `retriever.py`、`reranker.py`、
  `tests/test_experiment_semantic_retrieval.py`、
  `tests/test_experiment_reranker.py`。
- **Contract**：versioned sparse/dense profile；exact/structured pinned；semantic tail quota。
- **实现**：alias/sparse first，optional embedding；reranker uses task/definition/dataset/
  completeness/comparability features。
- **专项/回归**：alias hard negative、wrong unit/dataset/split、failed-for-best、provider
  unavailable、deterministic fallback。
- **Quality/Security**：独立消融，不合并 context；ACL prefilter；外部 provider 另授权。
- **可观测性**：channel candidates/scores/profile、pinned structured count、fallback/cost。
- **Flag**：`RAG_EXPERIMENT_EMBEDDING=off|profile`、
  `RAG_EXPERIMENT_RERANKER=off|profile`，默认 `off`。
- **回滚**：两 flag off，保留 exact/structured。
- **验收**：semantic 不改变 structured truth/order contract；provider 失败可用。
- **面试证据**：只同步独立 Run 与失败 slices；不引用论文收益。

### E4-03 — Structured Experiment context

- **状态**：`ENGINEERING COMPLETE / QUALITY UNAVAILABLE`；task-specific structured
  context、citation/budget/missing roles 已实现。
- **目标**：输出 Run/compare/reproduce/aggregate 的结构证据包。
- **依赖**：E3-02；可与 E4-02 分锁准备，E4 Gate 同时依赖。
- **最小文件锁**：
  `context_builder.py`、`tests/test_experiment_context_builder.py`。
- **Contract**：identity/conditions/observations/computed/comparability/missing roles/citations；
  no hidden calculation。
- **实现**：task-specific templates、budget、raw locator、computed provenance。
- **专项/回归**：missing roles、not comparable、unknown direction、large time series、
  citation completeness、deterministic truncation。
- **Quality/Security**：faithfulness/citation/role coverage；无 ACL-hidden field 或 secret。
- **可观测性**：role coverage、included citations、truncation/budget/fallback。
- **Flag**：`RAG_EXPERIMENT_CONTEXT=snippet-v1|structured-v2`，默认 `snippet-v1`。
- **回滚**：snippet-v1；结构化计算结果仍可返回给 opt-in API。
- **验收**：每个数值/decision 都能定位到 Observation/snapshot/policy。
- **面试证据**：同步真实 context/citation failure，不写生成式收益。

### E5-01 — API compatibility、flags 与 Platform opt-in/shadow

- **状态**：`ENGINEERING COMPLETE / OPT_IN / DEFAULT_V1`；stable routing、shadow response
  与 fallback contract 已实现。
- **目标**：把 V2 作为 opt-in/shadow 接到现有 API/Platform，default V1。
- **依赖**：E4 Gate 与被授权 E-T4 evidence。
- **最小文件锁**：
  `experiments/models.py`、`experiments/router.py`、
  `platform/models.py`、`platform/service.py`、
  `runtime.py`、`config.py`、
  `tests/test_experiment_platform_v2.py`、
  `tests/test_experiment_api_compat_v2.py`。
- **Contract**：旧 endpoint/field/type/status code 不变；request additive opt-in；
  response additive `experiment_trace/structured_result/comparability/fallback_reason`。
- **实现**：`RAG_EXPERIMENT_ENGINE=v1|shadow|canary|v2` 默认 `v1`；request/project override
  allowlist；shadow 不改变用户结果。
- **专项/回归**：V1 golden response snapshots、invalid override、shadow parity/divergence、
  ACL、timeout/fallback、all existing API/Platform tests。
- **Quality/Security**：shadow 输出只进受控 audit；不得将 V2 hidden candidate 泄漏到 V1。
- **可观测性**：engine requested/selected、shadow outcome、divergence、fallback/latency。
- **Flag**：engine default `v1`，canary percent default 0。
- **回滚**：engine=v1；request-level fallback；last-known-good V1。
- **验收**：默认请求 byte/contract compatible；V2 只有显式 opt-in。
- **面试证据**：同步 wiring/shadow engineering，不写 release。

### E5-02 — Sync generation、security 与 observability

- **状态**：`ENGINEERING COMPLETE / PRODUCTION SYNC EXTERNAL_BLOCKED`；atomic generation、
  path/SSRF、sanitized trace 已实现。
- **目标**：原子 sync generation、File/REST policy、Artifact/ACL 安全和完整 trace。
- **依赖**：E5-01、E4-01 generation contract。
- **最小文件锁**：
  `experiments/adapters/mlflow.py`、`experiments/service.py`、
  `experiments/store.py`、`security.py`、`observability.py`、
  `tests/test_experiment_sync_generation.py`、
  `tests/test_experiment_security_v2.py`。
- **Contract**：prepare/publish/fail/last-known-good、source watermark、partial/complete、
  allowlisted local root/remote host、artifact authorization。
- **实现**：batch atomic visibility、deleted tombstone、completed immutable observation、
  SSRF/path policy、safe trace redaction。
- **专项/回归**：partial failure、empty sync、running→completed、deleted lifecycle、path
  escape、redirect/private host、secret config、artifact ACL。
- **Quality/Security**：unauthorized leakage 绝对硬门；Artifact default no-fetch。
- **可观测性**：source/generation/watermark/count/drift/error、rows/latency/cost、fallback。
- **Flag**：sync V2 shadow；engine仍 default V1。
- **回滚**：不 publish failed generation；切 last-known-good；保留 failure diagnostics。
- **验收**：atomic/ACL/security/rollback rehearsal 通过，正式 service仍未自动授权。
- **面试证据**：同步真实安全/失败 evidence，不称 production released。

### E5-03 — Canary、release decision 与 rollback rehearsal

- **状态**：`ENGINEERING COMPLETE / HOLD_DEFAULT_V1`；side-effect-free canary/release
  evaluator 与 rollback rehearsal 已实现，production canary 未授权。
- **目标**：用固定 E-B0 和逐阶段 treatment 形成单源 release evidence。
- **依赖**：E5-01/E5-02 Engineering Gate；production canary 另需显式授权。
- **写锁**：
  `release.py`、`tests/test_experiment_release_v2.py`、
  新 immutable `evals/experiment/runs/<run-id>/` 与 release record；默认不改其他来源。
- **Contract**：off/V1、shadow、canary、on/V2；project/request override；quality/security/
  latency/storage/cost/rollback evidence；decision immutable。
- **执行**：先 isolated shadow/canary rehearsal，再经授权的 scoped canary；每次 Run
  分离变量并 verify-only。
- **专项/回归**：routing determinism、canary percent、fallback、timeout、last-known-good、
  rollback、V1 default、artifact tamper。
- **Quality/Security**：引用 E0 frozen denominators和设计门槛；任何安全泄漏、truth drift、
  missing artifact 都 HOLD。
- **可观测性**：exposure、engine、decision、fallback、quality slices、P95/storage/cost。
- **Flag**：只有 verified release decision 才允许改变 default；当前 default V1。
- **回滚**：一键 V1、project override off、last-known-good、保留失败 Run/decision。
- **验收**：真实证据支持 `RELEASE/HOLD/REJECT` 之一；无证据只能 HOLD。
- **面试证据**：只同步真实 Run、指标、失败和 decision；不填目标值冒充结果。

## 13. Gate、Evaluation 与 evidence 统一规则

每阶段固定顺序：

```text
Issue/PR implementation
→ 专项 + 回归 + quality-contract + security tests
→ Engineering Gate
→ explicit Run authorization
→ isolated immutable Treatment Run
→ portable verify-only
→ slice/error/latency/storage/cost review
→ QUALIFIED / NON_QUALIFIED / UNAVAILABLE
→ next-stage authorization
```

每个 Gate 的 Evidence Card 必须包含：

- Issue/PR 和实际 file lock；
- code identity；
- schema/registry/policy/model/profile version；
- Golden/package identity；
- fixed E-B0 与 direct-parent Run identity；
- test/quality/security结果；
- case/slice denominator 与 unavailable；
- latency/storage/cost；
- observability trace；
- rollback rehearsal；
- interview sync diff。

禁止：

- baseline 前效果实现；
- smoke 冒充 released Run；
- engineering complete 冒充 treatment qualified；
- overall metric 掩盖 slice/unavailable；
- overwrite failed/negative artifact；
- 把正式库既有 0 当作本轮重新测量；
- 把 Artifact URI、Notebook Output、Document number 或 Code body 变成未验证的
  Experiment truth。

## 14. API compatibility 与 release modes

必须保持：

- 当前 `/v1/experiments*` endpoints；
- 当前 Manual/MLflow request fields；
- 当前 V1 stats/source/run/comparison response fields；
- 当前 `/v1/search` default behavior；
- client 未发送 opt-in 时 default V1。

目标 release modes：

```text
v1/off
shadow
canary
v2/on
```

shadow 只记录 V2 candidate/result/trace，不改变 V1 response。canary 必须 deterministic、
project-scoped、可立即 fallback。任何 V2 exception、timeout、schema mismatch、
insufficient truth 或 last-known-good 不可用都回 V1，并保留 reason。

Deprecation 只在 V2 稳定观察期后另立计划；E5 不删除 V1。

## 15. 测试、质量、安全与可观测性矩阵

| 类别 | 必测 |
| --- | --- |
| Foundation | exact 45、slice count、membership、denominator、locator、manifest/tamper |
| Fixture | FileStore+Manual parity、multi-run/seed、time series、stable logical IDs |
| Registry | unknown direction、unit、split、step、aggregation、raw/normalized provenance |
| Numeric | NaN/Inf、ratio-percent、ms-s、zero baseline、tie、missing |
| Conditions | dataset version、typed config、environment compatibility、seed/group |
| DSL | whitelist、parameterized SQL、type/operator/limit、injection、ACL prefilter |
| Comparability | four states、difference roles、failed/running、missing metadata |
| Reproduction | command/commit/dataset/config/environment/seed/artifact roles |
| Semantic | alias、wrong unit/dataset/split、structured pin、provider fallback |
| Context | citation/role coverage、deterministic budget、no hidden calculation |
| Sync | idempotency、history、partial/empty/deleted、atomic generation |
| Security | secret、path traversal、SSRF、artifact ACL、unauthorized leakage |
| Compatibility | all V1 endpoint/field/status/default/search/comparison contracts |
| Release | shadow divergence、canary routing、fallback、last-known-good、rollback |

最小 trace fields：

```text
request/query ID
project/source/ACL scope
engine requested/selected
parser/query-plan/policy versions
resolved/unresolved predicates
exact/structured/semantic candidate counts
rows scanned and latency by stage
numeric/aggregation/comparability reason
missing reproduction roles
generation/watermark
fallback/exception
```

trace 不得记录 secret config values、credential、raw unauthorized payload 或 Artifact 内容。

## 16. 最小文件锁总表

| 阶段 | 默认 owner | 禁止顺带修改 |
| --- | --- | --- |
| E0-01 | 新 Experiment evaluation/fixture/baseline 包、专项 tests/fixtures | runtime/API/config/schema/platform/正式库 |
| E-B0 | 新 immutable Run artifact | 实现、Golden、正式库 |
| E1 | contracts/registry/conditions/run groups + Experiment schema/store/service/adapter 的最小改动 | DSL/platform/context |
| E2 | parser/compiler/retriever/numeric evaluator + 专项 tests | semantic/context/release |
| E3 | comparability/aggregation/reproduction/comparison opt-in | platform release/semantic |
| E4 | unit/surface、semantic/reranker、context 分离锁 | schema cleanup/API default |
| E5 | API/config/runtime/platform wiring、sync/security、release controller 分离锁 | 其他来源、V1 cleanup、正式库 |

每个 PR 开始前必须重新检查 dirty worktree；表外修改视为用户所有，不覆盖、不格式化、不
顺手清理。文档同步只允许本计划与对应 Experiment interview 文件，不改其他来源/review。

## 17. 面试证据同步

当前只允许在 `CURRENT_PLAN` 同步：

- 本开发计划链接；
- E0-01 Foundation `PASS / COMPLETE`；
- E-B0 verified immutable artifact 与 `NON_QUALIFIED` 真值；
- E1–E5 repository engineering `COMPLETE / QUALITY UNAVAILABLE`；
- 没有 E1–E5 production treatment/quality qualification/default V2。

未来同步规则：

| Gate/Run | 可同步 | 仍禁止 |
| --- | --- | --- |
| E0 Foundation | released Golden identity、exact counts、fixture/evaluator evidence | smoke 叫 baseline |
| E-B0 | verified Run、真实 negative/positive baseline、slices/errors | 先填目标值或优化收益 |
| E1 | schema/registry/snapshot 实现与 E-T1 | unknown direction 冒充 higher |
| E2 | DSL/numeric 实现与 E-T2 | LLM 计算冒充 deterministic |
| E3 | comparability/aggregation/reproduction 与 E-T3 | 不可比 Run 宣称提升 |
| E4 | semantic/context 独立消融与 E-T4 | semantic 覆盖结构 truth |
| E5 | shadow/canary/rollback/decision | engineering complete 冒充 released |

所有简历数字只能来自 verify-only artifact 的真实测量；目标设计阈值不进入 CURRENT。

## 18. 本计划完成定义

本开发计划文档完成需满足：

- 映射当前 runtime/API/models/schema/service/store/File+REST adapters/platform/tests/
  evaluation；
- 保留正式数据 0 的既有真值，同时明确本轮未读正式库；
- 明确 Commit/Notebook/Document/Artifact 的跨源边界；
- 按 E0→E-B0→E1→E2→E3→E4→E5 排序；
- exact 45、八 slice、programmatic multi-run/multi-seed FileStore+Manual fixture、
  frozen truth/denominator/locator 和 content-addressed release 均可执行；
- E-B0 精确运行 current V1 search/comparison 并输出 portable verify-only artifact；
- Metric Registry、unknown direction、unit/time-series/NaN/Inf/ratio-percent 完整；
- additive schema、isolated migration/dual read-write/rollback 完整；正式库
  migration/backfill 明确 external blocked；
- safe DSL、parameterized SQL、numeric evaluator、comparability、aggregation、
  reproduction、semantic/context 和 release 任务均有 Issue/PR 级 contract；
- 每项有文件锁、依赖、测试、quality/security、observability、flag、rollback、
  acceptance 和 interview evidence；
- 状态如实写为 E0/E-B0 已有 evidence、E1–E5 repository engineering complete；
  production treatment/release 保持 unavailable/HOLD；
- 没有虚构 E1–E5 treatment Run、提升指标、qualification、正式库读取或“不变”声明。

当前结论：E0 Foundation PASS；E-B0
`PUBLISHED_VERIFIED_NON_QUALIFIED`；E1–E5 repository engineering COMPLETE；
production migration/treatment/shadow/canary/default switch
`EXTERNAL_BLOCKED / QUALITY_HOLD / DEFAULT_V1`。
