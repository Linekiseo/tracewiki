# G0 版本范围、Self-contained Cleanroom 与 Admission Manifest

版本：2026-08-31 version-neutral lineage
状态：`VERSION_SCOPE_REFINED / CURRENT_ARTIFACT_EXTERNALLY_RESOLVED / QUALITY_HOLD`
基线：`artifacts/rag-maturity/g0/baseline-20260806-v1/baseline.json`  
准入策略引擎：`src/evidence_rag/evaluation/maturity_g0_admission_v2.py`  
当前准入证据：由 stable durable current-state record 解析并校验，不由本文硬编码

## 1. 目的与硬边界

本清单把“当前工作树能运行”拆成四个不得混写的结论：

1. **源码范围已识别**：每个准入文件有相对路径、角色、scope、字节数、SHA-256 和捕获时
   Git 状态；
2. **纯准入 bytes 可材料化**：只复制 manifest 中的文件，不复制原仓库 `.git`、cache 或生成物；
3. **自包含 cleanroom 可重建**：材料化结果只使用随版本纳管、内容寻址的历史 bundle 建立 Git
   仓库，普通 clone、前端 build、Code Golden 与完整回归均可执行；
4. **正式版本已准入**：必须由 owner 审阅并形成受控 revision，再由 fresh clean checkout 和远程
   CI 证明。

当前工程候选满足 1–3，不满足 4。工具不执行正式工作区的 `git add`、commit、push、branch 或 worktree
操作，不把现有用户变更据为本轮独立提交，也不授权 G1 runtime 实施。

## 2. 历史 admission evidence 的保留与取代关系

### V1：范围政策不合格，保留为负证据

`admission-20260806-v1` 不删除、不覆盖，但不再作为默认准入：

- 收录 `web/app/**`、`web/index.html` 等可重建前端产物；
- 收录未进入产品资产清单的设计图片；
- 排除 evaluator 实际依赖的 immutable `evaluation.sqlite3` 锚点；
- 没有把“已跟踪生成产物”升级为发布阻断。

### V2：范围正确，但 cleanroom 仍依赖原 Git object

`admission-20260806-v2` 固定了 source/evaluation/generated/sensitive 边界，并证明 1,078 个文件可
隔离 build/test。但为了让 Code Golden V2 的固定提交在 synthetic Git 中可达，验证过程仍从原
仓库 fetch 对象。它证明了文件范围，却没有证明“只凭准入 bytes”可重建完整测试环境。

V2 artifact 和结果保持不可变；V3 不回写它，而是新增自包含历史 fixture 后生成新的 evidence run。

### V3：历史机制完整，但全量 cleanroom 暴露路径绑定

V3 沿用 `rag-maturity-g0-admission-v2` schema 和已冻结的范围策略，同时要求
`history_fixture_policy` 完整存在。它的 receipt、history/security、ordinary clone、Code Golden 和
release-ready 均通过，但随后在同一 materialized cleanroom 执行完整后端时得到
`2059 passed / 4 failed`。四项失败均指向 Experiment authority 将 `httpx`、`yaml` 等模块的
`.venv` 绝对路径纳入 binding digest。V3 保留为不可覆盖的失败见证，不再作为默认准入。

### V4：可移植 runtime authority evidence run

V4 保留 V3 的文件范围、bundle、固定 Code Golden V2 身份和安全分母，仅把 Experiment module
origin 从物理 checkout/install root 投影为稳定的逻辑来源；Windows 路径、普通 Python package、
扩展模块和 frozen module 均有回归。Foundation authority 显式升级到 v4，E-B0 current authority
显式升级到 v7，旧 released authority 不回写。旧 V2/V3 artifact 仍可 canonical verify；当前
V4 artifact 保留为完整 runtime qualification evidence，不因后续文档换代而覆盖。

### V5：当前文档完备化 evidence run

V5 不改 V4 的 runtime、tests、frontend、lock、历史 bundle 或 authority bytes；新增
`15_G1_BINDING_FOUNDATION_EXECUTION_SPEC.md` 与独立 review 22，并同步总纲、台账、目标卡和
文档索引。它把 G1I-01 从方向设计细化为 V2 并列表、canonical identity、M0–M8 migration、dry-run
artifact、BF-01–15 tests 和 rollback 合同。由于 admission source bytes 已变化，V4 继续作为完整
runtime qualification 证据，当时的 source exact/owner 审阅使用不可覆盖的 V5 artifact。

