# G1 Entry 签署请求、外部收据与安全组装 Runbook

版本：2026-08-06 V1  
工作包：`WP-G1D-06`  
状态：`HANDOFF_TOOL_LOCAL_ENGINEERING_PASS / REAL_EXTERNAL_RECEIPTS_MISSING`  
上位合同：`28_G1_ENTRY_DECISION_AND_REQUIREMENT_TRACE_PACKET.md` V2  
验证器：`30_G1_ENTRY_OFFLINE_VERIFIER_EXECUTION_RECORD.md`  
边界：`OFFLINE / NO KEY GENERATION / NO SIGNING / NO NETWORK / NO DATABASE / NO RELEASE`

## 1. 目的与结论

V16 已能严格验证一个完整 Entry Packet，但此前 owner 仍需手工拆 subject、关联 15+ 个 receipt、回填
引用并重算 E-01–10。人工流程容易发生跨 revision、漏签、重复 receipt、旧 receipt 重放和聚合字段
漂移。

`maturity_g1_entry_handoff_v1.py` 将交接冻结为三个不可跳步的离线阶段：

```text
proposal
  → PENDING unsigned draft
  → fact-ready signing-request bundle
  → externally signed receipt bundle + out-of-band trust digest
  → assembled APPROVED packet
  → original offline verifier
```

该工具不生成 Ed25519 私钥、不生成签名、不从网络收集审批，也不能把未满足事实条件的 packet 变成
APPROVED。合成测试私钥仍只属于测试夹具。

## 2. 三阶段合同

### 2.1 `draft`：剥离权限，只保留事实

输入必须已经满足 Entry Packet schema 和 canonical digest。`draft` 会：

1. 删除 owner、CI、security、architecture、rollback、data、independent 和 runtime 的全部 receipt
   引用；
2. 将 attestation set 固定为空集合摘要；
3. 使用空 receipt map 重算 E-01–10；
4. 将所有 HOLD reason 排序写入 blockers；
5. 强制 `status=PENDING` 并重算 packet digest。

因此即使输入 proposal 自报 APPROVED，draft 输出也只能是无权限的 PENDING artifact。

### 2.2 `requests`：事实未就绪时禁止请求签名

只有以下非签名事实全部成立，才生成 signing requests：

| 范围 | 必须成立 |
|---|---|
| revision | reviewed time 存在；repository/commit/ref 合同有效 |
| G0 | exact/exclusion/cleanroom 为真；unapproved addition=0；release blocker=0；原生 manifest verifier PASS |
| remote CI | 同 repository/revision/source；3.12/3.13 full；完整 admission stages；连续 attempt history |
| generated | tracked generated=0；release blocker=0；Git clean；build reconstruction PASS |
| supply | high/critical=0；例外未过期且禁止 shadow/canary/default-v2/production |
| D1–D5 | ACCEPTED 或 reviewed ADR；同 revision；未过期 |
| data | fixture；stable；禁止 production connect；未过期 |
| independent | 只审 T1.1.1 C1–C10；implementation paths 为空 |
| runtime | default V1；未访问正式 DB；未改 schema/pointer；未授权 release |
| time | request time 不早于 owner review、independent review 和 remote CI completion |

任一项不满足都返回 `facts are not ready for signing`，避免让外部 approver 签署一个无论如何都不能进入
Entry 的 packet。

每个 request 使用 `rag-g1-entry-signing-request-v1`：

```text
schema_version
request_id
kind
issuer_role
revision
subject
subject_sha256
packet_content_sha256
content_sha256
```

bundle 使用 `rag-g1-entry-signing-request-bundle-v1`，额外绑定 request time、draft packet digest、G0
manifest digest 和排序后的完整 request 集。无 supply exception 时固定 15 项：owner 1、CI 1、
architecture 5、rollback 5、data 1、independent 1、runtime 1；每个 security exception 再增加 1 项。

### 2.3 `assemble`：只有完整外部 authority 才能组装

assembly 重新执行，而不是信任上一步结果：

1. 复核 draft 仍为无引用 PENDING 且 G0/facts 在当前 verification time 仍有效；
2. 完整重建 signing request bundle，拒绝改写 request id、subject、revision、role 或 packet digest；
3. 以调用方带外 digest 验 trusted keyset；
4. 逐 receipt 验 Ed25519、role、revision、subject、有效期和撤销；
5. receipt `issued_at` 不得早于 request time；
6. 每个 request 必须恰有一张 receipt，缺失、多余或一个 target 多张 receipt 均拒绝；
7. 回填引用后重算 attestation set、E-01–10、blockers、status 和 packet digest；
8. 只要一项 HOLD 就不输出部分批准 packet；全部 PASS 才输出 APPROVED；
9. 输出仍须交给原 `maturity_g1_entry_v1` verifier 独立复核。

