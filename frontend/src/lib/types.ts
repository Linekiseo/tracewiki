export type ProjectStatus = "active" | "paused" | "archived";

export type Project = {
  id: string;
  name: string;
  description: string;
  owner: string;
  acl_ref: string;
  classification: string;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
  last_activity_at?: string | null;
  topic_count: number;
  active_iteration_count: number;
  active_work_item_count?: number;
  current_topic?: string | null;
  repositories?: number;
  sessions?: number;
  experiments?: number;
  documents?: number;
  pending_reviews?: number;
  settings: Record<string, unknown>;
};

export type Topic = {
  id: string;
  display_key: string;
  project_id: string;
  title: string;
  problem_statement: string;
  objective: string;
  status: string;
  priority: number;
  owner: string;
  updated_at: string;
  iteration_count?: number;
  active_iteration_count?: number;
  tags: string[];
};

export type WorkItem = {
  id: string;
  display_key: string;
  project_id: string;
  topic_id?: string | null;
  iteration_id?: string | null;
  repository_id?: string | null;
  title: string;
  objective: string;
  kind: string;
  status: string;
  summary: string;
  acceptance_criteria?: string[];
  priority?: number;
  assignee_type?: "human" | "codex";
  assignee?: string;
  workspace_path?: string | null;
  base_ref?: string | null;
  due_at?: string | null;
  version?: number;
  created_at?: string;
  updated_at: string;
};

export type AuditEvent = {
  id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  actor: string;
  created_at: string;
  detail: Record<string, unknown>;
};

export type ProjectDashboard = {
  project: Project;
  stats: {
    repositories: number;
    sessions: number;
    relations: number;
    pending_reviews: number;
    topics: number;
    active_topics: number;
    iterations: number;
    active_iterations: number;
    linked_evidence: number;
    work_items: number;
    active_work_items: number;
  };
  active_iterations: Array<{
    id: string;
    display_key: string;
    title: string;
    status: string;
    progress: number;
    evidence_count: number;
    updated_at: string;
  }>;
  active_work_items: WorkItem[];
  recent_topics: Topic[];
  recent_activity: AuditEvent[];
};

export type SearchSource =
  "code" | "codex" | "experiment" | "notebook" | "document" | "workspace";

// Search responses cross a runtime trust boundary. Keep unknown source strings
// representable so the UI can show them without coercing them into a known source.
export type SearchResultSource = SearchSource | (string & Record<never, never>);

export type SearchResult = {
  entity_id: string;
  source: SearchResultSource;
  entity_type?: string;
  title: string;
  subtitle?: string;
  locator: string;
  snippet: string;
  version?: string;
  status?: string;
  evidence_role?: string;
  roles?: string[];
  channels?: string[];
  repository_id?: string;
  path?: string;
  thread_id?: string;
  start_line?: number;
  end_line?: number;
  relation_only?: boolean;
  relation_ids?: string[];
};

export type SearchResponse = {
  query_id: string;
  total: number;
  results: SearchResult[];
  trace: {
    duration_ms?: number;
    sources?: Record<string, number>;
  };
};

export type WikiSourceDomain =
  | "code"
  | "codex"
  | "experiment"
  | "notebook"
  | "document"
  | "workspace";

export type WikiQueryClass =
  | "local_detail"
  | "multi_hop"
  | "comparison"
  | "temporal"
  | "global"
  | "exploratory";

export type WikiSourceRef = {
  source_ref_id: string;
  source: WikiSourceDomain;
  entity_type: string;
  entity_id: string;
  locator: string;
  generation_id: string;
  watermark: string;
  acl_refs: string[];
  observed_at: string;
  raw_content_sha256: string;
  content_sha256: string;
};

export type WikiFact = {
  fact_id: string;
  subject_path: string;
  predicate: string;
  object_text?: string | null;
  object_path?: string | null;
  qualifiers: Array<{ key: string; value: string }>;
  source_ref_ids: string[];
  authority: string;
  status: string;
  confidence: number;
  valid_from?: string | null;
  valid_to?: string | null;
  content_sha256: string;
};

export type WikiLink = {
  link_id: string;
  source_path: string;
  target_path: string;
  relation: string;
  inverse_relation: string;
  source_ref_ids: string[];
  status: string;
  confidence: number;
  content_sha256: string;
};

export type WikiPage = {
  fragment_id: string;
  logical_path: string;
  page_type: string;
  title: string;
  summary: string;
  aliases: string[];
  tags: string[];
  scope: {
    project_id: string;
    visibility_partition: string;
    acl_refs: string[];
    source_generations: Array<{
      source: WikiSourceDomain;
      generation_id: string;
      watermark: string;
      content_sha256: string;
    }>;
    as_of?: string | null;
  };
  source_refs: WikiSourceRef[];
  facts: WikiFact[];
  links: WikiLink[];
  evidence_roles: string[];
  compiler_version: string;
  contract_version: string;
  content_sha256: string;
};