### V6：Portable Python runtime qualification

V6 修复 V5 在 Python 3.12 暴露的 CPython minor bytecode 身份漂移，并把“可持久、跨支持版本的
identity”与“同解释器内存篡改完整性检查”拆为两层。Codex、Experiment、release registry、CB6
与 Notebook AST 都完成显式换代或兼容修复；历史 released artifact 不回写。Python 3.12.13 与
3.13.12 对同一源码均达到 2,072/2,072，前端 179、typecheck、build 与 V6 bytes-only cleanroom
均通过。V6 在最终成熟化文档写入前生成，保留为不可覆盖的 runtime qualification evidence。

### V7：首次 documentation-complete owner-review candidate

V7 不改变 V6 runtime、测试、依赖锁或历史 bundle；它把 portable identity 执行记录、独立 Gate
Review、总纲、台账、目标卡、版本范围和文档索引纳入同一个 source snapshot。V7 当时是 exact
source、cleanroom 与 owner 审阅候选；V6 继续证明运行资格，不以覆盖旧 artifact 的方式
伪造文档已在当时存在。

### V8：documentation-final owner-review candidate

V8 不改变 V7 的文件分母、runtime、测试、锁文件或历史 bundle；仅消除当时历史/operational
语义歧义，统一总纲、台账、目标卡、review 和文档索引。V7 保留首次文档完备 cleanroom 证据；V8 当时是
source-exact owner-review candidate。

### V9：owner-handoff 与 clean-full 负证据

V9 在 V8 基础上补齐全链 G0 CI：移除 path-filter bypass，要求所有 PR/main push 先完成 Python
3.12/3.13 backend matrix，再执行 exact source、self-contained cleanroom、frontend tests/typecheck/build
和最终 release-ready verify；新增 3 项防退化合同、owner handoff 与独立 review。双 Python 当前
完整分母均为 2,075。V8 不覆盖，V9 是
当时的 source-exact owner-review candidate；远程 run 仍明确未观察。后续在 V9 clean checkout 的
locked install 后、frontend build 前执行 backend full，暴露 `web/index.html` 缺失：2,074 PASS / 1
FAIL。V9 因此同时保留为 clean-full 负证据。

### V10：clean-full 与 exclusion 语义负证据

V10 将 SPA 源入口合同从 generated `web/index.html` 纠正为 admitted `frontend/index.html`，不删除
任何行为断言；双 Python matrix 也在 backend full 前执行 exact verify。新增 clean checkout 执行记录
与 review，随后在无预生成 Web 产物的新 cleanroom 依次重放 backend、frontend 和构建后
release-ready。随后构建后 verifier 返回 exclusion exact=false、release-ready=true，暴露 portable
exclusion 语义未分层；V10 因此也保留为负证据。

### V11：portable-exclusion owner-review candidate

V11 保留 V10 clean-full 修复，将 exclusion audit 拆为 exact match、approved 和 unapproved addition
count；release-ready 固定为 Git clean、approved 和 blocker=0 的合取。新增 ignored sensitive
fail-closed 与 cache/缺失 exclusion 可移植测试，并在同一 checkout 串行执行 evaluation full。V11
是 portable-exclusion 语义的完整工程证据。

### V12：G1-readiness 文档协调中间候选

V12 不改变 V11 的 runtime/test/evaluation/frontend/native/plugin bytes，只增加 G1 contract→code
readiness audit/review，并更新规格、上位设计、目标台账和默认 admission pointer。V11 的双 Python、
clean full 与 frontend 运行结果继续作为相同产品源码的历史运行证据；V12 重新执行 source-exact、
bytes-only materialize、history/security/ordinary clone 和 portable release-ready，以证明新增文档也能
由对应 source snapshot 重建。其后交叉检查发现少量历史 review 仍将 V11 写作“当前候选”；V12 不
覆盖，保留该中间状态与 cleanroom PASS。

### V13：current-authority 收口历史候选

V13 只修正上述 current/historical candidate 语义并将默认 Makefile pointer 指向自身；产品源码、测试、
评测和 G1 设计实质均不变。V13 重新生成 source snapshot，并重新执行 exact、bytes-only materialize、
history/security/ordinary clone 与 portable release-ready。V13 保留为 G1 readiness current-authority
收口证据，后续由 V14 接续。

### V14：G1 Entry 与 frontend supply-chain 中间候选

