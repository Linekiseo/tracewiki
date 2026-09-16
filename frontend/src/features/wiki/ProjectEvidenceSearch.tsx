import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  Clock3,
  Database,
  FileCheck2,
  FileSearch,
  GitBranch,
  Link2,
  LoaderCircle,
  Route,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button, Status } from "../../components/ui";
import { api } from "../../lib/api";
import type {
  QueryIntent,
  QueryPlanPreview,
  QuerySource,
  TrustedQueryResponse,
} from "../../lib/types";

const allSources: Array<{ key: QuerySource; label: string; hint: string }> = [
  { key: "code", label: "代码", hint: "实现、版本与测试" },
  { key: "codex", label: "会话", hint: "决策、目标与验证过程" },
  { key: "experiment", label: "实验", hint: "指标、对照与复现" },
  { key: "notebook", label: "Notebook", hint: "输出、参数与执行证据" },
  { key: "document", label: "文档", hint: "声明、引文与原文" },
  { key: "workspace", label: "工作区", hint: "跨来源关系与上下文" },
];

const intentOptions: Array<{ value: QueryIntent; label: string }> = [
  { value: "current_implementation", label: "当前实现" },
  { value: "rationale", label: "设计依据" },
  { value: "change_trace", label: "变更追踪" },
  { value: "claim_verification", label: "声明核验" },
  { value: "experiment_validation", label: "实验验证" },
  { value: "reproduction", label: "复现检查" },
  { value: "staleness_check", label: "时效检查" },
  { value: "global_synthesis", label: "全局综述" },
];

const roleLabels: Record<string, string> = {
  current_code: "当前代码",
  version: "明确版本",
  tests: "测试证据",
  safe_generation_context: "安全生成上下文",
  development_context: "开发上下文",
  decision_or_goal: "决策或目标",
  validation: "验证记录",
  claim: "可验证声明",
  source_location: "原始位置",
  citation: "引用",
  experiment: "实验结果",
  rationale: "设计依据",
  architecture_design: "架构与设计",
  wiki_organization: "Wiki 知识组织",
  retrieval_pipeline: "检索与排序链路",
  product_interaction: "产品交互",
  service_integration: "服务集成",
  validation_quality: "验证与质量",
  governance_security: "治理与安全",
  implementation_history: "研发会话与变更",
  current_implementation: "当前实现",
  documented_knowledge: "文档知识",
  experimental_results: "实验结果",
  analysis_reproduction: "分析与复现",
  project_context: "项目上下文",
  desktop_delivery: "桌面端交付",
  relation_graph: "关系网络",
};

const sourceStatusLabels: Record<string, string> = {
  complete: "完成",
  partial: "部分完成",
  timeout: "超时",
  unavailable: "不可用",
  unauthorized: "无权限",
  not_indexed: "未索引",
  no_matching_evidence: "无匹配证据",
};

const reasonLabels: Record<string, string> = {
  evidence_requested: "已完成证据检索；请核对引用后再形成最终结论",
  generation_unavailable: "当前未启用生成器，已返回可人工复核的证据结论",
  evidence_sufficient: "证据义务已覆盖，可继续人工审阅或生成结论",
  verified_authority_unavailable: "可信发布权威不可用，本次只返回可核验证据",
  code_version_untrusted: "代码版本范围未通过可信校验",
  unsafe_evidence_removed: "不安全证据已移除，结果按可用证据降级",
  ambiguous_scope_or_version: "项目范围或版本不明确",
  source_timeout: "来源在本次时限内未完成",
  no_matching_evidence: "该来源没有匹配证据",
  citation_verification_failed: "声明与引用校验未通过",
  generator_not_configured: "生成器未配置",
};

const examples = [
  "当前 Wiki 智能检索由哪些生产组件实现，测试证据在哪里？",
  "CodePlatformIntegration 如何保证 V2 scope 与 ACL fail-closed？",
  "最近哪些 Codex 会话改变了 Wiki 与 RAG 的实现？",
];

type QueryPhase = "idle" | "planning" | "retrieving" | "complete" | "failed";

function sourceLabel(source: string) {
  return allSources.find((item) => item.key === source)?.label || source;
}

function roleLabel(role: string) {
  return roleLabels[role] || role.replaceAll("_", " ");
}

function reasonLabel(reason?: string | null) {
  if (!reason) return "未报告附加原因";
  return reasonLabels[reason] || reason.replaceAll("_", " ");
}

