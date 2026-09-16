# G0 Portable Python Identity 与 V6 执行记录

版本：2026-08-06 V1  
状态：`ENGINEERING_PASS / OWNER_REVISION_HOLD / REMOTE_CI_HOLD / DEFAULT_V1 / NO_RELEASE`  
上位目标：`14_RAG_WIKI_STAGE_OBJECTIVE_BREAKDOWN.md`  
准入边界：`12_G0_VERSION_SCOPE_MANIFEST.md`  
Gate Review：`reviews/23_RAG_WIKI_G0_V6_PORTABLE_PYTHON_GATE_REVIEW_2026-08-06.md`

## 1. 目的与结论

V5 self-contained cleanroom 在 Python 3.13 能通过，但在 Python 3.12 完整收集时出现 72 个错误。
根因不是业务行为，而是若干“当前生产权威”把 CPython 次版本私有 bytecode、stack size、stdlib
内部模块路径写入持久摘要。修复第一条链后，完整 3.12 回归又暴露 Notebook Golden 使用
`ast.dump` 的次版本空字段差异。

V6 将两类问题同时关闭：

1. 持久 code identity 使用源码 AST、签名、默认值和逻辑 stdlib identity，不记录 `co_code`、
   `co_stacksize`、checkout path、虚拟环境 path 或 Python minor；
2. 运行时完整性仍由当前解释器重新编译 canonical module source，并严格比较当前解释器 bytecode，
   因而不能通过“为了可移植而弱化篡改检测”；
3. Notebook external dependency identity 使用冻结的 empty-field-free AST 表示，3.12/3.13 对同一源码
   产生同一历史 Golden package；
4. released/historical artifact 保持原始版本与摘要，只有 current runtime authority 显式换代；
5. Python 3.12.13 与 3.13.12 对同一最终源码各收集并通过 2,072 项后端测试；前端 179 项、
   typecheck、production build 通过；Ruff 通过。

该结论只提升本地工程可复现性，不等于 owner-reviewed revision、远程 CI、真实六源质量或生产授权。

## 2. 失败证据与根因分解

| 证据 | 观察 | 根因 | 处置 |
|---|---|---|---|
| V5 Python 3.12 cleanroom | 72 collection errors | current authority 持久化 CPython minor 私有 code fields | 保留 V5 为负证据；建立 portable identity v1 |
| current Codex/Experiment/Release/CB6 | 3.13 可导入，3.12 digest mismatch | bytecode、stack size、stdlib internal module path | current authority 换代；released rows 不回写 |
| V6 首轮 Python 3.12 full | 5 Notebook failures | 3.12 `ast.dump` 输出 `keywords=[]`，3.13 默认省略 | 冻结自有 AST dump；历史 Notebook digest 不变 |
| V6 当时前端依赖审计 | 2 high，来自同一 RSC-only advisory | 上游声明修复版本尚不可从 npm registry 安装 | 当时保持 7.18.2/client-only；后续由 V15/ADR-MAT-018 关闭 |

禁止处置包括：删除 Python 3.12 matrix、跳过失败、为 3.12 写第二套历史 Golden、把旧 artifact
摘要改成当前值、仅比较可移植结构而取消内存篡改检测。

## 3. 双层身份合同

### 3.1 Persistent identity

`src/evidence_rag/code_identity_v1.py` 的持久载荷只允许：

- normalized source AST 与 source-declared qualname；
- 参数数量、变量名、引用名、free/cell vars；
- portable constants、defaults 与 keyword defaults；
- `python-stdlib + implementation + language_major` 的逻辑运行时身份；
- stdlib/builtin 的公开逻辑 module/qualname。

以下字段禁止进入持久 authority：`co_code`、`co_stacksize`、line table、first line、filename、
checkout root、venv root、stdlib 私有重导出路径和 Python minor。

### 3.2 Runtime integrity

canonical function 在成为当前权威前必须：

1. 仍绑定原 module globals 和声明位置；
2. 从当前磁盘 module source 重新编译；
3. 以同一解释器比较 strict bytecode/code constants/names/flags；
4. module/export/object/builtins/global binding 均为 exact；
5. 任一不一致 fail closed。

这使“跨解释器可移植”和“同解释器防内存篡改”成为两条独立且同时成立的保证。

## 4. Authority 换代边界

