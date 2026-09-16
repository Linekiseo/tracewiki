# Code Source C0 Gate Review

复审轮次：第五轮 C0-03 / C0 最终门禁复审（取代本文此前全部快照）

复审日期：2026-07-27

共享工作目录：`/Users/example/project/rag`

审查分支 / HEAD：`main` /
`bc3326edc761e3bdb42ed78726a21f314ab44974`

唯一有效 Baseline Run：
`evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`

## 1. 最终结论

| Gate | 结论 | 直接证据 | 授权 |
| --- | --- | --- | --- |
| C0-01 Evaluation V2 | **PASS** | 前轮 P0-01～P0-06 保持 CLOSED；本轮专项与全量回归通过 | 保持通过 |
| C0-02 Code Golden v2 | **PASS** | released package、50/33/17、三源、release/provenance、无 Unit treatment 均复验通过 | 保持通过 |
| C0-03 V1 graph-off baseline | **PASS** | P0-C0-03-09、P0-C0-03-10 均 CLOSED；新 Run 的 qualification、内容重建、安全扫描、不可变性和报告全部通过 | 通过 |
| C0 total | **PASS** | C0-01、C0-02、C0-03 全部通过 | **允许进入 C1** |
| C1 | **AUTHORIZED** | Evaluation-first 的 Golden → baseline 前置已具备 | 仅授权开始 C1，不代表 C1 RELEASED |

最终状态：

```text
C0-01   PASS
C0-02   PASS
C0-03   PASS
C0      PASS
C1      AUTHORIZED
```

本报告给出技术 Gate 结论；主控仍负责最终人工验收和后续文档同步。

## 2. 第五轮证据分类

| 断言 | 证据分类 | 结论 |
| --- | --- | --- |
| `5a92...` 是唯一 latest qualified Run | 直接支持 | qualification ledger 独立复算通过 |
| qualification record / manifest canonical hash 为指定值 | 直接支持 | canonical JSON 独立复算一致 |
| `3c63...`、`24fd...` 已 revoked | 直接支持 | latest ledger decision 与 CLI fail-closed 一致 |
| `d16...` 无 qualification | 直接支持 | ledger 无该 Run，CLI fail-closed |
| manifest 可从 package/SQLite/reports/runner contract 重建 | 直接支持 | 未篡改内容 exact match；篡改负向测试全部拒绝 |
| 最终 SQLite/JSON 无临时路径与真实 credential assignment | 直接支持 | 独立全字段扫描和内置 scanner 均为 0 |
| 所有 unavailable 语义合法 | 直接支持 | SQLite 与递归 JSON 检查均为 0 违规 |
| 正式库未被 baseline 污染 | 直接支持 | 6 个 evaluation 表均为 0 |
| 主控声称全量 `130 passed` | 版本错配 / 不可复现 | 当前 collection 和两次全量执行均为 129；无失败、skip 或 C0 专项缺失 |

没有 OPEN P0 或 P1。

## 3. P0-C0-03-09 — CLOSED

### 3.1 固定 genesis、线性 content-addressed ledger

固定 genesis：

```text
sha256:0000000000000000000000000000000000000000000000000000000000000000
```

独立使用 canonical JSON（UTF-8、sort keys、紧凑 separators、排除
`record_hash`）复算四条 record：

| Seq | Record hash | Run | Decision | Previous |
| ---: | --- | --- | --- | --- |
| 1 | `sha256:491dd04a9b93cc1012ba4c61eb37730df76ecce1ccbd000f95dadbddaa335160` | `3c63...` | revoked | genesis |
| 2 | `sha256:b56e763e7886db0e6153073409539ee743ed60eb4f8bf52f16f24cb4a7620b02` | `24fd...` | qualified | seq 1 |
| 3 | `sha256:8f0f1459e63d25c9a9b36c0c7e82115182ba86fbe8ec768d58858a7372c0d75e` | `24fd...` | revoked | seq 2 |
| 4 | `sha256:9ba8081fa67285e5e9d418703b53d1ffc42562f33e24de2028afeaaa223a034f` | `5a92...` | qualified | seq 3 |

