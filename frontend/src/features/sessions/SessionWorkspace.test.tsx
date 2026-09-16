import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SessionDetail } from "./SessionWorkspace";

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
  command_count: 1,
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
          patch:
            "@@ -10,3 +10,4 @@ def rank(items):\n def rank(items):\n-    return sorted(items)\n+    ranked = sorted(items)\n+    return ranked",
          additions: 2,
          deletions: 1,
          hunk_count: 1,
          patch_format: "unified",
        },
      ],
      operations: [
        {
          id: "command-1",
          kind: "validation",
          name: "命令执行",
          command: "pytest -q",
          output: "18 passed",
          status: "passed",
          exit_code: 0,
          timestamp: "2026-07-25T08:08:00Z",
          locator: "codex://thread/thread-machine-id/turn/1/item/command-1",
        },
      ],
      command_count: 1,
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

function SessionSwitchProbe() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate("/p/project-rag/sessions/thread-two")}>
      切换测试会话
    </button>
  );
}

function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={["/p/project-rag/sessions/thread-machine-id"]}
      >
        <Routes>
          <Route
            path="/p/:projectId/sessions/:threadId"
            element={
              <>
                <SessionDetail />
                <SessionSwitchProbe />
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

describe("SessionDetail audit workspace", () => {
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
            : url.includes("/v1/search")
              ? related
              : {
                  project: { settings: { workspace_path: "/workspace/rag" } },
                };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  it("renders a goal-led title, compact audit overview, and command evidence", async () => {
    const user = userEvent.setup();
    renderDetail();

    expect(
      await screen.findByRole("heading", {
        name: "优化会话审计与关联来源跳转",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("本轮结论")).toBeInTheDocument();
    expect(screen.getByText("完成事项")).toBeInTheDocument();
    expect(screen.getByText("验证与风险")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "查看本轮代码影响" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("pytest -q")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /执行记录/ }));
    const command = await screen.findByRole("button", { name: /pytest -q/ });
    expect(screen.queryByText("执行与验证审计")).not.toBeInTheDocument();
    expect(screen.queryByText("退出码")).not.toBeInTheDocument();
    await user.click(command);

    expect(screen.getAllByText("18 passed").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("退出码")).toBeInTheDocument();
    expect(screen.getByText("执行命令")).toBeInTheDocument();
    expect(screen.getByText("标准输出")).toBeInTheDocument();
    expect(screen.getAllByText("审计定位").length).toBeGreaterThanOrEqual(1);
  });

  it("supports direct turn navigation, detail tabs, and a collapsible source rail", async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("heading", { name: "优化会话审计与关联来源跳转" });
    expect(await screen.findByText("命中内容")).toBeInTheDocument();
    expect(screen.getByText("关联依据")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: /复制/ }).length,
    ).toBeGreaterThan(0);
    const sourceReader = screen.getByRole("button", {
      name: /展开详情：.*完整命中内容/,
    });
    await user.click(sourceReader);
    expect(
      screen.getByRole("dialog", { name: /完整命中内容/ }),
    ).toHaveTextContent("def rank(): ...");
    await user.click(screen.getByRole("button", { name: "关闭详情" }));
    await waitFor(() => expect(sourceReader).toHaveFocus());
    expect(await screen.findByLabelText("选择轮次")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /代码变更/ }));
    expect(screen.getByLabelText("搜索变更文件")).toBeInTheDocument();
    expect(
      screen.getAllByText("src/retrieval.py").length,
    ).toBeGreaterThanOrEqual(2);
    expect(
      screen.getByLabelText("代码变更明细：src/retrieval.py"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("−1").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("+2").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("return sorted(items)")).toBeInTheDocument();
    expect(screen.getByText("ranked = sorted(items)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "收起关联来源" }));
    expect(screen.queryByLabelText("来源互查详情")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^关联来源\s+1$/ }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps audited session code changes visible when project search has no matches", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/turn-audit")
          ? timeline.turns[0]
          : url.includes("/timeline")
            ? timeline
            : url.includes("/v1/search")
              ? { query_id: "empty", total: 0, trace: {}, results: [] }
              : {
                  project: { settings: { workspace_path: "/workspace/rag" } },
                };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    expect(
      await screen.findByText("直接来自会话审计；项目搜索用于补充其他来源"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("会话改动来源说明")).toHaveTextContent(
      "1 个变更文件",
    );
    const sourceFilters = screen.getByRole("navigation", {
      name: "关联来源类型",
    });
    await user.click(
      within(sourceFilters).getByRole("button", { name: "代码" }),
    );

    expect(screen.getAllByText("src/retrieval.py").length).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/会话审计 · 本轮代码变更/).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("该类型暂无关联")).not.toBeInTheDocument();
  });

  it("navigates an associated source to its original code location", async () => {
    const user = userEvent.setup();
    renderDetail();

    const open = await screen.findByRole("button", { name: "打开来源：rank" });
    await user.click(open);

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/p/project-rag/code?file=src%2Fretrieval.py&repository=repo-rag",
    );
  });

  it("opens the dedicated session graph without leaving the session workspace", async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByRole("heading", {
      name: "优化会话审计与关联来源跳转",
    });
    await user.click(screen.getByRole("button", { name: "变动图谱" }));

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/p/project-rag/sessions/thread-machine-id/graph?turn=codex%3A%2F%2Fthread%2Fthread-machine-id%2Fturn%2F1",
    );
  });

  it("paginates dense completion, validation, source, file, and operation records", async () => {
    const manyFiles = Array.from(
      { length: 9 },
      (_, index) => `src/module-${index + 1}.py`,
    );
    const manyOperations = Array.from({ length: 11 }, (_, index) => ({
      ...timeline.turns[0].operations[0],
      id: `validation-${index + 1}`,
      command: `pytest tests/test_case_${index + 1}.py -q`,
      locator: `codex://thread/thread-machine-id/turn/1/item/validation-${index + 1}`,
    }));
    const longTurn = {
      ...timeline.turns[0],
      summary: [
        "- 新增完成事项一组件",
        "- 新增完成事项二组件",
        "- 新增完成事项三组件",
        "- 新增完成事项四组件",
        "- 新增完成事项五组件",
        "- 新增完成事项六组件",
      ].join("\n"),
      files: manyFiles,
      file_changes: manyFiles.map((path, index) => ({
        id: `change-${index + 1}`,
        path,
        change_type: "update",
        status: "completed",
        locator: `codex://thread/thread-machine-id/turn/1/item/change-${index + 1}`,
      })),
      operations: manyOperations,
      command_count: manyOperations.length,
      validation_count: manyOperations.length,
    };
    const manyRelated = {
      ...related,
      total: 9,
      results: Array.from({ length: 9 }, (_, index) => ({
        ...related.results[0],
        entity_id: `code://symbol/rank-${index + 1}`,
        title: `rank-${index + 1}`,
        path: `src/module-${index + 1}.py`,
        subtitle: `src/module-${index + 1}.py`,
        locator: `rag@abcdef:src/module-${index + 1}.py#L10-L30`,
      })),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/turn-audit")
          ? longTurn
          : url.includes("/timeline")
            ? { ...timeline, file_change_count: 9, turns: [longTurn] }
            : url.includes("/v1/search")
              ? manyRelated
              : {
                  project: { settings: { workspace_path: "/workspace/rag" } },
                };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    const completedPager = await screen.findByRole("navigation", {
      name: "完成事项分页",
    });
    expect(screen.getAllByText("新增完成事项一组件").length).toBeGreaterThan(0);
    expect(screen.queryByText("新增完成事项五组件")).not.toBeInTheDocument();
    await user.click(
      within(completedPager).getByRole("button", { name: "下一页" }),
    );
    expect(screen.getByText("新增完成事项五组件")).toBeInTheDocument();

    const checkPager = screen.getByRole("navigation", {
      name: "验证与风险分页",
    });
    await user.click(
      within(checkPager).getByRole("button", { name: "下一页" }),
    );
    expect(
      screen.getByText("pytest tests/test_case_5.py -q"),
    ).toBeInTheDocument();

    const sourcePager = await screen.findByRole("navigation", {
      name: "关联来源分页",
    });
    await user.click(
      within(sourcePager).getByRole("button", { name: "下一页" }),
    );
    expect(screen.getAllByText("rank-5").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("tab", { name: /代码变更/ }));
    expect(
      screen.getByRole("navigation", { name: "代码变更分页" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("src/module-9.py")).not.toBeInTheDocument();
    await user.click(
      within(
        screen.getByRole("navigation", { name: "代码变更分页" }),
      ).getByRole("button", { name: "下一页" }),
    );
    expect(screen.getAllByText("src/module-9.py").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("tab", { name: /执行记录/ }));
    const operationPager = screen.getByRole("navigation", {
      name: "执行记录分页",
    });
    expect(
      screen.queryByRole("button", { name: /test_case_11/ }),
    ).not.toBeInTheDocument();
    await user.click(
      within(operationPager).getByRole("button", { name: "下一页" }),
    );
    expect(
      screen.getByRole("button", { name: /test_case_11/ }),
    ).toBeInTheDocument();
  });

  it("resets the selected turn and transient detail state when switching sessions", async () => {
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    const secondTurn = {
      ...timeline.turns[0],
      id: "codex://thread/thread-machine-id/turn/2",
      ordinal: 2,
      goal: "旧会话第二轮目标",
      locator: "codex://thread/thread-machine-id/turn/2",
    };
    const firstTimeline = {
      ...timeline,
      turns: [timeline.turns[0], secondTurn],
    };
    const nextTurn = {
      ...timeline.turns[0],
      id: "codex://thread/thread-two/turn/1",
      goal: "新会话第一轮目标",
      locator: "codex://thread/thread-two/turn/1",
    };
    const nextTimeline = {
      ...timeline,
      id: "codex://thread/thread-two",
      thread_id: "thread-two",
      title: "thread-019f9976-c2eb-72c1-89ea-683855ded0ff",
      turns: [nextTurn],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const activeTimeline = url.includes("thread-two")
          ? nextTimeline
          : firstTimeline;
        const payload = url.includes("/turn-audit")
          ? activeTimeline.turns[0]
          : url.includes("/timeline")
            ? activeTimeline
            : url.includes("/v1/search")
              ? related
              : { project: { settings: { workspace_path: "/workspace/rag" } } };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    try {
      const user = userEvent.setup();
      renderDetail();
      await screen.findByRole("option", { name: /轮次 2 · 旧会话第二轮目标/ });
      const turnSelect = screen.getByLabelText("选择轮次");
      await user.selectOptions(turnSelect, "1");
      expect(turnSelect).toHaveValue("1");

      await user.click(screen.getByRole("button", { name: "切换测试会话" }));

      expect(
        await screen.findByRole("heading", {
          level: 1,
          name: "新会话第一轮目标",
        }),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("选择轮次")).toHaveValue("0");
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/p/project-rag/sessions/thread-two",
      );
    } finally {
      Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
        configurable: true,
        value: originalScrollIntoView,
      });
    }
  });

  it("swipes the session chain exactly one turn while preserving the bottom scrollbar", async () => {
    const originalSetPointerCapture = HTMLElement.prototype.setPointerCapture;
    const originalHasPointerCapture = HTMLElement.prototype.hasPointerCapture;
    const originalReleasePointerCapture =
      HTMLElement.prototype.releasePointerCapture;
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    const capture = vi.fn();
    const release = vi.fn();
    const scrollIntoView = vi.fn();
    Object.defineProperties(HTMLElement.prototype, {
      setPointerCapture: { configurable: true, value: capture },
      hasPointerCapture: { configurable: true, value: () => true },
      releasePointerCapture: { configurable: true, value: release },
      scrollIntoView: { configurable: true, value: scrollIntoView },
    });
    const secondTurn = {
      ...timeline.turns[0],
      id: "codex://thread/thread-machine-id/turn/2",
      ordinal: 2,
      goal: "滑动后的相邻轮次",
      locator: "codex://thread/thread-machine-id/turn/2",
      operations: [],
    };
    const swipeTimeline = {
      ...timeline,
      turn_count: 2,
      turns: [{ ...timeline.turns[0], operations: [] }, secondTurn],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const requestedTurn = new URL(url, "http://test").searchParams.get(
          "turn_id",
        );
        const payload = url.includes("/turn-audit")
          ? swipeTimeline.turns.find((item) => item.id === requestedTurn) ||
            swipeTimeline.turns[0]
          : url.includes("/timeline")
            ? swipeTimeline
            : url.includes("/v1/search")
              ? related
              : {
                  project: { settings: { workspace_path: "/workspace/rag" } },
                };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
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

    try {
      renderDetail();
      const board = await screen.findByLabelText("可拖动的会话轮次白板");
      Object.defineProperty(board, "getBoundingClientRect", {
        configurable: true,
        value: () => ({
          x: 0,
          y: 0,
          top: 0,
          right: 900,
          bottom: 158,
          left: 0,
          width: 900,
          height: 158,
          toJSON: () => ({}),
        }),
      });
      const firstNode = await screen.findByRole("button", {
        name: /轮次 1：优化会话审计与关联来源跳转/,
      });
      fireEvent(
        firstNode,
        pointerEvent("pointerdown", {
          pointerId: 9,
          clientX: 220,
          clientY: 90,
        }),
      );
      expect(capture).not.toHaveBeenCalled();

      fireEvent(
        board,
        pointerEvent("pointermove", {
          pointerId: 9,
          clientX: 140,
          clientY: 90,
        }),
      );
      expect(capture).toHaveBeenCalledWith(9);
      expect(board.scrollLeft).toBe(80);
      expect(board).toHaveAttribute("data-dragging", "true");

      fireEvent(
        board,
        pointerEvent("pointerup", {
          pointerId: 9,
          clientX: 140,
          clientY: 90,
        }),
      );
      expect(release).toHaveBeenCalledWith(9);
      expect(board).toHaveAttribute("data-dragging", "true");
      expect(screen.getByLabelText("选择轮次")).toHaveValue("1");
      expect(screen.getByTestId("location")).toHaveTextContent(
        encodeURIComponent(secondTurn.id),
      );
      expect(scrollIntoView).toHaveBeenCalled();
      await waitFor(() => expect(board).not.toHaveAttribute("data-dragging"), {
        timeout: 600,
      });

      capture.mockClear();
      fireEvent(
        board,
        pointerEvent("pointerdown", {
          pointerId: 10,
          clientX: 200,
          clientY: 150,
        }),
      );
      fireEvent(
        board,
        pointerEvent("pointermove", {
          pointerId: 10,
          clientX: 80,
          clientY: 150,
        }),
      );
      expect(capture).not.toHaveBeenCalled();
      expect(screen.getByLabelText("选择轮次")).toHaveValue("1");
    } finally {
      Object.defineProperties(HTMLElement.prototype, {
        setPointerCapture: {
          configurable: true,
          value: originalSetPointerCapture,
        },
        hasPointerCapture: {
          configurable: true,
          value: originalHasPointerCapture,
        },
        releasePointerCapture: {
          configurable: true,
          value: originalReleasePointerCapture,
        },
        scrollIntoView: {
          configurable: true,
          value: originalScrollIntoView,
        },
      });
    }
  });

  it("loads long timelines in bounded pages instead of rendering every turn", async () => {
    const makeTurns = (start: number, count: number) =>
      Array.from({ length: count }, (_, index) => ({
        ...timeline.turns[0],
        id: `codex://thread/thread-machine-id/turn/${start + index}`,
        ordinal: start + index,
        goal: `分页轮次 ${start + index}`,
        operations: [],
      }));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const payload = url.includes("/turn-audit")
        ? {
            ...timeline.turns[0],
            id: new URL(url, "http://test").searchParams.get("turn_id"),
          }
        : url.includes("/timeline")
          ? {
              ...timeline,
              turn_count: 37,
              visible_turn_count: 18,
              turn_offset: url.includes("turn_offset=18") ? 18 : 0,
              turn_limit: 18,
              has_more_turns: !url.includes("turn_offset=36"),
              turns: url.includes("turn_offset=18")
                ? makeTurns(2, 18)
                : makeTurns(20, 18),
            }
          : url.includes("/v1/search")
            ? related
            : { project: { settings: { workspace_path: "/workspace/rag" } } };
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderDetail();

    expect((await screen.findAllByText("分页轮次 20")).length).toBeGreaterThan(
      0,
    );
    expect(
      screen.getByRole("navigation", { name: "会话轮次分页" }).textContent,
    ).toContain("当前加载 18/ 37 轮");
    expect(screen.queryByText("分页轮次 2")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /较早轮次/ }));
    expect((await screen.findAllByText("分页轮次 2")).length).toBeGreaterThan(
      0,
    );
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes("turn_offset=18"),
      ),
    ).toBe(true);
  });
});
