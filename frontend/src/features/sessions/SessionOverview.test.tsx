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
import { afterEach, describe, expect, it, vi } from "vitest";
import { SessionOverview } from "./SessionWorkspace";

const firstSession = {
  id: "codex://thread/thread-one",
  thread_id: "thread-one",
  project_id: "project-rag",
  title: "实现会话证据审计",
  cwd: "/workspace/rag",
  status: "completed",
  started_at: "2026-07-25T08:00:00Z",
  updated_at: "2026-07-25T09:00:00Z",
  turn_count: 4,
  item_count: 20,
  file_change_count: 3,
  command_count: 5,
  metadata: {},
};

const secondSession = {
  ...firstSession,
  id: "codex://thread/thread-two",
  thread_id: "thread-two",
  title: "完善项目多会话动态加载",
  updated_at: "2026-07-26T09:00:00Z",
  turn_count: 2,
};

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname}</output>;
}

function renderOverview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/p/project-rag/sessions"]}>
        <Routes>
          <Route
            path="/p/:projectId/sessions"
            element={
              <>
                <SessionOverview />
                <LocationProbe />
              </>
            }
          />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SessionOverview project session discovery", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("automatically syncs newly discovered project sessions and opens either session", async () => {
    let syncRequested = false;
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        let payload: unknown;
        if (url.includes("/v1/codex/sessions?")) {
          payload = syncRequested
            ? [secondSession, firstSession]
            : [firstSession];
        } else if (url.includes("/sync-status")) {
          payload = {
            project_id: "project-rag",
            source_path: "/workspace/.codex",
            project_path: "/workspace/rag",
            source_status: "ready",
            last_indexed_at: "2026-07-25T09:00:00Z",
            discovered_sessions: 2,
            indexable_sessions: 2,
            indexed_sessions: syncRequested ? 2 : 1,
            hidden_subagent_sessions: 0,
            new_sessions: syncRequested ? 0 : 1,
            changed_sessions: 0,
            live_sessions: 0,
            removed_sessions: 0,
            oversized_sessions: 0,
            needs_sync: !syncRequested,
            active_workflow_id: null,
          };
        } else if (url.endsWith("/sync") && init?.method === "POST") {
          syncRequested = true;
          payload = {
            project_id: "project-rag",
            source_path: "/workspace/.codex",
            source_status: "ready",
            discovered_sessions: 2,
            indexable_sessions: 2,
            indexed_sessions: 1,
            hidden_subagent_sessions: 0,
            new_sessions: 1,
            changed_sessions: 0,
            live_sessions: 0,
            removed_sessions: 0,
            oversized_sessions: 0,
            needs_sync: true,
            status: "queued",
            workflow_id: "wf-codex-sync",
          };
        } else if (url.includes("/v1/ingestion/workflows/wf-codex-sync")) {
          payload = {
            id: "wf-codex-sync",
            status: "completed",
            stage: "complete",
            progress: 100,
            error: null,
            created_at: "2026-07-26T09:00:00Z",
            updated_at: "2026-07-26T09:00:01Z",
            counters: { sessions: 2 },
            kind: "codex",
          };
        } else {
          payload = {
            project: { settings: { workspace_path: "/workspace/rag" } },
          };
        }
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderOverview();

    expect(await screen.findByText("实现会话证据审计")).toBeInTheDocument();
    expect(
      await screen.findByText("完善项目多会话动态加载"),
    ).toBeInTheDocument();
    expect(screen.getByText("已载入 2 个会话")).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/v1/codex/projects/project-rag/sync",
        expect.objectContaining({ method: "POST" }),
      ),
    );

    await user.click(screen.getByText("完善项目多会话动态加载"));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/p/project-rag/sessions/thread-two",
    );
  });

  it("does not report zero sessions while the project list is still loading", async () => {
    let resolveSessions: ((response: Response) => void) | undefined;
    const sessionsResponse = new Promise<Response>((resolve) => {
      resolveSessions = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/v1/codex/sessions?")) return sessionsResponse;
        const payload = url.includes("/sync-status")
          ? {
              project_id: "project-rag",
              source_status: "ready",
              discovered_sessions: 1,
              indexable_sessions: 1,
              indexed_sessions: 1,
              hidden_subagent_sessions: 0,
              new_sessions: 0,
              changed_sessions: 0,
              live_sessions: 0,
              removed_sessions: 0,
              oversized_sessions: 0,
              needs_sync: false,
              active_workflow_id: null,
            }
          : { project: { settings: { workspace_path: "/workspace/rag" } } };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    renderOverview();

    expect(await screen.findByText("正在读取项目会话")).toBeInTheDocument();
    expect(screen.queryByText("已载入 0 个会话")).not.toBeInTheDocument();

    resolveSessions?.(
      new Response(JSON.stringify([firstSession]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(await screen.findByText("已载入 1 个会话")).toBeInTheDocument();
  });

  it("disables Codex launch until bridge availability and workspace authority agree", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/v1/codex-bridge/status")
          ? {
              project_id: "project-rag",
              availability: "disconnected",
              reason: "handshake_not_observed",
            }
          : url.includes("/v1/codex/sessions?")
            ? [firstSession]
            : url.includes("/sync-status")
              ? {
                  project_id: "project-rag",
                  source_status: "ready",
                  discovered_sessions: 1,
                  indexable_sessions: 1,
                  indexed_sessions: 1,
                  hidden_subagent_sessions: 0,
                  new_sessions: 0,
                  changed_sessions: 0,
                  live_sessions: 0,
                  removed_sessions: 0,
                  oversized_sessions: 0,
                  needs_sync: false,
                  active_workflow_id: null,
                }
              : {
                  project: {
                    settings: { workspace_path: "/workspace/authorized-rag" },
                  },
                };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    renderOverview();

    const launch = await screen.findByRole("button", { name: "新建会话" });
    expect(launch).toBeDisabled();
    expect(launch).toHaveAttribute(
      "title",
      "Codex 尚未连接，或项目工作区未配置",
    );
  });

  it("shows discovery gaps and keeps bound sessions in the default list", async () => {
    const boundSession = {
      ...firstSession,
      metadata: { execution_id: "codex-execution://bound" },
    };
    const subagentSession = {
      ...secondSession,
      id: "codex://thread/thread-subagent",
      thread_id: "thread-subagent",
      title: "核对类型边界子任务",
      metadata: {
        source: {
          subagent: {
            thread_spawn: {
              parent_thread_id: "thread-parent",
              depth: 1,
              agent_path: "/root/type-boundary-review",
              agent_nickname: "Reviewer",
            },
          },
        },
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/v1/codex/sessions?")
          ? [boundSession, secondSession, subagentSession]
          : url.includes("/sync-status")
            ? {
                project_id: "project-rag",
                source_status: "ready",
                discovered_sessions: 7,
                indexable_sessions: 5,
                indexed_sessions: 3,
                hidden_subagent_sessions: 1,
                new_sessions: 1,
                changed_sessions: 1,
                live_sessions: 0,
                removed_sessions: 1,
                oversized_sessions: 1,
                needs_sync: false,
                active_workflow_id: null,
                discovery: {
                  scan_complete: true,
                  project_match_complete: true,
                  complete: true,
                  visibility_state: "complete",
                  candidate_limit_applied: false,
                  requested_session_limit: 2000,
                  selected_session_limit: 2000,
                  jsonl_entries: 11,
                  candidate_sessions: 11,
                  active_candidates: 11,
                  archived_candidates: 0,
                  unreadable_entries: 0,
                  unsafe_symlink_entries: 0,
                  traversal_errors: 0,
                  matched_project_sessions: 7,
                  excluded_other_project_sessions: 2,
                  excluded_unbound_sessions: 1,
                  excluded_unreadable_sessions: 0,
                  excluded_oversized_sessions: 1,
                  sessions_beyond_selection_limit: 0,
                },
              }
            : url.includes("/v1/codex-bridge/status")
              ? {
                  project_id: "project-rag",
                  availability: "disconnected",
                  reason: "handshake_not_observed",
                }
              : {
                  project: {
                    settings: { workspace_path: "/workspace/rag" },
                  },
                };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const user = userEvent.setup();

    renderOverview();

    expect(await screen.findByText("实现会话证据审计")).toBeInTheDocument();
    expect(screen.getByText("完善项目多会话动态加载")).toBeInTheDocument();
    const summary = screen.getByRole("region", {
      name: "会话发现与同步覆盖",
    });
    const expectMetric = (label: string, value: string) => {
      const container = within(summary).getByText(label).closest("div");
      expect(container).not.toBeNull();
      expect(
        within(container as HTMLElement).getByText(value),
      ).toBeInTheDocument();
    };
    expectMetric("发现总数", "11");
    expectMetric("当前筛选", "3 / 3");
    expectMetric("明确过滤", "4");
    expectMetric("尚未索引", "2");
    expectMetric("同步缺口", "3");
    expect(
      screen.getByText("默认包含主会话、子任务及已关联会话"),
    ).toBeInTheDocument();
    expect(screen.getByText("核对类型边界子任务")).toBeInTheDocument();
    expect(screen.getByText("其中 1 个子任务")).toBeInTheDocument();
    expect(
      screen.getByText("子任务", { selector: ".session-subagent-mark" }),
    ).toBeInTheDocument();

    await user.type(
      screen.getByPlaceholderText("搜索会话目标或工作目录"),
      "完善",
    );
    expectMetric("当前筛选", "1 / 3");

    await user.clear(screen.getByPlaceholderText("搜索会话目标或工作目录"));
    await user.selectOptions(screen.getByLabelText("筛选会话类型"), "subagent");
    expectMetric("当前筛选", "1 / 3");
    expect(screen.getByText("核对类型边界子任务")).toBeInTheDocument();
    expect(screen.queryByText("实现会话证据审计")).not.toBeInTheDocument();
  });

  it("fails closed when the session list is unauthorized", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/v1/codex/sessions?")) {
          return new Response(
            JSON.stringify({ detail: "token=secret /Users/private" }),
            {
              status: 403,
              headers: { "Content-Type": "application/json" },
            },
          );
        }
        const payload = url.includes("/sync-status")
          ? {
              project_id: "project-rag",
              source_status: "ready",
              discovered_sessions: 1,
              indexable_sessions: 1,
              indexed_sessions: 1,
              hidden_subagent_sessions: 0,
              new_sessions: 0,
              changed_sessions: 0,
              live_sessions: 0,
              removed_sessions: 0,
              oversized_sessions: 0,
              needs_sync: false,
              active_workflow_id: null,
            }
          : url.includes("/v1/codex-bridge/status")
            ? {
                project_id: "project-rag",
                availability: "ready",
                reason: "connected",
              }
            : {
                project: {
                  settings: { workspace_path: "/workspace/rag" },
                },
              };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    renderOverview();

    expect(await screen.findByText("会话不可读取")).toBeInTheDocument();
    expect(
      screen.queryByText(/token=secret|\/Users\/private/),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建会话" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "同步会话" })).toBeDisabled();
  });

  it("pages long session lists and explains how to continue an oversized session", async () => {
    const manySessions = Array.from({ length: 45 }, (_, index) => ({
      ...firstSession,
      id: `codex://thread/thread-${index + 1}`,
      thread_id: `thread-${index + 1}`,
      title: `分页会话 ${String(index + 1).padStart(2, "0")}`,
    }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const payload = url.includes("/v1/codex/sessions?")
          ? manySessions
          : url.includes("/sync-status")
            ? {
                project_id: "project-rag",
                source_status: "ready",
                discovered_sessions: 46,
                indexable_sessions: 45,
                indexed_sessions: 45,
                hidden_subagent_sessions: 0,
                new_sessions: 0,
                changed_sessions: 0,
                live_sessions: 0,
                removed_sessions: 0,
                oversized_sessions: 1,
                max_session_bytes: 250_000_000,
                needs_sync: false,
                active_workflow_id: null,
              }
            : url.includes("/v1/codex-bridge/status")
              ? {
                  project_id: "project-rag",
                  availability: "ready",
                  reason: "connected",
                }
              : {
                  project: { settings: { workspace_path: "/workspace/rag" } },
                };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const user = userEvent.setup();

    renderOverview();

    expect(await screen.findByText("分页会话 01")).toBeInTheDocument();
    expect(screen.queryByText("分页会话 21")).not.toBeInTheDocument();
    expect(screen.getByText(/第 1 \/ 3 页/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /下一页/ }));
    expect(await screen.findByText("分页会话 21")).toBeInTheDocument();
    expect(screen.queryByText("分页会话 01")).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: /1 个超大会话待处理/ }),
    );
    expect(screen.getByText(/该会话已保留/)).toBeInTheDocument();
    expect(screen.getByText(/238 MB/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "新建延续会话" }),
    ).toBeInTheDocument();
  });
});
