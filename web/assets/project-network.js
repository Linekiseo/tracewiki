(function () {
  "use strict";

  const DOMAIN_META = {
    research: {label: "研究", icon: "ph-lightbulb"},
    codex: {label: "Codex 会话", icon: "ph-chats-circle"},
    code: {label: "代码与版本", icon: "ph-code"},
    experiment: {label: "实验", icon: "ph-flask"},
    document: {label: "文档", icon: "ph-file-text"},
    review: {label: "复核", icon: "ph-shield-check"},
  };

  const ACTION_LABELS = {
    "topic.created": "创建研究主题",
    "topic.updated": "更新研究主题",
    "topic.deleted": "删除研究主题",
    "iteration.created": "创建研究迭代",
    "iteration.updated": "推进研究迭代",
    "iteration.deleted": "删除研究迭代",
    "work_item.created": "创建执行任务",
    "work_item.updated": "更新执行任务",
    "work_item.transitioned": "推进任务状态",
    "work_item.deleted": "删除执行任务",
    "iteration.evidence_linked": "关联研究证据",
    "iteration.evidence_unlinked": "移除研究证据",
    "relation.created": "建立证据关系",
    "relation.reviewed": "完成人工复核",
    "project.updated": "更新项目设置",
  };

  const ui = {
    config: null,
    model: {nodes: [], edges: []},
    installed: false,
    mode: "network",
    inspectorTab: "overview",
    selectedNodeId: null,
    inspectorDismissed: true,
    scale: 1,
    offsetX: 16,
    offsetY: 8,
    positions: new Map(),
    expanded: new Set(),
    enabledRelations: new Set(),
    knownRelations: new Set(),
    pointer: null,
    suppressClick: false,
    initialFitDone: false,
    sessionDetail: null,
    sessionDetailLoading: null,
    resizeTimer: null,
  };

  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];
  const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
  const number = value => new Intl.NumberFormat("zh-CN").format(Number(value || 0));
  const time = value => value
    ? new Intl.DateTimeFormat("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}).format(new Date(value))
    : "—";
  const shortCommit = value => value ? String(value).slice(0, 10) : "未索引";
  const statusTone = status => {
    if (["failed", "blocked", "rejected", "contradicted", "cancelled"].includes(status)) return "danger";
    if (["planned", "review", "awaiting_approval", "unreviewed", "backlog"].includes(status)) return "warning";
    if (["active", "in_progress", "running", "validating", "indexing"].includes(status)) return "active";
    return "success";
  };

  function positioned(input) {
    const saved = ui.positions.get(input.id);
    const coordinates = saved || {x: input.x, y: input.y};
    ui.positions.set(input.id, coordinates);
    return {...input, x: coordinates.x, y: coordinates.y};
  }

  function sessionEvidence(thread, summary) {
    if (!thread || thread.thread_id !== summary?.thread_id) return {paths: [], validations: [], goal: ""};
    const items = (thread.turns || []).flatMap(turn => turn.items || []);
    const cwd = String(summary.cwd || "").replace(/\/+$/, "");
    const paths = [...new Set(items
      .filter(item => ["FileChange", "Patch"].includes(item.item_type))
      .flatMap(item => item.metadata?.paths || [])
      .map(path => String(path || "").trim())
      .filter(path => path && !/^[+\-.\/\s]+$/.test(path))
      .map(path => cwd && path.startsWith(`${cwd}/`) ? path.slice(cwd.length + 1) : path)
      .filter(path => /\.[a-zA-Z0-9]{1,8}$/.test(path.split("/").pop() || "") || /(^|\/)(Makefile|Dockerfile|README|LICENSE)$/.test(path))
    )].slice(0, 6);
    const validations = items
      .filter(item => item.item_type === "CommandExecution" && item.metadata?.exit_code !== undefined && item.metadata?.exit_code !== null)
      .map(item => ({
        command: String(item.content || item.name || "命令验证").split("\n")[0].slice(0, 120),
        exitCode: Number(item.metadata.exit_code),
      }))
      .slice(-8);
    const goal = [...(thread.turns || [])].reverse().find(turn => String(turn.goal || "").trim())?.goal || "";
    return {paths, validations, goal};
  }

  function buildModel(state) {
    if (window.ProjectNetworkModel?.build) {
      return window.ProjectNetworkModel.build({
        state,
        ui,
        helpers: {
          number,
          time,
          shortCommit,
          sessionEvidence,
          statusLabel: status => ui.config.labels.statusLabel(status || "ready"),
        },
      });
    }
    const project = state.workspace?.project || {};
    const stats = state.workspace?.stats || {};
    const topic = state.topics.find(item => item.id === state.selectedTopic) || state.topics[0] || null;
    const topicIterations = state.iterations.filter(item => item.topic_id === topic?.id);
    const iteration = topicIterations.find(item => item.id === state.selectedIteration)
      || topicIterations.find(item => ["active", "validating"].includes(item.status))
      || topicIterations[0]
      || null;
    const workItems = state.workItems.filter(item => item.topic_id === topic?.id);
    const work = workItems.find(item => ["running", "review", "ready"].includes(item.status)) || workItems[0] || null;
    const sessions = [...(state.codexSessions || [])].sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
    const session = sessions[0] || null;
    const sessionData = sessionEvidence(ui.sessionDetail, session);
    const repositories = state.repositories || [];
    const experiments = state.experiments || [];
    const runs = state.experimentRuns || state.runs || [];
    const documents = state.documents || [];
    const claims = state.claims || [];
    const binding = state.bindingStats || {};
    const nodes = [];
    const edges = [];
    const add = input => {
      const item = positioned(input);
      nodes.push(item);
      return item.id;
    };
    const connect = (source, target, label, options = {}) => {
      if (!source || !target) return;
      edges.push({
        source, target, label,
        state: options.state || "solid",
        tone: options.tone || "neutral",
        sourcePort: options.sourcePort,
        targetPort: options.targetPort,
        route: options.route || "orthogonal",
      });
    };

    const focusId = add({
      id: topic ? `focus:${topic.id}` : `project:${project.id || "current"}`,
      domain: "research", variant: "focus", focus: true, highlight: true,
      kind: "假设", badge: "H1", code: topic?.display_key,
      title: topic?.title || project.name || "当前科研项目",
      meta: `创建：${time(topic?.created_at || project.created_at)} · ${topic?.owner || project.owner || "研究人员"}`,
      detail: topic?.problem_statement || topic?.objective || project.description || "围绕当前项目组织代码、会话、实验和文档证据。",
      status: topic?.status || project.status || "active",
      source: topic || project, action: topic ? "edit-topic" : null,
      evidence: `${topicIterations.reduce((sum, item) => sum + Number(item.evidence_count || 0), 0)} 条已关联证据`,
      x: 254, y: 110,
    });

    const repositoryId = add({
      id: "cluster:repositories", domain: "code", variant: "cluster", cluster: true,
      kind: "历史代码", title: `${number(repositories.length || stats.repositories)} 个仓库版本`,
      meta: `${number(repositories.reduce((sum, item) => sum + Number(item.stats?.commits || 0), 0))} commits`,
      detail: "已索引仓库、分支和提交提供实现的版本上下文。",
      status: repositories.every(item => item.status === "ready") ? "ready" : "indexing",
      icon: "ph-git-branch", count: repositories.length, expandKey: "repositories",
      route: "repository", evidence: `${number(repositories.reduce((sum, item) => sum + Number(item.stats?.files || 0), 0))} 个文件`,
      x: 46, y: 58,
    });

    const literatureId = add({
      id: "cluster:literature", domain: "document", variant: "cluster", cluster: true,
      kind: "相关文献", title: `${number(documents.length)} 篇科研文档`,
      meta: `${number(claims.length)} 条主张`,
      detail: "当前项目已接入的论文、方案与研究记录。",
      status: documents.length ? "ready" : "planned", icon: "ph-files",
      count: documents.length, expandKey: "documents", route: "documents",
      evidence: `${number(documents.length)} 个可追溯文档来源`,
      x: 46, y: 116,
    });
    connect(repositoryId, focusId, "版本依据", {state: "pending", sourcePort: "right", targetPort: "left"});
    connect(literatureId, focusId, "相关研究", {state: "pending", sourcePort: "right", targetPort: "left"});

    const paper = documents[0] || null;
    const paperId = add({
      id: paper ? `paper:${paper.id}` : "paper:pending",
      domain: "document", variant: "paper", kind: "文献",
      code: paper?.display_key || "LIT-01",
      title: paper?.title || paper?.name || "当前研究的主要文献依据",
      meta: paper ? `更新：${time(paper.updated_at)}` : "尚待关联主要文献",
      detail: paper?.abstract || paper?.description || "将支持当前假设的主要文献关联到研究主题。",
      status: paper ? "ready" : "planned", icon: "ph-book-open-text",
      source: paper, route: paper ? "documents" : null,
      evidence: paper ? "已进入项目文档索引" : "等待研究人员关联",
      x: 315, y: 16,
    });
    connect(paperId, focusId, "支持", {tone: "blue", sourcePort: "bottom", targetPort: "top"});

    const iterationId = add({
      id: iteration ? `iteration:${iteration.id}` : `iteration:empty:${topic?.id || "current"}`,
      domain: "research", variant: "iteration", kind: "当前迭代",
      code: iteration?.display_key || "I00", title: iteration?.title || "建立首个研究迭代",
      meta: iteration ? `状态：${ui.config.labels.statusLabel(iteration.status)}` : "尚未开始",
      detail: iteration?.goal || iteration?.hypothesis || "为当前研究问题建立可执行的验证轮次。",
      status: iteration?.status || "planned", icon: "ph-git-commit",
      source: iteration, action: iteration ? "edit-iteration" : "create-iteration",
      evidence: `${iteration?.evidence_count || 0} 条迭代证据`,
      x: 664, y: 112,
    });
    connect(focusId, iterationId, "推进", {state: "pending", sourcePort: "right", targetPort: "left"});

    const workId = add({
      id: work ? `work:${work.id}` : "work:planned",
      domain: "research", variant: "task", kind: "开发任务",
      code: work?.display_key || "CX-001", title: work?.title || "实现假设验证所需的开发任务",
      meta: `${work?.assignee_type === "codex" ? "Codex" : work?.owner || "待分配"} · ${time(work?.updated_at)}`,
      detail: work?.objective || "由当前假设自动带入项目、迭代与验收上下文。",
      status: work?.status || "planned", icon: "ph-clipboard-text",
      source: work, action: work ? "edit-work" : null, route: work?.assignee_type === "codex" ? "codex-bridge" : null,
      evidence: work?.acceptance_criteria || "等待执行与验证结果",
      x: 276, y: 212,
    });
    connect(focusId, workId, "需要实现", {sourcePort: "bottom", targetPort: "top"});
    connect(iterationId, workId, "归属", {state: "pending", sourcePort: "bottom", targetPort: "right"});

    const sessionId = add({
      id: session ? `session:${session.thread_id}` : "session:pending",
      domain: "codex", variant: "session", kind: "Codex 会话",
      code: session?.thread_key ? `#${session.thread_key}` : "#—",
      title: session?.title || "等待 Codex 执行",
      meta: session ? `${time(session.updated_at)} · ${number(session.turn_count)} 轮` : "交给 Codex 后自动关联",
      detail: session ? `工作目录：${session.cwd || "未记录"}` : "从研究任务启动 Codex 后，会话会在此形成可追溯节点。",
      status: session?.status || "planned", icon: "ph-chats-circle",
      source: session, route: session ? "codex" : null,
      evidence: session ? `${number(session.command_count)} 次命令 · ${number(session.file_change_count)} 项变更` : "尚无会话证据",
      x: -8, y: 304,
    });

    const changeId = add({
      id: session ? `changes:${session.thread_id}` : "changes:planned",
      domain: "code", variant: "code-main", highlight: true, kind: "代码",
      code: work?.display_key || "PR-127",
      title: work?.title || sessionData.goal || "当前研发实现",
      meta: `Codex · ${time(session?.updated_at)}`,
      detail: session ? "由 Codex 会话产生的代码变更集合；提交与测试状态需独立核对。" : "开发任务执行后会在此汇总代码变更。",
      status: session?.file_change_count ? "running" : "planned", icon: "ph-git-branch",
      source: session, route: session ? "codex" : null,
      evidence: session ? `${number(session.item_count)} 个结构化事件` : "等待代码执行",
      x: 280, y: 304,
    });
    connect(sessionId, changeId, "产生", {tone: "purple", sourcePort: "right", targetPort: "left"});
    connect(workId, changeId, "执行", {tone: "blue", sourcePort: "bottom", targetPort: "top"});

    const fileFallbacks = ["src/pipeline.py", "src/evidence.py", "tests/test_pipeline.py"];
    const filePaths = sessionData.paths.length ? sessionData.paths.slice(0, 3) : fileFallbacks;
    const fileIds = filePaths.map((path, index) => add({
      id: `file:${index}:${path}`, domain: "code", variant: "file", kind: "代码文件",
      title: path.split("/").pop() || path, meta: path, detail: path,
      status: sessionData.paths.length ? (index === 2 ? "review" : "ready") : "planned",
      icon: "ph-code", source: {path}, route: sessionData.paths.length ? "repository" : null,
      evidence: sessionData.paths.length ? "路径来自 Codex 结构化文件变更事件" : "待执行任务中的预期文件位置",
      x: 634, y: 286 + index * 42,
    }));
    fileIds.forEach((fileId, index) => connect(changeId, fileId, index === 2 ? "待测试" : "实现", {
      tone: index === 2 ? "red" : "blue", state: index === 2 ? "pending" : "solid",
      sourcePort: "right", targetPort: "left",
    }));

    const baseline = experiments[1] || experiments[0] || null;
    const currentExperiment = experiments[0] || null;
    const baselineId = add({
      id: baseline ? `experiment:baseline:${baseline.id}` : "experiment:baseline",
      domain: "experiment", variant: "experiment", kind: "实验（基线）",
      code: baseline?.display_key || "EXP-BASE", title: baseline?.name || baseline?.title || "基线实验",
      meta: baseline ? `${number(baseline.run_count)} Run · ${time(baseline.updated_at)}` : "等待建立基线",
      detail: baseline?.objective || baseline?.description || "为当前方法建立可对照的实验基线。",
      status: baseline?.status || "planned", icon: "ph-flask",
      source: baseline, route: baseline ? "experiments" : null,
      evidence: baseline ? `${number(baseline.run_count)} 个实验运行` : "等待实验数据",
      x: -8, y: 417,
    });
    const experimentId = add({
      id: currentExperiment ? `experiment:${currentExperiment.id}` : "experiment:current",
      domain: "experiment", variant: "experiment", kind: "实验（当前）",
      code: currentExperiment?.display_key || "EXP-CURRENT",
      title: currentExperiment?.name || currentExperiment?.title || "当前验证实验",
      meta: currentExperiment ? `${number(currentExperiment.run_count)} Run · ${time(currentExperiment.updated_at)}` : "等待运行",
      detail: currentExperiment?.objective || currentExperiment?.description || "执行当前实现并收集指标与产物。",
      status: currentExperiment?.status || "planned", icon: "ph-flask",
      source: currentExperiment, route: currentExperiment ? "experiments" : null,
      evidence: currentExperiment ? `${number(currentExperiment.run_count)} 个实验运行` : "等待实验数据",
      x: 251, y: 417,
    });
    connect(baselineId, experimentId, "对照", {state: "pending", sourcePort: "right", targetPort: "left"});
    connect(changeId, experimentId, "待测试", {tone: "blue", state: "pending", sourcePort: "bottom", targetPort: "top"});

    const latestRun = runs[0] || null;
    const metricId = add({
      id: latestRun ? `metric:${latestRun.id}` : "metric:planned",
      domain: "experiment", variant: "metric", kind: "指标",
      code: latestRun?.display_key || "METRIC", title: latestRun?.name || "核心验证指标",
      meta: latestRun?.status || "等待实验结果", detail: "实验运行产生的关键评价指标。",
      status: latestRun?.status || "planned", icon: "ph-chart-line-up",
      source: latestRun, route: latestRun ? "experiments" : null,
      evidence: latestRun ? "来自版本化实验运行" : "尚无指标结果",
      x: 508, y: 417,
    });
    connect(experimentId, metricId, "产生", {tone: "green", sourcePort: "right", targetPort: "left"});

    const document = documents[1] || documents[0] || null;
    const documentId = add({
      id: document ? `document:${document.id}` : "document:planned",
      domain: "document", variant: "document", kind: "文档",
      code: document?.display_key || "DOC-01",
      title: document?.title || document?.name || "实验设计与结果记录",
      meta: document ? `更新：${time(document.updated_at)}` : "等待生成",
      detail: document?.description || "记录实验配置、结论与复现边界。",
      status: document ? "ready" : "planned", icon: "ph-file-text",
      source: document, route: document ? "documents" : null,
      evidence: document ? `${number(document.claim_count)} 条文档主张` : "尚未形成文档证据",
      x: -8, y: 540,
    });

    const claim = claims[0] || null;
    const claimId = add({
      id: claim ? `claim:${claim.id}` : "claim:planned",
      domain: "document", variant: "claim", kind: "Claim",
      code: claim?.display_key || "C-01",
      title: claim?.title || claim?.statement || "当前实现达到预期研究目标",
      meta: claim ? `更新：${time(claim.updated_at)}` : "等待实验支持",
      detail: claim?.statement || "结论必须同时获得文档、实验和版本化实现的支持。",
      status: claim?.status || "review", icon: "ph-note",
      source: claim, route: claim ? "documents" : null,
      evidence: claim ? "已进入主张复核流程" : "等待可接受证据",
      x: 251, y: 540,
    });
    connect(documentId, claimId, "包含", {sourcePort: "right", targetPort: "left"});
    connect(experimentId, claimId, "验证", {tone: "green", sourcePort: "bottom", targetPort: "top"});
    connect(metricId, claimId, "支持", {tone: "green", sourcePort: "bottom", targetPort: "right"});

    const reviewId = add({
      id: "review:current", domain: "review", variant: "review", kind: "复核任务",
      code: "R-09", title: `${number(binding.pending || 0)} 条关系等待确认`,
      meta: `${number(binding.confirmed || 0)} 已确认 · ${number(binding.total || 0)} 候选`,
      detail: "待复核关系不会自动成为结论证据，关键证据必须由用户确认。",
      status: Number(binding.pending || 0) ? "review" : "ready", icon: "ph-shield-check",
      source: binding, route: "bindings", evidence: `${number(binding.pending || 0)} 条待人工复核关系`,
      x: 663, y: 540,
    });
    connect(fileIds[2], reviewId, "测试失败", {tone: "red", state: "conflict", sourcePort: "bottom", targetPort: "top"});
    connect(claimId, reviewId, "矛盾", {tone: "red", state: "conflict", sourcePort: "right", targetPort: "left"});

    const conclusionId = add({
      id: "conclusion:draft", domain: "research", variant: "conclusion", kind: "结论草案",
      code: iteration?.display_key || "CONCLUSION", title: iteration?.conclusion || "等待证据复核后形成结论",
      meta: Number(binding.pending || 0) ? "草稿 · 待复核" : "可进入结论整理",
      detail: "只有通过复核的代码、实验与文档证据才能支持最终结论。",
      status: Number(binding.pending || 0) ? "review" : "ready", icon: "ph-file-check",
      source: iteration, action: iteration ? "edit-iteration" : null,
      evidence: `${iteration?.evidence_count || 0} 条迭代证据`,
      x: 243, y: 633,
    });
    connect(claimId, conclusionId, "支持", {tone: "green", sourcePort: "bottom", targetPort: "top"});

    if (ui.expanded.has("repositories")) {
      repositories.slice(0, 3).forEach((repository, index) => {
        const id = add({
          id: `repository:${repository.id}`, domain: "code", variant: "file", kind: "代码仓库",
          title: repository.name, meta: `${repository.default_branch || "HEAD"} · ${shortCommit(repository.head_commit)}`,
          detail: repository.source_url || repository.local_path, status: repository.status, icon: "ph-git-repository",
          source: repository, route: "repository", evidence: `${number(repository.stats?.files)} 文件`,
          x: 44, y: 196 + index * 54,
        });
        connect(repositoryId, id, "包含", {sourcePort: "bottom", targetPort: "top"});
      });
    }

    if (ui.expanded.has("documents")) {
      documents.slice(0, 3).forEach((item, index) => {
        const id = add({
          id: `document:expanded:${item.id}`, domain: "document", variant: "file", kind: "科研文档",
          title: item.title || item.name, meta: `${number(item.claim_count)} 条主张`,
          detail: item.description || "项目文档", status: item.status || "ready", icon: "ph-file-text",
          source: item, route: "documents", evidence: "已进入项目索引",
          x: 44, y: 252 + index * 54,
        });
        connect(literatureId, id, "包含", {sourcePort: "bottom", targetPort: "top"});
      });
    }

    return {
      nodes, edges, focusId, defaultSelectedId: changeId,
      topic, iteration, project, work, session,
    };
  }

  function visibleModel() {
    const availableNodes = ui.model.nodes.filter(item =>
      !item.contextKey
      || ui.expanded.has(item.contextKey)
      || item.id === ui.selectedNodeId
    );
    const availableIds = new Set(availableNodes.map(item => item.id));
    const edges = ui.model.edges.filter(item =>
      ui.enabledRelations.has(item.label)
      && availableIds.has(item.source)
      && availableIds.has(item.target)
    );
    const connected = new Set(edges.flatMap(item => [item.source, item.target]));
    const nodes = availableNodes.filter(item =>
      item.id === ui.model.focusId
      || item.id === ui.selectedNodeId
      || connected.has(item.id)
    );
    return {nodes, edges};
  }

  function renderContext() {
    const {state, labels} = ui.config;
    const {project, topic, iteration} = ui.model;
    const intelligence = state.workspaceIntelligence;
    qs("#workspace-project-title").textContent = project.name || "科研项目";
    qs(".project-switcher strong").textContent = project.name || "科研项目";
    qs("#workspace-context-iteration").textContent = iteration?.title || topic?.title || "尚未选择研究主题";
    const contextStatus = qs("#workspace-context-status");
    contextStatus.textContent = topic ? labels.statusLabel(topic.status) : "等待研究主题";
    contextStatus.dataset.level = intelligence?.readiness?.level || "unknown";
    qs("#research-command-form input[name=objective]").placeholder = "询问当前关系，或交给 Codex 处理…";
    qs("#research-network-focus-title").textContent = topic?.title || project.name || "研究关系网";
    qs("#open-iteration").disabled = !topic;
    qs("#open-work-item").disabled = !topic;
    qs("#edit-topic").disabled = !topic;
    const list = qs("#research-context-topic-list");
    list.innerHTML = state.topics.length ? state.topics.map(item => `
      <button type="button" class="research-context-topic ${item.id === state.selectedTopic ? "active" : ""}" data-research-topic="${escapeHtml(item.id)}">
        <i class="ph ph-lightbulb"></i>
        <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.problem_statement || item.objective || "待补充研究问题")}</small></span>
        <span>${escapeHtml(labels.statusLabel(item.status))}</span>
      </button>`).join("") : '<div class="research-context-topic-empty">尚未创建研究主题</div>';
  }

  function renderFilterMenu() {
    const counts = ui.model.edges.reduce((result, edge) => {
      result.set(edge.label, (result.get(edge.label) || 0) + 1);
      return result;
    }, new Map());
    qs("#research-relation-menu").innerHTML = [...counts].map(([label, count]) => `
      <label class="research-relation-option">
        <input type="checkbox" data-relation-label="${escapeHtml(label)}" ${ui.enabledRelations.has(label) ? "checked" : ""}>
        <i></i><span>${escapeHtml(label)}</span><b>${count}</b>
      </label>`).join("");
  }

  function renderNetwork() {
    const {nodes, edges} = visibleModel();
    const worldWidth = ui.model.worldWidth || 930;
    const worldHeight = ui.model.worldHeight || 740;
    const world = qs("#research-network-world");
    const svg = qs("#research-network-edges");
    world.style.width = `${worldWidth}px`;
    world.style.height = `${worldHeight}px`;
    svg.setAttribute("viewBox", `0 0 ${worldWidth} ${worldHeight}`);
    svg.style.width = `${worldWidth}px`;
    svg.style.height = `${worldHeight}px`;
    const edgeStates = new Set(edges.map(edge => edge.state));
    const legend = qs(".research-network-legend");
    legend.innerHTML = [
      edgeStates.has("solid") ? '<span><i class="research-edge-swatch solid"></i>已建立</span>' : "",
      edgeStates.has("pending") ? '<span><i class="research-edge-swatch dashed"></i>待复核</span>' : "",
      edgeStates.has("conflict") ? '<span><i class="research-edge-swatch conflict"></i>矛盾</span>' : "",
    ].join("");
    legend.hidden = !edgeStates.size;
    qs("#research-network-empty").hidden = Boolean(ui.model.topic || ui.model.project?.id);
    qs("#research-network-node-layer").innerHTML = nodes.map(item => {
      const meta = DOMAIN_META[item.domain] || DOMAIN_META.research;
      const icon = item.badge
        ? `<span class="research-node-icon research-node-badge">${escapeHtml(item.badge)}</span>`
        : `<span class="research-node-icon"><i class="ph ${item.icon || meta.icon}"></i></span>`;
      const expand = item.expandKey
        ? `<span class="research-node-expand"><i class="ph ${ui.expanded.has(item.expandKey) ? "ph-caret-up" : "ph-caret-down"}"></i></span>`
        : "";
      return `<button type="button"
        class="research-network-node node-${escapeHtml(item.variant || "standard")} ${item.focus ? "focus-node" : ""} ${item.cluster ? "cluster-node" : ""} ${item.highlight ? "is-highlighted" : ""} ${item.contextual ? "contextual-node" : ""} ${ui.selectedNodeId === item.id ? "selected" : ""}"
        data-research-node="${escapeHtml(item.id)}" data-domain="${escapeHtml(item.domain)}"
        style="left:${item.x}px;top:${item.y}px" aria-pressed="${ui.selectedNodeId === item.id}">
        ${icon}
        <span class="research-node-copy">
          <span>${escapeHtml(item.kind)}${item.code ? ` <b>${escapeHtml(item.code)}</b>` : ""}</span>
          <strong>${escapeHtml(item.title)}</strong>
          <small>${escapeHtml(item.meta || "")}</small>
        </span>
        ${item.count ? `<span class="research-node-count">${number(item.count)}</span>` : ""}
        <i class="research-node-state ${statusTone(item.status)}" title="${escapeHtml(ui.config.labels.statusLabel(item.status || "ready"))}"></i>
        ${expand}
      </button>`;
    }).join("");
    renderEdges();
    updateTransform();
  }

  function nodeElement(id) {
    return qs(`[data-research-node="${CSS.escape(id)}"]`);
  }

  function anchor(item, element, port) {
    const width = element.offsetWidth;
    const height = element.offsetHeight;
    if (port === "left") return {x: item.x, y: item.y + height / 2};
    if (port === "right") return {x: item.x + width, y: item.y + height / 2};
    if (port === "top") return {x: item.x + width / 2, y: item.y};
    return {x: item.x + width / 2, y: item.y + height};
  }

  function inferredPorts(source, target) {
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? ["right", "left"] : ["left", "right"];
    return dy >= 0 ? ["bottom", "top"] : ["top", "bottom"];
  }

  function orthogonalPath(start, end, sourcePort, targetPort) {
    const vertical = ["top", "bottom"].includes(sourcePort);
    if (vertical) {
      const midY = start.y + (end.y - start.y) / 2;
      return {
        d: `M${start.x},${start.y} V${midY} H${end.x} V${end.y}`,
        label: {x: (start.x + end.x) / 2, y: midY - 6},
      };
    }
    const midX = start.x + (end.x - start.x) / 2;
    return {
      d: `M${start.x},${start.y} H${midX} V${end.y} H${end.x}`,
      label: {x: midX, y: (start.y + end.y) / 2 - 6},
    };
  }

  function renderEdges() {
    const {edges} = visibleModel();
    const nodeMap = new Map(ui.model.nodes.map(item => [item.id, item]));
    const markup = [];
    edges.forEach(edge => {
      const source = nodeMap.get(edge.source);
      const target = nodeMap.get(edge.target);
      const sourceElement = nodeElement(edge.source);
      const targetElement = nodeElement(edge.target);
      if (!source || !target || !sourceElement || !targetElement) return;
      const inferred = inferredPorts(source, target);
      const sourcePort = edge.sourcePort || inferred[0];
      const targetPort = edge.targetPort || inferred[1];
      const start = anchor(source, sourceElement, sourcePort);
      const end = anchor(target, targetElement, targetPort);
      const path = orthogonalPath(start, end, sourcePort, targetPort);
      const tone = ["blue", "green", "red", "purple", "amber"].includes(edge.tone) ? edge.tone : "neutral";
      markup.push(`<path class="research-edge ${edge.state || "solid"} tone-${tone}" d="${path.d}"></path>`);
      markup.push(`<circle class="research-edge-port tone-${tone}" cx="${start.x}" cy="${start.y}" r="2.4"></circle>`);
      markup.push(`<text class="research-edge-label tone-${tone}" x="${path.label.x}" y="${path.label.y}" text-anchor="middle">${escapeHtml(edge.label)}</text>`);
    });
    qs("#research-network-edge-layer").innerHTML = markup.join("");
  }

  function updateTransform() {
    const canvas = qs("#research-network-canvas");
    if (canvas) {
      canvas.scrollTop = 0;
      canvas.scrollLeft = 0;
    }
    qs("#research-network-world").style.transform = `translate(${ui.offsetX}px, ${ui.offsetY}px) scale(${ui.scale})`;
    qs("#research-zoom-level").textContent = `${Math.round(ui.scale * 100)}%`;
  }

  function setScale(next, origin) {
    const canvas = qs("#research-network-canvas");
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const scale = Math.max(.4, Math.min(1.8, next));
    const point = origin || {x: rect.width / 2, y: rect.height / 2};
    const worldX = (point.x - ui.offsetX) / ui.scale;
    const worldY = (point.y - ui.offsetY) / ui.scale;
    ui.offsetX = point.x - worldX * scale;
    ui.offsetY = point.y - worldY * scale;
    ui.scale = scale;
    updateTransform();
  }

  function fitView() {
    const {nodes} = visibleModel();
    const canvas = qs("#research-network-canvas");
    if (!nodes.length || !canvas) return;
    const boxes = nodes.map(item => {
      const element = nodeElement(item.id);
      return {x: item.x, y: item.y, width: element?.offsetWidth || 220, height: element?.offsetHeight || 70};
    });
    const minX = Math.min(...boxes.map(item => item.x));
    const minY = Math.min(...boxes.map(item => item.y));
    const maxX = Math.max(...boxes.map(item => item.x + item.width));
    const maxY = Math.max(...boxes.map(item => item.y + item.height));
    const bounds = canvas.getBoundingClientRect();
    const paddingX = 38;
    const paddingY = 24;
    const scale = Math.max(.62, Math.min(1, Math.min(
      (bounds.width - paddingX * 2) / Math.max(1, maxX - minX),
      (bounds.height - paddingY * 2) / Math.max(1, maxY - minY)
    )));
    ui.scale = scale;
    ui.offsetX = paddingX - minX * scale;
    ui.offsetY = paddingY - minY * scale;
    updateTransform();
  }

  function selectNode(id) {
    if (!ui.model.nodes.some(item => item.id === id)) return;
    ui.selectedNodeId = id;
    ui.inspectorDismissed = false;
    qs("#research-node-inspector").hidden = false;
    qs("#research-network-workarea").classList.add("has-inspector");
    renderNetwork();
    renderInspector();
  }

  function closeInspector() {
    ui.selectedNodeId = null;
    ui.inspectorDismissed = true;
    qs("#research-node-inspector").hidden = true;
    qs("#research-network-workarea").classList.remove("has-inspector");
    renderNetwork();
    requestAnimationFrame(fitView);
  }

  function selectedRelations(nodeId) {
    return ui.model.edges.filter(edge => edge.source === nodeId || edge.target === nodeId).map(edge => {
      const otherId = edge.source === nodeId ? edge.target : edge.source;
      return {...edge, other: ui.model.nodes.find(item => item.id === otherId), outgoing: edge.source === nodeId};
    }).filter(item => item.other);
  }

  function renderInspector() {
    const item = ui.model.nodes.find(nodeItem => nodeItem.id === ui.selectedNodeId);
    if (!item) return;
    const meta = DOMAIN_META[item.domain] || DOMAIN_META.research;
    const source = item.source || {};
    qs("#research-inspector-kind").textContent = `${meta.label} · ${item.code || item.kind}`;
    qs("#research-inspector-title").textContent = item.title;
    const technical = ["code", "codex"].includes(item.domain);
    const sessionData = technical
      ? sessionEvidence(ui.sessionDetail, source.thread_id ? source : ui.model.session)
      : {paths: [], validations: [], goal: ""};
    const passedValidations = sessionData.validations.filter(validation => validation.exitCode === 0).length;
    const failedValidations = sessionData.validations.length - passedValidations;
    if (!technical && ["diff", "tests"].includes(ui.inspectorTab)) ui.inspectorTab = "overview";
    qsa("[data-research-inspector-tab]").forEach(button => {
      button.hidden = !technical && ["diff", "tests"].includes(button.dataset.researchInspectorTab);
      button.classList.toggle("active", button.dataset.researchInspectorTab === ui.inspectorTab);
    });
    const relations = selectedRelations(item.id);
    const content = qs("#research-inspector-content");
    if (ui.inspectorTab === "overview") {
      const author = source.assignee_type === "codex"
        ? "Codex"
        : source.author_name || source.author || source.owner || source.actor || (item.domain === "codex" ? "Codex" : "系统推断");
      const facts = [
        ["状态", ui.config.labels.statusLabel(item.status || "ready")],
        ["负责", author],
        ["创建", time(source.created_at || source.started_at)],
        ["更新", time(item.source?.updated_at)],
      ];
      const technicalEvidence = technical ? `
        <section class="research-inspector-section">
          <div class="research-inspector-heading"><h3>验证状态</h3><button type="button" data-inspector-tab-jump="tests">查看测试</button></div>
          <div class="research-inspector-test-card ${failedValidations ? "has-failure" : ""}">
            <div><strong>${passedValidations} / ${sessionData.validations.length}</strong><span>${sessionData.validations.length ? "命令验证通过" : "等待测试结果"}</span></div>
            <dl><div><dt>通过</dt><dd>${passedValidations}</dd></div><div><dt>失败</dt><dd>${failedValidations}</dd></div><div><dt>变更</dt><dd>${number(source.file_change_count || sessionData.paths.length)}</dd></div></dl>
          </div>
          ${sessionData.paths.length ? `<div class="research-inspector-code-list compact">${sessionData.paths.slice(0, 4).map(path => `<button type="button" data-inspector-path="${escapeHtml(path)}"><i class="ph ph-file-code"></i><span>${escapeHtml(path)}</span><i class="ph ph-arrow-square-out"></i></button>`).join("")}</div>` : '<p class="research-inspector-empty-copy">当前会话尚未提供可定位的文件路径；变更计数仍保留为结构化事实。</p>'}
        </section>` : "";
      content.innerHTML = `
        <section class="research-inspector-section inspector-summary"><p>${escapeHtml(item.detail || "当前节点已进入项目上下文。")}</p></section>
        <section class="research-inspector-section"><h3>节点信息</h3><dl class="research-inspector-facts">
          ${facts.map(([label, value]) => `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}
        </dl></section>${technicalEvidence}
        <section class="research-inspector-section"><h3>与本节点相关</h3>
          ${relations.length ? relations.map(relation => {
            const relationMeta = DOMAIN_META[relation.other.domain] || DOMAIN_META.research;
            return `<button class="research-inspector-relation" type="button" data-related-node="${escapeHtml(relation.other.id)}"><i class="ph ${relationMeta.icon}"></i><div><strong>${escapeHtml(relation.other.title)}</strong><small>${relation.outgoing ? `${relation.label} →` : `← ${relation.label}`} · ${escapeHtml(relationMeta.label)}</small></div><i class="ph ph-caret-right"></i></button>`;
          }).join("") : "<p>当前没有直接关系。</p>"}
        </section>`;
    } else if (ui.inspectorTab === "diff") {
      const paths = item.source?.path ? [item.source.path] : sessionData.paths;
      content.innerHTML = `<section class="research-inspector-section"><h3>变更文件</h3>
        ${paths.length ? `<div class="research-inspector-code-list">${paths.map(path => `<button type="button" data-inspector-path="${escapeHtml(path)}"><i class="ph ph-file-code"></i><span>${escapeHtml(path)}</span><i class="ph ph-arrow-square-out"></i></button>`).join("")}</div>` : "<p>这个节点没有可定位的文件 Diff。</p>"}
        </section><section class="research-inspector-section"><h3>版本边界</h3><p>会话变更与 Git 提交分别核验，系统不会把尚未提交的 Patch 视为稳定版本。</p></section>`;
    } else if (ui.inspectorTab === "tests") {
      content.innerHTML = `<section class="research-inspector-section"><h3>测试状态</h3>
        <div class="research-test-summary"><strong>${passedValidations} / ${sessionData.validations.length}</strong><span>验证通过</span></div>
        ${sessionData.validations.length ? sessionData.validations.map(validation => `<div class="research-validation ${validation.exitCode === 0 ? "passed" : "failed"}"><i class="ph ${validation.exitCode === 0 ? "ph-check-circle" : "ph-x-circle"}"></i><span><strong>${escapeHtml(validation.command)}</strong><small>exit ${validation.exitCode}</small></span></div>`).join("") : "<p>当前没有带退出码的测试结果。</p>"}
        </section>`;
    } else {
      content.innerHTML = `<section class="research-inspector-section"><h3>可追溯证据</h3><p>${escapeHtml(item.evidence || "该节点尚未形成可接受的研究证据。")}</p></section>
        <section class="research-inspector-section"><h3>证据边界</h3><p>进入关系网不等于已验证；最终结论仍需人工复核。</p></section>`;
    }
    const actions = [];
    if (item.route) actions.push(`<button type="button" class="${item.domain === "code" ? "primary" : ""}" data-inspector-action="route"><i class="ph ${item.domain === "code" ? "ph-code" : "ph-arrow-square-out"}"></i>${item.domain === "code" ? "打开 Diff" : "打开来源"}</button>`);
    if (item.domain !== "research") actions.push('<button type="button" data-inspector-action="evidence"><i class="ph ph-link"></i>关联证据</button>');
    if (item.action) {
      const createAction = ["create-topic", "create-iteration"].includes(item.action);
      actions.push(`<button type="button" class="${createAction ? "primary" : ""}" data-inspector-action="${escapeHtml(item.action)}"><i class="ph ${createAction ? "ph-plus" : "ph-pencil-simple"}"></i>${item.action === "create-topic" ? "创建主题" : item.action === "create-iteration" ? "创建迭代" : "编辑"}</button>`);
    }
    if (item.domain === "research" && ui.model.topic) actions.push('<button type="button" class="primary" data-inspector-action="handoff"><i class="ph ph-terminal-window"></i>交给 Codex</button>');
    actions.push(`<div class="research-inspector-more">
      <button type="button" data-inspector-action="more" aria-expanded="false"><i class="ph ph-dots-three"></i>更多操作<i class="ph ph-caret-down"></i></button>
      <div id="research-inspector-more-menu" hidden>
        <button type="button" data-inspector-action="expand-related"><i class="ph ph-selection-plus"></i><span><strong>展开相关节点</strong><small>在画布中显示一阶关系</small></span></button>
        <button type="button" data-inspector-action="review"><i class="ph ph-shield-check"></i><span><strong>进入关系复核</strong><small>确认跨来源关系</small></span></button>
        <button type="button" data-inspector-action="copy-id"><i class="ph ph-copy"></i><span><strong>复制节点标识</strong><small>用于定位和审计</small></span></button>
      </div>
    </div>`);
    qs("#research-inspector-actions").innerHTML = actions.join("");
  }

  function renderRecord() {
    const activity = (ui.config.state.workspace?.recent_activity || []).filter(item => !item.action.startsWith("api."));
    qs("#research-record-list").innerHTML = activity.length ? activity.map(item => {
      const detail = item.detail || {};
      const summary = detail.title || detail.predicate || (detail.from && detail.to ? `${detail.from} → ${detail.to}` : item.resource_type);
      return `<article class="research-record-item"><time>${time(item.created_at)}</time><span class="research-record-marker"></span>
        <div class="research-record-copy"><strong>${escapeHtml(ACTION_LABELS[item.action] || item.action)}</strong><p>${escapeHtml(summary)} · ${escapeHtml(item.actor || "系统")}</p></div></article>`;
    }).join("") : '<div class="empty-state"><h3>暂无研究记录</h3><p>研究活动会按时间汇入这里。</p></div>';
  }

  function renderProducts() {
    const state = ui.config.state;
    const groups = [
      ["代码与版本", state.repositories || [], "repository", "ph-git-repository", item => `${number(item.stats?.files)} 文件 · ${shortCommit(item.head_commit)}`],
      ["研发会话", state.codexSessions || [], "codex", "ph-chats-circle", item => `${number(item.turn_count)} 轮 · ${number(item.file_change_count)} 变更`],
      ["实验与数据", state.experiments || [], "experiments", "ph-flask", item => `${number(item.run_count)} 个 Run`],
      ["科研文档", state.documents || [], "documents", "ph-file-text", item => `${number(item.claim_count)} 条主张`],
    ].filter(([, items]) => items.length);
    qs("#research-products-list").innerHTML = groups.length ? groups.map(([title, items, route, icon, meta]) => `
      <section class="research-product-group"><h2>${title}</h2>${items.map(item => `<button type="button" class="research-product-row" data-product-route="${route}">
        <i class="ph ${icon}"></i><span><strong>${escapeHtml(item.name || item.title || item.display_key || "未命名产物")}</strong><small>${escapeHtml(meta(item))}</small></span><span>${time(item.updated_at)}</span>
      </button>`).join("")}</section>`).join("") : '<div class="empty-state"><h3>暂无研究产物</h3></div>';
  }

  function renderMode() {
    qsa("[data-research-view]").forEach(button => button.classList.toggle("active", button.dataset.researchView === ui.mode));
    qsa("[data-research-panel]").forEach(panel => { panel.hidden = panel.dataset.researchPanel !== ui.mode; });
    if (ui.mode === "record") renderRecord();
    if (ui.mode === "products") renderProducts();
    if (ui.mode === "network") requestAnimationFrame(renderEdges);
  }

  function rebuild() {
    ui.model = buildModel(ui.config.state);
    ui.model.edges.forEach(edge => {
      if (!ui.knownRelations.has(edge.label)) ui.enabledRelations.add(edge.label);
      ui.knownRelations.add(edge.label);
    });
    if (ui.selectedNodeId && !ui.model.nodes.some(item => item.id === ui.selectedNodeId)) ui.selectedNodeId = null;
    if (!ui.selectedNodeId && !ui.inspectorDismissed) ui.selectedNodeId = ui.model.defaultSelectedId;
    const inspectorOpen = Boolean(ui.selectedNodeId);
    qs("#research-node-inspector").hidden = !inspectorOpen;
    qs("#research-network-workarea").classList.toggle("has-inspector", inspectorOpen);
    renderContext();
    renderFilterMenu();
    renderNetwork();
    renderRecord();
    renderProducts();
    if (inspectorOpen) renderInspector();
  }

  async function hydrateLatestSession() {
    const summary = ui.config?.state?.codexSessions?.[0];
    if (!summary || typeof ui.config.actions.loadCodexThread !== "function") return;
    if (ui.sessionDetail?.thread_id === summary.thread_id || ui.sessionDetailLoading === summary.thread_id) return;
    ui.sessionDetailLoading = summary.thread_id;
    try {
      ui.sessionDetail = await ui.config.actions.loadCodexThread(summary.thread_id);
      ui.initialFitDone = false;
      rebuild();
      requestAnimationFrame(() => {
        ui.initialFitDone = true;
        fitView();
      });
    } catch {
      ui.sessionDetail = null;
    } finally {
      ui.sessionDetailLoading = null;
    }
  }

  function invokeNodeAction(action, item) {
    const actions = ui.config.actions;
    if (action === "route" && item.route) actions.openSource(item.route, item.source);
    if (action === "edit-topic") actions.editTopic(item.source);
    if (action === "create-topic") actions.createTopic();
    if (action === "edit-iteration") actions.editIteration(item.source?.id);
    if (action === "edit-work") actions.editWorkItem(item.source?.id);
    if (action === "create-iteration") actions.createIteration();
    if (action === "handoff") actions.startCodex("");
    if (action === "evidence") actions.openSource("search", item.source);
    if (action === "expand-related") {
      selectedRelations(item.id).map(relation => relation.other?.expandKey).filter(Boolean).forEach(key => ui.expanded.add(key));
      rebuild();
      requestAnimationFrame(fitView);
    }
    if (action === "review") actions.openSource("bindings", item.source);
    if (action === "copy-id") {
      navigator.clipboard?.writeText(item.id);
      const toast = qs("#toast");
      if (toast) {
        toast.textContent = "节点标识已复制";
        toast.classList.add("show");
        window.setTimeout(() => toast.classList.remove("show"), 1600);
      }
    }
  }

  function installEvents() {
    if (ui.installed) return;
    ui.installed = true;
    qs("#research-context-switcher").addEventListener("click", () => {
      const menu = qs("#research-context-menu");
      menu.hidden = !menu.hidden;
      qs("#research-context-switcher").setAttribute("aria-expanded", String(!menu.hidden));
    });
    qs("#research-relation-filter").addEventListener("click", () => {
      const menu = qs("#research-relation-menu");
      menu.hidden = !menu.hidden;
      qs("#research-relation-filter").setAttribute("aria-expanded", String(!menu.hidden));
    });
    qs("#research-context-topic-list").addEventListener("click", event => {
      const button = event.target.closest("[data-research-topic]");
      if (!button) return;
      qs("#research-context-menu").hidden = true;
      ui.config.actions.selectTopic(button.dataset.researchTopic);
    });
    qs("#research-relation-menu").addEventListener("change", event => {
      const input = event.target.closest("[data-relation-label]");
      if (!input) return;
      if (input.checked) ui.enabledRelations.add(input.dataset.relationLabel);
      else ui.enabledRelations.delete(input.dataset.relationLabel);
      renderNetwork();
    });
    qsa("[data-research-view]").forEach(button => button.addEventListener("click", () => {
      ui.mode = button.dataset.researchView;
      renderMode();
    }));
    qsa("[data-research-inspector-tab]").forEach(button => button.addEventListener("click", () => {
      ui.inspectorTab = button.dataset.researchInspectorTab;
      renderInspector();
    }));
    qs("#research-inspector-close").addEventListener("click", closeInspector);
    qs("#research-inspector-actions").addEventListener("click", event => {
      const control = event.target.closest("[data-inspector-action]");
      const item = ui.model.nodes.find(nodeItem => nodeItem.id === ui.selectedNodeId);
      if (!control || !item) return;
      if (control.dataset.inspectorAction === "more") {
        const menu = qs("#research-inspector-more-menu");
        menu.hidden = !menu.hidden;
        control.setAttribute("aria-expanded", String(!menu.hidden));
        return;
      }
      invokeNodeAction(control.dataset.inspectorAction, item);
    });
    qs("#research-inspector-content").addEventListener("click", event => {
      const related = event.target.closest("[data-related-node]");
      const path = event.target.closest("[data-inspector-path]");
      const tabJump = event.target.closest("[data-inspector-tab-jump]");
      if (related) selectNode(related.dataset.relatedNode);
      if (path) ui.config.actions.openSource("repository", {path: path.dataset.inspectorPath});
      if (tabJump) {
        ui.inspectorTab = tabJump.dataset.inspectorTabJump;
        renderInspector();
      }
    });
    qs("#research-expand-neighbors").addEventListener("click", () => {
      selectedRelations(ui.selectedNodeId || ui.model.focusId).map(relation => relation.other?.expandKey).filter(Boolean).forEach(key => ui.expanded.add(key));
      rebuild();
    });
    qs("#research-expand-all").addEventListener("click", () => {
      ui.model.nodes.map(item => item.expandKey).filter(Boolean).forEach(key => ui.expanded.add(key));
      ui.expanded.add("all-relations");
      rebuild();
      requestAnimationFrame(fitView);
    });
    qs("#research-add-node").addEventListener("click", () => {
      const menu = qs("#research-add-node-menu");
      menu.hidden = !menu.hidden;
      qs("#research-add-node").setAttribute("aria-expanded", String(!menu.hidden));
    });
    qs("#research-add-node-menu").addEventListener("click", event => {
      const control = event.target.closest("[data-add-research-node]");
      if (!control) return;
      qs("#research-add-node-menu").hidden = true;
      qs("#research-add-node").setAttribute("aria-expanded", "false");
      const type = control.dataset.addResearchNode;
      if (type === "topic") ui.config.actions.createTopic();
      if (type === "iteration") ui.config.actions.createIteration();
      if (type === "work") ui.config.actions.createWorkItem();
      if (type === "experiment") ui.config.actions.createExperiment();
      if (type === "document") ui.config.actions.createDocument();
    });
    qs("#research-zoom-in").addEventListener("click", () => setScale(ui.scale + .1));
    qs("#research-zoom-out").addEventListener("click", () => setScale(ui.scale - .1));
    qs("#research-fit-view").addEventListener("click", fitView);
    qs("#research-record-refresh").addEventListener("click", () => ui.config.actions.refresh());
    qs("#research-command-form").addEventListener("submit", event => {
      event.preventDefault();
      const input = event.currentTarget.elements.objective;
      ui.config.actions.startCodex(input.value.trim());
      input.value = "";
    });
    qs("#research-products-list").addEventListener("click", event => {
      const button = event.target.closest("[data-product-route]");
      if (button) ui.config.actions.openSource(button.dataset.productRoute);
    });

    const canvas = qs("#research-network-canvas");
    canvas.addEventListener("wheel", event => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      setScale(ui.scale * Math.exp(-event.deltaY * .0012), {x: event.clientX - rect.left, y: event.clientY - rect.top});
    }, {passive: false});
    canvas.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      const element = event.target.closest("[data-research-node]");
      const item = element && ui.model.nodes.find(nodeItem => nodeItem.id === element.dataset.researchNode);
      ui.suppressClick = false;
      ui.pointer = {
        type: item ? "node" : "canvas", id: item?.id,
        expand: Boolean(event.target.closest(".research-node-expand")),
        startX: event.clientX, startY: event.clientY,
        originX: item ? item.x : ui.offsetX, originY: item ? item.y : ui.offsetY,
      };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", event => {
      if (!ui.pointer) return;
      const dx = event.clientX - ui.pointer.startX;
      const dy = event.clientY - ui.pointer.startY;
      if (Math.abs(dx) + Math.abs(dy) > 5) ui.suppressClick = true;
      if (ui.pointer.type === "canvas") {
        ui.offsetX = ui.pointer.originX + dx;
        ui.offsetY = ui.pointer.originY + dy;
        updateTransform();
        return;
      }
      const item = ui.model.nodes.find(nodeItem => nodeItem.id === ui.pointer.id);
      if (!item) return;
      item.x = ui.pointer.originX + dx / ui.scale;
      item.y = ui.pointer.originY + dy / ui.scale;
      ui.positions.set(item.id, {x: item.x, y: item.y});
      const element = nodeElement(item.id);
      if (element) {
        element.style.left = `${item.x}px`;
        element.style.top = `${item.y}px`;
      }
      renderEdges();
    });
    canvas.addEventListener("pointerup", event => {
      if (!ui.pointer) return;
      const pointer = ui.pointer;
      ui.pointer = null;
      canvas.releasePointerCapture(event.pointerId);
      if (pointer.type === "node" && !ui.suppressClick) {
        const item = ui.model.nodes.find(nodeItem => nodeItem.id === pointer.id);
        if (item?.expandKey && pointer.expand) {
          if (ui.expanded.has(item.expandKey)) ui.expanded.delete(item.expandKey);
          else ui.expanded.add(item.expandKey);
          rebuild();
        } else {
          selectNode(pointer.id);
        }
      }
    });
    canvas.addEventListener("keydown", event => {
      if (event.key === "+" || event.key === "=") setScale(ui.scale + .1);
      if (event.key === "-") setScale(ui.scale - .1);
      if (event.key === "0") fitView();
    });
    document.addEventListener("pointerdown", event => {
      if (!event.target.closest(".research-context-picker")) qs("#research-context-menu").hidden = true;
      if (!event.target.closest(".research-filter")) qs("#research-relation-menu").hidden = true;
      if (!event.target.closest(".research-add-node")) {
        qs("#research-add-node-menu").hidden = true;
        qs("#research-add-node").setAttribute("aria-expanded", "false");
      }
      if (!event.target.closest(".research-inspector-more")) {
        const menu = qs("#research-inspector-more-menu");
        if (menu) menu.hidden = true;
      }
    });
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape") return;
      qs("#research-context-menu").hidden = true;
      qs("#research-relation-menu").hidden = true;
      if (!qs("#research-node-inspector").hidden) closeInspector();
    });
    window.addEventListener("resize", () => {
      window.clearTimeout(ui.resizeTimer);
      ui.resizeTimer = window.setTimeout(() => {
        if (document.body.dataset.currentView === "workspace" && ui.mode === "network") fitView();
      }, 90);
    });
  }

  function render(config) {
    ui.config = config;
    installEvents();
    rebuild();
    renderMode();
    hydrateLatestSession();
    requestAnimationFrame(() => {
      if (!ui.initialFitDone && qs("#research-network-canvas")) {
        ui.initialFitDone = true;
        fitView();
      }
    });
  }

  window.ProjectNetworkWorkbench = {render, fitView};
})();
