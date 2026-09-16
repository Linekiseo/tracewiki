# RAG + Wiki 当前完善总计划

版本：2026-08-31
计划状态：`ACTIVE / GATE_ORDER_PRESERVED / RUNTIME_IMPLEMENTATION_HOLD`

## 0. Current-state resolution

本计划保存稳定问题、依赖和验收标准；易变的 reviewed/pending/active/next truth 只从
`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md` 解析并核对 Git/receipts。该记录缺失、错误或矛盾时，
使用 `CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`，不从本计划的历史版本号猜任务。

## 1. 最终 Outcome

在保留 `DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE` 安全边界的同时，按 G0→G9 依赖推进：

1. 六源 raw evidence 可按 project、ACL、版本、generation 和 digest 回读；
2. Wiki 只负责组织、导航和派生表达，不冒充 raw authority；
3. 普通、Global V2、Wiki 查询共享 canonical plan、scope、as-of、snapshot 和 fallback 语义；
4. 增量编译和持久检索索引在保持结果正确性的前提下支持 M/L 规模；
5. 关键服务职责可维护，所有质量、容量、安全和发布结论可重放；
6. 最终由受保护的外部 authority 决定默认 V2，不由本地代码或文档自授予。

## 2. 当前问题到权威目标的映射

| ID | 已确认问题 | 主要证据 | 权威目标 | 解决完成标准 |
|---|---|---|---|---|
| F-01 | Live Organizer 与 Navigator 使用不同 evidence role 词表，所需角色可能永远无法满足 | Organizer 输出 Wiki 组织角色；Navigator 要求 implementation/decision/validation/current_version/metric 等证据角色；live navigation 已复现缺 `implementation` 并耗尽预算 | `T2.2.1`、`T2.4.1`，最终由 `T3.1.1`、`T3.5.4` 收敛 | 单一版本化 role registry；Organizer→Compiler→Navigator 投影显式；required roles E2E 可满足或给出准确 partial/refusal |
| F-02 | 当前 Wiki evidence gateway 回读 candidate envelope，不是真实六源原始对象 | Live candidate 统一标成 `derived_fact`；gateway 从 WikiStore 读取 candidate envelope | `T1.1.1–T1.10.2`、`T2.1.1–T2.8.1` | 读取正式 source/raw store；错误 project/ACL/version/generation/digest/tombstone 全部拒绝；snippet 不能满足 claim authority |
| F-03 | 普通 Query、Global V2 和 Wiki Query 并存且路由语义分叉 | Global V2 默认 V1；API 仅在显式 header 时进 Wiki；各入口持有独立 planner/fallback 逻辑 | `T3.1.1–T3.9.1` | 同一规范请求生成同一 canonical plan digest；adapter 不丢字段、不下游重规划；fallback 恰好一次 |
| F-04 | `compile_wiki_incremental_v1` 先执行全量编译，再计算复用，名为增量但成本仍是全量 | incremental 入口直接调用 full compile | 在 G4 generation 语义稳定后细化到 `T7.2.2` / compiler 性能子任务 | 无变化页面不重新解析/嵌入/写入；输出与同输入全量编译等价；删除不复活；有 changed/reused/removed 计数与基准 |
| F-05 | Wiki search 候选上限 2,000；dense 在线重算最多 512 页 embedding，没有持久 ANN | search candidate clamp 与在线 embedding 循环 | `T7.1.1`、`T7.2.1–2`、`T7.8.1–9.1` | generation/model 绑定的持久索引；M/L 数据集下 recall guard 和 P50/P95/P99 过门；查询不重嵌入整个候选集 |
| F-06 | query/service、multisource runtime、Wiki store 体积过大且职责交织 | 关键文件约 1.7k–2.2k 行 | 与 G2/G3/G7 行为合同完成同步分拆；不设提前大重构 | 行为、公共合同和 artifact 兼容；按 orchestration/contract/persistence/evaluation 分责；无平行新架构 |
| F-07 | main 工作树仍包含大量 owner-owned 改动；本地 reviewed revision 已建立，但受保护 remote、same-revision CI 与独立 Gate 仍缺 | immutable owner decision、local reviewed ref/bundle 和普通恢复；remote=0，G0/G1 未解锁 | `T0.28.1R–T0.30.1` | reviewed bytes 经受保护 revision、same-revision remote gates、UI 与独立 Gate 可重放；main 工作集不被候选流程覆盖 |

