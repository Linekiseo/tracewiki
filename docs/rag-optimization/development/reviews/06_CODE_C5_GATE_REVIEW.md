# Code C5 Gate Review

C5-01 独立 Gate 时间：2026-07-28 02:47 +0800

共享工作目录：`/Users/example/project/rag`

分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

## C5-02 第四轮最终极简复审裁决（2026-07-28 14:12 +0800）

```text
C5-02 engineering FAIL
P0 findings: 0
P1 findings: 1
C-B4 PRODUCTION EXECUTION NOT AUTHORIZED
```

### P1-01：自洽但未由 production 产生的 edge labels 可达到 AVAILABLE 并通过 verify

位置：
`src/evidence_rag/evaluation/cb4.py:630-643`、
`src/evidence_rag/evaluation/cb4.py:851-893`、
`src/evidence_rag/evaluation/cb4.py:1982-2019`

最小复现：不创建 `SemanticEdge`、不运行 production treatment，直接构造 200 个
`EdgeLabelRecord`。每条记录使用任意 `symbol:never-produced-N` target，并用
`_canonical_edge_id(...)` 为这些虚构字段生成自洽 id；`case_id` 只轮换固定的 30 个
Golden Python id。随后：

```python
dataset = build_edge_label_dataset(records, fixture_edge_count=200)
plan = edge_precision_sample_plan(
    fixture_edge_count=200,
    label_dataset=dataset,
)
```

实际返回：

```text
record_count=200
status=available
precision_gate_satisfied=true
```

把同一 dataset 及其重算后的 evidence ref 写入系统临时 artifact 的 manifest、
edge-quality 和 attempt-audit 后，`verify_artifact(...)` 仍返回：

```text
verify_status=verified
label_status=available
sample_count=200
precision_gate_satisfied=true
```

当前校验只证明 `edge_id` 与记录自身字段的 hash 一致；`build_edge_label_dataset`
仅检查 `case_id` 属于 30 个允许值，并从这些声明记录本身生成
`eligible_edge_membership_hash`。execute/build/verify 都没有把 `(case_id, edge_id)`
与该 Golden case 本次实际 production treatment 输出的 eligible edge 集合绑定。
因此 unknown edge 以及 edge 与 Golden case 的 membership mismatch 均可作为 valid，
200 条唯一伪造记录可错误授权 production quality。现有定向 172 项测试全部通过，但
`unknown_edge` 用例只篡改 id 而不重算字段，未覆盖这个自洽未知边路径。

## C5-02 第三轮极简复审裁决（2026-07-28 13:30 +0800）

```text
C5-02 engineering FAIL
P0 findings: 0
P1 findings: 1
C-B4 PRODUCTION EXECUTION NOT AUTHORIZED
```

### P1-01：无 production labels/digest 的摘要可越过 quality acceptance

位置：
`src/evidence_rag/evaluation/cb4.py:520-604`、
`src/evidence_rag/evaluation/cb4.py:653-785`、
`src/evidence_rag/evaluation/cb4.py:1630-1649`

最小复现：

```python
forged = edge_precision_sample_plan(
    fixture_edge_count=200,
    human_labeled_edge_count=200,
)
forged["label_validation"]["validation_mode"] = "exact-production-label-records"

edge_quality_acceptance_gate(
    label_qualification=forged,
    treatment_qualified=True,
)
```

本例没有传入任何 `SemanticEdgePrecisionLabel`，也没有 production sampled edge/label
digest。实际仍返回：

```text
label_status=available
precision_gate_satisfied=true
production_quality_qualification_eligible=true
treatment_qualified=true
```

`label_qualification` 中没有任何 digest/hash 字段；artifact verifier 只要求
`validation_mode == "exact-production-label-records"`，并检查 manifest、
`edge-quality.json`、`attempt-audit.json` 复制了同一份摘要。因此同一个伪造摘要可以在
三处保持一致并通过 acceptance。

预期：`AVAILABLE` / production qualification 必须绑定 exact production label records
和 production sampled IDs 的 canonical digest；prevalidated count 或仅修改 mode 字符串
不得冒充 exact-production labels。manifest、edge quality、attempt audit 与 acceptance
必须校验同一 digest。

## C5-02 第二轮极简复审裁决（2026-07-28 13:00 +0800）

```text
C5-02 engineering FAIL
P0 findings: 0
P1 findings: 1
C-B4 PRODUCTION EXECUTION NOT AUTHORIZED
```

