import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  ResearchMapEdge,
  ResearchMapNode,
} from "./researchMapModel";
import {
  buildVectorLayout,
  simulateVectorLayoutStep,
  vectorLayoutBounds,
  type VectorLayoutPoint,
} from "./codeVectorGraphModel";

export type CodeVectorGraphHandle = {
  zoomIn: () => void;
  zoomOut: () => void;
  fit: () => void;
  reset: () => void;
};

type Props = {
  nodes: ResearchMapNode[];
  edges: ResearchMapEdge[];
  focusId?: string | null;
  selectedId?: string | null;
  onSelect: (node: ResearchMapNode) => void;
  onActivate: (node: ResearchMapNode) => void;
  onClearSelection: () => void;
};

type Camera = { x: number; y: number; scale: number };

type ThemeColors = {
  background: string;
  surface: string;
  line: string;
  text: string;
  muted: string;
  accent: string;
  code: string;
  green: string;
  amber: string;
  purple: string;
  red: string;
};

const DEFAULT_CAMERA: Camera = { x: 0, y: 0, scale: 1 };

function readThemeColors(container: HTMLElement): ThemeColors {
  const style = getComputedStyle(container);
  const read = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    background: read("--bg", "#f7f8fa"),
    surface: read("--surface", "#ffffff"),
    line: read("--line-strong", "#cfd5dd"),
    text: read("--text", "#17202b"),
    muted: read("--text-3", "#718096"),
    accent: read("--accent", "#2563eb"),
    code: read("--cyan", "#0e8792"),
    green: read("--green", "#20835d"),
    amber: read("--amber", "#b7791f"),
    purple: read("--purple", "#7655bd"),
    red: read("--red", "#c94b52"),
  };
}

function nodeColor(node: ResearchMapNode, colors: ThemeColors): string {
  if (node.type === "SemanticQuery" || node.domain === "query") return colors.purple;
  if (node.domain === "code") return colors.code;
  if (node.domain === "codex") return colors.purple;
  if (node.domain === "experiment") return colors.green;
  if (node.domain === "document") return colors.amber;
  if (node.domain === "review") return colors.red;
  return colors.accent;
}

function edgeColor(edge: ResearchMapEdge, colors: ThemeColors): string {
  if (edge.predicate === "CALLS") return colors.accent;
  if (edge.predicate === "REFERENCES") return colors.green;
  if (edge.predicate === "IMPORTS") return colors.amber;
  if (edge.predicate === "SEMANTIC_MATCH") return colors.purple;
  if (edge.predicate === "SEMANTIC_SIMILAR") return colors.purple;
  return colors.muted;
}

function nodeGlyph(node: ResearchMapNode): string {
  if (node.type === "SemanticQuery") return "⌕";
  if (node.type === "CodeClass") return "C";
  if (node.type === "CodeMethod") return "m";
  if (node.type === "CodeFunction") return "ƒ";
  if (node.type === "CodeInterface") return "I";
  if (node.type === "FileVersion" || node.type === "CodeFileGroup") return "F";
  if (node.type === "Repository") return "R";
  return "·";
}

function compactNodeLabel(node: ResearchMapNode): string {
  if (node.type === "SemanticQuery") return node.label;
  const parts = node.label.split(/[.:/]/).filter(Boolean);
  if (parts.length >= 2) return parts.slice(-2).join(".");
  return node.label;
}

function screenPoint(
  point: VectorLayoutPoint,
  camera: Camera,
  width: number,
  height: number,
): { x: number; y: number } {
  return {
    x: width / 2 + camera.x + point.x * camera.scale,
    y: height / 2 + camera.y + point.y * camera.scale,
  };
}

