# Code C3 Gate Review

C3-01 复审时间：2026-07-27 19:46 +0800  
C3-02 / C-B2 PREPARED 复审时间：2026-07-27 20:27 +0800  
C-B2 production artifact 最终审计时间：2026-07-27 20:46 +0800  
C3-03 / C-B5 PREPARED 复审时间：2026-07-27 21:19 +0800  
C-B5 production artifact / C3 最终审计时间：2026-07-27 21:53 +0800  
共享工作目录：`/Users/example/project/rag`  
分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

## 最终裁决

```text
C3-01 PASS
C3-02 engineering PASS
C-B2 local-hash treatment NOT QUALIFIED
C3-03 engineering PASS
C-B5 deterministic reranker/calibration treatment NOT QUALIFIED
C3 engineering COMPLETE
C4 Typed Graph AUTHORIZED
P0/P1 artifact findings: 0
```

C-B5 production artifact 已证明 C3-01/C3-02/C3-03 的 production identity、固定分母、
隔离、安全、fail-closed calibration 与真实 reporting 工程合同完整。C-B5 唯一
acceptance failure 是相对 qualified C-B0 的 locator non-regression，因此 treatment
必须保持 NOT QUALIFIED，不能宣称质量提升；该真实负结果不是 C3 工程缺陷。C3 工程边界
到此封板，剩余 graph-required/locator 质量问题进入 C4 Typed Graph。

## 审查范围与写锁

本轮只读审查：

- `src/evidence_rag/rag/sources/code/embedding_v2.py`
- `src/evidence_rag/rag/sources/code/__init__.py`
- `tests/test_code_embedding_v2.py`
- 相关只读接口：
  `src/evidence_rag/rag/sources/code/store.py`、
  `src/evidence_rag/rag/sources/code/schema.py`、
  `src/evidence_rag/embeddings.py`

除本报告外未修改代码、测试、配置、依赖、其他文档、eval 或数据库；未创建
branch/worktree，未 commit/push。工作区在审查开始前已有大量主控改动，本 Gate 未改写或
清理它们。

## 独立功能审计

### 1. Frozen Profile 与可复现快照：PASS

`EmbeddingProfile` 使用 `frozen=True, slots=True`，完整承载 `id`、`purpose`、
`model`、profile `revision`、`dimension`、`instruction`、
`instruction_revision`、`max_tokens`、`batch_size`、`locality`、
`redaction_policy` 与 `normalization_revision`。字符串做严格类型、非空/NUL 与 NFC
规范化；purpose/locality 限定枚举；dimension/max_tokens/batch_size 拒绝 bool、
非整数和非正数。

`canonical_snapshot()` 覆盖全部字段且键序稳定，`canonical_json()` 使用
`sort_keys=True`、紧凑分隔符、`allow_nan=False`，同一 Profile 可得到确定性快照。

### 2. Provider、local-hash-v2 与默认离线：PASS

`EmbeddingProvider` 明确暴露 batch-only `embed_batch`、`available` 和不可变
`EmbeddingProvenance`。每个 batch 返回有序完整 vectors 及 provider/model/model
revision/dimension/locality。

`LocalHashCodeEmbeddingProvider` 直接适配既有 `LocalHashEmbedding`：

- model 真实固定为 `local-hash-v2`；
- 默认算法 revision 固定为 `2`；
- dimension 来自实际 embedder；
- locality 固定为 `local`；
- 单批不得超过 Profile `batch_size`；
- 二进制格式与既有 local-hash 实现一致。

模块只依赖标准库与本地 `LocalHashEmbedding`，没有 HTTP/socket client。未显式注入
provider 时，只有 local + `local-hash-v2` 可用；remote Profile 直接
fail closed，不发生网络访问。显式 provider 不可用时也只有
`allow_local_fallback=True` 才能回退，且 fallback 必须声明 local
`local-hash-v2` provenance。

### 3. Content-addressed cache：PASS

cache key 的 canonical payload 完整包含：

- unit `content_hash`；
- profile id/revision；
- 实际 provider model/model revision；
- instruction 内容 SHA-256 与 instruction revision；
- normalization revision；
- cache schema revision。

Store 查询还以 profile/model 二次隔离。独立探针确认相同输入 key 相同，分别改变
content、profile id、profile revision、instruction、instruction revision、
normalization revision、model 或 model revision 时均产生不同 key。专项测试进一步
确认跨 unit 的相同内容命中持久 cache，profile/model revision 变化产生真实 miss。

### 4. Index、隔离、安全与写前失败：PASS

Indexer 只 materialize 调用方传入的 unit iterable，不发现或扩展其他 unit。它保留
unit id、generation id、ACL ref、content hash，并在任何 cache/provider 工作前完成
输入结构和 batch 内 unit identity 校验。

secret/quarantine/max-token 判定发生在 provider 之前；被隔离记录没有 cache key、
vector 或 provider 调用。Profile instruction 也先做 secret scan。对可索引记录：

1. 先校验 provider model/locality/dimension provenance；
2. cache 只读且 `touch=False`，命中向量仍校验 dimension、字节长度和 finite float；
3. 所有 miss 按 Profile batch_size 完成 provider 调用；
4. 每批校验返回类型、完整数量、恒定 provenance、dimension、字节长度和 finite float；
5. 全部 batch 在内存中成功后，才进入 cache/vector 幂等 upsert。

因此 provider unavailable、provider exception、partial batch、provenance drift、
mixed dimension、非有限向量或坏 cache 都在任何 cache/vector 写前失败。

独立临时 SQLite 探针结果：

```text
late batch failure (第 2/2 批失败):
  provider calls = 2
  cache / vectors = 0 / 0

mixed dimension:
  cache / vectors = 0 / 0

remote default off:
  cache / vectors = 0 / 0

success + secret/quarantine:
  provider calls = [["def ok(): return 1"]]
  first indexed / skipped = 1 / 2
  repeated cache hits = 1
  cache / vectors = 1 / 1
  trace generation / ACL =
    generation://embedding / acl://engineering
```

成功重跑不重复发布向量；vector 主键和 upsert 保持 unit/generation/profile/model
幂等身份。读取侧 `iter_code_unit_vectors` 通过 unit+generation join 恢复 ACL，并继续
应用 project/repository/generation/allowed ACL/active generation scope。

