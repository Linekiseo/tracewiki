import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  Beaker,
  BookOpen,
  Boxes,
  Braces,
  Check,
  ChevronDown,
  Code2,
  FileText,
  FolderGit2,
  Home,
  KeyRound,
  LibraryBig,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  Moon,
  Network,
  Play,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  Sun,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { api, apiCredentials } from "../lib/api";
import type { Project } from "../lib/types";
import { CommandPalette } from "../features/search/CommandPalette";
import { CreateProjectDialog } from "../features/projects/CreateProjectDialog";
import { Button } from "./ui";
import { useDesktopRuntime, type DesktopConnectionMode } from "../desktop/runtime";

const traceWikiMarkUrl = new URL("../assets/tracewiki-mark.png", import.meta.url).href;

const globalNavigation = [
  { key: "projects", label: "项目", icon: Boxes, root: true },
  { key: "search", label: "查询", icon: Search, global: true },
  { key: "map", label: "图谱", icon: Network, context: true },
  { key: "wiki", label: "知识库", icon: LibraryBig, context: true },
  { key: "agents", label: "智能体", icon: Bot, context: true },
] as const;

const workspaceGroups = [
  {
    label: "理解与查询",
    items: [
      { key: "overview", label: "项目总览", icon: Home },
      { key: "wiki", label: "Wiki 知识中枢", icon: LibraryBig },
      { key: "map", label: "关系与影响网络", icon: Network },
    ],
  },
  {
    label: "研发证据",
    items: [
      { key: "code", label: "代码与版本", icon: Code2 },
      { key: "sessions", label: "研发会话", icon: MessageSquareText },
      { key: "documents", label: "科研文档", icon: FileText },
      { key: "experiments", label: "实验与数据", icon: Beaker },
    ],
  },
  {
    label: "执行与治理",
    items: [
      { key: "agents", label: "智能体控制面", icon: Bot },
      { key: "evidence", label: "证据工作台", icon: BookOpen },
      { key: "relations", label: "关系复核", icon: ShieldCheck },
      { key: "codex", label: "Codex 联动", icon: Braces },
    ],
  },
] as const;

const moduleTitles: Record<string, string> = {
  overview: "项目总览",
  map: "关系图谱",
  wiki: "Wiki 知识中枢",
  code: "代码与版本",
  sessions: "研发会话",
  documents: "科研文档",
  experiments: "实验与数据",
  evidence: "证据工作台",
  relations: "关系复核",
  codex: "Codex 联动",
  agents: "智能体控制面",
  ops: "RAG 运维状态",
  search: "全局搜索",
};

function useCurrentProject(projects: Project[]) {
  const location = useLocation();
  const match = location.pathname.match(/^\/p\/([^/]+)(?:\/([^/]+))?/);
  const searchProjectId =
    location.pathname === "/search"
      ? new URLSearchParams(location.search).get("project")
      : null;
  const projectId = match
    ? decodeURIComponent(match[1])
    : searchProjectId && searchProjectId !== "all"
      ? searchProjectId
      : null;
  const module =
    match?.[2] ||
    (location.pathname === "/search"
      ? "search"
      : location.pathname === "/ops"
        ? "ops"
        : "");
  return {
    projectId,
    module,
    project: projects.find((item) => item.id === projectId) || null,
  };
}

