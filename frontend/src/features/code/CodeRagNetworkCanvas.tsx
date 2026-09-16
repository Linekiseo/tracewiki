import {
  ArrowRight,
  GitBranch,
  LocateFixed,
  Move,
  Network,
  RotateCcw,
  Search,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  KeyboardEvent,
  PointerEvent as ReactPointerEvent,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  codeGraphLayoutPositions,
  CODE_GRAPH_NETWORK_FOOTPRINT_HEIGHT,
  CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH,
  CODE_GRAPH_NETWORK_NODE_CENTER_Y,
  CODE_GRAPH_NETWORK_NODE_RADIUS,
  CODE_GRAPH_NODE_HEIGHT,
  CODE_GRAPH_NODE_WIDTH,
  type CodeGraphLayoutMode,
  type CodeGraphPoint,
  type CodeGraphView,
  type CodeRagEdge,
  type CodeRagNode,
  zoomCodeGraphView,
} from "./codeRagGraph";

const CANVAS_WIDTH = 1280;
const CANVAS_HEIGHT = 820;
const FIT_VIEW: CodeGraphView = { x: 0, y: 0, scale: 1 };
const DRAG_THRESHOLD = 4;

export const CODE_GRAPH_TYPE_LABELS = {
  Repository: "仓库",
  Branch: "分支",
  WorktreeSnapshot: "工作树快照",
  Commit: "提交",
  FileVersion: "文件版本",
  CodeSymbol: "代码 Symbol",
  DiffHunk: "Diff Hunk",
  TestResult: "测试结果",
} as const;

export const CODE_GRAPH_RELATION_LABELS: Record<string, string> = {
  HAS_BRANCH: "包含分支",
  POINTS_TO: "指向提交",
  HAS_SNAPSHOT: "当前快照",
  HAS_COMMIT: "提交历史",
  PARENT: "父提交",
  CONTAINS: "包含",
  DEFINES: "定义",
  CALLS: "调用",
  IMPORTS: "导入",
  VALIDATED_BY: "测试验证",
};

type DragState =
  | {
      kind: "pan";
      pointerId: number;
      start: CodeGraphPoint;
      view: CodeGraphView;
      moved: boolean;
    }
  | {
      kind: "node";
      pointerId: number;
      nodeId: string;
      start: CodeGraphPoint;
      origin: CodeGraphPoint;
      moved: boolean;
    };

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

export function clientPointToCodeGraph(
  client: CodeGraphPoint,
  rect: Pick<DOMRect, "left" | "top" | "width" | "height">,
): CodeGraphPoint {
  return {
    x: ((client.x - rect.left) / Math.max(1, rect.width)) * CANVAS_WIDTH,
    y: ((client.y - rect.top) / Math.max(1, rect.height)) * CANVAS_HEIGHT,
  };
}

function graphPointToWorld(point: CodeGraphPoint, view: CodeGraphView) {
  return {
    x: (point.x - view.x) / view.scale,
    y: (point.y - view.y) / view.scale,
  };
}

function nodeSubtitle(node: CodeRagNode) {
  if (node.type === "Commit") return String(node.version || "").slice(0, 10);
  if (node.type === "Branch")
    return `${String(node.version || "").slice(0, 9)} · ref`;
  if (node.type === "CodeSymbol" && node.startLine) {
    return `L${node.startLine}${node.endLine ? `–${node.endLine}` : ""}`;
  }
  if (node.path) return node.path;
  if (node.version) return String(node.version).slice(0, 16);
  return node.origin === "projection" ? "权威投影" : "已载入图谱";
}

function shortText(value: string, length: number) {
  if (value.length <= length) return value;
  return `${value.slice(0, Math.max(1, length - 1))}…`;
}

function nodeGlyph(node: CodeRagNode) {
  if (node.type === "Repository") return "R";
  if (node.type === "Branch") return "B";
  if (node.type === "WorktreeSnapshot") return "W";
  if (node.type === "Commit") return "C";
  if (node.type === "FileVersion") return "F";
  if (node.type === "CodeSymbol") return "S";
  if (node.type === "DiffHunk") return "Δ";
  return "T";
}