export type WikiStatusResponse = {
  project_id: string;
  availability: "AVAILABLE" | "UNAVAILABLE";
  active_generation_id?: string | null;
  manifest_sha256?: string | null;
  compiler_authority_sha256?: string | null;
  source_generations: Array<[string, string]>;
  visible_page_count: number;
  error_book_count: number;
  pending_patch_count: number;
  sparse_availability: "AVAILABLE";
  dense_availability: "AVAILABLE" | "UNAVAILABLE";
  reranker_availability: "AVAILABLE" | "UNAVAILABLE";
  default_engine: "v1";
  wiki_opt_in_engine: "wiki_v1";
  quality_state: "QUALITY_HOLD";
  runtime_version: string;
  content_sha256: string;
};

export type WikiOrganizationSourceSummary = {
  source: WikiSourceDomain;
  availability: "AVAILABLE" | "EMPTY" | "UNAVAILABLE";
  raw_entity_count: number;
  candidate_count: number;
  generation_id: string;
  roles: string[];
  diagnostic?: string | null;
};

export type WikiOrganizationPreview = {
  project_id: string;
  organization_kind: "live_project_inventory";
  generation_id: string;
  request_sha256: string;
  active_generation_id?: string | null;
  active_snapshot_kind: "none" | "engineering_fixture" | "live_project";
  source_summaries: WikiOrganizationSourceSummary[];
  candidate_count: number;
  compiled_candidate_count: number;
  quarantined_candidate_count: number;
  page_count: number;
  page_type_counts: Array<[string, number]>;
  knowledge_role_counts: Array<[string, number]>;
  added_page_count: number;
  retained_page_count: number;
  removed_page_count: number;
  warnings: string[];
  requires_explicit_publish: true;
  quality_state: "QUALITY_HOLD";
  organizer_version: string;
  content_sha256: string;
};

export type WikiOrganizationStage = {
  project_id: string;
  generation_id: string;
  manifest_sha256: string;
  request_sha256: string;
  candidate_count: number;
  page_count: number;
  quarantined_candidate_count: number;
  reviewer_authority_sha256: string;
  state: "STAGED_FOR_REVIEW";
  published: false;
  organizer_version: string;
  content_sha256: string;
};

export type WikiPageListResponse = {
  project_id: string;
  generation_id?: string | null;
  total: number;
  offset: number;
  limit: number;
  next_offset?: number | null;
  pages: WikiPage[];
};

export type WikiSearchResponse = {
  request_sha256: string;
  generation_id: string;
  hits: Array<{
    rank: number;
    logical_path: string;
    page_sha256: string;
    title: string;
    page_type: string;
    source_domains: WikiSourceDomain[];
    evidence_roles: string[];
    exact_score: number;
    sparse_score: number;
    dense_score?: number | null;
    structural_score: number;
    fusion_score: number;
    rerank_score?: number | null;
    matched_terms: string[];
  }>;
  scanned_pages: number;
  sparse_availability: string;
  dense_availability: string;
  rerank_availability: string;
  content_sha256: string;
};

export type WikiNavigateResponse = {
  request_sha256: string;
  generation_id: string;
  obligations: Array<{
    obligation_id: string;
    role: string;
    required_sources: WikiSourceDomain[];
    status: string;
    supporting_page_paths: string[];
    supporting_source_ref_ids: string[];
  }>;
  selected_page_paths: string[];
  verified_source_ref_ids: string[];
  actions: Array<{
    ordinal: number;
    action: string;
    round: number;
    query_sha256: string;
    input_ids: string[];
    output_ids: string[];
    diagnostic_code?: string | null;
  }>;
  evidence_pack: {
    facts: Array<Record<string, unknown>>;
    missing_roles: string[];
    complete: boolean;
    [key: string]: unknown;
  };
  stop_reason: string;
  search_count: number;
  page_read_count: number;
  followed_link_count: number;
  raw_verify_count: number;
  navigator_version: string;
  content_sha256: string;
};

export type WikiFollowResponse = {
  project_id: string;
  generation_id: string;
  source_path: string;
  targets: Array<{ link: WikiLink; page: WikiPage }>;
  unresolved_target_paths: string[];
  content_sha256: string;
};

export type WikiRawEvidenceResponse = {
  project_id: string;
  generation_id: string;
  page_path: string;
  source_ref: WikiSourceRef;
  evidence: Record<string, unknown> & {
    source?: string;
    text?: string;
    locator?: string;
    status?: string;
    stable_version?: string;
  };
  content_sha256: string;
};

export type WikiErrorBookEntry = {
  entry_id: string;
  source: WikiSourceDomain;
  code: string;
  reason_code: string;
  constraint: string;
  first_seen_generation: string;
  last_seen_generation: string;
  occurrences: number;
  status: string;
  content_sha256: string;
};

export type WikiBuilderPatch = {
  patch_id: string;
  base_manifest_sha256: string;
  operations: Array<Record<string, unknown>>;
  affected_query_ids: string[];
  guard_query_ids: string[];
  trigger_error_entry_ids: string[];
  origin: string;
  reviewed: boolean;
  reviewer_authority_sha256?: string | null;
  content_sha256: string;
};

export type WikiBuilderDecision = {
  patch_sha256: string;
  trial_sha256: string;
  status: string;
  affected_utility_delta: number;
  improved_affected_queries: number;
  regressed_guard_queries: string[];
  hard_guard_failures: string[];
  production_authorized: false;
  content_sha256: string;
};

