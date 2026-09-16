# RAG + Wiki 完善验证矩阵

版本：2026-08-31
原则：验证结果绑定任务与输入；定向绿色不升级为阶段资格。

## 1. 当前控制层验证 VC-00

| 检查 | 预期 | 当前状态 |
|---|---|---|
| 六份控制文档存在 | README、计划、状态、日志、决策、验证齐全 | PASS |
| authority 链接可解析 | 所有相对 Markdown 链接目标存在 | PASS |
| 单一 current resolver | source 不硬编码 active task；durable record 最多选择一个 | PASS |
| missing-state 行为 | record 缺失/无效/矛盾时 `CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION` | PASS |
| 计划/事实分离 | MASTER_PLAN 不声称实现完成；LOG 只记录已执行动作 | PASS |
| 业务代码零改动 | 本轮新增范围仅 execution-control 文档 | PASS |
| current 状态诚实 | DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE；不自授 owner authority | PASS |

本轮验证命令：

```bash
git diff --check -- docs/rag-optimization/development/execution-control
git status --short -- docs/rag-optimization/development/execution-control
```

另以只读脚本检查本目录 Markdown 相对链接目标是否存在。验证结果追加到 `EXECUTION_LOG.md`，并更新上表。

## 2. 当前工程基线（非资格证据）

| 范围 | 最近结果 | 能证明 | 不能证明 |
|---|---:|---|---|
| Wiki/query/multisource/EvidencePack/E2E 定向后端 | 129 passed | 当前定向工程行为未见回归 | clean checkout、双 Python、正式源、完整 Gate |
| Wiki Workbench/TrustedQueryPanel/queryContract 定向前端 | 33 passed | 目标 UI/契约测试绿色 | LIVE-02 浏览器交互、供应链/全前端资格 |
| Wiki quality fixture | 120/120 hard gates | fixture 内部一致性 | 真实六源质量；artifact 自身为 QUALITY_HOLD |
| Wiki capacity S | 约 362 pages / 950 refs | S 层运行证据 | M/L 容量和延迟外推 |

任何新代码改动后必须重新运行与该调用链对应的定向集合；阶段退出采用上位专项规格的完整 Gate。

## 3. F-01 角色本体验证

必测：

1. canonical evidence role registry 的枚举、版本与未知值行为；
2. Live Organizer 输出到 canonical role 的显式投影；
3. Compiler 持久化后角色不漂移；
4. Navigator required-role coverage 不把 page hit 当 role/raw support；
5. local_detail、historical/change、numeric/metric、decision/validation/current-version 场景；
6. budget exhausted、missing role、source failure、raw failure 的停止原因彼此可区分；
7. ordinary/Wiki adapter role parity。

通过：同一 obligation 在 compile 前后语义一致；required role 可满足或准确 partial/refusal；无字符串别名静默猜测。

## 4. F-02 Raw Evidence 验证

每源最低矩阵：

- positive exact read；
- wrong project、ACL、version、generation、digest；
- stale、tombstone、missing raw object；
- selector 越界/歧义；
- candidate envelope/snippet 冒充 raw；
- migration V1 navigation-only；
- rollback 和失败副作用为 0。

E2E：`source → binding → organize → compile → publish → navigate → raw read → EvidencePack → answer/refusal`。

通过：raw read-back 100%；unsupported claim=0；wrong-version/ACL leakage=0；失败 raw read 不生成肯定 claim。

## 5. F-03 Canonical Query 验证

必测：

- ordinary、legacy/global、typed V2、Wiki 四入口相同规范请求的 plan digest parity；
- include 与 scope source_types 冲突；
- required roles 透传；
- as-of unsupported/expired/future/interval；
- source timeout、error、no-match、unauthorized 分离；
- snapshot 固定和 mid-query generation swap；
- preflight fallback exactly once；执行开始后 fallback=0；
- pack/citation/generation policy parity。

通过：planner capability violation=0；相同 authority/snapshot 同 plan digest；无下游 replan。

## 6. F-04 增量编译验证

数据集：no-op、单页新增、单页修改、共享依赖变化、删除、rename、ACL 变化、generation 变化、故障恢复。

对每例同时执行 full 与 incremental，并比较：

- canonical page/ref/membership digest；
- changed/reused/removed denominator；
- raw binding 和 tombstone 行为；
- publish 原子性与 rollback；
- wall time、CPU、峰值内存、embedding/build 数量。

通过：语义等价；no-op 不全量重算；删除不复活；收益在冻结 M/L profile 可重复。

## 7. F-05 搜索扩展性验证

必测：

- 持久 dense/sparse index 的 generation/model/ACL binding；
- index build、incremental update、load、corruption、rollback；
- S/M/L 下 P50/P95/P99、峰值内存、recall@k、role coverage、cost；
- cold/warm cache、并发读、publish swap；
- 与当前 exact/sparse/dense/rerank 质量基线对比。

通过：查询不全库重嵌入；M/L quality guard 不下降；corruption/版本错配不产生错误肯定答案。