function edgeGeometry(
  source: CodeGraphPoint,
  target: CodeGraphPoint,
  mode: CodeGraphLayoutMode,
) {
  const networkCenterX = CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH / 2;
  const sourceCenter = {
    x:
      source.x +
      (mode === "network" ? networkCenterX : CODE_GRAPH_NODE_WIDTH / 2),
    y:
      source.y +
      (mode === "network"
        ? CODE_GRAPH_NETWORK_NODE_CENTER_Y
        : CODE_GRAPH_NODE_HEIGHT / 2),
  };
  const targetCenter = {
    x:
      target.x +
      (mode === "network" ? networkCenterX : CODE_GRAPH_NODE_WIDTH / 2),
    y:
      target.y +
      (mode === "network"
        ? CODE_GRAPH_NETWORK_NODE_CENTER_Y
        : CODE_GRAPH_NODE_HEIGHT / 2),
  };
  const dx = targetCenter.x - sourceCenter.x;
  const dy = targetCenter.y - sourceCenter.y;
  const distance = Math.max(1, Math.hypot(dx, dy));
  const unitX = dx / distance;
  const unitY = dy / distance;
  const sourceScale =
    mode === "network"
      ? CODE_GRAPH_NETWORK_NODE_RADIUS
      : Math.min(
          Math.abs(CODE_GRAPH_NODE_WIDTH / 2 / (unitX || 0.0001)),
          Math.abs(CODE_GRAPH_NODE_HEIGHT / 2 / (unitY || 0.0001)),
        );
  const targetScale = sourceScale;
  const start = {
    x: sourceCenter.x + unitX * sourceScale,
    y: sourceCenter.y + unitY * sourceScale,
  };
  const end = {
    x: targetCenter.x - unitX * targetScale,
    y: targetCenter.y - unitY * targetScale,
  };
  const curve = clamp(distance * 0.055, 7, 24);
  const control = {
    x: (start.x + end.x) / 2 - unitY * curve,
    y: (start.y + end.y) / 2 + unitX * curve,
  };
  return {
    path: `M ${start.x} ${start.y} Q ${control.x} ${control.y} ${end.x} ${end.y}`,
    label: control,
  };
}

function topologyKey(
  nodes: readonly CodeRagNode[],
  edges: readonly CodeRagEdge[],
) {
  return `${nodes
    .map((node) => node.id)
    .sort()
    .join("|")}::${edges
    .map((edge) => `${edge.source}>${edge.predicate}>${edge.target}`)
    .sort()
    .join("|")}`;
}