export type QueryEngineVersion = "v1" | "v2";

export type QuerySource = SearchSource;

export type QueryEngineOverrides = Partial<
  Record<QuerySource, QueryEngineVersion>
>;

export type QueryIntent =
  | "current_implementation"
  | "historical_implementation"
  | "change_trace"
  | "rationale"
  | "experiment_validation"
  | "claim_verification"
  | "reproduction"
  | "staleness_check"
  | "global_synthesis";

export type RagComponentSwitch =
  | "generator_prompt"
  | "context_packer"
  | "fusion"
  | "reranker"
  | "embedding_generation"
  | "source_retrievers"
  | "planner";

export type RagEngineStageStatus = {
  stage?: string | null;
  status?: string | null;
  available?: boolean | null;
  fallback?: boolean | string | RagFallbackStatus | null;
};

export type RagFallbackStatus = {
  active?: boolean | null;
  enabled?: boolean | null;
  used?: boolean | null;
  triggered?: boolean | null;
  target?: string | null;
  engine?: string | null;
  reason?: string | null;
};

export type RagLastKnownGoodStatus = {
  available?: boolean | null;
  active?: boolean | null;
  status?: string | null;
  generation?: string | null;
  generation_id?: string | null;
  index_generation?: string | null;
};

export type RagSourceRuntimeStatus = {
  source?: QuerySource;
  name?: QuerySource;
  authority_status?: string | null;
  availability?: string | null;
  reason_code?: string | null;
  stage?: string | null;
  v1_stage?: string | null;
  stage_v1?: string | null;
  v2_stage?: string | null;
  stage_v2?: string | null;
  v1?: RagEngineStageStatus | null;
  v2?: RagEngineStageStatus | null;
  engines?: {
    v1?: RagEngineStageStatus | null;
    v2?: RagEngineStageStatus | null;
  } | null;
  fallback?: boolean | string | RagFallbackStatus | null;
  fallback_to_v1?: boolean | null;
  last_known_good?: boolean | string | RagLastKnownGoodStatus | null;
  lkg?: boolean | string | RagLastKnownGoodStatus | null;
  quality_state?: string | null;
  authority_availability?: string | null;
  runtime_availability?: string | null;
};

export type RagCanonicalReleaseStatus = {
  decision?: string | null;
  authority?: string | null;
  authority_id?: string | null;
  authority_ready?: boolean | null;
  authority_attested?: boolean | null;
  integrity?: string | null;
  integrity_status?: string | null;
  integrity_verified?: boolean | null;
  integrity_sha256?: string | null;
  quality_hold?: boolean | null;
  default_engine?: string | null;
};

export type RagPerformanceStatus = {
  index?: string | Record<string, unknown> | null;
  cache?: string | Record<string, unknown> | null;
  latency?: unknown;
  dashboard?: Record<string, unknown> | null;
  index_version?: string | null;
  cache_state?: string | null;
  trace_count?: number | null;
  span_count?: number | null;
  calibration?: RagReviewedCalibrationStatus | null;
};

export type RagReviewedCalibrationStatus = {
  available?: boolean | null;
  status?: string | null;
  reviewed?: boolean | null;
  profile_count?: number | null;
  source_count?: number | null;
};

export type RagStatusResponse = {
  schema_version?: string | null;
  operational_version?: string | null;
  status_version?: string | null;
  generated_at?: string | null;
  observed_at?: string | null;
  snapshot_at?: string | null;
  stale?: boolean | null;
  stale_after_seconds?: number | null;
  default_engine?: string | null;
  quality_hold?: boolean | null;
  decision?: string | null;
  canonical_release?: RagCanonicalReleaseStatus | null;
  release?: RagCanonicalReleaseStatus | null;
  release_authority?: RagCanonicalReleaseStatus | null;
  release_integrity_sha256?: string | null;
  sources?:
    | Partial<Record<QuerySource, RagSourceRuntimeStatus>>
    | RagSourceRuntimeStatus[]
    | null;
  source_status?:
    | Partial<Record<QuerySource, RagSourceRuntimeStatus>>
    | RagSourceRuntimeStatus[]
    | null;
  source_snapshot?: {
    status?: string | null;
    reason_code?: string | null;
    sources?: RagSourceRuntimeStatus[] | null;
  } | null;
  component_switches?:
    | (Partial<Record<RagComponentSwitch, boolean | null>> & {
        version?: string | null;
        availability?: string | null;
      })
    | null;
  performance?: RagPerformanceStatus | null;
  performance_snapshot?: {
    status?: string | null;
    qualified?: boolean | null;
    production_observation?: boolean | null;
    reason_code?: string | null;
  } | null;
  reviewed_calibration?: RagReviewedCalibrationStatus | null;
};

export type QueryEngineRouting = {
  requested?: string | null;
  selected?: string | null;
  fallback?: string | null;
  blockers?: string[];
  quality_qualified?: boolean | null;
  reason?: string | null;
  selection_reason?: string | null;
};

export type QueryEngineTraceRouting = QueryEngineRouting & {
  requested_engine?: string | null;
  selected_engine?: string | null;
  served_engine?: string | null;
  engine_requested?: string | null;
  engine_selected?: string | null;
  engine_used?: string | null;
  fallback_reason?: string | null;
  reason_code?: string | null;
};

