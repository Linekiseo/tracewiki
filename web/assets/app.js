const state = {
  repositories: [],
  workflows: [],
  stats: {},
  selectedRepository: localStorage.getItem("rag_selected_repository"),
  files: [],
  filesRepositoryId: null,
  openCodeFolders: new Set(),
  currentFile: null,
  codexStats: {},
  codexSessions: [],
  codexConfig: {},
  selectedCodexThread: null,
  codexThread: null,
  selectedCodexThreads: new Set(),
  codexComparisons: [],
  workspace: null,
  workspaceIntelligence: null,
  topics: [],
  iterations: [],
  workItems: [],
  selectedTopic: null,
  selectedIteration: null,
  bindingStats: {},
  bindings: [],
  relations: [],
  bindingPage: 0,
  selectedBinding: null,
  experimentStats: {},
  experiments: [],
  runs: [],
  selectedExperiment: null,
  selectedRuns: new Set(),
  documentStats: {},
  documents: [],
  claims: [],
  selectedDocument: null,
  selectedDocumentDetail: null,
  selectedClaim: null,
  claimMatchCandidates: [],
  tableMetricCandidates: [],
  tableMetricAggregations: [],
  claimEvidenceOptions: [],
  driftAssessments: [],
  evaluationStats: {},
  evaluationCases: [],
  evaluationRuns: [],
  audits: [],
  rawStats: {},
  rawObjects: [],
  experimentSources: [],
  notebookRuns: [],
  gitHistory: [],
  codeRefs: null,
  selectedCodeRef: "HEAD",
  selectedWorkflow: null,
  bridgeStatus: null,
  codexExecutions: [],
  approvalRequests: [],
  pendingDelete: null,
  pollTimer: null,
  workspaceRefreshTimer: null,
  workspaceRefreshInFlight: false,
};

const BINDING_PAGE_SIZE = 15;

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
const shortCommit = value => value ? value.slice(0, 12) : "—";
const formatNumber = value => new Intl.NumberFormat("zh-CN").format(value || 0);
const formatTime = value => value ? new Intl.DateTimeFormat("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"}).format(new Date(value)) : "—";

async function api(path, options = {}) {
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  const token = localStorage.getItem("rag_api_token");
  if (token) headers.Authorization = `Bearer ${token}`;
  const aclRefs = localStorage.getItem("rag_acl_refs");
  if (aclRefs) headers["X-RAG-ACL-Refs"] = aclRefs;
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function switchView(name) {
  const aliases = {ingestion:"experiments", sources:"documents"};
  name = aliases[name] || name;
  if (!$(`#view-${name}`)) name = "workspace";
  document.body.dataset.currentView = name;
  $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === name));
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `view-${name}`));
  history.replaceState(null, "", `#${name}`);
  window.scrollTo({top: 0, behavior: "instant"});
  if (name === "graph" && typeof activateRagGraph === "function") activateRagGraph();
  if (name === "workspace" && window.ProjectNetworkWorkbench) {
    renderWorkspace();
    requestAnimationFrame(() => requestAnimationFrame(() => window.ProjectNetworkWorkbench.fitView()));
  }
}

function statusClass(status) {
  if (["ready", "completed", "confirmed", "active", "verified", "done"].includes(status)) return "";
  if (status === "failed") return "failed";
  if (["blocked", "rejected", "contradicted", "cancelled"].includes(status)) return "failed";
  return "indexing";
}

function statusLabel(status) {
  return ({ready:"可执行", indexing:"构建中", failed:"失败", completed:"完成", done:"完成", cancelled:"已取消", review:"待复核", awaiting_approval:"待批准", in_progress:"进行中", running:"运行中", queued:"排队中", active:"研究中", planned:"已规划", validating:"验证中", backlog:"待规划", blocked:"受阻", archived:"已归档", confirmed:"已确认", rejected:"已拒绝", unreviewed:"待复核", reported:"待验证", verified:"已验证", partially_supported:"部分支持", contradicted:"存在反证", potentially_stale:"可能失效", unknown_version:"历史不足", diverged:"分支分叉", impacted:"已受影响", insufficient_evidence:"证据不足", superseded:"已取代", draft:"草稿", valid:"有效", quarantined:"已隔离", tombstoned:"已删除", passed:"通过"})[status] || status;
}

async function refreshAll() {
  try {
    const [stats, repositories, workflows, codexStats, codexSessions, codexConfig, codexComparisons, workspace, workspaceIntelligence, topics, iterations, workItems, bindingStats, bindings, relations, experimentStats, experiments, runs, documentStats, documents, claims, driftAssessments, evaluationStats, evaluationCases, evaluationRuns, audits, rawStats, rawObjects, experimentSources, notebookRuns, bridgeStatus, codexExecutions, approvalRequests] = await Promise.all([
      api("/v1/stats"), api("/v1/repositories"), api("/v1/ingestion/workflows?kind=repository&limit=12"),
      api("/v1/codex/stats"), api("/v1/codex/sessions?limit=200"), api("/v1/codex/config"),
      api("/v1/codex/comparisons?limit=30"),
      api("/v1/workspace/dashboard"), api("/v1/workspace/intelligence").catch(() => null), api("/v1/research/topics"), api("/v1/research/iterations"), api("/v1/research/work-items"),
      api("/v1/bindings/stats"), api("/v1/bindings/candidates?review_status=unreviewed"),
      api("/v1/relations?limit=500"),
      api("/v1/experiments/stats"), api("/v1/experiments"), api("/v1/experiments/runs"),
      api("/v1/documents/stats"), api("/v1/documents"), api("/v1/documents/claims"), api("/v1/drift/assessments"),
      api("/v1/evaluation/stats"), api("/v1/evaluation/cases"), api("/v1/evaluation/runs"), api("/v1/audit/events?limit=100"),
      api("/v1/ingestion/raw/stats"), api("/v1/ingestion/raw?limit=100"), api("/v1/experiments/sources"), api("/v1/notebooks/runs"),
      api("/v1/codex-bridge/status"), api("/v1/codex-bridge/executions"), api("/v1/codex-bridge/approvals")
    ]);
    state.stats = stats;
    state.repositories = repositories;
    if (state.selectedRepository && !repositories.some(item => item.id === state.selectedRepository)) state.selectedRepository = null;
    state.workflows = workflows;
    state.codexStats = codexStats;
    state.codexSessions = codexSessions;
    state.codexConfig = codexConfig;
    state.codexComparisons = codexComparisons;
    state.selectedCodexThreads = new Set([...state.selectedCodexThreads].filter(id => codexSessions.some(item => item.thread_id === id)));
    state.workspace = workspace;
    state.workspaceIntelligence = workspaceIntelligence;
    state.topics = topics;
    state.iterations = iterations;
    state.workItems = workItems;
    state.bindingStats = bindingStats;
    state.bindings = bindings;
    state.relations = relations;
    state.experimentStats = experimentStats;
    state.experiments = experiments;
    state.runs = runs;
    state.documentStats = documentStats;
    state.documents = documents;
    state.claims = claims;
    state.driftAssessments = driftAssessments;
    state.evaluationStats = evaluationStats; state.evaluationCases = evaluationCases; state.evaluationRuns = evaluationRuns; state.audits = audits;
    state.rawStats = rawStats; state.rawObjects = rawObjects; state.experimentSources = experimentSources; state.notebookRuns = notebookRuns;
    state.bridgeStatus = bridgeStatus; state.codexExecutions = codexExecutions; state.approvalRequests = approvalRequests;
    if (state.selectedTopic && !topics.some(item => item.id === state.selectedTopic)) state.selectedTopic = null;
    if (!state.selectedTopic && topics.length) state.selectedTopic = topics[0].id;
    renderStats();
    renderRepositories();
    renderWorkflows();
    renderRepositorySelectors();
    renderCodexStats();
    renderCodexSessions();
    renderCodexComparisonHistory();
    applyCodexDefaults();
    renderWorkspace();
    renderCodexBridge();
    renderBindingStats();
    renderBindings();
    renderExperimentCenter();
    renderDocumentCenter();
    renderDrift();
    renderGovernance();
    renderSources();
    renderExperimentIngestion();
    if (repositories.length) selectRepository(state.selectedRepository || repositories[0].id);
    refreshRagWorkbench();
    const active = workflows.find(item => ["queued", "running"].includes(item.status));
    renderWorkflowTrack(active || workflows[0]);
    if (!state.selectedCodexThread && codexSessions.length) selectCodexThread(codexSessions[0].thread_id);
  } catch (error) {
    showToast(`加载失败：${error.message}`);
  }
}

async function refreshGovernance() {
  try {
    const [stats, cases, runs, audits, workspace] = await Promise.all([api("/v1/evaluation/stats"),api("/v1/evaluation/cases"),api("/v1/evaluation/runs"),api("/v1/audit/events?limit=100"),api("/v1/workspace/dashboard")]);
    state.evaluationStats = stats; state.evaluationCases = cases; state.evaluationRuns = runs; state.audits = audits; state.workspace = workspace; renderGovernance();
  } catch (error) { showToast(`治理数据加载失败：${error.message}`); }
}

function renderGovernance() {
  const stats = state.evaluationStats;
  const values = [stats.cases, `${Math.round((stats.avg_pass_rate || 0) * 100)}%`, stats.queries, Number(stats.query_latency_ms || 0).toFixed(1)];
  $$("#evaluation-metric-grid .metric strong").forEach((node,index) => node.textContent = values[index] ?? "—");
  $("#eval-case-count").textContent = `${state.evaluationCases.length} 项`;
  $("#eval-case-list").innerHTML = state.evaluationCases.length ? state.evaluationCases.map(item => `<article class="eval-case"><div><b>${escapeHtml(item.display_key)}</b><span class="status-pill ${item.enabled ? "" : "neutral"}">${item.enabled ? "ENABLED" : "DISABLED"}</span></div><strong>${escapeHtml(item.name)}</strong><p>${escapeHtml(item.question)}</p><small>${(item.expected_sources || []).map(source => escapeHtml(source)).join(" · ") || "NO SOURCE ASSERTION"}${item.required_version ? ` · VERSION ${escapeHtml(shortCommit(item.required_version))}` : ""}</small></article>`).join("") : '<div class="empty-state compact"><p>尚未创建评测问题</p></div>';
  $("#eval-run-count").textContent = `${state.evaluationRuns.length} 次`;
  $("#eval-run-list").innerHTML = state.evaluationRuns.length ? state.evaluationRuns.map(item => `<article class="eval-run"><div><b>${escapeHtml(item.display_key)}</b><span class="status-pill ${item.summary?.pass_rate === 1 ? "" : "indexing"}">${Math.round((item.summary?.pass_rate || 0) * 100)}% PASS</span></div><div class="eval-score-grid"><span><strong>${item.summary?.entity_recall?.toFixed(2) || "—"}</strong>ENTITY</span><span><strong>${item.summary?.evidence_path_recall?.toFixed(2) || "—"}</strong>PATH</span><span><strong>${item.summary?.commit_accuracy?.toFixed(2) || "—"}</strong>COMMIT</span><span><strong>${item.summary?.version_accuracy?.toFixed(2) || "—"}</strong>VERSION</span><span><strong>${item.summary?.unauthorized_leakage?.toFixed(2) || "0.00"}</strong>ACL LEAK</span><span><strong>${Number(item.summary?.latency_ms || 0).toFixed(1)}</strong>MS</span></div><small>${item.case_count} CASES · WRONG VERSION ${Number(item.summary?.wrong_version_rate || 0).toFixed(2)} · ${formatTime(item.completed_at)}</small></article>`).join("") : '<div class="empty-state compact"><p>尚未运行回归</p></div>';
  const project = state.workspace?.project;
  if (project) {
    const form = $("#project-settings-form"); form.elements.name.value = project.name; form.elements.classification.value = project.classification; form.elements.default_limit.value = project.settings?.retrieval?.default_limit || 20; form.elements.require_relation_review.checked = project.settings?.governance?.require_relation_review !== false;
  }
  $("#audit-table").innerHTML = state.audits.length ? state.audits.map(item => `<tr><td>${formatTime(item.created_at)}</td><td>${escapeHtml(item.actor)}</td><td><span class="mono-label">${escapeHtml(item.action)}</span></td><td>${escapeHtml(item.resource_type)}</td><td class="audit-resource" title="${escapeHtml(item.resource_id)}">${escapeHtml(item.resource_id)}</td><td><span class="mono-label">${escapeHtml((item.trace_id || "—").slice(0, 12))}</span></td></tr>`).join("") : '<tr><td colspan="6" class="empty-row">暂无审计记录</td></tr>';
}

async function submitEvaluationCase(event) {
  event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); const errorBox = $("#eval-case-error"); errorBox.hidden = true;
  try { const item = await api("/v1/evaluation/cases", {method:"POST",body:JSON.stringify({name:String(form.get("name") || ""),question:String(form.get("question") || ""),expected_sources:[form.get("expected_source")],expected_entity_ids:form.get("expected_entity_id") ? [form.get("expected_entity_id")] : [],required_version:form.get("required_version") || null})}); $("#eval-case-dialog").close(); formElement.reset(); showToast(`${item.display_key} 已加入回归集`); await refreshGovernance(); }
  catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
}

async function runEvaluation() {
  if (!state.evaluationCases.length) { showToast("请先创建至少一个 Golden Question"); return; }
  const button = $("#run-evaluation"); button.disabled = true; button.textContent = "运行中…";
  try { const result = await api("/v1/evaluation/runs", {method:"POST",body:"{}"}); showToast(`${result.display_key} 完成，通过率 ${Math.round(result.summary.pass_rate * 100)}%`); await refreshGovernance(); }
  catch (error) { showToast(`回归失败：${error.message}`); }
  finally { button.disabled = false; button.textContent = "▶ 运行回归"; }
}

async function saveProjectSettings(event) {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  try { await api("/v1/projects/by-id?project_id=project-rag", {method:"PATCH",body:JSON.stringify({name:form.get("name"),classification:form.get("classification"),settings:{retrieval:{default_limit:Number(form.get("default_limit"))},governance:{require_relation_review:form.get("require_relation_review") === "on"}}})}); showToast("项目设置已保存并记录审计"); await refreshGovernance(); }
  catch (error) { showToast(`设置保存失败：${error.message}`); }
}

function renderDrift() {
  const items = state.driftAssessments;
  const valid = items.filter(item => item.status === "valid").length;
  const stale = items.filter(item => item.status !== "valid").length;
  const average = items.length ? items.reduce((sum, item) => sum + item.risk_score, 0) / items.length : 0;
  const values = [items.length, valid, stale, average.toFixed(2)];
  $$("#drift-summary article strong").forEach((node, index) => node.textContent = values[index]);
  $("#drift-count").textContent = `${items.length} 项`;
  const table = $("#drift-table");
  if (!items.length) { table.innerHTML = '<tr><td colspan="8" class="empty-row">尚未执行漂移扫描</td></tr>'; return; }
  table.innerHTML = items.map(item => `<tr title="${escapeHtml(item.reason)}"><td class="repo-cell"><strong>${escapeHtml(item.claim_key)}</strong><small>${escapeHtml((item.metadata?.changed_paths || []).slice(0, 3).join(", ") || item.content.slice(0, 70))}</small></td><td>${escapeHtml(item.run_key)}</td><td>${escapeHtml(item.repository_name)}</td><td><span class="mono-label">${escapeHtml(shortCommit(item.original_commit))}</span></td><td><span class="mono-label">${escapeHtml(shortCommit(item.current_commit))}</span></td><td><span class="status-pill ${item.status !== "valid" ? "indexing" : ""}">${statusLabel(item.status)}</span></td><td><b class="risk-score ${item.risk_score > 0 ? "warn" : ""}">${Number(item.risk_score).toFixed(2)}</b></td><td>${formatTime(item.assessed_at)}</td></tr>`).join("");
}

async function scanDrift() {
  const button = $("#scan-drift"); button.disabled = true; button.textContent = "扫描中…";
  try { const result = await api("/v1/drift/scan", {method:"POST",body:"{}"}); state.driftAssessments = await api("/v1/drift/assessments"); renderDrift(); await refreshDocuments(); showToast(`版本检查完成：评估 ${result.count} 条结论证据链`); }
  catch (error) { showToast(`漂移扫描失败：${error.message}`); }
  finally { button.disabled = false; button.textContent = "↻ 扫描版本漂移"; }
}

async function refreshDocuments() {
  try {
    const [stats, documents, claims] = await Promise.all([
      api("/v1/documents/stats"), api("/v1/documents"), api("/v1/documents/claims")
    ]);
    state.documentStats = stats; state.documents = documents; state.claims = claims;
    if (state.selectedDocument && !documents.some(item => item.id === state.selectedDocument)) { state.selectedDocument = null; state.selectedDocumentDetail = null; }
    if (state.selectedClaim && !claims.some(item => item.id === state.selectedClaim)) state.selectedClaim = null;
    renderDocumentCenter();
  } catch (error) { showToast(`文档中心加载失败：${error.message}`); }
}

