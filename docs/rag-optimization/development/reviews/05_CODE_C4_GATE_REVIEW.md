# Code C4 Gate Review

C4-01 独立 Gate 时间：2026-07-27 22:28 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

## C4-01 当时裁决（授权开始 C4-02）

```text
C4-01 Edge Ontology + Diagnostics: PASS
P0/P1 findings: 0
C4-02 Typed Graph Retriever AUTHORIZED
```

## C4-03 Query Profile / Fusion 独立 Gate（2026-07-28 01:55 +0800）

### 最终裁决

```text
C4-03 Query Profile / Fusion: PASS
P0/P1 findings: 0
C4 overall engineering COMPLETE
C5-01 AUTHORIZED
C-B3 remains NOT QUALIFIED
```

本轮只审
`src/evidence_rag/rag/sources/code/query_profile_v2.py`、最小 package export 与
`tests/test_code_query_profile_v2.py`。未发现阻断主流程的 P0/P1；C-B3 的零 accepted
path 继续作为负向质量证据，不解释为 graph recovery 或收益。

### 八类冻结、版本化 profile：PASS

registry 是不可变映射，八个 `CodeTask` 各有且只有一个 frozen
`CodeQueryProfileV2`；profile、rewrite、pipeline 均发布明确 v2 version。独立枚举确认
channel 与非零预算完全一致，graph edge 白名单逐项通过 C4-01 task registry：

| task | channels | E/S/D/G/H/T；node/edge | hops | required roles | adaptive-k min/default/max | missing role |
|---|---|---|---:|---|---|---|
| exact location | exact | 20/0/0/0/0/0；0/0 | 0 | target | 1/3/8 | degrade |
| implementation | exact/sparse/dense/graph | 20/40/40/12/0/0；48/96 | 1 | target | 3/8/14 | degrade |
| call/reference path | exact/sparse/dense/graph | 20/36/32/24/0/0；96/192 | 3 | target/dependency | 4/10/18 | degrade |
| bug localization | exact/sparse/dense/graph/test | 20/40/40/24/0/16；96/192 | 2 | target | 5/12/20 | degrade |
| impact analysis | exact/sparse/dense/graph | 16/36/36/28/0/0；128/256 | 3 | target/dependency | 6/14/24 | refuse |
| change context | exact/sparse/dense/graph/history | 16/36/32/20/20/0；96/192 | 2 | target/history | 5/12/20 | degrade |
| historical | exact/sparse/dense/graph/history | 16/32/28/20/28/0；96/192 | 3 | history | 5/12/22 | refuse |
| test/validation | exact/sparse/dense/graph/test | 16/36/32/24/0/28；112/224 | 2 | target/test | 5/12/20 | refuse |

七个 graph profile 都明确启用 incoming/outgoing；edge 仅使用各 task 获准关系，
包括 definitions/import/implementation、calls/references、test/coverage、change
validation/lineage 等相应集合。每个 profile 另有确定的 rewrite policy、bounded
context template、adaptive-k、degrade/refuse policy；exact profile 关闭 calibration，
其余 profile 只把 calibration 定义为 optional。相同输入的任务推断、规范化 rewrite、
path 接受/拒绝和 profile resolution 序列化结果稳定；控制字符和非受管 locator
fail closed。

### production fusion 与治理：PASS

`CodeSourceFusionPipeline` 的实际顺序为 profile/rewrite、exact、现有 hybrid
sparse+dense、seed fusion、typed graph、可选 history/test hook、entity/unit/version
dedup、现有 deterministic reranker、calibration、adaptive-k、严格
`CodeSourceResult`。独立代码路径和正向测试确认 graph-required profile 的真实 graph
hits 会进入 candidate generation，而不是只产生 trace：

- simple exact fast path 的 dense 与 graph 都是 `skipped`；exact/locator 是排序与去重
  的硬信号，不会被较弱 graph/dense 信号覆盖；
- graph request 携带 repo/version/generation、双端 ACL、edge/direction/hops、
  node/edge/deadline budgets；最终候选再次执行 ACL、repo、version/generation
  governance，按 entity/unit/version 确定去重；
- history/test 只通过显式 optional hook 接入；缺 hook、provider 或 artifact 时分别
  记录 skipped/unavailable，不伪称 channel available；
- hybrid/graph/rerank/calibration 的 skip、no-match、timeout、error、fallback、
  degrade/refuse 都进入 typed trace 与 channel outcome；refuse 会清空候选，不能留下
  看似成功的部分结果。

