# Code Graph RAG 面试与简历材料

对应设计：[01_CODE_SOURCE_RAG.md](../sources/01_CODE_SOURCE_RAG.md)  
对应开发方案：[01_CODE_SOURCE_DEVELOPMENT_PLAN.md](../development/01_CODE_SOURCE_DEVELOPMENT_PLAN.md)

C0 最终证据：[01_CODE_C0_GATE_REVIEW.md](../development/reviews/01_CODE_C0_GATE_REVIEW.md)  
C1 最终证据：[02_CODE_C1_GATE_REVIEW.md](../development/reviews/02_CODE_C1_GATE_REVIEW.md)  
C2 工程 Gate：[03_CODE_C2_FOUNDATION_GATE_REVIEW.md](../development/reviews/03_CODE_C2_FOUNDATION_GATE_REVIEW.md)  
C3 最终证据：[04_CODE_C3_GATE_REVIEW.md](../development/reviews/04_CODE_C3_GATE_REVIEW.md)  
C4 overall engineering 与 C-B3 最终证据：
[05_CODE_C4_GATE_REVIEW.md](../development/reviews/05_CODE_C4_GATE_REVIEW.md)  
C5 overall engineering 与 C-B4 最终证据：
[06_CODE_C5_GATE_REVIEW.md](../development/reviews/06_CODE_C5_GATE_REVIEW.md)
C6-01/C6-02/C6-03、C6 overall engineering 与 C-B6 最终证据：
[07_CODE_C6_GATE_REVIEW.md](../development/reviews/07_CODE_C6_GATE_REVIEW.md)
C7-01/C7-02/C7-03 与 C7 overall engineering 最终证据：
[08_CODE_C7_GATE_REVIEW.md](../development/reviews/08_CODE_C7_GATE_REVIEW.md)

## 1. 当前状态声明

### CURRENT_MEASURED

- 下列实体、View 与边计数是 C6 之前记录的正式库快照，不是外部服务可变正式库的实时
  不变性断言；
- 快照活跃代码实体：1,406 CodeSymbol、192 FileVersion；
- 快照 Search View：1,598；
- 快照代码边：4,712；
- 已发布 CALLS 2,052，未解析约 6,273；
- 已发布 REFERENCES 815，未解析约 15,845；
- 当前向量：`local-hash-v2`；
- 默认用户路径的 Graph 主要在 Top-K 后扩展；C-B3 仅在隔离 treatment 中让 typed
  Graph 进入 candidate generation；
- C5 SCIP/semantic edge 已有隔离 engineering 与 C-B4 production audit 证据，但未接管
  默认用户路径，也未实际下载、联网或执行外部 indexer；
- 正式库当前分类为 `EXTERNAL_MUTABLE_SERVICE_OWNED`，由外部用户服务拥有并可变；
  C6 Gate 使用 memory/系统临时目录 SQLite，C-B6 使用 isolated artifact SQLite 与
  immutable artifact 验收。不得把历史快照写成当前 hash/mtime 不变，外部服务漂移也
  不归因于 C-B6。

### CURRENT_IMPLEMENTED_C0

- C0-01 Evaluation V2：**PASS**；
- C0-02 released Code Golden v2：**PASS**，50 case / 33 eligible / 17
  machine-ineligible，15 smoke，覆盖三类真实物化来源；
- C0-03 V1 graph-off baseline：**PASS**；
- 唯一有效 Run：
  `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061`；
- Golden v2 package：
  `sha256:8b0283edc4ef81bd99cb6dad26326b42b49963c8fd3fa3c44b7faeaac25c0a61`；
- C0 最终 Gate 当时的工程验收：C0 专项 55 passed；全量 collection 129 tests、两次全量
  129 passed。旧 `130 passed` 口径不可复现，不能引用。

### CURRENT_IMPLEMENTED_C1

- C1-01 Code Source Contract：**PASS**；
- C1-02 V1 Adapter / Shadow：**PASS**；
- C1 overall：**PASS**；其最终 Gate 当时授权 C2 开始，后续 C2 实施状态见下一节；
- 真实 legacy execution evidence 在最终 top-k 截断前记录 lexical/dense 的
  unique-entity raw rank 与 `hit_count`；有命中但全被剪枝时输出 `complete_pruned`；
- V1 adapter 验证版本、repository/project/commit/generation/locator、requested/effective
  ACL 与执行证明；V1 默认路径和 endpoint contract 保持兼容；
- `rag_code_shadow=false` 时零 submit、零 adapt、零额外 retrieval；
- flag-on shadow 采用 bounded async AES-GCM 密封 snapshot、bounded inflight/telemetry，
  telemetry 只保留固定枚举和 keyed hash，失败或超时不改变 V1；
- 新增依赖 `cryptography>=45,<46`，lockfile 固定 `cryptography 45.0.7`。

最终 C1 Gate 证据：独立 identity/snapshot/nonce 短 probe **PASS**；定向 adversarial
**27 passed**；C1 三文件专项 **209 passed**；主控全量 **338 passed**；Golden
**18 passed**；ruff、format、diff、147 个 `src/tests` Python source 只读内存 compile、
`uv lock`、正式库 hash/quick_check/schema 与 only-main 均 **PASS**；Golden fixture
`.pyc` 为 **0**，正式库未写入。

### CURRENT_IMPLEMENTED_C2

- C2-01 Schema / Store、C2-02 Parser IR、C2-03 AST Unit Builder、C2-04 opt-in
  Dual-write、C2-05 Exact + Fielded Sparse：工程 Gate 均为 **PASS**；
- symbol entity projection 与 entity-aware diversification 的两个真实生产主流程修复也已
  通过 Gate；
- `rag_code_engine=v1`、`rag_code_unit_builder=raw-v1` 仍为默认；只有显式
  `ast-v2` 才启用 C2 dual-write，V1 endpoint/adapter/shadow 兼容不变；
- Foundation 关键证据包括 Parser IR 10 项 + 旧 parser 5 项、Store 11 项、Unit Builder
  6 项；dual-write 5 个临时 SQLite 关闭场景；Exact/Sparse 17 项专项；projection
  26 项回归 + 11 个语义 case；diversification 17 项专项 + 独立 probe；
- 工程探针均使用临时 SQLite；三次真实 C-B1 各自使用隔离 artifact SQLite，正式库未写入。

`CURRENT_IMPLEMENTED_C2` 只表示工程能力与安全/兼容主流程已通过，不表示质量
treatment 已通过。三次真实 C-B1 都是 production、同 33 eligible 分母、artifact
verified，但均为 `treatment_qualified=false`：

| Run | 修复阶段 | Overall entity recall@10 | Locator recall@10 | Entity duplicate@10 | 最终结论 |
| --- | --- | ---: | ---: | ---: | --- |
| [`ef293bbe…`](../../../evals/code/runs/ef293bbe574543e0a4224250b0dad0c9/manifest.json) | entity projection 缺失 | `0.000000` | `0.000000` | `0.777778` | **NOT QUALIFIED** |
| [`ee42ee27…`](../../../evals/code/runs/ee42ee27441a4361a2ec40f483c26be1/manifest.json) | projection 修复 | `0.522727` | `0.477273` | `0.477679` | **NOT QUALIFIED** |
| [`33f76ed5…`](../../../evals/code/runs/33f76ed55a3d4c9ab1060a54bfa13d35/manifest.json) | entity-aware diversification | `0.613636` | `0.590909` | `0.129464` | **NOT QUALIFIED** |

第三次 Run 的 Exact entity recall@10 `0.833333`、Identifier entity recall@10 `1.0`
均与 C-B0 持平，但 locator 仍从 C-B0 `0.840909` 降至 `0.590909`。Low-overlap
entity recall@10 为 `0.600000`；10 个 zero-result case 与剩余 locator 回退中 7 个
未召回目标实体，构成进入 C3 Dense/Profile 的真实失败切片。C0 的 `5a92...` 仍是唯一
qualified baseline，这三个失败 Run 只是不可覆盖的审计证据。

### CURRENT_IMPLEMENTED_C3

- C3-01 Embedding Provider/Profile：**PASS**；
- C3-02 Dense Publisher/Hybrid：engineering **PASS**；
- C3-03 Deterministic Reranker/Calibration：engineering **PASS**；
- C3 engineering：**COMPLETE**；不等于 quality PASS 或 release；
- frozen/versioned Profile、batch Provider、content-addressed cache 与完整
  model/revision/dimension/locality provenance 已实现；secret/quarantine、partial
  batch、mixed dimension 与 remote-default-off 均 fail closed；
- Dense profile 使用 building → ready/unavailable 发布边界，严格绑定
  project/repository/generation/ACL/Profile/provenance；Exact/Sparse/Dense 以
  deterministic weighted RRF 合并并保留真实 channel rank/score/outcome trace；
- deterministic reranker 先做 version/generation/ACL hard gate，再使用 EvidenceCard
  特征全序重排；deadline/异常保留原 hybrid RRF 顺序，trace 记录原/新 rank、feature
  contribution、拒绝原因与 fallback；
- calibration artifact 绑定 reranker/profile/model/index version；无样本 unavailable，
  小样本 provisional，版本漂移 fail closed，不把 empirical score 称为概率或 truth；
