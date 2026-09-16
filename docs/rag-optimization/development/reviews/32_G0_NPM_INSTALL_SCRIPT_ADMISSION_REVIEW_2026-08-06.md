# G0 npm 安装脚本准入对抗性复核

日期：2026-08-06  
复核类型：`FIRST-PARTY ADVERSARIAL SUPPLY-CHAIN REVIEW`  
复核对象：`package.json`、`.npmrc`、lock graph、install-policy verifier/tests、CI  
结论：`LOCAL_ENGINEERING_PASS / REMOTE_ATTESTATION_PENDING`  
边界：`NO OWNER REVISION / NO REMOTE RUN / NO RELEASE`

## 1. 结论

V17 的 CVE audit=0 不能覆盖安装期生命周期脚本。本变更把 npm runtime、script approval、registry
identity、integrity 和完整 install-script 集合同时冻结，并以 strict install 实跑证明遗漏会阻断而非
只提示。

本 review 不批准生产：真实 remote artifact、owner revision 和 LIVE-02 仍缺失。

## 2. Findings

### F-01：audit=0 被误当作安装期代码已审阅

处置：把 `allowScripts`、lock `hasInstallScript` 集合与 strict policy 纳入独立 Gate。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-02：name-only approval 会自动授权未来版本

处置：只接受 `esbuild@0.25.12`、`fsevents@2.3.3` 两个 exact key；version/URL/integrity 同时绑定。

判定：`FIXED_AND_NEGATIVE_TESTED`。

### F-03：pending CLI 漏列 macOS optional dependency

处置：不依赖 pending 输出枚举全量；直接扫描 lock graph 全部 `hasInstallScript=true` row。pending
artifact 只作为第二信号并要求为空。

判定：`FIXED_WITH_UPSTREAM_LIMITATION_RECORDED`。

### F-04：CI Node 自带 npm 漂移可绕过 strict policy

处置：安装前以 `--ignore-scripts` 固定 npm 11.17.0，随后断言版本；package engine 与 verifier 双重
拒绝 downgrade/drift。

判定：`FIXED_AND_CONTRACT_TESTED`。

### F-05：remote artifact 仍可能与审阅 revision 分离

处置：保留 G1 Entry E-06 same-revision CI aggregate receipt；artifact 名绑定 run id/attempt，内容增加
policy、pending、npm version、audit、SBOM 与 SHA256SUMS。

判定：`LOCAL_FIXED / REMOTE_PROOF_MISSING`。

### F-06：隐藏 `.npmrc` 未进入 admission source snapshot

处置：仅对项目级 `frontend/.npmrc` 建立显式 source exception；其他 `.npmrc` 仍按敏感路径拒绝。
新增 G0 回归断言其 scope/role，任何策略字节变化都会触发 source mismatch。

判定：`FIXED_AND_REGRESSION_TESTED`。

## 3. Evidence

| 检查 | 结果 |
|---|---|
| exact install-policy positive | PASS |
| drift/tamper/pending negative cases | 6/6 PASS |
| fresh strict install | PASS；未审脚本 0 |
| npm pending list | 0 |
| frontend | 33 files / 191 tests PASS |
| typecheck/build | PASS / PASS |
| audit | 0 vulnerabilities |
| CycloneDX SBOM | PASS |
| G0 workflow contract | 3/3 PASS |
| hidden `.npmrc` admission contract | 1/1 PASS |
| backend Python 3.12.13 | 2,118/2,118 PASS；1 known third-party warning |
| backend Python 3.13.12 | 2,118/2,118 PASS；1 known third-party warning |
| same-revision remote artifact | MISSING |

## 4. Gate 决定

```text
WP-G0-05K: LOCAL_ENGINEERING_PASS
V18 candidate: ELIGIBLE_FOR_LOCAL_ADMISSION_BUILD
G0: VERSION_ADMISSION_HOLD
formal database: NOT ACCESSED
default engine: V1
release: QUALITY_HOLD / NO_RELEASE
```

双 Python backend 与 V18 exact/cleanroom 已完成；V19 收口 post-run 文档；V20 加入 PENDING
owner-review packet 并重新执行同一准入链。之后只能由 owner/remote 系统关闭 revision、remote
supply-chain 与 LIVE-02 条件。
