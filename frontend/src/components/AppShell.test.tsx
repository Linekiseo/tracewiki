import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import type { Project } from "../lib/types";
import { AppShell } from "./AppShell";

const project: Project = {
  id: "project-rag",
  name: "Evidence RAG",
  description: "可信研发证据",
  owner: "RAG Core",
  acl_ref: "project:project-rag",
  classification: "internal",
  status: "active",
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-24T10:00:00Z",
  topic_count: 2,
  active_iteration_count: 1,
  settings: { workspace_path: "/workspace/authorized-rag" },
};

function renderShell() {
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    },
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/p/project-rag/overview"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route
              path="/p/:projectId/overview"
              element={<p>project body</p>}
            />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppShell Codex launch authority", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("keeps the project launch disabled without a real bridge handshake", async () => {
    vi.spyOn(api.projects, "list").mockResolvedValue([project]);
    vi.spyOn(api.codexBridge, "status").mockResolvedValue({
      project_id: project.id,
      availability: "disconnected",
      reason: "handshake_not_observed",
    } as Awaited<ReturnType<typeof api.codexBridge.status>>);

    renderShell();

    const launch = await screen.findByRole("button", { name: "交给 Codex" });
    await waitFor(() => expect(launch).toBeDisabled());
    expect(launch).toHaveAttribute(
      "title",
      "Codex 尚未连接，或项目工作区未配置",
    );
  });

  it("enables launch only when the bridge and project workspace are ready", async () => {
    vi.spyOn(api.projects, "list").mockResolvedValue([project]);
    vi.spyOn(api.codexBridge, "status").mockResolvedValue({
      project_id: project.id,
      availability: "ready",
      reason: "connected",
    } as Awaited<ReturnType<typeof api.codexBridge.status>>);

    renderShell();

    const launch = await screen.findByRole("button", { name: "交给 Codex" });
    await waitFor(() => expect(launch).toBeEnabled());
  });
});