使用隔离临时库、但接入真实 `CodeExactSparseRetriever`、
`CodeHybridV2Retriever`、`CodeTypedGraphRetriever` 和
`DeterministicCodeReranker` 的 production-component probe 返回：

```text
exact=complete|dense:skipped|graph:skipped|candidates:10|strict_result:true
impact=partial|hybrid:complete|graph:complete_no_match|rerank:complete|
       calibration:unavailable|required_role:refused|
       graph_candidates:0|candidates:0|strict_result:true
```

针对 trace/fail-closed 的独立负向 probe 返回：

```text
missing_providers=partial|dense:unavailable|graph:unavailable|calibration:unavailable
graph_timeout=partial|graph:timeout|required_role:degraded|CodeChannelTimeout
zero_path_impact=partial|graph:complete_no_match|required_role:refused|
                 graph_candidates:0|candidates:0
```

C-B3 artifact 的 `accepted_path_count=0`；上述真实 no-match 和显式 zero-path probe
均未制造候选、path explanation 或 graph recovery。因此 C-B3 仍为
`NOT QUALIFIED`，不以 4,101 edge / 719 node 扫描量冒充有效路径。

### 边界、验证与只读性：PASS

- 实现只组合既有 C3/C4 retriever、graph、reranker/calibration contract；未实现
  C5、runtime/store、SCIP/LSP 或 SQLite 写路径。
- 独立最小相关集合共 `163 passed`：
  `test_code_query_profile_v2.py`、`test_code_graph_retrieval_v2.py`、
  `test_code_dense_v2.py`、`test_code_rerank_v2.py`、
  `test_code_rag_contracts.py`、`test_code_cb3_run.py`。
- 目标三文件 `ruff check` 通过且 `ruff format --check` 为 already formatted。
  全仓既有 `rag_ui_v2` 锁外格式问题不属于本 Gate，也不影响本轮功能裁决。
- 三个只读目标在报告写入前的 SHA-256 分别为
  `e9b62b955c2f1fb644429f08a8fcbe1aeb3a6ccf06fa106a6f703ce5b45855d1`、
  `d7217c1a3d04f771695f6a38cf8cde5dc717311c0d721d627cd98fcbfbf05e52`、
  `9e77c00ee3cb1e1920ce86eb8fdd9400c1daf7baee57ba74aa967eb9099c4e39`。
- 审核全程保持 `main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`、
  单 worktree；正式库审前保持 size `1915490304`、mtime
  `2026-07-27 15:11:03 +0800`、SHA-256
  `9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b`，
  Run 树只读基线的 state/content SHA-256 为
  `58faf08cc19a028da5b0b5cb764bab3c5e94711fa0c46a0d03859817b0598c29` /
  `5a49a84648c67849b5017898f6ad86d5251ab4388e302b46aa69a790d0247e95`。

报告主体落盘后再次计算，三个目标文件和正式库的上述 size/mtime/hash 均与审前
一致；全 Run 树 state/content 仍精确为上述两个值，目标 C-B3 artifact state 仍为
`bdb55a280e4172e8a3f92dd271445a4d6769a640eb9605834365e3532e8e08d2`。
本 Gate 未创建或改写任何 Run、eval、Golden、数据库、实现、测试或 Git 状态。

## C-B3 最终只读 Run 审核（2026-07-28 01:10 +0800）

### 最终 Gate 裁决

```text
C4-01 PASS（保持）
C4-02 engineering COMPLETE
C-B3 production artifact audit: PASS
C-B3 NOT QUALIFIED audit
P0/P1 artifact findings: 0
C4-03 AUTHORIZED
```

本裁决确认 C4-02 的 engineering contract 已完成，但不把 C-B3 treatment 认定为
qualified，也不把零 path recall 或 graph 扫描量解释为收益。C-B3 的真实质量结果是
`NOT QUALIFIED`；到此停止以质量结果反复打开 C4-02 engineering 边界。

### Gate authorization 可移植性 P1：CLOSED

上轮发现的“追加本报告会令既有 artifact 失去可验证性”已关闭：

- 新执行将 Gate decision、固定 evidence 和 exact production component identity 规范化为
  自包含 `normalized-v1` authorization snapshot，并独立校验 evidence/snapshot hash；
- 既有 `92f8...` artifact 不读取当前可追加、缺失或移动后的 Gate 文件，改由
  `strict-legacy-v1` 交叉校验 artifact 内 decision、production identity、attempt audit
  和 passed acceptance evidence；
