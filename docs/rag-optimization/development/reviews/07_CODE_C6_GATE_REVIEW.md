# Code C6-01 Foundation Gate Review

独立 Gate 时间：2026-07-28 16:48 +0800

共享工作目录：`/Users/example/project/rag`

分支 / HEAD：`main` / `bc3326edc761e3bdb42ed78726a21f314ab44974`

## 裁决

```text
C6-01 engineering FAIL
P0 findings: 0
P1 findings: 4
C6-02 NOT AUTHORIZED
```

C6-01 的主干设计大部分已经形成：目标 commit 的 Git tree/blob 读取、真实
`CodeParser -> CodeUnitBuilder` 复用、临时 SQLite 原子 publication、Commit fact
保留、exact-scope query、三 commit rename/move fixture、确定性 lineage 和
split/merge review-only candidate 均有直接代码与测试支持。但下列四条问题分别破坏
historical 正文准确性、pin 保留、ACL fail-closed 和 current/dirty version identity，
均处在本 Gate 明确要求的主流程上，因此不能只按低价值 P2/P3 留待后续。

## P1-01：同一 content key 会把一个 path 子集错误复用于另一个 path 请求

位置：

- `src/evidence_rag/rag/sources/code/history_v2.py:374-420`
- `src/evidence_rag/rag/sources/code/history_v2.py:686-705`
- `src/evidence_rag/rag/sources/code/history_v2.py:1628-1675`
- `tests/test_code_history_v2.py:289-312`

`HistoryMaterializationRequest.paths` 会直接决定 `_tree` 和最终 publication 的
files/units，但 `history_content_key` 与 `get_exact` 不承诺 path selection，也不承诺
会改变 materialized/skipped 内容的 policy identity。cache-hit 分支拿到同一
repo/SHA/profile/generation/schema/builder 的 publication 后直接返回，完全没有核对
本次 `request.paths`。

独立 `/tmp` 两文件 Git repo 探针，对 memory 与 SQLite 两个 store 均执行：

```python
first = materializer.materialize(request(paths=("a.py",)))
second = materializer.materialize(request(paths=("b.py",)))
```

两种 store 的实际结果完全相同：

```text
first_paths=["a.py"]
second_requested_paths=["b.py"]
second_returned_paths=["a.py"]
second_cache_hit=true
same_content_key=true
```

影响：请求 `b.py` 的 historical query 会稳定返回 `a.py` publication；如果首个请求是
不存在路径、oversize policy 或其他子集，后续相同 exact version 的正文也会被首个
cache 内容决定。目标 SHA 虽正确，file/unit/locator/body 的查询选择却错误，直接违反
“historical query 返回目标正文”和 cache/idempotent 要求。

最小修复：

1. 二选一地固定 publication 语义：
   - 将规范化 path set 及所有 content-affecting policy/profile identity 纳入
     publication/cache identity；或
   - 每个 content key 始终构建同一 canonical full-profile publication，`paths` 仅作为
     cache 之后的授权查询 view，不参与被持久化内容。
2. cache hit 前验证请求的 materialization selection identity，mismatch 不得返回已有
   publication。
3. 对 memory/SQLite 各补“先 `a.py`、后 `b.py`”以及“先 missing/oversize、后正常正文”
   回归。

## P1-02：cache hit 吞掉后置 EXPLICIT/REFERENCED pin，随后 TTL 可将其驱逐

位置：

- `src/evidence_rag/rag/sources/code/history_v2.py:1005-1033`
- `src/evidence_rag/rag/sources/code/history_v2.py:1268-1340`
- `src/evidence_rag/rag/sources/code/history_v2.py:1440-1476`
- `src/evidence_rag/rag/sources/code/history_v2.py:1651-1675`
- `tests/test_code_history_v2.py:456-484`

现有正向测试只覆盖 publication 第一次创建时已经携带 pin。真实主路中，一个已存在的
普通 historical cache 后来可能被 Claim/Run/Codex 引用或被用户显式查询；此时
cache-hit 分支没有合并 `request.pin_reasons`，store 的 existing-publication 分支也只
返回旧 payload。

独立探针对 memory/SQLite 分别先无 pin materialize，再以
`pin_reasons=(REFERENCED,)` 请求同一 exact publication，随后执行
`ttl_seconds=0`、`retain_referenced_pins=True`、`retain_hot_publications=False` 的
eviction。两种 store 均得到：

```text
cache_hit=true
requested_pin=referenced
returned_pin_reasons=[]
evicted_after_pin_request=true
commit_fact_retained=true
```

影响：Commit fact 的确没有被删除，但明确 referenced/explicit 的派生 historical
publication 仍会被清除；“被引用的 commit / 用户显式查询 commit 保留”语义依赖首次
物化顺序，不是真实可用的 retention contract。

最小修复：

1. 在 cache hit / idempotent publish 内对 pin reason 做单调 union，并在 memory 与
   SQLite transaction 中原子持久化；
2. 返回合并后的 publication，不能只改调用侧临时对象；
3. 增加 unpinned -> referenced、unpinned -> explicit、重复 pin 和 rollback 的两种
   store 回归，再验证 TTL/LRU 不驱逐升级后的 publication。

## P1-03：公开 content-key 读取入口绕过 project/repository/version/ACL scope

位置：

- `src/evidence_rag/rag/sources/code/history_v2.py:898-923`
- `src/evidence_rag/rag/sources/code/history_v2.py:949-959`
- `src/evidence_rag/rag/sources/code/history_v2.py:1182-1205`
- `tests/test_code_history_v2.py:472-473`
- `tests/test_code_history_v2.py:487-515`

`get_exact` 对 wrong project/repository/SHA/generation/ACL/schema/builder 能 fail closed；
独立探针对 memory/SQLite 的 wrong ACL 也均返回 `None`。但是同一个 public store
protocol 还暴露 `get_publication(content_key)` 和别名 `get`，只凭不含 project/ACL 的
可推导 content key 就返回完整 publication payload，包括历史源正文。

