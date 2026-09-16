import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CodexBridgeWorkspace } from "./CodexBridgeWorkspace";

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/p/project-rag/codex"]}>
        <Routes>
          <Route
            path="/p/:projectId/codex"
            element={<CodexBridgeWorkspace />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CodexBridgeWorkspace", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/status")
          ? {
              project_id: "project-rag",
              mcp: {
                endpoint: "http://127.0.0.1:8000/mcp",
                transport: "streamable-http",
                status: "ready",
                authentication: "configured",
              },
              plugin: {
                installed: true,
                location: "configured",
                version: "0.1.0",
                name: "research-project-bridge",
              },
              codex_cli: { available: true, location: "available-on-host" },
              permissions: {
                read: "automatic",
                write: "confirmation-required",
                delete: "unavailable",
                sandbox: "read-only",
              },
              clients: [],
              capabilities: ["project_get_context", "execution_get"],
              capability_snapshots: [
                {
                  client_id: "client://one",
                  server_version: "0.1.0",
                  protocol_version: "2025-06-18",
                  capabilities: ["project_get_context", "execution_get"],
                  handshake_identity: {
                    authority: "authenticated_bearer_principal",
                    authenticated_client_id: "client://one",
                  },
                  observed_at: "2026-08-01T00:00:00Z",
                },
              ],
              sync: {
                status: "ready",
                reason: "indexed_sessions_available",
                source_status: "ready",
                indexed_sessions: 4,
                source_count: 1,
                active_workflow: false,
                last_sync_at: "2026-08-01T00:00:00Z",
                data_available: true,
              },
              availability: "ready",
              reason: "connected",
              last_connected_at: null,
            }
          : url.includes("/workspace/dashboard")
            ? { project: { settings: { workspace_path: "/workspace/rag" } } }
            : [];
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  it("turns an empty project into an actionable Codex execution flow", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    expect(await screen.findByText("从这里开始")).toBeInTheDocument();
    expect(screen.getByText("还没有 Codex 执行")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /实现一项代码改进/ }));
    expect(
      screen.getByRole("dialog", { name: "新建 Codex 执行" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("任务名称")).toHaveValue("实现一项代码改进");
    expect(
      (screen.getByLabelText("验收条件") as HTMLTextAreaElement).value,
    ).toContain("相关测试与类型检查通过");

    await user.click(screen.getByRole("button", { name: "下一步" }));
    expect(
      screen.getByRole("button", { name: /交互模式/ }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("工作区")).toHaveValue("/workspace/rag");
    expect(screen.getByLabelText("工作区")).toHaveAttribute("readonly");
    expect(
      within(screen.getByRole("dialog", { name: "新建 Codex 执行" })).getByRole(
        "button",
        { name: /^只读分析/ },
      ),
    ).toHaveClass("is-selected");
  });

  it("shows connection diagnostics with explicit permission boundaries", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await screen.findByText("从这里开始");
    await user.click(screen.getByRole("button", { name: "连接设置" }));

    expect(
      screen.getByRole("dialog", { name: "Codex 连接设置" }),
    ).toBeInTheDocument();
    expect(screen.getByText("MCP 身份验证")).toBeInTheDocument();
    expect(screen.getByText(/MCP 2025-06-18/)).toBeInTheDocument();
    expect(screen.getByText("project_get_context")).toBeInTheDocument();
    expect(screen.getByText("删除")).toBeInTheDocument();
    expect(screen.getAllByText("不可用").length).toBeGreaterThan(0);
  });

  it("does not advertise readiness or open execution controls before a handshake", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/status")
          ? {
              project_id: "project-rag",
              mcp: {
                endpoint: "http://127.0.0.1:8000/mcp",
                transport: "streamable-http",
                status: "ready",
                authentication: "configured",
              },
              plugin: {
                installed: true,
                location: "configured",
                version: "0.1.0",
                name: "research-project-bridge",
              },
              codex_cli: { available: true, location: "available-on-host" },
              permissions: {
                read: "automatic",
                write: "confirmation-required",
                delete: "unavailable",
                sandbox: "read-only",
              },
              clients: [],
              capabilities: ["project_get_context"],
              capability_snapshots: [],
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
              availability: "disconnected",
              reason: "handshake_not_observed",
              last_connected_at: null,
            }
          : url.includes("/workspace/dashboard")
            ? { project: { settings: { workspace_path: "/workspace/rag" } } }
            : [];
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    const user = userEvent.setup();
    renderWorkspace();

    expect(
      (await screen.findAllByText(/尚未观察到客户端握手/)).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("凭据已配置").length).toBeGreaterThan(0);
    expect(screen.queryByText("MCP Bridge已连接")).not.toBeInTheDocument();
    expect(
      screen
        .getAllByRole("button", { name: "新建执行" })
        .every((button) => button.hasAttribute("disabled")),
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: /实现一项代码改进/ }));
    expect(
      screen.queryByRole("dialog", { name: "新建 Codex 执行" }),
    ).not.toBeInTheDocument();
  });

  it("fails a stale capability snapshot closed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/status")
          ? {
              project_id: "project-rag",
              mcp: {
                endpoint: "http://127.0.0.1:8000/mcp",
                transport: "streamable-http",
                status: "ready",
                authentication: "configured",
              },
              plugin: {
                installed: true,
                location: "configured",
                version: "0.1.0",
                name: "research-project-bridge",
              },
              codex_cli: {
                available: true,
                location: "available-on-host",
              },
              permissions: {
                read: "automatic",
                write: "confirmation-required",
                delete: "unavailable",
                sandbox: "read-only",
              },
              clients: [],
              capabilities: ["must_not_render"],
              capability_snapshots: [
                {
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
              ],
              sync: {
                status: "ready",
                reason: "indexed_sessions_available",
                source_status: "ready",
                indexed_sessions: 1,
                source_count: 1,
                active_workflow: false,
                last_sync_at: "2026-08-01T00:00:00Z",
                data_available: true,
              },
              availability: "ready",
              reason: "capability_snapshot_stale",
              last_connected_at: "2020-01-01T00:00:00Z",
            }
          : url.includes("/workspace/dashboard")
            ? { project: { settings: { workspace_path: "/workspace/rag" } } }
            : [];
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const user = userEvent.setup();

    renderWorkspace();

    expect(
      (await screen.findAllByText(/能力快照已过期，请重新握手/)).length,
    ).toBeGreaterThan(0);
    expect(
      screen
        .getAllByRole("button", { name: "新建执行" })
        .every((button) => button.hasAttribute("disabled")),
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: "连接设置" }));
    expect(screen.queryByText("must_not_render")).not.toBeInTheDocument();
    expect(screen.getByText(/当前不展示可用能力/)).toBeInTheDocument();
  });

  it("shows Core status fetch failures as unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/status")) {
          return new Response(JSON.stringify({ detail: "core unavailable" }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          });
        }
        const payload = url.includes("/workspace/dashboard")
          ? { project: { settings: { workspace_path: "/workspace/rag" } } }
          : [];
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    const user = userEvent.setup();
    renderWorkspace();

    await screen.findByText(/部分联动数据暂时不可用/);
    expect(screen.getByText("状态不可用")).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("button", { name: "新建执行" })
        .every((button) => button.hasAttribute("disabled")),
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: "连接设置" }));
    const dialog = screen.getByRole("dialog", { name: "Codex 连接设置" });
    const coreDiagnostic = within(dialog)
      .getByText("RAG Core API")
      .closest("article");

    expect(coreDiagnostic).not.toBeNull();
    expect(
      within(coreDiagnostic as HTMLElement).getByText("不可用"),
    ).toBeInTheDocument();
    expect(
      within(coreDiagnostic as HTMLElement).queryByText("正常"),
    ).not.toBeInTheDocument();
  });

  it("keeps approvals execution-scoped and renders errors, details, and the session link", async () => {
    const execution = {
      id: "codex-execution://one",
      project_id: "project-rag",
      work_item_id: "work://one",
      work_item_title: "修复桥接错误",
      work_item_key: "RAG-1",
      client_id: "client://one",
      thread_id: "thread-one",
      mode: "interactive",
      status: "failed",
      sandbox: "read-only",
      workspace_path: "/workspace/rag",
      base_ref: "HEAD",
      prompt: "fix",
      summary: "执行失败",
      validation: [{ name: "typecheck", status: "failed" }],
      changed_files: ["frontend/src/app/App.tsx"],
      context_snapshot: {},
      error: "Typecheck failed",
      version: 1,
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-01T00:01:00Z",
      events: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        let payload: unknown = [];
        if (url.includes("/status")) {
          payload = {
            project_id: "project-rag",
            mcp: {
              endpoint: "http://127.0.0.1:8000/mcp",
              transport: "streamable-http",
              status: "ready",
              authentication: "configured",
            },
            plugin: {
              installed: true,
              location: "configured",
              version: "0.1.0",
              name: "research-project-bridge",
            },
            codex_cli: { available: true, location: "available-on-host" },
            permissions: {
              read: "automatic",
              write: "confirmation-required",
              delete: "unavailable",
              sandbox: "read-only",
            },
            clients: [],
            capabilities: ["execution_get"],
            availability: "ready",
            reason: "connected",
            last_connected_at: "2026-08-01T00:00:00Z",
          };
        } else if (url.includes("/workspace/dashboard")) {
          payload = {
            project: { settings: { workspace_path: "/workspace/rag" } },
          };
        } else if (url.includes("/approvals")) {
          payload = [
            {
              id: "approval://one",
              project_id: "project-rag",
              execution_id: execution.id,
              action: "execution.start",
              status: "pending",
              requested_by: "Codex",
              payload: {},
              note: "",
              created_at: "2026-08-01T00:00:00Z",
            },
            {
              id: "approval://other",
              project_id: "project-rag",
              execution_id: "codex-execution://other",
              action: "work_item.complete",
              status: "pending",
              requested_by: "Codex",
              payload: {},
              note: "",
              created_at: "2026-08-01T00:00:00Z",
            },
          ];
        } else if (url.includes("/executions/by-id")) {
          payload = execution;
        } else if (url.includes("/executions")) {
          payload = [execution];
        }
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    renderWorkspace();

    expect(await screen.findByText("Typecheck failed")).toBeInTheDocument();
    expect(screen.getByText("frontend/src/app/App.tsx")).toBeInTheDocument();
    expect(screen.getByText("启动后台执行")).toBeInTheDocument();
    expect(screen.queryByText("确认任务完成")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看会话" })).toHaveAttribute(
      "href",
      "/p/project-rag/sessions/thread-one",
    );
    expect(
      screen.getByRole("link", { name: "在 Codex 中打开" }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("path=%2Fworkspace%2Frag"),
    );
  });
});