- decision、snapshot hash、production identity、attempt binding 以及 fake legacy
  authorization 篡改全部 fail closed；
- 未授权 execute 和 injected/fake/wrapped/subclassed retriever 仍在创建 Run 目录前拒绝。

追加前报告 SHA-256：
`7a9626337458bbe269dea1ab308c971d9d5491475402552e7a43160b894e51d0`。
追加前独立 verify 返回：

```text
status=verified
authorization_mode=strict-legacy-v1
authorization_decision=C-B3 PRODUCTION EXECUTION AUTHORIZED
status_label=NOT QUALIFIED
treatment_qualified=false
```

即使在进程内将当前 Gate 读取函数替换为必然报错，既有 artifact 仍以上述
`strict-legacy-v1` 结果通过；decision、attempt 和 identity 的内存篡改 probe 均被拒绝。
本节最终文本写入后的工作区 verify 结果：
`PASS / status=verified / authorization_mode=strict-legacy-v1 /
status_label=NOT QUALIFIED / treatment_qualified=false`。

### Artifact、分母、anchors 与隔离：PASS

目标：
`evaluation-run://project-code-golden-v2/92f8d8e190f449bab9ba253227473419`

```text
artifact status: NOT QUALIFIED
treatment_qualified: false
artifact files: 11 regular files
artifact_set_hash:
  sha256:3327754cf28576692816f9d48173df2165e37155f4f8c932377e07fbed3c3439
manifest canonical hash:
  sha256:2d97fe6ebe02e76e6646d2729a6bcc0acccca061a6ccf0e912bc4d8d3c5d27aa
production C-B3 artifact directories: 1
```

artifact 内 SQLite `integrity_check=ok`，四个独立 treatment Runs 均为
`completed / case_count=33 / results=33`，且共享同一个固定 33-case membership：

- `graph_off`
- `graph_post_only`
- `typed_graph`
- `graph_reranker`

C-B0 `5a92...` 是唯一 `treatment_qualified=true / qualification_use=required`
baseline；C-B1 `33f7...`、C-B2 `6fad...`、C-B5 `baf5...` 全部为
`treatment_qualified=false / qualification_use=forbidden` audit controls。

11 个 entries 均为普通文件，没有 symlink、WAL/SHM、pyc/pycache。security report：

```text
clean=true
network_used=false
downloads_performed=false
portable_sqlite=true
temporary_path_findings=0
credential_assignment_findings=0
SQLite TEXT columns/values scanned=874/92244
```

正式 `var/evidence-rag.sqlite3` 在审核前保持：

```text
size=1915490304
mtime=2026-07-27 15:11:03 +0800
sha256=9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b
evaluation_runs/results/metric_values=0/0/0
```

### 真实质量、path truth 与 trace：NOT QUALIFIED

| treatment | Entity Recall@10 | Locator Recall@10 | MRR@10 | nDCG@10 | Query P95 ms |
|---|---:|---:|---:|---:|---:|
| graph off | 0.727273 | 0.568182 | 0.350479 | 0.420905 | 570.803 |
| graph + reranker | 0.727273 | 0.568182 | 0.397455 | 0.453732 | 692.525 |
| treatment − graph off | 0 | 0 | +0.046976 | +0.032827 | +121.721 |

相对唯一 qualified C-B0，graph + reranker 的四个核心指标分别回退
`-0.113636 / -0.272727 / -0.234729 / -0.151081`。同轮 reranker 排序提升不足以抵消
基线质量回退和 latency 成本。

33 个 eligible cases 中只有 12 个有 released typed path truth，另 21 个保持
unavailable。12 个可评 cases 的 Required Path Recall/Precision 均为 `0`；
accepted path 为 `0`，graph-only recovery 为 `0`。`graph_noise_rate@10` 没有分母，
正确记录为 `status=unavailable / value=null`，没有把缺失证据虚构为零噪声。

三个启用 graph 的 treatment 各有 27 个 production traces；graph + reranker 记录
`4101` examined edges、`719` expanded nodes、`cycle=1001`、`budget=1095` prunes，
但 accepted path 仍为 `0`。这些计数只证明真实 traversal 与 budget/cycle 行为，不是
有效路径、召回或质量收益。

