# Code Source C1 Gate Review

## 第四轮最小关闭结论（2026-07-27，当前结论）

复审时间：2026-07-27 14:56:19 +0800

### Gate 裁决

| Gate | 第四轮结论 | 依据 | 后续授权 |
| --- | --- | --- | --- |
| C1-01 Code Source Contract | **PASS** | 本轮静态复核与 209 项 C1 三文件专项未发现新 P1/P2；既有合同结论保持通过 | 已完成 |
| C1-02 V1 Adapter / Shadow | **PASS** | `P1-C1-02-R3-01`、`P1-C1-02-R3-02` 的最小反例及 nonce tamper 补充边界均已关闭 | 已完成 |
| C1 overall | **PASS** | C1-01、C1-02 均通过 | 允许进入 C2 |
| C2 | **AUTHORIZED** | C1 overall 已通过 | 可按统一开发合同开始 |

```text
C1-01: PASS
C1-02: PASS
C1 overall: PASS
C2: AUTHORIZED
```

本轮直接使用 `/Users/example/project/rag` 共享 dirty `main`，HEAD
`bc3326edc761e3bdb42ed78726a21f314ab44974`；未创建 branch/worktree/子任务，未
commit/push，未修改实现、测试、依赖或其他文档。除本报告外没有持久化写入。以下第三轮
及更早轮次原文完整保留，作为历史审查记录，不代表当前 Gate 状态。

### R3-01 最小关闭复核：严格 identity registry

- `HybridRetriever` authority 与 `CodeShadowRunner` state 均移出实例 `__dict__`，分别
  存放于以 `id(obj)` 为键的进程内 registry；lookup 同时要求
  `entry.reference() is obj`。恒定 hash 且互相 `eq` 的 Retriever/Runner 子类仍取得
  不同 authority/key/state/lock/slot/nonce counter，独立 probe 中 `__hash__`、
  `__eq__` 调用计数均为 0。
- weakref GC callback 仅在 registry 当前 entry 的 `reference is callback_reference`
  时删除；陈旧 callback 不能移除另一对象的当前 entry，真实 GC 会移除精确死 entry。
- `__new__` + `__dict__` 的未注册 clone 无法 lookup authority/state；全局
  `copyreg` 与局部 `Pickler.dispatch_table` 的 constructor + `BUILD` state restore
  均由固定失败的 `__setstate__` 拒绝。constructor-only clone 即使合法构造，也只获得
  新的独立 authority/state，不能验证或打开原实例 evidence/envelope。

结论：`P1-C1-02-R3-01` **CLOSED**。

### R3-02 最小关闭复核：single-pass bounded normalize

- 不可信 JSON 容器只接受 exact builtin `dict`/`list`；自定义/撒谎/翻转
  `Mapping`、`Sequence`、`Iterator` 以及 `dict`/`list` 子类在调用其 `len`、迭代器或
  `getitem` 前即固定拒绝。独立 probe 与测试均确认 length/iteration/access 计数为 0。
- `_build_bounded_submission()` 在一次有界遍历中生成唯一私有 normalized tree，同时
  执行 items/nodes/depth/string/UTF-8/final-byte 限额；最终 canonical plaintext
  只构建一次。之后 identity 只读取该私有副本，AES-GCM 只读取其 bounded plaintext；
  caller 随后修改 request/legacy response 不会改变 identity 或密封明文。
- oversized/custom/stateful 拒绝发生在 identity/AES 前，固定为
  `snapshot_oversized`，且 slot、pending、sealed-job registry 与 timer 均清理；正常
  后续提交仍可运行。worker 解封后的 legacy payload 继续经深层只读 freeze。

结论：`P1-C1-02-R3-02` **CLOSED**。

### Nonce tamper 补充复核

nonce 首字节 tamper 使用 `value[0] ^ 1`，因此原首字节 `0x58` 必定变为 `0x59`；
对 `0..255` 每个可能首字节均确定生成不同 nonce。定向 AES-GCM 回归对全部 256 个值
逐一密封并篡改，均返回固定 `snapshot_authentication_failed`，没有等值漏网。

### 本轮独立执行证据

```text
短 stdin identity/snapshot/nonce probe: PASS
定向 adversarial pytest: 27 passed
C1 三文件专项:
  tests/test_code_rag_contracts.py
  tests/test_code_rag_v1_adapter.py
  tests/test_code_rag_shadow.py
  209 passed
git diff --check: PASS
```

短 probe 首版在序列化 Runner 的公开 `adapter` 字段时先命中 Retriever 的预期
fail-closed；将 reducer state 缩为纯标量后，精确命中 Runner `BUILD/__setstate__`
边界并通过。该次 harness 路径校准不是产品失败。

主控已独立完成并提供以下关闭证据，本最小 Gate 为避免再次触发 systemError 未重复：

```text
全量 pytest: 338 passed
Golden: 18 passed
ruff / format / 只读 compile: PASS
uv lock: PASS
正式库 hash / quick_check / schema: PASS
only-main: PASS
```

第四轮没有发现新 P1/P2。C1 最小关闭完成，C2 正式授权。

审查轮次：C1-02 第二轮独立 Gate；C1-01 已通过并保留为历史快照

审查日期：2026-07-27

共享工作目录：`/Users/example/project/rag`

审查分支 / HEAD：`main` /
`bc3326edc761e3bdb42ed78726a21f314ab44974`

审查对象：

- `src/evidence_rag/retrieval.py`
- `src/evidence_rag/rag/sources/code/contracts.py`
- `src/evidence_rag/rag/sources/code/v1_adapter.py`
- `src/evidence_rag/rag/sources/code/shadow.py`
- `src/evidence_rag/rag/sources/code/__init__.py`
- `src/evidence_rag/runtime.py`
- `src/evidence_rag/platform/service.py` 的 C1-02 局部补丁
- `tests/test_code_rag_contracts.py`
- `tests/test_code_rag_v1_adapter.py`
- `tests/test_code_rag_shadow.py`

## C1-02 第二轮当前 Gate 结论

复审时间：2026-07-27 12:06:33 +0800

### 1. Gate 结论

| Gate | 第二轮审查结论 | 直接原因 | 后续授权 |
| --- | --- | --- | --- |
| C1-01 Code Source Contract | **PASS** | 85 项合同回归包含 canonical/deep-freeze/copy/strict-float/NFC/path/status/graph-budget，全部通过；本轮没有发现 C1-01 新回归 | 已完成 |
| C1-02 V1 Adapter / Shadow | **FAIL** | 首轮 3P1/2P2 的原始反例均已关闭，但第二轮独立 probes 新复现 3 个 P1：retriever copy/deepcopy 复制 attestation key；实际排队 worker 仍保存 query/profile/ACL/entity 明文；自实现 stream+HMAC 密封缺少 nonce-reuse/replay/request binding，并在 request path 做无大小上界的同步序列化/逐字节密封 | 修复并复审 |
| C1 overall | **BLOCKED** | C1-02 的安全、attestation 与 non-blocking 边界未关闭 | 不得进入 C2 |
| C2 | **NOT_AUTHORIZED** | C1 overall 未通过 | 禁止开始 schema/store/unit/retrieval |

当前状态：

```text
C1-01: PASS
C1-02: FAIL
C1 overall: BLOCKED
C2: NOT_AUTHORIZED
```

工程绿灯不能覆盖独立最小反例：

```text
C1 专项 pytest: 148 passed
Golden digest 定向 pytest: 1 passed
全量 pytest: 277 passed
ruff check src tests: PASS
C1 锁内相关 9 文件 format: PASS
diff check: PASS
src/tests 147 Python sources 只读 compile: PASS
正式库 hash/quick_check/C2 schema: PASS
only main: PASS
```

第一次全量复验曾得到 `276 passed, 1 failed`：唯一失败为
`tests/test_code_golden_dataset.py::test_manifest_versions_files_and_hashes_are_pinned`。
主控随后确认原因是其上一轮误用 `compileall -f src tests` 生成了 13 个
`tests/fixtures/code_golden/**/__pycache__/*.pyc` 验证副产物，而非 C1-02 实现或用户
改动。主控按精确清单删除这 13 个 `.pyc` 及对应空目录，未触碰 fixture 源文件、
manifest 或实现。本 Gate 只读确认 bytecode/空目录均已消失后，独立重跑 Golden digest
为 **1 passed**、全量为 **277 passed**；该临时失败记录为“主控验证副产物已清理并
复验”，不列为 C1-02 缺陷。后续 compile 只允许内存 `compile(..., "exec")` 并设置
`PYTHONDONTWRITEBYTECODE=1`，禁止再使用 `compileall` 生成 bytecode。

### 2. 第二轮审查范围、dirty main 与写锁

本轮完整重读：

- `retrieval.py` 的真实 `HybridRetriever.search()`、channel execution evidence、
  canonical request/response/attestation 与 legacy response 边界；
- `contracts.py` 的 `complete_pruned` 及 C1-01 全部 gate-sensitive invariant；
- `v1_adapter.py` 的验证、转换、rank/count/outcome/provenance；
- 重写后的 `shadow.py`，包括 queue admission、snapshot、telemetry、metric、timer/worker
  race、V2 protocol；
- code exports、Runtime wiring、Platform 两个 shadow submit 接点和 constructor
  identity check；
- 三份 C1 tests，共收集 148 项；
- legacy store 的 post-scope/post-ACL lexical/dense 查询语义；
- C1-02 plan、统一开发合同和 source design 的 ACL/安全/发布门槛。

开始与结束基线：

```text
workspace: /Users/example/project/rag
branch: main
HEAD: bc3326edc761e3bdb42ed78726a21f314ab44974
git status --porcelain=v1: 60 个既有顶层状态项
git worktree list: 只有 /Users/example/project/rag 的 main
```

共享 main 在本审开始前已经 dirty；`platform/service.py` 仍混有 graph/overview 等锁外
用户改动。本审没有清理、覆盖、格式化或归因这些内容。

本轮持久写入严格只有：

```text
docs/rag-optimization/development/reviews/02_CODE_C1_GATE_REVIEW.md
```

所有新 adversarial probes 均经 Python stdin 执行，没有持久化到实现、测试、config 或
其他文件。没有 branch/worktree、子智能体/其他会话、commit/push，也没有更新
development/source/interview 文档。

### 3. 首轮 3P1/2P2 原始反例关闭状态

| 首轮缺陷 | 第二轮独立重放 | 状态 |
| --- | --- | --- |
| P1-C1-02-01 pruned hit 被报 no-match、返回子集伪造 raw rank | 真实 HybridRetriever 的 sparse/dense 全剪枝均输出 `complete_pruned`；top-k=1、rank gap、tie、duplicate entity 均使用 truncation 前 unique entity 顺序；adapter 未重排 | **原始反例 CLOSED** |
| P1-C1-02-02 ACL execution scope 无证明 | request HMAC 覆盖 query/limit/project/repos/commit/include_edges、完整 scope、enforce ACL、requested/effective ACL policy；payload/rank/count/evidence/ACL replay 均 fail-closed | **原始反例 CLOSED**，但 copy key 新绕过见 §5 |
| P1-C1-02-03 telemetry 泄露低熵输入/generation/动态异常名 | query/query_id/profile/entity/generation 均为 per-runner keyed HMAC；异常只输出固定 stage/error enum；observation 不含动态 message/repr | **原始 observation 反例 CLOSED**，但排队明文与 key-copy 新缺陷见 §6 |
| P2-C1-02-04 overlap 误导 | stable first-rank dedup、top-k Jaccard、双向 recall、both-empty/one-empty、不等长严格子集、duplicate rank 全部符合定义 | **CLOSED** |
| P2-C1-02-05 timeout 接受 NaN/Inf/错误 primitive | constructor 只接受 exact finite positive float；flush/drain/close 只接受 exact finite nonnegative float；bool/int/string/NaN/±Inf 均拒绝 | **CLOSED** |

### 4. 第二轮独立通过的能力

以下能力有实现审查、现有测试和独立 stdin probes 三重证据；修复新 P1 时必须保持。

#### 4.1 真实 legacy truth、scope 与 adapter

对真实 `HybridRetriever.search()` 注入受控 post-scope lexical/dense rows，而不是只依赖
test fake。独立输出：

```text
sparse_all_pruned:
  lexical hit_count=1, returned_ranks=()
  sparse outcome=complete_pruned

dense_all_pruned:
  dense hit_count=1, returned_ranks=()
  dense outcome=complete_pruned

gap_tie_duplicate, top_k=1:
  lexical unique hit_count=3, returned raw rank=2
  dense unique hit_count=2, returned raw rank=0
```

结论：

- lexical/dense 均在最终 top-k 之前、相同 scope/ACL 查询之后生成 unique entity
  `hit_count` 和真实 zero-based raw rank；
- duplicate source rows 不膨胀 unique hit_count，stable tie 不改变 evidence 顺序；
- hit_count=0 且零返回才是 `complete_no_match`；
- hit_count>0 且零返回是 `complete_pruned`；
- `complete_with_hits` 必有 candidate，rank 严格小于 total hit_count；
- adapter candidate 顺序、fused score、repository/project/commit/ACL/generation/locator
  与 legacy 返回一致；
- direct path 的 terminal provenance 完整，unsupported exact/graph/history/test 和
  calibration 均显式 disabled；
- raw-v1 unit ID 对输入字段顺序与 NFC 等价输入稳定，generation/view/locator 改变会
  重建新 ID；
- commit mismatch 保留实际 returned commit 并明确标记 mismatch，不伪造 exact scope。

独立篡改矩阵均 fail-closed：

```text
trace count mutation
response item/score/snippet mutation
rank/count/evidence mutation
missing evidence / plain dict copy
forged attestation
separately constructed cross-retriever replay
query/limit/project/repository/commit replay
enforce_acl True/False replay
allowed ACL set change
```

