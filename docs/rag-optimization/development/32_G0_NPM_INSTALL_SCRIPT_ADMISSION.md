# G0 前端 npm 安装脚本准入与 V18–V20 执行规格

版本：2026-08-06 V2  
工作包：`WP-G0-05K`  
状态：`LOCAL_ENGINEERING_PASS / REMOTE_ATTESTATION_PENDING`  
前置候选：V17  
边界：`FRONTEND SUPPLY CHAIN / NO RELEASE / DEFAULT_V1 / NO DATABASE`

## 1. Finding 与结论

V17 的 fresh `npm ci` 在 npm 11.17.0 上虽然 audit 为 0，但报告 `esbuild@0.25.12` 与
`fsevents@2.3.3` 含未进入 `allowScripts` 的安装期脚本。漏洞数据库为 0 不能证明安装期间不会执行
未审代码，因此原 supply-chain Gate 只覆盖 CVE/SBOM，未完整覆盖 lifecycle script authority。

本工作包把安装脚本从提示升级为 fail-closed 合同：

```text
exact npm runtime
  + strict-allow-scripts=true
  + exact package@version approval
  + package-lock registry URL/integrity binding
  + complete hasInstallScript set equality
  + pending list = 0
  + audit/SBOM evidence
```

npm 官方说明项目级 `allowScripts` 应固定依赖安装脚本权限，`strict-allow-scripts=true` 可把遗漏从
warning 升级为硬失败。npm 11.17.0 对 macOS optional `fsevents` 存在 pending 列表漏报的已知问题，
所以本实现不把 CLI pending 列表作为唯一真值，而是直接扫描 lock graph 中全部
`hasInstallScript=true` row。[npm install-script policy](https://docs.npmjs.com/cli/v11/commands/npm-install-scripts/)
[npm optional fsevents issue](https://github.com/npm/cli/issues/9562)

## 2. 冻结合同

### 2.1 工具链身份

| 项 | 冻结值 |
|---|---|
| package manager | `npm@11.17.0` |
| Node range | `>=22.9.0 <27` |
| lock format | `lockfileVersion=3`、`requires=true` |
| project policy | `audit=true`、`engine-strict=true`、`strict-allow-scripts=true` |
| downgrade behavior | npm 非 11.17.0 时 policy verifier 和 engine-strict 均失败 |

CI 在任何前端安装之前使用 `--ignore-scripts` 安装精确 npm 版本，再断言版本；随后由
`make frontend-ci` 先运行 policy verifier，再执行 strict `npm ci`。

### 2.2 当前允许的完整集合

| Package | 用途 | Registry identity | 决定 |
|---|---|---|---|
| `esbuild@0.25.12` | Vite/Vitest 编译器平台二进制校验/选择 | URL 固定；lock integrity `sha512-bbPB…ryJg==` | `ALLOW_EXACT_VERSION` |
| `fsevents@2.3.3` | Rollup/Vite 的 macOS optional 文件事件原生模块 | URL 固定；lock integrity `sha512-5xoD…ffQw==` | `ALLOW_EXACT_VERSION` |

不允许 name-only、semver range、未来版本继承或第三项安装脚本。任一 package version、registry URL、
integrity、`hasInstallScript` 集合或 approval 集合变化，都必须重新审阅并生成新候选。

### 2.3 验证器

`frontend/scripts/verify-install-policy.mjs` 使用 Node 内置模块完成离线校验：

1. 当前 npm 必须精确为 11.17.0；
2. `packageManager`、`engines.npm` 和 `.npmrc` 必须与冻结策略完全一致；
3. `allowScripts` 必须恰好包含两个 pinned approval；
4. `frontend/.npmrc` 必须作为 source 被 G0 manifest 哈希，不能因隐藏文件/敏感路径通则被排除；
5. lock graph 中全部 `hasInstallScript=true` 路径必须恰好等于两个 reviewed path；
6. 两项 version/resolved/integrity 必须精确匹配；
7. 若提供 npm pending artifact，其 `allowScripts` 必须为空；
8. 成功输出 `rag-frontend-install-policy-v1`、完整 reviewed rows 和内容摘要。

验证器不联网、不修改 package/lock、不自动批准依赖，也不从已安装 `node_modules` 自学习权威。

## 3. CI 与 evidence artifact

Admission job 的原子顺序为：

1. setup Node；
2. 以 `--ignore-scripts` 安装并断言 npm 11.17.0；
3. `make frontend-ci`：policy verify → strict locked install；
4. 保存 npm version、pending list、install-policy result；
5. 生成 audit JSON、CycloneDX SBOM；
6. 对 package、lock、`.npmrc` 和全部 evidence 文件生成 SHA256SUMS；
7. 即使后续失败也上传 run-id/attempt 绑定 artifact；
8. tests → typecheck → build → release-ready。

remote artifact 必须来自同一 reviewed revision。当前本地结果不能冒充 GitHub job 或 E-06 receipt。

## 4. 对抗性验证

前端策略测试覆盖：

1. exact npm/package/lock/pending 正向 PASS；
2. npm 版本漂移拒绝；
3. strict policy 关闭拒绝；
4. name-only approval 拒绝；
5. lock integrity 篡改拒绝；
6. 新增第三项 install script 拒绝；
7. pending list 非空拒绝。
8. G0 admission 显式纳入隐藏的 `frontend/.npmrc`，修改该文件会令 source exact 失败。

本地执行结果：

```text
fresh strict npm ci: PASS
unreviewed install-script warning: 0
pending install scripts: 0
frontend test files: 33/33
frontend tests: 191/191
typecheck: PASS
production build: PASS
npm audit: 0 info/low/moderate/high/critical
CycloneDX SBOM: PASS, npm tool version 11.17.0
G0 workflow contract: 3/3 PASS
backend Python 3.12.13: 2,118/2,118 PASS
backend Python 3.13.12: 2,118/2,118 PASS
known backend warning: 1 per interpreter (Starlette/FastAPI TestClient deprecation)
```

## 5. Gate 与下一步

```text
WP-G0-05K local install-script policy: ENGINEERING_PASS
same-revision remote install policy artifact: MISSING
owner-reviewed revision: MISSING
LIVE-02: NOT OBSERVED
G0: VERSION_ADMISSION_HOLD
G1 Entry: NOT SATISFIED
release: DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE
```

V18 已完成 source exact 与 bytes-only self-contained cleanroom；V19 不改变产品代码、测试或 lock，
只收口 post-run 文档；V20 再加入始终 PENDING 的 owner-review packet；V21 纳入 atomic objective graph；
V22 加入 G2 execution spec；V23 加入 G3 canonical planning execution spec；V24 加入 G4
corpus/materialization/backfill execution spec；V25/V26 加入 G5–G8 specs 与 execution control。2026-08-29
本地回放发现 V26 lock 的 nanoid high finding，以最小 lock-only 更新到 3.3.18 后，strict policy、pending=0、
audit 0、SBOM、191 tests、typecheck/build 均通过；因此 current candidate/packet 换代为 V27/V8。
Owner 仍须审阅 V27 source snapshot、取消跟踪
`web/index.html`，并在同一 clean revision 执行完整 remote admission。任何 npm/package/lock/policy
变化都使本证据过期。