function compact(value?: string | null, width = 14) {
  if (!value) return "—";
  const normalized = value.startsWith("sha256:") ? value.slice(7) : value;
  return normalized.length > width ? `${normalized.slice(0, width)}…` : normalized;
}

function alignmentLabel(value?: string | null) {
  if (value === "exact") return "版本一致";
  if (value === "not_applicable") return "关系扩展";
  if (value === "stale") return "版本过期";
  return value ? value.replaceAll("_", " ") : "版本未报告";
}

function graphHref(projectId: string, source: string, entityId: string) {
  const board = allSources.some((item) => item.key === source) ? source : "overview";
  return `/p/${encodeURIComponent(projectId)}/map?${new URLSearchParams({ board, focus: entityId })}`;
}

function ProjectQueryPlan({ plan }: { plan: QueryPlanPreview }) {
  return (
    <section className="project-query-plan" aria-label="已确认的检索计划">
      <header>
        <div>
          <span>01 · QUERY PLAN</span>
          <h2>先确认范围与证据义务</h2>
        </div>
        <Status value="active">只读计划 · 尚未检索</Status>
      </header>
      <div className="project-query-plan__grid">
        <article>
          <small>解析范围</small>
          <strong>{plan.resolved_scope.project_id || plan.project_id}</strong>
          <span>{plan.resolved_scope.commit || plan.resolved_scope.branch || "当前已发布版本"}</span>
        </article>
        <article>
          <small>任务意图</small>
          <strong>{intentOptions.find((item) => item.value === plan.intent)?.label || plan.intent}</strong>
          <span>计划 {compact(plan.plan_digest)}</span>
        </article>
        <article className="is-wide">
          <small>证据义务</small>
          <div className="project-role-chips">
            {plan.required_roles.map((role) => <span key={role}>{roleLabel(role)}</span>)}
          </div>
        </article>
      </div>
      <ol className="project-source-waves">
        {plan.source_waves.map((wave) => (
          <li key={wave.wave}>
            <b>Wave {wave.wave}</b>
            <span>{wave.sources.map(sourceLabel).join(" · ")}</span>
            <small>{wave.execution_mode === "parallel" ? "并行检索" : wave.execution_mode}</small>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ProjectQueryResult({
  projectId,
  response,
}: {
  projectId: string;
  response: TrustedQueryResponse;
}) {
  const pack = response.evidence_pack || {};
  const evidence = [
    ...(pack.verified_facts || []),
    ...(pack.supporting_evidence || []),
    ...(pack.counter_evidence || []),
  ]
    .filter(
      (item, index, items) =>
        items.findIndex((candidate) => candidate.entity_id === item.entity_id) === index,
    )
    .map((item, originalOrder) => ({ item, originalOrder }))
    .sort(
      (left, right) =>
        Number(Boolean(left.item.relation_only)) - Number(Boolean(right.item.relation_only))
        || left.originalOrder - right.originalOrder,
    )
    .map(({ item }) => item);
  const satisfied = new Set(pack.role_coverage?.satisfied || response.satisfied_roles || []);
  const missing = new Set(pack.role_coverage?.missing || response.missing_roles || []);
  const required = [
    ...new Set([
      ...(pack.role_coverage?.required || []),
      ...(response.required_roles || []),
      ...satisfied,
      ...missing,
    ]),
  ];
  const sourceStatus = {
    ...(response.source_status || {}),
    ...(pack.source_status || {}),
  };
  const citationByEntity = new Map(
    Object.entries(pack.citation_map || {}).map(([citationId, citation]) => [
      citation.entity_id,
      citationId,
    ]),
  );
  const counterIds = new Set((pack.counter_evidence || []).map((item) => item.entity_id));
  const supportEvidence = evidence.filter((item) => !counterIds.has(item.entity_id));
  const inferredEvidenceClusters = Object.entries(
    supportEvidence.reduce<Record<string, typeof supportEvidence>>((groups, item) => {
      const key = item.roles?.find((role) => required.includes(role))
        || item.roles?.[0]
        || item.entity_type
        || item.source;
      groups[key] = [...(groups[key] || []), item];
      return groups;
    }, {}),
  ).sort((a, b) => b[1].length - a[1].length);
  const entityById = new Map(evidence.map((item) => [item.entity_id, item]));
  const evidenceClusters: Array<[string, typeof supportEvidence]> = pack.knowledge_organization
    ? pack.knowledge_organization.evidence_clusters.map((cluster) => [
        cluster.role,
        cluster.entity_ids
          .map((entityId) => entityById.get(entityId))
          .filter((item): item is typeof supportEvidence[number] => Boolean(item)),
      ])
    : inferredEvidenceClusters;
  const versionRiskEvidence = evidence.filter(
    (item) => item.version_alignment && !["exact", "not_applicable"].includes(item.version_alignment),
  );
  const versionDifferences = pack.version_differences || [];
  const conflicts = pack.conflicts_and_staleness || [];
  const relations = pack.knowledge_organization?.relation_paths || (pack.relations || [])
    .filter((relation) => entityById.has(relation.source) || entityById.has(relation.target))
    .map((relation) => ({
      relation_id: relation.id,
      source_entity_id: relation.source,
      target_entity_id: relation.target,
      predicate: relation.predicate || "related_to",
      domain: relation.domain || "unknown",
      review_status: relation.review_status,
      derivation: relation.derivation,
    }));
  const nextActions = response.next_actions || pack.next_actions || response.answer.next_actions || [];
  const decision = response.answer.decision_status || pack.decision?.status || response.answer.status || response.state || "retrieval_only";
  const complete = pack.knowledge_organization?.decision.complete ?? (missing.size === 0
    && evidence.length > 0
    && counterIds.size === 0
    && versionRiskEvidence.length === 0
    && versionDifferences.length === 0
    && conflicts.length === 0
    && ["supported", "complete"].includes(decision));

  return (
    <div className="project-query-result" aria-live="polite">
      <section className={`project-query-verdict ${complete ? "is-complete" : "is-partial"}`}>
        <span>{complete ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}</span>
        <div>
          <small>检索判定 · {decision.replaceAll("_", " ")}</small>
          <h2>{complete ? "证据义务已覆盖" : "仅形成部分证据链"}</h2>
          <p>{response.answer.text || "服务未返回可呈现的证据摘要。"}</p>
        </div>
        <dl>
          <div><dt>证据</dt><dd>{evidence.length}</dd></div>
          <div><dt>已满足</dt><dd>{satisfied.size}/{required.length}</dd></div>
          <div><dt>缺失</dt><dd>{missing.size}</dd></div>
        </dl>
      </section>

      <section className="project-result-section project-knowledge-organization">
        <header>
          <div><span>ORGANIZED KNOWLEDGE</span><h2>从问题到证据结论的组织结构</h2></div>
          <small>{pack.knowledge_organization ? "服务端确定性组织 · 可跨客户端复核" : "兼容组织 · 按证据角色聚合"}</small>
        </header>
        <div className="project-knowledge-route" aria-label="查询知识组织路径">
          <article className="is-question">
            <small>问题</small>
            <strong>{pack.question || "当前项目查询"}</strong>
          </article>
          <ArrowRight size={17} />
          <article className="is-obligation">
            <small>证据义务</small>
            <strong>{required.length} 项 · {satisfied.size} 项满足</strong>
          </article>
          <ArrowRight size={17} />
          <article className="is-evidence">
            <small>可核验证据</small>
            <strong>{supportEvidence.length} 支持 · {counterIds.size} 反证</strong>
          </article>
          <ArrowRight size={17} />
          <article className={complete ? "is-conclusion" : "is-gap"}>
            <small>当前结论</small>
            <strong>{complete ? "证据结构完整" : `仍缺 ${missing.size} 项`}</strong>
          </article>
        </div>
        <div className="project-knowledge-columns">
          <section>
            <header><CheckCircle2 size={15} /><strong>支持证据簇</strong><small>{evidenceClusters.length} 个主题</small></header>
            <div className="project-evidence-clusters">
              {evidenceClusters.map(([cluster, items]) => (
                <article key={cluster}>
                  <header><span>{roleLabel(cluster)}</span><b>{items.length}</b></header>
                  <p>{[...new Set(items.map((item) => sourceLabel(item.source)))].join(" · ")}</p>
                  <ul>{items.slice(0, 3).map((item) => <li key={item.entity_id}>{item.title}</li>)}</ul>
                  {items.length > 3 ? <small>另有 {items.length - 3} 条同角色证据</small> : null}
                </article>
              ))}
              {!evidenceClusters.length ? <p className="project-knowledge-empty">没有形成可支持结论的证据簇。</p> : null}
            </div>
          </section>
          <section>
            <header><AlertTriangle size={15} /><strong>反证、冲突与缺失</strong><small>必须显式呈现</small></header>
            <div className="project-risk-list">
              {(pack.counter_evidence || []).map((item) => (
                <article className="is-counter" key={item.entity_id}>
                  <b>反证 · {sourceLabel(item.source)}</b>
                  <strong>{item.title}</strong>
                  <p>{item.snippet}</p>
                </article>
              ))}
              {versionRiskEvidence.map((item) => (
                <article className="is-version" key={`version-${item.entity_id}`}>
                  <b>版本风险 · {alignmentLabel(item.version_alignment)}</b>
                  <strong>{item.title}</strong>
                  <p>{item.version || "未提供稳定版本"}</p>
                </article>
              ))}
              {versionDifferences.map((item, index) => (
                <article className="is-version" key={`version-difference-${item.entity_id || index}`}>
                  <b>版本差异</b>
                  <strong>{item.title || item.entity_id || `差异 ${index + 1}`}</strong>
                  <p>{item.reason || item.status || item.version || "来源报告了需要人工核对的版本变化。"}</p>
                </article>
              ))}
              {conflicts.map((item, index) => (
                <article className="is-counter" key={`conflict-${item.kind}-${index}`}>
                  <b>跨来源冲突 · {item.kind}</b>
                  <strong>{item.sources.map(sourceLabel).join(" ↔ ") || "证据状态不一致"}</strong>
                  <p>{item.reason}</p>
                </article>
              ))}
              {[...missing].map((role) => (
                <article className="is-missing" key={`missing-${role}`}>
                  <b>证据缺失</b>
                  <strong>{roleLabel(role)}</strong>
                  <p>{pack.missing_evidence?.find((item) => item.role === role)?.reason || "当前来源未提供足以满足该义务的证据。"}</p>
                </article>
              ))}
              {!counterIds.size && !versionRiskEvidence.length && !versionDifferences.length && !conflicts.length && !missing.size ? <p className="project-knowledge-empty">当前结果没有报告反证、版本冲突或角色缺口。</p> : null}
            </div>
          </section>
        </div>
        <section className="project-knowledge-relations">
          <header>
            <Route size={15} />
            <strong>可审计关系路径</strong>
            <small>{relations.length ? `${relations.length} 条与当前证据相连的关系` : "本次没有返回已确认关系"}</small>
          </header>
          {relations.length ? (
            <div>
              {relations.slice(0, 12).map((relation) => (
                <article key={relation.relation_id}>
                  <span>{entityById.get(relation.source_entity_id)?.title || compact(relation.source_entity_id, 28)}</span>
                  <b>{relation.predicate || "related_to"}</b>
                  <span>{entityById.get(relation.target_entity_id)?.title || compact(relation.target_entity_id, 28)}</span>
                  <small>{relation.review_status || relation.derivation || relation.domain || "关系证据"}</small>
                </article>
              ))}
            </div>
          ) : (
            <p className="project-knowledge-empty">当前证据只形成独立事实；系统不会伪造不存在的跨来源关系。</p>
          )}
        </section>
      </section>

      <section className="project-result-section">
        <header>
          <div><span>02 · SOURCE EXECUTION</span><h2>每个来源实际发生了什么</h2></div>
          <small>未完成来源不会被包装成“已检索”</small>
        </header>
        <div className="project-source-statuses">
          {Object.entries(sourceStatus).map(([source, item]) => (
            <article className={`is-${item.status}`} key={source}>
              <div><strong>{sourceLabel(source)}</strong><Status value={item.status}>{sourceStatusLabels[item.status] || item.status}</Status></div>
              <dl>
                <div><dt>候选</dt><dd>{item.candidate_count ?? 0}</dd></div>
                <div><dt>耗时</dt><dd>{item.latency_ms == null ? "—" : `${Math.round(item.latency_ms)} ms`}</dd></div>
              </dl>
              <small>{item.reason ? reasonLabel(item.reason) : `索引 ${compact(item.index_generation?.toString())}`}</small>
            </article>
          ))}
        </div>
      </section>

      <section className="project-result-section">
        <header>
          <div><span>03 · ROLE COVERAGE</span><h2>证据义务覆盖</h2></div>
          <small>按回答前必须具备的角色逐项核对</small>
        </header>
        <div className="project-obligation-list">
          {required.map((role) => {
            const state = satisfied.has(role) ? "satisfied" : missing.has(role) ? "missing" : "unknown";
            return (
              <article className={`is-${state}`} key={role}>
                {state === "satisfied" ? <CheckCircle2 size={16} /> : <CircleDashed size={16} />}
                <span><strong>{roleLabel(role)}</strong><small>{state === "satisfied" ? "已由当前结果支持" : "仍需补充可核验证据"}</small></span>
              </article>
            );
          })}
        </div>
      </section>

      <section className="project-result-section">
        <header>
          <div><span>04 · RAW EVIDENCE</span><h2>可人工审阅的原始定位</h2></div>
          <small>{evidence.length} 条去重证据 · 最多展示 12 条</small>
        </header>
        {evidence.length ? (
          <div className="project-evidence-list">
            {evidence.slice(0, 12).map((item, index) => (
              <article className={counterIds.has(item.entity_id) ? "is-counter" : ""} key={item.entity_id}>
                <header>
                  <b>{citationByEntity.get(item.entity_id) || `R${String(index + 1).padStart(2, "0")}`}</b>
                  <span>{sourceLabel(item.source)}{counterIds.has(item.entity_id) ? " · 反证" : ""}</span>
                  <Status value={item.version_alignment === "exact" || item.version_alignment === "not_applicable" ? "active" : "partial"}>{alignmentLabel(item.version_alignment)}</Status>
                </header>
                <h3>{item.title}</h3>
                <p>{item.snippet}</p>
                <footer>
                  <div><GitBranch size={13} /><span>{item.version || "版本未报告"}</span></div>
                  <div><FileCheck2 size={13} /><span>{item.locator || "原始定位未报告"}</span></div>
                  <Link to={graphHref(projectId, item.source, item.entity_id)}><Link2 size={13} />在图谱中核验</Link>
                </footer>
              </article>
            ))}
          </div>
        ) : (
          <div className="project-result-empty"><FileSearch size={20} /><span><strong>没有可审阅证据</strong><small>检查来源状态、项目范围与查询词后再试。</small></span></div>
        )}
      </section>

      {(response.reason_code || nextActions.length) ? (
        <section className="project-result-section project-next-actions">
          <header><div><span>STOP / NEXT</span><h2>为什么停止，以及下一步</h2></div></header>
          {response.reason_code ? <p><AlertTriangle size={15} />{reasonLabel(response.reason_code)}</p> : null}
          <ol>{nextActions.map((action) => <li key={action}>{action}</li>)}</ol>
        </section>
      ) : null}
    </div>
  );
}

export function ProjectEvidenceSearch({ projectId }: { projectId: string }) {
  const [question, setQuestion] = useState("");
  const [intent, setIntent] = useState<QueryIntent>("current_implementation");
  const [sources, setSources] = useState<QuerySource[]>(() => allSources.map((item) => item.key));
  const [phase, setPhase] = useState<QueryPhase>("idle");
  const [plan, setPlan] = useState<QueryPlanPreview | null>(null);
  const [response, setResponse] = useState<TrustedQueryResponse | null>(null);
  const [error, setError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const requiredSourceSet = useMemo(() => new Set(sources), [sources]);
  const toggleSource = (source: QuerySource) => {
    if (phase === "planning" || phase === "retrieving") return;
    setSources((current) =>
      current.includes(source)
        ? current.length === 1 ? current : current.filter((item) => item !== source)
        : [...current, source],
    );
  };

  const cancel = () => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setPhase("idle");
    setError("本次请求已取消；未用旧响应覆盖当前界面。");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion || !sources.length || controllerRef.current) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setError("");
    setPlan(null);
    setResponse(null);
    const input = {
      question: normalizedQuestion,
      project_id: projectId,
      sources,
      intent,
      latency_budget_ms: 15_000,
      allow_generation: false,
      max_evidence: 12,
      answer_format: "evidence_only" as const,
      clarification_policy: "never" as const,
    };
    try {
      setPhase("planning");
      const nextPlan = await api.query.preview(input, controller.signal);
      if (controller.signal.aborted) return;
      setPlan(nextPlan);
      setPhase("retrieving");
      const nextResponse = await api.query.run(input, controller.signal);
      if (controller.signal.aborted) return;
      setResponse(nextResponse);
      setPhase("complete");
    } catch (requestError) {
      if (controller.signal.aborted) return;
      setPhase("failed");
      setError(requestError instanceof Error ? requestError.message : "查询服务未返回可信结果。");
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  };

  const running = phase === "planning" || phase === "retrieving";
  return (
    <div className="project-evidence-workbench">
      <section className="project-query-hero">
        <header>
          <div className="project-query-hero__title">
            <span><Sparkles size={20} /></span>
            <div><small>LIVE PROJECT EVIDENCE</small><h2>证据义务驱动的项目检索</h2><p>读取当前项目索引，不使用 Wiki 质量样例；先产生只读计划，再执行多来源检索。</p></div>
          </div>
          <Status value="active"><ShieldCheck size={13} />证据模式 · 禁止无引用生成</Status>
        </header>

        <div className="project-query-flow" aria-label="智能检索执行阶段">
          {[
            ["01", "规划范围", phase === "planning"],
            ["02", "多源检索", phase === "retrieving"],
            ["03", "义务核对", phase === "complete"],
            ["04", "原文定位", phase === "complete"],
          ].map(([ordinal, label, active], index) => (
            <div className={active ? "is-active" : phase === "complete" ? "is-done" : ""} key={String(ordinal)}>
              <b>{ordinal}</b><span>{label}</span>{index < 3 ? <ArrowRight size={13} /> : null}
            </div>
          ))}
        </div>

        <form onSubmit={submit} className="project-query-form">
          <label className="project-query-input">
            <Search size={18} />
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} aria-label="项目证据查询" placeholder="询问当前项目的实现、变更、实验或证据来源……" rows={3} disabled={running} />
          </label>
          <div className="project-query-examples">
            <span>可直接试问</span>
            {examples.map((example) => <button type="button" key={example} onClick={() => setQuestion(example)} disabled={running}>{example}</button>)}
          </div>
          <div className="project-query-settings">
            <label><span>查询意图</span><select value={intent} onChange={(event) => setIntent(event.target.value as QueryIntent)} disabled={running}>{intentOptions.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
            <fieldset>
              <legend>项目来源（至少保留一个）</legend>
              {allSources.map((source) => (
                <button type="button" key={source.key} className={requiredSourceSet.has(source.key) ? "is-selected" : ""} onClick={() => toggleSource(source.key)} aria-pressed={requiredSourceSet.has(source.key)} title={source.hint}>{source.label}</button>
              ))}
            </fieldset>
            <div className="project-query-actions">
              {running ? <button type="button" className="project-query-cancel" onClick={cancel}><X size={14} />取消</button> : null}
              <Button type="submit" variant="primary" disabled={!question.trim() || running}>
                {phase === "planning" ? <><LoaderCircle className="is-spinning" size={15} />正在规划</> : phase === "retrieving" ? <><LoaderCircle className="is-spinning" size={15} />正在检索</> : <><Route size={15} />规划并检索</>}
              </Button>
            </div>
          </div>
        </form>
      </section>

      {error ? <div className="project-query-error" role="alert"><AlertTriangle size={17} /><span><strong>{phase === "failed" ? "智能检索未形成可信结果" : "请求已停止"}</strong><small>{error}</small></span></div> : null}
      {plan ? <ProjectQueryPlan plan={plan} /> : null}
      {phase === "retrieving" ? <div className="project-query-progress" role="status"><LoaderCircle className="is-spinning" size={18} /><span><strong>正在按计划并行读取项目来源</strong><small>超时、缺失和无权限会原样保留，不会伪装成成功。</small></span></div> : null}
      {response ? <ProjectQueryResult projectId={projectId} response={response} /> : null}
      {phase === "idle" && !plan && !response ? (
        <section className="project-query-primer">
          <div><Database size={19} /><span><strong>真实项目索引</strong><small>仓库、会话、实验、Notebook、文档、工作区</small></span></div>
          <div><Clock3 size={19} /><span><strong>诚实的执行状态</strong><small>完整、超时、无匹配、无权限逐来源呈现</small></span></div>
          <div><FileCheck2 size={19} /><span><strong>可回到原始证据</strong><small>实体、版本、行号与图谱入口不丢失</small></span></div>
        </section>
      ) : null}
    </div>
  );
}
