import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  Beaker,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Code2,
  Copy,
  FileCode2,
  FileDiff,
  FileText,
  GitCommitHorizontal,
  GripVertical,
  Hash,
  LayoutGrid,
  Link2,
  ListTree,
  ListChecks,
  Maximize2,
  MessageSquareText,
  Minus,
  Network,
  PanelRightClose,
  PanelRightOpen,
  Play,
  Plus,
  RotateCcw,
  Rows3,
  Search,
  ShieldCheck,
  Sparkles,
  Terminal,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Button,
  EmptyState,
  ExpandableText,
  Status,
} from "../../components/ui";
import { api } from "../../lib/api";
import {
  cleanEvidenceText,
  humanizeSearchTitle,
  humanizeSessionTitle,
  presentSearchSnippet,
  semanticSourceTitle,
  sourceDestination,
  sourceGraphDestination,
  structureTaskSummary,
} from "../../lib/presentation";
import type {
  CodexSession,
  CodexTimelineFileChange,
  CodexTimelineOperation,
  CodexTimelineTurn,
  SearchResult,
} from "../../lib/types";
import { findSessionDiffEvidence, parseSessionDiff } from "./sessionDiff";
import "./SessionWorkspace.css";

function formatTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function safeCount(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function formatBytes(value?: number | null) {
  if (!value || !Number.isFinite(value)) return "约 238 MB";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size >= 10 || unit === 0 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`;
}

const SESSION_PAGE_SIZE = 20;
const TIMELINE_PAGE_SIZE = 18;
const AUDIT_ITEM_PAGE_SIZE = 4;
const EVIDENCE_PAGE_SIZE = 4;
const FILE_LEDGER_PAGE_SIZE = 8;
const OPERATION_LEDGER_PAGE_SIZE = 10;

function InlinePager({
  label,
  page,
  total,
  pageSize,
  onPage,
}: {
  label: string;
  page: number;
  total: number;
  pageSize: number;
  onPage: (page: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) return null;
  return (
    <nav className="collection-pager session-inline-pager" aria-label={label}>
      <button
        type="button"
        disabled={page === 0}
        onClick={() => onPage(page - 1)}
      >
        <ChevronLeft size={14} />
        上一页
      </button>
      <span>
        第 {page + 1} / {pageCount} 页 · 共 {total} 项
      </span>
      <button
        type="button"
        disabled={page + 1 >= pageCount}
        onClick={() => onPage(page + 1)}
      >
        下一页
        <ChevronRight size={14} />
      </button>
    </nav>
  );
}

function statusLabel(value: string) {
  return (
    {
      completed: "已完成",
      running: "进行中",
      in_progress: "进行中",
      failed: "失败",
      archived: "已归档",
      passed: "通过",
      recorded: "已记录",
      pending: "待执行",
      indexed: "已索引",
      verified: "已验证",
      reported: "待核验",
    }[value] || value
  );
}

function openCodex(projectId: string, workspacePath: string, task?: string) {
  if (!workspacePath) return;
  const prompt = encodeURIComponent(
    task ||
      `读取科研项目 ${projectId} 的当前上下文，创建新的研发会话并先列出目标、验收条件与执行计划。`,
  );
  window.location.href = `codex://new?path=${encodeURIComponent(workspacePath)}&prompt=${prompt}`;
}

function isSubagentSession(session: CodexSession) {
  const source = session.metadata?.source;
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    return false;
  }
  const subagent = (source as Record<string, unknown>).subagent;
  return Boolean(
    subagent === true ||
    (subagent && typeof subagent === "object" && !Array.isArray(subagent)),
  );
}

