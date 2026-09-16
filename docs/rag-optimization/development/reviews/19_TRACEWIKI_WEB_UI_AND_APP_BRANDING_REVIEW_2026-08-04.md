# TraceWiki Web UI 与桌面品牌自测报告

日期：2026-08-04  
范围：正式 Tauri/Web UI、产品命名、ImageGen 图标、窗口布局、查询信息层级、桌面打包  
结论：`TRACEWIKI_WEB_UI_AND_APP_BRANDING_PASS`  
严重发现：`P0=0 / P1=0`

## 1. 产品与图标

- 正式产品名：`TraceWiki`；中文说明：`循证知识工作台`。
- 图标语义：折叠知识页、证据节点、检索路径，避免字母、聊天气泡和通用 AI 星芒。
- ImageGen 原始透明图保存在 `frontend/src-tauri/icons/tracewiki-icon.png`；Web 使用 256×256 优化副本，桌面安装包使用完整分辨率源图。
- Tauri 已生成 macOS `icon.icns`、Windows `icon.ico`/AppX 尺寸及其他标准尺寸；macOS App 的 `CFBundleName`、`CFBundleDisplayName` 与图标资源均为 TraceWiki。

## 2. 正式交互界面

- 正式 App 继续采用 Tauri + React Web UI；SwiftUI/WinUI 只作为平行增强线，不替换当前正式工作台。
- 应用外壳限制为窗口高度，内容区域独立滚动，避免页面把桌面窗口横向或纵向撑开。
- 左侧导航收窄；项目上下文完整展开时隐藏重复的图谱、Wiki、智能体全局入口，折叠或窄窗口时再提供快捷入口。
- 顶栏、对话框、长文本和技术元数据增加自适应边界与安全换行。
- 可信查询结果优先展示状态、回答与证据；规划、引擎路由、来源状态等技术信息收入可展开审计区，保留可核验性而不淹没主要任务。

## 3. 实际界面巡检

隔离服务：`http://127.0.0.1:8765`。未读取或修改正式服务库。

逐页检查项目总览、可信查询、Wiki、关系图谱、研发会话、智能体控制面：

- 六个工作区均非空且无框架错误覆盖层；
- 页面根容器在 1280×720 下 `scrollWidth == clientWidth`；
- TraceWiki 图标实际加载为构建哈希资源，natural size 256×256；
- 图谱超出视口的节点保持在可平移画布内，不再导致整个应用横向溢出；
- 项目上下文下“关系与影响网络”只有一个可见主入口；
- 查询审计信息使用原生 `details/summary` 合同，默认收起、可键盘与鼠标展开。

## 4. 自动化与桌面包

- 前端：31 files / 178 tests PASS；
- TypeScript typecheck：PASS；
- deterministic production build：PASS；
- Tauri Rust tests：5/5 PASS；
- `cargo check`：PASS；
- macOS App 构建：PASS；
- macOS App 本地 ad-hoc 资源封装与严格签名校验：PASS；
- `git diff --check`：PASS。

产物：`frontend/src-tauri/target/release/bundle/macos/TraceWiki.app`。

## 5. 保留边界

- Windows 已生成完整图标资产与 Tauri 构建配置，但本轮 macOS 环境不冒充 Windows 真机安装验收。
- SwiftUI/WinUI 原生界面仍是增强线；正式产品以当前 Web UI 的功能与交互为准。
- 本轮不改变 RAG 的 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE` 发布结论。
