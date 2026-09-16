import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResearchMap } from "./ResearchMap";

const dashboard = {
  project: {
    id: "project-rag",
    name: "RAG 项目",
    description: "多源研究项目",
    owner: "researcher",
    acl_ref: "project:project-rag",
    classification: "internal",
    status: "active",
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    topic_count: 0,
    active_iteration_count: 0,
    settings: {},
  },
  stats: {
    repositories: 0,
    sessions: 2,
    relations: 1,
    pending_reviews: 1,
    topics: 0,
    active_topics: 0,
    iterations: 0,
    active_iterations: 0,
    linked_evidence: 0,
    work_items: 0,
    active_work_items: 0,
  },
  recent_topics: [],
  active_iterations: [],
  active_work_items: [],
  recent_activity: [],
};

const graph = {
  domain: "overview",
  mode: "relations",
  query: "",
  nodes: [
    {
      id: "codex://thread/thread-one",
      type: "CodexThread",
      label: "解析检索计划",
      locator: "codex://thread/thread-one",
      domain: "codex",
      version: "2026-08-01T01:00:00Z",
    },
    {
      id: "codex://thread/thread-two",
      type: "CodexThread",
      label: "验证关系图谱",
      locator: "codex://thread/thread-two",
      domain: "codex",
      version: "2026-08-01T02:00:00Z",
    },
  ],
  edges: [
    {
      id: "relation://thread-one/thread-two",
      source: "codex://thread/thread-one",
      predicate: "continues_to",
      target: "codex://thread/thread-two",
      derivation: "session_timeline",
      confidence: 0.91,
      review_status: "unreviewed",
      domain: "codex",
    },
  ],
  metadata: {
    total_nodes: 2,
    total_edges: 1,
    visible_nodes: 2,
    visible_edges: 1,
    sampled: false,
  },
};

const sessions = [
  {
    id: "session-one",
    thread_id: "thread-one",
    project_id: "project-rag",
    title: "解析检索计划",
    cwd: "/workspace/rag",
    status: "completed",
    started_at: "2026-08-01T00:30:00Z",
    updated_at: "2026-08-01T01:00:00Z",
    turn_count: 8,
    item_count: 24,
    file_change_count: 3,
    command_count: 5,
    metadata: {},
  },
  {
    id: "session-two",
    thread_id: "thread-two",
    project_id: "project-rag",
    title: "验证关系图谱",
    cwd: "/workspace/rag",
    status: "active",
    started_at: "2026-08-01T01:30:00Z",
    updated_at: "2026-08-01T02:00:00Z",
    turn_count: 4,
    item_count: 11,
    file_change_count: 0,
    command_count: 2,
    metadata: {},
  },
];

const bindingCandidate = {
  id: "binding-one",
  project_id: "project-rag",
  source_title: "解析检索计划",
  source_thread_id: "thread-one",
  source_item_type: "FileChange",
  changed_path: "src/retrieval.py",
  repository_id: "repository-rag",
  repository_name: "rag",
  target_entity_id: "code://file/repository-rag/src/retrieval.py",
  target_path: "src/retrieval.py",
  target_commit_sha: "a".repeat(40),
  derivation: "path_exact",
  confidence: 0.98,
  review_status: "unreviewed",
  created_at: "2026-08-01T00:00:00Z",
  source: {
    content: "Changed retrieval planning and source routing.",
    locator: "codex://thread/thread-one",
    metadata: { acl_ref: "project:project-rag" },
  },
  target: {
    content: "def plan_retrieval(): ...",
    source_uri: "code://file/repository-rag/src/retrieval.py",
  },
};

let bindingPayload = [bindingCandidate];

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="focus-location">
      {new URLSearchParams(location.search).get("focus") || "none"}
    </output>
  );
}