function renderDocumentCenter() {
  const values = [state.documentStats.documents, state.documentStats.claims, state.documentStats.verified, state.documentStats.at_risk];
  $$("#document-metric-grid .metric strong").forEach((node, index) => node.textContent = formatNumber(values[index]));
  renderDocuments(); renderClaims(); renderDocumentIterationOptions();
}

function renderDocuments() {
  $("#document-count").textContent = `${state.documents.length} 份`;
  const list = $("#document-list");
  if (!state.documents.length) { list.innerHTML = '<div class="empty-state compact"><span class="empty-symbol">∅</span><p>尚未接入科研文档</p></div>'; return; }
  list.innerHTML = state.documents.map(item => `<button class="document-item ${state.selectedDocument === item.id ? "active" : ""}" data-document-id="${escapeHtml(item.id)}"><span><b>${escapeHtml(item.display_key)}</b><i>${escapeHtml(item.version)}</i></span><strong>${escapeHtml(item.title)}</strong><small>${item.section_count} 个章节 · ${item.claim_count} 条结论</small><div><em>${item.verified_claims} 条已验证</em><time>${formatTime(item.updated_at)}</time></div></button>`).join("");
  $$('[data-document-id]', list).forEach(button => button.addEventListener("click", () => selectDocument(button.dataset.documentId)));
}

async function selectDocument(documentId) {
  state.selectedDocument = state.selectedDocument === documentId ? null : documentId;
  state.selectedDocumentDetail = null; state.selectedClaim = null;
  renderDocuments(); renderClaims(); resetClaimDetail();
  if (!state.selectedDocument) return;
  try { state.selectedDocumentDetail = await api(`/v1/documents/by-id?document_id=${encodeURIComponent(documentId)}`); renderClaims(); }
  catch (error) { showToast(`文档加载失败：${error.message}`); }
}

function renderClaims() {
  const document = state.documents.find(item => item.id === state.selectedDocument);
  const claims = state.claims.filter(item => !document || item.document_id === document.id);
  $("#claim-panel-title").textContent = document ? document.title : "文档结论";
  $("#claim-panel-subtitle").textContent = state.selectedDocumentDetail ? `${state.selectedDocumentDetail.sections.length} Sections · ${document.version}` : "选择文档查看结构与结论";
  $("#claim-count").textContent = `${claims.length} 条`;
  const list = $("#claim-list");
  if (!claims.length) { list.innerHTML = '<div class="empty-state"><span class="empty-symbol">∅</span><h3>尚无可验证结论</h3><p>导入文档后，系统会提取显式结论与数值性陈述。</p></div>'; return; }
  const sectionMap = new Map((state.selectedDocumentDetail?.sections || []).map(item => [item.id, item.title]));
  list.innerHTML = claims.map(item => `<article class="claim-card ${state.selectedClaim === item.id ? "active" : ""}" data-claim-id="${escapeHtml(item.id)}"><div class="claim-card-head"><span><b>${escapeHtml(item.display_key)}</b><i>${escapeHtml(item.claim_type)}</i></span><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span></div><p>${escapeHtml(item.content)}</p><div class="claim-card-foot"><span>${escapeHtml(sectionMap.get(item.section_id) || item.document_title)} · ${escapeHtml(item.document_version)}</span><b>${item.evidence_count || 0} 条证据</b></div></article>`).join("");
  $$('[data-claim-id]', list).forEach(card => card.addEventListener("click", () => selectClaim(card.dataset.claimId)));
}

async function selectClaim(claimId) {
  state.selectedClaim = claimId; renderClaims();
  $("#claim-detail-panel").innerHTML = '<div class="empty-state"><span class="empty-symbol">···</span><p>正在装载验证证据</p></div>';
  try {
    const [claim, candidates, tableMetricCandidates, tableMetricAggregations, options] = await Promise.all([
      api(`/v1/documents/claims/by-id?claim_id=${encodeURIComponent(claimId)}`),
      api(`/v1/documents/claims/match-candidates?claim_id=${encodeURIComponent(claimId)}`),
      api(`/v1/documents/tables/metric-candidates?claim_id=${encodeURIComponent(claimId)}`),
      api(`/v1/documents/tables/aggregations?claim_id=${encodeURIComponent(claimId)}`),
      api("/v1/documents/evidence-options?evidence_type=experiment_run&limit=100"),
    ]);
    state.claimMatchCandidates = candidates;
    state.tableMetricCandidates = tableMetricCandidates;
    state.tableMetricAggregations = tableMetricAggregations;
    state.claimEvidenceOptions = options;
    renderClaimDetail(claim);
  }
  catch (error) { showToast(`Claim 加载失败：${error.message}`); }
}

function resetClaimDetail() {
  $("#claim-detail-panel").innerHTML = '<div class="panel-head"><strong>结论验证</strong><span class="status-pill neutral">未选择</span></div><div class="empty-state"><span class="empty-symbol">证</span><p>选择结论查看来源与证据</p></div>';
}