V14 新增 Entry E-01–10/requirement trace 及其 review，移除 vulnerable third-party router，以 bounded
first-party Hash/Memory router 和 4 项合同测试保持现有业务 API；G0 remote admission 同步增加
audit/SBOM/SHA256SUMS 与失败 artifact。前端完整分母由 179 增至 183，fresh audit 为 0。V14 重新
执行 source-exact、bytes-only cleanroom、history/security/ordinary clone 与 portable release-ready；
其后复核发现 external target 拒绝尚无负向测试，V14 不覆盖并保留为中间证据。

### V15：route-target fail-closed 历史候选

V15 为 protocol URL 与 scheme-relative target 增加明确拒绝测试，前端分母增至 184；其他 router、
lock、后端、评测和发布边界不变。V15 重新执行 fresh locked install、audit、frontend full/type/build、
source-exact 与 self-contained cleanroom。V15 保留为路由边界关闭证据；真实浏览器 LIVE-02 与
remote run 当时仍待补。

### V16：G1 Entry 离线验证器历史候选

V16 新增 strict canonical/Ed25519/keyset/trust-anchor 的 Entry verifier、27-case adversarial matrix、
CLI/Make 入口、执行记录与独立 review。复核中进一步把 G0/generated/supply 四块聚合绑定到同一 CI
receipt，并要求 G0 manifest 通过其原生完整 verifier。当前源码 Python 3.12.13/3.13.12 backend
各 2,104/2,104，Entry+G0 workflow 定向各 30/30。V16 只提升准入可判定性；真实 keyset/receipt、
owner revision 和 remote run 仍不存在，G1 runtime/DB 未启动。

### V17：G1 Entry signing handoff 历史候选

V17 在不改变 Entry 权限模型的前提下，新增 PENDING draft、非签名事实门禁、确定性 15 项基础
signing request、外部 receipt 一一匹配和 APPROVED packet 安全 assembly。工具无 keygen/sign/fetch、
network、database 或 release 路径；成功 assembly 仍须由 V16 原 verifier 再验。新增 Make 四步入口及
命令契约测试后，Entry+handoff 为 40、与 G0 workflow 合计 43，Python 3.12.13/3.13.12 完整
backend 各 2,117。真实 proposal、外部 request/receipt、带外 trust anchor、owner revision 与 remote
run 仍未产生，G1 runtime/DB 未启动。

### V18：npm install-script admission 历史候选

V18 关闭 V17 fresh install 暴露的 lifecycle-script authority 缺口：npm 固定为 11.17.0，项目启用
`engine-strict` 与 `strict-allow-scripts`，仅允许 exact `esbuild@0.25.12`、`fsevents@2.3.3`，并把
version、registry URL、lock integrity 与完整 `hasInstallScript` 集合纳入离线验证。CI 会保存 npm
version、pending=0、policy result、audit、SBOM 和 SHA256SUMS。复核还发现隐藏 `frontend/.npmrc`
原先未进入 source scope；V18 为该项目级安全配置建立唯一显式 exception，其他 `.npmrc` 继续按
sensitive path 拒绝。当前 frontend 33 files/191 tests、typecheck/build、audit 0、SBOM 均通过；
Python 3.12.13/3.13.12 backend 各 2,118。真实 remote artifact、owner revision 与 LIVE-02 仍缺失。

### V19：post-run 文档收口历史候选

V19 不改变 V18 的产品代码、测试、lock、install policy 或历史 bundle；它在 V18 exact/cleanroom
完成后，只修正 post-run 文档时态并把 Makefile、总纲、台账、目标卡、owner handoff 与 Entry
packet 指向同一当前候选。V18 保留首次 install-script 工程证据，V19 作为 owner 审阅的
documentation-final source snapshot。V19 重新执行 source exact 与 bytes-only self-contained
cleanroom；真实 owner revision、remote artifact 和 LIVE-02 仍缺失。

### V20：Owner review packet 历史候选

V20 新增 `maturity_g0_owner_review_v1.py`、8-case 对抗测试、Make build/verify、runbook 与独立
review。工具先执行 G0 native manifest/source exact，再生成始终 PENDING 的逐 path、逐 scope、逐
exclusion、逐 external action 审阅队列；不能 approve、sign、改 Git、联网、访问数据库或发布。
V20 的产品 runtime、前端、lock、历史 bundle 和默认 V1 与 V19 相同；完整 backend 分母增至
Python 3.12.13/3.13.12 各 2,126。V20 重新执行 source exact、bytes-only self-contained cleanroom，
并从自身生成 canonical owner-review packet；真实 owner decision、remote run 与 LIVE-02 仍缺失。

