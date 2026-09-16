import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WikiPage } from "../../lib/types";
import { WikiWorkbench } from "./WikiWorkbench";

const page: WikiPage = {
  fragment_id: "wiki-fragment-one",
  logical_path: "/components/wiki-navigator",
  page_type: "component",
  title: "Wiki Navigator",
  summary: "在固定快照中规划证据义务并核验原始来源。",
  aliases: ["Navigator"],
  tags: ["wiki"],
  scope: {
    project_id: "project-rag",
    visibility_partition: "visibility-1234567890",
    acl_refs: ["project:project-rag"],
    source_generations: [
      {
        source: "code",
        generation_id: "source-code-v1",
        watermark: "watermark-code-v1",
        content_sha256: `sha256:${"1".repeat(64)}`,
      },
    ],
  },
  source_refs: [
    {
      source_ref_id: "source-ref-one",
      source: "code",
      entity_type: "code.CodeSymbol",
      entity_id: "entity-wiki-navigator",
      locator: "code://project-rag/wiki/navigator",
      generation_id: "source-code-v1",
      watermark: "watermark-code-v1",
      acl_refs: ["project:project-rag"],
      observed_at: "2026-08-03T00:00:00Z",
      raw_content_sha256: `sha256:${"2".repeat(64)}`,
      content_sha256: `sha256:${"3".repeat(64)}`,
    },
  ],
  facts: [
    {
      fact_id: "fact-one",
      subject_path: "/components/wiki-navigator",
      predicate: "implements",
      object_text: "Evidence-obligation navigation",
      qualifiers: [],
      source_ref_ids: ["source-ref-one"],
      authority: "raw_observed",
      status: "active",
      confidence: 1,
      content_sha256: `sha256:${"4".repeat(64)}`,
    },
  ],
  links: [
    {
      link_id: "link-one",
      source_path: "/components/wiki-navigator",
      target_path: "/decisions/raw-verification",
      relation: "requires",
      inverse_relation: "required_by",
      source_ref_ids: ["source-ref-one"],
      status: "active",
      confidence: 0.98,
      content_sha256: `sha256:${"5".repeat(64)}`,
    },
  ],
  evidence_roles: ["implementation"],
  compiler_version: "wiki-compiler-v1",
  contract_version: "wiki-contract-v1",
  content_sha256: `sha256:${"6".repeat(64)}`,
};

