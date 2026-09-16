# Contributing to TraceWiki

感谢你参与 TraceWiki。提交改动前，请先说明要解决的问题，并让代码、测试和文档保持在同一个版本边界内。

## 本地开发

后端需要 Python 3.12 或 3.13、Git 和 `uv`：

```bash
uv sync --extra test
cp .env.example .env
git fetch tests/fixtures/g0_history/code-golden-v2-history.bundle \
  refs/heads/code-golden-v2-source:refs/heads/code-golden-v2-source
uv run evidence-rag serve --reload
```

上面的本地 Git bundle 导入只创建测试所需的本地 fixture 分支并恢复固定历史对象；不会切换当前分支或工作树。
该分支仅用于让测试创建的临时克隆读取固定提交，不应推送到远端。

前端与桌面端：

```bash
cd frontend
npm ci
npm run typecheck
npm test -- --run
```

## 提交前验证

根据改动范围至少运行对应测试；准备合并前建议运行：

```bash
uv run ruff check src tests
uv run pytest -q
cd frontend && npm run typecheck && npm test -- --run
```

不要提交 `.env`、API key、Token、私钥、真实会话、正式数据库、缓存或生成构建产物。新增数据源、检索策略、
Wiki fact 或关系时，应同时补充边界测试，明确版本、Scope、ACL、来源定位和拒答/降级行为。

## Pull Request

- 一个 PR 聚焦一个可验证目标；说明动机、行为变化、验证命令和结果。
- 若改变 API、配置、数据模型或用户工作流，请同步更新文档。
- 不要用新的默认行为绕过现有 `DEFAULT_V1`、质量 Gate 或人工审阅边界。
- 安全问题请按 `SECURITY.md` 私下报告，不要创建公开 Issue。

提交贡献即表示你同意按仓库的 MIT License 许可该贡献。