### 5. 无阶段偷跑：PASS

审查范围内没有 learned/dense benchmark 实现、候选模型下载、网络调用、reranker、
C4 graph 或正式库写入。所有独立 DB 探针均使用系统临时目录并自动销毁。

只读检查时正式库：

```text
var/evidence-rag.sqlite3
size: 1915490304
mtime: 2026-07-27 15:11:03 +0800
```

本 Gate 没有通过 Settings、production path 或正式库连接执行任何测试。

## 独立验证记录

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q tests/test_code_embedding_v2.py
  14 passed

PYTHONDONTWRITEBYTECODE=1 uv run ruff check \
  src/evidence_rag/rag/sources/code/embedding_v2.py \
  src/evidence_rag/rag/sources/code/__init__.py \
  tests/test_code_embedding_v2.py
  All checks passed

PYTHONDONTWRITEBYTECODE=1 uv run ruff format --check <same 3 files>
  3 files already formatted

public lazy-export / runtime Protocol probe
  PASS

cache-key identity-axis probe
  PASS; same=hit; 8 identity axes=miss

temporary SQLite fail-closed/idempotence/security probe
  PASS
```

主控交接中的相关 68 项、全量约 413 项、全 src/tests Ruff、三文件内存 compile 及正式库
mtime 结论作为既有外部证据记录；本精简独立 Gate 没有重复扩大到全量执行。

审查时五个只读目标文件 SHA-256：

```text
embedding_v2.py
  1e690ad81c690d827aaa6f839791f26e579d7389b0aa4a95aa04ffda57a4b103
code/__init__.py
  8a4dea30c79d954e85ddbb0446ee986ea817888210a76aa2b4a920ac9e98d643
test_code_embedding_v2.py
  2e4f64c0224ea1b47a53857615b15b8f161d6aac22932f2bdea2f8d7ae619ba0
code/store.py
  aa9067fda7eda1e7517eee71b5ad58dcec9171fbc6994093c7bb5fc41b5fceba
embeddings.py
  a19c1cf14c4acedc5edc99d7a9857e5a84e9da4d9ff1144a46bb09a0c9b9a5f6
```

## C3-01 当时的非阻塞发布边界与 C3-02 关闭状态

`CodeV2StoreMixin` 的 cache upsert 与 vector upsert 是两个既有独立事务。C3-01 已保证
所有 provider/validation 失败发生在两次写之前；但若未来发生存储阶段故障，第一事务
成功而第二事务失败，cache 可能先于 vector 留存。

该已知边界已由 C3-02 publisher 在不修改 Store 的前提下关闭到发布安全级别：重建前先把
profile 标为 building/not-ready；任一 index、vector verification 或 publication
validation 异常都会删除该 generation/profile vectors，并把 profile 标为
unavailable、`dense_retrieval=false`。cache 可以作为内容寻址的可复用中间结果保留，
但不能使 profile ready，也不能被 dense retriever 当作已发布向量。

当前 C3-02 与 C-B2 固定使用本地 `local-hash-v2`。remote/learned candidates 明确为
unavailable/not-run；本 Gate 和 C-B2 production Run 授权均不包含网络候选。

## C3-01 阶段结论

未发现可复现的 P0/P1 主流程、安全或数据正确性缺陷。

```text
C3-01 PASS
C3-02 Dense Benchmark AUTHORIZED
```

## C3-02 Local Dense 独立 Gate

### 审查范围

本轮新增只读审查：

- `src/evidence_rag/rag/sources/code/dense_v2.py`
- `src/evidence_rag/rag/sources/code/__init__.py`
- `tests/test_code_dense_v2.py`
- `src/evidence_rag/evaluation/cb2.py`
- `tests/test_code_cb2_run.py`
- 只读复核既有 Store、Embedding、C-B0/C-B1/Golden 与 artifact verifier 接口

除更新本报告外没有改代码、测试、配置、其他文档、eval、artifact、数据库或 Git
状态；该 PREPARED 复审轮没有运行 C-B2 production Run。

### Publisher 完整性与发布原子边界：PASS

`CodeDenseProfilePublisher` 只接受一个已经 published 的 C2 generation，并在任何
embedding 写前验证：

- publication、project、repository、generation 三元身份一致；
- 调用方提供的 unit id 唯一，且完整覆盖该 generation 的全部 Store units；
- 每个 unit 的 project/repository/generation、ACL、content、content hash 与 Store
  truth 完全一致；
- generation 本身仍为 published。

重建流程先写 `building`/`dense_retrieval=false`，删除旧 profile vectors，再调用
C3-01 indexer。只有 index result 覆盖全部 units、零 skipped、单一 provider
provenance，且 Store 中 vector 的 unit/generation/profile/model/dimension/content
hash/finite bytes 全部复核通过后，才写入 `ready` profile state。ready state 固定
profile/provider 快照、unit/vector count、generation content hash 与 vector hash。

独立故障注入在 vector Store 事务已成功、调用方随后抛错的边界得到：

```text
cache / vectors: 14 / 0
publication.embedding: not-built
profile status: unavailable
dense_retrieval: false
```

这证明保留 cache 不会形成部分发布；generation/profile vectors 被清理，retriever
无法消费失败 publication。partial caller units 与 tampered content 则在任何
cache/vector 写前失败，计数为 `0 / 0`。

### Dense scope、provenance 与 fail-closed：PASS

Dense 检索只解析请求 scope 内的 active 或显式 commit/branch/generation：

- project/repository 不匹配、generation 缺失/歧义/未发布直接 unavailable；
- publication 必须为 published，embedding id/revision 与完整 Profile snapshot 必须
  相等，且 `dense_retrieval=true`、profile state `ready`；
- query provider 必须 available，model/revision/dimension/locality canonical
  provenance 必须与每个 publication 完全一致；
- vector iterator 显式绑定 project/repository/generation/ACL/profile/model；
- 每个 vector 与回读 unit 再次校验 scope、ACL、dimension、bytes 与 content hash；
- 无 ACL 权限返回零候选，不跨 scope 取回 unit；无 ACL 过滤时还要求 vector count 与
  publication count 完全一致。

独立 tmp DB 结果：

```text
published units / vectors: 14 / 14
first cache hit/miss: 0 / 14
repeat cache hit/miss: 14 / 0
ready profile: ready
wrong project: unavailable
unauthorized ACL candidates: 0
dense deterministic top-k candidates: 3
```

provider unavailable、provider/profile dimension 或完整 provenance 不一致、query
batch 不完整、坏 vector、坏 publication 都返回显式 unavailable/error contract，
没有静默 dense fallback。

### Exact/Sparse/Dense hybrid：PASS

`CodeHybridV2Retriever` 只组合 C2 exact/sparse 与 C3-02 dense 三个真实通道：

- weighted RRF 固定为 exact `2.0`、sparse `1.0`、dense `1.0`，`k=60`；
- exact rank 是 hard signal，排序在非 exact 候选之前；
- 相同 generation/unit 合并三通道 rank/score，不伪造命中；
- entity-aware diversification 先保留各 entity 的代表 unit，再补重复 unit；
- candidate 保留真实 entity、retrieval unit、generation、ACL、version、locator、
  raw channel rank/score 与 fused score；
- channel outcome 区分 disabled、no-match、pruned、with-hits、unavailable/error，并
  产生严格 `CodeSourceResult`、dense trace 与 hybrid trace。

独立探针的 hybrid top-3 均保留 exact hard signal，候选合并后观察到
`exact/sparse/dense` 三通道，locator 全部为真实 `code://...#L...`。实现没有
reranker、calibration、graph traversal 或 context synthesis 偷跑。

