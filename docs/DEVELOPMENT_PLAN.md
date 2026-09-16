# 多源研发证据 RAG 平台完整开发计划

本计划以《多源研发证据RAG系统_项目设计文档_v1.0》及 `rag_ui_v2` 的 20 张高保真页面为唯一验收基准。实现状态不再以“存在数据表或接口”为完成标准。

## 执行状态（2026-07-23）

- 阶段 A：进行中。文档候选复核、全类型证据选择、解绑历史、TableCell→Metric/Run、多 Run 聚合、Codex 会话对照、代码—RAG 双向工作台，以及代码/Codex/文档三类图谱工作台已完成；文档原文并列查看和非代码证据的完整双向回跳仍待验收。
- 阶段 B：未开始。
- 阶段 C：未开始。
- 阶段 D：未开始。
- 阶段 E：未开始。
- 阶段 F：未开始。

每项“完成”均以 API、持久化、审计、回归测试和浏览器操作同时通过为准，不能用静态页面或演示数据替代。

## 完成定义

一个功能只有同时满足以下条件才标记为完成：

1. 真实来源数据能够接入，且保留 Raw Object、Source Event、版本、ACL 和派生血缘。
2. 领域实体、确定性关系、推断关系及证据状态符合设计文档。
3. API 支持创建、查询、复核、删除/失效传播和错误语义。
4. Web 工作台具有可发现入口，能够完成主要操作并回跳原始证据。
5. ACL 在检索和详情解析之前生效，写操作进入审计。
6. 单元测试、API 集成测试、跨源端到端测试和浏览器联调通过。
7. README、OpenAPI 和本计划同步更新，不用“后续可扩展”代替设计中的 P0/P1 能力。

## 当前差距审计

| 设计域 | 当前真实状态 | 未完成闭环 |
|---|---|---|
| 原始层与摄取 | 已有不可变原文、幂等事件、隔离、Tombstone、Generation、异常退出恢复和失败任务重试 | Rebuild API、取消、权限变更任务、派生摘要重建 |
| 代码证据 | 已有 Git 仓库、精选历史、DiffHunk、Symbol、CALLS/IMPORTS、TestResult | Tag、PR/Issue/Review、CI 事实、按需历史物化、SCIP/LSP 精确引用、依赖影响闭包 |
| Codex 会话 | 已有 Thread/Turn/Item/Episode/Patch/Validation、Patch→Commit 候选、多会话持久化对照 | App Server 流式适配、Approval/Failure 完整状态、Episode 人工修订、Decision/Alternative 权威结构化 |
| 实验与 Notebook | 已有 Experiment/Run/Metric/Artifact、MLflow、Notebook 结构与 Run 对照 | DVC/OpenLineage、数据与环境版本实体、日志证据、完整复现包、Notebook Artifact 下载定位 |
| 文档与 Claim | 已有多格式结构、Claim/Run 复核、全证据选择、解绑历史、TableCell→Metric/Run 和多 Run 聚合 | Figure/Formula 深层绑定、人工表格校正、原文并列查看、聚合编辑/撤销 |
| 查询与 Evidence Pack | 已有 9 类 Intent、分源召回、Scope、角色、反证、缺失证据、引用；问答显示原问题、系统理解、范围、模式和依据片段；代码—RAG 双栏已按 Repository/Commit/行号同步 | 历史时间点解析、关系模板、完整冲突裁决、复现包查询、实验/时间线同步区、导出 |
| 漂移与冲突 | 已有 Commit 祖先链与 DiffHunk 风险判断 | Config/Dataset/Environment 差异、依赖闭包、当前复验 Run、Contradicted/Superseded 规则、复验任务 |
| 安全与治理 | 已有 ACL 前置过滤、Secret 隔离、基础审计、删除阻断 | 策略模拟、拒绝访问审计、配置版本、导出审计、细粒度继承解释、保留策略 |
| 评测与观测 | 已有 Golden Case、路径/Commit/版本/ACL 指标 | 30–50 条项目基线、MRR/nDCG/Citation Precision、冲突与时效指标、版本对比门禁、完整 Trace waterfall |
| Web 工作台 | 9 个一级工作台按科研流程、研发资产、证据治理、平台控制重新分组；技术台账已退出一级导航；证据工作台已提供代码、Codex 会话、文档三图谱及关系/语义双模式 | 对照 `rag_ui_v2` 补齐 20 个完整流程，尤其文档原文并列、完整证据链和权限审计 |

## 2026-07-23 系统一致性修复验收

本轮先修复会阻断后续功能建设的入口归属、代码—RAG 联动和真实摄取生命周期：

| 功能入口 | 唯一职责 | 已验收主操作 |
|---|---|---|
| 代码仓库 | Git/本地仓库与代码 Generation | 新增、同步、失败重试、进度、Git 历史、文件、Symbol |
| 证据工作台 | 固定 Scope 的回答与代码核验 | 仓库同步、真实查询、文件/行号自动定位、证据/关系切换、可调分栏 |
| 实验与数据 | 可复现实验事实和入口 | MLflow、Notebook、手工 Run、Dataset/Config/Metric/Artifact |
| 科研文档 | 文档原文结构与 Claim | 文档接入、Claim 候选、证据绑定和数值检查 |
| 治理与运维 | 技术来源与索引运行状态 | Raw Object、Source Event、Generation、审计和评测 |

同时完成：

