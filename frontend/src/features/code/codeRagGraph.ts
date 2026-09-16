import type {
  CodeCommitDetail,
  CodeRefs,
  GitCommit,
  GraphEdge,
  GraphNode,
  GraphSnapshot,
  Repository,
} from "../../lib/types";

export const CODE_GRAPH_TYPES = [
  "Repository",
  "Branch",
  "WorktreeSnapshot",
  "Commit",
  "FileVersion",
  "CodeSymbol",
  "DiffHunk",
  "TestResult",
] as const;

export type CodeGraphNodeType = (typeof CODE_GRAPH_TYPES)[number];
export type CodeGraphOrigin = "graph" | "git" | "projection";

export type CodeRagNode = {
  id: string;
  type: CodeGraphNodeType;
  label: string;
  locator: string;
  origin: CodeGraphOrigin;
  repositoryId: string;
  version?: string | null;
  path?: string | null;
  language?: string | null;
  startLine?: number | null;
  endLine?: number | null;
  metadata: Record<string, unknown>;
  x: number;
  y: number;
};

export type CodeRagEdge = {
  id: string;
  source: string;
  predicate: string;
  target: string;
  origin: CodeGraphOrigin;
  confidence?: number;
  reviewStatus?: string;
};

export type CodeRagGraphModel = {
  nodes: CodeRagNode[];
  edges: CodeRagEdge[];
  width: number;
  height: number;
  counts: Record<CodeGraphNodeType, number>;
  loadedCounts: Record<CodeGraphNodeType, number>;
  sampled: boolean;
};

export type CodeGraphPoint = { x: number; y: number };
export type CodeGraphLayoutMode = "network" | "layered";
export type CodeGraphView = { x: number; y: number; scale: number };

export type BuildCodeRagGraphInput = {
  repository: Repository;
  refs?: CodeRefs;
  history: GitCommit[];
  commitDetails?: CodeCommitDetail[];
  snapshot?: GraphSnapshot;
  query?: string;
  enabledTypes?: ReadonlySet<CodeGraphNodeType>;
  enabledRelations?: ReadonlySet<string>;
  priorityNodeIds?: ReadonlySet<string>;
};

const NODE_WIDTH = 174;
const NODE_HEIGHT = 58;
const NETWORK_NODE_RADIUS = 27;
const NETWORK_NODE_CENTER_Y = 30;
const NETWORK_NODE_FOOTPRINT_WIDTH = 112;
const NETWORK_NODE_FOOTPRINT_HEIGHT = 88;
const LAYER_X: Record<CodeGraphNodeType, number> = {
  Repository: 34,
  Branch: 270,
  WorktreeSnapshot: 270,
  Commit: 510,
  FileVersion: 760,
  DiffHunk: 760,
  CodeSymbol: 1015,
  TestResult: 1015,
};

const TYPE_LIMITS: Record<CodeGraphNodeType, number> = {
  Repository: 1,
  Branch: 6,
  WorktreeSnapshot: 1,
  Commit: 9,
  FileVersion: 8,
  CodeSymbol: 10,
  DiffHunk: 5,
  TestResult: 4,
};

const EMPTY_COUNTS = (): Record<CodeGraphNodeType, number> => ({
  Repository: 0,
  Branch: 0,
  WorktreeSnapshot: 0,
  Commit: 0,
  FileVersion: 0,
  CodeSymbol: 0,
  DiffHunk: 0,
  TestResult: 0,
});

const acceptedGraphType = (value: string): CodeGraphNodeType | null => {
  if (value === "Worktree") return "WorktreeSnapshot";
  return (CODE_GRAPH_TYPES as readonly string[]).includes(value)
    ? (value as CodeGraphNodeType)
    : null;
};

const normalizedPredicate = (value: string) => {
  const normalized = value.trim().replace(/\s+/g, "_").toUpperCase();
  if (normalized === "PARENT_OF") return "PARENT";
  if (normalized === "CONTAINS_DIFF") return "CONTAINS";
  return normalized;
};