### 模型与网络边界：PASS

唯一 ready 默认候选是离线 deterministic `local-hash-v2`；Qwen3、BGE-M3、
code-specific 与 remote upper bound 全部声明 `unavailable`、`benchmark_status=not-run`。
实现没有 HTTP/socket/model hub client、下载逻辑或模型 artifact 安装。C3-02 不虚构
learned embedding benchmark 结果。

## C-B2 Harness PREPARED 只读 Gate

### 锚、分母与 production identity：PASS

独立运行 `prepare` 返回：

```text
status: PREPARED
execution_status: pending-c3-02-gate
artifact_created: false
treatment_qualified: false

Golden total / eligible / ineligible: 50 / 33 / 17
package_hash:
  sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61
membership_hash:
  sha256:a9551e3b77822fd67f11c32dffa04e6b0069cd88e44adbfabfb6cd12bd343aaa

C-B0 qualified baseline:
  evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061
C-B1 NOT QUALIFIED audit control:
  evaluation-run://project-code-golden-v2/33f76ed55a3d4c9ab1060a54bfa13d35
  qualification_use: forbidden

production publisher: CodeDenseProfilePublisher / ready
production retriever: CodeHybridV2Retriever / ready
```

C-B0 qualification record、manifest canonical hash 与 Golden membership 均由
prepare 重新验证。C-B1 latest audit 只作为 NOT QUALIFIED 对照；其 manifest 必须明确
`treatment_qualified=false`、acceptance `not-qualified`，不能成为 qualified baseline。

qualified mode 要求显式 C3-02 Gate approval，禁止 factory/component injection，并在
运行前后核对 workspace input fingerprint 与正式库状态。test mode 必须显式注入 fake
components、不得声称 Gate approval，且不能写入 repository `evals/code/runs`。因此 fake
只可验证 harness，不可产生 qualified 或混入正式 Run 目录。

### Isolation、canonical artifact 与 verify-only：PASS

在 PREPARED Gate 当时 production Run 尚未执行。harness 已具备以下 fail-closed 合同：

- 在系统临时目录物化隔离 DB，正式库运行前后比较；
- 固定 33/33 result membership、graph off、真实 production component identity；
- 只在完成 Run、完整 dense publication、input fingerprint 无漂移后构造 artifact；
- artifact 只能含固定 10 文件，必须是普通文件且禁止 symlink；
- verify-only 以 SQLite read-only 打开，执行 quick check、foreign-key check、single-Run
  identity、embedded Golden、terminal immutability；
- 从 SQLite 与固定 anchors 重算全部 reports、acceptance、manifest、artifact set hash；
- security scan 必须无临时绝对路径与 credential assignment；
- test artifact 默认拒绝验证，只有显式 `allow_test_artifact=True` 才可验证，且永远
  `treatment_qualified=false`；
- tamper、workspace drift、fake qualified、缺 Gate 或正式库变化均在 artifact 发布前
  fail closed。

该 PREPARED Gate 前后正式 Run 树文件状态指纹完全相同：

```text
d65e96a491943580297bc06b417c8f2833f6622ee16ac493715f12d5d6eba901
```

当时目录仍只有既有 C-B0/C-B1/历史失败 Run 与 `_qualification`，没有新增 C-B2
artifact。
正式库保持：

```text
var/evidence-rag.sqlite3
size: 1915490304
mtime: 2026-07-27 15:11:03 +0800
```

### 独立验证

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider \
  tests/test_code_dense_v2.py
  7 passed

PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider \
  tests/test_code_cb2_run.py
  8 passed

PYTHONDONTWRITEBYTECODE=1 uv run ruff check <C3-02/C-B2 5 files>
  All checks passed

PYTHONDONTWRITEBYTECODE=1 uv run ruff format --check <same 5 files>
  5 files already formatted

cb2 prepare
  PREPARED / artifact_created=false / treatment_qualified=false

temporary SQLite publisher/scope/hybrid probe
  PASS
```

主控交接中的 C3-02 7 专项/145 相关/420 全量、harness 69 相关，以及主控独立约 91
相关/428 全量、全 src/tests Ruff、五文件 format/diff/compile 作为既有外部证据记录；
本精简 Gate 未重复扩大到全量执行。

本轮五个只读目标文件 SHA-256：

```text
dense_v2.py
  6b5f79ea8f4c8177fb8fdef16d62f2a2d7f5fa0b63ac7f6202a40601e51ea249
code/__init__.py
  266cbf2be8600970d11874bdc011c540117af6c0624dbbf1378d90c75482d58a
