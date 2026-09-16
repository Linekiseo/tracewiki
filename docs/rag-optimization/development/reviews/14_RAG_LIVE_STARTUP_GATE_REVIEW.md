# RAG LIVE STARTUP GATE 独立复核

日期：2026-07-31  
复核对象：共享 dirty `main` 的最终静止源码与 production Web bundle  
最终隔离根：`/tmp/rag-live-gate.LDlUwO`  
监听边界：`127.0.0.1:18081`  
后端进程：PID `81896`（复核后已退出）

## 1. 结论

RAG LIVE STARTUP ACCEPTANCE GATE PASS

P0 findings: 0

P1 findings: 0

最终静止版本可以在 production 配置下，从全新隔离数据根真实启动；health、OpenAPI、
release status、production index 与 hashed assets 均可经 HTTP 访问。通过 HTTP 创建或摄取的
code、Codex、workspace、experiment、notebook、document 六类最小数据均可被全局检索命中；
默认请求保持 V1，显式全源 V2 请求在当前发布权威不可用时通过 authoritative trace 明确回退
V1。前端最终 bundle 能优先读取 `trace.platform.source_engine_routing`，并在其缺席时读取
`trace.sources[*].release_route`，不会把 Codex / Experiment / Notebook / Document /
Workspace 的“请求 V2、执行 V1”伪显示成“请求 V1”。

本 Gate 不改变质量或发布结论：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`。

## 2. 严格边界

- 最终运行数据、Codex fixture、allowed roots 全部位于
  `/tmp/rag-live-gate.LDlUwO/{data,codex,allowed}`。
- Code 来源是 `/tmp/rag-live-gate.LDlUwO/allowed/minirepo` 的单文件、单提交最小 Git
  仓库；没有索引共享工作区。
- `RAG_PROJECT_ROOT=/tmp/rag-live-gate.LDlUwO/allowed/project`，
  `RAG_ALLOWED_LOCAL_ROOTS=/tmp/rag-live-gate.LDlUwO/allowed`，
  `RAG_CODEX_HOME=/tmp/rag-live-gate.LDlUwO/codex`。
- 正式库 `/Users/example/project/rag/var/evidence-rag.sqlite3` 及其 WAL/SHM 被视为
  `EXTERNAL_MUTABLE_SERVICE_OWNED`。本次没有 open、hash、checkpoint、copy、delete，
  也没有 stat sidecars；因此本报告不对正式库或 sidecars 的不变性作任何声明。
- 没有运行 C-B1..B5、formal-path tests 或任何会访问正式路径的测试。
- 没有修改实现、测试、配置、其他文档、evals、正式库或 Git；唯一仓库写入是本报告。

共享工作区曾在本次复核开始时进行一次已知的并发 production Web 重建。其清理窗口使一轮
预备启动看到过暂时缺失的 `web/index.html`；该轮随即有序关停并作废，没有纳入 Gate 结果。
待最终 bundle 静止且主控确认 build SHA 后，本报告使用新的 `mktemp` 根从零重跑全部启动、
摄取、查询和关停步骤。最终运行的所有 production static 请求均为 200。

## 3. 关键命令与进程边界

创建最终隔离根：

```bash
mktemp -d /tmp/rag-live-gate.XXXXXX
# /tmp/rag-live-gate.LDlUwO
```

真实 production 启动（测试 token 在进程退出后失效）：

```bash
env \
  -u RAG_LLM_BASE_URL -u RAG_LLM_API_KEY -u RAG_LLM_MODEL \
  -u RAG_MULTISOURCE_CALIBRATION_BUNDLE \
  -u RAG_MULTISOURCE_CALIBRATION_SHA256 \
  RAG_DATA_DIR=/tmp/rag-live-gate.LDlUwO/data \
  RAG_CODEX_HOME=/tmp/rag-live-gate.LDlUwO/codex \
  RAG_PROJECT_ROOT=/tmp/rag-live-gate.LDlUwO/allowed/project \
  RAG_ALLOWED_LOCAL_ROOTS=/tmp/rag-live-gate.LDlUwO/allowed \
  RAG_WEB_DIR=/Users/example/project/rag/web \
  RAG_DEPLOYMENT_MODE=production \
  RAG_API_TOKEN=rag-live-gate-token \
  RAG_ENFORCE_ACL=true \
  RAG_TRUSTED_ACL_REFS=project:project-rag \
  PYTHONUNBUFFERED=1 \
  .venv/bin/evidence-rag serve --host 127.0.0.1 --port 18081