独立探针在 `acl:engineering` 下 publication 后：

```text
wrong_acl_exact=false
unscoped_content_key_read=true
unscoped_acl=acl:engineering
```

memory 与 SQLite 结果一致。

影响：调用方可以绕过 exact authorized query 直接按 key 读取跨 ACL 的 historical
files/units。content key 是内容身份，不是授权凭据；即使当前尚未 runtime 接线，也不能
把这个入口作为可导出的 store contract 带入 C6-02。

最小修复：删除/私有化无 scope 的 payload 读取与 `get` 别名，或让所有可公开读取强制
接收并校验完整 project/repository/SHA/generation/ACL/profile/schema/builder scope。
hot access 计数也必须通过已授权 exact read 更新。对 memory/SQLite 各补 raw-key ACL
负向测试。

## P1-04：current/dirty namespace 接受 stable version 与 commit SHA 不一致

位置：

- `src/evidence_rag/rag/sources/code/history_v2.py:270-371`
- `tests/test_code_history_v2.py:215-239`
- `docs/rag-optimization/sources/01_CODE_SOURCE_RAG.md:830-842`
- `docs/rag-optimization/development/01_CODE_SOURCE_DEVELOPMENT_PLAN.md:1969-1976`

historical namespace 会强制 `stable_version == commit_sha`，但 current 分支只检查
`+dirty.` 字样是否存在，不绑定 stable version 的 base SHA 与 `commit_sha`。独立构造
探针使用 `old_sha = "1"*40`、`new_sha = "2"*40`：

```python
HistoryNamespace.current(
    stable_version=new_sha,
    commit_sha=old_sha,
    dirty=False,
    ...
)
HistoryNamespace.current(
    stable_version=f"{new_sha}+dirty.manifest-new",
    commit_sha=old_sha,
    dirty=True,
    ...
)
```

实际：

```text
clean_mismatched_sha_accepted=true
dirty_base_mismatched_sha_accepted=true
```

影响：current contract 可把新 stable version/current generation 标到旧 commit SHA；
dirty contract 也可把 manifest identity 标到错误 base commit，正是验收要求禁止的
current/dirty/historical wrong-version 混淆。现有测试只验证 namespace 名称和 dirty
布尔值不同，没有验证 base SHA 一致。

最小修复：

1. clean current 强制 `stable_version == commit_sha`；
2. dirty current 严格解析 `<base-sha>+dirty.<manifest-hash>`，并强制
   `<base-sha> == commit_sha`，同时验证非空、冻结格式的 manifest identity；
3. 增加 clean-old-SHA、dirty-wrong-base、dirty-malformed-manifest 负向测试。

## 已直接支持的主流程

以下结论由当前工作树代码、专项测试和隔离探针直接支持；它们不抵消上述 P1：

- contracts 使用 frozen/slotted dataclass，并显式携带 project、repository、requested
  ref、full SHA、generation、ACL、profile/schema/builder/parser/policy provenance；
  historical file/unit/provenance/trace 对 scope 做一致性校验。
- ref 通过 `git rev-parse --verify --end-of-options <ref>^{commit}` 解析为 full SHA；
  explicit ref + expected SHA mismatch fail closed。路径是 POSIX repository-relative
  safe path；Git 使用 argv、`shell=False`、deadline 与 stdout/stderr cap。
- tree/blob 只读目标 `ls-tree <sha>` 与 `show <sha>:<path>`；dirty worktree 的
  `return 999` 没有进入历史正文。binary、oversize、unsupported 与 invalid UTF-8
  使用明确 skip status，不伪装为 materialized。
- `CodeParser.parse` 与 `CodeUnitBuildRequest.from_parsed_file` /
  `CodeUnitBuilder.build` 为真实 C2 主路复用；source URI、lineage attributes、unit
  locator 与目标 SHA 绑定。
- memory copy-on-write 和临时 SQLite transaction 的 failpoint rollback 均不留下
  publication 或 Commit fact；SQLite 非系统临时目录路径被拒绝。
- exact query 的 wrong project/repository/SHA/generation/ACL/schema/builder 返回
  no match；Commit fact 在 TTL/LRU 后保留。注意该结论仅适用于 `get_exact`，不覆盖
  P1-03 的 raw-key 入口。
- 三 commit fixture 覆盖 symbol rename 与 file move；SAME_SYMBOL_AS /
  RENAMED_TO / MOVED_TO confirmed link、稳定排序、scope/version/generation/ACL
  校验和 explanation trace 通过。SPLIT/MERGE 只产生 review-required candidate。
- package 仅增加 lazy export；仓库内除目标测试外没有 `history_v2` runtime 调用点。
  未发现 C6-02 Diff->Symbol、C6-03 Test 语义或 runtime 接线。模块体量本身未作为
  blocker。

## 独立测试与探针

所有 pytest 均设置 `PYTHONDONTWRITEBYTECODE=1`、禁用 pytest cache，SQLite 与 Git
fixture 均位于系统临时目录。

```text
tests/test_code_history_v2.py
  13 passed

固定相关集合：
  tests/test_code_history_v2.py
  tests/test_code_history_refs.py
  tests/test_code_parser_ir.py
  tests/test_code_unit_builder.py
  tests/test_code_graph_v2.py
  tests/test_code_rag_contracts.py
  126 passed, 1 third-party deprecation warning

ruff check（3 个目标文件）
  PASS

ruff format --check（3 个目标文件）
  3 files already formatted

package lazy import + __all__ uniqueness
  PASS
```

额外 `/tmp` 探针：

1. memory/SQLite path cache collision：均复现 P1-01；
2. memory/SQLite 后置 referenced pin + TTL：均复现 P1-02，Commit fact 保留；
3. memory/SQLite wrong-ACL exact read 与 raw-key read 对照：exact fail closed，
   raw-key 均返回正文；