每个文件名均等于其 canonical record hash；sequence 精确为 1～4，且每条
`previous_record_hash` 精确指向前一条。latest decision：

```text
3c63... = revoked
24fd... = revoked
5a92... = qualified
```

因此只有一个 latest qualified Run：

```text
evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061
```

### 3.2 新 Run 的三个锚

| 锚 | 独立复算值 |
| --- | --- |
| qualification record | `sha256:9ba8081fa67285e5e9d418703b53d1ffc42562f33e24de2028afeaaa223a034f` |
| manifest canonical hash | `sha256:6ef5a2f1b3c82ed9007da41fd17d07ad8fd64fd0f12532b2751dc0dd510035ca` |
| artifact-map hash | `sha256:9d9f3bc86a2e9e0419db54a85b89156c7593596a1903a940e2e37f8c1e33ab44` |
| SQLite SHA-256 | `sha256:8d5b9ea0c09bbf33874a55b584d3a0e2aa9f8174eb2d04a3f1a792b44008b8c4` |

qualification record 中的 manifest/artifact/package/config 锚均与独立复算一致。
Manifest 列出的 9 个文件全部通过 bytes 与 SHA-256 精确比对。

### 3.3 旧 Run fail-closed 且主文件未原地修改

官方 CLI 实测：

```text
verify 3c63... → exit 1
artifact qualification was revoked:
p0-c0-03-09-manifest-unanchored-and-p0-c0-03-10-temp-path-leak

verify d16... → exit 1
artifact has no controlled qualification record

verify 24fd... → exit 1
artifact qualification was revoked:
superseded-before-gate-due-to-unmapped-sqlite-wal-sidecars
```

旧 artifact 独立摘要检查：

- `3c63...`：manifest 列出的 9/9 文件仍匹配；revoke record hash 精确为
  `sha256:491dd04a...`；
- `24fd...`：manifest 列出的 9/9 文件仍匹配；先 qualified 后 revoked，
  latest 为 revoked；
- `d16...`：SQLite 与 7 个报告仍匹配历史 manifest；`attempt-audit.json`
  的历史 invalidation 改写仍按既有设计保留，且该 Run 从未有 qualification。

撤销通过独立 ledger record 表达，没有重写旧 terminal SQLite 或报告。

旧 revoked/unqualified 目录仍保留其历史 WAL/SHM sidecar；这是审计保留状态，
不能冒充最终 artifact。唯一有效的 `5a92...` 目录没有
`-wal`、`-shm` 或 `-journal` sidecar。

### 3.4 Manifest 重建

Verifier 与本轮独立审查均从以下可信输入重建 manifest：

- released Code Golden v2 package；
- 内嵌 SQLite 的唯一 completed Run；
- 精确 50-case Golden payload、33 个 result membership；
- Run request/config/result trace；
- 实际 repositories、active generations、parser/model counts；
- 可从 SQLite/package 重建的 7 个 JSON report；
- 固定 runner/security contract；
- artifact 文件的实际 bytes/SHA。

重建并比较的关键字段包括：

- dataset identity、`50/33/17`、released/evaluated membership hashes；
- Run ID/display key/started/completed/project/runner；
- repositories、generations、retriever observation；
- baseline config 与 config fingerprint；
- before/after workspace fingerprints；
- security declaration；
- 全 artifact map 与联合 hash。

未篡改 manifest 与重建值 exact equal。

### 3.5 Workspace fingerprint

写本报告前的证据冻结点：

```text
before == after == current
```

具体值：

