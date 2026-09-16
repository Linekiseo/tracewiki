# G0 前端路由供应链关闭对抗性复核

日期：2026-08-06  
复核类型：`FIRST-PARTY ADVERSARIAL SUPPLY-CHAIN AND ROUTE-CONTRACT REVIEW`  
复核对象：`29_G0_FRONTEND_ROUTER_SUPPLY_CHAIN_CLOSURE.md`  
结论：`LOCAL_ENGINEERING_PASS / REMOTE_AND_BROWSER_EVIDENCE_PENDING`  
边界：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE / FORMAL_DB_NOT_ACCESSED`

## 1. 复核结论

依赖删除是真实关闭而不是 advisory 隐藏：manifest/lock 不再包含 `react-router(-dom)`，fresh npm audit
为 0；应用 import 由明确的第一方 alias 解析；原有前端 179 项与新增 5 项路由合同共 184 项通过，
typecheck/build 通过。G0 远程 Gate 也新增 high-level audit、CycloneDX SBOM、digest 与失败仍上传的
artifact 合同。

本 review 不把整个 G0 标为 QUALIFIED：改动尚未进入 owner-reviewed revision，没有 remote run，
tracked generated HTML 仍是 release blocker；应用内浏览器又因安全策略不可验证而无法加载本地地址，
所以 console/click/hash/back 的 LIVE-02 仍待补。

## 2. 对抗性 Findings

### F-01：把 client-only mitigation 写成“漏洞已修复”

原风险：仅禁用 RSC/SSR 仍保留 vulnerable package，audit 继续为 high。

处置：完全删除包与传递依赖；用 lock 搜索和 fresh audit 证明 finding set 为空。client-only 不再作为
waiver，只作为本地实现的范围边界。

判定：`FIXED_LOCAL / REMOTE_REPLAY_PENDING`。

### F-02：降级制造 audit 绿色

隔离审计表明 `7.11.0` 聚合出多项旧 XSS/RCE/DoS/CSRF/open-redirect 风险，不能作为安全回滚。

处置：禁止降级路线；回滚只能指向无未接受 high/critical 的受审 revision，或未来安全上游版本。

判定：`UNSAFE_ALTERNATIVE_REJECTED`。

### F-03：本地兼容层悄然承诺完整 React Router

处置：冻结仅项目已使用的 API 与 absolute route 语义；类型检查阻止未导出调用；protocol 与
scheme-relative target 有显式负向测试；新增功能必须先扩展合同和测试。

判定：`BOUNDARY_EXPLICIT`。

### F-04：全量测试通过但 history/hash 语义未锁

处置：新增 nested params、search replace/back、Link/NavLink、hash URL、index redirect 与 external
target rejection 五项合同；原有
大量 MemoryRouter 页面测试继续回归真实业务调用面。

判定：`CONTRACT_TESTED`。

### F-05：一次性本地 audit 被冒充远程准入

处置：remote admission 生成 audit/SBOM/SHA256SUMS，并以 run id/attempt 命名上传；high 退出码阻断
Gate，失败 artifact 仍保留。

判定：`CI_CONTRACT_PASS / REMOTE_RUN_PENDING`。

### F-06：HTTP 200 被冒充交互页面验收

处置：HTTP smoke 只证明 build serving；浏览器 policy 失败单列 LIVE-02，不使用独立自动化绕过安全
控制，也不把单元测试改名为浏览器 evidence。

判定：`LIMITATION_RECORDED / LIVE_ACCEPTANCE_PENDING`。

## 3. 证据复核

| 证据 | 结果 |
|---|---|
| manifest/lock router dependency absent | PASS |
| npm 11.17.0 audit | 0 vulnerabilities |
| CycloneDX generation | PASS |
| frontend full | 32 files / 184 PASS |
| frontend typecheck/build | PASS / PASS |
| G0 workflow contract | 3 PASS |
| local built UI HTTP | 200 / correct title and hashed assets |
| interactive browser | NOT OBSERVED |
| remote admission | MISSING |
| owner-reviewed revision | MISSING |

## 4. Gate 决定

```text
WP-G0-05J: LOCAL_ENGINEERING_PASS
R-017: CLOSED_LOCAL_ENGINEERING / REMOTE_ATTESTATION_PENDING
E-06: LOCAL_PASS / REMOTE_ARTIFACT_PENDING
G0 QUALIFIED: NO
G1 Entry: PENDING
release: QUALITY_HOLD / DEFAULT_V1 / NO_RELEASE
```

在 same-revision remote artifact、owner review、generated-output blocker 与 LIVE-02 未闭合前，本 review
不能被用作发布或 G1 runtime 授权。
