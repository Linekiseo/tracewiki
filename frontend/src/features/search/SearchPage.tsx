import { useQueries, useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Beaker,
  BookOpen,
  CircleHelp,
  Code2,
  FileText,
  MessageSquareText,
  NotebookTabs,
  Search,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../../lib/api";
import type { Project, SearchResult, SearchSource } from "../../lib/types";
import { EmptyState, Inspector } from "../../components/ui";
import {
  cleanEvidenceText,
  humanizeSearchTitle,
  presentSearchSnippet,
} from "../../lib/presentation";
import { TrustedQueryPanel } from "./TrustedQueryPanel";
import { safeMetadataValue, sanitizeDisplayText } from "./queryContract";

type SourceMeta = {
  label: string;
  icon: LucideIcon;
};

const sourceMeta = {
  code: { label: "代码", icon: Code2 },
  codex: { label: "研发会话", icon: MessageSquareText },
  experiment: { label: "实验", icon: Beaker },
  notebook: { label: "Notebook", icon: NotebookTabs },
  document: { label: "文档", icon: FileText },
  workspace: { label: "项目工作", icon: BookOpen },
} satisfies Record<SearchSource, SourceMeta>;

const sourceOrder = new Map(
  (Object.keys(sourceMeta) as SearchSource[]).map((source, index) => [source, index]),
);

function isKnownSource(source: string): source is SearchSource {
  return Object.prototype.hasOwnProperty.call(sourceMeta, source);
}

function sourcePresentation(source: string): SourceMeta & { known: boolean } {
  if (isKnownSource(source)) return { ...sourceMeta[source], known: true };
  return {
    label: `未知来源 · ${safeMetadataValue(source, "不可识别")}`,
    icon: CircleHelp,
    known: false,
  };
}

function compareSourceGroups(
  [left]: [string, LocatedResult[]],
  [right]: [string, LocatedResult[]],
) {
  const leftOrder = sourceOrder.get(left as SearchSource);
  const rightOrder = sourceOrder.get(right as SearchSource);
  if (leftOrder !== undefined && rightOrder !== undefined) return leftOrder - rightOrder;
  if (leftOrder !== undefined) return -1;
  if (rightOrder !== undefined) return 1;
  return left.localeCompare(right);
}

type LocatedResult = SearchResult & { project: Project };

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const initialQuery = params.get("q") || "";
  const view = params.get("view") === "evidence" ? "evidence" : "query";
  const [draft, setDraft] = useState(initialQuery);
  const selectedProject = params.get("project") || "";
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects.list });
  const projects = projectsQuery.data || [];
  const searchProjects =
    selectedProject === "all"
      ? projects.filter((item) => item.status !== "archived")
      : selectedProject
        ? projects.filter((item) => item.id === selectedProject)
        : [];
  const searches = useQueries({
    queries: searchProjects.map((project) => ({
      queryKey: ["search", project.id, initialQuery],
      queryFn: ({ signal }) => api.search.project(project.id, initialQuery, signal),
      enabled: Boolean(initialQuery) && view === "evidence",
    })),
  });
  const [selected, setSelected] = useState<LocatedResult | null>(null);
  const results = useMemo(
    () =>
      searches.flatMap((search, index) =>
        (search.data?.results || []).map((item) => ({
          ...item,
          project: searchProjects[index],
        })),
      ),
    [searches, searchProjects],
  );
  const grouped = useMemo(
    () => {
      const groups = new Map<string, LocatedResult[]>();
      results.forEach((item) => {
        const items = groups.get(item.source) || [];
        items.push(item);
        groups.set(item.source, items);
      });
      return [...groups.entries()].sort(compareSourceGroups);
    },
    [results],
  );
  const isSearching = searches.some((item) => item.isFetching);

  const setView = (nextView: "query" | "evidence") => {
    const next = new URLSearchParams(params);
    if (nextView === "query") next.delete("view");
    else next.set("view", "evidence");
    setParams(next);
    setSelected(null);
  };

  return (
    <div className={`search-page ${view === "evidence" && selected ? "has-inspector" : ""}`}>
      <section className="search-page__main">
        <header className="page-heading search-page__heading">
          <div>
            <h1>项目查询</h1>
            <p>可信问答与原始证据浏览使用独立请求链。</p>
          </div>
          <nav className="search-view-switcher" aria-label="查询模式">
            <button
              type="button"
              className={view === "query" ? "is-active" : ""}
              onClick={() => setView("query")}
            >
              <ShieldCheck size={16} />
              可信问答
            </button>
            <button
              type="button"
              className={view === "evidence" ? "is-active" : ""}
              onClick={() => setView("evidence")}
            >
              <Search size={16} />
              证据浏览
            </button>
          </nav>
        </header>
        {view === "query" ? (
          <TrustedQueryPanel
            projects={projects}
            initialProjectId={selectedProject === "all" ? undefined : selectedProject}
            initialQuestion={initialQuery}
          />
        ) : (
          <>
            <div className="evidence-browser-note">
              <BookOpen size={17} />
              <div>
                <strong>原始证据浏览</strong>
                <span>这里调用 `/v1/search`，结果是候选证据，不代表系统回答或已验证结论。</span>
              </div>
            </div>
            <form
              className="search-hero"
              onSubmit={(event) => {
                event.preventDefault();
                if (!selectedProject || !draft.trim()) return;
                const next = new URLSearchParams(params);
                next.set("q", draft.trim());
                next.set("view", "evidence");
                setParams(next);
                setSelected(null);
              }}
            >
              <Search size={21} />
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="按文件、函数、指标或研究实体查找原始证据"
                autoFocus
              />
              <select
                value={selectedProject}
                onChange={(event) => {
                  const next = new URLSearchParams(params);
                  next.set("project", event.target.value);
                  next.set("view", "evidence");
                  setParams(next);
                  setSelected(null);
                }}
                aria-label="搜索范围"
              >
                <option value="">请选择项目范围</option>
                <option value="all">全部项目（显式选择）</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>{project.name}</option>
                ))}
              </select>
              <button type="submit" disabled={!selectedProject || !draft.trim()}>浏览</button>
            </form>

            {!selectedProject ? (
              <div className="evidence-browser-clarification" role="status">
                <AlertTriangle size={17} />
                <span>请选择单个项目或显式选择“全部项目”；不会自动使用首个项目。</span>
              </div>
            ) : null}
            {initialQuery && isSearching ? <div className="search-progress">正在跨来源查找证据…</div> : null}
            {!initialQuery ? (
              <EmptyState
                icon={<Sparkles />}
                title="输入证据定位词"
                description="使用文件名、符号、会话主题、实验指标或文档 Claim；这里不会生成回答。"
              />
            ) : grouped.length ? (
              <div className="search-groups">
                <div className="search-summary">
                  <strong>{results.length}</strong> 条候选证据
                  <span>“{initialQuery}”</span>
                </div>
                {grouped.map(([source, items]) => {
                  const meta = sourcePresentation(source);
                  return (
                    <section className="search-group" data-source={source} key={source}>
                      <header>
                        <span><meta.icon size={18} />{meta.label}</span>
                        <b>{items.length}</b>
                      </header>
                      {items.map((item, index) => (
                        <button
                          key={`${item.entity_id}-${index}`}
                          className={selected?.entity_id === item.entity_id ? "is-selected" : ""}
                          onClick={() => setSelected(item)}
                        >
                          <span className="search-result__icon"><meta.icon size={18} /></span>
                          <span className="search-result__body">
                            <strong>{sanitizeDisplayText(humanizeSearchTitle(item.title), "未命名证据")}</strong>
                            <small>
                              {sanitizeDisplayText(item.project.name, "未命名项目")}
                              {item.subtitle
                                ? ` · ${sanitizeDisplayText(cleanEvidenceText(item.subtitle, 80))}`
                                : ""}
                            </small>
                            <p>{sanitizeDisplayText(
                              presentSearchSnippet(item.source, item.title, item.snippet),
                              "打开结果查看完整上下文。",
                            )}</p>
                          </span>
                          {meta.known
                            ? item.status
                              ? <em>{safeMetadataValue(item.status)}</em>
                              : null
                            : <em>来源未识别</em>}
                        </button>
                      ))}
                    </section>
                  );
                })}
              </div>
            ) : searches.some((item) => item.isError) && !isSearching ? (
              <EmptyState
                icon={<AlertTriangle />}
                title="证据服务暂时不可用"
                description="没有把错误响应当作空结果；请稍后重试。"
              />
            ) : searches.some((item) => item.isFetched) && !isSearching ? (
              <EmptyState
                icon={<Search />}
                title="没有找到候选证据"
                description="尝试使用文件名、函数名、实验指标或更短的定位词。"
              />
            ) : null}
          </>
        )}
      </section>
      {view === "evidence" && selected ? (
        <Inspector
          eyebrow={sourcePresentation(selected.source).label}
          title={sanitizeDisplayText(humanizeSearchTitle(selected.title), "未命名证据")}
          onClose={() => setSelected(null)}
        >
          <section className="inspector-section">
            <h3>所在项目</h3>
            <strong className="current-question">
              {sanitizeDisplayText(selected.project.name, "未命名项目")}
            </strong>
          </section>
          <section className="inspector-section">
            <h3>匹配内容</h3>
            <p className="result-snippet">{sanitizeDisplayText(
              presentSearchSnippet(selected.source, selected.title, selected.snippet, true),
              "暂无摘要。",
            )}</p>
          </section>
          <section className="inspector-section">
            <h3>定位</h3>
            <div className="locator">{safeMetadataValue(selected.locator)}</div>
          </section>
          {selected.channels?.length ? (
            <section className="inspector-section">
              <h3>关联来源</h3>
              <div className="tag-list">
                {selected.channels.map((item) => (
                  <span key={item}>{safeMetadataValue(item)}</span>
                ))}
              </div>
            </section>
          ) : null}
        </Inspector>
      ) : null}
    </div>
  );
}