| 字段 | 值 |
| --- | --- |
| HEAD | `bc3326edc761e3bdb42ed78726a21f314ab44974` |
| tree | `2fe240ebdf1f3d04c252c32b505ca923394171da` |
| implementation input | `sha256:b69edf8fbb496e93a0f1b03b7cc964e0221585c5bbdcff865aef783c1c9ef376` |
| workspace state | `sha256:48d26e20e3b00590582f20e4c27da5e3cdeb95cdd2707faa1439b7990fba7024` |
| workspace status | `sha256:c0867ba9d0fd07a3e63292d9eae76dbb7b01a66179b3ce595a01f6e2edc71d37` |
| worktree list | `sha256:f752ead7f0430a859108ac8788410a4332b4c14d2d23c14107566b552924688e` |
| package | `sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61` |

本报告是唯一获授权的 post-freeze 写入，因此写入后完整
`workspace_state_hash` 会因本报告自身内容变化而变化；这不改变
implementation input、package、Run 或 artifact。不得把这项预期的审查报告写入
误判成 baseline 期间漂移。

### 3.6 逐字段与联合篡改

在 `TemporaryDirectory` 副本中分别篡改：

- total/eligible/ineligible counts；
- released/evaluated membership hashes；
- repositories/generations/retriever；
- before/after workspace state；
- time；
- security。

12/12 单字段篡改均被 qualification anchor 拒绝：

```text
manifest is not anchored by its qualification record
```

同时修改 `metrics.json`、manifest dataset、artifact bytes/SHA map 和
artifact-set hash 的联合篡改也被同一 anchor 拒绝。

为排除“只靠 ledger”的假阳性，本轮直接调用内容重建层：

```text
field tamper → manifest cannot be reconstructed ...
joint tamper → manifest cannot be reconstructed ...
```

因此 P0-C0-03-09 的逐字段、联合篡改、外部锚、内容重建与防回归条件全部满足。

## 4. P0-C0-03-10 — CLOSED

### 4.1 最终 SQLite 全 TEXT 扫描

独立枚举每个非 SQLite 内部表的全部 declared TEXT 列：

| 扫描项 | 数量 |
| --- | ---: |
| TEXT columns | 804 |
| non-NULL TEXT values | 27,835 |
| `/tmp` / `/private/tmp` / `/var/folders` findings | 0 |
| high-confidence credential assignment findings | 0 |

内置 verifier 返回相同结果：

```text
clean=true
temporary_path_findings=0
credential_assignment_findings=0
```

### 4.2 Artifact 与 ledger JSON

独立递归扫描 `evals/code/runs` 中所有 artifact/qualification JSON：

| 扫描项 | 数量 |
| --- | ---: |
| JSON files | 40 |
| absolute temporary path findings | 0 |
| credential assignment findings | 0 |

实现内置 scanner 对最终 artifact 的 9 个 JSON 也为 0/0。

源码中的 `password`、`request.password` 等标识符不会被误报为真实 credential；
使用 `"actual-secret-value"` 的赋值负向 fixture 会被正确检出。

### 4.3 Sanitization provenance

最终 SQLite 中一条历史 diff 使用稳定占位符：

```text
<redacted-temp-path>
```

独立复算确认：

- `diff_hunks.patch_hash` 等于 sanitized patch SHA-256；
- 对应 `raw_objects.content_hash` 相等；
- `raw_objects.byte_length` 相等；
- `storage_path` 为 `<artifact-raw>/<hash>`；
- metadata 保存
  `code-c0-03-stable-placeholder-v1 / payload_hash_recomputed=true`。

先前另一个 `/private/tmp` 历史片段不在最终 current-snapshot history-depth
范围内。没有通过篡改 hash 或丢弃 provenance 来伪造扫描通过。

### 4.4 Sidecar

最终有效目录：

```text
evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061
```

不存在 SQLite `-wal`、`-shm`、`-journal`。Verifier 复验后仍为 0。

因此 P0-C0-03-10 的全 TEXT/JSON 扫描、credential、hash provenance 和最终
sidecar 条件全部满足。

## 5. Golden、loader 与内嵌 SQLite

### 5.1 Released anchors