### V21：全阶段原子目标重设历史候选

V21 不改变 V20 的产品 runtime、前端、lock、历史 bundle、owner-review verifier 或默认 V1；新增
`34_FULL_STAGE_ATOMIC_OBJECTIVE_GRAPH_AND_V21_RUNBOOK.md`，把 G0 当前外部动作和 G1–G9 的实现、
数据、质量、性能、安全、shadow/canary、发布任务拆成具有依赖、唯一结果、验证证据、owner 与
exit/stop 的 atomic objectives。文档同时记录 2026-08-06 LIVE-02 的真实限制：本地服务和 HTML
入口可验证，但 in-app browser 因 admin-enforced policy 不可验证而拒绝访问，不能冒充 UI PASS。

V21 包含 1,121 个 admitted files，并生成 1,121-item、始终 PENDING 的 owner-review packet V2；
产品测试分母保持 Python 3.12/3.13 backend 各 2,126、frontend 191。V21 只提升执行粒度和审阅输入，
不产生 owner decision、受保护 revision、remote CI、LIVE-02 或 G1 Entry authority。

### V22：G2 raw-verified grounded-answer 执行规格历史候选

V22 不改变 V21 的产品 runtime、tests、frontend、lock、history bundle、默认 V1 或 owner-review
verifier；新增 `35_G2_LIVE_WIKI_GROUNDED_ANSWER_EXECUTION_SPEC.md`。该规格用当前代码证明 V1
`raw_verify` 实际回读 Wiki candidate snippet，冻结 WikiSourceRefV2、raw read receipt、page/raw 双轴
obligation、EvidencePackV3、claim authority、六源 ingestion E2E、32-case matrix、发布 authority 与
rollback，且明确在 G1 QUALIFIED 前不实现 runtime。

V22 包含 1,122 个 admitted files，并生成 1,122-item、始终 PENDING 的 owner-review packet V3；
产品测试分母保持 Python 3.12/3.13 backend 各 2,126、frontend 191。V22 提升 G2 实施可判定性，
不产生 owner decision、受保护 revision、remote CI、LIVE-02、G1/G2 runtime 或发布 authority。

### V23：G3 canonical planner/Scope/as-of 执行规格历史候选

V23 不改变 V22 的产品 runtime、tests、frontend、lock、history bundle、默认 V1 或 owner-review
verifier；新增 `36_G3_CANONICAL_QUERY_PLAN_SCOPE_ASOF_EXECUTION_SPEC.md`。该规格以当前代码证明
public/ordinary、legacy platform、typed V2 与 Wiki 至少四套规划语义存在 intent、role、filter、as-of、
snapshot 和 fallback 漂移，冻结 IntentV3、EvidenceRoleV3、ScopeResolutionV3、TemporalTargetV3、
CanonicalQueryPlanV3、CapabilityRegistryV3、SnapshotManifestV3、三入口 adapter、preflight-only
exactly-once fallback、统一 generation policy、16-step 实施序列与 42-case Gate。

V23 包含 1,123 个 admitted files，并生成 1,123-item、始终 PENDING 的 owner-review packet V4；
产品测试分母保持 Python 3.12/3.13 backend 各 2,126、frontend 191。V23 提升 G3 实施可判定性，
不产生 owner decision、受保护 revision、remote CI、LIVE-02、G1/G2/G3 runtime、真实 as-of capability
或发布 authority。

### V24：G4 六源 corpus/materialization/backfill 执行规格历史候选

V24 不改变 V23 的产品 runtime、tests、frontend、lock、history bundle、默认 V1 或 owner-review
verifier；新增 `37_G4_SIX_SOURCE_CORPUS_MATERIALIZATION_BACKFILL_EXECUTION_SPEC.md`。该规格以当前代码
证明五类非 Code V2 store 是 process-local disposable indexes，Code 正式双写与 legacy active pointer、
Experiment/Notebook/Document `generation_id=NULL` derivation、Workspace mutation/audit 分离等边界尚未
形成六源正式物化控制面；冻结 MAT-01–24、`QualificationDataAuthorizationV1`、
`QualificationCorpusManifestV1`、`MaterializationRunV1`、`SourceGenerationManifestV3`、
`ActiveGenerationSetV1`、coverage conservation、checkpoint/fencing、删除传播、no-resurrection rollback、
22-step 实施序列与 GM-01–54 Gate。