主控报告的临时 fail-closed 尝试未持久化；本 Gate 可直接确认最终 Run 树只有上述一个
C-B3 artifact。该过程说明不作为质量证据，也不因最终 artifact 真实、唯一且可验证而
阻塞 engineering completion。

### 独立验证与写锁

```text
tests/test_code_cb3_run.py: 13 passed
two-file ruff check: PASS
two-file ruff format --check: PASS
Gate-independent strict-legacy verify probe: PASS
decision / attempt / identity tamper probes: REJECTED
unauthorized execute: REJECTED before Run directory creation
```

追加前目标 artifact state/content fingerprints：

```text
state:   bdb55a280e4172e8a3f92dd271445a4d6769a640eb9605834365e3532e8e08d2
content: 2c9f4bce8f6a76f29a5a6a439641d4c9153d71fa0e5f808cc1e70eb180b53eb2
old Runs excluding C-B3 state:
  82be51d19edbe3286dcfabf328066a486729f6e594073e1d2dbfd38511c4d605
old Runs excluding C-B3 content:
  f7d89eb424c87fff88b6d424bb931bf5f374a42fad047ed81ceb3fa394284517
```

除本报告外，本 Gate 不修改代码、测试、配置、依赖、其他文档、eval/Run、Golden、
正式数据库或 Git；不创建 branch/worktree，不 commit/push。

## C4-02 第二轮极简复审（2026-07-27 23:50 +0800）

### 最新 Gate 裁决

```text
C4-01 PASS（保持）
C4-02 PASS
P0/P1 findings: 0
C-B3 PREPARED PASS（NON-QUALIFIED；尚无 Run/指标）
C-B3 PRODUCTION EXECUTION AUTHORIZED
C4-03 AUTHORIZED
```

首轮两个 P1 均已关闭，主流程未发现 P0/P1 回归：

1. `adjacent()` 返回空集或非空集但在调用期间越过 deadline 时，retriever 都在消费
   edge 前统一收敛为 `status=deadline`、`deadline_exceeded=True`，并返回零
   candidate、零 path、零 examined edge。独立 fake-clock probe 两条路径均只调用一次
   adjacency，结果完全一致；新增参数化专项也覆盖两种返回值。
2. 包级精确 `CodeTypedGraphRetriever` export 与
   `graph_retrieval_v2.CodeTypedGraphRetriever` 是同一类对象。C-B3 preparation 将生产
   component 报告为 `ready`，整体仍为 `PREPARED / NON-QUALIFIED`；真实 exact-type
   identity 被接受，fake、wrapped、subclassed 以及 injected factory 均继续
   fail closed。

独立最小验证：

```text
tests/test_code_graph_retrieval_v2.py: 18 passed
tests/test_code_cb3_run.py: 7 passed
tests/test_code_graph_v2.py: 8 passed
five-file ruff check: PASS
five-file ruff format --check: PASS
git diff --check: PASS

slow-empty:
  deadline / deadline_exceeded=true / candidates=0 / paths=0 / edges_examined=0
slow-nonempty:
  deadline / deadline_exceeded=true / candidates=0 / paths=0 / edges_examined=0
public exact class identity: true
C-B3 preparation:
  production_component=ready
  status=PREPARED
  qualification=NON-QUALIFIED
  created Run/artifact/metrics=false/false/false
fake/wrapped/subclassed/injected: rejected
```

五个只读目标文件在验证前后 SHA-256 不变。正式 SQLite 仍为
`9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b`，Code Run
树状态仍为
`82be51d19edbe3286dcfabf328066a486729f6e594073e1d2dbfd38511c4d605`；未出现 C-B3
Run、artifact 或指标。本轮除本报告外没有写入任何文件，也没有执行
branch/worktree/commit/push。

## C4-02 + C-B3 PREPARED 精简复审（2026-07-27 23:13 +0800）

### 首轮 Gate 裁决（已由上方第二轮裁决取代）

```text
C4-01 PASS（保持）
C4-02 completion: BLOCKED
C-B3 PREPARED: BLOCKED
C-B3 PRODUCTION EXECUTION: NOT AUTHORIZED
C4-03: NOT AUTHORIZED
P0 findings: 0
P1 findings: 2
```

本轮仅列 P0/P1。没有修改实现、测试、配置、依赖、其他文档、eval/Run、Golden 或正式
数据库；没有 branch/worktree/commit/push。

### [P1] Slow-empty adjacency 越过 deadline 后被错误报告为 no-match