test_code_dense_v2.py
  17dc7408b9445919ecb6b5ce2fc16ac5c7fec5db501b13600a32172ecd0ab6f9
evaluation/cb2.py
  a919b51b3c579305a1631651541fb62cab3cb83f8359faa2f48d450aa18b283c
test_code_cb2_run.py
  7f7f7b6b3b5bb714e601be7092a527e54e773c0329baeab040ac02f11f90c1a9
```

## C-B2 Production Artifact 最终只读审计

审计对象：

```text
evaluation-run://project-code-golden-v2/6fad2dd1c67846668ba38045ddae0064
artifact files: 10
artifact_set_hash:
  sha256:24d0e24c92294e43fe310b9502b5ea47f0848b9f433561819bc0714b7f653667
manifest_canonical_hash:
  sha256:7051a1e0e6ad897da739709bc73593d19e84f9e0af7816bafa89f4809500afc6
```

### Verify-only、identity 与固定锚：PASS

独立运行：

```text
PYTHONDONTWRITEBYTECODE=1 uv run python -m evidence_rag.evaluation.cb2 \
  --repository-root /Users/example/project/rag \
  verify 6fad2dd1c67846668ba38045ddae0064

status: verified
execution_mode: qualified
treatment_qualified: false
case_count / result_count: 33 / 33
security clean: true
```

verifier 从 artifact 内 SQLite 和固定输入重算全部报告、acceptance、manifest 与 artifact
set hash，结果一致。production identity 为真实
`CodeDenseProfilePublisher/code-dense-publication-v1` 与
`CodeHybridV2Retriever/code-hybrid-v2`；fusion 为
`deterministic-weighted-rrf-v2`，通道为 exact/sparse/dense，graph、reranker、
semantic resolver 均关闭。Run 前后 implementation input fingerprint 完全相同，
HEAD/tree 分别为 `bc3326edc761e3bdb42ed78726a21f314ab44974` /
`2fe240ebdf1f3d04c252c32b505ca923394171da`。

Golden 固定分母由 artifact 与 SQLite membership 双向核对：

```text
total / eligible / ineligible / results: 50 / 33 / 17 / 33
membership diff: 0
package_hash:
  sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61
membership_hash:
  sha256:a9551e3b77822fd67f11c32dffa04e6b0069cd88e44adbfabfb6cd12bd343aaa
```

固定对照身份未漂移：

- qualified C-B0：
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`；
  qualification record、artifact set 与 manifest canonical hash 均通过复验；
- latest C-B1：
  `evaluation-run://project-code-golden-v2/33f76ed55a3d4c9ab1060a54bfa13d35`；
  明确 `treatment_qualified=false`、`qualification_use=forbidden`，只作 audit 对照。

### Index、cache 与 publication 账本：PASS

只读 SQLite `quick_check=ok`、foreign-key check 零异常。账本为：

```text
evaluation runs / results: 1 / 33
AST units / FTS rows: 425 / 425
vectors / embedding cache: 425 / 425
publications: 3
```

三个 generation 分别为 `45 / 368 / 12` units；各自 FTS、vectors 与声明计数完全相等，
publication/generation 均为 published，dense profile 均为 ready、
`dense_retrieval=true`。全部 425 vectors/cache 固定为：

```text
profile: code_nl/local-hash-v2@1
provider/model revision: local-hash/local-hash-v2@2
dimension/locality: 384 / local
```

独立 join 检查得到 orphan vector、unit content-hash mismatch、blob dimension mismatch、
空 ACL ref 均为 0；425 个 cache key 全部唯一。materialization 明确 isolated DB、
`production_database_accessed=false`、`network_used=false`、
`downloads_performed=false`。Qwen3、BGE-M3 与 remote upper bound 均为
unavailable/not-run，不存在模型下载或虚构结果。

### Metrics、dense 贡献与 treatment 判定：NOT QUALIFIED

固定分母下的主要结果：

| 指标 | C-B2 | 对照 | 差值 / 判定 |
| --- | ---: | ---: | --- |
| overall entity recall@10 | 0.7273 (32/44) | C-B1 0.6136 | +0.1136 |
| overall locator recall@10 | 0.5682 (25/44) | qualified C-B0 0.8409 | -0.2727，Gate failed |
| exact entity recall@10 | 1.0000 | qualified C-B0 0.8333 | +0.1667，non-regression passed |
| identifier entity recall@10 | 1.0000 | qualified C-B0 1.0000 | 0，non-regression passed |
| low-overlap entity recall@10 | 0.6000 | C-B1 0.6000 | 0，strict-improvement Gate failed |
| hard-negative error@10 | 0.4118 (7/17) | C-B1 0.2941 | +0.1176 error，变差 |
| MRR@10 / nDCG@10 | 0.3505 / 0.4209 | — | 观测值 |

acceptance 中 production identity、33/33 coverage、artifact security、exact 与 identifier
non-regression 均通过；locator non-regression 与 low-overlap strict improvement 两项
失败，所以 `decision=not-qualified`、`treatment_qualified=false` 是正确的
fail-closed 结果。

dense 并非没有真实贡献：27/33 cases 含 dense contribution，融合结果内共有 224 个
dense-only positions；C-B1 的 10 个 zero-result cases 中恢复 4 个
（001、008、016、030，均从 0 增至 20 candidates）。因此可以精确陈述
“dense 恢复 4 个 C-B1 zero-result，并使 overall entity recall 相对 C-B1 提高
0.1136”，但不能把该局部事实扩张为 “C-B2/local-hash 整体质量提升” 或 qualified。

### Latency、storage 与安全可移植性：PASS（观测，不是质量放行）

```text
query P95: 550.13 ms
  vs C-B0: +535.09 ms
  vs C-B1: +463.91 ms
dense index P95 / total: 516.29 / 669.00 ms
ingest P95 / total: 603.93 / 1645.72 ms

isolated SQLite: 10,788,864 bytes
vector logical bytes: 652,800
cache logical bytes: 652,800
total retrieval-index logical bytes: 1,868,564
```

query latency 明显回归，应进入 C3-03 calibration/performance 观察项；artifact 本身完整
记录该负向结果，没有把它作为本阶段工程正确性的 P0/P1。