V24 包含 1,124 个 admitted files，并生成 1,124-item、始终 PENDING 的 owner-review packet V5；
产品测试分母保持 Python 3.12/3.13 backend 各 2,126、frontend 191。V24 提升 G4 实施可判定性，
不产生 owner decision、受保护 revision、remote CI、LIVE-02、G1–G4 runtime、真实数据授权、正式库
materialization/backfill 或发布 authority。

### V25：G5–G8 与执行控制 intermediate capture

V24/current 原生校验在 2026-08-29 fail closed。精确 delta 仅为新增 10 份 documentation：G5–G8 四份
执行规格和六份 execution-control 文档；修改/删除/exclusion/Git-state 变化均为 0，HEAD 不变。新增 refresh
记录与 development README 索引后，V25 捕获 1,135 files 并生成 1,135-item PENDING packet V6；
admission exact、packet canonical、Ruff 和 15 项定向测试全部 PASS。V25 是中间证据，不作为 current owner input。

### V26：Documentation-complete historical candidate

V25 运行事实、current pointer、任务恢复点和 Make 默认路径写回后，V26 以相同 1,135-file denominator
重新捕获最终文档 bytes，packet V7 继续保持 PENDING。V26/V7 的 post-build exact/canonical 事实记录在
`artifacts/rag-maturity/g0/admission-20260829-v26/EXECUTION_RECORD.md`，该 receipt 位于 source admission
之外，避免修改 source 后再次造成自引用 drift。V26 未重放 cleanroom/full/远程 CI，不提升 G0 资格。

### V27：供应链 lock 修复后的 historical candidate

V26 后续本地完整回放在 Python 3.12.13/3.13.12 各通过 2,126 项后端测试；严格 npm 11.17.0 前端
回放发现 V26 lock 中 `nanoid@3.3.16` 的一个 high finding。隔离验证证明只需把 lock 中 nanoid 更新到
3.3.18，不改 package manifest 或其他依赖图；修复后 install policy、pending=0、audit 0、SBOM、
33 files/191 tests、typecheck 与 production build 全部通过。

由于 `frontend/package-lock.json` 是 admitted source，V26 exact 正确失效；V26/V7 保留为历史证据，
当时换代为 V27/PENDING packet V8。V27 纳入本记录、执行控制更新和最小 lock 修复，预计 1,136 个
admitted files；最终 file/scope/exclusion/digest 与 exact/canonical/cleanroom 结果只由 V27 source-admission
外的 post-build receipt 确认，不回写本文。

## 3. 当前准入合同

### 3.1 准入角色与分母

| 角色 | 内容 |
|---|---|
| `runtime_source` | 后端运行源码、schema、service、store、router、CLI |
| `test_source` | tests、受控 fixture、自包含 Git history bundle 与 cleanroom harness |
| `documentation` | README、成熟化/设计/运维文档；不含未登记 bitmap/SVG |
| `evaluation_definition` | case、schema、release ledger、provenance 等文本定义 |
| `evaluation_anchor` | evaluator 明确引用的不可变运行锚点 |
| `frontend_source` | 前端源码、配置、锁文件和测试 |
| `native_source` / `plugin_source` | 当前交付路径中的原生端与插件源码 |
| `legacy_web_source` | 仍被产品引用、但不是前端构建输出的旧 Web 源文件 |
| `delivery_source` | lock、容器、Makefile、CI workflow 等交付定义 |
| `release_evidence` | 当前文档/verifier 明确引用的 G0/Wiki 证据 |

V27 历史 capture 为 1,136 个文件：backend 270、tests 206、documentation 144、evaluation 192、
frontend 153、native 78、plugins 7、legacy web 19、delivery 9、G0 evidence 58。该计数只保存 lineage；
当前分母必须从 resolved artifact 重算，不替代其逐文件记录。

### 3.2 数据库边界

- 12 个 `evals/<source>/runs/<id>/evaluation.sqlite3` 作为批准的不可变评测锚点准入；
- 准入工具只读取其 bytes 计算长度与 SHA-256，绝不以 SQLite 连接打开；
- 正式应用数据库、任意未批准 `.db/.sqlite*`、`-wal`、`-shm` 均不准入；
- 评测 sidecar 明确排除，不能用来补全或修改锚点。