export function AppShell() {
  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: api.projects.list,
  });
  const projects = projectsQuery.data || [];
  const { projectId, module, project } = useCurrentProject(projects);
  const codexStatusQuery = useQuery({
    queryKey: ["codex-bridge-status", projectId],
    queryFn: () => api.codexBridge.status(projectId || ""),
    enabled: Boolean(projectId),
    refetchInterval: 20_000,
  });
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [tokenDraft, setTokenDraft] = useState("");
  const [tokenConfigured, setTokenConfigured] = useState(
    apiCredentials.hasToken,
  );
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [railCollapsed, setRailCollapsed] = useState(
    () => window.localStorage.getItem("rag_workspace_rail") === "collapsed",
  );
  const [theme, setTheme] = useState(
    () =>
      (typeof window !== "undefined"
        ? window.localStorage.getItem("rag_theme")
        : null) || "light",
  );
  const navigate = useNavigate();
  const desktopRuntime = useDesktopRuntime();
  const [desktopBaseDraft, setDesktopBaseDraft] = useState("");
  const [desktopModeDraft, setDesktopModeDraft] = useState<DesktopConnectionMode>("managed_local");
  const [desktopProfileError, setDesktopProfileError] = useState("");
  const [llmBaseDraft, setLLMBaseDraft] = useState("");
  const [llmModelDraft, setLLMModelDraft] = useState("");
  const [llmKeyDraft, setLLMKeyDraft] = useState("");
  const [llmProfileMessage, setLLMProfileMessage] = useState("");
  const [llmTesting, setLLMTesting] = useState(false);
  const projectWorkspace = String(
    project?.settings?.workspace_path || "",
  ).trim();
  const codexCanLaunch =
    !codexStatusQuery.isError &&
    codexStatusQuery.data?.availability === "ready" &&
    Boolean(projectWorkspace);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("rag_theme", theme);
  }, [theme]);

  useEffect(() => {
    window.localStorage.setItem("rag_workspace_rail", railCollapsed ? "collapsed" : "expanded");
  }, [railCollapsed]);

  useEffect(() => {
    if (!settingsOpen || !desktopRuntime.bootstrap) return;
    setDesktopBaseDraft(desktopRuntime.bootstrap.profile.base_url);
    setDesktopModeDraft(desktopRuntime.bootstrap.profile.mode);
    setDesktopProfileError("");
    setLLMBaseDraft(desktopRuntime.llmProfile?.base_url || "https://api.openai.com/v1");
    setLLMModelDraft(desktopRuntime.llmProfile?.model || "");
    setLLMKeyDraft("");
    setLLMProfileMessage("");
  }, [desktopRuntime.bootstrap, desktopRuntime.llmProfile, settingsOpen]);

  useEffect(() => {
    setProjectMenuOpen(false);
  }, [projectId, module]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const availableProjects = useMemo(
    () => projects.filter((item) => item.status !== "archived"),
    [projects],
  );
  const moduleTitle = moduleTitles[module] || "项目";

  return (
    <div className={`app-shell ${railCollapsed ? "app-shell--rail-collapsed" : ""}`}>
      <aside className="icon-rail">
        <Link className="brand-mark" to="/projects" aria-label="TraceWiki 首页" title="TraceWiki · 循证知识工作台">
          <img src={traceWikiMarkUrl} alt="" />
        </Link>
        <nav aria-label="系统导航">
          {globalNavigation.map((item) => {
            const to =
              "root" in item && item.root
                ? "/projects"
                : "global" in item && item.global
                  ? projectId
                    ? `/search?project=${encodeURIComponent(projectId)}`
                    : "/search"
                : projectId
                  ? `/p/${encodeURIComponent(projectId)}/${item.key}`
                  : "/projects";
            return (
              <NavLink
                key={item.key}
                className={({ isActive }) =>
                  `icon-rail__item ${"context" in item && item.context ? "icon-rail__item--context" : ""} ${isActive ? "is-active" : ""}`
                }
                to={to}
                aria-label={item.label}
                title={projectId || "请先选择项目"}
              >
                <item.icon size={21} strokeWidth={1.75} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
        <div className="icon-rail__bottom">
          <NavLink
            className={({ isActive }) =>
              `icon-rail__item icon-rail__ops ${isActive ? "is-active" : ""}`
            }
            to={projectId ? `/p/${encodeURIComponent(projectId)}/ops` : "/ops"}
            aria-label="RAG 运维状态"
            title="只读 RAG 运维状态"
          >
            <Activity size={20} />
            <span>状态</span>
          </NavLink>
          <NavLink
            className={({ isActive }) =>
              `icon-rail__item icon-rail__codex ${isActive ? "is-active" : ""}`
            }
            to={
              projectId
                ? `/p/${encodeURIComponent(projectId)}/codex`
                : "/projects"
            }
            aria-label="Codex 联动"
          >
            <Braces size={20} />
            <span>Codex</span>
          </NavLink>
          <button
            aria-label="设置请求凭据"
            title="设置请求凭据"
            onClick={() => {
              setTokenDraft("");
              setTokenConfigured(apiCredentials.hasToken());
              setSettingsOpen(true);
            }}
          >
            <Settings size={20} />
          </button>
          <div className="user-avatar">研</div>
        </div>
      </aside>

      <aside className="project-rail" aria-label="项目工作区导航">
        <div className="project-rail__top">
          <button
            className="project-switcher"
            type="button"
            onClick={() => setProjectMenuOpen((open) => !open)}
            aria-label="切换当前项目"
          >
            <span className="project-switcher__icon"><FolderGit2 size={17} /></span>
            <span>
              <strong>{project?.name || "项目工作区"}</strong>
              <small>{project ? moduleTitle : "先选择一个项目"}</small>
            </span>
            <ChevronDown size={15} />
          </button>
          <button
            className="rail-toggle"
            type="button"
            aria-label={railCollapsed ? "展开项目导航" : "收起项目导航"}
            onClick={() => setRailCollapsed((collapsed) => !collapsed)}
          >
            {railCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
        </div>
        <div className="project-rail__scroll">
          {workspaceGroups.map((group) => (
            <section className="rail-section" key={group.label}>
              <header>{group.label}</header>
              {group.items.map((item) => {
                const to = projectId ? `/p/${encodeURIComponent(projectId)}/${item.key}` : "/projects";
                return (
                  <NavLink className={({ isActive }) => `rail-link ${isActive ? "is-active" : ""}`} key={item.key} to={to}>
                    <item.icon size={17} />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </section>
          ))}
        </div>
        <footer className="project-rail__footer">
          <button
            type="button"
            className={`system-health system-health--${desktopRuntime.health}`}
            onClick={() => void desktopRuntime.refreshHealth()}
            title="验证桌面端与 RAG 服务的连接"
          >
            <span />
            <span>
              <strong>{desktopRuntime.isDesktop ? "桌面 RAG" : "RAG 服务"}</strong>
              <small>{desktopRuntime.healthDetail}</small>
            </span>
          </button>
        </footer>
      </aside>

      <div className="app-stage">
        <header className="topbar">
          <div className="project-context">
            <button
              className="topbar-project"
              onClick={() => setProjectMenuOpen((open) => !open)}
              aria-expanded={projectMenuOpen}
              aria-label="切换项目"
            >
              <span>
                <FolderGit2 size={17} />
              </span>
              <strong>{project?.name || "全部项目"}</strong>
              <ChevronDown size={15} />
            </button>
            {projectId || module === "ops" ? (
              <>
                <span className="project-context__separator">/</span>
                <span className="project-context__module">{moduleTitle}</span>
              </>
            ) : null}
            {projectMenuOpen ? (
              <section className="project-menu" aria-label="项目列表">
                <header>
                  <strong>切换项目</strong>
                  <button onClick={() => setCreateOpen(true)}>
                    <Plus size={15} />
                    新建
                  </button>
                </header>
                <div>
                  {availableProjects.map((item) => (
                    <button
                      className={item.id === projectId ? "is-active" : ""}
                      key={item.id}
                      onClick={() =>
                        navigate(`/p/${encodeURIComponent(item.id)}/overview`)
                      }
                    >
                      <span>{item.name.slice(0, 1).toUpperCase()}</span>
                      <span>
                        <strong>{item.name}</strong>
                        <small>
                          {item.current_topic ||
                            `${item.topic_count} 个研究主题`}
                        </small>
                      </span>
                      {item.id === projectId ? <Check size={15} /> : null}
                    </button>
                  ))}
                </div>
                <Link to="/projects">查看全部项目</Link>
              </section>
            ) : null}
          </div>

          <button
            className="global-search"
            onClick={() => setPaletteOpen(true)}
          >
            <Search size={17} />
            <span>搜索项目、节点、文件、会话、实验…</span>
            <kbd>⌘ K</kbd>
          </button>

          <div className="topbar__actions">
            {desktopRuntime.isDesktop ? (
              <button
                type="button"
                className={`desktop-health-pill desktop-health-pill--${desktopRuntime.health}`}
                onClick={() => setSettingsOpen(true)}
              >
                <span />
                {desktopRuntime.health === "ready" ? "本机服务已连接" : desktopRuntime.health === "checking" ? "正在连接" : "服务离线"}
              </button>
            ) : null}
            <Button
              variant="quiet"
              className="icon-button"
              onClick={() => setTheme(theme === "light" ? "dark" : "light")}
              aria-label={theme === "light" ? "切换深色模式" : "切换浅色模式"}
            >
              {theme === "light" ? <Moon size={19} /> : <Sun size={19} />}
            </Button>
            <Button
              variant="primary"
              disabled={Boolean(projectId) && !codexCanLaunch}
              title={
                projectId && !codexCanLaunch
                  ? "Codex 尚未连接，或项目工作区未配置"
                  : undefined
              }
              onClick={() => {
                if (!projectId) {
                  setCreateOpen(true);
                  return;
                }
                if (!codexCanLaunch) return;
                const prompt = encodeURIComponent(
                  `读取科研项目 ${projectId} 的当前上下文，列出下一步可执行任务并等待我确认。`,
                );
                window.location.href = `codex://new?path=${encodeURIComponent(projectWorkspace)}&prompt=${prompt}`;
              }}
            >
              {projectId ? <Play size={17} /> : <Plus size={17} />}
              {projectId ? "交给 Codex" : "新建项目"}
            </Button>
          </div>
        </header>
        <main className="app-content">
          <Outlet />
        </main>
      </div>

      <CommandPalette
        open={paletteOpen}
        projects={projects}
        currentProject={project}
        onClose={() => setPaletteOpen(false)}
      />
      <CreateProjectDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
      />
      {settingsOpen ? (
        <div
          className="dialog-layer credential-layer"
          role="presentation"
          onMouseDown={() => setSettingsOpen(false)}
        >
          <section
            className="dialog credential-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="credential-dialog-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="dialog__header">
              <div>
                <h2 id="credential-dialog-title">桌面安全与智能模型</h2>
                <p>管理 RAG 服务连接、访问凭据与查询理解模型。</p>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="关闭请求凭据设置"
                onClick={() => setSettingsOpen(false)}
              >
                <X size={18} />
              </button>
            </header>
            <form
              className="form credential-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (!tokenDraft.trim()) return;
                setTokenConfigured(apiCredentials.saveToken(tokenDraft));
                setTokenDraft("");
              }}
            >
              <div className="credential-status">
                <KeyRound size={17} />
                <div>
                  <strong>
                    {tokenConfigured ? "请求 token 已配置" : "未配置请求 token"}
                  </strong>
                  <p>
                    此状态仅表示浏览器会附带凭据，不证明身份可信或拥有项目权限。
                  </p>
                </div>
              </div>
              <label>
                <span>新增或替换 token</span>
                <input
                  type="password"
                  autoComplete="off"
                  aria-label="API 请求 token"
                  value={tokenDraft}
                  onChange={(event) => setTokenDraft(event.target.value)}
                  placeholder="输入后保存；现有值不会回显"
                />
                <em>仅保存在当前浏览器的 localStorage；请勿在共享设备使用。</em>
              </label>
              <footer>
                {tokenConfigured ? (
                  <Button
                    type="button"
                    variant="danger"
                    onClick={() => {
                      apiCredentials.clearToken();
                      setTokenConfigured(false);
                      setTokenDraft("");
                    }}
                  >
                    清除 token
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setSettingsOpen(false)}
                >
                  取消
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  disabled={!tokenDraft.trim()}
                >
                  保存 token
                </Button>
              </footer>
            </form>
            {desktopRuntime.isDesktop && desktopRuntime.bootstrap ? (
              <>
              <section className="desktop-connection-form" aria-label="桌面服务连接">
                <div>
                  <strong>桌面 RAG 服务</strong>
                  <small>{desktopRuntime.healthDetail}</small>
                </div>
                <label>
                  <span>连接方式</span>
                  <select value={desktopModeDraft} onChange={(event) => setDesktopModeDraft(event.target.value as DesktopConnectionMode)}>
                    <option value="managed_local">本机隔离服务</option>
                    <option value="remote">HTTPS 远程服务</option>
                  </select>
                </label>
                <label>
                  <span>服务地址</span>
                  <input value={desktopBaseDraft} onChange={(event) => setDesktopBaseDraft(event.target.value)} placeholder="http://127.0.0.1:8765" />
                </label>
                {desktopProfileError ? <p role="alert">{desktopProfileError}</p> : null}
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    setDesktopProfileError("");
                    void desktopRuntime.saveProfile({
                      schema: "desktop-connection-profile-v1",
                      mode: desktopModeDraft,
                      base_url: desktopBaseDraft,
                      last_project_id: projectId || desktopRuntime.bootstrap?.profile.last_project_id || null,
                    }).catch(() => setDesktopProfileError("地址不符合安全规则：本机仅允许 loopback HTTP，远程必须使用 HTTPS。"));
                  }}
                >
                  保存并验证连接
                </Button>
              </section>
              <section className="desktop-connection-form desktop-llm-form" aria-label="智能模型配置">
                <div>
                  <strong>查询理解与受约束生成</strong>
                  <small>{desktopRuntime.llmDetail}</small>
                </div>
                <label>
                  <span>Provider</span>
                  <select value="openai_compatible" disabled>
                    <option value="openai_compatible">OpenAI Compatible</option>
                  </select>
                </label>
                <label>
                  <span>API Base URL</span>
                  <input
                    value={llmBaseDraft}
                    onChange={(event) => setLLMBaseDraft(event.target.value)}
                    placeholder="https://api.openai.com/v1"
                  />
                </label>
                <label>
                  <span>模型 ID</span>
                  <input
                    value={llmModelDraft}
                    onChange={(event) => setLLMModelDraft(event.target.value)}
                    placeholder="输入支持结构化 JSON 输出的模型"
                  />
                </label>
                <label>
                  <span>{desktopRuntime.llmProfile?.key_stored ? "替换 API Key" : "API Key"}</span>
                  <input
                    type="password"
                    autoComplete="off"
                    value={llmKeyDraft}
                    onChange={(event) => setLLMKeyDraft(event.target.value)}
                    placeholder={desktopRuntime.llmProfile?.key_stored ? "现有密钥不会回显；留空表示继续使用" : "仅保存到系统钥匙串"}
                  />
                  <em>密钥由 macOS Keychain / Windows Credential Manager 保存，不写入项目、浏览器存储或证据库。</em>
                </label>
                {llmProfileMessage ? <p role="status">{llmProfileMessage}</p> : null}
                <div className="desktop-llm-form__actions">
                  {desktopRuntime.llmProfile ? (
                    <Button
                      type="button"
                      variant="danger"
                      onClick={() => {
                        if (!projectId) return;
                        setLLMProfileMessage("");
                        void desktopRuntime.deleteLLMProfile(projectId)
                          .catch(() => setLLMProfileMessage("无法从系统钥匙串删除模型配置。"));
                      }}
                    >
                      删除配置
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={!projectId || desktopRuntime.llmStatus !== "ready" || llmTesting}
                    onClick={() => {
                      if (!projectId) return;
                      setLLMTesting(true);
                      setLLMProfileMessage("");
                      void api.ai.test(projectId)
                        .then(() => setLLMProfileMessage("连接测试通过：结构化查询理解与生成接口可用。"))
                        .catch(() => setLLMProfileMessage("连接测试失败；请核对地址、模型权限与 API Key。"))
                        .finally(() => setLLMTesting(false));
                    }}
                  >
                    {llmTesting ? "测试中" : "测试连接"}
                  </Button>
                  <Button
                    type="button"
                    variant="primary"
                    disabled={!projectId || !llmBaseDraft.trim() || !llmModelDraft.trim() || (!llmKeyDraft.trim() && !desktopRuntime.llmProfile?.key_stored)}
                    onClick={() => {
                      if (!projectId) {
                        setLLMProfileMessage("请先进入一个项目，再为该项目配置模型。");
                        return;
                      }
                      setLLMProfileMessage("");
                      void desktopRuntime.saveLLMProfile({
                        schema: "desktop-llm-profile-v1",
                        project_id: projectId,
                        provider: "openai_compatible",
                        base_url: llmBaseDraft,
                        model: llmModelDraft,
                        api_key: llmKeyDraft.trim() || undefined,
                      })
                        .then(() => setLLMKeyDraft(""))
                        .catch(() => setLLMProfileMessage("配置保存失败：远程地址必须使用 HTTPS，且系统钥匙串必须可用。"));
                    }}
                  >
                    保存到系统钥匙串
                  </Button>
                </div>
              </section>
              </>
            ) : null}
          </section>
        </div>
      ) : null}
    </div>
  );
}