## 8. F-06 模块拆分验证

每次拆分必须：

1. 先锁定公共 API、reason code、artifact schema 和关键 side effect 的 characterization tests；
2. 一次只移动一个职责；
3. 定向、调用链、序列化兼容和受影响回归全绿；
4. 不新增第二个 planner/store/runtime truth；
5. 记录 import cycle、依赖方向和 rollback diff。

通过标准是职责边界清晰且行为兼容，不是达到任意行数阈值。

## 9. 阶段资格边界

- `ENGINEERING_PASS`：本地受控输入的实现/测试通过；
- `QUALITY_HOLD`：仍缺 owner、真实数据、远程 CI、UI、质量或观察证据；
- `QUALIFIED`：只能由对应专项规格的 hard exit 和外部 authority 共同产生。

禁止用本文件的勾选替代 G0–G9 stage review artifact。

## 10. Historical T0.28.1R Candidate Refresh 验证

| 检查 | 当前证据 | 状态 |
|---|---|---|
| V24 native exact verify | `current source set does not match admission manifest` | EXPECTED_FAIL / RECORDED |
| packet V5 canonical verify against current source | `admission manifest verification failed` | EXPECTED_FAIL / RECORDED |
| drift denominator | old 1,124；current 1,134；added 10；modified/removed 0 | PASS |
| drift classification | 10 个 documentation paths；scope delta documentation +10 | PASS |
| exclusion parity | old/current counts 相同；added/removed exclusion 0 | PASS |
| HEAD parity | current HEAD = V24 base HEAD | PASS |
| V25 admission exact verify | 1,135 files；source exact；exclusion match | PASS |
| V25 PENDING packet canonical verify | 1,135 items；10 groups；83 listed；PENDING | PASS |
| owner-review/admission 定向测试 | Ruff PASS；pytest 15 PASS | PASS |
| 当时 current pointer 一致性 | Makefile/authority 当时指向 V26/V7 | HISTORICAL_PASS |
| V26 exact/canonical post-build | 见 `admission-20260829-v26/EXECUTION_RECORD.md` | RECEIPT_BOUND |

该批次当时由 V26 excluded receipt 关闭；后续 lock finding 使 V26 成为历史证据，current 结论由下一节
V27 receipt 接续。T0.28.2 始终仍需真实 owner authority。

## 11. Historical T0.28.1R V26 Local Replay、Supply Fix 与 V27 Refresh

| 检查 | 当前证据 | 状态 |
|---|---|---|
| Python 3.12.13 Ruff/full | 2,126 passed；449.17s | PASS |
| Python 3.13.12 Ruff/full | 2,126 passed；408.68s | PASS |
| npm runtime | task-local exact 11.17.0；外层 10.9.8 未覆盖 | PASS |
| install-script policy | pending 0；reviewed 2；strict allow scripts | PASS |
| V26 supply audit | nanoid 3.3.16，1 high | EXPECTED_FAIL / RECORDED |
| lock-only 修复范围 | package-lock nanoid version/resolved/integrity；package.json 不变 | PASS |
| 修复后 supply artifact | audit 0；CycloneDX 1.5/265；SHA-256 8/8 | PASS |
| frontend full | 33 files/191 tests；typecheck/build PASS | PASS |
| V26/current source delta | 1,135→1,135；added/removed 0；changed lock 1 | PASS |
| historical pointer | 当时 Makefile/authority 指向 V27/V8 | HISTORICAL_PASS |
| V27 exact/canonical/cleanroom | 见 `admission-20260829-v27/EXECUTION_RECORD.md` | RECEIPT_BOUND |

该 V27/V8 批次当时只关闭 source capture 与本地可重放性，不关闭 owner review、受保护 revision、远程 CI、
LIVE-02 或 G1 Entry。它是历史验证，不提供 current recovery pointer。

## 12. Current-state resolver 与 T0.28.10D 验证

| 检查 | 预期 | 状态 |
|---|---|---|
| resolver path | README、TASK_STATE、master program、atomic runbook 使用同一 stable path | PASS |
| source active literal | operational blocks 不把历史 candidate/task 写成 current | PASS |
| missing durable state | HOLD + no source mutation；不扫描历史猜 latest | PASS |
| stage dependency | G0 `QUALIFIED` + G1 Entry `PASS` 才允许 T1.1.1 | PASS |
| scope | 仅冻结的 15 个 Markdown；runtime/workflow/test/dependency 0 | PASS |
| history | V27 execution record byte-exact | PASS / `b5bc857f...` |
| docs links/static scan | local links resolve；stale operational assertion=0 | PASS |
| targeted tests | locked Python 3.13 admission V2 + owner review | PASS / 15 |
| portable candidate | complete bundle ordinary restore + successor admission/packet verify | PENDING |

本节状态必须由当前 candidate 的 execution/verification evidence 收口；不能用本文中的 `PASS` 自授
owner、remote、G0、G1 或 release authority。
