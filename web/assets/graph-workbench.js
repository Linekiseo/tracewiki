const ragGraphState = {
  domain: "overview",
  mode: "relations",
  query: "",
  data: null,
  selectedNodeId: null,
  selectedNodeIds: new Set(),
  hiddenNodeIds: new Set(),
  hiddenTypes: new Set(),
  pathNodeIds: new Set(),
  pathEdgeIds: new Set(),
  pathStartId: null,
  pathMode: false,
  focusNodeId: null,
  layout: "force",
  seedLimit: 60,
  requestSerial: 0,
  installed: false,
  statsLoaded: false,
  viewBox: {x: 0, y: 0, width: 1200, height: 760},
  drag: null,
  positions: new Map(),
  positionKey: "",
  pinnedNodeIds: new Set(),
  neighborExpansion: new Map(),
  visibleNodeCap: null,
  fitOnNextRender: true,
  suppressClick: false,
};

const ragGraphDomainLabel = domain => ({
  overview: "跨源总览",
  code: "代码图谱",
  codex: "Codex 会话图谱",
  research: "科研图谱",
  document: "文档图谱",
  experiment: "实验图谱",
  query: "语义查询",
  external: "外部证据",
})[domain] || domain;

const ragGraphTypeLabel = type => ({
  CodeSymbol: "Symbol",
  FileVersion: "File",
  Commit: "Commit",
  DiffHunk: "Diff",
  TestResult: "Test",
  CodexThread: "Thread",
  CodexTurn: "Turn",
  UserGoal: "Goal",
  DevelopmentEpisode: "Episode",
  CommandExecution: "Command",
  ToolCall: "Tool",
  ToolResult: "Result",
  Patch: "Patch",
  FileChange: "File change",
  ValidationResult: "Validation",
  AgentMessage: "Answer",
  ScientificDocument: "Document",
  DocumentSection: "Section",
  Claim: "Claim",
  Table: "Table",
  TableCell: "Cell",
  Figure: "Figure",
  Experiment: "Experiment",
  ExperimentRun: "Run",
  Metric: "Metric",
  Artifact: "Artifact",
  SemanticQuery: "Vector query",
})[type] || type;

const ragGraphTypeColors = {
  CodeSymbol: "#1f883d",
  FileVersion: "#bf8700",
  Commit: "#8250df",
  DiffHunk: "#bc4c00",
  TestResult: "#cf222e",
  CodexThread: "#0969da",
  CodexTurn: "#218bff",
  UserGoal: "#8250df",
  DevelopmentEpisode: "#6e40c9",
  CommandExecution: "#bf8700",
  ToolCall: "#57606a",
  ToolResult: "#6e7781",
  Patch: "#bc4c00",
  FileChange: "#d15704",
  ValidationResult: "#1f883d",
  AgentMessage: "#bf3989",
  ScientificDocument: "#bf8700",
  DocumentSection: "#0969da",
  Claim: "#cf222e",
  Table: "#1f883d",
  TableCell: "#2da44e",
  Figure: "#8250df",
  Experiment: "#7956d8",
  ExperimentRun: "#1f9d8a",
  Metric: "#c78119",
  Artifact: "#d15704",
  SemanticQuery: "#8250df",
};

function ragGraphTypeColor(type) {
  return ragGraphTypeColors[type] || "#57606a";
}

function ragGraphNodeColor(node) {
  return ({
    code: "#2f9e5b",
    codex: "#3b82f6",
    document: "#8b5cf6",
    experiment: "#18a999",
    workspace: "#7956d8",
    query: "#7956d8",
  })[node?.domain] || ragGraphTypeColor(node?.type);
}

function ragGraphTypeGlyph(type) {
  return ({
    CodeSymbol:"ƒ", FileVersion:"F", Commit:"C", DiffHunk:"Δ", TestResult:"✓",
    CodexThread:"T", CodexTurn:"↳", UserGoal:"G", DevelopmentEpisode:"E",
    CommandExecution:">_", ToolCall:"⊕", ToolResult:"R", Patch:"Δ",
    FileChange:"F", ValidationResult:"✓", AgentMessage:"A",
    ScientificDocument:"D", DocumentSection:"§", Claim:"C", Table:"▦",
    TableCell:"·", Figure:"◇", Experiment:"E", ExperimentRun:"R",
    Metric:"M", Artifact:"A", SemanticQuery:"Q",
  })[type] || "•";
}

function ragGraphDisplayLabel(node) {
  const raw = String(node?.label || "未命名实体").replace(/\s+/g, " ").trim();
  if (node?.type === "CodeSymbol") {
    const parts = raw.split(".").filter(Boolean);
    return parts.slice(-2).join(".");
  }
  if (["FileVersion", "DiffHunk"].includes(node?.type)) {
    return raw.split("/").filter(Boolean).at(-1) || raw;
  }
  return raw;
}

function ragGraphLabelLines(node) {
  const label = ragGraphDisplayLabel(node);
  if (/[\u3400-\u9fff]/.test(label) && label.length > 14) {
    return [ragGraphShort(label, 14)];
  }
  if (label.length <= 24) return [label];
  if (node?.type === "CodeSymbol" && label.includes(".")) {
    const splitAt = label.lastIndexOf(".");
    return [
      ragGraphShort(label.slice(0, splitAt), 25),
      ragGraphShort(label.slice(splitAt + 1), 25),
    ];
  }
  const words = label.split(" ");
  if (words.length > 1) {
    const midpoint = Math.ceil(words.length / 2);
    return [
      ragGraphShort(words.slice(0, midpoint).join(" "), 27),
      ragGraphShort(words.slice(midpoint).join(" "), 27),
    ];
  }
  return [ragGraphShort(label.slice(0, 25), 26), ragGraphShort(label.slice(25), 26)];
}

function ragGraphCaptionWidth(node) {
  const measured = ragGraphLabelLines(node).map(label => [...label].reduce(
    (width, character) => width + (character.charCodeAt(0) > 255 ? 10.5 : 6.2),
    20,
  ));
  return Math.min(180, Math.max(76, ...measured));
}