安全扫描覆盖 9 个 JSON 与 SQLite 全部 TEXT columns（874 columns / 42,959 values），
临时绝对路径与 credential finding 均为 0；artifact 使用
`<redacted-temp-path>`，unauthorized leakage count/rate 均为 0。固定 10 文件均为普通
文件，无 SQLite WAL/SHM sidecar，artifact 可移植性通过。

### 不变性与唯一写锁：PASS

verify-only 前后 production artifact、旧 Run 与正式库均未改变：

```text
production artifact aggregate content fingerprint:
  6d2d53609c12ae7b755a3d7815a2c0a66ec0c030cd847fdde8351e92c07867f5
all old Runs excluding this artifact aggregate content fingerprint:
  30cfb1d42bc81fb7d50cbff763339321a9aa691f71f5bc05f69f537c08434853

var/evidence-rag.sqlite3
size: 1915490304
mtime: 2026-07-27 15:11:03 +0800
```

审计保持 `main`、单一 worktree、相同 HEAD；未创建 branch/worktree，未
commit/push。除本报告外未修改代码、测试、配置、依赖、其他文档、eval、artifact 或
数据库。

## C-B2 Production 证据分类与阶段结论

| 结论 | 支持状态 | 直接来源 |
| --- | --- | --- |
| C3-02 publisher/retriever 工程完整 | supported | production code、专项/tmp DB Gate、真实 production publication 账本 |
| C-B2 production identity、分母、锚、安全与 artifact 完整 | supported | 独立 verify-only、SQLite read-only、固定 10-file hashes |
| dense 恢复 4 个 C-B1 zero-result，overall entity 相对 C-B1 +0.1136 | supported | `error-analysis.json`、`metrics.json`、SQLite 33-case membership |
| local-hash treatment 达到固定质量门并可宣称整体提升 | contradicted | locator -0.2727；low-overlap +0.0000；acceptance not-qualified |
| learned/remote model 效果 | excluded | Qwen/BGE/remote unavailable、未运行、无网络/下载 |

剩余 locator、low-overlap、hard-negative 与 query latency 问题转入 C3-03
reranker/calibration；可另行运行 learned local model benchmark，但必须保留独立模型
身份与相同固定分母，不得用尚未运行的 Qwen/BGE/remote 结果补写结论。

未发现 production artifact 的可复现 P0/P1 主流程、安全或数据正确性缺陷。质量门失败
被如实 fail closed，不否定 C3-02 engineering Gate。

```text
C3-01 PASS
C3-02 engineering PASS
C-B2 local-hash treatment NOT QUALIFIED
C3-03 Reranker/Calibration AUTHORIZED
P0/P1 artifact findings: 0
```

## C3-03 Deterministic Reranker / Calibration 独立 Gate

### 审查范围

本轮只读审查：

- `src/evidence_rag/rag/sources/code/rerank_v2.py`
- `src/evidence_rag/rag/sources/code/__init__.py`
- `tests/test_code_rerank_v2.py`
- `src/evidence_rag/evaluation/cb5.py`
- `tests/test_code_cb5_run.py`
- 只读复核既有 hybrid `CodeSourceResult`、Golden、C-B0/C-B1/C-B2 anchors 与 artifact
  verifier 接口

除更新本报告外没有改代码、测试、配置、依赖、其他文档、eval、artifact、数据库或
Git；没有执行 C-B5 production Run。

### EvidenceCard、scope hard gate 与确定性排序：PASS

`DeterministicCodeReranker` 只接受调用方已经完成 scope/ACL 过滤的
`CodeHybridSearchResult`。`EvidenceCard` 的 candidate identity、repository、
generation、ACL、stable version、真实 locator、三通道 rank/score 与 exact hard
signal 全部复制或派生自该 hybrid result/trace；模块没有 Store 查询、scope 扩张、网络
或模型调用。

在 candidate 进入排序前，hard gate 先核对：

- `version_alignment`、resolved repository/version 与显式 commit；
- hybrid trace 的 generation scope；
- request 的 `allowed_acl_refs` 与 `enforce_acl`。

不匹配项以 `wrong_version`、`wrong_generation` 或 `wrong_acl` 显式拒绝，不能被高分或
exact signal 覆盖。拒绝项可保留 diagnostic feature snapshot，但不参与 candidate
admission/ranking。

有效 candidate 使用固定规则和全序 tie-break：

- exact hard signal 始终排在非 exact-hard candidate 前；
- exact/sparse/dense rank、受限 raw-score tie-break、exact URI/qualified/path、
  same-name、path/module 与 canonical span 为正向特征；
- wrong path 的 same-name hard negative、child duplicate、parse error、generated 与
  partial unit 为显式负向特征；
- dense-only 只能作为 recovery evidence，不能越过 exact hard signal；
- 同 entity 的 canonical span 优先，child unit 显式降权；
- 最终 tie-break 固定使用各通道 rank、原 hybrid rank、repository/path/unit/generation。

候选 locator 原样保留并进入重新校验的 `CodeRetrievalCandidate`，没有合成或伪造路径。
`input_budget` 被限制为 30–60，wrapper 请求 hybrid 的有效上限为 50；输出严格取
`min(request.limit, configured top_k)`，重排后重新编号 `within_source_rank`。

### Deadline、异常 fallback 与 strict contract：PASS

wrapper 先保存原 hybrid 顺序内的输出预算切片，再执行 deterministic rerank。deadline
在 rerank 开始、逐 candidate materialize、hard gate 与排序路径中反复检查；任何
`RerankDeadlineExceeded` 或其他异常都返回原 hybrid RRF 顺序、原 fused score 与真实
candidate identity，不把请求升级为失败。

fallback 与成功结果均通过 `_result_with_candidates()` 重建并
`CodeSourceResult.model_validate()`；被完全裁剪的通道由
`complete_with_hits` 转为 `complete_pruned`，context blocks 同候选同步裁剪。trace
明确记录 `fallback_used`、异常类型/reason、input/output count 和模型状态。专项确认
异常与 deadline fallback 都保持原候选，且 strict `CodeSourceResult` 可 JSON
round-trip。

### Explanation、negative reason 与 necessity：PASS