4. clean/dirty current 使用错误 base SHA：两者均被接受；
5. 所有探针只创建临时 Git repo/SQLite，退出后由 `TemporaryDirectory` 删除。

## Git、正式库与 Run 零写入证据

审查前、专项/探针后：

```text
branch: main
worktrees: 1
worktree path: /Users/example/project/rag
worktree branch: refs/heads/main
HEAD: bc3326edc761e3bdb42ed78726a21f314ab44974
```

正式库只做文件级 `stat` / SHA-256，没有建立 SQLite 连接：

```text
path: var/evidence-rag.sqlite3
size: 1915490304
mtime_epoch: 1785136263
mode: -rw-r--r--
SHA-256: 9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b
sidecars: 0
```

上述 size/mtime/hash 审前与测试/探针后完全一致，也与 C5 最终审计记录一致。

既有 `evals/code/runs` 恰为 120 个文件；审前与测试/探针后：

```text
content fingerprint:
67c2a8a9ea4adb6a74a81ea5ff233d7d5b9357cdac62f8f37e457bcb10c7579b

mode/size/mtime/path state fingerprint:
47a6a7a2d025983b82ccc0ff884e6b71ebe75f700383241b8eb60eca7331904d
```

三个只读目标在审查前与报告写入前保持：

```text
history_v2.py
702261c082fac2657e0529fa67882bb22432ff7a25ab8ccdac5923308be7243f

code/__init__.py
bd825c6bc0d2ce14ab3fb9fc765f664672cc904f6dc1651ffc5a1f1deedf802b

test_code_history_v2.py
a86e41beabb630d24ee4c42848d1f482e5efb710e33a843cbd799220a8c42716
```

本轮没有创建 branch/worktree，没有 commit/push，没有修改实现、测试、config/依赖、
其他文档、Golden、evaluation/evals/Run 或正式库。唯一仓库写入是本 review。

## 证据边界与未做事项

- 主控交接中的专项 13、Code tests 607、全量 678、全 src/tests Ruff、三文件 format、
  diff check、only-main/single-worktree 与正式库不变证据作为上游交接记录保留；本独立
  Gate 自行重复的是上列 13/126 固定集合、三文件静态检查和四组 `/tmp` 负向探针，
  没有重复全量 607/678。
- 没有运行 production evaluation，没有新建/修改任何 Run，没有打开正式 SQLite，
  没有联网或下载。
- 没有审查或要求 C6-02 Diff->Symbol、C6-03 Test semantics、C7/context/release 与
  runtime 接线；这些未实现边界本身不是本 Gate 的失败原因。
- 研究项目桥接的 `project_get_context` / `research_get_work` / `evidence_search` /
  `execution_get` / `review_list_pending` 工具在本会话未暴露，因此没有把本工程 Gate
  误写成 research workbench 的人类最终批准；本结论只基于可定位的当前工作树、既有
  Gate 文档、独立测试和隔离复现。

关闭四条 P1、补齐最小负向回归并重新执行独立 C6-01 Gate 前，C6-02 保持
**NOT AUTHORIZED**。

## C6-01 第二轮极简复审最终裁决（2026-07-28 17:27 +0800）

首轮四个 P1 已关闭。第二轮只复核这四条关闭与 C6-01 原主路，不扩展 P2/P3，也不以
模块体量、C6-02/C6-03/runtime 未实现作为 blocker。

### P1-01 关闭：canonical selection 与 publication/cache identity 已绑定

直接代码证据：

- `HistoryMaterializationSelection` 冻结并规范化 path set、parser version、policy
  version、max file/output、Git timeout 与 supported languages；
- selection identity 进入 `history_content_key`、publication、trace、provenance、
  memory/SQLite exact query 和 SQLite exact index；
- materializer 在 lookup 前从 exact request + injected parser 构造 selection，
  cache hit 仍严格比较完整 selection。

独立 `/tmp` 两文件/oversize Git repo 探针对 memory 与 SQLite 均确认：

```text
先 a.py 后 b.py：content key 不同，第二次只返回 b.py
先 missing.py 后 a.py：content key 不同，后者 status=complete
先 max_file_bytes=50 后 1000：content key 不同，后者 materialized
同 scope 更换 parser_version：content key 不同
规范化 path 顺序：专项测试确认同 key 并真实 cache hit
```

首轮按 cache 顺序返回错误正文的路径不可复现。

### P1-02 关闭：pin reason 单调 union、持久化与 rollback 已闭环

memory existing-publication 路径使用 copy-on-write；SQLite existing-publication 路径在
`BEGIN IMMEDIATE` transaction 内更新 payload。两者均先校验 exact publication/
selection/scope，再对 pin reasons 做单调 union；cache-hit materializer 调用同一
atomic publish 并返回合并后的 publication。

独立 memory/SQLite 探针均确认：

```text
unpinned -> REFERENCED：cache hit，返回并持久化 referenced
再 -> EXPLICIT：持久化 union=[explicit, referenced]
重复只提交 referenced：不会降级或丢失 explicit
TTL=0 / hot retention off：publication 不被驱逐
Commit fact：保留
after_pin_union failpoint：
  memory -> RuntimeError，SQLite -> HistoryPublicationError
  rollback 后 persisted pins=[]，无部分更新
```

初始 publication failpoint rollback、TTL/LRU 与 hot access 的既有主路也在固定相关集合
通过。

### P1-03 关闭：raw content-key payload API 已删除

public `HistoricalPublicationStore`、memory store 与 SQLite store 均只暴露完整
project/repository/SHA/generation/ACL/profile/schema/builder/selection exact read；
`get_publication(content_key)` 与 `get` 别名均不存在。授权校验成功后才更新
last-access/access-count。

独立 memory/SQLite 探针均确认：

```text
has get_publication=false
has get alias=false
wrong ACL exact read=None
wrong selection exact read=None
额外传 content_key 调用 exact API=TypeError
denied read 不增加 hot access；旧 publication 按 LRU 被正常驱逐
```