### 3.3 生成物、缓存、敏感内容与文档资产

明确排除：

- `web/app/**`、`web/index.html`、`web/build-contract.json`、`.frontend-source.sha256`；
- `frontend/vite.config.js/.d.ts`、`frontend/src-tauri/gen/**`；
- `.venv/`、`node_modules/`、`target/`、`.build/`、dist、coverage、pytest/ruff/cache、tsbuildinfo；
- SQLite sidecar、本地浏览器状态、`.playwright-mcp/`；
- `.env*`、私钥、证书、npm/pypi 凭据和其他敏感路径；
- `docs/design/**` 中没有进入正式资产清单的 bitmap/SVG。

cache 只保留聚合计数；有限且需人工审阅的 sidecar、生成物和文档资产保留逐路径 finding，避免
manifest 被本地依赖噪声淹没。精确分母以 durable state 解析出的 admission artifact 为权威。

### 3.4 发布阻断语义

“文件被排除”不等于“仓库允许跟踪它”。若 Git 已跟踪运行时生成物，准入器产生
`TRACKED_RUNTIME_OUTPUT_OUTSIDE_SOURCE_SET` 并令 `--require-release-ready` 失败。

受审 revision 必须确保 `web/index.html` 不在 source tree、`.gitignore` 保留且前端 build 可重建它；
`frontend/index.html` 继续作为 source。具体 reviewed/pending disposition 由 durable state 与 owner receipt 核验，
不能从历史工作树描述推断。

## 4. 自包含 Code Golden 历史设计

### 4.1 问题

Code Golden V2 materializer 使用固定提交
`bc3326edc761e3bdb42ed78726a21f314ab44974`，并通过普通 `git clone --no-local` 读取真实历史。
仅复制源文件不会复制 Git object；之前的 synthetic cleanroom 必须向原仓库 fetch，因而不是完全
自包含。

只打包三个稀疏文件所需的约 45 KiB object pack 也不合格：固定 tree 仍引用其他缺失 blob，普通
`upload-pack` 会把仓库判为损坏。为了保留被冻结的提交、tree、entity ID 和 Code Golden V2
package identity，V3/V4 收录完整可达历史，而不是伪造新提交或改写 V2 数据集。

### 4.2 内容寻址 bundle

- bundle：`tests/fixtures/g0_history/code-golden-v2-history.bundle`；
- 字节数：34,534,047；
- SHA-256：`sha256:ffbf5daf05139c2af922d66e4bed681ce0417a9cc4f820ebb04dacea38b1ff03`；
- 对象：13 commits、90 trees、328 blobs，共 431；
- 固定 tree：`2fe240ebdf1f3d04c252c32b505ca923394171da`；
- bundle 记录完整历史，普通 clone 必须能传递固定提交；运行与验证不需要网络。

生成器不要求当前 `main` 停在旧提交：它在 system temp 建立 bare 仓库，fetch 固定提交，创建专用
source ref，再生成 bundle。因此 owner 形成后续 revision 后仍可重新审计生成过程。

### 4.3 历史安全扫描

bundle 导入后必须重新扫描所有 328 个 blob，而不是因为 bundle 是二进制就跳过：

- 264 text blobs、64 binary blobs；
- 高置信命中 7 个 blob，聚合到 3 个路径；
- 三个路径均是 redaction/secret-detection 专项测试中的合成 token/private-key 字面量；
- `.env.example` 是无高置信 credential 的环境模板；
- allowlist 同时绑定 path 和类别，新增路径、类别、blob 分母或 object 分母都会 fail closed；
- 扫描只报告路径和类别，不输出疑似 secret 内容。

### 4.4 Cleanroom 状态机

`tests/fixtures/g0_history/cleanroom.py` 只接受带 `.rag-admission-source.json` receipt、尚无 `.git`
的材料化目录：

1. 校验 receipt 与 admission content/source digest；
2. 校验 bundle/manifest/generator/harness 四个文件均在准入集合且 bytes digest 相等；
3. 为 1,096 个准入文件建立固定时间、固定身份的 synthetic source revision；
4. 从本地 bundle 导入专用历史 ref；
5. 复算 431 个对象与完整安全分母；
6. 执行普通 `git clone --no-local --no-checkout`，证明固定提交可传递；
7. 执行 `verify --require-release-ready`，并要求最终 worktree clean。