每个 decision 记录 accepted、原/新 rank、feature contribution、explanation code、
negative reason、necessity role 与受控解释。解释只陈述已观测 channel/locator/path/
quality/duplicate 信号；dense-only 标为 recovery evidence，background/target 只是
候选用途。实现没有把 explanation、negative reason、necessity 或 calibrated score
宣称为事实真值、正确答案或人工审查结论。

### Calibration artifact：PASS

`CalibrationArtifact` 固定绑定 artifact/reranker/profile/model/index version，并以
sorted-key、紧凑、UTF-8 JSON 产生 canonical bytes/hash：

- 无样本：`status=unavailable`、无 bins/ECE/Brier、给出明确 reason；
- 少于最小标注量：`rank_percentile_empirical`、`provisional=true`，ECE/Brier 不伪造；
- 足量标注：固定分箱与 PAVA 单调拟合，记录 ECE/Brier；
- apply 时 profile/model/index/reranker 任一版本不匹配即拒绝；
- application reason 明确 empirical score 不是 verified truth 或 probability。

独立内存探针确认 empty/small/40-labeled 三种状态、canonical hash 稳定、版本漂移拒绝。
deterministic model 与 cross-encoder 均为 `benchmark_status=not-run`；cross-encoder
额外为 `unavailable`。缺少 cross-encoder 是明确 excluded capability，不是 C3-03
工程 Gate 缺陷。

## C-B5 Harness PREPARED 只读 Gate

### 分母、锚与 production identity：PASS

独立执行 `cb5 prepare`：

```text
status: PREPARED
execution_status: pending-c3-03-gate
artifact_created: false
treatment_qualified: false

Golden total / eligible / ineligible: 50 / 33 / 17
package_hash:
  sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61
membership_hash:
  sha256:a9551e3b77822fd67f11c32dffa04e6b0069cd88e44adbfabfb6cd12bd343aaa

qualified baseline:
  evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061
C-B1 NOT QUALIFIED audit:
  evaluation-run://project-code-golden-v2/33f76ed55a3d4c9ab1060a54bfa13d35
C-B2 NOT QUALIFIED audit:
  evaluation-run://project-code-golden-v2/6fad2dd1c67846668ba38045ddae0064
```

prepare 重新 verify C-B0 qualification record、C-B1/C-B2 manifests、Golden package 与
membership。只有 C-B0 标记 `treatment_qualified=true`；C-B1/C-B2 均固定
`qualification_use=forbidden`，不能成为 qualified baseline。

production identity 固定为真实 `CodeDenseProfilePublisher`、
`CodeHybridV2Retriever`、`CodeRerankedRetriever` 与 `CalibrationArtifact`；stage
order 固定为 ast-v2 → local-dense-ready → exact/sparse/dense hybrid →
deterministic rerank。production Run 还必须显式提供恰好一个 public、版本化 calibration
artifact 并携带 C3-03 Gate approval。

### Fake、isolation、security、drift 与 tamper：PASS

- qualified mode 禁止任何 component factory 注入；缺 Gate 或 fake calibration
  fail closed；
- test mode 必须同时显式注入四个 fake factory，不得声称 Gate approval，不得写入
  repository `evals/code/runs`，且永远不能 treatment-qualified；
- Run 只在系统临时目录 materialize isolated SQLite，ACL 固定开启；正式库前后包含
  hash/state 校验；
- workspace implementation、Golden、anchors、flags 与 production identity 前后
  fingerprint 必须相同；
- artifact 只允许固定文件集、普通文件且禁止 symlink；SQLite read-only quick check、
  foreign-key check、single-Run、embedded Golden 与 terminal immutability 全部重算；
- reports、acceptance、attempt audit、manifest、artifact set hash 与 anchors 都必须从
  SQLite/固定输入重构；
- test artifact 默认拒绝 verify；report、qualification、anchor 或 credential tamper
  均 fail closed；
- security scan 覆盖全部 artifact JSON 与 SQLite TEXT，临时路径 sanitization 使用
  portable placeholder；network/download/cross-encoder 保持 false/not-run。

专项使用 pytest 临时目录执行完整 test-mode harness、isolated SQLite、test artifact
verify 与 tamper probes；没有向正式 Run 目录发布 artifact。

### 独立验证与不变性

```text
PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider \
  tests/test_code_rerank_v2.py tests/test_code_cb5_run.py
  24 collected / 24 passed

PYTHONDONTWRITEBYTECODE=1 uv run ruff check <C3-03/C-B5 5 files>
  All checks passed

PYTHONDONTWRITEBYTECODE=1 uv run ruff format --check <same 5 files>
  5 files already formatted

cb5 prepare
  PREPARED / artifact_created=false / treatment_qualified=false

public calibration state/version/canonical-hash probe
  PASS
```

主控交接中的 C3/C-B5/Golden 相关专项、项目全量 pytest 100%、全 src/tests Ruff、五文件
format/diff/内存 compile 作为既有外部证据记录；本精简 Gate 独立执行上述最小集合，没有
重复扩大到全量。

五个只读目标文件 SHA-256：

```text
rerank_v2.py
  0cc4d7303da32e5a5c965b9e17ae7a07896c6f02e39ce100afc38465437d4546
code/__init__.py
  e1bfbc3c403b2f31d28bc7069af3fdafc3730e9de15c04cd4e0e84cf0d20fc9f
test_code_rerank_v2.py
  618540da03bec8cdb4ebdd30b1ac2d02bc06ee5b12abf00923a4a33bf1d7872d
evaluation/cb5.py
  3ae8fa5f3d281960d3db8b3456229a7e124f37b76e9e446063b375cebb15874f
test_code_cb5_run.py
  569ab9f6222b963f584111cb254ae21ca5e3247ade0ec2389b562e75d91c622b
```

本轮前后正式 Run 树保持 90 个文件，content fingerprint 完全相同：

```text
4859cfb54e07715f47430172de19750f9e5ca6435b45a20ac140398166991e35
```

正式库保持：

```text
var/evidence-rag.sqlite3
size: 1915490304
mtime: 2026-07-27 15:11:03 +0800
```

审计保持 `main`、单一 worktree 与相同 HEAD；唯一写入为本报告。

