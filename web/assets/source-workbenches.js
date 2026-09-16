function renderStats() {
  const values = [state.stats.repositories, state.stats.files, state.stats.symbols, state.stats.edges];
  $$("#metric-grid .metric strong").forEach((node, index) => node.textContent = formatNumber(values[index]));
}

async function refreshSources() {
  try {
    const [rawStats, rawObjects, experimentSources, notebookRuns] = await Promise.all([
      api("/v1/ingestion/raw/stats"),
      api("/v1/ingestion/raw?limit=100"),
      api("/v1/experiments/sources"),
      api("/v1/notebooks/runs"),
    ]);
    Object.assign(state, {rawStats, rawObjects, experimentSources, notebookRuns});
    renderSources();
    renderExperimentIngestion();
  } catch (error) {
    showToast(`证据源加载失败：${error.message}`);
  }
}

function renderSources() {
  const stats = state.rawStats || {};
  const values = [
    stats.objects,
    stats.active,
    Number(stats.quarantined || 0) + Number(stats.tombstoned || 0),
    stats.events,
  ];
  $$("#raw-metric-grid .metric strong").forEach((node, index) => node.textContent = formatNumber(values[index]));
  $("#raw-object-table").innerHTML = state.rawObjects.length
    ? state.rawObjects.map(item => `<tr><td><span class="raw-source-type">${escapeHtml(item.source_type)}</span></td><td class="raw-object-id"><strong title="${escapeHtml(item.source_object_id)}">${escapeHtml(item.source_object_id)}</strong><small>${escapeHtml(item.source_version)} · ${escapeHtml(item.content_hash)}</small></td><td><span class="status-pill ${statusClass(item.state)}">${escapeHtml(item.state)}</span></td><td>${formatNumber(item.byte_length)} B</td><td><span class="mono-label">${escapeHtml(item.acl_ref)}</span></td><td>${formatTime(item.observed_at)}</td></tr>`).join("")
    : '<tr><td colspan="6" class="empty-row">尚无原始对象</td></tr>';

  const sourceTypes = Object.entries(stats.by_type || {}).sort((left, right) => right[1] - left[1]);
  $("#source-type-list").innerHTML = sourceTypes.length
    ? sourceTypes.map(([type, count]) => `<div class="source-type-item"><span class="raw-source-type">${escapeHtml(type)}</span><div><strong>${formatNumber(count)}</strong><small>RAW OBJECTS</small></div></div>`).join("")
    : '<div class="empty-state compact"><p>尚无来源数据</p></div>';
  const options = state.repositories.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(shortCommit(item.head_commit))}</option>`).join("");
  $("#mlflow-repository").innerHTML = `<option value="">按 Git URL 自动匹配</option>${options}`;
}

function renderExperimentIngestion() {
  const experimentRaw = state.rawObjects.filter(item => ["mlflow", "notebook"].includes(item.source_type));
  const values = [
    state.experimentSources.length,
    state.experimentStats.runs,
    state.notebookRuns.length,
    experimentRaw.length,
  ];
  $$("#ingestion-metric-grid .metric strong").forEach((node, index) => node.textContent = formatNumber(values[index]));

  const adapters = $("#adapter-source-list");
  adapters.innerHTML = state.experimentSources.length
    ? state.experimentSources.map(item => `<button class="adapter-source" data-mlflow-source="${escapeHtml(item.tracking_uri)}"><div><strong>${escapeHtml(item.tracking_uri)}</strong><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span></div><small>${escapeHtml(item.adapter_type)} · ${(item.stats || {}).runs || 0} 次运行 · ${formatTime(item.last_synced_at || item.updated_at)}</small>${item.last_error ? `<em>${escapeHtml(item.last_error)}</em>` : ""}</button>`).join("")
    : '<div class="empty-state compact"><p>尚无 MLflow 数据源</p></div>';
  $$("[data-mlflow-source]", adapters).forEach(button => button.addEventListener("click", () => {
    $("#mlflow-form").elements.tracking_uri.value = button.dataset.mlflowSource;
    $("#mlflow-dialog").showModal();
  }));

  $("#notebook-count").textContent = `${state.notebookRuns.length} 次`;
  $("#notebook-list").innerHTML = state.notebookRuns.length
    ? state.notebookRuns.map(item => `<div class="notebook-run"><div><strong>${escapeHtml(item.template_name || item.source_uri || item.id)}</strong><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span></div><small>${escapeHtml(item.version)} · ${item.cell_count || 0} 个单元格 · ${formatTime(item.created_at)}</small></div>`).join("")
    : '<div class="empty-state compact"><p>尚未接入 Notebook</p></div>';
  $("#experiment-raw-count").textContent = `${experimentRaw.length} 条`;
  $("#experiment-raw-table").innerHTML = experimentRaw.length
    ? experimentRaw.map(item => `<tr><td><span class="raw-source-type">${escapeHtml(item.source_type)}</span></td><td class="raw-object-id"><strong title="${escapeHtml(item.source_object_id)}">${escapeHtml(item.source_object_id)}</strong><small>${escapeHtml(item.source_version)} · ${escapeHtml(item.content_hash)}</small></td><td><span class="status-pill ${statusClass(item.state)}">${escapeHtml(item.state)}</span></td><td>${formatNumber(item.derived_count)}</td><td>${formatTime(item.observed_at)}</td></tr>`).join("")
    : '<tr><td colspan="5" class="empty-row">尚无实验原始对象</td></tr>';
  if ($("#experiment-source-count")) $("#experiment-source-count").textContent = formatNumber(state.experimentSources.length);
  if ($("#experiment-notebook-count")) $("#experiment-notebook-count").textContent = formatNumber(state.notebookRuns.length);
  if ($("#experiment-evidence-count")) $("#experiment-evidence-count").textContent = formatNumber(experimentRaw.length);
  renderPlatformSourceHealth();
}

function renderPlatformSourceHealth() {
  const container = $("#platform-source-list");
  if (!container) return;
  const readyRepositories = state.repositories.filter(item => item.status === "ready").length;
  const failedRepositories = state.repositories.filter(item => item.status === "failed").length;
  const activeRaw = Number(state.rawStats?.active || 0);
  const sources = [
    {label:"代码索引", value:readyRepositories, meta:`${failedRepositories} 个仓库异常`, state:failedRepositories ? "indexing" : "ready"},
    {label:"Codex 会话", value:Number(state.codexStats?.threads || state.codexStats?.sessions || state.codexSessions.length), meta:`${formatNumber(state.codexStats?.turns)} 个轮次`, state:"ready"},
    {label:"实验来源", value:state.experimentSources.length + state.notebookRuns.length, meta:`${formatNumber(state.experimentStats?.runs)} 次运行`, state:"ready"},
    {label:"科研文档", value:state.documents.length, meta:`${formatNumber(state.claims.length)} 条结论`, state:"ready"},
    {label:"原始证据对象", value:activeRaw, meta:`${formatNumber(state.rawStats?.events)} 条来源事件`, state:"ready"},
  ];
  $("#platform-ops-summary").textContent = `${sources.slice(0, 4).reduce((sum, item) => sum + item.value, 0)} 个来源`;
  container.innerHTML = sources.map(item => `<div class="platform-source-item"><div><strong>${escapeHtml(item.label)}</strong><span class="status-pill ${statusClass(item.state)}">${statusLabel(item.state)}</span></div><b>${formatNumber(item.value)}</b><small>${escapeHtml(item.meta)}</small></div>`).join("");
}

async function loadGitHistory(repositoryId) {
  const table = $("#history-table");
  const count = $("#git-history-count");
  if (!repositoryId) {
    state.gitHistory = [];
    if (count) count.textContent = "0";
    table.innerHTML = '<tr><td colspan="5" class="empty-row">请选择仓库</td></tr>';
    return;
  }
  if (count) count.textContent = "…";
  table.innerHTML = '<tr><td colspan="5" class="empty-row">正在加载 Git 历史…</td></tr>';
  try {
    state.gitHistory = await api(`/v1/code/history?repository_id=${encodeURIComponent(repositoryId)}&limit=100`);
    if (count) count.textContent = formatNumber(state.gitHistory.length);
    table.innerHTML = state.gitHistory.length
      ? state.gitHistory.map(item => `<tr data-history-commit="${escapeHtml(item.sha)}"><td><span class="mono-label">${escapeHtml(shortCommit(item.sha))}</span></td><td class="repo-cell"><strong>${escapeHtml(item.message)}</strong><small>${escapeHtml(item.source_uri)}</small></td><td>${escapeHtml(item.author_name || "—")}</td><td>${escapeHtml((item.parent_shas || []).map(shortCommit).join(", ") || "ROOT")}</td><td><time>${formatTime(item.committed_at)}</time><div class="history-row-actions"><button type="button" data-browse-commit="${escapeHtml(item.sha)}">浏览</button><button type="button" data-compare-commit="${escapeHtml(item.sha)}">与当前比较</button></div></td></tr>`).join("")
      : '<tr><td colspan="5" class="empty-row">当前来源不是 Git 仓库或尚未同步历史</td></tr>';
    $$("[data-browse-commit]", table).forEach(button => button.addEventListener("click", event => {
      event.stopPropagation();
      state.selectedCodeRef = button.dataset.browseCommit;
      $("#code-version-ref").value = state.selectedCodeRef;
      loadFiles(repositoryId, state.selectedCodeRef);
      setGitHistoryOpen(false);
      document.querySelector("#view-repository .workbench-tab:nth-child(2)")?.click();
    }));
    $$("[data-compare-commit]", table).forEach(button => button.addEventListener("click", event => {
      event.stopPropagation();
      setGitHistoryOpen(false);
      openCodeCompare(button.dataset.compareCommit, "HEAD");
    }));
    await loadCodeRefs(repositoryId);
  } catch (error) {
    if (count) count.textContent = "!";
    table.innerHTML = `<tr><td colspan="5" class="empty-row">${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderGitHistoryLauncher(repository = null) {
  const branch = repository?.default_branch || "—";
  const commit = shortCommit(repository?.head_commit);
  $("#git-history-trigger-branch").textContent = repository ? branch : "选择仓库";
  $("#git-history-trigger-commit").textContent = repository ? commit : "查看版本历史";
  $("#git-history-branch").textContent = branch;
  $("#git-history-head").textContent = commit;
}

function setGitHistoryOpen(open) {
  const toggle = $("#git-history-toggle");
  const panel = $("#git-history-popover");
  if (!toggle || !panel) return;
  panel.hidden = !open;
  toggle.setAttribute("aria-expanded", String(open));
  toggle.classList.toggle("open", open);
  if (open) {
    const repositoryId = $("#history-repository").value || state.selectedRepository;
    const repository = state.repositories.find(item => item.id === repositoryId) || null;
    renderGitHistoryLauncher(repository);
    if (repositoryId && !state.gitHistory.length) loadGitHistory(repositoryId);
    requestAnimationFrame(() => $("#git-history-close").focus({preventScroll:true}));
  } else {
    toggle.focus({preventScroll:true});
  }
}

function selectGitHistoryRepository(repositoryId) {
  const repository = state.repositories.find(item => item.id === repositoryId) || null;
  renderGitHistoryLauncher(repository);
  loadGitHistory(repositoryId);
}

function installGitHistoryPopover() {
  const toggle = $("#git-history-toggle");
  const panel = $("#git-history-popover");
  if (!toggle || !panel || toggle.dataset.installed === "true") return;
  toggle.dataset.installed = "true";
  toggle.addEventListener("click", () => setGitHistoryOpen(panel.hidden));
  $("#git-history-close").addEventListener("click", () => setGitHistoryOpen(false));
  document.addEventListener("click", event => {
    if (
      !panel.hidden &&
      !event.target.closest(".git-history-flyout") &&
      !event.target.closest("#git-history-popover")
    ) {
      setGitHistoryOpen(false);
    }
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !panel.hidden) {
      event.preventDefault();
      setGitHistoryOpen(false);
    }
  });
}