- learned/remote embedding 与 cross-encoder 均 **unavailable/not-run**。

C3 的两个真实 production treatment 均为 verified 33/33，但都没有通过质量门：

| Run | 真实结果 | 裁决 |
| --- | --- | --- |
| [C-B2 `6fad2dd1…`](../../../evals/code/runs/6fad2dd1c67846668ba38045ddae0064/manifest.json) | entity `0.7273`、locator `0.5682`；27/33 有 dense 贡献，恢复 C-B1 10 个 zero-result 中 4 个 | **NOT QUALIFIED** |
| [C-B5 `baf5b9eb…`](../../../evals/code/runs/baf5b9eb49464ca79920030fd1c772de/manifest.json) | locator `0.636364` vs C-B0 `0.840909`，delta `-0.204545`；calibration 65 observations、ECE `0.285`、Brier `0.307029`，仍 provisional | **NOT QUALIFIED** |

C-B0 `5a92...` 仍是唯一 qualified baseline；C-B1/C-B2/C-B5 只作 audit。
C-B2 的 dense 局部恢复、C-B5 相对 C-B2 的 locator 回升都不能写成相对 C-B0 的总体质量
提升。C-B5 真实 Run fallback/timeout 为 `0/0 of 33`，所以只能陈述 fallback 工程合同
通过，不能虚构 production timeout 演练。

### CURRENT_IMPLEMENTED_C4

- C4-01 Edge Ontology/Diagnostics：engineering **COMPLETE**；
- C4-02 Typed Graph Retriever：engineering **COMPLETE**；
- C4-03 Query Profile/Fusion：独立 Gate **PASS**，P0/P1=`0`；
- C4 overall engineering：**COMPLETE**；不等于 quality qualified 或 release；
- typed、exhaustive、immutable edge registry 与有界 unresolved diagnostics 已实现；
- SQLite adjacency 以 `mode=ro` 读取；publication/stable version、双端 ACL、
  registered type/direction/confidence 在存储与 retriever 两层校验；
- deterministic beam、cycle prevention、node/edge budget、deadline 与完整 path trace
  已实现；
- C-B3 authorization 采用可移植的 normalized snapshot；既有 artifact 使用
  `strict-legacy-v1` 自包含验证，不依赖当前可追加 Gate 文本。
- 8 个 frozen/versioned task profiles 与 `CodeSourceFusionPipeline` 已实现；pipeline
  串联 exact、sparse+dense hybrid、typed graph、optional history/test hooks、
  entity/unit/version dedup、deterministic reranker、calibration、adaptive-k 与 strict
  `CodeSourceResult`/typed trace；
- simple exact fast path 明确跳过 dense/graph；缺 hook/provider/artifact 时诚实记录
  skipped/unavailable，timeout/error/fallback/degrade/refuse 全部可审计，refuse 清空候选。

真实 C-B3
[`92f8d8e1…`](../../../evals/code/runs/92f8d8e190f449bab9ba253227473419/manifest.json)
已 `verified`，四个 arms `graph_off / graph_post_only / typed_graph /
graph_reranker` 各 33 case，但最终 **NOT QUALIFIED**：

| treatment | Entity Recall@10 | Locator Recall@10 | MRR@10 | nDCG@10 | Query P95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| graph off | `0.7273` | `0.5682` | `0.3505` | `0.4209` | `570.803` |
| graph + reranker | `0.7273` | `0.5682` | `0.3975` | `0.4537` | `692.525` |
| treatment − graph off | `0` | `0` | `+0.0470` | `+0.0328` | `+121.7` |

MRR/nDCG 分别增加 `0.0470/0.0328`，但 Entity/Locator Recall 没有增加，P95 反而增加
`121.7 ms`。33 case 中 12 个有 typed path truth、21 个 unavailable；Required Path
Recall/Precision=`0/0`、accepted paths=`0`、graph-only recovery=`0`。graph +
reranker 的 27 条 traces 扫描 `4101` edges、扩展 `719` nodes，并发生
`cycle=1001`、`budget=1095` prunes；这些是执行与限界证据，不是召回或质量收益。

C-B0 `5a92...` 仍是唯一 qualified baseline；C-B1/C-B2/C-B5 以及 C-B3 只作 audit，
不能声称 Graph 提升召回。

### CURRENT_IMPLEMENTED_C5

- C5-01 SCIP Consumer / Safe Execution：engineering **PASS**；
- C5-02 Python Semantic Edge Treatment：engineering **PASS**；
- C5 overall engineering：**COMPLETE**，最终 Gate P0/P1=`0`；
- safe runner 默认 off；bounded parser、path/scope canonicalization 与 resolver 均
  fail closed；Gate 使用 fixture/假 runtime/注入 executor，未下载、联网或实际执行
  indexer；
- Python 为 `SUPPORTED`；JavaScript 1 case 与 TypeScript 2 cases 明确
  `UNAVAILABLE`；
- `REFERENCES/CALLS/IMPLEMENTS/OVERRIDES` 需要各自严格 occurrence/relationship
  evidence；未解析/local/external occurrence 不授权内部语义边；
- same canonical edge 合并多 provenance，冲突保留 Tree-sitter baseline 并进入 bounded
  diagnostics；0 label records 必须产出 `UNAVAILABLE` 质量真值。

唯一 C-B4 production audit
[`a22b322a…`](../../../evals/code/runs/a22b322a141741ae9a710aa347fbf032/manifest.json)
已 verified，精确绑定 `SemanticEdgeTreatment` / `c5-python-semantic-edges-v1`、
Golden Python30 与 30 cases / 42 outputs attestation：

| Arm | Cases / edges | Provenance | Treatment P95 | Peak memory |
| --- | ---: | --- | ---: | ---: |
| Tree-sitter conservative | 30 / 42 `REFERENCES` | Tree-sitter 42 | `0.960875 ms` | `13,766 B` |
| SCIP semantic | 30 / 42 `REFERENCES` | SCIP 42 | `2.358417 ms` | `19,170 B` |
| merged policy | 30 / 42 `REFERENCES` | 42 条双 provenance | `3.228958 ms` | `18,984 B` |

90 treatment rows 与 artifact SQLite 的 126 semantic-edge rows 对账无差异；
artifact 恰为 9 个只读文件，SQLite `quick_check=ok`。这些只证明三臂执行、合并和资源
边界。真实 label records=`0`，所以 precision、coverage、quality unresolved reduction、
graph noise 与 harmful quality 全部 `UNAVAILABLE`；`42/42` 只能称
non-quality structural coverage。C-B4 真实结论是 **VERIFIED — NOT QUALIFIED**，
C-B0 `5a92...` 仍是唯一 qualified baseline。

### CURRENT_IMPLEMENTED_C6

- C6-01 Historical Materialization / Symbol Lineage：engineering **PASS**；首轮 4 个
  P1 已关闭；
- safe exact ref→full SHA 与 bounded `git ls-tree` / `git show` 只读 Git object、
  真实 `CodeParser → CodeUnitBuilder`、current/dirty/history namespace 已验收；
- memory copy-on-write 与系统临时目录 SQLite 的原子 publication/cache/rollback、
  exact authorized read、TTL/LRU 与 explicit/referenced/hot pins 已验收；
- `SAME_SYMBOL_AS` / `RENAMED_TO` / `MOVED_TO` 为 confirmed；split/merge 只保留
  review-required candidate；
- C6-02 Diff→Symbol：engineering **PASS / COMPLETE**；old/new exact version/path、
  typed CONTAINS/AFFECTS、ambiguous candidate no-edge 与 confirmed lineage citation
  主路通过；
- C6-02 exact production method identity Gate 已通过：
  `evidence_rag.rag.sources.code.diff_symbol_v2.DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2`。
- C6-03 Test/Validation→Symbol：engineering **PASS / COMPLETE**；首轮
  reported/observed status contradiction P1 已关闭，最终 P0/P1=`0`；
- TestResult 事实归属使用注册 `CONTAINS` + role=`has_test_result`；只有一致
  observed passed + exit 0 产生 `VALIDATED_BY`，一致 failed/error/nonzero 产生
  `FAILED_VALIDATION`，其余观察、缺失或矛盾状态不产生 validation edge；
- coverage function/line 与 exact selector/target 可 confirmed；SCIP/import/call/
  path-name 等弱证据只作 review-required candidate；historical/missing/scope truth
  保持 fail closed；
- C6 overall engineering：**COMPLETE**。

唯一 C-B6 Run
[`evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e`](../../../evals/code/runs/06eea9107f274fe3b13d77bd68b8a00e/manifest.json)
artifact 已 verified：12 cases、8 labeled cases、16 canonical symbol labels；merged
precision/recall/F1=`0.888889/1/0.941176`，false affected rate=`0.111111`。因
`8 < 30`、precision `< 0.9`、false affected `> 0.1`，结论为
**PROVISIONAL NOT QUALIFIED**；C-B0 `5a92...` 仍是唯一 qualified global baseline。

独立 120 组 truth probe：

```text
VALIDATED_BY: 2
FAILED_VALIDATION: 6
OBSERVATION_ONLY: 112
False Validation: 0
```