## C3-03 / C-B5 PREPARED 证据分类与阶段结论

| 结论 | 支持状态 | 直接来源 |
| --- | --- | --- |
| deterministic reranker 的 scope、排序、预算与 fallback 合同 | supported | production code、16 个 reranker parametrized executions |
| calibration 三态、版本绑定、canonical artifact 与 claim honesty | supported | production code、专项与独立内存探针 |
| C-B5 分母、锚、identity、fake 隔离与 verify-only 合同 | supported | `cb5 prepare`、8 个 harness executions、tmp SQLite artifact probes |
| C-B5 相对 C-B0/C-B1/C-B2 的真实质量结果 | missing by design | production Run 尚未执行；不得预写提升或 qualification |
| cross-encoder 效果 | excluded | unavailable / not-run，不是工程 Gate 前置条件 |

未发现可复现的 P0/P1 主流程、安全或数据正确性缺陷。真实质量、latency、fallback rate、
ECE/Brier 与 rerank contribution 必须等待 C-B5 production Run 原样报告；null、
provisional 或 negative result 不得被隐藏，也不反向否定本次工程 Gate。

```text
C3-01 PASS
C3-02 engineering PASS
C-B2 local-hash treatment NOT QUALIFIED
C3-03 PASS
C-B5 PREPARED
C-B5 PRODUCTION RUN AUTHORIZED
P0/P1 blocking findings: 0
```

## C-B5 Production Artifact / C3 最终只读审计

审计对象：

```text
evaluation-run://project-code-golden-v2/baf5b9eb49464ca79920030fd1c772de
artifact files: 10
artifact_set_hash:
  sha256:4598fca1682f6e94467ecdde70f3948f7f940ee4455aafba30a2697882db7946
manifest_canonical_hash:
  sha256:462d36e07151118e6ed35042d10224cb3341c2665164cf13ba75def58ab59366
```

### Verify-only、production identity、分母与 anchors：PASS

独立运行：

```text
PYTHONDONTWRITEBYTECODE=1 uv run python -m evidence_rag.evaluation.cb5 \
  --repository-root /Users/example/project/rag \
  verify baf5b9eb49464ca79920030fd1c772de

status: verified
execution_mode: qualified
c3_03_gate_approved: true
treatment_qualified: false
case_count / result_count: 33 / 33
security clean: true
```

verifier 以只读 SQLite 重算 embedded Golden、reports、slices、acceptance、attempt audit、
manifest、artifact set hash 与固定 anchors，结果一致。production stage/identity 为：

```text
ast-v2
-> CodeDenseProfilePublisher / local-hash-v2 384d
-> CodeHybridV2Retriever / deterministic-weighted-rrf-v2
-> CodeRerankedRetriever / deterministic-rules-v1
-> CalibrationArtifact / code-rerank-calibration-v1
```

Golden 固定为 50 total / 33 eligible / 17 ineligible，SQLite 结果 membership 与
`coverage.json` 差异为 0；package/membership hash 分别保持：

```text
sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61
sha256:a9551e3b77822fd67f11c32dffa04e6b0069cd88e44adbfabfb6cd12bd343aaa
```

只有 C-B0
`evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
是 qualified baseline；C-B1
`evaluation-run://project-code-golden-v2/33f76ed55a3d4c9ab1060a54bfa13d35`
与 C-B2
`evaluation-run://project-code-golden-v2/6fad2dd1c67846668ba38045ddae0064`
均经独立 verify 后作为 `treatment_qualified=false`、
`qualification_use=forbidden` 的 audit controls。Run 前后 implementation input
fingerprint 完全相同，HEAD/tree 未漂移。

### Artifact、SQLite、隔离与可移植性：PASS

固定 10 个 artifact entries 均为普通文件，无 symlink、WAL/SHM sidecar。SQLite：

```text
quick_check: ok
foreign-key failures: 0
evaluation runs / results: 1 / 33
AST units / FTS rows / vectors / cache: 425 / 425 / 425 / 425
publications: 3
```

三个 generation 分别完整发布 45 / 368 / 12 units、FTS rows 与 vectors；全部
publication/generation 为 published、dense profile 为 ready、
`dense_retrieval=true`。425 vectors/cache 均为
`code_nl/local-hash-v2@1`、model revision 2、384d，cache key 与 content identity
完整。

33 个 result trace 只有一个真实 reranker version 与一个 calibration artifact
fingerprint；ACL unauthorized leakage 为 0，invalid fallback rows 为 0，
cross-encoder run rows 为 0。materialization 明确：

```text
database_isolated: true
production_database_accessed: false
network_used / downloads_performed: false / false
cross_encoder: unavailable / not-run
graph_candidate / remote model: off / off
```

安全扫描覆盖 9 个 JSON 与 SQLite 全部 TEXT columns（874 columns / 42,943 values），
临时路径与 credential findings 均为 0，并使用 `<redacted-temp-path>`。artifact 真实、
可移植且未访问正式库。

### Acceptance gates：仅 locator quality 失败

9 个 acceptance gates 中 8 个通过：

- production component identity + C3-03 Gate：PASS；
- complete 33-case coverage：PASS；
- artifact security：PASS；
- C-B0 exact entity non-regression：PASS，0.833333 对 0.833333；
- C-B0 identifier entity non-regression：PASS，1.000000 对 1.000000；
- hard-negative or harmful non-regression：PASS；overall hard-negative error
  0.294118，优于 C-B0 0.352941；
- timeout fallback correctness：PASS；
- calibration evidence/claim honesty：PASS。

唯一失败：

```text
cb0_locator_non_regression: FAILED
C-B5 overall locator recall@10: 0.636364 (28/44)
qualified C-B0:              0.840909 (37/44)
delta:                      -0.204545
```

因此 `decision=not-qualified` 与 `treatment_qualified=false` 是正确的 fail-closed
结果。它是质量证据，不是 artifact 身份、分母、隔离、安全或数据正确性缺陷。

### 真实 quality / slice 结果：NOT QUALIFIED

不得只记录唯一 Gate failure 而隐藏其余负向观测：