export function SessionOverview() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [sessionKind, setSessionKind] = useState<"all" | "main" | "subagent">(
    "all",
  );
  const [sessionPage, setSessionPage] = useState(0);
  const [oversizedHelpOpen, setOversizedHelpOpen] = useState(false);
  const [syncWorkflowId, setSyncWorkflowId] = useState<string | null>(null);
  const settledWorkflowRef = useRef<string | null>(null);
  const autoSyncProjectRef = useRef<string | null>(null);
  const sessionsQuery = useQuery({
    queryKey: ["sessions", projectId],
    queryFn: ({ signal }) => api.sessions.list(projectId, "", signal),
    enabled: Boolean(projectId),
  });
  const dashboardQuery = useQuery({
    queryKey: ["project-dashboard", projectId],
    queryFn: () => api.projects.getDashboard(projectId),
    enabled: Boolean(projectId),
  });
  const codexStatusQuery = useQuery({
    queryKey: ["codex-bridge-status", projectId],
    queryFn: () => api.codexBridge.status(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 20_000,
  });
  const syncStatusQuery = useQuery({
    queryKey: ["session-sync-status", projectId],
    queryFn: ({ signal }) => api.sessions.syncStatus(projectId, signal),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
  const syncMutation = useMutation({
    mutationFn: (force: boolean) => api.sessions.sync(projectId, force),
    onSuccess: (result) => {
      if (result.workflow_id) {
        settledWorkflowRef.current = null;
        setSyncWorkflowId(result.workflow_id);
        return;
      }
      void queryClient.invalidateQueries({ queryKey: ["sessions", projectId] });
      void queryClient.invalidateQueries({
        queryKey: ["session-sync-status", projectId],
      });
    },
  });
  const workflowQuery = useQuery({
    queryKey: ["session-sync-workflow", syncWorkflowId],
    queryFn: ({ signal }) =>
      api.sessions.workflow(syncWorkflowId || "", signal),
    enabled: Boolean(syncWorkflowId),
    refetchInterval: syncWorkflowId ? 1_200 : false,
  });

  useEffect(() => {
    const activeWorkflowId = syncStatusQuery.data?.active_workflow_id;
    if (
      activeWorkflowId &&
      activeWorkflowId !== settledWorkflowRef.current &&
      !syncWorkflowId
    ) {
      setSyncWorkflowId(activeWorkflowId);
    }
  }, [syncStatusQuery.data?.active_workflow_id, syncWorkflowId]);

  useEffect(() => {
    if (
      !projectId ||
      !syncStatusQuery.data?.needs_sync ||
      syncStatusQuery.data.active_workflow_id ||
      syncMutation.isPending ||
      autoSyncProjectRef.current === projectId
    ) {
      return;
    }
    autoSyncProjectRef.current = projectId;
    syncMutation.mutate(false);
  }, [projectId, syncMutation, syncStatusQuery.data]);

  useEffect(() => {
    const workflow = workflowQuery.data;
    if (
      !workflow ||
      !syncWorkflowId ||
      !["completed", "failed"].includes(workflow.status)
    ) {
      return;
    }
    settledWorkflowRef.current = syncWorkflowId;
    setSyncWorkflowId(null);
    void queryClient.invalidateQueries({ queryKey: ["sessions", projectId] });
    void queryClient.invalidateQueries({
      queryKey: ["session-sync-status", projectId],
    });
    void queryClient.invalidateQueries({
      queryKey: ["project-dashboard", projectId],
    });
  }, [projectId, queryClient, syncWorkflowId, workflowQuery.data]);

  const sessions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (sessionsQuery.data || []).filter(
      (item) =>
        (status === "all" || item.status === status) &&
        (sessionKind === "all" ||
          (sessionKind === "subagent"
            ? isSubagentSession(item)
            : !isSubagentSession(item))) &&
        (!needle || `${item.title} ${item.cwd}`.toLowerCase().includes(needle)),
    );
  }, [query, sessionKind, sessionsQuery.data, status]);
  const sessionPageCount = Math.max(
    1,
    Math.ceil(sessions.length / SESSION_PAGE_SIZE),
  );
  const pagedSessions = sessions.slice(
    sessionPage * SESSION_PAGE_SIZE,
    (sessionPage + 1) * SESSION_PAGE_SIZE,
  );
  useEffect(() => {
    setSessionPage(0);
  }, [query, sessionKind, status]);
  useEffect(() => {
    if (sessionPage >= sessionPageCount) setSessionPage(sessionPageCount - 1);
  }, [sessionPage, sessionPageCount]);
  const workspacePath = String(
    dashboardQuery.data?.project.settings?.workspace_path || "",
  ).trim();
  const codexCanLaunch =
    !sessionsQuery.isError &&
    !syncStatusQuery.isError &&
    !dashboardQuery.isError &&
    !codexStatusQuery.isError &&
    codexStatusQuery.data?.availability === "ready" &&
    Boolean(workspacePath);
  const syncStatus = syncStatusQuery.data;
  const workflow = workflowQuery.data;
  const isSyncing =
    syncMutation.isPending ||
    workflow?.status === "queued" ||
    workflow?.status === "running" ||
    Boolean(syncStatus?.active_workflow_id && !workflow);
  const isLoadingSessions = sessionsQuery.isPending;
  const loadedCount = sessionsQuery.data?.length || 0;
  const unindexedCount = syncStatus
    ? Math.max(
        safeCount(syncStatus.indexable_sessions) -
          safeCount(syncStatus.indexed_sessions),
        0,
      )
    : 0;
  const discoveryTotal =
    syncStatus?.discovery?.candidate_sessions ??
    syncStatus?.discovered_sessions ??
    0;
  const filteredDiscoveryCount = syncStatus
    ? safeCount(syncStatus.discovery?.excluded_other_project_sessions) +
      safeCount(syncStatus.discovery?.excluded_unbound_sessions) +
      safeCount(syncStatus.discovery?.excluded_unreadable_sessions) +
      safeCount(
        syncStatus.discovery?.excluded_oversized_sessions ??
          syncStatus.oversized_sessions,
      )
    : 0;
  const syncGapCount = syncStatus
    ? safeCount(syncStatus.new_sessions) +
      safeCount(syncStatus.changed_sessions) +
      safeCount(syncStatus.removed_sessions)
    : 0;
  const syncError =
    syncMutation.error instanceof Error
      ? "同步请求未被服务接受。"
      : workflow?.status === "failed"
        ? "最近一次会话同步失败。"
        : syncStatusQuery.error instanceof Error
          ? "会话发现状态不可读取。"
          : "";

  return (
    <div className="session-overview">
      <header className="session-overview__heading">
        <div>
          <h1>研发会话</h1>
          <p>项目内全部 Codex 会话会自动发现、同步并保留独立审计链路</p>
        </div>
        <div className="session-overview__actions">
          <Button
            variant="secondary"
            disabled={
              isSyncing || syncStatusQuery.isError || sessionsQuery.isError
            }
            onClick={() => {
              autoSyncProjectRef.current = projectId;
              syncMutation.mutate(true);
            }}
          >
            <RotateCcw size={16} className={isSyncing ? "is-spinning" : ""} />
            {isSyncing ? "正在同步" : "同步会话"}
          </Button>
          <Button
            variant="primary"
            disabled={!codexCanLaunch}
            title={
              codexCanLaunch ? undefined : "Codex 尚未连接，或项目工作区未配置"
            }
            onClick={() => openCodex(projectId, workspacePath)}
          >
            <Plus size={17} />
            新建会话
          </Button>
        </div>
      </header>

      <section className="session-index">
        <div
          className={`session-sync-strip${syncError ? " is-error" : ""}`}
          aria-live="polite"
        >
          <span className="session-sync-strip__state">
            <span className={isSyncing ? "is-syncing" : "is-ready"} />
            <strong>
              {syncError
                ? "会话同步异常"
                : isLoadingSessions
                  ? "正在读取项目会话"
                  : isSyncing
                    ? `正在载入项目会话${workflow?.progress ? ` · ${workflow.progress}%` : ""}`
                    : `已载入 ${loadedCount} 个会话`}
            </strong>
          </span>
          {syncError ? (
            <span title={syncError}>{syncError}</span>
          ) : syncStatus ? (
            <>
              <span>
                本地发现 {safeCount(syncStatus.discovered_sessions)} 个
              </span>
              {syncStatus.new_sessions > 0 && (
                <span className="session-sync-strip__delta">
                  +{syncStatus.new_sessions} 新增
                </span>
              )}
              {syncStatus.changed_sessions > 0 && (
                <span className="session-sync-strip__delta">
                  {syncStatus.changed_sessions} 个有更新
                </span>
              )}
              {syncStatus.live_sessions > 0 && (
                <span title="会话仍在持续写入，将在记录稳定后同步最新内容">
                  {syncStatus.live_sessions} 个正在记录
                </span>
              )}
              {syncStatus.hidden_subagent_sessions > 0 && (
                <span title="子任务已包含在默认的全部会话列表中，可使用类型筛选单独查看">
                  其中 {syncStatus.hidden_subagent_sessions} 个子任务
                </span>
              )}
              <span>上次同步 {formatTime(syncStatus.last_indexed_at)}</span>
              {syncStatus.oversized_sessions > 0 && (
                <button
                  type="button"
                  className="session-sync-strip__warning"
                  aria-expanded={oversizedHelpOpen}
                  aria-controls="oversized-session-help"
                  title="查看超大会话的安全处理方案"
                  onClick={() => setOversizedHelpOpen((value) => !value)}
                >
                  <TriangleAlert size={14} />
                  {syncStatus.oversized_sessions} 个超大会话待处理
                  <ChevronDown size={13} />
                </button>
              )}
            </>
          ) : (
            <span>正在扫描项目会话源…</span>
          )}
        </div>
        {syncStatus?.oversized_sessions && oversizedHelpOpen ? (
          <section
            id="oversized-session-help"
            className="oversized-session-help"
            aria-label="超大会话处理方案"
          >
            <div>
              <span className="oversized-session-help__icon">
                <TriangleAlert size={18} />
              </span>
              <div>
                <strong>该会话已保留，但为避免内存过载暂未解析</strong>
                <p>
                  单个记录超过当前安全上限{" "}
                  {formatBytes(syncStatus.max_session_bytes)}。 推荐在 Codex
                  中新建延续会话承接剩余工作，再点击“同步会话”；历史文件不会被删除。
                </p>
              </div>
            </div>
            <div className="oversized-session-help__actions">
              <Button
                variant="secondary"
                disabled={!codexCanLaunch}
                onClick={() =>
                  openCodex(
                    projectId,
                    workspacePath,
                    `延续当前项目中因记录过大而暂停索引的研发会话。先读取项目现状与已有开发记录，再列出剩余目标和验收条件；不要复制超大原始会话全文。`,
                  )
                }
              >
                <Plus size={15} />
                新建延续会话
              </Button>
              <button
                type="button"
                onClick={() => {
                  autoSyncProjectRef.current = projectId;
                  syncMutation.mutate(true);
                }}
                disabled={isSyncing}
              >
                完成拆分后重新同步
              </button>
            </div>
            <small>
              管理员也可评估机器资源后调整 RAG_MAX_CODEX_SESSION_BYTES
              并重启隔离服务；不建议直接取消安全上限。
            </small>
          </section>
        ) : null}
        {syncStatus ? (
          <section
            className="session-discovery-summary"
            aria-label="会话发现与同步覆盖"
          >
            <div>
              <span>发现总数</span>
              <strong>{discoveryTotal}</strong>
              <small>扫描到的候选会话</small>
            </div>
            <div>
              <span>当前筛选</span>
              <strong>
                {sessions.length} / {loadedCount}
              </strong>
              <small>默认包含主会话、子任务及已关联会话</small>
            </div>
            <div className={filteredDiscoveryCount ? "has-gap" : ""}>
              <span>明确过滤</span>
              <strong>{filteredDiscoveryCount}</strong>
              <small>跨项目、未绑定或不安全</small>
            </div>
            <div className={unindexedCount ? "has-gap" : ""}>
              <span>尚未索引</span>
              <strong>{unindexedCount}</strong>
              <small>可索引但尚未发布</small>
            </div>
            <div className={syncGapCount ? "has-gap" : ""}>
              <span>同步缺口</span>
              <strong>{syncGapCount}</strong>
              <small>新增、变化或已移除</small>
            </div>
            {syncStatus.discovery && !syncStatus.discovery.complete ? (
              <p className="session-discovery-summary__warning" role="alert">
                <TriangleAlert size={14} />
                会话发现未完整完成；当前计数按 fail-closed
                展示，不推断遗漏会话。
              </p>
            ) : null}
          </section>
        ) : null}
        <div className="session-index__tools">
          <label>
            <Search size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索会话目标或工作目录"
            />
          </label>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="all">全部状态（含已关联）</option>
            <option value="in_progress">进行中</option>
            <option value="completed">已完成</option>
            <option value="failed">失败</option>
          </select>
          <select
            value={sessionKind}
            onChange={(event) =>
              setSessionKind(event.target.value as typeof sessionKind)
            }
            aria-label="筛选会话类型"
          >
            <option value="all">全部会话</option>
            <option value="main">主会话</option>
            <option value="subagent">子任务</option>
          </select>
          <span>
            {sessions.length === loadedCount
              ? `${loadedCount} 项`
              : `${sessions.length} / ${loadedCount} 项`}
          </span>
        </div>
        {sessionsQuery.isError ? (
          <EmptyState
            icon={<TriangleAlert size={23} />}
            title="会话不可读取"
            description="当前身份未授权、项目不存在或会话服务不可用；不会显示缓存列表或开放会话写入。"
            action={
              <Button
                variant="secondary"
                onClick={() => void sessionsQuery.refetch()}
              >
                重试读取
              </Button>
            }
          />
        ) : isLoadingSessions ? (
          <div className="session-table__loading">
            <RotateCcw size={18} className="is-spinning" />
            正在读取项目会话…
          </div>
        ) : sessions.length ? (
          <div className="session-table">
            <div className="session-table__head">
              <span>会话目标</span>
              <span>审计构成</span>
              <span>状态</span>
              <span>最近更新</span>
              <span />
            </div>
            {pagedSessions.map((session) => (
              <button
                key={session.id}
                onClick={() =>
                  navigate(
                    `/p/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(session.thread_id)}`,
                  )
                }
              >
                <span className="session-table__title">
                  <MessageSquareText size={17} />
                  <span>
                    <strong>
                      {humanizeSessionTitle(
                        session.title,
                        undefined,
                        session.updated_at,
                      )}
                    </strong>
                    <small>
                      {isSubagentSession(session) ? (
                        <em className="session-subagent-mark">子任务</em>
                      ) : null}
                      {session.turn_count} 个有效轮次 ·{" "}
                      {session.cwd
                        .replace(/\\/g, "/")
                        .split("/")
                        .filter(Boolean)
                        .at(-1) || "未记录目录"}
                    </small>
                  </span>
                </span>
                <span className="session-table__sources">
                  <span>
                    <FileDiff size={15} />
                    {session.file_change_count} 项变更
                  </span>
                  <span>
                    <Terminal size={15} />
                    {session.command_count} 次执行
                  </span>
                </span>
                <Status value={session.status}>
                  {statusLabel(session.status)}
                </Status>
                <span>{formatTime(session.updated_at)}</span>
                <ChevronRight size={17} />
              </button>
            ))}
            <nav className="collection-pager" aria-label="会话列表分页">
              <button
                type="button"
                disabled={sessionPage === 0}
                onClick={() =>
                  setSessionPage((value) => Math.max(0, value - 1))
                }
              >
                <ChevronLeft size={15} />
                上一页
              </button>
              <span>
                第 {sessionPage + 1} / {sessionPageCount} 页 · 本页{" "}
                {pagedSessions.length}项
              </span>
              <button
                type="button"
                disabled={sessionPage + 1 >= sessionPageCount}
                onClick={() =>
                  setSessionPage((value) =>
                    Math.min(sessionPageCount - 1, value + 1),
                  )
                }
              >
                下一页
                <ChevronRight size={15} />
              </button>
            </nav>
          </div>
        ) : (
          <EmptyState
            icon={<MessageSquareText size={23} />}
            title="没有符合条件的会话"
            description="调整搜索条件，或启动新的 Codex 研发会话。"
            action={
              <Button
                variant="primary"
                disabled={!codexCanLaunch}
                title={
                  codexCanLaunch
                    ? undefined
                    : "Codex 尚未连接，或项目工作区未配置"
                }
                onClick={() => openCodex(projectId, workspacePath)}
              >
                新建会话
              </Button>
            }
          />
        )}
      </section>
    </div>
  );
}

const sourceLabels: Record<string, string> = {
  code: "代码实现",
  document: "科研文档",
  experiment: "实验结果",
  workspace: "研究任务",
  codex: "研发会话",
};

const sourceIcons: Record<string, typeof Code2> = {
  code: Code2,
  document: FileText,
  experiment: Beaker,
  workspace: Sparkles,
  codex: MessageSquareText,
};

function SourceMark({ source }: { source: string }) {
  const Icon = sourceIcons[source] || Link2;
  return (
    <span className={`source-mark source-mark--${source}`}>
      <Icon size={15} />
    </span>
  );
}

function sourceLocation(item: SearchResult) {
  return cleanEvidenceText(item.path || item.subtitle || item.locator, 92);
}