content key 不再能够作为 ACL capability 直接读取 payload。

### P1-04 关闭：clean/dirty current version 严格绑定 base SHA

clean current 现在强制 `stable_version == commit_sha`。dirty current 只接受
`<base-sha>+dirty.<manifest_identity>`；base 必须等于 canonical full
`commit_sha`，manifest identity 必须非空、control-free 且符合冻结的小写安全字符与
长度约束。

独立负向探针确认以下四类均抛出 `ValueError`：

```text
clean stable_version / commit_sha mismatch
dirty base / commit_sha mismatch
dirty empty manifest
dirty uppercase/slash malformed manifest
```

专项同时保留正确 clean current、正确 dirty manifest 与 historical namespace 分离的
正向覆盖。

### 原 C6-01 主路与 C0-C5 回归

固定相关集合直接覆盖并通过：

- explicit ref -> full SHA、目标 Git tree/blob 与 dirty worktree 分离；
- historical file/unit/body/locator/provenance 绑定目标 SHA/generation/ACL；
- 真实 `CodeParser -> CodeUnitBuilder` 复用，binary/oversize/unsupported honest skip；
- memory/临时 SQLite atomic publication/rollback、exact query、TTL/LRU、Commit fact；
- 三 commit symbol rename + file move，SAME_SYMBOL_AS / RENAMED_TO / MOVED_TO
  confirmed；SPLIT/MERGE 保持 review-required candidate；跨 scope/version/
  generation/ACL fail closed；
- lazy package export 可导入且 `__all__` 无重复；
- `src`/`tests` 除 `code/__init__.py` lazy export 与本专项外没有 history_v2 runtime
  调用点；没有 C6-02 Diff->Symbol、C6-03 Test semantics 或 runtime 接线。

### 第二轮独立验证

所有 pytest 均设置 `PYTHONDONTWRITEBYTECODE=1` 并禁用 pytest cache；Git/SQLite
探针均位于系统临时目录。

```text
tests/test_code_history_v2.py:
22 passed

固定相关集合：
tests/test_code_history_v2.py
tests/test_code_history_refs.py
tests/test_code_parser_ir.py
tests/test_code_unit_builder.py
tests/test_code_graph_v2.py
tests/test_code_rag_contracts.py
147 passed, 1 third-party deprecation warning

ruff check（3 个目标文件）:
PASS

ruff format --check（3 个目标文件）:
3 files already formatted

lazy import + HistoryMaterializationSelection + __all__ uniqueness:
PASS

目标文件 trailing whitespace scan:
0 findings
```

主控交接的 `147`、Code tests `616`、全量 `687`、全 `src tests` Ruff、目标 format、
diff、only-main/single-worktree 为上游独立证据；本轮自行重复的是专项 `22`、固定相关
`147`、目标静态检查和 memory/SQLite `/tmp` 负向探针，没有重复运行 616/687。

### Git、正式库与 Run 零写入终核

审前、测试/探针后、报告追加前均为：

```text
branch: main
HEAD: bc3326edc761e3bdb42ed78726a21f314ab44974
worktrees: 1
worktree: /Users/example/project/rag

formal DB:
path: var/evidence-rag.sqlite3
size: 1915490304
mtime_epoch: 1785136263
SHA-256: 9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b
sidecars: 0

evals/code/runs:
files: 120
content fingerprint:
67c2a8a9ea4adb6a74a81ea5ff233d7d5b9357cdac62f8f37e457bcb10c7579b
mode/size/mtime/path fingerprint:
47a6a7a2d025983b82ccc0ff884e6b71ebe75f700383241b8eb60eca7331904d

history_v2.py:
a9c3c2a33f8a2fcb97d4fdf4526c7b10455b5687143a3ca584f77a8c4b605b30
code/__init__.py:
a2a7e54b8a533eb8aaa839fa14a303f8ce63ad9411d5beabc21fa013864781a3
test_code_history_v2.py:
99a6b9cd03d7a38a5bf21145403ab4887cbbe9f658c36ce4574f086bd260208d
```

本轮没有创建 branch/worktree，没有 commit/push，没有打开正式 SQLite，没有修改实现、
测试、config/依赖、其他文档、Golden、evaluation/evals/Run 或正式库。唯一仓库写入是
向本 review 追加第二轮裁决。

```text
C6-01 engineering PASS
P0 findings: 0
P1 findings: 0
C6-02 AUTHORIZED
```

## C6-02 Diff->Symbol + C-B6 PREPARED 精简 Gate

独立 Gate 时间：2026-07-28 18:28 +0800

### 裁决

```text
C6-02 engineering FAIL
P0 findings: 0
P1 findings: 1
C-B6 PRODUCTION EXECUTION NOT AUTHORIZED
C6-03 NOT AUTHORIZED
```

### P1-01：C-B6 exact production identity 可接受被替换的方法或伪造同名类

位置：

- `src/evidence_rag/evaluation/cb6.py:42-47`
- `src/evidence_rag/evaluation/cb6.py:1288-1396`
- `tests/test_code_cb6_run.py:295-312`

要求的生产 identity 是
`diff_symbol_v2.DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2`；当前冻结的
`PRODUCTION_COMPONENT_IDENTITY` 只到
`evidence_rag.rag.sources.code.diff_symbol_v2.DiffSymbolMapper`。method/version 虽在
相邻字段分别检查，但没有绑定原始 `map_hunk` 方法对象或实现 identity。

独立无文件写入子进程最小复现：

```python
module = importlib.import_module(cb6.DIFF_SYMBOL_MODULE)
original = module.DiffSymbolMapper.map_hunk
module.DiffSymbolMapper.map_hunk = lambda self, *args, **kwargs: {"fake": True}
cb6.require_production_component_identity(module.DiffSymbolMapper())
```

实际返回：

