import type {
  BindingCandidate,
  CodexSession,
  GraphEdge,
  GraphNode,
  ProjectDashboard,
} from "../../lib/types";
import { cleanEvidenceText, humanizeSearchTitle } from "../../lib/presentation";

export type ResearchDomain =
  | "workspace"
  | "code"
  | "codex"
  | "experiment"
  | "document"
  | "review"
  | "query";

export type ResearchMapNode = {
  id: string;
  type: string;
  domain: ResearchDomain;
  label: string;
  meta: string;
  locator: string;
  version?: string | null;
  status?: string;
  repositoryId?: string | null;
  path?: string | null;
  language?: string | null;
  qualifiedName?: string | null;
  symbolKind?: string | null;
  startLine?: number | null;
  endLine?: number | null;
  score?: number | null;
  channels?: string[];
  metadata?: Record<string, unknown>;
  parentId?: string | null;
  groupKey?: string | null;
  width?: number;
  height?: number;
  expanded?: boolean;
  x: number;
  y: number;
  source: "workspace" | "graph" | "binding" | "neighbor" | "cluster";
  children?: Array<{
    id: string;
    label: string;
    type: string;
    meta?: string;
  }>;
};

export type ResearchMapEdge = GraphEdge & {
  label: string;
  tone: ResearchDomain;
  occurrences?: number;
  structural?: boolean;
  scope?: boolean;
  /** Server-backed BindingCandidate id. Synthetic and generic graph edges omit it. */
  reviewCandidateId?: string;
};

export type EvidenceReviewField = {
  value: string;
  available: boolean;
};

export type EvidenceReviewRelation = {
  edge: ResearchMapEdge;
  direction: "incoming" | "outgoing";
  source: ResearchMapNode;
  target: ResearchMapNode;
  other: ResearchMapNode;
};

export type EvidenceReviewProfile = {
  identity: EvidenceReviewField;
  type: EvidenceReviewField;
  source: EvidenceReviewField;
  scope: EvidenceReviewField;
  acl: EvidenceReviewField;
  version: EvidenceReviewField;
  citation: EvidenceReviewField;
  summary: EvidenceReviewField;
  incoming: EvidenceReviewRelation[];
  outgoing: EvidenceReviewRelation[];
  reviewCandidateId?: string;
};

export type MapBounds = {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  width: number;
  height: number;
};

export type InspectorContextRole = "ancestor" | "focus" | "child" | "relation";

export type InspectorContextNode = {
  node: ResearchMapNode;
  role: InspectorContextRole;
  level: number;
};

export type InspectorContextEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
  predicate: string;
  role: "hierarchy" | "relation";
};

export type InspectorContextMap = {
  trail: InspectorContextNode[];
  children: InspectorContextNode[];
  relations: InspectorContextNode[];
  edges: InspectorContextEdge[];
  childTotal: number;
  relationTotal: number;
  focusLevel: number;
  totalLevels: number;
};

const domainOrder: ResearchDomain[] = [
  "workspace",
  "codex",
  "code",
  "experiment",
  "document",
  "review",
  "query",
];

const predicateLabels: Record<string, string> = {
  HAS_TURN: "包含轮次",
  HAS_ITEM: "包含记录",
  HAS_EPISODE: "形成片段",
  CONTAINS: "包含文件",
  DEFINES: "定义实体",
  CALLS: "调用",
  REFERENCES: "引用",
  IMPORTS: "导入",
  SEMANTIC_MATCH: "语义匹配",
  SEMANTIC_SIMILAR: "向量相似",
  contains_diff: "包含变更",
  contains_topic: "包含主题",
  contains_source: "项目范围",
  contains_directory: "包含目录",
  contains_file: "包含文件",
  scopes_session: "项目会话",
  scopes_code: "项目代码",
  continues_to: "下一轮",
  parent: "父提交",
  changed_path_maps_to: "变更映射",
  changed_symbol_maps_to: "修改实体",
  belongs_to_class: "属于类",
  implemented_in: "实现在",
  designed_by: "设计依据",
  implemented_by: "由其实现",
  validated_by: "由其验证",
  supported_by: "支持",
  produced_by: "产生于",
  reports: "报告",
  uses: "使用",
  related_to: "相关",
  contradicts: "矛盾",
};

export function graphDomain(value: string | null | undefined): ResearchDomain {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "research" || normalized === "workspace")
    return "workspace";
  if (normalized === "code") return "code";
  if (normalized === "codex" || normalized === "session") return "codex";
  if (normalized === "experiment") return "experiment";
  if (normalized === "document") return "document";
  if (normalized === "query") return "query";
  return "review";
}

export function domainLabel(domain: ResearchDomain): string {
  return {
    workspace: "研究",
    code: "代码",
    codex: "会话",
    experiment: "实验",
    document: "文档",
    review: "复核",
    query: "检索",
  }[domain];
}

export function predicateLabel(predicate: string): string {
  return predicateLabels[predicate] || predicate.replaceAll("_", " ");
}

export function entityTypeLabel(type: string): string {
  return (
    {
      ResearchProject: "科研项目",
      ResearchTopic: "研究主题",
      ResearchIteration: "研究迭代",
      ResearchWork: "研究任务",
      ExperimentWork: "实验任务",
      FileVersion: "文件版本",
      CodeSymbol: "代码实体",
      CodeClass: "类",
      CodeMethod: "方法",
      CodeFunction: "函数",
      CodeInterface: "接口",
      CodeModule: "模块",
      Repository: "代码仓库",
      Commit: "代码提交",
      DiffHunk: "代码差异",
      CodexThread: "研发会话",
      CodexTurn: "会话轮次",
      DevelopmentEpisode: "开发片段",
      UserGoal: "研究目标",
      AgentMessage: "Codex 回复",
      Patch: "代码补丁",
      FileChange: "文件修改",
      Experiment: "实验",
      ExperimentRun: "实验运行",
      Metric: "实验指标",
      Document: "科研文档",
      Claim: "研究主张",
      SemanticQuery: "语义检索",
      CodeBoard: "代码白板",
      CodexBoard: "会话白板",
      ExperimentBoard: "实验白板",
      DocumentBoard: "文档白板",
      ReviewBoard: "复核白板",
      TopicEvidenceBoard: "主题证据",
      CodexWorkspaceBoard: "会话空间",
      SessionCodeGroup: "代码变更",
      TopicCodexScope: "研发会话",
      TopicCodeScope: "代码证据",
      CodeRepositoryBoard: "代码空间",
      CodeDirectoryGroup: "代码模块",
      CodeFileGroup: "代码文件",
      CodeFileContainer: "代码文件",
      CodeFileFocus: "文件职责图",
      CodeResponsibilityGroup: "职责域",
      CodeNestedSymbol: "代码入口",
      CodeEvidenceGroup: "关联依据",
    }[type] || humanizeSearchTitle(type)
  );
}

export function statusLabel(status: string): string {
  return (
    {
      active: "进行中",
      running: "进行中",
      ready: "可用",
      indexed: "已索引",
      linked: "已关联",
      confirmed: "已确认",
      unreviewed: "待复核",
      pending: "待处理",
      rejected: "已拒绝",
      backlog: "待规划",
      blocked: "已阻塞",
      done: "已完成",
      completed: "已完成",
      scope: "项目范围",
      empty: "暂无数据",
    }[status] || humanizeSearchTitle(status)
  );
}

function normalizeThreadId(value: string): string {
  const normalized = String(value || "").trim();
  const match = normalized.match(/codex:\/\/thread\/([^/]+)/);
  if (match) return `codex://thread/${match[1]}`;
  return `codex://thread/${normalized.replace(/^\/+/, "")}`;
}

function bindingFileNodeId(binding: BindingCandidate): string {
  const targetPath = String(binding.target_path || "").trim();
  const entityId = String(binding.target_entity_id || "").trim();
  let decodedEntityId = entityId;
  try {
    decodedEntityId = decodeURIComponent(entityId);
  } catch {
    // A malformed entity locator is never used to synthesize a display path.
  }
  if (
    entityId &&
    targetPath &&
    (decodedEntityId.endsWith(targetPath) ||
      decodedEntityId.includes(`/${targetPath}`))
  ) {
    return entityId;
  }
  const repository = String(
    binding.repository_id || binding.repository_name || "repository",
  ).trim();
  const version = String(binding.target_commit_sha || "unversioned").trim();
  const encodedPath = targetPath
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/");
  return `code://binding-file/${encodeURIComponent(repository)}@${encodeURIComponent(version)}/${encodedPath}`;
}