C6-03 专项 `145 passed`，C6 五文件固定集合 `208 passed`。

### CURRENT_IMPLEMENTED_C7_01

- C7-01 Task-specific Context Builder：engineering **PASS / COMPLETE**，第二轮 Gate
  P0/P1=`0`，首轮 6 个 P1 全部关闭；
- 四个冻结模板覆盖 implementation、bug localization、impact analysis 与 change
  context；Retrieval/Comprehension contracts 分离，只从已证明的 context blocks 装配
  原文，并保留 stable citation、relation path、budget/refusal/missing/pruned trace；
- production fusion 使用模块私有 HMAC-SHA256 attestation 绑定 project/repository/
  ref/ACL/index/watermark/version/generation/paths、candidate/context manifest 与
  result digest，builder 拒绝裸结果、篡改和 replay；
- content manifest 绑定正文与 block identity；child/citation 使用严格 provenance
  dedup；clean/dirty version 只接受 lowercase 40/64-hex full-SHA 或严格 dirty
  grammar，canonical locator/exact span 均 fail closed；
- metrics 使用 `AVAILABLE / UNAVAILABLE + value + reason` 三态，不从无正文或无 label
  生成虚假满分；固定八文件 `363 passed`，主控与独立 Gate 均 PASS。

### CURRENT_IMPLEMENTED_C7_02

- C7-02 Platform opt-in integration：engineering **PASS / COMPLETE**，第二轮 Gate
  task `019fa8fd-4d83-7022-8a45-f68610db1a11` 裁决 P0/P1=`0`，首轮唯一多仓
  scope P1 已关闭；
- direct API 与 Platform 共用 `CodePlatformIntegration`；默认/显式 v1 只执行一次
  legacy，v1/v2 header 非法值返回 422，无 override 时 canary bucket 稳定；
- single repository + exact full commit 才进入 governed production V2，验证
  attestation 并经 builder 输出 V1 主字段投影；fallback 只执行一次；
- shadow 复用同一次 legacy response 并运行真实 V2；Platform code 与 mixed-source
  投影已通过；
- 歧义 project-only/显式多仓 scope 在 V2 pipeline 前以固定
  `scope_unavailable` fail closed；固定八文件 `227 passed`、C1-C7 扩展
  `511 passed`，静态检查 PASS。

### CURRENT_IMPLEMENTED_C7_03

- C7-03 Shadow/Canary/Release Evidence：engineering **PASS / COMPLETE**；第二轮 Gate
  task `019fa8fd-4d83-7022-8a45-f68610db1a11` 裁决 P0/P1=`0`，C7 overall
  engineering **COMPLETE**；
- 首轮 3 P1 全部关闭：C-B6 使用 exact immutable URI 且保持
  `PROVISIONAL_NOT_QUALIFIED`，rollback severity 由 trusted deployed stage 决定，
  child evidence 同时绑定 stage 与 parent snapshot，跨 stage copy fail closed；
- C7-03 专项 `41 passed`、十文件固定集合 `354 passed`，静态检查 PASS；
- release decision 为 **HOLD_DEFAULT_V1**：默认仍为 V1，**NOT RELEASED**；没有
  production canary、actual rollout、config/default switch、rollback execution、
  persistent release state 或新 quality Run。

面试时必须明确区分 `CURRENT_IMPLEMENTED_C0`、`CURRENT_IMPLEMENTED_C1`、
`CURRENT_IMPLEMENTED_C2`、`CURRENT_IMPLEMENTED_C3`、
`CURRENT_IMPLEMENTED_C4`、`CURRENT_IMPLEMENTED_C5`、`CURRENT_IMPLEMENTED_C6`
、`CURRENT_IMPLEMENTED_C7_01`、`CURRENT_IMPLEMENTED_C7_02` 与
`CURRENT_IMPLEMENTED_C7_03`。C2/C3/C4/C5/C6/C7-01/C7-02/C7-03 engineering
PASS/COMPLETE 表示工程能力、
兼容、隔离、fail-closed 和 production artifact 主流程可信，不表示
C-B1/C-B2/C-B3/C-B4/C-B5/C-B6 quality treatment、
Code RAG V2 发布或优化百分比已经完成。尤其要明确：
**engineering COMPLETE ≠ quality qualified**。

## 2. 15 秒定位

> 我先把 Code RAG 做成可审计的版本化证据域，完成 Evaluation V2、50 条 Golden v2、
> 隔离的 V1 baseline，以及通过 Gate 的 contract、V1 adapter 和 bounded AES-GCM
> shadow。随后实现并通过 C2 的 Parser IR、AST Unit、dual-write 和 Exact/Sparse 工程
> Gate；三次真实 C-B1 驱动了 entity projection 与 diversification 两轮修复，但 locator
> 仍低于 C-B0。C3 继续完成 versioned embedding profile/cache、local dense/hybrid、
> deterministic reranker、provisional calibration 与保序 fallback 工程合同；C-B2/C-B5
> 虽都 verified 33/33，却仍因 locator 回退拒绝 qualification。默认用户路径仍是 V1，
> C4-01/C4-02 工程已完成，但四臂 C-B3 的 Entity/Locator Recall 没有改善、path recall
> 和 graph-only recovery 都为 0，P95 还增加 121.7 ms，因此诚实拒绝 qualification；
> C4-03 又完成 8 个 task profiles、fusion pipeline、exact fast path 与 strict trace，
> 因此 C4 overall engineering COMPLETE；这不改变 C-B3 NOT QUALIFIED。C5 又完成
> 默认 off 的安全 SCIP consumer 与 Python semantic edge treatment，但唯一 C-B4 虽
> verified，仍因 0 labels 使全部质量指标 unavailable 而 NOT QUALIFIED；C5 Gate 当时
> 仅授权 C6-01。随后 C6-01 historical materialization/lineage 与 C6-02 Diff→Symbol 已通过
> engineering Gate，C-B6 artifact 也已 verified；但仅 8 个 labeled cases，precision
> `0.888889`、false affected `0.111111` 未达门槛，因此仍是 PROVISIONAL NOT
> QUALIFIED。C6-03 的 120 组 truth probe 达到 False Validation=0，C6 overall
> engineering COMPLETE。C7-01 又完成四模板 Context Builder，并关闭 scope
> attestation、原文 manifest、citation dedup、version/locator 与 metrics 六类 P1；
> C7-02 再以共享 direct/Platform integration 完成 governed exact-commit V2、V1
> 投影/一次 fallback、shadow legacy reuse 与 mixed-source，并关闭首轮多仓 scope
> P1。C7-03 又完成 release evidence evaluator，以 C-B6 exact URI、trusted deployed
> stage 与 stage+parent-bound child evidence 关闭首轮 3 P1，C7 overall engineering
> COMPLETE；但真实 release decision 是 HOLD_DEFAULT_V1，默认仍是 V1，NOT RELEASED，
> 且没有新 quality Run 或 production canary。

## 3. 1 分钟讲解

