import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RelationReviewWorkspace } from "./RelationReviewWorkspace";

const candidates = ["relation-one", "relation-two"].map((id, index) => ({
  id,
  project_id: "project-rag",
  source_title: `会话证据 ${index + 1}`,
  source_thread_id: `thread-${index + 1}`,
  source_item_type: "ToolResult",
  changed_path: `src/file_${index + 1}.py`,
  repository_name: "rag",
  target_entity_id: `entity-${index + 1}`,
  target_path: `src/file_${index + 1}.py`,
  target_commit_sha: "a".repeat(40),
  derivation: "path_exact",
  confidence: 0.9,
  review_status: "unreviewed",
  created_at: "2026-08-01T00:00:00Z",
  source: { content: "source", locator: `codex://thread/${index + 1}`, metadata: {} },
  target: { content: "target", source_uri: `code://file/${index + 1}` },
}));

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="review-location">{location.search}</output>;
}

let reviewFailure = false;
const reviewRequests: Array<Record<string, unknown>> = [];
const bulkReviewRequests: Array<Record<string, unknown>> = [];
let scanRequests = 0;
let pendingCount = 2;
let visibleCandidates = candidates;

function renderReview(initialEntry: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/p/:projectId/relations" element={<><RelationReviewWorkspace /><LocationProbe /></>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RelationReviewWorkspace navigation", () => {
  beforeEach(() => {
    reviewFailure = false;
    reviewRequests.length = 0;
    bulkReviewRequests.length = 0;
    scanRequests = 0;
    pendingCount = 2;
    visibleCandidates = candidates;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      let payload: unknown = visibleCandidates;
      let status = 200;
      if (url.pathname.endsWith("/scan/background") && init?.method === "POST") {
        scanRequests += 1;
        payload = {
          scan_id: "binding-scan://one",
          status: "running",
          counts: {},
        };
      } else if (url.pathname.endsWith("/scan/status")) {
        payload = {
          id: "binding-scan://one",
          project_id: "project-rag",
          status: "completed",
          counts: {
            source_items: 4,
            source_items_processed: 4,
            paths: 4,
            candidates: 2,
          },
        };
      } else if (url.pathname.endsWith("/review-all") && init?.method === "POST") {
        const body = JSON.parse(String(init.body || "{}")) as Record<string, unknown>;
        bulkReviewRequests.push(body);
        pendingCount = 0;
        visibleCandidates = [];
        payload = {
          project_id: "project-rag",
          decision: "confirmed",
          reviewed: 2,
          relations_created: 2,
          remaining_pending: 0,
        };
      } else if (url.pathname.endsWith("/review") && init?.method === "POST") {
        const body = JSON.parse(String(init.body || "{}")) as Record<string, unknown>;
        reviewRequests.push(body);
        if (reviewFailure) {
          status = 409;
          payload = { detail: "binding candidate has already been reviewed" };
        } else {
          const candidate = candidates.find((item) => item.id === url.searchParams.get("binding_id"));
          payload = {
            binding: { ...candidate, review_status: body.decision },
            relation: null,
          };
        }
      } else if (url.pathname.endsWith("/stats")) {
        payload = {
          total: 2,
          pending: pendingCount,
          confirmed: 2 - pendingCount,
          rejected: 0,
          high_confidence: pendingCount,
        };
      } else if (url.pathname.endsWith("/by-id")) {
        const candidate = candidates.find((item) => item.id === url.searchParams.get("binding_id"));
        if (candidate) payload = candidate;
        else {
          status = 404;
          payload = { detail: "binding candidate not found" };
        }
      }
      return new Response(JSON.stringify(payload), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("opens the requested relation and keeps later selections in the URL", async () => {
    const user = userEvent.setup();
    renderReview("/p/project-rag/relations?status=unreviewed&relation=relation-two");

    expect(await screen.findByRole("heading", { name: "会话证据 2 → file_2.py" })).toBeInTheDocument();
    expect(screen.getByTestId("review-location")).toHaveTextContent("relation=relation-two");

    await user.click(screen.getByRole("button", { name: /会话证据 1 → file_1.py/ }));
    expect(screen.getByTestId("review-location")).toHaveTextContent("relation=relation-one");
  });

  it("fails closed for an invalid deep-linked relation without selecting the first candidate", async () => {
    renderReview("/p/project-rag/relations?status=unreviewed&relation=codex-board-change%3A%2F%2Fthread-one");

    expect(await screen.findByText("无法打开指定关系")).toBeInTheDocument();
    expect(screen.getByTestId("review-location")).toHaveTextContent(
      "relation=codex-board-change%3A%2F%2Fthread-one",
    );
    expect(screen.queryByRole("button", { name: "确认关系" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "拒绝" })).not.toBeInTheDocument();
  });

  it("does not run bracket navigation while typing in the review note", async () => {
    const user = userEvent.setup();
    renderReview("/p/project-rag/relations?status=unreviewed&relation=relation-two");

    expect(await screen.findByRole("heading", { name: "会话证据 2 → file_2.py" })).toBeInTheDocument();
    const note = screen.getByLabelText("复核备注（拒绝时必填）");
    await user.click(note);
    fireEvent.keyDown(note, { key: "[" });
    fireEvent.change(note, { target: { value: "[] 保留当前候选" } });
    fireEvent.keyDown(note, { key: "]" });

    expect(note).toHaveValue("[] 保留当前候选");
    expect(screen.getByTestId("review-location")).toHaveTextContent("relation=relation-two");
  });

  it("requires a rejection note and confirms the exact relation before submitting", async () => {
    const user = userEvent.setup();
    renderReview("/p/project-rag/relations?status=unreviewed&relation=relation-two");

    await screen.findByRole("heading", { name: "会话证据 2 → file_2.py" });
    await user.click(screen.getByRole("button", { name: "拒绝" }));
    const dialog = screen.getByRole("alertdialog", { name: "确认复核判定" });
    expect(dialog).toHaveTextContent("relation-two");
    expect(screen.getByRole("button", { name: "确认拒绝" })).toBeDisabled();

    await user.type(screen.getByLabelText("复核备注（拒绝时必填）"), "目标版本不一致");
    await user.click(screen.getByRole("button", { name: "确认拒绝" }));

    await waitFor(() => expect(reviewRequests).toEqual([
      { decision: "rejected", note: "目标版本不一致" },
    ]));
  });

  it("keeps a failed mutation visible and retryable", async () => {
    reviewFailure = true;
    const user = userEvent.setup();
    renderReview("/p/project-rag/relations?status=unreviewed&relation=relation-one");

    await screen.findByRole("heading", { name: "会话证据 1 → file_1.py" });
    await user.click(screen.getByRole("button", { name: "确认关系" }));
    await user.click(screen.getByRole("button", { name: "确认提交" }));

    expect(await screen.findByText(/保存失败：binding candidate has already been reviewed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认提交" })).toBeEnabled();
    expect(screen.getByTestId("review-location")).toHaveTextContent("relation=relation-one");
  });

  it("scans missing candidates and confirms the complete pending queue once", async () => {
    const user = userEvent.setup();
    renderReview("/p/project-rag/relations?status=unreviewed");

    await screen.findByRole("heading", { name: "会话证据 1 → file_1.py" });
    await user.click(screen.getByRole("button", { name: "扫描候选" }));
    await waitFor(() => expect(scanRequests).toBe(1));
    expect(
      await screen.findByText(/候选扫描完成：检查 4 条源变更/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "全部通过" }));
    const dialog = screen.getByRole("alertdialog", { name: "确认全部通过" });
    expect(dialog).toHaveTextContent("完整队列中的2条关系");
    await user.click(screen.getByRole("button", { name: "确认全部 2 条" }));

    await waitFor(() =>
      expect(bulkReviewRequests).toEqual([
        {
          project_id: "project-rag",
          decision: "confirmed",
          expected_pending: 2,
          note: "在关系复核工作台一键确认全部待复核关系",
        },
      ]),
    );
    expect(
      await screen.findByText(/已一次确认 2 条关系/),
    ).toBeInTheDocument();
  });
});