### P1-01：C-B4 的 `<200` 真实标签状态仍会越过 `PROVISIONAL` Gate

位置：
`src/evidence_rag/evaluation/cb4.py:515-564`、
`tests/test_code_cb4_run.py:207-228`

最小复现：

```python
edge_precision_sample_plan(
    fixture_edge_count=1,
    human_labeled_edge_count=1,
)
```

实际：

```text
status=available
sample_count=1
sampling_scope=all-fixture
```

同一条已真实标注 edge 经 production `evaluate_semantic_edges` 返回：

```text
status=provisional
sample_count=1
precision=1.0
```

预期：`1 < 200`，C-B4 sample plan 必须与 production evaluator 一致返回
`PROVISIONAL`；只有 `>=200` 个真实、可用标签才能返回 `AVAILABLE`。当前不一致会让
C-B4 准备/执行层把小 fixture 的质量证据提前标为 available。

## C5-02 + C-B4 PREPARED 精简复审裁决（2026-07-28 12:24 +0800）

```text
C5-02 engineering FAIL
P0 findings: 0
P1 findings: 3
C-B4 PRODUCTION EXECUTION NOT AUTHORIZED
```

### P1-01：未解析的 occurrence 可授权 `OVERRIDES` 内部边

位置：
`src/evidence_rag/rag/sources/code/semantic_edges_v1.py:931-937`、
`src/evidence_rag/rag/sources/code/semantic_edges_v1.py:1009-1033`

最小复现：构造一个 `is_implementation=True` 且 source/target 均 resolved 的
relationship；再放入同 path/target symbol、带 `SCIP_EXPLICIT_OVERRIDE_ROLE`，但
`symbol_link=UNRESOLVED` 的 occurrence。调用 `treat_python_semantic_edges`。

实际：

```text
edge_types=['IMPLEMENTS', 'OVERRIDES']
diagnostic_codes=['link_unresolved']
```

预期：未解析、local 或 external occurrence 不能作为明确 override 证据；本例最多保留
独立成立的 `IMPLEMENTS`，不得生成 `OVERRIDES`。

### P1-02：零人工标签时仍发布 coverage、unresolved 与零 noise 数值

位置：
`src/evidence_rag/rag/sources/code/semantic_edges_v1.py:1357-1402`

最小复现：对一个含 1 条 edge 的 result 调用
`evaluate_semantic_edges(result, labels=(), eligible_edge_count=2,
unresolved_before=10, unresolved_after=7)`。

实际：

```text
status=unavailable
precision=None
coverage=0.5
unresolved_reduction_count=3
unresolved_reduction_rate=0.3
graph_noise_count=0
```

预期：没有真实人工 labels 时，precision、coverage、unresolved 与 noise 均必须明确
unavailable；`graph_noise_count=0` 不能冒充已观察到零噪声。

### P1-03：C-B4 不接受 Gate 要求的 production execution 授权

位置：
`src/evidence_rag/evaluation/cb4.py:41-48`、
`src/evidence_rag/evaluation/cb4.py:637-653`

最小复现：临时 Gate report 只写入：

```text
C5-02 PASS
P0 findings: 0
P1 findings: 0
C-B4 PRODUCTION EXECUTION AUTHORIZED
```

调用 `_validate_c5_02_execution_gate(root)`。

实际：

```text
CB4Error: C5-02 completion Gate has not authorized C-B4 production evaluation execution
```

将最后一行改成实现私有字符串
`C-B4 PRODUCTION EVALUATION AUTHORIZED` 后才返回 `authorized`。

预期：C-B4 的 Gate、授权 snapshot 与未来 artifact verifier 必须消费本 Gate 规定的
`C-B4 PRODUCTION EXECUTION AUTHORIZED`；当前字符串不一致会阻塞正式执行主路。

## 第二轮极简复审最终裁决（2026-07-28 11:28 +0800）

```text
C5-01 PASS
P0 findings: 0
P1 findings: 0
C5-02 AUTHORIZED
```

本轮仍只审
`src/evidence_rag/rag/sources/code/scip_v1.py`、最小 package lazy export 与
`tests/test_code_scip_v1.py`。首轮三个 P1 均已关闭，provided `index.scip` 的 bounded
consumer 主路无回归；未发现新的主流程 P0/P1。授权仅允许开始 C5-02，不代表 C5-02
已实现、完成或获得质量资格。

### 首轮 P1 关闭证据

