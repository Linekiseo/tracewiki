import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  DatabaseZap,
  Gauge,
  Info,
  Layers3,
  RefreshCw,
  ServerOff,
  ShieldAlert,
  SlidersHorizontal,
} from "lucide-react";
import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { Button } from "../../components/ui";
import { api, ApiRequestError } from "../../lib/api";
import type { OpsField } from "./ragStatus";
import { normalizeRagStatus } from "./ragStatus";

const STATUS_REFRESH_INTERVAL_MS = 30_000;

function StatusValue({
  field,
  compact = false,
}: {
  field: OpsField;
  compact?: boolean;
}) {
  return (
    <span
      className={`ops-status-value ${compact ? "ops-status-value--compact" : ""}`}
      data-tone={field.tone}
    >
      <strong>{field.value}</strong>
      <small>{field.detail}</small>
    </span>
  );
}

function unavailableCopy(error: unknown) {
  if (error instanceof ApiRequestError && error.status === 404) {
    return {
      title: "UNAVAILABLE · 404",
      description: "只读状态端点不存在。界面不会从其他接口或缺失字段推断运行状态。",
    };
  }
  if (error instanceof ApiRequestError && error.status === 503) {
    return {
      title: "UNAVAILABLE · 503",
      description: "RAG 状态服务当前不可用，无法确认引擎、发布、来源或性能状态。",
    };
  }
  return {
    title: "UNAVAILABLE",
    description: "状态请求失败。当前所有未获取到的结论都按不可确认处理。",
  };
}

function UnavailableState({
  error,
  onRetry,
  retrying,
}: {
  error: unknown;
  onRetry: () => void;
  retrying: boolean;
}) {
  const copy = unavailableCopy(error);
  return (
    <section className="ops-unavailable" role="alert" aria-live="assertive">
      <span className="ops-unavailable__icon">
        <ServerOff size={23} aria-hidden="true" />
      </span>
      <div>
        <h2>{copy.title}</h2>
        <p>{copy.description}</p>
      </div>
      <Button type="button" variant="secondary" onClick={onRetry} disabled={retrying}>
        <RefreshCw
          className={retrying ? "is-spinning" : undefined}
          size={16}
          aria-hidden="true"
        />
        重新读取
      </Button>
    </section>
  );
}

