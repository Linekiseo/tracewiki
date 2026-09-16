from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The legacy workbench remains available during the controlled React migration.
# Its contract tests intentionally target the preserved document, while the
# production entry point is covered separately below.
INDEX = ROOT / "web" / "legacy.html"
FRONTEND_ROOT = ROOT / "frontend"
SPA_INDEX = FRONTEND_ROOT / "index.html"
FRONTEND = FRONTEND_ROOT / "src"
RAG_SCRIPT = ROOT / "web" / "assets" / "rag-workbench.js"
GRAPH_SCRIPT = ROOT / "web" / "assets" / "graph-workbench.js"
SOURCE_SCRIPT = ROOT / "web" / "assets" / "source-workbenches.js"
CODEX_BOARD_SCRIPT = ROOT / "web" / "assets" / "codex-session-board.js"
DOCUMENT_IMPORT_SCRIPT = ROOT / "web" / "assets" / "document-import.js"
APP_SCRIPT = ROOT / "web" / "assets" / "app.js"
DESIGN_SYSTEM_STYLES = ROOT / "web" / "assets" / "design-system.css"
RAG_STYLES = ROOT / "web" / "assets" / "rag-workbench.css"
WORKBENCH_EXPERIENCE_SCRIPT = ROOT / "web" / "assets" / "workbench-experience.js"
WORKBENCH_EXPERIENCE_STYLES = ROOT / "web" / "assets" / "workbench-experience.css"
PROJECT_NETWORK_SCRIPT = ROOT / "web" / "assets" / "project-network.js"
PROJECT_NETWORK_MODEL_SCRIPT = ROOT / "web" / "assets" / "project-network-model.js"
PROJECT_NETWORK_STYLES = ROOT / "web" / "assets" / "project-network.css"


class UiContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.nav_views: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if element_id := values.get("id"):
            self.ids.add(element_id)
        if tag == "button" and (view := values.get("data-view")):
            self.nav_views.append(view)


def parse_index() -> UiContractParser:
    parser = UiContractParser()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser


def test_new_project_shell_is_the_production_entry_point() -> None:
    index = SPA_INDEX.read_text(encoding="utf-8")
    app = (FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    shell = (FRONTEND / "components" / "AppShell.tsx").read_text(encoding="utf-8")
    project_center = (FRONTEND / "features" / "projects" / "ProjectCenter.tsx").read_text(
        encoding="utf-8"
    )
    research_map = (FRONTEND / "features" / "workspace" / "ResearchMap.tsx").read_text(
        encoding="utf-8"
    )
    styles = (FRONTEND / "styles" / "app.css").read_text(encoding="utf-8")

    assert '<div id="root"></div>' in index
    assert "/projects" in app
    assert "/p/:projectId/overview" in app
    assert "/p/:projectId/map" in app
    assert "CommandPalette" in shell
    assert "CreateProjectDialog" in shell
    assert 'queryKey: ["projects"]' in project_center
    assert "project.repositories" in project_center
    assert "project.pending_reviews" in project_center
    assert "onWheel" in research_map
    assert "setPointerCapture" in research_map
    assert "backdrop-filter" not in styles
    assert "--surface:" in styles
    assert ':root[data-theme="dark"]' in styles


def test_primary_navigation_follows_research_information_architecture() -> None:
    parser = parse_index()

    assert parser.nav_views == [
        "workspace",
        "search",
        "graph",
        "repository",
        "codex",
        "codex-bridge",
        "experiments",
        "documents",
        "bindings",
        "drift",
        "governance",
    ]
    assert "ingestion" not in parser.nav_views
    assert "sources" not in parser.nav_views


def test_evidence_workbench_has_query_code_and_rag_surfaces() -> None:
    parser = parse_index()

    assert {
        "evidence-search-form",
        "rag-repository",
        "rag-workbench",
        "rag-file-list",
        "rag-code-content",
        "rag-code-repository-name",
        "rag-code-breadcrumb",
        "rag-code-size",
        "rag-file-collapse",
        "rag-file-summary",
        "rag-splitter",
        "rag-answer-content",
        "search-results",
        "evidence-inspector",
        "rag-advanced-scope",
        "rag-coverage-repositories",
        "rag-source-code-count",
        "rag-source-codex-count",
        "rag-source-experiment-count",
        "rag-source-document-count",
        "rag-codex-assets",
        "rag-experiment-assets",
        "rag-document-assets",
        "document-dialog",
        "document-files",
        "document-dropzone",
        "document-upload-queue",
        "document-file-count",
        "document-import-progress",
        "document-progress-bar",
        "document-submit",
        "rag-query-brief",
        "rag-graph-workspace",
        "rag-graph-query",
        "rag-graph-svg",
        "rag-graph-inspector",
        "rag-graph-categories",
        "rag-graph-context",
        "rag-graph-path-toggle",
        "rag-graph-progressive",
        "rag-graph-visible-slider",
        "rag-graph-visible-slider-value",
        "rag-graph-layout-toggle",
        "rag-graph-reveal-all",
        "rag-graph-download",
        "rag-graph-code-total",
        "rag-graph-codex-total",
        "rag-graph-overview-total",
        "rag-graph-research-total",
        "git-history-toggle",
        "git-history-popover",
        "git-history-close",
        "git-history-branch",
        "git-history-head",
        "git-history-count",
        "file-tree-collapse",
        "code-breadcrumb",
        "code-size",
        "codex-board-scene",
        "codex-board-links",
        "codex-board-nodes",
        "codex-board-overlay",
        "codex-board-visible-count",
        "codex-event-drawer",
        "codex-event-filter",
        "codex-event-list",
        "codex-event-range",
        "codex-timeline-fallback",
        "codex-related-filters",
        "codex-related-sources",
        "codex-related-count",
        "codex-related-pager",
        "binding-pager",
        "record-delete-dialog",
        "codex-execution-context",
    } <= parser.ids


def test_rag_script_scopes_queries_and_synchronizes_code_evidence() -> None:
    script = RAG_SCRIPT.read_text(encoding="utf-8")

    assert "project_id: projectId" in script
    assert "repository_ids: repositoryId ? [repositoryId] : []" in script
    assert "openCodeEvidenceInRag(firstCodeEvidence)" in script
    assert "/v1/code/file?repository_id=" in script
    assert "buildRagPathTree" in script
    assert "countRagTreeFiles" in script
    assert "renderRagCodeBreadcrumb" in script
    assert "revealRagTreeFile" in script
    assert "renderHighlightedCode" in script
    assert "normalizeCodeLanguage" in script
    assert "formatCodeBytes" in script
    assert "ragWorkbenchState.openRepositories.clear()" in script
    assert "setRagSourcePane(item.source)" in script
    assert 'setRagSourcePane("code")' in script
    assert "renderRagProjectAssets" in script
    assert "renderRagQueryBrief" in script
    assert "ragIntentDescription" in script
    assert 'splitter.addEventListener("pointermove"' in script
    assert 'splitter.addEventListener("keydown"' in script


def test_git_history_is_a_dismissible_floating_panel() -> None:
    script = SOURCE_SCRIPT.read_text(encoding="utf-8")

    assert "installGitHistoryPopover" in script
    assert "setGitHistoryOpen" in script
    assert 'event.key === "Escape"' in script
    assert 'event.target.closest(".git-history-flyout")' in script
    assert "renderGitHistoryLauncher" in script


def test_document_importer_supports_real_batch_upload_and_three_input_modes() -> None:
    script = DOCUMENT_IMPORT_SCRIPT.read_text(encoding="utf-8")

    assert "new XMLHttpRequest()" in script
    assert '"/v1/documents/upload-batch"' in script
    assert 'payload.append("files"' in script
    assert "MAX_BATCH_FILES" not in script
    assert "MAX_FILE_BYTES" not in script
    assert "文件超过 50 MB" not in script
    assert "单批最多" not in script
    assert "dataTransfer.files" in script
    assert "submitFiles" in script
    assert "submitPaths" in script
    assert "submitText" in script
    assert "renderQueue" in script


def test_code_browser_uses_hierarchical_tree_and_language_highlighting() -> None:
    script = APP_SCRIPT.read_text(encoding="utf-8")

    assert "buildCodeFileTree" in script
    assert "renderCodeTreeChildren" in script
    assert "openCodeFolders" in script
    assert "normalizeCodeLanguage" in script
    assert "highlightCodeLine" in script
    assert "renderHighlightedCode" in script
    assert "codeKeywordSets" in script
    assert "escapeHtml(value)" in script


def test_project_and_evidence_workbenches_follow_dark_theme_tokens() -> None:
    design_system = DESIGN_SYSTEM_STYLES.read_text(encoding="utf-8")
    rag_styles = RAG_STYLES.read_text(encoding="utf-8")

    assert ':root[data-theme="dark"] .workspace-filter input' in design_system
    assert ':root[data-theme="dark"] .source-health-list button' in design_system
    assert ':root[data-theme="dark"] .iteration-card' in design_system
    assert ':root[data-theme="dark"] .rag-question-deck' in rag_styles
    assert ':root[data-theme="dark"] .rag-code-surface' in rag_styles
    assert ':root[data-theme="dark"] .rag-evidence-surface' in rag_styles
    assert ':root[data-theme="dark"] .rag-answer-lead' in rag_styles
    assert "background: var(--surface-base)" in rag_styles
    assert "color: var(--text-strong)" in rag_styles


def test_project_network_is_data_driven_live_and_creatable() -> None:
    parser = parse_index()
    index = INDEX.read_text(encoding="utf-8")
    app_script = APP_SCRIPT.read_text(encoding="utf-8")
    network_script = PROJECT_NETWORK_SCRIPT.read_text(encoding="utf-8")
    model_script = PROJECT_NETWORK_MODEL_SCRIPT.read_text(encoding="utf-8")
    network_styles = PROJECT_NETWORK_STYLES.read_text(encoding="utf-8")

    assert {
        "research-add-node",
        "research-add-node-menu",
        "research-network-world",
        "research-network-node-layer",
        "research-network-edge-layer",
    } <= parser.ids
    assert "/assets/project-network-model.js" in index
    assert 'api("/v1/relations?limit=500")' in app_script
    assert "installWorkspaceLiveRefresh" in app_script
    assert 'document.body.dataset.currentView === "workspace"' in app_script
    assert "ProjectNetworkModel.build" in network_script
    assert 'data-add-research-node="topic"' in index
    assert 'data-add-research-node="iteration"' in index
    assert 'data-add-research-node="work"' in index
    assert 'data-add-research-node="experiment"' in index
    assert 'data-add-research-node="document"' in index
    assert "state.iterations" in model_script
    assert "state.workItems" in model_script
    assert "state.codexSessions" in model_script
    assert "state.repositories" in model_script
    assert "state.experiments" in model_script
    assert "state.documents" in model_script
    assert "state.relations" in model_script
    assert 'ui.expanded.has("all-relations")' in model_script
    assert "--research-focus: var(--accent)" in network_styles
    assert "--research-surface: var(--surface-raised)" in network_styles


def test_all_workbenches_use_readable_type_and_progressive_disclosure() -> None:
    script = WORKBENCH_EXPERIENCE_SCRIPT.read_text(encoding="utf-8")
    app_script = APP_SCRIPT.read_text(encoding="utf-8")
    styles = WORKBENCH_EXPERIENCE_STYLES.read_text(encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")

    assert "installWorkspaceInspector" in script
    assert "installRepositoryWorkbench" in script
    assert "installCodexWorkbench" in script
    assert "installEvidenceWorkbench" in script
    assert "installGenericInspectors" in script
    assert "installInspector" in script
    assert "openInspector" in script
    assert "closeInspector" in script
    assert "installSidebarCollapse" in script
    assert "createTabs" in script
    assert "--type-body: 14px" in styles
    assert "--type-control: 13px" in styles
    assert "--type-caption: 12px" in styles
    assert ".workbench-inspector.is-open" in styles
    assert ".workbench-tab.active" in styles
    assert ".app-shell.sidebar-collapsed" in styles
    assert ".codex-workbench.session-list-collapsed" in styles
    assert "grid-template-columns: 52px minmax(0, 1fr)" in styles
    assert ".panel-head > strong" in styles
    assert ".panel-head > span" in styles
    assert "#rag-workbench.evidence-layout-hide-files" in styles
    assert "#rag-workbench.evidence-layout-hide-code" in styles
    assert "#rag-workbench.evidence-layout-hide-answer" in styles
    assert "#view-graph .graph-studio-grid" in styles
    assert "grid-template-columns: minmax(0, 1fr)" in styles
    assert "grid-template-columns: minmax(300px, 38%) minmax(0, 1fr)" in styles
    assert ".research-workbench-grid" in styles
    assert ".repository-version-picker" in styles
    assert ".code-compare-layout" in styles
    assert ".bridge-main-grid" in styles
    assert "#view-repository #repository-table tr.selected" in styles
    assert ".binding-item.active" in styles
    assert "background: var(--surface-selected)" in styles
    assert ".list-pager" in styles
    assert ".inferred-context" in styles
    assert ".advanced-settings" in styles
    assert "workbench-drawer-backdrop" not in script
    assert "workbench-drawer-backdrop" not in styles
    inspector_styles = styles.split("/* Inspector is deliberately non-modal", 1)[1].split(
        ".sidebar-collapse-toggle", 1
    )[0]
    assert "backdrop-filter" not in inspector_styles
    assert '{id: "hide-files", label: "代码 + 回答"' in script
    assert '{id: "hide-code", label: "Files + 回答"' in script
    assert '{id: "hide-answer", label: "Files + 代码"' in script
    assert "document.body.append(shell)" in script
    assert 'id: "codex-related-inspector"' in script
    assert 'event.target.closest("[data-codex-node]")' in script
    assert "state.openInspector === panel" in script
    assert "state.openInspector === shell" in script
    assert "event.currentTarget.reset()" not in app_script
    assert "formElement.reset()" in app_script
    assert 'method:"DELETE"' in app_script
    assert "/assets/workbench-experience.css" in index
    assert "/assets/workbench-experience.js" in index


def test_visual_hierarchy_uses_tactile_surfaces_instead_of_glass_cards() -> None:
    design_system = DESIGN_SYSTEM_STYLES.read_text(encoding="utf-8")
    workbench_styles = WORKBENCH_EXPERIENCE_STYLES.read_text(encoding="utf-8")
    loaded_styles = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "web" / "assets").glob("*.css")
    )
    index = INDEX.read_text(encoding="utf-8")

    assert "--radius-sm: 2px" in design_system
    assert "--surface-canvas: #f1f3f5" in design_system
    assert "--surface-canvas: #0f141a" in design_system
    assert "--accent: #3767c6" in design_system
    assert "Research instrument visual system" in workbench_styles
    assert ".page-heading > div:first-child > p" in workbench_styles
    assert "#view-documents .document-layout" in workbench_styles
    assert ".research-asset-strip > div:first-child" in workbench_styles
    assert "backdrop-filter: blur" not in loaded_styles
    assert "<h1>证据工作台</h1>" in index
    assert "<h1>关系复核</h1>" in index