> 初始系统已经有 Tree-sitter Symbol、稳定 URI 和保守 CALLS/REFERENCES，但文件和完整
> Symbol 直接向量化，使用本地 hash 特征，而且 Graph 只在文本 Top-K 后展示，所以跨文件
> 依赖、低词面重合和历史版本查询较弱。
>
> 我先补齐了 Evaluation V2 和 released Code Golden v2：50 条 case 中 33 条进入 V1
> baseline 分母，17 条因历史 Symbol/tag 或 TestResult 能力缺失保留 unavailable。唯一
> qualified Run 的 Entity Recall@10 是 0.8409、MRR@10 是 0.6322、Exact Commit
> Accuracy 是 0.8421、Hard Negative Error@10 是 0.3529，P95 是 15.04 ms。这些数字
> 说明 baseline 便宜但质量边界明显，是诊断起点，不是优化收益。
>
> C1 随后把 V1 的真实 pre-truncation channel rank/hit_count 和 `complete_pruned` 语义
> 固化成严格 contract，并让 adapter 验证版本、ACL、commit、generation 和 locator
> execution evidence。默认路径仍是 V1，flag-off 零额外工作；flag-on 只把有界 snapshot
> 通过 AES-GCM 密封后异步送入 shadow，输出固定脱敏 telemetry，失败不影响响应。C1
> 最终 209 项专项、338 项全量和独立 adversarial Gate 均通过，但这证明的是迁移安全与
> 可审计性，不是 Recall 或排序提升。
>
> C2 已把稳定 CodeSymbol 与可重建 AST Retrieval Unit 分开，并完成 opt-in dual-write
> 与 Exact/Sparse。第一次真实 C-B1 因 Unit 未投影稳定 symbol entity，entity/locator
> 都为 0；projection 修复后分别到 0.5227/0.4773，但暴露 same-entity child 拥挤；
> diversification 后到 0.6136/0.5909，duplicate 降到 0.1295。Exact 0.8333、
> identifier 1.0 虽与 baseline 持平，locator 仍低于 baseline 0.8409，所以三次 Run
> 都拒绝 qualification。
>
> C3 随后把 profile/cache、Dense publisher、Exact/Sparse/Dense hybrid、
> deterministic reranker、calibration 和 fallback trace 做成完整工程合同。C-B2 的
> local-hash dense 在 27/33 case 有贡献并恢复 4/10 zero-result，但 entity 只有
> 0.7273、locator 0.5682；C-B5 加入 deterministic reranker 后 locator 到 0.6364，
> 仍比 C-B0 0.8409 低 0.2045。calibration 也只有 65 个 observations，ECE 0.285、
> Brier 0.307029，仍是 provisional；cross-encoder 未运行。因此 C3 engineering
> COMPLETE，但两个 treatment 都 NOT QUALIFIED。
>
> C4-01/C4-02 随后完成 typed edge registry/diagnostics、read-only adjacency、
> publication/version/ACL 双层校验，以及 beam/cycle/budget/deadline/path trace。
> 四臂 C-B3 各跑 33 case；graph + reranker 相对 graph off 的 MRR/nDCG 虽增加
> 0.0470/0.0328，但 Entity/Locator Recall 均不变，Required Path Recall/Precision、
> accepted paths 与 graph-only recovery 均为 0，P95 增加 121.7 ms。因此工程
> COMPLETE 与 quality NOT QUALIFIED 同时成立。
>
> C4-03 随后用 8 个 frozen/versioned profiles 与 `CodeSourceFusionPipeline` 串联
> exact fast path、hybrid、graph、optional history/test hooks、rerank、calibration、
> adaptive-k 和 strict trace；缺 provider/artifact 会诚实 unavailable/degrade/refuse。
> 独立 Gate P0/P1=0，C4 overall engineering COMPLETE，但没有新 quality Run，C-B3
> 仍 NOT QUALIFIED。
>
> C5-01 随后完成默认 off、bounded、fail-closed 的 SCIP consumer/safe runner；没有
> 下载、联网或实际执行 indexer。C5-02 对 Python 提供严格
> REFERENCES/CALLS/IMPLEMENTS/OVERRIDES occurrence evidence、merge/conflict 与
> provenance 语义，JS/TS 明确 unavailable。唯一 C-B4 以 Python30 跑
> Tree-sitter/SCIP/merged 三臂，各得 42 条 REFERENCES；merged 42 条均双 provenance，
> 但真实 labels 为 0，所以 precision、coverage、unresolved reduction、noise/harmful
> 全部 unavailable。C5 overall engineering COMPLETE 与 C-B4 NOT QUALIFIED 同时成立；
> `42/42` 只能讲结构覆盖，不能讲质量提升。C6-01 随后完成 safe exact historical
> materialization、namespace/cache/rollback/pins 与 confirmed lineage，C6-02 完成
> Diff→Symbol 和 exact production method identity Gate。C-B6 的 12 cases / 8 labeled /
> 16 labels artifact 已 verified，但 merged precision `0.888889`、false affected
> `0.111111` 未达门槛，故 PROVISIONAL NOT QUALIFIED。C6-03 随后完成注册
> CONTAINS/has_test_result、validation truth table、Test→Symbol 证据等级与
> historical/missing/scope 真值；120 组 truth probe 为 VALIDATED_BY 2、
> FAILED_VALIDATION 6、OBSERVATION_ONLY 112、False Validation 0。C6 overall
> engineering COMPLETE。C7-01 随后完成四模板 Retrieval/Comprehension Context
> Builder，以 production HMAC attestation、content manifest、严格 citation dedup、
> version/locator grammar 与三态 metrics 关闭首轮 6 P1，固定八文件 363 passed；
> C7-02 随后让 direct API 与 Platform 共用 integration：默认/显式 v1 仅一次 legacy，
> header 非法值 422，stable canary、单仓 exact commit governed V2 + attested builder、
> V1 投影/一次 fallback、shadow legacy reuse + 真实 V2 与 mixed-source 均通过。首轮
> 多仓 scope P1 以 pipeline 前 `scope_unavailable` 关闭；固定八文件 227、C1-C7 扩展
> 511 passed，静态检查 PASS。C7-03 随后以 C-B6 exact immutable URI、trusted
> deployed-stage rollback severity 与 stage+parent-bound child evidence 关闭首轮 3 P1；
> 专项 41、十文件 354 passed，静态检查 PASS。C7-01/C7-02/C7-03 engineering 均
> PASS / COMPLETE，C7 overall engineering COMPLETE；但 release decision 为
> HOLD_DEFAULT_V1，默认仍 V1，NOT RELEASED，没有 production canary、config/rollback
> 执行、default switch 或新增 quality Run。

## 4. 白板顺序

```text
Git/Folder/Test/SCIP
        ↓
Stable Entities
Repository → Commit → FileVersion → CodeSymbol
        ↓
Retrieval Units
signature / AST block / file surface / diff / test
        ↓
Exact + Sparse + Dense + Graph + History/Test
        ↓
Code Reranker + Calibration
        ↓
Task Context Builder
        ↓
Code Evidence + Locator + Version + Path
```

然后下钻一条：

```text
Test Failure
→ TESTS/COVERS
→ Suspect Symbol
→ CALLS/READS
→ Changed Symbol
→ DiffHunk
```

## 5. 三个核心决策

### 5.1 Stable Entity 与 Retrieval Unit 分离

原因：

- Chunk 策略会变，Citation ID 不应变；
- 小 Unit 适合召回，大 parent context 适合理解；
- File/Symbol/AST 可以去重；
- embedding 可独立重建。

未选择：

- 直接用 chunk ID 当知识实体；
- 整个函数/文件永久作为唯一粒度。

### 5.2 Graph 进入候选生成

原因：

- 文本相似无法稳定发现 caller/test/type dependency；
- Top-K 后附加 Graph 无法救回漏召；
- 不同任务需要不同结构路径。

控制噪声：

- typed edge whitelist；
- direction；
- max hops；
- confidence；
- version；
- ACL；
- beam budget；
- code reranker。

### 5.3 静态关系分层

Tree-sitter、SCIP/LSP、coverage、heuristic、LLM 和 human review 的可信度不同。关系记录
derivation、confidence、review status 和 locator；语义邻近不当作调用事实。

## 6. 关键算法口述

### 6.1 AST Chunk

> 先按 Symbol 边界；低于上限保留整体，超长函数按 branch/loop/try 等 AST block 递归。
> 每个 block 带 qualified name、signature、parent 和 used identifiers，但不重复整个父
> 函数。命中后才做 late context enrichment。

### 6.2 Graph Path Score

```text
seed relevance
× edge confidence product
× task-specific edge weight
× hop decay
× version alignment
```

### 6.3 Candidate Pipeline

```text
exact/sparse/dense seeds
→ typed graph/history/test expansion
→ entity/unit dedup
→ code rerank
→ calibrated relevance
→ adaptive-k
```

## 7. 高频追问

### 为什么不直接使用长上下文？

长上下文不能自动解决版本、符号身份、依赖路径和干扰问题，成本也随仓库增长。Code RAG
先精确找到候选，再后置加载结构上下文；最终要用相同 Golden Set 对比 long-context、
flat RAG 和 graph RAG。

### 为什么不迁移 Neo4j？

当前 4,712 条活跃边，问题不是图存储容量，而是关系覆盖、类型和检索算法。SQLite 继续做
事实主存，GraphRetriever 抽象与 adjacency 先解决查询；达到规模/并发阈值再评审图数据库。

### Tree-sitter 能做精确调用图吗？

不能。它适合语法和局部结构；receiver、多态、继承和跨包需要 SCIP/LSP/compiler。系统
保留 unresolved，并对不同 derivation 分层，不把静态近似说成运行时事实。

### 为什么还要 sparse？

代码有大量精确标识符、路径、error、SHA。Dense 擅长自然语言语义，sparse/exact 对
identifier 更稳；先 union 再 rerank。

### 如何找相关测试？

优先 test name/path 和显式 target，接入 coverage 后建立 TESTS/COVERS；没有 coverage 时
只能使用 import/call/path heuristic，并标为 candidate，不能说“已覆盖”。

### 如何处理历史代码？

查询先解析精确 commit/as-of；历史 commit 的 FileVersion/Symbol 按需物化，使用 Symbol
lineage 连接版本。不能用当前正文加旧 commit 标签冒充历史实现。

### 如何验证 Graph 有效？

比较 flat、flat+rerank、graph、graph+rerank；报告整体 Recall 之外，还报告 graph-only
recovered cases、required path recall 和 graph noise。真实 C-B3 已完成这种四臂对照，
结论是 Graph **尚未有效**：graph + reranker 相对 graph off 的 Entity/Locator Recall
均不变，Required Path Recall/Precision、accepted paths、graph-only recovery 均为 0，
且 P95 增加 `121.7 ms`。27 条 trace 扫描很多边只能证明 traversal 执行，不能证明收益。

### baseline 只有 3/33 acceptance pass，为什么 C0 还能 PASS？

C0 Gate 验的是 Evaluation V2、released Golden、artifact 完整性、分母语义、隔离和
可复现性，不是要求 V1 baseline 达到最终产品质量。`3/33=0.090909` 是 case acceptance
pass rate，不是 Recall、MRR 或 nDCG；弱 baseline 正是 C2-C7 quality treatment 的诊断起点。