const targetPage: WikiPage = {
  ...page,
  fragment_id: "wiki-fragment-two",
  logical_path: "/decisions/raw-verification",
  page_type: "decision",
  title: "Raw verification boundary",
  facts: [],
  links: [],
  content_sha256: `sha256:${"7".repeat(64)}`,
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderWorkbench() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/p/project-rag/wiki"]}>
        <Routes>
          <Route path="/p/:projectId/wiki" element={<WikiWorkbench />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WikiWorkbench", () => {
  const requests: Array<{ url: URL; init?: RequestInit }> = [];
  let wikiStatusAvailable = true;

  beforeEach(() => {
    requests.length = 0;
    wikiStatusAvailable = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        requests.push({ url, init });
        if (url.pathname.endsWith("/status")) {
          if (!wikiStatusAvailable) {
            return json({
              project_id: "project-rag",
              availability: "UNAVAILABLE",
              active_generation_id: null,
              manifest_sha256: null,
              compiler_authority_sha256: null,
              source_generations: [],
              visible_page_count: 0,
              error_book_count: 0,
              pending_patch_count: 0,
              sparse_availability: "UNAVAILABLE",
              dense_availability: "UNAVAILABLE",
              reranker_availability: "UNAVAILABLE",
              default_engine: "v1",
              wiki_opt_in_engine: "wiki_v1",
              quality_state: "QUALITY_HOLD",
              runtime_version: "agent-native-wiki-product-runtime-v1",
              content_sha256: `sha256:${"0".repeat(64)}`,
            });
          }
          return json({
            project_id: "project-rag",
            availability: "AVAILABLE",
            active_generation_id: "wiki-generation-v1",
            manifest_sha256: `sha256:${"8".repeat(64)}`,
            compiler_authority_sha256: `sha256:${"9".repeat(64)}`,
            source_generations: [["code", "wiki-quality-code-generation-v1"]],
            visible_page_count: 81,
            error_book_count: 1,
            pending_patch_count: 1,
            sparse_availability: "AVAILABLE",
            dense_availability: "UNAVAILABLE",
            reranker_availability: "UNAVAILABLE",
            default_engine: "v1",
            wiki_opt_in_engine: "wiki_v1",
            quality_state: "QUALITY_HOLD",
            runtime_version: "agent-native-wiki-product-runtime-v1",
            content_sha256: `sha256:${"a".repeat(64)}`,
          });
        }
        if (url.pathname.endsWith("/pages")) {
          return json({
            project_id: "project-rag",
            generation_id: "wiki-generation-v1",
            total: 81,
            offset: Number(url.searchParams.get("offset") || 0),
            limit: 40,
            next_offset: 40,
            pages: [page],
          });
        }
        if (url.pathname.endsWith("/read")) {
          return json(url.searchParams.get("path") === targetPage.logical_path ? targetPage : page);
        }
        if (url.pathname.endsWith("/follow")) {
          return json({
            project_id: "project-rag",
            generation_id: "wiki-generation-v1",
            source_path: page.logical_path,
            targets: [{ link: page.links[0], page: targetPage }],
            unresolved_target_paths: [],
            content_sha256: `sha256:${"b".repeat(64)}`,
          });
        }
        if (url.pathname.endsWith("/evidence")) {
          return json({
            project_id: "project-rag",
            generation_id: "wiki-generation-v1",
            page_path: page.logical_path,
            source_ref: page.source_refs[0],
            evidence: {
              source: "code",
              status: "observed",
              text: "Navigator verifies the raw candidate before completing evidence.",
              locator: page.source_refs[0].locator,
            },
            content_sha256: `sha256:${"c".repeat(64)}`,
          });
        }
        if (url.pathname.endsWith("/navigate")) {
          return json({
            request_sha256: `sha256:${"d".repeat(64)}`,
            generation_id: "wiki-generation-v1",
            obligations: [
              {
                obligation_id: "obligation-implementation",
                role: "implementation",
                required_sources: ["code"],
                status: "satisfied",
                supporting_page_paths: [page.logical_path],
                supporting_source_ref_ids: ["source-ref-one"],
              },
            ],
            selected_page_paths: [page.logical_path],
            verified_source_ref_ids: ["source-ref-one"],
            actions: [
              { ordinal: 1, action: "search", round: 1, query_sha256: `sha256:${"e".repeat(64)}`, input_ids: [], output_ids: [page.logical_path], diagnostic_code: null },
              { ordinal: 2, action: "verify_raw", round: 1, query_sha256: `sha256:${"e".repeat(64)}`, input_ids: ["source-ref-one"], output_ids: ["source-ref-one"], diagnostic_code: null },
              { ordinal: 3, action: "stop", round: 1, query_sha256: `sha256:${"e".repeat(64)}`, input_ids: [], output_ids: [], diagnostic_code: null },
            ],
            evidence_pack: { facts: [], missing_roles: [], complete: true },
            stop_reason: "evidence_complete",
            search_count: 1,
            page_read_count: 1,
            followed_link_count: 0,
            raw_verify_count: 1,
            navigator_version: "wiki-navigator-v1",
            content_sha256: `sha256:${"f".repeat(64)}`,
          });
        }
        if (url.pathname.endsWith("/query/preview")) {
          return json({
            contract_version: "query-plan-preview-v1",
            project_id: "project-rag",
            resolved_scope: {
              project_id: "project-rag",
              repository_ids: ["repo-rag"],
              branch: "main",
              commit: "abc123",
              source_types: ["code", "codex"],
            },
            intent: "current_implementation",
            intent_signals: ["implementation"],
            source_waves: [{
              wave: 1,
              execution_mode: "parallel",
              sources: ["code", "codex"],
              budgets: { code: 12, codex: 12 },
            }],
            required_roles: ["current_code", "version", "tests"],
            budgets: {
              max_evidence: 12,
              max_context_tokens: 8000,
              deadline_ms: 15000,
              per_source: { code: 12, codex: 12 },
            },
            generation: { requested: false, policy: "disabled", answer_format: "evidence_only" },
            plan_digest: `sha256:${"d".repeat(64)}`,
            retrieval_performed: false,
          });
        }
        if (url.pathname.endsWith("/query")) {
          return json({
            query_id: "query-live-one",
            trace_id: "trace-live-one",
            state: "retrieval_only",
            interaction_state: "retrieval_only",
            answer_mode: "retrieval_only",
            reason_code: "unsafe_evidence_removed",
            answer: {
              status: "retrieval_only",
              decision_status: "partially_supported",
              text: "当前实现由真实项目代码证据支持，但仍缺少测试角色。",
              citations: ["E1"],
              refusal: false,
              next_actions: ["补充测试证据"],
            },
            evidence_pack: {
              verified_facts: [],
              supporting_evidence: [{
                entity_id: "code-entity-one",
                source: "code",
                entity_type: "CodeSymbol",
                title: "CodePlatformIntegration",
                snippet: "生产实现会在进入 V2 前解析受治理 scope。",
                locator: "rag@abc123:src/platform_v2.py#L10-L40",
                version: "abc123",
                status: "indexed",
                evidence_role: "supporting",
                channels: ["lexical", "dense"],
                authority: 1,
                version_alignment: "exact",
              }],
              counter_evidence: [],
              role_coverage: {
                required: ["current_code", "version", "tests"],
                satisfied: ["current_code", "version"],
                missing: ["tests"],
                stale: [],
              },
              missing_evidence: [{ role: "tests", reason: "当前索引没有返回可核验的测试运行或测试源码。" }],
              source_status: {
                code: { status: "complete", candidate_count: 12, latency_ms: 24, index_generation: "code-gen-v1" },
                codex: { status: "timeout", candidate_count: 0, latency_ms: 3000, reason: "source_timeout" },
              },
              relations: [{
                id: "relation-one",
                source: "code-entity-one",
                target: "test-entity-missing",
                predicate: "validated_by",
                domain: "code",
                hop: 1,
                confidence: 0.9,
                review_status: "confirmed",
                derivation: "observed",
              }],
              knowledge_organization: {
                schema_version: "query-knowledge-organization-v1",
                question_digest: `sha256:${"7".repeat(64)}`,
                decision: { status: "insufficient_evidence", complete: false },
                obligations: [
                  { role: "current_code", status: "satisfied", basis: "direct_evidence", evidence_entity_ids: ["code-entity-one"], citation_ids: ["E1"], sources: ["code"] },
                  { role: "version", status: "satisfied", basis: "direct_evidence", evidence_entity_ids: ["code-entity-one"], citation_ids: ["E1"], sources: ["code"] },
                  { role: "tests", status: "missing", basis: "direct_evidence", evidence_entity_ids: [], citation_ids: [], sources: [] },
                ],
                evidence_clusters: [{
                  cluster_id: "cluster-current-code",
                  role: "current_code",
                  entity_ids: ["code-entity-one"],
                  citation_ids: ["E1"],
                  sources: ["code"],
                  verified_count: 0,
                  supporting_count: 1,
                }],
                relation_paths: [{
                  relation_id: "relation-one",
                  source_entity_id: "code-entity-one",
                  target_entity_id: "test-entity-missing",
                  predicate: "validated_by",
                  domain: "code",
                  review_status: "confirmed",
                  derivation: "observed",
                }],
                risks: [],
                gaps: [{ role: "tests", reason: "当前索引没有返回可核验的测试运行或测试源码。" }],
                selected_evidence_count: 1,
                counter_evidence_count: 0,
                reasoning_included: false,
                derived_only_from_evidence_pack: true,
                content_digest: `sha256:${"8".repeat(64)}`,
              },
              next_actions: ["补充测试证据"],
            },
            required_roles: ["current_code", "version", "tests"],
            satisfied_roles: ["current_code", "version"],
            missing_roles: ["tests"],
            source_status: {},
            next_actions: ["补充测试证据"],
          });
        }
        if (url.pathname.endsWith("/organization/preview")) {
          return json({
            project_id: "project-rag",
            organization_kind: "live_project_inventory",
            generation_id: "wiki-live-project-rag-preview",
            request_sha256: `sha256:${"1".repeat(64)}`,
            active_generation_id: "wiki-generation-v1",
            active_snapshot_kind: "engineering_fixture",
            source_summaries: ["code", "codex", "experiment", "notebook", "document", "workspace"].map((source, index) => ({
              source,
              availability: index < 2 ? "AVAILABLE" : "EMPTY",
              raw_entity_count: index < 2 ? 12 - index : 0,
              candidate_count: index < 2 ? 3 : 0,
              generation_id: `live-${source}-one`,
              roles: index === 0 ? ["wiki-knowledge-organization", "intelligent-retrieval"] : index === 1 ? ["agent-integration"] : [],
              diagnostic: index < 2 ? null : "当前项目没有该来源资产",
            })),
            candidate_count: 6,
            compiled_candidate_count: 6,
            quarantined_candidate_count: 0,
            page_count: 9,
            page_type_counts: [["architecture", 2], ["capability", 3], ["source", 4]],
            knowledge_role_counts: [["wiki-knowledge-organization", 3], ["intelligent-retrieval", 2], ["agent-integration", 1]],
            added_page_count: 9,
            retained_page_count: 0,
            removed_page_count: 81,
            warnings: [],
            requires_explicit_publish: true,
            quality_state: "QUALITY_HOLD",
            organizer_version: "project-live-wiki-organizer-v1",
            content_sha256: `sha256:${"2".repeat(64)}`,
          });
        }
        if (url.pathname.endsWith("/organization/stage")) {
          return json({
            project_id: "project-rag",
            generation_id: "wiki-live-project-rag-preview",
            manifest_sha256: `sha256:${"3".repeat(64)}`,
            request_sha256: `sha256:${"1".repeat(64)}`,
            candidate_count: 6,
            page_count: 9,
            quarantined_candidate_count: 0,
            reviewer_authority_sha256: `sha256:${"4".repeat(64)}`,
            state: "STAGED_FOR_REVIEW",
            published: false,
            organizer_version: "project-live-wiki-organizer-v1",
            content_sha256: `sha256:${"5".repeat(64)}`,
          });
        }
        if (url.pathname.endsWith("/wiki/publish")) {
          return json({
            project_id: "project-rag",
            generation_id: "wiki-live-project-rag-preview",
            content_sha256: `sha256:${"6".repeat(64)}`,
          });
        }
        if (url.pathname.endsWith("/error-book")) {
          return json({
            total: 1,
            entries: [{ entry_id: "error-one", source: "code", code: "unsafe_content", reason_code: "secret", constraint: "来源文本包含不可发布内容", first_seen_generation: "wiki-generation-v1", last_seen_generation: "wiki-generation-v1", occurrences: 1, status: "open", content_sha256: `sha256:${"1".repeat(64)}` }],
          });
        }
        if (url.pathname.endsWith("/builder/patches")) {
          return json({
            total: 1,
            patches: [{ patch_id: "wiki-patch-one", base_manifest_sha256: `sha256:${"8".repeat(64)}`, operations: [{}], affected_query_ids: ["q1"], guard_query_ids: ["g1"], trigger_error_entry_ids: [], origin: "agent_unreviewed", reviewed: false, reviewer_authority_sha256: null, content_sha256: `sha256:${"2".repeat(64)}` }],
          });
        }
        if (url.pathname.includes("/builder/patches/") && url.pathname.endsWith("/decision")) {
          return json({ detail: "not found" }, 404);
        }
        return json({ detail: "not found" }, 404);
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders the snapshot catalogue, applies server filters and paginates", async () => {
    const user = userEvent.setup();
    renderWorkbench();

    expect(await screen.findByRole("heading", { name: "Wiki 知识中枢" })).toBeVisible();
    expect(screen.getByText("当前模式读取项目实时索引")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Wiki 快照浏览/ }));
    expect(screen.getByText("当前 Wiki 是质量评测快照")).toBeVisible();
    expect(await screen.findByRole("button", { name: /Wiki Navigator/ })).toBeVisible();
    expect(screen.getByText("81")).toBeVisible();

    await user.type(screen.getByLabelText("筛选 Wiki 页面"), "Navigator");
    await user.selectOptions(screen.getByLabelText("页面类型"), "component");
    await user.selectOptions(screen.getByLabelText("证据来源"), "code");
    await user.type(screen.getByLabelText("证据角色"), "implementation");
    await user.click(screen.getByRole("button", { name: "应用 Wiki 页面筛选" }));
    await waitFor(() => {
      const latest = [...requests].reverse().find((item) => item.url.pathname.endsWith("/pages"));
      expect(latest?.url.searchParams.get("q")).toBe("Navigator");
      expect(latest?.url.searchParams.get("page_type")).toBe("component");
      expect(latest?.url.searchParams.get("source")).toBe("code");
      expect(latest?.url.searchParams.get("role")).toBe("implementation");
    });

    await user.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => {
      const latest = [...requests].reverse().find((item) => item.url.pathname.endsWith("/pages"));
      expect(latest?.url.searchParams.get("offset")).toBe("40");
    });
  });

  it("runs governed navigation and exposes page, relation and raw-evidence authority", async () => {
    const user = userEvent.setup();
    renderWorkbench();

    await screen.findByRole("heading", { name: "证据义务驱动的项目检索" });
    await user.click(screen.getByRole("button", { name: /Wiki 快照浏览/ }));
    await screen.findByRole("button", { name: /Wiki Navigator/ });
    await user.type(screen.getByLabelText("Wiki 快照查询"), "为什么要核验原始证据？");
    await user.click(screen.getByRole("button", { name: "代码" }));
    await user.click(screen.getByRole("button", { name: "导航当前快照" }));

    expect(await screen.findByText("证据义务已满足")).toBeVisible();
    expect(screen.getByText("可审计导航轨迹")).toBeVisible();
    const navigationRequest = requests.find((item) => item.url.pathname.endsWith("/navigate"));
    expect(JSON.parse(String(navigationRequest?.init?.body))).toMatchObject({
      project_id: "project-rag",
      query_class: "multi_hop",
      required_sources: ["code"],
      require_raw_evidence: true,
    });

    await user.click(screen.getByRole("button", { name: "原始证据 1" }));
    await user.click(screen.getByRole("button", { name: "核验原文" }));
    expect(await screen.findByText("已核验原始证据")).toBeVisible();
    expect(screen.getByText(/Navigator verifies the raw candidate/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "关系 1" }));
    const relation = await screen.findByRole("button", { name: /Raw verification boundary/ });
    expect(within(relation).getByText("requires")).toBeVisible();
    await user.click(relation);
    expect(await screen.findByRole("heading", { name: "Raw verification boundary" })).toBeVisible();
  });

  it("plans and executes a live project evidence query instead of the quality snapshot", async () => {
    const user = userEvent.setup();
    renderWorkbench();

    expect(await screen.findByRole("heading", { name: "证据义务驱动的项目检索" })).toBeVisible();
    expect(screen.getByText("证据模式 · 禁止无引用生成")).toBeVisible();
    await user.type(screen.getByLabelText("项目证据查询"), "当前实现如何保证 ACL？");
    await user.click(screen.getByRole("button", { name: "规划并检索" }));

    expect(await screen.findByRole("heading", { name: "先确认范围与证据义务" })).toBeVisible();
    expect(await screen.findByText("当前实现由真实项目代码证据支持，但仍缺少测试角色。")).toBeVisible();
    expect(screen.getByRole("heading", { name: "从问题到证据结论的组织结构" })).toBeVisible();
    expect(screen.getByText("支持证据簇")).toBeVisible();
    expect(screen.getByText("反证、冲突与缺失")).toBeVisible();
    expect(screen.getByText("服务端确定性组织 · 可跨客户端复核")).toBeVisible();
    expect(screen.getByText("validated_by")).toBeVisible();
    expect(screen.getByText("当前索引没有返回可核验的测试运行或测试源码。")).toBeVisible();
    expect(screen.getByRole("heading", { name: "每个来源实际发生了什么" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "可人工审阅的原始定位" })).toBeVisible();
    expect(screen.getAllByText("CodePlatformIntegration")).toHaveLength(3);
    expect(screen.getByText("仍需补充可核验证据")).toBeVisible();

    const previewIndex = requests.findIndex((item) => item.url.pathname.endsWith("/query/preview"));
    const queryIndex = requests.findIndex((item) => item.url.pathname.endsWith("/query"));
    expect(previewIndex).toBeGreaterThan(-1);
    expect(queryIndex).toBeGreaterThan(previewIndex);
    expect(JSON.parse(String(requests[queryIndex].init?.body))).toMatchObject({
      question: "当前实现如何保证 ACL？",
      scope: { project_id: "project-rag" },
      mode: "evidence",
      answer_format: "evidence_only",
    });
  });

  it("shows Error Book and keeps unreviewed Builder output outside publication", async () => {
    const user = userEvent.setup();
    renderWorkbench();
    await screen.findByRole("heading", { name: "Wiki 知识中枢" });
    await user.click(screen.getByRole("button", { name: "治理与 Builder" }));

    expect(await screen.findByText("来源文本包含不可发布内容")).toBeVisible();
    await user.click(await screen.findByRole("button", { name: /wiki-patch-one/ }));
    expect(await screen.findByText("尚无离线试验决策；该提案不能进入发布路径。")).toBeVisible();
    expect(screen.getByText("待人工审阅")).toBeVisible();
  });

  it("previews, stages, and explicitly publishes current-project knowledge organization", async () => {
    const user = userEvent.setup();
    renderWorkbench();
    await screen.findByRole("heading", { name: "Wiki 知识中枢" });
    await user.click(screen.getByRole("button", { name: "治理与 Builder" }));

    expect(await screen.findByRole("heading", { name: "把当前项目编译成可审阅知识结构" })).toBeVisible();
    expect(screen.getByText("2/6")).toBeVisible();
    expect(screen.getByText("当前不是项目知识")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "构建并进入审阅" }));
    expect(await screen.findByText(/候选快照已暂存/)).toBeVisible();
    expect(requests.filter((item) => item.url.pathname.endsWith("/organization/stage"))).toHaveLength(1);
    expect(requests.filter((item) => item.url.pathname.endsWith("/wiki/publish"))).toHaveLength(0);

    await user.click(screen.getByRole("checkbox", { name: /我已核对六源计数/ }));
    await user.click(screen.getByRole("button", { name: /确认发布 9 页/ }));
    await waitFor(() => {
      const publish = requests.find((item) => item.url.pathname.endsWith("/wiki/publish"));
      expect(JSON.parse(String(publish?.init?.body))).toEqual({
        project_id: "project-rag",
        generation_id: "wiki-live-project-rag-preview",
        expected_manifest_sha256: `sha256:${"3".repeat(64)}`,
        reviewer_authority_sha256: `sha256:${"4".repeat(64)}`,
      });
    });
  });

  it("keeps the organization workflow available before a first Wiki snapshot exists", async () => {
    wikiStatusAvailable = false;
    renderWorkbench();

    expect(await screen.findByText("当前项目尚未形成可查询 Wiki")).toBeVisible();
    expect(screen.getByRole("button", { name: /项目证据检索/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Wiki 快照浏览/ })).toBeDisabled();
    expect(await screen.findByRole("heading", { name: "把当前项目编译成可审阅知识结构" })).toBeVisible();
  });
});
