import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiRequestError } from "../../lib/api";
import type { RagStatusResponse } from "../../lib/types";
import { RagOpsPage } from "./RagOpsPage";

function renderPage(path = "/ops") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/ops" element={<RagOpsPage />} />
          <Route path="/p/:projectId/ops" element={<RagOpsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RagOpsPage", () => {
  it("shows an honest 503 unavailable state without control-plane actions", async () => {
    vi.spyOn(api.rag, "status").mockRejectedValue(
      new ApiRequestError("status unavailable", 503),
    );

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "UNAVAILABLE · 503" }),
    ).toBeVisible();
    expect(screen.getByText(/无法确认引擎、发布、来源或性能状态/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /切换|发布|回滚/ })).not.toBeInTheDocument();
    expect(screen.getByText(/未选择项目/)).toBeVisible();
  });

  it("renders seven independent read-only switch states in project context", async () => {
    const response: RagStatusResponse = {
      schema_version: "rag-ops-status-v1",
      generated_at: new Date().toISOString(),
      stale: false,
      default_engine: "v1",
      quality_hold: true,
      canonical_release: {
        decision: "quality_hold",
        authority_id: "rag-canonical-production-release-authority-v2",
        authority_ready: false,
        authority_attested: false,
        integrity_sha256: `sha256:${"d".repeat(64)}`,
      },
      component_switches: {
        generator_prompt: true,
        context_packer: false,
        fusion: true,
        reranker: false,
        embedding_generation: true,
        source_retrievers: false,
        planner: true,
      },
      reviewed_calibration: { available: false },
    };
    vi.spyOn(api.rag, "status").mockResolvedValue(response);

    renderPage("/p/project-rag/ops");

    expect(await screen.findByRole("heading", { name: "七组件开关 · 只读" })).toBeVisible();
    expect(screen.getByText("生成器 Prompt")).toBeVisible();
    expect(screen.getByText("上下文打包")).toBeVisible();
    expect(screen.getByText("多源融合")).toBeVisible();
    expect(screen.getByText("重排序")).toBeVisible();
    expect(screen.getByText("Embedding 生成")).toBeVisible();
    expect(screen.getByText("来源检索器")).toBeVisible();
    expect(screen.getByText("查询规划器")).toBeVisible();
    expect(screen.getByText(/已选择项目作为导航上下文/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /切换|发布|回滚/ })).not.toBeInTheDocument();
  });
});
