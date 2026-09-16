(function () {
  "use strict";

  const PREDICATE_LABELS = {
    implemented_by: "实现",
    validated_by: "验证",
    supported_by: "支持",
    reports: "报告",
    uses: "使用版本",
    parent: "父提交",
    derived_from: "派生自",
    aggregates: "聚合",
    has_notebook: "包含 Notebook",
    changed_path_maps_to: "映射到",
    applied_as: "应用为",
    produced_by: "产生于",
    related_to: "关联",
    contradicts: "矛盾",
  };

  const HIDDEN_STATUSES = new Set(["cancelled", "archived", "tombstoned"]);

  function build({state, ui, helpers}) {
    const {number, time, shortCommit, sessionEvidence} = helpers;
    const project = state.workspace?.project || {};
    const stats = state.workspace?.stats || {};
    const topic = state.topics.find(item => item.id === state.selectedTopic) || state.topics[0] || null;
    const iterations = state.iterations
      .filter(item => item.topic_id === topic?.id && !HIDDEN_STATUSES.has(item.status))
      .sort(sortRecent);
    const selectedIteration = iterations.find(item => item.id === state.selectedIteration)
      || iterations.find(item => ["active", "validating", "running"].includes(item.status))
      || iterations[0]
      || null;
    const workItems = (state.workItems || [])
      .filter(item => item.topic_id === topic?.id && !HIDDEN_STATUSES.has(item.status))
      .sort(sortRecent);
    const sessions = uniqueBy([...(state.codexSessions || [])].sort(sortRecent), item => item.thread_id);
    const repositories = (state.repositories || []).filter(item => !HIDDEN_STATUSES.has(item.status));
    const experiments = (state.experiments || []).filter(item => !HIDDEN_STATUSES.has(item.status)).sort(sortRecent);
    const runs = (state.runs || []).filter(item => !HIDDEN_STATUSES.has(item.status)).sort(sortRecent);
    const documents = (state.documents || []).filter(item => !HIDDEN_STATUSES.has(item.status)).sort(sortRecent);
    const claims = (state.claims || []).filter(item => !HIDDEN_STATUSES.has(item.status)).sort(sortRecent);
    const relations = state.relations || [];
    const executions = state.codexExecutions || [];
    const binding = state.bindingStats || {};
    const latestSession = sessions[0] || null;
    const latestSessionEvidence = sessionEvidence(ui.sessionDetail, latestSession);

    const nodes = [];
    const edges = [];
    const nodeById = new Map();
    const aliasToNode = new Map();
    const prefixAliases = [];
    const edgeKeys = new Set();

    function position(input, x, y) {
      const saved = ui.positions.get(input.id);
      return {...input, x: saved?.x ?? x, y: saved?.y ?? y};
    }

    function add(input, x, y, aliases = []) {
      if (!input?.id) return null;
      if (nodeById.has(input.id)) return input.id;
      const item = position(input, x, y);
      nodes.push(item);
      nodeById.set(item.id, item);
      [item.id, ...(aliases || [])].filter(Boolean).forEach(alias => aliasToNode.set(alias, item.id));
      return item.id;
    }

    function addPrefix(prefix, nodeId) {
      if (prefix && nodeId) prefixAliases.push([prefix, nodeId]);
    }

    function connect(source, target, label, options = {}) {
      if (!source || !target || source === target) return;
      const key = `${source}|${options.predicate || label}|${target}`;
      if (edgeKeys.has(key)) return;
      edgeKeys.add(key);
      edges.push({
        source,
        target,
        label,
        predicate: options.predicate || label,
        state: options.state || "solid",
        tone: options.tone || "neutral",
        sourcePort: options.sourcePort,
        targetPort: options.targetPort,
      });
    }

    const focusId = add({
      id: topic?.id || project.id || "project:current",
      domain: "research",
      variant: "focus",
      focus: true,
      highlight: true,
      kind: topic ? "假设" : "科研项目",
      badge: topic ? "H1" : null,
      code: topic?.display_key || "PROJECT",
      title: topic?.title || project.name || "当前科研项目",
      meta: topic
        ? `创建：${time(topic.created_at)} · ${topic.owner || project.owner || "研究人员"}`
        : `${number(stats.topics)} 个研究主题`,
      detail: topic?.problem_statement || topic?.objective || project.description || "当前科研项目上下文。",
      status: topic?.status || project.status || "active",
      source: topic || project,
      action: topic ? "edit-topic" : "create-topic",
      evidence: `${iterations.reduce((sum, item) => sum + Number(item.evidence_count || 0), 0)} 条迭代证据`,
    }, 254, 110);

    const repositoryClusterId = add({
      id: "cluster:repositories",
      domain: "code",
      variant: "cluster",
      cluster: true,
      kind: "历史代码",
      title: `${number(repositories.length)} 个仓库版本`,
      meta: `${number(repositories.reduce((sum, item) => sum + Number(item.stats?.commits || 0), 0))} commits`,
      detail: "展开查看当前项目实际接入的仓库与版本。",
      status: repositories.length && repositories.every(item => item.status === "ready") ? "ready" : repositories.length ? "indexing" : "planned",
      icon: "ph-git-branch",
      count: repositories.length,
      expandKey: "repositories",
      route: "repository",
      evidence: `${number(repositories.reduce((sum, item) => sum + Number(item.stats?.files || 0), 0))} 个版本化文件`,
    }, 46, 58);

    const documentClusterId = add({
      id: "cluster:documents",
      domain: "document",
      variant: "cluster",
      cluster: true,
      kind: "相关文献",
      title: `${number(documents.length)} 篇科研文档`,
      meta: `${number(claims.length)} 条主张`,
      detail: "展开查看项目接入的论文、方案、记录与主张。",
      status: documents.length ? "ready" : "planned",
      icon: "ph-files",
      count: documents.length,
      expandKey: "documents",
      route: "documents",
      evidence: `${number(documents.length)} 个文档来源`,
    }, 46, 116);
    connect(repositoryClusterId, focusId, "版本依据", {state: "pending", sourcePort: "right", targetPort: "left"});
    connect(documentClusterId, focusId, "相关研究", {state: "pending", sourcePort: "right", targetPort: "left"});

    if (!documents.length) {
      const primaryDocumentId = add({
        id: "gap:primary-document",
        domain: "document",
        variant: "paper",
        kind: "文献",
        code: "待导入",
        title: "关联支持当前问题的主要文献",
        meta: "尚无科研文档",
        detail: "由真实数据缺口生成；导入文档后会由实际文档替代。",
        status: "planned",
        icon: "ph-book-open-text",
        route: "documents",
        evidence: "等待导入可追溯文档",
      }, 315, 18);
      connect(primaryDocumentId, focusId, "待支持", {
        tone: "blue",
        state: "pending",
        sourcePort: "bottom",
        targetPort: "top",
      });
    }

    iterations.forEach((item, index) => {
      const id = add({
        id: item.id,
        domain: "research",
        variant: "iteration",
        kind: index === 0 ? "当前迭代" : "研究迭代",
        code: item.display_key,
        title: item.title,
        meta: `${helpers.statusLabel(item.status)} · ${Number(item.progress || 0)}%`,
        detail: item.goal || item.hypothesis || "本轮研究目标尚未补充。",
        status: item.status,
        icon: "ph-git-commit",
        source: item,
        action: "edit-iteration",
        evidence: `${item.evidence_count || 0} 条迭代证据`,
      }, 664, 112 + index * 82);
      connect(focusId, id, index === 0 ? "推进" : "包含迭代", {
        state: index === 0 ? "pending" : "solid",
        sourcePort: "right",
        targetPort: "left",
      });
    });

    let plannedIterationId = null;
    if (!iterations.length) {
      plannedIterationId = add({
        id: `gap:iteration:${topic?.id || project.id || "current"}`,
        domain: "research",
        variant: "iteration",
        kind: "研究迭代",
        code: "I00",
        title: "建立首个验证迭代",
        meta: "当前研究尚未分轮验证",
        detail: "由真实数据缺口生成；创建迭代后会自动替换为实际研究轮次。",
        status: "planned",
        icon: "ph-git-commit",
        action: "create-iteration",
        evidence: "等待定义验证目标",
      }, 664, 112);
      connect(focusId, plannedIterationId, "下一步", {
        state: "pending",
        sourcePort: "right",
        targetPort: "left",
      });
    }

    workItems.forEach((item, index) => {
      const column = index % 2;
      const row = Math.floor(index / 2);
      const id = add({
        id: item.id,
        domain: item.kind === "experiment" ? "experiment" : "research",
        variant: "task",
        kind: item.kind === "experiment" ? "实验任务" : item.kind === "analysis" ? "分析任务" : "开发任务",
        code: item.display_key,
        title: item.title,
        meta: `${item.assignee_type === "codex" ? "Codex" : item.owner || "待分配"} · ${time(item.updated_at)}`,
        detail: item.objective || "尚未补充任务目标。",
        status: item.status,
        icon: item.kind === "experiment" ? "ph-flask" : "ph-clipboard-text",
        source: item,
        action: "edit-work",
        route: item.assignee_type === "codex" ? "codex-bridge" : null,
        evidence: item.acceptance_criteria || "等待执行结果",
      }, 276 + column * 264, 212 + row * 84);
      connect(item.iteration_id && nodeById.has(item.iteration_id) ? item.iteration_id : focusId, id, "需要实现", {
        sourcePort: "bottom",
        targetPort: "top",
      });
    });

    let plannedWorkId = null;
    if (!workItems.length) {
      plannedWorkId = add({
        id: `gap:work:${topic?.id || project.id || "current"}`,
        domain: "research",
        variant: "task",
        kind: "开发任务",
        code: "待定义",
        title: "定义需要 Codex 执行的开发或分析任务",
        meta: "项目与研究上下文将自动带入",
        detail: "由真实数据缺口生成，不要求填写内部 ID、进度或审计字段。",
        status: "planned",
        icon: "ph-clipboard-text",
        route: "codex-bridge",
        evidence: "等待任务目标与验收条件",
      }, 276, 212);
      connect(plannedIterationId || selectedIteration?.id || focusId, plannedWorkId, "需要实现", {
        state: "pending",
        sourcePort: "bottom",
        targetPort: "top",
      });
    }

    const developmentRows = Math.max(1, sessions.length, workItems.length);
    const sessionChangeIds = new Map();
    sessions.forEach((item, index) => {
      const sessionTitle = cleanSessionTitle(item.title, `Codex 研发会话 ${index + 1}`);
      const threadUri = `codex://thread/${item.thread_id}`;
      const sessionId = add({
        id: threadUri,
        domain: "codex",
        variant: "session",
        kind: index === 0 ? "Codex 会话" : "历史会话",
        code: item.thread_key ? `#${item.thread_key}` : `#${index + 1}`,
        title: sessionTitle,
        meta: `${time(item.updated_at)} · ${number(item.turn_count)} 轮`,
        detail: `工作目录：${item.cwd || "未记录"}`,
        status: item.status,
        icon: "ph-chats-circle",
        source: item,
        route: "codex",
        evidence: `${number(item.command_count)} 次命令 · ${number(item.file_change_count)} 项文件变更`,
      }, -8, 304 + index * 84, [item.thread_id]);
      addPrefix(`${threadUri}/`, sessionId);

      const changeId = add({
        id: `changes:${item.thread_id}`,
        domain: "code",
        variant: "code-main",
        highlight: index === 0,
        kind: "代码变更",
        code: item.file_change_count ? `${number(item.file_change_count)} FILES` : "CHANGESET",
        title: sessionTitle,
        meta: `Codex · ${time(item.updated_at)}`,
        detail: "由会话结构化事件汇总的代码变更；提交和验证状态独立核对。",
        status: item.file_change_count ? item.status : "planned",
        icon: "ph-git-branch",
        source: item,
        route: "codex",
        evidence: `${number(item.item_count)} 个结构化会话事件`,
      }, 280, 304 + index * 84);
      sessionChangeIds.set(item.thread_id, changeId);
      connect(sessionId, changeId, "产生", {tone: "purple", sourcePort: "right", targetPort: "left"});
    });

    if (!sessions.length) {
      const plannedSessionId = add({
        id: "gap:codex-session",
        domain: "codex",
        variant: "session",
        kind: "Codex 会话",
        code: "#—",
        title: "等待执行研究任务",
        meta: "尚无已绑定研发会话",
        detail: "从任务交给 Codex 后，真实 Thread、命令与文件变更会自动进入关系网。",
        status: "planned",
        icon: "ph-chats-circle",
        route: "codex-bridge",
        evidence: "等待结构化执行证据",
      }, -8, 304);
      const plannedChangeId = add({
        id: "gap:code-change",
        domain: "code",
        variant: "code-main",
        kind: "代码",
        code: "CHANGESET",
        title: "等待代码实现",
        meta: "执行后自动汇总",
        detail: "这里只展示由实际会话解析得到的文件变更，不生成虚构文件名。",
        status: "planned",
        icon: "ph-git-branch",
        route: "codex-bridge",
        evidence: "等待代码与验证结果",
      }, 280, 304);
      connect(plannedWorkId || workItems[0]?.id || focusId, plannedSessionId, "交给 Codex", {
        tone: "purple",
        state: "pending",
        sourcePort: "bottom",
        targetPort: "top",
      });
      connect(plannedSessionId, plannedChangeId, "将产生", {
        tone: "purple",
        state: "pending",
        sourcePort: "right",
        targetPort: "left",
      });
    }

    executions.forEach(execution => {
      const workId = execution.work_item_id;
      const threadId = execution.thread_id || execution.codex_thread_id;
      const target = threadId ? aliasToNode.get(`codex://thread/${threadId}`) : null;
      if (nodeById.has(workId) && target) connect(workId, target, "交给 Codex", {
        tone: "blue",
        sourcePort: "bottom",
        targetPort: "top",
      });
    });
    if (!executions.length && workItems.length === 1 && sessions.length === 1 && workItems[0].assignee_type === "codex") {
      connect(workItems[0].id, `codex://thread/${sessions[0].thread_id}`, "交给 Codex", {
        tone: "blue",
        state: "pending",
        sourcePort: "bottom",
        targetPort: "top",
      });
    }

    repositories.forEach((item, index) => {
      const aliases = repositoryAliases(item);
      const id = add({
        id: item.id,
        domain: "code",
        variant: "file",
        kind: "代码仓库",
        title: item.name,
        meta: `${item.default_branch || "HEAD"} · ${shortCommit(item.head_commit)}`,
        detail: item.source_url || item.local_path || "代码仓库",
        status: item.status,
        icon: "ph-git-repository",
        source: item,
        route: "repository",
        evidence: `${number(item.stats?.files)} 文件 · ${number(item.stats?.symbols)} Symbol`,
        contextual: !ui.expanded.has("repositories"),
        contextKey: "repositories",
      }, 44, 188 + index * 48, aliases);
      connect(repositoryClusterId, id, "包含", {sourcePort: "bottom", targetPort: "top"});
    });

    const autoFileUris = new Set();
    let genericIndex = 0;
    relations.forEach(relation => {
      if (relation.predicate === "changed_path_maps_to" && String(relation.target_entity_id).startsWith("code://")) {
        autoFileUris.add(relation.target_entity_id);
      }
    });
    latestSessionEvidence.paths.forEach(path => autoFileUris.add(`local-code://${path}`));
    const fileIds = [];
    [...autoFileUris].forEach((uri, index) => {
      const parsed = parseEntityUri(uri);
      const id = add({
        id: uri,
        domain: "code",
        variant: "file",
        kind: "代码文件",
        title: parsed.title,
        meta: parsed.context,
        detail: parsed.detail,
        status: "ready",
        icon: "ph-code",
        source: {path: parsed.path, entity_id: uri},
        route: "repository",
        evidence: uri.startsWith("local-code://") ? "来自当前 Codex 会话的文件变更" : "来自已确认或待复核的平台关系",
      }, 634 + Math.floor(index / 8) * 184, 286 + (index % 8) * 42);
      fileIds.push(id);
    });

    experiments.forEach((item, index) => {
      const id = add({
        id: item.id,
        domain: "experiment",
        variant: "experiment",
        kind: index === 0 ? "实验（当前）" : "实验",
        code: item.display_key || `EXP-${index + 1}`,
        title: item.title || item.name,
        meta: `${number(item.run_count)} Run · ${time(item.updated_at)}`,
        detail: item.objective || item.hypothesis || item.description || "实验已进入当前项目。",
        status: item.status,
        icon: "ph-flask",
        source: item,
        route: "experiments",
        evidence: `${number(item.run_count)} 个实验运行`,
      }, -8 + (index % 3) * 260, validationY(developmentRows) + Math.floor(index / 3) * 84);
      if (item.iteration_id && nodeById.has(item.iteration_id)) {
        connect(item.iteration_id, id, "验证", {tone: "green", sourcePort: "bottom", targetPort: "top"});
      }
    });

    let plannedExperimentId = null;
    if (!experiments.length) {
      plannedExperimentId = add({
        id: "gap:experiment",
        domain: "experiment",
        variant: "experiment",
        kind: "实验",
        code: "待建立",
        title: "添加验证实验与结果数据",
        meta: "尚无实验记录",
        detail: "由真实数据缺口生成；实验接入后会显示实际 Run、Metric 与工件。",
        status: "planned",
        icon: "ph-flask",
        route: "experiments",
        evidence: "等待实验计划与运行结果",
      }, 84, validationY(developmentRows));
      connect(selectedIteration?.id || plannedIterationId || focusId, plannedExperimentId, "待验证", {
        tone: "green",
        state: "pending",
        sourcePort: "bottom",
        targetPort: "top",
      });
    }

    runs.forEach((item, index) => {
      const experimentIndex = Math.max(0, experiments.findIndex(experiment => experiment.id === item.experiment_id));
      const id = add({
        id: item.id,
        domain: "experiment",
        variant: "metric",
        kind: "实验 Run",
        code: item.display_key || `RUN-${index + 1}`,
        title: item.name || item.title || `${item.status || "实验"} Run`,
        meta: `${helpers.statusLabel(item.status)} · ${time(item.updated_at || item.completed_at)}`,
        detail: item.summary || item.note || "版本化实验运行。",
        status: item.status,
        icon: "ph-chart-line-up",
        source: item,
        route: "experiments",
        evidence: item.commit_entity_id ? "已绑定代码版本" : "尚未绑定代码版本",
      }, 508 + (index % 3) * 170, validationY(developmentRows) + experimentIndex * 84 + Math.floor(index / 3) * 74, runAliases(item));
      if (nodeById.has(item.experiment_id)) connect(item.experiment_id, id, "产生", {
        tone: "green",
        sourcePort: "right",
        targetPort: "left",
      });
      (item.metrics || []).forEach((metric, metricIndex) => {
        const metricId = metric.id || `${item.id}:metric:${metric.name || metricIndex}`;
        add({
          id: metricId,
          domain: "experiment",
          variant: "file",
          kind: "指标",
          code: metric.name,
          title: `${metric.name || "Metric"} ${metric.value ?? ""}`.trim(),
          meta: metric.unit || "实验指标",
          detail: `来自 ${item.display_key || "实验 Run"}`,
          status: "ready",
          icon: "ph-chart-line",
          source: metric,
          route: "experiments",
          evidence: "来自版本化实验运行",
        }, 690 + metricIndex * 176, validationY(developmentRows) + index * 46);
        connect(id, metricId, "报告", {tone: "green", sourcePort: "right", targetPort: "left"});
      });
    });

    if (!runs.length) {
      const plannedMetricId = add({
        id: "gap:metric",
        domain: "experiment",
        variant: "metric",
        kind: "指标",
        code: "METRIC",
        title: "定义实验成功判据",
        meta: "尚无版本化指标",
        detail: "指标会从真实实验 Run 中读取，不把手工占位数值当作研究结果。",
        status: "planned",
        icon: "ph-chart-line-up",
        route: "experiments",
        evidence: "等待实验运行报告",
      }, 506, validationY(developmentRows));
      connect(plannedExperimentId || experiments[0]?.id || focusId, plannedMetricId, "将报告", {
        tone: "green",
        state: "pending",
        sourcePort: "right",
        targetPort: "left",
      });
    }

    const evidenceBaseY = validationY(developmentRows)
      + Math.max(1, Math.ceil(Math.max(experiments.length, runs.length) / 3)) * 92
      + 28;
    documents.forEach((item, index) => {
      const id = add({
        id: item.id,
        domain: "document",
        variant: "document",
        kind: index === 0 ? "文档" : "科研文档",
        code: item.display_key,
        title: item.title || item.name,
        meta: `${number(item.claim_count)} 条主张 · ${time(item.updated_at)}`,
        detail: item.description || item.abstract || "科研文档已进入当前项目。",
        status: item.status || "ready",
        icon: "ph-file-text",
        source: item,
        route: "documents",
        evidence: `${number(item.verified_claims)} 条已验证主张`,
      }, -8, evidenceBaseY + index * 84);
      connect(documentClusterId, id, "包含", {sourcePort: "bottom", targetPort: "top"});
    });

    claims.forEach((item, index) => {
      const id = add({
        id: item.id,
        domain: "document",
        variant: "claim",
        kind: "Claim",
        code: item.display_key,
        title: item.title || item.claim || item.text || item.content || "科研主张",
        meta: `${helpers.statusLabel(item.status)} · ${time(item.updated_at)}`,
        detail: item.claim || item.text || item.content || item.rationale || "等待证据复核。",
        status: item.status || "review",
        icon: "ph-note",
        source: item,
        route: "documents",
        evidence: item.evidence_count ? `${item.evidence_count} 条关联证据` : "等待可接受证据",
      }, 251 + (index % 2) * 280, evidenceBaseY + Math.floor(index / 2) * 84);
      if (nodeById.has(item.document_id)) connect(item.document_id, id, "包含", {
        sourcePort: "right",
        targetPort: "left",
      });
    });

    let plannedClaimId = null;
    if (!claims.length) {
      plannedClaimId = add({
        id: "gap:claim",
        domain: "document",
        variant: "claim",
        kind: "Claim",
        code: "待形成",
        title: "从代码、实验与文档证据形成可复核主张",
        meta: "尚无科研主张",
        detail: "主张必须由实际来源支持；系统只提示缺口，不预填研究结论。",
        status: "planned",
        icon: "ph-note",
        route: "documents",
        evidence: "等待关联可接受证据",
      }, 260, evidenceBaseY);
      connect(plannedExperimentId || experiments[0]?.id || focusId, plannedClaimId, "待支持", {
        tone: "green",
        state: "pending",
        sourcePort: "bottom",
        targetPort: "top",
      });
    }

    let reviewId = null;
    if (Number(binding.total || 0) || Number(binding.pending || 0)) {
      reviewId = add({
        id: "review:bindings",
        domain: "review",
        variant: "review",
        kind: "复核任务",
        code: "REVIEW",
        title: `${number(binding.pending || 0)} 条关系等待确认`,
        meta: `${number(binding.confirmed || 0)} 已确认 · ${number(binding.total || 0)} 候选`,
        detail: "待复核关系不会自动成为结论证据。",
        status: Number(binding.pending || 0) ? "review" : "ready",
        icon: "ph-shield-check",
        source: binding,
        route: "bindings",
        evidence: `${number(binding.pending || 0)} 条待人工复核关系`,
      }, 663, evidenceBaseY);
    } else {
      reviewId = add({
        id: "gap:review",
        domain: "review",
        variant: "review",
        kind: "复核任务",
        code: "待建立",
        title: "等待跨来源关系进入人工复核",
        meta: "尚无关系候选",
        detail: "系统生成关系候选后仍需人工确认，未经复核不会自动成为结论证据。",
        status: "planned",
        icon: "ph-shield-check",
        route: "bindings",
        evidence: "等待多源关系候选",
      }, 663, evidenceBaseY);
      connect(plannedClaimId || claims[0]?.id || focusId, reviewId, "待复核", {
        tone: "red",
        state: "pending",
        sourcePort: "right",
        targetPort: "left",
      });
    }

    iterations.filter(item => item.summary).forEach((item, index) => {
      const id = add({
        id: `${item.id}:conclusion`,
        domain: "research",
        variant: "conclusion",
        kind: "结论",
        code: item.display_key,
        title: item.summary,
        meta: `${helpers.statusLabel(item.status)} · ${time(item.updated_at)}`,
        detail: item.summary,
        status: item.status,
        icon: "ph-file-check",
        source: item,
        action: "edit-iteration",
        evidence: `${item.evidence_count || 0} 条迭代证据`,
      }, 243 + index * 230, evidenceBaseY + Math.max(1, Math.ceil(claims.length / 2)) * 90);
      connect(item.id, id, "形成结论", {tone: "green", sourcePort: "bottom", targetPort: "top"});
    });

    if (!iterations.some(item => item.summary)) {
      const conclusionId = add({
        id: "gap:conclusion",
        domain: "research",
        variant: "conclusion",
        kind: "结论草案",
        code: "DRAFT",
        title: "等待复核通过后形成研究结论",
        meta: "当前没有已支持的结论",
        detail: "结论由研究人员确认，Codex 与系统不能替代最终科研判断。",
        status: "planned",
        icon: "ph-file-check",
        evidence: "等待代码、实验、文档与关系复核",
      }, 260, evidenceBaseY + Math.max(1, Math.ceil(Math.max(1, claims.length) / 2)) * 90);
      connect(reviewId || plannedClaimId || focusId, conclusionId, "复核后支持", {
        tone: "green",
        state: "pending",
        sourcePort: "bottom",
        targetPort: "top",
      });
    }

    relations.forEach(relation => {
      let source = resolveNode(relation.source_entity_id);
      let target = resolveNode(relation.target_entity_id);
      const shouldMaterialize = ui.expanded.has("all-relations")
        || relation.predicate !== "parent"
        || source
        || target;
      if (!shouldMaterialize) return;
      if (!source && (target || ui.expanded.has("all-relations"))) {
        source = materializeEntity(relation.source_entity_id);
      }
      if (!target && (source || ui.expanded.has("all-relations"))) {
        target = materializeEntity(relation.target_entity_id);
      }
      if (!source || !target) return;
      connect(source, target, PREDICATE_LABELS[relation.predicate] || humanizePredicate(relation.predicate), {
        predicate: relation.predicate,
        tone: relationTone(relation),
        state: relationState(relation),
      });
    });

    if (reviewId) {
      const pendingSources = relations
        .filter(item => item.review_status === "unreviewed")
        .map(item => resolveNode(item.source_entity_id))
        .filter(Boolean);
      [...new Set(pendingSources)].slice(0, 8).forEach(source => connect(source, reviewId, "待复核", {
        tone: "red",
        state: "pending",
      }));
    }

    function resolveNode(entityId) {
      if (!entityId) return null;
      if (aliasToNode.has(entityId)) return aliasToNode.get(entityId);
      const prefixMatch = prefixAliases
        .filter(([prefix]) => String(entityId).startsWith(prefix))
        .sort((a, b) => b[0].length - a[0].length)[0];
      return prefixMatch?.[1] || null;
    }

    function materializeEntity(entityId) {
      const existing = resolveNode(entityId);
      if (existing) return existing;
      const parsed = parseEntityUri(entityId);
      const index = genericIndex++;
      const domain = parsed.domain;
      const id = add({
        id: entityId,
        domain,
        variant: parsed.variant,
        kind: parsed.kind,
        code: parsed.code,
        title: parsed.title,
        meta: parsed.context,
        detail: parsed.detail,
        status: "ready",
        icon: parsed.icon,
        source: {entity_id: entityId, path: parsed.path},
        route: parsed.route,
        evidence: "来自平台真实关系端点",
      }, 920 + Math.floor(index / 10) * 190, 80 + (index % 10) * 48);
      return id;
    }

    const primaryChangeId = latestSession ? sessionChangeIds.get(latestSession.thread_id) : null;
    const defaultSelectedId = primaryChangeId || workItems[0]?.id || selectedIteration?.id || focusId;
    const bounds = modelBounds(nodes);
    return {
      nodes,
      edges,
      focusId,
      defaultSelectedId,
      topic,
      iteration: selectedIteration,
      project,
      work: workItems[0] || null,
      session: latestSession,
      worldWidth: Math.max(930, bounds.maxX + 260),
      worldHeight: Math.max(740, bounds.maxY + 130),
    };
  }

  function sortRecent(a, b) {
    return new Date(b.updated_at || b.created_at || 0) - new Date(a.updated_at || a.created_at || 0);
  }

  function uniqueBy(items, key) {
    const seen = new Set();
    return items.filter(item => {
      const value = key(item);
      if (!value || seen.has(value)) return false;
      seen.add(value);
      return true;
    });
  }

  function validationY(developmentRows) {
    return 417 + Math.max(0, developmentRows - 1) * 84;
  }

  function repositoryAliases(repository) {
    const aliases = [repository.id];
    const raw = String(repository.id || "").replace(/^repo:\/\//, "");
    if (raw) {
      aliases.push(`code://${raw}@`);
      aliases.push(`git://${raw}/`);
    }
    return aliases;
  }

  function runAliases(run) {
    return [run.id, run.entity_id, run.commit_entity_id].filter(Boolean);
  }

  function parseEntityUri(uri) {
    const value = decodeURIComponent(String(uri || ""));
    const withoutQuery = value.split("?")[0];
    const fragments = withoutQuery.split("/").filter(Boolean);
    const last = fragments.at(-1) || value;
    const isCode = value.startsWith("code://") || value.startsWith("git://") || value.startsWith("local-code://");
    const isCodex = value.startsWith("codex://");
    const isExperiment = /^(experiment|run|metric):\/\//.test(value);
    const isDocument = /^(document|claim|table|citation|figure):\/\//.test(value);
    const isResearch = /^(topic|iteration|work-item):\/\//.test(value);
    const domain = isCode ? "code" : isCodex ? "codex" : isExperiment ? "experiment" : isDocument ? "document" : isResearch ? "research" : "review";
    const looksLikeFile = isCode && (last.includes(".") || value.startsWith("local-code://"));
    const short = last.length > 54 ? `${last.slice(0, 51)}…` : last;
    const context = fragments.slice(-3, -1).join("/") || domain;
    return {
      domain,
      variant: looksLikeFile ? "file" : domain === "experiment" ? "metric" : "file",
      kind: looksLikeFile ? "代码文件" : domain === "codex" ? "会话事件" : domain === "experiment" ? "实验实体" : domain === "document" ? "文档实体" : domain === "research" ? "研究实体" : "关联实体",
      code: domain.toUpperCase(),
      title: short,
      context,
      detail: value,
      path: looksLikeFile ? last : null,
      icon: looksLikeFile ? "ph-code" : domain === "codex" ? "ph-terminal-window" : domain === "experiment" ? "ph-flask" : domain === "document" ? "ph-file-text" : domain === "research" ? "ph-lightbulb" : "ph-circles-three-plus",
      route: domain === "code" ? "repository" : domain === "codex" ? "codex" : domain === "experiment" ? "experiments" : domain === "document" ? "documents" : null,
    };
  }

  function relationTone(relation) {
    if (["contradicts", "failed", "blocks"].includes(relation.predicate)) return "red";
    if (["validated_by", "supported_by", "reports", "aggregates"].includes(relation.predicate)) return "green";
    if (relation.predicate === "changed_path_maps_to") return "blue";
    if (String(relation.source_entity_id).startsWith("codex://")) return "purple";
    return "neutral";
  }

  function relationState(relation) {
    if (["rejected", "contradicted"].includes(relation.review_status) || relation.predicate === "contradicts") return "conflict";
    if (["unreviewed", "pending"].includes(relation.review_status)) return "pending";
    return "solid";
  }

  function humanizePredicate(predicate) {
    return String(predicate || "关联").replaceAll("_", " ");
  }

  function cleanSessionTitle(value, fallback) {
    const title = String(value || "").replace(/\s+/g, " ").trim();
    if (!title) return fallback;
    const noise = [
      /^the following is the codex agent history\b/i,
      /^treat the transcript\b/i,
      /^you are (an?|the) /i,
      /^<recommended_plugins>/i,
      /^<environment_context>/i,
      /^<in-app-browser-context/i,
    ];
    if (noise.some(pattern => pattern.test(title))) return fallback;
    return title.length > 72 ? `${title.slice(0, 69)}…` : title;
  }

  function modelBounds(nodes) {
    return nodes.reduce((result, node) => ({
      maxX: Math.max(result.maxX, node.x + 280),
      maxY: Math.max(result.maxY, node.y + 90),
    }), {maxX: 0, maxY: 0});
  }

  window.ProjectNetworkModel = {build};
})();