def test_evidence_file_tree_uses_consistent_readable_rows() -> None:
    styles = RAG_STYLES.read_text(encoding="utf-8")

    assert "grid-template-rows: 52px 50px minmax(0, 1fr)" in styles
    assert ".rag-tree-directory > summary" in styles
    assert ".rag-tree-file {" in styles
    assert "min-height: 38px" in styles
    assert "font-size: 13px" in styles
    assert ".rag-tree-repository > summary" in styles
    assert "min-height: 54px" in styles


def test_repository_index_tasks_use_compact_stage_summary() -> None:
    index = INDEX.read_text(encoding="utf-8")
    script = SOURCE_SCRIPT.read_text(encoding="utf-8")
    styles = WORKBENCH_EXPERIENCE_STYLES.read_text(encoding="utf-8")

    assert 'class="workflow-stage-rail"' in index
    assert 'class="workflow-stage-summary"' in index
    assert 'class="workflow-stats-grid"' in index
    assert 'class="workflow-technical-details"' in index
    assert "workflowDisplayProgress" in script
    assert "任务在${stage.label}阶段停止" in script
    assert ".workflow-stage-rail" in styles
    assert ".workflow-stats-grid" in styles
    detail = index.split('class="panel workflow-detail-panel"', 1)[1].split(
        '<div class="section-divider">', 1
    )[0]
    assert "parser-strip" not in detail
    assert "flow-node" not in detail