requested/effective ACL refs 使用排序去重 canonicalization；相同 ACL 集合的重排/重复不会
改变 execution meaning。request/response/attestation 比较使用 `hmac.compare_digest`。
`LegacySearchResponse` 的公开 JSON/dict 不含 evidence、ACL hidden refs、key 或 tag；
endpoint response contract 保持兼容。`adapt_response()` 不执行检索，`adapter.search()`
恰好执行一次 legacy。

#### 4.2 Platform/Runtime、metric 与并发隔离

```text
PASS endpoint legacy、adapter.legacy、shadow.adapter 是同一实例
PASS 错误 wiring constructor fail-fast
PASS flag off 零 submit、零 adapt、零额外 retrieval
PASS rag_code_engine=v2 仍不切用户路径
PASS flag on/off 除随机 query_id/duration 外逐字段兼容
PASS Platform submit/profile/shadow 的普通 Exception 不改变 V1
PASS V2 error/slow/wrong contract type 只产生固定 failure/timeout
PASS adapter validation 和 V2 都不重复 legacy retrieval
PASS max_inflight/max_observations 生效，worker/timer 为 daemon
PASS timer/worker dispatch、seal/unseal、adapter/V2 failure 均释放正确
PASS timeout/late completion race 只有一个 terminal observation
PASS late completion 后 slot 可恢复，pending 未出现负数
PASS close 后 submit 固定 dropped，observation 保持 bounded
```

独立 metric 矩阵：

```text
both empty:       Jaccard=1, v1 recall=1, comparison recall=1
one empty:        Jaccard=0, v1 recall=0, empty-side recall=1
strict subset:    Jaccard=2/3, v1 recall=2/3, comparison recall=1
duplicate input:  first rank retained, unique sets equal => all 1
top_k=2:          第三名差异不进入 top-2 指标
```

#### 4.3 C1-01 与无 C2 偷跑

- 85 项 C1-01 contracts tests 全部通过；
- canonical tuple/deep freeze、deprecated `copy()` 禁用、`model_copy` 全量重验证、
  `model_construct` trusted-only 边界保持；
- strict bool/int/float、NaN/Inf、`-0.0` canonical、NFC、whitespace/control/NUL、
  path/status/context/channel/graph-budget 反例均保持关闭；
- code package 仍只有 `contracts.py`、`v1_adapter.py`、`shadow.py` 和 exports；
- 没有 C2 schema/store/unit builder/exact/sparse/dense/graph retriever；
- 正式库没有 `code_retrieval_*`、`code_unit_*`、`code_index_*` schema object。

### 5. P1-C1-02-R2-01 — `HybridRetriever` copy/deepcopy 复制 attestation key，跨实例证据被接受

严重度：**P1**

位置：

- `src/evidence_rag/retrieval.py:57`
- `src/evidence_rag/retrieval.py:61`
- `src/evidence_rag/retrieval.py:233`

每个正常 constructor 会生成独立 `__execution_evidence_key`，所以两个分别构造的
retriever 能正确 fail-closed；但类没有定义 copy/deepcopy 边界。Python 默认复制会把
同一 key 带到新实例。

最小复现：

```python
response = retriever._attest_response(
    request,
    payload,
    channel_entity_ids={"lexical": [], "dense": []},
)

shallow = copy.copy(retriever)
deep = copy.deepcopy(retriever)

assert shallow is not retriever
assert deep is not retriever
shallow.verify_execution_evidence(request, response)  # ACCEPTED
deep.verify_execution_evidence(request, response)     # ACCEPTED
```

独立输出：

```text
copy different_instance True
copy ACCEPTED_CROSS_INSTANCE
deepcopy different_instance True
deepcopy ACCEPTED_CROSS_INSTANCE
```

这违反“runner-local secret”和“跨 retriever fail-closed”。Platform 的正常 wiring identity
check 不足以修复对象本身的公开 copy 边界；任何测试、集成或未来 runner 复制 retriever
后都会得到新的 Python identity 和旧 attestation authority。

验收条件：

1. `HybridRetriever` 明确禁用 `copy.copy` 和 `copy.deepcopy`，或确保复制后生成独立
   attestation key 且原 evidence 必须拒绝；
2. 不得通过复制 store/embedder/key 的默认行为创建新的签名 authority；
3. 增加 shallow/deep copy、copy 后 adapter、原/新实例双向 evidence replay tests；
4. 保持 plain response copy/deepcopy 的合理语义：无 evidence 必拒绝，带有未篡改
   evidence 的同一执行 response 可复制，但任何 payload/evidence mutation 仍失败。

### 6. P1-C1-02-R2-02 — 实际 shadow 排队对象仍保留 query/profile/ACL/entity 明文，runner copy 还复用隐私 key

严重度：**P1**

位置：

- `src/evidence_rag/rag/sources/code/shadow.py:197`
- `src/evidence_rag/rag/sources/code/shadow.py:232`
- `src/evidence_rag/rag/sources/code/shadow.py:274`
- `src/evidence_rag/rag/sources/code/shadow.py:390`

现有测试只断言 `_SealedJsonSnapshot` 自身不含敏感文本。但 `submit()` 创建 worker 时，
实际 `thread._args` 同时保留：

- 明文 `request_snapshot`：query、project、repository、requested ACL refs；
- 明文 `profile`：target identifiers/path/ref 等控制信息；
- 明文 `execution_evidence`：returned entity IDs 与 raw ranks；
- 只有 legacy response payload 被 `_SealedJsonSnapshot` 包裹。

最小复现通过临时拦截 `threading.Thread.start` 捕获实际 worker args，不执行 worker，也不
写文件：

```python
with patch.object(threading.Thread, "start", intercept_code_shadow_start):
    runner.submit(request, profile, attested_response)

queued = repr(captured_worker_args)
```

独立输出：

```text
queued_plaintext 'credential token query' True
queued_plaintext 'PrivateSymbol' True
queued_plaintext 'team:secret' True
queued_plaintext 'code://secret/entity' True
queued_plaintext 'generation-secret' False
queued_plaintext 'credential token content' False
```

因此“密文对象不含明文”的单元测试没有证明“排队 snapshot 不含明文”。被调度延迟、timeout
或 slow V2 拉长生命周期时，这些对象会继续留在 daemon thread args。

另一个同类边界是：

```python
clone = copy.copy(runner)
```

独立输出：

```text
different_runner True
telemetry_key_reused True
snapshot_key_reused True
condition_shared True
slots_shared True
```

这使跨 runner telemetry 可关联、snapshot authority 可复用，并把两个逻辑 runner 接到
相同 semaphore/condition，却保留各自的 immutable counter/closed 值。正常 constructor
的“同 runner 稳定、跨 runner 不同”不覆盖该公开边界。

验收条件：

1. 真正排队的 job/thread args/timer args/observation 只能保留不敏感 control data、
   keyed identifiers 和一个完整 opaque snapshot；query/profile/ACL/entity/generation/
   locator/content/token/credential 不得以明文存在；
2. request、profile、execution evidence 若 worker 必须使用，应与 response 一并安全
   封装并在 worker 内短暂解封；
3. `CodeShadowRunner` 明确禁用 shallow/deep copy，或复制后生成独立 keys、locks、
   semaphore/counters，且跨 runner hash/snapshot 必须不可复用；
4. 新测试必须检查实际 queued worker object graph，而不只检查单个 sealed dataclass 的
   `repr`；
5. 保持现有 fixed error enum、observation redaction、caller mutation isolation 和
   bounded admission。

### 7. P1-C1-02-R2-03 — 自实现 stream+HMAC 密封不满足 nonce-reuse/replay 边界，且同步成本无上界

严重度：**P1**

位置：

- `src/evidence_rag/rag/sources/code/shadow.py:651`
- `src/evidence_rag/rag/sources/code/shadow.py:665`
- `src/evidence_rag/rag/sources/code/shadow.py:686`

`_SealedJsonSnapshot` 没有使用成熟 AEAD。它自行组合：

```text
HMAC-SHA256(key, domain || nonce || counter) 作为 XOR keystream
HMAC-SHA256(key, other-domain || nonce || ciphertext) 作为 tag
```

正向 tamper probes 证明 ciphertext/tag 单 bit 修改会被拒绝，且使用
`hmac.compare_digest`；但这不能证明自实现 construction 的完整安全性。它没有：

- nonce reuse 检测或 misuse resistance；
- job/request/profile/evidence AAD binding；
- one-time consumption / replay 防护；
- snapshot byte/depth/item 上界；
- request-path 序列化/加密 latency 上界。

强制 nonce reuse 的最小复现：

```python
with patch.object(shadow.secrets, "token_bytes", lambda n: b"N" * n):
    first = _seal_json(key, {"value": "A" * 16})
    second = _seal_json(key, {"value": "B" * 16})

assert xor(first.ciphertext, second.ciphertext) == xor(first_plain, second_plain)
```

独立输出：

```text
forced_nonce_equal True
xor_leak True
same_snapshot_replay_accepted True
ciphertext tamper_rejected
tag tamper_rejected
```

随机 128-bit nonce 的正常碰撞概率很低，但 Gate 要求的是正确处理 nonce reuse，而不是
假定它永不发生；上述复现显示一旦 RNG fault/reuse，两个 snapshot 的明文 XOR 会立即
泄露。同一个 snapshot 还可以无限 `_open_json()`，没有消费状态或 job binding。

`submit()` 在返回用户请求前同步执行完整 `json.dumps` 和纯 Python 逐字节 XOR。独立
同进程测量：

```text
payload 1,000 bytes:       0.074 ms
payload 100,000 bytes:     6.597 ms
payload 1,000,000 bytes:  62.844 ms
```

该数字只用于证明成本随 payload 线性增长，不作为固定性能 target。legacy metadata、
scope list 和 nested JSON 没有显式大小/深度上限，因此无法证明 shadow
“non-blocking and bounded”。`max_inflight` 限制 worker 数量，但不能限制当前 request
thread 在 admission 后执行的 snapshot 成本。

验收条件：

1. 不继续维护自实现加密；使用成熟、审计过的 AEAD / misuse-resistant primitive，或把
   queued state 收缩为无需保存敏感明文/密文的更小安全结构；
2. nonce 必须有可证明的唯一性/复用处置；AEAD AAD 至少绑定 job identity、request
   execution identity、profile/evidence version；
3. snapshot 必须 one-shot，重复/跨 job/跨 runner 解封 fail-closed；tamper/unseal 失败
   只产生固定 enum，不泄露异常 message/repr；
4. 对 JSON bytes、nesting、result/item count 建立明确小上界，并在低成本 admission
   阶段 fail-closed；
5. 把序列化/密封从用户 request critical path 移出，或用有明确输入上界和独立 latency
   预算的实现证明 submit 是 bounded non-blocking；
6. 新 tests 覆盖 nonce reuse、replay、wrong AAD/job/request/evidence、key/runner copy、
   oversized/deep payload 和 request-path latency budget。

### 8. 第二轮独立工程检查

执行：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider -q \
  tests/test_code_rag_contracts.py \
  tests/test_code_rag_v1_adapter.py \
  tests/test_code_rag_shadow.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider

.venv/bin/ruff check --no-cache src tests

.venv/bin/ruff format --check \
  src/evidence_rag/retrieval.py \
  src/evidence_rag/rag/sources/code/contracts.py \
  src/evidence_rag/rag/sources/code/v1_adapter.py \
  src/evidence_rag/rag/sources/code/shadow.py \
  src/evidence_rag/rag/sources/code/__init__.py \
  src/evidence_rag/runtime.py \
  tests/test_code_rag_contracts.py \
  tests/test_code_rag_v1_adapter.py \
  tests/test_code_rag_shadow.py

git diff --check