function renderTableAggregationPanel() {
  const grouped = new Map();
  state.tableMetricCandidates.forEach(item => {
    const key = `${item.table_cell_id}\u001f${item.metric_name}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(item);
  });
  const groups = [...grouped.values()].filter(items => items.length >= 2);
  const saved = state.tableMetricAggregations;
  return `<h4>多 Run 聚合 · ${saved.length}</h4><div class="table-aggregation-list">${groups.map((items,index) => `<article data-aggregation-group="${index}"><div><b>${escapeHtml(items[0].metric_name)} · ${escapeHtml(items[0].cell_value)}</b><span>${items.length} RUNS</span></div><div class="aggregation-metric-choices">${items.map(item => `<label><input type="checkbox" value="${escapeHtml(item.metric_id)}" checked /><span>${escapeHtml(item.run_key)} · ${escapeHtml(item.metric_value)}</span></label>`).join("")}</div><footer><select data-aggregation-function><option value="mean">Mean</option><option value="median">Median</option><option value="sum">Sum</option><option value="min">Min</option><option value="max">Max</option></select><button class="text-button" data-create-aggregation="${index}">记录聚合证据</button></footer></article>`).join("") || '<p class="subtle-copy">同一表格数值至少匹配两个 Run 后可记录聚合函数、样本数和方差。</p>'}${saved.map(item => `<article class="saved-aggregation"><div><b>${escapeHtml(item.display_key)} · ${escapeHtml(item.metric_name)}</b><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span></div><p>${escapeHtml(item.aggregation_function)}(${item.sample_count}) = ${escapeHtml(item.computed_value)} · reported ${escapeHtml(item.reported_value)} · variance ${escapeHtml(item.variance)}</p></article>`).join("")}</div>`;
}

function renderClaimDetail(claim) {
  const validation = claim.metadata?.validation;
  const optionMap = new Map(state.claimEvidenceOptions.map(item => [item.id, item]));
  const pendingCandidates = state.claimMatchCandidates.filter(item => item.review_status === "unreviewed");
  const pendingTableMetrics = state.tableMetricCandidates.filter(item => item.review_status === "unreviewed");
  $("#claim-detail-panel").innerHTML = `<div class="panel-head"><div><strong>${escapeHtml(claim.display_key)}</strong><small>${escapeHtml(claim.document_title)} · ${escapeHtml(claim.document_version)}</small></div><span class="status-pill ${statusClass(claim.status)}">${statusLabel(claim.status)}</span></div><div class="claim-detail-body"><blockquote>${escapeHtml(claim.content)}</blockquote><div class="claim-locator">${escapeHtml(claim.source_locator)}</div><h4>验证检查</h4><div class="claim-checks">${validation?.checks?.map(item => `<div class="${item.passed ? "pass" : "fail"}"><i>${item.passed ? "✓" : "!"}</i><span>${escapeHtml(item.check)}</span></div>`).join("") || '<p>尚未绑定可验证证据</p>'}</div><h4>Claim → Run 候选 · ${pendingCandidates.length}</h4><div class="claim-match-list">${pendingCandidates.map(item => `<article><div><b>${escapeHtml(item.run_key)} · ${escapeHtml(item.run_name)}</b><span>${Math.round(Number(item.score) * 100)}%</span></div><p>${Object.entries(item.signals || {}).map(([key,value]) => `${escapeHtml(key)}: ${escapeHtml(Array.isArray(value) ? value.join(", ") : value)}`).join(" · ") || "规则匹配"}</p><footer><button class="text-button danger" data-match-review="rejected" data-candidate-id="${escapeHtml(item.id)}">拒绝</button><button class="text-button" data-match-review="confirmed" data-candidate-id="${escapeHtml(item.id)}">确认支持</button></footer></article>`).join("") || '<p class="subtle-copy">没有待复核的 Run 候选。</p>'}</div><h4>TableCell → Metric 候选 · ${pendingTableMetrics.length}</h4><div class="claim-match-list table-metric-match-list">${pendingTableMetrics.map(item => `<article><div><b>${escapeHtml(item.table_title)} · ${escapeHtml(item.cell_value)}</b><span>${Math.round(Number(item.score) * 100)}%</span></div><p>${escapeHtml(item.metric_name)} = ${escapeHtml(item.metric_value)} · ${escapeHtml(item.run_key)} · ${escapeHtml(item.run_name)}</p><small>${escapeHtml(item.cell_locator)}</small><footer><button class="text-button danger" data-table-metric-review="rejected" data-candidate-id="${escapeHtml(item.id)}">拒绝</button><button class="text-button" data-table-metric-review="confirmed" data-candidate-id="${escapeHtml(item.id)}">确认数值链</button></footer></article>`).join("") || '<p class="subtle-copy">没有待复核的表格数值链。</p>'}</div>${renderTableAggregationPanel()}<h4>证据绑定 · ${claim.evidence.length}</h4><div class="claim-evidence-list">${claim.evidence.map(item => { const option = optionMap.get(item.evidence_entity_id); return `<div><span class="status-pill ${item.relationship === "refutes" ? "failed" : ""}">${escapeHtml(item.relationship)}</span><b>${escapeHtml(option?.title || item.evidence_type)}</b><button class="evidence-unlink" data-evidence-id="${escapeHtml(item.id)}" title="解除绑定">×</button><small>${escapeHtml(option?.subtitle || item.evidence_entity_id)}</small></div>`; }).join("") || '<p>暂无证据</p>'}</div><details class="claim-history"><summary>绑定历史 · ${claim.evidence_events?.length || 0}</summary>${(claim.evidence_events || []).map(item => `<div><span>${item.action === "linked" ? "＋" : "−"}</span><b>${escapeHtml(item.action)}</b><small>${escapeHtml(item.actor)} · ${formatTime(item.created_at)}</small></div>`).join("") || '<p>暂无历史</p>'}</details><div class="claim-bind-form"><select id="claim-evidence-type"><option value="experiment_run">Experiment Run</option><option value="metric">Metric</option><option value="artifact">Artifact</option><option value="table_cell">Table Cell</option><option value="figure">Figure</option><option value="metric_aggregation">Metric Aggregation</option><option value="code">代码 / Symbol</option><option value="codex">Codex 会话证据</option><option value="document">文档版本</option></select><select id="claim-relationship"><option value="supports">支持</option><option value="qualifies">限定</option><option value="refutes">反证</option></select><input id="claim-evidence-query" placeholder="筛选名称、路径或数值，回车检索" /><select id="claim-evidence-entity"></select><textarea id="claim-evidence-note" rows="2" placeholder="记录证据为何支持、限定或反驳此 Claim"></textarea><button class="button primary" id="bind-claim-evidence">绑定并重新验证</button></div></div>`;
  renderClaimEvidenceOptions();
  $$('[data-match-review]', $("#claim-detail-panel")).forEach(button => button.addEventListener("click", () => reviewClaimMatch(button.dataset.candidateId, button.dataset.matchReview)));
  $$('[data-table-metric-review]', $("#claim-detail-panel")).forEach(button => button.addEventListener("click", () => reviewTableMetricMatch(button.dataset.candidateId, button.dataset.tableMetricReview)));
  $$('[data-create-aggregation]', $("#claim-detail-panel")).forEach(button => button.addEventListener("click", () => createTableMetricAggregation(claim.id, Number(button.dataset.createAggregation))));
  $$('[data-evidence-id]', $("#claim-detail-panel")).forEach(button => button.addEventListener("click", () => unlinkClaimEvidence(claim.id, button.dataset.evidenceId)));
  $("#claim-evidence-type").addEventListener("change", event => loadClaimEvidenceOptions(event.target.value, $("#claim-evidence-query").value));
  $("#claim-evidence-query").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); loadClaimEvidenceOptions($("#claim-evidence-type").value, event.target.value); } });
  $("#bind-claim-evidence").addEventListener("click", () => bindClaimEvidence(claim.id));
}

function renderClaimEvidenceOptions() {
  const select = $("#claim-evidence-entity");
  if (!select) return;
  select.innerHTML = state.claimEvidenceOptions.length ? `<option value="">选择具体证据</option>${state.claimEvidenceOptions.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.title)} · ${escapeHtml(item.subtitle || item.entity_type)}</option>`).join("")}` : '<option value="">没有匹配证据</option>';
}

async function loadClaimEvidenceOptions(type, query = "") {
  try {
    state.claimEvidenceOptions = await api(`/v1/documents/evidence-options?evidence_type=${encodeURIComponent(type)}&query=${encodeURIComponent(query)}&limit=100`);
    renderClaimEvidenceOptions();
  } catch (error) { showToast(`证据目录加载失败：${error.message}`); }
}

async function bindClaimEvidence(claimId) {
  const entityId = $("#claim-evidence-entity").value;
  if (!entityId) { showToast("请选择一条具体证据"); return; }
  try {
    const result = await api("/v1/documents/claims/evidence", {method:"POST",body:JSON.stringify({claim_id:claimId,evidence_entity_id:entityId,evidence_type:$("#claim-evidence-type").value,relationship:$("#claim-relationship").value,note:$("#claim-evidence-note").value})});
    showToast(`Claim 状态已更新为 ${statusLabel(result.validation.status)}`);
    await refreshDocuments(); state.selectedClaim = claimId; await selectClaim(claimId);
  } catch (error) { showToast(`证据绑定失败：${error.message}`); }
}

async function reviewClaimMatch(candidateId, decision) {
  try {
    const result = await api(`/v1/documents/claims/match-candidates/review?candidate_id=${encodeURIComponent(candidateId)}`, {method:"POST",body:JSON.stringify({decision,relationship:"supports",reviewer:"RAG Core",note:decision === "confirmed" ? "在 Claim 验证工作台确认候选" : "在 Claim 验证工作台拒绝候选"})});
    showToast(decision === "confirmed" ? `候选已确认，Claim 为 ${statusLabel(result.evidence.validation.status)}` : "候选已拒绝");
    await refreshDocuments(); await selectClaim(state.selectedClaim);
  } catch (error) { showToast(`候选复核失败：${error.message}`); }
}

async function reviewTableMetricMatch(candidateId, decision) {
  try {
    const result = await api(`/v1/documents/tables/metric-candidates/review?candidate_id=${encodeURIComponent(candidateId)}`, {method:"POST",body:JSON.stringify({decision,reviewer:"RAG Core",note:decision === "confirmed" ? "在 Claim 验证工作台确认 TableCell → Metric 数值链" : "在 Claim 验证工作台拒绝表格数值链"})});
    const validation = result.claim_evidence?.validation;
    showToast(decision === "confirmed" ? `数值链已确认${validation ? `，Claim 为 ${statusLabel(validation.status)}` : ""}` : "表格数值链已拒绝");
    await refreshDocuments(); await selectClaim(state.selectedClaim);
  } catch (error) { showToast(`表格数值链复核失败：${error.message}`); }
}

async function createTableMetricAggregation(claimId, groupIndex) {
  const groups = new Map();
  state.tableMetricCandidates.forEach(item => {
    const key = `${item.table_cell_id}\u001f${item.metric_name}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  const group = [...groups.values()].filter(items => items.length >= 2)[groupIndex];
  const panel = $(`[data-aggregation-group="${groupIndex}"]`);
  if (!group || !panel) return;
  const metricIds = $$('input[type="checkbox"]', panel).filter(input => input.checked).map(input => input.value);
  if (metricIds.length < 2) { showToast("聚合至少需要保留两个 Run 指标"); return; }
  try {
    const result = await api("/v1/documents/tables/aggregations", {method:"POST",body:JSON.stringify({table_cell_id:group[0].table_cell_id,claim_id:claimId,metric_ids:metricIds,aggregation_function:$('[data-aggregation-function]', panel).value,actor:"RAG Core",note:"在 Claim 验证工作台记录多 Run 聚合证据"})});
    showToast(`${result.aggregation.display_key} 已记录，状态 ${statusLabel(result.aggregation.status)}`);
    await refreshDocuments(); await selectClaim(claimId);
  } catch (error) { showToast(`聚合证据创建失败：${error.message}`); }
}

async function unlinkClaimEvidence(claimId, evidenceId) {
  try {
    const result = await api("/v1/documents/claims/evidence/unlink", {method:"POST",body:JSON.stringify({claim_id:claimId,evidence_id:evidenceId,note:"从 Claim 验证工作台解除绑定"})});
    showToast(`证据已解除，Claim 为 ${statusLabel(result.validation.status)}`);
    await refreshDocuments(); await selectClaim(claimId);
  } catch (error) { showToast(`解除绑定失败：${error.message}`); }
}

async function scanClaimMatches() {
  const button = $("#scan-claim-matches");
  button.disabled = true; button.textContent = "扫描中…";
  try {
    const [result, tableResult] = await Promise.all([
      api("/v1/documents/claims/match-scan", {method:"POST"}),
      api("/v1/documents/tables/metric-scan", {method:"POST"}),
    ]);
    showToast(`已扫描 ${result.claims} 个 Claim，生成 ${result.candidates.length} 个 Run 候选与 ${tableResult.candidates.length} 个表格数值候选`);
    if (state.selectedClaim) await selectClaim(state.selectedClaim);
  } catch (error) { showToast(`候选扫描失败：${error.message}`); }
  finally { button.disabled = false; button.textContent = "↻ 扫描匹配候选"; }
}

function renderDocumentIterationOptions() {
  $("#document-iteration").innerHTML = `<option value="">不关联迭代</option>${state.iterations.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.display_key)} · ${escapeHtml(item.title)}</option>`).join("")}`;
}

async function submitDocument(event) {
  if (window.DocumentImporter) return window.DocumentImporter.submit(event);
  event.preventDefault();
  showToast("文档导入器尚未加载，请刷新页面后重试");
}

async function refreshExperiments() {
  try {
    const [stats, experiments, runs] = await Promise.all([
      api("/v1/experiments/stats"), api("/v1/experiments"), api("/v1/experiments/runs")
    ]);
    state.experimentStats = stats;
    state.experiments = experiments;
    state.runs = runs;
    if (state.selectedExperiment && !experiments.some(item => item.id === state.selectedExperiment)) state.selectedExperiment = null;
    state.selectedRuns = new Set([...state.selectedRuns].filter(id => runs.some(run => run.id === id)));
    renderExperimentCenter();
  } catch (error) {
    showToast(`实验中心加载失败：${error.message}`);
  }
}

function renderExperimentCenter() {
  const values = [state.experimentStats.experiments, state.experimentStats.runs, state.experimentStats.version_bound_runs, state.experimentStats.metrics];
  $$("#experiment-metric-grid .metric strong").forEach((node, index) => node.textContent = formatNumber(values[index]));
  renderExperiments();
  renderRuns();
  renderExperimentOptions();
}

function renderExperiments() {
  $("#experiment-count").textContent = `${state.experiments.length} EXP`;
  const list = $("#experiment-list");
  if (!state.experiments.length) {
    list.innerHTML = '<div class="empty-state compact"><span class="empty-symbol">∅</span><p>创建实验后开始记录版本化 Run</p></div>';
    return;
  }
  list.innerHTML = state.experiments.map(item => `<button class="experiment-item ${state.selectedExperiment === item.id ? "active" : ""}" data-experiment-id="${escapeHtml(item.id)}"><span><b>${escapeHtml(item.display_key)}</b><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span></span><strong>${escapeHtml(item.title)}</strong><small>${item.completed_runs || 0} / ${item.run_count} COMPLETED RUNS</small><p>${escapeHtml(item.hypothesis || item.objective || "尚未填写实验假设")}</p></button>`).join("");
  $$('[data-experiment-id]', list).forEach(button => button.addEventListener("click", () => {
    state.selectedExperiment = state.selectedExperiment === button.dataset.experimentId ? null : button.dataset.experimentId;
    state.selectedRuns.clear();
    $("#comparison-result").innerHTML = "";
    renderExperiments();
    renderRuns();
    renderExperimentOptions();
  }));
}

function renderRuns() {
  const experiment = state.experiments.find(item => item.id === state.selectedExperiment);
  const runs = state.runs.filter(item => !experiment || item.experiment_id === experiment.id);
  $("#run-panel-title").textContent = experiment ? experiment.title : "实验 Runs";
  $("#run-panel-subtitle").textContent = `${runs.length} Runs · 选择两个或更多 Run 进行受控对照`;
  const table = $("#run-table");
  if (!runs.length) {
    table.innerHTML = '<tr><td colspan="8" class="empty-row">当前实验尚无 Run</td></tr>';
    $("#compare-runs").disabled = true;
    return;
  }
  table.innerHTML = runs.map(item => `<tr data-run-id="${escapeHtml(item.id)}"><td><input class="run-checkbox" type="checkbox" ${state.selectedRuns.has(item.id) ? "checked" : ""} aria-label="选择 ${escapeHtml(item.display_key)}" /></td><td class="repo-cell"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.display_key)}</small></td><td><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span></td><td><span class="mono-label">${escapeHtml(shortCommit(item.commit_sha))}</span>${item.commit_entity_id ? '<i class="version-bound">BOUND</i>' : ''}</td><td>${escapeHtml(item.dataset_version || "—")}</td><td><span class="config-count">${Object.keys(item.config || {}).length} keys</span></td><td>${item.metric_count}</td><td>${formatTime(item.completed_at)}</td></tr>`).join("");
  $$('[data-run-id]', table).forEach(row => row.querySelector("input").addEventListener("change", event => {
    if (event.target.checked) state.selectedRuns.add(row.dataset.runId); else state.selectedRuns.delete(row.dataset.runId);
    $("#compare-runs").disabled = state.selectedRuns.size < 2;
  }));
  $("#compare-runs").disabled = state.selectedRuns.size < 2;
}

function renderExperimentOptions() {
  const experimentSelect = $("#run-experiment");
  experimentSelect.innerHTML = state.experiments.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.display_key)} · ${escapeHtml(item.title)}</option>`).join("");
  if (state.selectedExperiment) experimentSelect.value = state.selectedExperiment;
  const iterationSelect = $("#experiment-iteration");
  iterationSelect.innerHTML = `<option value="">不关联迭代</option>${state.iterations.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.display_key)} · ${escapeHtml(item.title)}</option>`).join("")}`;
  const repoSelect = $("#run-repository");
  repoSelect.innerHTML = `<option value="">未绑定仓库</option>${state.repositories.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(shortCommit(item.head_commit))}</option>`).join("")}`;
}

async function submitExperiment(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const errorBox = $("#experiment-error"); errorBox.hidden = true;
  try {
    const experiment = await api("/v1/experiments", {method:"POST", body:JSON.stringify({iteration_id:form.get("iteration_id") || null,title:String(form.get("title") || "").trim(),objective:String(form.get("objective") || "").trim(),hypothesis:String(form.get("hypothesis") || "").trim(),status:"running"})});
    state.selectedExperiment = experiment.id;
    $("#experiment-dialog").close(); formElement.reset();
    showToast(`实验 ${experiment.display_key} 已创建`); await refreshExperiments(); await refreshWorkspace();
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
}

async function submitRun(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const errorBox = $("#run-error"); errorBox.hidden = true;
  let config = {};
  try { config = String(form.get("config") || "").trim() ? JSON.parse(form.get("config")) : {}; }
  catch { errorBox.textContent = "配置必须是有效 JSON"; errorBox.hidden = false; return; }
  const metricName = String(form.get("metric_name") || "").trim();
  const metricValue = String(form.get("metric_value") || "").trim();
  const payload = {experiment_id:form.get("experiment_id"),name:String(form.get("name") || "Experiment run"),repository_id:form.get("repository_id") || null,commit_sha:form.get("commit_sha") || null,dataset_id:form.get("dataset_id") || null,dataset_version:form.get("dataset_version") || null,config,status:"completed",metrics:metricName && metricValue !== "" ? [{name:metricName,value:Number(metricValue)}] : []};
  try {
    const run = await api("/v1/experiments/runs", {method:"POST", body:JSON.stringify(payload)});
    state.selectedExperiment = run.experiment_id;
    $("#run-dialog").close(); formElement.reset();
    showToast(`Run ${run.display_key} 已记录${run.commit_entity_id ? "并绑定 Commit" : ""}`); await refreshExperiments();
  } catch (error) { errorBox.textContent = error.message; errorBox.hidden = false; }
}

async function compareRuns() {
  const runIds = [...state.selectedRuns];
  try {
    const comparison = await api("/v1/experiments/comparisons", {method:"POST", body:JSON.stringify({run_ids:runIds,baseline_run_id:runIds[0]})});
    renderComparison(comparison);
    showToast(`对照 ${comparison.display_key} 已保存`);
  } catch (error) { showToast(`对照失败：${error.message}`); }
}

function renderComparison(comparison) {
  const controls = comparison.result.controls;
  const metrics = comparison.result.metrics;
  $("#comparison-result").innerHTML = `<div class="comparison-head"><div><strong>${escapeHtml(comparison.display_key)} · ${escapeHtml(comparison.name)}</strong><small>${comparison.result.version_complete ? "所有 Run 已绑定代码版本" : "存在未验证 Commit 的 Run"}</small></div><span class="status-pill ${controls.every(item => item.consistent) ? "" : "indexing"}">${controls.every(item => item.consistent) ? "控制变量一致" : "控制变量有差异"}</span></div><div class="comparison-body"><div class="control-strip">${controls.map(item => `<span class="${item.consistent ? "pass" : "warn"}"><b>${escapeHtml(item.field)}</b>${item.consistent ? "一致" : "不一致"}</span>`).join("")}</div><table><thead><tr><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Delta</th></tr></thead><tbody>${metrics.flatMap(item => item.candidates.map(candidate => `<tr><td>${escapeHtml(item.name)}${item.split ? ` / ${escapeHtml(item.split)}` : ""}</td><td>${item.baseline}</td><td>${candidate.value}</td><td class="metric-delta ${candidate.delta >= 0 ? "up" : "down"}">${candidate.delta >= 0 ? "+" : ""}${candidate.delta.toFixed(4)}</td></tr>`)).join("") || '<tr><td colspan="4" class="empty-row">没有同名指标可比较</td></tr>'}</tbody></table><div class="config-diffs">${comparison.result.config_differences.map(item => `<span><b>${escapeHtml(item.key)}</b>${escapeHtml(JSON.stringify(item.baseline))} → ${escapeHtml(JSON.stringify(item.candidates))}</span>`).join("") || "配置一致"}</div></div>`;
  $("#comparison-result").scrollIntoView({behavior:"smooth", block:"nearest"});
}

async function refreshBindings() {
  const status = $("#binding-status-filter").value;
  try {
    const [stats, candidates] = await Promise.all([
      api("/v1/bindings/stats"),
      api(`/v1/bindings/candidates${status ? `?review_status=${encodeURIComponent(status)}` : ""}`),
    ]);
    state.bindingStats = stats;
    state.bindings = candidates;
    state.bindingPage = Math.min(
      state.bindingPage,
      Math.max(0, Math.ceil(candidates.length / BINDING_PAGE_SIZE) - 1),
    );
    if (state.selectedBinding && !candidates.some(item => item.id === state.selectedBinding)) state.selectedBinding = null;
    renderBindingStats();
    renderBindings();
    if (state.selectedBinding) selectBinding(state.selectedBinding);
  } catch (error) {
    showToast(`绑定队列加载失败：${error.message}`);
  }
}

function renderBindingStats() {
  const values = [state.bindingStats.total, state.bindingStats.pending, state.bindingStats.high_confidence, state.bindingStats.confirmed];
  $$("#binding-metric-grid .metric strong").forEach((node, index) => node.textContent = formatNumber(values[index]));
}

function renderBindings() {
  const list = $("#binding-list");
  const pager = $("#binding-pager");
  $("#binding-count").textContent = `${state.bindings.length} ITEMS`;
  if (!state.bindings.length) {
    list.innerHTML = '<div class="empty-state compact"><span class="empty-symbol">∅</span><p>当前筛选条件下没有绑定候选</p></div>';
    pager.hidden = true;
    if (!state.selectedBinding) resetBindingReview();
    return;
  }
  const pageCount = Math.ceil(state.bindings.length / BINDING_PAGE_SIZE);
  state.bindingPage = Math.min(state.bindingPage, pageCount - 1);
  const start = state.bindingPage * BINDING_PAGE_SIZE;
  const pageItems = state.bindings.slice(start, start + BINDING_PAGE_SIZE);
  list.innerHTML = pageItems.map(item => `
    <button class="binding-item ${item.id === state.selectedBinding ? "active" : ""}" data-binding-id="${escapeHtml(item.id)}">
      <span class="binding-item-head"><b>${escapeHtml(item.source_item_type)} → FileVersion</b><span class="status-pill ${statusClass(item.review_status)}">${statusLabel(item.review_status)}</span></span>
      <strong>${escapeHtml(item.changed_path)}</strong>
      <small>${escapeHtml(item.repository_name)} · ${escapeHtml(shortCommit(item.target_commit_sha))}</small>
      <span class="binding-item-foot"><i>${escapeHtml(item.derivation)}</i><b>${Number(item.confidence).toFixed(2)}</b></span>
    </button>`).join("");
  pager.hidden = pageCount <= 1;
  $("#binding-page-summary").textContent = `${start + 1}–${Math.min(start + BINDING_PAGE_SIZE, state.bindings.length)} / ${state.bindings.length}`;
  $('[data-binding-page="previous"]', pager).disabled = state.bindingPage === 0;
  $('[data-binding-page="next"]', pager).disabled = state.bindingPage >= pageCount - 1;
  $$('[data-binding-id]', list).forEach(button => button.addEventListener("click", () => selectBinding(button.dataset.bindingId)));
}

function resetBindingReview() {
  $("#binding-review-panel").innerHTML = '<div class="panel-head"><div><strong>关系复核</strong><small>比较会话变更与当前代码实体</small></div><span class="status-pill neutral">未选择</span></div><div class="empty-state"><span class="empty-symbol">绑</span><h3>选择一个候选关系</h3><p>复核源事件、目标文件、匹配信号和版本上下文。</p></div>';
}

async function selectBinding(bindingId) {
  state.selectedBinding = bindingId;
  renderBindings();
  const panel = $("#binding-review-panel");
  panel.innerHTML = '<div class="empty-state"><span class="empty-symbol">···</span><p>正在装载源事件与目标代码</p></div>';
  try {
    const item = await api(`/v1/bindings/by-id?binding_id=${encodeURIComponent(bindingId)}`);
    renderBindingReview(item);
  } catch (error) {
    panel.innerHTML = `<div class="empty-state"><h3>候选加载失败</h3><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function renderBindingReview(item) {
  const signals = item.signals || {};
  const canReview = item.review_status === "unreviewed";
  $("#binding-review-panel").innerHTML = `
    <div class="panel-head binding-review-head"><div><strong>${escapeHtml(item.changed_path)}</strong><small>${escapeHtml(item.source_title)}</small></div><span class="status-pill ${statusClass(item.review_status)}">${statusLabel(item.review_status)}</span></div>
    <div class="binding-review-body">
      <div class="binding-compare">
        <section class="binding-side source"><div class="binding-side-title"><span class="domain-badge">会</span><div><b>Codex ${escapeHtml(item.source_item_type)}</b><small>${escapeHtml(item.source?.locator || item.source_entity_id)}</small></div></div><pre>${escapeHtml(item.source?.content || "源事件不可用")}</pre></section>
        <section class="binding-side target"><div class="binding-side-title"><span class="domain-badge">码</span><div><b>${escapeHtml(item.repository_name)} / ${escapeHtml(item.target_path)}</b><small>Commit ${escapeHtml(shortCommit(item.target_commit_sha))} · ${item.target_symbol_ids.length} Symbols</small></div></div><pre>${escapeHtml((item.target?.content || "目标文件不可用").slice(0, 5000))}</pre></section>
      </div>
      <div class="binding-signal-grid">
        <div><span>Derivation</span><b>${escapeHtml(item.derivation)}</b></div><div><span>Confidence</span><b>${Number(item.confidence).toFixed(2)}</b></div><div><span>Workspace</span><b>${signals.cwd ? "matched" : "unknown"}</b></div><div><span>Version status</span><b>context only</b></div>
      </div>
      <div class="binding-caveat"><b>证据边界</b><span>当前候选仅证明会话变更路径可映射到此 FileVersion；Commit 是查询时的版本上下文，不自动证明 Patch 已提交。</span></div>
      ${canReview ? `<div class="binding-review-action"><textarea id="binding-review-note" rows="2" placeholder="记录确认依据或拒绝原因（可选）"></textarea><div><button class="button ghost binding-reject" data-binding-decision="rejected">拒绝</button><button class="button primary" data-binding-decision="confirmed">确认关系</button></div></div>` : `<div class="reviewed-note"><b>${escapeHtml(item.reviewed_by || "已复核")}</b><span>${escapeHtml(item.review_note || "未填写复核说明")}</span></div>`}
    </div>`;
  $$('[data-binding-decision]', $("#binding-review-panel")).forEach(button => button.addEventListener("click", () => reviewBinding(item.id, button.dataset.bindingDecision)));
}

