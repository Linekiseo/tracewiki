# G0 Owner Admission 与远程 CI Handoff

版本：2026-08-31 version-neutral handoff
状态：`LOCAL_REVIEWED_PROGRESS / PROTECTED_REMOTE_AND_RUN_HOLD`
准入输入：durable state 解析出的 exact admission artifact
审阅输入：同一 state 绑定的 immutable packet + independent owner decision receipt
独立复核：`reviews/24_G0_OWNER_ADMISSION_CI_HANDOFF_REVIEW_2026-08-06.md`

## 1. 目的

本文把 `WP-G0-05` 最后需要 owner/远程系统完成的动作，收敛成一条不可跳步的交接协议。它不把
本地 cleanroom 当作 owner revision，也不把“workflow 文件存在”当作远程 CI 已通过。

交接必须同时证明：

1. owner 审阅的是 durable state 解析出的精确 source snapshot 与 deterministic PENDING packet，而不是相似
   目录、目录级 staging、mutable branch tip 或后续漂移工作树；
2. 同一 revision 在 Python 3.12、3.13 上完成无排除的后端完整回归；
3. 两个后端 job 通过后，才执行 source-exact、self-contained cleanroom 和前端完整验证；
4. 构建生成物产生后，checkout 仍然 source-exact、Git clean、release blocker 为 0；
5. 所有 job 只持有 `contents: read`，checkout 不持久化凭据，不连接正式数据库。

## 2. 已关闭的 CI 定义缺口

旧 `backend-ci.yml` 只对一组路径触发双 Python 后端。文档、准入 artifact、前端、原生端或其他
source scope 的变更可以绕过 G0；workflow 也没有执行 cleanroom、前端 build 或最终
release-ready verify。

当前 `.github/workflows/backend-ci.yml` 固定为：

| 层 | 合同 |
|---|---|
| 触发 | 所有 pull request、`main` push、manual dispatch；禁止 path filter bypass |
| 后端矩阵 | Ubuntu 24.04；Python 3.12/3.13；`uv=0.11.14`；locked sync；`backend-full` |
| Admission 依赖 | `needs: verify`，双版本任一失败时不运行后续准入 |
| Admission 环境 | Python 3.13、Node 22、只读权限、无持久 checkout credential、隔离数据目录 |
| 重放顺序 | matrix 各自 exact → backend full → admission exact/cleanroom → exact npm/policy → strict locked install → audit/SBOM artifact → frontend tests/typecheck/build → release-ready |
| 失败语义 | source 漂移、历史缺失、high audit、前端失败、dirty checkout 或 blocker 非零均 fail closed |

该 workflow 不自动提交、不修改 release registry、不上传生产包，也不把 unsigned desktop engineering
artifact 当成 G0 发布证据。

## 3. 防退化合同

`tests/test_g0_ci_workflow.py` 不依赖 GitHub 在线状态，静态冻结以下条件：

- 触发器不得重新加入 `paths`；
- Python matrix 必须精确为 3.12/3.13，`fail-fast=false`；
- job 权限必须为只读，checkout `persist-credentials=false`；
- uv、Python、Node、lock 文件和安装命令必须明确；
- npm 必须固定为 11.17.0；`strict-allow-scripts=true`、exact `allowScripts`、lock graph/registry/integrity
  和 pending=0 必须在安装前 fail closed；
- admission 必须等待完整 backend matrix；
- exact、cleanroom、audit/SBOM、frontend tests/typecheck/build、release-ready 的顺序不得交换或缺失；
- supply-chain artifact 必须绑定 run id/attempt，失败仍上传，不能只保留绿色 badge。

这些测试只能证明 CI 定义没有明显漂移，不能证明 GitHub runner、网络依赖或远程执行成功。

V18 延续 V17 的 G1 Entry verifier/handoff，并新增 exact npm runtime、安装脚本白名单、lock
registry/integrity、pending=0、隐藏 `.npmrc` admission 和远程 supply artifact 合同；当前源码已在 Python
3.12.13 与 3.13.12 各完成 Entry+workflow 43/43、完整 backend 2,118/2,118；前端 33 files/191 tests、
typecheck/build、strict fresh install、audit 0 和 SBOM 均通过；后端共同仅有已登记的
Starlette/FastAPI TestClient 第三方弃用提示。该结果证明 workflow 新合同进入完整测试分母，但仍
不能替代远程 runner 结论。

## 4. Owner/remote 原子执行清单

