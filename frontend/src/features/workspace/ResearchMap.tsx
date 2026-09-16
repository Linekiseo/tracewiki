import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Beaker,
  Boxes,
  Braces,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Code2,
  Component,
  FileCode2,
  FileText,
  FolderTree,
  GitCommitHorizontal,
  GitFork,
  Layers3,
  LocateFixed,
  Maximize2,
  MessageSquareText,
  Minus,
  Network,
  Orbit,
  Package,
  Plus,
  Search,
  ShieldCheck,
  SquareFunction,
  Route,
  Workflow,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Button, Status } from "../../components/ui";
import { api } from "../../lib/api";
import type { GraphEdge, GraphNode } from "../../lib/types";
import { CodeVectorGraph, type CodeVectorGraphHandle } from "./CodeVectorGraph";
import {
  buildResearchGraph,
  buildCodexBoard,
  buildCodexOverviewBoard,
  buildCodeBoard,
  buildCodeCollaborationBoard,
  buildCodeRelationBoard,
  buildCodeVectorBoard,
  buildEvidenceReviewProfile,
  buildInspectorContextMap,
  buildResearchOverview,
  buildTopicBoard,
  codeNodeBehaviorFacts,
  connectedEdges,
  describeCodeNode,
  domainLabel,
  entityTypeLabel,
  graphBounds,
  statusLabel,
  type CodeCollaborationStats,
  type EvidenceReviewProfile,
  type ResearchDomain,
  type ResearchMapEdge,
  type ResearchMapNode,
} from "./researchMapModel";
import "./research-map.css";

const DEFAULT_LOAD_TARGET = 48;
const RELATION_LOAD_TARGET = 24;
const SESSION_OVERVIEW_PAGE_SIZE = 12;
const NODE_WIDTH = 232;
const NODE_HEIGHT = 92;
type ResearchBoard = "overview" | "topic" | "codex" | "code";
type CodeRelationDirection = "both" | "incoming" | "outgoing";
type CodeGraphView = "branch" | "vector";

const codeRelationOptions = [
  { value: "CALLS", label: "调用" },
  { value: "REFERENCES", label: "引用" },
  { value: "IMPORTS", label: "导入" },
  { value: "DEFINES", label: "定义" },
  { value: "SEMANTIC_SIMILAR", label: "向量相似" },
] as const;

function nodeDimensions(node: ResearchMapNode): {
  width: number;
  height: number;
} {
  if (node.width || node.height) {
    return {
      width: node.width || NODE_WIDTH,
      height: node.height || NODE_HEIGHT,
    };
  }
  if (node.children?.length) return { width: 286, height: 154 };
  if (
    node.type === "ResearchProject" ||
    node.type === "CodeRepositoryBoard" ||
    node.type === "CodexWorkspaceBoard"
  ) {
    return { width: 248, height: 96 };
  }
  if (node.type === "CodeDirectoryGroup") return { width: 248, height: 86 };
  return { width: NODE_WIDTH, height: NODE_HEIGHT };
}

function viewportForNodes(
  nodes: ResearchMapNode[],
  canvas: { width: number; height: number },
  maxScale = 0.92,
  minScale = 0.12,
  compactPadding = false,
): { x: number; y: number; scale: number } {
  const bounds = graphBounds(nodes);
  const horizontalPadding = compactPadding
    ? Math.min(72, Math.max(48, canvas.width * 0.05))
    : Math.min(120, Math.max(72, canvas.width * 0.09));
  const verticalPadding = compactPadding
    ? Math.min(84, Math.max(52, canvas.height * 0.07))
    : Math.min(120, Math.max(72, canvas.height * 0.11));
  const scale = Math.max(
    minScale,
    Math.min(
      maxScale,
      (canvas.width - horizontalPadding * 2) / bounds.width,
      (canvas.height - verticalPadding * 2) / bounds.height,
    ),
  );
  const scaledHeight = bounds.height * scale;
  const contentY =
    scaledHeight <= canvas.height - verticalPadding * 2
      ? (canvas.height - scaledHeight) / 2
      : verticalPadding;
  return {
    scale,
    x: (canvas.width - bounds.width * scale) / 2 - bounds.minX * scale,
    y: contentY - bounds.minY * scale,
  };
}

function initialViewportForBoard(
  nodes: ResearchMapNode[],
  canvas: { width: number; height: number },
  context: {
    board: ResearchBoard;
    activeThreadId?: string | null;
    activeCodePath?: string | null;
    requestedCodeEntity?: string | null;
    isCodeCollaboration: boolean;
  },
) {
  const minimumReadableScale =
    context.board === "codex" && context.activeThreadId
      ? 0.72
      : context.isCodeCollaboration
        ? 0.56
        : context.board === "code" && context.activeCodePath
          ? 0.64
          : context.board === "code" && context.requestedCodeEntity
            ? 0.7
            : 0.62;
  const next = viewportForNodes(
    nodes,
    canvas,
    context.isCodeCollaboration ? 0.84 : 0.94,
    minimumReadableScale,
    context.isCodeCollaboration,
  );
  if (context.board === "codex" && context.activeThreadId) {
    const focusNode =
      nodes.find((node) => node.type === "CodexThread") ||
      nodes.find((node) => node.type === "CodexTurn");
    if (focusNode) {
      next.scale = 0.8;
      next.x = canvas.width * 0.21 - focusNode.x * next.scale;
      next.y = canvas.height * 0.4 - focusNode.y * next.scale;
    }
  }
  if (context.isCodeCollaboration && nodes.length > 14) {
    const hub = [...nodes].sort(
      (left, right) =>
        Math.hypot(left.x, left.y) - Math.hypot(right.x, right.y) ||
        left.id.localeCompare(right.id),
    )[0];
    if (hub) {
      next.scale = nodes.length > 28 ? 0.68 : 0.78;
      next.x = canvas.width * 0.5 - hub.x * next.scale;
      next.y = canvas.height * 0.44 - hub.y * next.scale;
    }
  }
  return next;
}

const domainIcons = {
  workspace: Network,
  code: Code2,
  codex: MessageSquareText,
  experiment: Beaker,
  document: FileText,
  review: ShieldCheck,
  query: Search,
} satisfies Record<ResearchDomain, typeof Network>;

const codeTypeIcons: Record<string, typeof Network> = {
  Repository: Package,
  CodeRepositoryBoard: Package,
  CodeDirectoryGroup: FolderTree,
  CodeFileGroup: FileCode2,
  CodeFileContainer: FileCode2,
  CodeFileFocus: FileCode2,
  FileVersion: FileCode2,
  CodeResponsibilityGroup: Workflow,
  CodeClass: Boxes,
  CodeInterface: Component,
  CodeModule: Package,
  CodeMethod: SquareFunction,
  CodeFunction: Braces,
  CodeSymbol: Code2,
  CodeNestedSymbol: SquareFunction,
};

function iconForNode(
  node: Pick<ResearchMapNode, "domain" | "type">,
): typeof Network {
  return node.domain === "code"
    ? codeTypeIcons[node.type] || Code2
    : domainIcons[node.domain];
}

function iconForType(type: string): typeof Network {
  return codeTypeIcons[type] || Code2;
}

function codeLayerLabel(type: string): string {
  if (["Repository", "CodeRepositoryBoard", "Commit"].includes(type))
    return "版本层";
  if (type === "CodeDirectoryGroup") return "模块层";
  if (
    [
      "CodeFileGroup",
      "CodeFileContainer",
      "CodeFileFocus",
      "FileVersion",
    ].includes(type)
  )
    return "文件层";
  if (type === "CodeResponsibilityGroup") return "职责层";
  if (["CodeClass", "CodeInterface", "CodeModule"].includes(type))
    return "类型层";
  if (
    ["CodeMethod", "CodeFunction", "CodeSymbol", "CodeNestedSymbol"].includes(
      type,
    )
  )
    return "实现层";
  return "代码层";
}

function codeNodeUserValue(node: ResearchMapNode): string {
  if (node.type === "CodeDirectoryGroup") {
    return "先判断这一块代码解决什么问题、与哪些模块协作，再进入具体文件。";
  }
  if (
    [
      "CodeFileGroup",
      "CodeFileContainer",
      "CodeFileFocus",
      "FileVersion",
    ].includes(node.type)
  ) {
    return "快速看懂文件职责与内部能力，避免从整段源代码开始摸索。";
  }
  if (node.type === "CodeResponsibilityGroup") {
    return "把完成同一工作目标的入口放在一起，适合定位功能边界与修改范围。";
  }
  if (["CodeClass", "CodeInterface", "CodeModule"].includes(node.type)) {
    return "理解这个类型维护的状态、提供的能力，以及它在文件中的位置。";
  }
  if (["CodeMethod", "CodeFunction", "CodeSymbol"].includes(node.type)) {
    return "确认谁在调用它、它又依赖什么，从而判断改动影响和验证范围。";
  }
  return "理解该节点在当前研究项目与代码关系中的作用。";
}

function codeNodeFootLabel(node: ResearchMapNode): string {
  if (node.type === "CodeDirectoryGroup") {
    return node.meta.split(" · ")[0] || "代码模块";
  }
  if (node.type === "CodeResponsibilityGroup") {
    return `${node.children?.length || 0} 个入口`;
  }
  if (node.startLine) {
    return `L${node.startLine}${node.endLine ? `–${node.endLine}` : ""}`;
  }
  const path = node.path || "";
  if (path) {
    const parts = path.split("/").filter(Boolean);
    return parts.slice(-2).join("/");
  }
  return entityTypeLabel(node.type);
}

function childSectionTitle(node: ResearchMapNode): string {
  if (node.type === "CodeDirectoryGroup") return "该模块包含的文件";
  if (
    [
      "CodeFileGroup",
      "CodeFileContainer",
      "CodeFileFocus",
      "FileVersion",
    ].includes(node.type)
  )
    return "按职责组织的内部能力";
  if (node.type === "CodeResponsibilityGroup") return "完成该职责的代码入口";
  if (["CodeClass", "CodeInterface"].includes(node.type))
    return "该类型提供的方法";
  return "可继续检查的下级节点";
}

const sourceOrder: ResearchDomain[] = [
  "workspace",
  "code",
  "codex",
  "experiment",
  "document",
  "review",
];

function shortVersion(value?: string | null): string {
  return value ? value.slice(0, 10) : "";
}

function edgePath(
  source: ResearchMapNode,
  target: ResearchMapNode,
  _edge: ResearchMapEdge,
): string {
  const dx = target.x - source.x;
  const dy = target.y - source.y;
  const sourceSize = nodeDimensions(source);
  const targetSize = nodeDimensions(target);
  const radius = 14;
  if (Math.abs(dx) >= Math.abs(dy)) {
    const direction = dx >= 0 ? 1 : -1;
    const startX = source.x + (direction * sourceSize.width) / 2;
    const endX = target.x - (direction * targetSize.width) / 2;
    const middleX = (startX + endX) / 2;
    const verticalDirection = target.y >= source.y ? 1 : -1;
    const bend = Math.min(
      radius,
      Math.abs(middleX - startX),
      Math.abs(target.y - source.y) / 2,
    );
    if (Math.abs(target.y - source.y) < 2)
      return `M ${startX} ${source.y} H ${endX}`;
    return [
      `M ${startX} ${source.y}`,
      `H ${middleX - direction * bend}`,
      `Q ${middleX} ${source.y} ${middleX} ${source.y + verticalDirection * bend}`,
      `V ${target.y - verticalDirection * bend}`,
      `Q ${middleX} ${target.y} ${middleX + direction * bend} ${target.y}`,
      `H ${endX}`,
    ].join(" ");
  }
  const direction = dy >= 0 ? 1 : -1;
  const startY = source.y + (direction * sourceSize.height) / 2;
  const endY = target.y - (direction * targetSize.height) / 2;
  const middleY = (startY + endY) / 2;
  const horizontalDirection = target.x >= source.x ? 1 : -1;
  const bend = Math.min(
    radius,
    Math.abs(middleY - startY),
    Math.abs(target.x - source.x) / 2,
  );
  if (Math.abs(target.x - source.x) < 2)
    return `M ${source.x} ${startY} V ${endY}`;
  return [
    `M ${source.x} ${startY}`,
    `V ${middleY - direction * bend}`,
    `Q ${source.x} ${middleY} ${source.x + horizontalDirection * bend} ${middleY}`,
    `H ${target.x - horizontalDirection * bend}`,
    `Q ${target.x} ${middleY} ${target.x} ${middleY + direction * bend}`,
    `V ${endY}`,
  ].join(" ");
}

function sourceActionLabel(node: ResearchMapNode): string {
  if (node.domain === "code") return "查看代码";
  if (node.domain === "codex") return "查看会话";
  if (node.domain === "experiment") return "查看实验";
  if (node.domain === "document") return "查看文档";
  if (node.domain === "review") return "进入复核";
  return "查看项目";
}

function routeForNode(node: ResearchMapNode, projectId: string): string {
  const base = `/p/${encodeURIComponent(projectId)}`;
  if (node.domain === "code") {
    const path = node.path || (node.type === "CodeFileGroup" ? node.meta : "");
    const params = new URLSearchParams();
    if (path) params.set("file", path);
    if (node.repositoryId) params.set("repository", node.repositoryId);
    const query = params.toString();
    return `${base}/code${query ? `?${query}` : ""}`;
  }
  if (node.domain === "codex") {
    const threadId = node.id.match(/^codex:\/\/thread\/([^/]+)/)?.[1];
    return threadId
      ? `${base}/sessions/${encodeURIComponent(threadId)}`
      : `${base}/sessions`;
  }
  if (node.domain === "experiment") return `${base}/experiments`;
  if (node.domain === "document") return `${base}/documents`;
  if (node.domain === "review") return `${base}/relations`;
  return `${base}/overview`;
}

