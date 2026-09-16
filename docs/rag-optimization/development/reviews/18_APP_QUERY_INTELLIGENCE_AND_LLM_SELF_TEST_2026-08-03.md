# App 智能查询算法与桌面模型接入自测报告

日期：2026-08-03  
范围：查询理解、查询分解、多源融合、桌面模型配置、macOS App、后端安全回归  
结论：`APP_QUERY_INTELLIGENCE_ENGINEERING_PASS`  
严重发现：`P0=0 / P1=0`  
发布结论：`DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 1. 本轮纠正的核心问题

查询理解不再由“命中某个关键词就选择某个意图”的规则链承担。当前
`query-understanding-hybrid-v2` 是一条可解释的混合算法链：

1. 本地冻结训练语料经过中英文规范化、词 unigram/bigram 与中文字符 bigram/trigram 特征化；
2. 使用 sublinear TF-IDF 构建意图和来源质心；
3. 以 cosine similarity 计算类别分数，并经 temperature softmax 得到后验分布；
4. 用 posterior entropy、top-two margin 和查询结构计算不确定性与复杂度；
5. 复杂问题以 evidence obligation 为单位，通过 bounded MMR 选择互补子查询；
6. 配置桌面模型后，模型只提交受 Pydantic schema、枚举、长度和安全约束的结构化规划，结果与本地后验融合；
7. 模型不可用、输出不合法或超时会退回本地统计路径，不退回关键词分类器。

正则/别名仍存在，但职责已被收窄为 sparse retrieval 的词表扩展；它不再决定 intent、source、scope 或
answer mode。

## 2. 检索与答案链

- 多查询结果采用 Reciprocal Rank Fusion：`sum(1 / (60 + rank))`，并加入有界 appearance bonus；
- 原始问题始终参与检索，子查询用于补齐证据义务而不是替换用户问题；
- 六源结果按统一 Evidence Pack 组织，保留 source status、engine routing、watermark、证据角色、claim-citation
  映射与交互状态迁移；
- 未配置模型时输出可核验的 deterministic retrieval answer；配置模型后只允许 grounded generation，引用不在
  Evidence Pack membership 中即拒绝；
- public query-understanding payload 会先执行敏感内容检测与脱敏，query、subquery、entity 不回显路径、凭据或秘密。

## 3. 桌面模型安全边界

- App-only 配置入口已经存在：Provider、API Base URL、模型 ID、API Key、保存、测试和删除；
- API Key 的持久层是 macOS Keychain / Windows Credential Manager，不写入项目、浏览器 localStorage、证据库或
  provider status 响应；
- 后端只维护 process-local provider registry，桌面 App 启动时从系统凭据库重新注入；
- 远程地址强制 HTTPS，只有 loopback 允许 HTTP；禁止 URL 内嵌账号、查询串和 fragment；
- 测试连接只发送固定 `health check`，不发送项目证据；真实问答只有用户显式执行时才会发送必要的受约束输入；
- 本轮尚未保存或发送用户提供的模型密钥。外部配置保持 `UNCONFIGURED`，等待敏感操作即时确认。

目标 provider 参数已经按官方 OpenAI-compatible Chat Completions 合同核对：

- API Base：`https://api.deepseek.com`
- Model：`deepseek-v4-flash`
- Chat Completion：`POST /chat/completions`

## 4. macOS App 实机自测

使用打包后的桌面 App（现已更名为
`frontend/src-tauri/target/release/bundle/macos/TraceWiki.app`）连接 system-temp 隔离后端
`http://127.0.0.1:8765`，完成以下交互：

- 项目中心识别 `project-rag`，项目总览显示仓库与 112 个研发会话；
- 设置弹窗显示“桌面安全与智能模型”，模型配置字段及系统钥匙串边界可见；
- 未配置外部模型时，App 明确显示“本地统计理解 / 未配置 · 本地算法兜底”；
- 真实查询完成，界面显示 TF-IDF n-gram × centroid softmax、posterior、entropy、complexity 和 Q1 分解；
- 代码与研发会话两源完成，空的 Experiment/Notebook/Document/Workspace 来源诚实显示无匹配；
- 返回 15 条 evidence、claim citation、engine routing、source status、watermark 与 interaction audit；
- 结果模式为 `RETRIEVAL_ONLY`，没有把未配置模型冒充成生成回答。

本次隔离查询的观测性能：代码源约 1.5 秒，会话源约 10.9 秒。总延迟的主要瓶颈是会话检索，而不是本地
query-understanding。该观测是单次开发机结果，不冒充生产 P95。

## 5. 自动化验收

- backend safe suite：`1950/1950 PASS`；
- 排除：C-B1–B5 的外部证据执行文件，以及两条会解析正式服务库路径的既有节点；C-B6 保留；
- query/wiki/API targeted regression：PASS；
- `ruff check`：当前查询、provider、Wiki、runtime、API 与测试文件 PASS；
- `ruff format --check`：11 个本轮 Python 文件 PASS；
- Python in-memory compile：267 个相关源码/测试文件 PASS；
- frontend：31 files / 178 tests PASS；
- TypeScript typecheck：PASS；
- deterministic production web build：PASS；
- Rust `cargo check`：PASS；
- Rust tests：5/5 PASS；
- macOS `.app` 与 `.dmg`：已构建；App 实机交互通过；
- Windows：代码、凭据合同和 Tauri 工程已实现；当前 macOS 环境不冒充 Windows 真机运行结果。

自动化只使用 system tmp / in-memory / immutable fixtures。正式服务库
`/Users/example/project/rag/var/evidence-rag.sqlite3` 未被打开、哈希或 checkpoint，本报告也不声称其外部状态不变。

## 6. 当前诚实状态

仓库内 Wiki+RAG 查询理解、检索融合、证据组织、桌面安全配置和 macOS 打包已达到 engineering complete。
仍不应把下列事项写成已完成：

- 外部模型凭据保存与 DeepSeek 实际健康检查（等待即时确认）；
- 一次受约束的真实远程 grounded query（等待即时确认，且会向 provider 发送问题和必要证据）；
- Windows 真机安装、钥匙串和 UI 验收；
- production reviewed quality、真实并发、shadow/canary 和默认 V2/Wiki 切换。

Canonical：

`APP_QUERY_INTELLIGENCE_ENGINEERING_COMPLETE / PROVIDER_UNCONFIGURED / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`

## 7. 2026-08-04 状态补充

- 桌面模型配置已通过系统凭据库完成，隔离后端的 provider health 为 ready；API Key 未写入仓库、浏览器存储或证据库。
- 受约束的真实 grounded query 已完成，生成链与 citation membership 校验可用；回答相关性仍保留 `QUALITY_HOLD`，不据此切换默认版本或发布。
- 正式桌面交互继续采用 Tauri + React 的 Web UI；SwiftUI/WinUI 客户端作为平行增强线保留，未达到功能与交互同等前不替换正式界面。
- 产品名统一为 `TraceWiki（循证知识工作台）`，新图标由 ImageGen 生成并已进入 Web、macOS ICNS 与 Windows ICO/AppX 资源。

更新后的 canonical：

`APP_QUERY_INTELLIGENCE_ENGINEERING_COMPLETE / PROVIDER_READY / GROUNDED_QUERY_EXECUTED / DEFAULT_V1 / QUALITY_HOLD / NO_RELEASE`