export type QuerySourceEngineTrace = QueryEngineTraceRouting & {
  status?: string | null;
  fallback?:
    | string
    | {
        status?: string | null;
        reason?: string | null;
        component?: string | null;
      }
    | null;
  routing?: QueryEngineRouting;
  release_route?: QueryEngineTraceRouting;
};

export type QueryConversationSnapshot = {
  schema_version: string;
  conversation_id: string;
  revision: number;
  last_turn_id?: string | null;
  project_id: string;
  repository_ids?: string[];
  commit?: string | null;
  as_of?: string | null;
  source_types?: QuerySource[];
  intent?: string | null;
  citation_ids?: string[];
  index_generations?: string[];
  scope_digest: string;
};

export type TrustedQueryInput = {
  question: string;
  project_id: string;
  sources?: QuerySource[];
  repository_ids?: string[];
  branch?: string;
  commit?: string;
  as_of?: string;
  intent?: QueryIntent;
  interaction_id?: string;
  parent_interaction_id?: string;
  conversation_id?: string;
  context_revision?: number | null;
  conversation_snapshot?: QueryConversationSnapshot | null;
  clarification_answer?: string;
  clarification_policy?: "auto" | "always" | "never";
  latency_budget_ms?: number;
  allow_generation?: boolean;
  max_evidence?: number;
  answer_format?: "concise" | "detailed" | "evidence_only";
  global_engine?: QueryEngineVersion;
  engine_overrides?: QueryEngineOverrides;
};

export type QueryPlanPreview = {
  contract_version: "query-plan-preview-v1";
  project_id: string;
  resolved_scope: QueryResolvedScope & { source_types?: QuerySource[] };
  intent: QueryIntent | string;
  intent_signals: string[] | Record<string, unknown>;
  query_understanding?: QueryUnderstanding;
  source_waves: Array<{
    wave: 1 | 2;
    execution_mode: "parallel" | string;
    sources: QuerySource[];
    budgets: Record<string, number>;
  }>;
  required_roles: string[];
  budgets: {
    max_evidence: number;
    max_context_tokens: number;
    deadline_ms: number | null;
    per_source: Record<string, number>;
  };
  generation: {
    requested: boolean;
    policy: "grounded_only" | "disabled" | string;
    answer_format: string;
  };
  plan_digest: string;
  retrieval_performed: false;
};

export type QueryHistoryItem = {
  id: string;
  created_at: string;
  query_id: string;
  trace_id?: string | null;
  question_digest: string;
  intent?: string | null;
  resolved_scope?: QueryResolvedScope;
  interaction_state?: string | null;
  answer_mode?: string | null;
  reason_code?: string | null;
  source_status?: Record<string, QuerySourceStatus>;
  evidence?: {
    citation_ids?: string[];
    sources?: QuerySource[];
    entity_ids?: string[];
    count?: number;
  };
  consistency_watermark?: QueryConsistencyWatermark;
};

export type QueryHistoryResponse = {
  contract_version: "query-history-v1";
  project_id: string;
  items: QueryHistoryItem[];
};

export type LLMProviderStatus = {
  contract_version: "llm-provider-runtime-v1";
  project_id: string;
  provider: "openai_compatible";
  base_url?: string | null;
  model?: string | null;
  configured: boolean;
  api_key_present: boolean;
  secret_storage: string;
  source?: "desktop_runtime" | "environment" | null;
  capabilities: string[];
  health?: "ready";
  tested?: boolean;
};

export type QueryUnderstanding = {
  contract_version: "query-understanding-hybrid-v2";
  strategy: "llm_structured_hybrid" | "local_statistical" | string;
  normalized_query: string;
  intent: QueryIntent | string;
  intent_confidence: number;
  source_hints: QuerySource[];
  required_roles: string[];
  subqueries: string[];
  entities: string[];
  temporal_scope?: string | null;
  ambiguity: string[];
  fallback_reason?: string | null;
  intent_distribution: Record<string, number>;
  source_distribution: Partial<Record<QuerySource, number>>;
  algorithm: {
    version: "query-understanding-hybrid-v2" | string;
    encoder: "structured_llm_plus_tfidf_prior" | "frozen_tfidf_word_char_ngram" | string;
    classifier: "calibrated_centroid_softmax" | string;
    uncertainty: number;
    complexity: number;
    decomposition: "constrained_llm_evidence_obligations" | "evidence_obligation_mmr" | string;
    planner_model?: string | null;
  };
  content_digest: string;
};

export type QueryEvidenceItem = SearchResult & {
  authority?: number;
  version_alignment?: string;
};

export type QueryCitation = {
  entity_id: string;
  source: QuerySource;
  locator?: string;
  version?: string | null;
};

export type QueryEvidenceRelation = {
  id: string;
  source: string;
  target: string;
  predicate?: string;
  domain?: string;
  hop?: number;
  confidence?: number;
  review_status?: string;
  derivation?: string;
};

export type QueryEvidenceConflict = {
  kind: string;
  entity_ids: string[];
  sources: string[];
  status: string;
  reason: string;
};