function compactLocator(locator: string, fallback: string): string {
  const value = cleanEvidenceText(locator, 150);
  if (!value) return fallback;
  if (value.startsWith("codex://")) {
    const event = value.match(/(?:turn|episode|event)=?[/#:]*([^/#]+)/i)?.[1];
    return event ? `Codex · ${event.slice(0, 12)}` : "Codex 结构化记录";
  }
  const withoutFragment = value.split("#")[0];
  const parts = withoutFragment.split("/").filter(Boolean);
  return parts.slice(-3).join("/") || fallback;
}

function cleanGraphLabel(node: GraphNode): string {
  const original = cleanEvidenceText(node.label, 88);
  const threadSuffix =
    node.id.match(/codex:\/\/thread\/([^/]+)/)?.[1]?.slice(0, 8) ||
    node.id.split("/").filter(Boolean).at(-1)?.slice(0, 8);
  const looksLikeCodexArtifact =
    node.type === "CodexThread" &&
    (/\.jsonl$/i.test(original) ||
      /(?:^|\/)rollout-\d{4}-\d{2}-\d{2}t/i.test(original) ||
      /(?:^|\/)\d{2}\/\d{2}\/rollout-/i.test(original));
  if (looksLikeCodexArtifact) {
    return `Codex 会话 ${threadSuffix || ""}`.trim();
  }
  if (
    !original ||
    /^the following is the codex agent history/i.test(original) ||
    /^treat the transcript/i.test(original)
  ) {
    return node.type === "CodexThread"
      ? `Codex 会话 ${threadSuffix || ""}`.trim()
      : humanizeSearchTitle(node.type);
  }
  return humanizeSearchTitle(original);
}

function normalizeGraphNode(
  node: GraphNode,
  source: ResearchMapNode["source"] = "graph",
): ResearchMapNode {
  const domain = graphDomain(node.domain);
  const symbolKind = String(node.metadata?.kind || "").toLowerCase();
  const qualifiedSegments = String(node.qualified_name || "")
    .split(".")
    .filter(Boolean);
  const qualifiedParent = qualifiedSegments.at(-2) || "";
  const isQualifiedMethod =
    symbolKind === "function" && /^[A-Z][A-Za-z0-9_]*$/.test(qualifiedParent);
  const semanticType =
    node.type !== "CodeSymbol"
      ? node.type
      : symbolKind === "class"
        ? "CodeClass"
        : symbolKind === "method" || isQualifiedMethod
          ? "CodeMethod"
          : symbolKind === "function"
            ? "CodeFunction"
            : symbolKind === "interface" || symbolKind === "protocol"
              ? "CodeInterface"
              : symbolKind === "module" || symbolKind === "namespace"
                ? "CodeModule"
                : node.type;
  const lineRange =
    node.start_line && node.end_line
      ? `L${node.start_line}–${node.end_line}`
      : node.start_line
        ? `L${node.start_line}`
        : "";
  return {
    id: node.id,
    type: semanticType,
    domain,
    label: cleanGraphLabel(node),
    meta: [
      node.path || compactLocator(node.locator, semanticType),
      node.language,
      lineRange,
    ]
      .filter(Boolean)
      .join(" · "),
    locator: node.locator,
    version: node.version,
    repositoryId: node.repository_id,
    path: node.path,
    language: node.language,
    qualifiedName: node.qualified_name,
    symbolKind: symbolKind || null,
    startLine: node.start_line,
    endLine: node.end_line,
    score: node.score,
    channels: node.channels,
    metadata: node.metadata,
    x: 0,
    y: 0,
    source,
  };
}

function workspaceGraph(dashboard: ProjectDashboard): {
  nodes: ResearchMapNode[];
  edges: ResearchMapEdge[];
} {
  const nodes: ResearchMapNode[] = [];
  const edges: ResearchMapEdge[] = [];
  const projectId = `workspace://project/${dashboard.project.id}`;
  nodes.push({
    id: projectId,
    type: "ResearchProject",
    domain: "workspace",
    label: dashboard.project.name,
    meta: dashboard.project.description || "科研项目",
    locator: dashboard.project.id,
    status: dashboard.project.status,
    x: 0,
    y: -190,
    source: "workspace",
  });

  dashboard.recent_topics.forEach((topic, index) => {
    nodes.push({
      id: topic.id,
      type: "ResearchTopic",
      domain: "workspace",
      label: topic.title,
      meta: topic.objective || topic.problem_statement || topic.display_key,
      locator: topic.id,
      status: topic.status,
      x: (index - (dashboard.recent_topics.length - 1) / 2) * 250,
      y: -45,
      source: "workspace",
    });
    edges.push({
      id: `workspace-edge://${projectId}/${topic.id}`,
      source: projectId,
      predicate: "contains_topic",
      target: topic.id,
      derivation: "workspace",
      confidence: 1,
      review_status: "confirmed",
      label: "研究主题",
      tone: "workspace",
    });
  });

  dashboard.active_iterations.forEach((iteration, index) => {
    nodes.push({
      id: iteration.id,
      type: "ResearchIteration",
      domain: "workspace",
      label: iteration.title,
      meta: `${iteration.display_key} · ${iteration.progress}%`,
      locator: iteration.id,
      status: iteration.status,
      x: (index - (dashboard.active_iterations.length - 1) / 2) * 260,
      y: 115,
      source: "workspace",
    });
    const source =
      dashboard.recent_topics[index]?.id ||
      dashboard.recent_topics[0]?.id ||
      projectId;
    edges.push({
      id: `workspace-edge://${source}/${iteration.id}`,
      source,
      predicate: "advances",
      target: iteration.id,
      derivation: "workspace",
      confidence: 1,
      review_status: "confirmed",
      label: "推进验证",
      tone: "workspace",
    });
  });

  dashboard.active_work_items.forEach((work, index) => {
    const domain: ResearchDomain =
      work.kind === "experiment" ? "experiment" : "workspace";
    nodes.push({
      id: work.id,
      type: work.kind === "experiment" ? "ExperimentWork" : "ResearchWork",
      domain,
      label: work.title,
      meta: work.objective || work.display_key,
      locator: work.id,
      status: work.status,
      x: (index - (dashboard.active_work_items.length - 1) / 2) * 260,
      y: 280,
      source: "workspace",
    });
    const source =
      dashboard.active_iterations[index]?.id ||
      dashboard.active_iterations[0]?.id ||
      dashboard.recent_topics[0]?.id ||
      projectId;
    edges.push({
      id: `workspace-edge://${source}/${work.id}`,
      source,
      predicate: "requires",
      target: work.id,
      derivation: "workspace",
      confidence: 1,
      review_status: "confirmed",
      label: "需要执行",
      tone: domain,
    });
  });
  return { nodes, edges };
}

function bindingGraph(bindings: BindingCandidate[]): {
  nodes: ResearchMapNode[];
  edges: ResearchMapEdge[];
} {
  const nodes: ResearchMapNode[] = [];
  const edges: ResearchMapEdge[] = [];
  bindings.forEach((binding) => {
    const targetPath = String(binding.target_path || "").trim();
    if (!isPresentableCodePath(targetPath)) return;
    const thread = normalizeThreadId(binding.source_thread_id);
    const source = binding.source_entity_id || thread;
    const changedPath = isPresentableCodePath(binding.changed_path)
      ? String(binding.changed_path).trim()
      : null;
    const targetId = bindingFileNodeId(binding);
    nodes.push({
      id: thread,
      type: "CodexThread",
      domain: "codex",
      label: cleanEvidenceText(binding.source_title, 76) || "Codex 研发会话",
      meta: binding.source_turn_id ? "包含已定位的代码变更" : "会话代码变更",
      locator: thread,
      status: "linked",
      metadata: {
        ...(binding.source?.metadata || {}),
        evidence: cleanEvidenceText(binding.source?.content || "", 420),
        citation: binding.source?.locator || thread,
      },
      x: 0,
      y: 0,
      source: "binding",
    });
    if (source !== thread) {
      nodes.push({
        id: source,
        type: binding.source_item_type || "FileChange",
        domain: "codex",
        label: (changedPath || targetPath).split("/").at(-1) || "代码变更",
        meta: [
          binding.source_item_type,
          binding.source_turn_id ? "已定位到会话轮次" : "",
        ]
          .filter(Boolean)
          .join(" · "),
        locator: source,
        status: "linked",
        path: changedPath,
        metadata: {
          ...(binding.source?.metadata || {}),
          evidence: cleanEvidenceText(binding.source?.content || "", 420),
          citation: binding.source?.locator || source,
          target_path: targetPath,
        },
        x: 0,
        y: 0,
        source: "binding",
      });
      edges.push({
        id: `binding-source://${binding.id}`,
        source: thread,
        predicate: "HAS_ITEM",
        target: source,
        derivation: "codex_structure",
        confidence: 1,
        review_status: "confirmed",
        label: "产生修改",
        tone: "codex",
        structural: true,
      });
    }
    nodes.push({
      id: targetId,
      type: "FileVersion",
      domain: "code",
      label: targetPath.split("/").at(-1) || targetPath,
      meta: targetPath,
      locator: binding.target_entity_id || targetId,
      version: binding.target_commit_sha,
      repositoryId: binding.repository_id,
      path: targetPath,
      status: binding.review_status,
      metadata: {
        evidence: cleanEvidenceText(binding.target?.content || "", 420),
        citation: binding.target?.source_uri || binding.target_entity_id,
        binding_target_entity_id: binding.target_entity_id,
        binding_target_path: targetPath,
      },
      x: 0,
      y: 0,
      source: "binding",
    });
    edges.push({
      id: binding.id,
      source,
      predicate: "changed_path_maps_to",
      target: targetId,
      derivation: binding.derivation,
      confidence: binding.confidence,
      review_status: binding.review_status,
      label: "变更映射",
      tone: binding.review_status === "rejected" ? "review" : "code",
      reviewCandidateId: binding.id,
    });
    if (source !== thread) {
      edges.push({
        id: `binding-session-impact://${binding.id}`,
        source: thread,
        predicate: "changed_path_maps_to",
        target: targetId,
        derivation: "binding_session_rollup",
        confidence: binding.confidence,
        review_status: binding.review_status,
        label: "修改代码",
        tone: binding.review_status === "rejected" ? "review" : "code",
        reviewCandidateId: binding.id,
      });
    }
    (binding.target_symbol_ids || []).forEach((symbolId) => {
      const encodedName = symbolId.match(/#symbol=([^#]+)/)?.[1] || "symbol";
      const symbolName =
        decodeURIComponent(encodedName).split(/[.$]/).at(-1) || "代码实体";
      nodes.push({
        id: symbolId,
        type: "CodeSymbol",
        domain: "code",
        label: symbolName,
        meta: targetPath,
        locator: symbolId,
        version: binding.target_commit_sha,
        repositoryId: binding.repository_id,
        path: targetPath,
        status: binding.review_status,
        x: 0,
        y: 0,
        source: "binding",
      });
      edges.push({
        id: `binding-symbol://${binding.id}/${encodeURIComponent(symbolId)}`,
        source,
        predicate: "changed_symbol_maps_to",
        target: symbolId,
        derivation: binding.derivation,
        confidence: binding.confidence,
        review_status: binding.review_status,
        label: "修改实体",
        tone: binding.review_status === "rejected" ? "review" : "code",
      });
    });
  });
  return { nodes, edges };
}

function dedupeNodes(nodes: ResearchMapNode[]): ResearchMapNode[] {
  const byId = new Map<string, ResearchMapNode>();
  nodes.forEach((node) => {
    const current = byId.get(node.id);
    if (!current || current.source === "binding") byId.set(node.id, node);
  });
  return [...byId.values()];
}

function dedupeEdges(edges: ResearchMapEdge[]): ResearchMapEdge[] {
  const byRelation = new Map<string, ResearchMapEdge>();
  edges.forEach((edge) => {
    const key = [
      edge.source,
      edge.predicate,
      edge.target,
      edge.review_status || "confirmed",
    ].join("\u0000");
    const current = byRelation.get(key);
    if (!current) {
      byRelation.set(key, { ...edge, occurrences: edge.occurrences || 1 });
      return;
    }
    const occurrences = (current.occurrences || 1) + (edge.occurrences || 1);
    const reviewCandidateId =
      current.reviewCandidateId === edge.reviewCandidateId
        ? current.reviewCandidateId
        : current.reviewCandidateId && !edge.reviewCandidateId
          ? current.reviewCandidateId
          : edge.reviewCandidateId && !current.reviewCandidateId
            ? edge.reviewCandidateId
            : undefined;
    byRelation.set(key, {
      ...(Number(edge.confidence || 0) > Number(current.confidence || 0)
        ? edge
        : current),
      occurrences,
      reviewCandidateId,
    });
  });
  return [...byRelation.values()];
}

function edgeReviewPriority(status?: string): number {
  return (
    {
      rejected: 4,
      unreviewed: 3,
      pending: 2,
      confirmed: 1,
    }[status || "confirmed"] || 0
  );
}

function aggregateVisibleRelations(
  edges: ResearchMapEdge[],
): ResearchMapEdge[] {
  const grouped = new Map<string, ResearchMapEdge>();
  edges.forEach((edge) => {
    const key = [edge.source, edge.predicate, edge.target].join("\u0000");
    const current = grouped.get(key);
    if (!current) {
      grouped.set(key, { ...edge, occurrences: edge.occurrences || 1 });
      return;
    }
    const reviewStatus =
      edgeReviewPriority(edge.review_status) >
      edgeReviewPriority(current.review_status)
        ? edge.review_status
        : current.review_status;
    const reviewCandidateId =
      current.reviewCandidateId === edge.reviewCandidateId
        ? current.reviewCandidateId
        : current.reviewCandidateId && !edge.reviewCandidateId
          ? current.reviewCandidateId
          : edge.reviewCandidateId && !current.reviewCandidateId
            ? edge.reviewCandidateId
            : undefined;
    grouped.set(key, {
      ...current,
      confidence: Math.max(
        Number(current.confidence || 0),
        Number(edge.confidence || 0),
      ),
      occurrences: (current.occurrences || 1) + (edge.occurrences || 1),
      review_status: reviewStatus,
      reviewCandidateId,
    });
  });
  return [...grouped.values()];
}

function keepEdgesWithNodes(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
): ResearchMapEdge[] {
  const nodeIds = new Set(nodes.map((node) => node.id));
  return edges.filter(
    (edge) =>
      edge.source !== edge.target &&
      nodeIds.has(edge.source) &&
      nodeIds.has(edge.target),
  );
}

export function buildResearchGraph(input: {
  dashboard: ProjectDashboard;
  graphNodes: GraphNode[];
  graphEdges: GraphEdge[];
  bindings: BindingCandidate[];
  neighborNodes?: GraphNode[];
  neighborEdges?: GraphEdge[];
}): { nodes: ResearchMapNode[]; edges: ResearchMapEdge[] } {
  const workspace = workspaceGraph(input.dashboard);
  const binding = bindingGraph(input.bindings);
  const graphNodes = input.graphNodes.map((node) => normalizeGraphNode(node));
  const neighborNodes = (input.neighborNodes || []).map((node) =>
    normalizeGraphNode(node, "neighbor"),
  );
  const allNodes = dedupeNodes([
    ...workspace.nodes,
    ...graphNodes,
    ...binding.nodes,
    ...neighborNodes,
  ]);
  const nodeDomains = new Map(allNodes.map((node) => [node.id, node.domain]));
  const normalizeEdge = (edge: GraphEdge): ResearchMapEdge => {
    const sourceDomain =
      nodeDomains.get(edge.source) || graphDomain(edge.domain);
    const targetDomain =
      nodeDomains.get(edge.target) || graphDomain(edge.domain);
    return {
      ...edge,
      label: predicateLabel(edge.predicate),
      tone:
        edge.review_status === "rejected" || edge.predicate === "contradicts"
          ? "review"
          : sourceDomain !== targetDomain
            ? targetDomain
            : sourceDomain,
    };
  };
  const edges = dedupeEdges([
    ...workspace.edges,
    ...input.graphEdges.map(normalizeEdge),
    ...binding.edges,
    ...(input.neighborEdges || []).map(normalizeEdge),
  ]).filter(
    (edge) => nodeDomains.has(edge.source) && nodeDomains.has(edge.target),
  );
  return { nodes: layoutNodes(allNodes, edges), edges };
}

function stableHash(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function nodeImportance(node: ResearchMapNode, degree: number): number {
  const typeWeight =
    node.type === "ResearchProject" || node.type === "CodeRepositoryBoard"
      ? 1000
      : node.type === "ResearchTopic"
        ? 260
        : node.type === "ResearchIteration"
          ? 180
          : 0;
  return typeWeight + degree * 12;
}

function layoutNodeSize(node: ResearchMapNode): {
  width: number;
  height: number;
} {
  if (node.width || node.height) {
    return {
      width: node.width || 232,
      height: node.height || 92,
    };
  }
  if (node.children?.length) return { width: 286, height: 154 };
  if (node.type === "ResearchProject" || node.type === "CodeRepositoryBoard") {
    return { width: 248, height: 96 };
  }
  return { width: 232, height: 92 };
}

/**
 * Deterministic multi-stage force layout for the mixed research graph.
 *
 * Sources only provide a small initial angular bias. The final position is
 * decided by topology: real relations pull nodes together, high-degree nodes
 * form the visual skeleton, collision forces respect card dimensions, and the
 * research project remains a stable mental-map anchor.
 */
export function layoutNodes(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[] = [],
): ResearchMapNode[] {
  if (!nodes.length) return [];

  const nodeIndex = new Map(nodes.map((node, index) => [node.id, index]));
  const degree = new Map(nodes.map((node) => [node.id, 0]));
  edges.forEach((edge) => {
    if (!nodeIndex.has(edge.source) || !nodeIndex.has(edge.target)) return;
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  });

  const ordered = [...nodes].sort((left, right) => {
    const importance =
      nodeImportance(right, degree.get(right.id) || 0) -
      nodeImportance(left, degree.get(left.id) || 0);
    return importance || left.id.localeCompare(right.id);
  });
  const orderedIndex = new Map(ordered.map((node, index) => [node.id, index]));
  const count = ordered.length;
  const x = new Float64Array(count);
  const y = new Float64Array(count);
  const fx = new Float64Array(count);
  const fy = new Float64Array(count);
  const sizes = ordered.map(layoutNodeSize);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));

  ordered.forEach((node, index) => {
    if (
      node.type === "ResearchProject" ||
      node.type === "CodeRepositoryBoard"
    ) {
      x[index] = 0;
      y[index] = 0;
      return;
    }
    const domainIndex = domainOrder.indexOf(node.domain);
    const hash = stableHash(node.id);
    const jitter = ((hash % 1000) / 1000 - 0.5) * 0.8;
    const angle = index * goldenAngle + domainIndex * 0.31 + jitter;
    const radius =
      170 +
      Math.sqrt(index + 1) * 72 +
      Math.min(160, (degree.get(node.id) || 0) * 8);
    x[index] = Math.cos(angle) * radius;
    y[index] = Math.sin(angle) * radius * 0.76;
  });

  const springs = edges.flatMap((edge) => {
    const source = orderedIndex.get(edge.source);
    const target = orderedIndex.get(edge.target);
    if (source == null || target == null || source === target) return [];
    const crossSource = ordered[source].domain !== ordered[target].domain;
    return [
      {
        source,
        target,
        rest: crossSource ? 250 : 205,
        strength: crossSource ? 0.034 : 0.021,
      },
    ];
  });
  const pinned = ordered.findIndex(
    (node) =>
      node.type === "ResearchProject" || node.type === "CodeRepositoryBoard",
  );
  const iterations = count <= 90 ? 280 : count <= 220 ? 190 : 110;
  const pairwiseCount = Math.min(count, 320);

  for (let iteration = 0; iteration < iterations; iteration += 1) {
    fx.fill(0);
    fy.fill(0);

    for (let left = 0; left < pairwiseCount; left += 1) {
      for (let right = left + 1; right < pairwiseCount; right += 1) {
        let dx = x[right] - x[left];
        let dy = y[right] - y[left];
        if (dx === 0 && dy === 0) {
          const nudge =
            (stableHash(`${ordered[left].id}:${ordered[right].id}`) % 31) + 1;
          dx = nudge / 10;
          dy = -nudge / 13;
        }
        const distanceSquared = Math.max(100, dx * dx + dy * dy);
        const distance = Math.sqrt(distanceSquared);
        const repulsion = 76000 / distanceSquared;
        const ux = dx / distance;
        const uy = dy / distance;
        fx[left] -= ux * repulsion;
        fy[left] -= uy * repulsion;
        fx[right] += ux * repulsion;
        fy[right] += uy * repulsion;

        const requiredX = (sizes[left].width + sizes[right].width) / 2 + 18;
        const requiredY = (sizes[left].height + sizes[right].height) / 2 + 18;
        const overlapX = requiredX - Math.abs(dx);
        const overlapY = requiredY - Math.abs(dy);
        if (overlapX > 0 && overlapY > 0) {
          if (overlapX / requiredX < overlapY / requiredY) {
            const push = overlapX * 0.12 * (dx >= 0 ? 1 : -1);
            fx[left] -= push;
            fx[right] += push;
          } else {
            const push = overlapY * 0.16 * (dy >= 0 ? 1 : -1);
            fy[left] -= push;
            fy[right] += push;
          }
        }
      }
    }

    springs.forEach((spring) => {
      const dx = x[spring.target] - x[spring.source];
      const dy = y[spring.target] - y[spring.source];
      const distance = Math.max(1, Math.hypot(dx, dy));
      const pull = (distance - spring.rest) * spring.strength;
      const ux = dx / distance;
      const uy = dy / distance;
      fx[spring.source] += ux * pull;
      fy[spring.source] += uy * pull;
      fx[spring.target] -= ux * pull;
      fy[spring.target] -= uy * pull;
    });

    const cooling = 1 - iteration / iterations;
    const maxStep = 17 * cooling + 1.8;
    for (let index = 0; index < count; index += 1) {
      if (index === pinned) {
        x[index] = 0;
        y[index] = 0;
        continue;
      }
      const gravity = (degree.get(ordered[index].id) || 0) > 0 ? 0.0035 : 0.008;
      fx[index] -= x[index] * gravity;
      fy[index] -= y[index] * gravity;
      const magnitude = Math.max(1, Math.hypot(fx[index], fy[index]));
      const step = Math.min(maxStep, magnitude);
      x[index] += (fx[index] / magnitude) * step;
      y[index] += (fy[index] / magnitude) * step;
    }
  }

  // Force simulation preserves topology; this final proximity-preserving pass
  // makes card geometry a hard constraint instead of a visual suggestion.
  const overlapCount = Math.min(count, 360);
  for (let pass = 0; pass < 160; pass += 1) {
    let moved = false;
    for (let left = 0; left < overlapCount; left += 1) {
      for (let right = left + 1; right < overlapCount; right += 1) {
        let dx = x[right] - x[left];
        let dy = y[right] - y[left];
        if (dx === 0 && dy === 0) {
          dx = (stableHash(ordered[right].id) % 13) + 1;
          dy = -((stableHash(ordered[left].id) % 11) + 1);
        }
        const requiredX = (sizes[left].width + sizes[right].width) / 2 + 22;
        const requiredY = (sizes[left].height + sizes[right].height) / 2 + 22;
        const overlapX = requiredX - Math.abs(dx);
        const overlapY = requiredY - Math.abs(dy);
        if (overlapX <= 0 || overlapY <= 0) continue;
        moved = true;
        const leftPinned = left === pinned;
        const rightPinned = right === pinned;
        if (overlapX / requiredX < overlapY / requiredY) {
          const direction = dx >= 0 ? 1 : -1;
          const displacement = overlapX + 1;
          if (!leftPinned)
            x[left] -= direction * displacement * (rightPinned ? 1 : 0.5);
          if (!rightPinned)
            x[right] += direction * displacement * (leftPinned ? 1 : 0.5);
        } else {
          const direction = dy >= 0 ? 1 : -1;
          const displacement = overlapY + 1;
          if (!leftPinned)
            y[left] -= direction * displacement * (rightPinned ? 1 : 0.5);
          if (!rightPinned)
            y[right] += direction * displacement * (leftPinned ? 1 : 0.5);
        }
      }
    }
    if (!moved) break;
  }

  return ordered.map((node, index) => ({
    ...node,
    x: Math.round(x[index] * 10) / 10,
    y: Math.round(y[index] * 10) / 10,
  }));
}

function clusterChildren(nodes: ResearchMapNode[]) {
  const seen = new Set<string>();
  return [...nodes]
    .sort((left, right) => left.label.localeCompare(right.label))
    .filter((node) => {
      const key = `${node.type}\u0000${node.label}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 4)
    .map((node) => ({ id: node.id, label: node.label, type: node.type }));
}

function overviewNodePriority(node: ResearchMapNode): number {
  const priorities: Record<string, number> = {
    CodexThread: 0,
    Repository: 0,
    FileVersion: 1,
    CodeFileFocus: 1,
    CodeClass: 2,
    CodeFunction: 3,
    CodeMethod: 4,
  };
  return priorities[node.type] ?? 6;
}

function layoutOverviewNetwork(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
): ResearchMapNode[] {
  const sessions = nodes
    .filter((node) => node.type === "CodexThread")
    .sort((left, right) => left.label.localeCompare(right.label));
  if (!sessions.length) return layoutNodes(nodes, edges);

  const sessionIds = new Set(sessions.map((node) => node.id));
  const codeNodes = nodes.filter((node) => node.domain === "code");
  const codeIds = new Set(codeNodes.map((node) => node.id));
  const fileWeights = new Map<string, Map<string, number>>();
  edges.forEach((edge) => {
    if (
      !sessionIds.has(edge.source) ||
      !codeIds.has(edge.target) ||
      !["changed_path_maps_to", "changed_symbol_maps_to"].includes(
        edge.predicate,
      )
    )
      return;
    const bySession = fileWeights.get(edge.target) || new Map<string, number>();
    bySession.set(
      edge.source,
      (bySession.get(edge.source) || 0) + (edge.occurrences || 1),
    );
    fileWeights.set(edge.target, bySession);
  });
  const fileOwner = new Map<string, string>();
  fileWeights.forEach((weights, fileId) => {
    const owner = [...weights.entries()].sort(
      ([leftId, leftWeight], [rightId, rightWeight]) =>
        rightWeight - leftWeight || leftId.localeCompare(rightId),
    )[0]?.[0];
    if (owner) fileOwner.set(fileId, owner);
  });
  const filesBySession = new Map<string, ResearchMapNode[]>();
  sessions.forEach((session) => filesBySession.set(session.id, []));
  codeNodes.forEach((node) => {
    const owner = fileOwner.get(node.id);
    if (owner) filesBySession.get(owner)?.push(node);
  });
  filesBySession.forEach((files) => {
    files.sort((left, right) => {
      const leftConnections = fileWeights.get(left.id)?.size || 0;
      const rightConnections = fileWeights.get(right.id)?.size || 0;
      return (
        rightConnections - leftConnections ||
        String(left.path || left.label).localeCompare(
          String(right.path || right.label),
        )
      );
    });
  });
  sessions.sort((left, right) => {
    const leftFiles = filesBySession.get(left.id)?.length || 0;
    const rightFiles = filesBySession.get(right.id)?.length || 0;
    return rightFiles - leftFiles || left.label.localeCompare(right.label);
  });

  const positions = new Map<string, { x: number; y: number }>();
  let cursorY = 0;
  sessions.forEach((session) => {
    const files = filesBySession.get(session.id) || [];
    const sharedFiles = files.filter(
      (file) => (fileWeights.get(file.id)?.size || 0) > 1,
    );
    const ownedFiles = files.filter(
      (file) => (fileWeights.get(file.id)?.size || 0) <= 1,
    );
    const rowCount = Math.max(
      1,
      Math.ceil(ownedFiles.length / 3),
      sharedFiles.length,
    );
    const blockHeight = Math.max(116, rowCount * 112);
    const centerY = cursorY + blockHeight / 2;
    positions.set(session.id, { x: -140, y: centerY });
    ownedFiles.forEach((file, index) => {
      const column = index % 3;
      const row = Math.floor(index / 3);
      positions.set(file.id, {
        x: 190 + column * 250,
        y: cursorY + 56 + row * 112,
      });
    });
    sharedFiles.forEach((file, index) => {
      positions.set(file.id, {
        x: 940,
        y: cursorY + 56 + index * 112,
      });
    });
    cursorY += blockHeight + 64;
  });

  const unownedCode = codeNodes.filter((node) => !fileOwner.has(node.id));
  if (unownedCode.length) {
    const startY = cursorY + 40;
    unownedCode.forEach((node, index) => {
      positions.set(node.id, {
        x: 190 + (index % 3) * 250,
        y: startY + Math.floor(index / 3) * 112,
      });
    });
    cursorY = startY + Math.ceil(unownedCode.length / 3) * 112;
  }

  const verticalOffset = cursorY / 2;
  const project = nodes.find((node) => node.type === "ResearchProject");
  if (project) positions.set(project.id, { x: -500, y: 0 });
  const workspaceContext = nodes.filter(
    (node) => node.domain === "workspace" && node.id !== project?.id,
  );
  workspaceContext.forEach((node, index) => {
    positions.set(node.id, {
      x: -770,
      y: (index - (workspaceContext.length - 1) / 2) * 128,
    });
  });
  const supporting = nodes.filter(
    (node) => !["workspace", "codex", "code"].includes(node.domain),
  );
  supporting.forEach((node, index) => {
    positions.set(node.id, {
      x: 1210,
      y: (index - (supporting.length - 1) / 2) * 128,
    });
  });

  return nodes.map((node) => {
    const position = positions.get(node.id);
    if (!position) return node;
    const recenter =
      node.domain === "codex" || node.domain === "code" ? verticalOffset : 0;
    return {
      ...node,
      x: position.x,
      y: Math.round((position.y - recenter) * 10) / 10,
    };
  });
}

/**
 * The research overview is a bounded relationship network. Session nodes are
 * impact sources, governed code files are impact targets, and workspace nodes
 * provide context anchors. It deliberately avoids source-column frames: the
 * topology itself is the primary visual structure.
 */
export function buildResearchOverview(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
): { nodes: ResearchMapNode[]; edges: ResearchMapEdge[] } {
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const degree = new Map<string, number>();
  const impactDegree = new Map<string, number>();
  edges.forEach((edge) => {
    const source = nodesById.get(edge.source);
    const target = nodesById.get(edge.target);
    if (!source || !target) return;
    const occurrences = edge.occurrences || 1;
    degree.set(source.id, (degree.get(source.id) || 0) + occurrences);
    degree.set(target.id, (degree.get(target.id) || 0) + occurrences);
    if (source.domain === target.domain) return;
    const weight = [
      "changed_path_maps_to",
      "changed_symbol_maps_to",
      "implemented_by",
      "produced_by",
      "validated_by",
    ].includes(edge.predicate)
      ? 6 * occurrences
      : occurrences;
    impactDegree.set(source.id, (impactDegree.get(source.id) || 0) + weight);
    impactDegree.set(target.id, (impactDegree.get(target.id) || 0) + weight);
  });

  const directImpactEdges = edges.filter((edge) => {
    if (
      !["changed_path_maps_to", "changed_symbol_maps_to"].includes(
        edge.predicate,
      )
    )
      return false;
    const source = nodesById.get(edge.source);
    const target = nodesById.get(edge.target);
    return (
      source?.type === "CodexThread" &&
      target?.domain === "code" &&
      Boolean(target.path && isPresentableCodePath(target.path))
    );
  });
  const directSessionIds = new Set(
    directImpactEdges.map((edge) => edge.source),
  );
  const directCodeIds = new Set(directImpactEdges.map((edge) => edge.target));
  const ranked = (left: ResearchMapNode, right: ResearchMapNode) =>
    (impactDegree.get(right.id) || 0) - (impactDegree.get(left.id) || 0) ||
    (degree.get(right.id) || 0) - (degree.get(left.id) || 0) ||
    overviewNodePriority(left) - overviewNodePriority(right) ||
    left.label.localeCompare(right.label);

  const workspaceNodes = nodes
    .filter((node) => node.domain === "workspace")
    .sort((left, right) => {
      const order = (node: ResearchMapNode) =>
        node.type === "ResearchProject"
          ? 0
          : node.type === "ResearchTopic"
            ? 1
            : node.type === "ResearchIteration"
              ? 2
              : 3;
      return order(left) - order(right) || ranked(left, right);
    })
    .slice(0, 7);
  const project =
    workspaceNodes.find((node) => node.type === "ResearchProject") ||
    workspaceNodes[0];
  const sessions = nodes
    .filter((node) => node.type === "CodexThread")
    .sort(
      (left, right) =>
        Number(directSessionIds.has(right.id)) -
          Number(directSessionIds.has(left.id)) || ranked(left, right),
    )
    .slice(0, 10);
  const codeCandidates = nodes
    .filter(
      (node) =>
        node.domain === "code" &&
        Boolean(node.path && isPresentableCodePath(node.path)),
    )
    .sort(
      (left, right) =>
        Number(directCodeIds.has(right.id)) -
          Number(directCodeIds.has(left.id)) || ranked(left, right),
    );
  const codeNodes: ResearchMapNode[] = [];
  const seenCodePaths = new Set<string>();
  codeCandidates.forEach((node) => {
    const key = `${node.repositoryId || "repository"}\u0000${node.path}`;
    if (seenCodePaths.has(key) || codeNodes.length >= 14) return;
    seenCodePaths.add(key);
    codeNodes.push(node);
  });
  const supportingNodes = domainOrder.flatMap((domain) => {
    if (["workspace", "codex", "code"].includes(domain)) return [];
    return nodes
      .filter((node) => node.domain === domain)
      .sort(ranked)
      .slice(0, 3);
  });
  const overviewNodes = dedupeNodes([
    ...workspaceNodes,
    ...sessions,
    ...codeNodes,
    ...supportingNodes,
  ]).map((node) => ({
    ...node,
    groupKey: null,
    width: node.type === "ResearchProject" ? 248 : 220,
    height: node.type === "ResearchProject" ? 96 : 88,
  }));
  const visibleIds = new Set(overviewNodes.map((node) => node.id));
  const actualEdges = edges.filter(
    (edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target),
  );
  const scopeTargets = [
    ...sessions,
    ...codeNodes.filter(
      (node) =>
        !actualEdges.some(
          (edge) => edge.source === node.id || edge.target === node.id,
        ),
    ),
    ...supportingNodes.filter(
      (node) =>
        !actualEdges.some(
          (edge) => edge.source === node.id || edge.target === node.id,
        ),
    ),
  ];
  const scopeEdges: ResearchMapEdge[] = project
    ? scopeTargets.map((node) => ({
        id: `overview-scope://${project.id}/${node.id}`,
        source: project.id,
        predicate:
          node.domain === "codex" ? "scopes_session" : "contains_source",
        target: node.id,
        derivation: "project_scope",
        confidence: 1,
        review_status: "scope",
        domain: node.domain,
        label: node.domain === "codex" ? "项目会话" : "项目范围",
        tone: node.domain,
        structural: true,
        scope: true,
      }))
    : [];
  const overviewEdges = keepEdgesWithNodes(
    overviewNodes,
    aggregateVisibleRelations([...actualEdges, ...scopeEdges]),
  );
  return {
    nodes: layoutOverviewNetwork(overviewNodes, overviewEdges),
    edges: overviewEdges,
  };
}

function highestReviewStatus(edges: ResearchMapEdge[]): string {
  return edges.reduce(
    (status, edge) =>
      edgeReviewPriority(edge.review_status) > edgeReviewPriority(status)
        ? edge.review_status || status
        : status,
    "confirmed",
  );
}

function topicDescendants(
  topic: ResearchMapNode,
  workspaceNodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
): ResearchMapNode[] {
  const workspaceIds = new Set(workspaceNodes.map((node) => node.id));
  const found = new Set<string>([topic.id]);
  for (let depth = 0; depth < 3; depth += 1) {
    edges.forEach((edge) => {
      if (found.has(edge.source) && workspaceIds.has(edge.target))
        found.add(edge.target);
    });
  }
  return workspaceNodes.filter((node) => found.has(node.id));
}

function boardCluster(input: {
  id: string;
  type: string;
  domain: ResearchDomain;
  label: string;
  meta: string;
  status: string;
  x: number;
  y: number;
  children: ResearchMapNode[];
}): ResearchMapNode {
  return {
    id: input.id,
    type: input.type,
    domain: input.domain,
    label: input.label,
    meta: input.meta,
    locator: input.id,
    status: input.status,
    x: input.x,
    y: input.y,
    source: "cluster",
    children: clusterChildren(input.children),
  };
}

/**
 * A topic whiteboard is a truthful bridge between the research hierarchy and
 * project-scoped evidence. Scope links are deliberately distinct from actual
 * evidence links, so a project association is never presented as a confirmed
 * scientific relation.
 */
export function buildTopicBoard(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  topicId?: string | null,
): {
  nodes: ResearchMapNode[];
  edges: ResearchMapEdge[];
  topicId: string | null;
} {
  const workspaceNodes = nodes.filter((node) => node.domain === "workspace");
  const topics = workspaceNodes.filter((node) => node.type === "ResearchTopic");
  const topic = topics.find((node) => node.id === topicId) || topics[0];
  if (!topic) return { nodes: [], edges: [], topicId: null };

  const branch = topicDescendants(topic, workspaceNodes, edges);
  const iterations = branch.filter((node) => node.type === "ResearchIteration");
  const workItems = branch.filter((node) => node.type.endsWith("Work"));
  const positionedBranch = branch.map((node) => {
    if (node.id === topic.id) return { ...node, x: 0, y: -190 };
    const iterationIndex = iterations.findIndex(
      (candidate) => candidate.id === node.id,
    );
    if (iterationIndex >= 0) {
      return {
        ...node,
        x: (iterationIndex - (iterations.length - 1) / 2) * 330,
        y: 10,
      };
    }
    const workIndex = workItems.findIndex(
      (candidate) => candidate.id === node.id,
    );
    return {
      ...node,
      x: (workIndex - (workItems.length - 1) / 2) * 330,
      y: 205,
    };
  });

  const codexNodes = nodes.filter(
    (node) => node.domain === "codex" && node.type === "CodexThread",
  );
  const codeNodes = nodes.filter((node) => node.domain === "code");
  const bindingEdges = edges.filter((edge) => {
    const source = nodes.find((node) => node.id === edge.source);
    const target = nodes.find((node) => node.id === edge.target);
    return (
      edge.predicate === "changed_path_maps_to" &&
      source?.domain === "codex" &&
      target?.domain === "code"
    );
  });
  const uniqueFiles = new Set(bindingEdges.map((edge) => edge.target)).size;
  const pendingBindings = bindingEdges.reduce(
    (total, edge) =>
      total +
      (edge.review_status === "unreviewed" || edge.review_status === "pending"
        ? edge.occurrences || 1
        : 0),
    0,
  );
  const evidenceBaseY = workItems.length ? 420 : iterations.length ? 225 : 80;
  const codexCluster = codexNodes.length
    ? boardCluster({
        id: `board://topic/${encodeURIComponent(topic.id)}/codex`,
        type: "TopicCodexScope",
        domain: "codex",
        label: "研发会话",
        meta: `${codexNodes.length} 个会话 · ${bindingEdges.length} 组代码关联`,
        status: pendingBindings ? "unreviewed" : "linked",
        x: -220,
        y: evidenceBaseY,
        children: codexNodes,
      })
    : null;
  const codeCluster = codeNodes.length
    ? boardCluster({
        id: `board://topic/${encodeURIComponent(topic.id)}/code`,
        type: "TopicCodeScope",
        domain: "code",
        label: "代码证据",
        meta: `${uniqueFiles || new Set(codeNodes.map(codeFileKey)).size} 个文件 · ${pendingBindings} 条待复核`,
        status: pendingBindings ? "unreviewed" : "indexed",
        x: 220,
        y: evidenceBaseY,
        children: codeNodes,
      })
    : null;
  const sourceClusters = [codexCluster, codeCluster].filter(
    (node): node is ResearchMapNode => Boolean(node),
  );
  const anchor =
    positionedBranch.find((node) => node.type.endsWith("Work")) ||
    positionedBranch.find((node) => node.type === "ResearchIteration") ||
    positionedBranch.find((node) => node.id === topic.id)!;
  const scopeEdges: ResearchMapEdge[] = sourceClusters.map((cluster) => ({
    id: `topic-scope://${topic.id}/${cluster.domain}`,
    source: anchor.id,
    predicate: cluster.domain === "codex" ? "scopes_session" : "scopes_code",
    target: cluster.id,
    derivation: "project_scope",
    confidence: 1,
    review_status: "scope",
    domain: cluster.domain,
    label: cluster.domain === "codex" ? "项目会话" : "项目代码",
    tone: cluster.domain,
    structural: true,
    scope: true,
  }));
  const crossSourceEdge =
    codexCluster && codeCluster && bindingEdges.length
      ? [
          {
            id: `topic-binding://${topic.id}`,
            source: codexCluster.id,
            predicate: "changed_path_maps_to",
            target: codeCluster.id,
            derivation: "binding_rollup",
            confidence: Math.max(
              ...bindingEdges.map((edge) => Number(edge.confidence || 0)),
            ),
            review_status: highestReviewStatus(bindingEdges),
            domain: "code",
            label: "变更映射",
            tone: "code" as const,
            occurrences: bindingEdges.reduce(
              (total, edge) => total + (edge.occurrences || 1),
              0,
            ),
          },
        ]
      : [];
  const branchIds = new Set(positionedBranch.map((node) => node.id));
  const branchEdges = edges.filter(
    (edge) => branchIds.has(edge.source) && branchIds.has(edge.target),
  );
  const boardNodes = [...positionedBranch, ...sourceClusters];
  return {
    topicId: topic.id,
    nodes: boardNodes,
    edges: keepEdgesWithNodes(
      boardNodes,
      aggregateVisibleRelations([
        ...branchEdges,
        ...scopeEdges,
        ...crossSourceEdge,
      ]),
    ),
  };
}

function descendantsOf(
  rootId: string,
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  maxDepth: number,
): ResearchMapNode[] {
  const ids = new Set<string>([rootId]);
  for (let depth = 0; depth < maxDepth; depth += 1) {
    edges.forEach((edge) => {
      if (ids.has(edge.source)) ids.add(edge.target);
    });
  }
  const allowed = new Set(nodes.map((node) => node.id));
  return [...ids]
    .filter((id) => allowed.has(id))
    .map((id) => nodes.find((node) => node.id === id)!)
    .filter(Boolean);
}

function turnOrdinal(node: ResearchMapNode): number {
  const match = node.id.match(/\/turn\/([^/]+)/);
  if (!match) return Number.MAX_SAFE_INTEGER;
  const version = Date.parse(node.version || "");
  return Number.isFinite(version) ? version : stableHash(match[1]);
}

export type CodexOverviewStats = {
  totalSessions: number;
  visibleSessions: number;
  turns: number;
  fileChanges: number;
  commands: number;
};

/**
 * Build the no-focus Codex board from the session index rather than a guide
 * card. Sessions stay bounded by the caller's page, but every visible card is
 * a real thread that can be selected or drilled into.
 */
export function buildCodexOverviewBoard(
  sessions: CodexSession[],
  totalSessions = sessions.length,
): {
  nodes: ResearchMapNode[];
  edges: ResearchMapEdge[];
  threadId: null;
  stats: CodexOverviewStats;
} {
  const ordered = [...sessions].sort(
    (left, right) =>
      String(right.updated_at || "").localeCompare(
        String(left.updated_at || ""),
      ) || left.thread_id.localeCompare(right.thread_id),
  );
  const categories = [
    {
      key: "active",
      label: "正在进行",
      matches: (session: CodexSession) =>
        ["active", "live", "running", "in_progress"].includes(
          String(session.status || "").toLowerCase(),
        ),
    },
    {
      key: "changed",
      label: "产生代码变更",
      matches: (session: CodexSession) => session.file_change_count > 0,
    },
    {
      key: "recorded",
      label: "其他研发记录",
      matches: () => true,
    },
  ];
  const assigned = new Set<string>();
  const groups = categories.flatMap((category) => {
    const items = ordered.filter((session) => {
      if (assigned.has(session.thread_id) || !category.matches(session))
        return false;
      assigned.add(session.thread_id);
      return true;
    });
    return items.length ? [{ ...category, items }] : [];
  });
  const nodes: ResearchMapNode[] = [];
  let rowTop = 0;
  for (let index = 0; index < groups.length; index += 2) {
    const row = groups.slice(index, index + 2).map((group) => {
      const columns = Math.min(2, Math.max(1, group.items.length));
      const rows = Math.ceil(group.items.length / columns);
      return {
        ...group,
        columns,
        rows,
        width: columns === 1 ? 340 : 620,
        height: 112 + rows * 118,
      };
    });
    const rowHeight = Math.max(...row.map((group) => group.height));
    row.forEach((group, column) => {
      const frameX =
        row.length === 1 ? 0 : (column - (row.length - 1) / 2) * 710;
      const frameY = rowTop + rowHeight / 2;
      const frameId = `frame://codex/${group.key}`;
      nodes.push({
        id: frameId,
        type: "SessionImpactFrame",
        domain: "codex",
        label: group.label,
        meta: `${group.items.length} 个会话`,
        locator: `board://codex/${group.key}`,
        status: "scope",
        width: group.width,
        height: group.height,
        x: frameX,
        y: frameY,
        source: "cluster",
        metadata: { session_count: group.items.length },
      });
      const left = frameX - group.width / 2 + 24;
      const top = frameY - group.height / 2 + 88;
      const cardWidth = 270;
      const gap =
        (group.width - 48 - group.columns * cardWidth) /
        Math.max(1, group.columns - 1);
      group.items.forEach((session, sessionIndex) => {
        const threadId = session.thread_id.startsWith("codex://thread/")
          ? session.thread_id
          : `codex://thread/${session.thread_id}`;
        const sessionColumn = sessionIndex % group.columns;
        const sessionRow = Math.floor(sessionIndex / group.columns);
        nodes.push({
          id: threadId,
          type: "CodexThread",
          domain: "codex",
          label: session.title || `Codex 会话 ${session.thread_id.slice(0, 8)}`,
          meta: `${session.turn_count} 轮 · ${session.file_change_count} 个文件变更 · ${session.command_count} 条命令`,
          locator: threadId,
          version: session.updated_at,
          status: session.status,
          width: cardWidth,
          height: 92,
          x: left + cardWidth / 2 + sessionColumn * (cardWidth + gap),
          y: top + 46 + sessionRow * 118,
          source: "graph",
          groupKey: frameId,
          metadata: {
            project_id: session.project_id,
            cwd: session.cwd,
            item_count: session.item_count,
            turn_count: session.turn_count,
            file_change_count: session.file_change_count,
            command_count: session.command_count,
            ...(session.metadata || {}),
          },
        });
      });
    });
    rowTop += rowHeight + 82;
  }
  return {
    nodes,
    edges: [],
    threadId: null,
    stats: {
      totalSessions,
      visibleSessions: sessions.length,
      turns: sessions.reduce((total, session) => total + session.turn_count, 0),
      fileChanges: sessions.reduce(
        (total, session) => total + session.file_change_count,
        0,
      ),
      commands: sessions.reduce(
        (total, session) => total + session.command_count,
        0,
      ),
    },
  };
}

/**
 * Codex summary and focused session whiteboards share the same source graph.
 * Focused mode lays turns left-to-right as a serial timeline, while keeping
 * actual goals, replies, episodes and mapped code files connected beneath it.
 */
export function buildCodexBoard(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  focusThreadId?: string | null,
): {
  nodes: ResearchMapNode[];
  edges: ResearchMapEdge[];
  threadId: string | null;
} {
  const threads = nodes
    .filter((node) => node.domain === "codex" && node.type === "CodexThread")
    .sort((left, right) => left.label.localeCompare(right.label));
  const focused = focusThreadId
    ? threads.find((thread) => thread.id === normalizeThreadId(focusThreadId))
    : null;

  if (!focused) {
    const root: ResearchMapNode = {
      id: "board://codex/root",
      type: "CodexWorkspaceBoard",
      domain: "codex",
      label: "选择会话查看代码影响",
      meta: "会话列表保留在研发会话页；这里按需展开轮次、证据与代码变动",
      locator: "board://codex",
      status: "scope",
      x: 0,
      y: 0,
      source: "cluster",
    };
    return {
      threadId: null,
      nodes: [root],
      edges: [],
    };
  }

  const descendants = descendantsOf(focused.id, nodes, edges, 3).filter(
    (node) => node.domain === "codex",
  );
  const turns = descendants
    .filter((node) => node.type === "CodexTurn")
    .sort((left, right) => turnOrdinal(left) - turnOrdinal(right));
  const turnIds = new Set(turns.map((turn) => turn.id));
  const itemNodes = descendants.filter(
    (node) => node.id !== focused.id && !turnIds.has(node.id),
  );
  const positionedThread = {
    ...focused,
    meta: [
      `${turns.length} 个执行轮次`,
      focused.version
        ? new Date(focused.version).toLocaleDateString("zh-CN")
        : "Codex 研发记录",
    ].join(" · "),
    x: -520,
    y: 0,
  };
  const positionedTurns = turns.map((turn, index) => ({
    ...turn,
    label: `轮次 ${index + 1}`,
    meta: turn.version
      ? `${new Date(turn.version).toLocaleString("zh-CN", {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        })} · Codex 执行`
      : turn.meta,
    x: -185 + index * 340,
    y: 0,
  }));
  const positionedItems: ResearchMapNode[] = [];
  positionedTurns.forEach((turn, turnIndex) => {
    const priorities: Record<string, number> = {
      UserGoal: 0,
      DevelopmentEpisode: 1,
      FileChange: 2,
      Patch: 3,
      ValidationResult: 4,
      CommandExecution: 5,
      AgentMessage: 6,
    };
    const items = itemNodes
      .filter((item) =>
        edges.some(
          (edge) => edge.source === turn.id && edge.target === item.id,
        ),
      )
      .sort(
        (left, right) =>
          (priorities[left.type] ?? 9) - (priorities[right.type] ?? 9) ||
          left.id.localeCompare(right.id),
      )
      .slice(0, 6);
    items.forEach((item, index) => {
      const itemLabel =
        {
          UserGoal: `用户目标 · 轮次 ${turnIndex + 1}`,
          AgentMessage: `Codex 回复 · 轮次 ${turnIndex + 1}`,
          DevelopmentEpisode: `开发片段 · 轮次 ${turnIndex + 1}`,
        }[item.type] || item.label;
      positionedItems.push({
        ...item,
        label: itemLabel,
        x: turn.x + (index - (items.length - 1) / 2) * 250,
        y: 205 + Math.floor(index / 2) * 155,
      });
    });
  });

  const itemIds = new Set(positionedItems.map((node) => node.id));
  const bindingEdges = edges.filter(
    (edge) =>
      (edge.source === focused.id || itemIds.has(edge.source)) &&
      ["changed_path_maps_to", "changed_symbol_maps_to"].includes(
        edge.predicate,
      ),
  );
  const groupedTargets = new Map<
    string,
    { node: ResearchMapNode; edges: ResearchMapEdge[] }
  >();
  bindingEdges.forEach((edge) => {
    const target = nodes.find((node) => node.id === edge.target);
    if (!target) return;
    const key = codeFileKey(target);
    const current = groupedTargets.get(key);
    groupedTargets.set(key, {
      node: current?.node || target,
      edges: [...(current?.edges || []), edge],
    });
  });
  positionedItems
    .filter(
      (item) =>
        ["FileChange", "Patch"].includes(item.type) &&
        item.path &&
        isPresentableCodePath(item.path),
    )
    .forEach((item) => {
      const path = String(item.path);
      const current = groupedTargets.get(path);
      const evidenceEdge: ResearchMapEdge = {
        id: `codex-file-evidence://${item.id}/${encodeURIComponent(path)}`,
        source: item.id,
        predicate: "changed_path_maps_to",
        target: `board://codex/file/${encodeURIComponent(path)}`,
        derivation: "session_file_change",
        confidence: 1,
        review_status: "confirmed",
        domain: "codex",
        label: "修改代码",
        tone: "code",
      };
      groupedTargets.set(path, {
        node: current?.node || item,
        edges: [...(current?.edges || []), evidenceEdge],
      });
    });
  const visibleTargetGroups = [...groupedTargets.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .slice(0, 6);
  const codeStartX = positionedTurns.length
    ? positionedTurns.at(-1)!.x + 400
    : 120;
  const codeGroups = visibleTargetGroups.map(([path, target], index) => {
    const relatedEntities = nodes.filter(
      (node) => node.domain === "code" && codeFileKey(node) === path,
    );
    return {
      ...boardCluster({
        id: `board://codex/file/${encodeURIComponent(path)}`,
        type: "CodeFileGroup",
        domain: "code",
        label: path.split("/").at(-1) || path,
        meta: path,
        status: highestReviewStatus(target.edges),
        x: codeStartX + (index % 2) * 320,
        y: -120 + Math.floor(index / 2) * 185,
        children: relatedEntities,
      }),
      version: target.node.version,
      locator:
        target.node.domain === "code"
          ? target.node.locator
          : `code-change://${encodeURIComponent(path)}`,
      path,
    };
  });
  const codeEdges: ResearchMapEdge[] = visibleTargetGroups.map(
    ([path, target]) => ({
      id: `codex-file-binding://${focused.id}/${encodeURIComponent(path)}`,
      source:
        target.edges.find((edge) => itemIds.has(edge.source))?.source ||
        focused.id,
      predicate: "changed_path_maps_to",
      target: `board://codex/file/${encodeURIComponent(path)}`,
      derivation: "binding_rollup",
      confidence: Math.max(
        ...target.edges.map((edge) => Number(edge.confidence || 0)),
      ),
      review_status: highestReviewStatus(target.edges),
      domain: "code",
      label: "修改代码",
      tone: "code",
      occurrences: target.edges.reduce(
        (total, edge) => total + (edge.occurrences || 1),
        0,
      ),
    }),
  );
  const focusedIds = new Set([
    focused.id,
    ...turns.map((node) => node.id),
    ...positionedItems.map((node) => node.id),
  ]);
  const actualEdges = edges.filter(
    (edge) =>
      focusedIds.has(edge.source) &&
      focusedIds.has(edge.target) &&
      edge.predicate !== "HAS_TURN",
  );
  const firstTurnEdge = turns.length
    ? edges.find(
        (edge) =>
          edge.source === focused.id &&
          edge.target === turns[0].id &&
          edge.predicate === "HAS_TURN",
      )
    : null;
  const serialEdges: ResearchMapEdge[] = turns
    .slice(0, -1)
    .map((turn, index) => ({
      id: `codex-turn-order://${turn.id}/${turns[index + 1].id}`,
      source: turn.id,
      predicate: "continues_to",
      target: turns[index + 1].id,
      derivation: "timeline_order",
      confidence: 1,
      review_status: "confirmed",
      domain: "codex",
      label: "下一轮",
      tone: "codex",
      structural: true,
    }));
  const boardNodes = [
    positionedThread,
    ...positionedTurns,
    ...positionedItems,
    ...codeGroups,
  ];
  return {
    threadId: focused.id,
    nodes: boardNodes,
    edges: keepEdgesWithNodes(
      boardNodes,
      aggregateVisibleRelations([
        ...actualEdges,
        ...(firstTurnEdge ? [firstTurnEdge] : []),
        ...serialEdges,
        ...codeEdges,
      ]),
    ),
  };
}

function codeFileKey(node: ResearchMapNode): string {
  if (node.path) return node.path;
  const decoded = decodeURIComponent(node.locator || "");
  const withoutFragment = decoded.split("#")[0];
  const versionedPath =
    withoutFragment.match(/@[a-f0-9]{7,}[/:](.+)$/i)?.[1] ||
    node.meta.match(/@[a-f0-9]{7,}[/:](.+)$/i)?.[1];
  if (versionedPath) return versionedPath;
  const metaPath = node.meta.split(" · ")[0].trim();
  return metaPath || node.label;
}

function directoryKey(path: string): string {
  const parts = path.split("/").filter(Boolean);
  if (parts.length <= 1) return "仓库根目录";
  return parts.slice(0, -1).join("/");
}

function codeEntityKind(node: ResearchMapNode): boolean {
  return [
    "FileVersion",
    "CodeSymbol",
    "CodeClass",
    "CodeMethod",
    "CodeFunction",
    "CodeInterface",
    "CodeModule",
  ].includes(node.type);
}

function isPresentableCodePath(path: string): boolean {
  const value = String(path || "").trim();
  if (!value || /[\u0000-\u001f\\]/.test(value)) return false;
  if (
    value.startsWith("/") ||
    value.startsWith("../") ||
    value.includes("/../") ||
    value.includes("//") ||
    /^%2f/i.test(value)
  )
    return false;
  const basename = value.split("/").at(-1) || "";
  if (
    /(?:^|\.)(?:sqlite(?:3)?|db|wal|shm|pyc|pyo|pem|key|p12|pfx|env)$/i.test(
      basename,
    ) ||
    /(?:^|\/)(?:\.env(?:\.|$)|secrets?(?:\.|\/)|credentials?(?:\.|\/))/i.test(
      value,
    )
  )
    return false;
  return (
    /\.[a-z0-9][a-z0-9._-]*$/i.test(basename) ||
    ["Dockerfile", "Makefile", "Procfile"].includes(basename)
  );
}

function codeChildSummary(nodes: ResearchMapNode[]) {
  const seen = new Set<string>();
  return [...nodes]
    .filter((node) => node.type !== "FileVersion")
    .sort(
      (left, right) =>
        Number(left.startLine || Number.MAX_SAFE_INTEGER) -
          Number(right.startLine || Number.MAX_SAFE_INTEGER) ||
        left.label.localeCompare(right.label),
    )
    .filter((node) => {
      const key = `${node.type}\u0000${node.qualifiedName || node.label}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 28)
    .map((node) => ({
      id: node.id,
      label: node.label,
      type: node.type,
      meta: [node.language, node.startLine ? `L${node.startLine}` : ""]
        .filter(Boolean)
        .join(" · "),
    }));
}

type CodeResponsibility = {
  key: string;
  label: string;
  description: string;
};

const codeObjectLabels: Array<[RegExp, string]> = [
  [/work[_\s-]?items?/, "工作任务"],
  [/iterations?/, "研究迭代"],
  [/projects?/, "科研项目"],
  [/topics?/, "研究主题"],
  [/relations?/, "关系与证据"],
  [/workflows?/, "工作流"],
  [/executions?/, "执行记录"],
  [/evidence/, "研究证据"],
  [/documents?/, "科研文档"],
  [/experiments?/, "实验"],
  [/sessions?|threads?/, "研发会话"],
];

function codeObjectLabel(value: string): string {
  const normalized = value.toLowerCase();
  return (
    codeObjectLabels.find(([pattern]) => pattern.test(normalized))?.[1] ||
    humanizeSearchTitle(
      normalized
        .replace(/^_+/, "")
        .replace(
          /^(create|update|delete|remove|list|get|find|search|require|ensure|transition|link|unlink|review|audit|load|save)_?/,
          "",
        ) || value,
    )
  );
}

function codeResponsibility(node: ResearchMapNode): CodeResponsibility {
  // Responsibility is inferred from the local symbol name. Fully-qualified
  // names include package segments such as `evidence_rag`, which would
  // otherwise misclassify every unrelated helper as "research evidence".
  const value = node.label.toLowerCase();
  const match = codeObjectLabels.find(([pattern]) => pattern.test(value));
  if (match) {
    const key = match[1];
    return {
      key,
      label: key,
      description: `集中处理${key}的校验、状态变更与数据访问。`,
    };
  }
  if (/(audit|auth|permission|acl|token|security)/.test(value)) {
    return {
      key: "governance",
      label: "权限与审计",
      description: "负责权限边界、操作校验与审计记录。",
    };
  }
  if (/(dashboard|summary|stats|search|query|list|find|report)/.test(value)) {
    return {
      key: "query",
      label: "查询与汇总",
      description: "提供列表、检索、统计与界面汇总所需的读取入口。",
    };
  }
  if (
    /^_+|(__init__|helper|util|normalize|decode|encode|resolve)/.test(
      node.label,
    )
  ) {
    return {
      key: "internal",
      label: "内部校验与工具",
      description: "封装内部校验、对象解析与可复用辅助逻辑。",
    };
  }
  if (
    /(create|update|delete|remove|transition|link|unlink|review|save)/.test(
      value,
    )
  ) {
    return {
      key: "commands",
      label: "写入与状态变更",
      description: "集中处理创建、修改、关系绑定与状态迁移。",
    };
  }
  return {
    key: "core",
    label: "核心业务能力",
    description: "承载该文件中未归入专门领域的主要业务入口。",
  };
}

function metadataStrings(node: ResearchMapNode, key: string): string[] {
  const value = node.metadata?.[key];
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (item): item is string =>
        typeof item === "string" && Boolean(item.trim()),
    )
    .map((item) => item.trim());
}

export function describeCodeNode(node: ResearchMapNode): string {
  const supplied = node.metadata?.responsibility_description;
  if (typeof supplied === "string" && supplied.trim()) return supplied.trim();
  if (node.type === "CodeResponsibilityGroup") {
    return node.meta.split(" · ")[0] || "按职责聚合的代码入口。";
  }
  const path = String(node.path || node.meta || "").toLowerCase();
  const label = node.label.toLowerCase();
  if (
    [
      "CodeFileContainer",
      "CodeFileFocus",
      "CodeFileGroup",
      "FileVersion",
    ].includes(node.type)
  ) {
    const scope = path.includes("/workspace/")
      ? "科研项目、主题、迭代与任务"
      : path.includes("/codex")
        ? "Codex 执行、会话与结果回传"
        : path.includes("/experiment")
          ? "实验任务、运行与结果"
          : path.includes("/document")
            ? "科研文档、主张与引用"
            : "当前模块";
    if (/(^|\/)(service|manager)\.[a-z]+$/.test(path)) {
      return `编排${scope}的业务规则，对上提供操作入口，对下协调数据读写。`;
    }
    if (/(^|\/)(store|storage|repository)\.[a-z]+$/.test(path)) {
      return `负责${scope}的数据持久化、查询与一致性维护。`;
    }
    if (/(^|\/)(router|api|controller)\.[a-z]+$/.test(path)) {
      return `接收${scope}的外部请求，校验输入后转交业务服务。`;
    }
    if (/(^|\/)(models?|schema)\.[a-z]+$/.test(path)) {
      return `定义${scope}的数据结构、字段约束与传输边界。`;
    }
    if (/(test|spec)[^/]*\.[a-z]+$/.test(path)) {
      return `验证${scope}的关键行为、边界条件与回归风险。`;
    }
    return `组织${scope}的代码实体与对外依赖。`;
  }
  const target = codeObjectLabel(label);
  if (label === "__init__")
    return "初始化当前对象的依赖、默认状态与运行所需资源。";
  if (/(^|_)utc_now$|^now_utc$/.test(label)) {
    return "生成统一的 UTC 时间戳，供业务记录、审计与状态更新时间使用。";
  }
  if (/(^|_)transaction$|^begin_transaction$/.test(label)) {
    return "管理数据库事务边界：成功时提交，异常时回滚，避免写入半成品数据。";
  }
  if (/(^|_)(enqueue|queue|submit)$/.test(label)) {
    return "把待处理任务放入执行队列，交给后续异步流程继续处理。";
  }
  if (/^(validate|check|verify)_/.test(label)) {
    return `校验${target}是否满足后续操作要求，并返回明确的失败原因。`;
  }
  if (/^(parse|decode|normalize)_/.test(label)) {
    return `把${target}转换为系统内部可稳定处理的统一结构。`;
  }
  if (/^(execute|run)_/.test(label)) {
    return `执行${target}对应的核心流程，并汇总运行结果与失败状态。`;
  }
  if (/^(index|sync)_/.test(label)) {
    return `同步并索引${target}，让后续检索、关系分析与证据定位可以使用。`;
  }
  if (/^_?require_/.test(label))
    return `校验并取得${target}，在业务操作前统一处理缺失或越权情况。`;
  if (/^_?ensure_/.test(label))
    return `确保${target}处于可用状态，必要时补齐默认记录。`;
  if (/^create_/.test(label)) return `创建${target}并建立初始状态与关联。`;
  if (/^(update|save)_/.test(label)) return `更新${target}并保存变更结果。`;
  if (/^(delete|remove)_/.test(label))
    return `删除${target}并处理相关联的数据边界。`;
  if (/^transition_/.test(label)) return `校验规则后推进${target}的状态迁移。`;
  if (/^link_/.test(label)) return `把${target}与当前研究上下文建立关系。`;
  if (/^unlink_/.test(label)) return `解除${target}与当前研究上下文的关系。`;
  if (/^review_/.test(label)) return `复核${target}并记录确认或拒绝结果。`;
  if (/^(list|get|find|search)_/.test(label))
    return `读取${target}，供业务流程或界面继续使用。`;
  if (/^_?audit/.test(label)) return "记录关键业务操作，形成可追踪的审计事件。";
  if (node.type === "CodeClass")
    return `封装 ${node.label} 相关状态与操作，是该文件的主要业务对象。`;
  return `实现 ${node.label} 对应的代码能力。`;
}

export function codeNodeBehaviorFacts(node: ResearchMapNode): string[] {
  const facts: string[] = [];
  const calls = metadataStrings(node, "calls");
  const references = metadataStrings(node, "references");
  const imports = metadataStrings(node, "imports");
  if (node.startLine) {
    facts.push(
      `实现位置 L${node.startLine}${node.endLine ? `–${node.endLine}` : ""}`,
    );
  }
  if (calls.length)
    facts.push(
      `直接调用 ${calls.slice(0, 4).join("、")}${calls.length > 4 ? ` 等 ${calls.length} 项` : ""}`,
    );
  if (imports.length)
    facts.push(
      `导入 ${imports.slice(0, 4).join("、")}${imports.length > 4 ? ` 等 ${imports.length} 项` : ""}`,
    );
  if (references.length)
    facts.push(
      `涉及 ${references.slice(0, 4).join("、")}${references.length > 4 ? ` 等 ${references.length} 个标识符` : ""}`,
    );
  if (node.children?.length)
    facts.push(`包含 ${node.children.length} 个可继续检查的代码实体`);
  return facts.slice(0, 4);
}

function externalEvidenceForCode(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  codeIds: Set<string>,
): ResearchMapNode[] {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const direct = new Set<string>();
  edges.forEach((edge) => {
    if (codeIds.has(edge.source) && !codeIds.has(edge.target))
      direct.add(edge.target);
    if (codeIds.has(edge.target) && !codeIds.has(edge.source))
      direct.add(edge.source);
  });
  const contextual = new Set(direct);
  edges.forEach((edge) => {
    if (direct.has(edge.source) && !codeIds.has(edge.target))
      contextual.add(edge.target);
    if (direct.has(edge.target) && !codeIds.has(edge.source))
      contextual.add(edge.source);
  });
  return [...contextual]
    .map((id) => byId.get(id))
    .filter((node): node is ResearchMapNode =>
      Boolean(node && node.domain !== "code"),
    );
}

function positionCodeEvidence(
  evidence: ResearchMapNode[],
  leftX: number,
  rightX: number,
  startY = 20,
): ResearchMapNode[] {
  const left = evidence.filter((node) => node.domain === "codex");
  const right = evidence.filter((node) => node.domain !== "codex");
  return [
    ...left.map((node, index) => ({
      ...node,
      x: leftX,
      y: startY + index * 142,
    })),
    ...right.map((node, index) => ({
      ...node,
      x: rightX,
      y: startY + index * 142,
    })),
  ];
}

function summarizeCodeEvidence(
  evidence: ResearchMapNode[],
  edges: ResearchMapEdge[],
): {
  nodes: ResearchMapNode[];
  idMap: Map<string, string>;
} {
  const topTypes = new Set([
    "CodexThread",
    "ScientificDocument",
    "Experiment",
    "ResearchIteration",
    "ResearchTopic",
  ]);
  const topNodes = evidence.filter((node) => topTypes.has(node.type));
  const topIds = new Set(topNodes.map((node) => node.id));
  const evidenceIds = new Set(evidence.map((node) => node.id));
  const idMap = new Map(topNodes.map((node) => [node.id, node.id]));
  for (let pass = 0; pass < 2; pass += 1) {
    edges.forEach((edge) => {
      if (!evidenceIds.has(edge.source) || !evidenceIds.has(edge.target))
        return;
      const source =
        idMap.get(edge.source) ||
        (topIds.has(edge.source) ? edge.source : null);
      const target =
        idMap.get(edge.target) ||
        (topIds.has(edge.target) ? edge.target : null);
      if (source && !target) idMap.set(edge.target, source);
      if (target && !source) idMap.set(edge.source, target);
    });
  }
  evidence.forEach((node) => {
    if (idMap.has(node.id) || node.domain !== "codex") return;
    const threadId = normalizeThreadId(node.id);
    if (topIds.has(threadId)) idMap.set(node.id, threadId);
  });
  const unmapped = evidence
    .filter((node) => !idMap.has(node.id))
    .filter((node) => !["Patch", "FileChange"].includes(node.type))
    .slice(0, 4);
  unmapped.forEach((node) => idMap.set(node.id, node.id));
  return {
    nodes: [...topNodes, ...unmapped].slice(0, 12),
    idMap,
  };
}

function buildGroupedFocusedCodeBoard(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  focusPath: string,
  members: ResearchMapNode[],
  fileVersion: ResearchMapNode | undefined,
  repository: ResearchMapNode | undefined,
  commit: ResearchMapNode | undefined,
): { nodes: ResearchMapNode[]; edges: ResearchMapEdge[] } {
  const repositoryId =
    fileVersion?.repositoryId ||
    members.find((node) => node.repositoryId)?.repositoryId;
  const focusId = `board://code/focus/${encodeURIComponent(focusPath)}`;
  const symbols = members.filter((node) => node.type !== "FileVersion");
  const classes = symbols.filter((node) =>
    ["CodeClass", "CodeInterface", "CodeModule"].includes(node.type),
  );
  const normalizedSymbols = symbols.map((node) => {
    if (node.type !== "CodeFunction") return node;
    const qualifiedName = node.qualifiedName || "";
    const belongsToClass = classes.some((candidate) => {
      const className = candidate.qualifiedName || candidate.label;
      return qualifiedName.startsWith(`${className}.`);
    });
    return belongsToClass ? { ...node, type: "CodeMethod" } : node;
  });
  const callables = normalizedSymbols.filter(
    (node) => !classes.some((candidate) => candidate.id === node.id),
  );
  const parentForMember = new Map<string, ResearchMapNode>();
  callables.forEach((member) => {
    const qualifiedName = member.qualifiedName || "";
    const parent = classes
      .filter((candidate) => {
        const className = candidate.qualifiedName || candidate.label;
        return qualifiedName.startsWith(`${className}.`);
      })
      .sort(
        (left, right) =>
          (right.qualifiedName || right.label).length -
          (left.qualifiedName || left.label).length,
      )[0];
    if (parent) parentForMember.set(member.id, parent);
  });

  const grouped = new Map<
    string,
    {
      profile: CodeResponsibility;
      members: ResearchMapNode[];
    }
  >();
  callables.forEach((member) => {
    const profile = codeResponsibility(member);
    const current = grouped.get(profile.key) || { profile, members: [] };
    current.members.push(member);
    grouped.set(profile.key, current);
  });
  const memberRelationCount = new Map<string, number>();
  edges.forEach((edge) => {
    if (members.some((member) => member.id === edge.source)) {
      memberRelationCount.set(
        edge.source,
        (memberRelationCount.get(edge.source) || 0) + 1,
      );
    }
    if (members.some((member) => member.id === edge.target)) {
      memberRelationCount.set(
        edge.target,
        (memberRelationCount.get(edge.target) || 0) + 1,
      );
    }
  });
  const groups = [...grouped.entries()]
    .map(([key, value]) => ({
      key,
      ...value,
      importance: value.members.reduce(
        (total, member) => total + (memberRelationCount.get(member.id) || 0),
        0,
      ),
    }))
    .sort(
      (left, right) =>
        right.importance - left.importance ||
        right.members.length - left.members.length ||
        left.profile.label.localeCompare(right.profile.label),
    );
  const columns =
    groups.length > 4 ? 2 : Math.min(2, Math.max(1, groups.length));
  const rows = Math.max(1, Math.ceil(groups.length / columns));
  let groupNodes = groups.map((group, index): ResearchMapNode => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const id = `board://code/responsibility/${encodeURIComponent(focusPath)}/${group.key}`;
    return {
      id,
      type: "CodeResponsibilityGroup",
      domain: "code",
      label: group.profile.label,
      meta: `${group.profile.description} · ${group.members.length} 个入口`,
      locator: `${focusPath}#responsibility=${group.key}`,
      repositoryId,
      path: focusPath,
      language: fileVersion?.language || group.members[0]?.language,
      x: 160 + column * 372,
      y: (row - (rows - 1) / 2) * 214,
      width: 326,
      height: 178,
      source: "cluster",
      children: codeChildSummary(group.members),
      metadata: {
        responsibility_key: group.key,
        responsibility_description: group.profile.description,
        member_ids: group.members.map((member) => member.id),
      },
    };
  });
  const classBuckets = new Map<
    string,
    {
      label: string;
      description: string;
      members: ResearchMapNode[];
    }
  >();
  if (classes.length > 4) {
    classes.forEach((node) => {
      const value = `${node.label} ${node.qualifiedName || ""}`.toLowerCase();
      const profile = /(request|input|scope|query|filter|payload|command)/.test(
        value,
      )
        ? {
            key: "input-models",
            label: "输入与请求",
            description: "定义外部输入、查询范围与命令载荷。",
          }
        : /(result|response|output|summary|report|metric|match|hit)/.test(value)
          ? {
              key: "result-models",
              label: "结果与响应",
              description: "定义分析结果、响应对象与汇总数据。",
            }
          : /(status|state|config|policy|option|setting|mode|permission)/.test(
                value,
              )
            ? {
                key: "state-models",
                label: "状态与配置",
                description: "定义状态、策略与运行配置。",
              }
            : {
                key: "core-models",
                label: "核心数据结构",
                description: "定义当前模块使用的核心领域对象。",
              };
      const bucket = classBuckets.get(profile.key) || {
        ...profile,
        members: [],
      };
      bucket.members.push(node);
      classBuckets.set(profile.key, bucket);
    });
  }
  let classGroupNodes: ResearchMapNode[] = [...classBuckets.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, bucket]) => ({
      id: `board://code/type-group/${encodeURIComponent(focusPath)}/${key}`,
      type: "CodeResponsibilityGroup",
      domain: "code",
      label: bucket.label,
      meta: `${bucket.description} · ${bucket.members.length} 个类型`,
      locator: `${focusPath}#type-group=${key}`,
      repositoryId,
      path: focusPath,
      language: fileVersion?.language || bucket.members[0]?.language,
      x: 0,
      y: 0,
      width: 326,
      height: 178,
      source: "cluster",
      children: codeChildSummary(bucket.members),
      metadata: {
        responsibility_key: key,
        responsibility_description: bucket.description,
        member_ids: bucket.members.map((member) => member.id),
      },
    }));
  const capabilityNodes = [...groupNodes, ...classGroupNodes];
  const capabilityColumns =
    capabilityNodes.length > 4
      ? 2
      : Math.min(2, Math.max(1, capabilityNodes.length));
  const capabilityRows = Math.max(
    1,
    Math.ceil(capabilityNodes.length / capabilityColumns),
  );
  const positionedCapabilities = capabilityNodes.map((node, index) => ({
    ...node,
    x: 160 + (index % capabilityColumns) * 372,
    y: (Math.floor(index / capabilityColumns) - (capabilityRows - 1) / 2) * 214,
  }));
  groupNodes = positionedCapabilities.slice(0, groupNodes.length);
  classGroupNodes = positionedCapabilities.slice(groupNodes.length);
  const groupNodeByKey = new Map(
    groups.map((group, index) => [group.key, groupNodes[index]]),
  );
  const memberToGroup = new Map<string, string>();
  groups.forEach((group) => {
    const groupId = groupNodeByKey.get(group.key)!.id;
    group.members.forEach((member) => memberToGroup.set(member.id, groupId));
  });
  const classToGroup = new Map<string, string>();
  classBuckets.forEach((bucket, key) => {
    const groupId = classGroupNodes.find((node) =>
      node.id.endsWith(`/${key}`),
    )?.id;
    if (!groupId) return;
    bucket.members.forEach((member) => classToGroup.set(member.id, groupId));
  });
  const focus: ResearchMapNode = {
    ...(fileVersion || members[0]),
    id: focusId,
    type: "CodeFileFocus",
    label: focusPath.split("/").at(-1) || focusPath,
    meta: [
      focusPath,
      fileVersion?.language,
      `${symbols.length} 个实体`,
      `${groups.length} 个职责域`,
    ]
      .filter(Boolean)
      .join(" · "),
    path: focusPath,
    x: -430,
    y: 0,
    width: 292,
    height: 118,
    source: "cluster",
    children: [...groupNodes, ...classGroupNodes].map((group) => ({
      id: group.id,
      label: group.label,
      type: group.type,
      meta: group.meta,
    })),
    metadata: fileVersion?.metadata,
  };
  const classGroups = new Map<string, ResearchMapNode[]>();
  groups.forEach((group) => {
    const parents = group.members
      .map((member) => parentForMember.get(member.id))
      .filter((item): item is ResearchMapNode => Boolean(item));
    const uniqueParents = [
      ...new Map(parents.map((item) => [item.id, item])).values(),
    ];
    if (uniqueParents.length !== 1 || uniqueParents.length !== parents.length)
      return;
    const parent = uniqueParents[0];
    classGroups.set(parent.id, [
      ...(classGroups.get(parent.id) || []),
      groupNodeByKey.get(group.key)!,
    ]);
  });
  const positionedClasses = (classes.length > 4 ? [] : classes).map(
    (node, index) => {
      const owned = classGroups.get(node.id) || [];
      return {
        ...node,
        x: -105,
        y: owned.length
          ? owned.reduce((total, group) => total + group.y, 0) / owned.length
          : (index - (classes.length - 1) / 2) * 154,
        width: 248,
        height: 96,
      };
    },
  );

  const memberIds = new Set(members.map((node) => node.id));
  const allEvidence = externalEvidenceForCode(nodes, edges, memberIds);
  const directEvidenceIds = new Set<string>();
  edges.forEach((edge) => {
    if (memberIds.has(edge.source)) directEvidenceIds.add(edge.target);
    if (memberIds.has(edge.target)) directEvidenceIds.add(edge.source);
  });
  const evidence = [
    ...allEvidence.filter((node) => node.type === "CodexThread"),
    ...allEvidence.filter((node) => directEvidenceIds.has(node.id)),
    ...allEvidence.filter(
      (node) => node.type !== "CodexThread" && !directEvidenceIds.has(node.id),
    ),
  ]
    .filter(
      (node, index, values) =>
        values.findIndex((candidate) => candidate.id === node.id) === index,
    )
    .slice(0, 6);
  const positionedEvidence = positionCodeEvidence(evidence, -790, 980);
  const evidenceIds = new Set(positionedEvidence.map((node) => node.id));
  const visibleSourceIds = new Set([
    ...memberIds,
    ...evidenceIds,
    ...(repository ? [repository.id] : []),
    ...(commit ? [commit.id] : []),
  ]);
  const mappedId = new Map<string, string>([
    ...(fileVersion ? [[fileVersion.id, focusId] as const] : []),
    ...memberToGroup.entries(),
    ...classToGroup.entries(),
  ]);
  classes.forEach((node) => {
    if (!mappedId.has(node.id)) mappedId.set(node.id, node.id);
  });

  // The focused file board answers "what does this file do?" Repository and
  // commit are preserved in the inspector locator/breadcrumb instead of
  // consuming a remote corner of the canvas and forcing the useful file →
  // class → responsibility chain to zoom out.
  const positionedAncestors: ResearchMapNode[] = [];
  const structure: ResearchMapEdge[] = [];
  positionedClasses.forEach((node) => {
    structure.push({
      id: `code-focus-class://${focusId}/${node.id}`,
      source: focusId,
      predicate: "DEFINES",
      target: node.id,
      derivation: "parser",
      confidence: 1,
      review_status: "confirmed",
      domain: "code",
      label: "定义类",
      tone: "code",
      structural: true,
    });
  });
  classGroupNodes.forEach((node) => {
    structure.push({
      id: `code-focus-type-group://${focusId}/${node.id}`,
      source: focusId,
      predicate: "DEFINES",
      target: node.id,
      derivation: "type_responsibility_group",
      confidence: 1,
      review_status: "confirmed",
      domain: "code",
      label: "数据结构",
      tone: "code",
      structural: true,
    });
  });
  groups.forEach((group) => {
    const target = groupNodeByKey.get(group.key)!;
    const parents = group.members
      .map((member) => parentForMember.get(member.id)?.id)
      .filter((value): value is string => Boolean(value));
    const uniqueParents = new Set(parents);
    const parentId =
      uniqueParents.size === 1 && parents.length === group.members.length
        ? parents[0]
        : null;
    const source = parentId ? classToGroup.get(parentId) || parentId : focusId;
    structure.push({
      id: `code-focus-responsibility://${source}/${target.id}`,
      source,
      predicate: source === focusId ? "DEFINES" : "belongs_to_class",
      target: target.id,
      derivation: "responsibility_group",
      confidence: 1,
      review_status: "confirmed",
      domain: "code",
      label: source === focusId ? "职责域" : "包含能力",
      tone: "code",
      structural: true,
    });
  });
  const relations = edges.flatMap((edge) => {
    if (
      !visibleSourceIds.has(edge.source) ||
      !visibleSourceIds.has(edge.target)
    )
      return [];
    if (["HAS_COMMIT", "CONTAINS", "DEFINES"].includes(edge.predicate))
      return [];
    const source = mappedId.get(edge.source) || edge.source;
    const target = mappedId.get(edge.target) || edge.target;
    if (source === target) return [];
    return [
      {
        ...edge,
        id: `code-focus-grouped://${source}/${edge.predicate}/${target}`,
        source,
        target,
        label: predicateLabel(edge.predicate),
      },
    ];
  });
  const boardNodes = [
    ...positionedAncestors,
    focus,
    ...positionedClasses,
    ...groupNodes,
    ...classGroupNodes,
    ...positionedEvidence,
  ];
  return {
    nodes: boardNodes,
    edges: keepEdgesWithNodes(
      boardNodes,
      aggregateVisibleRelations([...structure, ...relations]),
    ),
  };
}

function buildFocusedCodeBoard(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  focusPath: string,
  focusRepositoryId?: string | null,
): { nodes: ResearchMapNode[]; edges: ResearchMapEdge[] } {
  const members = nodes.filter(
    (node) =>
      node.domain === "code" &&
      codeEntityKind(node) &&
      codeFileKey(node) === focusPath &&
      (!focusRepositoryId || node.repositoryId === focusRepositoryId),
  );
  if (!members.length) return { nodes: [], edges: [] };
  const fileVersion = members.find((node) => node.type === "FileVersion");
  const symbols = members.filter((node) => node.type !== "FileVersion");
  const repositoryId =
    fileVersion?.repositoryId ||
    members.find((node) => node.repositoryId)?.repositoryId;
  const indexedRepository = nodes.find(
    (node) =>
      node.type === "Repository" && (!repositoryId || node.id === repositoryId),
  );
  const repository: ResearchMapNode | undefined =
    indexedRepository ||
    (repositoryId
      ? {
          id: repositoryId,
          type: "Repository",
          domain: "code",
          label: repositoryId.replace(/^repo:\/\//, ""),
          meta: "当前项目仓库",
          locator: repositoryId,
          repositoryId,
          status: "indexed",
          x: 0,
          y: 0,
          source: "cluster",
        }
      : undefined);
  const commit = nodes.find(
    (node) =>
      node.type === "Commit" &&
      (!repositoryId || node.repositoryId === repositoryId) &&
      (!fileVersion?.version || node.version === fileVersion.version),
  );
  if (symbols.length > 8) {
    return buildGroupedFocusedCodeBoard(
      nodes,
      edges,
      focusPath,
      members,
      fileVersion,
      repository,
      commit,
    );
  }
  const focusId = `board://code/focus/${encodeURIComponent(focusPath)}`;
  const focus: ResearchMapNode = {
    ...(fileVersion || members[0]),
    id: focusId,
    type: "CodeFileFocus",
    label: focusPath.split("/").at(-1) || focusPath,
    meta: [focusPath, fileVersion?.language, `${symbols.length} 个代码实体`]
      .filter(Boolean)
      .join(" · "),
    path: focusPath,
    x: -300,
    y: 0,
    source: "cluster",
    children: codeChildSummary(members),
  };

  const classes = symbols.filter((node) =>
    ["CodeClass", "CodeInterface", "CodeModule"].includes(node.type),
  );
  const classifiedSymbols = symbols.map((node) => {
    if (node.type !== "CodeFunction") return node;
    const qualifiedName = node.qualifiedName || "";
    const belongsToClass = classes.some((candidate) => {
      const className = candidate.qualifiedName || candidate.label;
      return qualifiedName.startsWith(`${className}.`);
    });
    return belongsToClass ? { ...node, type: "CodeMethod" } : node;
  });
  const methods = classifiedSymbols.filter(
    (node) => node.type === "CodeMethod",
  );
  const functions = classifiedSymbols.filter(
    (node) =>
      !classes.some((candidate) => candidate.id === node.id) &&
      node.type !== "CodeMethod",
  );
  const parentForMethod = new Map<string, ResearchMapNode>();
  methods.forEach((method) => {
    const parent = classes
      .filter((candidate) => {
        const candidateName = candidate.qualifiedName || candidate.label;
        const methodName = method.qualifiedName || "";
        return (
          methodName.startsWith(`${candidateName}.`) ||
          methodName.startsWith(`${candidate.label}.`)
        );
      })
      .sort(
        (left, right) =>
          (right.qualifiedName || right.label).length -
          (left.qualifiedName || left.label).length,
      )[0];
    if (parent) parentForMethod.set(method.id, parent);
  });

  const classY = new Map<string, number>();
  const methodPositions = new Map<string, { x: number; y: number }>();
  let entityCursorY = -190;
  classes.forEach((parent) => {
    const children = methods.filter(
      (method) => parentForMethod.get(method.id)?.id === parent.id,
    );
    const rows = Math.max(1, Math.ceil(children.length / 2));
    classY.set(parent.id, entityCursorY + (rows - 1) * 65);
    children.forEach((method, index) => {
      methodPositions.set(method.id, {
        x: 330 + (index % 2) * 270,
        y: entityCursorY + Math.floor(index / 2) * 130,
      });
    });
    entityCursorY += rows * 130 + 72;
  });
  methods
    .filter((method) => !parentForMethod.has(method.id))
    .forEach((method, index) => {
      methodPositions.set(method.id, {
        x: 330 + (index % 2) * 270,
        y: entityCursorY + Math.floor(index / 2) * 130,
      });
    });
  const unparentedMethodRows = Math.ceil(
    methods.filter((method) => !parentForMethod.has(method.id)).length / 2,
  );
  entityCursorY += unparentedMethodRows * 130 + (unparentedMethodRows ? 72 : 0);
  const positionedClasses = classes.map((node) => ({
    ...node,
    x: 20,
    y: classY.get(node.id) || 0,
  }));
  const positionedMethods = methods.map((node) => ({
    ...node,
    ...(methodPositions.get(node.id) || { x: 330, y: 0 }),
  }));
  const positionedFunctions = functions.map((node, index) => ({
    ...node,
    x: (classes.length || methods.length ? 330 : 20) + (index % 2) * 270,
    y: entityCursorY + Math.floor(index / 2) * 130,
  }));

  const memberIds = new Set(members.map((node) => node.id));
  const allEvidence = externalEvidenceForCode(nodes, edges, memberIds);
  const directEvidenceIds = new Set<string>();
  edges.forEach((edge) => {
    if (memberIds.has(edge.source)) directEvidenceIds.add(edge.target);
    if (memberIds.has(edge.target)) directEvidenceIds.add(edge.source);
  });
  const evidence = [
    ...allEvidence.filter((node) => node.type === "CodexThread"),
    ...allEvidence.filter((node) => directEvidenceIds.has(node.id)),
    ...allEvidence.filter(
      (node) => node.type !== "CodexThread" && !directEvidenceIds.has(node.id),
    ),
  ]
    .filter(
      (node, index, values) =>
        values.findIndex((candidate) => candidate.id === node.id) === index,
    )
    .slice(0, 10);
  const positionedEvidence = positionCodeEvidence(evidence, -650, 900);
  const evidenceIds = new Set(positionedEvidence.map((node) => node.id));
  const visibleSourceIds = new Set([
    ...memberIds,
    ...evidenceIds,
    ...(repository ? [repository.id] : []),
    ...(commit ? [commit.id] : []),
  ]);
  const idMap = new Map<string, string>([
    ...(fileVersion ? [[fileVersion.id, focusId] as const] : []),
  ]);

  const positionedAncestors: ResearchMapNode[] = [];
  const structure: ResearchMapEdge[] = [];
  [...classes, ...functions].forEach((node) => {
    structure.push({
      id: `code-focus-defines://${focusId}/${node.id}`,
      source: focusId,
      predicate: "DEFINES",
      target: node.id,
      derivation: "parser",
      confidence: 1,
      review_status: "confirmed",
      domain: "code",
      label: "定义",
      tone: "code",
      structural: true,
    });
  });
  methods.forEach((node) => {
    const parent = parentForMethod.get(node.id);
    structure.push({
      id: `code-focus-method://${parent?.id || focusId}/${node.id}`,
      source: parent?.id || focusId,
      predicate: parent ? "belongs_to_class" : "DEFINES",
      target: node.id,
      derivation: parent ? "qualified_name" : "parser",
      confidence: 1,
      review_status: "confirmed",
      domain: "code",
      label: parent ? "包含方法" : "定义",
      tone: "code",
      structural: true,
    });
  });

  const relations = edges.flatMap((edge) => {
    if (
      !visibleSourceIds.has(edge.source) ||
      !visibleSourceIds.has(edge.target)
    )
      return [];
    if (["HAS_COMMIT", "CONTAINS", "DEFINES"].includes(edge.predicate))
      return [];
    const source = idMap.get(edge.source) || edge.source;
    const target = idMap.get(edge.target) || edge.target;
    if (source === target) return [];
    return [{ ...edge, source, target }];
  });
  const boardNodes = [
    ...positionedAncestors,
    focus,
    ...positionedClasses,
    ...positionedMethods,
    ...positionedFunctions,
    ...positionedEvidence,
  ];
  return {
    nodes: boardNodes,
    edges: keepEdgesWithNodes(
      boardNodes,
      aggregateVisibleRelations([...structure, ...relations]),
    ),
  };
}

/**
 * Code gets a compound whiteboard: repository → version → directory → file.
 * A file can then be opened as its own logic board with classes, functions,
 * methods and only the real session/document/experiment relations attached.
 */
export function buildCodeBoard(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  focusPath?: string | null,
  focusRepositoryId?: string | null,
): { nodes: ResearchMapNode[]; edges: ResearchMapEdge[] } {
  if (focusPath) {
    const focused = buildFocusedCodeBoard(
      nodes,
      edges,
      focusPath,
      focusRepositoryId,
    );
    if (focused.nodes.length) return focused;
  }
  const codeNodes = nodes.filter(
    (node) => node.domain === "code" && codeEntityKind(node),
  );
  const groups = new Map<string, ResearchMapNode[]>();
  codeNodes.forEach((node) => {
    const key = codeFileKey(node);
    groups.set(key, [...(groups.get(key) || []), node]);
  });

  const childToGroup = new Map<string, string>();
  const fileGroups = [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([path, children]) => {
      const id = `board://code/file/${encodeURIComponent(path)}`;
      children.forEach((child) => childToGroup.set(child.id, id));
      return {
        id,
        type: "CodeFileGroup",
        domain: "code" as const,
        label: path.split("/").filter(Boolean).at(-1) || path,
        meta: path,
        locator: children[0]?.locator || path,
        version: children.find((child) => child.version)?.version,
        repositoryId:
          children.find((child) => child.repositoryId)?.repositoryId ||
          nodes.find((node) => node.type === "Repository")?.id ||
          "board://code/repository",
        path,
        language: children.find((child) => child.language)?.language,
        status: "indexed",
        x: 0,
        y: 0,
        source: "cluster" as const,
        children: codeChildSummary(children),
      };
    });
  const repositoryNodes = nodes.filter((node) => node.type === "Repository");
  const commitNodes = nodes.filter((node) => node.type === "Commit");
  const repositoryIds = new Set([
    ...repositoryNodes.map((node) => node.id),
    ...fileGroups
      .map((node) => node.repositoryId)
      .filter((value): value is string => Boolean(value)),
  ]);
  const repositories = [...repositoryIds].map((repositoryId) => {
    const actual = repositoryNodes.find((node) => node.id === repositoryId);
    return (
      actual || {
        id: repositoryId,
        type: "Repository",
        domain: "code" as const,
        label: repositoryId.replace(/^repo:\/\//, ""),
        meta: "已索引项目仓库",
        locator: repositoryId,
        repositoryId,
        status: "indexed",
        x: 0,
        y: 0,
        source: "cluster" as const,
      }
    );
  });
  const filesByDirectory = new Map<string, ResearchMapNode[]>();
  fileGroups.forEach((group) => {
    const directory = directoryKey(group.path || group.meta);
    const key = `${group.repositoryId || "repository"}\u0000${directory}`;
    filesByDirectory.set(key, [...(filesByDirectory.get(key) || []), group]);
  });
  const directoryEntries = [...filesByDirectory.entries()].sort(
    ([left], [right]) => left.localeCompare(right),
  );
  const directoryNodes: ResearchMapNode[] = [];
  const positionedFiles: ResearchMapNode[] = [];
  const fileGroupToDirectory = new Map<string, string>();
  const collapseFiles = true;
  const repositoryCenters = new Map(
    repositories.map((repository, index) => [
      repository.id,
      (index - (repositories.length - 1) / 2) * 760,
    ]),
  );
  const directoriesByRepository = new Map<string, typeof directoryEntries>();
  directoryEntries.forEach((entry) => {
    const [repositoryId] = entry[0].split("\u0000");
    directoriesByRepository.set(repositoryId, [
      ...(directoriesByRepository.get(repositoryId) || []),
      entry,
    ]);
  });
  directoriesByRepository.forEach((entries, repositoryId) => {
    const centerX = repositoryCenters.get(repositoryId) || 0;
    const columns = Math.min(2, entries.length);
    entries.forEach(([key, files], directoryIndex) => {
      const [, directory] = key.split("\u0000");
      const column = directoryIndex % columns;
      const row = Math.floor(directoryIndex / columns);
      const entityCount = files.reduce(
        (total, file) => total + (file.children?.length || 0),
        0,
      );
      const directoryId = `board://code/directory/${encodeURIComponent(repositoryId)}/${encodeURIComponent(directory)}`;
      files.forEach((file) => fileGroupToDirectory.set(file.id, directoryId));
      directoryNodes.push({
        id: directoryId,
        type: "CodeDirectoryGroup",
        domain: "code",
        label: directory.split("/").at(-1) || directory,
        meta:
          directory === "仓库根目录"
            ? `${files.length} 个根目录文件`
            : `${files.length} 个文件 · ${entityCount} 个已载入实体`,
        locator: directory,
        repositoryId,
        status: "indexed",
        x: centerX + (column - (columns - 1) / 2) * 310,
        y: -30 + row * 184,
        source: "cluster",
        children: files.map((file) => ({
          id: file.id,
          label: file.label,
          type: file.type,
          meta: file.path || file.meta,
        })),
      });
    });
  });
  const positionedRepositories = repositories.map((repository) => {
    const repositoryName = repository.id.replace(/^repo:\/\//, "");
    const label = /^[a-f0-9]{12,}$/i.test(repository.label)
      ? repositoryName
      : repository.label;
    return {
      ...repository,
      label,
      repositoryId: repository.id,
      x: repositoryCenters.get(repository.id) || 0,
      y: -330,
    };
  });
  const positionedCommits = commitNodes
    .filter((commit) => repositoryIds.has(commit.repositoryId || ""))
    .map((commit) => {
      return {
        ...commit,
        x: repositoryCenters.get(commit.repositoryId || "") || 0,
        y: -190,
      };
    });
  const root: ResearchMapNode = {
    id: "board://code/root",
    type: "CodeRepositoryBoard",
    domain: "code",
    label: "项目代码空间",
    meta: `${positionedRepositories.length} 个仓库 · ${fileGroups.length} 个已载入文件`,
    locator: "board://code",
    status: "indexed",
    x: 0,
    y: -475,
    source: "cluster",
    children: positionedRepositories.map((repository) => ({
      id: repository.id,
      label: repository.label,
      type: repository.type,
      meta: repository.version?.slice(0, 10),
    })),
  };
  const structure: ResearchMapEdge[] = [
    ...positionedRepositories.map((repository) => ({
      id: `code-board-repository-edge://${root.id}/${repository.id}`,
      source: root.id,
      predicate: "contains_source",
      target: repository.id,
      derivation: "project_scope",
      confidence: 1,
      review_status: "confirmed",
      domain: "code",
      label: "项目仓库",
      tone: "code" as const,
      structural: true,
      scope: true,
    })),
    ...positionedRepositories.flatMap((repository) => {
      const commits = positionedCommits.filter(
        (commit) => commit.repositoryId === repository.id,
      );
      return commits.map((commit) => ({
        id: `code-board-commit-edge://${repository.id}/${commit.id}`,
        source: repository.id,
        predicate: "HAS_COMMIT",
        target: commit.id,
        derivation: "code_hierarchy",
        confidence: 1,
        review_status: "confirmed",
        domain: "code",
        label: "当前版本",
        tone: "code" as const,
        structural: true,
      }));
    }),
    ...directoryNodes.map((directory) => {
      const commit = positionedCommits.find(
        (item) => item.repositoryId === directory.repositoryId,
      );
      return {
        id: `code-board-directory-edge://${directory.id}`,
        source:
          commit?.id || directory.repositoryId || positionedRepositories[0].id,
        predicate: "contains_directory",
        target: directory.id,
        derivation: "code_hierarchy",
        confidence: 1,
        review_status: "confirmed",
        domain: "code",
        label: "目录",
        tone: "code" as const,
        structural: true,
      };
    }),
    ...(!collapseFiles ? positionedFiles : []).map((file) => {
      const directory = directoryKey(file.path || file.meta);
      const directoryId = `board://code/directory/${encodeURIComponent(file.repositoryId || "repository")}/${encodeURIComponent(directory)}`;
      return {
        id: `code-board-file-edge://${file.id}`,
        source: directoryId,
        predicate: "contains_file",
        target: file.id,
        derivation: "code_hierarchy",
        confidence: 1,
        review_status: "confirmed",
        domain: "code",
        label: "文件",
        tone: "code" as const,
        occurrences: file.children?.length || 0,
        structural: true,
      };
    }),
  ];
  const codeIds = new Set(codeNodes.map((node) => node.id));
  const evidenceSummary = summarizeCodeEvidence(
    externalEvidenceForCode(nodes, edges, codeIds),
    edges,
  );
  const evidenceStartY = Math.max(
    220,
    ...directoryNodes.map((node) => node.y + 210),
  );
  const positionedEvidence = positionCodeEvidence(
    evidenceSummary.nodes,
    -240,
    240,
    evidenceStartY,
  );
  const visibleExternalIds = new Set(positionedEvidence.map((node) => node.id));
  const visibleCodeIds = new Set([
    ...codeIds,
    ...positionedRepositories.map((node) => node.id),
    ...positionedCommits.map((node) => node.id),
  ]);
  const relations = dedupeEdges(
    edges.flatMap((edge) => {
      if (["HAS_COMMIT", "CONTAINS", "DEFINES"].includes(edge.predicate))
        return [];
      const sourceFile = childToGroup.get(edge.source);
      const targetFile = childToGroup.get(edge.target);
      const source =
        (collapseFiles && sourceFile
          ? fileGroupToDirectory.get(sourceFile)
          : sourceFile) ||
        evidenceSummary.idMap.get(edge.source) ||
        edge.source;
      const target =
        (collapseFiles && targetFile
          ? fileGroupToDirectory.get(targetFile)
          : targetFile) ||
        evidenceSummary.idMap.get(edge.target) ||
        edge.target;
      const sourceVisible =
        visibleCodeIds.has(edge.source) || visibleExternalIds.has(edge.source);
      const targetVisible =
        visibleCodeIds.has(edge.target) || visibleExternalIds.has(edge.target);
      const mappedSourceVisible =
        visibleExternalIds.has(source) ||
        childToGroup.has(edge.source) ||
        directoryNodes.some((node) => node.id === source);
      const mappedTargetVisible =
        visibleExternalIds.has(target) ||
        childToGroup.has(edge.target) ||
        directoryNodes.some((node) => node.id === target);
      if (
        (!sourceVisible && !mappedSourceVisible) ||
        (!targetVisible && !mappedTargetVisible) ||
        source === target
      )
        return [];
      return [
        {
          ...edge,
          id: `code-group-edge://${source}/${edge.predicate}/${target}`,
          source,
          target,
        },
      ];
    }),
  );
  const boardNodes = [
    root,
    ...positionedRepositories,
    ...positionedCommits,
    ...directoryNodes,
    ...(!collapseFiles ? positionedFiles : []),
    ...positionedEvidence,
  ];
  const boardEdges = keepEdgesWithNodes(
    boardNodes,
    aggregateVisibleRelations([...structure, ...relations]),
  );
  return {
    nodes: boardNodes,
    edges: boardEdges,
  };
}

export type CodeCollaborationStats = {
  mode: "modules" | "files";
  modules: number;
  visibleModules: number;
  repositories: number;
  files: number;
  visibleFiles: number;
  entities: number;
  expandedFiles: number;
  crossFileRelations: number;
  internalRelations: number;
  expandedFileKeys: string[];
};

export type CodeCollaborationBoard = {
  nodes: ResearchMapNode[];
  edges: ResearchMapEdge[];
  stats: CodeCollaborationStats;
};

type CodeCollaborationOptions = {
  expandedFileKeys?: string[];
  autoExpand?: boolean;
  relationTypes?: string[];
  showAllModules?: boolean;
};

const collaborationPredicates = new Set([
  "CALLS",
  "REFERENCES",
  "IMPORTS",
  "SEMANTIC_SIMILAR",
]);

export function codeCollaborationFileKey(node: ResearchMapNode): string {
  return `${node.repositoryId || "repository"}::${codeFileKey(node)}`;
}

/**
 * Build a compound code workspace rather than flattening every symbol.
 *
 * Files are stable containers. Cross-file relations always terminate at the
 * container boundary so dependency lines never cut through a file's internal
 * entities. Expanding a file reveals a bounded structural preview; concrete
 * method-level tracing happens in the focused entity board.
 */
export function buildCodeCollaborationBoard(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  options: CodeCollaborationOptions = {},
): CodeCollaborationBoard {
  const groups = new Map<
    string,
    {
      key: string;
      id: string;
      path: string;
      repositoryId: string;
      members: ResearchMapNode[];
    }
  >();
  nodes
    .filter((node) => node.domain === "code" && codeEntityKind(node))
    .forEach((node) => {
      const path = codeFileKey(node);
      if (!isPresentableCodePath(path)) return;
      const repositoryId = node.repositoryId || "repository";
      const key = `${repositoryId}::${path}`;
      const current = groups.get(key) || {
        key,
        id: `board://code/collaboration/${encodeURIComponent(repositoryId)}/${encodeURIComponent(path)}`,
        path,
        repositoryId,
        members: [],
      };
      if (!current.members.some((member) => member.id === node.id)) {
        current.members.push(node);
      }
      groups.set(key, current);
    });

  const entityToGroup = new Map<string, string>();
  groups.forEach((group) => {
    group.members.forEach((member) => entityToGroup.set(member.id, group.key));
  });
  const relationTypes = new Set(options.relationTypes || []);
  const relevantEdges = edges.filter((edge) => {
    if (!collaborationPredicates.has(edge.predicate)) return false;
    if (relationTypes.size && !relationTypes.has(edge.predicate)) return false;
    return entityToGroup.has(edge.source) && entityToGroup.has(edge.target);
  });
  const crossEdges = relevantEdges.filter(
    (edge) => entityToGroup.get(edge.source) !== entityToGroup.get(edge.target),
  );
  const internalEdges = relevantEdges.filter(
    (edge) => entityToGroup.get(edge.source) === entityToGroup.get(edge.target),
  );
  const staticCrossEdges = crossEdges.filter(
    (edge) => edge.predicate !== "SEMANTIC_SIMILAR",
  );
  const incoming = new Map<string, number>();
  const outgoing = new Map<string, number>();
  const crossCount = new Map<string, number>();
  const internalCount = new Map<string, number>();
  crossEdges.forEach((edge) => {
    const source = entityToGroup.get(edge.source)!;
    const target = entityToGroup.get(edge.target)!;
    crossCount.set(source, (crossCount.get(source) || 0) + 1);
    crossCount.set(target, (crossCount.get(target) || 0) + 1);
    if (edge.predicate !== "SEMANTIC_SIMILAR") {
      outgoing.set(source, (outgoing.get(source) || 0) + 1);
      incoming.set(target, (incoming.get(target) || 0) + 1);
    }
  });
  internalEdges.forEach((edge) => {
    const key = entityToGroup.get(edge.source)!;
    internalCount.set(key, (internalCount.get(key) || 0) + 1);
  });

  const groupValues = [...groups.values()];
  const importance = (group: (typeof groupValues)[number]) =>
    (crossCount.get(group.key) || 0) * 16 +
    (internalCount.get(group.key) || 0) * 7 +
    group.members.filter((member) => member.type !== "FileVersion").length * 2 +
    Math.max(0, ...group.members.map((member) => Number(member.score || 0))) *
      5;
  if (groupValues.length > 10) {
    const modulePathFor = (path: string) => {
      const parts = path.split("/").filter(Boolean);
      if (
        parts[0] === "src" &&
        parts[1] === "evidence_rag" &&
        parts.length > 3
      ) {
        return parts.slice(0, 3).join("/");
      }
      if (
        parts[0] === "frontend" &&
        parts[1] === "src" &&
        parts[2] === "features" &&
        parts.length > 4
      ) {
        return parts.slice(0, 4).join("/");
      }
      if (parts[0] === "frontend" && parts[1] === "src" && parts.length > 3) {
        return parts.slice(0, 3).join("/");
      }
      if (parts[0] === "tests") return "tests";
      return parts.length > 1 ? parts.slice(0, -1).join("/") : "仓库根目录";
    };
    const modules = new Map<
      string,
      {
        id: string;
        path: string;
        repositoryId: string;
        files: typeof groupValues;
      }
    >();
    const groupToModule = new Map<string, string>();
    groupValues.forEach((group) => {
      const modulePath = modulePathFor(group.path);
      const key = `${group.repositoryId}::${modulePath}`;
      const current = modules.get(key) || {
        id: `board://code/module/${encodeURIComponent(group.repositoryId)}/${encodeURIComponent(modulePath)}`,
        path: modulePath,
        repositoryId: group.repositoryId,
        files: [],
      };
      current.files.push(group);
      modules.set(key, current);
      groupToModule.set(group.key, current.id);
    });
    const moduleEdges = aggregateVisibleRelations(
      relevantEdges.flatMap((edge) => {
        const sourceGroup = entityToGroup.get(edge.source);
        const targetGroup = entityToGroup.get(edge.target);
        const source = sourceGroup ? groupToModule.get(sourceGroup) : null;
        const target = targetGroup ? groupToModule.get(targetGroup) : null;
        if (!source || !target || source === target) return [];
        return [
          {
            ...edge,
            id: `code-module-edge://${source}/${edge.predicate}/${target}`,
            source,
            target,
            label: predicateLabel(edge.predicate),
            occurrences: edge.occurrences || 1,
          },
        ];
      }),
    );
    const moduleDegree = new Map<string, number>();
    moduleEdges.forEach((edge) => {
      moduleDegree.set(edge.source, (moduleDegree.get(edge.source) || 0) + 1);
      moduleDegree.set(edge.target, (moduleDegree.get(edge.target) || 0) + 1);
    });
    const moduleValues = [...modules.values()].sort(
      (left, right) =>
        (moduleDegree.get(right.id) || 0) - (moduleDegree.get(left.id) || 0) ||
        right.files.length - left.files.length ||
        left.path.localeCompare(right.path),
    );
    const visibleModules =
      options.showAllModules || moduleValues.length <= 14
        ? moduleValues
        : moduleValues.slice(0, 14);
    const visibleModuleIds = new Set(visibleModules.map((module) => module.id));
    const boardNodes: ResearchMapNode[] = [];
    const visibleGroupKeys = new Set<string>();
    let moduleRowTop = 0;
    for (let index = 0; index < visibleModules.length; index += 2) {
      const row = visibleModules.slice(index, index + 2).map((module) => {
        const files = [...module.files].sort(
          (left, right) =>
            importance(right) - importance(left) ||
            left.path.localeCompare(right.path),
        );
        const columns = Math.min(2, Math.max(1, files.length));
        const rows = Math.ceil(files.length / columns);
        return {
          module,
          files,
          columns,
          rows,
          width: columns === 1 ? 350 : 640,
          height: 112 + rows * 116,
        };
      });
      const rowHeight = Math.max(...row.map((value) => value.height));
      row.forEach((value, column) => {
        const frameX =
          row.length === 1 ? 0 : (column - (row.length - 1) / 2) * 760;
        const frameY = moduleRowTop + rowHeight / 2;
        const entityCount = value.files.reduce(
          (total, file) =>
            total +
            file.members.filter((member) => member.type !== "FileVersion")
              .length,
          0,
        );
        const relationCount = moduleEdges.reduce(
          (total, edge) =>
            edge.source === value.module.id || edge.target === value.module.id
              ? total + (edge.occurrences || 1)
              : total,
          0,
        );
        boardNodes.push({
          id: value.module.id,
          type: "CodeModuleFrame",
          domain: "code",
          label: value.module.path.split("/").at(-1) || value.module.path,
          meta: `${value.files.length} 个文件 · ${entityCount} 个实体 · ${relationCount} 组关系`,
          locator: value.module.path,
          repositoryId: value.module.repositoryId,
          path: value.module.path,
          width: value.width,
          height: value.height,
          x: frameX,
          y: frameY,
          source: "cluster",
          metadata: {
            responsibility_description: `框选 ${value.module.path} 下的真实文件节点，可直接进入文件职责图。`,
            file_count: value.files.length,
            entity_count: entityCount,
            relation_count: relationCount,
          },
        });
        const left = frameX - value.width / 2 + 24;
        const top = frameY - value.height / 2 + 88;
        const fileWidth = 270;
        const gap =
          (value.width - 48 - value.columns * fileWidth) /
          Math.max(1, value.columns - 1);
        value.files.forEach((file, fileIndex) => {
          visibleGroupKeys.add(file.key);
          const fileColumn = fileIndex % value.columns;
          const fileRow = Math.floor(fileIndex / value.columns);
          const entityTotal = file.members.filter(
            (member) => member.type !== "FileVersion",
          ).length;
          const relationTotal =
            (crossCount.get(file.key) || 0) +
            (internalCount.get(file.key) || 0);
          boardNodes.push({
            id: file.id,
            type: "CodeFileGroup",
            domain: "code",
            label: file.path.split("/").at(-1) || file.path,
            meta: `${file.path} · ${entityTotal} 个实体 · ${relationTotal} 条关系`,
            locator: file.path,
            repositoryId: file.repositoryId,
            path: file.path,
            width: fileWidth,
            height: 92,
            x: left + fileWidth / 2 + fileColumn * (fileWidth + gap),
            y: top + 46 + fileRow * 116,
            source: "graph",
            groupKey: value.module.id,
            metadata: {
              entity_count: entityTotal,
              relation_count: relationTotal,
              module_path: value.module.path,
            },
          });
        });
      });
      moduleRowTop += rowHeight + 84;
    }
    const visibleFileEdges = aggregateVisibleRelations(
      relevantEdges.flatMap((edge) => {
        const source = entityToGroup.get(edge.source);
        const target = entityToGroup.get(edge.target);
        if (
          !source ||
          !target ||
          source === target ||
          !visibleGroupKeys.has(source) ||
          !visibleGroupKeys.has(target)
        )
          return [];
        return [
          {
            ...edge,
            id: `code-file-edge://${source}/${edge.predicate}/${target}`,
            source: groups.get(source)!.id,
            target: groups.get(target)!.id,
            label: predicateLabel(edge.predicate),
            occurrences: edge.occurrences || 1,
          },
        ];
      }),
    );
    return {
      nodes: boardNodes,
      edges: keepEdgesWithNodes(boardNodes, visibleFileEdges),
      stats: {
        mode: "modules",
        modules: moduleValues.length,
        visibleModules: visibleModuleIds.size,
        repositories: new Set(groupValues.map((group) => group.repositoryId))
          .size,
        files: groupValues.length,
        visibleFiles: visibleGroupKeys.size,
        entities: groupValues.reduce(
          (total, group) =>
            total +
            group.members.filter((member) => member.type !== "FileVersion")
              .length,
          0,
        ),
        expandedFiles: 0,
        crossFileRelations: staticCrossEdges.length,
        internalRelations: internalEdges.filter(
          (edge) => edge.predicate !== "SEMANTIC_SIMILAR",
        ).length,
        expandedFileKeys: [],
      },
    };
  }
  const automaticExpanded = [...groupValues]
    .sort(
      (left, right) =>
        importance(right) - importance(left) ||
        left.path.localeCompare(right.path),
    )
    .slice(0, Math.min(1, groupValues.length))
    .map((group) => group.key);
  const expanded = new Set(
    options.autoExpand === false
      ? options.expandedFileKeys || []
      : automaticExpanded,
  );

  const symbolsFor = (group: (typeof groupValues)[number]) =>
    group.members
      .filter((member) => member.type !== "FileVersion")
      .sort(
        (left, right) =>
          Number(left.startLine || Number.MAX_SAFE_INTEGER) -
            Number(right.startLine || Number.MAX_SAFE_INTEGER) ||
          left.label.localeCompare(right.label),
      );
  const relationDegree = new Map<string, number>();
  relevantEdges.forEach((edge) => {
    relationDegree.set(edge.source, (relationDegree.get(edge.source) || 0) + 1);
    relationDegree.set(edge.target, (relationDegree.get(edge.target) || 0) + 1);
  });
  const visibleSymbolsFor = (group: (typeof groupValues)[number]) => {
    const symbols = symbolsFor(group);
    const classes = symbols.filter((symbol) =>
      ["CodeClass", "CodeInterface", "CodeModule"].includes(symbol.type),
    );
    const related = symbols
      .filter(
        (symbol) => !classes.some((candidate) => candidate.id === symbol.id),
      )
      .sort(
        (left, right) =>
          (relationDegree.get(right.id) || 0) -
            (relationDegree.get(left.id) || 0) ||
          Number(left.startLine || Number.MAX_SAFE_INTEGER) -
            Number(right.startLine || Number.MAX_SAFE_INTEGER),
      );
    return [...classes.slice(0, 2), ...related].slice(0, 6);
  };
  const groupHeight = (group: (typeof groupValues)[number]) =>
    expanded.has(group.key)
      ? Math.max(274, 122 + visibleSymbolsFor(group).length * 62)
      : 174;
  const relationPairs = crossEdges.map((edge) => ({
    edge,
    source: entityToGroup.get(edge.source)!,
    target: entityToGroup.get(edge.target)!,
  }));
  const adjacency = new Map<
    string,
    Array<{
      key: string;
      edge: ResearchMapEdge;
      sourceGroup: string;
      targetGroup: string;
    }>
  >();
  relationPairs.forEach(({ edge, source, target }) => {
    const relation = { edge, sourceGroup: source, targetGroup: target };
    adjacency.set(source, [
      ...(adjacency.get(source) || []),
      { key: target, ...relation },
    ]);
    adjacency.set(target, [
      ...(adjacency.get(target) || []),
      { key: source, ...relation },
    ]);
  });
  const linkedKeys = new Set(adjacency.keys());
  const standaloneKeys = !linkedKeys.size
    ? new Set(
        [...groupValues]
          .sort(
            (left, right) =>
              importance(right) - importance(left) ||
              left.path.localeCompare(right.path),
          )
          .slice(0, 6)
          .map((group) => group.key),
      )
    : new Set<string>();
  const visibleGroups = groupValues.filter(
    (group) =>
      linkedKeys.has(group.key) ||
      expanded.has(group.key) ||
      standaloneKeys.has(group.key),
  );
  const groupsByKey = new Map(groupValues.map((group) => [group.key, group]));
  const components: string[][] = [];
  const visitedGroups = new Set<string>();
  visibleGroups
    .map((group) => group.key)
    .sort()
    .forEach((start) => {
      if (visitedGroups.has(start)) return;
      const queue = [start];
      const component: string[] = [];
      visitedGroups.add(start);
      while (queue.length) {
        const current = queue.shift()!;
        component.push(current);
        (adjacency.get(current) || [])
          .map((item) => item.key)
          .sort()
          .forEach((neighbor) => {
            if (!groupsByKey.has(neighbor) || visitedGroups.has(neighbor))
              return;
            visitedGroups.add(neighbor);
            queue.push(neighbor);
          });
      }
      components.push(component);
    });
  components.sort((left, right) => {
    const leftScore = left.reduce(
      (total, key) => total + importance(groupsByKey.get(key)!),
      0,
    );
    const rightScore = right.reduce(
      (total, key) => total + importance(groupsByKey.get(key)!),
      0,
    );
    return rightScore - leftScore || left[0].localeCompare(right[0]);
  });
  const groupPositions = new Map<
    string,
    { x: number; y: number; height: number }
  >();
  let componentTop = 0;
  components.forEach((component) => {
    const hub = [...component].sort((leftKey, rightKey) => {
      const left = groupsByKey.get(leftKey)!;
      const right = groupsByKey.get(rightKey)!;
      return (
        (adjacency.get(rightKey)?.length || 0) -
          (adjacency.get(leftKey)?.length || 0) ||
        importance(right) - importance(left) ||
        left.path.localeCompare(right.path)
      );
    })[0];
    const distances = new Map<string, number>([[hub, 0]]);
    const queue = [hub];
    while (queue.length) {
      const current = queue.shift()!;
      const currentDistance = distances.get(current) || 0;
      [...(adjacency.get(current) || [])]
        .sort((left, right) => left.key.localeCompare(right.key))
        .forEach(({ key: neighbor }) => {
          if (!component.includes(neighbor) || distances.has(neighbor)) return;
          distances.set(neighbor, currentDistance + 1);
          queue.push(neighbor);
        });
    }
    const byDistance = new Map<number, string[]>();
    component.forEach((key) => {
      const distance = distances.get(key) || 0;
      byDistance.set(distance, [...(byDistance.get(distance) || []), key]);
    });
    const layers = [...byDistance.entries()]
      .sort(([left], [right]) => left - right)
      .map(([distance, keys]) => {
        const ordered = keys.sort((leftKey, rightKey) => {
          const direction = (key: string) => {
            const lowerNeighbors = (adjacency.get(key) || []).filter(
              (item) =>
                (distances.get(item.key) || 0) < (distances.get(key) || 0),
            );
            const relation = lowerNeighbors[0];
            if (!relation || relation.edge.predicate === "SEMANTIC_SIMILAR") {
              return stableHash(key) % 3;
            }
            return relation.sourceGroup === key ? 0 : 2;
          };
          const directionOrder = direction(leftKey) - direction(rightKey);
          if (directionOrder) return directionOrder;
          const left = groupsByKey.get(leftKey)!;
          const right = groupsByKey.get(rightKey)!;
          return (
            importance(right) - importance(left) ||
            left.path.localeCompare(right.path)
          );
        });
        return { distance, keys: ordered };
      });
    const furthestDistance = Math.max(
      0,
      ...layers.map((layer) => layer.distance),
    );
    const maxLayerSize = Math.max(
      1,
      ...layers.map((layer) => layer.keys.length),
    );
    const radiusY =
      furthestDistance > 0
        ? 270 +
          (furthestDistance - 1) * 190 +
          Math.max(0, maxLayerSize - 8) * 20
        : 0;
    const maxNodeHeight = Math.max(
      ...component.map((key) => groupHeight(groupsByKey.get(key)!)),
    );
    const componentHeight = Math.max(
      maxNodeHeight,
      radiusY * 2 + maxNodeHeight + 110,
    );
    const componentCenterY = componentTop + componentHeight / 2;
    layers.forEach((layer) => {
      if (layer.distance === 0) {
        const group = groupsByKey.get(layer.keys[0])!;
        groupPositions.set(group.key, {
          x: 0,
          y: componentCenterY,
          height: groupHeight(group),
        });
        return;
      }
      const layerRadiusX = 480 + (layer.distance - 1) * 300;
      const layerRadiusY = 270 + (layer.distance - 1) * 190;
      layer.keys.forEach((key, index) => {
        const group = groupsByKey.get(key)!;
        const count = layer.keys.length;
        const angle =
          count === 1
            ? (() => {
                const relation = (adjacency.get(key) || []).find(
                  (item) => (distances.get(item.key) || 0) < layer.distance,
                );
                if (
                  !relation ||
                  relation.edge.predicate === "SEMANTIC_SIMILAR"
                ) {
                  return stableHash(key) % 2 ? Math.PI / 2 : -Math.PI / 2;
                }
                return relation.sourceGroup === key ? Math.PI : 0;
              })()
            : -Math.PI / 2 +
              (Math.PI * 2 * index) / count +
              (layer.distance % 2 ? Math.PI / count : 0);
        groupPositions.set(key, {
          x: Math.cos(angle) * layerRadiusX,
          y: componentCenterY + Math.sin(angle) * layerRadiusY,
          height: groupHeight(group),
        });
      });
    });
    componentTop += componentHeight + 140;
  });
  const dependencyOnly =
    relationTypes.size === 1 && relationTypes.has("IMPORTS");
  if (dependencyOnly) {
    groupPositions.clear();
    componentTop = 0;
    components.forEach((component) => {
      const componentSet = new Set(component);
      const directed = relationPairs.filter(
        (pair) =>
          pair.edge.predicate === "IMPORTS" &&
          componentSet.has(pair.source) &&
          componentSet.has(pair.target),
      );
      const indegree = new Map(component.map((key) => [key, 0]));
      const outgoingTargets = new Map<string, string[]>();
      directed.forEach((pair) => {
        indegree.set(pair.target, (indegree.get(pair.target) || 0) + 1);
        outgoingTargets.set(pair.source, [
          ...(outgoingTargets.get(pair.source) || []),
          pair.target,
        ]);
      });
      const rank = new Map(component.map((key) => [key, 0]));
      const queue = component
        .filter((key) => (indegree.get(key) || 0) === 0)
        .sort(
          (left, right) =>
            importance(groupsByKey.get(right)!) -
            importance(groupsByKey.get(left)!),
        );
      const processed = new Set<string>();
      while (queue.length) {
        const current = queue.shift()!;
        if (processed.has(current)) continue;
        processed.add(current);
        (outgoingTargets.get(current) || []).forEach((target) => {
          rank.set(
            target,
            Math.max(rank.get(target) || 0, (rank.get(current) || 0) + 1),
          );
          indegree.set(target, Math.max(0, (indegree.get(target) || 0) - 1));
          if ((indegree.get(target) || 0) === 0) queue.push(target);
        });
      }
      component
        .filter((key) => !processed.has(key))
        .sort(
          (left, right) =>
            importance(groupsByKey.get(right)!) -
            importance(groupsByKey.get(left)!),
        )
        .forEach((key, index) =>
          rank.set(key, index % Math.max(1, Math.ceil(component.length / 4))),
        );
      const byRank = new Map<number, string[]>();
      component.forEach((key) => {
        const value = rank.get(key) || 0;
        byRank.set(value, [...(byRank.get(value) || []), key]);
      });
      const ranks = [...byRank.keys()].sort((left, right) => left - right);
      const layerHeights = ranks.map((value) =>
        (byRank.get(value) || []).reduce(
          (total, key) => total + groupHeight(groupsByKey.get(key)!) + 74,
          -74,
        ),
      );
      const componentHeight = Math.max(174, ...layerHeights);
      ranks.forEach((value, rankIndex) => {
        const keys = (byRank.get(value) || []).sort(
          (left, right) =>
            importance(groupsByKey.get(right)!) -
              importance(groupsByKey.get(left)!) || left.localeCompare(right),
        );
        let y = componentTop + (componentHeight - layerHeights[rankIndex]) / 2;
        keys.forEach((key) => {
          const group = groupsByKey.get(key)!;
          const height = groupHeight(group);
          groupPositions.set(key, {
            x: (rankIndex - (ranks.length - 1) / 2) * 460,
            y: y + height / 2,
            height,
          });
          y += height + 74;
        });
      });
      componentTop += componentHeight + 170;
    });
  }
  if (!linkedKeys.size) {
    groupPositions.clear();
    const ordered = [...visibleGroups].sort(
      (left, right) =>
        importance(right) - importance(left) ||
        left.path.localeCompare(right.path),
    );
    const columns = Math.min(
      3,
      Math.max(1, Math.ceil(Math.sqrt(ordered.length))),
    );
    const rows = Math.ceil(ordered.length / columns);
    ordered.forEach((group, index) => {
      const column = index % columns;
      const row = Math.floor(index / columns);
      groupPositions.set(group.key, {
        x: (column - (columns - 1) / 2) * 420,
        y: (row - (rows - 1) / 2) * 270,
        height: groupHeight(group),
      });
    });
  }
  const groupWidth = (group: (typeof groupValues)[number]) =>
    expanded.has(group.key) ? 390 : 306;
  for (let iteration = 0; iteration < 24; iteration += 1) {
    let moved = false;
    for (let leftIndex = 0; leftIndex < visibleGroups.length; leftIndex += 1) {
      const left = visibleGroups[leftIndex];
      const leftPosition = groupPositions.get(left.key);
      if (!leftPosition) continue;
      for (
        let rightIndex = leftIndex + 1;
        rightIndex < visibleGroups.length;
        rightIndex += 1
      ) {
        const right = visibleGroups[rightIndex];
        const rightPosition = groupPositions.get(right.key);
        if (!rightPosition) continue;
        const dx = rightPosition.x - leftPosition.x;
        const dy = rightPosition.y - leftPosition.y;
        const overlapX =
          (groupWidth(left) + groupWidth(right)) / 2 + 54 - Math.abs(dx);
        const overlapY =
          (leftPosition.height + rightPosition.height) / 2 + 54 - Math.abs(dy);
        if (overlapX <= 0 || overlapY <= 0) continue;
        moved = true;
        if (overlapX < overlapY) {
          const direction =
            Math.sign(dx) || (stableHash(right.key) % 2 ? 1 : -1);
          const offset = overlapX / 2 + 1;
          leftPosition.x -= direction * offset;
          rightPosition.x += direction * offset;
        } else {
          const direction =
            Math.sign(dy) || (stableHash(right.key) % 2 ? 1 : -1);
          const offset = overlapY / 2 + 1;
          leftPosition.y -= direction * offset;
          rightPosition.y += direction * offset;
        }
      }
    }
    if (!moved) break;
  }

  const containerByGroup = new Map<string, ResearchMapNode>();
  const symbolNodes: ResearchMapNode[] = [];
  const hierarchyEdges: ResearchMapEdge[] = [];
  visibleGroups.forEach((group) => {
    const position = groupPositions.get(group.key) || {
      x: 0,
      y: 0,
      height: 174,
    };
    const symbols = visibleSymbolsFor(group);
    const allSymbols = symbolsFor(group);
    const fileVersion = group.members.find(
      (member) => member.type === "FileVersion",
    );
    const repository = nodes.find(
      (node) => node.type === "Repository" && node.id === group.repositoryId,
    );
    const isExpanded = expanded.has(group.key);
    const container: ResearchMapNode = {
      ...(fileVersion || group.members[0]),
      id: group.id,
      type: "CodeFileContainer",
      domain: "code",
      label: group.path.split("/").filter(Boolean).at(-1) || group.path,
      meta: [
        directoryKey(group.path),
        `${allSymbols.length} 个实体`,
        `${crossCount.get(group.key) || 0} 个外部连接`,
      ].join(" · "),
      locator: fileVersion?.locator || group.path,
      repositoryId: group.repositoryId,
      path: group.path,
      status: undefined,
      groupKey: group.key,
      expanded: isExpanded,
      width: isExpanded ? 390 : 306,
      height: position.height,
      x: position.x,
      y: position.y,
      source: "cluster",
      children: codeChildSummary(group.members),
      metadata: {
        ...(fileVersion?.metadata || {}),
        visible_symbol_count: symbols.length,
        hidden_symbol_count: Math.max(0, allSymbols.length - symbols.length),
        cross_file_relations: crossCount.get(group.key) || 0,
      },
      version:
        fileVersion?.version ||
        group.members.find((member) => member.version)?.version,
      qualifiedName: repository?.label || group.repositoryId,
    };
    containerByGroup.set(group.key, container);
    if (!isExpanded) return;

    const classes = symbols.filter((symbol) =>
      ["CodeClass", "CodeInterface", "CodeModule"].includes(symbol.type),
    );
    const parentForMethod = new Map<string, ResearchMapNode>();
    symbols.forEach((symbol) => {
      if (!["CodeMethod", "CodeFunction"].includes(symbol.type)) return;
      const qualifiedName = symbol.qualifiedName || "";
      const parent = classes
        .filter((candidate) => {
          const candidateName = candidate.qualifiedName || candidate.label;
          return qualifiedName.startsWith(`${candidateName}.`);
        })
        .sort(
          (left, right) =>
            (right.qualifiedName || right.label).length -
            (left.qualifiedName || left.label).length,
        )[0];
      if (parent) parentForMethod.set(symbol.id, parent);
    });
    const top = position.y - position.height / 2 + 112;
    symbols.forEach((symbol, index) => {
      const parent = parentForMethod.get(symbol.id);
      symbolNodes.push({
        ...symbol,
        parentId: group.id,
        groupKey: group.key,
        width: parent ? 308 : 326,
        height: 54,
        x: position.x + (parent ? 14 : 0),
        y: top + index * 62,
      });
      if (parent) {
        hierarchyEdges.push({
          id: `code-collaboration-parent://${parent.id}/${symbol.id}`,
          source: parent.id,
          predicate: "belongs_to_class",
          target: symbol.id,
          derivation: "qualified_name",
          confidence: 1,
          review_status: "confirmed",
          domain: "code",
          label: "包含方法",
          tone: "code",
          structural: true,
        });
      }
    });
  });

  const mappedRelations = relevantEdges.flatMap((edge) => {
    const sourceGroupKey = entityToGroup.get(edge.source);
    const targetGroupKey = entityToGroup.get(edge.target);
    if (!sourceGroupKey || !targetGroupKey) return [];
    if (sourceGroupKey === targetGroupKey) return [];
    const source = containerByGroup.get(sourceGroupKey)?.id;
    const target = containerByGroup.get(targetGroupKey)?.id;
    if (!source || !target || source === target) return [];
    return [
      {
        ...edge,
        id: `code-collaboration-rollup://${source}/${edge.predicate}/${target}`,
        source,
        target,
        label:
          edge.predicate === "SEMANTIC_SIMILAR"
            ? "语义邻近"
            : `跨文件${predicateLabel(edge.predicate)}`,
        tone: "code" as const,
        scope: edge.predicate === "SEMANTIC_SIMILAR",
      },
    ];
  });
  const containerNodes = [...containerByGroup.values()];
  // This is a relationship canvas rather than a repository inventory. Keeping
  // disconnected folders and repository headers on this surface forced the
  // useful dependency graph to zoom out and added long, meaningless scope
  // lines. Repository and hidden-file counts remain available through stats
  // and the code browser.
  const boardNodes = [...containerNodes, ...symbolNodes];
  return {
    nodes: boardNodes,
    edges: keepEdgesWithNodes(
      boardNodes,
      aggregateVisibleRelations([...hierarchyEdges, ...mappedRelations]),
    ),
    stats: {
      mode: "files",
      modules: new Set(groupValues.map((group) => directoryKey(group.path)))
        .size,
      visibleModules: new Set(
        visibleGroups.map((group) => directoryKey(group.path)),
      ).size,
      repositories: new Set(groupValues.map((group) => group.repositoryId))
        .size,
      files: groupValues.length,
      visibleFiles: containerNodes.length,
      entities: groupValues.reduce(
        (total, group) => total + symbolsFor(group).length,
        0,
      ),
      expandedFiles: expanded.size,
      crossFileRelations: staticCrossEdges.length,
      internalRelations: internalEdges.filter(
        (edge) => edge.predicate !== "SEMANTIC_SIMILAR",
      ).length,
      expandedFileKeys: [...expanded].sort(),
    },
  };
}

/**
 * Lay out one code entity as a readable dependency branch instead of a generic
 * force cloud: incoming relations on the left, focus in the centre and outgoing
 * relations on the right.  The API remains the authority for which edges exist.
 */
export function buildCodeRelationBoard(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  focusEntityId: string,
): { nodes: ResearchMapNode[]; edges: ResearchMapEdge[] } {
  const focus = nodes.find((node) => node.id === focusEntityId);
  if (!focus) return { nodes: [], edges: [] };
  const directEdges = edges.filter(
    (edge) => edge.source === focusEntityId || edge.target === focusEntityId,
  );
  const incomingIds = new Set(
    directEdges
      .filter((edge) => edge.target === focusEntityId)
      .map((edge) => edge.source),
  );
  const outgoingIds = new Set(
    directEdges
      .filter((edge) => edge.source === focusEntityId)
      .map((edge) => edge.target),
  );
  const relationRank = (nodeId: string, incoming: boolean) => {
    const edge = directEdges.find((item) =>
      incoming
        ? item.source === nodeId && item.target === focusEntityId
        : item.source === focusEntityId && item.target === nodeId,
    );
    return ["CALLS", "REFERENCES", "IMPORTS", "DEFINES", "CONTAINS"].indexOf(
      edge?.predicate || "",
    );
  };
  const byLabel = (left: ResearchMapNode, right: ResearchMapNode) =>
    left.label.localeCompare(right.label);
  const incoming = nodes
    .filter((node) => incomingIds.has(node.id))
    .sort(
      (left, right) =>
        relationRank(left.id, true) - relationRank(right.id, true) ||
        byLabel(left, right),
    );
  const outgoing = nodes
    .filter((node) => outgoingIds.has(node.id) && !incomingIds.has(node.id))
    .sort(
      (left, right) =>
        relationRank(left.id, false) - relationRank(right.id, false) ||
        byLabel(left, right),
    );
  const positionWing = (
    values: ResearchMapNode[],
    side: -1 | 1,
  ): ResearchMapNode[] => {
    const rows = Math.max(
      1,
      Math.min(9, Math.ceil(Math.sqrt(values.length * 1.45))),
    );
    return values.map((node, index) => {
      const column = Math.floor(index / rows);
      const row = index % rows;
      const rowsInColumn = Math.min(rows, values.length - column * rows);
      return {
        ...node,
        x: side * (390 + column * 292),
        y: (row - (rowsInColumn - 1) / 2) * 126,
      };
    });
  };
  const boardNodes = [
    ...positionWing(incoming, -1),
    {
      ...focus,
      x: 0,
      y: 0,
      status: focus.status || "focus",
    },
    ...positionWing(outgoing, 1),
  ];
  const boardEdges = directEdges.map((edge) => {
    const incomingEdge = edge.target === focusEntityId;
    const incomingLabels: Record<string, string> = {
      CALLS: "被调用",
      REFERENCES: "被引用",
      IMPORTS: "被导入",
      DEFINES: "定义于",
      CONTAINS: "包含于",
    };
    return {
      ...edge,
      label: incomingEdge
        ? incomingLabels[edge.predicate] ||
          `被${predicateLabel(edge.predicate)}`
        : predicateLabel(edge.predicate),
      tone: "code" as const,
      structural: ["DEFINES", "CONTAINS", "HAS_COMMIT"].includes(
        edge.predicate,
      ),
    };
  });
  return {
    nodes: boardNodes,
    edges: keepEdgesWithNodes(boardNodes, boardEdges),
  };
}

export function buildCodeVectorBoard(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  sourceNodeIds: string[],
): { nodes: ResearchMapNode[]; edges: ResearchMapEdge[] } {
  const requested = new Set(sourceNodeIds);
  const boardNodes = nodes
    .filter((node) => requested.has(node.id))
    .map((node) => ({ ...node, x: 0, y: 0 }));
  return {
    nodes: boardNodes,
    edges: keepEdgesWithNodes(boardNodes, edges),
  };
}

export function graphBounds(
  nodes: ResearchMapNode[],
  positions: Record<string, { x: number; y: number }> = {},
): MapBounds {
  if (!nodes.length) {
    return {
      minX: -400,
      minY: -300,
      maxX: 400,
      maxY: 300,
      width: 800,
      height: 600,
    };
  }
  const values = nodes.map((node) => positions[node.id] || node);
  const minX = Math.min(
    ...values.map(
      (node, index) => node.x - layoutNodeSize(nodes[index]).width / 2,
    ),
  );
  const maxX = Math.max(
    ...values.map(
      (node, index) => node.x + layoutNodeSize(nodes[index]).width / 2,
    ),
  );
  const minY = Math.min(
    ...values.map(
      (node, index) => node.y - layoutNodeSize(nodes[index]).height / 2,
    ),
  );
  const maxY = Math.max(
    ...values.map(
      (node, index) => node.y + layoutNodeSize(nodes[index]).height / 2,
    ),
  );
  return {
    minX,
    minY,
    maxX,
    maxY,
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
  };
}

export function connectedEdges(
  nodeId: string,
  edges: ResearchMapEdge[],
): ResearchMapEdge[] {
  return edges.filter(
    (edge) => edge.source === nodeId || edge.target === nodeId,
  );
}

function evidenceText(
  metadata: Record<string, unknown> | undefined,
  keys: string[],
): string {
  for (const key of keys) {
    const value = metadata?.[key];
    if (typeof value === "string" && value.trim()) {
      return value.replace(/\s+/g, " ").trim().slice(0, 420);
    }
  }
  return "";
}

/**
 * Builds the reviewer-facing facts without manufacturing missing provenance.
 * Every fallback is explicitly unavailable so callers can fail closed.
 */
export function buildEvidenceReviewProfile({
  node,
  edges,
  nodesById,
  projectId,
  projectAclRef,
}: {
  node: ResearchMapNode;
  edges: ResearchMapEdge[];
  nodesById: Map<string, ResearchMapNode>;
  projectId: string;
  projectAclRef?: string | null;
}): EvidenceReviewProfile {
  const field = (
    value: string | null | undefined,
    missing: string,
  ): EvidenceReviewField => {
    const normalized = value?.trim() || "";
    return normalized
      ? { value: normalized, available: true }
      : { value: missing, available: false };
  };
  const sourceLabels: Record<ResearchMapNode["source"], string> = {
    workspace: "项目工作区",
    graph: "关系图谱载荷",
    binding: "绑定候选载荷",
    neighbor: "相邻节点载荷",
    cluster: "当前视图聚合",
  };
  const scope = node.repositoryId
    ? `${projectId} / ${node.repositoryId.split("/").at(-1)}`
    : projectId;
  const acl =
    evidenceText(node.metadata, ["acl_ref", "acl", "classification"]) ||
    projectAclRef ||
    "";
  const citation =
    evidenceText(node.metadata, ["citation", "source_uri", "locator"]) ||
    node.locator;
  const summary = evidenceText(node.metadata, [
    "evidence",
    "snippet",
    "summary",
    "description",
    "content",
  ]);
  const relations = connectedEdges(
    node.id,
    edges,
  ).flatMap<EvidenceReviewRelation>((edge) => {
    const source = nodesById.get(edge.source);
    const target = nodesById.get(edge.target);
    if (!source || !target) return [];
    const direction = edge.source === node.id ? "outgoing" : "incoming";
    return [
      {
        edge,
        direction,
        source,
        target,
        other: direction === "outgoing" ? target : source,
      },
    ];
  });
  const reviewCandidateId = relations.find(
    ({ edge }) => edge.review_status === "unreviewed" && edge.reviewCandidateId,
  )?.edge.reviewCandidateId;

  return {
    identity: field(node.id, "身份标识未提供"),
    type: field(node.type, "节点类型未提供"),
    source: field(sourceLabels[node.source], "来源未提供"),
    scope: field(scope, "范围未提供"),
    acl: field(acl, "ACL 未提供"),
    version: field(node.version, "版本未提供"),
    citation: field(citation, "引用定位未提供"),
    summary: field(summary, "内容摘要未提供"),
    incoming: relations.filter(({ direction }) => direction === "incoming"),
    outgoing: relations.filter(({ direction }) => direction === "outgoing"),
    reviewCandidateId,
  };
}

const inspectorHierarchyPredicates = new Set([
  "belongs_to_class",
  "DEFINES",
  "CONTAINS",
  "contains_file",
  "contains_directory",
  "HAS_ITEM",
  "HAS_TURN",
  "HAS_EPISODE",
]);

function inspectorCodeLevel(node: ResearchMapNode): number {
  if (node.type === "Repository") return 1;
  if (
    [
      "CodeFileContainer",
      "FileVersion",
      "CodeFileGroup",
      "CodeFileFocus",
    ].includes(node.type)
  ) {
    return 2;
  }
  if (["CodeClass", "CodeInterface", "CodeModule"].includes(node.type))
    return 3;
  if (node.type === "CodeResponsibilityGroup") return 4;
  if (["CodeMethod", "CodeFunction", "CodeSymbol"].includes(node.type))
    return 4;
  return 0;
}

function contextChildNode(
  child: NonNullable<ResearchMapNode["children"]>[number],
  parent: ResearchMapNode,
  nodesById: Map<string, ResearchMapNode>,
): ResearchMapNode {
  return (
    nodesById.get(child.id) || {
      id: child.id,
      type: child.type,
      domain: parent.domain,
      label: child.label,
      meta: child.meta || "",
      locator: child.id,
      repositoryId: parent.repositoryId,
      path: parent.path,
      x: 0,
      y: 0,
      source: "cluster",
    }
  );
}

/**
 * Build a small, stable hierarchy view for the inspector. The main graph can
 * stay dense and exploratory; this context map answers one narrower question:
 * where is the selected entity in its repository/file/class chain, and what
 * does it directly contain or connect to?
 */
export function buildInspectorContextMap(
  focus: ResearchMapNode,
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  maxChildren = 5,
  maxRelations = 5,
): InspectorContextMap {
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const trailFromFocus: ResearchMapNode[] = [focus];
  const visited = new Set([focus.id]);
  let cursor = focus;

  for (let depth = 0; depth < 6; depth += 1) {
    const structuralParents = edges
      .filter(
        (edge) =>
          edge.target === cursor.id &&
          (edge.structural || inspectorHierarchyPredicates.has(edge.predicate)),
      )
      .sort((left, right) => {
        const priority = (edge: ResearchMapEdge) =>
          edge.predicate === "belongs_to_class"
            ? 0
            : edge.predicate === "DEFINES"
              ? 1
              : 2;
        return priority(left) - priority(right);
      });
    const structuralParent = structuralParents
      .map((edge) => nodesById.get(edge.source))
      .find((candidate): candidate is ResearchMapNode =>
        Boolean(candidate && !visited.has(candidate.id)),
      );
    const explicitParent =
      cursor.parentId && !visited.has(cursor.parentId)
        ? nodesById.get(cursor.parentId)
        : undefined;
    const parent = structuralParent || explicitParent;
    if (!parent) break;
    trailFromFocus.push(parent);
    visited.add(parent.id);
    cursor = parent;
  }

  const trailNodes = trailFromFocus
    .reverse()
    // A commit identifies the immutable version of the file; it is not
    // another code-containment level. Keeping it inside the L1/L2/L3 trail
    // produced duplicate "L2" labels. Version remains visible in the header
    // and locator tab.
    .filter((node) => focus.type === "Commit" || node.type !== "Commit");
  if (
    focus.domain === "code" &&
    ["CodeMethod", "CodeFunction"].includes(focus.type) &&
    !trailNodes.some((node) =>
      ["CodeClass", "CodeInterface"].includes(node.type),
    )
  ) {
    const qualifiedSegments = String(focus.qualifiedName || "")
      .split(".")
      .filter(Boolean);
    const parentName = qualifiedSegments.at(-2) || "";
    if (/^[A-Z][A-Za-z0-9_]*$/.test(parentName)) {
      const focusIndex = trailNodes.findIndex((node) => node.id === focus.id);
      trailNodes.splice(Math.max(0, focusIndex), 0, {
        id: `inspector://class/${encodeURIComponent(
          focus.repositoryId || "",
        )}/${encodeURIComponent(focus.path || "")}/${encodeURIComponent(parentName)}`,
        type: "CodeClass",
        domain: "code",
        label: parentName,
        meta: focus.path || "由限定名推断",
        locator: qualifiedSegments.slice(0, -1).join("."),
        repositoryId: focus.repositoryId,
        path: focus.path,
        x: 0,
        y: 0,
        source: "cluster",
      });
    }
  }
  if (focus.domain === "code" && focus.repositoryId) {
    const hasRepository = trailNodes.some((node) => node.type === "Repository");
    if (!hasRepository) {
      const repository = nodes.find(
        (node) => node.type === "Repository" && node.id === focus.repositoryId,
      );
      const fileContainer = trailNodes.find(
        (node) => node.type === "CodeFileContainer",
      );
      trailNodes.unshift(
        repository || {
          id: focus.repositoryId,
          type: "Repository",
          domain: "code",
          label:
            fileContainer?.qualifiedName ||
            focus.repositoryId.split("/").filter(Boolean).at(-1) ||
            "代码仓库",
          meta: "当前代码仓库",
          locator: focus.repositoryId,
          repositoryId: focus.repositoryId,
          x: 0,
          y: 0,
          source: "cluster",
        },
      );
    }
  }

  const trailIds = new Set(trailNodes.map((node) => node.id));
  const hierarchyChildrenAll = [
    ...(focus.children || []).map((child) =>
      contextChildNode(child, focus, nodesById),
    ),
    ...edges
      .filter(
        (edge) =>
          edge.source === focus.id &&
          (edge.structural || inspectorHierarchyPredicates.has(edge.predicate)),
      )
      .map((edge) => nodesById.get(edge.target))
      .filter((candidate): candidate is ResearchMapNode => Boolean(candidate)),
  ].filter(
    (node, index, values) =>
      !trailIds.has(node.id) &&
      values.findIndex((candidate) => candidate.id === node.id) === index,
  );
  const hierarchyChildren = hierarchyChildrenAll.slice(0, maxChildren);

  const childIds = new Set(hierarchyChildren.map((node) => node.id));
  const relationEdgesAll = connectedEdges(focus.id, edges)
    .filter(
      (edge) =>
        !edge.structural && !inspectorHierarchyPredicates.has(edge.predicate),
    )
    .filter((edge) => {
      const otherId = edge.source === focus.id ? edge.target : edge.source;
      return (
        !trailIds.has(otherId) &&
        !childIds.has(otherId) &&
        nodesById.has(otherId)
      );
    });
  const relationEdges = relationEdgesAll.slice(0, maxRelations);
  const relationNodes = relationEdges
    .map((edge) =>
      nodesById.get(edge.source === focus.id ? edge.target : edge.source),
    )
    .filter((candidate): candidate is ResearchMapNode => Boolean(candidate))
    .filter(
      (node, index, values) =>
        values.findIndex((candidate) => candidate.id === node.id) === index,
    );

  const trail: InspectorContextNode[] = trailNodes.map((node) => ({
    node,
    role: node.id === focus.id ? "focus" : "ancestor",
    level: inspectorCodeLevel(node) || trailNodes.indexOf(node) + 1,
  }));
  const focusLevel =
    inspectorCodeLevel(focus) ||
    trail.find((item) => item.node.id === focus.id)?.level ||
    trail.length;
  const totalLevels =
    focus.domain === "code"
      ? focus.type === "CodeResponsibilityGroup"
        ? 5
        : 4
      : Math.max(focusLevel, trail.length);
  const children: InspectorContextNode[] = hierarchyChildren.map((node) => ({
    node,
    role: "child",
    level:
      focus.type === "CodeResponsibilityGroup"
        ? 5
        : inspectorCodeLevel(node) || Math.min(totalLevels, focusLevel + 1),
  }));
  const relations: InspectorContextNode[] = relationNodes.map((node) => ({
    node,
    role: "relation",
    level: inspectorCodeLevel(node) || focusLevel,
  }));

  const hierarchyEdges: InspectorContextEdge[] = trailNodes
    .slice(1)
    .map((node, index) => {
      const source = trailNodes[index];
      const matching = edges.find(
        (edge) => edge.source === source.id && edge.target === node.id,
      );
      return {
        id: `inspector-hierarchy://${source.id}/${node.id}`,
        source: source.id,
        target: node.id,
        label:
          matching?.label ||
          (source.type === "Repository"
            ? "包含文件"
            : node.id === focus.id
              ? "定位于"
              : "包含"),
        predicate: matching?.predicate || "contains",
        role: "hierarchy" as const,
      };
    });
  const childEdges: InspectorContextEdge[] = children.map(({ node }) => {
    const matching = edges.find(
      (edge) => edge.source === focus.id && edge.target === node.id,
    );
    return {
      id: `inspector-child://${focus.id}/${node.id}`,
      source: focus.id,
      target: node.id,
      label: matching?.label || "包含",
      predicate: matching?.predicate || "contains",
      role: "hierarchy" as const,
    };
  });
  const mappedRelationEdges: InspectorContextEdge[] = relationEdges.map(
    (edge) => ({
      id: `inspector-relation://${edge.id}`,
      source: edge.source,
      target: edge.target,
      label: edge.label,
      predicate: edge.predicate,
      role: "relation" as const,
    }),
  );

  return {
    trail,
    children,
    relations,
    edges: [...hierarchyEdges, ...childEdges, ...mappedRelationEdges],
    childTotal: hierarchyChildrenAll.length,
    relationTotal: relationEdgesAll.length,
    focusLevel,
    totalLevels,
  };
}