function NodeContextBoard({
  focus,
  nodes,
  edges,
  compact = false,
  busy = false,
  hasMore,
  onExpand,
  onSelect,
  onNavigate,
}: {
  focus: ResearchMapNode;
  nodes: ResearchMapNode[];
  edges: ResearchMapEdge[];
  compact?: boolean;
  busy?: boolean;
  hasMore?: boolean;
  onExpand?: () => void;
  onSelect: (node: ResearchMapNode) => void;
  onNavigate?: (node: ResearchMapNode) => void;
}) {
  const context = useMemo(
    () =>
      buildInspectorContextMap(
        focus,
        nodes,
        edges,
        compact ? 3 : 24,
        compact ? 3 : 24,
      ),
    [compact, edges, focus, nodes],
  );
  const viewportRef = useRef<HTMLDivElement>(null);
  const actualById = new Map(nodes.map((node) => [node.id, node]));
  const visibleItems = [
    ...context.trail,
    ...context.children,
    ...context.relations,
  ];
  const navigable = visibleItems.filter(
    (item) =>
      item.node.id !== focus.id &&
      (actualById.has(item.node.id) || Boolean(onNavigate)),
  );
  const moveViewport = (direction: -1 | 1) => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollBy({
      left: direction * Math.max(160, viewport.clientWidth * 0.64),
      behavior: "smooth",
    });
  };
  const selectItem = (candidate: ResearchMapNode) => {
    const actual = actualById.get(candidate.id);
    if (actual) onSelect(actual);
    else onNavigate?.(candidate);
  };
  const selectAdjacent = (direction: -1 | 1) => {
    if (!navigable.length) return;
    const currentIndex = Math.max(
      0,
      visibleItems.findIndex((item) => item.node.id === focus.id),
    );
    for (let offset = 1; offset <= visibleItems.length; offset += 1) {
      const next =
        visibleItems[
          (currentIndex + direction * offset + visibleItems.length) %
            visibleItems.length
        ];
      if (!navigable.includes(next)) continue;
      selectItem(next.node);
      return;
    }
  };

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const current = viewport.querySelector<HTMLElement>(
      '[aria-current="true"]',
    );
    current?.scrollIntoView?.({ inline: "center", block: "center" });
  }, [focus.id]);

  const renderItem = (
    item: (typeof visibleItems)[number],
    relationLabel?: string,
  ) => {
    const ItemIcon = iconForNode(item.node);
    const selectable =
      item.node.id !== focus.id &&
      (actualById.has(item.node.id) || Boolean(onNavigate));
    return (
      <button
        key={`${item.role}:${item.node.id}`}
        className={`map-context-item is-${item.role} domain-${item.node.domain}`}
        onClick={() => selectable && selectItem(item.node)}
        disabled={!selectable}
        aria-current={item.role === "focus" ? "true" : undefined}
        title={`${entityTypeLabel(item.node.type)} · ${item.node.label}`}
      >
        <span>
          <ItemIcon size={15} />
        </span>
        <span>
          <small>
            {relationLabel ||
              `${item.node.domain === "code" ? codeLayerLabel(item.node.type) : `L${item.level}`} · ${entityTypeLabel(item.node.type)}`}
          </small>
          <strong>{item.node.label}</strong>
        </span>
        {selectable ? <ChevronRight size={14} /> : null}
      </button>
    );
  };

  const relationEdgeByNodeId = new Map(
    context.relations.flatMap((item) => {
      const edge = context.edges.find(
        (candidate) =>
          candidate.role === "relation" &&
          (candidate.source === item.node.id ||
            candidate.target === item.node.id),
      );
      return edge ? [[item.node.id, edge] as const] : [];
    }),
  );
  const ancestors = context.trail.filter((item) => item.role === "ancestor");
  const focusItem =
    context.trail.find((item) => item.role === "focus") || context.trail.at(-1);
  const incomingRelations = context.relations.filter(
    (item) => relationEdgeByNodeId.get(item.node.id)?.target === focus.id,
  );
  const outgoingRelations = context.relations.filter(
    (item) => relationEdgeByNodeId.get(item.node.id)?.target !== focus.id,
  );
  const leftItems = [...ancestors, ...incomingRelations];
  const rightItems = [...context.children, ...outgoingRelations];
  const boardHeight = Math.max(
    560,
    Math.max(leftItems.length, rightItems.length, 1) * 78 + 128,
  );
  const distribute = (index: number, total: number) =>
    total <= 1 ? 50 : 11 + (index * 78) / (total - 1);
  const positionedItems = [
    ...leftItems.map((item, index) => ({
      item,
      x: 17,
      y: distribute(index, leftItems.length),
    })),
    ...(focusItem ? [{ item: focusItem, x: 50, y: 50 }] : []),
    ...rightItems.map((item, index) => ({
      item,
      x: 83,
      y: distribute(index, rightItems.length),
    })),
  ];
  const positionById = new Map(
    positionedItems.map((position) => [position.item.node.id, position]),
  );
  const positionedEdges = context.edges.flatMap((edge) => {
    const source = positionById.get(edge.source);
    const target = positionById.get(edge.target);
    return source && target ? [{ edge, source, target }] : [];
  });
  const itemContextLabel = (item: (typeof visibleItems)[number]) => {
    if (item.role === "focus") {
      return `当前焦点 · ${entityTypeLabel(item.node.type)}`;
    }
    if (item.role === "ancestor") {
      return `上级 L${item.level} · ${entityTypeLabel(item.node.type)}`;
    }
    if (item.role === "child") {
      return `下级 · ${entityTypeLabel(item.node.type)}`;
    }
    const edge = relationEdgeByNodeId.get(item.node.id);
    return `${edge?.target === focus.id ? "传入" : "向外"} · ${edge?.label || "关联"}`;
  };

  return (
    <section
      className={`map-context-board ${compact ? "is-compact" : "is-network"}`}
      aria-label={
        compact
          ? `${focus.label} 局部层级白板`
          : `${focus.label} 为中心的完整层级与关联节点白板`
      }
    >
      <header className="map-context-board__header">
        <div>
          <span>
            <Layers3 size={15} />
            {compact ? "结构坐标" : "当前节点关系白板"}
          </span>
          <strong>
            第 {context.focusLevel} / {context.totalLevels} 层 ·{" "}
            {entityTypeLabel(focus.type)}
          </strong>
        </div>
        <div className="map-context-board__controls">
          <small>
            {context.trail.length - 1} 上级 · {context.childTotal} 下级 ·{" "}
            {context.relationTotal} 关系
          </small>
          {!compact && onExpand ? (
            <button
              className="map-context-board__expand"
              onClick={onExpand}
              disabled={busy || hasMore === false}
              aria-label="展开当前节点关联"
              title="从服务端继续读取当前节点的直接关联"
            >
              <Plus size={14} />
              {busy ? "读取中" : hasMore === false ? "已载完" : "展开关联"}
            </button>
          ) : null}
          <span>
            <button
              onClick={() => {
                moveViewport(-1);
                selectAdjacent(-1);
              }}
              disabled={!navigable.length}
              aria-label="上一个层级节点"
              title="上一个层级节点"
            >
              <ChevronLeft size={15} />
            </button>
            <button
              onClick={() => {
                moveViewport(1);
                selectAdjacent(1);
              }}
              disabled={!navigable.length}
              aria-label="下一个层级节点"
              title="下一个层级节点"
            >
              <ChevronRight size={15} />
            </button>
          </span>
        </div>
      </header>
      <div
        ref={viewportRef}
        className="map-context-board__viewport"
        tabIndex={0}
        aria-label={`${focus.label} 局部层级白板`}
        onKeyDown={(event) => {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
          event.preventDefault();
          const direction = event.key === "ArrowLeft" ? -1 : 1;
          moveViewport(direction);
          selectAdjacent(direction);
        }}
      >
        {compact ? (
          <div className="map-context-board__track">
            <span className="map-context-board__lane">结构路径</span>
            <div>
              {context.trail.map((item, index) => (
                <span key={item.node.id} className="map-context-board__step">
                  {renderItem(item)}
                  {index < context.trail.length - 1 ? (
                    <ArrowRight size={15} />
                  ) : null}
                </span>
              ))}
            </div>
          </div>
        ) : (
          <div
            className="map-context-board__canvas"
            style={{ minHeight: boardHeight }}
            data-testid="context-relationship-canvas"
          >
            <div className="map-context-board__canvas-label is-left">
              上级结构 / 传入
            </div>
            <div className="map-context-board__canvas-label is-center">
              当前节点
            </div>
            <div className="map-context-board__canvas-label is-right">
              下级实体 / 向外
            </div>
            <svg
              className="map-context-board__links"
              viewBox="0 0 1000 1000"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <defs>
                <marker
                  id="context-hierarchy-arrow"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" />
                </marker>
                <marker
                  id="context-relation-arrow"
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0 L 10 5 L 0 10 z" />
                </marker>
              </defs>
              {positionedEdges.map(({ edge, source, target }) => {
                const sourceX = source.x * 10;
                const sourceY = source.y * 10;
                const targetX = target.x * 10;
                const targetY = target.y * 10;
                const middleX = (sourceX + targetX) / 2;
                return (
                  <path
                    key={edge.id}
                    className={`is-${edge.role}`}
                    d={`M ${sourceX} ${sourceY} C ${middleX} ${sourceY}, ${middleX} ${targetY}, ${targetX} ${targetY}`}
                    markerEnd={`url(#context-${edge.role}-arrow)`}
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
            </svg>
            {positionedEdges.map(({ edge, source, target }) => (
              <span
                key={`label:${edge.id}`}
                className={`map-context-board__edge-label is-${edge.role}`}
                style={{
                  left: `${(source.x + target.x) / 2}%`,
                  top: `${(source.y + target.y) / 2}%`,
                }}
              >
                {edge.label}
              </span>
            ))}
            {positionedItems.map(({ item, x, y }) => (
              <div
                key={`canvas:${item.role}:${item.node.id}`}
                className={`map-context-board__node is-${item.role}`}
                data-context-role={item.role}
                style={{ left: `${x}%`, top: `${y}%` }}
              >
                {renderItem(item, itemContextLabel(item))}
              </div>
            ))}
            {positionedItems.length <= 1 ? (
              <div className="map-context-board__empty">
                <Network size={18} />
                <strong>当前载入范围只有此节点</strong>
                <span>使用“展开关联”读取它的直接上下游，白板会原位补全。</span>
              </div>
            ) : null}
          </div>
        )}
      </div>
      {compact && (context.children.length || context.relations.length) ? (
        <div className="map-context-board__branches">
          {context.children.length ? (
            <section>
              <header>
                <strong>内部实体</strong>
                <span>{context.childTotal}</span>
              </header>
              <div>
                {context.children.map((item) =>
                  renderItem(item, `包含 · ${entityTypeLabel(item.node.type)}`),
                )}
              </div>
            </section>
          ) : null}
          {context.relations.length ? (
            <section>
              <header>
                <strong>直接互联</strong>
                <span>{context.relationTotal}</span>
              </header>
              <div>
                {context.relations.map((item) => {
                  const edge = context.edges.find(
                    (candidate) =>
                      candidate.role === "relation" &&
                      (candidate.source === item.node.id ||
                        candidate.target === item.node.id),
                  );
                  const direction = edge?.source === focus.id ? "向外" : "传入";
                  return renderItem(
                    item,
                    `${direction} · ${edge?.label || "关联"}`,
                  );
                })}
              </div>
            </section>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function ReviewRouteRail({
  node,
  profile,
  navigationTrail,
  canGoBack,
  onBack,
  onSelect,
  onTrailSelect,
}: {
  node: ResearchMapNode;
  profile: EvidenceReviewProfile;
  navigationTrail: ResearchMapNode[];
  canGoBack: boolean;
  onBack: () => void;
  onSelect: (node: ResearchMapNode) => void;
  onTrailSelect: (node: ResearchMapNode) => void;
}) {
  const [filter, setFilter] = useState<
    "all" | "incoming" | "outgoing" | "review"
  >("all");
  const relations = [...profile.incoming, ...profile.outgoing];
  const visibleRelations = relations.filter(({ direction, edge }) => {
    if (filter === "all") return true;
    if (filter === "review") {
      return (
        edge.review_status === "unreviewed" && Boolean(edge.reviewCandidateId)
      );
    }
    return direction === filter;
  });
  const filters = [
    { value: "all" as const, label: "全部", count: relations.length },
    {
      value: "incoming" as const,
      label: "传入",
      count: profile.incoming.length,
    },
    {
      value: "outgoing" as const,
      label: "发出",
      count: profile.outgoing.length,
    },
    {
      value: "review" as const,
      label: "可复核",
      count: relations.filter(
        ({ edge }) =>
          edge.review_status === "unreviewed" &&
          Boolean(edge.reviewCandidateId),
      ).length,
    },
  ];

  useEffect(() => setFilter("all"), [node.id]);

  return (
    <aside className="research-review-rail" aria-label="审阅路径与关系过滤">
      <header className="research-review-rail__focus">
        <span>当前焦点</span>
        <strong>{node.label}</strong>
        <small>
          {entityTypeLabel(node.type)} · {profile.source.value}
        </small>
      </header>

      <nav className="research-review-rail__trail" aria-label="图谱审阅路径">
        <div>
          <span>
            <Route size={14} />
            审阅路径
          </span>
          <button type="button" onClick={onBack} disabled={!canGoBack}>
            <ChevronLeft size={14} />
            返回上一焦点
          </button>
        </div>
        <ol>
          {navigationTrail.map((item, index) => (
            <li key={item.id}>
              <button
                type="button"
                aria-current={item.id === node.id ? "page" : undefined}
                onClick={() => item.id !== node.id && onTrailSelect(item)}
              >
                <small>{String(index + 1).padStart(2, "0")}</small>
                <span>{item.label}</span>
              </button>
            </li>
          ))}
        </ol>
      </nav>

      <section
        className="research-review-rail__relations"
        aria-labelledby="review-route-relations"
      >
        <header>
          <span id="review-route-relations">相邻关系</span>
          <small>{relations.length} 条已载入</small>
        </header>
        <div
          className="research-review-rail__filters"
          role="group"
          aria-label="路径关系过滤"
        >
          {filters.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={filter === option.value}
              onClick={() => setFilter(option.value)}
              disabled={option.value !== "all" && option.count === 0}
            >
              {option.label}
              <b>{option.count}</b>
            </button>
          ))}
        </div>
        <div className="research-review-rail__relation-list">
          {visibleRelations.length ? (
            visibleRelations
              .slice(0, 10)
              .map(({ edge, source, target, other, direction }) => (
                <button
                  key={edge.id}
                  type="button"
                  onClick={() => onSelect(other)}
                  aria-label={`${source.label} 到 ${target.label}，${edge.label}`}
                >
                  <span className="research-review-rail__route">
                    <b>{source.label}</b>
                    <ArrowRight size={13} />
                    <b>{target.label}</b>
                  </span>
                  <small>
                    {direction === "incoming" ? "传入" : "发出"} · {edge.label}{" "}
                    · {statusLabel(edge.review_status || "confirmed")}
                  </small>
                </button>
              ))
          ) : (
            <p>
              {relations.length
                ? "当前过滤条件下没有可核验关系。"
                : "当前载入范围没有可核验相邻关系。"}
            </p>
          )}
        </div>
      </section>

      <footer className={profile.reviewCandidateId ? "is-reviewable" : ""}>
        <ShieldCheck size={15} />
        <span>
          <strong>
            {profile.reviewCandidateId
              ? "存在可安全打开的候选"
              : "没有可安全打开的候选"}
          </strong>
          <small>
            {profile.reviewCandidateId
              ? "详情面板提供复核入口"
              : "不会把普通图谱边当成复核记录"}
          </small>
        </span>
      </footer>
    </aside>
  );
}

function MapInspector({
  node,
  edges,
  nodesById,
  projectId,
  projectAclRef,
  busy,
  neighborTotal,
  hasMore,
  error,
  drillLabel,
  onClose,
  onSelect,
  onExpand,
  onDrill,
  onToggleFile,
  onOpenChild,
  navigationTrail,
  onTrailSelect,
  canGoBack,
  onBack,
}: {
  node: ResearchMapNode;
  edges: ResearchMapEdge[];
  nodesById: Map<string, ResearchMapNode>;
  projectId: string;
  projectAclRef?: string | null;
  busy: boolean;
  neighborTotal?: number | null;
  hasMore?: boolean;
  error?: string;
  drillLabel?: string | null;
  onClose: () => void;
  onSelect: (node: ResearchMapNode) => void;
  onExpand: () => void;
  onDrill?: () => void;
  onToggleFile?: () => void;
  onOpenChild: (
    child: NonNullable<ResearchMapNode["children"]>[number],
  ) => void;
  navigationTrail: ResearchMapNode[];
  onTrailSelect: (node: ResearchMapNode) => void;
  canGoBack: boolean;
  onBack: () => void;
}) {
  const [tab, setTab] = useState<
    "overview" | "hierarchy" | "relations" | "locator"
  >("overview");
  const [relationFilter, setRelationFilter] = useState<
    "all" | "calls" | "references" | "structure" | "semantic"
  >("all");
  const inspectorBodyRef = useRef<HTMLDivElement | null>(null);
  const Icon = iconForNode(node);
  const relations = connectedEdges(node.id, edges);
  const inspectorNodes = useMemo(() => [...nodesById.values()], [nodesById]);
  const context = useMemo(
    () => buildInspectorContextMap(node, inspectorNodes, edges),
    [edges, inspectorNodes, node],
  );
  const reviewProfile = useMemo(
    () =>
      buildEvidenceReviewProfile({
        node,
        edges,
        nodesById,
        projectId,
        projectAclRef,
      }),
    [edges, node, nodesById, projectAclRef, projectId],
  );
  const outgoing = relations.filter((edge) => edge.source === node.id);
  const incoming = relations.filter((edge) => edge.target === node.id);
  const purpose = node.domain === "code" ? describeCodeNode(node) : node.meta;
  const behaviorFacts =
    node.domain === "code" ? codeNodeBehaviorFacts(node) : [];
  const incomingFunctional = incoming.filter(
    (edge) =>
      !edge.structural &&
      !["DEFINES", "CONTAINS", "belongs_to_class"].includes(edge.predicate),
  );
  const outgoingFunctional = outgoing.filter(
    (edge) =>
      !edge.structural &&
      !["DEFINES", "CONTAINS", "belongs_to_class"].includes(edge.predicate),
  );
  const userValue =
    node.domain === "code"
      ? codeNodeUserValue(node)
      : "快速理解该节点在当前研究流程中的位置、关联来源与可继续执行的动作。";
  const layerLabel =
    node.domain === "code"
      ? codeLayerLabel(node.type)
      : domainLabel(node.domain);
  const relationCategory = (edge: ResearchMapEdge) => {
    if (
      edge.structural ||
      ["belongs_to_class", "DEFINES", "CONTAINS"].includes(edge.predicate)
    ) {
      return "structure";
    }
    if (edge.predicate === "CALLS") return "calls";
    if (["REFERENCES", "IMPORTS"].includes(edge.predicate)) return "references";
    if (["SEMANTIC_SIMILAR", "SEMANTIC_MATCH"].includes(edge.predicate))
      return "semantic";
    return "references";
  };
  const visibleRelations =
    relationFilter === "all"
      ? relations
      : relations.filter((edge) => relationCategory(edge) === relationFilter);
  const relationFilterOptions = [
    { value: "all" as const, label: "全部", count: relations.length },
    {
      value: "calls" as const,
      label: "调用",
      count: relations.filter((edge) => relationCategory(edge) === "calls")
        .length,
    },
    {
      value: "references" as const,
      label: "引用",
      count: relations.filter((edge) => relationCategory(edge) === "references")
        .length,
    },
    {
      value: "structure" as const,
      label: "结构",
      count: relations.filter((edge) => relationCategory(edge) === "structure")
        .length,
    },
    {
      value: "semantic" as const,
      label: "语义",
      count: relations.filter((edge) => relationCategory(edge) === "semantic")
        .length,
    },
  ].filter((option) => option.value === "all" || option.count);
  useEffect(() => {
    setTab("overview");
    setRelationFilter("all");
  }, [node.id]);
  useEffect(() => {
    inspectorBodyRef.current?.scrollTo({ top: 0 });
  }, [node.id, tab]);
  const navigateContextNode = (candidate: ResearchMapNode) => {
    const actual = nodesById.get(candidate.id);
    if (actual) {
      onSelect(actual);
      return;
    }
    const child = node.children?.find((item) => item.id === candidate.id);
    if (child) {
      onOpenChild(child);
      return;
    }
    if (candidate.type === "Repository") {
      onOpenChild({
        id: candidate.id,
        label: candidate.label,
        type: candidate.type,
        meta: candidate.meta,
      });
    }
  };
  return (
    <aside
      className={`map-inspector domain-${node.domain} type-${node.type.toLowerCase()}`}
      aria-label={`${node.label} 详情`}
    >
      <header className="map-inspector__header">
        <span className="map-inspector__icon">
          <Icon size={20} />
        </span>
        <div>
          <small>
            {layerLabel} · {entityTypeLabel(node.type)}
          </small>
          <h2>{node.label}</h2>
          <p>{node.path || node.qualifiedName || node.meta}</p>
          <nav className="map-inspector__trail" aria-label="移动端图谱审阅路径">
            <span>
              <Route size={12} />
              审阅路径
            </span>
            <div>
              <button type="button" onClick={onBack} disabled={!canGoBack}>
                <ChevronLeft size={12} />
                返回
              </button>
              {navigationTrail.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  aria-current={item.id === node.id ? "page" : undefined}
                  onClick={() => item.id !== node.id && onTrailSelect(item)}
                  title={`${entityTypeLabel(item.type)} · ${item.label}`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </nav>
        </div>
        <button onClick={onClose} aria-label="关闭节点详情">
          <X size={19} />
        </button>
      </header>
      <nav className="map-inspector__tabs" aria-label="节点详情栏目">
        <button
          className={tab === "overview" ? "is-active" : ""}
          onClick={() => setTab("overview")}
        >
          概览
        </button>
        <button
          className={tab === "hierarchy" ? "is-active" : ""}
          onClick={() => setTab("hierarchy")}
        >
          层级
        </button>
        <button
          className={tab === "relations" ? "is-active" : ""}
          onClick={() => setTab("relations")}
        >
          关系 {relations.length}
        </button>
        <button
          className={tab === "locator" ? "is-active" : ""}
          onClick={() => setTab("locator")}
        >
          定位
        </button>
      </nav>
      <div
        ref={inspectorBodyRef}
        className={`map-inspector__body ${tab === "hierarchy" ? "is-hierarchy" : ""}`}
      >
        {tab === "overview" ? (
          <>
            <section
              className={`map-node-insight type-${node.type.toLowerCase()}`}
            >
              <header>
                <span>
                  {node.domain === "code" ? "这个节点负责什么" : "当前节点说明"}
                </span>
                {node.version ? (
                  <code>{shortVersion(node.version)}</code>
                ) : null}
              </header>
              <h3>{purpose}</h3>
              <p>{node.path || node.qualifiedName || node.meta}</p>
              <div className="map-node-insight__meaning">
                <article>
                  <small>为什么要看</small>
                  <p>{userValue}</p>
                </article>
                <article>
                  <small>关系影响</small>
                  <strong>
                    {incomingFunctional.length} 个上游 ·{" "}
                    {outgoingFunctional.length} 个下游
                  </strong>
                  <p>
                    {incomingFunctional.length || outgoingFunctional.length
                      ? "可继续查看调用方向，判断修改会影响哪些代码。"
                      : "当前载入范围内未发现直接功能调用。"}
                  </p>
                </article>
              </div>
              {behaviorFacts.length ? (
                <ul>
                  {behaviorFacts.map((fact) => (
                    <li key={fact}>{fact}</li>
                  ))}
                </ul>
              ) : null}
            </section>
            <section className="map-detail-level-path">
              <header>
                <span>
                  <Route size={15} />
                  所在结构
                </span>
                <button onClick={() => setTab("hierarchy")}>
                  查看完整结构 <ChevronRight size={14} />
                </button>
              </header>
              <div>
                {context.trail.map((item, index) => (
                  <span
                    key={item.node.id}
                    className={item.role === "focus" ? "is-current" : ""}
                  >
                    <small>L{item.level}</small>
                    <b>{item.node.label}</b>
                    {index < context.trail.length - 1 ? (
                      <ChevronRight size={13} />
                    ) : null}
                  </span>
                ))}
              </div>
            </section>
            <section
              className="map-detail-section map-evidence-authority"
              aria-label="证据与权限摘要"
            >
              <header>
                <div>
                  <span>证据与权限</span>
                  <h3>当前节点的可核验依据</h3>
                </div>
                {node.status ? (
                  <Status value={node.status}>
                    {statusLabel(node.status)}
                  </Status>
                ) : (
                  <span className="map-evidence-authority__missing">
                    状态未提供
                  </span>
                )}
              </header>
              <p
                className={reviewProfile.summary.available ? "" : "is-missing"}
              >
                {reviewProfile.summary.value}
              </p>
              <dl>
                <div>
                  <dt>Identity</dt>
                  <dd>{reviewProfile.identity.value}</dd>
                </div>
                <div>
                  <dt>Type</dt>
                  <dd>{entityTypeLabel(reviewProfile.type.value)}</dd>
                </div>
                <div>
                  <dt>Source</dt>
                  <dd>{reviewProfile.source.value}</dd>
                </div>
                <div>
                  <dt>Scope</dt>
                  <dd>{reviewProfile.scope.value}</dd>
                </div>
                <div
                  className={reviewProfile.acl.available ? "" : "is-missing"}
                >
                  <dt>ACL</dt>
                  <dd>{reviewProfile.acl.value}</dd>
                </div>
                <div
                  className={
                    reviewProfile.citation.available ? "" : "is-missing"
                  }
                >
                  <dt>Citation</dt>
                  <dd>{reviewProfile.citation.value}</dd>
                </div>
                <div
                  className={
                    reviewProfile.version.available ? "" : "is-missing"
                  }
                >
                  <dt>Version</dt>
                  <dd>{reviewProfile.version.value}</dd>
                </div>
              </dl>
            </section>
            {node.children?.length ? (
              <section className="map-detail-section map-capability-section">
                <header>
                  <div>
                    <span>内部能力</span>
                    <h3>{childSectionTitle(node)}</h3>
                  </div>
                  <button onClick={() => setTab("hierarchy")}>
                    查看全部 {node.children.length}
                    <ChevronRight size={14} />
                  </button>
                </header>
                <div className="map-child-list">
                  {node.children.slice(0, 5).map((child) => (
                    <button key={child.id} onClick={() => onOpenChild(child)}>
                      {(() => {
                        const ChildIcon = iconForType(child.type);
                        return <ChildIcon size={15} />;
                      })()}
                      <span>
                        <strong>{child.label}</strong>
                        <small>
                          {entityTypeLabel(child.type)}
                          {child.meta ? ` · ${child.meta}` : ""}
                        </small>
                      </span>
                      <ChevronRight size={15} />
                    </button>
                  ))}
                </div>
              </section>
            ) : null}
            {incomingFunctional.length || outgoingFunctional.length ? (
              <section className="map-detail-section map-code-flow">
                <header>
                  <div>
                    <span>影响范围</span>
                    <h3>上游谁会受影响，下游依赖什么</h3>
                  </div>
                  <button onClick={() => setTab("relations")}>
                    全部 {relations.length}
                    <ChevronRight size={14} />
                  </button>
                </header>
                <div className="map-code-flow__columns">
                  <section>
                    <h4>
                      <ArrowLeft size={14} />
                      上游 · 哪些节点使用它
                    </h4>
                    {incomingFunctional.length ? (
                      incomingFunctional.slice(0, 3).map((edge) => {
                        const other = nodesById.get(edge.source);
                        if (!other) return null;
                        return (
                          <button key={edge.id} onClick={() => onSelect(other)}>
                            <span>
                              <strong>{other.label}</strong>
                              <small>{edge.label}</small>
                            </span>
                            <ChevronRight size={14} />
                          </button>
                        );
                      })
                    ) : (
                      <p>当前视图未发现传入调用</p>
                    )}
                  </section>
                  <section>
                    <h4>
                      <ArrowRight size={14} />
                      下游 · 它直接依赖什么
                    </h4>
                    {outgoingFunctional.length ? (
                      outgoingFunctional.slice(0, 3).map((edge) => {
                        const other = nodesById.get(edge.target);
                        if (!other) return null;
                        return (
                          <button key={edge.id} onClick={() => onSelect(other)}>
                            <span>
                              <strong>{other.label}</strong>
                              <small>{edge.label}</small>
                            </span>
                            <ChevronRight size={14} />
                          </button>
                        );
                      })
                    ) : (
                      <p>当前视图未发现向外调用</p>
                    )}
                  </section>
                </div>
              </section>
            ) : null}
            {node.domain !== "workspace" && node.source !== "cluster" ? (
              <section className="map-detail-section map-detail-next-action">
                <div>
                  <h3>继续沿关系探索</h3>
                  <p>
                    {neighborTotal == null
                      ? "读取当前节点尚未载入的直接邻居。"
                      : `已读取 ${relations.length} 条连接${neighborTotal ? `，服务端共有 ${neighborTotal} 条` : ""}。`}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  onClick={onExpand}
                  disabled={busy || hasMore === false}
                >
                  <Plus size={16} />
                  {busy
                    ? "正在读取…"
                    : hasMore
                      ? "继续展开"
                      : neighborTotal != null
                        ? "已展开全部"
                        : "展开相邻节点"}
                </Button>
                {error ? (
                  <span className="map-expand-error">{error}</span>
                ) : null}
              </section>
            ) : null}
          </>
        ) : null}
        {tab === "hierarchy" ? (
          <>
            <NodeContextBoard
              focus={node}
              nodes={inspectorNodes}
              edges={edges}
              busy={busy}
              hasMore={hasMore}
              onExpand={onExpand}
              onSelect={onSelect}
              onNavigate={navigateContextNode}
            />
            {(node.children?.length || 0) > 24 ? (
              <section className="map-detail-section">
                <h3>全部内部实体</h3>
                <div className="map-child-list">
                  {node.children?.map((child) => (
                    <button key={child.id} onClick={() => onOpenChild(child)}>
                      {(() => {
                        const ChildIcon = iconForType(child.type);
                        return <ChildIcon size={15} />;
                      })()}
                      <span>
                        <strong>{child.label}</strong>
                        <small>
                          {entityTypeLabel(child.type)}
                          {child.meta ? ` · ${child.meta}` : ""}
                        </small>
                      </span>
                      <ChevronRight size={15} />
                    </button>
                  ))}
                </div>
              </section>
            ) : null}
          </>
        ) : null}
        {tab === "relations" ? (
          <section className="map-detail-section map-detail-section--flush">
            {relations.length ? (
              <>
                <div
                  className="map-relation-filters"
                  role="group"
                  aria-label="详情关系类型"
                >
                  {relationFilterOptions.map((option) => (
                    <button
                      key={option.value}
                      className={
                        relationFilter === option.value ? "is-active" : ""
                      }
                      onClick={() => setRelationFilter(option.value)}
                    >
                      {option.label}
                      <b>{option.count}</b>
                    </button>
                  ))}
                </div>
                <div className="map-relation-flow-summary">
                  <span>
                    <ArrowRight size={14} />
                    发出 {outgoing.length}
                  </span>
                  <span>
                    <ArrowLeft size={14} />
                    接收 {incoming.length}
                  </span>
                  <span>
                    <ShieldCheck size={14} />
                    待复核{" "}
                    {
                      relations.filter(
                        (edge) => edge.review_status === "unreviewed",
                      ).length
                    }
                  </span>
                </div>
                <div className="map-relation-list">
                  {visibleRelations.map((edge) => {
                    const outgoing = edge.source === node.id;
                    const other = nodesById.get(
                      outgoing ? edge.target : edge.source,
                    );
                    if (!other) return null;
                    const OtherIcon = iconForNode(other);
                    return (
                      <button key={edge.id} onClick={() => onSelect(other)}>
                        <span className={`domain-${other.domain}`}>
                          <OtherIcon size={16} />
                        </span>
                        <span>
                          <small>
                            <i>{outgoing ? "向外" : "传入"}</i>
                            {edge.label}
                            {(edge.occurrences || 1) > 1
                              ? ` × ${edge.occurrences}`
                              : ""}
                          </small>
                          <strong>{other.label}</strong>
                          <em>
                            {entityTypeLabel(other.type)} · {other.meta}
                          </em>
                          <span className="map-relation-audit" title={edge.id}>
                            <b
                              className={`status-${edge.review_status || "confirmed"}`}
                            >
                              {statusLabel(edge.review_status || "confirmed")}
                            </b>
                            {edge.confidence != null ? (
                              <b>置信度 {Math.round(edge.confidence * 100)}%</b>
                            ) : null}
                            {edge.derivation ? (
                              <b>来源 {edge.derivation}</b>
                            ) : null}
                          </span>
                        </span>
                        <ChevronRight size={16} />
                      </button>
                    );
                  })}
                </div>
              </>
            ) : (
              <p className="map-detail-empty">
                当前载入范围内没有直接关系，可展开邻居继续检查。
              </p>
            )}
          </section>
        ) : null}
        {tab === "locator" ? (
          <>
            <section className="map-locator-summary">
              <span>
                <LocateFixed size={16} />
              </span>
              <div>
                <small>可复现定位</small>
                <strong>
                  {node.path || node.qualifiedName || node.locator}
                </strong>
              </div>
            </section>
            <dl className="map-detail-facts map-detail-facts--locator">
              <div>
                <dt>来源</dt>
                <dd>{domainLabel(node.domain)}</dd>
              </div>
              <div>
                <dt>类型</dt>
                <dd>{entityTypeLabel(node.type)}</dd>
              </div>
              {node.language ? (
                <div>
                  <dt>语言</dt>
                  <dd>{node.language}</dd>
                </div>
              ) : null}
              {node.startLine ? (
                <div>
                  <dt>代码范围</dt>
                  <dd>
                    L{node.startLine}
                    {node.endLine ? `–${node.endLine}` : ""}
                  </dd>
                </div>
              ) : null}
              {node.repositoryId ? (
                <div>
                  <dt>仓库</dt>
                  <dd>{node.repositoryId.split("/").at(-1)}</dd>
                </div>
              ) : null}
              {node.status ? (
                <div>
                  <dt>状态</dt>
                  <dd>{statusLabel(node.status)}</dd>
                </div>
              ) : null}
            </dl>
            {node.qualifiedName ? (
              <section className="map-detail-section">
                <h3>限定名</h3>
                <code className="map-locator">{node.qualifiedName}</code>
              </section>
            ) : null}
            <section className="map-detail-section">
              <h3>稳定定位</h3>
              <code className="map-locator">{node.locator}</code>
            </section>
            {node.version ? (
              <section className="map-detail-section">
                <h3>版本</h3>
                <code className="map-locator">{node.version}</code>
              </section>
            ) : null}
          </>
        ) : null}
      </div>
      <footer
        className={`map-inspector__actions ${tab === "hierarchy" ? "is-context-toolbar" : ""}`}
      >
        {node.source !== "cluster" && tab !== "hierarchy" ? (
          <p className="map-inspector__return-hint">
            当前节点已保存到地址；打开来源后可用浏览器返回继续本次审阅。
          </p>
        ) : null}
        {drillLabel && onDrill ? (
          <Button onClick={onDrill}>
            <Network size={16} />
            {drillLabel}
          </Button>
        ) : null}
        {node.type === "CodeFileContainer" && onToggleFile ? (
          <Button variant="secondary" onClick={onToggleFile}>
            <Layers3 size={16} />
            {node.expanded ? "收起内部预览" : "在协同图展开实体"}
          </Button>
        ) : null}
        {node.source !== "cluster" ? (
          <Link
            className={`button ${drillLabel ? "button--secondary" : "button--primary"}`}
            to={routeForNode(node, projectId)}
          >
            <LocateFixed size={16} />
            {sourceActionLabel(node)}
          </Link>
        ) : null}
        {reviewProfile.reviewCandidateId ? (
          <Link
            className="button button--secondary"
            to={`/p/${encodeURIComponent(projectId)}/relations?status=unreviewed&relation=${encodeURIComponent(
              reviewProfile.reviewCandidateId,
            )}`}
          >
            <ShieldCheck size={16} />
            复核关系
          </Link>
        ) : null}
      </footer>
    </aside>
  );
}

export function ResearchMap() {
  const { projectId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedCodePath = searchParams.get("code");
  const requestedCodeRepository = searchParams.get("repository");
  const requestedCodeEntity = searchParams.get("entity");
  const requestedCodeQuery = searchParams.get("q") || "";
  const requestedFocus = searchParams.get("focus");
  const persistedCodexTurnFocus = searchParams.get("turn_focus");
  const requestedCodexTurnFocus =
    persistedCodexTurnFocus ||
    (requestedFocus?.startsWith("codex://thread/") &&
    requestedFocus.includes("/turn/")
      ? requestedFocus
      : "");
  const requestedBoard = searchParams.get("board");
  const requestedTopic = searchParams.get("topic");
  const requestedThread = searchParams.get("thread");
  const requestedCodeView: CodeGraphView =
    searchParams.get("view") === "vector" ? "vector" : "branch";
  const requestedFileLayout =
    searchParams.get("file_layout") === "manual" ? "manual" : "auto";
  const requestedModuleScope =
    searchParams.get("module_scope") === "all" ? "all" : "core";
  const requestedOpenFiles = searchParams.getAll("open_file");
  const requestedRelations =
    searchParams.get("relations")?.split(",").filter(Boolean) || [];
  const requestedDirection = (
    ["incoming", "outgoing"].includes(searchParams.get("direction") || "")
      ? searchParams.get("direction")
      : "both"
  ) as CodeRelationDirection;
  const [loadTarget, setLoadTarget] = useState(DEFAULT_LOAD_TARGET);
  const [committedLimit, setCommittedLimit] = useState(DEFAULT_LOAD_TARGET);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectionTrail, setSelectionTrail] = useState<ResearchMapNode[]>([]);
  const [board, setBoard] = useState<ResearchBoard>(
    requestedCodePath ||
      requestedCodeEntity ||
      requestedCodeQuery ||
      requestedBoard === "code"
      ? "code"
      : requestedBoard === "topic"
        ? "topic"
        : requestedBoard === "codex"
          ? "codex"
          : "overview",
  );
  const [activeTopicId, setActiveTopicId] = useState<string | null>(
    requestedTopic,
  );
  const [activeThreadId, setActiveThreadId] = useState<string | null>(
    requestedThread,
  );
  const [activeCodePath, setActiveCodePath] = useState<string | null>(
    requestedCodePath,
  );
  const [activeCodeRepositoryId, setActiveCodeRepositoryId] = useState<
    string | null
  >(requestedCodeRepository);
  const [searchText, setSearchText] = useState(requestedCodeQuery);
  const [searchMenuOpen, setSearchMenuOpen] = useState(false);
  const [sessionOverviewQuery, setSessionOverviewQuery] = useState("");
  const [sessionOverviewPage, setSessionOverviewPage] = useState(0);
  const [impactPanelExpanded, setImpactPanelExpanded] = useState(false);
  const [viewport, setViewport] = useState({ x: 0, y: 0, scale: 0.86 });
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [positions, setPositions] = useState<
    Record<string, { x: number; y: number }>
  >({});
  const [neighborNodes, setNeighborNodes] = useState<GraphNode[]>([]);
  const [neighborEdges, setNeighborEdges] = useState<GraphEdge[]>([]);
  const [neighborPages, setNeighborPages] = useState<
    Record<
      string,
      {
        nextCursor: number;
        total: number | null;
        hasMore: boolean;
      }
    >
  >({});
  const [neighborBusy, setNeighborBusy] = useState<string | null>(null);
  const [neighborError, setNeighborError] = useState<Record<string, string>>(
    {},
  );
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const vectorGraphRef = useRef<CodeVectorGraphHandle | null>(null);
  const viewportSnapshot = useRef(viewport);
  const lockedViewport = useRef<typeof viewport | null>(null);
  const initializedView = useRef<string | null>(null);
  const centeredSelection = useRef<string | null>(null);
  const positionedSnapshot = useRef<string>("");
  const manuallyPositioned = useRef<Set<string>>(new Set());
  const pan = useRef<{
    pointerId: number;
    x: number;
    y: number;
    originX: number;
    originY: number;
  } | null>(null);
  const nodeDrag = useRef<{
    pointerId: number;
    id: string;
    x: number;
    y: number;
    originX: number;
    originY: number;
    moved: boolean;
    groupOrigins?: Record<string, { x: number; y: number }>;
  } | null>(null);

  const dashboardQuery = useQuery({
    queryKey: ["dashboard", projectId],
    queryFn: () => api.projects.getDashboard(projectId),
  });
  const graphQuery = useQuery({
    queryKey: [
      "research-map",
      projectId,
      committedLimit,
      board,
      activeThreadId,
      requestedCodexTurnFocus,
    ],
    queryFn: () =>
      api.graph.snapshot(
        projectId,
        committedLimit,
        board === "codex"
          ? "codex"
          : board === "topic"
            ? "research"
            : "overview",
        "relations",
        board === "codex" ? activeThreadId || "" : "",
        board === "codex"
          ? { focusEntityId: requestedCodexTurnFocus || undefined }
          : undefined,
      ),
    placeholderData: (previous) => previous,
    enabled: board !== "code",
    retry: false,
  });
  const codeGraphQuery = useQuery({
    queryKey: [
      "research-map-code",
      projectId,
      committedLimit,
      activeCodePath,
      requestedCodeEntity,
      requestedCodeQuery,
      requestedRelations.join(","),
      requestedDirection,
    ],
    queryFn: () =>
      api.graph.snapshot(
        projectId,
        committedLimit,
        "code",
        requestedCodeQuery && !requestedCodeEntity ? "semantic" : "relations",
        requestedCodeQuery || activeCodePath || "",
        {
          focusEntityId: requestedCodeEntity || undefined,
          relationTypes: requestedRelations,
          direction: requestedDirection,
        },
      ),
    placeholderData: (previous) => previous,
    enabled: board === "code",
    retry: false,
  });
  const repositoriesQuery = useQuery({
    queryKey: ["research-map-repositories", projectId],
    queryFn: () => api.code.repositories(projectId),
    enabled: board === "code",
  });
  const sessionsQuery = useQuery({
    queryKey: ["research-map-sessions", projectId],
    queryFn: ({ signal }) => api.sessions.list(projectId, "", signal),
    retry: false,
  });
  const bindingsQuery = useQuery({
    queryKey: ["bindings", projectId],
    queryFn: () => api.evidence.bindings(projectId),
  });

  const updateCodeLocation = useCallback(
    (values: {
      codePath?: string | null;
      repository?: string | null;
      codeBoard?: boolean;
      board?: ResearchBoard | null;
      topic?: string | null;
      thread?: string | null;
      entity?: string | null;
      query?: string | null;
      relations?: string[];
      direction?: CodeRelationDirection;
      view?: CodeGraphView;
      fileLayout?: "auto" | "manual";
      moduleScope?: "core" | "all";
      expandedFiles?: string[];
      clearPath?: boolean;
      clearCodeState?: boolean;
    }) => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        const setOrDelete = (key: string, value?: string | null) => {
          if (value) next.set(key, value);
          else next.delete(key);
        };
        if (values.clearCodeState) {
          [
            "code",
            "repository",
            "entity",
            "q",
            "relations",
            "direction",
            "view",
            "file_layout",
            "module_scope",
          ].forEach((key) => next.delete(key));
          next.delete("open_file");
          next.delete("turn_focus");
        }
        if (
          values.clearCodeState ||
          values.clearPath ||
          "board" in values ||
          "codeBoard" in values ||
          "topic" in values ||
          "thread" in values ||
          "codePath" in values ||
          "repository" in values ||
          "entity" in values ||
          "query" in values
        ) {
          next.delete("focus");
          next.delete("turn_focus");
        }
        if ("codePath" in values) setOrDelete("code", values.codePath);
        if ("repository" in values)
          setOrDelete("repository", values.repository);
        if ("codeBoard" in values) {
          setOrDelete("board", values.codeBoard ? "code" : null);
        }
        if ("board" in values) {
          setOrDelete(
            "board",
            values.board && values.board !== "overview" ? values.board : null,
          );
        }
        if ("topic" in values) setOrDelete("topic", values.topic);
        if ("thread" in values) setOrDelete("thread", values.thread);
        if ("entity" in values) setOrDelete("entity", values.entity);
        if ("query" in values) setOrDelete("q", values.query);
        if (values.relations)
          setOrDelete("relations", values.relations.join(","));
        if (values.direction) {
          setOrDelete(
            "direction",
            values.direction === "both" ? null : values.direction,
          );
        }
        if (values.view) {
          setOrDelete("view", values.view === "branch" ? null : values.view);
        }
        if (values.fileLayout) {
          setOrDelete(
            "file_layout",
            values.fileLayout === "auto" ? null : values.fileLayout,
          );
        }
        if (values.moduleScope) {
          setOrDelete(
            "module_scope",
            values.moduleScope === "core" ? null : values.moduleScope,
          );
        }
        if (values.expandedFiles) {
          next.delete("open_file");
          values.expandedFiles.forEach((file) =>
            next.append("open_file", file),
          );
        }
        if (values.clearPath) {
          next.delete("code");
          next.delete("repository");
        }
        return next;
      });
    },
    [setSearchParams],
  );

  const openSessionImpact = useCallback(
    (threadId: string) => {
      if (!threadId) return;
      setSelectedId(null);
      setSelectionTrail([]);
      setActiveThreadId(threadId);
      setBoard("codex");
      updateCodeLocation({
        board: "codex",
        topic: null,
        thread: threadId,
        clearCodeState: true,
      });
    },
    [updateCodeLocation],
  );

  const updateFocusLocation = useCallback(
    (nodeId: string | null) => {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        const currentFocus = next.get("focus") || "";
        if (
          currentFocus.startsWith("codex://thread/") &&
          currentFocus.includes("/turn/")
        ) {
          next.set("turn_focus", currentFocus);
        }
        if (
          nodeId?.startsWith("codex://thread/") &&
          nodeId.includes("/turn/")
        ) {
          next.set("turn_focus", nodeId);
        }
        if (nodeId) next.set("focus", nodeId);
        else next.delete("focus");
        return next;
      });
    },
    [setSearchParams],
  );

  const clearSelection = useCallback(() => {
    setSelectedId(null);
    updateFocusLocation(null);
  }, [updateFocusLocation]);

  const graph = useMemo(() => {
    if (!dashboardQuery.data) return { nodes: [], edges: [] };
    return buildResearchGraph({
      dashboard: dashboardQuery.data,
      graphNodes: graphQuery.data?.nodes || [],
      graphEdges: graphQuery.data?.edges || [],
      bindings: bindingsQuery.data || [],
      neighborNodes,
      neighborEdges,
    });
  }, [
    bindingsQuery.data,
    dashboardQuery.data,
    graphQuery.data,
    neighborEdges,
    neighborNodes,
  ]);
  const codeGraph = useMemo(() => {
    if (!dashboardQuery.data) return { nodes: [], edges: [] };
    const repositoryNodes: GraphNode[] = (repositoriesQuery.data || []).map(
      (repository) => ({
        id: repository.id,
        type: "Repository",
        label: repository.name,
        locator:
          repository.local_path || repository.source_url || repository.id,
        domain: "code",
        version: repository.head_commit,
        repository_id: repository.id,
        metadata: {
          source_type: repository.source_type,
          status: repository.status,
          files: repository.stats.files || 0,
          symbols: repository.stats.symbols || 0,
        },
      }),
    );
    const graphNodes = new Map(
      [...repositoryNodes, ...(codeGraphQuery.data?.nodes || [])].map(
        (node) => [node.id, node],
      ),
    );
    return buildResearchGraph({
      dashboard: dashboardQuery.data,
      graphNodes: [...graphNodes.values()],
      graphEdges: codeGraphQuery.data?.edges || [],
      bindings: bindingsQuery.data || [],
      neighborNodes,
      neighborEdges,
    });
  }, [
    bindingsQuery.data,
    codeGraphQuery.data,
    dashboardQuery.data,
    neighborEdges,
    neighborNodes,
    repositoriesQuery.data,
  ]);

  const sessionRecords = useMemo(
    () => (Array.isArray(sessionsQuery.data) ? sessionsQuery.data : []),
    [sessionsQuery.data],
  );
  const filteredSessions = useMemo(() => {
    const query = sessionOverviewQuery.trim().toLowerCase();
    return [...sessionRecords]
      .filter((session) =>
        query
          ? `${session.title} ${session.thread_id} ${session.cwd} ${session.status}`
              .toLowerCase()
              .includes(query)
          : true,
      )
      .sort(
        (left, right) =>
          String(right.updated_at || "").localeCompare(
            String(left.updated_at || ""),
          ) || left.thread_id.localeCompare(right.thread_id),
      );
  }, [sessionOverviewQuery, sessionRecords]);
  const sessionOverviewPageCount = Math.max(
    1,
    Math.ceil(filteredSessions.length / SESSION_OVERVIEW_PAGE_SIZE),
  );
  const visibleSessionPage = useMemo(
    () =>
      filteredSessions.slice(
        sessionOverviewPage * SESSION_OVERVIEW_PAGE_SIZE,
        (sessionOverviewPage + 1) * SESSION_OVERVIEW_PAGE_SIZE,
      ),
    [filteredSessions, sessionOverviewPage],
  );
  useEffect(() => {
    setSessionOverviewPage((current) =>
      Math.min(current, sessionOverviewPageCount - 1),
    );
  }, [sessionOverviewPageCount]);

  const boardGraph = useMemo(() => {
    if (board === "topic") {
      return buildTopicBoard(graph.nodes, graph.edges, activeTopicId);
    }
    if (board === "codex") {
      return activeThreadId
        ? buildCodexBoard(graph.nodes, graph.edges, activeThreadId)
        : buildCodexOverviewBoard(visibleSessionPage, filteredSessions.length);
    }
    if (board === "code") {
      if (requestedCodeView === "vector") {
        return buildCodeVectorBoard(
          codeGraph.nodes,
          codeGraph.edges,
          (codeGraphQuery.data?.nodes || []).map((node) => node.id),
        );
      }
      if (requestedCodeEntity) {
        return buildCodeRelationBoard(
          codeGraph.nodes,
          codeGraph.edges,
          requestedCodeEntity,
        );
      }
      if (!activeCodePath) {
        return buildCodeCollaborationBoard(codeGraph.nodes, codeGraph.edges, {
          expandedFileKeys: requestedOpenFiles,
          autoExpand: requestedFileLayout === "auto",
          relationTypes: requestedRelations,
          showAllModules: requestedModuleScope === "all",
        });
      }
      return buildCodeBoard(
        codeGraph.nodes,
        codeGraph.edges,
        activeCodePath,
        activeCodeRepositoryId,
      );
    }
    return buildResearchOverview(graph.nodes, graph.edges);
  }, [
    activeCodePath,
    activeCodeRepositoryId,
    activeThreadId,
    activeTopicId,
    board,
    codeGraph.edges,
    codeGraph.nodes,
    graph.edges,
    graph.nodes,
    filteredSessions.length,
    requestedCodeEntity,
    requestedCodeQuery,
    requestedCodeView,
    codeGraphQuery.data?.nodes,
    requestedFileLayout,
    requestedModuleScope,
    requestedOpenFiles,
    requestedRelations,
    visibleSessionPage,
  ]);
  const collaborationStats: CodeCollaborationStats | null =
    "stats" in boardGraph
      ? (boardGraph as { stats: CodeCollaborationStats }).stats
      : null;
  const isCodeCollaboration =
    board === "code" &&
    requestedCodeView === "branch" &&
    !requestedCodeEntity &&
    !activeCodePath;
  const setExpandedFiles = useCallback(
    (files: string[], mode: "auto" | "manual" = "manual") => {
      lockedViewport.current = viewportSnapshot.current;
      updateCodeLocation({
        fileLayout: mode,
        expandedFiles: files,
      });
    },
    [updateCodeLocation],
  );
  const toggleFileExpansion = useCallback(
    (node: ResearchMapNode) => {
      if (!node.groupKey || !collaborationStats) return;
      const next = new Set(collaborationStats.expandedFileKeys);
      if (next.has(node.groupKey)) next.delete(node.groupKey);
      else next.add(node.groupKey);
      setExpandedFiles([...next].sort(), "manual");
    },
    [collaborationStats, setExpandedFiles],
  );
  useEffect(() => {
    viewportSnapshot.current = viewport;
  }, [viewport]);
  useEffect(() => {
    if (!lockedViewport.current) return;
    setViewport(lockedViewport.current);
    lockedViewport.current = null;
  }, [requestedFileLayout, requestedOpenFiles.join("\u0000")]);
  const canonicalPositions = useMemo(
    () =>
      Object.fromEntries(
        boardGraph.nodes.map((node) => [node.id, { x: node.x, y: node.y }]),
      ),
    [boardGraph.nodes],
  );
  const layoutSignature = useMemo(
    () =>
      boardGraph.nodes
        .map((node) => `${node.id}:${node.x}:${node.y}`)
        .join("|"),
    [boardGraph.nodes],
  );
  const nodesWithPositions = useMemo(
    () =>
      boardGraph.nodes.map((node) => ({
        ...node,
        ...(positions[node.id] || {}),
      })),
    [boardGraph.nodes, positions],
  );
  const nodesById = useMemo(
    () => new Map(nodesWithPositions.map((node) => [node.id, node])),
    [nodesWithPositions],
  );
  const degreeById = useMemo(() => {
    const values = new Map(nodesWithPositions.map((node) => [node.id, 0]));
    boardGraph.edges.forEach((edge) => {
      values.set(edge.source, (values.get(edge.source) || 0) + 1);
      values.set(edge.target, (values.get(edge.target) || 0) + 1);
    });
    return values;
  }, [boardGraph.edges, nodesWithPositions]);
  const sourceCounts = useMemo(() => {
    const counts = new Map<ResearchDomain, number>();
    sourceOrder.forEach((domain) => counts.set(domain, 0));
    const countNodes =
      board === "code" ? [...graph.nodes, ...codeGraph.nodes] : graph.nodes;
    const seen = new Set<string>();
    countNodes.forEach((node) => {
      if (seen.has(node.id)) return;
      seen.add(node.id);
      counts.set(node.domain, (counts.get(node.domain) || 0) + 1);
    });
    return counts;
  }, [board, codeGraph.nodes, graph.nodes]);
  const filteredNodes = nodesWithPositions;
  const filteredIds = useMemo(
    () => new Set(filteredNodes.map((node) => node.id)),
    [filteredNodes],
  );
  const filteredEdges = useMemo(
    () =>
      boardGraph.edges.filter(
        (edge) => filteredIds.has(edge.source) && filteredIds.has(edge.target),
      ),
    [boardGraph.edges, filteredIds],
  );
  const impactMappings = useMemo(
    () =>
      filteredEdges
        .flatMap((edge) => {
          const source = nodesById.get(edge.source);
          const target = nodesById.get(edge.target);
          if (!source || !target) return [];
          if (
            !["changed_path_maps_to", "changed_symbol_maps_to"].includes(
              edge.predicate,
            ) ||
            source.type !== "CodexThread" ||
            target.domain !== "code" ||
            target.type.endsWith("Frame") ||
            !target.path
          )
            return [];
          return [{ edge, source, target }];
        })
        .sort((left, right) => {
          return (
            (right.edge.occurrences || 1) - (left.edge.occurrences || 1) ||
            left.source.label.localeCompare(right.source.label) ||
            String(left.target.path).localeCompare(String(right.target.path))
          );
        }),
    [filteredEdges, nodesById],
  );
  const selected = selectedId ? nodesById.get(selectedId) || null : null;
  const visibleSelectionTrail = useMemo(
    () =>
      selectionTrail
        .map((item) => nodesById.get(item.id) || item)
        .filter(
          (item, index, values) =>
            !values
              .slice(index + 1)
              .some((candidate) => candidate.id === item.id),
        )
        .slice(-5),
    [nodesById, selectionTrail],
  );
  const selectedReviewProfile = useMemo(
    () =>
      selected
        ? buildEvidenceReviewProfile({
            node: selected,
            edges: boardGraph.edges,
            nodesById,
            projectId,
            projectAclRef: dashboardQuery.data?.project.acl_ref,
          })
        : null,
    [
      boardGraph.edges,
      dashboardQuery.data?.project.acl_ref,
      nodesById,
      projectId,
      selected,
    ],
  );
  const navigateBack = useCallback(() => {
    const previous = visibleSelectionTrail.at(-2);
    if (!previous) {
      clearSelection();
      return;
    }
    setSelectionTrail((current) => current.slice(0, -1));
    setSelectedId(previous.id);
    updateFocusLocation(previous.id);
  }, [clearSelection, updateFocusLocation, visibleSelectionTrail]);
  const focusedNodeIds = useMemo(() => {
    if (!selectedId) return null;
    const direct = new Set<string>([selectedId]);
    boardGraph.edges.forEach((edge) => {
      if (edge.source === selectedId) direct.add(edge.target);
      if (edge.target === selectedId) direct.add(edge.source);
    });
    if (direct.size < 14) {
      boardGraph.edges.forEach((edge) => {
        if (direct.has(edge.source)) direct.add(edge.target);
        if (direct.has(edge.target)) direct.add(edge.source);
      });
    }
    return direct;
  }, [boardGraph.edges, selectedId]);
  const crossSourceRelations = useMemo(
    () =>
      boardGraph.edges.reduce((total, edge) => {
        const source = nodesById.get(edge.source);
        const target = nodesById.get(edge.target);
        return source && target && source.domain !== target.domain
          ? total + (edge.occurrences || 1)
          : total;
      }, 0),
    [boardGraph.edges, nodesById],
  );
  const activeGraphQuery = board === "code" ? codeGraphQuery : graphQuery;
  const missingRequestedFocus = Boolean(
    requestedFocus &&
    activeGraphQuery.data &&
    !activeGraphQuery.isFetching &&
    !nodesById.has(requestedFocus),
  );
  const totalNodes =
    Math.max(
      activeGraphQuery.data?.metadata.total_nodes || 0,
      activeGraphQuery.data?.metadata.visible_nodes || 0,
    ) + (sourceCounts.get("workspace") || 0);
  const boardNodeLimit = board === "codex" ? 160 : 500;
  const totalNodeCeiling = Math.min(
    boardNodeLimit,
    activeGraphQuery.data
      ? Math.max(1, totalNodes)
      : Math.max(DEFAULT_LOAD_TARGET, totalNodes),
  );

  useEffect(() => {
    if (!canvasRef.current) return;
    const observer = new ResizeObserver(([entry]) => {
      setCanvasSize({
        width: entry.contentRect.width,
        height: entry.contentRect.height,
      });
    });
    observer.observe(canvasRef.current);
    return () => observer.disconnect();
  }, [dashboardQuery.data, requestedCodeView]);

  useEffect(() => {
    setSelectedId(null);
    setPositions({});
    setNeighborNodes([]);
    setNeighborEdges([]);
    setNeighborPages({});
    const initialLoad = requestedCodeEntity
      ? RELATION_LOAD_TARGET
      : requestedBoard === "codex" && requestedThread
        ? 96
        : DEFAULT_LOAD_TARGET;
    setLoadTarget(initialLoad);
    setCommittedLimit(initialLoad);
    setBoard(
      requestedCodePath ||
        requestedCodeEntity ||
        requestedCodeQuery ||
        requestedBoard === "code"
        ? "code"
        : requestedBoard === "topic"
          ? "topic"
          : requestedBoard === "codex"
            ? "codex"
            : "overview",
    );
    setActiveTopicId(requestedTopic);
    setActiveThreadId(requestedThread);
    setActiveCodePath(requestedCodePath);
    setActiveCodeRepositoryId(requestedCodeRepository);
    setSearchText(requestedCodeQuery);
    initializedView.current = null;
    centeredSelection.current = null;
    positionedSnapshot.current = "";
    manuallyPositioned.current.clear();
  }, [
    projectId,
    requestedBoard,
    requestedCodeEntity,
    requestedCodePath,
    requestedCodeQuery,
    requestedCodeRepository,
    requestedThread,
    requestedTopic,
  ]);

  useEffect(() => {
    setSelectedId(null);
    setPositions({});
    initializedView.current = null;
    centeredSelection.current = null;
    positionedSnapshot.current = "";
    manuallyPositioned.current.clear();
  }, [board]);

  useEffect(() => {
    if (!requestedFocus) {
      setSelectedId(null);
      return;
    }
    const focused = nodesById.get(requestedFocus);
    if (focused && selectedId !== focused.id) setSelectedId(focused.id);
  }, [nodesById, requestedFocus, selectedId]);

  useEffect(() => {
    if (!selected) return;
    setSelectionTrail((current) => {
      if (current.at(-1)?.id === selected.id) return current;
      return [...current, selected].slice(-8);
    });
  }, [selected]);

  useEffect(() => {
    setSelectionTrail([]);
  }, [board, projectId]);

  useEffect(() => {
    if (board !== "code") return;
    setSelectedId(null);
    setPositions({});
    initializedView.current = null;
    centeredSelection.current = null;
    positionedSnapshot.current = "";
    lockedViewport.current = null;
    manuallyPositioned.current.clear();
  }, [
    board,
    requestedCodeView,
    requestedDirection,
    requestedRelations.join("\u0000"),
    requestedModuleScope,
  ]);

  useEffect(() => {
    if (board !== "code") setActiveCodePath(null);
  }, [board]);

  useEffect(() => {
    if (!boardGraph.nodes.length) return;
    if (layoutSignature === positionedSnapshot.current) return;
    positionedSnapshot.current = layoutSignature;
    setPositions((current) => {
      const next: Record<string, { x: number; y: number }> = {};
      boardGraph.nodes.forEach((node) => {
        if (manuallyPositioned.current.has(node.id) && current[node.id]) {
          next[node.id] = current[node.id];
          return;
        }
        const parentPosition = node.parentId ? current[node.parentId] : null;
        const canonicalParent = node.parentId
          ? canonicalPositions[node.parentId]
          : null;
        const canonical = canonicalPositions[node.id];
        next[node.id] =
          node.parentId &&
          manuallyPositioned.current.has(node.parentId) &&
          parentPosition &&
          canonicalParent
            ? {
                x: canonical.x + parentPosition.x - canonicalParent.x,
                y: canonical.y + parentPosition.y - canonicalParent.y,
              }
            : canonical;
      });
      return next;
    });
  }, [boardGraph.nodes.length, canonicalPositions, layoutSignature]);

  useEffect(() => {
    if (!canvasSize.width || !canvasSize.height || !nodesWithPositions.length)
      return;
    const viewSignature = [
      projectId,
      board,
      activeTopicId || "",
      activeThreadId || "",
      activeCodePath || "",
      requestedCodeEntity || "",
      requestedCodeQuery,
      requestedCodeView,
      requestedDirection,
      requestedRelations.join(","),
      requestedModuleScope,
      layoutSignature,
    ].join(":");
    if (initializedView.current === viewSignature) return;
    initializedView.current = viewSignature;
    const nextViewport = initialViewportForBoard(
      nodesWithPositions,
      canvasSize,
      {
        board,
        activeThreadId,
        activeCodePath,
        requestedCodeEntity,
        isCodeCollaboration,
      },
    );
    setViewport(nextViewport);
  }, [
    board,
    activeThreadId,
    activeTopicId,
    activeCodePath,
    isCodeCollaboration,
    canvasSize,
    nodesWithPositions,
    projectId,
    requestedCodeEntity,
    requestedCodeQuery,
    requestedCodeView,
    requestedDirection,
    requestedRelations.join("\u0000"),
    requestedModuleScope,
    layoutSignature,
  ]);

  useEffect(() => {
    if (!selectedId || !canvasSize.width || !canvasSize.height) {
      centeredSelection.current = null;
      return;
    }
    const key = `${selectedId}:${Math.round(canvasSize.width)}:${Math.round(canvasSize.height)}`;
    if (centeredSelection.current === key) return;
    const selectedNode = nodesById.get(selectedId);
    if (!selectedNode) return;
    centeredSelection.current = key;
    setViewport((current) => {
      const size = nodeDimensions(selectedNode);
      const insetX = (size.width * current.scale) / 2 + 34;
      const insetY = (size.height * current.scale) / 2 + 42;
      const screenX = selectedNode.x * current.scale + current.x;
      const screenY = selectedNode.y * current.scale + current.y;
      const left = insetX;
      const right = canvasSize.width - insetX;
      const top = insetY;
      const bottom = canvasSize.height - insetY;
      if (
        screenX >= left &&
        screenX <= right &&
        screenY >= top &&
        screenY <= bottom
      ) {
        return current;
      }
      const preferredX = Math.min(
        right,
        Math.max(left, canvasSize.width * 0.58),
      );
      const preferredY = Math.min(
        bottom,
        Math.max(top, canvasSize.height * 0.48),
      );
      return {
        ...current,
        x: current.x + preferredX - screenX,
        y: current.y + preferredY - screenY,
      };
    });
  }, [canvasSize.height, canvasSize.width, nodesById, selectedId]);

  const visibleNodes = useMemo(() => {
    const shouldCull =
      filteredNodes.length > 520 ||
      (isCodeCollaboration && filteredNodes.length > 32);
    if (!shouldCull || !canvasSize.width) return filteredNodes;
    const margin = isCodeCollaboration ? 80 : 260;
    return filteredNodes.filter((node) => {
      const screenX = node.x * viewport.scale + viewport.x;
      const screenY = node.y * viewport.scale + viewport.y;
      return (
        screenX >= -margin &&
        screenX <= canvasSize.width + margin &&
        screenY >= -margin &&
        screenY <= canvasSize.height + margin
      );
    });
  }, [
    canvasSize.height,
    canvasSize.width,
    filteredNodes,
    isCodeCollaboration,
    viewport,
  ]);
  const renderedIds = useMemo(
    () => new Set(visibleNodes.map((node) => node.id)),
    [visibleNodes],
  );
  const visibleEdges = useMemo(
    () =>
      filteredEdges.filter(
        (edge) => renderedIds.has(edge.source) && renderedIds.has(edge.target),
      ),
    [filteredEdges, renderedIds],
  );

  const searchMatches = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    if (!query) return [];
    return nodesWithPositions
      .filter((node) =>
        `${node.label} ${node.meta} ${node.type}`.toLowerCase().includes(query),
      )
      .slice(0, 8);
  }, [nodesWithPositions, searchText]);

  const selectNode = useCallback(
    (node: ResearchMapNode) => {
      setSelectedId(node.id);
      setSearchMenuOpen(false);
      updateFocusLocation(node.id);
    },
    [updateFocusLocation],
  );
  const activateNode = useCallback(
    (node: ResearchMapNode) => {
      if (node.type === "CodeBoard") {
        updateCodeLocation({
          board: "code",
          topic: null,
          thread: null,
          view: "branch",
        });
        setBoard("code");
        setSearchText("");
        return;
      }
      if (node.type === "CodexBoard") {
        updateCodeLocation({
          board: "codex",
          thread: null,
          topic: null,
          clearCodeState: true,
        });
        setBoard("codex");
        setActiveThreadId(null);
        setSearchText("");
        return;
      }
      selectNode(node);
    },
    [selectNode, updateCodeLocation],
  );

  const drillNode = useCallback(
    (node: ResearchMapNode) => {
      setSelectedId(null);
      if (node.type === "CodeFileContainer") {
        updateCodeLocation({
          board: "code",
          topic: null,
          thread: null,
          codePath: node.path || node.meta,
          repository: node.repositoryId || null,
          entity: null,
          query: null,
          relations: [],
        });
        return;
      }
      if (
        board === "code" &&
        ["FileVersion", "CodeFileGroup", "CodeFileFocus"].includes(node.type)
      ) {
        updateCodeLocation({
          board: "code",
          topic: null,
          thread: null,
          codePath: node.path || node.meta.split(" · ")[0],
          repository: node.repositoryId || null,
          entity: null,
          query: null,
          relations: [],
          view: "branch",
        });
        return;
      }
      if (!(board === "code" && requestedCodeQuery)) setSearchText("");
      if (node.type === "ResearchTopic") {
        updateCodeLocation({
          board: "topic",
          topic: node.id,
          thread: null,
          clearCodeState: true,
        });
        setActiveTopicId(node.id);
        setBoard("topic");
        return;
      }
      if (
        node.type === "CodexBoard" ||
        node.type === "TopicCodexScope" ||
        node.type === "CodexWorkspaceBoard"
      ) {
        updateCodeLocation({
          board: "codex",
          thread: null,
          topic: null,
          clearCodeState: true,
        });
        setActiveThreadId(null);
        setBoard("codex");
        return;
      }
      if (node.type === "CodexThread") {
        updateCodeLocation({
          board: "codex",
          thread: node.id,
          topic: null,
          clearCodeState: true,
        });
        setActiveThreadId(node.id);
        setBoard("codex");
        return;
      }
      if (
        node.type === "CodeBoard" ||
        node.type === "TopicCodeScope" ||
        node.type === "SessionCodeGroup" ||
        (board === "codex" && node.type === "CodeFileGroup")
      ) {
        const codePath =
          node.path || (node.type === "CodeFileGroup" ? node.meta : null);
        if (codePath) {
          updateCodeLocation({
            board: "code",
            topic: null,
            thread: null,
            codePath,
            repository: node.repositoryId || null,
            entity: null,
            query: null,
            relations: [],
          });
        } else {
          updateCodeLocation({
            board: "code",
            topic: null,
            thread: null,
            view: "branch",
          });
          setBoard("code");
        }
        return;
      }
      if (board === "code" && node.type === "CodeFileGroup") {
        updateCodeLocation({
          board: "code",
          topic: null,
          thread: null,
          codePath: node.path || node.meta,
          repository: node.repositoryId || null,
          entity: null,
          query: null,
          relations: [],
        });
        return;
      }
      if (
        board === "code" &&
        node.domain === "code" &&
        [
          "CodeSymbol",
          "CodeClass",
          "CodeMethod",
          "CodeFunction",
          "CodeInterface",
          "CodeModule",
        ].includes(node.type)
      ) {
        updateCodeLocation({
          board: "code",
          topic: null,
          thread: null,
          entity: node.id,
          query: requestedCodeQuery || null,
          clearPath: true,
          view: "branch",
        });
      }
    },
    [board, requestedCodeQuery, updateCodeLocation],
  );

  const drillLabelForNode = useCallback(
    (node: ResearchMapNode): string | null => {
      if (node.type === "ResearchTopic") return "进入主题证据";
      if (
        node.type === "CodexBoard" ||
        node.type === "TopicCodexScope" ||
        node.type === "CodexWorkspaceBoard"
      )
        return "进入会话白板";
      if (node.type === "CodexThread") {
        return board === "codex" && activeThreadId === node.id
          ? null
          : "展开会话执行链";
      }
      if (
        node.type === "CodeBoard" ||
        node.type === "TopicCodeScope" ||
        node.type === "SessionCodeGroup" ||
        (board === "codex" && node.type === "CodeFileGroup")
      )
        return "进入代码白板";
      if (
        board === "code" &&
        [
          "CodeFileGroup",
          "CodeFileContainer",
          "FileVersion",
          "CodeFileFocus",
        ].includes(node.type)
      ) {
        return "打开文件职责图";
      }
      if (
        board === "code" &&
        node.domain === "code" &&
        [
          "CodeSymbol",
          "CodeClass",
          "CodeMethod",
          "CodeFunction",
          "CodeInterface",
          "CodeModule",
        ].includes(node.type) &&
        requestedCodeEntity !== node.id
      )
        return "查看调用与引用";
      return null;
    },
    [activeThreadId, board, requestedCodeEntity],
  );

  const openChild = useCallback(
    (child: NonNullable<ResearchMapNode["children"]>[number]) => {
      if (child.type === "Repository") {
        setSelectedId(null);
        updateCodeLocation({
          board: "code",
          topic: null,
          thread: null,
          codePath: null,
          repository: child.id,
          entity: null,
          query: null,
          relations: [],
          view: "branch",
        });
        setActiveCodePath(null);
        setBoard("code");
        return;
      }
      if (child.type === "CodeFileGroup") {
        setSelectedId(null);
        const sourceNode = codeGraph.nodes.find((node) => node.id === child.id);
        updateCodeLocation({
          board: "code",
          topic: null,
          thread: null,
          codePath:
            child.meta ||
            decodeURIComponent(
              child.id.match(/board:\/\/code\/file\/(.+)$/)?.[1] || "",
            ),
          repository: sourceNode?.repositoryId || null,
          entity: null,
          query: null,
          relations: [],
        });
        return;
      }
      const sourceNode = (
        board === "code" ? codeGraph.nodes : graph.nodes
      ).find((node) => node.id === child.id);
      if (
        child.type === "CodexThread" ||
        child.id.startsWith("codex://thread/")
      ) {
        setSelectedId(null);
        const threadId =
          child.id.match(/^(codex:\/\/thread\/[^/]+)/)?.[1] || child.id;
        updateCodeLocation({
          board: "codex",
          thread: threadId,
          topic: null,
          clearCodeState: true,
        });
        setActiveThreadId(threadId);
        setBoard("codex");
        return;
      }
      if (
        sourceNode?.domain === "code" &&
        [
          "CodeSymbol",
          "CodeClass",
          "CodeMethod",
          "CodeFunction",
          "CodeInterface",
          "CodeModule",
        ].includes(sourceNode.type)
      ) {
        setSelectedId(null);
        updateCodeLocation({
          board: "code",
          topic: null,
          thread: null,
          entity: sourceNode.id,
          clearPath: true,
        });
        setBoard("code");
        return;
      }
      if (sourceNode?.domain === "code" || child.type === "FileVersion") {
        setSelectedId(null);
        const codePath =
          sourceNode?.path || sourceNode?.meta.split(" · ")[0] || null;
        updateCodeLocation({
          board: "code",
          topic: null,
          thread: null,
          codePath,
          repository: sourceNode?.repositoryId || null,
        });
        setActiveCodePath(codePath);
        setBoard("code");
        return;
      }
      if (sourceNode) selectNode(sourceNode);
    },
    [board, codeGraph.nodes, graph.nodes, selectNode, updateCodeLocation],
  );

  const fitGraph = useCallback(() => {
    if (!filteredNodes.length || !canvasSize.width || !canvasSize.height)
      return;
    setViewport(viewportForNodes(filteredNodes, canvasSize, 1, 0.12));
  }, [canvasSize, filteredNodes]);

  const resetLayout = useCallback(() => {
    const canonicalNodes = boardGraph.nodes.map((node) => ({
      ...node,
      ...(canonicalPositions[node.id] || {}),
    }));
    manuallyPositioned.current.clear();
    setPositions(canonicalPositions);
    clearSelection();
    if (canvasSize.width && canvasSize.height) {
      setViewport(
        initialViewportForBoard(canonicalNodes, canvasSize, {
          board,
          activeThreadId,
          activeCodePath,
          requestedCodeEntity,
          isCodeCollaboration,
        }),
      );
    }
  }, [
    activeCodePath,
    activeThreadId,
    board,
    boardGraph.nodes,
    canonicalPositions,
    canvasSize,
    isCodeCollaboration,
    requestedCodeEntity,
    clearSelection,
  ]);

  const expandSelected = useCallback(async () => {
    if (
      !selected ||
      selected.domain === "workspace" ||
      selected.source === "cluster" ||
      neighborBusy
    )
      return;
    const page = neighborPages[selected.id];
    const cursor = page?.nextCursor || 0;
    setNeighborBusy(selected.id);
    setNeighborError((current) => ({ ...current, [selected.id]: "" }));
    try {
      const response = await api.graph.neighbors(selected.id, cursor, 80);
      setNeighborNodes((current) => {
        const values = new Map(current.map((node) => [node.id, node]));
        response.nodes.forEach((node) => values.set(node.id, node));
        return [...values.values()];
      });
      setNeighborEdges((current) => {
        const values = new Map(current.map((edge) => [edge.id, edge]));
        response.edges.forEach((edge) => values.set(edge.id, edge));
        return [...values.values()];
      });
      setNeighborPages((current) => ({
        ...current,
        [selected.id]: {
          nextCursor: response.next_cursor,
          total: response.total,
          hasMore: response.has_more,
        },
      }));
    } catch (error) {
      setNeighborError((current) => ({
        ...current,
        [selected.id]: error instanceof Error ? error.message : "邻居读取失败",
      }));
    } finally {
      setNeighborBusy(null);
    }
  }, [neighborBusy, neighborPages, selected]);

  const activeTopic =
    graph.nodes.find((node) => node.id === activeTopicId) ||
    graph.nodes.find((node) => node.type === "ResearchTopic");
  const activeThread = activeThreadId
    ? graph.nodes.find(
        (node) =>
          node.id === activeThreadId ||
          node.id ===
            (activeThreadId.startsWith("codex://")
              ? activeThreadId
              : `codex://thread/${activeThreadId}`),
      )
    : null;
  const activeSession = activeThreadId
    ? sessionRecords.find(
        (session) =>
          session.thread_id === activeThreadId ||
          `codex://thread/${session.thread_id}` === activeThreadId,
      )
    : null;
  const codeBoardFileCount = useMemo(() => {
    const visibleFiles = nodesWithPositions.filter((node) =>
      ["CodeFileGroup", "CodeFileContainer"].includes(node.type),
    );
    if (visibleFiles.length) return visibleFiles.length;
    const fileIds = new Set(
      nodesWithPositions
        .filter((node) => node.type === "CodeDirectoryGroup")
        .flatMap((node) => node.children || [])
        .filter((child) => child.type === "CodeFileGroup")
        .map((child) => child.id),
    );
    return fileIds.size;
  }, [nodesWithPositions]);
  const codeBoardEntityCount = useMemo(
    () =>
      nodesWithPositions.reduce((total, node) => {
        if (node.type === "CodeResponsibilityGroup") {
          const memberIds = node.metadata?.member_ids;
          return total + (Array.isArray(memberIds) ? memberIds.length : 0);
        }
        return (
          total +
          ([
            "CodeClass",
            "CodeMethod",
            "CodeFunction",
            "CodeInterface",
            "CodeModule",
            "CodeSymbol",
          ].includes(node.type)
            ? 1
            : 0)
        );
      }, 0),
    [nodesWithPositions],
  );
  const boardTitle =
    board === "code"
      ? requestedCodeView === "vector" &&
        !requestedCodeEntity &&
        !requestedCodeQuery
        ? "代码向量网络"
        : requestedCodeEntity
          ? nodesWithPositions.find((node) => node.id === requestedCodeEntity)
              ?.label || "代码关系"
          : requestedCodeQuery
            ? `“${requestedCodeQuery}”`
            : activeCodePath
              ? activeCodePath.split("/").at(-1) || activeCodePath
              : "代码白板"
      : board === "codex"
        ? activeThread?.label || activeSession?.title || "会话影响总览"
        : board === "topic"
          ? activeTopic?.label || "主题证据"
          : dashboardQuery.data?.project.name || "研究总图";
  const boardSubtitle =
    board === "code"
      ? requestedCodeView === "vector"
        ? `${filteredNodes.length} 个代码节点 · ${filteredEdges.length} 条静态与语义关系`
        : requestedCodeEntity
          ? `${codeGraphQuery.data?.metadata.direction_counts?.incoming || 0} 条被调用/引用 · ${codeGraphQuery.data?.metadata.direction_counts?.outgoing || 0} 条向外关系`
          : requestedCodeQuery
            ? `${codeGraphQuery.data?.metadata.visible_nodes || 0} 个语义匹配节点 · ${collaborationStats?.visibleFiles || 0} 个关联文件`
            : activeCodePath
              ? `${codeBoardEntityCount} 个代码实体 · ${nodesWithPositions.filter((node) => node.type === "CodeResponsibilityGroup").length} 个职责域 · ${nodesWithPositions.filter((node) => node.domain !== "code").length} 个关联证据`
              : collaborationStats?.mode === "modules"
                ? `${
                    collaborationStats.visibleModules ===
                    collaborationStats.modules
                      ? collaborationStats.modules
                      : `${collaborationStats.visibleModules} / ${collaborationStats.modules}`
                  } 模块 · ${collaborationStats.files} 文件 · ${boardGraph.edges.reduce((total, edge) => total + (edge.occurrences || 1), 0)} 组依赖`
                : `${collaborationStats?.repositories || 0} 个仓库 · ${collaborationStats?.visibleFiles || codeBoardFileCount} 个关联文件 / ${collaborationStats?.files || codeBoardFileCount} 个载入文件 · ${boardGraph.edges.filter((edge) => !edge.structural).length} 组跨文件关系`
      : board === "codex"
        ? activeThreadId
          ? `${nodesWithPositions.filter((node) => node.type === "CodexTurn").length} 个轮次 · ${nodesWithPositions.filter((node) => node.domain === "code").length} 个关联文件`
          : `${filteredSessions.length} 个会话 · ${filteredSessions.reduce((total, session) => total + session.file_change_count, 0)} 个文件变更 · ${filteredSessions.reduce((total, session) => total + session.command_count, 0)} 条命令`
        : board === "topic"
          ? `${nodesWithPositions.length} 个主题节点 · ${crossSourceRelations} 条跨源证据`
          : `${nodesWithPositions.filter((node) => !node.type.endsWith("Frame")).length} 个可审阅节点 · ${impactMappings.length} 组会话—代码映射 · ${crossSourceRelations} 条跨源证据`;

  if (!dashboardQuery.data) {
    return <div className="page-loading">正在读取项目关系数据…</div>;
  }

  return (
    <div
      className={`map-page research-review-workbench ${selected ? "has-inspector" : ""}`}
    >
      <section
        className={`map-page__main ${
          board === "code" &&
          requestedCodeView === "branch" &&
          (requestedCodeEntity || collaborationStats)
            ? "has-relation-toolbar"
            : ""
        }`}
      >
        <header className="map-commandbar">
          <div className="map-commandbar__title">
            {board === "code" &&
            (activeCodePath || requestedCodeEntity || requestedCodeQuery) ? (
              <button
                className="map-commandbar__back"
                onClick={() => {
                  setSelectedId(null);
                  if (requestedCodeEntity) {
                    updateCodeLocation({
                      entity: null,
                      relations: [],
                      direction: "both",
                    });
                  } else if (requestedCodeQuery) {
                    updateCodeLocation({
                      query: null,
                      entity: null,
                      relations: [],
                      direction: "both",
                    });
                    setSearchText("");
                  } else {
                    updateCodeLocation({
                      codePath: null,
                      repository: null,
                      entity: null,
                      codeBoard: true,
                      relations: [],
                      direction: "both",
                    });
                  }
                }}
                aria-label={
                  requestedCodeEntity && requestedCodeQuery
                    ? "返回语义检索结果"
                    : "返回仓库代码白板"
                }
                title={
                  requestedCodeEntity && requestedCodeQuery
                    ? "返回语义检索结果"
                    : "返回仓库代码白板"
                }
              >
                <ArrowLeft size={18} />
              </button>
            ) : null}
            {board === "code" ? (
              <Braces size={20} />
            ) : board === "codex" ? (
              <MessageSquareText size={20} />
            ) : (
              <Network size={20} />
            )}
            <div>
              <h1 title={boardTitle}>{boardTitle}</h1>
              <span title={boardSubtitle}>{boardSubtitle}</span>
            </div>
          </div>
          <nav className="map-source-tabs map-board-tabs" aria-label="图谱层级">
            <button
              className={board === "overview" ? "is-active" : ""}
              onClick={() => {
                updateCodeLocation({
                  board: "overview",
                  topic: null,
                  thread: null,
                  clearCodeState: true,
                });
                setBoard("overview");
              }}
            >
              <Network size={15} />
              研究总图
              <b>
                {board === "overview"
                  ? nodesWithPositions.filter(
                      (node) => !node.type.endsWith("Frame"),
                    ).length
                  : graph.nodes.length}
              </b>
            </button>
            {(sourceCounts.get("workspace") || 0) > 1 ? (
              <button
                className={board === "topic" ? "is-active" : ""}
                onClick={() => {
                  const topicId = activeTopicId || activeTopic?.id || null;
                  updateCodeLocation({
                    board: "topic",
                    topic: topicId,
                    thread: null,
                    clearCodeState: true,
                  });
                  setActiveTopicId(topicId);
                  setBoard("topic");
                }}
              >
                <Network size={15} />
                主题证据
                <b>
                  {
                    graph.nodes.filter((node) => node.type === "ResearchTopic")
                      .length
                  }
                </b>
              </button>
            ) : null}
            {(sessionRecords.length || sourceCounts.get("codex") || 0) > 0 ? (
              <button
                className={`domain-codex ${board === "codex" ? "is-active" : ""}`}
                onClick={() => {
                  updateCodeLocation({
                    board: "codex",
                    topic: null,
                    thread: null,
                    clearCodeState: true,
                  });
                  setActiveThreadId(null);
                  setBoard("codex");
                }}
              >
                <MessageSquareText size={15} />
                会话影响
                <b>{sessionRecords.length}</b>
              </button>
            ) : null}
            {(sourceCounts.get("code") || 0) > 0 ? (
              <button
                className={`domain-code ${board === "code" ? "is-active" : ""}`}
                onClick={() => {
                  updateCodeLocation({
                    board: "code",
                    topic: null,
                    thread: null,
                    codePath: null,
                    repository: null,
                    entity: null,
                    query: null,
                    relations: [],
                    direction: "both",
                    view: "branch",
                    fileLayout: "auto",
                    expandedFiles: [],
                  });
                  setActiveCodePath(null);
                  setBoard("code");
                }}
              >
                <Braces size={15} />
                代码白板
                <b>
                  {board === "code" && collaborationStats
                    ? collaborationStats.files
                    : sourceCounts.get("code")}
                </b>
              </button>
            ) : null}
          </nav>
          <div className="map-control-stack">
            {board === "code" ? (
              <div
                className="code-graph-view-switch"
                role="group"
                aria-label="代码图谱组织形式"
              >
                <button
                  className={requestedCodeView === "branch" ? "is-active" : ""}
                  onClick={() => updateCodeLocation({ view: "branch" })}
                  aria-pressed={requestedCodeView === "branch"}
                >
                  <GitFork size={15} />
                  结构分支
                </button>
                <button
                  className={requestedCodeView === "vector" ? "is-active" : ""}
                  onClick={() => updateCodeLocation({ view: "vector" })}
                  aria-pressed={requestedCodeView === "vector"}
                >
                  <Orbit size={15} />
                  向量关系
                </button>
              </div>
            ) : null}
            <div className="map-view-controls">
              <button
                onClick={() => {
                  if (board === "code" && requestedCodeView === "vector") {
                    vectorGraphRef.current?.zoomOut();
                  } else {
                    setViewport((value) => ({
                      ...value,
                      scale: Math.max(0.08, value.scale / 1.18),
                    }));
                  }
                }}
                aria-label="缩小"
                title="缩小"
              >
                <Minus size={17} />
              </button>
              <span>
                {board === "code" && requestedCodeView === "vector"
                  ? "向量"
                  : `${Math.round(viewport.scale * 100)}%`}
              </span>
              <button
                onClick={() => {
                  if (board === "code" && requestedCodeView === "vector") {
                    vectorGraphRef.current?.zoomIn();
                  } else {
                    setViewport((value) => ({
                      ...value,
                      scale: Math.min(2.8, value.scale * 1.18),
                    }));
                  }
                }}
                aria-label="放大"
                title="放大"
              >
                <Plus size={17} />
              </button>
              <button
                onClick={() => {
                  if (board === "code" && requestedCodeView === "vector") {
                    vectorGraphRef.current?.fit();
                  } else {
                    fitGraph();
                  }
                }}
                aria-label="适应全部节点"
                title="适应全部节点"
              >
                <Maximize2 size={17} />
              </button>
              <button
                onClick={() => {
                  if (board === "code" && requestedCodeView === "vector") {
                    vectorGraphRef.current?.reset();
                  } else {
                    resetLayout();
                  }
                }}
                aria-label="重置图谱布局"
                title="重置图谱布局"
              >
                <LocateFixed size={17} />
              </button>
            </div>
          </div>
        </header>

        <div className="map-data-toolbar">
          <div className="map-node-search">
            <Search size={16} />
            <input
              value={searchText}
              onChange={(event) => {
                setSearchText(event.target.value);
                setSearchMenuOpen(true);
              }}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                const query = searchText.trim();
                if (board === "code" && query) {
                  setSelectedId(null);
                  setActiveCodePath(null);
                  setSearchMenuOpen(false);
                  updateCodeLocation({
                    entity: null,
                    query,
                    clearPath: true,
                  });
                }
              }}
              placeholder={
                board === "code"
                  ? "搜索函数、类、文件或代码语义，回车检索全部"
                  : board === "codex"
                    ? "查找会话、轮次或目标"
                    : board === "topic"
                      ? "查找主题证据"
                      : "查找顶层研究节点"
              }
              aria-label="查找当前白板节点"
            />
            {searchText ? (
              <button
                onClick={() => {
                  setSearchText("");
                  setSearchMenuOpen(false);
                }}
                aria-label="清空查找"
              >
                <X size={15} />
              </button>
            ) : null}
            {searchMenuOpen && searchText ? (
              <div className="map-search-results">
                {searchMatches.length ? (
                  searchMatches.map((node) => {
                    const Icon = domainIcons[node.domain];
                    return (
                      <button key={node.id} onClick={() => activateNode(node)}>
                        <span className={`domain-${node.domain}`}>
                          <Icon size={16} />
                        </span>
                        <span>
                          <strong>{node.label}</strong>
                          <small>
                            {domainLabel(node.domain)} ·{" "}
                            {entityTypeLabel(node.type)}
                          </small>
                        </span>
                        <ChevronRight size={15} />
                      </button>
                    );
                  })
                ) : (
                  <p>当前白板没有直接匹配</p>
                )}
                {board === "code" && searchText.trim() ? (
                  <button
                    className="map-search-results__semantic"
                    onClick={() => {
                      setSelectedId(null);
                      setActiveCodePath(null);
                      setSearchMenuOpen(false);
                      updateCodeLocation({
                        entity: null,
                        query: searchText.trim(),
                        clearPath: true,
                      });
                    }}
                  >
                    <span className="domain-query">
                      <Search size={16} />
                    </span>
                    <span>
                      <strong>检索全部已向量化代码</strong>
                      <small>文件内容、类、方法、函数与限定名</small>
                    </span>
                    <ChevronRight size={15} />
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
          <label className="map-load-control">
            <span>节点载入量</span>
            <input
              type="range"
              min={1}
              max={totalNodeCeiling}
              value={Math.min(loadTarget, totalNodeCeiling)}
              onChange={(event) => setLoadTarget(Number(event.target.value))}
              onPointerUp={(event) => {
                const value = Number(event.currentTarget.value);
                setLoadTarget(value);
                setCommittedLimit(value);
              }}
              onKeyUp={(event) => {
                const value = Number(event.currentTarget.value);
                setLoadTarget(value);
                setCommittedLimit(value);
              }}
              onBlur={(event) => {
                const value = Number(event.currentTarget.value);
                setLoadTarget(value);
                setCommittedLimit(value);
              }}
              aria-label="节点载入量"
            />
            <b>
              {Math.min(loadTarget, totalNodeCeiling).toLocaleString()} /{" "}
              {activeGraphQuery.data ? totalNodes.toLocaleString() : "…"}
            </b>
          </label>
          <div
            className="map-data-state"
            aria-live="polite"
            data-query-status={activeGraphQuery.status}
            data-fetch-status={activeGraphQuery.fetchStatus}
            data-api-nodes={activeGraphQuery.data?.nodes.length ?? -1}
          >
            {missingRequestedFocus ? (
              <span className="is-error">
                指定节点不在当前载入范围，请调整来源或载入量后重试
              </span>
            ) : activeGraphQuery.error ? (
              <span className="is-error">
                {activeGraphQuery.error instanceof Error
                  ? activeGraphQuery.error.message
                  : "图谱读取失败"}
              </span>
            ) : activeGraphQuery.isFetching ? (
              <span className="is-loading">正在载入真实关系</span>
            ) : (
              <>
                <CheckCircle2 size={16} />
                <span>
                  {activeGraphQuery.data?.metadata.sampled
                    ? "已按当前载入量采样"
                    : "已载入全部节点"}
                </span>
              </>
            )}
          </div>
        </div>

        {board === "code" &&
        requestedCodeView === "branch" &&
        (requestedCodeEntity || collaborationStats) ? (
          <div className="map-relation-toolbar" aria-label="代码关系筛选">
            <div className="map-relation-toolbar__summary">
              <Network size={16} />
              <strong>{requestedCodeEntity ? "关系分支" : "多文件协作"}</strong>
              <span>
                {requestedCodeEntity
                  ? "以当前代码实体为中心筛选真实静态分析边"
                  : `${collaborationStats?.repositories || 0} 个仓库 · ${collaborationStats?.visibleFiles || 0} 个有关联文件 / ${collaborationStats?.files || 0} 个载入文件 · ${collaborationStats?.entities || 0} 个实体 · ${collaborationStats?.crossFileRelations || 0} 个跨文件静态关系`}
              </span>
            </div>
            <div
              className="map-relation-toolbar__types"
              role="group"
              aria-label="关系类型"
            >
              <button
                className={!requestedRelations.length ? "is-active" : ""}
                onClick={() => updateCodeLocation({ relations: [] })}
              >
                全部
                <b>
                  {requestedCodeEntity
                    ? Object.values(
                        codeGraphQuery.data?.metadata.relation_counts || {},
                      ).reduce((total, count) => total + count, 0)
                    : boardGraph.edges
                        .filter((edge) => !edge.structural)
                        .reduce(
                          (total, edge) => total + (edge.occurrences || 1),
                          0,
                        )}
                </b>
              </button>
              {codeRelationOptions
                .filter((option) =>
                  requestedCodeEntity
                    ? option.value !== "SEMANTIC_SIMILAR"
                    : option.value !== "DEFINES",
                )
                .map((option) => {
                  const count = requestedCodeEntity
                    ? codeGraphQuery.data?.metadata.relation_counts?.[
                        option.value
                      ] || 0
                    : boardGraph.edges
                        .filter((edge) => edge.predicate === option.value)
                        .reduce(
                          (total, edge) => total + (edge.occurrences || 1),
                          0,
                        );
                  const active = requestedRelations.includes(option.value);
                  return (
                    <button
                      key={option.value}
                      className={active ? "is-active" : ""}
                      onClick={() => {
                        const next = requestedRelations.length
                          ? requestedRelations.includes(option.value)
                            ? requestedRelations.filter(
                                (item) => item !== option.value,
                              )
                            : [...requestedRelations, option.value]
                          : [option.value];
                        updateCodeLocation({
                          relations: next,
                        });
                      }}
                    >
                      {option.label}
                      <b>{count}</b>
                    </button>
                  );
                })}
            </div>
            {requestedCodeEntity ||
            collaborationStats?.mode === "files" ||
            (collaborationStats?.mode === "modules" &&
              (requestedModuleScope === "all" ||
                collaborationStats.modules >
                  collaborationStats.visibleModules)) ? (
              <div
                className="map-relation-toolbar__direction"
                role="group"
                aria-label="关系方向"
              >
                {requestedCodeEntity ? (
                  (
                    [
                      ["both", "双向"],
                      ["incoming", "被调用 / 被引用"],
                      ["outgoing", "向外调用 / 引用"],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      className={
                        requestedDirection === value ? "is-active" : ""
                      }
                      onClick={() => updateCodeLocation({ direction: value })}
                    >
                      {label}
                    </button>
                  ))
                ) : collaborationStats?.mode === "files" ? (
                  <>
                    <button
                      className={
                        requestedFileLayout === "auto" ? "is-active" : ""
                      }
                      onClick={() => setExpandedFiles([], "auto")}
                    >
                      展开关键文件
                      <b>{collaborationStats?.expandedFiles || 0}</b>
                    </button>
                    <button
                      className={
                        requestedFileLayout === "manual" &&
                        !collaborationStats?.expandedFiles
                          ? "is-active"
                          : ""
                      }
                      onClick={() => setExpandedFiles([], "manual")}
                    >
                      全部收起
                    </button>
                  </>
                ) : collaborationStats?.mode === "modules" ? (
                  <button
                    className={
                      requestedModuleScope === "core" ? "is-active" : ""
                    }
                    onClick={() =>
                      updateCodeLocation({
                        moduleScope:
                          requestedModuleScope === "core" ? "all" : "core",
                      })
                    }
                  >
                    {requestedModuleScope === "core"
                      ? `显示全部模块 ${collaborationStats.modules}`
                      : `聚焦核心模块 ${Math.min(14, collaborationStats.modules)}`}
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        {board === "code" && requestedCodeView === "vector" ? (
          <CodeVectorGraph
            ref={vectorGraphRef}
            nodes={filteredNodes}
            edges={filteredEdges}
            focusId={
              requestedCodeEntity ||
              filteredNodes.find((node) => node.type === "SemanticQuery")?.id ||
              null
            }
            selectedId={selectedId}
            onSelect={selectNode}
            onActivate={drillNode}
            onClearSelection={clearSelection}
          />
        ) : (
          <div
            ref={canvasRef}
            className="research-canvas"
            data-testid="research-canvas"
            onWheel={(event) => {
              event.preventDefault();
              const bounds = event.currentTarget.getBoundingClientRect();
              const cursorX = event.clientX - bounds.left;
              const cursorY = event.clientY - bounds.top;
              setViewport((value) => {
                const nextScale = Math.min(
                  2.8,
                  Math.max(
                    0.08,
                    value.scale * Math.exp(-event.deltaY * 0.0014),
                  ),
                );
                const ratio = nextScale / value.scale;
                return {
                  scale: nextScale,
                  x: cursorX - (cursorX - value.x) * ratio,
                  y: cursorY - (cursorY - value.y) * ratio,
                };
              });
            }}
            onPointerDown={(event) => {
              if (event.button !== 0) return;
              const target = event.target as Element;
              if (target.closest(".map-node, .map-edge-legend")) return;
              pan.current = {
                pointerId: event.pointerId,
                x: event.clientX,
                y: event.clientY,
                originX: viewport.x,
                originY: viewport.y,
              };
              event.currentTarget.dataset.panning = "true";
              event.currentTarget.setPointerCapture(event.pointerId);
            }}
            onPointerMove={(event) => {
              const currentPan = pan.current;
              if (!currentPan || currentPan.pointerId !== event.pointerId)
                return;
              setViewport((value) => ({
                ...value,
                x: currentPan.originX + event.clientX - currentPan.x,
                y: currentPan.originY + event.clientY - currentPan.y,
              }));
            }}
            onPointerUp={(event) => {
              if (pan.current?.pointerId !== event.pointerId) return;
              pan.current = null;
              delete event.currentTarget.dataset.panning;
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                event.currentTarget.releasePointerCapture(event.pointerId);
              }
            }}
            onPointerCancel={(event) => {
              pan.current = null;
              delete event.currentTarget.dataset.panning;
            }}
          >
            {board === "codex" && !activeThreadId ? (
              <div
                className="map-session-overview-toolbar"
                onPointerDown={(event) => event.stopPropagation()}
              >
                <div className="map-session-overview-toolbar__summary">
                  <MessageSquareText size={19} />
                  <span>
                    <strong>{sessionRecords.length}</strong>
                    全部会话
                  </span>
                  <span>
                    <strong>
                      {sessionRecords.reduce(
                        (total, session) => total + session.file_change_count,
                        0,
                      )}
                    </strong>
                    文件变更
                  </span>
                </div>
                <label className="map-session-overview-toolbar__search">
                  <Search size={15} />
                  <input
                    value={sessionOverviewQuery}
                    onChange={(event) => {
                      setSessionOverviewQuery(event.target.value);
                      setSessionOverviewPage(0);
                    }}
                    placeholder="筛选会话、目录或状态"
                    aria-label="筛选会话影响"
                  />
                </label>
                <select
                  aria-label="选择会话"
                  value=""
                  onChange={(event) => openSessionImpact(event.target.value)}
                  disabled={!filteredSessions.length}
                >
                  <option value="">
                    {sessionsQuery.isLoading
                      ? "正在读取会话…"
                      : "选择会话查看影响"}
                  </option>
                  {filteredSessions.map((session) => (
                    <option key={session.thread_id} value={session.thread_id}>
                      {session.title || session.thread_id} ·{" "}
                      {session.file_change_count} 个文件变更
                    </option>
                  ))}
                </select>
                <div className="map-session-overview-toolbar__pager">
                  <button
                    type="button"
                    aria-label="上一页会话"
                    disabled={sessionOverviewPage <= 0}
                    onClick={() =>
                      setSessionOverviewPage((current) =>
                        Math.max(0, current - 1),
                      )
                    }
                  >
                    <ChevronLeft size={15} />
                  </button>
                  <span>
                    {sessionOverviewPage + 1} / {sessionOverviewPageCount}
                  </span>
                  <button
                    type="button"
                    aria-label="下一页会话"
                    disabled={
                      sessionOverviewPage >= sessionOverviewPageCount - 1
                    }
                    onClick={() =>
                      setSessionOverviewPage((current) =>
                        Math.min(sessionOverviewPageCount - 1, current + 1),
                      )
                    }
                  >
                    <ChevronRight size={15} />
                  </button>
                </div>
              </div>
            ) : null}
            {board === "overview" && impactMappings.length ? (
              <aside
                className={`map-impact-bridge${impactPanelExpanded ? "" : " is-collapsed"}`}
                aria-label="会话与代码影响映射"
                onPointerDown={(event) => event.stopPropagation()}
              >
                <header>
                  <button
                    type="button"
                    aria-expanded={impactPanelExpanded}
                    aria-label={
                      impactPanelExpanded
                        ? "收起会话代码影响映射"
                        : "展开会话代码影响映射"
                    }
                    onClick={() =>
                      setImpactPanelExpanded((expanded) => !expanded)
                    }
                  >
                    <span>
                      <GitFork size={15} />
                      会话 → 代码影响映射
                    </span>
                    <b>{impactMappings.length}</b>
                    {impactPanelExpanded ? (
                      <ChevronRight size={14} />
                    ) : (
                      <ChevronLeft size={14} />
                    )}
                  </button>
                </header>
                {impactPanelExpanded ? (
                  <div>
                    {impactMappings.map(({ edge, source, target }) => {
                      const sessionNode =
                        source.domain === "codex" ? source : target;
                      const codeNode =
                        source.domain === "code" ? source : target;
                      return (
                        <button
                          type="button"
                          key={edge.id}
                          onClick={() => selectNode(sessionNode)}
                          aria-label={`${sessionNode.label} 影响 ${codeNode.path || codeNode.label}`}
                        >
                          <span title={sessionNode.label}>
                            {sessionNode.label}
                          </span>
                          <small>
                            {edge.label}
                            {(edge.occurrences || 1) > 1
                              ? ` × ${edge.occurrences}`
                              : ""}
                          </small>
                          <strong title={codeNode.path || codeNode.label}>
                            {codeNode.path || codeNode.label}
                          </strong>
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </aside>
            ) : null}
            <div
              className="research-world"
              style={{
                transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`,
              }}
            >
              <svg
                className="research-edge-layer"
                width="1"
                height="1"
                aria-hidden="true"
              >
                <defs>
                  {sourceOrder.concat("query").map((domain) => (
                    <marker
                      key={domain}
                      id={`map-arrow-${domain}`}
                      className={`domain-${domain}`}
                      markerWidth="8"
                      markerHeight="8"
                      refX="7"
                      refY="4"
                      orient="auto"
                    >
                      <path d="M0,0 L0,8 L8,4 z" />
                    </marker>
                  ))}
                  {codeRelationOptions.map((relation) => (
                    <marker
                      key={relation.value}
                      id={`map-arrow-relation-${relation.value.toLowerCase()}`}
                      className={`relation-${relation.value.toLowerCase()}`}
                      markerWidth="8"
                      markerHeight="8"
                      refX="7"
                      refY="4"
                      orient="auto"
                    >
                      <path d="M0,0 L0,8 L8,4 z" />
                    </marker>
                  ))}
                </defs>
                {visibleEdges.map((edge) => {
                  const source = nodesById.get(edge.source);
                  const target = nodesById.get(edge.target);
                  if (!source || !target) return null;
                  const selectedEdge =
                    selectedId === edge.source || selectedId === edge.target;
                  const focusedEdge =
                    !focusedNodeIds ||
                    (focusedNodeIds.has(edge.source) &&
                      focusedNodeIds.has(edge.target));
                  const showLabel =
                    selectedEdge ||
                    (!isCodeCollaboration &&
                      viewport.scale >= (board === "overview" ? 0.86 : 0.62) &&
                      visibleEdges.length <= (board === "overview" ? 14 : 30) &&
                      (!edge.structural || edge.scope));
                  return (
                    <g
                      key={edge.id}
                      data-source={edge.source}
                      data-target={edge.target}
                      className={[
                        selectedEdge ? "is-selected" : "",
                        !focusedEdge ? "is-muted" : "",
                        edge.structural ? "is-structural" : "",
                        edge.scope ? "is-scope" : "",
                        `predicate-${edge.predicate.toLowerCase().replaceAll("_", "-")}`,
                      ]
                        .filter(Boolean)
                        .join(" ")}
                    >
                      <path
                        className={`research-edge domain-${edge.tone} status-${edge.review_status || "confirmed"}`}
                        d={edgePath(source, target, edge)}
                        markerEnd={
                          codeRelationOptions.some(
                            (item) => item.value === edge.predicate,
                          )
                            ? `url(#map-arrow-relation-${edge.predicate.toLowerCase()})`
                            : `url(#map-arrow-${edge.tone})`
                        }
                      />
                      {showLabel ? (
                        <text
                          className={`research-edge-label domain-${edge.tone}`}
                          x={(source.x + target.x) / 2}
                          y={(source.y + target.y) / 2 - 7}
                          textAnchor="middle"
                        >
                          {edge.label}
                          {(edge.occurrences || 1) > 1
                            ? ` × ${edge.occurrences}`
                            : ""}
                        </text>
                      ) : null}
                    </g>
                  );
                })}
              </svg>
              {visibleNodes.map((node) => {
                const Icon = iconForNode(node);
                const degree = degreeById.get(node.id) || 0;
                const isFocused =
                  !focusedNodeIds || focusedNodeIds.has(node.id);
                const isCodeNode = node.domain === "code";
                const isGroupFrame = [
                  "ResearchDomainFrame",
                  "SessionImpactFrame",
                  "CodeModuleFrame",
                ].includes(node.type);
                const cardDescription =
                  isGroupFrame || !isCodeNode
                    ? node.meta
                    : describeCodeNode(node);
                const layerLabel = isCodeNode
                  ? codeLayerLabel(node.type)
                  : domainLabel(node.domain);
                // Responsibility cards explain the domain and expose one
                // representative entry. The inspector owns the complete list,
                // preventing invisible/clipped rows inside a fixed-height card.
                const childPreviewLimit =
                  node.type === "CodeResponsibilityGroup" ? 1 : 2;
                return (
                  <button
                    key={node.id}
                    className={[
                      "map-node",
                      `domain-${node.domain}`,
                      `type-${node.type.toLowerCase()}`,
                      node.id === selectedId ? "is-selected" : "",
                      degree >= 4 ||
                      node.type === "ResearchProject" ||
                      node.type === "CodeRepositoryBoard" ||
                      node.type === "CodexWorkspaceBoard"
                        ? "is-hub"
                        : "",
                      node.children?.length ? "is-cluster" : "",
                      node.type === "CodeDirectoryGroup" ? "is-directory" : "",
                      node.type === "CodeFileContainer" ? "is-compound" : "",
                      node.type === "CodeResponsibilityGroup"
                        ? "is-capability"
                        : "",
                      isGroupFrame ? "is-group-frame" : "",
                      node.expanded ? "is-expanded" : "",
                      node.parentId ? "is-nested" : "",
                      selectedId && isFocused && node.id !== selectedId
                        ? "is-neighbor"
                        : "",
                      !isFocused ? "is-muted" : "",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    style={{
                      left: node.x,
                      top: node.y,
                      ...(node.width ? { width: node.width } : {}),
                      ...(node.height ? { height: node.height } : {}),
                    }}
                    aria-label={`${layerLabel} ${node.label}`}
                    title={`${layerLabel} · ${entityTypeLabel(node.type)}\n${cardDescription}\n单击查看详情${drillLabelForNode(node) ? "，双击进入下一级" : ""}`}
                    data-node-id={node.id}
                    data-degree={degree}
                    data-drillable={Boolean(drillLabelForNode(node))}
                    onPointerDown={(event) => {
                      if (event.button !== 0) return;
                      event.stopPropagation();
                      nodeDrag.current = {
                        pointerId: event.pointerId,
                        id: node.id,
                        x: event.clientX,
                        y: event.clientY,
                        originX: node.x,
                        originY: node.y,
                        moved: false,
                        groupOrigins:
                          node.type === "CodeFileContainer" || isGroupFrame
                            ? Object.fromEntries(
                                filteredNodes
                                  .filter(
                                    (candidate) =>
                                      candidate.parentId === node.id ||
                                      candidate.groupKey === node.id,
                                  )
                                  .map((candidate) => [
                                    candidate.id,
                                    { x: candidate.x, y: candidate.y },
                                  ]),
                              )
                            : undefined,
                      };
                      event.currentTarget.dataset.dragging = "true";
                      event.currentTarget.setPointerCapture(event.pointerId);
                    }}
                    onPointerMove={(event) => {
                      const current = nodeDrag.current;
                      if (
                        !current ||
                        current.pointerId !== event.pointerId ||
                        current.id !== node.id
                      )
                        return;
                      const dx = (event.clientX - current.x) / viewport.scale;
                      const dy = (event.clientY - current.y) / viewport.scale;
                      if (Math.abs(dx) + Math.abs(dy) > 5) current.moved = true;
                      if (current.moved) {
                        manuallyPositioned.current.add(node.id);
                        const parent = node.parentId
                          ? nodesById.get(node.parentId)
                          : null;
                        const nodeSize = nodeDimensions(node);
                        const parentSize = parent
                          ? nodeDimensions(parent)
                          : null;
                        const proposed = {
                          x: current.originX + dx,
                          y: current.originY + dy,
                        };
                        const next =
                          parent && parentSize
                            ? {
                                x: Math.min(
                                  parent.x +
                                    parentSize.width / 2 -
                                    nodeSize.width / 2 -
                                    10,
                                  Math.max(
                                    parent.x -
                                      parentSize.width / 2 +
                                      nodeSize.width / 2 +
                                      10,
                                    proposed.x,
                                  ),
                                ),
                                y: Math.min(
                                  parent.y +
                                    parentSize.height / 2 -
                                    nodeSize.height / 2 -
                                    16,
                                  Math.max(
                                    parent.y -
                                      parentSize.height / 2 +
                                      nodeSize.height / 2 +
                                      88,
                                    proposed.y,
                                  ),
                                ),
                              }
                            : proposed;
                        setPositions((values) => ({
                          ...values,
                          [node.id]: next,
                          ...(!node.parentId
                            ? Object.fromEntries(
                                Object.entries(current.groupOrigins || {}).map(
                                  ([id, origin]) => [
                                    id,
                                    { x: origin.x + dx, y: origin.y + dy },
                                  ],
                                ),
                              )
                            : {}),
                        }));
                      }
                    }}
                    onPointerUp={(event) => {
                      const current = nodeDrag.current;
                      if (
                        !current ||
                        current.pointerId !== event.pointerId ||
                        current.id !== node.id
                      )
                        return;
                      nodeDrag.current = null;
                      delete event.currentTarget.dataset.dragging;
                      if (
                        event.currentTarget.hasPointerCapture(event.pointerId)
                      ) {
                        event.currentTarget.releasePointerCapture(
                          event.pointerId,
                        );
                      }
                      if (!current.moved) activateNode(node);
                    }}
                    onPointerCancel={(event) => {
                      nodeDrag.current = null;
                      delete event.currentTarget.dataset.dragging;
                    }}
                    onClick={(event) => {
                      if (event.detail === 0) activateNode(node);
                    }}
                    onDoubleClick={(event) => {
                      event.stopPropagation();
                      if (drillLabelForNode(node)) drillNode(node);
                    }}
                  >
                    {isGroupFrame ? (
                      <span className="map-node__frame-heading">
                        <span>
                          <Icon size={16} />
                          {layerLabel}分组
                        </span>
                        <strong>{node.label}</strong>
                        <em>{cardDescription}</em>
                      </span>
                    ) : (
                      <>
                        <span className="map-node__icon">
                          <Icon size={18} />
                        </span>
                        <span className="map-node__body">
                          <small>
                            <span>{layerLabel}</span>
                            {entityTypeLabel(node.type)}
                          </small>
                          <strong>{node.label}</strong>
                          <em>{cardDescription}</em>
                        </span>
                        {node.children?.length && !node.expanded ? (
                          <span className="map-node__children">
                            {node.children
                              .slice(0, childPreviewLimit)
                              .map((child) => {
                                const ChildIcon = iconForType(child.type);
                                return (
                                  <span key={child.id}>
                                    <ChildIcon size={12} />
                                    <b>{child.label}</b>
                                  </span>
                                );
                              })}
                            {node.children.length > childPreviewLimit ? (
                              <em>
                                +{node.children.length - childPreviewLimit}
                              </em>
                            ) : null}
                          </span>
                        ) : null}
                        <span className="map-node__foot">
                          {node.status ? (
                            <Status value={node.status}>
                              {statusLabel(node.status)}
                            </Status>
                          ) : (
                            <span>
                              {isCodeNode
                                ? codeNodeFootLabel(node)
                                : domainLabel(node.domain)}
                            </span>
                          )}
                          {node.version ? (
                            <code>{shortVersion(node.version)}</code>
                          ) : null}
                          <span className="map-node__action">
                            查看详情
                            <ChevronRight size={12} />
                          </span>
                        </span>
                      </>
                    )}
                  </button>
                );
              })}
            </div>
            <div className="map-edge-legend" aria-label="关系线说明">
              {board === "code" ? (
                <>
                  <span>
                    <i className="is-call" />
                    调用
                  </span>
                  <span>
                    <i className="is-reference" />
                    引用
                  </span>
                  <span>
                    <i className="is-import" />
                    导入
                  </span>
                  {isCodeCollaboration ? (
                    <span>
                      <i className="is-vector" />
                      向量相似
                    </span>
                  ) : null}
                  <span>
                    <i className="is-container" />
                    结构层级
                  </span>
                </>
              ) : (
                <>
                  <span>
                    <i className="is-confirmed" />
                    已确认
                  </span>
                  <span>
                    <i className="is-scope" />
                    项目范围
                  </span>
                  <span>
                    <i className="is-unreviewed" />
                    待复核
                  </span>
                  <span>
                    <i className="is-rejected" />
                    冲突 / 拒绝
                  </span>
                </>
              )}
            </div>
            {!filteredNodes.length && !activeGraphQuery.isFetching ? (
              <div className="map-empty">
                <Network size={28} />
                <strong>
                  {isCodeCollaboration
                    ? "当前载入范围没有可连成关系的代码文件"
                    : board === "topic"
                      ? "当前主题还没有可展示的关联证据"
                      : board === "codex"
                        ? "当前项目还没有已索引的研发会话"
                        : "该来源尚无已索引节点"}
                </strong>
                <span>
                  {isCodeCollaboration
                    ? "增加节点载入量、切换关系类型，或使用向量关系查看语义邻近节点。"
                    : "完成对应来源的接入或关联后，图谱会自动更新。"}
                </span>
              </div>
            ) : null}
          </div>
        )}
      </section>
      {selected && selectedReviewProfile ? (
        <ReviewRouteRail
          node={selected}
          profile={selectedReviewProfile}
          navigationTrail={visibleSelectionTrail}
          canGoBack={visibleSelectionTrail.length > 1}
          onBack={navigateBack}
          onSelect={activateNode}
          onTrailSelect={selectNode}
        />
      ) : null}
      {selected ? (
        <MapInspector
          node={selected}
          edges={boardGraph.edges}
          nodesById={nodesById}
          projectId={projectId}
          projectAclRef={dashboardQuery.data?.project.acl_ref}
          busy={neighborBusy === selected.id}
          neighborTotal={neighborPages[selected.id]?.total}
          hasMore={neighborPages[selected.id]?.hasMore}
          error={neighborError[selected.id]}
          drillLabel={drillLabelForNode(selected)}
          onClose={clearSelection}
          onSelect={activateNode}
          onExpand={expandSelected}
          onDrill={() => drillNode(selected)}
          onToggleFile={
            selected.type === "CodeFileContainer"
              ? () => toggleFileExpansion(selected)
              : undefined
          }
          onOpenChild={openChild}
          navigationTrail={visibleSelectionTrail}
          onTrailSelect={selectNode}
          canGoBack={visibleSelectionTrail.length > 1}
          onBack={navigateBack}
        />
      ) : null}
    </div>
  );
}
