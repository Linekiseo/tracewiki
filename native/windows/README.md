# Evidence Wiki + RAG — Windows 原生客户端

这是独立 WinUI 3 / Windows App SDK 客户端，不加载 Web 页面，也不包含 WebView。首个垂直切片提供原生项目选择、可信查询及历史、Wiki 分页、关系图谱、研发会话、智能体和设置页面。

## 数据与安全边界

- 客户端只连接 `http://127.0.0.1:8765`，所有业务数据来自真实 RAG API；无演示项目、会话、Wiki 或图谱数据。
- 客户端不打开 SQLite 数据库，不管理 WAL/SHM；数据生命周期属于后端服务。
- LLM API Key 由 `Windows.Security.Credentials.PasswordVault` 持久化。注册时只把临时副本发送到本机后端，错误信息和日志不保留响应体或密钥。
- 取消页面或发起新请求会取消先前请求；列表页使用明确分页状态。

## Windows 构建

需要 Windows 11（或受支持的 Windows 10）、Visual Studio 2022 / .NET 8 SDK 和 Windows App SDK 工具链：

```powershell
pwsh .\ci\windows-build.ps1
```

脚本运行 Core 单元测试并分别编译 x64、arm64 WinUI 3 客户端，同时禁止任何 WebView 引用。macOS 只能运行 `ci/verify-structure.sh` 做结构与边界检查，不能替代 Windows 运行时构建结论。

## 当前垂直切片

| 原生页面 | 真实 API |
| --- | --- |
| 项目 | `GET /v1/projects` |
| 可信查询 | `POST /v1/query`、`GET /v1/query/history` |
| Wiki | `GET /v1/wiki/pages` |
| 关系图谱 | `GET /v1/graph` |
| 研发会话 | `GET /v1/codex/sessions` |
| 智能体 | `GET /v1/codex-bridge/agents` |
| 设置 | `GET/PUT /v1/ai/provider`、`POST /v1/ai/provider/test` |