export type QueryVersionDifference = {
  entity_id?: string;
  title?: string;
  status?: string;
  source?: string;
  version?: string;
  reason?: string;
  [key: string]: unknown;
};

export type QueryKnowledgeOrganization = {
  schema_version: "query-knowledge-organization-v1";
  question_digest: string;
  decision: { status: string; complete: boolean };
  obligations: Array<{
    role: string;
    status: "satisfied" | "missing" | "unknown";
    basis: string;
    evidence_entity_ids: string[];
    citation_ids: string[];
    sources: string[];
  }>;
  evidence_clusters: Array<{
    cluster_id: string;
    role: string;
    entity_ids: string[];
    citation_ids: string[];
    sources: string[];
    obligation_roles: string[];
    verified_count: number;
    supporting_count: number;
  }>;
  relation_paths: Array<{
    relation_id: string;
    source_entity_id: string;
    target_entity_id: string;
    predicate: string;
    domain: string;
    review_status?: string | null;
    derivation?: string | null;
  }>;
  risks: QueryEvidenceConflict[];
  gaps: Array<{ role: string; reason?: string }>;
  selected_evidence_count: number;
  counter_evidence_count: number;
  reasoning_included: false;
  derived_only_from_evidence_pack: true;
  content_digest: string;
};

export type QuerySourceStatus = {
  status: string;
  reason?: string | null;
  latency_ms?: number | null;
  candidate_count?: number;
  index_generation?: string | null;
  watermark?: string | null;
  requested_engine?: string | null;
  selected_engine?: string | null;
  engine_requested?: string | null;
  engine_selected?: string | null;
  fallback?: string | null;
  blockers?: string[];
  routing?: QueryEngineRouting;
};

export type QueryResolvedScope = {
  project_id?: string;
  repository_ids?: string[];
  repositories?: Array<{ id?: string; name?: string }>;
  branch?: string | null;
  commit?: string | null;
  as_of?: string | null;
};

export type QueryClaimVerification = {
  verifier_version?: string;
  supported?: boolean;
  claim_count?: number;
  supported_claim_count?: number;
  reason_counts?: Record<string, number>;
};

export type QueryConsistencyWatermark = {
  index_generations?: string[];
  sources?: Record<string, string | null>;
};

export type QueryTrace = {
  duration_ms?: number;
  interaction_states?: string[];
  planner?: {
    corrective_attempts?: Array<Record<string, unknown>>;
  };
  platform?: {
    global_engine?: QueryEngineRouting;
    source_engine_routing?: Record<string, QueryEngineTraceRouting>;
    release?: Record<string, unknown>;
  };
  global_engine?: QueryEngineRouting;
  sources?: Record<string, QuerySourceEngineTrace>;
};

export type TrustedQueryResponse = {
  query_id: string;
  trace_id?: string;
  state?: string;
  query_understanding?: QueryUnderstanding;
  llm_provider?: Partial<LLMProviderStatus>;
  resolved_scope?: QueryResolvedScope;
  answer: {
    status?: string;
    answer_mode?: string;
    decision_status?: string;
    text?: string;
    citations?: string[];
    applicable_scope?: QueryResolvedScope;
    refusal?: boolean;
    refusal_reason?: string | null;
    fallback_reason?: string | null;
    partial?: boolean;
    clarification?: QueryClarification | null;
    claim_verification?: QueryClaimVerification | null;
    next_actions?: string[];
  };
  evidence_pack?: {
    question?: string;
    verified_facts?: QueryEvidenceItem[];
    supporting_evidence?: QueryEvidenceItem[];
    counter_evidence?: QueryEvidenceItem[];
    related_evidence?: QueryEvidenceItem[];
    missing_evidence?: Array<{ role: string; reason?: string }>;
    citation_map?: Record<string, QueryCitation>;
    role_coverage?: {
      required?: string[];
      satisfied?: string[];
      missing?: string[];
      stale?: string[];
    };
    source_status?: Record<string, QuerySourceStatus>;
    relations?: QueryEvidenceRelation[];
    conflicts_and_staleness?: QueryEvidenceConflict[];
    version_differences?: QueryVersionDifference[];
    knowledge_organization?: QueryKnowledgeOrganization;
    decision?: {
      status?: string;
      confidence?: number;
    };
    trace?: QueryTrace;
    next_actions?: string[];
  };
  source_status?: Record<string, QuerySourceStatus>;
  interaction_id?: string | null;
  parent_interaction_id?: string | null;
  interaction_state?: string;
  reason_code?: string | null;
  answer_mode?: string;
  required_roles?: string[];
  satisfied_roles?: string[];
  missing_roles?: string[];
  corrective_rounds?: number;
  consistency_watermark?: QueryConsistencyWatermark;
  claim_verification?: QueryClaimVerification | null;
  fallback?: string | null;
  blockers?: string[];
  conversation_id?: string | null;
  client_turn_id?: string | null;
  parent_turn_id?: string | null;
  context_revision?: number | null;
  conversation_snapshot?: QueryConversationSnapshot | null;
  interaction?: {
    contract_version?: string;
    state?: string;
    transitions?: string[];
    answer_mode?: string;
    reason?: string;
    deadline_exceeded?: boolean;
    clarification?: QueryClarification | null;
    next_actions?: string[];
  };
  next_actions?: string[];
};