# 对 src/tests 逐个 read + compile(..., "exec")，不写 pyc
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python <read-only compile probe>
```

结果：

| 检查 | 第二轮独立结果 |
| --- | --- |
| C1 专项 | **148 passed**，1 个第三方 deprecation warning |
| Golden digest 定向 | **1 passed in 0.29s**；主控副产物清理后独立复验 |
| 全量 pytest | **277 passed**，1 个第三方 warning，34.80s |
| `ruff check src tests` | **All checks passed** |
| C1 锁内相关 format | **9 files already formatted** |
| `git diff --check` | **PASS** |
| 只读 compile | **147 Python sources compiled** |
| only main | **PASS**；单一 worktree、`main`、HEAD `bc3326...` |

补充只读检查与主控参考一致：

- 仓库根 `ruff check .` 仅有锁外既有
  `rag_ui_v2/debug.py` 1 个 I001、`rag_ui_v2/render.py` 1 个 I001/1 个 E702；
- `ruff format --check src/evidence_rag/platform/service.py` 会报告整文件需格式化，差异来自
  同文件既有 graph/overview 区域；本 Gate 没有格式化或修改该文件；
- C1-02 局部补丁和全仓 `git diff --check` 通过。

### 9. 正式库、C2 schema 与发布边界

本轮全部 tests/probes 前后正式库保持：

```text
path:   var/evidence-rag.sqlite3
size:   1,915,490,304 bytes
mtime:  1785093200
sha256: 89fb45182d1dd6eed9d45873333b6e8640952609d676750ebf10276d4b1983cc
```

immutable read-only SQLite URI 检查：

```text
PRAGMA quick_check = ok
code_retrieval_*/code_unit_*/code_index_* schema objects = 0
```

源码与 package 文件扫描没有发现 C2 schema/store/unit/embedding profile/index/graph
retrieval 偷跑。`retrieval.py` 的 `semantic_links` 既有能力仍保留；本轮没有修改正式库、
C0 artifacts、Golden fixture、manifest 或其他数据。

### 10. 第二轮复审入口

C1-02 再复审至少需要：

1. 关闭 P1-C1-02-R2-01～03，并把以上 copy、实际 worker object graph、nonce reuse、
   replay/AAD、oversize/non-blocking 最小反例固化为 tests；
2. 保持首轮 3P1/2P2 的原始反例全部关闭；
3. 保持 Golden fixture 不含验证 bytecode；后续 compile 只用只读内存
   `compile(..., "exec")`，不得调用 `compileall`；
4. 重跑 C1 专项、Golden digest、全量 pytest、`ruff check src tests`、9 文件 format、diff、147
   source compile、only-main 和正式库检查；
5. 再次证明 Platform flag off/on、同实例 wiring、V2 isolation、C1-01 invariant 和无
   C2 偷跑。

在此之前：

```text
C1-01: PASS
C1-02: FAIL
C1 overall: BLOCKED
C2: NOT_AUTHORIZED
```

本 Gate 不更新 development/interview 文档；只在 C1 总门真实 PASS 后由主控统一同步。

---

## C1-02 首轮 Gate 快照（历史记录，已由以上第二轮结论取代）

复审时间：2026-07-27 08:14:38 +0800

### 1. Gate 结论

| Gate | C1-02 审查结论 | 直接原因 | 后续授权 |
| --- | --- | --- | --- |
| C1-01 Code Source Contract | **PASS** | 第三轮合同 Gate 结论保持有效；本轮没有修改或推翻 C1-01 | 已完成 |
| C1-02 V1 Adapter / Shadow | **FAIL** | 独立 probes 复现 3 个 P1、2 个 P2：adapter 伪造 pruned channel no-match/raw rank，ACL execution scope 无法由 payload 证明，shadow telemetry 可泄露低熵输入/业务 generation/动态异常名，overlap 定义误导，timeout 接受非有限值 | 修复并复审 |
| C1 overall | **BLOCKED** | C1-02 是 C1 总门未关闭的 blocking dependency | 不得进入 C2 |
| C2 | **NOT_AUTHORIZED** | C1 overall 未通过 | 禁止开始 schema/store/unit/retrieval |

当前状态：

```text
C1-01: PASS
C1-02: FAIL
C1 overall: BLOCKED
C2: NOT_AUTHORIZED
```

工程绿灯不能覆盖本轮最小反例：

```text
C1 专项 pytest: 106 passed
全量 pytest: 235 passed
ruff: PASS
C1 新增/直接相关文件 format: PASS
platform/service.py 整文件 format: FAIL（既有同文件非 C1 改动）
diff check: PASS
只读 compile: PASS
正式库 hash/quick_check/schema: PASS
only main: PASS
```

### 2. C1-02 审查范围与 dirty main

完整审查：

- `src/evidence_rag/rag/sources/code/v1_adapter.py`；
- `src/evidence_rag/rag/sources/code/shadow.py`；
- `src/evidence_rag/rag/sources/code/__init__.py`；
- `src/evidence_rag/runtime.py`；
- `src/evidence_rag/platform/service.py` 的 C1-02 局部接入补丁；
- `tests/test_code_rag_v1_adapter.py`；
- `tests/test_code_rag_shadow.py`；
- 现有 `contracts.py`、`tests/test_code_rag_contracts.py`；
- legacy `src/evidence_rag/retrieval.py`、`SearchScope`/`EvidenceSearchRequest`。

本轮开始和工程检查结束时均为：

- shared dirty `main`；
- HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`；
- `git status --porcelain=v1` 有 60 个既有 dirty/untracked 顶层状态项；
- `git worktree list --porcelain` 只有 `/Users/example/project/rag`；
- `platform/service.py` 同时有大量既有 graph/overview 等非 C1 用户改动，本 Gate 只审
  `_submit_code_shadow` 及两处调用，不清理、不格式化、不覆盖其他改动。

本轮持久工作区写入严格只有：

```text
docs/rag-optimization/development/reviews/02_CODE_C1_GATE_REVIEW.md
```

所有 adversarial probes 通过 Python stdin 执行，没有持久化到实现、测试或其他文件。
没有创建 branch/worktree/子智能体/其他会话，没有 commit/push，没有修改实现、测试、
config、C0 artifacts、development/source/interview 文档或其他用户文件。

### 3. 已独立通过的 C1-02 能力

以下能力有独立直接证据，修复阻断项时必须保持：

#### 3.1 Adapter 正向等价与 provenance

```text
PASS adapter.search 只调用 legacy.search 一次
PASS adapt_response 不调用 legacy，且不修改输入 payload
PASS candidate entity/top-k/fused-score 顺序与 legacy 返回顺序一致
PASS fused-score tie 保持 legacy 稳定顺序
PASS repository/commit/generation/locator/ACL 与 direct-path terminal 一致
PASS retrieval unit ID 对字段顺序/NFC 稳定，generation/view/locator 改变会换 ID
PASS exact/graph/history/test 显式 disabled
PASS calibration 显式 disabled，不使用 0/None sentinel
PASS empty result 在 trace hit_count 都为 0 时可表示 complete no-match
PASS unauthorized returned candidate 在 enforce_acl=True 时拒绝
PASS commit mismatch 保留 returned commit 并标 `version_alignment=mismatch`
```

commit mismatch 没有篡改 legacy 证据：candidate/path 使用实际 returned commit，
`CodeVersionAlignment` 明确说明它与 resolved query watermark 的关系。该项本身通过。

#### 3.2 Shadow 正常并发与 Platform 隔离

```text
PASS adapter/V2 在 daemon worker 上执行，不重复 legacy retrieval
PASS timeout 先终结 observation，late worker 不产生第二条 observation
PASS timed-out worker 完成后释放 slot
PASS timer.start/worker.start 失败只终结一次并释放 slot
PASS max_inflight 与 max_observations 生效
PASS close/submit 并发未出现 pending<0
PASS flush 等待 accepted job 的 success/error/timeout terminal observation
PASS deep MappingProxy/tuple snapshot 阻止 nested mutation
PASS caller submit 后修改 payload 不影响 worker snapshot
PASS V2 slow/error/protocol wrong-type 不改变 V1
PASS Platform shadow exception 不改变 V1 response
PASS flag off 为零 submit、零 adapt、零额外 retrieval
PASS flag on/off endpoint 除 query_id/duration 外逐字段兼容
PASS `rag_code_engine=v2` 没有切换用户路径
```

上述正常路径通过不抵消下文的 truthfulness、安全与边界缺陷。

### 4. P1-C1-02-01 — Adapter 把 pruned hit 报为 no-match，并伪造返回子集 raw rank

严重度：**P1**

位置：

- `src/evidence_rag/rag/sources/code/v1_adapter.py:140`
- `src/evidence_rag/rag/sources/code/v1_adapter.py:292`
- `src/evidence_rag/rag/sources/code/v1_adapter.py:309`
- `src/evidence_rag/retrieval.py:24`
- `src/evidence_rag/retrieval.py:44`
- `src/evidence_rag/retrieval.py:74`

legacy trace 只提供：

```text
lexical_candidates = channel 总候选数
dense_matches      = threshold 后总匹配数
```

每个 returned item 有 channel score，但没有它在 truncation 前 channel list 中的原始
rank。`_raw_channel_ranks()` 目前只对最终返回子集重排 0..N；这不是 legacy raw rank。
`_channel_outcomes()` 又只看最终返回子集是否包含 channel，忽略 trace 已证明的 pruned
hit。

最小复现一：trace 有 sparse hit，top-k 只有 dense item。

```python
response["results"] = [dense_only_item]
response["trace"]["lexical_candidates"] = 5
result = adapter.adapt_response(request, response)
```

独立输出：

```text
pruned_sparse_trace_hits=5
adapter_sparse_status=complete_no_match
```

这把“channel 完成且有 5 个命中，但融合后没有该 channel item 进入 top-k”写成
`complete_no_match`，是事实错误。

最小复现二：legacy lexical score 直接证明原始零基 rank=3：

```python
lexical_score = 1.0 / (1.0 + 0.15 * 3)
response["results"] = [lexical_item]
response["trace"]["lexical_candidates"] = 5
```

独立输出：

```text
lexical_score_proves_zero_rank=3
adapter_emitted_raw_rank=0
```

dense 更无法反推：payload 只有一个 `dense_score=0.5` returned item 和
`dense_matches=5`，其真实 raw rank 可以是 0～4 中任意值；adapter 固定生成 0。

影响：

- 违反 C1-01 对 raw channel rank 和 no-match 的真实性要求；
- shadow rank delta、overlap、后续 source fusion/evaluation 建立在伪造 rank 上；
- pruned channel 的 hit/no-match 指标会系统性失真；
- 当前 tests 反而把返回子集重排值固化为期望，不能证明 legacy 等价。

验收条件：

1. legacy response/trace 必须为每个 returned entity 提供 truncation 前、零基、真实的
   lexical/dense raw rank，或 adapter 对缺失 rank fail closed；
2. 禁止从 returned subset 的 score 顺序重新枚举 raw rank；
3. 明确区分 channel total hit、returned hit 与 pruned hit；
4. 当前 C1 contract 不允许 `complete_with_hits` 没有 returned candidate；若 trace
   hit>0 但 returned=0，必须选择以下之一：
   - 扩展 contract 表达 pruned hits；
   - 修改 legacy trace/selection 使可返回可验证 representative；
   - adapter fail closed；
   不能写 `complete_no_match`；
5. 增加 sparse/dense pruned、真实 rank gap、tie、两 channel 不同 rank、top_k=1、
   hit_count>returned_count 的独立 tests；
6. 保持 `search()` legacy exactly-once，`adapt_response()` pure conversion。

### 5. P1-C1-02-02 — ACL execution scope 被 exclude，adapter 无法证明 trace 是授权后结果

严重度：**P1**

位置：

- `src/evidence_rag/models.py:70`
- `src/evidence_rag/models.py:71`
- `src/evidence_rag/rag/sources/code/v1_adapter.py:343`
- `src/evidence_rag/rag/sources/code/v1_adapter.py:414`

`SearchScope.allowed_acl_refs` 和 `SearchScope.enforce_acl` 都是 `exclude=True`。
legacy `resolved_scope=request.scope.model_dump()` 与 adapter 的
`expected_scope=request.scope.model_dump()` 因此同时丢失这两个字段。

最小复现：

```text
enforced request:
  allowed_acl_refs=["team:alpha"], enforce_acl=True
unenforced request:
  allowed_acl_refs=[], enforce_acl=False

serialized_scopes_equal=True
```

把 unenforced response 的 returned item 设为恰好允许的 `team:alpha`，但 trace
`lexical_candidates=99`，再用 enforced request 适配：

```text
accepted_unattested_acl_response=complete
claimed_sparse_hit_count=99
returned_candidate_acl=team:alpha
```

逐 item ACL 校验能阻止明显 unauthorized candidate 正文进入结果，这一点通过；但它
不能证明 legacy retrieval 实际执行了 ACL prefilter，也不能证明 hit_count=99 是授权后
计数。未 enforcement 的 response 可以被当成 enforced response，产生 ACL cardinality
side channel，并给后续 trace/metrics 一个虚假的授权 scope。

`PlatformService` 还允许分别注入 `code` 与 `code_v1_adapter`，没有构造时 invariant
证明 `code_v1_adapter.legacy is code`；当前 Runtime 恰好一致，但公共接入面没有封闭。

验收条件：

1. legacy response 提供不可被 `model_dump(exclude=True)` 丢失的 execution-scope
   evidence，至少含 project/repositories/commit、`enforce_acl` 与 canonical allowed
   ACL policy/fingerprint；
2. adapter 必须精确验证该 evidence，enforce false 的 response 不能被 adapt 成
   enforce true；
3. channel hit_count/raw ranks 必须证明是 ACL prefilter 之后产生，不泄露 unauthorized
   cardinality；
4. 保留 per-item repository/ACL 校验作为第二层防线；
5. Platform/runner wiring 必须证明被适配 response 来自同一个 legacy retriever 和同一
   request scope，或使用内部不可混淆的 response envelope；
6. 增加 excluded-field collision、unenforced→enforced replay、unauthorized-pruned-only、
   project/repository/commit mismatch tests。

### 6. P1-C1-02-03 — Shadow telemetry 的低熵 hash、generation 与异常类型仍可泄露

严重度：**P1**

位置：

- `src/evidence_rag/rag/sources/code/shadow.py:355`
- `src/evidence_rag/rag/sources/code/shadow.py:450`
- `src/evidence_rag/rag/sources/code/shadow.py:549`
- `src/evidence_rag/rag/sources/code/shadow.py:555`
- `src/evidence_rag/rag/sources/code/shadow.py:575`

当前 `_hash_text()` 是无 salt/key 的确定性 SHA-256。它不直接保存 plaintext，但对低熵
query/entity/profile 可以离线枚举，而且能跨 observation 关联。现有 test 还明确断言
`query_hash == sha256(query)`，固化了该问题。

最小复现：

```text
dictionary=["help", "rank", "search", "test"]
dictionary_recovered_query=rank
```

`_safe_generation()` 只检查少量敏感 marker 和字符集，以下业务标识会明文进入 observation：

```text
plaintext_generation=("generation://tenant/acquisition-target",)
```

`_exception_code()` 允许任意符合正则的异常类名。动态异常类可以把数据编码进 class name：

```text
exception_telemetry=("probe:AcquisitionTarget",)
```

异常 message/repr 的现有隐藏是正确的，但动态类型名仍不是固定安全码。该问题违反 shadow
不保存敏感全文和 Unauthorized/Secret Leakage=0 的发布边界。

验收条件：

1. query/query_id/profile/entity/rank-delta identity 使用 runner-local keyed HMAC/随机
   salt，或取消不必要的逐值 hash；不得保留可离线字典恢复的裸 digest；
2. 同一 observation 内仍可比较 identity，但不应默认跨进程/跨 run 可关联；
3. generation 默认 hash/redact；若要明文，必须使用有证明的 opaque generation schema，
   不能靠敏感词 denylist；
4. exception telemetry 使用固定 stage + 固定 error enum，不记录任意动态 class name、
   message 或 repr；
5. 增加低熵 dictionary attack、业务 codename generation、动态异常 class、credential/
   locator/entity/query representation 递归扫描 tests。

### 7. P2-C1-02-04 — `top_overlap` 对不等长结果和 duplicate entity 的定义会误导

严重度：**P2**

位置：

- `src/evidence_rag/rag/sources/code/shadow.py:486`

当前 denominator 是两个 top list 长度的最小值。独立结果：

