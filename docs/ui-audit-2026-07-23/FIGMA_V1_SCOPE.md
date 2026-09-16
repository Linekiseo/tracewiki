# 研发证据 RAG 前端重设计：Figma v1 范围

## 设计方向

- 产品骨架采用方案 1：保留清晰的固定导航、独立页面层级和三栏图谱工作台。
- 图谱细节采用方案 2：沉浸式画布、浮动命令区、可收起筛选栏、覆盖式节点详情。
- 所有核心界面提供 Light / Dark 两种主题；主题偏好持久化并跟随系统初始设置。
- 图谱从“证据工作台”的三级标签中移出，成为一级导航“图谱探索”。

## Code → Figma → Frontend 映射

| 现有代码对象 | Figma v1 对象 | 新前端职责 |
| --- | --- | --- |
| `.sidebar` / `.nav-item` | `App Navigation` | 一级导航、分组、折叠和当前页面状态 |
| `.topbar` / `.global-search` | `Top Command Bar` | 项目切换、全局检索、主题切换、帮助 |
| `.button` | `Button` | Primary / Secondary / Ghost / Danger；三种尺寸和交互状态 |
| 表单 `input/select` | `Field` | Search / Text / Select；默认、聚焦、禁用、错误状态 |
| `.panel` / `.metric` | `Surface` / `Metric` | 信息容器、健康状态、统计和空状态 |
| `.rag-graph-domain-tabs` | `Graph View Switcher` | 跨源总览 / 代码图谱 / 会话图谱 / 科研图谱 |
| `.rag-graph-categories` | `Graph Filter Chip` | 节点类型开关、计数和可见状态 |
| `.rag-graph-node` | `Graph Node` | 代码 / 会话 / 科研三域节点、选中、路径和固定状态 |
| `.rag-graph-inspector` | `Evidence Inspector` | 来源、关系、版本、验证、展开与打开原文 |
| 来源工作台列表 | `Source List Item` | 代码、会话、实验与文档使用统一交互骨架 |

## 双主题语义令牌

### Color

- `color/bg/canvas`
- `color/bg/surface`
- `color/bg/elevated`
- `color/bg/subtle`
- `color/bg/navigation`
- `color/text/primary`
- `color/text/secondary`
- `color/text/inverse`
- `color/border/default`
- `color/border/strong`
- `color/action/primary`
- `color/action/primary-hover`
- `color/focus/ring`
- `color/domain/code`
- `color/domain/codex`
- `color/domain/research`
- `color/status/success`
- `color/status/warning`
- `color/status/danger`

### Spacing / Radius

- Spacing: 4, 8, 12, 16, 20, 24, 32
- Radius: 6, 8, 10, 12, 16, full
- Shadow: low, medium, inspector

### Typography

- Product UI: `Noto Sans SC` in Figma; browser continues using the existing SF Pro / PingFang SC / Noto Sans SC fallback stack.
- Technical metadata: `JetBrains Mono`.
- Minimum production text size: 11px; ordinary labels 12–14px; page titles 24–28px.

## Figma v1 组件范围

- App Navigation
- Top Command Bar
- Button
- Icon Button
- Field
- Segmented Tabs
- Filter Chip
- Status Badge
- Surface / Panel
- Metric
- Graph Node
- Graph Edge Legend
- Evidence Inspector
- Source List Item
- Empty State

## Figma v1 页面范围

- Cover
- Getting Started
- Foundations / Color
- Foundations / Typography
- Foundations / Spacing
- Components
- Screens / Graph Explorer / Light
- Screens / Graph Explorer / Dark
- Screens / Code Source
- Screens / Codex Source
- Screens / Research Source
- Screens / Evidence Workbench

## 已解决的冲突

- 现有 Figma 文件为空：以生产代码和已选视觉方案为事实源，不存在旧 Figma 组件冲突。
- Simple Design System 与 Material 3 可用，但视觉语言、令牌和桌面密度与本产品不匹配：不直接套用其页面；仅作为组件结构与可访问状态参考。
- 现有 CSS 没有暗色主题：新增语义令牌层，保持原有蓝色品牌和三域颜色，同时补齐 Dark 模式。
- 现有图谱只有代码 / Codex / 文档标签：新结构合并文档与实验为“科研图谱”，并新增“跨源总览”。
- 现有图谱节点不能拖动：新交互要求节点拖动、固定、邻居展开、路径追踪、框选和画布平移互不冲突。

## 暂不进入 v1

- 不伪造后端尚不存在的跨源绑定关系。
- 不修改数据库与摄取流水线。
- 不为治理、漂移、关系复核等次要页分别创建完全不同的视觉体系；它们复用统一页面骨架。