function ragGraphShort(value, limit = 28) {
  const text = String(value || "未命名实体").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function ragGraphHash(value) {
  let hash = 2166136261;
  for (const char of String(value)) {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function ragGraphEdgeId(edge) {
  return `${edge.source}|${edge.predicate}|${edge.target}`;
}

function resetRagGraphInteraction() {
  ragGraphState.selectedNodeId = null;
  ragGraphState.selectedNodeIds.clear();
  ragGraphState.hiddenNodeIds.clear();
  ragGraphState.hiddenTypes.clear();
  ragGraphState.pathNodeIds.clear();
  ragGraphState.pathEdgeIds.clear();
  ragGraphState.pathStartId = null;
  ragGraphState.pathMode = false;
  ragGraphState.focusNodeId = null;
  closeRagGraphContext();
  const pathButton = $("#rag-graph-path-toggle");
  if (pathButton) pathButton.setAttribute("aria-pressed", "false");
}

function activateRagGraph() {
  if (!ragGraphState.statsLoaded) loadRagGraphStats();
  loadRagGraph();
}

async function loadRagGraphStats() {
  ragGraphState.statsLoaded = true;
  try {
    const stats = await api("/v1/graph/stats");
    ["overview", "code", "codex", "research"].forEach(domain => {
      const target = $(`#rag-graph-${domain}-total`);
      if (target) target.textContent = formatNumber(stats[domain]?.total_nodes || 0);
    });
  } catch (error) {
    ragGraphState.statsLoaded = false;
  }
}

async function loadRagGraph({force = false, preserveVisibleTarget = false} = {}) {
  if (!$("#rag-graph-workspace")) return;
  const query = $("#rag-graph-query").value.trim();
  ragGraphState.query = query;
  if (ragGraphState.mode === "semantic" && !query) {
    renderRagGraphPrompt();
    return;
  }
  const serial = ++ragGraphState.requestSerial;
  const visibleTarget = preserveVisibleTarget
    ? ragGraphState.visibleNodeCap
    : null;
  const overlay = $("#rag-graph-overlay");
  overlay.hidden = false;
  overlay.innerHTML = `<b>${preserveVisibleTarget ? `正在载入 ${formatNumber(visibleTarget)} 个目标节点` : ragGraphState.mode === "semantic" ? "正在计算向量语义邻域" : "正在构建关系网络"}</b><span>读取真实实体、关系、版本与索引状态</span>`;
  try {
    const params = new URLSearchParams({
      domain: ragGraphState.domain,
      mode: ragGraphState.mode,
      query,
      limit: String(preserveVisibleTarget ? visibleTarget : ragGraphState.seedLimit),
    });
    const data = await api(`/v1/graph?${params.toString()}`);
    if (serial !== ragGraphState.requestSerial) return;
    const previousPositions = ragGraphState.positions;
    ragGraphState.data = data;
    ragGraphState.positions = preserveVisibleTarget ? previousPositions : new Map();
    ragGraphState.positionKey = "";
    if (preserveVisibleTarget) {
      const totalNodes = Number(data.metadata?.total_nodes || data.nodes.length);
      ragGraphState.visibleNodeCap = data.nodes.length >= totalNodes && visibleTarget >= totalNodes
        ? null
        : visibleTarget;
      ragGraphState.fitOnNextRender = false;
    } else {
      ragGraphState.pinnedNodeIds.clear();
      ragGraphState.neighborExpansion.clear();
      ragGraphState.visibleNodeCap = null;
      ragGraphState.fitOnNextRender = true;
      resetRagGraphInteraction();
    }
    renderRagGraph();
  } catch (error) {
    if (serial !== ragGraphState.requestSerial) return;
    overlay.hidden = false;
    overlay.innerHTML = `<b>图谱读取失败</b><span>${escapeHtml(error.message)}</span>`;
  }
}

function renderRagGraphPrompt() {
  ragGraphState.data = null;
  ragGraphState.visibleNodeCap = null;
  resetRagGraphInteraction();
  $("#rag-graph-title").textContent = `${ragGraphDomainLabel(ragGraphState.domain)} · 语义邻域`;
  $("#rag-graph-summary").textContent = "输入一个实现、主题或结论，查看查询向量召回的实体及其真实关系";
  $("#rag-graph-svg").innerHTML = "";
  $("#rag-graph-categories").innerHTML = "";
  $("#rag-graph-list").innerHTML = "";
  $("#rag-graph-list-count").textContent = "0 个节点";
  $("#rag-graph-loaded-count").textContent = "0";
  $("#rag-graph-visible-count").textContent = "0";
  renderRagGraphVisibilityControl({nodes:[]});
  $("#rag-graph-overlay").hidden = false;
  $("#rag-graph-overlay").innerHTML = "<b>输入主题后构建向量语义邻域</b><span>例如 SourceAnalyzer、rerank 设计原因、nDCG 结论</span>";
  $("#rag-graph-index-state").innerHTML = "<b>向量语义邻域</b><span>虚线表示查询向量命中；实线表示来源中的真实关系。</span>";
  renderRagGraphInspector(null);
  renderRagGraphSelectionHint();
}

function renderRagGraph() {
  const data = ragGraphState.data;
  if (!data) return;
  const metadata = data.metadata || {};
  const sourceNodes = data.nodes.filter(node => node.type !== "SemanticQuery");
  const visibleData = getRagGraphVisibleData();
  const hasSourceNodes = sourceNodes.length > 0;
  const domainLabel = ragGraphDomainLabel(data.domain);
  $("#rag-graph-eyebrow").textContent = `${data.domain.toUpperCase()} · ${data.mode === "semantic" ? "VECTOR NEIGHBORHOOD" : "RELATION NETWORK"}`;
  $("#rag-graph-title").textContent = data.mode === "semantic" ? `${domainLabel} · 语义邻域` : domainLabel;
  $("#rag-graph-summary").textContent = hasSourceNodes
    ? `当前载入 ${data.nodes.length} 个节点、${data.edges.length} 条关系 · 全量索引 ${formatNumber(metadata.total_nodes)} 节点、${formatNumber(metadata.total_edges)} 关系${data.domain === "overview" ? ` · ${formatNumber(metadata.cross_source_edges || 0)} 条可见跨源关系` : ""}`
    : `${domainLabel}尚无可展示实体`;
  const vectorText = metadata.vector_views
    ? `${formatNumber(metadata.vector_views)} 向量视图 · ${metadata.embedding_model || "模型未记录"}`
    : "没有已发布的向量视图";
  $("#rag-graph-index-state").innerHTML = `<b>${visibleData.nodes.length} Visible / ${formatNumber(metadata.total_nodes)} Total</b><span>${formatNumber(metadata.total_edges)} Edges · ${escapeHtml(vectorText)} · ${data.mode === "semantic" ? "虚线语义 / 实线关系" : "真实关系网络"}</span>`;
  const totalTarget = $(`#rag-graph-${data.domain}-total`);
  if (totalTarget) totalTarget.textContent = formatNumber(metadata.total_nodes);
  $("#rag-graph-loaded-count").textContent = formatNumber(data.nodes.length);
  $("#rag-graph-total-count").textContent = formatNumber(metadata.total_nodes);
  renderRagGraphCategories();
  renderRagGraphSvg();
  renderRagGraphList();
  const selected = data.nodes.find(node => node.id === ragGraphState.selectedNodeId) || null;
  renderRagGraphInspector(selected);
  renderRagGraphSelectionHint();
  $("#rag-graph-reveal-all").hidden = !(
    ragGraphState.hiddenNodeIds.size ||
    ragGraphState.hiddenTypes.size ||
    ragGraphState.focusNodeId
  );
  const overlay = $("#rag-graph-overlay");
  overlay.hidden = hasSourceNodes;
  if (!hasSourceNodes) {
    overlay.innerHTML = `<b>${escapeHtml(domainLabel)}暂无实体</b><span>${data.domain === "document" ? "请先在科研文档中接入文档，解析后会形成 Document → Section → Claim 图谱。" : "请先完成来源同步和索引发布。"}</span>`;
  }
}

function renderRagGraphCategories() {
  const data = ragGraphState.data;
  if (!data) return;
  const counts = new Map();
  data.nodes
    .filter(node => node.type !== "SemanticQuery")
    .forEach(node => counts.set(node.type, (counts.get(node.type) || 0) + 1));
  $("#rag-graph-categories").innerHTML = [...counts.entries()]
    .sort(([left], [right]) => ragGraphTypeLabel(left).localeCompare(ragGraphTypeLabel(right)))
    .map(([type, count]) => `<button type="button" class="rag-graph-category" data-rag-graph-category="${escapeHtml(type)}"
      aria-pressed="${!ragGraphState.hiddenTypes.has(type)}" style="--node-color:${ragGraphNodeColor(data.nodes.find(node => node.type === type))}">
      ${escapeHtml(ragGraphTypeLabel(type))}<b>${count}</b>
    </button>`).join("");
}

function ragGraphLayer(type, domain) {
  const layers = {
    code: {Commit:0, FileVersion:1, DiffHunk:1, CodeSymbol:2, TestResult:3},
    codex: {CodexThread:0, CodexTurn:1, UserGoal:2, DevelopmentEpisode:2, CommandExecution:3, ToolCall:3, ToolResult:4, Patch:4, FileChange:4, ValidationResult:5, AgentMessage:5},
    document: {ScientificDocument:0, DocumentSection:1, Table:2, Figure:2, Claim:2, TableCell:3},
    experiment: {Experiment:0, ExperimentRun:1, Metric:2, Artifact:2},
  };
  return layers[domain]?.[type] ?? (domain === "external" ? 4 : 3);
}

function getRagGraphVisibleData() {
  const data = ragGraphState.data;
  if (!data) return {nodes:[], edges:[]};
  const eligibleNodes = data.nodes
    .filter(node => !ragGraphState.hiddenNodeIds.has(node.id))
    .filter(node => !ragGraphState.hiddenTypes.has(node.type));
  const requestedCap = ragGraphState.visibleNodeCap == null
    ? eligibleNodes.length
    : Math.max(1, Math.min(ragGraphState.visibleNodeCap, eligibleNodes.length));
  const cappedNodes = eligibleNodes.slice(0, requestedCap);
  const cappedIds = new Set(cappedNodes.map(node => node.id));
  const priorityIds = [
    ragGraphState.selectedNodeId,
    ragGraphState.focusNodeId,
    ragGraphState.pathStartId,
    ...ragGraphState.selectedNodeIds,
    ...ragGraphState.pathNodeIds,
    ...eligibleNodes.filter(node => node.type === "SemanticQuery").map(node => node.id),
  ].filter(Boolean);
  const protectedIds = new Set();
  priorityIds.forEach(nodeId => {
    if (!eligibleNodes.some(node => node.id === nodeId)) return;
    if (cappedIds.has(nodeId)) {
      protectedIds.add(nodeId);
      return;
    }
    const replacementIndex = cappedNodes.findLastIndex(node => !protectedIds.has(node.id));
    if (replacementIndex < 0) return;
    cappedIds.delete(cappedNodes[replacementIndex].id);
    cappedNodes[replacementIndex] = eligibleNodes.find(node => node.id === nodeId);
    cappedIds.add(nodeId);
    protectedIds.add(nodeId);
  });
  const allowedIds = new Set(cappedIds);
  if (ragGraphState.focusNodeId && allowedIds.has(ragGraphState.focusNodeId)) {
    const focusIds = new Set([ragGraphState.focusNodeId]);
    data.edges.forEach(edge => {
      if (edge.source === ragGraphState.focusNodeId) focusIds.add(edge.target);
      if (edge.target === ragGraphState.focusNodeId) focusIds.add(edge.source);
    });
    [...allowedIds].forEach(id => {
      if (!focusIds.has(id)) allowedIds.delete(id);
    });
  }
  const nodes = data.nodes.filter(node => allowedIds.has(node.id));
  const edges = data.edges.filter(edge => allowedIds.has(edge.source) && allowedIds.has(edge.target));
  return {nodes, edges};
}

function renderRagGraphVisibilityControl(visible = getRagGraphVisibleData()) {
  const slider = $("#rag-graph-visible-slider");
  const label = $("#rag-graph-visible-slider-value");
  if (!slider || !label) return;
  const loadedCount = ragGraphState.data?.nodes.length || 0;
  const totalCount = Number(ragGraphState.data?.metadata?.total_nodes || loadedCount);
  const requestedCount = ragGraphState.visibleNodeCap == null
    ? loadedCount
    : Math.min(ragGraphState.visibleNodeCap, totalCount);
  slider.min = "1";
  slider.max = String(Math.max(1, totalCount));
  slider.value = String(Math.max(1, requestedCount));
  slider.disabled = totalCount < 2;
  slider.style.setProperty(
    "--range-progress",
    `${totalCount > 1 ? ((Math.max(1, requestedCount) - 1) / (totalCount - 1)) * 100 : 100}%`,
  );
  const visibleCount = visible.nodes.length;
  label.textContent = requestedCount > loadedCount
    ? `${formatNumber(visibleCount)} / 目标 ${formatNumber(requestedCount)}`
    : requestedCount >= totalCount
      ? `${formatNumber(visibleCount)} / 全部 ${formatNumber(totalCount)}`
      : `${formatNumber(visibleCount)} / 总计 ${formatNumber(totalCount)}`;
  slider.setAttribute(
    "aria-valuetext",
    `${visibleCount} 个可见节点，目标 ${requestedCount} 个，索引共 ${totalCount} 个`,
  );
}

function buildLayeredRagGraphPositions(data, nodes) {
  const positions = new Map();
  const width = 1200;
  const height = 760;
  const groups = new Map();
  nodes.forEach(node => {
    const layer = node.type === "SemanticQuery"
      ? -1
      : ragGraphLayer(node.type, data.domain === "overview" ? node.domain : data.domain);
    if (!groups.has(layer)) groups.set(layer, []);
    groups.get(layer).push(node);
  });
  const columns = [...groups.entries()].sort(([left], [right]) => left - right);
  const columnGap = 300;
  const rowGap = 112;
  const startX = width / 2 - (columns.length - 1) * columnGap / 2;
  columns.forEach(([layer, columnNodes], columnIndex) => {
    const x = startX + columnIndex * columnGap;
    columnNodes.sort((left, right) => String(left.id).localeCompare(String(right.id)));
    const startY = height / 2 - (columnNodes.length - 1) * rowGap / 2;
    columnNodes.forEach((node, rowIndex) => {
      positions.set(node.id, {x, y:startY + rowIndex * rowGap});
    });
  });
  return positions;
}

function buildForceRagGraphPositions(data, nodes, edges) {
  const width = 1200;
  const height = 760;
  const positions = new Map();
  const groupFor = node => data.domain === "overview" ? node.domain : node.type;
  const groupOrder = [...new Set(nodes.map(groupFor))].sort();
  const spread = Math.max(1, Math.sqrt(nodes.length / 36));
  const overviewAnchors = {
    code: {x: 335, y: 365},
    codex: {x: 865, y: 365},
    document: {x: 390, y: 570},
    experiment: {x: 810, y: 570},
    workspace: {x: 600, y: 570},
    query: {x: 600, y: 355},
  };
  const typeAnchors = new Map(groupOrder.map((group, index) => {
    if (data.domain === "overview" && overviewAnchors[group]) {
      return [group, overviewAnchors[group]];
    }
    const angle = -Math.PI / 2 + index * (Math.PI * 2 / Math.max(1, groupOrder.length));
    return [group, {
      x:width / 2 + Math.cos(angle) * 345 * spread,
      y:height / 2 + Math.sin(angle) * 245 * spread,
    }];
  }));
  const ordered = [...nodes].sort((left, right) => {
    if (left.type === "SemanticQuery") return -1;
    if (right.type === "SemanticQuery") return 1;
    return Number(right.score || 0) - Number(left.score || 0) || String(left.id).localeCompare(String(right.id));
  });
  const groupIndexes = new Map();
  ordered.forEach((node, index) => {
    if (node.type === "SemanticQuery") {
      positions.set(node.id, {x:width / 2, y:height / 2});
      return;
    }
    const hash = ragGraphHash(node.id);
    if (data.mode === "semantic") {
      const resultIndex = Math.max(0, index - (ordered[0]?.type === "SemanticQuery" ? 1 : 0));
      const angle = resultIndex * 2.399963229728653;
      const radius = 128 + Math.sqrt(resultIndex) * 60;
      positions.set(node.id, {
        x: width / 2 + Math.cos(angle) * radius,
        y: height / 2 + Math.sin(angle) * radius * .72,
      });
    } else {
      const group = groupFor(node);
      const anchor = typeAnchors.get(group) || {x:width / 2, y:height / 2};
      const groupIndex = groupIndexes.get(group) || 0;
      groupIndexes.set(group, groupIndex + 1);
      const angle = groupIndex * 2.399963229728653 + ((hash % 31) / 31);
      const ring = 42 + Math.sqrt(groupIndex) * 88;
      positions.set(node.id, {
        x: anchor.x + Math.cos(angle) * ring,
        y: anchor.y + Math.sin(angle) * ring,
      });
    }
  });
  const byId = new Map(nodes.map(node => [node.id, node]));
  const captionWidths = new Map(nodes.map(node => [node.id, ragGraphCaptionWidth(node)]));
  const movable = nodes.filter(node => node.type !== "SemanticQuery");
  const collisionDistance = nodes.length > 48 ? 90 : nodes.length > 32 ? 100 : 108;
  const iterations = nodes.length > 300 ? 18 : nodes.length > 160 ? 32 : 90;
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const forces = new Map(nodes.map(node => [node.id, {x:0, y:0}]));
    for (let leftIndex = 0; leftIndex < nodes.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < nodes.length; rightIndex += 1) {
        const left = nodes[leftIndex];
        const right = nodes[rightIndex];
        const leftPosition = positions.get(left.id);
        const rightPosition = positions.get(right.id);
        let dx = rightPosition.x - leftPosition.x;
        let dy = rightPosition.y - leftPosition.y;
        const distanceSquared = Math.max(625, dx * dx + dy * dy);
        const distance = Math.sqrt(distanceSquared);
        const strength = 68000 / distanceSquared;
        dx /= distance;
        dy /= distance;
        forces.get(left.id).x -= dx * strength;
        forces.get(left.id).y -= dy * strength;
        forces.get(right.id).x += dx * strength;
        forces.get(right.id).y += dy * strength;
      }
    }
    edges.forEach(edge => {
      if (!byId.has(edge.source) || !byId.has(edge.target)) return;
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      let dx = target.x - source.x;
      let dy = target.y - source.y;
      const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const desired = edge.predicate === "SEMANTIC_MATCH" ? 180 : 145;
      const strength = (distance - desired) * .012;
      dx /= distance;
      dy /= distance;
      forces.get(edge.source).x += dx * strength;
      forces.get(edge.source).y += dy * strength;
      forces.get(edge.target).x -= dx * strength;
      forces.get(edge.target).y -= dy * strength;
    });
    movable.forEach(node => {
      const position = positions.get(node.id);
      const force = forces.get(node.id);
      const anchor = typeAnchors.get(groupFor(node)) || {x:width / 2, y:height / 2};
      if (data.mode !== "semantic") {
        force.x += (anchor.x - position.x) * .0025;
        force.y += (anchor.y - position.y) * .0025;
      }
      force.x += (width / 2 - position.x) * .0008;
      force.y += (height / 2 - position.y) * .0008;
      position.x += force.x * .46;
      position.y += force.y * .46;
    });
    for (let leftIndex = 0; leftIndex < nodes.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < nodes.length; rightIndex += 1) {
        const left = positions.get(nodes[leftIndex].id);
        const right = positions.get(nodes[rightIndex].id);
        let dx = right.x - left.x;
        let dy = right.y - left.y;
        let distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < collisionDistance) {
          if (distance < 1) {
            const angle = ((ragGraphHash(nodes[leftIndex].id) % 360) / 180) * Math.PI;
            dx = Math.cos(angle);
            dy = Math.sin(angle);
            distance = 1;
          }
          const overlap = (collisionDistance - distance) * .51;
          const offsetX = dx / distance * overlap;
          const offsetY = dy / distance * overlap;
          if (nodes[leftIndex].type !== "SemanticQuery") {
            left.x -= offsetX;
            left.y -= offsetY;
          }
          if (nodes[rightIndex].type !== "SemanticQuery") {
            right.x += offsetX;
            right.y += offsetY;
          }
        }
        const captionGapX = (captionWidths.get(nodes[leftIndex].id) + captionWidths.get(nodes[rightIndex].id)) / 2 + 14;
        const captionGapY = 92;
        const captionDx = right.x - left.x;
        const captionDy = right.y - left.y;
        if (Math.abs(captionDx) < captionGapX && Math.abs(captionDy) < captionGapY) {
          const horizontalPressure = (captionGapX - Math.abs(captionDx)) / captionGapX;
          const verticalPressure = (captionGapY - Math.abs(captionDy)) / captionGapY;
          if (horizontalPressure < verticalPressure) {
            const direction = captionDx >= 0 ? 1 : -1;
            const push = (captionGapX - Math.abs(captionDx)) * .28;
            if (nodes[leftIndex].type !== "SemanticQuery") left.x -= direction * push;
            if (nodes[rightIndex].type !== "SemanticQuery") right.x += direction * push;
          } else {
            const direction = captionDy >= 0 ? 1 : -1;
            const push = (captionGapY - Math.abs(captionDy)) * .28;
            if (nodes[leftIndex].type !== "SemanticQuery") left.y -= direction * push;
            if (nodes[rightIndex].type !== "SemanticQuery") right.y += direction * push;
          }
        }
      }
    }
  }
  return positions;
}

