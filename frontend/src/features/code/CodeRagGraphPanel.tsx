import {
  Braces,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  FileCode2,
  GitBranch,
  GitCommitHorizontal,
  GitFork,
  Inspect,
  Network,
  Search,
  ShieldCheck,
  TestTube2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { EmptyState } from "../../components/ui";
import { api } from "../../lib/api";
import type {
  CodeCommitDetail,
  CodeRefs,
  GitCommit,
  GraphSnapshot,
  Repository,
} from "../../lib/types";
import {
  buildCodeRagGraph,
  CODE_GRAPH_TYPES,
  type CodeGraphNodeType,
  type CodeRagEdge,
  type CodeRagNode,
} from "./codeRagGraph";
import {
  CODE_GRAPH_RELATION_LABELS,
  CODE_GRAPH_TYPE_LABELS,
  CodeRagNetworkCanvas,
} from "./CodeRagNetworkCanvas";

const TYPE_LABELS: Record<CodeGraphNodeType, string> = CODE_GRAPH_TYPE_LABELS;

const RELATIONS = [
  "HAS_BRANCH",
  "POINTS_TO",
  "HAS_SNAPSHOT",
  "HAS_COMMIT",
  "PARENT",
  "CONTAINS",
  "DEFINES",
  "CALLS",
  "IMPORTS",
  "VALIDATED_BY",
] as const;

const RELATION_LABELS = CODE_GRAPH_RELATION_LABELS;
const INSPECTOR_RELATION_PAGE_SIZE = 12;

function typeIcon(type: CodeGraphNodeType, size = 15) {
  if (type === "Repository") return <GitFork size={size} />;
  if (type === "Branch") return <GitBranch size={size} />;
  if (type === "WorktreeSnapshot") return <CircleDot size={size} />;
  if (type === "Commit") return <GitCommitHorizontal size={size} />;
  if (type === "FileVersion") return <FileCode2 size={size} />;
  if (type === "CodeSymbol") return <Braces size={size} />;
  if (type === "TestResult") return <TestTube2 size={size} />;
  return <Inspect size={size} />;
}

function safeMetadata(node: CodeRagNode) {
  const labels: Record<string, string> = {
    default_branch: "默认分支",
    generation: "Generation",
    status: "状态",
    author: "作者",
    committed_at: "提交时间",
    diff_count: "Diff 数",
    test_count: "测试数",
    is_default: "默认分支",
    observed_at: "观测时间",
    calls: "调用数",
    imports: "导入数",
    references: "引用数",
    line_count: "行数",
    change_type: "变更类型",
    affected_symbols: "受影响 Symbol",
    exit_code: "退出码",
    duration_ms: "耗时 ms",
    framework: "测试框架",
    authority: "来源绑定",
  };
  return Object.entries(labels)
    .map(([key, label]) => ({ label, value: node.metadata[key] }))
    .filter(({ value }) =>
      ["string", "number", "boolean"].includes(typeof value),
    )
    .slice(0, 8);
}

function mergeSnapshots(
  snapshot: GraphSnapshot | undefined,
  neighbors:
    | { nodes: GraphSnapshot["nodes"]; edges: GraphSnapshot["edges"] }
    | undefined,
) {
  if (!snapshot || !neighbors) return snapshot;
  const nodeMap = new Map(snapshot.nodes.map((node) => [node.id, node]));
  const edgeMap = new Map(snapshot.edges.map((edge) => [edge.id, edge]));
  for (const node of neighbors.nodes) {
    if (node.domain === "code") nodeMap.set(node.id, node);
  }
  for (const edge of neighbors.edges) {
    if (edge.domain === "code") edgeMap.set(edge.id, edge);
  }
  return {
    ...snapshot,
    nodes: [...nodeMap.values()],
    edges: [...edgeMap.values()],
  };
}

function GraphInspector({
  node,
  nodes,
  edges,
  onSelect,
  onOpenFile,
  expansion,
  onExpand,
}: {
  node?: CodeRagNode;
  nodes: CodeRagNode[];
  edges: CodeRagEdge[];
  onSelect: (id: string) => void;
  onOpenFile: (path: string, version?: string | null) => void;
  expansion: {
    canExpand: boolean;
    busy: boolean;
    loaded: number;
    total: number | null;
    hasMore: boolean;
    error: boolean;
  };
  onExpand: () => void;
}) {
  const [relationPage, setRelationPage] = useState(0);
  useEffect(() => setRelationPage(0), [node?.id]);
  if (!node) {
    return (
      <aside className="code-graph-inspector code-graph-inspector--empty">
        <Network size={24} />
        <h3>选择一个节点</h3>
        <p>查看版本、定位符、权威来源以及入边与出边。</p>
      </aside>
    );
  }
  const nodeMap = new Map(nodes.map((item) => [item.id, item]));
  const related = edges.filter(
    (edge) => edge.source === node.id || edge.target === node.id,
  );
  const relationPageCount = Math.max(
    1,
    Math.ceil(related.length / INSPECTOR_RELATION_PAGE_SIZE),
  );
  const activeRelationPage = Math.min(relationPage, relationPageCount - 1);
  const visibleRelated = related.slice(
    activeRelationPage * INSPECTOR_RELATION_PAGE_SIZE,
    (activeRelationPage + 1) * INSPECTOR_RELATION_PAGE_SIZE,
  );
  return (
    <aside className="code-graph-inspector" aria-label="代码图谱节点详情">
      <header>
        <span className={`code-graph-node-mark is-${node.type}`}>
          {typeIcon(node.type, 17)}
        </span>
        <div>
          <small>{TYPE_LABELS[node.type]}</small>
          <h3>{node.label}</h3>
        </div>
      </header>
      <div className="code-graph-inspector__scroll">
        <section className="code-graph-authority">
          <span>
            <ShieldCheck size={14} />
            {node.origin === "graph"
              ? "图谱权威数据"
              : node.origin === "git"
                ? "Git 权威数据"
                : "权威数据投影"}
          </span>
          <p>
            {node.origin === "projection"
              ? "由仓库、Ref、Generation 与节点版本确定性组合；不会冒充独立持久实体。"
              : "来自当前项目 ACL 范围内的服务响应。"}
          </p>
        </section>
        <section>
          <h4>定位与版本</h4>
          <dl className="code-graph-facts">
            <div>
              <dt>Locator</dt>
              <dd>{node.locator}</dd>
            </div>
            <div>
              <dt>Version</dt>
              <dd>{node.version || "未提供"}</dd>
            </div>
            {node.path ? (
              <div>
                <dt>Path</dt>
                <dd>{node.path}</dd>
              </div>
            ) : null}
            {node.startLine ? (
              <div>
                <dt>Span</dt>
                <dd>
                  L{node.startLine}–{node.endLine || node.startLine}
                </dd>
              </div>
            ) : null}
          </dl>
          {safeMetadata(node).length ? (
            <dl className="code-graph-facts code-graph-facts--compact">
              {safeMetadata(node).map(({ label, value }) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          ) : null}
        </section>
        <section>
          <h4>
            关系证据 <span>{related.length}</span>
          </h4>
          <div className="code-graph-relation-list">
            {related.length ? (
              visibleRelated.map((edge) => {
                const outgoing = edge.source === node.id;
                const target = nodeMap.get(
                  outgoing ? edge.target : edge.source,
                );
                return (
                  <button
                    type="button"
                    key={edge.id}
                    disabled={!target}
                    onClick={() => target && onSelect(target.id)}
                  >
                    <span>{outgoing ? "出" : "入"}</span>
                    <div>
                      <strong>
                        {RELATION_LABELS[edge.predicate] || edge.predicate}
                      </strong>
                      <small>{target?.label || "当前抽样未载入端点"}</small>
                    </div>
                    <ChevronRight size={14} />
                  </button>
                );
              })
            ) : (
              <p>当前节点尚未返回已载入关系，可在本页继续展开邻域。</p>
            )}
          </div>
          {relationPageCount > 1 ? (
            <nav
              className="code-graph-relation-pagination"
              aria-label="关系证据分页"
            >
              <button
                type="button"
                aria-label="上一页关系"
                disabled={activeRelationPage === 0}
                onClick={() => setRelationPage(activeRelationPage - 1)}
              >
                <ChevronLeft size={13} />
              </button>
              <span>
                {activeRelationPage + 1} / {relationPageCount}
              </span>
              <button
                type="button"
                aria-label="下一页关系"
                disabled={activeRelationPage + 1 >= relationPageCount}
                onClick={() => setRelationPage(activeRelationPage + 1)}
              >
                <ChevronRight size={13} />
              </button>
            </nav>
          ) : null}
        </section>
        <section className="code-graph-inspector__actions">
          <button
            type="button"
            className="is-primary"
            disabled={
              !expansion.canExpand || expansion.busy || !expansion.hasMore
            }
            onClick={onExpand}
          >
            <Network size={15} />
            {expansion.busy
              ? "正在当前图谱展开…"
              : expansion.loaded > 0 && expansion.hasMore
                ? "继续加载相邻节点"
                : expansion.loaded > 0
                  ? "当前邻域已载入"
                  : expansion.canExpand
                    ? "在当前图谱展开关系"
                    : "当前节点关系已在画布中"}
          </button>
          {expansion.loaded > 0 ? (
            <small>
              当前新增 {expansion.loaded} 个节点
              {expansion.total === null ? "" : ` / 邻域共 ${expansion.total}`}
            </small>
          ) : null}
          {expansion.error ? (
            <small role="alert">邻域读取失败，未添加不可信结果。</small>
          ) : null}
          {node.path ? (
            <button
              type="button"
              onClick={() => onOpenFile(node.path || "", node.version)}
            >
              <FileCode2 size={15} /> 打开文件版本
            </button>
          ) : null}
        </section>
      </div>
    </aside>
  );
}

export function CodeRagGraphPanel({
  repository,
  refs,
  history,
  commitDetails,
  snapshot,
  loading,
  error,
  onRetry,
  onOpenFile,
}: {
  repository: Repository;
  refs?: CodeRefs;
  history: GitCommit[];
  commitDetails: CodeCommitDetail[];
  snapshot?: GraphSnapshot;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  onOpenFile: (path: string, version?: string | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [enabledTypes, setEnabledTypes] = useState<Set<CodeGraphNodeType>>(
    () => new Set(CODE_GRAPH_TYPES),
  );
  const [enabledRelations, setEnabledRelations] = useState<Set<string>>(
    () => new Set(RELATIONS),
  );
  const [neighborNodes, setNeighborNodes] = useState<GraphSnapshot["nodes"]>(
    [],
  );
  const [neighborEdges, setNeighborEdges] = useState<GraphSnapshot["edges"]>(
    [],
  );
  const [neighborPages, setNeighborPages] = useState<
    Record<
      string,
      {
        nextCursor: number;
        total: number | null;
        hasMore: boolean;
        loaded: number;
      }
    >
  >({});
  const [neighborBusy, setNeighborBusy] = useState<string | null>(null);
  const [neighborError, setNeighborError] = useState<string | null>(null);

  useEffect(() => {
    setSelectedId(null);
    setNeighborNodes([]);
    setNeighborEdges([]);
    setNeighborPages({});
    setNeighborBusy(null);
    setNeighborError(null);
  }, [repository.id]);

  const mergedSnapshot = useMemo(
    () =>
      mergeSnapshots(snapshot, { nodes: neighborNodes, edges: neighborEdges }),
    [neighborEdges, neighborNodes, snapshot],
  );
  const expandableNodeIds = useMemo(
    () =>
      new Set(
        (mergedSnapshot?.nodes || [])
          .filter((node) => node.domain === "code")
          .map((node) => node.id),
      ),
    [mergedSnapshot?.nodes],
  );
  const priorityNodeIds = useMemo(
    () =>
      new Set([
        ...neighborNodes
          .filter((node) => node.domain === "code")
          .map((node) => node.id),
        ...(selectedId ? [selectedId] : []),
      ]),
    [neighborNodes, selectedId],
  );
  const model = useMemo(
    () =>
      buildCodeRagGraph({
        repository,
        refs,
        history,
        commitDetails,
        snapshot: mergedSnapshot,
        query,
        enabledTypes,
        enabledRelations,
        priorityNodeIds,
      }),
    [
      commitDetails,
      enabledRelations,
      enabledTypes,
      history,
      mergedSnapshot,
      query,
      refs,
      repository,
      priorityNodeIds,
    ],
  );
  const nodeMap = useMemo(
    () => new Map(model.nodes.map((node) => [node.id, node])),
    [model.nodes],
  );
  const selectedNode = selectedId ? nodeMap.get(selectedId) : undefined;
  const connectedIds = useMemo(() => {
    const result = new Set<string>();
    if (!selectedNode) return result;
    result.add(selectedNode.id);
    for (const edge of model.edges) {
      if (edge.source === selectedNode.id) result.add(edge.target);
      if (edge.target === selectedNode.id) result.add(edge.source);
    }
    return result;
  }, [model.edges, selectedNode]);

  const toggleType = (type: CodeGraphNodeType) => {
    setEnabledTypes((current) => {
      const next = new Set(current);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };
  const toggleRelation = (relation: string) => {
    setEnabledRelations((current) => {
      const next = new Set(current);
      if (next.has(relation)) next.delete(relation);
      else next.add(relation);
      return next;
    });
  };
  useEffect(() => {
    if (selectedId && !nodeMap.has(selectedId)) setSelectedId(null);
  }, [nodeMap, selectedId]);

  const expandSelected = useCallback(async () => {
    if (!selectedId || !expandableNodeIds.has(selectedId) || neighborBusy) {
      return;
    }
    const page = neighborPages[selectedId];
    if (page && !page.hasMore) return;
    setNeighborBusy(selectedId);
    setNeighborError(null);
    try {
      const response = await api.graph.neighbors(
        selectedId,
        page?.nextCursor || 0,
        80,
      );
      const acceptedNodes = response.nodes.filter(
        (node) => node.domain === "code",
      );
      const acceptedEdges = response.edges.filter(
        (edge) => edge.domain === "code",
      );
      setNeighborNodes((current) => {
        const values = new Map(current.map((node) => [node.id, node]));
        acceptedNodes.forEach((node) => values.set(node.id, node));
        return [...values.values()];
      });
      setNeighborEdges((current) => {
        const values = new Map(current.map((edge) => [edge.id, edge]));
        acceptedEdges.forEach((edge) => values.set(edge.id, edge));
        return [...values.values()];
      });
      setNeighborPages((current) => ({
        ...current,
        [selectedId]: {
          nextCursor: response.next_cursor,
          total: response.total,
          hasMore: response.has_more,
          loaded: (current[selectedId]?.loaded || 0) + acceptedNodes.length,
        },
      }));
    } catch {
      setNeighborError(selectedId);
    } finally {
      setNeighborBusy(null);
    }
  }, [expandableNodeIds, neighborBusy, neighborPages, selectedId]);

  useEffect(() => {
    if (
      selectedId &&
      expandableNodeIds.has(selectedId) &&
      !neighborPages[selectedId] &&
      !neighborBusy
    ) {
      void expandSelected();
    }
  }, [
    expandableNodeIds,
    expandSelected,
    neighborBusy,
    neighborPages,
    selectedId,
  ]);

  if (loading) {
    return (
      <div className="code-graph-loading">正在读取代码图谱与版本权威数据…</div>
    );
  }
  if (error || !snapshot) {
    return (
      <EmptyState
        icon={<Network size={24} />}
        title="代码图谱不可用"
        description="图谱请求失败；不会用文件列表或演示数据伪造关系。"
        action={<button onClick={onRetry}>重新读取图谱</button>}
      />
    );
  }

  return (
    <section className="code-rag-graph" aria-label="Code 仓库 RAG 图谱">
      <aside className="code-graph-controls">
        <header>
          <Network size={18} />
          <div>
            <strong>仓库知识结构</strong>
            <small>版本、代码、变更与验证</small>
          </div>
        </header>
        <label className="code-graph-search">
          <Search size={15} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索节点、路径、版本"
          />
        </label>
        <section>
          <h3>实体层</h3>
          <div className="code-graph-type-list">
            {CODE_GRAPH_TYPES.map((type) => (
              <button
                type="button"
                key={type}
                className={enabledTypes.has(type) ? "is-active" : ""}
                onClick={() => toggleType(type)}
              >
                <span>{typeIcon(type)}</span>
                <strong>{TYPE_LABELS[type]}</strong>
                <em>
                  {model.loadedCounts[type]} / {model.counts[type]}
                </em>
              </button>
            ))}
          </div>
        </section>
        <section>
          <h3>关系层</h3>
          <div className="code-graph-relation-filters">
            {RELATIONS.map((relation) => (
              <button
                type="button"
                key={relation}
                className={enabledRelations.has(relation) ? "is-active" : ""}
                onClick={() => toggleRelation(relation)}
              >
                {RELATION_LABELS[relation]}
              </button>
            ))}
          </div>
        </section>
        <footer>
          <ShieldCheck size={14} />
          <span>
            {model.sampled
              ? "大图谱已抽样，选择节点可加载邻居"
              : "当前图谱已完整载入"}
          </span>
        </footer>
      </aside>

      <CodeRagNetworkCanvas
        repositoryName={repository.name}
        nodes={model.nodes}
        edges={model.edges}
        selectedId={selectedId}
        connectedIds={connectedIds}
        fetching={neighborBusy !== null}
        onSelect={setSelectedId}
      />

      <GraphInspector
        node={selectedNode}
        nodes={model.nodes}
        edges={model.edges}
        onSelect={setSelectedId}
        onOpenFile={onOpenFile}
        expansion={{
          canExpand: Boolean(selectedId && expandableNodeIds.has(selectedId)),
          busy: neighborBusy === selectedId,
          loaded: selectedId ? neighborPages[selectedId]?.loaded || 0 : 0,
          total: selectedId ? (neighborPages[selectedId]?.total ?? null) : null,
          hasMore: selectedId
            ? (neighborPages[selectedId]?.hasMore ?? true)
            : false,
          error: neighborError === selectedId,
        }}
        onExpand={() => void expandSelected()}
      />
    </section>
  );
}
