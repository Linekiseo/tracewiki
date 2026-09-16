import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  Beaker,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Code2,
  Copy,
  FileCode2,
  Filter,
  Focus,
  Goal,
  LayoutGrid,
  Link2,
  ListTree,
  Maximize2,
  Minus,
  Network,
  PanelRightClose,
  Pin,
  Plus,
  RotateCcw,
  Save,
  Search,
  Terminal,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Button,
  EmptyState,
  ExpandableText,
  Status,
} from "../../components/ui";
import { api } from "../../lib/api";
import {
  cleanEvidenceText,
  humanizeSessionTitle,
  presentSearchSnippet,
  sourceDestination,
} from "../../lib/presentation";
import type {
  CodexTimelineOperation,
  CodexTimelineTurn,
  SearchResult,
} from "../../lib/types";
import {
  buildSessionGraph,
  type SessionGraphEdge,
  type SessionGraphNode,
  type SessionGraphNodeKind,
} from "./sessionGraph";

type GraphPosition = { x: number; y: number };
type GraphPositions = Record<string, GraphPosition>;
type GraphFilter = "all" | SessionGraphNodeKind;

const GRAPH_NODE_WIDTH = 196;
const GRAPH_PORT_Y = 44;
const GRAPH_CANVAS_WIDTH = 4800;
const GRAPH_CANVAS_HEIGHT = 3000;
const GRAPH_ORIGIN_X = 1700;
const GRAPH_ORIGIN_Y = 1000;

const graphKindMeta: Record<
  SessionGraphNodeKind,
  { label: string; icon: typeof Goal }
> = {
  goal: { label: "目标", icon: Goal },
  operation: { label: "操作", icon: ListTree },
  artifact: { label: "代码", icon: FileCode2 },
  validation: { label: "验证", icon: Beaker },
  conclusion: { label: "结论", icon: CheckCircle2 },
  risk: { label: "风险", icon: TriangleAlert },
  followup: { label: "后续", icon: Wrench },
};

const graphNodeKinds: SessionGraphNodeKind[] = [
  "goal",
  "operation",
  "artifact",
  "validation",
  "conclusion",
  "risk",
  "followup",
];
const graphFilters: GraphFilter[] = ["all", ...graphNodeKinds];

function formatTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function statusLabel(value: string) {
  return (
    {
      completed: "已完成",
      running: "进行中",
      failed: "失败",
      passed: "通过",
      recorded: "已记录",
      pending: "待执行",
      verified: "已验证",
      indexed: "已索引",
    }[value] || value
  );
}

function graphStorageKey(threadId: string, turnId: string) {
  return `rag_session_graph_layout:v2:${threadId}:${turnId}`;
}

function savedPositions(threadId: string, turnId: string): GraphPositions {
  try {
    return JSON.parse(
      window.localStorage.getItem(graphStorageKey(threadId, turnId)) || "{}",
    ) as GraphPositions;
  } catch {
    return {};
  }
}

function nodeReason(node: SessionGraphNode) {
  if (node.kind === "goal")
    return "该节点来自当前轮次的用户目标，是本轮证据链的起点。";
  if (node.kind === "operation") {
    return "该操作簇汇总本轮命令、工具调用和实现步骤，并连接它们产生的代码产物。";
  }
  if (node.kind === "artifact") {
    return "该文件在本轮被修改，并被代码来源或审计定位命中，可继续跳转到源码核对。";
  }
  if (node.kind === "validation") {
    return "该验证记录包含真实命令或退出状态，用于支持本轮完成结论。";
  }
  if (node.kind === "conclusion")
    return "该结论由上游代码产物与验证记录共同支持。";
  if (node.kind === "risk")
    return "该风险来自本轮总结或失败状态，需要保留为后续审计对象。";
  return "该后续任务由已识别风险派生，便于返回链式审计页继续执行。";
}

function previewText(node: SessionGraphNode) {
  if (node.operation) {
    return [node.operation.command, node.operation.output]
      .filter(Boolean)
      .join("\n");
  }
  if (node.source) {
    return presentSearchSnippet(
      node.source.source,
      node.source.title,
      node.source.snippet,
      true,
    );
  }
  return node.locator;
}

