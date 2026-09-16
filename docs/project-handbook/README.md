# RAG 项目完整手册

版本：2026-08-03  
适用仓库：`/Users/example/project/rag` 共享 dirty main

这套手册是当前项目的统一阅读入口，用于替代“从几十份阶段文档中自行拼接全貌”的方式。

## 文档导航

1. [完整 Wiki + RAG 技术文档](../rag-optimization/development/07_RAG_END_TO_END_DEVELOPMENT_ROADMAP_AND_TECHNICAL_REPORT.md)
   - 以当前实现为主体，说明 Evidence Compiler、Agent-Native Wiki、混合检索、Navigator、raw verify、Evidence Pack、智能生成、Codex/MCP、质量、性能和生产边界。
   - [App Wiki + RAG 智能查询架构](../design/APP_WIKI_RAG_INTELLIGENCE_ARCHITECTURE.md)进一步给出混合查询理解算法、子问题分解、RRF、LLM keychain、安全与性能合同。
   - [旧六源 RAG 底座说明](01_TECHNICAL_ARCHITECTURE.md)保留为实现历史和来源内技术附录，不再代表 Wiki 完成状态。
2. [产品与交互设计文档](02_PRODUCT_AND_INTERACTION_DESIGN.md)
   - 信息架构、页面职责、关键用户流程、关系图谱、会话—代码联动、分页和状态设计。
3. [当前 Wiki + RAG 项目面试 QA](../rag-optimization/interview/08_RAG_PROJECT_INTERVIEW_QA.md)
   - 85 组由浅入深的当前问答，主线是证据编译、Wiki 导航、查询理解、检索融合、证据构造、生成验证、Agent/插件、原生 App、评估发布和实际技术栈。
   - [旧 117 题六源底座题库](03_INTERVIEW_QA.md)继续作为扩展追问题库。

## 当前结论

| 维度 | 当前状态 | 可对外表述 |
| --- | --- | --- |
| 仓库 Wiki + RAG 工程 | `COMPLETE` | 六来源、Compiler、WikiStore、Navigator、Builder、智能查询、MCP/Codex plugin、API 和 macOS/Windows App 工程已形成闭环 |
| 默认运行策略 | `DEFAULT_V1` | V2 必须显式、受权威校验和回退策略约束 |
| 生产质量 | `QUALITY_HOLD` | 可以继续隔离开发和审阅，不能声称生产质量已达标 |
| 发布状态 | `NO_RELEASE` | 尚未完成正式 shadow/canary 与默认 V2 切换 |
| L4/L5 | `EXTERNAL_DEFERRED` | 正式库迁移、生产回放和发布需要额外授权与生产证据 |

## 证据优先级

出现口径冲突时，按以下顺序判断：

1. 当前代码、测试和运行结果；
2. [Wiki + RAG 工程与启动最终复核](../rag-optimization/development/reviews/17_WIKI_RAG_ENGINEERING_AND_LIVE_GATE_REVIEW_2026-08-03.md)；
3. [最终完整性 Gate](../rag-optimization/development/reviews/13_RAG_FINAL_COMPLETENESS_GATE_REVIEW.md)；
4. [真实启动 Gate](../rag-optimization/development/reviews/14_RAG_LIVE_STARTUP_GATE_REVIEW.md)；
5. [全产品自测报告](../rag-optimization/development/reviews/15_RAG_FULL_PRODUCT_SELF_TEST_REPORT_2026-08-02.md)；
6. [会话图谱与分页报告](../rag-optimization/development/reviews/16_SESSION_GRAPH_PAGINATION_SELF_TEST_REPORT_2026-08-02.md)；
7. 本手册；
8. 早期设计、路线图和历史评审文档。

历史文档仍用于解释设计来源和修复轨迹，但其中旧测试数量、旧 authority digest、旧页面限制和阶段状态不自动代表当前版本。
