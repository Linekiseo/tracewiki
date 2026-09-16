# Agent-Native Wiki + RAG 工程与隔离启动最终复核

日期：2026-08-03  
范围：Wiki Compiler/Store/Search/Navigator/Builder、智能查询、MCP/Codex plugin、Workbench、质量、容量和启动  
结论：`WIKI_RAG_REPOSITORY_ENGINEERING_GATE_PASS`  
严重发现：`P0=0 / P1=0 / P2=0`  
发布结论：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. Gate 结论

设计权威 `08_PRODUCTION_RAG_CORE_COMPLETION_PLAN.md` 中 WR0–WR7 的仓库内工程范围已闭合。系统不再是
“六源 Retriever + UI”的样子工程，而具有可执行的：

- 六源 Evidence Compiler 与跨源 Agent-Native Wiki；
- generation/ACL 隔离的 WikiStore、原子 publish/rollback 和 portable verifier；
- exact/FTS5/BM25/dense/structural/RRF/rerank hybrid search；
- evidence-obligation 驱动的 search/read/follow/raw-verify Navigator；
- Evidence Pack、结构化 claims、citation verifier 和 final refusal；
- Error Book、受审 Builder patch、affected/guard/hard-gate utility；
- product API、Wiki Workbench、MCP 0.2.0 和 Codex plugin 0.2.0；
- bounded cache、quality/capacity artifacts 和隔离启动路径。

本 Gate 只证明仓库工程与 isolated evidence。生产数据、远程模型、M/L 容量、shadow/canary 和默认切换
没有被冒充为已完成。

## 2. 关键实现复核

| 能力 | 复核结果 |
|---|---|
| Wiki IR/paths | canonical page/fact/link/source-ref/manifest、逻辑路径与物理 generation/visibility key 完整 |
| Store | staged generation、active pointer、atomic publish/rollback、ACL fragment、symlink/path/DB 拒绝完整 |
| Compiler | 六源输入、跨源意图页、依赖传播、incremental/clean equivalence、Error Book 完整 |
| Search | exact、FTS/BM25、可替换 dense、structural、RRF、可替换 reranker 完整 |
| Navigator | obligation、query relevance、bounded actions、deadline、raw verification、fail-closed stop 完整 |
| Answer | Evidence Pack membership/citation/token digest；unanswerable+empty claims 仍为 final refusal |
| Builder | patch authority、immutable generation、affected/guard utility、安全 hard gates 完整 |
| Cache | project/generation/visibility/path/kind 精确键、bounded bytes/entries、并发不混代完整 |
| API/UI | Wiki 全产品 API、显式 query opt-in、Workbench 桌面/移动与 empty/error states 完整 |
| Agent | 21 MCP tools、Wiki status/search/read/follow/navigate/evidence/patch、四个 plugin skills 完整 |

## 3. Quality evidence

当前权威：`artifacts/wiki-rag/quality/wiki-quality-20260803-v3`

- artifact set：`sha256:b4aa2b84d2e17033dfca4eab356518385b4dae31666b5c85207054a53e0176d4`；
- report：`sha256:10c1afb8f0605b009b99741d1890977d6fdfbbbc36c396bfbc483bd0f9085879`；
- 120/120 complete membership；
- answerable：path/full evidence/obligation/raw citation/premature-stop 110/110；
- unanswerable：correct refusal 10/10；
- ACL/unsupported claim/no errors 120/120；
- P50/P95/P99：10.881208/16.099334/34.080333 ms；
- qualification：`ENGINEERING_FIXTURE_NON_QUALIFIED`。

历史 v2 的 correct refusal 0/10 保持不可变。v3 在修复 query relevance admission 和 empty-claim
unanswerable authority 后复跑，证明缺陷被发现和闭合，而不是修改 denominator 或删除负证据。

## 4. Capacity evidence

当前权威：`artifacts/wiki-rag/capacity/wiki-s-capacity-20260803-v4`

- artifact set：`sha256:c89ac810dd8ed73bdd4a103eb3d5ff311a3de95a5cce3096ca618c881e66b8b3`；
- report：`sha256:2eea4c00ac6321cd56f6f836cd69a32338f235664f51a20b00dffd84ae0106cc`；
- 362 pages、950 source refs、48,328,704-byte DB、15,108,599-byte peak allocation；
- path/directory/hybrid P95：0.307584/0.353/10.893417 ms；
- standard/deep navigation P95：80.346333/86.228667 ms；
- compile/publish/rollback P95：267.919667/169.019667/228.916083 ms；
- S 层全部目标通过，`production_authorized=false`。

## 5. 最终自动化回归

- backend safe suite：`1923/1923 PASS`；
- 排除：C-B1–B5 整文件和两条会解析正式服务库路径的既有节点；C-B6 保留；
- `ruff check src tests`：PASS；
- Python in-memory compile：408 files PASS；
- `git diff --check`：PASS；
- frontend：30 files/171 tests PASS；
- TypeScript typecheck + deterministic production build：PASS；
- frontend build contract：`sha256:f8e30e5f462dd2180306828cb3ddc745f96ca6428c1cc26711d6b4774aeba667`。

正式库 `/Users/example/project/rag/var/evidence-rag.sqlite3` 未被打开、哈希、checkpoint 或修改，本报告也
不声称其 bytes/mtime 不变。

## 6. 隔离启动与浏览器验收

隔离数据根位于 system tmp，服务在 `http://127.0.0.1:8000` 启动。验收包括：

- active Wiki generation 可读，Workbench 显示路径树、页详情、facts/links/source refs；
- 服务端搜索、筛选和分页；
- 智能查询 navigation trace、obligations、raw verify；
- Error Book 与 Builder 空/状态页面；
- desktop 三栏与 390×844 mobile 堆叠，无水平溢出；
- 页面无 console error、warning 或 framework overlay；
- 默认 `/v1/query` 仍为 V1；显式 `X-RAG-Wiki-Engine: wiki_v1` 才进入 Wiki；
- 命中问题返回 `supported` 和引用；不可回答问题返回 `insufficient_evidence/refusal`，reason 为
  `missing_required_role`，不调用生成器。

## 7. 外部延期与停止边界

以下不是仓库内继续打补丁可以诚实完成的事项：

- production-owned DB 的只读快照、migration/backfill；
- M/L 容量、真实并发、生产硬件和远程模型成本；
- production reviewed quality、真实用户 trace 和 Builder 长期 utility；
- shadow/canary、rollback observation、reviewed release registry；
- 默认 Wiki/V2 切换。

这些事项必须有数据所有者/生产发布授权和新的不可变 evidence。当前最终状态为：

`WIKI_RAG_REPOSITORY_ENGINEERING_COMPLETE / ENGINEERING_FIXTURE_NON_QUALIFIED / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`