## 3. 依赖顺序与阶段计划

### P0 — 执行控制与事实基线

目标：建立本目录的计划、任务、日志、决策和验证控制；不改 RAG/Wiki 业务代码。

步骤：

1. 读取成熟度总纲、原子 Runbook、执行台账和相关专项规格；
2. 将本次代码审计发现映射到既有原子目标；
3. 固定当前工作树、测试和 Gate 状态；
4. 建立 durable current-state resolver、唯一任务恢复点和强制读取协议；
5. 验证 Markdown 结构、内部链接和状态一致性。

退出：本目录五类职责无冲突；source 只定义 resolver，durable record 最多选择一个 active task；解析失败
时 fail closed；业务代码 diff 为 0。

### P1 — G0 版本范围与可复现准入

依赖：P0。

严格顺序：

1. `T0.28.1R` source drift 时刷新不可覆盖 admission 与 PENDING packet；
2. `T0.28.2` owner 逐 path/scope 审阅；
3. `T0.28.3` 逐项处置 exclusions；
4. `T0.28.4` 处理可重建 generated HTML 的跟踪边界；
5. `T0.28.5` 形成 owner-reviewed protected revision；
6. `T0.28.6–8` clean replay、双 Python和严格前端供应链；
7. `T0.28.9` 真实浏览器 LIVE-02；
8. `T0.28.10–T0.30.1` 外部 receipts、独立 G0 decision、G1 Entry verify。

退出：G0 `QUALIFIED` 且 E-01–E-10 PASS。此前不启动 G1 runtime mutation。

### P2 — G1 六源 Raw Evidence Authority

依赖：P1 全部退出条件。

实施批次：

1. `T1.1.1–6`：并列表、digest、唯一键、内外模型、resolve/invalidate/tombstone、M0–M8 migration；
2. `T1.2.1–3`：shared secure reader、selector union、typed failure 和隐私安全审计；
3. `T1.3–T1.8`：依次实现 Code、Codex、Experiment、Notebook、Document、Workspace gateway 与 backfill；
4. `T1.9.1–2`：WikiSourceRefV2 迁移、generation rebuild 与 rollback；
5. `T1.10.1–2`：六源正负故障包和独立 Gate review。

每个来源只在 shared reader 合同稳定后进入；不得并行创造六套 digest/ACL/error 语义。

退出：六源均有真实 positive；raw read-back 100%；V1 ref 只能 navigation-only；leakage=0。

### P3 — G2 Live Wiki Grounded Answer 正确性

依赖：G1 `QUALIFIED`。

实施批次：

1. `T2.1.1–2`：状态机与 reviewer authority；
2. `T2.2.1`：Organizer 保留 raw/derived/status/binding，并解决 F-01 的角色投影；
3. `T2.3.1–2`：Compiler/Store 写入并校验 V2 ref 与 generation membership；
4. `T2.4.1`：Navigator 区分 page-found、role-covered 与 raw-verified；
5. `T2.5.1–2`：claim authority、counter evidence、partial/refusal；
6. `T2.6.1–T2.8.1`：六源 12-case、跨源/冲突/as-of/ACL/generation E2E 与独立 Gate。

F-01 的局部完成标准：

- 角色只有一个 canonical registry；
- Wiki 组织角色与 evidence role 的转换是显式、可版本化、可测试的投影，不靠字符串巧合；
- required role 未覆盖时停止原因为准确的 `missing_role`/预算/来源失败，不把 page hit 算成证据满足；
- live E2E 至少覆盖 implementation、decision、validation、current_version 和 metric 类 obligation。

退出：每个 answer claim 都有 raw citation membership；失败 raw read 只能 partial/refusal。

### P4 — G3 Canonical Query Contract 收敛

依赖：G2 `QUALIFIED`。

实施批次：

