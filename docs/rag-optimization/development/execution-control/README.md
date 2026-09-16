# RAG + Wiki 完善开发执行控制

版本：2026-08-31
状态：`ACTIVE / EXTERNAL_CURRENT_STATE_RESOLUTION / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. 目的

本目录是当前 RAG + Wiki 完善工作的执行控制层，用于防止任务丢失、范围漂移、计划与事实混淆，
以及为了覆盖假设风险而进行过度防御性开发。

本目录不替代已有架构和成熟度规范。发生冲突时，按以下顺序解释：

1. Git objects、immutable receipts 与外部 authority 的可验证事实；
2. 工作区 durable current-state record：`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md`；
3. [`10_RAG_WIKI_MATURITY_PROGRAM.md`](../10_RAG_WIKI_MATURITY_PROGRAM.md) 的阶段依赖与硬门；
4. [`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`](../34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md) 的原子目标、准入和停止点；
5. 当前阶段专项规格、本目录的计划/决定/执行/验证记录；
6. 明确标为历史的报告与 artifact。

若第 2 项缺失、无效或与第 1 项矛盾，状态必须是
`CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`；不得从历史文档推测 current。

## 2. 文档职责

| 文档 | 唯一职责 | 何时更新 |
|---|---|---|
| [`MASTER_PLAN.md`](MASTER_PLAN.md) | 问题到阶段、原子目标、依赖和验收标准的完整映射 | 范围或顺序发生真实变化时 |
| [`TASK_STATE.md`](TASK_STATE.md) | current-state resolver、fail-closed 行为和通用恢复合同 | 解析合同或 Gate 边界真实变化时 |
| [`EXECUTION_LOG.md`](EXECUTION_LOG.md) | 已执行动作、实际改动、验证结果和残余风险 | 每个物质性动作或验证批次后 |
| [`DECISIONS.md`](DECISIONS.md) | 影响架构、范围、顺序或门槛的决定 | 出现非显然取舍或计划偏离时 |
| [`VERIFICATION.md`](VERIFICATION.md) | 验收矩阵、验证命令和证据状态 | 测试集合或验证结果变化时 |

计划不是完成事实；日志不是计划；测试通过也不自动等于阶段 `QUALIFIED`。

## 3. 每个任务开始前的强制读取顺序

开始任何新的原子目标前，执行者必须按顺序读取：

1. 本文件；
2. durable current-state record，并核对其 Git/ref/artifact 绑定；
3. `TASK_STATE.md` 的解析、missing-state 和恢复合同；
4. `MASTER_PLAN.md` 中该任务的依赖、边界和验收标准；
5. 上位 Runbook 与该阶段专项规格；
6. `DECISIONS.md` 中与该任务相关的有效决定；
7. `VERIFICATION.md` 中该任务的必测项；
8. `EXECUTION_LOG.md` 最后一个相关任务记录和最近一次全局记录。

读取后必须核对：工作树、冻结输入、前置 Gate、允许改动集合、停止条件是否与 `TASK_STATE.md` 一致。
如果不一致，先记录 `STATE_DRIFT`，修正文档真值，不继续写业务代码。

## 4. 单任务执行协议

每轮只允许一个原子目标处于 `IN_PROGRESS`。任务卡必须具备：

- `Outcome`：本任务唯一新增的可观察结果；
- `Frozen inputs`：revision、manifest、generation、配置和基线；
- `Allowed mutations`：允许修改的文件、表、索引或运行状态；
- `Negative boundary`：明确不做什么；
- `Verification`：最小回归、负向、故障和必要的全量验证；
- `Evidence`：日志、artifact、摘要和限制；
- `Exit`：可判断的完成条件；
- `Stop/Rollback`：停止信号与恢复点。

标准执行顺序：

1. 用最小复现或 characterization test 固定问题；
2. 只修改完成当前 Outcome 所需的最小职责面；
3. 先跑定向验证，再跑受影响面回归；
4. 记录失败，不删除失败样本，不放宽既有断言来制造绿色；
5. 更新执行日志、验证矩阵和任务状态；
6. 重新执行本节读取协议后，才可领取下一任务。

## 5. 防止方向漂移与过度防御性开发

以下规则对所有任务生效：

- 不创建新的平行 V3/V4 路径来绕过现有合同；优先收敛到冻结合同；
- 不为没有复现、威胁模型、验收要求或真实事故的假设风险增加 guard、重试、缓存或抽象层；
- 不进行“顺手式”大重构；仅在当前任务需要且有行为测试保护时提取职责；
- 不把页面、snippet、candidate 或派生摘要重新命名成 raw evidence；
- 不以降低阈值、删除负例、扩大排除项或改变 denominator 使 Gate 通过；
- 不把每条只读查看命令写成流程负担；只记录影响判断、改动、验证或任务状态的物质性动作；
- 若出现更优方案，先写入 `DECISIONS.md`，说明证据、影响和回退，再改变计划；
- 前置 Gate 未通过时，可做只读审计、测试设计和 fixture 计划，但不得越权写正式数据或声称后续阶段完成。

## 6. 状态词

沿用项目既有状态：

- `NOT_STARTED`
- `IN_PROGRESS`
- `ENGINEERING_PASS`
- `QUALITY_HOLD`
- `QUALIFIED`
- `FAILED`
- `BLOCKED_EXTERNAL`（仅在同一外部阻塞连续三个目标轮次无变化且没有其他可推进工作时使用）

禁止使用不带范围的 `COMPLETE`。

## 7. 当前恢复入口

任何中断或上下文压缩后，先解析 durable current-state record，再按 [`TASK_STATE.md`](TASK_STATE.md) 的
“Recovery checkpoint”恢复；不得仅凭对话记忆、旧 admission 版本或历史日志决定下一步。解析失败时停止
source mutation，但可以继续做不改变状态的诊断。