def test_codex_session_uses_bounded_operation_clusters_event_drawer_and_related_sources() -> None:
    script = CODEX_BOARD_SCRIPT.read_text(encoding="utf-8")

    assert "OPERATION_CLUSTERS" in script
    assert "clustersForTurn" in script
    assert "renderTurnNode" in script
    assert "renderClusterNode" in script
    assert "renderEventDrawer" in script
    assert "drawerEvents" in script
    assert "eventPageSize" in script
    assert "data-codex-cluster-open" in script
    assert "toggleAllTurnItems" in script
    assert "data-codex-item-expand" in script
    assert "data-codex-turn-cluster" in script
    assert 'kind:"serial"' in script
    assert 'viewport.addEventListener("wheel"' in script
    assert 'viewport.addEventListener("pointerdown"' in script
    assert "zoomAt" in script
    assert "fitBoard" in script
    assert "/v1/bindings/candidates?thread_id=" in script
    assert 'sources:["code", "experiment", "document"]' in script
    assert "relationLabel" in script
    assert "populateFallbackTurn" in script


def test_graph_workbench_uses_real_domains_and_distinguishes_semantic_edges() -> None:
    script = GRAPH_SCRIPT.read_text(encoding="utf-8")

    assert 'api("/v1/graph/stats")' in script
    assert "api(`/v1/graph?${params.toString()}`)" in script
    assert 'domain: "overview"' in script
    assert '"codex"' in script
    assert '"document"' in script
    assert '"research"' in script
    assert '"SEMANTIC_MATCH"' in script
    assert 'data.mode === "semantic"' in script
    assert "openCodeEvidenceInRag" in script
    assert "selectCodexThread" in script
    assert "selectClaim" in script
    assert 'switchView("documents")' in script
    assert "buildForceRagGraphPositions" in script
    assert '"contextmenu"' in script
    assert '"dblclick"' in script
    assert "findRagGraphPath" in script
    assert "downloadRagGraph" in script
    assert "hiddenNodeIds" in script
    assert "hiddenTypes" in script
    assert "pinnedNodeIds" in script
    assert "neighborExpansion" in script
    assert "visibleNodeCap" in script
    assert "rag-graph-visible-slider" in script
    assert "metadata?.total_nodes" in script
    assert "preserveVisibleTarget" in script
    assert "expandRagGraphNode" in script
    assert "/v1/graph/neighbors?" in script
    assert 'addEventListener("wheel"' in script
    assert "zoomRagGraphAtPoint" in script
    assert "ragGraphDisplayLabel" in script
    assert "ragGraphCaptionWidth" in script


