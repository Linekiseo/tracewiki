const ragWorkbenchState = {
  repositoryFiles: new Map(),
  openRepositories: new Set(),
  currentFile: null,
  currentRepositoryId: null,
  selectedEvidenceId: null,
  lastResult: null,
  sourcePane: "code",
  requestSerial: 0,
  codeRequestSerial: 0,
  fileSearchSerial: 0,
  eventsInstalled: false,
  queryController: null,
  queryTimer: null,
  queryAbortReason: null,
  queryConversationId: null,
  querySnapshot: null,
  queryContextRevision: null,
  queryLastTurnId: null,
  awaitingClarification: false,
  lastQuestion: "",
};

const ragDomainGlyph = source => ({code:"码", codex:"会", workspace:"研", experiment:"验", document:"文"})[source] || "证";
const ragSourceLabel = source => ({code:"代码", codex:"研发会话", workspace:"研究工作区", experiment:"实验数据", document:"科研文档"})[source] || source;
const ragRoleLabel = role => ({verified:"直接事实", supporting:"支持证据", counter:"反证", inferred:"推断关系"})[role] || role;
const ragIntentLabel = intent => ({
  current_implementation:"当前实现",
  historical_implementation:"历史实现",
  change_trace:"变更追踪",
  rationale:"设计原因",
  experiment_validation:"实验验证",
  claim_verification:"结论核验",
  reproduction:"实验复现",
  staleness_check:"有效性检查",
  global_synthesis:"项目综合",
})[intent] || "项目查询";
const ragIntentDescription = intent => ({
  current_implementation:"定位当前版本中的实现、入口、调用与测试",
  historical_implementation:"定位历史版本中的实现和对应 Commit",
  change_trace:"追踪谁在何时修改了什么，以及相关 Patch 与会话",
  rationale:"查找设计目标、决策、替代方案与最终实现",
  experiment_validation:"核对 Run、Metric、数据版本与实现版本",
  claim_verification:"核对文档 Claim 是否有实验、代码和原文支持",
  reproduction:"收集复现实验所需的代码、配置、数据与环境",
  staleness_check:"比较原始证据与当前版本，判断结论是否仍有效",
  global_synthesis:"跨代码、会话、实验和文档综合项目事实",
})[intent] || "跨来源检索项目证据";
const ragMissingRoleLabel = role => ({
  current_code:"当前代码",
  version:"明确版本",
  tests:"测试结果",
  experiment:"实验结果",
  rationale:"设计依据",
  document:"科研文档",
  claim:"可验证结论",
})[role] || role;
const ragSvgIcon = name => {
  const paths = {
    chevron:'<path d="m6.25 3.5 4.5 4.5-4.5 4.5" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"/>',
    repo:'<path d="M3.25 1h8.5A1.25 1.25 0 0 1 13 2.25v10.5c0 .41-.34.75-.75.75H4.5a1.5 1.5 0 0 0 0 3h7.75a.75.75 0 0 1 0 1.5H4.5A3 3 0 0 1 1.5 15V3.25A2.25 2.25 0 0 1 3.25 1Zm-.25 11.4c.45-.26.96-.4 1.5-.4h7V2.5H3.25a.25.25 0 0 0-.25.25v9.65Z"/>',
    folder:'<path d="M1.75 2.5h4l1.5 1.75h7a1.25 1.25 0 0 1 1.25 1.25v7.75a1.25 1.25 0 0 1-1.25 1.25H1.75A1.25 1.25 0 0 1 .5 13.25v-9.5A1.25 1.25 0 0 1 1.75 2.5Zm.25 1.5v9h12V5.75H6.56L5.06 4H2Z"/>',
    file:'<path d="M3.75 1h5.19L13 5.06v9.19A1.75 1.75 0 0 1 11.25 16h-7.5A1.75 1.75 0 0 1 2 14.25V2.75A1.75 1.75 0 0 1 3.75 1Zm4.5 1.5h-4.5a.25.25 0 0 0-.25.25v11.5c0 .14.11.25.25.25h7.5c.14 0 .25-.11.25-.25V6h-2A1.25 1.25 0 0 1 8.25 4.75V2.5Zm1.5.56V4.5h1.44L9.75 3.06Z"/>',
    branch:'<path d="M4 2.5a2.5 2.5 0 1 1-1.5 2.29v6.42a2.5 2.5 0 1 1 1.5 0V8.75h4.25A2.75 2.75 0 0 0 11 6V4.79a2.5 2.5 0 1 1 1.5 0V6a4.25 4.25 0 0 1-4.25 4.25H4v.96a2.5 2.5 0 0 1 0-4.42V4.79A2.5 2.5 0 0 1 4 2.5Z"/>',
    discussion:'<path d="M2.75 2h10.5C14.77 2 16 3.23 16 4.75v5.5A2.75 2.75 0 0 1 13.25 13H8.81l-3.09 2.32A.75.75 0 0 1 4.5 14.7V13H2.75A2.75 2.75 0 0 1 0 10.25v-5.5A2.75 2.75 0 0 1 2.75 2Zm0 1.5c-.69 0-1.25.56-1.25 1.25v5.5c0 .69.56 1.25 1.25 1.25H6v1.7l2.31-1.7h4.94c.69 0 1.25-.56 1.25-1.25v-5.5c0-.69-.56-1.25-1.25-1.25H2.75Z"/>',
    beaker:'<path d="M5 1.25A.75.75 0 0 1 5.75.5h4.5a.75.75 0 0 1 0 1.5H10v3.12l3.66 6.1A2.5 2.5 0 0 1 11.52 15H4.48a2.5 2.5 0 0 1-2.14-3.78L6 5.12V2h-.25A.75.75 0 0 1 5 1.25ZM7.5 5.54l-3.87 6.45a1 1 0 0 0 .85 1.51h7.04a1 1 0 0 0 .85-1.51L8.5 5.54V2h-1v3.54Zm-2.03 5.02h5.06l.9 1.5H4.57l.9-1.5Z"/>',
    document:'<path d="M3.75 1h5.19L13 5.06v9.19A1.75 1.75 0 0 1 11.25 16h-7.5A1.75 1.75 0 0 1 2 14.25V2.75A1.75 1.75 0 0 1 3.75 1Zm0 1.5a.25.25 0 0 0-.25.25v11.5c0 .14.11.25.25.25h7.5c.14 0 .25-.11.25-.25V6h-2A1.25 1.25 0 0 1 8.25 4.75V2.5h-4.5Zm6 .56V4.5h1.44L9.75 3.06ZM5 8h5v1.25H5V8Zm0 2.5h5v1.25H5V10.5Z"/>',
  };
  return `<svg class="rag-octicon" viewBox="0 0 16 16" aria-hidden="true">${paths[name] || paths.file}</svg>`;
};

