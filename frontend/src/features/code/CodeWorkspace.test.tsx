import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CodeWorkspace } from "./CodeWorkspace";

const repository = {
  id: "repository://rag",
  project_id: "project-rag",
  name: "evidence-rag",
  source_type: "local",
  source_url: null,
  local_path: "/Users/private/evidence-rag",
  default_branch: "main",
  head_commit: "abcdef1234567890",
  active_generation_id: "generation://code-7",
  status: "failed",
  updated_at: "2026-08-01T00:00:00Z",
  last_error: "api_key=must-not-render /Users/private/error",
  stats: { files: 24, symbols: 81, commits: 12 },
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={[
          "/p/project-rag/code?repository=repository%3A%2F%2Frag",
        ]}
      >
        <Routes>
          <Route path="/p/:projectId/code" element={<CodeWorkspace />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function codeApiResponse(input: RequestInfo | URL) {
  const url = String(input);
  if (url.startsWith("/v1/repositories")) return json([repository]);
  if (url.startsWith("/v1/code/refs")) {
    return json({
      repository_id: repository.id,
      head: { name: "main", sha: repository.head_commit, branch: "main" },
      head_sha: repository.head_commit,
      default_branch: "main",
      branches: [],
      observed_at: "2026-08-01T00:00:00Z",
    });
  }
  if (url.startsWith("/v1/code/files")) return json([]);
  if (url.startsWith("/v1/code/history")) return json([]);
  if (url.startsWith("/v1/ingestion/workflows")) return json([]);
  if (url.startsWith("/v1/graph?")) {
    return json({
      domain: "code",
      mode: "relations",
      query: "",
      nodes: [
        {
          id: repository.id,
          type: "Repository",
          label: repository.name,
          locator: repository.id,
          domain: "code",
          repository_id: repository.id,
        },
        {
          id: "file://workspace",
          type: "FileVersion",
          label: "CodeWorkspace.tsx",
          locator: "code://rag/CodeWorkspace.tsx",
          domain: "code",
          repository_id: repository.id,
          version: repository.head_commit,
          path: "frontend/src/features/code/CodeWorkspace.tsx",
        },
        {
          id: "symbol://workspace",
          type: "CodeSymbol",
          label: "CodeWorkspace",
          locator: "code://rag/CodeWorkspace.tsx#CodeWorkspace",
          domain: "code",
          repository_id: repository.id,
          version: repository.head_commit,
          path: "frontend/src/features/code/CodeWorkspace.tsx",
        },
      ],
      edges: [
        {
          id: "edge://defines",
          source: "file://workspace",
          predicate: "DEFINES",
          target: "symbol://workspace",
          domain: "code",
        },
      ],
      metadata: {
        total_nodes: 3,
        total_edges: 1,
        visible_nodes: 3,
        visible_edges: 1,
        sampled: false,
      },
    });
  }
  if (url.startsWith("/v1/graph/neighbors")) {
    return json({
      root: repository.id,
      cursor: 0,
      next_cursor: 0,
      total: 0,
      has_more: false,
      nodes: [],
      edges: [],
    });
  }
  return json([]);
}

describe("CodeWorkspace repository authority", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows repository, ref, index, generation, safe error and actions", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      codeApiResponse(input),
    );
    const user = userEvent.setup();

    renderWorkspace();

    const summary = await screen.findByRole("region", {
      name: "仓库索引权威状态",
    });
    expect(summary).toHaveTextContent("evidence-rag");
    expect(summary).toHaveTextContent("main · abcdef1234");
    expect(summary).toHaveTextContent("索引失败");
    expect(summary).toHaveTextContent("generation://code-7");
    expect(summary).toHaveTextContent("24 / 81");
    expect(summary).toHaveTextContent(
      "最近索引失败；详细错误未在仓库总览展开。",
    );
    expect(
      screen.queryByText(/must-not-render|\/Users\/private/),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "查看索引任务" }));
    expect(screen.getByText("解析与采集")).toBeInTheDocument();
  });

  it("does not expose repository write controls after an ACL failure", async () => {
    let repositoryReads = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (!String(input).startsWith("/v1/repositories")) {
        return codeApiResponse(input);
      }
      repositoryReads += 1;
      return repositoryReads === 1
        ? json({ detail: "token=secret /Users/private" }, 403)
        : json([repository]);
    });
    const user = userEvent.setup();

    renderWorkspace();

    expect(await screen.findByText("代码仓库不可用")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /导入仓库/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /移除仓库/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/token=secret|\/Users\/private/),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(repositoryReads).toBe(2));
    expect(
      await screen.findByRole("region", { name: "仓库索引权威状态" }),
    ).toBeInTheDocument();
  });

  it("opens the dedicated repository RAG graph with typed layers", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      codeApiResponse(input),
    );
    const user = userEvent.setup();
    renderWorkspace();

    await screen.findByRole("region", { name: "仓库索引权威状态" });
    await user.click(screen.getByRole("button", { name: "仓库图谱" }));

    expect(
      await screen.findByRole("region", { name: "Code 仓库 RAG 图谱" }),
    ).toBeInTheDocument();
    expect(screen.getByText("仓库知识结构")).toBeInTheDocument();
    expect(screen.getAllByText("文件版本").length).toBeGreaterThan(0);
    expect(screen.getAllByText("代码 Symbol").length).toBeGreaterThan(0);
    expect(screen.getByText("测试结果")).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "evidence-rag 代码仓库关系图谱" }),
    ).toBeInTheDocument();
    const workspace = screen
      .getByRole("region", { name: "Code 仓库 RAG 图谱" })
      .closest(".code-workspace");
    expect(workspace).toHaveClass("is-graph-mode");
    const networkNode = screen.getByRole("button", {
      name: "仓库：evidence-rag",
    });
    expect(
      networkNode.querySelector(".code-graph-network-node__body"),
    ).toBeInTheDocument();
    expect(
      networkNode.querySelector(".code-graph-card-node__body"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /树状/ }));
    const layeredNode = screen.getByRole("button", {
      name: "仓库：evidence-rag",
    });
    expect(
      layeredNode.querySelector(".code-graph-card-node__body"),
    ).toBeInTheDocument();
    expect(
      layeredNode.querySelector(".code-graph-network-node__body"),
    ).not.toBeInTheDocument();
  });

  it("supports network pan, node drag, zoom and stable click selection", async () => {
    vi.stubGlobal("PointerEvent", MouseEvent);
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      codeApiResponse(input),
    );
    const user = userEvent.setup();
    renderWorkspace();

    await screen.findByRole("region", { name: "仓库索引权威状态" });
    await user.click(screen.getByRole("button", { name: "仓库图谱" }));

    const svg = (await screen.findByRole("img", {
      name: "evidence-rag 代码仓库关系图谱",
    })) as unknown as SVGSVGElement;
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 1280,
      bottom: 820,
      width: 1280,
      height: 820,
      toJSON: () => ({}),
    });
    const node = screen.getByRole("button", { name: "仓库：evidence-rag" });
    const initialNodeTransform = node.getAttribute("transform");

    fireEvent.pointerDown(node, {
      pointerId: 1,
      clientX: 640,
      clientY: 410,
    });
    fireEvent.pointerMove(svg, {
      pointerId: 1,
      clientX: 710,
      clientY: 455,
    });
    fireEvent.pointerUp(svg, {
      pointerId: 1,
      clientX: 710,
      clientY: 455,
    });
    await waitFor(() =>
      expect(node.getAttribute("transform")).not.toBe(initialNodeTransform),
    );

    const world = screen.getByTestId("code-graph-world");
    const initialWorldTransform = world.getAttribute("transform");
    const background = screen.getByTestId("code-graph-background");
    fireEvent.pointerDown(background, {
      pointerId: 2,
      clientX: 100,
      clientY: 100,
    });
    fireEvent.pointerMove(svg, {
      pointerId: 2,
      clientX: 150,
      clientY: 130,
    });
    fireEvent.pointerUp(svg, {
      pointerId: 2,
      clientX: 150,
      clientY: 130,
    });
    await waitFor(() =>
      expect(world.getAttribute("transform")).not.toBe(initialWorldTransform),
    );

    const pannedTransform = world.getAttribute("transform");
    fireEvent.wheel(svg, {
      clientX: 640,
      clientY: 410,
      deltaY: -180,
    });
    await waitFor(() =>
      expect(world.getAttribute("transform")).not.toBe(pannedTransform),
    );

    fireEvent.pointerDown(node, {
      pointerId: 3,
      clientX: 710,
      clientY: 455,
    });
    fireEvent.pointerUp(svg, {
      pointerId: 3,
      clientX: 710,
      clientY: 455,
    });
    expect(
      await screen.findByLabelText("代码图谱节点详情"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /在全局图谱继续探索/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /网状/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("expands code neighbours inside the code workspace", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith("/v1/graph/neighbors")) {
        return json({
          root: "file://workspace",
          cursor: 0,
          next_cursor: 1,
          total: 1,
          has_more: false,
          nodes: [
            {
              id: "test://workspace",
              type: "TestResult",
              label: "CodeWorkspace interaction test",
              locator: "test://workspace",
              domain: "code",
              repository_id: repository.id,
              version: repository.head_commit,
            },
          ],
          edges: [
            {
              id: "edge://workspace-test",
              source: "file://workspace",
              predicate: "VALIDATED_BY",
              target: "test://workspace",
              domain: "code",
            },
          ],
        });
      }
      return codeApiResponse(input);
    });
    const user = userEvent.setup();
    renderWorkspace();

    await screen.findByRole("region", { name: "仓库索引权威状态" });
    await user.click(screen.getByRole("button", { name: "仓库图谱" }));
    await user.click(
      await screen.findByRole("button", {
        name: "文件版本：CodeWorkspace.tsx",
      }),
    );

    expect(
      await screen.findByRole("button", {
        name: "测试结果：CodeWorkspace interaction test",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("当前新增 1 个节点 / 邻域共 1"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /全局图谱/ }),
    ).not.toBeInTheDocument();
  });
});