export function RagOpsPage() {
  const { projectId } = useParams<{ projectId?: string }>();
  const statusQuery = useQuery({
    queryKey: ["rag", "ops-status"],
    queryFn: ({ signal }) => api.rag.status(signal),
    retry: false,
    staleTime: 0,
    refetchInterval: STATUS_REFRESH_INTERVAL_MS,
  });
  const model = useMemo(
    () => (statusQuery.data ? normalizeRagStatus(statusQuery.data) : null),
    [statusQuery.data, statusQuery.dataUpdatedAt],
  );

  return (
    <section className="ops-page" aria-labelledby="ops-page-title">
      <header className="page-heading ops-heading">
        <div>
          <h1 id="ops-page-title">RAG 运维状态</h1>
          <p>只读呈现规范状态合同；未知、失败和陈旧数据均保守降级。</p>
        </div>
        <div className="ops-heading__actions">
          <span className="ops-readonly-mark">
            <ShieldAlert size={15} aria-hidden="true" />
            READ ONLY
          </span>
          <Button
            type="button"
            variant="secondary"
            onClick={() => void statusQuery.refetch()}
            disabled={statusQuery.isFetching}
          >
            <RefreshCw
              className={statusQuery.isFetching ? "is-spinning" : undefined}
              size={16}
              aria-hidden="true"
            />
            刷新状态
          </Button>
        </div>
      </header>

      <div className="ops-scope-note" role="note">
        <Info size={16} aria-hidden="true" />
        <span>
          {projectId
            ? "已选择项目作为导航上下文；本页仍只读取全局 RAG 运行时，不执行项目操作。"
            : "未选择项目；本页读取全局 RAG 运行时，项目级状态不作推断。"}
        </span>
      </div>

      {statusQuery.isPending && !model ? (
        <div className="ops-loading" role="status" aria-live="polite" aria-busy="true">
          <RefreshCw className="is-spinning" size={19} aria-hidden="true" />
          正在读取只读状态…
        </div>
      ) : null}

      {statusQuery.isError && !model ? (
        <UnavailableState
          error={statusQuery.error}
          onRetry={() => void statusQuery.refetch()}
          retrying={statusQuery.isFetching}
        />
      ) : null}

      {model ? (
        <>
          {statusQuery.isError ? (
            <UnavailableState
              error={statusQuery.error}
              onRetry={() => void statusQuery.refetch()}
              retrying={statusQuery.isFetching}
            />
          ) : null}

          {model.freshness.tone === "danger" || model.freshness.tone === "unknown" ? (
            <section className="ops-freshness-alert" role="alert">
              <AlertTriangle size={18} aria-hidden="true" />
              <div>
                <strong>{model.freshness.value}</strong>
                <p>
                  {model.freshness.detail}。下方仍展示最后一份响应，但不得视作当前状态。
                </p>
              </div>
            </section>
          ) : null}

          <section className="ops-summary" aria-label="运行时概要">
            <div>
              <span>Default engine</span>
              <StatusValue field={model.defaultEngine} />
            </div>
            <div>
              <span>Quality hold</span>
              <StatusValue field={model.qualityHold} />
            </div>
            <div>
              <span>Response freshness</span>
              <StatusValue field={model.freshness} />
            </div>
            <div>
              <span>Reviewed calibration</span>
              <StatusValue field={model.reviewedCalibration} />
            </div>
          </section>

          <section className="ops-panel" aria-labelledby="ops-release-title">
            <header className="ops-panel__header">
              <span>
                <ShieldAlert size={18} aria-hidden="true" />
              </span>
              <div>
                <h2 id="ops-release-title">Canonical release</h2>
                <p>只转述规范决策、authority 与 integrity；浏览器不签发发布结论。</p>
              </div>
            </header>
            <dl className="ops-fact-list">
              <div>
                <dt>Decision</dt>
                <dd><StatusValue field={model.releaseDecision} /></dd>
              </div>
              <div>
                <dt>Authority</dt>
                <dd><StatusValue field={model.releaseAuthority} /></dd>
              </div>
              <div>
                <dt>Integrity</dt>
                <dd><StatusValue field={model.releaseIntegrity} /></dd>
              </div>
            </dl>
          </section>

          <section className="ops-panel" aria-labelledby="ops-source-title">
            <header className="ops-panel__header">
              <span>
                <Layers3 size={18} aria-hidden="true" />
              </span>
              <div>
                <h2 id="ops-source-title">来源运行状态</h2>
                <p>每个来源独立显示 V1、V2、fallback 与 last-known-good。</p>
              </div>
            </header>
            <div className="ops-source-table-wrap">
              <table className="ops-source-table">
                <thead>
                  <tr>
                    <th scope="col">来源</th>
                    <th scope="col">V1 stage</th>
                    <th scope="col">V2 stage</th>
                    <th scope="col">Fallback</th>
                    <th scope="col">LKG</th>
                  </tr>
                </thead>
                <tbody>
                  {model.sources.map((source) => (
                    <tr key={source.source}>
                      <th scope="row">
                        <span className="ops-source-name">
                          <DatabaseZap size={15} aria-hidden="true" />
                          {source.label}
                        </span>
                      </th>
                      <td data-label="V1 stage"><StatusValue compact field={source.v1} /></td>
                      <td data-label="V2 stage"><StatusValue compact field={source.v2} /></td>
                      <td data-label="Fallback">
                        <StatusValue compact field={source.fallback} />
                      </td>
                      <td data-label="LKG">
                        <StatusValue compact field={source.lastKnownGood} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="ops-panel" aria-labelledby="ops-switch-title">
            <header className="ops-panel__header">
              <span>
                <SlidersHorizontal size={18} aria-hidden="true" />
              </span>
              <div>
                <h2 id="ops-switch-title">七组件开关 · 只读</h2>
                <p>
                  每项使用独立严格布尔值；版本 {model.switchesVersion}，缺失值不继承。
                </p>
              </div>
            </header>
            <ul className="ops-switch-list">
              {model.switches.map((item, index) => (
                <li key={item.key}>
                  <span className="ops-switch-list__index" aria-hidden="true">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="ops-switch-list__label">{item.label}</span>
                  <StatusValue compact field={item.state} />
                </li>
              ))}
            </ul>
          </section>

          <section className="ops-panel" aria-labelledby="ops-performance-title">
            <header className="ops-panel__header">
              <span>
                <Gauge size={18} aria-hidden="true" />
              </span>
              <div>
                <h2 id="ops-performance-title">Performance</h2>
                <p>仅显示聚合后的 index、cache、latency 与 dashboard 合同字段。</p>
              </div>
            </header>
            <div className="ops-performance-grid">
              <article>
                <span>Index</span>
                <StatusValue field={model.performance.index} />
              </article>
              <article>
                <span>Cache</span>
                <StatusValue field={model.performance.cache} />
              </article>
              <article>
                <span>Dashboard</span>
                <StatusValue field={model.performance.dashboard} />
              </article>
            </div>
            <div className="ops-latency">
              <h3>
                <Activity size={16} aria-hidden="true" />
                Latency distribution
              </h3>
              {model.performance.latency.length ? (
                <div className="ops-latency__table" role="table" aria-label="延迟分布">
                  <div role="row">
                    <span role="columnheader">Span</span>
                    <span role="columnheader">Count</span>
                    <span role="columnheader">P50</span>
                    <span role="columnheader">P95</span>
                    <span role="columnheader">Max</span>
                  </div>
                  {model.performance.latency.map((row) => (
                    <div role="row" key={row.name}>
                      <strong role="cell">{row.name}</strong>
                      <span role="cell">{row.count ?? "UNKNOWN"}</span>
                      <span role="cell">{row.p50 === null ? "UNKNOWN" : `${row.p50} ms`}</span>
                      <span role="cell">{row.p95 === null ? "UNKNOWN" : `${row.p95} ms`}</span>
                      <span role="cell">
                        {row.maximum === null ? "UNKNOWN" : `${row.maximum} ms`}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="ops-latency__empty">
                  UNKNOWN · 没有可显示的允许列表延迟聚合。
                </p>
              )}
            </div>
          </section>

          <footer className="ops-contract-note">
            <span>
              <Info size={15} aria-hidden="true" />
              schema {model.schemaVersion}
            </span>
            <span>
              快照 {model.snapshotTime ? model.snapshotTime : "UNKNOWN"}
            </span>
            <strong>本页不提供引擎切换、发布、回滚或状态写入。</strong>
          </footer>
        </>
      ) : null}
    </section>
  );
}