function refreshRagWorkbench() {
  const select = $("#rag-repository");
  if (!select) return;
  const ready = state.repositories.filter(item => item.status === "ready");
  const currentScope = select.value;
  select.innerHTML = `<option value="">全部代码仓库</option>${ready.map(repo => (
    `<option value="${escapeHtml(repo.id)}">${escapeHtml(repo.name)} · ${escapeHtml(shortCommit(repo.head_commit))}</option>`
  )).join("")}`;
  select.value = ready.some(item => item.id === currentScope) ? currentScope : "";

  $("#rag-coverage-repositories").textContent = formatNumber(ready.length);
  $("#rag-coverage-files").textContent = formatNumber(ready.reduce((sum, item) => sum + Number(item.stats?.files || 0), 0));
  $("#rag-coverage-sessions").textContent = formatNumber(state.codexSessions.length);
  $("#rag-coverage-experiments").textContent = formatNumber(state.experiments.length);
  $("#rag-coverage-documents").textContent = formatNumber(state.documents.length);
  $("#rag-source-code-count").textContent = formatNumber(ready.length);
  $("#rag-source-codex-count").textContent = formatNumber(state.codexSessions.length);
  $("#rag-source-experiment-count").textContent = formatNumber(state.experiments.length);
  $("#rag-source-document-count").textContent = formatNumber(state.documents.length);
  $("#rag-generation-label").textContent = `${ready.length} 个代码库 · 全项目检索`;
  updateRagProjectScope();
  renderRagProjectAssets();

  if (state.selectedRepository && ready.some(item => item.id === state.selectedRepository)) {
    ragWorkbenchState.openRepositories.add(state.selectedRepository);
  }
  renderRagFileList($("#rag-file-filter").value);
}