async function scanBindings() {
  const button = $("#scan-bindings");
  button.disabled = true;
  button.textContent = "扫描中…";
  try {
    const result = await api("/v1/bindings/scan", {method:"POST", body:"{}"});
    showToast(`扫描完成：${result.counts.candidates} 个候选，${result.counts.unmatched} 条路径未匹配`);
    $("#binding-status-filter").value = "unreviewed";
    await refreshBindings();
  } catch (error) {
    showToast(`扫描失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "↻ 扫描绑定候选";
  }
}

async function reviewBinding(bindingId, decision) {
  const note = $("#binding-review-note")?.value || "";
  try {
    await api(`/v1/bindings/review?binding_id=${encodeURIComponent(bindingId)}`, {method:"POST", body:JSON.stringify({decision, note})});
    showToast(decision === "confirmed" ? "关系已确认并写入证据图" : "候选已拒绝");
    state.selectedBinding = null;
    await refreshBindings();
  } catch (error) {
    showToast(`复核失败：${error.message}`);
  }
}

async function refreshWorkspace({silent = false} = {}) {
  if (state.workspaceRefreshInFlight) return;
  state.workspaceRefreshInFlight = true;
  try {
    const [
      workspace,
      topics,
      iterations,
      workItems,
      relations,
      repositories,
      codexSessions,
      experiments,
      runs,
      documents,
      claims,
      bindingStats,
      codexExecutions,
    ] = await Promise.all([
      api("/v1/workspace/dashboard"),
      api("/v1/research/topics"),
      api("/v1/research/iterations"),
      api("/v1/research/work-items"),
      api("/v1/relations?limit=500"),
      api("/v1/repositories"),
      api("/v1/codex/sessions?limit=200"),
      api("/v1/experiments"),
      api("/v1/experiments/runs"),
      api("/v1/documents"),
      api("/v1/documents/claims"),
      api("/v1/bindings/stats"),
      api("/v1/codex-bridge/executions"),
    ]);
    state.workspace = workspace;
    state.topics = topics;
    state.iterations = iterations;
    state.workItems = workItems;
    state.relations = relations;
    state.repositories = repositories;
    state.codexSessions = codexSessions;
    state.experiments = experiments;
    state.runs = runs;
    state.documents = documents;
    state.claims = claims;
    state.bindingStats = bindingStats;
    state.codexExecutions = codexExecutions;
    if (state.selectedTopic && !topics.some(item => item.id === state.selectedTopic)) state.selectedTopic = null;
    if (!state.selectedTopic && topics.length) state.selectedTopic = topics[0].id;
    if (state.selectedIteration && !iterations.some(item => item.id === state.selectedIteration)) state.selectedIteration = null;
    const intelligenceParams = new URLSearchParams({project_id: "project-rag"});
    if (state.selectedTopic) intelligenceParams.set("topic_id", state.selectedTopic);
    if (state.selectedIteration) intelligenceParams.set("iteration_id", state.selectedIteration);
    state.workspaceIntelligence = await api(`/v1/workspace/intelligence?${intelligenceParams}`)
      .catch(() => state.workspaceIntelligence);
    renderWorkspace();
  } catch (error) {
    if (!silent) showToast(`工作区加载失败：${error.message}`);
  } finally {
    state.workspaceRefreshInFlight = false;
  }
}

function installWorkspaceLiveRefresh() {
  if (state.workspaceRefreshTimer) window.clearInterval(state.workspaceRefreshTimer);
  state.workspaceRefreshTimer = window.setInterval(() => {
    if (!document.hidden && document.body.dataset.currentView === "workspace") {
      refreshWorkspace({silent: true});
    }
  }, 15000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && document.body.dataset.currentView === "workspace") {
      refreshWorkspace({silent: true});
    }
  });
}

async function refreshWorkspaceIntelligence() {
  try {
    const params = new URLSearchParams({project_id: "project-rag"});
    if (state.selectedTopic) params.set("topic_id", state.selectedTopic);
    if (state.selectedIteration) params.set("iteration_id", state.selectedIteration);
    state.workspaceIntelligence = await api(`/v1/workspace/intelligence?${params}`);
    renderWorkspace();
  } catch (error) {
    showToast(`项目判断更新失败：${error.message}`);
  }
}

function renderWorkspace() {
  if (!state.workspace || !window.ProjectNetworkWorkbench) return;
  if (state.selectedTopic && !state.topics.some(item => item.id === state.selectedTopic)) state.selectedTopic = null;
  if (!state.selectedTopic && state.topics.length) state.selectedTopic = state.topics[0].id;
  const topicIterations = state.iterations.filter(item => item.topic_id === state.selectedTopic);
  if (!topicIterations.some(item => item.id === state.selectedIteration)) {
    state.selectedIteration = topicIterations.find(item => ["active", "validating"].includes(item.status))?.id || topicIterations[0]?.id || null;
  }
  window.ProjectNetworkWorkbench.render({
    state,
    labels: {statusLabel, statusClass},
    actions: {
      selectTopic(topicId) {
        state.selectedTopic = topicId;
        state.selectedIteration = null;
        renderWorkspace();
        refreshWorkspaceIntelligence();
        renderIterationTopicOptions();
        renderCodexWorkItemOptions();
      },
      openSource(view, source) {
        switchView(view);
        if (view === "repository" && source?.id) selectRepository(source.id);
      },
      editTopic() {
        openTopicEditor();
      },
      createTopic() {
        $("#topic-form").reset();
        $("#topic-dialog").showModal();
      },
      editIteration(iterationId) {
        openIterationEditor(iterationId);
      },
      editWorkItem(workItemId) {
        openWorkItemEditor(workItemId);
      },
      createIteration() {
        if (!state.selectedTopic) {
          showToast("请先创建或选择研究主题");
          $("#topic-dialog").showModal();
          return;
        }
        renderIterationTopicOptions();
        $("#iteration-dialog").showModal();
      },
      createWorkItem() {
        if (!state.selectedTopic) {
          showToast("请先创建或选择研究主题");
          $("#topic-dialog").showModal();
          return;
        }
        $("#work-item-form").reset();
        $("#work-item-dialog").showModal();
      },
      createExperiment() {
        $("#experiment-form").reset();
        renderExperimentOptions();
        $("#experiment-dialog").showModal();
      },
      createDocument() {
        if (window.DocumentImporter) window.DocumentImporter.open();
        else {
          renderDocumentIterationOptions();
          $("#document-dialog").showModal();
        }
      },
      startCodex(objective = "") {
        const currentWork = (state.workItems || []).find(item => item.topic_id === state.selectedTopic && item.id === state.selectedWorkItem)
          || (state.workItems || []).find(item => item.topic_id === state.selectedTopic && ["ready", "running", "review"].includes(item.status));
        if (!objective && currentWork) {
          openCodexExecution(currentWork.id);
          return;
        }
        const form = $("#work-item-form");
        form.elements.title.value = objective;
        $("#work-item-dialog").showModal();
      },
      refresh() {
        refreshWorkspace();
      },
      loadCodexThread(threadId) {
        return api(`/v1/codex/sessions/${encodeURIComponent(threadId)}`);
      },
    },
  });
  renderIterationTopicOptions();
  renderCodexWorkItemOptions();
}

function renderTopics(filter = "") {
  const query = filter.trim().toLowerCase();
  const topics = state.topics.filter(item => !query || `${item.title} ${item.problem_statement} ${item.objective}`.toLowerCase().includes(query));
  $("#topic-count").textContent = `${topics.length} TOPICS`;
  const list = $("#topic-list");
  if (!topics.length) {
    list.innerHTML = '<div class="empty-state compact"><p>尚无匹配主题</p></div>';
    return;
  }
  list.innerHTML = topics.map(item => `
    <button class="topic-item ${state.selectedTopic === item.id ? "active" : ""}" data-workspace-topic="${escapeHtml(item.id)}">
      <span class="topic-item-head"><span class="priority-mark p${item.priority}">P${item.priority}</span><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span></span>
      <strong>${escapeHtml(item.title)}</strong>
      <small>${item.iteration_count} 个迭代 · 更新于 ${formatTime(item.updated_at)}</small>
      <span class="topic-tags">${(item.tags || []).slice(0, 3).map(tag => `<i>${escapeHtml(tag)}</i>`).join("")}</span>
    </button>`).join("");
  $$('[data-workspace-topic]', list).forEach(button => button.addEventListener("click", () => {
    state.selectedTopic = button.dataset.workspaceTopic;
    state.selectedIteration = null;
    renderTopics($("#topic-filter").value);
    renderSelectedTopic();
    renderIterations();
    renderWorkItems();
    renderWorkspaceEvidence();
    renderIterationTopicOptions();
    renderCodexWorkItemOptions();
  }));
}

function renderSelectedTopic() {
  const topic = state.topics.find(item => item.id === state.selectedTopic);
  $("#workspace-topic-empty").hidden = Boolean(topic);
  $("#workspace-topic-detail").hidden = !topic;
  $("#open-iteration").disabled = !topic;
  $("#open-work-item").disabled = !topic;
  if (!topic) return;
  $("#workspace-topic-status").textContent = statusLabel(topic.status);
  $("#workspace-topic-status").className = `status-pill ${statusClass(topic.status)}`;
  $("#workspace-topic-title").textContent = topic.title;
  $("#workspace-topic-problem").textContent = topic.problem_statement || topic.objective || "尚未补充问题陈述；可进入编辑详情完善。";
  $("#workspace-topic-meta").innerHTML = [
    `目标：${escapeHtml(topic.objective || "待完善")}`,
    `负责人：${escapeHtml(topic.owner)}`,
    `优先级：P${topic.priority}`,
    `更新：${formatTime(topic.updated_at)}`,
  ].map(value => `<span>${value}</span>`).join("");
}

function renderIterations() {
  const selected = state.topics.find(item => item.id === state.selectedTopic);
  const iterations = state.iterations.filter(item => selected && item.topic_id === selected.id);
  if (!state.selectedIteration || !iterations.some(item => item.id === state.selectedIteration)) {
    state.selectedIteration = iterations.find(item => ["active", "validating"].includes(item.status))?.id || iterations[0]?.id || null;
  }
  $("#iteration-count").textContent = `${iterations.length} ITERATIONS`;
  const list = $("#iteration-list");
  if (!iterations.length) {
    list.innerHTML = '<div class="empty-state"><h3>这个主题还没有研究迭代</h3><p>创建一轮可推进、可验证并可挂接证据的工作单元。</p></div>';
    return;
  }
  list.innerHTML = iterations.map(item => {
    const next = ({planned:"active", active:"validating", validating:"completed"})[item.status];
    return `<article class="iteration-card ${state.selectedIteration === item.id ? "selected" : ""}" data-select-iteration="${escapeHtml(item.id)}">
      <div class="iteration-card-main">
        <div class="iteration-card-meta"><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span><span>${escapeHtml(item.display_key)}</span><span>${item.evidence_count} 条证据</span></div>
        <h3>${escapeHtml(item.title)}</h3>
        <p>${escapeHtml(item.goal || item.hypothesis || "尚未填写本轮目标")}</p>
        <div class="iteration-foot"><span>证据 <b>${item.evidence_count}</b></span><span>负责人 <b>${escapeHtml(item.owner)}</b></span><span>目标 <b>${formatDate(item.target_at)}</b></span></div>
      </div>
      <div class="iteration-progress"><strong>${item.progress}%</strong><div><i style="width:${item.progress}%"></i></div><button class="text-button" data-edit-iteration="${escapeHtml(item.id)}">查看 / 编辑</button>${next ? `<button class="text-button" data-advance-iteration="${escapeHtml(item.id)}" data-next-status="${next}">${statusLabel(next)}</button>` : ""}</div>
    </article>`;
  }).join("");
  $$("[data-select-iteration]", list).forEach(card => card.addEventListener("click", event => {
    if (event.target.closest("button")) return;
    state.selectedIteration = card.dataset.selectIteration;
    renderIterations();
    renderWorkItems();
  }));
  $$("[data-edit-iteration]", list).forEach(button => button.addEventListener("click", () => openIterationEditor(button.dataset.editIteration)));
  $$('[data-advance-iteration]', list).forEach(button => button.addEventListener("click", () => advanceIteration(button.dataset.advanceIteration, button.dataset.nextStatus)));
}

function renderWorkItems() {
  const items = (state.workItems || []).filter(item => item.topic_id === state.selectedTopic);
  $("#work-item-count").textContent = `${items.length} TASKS`;
  const list = $("#work-item-list");
  if (!items.length) {
    list.innerHTML = '<div class="empty-state"><h3>尚无执行任务</h3><p>把研究迭代拆成可执行并可回传验证结果的工作。</p></div>';
    return;
  }
  const kindLabels = {research:"研究", development:"开发", experiment:"实验", analysis:"分析", review:"复核"};
  list.innerHTML = items.map(item => `<article class="work-item-card">
    <div>
      <div class="work-item-meta"><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span><span>${kindLabels[item.kind] || item.kind}</span><span>${escapeHtml(item.iteration_title || "未绑定迭代")}</span></div>
      <h3>${escapeHtml(item.title)}</h3>
      <p>${escapeHtml(item.objective || "尚未补充任务目标与验收条件")}</p>
    </div>
    <div class="work-item-actions">
      <button class="button ghost compact-button" data-edit-work-item="${escapeHtml(item.id)}">详情</button>
      <button class="button primary compact-button" data-handoff-work-item="${escapeHtml(item.id)}"><i class="ph ph-terminal-window"></i>交给 Codex</button>
    </div>
  </article>`).join("");
  $$("[data-edit-work-item]", list).forEach(button => button.addEventListener("click", () => openWorkItemEditor(button.dataset.editWorkItem)));
  $$("[data-handoff-work-item]", list).forEach(button => button.addEventListener("click", () => openCodexExecution(button.dataset.handoffWorkItem)));
}

function renderWorkspaceEvidence() {
  const iterations = state.iterations.filter(item => item.topic_id === state.selectedTopic);
  const linked = iterations.reduce((sum, item) => sum + Number(item.evidence_count || 0), 0);
  $("#workspace-evidence-count").textContent = `${linked} LINKS`;
  const container = $("#workspace-evidence-summary");
  if (!linked) {
    container.innerHTML = '<div class="empty-state"><h3>尚无关联证据</h3><p>代码、会话、实验和文档证据将在迭代下汇总。</p></div>';
    return;
  }
  container.innerHTML = iterations.filter(item => item.evidence_count).map(item => `<article class="work-item-card"><div><div class="work-item-meta"><span>${escapeHtml(item.display_key)}</span><span>${item.evidence_count} LINKS</span></div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.summary || item.hypothesis || "进入证据工作台核对来源")}</p></div><button class="button ghost compact-button" data-source-view="search">核对证据</button></article>`).join("");
  $$('[data-source-view]', container).forEach(button => button.addEventListener("click", () => switchView(button.dataset.sourceView)));
}

function renderSourceHealth(stats) {
  const sources = [
    {view:"repository", label:"代码仓库", value:stats.repositories, meta:"Repository"},
    {view:"codex", label:"Codex 会话", value:stats.sessions, meta:"Thread"},
    {view:"experiments", label:"实验与数据", value:state.experiments.length, meta:"Experiment"},
    {view:"documents", label:"科研文档", value:state.documents.length, meta:"Document"},
  ];
  $("#workspace-source-health").innerHTML = sources.map(item => `<button data-source-view="${item.view}"><span><i></i><b>${item.label}</b><small>${item.meta}</small></span><strong>${formatNumber(item.value)}</strong></button>`).join("");
  $$('[data-source-view]', $("#workspace-source-health")).forEach(button => button.addEventListener("click", () => switchView(button.dataset.sourceView)));
}

function renderWorkspaceActivity(activity = []) {
  const container = $("#workspace-activity");
  if (!activity.length) {
    container.innerHTML = '<div class="empty-state compact"><p>暂无活动记录</p></div>';
    return;
  }
  const labels = {"topic.created":"创建主题", "topic.updated":"更新主题", "topic.deleted":"删除主题", "iteration.created":"创建迭代", "iteration.updated":"推进迭代", "iteration.deleted":"删除迭代", "work_item.created":"创建任务", "work_item.updated":"更新任务", "work_item.transitioned":"更新任务状态", "work_item.deleted":"删除任务", "iteration.evidence_linked":"关联证据", "iteration.evidence_unlinked":"移除证据", "relation.created":"建立关系", "relation.reviewed":"复核关系", "project.updated":"更新项目"};
  container.innerHTML = activity.map(item => `<div class="activity-item"><i></i><div><strong>${escapeHtml(labels[item.action] || item.action)}</strong><small>${escapeHtml(item.resource_type)} · ${escapeHtml(item.actor)}</small></div><time>${formatTime(item.created_at)}</time></div>`).join("");
}

function renderIterationTopicOptions() {
  const input = $("#iteration-topic");
  if (input) input.value = state.selectedTopic || "";
}

const formatDate = value => value ? new Intl.DateTimeFormat("zh-CN", {year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(value)) : "未设置";

async function submitTopic(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const errorBox = $("#topic-error");
  errorBox.hidden = true;
  const payload = {
    title: String(form.get("title") || "").trim(),
  };
  try {
    const topic = await api("/v1/research/topics", {method:"POST", body:JSON.stringify(payload)});
    state.selectedTopic = topic.id;
    $("#topic-dialog").close();
    formElement.reset();
    showToast(`研究主题 ${topic.display_key} 已创建`);
    await refreshWorkspace();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

async function submitIteration(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const errorBox = $("#iteration-error");
  errorBox.hidden = true;
  const payload = {
    topic_id: form.get("topic_id"),
    title: String(form.get("title") || "").trim(),
  };
  try {
    const iteration = await api("/v1/research/iterations", {method:"POST", body:JSON.stringify(payload)});
    state.selectedTopic = iteration.topic_id;
    $("#iteration-dialog").close();
    formElement.reset();
    showToast(`研究迭代 ${iteration.display_key} 已创建`);
    await refreshWorkspace();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

function openTopicEditor() {
  const topic = state.topics.find(item => item.id === state.selectedTopic);
  if (!topic) return;
  const form = $("#topic-edit-form");
  Object.entries({
    topic_id: topic.id,
    title: topic.title,
    problem_statement: topic.problem_statement,
    objective: topic.objective,
    status: topic.status,
    priority: topic.priority,
    owner: topic.owner,
    tags: (topic.tags || []).join(", "),
  }).forEach(([name, value]) => { form.elements[name].value = value ?? ""; });
  $("#topic-edit-dialog").showModal();
}

async function submitTopicEdit(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const errorBox = $("#topic-edit-error");
  errorBox.hidden = true;
  const topicId = form.get("topic_id");
  const payload = {
    title: String(form.get("title") || "").trim(),
    problem_statement: String(form.get("problem_statement") || "").trim(),
    objective: String(form.get("objective") || "").trim(),
    status: form.get("status"),
    priority: Number(form.get("priority")),
    owner: String(form.get("owner") || "").trim(),
    tags: String(form.get("tags") || "").split(",").map(value => value.trim()).filter(Boolean),
  };
  try {
    await api(`/v1/research/topics/by-id?topic_id=${encodeURIComponent(topicId)}`, {method:"PATCH", body:JSON.stringify(payload)});
    $("#topic-edit-dialog").close();
    showToast("研究主题已更新");
    await refreshWorkspace();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

function openIterationEditor(iterationId) {
  const item = state.iterations.find(value => value.id === iterationId);
  if (!item) return;
  const form = $("#iteration-edit-form");
  ["iteration_id", "title", "goal", "hypothesis", "status", "progress", "owner", "target_at", "summary"].forEach(name => {
    form.elements[name].value = name === "iteration_id" ? item.id : item[name] ?? "";
  });
  $("#iteration-edit-dialog").showModal();
}

async function submitIterationEdit(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const errorBox = $("#iteration-edit-error");
  errorBox.hidden = true;
  const iterationId = form.get("iteration_id");
  const payload = {
    title: String(form.get("title") || "").trim(),
    goal: String(form.get("goal") || "").trim(),
    hypothesis: String(form.get("hypothesis") || "").trim(),
    status: form.get("status"),
    progress: Number(form.get("progress") || 0),
    owner: String(form.get("owner") || "").trim(),
    target_at: form.get("target_at") || null,
    summary: String(form.get("summary") || "").trim(),
  };
  try {
    await api(`/v1/research/iterations/by-id?iteration_id=${encodeURIComponent(iterationId)}`, {method:"PATCH", body:JSON.stringify(payload)});
    $("#iteration-edit-dialog").close();
    showToast("研究迭代已更新");
    await refreshWorkspace();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

async function submitWorkItem(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const errorBox = $("#work-item-error");
  errorBox.hidden = true;
  const payload = {
    topic_id: state.selectedTopic,
    iteration_id: state.selectedIteration || null,
    title: String(form.get("title") || "").trim(),
    kind: "development",
  };
  try {
    const item = await api("/v1/research/work-items", {method:"POST", body:JSON.stringify(payload)});
    $("#work-item-dialog").close();
    formElement.reset();
    showToast(`任务 ${item.display_key} 已创建`);
    await refreshWorkspace();
    activateWorkspaceSection("work-items");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

function openWorkItemEditor(workItemId) {
  const item = (state.workItems || []).find(value => value.id === workItemId);
  if (!item) return;
  const form = $("#work-item-edit-form");
  const values = {
    work_item_id: item.id,
    expected_version: item.version,
    title: item.title,
    objective: item.objective,
    acceptance_criteria: (item.acceptance_criteria || []).join("\n"),
    kind: item.kind,
    status: item.status,
    assignee_type: item.assignee_type,
    assignee: item.assignee,
    workspace_path: item.workspace_path,
    base_ref: item.base_ref,
    summary: item.summary,
  };
  Object.entries(values).forEach(([name, value]) => { form.elements[name].value = value ?? ""; });
  $("#work-item-edit-dialog").showModal();
}

async function submitWorkItemEdit(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const errorBox = $("#work-item-edit-error");
  errorBox.hidden = true;
  const workItemId = form.get("work_item_id");
  const payload = {
    expected_version: Number(form.get("expected_version")),
    title: String(form.get("title") || "").trim(),
    objective: String(form.get("objective") || "").trim(),
    acceptance_criteria: String(form.get("acceptance_criteria") || "").split("\n").map(value => value.trim()).filter(Boolean),
    kind: form.get("kind"),
    status: form.get("status"),
    assignee_type: form.get("assignee_type"),
    assignee: String(form.get("assignee") || "").trim(),
    workspace_path: form.get("workspace_path") || null,
    base_ref: form.get("base_ref") || null,
    summary: String(form.get("summary") || "").trim(),
  };
  try {
    await api(`/v1/research/work-items/by-id?work_item_id=${encodeURIComponent(workItemId)}`, {method:"PATCH", body:JSON.stringify(payload)});
    $("#work-item-edit-dialog").close();
    showToast("任务详情已更新");
    await refreshWorkspace();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

function openDeleteRecord(kind) {
  const records = {
    topic: state.topics.find(item => item.id === state.selectedTopic),
    iteration: state.iterations.find(item => item.id === $("#iteration-edit-form").elements.iteration_id.value),
    "work-item": state.workItems.find(item => item.id === $("#work-item-edit-form").elements.work_item_id.value),
  };
  const labels = {topic:"研究主题", iteration:"研究迭代", "work-item":"科研任务"};
  const record = records[kind];
  if (!record) return;
  state.pendingDelete = {
    kind,
    id: record.id,
    version: record.version,
    title: record.title,
  };
  $(`#${kind === "work-item" ? "work-item" : kind}-edit-dialog`)?.close();
  $("#record-delete-title").textContent = `删除${labels[kind]}`;
  $("#record-delete-summary").innerHTML = `<strong>${escapeHtml(record.title)}</strong><span>${escapeHtml(record.display_key || labels[kind])}</span>`;
  $("#record-delete-error").hidden = true;
  $("#record-delete-dialog").showModal();
}