```text
v1=["a","b","c"], comparison=["a"] → top_overlap=1.0
v1=[], comparison=[]              → top_overlap=1.0
```

严格子集被记为满分，即使 comparison 丢失 2/3 的 V1 结果。虽然 observation 另有 result
count，字段名 `top_overlap` 本身仍不足以说明这是 overlap coefficient 而非 recall/Jaccard。

C1 contract 允许同一 stable entity 对应不同 retrieval unit；V2 entity list 因此可能
重复。当前 dict comprehension 采用最后一次 rank：

```text
v1=["a","b"], comparison=["a","a"]
top_overlap=0.5
rank delta for a = 1
```

同一 entity 的 rank 被第二次出现覆盖，rank delta 失去稳定语义。

验收条件：

1. 明确定义指标是 Jaccard、overlap coefficient、双向 recall 或 fixed-top-k overlap；
2. 若保留 `min(length)`，字段必须明确改名/文档化，且不能把它当完整 top-k 等价；
3. 明确 both-empty 和 one-empty 语义；
4. stable entity 重复必须在比较前 canonical dedupe，或协议层拒绝；
5. rank delta 使用确定的首次/最佳 rank，并覆盖不同长度、duplicates、top_k 截断 tests。

### 8. P2-C1-02-05 — Shadow timeout/flush 接受 NaN 和正 Inf

严重度：**P2**

位置：

- `src/evidence_rag/rag/sources/code/shadow.py:101`
- `src/evidence_rag/rag/sources/code/shadow.py:253`

验证只检查 `timeout_ms <= 0` / `timeout < 0`。NaN 比较为 false，正 Inf 也大于零：

```text
CodeShadowRunner(..., timeout_ms=nan) → ACCEPT
CodeShadowRunner(..., timeout_ms=inf) → ACCEPT
```

正 Inf 会使 timeout 失去 hard bound；NaN 的 Timer/Condition 行为依赖运行时。若 V2
永久阻塞，job/slot 可永久占用，`flush`/`close(wait=True)` 的行为也不再满足声明。

有限 timeout 下的 timeout race、late completion、slot release 和 pending 计数均独立
通过；缺陷只在非有限公共输入边界。

验收条件：

1. constructor `timeout_ms` 与 `flush/drain/close timeout` 必须 exact numeric、
   `math.isfinite()` 且满足各自正/非负边界；
2. 增加 bool、NaN、±Inf、0、极小有限值和 timeout race tests；
3. 复验 timer/worker dispatch failure、late completion、slot recovery 与
   `pending >= 0`。

### 9. 独立 adversarial 与工程检查

独立 probes：

| 类别 | 结果 |
| --- | --- |
| adapter exactly-once/pure/order/provenance/unit/disabled/commit/ACL 正向 assertions | PASS |
| pruned sparse no-match | **FAIL：5 hits → complete_no_match** |
| lexical raw rank | **FAIL：可证明 rank=3 → 输出 0** |
| dense returned-subset raw rank | **FAIL：payload 不可识别 → 输出 0** |
| excluded ACL scope replay | **FAIL：unenforced payload 可作为 enforced 接受** |
| snapshot/read-only/caller mutation | PASS |
| timeout late completion/slot release | PASS |
| timer/worker dispatch failure recovery | PASS |
| 30-way close/submit race | PASS，`pending=0`、observation bounded |
| slow/error/wrong-type V2 isolation | PASS |
| low-entropy query dictionary | **FAIL：恢复 `rank`** |
| generation/exception telemetry | **FAIL：业务 generation/class name 明文** |
| unequal/duplicate ranking metric | **FAIL：满分子集/last-rank overwrite** |
| nonfinite timeout | **FAIL：NaN/+Inf accepted** |

工程命令与结果：

| 检查 | C1-02 独立结果 |
| --- | --- |
| C1 专项 | **106 passed**, 1 warning，1.14s |
| 全量 pytest | **235 passed**, 1 warning，33.46s |
| 全量 ruff | **All checks passed** |
| C1 新增/直接相关 format（不含整份 platform 文件） | **11 files already formatted** |
| `platform/service.py` 整文件 format | **FAIL：1 file would be reformatted** |
| `git diff --check` | PASS |
| 只读 compile | **147 Python sources** compiled，无 bytecode 写入 |
| worktree/branch | 只有 shared `main`，HEAD `bc3326...` |

`platform/service.py` 的 Ruff format diff 位于既有 graph/overview/semantic 改动；C1-02
新增的 constructor、search submit 与 `_submit_code_shadow` 局部没有被 formatter
列为变更。因本 Gate 只有报告写锁，不能格式化或重写该同文件用户改动。因此主控的
“相关 format PASS”只有在排除整份 co-located platform 文件时可复现；整文件检查不能
报告为 PASS。

### 10. 正式库、Runtime 与未越级检查

tests/probes 前后正式库保持：

```text
path:   var/evidence-rag.sqlite3
size:   1,915,490,304 bytes
mtime:  1785093200
sha256: 89fb45182d1dd6eed9d45873333b6e8640952609d676750ebf10276d4b1983cc
```

只读检查：

```text
PRAGMA quick_check = ok
code_retrieval_*/code_unit_*/code_index_* schema objects = []
```

Runtime 新增 adapter 与 optional shadow 字段；shadow 仅由 `rag_code_shadow` 控制。
`rag_code_engine` 在源码中仍只被 config 解析，没有任何 V2 用户路径分支，因此
`rag_code_engine=v2` 不会切换 endpoint。Platform flag off 不调用 submit/profile/adapt，
flag on 的 shadow 异常被隔离。

`src/evidence_rag/rag/**` 除 C1 contracts、V1 adapter、shadow 外没有 V2 retriever、
C2 schema/store/unit/index/retrieval；正式库 schema=0，C2 未偷跑。

### 11. C1-02 复审入口

复审至少需要：

1. 关闭 P1-C1-02-01，禁止 pruned no-match 与返回子集 raw-rank 伪造；
2. 关闭 P1-C1-02-02，为 ACL execution scope 和 post-ACL count 提供可验证证据；
3. 关闭 P1-C1-02-03，移除低熵裸 hash、明文 generation 和动态异常名泄漏；
4. 关闭 P2-C1-02-04/05，明确 ranking metric 并拒绝非有限 timeout；
5. 把以上最小复现固化为 tests；
6. 重跑专项/全量 pytest、全量 ruff、相关/整文件 format、diff/compile、
   only-main 与正式库检查；
7. 保持 Platform 既有用户改动，不做无关格式化或重构。

在此之前：

```text
C1-01: PASS
C1-02: FAIL
C1 overall: BLOCKED
C2: NOT_AUTHORIZED
```

不得开始 C2。本 Gate 不同步 development/interview 文档；由主控在 C1 总门真正 PASS
后统一处理。

---

## C1-01 第三轮 Gate 快照（历史记录，已由以上 C1-02 结论接续）

复审时间：2026-07-27 07:24:55 +0800

### 1. Gate 结论

| Gate | 第三轮结论 | 直接原因 | 后续授权 |
| --- | --- | --- | --- |
| C1-01 Code Source Contract | **PASS** | 第二轮 P1-R2-01/02 与 P2-R2-03/04 均已独立关闭；前两轮 path/status/context/channel/deep-freeze/whitespace/profile/graph-budget 缺陷保持关闭；未发现新的 P0/P1/P2 | C1-01 完成 |
| C1-02 V1 Adapter / Shadow | **NOT_STARTED** | 源码、schema 与正式库检查均确认尚未实现 adapter/shadow/runtime wiring | **AUTHORIZED** |
| C1 overall | **IN_PROGRESS** | C1-01 已通过；C1-02 尚未开始 | 可按计划进入 C1-02 |

当前状态：

```text
C1-01: PASS
C1-02: NOT_STARTED
C1 overall: IN_PROGRESS
C1-02: AUTHORIZED
```

第三轮工程检查：

```text
专项 pytest: 85 passed
全量 pytest: 214 passed
ruff: PASS
相关 format: PASS
diff check: PASS
只读 compile: PASS
正式库 hash/quick_check/schema: PASS
only main: PASS
```

主控给出的 85/214 与正式库参考值均已独立复现；Gate 结论还额外建立在两组临时
adversarial probes 和 62,500 个无命中 SourceResult 状态组合穷举上，而不是仅信任现有
tests。

### 2. 第三轮审查边界与 dirty main

第三轮开始和工程检查结束时均为：

