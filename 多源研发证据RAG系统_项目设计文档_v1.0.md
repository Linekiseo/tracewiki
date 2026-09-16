# 多源研发证据 RAG 系统：项目设计文档

> **Versioned R&D Evidence RAG**
>
> 代码仓库、Codex 会话、科研文档与实验事实的版本化协同

> **面向研发过程的可追溯知识系统，而不是普通的多知识库问答。**
>
> 以 Entity、Relation、Evidence、Version 和 Time 为核心，连接讨论、代码、实验与结论。
>
> 本文覆盖需求、架构、数据模型、摄取、检索、API、安全、评测、部署和实施路线。

| **文档类型**    | 系统设计 / 技术方案 |
|-----------------|---------------------|
| **版本 / 状态** | v1.0 / 方案评审稿   |
| **日期**        | 2026-07-21          |
| **责任方**      | 研发知识系统项目组  |
| **密级**        | 内部设计资料        |

**文档用途**

用于产品、架构、数据、算法、安全和平台团队的方案评审、PoC 规划与实施拆解。文中开源项目和许可证信息应在锁定具体版本时重新核验。

<a id="toc"></a>
## 目录

- [1. 文档说明与执行摘要](#section-1)
  - [1.1 文档目的](#section-1-1)
  - [1.2 一句话定位](#section-1-2)
  - [1.3 核心价值](#section-1-3)
  - [1.4 本项目的差异化](#section-1-4)
  - [1.5 关键成功条件](#section-1-5)
- [2. 项目背景、目标与边界](#section-2)
  - [2.1 背景问题](#section-2-1)
  - [2.2 建设目标](#section-2-2)
  - [2.3 非目标](#section-2-3)
  - [2.4 系统边界](#section-2-4)
- [3. 用户、场景与需求](#section-3)
  - [3.1 目标用户](#section-3-1)
  - [3.2 核心用户故事](#section-3-2)
  - [3.3 代表性查询集](#section-3-3)
  - [3.4 功能需求](#section-3-4)
  - [3.5 非功能需求](#section-3-5)
- [4. 设计原则与关键决策](#section-4)
  - [4.1 Entity RAG，而非 Chunk RAG](#section-4-1)
  - [4.2 分源索引，检索后融合](#section-4-2)
  - [4.3 原始数据不可变，摘要可重建](#section-4-3)
  - [4.4 确定性优先，推断关系显式降权](#section-4-4)
  - [4.5 双时间与版本优先于“最近摄取”](#section-4-5)
  - [4.6 权限前置，证据先于生成](#section-4-6)
  - [4.7 查询相关的证据权威度](#section-4-7)
- [5. 总体架构](#section-5)
  - [5.1 逻辑架构](#section-5-1)
  - [5.2 核心服务划分](#section-5-2)
  - [5.3 数据流](#section-5-3)
  - [5.4 高可用与故障隔离](#section-5-4)
- [6. 统一领域模型与证据图](#section-6)
  - [6.1 通用实体结构](#section-6-1)
  - [6.2 稳定 ID 规范](#section-6-2)
  - [6.3 代码域实体](#section-6-3)
  - [6.4 Codex 会话域实体](#section-6-4)
  - [6.5 科研与实验域实体](#section-6-5)
  - [6.6 关系模型](#section-6-6)
  - [6.7 边的证据与状态](#section-6-7)
  - [6.8 证据状态机](#section-6-8)
  - [6.9 数据质量规则](#section-6-9)
- [7. 数据源接入与摄取设计](#section-7)
  - [7.1 统一事件信封](#section-7-1)
  - [7.2 Raw Immutable Store](#section-7-2)
  - [7.3 摄取工作流](#section-7-3)
  - [7.4 增量策略](#section-7-4)
  - [7.5 删除和权限变化传播](#section-7-5)
- [8. 代码仓库知识链路](#section-8)
  - [8.1 接入内容](#section-8-1)
  - [8.2 解析与索引流程](#section-8-2)
  - [8.3 代码切块策略](#section-8-3)
  - [8.4 代码召回通道](#section-8-4)
  - [8.5 历史版本策略](#section-8-5)
  - [8.6 代码证据定位](#section-8-6)
- [9. Codex 会话知识链路](#section-9)
  - [9.1 接入方式](#section-9-1)
  - [9.2 事件标准化](#section-9-2)
  - [9.3 Development Episode](#section-9-3)
  - [9.4 Patch 到 Commit 的关联](#section-9-4)
  - [9.5 会话事实裁决](#section-9-5)
  - [9.6 会话检索信号](#section-9-6)
- [10. 科研文档与实验链路](#section-10)
  - [10.1 两层事实模型](#section-10-1)
  - [10.2 文档解析](#section-10-2)
  - [10.3 Claim 模型](#section-10-3)
  - [10.4 实验事实源](#section-10-4)
  - [10.5 文档到 Run 的匹配](#section-10-5)
  - [10.6 Notebook 处理](#section-10-6)
  - [10.7 实验复现包](#section-10-7)
- [11. 索引与存储设计](#section-11)
  - [11.1 MVP 存储组合](#section-11-1)
  - [11.2 搜索视图](#section-11-2)
  - [11.3 向量策略](#section-11-3)
  - [11.4 索引 Generation](#section-11-4)
  - [11.5 扩展条件](#section-11-5)
- [12. 查询理解与检索编排](#section-12)
  - [12.1 查询分类](#section-12-1)
  - [12.2 Scope 解析](#section-12-2)
  - [12.3 分源召回](#section-12-3)
  - [12.4 关系扩展](#section-12-4)
  - [12.5 重排与融合](#section-12-5)
  - [12.6 证据预算](#section-12-6)
- [13. Evidence Pack、生成与引用](#section-13)
  - [13.1 Evidence Pack 结构](#section-13-1)
  - [13.2 生成约束](#section-13-2)
  - [13.3 引用协议](#section-13-3)
  - [13.4 回答模板](#section-13-4)
  - [13.5 Evidence UI](#section-13-5)
- [14. 版本有效性与结论失效检测](#section-14)
  - [14.1 判定链路](#section-14-1)
  - [14.2 关键依赖模型](#section-14-2)
  - [14.3 判定规则示例](#section-14-3)
  - [14.4 输出状态](#section-14-4)
  - [14.5 避免误报](#section-14-5)
- [15. API 与事件契约](#section-15)
  - [15.1 Query API](#section-15-1)
  - [15.2 Evidence Search API](#section-15-2)
  - [15.3 Entity 与 Lineage API](#section-15-3)
  - [15.4 Ingestion API](#section-15-4)
  - [15.5 事件主题](#section-15-5)
- [16. 安全、权限与治理](#section-16)
  - [16.1 身份与授权](#section-16-1)
  - [16.2 安全边界](#section-16-2)
  - [16.3 敏感数据分级](#section-16-3)
  - [16.4 Secret 和内容安全](#section-16-4)
  - [16.5 治理与审计](#section-16-5)
- [17. 可观测性、评测与质量门禁](#section-17)
  - [17.1 Trace 模型](#section-17-1)
  - [17.2 离线评测集](#section-17-2)
  - [17.3 指标体系](#section-17-3)
  - [17.4 质量门禁](#section-17-4)
  - [17.5 人工评审](#section-17-5)
- [18. 部署、容量与成本](#section-18)
  - [18.1 环境划分](#section-18-1)
  - [18.2 容量估算方法](#section-18-2)
  - [18.3 性能策略](#section-18-3)
  - [18.4 成本观测](#section-18-4)
- [19. 实施路线与团队分工](#section-19)
  - [19.1 分阶段路线](#section-19-1)
  - [19.2 团队分工](#section-19-2)
  - [19.3 建议迭代节奏](#section-19-3)
- [20. 风险、开放问题与验收标准](#section-20)
  - [20.1 主要风险](#section-20-1)
  - [20.2 开放问题](#section-20-2)
  - [20.3 MVP 验收标准](#section-20-3)
  - [20.4 最终建议](#section-20-4)
- [附录 A. 核心表结构建议](#appendix-a)
  - [A.1 Entity 表](#appendix-a-1)
  - [A.2 Search View 表](#appendix-a-2)
  - [A.3 Claim Validity 表](#appendix-a-3)
- [附录 B. Golden Questions 模板](#appendix-b)
- [附录 C. 架构决策记录（ADR）清单](#appendix-c)
- [附录 D. 技术依据与官方资料](#appendix-d)

<a id="section-1"></a>
## 1. 文档说明与执行摘要

[返回目录](#toc)

<a id="section-1-1"></a>
### 1.1 文档目的

本文给出“多源研发证据 RAG 系统”的完整项目设计基线。系统统一接入**代码仓库、Codex 会话、科研文档与实验事实**，目标不是简单回答“哪里有相关内容”，而是形成可追溯的研发证据链，解释一项技术结论由谁提出、如何实现、如何验证、适用于哪个版本，以及在当前版本上是否仍然成立。

本文可用于产品立项、架构评审、PoC 拆解、数据治理、安全评审、算法评测和研发排期。文档中的组件选型是参考实现，不改变以下核心约束：统一实体与 ID、确定性关系优先、版本和时间显式化、权限前置、证据先于生成、引用落到原始来源。

<a id="section-1-2"></a>
### 1.2 一句话定位

> **产品定位**
>
> 建设一个 Versioned Research & Engineering Evidence RAG（版本化研发证据系统），把“讨论意图—Agent 执行—代码实现—实验验证—科研结论—当前有效性”连接成可查询、可复现、可审计的证据网络。

![图 1 研发证据闭环](多源研发证据RAG系统_项目设计文档_v1.0_assets/image1.png)

图 1 研发证据闭环

<a id="section-1-3"></a>
### 1.3 核心价值

- **减少研发失忆**：保留为什么做、试过什么、为何放弃，而不仅是代码最终状态。

- **提高实验可信度**：将文档中的 Claim 追溯到 Run、配置、数据版本和 Commit。

- **降低复现成本**：围绕一次实验自动汇集环境、参数、代码、数据、日志、图表和讨论。

- **识别结论失效**：当前代码、配置或数据发生关键变化时，主动标记旧结论的适用风险。

- **支撑工程审计**：每条回答展示证据来源、版本、时间、权限、派生方式和冲突信息。

- **服务人和 Agent**：同一证据层可供 Web、IDE、Codex、自动评测和研发治理服务调用。

<a id="section-1-4"></a>
### 1.4 本项目的差异化

| **维度**   | **普通文档 RAG** | **代码搜索 / 代码助手** | **本项目**                                     |
|------------|------------------|-------------------------|------------------------------------------------|
| 基本对象   | 文本 Chunk       | 文件、Symbol、Diff      | Entity + Relation + Evidence                   |
| 时间与版本 | 通常隐式         | 主要关注 Git 版本       | Git、会话、实验、文档统一版本化                |
| 事实来源   | 文档陈述         | 当前代码                | 代码行为、执行事件、原始 Run、文档 Claim 分层  |
| 跨源推理   | 相似片段拼接     | 代码上下文拼接          | 决策→Patch→Commit→Run→Claim 证据路径           |
| 冲突处理   | 较弱             | 较弱                    | 来源权威度、反证、失效和证据缺口显式化         |
| 引用       | 文档页或 Chunk   | 路径和行号              | Commit/行号、Thread/Item、Run/Metric、页/表/图 |

<a id="section-1-5"></a>
### 1.5 关键成功条件

> **1.** 系统能够稳定建立 Codex Session → Patch → Commit → Symbol → Experiment Run → Claim 的证据路径。
>
> **2.** 回答引用的是原始代码、事件、Run 或文档定位，而不是模型摘要。
>
> **3.** 用户查询当前实现时，不会混入错误 Branch 或历史 Commit。
>
> **4.** 文档结论与原始实验指标冲突时，系统能识别并披露。
>
> **5.** 用户无权限访问的内容不会进入候选集、提示词、缓存或 Trace。
>
> **6.** 组件升级、解析器变化和 Embedding 变化均可重建、可回滚、可审计。

<a id="section-2"></a>
## 2. 项目背景、目标与边界

[返回目录](#toc)

<a id="section-2-1"></a>
### 2.1 背景问题

研发知识分散在 Git、Issue/PR、Codex 对话、终端输出、实验平台、Notebook、PDF、表格、对象存储和个人记忆中。单个系统通常只能回答局部问题：Git 能说明“改了什么”，会话能说明“考虑过什么”，实验平台能说明“指标是多少”，文档能说明“最终如何解释”。一旦人员流动、版本演进或实验复现，跨系统关联便成为高成本人工调查。

典型问题包括：

- 报告写“Recall 提升 4.2%”，但无法快速找到对应 Run、配置、数据版本和代码版本。

- Codex 会话声称已修复，但实际 Patch 未提交、测试未通过或随后被覆盖。

- 当前 main 已修改关键 Symbol，团队仍引用旧 Commit 上得出的性能结论。

- 同一个实验编号在 Notebook、MLflow 和文档中含义不一致。

- 搜索召回了语义相关但版本错误的代码，生成结果看似合理却不可执行。

- 删除源文档或撤销仓库权限后，派生摘要和向量仍然泄露信息。

<a id="section-2-2"></a>
### 2.2 建设目标

<a id="业务目标"></a>
#### 业务目标

- 将跨源调查时间从“小时或天”降低到“分钟”。

- 将关键实验的可复现率、代码版本可定位率和引用覆盖率提升为可量化指标。

- 形成团队可复用的研发记忆，降低人员交接和重复试错成本。

- 为后续 Agent 自动调查、代码评审、实验审计和研发治理提供统一上下文层。

<a id="技术目标"></a>
#### 技术目标

- 支持代码、Codex 会话、科研文档和实验平台的增量摄取。

- 建立稳定 URI、统一实体模型、确定性关系和时间有效性模型。

- 实现分源召回、关系扩展、版本对齐、证据重排和冲突检测。

- 输出结构化 Evidence Pack，并在受约束生成中强制引用。

- 支持项目级与实体级 ACL，检索前完成权限过滤。

- 建立离线 Golden Set、在线 Trace、回归评测和质量门禁。

<a id="section-2-3"></a>
### 2.3 非目标

> **MVP 明确不做**
>
> 第一阶段不建设通用企业搜索，不一次性索引所有 Git 历史，不让 LLM 自动确认所有关系，不直接让 RAG 服务修改生产仓库，不以全自动知识图谱替代确定性元数据，也不同时引入多个独立搜索和图数据库。

- 不替代 GitHub/GitLab、MLflow、DVC、文档管理或科研工作台。

- 不将 Codex 的自然语言陈述直接当成已验证事实。

- 不对缺乏 Run、Commit 或 Dataset Version 的结论做伪确定性判断。

- 不承诺从任意扫描 PDF 中无误恢复复杂公式和表格；解析质量需评分并允许人工修订。

- 不在未建立评测基线前追求复杂 Multi-Agent 编排。

<a id="section-2-4"></a>
### 2.4 系统边界

| **边界对象** | **系统负责**                                        | **源系统仍负责**                  |
|--------------|-----------------------------------------------------|-----------------------------------|
| Git 仓库     | 镜像元数据、版本解析、Symbol/引用索引、证据定位     | 权威代码、分支保护、PR 合并       |
| Codex 会话   | 事件接入、Episode 聚合、Patch/Commit 关联、长期检索 | Agent 执行、审批、沙箱与会话运行  |
| 实验平台     | 同步 Run、参数、指标、产物引用和谱系                | 实验执行、指标写入、Artifact 保存 |
| 科研文档     | 结构解析、Claim/表图提取、页级引用                  | 文档协作、最终发布与签审          |
| RAG 服务     | 查询、证据裁决、生成、引用、评测                    | 不作为源事实的唯一存储            |

<a id="section-3"></a>
## 3. 用户、场景与需求

[返回目录](#toc)

<a id="section-3-1"></a>
### 3.1 目标用户

| **角色**      | **主要诉求**                         | **典型动作**                                  |
|---------------|--------------------------------------|-----------------------------------------------|
| 算法研究员    | 找到实验依据、复现旧结果、比较方案   | 查询 Run、参数、数据版本、表图和 Claim        |
| 研发工程师    | 理解实现和历史原因、定位影响范围     | 查 Symbol、Diff、测试、会话决策和关联实验     |
| 技术负责人    | 判断方案可信度、识别版本和结论风险   | 审查证据链、冲突、失效提示和质量指标          |
| 新成员        | 快速理解项目技术路线和失败经验       | 按主题和时间浏览 Development Episode          |
| 安全/治理人员 | 控制访问、审计引用和删除传播         | 检查 ACL、审计日志、敏感数据和数据血缘        |
| 自动化 Agent  | 获取可执行、版本正确且有来源的上下文 | 调用 Query/Evidence API，不直接读取混合 Chunk |

<a id="section-3-2"></a>
### 3.2 核心用户故事

- 作为工程师，我想知道当前 main 中 Retriever.search 的实际实现、相关测试及设计原因。

- 作为研究员，我想从报告中的表格跳转到原始 MLflow Run，并确认使用的 Commit 和数据版本。

- 作为负责人，我想知道一次 Codex 优化是否真正进入 Git、是否通过测试、是否被实验验证。

- 作为评审人，我想比较两个 Claim 的支持证据、反证和适用条件。

- 作为新成员，我想查看某项技术路线从提出、失败到替代的完整时间线。

- 作为 Agent，我想获取一个结构化 Evidence Pack，其中已完成权限、版本和冲突判断。

<a id="section-3-3"></a>
### 3.3 代表性查询集

| **查询类型** | **示例**                               | **必需证据**                                    |
|--------------|----------------------------------------|-------------------------------------------------|
| 当前实现     | 当前主分支的重排逻辑是什么？           | 当前 Branch→Commit→Symbol→代码行→测试           |
| 设计原因     | 为什么将 CrossEncoder 替换为 ColBERT？ | 已接受 Decision、PR、相关会话、权衡             |
| 执行核验     | 会话 S-88 的缓存优化是否完成？         | FileChange、Patch、Commit、TestResult           |
| 实验核验     | E17 的 Recall 提升是否可信？           | Run、Metric、Config、DatasetVersion、Commit     |
| 论文溯源     | Table 6 的每个数来自哪些 Run？         | DocumentVersion、TableCell、Run、MetricResult   |
| 复现         | 如何复现 RUN-042-07？                  | Commit、环境、Config、Dataset、命令、Artifact   |
| 失效判断     | 当前 main 是否仍支持 Claim C12？       | Claim→Run→Commit A，Current Commit B，Diff/影响 |
| 全局综合     | 最近半年主要失败模式是什么？           | 多 Episode、失败 Run、推断主题图；需标明推断    |

<a id="section-3-4"></a>
### 3.4 功能需求

<a id="p0-必须具备"></a>
#### P0：必须具备

- 三类来源的增量摄取、原始数据留存和可重建索引。

- 稳定 ID、实体去重、确定性边、派生关系和证据定位。

- 分源检索、版本过滤、关系扩展和 Evidence Pack。

- 精确引用、答案适用版本、不确定性与冲突披露。

- 项目/仓库/会话/实验/文档级权限继承与检索前过滤。

- Golden Questions、Trace、回归评测和审计日志。

<a id="p1-应具备"></a>
#### P1：应具备

- Claim 抽取与 Run 自动匹配建议。

- Development Episode 自动聚合和人工修订。

- 结论失效风险检测、关键依赖变化解释。

- Notebook 参数和输出结构化、实验差异比较。

- Evidence UI：代码 Diff、会话时间线、实验指标和文档页并列展示。

<a id="p2-后续演进"></a>
#### P2：后续演进

- 跨项目概念图、主题摘要和组织级知识控制面。

- Agent 自动调查和证据补全建议。

- 关系审核工作台、主动通知和结论复验任务。

- 基于使用反馈的在线排序与个性化，但不得突破 ACL。

<a id="section-3-5"></a>
### 3.5 非功能需求

| **维度** | **初始目标**                                                    |
|----------|-----------------------------------------------------------------|
| 可用性   | 查询服务月可用性 ≥ 99.5%；摄取失败可重试且不影响已有索引        |
| 延迟     | 常规查询 P50 ≤ 3 秒，P95 ≤ 8 秒；深度调查允许分阶段返回         |
| 正确性   | Wrong-Version Answer Rate \< 2%；关键查询 Commit Accuracy ≥ 95% |
| 安全     | Unauthorized Evidence Leakage = 0；Secret Leakage = 0           |
| 可重建   | 任意派生数据可由 Raw Source + Parser/Model Version 重建         |
| 可审计   | 每个回答保留 Query、Scope、候选、Evidence Pack、模型和引用日志  |
| 可扩展   | 数据源 Adapter、Retriever 和 Evidence Rule 可插件化扩展         |

<a id="section-4"></a>
## 4. 设计原则与关键决策

[返回目录](#toc)

<a id="section-4-1"></a>
### 4.1 Entity RAG，而非 Chunk RAG

传统 RAG 把所有数据压平为 Chunk，容易丢失版本、对象边界和跨源关系。本项目以实体作为知识主键，Chunk 只是实体的文本表现或检索视图。

```text
Repository → Commit → FileVersion → Symbol
CodexThread → Turn → Item → Patch → Commit
Experiment → Run → Config / DatasetVersion → MetricResult
DocumentVersion → Section / Table / Figure → Claim
```

<a id="section-4-2"></a>
### 4.2 分源索引，检索后融合

代码、会话和科研资料具有不同的语义与检索信号，必须使用专用解析和召回，再在 Evidence 层融合。统一向量空间仅作为一种候选通道，不是唯一入口。

<a id="section-4-3"></a>
### 4.3 原始数据不可变，摘要可重建

原始 Git 对象、Codex JSONL 事件、文档文件、MLflow Run 元数据和 Artifact 引用写入不可变层。任何摘要、Embedding、Claim、Episode 或关系必须记录生成器及版本，允许回滚和重算。

<a id="section-4-4"></a>
### 4.4 确定性优先，推断关系显式降权

- Run uses Commit 若来自 Run Tag 中的 SHA，可标为 deterministic。

- Decision motivates Commit 若由 LLM 根据语义判断，只能标为 llm_inferred。

- 人工审核可将关系升级为 human_confirmed，但不能抹去原始派生路径。

<a id="section-4-5"></a>
### 4.5 双时间与版本优先于“最近摄取”

系统同时维护事实发生时间、系统观察时间和有效区间，避免把今天导入的旧报告误认为当前知识。

```text
event_time = 2025-03-01T10:00:00Z
observed_at = 2026-07-21T14:20:00Z
valid_from = 2025-03-01T10:00:00Z
valid_to = 2025-06-18T09:30:00Z
```

<a id="section-4-6"></a>
### 4.6 权限前置，证据先于生成

检索服务在召回前根据用户和 Scope 计算允许访问的 Source Object；禁止先检索敏感内容，再依赖 Prompt 让模型忽略。生成模型只能读取已过滤并裁决过的 Evidence Pack。

<a id="section-4-7"></a>
### 4.7 查询相关的证据权威度

来源没有一个全局固定排名：当前行为以当前代码和测试为准，性能结论以原始 Run 为准，设计原因以已接受决策、PR 和相关会话为准，文档内容以指定文档版本为准。

> **核心架构决策摘要**
>
> MVP 使用 PostgreSQL 承载实体、关系、权限、全文和向量；Zoekt 承载代码精确搜索；对象存储保存原始文件与 Artifact；先用关系表实现 1–3 跳图扩展，待数据和查询复杂度明确后再拆出专用搜索或图系统。

<a id="section-5"></a>
## 5. 总体架构

[返回目录](#toc)

<a id="section-5-1"></a>
### 5.1 逻辑架构

![图 2 系统逻辑架构](多源研发证据RAG系统_项目设计文档_v1.0_assets/image2.png)

图 2 系统逻辑架构

系统分为七个纵向层次和四个横向控制面：

> **1. 数据源层**：Git/GitHub/GitLab、Codex、科研文档、Notebook、MLflow、DVC、对象存储。
>
> **2. 接入与原始层**：Webhook、API、文件扫描、事件流和 Raw Immutable Store。
>
> **3. 分源解析层**：代码 AST/Symbol/Diff、Codex Event/Episode、文档 Layout/Claim、实验 Run/Lineage。
>
> **4. 统一知识层**：Entity Registry、Cross-source Linker、Evidence Graph、Temporal Resolver。
>
> **5. 索引与存储层**：事务元数据、全文/向量、代码搜索、对象与事件存储。
>
> **6. 查询与证据层**：权限、意图、分源召回、图扩展、版本对齐、冲突和时效判断。
>
> **7. 交互与生成层**：Evidence Pack、受约束生成、引用、Web/IDE/API。

横向控制面包括 Temporal 工作流、OpenTelemetry/OpenInference 可观测、Keycloak/OpenFGA 身份授权，以及 Ragas/promptfoo/自定义指标评测。

<a id="section-5-2"></a>
### 5.2 核心服务划分

| **服务**               | **主要职责**                                 | **不承担的职责**           |
|------------------------|----------------------------------------------|----------------------------|
| Source Adapter Service | 接收 Git、Codex、Document、Run 事件并标准化  | 不做最终实体合并和语义判断 |
| Raw Store Service      | 保存原始对象、哈希、来源版本和删除标记       | 不作为用户直接搜索接口     |
| Parser Workers         | 按来源提取结构、Chunk、实体候选和定位信息    | 不直接确认跨源推断关系     |
| Entity Registry        | 生成稳定 ID、去重、别名和实体版本            | 不保存大文件正文           |
| Cross-source Linker    | 建立 Session/Patch/Commit/Run/Claim 关系     | 低置信关系不得自动视为事实 |
| Index Service          | 管理 FTS、Vector、Zoekt 和增量重建           | 不决定答案权威性           |
| Query Service          | 解析 Scope、意图、实体、版本和权限           | 不直接调用模型自由回答     |
| Evidence Service       | 扩展关系、重排、冲突检测、生成 Evidence Pack | 不修改源系统               |
| Answer Service         | 基于 Evidence Pack 生成答案和引用            | 不读取未授权 Raw 数据      |
| Evaluation Service     | 离线评测、在线抽样、回归门禁                 | 不替代人工领域评审         |

<a id="section-5-3"></a>
### 5.3 数据流

```text
Source Event
→ Normalize Envelope
→ Persist Raw Object / Event
→ Schedule Parse Workflow
→ Produce Source Entities and Chunks
→ Resolve Stable IDs and Aliases
→ Create Deterministic Edges
→ Propose Inferred Edges
→ Index Search Views
→ Run Data Quality Checks
→ Publish Index Snapshot
```

查询数据流如下：

![图 3 查询与证据构造流程](多源研发证据RAG系统_项目设计文档_v1.0_assets/image3.png)

图 3 查询与证据构造流程

<a id="section-5-4"></a>
### 5.4 高可用与故障隔离

- 摄取和查询解耦，解析失败不会使已有查询不可用。

- 每个来源使用独立 Workflow Queue，避免大 PDF OCR 阻塞 Git 增量索引。

- 索引采用 Snapshot/Generation 发布，失败构建不会覆盖上一可用版本。

- Answer Service 降级时仍可返回 Evidence Search 结果和原始引用。

- 图关系或向量服务故障时，保留 BM25/Zoekt/ID 精确查询的降级路径。

- 原始源删除后先设置 Tombstone 并阻止检索，随后异步清理派生数据和缓存。

<a id="section-6"></a>
## 6. 统一领域模型与证据图

[返回目录](#toc)

<a id="section-6-1"></a>
### 6.1 通用实体结构

每个实体至少包含以下字段：

```json
{
  "id": "code://rag-core@31ab90/src/retriever.py#Retriever.search",
  "entity_type": "CodeSymbol",
  "project_id": "project-rag",
  "source_system": "github",
  "source_uri": "https://.../blob/31ab90/src/retriever.py#L91-L146",
  "source_version": "31ab90...",
  "content_hash": "sha256:...",
  "event_time": "2026-07-01T09:10:00Z",
  "observed_at": "2026-07-01T09:10:08Z",
  "valid_from": "2026-07-01T09:10:00Z",
  "valid_to": null,
  "acl_ref": "repo:rag-core",
  "provenance": {
    "adapter": "github-v1",
    "parser": "tree-sitter-python-v0.25",
    "workflow_id": "wf-..."
  }
}
```

<a id="section-6-2"></a>
### 6.2 稳定 ID 规范

| **对象**         | **URI 规范**                                    | **示例**                                    |
|------------------|-------------------------------------------------|---------------------------------------------|
| Repository       | repo://{org}/{repo}                             | repo://acme/rag-core                        |
| Commit           | git://{repo-id}/commit/{sha}                    | git://acme/rag-core/commit/31ab90...        |
| Code Symbol      | code://{repo-id}@{sha}/{path}#{symbol}          | code://acme/rag-core@31ab90/src/r.py#search |
| Codex Thread     | codex://thread/{thread-id}                      | codex://thread/th_88                        |
| Codex Item       | codex://thread/{thread}/turn/{turn}/item/{item} | codex://thread/th_88/turn/t3/item/i7        |
| Experiment       | experiment://{project}/{experiment-id}          | experiment://rag/E17                        |
| Run              | run://{project}/{experiment}/{run-id}           | run://rag/E17/R17-03                        |
| Document Version | document://{doc-id}/{version}                   | document://report-2026/v3                   |
| Claim            | claim://{doc-id}/{version}/{claim-id}           | claim://report-2026/v3/C12                  |

稳定 ID 与展示名称分离。代码 Symbol 的语义身份还应保存 SCIP Symbol 或语言专用签名；文件重命名和 Symbol 移动通过 Alias/Lineage 连接，而不是覆盖旧 ID。

<a id="section-6-3"></a>
### 6.3 代码域实体

```text
Repository
├── Branch / Tag
├── Commit
│ ├── ParentCommit
│ ├── FileVersion → FileBlob
│ ├── DiffHunk
│ └── TestResult
├── PullRequest / Issue
└── CodeSymbol
├── Definition
├── Reference
├── CallEdge
├── ImplementsEdge
└── Documentation
```

<a id="section-6-4"></a>
### 6.4 Codex 会话域实体

```text
CodexThread
├── CodexTurn
│ └── CodexItem
│ ├── Message
│ ├── CommandExecution
│ ├── FileChange
│ ├── Patch
│ ├── Approval
│ └── ValidationResult
├── UserGoal
├── Decision / Alternative
├── DevelopmentEpisode
└── UnresolvedItem
```

<a id="section-6-5"></a>
### 6.5 科研与实验域实体

```text
ResearchProject
├── Experiment
│ └── ExperimentRun
│ ├── Configuration
│ ├── DatasetVersion
│ ├── MetricResult
│ ├── Artifact
│ └── NotebookRun
└── Document
└── DocumentVersion
├── Section / Paragraph
├── Table / TableCell
├── Figure / Formula
├── Citation
└── Claim
```

<a id="section-6-6"></a>
### 6.6 关系模型

| **主体**        | **关系**        | **客体**       | **默认派生方式**             |
|-----------------|-----------------|----------------|------------------------------|
| CodexThread     | has_goal        | UserGoal       | parser / human               |
| CodexTurn       | proposes        | Decision       | parser / LLM                 |
| CodexItem       | produces        | Patch          | deterministic                |
| Patch           | applied_as      | Commit         | deterministic / matched      |
| Commit          | modifies        | CodeSymbol     | deterministic                |
| Commit          | validated_by    | TestResult     | deterministic                |
| ExperimentRun   | uses            | Commit         | deterministic if SHA present |
| ExperimentRun   | uses            | DatasetVersion | deterministic / declared     |
| ExperimentRun   | produces        | MetricResult   | deterministic                |
| DocumentSection | reports         | ExperimentRun  | ID match / inferred          |
| Claim           | supported_by    | ExperimentRun  | rule / inferred / human      |
| Claim           | contradicted_by | ExperimentRun  | rule / inferred / human      |
| Decision        | motivates       | Commit         | inferred / human             |
| Commit          | implements      | Claim          | inferred / human             |

<a id="section-6-7"></a>
### 6.7 边的证据与状态

```sql
CREATE TABLE evidence_edge (
  id uuid PRIMARY KEY,
  project_id uuid NOT NULL,
  src_entity_id text NOT NULL,
  predicate text NOT NULL,
  dst_entity_id text NOT NULL,
  evidence_entity_id text,
  derivation text NOT NULL,
  confidence numeric(4,3) NOT NULL,
  review_status text NOT NULL DEFAULT 'unreviewed',
  valid_from timestamptz,
  valid_to timestamptz,
  rule_version text,
  created_at timestamptz NOT NULL,
  UNIQUE(src_entity_id, predicate, dst_entity_id, evidence_entity_id)
);
```

derivation 可取：

```text
deterministic 明确 ID、SHA、路径或事件产生
parser_extracted 结构解析器直接提取
rule_derived 可解释规则计算
llm_inferred 模型语义判断
human_confirmed 人工确认
```

<a id="section-6-8"></a>
### 6.8 证据状态机

```text
mentioned → proposed → accepted → executed → committed
↓ ↓
failed tested → experimentally_verified
任一阶段可进入 rejected / superseded / unresolved
```

状态升级必须由更强证据触发。例如 executed 需要命令或文件变化事件，committed 需要 Git Commit，tested 需要 TestResult，experimentally_verified 需要满足验收规则的 Run 与 Metric。

<a id="section-6-9"></a>
### 6.9 数据质量规则

- Run 声明 Commit SHA 时，SHA 必须存在于已授权仓库或明确标记为外部依赖。

- MetricResult 必须关联 MetricDefinition、单位、方向和计算范围。

- 文档 Claim 若包含数值，应尽量连接 TableCell、Figure 或 Run；否则标记 evidence_incomplete。

- Patch 与 Commit 匹配应保存算法、相似度和冲突候选。

- 同一实体多个 Alias 冲突时，禁止自动合并并进入人工队列。

- LLM 推断边置信度低于阈值时只用于探索，不进入回答中的“已验证事实”。

<a id="section-7"></a>
## 7. 数据源接入与摄取设计

[返回目录](#toc)

<a id="section-7-1"></a>
### 7.1 统一事件信封

所有 Adapter 输出统一信封，保证幂等、追踪和删除传播：

```json
{
  "event_id": "evt-uuid",
  "source_type": "codex",
  "source_instance": "team-local-codex",
  "event_type": "item.completed",
  "source_object_id": "th_88/t3/i7",
  "source_version": "sequence:928",
  "event_time": "2026-07-21T13:04:27Z",
  "observed_at": "2026-07-21T13:04:28Z",
  "project_hint": "project-rag",
  "acl_hint": "team-rag",
  "content_hash": "sha256:...",
  "payload_ref": "s3://raw/codex/...json",
  "trace_id": "..."
}
```

幂等键建议为 (source_instance, source_object_id, source_version, event_type)。同一版本重复投递只更新观察信息，不重复生成实体。

<a id="section-7-2"></a>
### 7.2 Raw Immutable Store

原始层保存：

- 源对象原文或可验证快照。

- 内容哈希、MIME、编码、字节长度和压缩方式。

- 来源 URL、外部版本、ACL 快照和删除状态。

- Adapter、Parser、Schema、Embedding、Prompt 和 Workflow 版本。

- 原始事件顺序号，支持重放和重建。

对于敏感来源，可采用分桶、客户管理密钥、保留期和不可变策略；系统只在元数据中保存对象引用，不把大文件复制到事务库。

<a id="section-7-3"></a>
### 7.3 摄取工作流

```text
Receive → Authorize Source → Persist Raw → Virus/Secret Scan
→ Parse → Validate → Resolve Entity → Link Deterministic Edges
→ Generate Search Views → Embed/Rerank Features
→ Data Quality Gate → Publish Index Generation → Emit Audit Event
```

每一步均为幂等 Activity，并记录输入版本和输出哈希。失败进入可重试或人工处理队列；任何失败不得半发布索引。

<a id="section-7-4"></a>
### 7.4 增量策略

| **来源**        | **增量单位**                   | **去重主键**                    | **重建触发**                       |
|-----------------|--------------------------------|---------------------------------|------------------------------------|
| Git             | Push/PR/Commit/Blob            | Repo + SHA/Blob Hash            | Parser/SCIP 版本变化或显式历史索引 |
| Codex           | Thread/Turn/Item Event         | Thread + Sequence/Item ID       | Episode 规则或 Schema 变化         |
| 文档            | DocumentVersion/Page/Object    | File Hash + Version             | Parser/OCR/Claim Prompt 变化       |
| MLflow          | Experiment/Run/Metric/Artifact | Tracking URI + Run ID + Version | Schema 或 Lineage 映射变化         |
| DVC/OpenLineage | Revision/Run Event             | Repo/Job/Run ID                 | 事件规范或映射变化                 |

<a id="section-7-5"></a>
### 7.5 删除和权限变化传播

```text
Source revoke/delete
→ mark source object denied/tombstoned immediately
→ invalidate query cache and Evidence Pack cache
→ exclude from all new retrievals
→ remove/mark derived chunks, vectors, edges, summaries
→ rebuild affected aggregate summaries
→ write deletion audit and completion status
```

> **删除传播风险**
>
> 聚合摘要、主题社区和 LLM 推断边可能间接包含已删除信息，不能只删除原始 Chunk。所有派生对象必须维护 derived_from 关系，支持影响分析和级联失效。

<a id="section-8"></a>
## 8. 代码仓库知识链路

[返回目录](#toc)

<a id="section-8-1"></a>
### 8.1 接入内容

- Repository、Branch、Tag、Commit Graph、Author、Timestamp。

- PR、Issue、Review、Merge Commit、关联测试状态。

- FileBlob、FileVersion、DiffHunk、语言和路径。

- AST 节点、Symbol、定义、引用、实现、调用和文档注释。

- 测试、配置、依赖文件以及与实验相关的脚本。

<a id="section-8-2"></a>
### 8.2 解析与索引流程

```text
Git Event
→ Resolve Commit and Parents
→ Enumerate Changed Blobs
→ Deduplicate by Blob Hash
→ Parse with Tree-sitter
→ Import SCIP Index
→ Build Symbol/File/Commit summaries
→ Update Zoekt index
→ Embed selected views
→ Publish code evidence generation
```

<a id="section-8-3"></a>
### 8.3 代码切块策略

优先级如下：

> **1.** Symbol 原文：函数、类、方法、接口、配置块。
>
> **2.** Symbol 上下文：导入、父类、调用方、被调用方、相关测试。
>
> **3.** 文件级摘要：职责、主要入口、依赖和版本。
>
> **4.** Commit/PR Diff：变更意图、受影响 Symbol 和测试。
>
> **5.** 仓库/模块 Repo Map：高价值 Symbol 和依赖概览。

固定 Token 窗口只能作为超长 Symbol 的回退方案。Chunk 必须携带 Commit、Blob、路径、Symbol、行号、语言和 ACL。

<a id="section-8-4"></a>
### 8.4 代码召回通道

| **通道**             | **适用问题**                         | **主要信号**               |
|----------------------|--------------------------------------|----------------------------|
| Zoekt/关键词         | 标识符、错误码、配置键、字符串、正则 | 词法、路径、Symbol 命中    |
| SCIP/Symbol          | 定义、引用、实现、跨文件关系         | 精确 Symbol ID 和关系      |
| Dense Retrieval      | “哪里实现了降级策略”等语义问题       | Symbol/文件摘要向量        |
| Git Diff             | 为什么变化、何时引入、影响什么       | Commit、Hunk、PR、Issue    |
| Dependency Expansion | 变化影响和失效判断                   | 调用、导入、配置、测试关系 |

<a id="section-8-5"></a>
### 8.5 历史版本策略

MVP 不索引所有 Commit 的完整工作树，优先覆盖：

- 默认分支当前版本。

- 发布 Tag 和生产版本。

- 被 Run、Claim 或 Codex Episode 引用的 Commit。

- 最近活跃 PR/Commit。

- 用户显式请求后按需物化的历史版本。

同一 Blob 被多个 Commit 引用时复用解析结果和 Embedding；FileVersion 只保存版本关系和定位信息。

<a id="section-8-6"></a>
### 8.6 代码证据定位

```text
repo@commit:path#L120-L168
symbol: package.module.Class.method
blob: sha256/oid
```

回答当前行为时，引用必须落在已解析的当前 Commit；若只能获取旧版本，应明确注明版本不一致并降低结论强度。

<a id="section-9"></a>
## 9. Codex 会话知识链路

[返回目录](#toc)

<a id="section-9-1"></a>
### 9.1 接入方式

优先接入结构化事件，而不是抓取聊天 UI 或依赖未承诺稳定的私有本地格式。推荐入口包括 Codex App Server、非交互 JSONL 和 SDK/Wrapper 事件。Adapter 保存 Thread、Turn、Item、命令、文件变化、审批和完成状态，并映射到自己的稳定 Schema。

官方参考：

- [Codex App Server](https://developers.openai.com/codex/app-server)

- [Codex Non-interactive mode](https://developers.openai.com/codex/noninteractive)

- [Codex SDK](https://developers.openai.com/codex/sdk)

<a id="section-9-2"></a>
### 9.2 事件标准化

| **事件**               | **生成实体**     | **关键字段**                             |
|------------------------|------------------|------------------------------------------|
| Thread created/resumed | CodexThread      | thread_id、cwd、project、initial head    |
| Turn started/completed | CodexTurn        | turn_id、sequence、status、timestamps    |
| Message item           | Message/UserGoal | role、content、references                |
| Command item           | CommandExecution | argv、cwd、exit_code、stdout/stderr ref  |
| File change item       | FileChange/Patch | path、before/after hash、diff ref        |
| Approval item          | Approval         | request、decision、actor、scope          |
| Test/validation result | ValidationResult | command、status、artifact、duration      |
| Error/cancel           | Failure Event    | error class、retryability、affected item |

中间增量事件只用于流式展示；Item 的最终状态以完成事件或显式失败事件为准。

<a id="section-9-3"></a>
### 9.3 Development Episode

长会话不应仅按单条消息检索。系统根据目标连续性、文件集合、Git 状态和时间窗口聚合为 Episode：

```json
{
  "episode_id": "ep-88-04",
  "thread_id": "th_88",
  "goal": "降低检索阶段 P95 延迟",
  "alternatives": [
    "结果缓存",
    "批量重排",
    "降低 top_k"
  ],
  "decision": "先引入 query-aware cache，并保留回退",
  "commands": [
    "pytest ...",
    "python benchmark.py ..."
  ],
  "files_changed": [
    "src/cache.py",
    "src/retriever.py"
  ],
  "patch_ids": [
    "patch-9"
  ],
  "validation": [
    "unit tests passed",
    "benchmark incomplete"
  ],
  "commit_ids": [
    "git://.../31ab90"
  ],
  "unresolved": [
    "缺少长尾数据集验证"
  ]
}
```

Episode 摘要用于召回，原始 Item 用于引用和审计。摘要必须记录模型、Prompt、输入事件范围和版本。

<a id="section-9-4"></a>
### 9.4 Patch 到 Commit 的关联

关联优先级：

> **1.** 事件中显式记录 Commit SHA。
>
> **2.** Thread/Turn 前后的 Git HEAD 变化。
>
> **3.** Patch 内容哈希与 Commit Diff 的确定性匹配。
>
> **4.** 变化文件集合、Hunk 和时间窗口联合匹配。
>
> **5.** 分支、作者、工作树状态和相似度推断。
>
> **6.** LLM 语义推断。

前四项可形成高可信关系；第五和第六项必须保存候选、相似度和不确定性，不得自动将 proposed 升级为 committed。

<a id="section-9-5"></a>
### 9.5 会话事实裁决

| **会话陈述**          | **默认状态**                 | **升级条件**                          |
|-----------------------|------------------------------|---------------------------------------|
| “建议增加缓存”        | proposed                     | 用户接受或形成 Decision               |
| “已修改 retriever.py” | mentioned/executed candidate | FileChange/Patch 事件存在             |
| “修复已完成”          | claimed                      | Patch applied_as Commit               |
| “测试通过”            | claimed                      | 命令退出码、日志和 TestResult 存在    |
| “性能提升 12%”        | claimed                      | 原始 Run、Metric、Baseline 和条件匹配 |

<a id="section-9-6"></a>
### 9.6 会话检索信号

- Goal 和 Decision 的语义相似度。

- 文件路径、Symbol、Commit、PR、Experiment ID 的精确命中。

- 状态过滤：仅讨论、已执行、已提交、已验证、失败、未解决。

- 时间范围和与当前版本的距离。

- 用户、团队和项目权限。

- 证据强度：原始事件优先于 Episode 摘要。

<a id="section-10"></a>
## 10. 科研文档与实验链路

[返回目录](#toc)

<a id="section-10-1"></a>
### 10.1 两层事实模型

```text
文档层：描述、解释、组织和发布结论
实验层：记录实际执行、参数、数据、指标和产物
```

当二者冲突时，系统不能悄悄选择一方；应显示原始 Run 与文档陈述的差异，并依据查询类型决定权威度。

<a id="section-10-2"></a>
### 10.2 文档解析

推荐 Docling 负责通用文档结构、版面、表格、图片和公式，GROBID 负责学术元数据、章节、参考文献和 Citation Context。解析结果保留页码、坐标和阅读顺序。

```text
DocumentVersion
├── Page
├── Section
├── Paragraph
├── Table → TableCell
├── Figure → Caption / Region
├── Formula
├── Citation → BibliographicEntity
└── Claim
```

表格和图是一等实体，不能只变成一段 Markdown。对于识别质量低的表格，应保存原图、结构化候选、置信度和人工校正版本。

<a id="section-10-3"></a>
### 10.3 Claim 模型

```json
{
  "claim_id": "claim://report-2026/v3/C12",
  "claim_text": "在 Dataset-X 上，Recall@10 相比基线提高 4.2 个百分点。",
  "subject": "Retriever-v2",
  "metric_definition": "Recall@10",
  "reported_value": 0.824,
  "baseline_value": 0.782,
  "delta": 0.042,
  "dataset_scope": "Dataset-X:v4/test",
  "conditions": [
    "seed=42",
    "top_k=50"
  ],
  "supporting_table_ids": [
    "table://report-2026/v3/T6"
  ],
  "candidate_run_ids": [
    "run://rag/E17/R17-03"
  ],
  "status": "reported",
  "confidence": 0.88
}
```

Claim 状态可取：reported、verified、partially_supported、contradicted、superseded、potentially_stale。

<a id="section-10-4"></a>
### 10.4 实验事实源

MLflow 可作为 Experiment/Run 的主事实源；DVC 或 lakeFS 提供数据/模型版本；OpenLineage 统一跨系统 Job、Run、Dataset 谱系；Notebook 则通过 Papermill 等方式记录参数化执行。

每次 Run 建议强制写入：

```text
experiment_id / run_id
repository_url / branch / commit_sha / dirty_state
config_hash / config_artifact
dataset_id / dataset_version / split
environment_hash / container_digest / dependency_lock
seed / parameters / metrics
started_at / completed_at / status
artifact_uri / logs / notebook_run
```

<a id="section-10-5"></a>
### 10.5 文档到 Run 的匹配

匹配信号按可靠性排序：

> **1.** 文档中显式 Run ID、Experiment ID 或 Artifact URI。
>
> **2.** 表格脚注或元数据中的 Commit、Config Hash、Dataset Version。
>
> **3.** 指标名称、数值、Baseline、Seed 和时间窗口的规则匹配。
>
> **4.** 表格行列与 Run 集合的组合优化。
>
> **5.** LLM 语义推断和人工确认。

系统应保存候选匹配，而不是只保留最高分结果；多个 Run 共同汇总为表格数字时，应记录聚合函数、样本数、方差和异常剔除规则。

<a id="section-10-6"></a>
### 10.6 Notebook 处理

Notebook 不能只按 JSON 文本切块。建议实体化：

```text
NotebookTemplate
NotebookRun
CellDefinition
CellExecution
InjectedParameter
CellOutput
GeneratedArtifact
```

代码 Cell 与输出 Cell 分别索引，执行序号、异常、耗时和生成图表保留。Notebook Diff 应使用结构化工具，而非普通文本 Diff。

<a id="section-10-7"></a>
### 10.7 实验复现包

面向复现查询，Evidence Service 生成：

- Git Commit/Tag 与 Dirty Diff。

- 数据集 ID、版本、获取方式和校验哈希。

- 配置文件、参数覆盖和随机种子。

- 环境锁文件、容器镜像和硬件信息。

- 运行命令、Notebook 参数和前置步骤。

- 原始日志、Metric、Artifact 和已知偏差。

- 相关 Codex Episode、失败尝试和未解决事项。

<a id="section-11"></a>
## 11. 索引与存储设计

[返回目录](#toc)

<a id="section-11-1"></a>
### 11.1 MVP 存储组合

![图 4 推荐部署拓扑](多源研发证据RAG系统_项目设计文档_v1.0_assets/image4.png)

图 4 推荐部署拓扑

| **数据类型**          | **MVP 存储**               | **理由**                              |
|-----------------------|----------------------------|---------------------------------------|
| 实体、关系、版本、ACL | PostgreSQL                 | 事务一致性、递归查询、JSONB、成熟运维 |
| 向量                  | pgvector                   | 与元数据同库，减少同步和删除风险      |
| 普通全文              | PostgreSQL FTS             | MVP 足够，支持权限和结构过滤          |
| 代码精确搜索          | Zoekt                      | 面向代码的快速子串、正则和多仓搜索    |
| 原始文件与 Artifact   | S3/MinIO                   | 低成本、版本化、生命周期和对象锁      |
| 事件与 Workflow       | Temporal + Raw Event Store | 长任务重试、恢复和重放                |
| 热缓存                | Redis                      | Scope、Evidence Pack 和短期结果缓存   |

<a id="section-11-2"></a>
### 11.2 搜索视图

同一实体可生成多个 Search View：

```text
CodeSymbol.raw 原始代码
CodeSymbol.summary 职责和接口摘要
CodeSymbol.change_history 主要变更摘要
DevelopmentEpisode.summary 目标/决策/结果/未解决
ExperimentRun.card 参数、指标、Commit、Dataset
Claim.card 结论、条件、支持/反证
DocumentSection.text 正文与结构上下文
```

Search View 记录来源实体、视图类型、生成器版本和有效区间，不复制权限逻辑。

<a id="section-11-3"></a>
### 11.3 向量策略

- 代码原文和代码摘要可使用不同 Embedding 模型或字段权重。

- 不为每个 Commit 重复 Embedding 未变化 Blob。

- 表格使用结构化文本、标题、行列语义和 Metric Definition 共同生成视图。

- 会话仅 Embedding Episode 和高价值 Decision；原始 Item 依靠精确过滤和局部语义检索。

- 所有向量记录 model_id、维度、归一化方式和生成时间，支持多版本并存和灰度切换。

<a id="section-11-4"></a>
### 11.4 索引 Generation

```text
index_generation
├── source_snapshot
├── parser_versions
├── embedding_versions
├── relation_rule_versions
├── build_started_at / completed_at
├── validation_report
└── publish_status
```

查询请求绑定一个已发布 Generation，保证一次请求中的实体、关系和向量视图一致。增量更新可构建 Delta 并合并，但发布前必须完成数据质量检查。

<a id="section-11-5"></a>
### 11.5 扩展条件

| **触发条件**                          | **演进方向**                       |
|---------------------------------------|------------------------------------|
| 向量数量和过滤复杂度显著增长          | pgvector → Qdrant 等独立向量服务   |
| 文档/会话全文量大、聚合需求复杂       | PostgreSQL FTS → OpenSearch        |
| 图遍历超过 3 跳且多种时间查询成为核心 | Edge Table → Graphiti/专用图数据库 |
| 组织级资产治理和连接器成为主要需求    | 引入 OpenMetadata 控制面           |

> **基础设施克制**
>
> MVP 不应同时部署 PostgreSQL、OpenSearch、Qdrant、Neo4j 和多个缓存层。过早拆分会使权限、删除、版本和索引一致性成为主要成本。

<a id="section-12"></a>
## 12. 查询理解与检索编排

[返回目录](#toc)

<a id="section-12-1"></a>
### 12.1 查询分类

```text
current_implementation 当前代码行为
historical_implementation 历史版本行为
change_trace 变更过程和影响
rationale 设计原因和权衡
experiment_validation 实验是否支持结论
claim_verification 文档 Claim 核验
reproduction 实验复现
staleness_check 当前版本是否使旧结论失效
global_synthesis 跨项目/跨时间主题总结
```

<a id="section-12-2"></a>
### 12.2 Scope 解析

请求 Scope 包括：

```json
{
  "project_id": "project-rag",
  "repositories": [
    "repo://acme/rag-core"
  ],
  "branch": "main",
  "commit": null,
  "experiments": [
    "E17"
  ],
  "documents": [],
  "time_range": null,
  "source_types": [
    "code",
    "codex",
    "experiment",
    "document"
  ]
}
```

若用户说“现在”“最新版”，Query Service 必须解析 Branch 当前指针并记录查询时间；若用户指定历史日期，则按有效时间解析当时 Branch/Tag 和文档版本。

<a id="section-12-3"></a>
### 12.3 分源召回

<a id="代码"></a>
#### 代码

| Zoekt lexical + SCIP symbol + dense semantic + Git diff + dependency expansion |
|--------------------------------------------------------------------------------|

<a id="codex"></a>
#### Codex

| Goal/Decision semantic + path/commit/experiment exact + status/time filters |
|-----------------------------------------------------------------------------|

<a id="科研"></a>
#### 科研

| Experiment/Run/Metric exact + BM25 + dense + table/claim structured filters |
|-----------------------------------------------------------------------------|

各通道返回统一 Candidate：

```json
{
  "entity_id": "...",
  "source_type": "code",
  "retrieval_channel": "scip_reference",
  "raw_score": 0.91,
  "matched_fields": [
    "symbol",
    "path"
  ],
  "version_alignment": "exact",
  "authority_hint": "primary",
  "evidence_locator": "repo@sha:path#Lx-Ly"
}
```

<a id="section-12-4"></a>
### 12.4 关系扩展

扩展遵循意图模板，避免无约束图遍历。例如 staleness_check：

```text
Claim → supported_by Run → uses Commit A
Current Branch → Commit B
Commit A..B → changed Symbol / Config / Dataset
Changed Entity → dependency/test/claim impact
```

每次扩展记录路径、边派生方式和累计置信度。推断边不能使整条路径被标为确定性。

<a id="section-12-5"></a>
### 12.5 重排与融合

```text
FinalScore =
lexical_score
+ dense_score
+ entity_match
+ graph_proximity
+ source_authority(intent)
+ version_alignment
+ temporal_alignment
+ evidence_diversity
- stale_penalty
- uncertainty_penalty
- redundancy_penalty
```

不同意图使用不同权重和证据预算。当前实现问题优先当前代码和测试；性能问题优先原始 Run；设计原因优先已接受 Decision、PR 和会话证据。

<a id="section-12-6"></a>
### 12.6 证据预算

模型输入不按固定 Top-K，而按证据角色分配：

```text
primary facts 4–8 条
supporting context 4–10 条
counter evidence 0–5 条
version diffs 0–8 条
inferred relations 0–4 条
missing evidence 0–5 条
```

多条重复摘要应合并，原始证据保留引用映射。若关键证据缺失，系统应返回“证据不足”而不是扩充弱相关 Chunk。

<a id="section-13"></a>
## 13. Evidence Pack、生成与引用

[返回目录](#toc)

<a id="section-13-1"></a>
### 13.1 Evidence Pack 结构

Evidence Pack 是查询服务与生成模型之间的稳定契约：

```json
{
  "query_id": "q-uuid",
  "question": "当前 main 是否仍然支持实验 E17 的结论？",
  "intent": "staleness_check",
  "resolved_scope": {
    "repository": "repo://acme/rag-core",
    "current_commit": "9f28c1",
    "experiment": "experiment://rag/E17",
    "experiment_commit": "31ab90",
    "as_of": "2026-07-21T14:00:00Z"
  },
  "verified_facts": [],
  "supporting_evidence": [],
  "counter_evidence": [],
  "version_differences": [],
  "inferred_relations": [],
  "missing_evidence": [],
  "citation_map": {},
  "decision": {
    "status": "potentially_stale",
    "confidence": 0.78,
    "rule_version": "staleness-v2"
  }
}
```

<a id="section-13-2"></a>
### 13.2 生成约束

- 结论句必须关联一条或多条 Citation ID。

- 推断性内容使用“可能、推测、尚未确认”等显式措辞。

- 回答当前行为时必须展示 Commit/Branch 和查询时间。

- 数值结论必须引用 MetricResult 或文档中的明确位置；优先引用原始 Run。

- 存在反证时必须展示，不允许只选择支持性证据。

- Evidence Pack 缺关键字段时，回答应说明缺口和下一步验证动作。

- 模型不得自行编造未出现在 Citation Map 中的路径、Run ID、Commit 或指标。

<a id="section-13-3"></a>
### 13.3 引用协议

| **来源**   | **引用格式**                 | **示例**                                  |
|------------|------------------------------|-------------------------------------------|
| 代码       | repo@commit:path#Lx-Ly       | rag-core@31ab90:src/retriever.py#L91-L146 |
| Symbol     | symbol:{qualified-name}      | symbol:rag.retriever.Retriever.search     |
| Codex      | thread/turn/item             | thread:th_88/turn:t3/item:i7              |
| 命令/Patch | command:{id} / patch:{hash}  | patch:sha256:...                          |
| 实验       | experiment/run               | experiment:E17/run:R17-03                 |
| 指标       | metric:{name}@{scope}        | metric:Recall@10@Dataset-X:v4/test        |
| 文档       | document/version/page/object | report-2026/v3/page:14/table:6            |

<a id="section-13-4"></a>
### 13.4 回答模板

```text
结论
- 状态：仍然有效 / 可能失效 / 已反驳 / 证据不足
- 适用范围：仓库、Branch、Commit、实验和时间
主要依据
- 代码依据
- 实验依据
- 研发过程依据
- 文档依据
版本变化与冲突
- 关键 Diff
- 支持证据与反证
不确定性
- 推断关系
- 缺失证据
- 建议验证动作
```

<a id="section-13-5"></a>
### 13.5 Evidence UI

推荐界面由五个同步区域组成：

> **1. 回答区**：结论、范围、置信度和引用。
>
> **2. 证据侧栏**：按 Primary/Supporting/Counter/Missing 分类。
>
> **3. 代码面板**：文件、行号、Symbol、Commit Diff 和测试。
>
> **4. 实验面板**：Run、参数、数据版本、指标、图表和 Artifact。
>
> **5. 时间线面板**：Codex Episode、Decision、Patch、Commit、Run 和文档版本。

用户可将推断关系标记为确认/拒绝，反馈进入关系审核队列，但不能直接覆盖源事实。

<a id="section-14"></a>
## 14. 版本有效性与结论失效检测

[返回目录](#toc)

<a id="section-14-1"></a>
### 14.1 判定链路

![图 5 结论失效检测](多源研发证据RAG系统_项目设计文档_v1.0_assets/image5.png)

图 5 结论失效检测

核心步骤：

> **1.** 从 Claim 找到支持或反驳它的 Run。
>
> **2.** 获取 Run 使用的 Commit、Config、DatasetVersion 和环境。
>
> **3.** 解析目标 Branch 当前 Commit 或指定时间点 Commit。
>
> **4.** 计算 Commit A 到 B 的 Diff、配置差异和数据差异。
>
> **5.** 将变化映射到 Claim 的关键依赖 Symbol、Metric 和条件。
>
> **6.** 查找当前版本上的复验 Run、测试和反证。
>
> **7.** 输出状态、影响解释、证据路径和建议动作。

<a id="section-14-2"></a>
### 14.2 关键依赖模型

Claim 可显式或推断关联：

```text
required_symbols
required_config_keys
required_dataset_properties
required_metric_definitions
required_environment_constraints
```

变化影响分为：

- **直接变化**：Claim 关联的 Symbol、配置键、数据版本直接改变。

- **依赖变化**：调用方、被调用方、公共组件或预处理链路改变。

- **行为变化**：测试、Benchmark 或当前 Run 指标改变。

- **无关变化**：文档、注释或不在依赖闭包中的代码变化。

<a id="section-14-3"></a>
### 14.3 判定规则示例

```text
rule: claim-staleness-v2
if:
- no_current_revalidation_run
- any:
- critical_symbol_changed
- critical_config_changed
- dataset_version_changed
then:
status: potentially_stale
confidence: weighted_impact_score
requirements:
- show_changed_entities
- show_experiment_commit
- recommend_revalidation
```

<a id="section-14-4"></a>
### 14.4 输出状态

| **状态**              | **定义**                           | **典型条件**                         |
|-----------------------|------------------------------------|--------------------------------------|
| valid                 | 当前版本有等价证据或关键依赖未变化 | 当前 Run 复验通过；关键闭包无变化    |
| potentially_stale     | 关键依赖已变但缺当前复验           | Symbol/Config/Dataset 变化，无新 Run |
| contradicted          | 当前证据明确反驳 Claim             | 当前 Run 指标不满足 Claim，条件可比  |
| superseded            | 新 Claim 或方案明确替代旧结论      | 文档/Decision 标记替代且有证据       |
| insufficient_evidence | 无法定位原 Run、版本或条件         | 缺 Commit、Run、配置或数据版本       |

<a id="section-14-5"></a>
### 14.5 避免误报

- Diff 只是风险信号，不等于结论必然失效。

- 指标变化需验证 MetricDefinition、数据切分和统计条件可比。

- 仅格式化或重命名变化应通过 Symbol Lineage 降权。

- Dirty Worktree 实验需保存未提交 Diff；否则不能把 Git SHA 当成完整代码快照。

- 多随机种子结论需比较统计分布，不只比较单一 Run。

<a id="section-15"></a>
## 15. API 与事件契约

[返回目录](#toc)

<a id="section-15-1"></a>
### 15.1 Query API

```http
POST /v1/query
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "question": "为什么当前 main 可能不再满足 E17 的结论？",
  "scope": {
    "project_id": "project-rag",
    "repository_ids": [
      "repo://acme/rag-core"
    ],
    "branch": "main",
    "experiment_ids": [
      "E17"
    ]
  },
  "mode": "answer",
  "as_of": "2026-07-21T14:00:00Z",
  "include": [
    "code",
    "codex",
    "experiment",
    "document"
  ]
}
```

响应：

```json
{
  "query_id": "q-...",
  "resolved_scope": {},
  "answer": {
    "status": "potentially_stale",
    "text": "...",
    "citations": [
      "c1",
      "c2",
      "c3"
    ]
  },
  "evidence_pack": {},
  "trace_id": "..."
}
```

<a id="section-15-2"></a>
### 15.2 Evidence Search API

| POST /v1/evidence/search |
|--------------------------|

用于 IDE、Agent 和调试界面，只返回候选证据与关系路径，不进行自然语言生成。支持：

```text
intent / entity_types / source_types
branch / commit / time range
status / derivation / minimum confidence
include_edges / max_hops / result budget
```

<a id="section-15-3"></a>
### 15.3 Entity 与 Lineage API

```http
GET /v1/entities/{entity_id}
GET /v1/entities/{entity_id}/lineage?direction=both&max_hops=2
GET /v1/claims/{claim_id}/validity?branch=main
GET /v1/runs/{run_id}/reproduction-pack
```

<a id="section-15-4"></a>
### 15.4 Ingestion API

```http
POST /v1/ingestion/events
POST /v1/ingestion/rebuild
GET /v1/ingestion/workflows/{workflow_id}
POST /v1/relations/{edge_id}/review
```

重建操作必须指定 Scope、原因和目标版本，默认写入新的 Index Generation，不原地覆盖线上索引。

<a id="section-15-5"></a>
### 15.5 事件主题

```text
source.raw.persisted
source.deleted
parse.completed / parse.failed
entity.upserted / entity.tombstoned
edge.proposed / edge.confirmed / edge.rejected
index.generation.published
claim.validity.changed
query.completed
security.access_denied
```

事件 Schema 需版本化，消费者声明兼容范围；跨服务消息带 trace_id、project_id、acl_ref 和 schema_version。

<a id="section-16"></a>
## 16. 安全、权限与治理

[返回目录](#toc)

<a id="section-16-1"></a>
### 16.1 身份与授权

推荐 Keycloak 提供 OIDC/OAuth/SAML 身份，OpenFGA 提供关系式资源授权：

```text
user:alice member_of team:rag
team:rag can_read project:rag
project:rag contains repo:rag-core
project:rag contains experiment:E17
repo:rag-core contains codex_thread:th_88
```

所有 Chunk、Search View 和派生实体继承 Source Object ACL。Query Service 先计算允许访问对象集合或授权过滤表达式，再调用任何检索器。

<a id="section-16-2"></a>
### 16.2 安全边界

- 只读 RAG 与可写 Agent/执行器分离部署和凭证。

- 检索内容视为不可信数据，不允许其中的提示注入覆盖系统规则。

- 命令输出、日志、Notebook 和会话摄取前执行 Secret/PII 扫描。

- 原始 Artifact 使用短期签名 URL，日志中不记录完整敏感 URL。

- 任何跨项目聚合必须按用户权限重新计算，不能复用其他用户的 Evidence Pack。

- 管理员也应通过审计身份访问，不提供无日志的超级查询路径。

<a id="section-16-3"></a>
### 16.3 敏感数据分级

| **等级**     | **示例**                       | **默认策略**                     |
|--------------|--------------------------------|----------------------------------|
| Public       | 开源仓库、公开论文             | 可索引，仍保留来源和许可证       |
| Internal     | 内部代码、一般实验             | 项目 ACL、加密、审计             |
| Confidential | 未发布论文、客户数据、模型权重 | 细粒度 ACL、禁外部模型、严格导出 |
| Restricted   | 密钥、个人敏感信息、受监管数据 | 默认不索引或只保存脱敏元数据     |

<a id="section-16-4"></a>
### 16.4 Secret 和内容安全

Gitleaks 等工具用于静态 Secret 扫描；对可验证凭证或复杂来源可引入额外扫描器。发现 Secret 时：

```text
quarantine raw object
→ block indexing
→ emit security event
→ notify owner
→ preserve minimal audit metadata
→ reingest after remediation
```

<a id="section-16-5"></a>
### 16.5 治理与审计

必须记录：

- 谁在什么 Scope 下发起了查询。

- 访问了哪些 Source Object 和 Citation。

- 使用的 Index Generation、模型、Prompt 和规则版本。

- 哪些推断关系参与回答，置信度和审核状态是什么。

- 导出、分享、关系确认、手工修订和删除操作。

- 数据保留、合规删除和衍生对象清理状态。

<a id="section-17"></a>
## 17. 可观测性、评测与质量门禁

[返回目录](#toc)

<a id="section-17-1"></a>
### 17.1 Trace 模型

使用 OpenTelemetry/OpenInference 统一记录：

```text
QUERY
├── AUTHORIZATION
├── INTENT_RESOLUTION
├── RETRIEVER(code/session/research)
├── GRAPH_EXPANSION
├── RERANKER
├── EVIDENCE_PACK
├── LLM
└── CITATION_VALIDATION
```

Span 附加 query_id、project_id、index_generation、entity_ids、commit_sha、run_id、Token、延迟和成本。敏感正文不默认写入 Trace。

<a id="section-17-2"></a>
### 17.2 离线评测集

阶段 0 建立 30–50 个 Golden Questions，后续扩展到至少 200 个，覆盖：

- 单源代码、会话、实验和文档问题。

- 两源和三源证据链问题。

- 当前/历史版本混淆问题。

- Claim 支持、反证和失效问题。

- 权限、删除和提示注入问题。

- 无答案和证据不足问题。

每个样本标注：正确答案要点、必需实体、必需证据路径、允许版本、禁止引用、冲突和预期状态。

<a id="section-17-3"></a>
### 17.3 指标体系

| **层级** | **指标**                                                        |
|----------|-----------------------------------------------------------------|
| 检索     | Recall@K、MRR、nDCG、Entity Recall、Source Recall               |
| 跨源     | Evidence Path Recall、Commit Accuracy、Run ID Accuracy          |
| 版本     | Branch Accuracy、Wrong-Version Rate、Temporal Scope Accuracy    |
| 实验     | Dataset Version Accuracy、Config Hash Accuracy、Metric Match    |
| 生成     | Citation Precision、Citation Coverage、Unsupported Claim Rate   |
| 冲突     | Conflict Detection F1、Conflict Disclosure Rate                 |
| 时效     | Stale Evidence Rate、Validity Classification F1                 |
| 安全     | Unauthorized Evidence Leakage、Secret Leakage、Deletion Success |
| 运维     | P50/P95、错误率、重试率、索引新鲜度、成本/查询                  |

<a id="section-17-4"></a>
### 17.4 质量门禁

发布新 Parser、Embedding、Reranker、规则或 Prompt 前：

> **1.** 在固定 Golden Set 上运行基线对比。
>
> **2.** Wrong-Version、ACL Leakage 和 Citation Precision 不得退化。
>
> **3.** 关键问题 Evidence Path Recall 达到阈值。
>
> **4.** 对性能和成本进行 P95 回归检查。
>
> **5.** 线上采用小流量 Shadow/Canary，不共享未授权 Trace。
>
> **6.** 失败可切回上一 Index Generation 和模型配置。

<a id="section-17-5"></a>
### 17.5 人工评审

模型指标无法完全覆盖科研正确性。每个里程碑安排研究员、工程师和安全人员共同抽样，重点审查：

- Claim 条件是否被遗漏。

- Run 是否可比、指标定义是否一致。

- 代码变化是否真正影响 Claim。

- 会话中被否决方案是否误当作 Decision。

- 引用是否能从 UI 跳到原始证据。

<a id="section-18"></a>
## 18. 部署、容量与成本

[返回目录](#toc)

<a id="section-18-1"></a>
### 18.1 环境划分

```text
DEV：小规模真实脱敏数据，快速重建
STAGING：生产拓扑，完整回归和安全测试
PROD：严格 ACL、审计、备份、密钥和变更门禁
```

每个环境使用独立对象桶、数据库、索引 Generation 和模型凭证；禁止从生产复制敏感原文到开发环境。

<a id="section-18-2"></a>
### 18.2 容量估算方法

容量按以下单位估算：

```text
Repositories / active branches / selected commits
Unique Git blobs / symbols / references
Codex threads / items / command logs
Documents / pages / tables / figures
Experiment runs / metrics / artifacts
Search views / embeddings / graph edges
Daily change rate / retention window
```

Blob 去重、按需历史版本和分层摘要是主要成本控制手段。实验 Artifact 大文件保留在源系统或对象存储，RAG 只同步元数据、摘要和可授权引用。

<a id="section-18-3"></a>
### 18.3 性能策略

- 并行分源召回，设置每通道预算和超时。

- 先用精确 ID/过滤缩小范围，再执行向量与图扩展。

- Cache Key 包含用户授权版本、Scope、Branch 指针和 Index Generation。

- 常用实体卡片预计算；深度失效调查允许增量返回状态。

- 对超大仓库按项目、Branch 和索引分片；Zoekt 索引与事务库解耦。

<a id="section-18-4"></a>
### 18.4 成本观测

按 Query 记录：

```text
retrieval CPU/storage reads; vector/reranker requests
LLM input/output tokens; document parsing/OCR cost
index rebuild cost; cache hit rate
```

成本优化不得牺牲版本正确性与引用质量；应优先去重 Chunk、裁剪弱证据并约束图扩展。

<a id="section-19"></a>
## 19. 实施路线与团队分工

[返回目录](#toc)

<a id="section-19-1"></a>
### 19.1 分阶段路线

![图 6 实施阶段](多源研发证据RAG系统_项目设计文档_v1.0_assets/image6.png)

图 6 实施阶段

<a id="阶段-0-领域模型与评测基线"></a>
#### 阶段 0：领域模型与评测基线

产出：Ontology v0.1、稳定 URI、关系枚举、状态机、事件信封、30–50 个 Golden Questions、PoC 数据清单。

退出标准：团队能对同一问题给出一致的“正确证据路径”，并明确哪些关系是确定性、哪些是推断。

<a id="阶段-1-确定性证据链"></a>
#### 阶段 1：确定性证据链

只支持 1 个项目、1–2 个仓库、一组 Codex 会话、一批文档和一个实验项目。打通：

```text
Thread → Patch → Commit
Commit → File/Symbol
Run → Commit/Dataset/Config
Document → Claim → Run
```

退出标准：关键对象可从 UI 双向跳转；引用定位可靠；删除和权限传播通过测试。

<a id="阶段-2-三源联合检索"></a>
#### 阶段 2：三源联合检索

实现 Query Intent、分源召回、关系模板、重排、Evidence Pack、答案和引用 UI。

退出标准：跨源 Golden Set 的 Evidence Path Recall、Commit Accuracy、Citation Precision 达到阈值。

<a id="阶段-3-失效与冲突检测"></a>
#### 阶段 3：失效与冲突检测

实现 Diff 影响、关键依赖、当前复验、Claim 状态和通知。

退出标准：人工评审的典型失效样本具有可接受 Precision/Recall，且输出解释可审计。

<a id="阶段-4-组织化与智能化"></a>
#### 阶段 4：组织化与智能化

按需求引入 Graphiti、OpenMetadata、OpenSearch/Qdrant、跨项目主题图和 Agent 自动调查。

退出标准：新组件解决已量化瓶颈，而不是单纯增加架构复杂度。

<a id="section-19-2"></a>
### 19.2 团队分工

| **角色**        | **主要职责**                                             |
|-----------------|----------------------------------------------------------|
| 产品/领域负责人 | 场景、Golden Questions、证据权威规则和验收               |
| 平台架构        | 服务边界、事件契约、部署、HA 和成本                      |
| 数据工程        | Adapter、Raw Store、Workflow、质量和删除传播             |
| 代码智能        | Git、Tree-sitter、SCIP、Zoekt、Diff 和影响分析           |
| RAG/算法        | Query、Retriever、Reranker、Claim/Episode、Evidence Pack |
| 科研平台        | MLflow/DVC/OpenLineage、Run 规范和复现包                 |
| 前端            | Evidence UI、Diff、时间线、表图和关系审核                |
| 安全治理        | 身份、OpenFGA、Secret、审计、数据分级和合规              |
| QA/Eval         | Golden Set、回归门禁、红队、性能与线上抽样               |

<a id="section-19-3"></a>
### 19.3 建议迭代节奏

- 每个迭代以一组真实 Golden Questions 为主线，而不是以组件安装为主线。

- 每条新关系先定义证据与错误案例，再开发抽取逻辑。

- 每次模型/Prompt 改动都必须可回滚，并与上一版本对比。

- 每月审查无用索引、低价值摘要和推断关系，控制知识污染。

<a id="section-20"></a>
## 20. 风险、开放问题与验收标准

[返回目录](#toc)

<a id="section-20-1"></a>
### 20.1 主要风险

| **风险**           | **影响**           | **缓解措施**                                               |
|--------------------|--------------------|------------------------------------------------------------|
| 跨源 ID 缺失       | 无法形成确定性链路 | 在 Run/文档/会话中强制记录 Commit、Run、Config、Dataset ID |
| 会话噪声与错误声明 | 将建议误当事实     | 状态机、原始事件、Commit/Test/Run 逐级验证                 |
| PDF 表格解析错误   | 指标映射错误       | 置信度、原图并列、人工校正和结构验证                       |
| 历史版本规模爆炸   | 成本和检索污染     | Blob 去重、精选 Commit、按需物化                           |
| 权限与派生摘要泄漏 | 高安全风险         | 检索前 ACL、derived_from、删除级联、独立缓存               |
| LLM 关系污染       | 错误知识长期传播   | 推断边隔离、审核状态、置信度和有效期                       |
| 多组件一致性       | 删除和版本不一致   | MVP 以 PostgreSQL 为中心、Generation 发布、事件审计        |
| 评测不代表真实使用 | 优化方向错误       | 真实团队问题、线上反馈和跨角色人工评审                     |

<a id="section-20-2"></a>
### 20.2 开放问题

- Codex 会话的保留范围、用户隐私和团队共享边界如何定义？

- 是否要求所有实验写入标准 Run Metadata；对历史实验如何补录？

- Claim 的人工确认责任属于论文作者、实验 Owner 还是技术负责人？

- 数据集版本采用 DVC、lakeFS、内部 Catalog 还是组合方案？

- 当前代码影响分析需要到 Symbol、调用图还是数据流级别？

- 外部论文和开源仓库的许可证、引用和再分发规则如何落地？

- 高敏数据是否允许调用外部 Embedding/LLM，还是必须本地推理？

<a id="section-20-3"></a>
### 20.3 MVP 验收标准

<a id="证据链"></a>
#### 证据链

- ≥ 90% 选定 Codex Episode 能定位到相关 Patch 或明确标记“未提交”。

- ≥ 95% 选定 Run 能定位正确 Commit；无法定位时明确报错，不猜测。

- ≥ 85% 选定数值 Claim 能连接表格/图和一个或多个候选 Run。

<a id="检索与生成"></a>
#### 检索与生成

- 关键 Golden Set 的 Commit Accuracy ≥ 95%。

- Evidence Path Recall ≥ 85%。

- Citation Precision ≥ 95%，Citation Coverage ≥ 90%。

- Unsupported Claim Rate ≤ 3%。

- Wrong-Version Answer Rate ≤ 2%。

<a id="安全与运维"></a>
#### 安全与运维

- Unauthorized Evidence Leakage = 0。

- 删除/撤权后，查询面即时阻断，派生清理在规定窗口完成。

- 摄取任务可重试、可恢复，重复事件不生成重复实体。

- 任意线上回答可追溯到 Index Generation、模型和证据集合。

<a id="section-20-4"></a>
### 20.4 最终建议

> **推荐起点**
>
> 首先完成 Ontology v0.1、稳定 ID、事件信封、证据状态机和 Golden Questions；然后以一个真实项目打通确定性证据链。只有当检索质量、版本正确性、引用和权限通过评测后，再引入复杂图系统和组织级元数据平台。

<a id="appendix-a"></a>
## 附录 A. 核心表结构建议

[返回目录](#toc)

<a id="appendix-a-1"></a>
### A.1 Entity 表

```sql
CREATE TABLE entity (
  id text PRIMARY KEY,
  project_id uuid NOT NULL,
  entity_type text NOT NULL,
  source_system text NOT NULL,
  source_uri text,
  source_version text,
  content_hash text,
  event_time timestamptz,
  observed_at timestamptz NOT NULL,
  valid_from timestamptz,
  valid_to timestamptz,
  acl_ref text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  provenance jsonb NOT NULL DEFAULT '{}',
  tombstoned_at timestamptz
);
```

<a id="appendix-a-2"></a>
### A.2 Search View 表

```sql
CREATE TABLE search_view (
  id uuid PRIMARY KEY,
  entity_id text NOT NULL REFERENCES entity(id),
  view_type text NOT NULL,
  text_content text,
  structured_content jsonb,
  embedding_model text,
  embedding vector,
  generator_version text NOT NULL,
  acl_ref text NOT NULL,
  valid_from timestamptz,
  valid_to timestamptz
);
```

<a id="appendix-a-3"></a>
### A.3 Claim Validity 表

```sql
CREATE TABLE claim_validity (
  claim_id text NOT NULL,
  target_scope jsonb NOT NULL,
  status text NOT NULL,
  confidence numeric(4,3),
  experiment_commit text,
  target_commit text,
  changed_entities jsonb,
  supporting_evidence jsonb,
  counter_evidence jsonb,
  missing_evidence jsonb,
  rule_version text NOT NULL,
  evaluated_at timestamptz NOT NULL,
  PRIMARY KEY (claim_id, target_scope, rule_version)
);
```

<a id="appendix-b"></a>
## 附录 B. Golden Questions 模板

[返回目录](#toc)

```text
id: GQ-STALENESS-001
question: 当前 main 是否仍支持实验 E17 的 Recall 提升结论？
intent: staleness_check
scope:
project: project-rag
repository: repo://acme/rag-core
branch: main
must_retrieve:
- claim://report-2026/v3/C12
- run://rag/E17/R17-03
- git://acme/rag-core/commit/31ab90
must_follow_edges:
- Claim supported_by Run
- Run uses Commit
expected_status: potentially_stale
forbidden:
- wrong branch code
- unsupported numeric claim
notes:
- top_k 配置已变化，但无当前版本复验 Run
```

<a id="appendix-c"></a>
## 附录 C. 架构决策记录（ADR）清单

[返回目录](#toc)

| **ADR** | **主题** | **决策摘要**                                          |
|---------|----------|-------------------------------------------------------|
| ADR-001 | 产品定位 | 版本化研发证据系统，而非统一 Chunk 知识库             |
| ADR-002 | 数据模型 | Entity/Edge/Evidence + 双时间 + 稳定 URI              |
| ADR-003 | 检索     | 分源召回、Late Fusion、意图相关权威度                 |
| ADR-004 | 存储     | MVP 以 PostgreSQL/pgvector 为中心，Zoekt 专用代码搜索 |
| ADR-005 | 推断关系 | 与确定性关系隔离，必须保存置信度和审核状态            |
| ADR-006 | 生成     | 模型仅消费 Evidence Pack，并强制 Citation Map         |
| ADR-007 | 权限     | ACL 在任何检索和缓存之前执行                          |
| ADR-008 | 发布     | 索引使用 Generation/Snapshot，支持回滚                |
| ADR-009 | 历史代码 | Blob 去重、精选 Commit、按需物化                      |
| ADR-010 | 失效判断 | Diff 是风险信号，需结合关键依赖和复验结果             |

<a id="appendix-d"></a>
## 附录 D. 技术依据与官方资料

[返回目录](#toc)

- [Codex App Server](https://developers.openai.com/codex/app-server)

- [Tree-sitter](https://github.com/tree-sitter/tree-sitter)

- [SCIP Code Intelligence Protocol](https://github.com/scip-code/scip)

- [Zoekt](https://github.com/sourcegraph/zoekt)

- [Docling](https://github.com/docling-project/docling)

- [GROBID](https://github.com/grobidOrg/grobid)

- [MLflow](https://github.com/mlflow/mlflow)

- [OpenLineage](https://github.com/OpenLineage/OpenLineage)

- [OpenMetadata](https://github.com/open-metadata/OpenMetadata)

- [Temporal](https://github.com/temporalio/temporal)

- [OpenInference](https://github.com/Arize-ai/openinference)

- [OpenFGA](https://github.com/openfga/openfga)

本文档中的链接和项目描述基于 2026-07-21 前可访问的官方文档或公开仓库。生产选型时应固定具体 Tag/Commit，重新核验 API、许可证、维护状态、安全公告和兼容性。