function relationshipReason(item: SearchResult) {
  const labels: Record<string, string> = {
    dense: "语义相似",
    lexical: "关键词命中",
    structured_multi_term: "结构化字段命中",
    query_vector: "向量检索",
    codex_vector: "会话语义",
    session_audit: "会话审计",
  };
  const reasons = (item.channels || []).map(
    (channel) => labels[channel] || channel,
  );
  if (item.evidence_role) reasons.push(item.evidence_role);
  return reasons.length ? reasons.join(" · ") : "项目内跨来源检索命中";
}

function evidencePreviewContent(item: SearchResult) {
  return item.snippet
    ? item.snippet.replace(/\\n/g, "\n")
    : presentSearchSnippet(item.source, item.title, item.snippet, true);
}

function searchResultPath(item: SearchResult) {
  return String(item.path || item.subtitle || "")
    .trim()
    .replaceAll("\\", "/");
}

function sessionCodeChangeEvidence(
  turns: CodexTimelineTurn[],
  activeTurnId?: string,
): SearchResult[] {
  const active = turns.find((item) => item.id === activeTurnId);
  const orderedTurns = [
    ...(active ? [active] : []),
    ...turns
      .filter((item) => item.id !== activeTurnId)
      .sort((left, right) => right.ordinal - left.ordinal),
  ];
  const seen = new Set<string>();
  const results: SearchResult[] = [];

  orderedTurns.forEach((item) => {
    const changes: CodexTimelineFileChange[] = item.file_changes?.length
      ? item.file_changes
      : item.files.map((path) => ({
          id: `${item.id}:${path}`,
          path,
          change_type: "update",
          status: item.status,
          locator: item.locator || item.id,
        }));
    changes.forEach((change) => {
      const path = change.path.trim().replaceAll("\\", "/");
      if (!presentableFilePath(path) || seen.has(path)) return;
      seen.add(path);
      const delta =
        typeof change.additions === "number" ||
        typeof change.deletions === "number"
          ? `+${change.additions || 0} / -${change.deletions || 0}`
          : "未记录逐行统计";
      const auditRole =
        item.id === activeTurnId
          ? "本轮代码变更"
          : `会话第 ${item.ordinal} 轮代码变更`;
      results.push({
        entity_id: `session-code-change:${encodeURIComponent(item.id)}:${encodeURIComponent(path)}`,
        entity_type: "SessionFileChange",
        source: "code",
        title: fileLabel(path),
        subtitle: path,
        path,
        locator: change.locator || item.locator || item.id,
        snippet: [
          `${auditRole} · ${changeTypeLabel(change.change_type)} · ${statusLabel(change.status)}`,
          delta,
          change.patch?.trim() || `会话审计记录了 ${path} 的文件级变更。`,
        ].join("\n"),
        status: change.status || item.status,
        evidence_role: auditRole,
        channels: ["session_audit"],
      });
    });
  });

  return results;
}

