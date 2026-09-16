# Wiki + RAG 实施工作日志

版本：2026-08-03  
目标权威：`08_PRODUCTION_RAG_CORE_COMPLETION_PLAN.md`  
当前阶段：`WR0–WR7 REPOSITORY ENGINEERING COMPLETE`  
发布状态：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

> **2026-08-06 成熟度审查勘误**
>
> 本文是 2026-08-03 的历史工程日志，不再作为后续完成判定权威。当前
> `StoreRawEvidenceGatewayV1` 回读的是 WikiStore 保存的 candidate envelope，不是六源
> source store 的原始对象；Live Organizer 的 `derived_fact/deterministic_derived` 也尚未与
> grounded claim authority 闭环。全量后端当前存在 C-B1/C-B2/C-B5 publication 合同漂移，
> 且 Wiki/多源实现尚未进入当前 Git HEAD。修复顺序、退出门和实时状态以
> [全阶段成熟化执行总纲](10_RAG_WIKI_MATURITY_PROGRAM.md)和
> [持续执行台账](11_RAG_WIKI_MATURITY_EXECUTION_LEDGER.md)为准。本文中的 WR0–WR7
> `COMPLETE` 仅表示当时隔离 fixture 的工程结论，不表示 production raw authority 或质量资格。

## 1. 不可偏离的主线

本轮开发的 Wiki 是 agent-native knowledge organization，不是传统 Wiki 页面功能。最终链路必须是：

```text
六源原始证据
→ Wiki Compiler
→ 版本化 Wiki Store
→ search/read/follow/verify Navigator
→ 原始证据回查
→ Evidence Pack / grounded answer
→ Error Book / Builder 反馈闭环
```

以下约束贯穿所有阶段：

- Wiki 只负责组织和导航，六源 raw evidence 始终是最终事实权威；
- project、generation、visibility partition、ACL 在编译、存储、检索、缓存和回答全链路绑定；
- 任何记录、快照、评测数据和发布工件均采用 canonical JSON + content SHA-256；
- 不读取、哈希、checkpoint 或修改正式数据库及其 sidecar；所有验收只用 isolated tmp/in-memory；
- 不以 synthetic smoke 冒充真实质量，不在质量门前切换默认 V1；
- Codex 插件必须建立在已验收的 MCP 工具之上，不创建空壳插件；
- 不提交、不推送、不创建 branch/worktree。

## 2. 里程碑和当前状态

| 里程碑 | 内容 | 状态 |
|---|---|---|
| WR0 | Wiki IR、路径、Golden、评测权威、工作边界 | COMPLETE |
| WR1 | Wiki Store、generation snapshot、ACL fragment、atomic publish | COMPLETE |
| WR2 | 六源 Compiler、跨源页、增量重编译、Error Book | COMPLETE |
| WR3 | Hybrid search、obligation graph、Navigator、raw fallback、Evidence Pack | COMPLETE |
| WR4 | Builder patch、before/after utility、guard queries、schema evolution | COMPLETE |
| WR5 | Runtime/API/MCP/Codex plugin/agent tools/UI | COMPLETE |
| WR6 | 质量、安全、性能、容量、故障注入、全量回归、启动自测 | COMPLETE（ENGINEERING_FIXTURE） |
| WR7 | 技术文档、设计文档、面试 QA、发布真值与最终复核 | COMPLETE |

## 3. WR0 决策记录

### 3.1 复用现有工程底座

- `rag/sources/{code,codex,experiment,notebook,document,workspace}`：Compiler 输入和 raw fallback；
- `multisource_foundation_v2.py`：六源 vocabulary、canonical hash、planner/calibration 基础；
- `multisource_pipeline_v2.py`：governed source execution 和 fallback；
- `evidence_graph_v2.py`：typed relation candidates；
- `answer_v2.py`：最终 Evidence Pack、citations、grounded answer；
- `global_governance_v2.py`：full-content secret/absolute/UNC/control/injection inspection。

### 3.2 新 Wiki 合同边界

- logical path 是稳定知识地址；physical key 同时绑定 project、generation、visibility partition；
- page fragment 只包含单一 visibility partition，禁止把不同 ACL 证据合并后再过滤；
- fact/link 必须绑定 raw source refs；缺 raw refs 的内容最多是 suggestion，不能成为 answer authority；
- internal links 使用 generation-independent logical path；一次导航固定单一 generation；
- manifest 必须绑定全部 record digests、source generations、compiler/evaluator authority；
- Golden membership、slice、eligible metrics 和答案权威由 released dataset 决定，review row 不得自报。

## 4. 变更与验证流水