export const CodeVectorGraph = forwardRef<CodeVectorGraphHandle, Props>(
  function CodeVectorGraph(
    {
      nodes,
      edges,
      focusId,
      selectedId,
      onSelect,
      onActivate,
      onClearSelection,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const layoutRef = useRef(buildVectorLayout(nodes, edges, focusId));
    const cameraRef = useRef<Camera>({ ...DEFAULT_CAMERA });
    const pointerRef = useRef<{
      id: number;
      startX: number;
      startY: number;
      originX: number;
      originY: number;
      nodeId: string | null;
      moved: boolean;
    } | null>(null);
    const hoveredRef = useRef<string | null>(null);
    const selectedRef = useRef<string | null>(selectedId || null);
    const sizeRef = useRef({ width: 1, height: 1, dpr: 1 });
    const colorsRef = useRef<ThemeColors>({
      background: "#f7f8fa",
      surface: "#ffffff",
      line: "#cfd5dd",
      text: "#17202b",
      muted: "#718096",
      accent: "#2563eb",
      code: "#0e8792",
      green: "#20835d",
      amber: "#b7791f",
      purple: "#7655bd",
      red: "#c94b52",
    });
    const [cameraLabel, setCameraLabel] = useState(100);

    const nodeById = useMemo(
      () => new Map(nodes.map((node) => [node.id, node])),
      [nodes],
    );
    const selectedNode = selectedId ? nodeById.get(selectedId) : null;
    const edgeByEndpoint = useMemo(() => {
      const values = new Map<string, Set<string>>();
      edges.forEach((edge) => {
        if (!values.has(edge.source)) values.set(edge.source, new Set());
        if (!values.has(edge.target)) values.set(edge.target, new Set());
        values.get(edge.source)!.add(edge.target);
        values.get(edge.target)!.add(edge.source);
      });
      return values;
    }, [edges]);
    const persistentLabels = useMemo(() => new Set(
      [...nodes]
        .sort((left, right) => {
          const leftDegree = edgeByEndpoint.get(left.id)?.size || 0;
          const rightDegree = edgeByEndpoint.get(right.id)?.size || 0;
          return rightDegree - leftDegree || left.label.localeCompare(right.label);
        })
        .slice(
          0,
          Math.min(nodes.length <= 80 ? 8 : nodes.length <= 220 ? 4 : 2, nodes.length),
        )
        .map((node) => node.id),
    ), [edgeByEndpoint, nodes]);
    const layoutSignature = useMemo(
      () => [
        focusId || "",
        ...nodes.map((node) => node.id),
        ...edges.map((edge) => `${edge.id}:${edge.confidence || ""}`),
      ].join("|"),
      [edges, focusId, nodes],
    );

    const render = useCallback(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const context = canvas.getContext("2d");
      if (!context) return;
      const { width, height, dpr } = sizeRef.current;
      const camera = cameraRef.current;
      const colors = colorsRef.current;
      const points = layoutRef.current.points;
      const activeId = hoveredRef.current || selectedRef.current;
      const neighbors = activeId ? edgeByEndpoint.get(activeId) : null;
      const labelRects: Array<{ x: number; y: number; width: number; height: number }> = [];

      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, width, height);

      edges.forEach((edge) => {
        const source = points.get(edge.source);
        const target = points.get(edge.target);
        if (!source || !target) return;
        const start = screenPoint(source, camera, width, height);
        const end = screenPoint(target, camera, width, height);
        const selected = Boolean(
          activeId && (edge.source === activeId || edge.target === activeId),
        );
        const muted = Boolean(
          activeId &&
          edge.source !== activeId &&
          edge.target !== activeId &&
          !neighbors?.has(edge.source) &&
          !neighbors?.has(edge.target),
        );
        const semantic = edge.predicate.startsWith("SEMANTIC_");
        context.save();
        context.globalAlpha = muted ? 0.07 : selected ? 0.92 : semantic ? 0.28 : 0.42;
        context.strokeStyle = edgeColor(edge, colors);
        context.lineWidth = selected ? 2.15 : semantic ? 1 : 1.25;
        context.setLineDash(
          edge.predicate === "SEMANTIC_SIMILAR"
            ? [3, 6]
            : edge.predicate === "SEMANTIC_MATCH"
              ? [7, 5]
              : [],
        );
        context.beginPath();
        const dx = end.x - start.x;
        const dy = end.y - start.y;
        const bend = Math.min(42, Math.hypot(dx, dy) * 0.12);
        const controlX = (start.x + end.x) / 2 - (dy / Math.max(1, Math.hypot(dx, dy))) * bend;
        const controlY = (start.y + end.y) / 2 + (dx / Math.max(1, Math.hypot(dx, dy))) * bend;
        context.moveTo(start.x, start.y);
        context.quadraticCurveTo(controlX, controlY, end.x, end.y);
        context.stroke();
        context.restore();
      });

      const ordered = [...points.values()].sort((left, right) => {
        const leftActive = left.id === activeId ? 1 : 0;
        const rightActive = right.id === activeId ? 1 : 0;
        return leftActive - rightActive;
      });
      ordered.forEach((point) => {
        const node = nodeById.get(point.id);
        if (!node) return;
        const screen = screenPoint(point, camera, width, height);
        const radius = point.radius * Math.max(0.78, Math.min(1.28, camera.scale));
        const active = point.id === activeId;
        const muted = Boolean(
          activeId && point.id !== activeId && !neighbors?.has(point.id),
        );
        const color = nodeColor(node, colors);
        context.save();
        context.globalAlpha = muted ? 0.18 : 1;
        if (active) {
          context.globalAlpha = 0.16;
          context.fillStyle = color;
          context.beginPath();
          context.arc(screen.x, screen.y, radius + 9, 0, Math.PI * 2);
          context.fill();
          context.globalAlpha = 1;
        }
        context.fillStyle = node.type === "SemanticQuery" ? color : colors.surface;
        context.strokeStyle = color;
        context.lineWidth = active ? 3 : point.degree >= 4 ? 2 : 1.5;
        context.beginPath();
        context.arc(screen.x, screen.y, radius, 0, Math.PI * 2);
        context.fill();
        context.stroke();

        context.fillStyle = node.type === "SemanticQuery" ? "#ffffff" : color;
        context.font = `700 ${Math.max(11, radius * 0.72)}px "IBM Plex Sans", "Noto Sans SC", sans-serif`;
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(nodeGlyph(node), screen.x, screen.y + 0.5);

        const showLabel =
          active ||
          node.type === "SemanticQuery" ||
          persistentLabels.has(node.id) ||
          camera.scale >= 1.08;
        if (showLabel) {
          const compact = compactNodeLabel(node);
          const label = compact.length > 26 ? `${compact.slice(0, 25)}…` : compact;
          context.font = `650 ${active ? 13 : 12}px "IBM Plex Sans", "Noto Sans SC", sans-serif`;
          const labelWidth = Math.min(190, context.measureText(label).width + 15);
          const labelOnLeft = point.x < -24;
          const labelX = labelOnLeft
            ? screen.x - radius - labelWidth - 8
            : screen.x + radius + 8;
          const labelY = screen.y - 9;
          const overlapsLabel = labelRects.some(
            (rect) =>
              labelX < rect.x + rect.width + 5 &&
              labelX + labelWidth + 5 > rect.x &&
              labelY < rect.y + rect.height + 5 &&
              labelY + 27 > rect.y,
          );
          if (active || node.type === "SemanticQuery" || !overlapsLabel) {
            labelRects.push({ x: labelX, y: labelY, width: labelWidth, height: 22 });
            context.globalAlpha = muted ? 0.2 : 0.94;
            context.fillStyle = colors.surface;
            context.strokeStyle = colors.line;
            context.lineWidth = 1;
            context.beginPath();
            context.roundRect(labelX, labelY, labelWidth, 22, 4);
            context.fill();
            if (active) context.stroke();
            context.fillStyle = colors.text;
            context.textAlign = "left";
            context.textBaseline = "middle";
            context.fillText(label, labelX + 7, labelY + 11, labelWidth - 12);
          }
        }
        context.restore();
      });
    }, [edgeByEndpoint, edges, nodeById, persistentLabels]);

    const fit = useCallback(() => {
      const { width, height } = sizeRef.current;
      const bounds = vectorLayoutBounds(layoutRef.current.points);
      const contentWidth = Math.max(1, bounds.maxX - bounds.minX);
      const contentHeight = Math.max(1, bounds.maxY - bounds.minY);
      const scale = Math.max(
        nodes.length <= 80 ? 0.7 : 0.2,
        Math.min(1.35, (width - 230) / contentWidth, (height - 190) / contentHeight),
      );
      cameraRef.current = {
        scale,
        x: -((bounds.minX + bounds.maxX) / 2) * scale,
        y: -((bounds.minY + bounds.maxY) / 2) * scale,
      };
      setCameraLabel(Math.round(scale * 100));
      render();
    }, [nodes.length, render]);

    const setScale = useCallback((scale: number) => {
      cameraRef.current.scale = Math.max(0.18, Math.min(3.4, scale));
      setCameraLabel(Math.round(cameraRef.current.scale * 100));
      render();
    }, [render]);

    const reset = useCallback(() => {
      layoutRef.current = buildVectorLayout(nodes, edges, focusId);
      cameraRef.current = { ...DEFAULT_CAMERA };
      selectedRef.current = null;
      onClearSelection();
      setCameraLabel(100);
    }, [edges, focusId, nodes, onClearSelection]);

    useImperativeHandle(ref, () => ({
      zoomIn: () => setScale(cameraRef.current.scale * 1.18),
      zoomOut: () => setScale(cameraRef.current.scale / 1.18),
      fit,
      reset,
    }), [fit, reset, setScale]);

    useEffect(() => {
      selectedRef.current = selectedId || null;
      render();
    }, [render, selectedId]);

    useEffect(() => {
      layoutRef.current = buildVectorLayout(nodes, edges, focusId);
      cameraRef.current = { ...DEFAULT_CAMERA };
      setCameraLabel(100);
      const maxTicks = nodes.length <= 80 ? 120 : nodes.length <= 350 ? 72 : 24;
      for (let tick = 1; tick <= maxTicks; tick += 1) {
        const alpha = Math.max(0.035, 1 - tick / maxTicks);
        simulateVectorLayoutStep(
          layoutRef.current.points,
          layoutRef.current.links,
          alpha,
        );
      }
      fit();
    }, [layoutSignature]);

    useEffect(() => {
      const container = containerRef.current;
      const canvas = canvasRef.current;
      if (!container || !canvas) return;
      const resize = () => {
        const bounds = container.getBoundingClientRect();
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const resized =
          Math.abs(sizeRef.current.width - bounds.width) > 1 ||
          Math.abs(sizeRef.current.height - bounds.height) > 1;
        sizeRef.current = {
          width: Math.max(1, bounds.width),
          height: Math.max(1, bounds.height),
          dpr,
        };
        canvas.width = Math.round(bounds.width * dpr);
        canvas.height = Math.round(bounds.height * dpr);
        canvas.style.width = `${bounds.width}px`;
        canvas.style.height = `${bounds.height}px`;
        colorsRef.current = readThemeColors(container);
        if (resized) fit();
        else render();
      };
      const resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(container);
      const themeObserver = new MutationObserver(resize);
      themeObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["class", "data-theme"],
      });
      resize();
      return () => {
        resizeObserver.disconnect();
        themeObserver.disconnect();
      };
    }, [fit, render]);

    const hitTest = useCallback((clientX: number, clientY: number) => {
      const container = containerRef.current;
      if (!container) return null;
      const bounds = container.getBoundingClientRect();
      const x = clientX - bounds.left;
      const y = clientY - bounds.top;
      const { width, height } = sizeRef.current;
      const camera = cameraRef.current;
      return [...layoutRef.current.points.values()]
        .reverse()
        .find((point) => {
          const screen = screenPoint(point, camera, width, height);
          return Math.hypot(screen.x - x, screen.y - y) <= point.radius * camera.scale + 10;
        }) || null;
    }, []);

    return (
      <div
        ref={containerRef}
        className="code-vector-graph"
        data-testid="code-vector-graph"
      >
        <canvas
          ref={canvasRef}
          aria-label="代码向量关系图；拖动画布移动，滚轮缩放，点击节点查看详情，双击展开关系"
          onWheel={(event) => {
            event.preventDefault();
            const bounds = event.currentTarget.getBoundingClientRect();
            const cursorX = event.clientX - bounds.left - bounds.width / 2;
            const cursorY = event.clientY - bounds.top - bounds.height / 2;
            const current = cameraRef.current;
            const nextScale = Math.max(
              0.18,
              Math.min(3.4, current.scale * Math.exp(-event.deltaY * 0.0013)),
            );
            const ratio = nextScale / current.scale;
            cameraRef.current = {
              scale: nextScale,
              x: cursorX - (cursorX - current.x) * ratio,
              y: cursorY - (cursorY - current.y) * ratio,
            };
            setCameraLabel(Math.round(nextScale * 100));
            render();
          }}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            const point = hitTest(event.clientX, event.clientY);
            const camera = cameraRef.current;
            pointerRef.current = {
              id: event.pointerId,
              startX: event.clientX,
              startY: event.clientY,
              originX: point ? point.x : camera.x,
              originY: point ? point.y : camera.y,
              nodeId: point?.id || null,
              moved: false,
            };
            if (point) point.pinned = true;
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            const pointer = pointerRef.current;
            if (!pointer || pointer.id !== event.pointerId) {
              const hovered = hitTest(event.clientX, event.clientY)?.id || null;
              if (hoveredRef.current !== hovered) {
                hoveredRef.current = hovered;
                event.currentTarget.style.cursor = hovered ? "pointer" : "grab";
                render();
              }
              return;
            }
            const dx = event.clientX - pointer.startX;
            const dy = event.clientY - pointer.startY;
            if (Math.abs(dx) + Math.abs(dy) > 4) pointer.moved = true;
            if (pointer.nodeId) {
              const point = layoutRef.current.points.get(pointer.nodeId);
              if (point) {
                point.x = pointer.originX + dx / cameraRef.current.scale;
                point.y = pointer.originY + dy / cameraRef.current.scale;
                point.vx = 0;
                point.vy = 0;
              }
            } else if (pointer.moved) {
              cameraRef.current.x = pointer.originX + dx;
              cameraRef.current.y = pointer.originY + dy;
            }
            render();
          }}
          onPointerUp={(event) => {
            const pointer = pointerRef.current;
            if (!pointer || pointer.id !== event.pointerId) return;
            const point = pointer.nodeId
              ? layoutRef.current.points.get(pointer.nodeId)
              : null;
            if (point) point.pinned = false;
            pointerRef.current = null;
            if (!pointer.moved) {
              if (pointer.nodeId) {
                const node = nodeById.get(pointer.nodeId);
                if (node) onSelect(node);
              } else {
                onClearSelection();
              }
            }
            render();
          }}
          onPointerCancel={() => {
            const pointer = pointerRef.current;
            if (pointer?.nodeId) {
              const point = layoutRef.current.points.get(pointer.nodeId);
              if (point) point.pinned = false;
            }
            pointerRef.current = null;
          }}
          onDoubleClick={(event) => {
            const point = hitTest(event.clientX, event.clientY);
            const node = point ? nodeById.get(point.id) : null;
            if (node) onActivate(node);
          }}
        />
        <nav
          className="code-vector-graph__accessible"
          aria-label="向量图节点导航"
        >
          {nodes.map((node) => (
            <button
              key={node.id}
              onClick={() => onSelect(node)}
              onDoubleClick={() => onActivate(node)}
            >
              {node.label}，{node.type}，{edgeByEndpoint.get(node.id)?.size || 0} 条连接
            </button>
          ))}
        </nav>
        <div className="code-vector-graph__mode">
          <strong>{selectedNode?.label || "语义向量网络"}</strong>
          <span>
            {selectedNode
              ? `${selectedNode.type} · ${edgeByEndpoint.get(selectedNode.id)?.size || 0} 条直接关系`
              : `${nodes.length} 节点 · ${edges.length} 连接 · ${cameraLabel}%`}
          </span>
        </div>
        <div className="code-vector-graph__legend" aria-label="向量图关系说明">
          <span><i className="is-static" />静态关系</span>
          <span><i className="is-vector" />向量相似</span>
          <span><i className="is-query" />查询命中</span>
        </div>
      </div>
    );
  },
);
