# 多源研发证据 RAG 平台

[![Project CI](https://github.com/Linekiseo/tracewiki/actions/workflows/project-ci.yml/badge.svg)](https://github.com/Linekiseo/tracewiki/actions/workflows/project-ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**TraceWiki（循证知识工作台）** 是一个开源的、版本感知的研发证据 RAG 与 Agent-native Wiki 工作台。
当前公开版本是工程预览版：默认路径仍保持 `DEFAULT_V1`，文档中标记为 `QUALITY_HOLD` 的生产资格结论不会因
代码开源而自动升级。

这是一个可本地运行的多源研发证据平台：把代码仓库、Codex 会话、研究迭代、实验结果和论文/报告中的 Claim 统一转换为**版本化实体、关系边与可引用证据**，并提供跨源检索、Lineage、版本漂移、评测和审计闭环。

代码接入不是字符窗口式“切块器”。平台以 Repository、Commit、FileVersion、CodeSymbol 为主键，用 Tree-sitter 抽取 Symbol 边界和结构关系；其他来源也使用稳定 URI 和明确的证据定位器。推断关系与确定事实分开存储，需要人工确认的绑定不会伪装成已经提交或已经验证的事实。核心能力是否真实接通、当前边界和可复现规模见 [核心业务能力审计](docs/CORE_CAPABILITY_AUDIT.md)。

项目按设计文档分阶段交付。页面职责和跨页面规则见 [产品信息架构](docs/PRODUCT_INFORMATION_ARCHITECTURE.md)，当前完成情况、未完成差距和逐阶段退出标准见 [完整开发计划](docs/DEVELOPMENT_PLAN.md)。README 中的“已实现”仅代表已经通过回归测试的能力，不代表 20 个设计页面已经全部验收。

当前版本的统一说明见 [RAG 项目完整手册](docs/project-handbook/README.md)。当前技术权威是
[Evidence-Compiled Agent-Native Wiki + RAG 技术报告](docs/rag-optimization/development/07_RAG_END_TO_END_DEVELOPMENT_ROADMAP_AND_TECHNICAL_REPORT.md)，
[App Wiki + RAG 智能查询架构](docs/design/APP_WIKI_RAG_INTELLIGENCE_ARCHITECTURE.md) 说明查询理解算法、融合公式、LLM 密钥边界和桌面端交互，
面试口径见 [Wiki + RAG 项目面试 QA](docs/rag-optimization/interview/08_RAG_PROJECT_INTERVIEW_QA.md)，
产品交互见 [产品与交互设计文档](docs/project-handbook/02_PRODUCT_AND_INTERACTION_DESIGN.md)。阶段性设计、Gate 与自测报告继续作为底层审计证据保留。

面向下一阶段的 RAG 质量优化，见 [多源研发证据 RAG 优化设计文档集](docs/rag-optimization/README.md)。该方案按“单源专项优化 → 多源联合检索 → 整体 RAG 打磨”设置验收门，并同步维护可复现的简历与面试证据。

## 已实现能力

- Evidence-Compiled Agent-Native Wiki：六源 Compiler、路径化 page/fact/link/source-ref、独立 SQLite generation、ACL visibility fragment、原子发布/回滚和 portable verifier。
- Wiki 混合检索与 Agentic Navigator：exact、FTS5/BM25、可替换 dense、structural、RRF、可替换 reranker，以及 obligation 驱动的 search/read/follow/raw-verify。
- 智能查询：结构化 LLM 语义规划 + 冻结 TF-IDF 词/字符 n-gram 质心分类兜底、意图/来源 posterior、证据义务分解、多查询 RRF、Evidence Pack、结构化 claim/citation verifier 和正确拒答；默认 V1 和质量 HOLD 保持不变。
- Error Book 与受审 Builder：失败归因、content-addressed patch、affected/guard utility 和安全 hard gates，在线请求不能越权发布。
- Codex/MCP 集成：`research-project-bridge` plugin、四个 Agent skills、21 个 MCP tools 和受治理的 Wiki 导航/证据/patch 工具。
- TraceWiki App Workbench：当前正式客户端使用 Tauri 承载同一套成熟 React 工作台，以保持 Wiki、查询、图谱、会话和智能体交互的一致性；macOS SwiftUI 与 Windows WinUI 3 客户端继续作为原生增强线维护，达到 Web 工作台功能与交互覆盖后再切换。API key 只进入 OS credential vault，RAG 服务继续复用同一受治理 API。
- 本地目录、Git 工作区和远程 Git URL 接入。
- Python、JavaScript/TypeScript、Java、Go、Rust、C/C++、C#、Ruby、PHP、Swift、Kotlin、Scala 等 Tree-sitter 解析入口。
- Repository → Commit → FileVersion → CodeSymbol 稳定 URI。
- 可配置深度的 Git Commit、父提交、Branch、DiffHunk 与 TestResult 索引；历史同步可独立重跑。
- Symbol 原文切分，以及静态 CALLS / IMPORTS 关系解析。
- Content-addressed Raw Immutable Store、幂等 Source Event、Raw→派生实体血缘和 Tombstone 传播。
- SQLite Generation 快照：构建失败不覆盖上一个已发布索引。
- FTS5 词法召回 + 兼容旧索引的 `local-hash-v2` 本地特征向量 + Symbol/路径信号融合；Scope 内向量全量参与，不设置静默 candidate 上限。
- `/v1/evidence/search`、`/v1/query`、实体、文件和摄取状态 API。
- 可操作 Web 控制台：代码仓库页独立完成新增、重同步、失败重试、工作流、
  Git 历史、文件与 Symbol 浏览；仓库任务与 Codex 摄取任务按领域隔离展示。
- 高置信私钥、AWS Access Key、GitHub Token 文件隔离。
- Codex rollout JSONL 与公开非交互 JSONL 事件标准化；按 cwd/workspace root 做项目级筛选。
- CodexThread → CodexTurn → UserGoal / CommandExecution / Patch / ToolResult 实体与关系。
- 只把带真实退出码的测试、lint、typecheck 命令固化为 ValidationResult；助手文字不会被误当成验证事实。
- 每个 Turn 生成可检索 Development Episode，并提供专用会话混合检索与 Evidence Locator。
- 内部 reasoning/agent_reasoning 不入库；会话中的高置信 Token、Key 与私钥在写入前脱敏。
- Web 控制台支持会话同步、列表过滤、Turn 时间线、会话 Evidence Pack 与结果回跳。
- Codex 会话支持 2–8 个 Thread 的持久化对照，比较 Goal、候选 Decision、命令、文件、验证和 Commit，并保留基线差异。
- Project → Topic → Iteration 研究工作区，支持阶段推进、证据挂接和跨源关系复核。
- 确定性研究运营判断：`/v1/workspace/intelligence` 自动汇总当前研究阶段、任务状态、
  证据来源覆盖、索引与数据源健康、人工审批、版本漂移和阻塞项，返回最多 8 个按
  优先级排序的下一步行动；结果同步进入项目工作台、Codex MCP 上下文和执行快照，
  不要求用户重复填写系统已经掌握的信息。
- Codex Patch / FileChange 到当前代码 FileVersion / Symbol 的候选扫描、置信信号和人工确认；Commit 只作为上下文，不自动声称 Patch 已提交。
- Experiment、Run、Metric、Artifact 建模，真实 MLflow FileStore/HTTP Tracking Server 同步，以及控制变量、配置差异与指标差异比较。
- `.ipynb` 结构化接入：Cell、参数、执行序号、输出、错误与 Artifact 类型，并支持版本比较。
- Markdown、HTML、PDF、DOCX 与纯文本接入，保留 Section/Page/Table/Cell/Figure/Citation 结构；支持 Claim/Run 候选复核、全类型证据选择、解绑历史与数值一致性验证。
- TableCell→Metric→Run 候选保存可解释信号并由人工确认；多个 Run 汇总时记录聚合函数、样本数、方差、排除项、容差和验证状态。
- `/v1/query` 识别 9 类研发意图，解析 branch/commit/as-of Scope，分源召回后构建带角色、反证、缺失证据、确认关系一跳扩展和 citation map 的 Evidence Pack；未配置 LLM 时仍返回带引用的确定性问题分析。
- `/v1/graph` 与 `/v1/graph/stats` 提供代码、Codex 会话和科研文档三类真实图谱；
  关系模式展示确定性来源边，语义模式把查询向量命中标为独立的
  `SEMANTIC_MATCH` 虚线，并保留命中实体之间的真实关系。
- 全项目代码—RAG 工作台：无需预先选择仓库，默认同时检索代码、会话、实验和
  科研文档；仓库、问题类型和来源只作为可选高级筛选。左侧用
  `Repository → Directory → File` 层级树浏览全部已发布代码，右侧分层查看
  回答摘要、证据来源和关系链；代码证据会自动展开对应仓库并定位 Commit、文件
  与行号。左右区域支持拖拽、键盘微调和代码/回答聚焦，比例在本地保存。
- 问答结果明确展示原始问题、系统意图解释、查询 Scope、回答模式、来源覆盖与
  依据片段；知识图谱页可切换代码图谱、Codex 会话图谱和文档图谱，查看稳定
  抽样子图、向量语义邻域、节点详情并回跳原始来源。图谱交互参考
  `FalkorDB/code-graph` 的全画布力导向方式，提供实体类型筛选、右键节点菜单、
  相邻节点聚焦、两点路径、隐藏/恢复、力导向/分层切换、缩放、居中和 SVG 导出。
  画布明确区分全量索引规模与当前 24/36/60 节点可交互视图，并使用碰撞消解和
  双行语义标签保证节点名称不被圆形节点裁切。
- 跨来源 Lineage 遍历，以及基于已索引 Git 祖先链和 DiffHunk 的 Claim 版本漂移检查。
- Golden Questions 回归评测：来源/实体/路径召回、Commit 准确率、错误版本率、未授权泄漏、引用完整率和延迟。
- 查询观测、项目分类与检索策略、所有平台写操作的 actor / endpoint / status / trace 审计。
- 9 个一级 Web 工作台：项目、证据、代码与版本、研发会话、实验与数据、
  科研文档、关系复核、有效性分析、治理与运维。MLflow、Notebook 和手工 Run
  是实验中心的上下文入口；Raw Object、Source Event 和来源健康下沉到运维，
  不再与科研资产并列。
- 开发态热重载只监听 `src/` 与 `web/`；远程仓库克隆到 `var/repositories/`
  不会重启服务或中断后台索引。服务启动时会把上次异常退出遗留的任务和
  Generation 明确收敛为失败，允许从界面重试。
- 无 LLM 时使用本地统计查询理解并返回 retrieval-only；配置 OpenAI-compatible provider 后启用结构化语义规划与 grounded generation。API key 由原生 App 写入 OS credential vault，后端只在进程内持有。

## TraceWiki 桌面应用

当前桌面产品名为 **TraceWiki（循证知识工作台）**。正式 App 保留 Web UI，通过 Tauri 封装为 macOS/Windows 桌面程序；Web 页面仍是开发调试入口，不作为单独发布产品。SwiftUI/WinUI 3 工程位于 `native/`，作为后续原生增强实现保留。

构建当前正式桌面 App：

```bash
cd frontend
npm ci
npm run typecheck
npm test -- --run
npm run desktop:test
npm run desktop:build
```

原生增强线仍可独立验证：

```bash
make native-macos-test
make native-windows-structure
```

启动 App 后，在“桌面安全与智能模型”中配置 provider URL、model 和 API key。公开配置位于 App 配置目录；key 只进入 macOS Keychain / Windows Credential Manager，Web UI、项目数据库和证据库均不会保存或回显 key。

## 快速启动

需要 Python 3.12 或 3.13、Git 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --extra test
cp .env.example .env
uv run evidence-rag serve --reload
```

这是后端开发启动方式；正常用户从原生 App 进入。API 文档位于本地服务的 `/docs`。

默认只允许接入服务启动目录之下的本地路径。如需接入其他目录：

```bash
RAG_ALLOWED_LOCAL_ROOTS=/workspace,/Users/me/src uv run evidence-rag serve
```

也可以直接使用 CLI：

```bash
uv run evidence-rag index /absolute/path/to/repository
uv run evidence-rag index https://github.com/FalkorDB/code-graph.git --branch main
uv run evidence-rag search "哪里实现了调用关系解析"
uv run evidence-rag index-codex --project-path /absolute/path/to/project
uv run evidence-rag search-codex "哪次会话修改了 rerank 并执行验证"
```

打开控制台的“Codex 会话”，点击“同步会话”即可读取默认的 `~/.codex/sessions/**/*.jsonl`。默认项目路径是服务启动目录，也可通过环境变量覆盖：

```bash
RAG_CODEX_HOME=~/.codex
RAG_PROJECT_ROOT=/absolute/path/to/project
```

Adapter 只扫描会话 JSONL 和可选的 `archived_sessions`，不会读取 `auth.json`、全局历史或日志。对于大型会话，先读取小段元数据匹配项目，再完整解析命中的文件；超过 `RAG_MAX_CODEX_SESSION_BYTES` 的文件会安全跳过并计数。

## API 示例

创建异步摄取任务：

```bash
curl -X POST http://127.0.0.1:8000/v1/ingestion/repositories \
  -H 'Content-Type: application/json' \
  -d '{
    "source": "/absolute/path/to/repository",
    "project_id": "project-rag",
    "acl_ref": "project:project-rag",
    "ignore": ["fixtures", "generated"],
    "history_depth": 200
  }'
```

查询任务并检索证据：

```bash
curl http://127.0.0.1:8000/v1/ingestion/workflows/wf-...
curl 'http://127.0.0.1:8000/v1/ingestion/workflows?kind=repository&limit=20'

curl -X POST http://127.0.0.1:8000/v1/ingestion/workflows/wf-.../retry \
  -H 'Content-Type: application/json' -d '{}'

curl -X POST http://127.0.0.1:8000/v1/evidence/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "hybrid retrieval score fusion",
    "scope": {"repository_ids": []},
    "limit": 10,
    "include_edges": true
  }'