1. **Resolver 原子失败：CLOSED。** `RuntimeError`、`TimeoutError`、非 sequence、
   非 `ScipEntityRef`、scope/type/path/symbol contract 违规都会抛出统一
   `ScipResolverFailure`，由 `consume` / `consume_bytes` 在最外层转换为整次
   `PARTIAL`。独立 midstream timeout probe 已先成功解析并进入 resolver，再确认
   documents/occurrences/external_symbols/relationships 全部为 0；diagnostic 固定只有
   `resolver_failure`，fallback 固定为 `delegated / resolver_failure`，无后端错误文本
   或半解析 semantic 泄漏。RawObject/protocol/indexer provenance 作为非 semantic
   输入血缘保留。
2. **Hard deny 与 runtime pin：CLOSED。** `ScipCommandPin` 只允许
   `CONTAINER_NONE`，container runtime 必须为 repo 外绝对 realpath、普通可执行文件、
   非 symlink、非 world-writable，并匹配 docker/podman basename 与 executable
   SHA-256；tool name/version、image SHA-256 digest 和完整 argv grammar 均冻结。
   独立构造的 shell、interpreter、package manager、`install`、`-c`、`eval`、`exec`、
   response file、repo script、network bridge、repo rw 及缺 isolation flag 共 17 类
   变体全部在 pin 构造期拒绝。local/host mode fail closed。
3. **Container 隔离与 cleanup：CLOSED。** 固定 grammar 强制 `--rm`、唯一随机
   `--name`、`--network none`、`--read-only`、CPU/memory/pids、`no-new-privileges`、
   repo 只读 mount 与独立 0700 output mount；默认 backend 以
   `start_new_session=True / shell=False` 启动，timeout/output cap 会 kill process
   group。timeout/error 后使用相同已验证 runtime 独立执行
   `rm -f <unique-container-name>`；cleanup 成功/失败均记录，失败仍保持
   `PARTIAL`。独立 timeout capture 返回
   `indexer_timeout / cleanup_status=succeeded`，主 argv 与 cleanup argv 使用同一唯一
   name。

上述 runner 复审全部使用临时假 runtime、注入 capture executor 与程序化输出；没有
执行真实 Docker、Podman、scip-python 或任何 repo/indexer/install 脚本。

### Consumer 主路、范围与回归

- 程序化合法 SCIP subset 的 Index/Metadata/Document/Occurrence/
  SymbolInformation/Relationship、legacy/typed ranges、unknown wire、安全预算、
  path/position、definition/reference/external/local、duplicate、scope/ACL、provenance
  继续由 60 项专项覆盖。
- 独立 provided-index probe 返回
  `complete / documents=1 / occurrences=4 / semantic_ready=false`；runner 未参与。
- `scip_v1.py` 仍无 persistence dependency；package 仅 lazy export。未发现 C5-02
  edge/schema/store/runtime/indexer treatment 实现，未联网下载，未写正式库、eval 或
  Run。

验证结果：

```text
tests/test_code_scip_v1.py: 60 passed
related C5/C4/contracts suite: 195 passed
full suite: 574 passed, 1 warning in 140.17s
target ruff check: PASS
target ruff format --check: 3 files already formatted
target no-index diff --check + tracked git diff --check: PASS
main only / single worktree: PASS
```

相关 195 的固定集合为：
`test_code_scip_v1.py`、`test_code_graph_v2.py`、
`test_code_graph_retrieval_v2.py`、`test_code_dense_v2.py`、
`test_code_rerank_v2.py`、`test_code_rag_contracts.py`。

三个只读目标在第二轮报告写入前后的 SHA-256 均保持：

```text
scip_v1.py       20f690a0b1205cddb82d6e8a431bb3e790109e082bb017abb541d0250dfa28f7
code/__init__.py 9037d67be304a37bf1eed9be50f4fa88e50e1124b37aa17fb7bce91d49e39ede
test_scip_v1.py  48b1c238d842cc171786401a5b0a00077611c182456e14e91825902a61c0efb9
```

正式 `var/evidence-rag.sqlite3` 在第二轮测试前后均为 size `1915490304`、mtime
`2026-07-27 15:11:03 +0800`、SHA-256
`9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b`。
全 Run 树 111 个文件的 content/state SHA-256 在第二轮测试前后均为
`5a49a84648c67849b5017898f6ad86d5251ab4388e302b46aa69a790d0247e95` /
`b502162119c08f07d79688aa39f4c58c635f93815268356b4139acdc8e10ef11`。

## 首轮裁决（2026-07-28 02:47 +0800；已由第二轮关闭）