它拒绝在正式工作区或已有 `.git` 的目录运行，避免把 cleanroom 操作误施加到用户仓库。

## 5. 已完成验证

### 5.1 可移植性修复

- Codex normalizer 摘要已移除 `co_filename`，相同源码移动 checkout 不再被误判篡改；
- Evaluation implementation snapshot 将物理仓库路径投影为 `<repository-root>`，不再绑定或泄露
  开发机/CI 位置；
- Experiment Foundation/E-B0 的 module binding origin 使用 `<module-root>` / `<module-file>`
  逻辑身份，不再绑定 checkout 或 `.venv` 根目录；authority 版本与 aggregate/parity hash 均显式
  换代，released v1 行保持不可变；
- 历史 CX1/Code Golden artifact 均未重写。

### 5.2 V4–V27 cleanroom、exact 与回归

1. materialize 当前 1,124 个准入文件到 system temp；
2. 不从原仓库注入 object，仅从准入 bundle 准备 cleanroom；
3. cleanroom：receipt、bundle digest、431 objects、安全分母、ordinary clone、release-ready 全部
   PASS；
4. cleanroom 中 Code Golden + Code baseline：40/40 PASS；
5. bundle/manifest tamper 在 Git 初始化前拒绝；
6. V3 完整 cleanroom 的 4 个路径误报保留为负证据；V5 Python 3.12 的 identity 漂移也保留为负
   证据；V9 generated HTML 失败与 V10 exclusion 语义失败继续保留；V11 历史双 Python 2,077、
   V16 2,104、V17 2,117 分母均不回写；
7. V18 源码 Ruff：PASS；Python 3.12.13 与 3.13.12 后端完整回归均为 2,118/2,118 PASS，
   每个解释器 1 个已记录的 Starlette/FastAPI TestClient 第三方弃用提示；
8. 前端最新证据：33 files / 191 tests、strict fresh install、pending=0、audit 0、SBOM、typecheck、
   production build PASS；
9. V20 owner-review：8/8 tests、1,120 review items、10 scope groups、7 PENDING actions、canonical
   build/verify PASS；Python 3.12.13/3.13.12 backend 各 2,126；
10. V21 atomic-objective reset：1,121 admitted files、1,121 review items；G0–G9 原子依赖、证据、owner、
    exit/stop 固定；owner/remote/LIVE-02 仍 PENDING；
11. V22 G2 spec：1,122 admitted files、1,122 review items；V2 raw refs/receipts/obligations/claim policy 与
    32-case Gate 固定；G1 前 runtime 仍锁定；
12. V23 G3 spec：1,123 admitted files、1,123 review items；canonical plan/Scope/TemporalTarget/snapshot/
    adapter/fallback/generation policy 与 42-case Gate 固定；G2 前 runtime 仍锁定；
13. V24 G4 spec：1,124 admitted files、1,124 review items；authorization/corpus/materialization run、六源
    generation set、coverage、resume/delete/rollback 与 54-case Gate 固定；G3 与真实数据授权前 runtime 锁定；
14. V25 intermediate：1,135 admitted files/items；exact/canonical PASS；Ruff + 定向 pytest 15 PASS；
15. V26 历史：1,135 admitted files/items；post-build exact/canonical 由 excluded receipt 固定；后续双
    Python 2,126、frontend 191 回放通过，但 nanoid high finding 使其不再是 current；
16. V27 historical：预计 1,136 admitted files/items；最小 lock-only 修复后 audit 0；最终 exact/canonical/
    cleanroom 由 excluded receipt 固定；
17. 端到端重放顺序固定为：materialize → cleanroom prepare → exact npm/policy → strict locked install → frontend
   tests/typecheck/build → backend full → release-ready verify。

## 6. 前端供应链关闭与剩余证据

`WP-G0-05J` 已删除 `react-router-dom/react-router`，以第一方 bounded Hash/Memory router 覆盖项目
实际 API；`WP-G0-05K` 进一步固定 npm 11.17.0、strict exact-version install-script approval、lock
registry/integrity 与 pending=0。fresh `npm audit` 为 0，33 files/191 tests、typecheck/build 与 CI
contract 通过。远程 admission 会生成 npm/policy/pending、audit JSON、CycloneDX SBOM 和
SHA256SUMS，任一 policy drift 或 high finding 阻断 Gate。完整记录见
`29_G0_FRONTEND_ROUTER_SUPPLY_CHAIN_CLOSURE.md`、`32_G0_NPM_INSTALL_SCRIPT_ADMISSION.md`。

