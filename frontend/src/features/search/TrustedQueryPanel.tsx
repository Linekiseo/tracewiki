import {
  AlertTriangle,
  ArrowRight,
  Ban,
  CheckCircle2,
  ChevronDown,
  CircleStop,
  Clock3,
  Database,
  Link2,
  MessageSquareText,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import {
  type ComponentProps,
  type CSSProperties,
  type FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button, EmptyState, Status } from "../../components/ui";
import { api } from "../../lib/api";
import type {
  Project,
  QueryEngineOverrides,
  QueryHistoryItem,
  QueryIntent,
  QueryPlanPreview,
  QuerySource,
  TrustedQueryResponse,
} from "../../lib/types";
import {
  queryCitations,
  queryBlockers,
  queryClaimCitationMap,
  queryClaimVerification,
  queryCorrectiveRounds,
  queryEngineRouting,
  queryEvidence,
  queryFallbacks,
  queryMissingRoles,
  queryNextActions,
  queryPresentationMode,
  queryReasons,
  queryRequiredRoles,
  querySatisfiedRoles,
  queryScope,
  querySourceStatus,
  queryStaleRoles,
  queryTransitions,
  queryWatermarks,
  safeClientError,
  safeMetadataValue,
  sanitizeDisplayText,
} from "./queryContract";

const querySources: Array<{ key: QuerySource; label: string }> = [
  { key: "code", label: "代码" },
  { key: "codex", label: "研发会话" },
  { key: "experiment", label: "实验" },
  { key: "notebook", label: "Notebook" },
  { key: "document", label: "文档" },
  { key: "workspace", label: "项目工作" },
];

const queryIntents: Array<{ key: QueryIntent; label: string }> = [
  { key: "current_implementation", label: "当前实现" },
  { key: "historical_implementation", label: "历史实现" },
  { key: "change_trace", label: "变更追踪" },
  { key: "rationale", label: "设计依据" },
  { key: "experiment_validation", label: "实验验证" },
  { key: "claim_verification", label: "声明核验" },
  { key: "reproduction", label: "复现" },
  { key: "staleness_check", label: "时效检查" },
  { key: "global_synthesis", label: "全局综合" },
];

const modeMeta = {
  clarification: {
    label: "需要澄清",
    description: "补充范围或版本后，系统才会继续检索。",
    icon: AlertTriangle,
  },
  partial: {
    label: "PARTIAL",
    description: "部分来源未完成，以下结论仅覆盖已返回证据。",
    icon: AlertTriangle,
  },
  refusal: {
    label: "拒绝作答",
    description: "当前证据、权限或版本条件不足以形成可信回答。",
    icon: Ban,
  },
  retrieval_only: {
    label: "仅检索",
    description: "未执行生成；内容是后端基于证据返回的检索摘要。",
    icon: Database,
  },
  grounded: {
    label: "GROUNDED",
    description: "回答已通过证据引用校验。",
    icon: ShieldCheck,
  },
  unverified: {
    label: "UNVERIFIED",
    description: "响应状态不在已知可信契约内；前端已按 fail-closed 处理。",
    icon: AlertTriangle,
  },
} as const;

const sourceStatusLabels: Record<string, string> = {
  complete: "完成",
  partial: "部分完成",
  timeout: "超时",
  unavailable: "不可用",
  unauthorized: "无权限",
  not_indexed: "未索引",
  no_matching_evidence: "无匹配证据",
};

const roleLabels: Record<string, string> = {
  current_code: "当前代码",
  version: "明确版本",
  tests: "测试证据",
  experiment: "实验结果",
  rationale: "设计依据",
  document: "科研文档",
  claim: "可验证结论",
  query_scope: "查询范围",
};

const reasonLabels: Record<string, string> = {
  ambiguous_scope_or_version: "查询范围或版本不明确",
  underspecified_follow_up: "追问缺少必要上下文",
  conversation_snapshot_mismatch: "会话上下文不匹配",
  context_revision_mismatch: "会话上下文已经更新",
  parent_turn_mismatch: "父交互与当前上下文不一致",
  conversation_snapshot_required: "需要最新的会话上下文",
  deadline_exceeded: "已达到本次延迟预算",
  citation_verification_failed: "声明与引用校验失败",
  generator_failure: "生成器失败，已退回仅检索",
};

type RequestState = "idle" | "running";
type QueryError = {
  kind: "cancelled" | "timeout" | "request";
  message: string;
};
type QueryFeedback = "helpful" | "needs_correction";
type QueryHistoryEntry = {
  id: string;
  question: string;
  response: TrustedQueryResponse;
};

const maxHistoryEntries = 8;

function createInteractionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `turn-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function presentRole(role: string): string {
  return roleLabels[role] || safeMetadataValue(role.replaceAll("_", " "));
}

function presentReason(reason?: string | null): string {
  if (!reason) return "当前可信条件未满足";
  return (
    reasonLabels[reason] ||
    safeMetadataValue(reason, "当前证据或权限条件不满足")
  );
}

function sourceLabel(source: string): string {
  return (
    querySources.find((entry) => entry.key === source)?.label ||
    safeMetadataValue(source)
  );
}

function citationAnchor(id: string): string {
  return `citation-${id.replace(/[^a-z0-9_-]/gi, "-")}`;
}

function graphHref(
  projectId: string,
  source: string,
  entityId: string,
): string {
  const board = querySources.some((item) => item.key === source)
    ? source
    : "overview";
  const query = new URLSearchParams({ board, focus: entityId });
  return `/p/${encodeURIComponent(projectId)}/map?${query.toString()}`;
}

function sourceReviewHref(projectId: string, source: string): string {
  const base = `/p/${encodeURIComponent(projectId)}`;
  if (source === "code" || source === "codex") {
    return `${base}/map?${new URLSearchParams({ board: source }).toString()}`;
  }
  if (source === "experiment") return `${base}/experiments`;
  if (source === "document" || source === "notebook")
    return `${base}/documents`;
  return `${base}/overview`;
}

function SafeAnswerLink({ children }: ComponentProps<"a">) {
  return <span className="trusted-answer__link-label">{children}</span>;
}

function QueryResultView({
  response,
  projectId,
  clarificationAnswer,
  setClarificationAnswer,
  onClarification,
  pending,
}: {
  response: TrustedQueryResponse;
  projectId: string;
  clarificationAnswer: string;
  setClarificationAnswer: (value: string) => void;
  onClarification: () => void;
  pending: boolean;
}) {
  const mode = queryPresentationMode(response);
  const meta = modeMeta[mode];
  const ModeIcon = meta.icon;
  const sourceStatus = querySourceStatus(response);
  const evidence = queryEvidence(response);
  const primaryEvidence = evidence.filter(
    (item) => item.evidence_role !== "counter",
  );
  const counters = evidence.filter((item) => item.evidence_role === "counter");
  const citations = queryCitations(response);
  const claimCitationMap = queryClaimCitationMap(response);
  const verification = queryClaimVerification(response);
  const missingRoles = queryMissingRoles(response);
  const satisfiedRoles = querySatisfiedRoles(response);
  const requiredRoles = queryRequiredRoles(response);
  const staleRoles = queryStaleRoles(response);
  const nextActions = queryNextActions(response);
  const routing = queryEngineRouting(response);
  const reasons = queryReasons(response);
  const fallbacks = queryFallbacks(response);
  const blockers = queryBlockers(response);
  const transitions = queryTransitions(response);
  const watermarks = queryWatermarks(response);
  const correctiveRounds = queryCorrectiveRounds(response);
  const scope = queryScope(response);
  const clarification =
    response.interaction?.clarification || response.answer.clarification;
  const repositories = scope.repositories?.length
    ? scope.repositories.map((item) => item.name || item.id).filter(Boolean)
    : scope.repository_ids || [];
  const answerText = sanitizeDisplayText(
    response.answer.text ||
      (mode === "refusal"
        ? "系统没有返回可安全呈现的回答。"
        : "系统没有返回回答正文，请查看来源状态与证据覆盖。"),
  );
  const clarificationQuestions = clarification?.questions?.filter(Boolean) || [
    answerText,
  ];

  return (
    <div className="trusted-result" aria-live="polite">
      <header
        className={`trusted-result__status trusted-result__status--${mode}`}
      >
        <span>
          <ModeIcon size={19} />
        </span>
        <div>
          <strong>{meta.label}</strong>
          <p>{meta.description}</p>
        </div>
        {response.interaction?.deadline_exceeded ? (
          <Status value="partial">
            <Clock3 size={13} /> 达到时限
          </Status>
        ) : null}
      </header>

      <details className="query-audit-disclosure">
        <summary>
          <ChevronDown size={16} />
          查询规划、来源状态与审计范围
        </summary>
        {response.query_understanding ? (
          <section className="query-intelligence" aria-label="查询理解与检索分解">
          <header>
            <div>
              <span>QUERY UNDERSTANDING</span>
              <h2>{response.query_understanding.strategy === "llm_structured_hybrid" ? "结构化模型规划" : "本地统计理解"}</h2>
            </div>
            <Status value={response.query_understanding.fallback_reason ? "partial" : "complete"}>
              置信度 {Math.round(response.query_understanding.intent_confidence * 100)}%
            </Status>
          </header>
          <div className="query-intelligence__grid">
            <div>
              <span>意图</span>
              <strong>{safeMetadataValue(response.query_understanding.intent)}</strong>
            </div>
            <div>
              <span>模型状态</span>
              <strong>{response.llm_provider?.configured ? `${safeMetadataValue(response.llm_provider.model)} 已启用` : "未配置 · 本地算法兜底"}</strong>
            </div>
            <div>
              <span>实体</span>
              <strong>{response.query_understanding.entities.map((item) => safeMetadataValue(item)).join("、") || "未显式识别"}</strong>
            </div>
            <div>
              <span>来源建议</span>
              <strong>{response.query_understanding.source_hints.map(sourceLabel).join("、")}</strong>
            </div>
          </div>
          <div className="query-intelligence__algorithm">
            <div>
              <span>算法链</span>
              <strong>
                {response.query_understanding.algorithm.encoder === "structured_llm_plus_tfidf_prior"
                  ? "结构化 LLM × 本地 TF-IDF 先验校准"
                  : "TF-IDF 词/字符 n-gram × 质心 Softmax"}
              </strong>
              <small>
                不确定性 {Math.round(response.query_understanding.algorithm.uncertainty * 100)}%
                {" · "}复杂度 {Math.round(response.query_understanding.algorithm.complexity * 100)}%
              </small>
            </div>
            <div className="query-intelligence__posterior" aria-label="意图概率分布">
              {Object.entries(response.query_understanding.intent_distribution)
                .slice(0, 3)
                .map(([intent, probability]) => (
                  <div key={intent}>
                    <label>
                      <span>{safeMetadataValue(intent)}</span>
                      <b>{Math.round(probability * 100)}%</b>
                    </label>
                    <i style={{ "--query-probability": `${Math.max(2, probability * 100)}%` } as CSSProperties} />
                  </div>
                ))}
            </div>
          </div>
          <ol>
            {response.query_understanding.subqueries.map((item, index) => (
              <li key={`${index}-${item}`}><b>Q{index + 1}</b><span>{sanitizeDisplayText(item)}</span></li>
            ))}
          </ol>
          {response.query_understanding.fallback_reason ? (
            <p>结构化模型规划不可用，本轮已安全降级；ACL、版本与证据核验未放宽。</p>
          ) : null}
          </section>
        ) : null}

      {mode === "clarification" ? (
        <section className="clarification-card">
          <div>
            <span>CLARIFICATION</span>
            <h2>请补充以下查询条件</h2>
            <ol className="clarification-questions">
              {clarificationQuestions.map((question, index) => (
                <li key={`${index}-${question}`}>
                  {sanitizeDisplayText(question, "请补充查询范围")}
                </li>
              ))}
            </ol>
            <p>{presentReason(clarification?.reason)}</p>
          </div>
          <label>
            <span>补充说明（按问题顺序回答）</span>
            <textarea
              aria-label="补充说明"
              value={clarificationAnswer}
              onChange={(event) => setClarificationAnswer(event.target.value)}
              placeholder="1. …&#10;2. …"
              rows={3}
            />
          </label>
          <Button
            variant="primary"
            onClick={onClarification}
            disabled={!clarificationAnswer.trim() || pending}
          >
            继续查询
          </Button>
        </section>
      ) : (
        <article className="trusted-answer">
          <div className="trusted-answer__markdown">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{ a: SafeAnswerLink }}
            >
              {answerText}
            </ReactMarkdown>
          </div>
          {response.answer.refusal ? (
            <p className="trusted-answer__reason">
              <Ban size={15} />
              {presentReason(response.answer.refusal_reason)}
            </p>
          ) : null}
        </article>
      )}

        <section className="query-metadata-grid" aria-label="查询范围和交互信息">
        <div>
          <span>项目</span>
          <strong>{safeMetadataValue(scope.project_id)}</strong>
        </div>
        <div>
          <span>仓库范围</span>
          <strong>
            {repositories.length
              ? repositories.map((item) => safeMetadataValue(item)).join("、")
              : "项目全部仓库"}
          </strong>
        </div>
        <div>
          <span>版本 / 时间</span>
          <strong>
            {safeMetadataValue(
              scope.commit || scope.branch || scope.as_of,
              "当前已发布范围",
            )}
          </strong>
        </div>
        <div>
          <span>Interaction ID</span>
          <strong>
            {safeMetadataValue(response.client_turn_id || response.query_id)}
          </strong>
        </div>
        <div>
          <span>Parent Interaction</span>
          <strong>{safeMetadataValue(response.parent_turn_id, "首轮")}</strong>
        </div>
        <div>
          <span>Trace</span>
          <strong>
            {safeMetadataValue(response.trace_id || response.query_id)}
          </strong>
        </div>
        </section>

        {routing.length ? (
          <section className="query-section">
          <header>
            <div>
              <span>ENGINE ROUTING</span>
              <h2>请求与实际执行引擎</h2>
            </div>
          </header>
          <div className="engine-routing-list">
            {routing.map((item) => (
              <article key={item.source}>
                <strong>
                  {item.source === "global"
                    ? "全局融合"
                    : sourceLabel(item.source)}
                </strong>
                <div>
                  <span>请求 {safeMetadataValue(item.requested, "默认")}</span>
                  <ArrowRight size={14} />
                  <span>执行 {safeMetadataValue(item.selected, "未报告")}</span>
                </div>
                {item.fallback ? (
                  <small>Fallback · {safeMetadataValue(item.fallback)}</small>
                ) : null}
                {item.reason ? (
                  <small>Reason · {presentReason(item.reason)}</small>
                ) : null}
                {item.blockers?.map((blocker) => (
                  <small key={blocker}>
                    Blocker · {safeMetadataValue(blocker)}
                  </small>
                ))}
              </article>
            ))}
          </div>
          </section>
        ) : null}

        <section className="query-section">
        <header>
          <div>
            <span>SOURCE STATUS</span>
            <h2>来源执行状态</h2>
          </div>
          <b>{Object.keys(sourceStatus).length} SOURCES</b>
        </header>
        {Object.keys(sourceStatus).length ? (
          <div className="source-status-grid">
            {Object.entries(sourceStatus).map(([source, item]) => (
              <article
                className={`source-status source-status--${item.status}`}
                key={source}
              >
                <div>
                  <strong>{sourceLabel(source)}</strong>
                  <Status value={item.status}>
                    {sourceStatusLabels[item.status] || "状态未知"}
                  </Status>
                </div>
                <dl>
                  <div>
                    <dt>候选</dt>
                    <dd>{item.candidate_count ?? 0}</dd>
                  </div>
                  <div>
                    <dt>耗时</dt>
                    <dd>
                      {item.latency_ms == null
                        ? "—"
                        : `${Math.round(item.latency_ms)} ms`}
                    </dd>
                  </div>
                </dl>
                <small title={safeMetadataValue(item.watermark)}>
                  Watermark · {safeMetadataValue(item.watermark, "未提供")}
                </small>
                {item.reason ? (
                  <small>Reason · {presentReason(item.reason)}</small>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <p className="query-section__empty">后端未返回来源执行状态。</p>
        )}
        </section>
      </details>

      {requiredRoles.length ||
      satisfiedRoles.length ||
      missingRoles.length ||
      staleRoles.length ? (
        <section className="query-section">
          <header>
            <div>
              <span>ROLE COVERAGE</span>
              <h2>证据角色覆盖</h2>
            </div>
          </header>
          <div className="role-coverage">
            {requiredRoles.map((role) => (
              <span className="is-required" key={`r-${role}`}>
                {presentRole(role)} · 必需
              </span>
            ))}
            {satisfiedRoles.map((role) => (
              <span className="is-satisfied" key={`s-${role}`}>
                <CheckCircle2 size={14} /> {presentRole(role)}
              </span>
            ))}
            {missingRoles.map((role) => (
              <span className="is-missing" key={`m-${role}`}>
                <AlertTriangle size={14} /> {presentRole(role)}
              </span>
            ))}
            {staleRoles.map((role) => (
              <span className="is-stale" key={`stale-${role}`}>
                <Clock3 size={14} /> {presentRole(role)} · 过期
              </span>
            ))}
          </div>
        </section>
      ) : null}

      {primaryEvidence.length ? (
        <section className="query-section">
          <header>
            <div>
              <span>EVIDENCE</span>
              <h2>已返回证据</h2>
            </div>
            <b>{primaryEvidence.length}</b>
          </header>
          <div className="evidence-card-list">
            {primaryEvidence.map((item) => (
              <article key={`${item.evidence_role}-${item.entity_id}`}>
                <span>
                  {item.evidence_role === "verified"
                    ? "VERIFIED"
                    : "SUPPORTING"}
                </span>
                <strong>{sanitizeDisplayText(item.title, "未命名证据")}</strong>
                <p>{sanitizeDisplayText(item.snippet, "未提供摘要")}</p>
                <small>
                  {sourceLabel(item.source)} ·{" "}
                  {safeMetadataValue(item.version || item.locator)}
                </small>
                <a href={graphHref(projectId, item.source, item.entity_id)}>
                  <Link2 size={13} /> 在关系图谱中审阅
                </a>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {counters.length ? (
        <section className="query-section">
          <header>
            <div>
              <span>COUNTER EVIDENCE</span>
              <h2>反证与冲突</h2>
            </div>
            <b>{counters.length}</b>
          </header>
          <div className="counter-evidence-list">
            {counters.map((item) => (
              <article key={item.entity_id}>
                <strong>{sanitizeDisplayText(item.title, "未命名反证")}</strong>
                <p>{sanitizeDisplayText(item.snippet, "未提供摘要")}</p>
                <small>
                  {sourceLabel(item.source)} ·{" "}
                  {safeMetadataValue(item.version || item.locator)}
                </small>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {claimCitationMap.length ? (
        <section className="query-section">
          <header>
            <div>
              <span>CLAIM MAP</span>
              <h2>声明与引用映射</h2>
            </div>
            <b>{claimCitationMap.length} CLAIMS</b>
          </header>
          <ol className="claim-map">
            {claimCitationMap.map((claim, index) => (
              <li key={`${index}-${claim.claim}`}>
                <p>{claim.claim}</p>
                <div>
                  {claim.citationIds.map((id) => (
                    <a key={id} href={`#${citationAnchor(id)}`}>
                      <Link2 size={13} /> 跳转 {safeMetadataValue(id)}
                    </a>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {citations.length || verification ? (
        <section className="query-section">
          <header>
            <div>
              <span>CLAIM CITATIONS</span>
              <h2>回答引用</h2>
            </div>
            {verification ? (
              <Status value={verification.supported ? "complete" : "partial"}>
                {verification.supported ? "已校验" : "未完全校验"}
              </Status>
            ) : null}
          </header>
          {verification ? (
            <div className="claim-verification">
              <strong>
                {verification.supported_claim_count ?? 0} /{" "}
                {verification.claim_count ?? 0} 条声明通过
              </strong>
              <span>
                {safeMetadataValue(
                  verification.verifier_version,
                  "校验器版本未报告",
                )}
              </span>
              {Object.entries(verification.reason_counts || {}).map(
                ([reason, count]) => (
                  <small key={reason}>
                    {presentReason(reason)} · {count}
                  </small>
                ),
              )}
            </div>
          ) : null}
          {citations.length ? (
            <div className="claim-citations">
              {citations.map(({ id, citation }) => (
                <article id={citationAnchor(id)} tabIndex={-1} key={id}>
                  <b>{safeMetadataValue(id)}</b>
                  <div>
                    <strong>{sourceLabel(citation.source)}</strong>
                    <span>
                      {safeMetadataValue(citation.locator, "来源位置未提供")}
                    </span>
                  </div>
                  <small>
                    {safeMetadataValue(citation.version, "当前版本")}
                  </small>
                  <a
                    href={graphHref(
                      projectId,
                      citation.source,
                      citation.entity_id,
                    )}
                  >
                    图谱定位
                  </a>
                </article>
              ))}
            </div>
          ) : (
            <p className="query-section__empty">回答未返回可跳转引用。</p>
          )}
        </section>
      ) : null}

      <section className="query-section">
        <header>
          <div>
            <span>INTERACTION AUDIT</span>
            <h2>交互审计链</h2>
          </div>
          <b>{correctiveRounds} CORRECTIVE ROUNDS</b>
        </header>
        <div className="interaction-audit">
          <div>
            <strong>Reason</strong>
            <span>
              {reasons.length
                ? reasons.map((item) => presentReason(item)).join(" · ")
                : "未报告"}
            </span>
          </div>
          <div>
            <strong>Fallback</strong>
            <span>
              {fallbacks.length
                ? fallbacks.map((item) => safeMetadataValue(item)).join(" · ")
                : "未使用"}
            </span>
          </div>
          <div>
            <strong>Blocker</strong>
            <span>
              {blockers.length
                ? blockers.map((item) => safeMetadataValue(item)).join(" · ")
                : "无已报告阻塞"}
            </span>
          </div>
          <div>
            <strong>Watermark</strong>
            <span>
              {watermarks.length
                ? watermarks
                    .map(
                      (item) =>
                        `${sourceLabel(item.source)}: ${safeMetadataValue(item.value)}`,
                    )
                    .join(" · ")
                : "未报告"}
            </span>
          </div>
        </div>
        {transitions.length ? (
          <ol className="interaction-transitions" aria-label="交互状态迁移">
            {transitions.map((transition, index) => (
              <li key={`${index}-${transition}`}>
                <span>{index + 1}</span>
                {safeMetadataValue(transition)}
              </li>
            ))}
          </ol>
        ) : (
          <p className="query-section__empty">后端未返回状态迁移记录。</p>
        )}
      </section>

      {nextActions.length ? (
        <section className="query-section">
          <header>
            <div>
              <span>NEXT ACTIONS</span>
              <h2>后续动作</h2>
            </div>
          </header>
          <ol className="next-actions">
            {nextActions.map((action) => (
              <li key={action}>{sanitizeDisplayText(action, "未命名动作")}</li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}

export function TrustedQueryPanel({
  projects,
  initialProjectId,
  initialQuestion,
}: {
  projects: Project[];
  initialProjectId?: string;
  initialQuestion?: string;
}) {
  const activeProjects = useMemo(
    () => projects.filter((project) => project.status !== "archived"),
    [projects],
  );
  const [projectId, setProjectId] = useState(initialProjectId || "");
  const [question, setQuestion] = useState(initialQuestion || "");
  const [allowGeneration, setAllowGeneration] = useState(true);
  const [selectedSources, setSelectedSources] = useState<QuerySource[]>(() =>
    querySources.map((source) => source.key),
  );
  const [latencyBudget, setLatencyBudget] = useState(15_000);
  const [repositoryScope, setRepositoryScope] = useState("");
  const [commitScope, setCommitScope] = useState("");
  const [asOfScope, setAsOfScope] = useState("");
  const [intent, setIntent] = useState<"" | QueryIntent>("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [globalEngine, setGlobalEngine] = useState<"" | "v1" | "v2">("");
  const [engineOverrides, setEngineOverrides] = useState<QueryEngineOverrides>(
    {},
  );
  const [requestState, setRequestState] = useState<RequestState>("idle");
  const [response, setResponse] = useState<TrustedQueryResponse | null>(null);
  const [error, setError] = useState<QueryError | null>(null);
  const [clarificationAnswer, setClarificationAnswer] = useState("");
  const [correctionDraft, setCorrectionDraft] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [history, setHistory] = useState<QueryHistoryEntry[]>([]);
  const [persistedHistory, setPersistedHistory] = useState<QueryHistoryItem[]>(
    [],
  );
  const [selectedHistory, setSelectedHistory] =
    useState<QueryHistoryItem | null>(null);
  const [historyState, setHistoryState] = useState<
    "idle" | "loading" | "error"
  >("idle");
  const [plan, setPlan] = useState<QueryPlanPreview | null>(null);
  const [planState, setPlanState] = useState<"idle" | "loading" | "error">(
    "idle",
  );
  const [planStale, setPlanStale] = useState(false);
  const [feedbackByTurn, setFeedbackByTurn] = useState<
    Record<string, QueryFeedback>
  >({});
  const controllerRef = useRef<AbortController | null>(null);
  const timeoutRef = useRef<number | null>(null);
  const requestSerialRef = useRef(0);
  const planSerialRef = useRef(0);

  useEffect(
    () => () => {
      controllerRef.current?.abort();
      if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current);
    },
    [],
  );

  const pending = requestState === "running";
  const projectIsValid = activeProjects.some(
    (project) => project.id === projectId,
  );
  const repositoryIds = useMemo(
    () =>
      repositoryScope
        .split(/[\s,，]+/)
        .map((item) => item.trim())
        .filter(Boolean),
    [repositoryScope],
  );
  const normalizedAsOf = useMemo(() => {
    if (!asOfScope) return undefined;
    const parsed = new Date(asOfScope);
    return Number.isNaN(parsed.getTime())
      ? asOfScope.trim()
      : parsed.toISOString();
  }, [asOfScope]);

  const loadPersistedHistory = async (targetProjectId = projectId) => {
    if (!targetProjectId) return;
    setHistoryState("loading");
    try {
      const result = await api.query.history(targetProjectId, 20);
      setPersistedHistory(result.items || []);
      setSelectedHistory(null);
      setHistoryState("idle");
    } catch {
      setPersistedHistory([]);
      setSelectedHistory(null);
      setHistoryState("error");
    }
  };

  useEffect(() => {
    if (!initialProjectId || !projectIsValid) return;
    void loadPersistedHistory(initialProjectId);
    // Initial project is a route authority; later project changes are user initiated.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialProjectId, projectIsValid]);

  const stopPending = (
    errorKind?: "cancelled" | "timeout",
    message?: string,
  ) => {
    requestSerialRef.current += 1;
    if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
    const controller = controllerRef.current;
    controllerRef.current = null;
    setRequestState("idle");
    controller?.abort();
    if (errorKind) {
      setError({
        kind: errorKind,
        message:
          message ||
          (errorKind === "timeout"
            ? "客户端已按延迟预算停止等待；服务端可能仍在收束请求。"
            : "本次查询已取消，没有用旧响应覆盖当前界面。"),
      });
    }
  };

  const resetConversation = () => {
    stopPending();
    setResponse(null);
    setError(null);
    setConversationId(null);
    setClarificationAnswer("");
    setCorrectionDraft("");
    setHistory([]);
    setSelectedHistory(null);
    planSerialRef.current += 1;
    setPlan(null);
    setPlanState("idle");
    setPlanStale(false);
    setFeedbackByTurn({});
  };

  const invalidatePlanPreview = () => {
    planSerialRef.current += 1;
    if (plan || planState === "loading") setPlanStale(true);
    setPlan(null);
    setPlanState("idle");
  };

  useEffect(() => {
    if (!activeProjects.length || !projectId || projectIsValid) return;
    stopPending();
    setProjectId("");
    setResponse(null);
    setConversationId(null);
    setHistory([]);
    setPersistedHistory([]);
    setSelectedHistory(null);
    planSerialRef.current += 1;
    setPlan(null);
    setPlanState("idle");
    setPlanStale(false);
  }, [activeProjects.length, projectId, projectIsValid]);

  const queryInput = (requestQuestion: string) => ({
    question: requestQuestion,
    project_id: projectId,
    sources: selectedSources,
    repository_ids: repositoryIds,
    commit: commitScope.trim() || undefined,
    as_of: normalizedAsOf,
    intent: intent || undefined,
    latency_budget_ms: latencyBudget,
    allow_generation: allowGeneration,
    max_evidence: 12,
    answer_format: allowGeneration
      ? ("concise" as const)
      : ("evidence_only" as const),
    global_engine: globalEngine || undefined,
    engine_overrides: engineOverrides,
  });

  const previewPlan = async () => {
    if (!projectIsValid || !selectedSources.length || !question.trim()) return;
    const serial = ++planSerialRef.current;
    setPlanState("loading");
    setPlan(null);
    setPlanStale(false);
    try {
      const nextPlan = await api.query.preview(queryInput(question.trim()));
      if (serial !== planSerialRef.current) return;
      setPlan(nextPlan);
      setPlanState("idle");
    } catch {
      if (serial !== planSerialRef.current) return;
      setPlanState("error");
    }
  };

  const runQuery = async ({
    clarification,
    correction,
  }: {
    clarification?: string;
    correction?: string;
  } = {}) => {
    const requestedText = correction || clarification || question;
    if (
      controllerRef.current ||
      !projectIsValid ||
      !selectedSources.length ||
      !requestedText.trim()
    ) {
      return;
    }

    const controller = new AbortController();
    const serial = ++requestSerialRef.current;
    const interactionId = createInteractionId();
    const currentConversation =
      conversationId || `conversation-${createInteractionId()}`;
    const canResumeSnapshot = Boolean(response?.conversation_snapshot);
    const parentInteractionId = canResumeSnapshot
      ? response?.client_turn_id || undefined
      : undefined;
    const requestQuestion = correction
      ? `请纠正上一轮回答：${correction.trim()}`
      : clarification && !canResumeSnapshot
        ? `${question}\n\n补充范围：${clarification}`
        : question;
    const historyQuestion = correction
      ? `纠错：${correction.trim()}`
      : clarification
        ? `澄清：${clarification.trim()}`
        : question.trim();
    controllerRef.current = controller;
    setRequestState("running");
    setError(null);
    if (!conversationId) setConversationId(currentConversation);
    timeoutRef.current = window.setTimeout(() => {
      if (serial !== requestSerialRef.current) return;
      stopPending("timeout");
    }, latencyBudget + 1_500);

    try {
      const result = await api.query.run(
        {
          ...queryInput(requestQuestion),
          clarification_answer:
            clarification && canResumeSnapshot ? clarification : undefined,
          interaction_id: interactionId,
          parent_interaction_id: parentInteractionId,
          conversation_id: currentConversation,
          context_revision: canResumeSnapshot
            ? response?.context_revision
            : null,
          conversation_snapshot: canResumeSnapshot
            ? response?.conversation_snapshot
            : null,
          clarification_policy: "auto",
        },
        controller.signal,
      );
      if (serial !== requestSerialRef.current) return;
      setResponse(result);
      setHistory((current) =>
        [
          ...current,
          {
            id:
              result.client_turn_id || result.interaction_id || result.query_id,
            question: historyQuestion,
            response: result,
          },
        ].slice(-maxHistoryEntries),
      );
      setClarificationAnswer("");
      setCorrectionDraft("");
    } catch (requestError) {
      if (serial !== requestSerialRef.current) return;
      if (!controller.signal.aborted) {
        setError({ kind: "request", message: safeClientError(requestError) });
      }
    } finally {
      if (timeoutRef.current != null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
      if (serial === requestSerialRef.current) {
        controllerRef.current = null;
        setRequestState("idle");
      }
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void runQuery();
  };

  const cancel = () => {
    if (!controllerRef.current) return;
    stopPending("cancelled");
  };

  return (
    <div className="trusted-query">
      <form className="trusted-query__composer" onSubmit={submit}>
        <div className="trusted-query__prompt">
          <Search size={21} />
          <textarea
            value={question}
            onChange={(event) => {
              setQuestion(event.target.value);
              invalidatePlanPreview();
            }}
            placeholder="询问一个需要跨代码、会话、实验或文档核验的研究问题"
            aria-label="可信查询问题"
            rows={2}
            autoFocus
          />
          <select
            value={projectId}
            onChange={(event) => {
              resetConversation();
              invalidatePlanPreview();
              setPersistedHistory([]);
              setHistoryState("idle");
              setProjectId(event.target.value);
            }}
            aria-label="可信查询项目"
          >
            <option value="">
              {activeProjects.length ? "请选择项目（必选）" : "没有可用项目"}
            </option>
            {activeProjects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
          {pending ? (
            <Button type="button" variant="danger" onClick={cancel}>
              <CircleStop size={16} /> 取消
            </Button>
          ) : (
            <Button
              type="submit"
              variant="primary"
              disabled={
                !question.trim() || !projectIsValid || !selectedSources.length
              }
            >
              <Sparkles size={16} /> 查询
            </Button>
          )}
        </div>

        <fieldset className="query-source-selection">
          <legend>检索来源（至少选择一项）</legend>
          {querySources.map((source) => (
            <label key={source.key}>
              <input
                type="checkbox"
                checked={selectedSources.includes(source.key)}
                onChange={() => {
                  resetConversation();
                  invalidatePlanPreview();
                  setSelectedSources((current) =>
                    current.includes(source.key)
                      ? current.filter((item) => item !== source.key)
                      : [...current, source.key],
                  );
                }}
              />
              <span>{source.label}</span>
            </label>
          ))}
          <small>
            {selectedSources.length} / {querySources.length} 个来源已选择
          </small>
        </fieldset>

        <div className="trusted-query__controls">
          <label className="query-toggle">
            <input
              type="checkbox"
              checked={allowGeneration}
              onChange={(event) => {
                setAllowGeneration(event.target.checked);
                invalidatePlanPreview();
              }}
            />
            <span>允许 grounded generation</span>
          </label>
          <label>
            <Clock3 size={14} />
            <span>延迟预算</span>
            <select
              aria-label="延迟预算"
              value={latencyBudget}
              onChange={(event) => {
                setLatencyBudget(Number(event.target.value));
                invalidatePlanPreview();
              }}
            >
              <option value={5_000}>5 秒</option>
              <option value={15_000}>15 秒</option>
              <option value={30_000}>30 秒</option>
              <option value={60_000}>60 秒</option>
            </select>
          </label>
          <button
            type="button"
            className={advancedOpen ? "is-open" : ""}
            onClick={() => setAdvancedOpen((open) => !open)}
            aria-expanded={advancedOpen}
          >
            来源引擎
            <ChevronDown size={15} />
          </button>
          <button
            type="button"
            onClick={() => void previewPlan()}
            disabled={
              !question.trim() ||
              !projectIsValid ||
              !selectedSources.length ||
              planState === "loading"
            }
          >
            <ShieldCheck size={14} />
            {planState === "loading"
              ? "正在解析计划"
              : planStale
                ? "重新预览执行计划"
                : "预览执行计划"}
          </button>
          {response || history.length ? (
            <button type="button" onClick={resetConversation}>
              <RotateCcw size={14} />
              新交互链
            </button>
          ) : null}
        </div>

        {advancedOpen ? (
          <div className="trusted-query__advanced">
            <fieldset className="query-scope-controls">
              <legend>查询范围（显式、可审阅）</legend>
              <label>
                <span>仓库 ID</span>
                <input
                  aria-label="查询仓库范围"
                  value={repositoryScope}
                  onChange={(event) => {
                    setRepositoryScope(event.target.value);
                    invalidatePlanPreview();
                  }}
                  placeholder="repo-one, repo-two"
                />
              </label>
              <label>
                <span>Commit</span>
                <input
                  aria-label="查询提交版本"
                  value={commitScope}
                  onChange={(event) => {
                    setCommitScope(event.target.value);
                    invalidatePlanPreview();
                  }}
                  placeholder="完整提交哈希（可选）"
                />
              </label>
              <label>
                <span>As-of</span>
                <input
                  type="datetime-local"
                  aria-label="查询截止时间"
                  value={asOfScope}
                  onChange={(event) => {
                    setAsOfScope(event.target.value);
                    invalidatePlanPreview();
                  }}
                />
              </label>
              <label>
                <span>意图</span>
                <select
                  aria-label="查询意图"
                  value={intent}
                  onChange={(event) => {
                    setIntent(event.target.value as "" | QueryIntent);
                    invalidatePlanPreview();
                  }}
                >
                  <option value="">自动解析</option>
                  {queryIntents.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            </fieldset>
            <fieldset className="engine-overrides">
              <legend>Engine Override（缺省保持 V1）</legend>
              <label>
                <span>全局融合</span>
                <select
                  aria-label="全局融合引擎"
                  value={globalEngine}
                  onChange={(event) => {
                    setGlobalEngine(event.target.value as "" | "v1" | "v2");
                    invalidatePlanPreview();
                  }}
                >
                  <option value="">默认 V1</option>
                  <option value="v1">V1</option>
                  <option value="v2">V2（证据门控）</option>
                </select>
              </label>
              {querySources.map((source) => (
                <label key={source.key}>
                  <span>{source.label}</span>
                  <select
                    aria-label={`${source.label}引擎`}
                    value={engineOverrides[source.key] || ""}
                    onChange={(event) => {
                      const version = event.target.value as "" | "v1" | "v2";
                      setEngineOverrides((current) => {
                        const next = { ...current };
                        if (version) next[source.key] = version;
                        else delete next[source.key];
                        return next;
                      });
                      invalidatePlanPreview();
                    }}
                  >
                    <option value="">默认</option>
                    <option value="v1">V1</option>
                    <option value="v2">V2</option>
                  </select>
                </label>
              ))}
            </fieldset>
          </div>
        ) : null}
      </form>

      {planStale ? (
        <section
          className="project-clarification"
          role="status"
          aria-live="polite"
        >
          <AlertTriangle size={18} />
          <div>
            <strong>执行计划已失效</strong>
            <p>查询输入已变化，请重新预览执行计划。</p>
          </div>
        </section>
      ) : null}

      {planState === "error" ? (
        <section className="query-error" role="alert">
          <AlertTriangle size={18} />
          <div>
            <strong>执行计划不可用</strong>
            <p>没有执行检索；请检查范围后重试。</p>
          </div>
        </section>
      ) : null}

      {plan ? (
        <section className="query-plan" aria-label="查询执行计划">
          <header>
            <div>
              <span>PLAN PREVIEW</span>
              <h2>执行前审阅</h2>
            </div>
            <Status value={plan.retrieval_performed ? "partial" : "complete"}>
              零检索
            </Status>
          </header>
          <div className="query-plan__summary">
            <div>
              <span>意图</span>
              <strong>{safeMetadataValue(plan.intent)}</strong>
            </div>
            <div>
              <span>仓库</span>
              <strong>
                {plan.resolved_scope.repository_ids?.length
                  ? plan.resolved_scope.repository_ids
                      .map((item) => safeMetadataValue(item))
                      .join("、")
                  : "项目范围"}
              </strong>
            </div>
            <div>
              <span>版本</span>
              <strong>
                {safeMetadataValue(
                  plan.resolved_scope.commit || plan.resolved_scope.as_of,
                  "当前发布范围",
                )}
              </strong>
            </div>
            <div>
              <span>预算</span>
              <strong>
                {plan.budgets.deadline_ms
                  ? `${plan.budgets.deadline_ms / 1000}s`
                  : "默认"}{" "}
                · {plan.budgets.max_evidence} evidence
              </strong>
            </div>
          </div>
          {plan.query_understanding ? (
            <section className="query-plan__understanding">
              <div>
                <span>理解算法</span>
                <strong>{plan.query_understanding.strategy === "llm_structured_hybrid" ? "结构化模型规划" : "本地统计预规划"}</strong>
              </div>
              <ol>
                {plan.query_understanding.subqueries.map((item, index) => (
                  <li key={`${index}-${item}`}><b>Q{index + 1}</b><span>{sanitizeDisplayText(item)}</span></li>
                ))}
              </ol>
            </section>
          ) : null}
          <ol className="query-plan__waves">
            {plan.source_waves.map((wave) => (
              <li key={wave.wave}>
                <b>Wave {wave.wave}</b>
                <span>
                  {wave.sources.map(sourceLabel).join("、") || "无来源"}
                </span>
                <small>
                  {wave.execution_mode} ·{" "}
                  {Object.values(wave.budgets).reduce(
                    (total, value) => total + value,
                    0,
                  )}{" "}
                  ms budget
                </small>
              </li>
            ))}
          </ol>
          <footer>
            <span>
              所需角色：
              {plan.required_roles.map(presentRole).join("、") || "未指定"}
            </span>
            <code>{safeMetadataValue(plan.plan_digest)}</code>
          </footer>
        </section>
      ) : null}

      {!projectId && question.trim() ? (
        <section className="project-clarification" role="status">
          <AlertTriangle size={18} />
          <div>
            <strong>需要澄清：请选择项目</strong>
            <p>查询不会静默选择第一个项目。请在上方明确项目后再提交。</p>
          </div>
        </section>
      ) : null}

      {!selectedSources.length ? (
        <section className="project-clarification" role="alert">
          <AlertTriangle size={18} />
          <div>
            <strong>请选择至少一个检索来源</strong>
            <p>来源范围会随请求显式发送，不使用隐藏的全来源默认值。</p>
          </div>
        </section>
      ) : null}

      {pending ? (
        <div className="trusted-query__progress" role="status">
          <span />
          <div>
            <strong>正在执行可信查询</strong>
            <small>
              请求可取消；服务端同时遵守 {latencyBudget / 1_000} 秒延迟预算。
            </small>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className={`query-error query-error--${error.kind}`} role="alert">
          <AlertTriangle size={18} />
          <div>
            <strong>
              {error.kind === "timeout"
                ? "查询超时"
                : error.kind === "cancelled"
                  ? "查询已取消"
                  : "查询失败"}
            </strong>
            <p>{error.message}</p>
          </div>
          {error.kind !== "cancelled" ? (
            <Button
              type="button"
              variant="secondary"
              onClick={() => void runQuery()}
            >
              重试
            </Button>
          ) : null}
        </div>
      ) : null}

      {history.length ? (
        <section className="query-history" aria-label="有界查询会话历史">
          <header>
            <div>
              <span>SESSION HISTORY</span>
              <h2>会话链（最近 {maxHistoryEntries} 轮）</h2>
            </div>
            <b>
              {history.length} / {maxHistoryEntries}
            </b>
          </header>
          <ol>
            {history.map((entry, index) => (
              <li key={`${entry.id}-${index}`}>
                <span>{index + 1}</span>
                <div>
                  <strong>
                    {sanitizeDisplayText(entry.question, "未命名查询")}
                  </strong>
                  <small>
                    {safeMetadataValue(entry.id)} ·{" "}
                    {queryPresentationMode(entry.response).toUpperCase()}
                  </small>
                </div>
                {feedbackByTurn[entry.id] ? (
                  <em>
                    {feedbackByTurn[entry.id] === "helpful"
                      ? "有帮助"
                      : "待纠正"}
                  </em>
                ) : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {projectId ? (
        <section
          className="query-history query-history--persisted"
          aria-label="持久查询审计历史"
        >
          <header>
            <div>
              <span>PERSISTED AUDIT HISTORY</span>
              <h2>可重开审计记录</h2>
            </div>
            <Button
              type="button"
              variant="secondary"
              onClick={() => void loadPersistedHistory()}
              disabled={historyState === "loading"}
            >
              {historyState === "loading" ? "载入中" : "刷新历史"}
            </Button>
          </header>
          {historyState === "error" ? (
            <p className="query-section__empty">
              历史暂时不可用；当前查询不受影响。
            </p>
          ) : null}
          {persistedHistory.length ? (
            <ol>
              {persistedHistory.map((entry, index) => (
                <li key={entry.id}>
                  <span>{index + 1}</span>
                  <button
                    type="button"
                    onClick={() => setSelectedHistory(entry)}
                  >
                    <strong>
                      {safeMetadataValue(entry.intent, "自动意图")}
                    </strong>
                    <small>
                      {safeMetadataValue(entry.query_id)} ·{" "}
                      {safeMetadataValue(entry.answer_mode, "未作答")}
                    </small>
                  </button>
                  <em>{entry.evidence?.count ?? 0} evidence</em>
                </li>
              ))}
            </ol>
          ) : historyState === "idle" ? (
            <p className="query-section__empty">尚无持久查询审计记录。</p>
          ) : null}
          {selectedHistory ? (
            <div
              className="query-history__audit"
              role="region"
              aria-label="历史审计详情"
            >
              <div>
                <span>Trace</span>
                <strong>
                  {safeMetadataValue(
                    selectedHistory.trace_id || selectedHistory.query_id,
                  )}
                </strong>
              </div>
              <div>
                <span>状态</span>
                <strong>
                  {safeMetadataValue(selectedHistory.interaction_state)} ·{" "}
                  {safeMetadataValue(selectedHistory.reason_code, "无阻塞")}
                </strong>
              </div>
              <div>
                <span>范围</span>
                <strong>
                  {selectedHistory.resolved_scope?.repository_ids
                    ?.map((item) => safeMetadataValue(item))
                    .join("、") || "项目范围"}
                </strong>
              </div>
              <div>
                <span>证据</span>
                <strong>
                  {selectedHistory.evidence?.sources
                    ?.map(sourceLabel)
                    .join("、") || "无"}
                </strong>
              </div>
              {(selectedHistory.evidence?.sources || []).length === 1 ? (
                <nav aria-label="历史证据定位">
                  {(selectedHistory.evidence?.entity_ids || [])
                    .slice(0, 8)
                    .map((entityId, index) => (
                      <a
                        key={entityId}
                        href={graphHref(
                          projectId,
                          selectedHistory.evidence?.sources?.[0] || "overview",
                          entityId,
                        )}
                      >
                        证据 {index + 1}
                      </a>
                    ))}
                </nav>
              ) : (
                <nav aria-label="多源历史证据审阅">
                  <a href={`/p/${encodeURIComponent(projectId)}/map`}>
                    打开多源证据总览
                  </a>
                  {(selectedHistory.evidence?.sources || []).map((source) => (
                    <a key={source} href={sourceReviewHref(projectId, source)}>
                      审阅{sourceLabel(source)}来源
                    </a>
                  ))}
                  <span>
                    {selectedHistory.evidence?.entity_ids?.length || 0}{" "}
                    个证据标识；历史记录未提供逐项来源映射，因此不生成误导定位链接。
                  </span>
                </nav>
              )}
            </div>
          ) : null}
        </section>
      ) : null}

      {response ? (
        <>
          <QueryResultView
            response={response}
            projectId={projectId}
            clarificationAnswer={clarificationAnswer}
            setClarificationAnswer={setClarificationAnswer}
            onClarification={() =>
              void runQuery({ clarification: clarificationAnswer })
            }
            pending={pending}
          />
          {queryPresentationMode(response) !== "clarification" ? (
            <section className="query-feedback">
              <div>
                <MessageSquareText size={18} />
                <div>
                  <strong>反馈与纠错链</strong>
                  <p>
                    反馈仅保存在当前浏览器会话；纠错会作为下一轮查询继续当前证据链。
                  </p>
                </div>
              </div>
              <div className="query-feedback__actions">
                <button
                  type="button"
                  className={
                    feedbackByTurn[
                      response.client_turn_id ||
                        response.interaction_id ||
                        response.query_id
                    ] === "helpful"
                      ? "is-active"
                      : ""
                  }
                  onClick={() => {
                    const id =
                      response.client_turn_id ||
                      response.interaction_id ||
                      response.query_id;
                    setFeedbackByTurn((current) => ({
                      ...current,
                      [id]: "helpful",
                    }));
                  }}
                >
                  <ThumbsUp size={14} /> 结果有帮助
                </button>
                <button
                  type="button"
                  className={
                    feedbackByTurn[
                      response.client_turn_id ||
                        response.interaction_id ||
                        response.query_id
                    ] === "needs_correction"
                      ? "is-active"
                      : ""
                  }
                  onClick={() => {
                    const id =
                      response.client_turn_id ||
                      response.interaction_id ||
                      response.query_id;
                    setFeedbackByTurn((current) => ({
                      ...current,
                      [id]: "needs_correction",
                    }));
                  }}
                >
                  <ThumbsDown size={14} /> 需要纠正
                </button>
              </div>
              {feedbackByTurn[
                response.client_turn_id ||
                  response.interaction_id ||
                  response.query_id
              ] === "needs_correction" ? (
                <form
                  className="query-correction"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void runQuery({ correction: correctionDraft });
                  }}
                >
                  <label>
                    <span>指出错误并提供期望核验方向</span>
                    <textarea
                      aria-label="纠错说明"
                      value={correctionDraft}
                      onChange={(event) =>
                        setCorrectionDraft(event.target.value)
                      }
                      rows={3}
                      placeholder="例如：上一轮使用了旧提交，请按 commit abc123 重新核验。"
                    />
                  </label>
                  <Button
                    type="submit"
                    variant="primary"
                    disabled={!correctionDraft.trim() || pending}
                  >
                    提交纠错查询
                  </Button>
                </form>
              ) : null}
            </section>
          ) : null}
        </>
      ) : !pending && !error ? (
        <EmptyState
          icon={<ShieldCheck />}
          title="从可核验问题开始"
          description="回答会明确呈现检索状态、反证、缺失角色、逐项引用和适用范围。证据浏览请切换到上方独立入口。"
        />
      ) : null}
    </div>
  );
}
