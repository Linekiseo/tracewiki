import type {
  AgentAuditResponse,
  BindingCandidate,
  AgentControlPlaneItem,
  BindingStats,
  CodeCommitDetail,
  CodeComparison,
  CodeFile,
  CodeFileDetail,
  CodeRefs,
  CodexApproval,
  CodexBridgeStatus,
  CodexExecution,
  CodexProjectSyncResult,
  CodexProjectSyncStatus,
  CodexSession,
  CodexTimeline,
  CodexTimelineTurn,
  CreateCodexExecutionInput,
  CreateProjectInput,
  Experiment,
  ExperimentRun,
  GraphNeighbors,
  GraphSnapshot,
  GitCommit,
  IngestionWorkflow,
  LLMProviderStatus,
  Project,
  ProjectDashboard,
  RagStatusResponse,
  Repository,
  ResearchClaim,
  ScientificDocument,
  SearchResponse,
  TrustedQueryInput,
  QueryHistoryResponse,
  QueryPlanPreview,
  TrustedQueryResponse,
  WorkItem,
  WikiBuilderDecision,
  WikiBuilderPatch,
  WikiErrorBookEntry,
  WikiFollowResponse,
  WikiNavigateResponse,
  WikiOrganizationPreview,
  WikiOrganizationStage,
  WikiPage,
  WikiPageListResponse,
  WikiQueryClass,
  WikiRawEvidenceResponse,
  WikiSearchResponse,
  WikiSourceDomain,
  WikiStatusResponse,
} from "./types";
import { resolveDesktopApiUrl } from "../desktop/runtime";

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: BodyInit | object | null;
  safeErrors?: boolean;
};

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

function authHeaders(): HeadersInit {
  const storage = typeof window !== "undefined" ? window.localStorage : null;
  const token = storage?.getItem("rag_api_token");
  const aclRefs = storage?.getItem("rag_acl_refs");
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(aclRefs ? { "X-RAG-ACL-Refs": aclRefs } : {}),
  };
}

export const apiCredentials = {
  hasToken: () => {
    if (typeof window === "undefined") return false;
    try {
      return Boolean(window.localStorage.getItem("rag_api_token"));
    } catch {
      return false;
    }
  },
  saveToken: (value: string) => {
    if (typeof window === "undefined") return false;
    const token = value.trim();
    try {
      if (token) window.localStorage.setItem("rag_api_token", token);
      else window.localStorage.removeItem("rag_api_token");
      return Boolean(token);
    } catch {
      return false;
    }
  },
  clearToken: () => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.removeItem("rag_api_token");
    } catch {
      // Storage may be disabled; requests will continue without a token.
    }
  },
};