### 为什么 C2 工程 PASS，但 C-B1 仍不合格？

两个 Gate 回答的问题不同。C2 工程 Gate 验证 Parser IR、Store、Unit Builder、
dual-write、Exact/Sparse、scope/ACL、rollback、真实 locator 与 V1 默认兼容是否正确；
C-B1 quality Gate 则要求同一 33-case 分母上 Exact、Identifier 和 Locator 相对唯一
qualified C-B0 不回退。第三次 Run 的 Exact `0.833333`、Identifier `1.0` 持平，但
Locator `0.590909 < 0.840909`，所以工程可以 PASS，treatment 必须 NOT QUALIFIED。

### 真实 Run 如何驱动两轮修复？

第一次 `ef293...` 的 entity/locator 都为 0，artifact 直接暴露 72 个 symbol Units 没有
投影稳定 `#symbol=` identity，因此先修 entity projection；第二次 `ee42...` 恢复了
Exact/Identifier，但 23 个 case 出现 same-entity child 拥挤、duplicate 为 `0.477679`，
因此再做 canonical representative + distinct-entity-first diversification。第三次
`33f76...` 把 duplicate 降到 `0.129464`、locator 提高到 `0.590909`，同时也证明剩余
问题已不是同一个 projection/拥挤缺陷。三个失败 artifact 全部保留，没有覆盖或挑选
“最好看”的 Run。

### 为什么当时进入 C3，而不是继续堆 lexical 边界？

两轮最小主流程修复后，Exact 与 Identifier 已和 C-B0 持平，继续改 exact/sparse 更容易
针对固定 case 过拟合，也无法直接覆盖自然语言低词面重合。第三次 Run 仍有 low-overlap
`0.600000`、10 个 zero-result 和 7 个未召回目标实体，因此进入 C3 做独立变量
benchmark。真实 C-B2 表明 local-hash dense 恢复了其中 4 个 zero-result，且 27/33
case 有 dense 贡献，但 low-overlap 仍为 `0.600000`、locator 只有 `0.5682`、query P95
升至 `550.13 ms`，所以 treatment 被拒绝；这正是停止条件生效，而不是“模型已提升”。

### 为什么 C3 engineering COMPLETE，但 C-B2/C-B5 仍不合格？

工程 Gate 验证的是 Profile/cache 身份、发布完整性、scope/ACL/provenance、三通道融合、
reranker hard gate、fallback、trace 与 calibration claim honesty；质量 Gate 仍以唯一
qualified C-B0 为锚。C-B2 locator `0.5682`；C-B5 虽回到 `0.636364`，仍低于 C-B0
`0.840909`，delta `-0.204545`。C-B5 calibration 只有 65 observations，ECE `0.285`、
Brier `0.307029`，仍 provisional；cross-encoder unavailable/not-run。因此工程可以
封板，两个 treatment 必须继续保留为 NOT QUALIFIED audit。

### 为什么 C5 engineering COMPLETE，但 C-B4 仍不合格？

C5 engineering Gate 验的是 safe runner 默认 off、bounded parser、resolver
fail-closed、严格 occurrence/relationship evidence、merge/conflict/provenance，以及
artifact verifier 在 0 labels 时不制造质量真值。C-B4 则需要真实人工 label evidence
才能评价 edge quality；实际 record count 是 0，因此 precision、coverage、unresolved
reduction、noise 与 harmful quality 都是 `UNAVAILABLE`，quality 必须 NOT QUALIFIED。
这正是工程合同生效，不是工程与质量结论矛盾。

### 三臂都是 42/42，为什么不能说 coverage 或 precision 是 100%？

因为 `42/42` 只表示 exact production fixture 的目标实体/结构输出全部出现，artifact
把它分类为 `non_quality_structural_observation`。它没有人工正确性标签，不能回答边是否
语义正确、是否覆盖真实世界关系、是否减少 unresolved 或引入 Graph 噪声。面试只能说
三臂各 42 条结构输出、merged 42 条双 provenance，不能说 quality coverage/precision
为 100%。

### 你能声称提升了多少吗？

不能声称总体或 Graph Recall 提升。当前有唯一 qualified V1 baseline，以及
C-B1/C-B2/C-B3/C-B4/C-B5 真实 audit Runs，但后者全部
`treatment_qualified=false`。可以
陈述 C-B2 “27/33 有 dense 贡献、恢复 4/10 zero-result”，也可以陈述 C-B3
MRR/nDCG 相对同轮 graph off 增加 `0.0470/0.0328`；必须同时说明 Recall 不变、
path/recovery 为 0、P95 增加 `121.7 ms`。C-B4 只能陈述结构/性能证据与质量
UNAVAILABLE，不能把局部计数、合并率或 P95 写成 semantic quality 提升。

### 为什么 17 条没有进入 baseline 分母？

这是机器能力边界而非删难题：10 条需要 non-active historical CodeSymbol 或 tag
resolution，7 条需要 stable validation artifact/TestResult retrieval。它们仍保留在
50 条 released Golden 和 coverage 报告中，并带非空 unavailable reason。

### 为什么同时保留 129 和 338？

129/55 是 C0 最终 Gate 的历史工程快照，不能改写；当时 `130 passed` 无法复现，所以
不能引用。C1 最终 Gate 的当前关闭证据已经增长为 C1 三文件专项 209、主控全量 338、
Golden 18，并另有定向 adversarial 27 和短 probe PASS。面试陈述必须带阶段，不能把
C0 的 129 与 C1 的 338 混成同一次运行。

## 8. 必做消融

| Run | 变量 | 指标 |
| --- | --- | --- |
| C-B0 | V1 raw + local hash | **已完成**；唯一有效 Run `5a92...` |
| C-B1 | AST Unit + exact/sparse | 工程已实现；三次真实 Run 均 **NOT QUALIFIED**，作为修复/拒绝审计保留 |
| C-B2 | local-hash dense/hybrid | **已完成**；`6fad2dd1...` verified 33/33、**NOT QUALIFIED**；learned embedding unavailable/not-run |
| C-B3 | graph off / post-only / typed graph / graph+reranker | **已完成**；`92f8d8e1...` 四臂各 33 case、verified、**NOT QUALIFIED**；Recall 无增益，Path Recall/Precision 与 graph-only recovery 均为 0 |
| C-B4 | Tree-sitter / SCIP / merged semantic edges | **已完成 production audit**；[`a22b322a...`](../../../evals/code/runs/a22b322a141741ae9a710aa347fbf032/manifest.json) verified，Python30 三臂各 42 edges、0 labels、quality **NOT QUALIFIED** |
| C-B5 | deterministic reranker + provisional calibration | **已完成**；`baf5b9eb...` verified 33/33、**NOT QUALIFIED**；cross-encoder unavailable/not-run |
| C-B6 | line overlap / AST enclosing / AST + rename-lineage merged | **已完成 production audit**；`06eea910...` artifact verified，12 cases / 8 labeled / 16 labels，merged P/R/F1=`0.888889/1/0.941176`、false affected=`0.111111`，**PROVISIONAL NOT QUALIFIED** |

## 9. 真实 baseline 失败分析

### 失败一：首位召回和排序弱

- Entity Recall@1 只有 `12/44=0.272727`，而 Recall@10 为
  `37/44=0.840909`；
- MRR@10 为 `0.632184`、nDCG@10 为 `0.604813`；
- 说明 V1 经常把相关实体放进候选，但首位排序和 graded ranking 不够好；
- C2 exact/sparse、C3 dense/rerank 与 C4 typed graph 工程均已实现，但
  C-B1/C-B2/C-B3/C-B5 全部未 qualified；C-B3 graph+reranker MRR@10 `0.3975`、
  nDCG@10 `0.4537` 仍低于 C-B0，不能写成弱排序或召回已修复。

### 失败二：同名与 harmful candidate

- 4 个 same-name error；
- Hard Negative Error@10 为 `6/17=0.352941`；
- Harmful Candidate Rate@10 为 `7/92=0.076087`，分布在 6 个 case；
- 典型边界是同名 `format_line`、overload 与 graph-harmful 查询；C-B5
  hard-negative error@10 为 `0.2941`，比 C-B0 `0.3529` 低，但 locator 与整体
  ranking 仍不合格；这只能说 slice 局部改善，不能覆盖 treatment 拒绝结论。

### 失败三：版本、结构与 context 能力不完整

- Exact Commit Accuracy 为 `32/38=0.842105`，Dirty Snapshot Accuracy 为
  `5/6=0.833333`；
- 12 个 graph-required miss；Required Context Coverage@10 为 `0/43=0`；
- 17 条 case 因历史 Symbol/tag 或 validation artifact/TestResult 能力边界不进入
  treatment 分母；
- C-B0 的 Unit 指标、paired graph-only recovery 和 dense full-scan timing 仍
  unavailable；C-B3 已补出真实 graph 对照：12 个 path-truth case 可评、21 个
  unavailable，Required Path Recall/Precision 与 graph-only recovery 均为 0；
- C-B2/C-B5 已有 local dense/rerank timing，C-B3 也有四臂 timing，但所有这些
  treatment 均不 qualified，不能替代缺失的 history/test/context 结论。

