import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectOverview } from "./ProjectOverview";

const dashboard = {
  project: {
    id: "project-rag",
    name: "Evidence RAG",
    description: "可信研发证据",
    owner: "RAG Core",
    acl_ref: "project:project-rag",
    classification: "internal",
    status: "active",
    created_at: "2026-07-20T10:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    topic_count: 0,
    active_iteration_count: 0,
    settings: {},
  },
  stats: {
    repositories: 1,
    sessions: 2,
    relations: 0,
    pending_reviews: 0,
    topics: 0,
    active_topics: 0,
    iterations: 0,
    active_iterations: 0,
    linked_evidence: 0,
    work_items: 0,
    active_work_items: 0,
  },
  active_iterations: [],
  active_work_items: [],
  recent_topics: [],
  recent_activity: [],
};

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
  last_error: "token=must-not-render /Users/private/error",
  stats: { files: 24, symbols: 81, commits: 12 },
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderOverview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/p/project-rag/overview"]}>
        <Routes>
          <Route path="/p/:projectId/overview" element={<ProjectOverview />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectOverview repository entry", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows repository ref, index, generation and a first-class action", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL) =>
        String(input).includes("/v1/repositories")
          ? json([repository])
          : json(dashboard),
    );

    renderOverview();

    expect(await screen.findByText("evidence-rag")).toBeInTheDocument();
    expect(screen.getByText("main · abcdef1234")).toBeInTheDocument();
    expect(screen.getByText("generation://code-7")).toBeInTheDocument();
    expect(screen.getByText("索引失败")).toBeInTheDocument();
    expect(
      screen.getByText("最近索引失败；详细错误未在项目总览展开。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/must-not-render|\/Users\/private/),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /检查仓库与索引/ }),
    ).toHaveAttribute(
      "href",
      "/p/project-rag/code?repository=repository%3A%2F%2Frag",
    );
    expect(
      screen.getByRole("link", { name: /打开代码工作区/ }),
    ).toHaveAttribute("href", "/p/project-rag/code");
  });

  it("fails closed on a repository ACL error and supports retry", async () => {
    let repositoryReads = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL) => {
        if (!String(input).includes("/v1/repositories")) return json(dashboard);
        repositoryReads += 1;
        return repositoryReads === 1
          ? json({ detail: "token=secret /Users/private" }, 403)
          : json([repository]);
      },
    );
    const user = userEvent.setup();

    renderOverview();

    expect(await screen.findByText(/仓库状态不可读取/)).toBeInTheDocument();
    expect(screen.queryByText("evidence-rag")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/token=secret|\/Users\/private/),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(repositoryReads).toBe(2));
    expect(await screen.findByText("evidence-rag")).toBeInTheDocument();
  });
});
