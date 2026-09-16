import type {
  ResearchMapEdge,
  ResearchMapNode,
} from "./researchMapModel";

export type VectorLayoutPoint = {
  id: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  degree: number;
  cluster: string;
  pinned: boolean;
};

export type VectorLayoutLink = {
  source: string;
  target: string;
  predicate: string;
  confidence: number;
  strength: number;
  distance: number;
};

function stableHash(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function nodeCluster(node: ResearchMapNode): string {
  if (node.type === "SemanticQuery") return "query";
  if (node.repositoryId) {
    const directory = node.path?.split("/").slice(0, 2).join("/") || "root";
    return `${node.repositoryId}:${directory}`;
  }
  return `${node.domain}:${node.type}`;
}

function linkPhysics(edge: ResearchMapEdge): Pick<
  VectorLayoutLink,
  "confidence" | "strength" | "distance"
> {
  const confidence = Math.max(0.05, Math.min(1, Number(edge.confidence ?? 1)));
  if (edge.predicate === "SEMANTIC_MATCH") {
    return {
      confidence,
      strength: 0.032 + confidence * 0.038,
      distance: 110 + (1 - confidence) * 145,
    };
  }
  if (edge.predicate === "SEMANTIC_SIMILAR") {
    return {
      confidence,
      strength: 0.018 + confidence * 0.032,
      distance: 145 + (1 - confidence) * 155,
    };
  }
  if (["CALLS", "REFERENCES", "IMPORTS", "DEFINES"].includes(edge.predicate)) {
    return { confidence, strength: 0.072, distance: 142 };
  }
  return { confidence, strength: 0.046, distance: 176 };
}

export function buildVectorLayout(
  nodes: ResearchMapNode[],
  edges: ResearchMapEdge[],
  focusId?: string | null,
): {
  points: Map<string, VectorLayoutPoint>;
  links: VectorLayoutLink[];
} {
  const degree = new Map(nodes.map((node) => [node.id, 0]));
  edges.forEach((edge) => {
    degree.set(edge.source, (degree.get(edge.source) || 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) || 0) + 1);
  });
  const clusters = [...new Set(nodes.map(nodeCluster))].sort();
  const clusterIndex = new Map(clusters.map((cluster, index) => [cluster, index]));
  const points = new Map<string, VectorLayoutPoint>();
  nodes.forEach((node, index) => {
    const cluster = nodeCluster(node);
    const clusterPosition = clusterIndex.get(cluster) || 0;
    const clusterAngle = clusters.length
      ? (clusterPosition / clusters.length) * Math.PI * 2
      : 0;
    const jitter = (stableHash(node.id) % 10_000) / 10_000;
    const angle = clusterAngle + (jitter - 0.5) * 0.82;
    const ring = 155 + (index % 7) * 24 + Math.floor(index / 7) * 8;
    const nodeDegree = degree.get(node.id) || 0;
    const isFocus = node.id === focusId || node.type === "SemanticQuery";
    points.set(node.id, {
      id: node.id,
      x: isFocus ? 0 : Math.cos(angle) * ring,
      y: isFocus ? 0 : Math.sin(angle) * ring * 0.72,
      vx: 0,
      vy: 0,
      radius: isFocus
        ? 23
        : Math.min(19, 9 + Math.sqrt(Math.max(1, nodeDegree)) * 2.2),
      degree: nodeDegree,
      cluster,
      pinned: false,
    });
  });
  return {
    points,
    links: edges
      .filter((edge) => points.has(edge.source) && points.has(edge.target))
      .map((edge) => ({
        source: edge.source,
        target: edge.target,
        predicate: edge.predicate,
        ...linkPhysics(edge),
      })),
  };
}

export function simulateVectorLayoutStep(
  points: Map<string, VectorLayoutPoint>,
  links: VectorLayoutLink[],
  alpha: number,
): void {
  const values = [...points.values()];
  const clusterAnchors = new Map<string, { x: number; y: number }>();
  const clusters = [...new Set(values.map((point) => point.cluster))].sort();
  clusters.forEach((cluster, index) => {
    const angle = clusters.length ? (index / clusters.length) * Math.PI * 2 : 0;
    clusterAnchors.set(cluster, {
      x: Math.cos(angle) * Math.min(240, clusters.length * 34),
      y: Math.sin(angle) * Math.min(170, clusters.length * 24),
    });
  });

  const densePairwise = values.length <= 220;
  const neighborSamples = densePairwise ? values.length : Math.min(36, values.length - 1);
  for (let leftIndex = 0; leftIndex < values.length; leftIndex += 1) {
    const left = values[leftIndex];
    for (let offset = 1; offset <= neighborSamples; offset += 1) {
      const rightIndex = densePairwise
        ? leftIndex + offset
        : (leftIndex + offset * 17) % values.length;
      if (rightIndex >= values.length || rightIndex === leftIndex) continue;
      if (!densePairwise && rightIndex < leftIndex) continue;
      const right = values[rightIndex];
      let dx = right.x - left.x;
      let dy = right.y - left.y;
      if (dx === 0 && dy === 0) {
        dx = ((stableHash(`${left.id}:${right.id}`) % 17) - 8) / 10;
        dy = 1;
      }
      const distanceSquared = Math.max(36, dx * dx + dy * dy);
      const distance = Math.sqrt(distanceSquared);
      const collision = left.radius + right.radius + 18;
      const repulsion = (3_800 * alpha) / distanceSquared;
      const overlapPush = distance < collision
        ? ((collision - distance) / collision) * 0.34 * alpha
        : 0;
      const force = repulsion + overlapPush;
      const fx = (dx / distance) * force;
      const fy = (dy / distance) * force;
      if (!left.pinned) {
        left.vx -= fx;
        left.vy -= fy;
      }
      if (!right.pinned) {
        right.vx += fx;
        right.vy += fy;
      }
    }
  }

  links.forEach((link) => {
    const source = points.get(link.source);
    const target = points.get(link.target);
    if (!source || !target) return;
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(1, Math.hypot(dx, dy));
    const force = (distance - link.distance) * link.strength * alpha;
    const fx = (dx / distance) * force;
    const fy = (dy / distance) * force;
    if (!source.pinned) {
      source.vx += fx;
      source.vy += fy;
    }
    if (!target.pinned) {
      target.vx -= fx;
      target.vy -= fy;
    }
  });

  values.forEach((point) => {
    if (point.pinned) return;
    const anchor = clusterAnchors.get(point.cluster) || { x: 0, y: 0 };
    point.vx += (anchor.x - point.x) * 0.0018 * alpha;
    point.vy += (anchor.y - point.y) * 0.0018 * alpha;
    point.vx += -point.x * 0.0009 * alpha;
    point.vy += -point.y * 0.0009 * alpha;
    point.vx *= 0.82;
    point.vy *= 0.82;
    point.x += point.vx;
    point.y += point.vy;
  });
}

export function vectorLayoutBounds(
  points: Map<string, VectorLayoutPoint>,
): { minX: number; minY: number; maxX: number; maxY: number } {
  const values = [...points.values()];
  if (!values.length) return { minX: -1, minY: -1, maxX: 1, maxY: 1 };
  return {
    minX: Math.min(...values.map((point) => point.x - point.radius)),
    minY: Math.min(...values.map((point) => point.y - point.radius)),
    maxX: Math.max(...values.map((point) => point.x + point.radius)),
    maxY: Math.max(...values.map((point) => point.y + point.radius)),
  };
}