位置：
`src/evidence_rag/rag/sources/code/graph_retrieval_v2.py:926`、
`src/evidence_rag/rag/sources/code/graph_retrieval_v2.py:936`、
`src/evidence_rag/rag/sources/code/graph_retrieval_v2.py:1013`

最小复现使用 ready publication、`deadline=1.0`、初始 clock `0.0`；provider 的
`adjacent()` 在返回空 tuple 前把 clock 推进到 `2.0`。调用前 deadline check 通过；
空结果不进入 line 936 的 edge loop，因此没有执行 line 940 的 deadline check，最终
line 1013 仍按未置位的 `deadline_exceeded` 选择 `NO_MATCH`。

```text
status=no_match
deadline_exceeded=False
adjacency_calls=1
clock_after=2.0
```

预期是 `status=deadline` 且 `deadline_exceeded=True`。空 adjacency 是正常主流程结果，
SQLite 查询也可能在调用期间耗尽 budget；当前结果会把 timeout 伪装成真实无匹配，
破坏 deadline、fallback 和 trace 语义。现有专项只覆盖“调用前已经过期”，没有覆盖
“查询期间过期且返回空集”。

### [P1] C-B3 要求的公开生产 identity 未 export，获批 flag 也无法执行

位置：
`src/evidence_rag/rag/sources/code/__init__.py:217`、
`src/evidence_rag/evaluation/cb3.py:55`、
`src/evidence_rag/evaluation/cb3.py:438`、
`src/evidence_rag/evaluation/cb3.py:761`

C-B3 把唯一合法生产类固定为 `CodeTypedGraphRetriever`，但包级 lazy export 只公开
`CodeGraphRetriever` / `CodeGraphV2Retriever` alias，没有公开该 exact name。

最小只读 probe：

```text
public.CodeGraphRetriever is direct CodeTypedGraphRetriever: True
hasattr(public, "CodeTypedGraphRetriever"): False
preparation_status()["production_component"]["status"]: pending
reason: public production class is not exported yet

execute_cb3(c4_02_gate_approved=True):
  CB3Error: production CodeTypedGraphRetriever is not ready
```

因此 `require_production_retriever_identity()` 当前不可能接受任何实例，C-B3 即使收到本
Gate approval flag 也无法越过 production identity 检查。现有 C-B3 专项显式容忍
`production_component=pending`，没有验证 C4-02 完成后的 ready 条件。

### 独立验证与零写入记录

```text
tests/test_code_graph_retrieval_v2.py: 16 passed
tests/test_code_cb3_run.py: 7 passed
five-file ruff check: PASS
five-file ruff format --check: PASS

max_hops=4 / six deterministic runs:
  entities=b,c,d,e
  scores=0.72,0.5184,0.373248,0.26873856
  PASS

C-B3 four arms:
  graph_off, graph_post_only, typed_graph, graph_reranker
same denominator:
  33 per arm
  sha256:a9551e3b77822fd67f11c32dffa04e6b0069cd88e44adbfabfb6cd12bd343aaa
prepare created Run/artifact/metrics:
  false / false / false
evals/code/runs tree state hash before/after:
  82be51d19edbe3286dcfabf328066a486729f6e594073e1d2dbfd38511c4d605

formal SQLite before/after:
  size=1915490304
  mtime=2026-07-27 15:11:03 +0800
  sha256=9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b
```

跨 generation lineage 继续 fail closed，不属于本轮 blocker。五个只读目标文件 SHA-256
在复审前后保持一致。

C4-01 已提供封闭、不可变、可确定序列化的 17 类 Code edge ontology，以及不丢永久
aggregate、按 source/relation 稳定限量采样的 unresolved diagnostics。未知/自由 edge
fail closed；semantic derivation 不能声明 `CALLS`；local variable/parameter 不在 entity
type 或 symbol kind vocabulary 中。

本裁决只授权开始 C4-02，不表示 graph traversal、Graph Retriever、query fusion 或 C5
SCIP/LSP semantic resolver 已经实现，也不改变 C-B0 仍为唯一 qualified baseline 的既有
结论。

## 审查范围与写锁

本轮只读审查：

- `src/evidence_rag/rag/sources/code/graph_v2.py`
- `src/evidence_rag/rag/sources/code/__init__.py` 中 C4-01 最小公开 export
- `tests/test_code_graph_v2.py`
- 只读对照 `contracts.py`、C0-C3 contract tests、C4 计划和 Code Source 目标设计