以下 lineage 为历史语境，不是 current recovery pointer：V19 不改变上述产品/测试/lock，只收口 V18 运行后的文档时态。V20 新增 always-PENDING
owner-review packet。V21 再纳入全阶段 atomic objective graph；V22 加入 G2 execution spec；V23
加入 G3 canonical planning execution spec；V24 加入 G4 corpus/materialization/backfill execution spec；
V25/V26 再加入 G5–G8 specs、execution control 与 refresh record；V26 后续本地完整回放发现并以最小
lock-only 更新关闭 nanoid high finding，因 admitted lock bytes 改变而换代 V27/V8。该 packet 当时保存逐
path item、10 个 scope group、完整 exclusion denominator 和 G0-OWNER-01–07；后续对象必须从 resolved
admission 原生重建，不能自批、替换 manifest 或绕过 source drift。

1. 对 resolved admission artifact 执行 canonical/current-source verify，并确认 source snapshot 精确匹配；
2. 对 resolved packet 执行 native owner-review verify，按 path/scope/exclusion/action 审阅，不使用整目录
   盲目 stage；packet 保持 PENDING，外部 decision 另行签发；
3. 将准入文件、exact admission、history bundle 与批准 release evidence 绑定同一 reviewed revision；
4. 从版本跟踪中移除 `web/index.html`，保留 ignore 和可重建合同；
5. 确认 revision 中没有 conflict、未审敏感文件或额外 tracked runtime output；
6. 创建 pull request，让 `G0 source admission` 自动运行；
7. 要求 `Python 3.12`、`Python 3.13`、`Exact source, cleanroom, and frontend` 三个 job 全绿，且下载核验
   npm version/install-policy/pending、audit、SBOM 和 SHA256SUMS artifact；
8. 保存同一 revision SHA、workflow run/attempt、job conclusion、resolved source snapshot、install-policy
   digest 与 supply-chain artifact digest；
9. 从远程 clean checkout 再执行 `make g0-admission-release-verify`；
10. 生成新的 G0 package/Gate Review，不覆盖任何历史证据；
11. 由组织信任配置独立提供 G1 keyset digest，不从 packet/bundle/仓库自举；
12. 执行 `make g1-entry-draft` 产生无 receipt、固定 PENDING 的 draft；
13. 事实全部就绪后执行 `make g1-entry-signing-requests`，由组织外部 owner/CI/D1–D5/data/
    review/runtime 签发者按 request 产生 Ed25519 receipts；
14. 使用带外 trust digest 执行 `make g1-entry-assemble`；缺失、多余、旧时序或错 revision receipt
    必须失败；
15. 执行 `make g1-entry-verify`，保存 canonical verification artifact；只有真实输出
    `ENTRY_QUALIFIED` 才解锁 T1.1.1。完整协议见文档 31。

任何 admitted bytes 改动都必须生成新 admission run；Git state 从 modified/untracked 变为 clean 不会改变
source identity，但 path、role、scope、长度或内容摘要变化会被拒绝。

## 5. 当前停止条件

本地自动化已完成 workflow/合同、历史 cleanroom、双 Python/full backend、严格 frontend supply-chain、
owner-reviewed local ref/bundle 与普通恢复。resolved local revision 不能替代以下 owner/远程系统证据：

- owner-reviewed revision；
- `web/index.html` 取消跟踪后的真实 clean checkout；
- GitHub 三个 job 的远程结论与 run URL；
- same-revision supply-chain remote artifact；
- 真实浏览器 page identity、console、hash/navigation/back 验收。

在这些条件完成前，状态保持 `QUALITY_HOLD / DEFAULT_V1 / NO_RELEASE`，`WP-G1I-01` 不解锁。

## 6. Exact-source authority transport and current resolution

workflow 不在 candidate source 中硬编码“当前 admission digest”，也不得在同一次 CI 中临时 build manifest
再把它冒充预先 reviewed authority。合法 transport 固定为：

1. evidence-only Git object 保存 canonical admission bytes，workflow 只按 exact commit 取 regular blob；
2. protected environment 通过带外 authority value 提供 reviewed file SHA；
3. runner 在解析前核对 file SHA，再把相同 bytes 作为 same-run artifact 分发给 Python 3.12/3.13、
   cleanroom、frontend 与 release-ready jobs；
4. run/attempt、candidate/tree、workflow source、authority commit、file/content/source 和 artifact digest 全部进入
   remote receipt；mutable branch/tag/latest、同 job rebuild 与只校验内层 JSON digest 均禁止。

当前 reviewed/pending/admission/packet 角色从
`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md` 解析。record 缺失、无效或与 Git/receipts 矛盾时使用
`CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`，不回退到本文早期 revision snapshot。真正启动 remote
handoff 还需要：真实 remote URL、目标保护分支/PR 规则、push 授权、protected environment authority values
和可保存的 run/artifact/attestation；这些输入缺失时 local candidate 开发可按已批准计划继续，但不得声称
remote CI 或 G0 qualification。