function buildRagGraphPositions(data, nodes, edges) {
  return ragGraphState.layout === "force"
    ? buildForceRagGraphPositions(data, nodes, edges)
    : buildLayeredRagGraphPositions(data, nodes);
}

function renderRagGraphSvg() {
  const data = ragGraphState.data;
  const svg = $("#rag-graph-svg");
  const visible = getRagGraphVisibleData();
  renderRagGraphVisibilityControl(visible);
  if (!data || !visible.nodes.some(node => node.type !== "SemanticQuery")) {
    svg.innerHTML = "";
    $("#rag-graph-visible-count").textContent = "0";
    return;
  }
  const positionKey = [
    ragGraphState.layout,
    data.domain,
    data.mode,
    visible.nodes.map(node => node.id).sort().join("|"),
  ].join("::");
  if (ragGraphState.positionKey !== positionKey) {
    const previous = ragGraphState.positions;
    const allPositioned = visible.nodes.every(node => previous.has(node.id));
    const next = allPositioned
      ? new Map(visible.nodes.map(node => [node.id, previous.get(node.id)]))
      : buildRagGraphPositions(data, visible.nodes, visible.edges);
    previous.forEach((position, nodeId) => {
      if (next.has(nodeId)) next.set(nodeId, position);
    });
    ragGraphState.positions = next;
    ragGraphState.positionKey = positionKey;
  }
  const positions = ragGraphState.positions;
  $("#rag-graph-visible-count").textContent = formatNumber(visible.nodes.length);
  const selectedId = ragGraphState.selectedNodeId;
  const connected = new Set([selectedId]);
  visible.edges.forEach(edge => {
    if (edge.source === selectedId) connected.add(edge.target);
    if (edge.target === selectedId) connected.add(edge.source);
  });
  const edgeHtml = visible.edges.map(edge => {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) return "";
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
    const radius = 24;
    const startX = source.x + dx / distance * radius;
    const startY = source.y + dy / distance * radius;
    const endX = target.x - dx / distance * (radius + 3);
    const endY = target.y - dy / distance * (radius + 3);
    const focused = edge.source === selectedId || edge.target === selectedId;
    const onPath = ragGraphState.pathEdgeIds.has(ragGraphEdgeId(edge));
    const dimmed = selectedId && !focused && !onPath;
    const semantic = edge.predicate === "SEMANTIC_MATCH";
    const midX = (startX + endX) / 2;
    const midY = (startY + endY) / 2 - 5;
    const label = (focused || onPath) && !semantic
      ? `<text class="rag-graph-edge-label" x="${midX}" y="${midY}">${escapeHtml(ragGraphShort(edge.predicate, 18))}</text>`
      : "";
    return `<line class="rag-graph-edge ${semantic ? "semantic" : ""} ${focused ? "focused" : ""} ${onPath ? "path" : ""} ${dimmed ? "dimmed" : ""}"
      x1="${startX}" y1="${startY}" x2="${endX}" y2="${endY}"></line>${label}`;
  }).join("");
  const nodeHtml = visible.nodes.map(node => {
    const position = positions.get(node.id);
    if (!position) return "";
    const selected = ragGraphState.selectedNodeIds.has(node.id) || node.id === selectedId;
    const pinned = ragGraphState.pinnedNodeIds.has(node.id);
    const onPath = ragGraphState.pathNodeIds.has(node.id);
    const dimmed = selectedId && !connected.has(node.id) && !onPath;
    const score = node.score != null && data.mode === "semantic" && node.type !== "SemanticQuery"
      ? `${Math.round(Number(node.score) * 100)}%`
      : "";
    const radius = node.type === "SemanticQuery" ? 30 : selected ? 28 : 24;
    const labelLines = ragGraphLabelLines(node);
    const captionWidth = ragGraphCaptionWidth(node);
    const captionY = radius + 19;
    const captionHeight = labelLines.length > 1 ? 47 : 36;
    const labelHtml = labelLines.length > 1
      ? `<text class="node-label" x="0" y="-2">${escapeHtml(labelLines[0])}</text><text class="node-label" x="0" y="9">${escapeHtml(labelLines[1])}</text>`
      : `<text class="node-label" x="0" y="0">${escapeHtml(labelLines[0])}</text>`;
    const metaY = labelLines.length > 1 ? 21 : 11;
    return `<g class="rag-graph-node ${selected ? "selected" : ""} ${pinned ? "pinned" : ""} ${onPath ? "path" : ""} ${dimmed ? "dimmed" : ""}"
      data-rag-graph-node="${escapeHtml(node.id)}" transform="translate(${position.x} ${position.y})"
      style="--node-color:${ragGraphNodeColor(node)}" tabindex="0" role="button"
      aria-label="${escapeHtml(`${ragGraphTypeLabel(node.type)} ${node.label}`)}">
      <title>${escapeHtml(`${ragGraphTypeLabel(node.type)} · ${node.label}`)}</title>
      <circle class="node-halo" r="${radius + 8}"></circle>
      <circle class="node-orb" r="${radius}"></circle>
      <text class="node-glyph" x="0" y="3">${escapeHtml(ragGraphTypeGlyph(node.type))}</text>
      ${pinned ? `<circle class="node-pin-badge" cx="${-radius + 2}" cy="${-radius + 2}" r="7"></circle><text class="node-pin" x="${-radius + 2}" y="${-radius + 0.5}">•</text>` : ""}
      ${score ? `<circle class="node-score-badge" cx="${radius - 1}" cy="${-radius + 2}" r="10"></circle><text class="node-score" x="${radius - 1}" y="${-radius + 4.5}">${score}</text>` : ""}
      <g class="node-caption" transform="translate(0 ${captionY})">
        <rect x="${-captionWidth / 2}" y="-12" width="${captionWidth}" height="${captionHeight}" rx="7"></rect>
        ${labelHtml}
        <text class="node-meta" x="0" y="${metaY}">${escapeHtml(ragGraphTypeLabel(node.type))}</text>
      </g>
    </g>`;
  }).join("");
  svg.innerHTML = `<defs>
    <marker id="rag-graph-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#8c959f"></path></marker>
    <marker id="rag-graph-arrow-semantic" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#8250df"></path></marker>
    <marker id="rag-graph-arrow-path" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#e85d04"></path></marker>
  </defs><g class="rag-graph-scene">${edgeHtml}${nodeHtml}</g>`;
  if (ragGraphState.fitOnNextRender) {
    ragGraphState.fitOnNextRender = false;
    resetRagGraphView();
  } else {
    applyRagGraphView();
  }
}