const graphNodeBelongsToRepository = (node: GraphNode, repositoryId: string) =>
  node.id === repositoryId ||
  node.repository_id === repositoryId ||
  (typeof node.metadata?.repository_id === "string" &&
    node.metadata.repository_id === repositoryId);

function graphNodeToCodeNode(
  node: GraphNode,
  repositoryId: string,
): Omit<CodeRagNode, "x" | "y"> | null {
  const type = acceptedGraphType(node.type);
  if (!type || !graphNodeBelongsToRepository(node, repositoryId)) return null;
  return {
    id: node.id,
    type,
    label: node.label || node.path || node.qualified_name || type,
    locator: node.locator || node.id,
    origin: "graph",
    repositoryId,
    version: node.version,
    path: node.path,
    language: node.language,
    startLine: node.start_line,
    endLine: node.end_line,
    metadata: node.metadata || {},
  };
}

function nodeSearchText(node: Omit<CodeRagNode, "x" | "y">) {
  return [
    node.label,
    node.type,
    node.locator,
    node.version,
    node.path,
    node.language,
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
}

function appendUnique<T extends { id: string }>(target: T[], item: T) {
  if (!target.some((candidate) => candidate.id === item.id)) target.push(item);
}

function sortByGraphDegree(nodes: GraphNode[], edges: GraphEdge[]) {
  const degree = new Map<string, number>();
  for (const edge of edges) {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  }
  return [...nodes].sort(
    (left, right) =>
      (degree.get(right.id) || 0) - (degree.get(left.id) || 0) ||
      left.label.localeCompare(right.label),
  );
}

function layoutNodes(
  nodes: Array<Omit<CodeRagNode, "x" | "y">>,
): CodeRagNode[] {
  const groups = new Map<number, Array<Omit<CodeRagNode, "x" | "y">>>();
  for (const node of nodes) {
    const x = LAYER_X[node.type];
    groups.set(x, [...(groups.get(x) || []), node]);
  }
  return nodes.map((node) => {
    const group = groups.get(LAYER_X[node.type]) || [];
    const index = group.findIndex((item) => item.id === node.id);
    const rowGap = NODE_HEIGHT + 20;
    const centeredOffset = Math.max(0, (13 - group.length) * 16);
    return {
      ...node,
      x: LAYER_X[node.type],
      y: 70 + centeredOffset + Math.max(0, index) * rowGap,
    };
  });
}

const hashString = (value: string) => {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
};

const clamp = (value: number, minimum: number, maximum: number) =>
  Math.min(maximum, Math.max(minimum, value));

const NETWORK_RING: Record<CodeGraphNodeType, number> = {
  Repository: 0,
  Branch: 150,
  WorktreeSnapshot: 150,
  Commit: 245,
  FileVersion: 315,
  DiffHunk: 315,
  CodeSymbol: 390,
  TestResult: 390,
};

/**
 * Deterministic force layout for the bounded repository overview.
 * It intentionally runs once during model preparation rather than animating,
 * so repeated renders do not make the graph drift under the user's pointer.
 */
export function layoutCodeRagNetwork(
  nodes: readonly CodeRagNode[],
  edges: readonly CodeRagEdge[],
  width = 1280,
  height = 820,
): Record<string, CodeGraphPoint> {
  if (!nodes.length) return {};
  const center = { x: width / 2, y: height / 2 };
  const points = new Map<string, CodeGraphPoint>();
  const targets = new Map<string, CodeGraphPoint>();
  const ordered = [...nodes].sort((left, right) =>
    left.id.localeCompare(right.id),
  );

  for (const node of ordered) {
    const seed = hashString(node.id);
    const angle = ((seed % 3600) / 3600) * Math.PI * 2;
    const ring = NETWORK_RING[node.type];
    const jitter = node.type === "Repository" ? 0 : ((seed >>> 8) % 37) - 18;
    const target = {
      x: center.x + Math.cos(angle) * (ring + jitter),
      y: center.y + Math.sin(angle) * (ring * 0.72 + jitter * 0.4),
    };
    targets.set(node.id, target);
    points.set(node.id, { ...target });
  }

  const validEdges = edges.filter(
    (edge) => points.has(edge.source) && points.has(edge.target),
  );
  const velocities = new Map(ordered.map((node) => [node.id, { x: 0, y: 0 }]));

  for (let iteration = 0; iteration < 220; iteration += 1) {
    const force = new Map(ordered.map((node) => [node.id, { x: 0, y: 0 }]));
    const temperature = 1 - iteration / 240;

    for (let leftIndex = 0; leftIndex < ordered.length; leftIndex += 1) {
      for (
        let rightIndex = leftIndex + 1;
        rightIndex < ordered.length;
        rightIndex += 1
      ) {
        const left = ordered[leftIndex];
        const right = ordered[rightIndex];
        const leftPoint = points.get(left.id)!;
        const rightPoint = points.get(right.id)!;
        let dx = leftPoint.x - rightPoint.x;
        let dy = leftPoint.y - rightPoint.y;
        if (Math.abs(dx) + Math.abs(dy) < 0.01) {
          const nudge = (hashString(`${left.id}:${right.id}`) % 31) - 15;
          dx = nudge || 1;
          dy = 16 - Math.abs(nudge);
        }
        const distanceSquared = Math.max(64, dx * dx + dy * dy);
        const distance = Math.sqrt(distanceSquared);
        const strength = Math.min(9, 10400 / distanceSquared);
        const pushX = (dx / distance) * strength;
        const pushY = (dy / distance) * strength;
        force.get(left.id)!.x += pushX;
        force.get(left.id)!.y += pushY;
        force.get(right.id)!.x -= pushX;
        force.get(right.id)!.y -= pushY;
      }
    }

    for (const edge of validEdges) {
      const source = points.get(edge.source)!;
      const target = points.get(edge.target)!;
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const desired = edge.predicate === "PARENT" ? 128 : 172;
      const strength = (distance - desired) * 0.014;
      const pullX = (dx / distance) * strength;
      const pullY = (dy / distance) * strength;
      force.get(edge.source)!.x += pullX;
      force.get(edge.source)!.y += pullY;
      force.get(edge.target)!.x -= pullX;
      force.get(edge.target)!.y -= pullY;
    }

    for (const node of ordered) {
      const point = points.get(node.id)!;
      const target = targets.get(node.id)!;
      const nodeForce = force.get(node.id)!;
      const anchorStrength = node.type === "Repository" ? 0.2 : 0.012;
      nodeForce.x += (target.x - point.x) * anchorStrength;
      nodeForce.y += (target.y - point.y) * anchorStrength;
      nodeForce.x += (center.x - point.x) * 0.0015;
      nodeForce.y += (center.y - point.y) * 0.0015;
      const velocity = velocities.get(node.id)!;
      velocity.x = (velocity.x + nodeForce.x) * 0.72;
      velocity.y = (velocity.y + nodeForce.y) * 0.72;
      point.x += clamp(velocity.x, -12, 12) * temperature;
      point.y += clamp(velocity.y, -12, 12) * temperature;
    }
  }

  // The network uses circular marks with a compact two-line label footprint.
  // Collision removal includes that label so nodes remain readable without
  // borrowing the much larger layered-card geometry.
  for (let pass = 0; pass < 90; pass += 1) {
    let moved = false;
    for (let leftIndex = 0; leftIndex < ordered.length; leftIndex += 1) {
      for (
        let rightIndex = leftIndex + 1;
        rightIndex < ordered.length;
        rightIndex += 1
      ) {
        const left = points.get(ordered[leftIndex].id)!;
        const right = points.get(ordered[rightIndex].id)!;
        const dx = right.x - left.x;
        const dy = right.y - left.y;
        const overlapX = NETWORK_NODE_FOOTPRINT_WIDTH + 10 - Math.abs(dx);
        const overlapY = NETWORK_NODE_FOOTPRINT_HEIGHT + 8 - Math.abs(dy);
        if (overlapX <= 0 || overlapY <= 0) continue;
        moved = true;
        if (overlapX < overlapY) {
          const shift = overlapX / 2 + 0.5;
          const direction = dx >= 0 ? 1 : -1;
          left.x -= shift * direction;
          right.x += shift * direction;
        } else {
          const shift = overlapY / 2 + 0.5;
          const direction = dy >= 0 ? 1 : -1;
          left.y -= shift * direction;
          right.y += shift * direction;
        }
      }
    }
    if (!moved) break;
  }

  const result: Record<string, CodeGraphPoint> = {};
  for (const node of ordered) {
    const point = points.get(node.id)!;
    result[node.id] = {
      x: clamp(
        point.x - NETWORK_NODE_FOOTPRINT_WIDTH / 2,
        24,
        width - NETWORK_NODE_FOOTPRINT_WIDTH - 24,
      ),
      y: clamp(
        point.y - NETWORK_NODE_CENTER_Y,
        24,
        height - NETWORK_NODE_FOOTPRINT_HEIGHT - 24,
      ),
    };
  }
  return result;
}

export function codeGraphLayoutPositions(
  nodes: readonly CodeRagNode[],
  edges: readonly CodeRagEdge[],
  mode: CodeGraphLayoutMode,
  width = 1280,
  height = 820,
): Record<string, CodeGraphPoint> {
  if (mode === "network")
    return layoutCodeRagNetwork(nodes, edges, width, height);
  return Object.fromEntries(
    nodes.map((node) => [node.id, { x: node.x, y: node.y }]),
  );
}

export function zoomCodeGraphView(
  view: CodeGraphView,
  anchor: CodeGraphPoint,
  nextScale: number,
): CodeGraphView {
  const scale = clamp(nextScale, 0.45, 2.4);
  const worldX = (anchor.x - view.x) / view.scale;
  const worldY = (anchor.y - view.y) / view.scale;
  return {
    x: anchor.x - worldX * scale,
    y: anchor.y - worldY * scale,
    scale,
  };
}

function derivedEdge(
  id: string,
  source: string,
  predicate: string,
  target: string,
): CodeRagEdge {
  return { id, source, predicate, target, origin: "projection" };
}

export function buildCodeRagGraph({
  repository,
  refs,
  history,
  commitDetails = [],
  snapshot,
  query = "",
  enabledTypes = new Set(CODE_GRAPH_TYPES),
  enabledRelations,
  priorityNodeIds = new Set<string>(),
}: BuildCodeRagGraphInput): CodeRagGraphModel {
  const counts = EMPTY_COUNTS();
  counts.Repository = 1;
  counts.Branch = refs?.branches.length || 0;
  counts.WorktreeSnapshot = 1;
  counts.Commit = Math.max(history.length, repository.stats.commits || 0);
  counts.FileVersion = repository.stats.files || 0;
  counts.CodeSymbol = repository.stats.symbols || 0;
  counts.DiffHunk = Number(repository.stats.diff_hunks || 0);
  counts.TestResult = Math.max(
    repository.stats.test_results || 0,
    history.reduce((total, commit) => total + (commit.test_count || 0), 0),
  );

  const rawGraphNodes = (snapshot?.nodes || []).filter((node) =>
    graphNodeBelongsToRepository(node, repository.id),
  );
  const graphCounts = EMPTY_COUNTS();
  for (const node of rawGraphNodes) {
    const type = acceptedGraphType(node.type);
    if (type) graphCounts[type] += 1;
  }
  for (const type of CODE_GRAPH_TYPES) {
    counts[type] = Math.max(counts[type], graphCounts[type]);
  }

  const candidates: Array<Omit<CodeRagNode, "x" | "y">> = [];
  appendUnique(candidates, {
    id: repository.id,
    type: "Repository",
    label: repository.name,
    locator: repository.id,
    origin: "git",
    repositoryId: repository.id,
    version: repository.head_commit,
    metadata: {
      default_branch: repository.default_branch,
      generation: repository.active_generation_id,
      status: repository.status,
    },
  });

  const stableVersion = refs?.head_sha || repository.head_commit;
  const worktreeId = `worktree-snapshot://${encodeURIComponent(repository.id)}@${encodeURIComponent(stableVersion)}`;
  appendUnique(candidates, {
    id: worktreeId,
    type: "WorktreeSnapshot",
    label: stableVersion.includes("+dirty")
      ? "当前工作树 · dirty"
      : "当前工作树",
    locator: worktreeId,
    origin: "projection",
    repositoryId: repository.id,
    version: stableVersion,
    metadata: {
      generation: repository.active_generation_id,
      head: stableVersion,
      authority: "repository + refs + graph version",
    },
  });

  for (const branch of refs?.branches || []) {
    appendUnique(candidates, {
      id: branch.id,
      type: "Branch",
      label: branch.name,
      locator: branch.id,
      origin: "git",
      repositoryId: repository.id,
      version: branch.head_sha,
      metadata: {
        is_default: branch.is_default,
        observed_at: branch.observed_at,
      },
    });
  }
  for (const commit of history) {
    appendUnique(candidates, {
      id: commit.id,
      type: "Commit",
      label: commit.message || commit.sha.slice(0, 12),
      locator: commit.id,
      origin: "git",
      repositoryId: repository.id,
      version: commit.sha,
      metadata: {
        author: commit.author_name,
        committed_at: commit.committed_at,
        diff_count: commit.diff_count,
        test_count: commit.test_count,
        parent_shas: commit.parent_shas,
      },
    });
  }

  for (const detail of commitDetails) {
    for (const hunk of detail.diff_hunks) {
      appendUnique(candidates, {
        id: hunk.id,
        type: "DiffHunk",
        label: hunk.path,
        locator: hunk.source_locator,
        origin: "git",
        repositoryId: repository.id,
        version: hunk.commit_sha,
        path: hunk.path,
        startLine: hunk.new_start,
        endLine:
          hunk.new_start && hunk.new_count
            ? hunk.new_start + Math.max(0, hunk.new_count - 1)
            : hunk.new_start,
        metadata: {
          change_type: hunk.change_type,
          old_path: hunk.old_path,
          parent_sha: hunk.parent_sha,
          affected_symbols: hunk.affected_symbols.length,
        },
      });
    }
    for (const test of detail.test_results) {
      appendUnique(candidates, {
        id: test.id,
        type: "TestResult",
        label: test.command,
        locator: test.id,
        origin: "git",
        repositoryId: repository.id,
        version: test.commit_sha,
        metadata: {
          status: test.status,
          exit_code: test.exit_code,
          duration_ms: test.duration_ms,
          framework: test.framework,
          observed_at: test.observed_at,
        },
      });
    }
  }

  const sortedGraphNodes = sortByGraphDegree(
    rawGraphNodes,
    snapshot?.edges || [],
  );
  for (const graphNode of sortedGraphNodes) {
    const converted = graphNodeToCodeNode(graphNode, repository.id);
    if (converted) appendUnique(candidates, converted);
  }

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const selected: Array<Omit<CodeRagNode, "x" | "y">> = [];
  for (const type of CODE_GRAPH_TYPES) {
    if (!enabledTypes.has(type)) continue;
    const typed = candidates.filter((node) => node.type === type);
    const matches = normalizedQuery
      ? typed.filter((node) => nodeSearchText(node).includes(normalizedQuery))
      : typed;
    const priority = matches.filter((node) => priorityNodeIds.has(node.id));
    const overview = matches
      .filter((node) => !priorityNodeIds.has(node.id))
      .slice(0, Math.max(0, TYPE_LIMITS[type] - priority.length));
    // Expanded neighbours are explicit review context and must not be silently
    // dropped by the compact overview sampling cap.
    for (const node of [...priority, ...overview]) appendUnique(selected, node);
  }

  // Repository context remains visible while searching deeper entities.
  if (normalizedQuery && enabledTypes.has("Repository")) {
    const repositoryNode = candidates.find(
      (node) => node.type === "Repository",
    );
    if (repositoryNode) appendUnique(selected, repositoryNode);
  }
  if (normalizedQuery && enabledTypes.has("WorktreeSnapshot")) {
    const worktreeNode = candidates.find(
      (node) => node.type === "WorktreeSnapshot",
    );
    if (worktreeNode) appendUnique(selected, worktreeNode);
  }

  const selectedIds = new Set(selected.map((node) => node.id));
  const edges: CodeRagEdge[] = [];
  const addEdge = (edge: CodeRagEdge) => {
    if (!selectedIds.has(edge.source) || !selectedIds.has(edge.target)) return;
    if (enabledRelations && !enabledRelations.has(edge.predicate)) return;
    if (!edges.some((candidate) => candidate.id === edge.id)) edges.push(edge);
  };

  for (const edge of snapshot?.edges || []) {
    const predicate = normalizedPredicate(edge.predicate);
    addEdge({
      id: edge.id,
      source: edge.source,
      predicate,
      target: edge.target,
      origin: "graph",
      confidence: edge.confidence,
      reviewStatus: edge.review_status,
    });
  }

  addEdge(
    derivedEdge(
      `projection:${repository.id}:worktree`,
      repository.id,
      "HAS_SNAPSHOT",
      worktreeId,
    ),
  );
  for (const branch of refs?.branches || []) {
    addEdge(
      derivedEdge(
        `projection:${repository.id}:branch:${branch.id}`,
        repository.id,
        "HAS_BRANCH",
        branch.id,
      ),
    );
    const targetCommit = history.find(
      (commit) => commit.sha === branch.head_sha,
    );
    if (targetCommit) {
      addEdge(
        derivedEdge(
          `projection:${branch.id}:points:${targetCommit.id}`,
          branch.id,
          "POINTS_TO",
          targetCommit.id,
        ),
      );
    }
  }
  for (const commit of history) {
    addEdge(
      derivedEdge(
        `projection:${repository.id}:commit:${commit.id}`,
        repository.id,
        "HAS_COMMIT",
        commit.id,
      ),
    );
    for (const parentSha of commit.parent_shas) {
      const parent = history.find((candidate) => candidate.sha === parentSha);
      if (parent) {
        addEdge(
          derivedEdge(
            `projection:${commit.id}:parent:${parent.id}`,
            commit.id,
            "PARENT",
            parent.id,
          ),
        );
      }
    }
  }
  for (const detail of commitDetails) {
    for (const hunk of detail.diff_hunks) {
      addEdge({
        id: `history:${detail.id}:hunk:${hunk.id}`,
        source: detail.id,
        predicate: "CONTAINS",
        target: hunk.id,
        origin: "git",
      });
    }
    for (const test of detail.test_results) {
      addEdge({
        id: `history:${detail.id}:test:${test.id}`,
        source: detail.id,
        predicate: "VALIDATED_BY",
        target: test.id,
        origin: "git",
      });
    }
  }
  for (const file of selected.filter((node) => node.type === "FileVersion")) {
    if (
      file.version === stableVersion ||
      stableVersion.startsWith(String(file.version))
    ) {
      addEdge(
        derivedEdge(
          `projection:${worktreeId}:contains:${file.id}`,
          worktreeId,
          "CONTAINS",
          file.id,
        ),
      );
    }
  }

  const nodes = layoutNodes(selected);
  const loadedCounts = EMPTY_COUNTS();
  for (const node of nodes) loadedCounts[node.type] += 1;
  const maxY = nodes.reduce((highest, node) => Math.max(highest, node.y), 0);
  return {
    nodes,
    edges,
    width: 1240,
    height: Math.max(620, maxY + NODE_HEIGHT + 70),
    counts,
    loadedCounts,
    sampled: Boolean(snapshot?.metadata.sampled),
  };
}

export const CODE_GRAPH_NODE_WIDTH = NODE_WIDTH;
export const CODE_GRAPH_NODE_HEIGHT = NODE_HEIGHT;
export const CODE_GRAPH_NETWORK_NODE_RADIUS = NETWORK_NODE_RADIUS;
export const CODE_GRAPH_NETWORK_NODE_CENTER_Y = NETWORK_NODE_CENTER_Y;
export const CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH = NETWORK_NODE_FOOTPRINT_WIDTH;
export const CODE_GRAPH_NETWORK_FOOTPRINT_HEIGHT =
  NETWORK_NODE_FOOTPRINT_HEIGHT;
