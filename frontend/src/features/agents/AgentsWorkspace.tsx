import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  CircleAlert,
  Clock3,
  Database,
  Fingerprint,
  Link2,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { EmptyState, Status } from "../../components/ui";
import { api } from "../../lib/api";
import type {
  AgentAuditEvent,
  AgentControlPlaneItem,
  CodexSyncProjection,
} from "../../lib/types";

const availabilityLabels = {
  ready: "已连接",
  disconnected: "未连接",
  unauthorized: "未授权",
  unavailable: "不可用",
} as const;

const reasonLabels: Record<string, string> = {
  connected: "已观察到项目凭据握手",
  handshake_not_observed: "尚未观察到客户端握手",
  capability_snapshot_stale: "能力快照已过期，请重新握手",
  project_token_missing: "当前项目尚未配置访问凭据",
  project_token_inactive: "当前项目凭据已失效或撤销",
  plugin_missing: "未检测到 Research Project Bridge",
};

const syncLabels: Record<
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

const auditEventLabels: Record<string, string> = {
  "mcp.handshake": "MCP 握手已验证",
  "capability.discovery": "能力快照已发现",
  "execution.claimed": "执行已认领",
  "execution.report": "执行状态已回报",
  "approval.requested": "审批已请求",
  "approval.decided": "审批已决策",
};

function formatTime(value?: string | null) {
  if (!value) return "尚无记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间不可用"
    : date.toLocaleString("zh-CN");
}

function SyncSummary({ sync }: { sync?: CodexSyncProjection }) {
  const presentation =
    syncLabels[sync?.status || "unavailable"] || syncLabels.unavailable;
  return (
    <section className="agent-sync" aria-label="会话同步状态">
      <header>
        <span>
          <Database size={16} />
          <strong>会话数据同步</strong>
        </span>
        <Status value={presentation.tone}>{presentation.label}</Status>
      </header>
      <p>{presentation.description}</p>
      <dl>
        <div>
          <dt>已索引会话</dt>
          <dd>{sync?.indexed_sessions ?? 0}</dd>
        </div>
        <div>
          <dt>已发现来源</dt>
          <dd>{sync?.source_count ?? 0}</dd>
        </div>
        <div>
          <dt>最近同步</dt>
          <dd>{formatTime(sync?.last_sync_at)}</dd>
        </div>
      </dl>
    </section>
  );
}

function AuditEvent({ event }: { event: AgentAuditEvent }) {
  return (
    <li>
      <Clock3 size={15} />
      <span>
        <strong>{auditEventLabels[event.event_type] || "受治理事件"}</strong>
        <small>{formatTime(event.created_at)}</small>
      </span>
      <em>
        {event.resource_type === "approval"
          ? "审批"
          : event.resource_type === "execution"
            ? "执行"
            : "连接"}
      </em>
    </li>
  );
}