除本报告外没有修改代码、测试、配置、依赖、其他文档、eval/Run、schema/store 或数据库。
未创建或切换 branch/worktree，未 commit/push。工作区在 Gate 开始前已有大量主控及其他
会话改动，本 Gate 未覆盖、清理或归属这些改动。

`rg` 交叉引用确认 C4-01 新符号只进入 `graph_v2.py`、包级最小 export 和专项测试；没有
接入 ingestion、store、retrieval、evaluation 或生产运行路径。

## 17 类 Edge Registry 审计

### 完整性、身份与方向

`CodeRelationType` 与 registry 一一对应，顺序和成员均为 17/17；registry 使用
`MappingProxyType`，spec 使用 frozen contract model。每条 spec 都显式定义
source/target、direction、inverse、owner、derivation、confidence、version、
generation、allowed tasks、cost 与 review requirement。

| Edge | Source → Target | Direction / inverse | Owner | Version / generation |
| --- | --- | --- | --- | --- |
| `DEFINES` | `FileVersion, CodeSymbol` → `CodeSymbol, CodeRetrievalUnit, TypeEntity` | forward / `DEFINED_BY` | source | same stable / same generation |
| `CONTAINS` | `Repository, Commit, GitCommit, Worktree, FileVersion, CodeSymbol, CodeRetrievalUnit` → `FileVersion, CodeSymbol, CodeRetrievalUnit, DiffHunk, TestResult` | forward / `CONTAINED_BY` | source | same stable / same generation |
| `IMPORTS` | `FileVersion, CodeSymbol, PackageModule` → `FileVersion, CodeSymbol, PackageModule, Dependency` | forward / `IMPORTED_BY` | source | same stable / same generation |
| `CALLS` | `CodeSymbol` → `CodeSymbol` | forward / `CALLED_BY` | source | same stable / same generation |
| `REFERENCES` | `FileVersion, CodeSymbol` → `FileVersion, CodeSymbol, TypeEntity, ConfigKey` | forward / `REFERENCED_BY` | source | same stable / same generation |
| `PARENT_OF` | `CodeSymbol, CodeRetrievalUnit` → `CodeSymbol, CodeRetrievalUnit` | forward / `CHILD_OF` | source | same stable / same generation |
| `TYPE_OF` | `CodeSymbol` → `TypeEntity` | forward / `HAS_TYPE` | source | same stable / same generation |
| `IMPLEMENTS` | `CodeSymbol, TypeEntity` → `CodeSymbol, TypeEntity` | forward / `IMPLEMENTED_BY` | source | same stable / same generation |
| `OVERRIDES` | `CodeSymbol` → `CodeSymbol` | forward / `OVERRIDDEN_BY` | source | same stable / same generation |
| `TESTS` | `CodeSymbol, TestCase, ValidationTarget` → `FileVersion, CodeSymbol, TypeEntity` | forward / `TESTED_BY` | source | same stable / same generation |
| `COVERS` | `TestResult, Coverage` → `FileVersion, CodeSymbol, CodeRetrievalUnit` | forward / `COVERED_BY` | source | exact target / target aligned |
| `AFFECTS` | `GitCommit, DiffHunk, ChangeSet` → `FileVersion, CodeSymbol` | forward / `AFFECTED_BY` | source | exact target / target aligned |
| `VALIDATED_BY` | `Commit, GitCommit, Worktree, ChangeSet, ValidationTarget` → `TestResult` | forward / `VALIDATES` | source | exact target / target aligned |
| `FAILED_VALIDATION` | `Commit, GitCommit, Worktree, ChangeSet, ValidationTarget` → `TestResult` | forward / `HAS_FAILED_VALIDATION` | source | exact target / target aligned |
| `SAME_SYMBOL_AS` | `CodeSymbol` ↔ `CodeSymbol` | bidirectional / self-inverse | both | explicit transition / explicit generations |
| `RENAMED_TO` | `CodeSymbol` → `CodeSymbol` | forward / `RENAMED_FROM` | source | explicit transition / explicit generations |
| `MOVED_TO` | `CodeSymbol` → `CodeSymbol` | forward / `MOVED_FROM` | source | explicit transition / explicit generations |

### Derivation、confidence、task、cost 与 review

任务缩写：`EL` exact location、`IM` implementation、`CP` call path、`BL` bug
localization、`IA` impact analysis、`CC` change context、`HI` historical、`TV` test
validation。Derivation 缩写：`D` deterministic、`S` static、`M` semantic、`H` human。