```

实际启动日志：

```text
Started server process [81896]
Waiting for application startup.
Application startup complete.
Uvicorn running on http://127.0.0.1:18081
```

HTTP 操作均通过 `urllib` 的空 proxy handler 或 `curl --noproxy '*'` 访问 loopback，带
production bearer token 和 `X-RAG-ACL-Refs: project:project-rag`。主要路径如下：

```text
GET  /health
GET  /openapi.json
GET  /v1/rag/status
GET  /
GET  /app/index-sRXKfFYm.js
GET  /app/index-CLQjNCow.css
GET  /app/SearchPage-BNROIcRs.js
POST /v1/research/topics
POST /v1/research/iterations
POST /v1/research/work-items
POST /v1/experiments
POST /v1/experiments/runs
POST /v1/notebooks/ingest
POST /v1/documents/ingest
POST /v1/ingestion/repositories
GET  /v1/ingestion/workflows/{workflow_id}
POST /v1/ingestion/codex
POST /v1/search
POST /v1/query
```

显式全源 V2 的请求头：

```text
X-RAG-Engine: v2
X-RAG-Code-Engine: v2
X-RAG-Codex-Engine: v2
X-RAG-Experiment-Engine: v2
X-RAG-Notebook-Engine: v2
X-RAG-Document-Engine: v2
X-RAG-Workspace-Engine: v2
```

前端契约回归：

```bash
cd frontend
npm test -- src/features/search/queryContract.test.ts src/lib/queryApi.test.ts
# 2 files, 14 tests passed