| 字段 | 值 |
| --- | --- |
| dataset | `code-golden` |
| version | `code-golden-v2` |
| release record | `code-golden-v2-release-001` |
| package | `sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61` |
| released eligible membership | `sha256:a9551e3b77822fd67f11c32dffa04e6b0069cd88e44adbfabfb6cd12bd343aaa` |
| evaluated membership | `sha256:91a5ab7c4d61fbb4d6b587d77e06a264ca38372745897663fd2fee7e3c1d5f74` |
| total / eligible / ineligible / smoke | `50 / 33 / 17 / 15` |

`validate` 成功；`prepare` 成功并观测三个 completed repository、精确
`file.raw/symbol.raw` 与 `local-hash-v2`。

独立 loader 使用临时 SQLite：

```text
total=50
enabled=33
disabled=17
eligible_ids=33
second load=rejected (immutable single-use)
```

### 5.2 SQLite integrity/membership

| 表 | 行数 |
| --- | ---: |
| `evaluation_cases` | 50 |
| `evaluation_case_profiles` | 50 |
| `evaluation_candidate_judgments` | 95 |
| `evaluation_runs` | 1 |
| `evaluation_results` | 33 |
| `evaluation_metric_values` | 2,240 |
| `repositories` / `index_generations` | 3 / 3 |
| `search_views` | 86 |

`PRAGMA quick_check=ok`，foreign-key failures 为 0。50 个 case/profile、
95 个 judgment 与 released package 逐字段一致；33 个 result IDs 精确等于
enabled membership，无重复或越界。

8 个 terminal trigger 存在，Run/result/metric 的 update/delete/insert 探针均被
拒绝。Run 为唯一 completed graph-off Run。

### 5.3 三源与 runner contract

固定配置：

```text
retriever=HybridRetriever
fusion=weighted-hybrid-v2
embedding=local-hash-v2
views=file.raw,symbol.raw
graph_candidate=false
graph_post_expand=record_separately
top_k=20
```

| Source | Ref | File / Symbol / View / Edge |
| --- | --- | ---: |
| controlled multilingual | `85e41dc96462ddfa897483d17388d2be5f9d5fff` | `8 / 15 / 23 / 33` |
| current project snapshot | `bc3326edc761e3bdb42ed78726a21f314ab44974` | `3 / 53 / 56 / 145` |
| historical error | `6bb97313737dbb53ac508dba84f01082dda7fab4+dirty.ce8366654809` | `4 / 3 / 7 / 11` |

三个 generation 均记录：

```text
parser=tree-sitter-language-pack-1.13
embedding=local-hash-v2
schema=code-evidence-v1
```

Run request、result traces、active generation IDs、repository rows与 manifest
互相重建一致；ACL 强制开启，validation observations 与 expected status/exit
一致。

## 6. Unavailable、分母与正确零结果

### 6.1 指标语义

| 检查 | 结果 |
| --- | ---: |
| available metrics | 1,511 |
| unavailable metrics | 729 |
| unavailable with non-NULL value | 0 |
| unavailable with value=1.0 | 0 |
| unavailable without reason | 0 |
| available with NULL value | 0 |
| available ratio mismatch | 0 |

递归扫描全部最终 artifact JSON：

```text
unavailable records=1,207
non-NULL=0
value 1.0=0
missing reason=0
```

Unit treatment 仍不存在：

- 95 个 judgment 全部为 entity；
- required unit denominator 合计 0；
- Unit Recall/duplicate 均 unavailable。

Enabled judgment grade 分布：

```text
required(2)=34
helpful(1)=8
neutral(0)=6
harmful(-1)=17
```

Required recall 使用 expected entities、grade-2 与折叠后的 alternative groups，
33 条 case 的 required entity denominator 合计 44；grade-1 helpful 只进入
graded ranking，不进入 required recall denominator。

### 6.2 正确零结果

| Case | Expected answer mode | Candidates | Result |
| --- | --- | ---: | --- |
| `014` | refuse | 0 | passed |
| `027` | missing_evidence | 0 | passed |
| `035` | refuse | 0 | passed |