- shared dirty `main`；
- HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`；
- `git status --porcelain=v1` 有 57 个既有 dirty/untracked 顶层状态项；
- `git worktree list --porcelain` 只有 `/Users/example/project/rag`；
- C1 contracts/tests/config 和本 Gate 报告仍位于同一 dirty main。

第三轮持久工作区写入严格只有：

```text
docs/rag-optimization/development/reviews/02_CODE_C1_GATE_REVIEW.md
```

临时 probes 通过 Python stdin 执行，没有持久化到实现、测试或其他文件。第三轮没有创建
branch/worktree/子智能体/其他会话，没有 commit/push，没有修改实现、测试、config、
C0 artifacts、development/source/interview 文档或其他用户文件。共享 main 的既有
dirty 内容不由本 Gate 清理、覆盖或归因。

### 3. 第二轮四项缺陷关闭状态

| 第二轮缺陷 | 第三轮独立结论 | 直接证据 |
| --- | --- | --- |
| P1-R2-01 deprecated copy validator bypass | **CLOSED** | 24 个导出的 contract model 全部继承受保护的 `copy`/`model_copy`；实例 `copy()`/`copy(update)` 均直接拒绝；`model_copy` 与 Python `copy.replace` 均重新验证；非法 raw/path/context/outcome/result update 不能生成对象 |
| P1-R2-02 hit_count/raw/source rank 矛盾 | **CLOSED** | raw rank 为零基且 `< hit_count`，同一 channel 返回 rank 唯一；`hit_count` 明确定义为截断前总命中数；source rank 零基、唯一、连续，candidate 输入反序会 canonicalize，duplicate/gap/negative 拒绝 |
| P2-R2-03 exact float/signed zero | **CLOSED** | 六类 float 字段均要求原始值 `type(value) is float`；bool/int/string/NaN/Inf 拒绝；`-0.0` 统一成 `0.0`，跨各 model 的 canonical bytes/hash 与正零一致 |
| P2-R2-04 repository/locator provenance | **CLOSED** | node/candidate/context 均有结构化 repository；hop locator 有 owner entity/repository/version/generation/ACL；path 对 stored source/target 重校验；incoming locator 仍属于 stored source；所有错配反例拒绝 |

首轮 P1-C1-01-01～03 与 P2-C1-01-04～05 的 cycle/version/generation/ACL、
status/context/channel、deep-freeze/canonical、whitespace/profile、graph-budget/hop
关闭状态也全部回归通过。

### 4. 第三轮独立审查明细

#### 4.1 Copy、全量重验证与 trusted-only 边界

导出的 24 个 Pydantic contract model 均解析到 `_ContractModel.copy` 和
`_ContractModel.model_copy`。独立结果：

```text
PASS reject candidate.copy()
PASS reject candidate.copy(update=invalid)
PASS reject path/context/outcome/result copy(update=invalid)
PASS reject model_copy(raw set mismatch)
PASS reject model_copy(unknown field)
PASS reject model_copy(exact-float wrong type)
PASS copy.replace performs validated model_copy
PASS copy.copy/deepcopy preserve valid canonical state
PASS valid model_copy accepted and canonical
```

`model_copy` 使用 round-trip dump、应用 update 后调用目标类型 `model_validate`，会重新
执行 nested/after validators；现有 path 还显式重跑 hop 与 locator owner 校验。
`model_construct` 在 `_ContractModel` 文档中明确标为 trusted-only escape，不作为普通
合同构造 API。

额外 tamper probes 也显式调用了 Pydantic 基类的未绑定
`BaseModel.copy(instance, ...)`/`BaseModel.model_copy(instance, ...)`。与直接改
`__dict__` 或 `object.__setattr__` 一样，这会故意绕开子类方法解析；它不是 contract
实例暴露的 `copy`/`model_copy` 路径，也不属于本 Gate 承诺的 hostile in-process
tamper resistance。仓库中没有这种调用。普通调用方可达的实例 API 与
`copy.replace` 均已封闭；`model_construct` 是唯一记录在 contract 上的 trusted-only
逃生口，因此不构成新的 Gate 缺陷。

#### 4.2 Raw observation、hit_count 与 canonical source rank

独立构造两个候选并反转 candidate/outcome/raw 输入顺序后：

```text
PASS raw score/rank canonical frozen tuple
PASS nested raw entry frozen
PASS raw input reorder gives identical bytes/hash
PASS reversed candidate tuple canonicalizes by within_source_rank
PASS reversed result gives identical bytes/hash
PASS reject raw_rank == hit_count
PASS reject raw_rank > hit_count
PASS reject duplicate returned raw rank in one channel
PASS accept returned raw ranks [0, 2] with hit_count=3
PASS reject duplicate/gap/negative within_source_rank
```

最后一个合法 hit_count 反例证明它表示 channel 截断前总命中数，而不是 result 中返回
的 candidate 数；因此允许返回数小于 hit_count，同时仍能验证每个 raw rank 的上界。

#### 4.3 Exact float 与 canonical signed zero

逐一覆盖：

- `CodeChannelScore.score`；
- `CodeRetrievalCandidate.source_fused_score`；
- `CodeCalibratedScore.score`；
- `CodeRelationHop.confidence`；
- `CodeRelationPath.path_score`；
- `CodeSourceResult.latency_ms`。

每类字段均独立拒绝 `True`、`1`、`"1.0"`、NaN、正/负 Inf。每类 `-0.0` model 都与
对应 `0.0` model 相等，canonical JSON 不包含 `-0.0`，canonical bytes/hash 完全相同。

#### 4.4 Repository、typed locator 与 path provenance

`CodeRelationNode`、candidate、context 的 terminal tuple 均包含 repository/version/
generation/locator/ACL。`CodeRelationHop` 的 stored endpoint 除 entity 外还分别保存
repository/version/generation/ACL；`CodeRelationLocator` 保存 locator 与 owner
entity/repository/version/generation/ACL。

独立结果：

```text
PASS accept outgoing multi-hop
PASS accept incoming multi-hop
PASS incoming locator owner is stored source, not traversal-left node
PASS reject wrong locator owner entity
PASS reject wrong locator repository/version/generation/ACL
PASS reject cross-repository hop
PASS reject node/hop repository mismatch
PASS reject candidate/terminal repository mismatch
PASS reject context/candidate repository mismatch
```

locator 字符串仍是允许 call-site/子范围的 opaque locator；其治理身份来自结构化 owner
tuple，不能简单强制等于 node locator。该 tuple 与 stored source 的 repository/version/
generation/ACL 已被 hop 和 path 双层验证，满足第二轮验收条件。

#### 4.5 NFC、空白与控制字符

所有 `ContractText` 先规范为 NFC，再做空白/控制字符检查。第三轮分别用 composed 和
decomposed 输入构造 Query/Profile collections、node 全部六个控制字段、typed error
以及 result query/index/watermark：

```text
PASS NFC-equivalent models equal
PASS NFC-equivalent canonical bytes/hash equal
PASS NFC-equivalent duplicate query target rejected
PASS reject leading/trailing whitespace
PASS reject newline/tab/NUL/NBSP/zero-width/bidi control text
PASS reject whitespace-only/NUL Context
PASS preserve valid Context code layout
PASS embedding profile env/direct use same fullmatch
```

Context `content` 是保留代码布局的正文而不是控制身份字段，因此不做 NFC 改写；它仍会
拒绝空白-only 和不允许的控制字符。

#### 4.6 SourceResult 六态 outcome、status/context/error/fallback

除现有 tests 外，第三轮枚举六个 channel 上
`disabled/complete_no_match/timeout/error/unavailable` 的全部 15,625 个组合，并对每个
组合尝试四种 overall status，共 62,500 个构造：

```text
accepted exactly once for every non-all-disabled combination: 15,624
rejected illegal declarations: 46,876
all-disabled combination: all four statuses rejected
```

矩阵规则独立成立：

- 有完成、无失败 → `complete`；
- 有完成、有失败 → `partial`，包括 zero-hit；
- 无完成且有 timeout → `timeout`（timeout 对混合失败优先）；
- 无完成、有非-timeout failure 且无 timeout → `unavailable`；
- completed hit outcome 必须有对应 returned candidate；
- timeout/unavailable 不得携带 candidate/context；
- complete empty no-match 合法但不得携带 context；
- typed channel/fallback errors 必须精确镜像并 canonicalize；
- fallback succeeded 只允许 complete；
- fallback failed 只允许非-complete 且必须有 typed failure；
- orphan、cross-provenance、semantic duplicate context 均拒绝。

Calibration 的 `calibrated/disabled/unavailable` 和 channel 的六态 outcome 都是
discriminated state，不使用 `0` 或 `None` 伪装 unsupported/unavailable/no-match。

#### 4.7 七个要求合同、Settings 与 import boundary

七个要求合同均完整公开：

```text
CodeQueryProfile
CodeRetrievalBudget
CodeRetrievalCandidate
CodeRelationPath
CodeContextBlock
CodeSourceStatus
CodeSourceResult
```

共同模型均 `extra="forbid"`、`frozen=True`、strict primitive、NaN/Inf forbidden；
stable entity 与 rebuildable unit 分离；raw score/rank、source fusion、calibration、
version/generation、fact/derivation/review、role、typed path、locator/token/ACL 均为独立
字段，没有重新塞入自由 metadata blob。

Settings 仍精确九项，默认全 V1/off/raw/local-hash/snippet/0；现有显式构造兼容；enum/
bool/profile/percentage 的 direct/env 输入均 fail fast；无 secret 字段；全部九个
`RAG_CODE_*` 读取只在 `config.py`。

contracts import allowlist 精确为：

```text
__future__
collections.abc
enum
hashlib
json
pydantic
typing
unicodedata
```

没有 Platform/Document/Codex/runtime/storage/store 依赖。

### 5. 第三轮 adversarial 与工程检查结果

临时 stdin probes：

| Probe | 第三轮结果 |
| --- | --- |
| copy/rank/float/repository/NFC 定向 assertions | **127 PASS** |
| path/status/context/fallback/whitespace/profile/graph/import 回归 assertions | **63 PASS** |
| 无命中 SourceResult overall-status 穷举 | **62,500/62,500 符合矩阵** |

现有与全库检查：

| 检查 | 第三轮独立结果 |
| --- | --- |
| C1 专项 | **85 passed in 0.35s** |
| 全量 pytest | **214 passed**, 1 个第三方 deprecation warning，35.12s |
| 全量 ruff | **All checks passed** |
| C1 相关 format | **6 files already formatted** |
| `git diff --check` | PASS |
| 只读 compile | **143 Python sources** compiled，无 bytecode 写入 |
| worktree/branch | 只有 shared `main`，HEAD `bc3326...` |

第三轮没有新的 P0、P1 或 P2。

### 6. 正式库、C1-02 与未越级检查

tests/probes 前后正式库保持：

```text
path:   var/evidence-rag.sqlite3
size:   1,915,490,304 bytes
mtime:  1785093200
sha256: 89fb45182d1dd6eed9d45873333b6e8640952609d676750ebf10276d4b1983cc
```

只读检查：

```text
PRAGMA quick_check = ok
code_retrieval_*/code_unit_*/code_index_* schema objects = []
```

`src/evidence_rag/rag/**` 仍只有 package init 与 contracts；没有
`CodeSourceRetrieverV1Adapter`、`CodeShadowRunner`、Runtime/Platform wiring、C2
schema/store/index/retrieval。正式库无 C1/C2 schema 或数据写入，C1-02 未偷跑。

### 7. 第三轮授权

第二轮 P1-R2-01/02 与 P2-R2-03/04 已关闭，前两轮全部回归保持关闭，工程门、正式库门
和 only-main 边界均通过。因此：

```text
C1-01: PASS
C1-02: NOT_STARTED
C1 overall: IN_PROGRESS
C1-02: AUTHORIZED
```

C1-02 可按统一开发契约和 C1-02 计划开始；C1 overall 在 C1-02 Gate 完成前保持
`IN_PROGRESS`。本报告继续作为 C1-02 Gate 的同一写锁；本轮不更新 interview 文档。

---

## 第二轮 Gate 快照（历史记录，已由第三轮结论取代）

复审时间：2026-07-27 06:43:19 +0800

### 1. Gate 结论

| Gate | 第二轮结论 | 直接原因 | 后续授权 |
| --- | --- | --- | --- |
| C1-01 Code Source Contract | **FAIL** | 首轮核心修复大部分成立，但独立 probes 新确认 2 个 P1、2 个 P2；deprecated copy 可直接绕过 validator，rank/hit/status provenance 仍可自相矛盾 | 修复并第三轮复审 |
| C1-02 V1 Adapter / Shadow | **NOT_STARTED** | 源码扫描仍没有 adapter、shadow、Runtime/Platform 接入 | **NOT_AUTHORIZED** |
| C1 overall | **BLOCKED** | C1-01 是 C1-02 的 blocking dependency，当前仍有 OPEN P1 | 不得开始 C1-02 |

当前状态：

```text
C1-01: FAIL
C1-02: NOT_STARTED
C1 overall: BLOCKED
C1-02: NOT_AUTHORIZED
```

第二轮工程检查：

```text
专项 pytest: 71 passed
全量 pytest: 200 passed
ruff: PASS
相关 format: PASS
diff check: PASS
只读 compile: PASS
only main: PASS
正式库 hash/quick_check/schema: PASS
```

主控参考的 71/200 计数可以独立复现，但这些测试没有覆盖本轮最小反例，不能替代合同
Gate。

### 2. 第二轮审查边界与 dirty main

第二轮开始和结束均为：

- shared `main`；
- HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`；
- `git status --porcelain=v1` 仍有 57 个既有 dirty/untracked 顶层状态项；
- `git worktree list --porcelain` 只有 `/Users/example/project/rag`；
- C1 contracts/tests/config 与首轮报告仍位于同一 dirty main。

第二轮持久工作区写入仍严格只有：

```text
docs/rag-optimization/development/reviews/02_CODE_C1_GATE_REVIEW.md
```

没有创建 branch/worktree/子智能体/其他会话，没有 commit/push，没有修改实现、测试、
config、C0 artifacts、development/source/interview 文档或其他用户文件。

### 3. 首轮五项缺陷关闭状态

| 首轮缺陷 | 第二轮独立结论 | 直接证据 |
| --- | --- | --- |
| P1-C1-01-01 typed path cycle/version/generation/ACL | **CLOSED（核心条件）** | ordinary CALLS/REFERENCES 不能跨 version/generation；ACL 两端一致；incoming/outgoing multi-hop 正确；普通 cycle、lineage 回环与 CALLS lineage bypass 均拒绝；4-hop 接受 |
| P1-C1-01-02 status/context/channel/fallback | **CLOSED（首轮条件）** | 六态 channel outcome、typed errors、partial-zero-hit、complete empty、timeout/unavailable、orphan/cross-provenance/semantic duplicate context 与 fallback matrix 均独立通过；另有新 ranking consistency 缺陷见 P1-R2-02 |
| P1-C1-01-03 frozen/canonical raw maps | **PARTIALLY CLOSED / 仍 OPEN** | raw score/rank 已改 canonical frozen tuple，正常 `model_copy` 会重校验，输入重排 hash 稳定；但继承的 deprecated `copy(update=...)` 仍绕过所有 validator，且 signed zero canonical hash 不稳定 |
| P2-C1-01-04 whitespace/profile | **CLOSED** | 首尾空白、tab/newline、NUL、NBSP、zero-width、bidi/Unicode separator 均拒绝；Context 空白/NUL 拒绝且代码布局保留；embedding profile env/direct 使用相同 fullmatch |
| P2-C1-01-05 graph budget/hop | **CLOSED** | zero-hop 与 graph budget/filter 双向一致；positive-hop 必须有 filters/active budgets；4-hop 接受、5-hop 拒绝 |

### 4. 第二轮独立通过的核心反例

以下不是复述现有 tests，而是使用临时内存对象重新构造：

#### 4.1 Typed path

```text
PASS accept outgoing multi-hop
PASS accept incoming multi-hop
PASS reject ordinary static cycle
PASS accept lineage version transition
PASS reject lineage same version
PASS reject CALLS cross version
PASS reject REFERENCES cross generation
PASS reject ACL hidden target
PASS reject CALLS lineage bypass
PASS accept four hop boundary
PASS reject five hop boundary
PASS reject terminal candidate version mismatch
PASS reject terminal candidate ACL mismatch
PASS reject context terminal generation mismatch
```

`CodeRelationNode` 保存 entity/version/generation/locator/ACL；hop 保存两端
entity/version/generation/ACL；path 按 traversal direction 对照 stored endpoints；
candidate/context 再与 terminal node 对齐。首轮 ordinary path 的核心治理漏洞已关闭。

#### 4.2 Channel/status/context/fallback

```text
PASS accept complete empty no-match
PASS accept partial zero-hit
PASS accept timeout no evidence
PASS accept unavailable no evidence
PASS reject complete no-match orphan context
PASS reject unavailable with context
PASS reject orphan context
PASS reject cross-provenance context
PASS reject semantic duplicate context
PASS reject missing typed error mirror
PASS reject successful fallback with partial
PASS reject failed fallback with complete
PASS reject duplicate channel outcome
PASS reject all channels disabled
```

每个 result 必须精确覆盖 exact/sparse/dense/graph/history/test 六个 channel；outcome
为 `disabled/unavailable/timeout/error/complete_no_match/complete_with_hits` 之一；
typed errors 与 failed channel/fallback 精确镜像。首轮无法表达 partial-zero-hit 和
orphan context 的问题已关闭。

#### 4.3 Frozen/canonical/text/config

```text
PASS canonical raw input reorder/hash
PASS reject raw tuple structural mutation
PASS reject nested raw entry mutation
PASS reject validated model_copy mismatch
PASS reject raw set mismatch
PASS reject raw duplicate/unknown channel
PASS reject bool-as-int / string numeric / NaN / Inf
PASS reject whitespace/control/NUL/Unicode control text
PASS reject whitespace/NUL-only Context
PASS embedding profile env/direct fullmatch
PASS zero-hop/positive-hop graph budget matrix
```

Settings 仍是九项、默认全 V1/off/raw/local-hash/snippet/0，现有显式构造兼容；所有
`RAG_CODE_*` 读取仍集中在 `config.py`。

### 5. P1-R2-01 — Deprecated `copy(update=...)` 仍可绕过全部合同 validator

严重度：**P1**

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:62`
- `src/evidence_rag/rag/sources/code/contracts.py:81`
- 继承自当前 Pydantic `BaseModel.copy`

当前 `_ContractModel.model_copy()` 已改为 dump → update → `model_validate()`，正常路径会
重新验证；但同一个公开 model 仍继承 deprecated `copy(update=...)`。在当前安装的
Pydantic 中，该 API 虽发出 deprecation warning，仍可调用并绕过验证。

最小复现：

```python
candidate = valid_candidate()
invalid = candidate.copy(update={"raw_channel_scores": ()})

assert invalid.raw_channel_scores == ()
assert invalid.raw_channel_ranks != ()
```

以及：

```python
result = valid_complete_result()
invalid = result.copy(update={"channel_outcomes": ()})

assert invalid.status == "complete"
assert invalid.channel_outcomes == ()
```

独立结果：

```text
OBSERVE accept deprecated candidate.copy invalid
OBSERVE accept deprecated result.copy invalid
```

这直接破坏：

- frozen/validated contract 的生命周期 invariant；
- raw score/rank channel set equality；
- result 必须精确覆盖六个 channel；
- status/error/context/fallback 矩阵；
- canonical artifact 和 shadow/evaluation trace 的可信度。

代码注释声称 “Pydantic's unsafe update bypass is not exposed”，但当前公共对象上实际仍
可见，因此首轮 P1-03 不能关闭。

验收条件：

1. 覆盖/禁用 deprecated `copy()`，或让它与 `model_copy()` 一样执行完整重验证；
2. 所有公开 copy/update 入口都不能产生违反 invariant 的对象；
3. 明确记录 `model_construct` 等显式 trusted-only escape hatch，不得让普通 copy API
   与其等价；
4. 增加 candidate、path、context、channel outcome 与 result 的 deprecated-copy
   adversarial tests；
5. 复验 copied nested objects 仍深冻结，canonical hash 只来自重新验证后的状态。

### 6. P1-R2-02 — Channel `hit_count`、raw rank 与 within-source rank 可互相矛盾

严重度：**P1**

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:502`
- `src/evidence_rag/rag/sources/code/contracts.py:539`
- `src/evidence_rag/rag/sources/code/contracts.py:541`
- `src/evidence_rag/rag/sources/code/contracts.py:687`
- `src/evidence_rag/rag/sources/code/contracts.py:881`

当前 result 只统计每个 channel 有多少 returned candidate，并检查：

```text
hit_count >= returned candidate count
```

它没有检查 candidate 的 raw rank 是否落在该 channel 的 `hit_count` 内。现有 tests 已
固定 raw rank 为零基（首位 rank=0），但以下矛盾仍被接受：

```python
candidate.raw_channel_ranks = (
    CodeChannelRank(channel="exact", rank=7),
)
outcome = CodeChannelCompleteWithHits(channel="exact", hit_count=1)
CodeSourceResult(candidates=[candidate], channel_outcomes=[outcome, ...])
```

独立结果：

```text
OBSERVE accept raw rank beyond channel hit_count
```

同一 result 还接受：

```text
candidates tuple: [within_source_rank=1, within_source_rank=0]
duplicate within_source_rank: [0, 0]
```

因此 tuple reading order、`within_source_rank` 与 canonical serialization 可以表达三个
不同排序。该缺陷会直接污染 C1-02 shadow 的 rank delta/overlap、后续 source fusion 和
Evaluation rank evidence。

验收条件：

1. 明确定义 raw rank 为零基还是一基；按现有测试使用零基时，必须满足
   `0 <= raw_rank < channel.hit_count`；
2. 明确 `hit_count` 是 channel 总命中数还是返回数；若不是总命中数，应换名或增加足以
   验证 raw rank 的总数；
3. `within_source_rank` 必须有明确 tie policy、唯一性/连续性和 tuple order 规则；
4. result 必须 canonicalize candidates，或拒绝 tuple order 与 rank 冲突的输入；
5. 增加 rank=hit_count、rank>hit_count、reversed tuple、duplicate rank、tie-break 与
   canonical hash tests。

### 7. P2-R2-03 — Exact float strictness 与 signed-zero canonical identity 未关闭

严重度：**P2**

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:46`
- `src/evidence_rag/rag/sources/code/contracts.py:48`
- `src/evidence_rag/rag/sources/code/contracts.py:70`

`StrictInt`/`StrictBool` 已拒绝 bool、float 和字符串错型，NaN/Inf 也被拒绝；但当前
Pydantic `StrictFloat` 仍接受整数并转换：

```text
CodeChannelScore(channel="exact", score=1)
→ score=1.0
```

这不满足本轮要求的 exact bool/int/float 类型边界。

另一个 canonical 问题：

```python
positive = CodeChannelScore(channel="exact", score=0.0)
negative = CodeChannelScore(channel="exact", score=-0.0)

assert positive == negative
assert positive.canonical_json_bytes() != negative.canonical_json_bytes()
assert positive.canonical_sha256() != negative.canonical_sha256()
```

即语义相等 model 仍产生不同 canonical bytes/hash。该问题影响所有
`NonNegativeFloat/UnitFloat` 字段，包括 raw score、fusion、calibration、confidence、
path score 和 latency。

验收条件：

1. 若合同要求 exact float，增加 `type(value) is float` 的 before-validator，不能依赖
   当前 `StrictFloat` 的 int widening；
2. 在所有 canonical float 字段统一拒绝或规范化 `-0.0` 为 `0.0`；
3. 保持 NaN/Inf/bool/string rejection；
4. 增加 int→float、signed zero、canonical bytes/hash 的跨模型 tests。

### 8. P2-R2-04 — Repository 与 hop locator provenance 仍是不可验证的自由文本

严重度：**P2**

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:359`
- `src/evidence_rag/rag/sources/code/contracts.py:377`
- `src/evidence_rag/rag/sources/code/contracts.py:395`
- `src/evidence_rag/rag/sources/code/contracts.py:452`
- `src/evidence_rag/rag/sources/code/contracts.py:535`
- `src/evidence_rag/rag/sources/code/contracts.py:583`

核心 version/generation/ACL 已结构化并校验，但：

- `CodeRelationNode` 没有独立 `repository_id`；
- terminal candidate 对齐 tuple 不含 `candidate.repository_id`；
- `CodeRelationHop.locator` 没有 owner entity/version/generation/ACL；
- path 对 hop/node provenance 的比较 tuple 不含 locator。

以下明显冲突仍被接受：

```text
candidate.repository_id = "repository://different"
terminal.entity_id       = "code://repo@abc/..."

hop.locator = "code://other@def/hidden#L999"
hop source  = "code://repo@abc/src/a.py#symbol=a"
```

edge locator 合理地可能是 source symbol 内的 call-site，不能简单要求等于 node locator；
但当前合同也没有结构化 owner/version/generation 来证明它属于 stored source。

验收条件：

1. 为 relation node/terminal provenance 增加结构化 repository identity，或定义并验证
   opaque entity ID 与 repository 的绑定；
2. candidate `repository_id` 必须与 terminal node 的 repository provenance 一致；
3. hop locator 必须携带可验证的 owner entity/repository/version/generation/ACL；
4. locator 可以是 node 范围内的子定位，但不能跨 repository/version/generation；
5. 增加 wrong-repository、wrong-version edge locator 与 incoming stored-source locator
   tests。

### 9. 第二轮 adversarial 覆盖总表

| 类别 | 第二轮独立结果 | Gate |
| --- | --- | --- |
| ordinary/static path version/generation/ACL | 正确拒绝跨界 | PASS |
| incoming/outgoing multi-hop | traversal/stored endpoints 正确 | PASS |
| ordinary/lineage cycle 与 CALLS/REFERENCES bypass | 正确拒绝；显式 lineage transition 接受 | PASS |
| candidate/context terminal provenance | entity/version/generation/locator/ACL 对齐；cross-provenance 拒绝 | PASS |
| six-state channel outcomes | 六 channel 精确覆盖、状态与 typed errors canonical | PASS |
| overall status/context/fallback | partial-zero-hit、complete empty、timeout/unavailable、orphan/duplicate/fallback 均正确 | PASS |
| raw tuple freeze/reorder | 深冻结、集合匹配、unknown/duplicate、输入重排 hash 正确 | PASS |
| unsafe copy/update | `model_copy` 正确；deprecated `copy(update)` 可绕过 | **FAIL / P1** |
| raw rank/hit/source rank | `rank=7/hit_count=1`、reversed/duplicate source rank 可通过 | **FAIL / P1** |
| bool/int/float、NaN/Inf | bool/int fields/NaN/Inf 正确；int→StrictFloat 仍转换 | **FAIL / P2** |
| canonical hash | map/outcome reorder 稳定；signed zero 不稳定 | **FAIL / P2** |
| whitespace/control/NUL/Unicode | control/Unicode whitespace/bidi/zero-width 拒绝；正常中文代码保留 | PASS |
| embedding profile env/direct | 同一 ASCII fullmatch，长度/控制字符 fail-fast | PASS |
| graph budget/hop | 双向一致，4-hop 边界正确 | PASS |
| unknown field/channel | 正确拒绝 | PASS |
| import/runtime/C1-02 boundary | 纯 stdlib+pydantic；无 Runtime/Platform/store/schema/retrieval 偷跑 | PASS |

附加观察：NFC-equivalent `é` / `e + combining acute` 当前会得到不同 model/hash。
Code identifier 与 filesystem path 是否统一 NFC/NFKC 尚无字段级规范；本轮不单独升级
severity，但后续若以 canonical hash 作跨平台 artifact identity，应先明确 Unicode
normalization policy。

### 10. 第二轮独立命令与结果

执行：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider tests/test_code_rag_contracts.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider

.venv/bin/ruff check --no-cache src tests

.venv/bin/ruff format --check \
  src/evidence_rag/config.py \
  src/evidence_rag/rag \
  tests/test_code_rag_contracts.py

git diff --check

# read + compile(source, ..., "exec")；不写 pyc
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python <read-only compile probe>

git worktree list --porcelain
```

结果：

| 检查 | 第二轮独立结果 |
| --- | --- |
| C1 专项 | **71 passed in 0.32s** |
| 全量 pytest | **200 passed**, 1 个第三方 deprecation warning，32.95s |
| 全量 ruff | **All checks passed** |
| C1 相关 format | **6 files already formatted** |
| `git diff --check` | PASS |
| 只读 compile | **143 Python sources** compiled，无 bytecode写入 |
| worktree/branch | 只有 shared `main`，HEAD `bc3326...` |

### 11. 正式库、import 与未越级检查

第二轮 tests/probes 前后正式库保持：

```text
path:   var/evidence-rag.sqlite3
size:   1,915,490,304 bytes
mtime:  1785093200
sha256: 89fb45182d1dd6eed9d45873333b6e8640952609d676750ebf10276d4b1983cc
```

只读检查：

```text
PRAGMA quick_check = ok
code_retrieval_*/code_unit_*/code_index_* schema objects = []
```

contracts imports 精确为：

```text
__future__
collections.abc
enum
hashlib
json
pydantic
typing
unicodedata
```

无 Platform/Document/Codex/runtime/storage/store/schema/retrieval 依赖；所有
`RAG_CODE_*` 读取仍集中在 `config.py`；`src/evidence_rag/rag/**` 仍只有 package init
和 contracts，没有 `CodeSourceRetrieverV1Adapter`、`CodeShadowRunner` 或 C2 schema/
store/index。

### 12. 第二轮复审授权

第二轮确认首轮 path、status/context、whitespace/profile 和 graph-budget 核心修复有效，
但 P1-R2-01、P1-R2-02 仍 OPEN。

因此：

```text
C1-01: FAIL
C1-02: NOT_STARTED
C1 overall: BLOCKED
C1-02: NOT_AUTHORIZED
```

第三轮复审至少需要：

1. 关闭 deprecated copy/update validator bypass；
2. 关闭 channel hit_count/raw rank/within-source rank consistency；
3. 关闭或明确处置 signed-zero/exact-float 与 locator/repository P2；
4. 把第二轮最小复现固化为 tests；
5. 重跑专项、全量、ruff/format/diff/compile、only-main 与正式库检查。

不得开始 C1-02；本报告不更新 interview 文档。

---

## 附录：首轮 Gate 快照（历史记录，已由以上第二轮结论取代）

### 1. Gate 结论

| Gate | 结论 | 直接原因 | 后续授权 |
| --- | --- | --- | --- |
| C1-01 Code Source Contract | **FAIL** | 3 个 P1、2 个 P2；现有绿灯测试没有覆盖并阻止这些非法状态 | 修复并复审 |
| C1-02 V1 Adapter / Shadow | **NOT_STARTED** | 本轮未发现 adapter、shadow、Runtime/Platform 接入或其他 C1-02 实现 | **NOT_AUTHORIZED** |
| C1 overall | **BLOCKED** | C1-01 尚未通过，不能越过 blocking dependency | 不得开始 C1-02 |

最终状态：

```text
C1-01: FAIL
C1-02: NOT_STARTED
C1 overall: BLOCKED
C1-02: NOT_AUTHORIZED
```

工程检查本身全部通过：

```text
专项 pytest: 30 passed
全量 pytest: 159 passed
ruff: PASS
相关 format: PASS
diff check: PASS
只读 compile: PASS
only main: PASS
```

但测试全绿不能覆盖本轮独立 adversarial probes 已复现的合同漏洞，因此不能据此把
C1-01 判为 PASS。

## 2. 审查边界与 dirty main

审查开始前：

- 仓库已经处于 dirty `main`，`git status --porcelain=v1` 有 57 个顶层状态项；
- 其中包括 36 个 tracked modified 文件，以及既有 untracked
  `docs/rag-optimization/`、`evals/`、`src/evidence_rag/rag/`、
  `tests/test_code_rag_contracts.py` 等成果；
- `src/evidence_rag/config.py` 已修改，C1 contracts/tests 为既有 untracked 内容；
- `git worktree list --porcelain` 只有
  `/Users/example/project/rag` 的 `main`，HEAD 为 `bc3326...`。

本 Gate 的持久工作区写入严格限定为本报告：

```text
docs/rag-optimization/development/reviews/02_CODE_C1_GATE_REVIEW.md
```

本轮没有：

- 创建或切换 branch/worktree；
- 创建子智能体或其他会话；
- commit/push；
- 修改实现、测试、配置、C0 artifacts/ledger；
- 修改 development plan、source design 或 interview 文档；
- 写入正式 evidence 库。

共享 main 上其他既有 dirty 内容属于用户或并发流程，本审未清理、覆盖或归因。

## 3. 权威材料核对

本轮完整阅读并按以下优先级核对：

1. `development/00_SINGLE_SOURCE_DEVELOPMENT_CONTRACT.md`；
2. `development/01_CODE_SOURCE_DEVELOPMENT_PLAN.md` 全文，重点为全局约束、
   C1-01、C1-02、C4 typed graph、C7 release；
3. `sources/01_CODE_SOURCE_RAG.md` 全文，重点为 Query Profile、Candidate
   Generation、Graph Traversal、Context、ACL、安全和发布门槛；
4. `development/reviews/01_CODE_C0_GATE_REVIEW.md` 全文。

C0 的最终结论仍是 PASS，并只授权开始 C1。C1-01 是 C1-02 的 blocking dependency；
当前审查失败不改变 C0，也不允许跳过 C1-01 直接实现 adapter/shadow。

## 4. 七个要求合同逐项结论

| 合同 | 已满足 | 未满足 / 风险 | 结论 |
| --- | --- | --- | --- |
| `CodeQueryProfile` | task/ref、typed direction/edge、hop、test/history 与预算已分字段；基础 strict/extra 校验存在 | 语义重复可用首尾空白绕过；`max_hops=0` 仍可声明活动 graph budget | **FAIL** |
| `CodeRetrievalBudget` | 各通道、node/edge/context 分离；负数、字符串和部分 graph 预算矛盾会拒绝 | hop 与 graph budget 只有单向约束，零 hop 的反向一致性缺失 | **FAIL** |
| `CodeRetrievalCandidate` | stable entity、rebuildable unit、raw score/rank、source fusion、calibration、version/generation、fact/derivation/review、role/path/locator/token/ACL 均独立 | raw maps 可在冻结后原地修改；等价 map 的 JSON 字节不稳定；path 的版本/ACL/generation 治理不足 | **FAIL** |
| `CodeRelationPath` | incoming/outgoing 的 stored-edge 方向、hop 连续性、node/edge 数量与端点顺序已校验 | 非 lineage 循环、非法跨版本路径、ACL hidden middle node、generation provenance 均未封死 | **FAIL** |
| `CodeContextBlock` | stable entity/unit、role、content、version/generation、path、locator/token/ACL 已分字段 | 空白 content 可通过；放入 result 后不要求关联 candidate，且可与 candidate provenance 冲突 | **FAIL** |
| `CodeSourceStatus` | `complete/partial/timeout/unavailable` 枚举明确；complete empty 可表达 no-match | `partial` 强制至少一个 candidate，无法表达“部分通道完成且 no-match、另一通道失败” | **FAIL** |
| `CodeSourceResult` | 基本 status/error/fallback 矩阵、candidate pair 与 block ID 重复检查存在 | orphan/冲突/重复 context 可通过；unavailable/no-match 可带 context；缺少 channel-level unavailable/disabled/no-match 语义 | **FAIL** |

共有能力中，`extra="forbid"`、字段级 strict primitive、`allow_inf_nan=False`、
model attribute frozen 和 JSON round-trip 已实现；但“frozen”和“序列化稳定”没有达到
深层、可作为跨阶段合同的强度，详见 P1-03。

Calibration 对 `calibrated/disabled/unavailable` 使用 discriminated union，成功避免以
`0` 或 `None` 伪装不可用；相反，channel-level unsupported/disabled/error/no-match
没有一等状态，只能依靠 candidate map 缺项和自由文本 `errors` 推测，仍有语义缺口。

## 5. P1-C1-01-01 — Typed path 未封死循环、版本/generation 与 ACL

严重度：**P1**

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:239`
- `src/evidence_rag/rag/sources/code/contracts.py:262`
- `src/evidence_rag/rag/sources/code/contracts.py:269`
- `src/evidence_rag/rag/sources/code/contracts.py:334`

### 5.1 直接证据

当前 validator 只检查：

- 单 hop 两端不能相同；
- edge 数等于相邻 node 对数；
- hop 从 1 连续；
- stored source/target 与 traversal direction 对齐；
- candidate 等于 path terminal node。

以下非法状态均被接受：

```python
# 非 lineage CALLS 循环
CodeRelationPath(
    nodes=[a, b, a],
    edges=[
        hop(a, b, edge_type="CALLS", hop=1, stable_version="abc"),
        hop(b, a, edge_type="CALLS", hop=2, stable_version="abc"),
    ],
)

# 同一 CALLS path 混合版本和 ACL，仍可挂到 stable_version="abc" 的 candidate
CodeRelationPath(
    nodes=[a, b, target],
    edges=[
        hop(a, b, hop=1, stable_version="abc", acl_ref="project:public"),
        hop(b, target, hop=2, stable_version="def", acl_ref="project:private"),
    ],
)
```

独立 probe 结果：

```text
ACCEPT non-lineage cycle
ACCEPT mixed-version-and-acl path
ACCEPT candidate/path version-acl mismatch
```

`CodeRelationHop` 没有 `source_generation`，path 也没有 generation/effective ACL 或
node-level ACL。单个自由文本 `acl_ref` 未声明为“两端 ACL 交集”，因此合同无法证明
hidden middle node 已在 expansion 前授权。

### 5.2 影响

- 违反 source design 的 “cycle forbidden unless lineage”；
- 普通 `CALLS/REFERENCES` 可跨版本拼成伪路径；
- candidate 的 `stable_version/source_generation/acl_ref` 可与 path 不一致；
- C4 graph trace、Required Path Recall、wrong-version 与 ACL leakage 无法依赖该合同；
- 一旦 C1-02 adapter 或后续 graph runtime 消费该结构，错误 provenance 会成为稳定 API。

### 5.3 验收条件

1. 明确 path/hop 的 version、generation 与 effective ACL 语义，并携带足够字段验证；
2. 非 lineage path 禁止重复 node；若允许 lineage 例外，必须用明确 edge whitelist 和
   version-transition 规则，不能给普通 CALLS/REFERENCES 放行；
3. 普通静态路径的每一 hop 必须与 candidate 的 resolved version/generation 对齐；
4. 跨版本只允许显式 lineage edge，并保存 from/to version；
5. 合同必须能证明所有 node/edge ACL 已授权，或明确 `effective_acl_ref` 是两端交集并与
   candidate/context 一致；
6. 增加 incoming/outgoing 多 hop、普通 cycle、lineage 例外、mixed version、
   generation mismatch、ACL hidden middle node 的负向 tests。

## 6. P1-C1-01-02 — SourceResult 可产生自相矛盾的 status/context provenance

严重度：**P1**

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:345`
- `src/evidence_rag/rag/sources/code/contracts.py:391`
- `src/evidence_rag/rag/sources/code/contracts.py:411`
- `src/evidence_rag/rag/sources/code/contracts.py:432`

### 6.1 直接证据

当前 result 只检查 candidate `(entity_id, retrieval_unit_id)` pair 不重复，以及
`block_id` 不重复；它不要求 context 对应 result 中的 candidate，也不比较 version、
generation、role、path、locator 或 ACL provenance。

以下状态均被独立接受：

```text
ACCEPT candidate-context provenance contradiction
  candidate: version=abc, generation=code-v1, role=target, ACL=project-rag
  context:   version=def, generation=other, role=history, ACL=private

ACCEPT complete-no-match with orphan context
ACCEPT unavailable with context
ACCEPT duplicate context entity-unit with a different block_id
```

另外，当前合同主动拒绝：

```python
CodeSourceResult(
    status="partial",
    candidates=[],
    errors=["dense channel failed"],
    ...
)
```

这使“exact/sparse 已完整执行且 no-match，但 dense 非 timeout 失败”的真实状态无法表达：

- `complete` 不能带 error；
- `partial` 强制至少一个 candidate；
- `timeout` 与错误类型不符；
- `unavailable` 会错误宣称整个 source 不可用。

Candidate 的 raw maps 只说明该 candidate 命中了哪些 channel；不存在 source-level
channel outcome，因此缺项无法区分 disabled、unsupported、budget=0、complete
no-match、timeout 或 error。

Fallback 矩阵也只编码了部分组合。`complete + failed`、
`timeout/unavailable + succeeded` 会拒绝，但 `partial + succeeded` 的整体语义未定义，
也没有 channel/source outcome 支撑判断。

### 6.2 影响

- `complete` 的 empty no-match 可同时携带 evidence context，语义自相矛盾；
- `unavailable` 可返回没有 candidate provenance 的代码上下文；
- context 可跨版本、generation 或 ACL 冒充 candidate 的渲染结果；
- 下游无法可靠区分 no-match、partial failure、unsupported 与 fallback；
- duplicate context 可能浪费 token，并破坏 Context duplicate ratio。

### 6.3 验收条件

1. 每个 context block 必须显式关联 result 中存在的 candidate，且稳定身份、
   version/generation、path/ACL 等不可冲突；若 locator/role 允许扩展，必须建模并测试
   允许的差异，而不是任意自由变化；
2. `complete` empty no-match 与 `unavailable` 不得携带 orphan context；
3. 定义 context 的语义重复键或内容/provenance hash，不能只换 `block_id` 绕过重复检查；
4. 增加 source/channel outcome 合同，显式区分
   `disabled/unavailable/timeout/error/complete_no_match/complete_with_hits`；
5. 能表达“partial 但当前零 candidate”，或给出不歪曲 source 状态的等价建模；
6. 完整定义并测试 status × candidate × context × error × fallback 矩阵；
7. 增加 orphan、cross-version、cross-generation、ACL mismatch、duplicate context、
   partial-zero-hit 和 illegal fallback 的负向 tests。

## 7. P1-C1-01-03 — Frozen 是浅冻结，raw maps 可变且序列化不稳定

严重度：**P1**

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:26`
- `src/evidence_rag/rag/sources/code/contracts.py:319`
- `src/evidence_rag/rag/sources/code/contracts.py:334`

### 7.1 直接证据

`ConfigDict(frozen=True)` 阻止 model attribute 重新赋值，但
`raw_channel_scores` / `raw_channel_ranks` 是普通可变 `dict`：

```python
candidate = valid_candidate()
candidate.raw_channel_scores.clear()  # 成功

assert candidate.raw_channel_scores == {}
assert candidate.raw_channel_ranks == {"exact": 0, "sparse": 2}
```

这会在构造完成后破坏 “scores/ranks exactly same channels” invariant，且不会重新触发
validator。

等价 map 的插入顺序还会改变序列化字节：

```text
models_equal=True
bytes_equal=False
sha256 prefix: c32f72231714 vs e838b60cab80
```

现有 round-trip test 只证明同一实例可 dump/load，不证明：

- 构造后不可变；
- 语义等价输入产生 canonical serialization；
- cache/hash/trace identity 稳定。

### 7.2 影响

- validated contract 可被原地改成非法状态；
- raw scores/ranks、fusion trace 与 Evaluation evidence 不再可信；
- JSON hash、cache key、shadow comparison 与审计 artifact 取决于调用方插入顺序；
- “frozen/序列化稳定”验收不成立。

### 7.3 验收条件

1. raw channel observations 使用深不可变结构，或在访问/序列化前不可变封装并重新校验；
2. scores/ranks 的 channel 集合 invariant 在对象整个生命周期都成立；
3. channel serialization 使用固定 enum 顺序或明确 canonical JSON；
4. 两个语义等价、不同输入顺序的 candidate 必须产生相同 canonical bytes/hash；
5. 增加 nested mutation、map reorder、copy/update、unknown channel 和 raw-map mismatch
   tests。

## 8. P2-C1-01-04 — 空白规范化与 profile identifier 校验不足

严重度：**P2**

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:23`
- `src/evidence_rag/rag/sources/code/contracts.py:216`
- `src/evidence_rag/rag/sources/code/contracts.py:352`
- `src/evidence_rag/config.py:43`
- `src/evidence_rag/config.py:113`

`ContractText` 的 `pattern=r"\S"` 只要求任意位置存在一个非空白字符，不拒绝首尾空白。
因此：

```text
ACCEPT target_identifiers=["rank", " rank "]
ACCEPT context content=" \n\t"
ACCEPT RAG_CODE_EMBEDDING_PROFILE="qwen\nlog-forgery"
```

第一项绕过 duplicate check，第二项生成无内容 context，第三项允许控制字符进入 profile
identifier/日志/trace。

验收条件：

1. identity、version、locator、ACL、error/profile 等控制字段拒绝首尾空白与控制字符；
2. duplicate check 在 canonical value 上执行，或直接拒绝非 canonical 输入；
3. Context content 至少包含非空白内容；
4. embedding profile 使用文档化 allowlist/pattern，并让 env 与 direct constructor
   使用同一 validator；
5. 增加首尾空白、tab/newline/NUL、Unicode 空白和 canonical duplicate tests。

## 9. P2-C1-01-05 — 零 hop 仍可声明活动 Graph 预算

严重度：**P2**

位置：

- `src/evidence_rag/rag/sources/code/contracts.py:182`
- `src/evidence_rag/rag/sources/code/contracts.py:216`

以下 profile 被接受：

```python
CodeQueryProfile(
    task="implementation",
    max_hops=0,
    directions=[],
    edge_types=[],
    budget=CodeRetrievalBudget(
        graph_candidates=10,
        graph_node_budget=10,
        graph_edge_budget=20,
    ),
)
```

当前只验证 “positive hop → graph candidate budget”，没有反向验证。该状态同时表达
“不遍历 graph”和“为 graph candidate/node/edge 分配活动预算”，会让 trace、budget
accounting 与实现选择产生歧义。

验收条件：

1. 明确零 hop 时 graph candidate/node/edge budget 必须为 0，或增加显式
   `graph_enabled`/budget state 解释为何保留非零 ceiling；
2. profile、budget、directions、edge types 和 max_hops 的关系必须双向一致；
3. 增加 zero-hop/non-zero-budget、positive-hop/zero-budget 与边界 4-hop tests。

## 10. 已通过的合同与配置检查

以下项目有直接支持，修复 P1/P2 时应保持：

- 七个要求类型均存在并公开导出；
- stable `entity_id` 与 rebuildable `retrieval_unit_id` 是不同字段，且相等会拒绝；
- raw channel scores/ranks、source fused score、calibration、version alignment、
  stable version、source generation、fact、derivation、review、role、typed path、
  locator、token 与 ACL 均未合并成单一 metadata blob；
- calibrated score 限制在 `[0,1]`，disabled/unavailable 没有 `score=None/0` sentinel；
- incoming/outgoing 的 stored-edge endpoint 解释正确；
- hop 是一基、连续且最多 4，path node/edge 数量一致；
- unknown enum、extra field、负数、字符串数值、bool-as-int、NaN/Inf 会 fail fast；
- model attribute reassignment 会被 frozen 拒绝；
- Settings 精确新增九项，默认是 V1/off/raw/snippet/local-hash/0；
- 现有显式 `Settings(...)` 构造保持兼容；
- enum、bool、percentage 的 env parsing 为 exact/fail-fast；
- 九个 Code settings 没有新增 secret 字段；
- 所有 `RAG_CODE_*` 读取只位于 `src/evidence_rag/config.py`，无散落 `os.getenv`；
- contracts 只 import `__future__`、`enum`、`typing`、`pydantic`；
- 没有 import Platform/Document/Codex/runtime/storage/store；
- `src/evidence_rag/rag/**` 只有 package init 与 contracts，没有 C1-02 adapter/shadow，
  也没有 C2 schema/store/unit/retrieval 偷跑。

## 11. Adversarial negative test 覆盖审计

| 类别 | 现有测试 / 独立结果 | 结论 |
| --- | --- | --- |
| bool-as-int | profile bool、direct Settings 有测试；candidate token/raw float 独立拒绝 | 基础 strict PASS |
| numeric coercion | string int/float 独立拒绝 | PASS |
| NaN/Inf | 独立拒绝 | PASS，但应固化为回归 test |
| 空白/重复 | 纯空白和完全重复有测试；首尾空白/语义重复可绕过 | **FAIL** |
| unknown field/enum | extra/enum 有测试且拒绝 | PASS |
| path mismatch | endpoint/direction/hop 有测试；cycle/version/generation/ACL 未覆盖且可通过 | **FAIL** |
| raw map mismatch | 构造时 key mismatch 会拒绝；构造后可原地破坏 | **FAIL** |
| duplicate candidate/context | candidate exact pair 有测试；semantic duplicate context 可通过 | **FAIL** |
| status/fallback | 部分矩阵有测试；partial-zero-hit、orphan context、channel state 未覆盖 | **FAIL** |
| invalid env | enum/bool/basic profile/percent 有测试；profile 控制字符可通过 | **FAIL** |

现有专项 30 tests 对已编码规则有效，但不足以作为 adversarial Gate。

## 12. 独立命令与结果

执行：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider tests/test_code_rag_contracts.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  -p no:cacheprovider

.venv/bin/ruff check --no-cache src tests

.venv/bin/ruff format --check \
  src/evidence_rag/config.py \
  src/evidence_rag/rag \
  tests/test_code_rag_contracts.py

git diff --check

# read + compile(source, ..., "exec")，不写 pyc
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python <read-only compile probe>

git worktree list --porcelain
```

结果：

| 检查 | 独立结果 |
| --- | --- |
| C1 专项 | **30 passed in 0.30s** |
| 全量 pytest | **159 passed**, 1 个第三方 deprecation warning，32.16s |
| 全量 ruff | **All checks passed** |
| C1 相关 format | **6 files already formatted** |
| `git diff --check` | PASS |
| 只读 compile | **143 Python sources** compiled，无 bytecode 写入 |
| worktree/branch | 只有 shared `main`，HEAD `bc3326...` |

这与主控参考的 30/159/ruff/format/diff/compile/only-main 计数一致。

## 13. 正式库与未越级实施检查

正式库在专项、全量测试和 probes 前后保持：

```text
path:   var/evidence-rag.sqlite3
size:   1,915,490,304 bytes
mtime:  1785093200
sha256: 89fb45182d1dd6eed9d45873333b6e8640952609d676750ebf10276d4b1983cc
```

只读检查：

```text
PRAGMA quick_check = ok
code_retrieval_*/code_unit_*/code_index_* schema objects = []
```

因此本轮没有正式库 C1 写入，也没有偷跑 C2 private schema/store/index。源码扫描同样未
发现 C1-02 `CodeSourceRetrieverV1Adapter`、`CodeShadowRunner` 或 Runtime/Platform
wiring。

## 14. 复审入口

C1-01 复审至少需要：

1. 关闭 P1-C1-01-01～03；
2. 关闭或明确接受并记录 P2-C1-01-04～05；
3. 把本轮最小复现固化为 adversarial tests；
4. 重跑专项、全量 pytest、全量 ruff、相关 format、diff/compile；
5. 再次确认 only main、正式库 hash 不变、无 C1-02 偷跑。

在此之前：

```text
C1-01: FAIL
C1-02: NOT_STARTED
C1 overall: BLOCKED
C1-02: NOT_AUTHORIZED
```

不得开始 C1-02。本报告不更新 interview 文档；由主控在后续 Gate 真正通过后按统一
开发契约决定同步时机。
# C1-02 第三轮独立 Gate（2026-07-27，当前结论）

## Gate 状态

- `C1-01: PASS`
- `C1-02: FAIL`
- `C1 overall: BLOCKED`
- `C2: NOT_AUTHORIZED`

第三轮确认 2 个新的 P1。二者均有独立 stdin adversarial probe 复现，且直接违反第三轮 Gate 对 authority 不可复制和 shadow request path 硬上界/早停的要求。因此，即使现有专项测试通过，也不能授权 C2。

## 审查边界与共享工作区状态

- 审查对象是 `/Users/example/project/rag` 的当前 dirty `main`；未创建 branch/worktree，未 commit/push。
- 本 Gate 未修改实现、测试、配置、依赖、C0 artifacts、development/interview 文档或正式库；唯一持久化写入是本报告。
- 审查前基线为 `main`、HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`，仅发现一个共享工作区既有 golden fixture bytecode：`tests/fixtures/code_golden/v2/__pycache__/materializer.cpython-313.pyc`；本 Gate 未删除或改动它。
- 已完整重读本轮相关依赖、authority、adapter/contracts、shadow、runtime/platform 局部接线及三份 C1 测试。C1-01 合同结论保持 PASS。
- 连续续跑期间发生 systemError。根据主控最后指令，本轮不再启动命令或探针；因此全量 pytest 的最终结果，以及后续 ruff、format、diff、内存 compile、正式库最终 hash/quick_check/schema、最终 only-main 复验记为 `UNAVAILABLE (systemError)`，不把它们误记为实现失败或通过。