| Edge | Derivation | Confidence 语义 | Tasks | Cost | Review |
| --- | --- | --- | --- | ---: | --- |
| `DEFINES` | D/H | 直接 parser 或 governed human 的结构身份，不是相似度 | EL/IM/CP/BL/IA | 1 | none |
| `CONTAINS` | D/H | 精确结构或 snapshot membership，不是 relevance | 全部 | 1 | none |
| `IMPORTS` | D/S/H | import 解析确定性；syntax 可精确、target resolution 可部分 | EL/IM/CP/BL/IA | 2 | none |
| `CALLS` | S/H | 具体 caller/callee resolver 确定性，不是向量相似或调用频率 | IM/CP/BL/IA/TV | 1 | none |
| `REFERENCES` | S/H | 静态 name-to-entity 确定性，不是泛化语义关联 | IM/CP/BL/IA/TV | 2 | none |
| `PARENT_OF` | D/H | 同 generation 的精确 lexical/AST parentage | EL/IM/CP/BL/IA | 1 | none |
| `TYPE_OF` | S/H | entity-worthy symbol 的静态类型解析；local/parameter 仅 resolver detail | EL/IM/CP/BL/IA | 2 | none |
| `IMPLEMENTS` | S/H | 显式 implementation relationship 的 compiler/indexer 确定性 | EL/IM/CP/BL/IA | 1 | none |
| `OVERRIDES` | S/H | 具体 override pair 的静态 dispatch hierarchy 确定性 | EL/IM/CP/BL/IA | 1 | none |
| `TESTS` | D/S/H | selector/framework/static target 对齐；不表示运行、通过或覆盖 | IM/CP/BL/IA/TV | 1 | none |
| `COVERS` | D/S/H | observed coverage 与 target version 对齐；独立于测试通过状态 | BL/IA/TV | 1 | none |
| `AFFECTS` | D/S/M/H | change-to-target 对齐；semantic 仅 reviewable candidate，不是 impact 证明 | BL/IA/CC/HI/TV | 2 | semantic candidates |
| `VALIDATED_BY` | D/H | observed passing 且 exit code zero 的精确 target-version 对齐 | BL/IA/CC/HI/TV | 1 | none |
| `FAILED_VALIDATION` | D/H | observed failed/error/nonzero 的精确对齐，明确不能作为验证成功 | BL/IA/CC/HI/TV | 1 | none |
| `SAME_SYMBOL_AS` | S/M/H | 跨版本 identity likelihood；semantic 保持 candidate 直到确认 | EL/IM/BL/IA/CC/HI | 2 | semantic candidates |
| `RENAMED_TO` | D/S/M/H | 正向跨版本 rename identity；similarity-only 必须复核 | EL/IM/BL/IA/CC/HI | 1 | semantic candidates |
| `MOVED_TO` | D/S/M/H | 正向跨版本 path-move identity；similarity-only 必须复核 | EL/IM/BL/IA/CC/HI | 1 | semantic candidates |

四个允许 semantic derivation 的关系恰为 `AFFECTS`、`SAME_SYMBOL_AS`、
`RENAMED_TO`、`MOVED_TO`，全部强制 `semantic_candidates` review。
`CALLS` 只允许 static/human；无论通过 spec 构造、`require_derivation()` 还是
`validate_edge_assertion()`，semantic `CALLS` 都 fail closed。

## Entity 与 fail-closed 边界

- `registered_edge_type()`、`get_edge_spec()`、traversal whitelist canonicalizer 均只接受
  registry enum；大小写变体、`DEPENDS_ON`、空值和自由文本被拒绝。
- endpoint type 与 derivation 在 `validate_edge_assertion()` 中同时验证；未知 endpoint
  不能跨越 ontology 边界。
- `CodeGraphEntityType` 没有 `LocalVariable` 或 `Parameter`；
  `CodeSymbolKind` 没有 `local_variable`、`parameter` 或 `argument`。现有 parser 也只从
  definition node 建 Symbol，reference linker 保持 local 为 resolver detail。
- `symbol_kind` 只能用于 `CodeSymbol`；其他 entity 携带 symbol kind 会被拒绝。

## Unresolved Diagnostics 审计

### 信息与永久 aggregate

每条 detail 完整保留 source entity、raw target、typed relation、五类 reason
（`no_candidate/ambiguous/external/dynamic/limit`）、candidate IDs、parser/resolver
version、language 和 module。所有控制文本均执行 strict string、非空、NFC、无控制字符
验证；candidate IDs 去重后按确定顺序保存。