function connectedNodeIds(selectedId: string, edges: SessionGraphEdge[]) {
  const ids = new Set([selectedId]);
  edges.forEach((edge) => {
    if (edge.source === selectedId) ids.add(edge.target);
    if (edge.target === selectedId) ids.add(edge.source);
  });
  return ids;
}

function GraphNodeCard({
  node,
  position,
  selected,
  dimmed,
  dragging,
  hasInput,
  hasOutput,
  clusterOpen,
  operations,
  onSelect,
  onToggleCluster,
  onPointerDown,
}: {
  node: SessionGraphNode;
  position: GraphPosition;
  selected: boolean;
  dimmed: boolean;
  dragging: boolean;
  hasInput: boolean;
  hasOutput: boolean;
  clusterOpen: boolean;
  operations: CodexTimelineOperation[];
  onSelect: () => void;
  onToggleCluster: () => void;
  onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
}) {
  const meta = graphKindMeta[node.kind];
  const Icon = meta.icon;
  const onKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect();
    }
  };
  return (
    <article
      className={[
        "turn-graph-node",
        `is-${node.kind}`,
        selected ? "is-selected" : "",
        dimmed ? "is-dimmed" : "",
        dragging ? "is-dragging" : "",
        node.kind === "operation" && clusterOpen ? "is-expanded" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      data-graph-node={node.id}
      role="button"
      tabIndex={0}
      aria-label={`${meta.label}节点：${node.title}`}
      style={{ left: position.x, top: position.y }}
      onClick={onSelect}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
    >
      {hasInput ? (
        <span
          className="turn-graph-node__port is-input"
          data-port={`${node.id}:input`}
          aria-hidden="true"
        />
      ) : null}
      <header>
        <span className="turn-graph-node__icon">
          <Icon size={17} />
        </span>
        <span>{meta.label}</span>
        <i aria-hidden="true">⠿</i>
      </header>
      <strong>{node.title}</strong>
      <small title={node.subtitle}>{node.subtitle}</small>
      {node.facts?.length ? (
        <div className="turn-graph-node__facts">
          {node.facts.map((fact) => (
            <span key={`${fact.label}-${fact.value}`}>
              <em>{fact.label}</em>
              {fact.value}
            </span>
          ))}
        </div>
      ) : null}
      {node.kind === "artifact" && node.evidence ? (
        <code className="turn-graph-node__evidence">
          {cleanEvidenceText(node.evidence, 88)}
        </code>
      ) : null}
      {node.kind === "operation" ? (
        <button
          type="button"
          className="turn-graph-node__cluster-toggle"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            onToggleCluster();
          }}
          aria-expanded={clusterOpen}
        >
          <Terminal size={13} />
          {clusterOpen ? "收起执行明细" : `展开 ${operations.length} 条执行`}
          <ChevronDown size={13} />
        </button>
      ) : null}
      {node.kind === "operation" && clusterOpen ? (
        <div className="turn-graph-node__cluster">
          {operations.slice(0, 5).map((operation) => (
            <span key={operation.id}>
              {operation.kind === "validation" ? (
                <Beaker size={12} />
              ) : operation.kind === "tool" ? (
                <Wrench size={12} />
              ) : (
                <Terminal size={12} />
              )}
              {cleanEvidenceText(operation.command || operation.name, 45)}
            </span>
          ))}
          {operations.length > 5 ? (
            <em>另有 {operations.length - 5} 条记录</em>
          ) : null}
        </div>
      ) : null}
      {hasOutput ? (
        <span
          className="turn-graph-node__port is-output"
          data-port={`${node.id}:output`}
          aria-hidden="true"
        />
      ) : null}
    </article>
  );
}