### 正确零结果也要讲

`014/027/035` 都返回 0 candidate，并分别符合 refuse/missing-evidence/refuse。安全拒答
和缺证据不是 retrieval failure。Unauthorized Leakage Rate@10 为 `0/224`，Returned
Locator Validity@10 为 `224/224`。

完整一手失败切片见
[error-analysis.json](../../../evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061/error-analysis.json)；
指标与分母见
[metrics.json](../../../evals/code/runs/5a92eafdff5d49e6aae8bb55fdc14061/metrics.json)。

## 10. C3/C4 已测负结果、C5/C6 production audit 与后续待验证假设

### 假设一：无约束 Graph 扩展

- 假设：更多邻居提高 Recall；
- 实测：C-B3 graph + reranker 相对同轮 graph off 的 Entity/Locator Recall 均为
  `0.7273/0.5682`，没有增加；MRR/nDCG 增加 `0.0470/0.0328`，但 P95 增加
  `121.7 ms`；
- path truth：12 available / 21 unavailable；Required Path Recall/Precision、
  accepted paths、graph-only recovery 全为 `0`；
- trace：27 条记录扫描 `4101` edges、扩展 `719` nodes，且有 `cycle=1001`、
  `budget=1095` prunes；扫描量不是收益；
- 结论：C4 overall engineering COMPLETE，但 C-B3 **NOT QUALIFIED**；
- C5 后续结果：semantic resolver/edge engineering 已完成，但 C-B4 不执行 retrieval
  ranking，且 0 labels 使 semantic quality unavailable；不能据此声称 C-B3 的有用
  edge/path coverage 或 Graph Recall 已改善。未来复跑必须重新取得真实质量证据。

### 假设二：Dense 可弥补低词面重合

- 假设：dense 能找回 exact/sparse 未召回的自然语言目标；
- 输入：第三次 C-B1 low-overlap `0.600000`、10 个 zero-result、7 个未召回目标实体；
- 实测：C-B2 local-hash dense 在 27/33 case 有贡献并恢复 4/10 zero-result，但
  low-overlap 不变、locator `0.5682`，相对 C-B0 回退 `-0.2727`，P95 `550.13 ms`；
- 结论：局部贡献成立，整体 treatment **NOT QUALIFIED**；
- 未测边界：neural/remote learned embedding 全部 unavailable/not-run，不得把
  local-hash 结果称为 neural embedding 效果。

C-B3 已从假设变成真实负结果；C4-03 engineering 已完成但没有新 quality Run。C-B4
也已从目标变成真实 production audit：工程完成、结构/性能可核验、质量因 0 labels
`UNAVAILABLE` / NOT QUALIFIED。C6-01/C6-02 已有 engineering Gate，C-B6 也已有
verified artifact，但仍为 PROVISIONAL NOT QUALIFIED。C6-03 engineering 已完成且
False Validation=0。C7-01 engineering 已完成，但没有新 quality Run 或 release；
C7-02 engineering 也已完成，但默认仍为 V1 且没有新 quality Run 或 release；
C7-03 engineering PASS / COMPLETE，C7 overall engineering COMPLETE；release
decision 仍为 `HOLD_DEFAULT_V1` / `NOT RELEASED`。C3/C4 的负结果必须原样保留。

## 11. 简历 Bullet

### C0 已完成、可验证

> 建立 Code Evaluation V2 与 released Golden v2，覆盖三类可复现实仓物化来源和 50 条
> case（33 条当前能力可评测、17 条 capability-boundary coverage），产出 ledger 锚定、
> 正式库零污染的 graph-off baseline；定位 Hard Negative Error@10 35.29%、Exact Commit
> Accuracy 84.21% 和 12 个 graph-required miss，为后续 AST/Graph/Rerank treatment
> 固化可比较起点。

### C1 已完成、可验证

> 建立严格 Code Source contract 与 V1 adapter，保留真实 pre-truncation channel
> rank/hit_count、`complete_pruned`、版本和 ACL execution evidence；以 flag-off 零额外
> 工作、bounded async AES-GCM shadow 和固定脱敏 telemetry 保持 V1 默认兼容，并通过
> 209 项 C1 专项、338 项全量及独立 adversarial Gate。

### C2 工程已完成、质量未通过

> 实现 Parser IR、稳定 AST Retrieval Unit、opt-in dual-write 与 Exact/Fielded Sparse，
> 通过 Schema/Store、版本/ACL、rollback、symbol projection 和 entity diversification
> 工程 Gate；以三次同分母 production C-B1 artifact 驱动两轮修复，并因最终 Locator
> Recall@10 `0.590909` 低于 C-B0 `0.840909` 拒绝 treatment qualification。

### C3 工程已完成、两个 quality treatment 均未通过

> 实现版本化 Embedding Profile/Provider、content-addressed cache、隔离 Dense
> publication、Exact/Sparse/Dense hybrid、deterministic reranker、版本绑定
> calibration 与保序 fallback/trace，并以两个真实 33-case production artifact
> 完成 C3 工程封板；C-B2/C-B5 因 locator 分别仅 `0.5682`/`0.6364`、均低于唯一
> qualified C-B0 `0.8409` 而拒绝 treatment qualification，未虚构质量提升。

### C4 overall 工程已完成、Graph quality treatment 未通过

> 实现 typed edge registry 与 unresolved diagnostics、SQLite read-only adjacency、
> publication/version/双端 ACL fail-closed 校验，以及 beam/cycle/budget/deadline/path
> trace；再以 8 个 frozen/versioned profiles 和 `CodeSourceFusionPipeline` 串联 exact
> fast path、hybrid、graph/hooks、rerank、calibration、adaptive-k 与 strict trace，
> 缺 provider/artifact 时诚实 unavailable/degrade/refuse。C4 overall engineering
> COMPLETE，但四臂 C-B3 的 Entity/Locator Recall 无增益、Required Path
> Recall/Precision 与 graph-only recovery 均为 0、P95 增加 `121.7 ms`，因此拒绝
> quality qualification，不把 `4101` 条扫描边当收益。

### C5 工程已完成、semantic quality 未通过

> 完成默认 off、bounded、fail-closed 的 SCIP consumer/safe runner 与 Python-first
> semantic edge treatment，严格区分 REFERENCES/CALLS/IMPLEMENTS/OVERRIDES evidence，
> 并以 Python30 三臂 production audit 验证 42-edge 输出、双 provenance 合并、90
> treatment rows 与毫秒/内存边界；因 0 条真实 labels 将全部质量指标保持
> UNAVAILABLE，C5 engineering COMPLETE 但 C-B4 NOT QUALIFIED，不把 42/42 当质量收益。

### C6 overall 工程已完成、独立质量审计未通过

> 实现 safe exact ref→SHA/Git object historical materialization、真实 Parser→Unit、
> current/dirty/history namespace、memory/临时 SQLite publication/cache/rollback、
> TTL/LRU pins 与 confirmed rename/move lineage；再完成 Diff→Symbol exact
> version/path mapping 和 production `map_hunk@version` identity Gate。C6-01
> engineering PASS、C6-02 engineering PASS / COMPLETE，但 C-B6 仅 8 个 labeled
> cases，merged precision `0.888889`、false affected `0.111111`，因此 artifact
> VERIFIED 而质量 PROVISIONAL NOT QUALIFIED。C6-03 又以注册
> CONTAINS/has_test_result、严格 validation truth table、分级 Test→Symbol evidence
> 和 historical/missing/scope truth 将 120 组 probe 的 False Validation 保持为 0；
> C6 overall engineering COMPLETE。

### C7-01 Context Builder 工程已完成、未发布

> 完成四个 task-specific Context 模板与分离的 Retrieval/Comprehension contracts，
> 只从已证明的 source blocks 装配原文并保留 stable citation、relation path 与
> budget/refusal/missing/pruned trace；以 production HMAC scope/publication
> attestation、正文 content manifest、严格 child/citation dedup、clean/dirty version
> grammar、canonical locator/exact span 和三态 metrics 关闭首轮 6 P1，固定八文件
> 363 passed。C7-01 engineering PASS / COMPLETE 不等于 quality 或 release；
> runtime/API/Platform 接线由已完成的 C7-02 承担。

### C7-02 Platform Opt-in Integration 工程已完成、默认仍为 V1

> 让 direct Code API 与 Platform 共用 `CodePlatformIntegration`，保持默认/显式 v1
> 一次 legacy 与非法 header 422；实现 stable canary、单仓 exact full commit governed
> V2 + attested builder、V1 主字段投影、固定原因一次 fallback、shadow legacy reuse +
> 真实 V2，以及 code/mixed-source 投影。首轮唯一多仓 scope P1 已在 V2 pipeline 前
> 以 `scope_unavailable` 关闭；固定八文件 227、C1-C7 扩展 511 passed，静态检查
> PASS。C7-02 engineering PASS / COMPLETE 不等于 quality 或 release；发布裁决由
> 已完成的 C7-03 evaluator 给出。

### C7-03 Release Evidence 工程已完成、决策保持 V1