function setRagSourcePane(source) {
  const supported = ["code", "codex", "experiment", "document"];
  const next = supported.includes(source) ? source : "code";
  ragWorkbenchState.sourcePane = next;
  $$("[data-rag-source-tab]").forEach(button => {
    const active = button.dataset.ragSourceTab === next;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $$("[data-rag-source-pane]").forEach(pane => pane.classList.toggle("active", pane.dataset.ragSourcePane === next));
}

function renderRagProjectAssets() {
  const codex = $("#rag-codex-assets");
  const experiments = $("#rag-experiment-assets");
  const documents = $("#rag-document-assets");
  if (!codex || !experiments || !documents) return;

  codex.innerHTML = state.codexSessions.length ? `<div class="rag-asset-browser-head"><div><strong>研发会话</strong><small>目标、命令、补丁与验证记录</small></div><b>${state.codexSessions.length} THREADS</b></div>
    <div class="rag-asset-list">${state.codexSessions.map(item => `<button type="button" class="rag-asset-item" data-rag-codex-thread="${escapeHtml(item.thread_id)}">
      <span class="rag-asset-visual session">${ragSvgIcon("discussion")}</span><div><strong>${escapeHtml(item.title || item.thread_id)}</strong><small>${escapeHtml(item.cwd || "Codex session")}</small><span>${formatNumber(item.turn_count)} Turns · ${formatNumber(item.file_change_count)} 文件变更 · ${formatNumber(item.command_count)} 命令</span></div><i>打开</i>
    </button>`).join("")}</div>`
    : `<div class="rag-empty"><span>${ragSvgIcon("discussion")}</span><b>尚未接入研发会话</b><small>在“研发会话”中同步 Codex Thread 后，会作为项目级 RAG 来源参与检索。</small></div>`;

  experiments.innerHTML = state.experiments.length ? `<div class="rag-asset-browser-head"><div><strong>实验与数据</strong><small>Run、Dataset、Config、Metric 与 Artifact</small></div><b>${state.experiments.length} EXPERIMENTS</b></div>
    <div class="rag-asset-list">${state.experiments.map(item => `<button type="button" class="rag-asset-item" data-rag-experiment="${escapeHtml(item.id)}">
      <span class="rag-asset-visual experiment">${ragSvgIcon("beaker")}</span><div><strong>${escapeHtml(item.title || item.name || item.id)}</strong><small>${escapeHtml(item.objective || item.hypothesis || "可复现实验")}</small><span>${escapeHtml(statusLabel(item.status || "active"))}</span></div><i>打开</i>
    </button>`).join("")}</div>`
    : `<div class="rag-empty"><span>${ragSvgIcon("beaker")}</span><b>尚未接入实验数据</b><small>MLflow、Notebook 和手工 Run 接入后，会在这里形成项目实验事实来源。</small></div>`;

  documents.innerHTML = state.documents.length ? `<div class="rag-asset-browser-head"><div><strong>科研文档</strong><small>章节、表格、图、引用与 Claim</small></div><b>${state.documents.length} DOCUMENTS</b></div>
    <div class="rag-asset-list">${state.documents.map(item => `<button type="button" class="rag-asset-item" data-rag-document="${escapeHtml(item.id)}">
      <span class="rag-asset-visual document">${ragSvgIcon("document")}</span><div><strong>${escapeHtml(item.title || item.id)}</strong><small>${escapeHtml(item.source_uri || item.version || "Scientific document")}</small><span>${formatNumber(item.claim_count)} Claims · ${escapeHtml(statusLabel(item.status || "active"))}</span></div><i>打开</i>
    </button>`).join("")}</div>`
    : `<div class="rag-empty"><span>${ragSvgIcon("document")}</span><b>尚未接入科研文档</b><small>论文、报告和实验记录接入后，可从回答直接回到 Page、Table、Figure 和 Claim。</small></div>`;

  $$("[data-rag-codex-thread]", codex).forEach(button => button.addEventListener("click", async () => {
    switchView("codex");
    await selectCodexThread(button.dataset.ragCodexThread);
  }));
  $$("[data-rag-experiment]", experiments).forEach(button => button.addEventListener("click", () => {
    state.selectedExperiment = button.dataset.ragExperiment;
    renderExperimentCenter();
    switchView("experiments");
  }));
  $$("[data-rag-document]", documents).forEach(button => button.addEventListener("click", () => {
    state.selectedDocument = button.dataset.ragDocument;
    renderDocumentCenter();
    switchView("documents");
  }));
}

function updateRagProjectScope() {
  const repositoryId = $("#rag-repository")?.value || "";
  const repository = state.repositories.find(item => item.id === repositoryId);
  const source = $("#search-source")?.value || "";
  if (repository || source) {
    $("#rag-scope-title").textContent = "已缩小查询范围";
    $("#rag-scope-description").textContent = [
      repository ? `代码：${repository.name}` : "全部代码仓库",
      source ? `证据：${ragSourceLabel(source)}` : "全部证据类型",
    ].join(" · ");
  } else {
    $("#rag-scope-title").textContent = "整个研发项目";
    $("#rag-scope-description").textContent = "代码、研发会话、实验与科研文档";
  }
}

async function ensureRagRepositoryFiles(repositoryId, {force = false} = {}) {
  if (!force && ragWorkbenchState.repositoryFiles.has(repositoryId)) {
    return ragWorkbenchState.repositoryFiles.get(repositoryId);
  }
  const files = await api(`/v1/code/files?repository_id=${encodeURIComponent(repositoryId)}`);
  const normalized = files.map(file => ({...file, repository_id: repositoryId}));
  ragWorkbenchState.repositoryFiles.set(repositoryId, normalized);
  return normalized;
}

async function syncRagRepository(repositoryId, {force = false} = {}) {
  const repository = state.repositories.find(item => item.id === repositoryId);
  if (!repository) return;
  ragWorkbenchState.currentRepositoryId = repositoryId;
  ragWorkbenchState.openRepositories.add(repositoryId);
  renderRagFileList($("#rag-file-filter").value);
  try {
    await ensureRagRepositoryFiles(repositoryId, {force});
    renderRagFileList($("#rag-file-filter").value);
  } catch (error) {
    showToast(`项目目录加载失败：${error.message}`);
  }
}

function buildRagPathTree(files) {
  const root = {directories:new Map(), files:[]};
  files.forEach(file => {
    const parts = file.path.split("/").filter(Boolean);
    let cursor = root;
    parts.slice(0, -1).forEach(part => {
      if (!cursor.directories.has(part)) cursor.directories.set(part, {directories:new Map(), files:[]});
      cursor = cursor.directories.get(part);
    });
    cursor.files.push({...file, file_name:parts.at(-1) || file.path});
  });
  return root;
}

function countRagTreeFiles(node) {
  return node.files.length + [...node.directories.values()].reduce((total, child) => total + countRagTreeFiles(child), 0);
}

function renderRagTreeNode(node, prefix, repositoryId, expandAll = false) {
  const current = ragWorkbenchState.currentFile;
  const directories = [...node.directories.entries()].sort(([left], [right]) => left.localeCompare(right));
  const files = [...node.files].sort((left, right) => left.file_name.localeCompare(right.file_name));
  const directoryHtml = directories.map(([name, child]) => {
    const path = prefix ? `${prefix}/${name}` : name;
    const containsCurrent = current?.repository_id === repositoryId && current.path.startsWith(`${path}/`);
    return `<details class="rag-tree-directory" data-rag-directory="${escapeHtml(path)}" role="treeitem" aria-label="${escapeHtml(name)}" ${expandAll || containsCurrent ? "open" : ""}>
      <summary><span class="rag-tree-arrow">${ragSvgIcon("chevron")}</span><span class="rag-tree-folder-icon">${ragSvgIcon("folder")}</span><span>${escapeHtml(name)}</span><b>${countRagTreeFiles(child)}</b></summary>
      <div class="rag-tree-children">${renderRagTreeNode(child, path, repositoryId, expandAll)}</div>
    </details>`;
  }).join("");
  const fileHtml = files.map(file => {
    const language = normalizeCodeLanguage(file.language, file.path);
    const meta = codeLanguageMeta[language] || codeLanguageMeta.text;
    return `
    <button type="button" class="rag-tree-file ${current?.repository_id === repositoryId && current.path === file.path ? "active" : ""}"
      data-rag-file="${escapeHtml(file.path)}" data-rag-file-repository="${escapeHtml(repositoryId)}" title="${escapeHtml(file.path)}" role="treeitem">
      <span class="rag-tree-file-type language-${escapeHtml(language)}">${escapeHtml(meta.mark)}</span>
      <span>${escapeHtml(file.file_name)}</span>
    </button>`;
  }).join("");
  return directoryHtml + fileHtml;
}

function renderRagFileList(filter = "") {
  const list = $("#rag-file-list");
  if (!list) return;
  const query = filter.trim().toLowerCase();
  const repositories = state.repositories.filter(item => item.status === "ready");
  const loadedCount = repositories.reduce((total, repository) => total + (ragWorkbenchState.repositoryFiles.get(repository.id)?.length || 0), 0);
  $("#rag-file-summary").textContent = query
    ? `在 ${repositories.length} 个仓库中筛选`
    : `${repositories.length} repositories${loadedCount ? ` · ${formatNumber(loadedCount)} files` : ""}`;
  if (!repositories.length) {
    list.innerHTML = '<div class="rag-empty compact"><b>还没有可浏览的代码</b><span>请先在“代码与版本”中接入仓库</span></div>';
    return;
  }

  const html = repositories.map(repository => {
    const loadedFiles = ragWorkbenchState.repositoryFiles.get(repository.id);
    const visibleFiles = loadedFiles?.filter(file => !query || file.path.toLowerCase().includes(query)) || [];
    const isOpen = query ? Boolean(visibleFiles.length) : ragWorkbenchState.openRepositories.has(repository.id);
    if (query && loadedFiles && !visibleFiles.length) return "";
    const content = loadedFiles
      ? (visibleFiles.length
        ? renderRagTreeNode(buildRagPathTree(visibleFiles), "", repository.id, Boolean(query))
        : '<div class="rag-tree-loading">没有匹配文件</div>')
      : '<div class="rag-tree-loading">展开以读取目录层级</div>';
    return `<details class="rag-tree-repository" data-rag-tree-repository="${escapeHtml(repository.id)}" role="treeitem" ${isOpen ? "open" : ""}>
      <summary>
        <span class="rag-tree-arrow">${ragSvgIcon("chevron")}</span>
        <span class="rag-tree-repo-icon">${ragSvgIcon("repo")}</span>
        <div><strong>${escapeHtml(repository.name)}</strong><small><span class="rag-branch-name">${ragSvgIcon("branch")}${escapeHtml(repository.default_branch || "HEAD")}</span><code>${escapeHtml(shortCommit(repository.head_commit).slice(0, 7))}</code></small></div>
      </summary>
      <div class="rag-tree-repository-content">${content}</div>
    </details>`;
  }).join("");
  list.innerHTML = html || '<div class="rag-empty compact"><b>没有匹配文件</b><span>尝试使用文件名或目录名搜索</span></div>';
}

async function filterRagProjectFiles(query) {
  const serial = ++ragWorkbenchState.fileSearchSerial;
  if (query.trim()) {
    $("#rag-file-list").innerHTML = '<div class="rag-empty compact"><b>正在查找项目文件</b><span>搜索所有代码仓库的目录</span></div>';
    const ready = state.repositories.filter(item => item.status === "ready");
    await Promise.all(ready.map(item => ensureRagRepositoryFiles(item.id).catch(() => [])));
  }
  if (serial === ragWorkbenchState.fileSearchSerial) renderRagFileList(query);
}

function clearRagCodeViewer() {
  ragWorkbenchState.currentFile = null;
  $("#rag-code-breadcrumb").innerHTML = '<span id="rag-code-repository-name">project</span><i>/</i><strong id="rag-code-path">选择一个文件查看原文</strong>';
  $("#rag-code-locator").textContent = "也可以先提问，相关代码会自动在这里打开";
  $("#rag-code-language").textContent = "Text";
  $("#rag-code-language").className = "status-pill code-language-badge language-text";
  $("#rag-code-size").textContent = "—";
  $("#rag-code-content").innerHTML = '<div class="rag-empty"><span>{ }</span><b>这里显示可核验的源码</b><small>从左侧目录打开文件，或在右侧选择一条代码证据。</small></div>';
}

function renderRagCodeBreadcrumb(repository, path) {
  const parts = path.split("/").filter(Boolean);
  const directories = parts.slice(0, -1);
  let current = "";
  const directoryHtml = directories.map(part => {
    current = current ? `${current}/${part}` : part;
    return `<i>/</i><button type="button" data-rag-breadcrumb="${escapeHtml(current)}">${escapeHtml(part)}</button>`;
  }).join("");
  $("#rag-code-breadcrumb").innerHTML = `<span id="rag-code-repository-name">${escapeHtml(repository?.name || "repository")}</span>${directoryHtml}<i>/</i><strong id="rag-code-path">${escapeHtml(parts.at(-1) || path)}</strong>`;
}

function revealRagTreeFile(repositoryId, path) {
  const list = $("#rag-file-list");
  if (!list) return false;
  list.querySelectorAll(".rag-tree-file.active").forEach(button => button.classList.remove("active"));
  const button = [...list.querySelectorAll("[data-rag-file][data-rag-file-repository]")]
    .find(item => item.dataset.ragFileRepository === repositoryId && item.dataset.ragFile === path);
  if (!button) return false;
  button.classList.add("active");
  let parent = button.parentElement?.closest("details");
  while (parent) {
    parent.open = true;
    parent = parent.parentElement?.closest("details");
  }
  button.scrollIntoView({block:"nearest"});
  return true;
}

async function loadRagFile(repositoryId, path, highlight = {}) {
  if (!repositoryId || !path) return;
  const serial = ++ragWorkbenchState.codeRequestSerial;
  const repository = state.repositories.find(item => item.id === repositoryId);
  ragWorkbenchState.currentRepositoryId = repositoryId;
  ragWorkbenchState.openRepositories.add(repositoryId);
  renderRagCodeBreadcrumb(repository, path);
  $("#rag-code-locator").textContent = "正在读取已发布版本…";
  try {
    await ensureRagRepositoryFiles(repositoryId);
    const file = await api(`/v1/code/file?repository_id=${encodeURIComponent(repositoryId)}&path=${encodeURIComponent(path)}`);
    if (serial !== ragWorkbenchState.codeRequestSerial) return;
    ragWorkbenchState.currentFile = {...file, repository_id: repositoryId};
    if (!revealRagTreeFile(repositoryId, file.path)) {
      renderRagFileList($("#rag-file-filter").value);
      requestAnimationFrame(() => revealRagTreeFile(repositoryId, file.path));
    }
    renderRagCodeBreadcrumb(repository, file.path);
    $("#rag-code-locator").textContent = `${repository?.default_branch || "HEAD"} · ${shortCommit(file.commit_sha)}${highlight.start ? ` · 第 ${highlight.start}–${highlight.end || highlight.start} 行` : ""}`;
    const language = normalizeCodeLanguage(file.language, file.path);
    const languageMeta = codeLanguageMeta[language] || codeLanguageMeta.text;
    const rendered = renderHighlightedCode(file.content, language, {
      start:highlight.start,
      end:highlight.end || highlight.start,
    });
    $("#rag-code-language").textContent = languageMeta.label;
    $("#rag-code-language").className = `status-pill code-language-badge language-${language}`;
    $("#rag-code-size").textContent = `${formatNumber(rendered.lines.length)} LINES · ${formatCodeBytes(file.content)}`;
    $("#rag-code-content").innerHTML = rendered.html;
    if (highlight.start) {
      const container = $("#rag-code-content");
      requestAnimationFrame(() => container.querySelector(".code-row.highlight")?.scrollIntoView({block:"center"}));
    }
  } catch (error) {
    $("#rag-code-content").innerHTML = `<div class="rag-empty"><b>代码加载失败</b><small>${escapeHtml(error.message)}</small></div>`;
  }
}

function switchRagTab(name) {
  const workbench = $("#rag-workbench");
  workbench?.classList.toggle("graph-mode", name === "graph");
  $$("[data-rag-tab]").forEach(button => {
    const active = button.dataset.ragTab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $$("[data-rag-panel]").forEach(panel => panel.classList.toggle("active", panel.dataset.ragPanel === name));
  if (name === "graph" && typeof activateRagGraph === "function") activateRagGraph();
}

function setRagLayout(mode, ratio = null) {
  const workbench = $("#rag-workbench");
  if (!workbench) return;
  workbench.classList.toggle("layout-code", mode === "code");
  workbench.classList.toggle("layout-evidence", mode === "evidence");
  $$("[data-rag-layout]").forEach(button => button.classList.toggle("active", button.dataset.ragLayout === mode));
  if (mode === "balanced") {
    const value = ratio ?? Number(localStorage.getItem("rag_split_ratio") || 50);
    workbench.style.setProperty("--rag-left", `${Math.min(72, Math.max(32, value))}%`);
  }
}

function setRagSplitRatio(ratio) {
  const safe = Math.min(72, Math.max(32, ratio));
  const workbench = $("#rag-workbench");
  workbench.classList.remove("layout-code", "layout-evidence");
  workbench.style.setProperty("--rag-left", `${safe}%`);
  localStorage.setItem("rag_split_ratio", String(safe));
  $$("[data-rag-layout]").forEach(button => button.classList.toggle("active", button.dataset.ragLayout === "balanced"));
}

async function runSearch(event) {
  event.preventDefault();
  if (ragWorkbenchState.queryController) {
    ragWorkbenchState.queryAbortReason = "cancelled";
    ragWorkbenchState.queryController.abort();
    return;
  }
  const form = new FormData(event.currentTarget);
  const enteredQuestion = String(form.get("query") || "").trim();
  if (!enteredQuestion) return;
  const source = String(form.get("source") || "");
  const intent = String(form.get("intent") || "");
  const repositoryId = String(form.get("repository_id") || "");
  const requestedProject = new URLSearchParams(window.location.search).get("project");
  const projectId = (requestedProject && requestedProject !== "all")
    ? requestedProject
    : state.selectedProject || "project-rag";
  const controller = new AbortController();
  const clientTurnId = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `turn-${Date.now()}`;
  const conversationId = ragWorkbenchState.queryConversationId || `conversation-${clientTurnId}`;
  const canResume = Boolean(ragWorkbenchState.querySnapshot);
  const question = ragWorkbenchState.awaitingClarification && !canResume
    ? `${ragWorkbenchState.lastQuestion}\n\n补充范围：${enteredQuestion}`
    : enteredQuestion;
  const serial = ++ragWorkbenchState.requestSerial;
  const submit = $(".rag-submit");
  ragWorkbenchState.queryController = controller;
  ragWorkbenchState.queryAbortReason = null;
  ragWorkbenchState.queryConversationId = conversationId;
  if (!ragWorkbenchState.awaitingClarification) ragWorkbenchState.lastQuestion = enteredQuestion;
  submit.querySelector("span").textContent = "取消查询";
  submit.querySelector("small").textContent = "ESC";
  $("#query-decision").hidden = true;
  $("#rag-query-brief").hidden = false;
  $("#rag-query-brief").innerHTML = `<span>正在理解</span><div><strong>${escapeHtml(question)}</strong><small>识别问题类型、解析项目范围并选择检索通道</small></div><b>分析中</b>`;
  $("#rag-answer-content").innerHTML = '<div class="rag-empty"><span>···</span><b>正在理解问题</b><small>同时查找代码、会话、实验和科研文档中的相关依据。</small></div>';
  $("#search-results").innerHTML = '<div class="rag-empty"><span>···</span><b>正在检索整个项目</b><small>结果会保留来源、版本和原始位置。</small></div>';
  $("#search-summary").textContent = "正在查询整个项目";
  $("#search-duration").textContent = "查询中";
  switchRagTab("answer");
  try {
    const payload = {
      question,
      scope: {
        project_id: projectId,
        repository_ids: repositoryId ? [repositoryId] : [],
      },
      mode: "answer",
      max_evidence: 12,
      intent: intent || null,
      include: source ? [source] : [],
      conversation_id: conversationId,
      client_turn_id: clientTurnId,
      parent_turn_id: canResume ? ragWorkbenchState.queryLastTurnId : null,
      context_revision: canResume ? ragWorkbenchState.queryContextRevision : null,
      conversation_snapshot: canResume ? ragWorkbenchState.querySnapshot : null,
      clarification_policy: "auto",
      deadline_ms: 15000,
    };
    ragWorkbenchState.queryTimer = window.setTimeout(() => {
      ragWorkbenchState.queryAbortReason = "timeout";
      controller.abort();
    }, 16500);
    const result = await api("/v1/query", {
      method:"POST",
      body:JSON.stringify(payload),
      signal:controller.signal,
    });
    if (serial !== ragWorkbenchState.requestSerial) return;
    ragWorkbenchState.querySnapshot = result.conversation_snapshot || null;
    ragWorkbenchState.queryContextRevision = result.context_revision ?? null;
    ragWorkbenchState.queryLastTurnId = result.client_turn_id || clientTurnId;
    ragWorkbenchState.awaitingClarification = ragQueryMode(result) === "clarification";
    renderQueryResult(result);
  } catch (error) {
    if (serial !== ragWorkbenchState.requestSerial) return;
    const aborted = controller.signal.aborted;
    const timeout = aborted && ragWorkbenchState.queryAbortReason === "timeout";
    const title = timeout ? "查询超时" : aborted ? "查询已取消" : "项目查询失败";
    const message = timeout
      ? "客户端已按延迟预算停止等待；可以缩小范围后重试。"
      : aborted
        ? "本次查询已取消，不会用迟到响应覆盖当前界面。"
        : "查询服务暂时不可用，请稍后重试。";
    $("#rag-answer-content").innerHTML = `<div class="rag-empty"><span>!</span><b>${title}</b><small>${message}</small></div>`;
    $("#search-results").innerHTML = `<div class="rag-empty"><b>没有取得证据结果</b><small>${message}</small></div>`;
    $("#search-summary").textContent = title;
    $("#search-duration").textContent = "—";
  } finally {
    if (ragWorkbenchState.queryTimer) window.clearTimeout(ragWorkbenchState.queryTimer);
    ragWorkbenchState.queryTimer = null;
    if (serial === ragWorkbenchState.requestSerial) {
      ragWorkbenchState.queryController = null;
      ragWorkbenchState.queryAbortReason = null;
      submit.querySelector("span").textContent = "查询整个项目";
      submit.querySelector("small").textContent = "⌘ ↵";
    }
  }
}

function ragQueryMode(result) {
  const states = [
    result.interaction?.answer_mode,
    result.interaction?.state,
    result.state,
    result.answer?.answer_mode,
    result.answer?.status,
  ].filter(Boolean).map(value => String(value).toLowerCase());
  if (states.some(value => value === "needs_clarification" || value === "clarification")) return "clarification";
  if (result.answer?.refusal || states.some(value => value === "refusal" || value === "refused")) return "refusal";
  if (states.includes("partial")) return "partial";
  if (states.some(value => value === "retrieval_only" || value === "insufficient_evidence")) return "retrieval_only";
  return "grounded";
}

function renderQueryResult(result) {
  const pack = result.evidence_pack || {};
  const results = [
    ...(pack.verified_facts || []).map(item => ({...item, evidence_role:"verified"})),
    ...(pack.supporting_evidence || []).map(item => ({...item, evidence_role:"supporting"})),
    ...(pack.counter_evidence || []).map(item => ({...item, evidence_role:"counter"})),
    ...(pack.inferred_relations || []).filter(item => item.entity_id).map(item => ({...item, evidence_role:"inferred"})),
  ];
  const relations = [...(pack.relations || []), ...(pack.version_differences || [])];
  const payload = {total:results.length, results, evidence_pack:{...pack, relations}, trace:pack.trace || {}};
  ragWorkbenchState.lastResult = {result, payload};
  renderRagQueryBrief(result, payload);
  const decision = $("#query-decision");
  const missing = (pack.missing_evidence || []).length;
  decision.hidden = false;
  const mode = ragQueryMode(result);
  const modeLabel = {clarification:"需要澄清", partial:"PARTIAL", refusal:"拒绝作答", retrieval_only:"仅检索", grounded:"GROUNDED"}[mode];
  decision.innerHTML = `<span>${escapeHtml(modeLabel)}</span><div><strong>${results.length ? `找到 ${results.length} 条可核验依据` : "没有找到足够依据"}</strong><small>${missing ? `仍缺少 ${missing} 类证据` : "证据角色完整"} · 查询编号 ${escapeHtml(String(result.trace_id || result.query_id || "").slice(0, 8))}</small></div><b>${Math.round(Number(pack.decision?.confidence || 0) * 100)}%</b>`;
  renderRagAnswer(result, payload);
  renderSearchResults(payload);
  $("#rag-answer-count").textContent = "1";
  $("#rag-evidence-count").textContent = String(results.length);
  $("#rag-relation-count").textContent = String(relations.length);
  const firstCodeEvidence = results.find(item => item.source === "code" && item.repository_id && item.path);
  if (firstCodeEvidence) openCodeEvidenceInRag(firstCodeEvidence);
  if (mode === "clarification") {
    const question = $('#evidence-search-form textarea[name="query"]');
    question.value = "";
    question.placeholder = "请补充目标项目实体、分支、提交或时间范围";
    question.focus();
  }
}

function renderRagQueryBrief(result, payload) {
  const pack = result.evidence_pack || {};
  const sources = [...new Set(payload.results.map(item => ragSourceLabel(item.source)))];
  const repositories = (pack.resolved_scope || result.resolved_scope)?.repositories || [];
  const scope = repositories.length
    ? `${repositories.length} 个代码仓库 · ${sources.join("、") || "全部来源"}`
    : `整个研发项目 · ${sources.join("、") || "当前无命中来源"}`;
  const answerMode = {
    clarification:"需要澄清",
    partial:"部分结果",
    refusal:"拒绝作答",
    retrieval_only:"仅检索",
    grounded:"带引用生成",
  }[ragQueryMode(result)];
  const brief = $("#rag-query-brief");
  brief.hidden = false;
  brief.innerHTML = `<span>系统理解</span><div><strong>${escapeHtml(pack.question || ragWorkbenchState.lastQuestion)}</strong><small>${escapeHtml(ragIntentLabel(pack.intent))}：${escapeHtml(ragIntentDescription(pack.intent))} · 范围：${escapeHtml(scope)}</small></div><b>${escapeHtml(answerMode)}</b>`;
}

function buildRagReadableSummary(result, results) {
  if (!results.length) return "当前项目索引中没有找到足以回答这个问题的依据。可以换一种问法，或检查相关代码、实验和文档是否已经接入。";
  const sources = [...new Set(results.map(item => ragSourceLabel(item.source)))];
  const code = results.find(item => item.source === "code" && item.path);
  const missing = result.evidence_pack.missing_evidence || [];
  let text = `已从${sources.join("、")}中找到 ${results.length} 条相关依据。`;
  if (code) text += ` 最相关的代码位于 ${code.path}${code.start_line ? ` 第 ${code.start_line}–${code.end_line || code.start_line} 行` : ""}，已在左侧自动打开。`;
  if (missing.length) text += ` 当前仍缺少${missing.map(item => ragMissingRoleLabel(item.role)).join("、")}，因此系统不会把结果表述成已经完全验证的结论。`;
  return text;
}

function selectRagAnswerFacts(results) {
  const selected = [];
  const seenSources = new Set();
  results.forEach(item => {
    if (selected.length >= 4 || seenSources.has(item.source)) return;
    selected.push(item);
    seenSources.add(item.source);
  });
  results.forEach(item => {
    if (selected.length >= 4 || selected.includes(item)) return;
    selected.push(item);
  });
  return selected;
}

function ragAnswerExcerpt(value, limit = 150) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function ragSafeMetadataValue(value, fallback = "—") {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  if (/^(?:\/|[a-z]:[\\/]|file:\/\/)/i.test(text)) return "受限本地来源";
  return text.length > 160 ? `${text.slice(0, 157)}…` : text;
}

function renderRagAnswer(result, payload) {
  const pack = result.evidence_pack || {};
  const answer = result.answer || {};
  const mode = ragQueryMode(result);
  const scope = answer.applicable_scope || result.resolved_scope || pack.resolved_scope || {};
  const repositories = (scope.repositories || []).map(item => ({
    id:item.id,
    name:item.name,
  }));
  const verified = payload.results.filter(item => item.evidence_role === "verified");
  const supporting = payload.results.filter(item => item.evidence_role === "supporting");
  const counter = payload.results.filter(item => item.evidence_role === "counter");
  const citations = Object.entries(pack.citation_map || {});
  const sourceStatus = Object.entries(result.source_status || pack.source_status || {});
  const nextActions = [
    ...(result.next_actions || []),
    ...(answer.next_actions || []),
    ...(pack.next_actions || []),
    ...(result.interaction?.next_actions || []),
  ].filter((value, index, values) => value && values.indexOf(value) === index);
  const spans = pack.trace?.spans || [];
  const maxDuration = Math.max(1, ...spans.map(span => Number(span.duration_ms || 0)));
  const observations = verified.slice(0, 5);
  const answerFacts = selectRagAnswerFacts(payload.results);
  const scopeLabel = repositories.length === 1
    ? ragSafeMetadataValue(repositories[0].name || repositories[0].id)
    : (repositories.length ? `${repositories.length} 个代码仓库` : ragSafeMetadataValue(scope.project_id, "整个研发项目"));
  const clarification = result.interaction?.clarification || answer.clarification;
  const modeLabel = {
    clarification:"需要澄清范围后继续",
    partial:"PARTIAL · 仅覆盖已返回来源",
    refusal:"拒绝作答 · 可信条件不足",
    retrieval_only:"仅检索 · 未执行生成",
    grounded:"GROUNDED · 引用已校验",
  }[mode];
  const answerText = mode === "retrieval_only"
    ? (answer.text || buildRagReadableSummary(result, payload.results))
    : (answer.text || "系统没有返回可安全呈现的回答正文。");
  $("#rag-answer-content").innerHTML = `<div class="rag-answer-sheet">
    <div class="rag-answer-lead">
      <strong>${escapeHtml(modeLabel)}</strong>
      <h2>${escapeHtml(pack.question || ragWorkbenchState.lastQuestion)}</h2>
      <p>${escapeHtml(answerText)}</p>
      ${mode === "clarification" ? `<div class="rag-missing-list"><b>${escapeHtml(clarification?.questions?.[0] || "请补充项目实体、版本或时间范围")}</b><span>请在上方输入补充说明并再次提交。</span></div>` : ""}
      ${mode !== "clarification" && answerFacts.length ? `<div class="rag-answer-facts">${answerFacts.map(item => `<button type="button" data-rag-entity="${escapeHtml(item.entity_id)}"><span>${escapeHtml(ragSourceLabel(item.source))}</span><b>${escapeHtml(item.title)}</b><small>${escapeHtml(ragAnswerExcerpt(item.snippet))}</small></button>`).join("")}</div>` : ""}
    </div>
    <section class="rag-answer-section">
      <h3>本次查询覆盖</h3>
      <div class="rag-scope-card">
        <div><span>项目范围</span><b>${escapeHtml(scopeLabel)}</b></div>
        <div><span>版本范围</span><b>${escapeHtml(ragSafeMetadataValue(scope.branch || scope.as_of, "当前已发布范围"))} ${scope.commit ? `/ ${escapeHtml(shortCommit(scope.commit))}` : ""}</b></div>
        <div><span>直接事实</span><b>${verified.length} 条</b></div>
        <div><span>支持 / 反证</span><b>${supporting.length} / ${counter.length}</b></div>
      </div>
    </section>
    ${sourceStatus.length ? `<section class="rag-answer-section"><h3>来源状态与 Watermark</h3><div class="rag-observation-list">${sourceStatus.map(([source, item]) => `<div class="rag-observation"><b>${escapeHtml(ragSourceLabel(source))} · ${escapeHtml(item.status || "unknown")}</b><span>${Number(item.candidate_count || 0)} candidates · ${item.latency_ms == null ? "—" : `${Math.round(Number(item.latency_ms))} ms`} · ${escapeHtml(ragSafeMetadataValue(item.watermark, "未提供 watermark"))}</span></div>`).join("")}</div></section>` : ""}
    ${observations.length ? `<section class="rag-answer-section"><h3>最相关的实现与事实</h3><div class="rag-observation-list">${observations.map(item => `<button type="button" class="rag-observation" data-rag-entity="${escapeHtml(item.entity_id)}"><b>${escapeHtml(item.title)}</b><span>${escapeHtml(item.subtitle || item.locator)}</span></button>`).join("")}</div></section>` : ""}
    ${(pack.missing_evidence || []).length ? `<section class="rag-answer-section"><h3>尚未验证的部分</h3><ul class="rag-missing-list">${pack.missing_evidence.map(item => `<li><b>${escapeHtml(ragMissingRoleLabel(item.role))}</b>：项目中暂未找到满足要求的来源</li>`).join("")}</ul></section>` : ""}
    ${counter.length ? `<section class="rag-answer-section"><h3>反证与冲突</h3><div class="rag-observation-list">${counter.map(item => `<button type="button" class="rag-observation" data-rag-entity="${escapeHtml(item.entity_id)}"><b>${escapeHtml(item.title)}</b><span>${escapeHtml(ragAnswerExcerpt(item.snippet))}</span></button>`).join("")}</div></section>` : ""}
    ${citations.length ? `<section class="rag-answer-section"><h3>可定位引用</h3><div class="rag-citation-list">${citations.map(([key, item]) => `<button type="button" class="rag-citation" data-rag-citation="${escapeHtml(key)}">${escapeHtml(key)} · ${escapeHtml(ragSourceLabel(item.source))} · ${escapeHtml(shortCommit(item.version))}</button>`).join("")}</div></section>` : ""}
    ${answer.claim_verification ? `<section class="rag-answer-section"><h3>Claim Citation 校验</h3><div class="rag-scope-card"><div><span>校验状态</span><b>${answer.claim_verification.supported ? "通过" : "未完全通过"}</b></div><div><span>Claims</span><b>${Number(answer.claim_verification.supported_claim_count || 0)} / ${Number(answer.claim_verification.claim_count || 0)}</b></div></div></section>` : ""}
    ${nextActions.length ? `<section class="rag-answer-section"><h3>Next Actions</h3><ol class="rag-missing-list">${nextActions.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ol></section>` : ""}
    ${spans.length ? `<details class="rag-technical-details"><summary>查看检索过程与耗时</summary><div class="rag-trace-list">${spans.map(span => `<div class="rag-trace-row"><span>${escapeHtml(span.name)}</span><i style="--trace-width:${Math.max(3, Number(span.duration_ms || 0) / maxDuration * 100)}%"></i><b>${Number(span.duration_ms || 0).toFixed(1)} ms</b></div>`).join("")}</div></details>` : ""}
  </div>`;
  $$("[data-rag-entity]", $("#rag-answer-content")).forEach(button => button.addEventListener("click", () => {
    const item = payload.results.find(value => value.entity_id === button.dataset.ragEntity);
    if (!item) return;
    inspectEvidence(item, payload);
    if (item.source === "code") openCodeEvidenceInRag(item);
  }));
  $$("[data-rag-citation]", $("#rag-answer-content")).forEach(button => button.addEventListener("click", () => {
    const citation = pack.citation_map?.[button.dataset.ragCitation];
    const item = payload.results.find(value => value.entity_id === citation?.entity_id);
    if (!item) return;
    inspectEvidence(item, payload);
    if (item.source === "code") openCodeEvidenceInRag(item);
  }));
}

function renderSearchResults(payload) {
  const sourceCount = new Set(payload.results.map(item => item.source)).size;
  $("#search-summary").textContent = `${payload.total} 条依据 · ${sourceCount} 类来源`;
  $("#search-duration").textContent = `${Number(payload.trace.duration_ms || 0).toFixed(1)} MS`;
  const container = $("#search-results");
  if (!payload.results.length) {
    container.innerHTML = '<div class="rag-empty"><span>∅</span><b>整个项目中没有找到相关依据</b><small>可以换一种问法，或确认相关科研资产已经接入。</small></div>';
    $("#evidence-inspector").innerHTML = '<div class="rag-empty"><span>链</span><b>没有可展示的关系</b><small>当前结果已明确标记为证据不足。</small></div>';
    return;
  }
  container.innerHTML = payload.results.map((item, index) => `
    <article class="result-card ${ragWorkbenchState.selectedEvidenceId === item.entity_id ? "active" : ""}" data-rag-result="${index}">
      <span class="rank-badge source-${escapeHtml(item.source)}">${ragDomainGlyph(item.source)}</span>
      <div><div class="result-title"><strong>${escapeHtml(item.title)}</strong> <small>· ${escapeHtml(ragSourceLabel(item.source))}</small></div>
        <div class="locator">${escapeHtml(item.subtitle || item.locator)}</div>
        <div class="result-snippet">${escapeHtml(item.snippet)}</div>
      </div>
      <div class="score"><small>${escapeHtml(ragRoleLabel(item.evidence_role))}</small><em>${escapeHtml(ragSourceLabel(item.source))}</em></div>
    </article>`).join("");
  $$("[data-rag-result]", container).forEach(row => row.addEventListener("click", () => {
    const item = payload.results[Number(row.dataset.ragResult)];
    inspectEvidence(item, payload);
    if (item.source === "code") openCodeEvidenceInRag(item);
  }));
  inspectEvidence(payload.results[0], payload);
}

function inspectEvidence(item, payload, {activateTab = false} = {}) {
  ragWorkbenchState.selectedEvidenceId = item.entity_id;
  setRagSourcePane(item.source);
  $$("[data-rag-result]", $("#search-results")).forEach((row, index) => row.classList.toggle("active", payload.results[index]?.entity_id === item.entity_id));
  const itemRelations = (payload.evidence_pack?.relations || []).filter(edge => edge.source === item.entity_id || edge.target === item.entity_id);
  const relations = itemRelations.map(edge => `<div class="relation"><b>${escapeHtml(edge.predicate || edge.kind || "关联")}</b><div>${escapeHtml(edge.source === item.entity_id ? edge.target : edge.source)}</div><small>${escapeHtml(edge.derivation || "recorded")} · ${Number(edge.confidence || 0).toFixed(2)}</small></div>`).join("");
  $("#evidence-inspector").innerHTML = `
    <div class="panel-head"><strong>证据详情与关系</strong><span class="status-pill">${escapeHtml(ragRoleLabel(item.evidence_role))}</span></div>
    <div class="inspector-body">
      <div class="channel-tags">${(item.channels || []).map(channel => `<span class="channel-tag">${escapeHtml(channel)}</span>`).join("")}</div>
      <h3>${escapeHtml(item.title)}</h3>
      <div class="evidence-id">${escapeHtml(item.entity_id)}</div>
      <div class="detail-row"><span>来源</span><b>${escapeHtml(ragSourceLabel(item.source))}</b></div>
      <div class="detail-row"><span>原始位置</span><b>${escapeHtml(item.locator)}</b></div>
      <div class="detail-row"><span>版本</span><b>${escapeHtml(shortCommit(item.version))}</b></div>
      <div class="detail-row"><span>状态</span><b>${escapeHtml(statusLabel(item.status))}</b></div>
      <h4>直接关系 · ${itemRelations.length}</h4>
      <div class="rag-relation-summary">这些关系来自当前版本的证据图，用于说明本条依据如何连接到其他研发资产。</div>
      <div class="relation-list">${relations || '<div class="rag-empty compact"><b>当前没有直接关系</b><span>可继续展开完整关系路径</span></div>'}</div>
      <button class="button primary" id="open-result-source" style="width:100%;margin-top:13px">打开原始来源</button>
      <button class="button ghost" id="load-result-lineage" style="width:100%;margin-top:7px">展开完整关系路径</button>
      <div id="result-lineage" class="result-lineage"></div>
    </div>`;
  $("#open-result-source").addEventListener("click", () => openGlobalResult(item));
  $("#load-result-lineage").addEventListener("click", () => loadLineage(item.entity_id));
  if (activateTab) switchRagTab("relations");
}

async function loadLineage(entityId) {
  const container = $("#result-lineage");
  container.innerHTML = '<div class="rag-empty compact"><b>正在展开关系路径</b></div>';
  try {
    const graph = await api(`/v1/lineage?entity_id=${encodeURIComponent(entityId)}&depth=2`);
    container.innerHTML = `<div class="lineage-summary"><b>${graph.nodes.length}</b> 个实体 · <b>${graph.edges.length}</b> 条关系</div>${graph.edges.slice(0, 30).map(edge => `<div class="lineage-edge"><span>${escapeHtml(edge.predicate)}</span><small>${escapeHtml(edge.source)}<br>→ ${escapeHtml(edge.target)}</small></div>`).join("")}`;
  } catch (error) {
    container.innerHTML = `<div class="rag-empty compact"><span>${escapeHtml(error.message)}</span></div>`;
  }
}

async function openCodeEvidenceInRag(item) {
  const repositoryId = item.repository_id || ragWorkbenchState.currentRepositoryId;
  if (!repositoryId || !item.path) return;
  setRagSourcePane("code");
  ragWorkbenchState.openRepositories.clear();
  ragWorkbenchState.openRepositories.add(repositoryId);
  await ensureRagRepositoryFiles(repositoryId);
  await loadRagFile(repositoryId, item.path, {start:Number(item.start_line || 1), end:Number(item.end_line || item.start_line || 1)});
}

async function openGlobalResult(item) {
  if (item.source === "code" && item.path) {
    switchView("search");
    await openCodeEvidenceInRag(item);
    return;
  }
  if (item.source === "codex" && item.thread_id) {
    switchView("codex");
    await selectCodexThread(item.thread_id, item.entity_id);
    return;
  }
  if (item.source === "document") {
    switchView("documents");
    const claim = state.claims.find(value => value.id === item.entity_id);
    if (claim) {
      state.selectedDocument = claim.document_id;
      await selectClaim(claim.id);
    }
    return;
  }
  if (item.source === "experiment") {
    switchView("experiments");
    const run = state.runs.find(value => value.id === item.entity_id);
    if (run) {
      state.selectedExperiment = run.experiment_id;
      renderExperimentCenter();
    }
    return;
  }
  if (item.source === "workspace") {
    switchView("workspace");
    return;
  }
  showToast("该证据保留了稳定来源标识，可在关系链中继续核验");
}

async function openRepositoryInRag(repositoryId = state.selectedRepository) {
  switchView("search");
  if (repositoryId) {
    await syncRagRepository(repositoryId);
    const repository = state.repositories.find(item => item.id === repositoryId);
    if (repository) showToast(`已展开 ${repository.name}，提问仍默认检索整个项目`);
  }
  $('#evidence-search-form textarea[name="query"]').focus();
}

function installRagWorkbenchEvents() {
  if (ragWorkbenchState.eventsInstalled) return;
  ragWorkbenchState.eventsInstalled = true;
  $("#toggle-rag-scope").addEventListener("click", event => {
    const expanded = event.currentTarget.getAttribute("aria-expanded") === "true";
    event.currentTarget.setAttribute("aria-expanded", String(!expanded));
    $("#rag-advanced-scope").hidden = expanded;
  });
  $("#rag-repository").addEventListener("change", async event => {
    updateRagProjectScope();
    if (event.target.value) {
      ragWorkbenchState.openRepositories.add(event.target.value);
      await syncRagRepository(event.target.value);
    }
  });
  $("#search-source").addEventListener("change", updateRagProjectScope);
  $$("[data-rag-question]").forEach(button => button.addEventListener("click", () => {
    const form = $("#evidence-search-form");
    form.elements.query.value = button.dataset.ragQuestion;
    form.elements.intent.value = button.dataset.ragIntent || "";
    form.elements.query.focus();
  }));
  $$("[data-rag-source-tab]").forEach(button => button.addEventListener("click", () => setRagSourcePane(button.dataset.ragSourceTab)));
  $("#rag-file-filter").addEventListener("input", event => filterRagProjectFiles(event.target.value));
  $("#rag-file-collapse").addEventListener("click", () => {
    ragWorkbenchState.openRepositories.clear();
    $("#rag-file-list").querySelectorAll("details").forEach(detail => { detail.open = false; });
  });
  $("#rag-file-list").addEventListener("click", event => {
    const button = event.target.closest("[data-rag-file]");
    if (button) loadRagFile(button.dataset.ragFileRepository, button.dataset.ragFile);
  });
  $("#rag-file-list").addEventListener("toggle", async event => {
    const details = event.target.closest("[data-rag-tree-repository]");
    if (!details || event.target !== details) return;
    const repositoryId = details.dataset.ragTreeRepository;
    if (!details.open) {
      ragWorkbenchState.openRepositories.delete(repositoryId);
      return;
    }
    ragWorkbenchState.openRepositories.add(repositoryId);
    if (!ragWorkbenchState.repositoryFiles.has(repositoryId)) {
      details.querySelector(".rag-tree-repository-content").innerHTML = '<div class="rag-tree-loading">正在读取目录…</div>';
      try {
        await ensureRagRepositoryFiles(repositoryId);
        renderRagFileList($("#rag-file-filter").value);
      } catch (error) {
        showToast(`目录加载失败：${error.message}`);
      }
    }
  }, true);
  $("#rag-code-breadcrumb").addEventListener("click", event => {
    const button = event.target.closest("[data-rag-breadcrumb]");
    if (!button) return;
    $("#rag-file-filter").value = "";
    renderRagFileList();
    const target = [...$("#rag-file-list").querySelectorAll("[data-rag-directory]")]
      .find(detail => detail.dataset.ragDirectory === button.dataset.ragBreadcrumb);
    if (!target) return;
    let parent = target;
    while (parent) {
      if (parent.matches?.("details")) parent.open = true;
      parent = parent.parentElement?.closest?.("details");
    }
    target.scrollIntoView({behavior:"smooth", block:"center"});
  });
  $$("[data-rag-tab]").forEach(button => button.addEventListener("click", () => switchRagTab(button.dataset.ragTab)));
  $$("[data-rag-layout]").forEach(button => button.addEventListener("click", () => setRagLayout(button.dataset.ragLayout)));
  $("#rag-open-repository").addEventListener("click", () => {
    const repositoryId = ragWorkbenchState.currentFile?.repository_id || ragWorkbenchState.currentRepositoryId || state.selectedRepository;
    if (repositoryId) {
      state.selectedRepository = repositoryId;
      selectRepository(repositoryId);
    }
    switchView("repository");
  });
  const question = $('#evidence-search-form textarea[name="query"]');
  question.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      $("#evidence-search-form").requestSubmit();
    }
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || !ragWorkbenchState.queryController) return;
    event.preventDefault();
    ragWorkbenchState.queryAbortReason = "cancelled";
    ragWorkbenchState.queryController.abort();
  });
  const splitter = $("#rag-splitter");
  splitter.addEventListener("pointerdown", event => {
    if (window.matchMedia("(max-width: 1140px)").matches) return;
    event.preventDefault();
    splitter.setPointerCapture(event.pointerId);
    splitter.classList.add("dragging");
  });
  splitter.addEventListener("pointermove", event => {
    if (!splitter.hasPointerCapture(event.pointerId)) return;
    const bounds = $("#rag-workbench").getBoundingClientRect();
    setRagSplitRatio((event.clientX - bounds.left) / bounds.width * 100);
  });
  const finishDrag = event => {
    if (splitter.hasPointerCapture(event.pointerId)) splitter.releasePointerCapture(event.pointerId);
    splitter.classList.remove("dragging");
  };
  splitter.addEventListener("pointerup", finishDrag);
  splitter.addEventListener("pointercancel", finishDrag);
  splitter.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = Number((getComputedStyle($("#rag-workbench")).getPropertyValue("--rag-left") || "50").replace("%", "")) || 50;
    if (event.key === "Home") setRagSplitRatio(32);
    else if (event.key === "End") setRagSplitRatio(72);
    else setRagSplitRatio(current + (event.key === "ArrowRight" ? 2 : -2));
  });
  setRagLayout("balanced");
}