function renderRagGraphList() {
  const data = ragGraphState.data;
  if (!data) return;
  const visible = getRagGraphVisibleData();
  const nodes = visible.nodes.some(node => node.type !== "SemanticQuery") ? visible.nodes : [];
  $("#rag-graph-list-count").textContent = `${nodes.length} 个节点`;
  $("#rag-graph-list").innerHTML = nodes.map(node => {
    const degree = visible.edges.filter(edge => edge.source === node.id || edge.target === node.id).length;
    return `<button type="button" class="rag-graph-list-row" data-rag-graph-list-node="${escapeHtml(node.id)}">
      <i style="--node-color:${ragGraphNodeColor(node)}"></i><span>${escapeHtml(node.label)}</span>
      <small>${escapeHtml(ragGraphTypeLabel(node.type))} · ${degree} 关系</small>
    </button>`;
  }).join("");
}

function selectRagGraphNode(nodeId, {additive = false} = {}) {
  const node = ragGraphState.data?.nodes.find(item => item.id === nodeId);
  if (!node) return;
  if (ragGraphState.pathMode) {
    selectRagGraphPathPoint(nodeId);
    return;
  }
  if (!additive) ragGraphState.selectedNodeIds.clear();
  if (additive && ragGraphState.selectedNodeIds.has(nodeId)) {
    ragGraphState.selectedNodeIds.delete(nodeId);
  } else {
    ragGraphState.selectedNodeIds.add(nodeId);
  }
  ragGraphState.selectedNodeId = ragGraphState.selectedNodeIds.has(nodeId)
    ? nodeId
    : [...ragGraphState.selectedNodeIds].at(-1) || null;
  closeRagGraphContext();
  renderRagGraphSvg();
  renderRagGraphInspector(
    ragGraphState.data.nodes.find(item => item.id === ragGraphState.selectedNodeId) || null
  );
  renderRagGraphSelectionHint();
}