export type QueryClarification = {
  needed?: boolean;
  reason?: string;
  questions?: string[];
  resume_required?: boolean;
  client_turn_id?: string | null;
};

export type GraphNode = {
  id: string;
  type: string;
  label: string;
  locator: string;
  domain: string;
  version?: string | null;
  repository_id?: string | null;
  qualified_name?: string | null;
  path?: string | null;
  language?: string | null;
  start_line?: number | null;
  end_line?: number | null;
  metadata?: Record<string, unknown>;
  score?: number;
  channels?: string[];
};

export type GraphEdge = {
  id: string;
  source: string;
  predicate: string;
  target: string;
  derivation?: string;
  confidence?: number;
  review_status?: string;
  domain?: string;
};

export type GraphMetadata = {
  total_nodes: number;
  total_edges: number;
  visible_nodes: number;
  visible_edges: number;
  cross_source_edges?: number;
  sampled?: boolean;
  vector_views?: number;
  embedding_model?: string | null;
  relation_counts?: Record<string, number>;
  semantic_similarity_edges?: number;
  semantic_result_nodes?: number;
  structural_neighbor_nodes?: number;
  direction_counts?: Record<"incoming" | "outgoing", number>;
  direction?: "both" | "incoming" | "outgoing";
  relation_types?: string[];
};

export type GraphSnapshot = {
  domain: string;
  mode: "relations" | "semantic";
  query: string;
  focus_entity_id?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  metadata: GraphMetadata;
};