- 仓库与 Codex 工作流增加明确 `kind`，列表不再互相污染。
- 远程克隆目录移出热重载监听范围，避免克隆过程终止后台任务。
- 启动时收敛异常退出遗留的 Workflow、Generation 和 Repository 状态。
- Git 超时/失败返回可操作错误，失败任务可由原请求创建新的重试任务。
- 重载后的前端资源显式版本化；代码/数据源工作台逻辑从主脚本拆出，避免继续累积。
- 导航按科研流程、研发资产、证据治理、平台控制重组；旧的“实验数据接入”和
  “原文与数据源”路由分别回收到实验/文档上下文，Raw 台账下沉治理。
- 代码仓库选择与证据工作台 Scope 使用同一状态；查询命中的 CodeSymbol 自动
  打开同一 Repository、Commit、File 和行号，分栏比例可拖拽、键盘调整并持久化。
- Symbol 重载/同名定义使用声明签名区分稳定 URI，真实
  `FalkorDB/code-graph` 已发布 126 Files、524 Symbols、1,179 Edges，
  并检索到 `SourceAnalyzer` 及其 `CALLS` 边。
- 证据工作台新增三类知识图谱。当前项目真实索引为代码 57,206 个实体 /
  145,197 条关系、Codex 会话 2,506 个实体 / 3,499 条关系；文档尚未接入，
  因此文档图谱明确显示 0 而不生成演示节点。语义模式将向量命中与真实关系分线展示。
- 问答结果新增“原问题、系统理解、查询范围、回答模式、命中来源和依据片段”，
  未启用生成模型时不再把检索摘要包装成无来源的自然语言答案。

## 开发阶段与退出标准

### 阶段 A：补齐当前断链

- 文档绑定工作台：候选扫描、候选解释、确认/拒绝、任意证据类型绑定、解绑和历史。
- 表格单元格、Claim、Metric、Run 的确定性/候选关系。
- Codex 会话多选和对照：Goal、Decision、命令、文件、Patch、验证、Commit、未解决事项。
- 从文档、会话、Binding、Run 双向跳转原始证据。

退出标准：`Document → Claim → TableCell → Metric/Run` 与 `Thread → Patch → Commit` 可在 UI 中双向核验；会话对照结果可保存、重载并引用。

### 阶段 B：确定性四方证据链

- 引入 ChangeSet/ChangeUnit、Decision/Alternative、DatasetVersion、ConfigSnapshot、EnvironmentSnapshot。
- Git Tag、PR、Issue、Review 和 CI/Test Adapter；按需物化被 Run/Claim 引用的历史 Commit。
- DVC/OpenLineage Adapter 与 Run 复现包。
- Episode 人工修订保留原始版本和审计记录。

退出标准：会话→代码→实验→文档链路的每一跳均有证据、版本、ACL、推导方式和复核状态。

### 阶段 C：完整查询与 Evidence UI

- branch/tag/commit/as-of 解析和版本不一致拒答。
- 查询相关权威度、关系模板、证据预算、支持/反证/缺失完整裁决。
- 五区同步界面：回答、证据侧栏、代码/Diff、实验、时间线。
- 证据链详情、关联图谱、Evidence Pack 导出和稳定引用回跳。

退出标准：设计文档的 8 类代表性查询均能返回正确适用范围、证据路径、冲突和缺失项。

### 阶段 D：失效、冲突与复验

- Claim 关键 Symbol、配置键、数据属性、指标定义和环境约束。
- Diff 依赖闭包、配置/数据/环境差异及当前复验 Run 检测。
- valid / potentially_stale / contradicted / superseded / insufficient_evidence 全状态机。
- 复验任务、风险解释、状态变更事件和审计。

退出标准：每个漂移结论都展示变更实体、原始 Run、当前版本、反证和建议验证动作，不能把 Diff 直接等同于失效。

### 阶段 E：治理、评测与运维闭环

- 摄取 Rebuild、Retry、Cancel、版本化工作流与删除完成状态。
- 项目配置版本、Ontology/来源/检索/引用规则发布和回滚。
- ACL 策略模拟、继承路径、拒绝访问、导出和高敏访问审计。
- 30–50 条真实 Golden Questions 基线及版本对比质量门禁。
- 完整 Query Trace、P50/P95、索引新鲜度、错误率和成本字段。

退出标准：Unauthorized Evidence Leakage 和 Secret Leakage 测试为 0；错误版本、引用和证据路径指标达到设计阈值。

### 阶段 F：20 页面产品化验收

- 将 `rag_ui_v2` 的 20 张效果图逐页映射到真实 API 和真实状态。
- 补齐空态、加载、失败、权限拒绝、冲突、删除和长列表交互。
- 完成键盘导航、基础可访问性、桌面分辨率和浏览器回归。
- Docker/CLI/OpenAPI/环境配置/示例数据/迁移文档同步。

退出标准：20 个页面均无静态演示数据，关键按钮均有真实行为；全量测试、浏览器联调和安装启动验收通过。

## 当前执行顺序

1. ~~文档候选绑定与全证据类型工作台。~~
2. ~~Codex 会话对照 API、持久化和 UI。~~
3. ~~TableCell→Metric/Run 与多 Run 聚合。~~
4. ~~代码仓库、实验与数据、科研文档、运维台账职责拆分与仓库失败恢复。~~
5. ~~代码—RAG 双栏、Repository Scope 同步、Evidence 行号定位与可调分栏。~~
6. 原文并列查看、非代码证据双向回跳和完整证据链详情；~~代码/Codex/文档关联图谱。~~
7. 复现包、完整漂移状态机和复验任务。
8. 摄取运维、权限审计、项目设置和评测门禁。
9. 20 页面逐页验收及最终发布检查。

每完成一个阶段，必须更新本文件的差距表、增加相应回归测试并重新执行浏览器验收。