```text
declared_identity=evidence_rag.rag.sources.code.diff_symbol_v2.DiffSymbolMapper
required_identity=evidence_rag.rag.sources.code.diff_symbol_v2.DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2
identity_equal=false
method_monkeypatch_accepted=ready map_hunk c6-diff-symbol-mapper-v2
```

把 module 与 public export 同时替换为 `type("DiffSymbolMapper", ...)`，并伪造相同
`__module__`、`map_hunk` 与 mapper version，`production_component_contract()` 和
`require_production_component_identity()` 也均返回 `ready`。这使 fake/injected/
wrapped production mapper 能穿过 C-B6 资格边界；现有测试只拒绝普通不同 module 的
fake 与 subclass，没有覆盖同 module/name 或真实方法被替换。

最小关闭条件：冻结并校验完整
`diff_symbol_v2.DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2` identity，同时将
公开类与原始 `map_hunk` 实现绑定，补 method replacement 和同 module/name spoof
负向测试。

### 独立验证与边界

```text
tests/test_code_diff_symbol_v2.py + tests/test_code_cb6_run.py:
32 passed

tests/test_code_history_v2.py:
22 passed

ruff check（5 个目标文件）:
PASS

ruff format --check（5 个目标文件）:
5 files already formatted

git diff --check:
PASS
```

DiffHunk/SymbolVersion/Match/Edge/Diagnostic/Treatment/Trace frozen scope、old/new exact
version/path mapping、registered CONTAINS/AFFECTS、ambiguous candidate no edge、confirmed
C6-01 lineage 引用、fixture/labels/三臂同分母/非生产指标合同与 PREPARED 零产物路径未发现
其他 P0/P1。主控交接的定向 95、Code 660、全量 719 是上游独立证据；本 Gate 未重复
运行 660/719，也未执行 C-B6 production Run、生成 artifact/production metrics，未审
C6-03/runtime 接线。

审前与本裁决追加前证据：

```text
branch: main
HEAD: bc3326edc761e3bdb42ed78726a21f314ab44974
worktrees: 1
worktree: /Users/example/project/rag

formal DB:
path: var/evidence-rag.sqlite3
size: 1915490304
mtime_epoch: 1785136263
SHA-256: 9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b
sidecars: 0

evals/code/runs:
files: 120
content fingerprint:
67c2a8a9ea4adb6a74a81ea5ff233d7d5b9357cdac62f8f37e457bcb10c7579b
```

本轮未创建 branch/worktree，未 commit/push，未修改实现、测试、config/依赖、其他文档、
evaluation/evals/Run 或正式库。唯一仓库写入是向本 review 追加本裁决。

## C6-02 / C-B6 第二轮 exact identity 极简复审

独立 Gate 时间：2026-07-28 19:17 +0800

首轮唯一 P1 已关闭。本轮只复核 production identity fail-closed 与 C6-02/C-B6 主路，
未扩展 P2/P3。

### P1-01 关闭：完整 method@version identity 与原始实现绑定

`PRODUCTION_COMPONENT_IDENTITY` 现为：

```text
evidence_rag.rag.sources.code.diff_symbol_v2.DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2
```

CB6 import 时冻结原始 diff module、public module、exact class、exact `map_hunk`
function、接口类型、module/class version 与稳定实现 digest；每次 production observation
重新核对对象 identity、实现 digest、公开导出和双 version。instance 验证另行强制 exact
type、bound `__self__`、bound `__func__` 与实现 digest。

独立无文件写入篡改探针确认以下 11 类均抛出 `CB6Error`：

```text
真实 class.map_hunk 替换为 replacement/lambda
functools.wraps wrapper
原函数 __code__ 替换
module/public export 同时替换为同 module/name/version 伪造类
subclass instance
injected instance
wrong-module 同名类
instance-level map_hunk injection
wrong module version
wrong class version
sys.modules production module object replacement
```

每一项均在 `finally` 恢复；恢复后 exact production instance 重新 `ready`，bound
`__func__` 为冻结原方法，完整 identity 与 digest 不变。三个全新 Python 进程得到相同：

```text
identity:
evidence_rag.rag.sources.code.diff_symbol_v2.DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2

method implementation digest:
sha256:ed93080d100f35acefdaaa3c00bb769d180ae10a74ab9fed0df649e95ce5bc9c
```

`production_component_contract()` 与 `prepare_cb6()` 的
`production_component` snapshot 均携带该完整 identity、digest、identity policy 与
逐项 checks；首轮同 module/name spoof 与方法替换路径不可复现。

### 主路不回归与独立验证

报告仍为首轮 FAIL 时先运行只读 prepare：

```text
status=PREPARED NON-QUALIFIED
execution_status=production-execution-not-authorized
gate=not-authorized / failed / false
run_created=false
artifact_created=false
metrics_created=false
fixture_created=false
production_component=ready
existing_run_file_count=120
```

直接重复：

```text
tests/test_code_cb6_run.py
tests/test_code_diff_symbol_v2.py
tests/test_code_history_v2.py
57 passed

tests/test_code_history_refs.py
tests/test_code_parser_ir.py
tests/test_code_unit_builder.py
tests/test_code_graph_v2.py
27 passed, 1 third-party deprecation warning

direct total:
84 passed

ruff check（5 个 C6 目标文件）:
PASS

ruff format --check（修复的 2 个文件）:
2 files already formatted

目标文件 trailing whitespace:
0 findings
```

上述测试覆盖 old/new exact path/version mapping、typed CONTAINS/AFFECTS、wrong
scope/version fail-closed、ambiguous candidate no edge、confirmed lineage、CB6 canonical
labels、三臂同分母、portable/secret-free 合同与 C6-01 parser/builder/history/graph 兼容。
主控交接的定向 90、Code 651、全量 722、全 `src tests` Ruff、diff 与 only-main/
single-worktree 是上游独立证据；本轮没有把它们记作直接复跑。

### Git、正式库与 Run 追加前终核

