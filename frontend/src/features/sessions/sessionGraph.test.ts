import { describe, expect, it } from "vitest";
import type { CodexTimelineTurn, SearchResult } from "../../lib/types";
import { arrangeSessionGraphNodes, buildSessionGraph } from "./sessionGraph";

const turn: CodexTimelineTurn = {
  id: "codex://thread/thread-machine-id/turn/1",
  ordinal: 1,
  status: "completed",
  started_at: "2026-07-25T08:00:00Z",
  completed_at: "2026-07-25T08:10:00Z",
  goal: "优化会话审计与关联来源跳转。",
  summary:
    "- 新增命令审计详情面板\n- pytest 通过 18 项测试\n- 后续补充移动端验证",
  files: ["src/retrieval.py"],
  file_changes: [
    {
      id: "change-1",
      path: "src/retrieval.py",
      change_type: "update",
      status: "completed",
      locator: "codex://thread/thread-machine-id/turn/1/item/change-1",
    },
  ],
  operations: [
    {
      id: "command-1",
      kind: "command",
      name: "命令执行",
      command: "sed -n '1,220p' src/retrieval.py",
      output: "class Retriever: ...",
      status: "completed",
      exit_code: 0,
      timestamp: "2026-07-25T08:04:00Z",
      locator: "codex://thread/thread-machine-id/turn/1/item/command-1",
    },
    {
      id: "validation-1",
      kind: "validation",
      name: "测试验证",
      command: "pytest -q",
      output: "18 passed",
      status: "passed",
      exit_code: 0,
      timestamp: "2026-07-25T08:08:00Z",
      locator: "codex://thread/thread-machine-id/turn/1/item/validation-1",
    },
  ],
  command_count: 2,
  validation_count: 1,
  locator: "codex://thread/thread-machine-id/turn/1",
};

const related: SearchResult[] = [
  {
    entity_id: "code://symbol/rank",
    entity_type: "Function",
    source: "code",
    title: "rank",
    subtitle: "src/retrieval.py",
    locator: "rag@abcdef:src/retrieval.py#L10-L30",
    snippet: "def rank(): ...",
    status: "indexed",
    channels: ["dense", "lexical"],
    repository_id: "repo-rag",
    path: "src/retrieval.py",
  },
];

describe("session graph model", () => {
  it("arranges evidence into stable stages with a separate risk rail", () => {
    const model = buildSessionGraph(turn, related);
    const byKind = new Map(model.nodes.map((node) => [node.kind, node]));

    expect(byKind.get("goal")!.x).toBeLessThan(byKind.get("operation")!.x);
    expect(byKind.get("operation")!.x).toBeLessThan(byKind.get("artifact")!.x);
    expect(byKind.get("artifact")!.x).toBeLessThan(byKind.get("validation")!.x);
    expect(byKind.get("validation")!.x).toBeLessThan(
      byKind.get("conclusion")!.x,
    );
    expect(byKind.get("risk")!.y).toBeGreaterThan(byKind.get("artifact")!.y);
    expect(arrangeSessionGraphNodes(model.nodes)).toEqual(model.nodes);
  });

  it("connects the turn goal, real artifacts, validation, conclusion, and follow-up", () => {
    const model = buildSessionGraph(turn, related);

    expect(model.nodes.find((node) => node.kind === "goal")?.title).toContain(
      "优化会话审计",
    );
    expect(model.nodes.find((node) => node.kind === "artifact")).toMatchObject({
      title: "混合检索与结果排序",
      locator: "rag@abcdef:src/retrieval.py#L10-L30",
      evidence: "def rank(): ...",
      facts: [
        { label: "变更", value: "修改" },
        { label: "证据", value: "代码片段" },
        { label: "定位", value: "可回查" },
      ],
      source: related[0],
    });
    expect(model.nodes.find((node) => node.kind === "validation")?.title).toBe(
      "pytest -q",
    );
    expect(model.nodes.some((node) => node.kind === "risk")).toBe(true);
    expect(model.nodes.some((node) => node.kind === "followup")).toBe(true);
    expect(
      model.nodes.find((node) => node.kind === "validation")?.facts,
    ).toEqual([
      { label: "退出码", value: "0" },
      { label: "状态", value: "通过" },
    ]);
    expect(
      model.edges.some(
        (edge) =>
          edge.source === "file:src/retrieval.py" &&
          edge.target === "validation:validation-1",
      ),
    ).toBe(true);
  });

  it("prioritizes executable code changes and reuses matching command output as evidence", () => {
    const model = buildSessionGraph(
      {
        ...turn,
        files: ["docs/plan.md", "web/index.html", "web/assets/app.js"],
        file_changes: [
          {
            id: "doc",
            path: "docs/plan.md",
            change_type: "patch",
            status: "completed",
            locator: "codex://doc",
          },
          {
            id: "html",
            path: "web/index.html",
            change_type: "patch",
            status: "completed",
            locator: "codex://html",
          },
          {
            id: "js",
            path: "web/assets/app.js",
            change_type: "patch",
            status: "completed",
            locator: "codex://js",
          },
        ],
        operations: [
          {
            id: "inspect-js",
            kind: "command",
            name: "命令执行",
            command: "sed -n '1,120p' web/assets/app.js",
            output: "export function openSessionGraph() {}",
            status: "completed",
            exit_code: 0,
            locator: "codex://inspect-js",
          },
        ],
      },
      [],
    );
    const artifacts = model.nodes.filter((node) => node.kind === "artifact");

    expect(artifacts[0]).toMatchObject({
      subtitle: "web/assets/app.js",
      evidence: "export function openSessionGraph() {}",
      locator: "codex://inspect-js",
    });
    expect(artifacts[0].facts).toContainEqual({
      label: "证据",
      value: "执行输出",
    });
  });
});
