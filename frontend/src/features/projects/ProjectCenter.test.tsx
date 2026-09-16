import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectCenter } from "./ProjectCenter";

const project = {
  id: "project-research",
  name: "协同强化学习",
  description: "验证多智能体奖励设计",
  owner: "RAG Core",
  acl_ref: "project:project-research",
  classification: "internal",
  status: "active",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-24T10:00:00Z",
  last_activity_at: "2026-07-24T10:00:00Z",
  topic_count: 2,
  active_iteration_count: 1,
  active_work_item_count: 3,
  current_topic: "奖励设计能否提升协作成功率？",
  repositories: 2,
  sessions: 14,
  experiments: 3,
  documents: 8,
  pending_reviews: 4,
  settings: {},
};

function renderCenter() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ProjectCenter />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectCenter", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify([project]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })),
    );
  });

  it("renders project data and opens the non-modal inspector", async () => {
    const user = userEvent.setup();
    renderCenter();

    expect((await screen.findAllByText("奖励设计能否提升协作成功率？")).length).toBeGreaterThan(0);
    await user.click(screen.getByLabelText("查看 协同强化学习 详情"));

    expect(screen.getByLabelText("协同强化学习详情")).toBeInTheDocument();
    expect(screen.getByText("条关系待复核")).toBeInTheDocument();
  });

  it("filters projects without exposing editable table fields", async () => {
    const user = userEvent.setup();
    renderCenter();
    expect((await screen.findAllByText("协同强化学习")).length).toBeGreaterThan(0);

    await user.type(screen.getByLabelText("查找项目"), "不存在");
    expect(screen.getByText("没有匹配的项目")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "项目名称" })).not.toBeInTheDocument();
  });
});