```text
branch: main
HEAD: bc3326edc761e3bdb42ed78726a21f314ab44974
worktrees: 1
worktree: /Users/example/project/rag

formal DB:
path: var/evidence-rag.sqlite3
size: 1915490304
mtime_epoch: 1785136263
SHA-256: 9b1710e77ecff9b6c8b6ebd4a86cc6d8242900e6ae79f6bfbaf953c3c3b2546b
sidecars: 0

evals/code/runs:
files: 120
content fingerprint:
67c2a8a9ea4adb6a74a81ea5ff233d7d5b9357cdac62f8f37e457bcb10c7579b
```

本轮未创建 branch/worktree，未 commit/push，未修改实现、测试、其他文档、
evaluation/evals/Run 或正式库。唯一仓库写入是向本 review 追加本复审裁决。

```text
C6-02 engineering PASS
P0 findings: 0
P1 findings: 0
C-B6 PRODUCTION EXECUTION AUTHORIZED
C-B6 production component identity: evidence_rag.rag.sources.code.diff_symbol_v2.DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2
C6-03 AUTHORIZED
```

## C-B6 Production Run 最终只读审计

独立审计时间：2026-07-28 20:22 +0800

Run：
`evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e`

目录：`evals/code/runs/06eea9107f274fe3b13d77bd68b8a00e`

### 裁决

C-B6 artifact 自身 P0/P1 均为 0，工程与 artifact 完整性通过；真实质量结论必须保持
**PROVISIONAL NOT QUALIFIED**。该 Run 只审计独立 Diff-to-Symbol binding quality，
不替代 C-B0，也不取得 global baseline qualification。

### 只读 verifier、identity 与 frozen membership

直接调用 `verify_artifact()` 返回：

```text
status=verified
verification_mode=read-only
artifact_file_count=7
qualification_status=PROVISIONAL NOT QUALIFIED
treatment_qualified=false
```

Production identity：

```text
evidence_rag.rag.sources.code.diff_symbol_v2.DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2
```

实现 digest：

```text
sha256:ed93080d100f35acefdaaa3c00bb769d180ae10a74ab9fed0df649e95ce5bc9c
```

Fixture/label/denominator 直接重算均匹配 manifest、JSON、SQLite 与 frozen 常量：

```text
fixture digest:
sha256:d75b530049a249adf1fefb52bf21f3a84301301a8be9ceb0e6ca522d062d4518

label membership digest:
sha256:48d74df8b1914af770e343f0a604bdb71dd5bdded7101cb1c2d7cf07a27ee4cf

denominator membership digest:
sha256:609a11322a88a21b6db9b3c82ed2e908f537fed863997ab2017975fe8d6fb4da
```

唯一 C-B6 production Run；canonical membership 为 12 cases、8 labeled cases、16
canonical symbol labels。control、AST 与 merged 三臂均为相同 12 case id 与相同
parent/target commit membership。纯内存分别篡改 label note、fixture label 与
denominator parent SHA 后，三个重算 digest 均不再匹配；verifier 对这些字段及 frozen
digest 是 fail-closed。

### 三臂真实指标与假阳性

三臂 `line_overlap_legacy`、`ast_enclosing`、
`ast_rename_lineage_merged` 的真实质量值相同：

```text
overall precision=0.888888888889
overall recall=1.0
overall F1=0.941176470588
true positive=16
false positive=2
false negative=0

modified precision=0.857142857143
modified recall=1.0
modified F1=0.923076923077

false affected symbols=2/18
false affected rate=0.111111111111
```

两个假阳性没有被修饰或隐藏：`cb6-ambiguous` 在每一臂都被报告为 `available`，old/new
各确认一个 `src/ambiguous.py` file-top-level `FileVersion`，共两个 `<module>`
modified prediction；canonical ambiguous case 没有 sole-owner quality label。

### Qualification 与 C-B0

Frozen qualification checks：

```text
sample_size=false               8 < 30
precision=false                 0.888888888889 < 0.9
false_affected_rate=false       0.111111111111 > 0.1
recall=true
F1=true
change_context_recall=true
```

因此 `PROVISIONAL NOT QUALIFIED` 与 `treatment_qualified=false` 是真实结论；
C6-02 engineering PASS 不等于质量 qualification。

C-B0 仍是唯一 qualified global retrieval baseline：

```text
evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061
```

manifest 明确 `replaced_by_cb6=false`；C-B6 scope 为
`independent-diff-binding-quality-audit`。

### 七文件 artifact、isolated SQLite 与旧 Run 不变性

Artifact 恰含：

```text
evaluation.sqlite3
labels.json
manifest.json
metrics.json
performance.json
results.json
security.json
```

六个 JSON 均为 canonical JSON 并通过 portable/secret/absolute-path 检查；目录和文件
均无 symlink，且无 WAL/SHM/pyc。manifest 的逐文件 bytes/SHA-256 与
artifact-set hash 均通过只读 verifier。七文件整体：

```text
content fingerprint:
a3f518a0e320756943fd297233040778b00fe1717abeb03d9afca669993e5288

mode/size/mtime/path fingerprint:
47c9c9f7cf5f693fe41a2ae4651843d0c0d3449e8a8c114811889bd95ecdd259
```

`evaluation.sqlite3` 只以 `mode=ro&immutable=1` 打开：

```text
PRAGMA quick_check=ok
cb6_runs rows=1
cb6_case_results rows=36
cb6_arm_metrics rows=3
```

唯一 Run row 的 run id、完整 production identity、实现 digest、label digest、
qualification status 与 manifest 完全一致。

新 Run 之前的 120 文件均保留，且排除新目录后直接重算：

```text
old file count=120
new file count=7
current total=127

old content fingerprint:
67c2a8a9ea4adb6a74a81ea5ff233d7d5b9357cdac62f8f37e457bcb10c7579b

old mode/size/mtime/path fingerprint:
47a6a7a2d025983b82ccc0ff884e6b71ebe75f700383241b8eb60eca7331904d
```

两个旧指纹均精确匹配 C-B6 执行前已记录的 120 文件基线；本审计没有创建、修改或重跑
该 Run。