> 实现只产出 decision/rollback plan 的 release evidence evaluator，以 C-B6 exact URI
> 保留 provisional negative truth，以 trusted deployed stage 决定 HOLD/rollback
> severity，并将 metric、guardrail、artifact、latency、cost、shadow child evidence
> 绑定 stage 与 parent snapshot，阻断跨 stage copy。首轮 3 P1 全部关闭，专项 41、
> 十文件 354 passed，静态检查 PASS；C7 overall engineering COMPLETE。但当前生产
> evidence 不足，Release decision 为 HOLD_DEFAULT_V1，默认仍为 V1，NOT RELEASED；
> 没有 production canary、actual rollout、config/default switch、rollback execution
> 或新 quality Run。

### 优化完成后才可使用

> 实现 AST-aware Code Graph RAG，通过 `[模型]` 代码语义召回、`[关系解析器]` 类型化图
> 遍历和 code reranker，将 `[N]` 条仓库级 Golden Query 的 Symbol Recall@10 从 `[A]`
> 提升至 `[B]`、Required Path Recall 从 `[C]` 提升至 `[D]`，Wrong-version Rate 保持
> `[E]`，P95 为 `[F]`。

当前不得使用这条“优化完成后”bullet；C-B1/C-B2/C-B3/C-B4/C-B5/C-B6 均没有
qualified treatment；C7 production quality 与 release-qualified evidence 仍不存在。

## 12. 面试证据表

| 证据 | 当前 | 完成后填写 |
| --- | --- | --- |
| Design | code-source-rag-v2 + code-source-development-v1 | 已有 |
| C0 Gate | C0-01/C0-02/C0-03 PASS | 已有 |
| C1 Gate | C1-01/C1-02/C1 overall PASS；历史上授权 C2 开始 | 已有 |
| C2 engineering Gate | C2-01/02/03/04/05、symbol projection、entity diversification PASS | 已有 |
| C3 engineering Gate | C3-01 PASS、C3-02/C3-03 engineering PASS；C3 engineering COMPLETE | 已有；不等于 quality PASS |
| C4 engineering Gate | C4-01 PASS；C4-02 engineering COMPLETE；C4-03 PASS（P0/P1=0）；C4 overall engineering COMPLETE | [C4 Gate](../development/reviews/05_CODE_C4_GATE_REVIEW.md)；不等于 quality PASS |
| C5 engineering Gate | C5-01 PASS；C5-02 engineering PASS；P0/P1=0；C5 overall engineering COMPLETE；C6-01 AUTHORIZED | [C5 Gate](../development/reviews/06_CODE_C5_GATE_REVIEW.md)；不等于 quality qualified |
| C6 engineering Gate | C6-01/C6-02/C6-03 engineering PASS；C6 overall engineering COMPLETE；P0/P1=0；C7-01 AUTHORIZED | [C6 Gate](../development/reviews/07_CODE_C6_GATE_REVIEW.md)；不等于 quality qualified |
| C7-01 engineering Gate | C7-01 engineering PASS / COMPLETE；首轮 6 P1 CLOSED；P0/P1=0；C7-02 AUTHORIZED | [C7 Gate](../development/reviews/08_CODE_C7_GATE_REVIEW.md)；不等于 quality 或 release |
| C7-02 engineering Gate | C7-02 engineering PASS / COMPLETE；首轮 1 P1 CLOSED；P0/P1=0；C7-03 AUTHORIZED | [C7 Gate](../development/reviews/08_CODE_C7_GATE_REVIEW.md)；默认仍 V1，不等于 quality 或 release |
| C7-03 / C7 overall engineering Gate | C7-03 engineering PASS / COMPLETE；首轮 3 P1 CLOSED；P0/P1=0；C7 overall engineering COMPLETE | [C7 Gate](../development/reviews/08_CODE_C7_GATE_REVIEW.md)；`HOLD_DEFAULT_V1` / `NOT RELEASED` |
| Golden Version | released `code-golden-v2`，package `sha256:8b0283ed...c0a61` | 已有 |
| Baseline Run | `evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061` | 已有 |
| Run anchors | qualification `9ba8081f...034f`；manifest `6ef5a2f1...35ca`；artifact-map `9d9f3bc8...ab44` | 已有 |
| C-B1 audit Runs | `ef293...` / `ee42...` / `33f76...`；均 production/33/artifact verified、`treatment_qualified=false` | 已有；不是 qualified treatment |
| C-B2 audit Run | `evaluation-run://project-code-golden-v2/6fad2dd1c67846668ba38045ddae0064`；verified 33/33、`treatment_qualified=false` | 已有；不是 qualified treatment |
| C-B5 audit Run | `evaluation-run://project-code-golden-v2/baf5b9eb49464ca79920030fd1c772de`；verified 33/33、`treatment_qualified=false` | 已有；不是 qualified treatment |
| C-B3 audit Run | [`evaluation-run://project-code-golden-v2/92f8d8e190f449bab9ba253227473419`](../../../evals/code/runs/92f8d8e190f449bab9ba253227473419/manifest.json)；四臂各 33 case、verified、`treatment_qualified=false` | 已有；不是 qualified treatment |
| C-B4 audit Run | [`evaluation-run://project-code-golden-v2/a22b322a141741ae9a710aa347fbf032`](../../../evals/code/runs/a22b322a141741ae9a710aa347fbf032/manifest.json)；Python30、三臂各 42 edges、verified、0 labels、`treatment_qualified=false` | 已有；42/42 仅 non-quality structural coverage |
| C-B6 audit Run | [`evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e`](../../../evals/code/runs/06eea9107f274fe3b13d77bd68b8a00e/manifest.json)；12 cases、8 labeled、16 labels；merged P/R/F1=`0.888889/1/0.941176`，false affected=`0.111111` | VERIFIED — PROVISIONAL NOT QUALIFIED |
| Embedding Profile/Cache | [embedding_v2.py](../../../src/evidence_rag/rag/sources/code/embedding_v2.py)；C3-01 PASS | 已有；learned model 未运行 |
| Dense/Hybrid | [dense_v2.py](../../../src/evidence_rag/rag/sources/code/dense_v2.py)；C3-02 engineering PASS / C-B2 NOT QUALIFIED | 已有 |
| Reranker/Calibration | [rerank_v2.py](../../../src/evidence_rag/rag/sources/code/rerank_v2.py)；C3-03 engineering PASS / C-B5 NOT QUALIFIED | 已有；cross-encoder 未运行 |
| Graph Run | C-B3 `92f8d8e1...`；NOT QUALIFIED | 已有真实负结果 |
| Code Commit | C0/C-B2/C-B3/C-B5 artifacts 与 C6 Gate 绑定 HEAD `bc3326ed...ab44974`；本次文档同步不新增 commit | 已有；不代表 release |
| Unit Builder | [unit_builder.py](../../../src/evidence_rag/rag/sources/code/unit_builder.py)；C2 Gate PASS | 已有 |
| Exact/Sparse | [retrieval_v2.py](../../../src/evidence_rag/rag/sources/code/retrieval_v2.py)；工程 Gate PASS / quality NOT QUALIFIED | 已有 |
| Graph Registry/Diagnostics | [graph_v2.py](../../../src/evidence_rag/rag/sources/code/graph_v2.py)；C4-01 engineering COMPLETE | 已有 |
| Graph Retriever | [graph_retrieval_v2.py](../../../src/evidence_rag/rag/sources/code/graph_retrieval_v2.py)；C4-02 engineering COMPLETE / C-B3 NOT QUALIFIED | 已有 |
| Query Profile/Fusion | [query_profile_v2.py](../../../src/evidence_rag/rag/sources/code/query_profile_v2.py)；8 profiles、fusion/fast path/hooks/rerank/calibration/adaptive-k/strict trace；C4-03 PASS | 已有；缺 provider/artifact 诚实 unavailable/degrade/refuse |
| SCIP Consumer/Safe Runner | [scip_v1.py](../../../src/evidence_rag/rag/sources/code/scip_v1.py)；默认 off、bounded parser、resolver fail-closed；C5-01 PASS | 已有；Gate 未下载/联网/执行 indexer |
| Python Semantic Edge | [semantic_edges_v1.py](../../../src/evidence_rag/rag/sources/code/semantic_edges_v1.py)；Python supported、JS/TS unavailable、严格 relation/provenance/0-label semantics；C5-02 PASS | 已有；C-B4 NOT QUALIFIED |
| Historical Materialization/Lineage | [history_v2.py](../../../src/evidence_rag/rag/sources/code/history_v2.py)；safe exact Git object、Parser→Unit、namespace、publication/cache/rollback、pins 与 lineage；C6-01 PASS | 已有 |
| Diff→Symbol | [diff_symbol_v2.py](../../../src/evidence_rag/rag/sources/code/diff_symbol_v2.py)；exact version/path、CONTAINS/AFFECTS、ambiguous fail-closed；C6-02 COMPLETE | 已有；C-B6 未 qualified |
| Test/Validation→Symbol | [test_validation_v2.py](../../../src/evidence_rag/rag/sources/code/test_validation_v2.py)；注册 CONTAINS/has_test_result、严格 validation truth table、confirmed/candidate Test→Symbol、historical/missing/scope truth；C6-03 PASS / COMPLETE | 120 组 probe False Validation=0 |
| Task-specific Context Builder | [context_builder_v2.py](../../../src/evidence_rag/rag/sources/code/context_builder_v2.py)；四模板、production attestation、content manifest、strict citation dedup/version/locator/metrics；C7-01 PASS / COMPLETE | 已有；runtime/API/Platform 接线由 C7-02 完成 |
| Platform Opt-in Integration | [platform_v2.py](../../../src/evidence_rag/rag/sources/code/platform_v2.py)；shared direct/Platform routing、governed exact-commit V2、V1 projection/fallback、shadow reuse、mixed-source；C7-02 PASS / COMPLETE | 已有；默认仍 V1，未 release |
| Release Evidence Evaluator | [release_v2.py](../../../src/evidence_rag/rag/sources/code/release_v2.py)；C-B6 exact URI、trusted deployed-stage severity、stage+parent-bound child evidence、fixed rollback plan；C7-03 PASS / COMPLETE | `HOLD_DEFAULT_V1`；未 runtime/config release wiring |
| Tests | C0 历史：专项 55 / 全量 129；C1 最终：短 probe PASS、定向 27、专项 209、全量 338、Golden 18；C2/C3/C4 关键专项见对应 Gate；C5 最终独立定向集合 218 passed；C6-03 专项 145、C6 五文件 208 passed；C7-01 固定八文件 363 passed；C7-02 固定八文件 227、C1-C7 扩展 511 passed；C7-03 专项 41、十文件 354 passed，静态检查 PASS | C7 engineering 已完成；release HOLD |
| Baseline P95 | `15.04 ms` | treatment 对比待填 |
| C3 query P95 | C-B2 `550.13 ms`；C-B5 `693.645 ms` | 已有负向观测 |
| C-B3 quality | graph off entity/locator/MRR/nDCG `0.7273/0.5682/0.3505/0.4209`；graph+reranker `0.7273/0.5682/0.3975/0.4537`；P95 `+121.7 ms` | Recall 无增益，NOT QUALIFIED |
| C-B3 path/trace | path truth 12 available / 21 unavailable；Path Recall/Precision、accepted paths、graph-only recovery 均为 0；27 traces 扫描 4101 edges / 719 nodes，cycle 1001 / budget 1095 prunes | 扫描量不是收益 |
| C-B4 structure/performance | 三臂各 42 `REFERENCES`；merged 42 条双 provenance；90 treatment rows；P95 `0.960875/2.358417/3.228958 ms`；peak `13,766/19,170/18,984 B` | 非质量结构/性能证据，不是 semantic quality 收益 |
| C-B4 quality | 0 label records；precision/coverage/unresolved reduction/noise/harmful 全部 `UNAVAILABLE` | VERIFIED — NOT QUALIFIED |
| C-B6 quality | 12 cases / 8 labeled / 16 labels；merged precision/recall/F1 `0.888889/1/0.941176`；false affected `0.111111` | VERIFIED — PROVISIONAL NOT QUALIFIED；8<30、precision<0.9、false affected>0.1 |
| Baseline quality | Recall@10 `0.840909`；MRR@10 `0.632184`；Path Recall `0.928571` | treatment 对比待填 |