## 3. 命令顺序

三个 Make target 都只向 stdout 输出 canonical JSON；调用方应使用不存在的新文件名，禁止覆盖历史
draft/request/packet。

```text
make g1-entry-draft \
  G1_ENTRY_PROPOSAL=proposal.json \
  G1_ENTRY_VERIFICATION_TIME=YYYY-MM-DDTHH:MM:SS.ffffffZ \
  > entry-draft.json

make g1-entry-signing-requests \
  G1_ENTRY_DRAFT=entry-draft.json \
  G1_ENTRY_VERIFICATION_TIME=YYYY-MM-DDTHH:MM:SS.ffffffZ \
  G0_ADMISSION_MANIFEST=admission.json \
  > signing-requests.json

# 外部系统独立审阅 requests，产生 attestations.json；keyset digest 走带外渠道。

make g1-entry-assemble \
  G1_ENTRY_DRAFT=entry-draft.json \
  G1_ENTRY_SIGNING_REQUESTS=signing-requests.json \
  G1_ENTRY_ATTESTATIONS=attestations.json \
  G1_ENTRY_KEYSET=keyset.json \
  G1_ENTRY_TRUSTED_KEYSET_SHA256=sha256:... \
  G1_ENTRY_VERIFICATION_TIME=YYYY-MM-DDTHH:MM:SS.ffffffZ \
  G0_ADMISSION_MANIFEST=admission.json \
  > assembled-entry.json

make g1-entry-verify \
  G1_ENTRY_PACKET=assembled-entry.json \
  G1_ENTRY_ATTESTATIONS=attestations.json \
  G1_ENTRY_KEYSET=keyset.json \
  G1_ENTRY_TRUSTED_KEYSET_SHA256=sha256:... \
  G1_ENTRY_VERIFICATION_TIME=YYYY-MM-DDTHH:MM:SS.ffffffZ \
  G0_ADMISSION_MANIFEST=admission.json
```

只有最后一条命令输出 `ENTRY_QUALIFIED` 才是 Entry 通过。`requests` 成功、外部人员收到请求、签名数
达到 15 或 assembly 输出文件存在，都不能单独替代最终结果。

## 4. 测试与对抗性证据

当前 Entry + handoff 文件共 40 个 case；加上 3 个相邻 G0 workflow contract，双 Python 定向集合为
43/43；Python 3.12.13 与 3.13.12 的完整 backend 均为 2,117/2,117。通过的关键场景包括：

1. draft 强制 PENDING、空 attestation set 和重算 blockers；
2. request bundle 15 项、排序稳定、重复运行字节一致；
3. request/bundle 不含 signature 或 private key；
4. D1–D5 PENDING、generated blocker、runtime V2 时不产生 signing request；
5. request 早于事实完成时拒绝；receipt 早于 request 时拒绝；
6. request 字段被改写且 bundle digest 被重算仍拒绝；
7. 错误 trust digest、缺 receipt、额外/重复 receipt 均拒绝；
8. 成功 assembly 的 packet 再由原 verifier 输出 ENTRY_QUALIFIED；
9. draft/request/assembly CLI 输出均为 canonical JSON；
10. 成功路径 network/database/subprocess I/O 为零；
11. Make 必须保留 draft → requests → assemble → verify 四入口，requests/assemble/verify 都显式绑定
    G0 manifest。

所有成功 assembly 都使用公开、确定性的测试私钥，只证明协议可执行，不证明真实 owner authority。

## 5. 当前 Gate

```text
WP-G1D-06 handoff tooling: LOCAL_ENGINEERING_PASS
real proposal on owner-reviewed revision: MISSING
real signing requests: NOT ISSUED
real external receipts: MISSING
real out-of-band trust digest: MISSING
G0 QUALIFIED: NO
G1 Entry: NOT SATISFIED
T1.1.1 runtime: NOT STARTED
default engine: V1
release: QUALITY_HOLD / NO_RELEASE
```

## 6. 下一目标

本工具关闭的是“如何安全交接和组装”，不是外部权限本身。下一目标恢复为真实 owner admission：先让
最新 G0 source snapshot 进入受审、clean revision，完成 same-revision remote CI 与 LIVE-02，再用本
runbook 产生真实 request/receipt/packet。Entry 通过后仍只解锁 T1.1.1 C1–C10。