npm test -- src/features/search/TrustedQueryPanel.test.tsx
# 1 file, 10 tests passed
```

## 4. 启动、状态与 production Web

| 检查 | 真实结果 |
|---|---|
| `GET /health` | 200，`status=ok`，`version=0.1.0` |
| `GET /openapi.json` | 200，117 paths，包含 `/v1/query` |
| production API 鉴权 | 无 token 的 `GET /v1/stats` 为 401；正确 token + ACL 为 200 |
| `GET /` | 200，917 bytes，引用最终 hashed JS/CSS |
| `/app/index-sRXKfFYm.js` | 200，226196 bytes |
| `/app/index-CLQjNCow.css` | 200，249281 bytes，`text/css` |
| `/app/SearchPage-BNROIcRs.js` | 200，37949 bytes，`text/javascript` |

最终 `web/build-contract.json`：

```text
buildSha256 = 21b9f585a893f4920ad1f7c29baa30d1f0d679e145e73527f60cc1d57a62a34e
```

对 `index.html`、entry JS、CSS、SearchPage chunk 独立重算的 byte size 与 SHA-256 均与
build contract 完全一致；SearchPage chunk SHA-256 为
`56b46cf0ddb3cd10a88a391ca3698614467b8ac29316cee88ea5c4b49fcc0f23`。

`GET /v1/rag/status` 的真实关键值：

```text
decision       = QUALITY_HOLD
default_engine = v1
quality_hold   = true
```

六来源各自均返回：

```text
v1_stage     = DEFAULT_V1
v2_stage     = NO_RELEASE
quality_state = QUALITY_HOLD
```

## 5. 六来源真实 HTTP 摄取

| 来源 | 操作与结果 |
|---|---|
| Workspace | topic、iteration、work-item 各 201 |
| Experiment | experiment 与 completed run 各 201，含 `live_gate_accuracy=0.91` |
| Notebook | `/v1/notebooks/ingest` 201，2 cells |
| Document | `/v1/documents/ingest` 201，抽取 1 claim |
| Code | repository enqueue 202；`wf-d3ac8128-...` 最终 `completed`、`error=null` |
| Codex | ingestion enqueue 202；`wf-codex-2dfc991b-...` 最终 `completed`、`error=null` |

全局 `/v1/search` 查询 `live gate hybrid retrieval quality` 返回 200、13 条结果，结果来源集合
精确覆盖：

```text
code, codex, document, experiment, notebook, workspace
```

这证明六类 fixture 都不是仅“创建成功”，而是已进入可用检索路径。

## 6. 默认 V1 与显式 V2 回退

默认 `/v1/search` 与真实 `/v1/query` 均为 200。`/v1/query` 的 retrieval context source
counts 为：

```text
code=2, codex=3, document=2, experiment=3, notebook=1, workspace=2
```

未配置 LLM 时回答为 `retrieval_only`；全局综合策略因证据角色不足返回 fail-closed
`state=refused`，但 Evidence Pack 已完整包含六来源计数与证据。此行为是回答安全策略，不是
摄取、检索或启动失败。

显式全源 V2 的 `/v1/search` 与 `/v1/query` 也均为 200，且全局 authoritative trace 为：

```text
requested         = v2
selected          = v1
served            = v1
fallback          = legacy_v1
quality_qualified = false
```

Codex / Experiment / Notebook / Document / Workspace 的
`trace.platform.source_engine_routing` 均为：

```text
requested = v2
selected  = v1
served    = v1
```

同五来源的 nested `release_route` 均为：

```text
stage            = off
requested_engine = v2
selected_engine  = v1
response_engine  = v1
served_engine    = v1
fallback_reason  = authority_unavailable
authority_sha256 = null
```

Code 使用其既有 `trace.sources.code.routing` 契约：`engine_requested=v2`、
`engine_selected=v1`、`selection_reason=fallback`、fallback status `succeeded`、reason
`scope_unavailable`。全局发布 trace 仍明确 `served=v1`，没有将 opt-in 当成发布晋级。

## 7. Production bundle 的 UI / trace 契约

最终 TypeScript 类型显式声明：

- `QueryTrace.platform.source_engine_routing`；
- `QuerySourceEngineTrace.release_route`；
- `requested_engine / selected_engine / served_engine / fallback_reason / reason_code`。

`queryEngineRouting()` 的合并优先级是 source legacy fields → source routing → nested
`release_route` → authoritative `platform.source_engine_routing`。因此真实 HTTP response 中
legacy `engine_requested=v1` 不会覆盖权威 `requested=v2`；authoritative route 缺失时，nested
release route 仍可正确呈现。

源码回归分别覆盖 platform-only 与 release-route-only 两种响应；14 项 query contract/API
测试全部通过。`TrustedQueryPanel` 的 10 项 DOM 测试也通过，并验证页面展示“请求与实际执行
引擎”、fallback 与 blocker。最终 served SearchPage chunk 中存在同一提取与优先级逻辑，
页面渲染为“请求 v2 → 执行 v1”，而不是伪显示“请求 v1”。

## 8. 有序关停

向 PID `81896` 发送一次 SIGINT 后日志为：

```text
Shutting down
Waiting for application shutdown.
Application shutdown complete.
Finished server process [81896]
```

关停后的独立检查：

```text
port_closed=true
pid_exited=true
post_shutdown_curl_exit=7
```

即 `18081` 无监听者，PID 不存在，loopback health 连接失败；没有残留 Gate 服务进程。

## 9. Findings 与外部项

- P0：0。
- P1：0。
- L4/L5、真实生产流量、外部人工发布权威与长期线上 SLO 证据不在本地工程启动 Gate 的授权
  范围内；它们继续作为外部发布项保留，不计为工程缺陷，也没有被本报告伪装成已完成。
- 本次 PASS 仅接受当前 production 启动、六来源 HTTP 可用性、默认 V1、显式 V2 可观察回退、
  最终 Web bundle/UI 契约和有序关停；不授权 V2 release 或默认切换。