```text
C5-01 SCIP Consumer + Safe Runner: FAIL
P0 findings: 0
P1 findings: 3
C5-02 NOT AUTHORIZED
```

本轮只审
`src/evidence_rag/rag/sources/code/scip_v1.py`、最小 package lazy export 与
`tests/test_code_scip_v1.py`。protobuf consumer、路径/位置规范化、只读 governed
link、provenance 和非阻塞 fallback 的主体功能已通过；但 resolver 失败语义与外部
runner 的安全边界仍有三个主流程 P1，因此不能写 `C5-01 PASS`，也不能授权 C5-02。

## P1-01：resolver 后端异常被伪装成真实 unresolved

位置：
`src/evidence_rag/rag/sources/code/scip_v1.py:1775-1778`、
`src/evidence_rag/rag/sources/code/scip_v1.py:1808-1817`

`resolve_file` / `resolve_symbol` 把 `LookupError`、`RuntimeError`、`TypeError`、
`ValueError` 全部吞掉并改成空候选，再由 `_validate_candidates` 生成
`ScipLinkStatus.UNRESOLVED` 和“resolver returned no governed candidate”。这会把后端
离线、超时包装异常、响应类型错误等基础设施失败计入真实 unresolved，破坏
unresolved reduction、fallback 原因和治理诊断的可信度。

最小复现（未改代码）：

```python
class BrokenResolver:
    def resolve_file(self, **kwargs):
        raise RuntimeError("backend offline")

    def resolve_symbol(self, **kwargs):
        raise RuntimeError("backend offline")

result = ScipConsumer().consume_bytes(
    valid_index, repo_root=repo, scope=scope, resolver=BrokenResolver()
)
```

实际：

```text
status=partial
file_link=unresolved
diagnostic_codes=duplicate_occurrence,external_symbol,unresolved
messages:
  FileVersion resolver returned no governed candidate
  CodeSymbol resolver returned no governed candidate
```

预期：resolver 调用失败必须与合法的零候选分开，返回
`RESOLVER_UNAVAILABLE` 或明确的 resolver error/timeout diagnostic；不得声称 resolver
成功返回空结果。

## P1-02：allowlist 接受 shell 与仓库安装脚本

位置：
`src/evidence_rag/rag/sources/code/scip_v1.py:370-404`、
`src/evidence_rag/rag/sources/code/scip_v1.py:592-630`

`ScipCommandPin` 只校验 placeholder、版本、digest 和网络参数，不限制 executable
类别，也不拒绝 repo-relative script、shell `-c` 或把仓库挂载后执行脚本的容器 argv。
`shell=False` 只阻止 `subprocess` 自行增加一层 shell；当 allowlisted executable 本身
就是 `sh` 时，仍会执行 shell。该行为直接违反 C5-01 的 no-shell / 不可执行仓库安装
脚本边界。

最小复现一：使用当前 `/bin/sh` 的真实 SHA-256，以下 pin 构造成功；注入 capture
executor 后实际收到 `/bin/sh -c ./install.sh ...`，runner 返回 `complete`。探针只
捕获 argv，没有执行脚本。

```python
ScipCommandPin(
    argv_template=("sh", "-c", './install.sh "$1"', "scip-gate", "{index}"),
    tool_name="scip-python",
    tool_version="0.6.8",
    network_isolation=ScipNetworkIsolation.HOST_SANDBOX,
    executable_sha256=sha256_of_bin_sh,
)
```

最小复现二：以下 default container 路径的 pin 同样构造成功：

```text
docker run --network=none -v .:/repo \
  indexer@sha256:aaaa...aaaa sh /repo/install.sh {index}
```

预期：policy 构造或 run 前必须 fail closed 地拒绝 shell/interpreter escape 和仓库脚本
入口；只允许冻结的 indexer executable/container entrypoint 与冻结参数形状。

## P1-03：容器 indexer 没有实际 CPU/内存/超时清理保证

位置：
`src/evidence_rag/rag/sources/code/scip_v1.py:396-404`、
`src/evidence_rag/rag/sources/code/scip_v1.py:2073-2143`

default executor 对 `HOST_SANDBOX` 一律返回 unavailable，因此当前唯一可运行的默认
网络隔离路径是 `CONTAINER_NONE`。但 container pin 只强制 digest 与
`--network=none`，不强制容器级 CPU、memory、pids、`--rm` 或确定的 container
identity。`_bounded_subprocess` 的 `RLIMIT_CPU` / `RLIMIT_AS` 和 `killpg` 只施加到
本机 `docker` client 进程；daemon/VM 中的 indexer container 不继承这些限制，杀死
client 也不能证明 container 已终止。