function renderRagGraphInspector(node) {
  const inspector = $("#rag-graph-inspector");
  if (!node || !ragGraphState.data) {
    inspector.hidden = false;
    inspector.innerHTML = `<div class="graph-inspector-empty"><i class="ph ph-cursor-click"></i><strong>选择节点查看详情</strong><p>这里会显示稳定实体 ID、来源、版本和直接关系，并可展开一跳邻居。</p></div>`;
    return;
  }
  inspector.hidden = false;
  const data = ragGraphState.data;
  const relations = data.edges.filter(edge => edge.source === node.id || edge.target === node.id);
  const byId = new Map(data.nodes.map(item => [item.id, item]));
  const expansion = ragGraphState.neighborExpansion.get(node.id);
  const expansionLabel = expansion?.hasMore === false
    ? "邻居已全部载入"
    : expansion?.cursor > 0
      ? "继续展开邻居"
      : "展开一跳邻居";
  inspector.innerHTML = `<div class="rag-graph-inspector-head">
      <span>${escapeHtml(ragGraphDomainLabel(node.domain))} · ${escapeHtml(ragGraphTypeLabel(node.type))}</span>
      <strong>${escapeHtml(node.label)}</strong>
      <button type="button" class="rag-graph-inspector-close" aria-label="关闭节点详情">×</button>
    </div>
    <div class="rag-graph-inspector-body">
      <div class="rag-graph-detail"><span>稳定实体 ID</span><b>${escapeHtml(node.id)}</b></div>
      <div class="rag-graph-detail"><span>原始位置</span><b>${escapeHtml(node.locator || "—")}</b></div>
      <div class="rag-graph-detail"><span>版本 / 模型</span><b>${escapeHtml(node.version ? shortCommit(node.version) : "—")}</b></div>
      ${node.score != null ? `<div class="rag-graph-detail"><span>语义相关度</span><b>${Math.round(Number(node.score) * 100)}% · ${escapeHtml((node.channels || []).join(" + ") || "vector")}</b></div>` : ""}
      <div class="graph-inspector-actions">
        <button type="button" class="graph-inspector-action secondary" id="rag-graph-pin-node">${ragGraphState.pinnedNodeIds.has(node.id) ? "取消固定" : "固定节点"}</button>
        <button type="button" class="graph-inspector-action secondary" id="rag-graph-expand-node" ${expansion?.hasMore === false ? "disabled" : ""}>${expansionLabel}</button>
      </div>
      <h4>直接关系 · ${relations.length}</h4>
      <div>${relations.slice(0, 16).map(edge => {
        const neighborId = edge.source === node.id ? edge.target : edge.source;
        const neighbor = byId.get(neighborId);
        return `<div class="rag-graph-neighbor"><b>${escapeHtml(edge.source === node.id ? `→ ${edge.predicate}` : `← ${edge.predicate}`)}</b><span>${escapeHtml(neighbor?.label || neighborId)}</span></div>`;
      }).join("") || '<div class="rag-graph-neighbor"><span>当前子图中没有直接关系</span></div>'}</div>
      ${node.domain !== "query" ? '<button type="button" class="rag-graph-open-source" id="rag-graph-open-source">打开原始来源</button>' : ""}
    </div>`;
  $(".rag-graph-inspector-close", inspector)?.addEventListener("click", () => {
    ragGraphState.selectedNodeId = null;
    ragGraphState.selectedNodeIds.clear();
    renderRagGraphSvg();
    renderRagGraphInspector(null);
    renderRagGraphSelectionHint();
  });
  $("#rag-graph-open-source")?.addEventListener("click", () => openRagGraphNodeSource(node));
  $("#rag-graph-pin-node")?.addEventListener("click", () => toggleRagGraphNodePin(node.id));
  $("#rag-graph-expand-node")?.addEventListener("click", () => expandRagGraphNode(node.id));
}

function renderRagGraphSelectionHint(message = "") {
  const target = $("#rag-graph-selection-hint");
  if (!target) return;
  if (message) {
    target.textContent = message;
  } else if (ragGraphState.pathMode && ragGraphState.pathStartId) {
    const start = ragGraphState.data?.nodes.find(node => node.id === ragGraphState.pathStartId);
    target.textContent = `路径起点：${start?.label || "已选择"} · 再选择一个终点`;
  } else if (ragGraphState.pathMode) {
    target.textContent = "路径模式：请选择起点";
  } else if (ragGraphState.focusNodeId) {
    target.textContent = "正在聚焦直接相邻节点 · 双击中心节点恢复完整子图";
  } else if (ragGraphState.selectedNodeIds.size > 1) {
    target.textContent = `已选择 ${ragGraphState.selectedNodeIds.size} 个节点 · 右键可批量查看`;
  } else {
    target.textContent = "拖动节点固定位置 · 拖动画布平移 · 滚轮缩放 · 单击查看详情";
  }
}

