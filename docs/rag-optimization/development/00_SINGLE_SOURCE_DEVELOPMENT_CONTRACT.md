# 单源 RAG 开发优化方案统一契约

状态：生效  
版本：single-source-development-contract-v1  
日期：2026-07-27

## 1. 目的

`sources/*` 回答“目标 RAG 应该是什么”，`development/*` 回答“如何在当前仓库中按
Issue/PR 安全实现”。每个单源必须严格按顺序完成开发方案；前一个来源未形成可评审方案时，
不开始下一个来源。

计划顺序：

```text
Code
→ Codex
→ Experiment
→ Notebook
→ Document
→ Workspace
→ Multi-source implementation
→ Global hardening
```

## 2. 每份开发方案的必备内容

### 2.1 当前实现映射

必须基于真实工作树逐项列出：

- runtime wiring；
- API/router；
- request/response model；
- ingest/parser/service/store；
- schema/table/index；
- retrieval/fusion/graph/context；
- evaluation；
- tests/fixtures；
- 当前正式库数据；
- 与其他来源的耦合点。

不能只复制目标架构。

### 2.2 变更边界

明确：

- 本源本轮负责什么；
- 哪些是公共契约；
- 哪些留到多源层；
- 哪些属于其他来源；
- 本轮不做什么；
- 是否引入新依赖、外部进程或模型。

### 2.3 Issue/PR 颗粒度

每个开发项必须能独立成为 Issue 和尽量可独立合并的 PR，至少包含：

- ID；
- 目标；
- 依赖；
- 修改/新增文件；
- Schema/API/数据契约；
- 实现步骤；
- 测试；
- Evaluation；
- 可观测性；
- Feature Flag；
- 回滚；
- 验收标准；
- 面试证据更新。

### 2.4 依赖图

必须给出任务 DAG，区分：

- blocking dependency；
- 可并行准备但不可越过的 release gate；
- 外部依赖；
- optional/P1；
- multi-source deferred。

### 2.5 Schema Migration

每个迁移说明：

- additive/destructive；
- DDL owner；
- backfill；
- active generation 兼容；
- dual read/write；
- rollback；
- cleanup 时机；
- 数据量/锁风险；
- integrity checks。

默认规则：

- P0 只做 additive migration；
- 不原地改写 stable entity ID；
- 不删除旧 Search View；
- 新索引先 shadow publish；
- cleanup 晚于稳定观察期。

### 2.6 API 兼容

必须列出：

- 保持不变的 V1 endpoint/field；
- 新增的 opt-in field；
- response compatibility；
- client fallback；
- deprecation；
- schema/contract tests。

### 2.7 Evaluation-first

开发顺序固定为：

```text
Golden schema
→ fixtures
→ baseline run
→ implementation
→ treatment run
→ slice/error analysis
→ release decision
```

没有 baseline，不进入效果型优化；没有 treatment run，不宣称完成。

### 2.8 发布

至少支持：

- off；
- shadow；
- canary；
- on；
- request/project override；
- last-known-good generation；
- request-level fallback；
- rollback rehearsal。

## 3. PR 大小原则

推荐：

- 单个 PR 只改变一个主要变量；
- Schema 与 Store 可同 PR；
- Parser IR 与 Retrieval Unit builder 可拆分；
- embedding benchmark 与模型上线拆分；
- Graph builder 与 Graph retrieval 拆分；
- Context 与 answer integration 拆分；
- Evaluation result 不与不相关重构混合。

不推荐：

- 一个 PR 同时换 chunk、embedding、reranker 和 graph；
- 在无 Feature Flag 情况下替换生产默认；
- 为开发方便修改 stable URI；
- 用全局搜索结果掩盖单源回归。

## 4. 状态定义

每个开发项标记：

- `NOT_STARTED`；
- `DESIGN_READY`；
- `IMPLEMENTING`；
- `SHADOW`；
- `CANARY`；
- `RELEASED`；
- `REJECTED`；
- `SUPERSEDED`。

开发方案本身只代表 `DESIGN_READY`，不能写成 `RELEASED`。

## 5. 验收证据

每项至少保存：

```text
Issue/PR
Code commit
Schema version
Builder/index/model/reranker/policy version
Golden dataset version
Baseline Evaluation Run
Treatment Evaluation Run
Slice report
Latency/storage/cost
Regression/failure
Feature Flag
Rollback evidence
Interview document update
```

## 6. 面试同步

对应 `interview/*` 每个实施阶段同步：

- 当前状态从 TARGET 移到 CURRENT_IMPLEMENTED 的条件；
- 实际代码入口；
- Evaluation Run；
- 真实指标；
- 失败实验；
- 性能/成本；
- 个人负责边界；
- 简历 bullet。

指标尚未产生时保留 `TBD`，不能填目标值。

## 7. 单源计划完成门

一份开发方案只有满足以下条件才算完成：

1. 映射到当前真实代码；
2. 有 Issue/PR 级任务；
3. 任务依赖明确；
4. Schema/API/兼容/回滚明确；
5. Golden/baseline/treatment 明确；
6. 安全与 ACL 明确；
7. 单源 Release Gate 明确；
8. 面试证据同步点明确；
9. 后续来源尚未越级设计；
10. 文档链接与格式校验通过。

