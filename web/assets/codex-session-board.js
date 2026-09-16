(() => {
  "use strict";

  const EVENT_TYPE_ORDER = [
    "UserGoal",
    "AgentMessage",
    "CommandExecution",
    "ToolCall",
    "ToolResult",
    "Patch",
    "FileChange",
    "DevelopmentEpisode",
  ];

  const EVENT_TYPES = {
    UserGoal: {label:"目标", plural:"目标", icon:"ph-target"},
    AgentMessage: {label:"回复", plural:"回复", icon:"ph-chat-centered-text"},
    CommandExecution: {label:"命令", plural:"命令", icon:"ph-terminal-window"},
    ToolCall: {label:"工具调用", plural:"调用", icon:"ph-wrench"},
    ToolResult: {label:"工具结果", plural:"结果", icon:"ph-check-square-offset"},
    Patch: {label:"代码补丁", plural:"补丁", icon:"ph-git-diff"},
    FileChange: {label:"文件变更", plural:"文件", icon:"ph-file-code"},
    DevelopmentEpisode: {label:"轮次总结", plural:"总结", icon:"ph-flag-checkered"},
    ValidationResult: {label:"验证结果", plural:"验证", icon:"ph-seal-check"},
  };

  const OPERATION_CLUSTERS = [
    {key:"intent", label:"目标与约束", caption:"用户意图与任务边界", icon:"ph-target", types:["UserGoal"]},
    {key:"discussion", label:"协作与回复", caption:"对话、说明与阶段反馈", icon:"ph-chat-centered-text", types:["AgentMessage"]},
    {key:"execution", label:"命令与工具", caption:"命令执行与工具调用", icon:"ph-terminal-window", types:["CommandExecution", "ToolCall"]},
    {key:"results", label:"执行结果", caption:"工具返回与运行输出", icon:"ph-check-square-offset", types:["ToolResult"]},
    {key:"changes", label:"代码变更", caption:"补丁、文件与实现记录", icon:"ph-git-diff", types:["Patch", "FileChange"]},
    {key:"conclusion", label:"总结与验证", caption:"轮次结论与验证状态", icon:"ph-flag-checkered", types:["DevelopmentEpisode", "ValidationResult"]},
  ];

  const RELATED_SOURCES = {
    code: {label:"代码", icon:"ph-file-code"},
    experiment: {label:"实验", icon:"ph-flask"},
    document: {label:"文档", icon:"ph-file-text"},
  };

  const boardState = {
    thread: null,
    options: {},
    transform: {x:64, y:58, scale:.82},
    positions: new Map(),
    expandedItems: new Set(),
    expandedRelated: new Set(),
    selectedId: null,
    selectedTurnId: null,
    selectedClusterKey: null,
    drawerOpen: false,
    eventQuery: "",
    eventPage: 0,
    eventPageSize: 12,
    focusItemId: null,
    pointer: null,
    sceneNodes: [],
    sceneEdges: [],
    related: [],
    relatedFilter: "all",
    relatedPage: 0,
    relatedPageSize: 8,
    relatedRequest: 0,
    installed: false,
  };

  const escapeHtml = (value = "") => String(value).replace(
    /[&<>'"]/g,
    character => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[character]),
  );
  const compactText = (value = "") => String(value).replace(/\s+/g, " ").trim();
  const shortText = (value, length = 180) => {
    const text = compactText(value);
    return text.length > length ? `${text.slice(0, length - 1)}…` : text;
  };
  function readableItemContent(item) {
    const raw = String(item?.content || "").trim();
    if (!raw) return "无文本内容";
    if (!raw.startsWith("{") || !raw.endsWith("}")) return raw;
    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return raw;
    }
    if (!payload || Array.isArray(payload) || typeof payload !== "object") return raw;
    if ("session_id" in payload || "cell_id" in payload) {
      const chars = typeof payload.chars === "string" ? payload.chars : "";
      if (chars.includes("\u0003")) return "中止后台命令";
      if (chars.includes("\u0004")) return "结束后台命令输入";
      if (chars === "\n" || chars === "\r" || chars === "\r\n") return "向后台命令发送回车";
      return chars ? "向后台命令发送输入" : "等待后台命令继续输出";
    }
    for (const key of ["cmd", "command", "query", "path", "prompt", "question", "description"]) {
      if (typeof payload[key] === "string" && payload[key].trim()) return payload[key].trim();
    }
    const usefulKeys = Object.keys(payload).filter(key => ![
      "id", "thread_id", "turn_id", "call_id", "timeout_ms", "yield_time_ms",
      "max_output_tokens", "expected_version", "idempotency_key",
    ].includes(key));
    if (!usefulKeys.length) return item?.name || typeInfo(item?.item_type).label;
    return `${item?.name || typeInfo(item?.item_type).label} · ${usefulKeys.slice(0, 3).join(" / ")}`;
  }
  const formatTime = value => value
    ? new Intl.DateTimeFormat("zh-CN", {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"}).format(new Date(value))
    : "—";
  const typeInfo = type => EVENT_TYPES[type] || {label:type || "事件", plural:type || "事件", icon:"ph-cube"};
  const typeClass = type => `type-${String(type || "event").toLowerCase()}`;
  const statusLabel = value => ({
    completed:"完成",
    failed:"失败",
    in_progress:"进行中",
    running:"运行中",
    queued:"排队中",
  })[value] || value || "已记录";

  function elements() {
    return {
      viewport: document.querySelector("#codex-timeline"),
      scene: document.querySelector("#codex-board-scene"),
      links: document.querySelector("#codex-board-links"),
      nodes: document.querySelector("#codex-board-nodes"),
      overlay: document.querySelector("#codex-board-overlay"),
      count: document.querySelector("#codex-board-visible-count"),
      description: document.querySelector("#codex-board-description"),
      fallback: document.querySelector("#codex-fallback-list"),
      fallbackCount: document.querySelector("#codex-fallback-count"),
      related: document.querySelector("#codex-related-sources"),
      relatedFilters: document.querySelector("#codex-related-filters"),
      relatedCount: document.querySelector("#codex-related-count"),
      relatedStatus: document.querySelector("#codex-related-status"),
      relatedPager: document.querySelector("#codex-related-pager"),
      relatedPageSummary: document.querySelector("#codex-related-page-summary"),
      drawer: document.querySelector("#codex-event-drawer"),
      drawerTitle: document.querySelector("#codex-event-drawer-title"),
      drawerMeta: document.querySelector("#codex-event-drawer-meta"),
      drawerSearch: document.querySelector("#codex-event-filter"),
      drawerList: document.querySelector("#codex-event-list"),
      drawerRange: document.querySelector("#codex-event-range"),
      drawerPrev: document.querySelector("#codex-event-prev"),
      drawerNext: document.querySelector("#codex-event-next"),
    };
  }

  function eventCounts(turn) {
    const counts = new Map();
    turn.items.forEach(item => counts.set(item.item_type, (counts.get(item.item_type) || 0) + 1));
    return counts;
  }

  function orderedTypes(turn) {
    const counts = eventCounts(turn);
    const known = EVENT_TYPE_ORDER.filter(type => counts.has(type));
    const unknown = [...counts.keys()].filter(type => !EVENT_TYPE_ORDER.includes(type)).sort();
    return [...known, ...unknown];
  }

  function clusterForType(type) {
    return OPERATION_CLUSTERS.find(cluster => cluster.types.includes(type))
      || {key:`other-${type || "event"}`, label:typeInfo(type).label, caption:"其他结构化事件", icon:typeInfo(type).icon, types:[type]};
  }

  function clustersForTurn(turn) {
    const clusters = new Map();
    turn.items.forEach(item => {
      const definition = clusterForType(item.item_type);
      if (!clusters.has(definition.key)) clusters.set(definition.key, {...definition, items:[]});
      clusters.get(definition.key).items.push(item);
    });
    return [...clusters.values()].sort((left, right) => {
      const leftRank = OPERATION_CLUSTERS.findIndex(cluster => cluster.key === left.key);
      const rightRank = OPERATION_CLUSTERS.findIndex(cluster => cluster.key === right.key);
      return (leftRank < 0 ? 99 : leftRank) - (rightRank < 0 ? 99 : rightRank)
        || left.label.localeCompare(right.label);
    });
  }

  function resetForThread(thread) {
    boardState.thread = thread;
    boardState.transform = {x:64, y:58, scale:.82};
    boardState.positions.clear();
    boardState.expandedItems.clear();
    boardState.expandedRelated.clear();
    boardState.selectedId = null;
    boardState.selectedTurnId = thread.turns[0]?.id || null;
    boardState.selectedClusterKey = null;
    boardState.drawerOpen = false;
    boardState.eventQuery = "";
    boardState.eventPage = 0;
    boardState.focusItemId = null;
    boardState.related = [];
    boardState.relatedFilter = "all";
    boardState.relatedPage = 0;
  }

  function defaultPosition(id, x, y) {
    const existing = boardState.positions.get(id);
    if (existing) return existing;
    const position = {x, y, pinned:false};
    boardState.positions.set(id, position);
    return position;
  }

  function renderTurnNode(turn, index) {
    const position = defaultPosition(turn.id, 110 + index * 378, 105);
    const clusters = clustersForTurn(turn);
    const isSelected = boardState.selectedTurnId === turn.id;
    const duration = turn.metadata?.duration_ms
      ? `${Math.max(1, Math.round(turn.metadata.duration_ms / 1000))}s`
      : formatTime(turn.completed_at || turn.started_at);
    return {
      id: turn.id,
      kind: "turn",
      x: position.x,
      y: position.y,
      html: `<article class="codex-board-node turn-node type-usergoal ${isSelected ? "selected" : ""}"
        data-codex-node="${escapeHtml(turn.id)}" data-codex-entity="${escapeHtml(turn.id)}" data-codex-turn-node="${escapeHtml(turn.id)}"
        tabindex="0" aria-label="Turn ${turn.ordinal}，${escapeHtml(shortText(turn.goal || "无显式目标", 120))}"
        style="--node-x:${position.x}px;--node-y:${position.y}px">
        <header class="codex-node-drag-handle">
          <span class="codex-node-icon"><i class="ph ph-git-commit"></i></span>
          <span class="codex-node-title"><strong>Turn ${turn.ordinal} · ${escapeHtml(statusLabel(turn.status))}</strong><small>${escapeHtml(formatTime(turn.started_at))} · ${escapeHtml(duration)}</small></span>
          <span class="codex-node-status">${turn.items.length} EVENTS</span>
        </header>
        <div class="codex-node-body">
          <p class="codex-turn-goal">${escapeHtml(turn.goal || "本轮未提取到显式用户目标")}</p>
          <div class="codex-turn-types" aria-label="Turn ${turn.ordinal} 操作分组">
            ${clusters.map(cluster => {
              const active = isSelected && boardState.selectedClusterKey === cluster.key;
              return `<button type="button" class="${active ? "active" : ""}"
                data-codex-turn-cluster="${escapeHtml(cluster.key)}" data-codex-turn-id="${escapeHtml(turn.id)}"
                aria-pressed="${active}" title="${escapeHtml(cluster.label)} ${cluster.items.length} 条">
                <i class="ph ${cluster.icon}"></i><span>${escapeHtml(cluster.label)}</span><b>${cluster.items.length}</b>
              </button>`;
            }).join("")}
          </div>
          <div class="codex-turn-actions">
            <span>${clusters.length} 个操作簇 · ${turn.items.length} 个原始事件</span>
            <button type="button" data-codex-turn-expand="${escapeHtml(turn.id)}" aria-expanded="${isSelected && boardState.drawerOpen && boardState.selectedClusterKey === "all"}">
              ${isSelected ? "查看全部事件" : "展开操作簇"} <i class="ph ph-arrow-right"></i>
            </button>
          </div>
        </div>
      </article>`,
    };
  }

  function renderClusterNode(cluster, turn, clusterIndex) {
    const turnPosition = defaultPosition(turn.id, 110, 105);
    const column = clusterIndex % 3;
    const row = Math.floor(clusterIndex / 3);
    const id = `cluster:${turn.id}:${cluster.key}`;
    const position = defaultPosition(id, turnPosition.x - 198 + column * 232, 395 + row * 154);
    const selected = boardState.selectedClusterKey === cluster.key;
    const typeLabels = cluster.types
      .map(type => typeInfo(type).label)
      .filter(label => cluster.items.some(item => typeInfo(item.item_type).label === label));
    const latest = cluster.items
      .slice()
      .sort((left, right) => (right.sequence || 0) - (left.sequence || 0))[0];
    return {
      id,
      kind: "cluster",
      turnId: turn.id,
      x: position.x,
      y: position.y,
      html: `<article class="codex-board-node cluster-node cluster-${escapeHtml(cluster.key)} ${selected ? "selected" : ""}"
        data-codex-node="${escapeHtml(id)}" data-codex-cluster="${escapeHtml(cluster.key)}" data-codex-turn-id="${escapeHtml(turn.id)}"
        tabindex="0" aria-label="${escapeHtml(cluster.label)}，${cluster.items.length} 个事件"
        style="--node-x:${position.x}px;--node-y:${position.y}px">
        <header class="codex-node-drag-handle">
          <span class="codex-node-icon"><i class="ph ${cluster.icon}"></i></span>
          <span class="codex-node-title"><strong>${escapeHtml(cluster.label)}</strong><small>${escapeHtml(cluster.caption)}</small></span>
          <span class="codex-node-status">${cluster.items.length}</span>
        </header>
        <div class="codex-node-body">
          <p class="codex-cluster-types">${escapeHtml(typeLabels.join(" · ") || "结构化事件")}</p>
          <p class="codex-node-summary">${escapeHtml(shortText(readableItemContent(latest), 118))}</p>
          <div class="codex-node-meta">
            <span>TURN ${turn.ordinal} · ${cluster.items.length} EVENTS</span>
            <button type="button" class="codex-node-expand" data-codex-cluster-open="${escapeHtml(cluster.key)}" data-codex-turn-id="${escapeHtml(turn.id)}">
              查看事件 <i class="ph ph-arrow-right"></i>
            </button>
          </div>
        </div>
      </article>`,
    };
  }

  function buildScene() {
    const nodes = [];
    const edges = [];
    boardState.thread.turns.forEach((turn, turnIndex) => {
      const turnNode = renderTurnNode(turn, turnIndex);
      nodes.push(turnNode);
      if (turnIndex > 0) {
        edges.push({source:boardState.thread.turns[turnIndex - 1].id, target:turn.id, kind:"serial"});
      }
      if (turn.id === boardState.selectedTurnId) {
        clustersForTurn(turn).forEach((cluster, clusterIndex) => {
          const clusterNode = renderClusterNode(cluster, turn, clusterIndex);
          nodes.push(clusterNode);
          edges.push({source:turn.id, target:clusterNode.id, kind:"branch"});
        });
      }
    });
    boardState.sceneNodes = nodes;
    boardState.sceneEdges = edges;
  }

  function renderScene({preserveViewport = true} = {}) {
    const {nodes, links, count, description, overlay} = elements();
    if (!nodes || !links || !boardState.thread) return;
    buildScene();
    nodes.innerHTML = boardState.sceneNodes.map(node => node.html).join("");
    links.innerHTML = `<defs>
      <marker id="codex-board-arrow" markerHeight="7" markerWidth="7" orient="auto" refX="6" refY="3.5">
        <path class="codex-board-edge-arrow" d="M0,0 L7,3.5 L0,7 Z"></path>
      </marker>
    </defs>${boardState.sceneEdges.map(edge => `<path class="codex-board-edge ${edge.kind}"
      data-codex-edge-source="${escapeHtml(edge.source)}" data-codex-edge-target="${escapeHtml(edge.target)}"
      marker-end="url(#codex-board-arrow)"></path>`).join("")}`;
    overlay.hidden = true;
    const clusterCount = boardState.sceneNodes.filter(node => node.kind === "cluster").length;
    count.textContent = `${boardState.thread.turns.length} TURNS · ${clusterCount} CLUSTERS`;
    const totalEvents = boardState.thread.turns.reduce((total, turn) => total + turn.items.length, 0);
    description.textContent = `当前会话包含 ${boardState.thread.turns.length} 个 Turn 和 ${totalEvents} 个原始事件。白板只显示 Turn 主线与当前轮次的 ${clusterCount} 个操作簇，完整事件在固定抽屉中搜索和分页查看。使用方向键平移，加减键缩放，数字零适合窗口。`;
    window.requestAnimationFrame(() => {
      updateSceneBounds();
      updateEdges();
      if (!preserveViewport) {
        boardState.transform = {x:64, y:58, scale:.82};
      }
      applyTransform();
    });
    renderEventDrawer();
  }

  function updateSceneBounds() {
    const {scene} = elements();
    if (!scene) return;
    let maxX = 1300;
    let maxY = 760;
    document.querySelectorAll("#codex-board-nodes [data-codex-node]").forEach(node => {
      const position = boardState.positions.get(node.dataset.codexNode);
      if (!position) return;
      maxX = Math.max(maxX, position.x + node.offsetWidth + 160);
      maxY = Math.max(maxY, position.y + node.offsetHeight + 160);
    });
    scene.style.width = `${maxX}px`;
    scene.style.height = `${maxY}px`;
  }

  function nodeElement(id) {
    return [...document.querySelectorAll("#codex-board-nodes [data-codex-node]")]
      .find(node => node.dataset.codexNode === id) || null;
  }

  function nodeAnchor(id, side) {
    const node = nodeElement(id);
    const position = boardState.positions.get(id);
    if (!node || !position) return null;
    if (side === "right") return {x:position.x + node.offsetWidth, y:position.y + node.offsetHeight / 2};
    if (side === "left") return {x:position.x, y:position.y + node.offsetHeight / 2};
    if (side === "top") return {x:position.x + node.offsetWidth / 2, y:position.y};
    return {x:position.x + node.offsetWidth / 2, y:position.y + node.offsetHeight};
  }

  function updateEdges() {
    document.querySelectorAll("#codex-board-links [data-codex-edge-source]").forEach(path => {
      const sourceId = path.dataset.codexEdgeSource;
      const targetId = path.dataset.codexEdgeTarget;
      const serial = path.classList.contains("serial");
      const source = nodeAnchor(sourceId, serial ? "right" : "bottom");
      const target = nodeAnchor(targetId, serial ? "left" : "top");
      if (!source || !target) return;
      const distance = serial
        ? Math.max(42, Math.abs(target.x - source.x) * .48)
        : Math.max(42, Math.abs(target.y - source.y) * .36);
      path.setAttribute(
        "d",
        serial
          ? `M ${source.x} ${source.y} C ${source.x + distance} ${source.y}, ${target.x - distance} ${target.y}, ${target.x} ${target.y}`
          : `M ${source.x} ${source.y} C ${source.x} ${source.y + distance}, ${target.x} ${target.y - distance}, ${target.x} ${target.y}`,
      );
      path.classList.toggle("selected", sourceId === boardState.selectedId || targetId === boardState.selectedId);
    });
  }

  function applyTransform() {
    const {viewport, scene} = elements();
    if (!viewport || !scene) return;
    const {x, y, scale} = boardState.transform;
    scene.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${scale})`;
    const grid = 26 * scale;
    viewport.style.setProperty("--board-grid-size", `${grid}px`);
    viewport.style.setProperty("--board-x", `${x % grid}px`);
    viewport.style.setProperty("--board-y", `${y % grid}px`);
  }

  function setTransform(next) {
    boardState.transform = {
      x: Number.isFinite(next.x) ? next.x : boardState.transform.x,
      y: Number.isFinite(next.y) ? next.y : boardState.transform.y,
      scale: Math.min(2.2, Math.max(.2, Number.isFinite(next.scale) ? next.scale : boardState.transform.scale)),
    };
    applyTransform();
  }

  function zoomAt(clientX, clientY, factor) {
    const {viewport} = elements();
    if (!viewport) return;
    const rect = viewport.getBoundingClientRect();
    const pointerX = clientX - rect.left;
    const pointerY = clientY - rect.top;
    const previous = boardState.transform;
    const nextScale = Math.min(2.2, Math.max(.2, previous.scale * factor));
    const sceneX = (pointerX - previous.x) / previous.scale;
    const sceneY = (pointerY - previous.y) / previous.scale;
    setTransform({
      x: pointerX - sceneX * nextScale,
      y: pointerY - sceneY * nextScale,
      scale: nextScale,
    });
  }

  function fitBoard() {
    const {viewport} = elements();
    const nodes = [...document.querySelectorAll("#codex-board-nodes [data-codex-node]")];
    if (!viewport || !nodes.length) return;
    const bounds = nodes.reduce((result, node) => {
      const position = boardState.positions.get(node.dataset.codexNode);
      if (!position) return result;
      result.minX = Math.min(result.minX, position.x);
      result.minY = Math.min(result.minY, position.y);
      result.maxX = Math.max(result.maxX, position.x + node.offsetWidth);
      result.maxY = Math.max(result.maxY, position.y + node.offsetHeight);
      return result;
    }, {minX:Infinity, minY:Infinity, maxX:-Infinity, maxY:-Infinity});
    const padding = 62;
    const width = Math.max(1, bounds.maxX - bounds.minX);
    const height = Math.max(1, bounds.maxY - bounds.minY);
    const scale = Math.min(1, Math.max(.2, Math.min(
      (viewport.clientWidth - padding * 2) / width,
      (viewport.clientHeight - padding * 2) / height,
    )));
    setTransform({
      x:(viewport.clientWidth - width * scale) / 2 - bounds.minX * scale,
      y:(viewport.clientHeight - height * scale) / 2 - bounds.minY * scale,
      scale,
    });
  }

  function selectedTurn() {
    return boardState.thread?.turns.find(turn => turn.id === boardState.selectedTurnId) || null;
  }

  function selectedCluster() {
    const turn = selectedTurn();
    if (!turn || !boardState.selectedClusterKey || boardState.selectedClusterKey === "all") return null;
    return clustersForTurn(turn).find(cluster => cluster.key === boardState.selectedClusterKey) || null;
  }

  function drawerEvents() {
    const turn = selectedTurn();
    if (!turn) return [];
    const cluster = selectedCluster();
    const source = cluster ? cluster.items : turn.items;
    const query = compactText(boardState.eventQuery).toLocaleLowerCase();
    return source
      .filter(item => !query || compactText([
        item.name,
        item.content,
        typeInfo(item.item_type).label,
        item.item_type,
        ...(item.metadata?.paths || []),
      ].join(" ")).toLocaleLowerCase().includes(query))
      .slice()
      .sort((left, right) => (left.sequence || 0) - (right.sequence || 0));
  }

  function renderDrawerEvent(item, turn) {
    const info = typeInfo(item.item_type);
    const expanded = boardState.expandedItems.has(item.id);
    const paths = Array.isArray(item.metadata?.paths)
      ? [...new Set(item.metadata.paths.map(path => cleanMentionPath(path, boardState.thread?.cwd)).filter(Boolean))]
      : [];
    return `<article class="codex-event-card ${typeClass(item.item_type)} ${expanded ? "expanded" : ""}"
      id="codex-event-${escapeHtml(item.id)}" data-codex-event="${escapeHtml(item.id)}">
      <header>
        <span class="codex-event-icon"><i class="ph ${info.icon}"></i></span>
        <span class="codex-event-heading">
          <strong>${escapeHtml(item.name || info.label)}</strong>
          <small>SEQ ${item.sequence ?? "—"} · ${escapeHtml(formatTime(item.timestamp))} · ${escapeHtml(statusLabel(item.status))}</small>
        </span>
        <span class="codex-event-type">${escapeHtml(info.label)}</span>
      </header>
      <div class="codex-event-body">
        ${expanded
          ? `<pre>${escapeHtml(item.content || "无文本内容")}</pre>`
          : `<p>${escapeHtml(shortText(readableItemContent(item), 360))}</p>`}
        ${paths.length ? `<div class="codex-event-paths">${paths.slice(0, 8).map(path => `<code>${escapeHtml(path)}</code>`).join("")}${paths.length > 8 ? `<span>+${paths.length - 8}</span>` : ""}</div>` : ""}
      </div>
      <footer>
        <span>TURN ${turn.ordinal}${paths.length ? ` · ${paths.length} PATHS` : ""}</span>
        <button type="button" data-codex-item-expand="${escapeHtml(item.id)}" aria-expanded="${expanded}">
          <i class="ph ${expanded ? "ph-caret-up" : "ph-arrows-out-simple"}"></i>${expanded ? "收起完整内容" : "展开完整内容"}
        </button>
      </footer>
    </article>`;
  }

  function renderEventDrawer() {
    const {
      viewport, drawer, drawerTitle, drawerMeta, drawerSearch,
      drawerList, drawerRange, drawerPrev, drawerNext,
    } = elements();
    if (!drawer || !boardState.thread) return;
    const turn = selectedTurn();
    if (!boardState.drawerOpen || !turn) {
      drawer.hidden = true;
      viewport?.classList.remove("drawer-open");
      return;
    }
    const cluster = selectedCluster();
    const events = drawerEvents();
    const pageCount = Math.max(1, Math.ceil(events.length / boardState.eventPageSize));
    boardState.eventPage = Math.max(0, Math.min(boardState.eventPage, pageCount - 1));
    const start = boardState.eventPage * boardState.eventPageSize;
    const pageItems = events.slice(start, start + boardState.eventPageSize);
    drawer.hidden = false;
    viewport?.classList.add("drawer-open");
    drawerTitle.textContent = cluster?.label || `Turn ${turn.ordinal} · 全部事件`;
    drawerMeta.textContent = `${events.length} 个匹配事件 · 原轮次共 ${turn.items.length} 个`;
    if (drawerSearch && drawerSearch.value !== boardState.eventQuery) drawerSearch.value = boardState.eventQuery;
    drawerList.innerHTML = pageItems.length
      ? pageItems.map(item => renderDrawerEvent(item, turn)).join("")
      : '<div class="codex-event-empty"><i class="ph ph-magnifying-glass"></i><strong>没有匹配事件</strong><span>尝试缩短关键词或清除筛选。</span></div>';
    drawerRange.textContent = events.length
      ? `${start + 1}–${Math.min(events.length, start + boardState.eventPageSize)} / ${events.length}`
      : "0 / 0";
    drawerPrev.disabled = boardState.eventPage === 0;
    drawerNext.disabled = boardState.eventPage >= pageCount - 1;
    if (boardState.focusItemId) {
      const focusId = boardState.focusItemId;
      boardState.focusItemId = null;
      window.requestAnimationFrame(() => document.querySelector(`[data-codex-event="${CSS.escape(focusId)}"]`)?.scrollIntoView({block:"center"}));
    }
  }

  function selectTurn(turnId, {openAll = false} = {}) {
    if (!boardState.thread?.turns.some(turn => turn.id === turnId)) return;
    boardState.selectedTurnId = turnId;
    boardState.selectedId = turnId;
    boardState.selectedClusterKey = openAll ? "all" : null;
    boardState.drawerOpen = openAll;
    boardState.eventQuery = "";
    boardState.eventPage = 0;
    renderScene();
  }

  function openCluster(turnId, clusterKey) {
    boardState.selectedTurnId = turnId;
    boardState.selectedClusterKey = clusterKey;
    boardState.selectedId = `cluster:${turnId}:${clusterKey}`;
    boardState.drawerOpen = true;
    boardState.eventQuery = "";
    boardState.eventPage = 0;
    renderScene();
  }

  function focusNode(id, {expand = false} = {}) {
    if (!boardState.thread) return;
    const itemTurn = boardState.thread.turns.find(turn => turn.items.some(item => item.id === id));
    if (itemTurn) {
      const item = itemTurn.items.find(value => value.id === id);
      const cluster = clusterForType(item.item_type);
      boardState.selectedTurnId = itemTurn.id;
      boardState.selectedClusterKey = cluster.key;
      boardState.selectedId = `cluster:${itemTurn.id}:${cluster.key}`;
      boardState.drawerOpen = true;
      boardState.eventQuery = "";
      const clusterItems = clustersForTurn(itemTurn).find(value => value.key === cluster.key)?.items
        .slice()
        .sort((left, right) => (left.sequence || 0) - (right.sequence || 0)) || [];
      boardState.eventPage = Math.max(0, Math.floor(clusterItems.findIndex(value => value.id === id) / boardState.eventPageSize));
      if (expand) boardState.expandedItems.add(id);
      boardState.focusItemId = id;
      renderScene();
      window.requestAnimationFrame(() => focusNodeInViewport(boardState.selectedId));
      return;
    }
    const turn = boardState.thread.turns.find(value => value.id === id);
    if (turn) {
      selectTurn(turn.id);
      window.requestAnimationFrame(() => focusNodeInViewport(turn.id));
    }
  }

  function focusNodeInViewport(id) {
    const {viewport} = elements();
    const node = nodeElement(id);
    const position = boardState.positions.get(id);
    if (!viewport || !node || !position) return;
    boardState.selectedId = id;
    applySelection();
    const scale = Math.max(.72, Math.min(1.05, boardState.transform.scale));
    setTransform({
      x:viewport.clientWidth / 2 - (position.x + node.offsetWidth / 2) * scale,
      y:viewport.clientHeight / 2 - (position.y + Math.min(node.offsetHeight, 360) / 2) * scale,
      scale,
    });
    node.focus({preventScroll:true});
  }

  function applySelection() {
    document.querySelectorAll("#codex-board-nodes [data-codex-node]").forEach(node => {
      node.classList.toggle("selected", node.dataset.codexNode === boardState.selectedId);
    });
    updateEdges();
  }

  function toggleAllTurnItems(turnId) {
    selectTurn(turnId, {openAll:true});
  }

  function renderFallbackShell() {
    const {fallback, fallbackCount} = elements();
    if (!fallback || !boardState.thread) return;
    const total = boardState.thread.turns.reduce((sum, turn) => sum + turn.items.length, 0);
    fallbackCount.textContent = `${total} EVENTS`;
    fallback.innerHTML = boardState.thread.turns.map(turn => `<details class="codex-fallback-turn" data-codex-fallback-turn="${escapeHtml(turn.id)}">
      <summary>Turn ${turn.ordinal} · ${escapeHtml(shortText(turn.goal || "无显式目标", 150))} (${turn.items.length})</summary>
      <div class="codex-fallback-events"><span class="mono-label">展开后载入 ${turn.items.length} 个完整事件</span></div>
    </details>`).join("");
  }

  function populateFallbackTurn(detail) {
    if (detail.dataset.loaded === "true") return;
    const turn = boardState.thread?.turns.find(value => value.id === detail.dataset.codexFallbackTurn);
    if (!turn) return;
    const body = detail.querySelector(".codex-fallback-events");
    body.innerHTML = turn.items
      .slice()
      .sort((left, right) => (left.sequence || 0) - (right.sequence || 0))
      .map(item => {
        const info = typeInfo(item.item_type);
        return `<article class="codex-fallback-event ${typeClass(item.item_type)}">
          <b><i class="ph ${info.icon}"></i> ${escapeHtml(info.label)} · ${escapeHtml(item.name || "")}</b>
          <small>SEQ ${item.sequence ?? "—"} · ${escapeHtml(formatTime(item.timestamp))}</small>
          <pre>${escapeHtml(readableItemContent(item))}</pre>
        </article>`;
      }).join("");
    detail.dataset.loaded = "true";
  }

  function cleanMentionPath(path, cwd = "") {
    const value = String(path || "").trim().replace(/\\/g, "/");
    if (!value || value.length > 380 || /---|\n|\r|\t/.test(value)) return "";
    if (/^(?:https?|data|file):/i.test(value)) return "";
    if (value.includes("/.codex/plugins/") || value.includes("/node_modules/")) return "";
    if (value.startsWith("/") && cwd && !value.startsWith(`${cwd}/`)) return "";
    const relative = cwd && value.startsWith(`${cwd}/`)
      ? value.slice(cwd.length + 1)
      : value.replace(/^\.?\//, "");
    if (!relative || relative.startsWith("../") || relative.includes("/../")) return "";
    if (/\s/.test(relative)) return "";
    const filename = relative.split("/").at(-1) || "";
    const knownNames = new Set(["Makefile", "Dockerfile", "README", "LICENSE", "Procfile"]);
    if (!knownNames.has(filename) && !/\.[A-Za-z0-9][A-Za-z0-9_-]{0,11}$/.test(filename)) return "";
    return relative;
  }

  function mentionNodes(thread, boundPaths) {
    const paths = new Map();
    thread.turns.flatMap(turn => turn.items).forEach(item => {
      if (!["Patch", "FileChange"].includes(item.item_type)) return;
      (item.metadata?.paths || []).forEach(rawPath => {
        const path = cleanMentionPath(rawPath, thread.cwd);
        if (!path || paths.has(path) || boundPaths.has(path)) return;
        paths.set(path, item.item_type);
      });
    });
    const groups = new Map();
    paths.forEach((itemType, path) => {
      const parts = path.split("/").filter(Boolean);
      const group = parts.length > 1 ? parts[0] : "project-root";
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push({path, itemType});
    });
    return [...groups.entries()].map(([group, entries]) => {
      entries.sort((left, right) => left.path.localeCompare(right.path));
      const title = group === "project-root" ? "项目根目录文件" : `${group}/`;
      return {
        id:`mention-group:${group}`,
        source:"code",
        title,
        locator:`${entries.length} 个会话记录路径`,
        snippet:entries.map(entry => `${entry.itemType} · ${entry.path}`).join("\n"),
        relation:"mention",
        relationLabel:"会话提及",
        confidence:null,
        openItem:null,
      };
    }).sort((left, right) => left.title.localeCompare(right.title));
  }

  function bindingNodes(bindings) {
    return bindings.map(binding => ({
      id:binding.target_entity_id || binding.id,
      source:"code",
      title:binding.target_path || binding.changed_path || "代码实体",
      locator:`${binding.repository_name || binding.repository_id || "repository"} · ${binding.target_commit_sha ? binding.target_commit_sha.slice(0, 12) : "current"}`,
      snippet:`${binding.changed_path || "会话变更"} → ${binding.target_path || binding.target_entity_id || "代码实体"}`,
      relation:binding.review_status === "confirmed" ? "confirmed" : "candidate",
      relationLabel:binding.review_status === "confirmed" ? "已确认映射" : "待复核映射",
      confidence:binding.confidence,
      reviewStatus:binding.review_status,
      derivation:binding.derivation,
      openItem:{
        source:"code",
        entity_id:binding.target_entity_id,
        repository_id:binding.repository_id,
        path:binding.target_path,
        start_line:1,
        end_line:1,
      },
    }));
  }

  function semanticNodes(payload) {
    return (payload?.results || [])
      .filter(item => ["code", "experiment", "document"].includes(item.source))
      .map(item => ({
        id:item.entity_id,
        source:item.source,
        title:item.title || item.entity_id,
        locator:item.locator || item.entity_id,
        snippet:item.snippet || "该节点由跨来源语义检索召回。",
        relation:"semantic",
        relationLabel:"语义关联",
        confidence:item.score,
        derivation:"cross-source-score-v1",
        openItem:item,
      }));
  }

  function mergeRelated(bindings, semantic, mentions) {
    const merged = new Map();
    [...bindings, ...semantic, ...mentions].forEach(item => {
      if (!item.id || merged.has(item.id)) return;
      merged.set(item.id, item);
    });
    return [...merged.values()].sort((left, right) => {
      const relationRank = {confirmed:0, candidate:1, semantic:2, mention:3};
      const sourceRank = {code:0, experiment:1, document:2};
      return (relationRank[left.relation] ?? 9) - (relationRank[right.relation] ?? 9)
        || (sourceRank[left.source] ?? 9) - (sourceRank[right.source] ?? 9)
        || (right.confidence || 0) - (left.confidence || 0);
    });
  }

  async function loadRelated(thread) {
    const requestId = ++boardState.relatedRequest;
    const {related, relatedStatus} = elements();
    related.innerHTML = `<div class="empty-state compact"><span class="empty-symbol">···</span><p>正在核对代码映射与跨来源语义节点</p></div>`;
    relatedStatus.textContent = "正在读取真实映射与语义关联";
    const query = compactText([
      thread.title,
      thread.turns[0]?.goal,
      thread.turns.at(-1)?.goal,
    ].filter(Boolean).join(" · ")).slice(0, 900);
    const [bindingResult, searchResult] = await Promise.allSettled([
      boardState.options.api(`/v1/bindings/candidates?thread_id=${encodeURIComponent(thread.id)}&limit=500`),
      boardState.options.api("/v1/search", {
        method:"POST",
        body:JSON.stringify({
          query:query || thread.title,
          project_id:thread.project_id || "project-rag",
          sources:["code", "experiment", "document"],
          limit:18,
          include_lineage:true,
        }),
      }),
    ]);
    if (requestId !== boardState.relatedRequest || boardState.thread?.thread_id !== thread.thread_id) return;
    const bindings = bindingResult.status === "fulfilled" ? bindingResult.value : [];
    const semantic = searchResult.status === "fulfilled" ? searchResult.value : {results:[]};
    const directNodes = bindingNodes(bindings);
    const boundPaths = new Set(directNodes.map(item => item.title).filter(Boolean));
    boardState.related = mergeRelated(directNodes, semanticNodes(semantic), mentionNodes(thread, boundPaths));
    renderRelated();
    const failures = [bindingResult, searchResult].filter(result => result.status === "rejected").length;
    if (failures) relatedStatus.textContent += ` · ${failures} 个来源暂不可用`;
  }

  function renderRelated() {
    const {
      related,
      relatedFilters,
      relatedCount,
      relatedStatus,
      relatedPager,
      relatedPageSummary,
    } = elements();
    if (!related || !relatedFilters) return;
    const counts = boardState.related.reduce((map, item) => {
      map[item.source] = (map[item.source] || 0) + 1;
      return map;
    }, {});
    relatedCount.textContent = `${boardState.related.length} NODES`;
    const direct = boardState.related.filter(item => ["confirmed", "candidate"].includes(item.relation)).length;
    const semantic = boardState.related.filter(item => item.relation === "semantic").length;
    relatedStatus.textContent = `${direct} 个路径映射 · ${semantic} 个语义关联`;
    relatedFilters.innerHTML = [
      {key:"all", label:"全部", count:boardState.related.length},
      ...Object.entries(RELATED_SOURCES).map(([key, meta]) => ({key, label:meta.label, count:counts[key] || 0})),
    ].map(item => `<button type="button" class="${boardState.relatedFilter === item.key ? "active" : ""}"
      data-codex-related-filter="${item.key}" aria-pressed="${boardState.relatedFilter === item.key}">
      ${escapeHtml(item.label)} ${item.count}
    </button>`).join("");
    const visible = boardState.related.filter(item => boardState.relatedFilter === "all" || item.source === boardState.relatedFilter);
    if (!visible.length) {
      related.innerHTML = '<div class="empty-state compact"><span class="empty-symbol">∅</span><p>当前筛选下没有可核验的关联节点</p></div>';
      if (relatedPager) relatedPager.hidden = true;
      return;
    }
    const pageCount = Math.ceil(visible.length / boardState.relatedPageSize);
    boardState.relatedPage = Math.min(boardState.relatedPage, pageCount - 1);
    const start = boardState.relatedPage * boardState.relatedPageSize;
    const pageItems = visible.slice(start, start + boardState.relatedPageSize);
    related.innerHTML = pageItems.map(item => {
      const source = RELATED_SOURCES[item.source] || {label:item.source, icon:"ph-cube"};
      const expanded = boardState.expandedRelated.has(item.id);
      const index = boardState.related.indexOf(item);
      const confidence = item.confidence == null ? "无评分" : `${Math.round(item.confidence * 100)}%`;
      return `<article class="codex-related-card source-${escapeHtml(item.source)}" data-codex-related-card="${index}">
        <button type="button" class="codex-related-main" data-codex-related-expand="${index}" aria-expanded="${expanded}">
          <span class="codex-related-icon"><i class="ph ${source.icon}"></i></span>
          <span class="codex-related-title"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(source.label)} · ${escapeHtml(item.locator)}</small></span>
          <span class="codex-related-kind">${escapeHtml(item.relationLabel)}</span>
        </button>
        <div class="codex-related-detail" ${expanded ? "" : "hidden"}>
          ${item.source === "code"
            ? `<pre><code>${escapeHtml(item.snippet)}</code></pre>`
            : `<p>${escapeHtml(item.snippet)}</p>`}
          <code>${escapeHtml(item.id)}</code>
          <div class="codex-related-actions">
            <span>${escapeHtml(item.derivation || item.relation)} · ${confidence}</span>
            ${item.openItem ? `<button type="button" class="codex-related-open" data-codex-related-open="${index}">打开来源 <i class="ph ph-arrow-square-out"></i></button>` : '<span>尚未建立可导航映射</span>'}
          </div>
        </div>
      </article>`;
    }).join("");
    if (relatedPager) {
      relatedPager.hidden = pageCount <= 1;
      relatedPageSummary.textContent = `${start + 1}–${Math.min(start + boardState.relatedPageSize, visible.length)} / ${visible.length}`;
      const previous = relatedPager.querySelector('[data-codex-related-page="previous"]');
      const next = relatedPager.querySelector('[data-codex-related-page="next"]');
      previous.disabled = boardState.relatedPage === 0;
      next.disabled = boardState.relatedPage >= pageCount - 1;
    }
  }

  function setLoading(message = "正在装载结构化会话事件") {
    const {overlay} = elements();
    if (!overlay) return;
    overlay.hidden = false;
    overlay.innerHTML = `<i class="ph ph-spinner-gap" aria-hidden="true"></i><strong>${escapeHtml(message)}</strong><span>正在整理 Turn、事件类型与跨来源节点</span>`;
  }

  function setError(message) {
    const {overlay} = elements();
    if (!overlay) return;
    overlay.hidden = false;
    overlay.innerHTML = `<i class="ph ph-warning-circle" aria-hidden="true"></i><strong>会话加载失败</strong><span>${escapeHtml(message)}</span>`;
  }

  function render(thread, options = {}) {
    if (!thread) return;
    boardState.options = options;
    const changed = boardState.thread?.thread_id !== thread.thread_id;
    if (changed) resetForThread(thread);
    else boardState.thread = thread;
    installEvents();
    renderScene({preserveViewport:!changed});
    renderFallbackShell();
    loadRelated(thread).catch(error => {
      const {related, relatedStatus} = elements();
      if (related) related.innerHTML = `<div class="empty-state compact"><p>${escapeHtml(error.message)}</p></div>`;
      if (relatedStatus) relatedStatus.textContent = "关联来源加载失败";
    });
  }

  function installEvents() {
    if (boardState.installed) return;
    const {viewport} = elements();
    if (!viewport) return;
    boardState.installed = true;

    viewport.addEventListener("wheel", event => {
      if (event.target.closest(".codex-event-drawer")) return;
      event.preventDefault();
      zoomAt(event.clientX, event.clientY, Math.exp(-event.deltaY * .00115));
    }, {passive:false});

    viewport.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      if (event.target.closest(".codex-event-drawer")) return;
      const handle = event.target.closest(".codex-node-drag-handle");
      if (handle) {
        const node = handle.closest("[data-codex-node]");
        const position = boardState.positions.get(node.dataset.codexNode);
        if (!position) return;
        event.preventDefault();
        viewport.setPointerCapture(event.pointerId);
        node.classList.add("dragging");
        boardState.pointer = {
          kind:"node",
          pointerId:event.pointerId,
          id:node.dataset.codexNode,
          startX:event.clientX,
          startY:event.clientY,
          originX:position.x,
          originY:position.y,
          moved:false,
        };
        return;
      }
      if (event.target.closest("button, summary, .codex-node-content")) return;
      event.preventDefault();
      viewport.setPointerCapture(event.pointerId);
      viewport.classList.add("panning");
      boardState.pointer = {
        kind:"pan",
        pointerId:event.pointerId,
        startX:event.clientX,
        startY:event.clientY,
        originX:boardState.transform.x,
        originY:boardState.transform.y,
        moved:false,
      };
    });

    viewport.addEventListener("pointermove", event => {
      const pointer = boardState.pointer;
      if (!pointer || pointer.pointerId !== event.pointerId) return;
      const dx = event.clientX - pointer.startX;
      const dy = event.clientY - pointer.startY;
      pointer.moved ||= Math.abs(dx) + Math.abs(dy) > 3;
      if (pointer.kind === "pan") {
        setTransform({x:pointer.originX + dx, y:pointer.originY + dy});
        return;
      }
      const position = boardState.positions.get(pointer.id);
      const node = nodeElement(pointer.id);
      if (!position || !node) return;
      position.x = pointer.originX + dx / boardState.transform.scale;
      position.y = pointer.originY + dy / boardState.transform.scale;
      position.pinned = true;
      node.style.setProperty("--node-x", `${position.x}px`);
      node.style.setProperty("--node-y", `${position.y}px`);
      updateSceneBounds();
      updateEdges();
    });

    const endPointer = event => {
      const pointer = boardState.pointer;
      if (!pointer || pointer.pointerId !== event.pointerId) return;
      viewport.releasePointerCapture?.(event.pointerId);
      viewport.classList.remove("panning");
      if (pointer.kind === "node") nodeElement(pointer.id)?.classList.remove("dragging");
      if (pointer.kind === "pan" && !pointer.moved) {
        boardState.selectedId = null;
        applySelection();
      }
      boardState.pointer = null;
    };
    viewport.addEventListener("pointerup", endPointer);
    viewport.addEventListener("pointercancel", endPointer);

    viewport.addEventListener("click", event => {
      const clusterTrigger = event.target.closest("[data-codex-turn-cluster], [data-codex-cluster-open]");
      if (clusterTrigger) {
        openCluster(clusterTrigger.dataset.codexTurnId, clusterTrigger.dataset.codexTurnCluster || clusterTrigger.dataset.codexClusterOpen);
        return;
      }
      const turnExpand = event.target.closest("[data-codex-turn-expand]");
      if (turnExpand) {
        toggleAllTurnItems(turnExpand.dataset.codexTurnExpand);
        return;
      }
      const itemExpand = event.target.closest("[data-codex-item-expand]");
      if (itemExpand) {
        const id = itemExpand.dataset.codexItemExpand;
        if (boardState.expandedItems.has(id)) boardState.expandedItems.delete(id);
        else boardState.expandedItems.add(id);
        renderEventDrawer();
        return;
      }
      const node = event.target.closest("[data-codex-node]");
      if (node && !boardState.pointer) {
        if (node.dataset.codexTurnNode) {
          selectTurn(node.dataset.codexTurnNode);
        } else if (node.dataset.codexCluster) {
          openCluster(node.dataset.codexTurnId, node.dataset.codexCluster);
        }
      }
    });

    viewport.addEventListener("keydown", event => {
      if (event.target.closest("button")) return;
      const amount = event.shiftKey ? 110 : 46;
      if (event.key === "ArrowLeft") setTransform({x:boardState.transform.x + amount});
      else if (event.key === "ArrowRight") setTransform({x:boardState.transform.x - amount});
      else if (event.key === "ArrowUp") setTransform({y:boardState.transform.y + amount});
      else if (event.key === "ArrowDown") setTransform({y:boardState.transform.y - amount});
      else if (event.key === "+" || event.key === "=") zoomAt(viewport.getBoundingClientRect().left + viewport.clientWidth / 2, viewport.getBoundingClientRect().top + viewport.clientHeight / 2, 1.16);
      else if (event.key === "-") zoomAt(viewport.getBoundingClientRect().left + viewport.clientWidth / 2, viewport.getBoundingClientRect().top + viewport.clientHeight / 2, 1 / 1.16);
      else if (event.key === "0") fitBoard();
      else if (event.key === "Escape") {
        if (boardState.drawerOpen) {
          boardState.drawerOpen = false;
          boardState.selectedClusterKey = null;
          boardState.selectedId = boardState.selectedTurnId;
          renderScene();
        } else {
          boardState.selectedId = null;
          applySelection();
        }
      } else return;
      event.preventDefault();
    });

    document.addEventListener("click", event => {
      const zoom = event.target.closest("[data-codex-board-zoom]");
      if (zoom) {
        const rect = viewport.getBoundingClientRect();
        if (zoom.dataset.codexBoardZoom === "fit") fitBoard();
        else zoomAt(
          rect.left + viewport.clientWidth / 2,
          rect.top + viewport.clientHeight / 2,
          zoom.dataset.codexBoardZoom === "in" ? 1.18 : 1 / 1.18,
        );
        return;
      }
      const drawerClose = event.target.closest("#codex-event-drawer-close");
      if (drawerClose) {
        boardState.drawerOpen = false;
        boardState.selectedClusterKey = null;
        boardState.selectedId = boardState.selectedTurnId;
        renderScene();
        return;
      }
      const drawerPage = event.target.closest("[data-codex-event-page]");
      if (drawerPage) {
        boardState.eventPage += drawerPage.dataset.codexEventPage === "next" ? 1 : -1;
        renderEventDrawer();
        return;
      }
      const filter = event.target.closest("[data-codex-related-filter]");
      if (filter) {
        boardState.relatedFilter = filter.dataset.codexRelatedFilter;
        boardState.relatedPage = 0;
        renderRelated();
        return;
      }
      const relatedPage = event.target.closest("[data-codex-related-page]");
      if (relatedPage) {
        boardState.relatedPage += relatedPage.dataset.codexRelatedPage === "next" ? 1 : -1;
        renderRelated();
        elements().related?.scrollTo({top:0, behavior:"instant"});
        return;
      }
      const relatedExpand = event.target.closest("[data-codex-related-expand]");
      if (relatedExpand) {
        const item = boardState.related[Number(relatedExpand.dataset.codexRelatedExpand)];
        if (!item) return;
        if (boardState.expandedRelated.has(item.id)) boardState.expandedRelated.delete(item.id);
        else boardState.expandedRelated.add(item.id);
        renderRelated();
        return;
      }
      const relatedOpen = event.target.closest("[data-codex-related-open]");
      if (relatedOpen) {
        const item = boardState.related[Number(relatedOpen.dataset.codexRelatedOpen)];
        if (item?.openItem) boardState.options.openRelated?.(item);
      }
    });

    document.querySelector("#codex-event-filter")?.addEventListener("input", event => {
      boardState.eventQuery = event.target.value;
      boardState.eventPage = 0;
      renderEventDrawer();
    });

    document.querySelector("#codex-fallback-list")?.addEventListener("toggle", event => {
      const detail = event.target.closest("[data-codex-fallback-turn]");
      if (detail?.open) populateFallbackTurn(detail);
    }, true);

    window.addEventListener("resize", () => {
      updateSceneBounds();
      updateEdges();
    });
  }

  window.CodexSessionBoard = {
    render,
    focus: id => focusNode(id, {expand:true}),
    fit: fitBoard,
    setLoading,
    setError,
  };
})();