async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { safeErrors = false, ...fetchOptions } = options;
  const headers = new Headers(options.headers);
  const auth = authHeaders();
  Object.entries(auth).forEach(([key, value]) => headers.set(key, value));
  let body = options.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(body);
  }
  const response = await fetch(resolveDesktopApiUrl(path), {
    ...fetchOptions,
    headers,
    body: body as BodyInit | null,
  });
  if (!response.ok) {
    if (safeErrors) {
      throw new ApiRequestError(
        safeRequestMessage(response.status),
        response.status,
      );
    }
    const payload = await response.json().catch(() => ({}));
    throw new Error(
      payload.detail || `${response.status} ${response.statusText}`,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function safeRequestMessage(status: number): string {
  if (status === 401 || status === 403) return "当前身份无权执行此查询。";
  if (status === 404) return "查询入口或所选项目不存在。";
  if (status === 408 || status === 504) return "查询服务未能在时限内完成。";
  if (status === 409) return "查询上下文已经变化，请重新提交。";
  if (status === 422 || status === 400)
    return "查询参数未被服务接受，请检查范围和高级选项。";
  if (status === 429) return "查询请求过于频繁，请稍后再试。";
  if (status >= 500) return "查询服务暂时不可用，请稍后重试。";
  return "查询请求失败。";
}

const queryEngineHeaders = {
  code: "X-RAG-Code-Engine",
  codex: "X-RAG-Codex-Engine",
  experiment: "X-RAG-Experiment-Engine",
  notebook: "X-RAG-Notebook-Engine",
  document: "X-RAG-Document-Engine",
  workspace: "X-RAG-Workspace-Engine",
} as const;

async function requestBlob(
  path: string,
  options: RequestOptions = {},
): Promise<Blob> {
  const headers = new Headers(options.headers);
  const auth = authHeaders();
  Object.entries(auth).forEach(([key, value]) => headers.set(key, value));
  const { body: _body, ...requestOptions } = options;
  const response = await fetch(resolveDesktopApiUrl(path), { ...requestOptions, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(
      payload.detail || `${response.status} ${response.statusText}`,
    );
  }
  return response.blob();
}

export const api = {
  ai: {
    status: (projectId: string) =>
      request<LLMProviderStatus>(
        `/v1/ai/provider?project_id=${encodeURIComponent(projectId)}`,
        { safeErrors: true },
      ),
    test: (projectId: string) =>
      request<LLMProviderStatus>("/v1/ai/provider/test", {
        method: "POST",
        safeErrors: true,
        body: { project_id: projectId },
      }),
  },
  rag: {
    status: (signal?: AbortSignal) =>
      request<RagStatusResponse>("/v1/rag/status", {
        method: "GET",
        signal,
        safeErrors: true,
      }),
  },
  wiki: {
    status: (projectId: string, signal?: AbortSignal) =>
      request<WikiStatusResponse>(
        `/v1/wiki/status?project_id=${encodeURIComponent(projectId)}`,
        { signal },
      ),
    organizationPreview: (projectId: string, signal?: AbortSignal) =>
      request<WikiOrganizationPreview>(
        `/v1/wiki/organization/preview?project_id=${encodeURIComponent(projectId)}`,
        { signal, safeErrors: true },
      ),
    organizationStage: (projectId: string, signal?: AbortSignal) =>
      request<WikiOrganizationStage>("/v1/wiki/organization/stage", {
        method: "POST",
        body: { project_id: projectId },
        signal,
        safeErrors: true,
      }),
    publish: (input: {
      project_id: string;
      generation_id: string;
      expected_manifest_sha256: string;
      reviewer_authority_sha256: string;
    }, signal?: AbortSignal) =>
      request<{
        project_id: string;
        generation_id: string;
        content_sha256: string;
      }>("/v1/wiki/publish", {
        method: "POST",
        body: input,
        signal,
        safeErrors: true,
      }),
    pages: (
      projectId: string,
      input: {
        generationId?: string;
        query?: string;
        pageType?: string;
        source?: WikiSourceDomain | "";
        role?: string;
        offset?: number;
        limit?: number;
      } = {},
      signal?: AbortSignal,
    ) => {
      const params = new URLSearchParams({ project_id: projectId });
      if (input.generationId) params.set("generation_id", input.generationId);
      if (input.query) params.set("q", input.query);
      if (input.pageType) params.set("page_type", input.pageType);
      if (input.source) params.set("source", input.source);
      if (input.role) params.set("role", input.role);
      params.set("offset", String(input.offset || 0));
      params.set("limit", String(input.limit || 40));
      return request<WikiPageListResponse>(`/v1/wiki/pages?${params}`, { signal });
    },
    read: (
      projectId: string,
      path: string,
      generationId?: string,
      signal?: AbortSignal,
    ) => {
      const params = new URLSearchParams({ project_id: projectId, path });
      if (generationId) params.set("generation_id", generationId);
      return request<WikiPage>(`/v1/wiki/read?${params}`, { signal });
    },
    search: (
      input: {
        project_id: string;
        query: string;
        query_class?: WikiQueryClass;
        required_sources?: WikiSourceDomain[];
        required_roles?: string[];
        generation_id?: string;
        top_k?: number;
        candidate_limit?: number;
      },
      signal?: AbortSignal,
    ) =>
      request<WikiSearchResponse>("/v1/wiki/search", {
        method: "POST",
        body: input,
        signal,
        safeErrors: true,
      }),
    navigate: (
      input: {
        project_id: string;
        query: string;
        query_class?: WikiQueryClass;
        required_sources?: WikiSourceDomain[];
        required_roles?: string[];
        require_raw_evidence?: boolean;
      },
      signal?: AbortSignal,
    ) =>
      request<WikiNavigateResponse>("/v1/wiki/navigate", {
        method: "POST",
        body: input,
        signal,
        safeErrors: true,
      }),
    follow: (
      projectId: string,
      path: string,
      generationId?: string,
      signal?: AbortSignal,
    ) => {
      const params = new URLSearchParams({ project_id: projectId, path });
      if (generationId) params.set("generation_id", generationId);
      return request<WikiFollowResponse>(`/v1/wiki/follow?${params}`, { signal });
    },
    evidence: (
      projectId: string,
      pagePath: string,
      sourceRefId: string,
      generationId?: string,
      signal?: AbortSignal,
    ) => {
      const params = new URLSearchParams({
        project_id: projectId,
        page_path: pagePath,
        source_ref_id: sourceRefId,
      });
      if (generationId) params.set("generation_id", generationId);
      return request<WikiRawEvidenceResponse>(`/v1/wiki/evidence?${params}`, {
        signal,
        safeErrors: true,
      });
    },
    errorBook: (projectId: string, signal?: AbortSignal) =>
      request<{ total: number; entries: WikiErrorBookEntry[] }>(
        `/v1/wiki/error-book?project_id=${encodeURIComponent(projectId)}`,
        { signal },
      ),
    patches: (projectId: string, signal?: AbortSignal) =>
      request<{ total: number; patches: WikiBuilderPatch[] }>(
        `/v1/wiki/builder/patches?project_id=${encodeURIComponent(projectId)}`,
        { signal },
      ),
    patchDecision: (
      projectId: string,
      patchId: string,
      signal?: AbortSignal,
    ) =>
      request<WikiBuilderDecision>(
        `/v1/wiki/builder/patches/${encodeURIComponent(patchId)}/decision?project_id=${encodeURIComponent(projectId)}`,
        { signal },
      ),
  },
  projects: {
    list: () => request<Project[]>("/v1/projects"),
    getDashboard: (projectId: string) =>
      request<ProjectDashboard>(
        `/v1/workspace/dashboard?project_id=${encodeURIComponent(projectId)}`,
      ),
    create: (input: CreateProjectInput) =>
      request<Project>("/v1/projects", { method: "POST", body: input }),
    archive: (projectId: string) =>
      request<Project>(
        `/v1/projects/by-id?project_id=${encodeURIComponent(projectId)}`,
        { method: "PATCH", body: { status: "archived" } },
      ),
    createTopic: (projectId: string, title: string) =>
      request("/v1/research/topics", {
        method: "POST",
        body: { project_id: projectId, title },
      }),
  },
  search: {
    project: (projectId: string, query: string, signal?: AbortSignal) =>
      request<SearchResponse>("/v1/search", {
        method: "POST",
        signal,
        body: {
          query,
          project_id: projectId,
          sources: [],
          limit: 24,
          include_lineage: false,
        },
      }),
  },
  query: {
    requestBody: (input: TrustedQueryInput) => ({
      question: (input.clarification_answer || input.question).trim(),
      scope: {
        project_id: input.project_id,
        repository_ids: input.repository_ids || [],
        branch: input.branch || null,
        commit: input.commit || null,
        source_types: input.sources || [],
      },
      mode: input.allow_generation === false ? "evidence" : "answer",
      max_evidence: input.max_evidence ?? 12,
      intent: input.intent || null,
      as_of: input.as_of || null,
      include: input.sources || [],
      conversation_id: input.conversation_id || null,
      client_turn_id: input.interaction_id || null,
      parent_turn_id: input.parent_interaction_id || null,
      context_revision: input.context_revision ?? null,
      conversation_snapshot: input.conversation_snapshot || null,
      clarification_policy: input.clarification_policy || "auto",
      deadline_ms: input.latency_budget_ms,
      answer_format:
        input.allow_generation === false
          ? "evidence_only"
          : input.answer_format || "concise",
    }),
    run: (input: TrustedQueryInput, signal?: AbortSignal) => {
      const headers = new Headers();
      if (input.global_engine) headers.set("X-RAG-Engine", input.global_engine);
      Object.entries(input.engine_overrides || {}).forEach(
        ([source, version]) => {
          const header =
            queryEngineHeaders[source as keyof typeof queryEngineHeaders];
          if (header && version) headers.set(header, version);
        },
      );
      return request<TrustedQueryResponse>("/v1/query", {
        method: "POST",
        signal,
        headers,
        safeErrors: true,
        body: api.query.requestBody(input),
      });
    },
    preview: (input: TrustedQueryInput, signal?: AbortSignal) =>
      request<QueryPlanPreview>("/v1/query/preview", {
        method: "POST",
        signal,
        safeErrors: true,
        body: api.query.requestBody(input),
      }),
    history: (projectId: string, limit = 20, signal?: AbortSignal) =>
      request<QueryHistoryResponse>(
        `/v1/query/history?project_id=${encodeURIComponent(projectId)}&limit=${limit}`,
        { signal, safeErrors: true },
      ),
  },
  graph: {
    snapshot: (
      projectId: string,
      limit: number,
      domain = "overview",
      mode: "relations" | "semantic" = "relations",
      query = "",
      options: {
        focusEntityId?: string;
        relationTypes?: string[];
        direction?: "both" | "incoming" | "outgoing";
      } = {},
    ) => {
      const params = new URLSearchParams({
        project_id: projectId,
        domain,
        mode,
        query,
        limit: String(Math.min(500, Math.max(1, Math.round(limit)))),
      });
      if (options.focusEntityId)
        params.set("focus_entity_id", options.focusEntityId);
      options.relationTypes?.forEach((relation) =>
        params.append("relation_types", relation),
      );
      if (options.direction && options.direction !== "both") {
        params.set("direction", options.direction);
      }
      return request<GraphSnapshot>(`/v1/graph?${params.toString()}`);
    },
    neighbors: (entityId: string, cursor = 0, limit = 80) =>
      request<GraphNeighbors>(
        `/v1/graph/neighbors?entity_id=${encodeURIComponent(entityId)}&cursor=${Math.max(0, Math.round(cursor))}&limit=${Math.max(1, Math.round(limit))}`,
      ),
  },
  code: {
    repositories: (projectId?: string) =>
      request<Repository[]>(
        `/v1/repositories${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
      ),
    deleteRepository: (repositoryId: string) =>
      request<{
        id: string;
        deleted: true;
        removed: Record<string, number>;
      }>(
        `/v1/repositories/by-id?repository_id=${encodeURIComponent(repositoryId)}`,
        { method: "DELETE" },
      ),
    ingestRepository: (
      projectId: string,
      source: string,
      branch?: string,
      historyDepth = 100,
    ) =>
      request<{ workflow_id: string; status: string }>(
        "/v1/ingestion/repositories",
        {
          method: "POST",
          body: {
            project_id: projectId,
            acl_ref: `project:${projectId}`,
            source,
            branch: branch || null,
            history_depth: historyDepth,
          },
        },
      ),
    syncHistory: (repositoryId: string, depth = 100) =>
      request<{
        repository_id: string;
        status: string;
        commits: number;
        branches: number;
        hunks: number;
      }>("/v1/code/history/sync", {
        method: "POST",
        body: { repository_id: repositoryId, depth, include_diffs: true },
      }),
    refs: (repositoryId: string) =>
      request<CodeRefs>(
        `/v1/code/refs?repository_id=${encodeURIComponent(repositoryId)}`,
      ),
    files: (repositoryId: string, ref = "HEAD") =>
      request<CodeFile[]>(
        `/v1/code/files?repository_id=${encodeURIComponent(repositoryId)}&ref=${encodeURIComponent(ref)}`,
      ),
    file: (repositoryId: string, path: string, ref = "HEAD") =>
      request<CodeFileDetail>(
        `/v1/code/file?repository_id=${encodeURIComponent(repositoryId)}&path=${encodeURIComponent(path)}&ref=${encodeURIComponent(ref)}`,
      ),
    history: (repositoryId: string, limit = 100) =>
      request<GitCommit[]>(
        `/v1/code/history?repository_id=${encodeURIComponent(repositoryId)}&limit=${limit}`,
      ),
    commitDetail: (repositoryId: string, sha: string) =>
      request<CodeCommitDetail>(
        `/v1/code/history/commit?repository_id=${encodeURIComponent(repositoryId)}&sha=${encodeURIComponent(sha)}`,
      ),
    compare: (
      repositoryId: string,
      baseRef: string,
      targetRef: string,
      path?: string,
    ) =>
      request<CodeComparison>(
        `/v1/code/compare?repository_id=${encodeURIComponent(repositoryId)}&base_ref=${encodeURIComponent(baseRef)}&target_ref=${encodeURIComponent(targetRef)}${path ? `&path=${encodeURIComponent(path)}` : ""}`,
      ),
    workflows: () => request<IngestionWorkflow[]>("/v1/ingestion/workflows"),
  },
  sessions: {
    list: (projectId: string, query = "", signal?: AbortSignal) =>
      request<CodexSession[]>(
        `/v1/codex/sessions?project_id=${encodeURIComponent(projectId)}&query=${encodeURIComponent(query)}&include_subagents=true&limit=500`,
        { signal },
      ),
    syncStatus: (projectId: string, signal?: AbortSignal) =>
      request<CodexProjectSyncStatus>(
        `/v1/codex/projects/${encodeURIComponent(projectId)}/sync-status?include_archived=true&max_sessions=2000`,
        { signal },
      ),
    sync: (projectId: string, force = false) =>
      request<CodexProjectSyncResult>(
        `/v1/codex/projects/${encodeURIComponent(projectId)}/sync`,
        {
          method: "POST",
          body: { force, include_archived: true, max_sessions: 2000 },
        },
      ),
    workflow: (workflowId: string, signal?: AbortSignal) =>
      request<IngestionWorkflow>(
        `/v1/ingestion/workflows/${encodeURIComponent(workflowId)}`,
        { signal },
      ),
    timeline: (
      threadId: string,
      signal?: AbortSignal,
      turnOffset = 0,
      turnLimit = 24,
    ) =>
      request<CodexTimeline>(
        `/v1/codex/sessions/${encodeURIComponent(threadId)}/timeline?turn_offset=${turnOffset}&turn_limit=${turnLimit}`,
        { signal },
      ),
    turnAudit: (threadId: string, turnId: string, signal?: AbortSignal) =>
      request<CodexTimelineTurn>(
        `/v1/codex/sessions/${encodeURIComponent(threadId)}/turn-audit?turn_id=${encodeURIComponent(turnId)}`,
        { signal },
      ),
  },
  workItems: {
    list: (projectId: string) =>
      request<WorkItem[]>(
        `/v1/research/work-items?project_id=${encodeURIComponent(projectId)}&limit=500`,
      ),
    create: (
      projectId: string,
      input: {
        title: string;
        objective?: string;
        acceptance_criteria?: string[];
        workspace_path?: string;
        base_ref?: string;
      },
    ) =>
      request<WorkItem>("/v1/research/work-items", {
        method: "POST",
        body: {
          project_id: projectId,
          title: input.title,
          objective: input.objective || "",
          acceptance_criteria: input.acceptance_criteria || [],
          kind: "development",
          status: "ready",
          assignee_type: "codex",
          assignee: "Codex",
          workspace_path: input.workspace_path || null,
          base_ref: input.base_ref || "HEAD",
        },
      }),
  },
  codexBridge: {
    agents: (projectId: string) =>
      request<AgentControlPlaneItem[]>(
        `/v1/codex-bridge/agents?project_id=${encodeURIComponent(projectId)}`,
      ),
    agentAudit: (projectId: string, clientId?: string, limit = 20) => {
      const query = new URLSearchParams({
        project_id: projectId,
        limit: String(limit),
      });
      if (clientId) query.set("client_id", clientId);
      return request<AgentAuditResponse>(
        `/v1/codex-bridge/agents/audit?${query.toString()}`,
        { method: "GET", safeErrors: true },
      );
    },
    status: (projectId: string) =>
      request<CodexBridgeStatus>(
        `/v1/codex-bridge/status?project_id=${encodeURIComponent(projectId)}`,
      ),
    executions: (projectId: string, status?: string) =>
      request<CodexExecution[]>(
        `/v1/codex-bridge/executions?project_id=${encodeURIComponent(projectId)}${status ? `&execution_status=${encodeURIComponent(status)}` : ""}`,
      ),
    execution: (executionId: string) =>
      request<CodexExecution>(
        `/v1/codex-bridge/executions/by-id?execution_id=${encodeURIComponent(executionId)}`,
      ),
    createExecution: (input: CreateCodexExecutionInput) =>
      request<CodexExecution>("/v1/codex-bridge/executions", {
        method: "POST",
        body: input,
      }),
    approvals: (projectId: string, status = "pending") =>
      request<CodexApproval[]>(
        `/v1/codex-bridge/approvals?project_id=${encodeURIComponent(projectId)}&approval_status=${encodeURIComponent(status)}`,
      ),
    decideApproval: (
      approvalId: string,
      decision: "approved" | "rejected",
      note = "",
    ) =>
      request<CodexApproval>(
        `/v1/codex-bridge/approvals/decide?approval_id=${encodeURIComponent(approvalId)}`,
        {
          method: "POST",
          body: { decision, decided_by: "RAG Core", note },
        },
      ),
  },
  documents: {
    list: (projectId: string) =>
      request<ScientificDocument[]>(
        `/v1/documents?project_id=${encodeURIComponent(projectId)}`,
      ),
    get: (documentId: string) =>
      request<ScientificDocument>(
        `/v1/documents/by-id?document_id=${encodeURIComponent(documentId)}`,
      ),
    content: (documentId: string) =>
      requestBlob(
        `/v1/documents/content?document_id=${encodeURIComponent(documentId)}`,
      ),
    claims: (projectId: string, documentId?: string) =>
      request<ResearchClaim[]>(
        `/v1/documents/claims?project_id=${encodeURIComponent(projectId)}${documentId ? `&document_id=${encodeURIComponent(documentId)}` : ""}`,
      ),
    uploadBatch: (form: FormData) =>
      request<{
        batch_id: string;
        total: number;
        succeeded: number;
        failed: number;
      }>("/v1/documents/upload-batch", { method: "POST", body: form }),
  },
  experiments: {
    list: (projectId: string) =>
      request<Experiment[]>(
        `/v1/experiments?project_id=${encodeURIComponent(projectId)}`,
      ),
    get: (experimentId: string) =>
      request<Experiment>(
        `/v1/experiments/by-id?experiment_id=${encodeURIComponent(experimentId)}`,
      ),
    runs: (projectId: string, experimentId?: string) =>
      request<ExperimentRun[]>(
        `/v1/experiments/runs?project_id=${encodeURIComponent(projectId)}${experimentId ? `&experiment_id=${encodeURIComponent(experimentId)}` : ""}`,
      ),
    create: (projectId: string, title: string, objective = "") =>
      request<Experiment>("/v1/experiments", {
        method: "POST",
        body: { project_id: projectId, title, objective },
      }),
  },
  evidence: {
    claims: (projectId: string) =>
      request<ResearchClaim[]>(
        `/v1/documents/claims?project_id=${encodeURIComponent(projectId)}`,
      ),
    bindings: (projectId: string, reviewStatus?: string) =>
      request<BindingCandidate[]>(
        `/v1/bindings/candidates?project_id=${encodeURIComponent(projectId)}&limit=500${reviewStatus ? `&review_status=${encodeURIComponent(reviewStatus)}` : ""}`,
      ),
    binding: (bindingId: string) =>
      request<BindingCandidate>(
        `/v1/bindings/by-id?binding_id=${encodeURIComponent(bindingId)}`,
      ),
    bindingStats: (projectId: string) =>
      request<BindingStats>(
        `/v1/bindings/stats?project_id=${encodeURIComponent(projectId)}`,
      ),
    scanBindings: (projectId: string) =>
      request<{
        scan_id: string;
        status: string;
        counts: Partial<{
          candidates: number;
          source_items: number;
          source_items_processed: number;
          paths: number;
        }>;
      }>("/v1/bindings/scan/background", {
        method: "POST",
        body: { project_id: projectId, repository_ids: [], thread_ids: [] },
      }),
    bindingScanStatus: (scanId: string) =>
      request<{
        id: string;
        project_id: string;
        status: "running" | "completed" | "failed";
        counts: Partial<{
          candidates: number;
          source_items: number;
          source_items_processed: number;
          paths: number;
        }>;
        error?: string | null;
      }>(`/v1/bindings/scan/status?scan_id=${encodeURIComponent(scanId)}`),
    reviewBinding: (
      candidateId: string,
      decision: "confirmed" | "rejected",
      note = "",
    ) =>
      request<BindingCandidate>(
        `/v1/bindings/review?binding_id=${encodeURIComponent(candidateId)}`,
        { method: "POST", body: { decision, note } },
      ),
    reviewAllBindings: (
      projectId: string,
      expectedPending: number,
      note = "在关系复核工作台一键确认全部待复核关系",
    ) =>
      request<{
        project_id: string;
        decision: "confirmed";
        reviewed: number;
        relations_created: number;
        remaining_pending: number;
      }>("/v1/bindings/review-all", {
        method: "POST",
        body: {
          project_id: projectId,
          decision: "confirmed",
          expected_pending: expectedPending,
          note,
        },
      }),
  },
};