### 正式 DB 外部可变服务例外

正式库本轮明确分类为：

```text
EXTERNAL_MUTABLE_SERVICE_OWNED
```

用户交接说明 `evidence-rag serve --reload` 的 parent PID 4592 / child PID 4609 持有并
改变正式库。本审计 shell 的 `ps`/`lsof` 视图没有显示这两个 PID 或文件持有者；这不
推翻用户提供的外部状态，也不授权本审计触碰服务。

本轮只用文件元数据记录到的当前外部漂移为：

```text
var/evidence-rag.sqlite3
size=1915490304
mtime_epoch=1785240365
inode=129697235

var/evidence-rag.sqlite3-wal
size=127752
mtime_epoch=1785240456
inode=138505988

var/evidence-rag.sqlite3-shm
size=32768
mtime_epoch=1785240365
inode=138505989
```

相较此前稳定快照，正式 DB mtime 已改变且 WAL/SHM 已出现。本审计没有打开、哈希、
写入或 checkpoint 正式 DB，没有删除 sidecar，也不声称正式库不变；该漂移不归因于
C-B6，且不作为 isolated artifact Gate。全量测试中既有 CB3 formal-sidecar assertion
受运行中外部服务影响的失败属于上游证据，不是 C-B6 artifact P0/P1。

### 任务写锁与证据边界

本审计保持 `main`、HEAD
`bc3326edc761e3bdb42ed78726a21f314ab44974`、单 worktree；未创建 branch/worktree，
未 commit/push，未修改实现、测试、其他文档、evaluation、evals/Run、正式库或 Git。
唯一仓库写入是向本 review 追加本审计。主控的定向测试 PASS 与首次 verifier 结果是
上游独立证据；本节记录的 verifier、JSON/digest、isolated SQLite、metrics、Run
fingerprints 与服务元数据是本轮直接重复。

```text
C6-02 engineering PASS
P0 findings: 0
P1 findings: 0
C-B6 production audit VERIFIED — PROVISIONAL NOT QUALIFIED
C6-02 COMPLETE
C6-03 AUTHORIZED
```

## C6-03 Test Validation 独立精简 Gate

独立 Gate 时间：2026-07-28 20:52 +0800

### 裁决

```text
C6-03 engineering FAIL
P0 findings: 0
P1 findings: 1
C6 overall engineering INCOMPLETE
C7-01 NOT AUTHORIZED
```

### P1-01：reported/observed contradiction 仍会产生成功验证边

位置：

- `src/evidence_rag/rag/sources/code/test_validation_v2.py:371-389`
- `src/evidence_rag/rag/sources/code/test_validation_v2.py:1136-1145`
- `tests/test_code_test_validation_v2.py:181-348`

`TestResultV2.status_consistency` 会把 observed `passed` + `exit_code=0` 与不同
`reported_status` 正确标为 `reported_observed_contradiction`，并产生同名 diagnostic；
但 `_validation_relation()` 不检查该 consistency，只看 observed status/exit，仍返回
`VALIDATED_BY`。

纯内存最小复现：

```python
result = TestResultV2.create(
    target=target,
    command=("pytest", "-q"),
    environment_ref="environment:test",
    status=TestExecutionStatus.PASSED,
    observation=TestObservation.OBSERVED,
    framework="pytest",
    framework_parser_version="pytest-v2",
    evidence_locator="test-run://contradiction",
    exit_code=0,
    reported_status=TestExecutionStatus.FAILED,
)
treatment = TestValidationBinder().bind(result, current_target=target)
```

实际：

```text
status_consistency=reported_observed_contradiction
diagnostics=[reported_observed_contradiction]
treatment.status=validated
status_edges=[VALIDATED_BY]
```

将 `reported_status` 改为 `error` 或 `unknown` 结果相同。独立穷举 120 组
status/exit/observation/reported_status 组合：

```text
VALIDATED_BY edges=5
false validations=3

false:
observed passed / exit 0 / reported failed
observed passed / exit 0 / reported error
observed passed / exit 0 / reported unknown
```

影响：当前 exact target 会在输入已明确自相矛盾时得到成功 `VALIDATED_BY`，直接违反
“矛盾不能发成功验证”和 False Validation Rate 必须为 0 的 C6-03 主 truth table。
现有专项只覆盖 observed failed/exit 0 + reported passed 方向；该方向会走
`FAILED_VALIDATION`，没有覆盖 observed passed/exit 0 + reported non-passed。

最小关闭条件：成功 relation 除 observed `passed` + `exit_code=0` 外，还必须要求
`status_consistency == consistent`；为 reported failed/error/unknown 三种矛盾补负向
测试，保证均不产生 `VALIDATED_BY`。failed/error/nonzero 的
`FAILED_VALIDATION` 语义应保持不变。

### 直接验证与边界

所有 pytest 均设置 `PYTHONDONTWRITEBYTECODE=1`、`UV_NO_SYNC=1` 并禁用 pytest
cache；未运行会触发正式库 sidecar 稳定断言的测试。

```text
tests/test_code_test_validation_v2.py:
32 passed

tests/test_code_graph_v2.py:
8 passed

tests/test_code_history_v2.py:
22 passed

tests/test_code_diff_symbol_v2.py:
10 passed

direct total:
72 passed

ruff check（3 个目标文件）:
PASS

ruff format --check（3 个目标文件）:
3 files already formatted

in-memory compile（3 个目标文件）:
PASS

module/package exact lazy exports、__all__ uniqueness:
PASS

目标文件 trailing whitespace:
0 findings
```

专项与源码直接支持 frozen/versioned contracts、exact target/scope/dirty manifest
fail-closed、registered `CONTAINS` role=`has_test_result`、failure truth table、coverage/
selector confirmed、SCIP/import/call/path-name candidate-only、historical isolation、
MISSING_CONTEXT、稳定排序/idempotence 与无 runtime/store persistence；这些正向证据不
抵消上述 False Validation P1。实施与主控的五文件 95、Ruff/format/diff/compile 是
上游独立证据，本 Gate 没有把它们记为本轮直接复跑。

