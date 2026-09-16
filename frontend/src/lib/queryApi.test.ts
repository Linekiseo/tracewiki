import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("trusted query API", () => {
  it("maps the interaction contract and sends per-source engine headers", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ query_id: "query://one", answer: {} }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await api.query.run({
      question: "Which implementation is current?",
      project_id: "project-rag",
      interaction_id: "turn-2",
      parent_interaction_id: "turn-1",
      conversation_id: "conversation-1",
      context_revision: 2,
      latency_budget_ms: 8_000,
      allow_generation: false,
      repository_ids: ["repo-rag"],
      commit: "abc123",
      as_of: "2026-08-01T00:00:00Z",
      intent: "change_trace",
      global_engine: "v2",
      engine_overrides: {
        code: "v2",
        experiment: "v1",
      },
    });

    const [input, init] = fetchSpy.mock.calls[0];
    const body = JSON.parse(String(init?.body));
    const headers = new Headers(init?.headers);

    expect(input).toBe("/v1/query");
    expect(body).toMatchObject({
      question: "Which implementation is current?",
      scope: {
        project_id: "project-rag",
        repository_ids: ["repo-rag"],
        commit: "abc123",
      },
      as_of: "2026-08-01T00:00:00Z",
      intent: "change_trace",
      mode: "evidence",
      client_turn_id: "turn-2",
      parent_turn_id: "turn-1",
      conversation_id: "conversation-1",
      context_revision: 2,
      deadline_ms: 8_000,
      answer_format: "evidence_only",
    });
    expect(body).not.toHaveProperty("interaction_id");
    expect(body).not.toHaveProperty("parent_interaction_id");
    expect(body).not.toHaveProperty("latency_budget_ms");
    expect(body).not.toHaveProperty("allow_generation");
    expect(headers.get("X-RAG-Engine")).toBe("v2");
    expect(headers.get("X-RAG-Code-Engine")).toBe("v2");
    expect(headers.get("X-RAG-Experiment-Engine")).toBe("v1");
  });

  it("previews a plan without engine execution headers and loads project history", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            contract_version: "query-plan-preview-v1",
            project_id: "project-rag",
            resolved_scope: { project_id: "project-rag" },
            intent: "global_synthesis",
            intent_signals: [],
            source_waves: [],
            required_roles: [],
            budgets: {
              max_evidence: 12,
              max_context_tokens: 0,
              deadline_ms: 15_000,
              per_source: {},
            },
            generation: {
              requested: true,
              policy: "grounded_only",
              answer_format: "concise",
            },
            plan_digest: "sha256:plan",
            retrieval_performed: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            contract_version: "query-history-v1",
            project_id: "project-rag",
            items: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

    const preview = await api.query.preview({
      question: "What changed?",
      project_id: "project-rag",
      sources: ["code", "codex"],
    });
    const history = await api.query.history("project-rag", 8);

    expect(preview.retrieval_performed).toBe(false);
    expect(history.items).toEqual([]);
    expect(fetchSpy.mock.calls[0][0]).toBe("/v1/query/preview");
    expect(fetchSpy.mock.calls[1][0]).toBe(
      "/v1/query/history?project_id=project-rag&limit=8",
    );
    expect(
      new Headers(fetchSpy.mock.calls[0][1]?.headers).get("X-RAG-Engine"),
    ).toBeNull();
  });

  it("never exposes raw backend detail for query failures", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail:
            "ACL project:secret failed at /Users/private/keys.env token=top-secret",
        }),
        {
          status: 502,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await expect(
      api.query.run({
        question: "show the answer",
        project_id: "project-rag",
      }),
    ).rejects.toThrow("查询服务暂时不可用，请稍后重试。");

    await expect(
      api.query.run({
        question: "show the answer",
        project_id: "project-rag",
      }),
    ).rejects.not.toThrow(/secret|ACL|Users|token/i);
  });
});