async function submitMlflow(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const errorBox = $("#mlflow-error");
  errorBox.hidden = true;
  try {
    const result = await api("/v1/experiments/sources/mlflow/sync", {
      method: "POST",
      body: JSON.stringify({
        tracking_uri: String(form.get("tracking_uri") || ""),
        repository_id: form.get("repository_id") || null,
        experiment_ids: String(form.get("experiment_ids") || "").split(",").map(value => value.trim()).filter(Boolean),
        max_runs: Number(form.get("max_runs") || 500),
      }),
    });
    $("#mlflow-dialog").close();
    showToast(`MLflow 已同步 ${result.stats.runs} Runs / ${result.stats.metrics} Metrics`);
    await refreshAll();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

async function submitNotebook(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const errorBox = $("#notebook-error");
  errorBox.hidden = true;
  try {
    const result = await api("/v1/notebooks/ingest", {
      method: "POST",
      body: JSON.stringify({
        source: String(form.get("source") || ""),
        version: String(form.get("version") || "v1"),
        run_id: form.get("run_id") || null,
      }),
    });
    $("#notebook-dialog").close();
    showToast(`Notebook 已结构化：${result.cells.length} Cells`);
    await refreshSources();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

function renderRepositories() {
  const table = $("#repository-table");
  $("#repository-count").textContent = `${state.repositories.length} REPOSITORIES`;
  if (!state.repositories.length) {
    table.innerHTML = '<tr><td colspan="6" class="empty-row">尚未接入代码仓库</td></tr>';
    return;
  }
  table.innerHTML = state.repositories.map(repo => `
    <tr data-repo="${escapeHtml(repo.id)}" class="${repo.id === state.selectedRepository ? "selected" : ""}">
      <td class="repo-cell"><strong>${escapeHtml(repo.name)}</strong><small>${escapeHtml(repo.source_url || repo.id)}</small></td>
      <td>${escapeHtml(repo.source_type)}</td>
      <td><span class="status-pill ${statusClass(repo.status)}">${statusLabel(repo.status)}</span></td>
      <td><span class="mono-label">${escapeHtml(shortCommit(repo.head_commit))}</span></td>
      <td>${formatNumber((repo.stats || {}).symbols)}</td>
      <td>${formatTime(repo.updated_at)}</td>
    </tr>`).join("");
  $$("[data-repo]", table).forEach(row => row.addEventListener("click", () => selectRepository(row.dataset.repo)));
}

function selectRepository(id) {
  state.selectedRepository = id;
  state.selectedCodeRef = "HEAD";
  state.codeRefs = null;
  state.gitHistory = [];
  localStorage.setItem("rag_selected_repository", id);
  renderRepositories();
  const repo = state.repositories.find(item => item.id === id);
  if (!repo) return;
  $("#source-detail").innerHTML = `
    <div class="panel-head"><strong>${escapeHtml(repo.name)}</strong><span class="status-pill ${statusClass(repo.status)}">${statusLabel(repo.status)}</span></div>
    <div class="detail-body">
      <div class="detail-row"><span>Stable ID</span><b title="${escapeHtml(repo.id)}">${escapeHtml(repo.id)}</b></div>
      <div class="detail-row"><span>Branch</span><b>${escapeHtml(repo.default_branch || "—")}</b></div>
      <div class="detail-row"><span>Commit / Snapshot</span><b title="${escapeHtml(repo.head_commit)}">${escapeHtml(shortCommit(repo.head_commit))}</b></div>
      <div class="detail-row"><span>Files / Symbols</span><b>${formatNumber(repo.stats?.files)} / ${formatNumber(repo.stats?.symbols)}</b></div>
      <div class="detail-row"><span>Parse errors</span><b>${formatNumber(repo.stats?.parse_errors)}</b></div>
      <div class="detail-row"><span>ACL</span><b>${escapeHtml(repo.acl_ref)}</b></div>
      <div class="health-bar"><i style="width:${repo.status === "ready" ? 100 : 24}%"></i></div>
      ${repo.last_error ? `<div class="repository-error">${escapeHtml(repo.last_error)}</div>` : ""}
      <div class="detail-actions">
        <button class="button ghost compact-button" id="resync-repository">↻ 重新同步</button>
        <button class="button primary compact-button" id="browse-repository">浏览已发布代码</button>
      </div>
    </div>`;
  $("#browser-repository").value = id;
  $("#history-repository").value = id;
  renderGitHistoryLauncher(repo);
  $("#resync-repository").addEventListener("click", () => resyncRepository(repo));
  $("#browse-repository").addEventListener("click", () => {
    loadFiles(id);
    $(".section-divider").scrollIntoView({behavior: "smooth", block: "start"});
  });
  loadCodeRefs(id);
  loadFiles(id, "HEAD");
  loadGitHistory(id);
  if (typeof syncRagRepository === "function") syncRagRepository(id);
}

const workflowStages = [
  {key:"discover", label:"发现", icon:"magnifying-glass", count:"discovered"},
  {key:"capture", label:"读取", icon:"files", count:"files"},
  {key:"parse", label:"解析", icon:"brackets-curly", count:"files"},
  {key:"structure", label:"结构化", icon:"tree-structure", count:"symbols"},
  {key:"index", label:"建索引", icon:"database", count:"views"},
  {key:"publish", label:"发布", icon:"check-circle", count:"edges"},
];

function workflowStage(item) {
  return workflowStages.find(stage => stage.key === item?.stage) || workflowStages[0];
}

function workflowDisplayProgress(item) {
  const raw = Math.max(0, Math.min(100, Number(item?.progress) || 0));
  if (item?.status !== "failed") return raw;
  const index = Math.max(0, workflowStages.findIndex(stage => stage.key === workflowStage(item).key));
  return Math.max(4, Math.min(raw, Math.round(((index + .5) / workflowStages.length) * 100)));
}

function workflowErrorMessage(error) {
  const value = String(error || "").trim();
  if (!value) return "";
  if (value.includes("duplicate stable entity IDs")) return "检测到重复的实体标识，索引无法安全发布。";
  if (value.includes("Service restarted before the workflow completed")) return "服务在任务完成前重新启动，请重新运行此任务。";
  return value;
}

function renderWorkflows() {
  const list = $("#workflow-list");
  const filter = $("#workflow-status-filter")?.value || "";
  const workflows = [...state.workflows]
    .filter(item => !filter || item.status === filter)
    .sort((left, right) => {
      const rank = value => ["running", "queued"].includes(value) ? 0 : value === "failed" ? 1 : 2;
      return rank(left.status) - rank(right.status) || String(right.updated_at).localeCompare(String(left.updated_at));
    });
  $("#workflow-list-count").textContent = `${workflows.length} TASKS`;
  if (!workflows.length) {
    list.innerHTML = '<div class="empty-state compact"><p>暂无仓库索引任务</p></div>';
    return;
  }
  list.innerHTML = workflows.map(item => {
    const repository = state.repositories.find(value => value.id === item.repository_id);
    const stage = workflowStage(item);
    const progress = workflowDisplayProgress(item);
    const selected = state.selectedWorkflow === item.id;
    return `<article class="workflow-item ${item.status === "failed" ? "has-action" : ""} ${selected ? "selected" : ""}" data-workflow="${escapeHtml(item.id)}" role="button" tabindex="0" aria-pressed="${selected}">
      <div class="workflow-item-title"><strong>${escapeHtml(repository?.name || item.request?.source || "代码仓库")}</strong><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span></div>
      <div class="workflow-item-stage"><span><i class="ph ph-${stage.icon}"></i>${item.status === "completed" ? "已完成" : `当前 · ${stage.label}`}</span><time>${formatTime(item.updated_at)}</time></div>
      <div class="workflow-item-counts"><span>${formatNumber(item.counters?.files)} 文件</span><span>${formatNumber(item.counters?.symbols)} Symbol</span><span>${formatNumber(item.counters?.edges)} 关系</span></div>
      ${item.error ? `<p class="workflow-item-error" title="${escapeHtml(item.error)}">任务在${stage.label}阶段停止，打开查看原因</p>` : ""}
      <div class="workflow-progress-row"><div class="progress-mini" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress}"><i style="width:${progress}%"></i></div><b>${progress}%</b></div>
      ${item.status === "failed" ? `<button class="button ghost workflow-retry" data-retry-workflow="${escapeHtml(item.id)}"><i class="ph ph-arrow-clockwise"></i>重试</button>` : ""}
    </article>`;
  }).join("");
  $$("[data-workflow]", list).forEach(row => row.addEventListener("click", () => {
    state.selectedWorkflow = row.dataset.workflow;
    renderWorkflows();
    renderWorkflowTrack(state.workflows.find(item => item.id === row.dataset.workflow));
  }));
  $$("[data-workflow]", list).forEach(row => row.addEventListener("keydown", event => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    row.click();
  }));
  $$("[data-retry-workflow]", list).forEach(button => button.addEventListener("click", event => {
    event.stopPropagation();
    retryWorkflow(button.dataset.retryWorkflow);
  }));
}

function renderWorkflowTrack(workflow) {
  if (workflow) state.selectedWorkflow = workflow.id;
  $("#workflow-id").textContent = workflow ? workflow.id : "NO ACTIVE RUN";
  $("#workflow-detail-empty").hidden = Boolean(workflow);
  $("#workflow-detail-content").hidden = !workflow;
  $("#workflow-detail-title").textContent = workflow ? (state.repositories.find(item => item.id === workflow.repository_id)?.name || "任务进度") : "任务进度";
  $("#workflow-detail-summary").textContent = workflow ? "索引阶段、处理结果与失败原因" : "选择任务后查看当前阶段与处理结果";
  $("#workflow-detail-status").textContent = workflow ? statusLabel(workflow.status) : "未选择";
  $("#workflow-detail-status").className = `status-pill ${workflow ? statusClass(workflow.status) : "neutral"}`;
  if (!workflow) return;
  const stage = workflowStage(workflow);
  const progress = workflowDisplayProgress(workflow);
  const stageIndex = Math.max(0, workflowStages.findIndex(item => item.key === stage.key));
  $("#workflow-current-stage").textContent = workflow.status === "completed" ? "索引已发布" : stage.label;
  $("#workflow-stage-updated").textContent = `更新于 ${formatTime(workflow.updated_at)}`;
  $("#workflow-progress-value").textContent = `${progress}%`;
  $(".workflow-stage-icon i").className = `ph ph-${workflow.status === "failed" ? "warning" : workflow.status === "completed" ? "check" : stage.icon}`;
  $$("#workflow-track [data-stage]").forEach((node, index) => {
    node.className = "";
    if (workflow.status === "completed" || index < stageIndex) node.classList.add("done");
    if (index === stageIndex && workflow.status !== "completed") node.classList.add(workflow.status === "failed" ? "failed" : "current");
    node.querySelector("i").textContent = workflow.status === "completed" || index < stageIndex ? "✓" : "";
  });
  const counters = workflow.counters || {};
  $("#workflow-stat-discovered").textContent = formatNumber(counters.discovered);
  $("#workflow-stat-files").textContent = formatNumber(counters.files);
  $("#workflow-stat-symbols").textContent = formatNumber(counters.symbols);
  $("#workflow-stat-edges").textContent = formatNumber(counters.edges);
  const errorCallout = $("#workflow-error-callout");
  errorCallout.hidden = !workflow.error;
  $("#workflow-error-message").textContent = workflowErrorMessage(workflow.error);
  const rawError = $("#workflow-raw-error");
  rawError.hidden = !workflow.error;
  rawError.textContent = workflow.error || "";
}

function renderRepositorySelectors() {
  const options = state.repositories.map(repo => `<option value="${escapeHtml(repo.id)}">${escapeHtml(repo.name)} · ${escapeHtml(shortCommit(repo.head_commit))}</option>`).join("");
  const browser = $("#browser-repository");
  const browserValue = state.selectedRepository || browser.value;
  browser.innerHTML = `<option value="">选择仓库</option>${options}`;
  browser.value = state.repositories.some(item => item.id === browserValue) ? browserValue : "";
  const history = $("#history-repository");
  const historyValue = state.selectedRepository || history.value;
  history.innerHTML = `<option value="">选择仓库</option>${options}`;
  history.value = state.repositories.some(item => item.id === historyValue) ? historyValue : "";
  renderGitHistoryLauncher(
    state.repositories.find(item => item.id === history.value) || null
  );
}

async function resyncRepository(repository) {
  const source = repository.source_url || repository.local_path;
  if (!source) {
    showToast("该仓库缺少可同步的来源地址");
    return;
  }
  try {
    const result = await api("/v1/ingestion/repositories", {
      method: "POST",
      body: JSON.stringify({
        source,
        branch: repository.default_branch === "HEAD" ? null : repository.default_branch,
        project_id: repository.project_id,
        acl_ref: repository.acl_ref,
        history_depth: 25,
      }),
    });
    showToast(`已创建同步任务：${result.workflow_id.slice(0, 14)}`);
    await refreshAll();
    pollWorkflow(result.workflow_id);
  } catch (error) {
    showToast(`同步失败：${error.message}`);
  }
}

async function retryWorkflow(workflowId) {
  try {
    const result = await api(`/v1/ingestion/workflows/${encodeURIComponent(workflowId)}/retry`, {
      method: "POST",
      body: "{}",
    });
    showToast(`已重新创建任务：${result.workflow_id.slice(0, 14)}`);
    await refreshAll();
    pollWorkflow(result.workflow_id);
  } catch (error) {
    showToast(`重试失败：${error.message}`);
  }
}

async function submitIngestion(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const errorBox = $("#ingest-error");
  errorBox.hidden = true;
  const payload = {
    source: form.get("source"),
    branch: form.get("branch") || null,
    project_id: form.get("project_id") || "project-rag",
    acl_ref: `project:${form.get("project_id") || "project-rag"}`,
    ignore: String(form.get("ignore") || "").split(",").map(item => item.trim()).filter(Boolean),
    history_depth: Number(form.get("history_depth") || 25),
  };
  try {
    const result = await api("/v1/ingestion/repositories", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#ingest-dialog").close();
    showToast(`索引任务已创建：${result.workflow_id.slice(0, 14)}`);
    switchView("repository");
    await refreshAll();
    pollWorkflow(result.workflow_id);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

function pollWorkflow(id) {
  clearTimeout(state.pollTimer);
  const poll = async () => {
    try {
      const item = await api(`/v1/ingestion/workflows/${encodeURIComponent(id)}`);
      if (item.kind === "repository") renderWorkflowTrack(item);
      if (["completed", "failed"].includes(item.status)) {
        showToast(item.status === "completed" ? "索引 Generation 已发布" : `索引失败：${item.error}`);
        if (item.status === "completed" && item.kind === "repository") state.selectedRepository = item.repository_id;
        await refreshAll();
        return;
      }
      state.pollTimer = setTimeout(poll, 900);
    } catch (error) {
      showToast(error.message);
    }
  };
  poll();
}
