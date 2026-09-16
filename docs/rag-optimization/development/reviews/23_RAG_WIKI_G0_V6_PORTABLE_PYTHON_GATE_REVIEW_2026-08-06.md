# RAG + Wiki G0 V6 Portable Python Gate Review

日期：2026-08-06  
判定：`ENGINEERING_PASS / VERSION_ADMISSION_HOLD / REMOTE_CI_HOLD / SUPPLY_CHAIN_HOLD`  
运行：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. Review 范围

本次复核审查 V5 在 Python 3.12 暴露的持久 code identity 缺陷、Notebook AST 次版本漂移、当前
authority 换代、历史 artifact 保护、双版本完整回归、V6 admission 与 self-contained cleanroom。

不审查真实六源质量、G1 runtime、Wiki grounded answer、生产 shadow/canary 或 owner 版本纳管。

## 2. 硬问题与处置结论

| 问题 | 结论 |
|---|---|
| 持久 authority 是否仍含 CPython minor bytecode | 否；仅私有 same-interpreter integrity payload 保留 raw bytecode |
| 移除 bytecode 是否降低篡改检测 | 否；canonical source 由当前解释器重编译并严格比较 |
| 3.12/3.13 stdlib 内部路径是否改变 authority | 否；投影为 Python-major logical stdlib identity |
| current 换代是否覆盖 released artifacts | 否；Codex correction、E-B0、CB6 与 Notebook Golden 历史 identity 保留 |
| Notebook Golden 是否通过改常量适配 3.12 | 否；修复 AST 规范化，3.13 原历史摘要不变，3.12 对齐 |
| 完整测试是否只在一个 Python 版本通过 | 否；两版本同一源码各 2,072/2,072 PASS |
| V6 是否只在脏工作树声称 release-ready | 否；脏工作树按设计拒绝，bytes-only cleanroom 为 release-ready |

## 3. 证据

- Python 3.13.12：2,072 tests collected，完整后端 PASS；
- Python 3.12.13：2,072 tests collected，完整后端 PASS；
- Ruff：PASS；
- Frontend：31 files / 179 tests、typecheck、production build PASS；
- V6 runtime candidate：1,087 files；bytes-only materialize、receipt、431-object history、安全分母、ordinary clone、
  `--require-release-ready` PASS；
- 新增 portable identity、relocation、memory tamper、stdlib alias/descriptor、Notebook AST 精确回归；
- `.github/workflows/backend-ci.yml` 已定义 3.12/3.13 locked matrix，但远程 run 未观察。

证据位置：

- `src/evidence_rag/code_identity_v1.py`；
- `tests/test_code_identity_v1.py`；
- `src/evidence_rag/rag/sources/notebook/code_analyzer.py`；
- `docs/rag-optimization/development/16_G0_PORTABLE_PYTHON_IDENTITY_AND_V6_EXECUTION_RECORD.md`；
- `artifacts/rag-maturity/g0/admission-20260806-v6/admission.json`。

本文和执行记录写入后，documentation-complete source snapshot 由不可覆盖的 V7/V8 承接；owner
handoff 与全链 CI 收口由 `admission-20260806-v9` 承接。V6–V8 均不被回写。

## 4. 剩余阻断

1. owner-reviewed revision 与 fresh clean checkout 未证明；
2. tracked `web/index.html` 仍是正式工作树 release blocker；
3. GitHub 双 Python 远程 CI 未观察；
4. React Router 新 RSC advisory 的官方 patched 版本尚不可从 npm registry 安装；应用不使用
   unstable RSC，但 audit 仍为 high；
5. G1–G9 的真实数据、质量、安全、容量、生产观测未完成。

## 5. Gate 决定

V6 通过本地工程可移植性与 self-contained cleanroom Gate；V7/V8 完成文档收口，V9 补齐
owner/CI handoff 并保留 clean-full 失败，V10 修复 source authority，V11 完成产品全链；当前
source-exact owner 审阅候选由文档 12 的 current authority 接续。V5 保留为
Python 3.12 失败负证据。由于上述外部/供应链条件未关闭，G0 不提升为
`QUALIFIED`，G1 runtime 不解锁，默认引擎不变。

下一 Gate 只接受同一 owner revision 的 clean checkout、双 Python 远程 CI、前端 locked build、
release-ready verify 与更新后的不可变 package；不接受口头确认或本地脏工作树结果替代。
