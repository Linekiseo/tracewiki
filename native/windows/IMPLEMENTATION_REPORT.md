# Windows 原生客户端第一批工程报告

## 结论

第一批是 WinUI 3 真原生垂直切片，不是 Tauri/WebView 包装。窗口、标题栏、Mica 背景、导航、页面、列表、输入、分页、进度和错误反馈均由 Windows App SDK 控件实现。

## 已实现

- WinUI 3 应用工程与独立可测试 Core 工程。
- 七个原生页面及统一项目上下文。
- `HttpClient` 真实 API 客户端，固定 loopback 边界、流式解码、取消传播、有限分页、无错误响应体泄漏。
- 查询请求绑定六类证据源，并在提交前固定已发布仓库、项目、活动代次、分支和提交；历史兼容数组和 envelope 两种服务合同。
- Windows Credential Locker 密钥保存、恢复、删除接口；设置 ViewModel 不持有可绑定 API Key 属性。
- MSTest 覆盖请求路径/JSON、解码安全、非 loopback 拒绝、项目状态、Wiki 分页、可信查询与 latest-request cancellation。
- Windows x64/arm64 构建脚本与无 WebView 结构门。

## 验证边界

当前开发主机是 macOS，因此不能宣称 WinUI 运行时构建或交互测试成功。已在临时 .NET 8 SDK 中实际编译 Core 与测试工程并运行 `11/11` 测试，格式检查通过；全部 XAML/csproj/manifest XML 可解析，七页双文件齐全，WebView 扫描为零，`git diff --check` 通过。WinUI 壳层仍以 `ci/windows-build.ps1` 的 Windows x64/arm64 结果为唯一构建验收依据。

| 验证 | 结果 |
| --- | --- |
| Core + MSTest（macOS/.NET 8 临时工具链） | 11/11 PASS |
| Core/Test `dotnet format --verify-no-changes` | PASS |
| XAML/csproj/manifest XML 解析 | PASS |
| 七个页面与 code-behind 结构 | PASS |
| `native/windows/src`、`tests` WebView 引用 | 0 |
| Windows x64/arm64 WinUI 运行时构建 | 待 Windows 脚本验收，不在 macOS 冒充通过 |

## 后续原生批次

第一批建立完整数据通路。后续仍应在 WinUI 内增量补充图谱画布的缩放/框选/聚类、Wiki 阅读详情、会话时间线与代码变更联动、智能体审批/执行、Windows 通知与安装签名；这些不应回退到 WebView。
