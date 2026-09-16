import {
  ArrowRight,
  Beaker,
  Code2,
  FileSearch,
  FileText,
  FolderGit2,
  LibraryBig,
  MessageSquareText,
  Network,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { Project } from "../../lib/types";

const destinations = [
  { label: "项目中心", keywords: "全部 项目 切换", path: "/projects", icon: FolderGit2 },
  { label: "关系图谱", keywords: "节点 关系 图谱", module: "map", icon: Network },
  { label: "Wiki 知识中枢", keywords: "wiki 知识 导航 证据 智能检索", module: "wiki", icon: LibraryBig },
  { label: "代码与版本", keywords: "代码 仓库 分支 commit", module: "code", icon: Code2 },
  { label: "研发会话", keywords: "codex 会话 thread", module: "sessions", icon: MessageSquareText },
  { label: "实验与数据", keywords: "实验 指标 run", module: "experiments", icon: Beaker },
  { label: "科研文档", keywords: "文献 论文 文档", module: "documents", icon: FileText },
] as const;

export function CommandPalette({
  open,
  projects,
  currentProject,
  onClose,
}: {
  open: boolean;
  projects: Project[];
  currentProject: Project | null;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const normalized = query.trim().toLowerCase();

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  const projectMatches = useMemo(
    () =>
      projects
        .filter((item) =>
          `${item.name} ${item.description} ${item.current_topic || ""}`
            .toLowerCase()
            .includes(normalized),
        )
        .slice(0, 5),
    [projects, normalized],
  );
  const destinationMatches = destinations.filter((item) =>
    `${item.label} ${item.keywords}`.toLowerCase().includes(normalized),
  );

  const go = (path: string) => {
    navigate(path);
    onClose();
  };

  if (!open) return null;
  return (
    <div className="command-layer" role="presentation" onMouseDown={onClose}>
      <section
        className="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label="全局搜索"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <form
          className="command-input"
          onSubmit={(event) => {
            event.preventDefault();
            if (!query.trim()) return;
            const params = new URLSearchParams({ q: query.trim() });
            if (currentProject) params.set("project", currentProject.id);
            go(`/search?${params.toString()}`);
          }}
        >
          <Search size={19} />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索项目，或输入要查找的代码、会话、实验与文档"
          />
          <kbd>ESC</kbd>
        </form>
        <div className="command-results">
          {query.trim() ? (
            <button
              className="command-search-action"
              onClick={() => {
                const params = new URLSearchParams({ q: query.trim() });
                if (currentProject) params.set("project", currentProject.id);
                go(`/search?${params.toString()}`);
              }}
            >
              <FileSearch size={19} />
              <span>
                <strong>搜索“{query.trim()}”</strong>
                <small>
                  {currentProject
                    ? `在 ${currentProject.name} 中查询全部来源`
                    : "打开查询页后显式选择项目"}
                </small>
              </span>
              <ArrowRight size={17} />
            </button>
          ) : null}

          {projectMatches.length ? (
            <section className="command-group">
              <h3>项目</h3>
              {projectMatches.map((project) => (
                <button
                  key={project.id}
                  onClick={() => go(`/p/${encodeURIComponent(project.id)}/overview`)}
                >
                  <span className="project-glyph">{project.name.slice(0, 1)}</span>
                  <span><strong>{project.name}</strong><small>{project.current_topic || "项目概览"}</small></span>
                  <ArrowRight size={16} />
                </button>
              ))}
            </section>
          ) : null}

          {destinationMatches.length ? (
            <section className="command-group">
              <h3>前往</h3>
              {destinationMatches.map((item) => {
                const path =
                  "path" in item
                    ? item.path
                    : currentProject
                      ? `/p/${encodeURIComponent(currentProject.id)}/${item.module}`
                      : "/projects";
                return (
                  <button key={item.label} onClick={() => go(path)}>
                    <item.icon size={18} />
                    <span><strong>{item.label}</strong><small>{"module" in item && !currentProject ? "请先选择项目" : item.keywords}</small></span>
                    <ArrowRight size={16} />
                  </button>
                );
              })}
            </section>
          ) : null}
        </div>
        <footer className="command-footer">
          <span><kbd>↵</kbd> 打开</span>
          <span><kbd>ESC</kbd> 关闭</span>
          <span>搜索结果遵循当前项目权限</span>
        </footer>
      </section>
    </div>
  );
}
