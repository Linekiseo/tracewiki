# RAG + Wiki 当前决定记录

## DEC-001 — 复用既有成熟度 authority，不创建竞争路线图

- 日期：2026-08-29
- 状态：ACCEPTED
- 决定：本目录只做执行控制和本次发现的细化映射；阶段依赖继续以成熟度总纲和原子 Runbook 为准。
- 原因：项目已有 G0→G9、T0→T9 和专项执行规格；另建一套独立阶段编号会造成双重真值。
- 影响：新发现必须映射到既有原子目标；冲突时上位文档优先。
- 回退：若上位 authority 被 owner 正式换代，记录新 decision 并更新所有 current pointer。

## DEC-002 — 每次只保持一个 active atomic objective

- 日期：2026-08-29
- 状态：ACCEPTED
- 决定：`TASK_STATE.md` 只能有一个 active task；后续任务保持 `NOT_STARTED` 或 Gate 状态。
- 原因：减少上下文切换和完成语义混淆，确保每个变更都有唯一 Outcome、验证和恢复点。
- 影响：不得同时实现角色本体、raw gateway、query convergence 和性能重构。
- 回退：只有上位 Runbook 明确允许并行且每条线有独立状态文件/owner 时，才能拆分 active lane。

## DEC-003 — 正确性和 authority 优先于局部严重度排序

- 日期：2026-08-29
- 状态：ACCEPTED
- 决定：虽然 F-01 角色错配和 F-02 raw verify 都是 P0，但执行顺序仍为 G0→G1→G2→G3。
- 原因：直接改 Navigator/Organizer 字符串无法建立 raw claim authority，并可能在 canonical role registry 落地时再次返工。
- 影响：允许保留 F-01 复现和测试设计，不提前做 runtime mutation。
- 回退：仅当新证据证明局部修复是 G0/G1 的必要前置且不改变后续合同，才通过新 decision 调整。

## DEC-004 — 以证据约束防御性开发，而不是堆叠防御代码

- 日期：2026-08-29
- 状态：ACCEPTED
- 决定：新增 guard、重试、fallback、缓存、抽象或兼容层前，至少需要复现、冻结合同、威胁模型或验收项之一。
- 原因：项目已有多条并行查询/存储路径；无证据的保护层会扩大状态空间并削弱可验证性。
- 影响：每个实现先写 characterization/回归；只修当前 Outcome；不以“以后可能需要”为理由建框架。
- 回退：若真实事故或安全评审提出新威胁，记录证据并新增明确的负向测试与任务。

## DEC-005 — 不触碰用户现有脏工作树的版本历史

- 日期：2026-08-29
- 状态：ACCEPTED
- 决定：在 owner review 前不自动 stage、commit、push、清理、重置或覆盖现有改动。
- 原因：241 个 status entries 的所有权和审阅范围未完成确认；破坏性版本操作会损失用户工作或伪造可复现性。
- 影响：当前只新增本目录文档；所有后续 diff 必须限制到任务允许集合。
- 回退：repository owner 明确给出版本处置授权和精确范围后，按 T0.28.2–5 执行。

## DEC-006 — 增量编译、ANN 与大文件拆分延后到正确性合同稳定后

- 日期：2026-08-29
- 状态：ACCEPTED
- 决定：F-04/F-05/F-06 不与 G1/G2/G3 同时开发；先稳定 raw authority、generation、canonical plan 和质量 guard。
- 原因：性能优化和职责拆分需要明确的语义等价基线，否则可能把现有错误固化成更难诊断的索引或模块边界。
- 影响：当前只冻结验收标准；实现进入 G4/G7 对应目标。
- 回退：若容量问题阻断前置 Gate 的测试执行，可建立仅针对测试工具链的最小性能任务，不改变 runtime 语义。

## DEC-007 — V24 保留为历史证据，刷新新的不可覆盖 candidate

- 日期：2026-08-29
- 状态：ACCEPTED
- 证据：V24 native exact verify 失败；精确差异为新增 10 个 documentation paths，修改/删除 0、
  exclusion 变化 0、Git-state 变化 0，当前 HEAD 与 V24 base HEAD 相同。
- 决定：不覆盖 V24/packet V5；V25/V6 用作 intermediate capture，验证结果写回文档后由 V26/V7
  作为 documentation-complete current candidate；随后恢复 T0.28.2。
