import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SessionGraphWorkspace } from "./SessionGraphWorkspace";

const timeline = {
  id: "codex://thread/thread-machine-id",
  thread_id: "thread-machine-id",
  project_id: "project-rag",
  title: "thread-019f9976-c2eb-72c1-89ea-683855ded0fe",
  cwd: "/workspace/rag",
  status: "completed",
  started_at: "2026-07-25T08:00:00Z",
  updated_at: "2026-07-25T09:00:00Z",
  turn_count: 1,
  file_change_count: 1,
  command_count: 2,
  validation_count: 1,
  metadata: {},
  turns: [
    {
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
    },
  ],
};

const related = {
  query_id: "query-1",
  total: 1,
  trace: {},
  results: [
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
  ],
};

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">{`${location.pathname}${location.search}`}</output>
  );
}

function renderGraph(turnId = timeline.turns[0].id) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          `/p/project-rag/sessions/thread-machine-id/graph?turn=${encodeURIComponent(turnId)}`,
        ]}
      >
        <Routes>
          <Route
            path="/p/:projectId/sessions/:threadId/graph"
            element={
              <>
                <SessionGraphWorkspace />
                <LocationProbe />
              </>
            }
          />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SessionGraphWorkspace", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/turn-audit")
          ? timeline.turns[0]
          : url.includes("/timeline")
            ? {
                ...timeline,
                turns: timeline.turns.map((turn) => ({
                  ...turn,
                  operations: [],
                })),
              }
            : related;
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  it("renders a standalone draggable evidence graph with an interactive inspector", async () => {
    const user = userEvent.setup();
    renderGraph();

    expect(
      await screen.findByRole("heading", {
        name: "优化会话审计与关联来源跳转",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/单会话变动图谱/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "变动图谱" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByLabelText("可拖动的单轮证据关系图")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /操作节点：解析、实现与索引操作/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("节点检查器")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: /操作节点：解析、实现与索引操作/ }),
    );
    await user.click(screen.getByRole("button", { name: /展开 1 条执行/ }));

    expect(
      screen.getAllByText(/sed -n '1,220p' src\/retrieval.py/).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByRole("heading", { name: "解析、实现与索引操作" }),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "代码节点：混合检索与结果排序" }),
    );
    expect(screen.getByRole("button", { name: "打开源码" })).toBeEnabled();
  });

  it("returns to the chain audit view while preserving the selected turn", async () => {
    const user = userEvent.setup();
    renderGraph();

    await screen.findByLabelText("可拖动的单轮证据关系图");
    await user.click(screen.getByRole("button", { name: "链式审计" }));

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/p/project-rag/sessions/thread-machine-id?turn=codex%3A%2F%2Fthread%2Fthread-machine-id%2Fturn%2F1",
    );
  });

  it("loads an explicitly requested older turn outside the latest timeline page", async () => {
    const oldTurnId = "codex://thread/thread-machine-id/turn/old-87";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/turn-audit")
          ? {
              ...timeline.turns[0],
              id: oldTurnId,
              ordinal: 87,
              goal: "审阅旧轮次的真实代码影响",
            }
          : url.includes("/timeline")
            ? timeline
            : related;
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    renderGraph(oldTurnId);

    expect(
      await screen.findByText("单会话变动图谱 / 轮次 87"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "目标节点：审阅旧轮次的真实代码影响",
      }),
    ).toBeInTheDocument();
  });

  it("moves a node with real pointer events and keeps edges attached to real ports", async () => {
    const { container } = renderGraph();
    const pointerEvent = (
      type: string,
      values: {
        pointerId: number;
        clientX: number;
        clientY: number;
        button?: number;
      },
    ) => {
      const event = new Event(type, { bubbles: true, cancelable: true });
      Object.entries({ button: 0, ...values }).forEach(([key, value]) => {
        Object.defineProperty(event, key, { value });
      });
      return event;
    };

    const goal = await screen.findByRole("button", {
      name: /目标节点：优化会话审计与关联来源跳转/,
    });
    const conclusion = screen.getByRole("button", { name: /结论节点/ });
    const world = container.querySelector<HTMLElement>(".session-graph-world");
    const edge = container.querySelector<SVGPathElement>(
      '[data-edge="goal-operation"]',
    );
    expect(world?.style.width).toBe("4800px");
    expect(world?.style.height).toBe("3000px");
    expect(edge).not.toBeNull();
    const beforePath = edge?.getAttribute("d");

    expect(goal.querySelector('[data-port="goal:input"]')).toBeNull();
    expect(goal.querySelector('[data-port="goal:output"]')).not.toBeNull();
    expect(
      conclusion.querySelector('[data-port="conclusion:output"]'),
    ).toBeNull();

    fireEvent(
      goal,
      pointerEvent("pointerdown", {
        pointerId: 7,
        clientX: 80,
        clientY: 280,
      }),
    );
    fireEvent(
      window,
      pointerEvent("pointermove", {
        pointerId: 7,
        clientX: 150,
        clientY: 320,
      }),
    );
    fireEvent(
      window,
      pointerEvent("pointerup", {
        pointerId: 7,
        clientX: 150,
        clientY: 320,
      }),
    );

    expect(goal.style.left).toBe("1810px");
    expect(goal.style.top).toBe("1260px");
    expect(edge?.getAttribute("d")).not.toBe(beforePath);
  });
});