```

同步并检索 Codex 会话：

```bash
curl -X POST http://127.0.0.1:8000/v1/ingestion/codex \
  -H 'Content-Type: application/json' \
  -d '{
    "project_path": "/absolute/path/to/project",
    "project_id": "project-rag",
    "include_archived": false,
    "max_sessions": 200
  }'

curl http://127.0.0.1:8000/v1/codex/sessions

curl -X POST http://127.0.0.1:8000/v1/codex/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "哪次会话修改了 rerank 并执行验证",
    "scope": {"item_types": ["DevelopmentEpisode", "CommandExecution"]},
    "limit": 10,
    "include_edges": true
  }'
```

`POST /v1/query` 始终先创建 Evidence Pack。推荐从 App 设置配置 provider；OS credential vault 保存 key，native host 启动时通过 `/v1/ai/provider` 注册到后端进程内存。以下环境变量只用于 headless/开发环境回退：

```bash
RAG_LLM_BASE_URL=https://provider.example/v1
RAG_LLM_API_KEY=...
RAG_LLM_MODEL=...
```

完整 API 契约可在 `/docs` 查看。平台新增入口按领域划分：

```text
/v1/projects, /v1/research/*       项目、Topic、Iteration、证据挂接
/v1/workspace/intelligence         阶段、阻塞、证据覆盖与下一步行动
/v1/ingestion/events, /ingestion/raw/* Source Event、不可变原文、删除传播
/v1/code/history, /code/test-results   Commit、DiffHunk、测试事实
/v1/bindings/*                     Codex ↔ Code 候选绑定与复核
/v1/codex/comparisons              Codex 多会话持久化对照
/v1/experiments/*                  Experiment、Run、Metric、Artifact、MLflow
/v1/notebooks/*                    Notebook 结构证据与版本比较
/v1/documents/*                    文档结构、Claim、TableCell/Metric 匹配、聚合与验证
/v1/query, /v1/search, /v1/lineage 意图编排、跨源检索与关系谱系
/v1/wiki/*                         Wiki 状态、搜索、导航、页面、原始证据、Error Book、Builder 和 generation 管理
/mcp                               Codex/Agent MCP 0.2.0 工具和资源入口
/v1/graph, /v1/graph/stats       代码、Codex、文档关系图与语义邻域
/v1/drift/*                        版本漂移扫描与评估
/v1/evaluation/*                   Golden Questions 与回归结果
/v1/audit/events                   写操作审计
```

## 数据与检索模型

```text
Repository
  └─ HAS_COMMIT → Commit / Worktree Snapshot
       └─ CONTAINS → FileVersion
            ├─ DEFINES → CodeSymbol
            └─ IMPORTS → FileVersion
                 CodeSymbol ─ CALLS → CodeSymbol

CodeSymbol / FileVersion
  └─ Search View → FTS5 + versioned local vector
                       └─ Hybrid fusion → Evidence + locator + one-hop edges

CodexThread
  └─ HAS_TURN → CodexTurn
       ├─ HAS_ITEM → UserGoal / AgentMessage / CommandExecution / Patch / ToolResult
       └─ HAS_EPISODE → DevelopmentEpisode
                            └─ FTS5 + Local Vector → Session Evidence Pack

Project
  ├─ HAS_TOPIC → ResearchTopic ─ HAS_ITERATION → ResearchIteration
  ├─ HAS_EXPERIMENT → Experiment ─ HAS_RUN → Run ─ REPORTS → Metric / Artifact
  └─ HAS_DOCUMENT → Document ─ HAS_SECTION → Section ─ STATES → Claim

Patch / FileChange ─ CANDIDATE_BINDING → FileVersion / CodeSymbol
Claim ─ SUPPORTED_BY / CONTRADICTED_BY → Run / Metric / Code / Codex Evidence
Claim ─ SUPPORTED_BY → TableCell ─ DERIVED_FROM → MetricAggregation ─ AGGREGATES → Metric
ExperimentRun ─ REPORTS → Metric

RawObject ─ DERIVED_INTO → 任意派生实体
Commit ─ HAS_DIFF_HUNK → DiffHunk; Commit ─ VALIDATED_BY → TestResult
NotebookRun ─ HAS_CELL → NotebookCell ─ PRODUCED → NotebookOutput

All sources ─ Normalized Search → Evidence Pack ─ Lineage / Drift / Evaluation
```

稳定 ID 示例：

```text
repo://github.com/acme/rag-core
git://github.com/acme/rag-core/commit/31ab90...
code://github.com/acme/rag-core@31ab90/src/retriever.py
code://github.com/acme/rag-core@31ab90/src/retriever.py#symbol=src.retriever.search
```

本地 Dirty Worktree 不会伪装成纯 Git Commit，版本会记录为：

```text
<base-sha>+dirty.<manifest-hash>
```

## 目录结构

```text
src/evidence_rag/
  api.py / runtime.py          组合根、依赖装配、静态控制台
  repository.py / parser.py   Git 安全边界与 Tree-sitter 代码解析
  ingestion.py / retrieval.py 代码 Generation 发布与混合召回
  codex_*.py                   Codex 发现、脱敏、摄取与检索
  workspace/                   Project、Topic、Iteration、关系、审计与研究运营判断
  bindings/                    Codex ↔ Code 候选生成与人工复核
  experiments/                 Experiment、Run、Metric、Artifact、比较
  documents/                   Document、Section、Claim、表格数值链与验证
  codex_comparisons/           多会话快照、差异计算与持久化
  platform/                    跨源检索、Evidence Pack、Lineage、Drift
  query/                       意图识别、Scope 解析与证据编排
  sources/                     Raw Object、Source Event、Tombstone
  code_history/                Commit、Branch、DiffHunk、TestResult
  notebooks/                   Notebook Cell/Output 解析与版本比较
  evaluation/                  Golden Questions、指标与查询观测
  db_schema.py / dependencies.py 共享数据库契约与鉴权依赖
web/              功能控制台
tests/            代码、会话、工作区、绑定、实验、文档、平台与治理测试
```

各业务域保持 `models / schema / store / service / router` 分层；组合根只负责装配，避免把新增能力继续堆进单个 API 或存储文件。

## 当前边界

当前版本是按开发计划持续完善的单机平台基线，并非 20 页面最终验收版。以下生产能力仍需按部署规模接入：

- 本地特征哈希向量是离线可运行基线，不等同于学习型代码 Embedding；存储已记录 `embedding_model`，可按 Generation 无损替换。
- CALLS / IMPORTS 是静态启发式关系；歧义目标不会伪装成确定事实。下一步接入 SCIP/LSP 结果作为更强的结构事实源。
- SQLite FTS5 承担当前词法检索；规模验证后可增加 Zoekt Adapter，并保持统一 Candidate 协议。
- `RAG_ENFORCE_ACL=true` 时，代码、Codex、文档/实验统一检索和 Lineage 会在召回前使用可信网关注入的 `X-RAG-ACL-Refs` 过滤；身份认证和用户到 ACL Ref 的 OpenFGA/组织目录判定仍属于部署层，不应直接信任公网客户端自报该请求头。
- 当前后台任务由单进程执行；生产化阶段用 Temporal/队列替代，并加入取消、重试和并发配额。
- Git Commit/Branch/DiffHunk/TestResult 已接入；PR Review、CI Provider webhook 与 SCIP 精确引用尚未接入。历史 Commit 超出选择的 `history_depth` 时会标记 `unknown_version`，不会伪造精确影响范围。
- 本机会话 rollout 格式作为兼容 Adapter，不假设私有字段永久稳定。生产集成优先采用 [Codex App Server](https://developers.openai.com/codex/app-server) 或 [`codex exec --json`](https://developers.openai.com/codex/noninteractive) 的结构化事件协议。
- PDF/DOCX 当前保留页、段落、表格和图形引用等可确定结构；复杂跨页表格、公式语义和图像 OCR 仍需专用版面模型增强。
- 当前 Episode 以 Turn 边界确定性生成；跨 Turn 目标连续性和高质量 Decision 抽取仍可继续增强。
- 控制台按设计面向桌面研发场景，当前最小宽度为 1180px，尚未做移动端布局。

参考实现主要借鉴 [FalkorDB/code-graph](https://github.com/FalkorDB/code-graph) 的 FastAPI、Tree-sitter 与代码属性图方式，同时按本项目设计补充版本化实体、稳定 URI、索引 Generation、原始 Evidence Locator 和混合检索契约。

## 验证

```bash
make backend-smoke
make backend-integration
make backend-evaluation
make backend-full

node --check web/assets/app.js
node --check web/assets/source-workbenches.js
node --check web/assets/rag-workbench.js
node --check web/assets/graph-workbench.js
uv run ruff check src tests
uv run pytest -q
```

四个 backend target 分别用于快速合同检查、RAG/Wiki 集成链、历史评估合同和完整发布前
回归；它们与后端 CI 使用相同的 locked environment、禁用 pytest 仓库缓存，并且不读取或
修改正式数据库。局部 target 通过不能替代 `backend-full`。

G0 版本准入候选使用逐文件、内容寻址 manifest 固定；以下命令不会 stage、commit、push，也
不会读取正式数据库：

```bash
make g0-admission-build
make g0-admission-verify
make g0-admission-cleanroom
make g0-admission-release-verify
```

V5 evidence run 沿用 V2 的 source/evaluation/generated/sensitive 范围策略、V3 的内容寻址
Code Golden complete-history bundle，以及 V4 的可移植 runtime authority；V5 新增 G1 binding
foundation 的可执行 schema/migration/dry-run 规格与独立设计复核，不修改 runtime/test/lock。
`g0-admission-cleanroom` 只从 admission bytes 建立临时 Git 仓库，复算历史对象和安全分母，
并证明固定提交可由普通 clone 传递。前端构建输出、数据库
sidecar、cache、敏感路径和未登记设计图片均排除；评测 SQLite 锚点只按不透明 bytes 计算摘要，
准入工具不会将它们作为数据库打开。

owner 将同一 manifest 对应的文件显式纳入受审 revision 后，应从 clean checkout 按以下顺序
验证：self-contained cleanroom；locked install；前端 tests/typecheck/build；后端 `backend-full`；最后执行
`g0-admission-release-verify`。构建输出必须仍被排除，且受审版本不得跟踪 `web/index.html` 等
运行时产物。随后再运行 Python 3.12/3.13 远程 CI。当前工作树的普通 verify 通过只表示源码
内容一致，不表示版本准入完成。

## 参与贡献与安全

开发与 Pull Request 约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请按
[SECURITY.md](SECURITY.md) 使用 GitHub 私有漏洞报告，不要在公开 Issue 中披露敏感信息。

## 许可证

本项目采用 [MIT License](LICENSE)。
