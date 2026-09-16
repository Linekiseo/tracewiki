import {
  Activity,
  ArrowUpRight,
  Braces,
  Check,
  CheckCircle2,
  ChevronRight,
  Circle,
  Copy,
  FileCode2,
  FlaskConical,
  Info,
  Layers3,
  Link2,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  SquareTerminal,
  X,
  XCircle,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { Button, EmptyState, Status } from "../../components/ui";
import { api } from "../../lib/api";
import type {
  CodexApproval,
  CodexBridgeStatus,
  CodexExecution,
  CodexSyncProjection,
  WorkItem,
} from "../../lib/types";

const executionLabels: Record<string, string> = {
  awaiting_approval: "待审批",
  queued: "排队中",
  running: "进行中",
  review: "待复核",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const tabs = [
  { key: "all", label: "全部" },
  { key: "awaiting_approval", label: "待审批" },
  { key: "running", label: "执行中" },
  { key: "review", label: "待复核" },
  { key: "completed", label: "已完成" },
] as const;

const availabilityLabels: Record<string, string> = {
  ready: "已连接",
  disconnected: "未连接",
  unauthorized: "未授权",
  unavailable: "不可用",
};

const availabilityReasons: Record<string, string> = {
  connected: "已观察到项目凭据握手",
  handshake_not_observed: "已配置凭据，但尚未观察到客户端握手",
  capability_snapshot_stale: "能力快照已过期，请重新握手",
  project_token_missing: "当前项目尚未配置访问凭据",
  project_token_inactive: "当前项目凭据已失效或撤销",
  plugin_missing: "未检测到 Research Project Bridge",
};

const syncStatusLabels: Record<
  string,
  { label: string; description: string; tone: string }
> = {
  syncing: {
    label: "正在同步",
    description: "会话源正在建立新的只读索引。",
    tone: "running",
  },
  sync_failed: {
    label: "同步失败",
    description: "最近一次同步失败，旧数据不会被标记为最新。",
    tone: "failed",
  },
  no_data: {
    label: "暂无数据",
    description: "当前项目尚无可审阅的会话索引。",
    tone: "pending",
  },
  ready: {
    label: "数据可用",
    description: "会话索引已发布，可用于只读审阅。",
    tone: "completed",
  },
  unavailable: {
    label: "状态不可用",
    description: "无法读取同步状态，页面不会假定数据已就绪。",
    tone: "failed",
  },
};

const permissionLabels: Record<string, string> = {
  automatic: "自动允许",
  "confirmation-required": "需要确认",
  unavailable: "不可用",
  "workspace-write": "工作区写入",
  "read-only": "只读",
};

function permissionLabel(value?: string) {
  if (!value) return "未报告";
  return permissionLabels[value] || value;
}

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

function syncPresentation(sync?: CodexSyncProjection) {
  if (sync?.status === "ready" && !sync.data_available) {
    return syncStatusLabels.no_data;
  }
  return (
    syncStatusLabels[sync?.status || "unavailable"] ||
    syncStatusLabels.unavailable
  );
}

function BridgeSyncSummary({
  sync,
  loading,
  error,
}: {
  sync?: CodexSyncProjection;
  loading: boolean;
  error: boolean;
}) {
  const presentation = error
    ? syncStatusLabels.unavailable
    : loading
      ? syncStatusLabels.syncing
      : syncPresentation(sync);
  return (
    <section className="bridge-sync-summary" aria-label="会话同步状态">
      <header>
        <div>
          <span>SESSION DATA</span>
          <h2>会话数据同步</h2>
        </div>
        <Status value={presentation.tone}>{presentation.label}</Status>
      </header>
      <p>{presentation.description}</p>
      <dl>
        <div>
          <dt>已索引会话</dt>
          <dd>{error ? 0 : (sync?.indexed_sessions ?? 0)}</dd>
        </div>
        <div>
          <dt>已发现来源</dt>
          <dd>{error ? 0 : (sync?.source_count ?? 0)}</dd>
        </div>
        <div>
          <dt>活跃工作流</dt>
          <dd>{!error && sync?.active_workflow ? "有" : "无"}</dd>
        </div>
        <div>
          <dt>最近同步</dt>
          <dd>{error ? "不可用" : formatTime(sync?.last_sync_at)}</dd>
        </div>
      </dl>
    </section>
  );
}

function relativeTime(value?: string | null) {
  if (!value) return "尚未连接";
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) return formatTime(value);
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return formatTime(value);
}

function taskGoal(execution: CodexExecution) {
  return (
    execution.context_snapshot?.work_item?.objective ||
    execution.summary ||
    execution.work_item_title
  );
}

function approvalLabel(approval: CodexApproval) {
  if (approval.action === "execution.start") return "启动后台执行";
  if (approval.action === "work_item.complete") return "确认任务完成";
  return approval.action;
}

function ConnectionNode({
  icon,
  title,
  status,
  tone = "ready",
}: {
  icon: ReactNode;
  title: string;
  status: string;
  tone?: "ready" | "warning" | "error";
}) {
  return (
    <div className={`bridge-connection-node bridge-connection-node--${tone}`}>
      <span className="bridge-connection-node__icon">{icon}</span>
      <span>
        <strong>{title}</strong>
        <small>
          <i />
          {status}
        </small>
      </span>
    </div>
  );
}

function DrawerShell({
  title,
  description,
  onClose,
  children,
  footer,
  ariaLabel,
}: {
  title: string;
  description: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  ariaLabel: string;
}) {
  return (
    <div
      className="bridge-drawer-layer"
      role="presentation"
      onMouseDown={onClose}
    >
      <aside
        className="bridge-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="bridge-drawer__header">
          <div>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
          <button onClick={onClose} aria-label="关闭">
            <X size={19} />
          </button>
        </header>
        <div className="bridge-drawer__body">{children}</div>
        {footer ? (
          <footer className="bridge-drawer__footer">{footer}</footer>
        ) : null}
      </aside>
    </div>
  );
}

function ConnectionSettings({
  status,
  statusLoading,
  statusError,
  onClose,
}: {
  status?: CodexBridgeStatus;
  statusLoading: boolean;
  statusError: boolean;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState("");
  const copy = async (value: string, key: string) => {
    await navigator.clipboard?.writeText(value);
    setCopied(key);
    window.setTimeout(() => setCopied(""), 1600);
  };
  const endpoint = status?.mcp.endpoint || "端点不可用";
  const trustedSnapshots =
    !statusError &&
    status?.availability === "ready" &&
    status.reason !== "capability_snapshot_stale"
      ? status.capability_snapshots || []
      : [];
  return (
    <DrawerShell
      title="Codex 连接设置"
      description="检查 MCP、插件与本地执行环境"
      onClose={onClose}
      ariaLabel="Codex 连接设置"
    >
      <section className="bridge-settings-section">
        <h3>连接诊断</h3>
        <div className="bridge-settings-checks">
          <article className={statusError ? "is-warning" : ""}>
            {statusLoading ? (
              <LoaderCircle className="is-spinning" size={17} />
            ) : statusError ? (
              <Info size={17} />
            ) : (
              <CheckCircle2 size={17} />
            )}
            <span>
              <strong>RAG Core API</strong>
              <small>
                {statusLoading
                  ? "正在检查项目上下文"
                  : statusError
                    ? "项目上下文不可读取"
                    : "项目上下文可读取"}
              </small>
            </span>
            <em>
              {statusLoading ? "检查中" : statusError ? "不可用" : "正常"}
            </em>
          </article>
          <article
            className={status?.mcp.status === "ready" ? "" : "is-warning"}
          >
            {status?.mcp.status === "ready" ? (
              <CheckCircle2 size={17} />
            ) : (
              <Info size={17} />
            )}
            <span>
              <strong>MCP 身份验证</strong>
              <small>{status?.mcp.transport || "streamable-http"}</small>
            </span>
            <em>
              {status?.mcp.status === "ready" ? "凭据已配置" : "需要令牌"}
            </em>
          </article>
          <article className={status?.plugin.installed ? "" : "is-warning"}>
            {status?.plugin.installed ? (
              <CheckCircle2 size={17} />
            ) : (
              <Info size={17} />
            )}
            <span>
              <strong>Research Project Bridge</strong>
              <small>{status?.plugin.location || "未检测到插件"}</small>
            </span>
            <em>{status?.plugin.installed ? "已安装" : "未安装"}</em>
          </article>
          <article className={status?.codex_cli.available ? "" : "is-warning"}>
            {status?.codex_cli.available ? (
              <CheckCircle2 size={17} />
            ) : (
              <Info size={17} />
            )}
            <span>
              <strong>Codex CLI</strong>
              <small>
                {status?.codex_cli.location || "后台队列执行暂不可用"}
              </small>
            </span>
            <em>{status?.codex_cli.available ? "可用" : "未检测到"}</em>
          </article>
        </div>
      </section>

      <section className="bridge-settings-section">
        <h3>MCP 端点</h3>
        <div className="bridge-copy-field">
          <code>{endpoint}</code>
          <button
            onClick={() => copy(endpoint, "endpoint")}
            aria-label="复制 MCP 端点"
          >
            {copied === "endpoint" ? <Check size={15} /> : <Copy size={15} />}
          </button>
        </div>
        <p>Codex 通过此端点读取当前项目、研究任务、迭代和证据上下文。</p>
      </section>

      <section className="bridge-settings-section">
        <h3>服务端能力快照</h3>
        {statusLoading ? (
          <p>正在读取服务端发现结果…</p>
        ) : trustedSnapshots.length ? (
          <div className="bridge-capability-snapshots">
            {trustedSnapshots.map((snapshot) => (
              <article key={`${snapshot.client_id}-${snapshot.observed_at}`}>
                <header>
                  <strong>已验证握手</strong>
                  <time>{formatTime(snapshot.observed_at)}</time>
                </header>
                <small>
                  MCP {snapshot.protocol_version} · Server{" "}
                  {snapshot.server_version}
                </small>
                {snapshot.capabilities.length ? (
                  <ul>
                    {snapshot.capabilities.map((capability) => (
                      <li key={capability}>{capability}</li>
                    ))}
                  </ul>
                ) : (
                  <p>该快照未声明可用能力。</p>
                )}
              </article>
            ))}
          </div>
        ) : (
          <p>
            {status?.reason === "capability_snapshot_stale"
              ? "能力快照已过期，请重新握手；当前不展示可用能力。"
              : "尚无已验证握手快照，当前不展示可用能力。"}
          </p>
        )}
      </section>

      <section className="bridge-settings-section">
        <h3>权限边界</h3>
        <dl className="bridge-permission-list">
          <div>
            <dt>读取</dt>
            <dd>{permissionLabel(status?.permissions.read)}</dd>
          </div>
          <div>
            <dt>写入</dt>
            <dd>{permissionLabel(status?.permissions.write)}</dd>
          </div>
          <div>
            <dt>删除</dt>
            <dd>{permissionLabel(status?.permissions.delete)}</dd>
          </div>
          <div>
            <dt>沙箱</dt>
            <dd>{permissionLabel(status?.permissions.sandbox)}</dd>
          </div>
        </dl>
      </section>

      <div className="bridge-safety-note">
        <ShieldCheck size={17} />
        <p>
          Bridge 只向 Codex
          暴露结构化项目工具。任务完成、写入确认与审批记录都会保留在当前项目中。
        </p>
      </div>
    </DrawerShell>
  );
}

function CreateExecutionDrawer({
  projectId,
  projectPath,
  workItems,
  initialTaskId,
  initialTemplate,
  onClose,
  onCreated,
}: {
  projectId: string;
  projectPath: string;
  workItems: WorkItem[];
  initialTaskId?: string;
  initialTemplate?: { title: string; objective: string; acceptance: string };
  onClose: () => void;
  onCreated: (execution: CodexExecution) => void;
}) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState(1);
  const [workItemId, setWorkItemId] = useState(
    initialTaskId || workItems[0]?.id || "",
  );
  const [createNew, setCreateNew] = useState(
    !workItems.length || Boolean(initialTemplate),
  );
  const [newTitle, setNewTitle] = useState(initialTemplate?.title || "");
  const [newObjective, setNewObjective] = useState(
    initialTemplate?.objective || "",
  );
  const [newAcceptance, setNewAcceptance] = useState(
    initialTemplate?.acceptance || "",
  );
  const [mode, setMode] = useState<"interactive" | "queue">("interactive");
  const [sandbox, setSandbox] = useState<"read-only" | "workspace-write">(
    "read-only",
  );
  const [workspacePath, setWorkspacePath] = useState(projectPath);
  const [baseRef, setBaseRef] = useState("HEAD");
  const [prompt, setPrompt] = useState("");
  const selected = workItems.find((item) => item.id === workItemId);

  useEffect(() => {
    if (initialTaskId) setWorkItemId(initialTaskId);
  }, [initialTaskId]);

  const mutation = useMutation({
    mutationFn: async () => {
      let targetWorkItemId = workItemId;
      if (createNew) {
        const createdWorkItem = await api.workItems.create(projectId, {
          title: newTitle.trim(),
          objective: newObjective.trim(),
          acceptance_criteria: newAcceptance
            .split("\n")
            .map((item) => item.trim())
            .filter(Boolean),
          workspace_path: workspacePath.trim() || undefined,
          base_ref: baseRef.trim() || undefined,
        });
        targetWorkItemId = createdWorkItem.id;
      }
      return api.codexBridge.createExecution({
        project_id: projectId,
        work_item_id: targetWorkItemId,
        mode,
        workspace_path: workspacePath.trim() || undefined,
        base_ref: baseRef.trim() || undefined,
        prompt: prompt.trim() || undefined,
        sandbox,
      });
    },
    onSuccess: (execution) => {
      queryClient.invalidateQueries({
        queryKey: ["codex-bridge-executions", projectId],
      });
      queryClient.invalidateQueries({
        queryKey: ["codex-bridge-approvals", projectId],
      });
      queryClient.invalidateQueries({ queryKey: ["work-items", projectId] });
      onCreated(execution);
      if (mode === "interactive" && execution.launch?.url) {
        window.location.href = execution.launch.url;
      }
    },
  });

  const canContinue =
    step === 1
      ? createNew
        ? Boolean(newTitle.trim())
        : Boolean(workItemId)
      : step === 2
        ? true
        : true;

  return (
    <DrawerShell
      title="新建 Codex 执行"
      description="从研究任务生成结构化上下文并安全交给 Codex"
      onClose={onClose}
      ariaLabel="新建 Codex 执行"
      footer={
        <>
          <Button
            variant="secondary"
            onClick={step === 1 ? onClose : () => setStep(step - 1)}
          >
            {step === 1 ? "取消" : "上一步"}
          </Button>
          {step < 3 ? (
            <Button
              variant="primary"
              disabled={!canContinue}
              onClick={() => setStep(step + 1)}
            >
              下一步
              <ChevronRight size={16} />
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? (
                <LoaderCircle className="is-spinning" size={16} />
              ) : (
                <Play size={16} />
              )}
              {mode === "interactive" ? "创建并打开" : "提交审批"}
            </Button>
          )}
        </>
      }
    >
      <nav className="bridge-steps" aria-label="创建执行步骤">
        {["选择任务", "配置执行", "确认"].map((label, index) => {
          const ordinal = index + 1;
          return (
            <span
              key={label}
              className={
                step === ordinal
                  ? "is-active"
                  : step > ordinal
                    ? "is-complete"
                    : ""
              }
            >
              <i>{step > ordinal ? <Check size={12} /> : ordinal}</i>
              {label}
            </span>
          );
        })}
      </nav>

      {step === 1 ? (
        <section className="bridge-form-section">
          <span className="bridge-form-label">研究任务</span>
          {workItems.length ? (
            <>
              <div className="bridge-task-source">
                <button
                  className={!createNew ? "is-selected" : ""}
                  onClick={() => setCreateNew(false)}
                  type="button"
                >
                  选择已有任务
                </button>
                <button
                  className={createNew ? "is-selected" : ""}
                  onClick={() => setCreateNew(true)}
                  type="button"
                >
                  创建新任务
                </button>
              </div>
              {!createNew ? (
                <select
                  id="bridge-task"
                  aria-label="研究任务"
                  value={workItemId}
                  onChange={(event) => setWorkItemId(event.target.value)}
                >
                  {workItems.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.display_key} · {item.title}
                    </option>
                  ))}
                </select>
              ) : null}
              {!createNew && selected ? (
                <div className="bridge-task-summary">
                  <p>
                    <strong>目标：</strong>
                    {selected.objective || selected.title}
                  </p>
                  <p>
                    <strong>验收：</strong>
                    {selected.acceptance_criteria?.length
                      ? selected.acceptance_criteria.join("；")
                      : "完成实现并通过项目现有验证"}
                  </p>
                </div>
              ) : null}
            </>
          ) : null}
          {createNew ? (
            <div className="bridge-new-task">
              {!workItems.length ? (
                <div className="bridge-new-task__intro">
                  <Layers3 size={17} />
                  <span>
                    <strong>从第一个研究任务开始</strong>
                    <small>创建后会自动绑定当前项目与 Codex 执行。</small>
                  </span>
                </div>
              ) : null}
              <label htmlFor="bridge-new-title">任务名称</label>
              <input
                id="bridge-new-title"
                value={newTitle}
                onChange={(event) => setNewTitle(event.target.value)}
                placeholder="例如：补齐混合检索回归测试"
                autoFocus
              />
              <label htmlFor="bridge-new-objective">执行目标</label>
              <textarea
                id="bridge-new-objective"
                rows={3}
                value={newObjective}
                onChange={(event) => setNewObjective(event.target.value)}
                placeholder="说明期望 Codex 完成什么，以及为什么"
              />
              <label htmlFor="bridge-new-acceptance">验收条件</label>
              <textarea
                id="bridge-new-acceptance"
                rows={3}
                value={newAcceptance}
                onChange={(event) => setNewAcceptance(event.target.value)}
                placeholder={
                  "每行一条，例如：\n相关测试通过\n不改变现有 API 契约"
                }
              />
            </div>
          ) : null}
        </section>
      ) : null}

      {step === 2 ? (
        <>
          <section className="bridge-form-section">
            <span className="bridge-form-label">执行方式</span>
            <div className="bridge-choice-grid">
              <button
                className={mode === "interactive" ? "is-selected" : ""}
                onClick={() => setMode("interactive")}
                type="button"
              >
                <i>{mode === "interactive" ? <Check size={12} /> : null}</i>
                <span>
                  <strong>交互模式</strong>
                  <small>在 Codex 中打开并实时协作</small>
                </span>
              </button>
              <button
                className={mode === "queue" ? "is-selected" : ""}
                onClick={() => setMode("queue")}
                type="button"
              >
                <i>{mode === "queue" ? <Check size={12} /> : null}</i>
                <span>
                  <strong>队列模式</strong>
                  <small>审批后由后台执行</small>
                </span>
              </button>
            </div>
          </section>

          <section className="bridge-form-section">
            <label htmlFor="bridge-workspace">工作区</label>
            <input
              id="bridge-workspace"
              value={workspacePath}
              readOnly
              placeholder="由项目设置决定"
            />
            <label className="bridge-form-sublabel" htmlFor="bridge-ref">
              基准分支或引用
            </label>
            <input
              id="bridge-ref"
              value={baseRef}
              onChange={(event) => setBaseRef(event.target.value)}
              placeholder="HEAD"
            />
          </section>

          <section className="bridge-form-section">
            <span className="bridge-form-label">权限范围</span>
            <div className="bridge-choice-grid">
              <button
                className={sandbox === "workspace-write" ? "is-selected" : ""}
                onClick={() => setSandbox("workspace-write")}
                type="button"
              >
                <i>
                  {sandbox === "workspace-write" ? <Check size={12} /> : null}
                </i>
                <span>
                  <strong>工作区写入</strong>
                  <small>允许在受治理项目根目录内修改文件</small>
                </span>
              </button>
              <button
                className={sandbox === "read-only" ? "is-selected" : ""}
                onClick={() => setSandbox("read-only")}
                type="button"
              >
                <i>{sandbox === "read-only" ? <Check size={12} /> : null}</i>
                <span>
                  <strong>只读分析</strong>
                  <small>不允许修改项目文件</small>
                </span>
              </button>
            </div>
          </section>

          <section className="bridge-form-section">
            <label htmlFor="bridge-prompt">自定义提示（可选）</label>
            <textarea
              id="bridge-prompt"
              rows={4}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="补充本次执行特有的约束；留空则根据研究任务自动生成。"
            />
          </section>
        </>
      ) : null}

      {step === 3 ? (
        <section className="bridge-confirm">
          <h3>确认执行配置</h3>
          <dl>
            <div>
              <dt>研究任务</dt>
              <dd>
                {createNew
                  ? `新建 · ${newTitle}`
                  : `${selected?.display_key} · ${selected?.title}`}
              </dd>
            </div>
            <div>
              <dt>执行方式</dt>
              <dd>{mode === "interactive" ? "交互模式" : "队列模式"}</dd>
            </div>
            <div>
              <dt>工作区</dt>
              <dd>
                <code>{workspacePath}</code>
              </dd>
            </div>
            <div>
              <dt>基准引用</dt>
              <dd>
                <code>{baseRef || "HEAD"}</code>
              </dd>
            </div>
            <div>
              <dt>权限</dt>
              <dd>
                {sandbox === "workspace-write" ? "工作区写入" : "只读分析"}
              </dd>
            </div>
          </dl>
          <div className="bridge-safety-note">
            <ShieldCheck size={17} />
            <p>
              系统会附带项目、研究任务与验收条件；写入和任务完成仍需人工确认。
            </p>
          </div>
          {mutation.isError ? (
            <p className="form-error">{(mutation.error as Error).message}</p>
          ) : null}
        </section>
      ) : null}
    </DrawerShell>
  );
}