本地 finding 已关闭，但 same-revision remote artifact 和真实浏览器 LIVE-02 尚未产生；因此不能
宣称 production security/UI acceptance pass，整体仍维持 `QUALITY_HOLD / NO_RELEASE`。

## 7. Historical V27 owner admission steps（superseded）

1. 运行 `make g0-admission-verify`，确认当时 1,136 个准入文件与 V27 artifact 完全一致；
2. 运行 `make g0-admission-cleanroom`，独立复核 self-contained history 与普通 clone；
3. 运行 `make g0-owner-review-verify`，按 packet V8 的 1,136 个 item、10 个 scope group、exclusion 与
   G0-OWNER-01–07 逐项审阅，禁止整目录盲目纳入；
4. 将准入文件、34.5 MB history bundle 和 58 个批准 release-evidence 文件显式纳入同一受控
   revision；
5. 取消 `web/index.html` 的版本跟踪并保留生成物 ignore；
6. 从新的 clean checkout 固定 npm 11.17.0，验证 install policy 后执行 locked Python/Node install；
7. 依次执行 cleanroom verify、前端 policy/pending/audit/SBOM/tests/typecheck/build，再执行
   `make backend-full`；
8. 执行 `make g0-admission-release-verify`，必须同时为 source exact、worktree clean、blocker 0；
9. 在 GitHub Python 3.12/3.13 workflow 上验证同一 revision 和 source digest；
10. 生成新的 G0 portable package 和独立 Gate Review，不覆盖 V1–V27 证据；
11. 按文档 31 依次生成 PENDING draft、fact-ready signing requests，收集真实外部 receipts 和带外
    trust digest，再 assemble packet；
12. 以原 G1 Entry offline verifier 复核 assembled packet；只有 `ENTRY_QUALIFIED` 才解锁 T1.1.1。

source identity 不含 capture-time `git_state`：owner 纳管后状态可以从 modified/untracked 变为
clean，但 path、role、scope、byte length 和 content SHA-256 必须完全一致。任一内容变化都必须
生成新的、不可覆盖 admission run。

## 8. Historical V27 determination

```text
V27 exact source: POST_BUILD_RECEIPT_AUTHORITY
V27 bytes-only materialize + self-contained cleanroom: POST_BUILD_RECEIPT_AUTHORITY
V26 exact source: HISTORICAL_ENGINEERING_PASS / INVALIDATED_BY_LOCK_CHANGE
V24 bytes-only materialize + self-contained cleanroom: HISTORICAL_ENGINEERING_PASS
ordinary clone + Code Golden/baseline replay: ENGINEERING_PASS
formal current-worktree release verify: BLOCKED (tracked web/index.html + dirty scope)
owner-reviewed revision: NOT PROVEN
fresh clean checkout: NOT PROVEN
Python 3.12/3.13 local full: ENGINEERING_PASS (2,126 each)
Python 3.12/3.13 remote CI: NOT OBSERVED
frontend supply-chain finding: CLOSED_LOCAL_ENGINEERING / REMOTE_ATTESTATION_PENDING
interactive browser acceptance: NOT OBSERVED
G0 / WP-G0-05: IN_PROGRESS / OWNER_ACTION_REQUIRED
release state: DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE
```

V27 artifact 内 canonical `content_sha256`、`source_snapshot_sha256`、history fixture policy 和逐 scope
digest 是最终权威；本文不复制 admission 自身 digest，避免文档自引用使 source snapshot 漂移。

## 9. Current admission resolution

当前 admission、owner packet、reviewed revision 和 pending candidate 只从
`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md` 读取，并逐项核对 Git object、receipt 和文件 SHA。该路径
稳定，但内容允许在 source admission 外随已验证 transition 更新，避免每次 candidate 都递归修改本文。

若 record 缺失、格式错误、对象不可达或绑定矛盾：

```text
CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION
```

不得回退到 §2、§5、§7 或 §8 中任一历史版本号。resolved artifact 仍必须满足本文 §3 的 source/evaluation/
generated/sensitive/database 合同，并保持 12 张 owner-excluded design PNG 不进入 source、generated
`web/index.html` 不跟踪、source `frontend/index.html` 存在。protected remote、remote CI、independent Gate、
G0/G1 与 release 状态必须由各自 evidence 给出，local bundle/replay 不替代。