## P1-C1-02-R3-01：`copyreg` 可绕过 authority 禁复制边界

### 最小复现

标准 `copy.copy`、`copy.deepcopy`、`pickle.dumps`、`reduce/reduce_ex/getstate` 均会被当前实现拒绝；但 pickle dispatch table 的 reducer 优先于对象自身的 `__reduce_ex__`。独立探针使用默认 constructor + pickle `BUILD` state 即可复制 authority：

```python
def reduce_retriever(value):
    return (
        HybridRetriever,
        (value.store, value.embedder),
        dict(value.__dict__),
    )

copyreg.pickle(HybridRetriever, reduce_retriever)
cloned = pickle.loads(pickle.dumps(original))

# 原实例签发的 response 在复制实例上被接受。
cloned.verify_execution_evidence(request, attested_response)
```

实测结果为 `ACCEPTED_CROSS_INSTANCE`。复制后的 `HybridRetriever` 获得相同 execution-evidence HMAC authority。`CodeShadowRunner` 的等价 constructor + state probe 也被接受，并复制了 `_telemetry_key`、`_snapshot_key`、`_runner_id` 和 `_nonce_prefix`。这不是仅调用私有 `__reduce__` 的理论问题，而是 Python pickle 的公开 dispatch/state 恢复路径。

