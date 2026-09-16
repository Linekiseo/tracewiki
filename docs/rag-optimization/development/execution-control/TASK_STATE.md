# RAG + Wiki 当前任务状态解析合同

版本：2026-08-31
程序目标：`OBJ-RW-L5`
程序边界：`ACTIVE / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. 单一当前状态来源

本文件不再保存易过期的 candidate、packet、active task 或 next task 快照。当前执行真值必须从工作区
稳定控制路径读取：

`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md`

解析顺序固定为：

1. 读取 durable current-state record；
2. 核对其中 ref/commit/tree、artifact SHA、owner decision 与 Git 可达对象；
3. 核对本目录的阶段依赖、mutation boundary 与 stop 条件；
4. 再读取对应 plan、decision、execution、verification evidence；
5. 仅当全部一致时，领取 record 中唯一的 `active_local_task`。

durable record 不存在、格式错误、证据不可用或彼此矛盾时，唯一合法状态是：

```yaml
active_atomic_id: null
resolution: CURRENT_STATE_UNRESOLVED
program_state: QUALITY_HOLD
mutation: NO_SOURCE_MUTATION
```

不得扫描历史 admission、packet、execution log 或本文件的 Git 历史来猜测“最新任务”。远程 CI 可在没有
本地 durable record 的 checkout 中执行已明确指定的 build/test，但不得由此选择新的开发任务。

## 2. Source-controlled invariants

外部 current-state record 只能选择已被 source 计划允许的任务，不能削弱以下规则：

- 每次最多一个 atomic objective 为 `IN_PROGRESS`；
- G0 `QUALIFIED` 且 G1 Entry `PASS` 前，`T1.1.1` 及全部 G1 runtime/source mutation 保持 HOLD；
- G1→G2→G3→G4→G5→G6→G7→G8→G9 严格按 Gate 依赖推进；
- `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE` 只可由相应外部 authority 和 hard exits 改变；
- owner approval、protected remote、remote CI、UI review、independent Gate 与 release authority 不互相替代；
- page、snippet、candidate 或派生摘要不得冒充 raw evidence；正式数据库和数据物化必须有前置授权；
- 计划范围外的“顺手修复”、推测性 guard、重试、缓存、抽象和全仓验证不得自动扩张当前任务。

## 3. Generic active-task card

解析出的 active task 必须在其 durable plan/evidence 中完整给出：

```yaml
active_atomic_id: <one task id>
parent_gate: <G0..G9>
outcome: <one observable result>
frozen_inputs: <reviewed revision/artifacts/config>
allowed_mutations: <exact allowlist>
negative_boundary: <explicit exclusions>
verification: <proportional positive/negative/replay checks>
exit: <machine and authority conditions>
stop_rollback: <fail-closed recovery point>
```

任一字段缺失、allowlist 与 Git diff 不一致、输入不可重放或 authority 被本地结果冒充时，停止 source mutation。

## 4. Queue and Gate resolution

后续任务不在本文写死为 current。解析 durable record 后，仍必须用 `MASTER_PLAN.md` 和上位 atomic runbook
验证依赖：

1. G0 本地 reviewed revision、same-revision remote gates、UI/owner/independent receipts；
2. G0 `QUALIFIED` 与 G1 Entry E-01–E-10 `PASS`；
3. G1 raw authority；G2 grounded answer；G3 canonical query；
4. G4 materialization；G5–G6 quality；G7 capacity/reliability/maintainability；
5. G8 replay/shadow/canary/rollback；G9 reviewed default authority。

已完成或历史任务只能作为 evidence，不因 durable record 缺失而重新激活。

## 5. Recovery checkpoint

中断、交接或上下文压缩后：

1. 按 `README.md` 的强制读取顺序重新开始；
2. 解析并核对 durable current-state record；
3. 核对 main checkout 守恒、目标 worktree/ref、remote 与 staged 状态；
4. 读取 active task 的 plan、allowlist、最新 execution log、decisions 和 verification；
5. 若一致，继续记录中尚未完成的最早步骤；若不一致，进入
   `CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`；
6. active task 达到 Exit 并更新 durable record 前，不领取下一任务。

历史 V24–V27、V8 packet 和早期 owner-review 快照仍由各自不可变 execution/admission records 保存；本文件
不复制它们为 current fallback。