function openRagGraphContext(nodeId, clientX, clientY) {
  const node = ragGraphState.data?.nodes.find(item => item.id === nodeId);
  const menu = $("#rag-graph-context");
  const canvas = $("#rag-graph-canvas");
  if (!node || !menu || !canvas) return;
  ragGraphState.selectedNodeId = nodeId;
  ragGraphState.selectedNodeIds.clear();
  ragGraphState.selectedNodeIds.add(nodeId);
  renderRagGraphSvg();
  renderRagGraphInspector(node);
  const expansion = ragGraphState.neighborExpansion.get(nodeId);
  menu.innerHTML = `
    <button type="button" data-rag-graph-action="details">详情</button>
    <button type="button" data-rag-graph-action="expand" ${expansion?.hasMore === false ? "disabled" : ""}>${expansion?.cursor > 0 ? (expansion.hasMore === false ? "邻居已全部载入" : "继续展开邻居") : "展开一跳邻居"}</button>
    <button type="button" data-rag-graph-action="pin">${ragGraphState.pinnedNodeIds.has(nodeId) ? "取消固定" : "固定节点"}</button>
    <button type="button" data-rag-graph-action="focus">${ragGraphState.focusNodeId === nodeId ? "恢复" : "聚焦相邻"}</button>
    <button type="button" data-rag-graph-action="path">设为路径点</button>
    <button type="button" data-rag-graph-action="copy">复制 ID</button>
    <button type="button" data-rag-graph-action="hide">隐藏</button>
    ${node.domain !== "query" ? '<button type="button" data-rag-graph-action="source">打开来源</button>' : ""}
  `;
  const bounds = canvas.getBoundingClientRect();
  menu.hidden = false;
  const left = Math.max(8, Math.min(clientX - bounds.left - menu.offsetWidth / 2, bounds.width - menu.offsetWidth - 8));
  const top = Math.max(8, Math.min(clientY - bounds.top - 48, bounds.height - menu.offsetHeight - 8));
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  menu.dataset.nodeId = nodeId;
}

function closeRagGraphContext() {
  const menu = $("#rag-graph-context");
  if (!menu) return;
  menu.hidden = true;
  menu.innerHTML = "";
  delete menu.dataset.nodeId;
}

function focusRagGraphNode(nodeId) {
  ragGraphState.focusNodeId = ragGraphState.focusNodeId === nodeId ? null : nodeId;
  ragGraphState.pathNodeIds.clear();
  ragGraphState.pathEdgeIds.clear();
  closeRagGraphContext();
  renderRagGraph();
}

function hideRagGraphNode(nodeId) {
  ragGraphState.hiddenNodeIds.add(nodeId);
  ragGraphState.selectedNodeIds.delete(nodeId);
  if (ragGraphState.selectedNodeId === nodeId) ragGraphState.selectedNodeId = null;
  if (ragGraphState.focusNodeId === nodeId) ragGraphState.focusNodeId = null;
  closeRagGraphContext();
  renderRagGraph();
}

function revealRagGraph() {
  ragGraphState.hiddenNodeIds.clear();
  ragGraphState.hiddenTypes.clear();
  ragGraphState.focusNodeId = null;
  ragGraphState.pathNodeIds.clear();
  ragGraphState.pathEdgeIds.clear();
  ragGraphState.pathStartId = null;
  renderRagGraph();
}

function toggleRagGraphNodePin(nodeId) {
  if (ragGraphState.pinnedNodeIds.has(nodeId)) ragGraphState.pinnedNodeIds.delete(nodeId);
  else ragGraphState.pinnedNodeIds.add(nodeId);
  renderRagGraphSvg();
  renderRagGraphInspector(
    ragGraphState.data?.nodes.find(node => node.id === nodeId) || null
  );
}

function placeRagGraphAdditions(parentId, additions, startIndex = 0) {
  if (!additions.length) return;
  const parent = ragGraphState.positions.get(parentId) || {x:600, y:380};
  additions.forEach((node, index) => {
    if (ragGraphState.positions.has(node.id)) return;
    const expansionIndex = startIndex + index;
    const ring = 185 + Math.floor(expansionIndex / 28) * 155;
    const angle = expansionIndex * 2.399963229728653 + (ragGraphHash(node.id) % 17) / 17;
    ragGraphState.positions.set(node.id, {
      x: parent.x + Math.cos(angle) * ring,
      y: parent.y + Math.sin(angle) * ring,
    });
  });
}

async function expandRagGraphNode(nodeId) {
  const previous = ragGraphState.neighborExpansion.get(nodeId) || {
    cursor: 0,
    hasMore: true,
    total: null,
  };
  if (!previous.hasMore) {
    showToast("该节点的直接关系已全部载入");
    return;
  }
  const overlay = $("#rag-graph-overlay");
  overlay.hidden = false;
  overlay.innerHTML = `<b>${previous.cursor ? "正在继续展开邻居" : "正在展开一跳邻居"}</b><span>按游标读取下一页真实实体与关系</span>`;
  try {
    const params = new URLSearchParams({
      entity_id: nodeId,
      cursor: String(previous.cursor),
      limit: "40",
    });
    const page = await api(`/v1/graph/neighbors?${params.toString()}`);
    if (!ragGraphState.data) return;
    const nodes = new Map(ragGraphState.data.nodes.map(node => [node.id, node]));
    const edges = new Map(ragGraphState.data.edges.map(edge => [edge.id, edge]));
    const existingIds = new Set(nodes.keys());
    const additions = page.nodes.filter(node => node.id !== nodeId && !existingIds.has(node.id));
    placeRagGraphAdditions(nodeId, additions, previous.cursor);
    additions.forEach(node => nodes.set(node.id, node));
    const allowedIds = new Set(nodes.keys());
    const edgeCountBefore = edges.size;
    page.edges
      .filter(edge => allowedIds.has(edge.source) && allowedIds.has(edge.target))
      .forEach(edge => edges.set(edge.id, edge));
    ragGraphState.neighborExpansion.set(nodeId, {
      cursor: page.next_cursor,
      hasMore: page.has_more,
      total: page.total,
    });
    ragGraphState.data.nodes = [...nodes.values()];
    ragGraphState.data.edges = [...edges.values()];
    ragGraphState.data.metadata.visible_nodes = nodes.size;
    ragGraphState.data.metadata.visible_edges = edges.size;
    ragGraphState.positionKey = "";
    renderRagGraph();
    selectRagGraphNode(nodeId);
    const progress = page.total == null
      ? `已扫描 ${page.next_cursor} 条关系`
      : `已载入 ${page.next_cursor}/${page.total} 条直接关系`;
    showToast(`新增 ${additions.length} 个节点、${edges.size - edgeCountBefore} 条关系 · ${progress}${page.has_more ? " · 可继续展开" : " · 已全部载入"}`);
  } catch (error) {
    showToast(`展开失败：${error.message}`);
  } finally {
    overlay.hidden = true;
  }
}

function findRagGraphPath(startId, endId) {
  const visible = getRagGraphVisibleData();
  const adjacency = new Map(visible.nodes.map(node => [node.id, []]));
  visible.edges.forEach(edge => {
    adjacency.get(edge.source)?.push({node:edge.target, edge});
    adjacency.get(edge.target)?.push({node:edge.source, edge});
  });
  const queue = [startId];
  const previous = new Map([[startId, null]]);
  while (queue.length) {
    const current = queue.shift();
    if (current === endId) break;
    for (const step of adjacency.get(current) || []) {
      if (previous.has(step.node)) continue;
      previous.set(step.node, {node:current, edge:step.edge});
      queue.push(step.node);
    }
  }
  if (!previous.has(endId)) return null;
  const nodes = new Set([endId]);
  const edges = new Set();
  let current = endId;
  while (current !== startId) {
    const step = previous.get(current);
    nodes.add(step.node);
    edges.add(ragGraphEdgeId(step.edge));
    current = step.node;
  }
  return {nodes, edges};
}