1. `T3.1.1–3`：Intent/EvidenceRole/Scope/Temporal contracts；
2. `T3.2.1–T3.4.2`：Capability、Snapshot、CanonicalQueryPlan 与 immutable receipt；
3. `T3.5.1–4`：ordinary、legacy/global、typed V2、Wiki adapter；
4. `T3.6.1–T3.7.1`：typed outcome、preflight-only exactly-once fallback、corrective policy、统一 generation；
5. `T3.8.1–T3.9.1`：入口 parity replay 和 portable Gate。

退出：相同输入/authority/snapshot 的 plan digest 一致；unsupported as-of 明确拒绝；一次查询固定快照。

### P5 — G4–G6 真实数据与质量资格

依赖：依次 G3、G4、G5 前置 Gate。

步骤：

1. 按 G4 建立授权语料、不可变 source generation、ActiveGenerationSet CAS、backfill、删除传播和 rollback；
2. 按 G5 分来源冻结 train/calibration/test，六源独立过门，联合得分不掩盖单源失败；
3. 按 G6 用真实联合样本评估 planner、fusion、Wiki navigation/raw read 和 claim-level answer；
4. 只有在 generation 与正确性合同稳定后，才把 F-04 的增量编译纳入性能实现。

增量编译验收：

- no-op build 不重复处理页面；单页变更只触及依赖闭包；删除传播不复活旧页面；
- incremental 与 full build 的 canonical generation content 等价；
- 中断可恢复，publish 仍为原子切换；
- artifact 记录 total/changed/reused/removed、耗时、峰值内存和等价性摘要。

### P6 — G7 扩展性、可靠性和可维护性

依赖：G6 `QUALIFIED`。

步骤：

1. 冻结 S/M/L corpus、硬件、并发、cache 与 embedding model profile；
2. 建立 generation/model/ACL partition 绑定的持久 sparse+dense index；
3. 用 M/L 基准替换“2,000/512 固定截断代表规模”的假设；
4. 验证并发 publish、rollback、reader consistency、故障和恢复；
5. 在 characterization tests 保护下拆分超大服务职责；
6. 完成 trace、dashboard、alerts、runbook 和 portable packages。

搜索验收：

- 查询阶段不重新计算整个页面集合 embedding；
- 索引版本与 Wiki generation、embedding model、ACL partition 可核验；
- P50/P95/P99、峰值内存、index build/update time、recall/role coverage 同时报告；
- 性能优化不得降低 frozen quality guard，index corruption 必须退到明确 partial/refusal 或受控 sparse path。

重构验收：

- 仅围绕已冻结边界提取模块，不改变公共请求/响应、reason code 和 artifact schema；
- 每次提取只有一个职责，先 characterization、后移动、再完整受影响回归；
- 不以文件行数作为唯一完成条件，不创建第二套路由或存储真值。

### P7 — G8–G9 发布资格与持续运营

依赖：G7 `QUALIFIED`。

步骤：offline paired replay → plan shadow → retrieval/EvidencePack shadow → answer shadow → internal canary →
分段 canary → 请求级与数据级 rollback 演练 → reviewed registries → 可逆默认 V2 → 观察窗口与持续监控。

退出：G0–G8 新鲜证据齐全，外部 authority 授权，V1 fallback 未提前删除，自动降级真实演练通过。

## 4. 每个实现任务的最小测试策略

1. 先新增或确认一个能稳定失败的回归/characterization test；
2. 实现最小修复；
3. 运行当前模块定向测试；
4. 运行直接调用链测试；
5. 只有跨合同、schema、公共 API 或发布路径变化才运行对应更大 Gate；
6. 阶段退出时再执行冻结的完整 Gate，不在每个小改动后无差别运行全仓全部测试。

这既防止回归，也避免把防御性验证成本扩散到每个局部改动。

## 5. 计划变更规则

只有以下情况可改变阶段顺序或验收门：

- 新证据证明依赖关系错误；
- 安全/数据完整性问题要求提前停止；
- 当前方案无法满足已冻结 Outcome；
- 外部 authority 明确改变范围或发布目标。

变更前必须新增 decision record；已有失败 evidence 不覆盖；`TASK_STATE.md` 同步新恢复点。
