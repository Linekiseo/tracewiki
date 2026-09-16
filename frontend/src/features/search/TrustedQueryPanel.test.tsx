import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../../lib/types";
import { TrustedQueryPanel } from "./TrustedQueryPanel";

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
  settings: {},
};
const secondProject: Project = {
  ...project,
  id: "project-second",
  name: "Second Project",
  acl_ref: "project:project-second",
};

function response(payload: object) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function planPreview() {
  return {
    contract_version: "query-plan-preview-v1",
    project_id: project.id,
    resolved_scope: {
      project_id: project.id,
      repository_ids: [],
      source_types: ["code", "codex"],
    },
    intent: "change_trace",
    intent_signals: [],
    source_waves: [
      {
        wave: 1,
        execution_mode: "parallel",
        sources: ["code", "codex"],
        budgets: { code: 4000, codex: 4000 },
      },
    ],
    required_roles: ["current_code", "rationale"],
    budgets: {
      max_evidence: 12,
      max_context_tokens: 5000,
      deadline_ms: 15000,
      per_source: { code: 4000, codex: 4000 },
    },
    generation: {
      requested: true,
      policy: "grounded_only",
      answer_format: "concise",
    },
    plan_digest: "sha256:plan-safe",
    retrieval_performed: false,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("TrustedQueryPanel", () => {
  it("requires an explicit project and never selects the first project silently", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project, secondProject]} />);

    expect(screen.getByLabelText("可信查询项目")).toHaveValue("");
    await user.type(
      screen.getByLabelText("可信查询问题"),
      "Which project owns this?",
    );

    expect(screen.getByText("需要澄清：请选择项目")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /查询/ })).toBeDisabled();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("renders grounded trust signals, citations, counter evidence and next actions", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        query_id: "query://one",
        trace_id: "trace-safe",
        query_understanding: {
          contract_version: "query-understanding-hybrid-v2",
          strategy: "local_statistical",
          normalized_query: "What is current?",
          intent: "current_implementation",
          intent_confidence: 0.61,
          source_hints: ["code", "codex"],
          required_roles: ["current_code", "tests"],
          subqueries: ["What is current?", "current implementation tests"],
          entities: ["UnifiedQueryService"],
          temporal_scope: "current",
          ambiguity: [],
          fallback_reason: null,
          intent_distribution: {
            current_implementation: 0.61,
            change_trace: 0.24,
            global_synthesis: 0.15,
          },
          source_distribution: { code: 0.68, codex: 0.32 },
          algorithm: {
            version: "query-understanding-hybrid-v2",
            encoder: "frozen_tfidf_word_char_ngram",
            classifier: "calibrated_centroid_softmax",
            uncertainty: 0.52,
            complexity: 0.43,
            decomposition: "evidence_obligation_mmr",
            planner_model: null,
          },
          content_digest: "sha256:query-understanding-safe",
        },
        llm_provider: {
          contract_version: "llm-provider-runtime-v1",
          configured: false,
          provider: "openai_compatible",
        },
        state: "complete",
        interaction_state: "complete",
        reason_code: "evidence_sufficient",
        required_roles: ["current_code", "tests"],
        satisfied_roles: ["current_code"],
        missing_roles: ["tests"],
        corrective_rounds: 1,
        consistency_watermark: {
          index_generations: ["index:generation-7"],
          sources: { code: "commit:abc123" },
        },
        client_turn_id: "turn-safe",
        parent_turn_id: null,
        resolved_scope: {
          project_id: "project-rag",
          repositories: [{ id: "repo-one", name: "rag" }],
          commit: "abc123",
          acl_ref: "project:must-not-render",
        },
        answer: {
          status: "supported",
          answer_mode: "grounded_generation",
          text: "The current implementation is cited. [E1]",
          citations: ["E1"],
          claim_verification: {
            supported: true,
            claim_count: 1,
            supported_claim_count: 1,
            verifier_version: "claim-check-v2",
          },
        },
        source_status: {
          code: {
            status: "complete",
            candidate_count: 2,
            latency_ms: 12,
            watermark: "commit:abc123",
          },
          experiment: {
            status: "partial",
            candidate_count: 0,
            latency_ms: 14,
            watermark: null,
          },
        },
        evidence_pack: {
          verified_facts: [
            {
              entity_id: "code://one",
              source: "code",
              title: "Current implementation",
              locator: "src/current.ts:10",
              snippet: "export function current()",
            },
          ],
          counter_evidence: [
            {
              entity_id: "experiment://old",
              source: "experiment",
              title: "Older run disagrees",
              locator: "run://old",
              snippet: "The older metric moved in the opposite direction.",
            },
          ],
          missing_evidence: [{ role: "tests", reason: "private detail" }],
          role_coverage: {
            satisfied: ["current_code"],
            missing: ["tests"],
          },
          citation_map: {
            E1: {
              entity_id: "code://one",
              source: "code",
              locator: "src/current.ts:10",
              version: "abc123",
            },
          },
          next_actions: ["同步缺失的测试证据"],
          trace: {
            platform: {
              global_engine: {
                requested: "v2",
                selected: "v1",
                fallback: "legacy_v1",
                blockers: ["calibration_unavailable:code"],
              },
            },
          },
        },
        interaction: {
          state: "complete",
          answer_mode: "grounded_generation",
          reason: "evidence_sufficient",
          transitions: ["received", "retrieving", "complete"],
        },
      }),
    );
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(screen.getByLabelText("可信查询问题"), "What is current?");
    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText("GROUNDED")).toBeInTheDocument();
    expect(screen.getByText("本地统计理解")).toBeInTheDocument();
    expect(
      screen.getByText("TF-IDF 词/字符 n-gram × 质心 Softmax"),
    ).toBeInTheDocument();
    expect(screen.getByText(/不确定性 52% · 复杂度 43%/)).toBeInTheDocument();
    expect(screen.getAllByText("current_implementation").length).toBeGreaterThan(0);
    expect(screen.getByText("来源执行状态")).toBeInTheDocument();
    expect(screen.getByText("反证与冲突")).toBeInTheDocument();
    expect(screen.getByText("回答引用")).toBeInTheDocument();
    expect(screen.getByText("测试证据")).toBeInTheDocument();
    expect(screen.getByText("同步缺失的测试证据")).toBeInTheDocument();
    expect(screen.getByText("请求与实际执行引擎")).toBeInTheDocument();
    expect(screen.getByText(/Fallback · legacy_v1/)).toBeInTheDocument();
    expect(
      screen.getByText(/Blocker · calibration_unavailable:code/),
    ).toBeInTheDocument();
    expect(screen.getByText("交互审计链")).toBeInTheDocument();
    expect(screen.getByText("1 CORRECTIVE ROUNDS")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /跳转 E1/ })).toHaveAttribute(
      "href",
      "#citation-E1",
    );
    expect(
      screen.queryByText(/must-not-render|private detail/),
    ).not.toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const body = JSON.parse(String(fetchSpy.mock.calls[0][1]?.body));
    expect(body.scope.project_id).toBe(project.id);
    expect(body.include).toEqual([
      "code",
      "codex",
      "experiment",
      "notebook",
      "document",
      "workspace",
    ]);
  });

  it("renders a partial response without upgrading it to grounded", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        query_id: "query://partial",
        trace_id: "trace-partial",
        state: "partial",
        client_turn_id: "turn-partial",
        resolved_scope: { project_id: "project-rag" },
        answer: {
          status: "partial",
          answer_mode: "partial",
          text: "Only the available document evidence is shown.",
          citations: [],
        },
        source_status: {
          document: {
            status: "complete",
            candidate_count: 1,
            latency_ms: 8,
            watermark: "document:v2",
          },
          experiment: {
            status: "timeout",
            candidate_count: 0,
            latency_ms: 50,
            watermark: null,
          },
        },
        evidence_pack: {
          verified_facts: [],
          supporting_evidence: [
            {
              entity_id: "document://one",
              source: "document",
              title: "Available evidence",
              locator: "document://one#section-1",
              snippet: "Bounded supporting evidence.",
            },
          ],
          counter_evidence: [],
          missing_evidence: [{ role: "experiment" }],
          role_coverage: {
            satisfied: ["document"],
            missing: ["experiment"],
          },
          citation_map: {},
          next_actions: ["Retry the timed-out experiment source."],
        },
      }),
    );
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(
      screen.getByLabelText("可信查询问题"),
      "Compare the evidence.",
    );
    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText("PARTIAL")).toBeInTheDocument();
    expect(screen.getByText("超时")).toBeInTheDocument();
    expect(screen.getByText("实验结果")).toBeInTheDocument();
    expect(screen.queryByText("GROUNDED")).not.toBeInTheDocument();
  });

  it("renders only the sanitized client error for a backend failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail:
            "token=top-secret at /Users/private/keys.env ACL project:private",
        }),
        {
          status: 502,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(
      screen.getByLabelText("可信查询问题"),
      "Show safe failure.",
    );
    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText("查询失败")).toBeInTheDocument();
    expect(
      screen.getByText("查询服务暂时不可用，请稍后重试。"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/top-secret|Users\/private|project:private|ACL/i),
    ).not.toBeInTheDocument();
  });

  it("cancels an in-flight request without issuing a duplicate submission", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(
      screen.getByLabelText("可信查询问题"),
      "Long running query",
    );
    await user.click(screen.getByRole("button", { name: /查询/ }));
    await user.click(await screen.findByRole("button", { name: /取消/ }));

    expect(await screen.findByText("查询已取消")).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("resubmits a clarification as a complete question when no snapshot exists", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        response({
          query_id: "query://clarify",
          trace_id: "trace-clarify",
          state: "needs_clarification",
          client_turn_id: "turn-clarify",
          answer: {
            status: "needs_clarification",
            answer_mode: "needs_clarification",
            text: "Which version?",
            clarification: {
              reason: "ambiguous_scope_or_version",
              questions: ["Which version should this apply to?"],
            },
          },
          evidence_pack: {
            verified_facts: [],
            supporting_evidence: [],
            counter_evidence: [],
            missing_evidence: [{ role: "query_scope" }],
            citation_map: {},
          },
          source_status: {},
          conversation_snapshot: null,
          context_revision: null,
        }),
      )
      .mockResolvedValueOnce(
        response({
          query_id: "query://resolved",
          state: "retrieval_only",
          client_turn_id: "turn-resolved",
          answer: {
            status: "retrieval_only",
            answer_mode: "retrieval_only",
            text: "Resolved evidence summary.",
            citations: [],
          },
          evidence_pack: {
            verified_facts: [],
            supporting_evidence: [],
            counter_evidence: [],
            missing_evidence: [],
            citation_map: {},
          },
          source_status: {},
        }),
      );
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(
      screen.getByLabelText("可信查询问题"),
      "What changed there?",
    );
    await user.click(screen.getByRole("button", { name: /查询/ }));
    expect(
      await screen.findByText("Which version should this apply to?"),
    ).toBeInTheDocument();

    await user.type(screen.getByLabelText("补充说明"), "Use commit abc123.");
    await user.click(screen.getByRole("button", { name: "继续查询" }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(2));
    const secondBody = JSON.parse(String(fetchSpy.mock.calls[1][1]?.body));
    expect(secondBody.question).toContain("What changed there?");
    expect(secondBody.question).toContain("Use commit abc123.");
    expect(secondBody.parent_turn_id).toBeNull();
    expect(secondBody).not.toHaveProperty("clarification_answer");
  });

  it("distinguishes a client timeout from a user cancellation", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    render(
      <TrustedQueryPanel projects={[project]} initialQuestion="Slow query" />,
    );

    fireEvent.change(screen.getByLabelText("可信查询项目"), {
      target: { value: project.id },
    });
    fireEvent.click(screen.getByRole("button", { name: /查询/ }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(16_500);
      await Promise.resolve();
    });

    expect(screen.getByText("查询超时")).toBeInTheDocument();
    expect(screen.getByText(/客户端已按延迟预算停止等待/)).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("fails closed in the UI for an unknown response state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        query_id: "query://drift",
        state: "complete_v3",
        answer: {
          status: "supported",
          answer_mode: "grounded_generation",
          text: "This must not be presented as grounded. [E1]",
          citations: ["E1"],
          claim_verification: {
            supported: true,
            claim_count: 1,
            supported_claim_count: 1,
          },
        },
        evidence_pack: {
          citation_map: {
            E1: {
              entity_id: "code://one",
              source: "code",
              locator: "src/current.ts:10",
            },
          },
        },
      }),
    );
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(screen.getByLabelText("可信查询问题"), "Check drift.");
    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText("UNVERIFIED")).toBeInTheDocument();
    expect(screen.queryByText("GROUNDED")).not.toBeInTheDocument();
  });

  it("shows every clarification question", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        query_id: "query://multi-clarify",
        state: "needs_clarification",
        answer: {
          status: "needs_clarification",
          answer_mode: "needs_clarification",
          clarification: {
            reason: "ambiguous_scope_or_version",
            questions: ["Which repository?", "Which commit or time range?"],
          },
        },
      }),
    );
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(screen.getByLabelText("可信查询问题"), "What changed?");
    await user.click(screen.getByRole("button", { name: /查询/ }));

    expect(await screen.findByText("Which repository?")).toBeInTheDocument();
    expect(screen.getByText("Which commit or time range?")).toBeInTheDocument();
  });

  it("leaves pending immediately when the project changes even if fetch ignores abort", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => new Promise(() => undefined));
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project, secondProject]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(screen.getByLabelText("可信查询问题"), "Long query");
    await user.click(screen.getByRole("button", { name: /查询/ }));
    expect(
      await screen.findByRole("button", { name: /取消/ }),
    ).toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText("可信查询项目"),
      secondProject.id,
    );

    expect(
      screen.queryByRole("button", { name: /取消/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /查询/ })).toBeEnabled();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("previews an explicit repository, commit, time and intent without running retrieval", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        contract_version: "query-plan-preview-v1",
        project_id: project.id,
        resolved_scope: {
          project_id: project.id,
          repository_ids: ["repo-rag"],
          commit: "abc123",
          as_of: "2026-08-01T00:00:00.000Z",
          source_types: ["code", "codex"],
        },
        intent: "change_trace",
        intent_signals: [],
        source_waves: [
          {
            wave: 1,
            execution_mode: "parallel",
            sources: ["code", "codex"],
            budgets: { code: 4000, codex: 4000 },
          },
        ],
        required_roles: ["current_code", "rationale"],
        budgets: {
          max_evidence: 12,
          max_context_tokens: 5000,
          deadline_ms: 15000,
          per_source: { code: 4000, codex: 4000 },
        },
        generation: {
          requested: true,
          policy: "grounded_only",
          answer_format: "concise",
        },
        plan_digest: "sha256:plan-safe",
        retrieval_performed: false,
      }),
    );
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(screen.getByLabelText("可信查询问题"), "What changed?");
    await user.click(screen.getByRole("button", { name: /来源引擎/ }));
    await user.type(screen.getByLabelText("查询仓库范围"), "repo-rag");
    await user.type(screen.getByLabelText("查询提交版本"), "abc123");
    await user.selectOptions(screen.getByLabelText("查询意图"), "change_trace");
    await user.click(screen.getByRole("button", { name: "预览执行计划" }));

    expect(await screen.findByText("执行前审阅")).toBeInTheDocument();
    expect(screen.getByText("零检索")).toBeInTheDocument();
    expect(screen.getByText("Wave 1")).toBeInTheDocument();
    expect(screen.queryByText("GROUNDED")).not.toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe("/v1/query/preview");
    const body = JSON.parse(String(fetchSpy.mock.calls[0][1]?.body));
    expect(body.scope.repository_ids).toEqual(["repo-rag"]);
    expect(body.scope.commit).toBe("abc123");
    expect(body.intent).toBe("change_trace");
  });

  it("invalidates a preview when question, generation, latency, or engine inputs change", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => response(planPreview()));
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(screen.getByLabelText("可信查询问题"), "What changed?");
    await user.click(screen.getByRole("button", { name: /来源引擎/ }));

    const preview = async () => {
      await user.click(
        screen.getByRole("button", { name: /(?:重新)?预览执行计划/ }),
      );
      expect(await screen.findByText("执行前审阅")).toBeInTheDocument();
    };
    const expectStale = () => {
      expect(screen.queryByText("执行前审阅")).not.toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("执行计划已失效");
      expect(
        screen.getByRole("button", { name: "重新预览执行计划" }),
      ).toBeEnabled();
    };

    await preview();
    await user.type(screen.getByLabelText("可信查询问题"), " now");
    expectStale();

    await preview();
    await user.click(screen.getByLabelText("允许 grounded generation"));
    expectStale();

    await preview();
    await user.selectOptions(screen.getByLabelText("延迟预算"), "30000");
    expectStale();

    await preview();
    await user.selectOptions(screen.getByLabelText("全局融合引擎"), "v2");
    expectStale();
    expect(fetchSpy).toHaveBeenCalledTimes(4);
  });

  it("does not restore an in-flight preview after its inputs become stale", async () => {
    let resolvePreview: ((value: Response) => void) | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolvePreview = resolve;
        }),
    );
    const user = userEvent.setup();
    render(<TrustedQueryPanel projects={[project]} />);

    await user.selectOptions(screen.getByLabelText("可信查询项目"), project.id);
    await user.type(screen.getByLabelText("可信查询问题"), "What changed?");
    await user.click(screen.getByRole("button", { name: "预览执行计划" }));
    expect(screen.getByRole("button", { name: "正在解析计划" })).toBeDisabled();

    await user.type(screen.getByLabelText("可信查询问题"), " now");
    expect(screen.getByRole("status")).toHaveTextContent("执行计划已失效");

    await act(async () => {
      resolvePreview?.(response(planPreview()));
    });

    expect(screen.queryByText("执行前审阅")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("执行计划已失效");
  });

  it("reopens persisted audit history and offers evidence graph links", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        contract_version: "query-history-v1",
        project_id: project.id,
        items: [
          {
            id: "audit://one",
            created_at: "2026-08-01T01:00:00Z",
            query_id: "global-query://one",
            trace_id: "trace-one",
            question_digest: "sha256:question",
            intent: "change_trace",
            interaction_state: "complete",
            answer_mode: "retrieval_only",
            reason_code: "evidence_sufficient",
            resolved_scope: {
              project_id: project.id,
              repository_ids: ["repo-rag"],
            },
            evidence: {
              count: 1,
              sources: ["code"],
              entity_ids: ["code://symbol/one"],
              citation_ids: ["E1"],
            },
          },
        ],
      }),
    );
    const user = userEvent.setup();
    render(
      <TrustedQueryPanel projects={[project]} initialProjectId={project.id} />,
    );

    const historyButton = await screen.findByRole("button", {
      name: /change_trace.*retrieval_only/,
    });
    await user.click(historyButton);

    expect(screen.getByText("trace-one")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "证据 1" })).toHaveAttribute(
      "href",
      expect.stringContaining("focus=code%3A%2F%2Fsymbol%2Fone"),
    );
  });

  it("routes mixed-source history to reviewable overviews without false entity focus", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      response({
        contract_version: "query-history-v1",
        project_id: project.id,
        items: [
          {
            id: "audit://mixed",
            created_at: "2026-08-01T01:00:00Z",
            query_id: "global-query://mixed",
            trace_id: "trace-mixed",
            question_digest: "sha256:question",
            intent: "global_synthesis",
            interaction_state: "complete",
            answer_mode: "retrieval_only",
            reason_code: "evidence_sufficient",
            resolved_scope: {
              project_id: project.id,
              repository_ids: ["repo-rag"],
            },
            evidence: {
              count: 2,
              sources: ["code", "experiment"],
              entity_ids: ["code://symbol/one", "experiment://run/one"],
              citation_ids: ["E1", "E2"],
            },
          },
        ],
      }),
    );
    const user = userEvent.setup();
    render(
      <TrustedQueryPanel projects={[project]} initialProjectId={project.id} />,
    );

    await user.click(
      await screen.findByRole("button", {
        name: /global_synthesis.*retrieval_only/,
      }),
    );

    expect(
      screen.queryByRole("link", { name: "证据 1" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("navigation", { name: "多源历史证据审阅" }),
    ).toHaveTextContent("不生成误导定位链接");
    expect(
      screen.getByRole("link", { name: "打开多源证据总览" }),
    ).toHaveAttribute("href", "/p/project-rag/map");
    expect(screen.getByRole("link", { name: "审阅代码来源" })).toHaveAttribute(
      "href",
      "/p/project-rag/map?board=code",
    );
    expect(screen.getByRole("link", { name: "审阅实验来源" })).toHaveAttribute(
      "href",
      "/p/project-rag/experiments",
    );
  });
});