function selectRagGraphPathPoint(nodeId) {
  if (!ragGraphState.pathStartId) {
    ragGraphState.pathStartId = nodeId;
    ragGraphState.pathNodeIds = new Set([nodeId]);
    ragGraphState.pathEdgeIds.clear();
    ragGraphState.selectedNodeId = nodeId;
    renderRagGraphSvg();
    renderRagGraphSelectionHint();
    return;
  }
  const startId = ragGraphState.pathStartId;
  const path = findRagGraphPath(startId, nodeId);
  ragGraphState.pathMode = false;
  ragGraphState.pathStartId = null;
  $("#rag-graph-path-toggle").setAttribute("aria-pressed", "false");
  if (!path) {
    ragGraphState.pathNodeIds.clear();
    ragGraphState.pathEdgeIds.clear();
    showToast("当前可见子图中没有连接这两个节点的路径");
    renderRagGraphSelectionHint("未找到路径 · 可恢复完整图谱后重试");
  } else {
    ragGraphState.pathNodeIds = path.nodes;
    ragGraphState.pathEdgeIds = path.edges;
    renderRagGraphSelectionHint(`已找到 ${path.edges.size} 跳关系路径`);
  }
  renderRagGraphSvg();
}

function toggleRagGraphPathMode() {
  ragGraphState.pathMode = !ragGraphState.pathMode;
  ragGraphState.pathStartId = null;
  ragGraphState.pathNodeIds.clear();
  ragGraphState.pathEdgeIds.clear();
  $("#rag-graph-path-toggle").setAttribute("aria-pressed", String(ragGraphState.pathMode));
  renderRagGraphSvg();
  renderRagGraphSelectionHint();
}

