import { describe, expect, it } from "vitest";
import {
  cleanEvidenceText,
  humanizeSearchTitle,
  humanizeSessionTitle,
  presentSearchSnippet,
  semanticFileTitle,
  semanticSourceTitle,
  sourceDestination,
  structureTaskSummary,
} from "./presentation";

describe("evidence presentation", () => {
  it("removes serialized UI noise and escaped control characters", () => {
    const value =
      String.raw`…\\" - generic: CommandExecution - article: - textbox: \\"检索会话\\" \\n 实现 rerank 缓存方案`;
    const cleaned = cleanEvidenceText(value);
    expect(cleaned).toContain("实现 rerank 缓存方案");
    expect(cleaned).not.toContain("generic:");
    expect(cleaned).not.toContain("\\n");
    expect(cleaned).not.toContain('\\"');
  });

  it("presents markdown links as readable labels in audit text", () => {
    expect(
      cleanEvidenceText(
        "验证见 [Codex 会话测试](/workspace/tests/test_codex_sessions.py)",
      ),
    ).toBe("验证见 Codex 会话测试");
  });

  it("uses user-facing Codex result titles", () => {
    expect(humanizeSearchTitle("工具结果")).toBe("执行结果");
    expect(humanizeSearchTitle("代码补丁")).toBe("代码补丁");
  });

  it("summarizes raw Codex events instead of exposing serialized payloads", () => {
    const summary = presentSearchSnippet(
      "codex",
      "工具结果",
      '{"payload":{"type":"response_item"},"raw_objects":6}',
    );
    expect(summary).toContain("结构化结果");
    expect(summary).not.toContain("raw_objects");

    const patch = presentSearchSnippet(
      "codex",
      "代码补丁",
      "*** Update File: /workspace/src/retrieval.py",
    );
    expect(patch).toContain("/workspace/src/retrieval.py");
  });

  it("replaces machine identifiers with a goal-led session title", () => {
    expect(
      humanizeSessionTitle(
        "thread-019f9976-c2eb-72c1-89ea-683855ded0fe",
        "优化会话审计与关联来源跳转。",
      ),
    ).toBe("优化会话审计与关联来源跳转");
    expect(humanizeSessionTitle("implement-rerank-cache")).toBe("Implement Rerank Cache");
  });

  it("organizes a flat task summary into auditable sections", () => {
    const sections = structureTaskSummary(
      "- 新增会话命令详情面板\n- pytest 通过 18 项测试\n- 后续补充移动端验证",
    );
    expect(sections.map((item) => item.title)).toEqual([
      "关键变更",
      "验证结果",
      "风险与后续",
    ]);
  });

  it("builds a deep link for a code search result", () => {
    const destination = sourceDestination("project-rag", {
      entity_id: "symbol://rank",
      entity_type: "Function",
      source: "code",
      title: "rank",
      subtitle: "src/retrieval.py",
      locator: "rag@abcdef:src/retrieval.py#L10-L30",
      snippet: "def rank(): ...",
      repository_id: "repo-rag",
      path: "src/retrieval.py",
    });
    expect(destination).toContain("/p/project-rag/code?");
    expect(destination).toContain("file=src%2Fretrieval.py");
    expect(destination).toContain("repository=repo-rag");
  });

  it("turns source paths into user-facing audit titles", () => {
    expect(semanticFileTitle("src/evidence_rag/codex_store.py")).toBe(
      "Codex 会话存储与检索实现",
    );
    expect(
      semanticSourceTitle({
        entity_id: "file://codex-store",
        source: "code",
        title: "src/evidence_rag/codex_store.py",
        subtitle: "src/evidence_rag/codex_store.py",
        locator: "rag@abcdef:src/evidence_rag/codex_store.py#L10-L30",
        snippet: "class CodexStore: ...",
        path: "src/evidence_rag/codex_store.py",
      }),
    ).toBe("Codex 会话存储与检索实现");
    expect(
      semanticSourceTitle({
        entity_id: "file://embeddings-diff",
        source: "code",
        title: "embeddings.py#diff 实现",
        subtitle: "rag@abcdef",
        locator: "rag@abcdef:src/evidence_rag/embeddings.py#diff",
        snippet: "TOKEN_RE = ...",
      }),
    ).toBe("向量表示与检索实现");
  });

  it("removes audit fragments from code source jump paths", () => {
    const destination = sourceDestination("project-rag", {
      entity_id: "file://embeddings-diff",
      source: "code",
      title: "embeddings.py#diff 实现",
      subtitle: "rag@abcdef",
      locator: "rag@abcdef:src/evidence_rag/embeddings.py#diff",
      snippet: "TOKEN_RE = ...",
      repository_id: "repo-rag",
    });
    expect(destination).toContain("file=src%2Fevidence_rag%2Fembeddings.py");
    expect(destination).not.toContain("%23diff");
  });
});