function RelatedSessionEvidence({
  results,
  recordedChangeCount,
  projectId,
  onClose,
}: {
  results: SearchResult[];
  recordedChangeCount?: number;
  projectId: string;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState<string | null>(
    results[0]?.entity_id || null,
  );
  const [sourceFilter, setSourceFilter] = useState("all");
  const [evidencePage, setEvidencePage] = useState(0);
  const [copiedLocator, setCopiedLocator] = useState(false);
  const sessionAuditCount = results.filter((item) =>
    item.channels?.includes("session_audit"),
  ).length;
  const visibleResults = useMemo(
    () =>
      sourceFilter === "all"
        ? results
        : results.filter((item) => item.source === sourceFilter),
    [results, sourceFilter],
  );
  const evidencePageCount = Math.max(
    1,
    Math.ceil(visibleResults.length / EVIDENCE_PAGE_SIZE),
  );
  const pagedResults = useMemo(
    () =>
      visibleResults.slice(
        evidencePage * EVIDENCE_PAGE_SIZE,
        (evidencePage + 1) * EVIDENCE_PAGE_SIZE,
      ),
    [evidencePage, visibleResults],
  );
  const groups = useMemo(() => {
    const output = new Map<string, SearchResult[]>();
    pagedResults.forEach((item) => {
      const group = output.get(item.source) || [];
      group.push(item);
      output.set(item.source, group);
    });
    return output;
  }, [pagedResults]);
  useEffect(() => {
    if (!pagedResults.length) {
      setSelectedId(null);
      return;
    }
    if (!pagedResults.some((item) => item.entity_id === selectedId)) {
      setSelectedId(pagedResults[0].entity_id);
    }
  }, [pagedResults, selectedId]);
  useEffect(() => {
    setEvidencePage(0);
  }, [sourceFilter]);
  useEffect(() => {
    if (evidencePage >= evidencePageCount) {
      setEvidencePage(evidencePageCount - 1);
    }
  }, [evidencePage, evidencePageCount]);
  const selected =
    pagedResults.find((item) => item.entity_id === selectedId) ||
    pagedResults[0];

  return (
    <aside className="session-evidence">
      <header>
        <span>
          <Link2 size={17} />
          <strong>关联来源</strong>
        </span>
        <span className="session-evidence__header-actions">
          <em>{results.length} 项可互查</em>
          <button
            onClick={onClose}
            aria-label="收起关联来源"
            title="收起关联来源"
          >
            <PanelRightClose size={15} />
          </button>
        </span>
      </header>
      <nav className="session-evidence__filters" aria-label="关联来源类型">
        {[
          ["all", "全部"],
          ["code", "代码"],
          ["document", "文档"],
          ["experiment", "实验"],
          ["workspace", "任务"],
        ].map(([value, label]) => (
          <button
            className={sourceFilter === value ? "is-active" : ""}
            key={value}
            onClick={() => setSourceFilter(value)}
          >
            {label}
          </button>
        ))}
      </nav>
      {sessionAuditCount ? (
        <div
          className="session-evidence__scope"
          role="status"
          aria-label="会话改动来源说明"
        >
          <span>
            <FileDiff size={13} />
            <strong>{sessionAuditCount}</strong> 个变更文件
            {recordedChangeCount && recordedChangeCount !== sessionAuditCount
              ? ` · ${recordedChangeCount} 条变更记录`
              : ""}
          </span>
          <small>直接来自会话审计；项目搜索用于补充其他来源</small>
        </div>
      ) : null}
      <div className="session-evidence__scroll">
        {[...groups.entries()].map(([source, items]) => (
          <section key={source}>
            <h3>
              {sourceLabels[source] || source}
              <span>{items.length}</span>
            </h3>
            {items.map((item) => (
              <article
                className={
                  selected?.entity_id === item.entity_id ? "is-selected" : ""
                }
                key={`${item.source}-${item.entity_id}`}
              >
                <button
                  className="session-evidence__item"
                  onClick={() => setSelectedId(item.entity_id)}
                >
                  <SourceMark source={source} />
                  <span>
                    <strong>{semanticSourceTitle(item)}</strong>
                    <small>
                      <code>{sourceLocation(item)}</code>
                      <em>{relationshipReason(item)}</em>
                    </small>
                  </span>
                </button>
                <button
                  className="session-evidence__open"
                  onClick={() => navigate(sourceDestination(projectId, item))}
                  aria-label={`打开来源：${semanticSourceTitle(item)}`}
                  title="打开原始来源"
                >
                  <ArrowUpRight size={14} />
                </button>
              </article>
            ))}
          </section>
        ))}
        {!visibleResults.length ? (
          <div className="session-evidence__empty">
            <Link2 size={20} />
            <strong>
              {results.length ? "该类型暂无关联" : "还没有跨来源关联"}
            </strong>
            <p>
              {results.length
                ? "切换到“全部”查看本轮已经命中的其他来源。"
                : "索引到代码、文档或实验后，来源会在这里按类型归档。"}
            </p>
          </div>
        ) : null}
      </div>
      <InlinePager
        label="关联来源分页"
        page={evidencePage}
        total={visibleResults.length}
        pageSize={EVIDENCE_PAGE_SIZE}
        onPage={setEvidencePage}
      />
      {selected ? (
        <section className="source-audit-card" aria-label="来源互查详情">
          <header>
            <span>当前来源</span>
            <Status value={selected.status || "indexed"}>
              {statusLabel(selected.status || "indexed")}
            </Status>
          </header>
          <div className="source-audit-card__headline">
            <SourceMark source={selected.source} />
            <span>
              <h3>{semanticSourceTitle(selected)}</h3>
              <code className="source-audit-card__location">
                {sourceLocation(selected)}
              </code>
            </span>
          </div>
          <ExpandableText
            className="source-audit-card__reader"
            label="命中内容"
            title={`${semanticSourceTitle(selected)} · 完整命中内容`}
            description={`${sourceLabels[selected.source] || selected.source} · ${sourceLocation(selected)}`}
            content={evidencePreviewContent(selected)}
            previewSize="comfortable"
          />
          <div className="source-audit-card__facts">
            <span>
              <small>关联依据</small>
              <strong>{relationshipReason(selected)}</strong>
            </span>
            <span>
              <small>来源</small>
              <strong>
                {sourceLabels[selected.source] || selected.source}
              </strong>
            </span>
            {selected.version ? (
              <span>
                <small>版本</small>
                <strong title={selected.version}>
                  {cleanEvidenceText(selected.version, 24)}
                </strong>
              </span>
            ) : null}
          </div>
          <div className="source-audit-card__locator">
            <span>
              <small>审计定位</small>
              <code title={selected.locator}>{selected.locator}</code>
            </span>
            <button
              onClick={async () => {
                await navigator.clipboard?.writeText(selected.locator);
                setCopiedLocator(true);
                window.setTimeout(() => setCopiedLocator(false), 1200);
              }}
            >
              <Copy size={13} />
              {copiedLocator ? "已复制" : "复制"}
            </button>
          </div>
          <div className="source-audit-card__actions">
            <Button
              variant="primary"
              onClick={() => navigate(sourceDestination(projectId, selected))}
            >
              <ArrowUpRight size={15} />
              打开来源
            </Button>
            {selected.source === "code" ? (
              <Button
                variant="quiet"
                onClick={() =>
                  navigate(sourceGraphDestination(projectId, selected))
                }
              >
                <Network size={15} />
                关系图核对
              </Button>
            ) : null}
          </div>
        </section>
      ) : null}
    </aside>
  );
}

function SessionOverviewAudit({ turn }: { turn: CodexTimelineTurn }) {
  const [completedPage, setCompletedPage] = useState(0);
  const [checkPage, setCheckPage] = useState(0);
  const sections = structureTaskSummary(turn.summary);
  const conclusion =
    sections.find((section) => section.key === "decision")?.items[0] ||
    sections.find((section) => section.key === "validation")?.items[0] ||
    sections.find((section) => section.key === "delivery")?.items[0] ||
    cleanEvidenceText(turn.goal, 180);
  const completed = sections
    .filter((section) => section.key === "delivery" || section.key === "change")
    .flatMap((section) => section.items);
  const validations = (turn.operations || []).filter(
    (operation) => operation.kind === "validation",
  );
  const validationSummaries =
    sections.find((section) => section.key === "validation")?.items || [];
  const risks =
    sections.find((section) => section.key === "followup")?.items || [];
  const completedItems = completed.length
    ? completed
    : turn.files.map((path) => `完成 ${fileLabel(path)} 的相关实现`);
  const checkItems = [
    ...(validations.length
      ? validations.map((operation) => ({
          id: operation.id,
          kind: "validation" as const,
          title: cleanEvidenceText(operation.command || operation.name, 96),
          detail: `${formatTime(operation.timestamp)} · 可查看命令与原始输出`,
          status: operation.status,
        }))
      : validationSummaries.map((item, index) => ({
          id: `summary-${index}-${item}`,
          kind: "validation" as const,
          title: item,
          detail: `${turn.validation_count} 项验证记录 · 可进入执行记录互查`,
          status: "passed",
        }))),
    ...risks.map((item, index) => ({
      id: `risk-${index}-${item}`,
      kind: "risk" as const,
      title: item,
      detail: "风险与后续 · 需要保留在审计记录中",
      status: "pending",
    })),
  ];
  const visibleCompleted = completedItems.slice(
    completedPage * AUDIT_ITEM_PAGE_SIZE,
    (completedPage + 1) * AUDIT_ITEM_PAGE_SIZE,
  );
  const visibleChecks = checkItems.slice(
    checkPage * AUDIT_ITEM_PAGE_SIZE,
    (checkPage + 1) * AUDIT_ITEM_PAGE_SIZE,
  );
  useEffect(() => {
    setCompletedPage(0);
    setCheckPage(0);
  }, [turn.id]);

  return (
    <div className="session-overview-audit">
      <section className="session-overview-audit__conclusion">
        <header>
          <CheckCircle2 size={18} />
          <h3>本轮结论</h3>
        </header>
        <strong>{conclusion || "该轮次还没有形成可用结论。"}</strong>
        {sections.length ? (
          <ul>
            {sections
              .filter((section) => section.key !== "followup")
              .flatMap((section) => section.items)
              .slice(0, 3)
              .map((item) => (
                <li key={item}>{item}</li>
              ))}
          </ul>
        ) : null}
      </section>

      <section className="session-overview-audit__section">
        <header>
          <h3>完成事项</h3>
          <span>{completedItems.length}</span>
        </header>
        <div className="session-overview-audit__rows">
          {visibleCompleted.map((item, index) => {
            const absoluteIndex = completedPage * AUDIT_ITEM_PAGE_SIZE + index;
            return (
              <article key={`${item}-${absoluteIndex}`}>
                <span className="is-code">
                  {absoluteIndex % 2 === 0 ? (
                    <Code2 size={15} />
                  ) : (
                    <GitCommitHorizontal size={15} />
                  )}
                </span>
                <div>
                  <strong>{cleanEvidenceText(item, 92)}</strong>
                  <small>
                    {turn.files[absoluteIndex]
                      ? `${fileLabel(turn.files[absoluteIndex])} · 可在“代码变更”中定位`
                      : "由本轮任务总结归纳，可展开原始总结核对"}
                  </small>
                </div>
                <Status value="completed">已完成</Status>
              </article>
            );
          })}
        </div>
        <InlinePager
          label="完成事项分页"
          page={completedPage}
          total={completedItems.length}
          pageSize={AUDIT_ITEM_PAGE_SIZE}
          onPage={setCompletedPage}
        />
      </section>

      <section className="session-overview-audit__section">
        <header>
          <h3>验证与风险</h3>
          <span>{checkItems.length}</span>
        </header>
        <div className="session-overview-audit__checks">
          {visibleChecks.map((item) => (
            <article className={`is-${item.kind}`} key={item.id}>
              {item.kind === "risk" ? (
                <TriangleAlert size={16} />
              ) : (
                <Beaker size={16} />
              )}
              <div>
                <strong>{item.title}</strong>
                <small>{item.detail}</small>
              </div>
              <Status value={item.status}>{statusLabel(item.status)}</Status>
            </article>
          ))}
          {!checkItems.length ? (
            <p className="session-compact-empty">
              本轮尚未形成可单独展示的验证或风险条目。
            </p>
          ) : null}
        </div>
        <InlinePager
          label="验证与风险分页"
          page={checkPage}
          total={checkItems.length}
          pageSize={AUDIT_ITEM_PAGE_SIZE}
          onPage={setCheckPage}
        />
      </section>

      {turn.summary ? (
        <details className="raw-summary">
          <summary>查看原始总结</summary>
          <p>{cleanEvidenceText(turn.summary, 1800)}</p>
        </details>
      ) : null}
    </div>
  );
}

function fileLabel(path: string) {
  const parts = path.split("/");
  return parts.slice(-2).join("/");
}

function changeTypeLabel(value: string) {
  return (
    {
      add: "新增",
      create: "新增",
      update: "修改",
      modify: "修改",
      delete: "删除",
      patch: "补丁",
    }[value.toLowerCase()] || value
  );
}

function presentableFilePath(path: string) {
  const normalized = path.trim().replaceAll("\\", "/");
  const basename = normalized.split("/").at(-1) || "";
  if (!normalized || normalized.length > 260 || /[\n{}]/.test(normalized))
    return false;
  if (
    /^\d+(?:\.\d+)?$/.test(basename) ||
    /^\d+(?:px|rem|em|vh|vw)\//i.test(normalized)
  ) {
    return false;
  }
  return (
    ["Dockerfile", "Makefile", "LICENSE", "README", "Procfile"].includes(
      basename,
    ) || /\.[A-Za-z][A-Za-z0-9_-]{0,11}$/.test(basename)
  );
}

function auditEventLabel(locator: string) {
  const event = locator.match(/#event=(\d+)/)?.[1];
  return event ? `原始补丁事件 ${event}` : "原始补丁事件";
}

function FilePatchDetail({
  change,
  codeResult,
  sourcePath,
  graphPath,
  operations,
  related,
}: {
  change: CodexTimelineFileChange & {
    eventCount: number;
    changeTypes: string[];
  };
  codeResult?: SearchResult;
  sourcePath: string;
  graphPath: string;
  operations: CodexTimelineOperation[];
  related: SearchResult[];
}) {
  const navigate = useNavigate();
  const [showAll, setShowAll] = useState(false);
  const [copied, setCopied] = useState(false);
  const evidence = useMemo(
    () => findSessionDiffEvidence(change, related, operations),
    [change, operations, related],
  );
  const parsed = useMemo(
    () => (evidence ? parseSessionDiff(evidence.patch) : null),
    [evidence],
  );
  const visibleHunks = useMemo(() => {
    if (!parsed || showAll) return parsed?.hunks || [];
    let remaining = 220;
    return parsed.hunks
      .map((hunk) => {
        const lines = hunk.lines.slice(0, Math.max(0, remaining));
        remaining -= lines.length;
        return { ...hunk, lines };
      })
      .filter((hunk) => hunk.lines.length);
  }, [parsed, showAll]);
  const lineRange =
    codeResult?.start_line && codeResult?.end_line
      ? `L${codeResult.start_line}–L${codeResult.end_line}`
      : null;

  const copyPatch = async () => {
    if (!evidence) return;
    await navigator.clipboard?.writeText(evidence.patch);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <section
      className="session-file-diff"
      aria-label={`代码变更明细：${change.path}`}
    >
      <header className="session-file-diff__header">
        <div>
          <span>
            <FileDiff size={15} />
            逐行变更
          </span>
          <strong>{change.path}</strong>
          <small>
            {evidence?.label || "文件级审计记录"}
            {lineRange ? ` · 代码证据 ${lineRange}` : ""}
          </small>
        </div>
        {parsed ? (
          <div className="session-file-diff__stats" aria-label="变更统计">
            <span className="is-addition">+{parsed.additions}</span>
            <span className="is-deletion">−{parsed.deletions}</span>
            <span>{parsed.hunks.length} 个变更块</span>
          </div>
        ) : null}
        <div className="session-file-diff__actions">
          {evidence ? (
            <button onClick={copyPatch} aria-label={`复制 ${change.path} 补丁`}>
              {copied ? <CheckCircle2 size={14} /> : <Copy size={14} />}
              {copied ? "已复制" : "复制补丁"}
            </button>
          ) : null}
          <button onClick={() => navigate(sourcePath)}>
            <Code2 size={14} />
            打开源码
          </button>
          <button onClick={() => navigate(graphPath)}>
            <Network size={14} />
            查看关系
          </button>
        </div>
      </header>

      {parsed?.hunks.length ? (
        <div className="session-file-diff__viewport">
          <div className="session-file-diff__columns" aria-hidden="true">
            <span>旧行</span>
            <span>新行</span>
            <span />
            <span>代码</span>
          </div>
          {visibleHunks.map((hunk) => (
            <section className="session-diff-hunk" key={hunk.id}>
              <header>
                <code>{hunk.header}</code>
                <span>
                  旧 L{hunk.oldStart || "—"} · 新 L{hunk.newStart || "—"}
                </span>
              </header>
              <div>
                {hunk.lines.map((line) => (
                  <div
                    className={`session-diff-line is-${line.kind}`}
                    key={line.id}
                  >
                    <span>{line.oldLine ?? ""}</span>
                    <span>{line.newLine ?? ""}</span>
                    <b>{line.marker}</b>
                    <code>{line.content || " "}</code>
                  </div>
                ))}
              </div>
            </section>
          ))}
          {parsed.lineCount > 220 && !showAll ? (
            <button
              className="session-file-diff__show-all"
              onClick={() => setShowAll(true)}
            >
              展开全部 {parsed.lineCount} 行
            </button>
          ) : null}
        </div>
      ) : (
        <div className="session-file-diff__empty">
          <FileDiff size={20} />
          <div>
            <strong>该条旧记录没有保存逐行补丁</strong>
            <p>
              已保留文件级事件和原始定位；不会根据当前文件状态伪造历史增删行。
            </p>
          </div>
        </div>
      )}

      <footer className="session-file-diff__audit">
        <span>
          <ShieldCheck size={13} />
          {auditEventLabel(change.locator)}
        </span>
        <code title={change.locator}>{change.locator}</code>
        {evidence?.truncated ? <em>原始补丁过长，当前显示已截断</em> : null}
      </footer>
    </section>
  );
}

function FileChangeLedger({
  turn,
  projectId,
  related,
}: {
  turn: CodexTimelineTurn;
  projectId: string;
  related: SearchResult[];
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const changes = useMemo(() => {
    const fallback: CodexTimelineFileChange[] = turn.files.map((path) => ({
      id: `${turn.id}:${path}`,
      path,
      change_type: "update",
      status: "completed",
      locator: turn.locator || turn.id,
    }));
    const source = turn.file_changes?.length ? turn.file_changes : fallback;
    const grouped = new Map<
      string,
      CodexTimelineFileChange & { eventCount: number; changeTypes: string[] }
    >();
    source.forEach((change) => {
      if (!presentableFilePath(change.path)) return;
      const current = grouped.get(change.path);
      if (current) {
        current.eventCount += 1;
        if (!current.changeTypes.includes(change.change_type)) {
          current.changeTypes.push(change.change_type);
        }
        if (change.status === "failed") current.status = "failed";
        if (change.patch) {
          const shouldReplace =
            !current.patch ||
            (change.patch_format === "unified" &&
              current.patch_format !== "unified");
          if (shouldReplace) {
            current.patch = change.patch;
            current.additions = change.additions;
            current.deletions = change.deletions;
            current.hunk_count = change.hunk_count;
            current.patch_truncated = change.patch_truncated;
            current.patch_format = change.patch_format;
          } else if (
            change.patch !== current.patch &&
            change.patch_format === current.patch_format
          ) {
            current.patch = `${current.patch}\n${change.patch}`;
            current.additions =
              (current.additions || 0) + (change.additions || 0);
            current.deletions =
              (current.deletions || 0) + (change.deletions || 0);
            current.hunk_count =
              (current.hunk_count || 0) + (change.hunk_count || 0);
            current.patch_truncated =
              current.patch_truncated || change.patch_truncated;
          }
        }
        return;
      }
      grouped.set(change.path, {
        ...change,
        eventCount: 1,
        changeTypes: [change.change_type],
      });
    });
    return [...grouped.values()];
  }, [turn.file_changes, turn.files, turn.id, turn.locator]);
  useEffect(() => {
    setExpandedPath(changes[0]?.path || null);
  }, [turn.id]);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return changes;
    return changes.filter((change) =>
      change.path.toLowerCase().includes(normalized),
    );
  }, [changes, query]);
  if (!changes.length) {
    return (
      <p className="session-compact-empty">本轮没有记录到可定位的代码变更。</p>
    );
  }
  return (
    <div className="file-change-ledger">
      <div className="ledger-toolbar">
        <label>
          <Search size={14} />
          <input
            type="search"
            aria-label="搜索变更文件"
            placeholder="按路径筛选"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
          />
        </label>
        <span>
          {filtered.length} 个文件
          {changes.length !== (turn.file_changes?.length || turn.files.length)
            ? ` · 已合并 ${turn.file_changes?.length || turn.files.length} 条记录`
            : ""}
        </span>
      </div>
      <div className="file-change-ledger__list">
        {filtered
          .slice(
            page * FILE_LEDGER_PAGE_SIZE,
            (page + 1) * FILE_LEDGER_PAGE_SIZE,
          )
          .map((change) => {
            const codeResult = related.find(
              (item) =>
                item.source === "code" &&
                (item.path === change.path ||
                  item.subtitle === change.path ||
                  item.locator.includes(`:${change.path}#`)),
            );
            const sourcePath = codeResult
              ? sourceDestination(projectId, codeResult)
              : `/p/${encodeURIComponent(projectId)}/code?file=${encodeURIComponent(change.path)}`;
            const graphPath = codeResult
              ? sourceGraphDestination(projectId, codeResult)
              : `/p/${encodeURIComponent(projectId)}/map?code=${encodeURIComponent(change.path)}`;
            return (
              <article
                className={expandedPath === change.path ? "is-expanded" : ""}
                key={`${change.id}-${change.path}`}
              >
                <div className="file-change-ledger__row">
                  <button
                    className="file-change-ledger__trigger"
                    onClick={() =>
                      setExpandedPath((current) =>
                        current === change.path ? null : change.path,
                      )
                    }
                    aria-expanded={expandedPath === change.path}
                    aria-label={`${expandedPath === change.path ? "收起" : "查看"} ${change.path} 逐行变更`}
                  >
                    <span className="file-change-ledger__icon">
                      <FileCode2 size={17} />
                    </span>
                    <span className="file-change-ledger__identity">
                      <strong>{fileLabel(change.path)}</strong>
                      <small title={change.path}>{change.path}</small>
                    </span>
                    <em>
                      {change.changeTypes.map(changeTypeLabel).join(" / ")}
                      {change.eventCount > 1
                        ? ` · ${change.eventCount} 条`
                        : ""}
                    </em>
                    {change.patch ? (
                      <span className="file-change-ledger__delta">
                        <b>+{change.additions || 0}</b>
                        <i>−{change.deletions || 0}</i>
                      </span>
                    ) : (
                      <span className="file-change-ledger__delta is-empty">
                        无逐行记录
                      </span>
                    )}
                    {expandedPath === change.path ? (
                      <ChevronDown size={16} />
                    ) : (
                      <ChevronRight size={16} />
                    )}
                  </button>
                  <div className="file-change-ledger__actions">
                    <button onClick={() => navigate(sourcePath)}>
                      <Code2 size={14} />
                      源码
                    </button>
                    <button
                      onClick={() => navigate(graphPath)}
                      aria-label={`核对 ${change.path} 关系`}
                    >
                      <Network size={14} />
                    </button>
                  </div>
                </div>
                {expandedPath === change.path ? (
                  <FilePatchDetail
                    change={change}
                    codeResult={codeResult}
                    sourcePath={sourcePath}
                    graphPath={graphPath}
                    operations={turn.operations || []}
                    related={related}
                  />
                ) : null}
              </article>
            );
          })}
        {!filtered.length ? (
          <p className="session-compact-empty">没有匹配该路径的变更文件。</p>
        ) : null}
      </div>
      <InlinePager
        label="代码变更分页"
        page={page}
        total={filtered.length}
        pageSize={FILE_LEDGER_PAGE_SIZE}
        onPage={(nextPage) => {
          setPage(nextPage);
          setExpandedPath(null);
        }}
      />
    </div>
  );
}

function operationTitle(operation: CodexTimelineOperation) {
  const command = cleanEvidenceText(operation.command?.split("\n")[0], 150);
  return command || humanizeSearchTitle(operation.name) || "未命名操作";
}

function OperationLedger({
  operations,
  commandCount,
  validationCount,
  loading,
}: {
  operations: CodexTimelineOperation[];
  commandCount: number;
  validationCount: number;
  loading?: boolean;
}) {
  const [filter, setFilter] = useState<"all" | CodexTimelineOperation["kind"]>(
    "all",
  );
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return operations.filter((item) => {
      if (filter !== "all" && item.kind !== filter) return false;
      if (!normalized) return true;
      return [item.name, item.command, item.output]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalized));
    });
  }, [filter, operations, query]);
  const visible = filtered.slice(
    page * OPERATION_LEDGER_PAGE_SIZE,
    (page + 1) * OPERATION_LEDGER_PAGE_SIZE,
  );
  const filters: Array<{
    value: "all" | CodexTimelineOperation["kind"];
    label: string;
  }> = [
    { value: "all", label: `全部 ${operations.length}` },
    {
      value: "command",
      label: `命令 ${operations.filter((item) => item.kind === "command").length}`,
    },
    {
      value: "validation",
      label: `验证 ${operations.filter((item) => item.kind === "validation").length}`,
    },
    {
      value: "tool",
      label: `工具 ${operations.filter((item) => item.kind === "tool").length}`,
    },
  ];

  if (loading) {
    return (
      <div className="operation-ledger__compat">
        <Terminal size={20} />
        <div>
          <strong>正在加载该轮审计记录</strong>
          <p>命令、工具结果、退出码与原始定位会按需载入。</p>
        </div>
      </div>
    );
  }

  if (!operations.length) {
    return (
      <div className="operation-ledger__compat">
        <Terminal size={20} />
        <div>
          <strong>旧版索引只保留了执行计数</strong>
          <p>
            已知有 {commandCount} 次开发操作、{validationCount}{" "}
            项测试与检查；重新同步会话后可查看命令、输出和审计定位。
          </p>
        </div>
      </div>
    );
  }

  const copy = async (key: string, value: string) => {
    await navigator.clipboard?.writeText(value);
    setCopied(key);
    window.setTimeout(() => setCopied(null), 1200);
  };

  return (
    <div className="operation-ledger">
      <div className="operation-ledger__toolbar">
        <div className="operation-ledger__filters" aria-label="执行记录筛选">
          {filters.map((item) => (
            <button
              className={filter === item.value ? "is-active" : ""}
              key={item.value}
              onClick={() => {
                setFilter(item.value);
                setPage(0);
                setExpanded(null);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
        <label>
          <Search size={14} />
          <input
            type="search"
            aria-label="搜索执行记录"
            placeholder="搜索命令或输出"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
              setExpanded(null);
            }}
          />
        </label>
      </div>
      <div className="operation-ledger__list">
        {visible.map((operation, index) => {
          const open = expanded === operation.id;
          const outputLines = (operation.output || "")
            .replace(/\r/g, "")
            .split("\n");
          const Icon =
            operation.kind === "command"
              ? Terminal
              : operation.kind === "validation"
                ? Beaker
                : Wrench;
          return (
            <article className={open ? "is-expanded" : ""} key={operation.id}>
              <button
                className="operation-row"
                onClick={() => setExpanded(open ? null : operation.id)}
                aria-expanded={open}
              >
                <span className={`operation-row__kind is-${operation.kind}`}>
                  <Icon size={16} />
                </span>
                <span className="operation-row__body">
                  <small>
                    {operation.kind === "command"
                      ? "命令执行"
                      : operation.kind === "validation"
                        ? "验证记录"
                        : humanizeSearchTitle(operation.name)}
                    <em>
                      #
                      {String(
                        page * OPERATION_LEDGER_PAGE_SIZE + index + 1,
                      ).padStart(2, "0")}
                    </em>
                  </small>
                  <strong>{operationTitle(operation)}</strong>
                  {operation.output ? (
                    <span className="operation-row__preview">
                      {cleanEvidenceText(operation.output, 120)}
                    </span>
                  ) : null}
                </span>
                <Status value={operation.status}>
                  {statusLabel(operation.status)}
                </Status>
                <span
                  className={`operation-row__exit ${
                    operation.exit_code === 0 ? "is-success" : ""
                  }`}
                >
                  {typeof operation.exit_code === "number"
                    ? `exit ${operation.exit_code}`
                    : "无退出码"}
                </span>
                <time>{formatTime(operation.timestamp)}</time>
                <ChevronDown size={16} />
              </button>
              {open ? (
                <div className="operation-detail">
                  <div className="operation-detail__command">
                    <header>
                      <span>执行命令</span>
                      <button
                        onClick={() =>
                          copy(
                            `${operation.id}:command`,
                            operation.command || operation.name,
                          )
                        }
                      >
                        <Copy size={14} />
                        {copied === `${operation.id}:command`
                          ? "已复制"
                          : "复制命令"}
                      </button>
                    </header>
                    <code>{operation.command || "未记录命令"}</code>
                  </div>
                  <div
                    className="operation-detail__resultbar"
                    aria-label="执行结果摘要"
                  >
                    <span>
                      <small>状态</small>
                      <strong>{statusLabel(operation.status)}</strong>
                    </span>
                    <span>
                      <small>退出码</small>
                      <strong>
                        {typeof operation.exit_code === "number"
                          ? operation.exit_code
                          : "未返回"}
                      </strong>
                    </span>
                    <span>
                      <small>执行时间</small>
                      <strong>{formatTime(operation.timestamp)}</strong>
                    </span>
                    <span>
                      <small>输出</small>
                      <strong>
                        {operation.output
                          ? `${outputLines.length} 行`
                          : "未记录"}
                      </strong>
                    </span>
                  </div>
                  <ExpandableText
                    className="operation-detail__output"
                    label="标准输出"
                    title={`${operationTitle(operation)} · 完整输出`}
                    description={`${operation.kind === "validation" ? "验证记录" : "执行记录"} · ${outputLines.length} 行`}
                    content={operation.output}
                    emptyText="[未记录标准输出]"
                    previewSize="comfortable"
                    tone="terminal"
                  />
                  <div className="operation-detail__locator">
                    <span>
                      <small>审计定位</small>
                      <code title={operation.locator}>{operation.locator}</code>
                    </span>
                    <button
                      onClick={() =>
                        copy(`${operation.id}:locator`, operation.locator)
                      }
                    >
                      <Copy size={13} />
                      {copied === `${operation.id}:locator`
                        ? "已复制"
                        : "复制定位"}
                    </button>
                  </div>
                </div>
              ) : null}
            </article>
          );
        })}
        {!visible.length ? (
          <p className="operation-ledger__empty">没有匹配的执行记录。</p>
        ) : null}
      </div>
      <InlinePager
        label="执行记录分页"
        page={page}
        total={filtered.length}
        pageSize={OPERATION_LEDGER_PAGE_SIZE}
        onPage={(nextPage) => {
          setPage(nextPage);
          setExpanded(null);
        }}
      />
    </div>
  );
}

type SessionDetailTab = "overview" | "files" | "operations";
type SessionNodeOffsets = Record<string, { x: number; y: number }>;

const SESSION_CHAIN_SWIPE_START = 8;
const SESSION_CHAIN_SWIPE_COMMIT = 42;
const SESSION_CHAIN_SCROLLBAR_GUTTER = 18;

function chainLayoutStorageKey(threadId: string) {
  return `rag_session_chain_layout:v1:${threadId}`;
}

function readChainLayout(threadId: string): SessionNodeOffsets {
  try {
    return JSON.parse(
      window.localStorage.getItem(chainLayoutStorageKey(threadId)) || "{}",
    ) as SessionNodeOffsets;
  } catch {
    return {};
  }
}

export function SessionDetail() {
  const { projectId = "", threadId = "" } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [turnIndex, setTurnIndex] = useState(0);
  const [turnPage, setTurnPage] = useState(() => {
    const parsed = Number(searchParams.get("page") || 0);
    return Number.isInteger(parsed) && parsed >= 0 ? parsed : 0;
  });
  const [activeTab, setActiveTab] = useState<SessionDetailTab>("overview");
  const [timelineOpen, setTimelineOpen] = useState(true);
  const [relatedOpen, setRelatedOpen] = useState(true);
  const [boardZoom, setBoardZoom] = useState(1);
  const [nodeOffsets, setNodeOffsets] = useState<SessionNodeOffsets>(() =>
    readChainLayout(threadId),
  );
  const nodeOffsetsRef = useRef(nodeOffsets);
  const boardRef = useRef<HTMLDivElement>(null);
  const boardSettleTimerRef = useRef<number | null>(null);
  const dragState = useRef({
    active: false,
    moved: false,
    pointerId: -1,
    startX: 0,
    startY: 0,
    startScroll: 0,
  });
  const nodeDragState = useRef<{
    active: boolean;
    moved: boolean;
    pointerId: number;
    turnId: string;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const suppressNodeClick = useRef(false);
  const timelineQuery = useQuery({
    queryKey: ["session-timeline", threadId, turnPage],
    queryFn: ({ signal }) =>
      api.sessions.timeline(
        threadId,
        signal,
        turnPage * TIMELINE_PAGE_SIZE,
        TIMELINE_PAGE_SIZE,
      ),
    enabled: Boolean(threadId),
  });
  const dashboardQuery = useQuery({
    queryKey: ["project-dashboard", projectId],
    queryFn: () => api.projects.getDashboard(projectId),
    enabled: Boolean(projectId),
  });
  const codexStatusQuery = useQuery({
    queryKey: ["codex-bridge-status", projectId],
    queryFn: () => api.codexBridge.status(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 20_000,
  });
  const timeline = timelineQuery.data;
  const turnPageCount = Math.max(
    1,
    Math.ceil((timeline?.turn_count || 0) / TIMELINE_PAGE_SIZE),
  );
  const turn: CodexTimelineTurn | undefined = timeline?.turns[turnIndex];
  const turnAuditQuery = useQuery({
    queryKey: ["session-turn-audit", threadId, turn?.id],
    queryFn: ({ signal }) =>
      api.sessions.turnAudit(threadId, turn?.id || "", signal),
    enabled: Boolean(threadId && turn?.id),
  });
  const auditedTurn =
    turnAuditQuery.data?.id === turn?.id ? turnAuditQuery.data : turn;
  useEffect(() => {
    setTurnIndex(0);
    setTurnPage(0);
    setActiveTab("overview");
    setNodeOffsets(readChainLayout(threadId));
    nodeDragState.current = null;
    dragState.current.active = false;
    suppressNodeClick.current = false;
    if (boardSettleTimerRef.current !== null) {
      window.clearTimeout(boardSettleTimerRef.current);
      boardSettleTimerRef.current = null;
    }
  }, [threadId]);
  useEffect(
    () => () => {
      if (boardSettleTimerRef.current !== null) {
        window.clearTimeout(boardSettleTimerRef.current);
      }
    },
    [],
  );
  useEffect(() => {
    setTurnIndex(0);
  }, [turnPage]);
  useEffect(() => {
    if (timeline && turnPage >= turnPageCount) setTurnPage(turnPageCount - 1);
  }, [timeline, turnPage, turnPageCount]);
  useEffect(() => {
    if (timeline?.turns.length && turnIndex >= timeline.turns.length)
      setTurnIndex(0);
  }, [timeline?.turns.length, turnIndex]);
  useEffect(() => {
    const requestedTurn = searchParams.get("turn");
    if (!requestedTurn || !timeline?.turns.length) return;
    const requestedIndex = timeline.turns.findIndex(
      (item) =>
        item.id === requestedTurn || String(item.ordinal) === requestedTurn,
    );
    if (requestedIndex >= 0 && requestedIndex !== turnIndex) {
      setTurnIndex(requestedIndex);
    }
  }, [searchParams, timeline?.turns, turnIndex]);
  useEffect(() => {
    const requestedTab = searchParams.get("tab");
    if (
      requestedTab === "overview" ||
      requestedTab === "files" ||
      requestedTab === "operations"
    ) {
      setActiveTab(requestedTab);
    }
  }, [searchParams]);
  useEffect(() => {
    nodeOffsetsRef.current = nodeOffsets;
  }, [nodeOffsets]);
  const relatedSearchText = cleanEvidenceText(
    [timeline?.title, turn?.goal, ...(turn?.files || []).slice(0, 4)]
      .filter(Boolean)
      .join(" "),
    200,
  );
  const relatedQuery = useQuery({
    queryKey: [
      "session-detail-related",
      projectId,
      turn?.id,
      relatedSearchText,
    ],
    queryFn: () => api.search.project(projectId, relatedSearchText),
    enabled: Boolean(projectId && relatedSearchText),
  });
  const related = useMemo(() => {
    const timelineTurns = (timeline?.turns || []).map((item) =>
      item.id === auditedTurn?.id ? auditedTurn : item,
    );
    const auditedChanges = sessionCodeChangeEvidence(timelineTurns, turn?.id);
    const searched = (relatedQuery.data?.results || [])
      .filter((item) => item.source !== "codex")
      .slice(0, 24);
    const searchedCodePaths = new Set(
      searched
        .filter((item) => item.source === "code")
        .map(searchResultPath)
        .filter(Boolean),
    );
    const exactOnly = auditedChanges.filter(
      (item) => !searchedCodePaths.has(searchResultPath(item)),
    );
    const enrichedSearch = searched.map((item) => {
      if (item.source !== "code") return item;
      const auditMatch = auditedChanges.find(
        (candidate) => searchResultPath(candidate) === searchResultPath(item),
      );
      if (!auditMatch) return item;
      return {
        ...item,
        channels: [...new Set([...(item.channels || []), "session_audit"])],
        evidence_role: auditMatch.evidence_role,
      };
    });
    return [...exactOnly, ...enrichedSearch];
  }, [auditedTurn, relatedQuery.data?.results, timeline?.turns, turn?.id]);
  const workspacePath = String(
    dashboardQuery.data?.project.settings?.workspace_path || "",
  ).trim();
  const codexCanLaunch =
    !codexStatusQuery.isError &&
    codexStatusQuery.data?.availability === "ready" &&
    Boolean(workspacePath);
  const sessionTitle = humanizeSessionTitle(
    timeline?.title,
    timeline?.turns[0]?.goal,
    timeline?.updated_at,
  );

  const selectTurnPage = (page: number) => {
    const safePage = Math.max(0, Math.min(turnPageCount - 1, page));
    setTurnPage(safePage);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("page", String(safePage));
      next.delete("turn");
      return next;
    });
  };

  const selectTurn = (index: number) => {
    const selectedTurn = timeline?.turns[index];
    setTurnIndex(index);
    if (selectedTurn) {
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.set("turn", selectedTurn.id);
        return next;
      });
    }
    boardRef.current
      ?.querySelector<HTMLButtonElement>(`[data-turn-index="${index}"]`)
      ?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
        inline: "center",
      });
  };

  const setDetailTab = (tab: SessionDetailTab) => {
    setActiveTab(tab);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("tab", tab);
      if (turn) next.set("turn", turn.id);
      return next;
    });
  };

  const resetChainLayout = () => {
    setNodeOffsets({});
    window.localStorage.removeItem(chainLayoutStorageKey(threadId));
  };

  const persistChainLayout = (next: SessionNodeOffsets) => {
    window.localStorage.setItem(
      chainLayoutStorageKey(threadId),
      JSON.stringify(next),
    );
  };

  const nudgeChainNode = (turnId: string, delta: { x: number; y: number }) => {
    setNodeOffsets((current) => {
      const origin = current[turnId] || { x: 0, y: 0 };
      const next = {
        ...current,
        [turnId]: {
          x: Math.max(-26, Math.min(26, origin.x + delta.x)),
          y: Math.max(-22, Math.min(22, origin.y + delta.y)),
        },
      };
      nodeOffsetsRef.current = next;
      persistChainLayout(next);
      return next;
    });
  };

  if (!timelineQuery.isLoading && !timeline) {
    return (
      <EmptyState
        icon={<MessageSquareText size={23} />}
        title="没有找到该研发会话"
        description="返回总览选择仍在项目中的会话。"
        action={
          <Button onClick={() => navigate(`/p/${projectId}/sessions`)}>
            返回总览
          </Button>
        }
      />
    );
  }

  return (
    <div className="session-detail">
      <header className="session-detail__heading">
        <button onClick={() => navigate(`/p/${projectId}/sessions`)}>
          <ArrowLeft size={17} />
          会话总览
        </button>
        <div>
          <span className="session-detail__eyebrow">
            <ShieldCheck size={13} />
            可审计研发记录
          </span>
          <h1>{timeline ? sessionTitle : "读取会话中…"}</h1>
          <p>
            {timeline?.turn_count || 0} 个有效轮次 ·{" "}
            {timeline?.file_change_count || 0} 项代码变更 · 更新于{" "}
            {formatTime(timeline?.updated_at)}
          </p>
        </div>
        <div className="session-detail__heading-actions">
          <nav className="session-view-switcher" aria-label="会话视图">
            <button className="is-active" aria-current="page">
              <ListTree size={15} />
              链式审计
            </button>
            <button
              onClick={() =>
                navigate(
                  `/p/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(threadId)}/graph${turn ? `?turn=${encodeURIComponent(turn.id)}` : ""}`,
                )
              }
            >
              <Network size={15} />
              变动图谱
            </button>
          </nav>
          <Button
            variant="primary"
            disabled={!codexCanLaunch}
            title={
              codexCanLaunch ? undefined : "Codex 尚未连接，或项目工作区未配置"
            }
            onClick={() =>
              openCodex(projectId, workspacePath, turn?.goal || sessionTitle)
            }
          >
            <Play size={16} />
            继续会话
          </Button>
        </div>
      </header>

      <section
        className={`session-board-shell${timelineOpen ? "" : " is-collapsed"}`}
      >
        <header>
          <div>
            <strong>
              会话链路 · 共 {timeline?.turn_count || 0} 轮 · 当前第{" "}
              {turnPage + 1}页
            </strong>
            <span>左右拖动节点区逐轮切换；长距离浏览请使用底部滚动条</span>
          </div>
          <div className="session-board-shell__controls">
            <button
              onClick={() => selectTurn(Math.max(0, turnIndex - 1))}
              disabled={turnIndex === 0}
              aria-label="上一轮"
            >
              <ChevronLeft size={15} />
            </button>
            <label>
              <span>定位</span>
              <select
                aria-label="选择轮次"
                value={turnIndex}
                onChange={(event) => selectTurn(Number(event.target.value))}
              >
                {(timeline?.turns || []).map((item, index) => (
                  <option key={item.id} value={index}>
                    轮次 {item.ordinal} ·{" "}
                    {cleanEvidenceText(item.goal, 38) || "未命名目标"}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() =>
                selectTurn(
                  Math.min((timeline?.turns.length || 1) - 1, turnIndex + 1),
                )
              }
              disabled={turnIndex >= (timeline?.turns.length || 1) - 1}
              aria-label="下一轮"
            >
              <ChevronRight size={15} />
            </button>
            <button
              onClick={() =>
                setBoardZoom((value) => Math.max(0.78, value - 0.1))
              }
              aria-label="缩小会话链路"
            >
              <Minus size={14} />
            </button>
            <output className="session-board-shell__zoom">
              {Math.round(boardZoom * 100)}%
            </output>
            <button
              onClick={() =>
                setBoardZoom((value) => Math.min(1.18, value + 0.1))
              }
              aria-label="放大会话链路"
            >
              <Plus size={14} />
            </button>
            <button onClick={() => setBoardZoom(0.86)} title="适应画布">
              <Maximize2 size={14} />
              适应
            </button>
            <button onClick={resetChainLayout} title="自动恢复时间顺序布局">
              <LayoutGrid size={14} />
              自动布局
            </button>
            <button onClick={resetChainLayout} title="恢复默认布局">
              <RotateCcw size={14} />
            </button>
            <button
              className="session-board-shell__toggle"
              onClick={() => setTimelineOpen((value) => !value)}
            >
              <Rows3 size={14} />
              {timelineOpen ? "收起路径" : "展开路径"}
            </button>
          </div>
        </header>
        {timelineOpen ? (
          <div
            className="session-board"
            ref={boardRef}
            tabIndex={0}
            aria-label="可拖动的会话轮次白板"
            aria-description="左右拖动一次切换相邻轮次；底部滚动条用于长距离浏览"
            onWheel={(event) => {
              if (!boardRef.current) return;
              if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
                boardRef.current.scrollLeft += event.deltaY;
              }
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowLeft")
                selectTurn(Math.max(0, turnIndex - 1));
              if (event.key === "ArrowRight") {
                selectTurn(
                  Math.min((timeline?.turns.length || 1) - 1, turnIndex + 1),
                );
              }
            }}
            onPointerDown={(event) => {
              if (!boardRef.current) return;
              if (
                (event.target as HTMLElement).closest("[data-node-drag-handle]")
              )
                return;
              if (event.button !== 0) return;
              if (boardSettleTimerRef.current !== null) {
                window.clearTimeout(boardSettleTimerRef.current);
                boardSettleTimerRef.current = null;
                delete boardRef.current.dataset.dragging;
              }
              const boardBounds = boardRef.current.getBoundingClientRect();
              if (
                boardBounds.height > SESSION_CHAIN_SCROLLBAR_GUTTER &&
                event.clientY >=
                  boardBounds.bottom - SESSION_CHAIN_SCROLLBAR_GUTTER
              )
                return;
              dragState.current = {
                active: true,
                moved: false,
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                startScroll: boardRef.current.scrollLeft,
              };
              suppressNodeClick.current = false;
            }}
            onPointerMove={(event) => {
              const drag = dragState.current;
              if (!drag.active || !boardRef.current) return;
              if (drag.pointerId !== event.pointerId) return;
              const distance = event.clientX - dragState.current.startX;
              const verticalDistance = event.clientY - drag.startY;
              if (
                !drag.moved &&
                Math.abs(verticalDistance) > SESSION_CHAIN_SWIPE_START &&
                Math.abs(verticalDistance) > Math.abs(distance)
              ) {
                drag.active = false;
                return;
              }
              if (
                !drag.moved &&
                Math.abs(distance) > SESSION_CHAIN_SWIPE_START &&
                Math.abs(distance) >= Math.abs(verticalDistance)
              ) {
                drag.moved = true;
                suppressNodeClick.current = true;
                boardRef.current.dataset.dragging = "true";
                boardRef.current.setPointerCapture(event.pointerId);
              }
              if (!drag.moved) return;
              event.preventDefault();
              boardRef.current.scrollLeft = drag.startScroll - distance;
            }}
            onPointerUp={(event) => {
              const board = boardRef.current;
              const drag = dragState.current;
              if (!drag.active || drag.pointerId !== event.pointerId) return;
              const swipeDistance = event.clientX - drag.startX;
              if (boardRef.current?.hasPointerCapture(event.pointerId)) {
                boardRef.current.releasePointerCapture(event.pointerId);
              }
              drag.active = false;
              let committed = false;
              if (drag.moved) {
                if (Math.abs(swipeDistance) >= SESSION_CHAIN_SWIPE_COMMIT) {
                  committed = true;
                  const direction = swipeDistance < 0 ? 1 : -1;
                  selectTurn(
                    Math.max(
                      0,
                      Math.min(
                        (timeline?.turns.length || 1) - 1,
                        turnIndex + direction,
                      ),
                    ),
                  );
                } else if (board) {
                  board.scrollLeft = drag.startScroll;
                }
              }
              if (board) {
                if (committed) {
                  boardSettleTimerRef.current = window.setTimeout(() => {
                    delete board.dataset.dragging;
                    boardSettleTimerRef.current = null;
                  }, 380);
                } else {
                  delete board.dataset.dragging;
                }
              }
              window.setTimeout(() => {
                suppressNodeClick.current = false;
              }, 0);
            }}
            onPointerCancel={(event) => {
              if (
                !dragState.current.active ||
                dragState.current.pointerId !== event.pointerId
              )
                return;
              const board = boardRef.current;
              if (board?.hasPointerCapture(event.pointerId)) {
                board.releasePointerCapture(event.pointerId);
              }
              if (board) {
                delete board.dataset.dragging;
                if (dragState.current.moved) {
                  board.scrollLeft = dragState.current.startScroll;
                }
              }
              dragState.current.active = false;
              suppressNodeClick.current = false;
            }}
          >
            <div
              className="session-board__track"
              style={{ zoom: boardZoom } as CSSProperties}
            >
              {(timeline?.turns || []).map((item, index) => (
                <button
                  aria-label={`轮次 ${item.ordinal}：${cleanEvidenceText(item.goal, 82) || "未命名目标"}`}
                  className={index === turnIndex ? "is-selected" : ""}
                  data-turn-index={index}
                  key={item.id}
                  style={{
                    translate: `${nodeOffsets[item.id]?.x || 0}px ${nodeOffsets[item.id]?.y || 0}px`,
                  }}
                  onClick={() => {
                    if (!suppressNodeClick.current) selectTurn(index);
                  }}
                  onKeyDown={(event) => {
                    if (!event.altKey) return;
                    const delta =
                      event.key === "ArrowLeft"
                        ? { x: -6, y: 0 }
                        : event.key === "ArrowRight"
                          ? { x: 6, y: 0 }
                          : event.key === "ArrowUp"
                            ? { x: 0, y: -6 }
                            : event.key === "ArrowDown"
                              ? { x: 0, y: 6 }
                              : null;
                    if (!delta) return;
                    event.preventDefault();
                    nudgeChainNode(item.id, delta);
                  }}
                >
                  <span
                    className="session-board__node-handle"
                    data-node-drag-handle
                    aria-hidden="true"
                    title="拖动节点；键盘可在聚焦轮次后使用 Alt + 方向键"
                    onPointerDown={(event) => {
                      if (event.button !== 0) return;
                      event.preventDefault();
                      event.stopPropagation();
                      const origin = nodeOffsetsRef.current[item.id] || {
                        x: 0,
                        y: 0,
                      };
                      nodeDragState.current = {
                        active: true,
                        moved: false,
                        pointerId: event.pointerId,
                        turnId: item.id,
                        startX: event.clientX,
                        startY: event.clientY,
                        originX: origin.x,
                        originY: origin.y,
                      };
                      event.currentTarget.setPointerCapture(event.pointerId);
                    }}
                    onPointerMove={(event) => {
                      const drag = nodeDragState.current;
                      if (!drag?.active || drag.pointerId !== event.pointerId)
                        return;
                      const x = (event.clientX - drag.startX) / boardZoom;
                      const y = (event.clientY - drag.startY) / boardZoom;
                      if (!drag.moved && Math.abs(x) + Math.abs(y) > 4) {
                        drag.moved = true;
                        suppressNodeClick.current = true;
                      }
                      if (!drag.moved) return;
                      setNodeOffsets((current) => {
                        const next = {
                          ...current,
                          [drag.turnId]: {
                            x: Math.max(-26, Math.min(26, drag.originX + x)),
                            y: Math.max(-22, Math.min(22, drag.originY + y)),
                          },
                        };
                        nodeOffsetsRef.current = next;
                        return next;
                      });
                    }}
                    onPointerUp={(event) => {
                      if (
                        event.currentTarget.hasPointerCapture(event.pointerId)
                      ) {
                        event.currentTarget.releasePointerCapture(
                          event.pointerId,
                        );
                      }
                      if (nodeDragState.current?.moved) {
                        persistChainLayout(nodeOffsetsRef.current);
                      }
                      nodeDragState.current = null;
                      window.setTimeout(() => {
                        suppressNodeClick.current = false;
                      }, 0);
                    }}
                    onPointerCancel={(event) => {
                      if (
                        event.currentTarget.hasPointerCapture(event.pointerId)
                      ) {
                        event.currentTarget.releasePointerCapture(
                          event.pointerId,
                        );
                      }
                      nodeDragState.current = null;
                      suppressNodeClick.current = false;
                    }}
                  >
                    <GripVertical size={13} />
                  </span>
                  <span className="session-board__ordinal">{item.ordinal}</span>
                  <span className="session-board__content">
                    <strong>
                      {cleanEvidenceText(item.goal, 82) ||
                        `研究轮次 ${item.ordinal}`}
                    </strong>
                    <small>
                      {item.files.length} 文件 · {item.command_count} 执行 ·{" "}
                      {item.validation_count} 验证
                    </small>
                  </span>
                  <Status value={item.status}>
                    {statusLabel(item.status)}
                  </Status>
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {timelineOpen && timeline?.turns.length ? (
          <nav
            className="session-board-overview"
            aria-label="全部会话轮次快速定位"
          >
            <span>本页 {timeline.turns.length} 轮</span>
            <div>
              {timeline.turns.map((item, index) => (
                <button
                  className={index === turnIndex ? "is-selected" : ""}
                  key={item.id}
                  onClick={() => selectTurn(index)}
                  aria-label={`定位到轮次 ${item.ordinal}`}
                  title={cleanEvidenceText(item.goal, 90)}
                >
                  {String(item.ordinal).padStart(2, "0")}
                </button>
              ))}
            </div>
          </nav>
        ) : null}
        {timeline && turnPageCount > 1 ? (
          <nav
            className="collection-pager session-turn-pager"
            aria-label="会话轮次分页"
          >
            <button
              type="button"
              disabled={turnPage === 0 || timelineQuery.isFetching}
              onClick={() => selectTurnPage(turnPage - 1)}
            >
              <ChevronLeft size={15} />
              较新轮次
            </button>
            <span>
              第 {turnPage + 1} / {turnPageCount} 页 · 当前加载{" "}
              {timeline.turns.length}/ {timeline.turn_count} 轮
            </span>
            <button
              type="button"
              disabled={
                turnPage + 1 >= turnPageCount || timelineQuery.isFetching
              }
              onClick={() => selectTurnPage(turnPage + 1)}
            >
              较早轮次
              <ChevronRight size={15} />
            </button>
          </nav>
        ) : null}
      </section>

      <div
        className={`session-detail__layout${relatedOpen ? " has-related" : ""}`}
      >
        <main>
          {turn ? (
            <article className="session-story">
              <header className="session-turn-brief">
                <span className="session-turn-brief__index">
                  <small>TURN</small>
                  {String(turn.ordinal).padStart(2, "0")}
                </span>
                <div>
                  <p>当前轮次</p>
                  <h2>
                    {cleanEvidenceText(turn.goal, 180) ||
                      `研究轮次 ${turnIndex + 1}`}
                  </h2>
                </div>
                <div className="session-turn-brief__facts">
                  <span>
                    <FileDiff size={15} />
                    <strong>{turn.files.length}</strong> 变更文件
                  </span>
                  <span>
                    <Terminal size={15} />
                    <strong>{turn.command_count}</strong> 次执行
                  </span>
                  <span>
                    <ListChecks size={15} />
                    <strong>{turn.validation_count}</strong> 项验证
                  </span>
                </div>
                <Status value={turn.status}>{statusLabel(turn.status)}</Status>
                <button
                  className="session-turn-brief__graph"
                  onClick={() =>
                    navigate(
                      `/p/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(threadId)}/graph?turn=${encodeURIComponent(turn.id)}`,
                    )
                  }
                >
                  <Network size={15} />
                  查看本轮代码影响
                </button>
              </header>

              <nav
                className="session-detail-tabs"
                role="tablist"
                aria-label="轮次详情"
              >
                <button
                  id="session-tab-overview"
                  role="tab"
                  aria-controls="session-turn-panel"
                  aria-selected={activeTab === "overview"}
                  className={activeTab === "overview" ? "is-active" : ""}
                  onClick={() => setDetailTab("overview")}
                >
                  <Sparkles size={15} />
                  概览
                </button>
                <button
                  id="session-tab-files"
                  role="tab"
                  aria-controls="session-turn-panel"
                  aria-selected={activeTab === "files"}
                  className={activeTab === "files" ? "is-active" : ""}
                  onClick={() => setDetailTab("files")}
                >
                  <FileDiff size={15} />
                  代码变更
                  <em>{turn.files.length}</em>
                </button>
                <button
                  id="session-tab-operations"
                  role="tab"
                  aria-controls="session-turn-panel"
                  aria-selected={activeTab === "operations"}
                  className={activeTab === "operations" ? "is-active" : ""}
                  onClick={() => setDetailTab("operations")}
                >
                  <Terminal size={15} />
                  执行记录
                  <em>
                    {auditedTurn?.operations?.length || turn.command_count}
                  </em>
                </button>
                <button
                  className={`session-detail-tabs__source${relatedOpen ? " is-active" : ""}`}
                  onClick={() => setRelatedOpen((value) => !value)}
                  aria-expanded={relatedOpen}
                >
                  {relatedOpen ? (
                    <PanelRightClose size={15} />
                  ) : (
                    <PanelRightOpen size={15} />
                  )}
                  关联来源
                  <em>{related.length}</em>
                </button>
              </nav>

              <div
                className="session-detail-tabpanel"
                id="session-turn-panel"
                role="tabpanel"
                aria-labelledby={`session-tab-${activeTab}`}
              >
                {activeTab === "overview" ? (
                  <SessionOverviewAudit turn={auditedTurn || turn} />
                ) : null}

                {activeTab === "files" ? (
                  <section
                    className="session-ledger-panel"
                    aria-label="代码变更互查"
                  >
                    <FileChangeLedger
                      key={turn.id}
                      turn={auditedTurn || turn}
                      projectId={projectId}
                      related={related}
                    />
                  </section>
                ) : null}

                {activeTab === "operations" ? (
                  <section
                    className="session-ledger-panel"
                    aria-label="执行记录审计"
                  >
                    <OperationLedger
                      key={turn.id}
                      operations={auditedTurn?.operations || []}
                      commandCount={turn.command_count}
                      validationCount={turn.validation_count}
                      loading={turnAuditQuery.isLoading}
                    />
                  </section>
                ) : null}
              </div>

              <footer className="session-audit-strip">
                <span>
                  <Hash size={14} />
                  轮次定位
                  <code>{cleanEvidenceText(turn.locator || turn.id, 110)}</code>
                </span>
                <span>
                  <Clock3 size={14} />
                  {formatTime(turn.started_at)} →{" "}
                  {formatTime(turn.completed_at)}
                </span>
              </footer>
            </article>
          ) : (
            <EmptyState
              icon={<MessageSquareText size={23} />}
              title="会话还没有有效轮次"
              description="同步完成后会按目标、总结、变更和执行记录组织。"
            />
          )}
        </main>
        {relatedOpen ? (
          <RelatedSessionEvidence
            results={related}
            recordedChangeCount={timeline?.file_change_count}
            projectId={projectId}
            onClose={() => setRelatedOpen(false)}
          />
        ) : null}
      </div>
    </div>
  );
}