async function openRagGraphNodeSource(node) {
  if (node.domain === "code") {
    const locator = String(node.locator || "");
    const match = locator.match(/^([^@]+)@([^:]+):(.+?)#L(\d+)-L(\d+)$/);
    const repository = state.repositories.find(item => item.name === match?.[1]);
    if (match && repository) {
      switchRagTab("answer");
      await openCodeEvidenceInRag({
        repository_id: repository.id,
        path: match[3],
        start_line: Number(match[4]),
        end_line: Number(match[5]),
      });
      return;
    }
    switchView("repository");
    return;
  }
  if (node.domain === "codex") {
    const threadId = node.id.match(/^codex:\/\/thread\/([^/]+)/)?.[1];
    switchView("codex");
    if (threadId) await selectCodexThread(threadId, node.id);
    return;
  }
  if (node.domain === "document") {
    switchView("documents");
    const claim = state.claims.find(item => item.id === node.id);
    if (claim) {
      state.selectedDocument = claim.document_id;
      await selectClaim(claim.id);
      return;
    }
    const document = state.documents.find(item => item.id === node.id);
    if (document) {
      state.selectedDocument = document.id;
      renderDocumentCenter();
    }
    return;
  }
  if (node.domain === "experiment") {
    switchView("experiments");
    return;
  }
  showToast("该节点保留了稳定来源标识，可通过关系链继续核验");
}

function resetRagGraphView() {
  const positioned = $$("#rag-graph-svg [data-rag-graph-node]")
    .map(node => {
      const match = /translate\(([-\d.]+)[,\s]+([-\d.]+)\)/.exec(
        node.getAttribute("transform") || ""
      );
      return match ? {x:Number(match[1]), y:Number(match[2])} : null;
    })
    .filter(Boolean);
  if (!positioned.length) {
    ragGraphState.viewBox = {x:75, y:70, width:1050, height:665};
    applyRagGraphView();
    return;
  }
  const minX = Math.min(...positioned.map(position => position.x)) - 130;
  const maxX = Math.max(...positioned.map(position => position.x)) + 130;
  const minY = Math.min(...positioned.map(position => position.y)) - 120;
  const maxY = Math.max(...positioned.map(position => position.y)) + 150;
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  const graphWidth = Math.max(720, maxX - minX);
  const graphHeight = Math.max(456, maxY - minY);
  const aspect = 1200 / 760;
  const width = Math.max(graphWidth, graphHeight * aspect);
  const height = width / aspect;
  ragGraphState.viewBox = {
    x:centerX - width / 2,
    y:centerY - height / 2,
    width,
    height,
  };
  applyRagGraphView();
}

function applyRagGraphView() {
  const view = ragGraphState.viewBox;
  $("#rag-graph-svg")?.setAttribute("viewBox", `${view.x} ${view.y} ${view.width} ${view.height}`);
}

function zoomRagGraph(factor) {
  const view = ragGraphState.viewBox;
  const width = Math.max(260, view.width * factor);
  const height = width / (1200 / 760);
  ragGraphState.viewBox = {
    x: view.x + (view.width - width) / 2,
    y: view.y + (view.height - height) / 2,
    width,
    height,
  };
  applyRagGraphView();
}

function zoomRagGraphAtPoint(factor, clientX, clientY) {
  const svg = $("#rag-graph-svg");
  const bounds = svg.getBoundingClientRect();
  const view = ragGraphState.viewBox;
  const width = Math.max(180, view.width * factor);
  const height = width / (1200 / 760);
  const ratioX = bounds.width ? (clientX - bounds.left) / bounds.width : .5;
  const ratioY = bounds.height ? (clientY - bounds.top) / bounds.height : .5;
  const anchorX = view.x + ratioX * view.width;
  const anchorY = view.y + ratioY * view.height;
  ragGraphState.viewBox = {
    x: anchorX - ratioX * width,
    y: anchorY - ratioY * height,
    width,
    height,
  };
  applyRagGraphView();
}

function downloadRagGraph() {
  const source = $("#rag-graph-svg");
  if (!source?.innerHTML) {
    showToast("当前没有可导出的图谱");
    return;
  }
  const clone = source.cloneNode(true);
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", "1600");
  clone.setAttribute("height", "1040");
  const style = document.createElementNS("http://www.w3.org/2000/svg", "style");
  style.textContent = `
    .rag-graph-edge{stroke:#aeb6bf;stroke-width:1.4;fill:none}
    .rag-graph-edge.semantic{stroke:#8250df;stroke-dasharray:7 5}
    .rag-graph-edge.path{stroke:#e85d04;stroke-width:3.4}
    .node-halo{fill:none;stroke:var(--node-color);stroke-width:2;opacity:.3}
    .node-orb{fill:var(--node-color);stroke:var(--node-color);stroke-width:2.4}
    text{font-family:ui-sans-serif,sans-serif;text-anchor:middle;fill:#1f2328}
    .node-glyph{fill:#fff;font-size:10px;font-weight:700}.node-caption rect{fill:#fff;stroke:#d0d7de}
    .node-label{font-size:9px;font-weight:700}.node-meta{font-size:6.8px;fill:#656d76}
    .node-score,.rag-graph-edge-label{font-size:6.8px;font-weight:700}
  `;
  clone.prepend(style);
  const blob = new Blob([new XMLSerializer().serializeToString(clone)], {type:"image/svg+xml"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${ragGraphState.domain}-${ragGraphState.mode}-graph.svg`;
  link.click();
  URL.revokeObjectURL(url);
}

function installGraphWorkbenchEvents() {
  if (ragGraphState.installed || !$("#rag-graph-workspace")) return;
  ragGraphState.installed = true;
  $$("[data-rag-graph-domain]").forEach(button => button.addEventListener("click", () => {
    ragGraphState.domain = button.dataset.ragGraphDomain;
    $$("[data-rag-graph-domain]").forEach(item => item.classList.toggle("active", item === button));
    loadRagGraph({force:true});
  }));
  $$("[data-rag-graph-mode]").forEach(button => button.addEventListener("click", () => {
    ragGraphState.mode = button.dataset.ragGraphMode;
    $$("[data-rag-graph-mode]").forEach(item => item.classList.toggle("active", item === button));
    loadRagGraph({force:true});
  }));
  $("#rag-graph-search").addEventListener("submit", event => {
    event.preventDefault();
    loadRagGraph({force:true});
  });
  $("#rag-graph-visible-slider").addEventListener("input", event => {
    const loadedCount = ragGraphState.data?.nodes.length || 0;
    const totalCount = Number(ragGraphState.data?.metadata?.total_nodes || loadedCount);
    const nextCount = Math.max(1, Math.min(Number(event.target.value), totalCount));
    ragGraphState.visibleNodeCap = nextCount >= totalCount && loadedCount >= totalCount
      ? null
      : nextCount;
    renderRagGraph();
  });
  $("#rag-graph-visible-slider").addEventListener("change", async event => {
    const targetCount = Number(event.target.value);
    const loadedCount = ragGraphState.data?.nodes.length || 0;
    if (targetCount <= loadedCount) return;
    await loadRagGraph({force:true, preserveVisibleTarget:true});
  });
  const svg = $("#rag-graph-svg");
  $("#rag-graph-canvas").addEventListener("click", event => {
    const button = event.target.closest("[data-rag-graph-zoom]");
    if (!button) return;
    if (button.dataset.ragGraphZoom === "fit") resetRagGraphView();
    else zoomRagGraph(button.dataset.ragGraphZoom === "in" ? .82 : 1.22);
  });
  $("#rag-graph-layout-toggle").addEventListener("click", () => {
    ragGraphState.layout = ragGraphState.layout === "force" ? "layered" : "force";
    const button = $("#rag-graph-layout-toggle");
    button.querySelector("span").textContent = ragGraphState.layout === "force" ? "力导向" : "分层";
    button.setAttribute("aria-pressed", String(ragGraphState.layout === "force"));
    ragGraphState.positions.clear();
    ragGraphState.positionKey = "";
    ragGraphState.pinnedNodeIds.clear();
    ragGraphState.fitOnNextRender = true;
    renderRagGraphSvg();
  });
  $("#rag-graph-categories").addEventListener("click", event => {
    const button = event.target.closest("[data-rag-graph-category]");
    if (!button) return;
    const type = button.dataset.ragGraphCategory;
    if (ragGraphState.hiddenTypes.has(type)) ragGraphState.hiddenTypes.delete(type);
    else ragGraphState.hiddenTypes.add(type);
    renderRagGraph();
  });
  svg.addEventListener("click", event => {
    if (ragGraphState.suppressClick) {
      ragGraphState.suppressClick = false;
      return;
    }
    const node = event.target.closest("[data-rag-graph-node]");
    if (node) {
      selectRagGraphNode(node.dataset.ragGraphNode, {additive:event.ctrlKey || event.metaKey});
    } else {
      closeRagGraphContext();
    }
  });
  svg.addEventListener("dblclick", event => {
    const node = event.target.closest("[data-rag-graph-node]");
    if (!node) return;
    event.preventDefault();
    focusRagGraphNode(node.dataset.ragGraphNode);
  });
  svg.addEventListener("contextmenu", event => {
    const node = event.target.closest("[data-rag-graph-node]");
    if (!node) return;
    event.preventDefault();
    openRagGraphContext(node.dataset.ragGraphNode, event.clientX, event.clientY);
  });
  svg.addEventListener("keydown", event => {
    const node = event.target.closest("[data-rag-graph-node]");
    if (node && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      selectRagGraphNode(node.dataset.ragGraphNode, {additive:event.ctrlKey || event.metaKey});
    }
  });
  $("#rag-graph-context").addEventListener("click", async event => {
    const action = event.target.closest("[data-rag-graph-action]")?.dataset.ragGraphAction;
    const nodeId = $("#rag-graph-context").dataset.nodeId;
    const node = ragGraphState.data?.nodes.find(item => item.id === nodeId);
    if (!action || !node) return;
    if (action === "details") {
      closeRagGraphContext();
      renderRagGraphInspector(node);
    } else if (action === "expand") {
      closeRagGraphContext();
      await expandRagGraphNode(nodeId);
    } else if (action === "pin") {
      closeRagGraphContext();
      toggleRagGraphNodePin(nodeId);
    } else if (action === "focus") {
      focusRagGraphNode(nodeId);
    } else if (action === "path") {
      ragGraphState.pathMode = true;
      ragGraphState.pathStartId = null;
      $("#rag-graph-path-toggle").setAttribute("aria-pressed", "true");
      closeRagGraphContext();
      selectRagGraphPathPoint(nodeId);
    } else if (action === "copy") {
      try {
        await navigator.clipboard.writeText(node.id);
        showToast("已复制稳定实体 ID");
      } catch (error) {
        showToast("浏览器未允许复制，请在详情中手动复制");
      }
      closeRagGraphContext();
    } else if (action === "hide") {
      hideRagGraphNode(nodeId);
    } else if (action === "source") {
      closeRagGraphContext();
      await openRagGraphNodeSource(node);
    }
  });
  $("#rag-graph-list").addEventListener("click", event => {
    const row = event.target.closest("[data-rag-graph-list-node]");
    if (row) selectRagGraphNode(row.dataset.ragGraphListNode);
  });
  $("#rag-graph-path-toggle").addEventListener("click", toggleRagGraphPathMode);
  $("#rag-graph-reveal-all").addEventListener("click", revealRagGraph);
  $("#rag-graph-download").addEventListener("click", downloadRagGraph);
  svg.addEventListener("pointerdown", event => {
    const node = event.target.closest("[data-rag-graph-node]");
    svg.setPointerCapture(event.pointerId);
    if (node) {
      event.preventDefault();
      const position = ragGraphState.positions.get(node.dataset.ragGraphNode);
      ragGraphState.drag = {
        kind: "node",
        nodeId: node.dataset.ragGraphNode,
        clientX: event.clientX,
        clientY: event.clientY,
        position: {...position},
        moved: false,
      };
      node.classList.add("dragging");
    } else {
      svg.classList.add("dragging");
      ragGraphState.drag = {kind:"canvas", clientX:event.clientX, clientY:event.clientY, view:{...ragGraphState.viewBox}};
    }
  });
  svg.addEventListener("pointermove", event => {
    if (!ragGraphState.drag || !svg.hasPointerCapture(event.pointerId)) return;
    const bounds = svg.getBoundingClientRect();
    const drag = ragGraphState.drag;
    if (drag.kind === "node") {
      const dx = (event.clientX - drag.clientX) * ragGraphState.viewBox.width / bounds.width;
      const dy = (event.clientY - drag.clientY) * ragGraphState.viewBox.height / bounds.height;
      drag.moved ||= Math.abs(dx) + Math.abs(dy) > 3;
      ragGraphState.positions.set(drag.nodeId, {
        x: drag.position.x + dx,
        y: drag.position.y + dy,
      });
      ragGraphState.pinnedNodeIds.add(drag.nodeId);
      renderRagGraphSvg();
    } else {
      ragGraphState.viewBox.x = drag.view.x - (event.clientX - drag.clientX) * drag.view.width / bounds.width;
      ragGraphState.viewBox.y = drag.view.y - (event.clientY - drag.clientY) * drag.view.height / bounds.height;
      applyRagGraphView();
    }
  });
  svg.addEventListener("wheel", event => {
    event.preventDefault();
    const delta = Math.max(-180, Math.min(180, event.deltaY));
    zoomRagGraphAtPoint(Math.exp(delta * .0016), event.clientX, event.clientY);
  }, {passive:false});
  const stop = event => {
    if (ragGraphState.drag?.kind === "node" && ragGraphState.drag.moved) {
      ragGraphState.suppressClick = true;
      renderRagGraphInspector(
        ragGraphState.data?.nodes.find(node => node.id === ragGraphState.drag.nodeId) || null
      );
    }
    if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    svg.classList.remove("dragging");
    svg.querySelectorAll(".rag-graph-node.dragging").forEach(node => node.classList.remove("dragging"));
    ragGraphState.drag = null;
  };
  svg.addEventListener("pointerup", stop);
  svg.addEventListener("pointercancel", stop);
  document.addEventListener("click", event => {
    if (!event.target.closest("#rag-graph-context") && !event.target.closest("[data-rag-graph-node]")) {
      closeRagGraphContext();
    }
  });
}