| 时间 | 变更 | 验证 | 结论 |
|---|---|---|---|
| 2026-08-03 | 完成仓库、最终 Gate、六源合同、MCP/插件手册审计 | 只读检查；未触碰正式库 | Wiki 内核确实缺失，进入 WR0 |
| 2026-08-03 | 启动 Wiki contracts/path/Golden/evaluator 实施 | 待专项测试 | IN_PROGRESS |
| 2026-08-03 | 冻结 120-case Golden、10 slices、8 metrics 与三态 evaluator | Wiki Foundation 8/8 PASS | WR0 COMPLETE |
| 2026-08-03 | 实现 isolated Wiki Store、ACL/generation snapshot、atomic publish、portable verifier | Wiki Store 5/5 PASS | WR1 COMPLETE |
| 2026-08-03 | 实现六源 Compiler、跨源页/双向 links、依赖索引、Error Book、增量等价验证 | Wiki Compiler 7/7 PASS；联合 20/20 PASS | WR2 COMPLETE |
| 2026-08-03 | 实现 exact/BM25/dense/RRF/structural/rerank 混合检索、obligation 驱动导航与 raw evidence 回查 | Wiki Search/Navigator 6/6 PASS；联合 26/26 PASS | WR3 COMPLETE |
| 2026-08-03 | 实现受审 content-addressed patch、immutable generation、before/after marginal utility 与 guard/hard gate | Wiki Builder 4/4 PASS；联合 30/30 PASS | WR4 COMPLETE |
| 2026-08-03 | 接入 governed runtime、Wiki 产品 API、显式智能查询引擎、Workbench、MCP 资源/工具和 Codex 插件 | Wiki 专项、API/MCP/plugin、前端测试和隔离启动 PASS | WR5 COMPLETE |
| 2026-08-03 | 增加 generation/ACL 精确键 bounded LRU、并发 reader/publish 不混代、S 层容量 artifact | capacity v4 全部 SLO PASS；production_authorized=false | WR6 性能 COMPLETE |
| 2026-08-03 | 运行 120-case Wiki navigation quality；v2 暴露 refusal 0/10，修复 query relevance admission 和 final refusal authority 后生成 v3 | quality v3 120/120，correct_refusal 10/10，全部安全 hard gates PASS；仍 NON_QUALIFIED | WR6 质量 COMPLETE |
| 2026-08-03 | 重启隔离服务，验证命中与不可回答 HTTP 双路径；运行全仓 safe suite、静态检查、前端构建与浏览器桌面/移动验收 | 1923 pytest、408 Python compile、30 frontend files/171 tests、typecheck/build、无浏览器错误 | WR6 COMPLETE |
| 2026-08-03 | 同步设计权威、RAG 技术报告、面试 QA、最终 Gate 与发布边界 | 保留 DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE | WR7 COMPLETE |

## 5. 最终工程证据

### 5.1 Quality

- 权威目录：`artifacts/wiki-rag/quality/wiki-quality-20260803-v3`；
- artifact set：`sha256:b4aa2b84d2e17033dfca4eab356518385b4dae31666b5c85207054a53e0176d4`；
- report：`sha256:10c1afb8f0605b009b99741d1890977d6fdfbbbc36c396bfbc483bd0f9085879`；
- 120/120 membership，110 个 answerable case 的 path/full-evidence/obligation/raw-citation/premature-stop 指标均为 110/110；
- 10 个 unanswerable case 的 correct refusal 为 10/10；ACL、unsupported claim、membership、execution-error hard gates 全部通过；
- P50/P95/P99 navigation 为 10.881208/16.099334/34.080333 ms；
- 真值仍为 `ENGINEERING_FIXTURE_NON_QUALIFIED / QUALITY_HOLD / production_authorized=false`。

历史 v2 artifact 保留 refusal 0/10 的负证据，不覆盖、不删除；v3 是修复后的当前工程证据。

### 5.2 Capacity

- 权威目录：`artifacts/wiki-rag/capacity/wiki-s-capacity-20260803-v4`；
- artifact set：`sha256:c89ac810dd8ed73bdd4a103eb3d5ff311a3de95a5cce3096ca618c881e66b8b3`；
- report：`sha256:2eea4c00ac6321cd56f6f836cd69a32338f235664f51a20b00dffd84ae0106cc`；
- 362 pages、950 source refs、48,328,704-byte SQLite、15,108,599-byte peak allocation；
- P95：path 0.307584 ms、directory 0.353 ms、hybrid 10.893417 ms、standard navigation 80.346333 ms、deep navigation 86.228667 ms、compile 267.919667 ms、publish 169.019667 ms、rollback 228.916083 ms；
- 全部 S 层 SLO 通过，但仅为 measured S point，不外推为 50k/M/L 生产容量。

### 5.3 Runtime、Agent 与产品

- `/v1/wiki/status|search|navigate|pages|read|follow|evidence|error-book|builder/*` 可用；
- `/v1/query` 只有显式 `X-RAG-Wiki-Engine: wiki_v1` 才进入 Wiki，默认 V1 不变；
- 不可回答的 evidence-mode 查询返回 final `refusal`，不会伪装为普通 retrieval handoff；
- MCP server 版本 `0.2.0`，21 个工具，Wiki 工具包含 status/search/read/follow/navigate/evidence/propose-patch；
- 插件 `research-project-bridge` 版本 `0.2.0+wiki.20260803`，四个有真实工具依赖的 skills；
- Wiki Workbench 已验收桌面三栏、移动堆叠、服务端筛选/分页、trace、raw verify、关系跳转、Error Book 与 Builder 状态。

### 5.4 最终回归

- 后端 safe suite 1923/1923 PASS；排除项仅为既有协议中会解析/触碰正式服务库路径的 C-B1–B5 整文件和两条正式路径节点，C-B6 保留；
- `ruff check src tests` PASS；408 个 Python 文件 in-memory compile PASS；`git diff --check` PASS；
- 前端 30 files/171 tests PASS，TypeScript typecheck 与 deterministic production build PASS；
- 隔离服务位于 system tmp，正式库未被读取、哈希、checkpoint 或修改。

## 6. 外部延期项

- production-owned 数据的只读镜像、迁移/backfill 与正式 generation 发布；
- M/L 容量、真实并发、远程 embedding/reranker/生成模型的质量、成本和隐私证据；
- 真实用户导航 trace、Builder 长期 utility、shadow/canary 与 rollback observation；
- reviewed release registry 与默认引擎切换。

这些项目需要新的数据所有者授权或外部资源，不能用仓库 fixture 代替。准确发布真值继续是
`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`。