### 影响

- “runner-local/retriever-local authority” 可被复制，原/新实例之间可重放 attested response。
- Shadow runner 的 telemetry/snapshot authority 和 nonce namespace 可被复制；这破坏跨 runner 不可链接、AES-GCM 每 key nonce 唯一和 one-shot registry 的安全前提。
- 当前测试只覆盖标准 copy/deepcopy/pickle 入口，没有覆盖 `copyreg`/自定义 Pickler dispatch + 默认 state restore。

### 验收条件

1. `HybridRetriever` 和 `CodeShadowRunner` 必须拒绝 pickle `BUILD`/state restore（例如 fail-closed `__setstate__`，或提供同等强度且可证明的结构性边界），不能让 constructor + state 注入 authority。
2. 新增独立负测覆盖全局 `copyreg.pickle` 与局部 `Pickler.dispatch_table`，并覆盖 constructor kwargs/state、`copy.copy`、`copy.deepcopy`、`pickle.dumps`、`reduce/reduce_ex/getstate`。
3. 所有复制尝试后都不得产生可用于构造 adapter、验证原实例 evidence、解封原 runner envelope 或复用 telemetry/snapshot key、runner id、nonce prefix/counter 的新实例。
4. 原实例在失败复制尝试后仍正常工作；attested response 的普通 deepcopy 如继续支持，只能由签发它的原 retriever 验证，payload/evidence 任意变更仍须 fail closed。

