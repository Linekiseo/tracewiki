import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Beaker,
  BookOpen,
  Box,
  Code2,
  FileText,
  FolderGit2,
  MessageSquareText,
  MoreHorizontal,
  Network,
  Search,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../lib/api";
import type { Project } from "../../lib/types";
import { EmptyState, Inspector, Status } from "../../components/ui";

const relativeTime = (value?: string | null) => {
  if (!value) return "尚无活动";
  const elapsed = Math.max(0, Date.now() - new Date(value).getTime());
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return days < 30 ? `${days} 天前` : new Date(value).toLocaleDateString("zh-CN");
};

function sourceTotal(project: Project) {
  return (
    (project.repositories || 0) +
    (project.sessions || 0) +
    (project.experiments || 0) +
    (project.documents || 0)
  );
}

export function ProjectCenter() {
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.projects.list });
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"all" | "active" | "paused" | "archived">("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const projects = projectsQuery.data || [];
  const selected = projects.find((item) => item.id === selectedId) || null;
  const filtered = useMemo(
    () =>
      projects.filter(
        (item) =>
          (status === "all" || item.status === status) &&
          `${item.name} ${item.description} ${item.current_topic || ""}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [projects, query, status],
  );
  const active = projects.filter((item) => item.status === "active");
  const continuing = active.slice(0, 3);

  if (projectsQuery.isLoading) {
    return <div className="page-loading">正在读取项目上下文…</div>;
  }
  if (projectsQuery.error) {
    return (
      <EmptyState
        icon={<Box />}
        title="暂时无法读取项目"
        description={projectsQuery.error.message}
      />
    );
  }

  return (
    <div className={`project-center ${selected ? "has-inspector" : ""}`}>
      <section className="project-center__main">
        <header className="page-heading">
          <div>
            <span className="eyebrow">RESEARCH PROJECTS</span>
            <h1>科研项目</h1>
            <p>从研究问题进入项目，代码、会话、实验与文档在同一上下文中协作。</p>
          </div>
          <div className="heading-summary">
            <strong>{active.length}</strong>
            <span>个活跃项目</span>
          </div>
        </header>

        {continuing.length ? (
          <section className="continue-section">
            <div className="section-heading">
              <div>
                <h2>继续研究</h2>
                <p>回到最近产生变化的项目</p>
              </div>
              <Link to={`/p/${encodeURIComponent(continuing[0].id)}/overview`}>
                进入最近项目 <ArrowRight size={16} />
              </Link>
            </div>
            <div className="continue-grid">
              {continuing.map((project, index) => (
                <Link
                  key={project.id}
                  to={`/p/${encodeURIComponent(project.id)}/overview`}
                  className={`continue-card continue-card--${index + 1}`}
                >
                  <div className="continue-card__top">
                    <span className="project-glyph">{project.name.slice(0, 1).toUpperCase()}</span>
                    <Status value={project.status}>{project.status === "active" ? "进行中" : "已暂停"}</Status>
                  </div>
                  <h3>{project.name}</h3>
                  <p>{project.current_topic || project.description || "等待建立首个研究问题"}</p>
                  <div className="continue-card__meta">
                    <span><Network size={15} /> {project.topic_count} 主题</span>
                    <span><Box size={15} /> {sourceTotal(project)} 来源</span>
                    <time>{relativeTime(project.last_activity_at || project.updated_at)}</time>
                  </div>
                </Link>
              ))}
            </div>
          </section>
        ) : null}

        <section className="project-list-section">
          <div className="section-heading section-heading--list">
            <div>
              <h2>全部项目</h2>
              <p>选择项目查看来源、研究主题与待处理工作</p>
            </div>
            <div className="list-controls">
              <label className="inline-search">
                <Search size={16} />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="查找项目"
                  aria-label="查找项目"
                />
              </label>
              <select
                value={status}
                onChange={(event) => setStatus(event.target.value as typeof status)}
                aria-label="筛选项目状态"
              >
                <option value="all">全部状态</option>
                <option value="active">进行中</option>
                <option value="paused">已暂停</option>
                <option value="archived">已归档</option>
              </select>
            </div>
          </div>

          {filtered.length ? (
            <div className="project-table" role="table" aria-label="科研项目">
              <div className="project-table__head" role="row">
                <span>项目</span>
                <span>当前研究</span>
                <span>数据来源</span>
                <span>待复核</span>
                <span>最近活动</span>
                <span />
              </div>
              {filtered.map((project) => (
                <div
                  key={project.id}
                  className={`project-row ${project.id === selectedId ? "is-selected" : ""}`}
                  role="row"
                  tabIndex={0}
                  onClick={() => setSelectedId(project.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") setSelectedId(project.id);
                  }}
                >
                  <div className="project-row__name">
                    <span className="project-glyph">{project.name.slice(0, 1).toUpperCase()}</span>
                    <span>
                      <strong>{project.name}</strong>
                      <small><Status value={project.status}>{project.status === "active" ? "进行中" : project.status === "paused" ? "已暂停" : "已归档"}</Status></small>
                    </span>
                  </div>
                  <div className="project-row__topic">
                    <strong>{project.current_topic || "尚未建立研究主题"}</strong>
                    <small>{project.active_iteration_count || 0} 个活跃迭代</small>
                  </div>
                  <div className="source-stack" aria-label={`${sourceTotal(project)} 个数据来源`}>
                    <span><FolderGit2 size={15} />{project.repositories || 0}</span>
                    <span><MessageSquareText size={15} />{project.sessions || 0}</span>
                    <span><Beaker size={15} />{project.experiments || 0}</span>
                    <span><FileText size={15} />{project.documents || 0}</span>
                  </div>
                  <span className="review-count">{project.pending_reviews || 0}</span>
                  <time>{relativeTime(project.last_activity_at || project.updated_at)}</time>
                  <button
                    className="row-action"
                    aria-label={`查看 ${project.name} 详情`}
                    onClick={(event) => {
                      event.stopPropagation();
                      setSelectedId(project.id);
                    }}
                  >
                    <MoreHorizontal size={18} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={<Search />}
              title="没有匹配的项目"
              description="调整搜索词或状态筛选后再试。"
            />
          )}
        </section>
      </section>

      {selected ? (
        <Inspector eyebrow="PROJECT" title={selected.name} onClose={() => setSelectedId(null)}>
          <div className="project-inspector__lead">
            <Status value={selected.status}>
              {selected.status === "active" ? "进行中" : selected.status === "paused" ? "已暂停" : "已归档"}
            </Status>
            <p>{selected.description || "这个项目尚未补充说明。"}</p>
          </div>
          <section className="inspector-section">
            <h3>当前研究</h3>
            <strong className="current-question">
              {selected.current_topic || "尚未建立研究问题"}
            </strong>
            <dl className="compact-facts">
              <div><dt>研究主题</dt><dd>{selected.topic_count}</dd></div>
              <div><dt>活跃迭代</dt><dd>{selected.active_iteration_count}</dd></div>
              <div><dt>执行任务</dt><dd>{selected.active_work_item_count || 0}</dd></div>
            </dl>
          </section>
          <section className="inspector-section">
            <h3>数据来源</h3>
            <div className="source-list">
              <span><Code2 size={17} />代码仓库 <b>{selected.repositories || 0}</b></span>
              <span><MessageSquareText size={17} />Codex 会话 <b>{selected.sessions || 0}</b></span>
              <span><Beaker size={17} />实验与运行 <b>{selected.experiments || 0}</b></span>
              <span><BookOpen size={17} />科研文档 <b>{selected.documents || 0}</b></span>
            </div>
          </section>
          <section className="inspector-section">
            <h3>需要处理</h3>
            <div className="attention-row">
              <span>{selected.pending_reviews || 0}</span>
              <div><strong>条关系待复核</strong><small>确认后才进入可信证据链</small></div>
            </div>
          </section>
          <div className="inspector-actions">
            <Link className="button button--primary" to={`/p/${encodeURIComponent(selected.id)}/overview`}>
              进入项目 <ArrowRight size={17} />
            </Link>
            <Link className="button button--secondary" to={`/p/${encodeURIComponent(selected.id)}/map`}>
              查看关系图
            </Link>
          </div>
        </Inspector>
      ) : null}
    </div>
  );
}