- 原因：owner 必须审阅当前完整 source denominator；继续使用 V24 会漏掉 G5–G8 规格和执行控制文档。
- 影响：该批次当时的 current pointer、Makefile 默认 artifact 路径和相关权威文档一致指向 V26/V7；
  后续由 DEC-008 换代；本决定不产生 owner approval。
- 回退：若新 candidate 构建失败，保留 V24/V5 与失败日志，修复 pointer/source 后使用新的版本号重试。

## DEC-008 — 以最小 lock-only 修复关闭 V26 供应链 finding，并换代 V27

- 日期：2026-08-29
- 状态：ACCEPTED
- 证据：npm 11.17.0 对 V26 lock 报告 1 个 high finding；两个隔离副本证明 nanoid 3.3.16→3.3.18
  只需 lock 的 version/resolved/integrity 三字段变化；修复后 audit 0、policy/pending/SBOM/191 tests/
  typecheck/build 全部通过；V26 admission delta 只有一个 changed path。
- 决定：保留 V26/V7，不放宽 audit、不覆盖用户全局 npm、不引入 package manifest 或运行时代码改动；
  以最小 lock-only patch 关闭 finding，并用 V27/PENDING packet V8 重新固定完整 source denominator。
- 原因：继续使用 V26 会把已知 vulnerable lock 交给 owner；扩大依赖升级或覆盖用户 npm 都超出当前
  finding 的最小职责面。
- 影响：V26 成为历史工程证据；Makefile、authority、任务状态和 owner review 全部使用 V27/V8；
  owner/remote/LIVE/G1 Entry 状态不提升。
- 回退：若 V27 exact/canonical/cleanroom 失败，保留全部失败证据和 V26/V7，修正确定性问题后使用新
  版本号；不把 lock 降回已知 finding 版本制造 source parity。

## CP-01 — Git/receipt 事实优先于过期 current prose

- 日期：2026-08-30
- 状态：ACCEPTED
- 证据：admitted execution-control prose 仍保存早期 current snapshot，而 Git refs、owner decisions、bundles
  和 durable ledger 已继续推进；旧 bytes 在多个候选中相同，证明问题是控制面漂移而非 main 工作树污染。
- 决定：发生冲突时按 Git objects/immutable receipts → durable current-state/ledger/log → task evidence →
  source stage rules → 明确历史文本解释；先 reconciliation，不按旧 task 写 source。
- 影响：历史运行继续保留，但不能因“看起来像 current”而重新激活。
- 回退：若 durable evidence 自相矛盾，则不是降级到旧 prose，而是进入 fail-closed unresolved 状态。

## CP-02 — Source 保存稳定 resolver，易变 current truth 外置

- 日期：2026-08-30
- 状态：ACCEPTED
- 决定：admitted docs 保存阶段依赖、resolver 与 missing-state 规则；candidate/ref/active/next 等易变真值保存在
  stable durable Markdown。记录缺失、无效或矛盾时使用
  `CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`，禁止扫描历史猜 latest。
- 原因：把每个新 candidate 写回 source current pointer 会形成递归 admission churn，并在后续留下新的漂移。
- 影响：fresh remote checkout 可执行显式 CI job，但不能自行选择下一开发任务；外部记录不能弱化 Gate。
- 回退：只有 owner 正式采纳另一种具备同等 fail-closed/可核验语义的 source-controlled control plane，才以新
  decision 换代。

## DEC-009 — T0.28.10D 只同步 15 份控制文档

- 日期：2026-08-31
- 状态：ACCEPTED
- 输入：owner-reviewed V11、T0.28.10C drift inventory、CP-01/CP-02 与精确 15-file edit matrix。
- 决定：用 documentation-only candidate 替换 operational current literals、追加历史/决定/执行记录；不修改
  workflow、tests、runtime、dependency、schema、database、release 或历史 V27 execution record。
- 验证：exact diff allowlist、link/static/history checks、targeted G0 tests、bundle restore、successor
  admission/packet；不因 prose-only 变化无差别重跑全仓。
- 影响：G0/G1/runtime/release 状态不提升；candidate 仍需新的 owner decision。
- 回退：scope 越界、历史 bytes 改变或 resolver 可回退到旧 snapshot 时，在 commit 前停止并移除隔离 candidate。
