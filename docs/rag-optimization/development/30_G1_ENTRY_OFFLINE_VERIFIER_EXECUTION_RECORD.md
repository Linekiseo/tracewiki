# G1 Entry 离线验证器实施、证据与当前准入记录

版本：2026-08-06 V1  
工作包：`WP-G1D-05`  
状态：`VERIFIER_LOCAL_ENGINEERING_PASS / REAL_ENTRY_NOT_SATISFIED`  
上位合同：`28_G1_ENTRY_DECISION_AND_REQUIREMENT_TRACE_PACKET.md` V2  
运行边界：`OFFLINE / READ-ONLY INPUT / DEFAULT_V1 / NO_RELEASE / FORMAL_DB_NOT_ACCESSED`

## 1. 结论

G1 Entry 已从“文档要求”推进为可执行的 fail-closed 判定器。新增实现能离线读取 Entry Packet、
Ed25519 attestation bundle、trusted keyset 和 G0 admission manifest，重算 E-01–10，并只在十项全部
PASS、packet 为 APPROVED、blockers 为空且 G0 原生验证成功时输出 `ENTRY_QUALIFIED`。

本工作包没有让真实项目 Entry 通过。当前仍缺 owner-reviewed revision、remote CI run/attempt/artifact、
带外 trust anchor、D1–D5、数据 owner、独立 reviewer 和 release owner 的真实收据；本记录对应的
V16 当时仍有
tracked generated-output blocker。测试中的全绿 packet 和固定私钥只验证协议，不构成项目授权。

## 2. 实施边界

| 项目 | 实施 | 明确未做 |
|---|---|---|
| canonical data | strict UTF-8 canonical-json-v1；duplicate/NFC collision/float/non-finite/control 拒绝 | 不宽容重写输入 |
| identity | domain-separated SHA-256；packet/keyset/bundle/receipt/subject/set 各自 domain | 不信任手填摘要 |
| cryptography | Ed25519；canonical base64；key role/有效期/撤销；receipt 决策/有效期 | 不在线取 key 或 receipt |
| trust | 调用方显式提供带外 keyset digest | 不从仓库或 packet 自举信任 |
| revision | owner、CI、D1–D5、data、review、runtime 全绑定 reviewed commit | 不允许跨 revision 拼接 |
| G0 | 调用 G0 V2 原生完整 verifier，再交叉核对 digest/source/file/scope/blocker | 不只信任聚合数字 |
| CI | 连续 attempt history；job/stage/log/artifact digest；CI receipt 原子绑定四块 Gate 聚合 | 不接受 badge、URL 或被覆盖的失败 attempt |
| duties | architecture≠rollback；owner≠independent；independent≠runtime signer | 不用 role 名称替代 key identity |
| output | canonical `rag-g1-entry-verification-v1`；显式记录无 network/DB/release | 不改变 runtime/default/schema |

实现文件：

- `src/evidence_rag/evaluation/maturity_g1_entry_v1.py`
- `tests/test_maturity_g1_entry_v1.py`
- `Makefile` 的 `g1-entry-verify`

## 3. 关键对抗性修正

### 3.1 packet 摘要不是签名

仅重算 `content_sha256` 无法证明 packet 作者有 authority。verifier 要求 packet 中所有 receipt 引用与
bundle 集合精确相等，逐条验 Ed25519 签名，并把 bundle keyset 与调用方带外 digest 比较。仓库自填
`APPROVED` 或把本地 keyset digest 一并提交，均不能替代调用方信任配置。

### 3.2 CI 必须签 Gate 聚合，而不只签 run id

首版实现复核时发现：若 CI receipt 只签 `remote_ci`，攻击者可改写 G0 exact/cleanroom、generated
boundary 或 supply-chain aggregate，再重算 packet digest。现已把 `REMOTE_CI` subject 冻结为：

```text
{
  g0_admission,
  generated_output_boundary,
  remote_ci_without_receipt_reference,
  supply_chain_disposition
}
```

27 个 Entry test case 中包含三类 aggregate rewrite，均会使 E-03/E-04 进入 HOLD。

### 3.3 G0 必须原生完整复核

verifier 调用 `maturity_g0_admission_v2.verify_admission_manifest`，由 G0 自己验证 schema、scope policy、
逐文件 source identity、state/scope denominator、exclusion/finding、history fixture 和 DB boundary；Entry
层只追加 packet 交叉核对。合成一个只有外层 digest、files/counts 的“像 G0 的 JSON”不再可通过。

### 3.4 rerun 与职责分离