最小复现：上节 container pin 在没有任何 CPU/memory/cleanup 参数时即通过
`ScipCommandPin` 校验：

```text
pin_constructed=true
has_cpu_flag=false
has_memory_flag=false
has_rm_or_cleanup_contract=false
```

预期：要么对受支持的 container runtime 强制并校验容器级 resource flags 与可验证
cleanup/timeout，要么将该 backend 明确判定为 unavailable；不能把 client 进程的
rlimit 描述为实际 indexer 的 CPU/memory cap。

## 已通过的功能与回归证据

- 使用程序化小 protobuf 独立覆盖 Index / Metadata / ToolInfo / Document /
  Occurrence / SymbolInformation / Relationship，legacy packed/unpacked 与 typed
  single/multi-line range 均可解析；unknown varint/fixed64/length/fixed32/group
  wire 可安全跳过。
- file/message/depth/per-message fields/total fields/documents/symbols/occurrences/
  strings/range 各预算均独立触发 `ScipDecodeError`。在完整合法 Index 后追加截断
  length-delimited field，最终为 `PARTIAL`，且 documents/occurrences/relationships
  全为 0、provenance 为 `None`，无半解析 semantic 泄漏。
- path 规范化、repo-root/symlink containment、0-based 到 1-based half-open
  position、definition/reference/external/local、duplicate occurrence 确定去重通过；
  相同输入两次完整结果相等。
- project/repository/generation/ACL/path/type scope、resolved/ambiguous/unresolved/
  badpath/external/local、content SHA-256、indexer/protocol、RawObject provenance
  正向和 mismatch 路径通过；`semantic_ready` 始终为 `False`。
- runner 默认 off、显式 opt-in、exact pin/digest、repo cwd、clean env、network
  policy、无隐式 shell、stdout/stderr/output/index cap、失败 `PARTIAL` 与 fallback
  的既有正向/负向测试通过；但 P1-02/P1-03 所列绕过未被现有测试覆盖。
- package lazy export 可导入且 `__all__` 无重复；C5-01 模块没有 persistence
  dependency。未发现 C5-02 edge/schema/store/runtime/indexer 实现，未联网下载，也
  未写正式库、eval 或 Run。

验证结果：

```text
tests/test_code_scip_v1.py: 29 passed
related C5/C4/contracts suite: 164 passed
full suite: 543 passed, 1 warning in 146.62s
target ruff check: PASS
target ruff format --check: 3 files already formatted
target no-index diff --check + tracked git diff --check: PASS
main only / single worktree: PASS
```

相关 164 的固定集合为：
`test_code_scip_v1.py`、`test_code_graph_v2.py`、
`test_code_graph_retrieval_v2.py`、`test_code_dense_v2.py`、
`test_code_rerank_v2.py`、`test_code_rag_contracts.py`。

三个只读目标在报告写入前后的 SHA-256 均保持：

```text
scip_v1.py       b693a175dd8b1106ba8b3ff95a20650af2ee09d041211e50eb490fadc1d7d746
code/__init__.py 1b512033b2dbe0d504e67fc29a3a4c3ddfff0eca0d7c13af4ac5c4502381bf9b
test_scip_v1.py  36a6557eb83f028f84276cb04a17a7626282834c550ab5b69aeb9013bc9a2fbb
```

正式 `var/evidence-rag.sqlite3` 在测试前后均为 size `1915490304`、mtime
`2026-07-27 15:11:03 +0800`、SHA-256
`9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b`。
全 Run 树 111 个文件的 content/state SHA-256 在测试前后均为
`5a49a84648c67849b5017898f6ad86d5251ab4388e302b46aa69a790d0247e95` /
`b502162119c08f07d79688aa39f4c58c635f93815268356b4139acdc8e10ef11`。

本 Gate 不修实现；关闭上述三个 P1 并补最小回归后，重新执行 C5-01 独立 Gate。

## C5-02 第五轮最终极简复审裁决（2026-07-28 14:55 +0800）

第四轮唯一 P1 已关闭。固定 Golden Python30 由 exact
`SemanticEdgeTreatment` 生成 30 个 per-case attestation、合计 42 条 canonical
production outputs；attestation 固定绑定 component identity、pipeline profile、完整
input fixture、generation、edge payload 与 provenance，root hash 为
`sha256:23feb1ee5ecf3f59cb7aad63b487bae61321da0fc140d64ae27dfb43b8b9f064`。