Run summary 的 `3/33=0.090909` 是 case acceptance pass rate，不是 retrieval
quality。本报告没有把它冒充为 Recall/MRR/nDCG。

### 6.3 17 条 unavailable treatment 边界

| 边界 | Case IDs | 数量 |
| --- | --- | ---: |
| historical non-active CodeSymbol / tag resolution | `020, 036, 037, 038, 040, 041, 042, 043, 044, 045` | 10 |
| stable validation artifact / TestResult retrieval | `024, 039, 046, 047, 048, 049, 050` | 7 |

额外 eligible capability boundary：

- versioned Retrieval Unit identity：33 unavailable；
- paired graph-only recovery：29 positive unavailable、4 not applicable；
- dense full-scan timing：unavailable；
- exact commit：24 available、5 unavailable；
- missing/wrong version：30 available、3 unavailable。

## 7. Baseline 数字、latency、storage 与 errors

| 指标 | numerator / denominator | value |
| --- | ---: | ---: |
| Entity Recall@1 | `12 / 44` | `0.272727` |
| Entity Recall@5 | `33 / 44` | `0.750000` |
| Entity Recall@10 | `37 / 44` | `0.840909` |
| Entity Recall@20 | `38 / 44` | `0.863636` |
| MRR@10 | `18.333333 / 29` | `0.632184` |
| nDCG@10 | `17.539576 / 29` | `0.604813` |
| Symbol Recall@10 | `37 / 44` | `0.840909` |
| Required Path Recall | `13 / 14` | `0.928571` |
| Exact Commit Accuracy | `32 / 38` | `0.842105` |
| Dirty Snapshot Accuracy | `5 / 6` | `0.833333` |
| Missing / Wrong Version Rate | `0 / 224` | `0.0 / 0.0` |
| Hard Negative Error@10 | `6 / 17` | `0.352941` |
| Harmful Candidate Rate@10 | `7 / 92` | `0.076087` |
| Unauthorized Leakage Rate@10 | `0 / 224` | `0.0` |
| Returned Locator Validity@10 | `224 / 224` | `1.0` |
| Required Context Coverage@10 | `0 / 43` | `0.0` |
| Graph channel-exclusive relevant@10 | `0 / 52` | `0.0` |
| Candidate count | `303 / 33` | `9.181818` |

Latency：

```text
mean=216.51/33=6.560909 ms
P50=5.35 ms
P95=15.04 ms
dense full-scan=unavailable (未单独暴露计时)
```

Storage：

```text
SQLite=4,956,160 bytes
page size/count/free=4,096 / 1,210 / 0
active index logical=313,932 bytes
active index physical=565,248 bytes
file.raw=15 rows / 60,213 content bytes / 23,040 vector bytes
symbol.raw=71 rows / 121,623 content bytes / 109,056 vector bytes
```

Error analysis：

| 分类 | 数量 |
| --- | ---: |
| top misses | 20 |
| same-name errors | 4 |
| wrong/missing version | 3 |
| graph-required misses | 12 |
| harmful cases | 6 |
| duplicate candidates | 0 |
| correct zero results | 3 |
| candidate channels | 5 |

## 8. 正式库隔离

只读检查 `var/evidence-rag.sqlite3`：

```text
evaluation_cases=0
evaluation_case_profiles=0
evaluation_candidate_judgments=0
evaluation_runs=0
evaluation_results=0
evaluation_metric_values=0
```

Baseline 仅使用 artifact 内嵌 isolated SQLite，未污染正式 evidence store。

## 9. 本轮命令与工程验收

执行：