function renderMap(initialEntry = "/p/project-rag/map?board=codex") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route
            path="/p/:projectId/map"
            element={
              <>
                <ResearchMap />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ResearchMap visible review navigation", () => {
  beforeEach(() => {
    bindingPayload = [bindingCandidate];
    Object.defineProperties(HTMLElement.prototype, {
      setPointerCapture: {
        configurable: true,
        value: vi.fn(),
      },
      hasPointerCapture: {
        configurable: true,
        value: vi.fn(() => true),
      },
      releasePointerCapture: {
        configurable: true,
        value: vi.fn(),
      },
      scrollTo: {
        configurable: true,
        value: vi.fn(),
      },
    });
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/dashboard")
          ? dashboard
          : url.includes("/v1/codex/sessions")
            ? sessions
            : url.includes("/v1/bindings/candidates")
              ? bindingPayload
              : graph;
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    delete (HTMLElement.prototype as Partial<HTMLElement>).setPointerCapture;
    delete (HTMLElement.prototype as Partial<HTMLElement>).hasPointerCapture;
    delete (HTMLElement.prototype as Partial<HTMLElement>)
      .releasePointerCapture;
    delete (HTMLElement.prototype as Partial<HTMLElement>).scrollTo;
  });

  it("shows a selectable paged session impact overview", async () => {
    const user = userEvent.setup();
    renderMap();

    expect(
      await screen.findByRole("button", { name: "会话 解析检索计划" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "会话 验证关系图谱" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "选择会话" })).toBeEnabled();
    expect(screen.getByText("产生代码变更")).toBeInTheDocument();
    expect(screen.getByText("正在进行")).toBeInTheDocument();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "选择会话" }),
      "thread-one",
    );
    expect(
      await screen.findByRole("heading", { level: 1, name: "解析检索计划" }),
    ).toBeInTheDocument();
  });

  it("exposes explicit session-to-code impact mappings on the overview", async () => {
    const user = userEvent.setup();
    renderMap("/p/project-rag/map");

    const mapping = await screen.findByLabelText("会话与代码影响映射");
    expect(mapping).toHaveTextContent("会话 → 代码影响映射");
    await user.click(
      within(mapping).getByRole("button", {
        name: "展开会话代码影响映射",
      }),
    );
    const route = within(mapping).getByRole("button", {
      name: /影响 src\/retrieval\.py/,
    });
    expect(route).toHaveTextContent("变更映射");

    await user.click(route);
    expect(
      screen.getByRole("complementary", { name: /详情/ }),
    ).toBeInTheDocument();
  });

  it("persists a selected session node in the URL and exposes its review trail", async () => {
    const user = userEvent.setup();
    renderMap("/p/project-rag/map?board=codex&thread=thread-one");

    await user.click(
      await screen.findByRole("button", { name: "会话 解析检索计划" }),
    );
    expect(screen.getByTestId("focus-location")).toHaveTextContent(
      "codex://thread/thread-one",
    );
    expect(
      screen.getByRole("navigation", { name: "图谱审阅路径" }),
    ).toHaveTextContent("解析检索计划");
    expect(
      screen.getByRole("region", { name: "证据与权限摘要" }),
    ).toHaveTextContent("project:project-rag");
    expect(
      screen.getByRole("region", { name: "证据与权限摘要" }),
    ).toHaveTextContent("内容摘要未提供");
    expect(
      screen.queryByRole("link", { name: "复核关系" }),
    ).not.toBeInTheDocument();

    expect(
      screen.getByRole("heading", { level: 1, name: "解析检索计划" }),
    ).toBeInTheDocument();
  });

  it("shows source-to-target routes and returns to the previous focus", async () => {
    const user = userEvent.setup();
    renderMap("/p/project-rag/map?board=codex&thread=thread-one");

    await user.click(
      await screen.findByRole("button", { name: "会话 解析检索计划" }),
    );
    const rail = screen.getByRole("complementary", {
      name: "审阅路径与关系过滤",
    });
    expect(rail).toHaveTextContent("当前焦点");
    expect(rail).toHaveTextContent("解析检索计划");
    expect(
      within(rail).getByRole("button", {
        name: /解析检索计划 到 retrieval\.py/,
      }),
    ).toBeInTheDocument();

    await user.click(
      within(rail).getByRole("button", {
        name: /解析检索计划 到 retrieval\.py/,
      }),
    );
    expect(screen.getByTestId("focus-location")).toHaveTextContent(
      "board://codex/file/src%2Fretrieval.py",
    );

    await user.click(
      within(
        screen.getByRole("complementary", { name: "审阅路径与关系过滤" }),
      ).getByRole("button", { name: "返回上一焦点" }),
    );
    expect(screen.getByTestId("focus-location")).toHaveTextContent(
      "codex://thread/thread-one",
    );
  });

  it("uses the hierarchy body as a focus-centered relationship whiteboard", async () => {
    const user = userEvent.setup();
    renderMap("/p/project-rag/map?board=codex&thread=thread-one");

    await user.click(
      await screen.findByRole("button", { name: "会话 解析检索计划" }),
    );
    await user.click(screen.getByRole("button", { name: "层级" }));

    const board = screen.getByRole("region", {
      name: "解析检索计划 为中心的完整层级与关联节点白板",
    });
    const canvas = within(board).getByTestId("context-relationship-canvas");
    const focusNode = canvas.querySelector<HTMLElement>(
      '[data-context-role="focus"]',
    );

    expect(board).toHaveClass("is-network");
    expect(canvas).toHaveTextContent("上级结构 / 传入");
    expect(canvas).toHaveTextContent("当前节点");
    expect(canvas).toHaveTextContent("下级实体 / 向外");
    expect(focusNode).not.toBeNull();
    expect(focusNode).toHaveStyle({ left: "50%", top: "50%" });
    expect(within(board).getByText("retrieval.py")).toBeInTheDocument();
    expect(
      within(board).getByRole("button", { name: "展开当前节点关联" }),
    ).toBeInTheDocument();

    await user.click(within(board).getByText("retrieval.py"));
    expect(screen.getByTestId("focus-location")).toHaveTextContent(
      "board://codex/file/src%2Fretrieval.py",
    );
  });

  it("does not deep-link a generic unreviewed graph edge as a binding candidate", async () => {
    bindingPayload = [];
    const user = userEvent.setup();
    renderMap("/p/project-rag/map?board=codex&thread=thread-one");

    await user.click(
      await screen.findByRole("button", { name: "会话 解析检索计划" }),
    );

    expect(
      screen.queryByRole("link", { name: "复核关系" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("complementary", { name: "审阅路径与关系过滤" }),
    ).toHaveTextContent("没有可安全打开的候选");
  });

  it("does not deep-link an ambiguous rollup that represents multiple candidates", async () => {
    bindingPayload = [
      bindingCandidate,
      {
        ...bindingCandidate,
        id: "binding-two",
        target_entity_id: "code://file/repository-rag/src/rerank.py",
        target_path: "src/rerank.py",
      },
    ];
    const user = userEvent.setup();
    renderMap("/p/project-rag/map?board=codex&thread=thread-one");

    await user.click(
      await screen.findByRole("button", { name: "会话 解析检索计划" }),
    );

    expect(
      screen.queryByRole("link", { name: "复核关系" }),
    ).not.toBeInTheDocument();
  });

  it("restores a focused node from a direct URL", async () => {
    renderMap(
      "/p/project-rag/map?board=codex&thread=thread-two&focus=codex%3A%2F%2Fthread%2Fthread-two",
    );

    expect(
      await screen.findByRole("complementary", { name: "验证关系图谱 详情" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("focus-location")).toHaveTextContent(
        "codex://thread/thread-two",
      ),
    );
  });

  it("explains when a deep-linked node is outside the loaded graph", async () => {
    renderMap(
      "/p/project-rag/map?board=codex&thread=thread-one&focus=codex%3A%2F%2Fthread%2Fmissing",
    );

    expect(
      await screen.findByText(
        "指定节点不在当前载入范围，请调整来源或载入量后重试",
      ),
    ).toBeInTheDocument();
  });
});
