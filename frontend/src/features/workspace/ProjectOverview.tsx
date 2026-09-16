import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Beaker,
  BookOpen,
  CheckCircle2,
  Code2,
  FileText,
  Link2,
  MessageSquareText,
  Network,
  Play,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../lib/api";
import { EmptyState, Status } from "../../components/ui";
import type { Repository } from "../../lib/types";
import "./ProjectOverview.css";

const repositoryStatus: Record<
  string,
  { label: string; description: string; tone: string }
> = {
  ready: {
    label: "索引可用",
    description: "当前 generation 已发布",
    tone: "completed",
  },
  indexing: {
    label: "正在索引",
    description: "新 generation 尚未发布",
    tone: "running",
  },
  failed: {
    label: "索引失败",
    description: "未将失败结果标记为可用",
    tone: "failed",
  },
};

function repositoryPresentation(repository: Repository) {
  if (repository.status === "ready" && !repository.active_generation_id) {
    return {
      label: "索引未发布",
      description: "仓库没有可用 generation",
      tone: "failed",
    };
  }
  return (
    repositoryStatus[repository.status] || {
      label: "状态不可用",
      description: "未确认索引可用性",
      tone: "failed",
    }
  );
}

export function ProjectOverview() {
  const { projectId = "" } = useParams();
  const dashboardQuery = useQuery({
    queryKey: ["dashboard", projectId],
    queryFn: () => api.projects.getDashboard(projectId),
    enabled: Boolean(projectId),
  });
  const repositoriesQuery = useQuery({
    queryKey: ["repositories", projectId],
    queryFn: () => api.code.repositories(projectId),
    enabled: Boolean(projectId),
  });
  const dashboard = dashboardQuery.data;

  if (dashboardQuery.isLoading)
    return <div className="page-loading">正在组织项目上下文…</div>;
  if (!dashboard) {
    return (
      <EmptyState
        icon={<BookOpen />}
        title="项目不可用"
        description={dashboardQuery.error?.message || "未找到项目。"}
      />
    );
  }
  const project = dashboard.project;
  const currentTopic = dashboard.recent_topics[0];
  const relevantActivity = dashboard.recent_activity.filter(
    (event) => !event.action.startsWith("api."),
  );
  const activityLabels: Record<string, string> = {
    "project.created": "创建项目",
    "project.updated": "更新项目",
    "topic.created": "建立研究问题",
    "topic.updated": "更新研究问题",
    "iteration.created": "建立研究迭代",
    "iteration.updated": "更新研究迭代",
    "work_item.created": "建立执行任务",
    "work_item.updated": "更新执行任务",
    "work_item.transitioned": "任务状态变化",
    "relation.reviewed": "完成关系复核",
  };

  return (
    <div className="overview-page">
      <header className="page-heading page-heading--project">
        <div>
          <span className="eyebrow">PROJECT OVERVIEW</span>
          <h1>{project.name}</h1>
          <p>
            {project.description || "集中管理研究问题、开发执行与证据复核。"}
          </p>
        </div>
        <Status value={project.status}>
          {project.status === "active" ? "进行中" : project.status}
        </Status>
      </header>

      <section className="research-focus">
        <div className="research-focus__mark">H1</div>
        <div>
          <span>当前研究问题</span>
          <h2>{currentTopic?.title || "建立项目的首个研究问题"}</h2>
          <p>
            {currentTopic?.problem_statement ||
              "从一个需要验证的问题出发，系统会自动组织相关代码、会话、实验与文档证据。"}
          </p>
        </div>
        <Link to={`/p/${encodeURIComponent(projectId)}/map`}>
          打开关系图 <ArrowRight size={17} />
        </Link>
      </section>

      <div className="overview-grid">
        <section
          className="overview-panel project-repositories"
          aria-label="项目代码仓库"
        >
          <header>
            <div>
              <h2>代码仓库</h2>
              <p>项目一级入口 · 仓库、Ref 与索引 generation</p>
            </div>
            <Link to={`/p/${encodeURIComponent(projectId)}/code`}>
              打开代码工作区 <ArrowRight size={16} />
            </Link>
          </header>
          {repositoriesQuery.isLoading ? (
            <p className="project-repositories__state">
              <RefreshCw size={16} className="is-spinning" /> 正在读取项目仓库…
            </p>
          ) : repositoriesQuery.isError ? (
            <div className="project-repositories__error" role="alert">
              <TriangleAlert size={17} />
              <span>仓库状态不可读取；不会显示缓存路径或假定索引可用。</span>
              <button
                type="button"
                onClick={() => void repositoriesQuery.refetch()}
              >
                重试
              </button>
            </div>
          ) : repositoriesQuery.data?.length ? (
            <div className="project-repository-list">
              {repositoriesQuery.data.map((repository) => {
                const presentation = repositoryPresentation(repository);
                const repositoryHref = `/p/${encodeURIComponent(projectId)}/code?repository=${encodeURIComponent(repository.id)}`;
                return (
                  <article key={repository.id}>
                    <header>
                      <div>
                        <Code2 size={18} />
                        <span>
                          <strong>{repository.name}</strong>
                          <small>
                            {repository.default_branch || "HEAD"} ·{" "}
                            {repository.head_commit
                              ? repository.head_commit.slice(0, 10)
                              : "ref 未发现"}
                          </small>
                        </span>
                      </div>
                      <Status value={presentation.tone}>
                        {presentation.label}
                      </Status>
                    </header>
                    <dl>
                      <div>
                        <dt>Index</dt>
                        <dd>{presentation.description}</dd>
                      </div>
                      <div>
                        <dt>Generation</dt>
                        <dd>{repository.active_generation_id || "未发布"}</dd>
                      </div>
                    </dl>
                    {repository.last_error ? (
                      <p className="project-repository-list__error">
                        最近索引失败；详细错误未在项目总览展开。
                      </p>
                    ) : null}
                    <Link to={repositoryHref}>
                      {repository.status === "failed"
                        ? "检查仓库与索引"
                        : "浏览仓库"}
                      <ArrowRight size={15} />
                    </Link>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="project-repositories__empty">
              <p>当前 ACL 范围内没有代码仓库。</p>
              <Link to={`/p/${encodeURIComponent(projectId)}/code`}>
                前往代码工作区导入仓库 <ArrowRight size={15} />
              </Link>
            </div>
          )}
        </section>

        <section className="overview-panel overview-panel--sources">
          <header>
            <div>
              <h2>研究来源</h2>
              <p>当前项目已接入并可检索的数据</p>
            </div>
          </header>
          <div className="source-matrix">
            <Link to={`/p/${projectId}/code`}>
              <Code2 />
              <span>
                <strong>{dashboard.stats.repositories}</strong>代码仓库
              </span>
              <ArrowRight />
            </Link>
            <Link to={`/p/${projectId}/sessions`}>
              <MessageSquareText />
              <span>
                <strong>{dashboard.stats.sessions}</strong>研发会话
              </span>
              <ArrowRight />
            </Link>
            <Link to={`/p/${projectId}/experiments`}>
              <Beaker />
              <span>
                <strong>{project.experiments || 0}</strong>实验
              </span>
              <ArrowRight />
            </Link>
            <Link to={`/p/${projectId}/documents`}>
              <FileText />
              <span>
                <strong>{project.documents || 0}</strong>科研文档
              </span>
              <ArrowRight />
            </Link>
          </div>
        </section>

        <section className="overview-panel">
          <header>
            <div>
              <h2>正在推进</h2>
              <p>只显示当前需要处理的科研工作</p>
            </div>
          </header>
          <div className="work-list">
            {dashboard.active_work_items.length ? (
              dashboard.active_work_items.slice(0, 5).map((item) => (
                <div key={item.id}>
                  <span className={`work-kind work-kind--${item.kind}`}>
                    {item.kind === "experiment" ? (
                      <Beaker />
                    ) : item.kind === "review" ? (
                      <ShieldCheck />
                    ) : (
                      <Play />
                    )}
                  </span>
                  <span>
                    <strong>{item.title}</strong>
                    <small>
                      {item.kind} · {item.status}
                    </small>
                  </span>
                  <Status value={item.status}>{item.status}</Status>
                </div>
              ))
            ) : (
              <div className="compact-empty">
                <CheckCircle2 />
                <span>
                  <strong>当前没有待执行任务</strong>
                  <small>研究问题建立后，可将具体工作交给 Codex。</small>
                </span>
              </div>
            )}
          </div>
        </section>

        <section className="overview-panel">
          <header>
            <div>
              <h2>证据与复核</h2>
              <p>进入结论前需要确认的关系</p>
            </div>
          </header>
          <div className="evidence-summary">
            <div>
              <Network />
              <strong>{dashboard.stats.relations}</strong>
              <span>跨来源关系</span>
            </div>
            <div>
              <Link2 />
              <strong>{dashboard.stats.linked_evidence}</strong>
              <span>已关联证据</span>
            </div>
            <div
              className={
                dashboard.stats.pending_reviews ? "needs-attention" : ""
              }
            >
              <ShieldCheck />
              <strong>{dashboard.stats.pending_reviews}</strong>
              <span>待人工复核</span>
            </div>
          </div>
          <Link className="panel-link" to={`/p/${projectId}/relations`}>
            进入关系复核 <ArrowRight size={16} />
          </Link>
        </section>

        <section className="overview-panel">
          <header>
            <div>
              <h2>最近活动</h2>
              <p>由系统记录，不需要人工填写</p>
            </div>
          </header>
          <div className="activity-list">
            {relevantActivity.slice(0, 5).map((event) => (
              <div key={event.id}>
                <span />
                <p>
                  <strong>
                    {activityLabels[event.action] || "项目内容发生变化"}
                  </strong>
                  <small>
                    {event.actor} ·{" "}
                    {new Date(event.created_at).toLocaleString("zh-CN")}
                  </small>
                </p>
              </div>
            ))}
            {!relevantActivity.length ? (
              <div className="compact-empty">
                研究主题、任务或证据发生变化后会在这里自动记录。
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
