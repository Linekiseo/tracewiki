import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiRequestError } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("API contracts", () => {
  it("reads the RAG status from the fixed read-only endpoint", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("service unavailable", { status: 503 }));

    await expect(api.rag.status()).rejects.toMatchObject({
      name: "ApiRequestError",
      status: 503,
    } satisfies Partial<ApiRequestError>);

    const [input, init] = fetchSpy.mock.calls[0];
    expect(String(input)).toBe("/v1/rag/status");
    expect(init?.method).toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("sends the binding identifier expected by the review endpoint", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "binding://one",
          review_status: "confirmed",
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await api.evidence.reviewBinding("binding://one", "confirmed", "verified");

    const [input, init] = fetchSpy.mock.calls[0];
    expect(String(input)).toContain("binding_id=binding%3A%2F%2Fone");
    expect(String(input)).not.toContain("candidate_id=");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      decision: "confirmed",
      note: "verified",
    });
  });

  it("keeps repository import minimal and lets the system infer the branch", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ workflow_id: "workflow://one", status: "queued" }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await api.code.ingestRepository("project-rag", "/workspace/research");

    const [, init] = fetchSpy.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toMatchObject({
      project_id: "project-rag",
      source: "/workspace/research",
      branch: null,
    });
  });

  it("passes the requested graph size through without an artificial client ceiling", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          domain: "overview",
          mode: "relations",
          query: "",
          nodes: [],
          edges: [],
          metadata: {
            total_nodes: 60776,
            total_edges: 103769,
            visible_nodes: 0,
            visible_edges: 0,
          },
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await api.graph.snapshot("project-rag", 60776);

    expect(String(fetchSpy.mock.calls[0][0])).toContain("limit=500");
  });

  it("reads a bounded project and client scoped agent audit without a request body", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          project_id: "project-rag",
          client_id: "client://one",
          items: [],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await api.codexBridge.agentAudit("project-rag", "client://one", 25);

    const [input, init] = fetchSpy.mock.calls[0];
    expect(String(input)).toBe(
      "/v1/codex-bridge/agents/audit?project_id=project-rag&limit=25&client_id=client%3A%2F%2Fone",
    );
    expect(init?.method).toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("requests all session kinds at the largest supported page size", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.sessions.list("project-rag");

    expect(String(fetchSpy.mock.calls[0][0])).toBe(
      "/v1/codex/sessions?project_id=project-rag&query=&include_subagents=true&limit=500",
    );
  });

  it("discovers and synchronizes archived project sessions within the server bound", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );

    await api.sessions.syncStatus("project-rag");
    expect(String(fetchSpy.mock.calls[0][0])).toBe(
      "/v1/codex/projects/project-rag/sync-status?include_archived=true&max_sessions=2000",
    );

    await api.sessions.sync("project-rag", true);
    const [input, init] = fetchSpy.mock.calls[1];
    expect(String(input)).toBe("/v1/codex/projects/project-rag/sync");
    expect(JSON.parse(String(init?.body))).toEqual({
      force: true,
      include_archived: true,
      max_sessions: 2000,
    });
  });
});
