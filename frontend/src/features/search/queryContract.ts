import type {
  QueryClaimVerification,
  QueryCitation,
  QueryEngineRouting,
  QueryEvidenceItem,
  QueryResolvedScope,
  QuerySourceStatus,
  TrustedQueryResponse,
} from "../../lib/types";

export type QueryPresentationMode =
  | "clarification"
  | "partial"
  | "refusal"
  | "retrieval_only"
  | "grounded"
  | "unverified";

export type PresentedEvidence = QueryEvidenceItem & {
  evidence_role: "verified" | "supporting" | "counter";
};

const absolutePathPattern = /(?:file:\/\/\/|(?:^|[\s("'`])\/(?:Users|home|private|var|tmp|opt|etc)\/|[a-z]:[\\/])/i;
const secretPattern =
  /\b(?:bearer\s+\S+|(?:api[_-]?key|access[_-]?token|token|secret|password)\s*[:=]\s*\S+|(?:acl(?:_ref)?|allowed_acl_refs)\s*[:=]\s*\S+)/gi;
const citationPattern = /\[(E\d+)\]/g;

const clarificationStates = new Set(["needs_clarification", "clarification"]);
const refusalStates = new Set(["refusal", "refused"]);
const partialStates = new Set(["partial"]);
const retrievalOnlyStates = new Set(["retrieval_only", "insufficient_evidence"]);
const groundedStates = new Set([
  "complete",
  "supported",
  "grounded",
  "grounded_generation",
  "verified",
]);
const knownSourceStates = new Set([
  "complete",
  "partial",
  "timeout",
  "unavailable",
  "unauthorized",
  "not_indexed",
  "no_matching_evidence",
]);

export function queryPresentationMode(response: TrustedQueryResponse): QueryPresentationMode {
  const states = [
    response.interaction?.answer_mode,
    response.interaction?.state,
    response.interaction_state,
    response.state,
    response.answer_mode,
    response.answer.answer_mode,
    response.answer.status,
    response.answer.decision_status,
  ]
    .filter(Boolean)
    .map((value) => String(value).toLowerCase());

  if (states.some((value) => clarificationStates.has(value))) {
    return "clarification";
  }
  if (
    response.answer.refusal ||
    states.some((value) => refusalStates.has(value))
  ) {
    return "refusal";
  }
  if (states.some((value) => partialStates.has(value))) return "partial";
  if (states.some((value) => retrievalOnlyStates.has(value))) {
    return "retrieval_only";
  }
  const sourceStates = Object.values(querySourceStatus(response)).map((item) =>
    String(item.status || "").toLowerCase(),
  );
  if (sourceStates.some((value) => !knownSourceStates.has(value))) {
    return "unverified";
  }
  const hasGroundedMode = states.some(
    (value) => value === "grounded" || value === "grounded_generation",
  );
  const everyStateIsKnownGrounded =
    states.length > 0 && states.every((value) => groundedStates.has(value));
  const verification =
    response.claim_verification || response.answer.claim_verification;
  if (
    hasGroundedMode &&
    everyStateIsKnownGrounded &&
    verification?.supported !== false
  ) {
    return "grounded";
  }
  return "unverified";
}

export function queryEvidence(response: TrustedQueryResponse): PresentedEvidence[] {
  const pack = response.evidence_pack || {};
  return [
    ...(pack.verified_facts || []).map((item) => ({
      ...item,
      evidence_role: "verified" as const,
    })),
    ...(pack.supporting_evidence || []).map((item) => ({
      ...item,
      evidence_role: "supporting" as const,
    })),
    ...(pack.counter_evidence || []).map((item) => ({
      ...item,
      evidence_role: "counter" as const,
    })),
  ];
}

export function querySourceStatus(
  response: TrustedQueryResponse,
): Record<string, QuerySourceStatus> {
  return response.source_status || response.evidence_pack?.source_status || {};
}

export function queryScope(response: TrustedQueryResponse): QueryResolvedScope {
  return response.answer.applicable_scope || response.resolved_scope || {};
}

export function queryMissingRoles(response: TrustedQueryResponse): string[] {
  const coverage = response.evidence_pack?.role_coverage?.missing || [];
  const missing = response.evidence_pack?.missing_evidence?.map((item) => item.role) || [];
  return [...new Set([...response.missing_roles || [], ...coverage, ...missing])];
}

export function querySatisfiedRoles(response: TrustedQueryResponse): string[] {
  return [
    ...new Set([
      ...response.satisfied_roles || [],
      ...response.evidence_pack?.role_coverage?.satisfied || [],
    ]),
  ];
}

export function queryRequiredRoles(response: TrustedQueryResponse): string[] {
  return [
    ...new Set([
      ...response.required_roles || [],
      ...response.evidence_pack?.role_coverage?.required || [],
    ]),
  ];
}

export function queryStaleRoles(response: TrustedQueryResponse): string[] {
  return [...new Set(response.evidence_pack?.role_coverage?.stale || [])];
}

export function queryCitations(response: TrustedQueryResponse): Array<{
  id: string;
  citation: QueryCitation;
}> {
  const citationMap = response.evidence_pack?.citation_map || {};
  const citedIds = response.answer.citations?.length
    ? response.answer.citations
    : Object.keys(citationMap);
  return citedIds.flatMap((id) => {
    const citation = citationMap[id];
    return citation ? [{ id, citation }] : [];
  });
}

export function queryNextActions(response: TrustedQueryResponse): string[] {
  const candidates = [
    response.next_actions,
    response.answer.next_actions,
    response.evidence_pack?.next_actions,
    response.interaction?.next_actions,
  ];
  return [...new Set(candidates.flatMap((value) => value || []).filter(Boolean))];
}

export type PresentedEngineRouting = QueryEngineRouting & {
  source: string;
};

function engineRoutingFromUnknown(value: unknown): QueryEngineRouting {
  if (!value || typeof value !== "object") return {};
  const routing = value as Record<string, unknown>;
  return {
    requested:
      typeof routing.requested === "string"
        ? routing.requested
        : typeof routing.requested_engine === "string"
          ? routing.requested_engine
          : typeof routing.engine_requested === "string"
            ? routing.engine_requested
            : null,
    selected:
      typeof routing.selected === "string"
        ? routing.selected
        : typeof routing.selected_engine === "string"
          ? routing.selected_engine
          : typeof routing.engine_selected === "string"
            ? routing.engine_selected
            : null,
    fallback:
      typeof routing.fallback === "string"
        ? routing.fallback
        : routing.fallback && typeof routing.fallback === "object"
          ? [
              (routing.fallback as Record<string, unknown>).status,
              (routing.fallback as Record<string, unknown>).reason,
              (routing.fallback as Record<string, unknown>).component,
            ]
              .filter((item): item is string => typeof item === "string")
              .join(" · ") || "fallback_reported"
          : typeof routing.fallback_reason === "string"
            ? routing.fallback_reason
            : null,
    blockers: Array.isArray(routing.blockers)
      ? routing.blockers.filter((item): item is string => typeof item === "string")
      : [],
    quality_qualified:
      typeof routing.quality_qualified === "boolean"
        ? routing.quality_qualified
        : null,
    reason:
      typeof routing.reason === "string"
        ? routing.reason
        : typeof routing.selection_reason === "string"
          ? routing.selection_reason
          : typeof routing.reason_code === "string"
            ? routing.reason_code
            : null,
  };
}

export function queryEngineRouting(response: TrustedQueryResponse): PresentedEngineRouting[] {
  const trace = response.evidence_pack?.trace;
  const global = engineRoutingFromUnknown(
    trace?.platform?.global_engine || trace?.global_engine,
  );
  const sourceTrace = trace?.sources || {};
  const platformSourceRouting = trace?.platform?.source_engine_routing || {};
  const status = querySourceStatus(response);
  const sourceNames = [
    ...new Set([
      ...Object.keys(sourceTrace),
      ...Object.keys(status),
      ...Object.keys(platformSourceRouting),
    ]),
  ];
  const sourceRouting = sourceNames.map((source) => {
    const traced = sourceTrace[source] || {};
    const item = status[source];
    const releaseRoute = traced.release_route || {};
    const authoritativeRoute = platformSourceRouting[source] || {};
    return {
      source,
      ...engineRoutingFromUnknown({
        ...item,
        ...traced,
        ...(item?.routing || {}),
        ...(traced.routing || {}),
        ...releaseRoute,
        ...authoritativeRoute,
      }),
    };
  });
  const hasGlobal = Boolean(
    global.requested ||
      global.selected ||
      global.fallback ||
      global.blockers?.length ||
      global.reason,
  );
  return [...(hasGlobal ? [{ source: "global", ...global }] : []), ...sourceRouting];
}

export function queryCorrectiveRounds(response: TrustedQueryResponse): number {
  if (typeof response.corrective_rounds === "number") return response.corrective_rounds;
  return response.evidence_pack?.trace?.planner?.corrective_attempts?.length || 0;
}

export function queryTransitions(response: TrustedQueryResponse): string[] {
  return [
    ...new Set([
      ...response.interaction?.transitions || [],
      ...response.evidence_pack?.trace?.interaction_states || [],
    ]),
  ];
}

export function queryClaimVerification(
  response: TrustedQueryResponse,
): QueryClaimVerification | null {
  return response.claim_verification || response.answer.claim_verification || null;
}

export function queryReasons(response: TrustedQueryResponse): string[] {
  return [
    ...new Set(
      [
        response.reason_code,
        response.interaction?.reason,
        response.answer.refusal_reason,
        response.answer.fallback_reason,
      ].filter((value): value is string => Boolean(value)),
    ),
  ];
}

export function queryFallbacks(response: TrustedQueryResponse): string[] {
  return [
    ...new Set(
      [
        response.fallback,
        response.answer.fallback_reason,
        ...queryEngineRouting(response).map((item) => item.fallback),
      ].filter((value): value is string => Boolean(value)),
    ),
  ];
}

export function queryBlockers(response: TrustedQueryResponse): string[] {
  return [
    ...new Set([
      ...response.blockers || [],
      ...queryEngineRouting(response).flatMap((item) => item.blockers || []),
    ]),
  ];
}

export function queryWatermarks(
  response: TrustedQueryResponse,
): Array<{ source: string; value: string }> {
  const consistency = response.consistency_watermark;
  const status = querySourceStatus(response);
  const values = [
    ...Object.entries(consistency?.sources || {}).flatMap(([source, value]) =>
      value ? [{ source, value }] : [],
    ),
    ...Object.entries(status).flatMap(([source, item]) =>
      item.watermark ? [{ source, value: item.watermark }] : [],
    ),
    ...(consistency?.index_generations || []).map((value) => ({
      source: "index",
      value,
    })),
  ];
  return values.filter(
    (item, index) =>
      values.findIndex(
        (candidate) =>
          candidate.source === item.source && candidate.value === item.value,
      ) === index,
  );
}

export function queryClaimCitationMap(
  response: TrustedQueryResponse,
): Array<{ claim: string; citationIds: string[] }> {
  const text = sanitizeDisplayText(response.answer.text || "");
  const claims = text
    .split(/(?<=[.!?。！？])(?:\s+|\n+)|\n{2,}/)
    .map((item) => item.trim())
    .filter(Boolean);
  return claims.flatMap((claim) => {
    const citationIds = [...claim.matchAll(citationPattern)].map((match) => match[1]);
    return citationIds.length ? [{ claim, citationIds: [...new Set(citationIds)] }] : [];
  });
}

export function sanitizeDisplayText(value: unknown, fallback = ""): string {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  if (absolutePathPattern.test(text)) {
    return text
      .replace(/file:\/\/\/[^\s)"'`]+/gi, "[受限本地来源]")
      .replace(/(^|[\s("'`])\/(?:Users|home|private|var|tmp|opt|etc)\/[^\s)"'`]+/gi, "$1[受限本地来源]")
      .replace(/[a-z]:[\\/][^\s)"'`]+/gi, "[受限本地来源]")
      .replace(secretPattern, "[受限凭据]");
  }
  return text.replace(secretPattern, "[受限凭据]");
}

export function safeMetadataValue(value: unknown, fallback = "—"): string {
  const text = sanitizeDisplayText(value);
  if (!text) return fallback;
  if (text === "[受限本地来源]") return "受限本地来源";
  return text.length > 180 ? `${text.slice(0, 177)}…` : text;
}

export function safeClientError(error: unknown): string {
  if (error instanceof Error && error.name === "ApiRequestError") return error.message;
  if (typeof navigator !== "undefined" && navigator.onLine === false) {
    return "当前网络不可用，请恢复连接后重试。";
  }
  return "无法连接查询服务，请稍后重试。";
}