attempt history 必须严格覆盖 `1..run_attempt`，最后 attempt conclusion 必须与 run conclusion 一致；
失败或取消记录不能被删除。即使 keyset 给同一 key 多个合法 role，owner 与 independent reviewer、
architecture 与 rollback owner、independent reviewer 与 runtime signer 仍必须使用不同 key。

## 4. 验证结果

| 验证 | Python 3.12.13 | Python 3.13.12 |
|---|---:|---:|
| Entry verifier cases | 27/27 PASS | 27/27 PASS |
| 相邻 G0 workflow contract | 3/3 PASS | 3/3 PASS |
| 合计定向回归 | 30/30 PASS | 30/30 PASS |
| Ruff（实现+测试） | PASS | PASS |
| 完整 backend | 2,104/2,104 PASS | 2,104/2,104 PASS |

双版本完整回归各只有同一条已知 Starlette/FastAPI TestClient 第三方弃用警告，没有新增 warning、跳过
或排除项。

覆盖的主要拒绝面：

1. 无外部 receipt 的仓库自批；
2. 错误带外 trust digest、过期 key/receipt、签名篡改；
3. 已签名但跨 revision/snapshot 的 CI；
4. rerun 覆盖旧失败、badge/日志链接冒充内容摘要；
5. G0 file/scope 聚合伪造与完整 schema/policy 伪造；
6. G0/generated/supply 聚合被重算 packet digest 后改写；
7. D1–D5 PENDING、owner 与 independent 同 key；
8. supply exception 缺发布阶段禁止项或已经过期；
9. production authorization 自报、default runtime 改变、generated blocker；
10. absolute path、URI、token/secret、duplicate key、NFC collision、float 和 copied packet tamper；
11. CLI 输出非 canonical 的风险；
12. 成功路径 network/database/subprocess I/O 必须为零。

一次把 Python 3.12 与 3.13 的 `uv --python` 命令并发运行时，两个进程竞争同一个 `.venv`，导致收集前
项目包暂时缺失。该结果记录为 `RUNNER_ENVIRONMENT_INTERFERENCE`，不计入产品失败；本地双版本必须
串行 `uv sync --frozen --extra test --python X` 后 `uv run --no-sync ...`，或使用完全隔离的 venv。
远程 matrix job 天然使用独立 runner，仍必须保留。

## 5. 操作合同

推荐通过 Make 入口显式传参：

```text
make g1-entry-verify \
  G1_ENTRY_PACKET=... \
  G1_ENTRY_ATTESTATIONS=... \
  G1_ENTRY_KEYSET=... \
  G1_ENTRY_TRUSTED_KEYSET_SHA256=sha256:... \
  G1_ENTRY_VERIFICATION_TIME=YYYY-MM-DDTHH:MM:SS.ffffffZ \
  G0_ADMISSION_MANIFEST=...
```

信任 digest 必须来自独立组织配置或等价带外渠道。命令行、packet 或仓库 README 中出现一个相同
digest，只证明字节相同，不证明调用方已信任签发者。

## 6. 当前 Gate 与停止条件

```text
WP-G1D-05 verifier implementation: LOCAL_ENGINEERING_PASS
real trusted keyset: ABSENT
real attestation bundle: ABSENT
G0 QUALIFIED: NO
G1 Entry E-01..E-10: NOT SATISFIED
T1.1.1 runtime: NOT STARTED
formal database accessed: false
default engine: v1
release: QUALITY_HOLD / NO_RELEASE
```

在真实 Entry 通过前，允许继续做 verifier/documentation/admission handoff 的只读或测试工作；禁止创建
V2 schema、连接正式数据库、实现 raw runtime、修改默认指针、启动 shadow/canary 或声明生产成熟。

## 7. 下一目标

当前唯一准入目标仍是 owner handoff：把最新完整 source snapshot 形成受审 revision，在同一 revision
生成 remote Python 3.12/3.13、exact/cleanroom/frontend/supply-chain/release-ready artifact，签署 D1–D5、
fixture data boundary、independent scope 和 runtime boundary，并从独立渠道配置 trusted keyset digest。
只有离线 verifier 对真实材料输出 `ENTRY_QUALIFIED`，才解锁 `T1.1.1 C1–C10`；它仍不解锁数据库、
Wiki V2、dual-write 或发布。

后续 `WP-G1D-06` 已把人工 handoff 收敛为 PENDING draft、fact-ready signing requests、外部 receipt
assembly 和原 verifier 复核四步；操作权威见 `31_G1_ENTRY_SIGNING_HANDOFF_AND_ASSEMBLY_RUNBOOK.md`。
该后续工具在 Python 3.12.13/3.13.12 各完成 Entry+handoff 40、与 G0 workflow 合计 43、完整
backend 2,117；仍不产生真实签名或 Entry authority。