function executionProgress(execution: CodexExecution) {
  const events = execution.events || [];
  const eventSteps = events.slice(-4).map((event, index) => ({
    label: event.summary,
    time: formatTime(event.created_at),
    state:
      index === Math.min(events.length, 4) - 1 && execution.status === "running"
        ? "active"
        : "complete",
  }));
  if (eventSteps.length > 1) return eventSteps;

  const active = execution.status;
  return [
    {
      label: "执行已创建",
      time: formatTime(execution.created_at),
      state: "complete",
    },
    {
      label:
        active === "awaiting_approval" ? "等待启动审批" : "已读取项目上下文",
      time:
        active === "awaiting_approval"
          ? "—"
          : formatTime(execution.started_at || execution.updated_at),
      state: active === "awaiting_approval" ? "active" : "complete",
    },
    {
      label: active === "running" ? "正在执行任务" : "代码与结果已回传",
      time: ["awaiting_approval", "queued"].includes(active)
        ? "—"
        : formatTime(execution.updated_at),
      state:
        active === "running" || active === "queued"
          ? "active"
          : active === "awaiting_approval"
            ? "waiting"
            : "complete",
    },
    {
      label: active === "completed" ? "人工复核已完成" : "等待人工复核",
      time: active === "completed" ? formatTime(execution.completed_at) : "—",
      state:
        active === "review"
          ? "active"
          : active === "completed"
            ? "complete"
            : "waiting",
    },
  ];
}