| 链 | current 换代 | historical 边界 |
|---|---|---|
| Codex baseline | current production authority v5 | 旧 correction artifact 与 evaluator fingerprint 不变 |
| Codex facts/X1 | source authority v2；CX1 component sets 重算 | 已发布 Golden/correction membership 不变 |
| Experiment fixture | current production authority v5 | released Foundation v1 rows/parity 不变 |
| Experiment baseline | current production authority v8 | historical E-B0 run authority 不变 |
| Release admission | production authority registry v4 | reviewed evidence artifact 不回写 |
| C-B6 | runtime method authority v2 | released method fingerprint 与 gate artifact 不变 |
| Notebook Golden | 无历史版本换代 | package、authority、recipe 三个历史摘要在 3.13 保持原值，3.12 对齐原值 |

current authority 任何后续改动都必须显式升级版本/集合摘要并重跑双版本；historical verifier 必须继续
验证发布时 identity，不能自动追随 current。

## 5. 回归与 CI 合同

### 5.1 必须长期保留的定向测试

- 固定 portable function digest 在 3.12/3.13 相同；
- relocation 只改变 `co_filename` 时 persistent identity 不变；
- canonical function 内存 `__code__` 篡改被 source recompile 拒绝；
- `pathlib.Path` 私有模块重排投影为公开逻辑身份；
- builtin alias 不能用 `min` 冒充 `max`；
- classmethod/staticmethod descriptor kind 改变使 release authority 失效；
- Notebook external reference 使用固定 empty-field-free AST ID；
- Notebook released package/authority/recipe 在两个 Python minor 完全一致。

### 5.2 完整矩阵

`.github/workflows/backend-ci.yml` 必须保留 Python `3.12` 与 `3.13`，两项都执行 locked install、
Ruff 和完整 `backend-full`。不允许用单版本成功替代另一版本，也不允许在 matrix 中加入针对上述
identity 测试的 skip/xfail。

本地最终证据：

| 验证 | 结果 |
|---|---|
| Python 3.13.12 backend full | 2,072 collected / 2,072 PASS |
| Python 3.12.13 backend full | 2,072 collected / 2,072 PASS |
| Ruff `src tests` | PASS |
| Frontend Vitest | 31 files / 179 PASS |
| Frontend typecheck/build | PASS / PASS |
| V6 runtime candidate cleanroom | receipt/history/security/ordinary clone/release-ready PASS |

共同剩余 warning 是 Starlette/FastAPI test client 的第三方弃用提示，不影响本次结果，但必须在依赖
升级工作包跟踪。

## 6. V6 运行资格、V7–V11 准入与剩余 HOLD

V6 artifact 固定 portable runtime qualification；V7/V8 完成文档收口，V9 补齐 owner/CI handoff
并保留 clean-full generated HTML 失败，V10 修复 source authority 并暴露 exclusion 语义矛盾，
V11 完成 portable exclusion 分层；旧 artifact 均不覆盖。V11 是 portable-exclusion 产品全链记录；
当前 owner-review source candidate 由 `12_G0_VERSION_SCOPE_MANIFEST.md` 的 current authority 接续。
V11 artifact 自身 canonical
`content_sha256`、`source_snapshot_sha256`、文件数与排除分母是最终权威；本文不复制这些自引用
摘要，避免文档更新反向改变 snapshot。

仍未关闭：

1. 当前用户工作树不是 owner-reviewed clean revision；
2. 已跟踪生成物 `web/index.html` 仍使正式工作树 release-ready fail closed；
3. GitHub 远程 Python 3.12/3.13 workflow 尚未观察；
4. React Router finding 已由 V15 本地工程移除，但 same-revision remote audit/SBOM 与浏览器验收未观察；
5. G1 design 尚需 owner/architecture review；
6. 真实六源、Wiki grounded answer、质量/容量/shadow/canary 均未资格化。

因此 Gate 仍为 `QUALITY_HOLD / DEFAULT_V1 / NO_RELEASE`。

## 7. 基于证据重设目标

当前唯一可执行外部目标仍是 `WP-G0-05 Version admission`：owner 审阅 `12` 文档指向的 current
文件集合与 PENDING review packet，并形成受控
revision，取消跟踪生成物，从 fresh checkout 跑 locked frontend/backend、release-ready 与远程双
Python/admission CI，再生成新的 G0 package/review。具体步骤见
`17_G0_OWNER_ADMISSION_AND_REMOTE_CI_HANDOFF.md`。

`WP-G1I-01 Binding foundation` 保持未解锁。只有 G0 owner admission 和 G1 design review 同时通过，
才从 `T1.1.1 canonical JSON/digest/ID contract` 开始；不得并行实现六源 gateway 或 Wiki runtime。
