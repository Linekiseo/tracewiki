# G0 前端路由供应链风险关闭与可复核准入记录

版本：2026-08-06 V2  
工作包：`WP-G0-05J`  
状态：`LOCAL_ENGINEERING_PASS / SUPPLY_CHAIN_FINDING_CLOSED / REMOTE_RUN_PENDING`  
运行边界：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE / FORMAL_DB_NOT_ACCESSED`

## 1. 目的与结论

本记录关闭 G0 风险 `R-017` 的仓库内工程部分。原依赖链把 client-only TraceWiki 前端绑定到
`react-router-dom@7.18.2`；2026-08-06 的新鲜审计仍报告 2 个 high。registry 未提供 advisory 指定的
首个修复版本 `8.3.0`，而隔离降级到 `7.11.0` 会重新引入更多历史高危项，因此不能用降级制造绿色
报告。

处置结果：移除 `react-router-dom` 和传递的 `react-router`，以项目内、边界冻结的 hash/memory
router 兼容层覆盖实际使用 API；新增四项路由合同测试；把 `npm audit --audit-level=high`、CycloneDX
SBOM 和三份 SHA-256 清单写入 G0 远程 admission job。

当前本地结果：

```text
npm audit: 0 total / 0 high / 0 critical
frontend: 32 files / 184 tests PASS
typecheck: PASS
production build: PASS
G0 workflow contract: 3 PASS
HTTP built-product smoke: 200 / TraceWiki title / hashed app entry present
interactive browser: NOT OBSERVED (browser security policy unavailable)
```

这关闭供应链 finding，不关闭整个 G0：owner-reviewed revision、tracked generated output、远程双 Python
与 admission run 仍缺失，故 `QUALITY_HOLD / NO_RELEASE` 不变。

补充边界：本文关闭的是 router advisory 与第一方路由合同。后续 `WP-G0-05K` 发现并关闭本地 npm
安装期脚本 authority 缺口；最新聚合为 33 files/191 tests、strict fresh install、audit 0。安装脚本
准入、npm pin、lock registry/integrity 和 pending=0 的当前权威是
`32_G0_NPM_INSTALL_SCRIPT_ADMISSION.md`，本文的 184-test 数字保留为 router 工作包历史分母。

## 2. 变更边界

### 2.1 本地 router 只支持已使用能力

实现文件：`frontend/src/lib/router.tsx`。

| 能力 | 支持合同 | 明确不支持/不隐式扩展 |
|---|---|---|
| history | `HashRouter`、`MemoryRouter`、push/replace/delta | browser/data router、server router |
| matching | absolute path、`:param`、`*`、pathless parent、index | relative route、optional/splat scoring、loader/action |
| rendering | `Routes`、`Route`、`Outlet`、`Navigate` | lazy route module、errorElement、defer |
| navigation | `Link`、`NavLink`、`useNavigate` | external URL、protocol URL、`//host` |
| read hooks | `useLocation`、`useParams`、`useSearchParams` | block/prompt、fetcher、navigation state |
| query update | value/updater、push/replace、hash preservation | implicit schema coercion |

`parsePath` 只接受以单个 `/` 开头的站内绝对路径，拒绝 protocol 和 scheme-relative target；Link 只拦截
未修饰的主键点击，保留新窗口、修饰键和调用方 `preventDefault` 的浏览器行为。HashRouter 只注册一组
`hashchange/popstate` 监听，并在卸载时清理；协议 URL 和 `//host` 有独立负向测试。

### 2.2 兼容接入

- Vite 和 TypeScript 把现有 `react-router-dom` import 映射到本地模块，避免一次性改写业务文件；
- `package.json/package-lock.json` 删除第三方 router 依赖；
- `vendor-react` chunk 不再声明该第三方包；
- 生产构建把本地 router 作为第一方应用源码处理；
- 没有改后端、数据库、API、RAG/Wiki runtime、默认引擎或发布 registry。

该 alias 是迁移兼容面，不代表支持完整 React Router API。新增调用必须先扩展本文合同和测试，不能
假设第三方文档中的其他 API 可用。

## 3. 验证矩阵

| ID | 验证 | 结果 | 证明/限制 |
|---|---|---|---|
| SC-01 | locked manifest/lock 不含 router package | PASS | package 与 lock 搜索无 dependency；alias 只指向本地文件 |
| SC-02 | `npm audit --json` | PASS | 0 vulnerabilities |
| SC-03 | CycloneDX SBOM 可生成 | PASS | npm 11.17.0 输出成功；远程 run 保存 artifact 与 SHA256SUMS |
| RT-01 | nested route + decoded params | PASS | route contract test |
| RT-02 | search updater + replace history | PASS | back 返回 replace 前一 entry，而非产生额外 entry |
| RT-03 | Link/NavLink | PASS | href、active、aria-current、站内跳转 |
| RT-04 | hash URL/history | PASS | `#/...?...` 初始化、href 和点击后三者一致 |
| RT-05 | index redirect | PASS | replace 后显示 project center |
| RT-06 | external target rejection | PASS | protocol 与 scheme-relative target 均 fail closed |
| REG-01 | 原有前端全量回归 | PASS | 32 files / 184 tests；原 179 + 新 5 |
| REG-02 | TypeScript strict check | PASS | app + Vite config |
| REG-03 | production build | PASS | 1,925 modules transformed；clean-build verify PASS |
| CI-01 | G0 workflow contract | PASS | 3 tests；审计/SBOM/always-upload/order 均受断言 |
| LIVE-01 | isolated backend serves built UI | PASS | `/` 200，title 与 hashed JS/CSS entry 存在 |
| LIVE-02 | browser page identity/console/click/hash | NOT OBSERVED | 应用内浏览器的 admin policy check 连续两次不可验证；未绕过 |