完整锚与审计口径见
[Actual C0 Run Evidence](../development/01_CODE_SOURCE_DEVELOPMENT_PLAN.md#actual-c0-run-evidence)。

## 13. 开发实施证据路线

当前开发方案为 `IMPLEMENTATION_IN_PROGRESS`：C0、C1 已通过；C2 engineering PASS，
但 C-B1 quality NOT QUALIFIED；C3 engineering COMPLETE，但 C-B2/C-B5 quality
NOT QUALIFIED；C4-01/02/03 与 C4 overall engineering COMPLETE，但 C-B3 quality
NOT QUALIFIED。C5-01/C5-02 engineering PASS，C5 overall engineering COMPLETE，但
C-B4 quality NOT QUALIFIED。C6-01/C6-02/C6-03 engineering PASS，C6 overall
engineering COMPLETE，C-B6 VERIFIED — PROVISIONAL NOT QUALIFIED。C7-01
engineering PASS / COMPLETE；C7-02/C7-03 engineering PASS / COMPLETE，C7 overall
engineering COMPLETE。Release decision 为 `HOLD_DEFAULT_V1`，默认仍为 V1，
`NOT RELEASED`。后续严格按以下顺序推进：

| 阶段 | 实施内容 | 面试材料何时可以更新 |
| --- | --- | --- |
| C0 | Code Golden v2、Evaluation V2、V1 baseline | **已同步：最终 Gate PASS** |
| C1 | V2 contract、V1 adapter、shadow path | **已同步：最终 Gate PASS；不宣称质量提升** |
| C2 | AST Unit、dual write、exact/fielded sparse | **已同步：工程 PASS；三次 C-B1 失败审计，不宣称质量提升** |
| C3 | embedding profile/cache、dense/hybrid、reranker、calibration/fallback/trace | **已同步：engineering COMPLETE；C-B2/C-B5 verified 33/33 但 NOT QUALIFIED** |
| C4 | edge ontology、typed graph candidate、query profile/fusion | **已同步：C4-01/02/03 与 C4 overall engineering COMPLETE；C-B3 四臂各 33 case、verified 但 NOT QUALIFIED；不宣称离线质量提升** |
| C5 | SCIP consumer、Python-first semantic indexing | **已同步：C5-01/C5-02 engineering PASS；C5 overall engineering COMPLETE；C-B4 verified 但 0 labels / quality NOT QUALIFIED** |
| C6 | historical symbol、Diff/Test binding、验证语义修正 | **已同步：C6-01/C6-02/C6-03 engineering PASS；C6 overall engineering COMPLETE；C-B6 verified 但 PROVISIONAL NOT QUALIFIED** |
| C7 | task context、Platform opt-in、release evidence | **已同步：C7-01/C7-02/C7-03 engineering PASS / COMPLETE；C7 overall engineering COMPLETE**；C7-01 关闭 6 P1 / 363，C7-02 关闭 1 P1 / 227 / 511，C7-03 关闭 3 P1 / 41 / 354；`HOLD_DEFAULT_V1` / `NOT RELEASED` |

每个阶段同步保存四类证据：

- 合并后的代码入口与 commit；
- Golden/Evaluation dataset version；
- baseline/treatment/ablation Run；
- 一个真实失败案例及其修复、回滚或拒绝上线理由。

完整的 24 个建议 PR、依赖关系、schema、feature flag、验收指标和停止条件见
[Code Source RAG 开发优化方案](../development/01_CODE_SOURCE_DEVELOPMENT_PLAN.md)。

## 14. 不能夸大的部分

- 当前不是神经代码 embedding；
- C-B3 已真实执行 typed graph candidate generation，但 accepted path 与 graph-only
  recovery 都为 0，不能说 Graph 提升召回；
- 当前 CALLS/REFERENCES 覆盖有限；
- 当前已有隔离 SCIP consumer/semantic edge engineering 与 C-B4 audit，但 runner
  默认 off、未实际执行外部 indexer，JS/TS unavailable，且默认用户路径仍是 V1；
- 当前虽有 released Code Golden v2、baseline，以及已通过 Gate 的
  C1/C2/C3/C4/C5/C6/C7-01/C7-02/C7-03
  engineering 能力，
  但没有 qualified 的 C2-C7 检索质量 treatment；
- baseline 很弱是诊断结果，不能声称优化收益；
- C-B3 的 path truth 必须按 12 available / 21 unavailable 陈述；不能把 unavailable
  当 0，也不能把 27 traces 的 4101 edges / 719 nodes 扫描量当收益；
- C-B4 的 42/42、三臂 42 edges、双 provenance、0 conflict/unresolved 只能称
  non-quality structural evidence；P95/内存只能称性能证据；
- 当前没有可声明的总体优化百分比，只有 baseline 与
  C-B1/C-B2/C-B3/C-B4/C-B5/C-B6 audit 的绝对指标、局部排序变化、结构计数和回退；
- 静态关系不等于运行时关系；
- C0/C1/C2/C3/C4/C5/C6/C7-01/C7-02/C7-03 engineering Gate PASS/COMPLETE 不等于
  qualified quality
  treatment 或 Code RAG V2 RELEASED；
- C-B1/C-B2/C-B3/C-B4/C-B5/C-B6 Run 是审计证据，不是 qualified treatment；
- C4-03/C4 overall engineering 已完成，但没有新 quality Run，不能写成 quality
  qualified；C5 engineering 已完成，但 0 labels 使 C-B4 quality NOT QUALIFIED；
  C6 overall engineering 已完成，但 C-B6 仍为 PROVISIONAL NOT QUALIFIED；
  C7-01/C7-02/C7-03 与 C7 overall engineering 已完成但未发布；真实 decision 为
  `HOLD_DEFAULT_V1`，默认仍为 V1，且没有新 quality Run、production canary、
  config/rollback execution、default switch 或 production rollout 数据；
- cross-encoder 与 learned/remote embedding unavailable/not-run；
- calibration provisional（65 observations、ECE `0.285`、Brier `0.307029`），不能称为
  final probability；
- 设计 Target 不等于 Result。