| Scope / 指标 | C-B5 | 对照与事实 |
| --- | ---: | --- |
| case-level passed | 2/33 | 31 failed；pass rate 0.0606 |
| overall entity recall@10 | 0.6591 | vs C-B0 -0.1818；vs C-B1 +0.0455；vs C-B2 -0.0682 |
| overall locator recall@10 | 0.6364 | vs C-B0 -0.2045；vs C-B1 +0.0455；vs C-B2 +0.0682 |
| overall MRR@10 / nDCG@10 | 0.3661 / 0.4886 | vs C-B0 -0.2661 / -0.1162 |
| exact entity recall@10 | 0.8333 | 与 C-B0/C-B1 相同；低于 C-B2 1.0000 |
| identifier entity recall@10 | 1.0000 | 与三个 controls 相同 |
| low-overlap entity / locator recall@10 | 0.6000 / 0.6000 | 与 C-B1/C-B2 相同；均低于 C-B0 0.9000 |
| hard-negative error@10 | 0.2941 | vs C-B0 0.3529，改善 0.0588 |
| harmful candidate rate@10 | 0.0786 | vs C-B0 0.0761，轻微变差 |
| returned locator validity / ACL leakage | 1.0000 / 0 | locator 格式与安全正确，不等于 locator recall 合格 |
| entity / unit duplicate rate@10 | 0.1444 / 0 | entity duplication 仍是质量观测 |

`error-analysis.json` 记录 6 个 zero-result，其中 014/035 是正确 refuse，013/019/025/034
是 direct-query miss；019/034 为 graph-required，025 为 graph-harmful，说明下一阶段
需要 typed graph，但不能据此反写 C-B5 质量提升。

### Contribution、fallback 与 calibration：真实报告

贡献来自真实 33-case Run：

```text
cases with contribution: 27
dense contribution positions: 451
dense-only positions: 196
rerank changed / promoted / demoted: 280 / 131 / 149
rerank added / removed: 127 / 127
```

explanation trace 真实记录：

```text
child_duplicate_suppressed: 22
dense_only_recovery: 21
deterministic_feature_rank: 18
exact_path_signal: 5
exact_qualified_signal: 10
output_budget_pruned / negative output_budget: 24 / 24
```

真实 Run 没有触发 fallback 或 timeout：

```text
fallback / timeout: 0 / 0 of 33
invalid timeout fallback: 0
timeout P95: unavailable (no timing samples)
policy: preserve-hybrid-order-v1
```

因此可以确认未出现 fallback 错误，但不能虚构一次 production timeout 演练或 timeout
latency。

calibration artifact 为 version-bound provisional artifact：

```text
method: rank_percentile_empirical
artifact samples / labeled: 160 / 13
run judged observations: 65
minimum final observations: 100
run ECE: 0.285000 (provisional, 10 bins)
run Brier: 0.307029 (provisional)
probability_claim_allowed: false
```

另有 120 次 application 因 profile/model/index/reranker context version mismatch 被明确
拒绝并记录为 unavailable；这证明版本 fail-closed 生效，也说明 calibration coverage
并不完整。artifact 自身 ECE/Brier 为 null，Run report 只用 65 个真实 judged
observations 计算 provisional ECE/Brier，不把 empirical score 伪称为概率或 truth。

### Latency、index 与 storage：真实负向观测

```text
query P95: 693.645 ms
  vs C-B0: +678.605 ms
  vs C-B1: +607.422 ms
  vs C-B2: +143.515 ms
dense index P95 / total: 572.850 / 743.370 ms
ingest P95 / total: 670.604 / 1857.610 ms

isolated SQLite: 10,846,208 bytes
AST / FTS / vectors / cache: 425 / 425 / 425 / 425
vector logical bytes: 652,800
cache logical bytes: 652,800
total retrieval-index logical bytes: 1,868,564
```

latency 明显回归，必须保留为真实 audit 结果；它不改变 C3 production contracts 已被
验证的工程结论。

### 不变性与唯一写锁：PASS

verify-only 前后内容指纹保持：

```text
C-B5 target artifact:
  802d7c69b0bbc2203ce113d1e30d12894c21e4609c5451d02a4caf4ca461f565
all old Runs excluding C-B5:
  4859cfb54e07715f47430172de19750f9e5ca6435b45a20ac140398166991e35
all Runs:
  73ecb4e1b0d84f99e00459101e0c28860b3472213d4dc90e783796e7569ba4a4
```

正式库保持：

```text
var/evidence-rag.sqlite3
size: 1915490304
mtime: 2026-07-27 15:11:03 +0800
```

审计保持 `main`、单一 worktree 与相同 HEAD；除本报告外未修改代码、测试、配置、
依赖、其他文档、evals/Run、Golden 或数据库，未 branch/worktree/commit/push。

## C3 最终证据分类与封板结论

| 结论 | 支持状态 | 直接来源 |
| --- | --- | --- |
| C-B5 是真实 production-component、固定 33-case、隔离且安全的 artifact | supported | 独立 verify-only、SQLite read-only、固定 anchors/security/hash |
| C3 dense/hybrid/reranker/calibration 工程合同完整 | supported | C3-01/02/03 Gates 与真实 C-B2/C-B5 production artifacts |
| C-B5 locator 或整体质量合格/提升 | contradicted | locator -0.204545 vs C-B0；case-level 2/33；多项 slice/latency 回归 |
| calibration 是 final 或概率化结果 | contradicted | provisional；65 observations；120 version mismatches；probability claim forbidden |
| cross-encoder 效果 | excluded | unavailable / not-run |

未发现 artifact 不真实、分母漂移、anchor 冒充、隔离失败、安全泄漏或数据损坏等可复现
P0/P1。C-B5 treatment 的 NOT QUALIFIED 必须永久作为真实 audit 保留，但不构成继续循环
C3 工程 Gate 的理由。C3 engineering 至此完成并停止边界循环；graph-required 与 locator
质量问题转入 C4 Typed Graph。

```text
C3-01 PASS
C3-02 engineering PASS
C-B2 local-hash treatment NOT QUALIFIED
C3-03 engineering PASS
C-B5 deterministic reranker/calibration treatment NOT QUALIFIED
C3 engineering COMPLETE
C4 Typed Graph AUTHORIZED
P0/P1 artifact findings: 0
```