```bash
cd /Users/example/project/rag

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  -m evidence_rag.evaluation.baseline validate
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  -m evidence_rag.evaluation.baseline prepare
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  -m evidence_rag.evaluation.baseline verify \
  5a92eafdff5d49e6aae8bb55fdc14061

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  -m evidence_rag.evaluation.baseline verify \
  3c63ce5394b643419bab7784c9c6c1b6
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  -m evidence_rag.evaluation.baseline verify \
  d16b4602efed4458b06ac99c5de4bcd3
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  -m evidence_rag.evaluation.baseline verify \
  24fdc3e12de74c4198627b3df40f86f1

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  tests/test_code_baseline.py \
  tests/test_code_evaluation_v2.py \
  tests/test_code_golden_dataset.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider
.venv/bin/ruff check --no-cache src tests
.venv/bin/ruff format --check \
  src/evidence_rag/evaluation/baseline.py \
  src/evidence_rag/evaluation/code.py \
  src/evidence_rag/evaluation/golden.py \
  src/evidence_rag/evaluation/models.py \
  src/evidence_rag/evaluation/schema.py \
  src/evidence_rag/evaluation/service.py \
  src/evidence_rag/evaluation/store.py \
  evals/code/code_golden_v2.py \
  tests/fixtures/code_golden/v2/materializer.py \
  tests/test_code_baseline.py \
  tests/test_code_evaluation_v2.py \
  tests/test_code_golden_dataset.py
git diff --check
git worktree list --porcelain
```

结果：

| 检查 | 本轮独立结果 |
| --- | --- |
| Golden validate | PASS，50/33/17、15 smoke、package/membership hash |
| prepare | PASS，isolated DB、三源 completed、views/model 精确 |
| loader | PASS，50/33/17；第二次 load fail-closed |
| new Run verify | PASS，指定 qualification/manifest/artifact anchors |
| old `3c63...` | 预期 revoked，exit 1 |
| old `d16...` | 预期 no qualification，exit 1 |
| old `24fd...` | 预期 revoked，exit 1 |
| P0-09 negative tests | PASS，12 单字段 + 1 联合 + 内容层均拒绝 |
| P0-10 scanner | PASS，SQLite 0/0、40 JSON 0/0、最终 sidecar 0 |
| C0 专项 | **55 passed**，1 个第三方 warning |
| pytest collection | **129 tests** |
| 全量 pytest | **129 passed**，1 个第三方 warning；两次执行一致 |
| ruff | **All checks passed** |
| C0 相关 format | **12 files already formatted** |
| `git diff --check` | PASS |
| worktree | PASS，仅 shared `main` |

主控给出的 `130 passed` 在本轮最终 main 上不可复现；collection 明确枚举 129，
两次全量均 129。由于所有 collected tests 通过、C0 专项 55 完整，且没有 skip/
failure，这一计数漂移为非阻断 P2，不得虚构为 130。

全仓 format 未作为 Gate 条件；本轮只要求并通过 12 个 C0/Golden 相关文件。

## 10. 并发、写锁与残余事项

- `git worktree list --porcelain` 只有
  `/Users/example/project/rag` 的 `main`；
- shared main 仍有大量既有 dirty/untracked 工作成果；Run 已用 before/after/current
  fingerprint 与 implementation input hash 冻结；
- 本轮没有创建 branch/worktree、子智能体/其他会话，没有 commit/push；
- 本轮没有修改实现、测试、Golden、artifact、ledger 或面试文档；
- 唯一写入是本 Gate 报告。

非阻断 P2：

1. 主控的 `130 passed` 与当前可收集的 129 不一致，应以后者为真实证据；
2. development/interview 仍可能保留旧 baseline 状态。本轮按写锁不修改；主控可在
   Gate 验收后同步唯一有效 Run ID。

## 11. Gate 授权

P0-C0-03-09：**CLOSED**

P0-C0-03-10：**CLOSED**

没有仍有直接最终证据支持的 P0/P1 阻断。

```text
C0-03: PASS
C0 total: PASS
C1: AUTHORIZED
```

允许主控进入 C1。该授权只表示 C0 Evaluation-first 前置完成，不表示 C1 或后续
C2/C3/C4 已实现、已通过 treatment Gate 或已发布。