function AgentInspector({
  projectId,
  agent,
}: {
  projectId: string;
  agent?: AgentControlPlaneItem;
}) {
  const clientId = agent?.client_id || "";
  const auditQuery = useQuery({
    queryKey: ["agent-audit", projectId, clientId],
    queryFn: () => api.codexBridge.agentAudit(projectId, clientId, 20),
    enabled: Boolean(projectId && agent && clientId),
    refetchInterval: 20_000,
  });

  if (!agent) {
    return (
      <aside className="agent-inspector">
        <EmptyState
          icon={<Bot size={22} />}
          title="选择一个智能体"
          description="查看能力快照、权限、同步健康和最近审计事件。"
        />
      </aside>
    );
  }

  const snapshot =
    agent.reason === "capability_snapshot_stale"
      ? undefined
      : agent.capability_snapshot;
  const effectiveAvailability =
    agent.reason === "capability_snapshot_stale"
      ? "disconnected"
      : agent.availability;
  const reportedClient = snapshot?.handshake_identity.reported_client;
  const auditEvents = auditQuery.data?.items || [];

  return (
    <aside className="agent-inspector" aria-label="智能体详情">
      <header>
        <div>
          <small>{agent.connector}</small>
          <h2>{agent.name}</h2>
        </div>
        <Status value={effectiveAvailability}>
          {availabilityLabels[effectiveAvailability]}
        </Status>
      </header>
      <section>
        <h3>连接与范围</h3>
        <dl className="agent-facts">
          <div>
            <dt>状态原因</dt>
            <dd>{reasonLabels[agent.reason] || "连接状态不可用"}</dd>
          </div>
          <div>
            <dt>项目范围</dt>
            <dd>{agent.scope.project_id}</dd>
          </div>
          <div>
            <dt>令牌权限</dt>
            <dd>{agent.scope.token_scopes.join(" · ") || "未授权"}</dd>
          </div>
          <div>
            <dt>最近连接</dt>
            <dd>{formatTime(agent.last_seen_at)}</dd>
          </div>
        </dl>
      </section>

      <SyncSummary sync={agent.sync} />

      <section className="agent-onboarding" aria-label="Codex 智能体使用方法">
        <header>
          <div>
            <h3>如何接入并使用 Codex</h3>
            <small>连接、核验、查询、执行四步均绑定当前项目</small>
          </div>
          <Status value={effectiveAvailability}>
            {effectiveAvailability === "ready" ? "可使用" : "待连接"}
          </Status>
        </header>
        <ol>
          <li>
            <b>1</b>
            <span>
              <strong>配置同一项目凭据</strong>
              <small>
                在右上角“设置请求凭据”填写项目令牌，并让 Codex 插件通过
                EVIDENCE_RAG_MCP_TOKEN 使用同一令牌。
              </small>
            </span>
          </li>
          <li>
            <b>2</b>
            <span>
              <strong>启用 Research Wiki + RAG Bridge</strong>
              <small>
                插件连接本机 /mcp 后，本页会显示已验证握手、协议版本与真实能力快照。
              </small>
            </span>
          </li>
          <li>
            <b>3</b>
            <span>
              <strong>先查 Wiki，再核验原始证据</strong>
              <small>
                让 Codex 搜索当前项目、沿关系导航，并对关键结论执行原始证据回查。
              </small>
            </span>
          </li>
          <li>
            <b>4</b>
            <span>
              <strong>只执行已授权任务</strong>
              <small>
                写操作仍受项目 ACL、审批和审计约束；智能体不能自行发布 Wiki 或绕过复核。
              </small>
            </span>
          </li>
        </ol>
        <nav aria-label="智能体下一步">
          <Link to={`/p/${encodeURIComponent(projectId)}/codex`}>打开 Codex 联动配置</Link>
          <Link to={`/p/${encodeURIComponent(projectId)}/wiki`}>开始证据查询</Link>
          <Link to={`/p/${encodeURIComponent(projectId)}/sessions`}>查看同步会话</Link>
        </nav>
      </section>

      <section>
        <h3>服务端能力快照</h3>
        {snapshot ? (
          <>
            <dl className="agent-facts">
              <div>
                <dt>认证身份</dt>
                <dd>
                  {snapshot.handshake_identity.authority ===
                  "authenticated_bearer_principal"
                    ? "项目 Bearer 身份"
                    : "服务端认证身份"}
                </dd>
              </div>
              <div>
                <dt>协议 / 服务端</dt>
                <dd>
                  {snapshot.protocol_version} · {snapshot.server_version}
                </dd>
              </div>
              <div>
                <dt>发现时间</dt>
                <dd>{formatTime(snapshot.observed_at)}</dd>
              </div>
              <div>
                <dt>客户端声明</dt>
                <dd>
                  {[reportedClient?.name, reportedClient?.version]
                    .filter(Boolean)
                    .join(" · ") || "未公开"}
                </dd>
              </div>
            </dl>
            {snapshot.capabilities.length ? (
              <ul className="agent-capabilities">
                {snapshot.capabilities.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : (
              <p className="agent-muted">快照未声明可用能力。</p>
            )}
          </>
        ) : (
          <p className="agent-muted">
            <Fingerprint size={15} /> 尚无已验证握手快照，不展示可用能力。
          </p>
        )}
      </section>

      <section>
        <h3>最近运行</h3>
        {agent.recent_executions.length ? (
          agent.recent_executions.map((execution) => (
            <article className="agent-run" key={execution.id}>
              <span>
                <strong>{execution.work_item_title}</strong>
                <small>
                  {execution.status} · {formatTime(execution.updated_at)}
                </small>
              </span>
              {execution.thread_id ? (
                <Link
                  to={`/p/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(execution.thread_id)}`}
                >
                  查看会话
                </Link>
              ) : null}
            </article>
          ))
        ) : (
          <p className="agent-muted">当前项目尚无该智能体运行记录。</p>
        )}
      </section>

      <section className="agent-audit" aria-label="项目 ACL 最近审计事件">
        <header>
          <div>
            <h3>最近审计事件</h3>
            <small>由当前项目 ACL 过滤，仅展示安全摘要</small>
          </div>
          {clientId ? (
            <button
              type="button"
              onClick={() => void auditQuery.refetch()}
              disabled={auditQuery.isFetching}
            >
              <RefreshCw
                size={14}
                className={auditQuery.isFetching ? "is-spinning" : ""}
              />
              {auditQuery.isFetching ? "读取中" : "重试 / 刷新"}
            </button>
          ) : null}
        </header>
        {!clientId ? (
          <p className="agent-muted">无已验证客户端身份，未读取审计事件。</p>
        ) : auditQuery.isLoading ? (
          <p className="agent-muted">
            <Activity size={15} /> 正在读取审计事件…
          </p>
        ) : auditQuery.isError ? (
          <div className="agent-audit__error" role="alert">
            <CircleAlert size={16} />
            <span>审计事件不可读取；不会使用缓存内容冒充最新状态。</span>
            <button type="button" onClick={() => void auditQuery.refetch()}>
              重试
            </button>
          </div>
        ) : auditEvents.length ? (
          <ol>
            {auditEvents.map((event) => (
              <AuditEvent key={event.id} event={event} />
            ))}
          </ol>
        ) : (
          <p className="agent-muted">当前项目 ACL 范围内尚无审计事件。</p>
        )}
      </section>

      <footer>
        <ShieldCheck size={15} />
        本页只读，不注册智能体、不签发凭据、不启动执行。
      </footer>
    </aside>
  );
}

export function AgentsWorkspace() {
  const { projectId = "" } = useParams();
  const [selectedId, setSelectedId] = useState("");
  const agentsQuery = useQuery({
    queryKey: ["agents", projectId],
    queryFn: () => api.codexBridge.agents(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 20_000,
  });
  const agents = agentsQuery.data || [];

  useEffect(() => {
    if (!agents.length) {
      setSelectedId("");
      return;
    }
    if (!agents.some((item) => item.id === selectedId)) {
      setSelectedId(agents[0].id);
    }
  }, [agents, selectedId]);

  const selected = useMemo(
    () => agents.find((item) => item.id === selectedId),
    [agents, selectedId],
  );

  return (
    <div className="agents-workspace">
      <header className="agents-heading">
        <div>
          <h1>智能体控制面</h1>
          <p>按项目查看真实连接、能力快照、同步状态与 ACL 审计事件</p>
        </div>
        <span>
          <ShieldCheck size={16} /> 只读
        </span>
      </header>
      {agentsQuery.isError ? (
        <div className="bridge-error-banner">
          <CircleAlert size={16} />
          智能体状态不可读取：当前身份未授权、服务不可用或项目不存在。
          <button type="button" onClick={() => void agentsQuery.refetch()}>
            重试
          </button>
        </div>
      ) : null}
      <div className="agents-layout">
        <section className="agents-list" aria-label="项目智能体">
          <header>
            <h2>项目智能体</h2>
            <span>{agents.length}</span>
          </header>
          {agentsQuery.isLoading ? (
            <p className="agent-muted">
              <Activity size={16} /> 正在读取权威状态…
            </p>
          ) : agentsQuery.isError ? (
            <EmptyState
              icon={<CircleAlert size={22} />}
              title="智能体状态不可用"
              description="未显示缓存能力或连接状态；请重试权威查询。"
            />
          ) : agents.length ? (
            agents.map((agent) => {
              const sync =
                syncLabels[agent.sync?.status || "unavailable"] ||
                syncLabels.unavailable;
              const effectiveAvailability =
                agent.reason === "capability_snapshot_stale"
                  ? "disconnected"
                  : agent.availability;
              return (
                <button
                  key={agent.id}
                  className={agent.id === selectedId ? "is-selected" : ""}
                  onClick={() => setSelectedId(agent.id)}
                >
                  <Bot size={18} />
                  <span>
                    <strong>{agent.name}</strong>
                    <small>
                      {agent.connector} · {sync.label}
                    </small>
                  </span>
                  <Status value={effectiveAvailability}>
                    {availabilityLabels[effectiveAvailability]}
                  </Status>
                </button>
              );
            })
          ) : (
            <EmptyState
              icon={<Link2 size={22} />}
              title="没有项目智能体"
              description="当前项目没有受治理的 connector 或访问凭据。"
            />
          )}
        </section>
        <AgentInspector projectId={projectId} agent={selected} />
      </div>
    </div>
  );
}