function ExecutionInspector({
  projectId,
  execution,
  approvals,
  codexReady,
}: {
  projectId: string;
  execution?: CodexExecution;
  approvals: CodexApproval[];
  codexReady: boolean;
}) {
  const queryClient = useQueryClient();
  const [approvalError, setApprovalError] = useState("");
  const decide = useMutation({
    mutationFn: ({
      approvalId,
      decision,
    }: {
      approvalId: string;
      decision: "approved" | "rejected";
    }) => api.codexBridge.decideApproval(approvalId, decision),
    onSuccess: () => {
      setApprovalError("");
      queryClient.invalidateQueries({
        queryKey: ["codex-bridge-approvals", projectId],
      });
      queryClient.invalidateQueries({
        queryKey: ["codex-bridge-executions", projectId],
      });
      if (execution) {
        queryClient.invalidateQueries({
          queryKey: ["codex-bridge-execution", execution.id],
        });
      }
    },
    onError: (error) => setApprovalError((error as Error).message),
  });

  if (!execution) {
    return (
      <aside className="bridge-inspector">
        <EmptyState
          icon={<Activity size={22} />}
          title="选择一条执行"
          description="查看目标、过程、权限、变更与待审批动作。"
        />
      </aside>
    );
  }

  const selectedApprovals = approvals
    .filter((item) => item.execution_id === execution.id)
    .slice(0, 3);
  const visibleApprovals = selectedApprovals;
  const workspacePath = execution.workspace_path.trim();
  const codexCanLaunch = codexReady && Boolean(workspacePath);
  const openUrl = codexCanLaunch
    ? `codex://new?path=${encodeURIComponent(workspacePath)}&prompt=${encodeURIComponent(execution.prompt)}`
    : undefined;

  return (
    <aside className="bridge-inspector" aria-label="执行详情">
      <header className="bridge-inspector__header">
        <h2>执行详情</h2>
        <Status value={execution.status}>
          {executionLabels[execution.status]}
        </Status>
      </header>
      <div className="bridge-inspector__scroll">
        <section className="bridge-inspector__goal">
          <span>任务目标</span>
          <h3>{execution.work_item_title}</h3>
          <p>{taskGoal(execution)}</p>
        </section>

        <section className="bridge-inspector__section">
          <h3>执行进度</h3>
          <div className="bridge-progress">
            {executionProgress(execution).map((item, index) => (
              <article
                className={`is-${item.state}`}
                key={`${item.label}-${index}`}
              >
                <span>
                  {item.state === "complete" ? (
                    <Check size={12} />
                  ) : item.state === "active" ? (
                    <i />
                  ) : (
                    <Circle size={10} />
                  )}
                </span>
                <strong>{item.label}</strong>
                <time>{item.time}</time>
              </article>
            ))}
          </div>
        </section>

        <section className="bridge-inspector__section">
          <h3>执行环境</h3>
          <dl className="bridge-execution-facts">
            <div>
              <dt>工作区路径</dt>
              <dd>
                <code>{execution.workspace_path}</code>
              </dd>
            </div>
            <div>
              <dt>基准引用</dt>
              <dd>
                <code>{execution.base_ref || "HEAD"}</code>
              </dd>
            </div>
            <div>
              <dt>权限范围</dt>
              <dd>
                {execution.sandbox === "workspace-write"
                  ? "工作区写入 · 删除不可用"
                  : "只读分析"}
              </dd>
            </div>
            <div>
              <dt>执行方式</dt>
              <dd>
                {execution.mode === "interactive"
                  ? "交互模式（实时会话）"
                  : "队列模式（后台执行）"}
              </dd>
            </div>
            {execution.thread_id ? (
              <div>
                <dt>关联会话</dt>
                <dd>
                  <Link
                    to={`/p/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(execution.thread_id)}`}
                  >
                    查看会话
                  </Link>
                </dd>
              </div>
            ) : null}
          </dl>
          <div className="bridge-inspector__actions">
            {openUrl ? (
              <a className="button button--primary" href={openUrl}>
                在 Codex 中打开
                <ArrowUpRight size={15} />
              </a>
            ) : (
              <span
                className="button button--primary"
                aria-disabled="true"
                title="Codex 尚未连接，或执行工作区不可用"
              >
                在 Codex 中打开
                <ArrowUpRight size={15} />
              </span>
            )}
            <Link
              className="button button--secondary"
              to={`/p/${encodeURIComponent(projectId)}/overview`}
            >
              查看研究任务
            </Link>
          </div>
        </section>

        <section className="bridge-inspector__section bridge-approvals">
          <header>
            <h3>待处理审批</h3>
            <span>{selectedApprovals.length}</span>
          </header>
          {visibleApprovals.length ? (
            visibleApprovals.map((approval) => (
              <article key={approval.id}>
                <span className="bridge-approval-icon">
                  {approval.action === "execution.start" ? (
                    <Play size={15} />
                  ) : (
                    <FileCode2 size={15} />
                  )}
                </span>
                <span>
                  <strong>{approvalLabel(approval)}</strong>
                  <small>
                    {formatTime(approval.created_at)} · {approval.requested_by}
                  </small>
                </span>
                <span className="bridge-approval-actions">
                  <button
                    disabled={decide.isPending}
                    onClick={() =>
                      decide.mutate({
                        approvalId: approval.id,
                        decision: "approved",
                      })
                    }
                  >
                    批准
                  </button>
                  <button
                    disabled={decide.isPending}
                    onClick={() =>
                      decide.mutate({
                        approvalId: approval.id,
                        decision: "rejected",
                      })
                    }
                  >
                    驳回
                  </button>
                </span>
              </article>
            ))
          ) : (
            <p className="bridge-approvals__empty">
              当前没有需要人工处理的审批。
            </p>
          )}
          {approvalError ? <p className="form-error">{approvalError}</p> : null}
        </section>
        {execution.error ? (
          <section className="bridge-inspector__section bridge-execution-error">
            <h3>执行错误</h3>
            <p>{execution.error}</p>
          </section>
        ) : null}
        <section className="bridge-inspector__section">
          <h3>变更与验证</h3>
          {execution.changed_files.length ? (
            <ul className="bridge-detail-list">
              {execution.changed_files.map((file) => (
                <li key={file}>
                  <code>{file}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="bridge-approvals__empty">未报告文件变更。</p>
          )}
          {execution.validation.length ? (
            <ul className="bridge-detail-list">
              {execution.validation.map((item, index) => (
                <li key={index}>
                  {String(
                    item.summary || item.name || item.status || "验证记录",
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="bridge-approvals__empty">未报告验证记录。</p>
          )}
        </section>
      </div>
      <footer className="bridge-inspector__safety">
        <ShieldCheck size={15} />
        写入与任务完成始终需要人工确认
      </footer>
    </aside>
  );
}

export function CodexBridgeWorkspace() {
  const { projectId = "" } = useParams();
  const [activeTab, setActiveTab] = useState("all");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [initialTaskId, setInitialTaskId] = useState<string>();
  const [initialTemplate, setInitialTemplate] = useState<
    { title: string; objective: string; acceptance: string } | undefined
  >();

  const statusQuery = useQuery({
    queryKey: ["codex-bridge-status", projectId],
    queryFn: () => api.codexBridge.status(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 20_000,
  });
  const executionsQuery = useQuery({
    queryKey: ["codex-bridge-executions", projectId],
    queryFn: () => api.codexBridge.executions(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 12_000,
  });
  const approvalsQuery = useQuery({
    queryKey: ["codex-bridge-approvals", projectId],
    queryFn: () => api.codexBridge.approvals(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 12_000,
  });
  const workItemsQuery = useQuery({
    queryKey: ["work-items", projectId],
    queryFn: () => api.workItems.list(projectId),
    enabled: Boolean(projectId),
  });
  const dashboardQuery = useQuery({
    queryKey: ["project-dashboard", projectId],
    queryFn: () => api.projects.getDashboard(projectId),
    enabled: Boolean(projectId),
  });

  const executions = executionsQuery.data || [];
  const approvals = approvalsQuery.data || [];
  const workItems = workItemsQuery.data || [];
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return executions.filter((execution) => {
      const statusMatches =
        activeTab === "all" ||
        execution.status === activeTab ||
        (activeTab === "running" && execution.status === "queued");
      const textMatches =
        !needle ||
        `${execution.work_item_title} ${execution.work_item_key} ${execution.summary} ${execution.workspace_path}`
          .toLowerCase()
          .includes(needle);
      return statusMatches && textMatches;
    });
  }, [activeTab, executions, query]);

  useEffect(() => {
    if (!filtered.length) {
      setSelectedId("");
      return;
    }
    if (!filtered.some((item) => item.id === selectedId)) {
      setSelectedId(filtered[0].id);
    }
  }, [filtered, selectedId]);

  const detailQuery = useQuery({
    queryKey: ["codex-bridge-execution", selectedId],
    queryFn: () => api.codexBridge.execution(selectedId),
    enabled: Boolean(selectedId),
    refetchInterval: (queryState) =>
      queryState.state.data?.status === "running" ? 5_000 : false,
  });
  const selected =
    detailQuery.data || executions.find((item) => item.id === selectedId);
  const usedWorkItems = new Set(executions.map((item) => item.work_item_id));
  const availableWorkItems = workItems.filter(
    (item) =>
      !["done", "cancelled"].includes(item.status) &&
      !usedWorkItems.has(item.id),
  );
  const projectPath = String(
    dashboardQuery.data?.project.settings?.workspace_path || "",
  );

  const counts = useMemo(
    () => ({
      all: executions.length,
      awaiting_approval: executions.filter(
        (item) => item.status === "awaiting_approval",
      ).length,
      running: executions.filter((item) =>
        ["queued", "running"].includes(item.status),
      ).length,
      review: executions.filter((item) => item.status === "review").length,
      completed: executions.filter((item) => item.status === "completed")
        .length,
    }),
    [executions],
  );

  const refresh = () => {
    statusQuery.refetch();
    executionsQuery.refetch();
    approvalsQuery.refetch();
    workItemsQuery.refetch();
  };

  const startTask = (
    workItemId?: string,
    template?: { title: string; objective: string; acceptance: string },
  ) => {
    if (statusQuery.data?.availability !== "ready") return;
    setInitialTaskId(workItemId);
    setInitialTemplate(template);
    setCreateOpen(true);
  };

  const status = statusQuery.data;
  const mcpReady = !statusQuery.isError && status?.mcp.status === "ready";
  const codexReady =
    !statusQuery.isError &&
    status?.availability === "ready" &&
    status.reason !== "capability_snapshot_stale";
  const clientAvailability =
    status?.reason === "capability_snapshot_stale"
      ? "disconnected"
      : status?.availability || "unavailable";
  const pageError =
    statusQuery.isError ||
    executionsQuery.isError ||
    approvalsQuery.isError ||
    workItemsQuery.isError;

  return (
    <div className="codex-bridge">
      <header className="codex-bridge__heading">
        <div>
          <h1>Codex 联动</h1>
          <p>将研究任务安全交给 Codex，并在同一处追踪执行、审批与结果回传</p>
        </div>
        <div>
          <Button variant="secondary" onClick={() => setSettingsOpen(true)}>
            <Settings2 size={16} />
            连接设置
          </Button>
          <Button
            variant="primary"
            disabled={!codexReady}
            onClick={() => startTask()}
          >
            <Plus size={17} />
            新建执行
          </Button>
        </div>
      </header>

      {pageError ? (
        <div className="bridge-error-banner">
          <XCircle size={16} />
          部分联动数据暂时不可用，请检查服务后重试。
          <button onClick={refresh}>重新加载</button>
        </div>
      ) : null}
      {!statusQuery.isLoading && status && !codexReady ? (
        <div className="bridge-error-banner bridge-error-banner--warning">
          <Info size={16} />
          {availabilityReasons[status.reason] ||
            "Codex 尚未连接到当前项目。"}{" "}
          请先完成连接诊断；未连接状态不会开放执行写入。
        </div>
      ) : null}

      <section className="bridge-connection" aria-label="Codex 连接状态">
        <ConnectionNode
          icon={<Layers3 size={19} />}
          title="RAG Core"
          status={
            statusQuery.isLoading
              ? "检查中"
              : statusQuery.isError
                ? "不可用"
                : "已就绪"
          }
          tone={statusQuery.isError ? "error" : "ready"}
        />
        <span
          className={`bridge-connection__line ${mcpReady ? "" : "is-warning"}`}
        />
        <ConnectionNode
          icon={<Link2 size={19} />}
          title="MCP Bridge"
          status={
            statusQuery.isLoading
              ? "检查中"
              : statusQuery.isError
                ? "不可用"
                : mcpReady
                  ? "凭据已配置"
                  : "待配置令牌"
          }
          tone={statusQuery.isError ? "error" : mcpReady ? "ready" : "warning"}
        />
        <span
          className={`bridge-connection__line ${codexReady ? "" : "is-warning"}`}
        />
        <ConnectionNode
          icon={<SquareTerminal size={19} />}
          title="Codex 客户端"
          status={
            statusQuery.isLoading
              ? "检查中"
              : availabilityLabels[clientAvailability] || "不可用"
          }
          tone={codexReady ? "ready" : "warning"}
        />
        <span className="bridge-connection__meta">
          {availabilityReasons[status?.reason || ""] ||
            "连接状态由服务端权威返回"}{" "}
          · 最近连接 {relativeTime(status?.last_connected_at)}
          <button onClick={() => setSettingsOpen(true)}>查看诊断</button>
        </span>
      </section>

      <BridgeSyncSummary
        sync={status?.sync}
        loading={statusQuery.isLoading}
        error={statusQuery.isError}
      />

      <div className="bridge-workspace">
        <section className="bridge-queue">
          <header className="bridge-queue__header">
            <div>
              <h2>执行队列</h2>
              <button
                onClick={refresh}
                aria-label="刷新执行队列"
                title="刷新执行队列"
              >
                <RefreshCw
                  className={
                    executionsQuery.isFetching || approvalsQuery.isFetching
                      ? "is-spinning"
                      : ""
                  }
                  size={16}
                />
              </button>
            </div>
            <nav aria-label="执行状态">
              {tabs.map((tab) => (
                <button
                  role="tab"
                  aria-selected={activeTab === tab.key}
                  className={activeTab === tab.key ? "is-active" : ""}
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                >
                  {tab.label}
                  <span>{counts[tab.key]}</span>
                </button>
              ))}
            </nav>
            <div className="bridge-queue__tools">
              <label>
                <Search size={16} />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索任务、ID 或执行摘要"
                  aria-label="搜索执行队列"
                />
              </label>
              <select
                value={activeTab}
                onChange={(event) => setActiveTab(event.target.value)}
                aria-label="筛选执行状态"
              >
                {tabs.map((tab) => (
                  <option key={tab.key} value={tab.key}>
                    {tab.label}
                  </option>
                ))}
              </select>
            </div>
          </header>

          {executionsQuery.isLoading ? (
            <div className="bridge-loading">
              <LoaderCircle className="is-spinning" size={20} />
              正在读取执行队列…
            </div>
          ) : filtered.length ? (
            <div className="bridge-execution-table">
              <div className="bridge-execution-table__head">
                <span>任务</span>
                <span>执行方式</span>
                <span>状态</span>
                <span>变更与验证</span>
                <span>最近更新</span>
              </div>
              {filtered.map((execution) => (
                <button
                  className={execution.id === selectedId ? "is-selected" : ""}
                  key={execution.id}
                  onClick={() => setSelectedId(execution.id)}
                  aria-label={`查看执行：${execution.work_item_title}`}
                >
                  <span className="bridge-execution-task">
                    <i>
                      {execution.mode === "interactive" ? (
                        <Braces size={16} />
                      ) : (
                        <SquareTerminal size={16} />
                      )}
                    </i>
                    <span>
                      <strong>{execution.work_item_title}</strong>
                      <small>{execution.work_item_key}</small>
                    </span>
                  </span>
                  <span className="bridge-execution-mode">
                    <strong>
                      {execution.mode === "interactive"
                        ? "交互模式"
                        : "队列模式"}
                    </strong>
                    <small>
                      {execution.mode === "interactive"
                        ? "实时会话"
                        : "审批后执行"}
                    </small>
                  </span>
                  <span className="bridge-execution-status">
                    <Status value={execution.status}>
                      {executionLabels[execution.status]}
                    </Status>
                    {execution.status === "running" ? (
                      <small>正在执行</small>
                    ) : null}
                  </span>
                  <span className="bridge-execution-evidence">
                    <span>
                      <FileCode2 size={14} />
                      {execution.changed_files.length} 文件
                    </span>
                    <span>
                      <FlaskConical size={14} />
                      {execution.validation.length} 验证
                    </span>
                  </span>
                  <span className="bridge-execution-updated">
                    <strong>{relativeTime(execution.updated_at)}</strong>
                    <small>{formatTime(execution.updated_at)}</small>
                  </span>
                </button>
              ))}
            </div>
          ) : executions.length ? (
            <EmptyState
              icon={<Search size={22} />}
              title="没有符合条件的执行"
              description="调整状态或搜索条件，查看其他 Codex 执行。"
            />
          ) : (
            <div className="bridge-empty-queue">
              <EmptyState
                icon={<Braces size={23} />}
                title="还没有 Codex 执行"
                description="从一个明确的研究任务开始，系统会自动附带项目上下文与验收条件。"
                action={
                  <Button
                    variant="primary"
                    disabled={!codexReady}
                    onClick={() => startTask()}
                  >
                    <Plus size={16} />
                    新建执行
                  </Button>
                }
              />
              {availableWorkItems.length ? (
                <section className="bridge-ready-tasks">
                  <header>
                    <span>
                      <Layers3 size={16} />
                      <strong>可交给 Codex 的任务</strong>
                    </span>
                    <em>{availableWorkItems.length} 项</em>
                  </header>
                  {availableWorkItems.slice(0, 4).map((item) => (
                    <button
                      key={item.id}
                      disabled={!codexReady}
                      onClick={() => startTask(item.id)}
                    >
                      <span>
                        <strong>{item.title}</strong>
                        <small>
                          {item.display_key} ·{" "}
                          {item.objective || "待补充执行目标"}
                        </small>
                      </span>
                      <span>
                        交给 Codex
                        <ChevronRight size={15} />
                      </span>
                    </button>
                  ))}
                </section>
              ) : (
                <section className="bridge-starting-points">
                  <header>
                    <span>
                      <Layers3 size={16} />
                      <strong>从这里开始</strong>
                    </span>
                    <em>创建任务后自动联动</em>
                  </header>
                  {[
                    {
                      title: "实现一项代码改进",
                      objective:
                        "读取项目上下文，完成目标代码变更并运行相关验证。",
                      acceptance:
                        "实现范围与任务目标一致\n相关测试与类型检查通过\n回传修改文件和验证结果",
                      icon: <FileCode2 size={16} />,
                    },
                    {
                      title: "补齐测试与回归验证",
                      objective:
                        "识别当前风险面，补充必要测试并验证现有行为没有回归。",
                      acceptance:
                        "新增测试覆盖目标场景\n完整测试集通过\n记录仍未覆盖的风险",
                      icon: <FlaskConical size={16} />,
                    },
                    {
                      title: "只读分析当前项目",
                      objective:
                        "在不修改文件的前提下分析实现、风险与下一步可执行任务。",
                      acceptance:
                        "给出带来源的实现摘要\n列出主要风险与验证缺口\n形成可执行的下一步建议",
                      icon: <Search size={16} />,
                    },
                  ].map((template) => (
                    <button
                      key={template.title}
                      disabled={!codexReady}
                      onClick={() => startTask(undefined, template)}
                    >
                      <i>{template.icon}</i>
                      <span>
                        <strong>{template.title}</strong>
                        <small>{template.objective}</small>
                      </span>
                      <ChevronRight size={15} />
                    </button>
                  ))}
                </section>
              )}
            </div>
          )}
        </section>

        <ExecutionInspector
          projectId={projectId}
          execution={selected}
          approvals={approvals}
          codexReady={codexReady}
        />
      </div>

      {createOpen ? (
        <CreateExecutionDrawer
          projectId={projectId}
          projectPath={projectPath}
          workItems={availableWorkItems.length ? availableWorkItems : workItems}
          initialTaskId={initialTaskId}
          initialTemplate={initialTemplate}
          onClose={() => setCreateOpen(false)}
          onCreated={(execution) => {
            setSelectedId(execution.id);
            setCreateOpen(false);
          }}
        />
      ) : null}
      {settingsOpen ? (
        <ConnectionSettings
          status={status}
          statusLoading={statusQuery.isLoading}
          statusError={statusQuery.isError}
          onClose={() => setSettingsOpen(false)}
        />
      ) : null}
    </div>
  );
}