summary 的 aggregate key 为：

```text
source entity id
+ source entity type
+ relation
+ reason
+ language
+ module
```

每个输入 observation 都先进入 aggregate 和 `total_count`，detail 限量或相同 detail
去重不会减少永久计数。summary 构造时强制
`total_count == sum(aggregate.count)`；手工 copy/update 也重新走完整验证。

### Bounded stable sampling 与确定序列化

采样 bucket 是 `(source entity id, relation)`，每 bucket 使用 canonical detail SHA-256
排名，只保留调用方给定上限；最终 detail、aggregate、candidate IDs 均再次 canonical
排序。因此输入顺序、mapping 顺序或重复 observation 不影响已选样本与序列化。

独立 probe 使用 500 条记录、5 个 source、2 个 relation、2 个 language、7 个 module：

```text
total_count: 500
aggregate sum: 500
detail buckets: 10
detail limit per bucket: 7
retained details: 70
maximum observed bucket: 7
aggregate cells: 210
language/module cells: 14
randomized input orders: 10/10 identical
canonical SHA-256:
  841f3a331ed2d9ce48be0c359d1fe72ef811ae39a13c55f37e39dc07734cdac7
```

`detail_limit=0` 保留全部 aggregates 且 details 为空；`by_language_module()` 的计数和
仍等于 total。JSON round trip、canonical bytes 与 hash 均保持一致。

## 阶段边界与 C0-C3 兼容性

`graph_v2.py` 只依赖标准库、Pydantic 与既有 Code contract enum。AST 静态扫描得到：

```text
banned imports (sqlite3/requests/httpx/socket): empty
I/O calls (open/connect/execute/commit/write_*): empty
```

模块没有 adjacency、graph traversal、retriever、beam/path scoring、query profile
fusion、SCIP/LSP/compiler adapter、schema migration 或 store publication。C4-02/C4-03/C5
均未偷跑。

包级 export probe 确认 C4-01 符号从 `evidence_rag.rag.sources.code` 与直接模块导入得到
同一对象，且没有改变既有 lazy retriever/embedding/reranker exports。既有 C0-C3
contract 专项 86 项全部通过。

## 独立验证记录

```text
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q \
  tests/test_code_graph_v2.py
  8 passed

PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q \
  tests/test_code_rag_contracts.py
  86 passed

PYTHONDONTWRITEBYTECODE=1 uv run --no-sync ruff check \
  src/evidence_rag/rag/sources/code/graph_v2.py \
  src/evidence_rag/rag/sources/code/__init__.py \
  tests/test_code_graph_v2.py
  All checks passed

PYTHONDONTWRITEBYTECODE=1 uv run --no-sync ruff format --check <same 3 files>
  3 files already formatted

17-edge registry matrix / semantic CALLS / public export probe
  PASS

500-record randomized diagnostic aggregation/sampling/serialization probe
  PASS

git diff --check
  PASS
```

主控交接中的专项、相关、全量 pytest、全 `src tests` Ruff/format 与 only-main /
single-worktree PASS 作为既有外部证据记录；本独立 Gate 只复跑最小专项与必要 probes。

## Git、文件身份与正式库零写入

审查前后 C4 三个只读目标文件 SHA-256 保持一致：

```text
graph_v2.py
  d81558a59212ac756cb30c7ab45f002365e44caed882f3a82bbb4d4a221d4ca6
code/__init__.py
  39ad214c468c4a4f6f3ec85bffbd2b3f9390c2b2e8ca68bc5684dffae15f8b80
test_code_graph_v2.py
  7e4e5f1d88a80a8c9cda517bf0f3fc14ee9d90664784cc0b6a739f76accb77bb
```

正式库只读 checksum/stat 在 Gate 前、专项后和报告写入后保持一致：

```text
var/evidence-rag.sqlite3
size: 1915490304
mtime: 2026-07-27 15:11:03 +0800
SHA-256:
  9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b
WAL/SHM: absent
```

Git 仍为 `main`、单一 worktree、同一 HEAD；C4 范围的实现/测试文件保持原有未跟踪状态，
本 Gate 唯一新增文件为本报告。

## 授权结论

未发现可复现的 P0/P1 主流程、数据正确性、安全或阶段越界缺陷。

```text
C4-01 PASS
C4-02 Typed Graph Retriever AUTHORIZED
```