独立恶意探针确认：42 条 self-consistent 非 Golden edge records 因不属于对应 case 的
production output 被拒绝；210 条伪造 records 在 fixture 上界处被拒绝。完整真实 42 条
records 只能得到 `PROVISIONAL`、`precision_gate_satisfied=false`；零 records 为
`UNAVAILABLE`。dataset digest 同时承诺 records 与 production-output attestation。
portable verifier 对完整 attestation、case outputs、records membership、dataset、
count/status、quality/attempt/acceptance 逐层重算并绑定冻结 root hash；output set、
digest、component identity、删除与跨文件 mismatch 探针均 fail closed。既有
occurrence evidence、零标签质量真值、状态机、exact authorization parser 与
content-addressed records 路径未见回归。

独立定向集合（CB4、semantic、SCIP、evaluation、Golden、CB1/2/3/5）218 项全部通过；
六个目标文件 ruff check / format check、`git diff --check`、only main / single
worktree 均通过。复审期间未修改实现、测试、其他文档、Golden、evaluation/evals/Run
或正式库。

C5-02 engineering PASS
P0 findings: 0
P1 findings: 0
C-B4 PRODUCTION EXECUTION AUTHORIZED

## C-B4 production Run 最终只读审计（2026-07-28 15:46 +0800）

审计对象：
`evaluation-run://project-code-golden-v2/a22b322a141741ae9a710aa347fbf032`，
目录 `evals/code/runs/a22b322a141741ae9a710aa347fbf032`。

离线 `cb4.verify_artifact` 独立返回 `status=verified`、
`treatment_qualified=false`、`label_sample_count=0`、
`label_status=unavailable`、`precision_gate_satisfied=false`；manifest canonical
hash 为
`sha256:4541cace7b90143572014cb17bc3731680307a338a01c0a34e71e7de3a8f691a`。

Run 使用 exact `SemanticEdgeTreatment` / `c5-python-semantic-edges-v1`，固定
Golden Python30 membership 与相同 input profile/generation，执行
Tree-sitter conservative、SCIP semantic、merged policy 三臂；JavaScript 1 case
和 TypeScript 2 cases 明确 `unavailable`。production-output attestation 为 30
cases / 42 outputs，hash
`sha256:23feb1ee5ecf3f59cb7aad63b487bae61321da0fc140d64ae27dfb43b8b9f064`。

三臂各 30 cases / 42 `REFERENCES` edges；merged 的 42 条均合并 SCIP 与
Tree-sitter 双 provenance，conflict 为 0，SCIP/merged unresolved before/after
均为 0。artifact-local isolated SQLite `quick_check=ok`、foreign-key violations
为 0、journal mode 为 `delete`、schema user version 为 1；90 treatment rows 与
126 semantic-edge rows 逐行对账 input fixture、generation、result/output hash、
status、latency、memory、relation、scope 与 provenance，无差异。

零条真实 label records 使 edge precision、edge coverage、quality unresolved
reduction、graph noise 与 graph harmful quality 全部为 `UNAVAILABLE`、值为
`null`、不可用于 acceptance。三臂的 42/42 target coverage、edge/provenance/
conflict/unresolved 仅为 `non_quality_structural_counts` /
`non_quality_structural_observation`，不得解释为 precision、coverage 或 production
quality；因此本 Run 的 `NOT QUALIFIED` 是真实质量结论，不影响 C5 engineering
完成。

artifact 恰为 9 个只读文件，目录 mode `0555`、文件 mode `0444`；canonical JSON、
manifest file hash/size 引用、Run/artifact/timestamp/profile、attestation 与 label
evidence 引用均一致。无 symlink、WAL/SHM、pyc/pycache、secret 或本机绝对/临时路径。

正式 `var/evidence-rag.sqlite3` 仍为 size `1915490304`、SHA-256
`9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b`。
原 111 文件 Run 树指纹仍为
`sha256:773a2dcdafda010f840fbaf0bf06f0b6747daafa473efede385e236076ff15be`；
当前 120 文件仅新增本 Run 的 9 文件。qualification ledger 当前唯一有效 qualified
决定仍是 C-B0
`evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`；
C-B1/B2/B3/B5 与本 C-B4 均仅作 audit，不得用于 qualification。06 Gate 的
`C5-02 engineering PASS`、P0/P1=0 保持有效。

C5 overall engineering COMPLETE
C-B4 production audit VERIFIED — NOT QUALIFIED
C6-01 AUTHORIZED