export function CodeRagNetworkCanvas({
  repositoryName,
  nodes,
  edges,
  selectedId,
  connectedIds,
  fetching,
  onSelect,
}: {
  repositoryName: string;
  nodes: CodeRagNode[];
  edges: CodeRagEdge[];
  selectedId: string | null;
  connectedIds: ReadonlySet<string>;
  fetching: boolean;
  onSelect: (id: string | null) => void;
}) {
  const [layoutMode, setLayoutMode] = useState<CodeGraphLayoutMode>("network");
  const graphKey = useMemo(() => topologyKey(nodes, edges), [edges, nodes]);
  const defaultPositions = useMemo(
    () =>
      codeGraphLayoutPositions(
        nodes,
        edges,
        layoutMode,
        CANVAS_WIDTH,
        CANVAS_HEIGHT,
      ),
    [edges, layoutMode, nodes],
  );
  const [positions, setPositions] =
    useState<Record<string, CodeGraphPoint>>(defaultPositions);
  const [view, setView] = useState<CodeGraphView>(FIT_VIEW);
  const [dragKind, setDragKind] = useState<DragState["kind"] | null>(null);
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(() => new Set());
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const suppressNodeClickRef = useRef<{ id: string; until: number } | null>(
    null,
  );
  const positionsRef = useRef(positions);
  const viewRef = useRef(view);
  const previousLayoutRef = useRef(layoutMode);
  const markerId = `code-graph-arrow-${useId().replaceAll(":", "")}`;

  positionsRef.current = positions;
  viewRef.current = view;

  useEffect(() => {
    const layoutChanged = previousLayoutRef.current !== layoutMode;
    setPositions((current) => {
      if (layoutChanged) return defaultPositions;
      return Object.fromEntries(
        nodes.map((node) => [
          node.id,
          current[node.id] ||
            defaultPositions[node.id] || { x: node.x, y: node.y },
        ]),
      );
    });
    if (layoutChanged) {
      setPinnedIds(new Set());
      setView(FIT_VIEW);
    }
    previousLayoutRef.current = layoutMode;
  }, [defaultPositions, graphKey, layoutMode, nodes]);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      const anchor = clientPointToCodeGraph(
        { x: event.clientX, y: event.clientY },
        svg.getBoundingClientRect(),
      );
      const factor = Math.exp(-event.deltaY * 0.0015);
      const next = zoomCodeGraphView(
        viewRef.current,
        anchor,
        viewRef.current.scale * factor,
      );
      viewRef.current = next;
      setView(next);
    };
    svg.addEventListener("wheel", handleWheel, { passive: false });
    return () => svg.removeEventListener("wheel", handleWheel);
  }, [nodes.length]);

  const positionedNodes = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        ...(positions[node.id] || defaultPositions[node.id]),
      })),
    [defaultPositions, nodes, positions],
  );
  const pointMap = useMemo(
    () => new Map(positionedNodes.map((node) => [node.id, node])),
    [positionedNodes],
  );

  const updateView = (next: CodeGraphView) => {
    viewRef.current = next;
    setView(next);
  };

  const zoomAtCenter = (factor: number) => {
    updateView(
      zoomCodeGraphView(
        viewRef.current,
        { x: CANVAS_WIDTH / 2, y: CANVAS_HEIGHT / 2 },
        viewRef.current.scale * factor,
      ),
    );
  };

  const handleBackgroundPointerDown = (
    event: ReactPointerEvent<SVGSVGElement>,
  ) => {
    const target = event.target as Element;
    if (target.closest(".code-graph-node")) return;
    const point = clientPointToCodeGraph(
      { x: event.clientX, y: event.clientY },
      event.currentTarget.getBoundingClientRect(),
    );
    dragRef.current = {
      kind: "pan",
      pointerId: event.pointerId,
      start: point,
      view: viewRef.current,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragKind("pan");
  };

  const handleNodePointerDown = (
    event: ReactPointerEvent<SVGGElement>,
    nodeId: string,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    const svg = event.currentTarget.ownerSVGElement;
    const origin = positionsRef.current[nodeId];
    if (!svg || !origin) return;
    const point = clientPointToCodeGraph(
      { x: event.clientX, y: event.clientY },
      svg.getBoundingClientRect(),
    );
    dragRef.current = {
      kind: "node",
      pointerId: event.pointerId,
      nodeId,
      start: graphPointToWorld(point, viewRef.current),
      origin,
      moved: false,
    };
    svg.setPointerCapture?.(event.pointerId);
    setDragKind("node");
  };

  const handlePointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const point = clientPointToCodeGraph(
      { x: event.clientX, y: event.clientY },
      event.currentTarget.getBoundingClientRect(),
    );
    if (drag.kind === "pan") {
      const dx = point.x - drag.start.x;
      const dy = point.y - drag.start.y;
      if (Math.hypot(dx, dy) >= DRAG_THRESHOLD) drag.moved = true;
      updateView({
        ...drag.view,
        x: drag.view.x + dx,
        y: drag.view.y + dy,
      });
      return;
    }
    const world = graphPointToWorld(point, viewRef.current);
    const dx = world.x - drag.start.x;
    const dy = world.y - drag.start.y;
    if (Math.hypot(dx, dy) >= DRAG_THRESHOLD / viewRef.current.scale) {
      drag.moved = true;
    }
    const next = {
      ...positionsRef.current,
      [drag.nodeId]: {
        x: clamp(
          drag.origin.x + dx,
          12,
          CANVAS_WIDTH -
            (layoutMode === "network"
              ? CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH
              : CODE_GRAPH_NODE_WIDTH) -
            12,
        ),
        y: clamp(
          drag.origin.y + dy,
          12,
          CANVAS_HEIGHT -
            (layoutMode === "network"
              ? CODE_GRAPH_NETWORK_FOOTPRINT_HEIGHT
              : CODE_GRAPH_NODE_HEIGHT) -
            12,
        ),
      },
    };
    positionsRef.current = next;
    setPositions(next);
  };

  const finishPointer = (event: ReactPointerEvent<SVGSVGElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (drag.kind === "node") {
      if (drag.moved) {
        suppressNodeClickRef.current = {
          id: drag.nodeId,
          until: Date.now() + 180,
        };
        setPinnedIds((current) => new Set(current).add(drag.nodeId));
      } else {
        onSelect(drag.nodeId);
      }
    } else if (!drag.moved) {
      onSelect(null);
    }
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    dragRef.current = null;
    setDragKind(null);
  };

  const cancelPointer = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    dragRef.current = null;
    setDragKind(null);
  };

  const resetLayout = () => {
    positionsRef.current = defaultPositions;
    setPositions(defaultPositions);
    setPinnedIds(new Set());
    updateView(FIT_VIEW);
    onSelect(null);
  };

  const handleNodeKey = (event: KeyboardEvent<SVGGElement>, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(id);
    }
  };

  if (!nodes.length) {
    return (
      <div className="code-graph-no-results">
        <Search size={22} />
        <h3>没有匹配节点</h3>
        <p>清除搜索或重新启用实体层。</p>
      </div>
    );
  }

  return (
    <div className="code-graph-stage">
      <header className="code-graph-stage__toolbar">
        <div className="code-graph-stage__summary">
          <strong>{nodes.length} 个可见节点</strong>
          <span>{edges.length} 条关系</span>
          {fetching ? <em>正在扩展邻居…</em> : null}
        </div>
        <div className="code-graph-stage__actions">
          <div className="code-graph-layout-switch" aria-label="图谱布局">
            <button
              type="button"
              className={layoutMode === "network" ? "is-active" : ""}
              aria-pressed={layoutMode === "network"}
              onClick={() => setLayoutMode("network")}
            >
              <Network size={14} /> 网状
            </button>
            <button
              type="button"
              className={layoutMode === "layered" ? "is-active" : ""}
              aria-pressed={layoutMode === "layered"}
              onClick={() => setLayoutMode("layered")}
            >
              <GitBranch size={14} /> 树状
            </button>
          </div>
          <button
            type="button"
            aria-label="缩小代码图谱"
            onClick={() => zoomAtCenter(0.86)}
          >
            <ZoomOut size={15} />
          </button>
          <span className="code-graph-zoom-value">
            {Math.round(view.scale * 100)}%
          </span>
          <button
            type="button"
            aria-label="放大代码图谱"
            onClick={() => zoomAtCenter(1.16)}
          >
            <ZoomIn size={15} />
          </button>
          <button type="button" onClick={() => updateView(FIT_VIEW)}>
            <LocateFixed size={14} /> 适配
          </button>
          <button type="button" onClick={resetLayout}>
            <RotateCcw size={14} /> 重置
          </button>
        </div>
      </header>
      <div className={`code-graph-viewport is-${dragKind || "idle"}`}>
        <svg
          ref={svgRef}
          className="code-graph-canvas"
          viewBox={`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`}
          role="img"
          aria-label={`${repositoryName} 代码仓库关系图谱`}
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "Escape") onSelect(null);
          }}
          onPointerDown={handleBackgroundPointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={finishPointer}
          onPointerCancel={cancelPointer}
        >
          <defs>
            <marker
              id={markerId}
              markerWidth="8"
              markerHeight="8"
              refX="7"
              refY="4"
              orient="auto"
            >
              <path d="M0,0 L8,4 L0,8 z" />
            </marker>
          </defs>
          <rect
            className="code-graph-hit-area"
            data-testid="code-graph-background"
            x="0"
            y="0"
            width={CANVAS_WIDTH}
            height={CANVAS_HEIGHT}
          />
          <g
            data-testid="code-graph-world"
            className="code-graph-world"
            transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}
          >
            {layoutMode === "layered" ? (
              <g className="code-graph-layer-labels" aria-hidden="true">
                <text x="34" y="32">
                  仓库
                </text>
                <text x="270" y="32">
                  Ref / 快照
                </text>
                <text x="510" y="32">
                  提交历史
                </text>
                <text x="760" y="32">
                  文件 / 变更
                </text>
                <text x="1015" y="32">
                  Symbol / 验证
                </text>
              </g>
            ) : null}
            <g className="code-graph-edges">
              {edges.map((edge) => {
                const source = pointMap.get(edge.source);
                const target = pointMap.get(edge.target);
                if (!source || !target) return null;
                const active =
                  !selectedId ||
                  edge.source === selectedId ||
                  edge.target === selectedId;
                const geometry = edgeGeometry(source, target, layoutMode);
                return (
                  <g
                    key={edge.id}
                    className={`${active ? "is-active" : "is-muted"} ${edge.origin === "projection" ? "is-projection" : ""}`}
                  >
                    <path
                      d={geometry.path}
                      markerEnd={`url(#${markerId})`}
                      vectorEffect="non-scaling-stroke"
                    />
                    {selectedId && active ? (
                      <text x={geometry.label.x} y={geometry.label.y - 5}>
                        {CODE_GRAPH_RELATION_LABELS[edge.predicate] ||
                          edge.predicate}
                      </text>
                    ) : null}
                  </g>
                );
              })}
            </g>
            <g className="code-graph-nodes">
              {positionedNodes.map((node) => {
                const selected = node.id === selectedId;
                const muted = Boolean(selectedId) && !connectedIds.has(node.id);
                return (
                  <g
                    key={node.id}
                    data-node-id={node.id}
                    data-position-x={node.x.toFixed(2)}
                    data-position-y={node.y.toFixed(2)}
                    className={`code-graph-node is-${layoutMode} is-${node.type} ${selected ? "is-selected" : ""} ${muted ? "is-muted" : ""} ${pinnedIds.has(node.id) ? "is-pinned" : ""}`}
                    transform={`translate(${node.x} ${node.y})`}
                    role="button"
                    tabIndex={0}
                    aria-label={`${CODE_GRAPH_TYPE_LABELS[node.type]}：${node.label}`}
                    onPointerDown={(event) =>
                      handleNodePointerDown(event, node.id)
                    }
                    onClick={(event) => {
                      event.stopPropagation();
                      if (
                        suppressNodeClickRef.current?.id === node.id &&
                        Date.now() <= suppressNodeClickRef.current.until
                      ) {
                        suppressNodeClickRef.current = null;
                        return;
                      }
                      suppressNodeClickRef.current = null;
                      onSelect(node.id);
                    }}
                    onKeyDown={(event) => handleNodeKey(event, node.id)}
                  >
                    {layoutMode === "network" ? (
                      <>
                        <circle
                          className="code-graph-network-node__body"
                          cx={CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH / 2}
                          cy={CODE_GRAPH_NETWORK_NODE_CENTER_Y}
                          r={CODE_GRAPH_NETWORK_NODE_RADIUS}
                        />
                        <text
                          className="code-graph-network-node__icon"
                          x={CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH / 2}
                          y={CODE_GRAPH_NETWORK_NODE_CENTER_Y + 5}
                        >
                          {nodeGlyph(node)}
                        </text>
                        <text
                          className="code-graph-network-node__title"
                          x={CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH / 2}
                          y="69"
                        >
                          {shortText(node.label, 16)}
                        </text>
                        <text
                          className="code-graph-network-node__type"
                          x={CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH / 2}
                          y="82"
                        >
                          {CODE_GRAPH_TYPE_LABELS[node.type]}
                        </text>
                      </>
                    ) : (
                      <>
                        <rect
                          className="code-graph-card-node__body"
                          width={CODE_GRAPH_NODE_WIDTH}
                          height={CODE_GRAPH_NODE_HEIGHT}
                          rx="9"
                        />
                        <circle
                          className="code-graph-card-node__badge"
                          cx="20"
                          cy="20"
                          r="10"
                        />
                        <text className="code-graph-node__icon" x="20" y="24">
                          {nodeGlyph(node)}
                        </text>
                        <text className="code-graph-node__type" x="38" y="16">
                          {CODE_GRAPH_TYPE_LABELS[node.type]}
                        </text>
                        <text className="code-graph-node__title" x="13" y="37">
                          {shortText(node.label, 23)}
                        </text>
                        <text
                          className="code-graph-node__subtitle"
                          x="13"
                          y="51"
                        >
                          {shortText(nodeSubtitle(node), 28)}
                        </text>
                      </>
                    )}
                    {node.origin === "projection" ? (
                      <circle
                        className="code-graph-node__projection"
                        cx={
                          layoutMode === "network"
                            ? CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH / 2 + 19
                            : CODE_GRAPH_NODE_WIDTH - 12
                        }
                        cy="11"
                        r="4"
                      />
                    ) : null}
                    {pinnedIds.has(node.id) ? (
                      <circle
                        className="code-graph-node__pin"
                        cx={
                          layoutMode === "network"
                            ? CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH / 2 + 11
                            : CODE_GRAPH_NODE_WIDTH - 25
                        }
                        cy={layoutMode === "network" ? 7 : 11}
                        r="3"
                      />
                    ) : null}
                  </g>
                );
              })}
            </g>
          </g>
        </svg>
        <div className="code-graph-interaction-hint" aria-hidden="true">
          <Move size={13} /> 拖动画布平移 · 拖动节点固定位置 · 滚轮缩放
        </div>
      </div>
      <footer className="code-graph-legend">
        <span>
          <i className="is-authority" /> 持久图谱 / Git 权威
        </span>
        <span>
          <i className="is-projection" /> 确定性投影
        </span>
        <span>
          <ArrowRight size={13} /> 点击聚焦邻域，空白处清除
        </span>
      </footer>
    </div>
  );
}
