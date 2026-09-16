import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "../../components/AppShell";
import { api } from "../../lib/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RAG operations navigation", () => {
  it("keeps the global read-only status route reachable without a project", async () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        getItem: () => null,
        setItem: () => undefined,
        removeItem: () => undefined,
      },
    });
    vi.spyOn(api.projects, "list").mockResolvedValue([]);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/ops"]}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/ops" element={<p>ops route body</p>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("ops route body")).toBeVisible();
    expect(screen.getByRole("link", { name: "RAG 运维状态" })).toHaveAttribute(
      "href",
      "/ops",
    );
    expect(screen.getByText("RAG 运维状态", { selector: ".project-context__module" }))
      .toBeVisible();
  });
});