export type GraphNeighbors = {
  root: string;
  cursor: number;
  next_cursor: number;
  total: number | null;
  has_more: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type CreateProjectInput = {
  name: string;
  description?: string;
};

export type Repository = {
  id: string;
  project_id: string;
  name: string;
  source_type: string;
  source_url: string | null;
  local_path: string;
  default_branch: string | null;
  head_commit: string;
  active_generation_id?: string | null;
  physical_repository_id?: string;
  binding_scope?: "project_scoped" | "legacy_primary" | string;
  status: string;
  updated_at: string;
  last_error?: string | null;
  stats: {
    files?: number;
    symbols?: number;
    edges?: number;
    commits?: number;
    diff_hunks?: number;
    test_results?: number;
    parse_errors?: number;
  };
};

export type CodeBranch = {
  id: string;
  name: string;
  head_sha: string;
  is_default: boolean;
  observed_at: string;
};

export type CodeRefs = {
  repository_id: string;
  head: { name: string; sha: string; branch: string };
  head_sha: string;
  default_branch: string;
  branches: CodeBranch[];
  observed_at?: string;
};

export type CodeFile = {
  repository_id: string;
  path: string;
  language: string;
  blob_hash?: string;
  size?: number | null;
  commit_sha?: string;
  ref?: string;
  historical?: boolean;
};

export type CodeSymbol = {
  name: string;
  qualified_name: string;
  kind: string;
  start_line: number;
  end_line: number;
};

export type CodeFileDetail = CodeFile & {
  content: string | null;
  binary: boolean;
  symbols: CodeSymbol[];
  parse_error?: string | null;
  source_uri?: string;
};

export type GitCommit = {
  id: string;
  repository_id: string;
  sha: string;
  author_name: string;
  authored_at: string;
  committed_at: string;
  message: string;
  diff_count: number;
  test_count: number;
  parent_shas: string[];
};

export type CodeDiffHunk = {
  id: string;
  repository_id: string;
  commit_id: string;
  commit_sha: string;
  parent_sha?: string | null;
  path: string;
  old_path?: string | null;
  change_type: string;
  old_start?: number | null;
  old_count?: number | null;
  new_start?: number | null;
  new_count?: number | null;
  source_locator: string;
  affected_symbols: string[];
};

export type CodeTestResult = {
  id: string;
  repository_id: string;
  commit_id?: string | null;
  commit_sha: string;
  command: string;
  status: string;
  exit_code?: number | null;
  duration_ms?: number | null;
  framework?: string | null;
  observed_at?: string | null;
};

export type CodeCommitDetail = GitCommit & {
  diff_hunks: CodeDiffHunk[];
  test_results: CodeTestResult[];
};

export type CodeComparison = {
  repository_id: string;
  base_ref: string;
  base_sha: string;
  target_ref: string;
  target_sha: string;
  changed_files: Array<{
    status: string;
    path: string;
    old_path?: string | null;
  }>;
  path?: string;
  diff?: string;
  base_file?: CodeFileDetail | null;
  target_file?: CodeFileDetail | null;
};

export type IngestionWorkflow = {
  id: string;
  repository_id?: string | null;
  status: string;
  stage: string;
  progress: number;
  error?: string | null;
  created_at: string;
  updated_at: string;
  counters: Record<string, number>;
  kind: string;
};

export type CodexProjectSyncStatus = {
  project_id: string;
  source_id?: string | null;
  source_path: string;
  project_path?: string | null;
  source_status: string;
  last_indexed_at?: string | null;
  discovered_sessions: number;
  indexable_sessions: number;
  indexed_sessions: number;
  visible_sessions?: number;
  hidden_subagent_sessions: number;
  invalid_metadata_sessions?: number;
  new_sessions: number;
  changed_sessions: number;
  live_sessions: number;
  removed_sessions: number;
  oversized_sessions: number;
  max_session_bytes?: number;
  needs_sync: boolean;
  sync_blocked_reason?: string | null;
  active_workflow_id?: string | null;
  discovery?: {
    scan_complete: boolean;
    project_match_complete: boolean;
    complete: boolean;
    visibility_state: "complete" | "partial_fail_closed" | string;
    candidate_limit_applied: boolean;
    requested_session_limit: number;
    selected_session_limit: number;
    jsonl_entries: number;
    candidate_sessions: number;
    active_candidates: number;
    archived_candidates: number;
    unreadable_entries: number;
    unsafe_symlink_entries: number;
    traversal_errors: number;
    matched_project_sessions: number;
    excluded_other_project_sessions: number;
    excluded_unbound_sessions: number;
    excluded_unreadable_sessions: number;
    excluded_oversized_sessions: number;
    sessions_beyond_selection_limit: number;
  };
  repository_visibility?: {
    project_id: string;
    bound_repositories: number;
    status_counts: Record<string, number>;
    ready_repositories: number;
    project_scoped_bindings: number;
    shared_physical_repositories: number;
  };
};

export type CodexProjectSyncResult = CodexProjectSyncStatus & {
  status: "queued" | "syncing" | "up_to_date";
  workflow_id?: string | null;
};

export type CodexSession = {
  id: string;
  thread_id: string;
  project_id: string;
  title: string;
  cwd: string;
  status: string;
  started_at: string;
  updated_at: string;
  turn_count: number;
  item_count: number;
  file_change_count: number;
  command_count: number;
  metadata: Record<string, unknown>;
};

export type CodexTimelineTurn = {
  id: string;
  ordinal: number;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  goal: string;
  summary: string;
  files: string[];
  file_changes?: CodexTimelineFileChange[];
  operations?: CodexTimelineOperation[];
  command_count: number;
  validation_count: number;
  locator?: string;
};

export type CodexTimelineFileChange = {
  id: string;
  path: string;
  change_type: string;
  status: string;
  locator: string;
  patch?: string;
  additions?: number;
  deletions?: number;
  hunk_count?: number;
  patch_truncated?: boolean;
  patch_format?: "unified" | "recovered";
};

export type CodexTimelineOperation = {
  id: string;
  kind: "command" | "validation" | "tool";
  name: string;
  command: string;
  output: string;
  status: string;
  exit_code?: number | null;
  timestamp?: string | null;
  locator: string;
};

export type CodexTimeline = Omit<CodexSession, "item_count"> & {
  validation_count: number;
  turns: CodexTimelineTurn[];
  visible_turn_count?: number;
  turn_offset?: number;
  turn_limit?: number;
  has_more_turns?: boolean;
};

export type IntegrationClient = {
  id: string;
  name: string;
  kind: string;
  version?: string | null;
  status: string;
  last_seen_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type CodexSyncProjection = {
  status: "syncing" | "sync_failed" | "no_data" | "ready" | "unavailable";
  reason: string;
  source_status: string;
  indexed_sessions: number;
  source_count: number;
  active_workflow: boolean;
  last_sync_at?: string | null;
  data_available: boolean;
};

export type CodexCapabilitySnapshot = {
  client_id: string;
  server_version: string;
  protocol_version: string;
  capabilities: string[];
  handshake_identity: {
    authority: string;
    authenticated_client_id: string;
    reported_client?: {
      name?: string | null;
      version?: string | null;
    } | null;
  };
  observed_at: string;
};

export type AgentAuditEvent = {
  id: string;
  client_id?: string | null;
  event_type: string;
  resource_type: string;
  resource_id?: string | null;
  detail?: Record<string, unknown>;
  created_at: string;
};

export type AgentAuditResponse = {
  project_id: string;
  client_id?: string | null;
  items: AgentAuditEvent[];
};

export type CodexBridgeStatus = {
  project_id: string;
  mcp: {
    endpoint: string;
    transport: string;
    status: string;
    authentication: string;
  };
  plugin: {
    installed: boolean;
    location: string;
    version?: string | null;
    name: string;
  };
  codex_cli: {
    available: boolean;
    location: string;
  };
  permissions: {
    read: string;
    write: string;
    delete: string;
    sandbox: string;
  };
  clients: IntegrationClient[];
  capabilities: string[];
  capability_snapshots: CodexCapabilitySnapshot[];
  sync: CodexSyncProjection;
  availability: "ready" | "disconnected" | "unauthorized" | "unavailable";
  reason: string;
  last_connected_at?: string | null;
};

export type AgentControlPlaneItem = {
  id: string;
  client_id?: string | null;
  name: string;
  connector: string;
  version?: string | null;
  availability: "ready" | "disconnected" | "unauthorized" | "unavailable";
  reason: string;
  capabilities: string[];
  capability_snapshot?: CodexCapabilitySnapshot | null;
  sync: CodexSyncProjection;
  recent_events: AgentAuditEvent[];
  scope: { project_id: string; token_scopes: string[] };
  last_seen_at?: string | null;
  active_execution_count: number;
  session_count: number;
  recent_executions: Array<
    Pick<
      CodexExecution,
      | "id"
      | "work_item_id"
      | "work_item_title"
      | "thread_id"
      | "status"
      | "mode"
      | "summary"
      | "error"
      | "created_at"
      | "updated_at"
    >
  >;
};

export type CodexExecutionEvent = {
  id: string;
  execution_id: string;
  sequence: number;
  event_type: string;
  summary: string;
  payload: Record<string, unknown>;
  artifact_path?: string | null;
  created_at: string;
};

export type CodexApproval = {
  id: string;
  project_id: string;
  execution_id?: string | null;
  work_item_id?: string | null;
  action: string;
  status: string;
  requested_by: string;
  decided_by?: string | null;
  payload: Record<string, unknown>;
  note: string;
  created_at: string;
  decided_at?: string | null;
};

export type CodexExecution = {
  id: string;
  project_id: string;
  work_item_id: string;
  work_item_title: string;
  work_item_key: string;
  client_id?: string | null;
  thread_id?: string | null;
  mode: "interactive" | "queue";
  status:
    | "awaiting_approval"
    | "queued"
    | "running"
    | "review"
    | "completed"
    | "failed"
    | "cancelled";
  sandbox: "read-only" | "workspace-write";
  workspace_path: string;
  base_ref?: string | null;
  base_commit?: string | null;
  result_commit?: string | null;
  prompt: string;
  summary: string;
  validation: Array<Record<string, unknown>>;
  changed_files: string[];
  context_snapshot: {
    work_item?: Partial<WorkItem>;
    project?: Partial<Project>;
    intelligence?: Record<string, unknown> | null;
  };
  artifact_path?: string | null;
  error?: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  events?: CodexExecutionEvent[];
  launch?: {
    runner: string;
    url?: string;
    available?: boolean;
    auto_send?: boolean;
  };
  approval?: CodexApproval;
};

export type CreateCodexExecutionInput = {
  project_id: string;
  work_item_id: string;
  mode: "interactive" | "queue";
  workspace_path?: string;
  base_ref?: string;
  prompt?: string;
  sandbox: "read-only" | "workspace-write";
};

export type ScientificDocument = {
  id: string;
  display_key: string;
  project_id: string;
  title: string;
  version: string;
  source_type: string;
  source_uri: string;
  content?: string;
  authors: string[];
  tags: string[];
  status: string;
  created_at: string;
  updated_at: string;
  sections?: Array<{
    id: string;
    title: string;
    level: number;
    content: string;
    start_line: number;
    end_line: number;
  }>;
  claims?: ResearchClaim[];
  pages?: Array<{
    id: string;
    page_number: number;
    content: string;
    source_locator: string;
  }>;
  tables?: Array<{
    id: string;
    title: string;
    caption: string;
    row_count: number;
    column_count: number;
    cells: Array<{
      id: string;
      row_index: number;
      column_index: number;
      value: string;
      is_header: number;
    }>;
  }>;
  figures?: Array<Record<string, unknown>>;
  citations?: Array<Record<string, unknown>>;
};

export type ResearchClaim = {
  id: string;
  display_key: string;
  project_id: string;
  document_id: string;
  content: string;
  claim_type: string;
  status: string;
  extraction_confidence: number;
  source_locator: string;
  updated_at: string;
  evidence?: Array<{
    id: string;
    evidence_type: string;
    relationship: string;
    confidence: number;
  }>;
};

export type Experiment = {
  id: string;
  display_key: string;
  project_id: string;
  title: string;
  objective: string;
  hypothesis: string;
  owner: string;
  status: string;
  tags: string[];
  created_at: string;
  updated_at: string;
  run_count?: number;
  completed_run_count?: number;
  failed_run_count?: number;
};

export type ExperimentRun = {
  id: string;
  display_key: string;
  project_id: string;
  experiment_id: string;
  name: string;
  status: string;
  branch?: string | null;
  commit_sha?: string | null;
  dataset_id?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  metrics?: Array<{
    id: string;
    name: string;
    value: number;
    unit?: string | null;
    split?: string | null;
  }>;
  artifacts?: Array<{
    id: string;
    name: string;
    uri: string;
    kind: string;
  }>;
};

export type BindingCandidate = {
  id: string;
  project_id: string;
  source_entity_id?: string;
  source_title: string;
  source_thread_id: string;
  source_turn_id?: string | null;
  source_item_type: string;
  changed_path: string;
  repository_id?: string;
  repository_name: string;
  target_entity_id: string;
  target_symbol_ids?: string[];
  target_path: string;
  target_commit_sha: string;
  derivation: string;
  confidence: number;
  review_status: string;
  created_at: string;
  reviewed_at?: string | null;
  review_note?: string;
  source?: {
    content: string;
    locator: string;
    metadata: Record<string, unknown>;
  };
  target?: {
    content: string;
    source_uri: string;
    language?: string | null;
    start_line?: number | null;
    end_line?: number | null;
  };
};

export type BindingStats = {
  total: number;
  pending: number;
  confirmed: number;
  rejected: number;
  high_confidence: number;
};