`LIVE-02` 不影响 SC-01–CI-01 的真实性，但在远程/人工页面验收完成前，不允许把本记录称为完整 UI
acceptance。下一次可用浏览器验收必须至少检查：页面非空、无错误 overlay、console error=0、从
`#/projects` 点击项目/全局导航后 URL hash 与页面标题一致、back/forward 可恢复。

## 4. 远程准入证据合同

`.github/workflows/backend-ci.yml` 的 admission job 在 locked install、exact verify 和 cleanroom 后执行：

1. `npm audit --audit-level=high --json`，保留退出码；
2. `npm sbom --sbom-format cyclonedx --json`；
3. 对 lock、audit、SBOM 生成 `SHA256SUMS`；
4. 即使审计失败也上传 `rag-frontend-supply-chain-<run_id>-<run_attempt>`；
5. high/critical 导致 admission job 失败，不能继续形成 release-ready 证据。

E-06 的远程 PASS 必须绑定同一 reviewed revision、run attempt 和 source snapshot；本地输出或本文中的
数字不能替代远程 artifact。

## 5. 威胁模型与回滚

本地实现减少了未使用的 server/RSC/data-router 攻击面，但也把路由正确性变成第一方维护责任。主要
风险和控制为：

| 风险 | 控制 | 失败动作 |
|---|---|---|
| route precedence 漂移 | App 固定顺序 + 全量页面测试 | 阻断 merge/admission |
| query/history 丢失 | updater/replace/hash tests | 回滚本地 router change |
| 外部 URL 注入 | 单 `/` absolute in-app contract | 同步拒绝，不导航 |
| browser back/forward 差异 | popstate/hashchange + LIVE-02 | 无页面证据不得发布 |
| 新业务调用未支持 API | 本地导出类型 + typecheck | 先更新合同/测试再实现 |
| SBOM/audit 随 lock 漂移 | 每个远程 admission 重跑并保存 artifact | high/critical 自动 HOLD |

回滚不是降级到 `7.11.0`。允许的回滚候选只有：回到上一个受审且没有未接受 high/critical 的前端
revision，或在上游安全版本真实可安装、审计与全回归通过后恢复受审第三方 router。任何恢复都必须
删除 alias、更新 lock/SBOM、重跑 184+ 回归和 LIVE-02。

## 6. Historical Gate impact and sequence

```text
R-017: CLOSED_LOCAL_ENGINEERING / REMOTE_ATTESTATION_PENDING
E-06: LOCAL_PASS / SAME-REVISION_REMOTE_ARTIFACT_PENDING
G0: QUALITY_HOLD
G1 Entry: NOT SATISFIED
default engine: V1
release: NO_RELEASE
formal database accessed: false
```

下一顺序仍是：使用包含 router/install-script policy、全阶段 atomic graph、G2/G3 execution specs 与
PENDING owner-review packet V8 的 V27 immutable G0 admission；owner 逐项审阅后形成受审 revision并取消跟踪生成
HTML；同 revision 远程双 Python + exact/cleanroom/supply-chain/frontend/release-ready 全链通过；补做
LIVE-02；随后 E-01–10、D1–D5、数据授权和独立复核全部 PASS，才解锁 G1 T1.1.1 C1–C10。

2026-08-29 本地重放又发现 V26 lock 中的 `nanoid@3.3.16` high finding；仅更新 lock 到 3.3.18 后，
npm 11.17.0 audit 重新为 0，33 files/191 tests、typecheck/build、SBOM 与安装脚本策略通过。该后续结果
由文档 43 和排除于 admission 的 replay receipt 固定，不改写本工作包的 router 历史分母。

## 7. Current-sequence supersession

§6 保存该 router/supply-chain 工作包完成时的历史序列，不再是 current recovery pointer。当前 reviewed
revision、pending candidate、admission/packet 与 active task 从
`artifacts/rag-maturity/control/CURRENT_TASK_STATE.md` 解析并核对 Git/receipts；source 不固定新的版本号。
record 缺失、无效或矛盾时使用
`CURRENT_STATE_UNRESOLVED / QUALITY_HOLD / NO_SOURCE_MUTATION`，不得按早期 revision snapshot 重新开始 owner review。

供应链 Gate 的稳定顺序仍是：resolved reviewed bytes → protected same-revision remote run → exact admission
authority transport → strict npm/policy/pending/audit/SBOM/tests/typecheck/build → release-ready → external UI review →
independent G0 decision。local audit/build/browser evidence 只能按其真实 scope 复用；remote/保护/独立 authority
缺失时 E-06、G0 与 G1 Entry 不升级。T1.1.1 仍要求 G0 `QUALIFIED` + G1 Entry `PASS`。
