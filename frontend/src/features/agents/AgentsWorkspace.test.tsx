import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentControlPlaneItem } from "../../lib/types";
import { AgentsWorkspace } from "./AgentsWorkspace";

const baseAgent: AgentControlPlaneItem = {
  id: "agent://codex/client-one",
  client_id: "client://one",
  name: "Codex Desktop",
  connector: "codex-plugin",
  version: "0.1.0",
  availability: "disconnected",
  reason: "handshake_not_observed",
  capabilities: ["project_get_context"],
  scope: { project_id: "project-rag", token_scopes: ["read"] },
  last_seen_at: null,
  active_execution_count: 0,
  session_count: 0,
  recent_executions: [],
  sync: {
    status: "no_data",
    reason: "source_not_configured",
    source_status: "unconfigured",
    indexed_sessions: 0,
    source_count: 0,
    active_workflow: false,
    last_sync_at: null,
    data_available: false,
  },
  recent_events: [],
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
      <MemoryRouter initialEntries={["/p/project-rag/agents"]}>
        <Routes>
          <Route path="/p/:projectId/agents" element={<AgentsWorkspace />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AgentsWorkspace", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("does not present unverified advertised capabilities as discovered", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input: RequestInfo | URL) =>
        String(input).includes("/agents/audit")
          ? json({
              project_id: "project-rag",
              client_id: "client://one",
              items: [],
            })
          : json([baseAgent]),
      );

    renderWorkspace();

    expect(await screen.findByText("Codex Desktop")).toBeInTheDocument();
    expect(screen.getAllByText("未连接").length).toBeGreaterThan(0);
    expect(screen.getByText("尚未观察到客户端握手")).toBeInTheDocument();
    expect(screen.queryByText("project_get_context")).not.toBeInTheDocument();
    expect(
      screen.getByText("尚无已验证握手快照，不展示可用能力。"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("当前项目 ACL 范围内尚无审计事件。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /注册|签发|启动/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("如何接入并使用 Codex")).toBeInTheDocument();
    expect(screen.getByText("配置同一项目凭据")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "打开 Codex 联动配置" }),
    ).toHaveAttribute("href", "/p/project-rag/codex");
    expect(screen.getByRole("link", { name: "开始证据查询" })).toHaveAttribute(
      "href",
      "/p/project-rag/wiki",
    );
    expect(
      fetchSpy.mock.calls.some(([input]) =>
        String(input).includes("project_id=project-rag"),
      ),
    ).toBe(true);
  });

  it("renders only the verified snapshot and safe ACL audit summaries", async () => {
    const readyAgent: AgentControlPlaneItem = {
      ...baseAgent,
      availability: "ready",
      reason: "connected",
      last_seen_at: "2026-08-01T01:00:00Z",
      capability_snapshot: {
        client_id: "client://one",
        server_version: "0.1.0",
        protocol_version: "2025-06-18",
        capabilities: ["project_get_context", "execution_get"],
        handshake_identity: {
          authority: "authenticated_bearer_principal",
          authenticated_client_id: "client://one",
          reported_client: { name: "Codex Desktop", version: "1.2.3" },
        },
        observed_at: "2026-08-01T01:00:00Z",
      },
      sync: {
        status: "ready",
        reason: "indexed_sessions_available",
        source_status: "ready",
        indexed_sessions: 7,
        source_count: 1,
        active_workflow: false,
        last_sync_at: "2026-08-01T00:59:00Z",
        data_available: true,
      },
    };
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input: RequestInfo | URL) =>
        String(input).includes("/agents/audit")
          ? json({
              project_id: "project-rag",
              client_id: "client://one",
              items: [
                {
                  id: "audit://one",
                  client_id: "client://one",
                  event_type: "capability.discovery",
                  resource_type: "integration_client",
                  resource_id: "client://one",
                  detail: {
                    token: "must-not-render",
                    path: "/Users/private/workspace",
                  },
                  created_at: "2026-08-01T01:00:00Z",
                },
              ],
            })
          : json([readyAgent]),
      );

    renderWorkspace();

    expect(await screen.findByText("project_get_context")).toBeInTheDocument();
    expect(screen.getByText("execution_get")).toBeInTheDocument();
    expect(screen.getByText("2025-06-18 · 0.1.0")).toBeInTheDocument();
    expect(screen.getAllByText("数据可用").length).toBeGreaterThan(0);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(await screen.findByText("能力快照已发现")).toBeInTheDocument();
    expect(screen.queryByText("must-not-render")).not.toBeInTheDocument();
    expect(
      screen.queryByText("/Users/private/workspace"),
    ).not.toBeInTheDocument();
    expect(
      fetchSpy.mock.calls.some(([input]) =>
        String(input).includes(
          "project_id=project-rag&limit=20&client_id=client%3A%2F%2Fone",
        ),
      ),
    ).toBe(true);
  });

  it.each([
    ["syncing", "正在同步"],
    ["sync_failed", "同步失败"],
    ["no_data", "暂无数据"],
    ["ready", "数据可用"],
    ["unavailable", "状态不可用"],
  ] as const)("distinguishes the %s sync projection", async (status, label) => {
    const agent = {
      ...baseAgent,
      sync: {
        ...baseAgent.sync,
        status,
        data_available: status === "ready",
      },
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL) =>
        String(input).includes("/agents/audit")
          ? json({ project_id: "project-rag", items: [] })
          : json([agent]),
    );

    renderWorkspace();

    expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
  });

  it("fails a stale snapshot closed and asks for a new handshake", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL) =>
        String(input).includes("/agents/audit")
          ? json({ project_id: "project-rag", items: [] })
          : json([
              {
                ...baseAgent,
                availability: "ready",
                reason: "capability_snapshot_stale",
                capability_snapshot: {
                  client_id: "client://one",
                  server_version: "old-server",
                  protocol_version: "old-protocol",
                  capabilities: ["must_not_render"],
                  handshake_identity: {
                    authority: "authenticated_bearer_principal",
                    authenticated_client_id: "client://one",
                  },
                  observed_at: "2020-01-01T00:00:00Z",
                },
              },
            ]),
    );

    renderWorkspace();

    expect(
      await screen.findByText("能力快照已过期，请重新握手"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("未连接").length).toBeGreaterThan(0);
    expect(screen.queryByText("must_not_render")).not.toBeInTheDocument();
  });

  it("shows an audit error and retries the bounded read", async () => {
    let auditAttempts = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL) => {
        if (!String(input).includes("/agents/audit")) return json([baseAgent]);
        auditAttempts += 1;
        return auditAttempts === 1
          ? json({ detail: "secret backend failure" }, 503)
          : json({
              project_id: "project-rag",
              client_id: "client://one",
              items: [
                {
                  id: "audit://retry",
                  client_id: "client://one",
                  event_type: "mcp.handshake",
                  resource_type: "integration_client",
                  created_at: "2026-08-01T01:00:00Z",
                },
              ],
            });
      },
    );
    const user = userEvent.setup();

    renderWorkspace();

    expect(await screen.findByText(/审计事件不可读取/)).toBeInTheDocument();
    expect(
      screen.queryByText("secret backend failure"),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(auditAttempts).toBe(2));
    expect(await screen.findByText("MCP 握手已验证")).toBeInTheDocument();
  });
});