C-B6 继续保持 `VERIFIED — PROVISIONAL NOT QUALIFIED`；C-B0
`evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
仍是唯一 qualified global baseline。

正式库及 sidecars 本轮始终按
`EXTERNAL_MUTABLE_SERVICE_OWNED` 处理。本 Gate 没有打开、哈希、写入、
checkpoint 或删除 `/Users/example/project/rag/var/evidence-rag.sqlite3`、WAL 或 SHM；
所有数据验证均为纯内存/tmp，正式库外部漂移不归因于本 Gate。

本轮保持 `main`、HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`、单
worktree；没有创建 branch/worktree，没有 commit/push，没有修改实现、测试、其他
文档、evaluation/evals/Run/fixtures 或 Git。唯一仓库写入是向本 review 追加本裁决。

## C6-03 Test Validation 第二轮极简复审

独立 Gate 时间：2026-07-28 21:13 +0800

### 裁决与首轮 P1 关闭

首轮唯一 P1 已关闭。`_validation_relation()` 现先要求
`status_consistency == consistent`；非一致、缺失 exit code 或 reported-only 结果不会
产生 `VALIDATED_BY` 或 `FAILED_VALIDATION`，但 exact target 下仍保留注册的
`CONTAINS`、诊断与观察事实。

本轮没有复用实现或测试中的期望值 helper，而以独立 fail-closed oracle 对
observation × status × exit code × reported status 的全部 120 组组合做纯内存穷举：

```text
VALIDATED_BY: 2
FAILED_VALIDATION: 6
OBSERVATION_ONLY: 112
False Validation: 0
```

首轮三个反例逐一重放：

```text
observed passed / exit 0 / reported failed:
reported_observed_contradiction, observation_only, edges=[CONTAINS]

observed passed / exit 0 / reported error:
reported_observed_contradiction, observation_only, edges=[CONTAINS]

observed passed / exit 0 / reported unknown:
reported_observed_contradiction, observation_only, edges=[CONTAINS]
```

三例均只产生 `REPORTED_OBSERVED_CONTRADICTION` diagnostic；没有成功或失败验证边。

### 主路回归证据

以下五文件由本 Gate 直接复跑，禁用 pytest cache、禁止 bytecode、`UV_NO_SYNC=1`：

```text
tests/test_code_test_validation_v2.py: 145
tests/test_code_graph_v2.py: 8
tests/test_code_history_v2.py: 22
tests/test_code_diff_symbol_v2.py: 10
tests/test_code_query_profile_v2.py: 23
direct total: 208 passed
```

专项与源码检查共同确认：

- project/repository/ACL、generation、commit/worktree stable version 与 dirty manifest
  继续 exact fail-closed；historical 结果、错 version/scope/evidence 不物化验证或符号边。
- coverage function/line 与 exact selector/target 仍可 confirmed 并物化注册
  `COVERS`/`TESTS`；SCIP/import/call/path-name heuristic 仍仅 candidate +
  review-required，不物化弱证据边。
- 事实归属仍是注册 `CONTAINS` + role=`has_test_result`；全部输出边通过 C4 registry
  endpoint/derivation 校验，未出现自造 `HAS_TEST_RESULT` relation。
- no-result 仍显式 `MISSING_CONTEXT`；输出仍具稳定排序、幂等 canonical bytes/hash 与
  exact locator/provenance。

静态复验：

```text
ruff check（3 个目标文件）: PASS
ruff format --check（3 个目标文件）: 3 files already formatted
in-memory compile（3 个目标文件）: PASS
trailing whitespace: 0 findings
```

实施与主控报告的 208 PASS、Ruff/format/diff/compile 属于上游独立证据；上述
truth probe、三个冲突重放、五文件 208、静态检查与源码审查是本轮直接证据。

### 写锁、Run 与正式 DB 边界

测试前后三个只读目标 SHA-256 保持：

```text
test_validation_v2.py
c45adcda521369d480cf1d5ef4af784bd36de6972ca9f5774ab6cbf1581b4dc8

code/__init__.py
90aeb947dd103c980334c69f8c9120f3732e47476b364ac350f44f2965178233

test_code_test_validation_v2.py
0c9bd4c57cc3adb8bfffaa7f2c51a8c68a117bcec8039ec2218861aa55191e3d
```

Run 树仍为旧 120 文件加 C-B6 的 7 文件；只读 content fingerprints 精确保持：

```text
C-B6 7 files:
a3f518a0e320756943fd297233040778b00fe1717abeb03d9afca669993e5288

pre-C-B6 120 files:
67c2a8a9ea4adb6a74a81ea5ff233d7d5b9357cdac62f8f37e457bcb10c7579b
```

C-B6 继续为 `VERIFIED — PROVISIONAL NOT QUALIFIED`；C-B0
`evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`
仍是唯一 qualified global baseline。

正式库及 sidecars 全程保持 `EXTERNAL_MUTABLE_SERVICE_OWNED`。本轮没有打开、哈希、
写入、checkpoint 或删除 `/Users/example/project/rag/var/evidence-rag.sqlite3`、WAL
或 SHM；所有数据验证均为纯内存，外部服务导致的正式库漂移不归因于本 Gate。

本轮保持 `main`、HEAD `bc3326edc761e3bdb42ed78726a21f314ab44974`、单
worktree；没有创建 branch/worktree，没有 commit/push，没有修改实现、测试、其他
文档、evaluation/evals/Run/fixtures、正式库或 Git。唯一仓库写入是向本 review
追加本复审裁决。未审查或授权 runtime/store persistence、C7 实现或正式 DB 接线。

```text
C6-03 engineering PASS
P0 findings: 0
P1 findings: 0
C6 overall engineering COMPLETE
C7-01 AUTHORIZED
```
