(() => {
  "use strict";

  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {openInspector: null, trigger: null};

  const icon = name => `<i class="ph ph-${name}" aria-hidden="true"></i>`;

  function button(label, iconName, className = "button ghost") {
    const element = document.createElement("button");
    element.type = "button";
    element.className = className;
    element.innerHTML = `${icon(iconName)}${label ? `<span>${label}</span>` : ""}`;
    return element;
  }

  function closeInspector({restoreFocus = true} = {}) {
    if (!state.openInspector) return;
    const panel = state.openInspector;
    const trigger = state.trigger;
    panel.classList.remove("is-open");
    panel.setAttribute("aria-hidden", "true");
    trigger?.setAttribute("aria-expanded", "false");
    state.openInspector = null;
    state.trigger = null;
    if (restoreFocus) trigger?.focus({preventScroll: true});
  }

  function openInspector(panel, trigger) {
    if (!panel) return;
    if (state.openInspector === panel) {
      qs(".workbench-inspector-content", panel)?.scrollTo({top: 0, behavior: "instant"});
      return;
    }
    closeInspector({restoreFocus: false});
    state.openInspector = panel;
    state.trigger = trigger || null;
    panel.classList.add("is-open");
    panel.setAttribute("aria-hidden", "false");
    trigger?.setAttribute("aria-expanded", "true");
    qs(".workbench-inspector-content", panel)?.scrollTo({top: 0, behavior: "instant"});
    qs("[data-close-inspector]", panel)?.focus({preventScroll: true});
  }

  function installInspector(content, {
    id,
    title,
    eyebrow = "INSPECTOR",
    trigger = null,
    label = title,
    iconName = "sidebar-simple",
  }) {
    if (!content || content.dataset.inspectorReady === "true") return trigger;
    content.dataset.inspectorReady = "true";
    const shell = document.createElement("aside");
    shell.id = id;
    shell.className = "workbench-inspector";
    shell.setAttribute("aria-hidden", "true");
    shell.setAttribute("aria-label", title);
    /*
     * Inspectors are viewport-level surfaces. Keeping their shells inside a
     * grid made removed in-flow panes silently reserve or reorder columns on
     * unrelated pages. Mount them at the document surface instead.
     */
    document.body.append(shell);
    content.classList.add("workbench-inspector-content");

    const header = document.createElement("header");
    header.className = "workbench-inspector-head";
    header.innerHTML = `<div><small>${eyebrow}</small><strong>${title}</strong></div>`;
    const close = button("", "x", "workbench-inspector-close");
    close.dataset.closeInspector = "";
    close.setAttribute("aria-label", `关闭${title}`);
    close.addEventListener("click", () => closeInspector());
    header.append(close);
    shell.append(header, content);

    const control = trigger || button(label, iconName);
    control.classList.add("workbench-inspector-trigger");
    control.title ||= label;
    control.setAttribute("aria-controls", id);
    control.setAttribute("aria-expanded", "false");
    control.addEventListener("click", () => {
      if (state.openInspector === shell) closeInspector();
      else openInspector(shell, control);
    });
    return control;
  }

  window.WorkbenchInspector = {install: installInspector, open: openInspector, close: closeInspector};

  function installSidebarCollapse() {
    const shell = qs(".app-shell");
    const sidebar = qs(".sidebar");
    if (!shell || !sidebar) return;
    qsa(".nav-item", sidebar).forEach(item => {
      item.title ||= qs("span", item)?.textContent?.trim() || "";
    });
    let toggle = qs("#sidebar-collapse-toggle", sidebar);
    if (!toggle) {
      toggle = button("", "sidebar-simple", "sidebar-collapse-toggle");
      toggle.id = "sidebar-collapse-toggle";
      sidebar.append(toggle);
    }
    const apply = collapsed => {
      shell.classList.toggle("sidebar-collapsed", collapsed);
      sidebar.classList.toggle("is-collapsed", collapsed);
      toggle.innerHTML = icon(collapsed ? "sidebar" : "sidebar-simple");
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.setAttribute("aria-label", collapsed ? "展开主导航" : "折叠主导航");
      toggle.title = collapsed ? "展开主导航" : "折叠主导航";
      localStorage.setItem("rag-sidebar-collapsed", String(collapsed));
    };
    toggle.addEventListener("click", () => {
      apply(!shell.classList.contains("sidebar-collapsed"));
    });
    const savedPreference = localStorage.getItem("rag-sidebar-collapsed");
    apply(savedPreference === null ? true : savedPreference === "true");
  }

  function headingActions(view) {
    const heading = qs(".page-heading, .graph-page-heading", view);
    if (!heading) return null;
    let actions = qs(".heading-actions", heading);
    if (!actions) {
      actions = document.createElement("div");
      actions.className = "heading-actions";
      heading.append(actions);
    }
    return actions;
  }

  function createTabs(view, items, defaultId) {
    const tabs = document.createElement("nav");
    tabs.className = "workbench-tabs";
    tabs.setAttribute("role", "tablist");
    const activate = id => {
      items.forEach(item => {
        const active = item.id === id;
        item.elements.forEach(element => {
          if (element) element.hidden = !active;
        });
        item.control.classList.toggle("active", active);
        item.control.setAttribute("aria-selected", String(active));
      });
      view.dataset.workbenchTab = id;
    };
    items.forEach(item => {
      item.control = button(item.label, item.icon, "workbench-tab");
      item.control.setAttribute("role", "tab");
      item.control.addEventListener("click", () => activate(item.id));
      tabs.append(item.control);
    });
    activate(defaultId);
    return {tabs, activate};
  }

  function installWorkspaceInspector() {
    const view = qs("#view-workspace");
    const content = qs(".workspace-side-stack", view);
    const trigger = qs("#workspace-activity-toggle", view);
    if (!view || !content || !trigger) return;
    installInspector(content, {
      id: "workspace-activity-inspector",
      title: "活动与项目状态",
      eyebrow: "PROJECT ACTIVITY",
      trigger,
    });
  }

  function installRepositoryWorkbench() {
    const view = qs("#view-repository");
    const metrics = qs("#metric-grid", view);
    const repositories = qs(".ingestion-layout", view);
    const indexing = qs(".workflow-layout", view);
    const divider = qs(".section-divider", view);
    const browser = qs(".browser-layout", view);
    if (!view || !metrics || !repositories || !indexing || !divider || !browser) return;
    const tabSet = createTabs(
      view,
      [
        {id: "repositories", label: "仓库", icon: "git-branch", elements: [repositories]},
        {id: "code", label: "代码浏览", icon: "file-code", elements: [divider, browser]},
        {id: "indexing", label: "索引任务", icon: "stack", elements: [indexing]},
      ],
      "repositories",
    );
    metrics.after(tabSet.tabs);

    const detail = qs("#source-detail", view);
    const detailTrigger = button("仓库详情", "sidebar-simple");
    headingActions(view)?.prepend(detailTrigger);
    installInspector(detail, {
      id: "repository-detail-inspector",
      title: "仓库详情",
      trigger: detailTrigger,
    });

    const symbols = qs(".symbol-panel", view);
    const symbolTrigger = button("Symbols", "brackets-curly", "button ghost compact-button");
    qs(".repository-browser-tools", view)?.prepend(symbolTrigger);
    installInspector(symbols, {
      id: "repository-symbol-inspector",
      title: "Symbol Outline",
      trigger: symbolTrigger,
    });
    view.addEventListener("click", event => {
      if (event.target.closest("#repository-table tr[data-repo]")) {
        openInspector(qs("#repository-detail-inspector"), detailTrigger);
      }
      if (event.target.closest("#browse-repository")) tabSet.activate("code");
    });
  }

  function installCodexWorkbench() {
    const view = qs("#view-codex");
    const related = qs(".codex-evidence-panel", view);
    const actions = headingActions(view);
    if (view && related && actions) {
      const trigger = button("关联来源", "link");
      actions.prepend(trigger);
      installInspector(related, {
        id: "codex-related-inspector",
        title: "跨来源关联",
        trigger,
      });
      const board = qs("#codex-board-nodes", view);
      if (board && board.dataset.relatedInspectorReady !== "true") {
        board.dataset.relatedInspectorReady = "true";
        board.addEventListener("click", event => {
          if (event.target.closest("[data-codex-node]")) {
            setTimeout(() => openInspector(qs("#codex-related-inspector"), trigger), 0);
          }
        });
      }
    }
    const workbench = qs(".codex-workbench", view);
    const sessions = qs(".codex-session-panel", view);
    const header = qs(".panel-head", sessions);
    if (!workbench || !sessions || !header || qs("#codex-session-toggle", sessions)) return;
    const toggle = button("", "sidebar-simple", "codex-session-toggle");
    toggle.id = "codex-session-toggle";
    header.append(toggle);
    const apply = collapsed => {
      workbench.classList.toggle("session-list-collapsed", collapsed);
      sessions.classList.toggle("is-collapsed", collapsed);
      toggle.innerHTML = icon(collapsed ? "sidebar" : "sidebar-simple");
      toggle.setAttribute("aria-expanded", String(!collapsed));
      toggle.setAttribute("aria-label", collapsed ? "展开会话列表" : "折叠会话列表");
      localStorage.setItem("rag-codex-sessions-collapsed", String(collapsed));
    };
    toggle.addEventListener("click", () => apply(!sessions.classList.contains("is-collapsed")));
    apply(localStorage.getItem("rag-codex-sessions-collapsed") === "true");
  }

  function installEvidenceWorkbench() {
    const view = qs("#view-search");
    const workbench = qs("#rag-workbench", view);
    const question = qs(".rag-question-deck", view);
    if (!view || !workbench || !question || qs("#rag-layout-switcher", view)) return;
    const modes = [
      {id: "three", label: "全景", icon: "columns"},
      {id: "hide-files", label: "代码 + 回答", icon: "file-code"},
      {id: "hide-code", label: "Files + 回答", icon: "folder-open"},
      {id: "hide-answer", label: "Files + 代码", icon: "brackets-curly"},
    ];
    const toolbar = document.createElement("nav");
    toolbar.id = "rag-layout-switcher";
    toolbar.className = "rag-layout-switcher";
    toolbar.setAttribute("aria-label", "证据工作台布局");
    const apply = mode => {
      const selected = modes.some(item => item.id === mode) ? mode : "three";
      workbench.dataset.evidenceLayout = selected;
      workbench.className = workbench.className
        .split(" ")
        .filter(name => !name.startsWith("evidence-layout-"))
        .concat(`evidence-layout-${selected}`)
        .join(" ");
      qsa("[data-evidence-layout]", toolbar).forEach(control => {
        control.classList.toggle("active", control.dataset.evidenceLayout === selected);
      });
      localStorage.setItem("rag-evidence-layout", selected);
    };
    modes.forEach(item => {
      const control = button(item.label, item.icon, "rag-layout-button");
      control.dataset.evidenceLayout = item.id;
      control.addEventListener("click", () => apply(item.id));
      toolbar.append(control);
    });
    question.after(toolbar);
    apply(localStorage.getItem("rag-evidence-layout") || "three");
  }

  function installGenericInspectors() {
    const definitions = [
      ["#view-documents", "#claim-detail-panel", "Claim 验证", "shield-check"],
      ["#view-graph", ".graph-filter-rail", "筛选与定位", "funnel"],
      ["#view-graph", "#rag-graph-inspector", "节点详情", "sidebar-simple"],
    ];
    definitions.forEach(([viewSelector, panelSelector, title, iconName], index) => {
      const view = qs(viewSelector);
      const content = qs(panelSelector, view);
      const actions = headingActions(view);
      if (!view || !content || !actions) return;
      const trigger = button(title, iconName);
      actions.prepend(trigger);
      installInspector(content, {
        id: `context-inspector-${index}`,
        title,
        trigger,
      });
      if (title === "节点详情") {
        qs("#rag-graph-svg", view)?.addEventListener("click", event => {
          if (event.target.closest(".rag-graph-node")) {
            setTimeout(() => openInspector(qs(`#context-inspector-${index}`), trigger), 0);
          }
        });
      }
      if (title === "Claim 验证") {
        qs("#claim-list", view)?.addEventListener("click", event => {
          if (event.target.closest(".claim-card")) {
            openInspector(qs(`#context-inspector-${index}`), trigger);
          }
        });
      }
    });
  }

  function installMetricSummaries() {
    qsa(".metric-grid, .drift-summary").forEach(grid => {
      grid.classList.add("compact-summary-strip");
    });
  }

  function install() {
    installSidebarCollapse();
    installMetricSummaries();
    installWorkspaceInspector();
    installRepositoryWorkbench();
    installCodexWorkbench();
    installEvidenceWorkbench();
    installGenericInspectors();
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && state.openInspector) {
        event.preventDefault();
        closeInspector();
      }
    });
    qsa("[data-view]").forEach(control => {
      control.addEventListener("click", () => closeInspector({restoreFocus: false}));
    });
  }

  document.addEventListener("DOMContentLoaded", install);
})();
