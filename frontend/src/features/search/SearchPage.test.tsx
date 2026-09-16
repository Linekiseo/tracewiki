import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { Project, SearchResult } from "../../lib/types";
import { SearchPage } from "./SearchPage";

const project: Project = {
  id: "project-rag",
  name: "RAG 研究",
  description: "多来源证据项目",
  owner: "RAG Core",
  acl_ref: "project:project-rag",
  classification: "internal",
  status: "active",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-31T10:00:00Z",
  topic_count: 1,
  active_iteration_count: 1,
  settings: {},
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderSearchPage(results: SearchResult[]) {
  vi.spyOn(api.projects, "list").mockResolvedValue([project]);
  vi.spyOn(api.search, "project").mockResolvedValue({
    query_id: "search://source-gate",
    total: results.length,
    results,
    trace: { sources: {} },
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter
        initialEntries={["/search?view=evidence&project=project-rag&q=source"]}
      >
        <SearchPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SearchPage source presentation", () => {
  it("renders notebook as its own source label and icon instead of workspace", async () => {
    renderSearchPage([
      {
        entity_id: "notebook://analysis",
        source: "notebook",
        title: "Notebook 设计记录",
        locator: "notebook://analysis#cell-4",
        snippet: "比较两组实验输出。",
      },
      {
        entity_id: "workspace://task",
        source: "workspace",
        title: "工作区研究任务",
        locator: "workspace://task",
        snippet: "整理下一步研究工作。",
      },
    ]);

    const notebookHeader = (await screen.findByText("Notebook")).closest("header");
    const workspaceHeader = screen.getByText("项目工作").closest("header");

    expect(notebookHeader).toHaveTextContent("Notebook");
    expect(notebookHeader?.querySelector(".lucide-notebook-tabs")).toBeInTheDocument();
    expect(notebookHeader?.querySelector(".lucide-book-open")).not.toBeInTheDocument();
    expect(workspaceHeader?.querySelector(".lucide-book-open")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Notebook 设计记录/ }));
    expect(screen.getAllByText("Notebook")).toHaveLength(2);
  });

  it("fails closed for an unknown source while retaining its raw source string", async () => {
    const rawSource = "future_retriever_v3";
    renderSearchPage([
      {
        entity_id: "future://result",
        source: rawSource,
        title: "未来来源结果",
        locator: "future://result#1",
        snippet: "这条结果来自前端尚未识别的检索器。",
        status: "VERIFIED",
      },
    ]);

    const label = `未知来源 · ${rawSource}`;
    const unknownHeader = (await screen.findByText(label)).closest("header");
    const unknownGroup = unknownHeader?.closest("section");

    expect(unknownGroup).toHaveAttribute("data-source", rawSource);
    expect(unknownHeader?.querySelector(".lucide-circle-help")).toBeInTheDocument();
    expect(unknownHeader?.querySelector(".lucide-book-open")).not.toBeInTheDocument();
    expect(screen.getByText("来源未识别")).toBeVisible();
    expect(screen.queryByText("VERIFIED")).not.toBeInTheDocument();
    expect(screen.queryByText("项目工作")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /未来来源结果/ }));
    expect(screen.getAllByText(label)).toHaveLength(2);
  });
});