export function SessionGraphWorkspace() {
  const { projectId = "", threadId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const viewportRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    nodeId: string;
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    moved: boolean;
  } | null>(null);
  const suppressNodeClickRef = useRef(false);
  const panRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    scrollLeft: number;
    scrollTop: number;
  } | null>(null);
  const timelineQuery = useQuery({
    queryKey: ["session-timeline", threadId],
    queryFn: ({ signal }) => api.sessions.timeline(threadId, signal),
    enabled: Boolean(threadId),
  });
  const timeline = timelineQuery.data;
  const requestedTurn = searchParams.get("turn");
  const matchedTurnIndex =
    timeline?.turns.findIndex(
      (item) =>
        item.id === requestedTurn || String(item.ordinal) === requestedTurn,
    ) ?? -1;
  const timelineTurn =
    matchedTurnIndex >= 0
      ? timeline?.turns[matchedTurnIndex]
      : !requestedTurn
        ? timeline?.turns[0]
        : undefined;
  const auditTurnId =
    timelineTurn?.id ||
    (requestedTurn && !/^\d+$/.test(requestedTurn) ? requestedTurn : "");
  const auditQuery = useQuery({
    queryKey: ["session-turn-audit", threadId, auditTurnId],
    queryFn: ({ signal }) =>
      api.sessions.turnAudit(threadId, auditTurnId, signal),
    enabled: Boolean(threadId && auditTurnId),
  });
  const turn =
    auditQuery.data?.id === auditTurnId ? auditQuery.data : timelineTurn;
  const turnIndex = matchedTurnIndex;
  const relatedSearchText = cleanEvidenceText(
    [timeline?.title, turn?.goal, ...(turn?.files || []).slice(0, 5)]
      .filter(Boolean)
      .join(" "),
    220,
  );
  const relatedQuery = useQuery({
    queryKey: ["session-graph-related", projectId, turn?.id, relatedSearchText],
    queryFn: () => api.search.project(projectId, relatedSearchText),
    enabled: Boolean(projectId && relatedSearchText),
  });
  const related = useMemo(
    () =>
      (relatedQuery.data?.results || [])
        .filter((item) => item.source !== "codex")
        .slice(0, 32),
    [relatedQuery.data?.results],
  );
  const model = useMemo(
    () => (turn ? buildSessionGraph(turn, related) : null),
    [turn, related],
  );
  const [positions, setPositions] = useState<GraphPositions>({});
  const [selectedId, setSelectedId] = useState("goal");
  const [filter, setFilter] = useState<GraphFilter>("all");
  const [query, setQuery] = useState("");
  const [focusNeighbors, setFocusNeighbors] = useState(false);
  const [clusterOpen, setClusterOpen] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [copied, setCopied] = useState(false);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [panning, setPanning] = useState(false);

  useEffect(() => {
    if (!turn || !model) return;
    const stored = savedPositions(threadId, turn.id);
    setPositions(
      Object.fromEntries(
        model.nodes.map((node) => [
          node.id,
          stored[node.id] || { x: node.x, y: node.y },
        ]),
      ),
    );
    setSelectedId("goal");
    setClusterOpen(false);
    window.requestAnimationFrame(() => {
      const viewport = viewportRef.current;
      if (!viewport) return;
      viewport.scrollLeft = (GRAPH_ORIGIN_X - 60) * zoom;
      viewport.scrollTop =
        (GRAPH_ORIGIN_Y + 315) * zoom - viewport.clientHeight / 2;
    });
  }, [threadId, turn?.id]);

  const selectTurn = useCallback(
    (index: number) => {
      const item = timeline?.turns[index];
      if (!item) return;
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("turn", item.id);
        return next;
      });
    },
    [setSearchParams, timeline?.turns],
  );

  const selected =
    model?.nodes.find((node) => node.id === selectedId) || model?.nodes[0];
  const connected = useMemo(
    () => connectedNodeIds(selected?.id || "", model?.edges || []),
    [model?.edges, selected?.id],
  );
  const queryNeedle = query.trim().toLowerCase();
  const visibleNodeIds = useMemo(() => {
    const ids = new Set<string>();
    (model?.nodes || []).forEach((node) => {
      if (filter !== "all" && node.kind !== filter) return;
      if (
        queryNeedle &&
        !`${node.title} ${node.subtitle}`.toLowerCase().includes(queryNeedle)
      ) {
        return;
      }
      if (focusNeighbors && selected && !connected.has(node.id)) return;
      ids.add(node.id);
    });
    return ids;
  }, [connected, filter, focusNeighbors, model?.nodes, queryNeedle, selected]);
  const visibleEdges = useMemo(
    () =>
      (model?.edges || []).filter(
        (edge) =>
          visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target),
      ),
    [model?.edges, visibleNodeIds],
  );
  const nodesWithInput = useMemo(
    () => new Set(visibleEdges.map((edge) => edge.target)),
    [visibleEdges],
  );
  const nodesWithOutput = useMemo(
    () => new Set(visibleEdges.map((edge) => edge.source)),
    [visibleEdges],
  );
  const nodeMap = useMemo(
    () => new Map((model?.nodes || []).map((node) => [node.id, node])),
    [model?.nodes],
  );
  const upstream = useMemo(
    () =>
      (model?.edges || [])
        .filter((edge) => edge.target === selected?.id)
        .map((edge) => nodeMap.get(edge.source))
        .filter(Boolean) as SessionGraphNode[],
    [model?.edges, nodeMap, selected?.id],
  );
  const downstream = useMemo(
    () =>
      (model?.edges || [])
        .filter((edge) => edge.source === selected?.id)
        .map((edge) => nodeMap.get(edge.target))
        .filter(Boolean) as SessionGraphNode[],
    [model?.edges, nodeMap, selected?.id],
  );

  const chainPath = `/p/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(threadId)}${turn ? `?turn=${encodeURIComponent(turn.id)}` : ""}`;
  const sessionTitle = humanizeSessionTitle(
    timeline?.title,
    timeline?.turns[0]?.goal,
    timeline?.updated_at,
  );
  const operations = turn?.operations || [];
  const clusterOperations = operations.filter(
    (item) => item.kind !== "validation",
  );

  const saveLayout = () => {
    if (!turn) return;
    window.localStorage.setItem(
      graphStorageKey(threadId, turn.id),
      JSON.stringify(positions),
    );
  };
  const centreGraph = (nextZoom = zoom) => {
    window.requestAnimationFrame(() => {
      const viewport = viewportRef.current;
      if (!viewport) return;
      viewport.scrollLeft = (GRAPH_ORIGIN_X - 40) * nextZoom;
      viewport.scrollTop =
        (GRAPH_ORIGIN_Y + 300) * nextZoom - viewport.clientHeight / 2;
    });
  };
  const autoArrangeLayout = () => {
    if (!model || !turn) return;
    setPositions(
      Object.fromEntries(
        model.nodes.map((node) => [node.id, { x: node.x, y: node.y }]),
      ),
    );
    centreGraph();
  };
  const restoreSavedLayout = () => {
    if (!model || !turn) return;
    const stored = savedPositions(threadId, turn.id);
    setPositions(
      Object.fromEntries(
        model.nodes.map((node) => [
          node.id,
          stored[node.id] || { x: node.x, y: node.y },
        ]),
      ),
    );
    centreGraph();
  };
  const focusNode = (nodeId: string) => {
    setSelectedId(nodeId);
    setInspectorOpen(true);
    window.requestAnimationFrame(() => {
      const target = viewportRef.current?.querySelector<HTMLElement>(
        `[data-graph-node="${CSS.escape(nodeId)}"]`,
      );
      target?.scrollIntoView?.({
        behavior: "smooth",
        block: "center",
        inline: "center",
      });
    });
  };
  const onNodePointerDown = (
    nodeId: string,
    event: ReactPointerEvent<HTMLElement>,
  ) => {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest("button")) return;
    const position = positions[nodeId];
    if (!position) return;
    event.preventDefault();
    dragRef.current = {
      nodeId,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: position.x,
      originY: position.y,
      moved: false,
    };
    setDraggingId(nodeId);
  };

  useEffect(() => {
    const onPointerMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const dx = (event.clientX - drag.startX) / zoom;
      const dy = (event.clientY - drag.startY) / zoom;
      if (Math.abs(dx) + Math.abs(dy) > 4) {
        drag.moved = true;
        suppressNodeClickRef.current = true;
      }
      if (!drag.moved) return;
      event.preventDefault();
      setPositions((current) => ({
        ...current,
        [drag.nodeId]: {
          x: drag.originX + dx,
          y: drag.originY + dy,
        },
      }));
    };
    const finishDrag = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      dragRef.current = null;
      setDraggingId(null);
      window.setTimeout(() => {
        suppressNodeClickRef.current = false;
      }, 0);
    };
    window.addEventListener("pointermove", onPointerMove, { passive: false });
    window.addEventListener("pointerup", finishDrag);
    window.addEventListener("pointercancel", finishDrag);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", finishDrag);
      window.removeEventListener("pointercancel", finishDrag);
    };
  }, [zoom]);

  if (!timelineQuery.isLoading && !timeline) {
    return (
      <EmptyState
        icon={<Network size={24} />}
        title="没有找到该研发会话"
        description="返回会话总览选择仍在项目中的会话。"
        action={
          <Button onClick={() => navigate(`/p/${projectId}/sessions`)}>
            返回总览
          </Button>
        }
      />
    );
  }

  return (
    <div className="session-graph-page">
      <header className="session-graph-header">
        <div className="session-graph-header__title">
          <button onClick={() => navigate(chainPath)} aria-label="返回会话详情">
            <ArrowLeft size={16} />
          </button>
          <div>
            <span>
              单会话变动图谱 / 轮次{" "}
              {String(turn?.ordinal || 1).padStart(2, "0")}
            </span>
            <h1>{timeline ? sessionTitle : "读取会话中…"}</h1>
            <p>
              {turn?.files.length || 0} 变更文件 · {turn?.command_count || 0}{" "}
              次执行 · {turn?.validation_count || 0} 项验证 ·{" "}
              {statusLabel(turn?.status || "pending")}
            </p>
          </div>
        </div>
        <div className="session-graph-header__turn">
          <button
            onClick={() => selectTurn(Math.max(0, turnIndex - 1))}
            disabled={turnIndex <= 0}
            aria-label="上一轮"
          >
            <ChevronLeft size={16} />
          </button>
          <label>
            <span>当前轮次</span>
            <select
              value={turnIndex >= 0 ? String(turnIndex) : "requested"}
              onChange={(event) => {
                if (event.target.value !== "requested") {
                  selectTurn(Number(event.target.value));
                }
              }}
            >
              {turnIndex < 0 && turn ? (
                <option value="requested">
                  轮次 {String(turn.ordinal).padStart(2, "0")} ·{" "}
                  {cleanEvidenceText(turn.goal, 44)}
                </option>
              ) : null}
              {(timeline?.turns || []).map((item, index) => (
                <option value={index} key={item.id}>
                  轮次 {String(item.ordinal).padStart(2, "0")} ·{" "}
                  {cleanEvidenceText(item.goal, 44)}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() =>
              selectTurn(
                Math.min((timeline?.turns.length || 1) - 1, turnIndex + 1),
              )
            }
            disabled={
              turnIndex < 0 || turnIndex >= (timeline?.turns.length || 1) - 1
            }
            aria-label="下一轮"
          >
            <ChevronRight size={16} />
          </button>
        </div>
        <nav className="session-view-switcher" aria-label="会话视图">
          <button onClick={() => navigate(chainPath)}>
            <ListTree size={15} />
            链式审计
          </button>
          <button className="is-active" aria-current="page">
            <Network size={15} />
            变动图谱
          </button>
        </nav>
      </header>

      <section
        className={`session-graph-workspace${inspectorOpen ? " has-inspector" : ""}`}
      >
        <main className="session-graph-canvas-column">
          <div className="session-graph-toolbar">
            <label>
              <Search size={16} />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索节点、文件、命令或结论…"
              />
            </label>
            <div className="session-graph-filter">
              <Filter size={14} />
              <select
                value={filter}
                onChange={(event) =>
                  setFilter(event.target.value as GraphFilter)
                }
              >
                {graphFilters.map((item) => (
                  <option value={item} key={item}>
                    {item === "all" ? "全部节点" : graphKindMeta[item].label}
                  </option>
                ))}
              </select>
            </div>
            <button
              className={focusNeighbors ? "is-active" : ""}
              onClick={() => setFocusNeighbors((value) => !value)}
            >
              <Focus size={14} />
              仅看上下游
            </button>
            {!inspectorOpen ? (
              <button onClick={() => setInspectorOpen(true)}>
                <PanelRightClose size={14} />
                打开检查器
              </button>
            ) : null}
          </div>

          <div className="session-graph-stage-labels" aria-hidden="true">
            {[
              ["目标", "我们要达成什么"],
              ["操作", "我们做了什么"],
              ["产物", "我们产出了什么"],
              ["验证", "如何验证正确性"],
              ["结论", "得到了什么结论"],
            ].map(([title, subtitle]) => (
              <span key={title}>
                <strong>{title}</strong>
                <small>{subtitle}</small>
              </span>
            ))}
          </div>

          <div
            className={`session-graph-viewport${panning ? " is-panning" : ""}`}
            ref={viewportRef}
            tabIndex={0}
            aria-label="可拖动的单轮证据关系图"
            onPointerDown={(event) => {
              if (event.button !== 0) return;
              if (
                (event.target as HTMLElement).closest(
                  ".turn-graph-node, button, input, select",
                )
              ) {
                return;
              }
              const viewport = viewportRef.current;
              if (!viewport) return;
              panRef.current = {
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                scrollLeft: viewport.scrollLeft,
                scrollTop: viewport.scrollTop,
              };
              viewport.setPointerCapture?.(event.pointerId);
              setPanning(true);
            }}
            onPointerMove={(event) => {
              const pan = panRef.current;
              const viewport = viewportRef.current;
              if (!pan || !viewport || pan.pointerId !== event.pointerId)
                return;
              viewport.scrollLeft =
                pan.scrollLeft - (event.clientX - pan.startX);
              viewport.scrollTop = pan.scrollTop - (event.clientY - pan.startY);
            }}
            onPointerUp={(event) => {
              const viewport = viewportRef.current;
              if (viewport?.hasPointerCapture?.(event.pointerId)) {
                viewport.releasePointerCapture(event.pointerId);
              }
              panRef.current = null;
              setPanning(false);
            }}
            onPointerCancel={() => {
              panRef.current = null;
              setPanning(false);
            }}
          >
            <div
              className="session-graph-world"
              style={{
                width: GRAPH_CANVAS_WIDTH,
                height: GRAPH_CANVAS_HEIGHT,
                transform: `scale(${zoom})`,
              }}
            >
              <svg
                className="session-graph-edges"
                viewBox={`0 0 ${GRAPH_CANVAS_WIDTH} ${GRAPH_CANVAS_HEIGHT}`}
                style={{
                  width: GRAPH_CANVAS_WIDTH,
                  height: GRAPH_CANVAS_HEIGHT,
                }}
                aria-hidden="true"
              >
                {visibleEdges.map((edge) => {
                  const sourcePosition = positions[edge.source];
                  const targetPosition = positions[edge.target];
                  if (!sourcePosition || !targetPosition) return null;
                  const highlighted =
                    edge.source === selected?.id ||
                    edge.target === selected?.id;
                  const x1 =
                    sourcePosition.x + GRAPH_ORIGIN_X + GRAPH_NODE_WIDTH;
                  const y1 = sourcePosition.y + GRAPH_ORIGIN_Y + GRAPH_PORT_Y;
                  const x2 = targetPosition.x + GRAPH_ORIGIN_X;
                  const y2 = targetPosition.y + GRAPH_ORIGIN_Y + GRAPH_PORT_Y;
                  const direction = x2 >= x1 ? 1 : -1;
                  const bend = Math.max(
                    38,
                    Math.min(112, Math.abs(x2 - x1) * 0.45),
                  );
                  const path = `M ${x1} ${y1} C ${x1 + bend * direction} ${y1}, ${x2 - bend * direction} ${y2}, ${x2} ${y2}`;
                  return (
                    <g
                      className={[
                        `is-${edge.tone}`,
                        highlighted ? "is-highlighted" : "",
                        focusNeighbors && selected && !highlighted
                          ? "is-muted"
                          : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                      key={edge.id}
                    >
                      <path
                        d={path}
                        data-edge={edge.id}
                        data-edge-source={edge.source}
                        data-edge-target={edge.target}
                      />
                      <circle
                        className="session-graph-edge-end"
                        cx={x2}
                        cy={y2}
                        r="2.5"
                      />
                      <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 7}>
                        {edge.label}
                      </text>
                      <title>{`${nodeMap.get(edge.source)?.title || edge.source} → ${nodeMap.get(edge.target)?.title || edge.target}：${edge.label}`}</title>
                    </g>
                  );
                })}
              </svg>

              {(model?.nodes || [])
                .filter((node) => visibleNodeIds.has(node.id))
                .map((node) => (
                  <GraphNodeCard
                    key={node.id}
                    node={node}
                    position={(() => {
                      const position = positions[node.id] || {
                        x: node.x,
                        y: node.y,
                      };
                      return {
                        x: position.x + GRAPH_ORIGIN_X,
                        y: position.y + GRAPH_ORIGIN_Y,
                      };
                    })()}
                    selected={node.id === selected?.id}
                    dimmed={false}
                    dragging={node.id === draggingId}
                    hasInput={nodesWithInput.has(node.id)}
                    hasOutput={nodesWithOutput.has(node.id)}
                    clusterOpen={clusterOpen}
                    operations={clusterOperations}
                    onSelect={() => {
                      if (!suppressNodeClickRef.current) focusNode(node.id);
                    }}
                    onToggleCluster={() => setClusterOpen((value) => !value)}
                    onPointerDown={(event) => onNodePointerDown(node.id, event)}
                  />
                ))}
            </div>
          </div>

          <aside className="session-graph-legend" aria-label="关系图图例">
            <header>
              <strong>图例</strong>
              <button
                onClick={() => {
                  setFilter("all");
                  setQuery("");
                  setFocusNeighbors(false);
                }}
              >
                清空筛选
              </button>
            </header>
            {graphNodeKinds.map((item) => {
              const Icon = graphKindMeta[item].icon;
              return (
                <button
                  className={filter === item ? "is-active" : ""}
                  key={item}
                  onClick={() =>
                    setFilter((current) => (current === item ? "all" : item))
                  }
                >
                  <Icon size={13} />
                  {graphKindMeta[item].label}
                </button>
              );
            })}
          </aside>

          <div className="session-graph-controls" aria-label="关系图画布控制">
            <button
              onClick={() => setZoom((value) => Math.max(0.7, value - 0.1))}
              aria-label="缩小"
            >
              <Minus size={15} />
            </button>
            <output>{Math.round(zoom * 100)}%</output>
            <button
              onClick={() => setZoom((value) => Math.min(1.3, value + 0.1))}
              aria-label="放大"
            >
              <Plus size={15} />
            </button>
            <button
              onClick={() => {
                const nextZoom = 0.7;
                setZoom(nextZoom);
                window.requestAnimationFrame(() => {
                  const viewport = viewportRef.current;
                  if (!viewport) return;
                  viewport.scrollLeft = (GRAPH_ORIGIN_X - 40) * nextZoom;
                  viewport.scrollTop = (GRAPH_ORIGIN_Y + 40) * nextZoom - 70;
                });
              }}
            >
              <Maximize2 size={14} />
              适应画布
            </button>
            <button onClick={autoArrangeLayout}>
              <LayoutGrid size={14} />
              整理为证据流
            </button>
            <button onClick={restoreSavedLayout}>
              <RotateCcw size={14} />
              恢复保存布局
            </button>
            <button onClick={saveLayout}>
              <Save size={14} />
              保存当前布局
            </button>
          </div>

          <div className="session-graph-minimap" aria-hidden="true">
            <svg viewBox="-500 -400 2200 1500">
              {(model?.edges || []).map((edge) => {
                const sourcePosition = positions[edge.source];
                const targetPosition = positions[edge.target];
                if (!sourcePosition || !targetPosition) return null;
                return (
                  <line
                    key={edge.id}
                    x1={sourcePosition.x + 98}
                    y1={sourcePosition.y + 43}
                    x2={targetPosition.x + 98}
                    y2={targetPosition.y + 43}
                  />
                );
              })}
              {(model?.nodes || []).map((node) => {
                const position = positions[node.id] || { x: node.x, y: node.y };
                return (
                  <rect
                    className={`is-${node.kind}`}
                    key={node.id}
                    x={position.x}
                    y={position.y}
                    width="196"
                    height="86"
                    rx="10"
                  />
                );
              })}
            </svg>
          </div>
        </main>

        {inspectorOpen && selected ? (
          <aside className="session-node-inspector">
            <header>
              <strong>节点检查器</strong>
              <span>
                <button aria-label="固定检查器" title="固定检查器">
                  <Pin size={14} />
                </button>
                <button
                  onClick={() => setInspectorOpen(false)}
                  aria-label="关闭节点检查器"
                >
                  <PanelRightClose size={15} />
                </button>
              </span>
            </header>
            <section className="session-node-inspector__identity">
              <span className={`is-${selected.kind}`}>
                {(() => {
                  const Icon = graphKindMeta[selected.kind].icon;
                  return <Icon size={18} />;
                })()}
              </span>
              <div>
                <h2>{selected.title}</h2>
                <p title={selected.subtitle}>{selected.subtitle}</p>
                <em>{graphKindMeta[selected.kind].label}节点</em>
              </div>
            </section>
            <div
              className="session-node-inspector__auditline"
              aria-label="节点审计摘要"
            >
              <span>
                <small>状态</small>
                <strong>
                  {statusLabel(
                    selected.operation?.status ||
                      selected.file?.status ||
                      turn?.status ||
                      "recorded",
                  )}
                </strong>
              </span>
              <span>
                <small>轮次</small>
                <strong>{String(turn?.ordinal || 1).padStart(2, "0")}</strong>
              </span>
              <span>
                <small>时间</small>
                <strong>
                  {formatTime(
                    selected.operation?.timestamp || turn?.completed_at,
                  )}
                </strong>
              </span>
              {(selected.facts || []).map((fact) => (
                <span key={`${fact.label}-${fact.value}`}>
                  <small>{fact.label}</small>
                  <strong>{fact.value}</strong>
                </span>
              ))}
            </div>
            <section className="session-node-inspector__section">
              <ExpandableText
                className="session-node-inspector__reader"
                label={
                  selected.kind === "artifact"
                    ? "代码变更证据"
                    : selected.kind === "operation" ||
                        selected.kind === "validation"
                      ? "命令与结果"
                      : "原始证据"
                }
                title={`${selected.title} · 完整证据`}
                description={`${graphKindMeta[selected.kind].label} · ${nodeReason(selected)}`}
                content={selected.evidence || previewText(selected)}
                previewSize="comfortable"
                tone={
                  selected.kind === "operation" ||
                  selected.kind === "validation"
                    ? "terminal"
                    : "code"
                }
              />
              <p className="session-node-inspector__reason">
                <Link2 size={12} />
                {nodeReason(selected)}
              </p>
            </section>
            <section className="session-node-inspector__section">
              <h3>关系概览</h3>
              <div className="session-node-inspector__relations">
                {upstream.map((node) => (
                  <button
                    key={`up-${node.id}`}
                    onClick={() => focusNode(node.id)}
                  >
                    <span>上游</span>
                    {node.title}
                    <ArrowUpRight size={13} />
                  </button>
                ))}
                {downstream.map((node) => (
                  <button
                    key={`down-${node.id}`}
                    onClick={() => focusNode(node.id)}
                  >
                    <span>下游</span>
                    {node.title}
                    <ArrowUpRight size={13} />
                  </button>
                ))}
                {!upstream.length && !downstream.length ? (
                  <p>当前筛选下没有相邻节点。</p>
                ) : null}
              </div>
            </section>
            <section className="session-node-inspector__section">
              <div className="session-node-inspector__locator">
                <span>
                  <small>审计定位</small>
                  <code title={selected.locator}>{selected.locator}</code>
                </span>
                <button
                  aria-label="复制审计定位"
                  onClick={async () => {
                    await navigator.clipboard?.writeText(selected.locator);
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1200);
                  }}
                >
                  <Copy size={13} />
                  {copied ? "已复制" : "复制"}
                </button>
              </div>
            </section>
            <footer>
              <Button
                variant="primary"
                disabled={!selected.source && !selected.file}
                onClick={() => {
                  if (selected.source) {
                    navigate(sourceDestination(projectId, selected.source));
                  } else if (selected.file) {
                    const params = new URLSearchParams({
                      file: selected.file.path,
                    });
                    navigate(
                      `/p/${encodeURIComponent(projectId)}/code?${params.toString()}`,
                    );
                  }
                }}
              >
                <Code2 size={15} />
                打开源码
              </Button>
              <Button
                variant="secondary"
                onClick={() =>
                  navigate(
                    `${chainPath}${chainPath.includes("?") ? "&" : "?"}tab=operations`,
                  )
                }
              >
                <Link2 size={15} />
                查看原始记录
              </Button>
            </footer>
          </aside>
        ) : null}
      </section>
    </div>
  );
}