async function submitDeleteRecord(event) {
  event.preventDefault();
  const target = state.pendingDelete;
  if (!target) return;
  const errorBox = $("#record-delete-error");
  errorBox.hidden = true;
  const paths = {
    topic: `/v1/research/topics/by-id?topic_id=${encodeURIComponent(target.id)}`,
    iteration: `/v1/research/iterations/by-id?iteration_id=${encodeURIComponent(target.id)}`,
    "work-item": `/v1/research/work-items/by-id?work_item_id=${encodeURIComponent(target.id)}&expected_version=${encodeURIComponent(target.version)}`,
  };
  try {
    await api(paths[target.kind], {method:"DELETE"});
    $("#record-delete-dialog").close();
    state.pendingDelete = null;
    if (target.kind === "topic") {
      state.selectedTopic = null;
      state.selectedIteration = null;
    } else if (target.kind === "iteration") {
      state.selectedIteration = null;
    }
    showToast("已从当前工作区删除，审计记录仍保留");
    await refreshWorkspace();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

function activateWorkspaceSection(section) {
  $$("[data-workspace-section]").forEach(button => button.classList.toggle("active", button.dataset.workspaceSection === section));
  $$("[data-workspace-panel]").forEach(panel => { panel.hidden = panel.dataset.workspacePanel !== section; });
}

async function refreshCodexBridge() {
  try {
    const [bridgeStatus, codexExecutions, approvalRequests, workItems] = await Promise.all([
      api("/v1/codex-bridge/status"),
      api("/v1/codex-bridge/executions"),
      api("/v1/codex-bridge/approvals"),
      api("/v1/research/work-items"),
    ]);
    Object.assign(state, {bridgeStatus, codexExecutions, approvalRequests, workItems});
    renderCodexBridge();
    renderWorkspace();
  } catch (error) {
    showToast(`Codex 联动状态加载失败：${error.message}`);
  }
}

function renderCodexBridge() {
  if (!state.bridgeStatus) return;
  const status = state.bridgeStatus;
  const cards = [
    {
      icon:"plugs",
      title:"MCP 服务",
      ready:status.mcp?.status === "ready",
      value:status.mcp?.status === "ready" ? status.mcp.endpoint : "服务可用，需配置 EVIDENCE_RAG_MCP_TOKEN",
    },
    {
      icon:"package",
      title:"本地插件",
      ready:Boolean(status.plugin?.installed),
      value:status.plugin?.installed ? `${status.plugin.name} · ${status.plugin.version || "local"}` : "尚未安装 research-project-bridge",
    },
    {
      icon:"terminal-window",
      title:"Codex CLI",
      ready:Boolean(status.codex_cli?.available),
      value:status.codex_cli?.available ? status.codex_cli.path : "CLI 不可用",
    },
    {
      icon:"shield-check",
      title:"权限策略",
      ready:true,
      value:"读取自动 · 写入确认 · 禁止删除",
    },
  ];
  $("#bridge-status-grid").innerHTML = cards.map(item => `<article class="bridge-status-card">
    <div><span class="status-pill ${item.ready ? "" : "failed"}">${item.ready ? "就绪" : "需处理"}</span><i class="ph ph-${item.icon}"></i></div>
    <h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.value)}</p>
  </article>`).join("");
  $("#bridge-project-binding").textContent = String(status.project_id || "project-rag").toUpperCase();
  $("#bridge-project-context").textContent = state.workspace?.project?.name || status.project_id;

  $("#bridge-execution-count").textContent = `${state.codexExecutions.length} EXECUTIONS`;
  $("#bridge-execution-list").innerHTML = state.codexExecutions.length ? state.codexExecutions.map(item => `<article class="bridge-execution">
    <header><strong>${escapeHtml(item.work_item_title)}</strong><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span></header>
    <small>${escapeHtml(item.mode === "queue" ? "后台队列" : "交互会话")} · ${escapeHtml(item.thread_id || "等待绑定 Thread")} · ${formatTime(item.updated_at)}</small>
    <p>${escapeHtml(item.summary || item.error || "尚未回传执行摘要")}</p>
    <small>${(item.changed_files || []).length} 个文件变化 · ${(item.validation || []).length} 条验证结果 · ${escapeHtml(item.sandbox)}</small>
  </article>`).join("") : '<div class="empty-state"><h3>尚无 Codex 执行</h3><p>从科研任务中选择“交给 Codex”，或在此创建一次执行。</p></div>';

  $("#bridge-approval-count").textContent = `${state.approvalRequests.length} PENDING`;
  $("#bridge-approval-list").innerHTML = state.approvalRequests.length ? state.approvalRequests.map(item => `<article class="bridge-approval">
    <header><strong>${escapeHtml(approvalLabel(item.action))}</strong><span class="status-pill indexing">待批准</span></header>
    <small>${escapeHtml(item.requested_by)} · ${formatTime(item.created_at)}</small>
    <p>${escapeHtml(item.payload?.summary || approvalDescription(item.action))}</p>
    <div class="bridge-approval-actions"><button class="button primary compact-button" data-approval-decision="approved" data-approval-id="${escapeHtml(item.id)}">批准</button><button class="button ghost compact-button" data-approval-decision="rejected" data-approval-id="${escapeHtml(item.id)}">拒绝</button></div>
  </article>`).join("") : '<div class="empty-state compact"><p>当前没有待审批操作</p></div>';
  $$("[data-approval-decision]", $("#bridge-approval-list")).forEach(button => button.addEventListener("click", () => decideBridgeApproval(button.dataset.approvalId, button.dataset.approvalDecision)));
  renderCodexWorkItemOptions();
}

function approvalLabel(action) {
  return ({ "execution.start":"启动 Codex 后台执行", "work_item.complete":"完成科研任务", "evidence.link":"绑定研究证据" })[action] || action;
}

function approvalDescription(action) {
  return action === "execution.start" ? "批准后将在任务工作目录中使用 workspace-write 沙箱执行。" : "批准后才会改变系统中的最终科研状态。";
}

function renderCodexWorkItemOptions() {
  const select = $("#codex-execution-work-item");
  if (!select) return;
  const items = state.workItems || [];
  select.innerHTML = items.length ? items.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.display_key)} · ${escapeHtml(item.title)}</option>`).join("") : '<option value="">没有可执行任务</option>';
}

function openCodexExecution(workItemId = null) {
  if (!(state.workItems || []).length) {
    showToast("请先在项目工作台创建一个开发或分析任务");
    switchView("workspace");
    return;
  }
  renderCodexWorkItemOptions();
  const select = $("#codex-execution-work-item");
  if (workItemId && state.workItems.some(item => item.id === workItemId)) select.value = workItemId;
  const item = state.workItems.find(value => value.id === select.value);
  const form = $("#codex-execution-form");
  form.elements.workspace_path.value = item?.workspace_path || "";
  form.elements.base_ref.value = item?.base_ref || "HEAD";
  renderCodexExecutionContext(item);
  $("#codex-execution-dialog").showModal();
}

function renderCodexExecutionContext(item) {
  const context = $("#codex-execution-context");
  if (!context) return;
  if (!item) {
    context.innerHTML = '<span class="inferred-context-empty">选择任务后，系统会显示自动带入的上下文。</span>';
    return;
  }
  const topic = state.topics.find(value => value.id === item.topic_id);
  const iteration = state.iterations.find(value => value.id === item.iteration_id);
  const repository = state.repositories.find(value => value.id === item.repository_id) || state.repositories.find(value => value.id === state.selectedRepository);
  const rows = [
    ["研究上下文", [topic?.title, iteration?.title].filter(Boolean).join(" / ") || "当前项目"],
    ["代码工作区", item.workspace_path || repository?.name || "由当前项目目录推断"],
    ["版本基线", item.base_ref || repository?.default_branch || "HEAD"],
    ["权限边界", "workspace-write · 业务写入需确认"],
  ];
  context.innerHTML = `<div class="inferred-context-head"><span><i class="ph ph-sparkle"></i>系统已带入</span><small>无需重复填写</small></div>
    <dl>${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd></div>`).join("")}</dl>`;
}

async function submitCodexExecution(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const errorBox = $("#codex-execution-error");
  errorBox.hidden = true;
  const payload = {
    work_item_id: form.get("work_item_id"),
    mode: form.get("mode"),
    workspace_path: form.get("workspace_path") || null,
    base_ref: form.get("base_ref") || null,
    prompt: String(form.get("prompt") || ""),
    sandbox: "workspace-write",
  };
  try {
    const execution = await api("/v1/codex-bridge/executions", {method:"POST", body:JSON.stringify(payload)});
    $("#codex-execution-dialog").close();
    formElement.reset();
    await refreshCodexBridge();
    switchView("codex-bridge");
    if (execution.mode === "interactive" && execution.launch?.url) {
      showToast("已创建交互执行；提示将预填到 Codex，不会自动发送");
      window.location.href = execution.launch.url;
    } else {
      showToast("后台执行已进入审批中心");
    }
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

async function decideBridgeApproval(approvalId, decision) {
  try {
    await api(`/v1/codex-bridge/approvals/decide?approval_id=${encodeURIComponent(approvalId)}`, {
      method:"POST",
      body:JSON.stringify({decision, decided_by:"RAG Core"}),
    });
    showToast(decision === "approved" ? "操作已批准" : "操作已拒绝");
    await Promise.all([refreshCodexBridge(), refreshWorkspace()]);
  } catch (error) {
    showToast(`审批失败：${error.message}`);
  }
}

async function advanceIteration(iterationId, nextStatus) {
  const progress = ({active:25, validating:75, completed:100})[nextStatus] || 0;
  try {
    await api(`/v1/research/iterations/by-id?iteration_id=${encodeURIComponent(iterationId)}`, {method:"PATCH", body:JSON.stringify({status:nextStatus, progress})});
    showToast(`迭代已推进至${statusLabel(nextStatus)}`);
    await refreshWorkspace();
  } catch (error) {
    showToast(`推进失败：${error.message}`);
  }
}

function renderCodexStats() {
  const values = [state.codexStats.sessions, state.codexStats.turns, state.codexStats.commands, state.codexStats.file_changes];
  $$("#codex-metric-grid .metric strong").forEach((node, index) => node.textContent = formatNumber(values[index]));
}

function applyCodexDefaults() {
  const form = $("#codex-sync-form");
  if (!form) return;
  const source = form.elements.source;
  const project = form.elements.project_path;
  if (!source.value && state.codexConfig.source) source.value = state.codexConfig.source;
  if (!project.value && state.codexConfig.project_path) project.value = state.codexConfig.project_path;
}

function renderCodexSessions(filter = "") {
  const list = $("#codex-session-list");
  const query = filter.trim().toLowerCase();
  const sessions = state.codexSessions.filter(item => !query || `${item.title} ${item.cwd || ""} ${item.thread_id}`.toLowerCase().includes(query));
  $("#codex-session-count").textContent = `${sessions.length} THREADS`;
  if (!sessions.length) {
    list.innerHTML = '<div class="empty-state compact"><span class="empty-symbol">∅</span><p>没有匹配会话，请先同步或调整过滤条件</p></div>';
    return;
  }
  list.innerHTML = sessions.map(item => `<div class="codex-session-row ${state.selectedCodexThreads.has(item.thread_id) ? "compare-selected" : ""}"><label class="session-compare-check" title="加入会话对照"><input type="checkbox" data-compare-thread="${escapeHtml(item.thread_id)}" ${state.selectedCodexThreads.has(item.thread_id) ? "checked" : ""}/><span></span></label>
    <button class="codex-session-item ${item.thread_id === state.selectedCodexThread ? "active" : ""}" data-codex-thread="${escapeHtml(item.thread_id)}">
      <strong title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</strong>
      <small title="${escapeHtml(item.cwd || "")}">${escapeHtml(item.cwd || item.thread_id)}</small>
      <span class="session-item-foot"><span class="status-pill ${statusClass(item.status)}">${statusLabel(item.status)}</span><span class="session-counts">${item.turn_count} TURNS · ${item.command_count} CMD · ${item.file_change_count} Δ</span></span>
    </button></div>`).join("");
  $$('[data-codex-thread]', list).forEach(button => button.addEventListener("click", () => selectCodexThread(button.dataset.codexThread)));
  $$('[data-compare-thread]', list).forEach(input => input.addEventListener("change", () => toggleCodexComparisonThread(input.dataset.compareThread, input.checked)));
  updateCodexCompareButton();
}

function toggleCodexComparisonThread(threadId, selected) {
  if (selected) state.selectedCodexThreads.add(threadId); else state.selectedCodexThreads.delete(threadId);
  renderCodexSessions($("#codex-session-filter").value);
}

function updateCodexCompareButton() {
  const button = $("#compare-codex-sessions");
  button.disabled = state.selectedCodexThreads.size < 2;
  button.textContent = state.selectedCodexThreads.size ? `⇄ 对照所选会话 (${state.selectedCodexThreads.size})` : "⇄ 对照所选会话";
}

function renderCodexComparisonHistory() {
  const list = $("#codex-comparison-list");
  if (!list) return;
  $("#codex-comparison-count").textContent = state.codexComparisons.length;
  list.innerHTML = state.codexComparisons.length ? state.codexComparisons.slice(0, 8).map(item => `<button data-codex-comparison="${escapeHtml(item.id)}"><span><b>${escapeHtml(item.display_key)}</b><time>${formatTime(item.created_at)}</time></span><strong>${escapeHtml(item.name)}</strong><small>${item.thread_ids.length} THREADS · ${escapeHtml(item.result?.baseline?.outcome || "stored")}</small></button>`).join("") : '<p>尚未创建会话对照</p>';
  $$('[data-codex-comparison]', list).forEach(button => button.addEventListener("click", () => openCodexComparison(button.dataset.codexComparison)));
}

async function compareCodexSessions() {
  const ids = [...state.selectedCodexThreads];
  if (ids.length < 2) { showToast("请至少选择两个 Codex 会话"); return; }
  const button = $("#compare-codex-sessions");
  button.disabled = true; button.textContent = "正在构建证据对照…";
  try {
    const titles = ids.map(id => state.codexSessions.find(item => item.thread_id === id)?.title).filter(Boolean);
    const comparison = await api("/v1/codex/comparisons", {method:"POST",body:JSON.stringify({thread_ids:ids,baseline_thread_id:ids[0],name:titles.slice(0, 2).join(" ↔ ") || "Codex session comparison"})});
    state.codexComparisons = [comparison, ...state.codexComparisons.filter(item => item.id !== comparison.id)];
    renderCodexComparisonHistory(); renderCodexComparison(comparison); $("#codex-compare-dialog").showModal();
    showToast(`${comparison.display_key} 已保存`);
  } catch (error) { showToast(`会话对照失败：${error.message}`); }
  finally { updateCodexCompareButton(); }
}

async function openCodexComparison(comparisonId) {
  try {
    const comparison = state.codexComparisons.find(item => item.id === comparisonId) || await api(`/v1/codex/comparisons/by-id?comparison_id=${encodeURIComponent(comparisonId)}`);
    renderCodexComparison(comparison); $("#codex-compare-dialog").showModal();
  } catch (error) { showToast(`对照加载失败：${error.message}`); }
}

function renderCodexComparison(comparison) {
  $("#codex-compare-title").textContent = `${comparison.display_key} · ${comparison.name}`;
  const result = comparison.result;
  const cards = result.threads.map(thread => `<article class="session-compare-card ${thread.thread_id === comparison.baseline_thread_id ? "baseline" : ""}"><header><div><span>${thread.thread_id === comparison.baseline_thread_id ? "BASELINE" : "CANDIDATE"}</span><b>${escapeHtml(thread.title)}</b></div><em class="status-pill ${statusClass(thread.outcome)}">${escapeHtml(thread.outcome)}</em></header><div class="session-compare-kpis"><span><b>${thread.counts.turns}</b>TURN</span><span><b>${thread.counts.commands}</b>CMD</span><span><b>${thread.counts.changes}</b>CHANGE</span><span><b>${thread.counts.validations}</b>VALID</span></div><section><h4>目标</h4>${thread.goals.map(item => `<p>${escapeHtml(item.text)}</p>`).join("") || '<p class="muted">无显式目标</p>'}</section><section><h4>决策候选</h4>${thread.decision_candidates.map(item => `<p title="${escapeHtml(item.locator)}">${escapeHtml(item.text)}</p>`).join("") || '<p class="muted">未抽取到决策候选</p>'}</section><section><h4>变更文件</h4><div class="compare-chip-row">${thread.changed_paths.map(item => `<span>${escapeHtml(item.path)}</span>`).join("") || "—"}</div></section><section><h4>验证</h4>${thread.validations.map(item => `<div class="compare-validation"><span class="status-pill ${statusClass(item.status)}">${escapeHtml(item.status)}</span><b>${escapeHtml(item.command)}</b><small>exit ${item.exit_code ?? "?"}</small></div>`).join("") || '<p class="muted">没有真实退出码验证</p>'}</section><section><h4>Commit</h4><div class="compare-chip-row">${thread.commits.map(item => `<span>${escapeHtml(shortCommit(item))}</span>`).join("") || "未提交"}</div></section></article>`).join("");
  const deltas = result.comparisons.map(item => `<tr><td>${escapeHtml(item.candidate_thread_id)}</td><td>${Math.round(item.goal_similarity * 100)}%</td><td>${escapeHtml(item.outcome_transition)}</td><td>＋ ${item.deltas.changed_paths.added.map(escapeHtml).join(", ") || "—"}<br>− ${item.deltas.changed_paths.removed.map(escapeHtml).join(", ") || "—"}</td><td>${item.deltas.commands.added.length} / ${item.deltas.validations.added.length}</td></tr>`).join("");
  $("#codex-compare-content").innerHTML = `<div class="comparison-summary-strip"><span><b>${result.summary.thread_count}</b>会话</span><span><b>${Math.round(result.summary.average_goal_similarity * 100)}%</b>目标相似</span><span><b>${result.summary.shared_paths.length}</b>共享文件</span><span><b>${Object.entries(result.summary.outcomes).map(([key,value]) => `${key}:${value}`).join(" · ")}</b>结果分布</span></div><div class="session-compare-grid">${cards}</div><div class="comparison-delta-table"><h3>相对 Baseline 的确定性差异</h3><table><thead><tr><th>Candidate</th><th>目标相似</th><th>结果迁移</th><th>文件增减</th><th>新增命令 / 验证</th></tr></thead><tbody>${deltas}</tbody></table></div><footer>Derivation · ${escapeHtml(result.derivation)} · 保存于 ${formatTime(comparison.created_at)} · ${escapeHtml(comparison.created_by)}</footer>`;
}

async function selectCodexThread(threadId, focusEntity = null) {
  state.selectedCodexThread = threadId;
  renderCodexSessions($("#codex-session-filter").value);
  if (window.CodexSessionBoard) window.CodexSessionBoard.setLoading("正在装载结构化会话事件");
  try {
    const thread = await api(`/v1/codex/sessions/${encodeURIComponent(threadId)}`);
    state.codexThread = thread;
    renderCodexThread(thread);
    if (focusEntity) requestAnimationFrame(() => focusCodexEntity(focusEntity));
  } catch (error) {
    if (window.CodexSessionBoard) window.CodexSessionBoard.setError(error.message);
  }
}

function renderCodexThread(thread) {
  $("#codex-thread-title").textContent = thread.title;
  $("#codex-thread-meta").textContent = `${thread.thread_id} · ${thread.cwd || "未记录 cwd"} · ${formatTime(thread.updated_at)}`;
  const status = $("#codex-thread-status");
  status.className = `status-pill ${statusClass(thread.status)}`;
  status.textContent = statusLabel(thread.status);
  if (window.CodexSessionBoard) {
    window.CodexSessionBoard.render(thread, {
      api,
      openRelated: openCodexRelatedSource,
    });
  }

  const items = thread.turns.flatMap(turn => turn.items);
  const firstGoal = thread.turns.find(turn => turn.goal)?.goal || "未提取到显式用户目标";
  const commands = items.filter(item => item.item_type === "CommandExecution").length;
  const changes = items.filter(item => ["FileChange", "Patch"].includes(item.item_type)).length;
  const paths = [...new Set(items
    .filter(item => ["FileChange", "Patch"].includes(item.item_type))
    .flatMap(item => item.metadata?.paths || [])
    .map(path => {
      const value = String(path || "").trim().replace(/\\/g, "/");
      if (!value || /---|\n|\r|\t/.test(value) || value.includes("/.codex/plugins/") || value.includes("/node_modules/")) return "";
      if (value.startsWith("/") && thread.cwd && !value.startsWith(`${thread.cwd}/`)) return "";
      const relative = thread.cwd && value.startsWith(`${thread.cwd}/`) ? value.slice(thread.cwd.length + 1) : value.replace(/^\.?\//, "");
      if (!relative || relative.startsWith("../") || relative.includes("/../") || /\s/.test(relative)) return "";
      const filename = relative.split("/").at(-1) || "";
      const knownNames = new Set(["Makefile", "Dockerfile", "README", "LICENSE", "Procfile"]);
      return knownNames.has(filename) || /\.[A-Za-z0-9][A-Za-z0-9_-]{0,11}$/.test(filename) ? relative : "";
    })
    .filter(Boolean))];
  $("#codex-thread-summary").innerHTML = `
    <div class="summary-goal"><b>当前会话目标</b>${escapeHtml(firstGoal.slice(0, 420))}</div>
    <div class="summary-stats"><div><strong>${thread.turns.length}</strong><small>TURNS</small></div><div><strong>${commands}</strong><small>COMMANDS</small></div><div><strong>${changes}</strong><small>CHANGES</small></div></div>
    ${paths.length ? `<div class="locator" style="margin-top:9px" title="${escapeHtml(paths.join(" · "))}">FILES · ${escapeHtml(paths.slice(0, 4).join(" · "))}</div>` : ""}`;
}

function renderCodexItem(item) {
  const cssType = item.item_type.toLowerCase();
  const cardClass = item.item_type === "AgentMessage" ? "agent-message" : (item.item_type === "UserGoal" ? "user-goal" : "");
  return `<article class="codex-item ${cardClass}" data-codex-entity="${escapeHtml(item.id)}">
    <div class="codex-item-head"><div><span class="event-kind ${cssType}">${escapeHtml(item.item_type)}</span> <strong>${escapeHtml(item.name)}</strong></div><small>${formatTime(item.timestamp)}</small></div>
    <pre>${escapeHtml(item.content)}</pre>
  </article>`;
}

function focusCodexEntity(entityId) {
  if (window.CodexSessionBoard) {
    window.CodexSessionBoard.focus(entityId);
    return;
  }
  const target = $$('[data-codex-entity]').find(node => node.dataset.codexEntity === entityId);
  if (!target) return;
  target.classList.add("focused");
  target.scrollIntoView({behavior:"smooth", block:"center"});
  window.setTimeout(() => target.classList.remove("focused"), 2200);
}

async function openCodexRelatedSource(item) {
  if (!item?.openItem) return;
  try {
    if (typeof openGlobalResult === "function") {
      await openGlobalResult(item.openItem);
      return;
    }
    showToast("该节点保留了稳定来源标识，但当前来源导航尚不可用");
  } catch (error) {
    showToast(`打开来源失败：${error.message}`);
  }
}

async function runCodexSearch(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const query = String(form.get("query") || "").trim();
  const itemType = String(form.get("item_type") || "");
  if (!query) return;
  $("#codex-search-results").innerHTML = '<div class="empty-state compact"><p>正在融合会话词法与向量信号</p></div>';
  try {
    const payload = await api("/v1/codex/search", {method:"POST", body:JSON.stringify({query, scope:{item_types:itemType ? [itemType] : []}, limit:12, include_edges:true})});
    renderCodexSearchResults(payload);
  } catch (error) {
    $("#codex-search-results").innerHTML = `<div class="empty-state compact"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function renderCodexSearchResults(payload) {
  $("#codex-search-summary").textContent = `${payload.total} 条证据 · ${payload.trace.lexical_candidates} lexical / ${payload.trace.dense_candidates} dense`;
  $("#codex-search-duration").textContent = `${payload.trace.duration_ms} MS`;
  const container = $("#codex-search-results");
  if (!payload.results.length) {
    container.innerHTML = '<div class="empty-state compact"><p>没有找到匹配的会话证据</p></div>';
    return;
  }
  container.innerHTML = payload.results.map((item, index) => `
    <article class="codex-search-result" data-codex-result="${index}">
      <div class="codex-search-result-head"><strong>${escapeHtml(item.thread_title)}</strong><span class="codex-search-score">${item.score.toFixed(3)}</span></div>
      <div class="locator">${escapeHtml(item.item_type)} · ${escapeHtml(item.evidence_locator)}</div>
      <div class="result-snippet">${escapeHtml(item.snippet)}</div>
    </article>`).join("");
  $$('[data-codex-result]', container).forEach(node => node.addEventListener("click", () => {
    const item = payload.results[Number(node.dataset.codexResult)];
    selectCodexThread(item.thread_id, item.entity_id);
  }));
}

async function submitCodexSync(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const errorBox = $("#codex-sync-error");
  errorBox.hidden = true;
  const projectId = String(form.get("project_id") || "project-rag");
  const payload = {
    source: form.get("source"),
    project_path: form.get("project_path"),
    project_id: projectId,
    acl_ref: `project:${projectId}`,
    include_archived: form.get("include_archived") === "on",
    max_sessions: Number(form.get("max_sessions") || 200),
  };
  try {
    const result = await api("/v1/ingestion/codex", {method:"POST", body:JSON.stringify(payload)});
    $("#codex-sync-dialog").close();
    showToast(`会话同步任务已创建：${result.workflow_id.slice(0, 18)}`);
    await refreshAll();
    pollWorkflow(result.workflow_id);
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
}

async function loadCodeRefs(repositoryId) {
  const select = $("#code-version-ref");
  if (!repositoryId) {
    state.codeRefs = null;
    state.selectedCodeRef = "HEAD";
    select.innerHTML = '<option value="HEAD">当前 HEAD</option>';
    return;
  }
  try {
    state.codeRefs = await api(`/v1/code/refs?repository_id=${encodeURIComponent(repositoryId)}`);
    const branchOptions = (state.codeRefs.branches || []).map(item => `<option value="${escapeHtml(item.name)}">${item.is_default ? "默认分支" : "分支"} · ${escapeHtml(item.name)} · ${escapeHtml(shortCommit(item.head_sha))}</option>`).join("");
    const commitOptions = state.gitHistory.slice(0, 60).map(item => `<option value="${escapeHtml(item.sha)}">提交 · ${escapeHtml(shortCommit(item.sha))} · ${escapeHtml(item.message)}</option>`).join("");
    select.innerHTML = `<option value="HEAD">当前 HEAD · ${escapeHtml(shortCommit(state.codeRefs.head_sha))}</option><optgroup label="分支">${branchOptions}</optgroup><optgroup label="最近提交">${commitOptions}</optgroup>`;
    if (![...select.options].some(option => option.value === state.selectedCodeRef)) state.selectedCodeRef = "HEAD";
    select.value = state.selectedCodeRef;
    renderCompareRefOptions();
  } catch (error) {
    showToast(`版本列表加载失败：${error.message}`);
  }
}

function renderCompareRefOptions() {
  const options = [
    {value:"HEAD", label:`当前 HEAD · ${shortCommit(state.codeRefs?.head_sha)}`},
    ...(state.codeRefs?.branches || []).map(item => ({value:item.name, label:`分支 · ${item.name} · ${shortCommit(item.head_sha)}`})),
    ...state.gitHistory.slice(0, 80).map(item => ({value:item.sha, label:`提交 · ${shortCommit(item.sha)} · ${item.message}`})),
  ];
  const html = options.map(item => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`).join("");
  $("#compare-base-ref").innerHTML = html;
  $("#compare-target-ref").innerHTML = html;
  $("#compare-base-ref").value = state.codeRefs?.default_branch || "HEAD";
  $("#compare-target-ref").value = state.selectedCodeRef || "HEAD";
}

function openCodeCompare(baseRef = null, targetRef = null) {
  if (!state.selectedRepository) {
    showToast("请先选择代码仓库");
    return;
  }
  renderCompareRefOptions();
  if (baseRef && [...$("#compare-base-ref").options].some(option => option.value === baseRef)) $("#compare-base-ref").value = baseRef;
  if (targetRef && [...$("#compare-target-ref").options].some(option => option.value === targetRef)) $("#compare-target-ref").value = targetRef;
  $("#code-compare-dialog").showModal();
}

async function submitCodeCompare(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const baseRef = form.get("base_ref");
  const targetRef = form.get("target_ref");
  const container = $("#code-compare-content");
  container.innerHTML = '<div class="empty-state"><p>正在读取 Git 对象并计算版本差异…</p></div>';
  try {
    const comparison = await api(`/v1/code/compare?repository_id=${encodeURIComponent(state.selectedRepository)}&base_ref=${encodeURIComponent(baseRef)}&target_ref=${encodeURIComponent(targetRef)}`);
    container.innerHTML = `<div class="comparison-summary-strip"><span><b>${escapeHtml(shortCommit(comparison.base_sha))}</b>BASE</span><span><b>${escapeHtml(shortCommit(comparison.target_sha))}</b>TARGET</span><span><b>${comparison.changed_files.length}</b>CHANGED FILES</span><span><b>工作树未改变</b>READ-ONLY GIT OBJECTS</span></div>
      <div class="code-compare-layout">
        <aside class="code-compare-files">${comparison.changed_files.map(item => `<button type="button" data-compare-path="${escapeHtml(item.path)}"><span class="status-pill neutral">${escapeHtml(item.status)}</span><strong>${escapeHtml(item.path)}</strong>${item.old_path ? `<small>${escapeHtml(item.old_path)} →</small>` : ""}</button>`).join("") || '<p>两个版本没有文件差异</p>'}</aside>
        <pre id="code-compare-diff" class="code-compare-diff">选择一个变更文件查看统一 Diff</pre>
      </div>`;
    $$("[data-compare-path]", container).forEach(button => button.addEventListener("click", () => loadCodeComparePath(baseRef, targetRef, button.dataset.comparePath)));
  } catch (error) {
    container.innerHTML = `<div class="empty-state"><h3>比较失败</h3><p>${escapeHtml(error.message)}</p></div>`;
  }
}

async function loadCodeComparePath(baseRef, targetRef, path) {
  const diff = $("#code-compare-diff");
  diff.textContent = "正在加载 Diff…";
  try {
    const comparison = await api(`/v1/code/compare?repository_id=${encodeURIComponent(state.selectedRepository)}&base_ref=${encodeURIComponent(baseRef)}&target_ref=${encodeURIComponent(targetRef)}&path=${encodeURIComponent(path)}`);
    diff.textContent = comparison.diff || "该文件没有文本 Diff（可能为二进制、重命名或删除）。";
  } catch (error) {
    diff.textContent = error.message;
  }
}

async function loadFiles(repositoryId, ref = state.selectedCodeRef || "HEAD") {
  if (!repositoryId) {
    state.files = [];
    state.filesRepositoryId = null;
    state.openCodeFolders.clear();
    state.currentFile = null;
    renderFiles();
    renderEmptyCodeViewer();
    return;
  }
  try {
    const repositoryChanged = state.filesRepositoryId !== repositoryId;
    state.files = await api(`/v1/code/files?repository_id=${encodeURIComponent(repositoryId)}&ref=${encodeURIComponent(ref)}`);
    state.filesRepositoryId = repositoryId;
    if (repositoryChanged) {
      state.openCodeFolders = new Set();
      state.currentFile = null;
      $("#file-filter").value = "";
      renderEmptyCodeViewer();
    }
    if (state.currentFile && !state.files.some(item => item.path === state.currentFile.path)) {
      state.currentFile = null;
      renderEmptyCodeViewer();
    }
    $("#history-version-banner").hidden = ref === "HEAD";
    renderFiles();
  } catch (error) { showToast(error.message); }
}

const codeLanguageMeta = {
  rust: {label:"Rust", mark:"RS"},
  typescript: {label:"TypeScript", mark:"TS"},
  javascript: {label:"JavaScript", mark:"JS"},
  json: {label:"JSON", mark:"{}"},
  markdown: {label:"Markdown", mark:"MD"},
  python: {label:"Python", mark:"PY"},
  yaml: {label:"YAML", mark:"YML"},
  toml: {label:"TOML", mark:"TOML"},
  sql: {label:"SQL", mark:"SQL"},
  bash: {label:"Shell", mark:"SH"},
  html: {label:"HTML", mark:"<>"},
  xml: {label:"XML", mark:"XML"},
  css: {label:"CSS", mark:"#"},
  proto: {label:"Proto", mark:"PB"},
  dockerfile: {label:"Dockerfile", mark:"DO"},
  c: {label:"C", mark:"C"},
  text: {label:"Text", mark:"TX"},
};

const codeKeywordSets = Object.fromEntries(Object.entries({
  rust: "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while abstract become box do final macro override priv typeof unsized virtual yield try",
  typescript: "abstract any as asserts async await bigint boolean break case catch class const constructor continue debugger declare default delete do else enum export extends false finally for from function get if implements import in infer instanceof interface is keyof let module namespace never new null number object of override package private protected public readonly require return set static string super switch symbol this throw true try type typeof undefined unique unknown var void while with yield",
  javascript: "async await break case catch class const continue debugger default delete do else export extends false finally for from function get if import in instanceof let new null of return set static super switch this throw true try typeof undefined var void while with yield",
  python: "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield match case",
  sql: "all alter and as asc between by case check column constraint create cross database default delete desc distinct drop else end exists false foreign from full grant group having in index inner insert intersect into is join key left like limit not null on or order outer primary references right select set table then true union unique update values view when where with",
  bash: "case do done elif else esac export fi for function if in local readonly return set then unset until while",
  c: "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while",
  proto: "syntax import package option message enum service rpc returns repeated optional required oneof map reserved extensions extend public weak",
  dockerfile: "add arg cmd copy entrypoint env expose from healthcheck label maintainer onbuild run shell stopsignal user volume workdir",
}).map(([language, words]) => [language, new Set(words.split(/\s+/))]));

function normalizeCodeLanguage(language = "", path = "") {
  const normalized = String(language || "").toLowerCase();
  if (codeLanguageMeta[normalized]) return normalized;
  const filename = path.split("/").at(-1)?.toLowerCase() || "";
  if (filename === "dockerfile") return "dockerfile";
  const extension = filename.split(".").at(-1);
  return ({
    rs:"rust", ts:"typescript", tsx:"typescript", js:"javascript", jsx:"javascript",
    json:"json", md:"markdown", py:"python", yml:"yaml", yaml:"yaml", toml:"toml",
    sql:"sql", sh:"bash", bash:"bash", html:"html", htm:"html", xml:"xml", css:"css",
    proto:"proto", c:"c", h:"c",
  })[extension] || "text";
}

function buildCodeFileTree(files) {
  const root = {kind:"folder", name:"", path:"", children:new Map(), count:0};
  files.forEach(file => {
    const parts = file.path.split("/").filter(Boolean);
    let folder = root;
    folder.count += 1;
    parts.slice(0, -1).forEach((part, index) => {
      const path = parts.slice(0, index + 1).join("/");
      const key = `folder:${part}`;
      if (!folder.children.has(key)) {
        folder.children.set(key, {kind:"folder", name:part, path, children:new Map(), count:0});
      }
      folder = folder.children.get(key);
      folder.count += 1;
    });
    const name = parts.at(-1) || file.path;
    folder.children.set(`file:${name}`, {kind:"file", name, path:file.path, file});
  });
  return root;
}

function codeTreeEntries(folder) {
  return [...folder.children.values()].sort((left, right) => {
    if (left.kind !== right.kind) return left.kind === "folder" ? -1 : 1;
    return left.name.localeCompare(right.name, undefined, {numeric:true, sensitivity:"base"});
  });
}

function renderCodeTreeChildren(folder, depth, filter) {
  const normalizedFilter = filter.toLowerCase();
  return codeTreeEntries(folder).map(node => {
    if (node.kind === "file") {
      if (normalizedFilter && !node.path.toLowerCase().includes(normalizedFilter)) return "";
      const language = normalizeCodeLanguage(node.file.language, node.path);
      const meta = codeLanguageMeta[language] || codeLanguageMeta.text;
      const active = state.currentFile?.path === node.path;
      return `<button type="button" class="code-tree-row code-tree-file ${active ? "active" : ""}"
        data-path="${escapeHtml(node.path)}" role="treeitem" aria-selected="${active}"
        style="--tree-depth:${depth}" title="${escapeHtml(node.path)}">
        <span class="code-file-mark language-${escapeHtml(language)}">${escapeHtml(meta.mark)}</span>
        <span>${escapeHtml(node.name)}</span>
      </button>`;
    }
    const expanded = normalizedFilter ? true : state.openCodeFolders.has(node.path);
    const filterMatch = !normalizedFilter || node.path.toLowerCase().includes(normalizedFilter);
    const childrenHtml = expanded || normalizedFilter
      ? renderCodeTreeChildren(node, depth + 1, filter)
      : "";
    if (normalizedFilter && !filterMatch && !childrenHtml) return "";
    return `<div class="code-tree-folder" role="treeitem" aria-expanded="${expanded}">
      <button type="button" class="code-tree-row code-tree-folder-row" data-code-folder="${escapeHtml(node.path)}"
        aria-expanded="${expanded}" style="--tree-depth:${depth}">
        <i class="ph ph-caret-right code-tree-caret" aria-hidden="true"></i>
        <i class="ph ${expanded ? "ph-folder-open" : "ph-folder"} code-tree-folder-icon" aria-hidden="true"></i>
        <span>${escapeHtml(node.name)}</span><small>${formatNumber(node.count)}</small>
      </button>
      ${expanded ? `<div role="group">${childrenHtml}</div>` : ""}
    </div>`;
  }).join("");
}

function expandCodePath(path) {
  const parts = path.split("/").filter(Boolean);
  parts.slice(0, -1).forEach((_, index) => {
    state.openCodeFolders.add(parts.slice(0, index + 1).join("/"));
  });
}

function renderFiles(filter = $("#file-filter")?.value || "") {
  const list = $("#file-list");
  const visible = state.files.filter(item => item.path.toLowerCase().includes(filter.toLowerCase()));
  $("#file-count").textContent = `${visible.length} FILES`;
  if (!visible.length) { list.innerHTML = '<div class="empty-state compact"><p>没有匹配文件</p></div>'; return; }
  list.innerHTML = renderCodeTreeChildren(buildCodeFileTree(state.files), 0, filter);
  $$("[data-code-folder]", list).forEach(button => button.addEventListener("click", () => {
    const path = button.dataset.codeFolder;
    if (state.openCodeFolders.has(path)) state.openCodeFolders.delete(path);
    else state.openCodeFolders.add(path);
    renderFiles();
    [...$$("[data-code-folder]", list)]
      .find(item => item.dataset.codeFolder === path)
      ?.focus({preventScroll:true});
  }));
  $$('[data-path]', list).forEach(button => button.addEventListener("click", () => loadFile($("#browser-repository").value, button.dataset.path)));
}

function renderEmptyCodeViewer() {
  $("#code-breadcrumb").innerHTML = '<strong id="code-path">未选择文件</strong>';
  $("#code-version").textContent = "—";
  $("#code-size").textContent = "—";
  $("#code-language").textContent = "TEXT";
  $("#code-language").className = "status-pill neutral";
  $("#code-content").innerHTML = '<div class="empty-state"><span class="empty-symbol">{ }</span><p>从左侧目录树选择文件查看原始证据</p></div>';
  $("#symbol-count").textContent = "0";
  $("#symbol-list").innerHTML = '<div class="empty-state compact"><p>暂无 Symbol</p></div>';
}

function formatCodeBytes(value) {
  const bytes = new TextEncoder().encode(value).length;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderCodeBreadcrumb(path) {
  const repository = state.repositories.find(item => item.id === state.selectedRepository);
  const parts = path.split("/").filter(Boolean);
  const directoryParts = parts.slice(0, -1);
  let current = "";
  const directoryHtml = directoryParts.map(part => {
    current = current ? `${current}/${part}` : part;
    return `<i>/</i><button type="button" data-code-breadcrumb="${escapeHtml(current)}">${escapeHtml(part)}</button>`;
  }).join("");
  $("#code-breadcrumb").innerHTML = `<span>${escapeHtml(repository?.name || "repository")}</span>${directoryHtml}<i>/</i><strong id="code-path">${escapeHtml(parts.at(-1) || path)}</strong>`;
  $$("[data-code-breadcrumb]", $("#code-breadcrumb")).forEach(button => button.addEventListener("click", () => {
    const folderPath = button.dataset.codeBreadcrumb;
    const partsToOpen = folderPath.split("/");
    partsToOpen.forEach((_, index) => state.openCodeFolders.add(partsToOpen.slice(0, index + 1).join("/")));
    $("#file-filter").value = "";
    renderFiles();
    [...$$("[data-code-folder]", $("#file-list"))]
      .find(item => item.dataset.codeFolder === folderPath)
      ?.scrollIntoView({behavior:"smooth", block:"center"});
  }));
}

function codeSyntaxProfile(language) {
  if (["rust", "typescript", "javascript", "c", "proto"].includes(language)) {
    return {lineComment:"//", blockStart:"/*", blockEnd:"*/"};
  }
  if (language === "css") return {blockStart:"/*", blockEnd:"*/"};
  if (["python", "bash", "yaml", "toml", "dockerfile"].includes(language)) return {lineComment:"#"};
  if (language === "sql") return {lineComment:"--", blockStart:"/*", blockEnd:"*/"};
  if (["html", "xml"].includes(language)) return {blockStart:"<!--", blockEnd:"-->"};
  if (language === "json") return {lineComment:"//"};
  return {};
}

function pushCodeToken(parts, className, value) {
  if (!value) return;
  const escaped = escapeHtml(value);
  parts.push(className ? `<span class="syntax-${className}">${escaped}</span>` : escaped);
}

function highlightMarkdownLine(line) {
  let match = line.match(/^(\s*)(#{1,6})(\s+)(.*)$/);
  if (match) {
    return `${escapeHtml(match[1])}<span class="syntax-markup">${escapeHtml(match[2])}</span>${escapeHtml(match[3])}<span class="syntax-heading">${escapeHtml(match[4])}</span>`;
  }
  match = line.match(/^(\s*)(```+|~~~+)(.*)$/);
  if (match) {
    return `${escapeHtml(match[1])}<span class="syntax-markup">${escapeHtml(match[2])}</span><span class="syntax-type">${escapeHtml(match[3])}</span>`;
  }
  match = line.match(/^(\s*)([-*+]|\d+\.)(\s+)(.*)$/);
  if (match) {
    return `${escapeHtml(match[1])}<span class="syntax-markup">${escapeHtml(match[2])}</span>${escapeHtml(match[3])}${escapeHtml(match[4])}`;
  }
  if (/^\s*>/.test(line)) return `<span class="syntax-comment">${escapeHtml(line)}</span>`;
  return escapeHtml(line);
}

function highlightCodeLine(line, language, state) {
  if (language === "markdown") return highlightMarkdownLine(line);
  const profile = codeSyntaxProfile(language);
  const keywords = codeKeywordSets[language] || new Set();
  const parts = [];
  let index = 0;
  while (index < line.length) {
    if (state.blockComment && profile.blockEnd) {
      const endIndex = line.indexOf(profile.blockEnd, index);
      if (endIndex < 0) {
        pushCodeToken(parts, "comment", line.slice(index));
        return parts.join("");
      }
      pushCodeToken(parts, "comment", line.slice(index, endIndex + profile.blockEnd.length));
      index = endIndex + profile.blockEnd.length;
      state.blockComment = false;
      continue;
    }
    if (profile.lineComment && line.startsWith(profile.lineComment, index)) {
      pushCodeToken(parts, "comment", line.slice(index));
      break;
    }
    if (profile.blockStart && line.startsWith(profile.blockStart, index)) {
      const endIndex = line.indexOf(profile.blockEnd, index + profile.blockStart.length);
      if (endIndex < 0) {
        pushCodeToken(parts, "comment", line.slice(index));
        state.blockComment = true;
        break;
      }
      pushCodeToken(parts, "comment", line.slice(index, endIndex + profile.blockEnd.length));
      index = endIndex + profile.blockEnd.length;
      continue;
    }
    const character = line[index];
    if (character === '"' || character === "'" || (character === "`" && ["typescript", "javascript", "bash"].includes(language))) {
      let endIndex = index + 1;
      while (endIndex < line.length) {
        if (line[endIndex] === character && line[endIndex - 1] !== "\\") {
          endIndex += 1;
          break;
        }
        endIndex += 1;
      }
      const nextAfterString = line.slice(endIndex).match(/^\s*(.)/)?.[1] || "";
      const stringClass = ["json", "yaml", "toml"].includes(language) &&
        [":", "="].includes(nextAfterString)
        ? "property"
        : "string";
      pushCodeToken(parts, stringClass, line.slice(index, endIndex));
      index = endIndex;
      continue;
    }
    const numberMatch = line.slice(index).match(/^(?:0[xob][\da-f_]+|\d[\d_]*(?:\.\d+)?(?:e[+-]?\d+)?)/i);
    if (numberMatch) {
      pushCodeToken(parts, "number", numberMatch[0]);
      index += numberMatch[0].length;
      continue;
    }
    const wordMatch = line.slice(index).match(/^[A-Za-z_$][\w$]*/);
    if (wordMatch) {
      const word = wordMatch[0];
      const lookup = language === "sql" || language === "dockerfile" ? word.toLowerCase() : word;
      const rest = line.slice(index + word.length);
      const next = rest.match(/^\s*(.)/)?.[1] || "";
      const before = line.slice(0, index).trimEnd();
      let tokenClass = "";
      if (keywords.has(lookup)) tokenClass = "keyword";
      else if (["true", "false", "null", "None", "Some", "Ok", "Err"].includes(word)) tokenClass = "literal";
      else if (["html", "xml"].includes(language) && /<\/?$/.test(before)) tokenClass = "keyword";
      else if (["html", "xml"].includes(language) && next === "=") tokenClass = "property";
      else if (["json", "yaml", "toml", "css"].includes(language) && [":", "="].includes(next)) tokenClass = "property";
      else if (next === "(") tokenClass = "function";
      else if (/^[A-Z][A-Za-z0-9_]*$/.test(word)) tokenClass = "type";
      pushCodeToken(parts, tokenClass, word);
      index += word.length;
      continue;
    }
    if ("{}[]()=><+-*/%!:.,;&|?@#".includes(character)) {
      pushCodeToken(parts, "operator", character);
      index += 1;
      continue;
    }
    pushCodeToken(parts, "", character);
    index += 1;
  }
  return parts.join("");
}

function renderHighlightedCode(content, language, highlight = {}) {
  const lines = content.split("\n");
  const syntaxState = {blockComment:false};
  const tabSize = language === "python" ? 4 : 2;
  return {
    lines,
    html:`<table class="code-table github-code-table language-${escapeHtml(language)}" style="--tab-size:${tabSize}"><tbody>${lines.map((line, index) => {
      const number = index + 1;
      const active = highlight.start && number >= highlight.start && number <= highlight.end;
      const highlighted = highlightCodeLine(line, language, syntaxState);
      return `<tr class="code-row ${active ? "highlight" : ""}" data-line="${number}"><td class="line-number">${number}</td><td class="code-line"><code class="code-line-text">${highlighted || " "}</code></td></tr>`;
    }).join("")}</tbody></table>`,
  };
}

async function loadFile(repositoryId, path, highlight = {}) {
  if (!repositoryId || !path) return;
  try {
    const ref = state.selectedCodeRef || "HEAD";
    const file = await api(`/v1/code/file?repository_id=${encodeURIComponent(repositoryId)}&ref=${encodeURIComponent(ref)}&path=${encodeURIComponent(path)}`);
    state.currentFile = file;
    expandCodePath(file.path);
    renderFiles($("#file-filter").value);
    renderCodeBreadcrumb(file.path);
    $("#code-version").textContent = `${file.historical ? "历史只读" : "当前版本"} · Commit ${shortCommit(file.commit_sha)} · ${file.source_uri || file.path}`;
    $("#history-version-banner").hidden = !file.historical;
    if (file.binary) {
      $("#code-language").textContent = "BINARY";
      $("#code-language").className = "status-pill neutral";
      $("#code-size").textContent = `${formatNumber(file.size)} B`;
      $("#code-content").innerHTML = '<div class="empty-state"><span class="empty-symbol">01</span><h3>二进制文件</h3><p>历史对象已验证，但不在代码阅读器中渲染。</p></div>';
      $("#symbol-count").textContent = "0";
      $("#symbol-list").innerHTML = '<div class="empty-state compact"><p>二进制文件没有 Symbol</p></div>';
      return;
    }
    const language = normalizeCodeLanguage(file.language, file.path);
    const languageMeta = codeLanguageMeta[language] || codeLanguageMeta.text;
    const rendered = renderHighlightedCode(file.content, language, highlight);
    $("#code-language").textContent = languageMeta.label;
    $("#code-language").className = `status-pill code-language-badge language-${language}`;
    $("#code-size").textContent = `${formatNumber(rendered.lines.length)} LINES · ${formatCodeBytes(file.content)}`;
    $("#code-content").innerHTML = rendered.html;
    $("#symbol-count").textContent = file.symbols.length;
    $("#symbol-list").innerHTML = file.symbols.length ? file.symbols.map(symbol => `<button class="symbol-item" data-start="${symbol.start_line}" data-end="${symbol.end_line}"><strong>${escapeHtml(symbol.qualified_name || symbol.name)}</strong><small>${escapeHtml(symbol.kind || symbol.metadata?.kind || "symbol")} · L${symbol.start_line}–${symbol.end_line}</small></button>`).join("") : '<div class="empty-state compact"><p>此文件没有结构化 Symbol</p></div>';
    $$('[data-start]', $("#symbol-list")).forEach(button => button.addEventListener("click", () => highlightLines(Number(button.dataset.start), Number(button.dataset.end))));
    if (highlight.start) requestAnimationFrame(() => $(".code-row.highlight")?.scrollIntoView({block:"center"}));
  } catch (error) { showToast(error.message); }
}

function highlightLines(start, end) {
  $$(".code-row").forEach(row => row.classList.toggle("highlight", Number(row.dataset.line) >= start && Number(row.dataset.line) <= end));
  $(".code-row.highlight")?.scrollIntoView({behavior:"smooth", block:"center"});
}

function openManualRun() {
  switchView("experiments");
  if (!state.experiments.length) {
    showToast("请先创建实验，再记录 Run");
    $("#experiment-dialog").showModal();
    return;
  }
  renderExperimentOptions();
  $("#run-dialog").showModal();
}

function installEvents() {
  $$(".nav-item").forEach(item => item.addEventListener("click", () => switchView(item.dataset.view)));
  $("#open-topic").addEventListener("click", () => $("#topic-dialog").showModal());
  $$("[data-create-first-topic]").forEach(button => button.addEventListener("click", () => $("#topic-dialog").showModal()));
  $("#open-iteration").addEventListener("click", () => {
    if (!state.selectedTopic) {
      showToast("请先创建一个研究主题");
      $("#topic-dialog").showModal();
      return;
    }
    renderIterationTopicOptions();
    $("#iteration-dialog").showModal();
  });
  $("#open-work-item").addEventListener("click", () => {
    if (!state.selectedTopic) {
      showToast("请先选择研究主题");
      return;
    }
    $("#work-item-dialog").showModal();
  });
  $("#edit-topic").addEventListener("click", openTopicEditor);
  $$("[data-workspace-section]").forEach(button => button.addEventListener("click", () => activateWorkspaceSection(button.dataset.workspaceSection)));
  $("#open-experiment").addEventListener("click", () => {
    renderExperimentOptions();
    $("#experiment-dialog").showModal();
  });
  $("#open-run").addEventListener("click", () => {
    if (!state.experiments.length) { showToast("请先创建一个实验"); $("#experiment-dialog").showModal(); return; }
    renderExperimentOptions();
    $("#run-dialog").showModal();
  });
  $("#open-document").addEventListener("click", () => {
    if (window.DocumentImporter) window.DocumentImporter.open();
    else { renderDocumentIterationOptions(); $("#document-dialog").showModal(); }
  });
  $("#scan-claim-matches").addEventListener("click", scanClaimMatches);
  $("#open-mlflow").addEventListener("click", () => $("#mlflow-dialog").showModal());
  $("#open-mlflow-from-experiments").addEventListener("click", () => $("#mlflow-dialog").showModal());
  $("#open-notebook-from-experiments").addEventListener("click", () => $("#notebook-dialog").showModal());
  $("#open-manual-run").addEventListener("click", openManualRun);
  $("#open-notebook").addEventListener("click", () => $("#notebook-dialog").showModal());
  $$("[data-open-experiment-source]").forEach(button => button.addEventListener("click", () => {
    if (button.dataset.openExperimentSource === "mlflow") $("#mlflow-dialog").showModal();
    if (button.dataset.openExperimentSource === "notebook") $("#notebook-dialog").showModal();
    if (button.dataset.openExperimentSource === "manual") openManualRun();
  }));
  $("#open-ingest").addEventListener("click", () => $("#ingest-dialog").showModal());
  $("#open-codex-sync").addEventListener("click", () => $("#codex-sync-dialog").showModal());
  $("#compare-codex-sessions").addEventListener("click", compareCodexSessions);
  $$('[data-close-modal]').forEach(button => button.addEventListener("click", () => button.closest("dialog")?.close()));
  $("#ingest-form").addEventListener("submit", submitIngestion);
  $("#codex-sync-form").addEventListener("submit", submitCodexSync);
  $("#topic-form").addEventListener("submit", submitTopic);
  $("#iteration-form").addEventListener("submit", submitIteration);
  $("#topic-edit-form").addEventListener("submit", submitTopicEdit);
  $("#iteration-edit-form").addEventListener("submit", submitIterationEdit);
  $("#work-item-form").addEventListener("submit", submitWorkItem);
  $("#work-item-edit-form").addEventListener("submit", submitWorkItemEdit);
  $$("[data-delete-record]").forEach(button => button.addEventListener("click", () => openDeleteRecord(button.dataset.deleteRecord)));
  $("#record-delete-form").addEventListener("submit", submitDeleteRecord);
  $("#codex-execution-form").addEventListener("submit", submitCodexExecution);
  $("#code-compare-form").addEventListener("submit", submitCodeCompare);
  $("#experiment-form").addEventListener("submit", submitExperiment);
  $("#run-form").addEventListener("submit", submitRun);
  $("#document-form").addEventListener("submit", submitDocument);
  $("#mlflow-form").addEventListener("submit", submitMlflow);
  $("#notebook-form").addEventListener("submit", submitNotebook);
  $("#refresh-data").addEventListener("click", refreshAll);
  $("#workflow-status-filter").addEventListener("change", renderWorkflows);
  $("#refresh-workspace").addEventListener("click", refreshWorkspace);
  $("#refresh-sources").addEventListener("click", refreshSources);
  $("#refresh-experiment-sources").addEventListener("click", refreshSources);
  $("#history-repository").addEventListener("change", event => selectGitHistoryRepository(event.target.value));
  installGitHistoryPopover();
  $("#code-version-ref").addEventListener("change", async event => {
    state.selectedCodeRef = event.target.value || "HEAD";
    const path = state.currentFile?.path;
    await loadFiles(state.selectedRepository, state.selectedCodeRef);
    if (path && state.files.some(item => item.path === path)) await loadFile(state.selectedRepository, path);
  });
  $("#open-code-compare").addEventListener("click", () => openCodeCompare());
  $("#scan-bindings").addEventListener("click", scanBindings);
  $("#refresh-bindings").addEventListener("click", refreshBindings);
  $("#binding-status-filter").addEventListener("change", () => {
    state.bindingPage = 0;
    refreshBindings();
  });
  $$("[data-binding-page]").forEach(button => button.addEventListener("click", () => {
    state.bindingPage += button.dataset.bindingPage === "next" ? 1 : -1;
    renderBindings();
    $("#binding-list").scrollTop = 0;
  }));
  $("#scan-drift").addEventListener("click", scanDrift);
  $("#open-eval-case").addEventListener("click", () => $("#eval-case-dialog").showModal());
  $("#eval-case-form").addEventListener("submit", submitEvaluationCase);
  $("#run-evaluation").addEventListener("click", runEvaluation);
  $("#refresh-governance").addEventListener("click", refreshGovernance);
  $("#project-settings-form").addEventListener("submit", saveProjectSettings);
  $("#compare-runs").addEventListener("click", compareRuns);
  $("#run-repository").addEventListener("change", event => {
    const repository = state.repositories.find(item => item.id === event.target.value);
    $("#run-form").elements.commit_sha.value = repository?.head_commit || "";
  });
  $("#refresh-codex").addEventListener("click", refreshAll);
  $("#refresh-codex-bridge").addEventListener("click", refreshCodexBridge);
  $("#open-codex-execution").addEventListener("click", () => openCodexExecution());
  $("#codex-execution-work-item").addEventListener("change", event => {
    const item = state.workItems.find(value => value.id === event.target.value);
    const form = $("#codex-execution-form");
    form.elements.workspace_path.value = item?.workspace_path || "";
    form.elements.base_ref.value = item?.base_ref || "HEAD";
    renderCodexExecutionContext(item);
  });
  $("#evidence-search-form").addEventListener("submit", runSearch);
  $("#ask-about-repository").addEventListener("click", () => openRepositoryInRag());
  $("#codex-search-form").addEventListener("submit", runCodexSearch);
  $("#codex-session-filter").addEventListener("input", event => renderCodexSessions(event.target.value));
  $("#global-search").addEventListener("submit", event => {
    event.preventDefault();
    const value = new FormData(event.currentTarget).get("query");
    switchView("search");
    $('#evidence-search-form textarea[name="query"]').value = value;
    $("#evidence-search-form").requestSubmit();
  });
  document.addEventListener("keydown", event => {
    if (
      (event.metaKey || event.ctrlKey) &&
      event.key.toLowerCase() === "p" &&
      $("#view-repository").classList.contains("active")
    ) {
      event.preventDefault();
      $("#file-filter").focus();
      $("#file-filter").select();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $('.global-search input[name="query"]').focus();
    }
  });
  $("#browser-repository").addEventListener("change", event => {
    if (event.target.value) selectRepository(event.target.value);
    else {
      state.selectedRepository = null;
      state.files = [];
      renderFiles();
      $("#history-repository").value = "";
      renderGitHistoryLauncher(null);
      loadGitHistory("");
    }
  });
  $("#file-filter").addEventListener("input", event => renderFiles(event.target.value));
  $("#file-tree-collapse").addEventListener("click", () => {
    state.openCodeFolders.clear();
    $("#file-filter").value = "";
    renderFiles();
    $("#file-tree-collapse").focus({preventScroll:true});
  });
  $("#topic-filter")?.addEventListener("input", event => renderTopics(event.target.value));
  installRagWorkbenchEvents();
  installGraphWorkbenchEvents();
}

installEvents();
installWorkspaceLiveRefresh();
switchView(location.hash.slice(1) || "workspace");
refreshAll();