## P1-C1-02-R3-02：恶意/状态化容器可突破 preflight 早停与硬上界

### 最小复现 A：访问节点数突破 cap

```python
class LyingSequence(Sequence):
    def __len__(self):
        return 1

    def __iter__(self):
        for index in range(10_000):
            accesses.append(index)
            yield index

_preflight_json(LyingSequence())
```

`_preflight_json()` 在检查 `len == 1` 后执行 `stack.extend(...)`，先完整消费迭代器，再按 stack 节点计数。实测访问 10,001 个元素后才停止，超过 `SHADOW_STRUCTURE_MAX_NODES == 4096`；伪报 `len == 1` 的 Mapping `.items()` 也被访问 9,927 项。由此，“访问节点不超过 cap 并早停”并不成立。

### 最小复现 B：preflight/seal 两次迭代之间膨胀

```python
class FlippingSequence(Sequence):
    def __len__(self):
        return 1

    def __iter__(self):
        self.iterations += 1
        yield from ([small_item] if self.iterations == 1 else [small_item] * 5_000)

legacy_response["results"] = FlippingSequence()
runner.submit(request, profile, legacy_response)
```

第一次迭代供 preflight 使用，仅返回一个元素；第二次迭代发生在 `_plain_json()`/seal，返回 5,000 个元素。独立 probe 实测 `submit == True`，生成的 canonical envelope 为 26,510 bytes，仍低于 64 KiB，因此绕过 `SHADOW_RESULTS_MAX_ITEMS == 50` 并被接纳。preflight 与 seal 对同一不受信容器的二次遍历形成 TOCTOU。

### 影响

- 恶意 Sequence/Mapping 可在 request path 触发超出声明 cap 的迭代、内存和 CPU 工作，`submit` 不再具备可证明的 bounded/non-blocking 性质。
- `len()` 不是真实消费数量的证明；状态化迭代器还能让检查对象和密封对象不同。
- 64 KiB 最终 envelope 上限不能替代 results/items/nodes/depth 的前置硬上界，也不能证明 identity/HMAC/AEAD 之前已早停。

### 验收条件

1. 不得对同一不受信 Mapping/Sequence 分别执行 preflight 和序列化。应在一次有界遍历中生成唯一的可信快照并同时计数，后续 identity/JSON/AESGCM 只能消费该快照；或仅接受可证明稳定的 exact builtin JSON container 并拒绝自定义容器。
2. 遍历必须逐项计数并在入栈/复制前停止；不得通过 `stack.extend(generator)` 先无界消费。实际元素数必须独立于容器报告的 `len()` 受控。
3. 对实际消费值强制：results `<= 50`、单容器 items `<= 256`、总 nodes `<= 4096`、depth/string chars/UTF-8 bytes 均不超过声明上限，且 canonical envelope `<= 64 KiB`。
4. 新增恶意负测：虚假 `len()` 的 Sequence/Mapping、超大或无限 iterator、preflight/seal 间改变内容的 stateful iterator、并发 mutation、多字节 Unicode。拒绝路径必须证明未调用全量 identity HMAC/JSON/AESGCM，立即返回固定 `snapshot_oversized`，`pending == 0`、slot/registry/timer 全部清理，V1 响应不受影响。

## 已通过的第三轮边界

- 标准 authority copy/deepcopy/pickle/reduce 入口均 fail closed；普通 attested response deepcopy 仅在原 retriever 验证，payload/evidence mutation 均拒绝。该结果不关闭上面的 `copyreg` state 绕过。
- 依赖声明/锁定为 `cryptography>=45,<46`，环境实测 `cryptography 45.0.7`，实际调用 Rust/OpenSSL 后端 `AESGCM` 且 key 为 256 bit；`uv lock --check` PASS，未发现自制 XOR/keystream cipher 或越锁安装脚本。
- AES-GCM wrong key/AAD/job/request/profile/evidence/runner 与 ciphertext/tag/nonce mutation均返回固定错误；one-shot 第二次打开、跨 job/runner replay 和并发双开均 fail closed，只有一个并发 open 成功。
- 4-byte runner prefix + 锁保护 64-bit counter 的并发 nonce probe 无重复；rollback、duplicate、overflow 均 fail closed。
- 对实际 worker/timer args、`_ShadowJob`、runner 持久字段、observation 和 callback/closure 的递归对象图检查未发现 query/profile target/ACL/entity/generation/locator/content/token/credential 明文；敏感请求和 legacy evidence 仅存在于 AEAD envelope。
- 普通百万结果、深层、超长 ASCII/Unicode、超大 mapping 输入会在 identity/seal 前拒绝并释放 slot；该结果不覆盖上面的虚假 `len()` 和二次迭代 TOCTOU。
- C1 专项三文件测试完成且 exit code 0（173 cases）；三份最新 C1 测试已逐段重读。现有绿测未覆盖本轮两个最小反例。

## 第三轮最终裁决

`C1-01: PASS`

`C1-02: FAIL`

`C1 overall: BLOCKED`

`C2: NOT_AUTHORIZED`

必须先关闭 `P1-C1-02-R3-01` 与 `P1-C1-02-R3-02`，逐项加入上述 adversarial 回归，并重新执行 C1 专项、全量 pytest、ruff、锁内 format/diff、只读内存 compile、golden digest、正式库 hash/quick_check/schema 与 only-main 检查。systemError 导致的工程复验 unavailable 不替代这些验收。