def test_experiment_sources_are_context_actions_not_primary_pages() -> None:
    parser = parse_index()

    assert {
        "open-mlflow-from-experiments",
        "open-notebook-from-experiments",
        "experiment-source-count",
        "platform-source-list",
    } <= parser.ids


def test_react_shell_has_route_level_recovery_and_accessible_dialogs() -> None:
    app = (FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    ui = (FRONTEND / "components" / "ui.tsx").read_text(encoding="utf-8")

    assert "class RouteErrorBoundary" in app
    assert "RouteLoadingState" in app
    assert "RouteErrorState" in app
    assert "NotFoundState" in app
    assert 'path="*"' in app
    assert 'role="status"' in app
    assert 'role="alert"' in app
    assert "window.location.reload()" in app
    assert "useId()" in ui
    assert 'aria-modal="true"' in ui
    assert "aria-describedby" in ui
    assert 'event.key === "Escape"' in ui
    assert 'event.key !== "Tab"' in ui
    assert 'document.addEventListener("focusin"' in ui
    assert "previouslyFocused?.isConnected" in ui


def test_frontend_build_is_clean_verified_and_production_safe() -> None:
    frontend_root = ROOT / "frontend"
    package = (frontend_root / "package.json").read_text(encoding="utf-8")
    vite = (frontend_root / "vite.config.ts").read_text(encoding="utf-8")
    clean_build = (frontend_root / "scripts" / "clean-build.mjs").read_text(encoding="utf-8")

    assert "clean-build.mjs prepare" in package
    assert "vite build --config ./vite.config.ts" in package
    assert "clean-build.mjs verify" in package
    assert "RAG_FRONTEND_OUT_DIR" in vite
    assert "sourcemap: false" in vite
    assert "production-same-origin-fallbacks" in vite
    assert "Local backend fallback leaked" in vite
    assert "recursive: true" in clean_build
    assert 'const assetsDirectoryName = "app"' in clean_build
    assert 'const contractFileName = "build-contract.json"' in clean_build
    assert "sourceSha256" in clean_build
    assert "artifactsSha256" in clean_build
    assert "buildSha256" in clean_build
    assert "sourceMappingURL" in clean_build
    assert "127\\.0\\.0\\.1" in clean_build


def test_delivery_builds_the_frontend_instead_of_copying_prebuilt_spa() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "AS frontend-builder" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm test" in dockerfile
    assert "npm run typecheck" in dockerfile
    assert "RAG_FRONTEND_OUT_DIR=/frontend-dist npm run build" in dockerfile
    assert "COPY --from=frontend-builder /frontend-dist/ ./web/" in dockerfile
    assert "COPY web ./web" not in dockerfile
    assert "frontend-build: frontend-verify" in makefile
    assert "frontend-verify: frontend-test frontend-typecheck" in makefile
    assert "npm --prefix frontend test" in makefile
    assert "npm --prefix frontend run typecheck" in makefile
    assert "npm --prefix frontend run build" in makefile
    assert "build: frontend-build" in makefile
