import { useMutation, useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  BookOpenCheck,
  Boxes,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  DatabaseZap,
  ExternalLink,
  FileCheck2,
  FileClock,
  FileSearch,
  Fingerprint,
  GitFork,
  Layers3,
  Link2,
  ListFilter,
  LoaderCircle,
  Network,
  Route,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Waypoints,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Button, EmptyState, ExpandableText, Status } from "../../components/ui";
import { api } from "../../lib/api";
import type {
  WikiBuilderPatch,
  WikiNavigateResponse,
  WikiPage,
  WikiQueryClass,
  WikiSourceDomain,
} from "../../lib/types";
import { ProjectEvidenceSearch } from "./ProjectEvidenceSearch";
import { WikiOrganizationControl } from "./WikiOrganizationControl";
import "./WikiWorkbench.css";

const pageSize = 40;
const sourceOptions: Array<{ value: WikiSourceDomain; label: string }> = [
  { value: "code", label: "代码" },
  { value: "codex", label: "会话" },
  { value: "experiment", label: "实验" },
  { value: "notebook", label: "Notebook" },
  { value: "document", label: "文档" },
  { value: "workspace", label: "工作区" },
];
const queryClassOptions: Array<{ value: WikiQueryClass; label: string }> = [
  { value: "local_detail", label: "局部事实" },
  { value: "multi_hop", label: "多跳因果" },
  { value: "comparison", label: "对比验证" },
  { value: "temporal", label: "时间演化" },
  { value: "global", label: "全局综述" },
  { value: "exploratory", label: "探索发现" },
];
const pageTypeLabels: Record<string, string> = {
  architecture: "架构",
  capability: "能力",
  change: "变更",
  component: "组件",
  decision: "决策",
  experiment: "实验",
  finding: "发现",
  issue: "问题",
  procedure: "流程",
  requirement: "需求",
  source: "来源",
};
const stopReasonLabels: Record<string, string> = {
  evidence_complete: "证据义务已满足",
  missing_obligations: "仍有证据义务缺失",
  missing_raw_evidence: "原始证据不可用",
  budget_exhausted: "导航预算已用尽",
  wiki_unavailable: "Wiki 快照不可用",
};
const actionLabels: Record<string, string> = {
  search: "检索候选页面",
  read: "读取页面事实",
  follow: "沿关系继续导航",
  verify_raw: "核验原始证据",
  stop: "完成导航判定",
};

function compactHash(value?: string | null, width = 12) {
  if (!value) return "—";
  return value.startsWith("sha256:")
    ? `${value.slice(7, 7 + width)}…`
    : value.length > width + 4
      ? `${value.slice(0, width)}…`
      : value;
}

function sourceLabel(source: string) {
  return sourceOptions.find((item) => item.value === source)?.label || source;
}

function directoryLabel(path: string) {
  const root = path.split("/").filter(Boolean)[0] || "root";
  return pageTypeLabels[root.replace(/s$/, "")] || root;
}

function asText(value: unknown) {
  if (typeof value === "string") return value;
  if (value == null) return "";
  return JSON.stringify(value, null, 2);
}

function AtlasPageRow({
  page,
  selected,
  onSelect,
}: {
  page: WikiPage;
  selected: boolean;
  onSelect: () => void;
}) {
  const sources = [...new Set(page.source_refs.map((item) => item.source))];
  return (
    <button
      className={`wiki-atlas-row ${selected ? "is-selected" : ""}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <span className="wiki-atlas-row__rail" />
      <span className="wiki-atlas-row__body">
        <span className="wiki-atlas-row__title">
          <strong>{page.title}</strong>
          <em>{pageTypeLabels[page.page_type] || page.page_type}</em>
        </span>
        <small>{page.logical_path}</small>
        <span className="wiki-atlas-row__meta">
          <span>{sources.map(sourceLabel).join(" · ")}</span>
          <span>{page.facts.length} 事实</span>
          <span>{page.links.length} 关系</span>
        </span>
      </span>
      <ChevronRight size={15} />
    </button>
  );
}

function NavigationResult({
  result,
  onOpenPage,
}: {
  result: WikiNavigateResponse;
  onOpenPage: (path: string) => void;
}) {
  const satisfied = result.obligations.filter(
    (item) => item.status === "satisfied",
  ).length;
  return (
    <div className="wiki-navigation-result" aria-live="polite">
      <header className="wiki-navigation-verdict">
        <span
          className={
            result.stop_reason === "evidence_complete" ? "is-complete" : "is-hold"
          }
        >
          {result.stop_reason === "evidence_complete" ? (
            <CheckCircle2 size={20} />
          ) : (
            <ShieldAlert size={20} />
          )}
        </span>
        <div>
          <small>导航判定</small>
          <strong>{stopReasonLabels[result.stop_reason] || result.stop_reason}</strong>
        </div>
        <dl>
          <div>
            <dt>证据义务</dt>
            <dd>{satisfied}/{result.obligations.length}</dd>
          </div>
          <div>
            <dt>原文核验</dt>
            <dd>{result.raw_verify_count}</dd>
          </div>
          <div>
            <dt>关系跳转</dt>
            <dd>{result.followed_link_count}</dd>
          </div>
        </dl>
      </header>

      <section className="wiki-result-section">
        <header>
          <span><BookOpenCheck size={15} />证据义务</span>
          <small>回答前必须满足的角色与来源</small>
        </header>
        <div className="wiki-obligation-grid">
          {result.obligations.map((item) => (
            <article key={item.obligation_id} className={`is-${item.status}`}>
              <span>
                {item.status === "satisfied" ? (
                  <CheckCircle2 size={15} />
                ) : (
                  <CircleDashed size={15} />
                )}
                {item.role}
              </span>
              <small>
                {item.required_sources.length
                  ? item.required_sources.map(sourceLabel).join(" + ")
                  : "跨来源"}
              </small>
              <b>{item.supporting_page_paths.length} 个页面</b>
            </article>
          ))}
        </div>
      </section>

      <section className="wiki-result-section">
        <header>
          <span><Layers3 size={15} />选定证据页面</span>
          <small>{result.selected_page_paths.length} 个快照内页面</small>
        </header>
        <div className="wiki-selected-pages">
          {result.selected_page_paths.map((path, index) => (
            <button key={path} onClick={() => onOpenPage(path)}>
              <em>{String(index + 1).padStart(2, "0")}</em>
              <span>
                <strong>{path.split("/").pop()}</strong>
                <small>{path}</small>
              </span>
              <ArrowRight size={15} />
            </button>
          ))}
        </div>
      </section>

      <section className="wiki-result-section">
        <header>
          <span><Route size={15} />可审计导航轨迹</span>
          <small>{result.actions.length} 个确定性动作</small>
        </header>
        <ol className="wiki-action-timeline">
          {result.actions.map((action) => (
            <li key={action.ordinal}>
              <b>{action.ordinal}</b>
              <span>
                <strong>{actionLabels[action.action] || action.action}</strong>
                <small>
                  第 {action.round} 轮 · 输入 {action.input_ids.length} · 输出 {action.output_ids.length}
                  {action.diagnostic_code ? ` · ${action.diagnostic_code}` : ""}
                </small>
              </span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function PageInspector({
  projectId,
  generationId,
  page,
  onOpenPage,
}: {
  projectId: string;
  generationId?: string | null;
  page?: WikiPage;
  onOpenPage: (path: string) => void;
}) {
  const [tab, setTab] = useState<"facts" | "evidence" | "relations">("facts");
  const [rawRefId, setRawRefId] = useState("");
  const evidenceMutation = useMutation({
    mutationFn: (sourceRefId: string) =>
      api.wiki.evidence(projectId, page?.logical_path || "", sourceRefId, generationId || undefined),
    onMutate: (sourceRefId) => setRawRefId(sourceRefId),
  });
  const followQuery = useQuery({
    queryKey: ["wiki-follow", projectId, generationId, page?.logical_path],
    queryFn: ({ signal }) =>
      api.wiki.follow(projectId, page?.logical_path || "", generationId || undefined, signal),
    enabled: Boolean(projectId && page?.logical_path),
    retry: false,
  });

  useEffect(() => {
    setRawRefId("");
    evidenceMutation.reset();
    // Resetting the evidence reader on page identity change is intentional.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page?.content_sha256]);

  if (!page) {
    return (
      <aside className="wiki-inspector">
        <EmptyState
          icon={<FileSearch size={22} />}
          title="选择一个知识页面"
          description="右侧会显示事实权威、原始证据和可追踪关系，不会只展示摘要。"
        />
      </aside>
    );
  }

  const sources = [...new Set(page.source_refs.map((item) => item.source))];
  return (
    <aside className="wiki-inspector" aria-label="Wiki 页面权威详情">
      <header className="wiki-inspector__heading">
        <div>
          <span>{pageTypeLabels[page.page_type] || page.page_type}</span>
          <h2>{page.title}</h2>
          <p>{page.logical_path}</p>
        </div>
        <Status value="indexed">快照内有效</Status>
      </header>
      <div className="wiki-page-authority">
        <div>
          <Fingerprint size={14} />
          <span><small>内容摘要</small><b>{compactHash(page.content_sha256, 16)}</b></span>
        </div>
        <div>
          <ShieldCheck size={14} />
          <span><small>可见性分区</small><b>{compactHash(page.scope.visibility_partition, 16)}</b></span>
        </div>
        <div>
          <DatabaseZap size={14} />
          <span><small>来源</small><b>{sources.map(sourceLabel).join(" · ")}</b></span>
        </div>
      </div>
      <p className="wiki-page-summary">{page.summary}</p>
      <nav className="wiki-inspector-tabs" aria-label="页面详情分区">
        <button className={tab === "facts" ? "is-active" : ""} onClick={() => setTab("facts")}>
          事实 {page.facts.length}
        </button>
        <button className={tab === "evidence" ? "is-active" : ""} onClick={() => setTab("evidence")}>
          原始证据 {page.source_refs.length}
        </button>
        <button className={tab === "relations" ? "is-active" : ""} onClick={() => setTab("relations")}>
          关系 {page.links.length}
        </button>
      </nav>
      <div className="wiki-inspector__scroll">
        {tab === "facts" ? (
          <section className="wiki-fact-list">
            {page.facts.length ? page.facts.map((fact) => (
              <article key={fact.fact_id}>
                <header>
                  <strong>{fact.predicate}</strong>
                  <span className={`is-${fact.status}`}>{fact.status}</span>
                </header>
                <p>{fact.object_text || fact.object_path}</p>
                <footer>
                  <span>{fact.authority.replaceAll("_", " ")}</span>
                  <span>{Math.round(fact.confidence * 100)}%</span>
                  <span>{fact.source_ref_ids.length} refs</span>
                </footer>
              </article>
            )) : (
              <p className="wiki-inline-empty">此页面只提供来源权威，尚无结构化事实。</p>
            )}
          </section>
        ) : null}

        {tab === "evidence" ? (
          <section className="wiki-source-ref-list">
            {page.source_refs.map((ref) => (
              <article key={ref.source_ref_id} className={rawRefId === ref.source_ref_id ? "is-selected" : ""}>
                <header>
                  <span>{sourceLabel(ref.source)}</span>
                  <b>{ref.entity_type}</b>
                </header>
                <p>{ref.locator}</p>
                <footer>
                  <small>{ref.observed_at}</small>
                  <button onClick={() => evidenceMutation.mutate(ref.source_ref_id)}>
                    <FileCheck2 size={13} />核验原文
                  </button>
                </footer>
              </article>
            ))}
            {evidenceMutation.isPending ? (
              <p className="wiki-inline-state"><LoaderCircle className="is-spinning" size={15} />正在校验来源权威…</p>
            ) : null}
            {evidenceMutation.isError ? (
              <p className="wiki-inline-state is-error">原始证据无法通过当前快照权威核验。</p>
            ) : null}
            {evidenceMutation.data ? (
              <div className="wiki-raw-reader">
                <header>
                  <span><ShieldCheck size={15} />已核验原始证据</span>
                  <Status value="active">{evidenceMutation.data.evidence.status || "verified"}</Status>
                </header>
                <ExpandableText
                  label={sourceLabel(evidenceMutation.data.source_ref.source)}
                  title="已核验原始证据"
                  description={evidenceMutation.data.source_ref.locator}
                  content={asText(evidenceMutation.data.evidence.text)}
                  previewSize="large"
                  tone="plain"
                />
              </div>
            ) : null}
          </section>
        ) : null}

        {tab === "relations" ? (
          <section className="wiki-relation-list">
            {followQuery.isLoading ? (
              <p className="wiki-inline-state"><LoaderCircle className="is-spinning" size={15} />正在解析关系目标…</p>
            ) : followQuery.data?.targets.length ? (
              followQuery.data.targets.map(({ link, page: target }) => (
                <button key={link.link_id} onClick={() => onOpenPage(target.logical_path)}>
                  <span><GitFork size={15} /><em>{link.relation}</em></span>
                  <strong>{target.title}</strong>
                  <small>{target.logical_path}</small>
                  <footer>
                    <span>{Math.round(link.confidence * 100)}% 置信</span>
                    <ExternalLink size={13} />
                  </footer>
                </button>
              ))
            ) : (
              <p className="wiki-inline-empty">当前页面没有可跟随的有效关系。</p>
            )}
            {(followQuery.data?.unresolved_target_paths || []).map((path) => (
              <div className="wiki-unresolved-relation" key={path}>
                <AlertTriangle size={14} />目标在当前 ACL 或快照中不可解析：{path}
              </div>
            ))}
          </section>
        ) : null}
      </div>
    </aside>
  );
}

function GovernanceDrawer({
  projectId,
  errorPage,
  setErrorPage,
  onPublished,
}: {
  projectId: string;
  errorPage: number;
  setErrorPage: (value: number) => void;
  onPublished: () => void;
}) {
  const errorQuery = useQuery({
    queryKey: ["wiki-error-book", projectId],
    queryFn: ({ signal }) => api.wiki.errorBook(projectId, signal),
    enabled: Boolean(projectId),
  });
  const patchQuery = useQuery({
    queryKey: ["wiki-builder-patches", projectId],
    queryFn: ({ signal }) => api.wiki.patches(projectId, signal),
    enabled: Boolean(projectId),
  });
  const [selectedPatch, setSelectedPatch] = useState<WikiBuilderPatch>();
  const decisionQuery = useQuery({
    queryKey: ["wiki-builder-decision", projectId, selectedPatch?.patch_id],
    queryFn: ({ signal }) =>
      api.wiki.patchDecision(projectId, selectedPatch?.patch_id || "", signal),
    enabled: Boolean(selectedPatch?.patch_id),
    retry: false,
  });
  const errors = errorQuery.data?.entries || [];
  const pagedErrors = errors.slice(errorPage * 10, errorPage * 10 + 10);
  const errorPages = Math.max(1, Math.ceil(errors.length / 10));
  return (
    <section className="wiki-governance">
      <WikiOrganizationControl projectId={projectId} onPublished={onPublished} />
      <div className="wiki-governance-column">
        <header>
          <span><ShieldAlert size={17} />Error Book</span>
          <small>{errors.length} 个编译约束事件</small>
        </header>
        <div className="wiki-governance-list">
          {pagedErrors.map((entry) => (
            <article key={entry.entry_id}>
              <header>
                <span>{sourceLabel(entry.source)}</span>
                <Status value={entry.status === "open" ? "blocked" : "active"}>{entry.status}</Status>
              </header>
              <strong>{entry.code.replaceAll("_", " ")}</strong>
              <p>{entry.constraint}</p>
              <footer>{entry.occurrences} 次 · {entry.last_seen_generation}</footer>
            </article>
          ))}
          {!errorQuery.isLoading && !errors.length ? (
            <p className="wiki-inline-empty">当前快照没有开放的编译错误。</p>
          ) : null}
        </div>
        <footer className="wiki-mini-pager">
          <button disabled={errorPage === 0} onClick={() => setErrorPage(errorPage - 1)}><ChevronLeft size={14} /></button>
          <span>{errorPage + 1} / {errorPages}</span>
          <button disabled={errorPage + 1 >= errorPages} onClick={() => setErrorPage(errorPage + 1)}><ChevronRight size={14} /></button>
        </footer>
      </div>
      <div className="wiki-governance-column">
        <header>
          <span><Sparkles size={17} />Builder 审阅队列</span>
          <small>{patchQuery.data?.total || 0} 个不可在线自发布提案</small>
        </header>
        <div className="wiki-governance-list">
          {(patchQuery.data?.patches || []).map((patch) => (
            <button key={patch.patch_id} className={selectedPatch?.patch_id === patch.patch_id ? "is-selected" : ""} onClick={() => setSelectedPatch(patch)}>
              <header>
                <span>{patch.origin.replaceAll("_", " ")}</span>
                <Status value={patch.reviewed ? "review" : "blocked"}>{patch.reviewed ? "已绑定审阅" : "待人工审阅"}</Status>
              </header>
              <strong>{patch.patch_id}</strong>
              <p>{patch.operations.length} 项页面操作 · {patch.affected_query_ids.length} 条影响查询 · {patch.guard_query_ids.length} 条守护查询</p>
            </button>
          ))}
          {!patchQuery.isLoading && !patchQuery.data?.total ? (
            <p className="wiki-inline-empty">当前没有 Builder 提案。</p>
          ) : null}
        </div>
        {selectedPatch ? (
          <aside className="wiki-patch-decision">
            <header><Fingerprint size={14} />提案审计</header>
            {decisionQuery.data ? (
              <dl>
                <div><dt>决策</dt><dd>{decisionQuery.data.status}</dd></div>
                <div><dt>效用变化</dt><dd>{decisionQuery.data.affected_utility_delta.toFixed(4)}</dd></div>
                <div><dt>改善查询</dt><dd>{decisionQuery.data.improved_affected_queries}</dd></div>
                <div><dt>发布授权</dt><dd>否</dd></div>
              </dl>
            ) : decisionQuery.isLoading ? (
              <p>正在读取评估决策…</p>
            ) : (
              <p>尚无离线试验决策；该提案不能进入发布路径。</p>
            )}
          </aside>
        ) : null}
      </div>
    </section>
  );
}

export function WikiWorkbench() {
  const { projectId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [catalogDraft, setCatalogDraft] = useState("");
  const [catalogQuery, setCatalogQuery] = useState("");
  const [pageType, setPageType] = useState("");
  const [catalogSource, setCatalogSource] = useState<WikiSourceDomain | "">("");
  const [role, setRole] = useState("");
  const [pageIndex, setPageIndex] = useState(0);
  const [question, setQuestion] = useState("");
  const [queryClass, setQueryClass] = useState<WikiQueryClass>("multi_hop");
  const [requiredSources, setRequiredSources] = useState<WikiSourceDomain[]>([]);
  const [centerTab, setCenterTab] = useState<"project" | "atlas" | "governance">("project");
  const [errorPage, setErrorPage] = useState(0);
  const selectedPath = searchParams.get("page") || "";

  const statusQuery = useQuery({
    queryKey: ["wiki-status", projectId],
    queryFn: ({ signal }) => api.wiki.status(projectId, signal),
    enabled: Boolean(projectId),
    refetchInterval: 30_000,
  });
  const pagesQuery = useQuery({
    queryKey: [
      "wiki-pages",
      projectId,
      statusQuery.data?.active_generation_id,
      catalogQuery,
      pageType,
      catalogSource,
      role,
      pageIndex,
    ],
    queryFn: ({ signal }) =>
      api.wiki.pages(
        projectId,
        {
          generationId: statusQuery.data?.active_generation_id || undefined,
          query: catalogQuery,
          pageType,
          source: catalogSource,
          role,
          offset: pageIndex * pageSize,
          limit: pageSize,
        },
        signal,
      ),
    enabled: Boolean(projectId && statusQuery.data?.availability === "AVAILABLE"),
    placeholderData: (previous) => previous,
  });
  const pageQuery = useQuery({
    queryKey: ["wiki-page", projectId, statusQuery.data?.active_generation_id, selectedPath],
    queryFn: ({ signal }) =>
      api.wiki.read(projectId, selectedPath, statusQuery.data?.active_generation_id || undefined, signal),
    enabled: Boolean(projectId && selectedPath && statusQuery.data?.active_generation_id),
    retry: false,
  });
  const navigationMutation = useMutation({
    mutationFn: () =>
      api.wiki.navigate({
        project_id: projectId,
        query: question.trim(),
        query_class: queryClass,
        required_sources: [...requiredSources].sort(),
        require_raw_evidence: true,
      }),
  });

  const groups = useMemo(() => {
    const grouped = new Map<string, WikiPage[]>();
    (pagesQuery.data?.pages || []).forEach((page) => {
      const root = page.logical_path.split("/").filter(Boolean)[0] || "root";
      grouped.set(root, [...(grouped.get(root) || []), page]);
    });
    return [...grouped.entries()];
  }, [pagesQuery.data?.pages]);
  const totalPages = Math.max(1, Math.ceil((pagesQuery.data?.total || 0) / pageSize));

  const selectPage = (path: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("page", path);
      return next;
    });
  };
  useEffect(() => {
    if (selectedPath || !pagesQuery.data?.pages.length) return;
    selectPage(pagesQuery.data.pages[0].logical_path);
    // The first authoritative page is selected only when URL state is absent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pagesQuery.data?.generation_id]);

  const submitCatalog = (event: FormEvent) => {
    event.preventDefault();
    setPageIndex(0);
    setCatalogQuery(catalogDraft.trim());
  };
  const submitNavigation = (event: FormEvent) => {
    event.preventDefault();
    if (question.trim()) navigationMutation.mutate();
  };
  const toggleSource = (source: WikiSourceDomain) => {
    setRequiredSources((current) =>
      current.includes(source)
        ? current.filter((item) => item !== source)
        : [...current, source],
    );
  };

  if (statusQuery.isLoading) {
    return <div className="wiki-page-state" role="status"><LoaderCircle className="is-spinning" size={20} />正在读取 Wiki 快照权威…</div>;
  }
  if (statusQuery.isError) {
    return <div className="wiki-page-state is-error" role="alert"><AlertTriangle size={20} />Wiki 运行状态不可用；页面不会假定知识已发布。</div>;
  }
  const status = statusQuery.data;
  const wikiAvailable = status?.availability === "AVAILABLE";
  const activeCenterTab = wikiAvailable ? centerTab : "governance";
  const qualitySnapshot = Boolean(
    status?.source_generations.some(([, generation]) =>
      /(quality|golden|fixture|synthetic)/i.test(generation),
    ),
  );

  return (
    <main className="wiki-workbench">
      <header className="wiki-workbench__masthead">
        <div className="wiki-masthead-title">
          <span><Waypoints size={18} /></span>
          <div>
            <small>Agent-native knowledge plane</small>
            <h1>Wiki 知识中枢</h1>
          </div>
        </div>
        <dl className="wiki-masthead-metrics">
          <div><dt>当前快照</dt><dd>{wikiAvailable ? compactHash(status.active_generation_id, 16) : "尚未发布"}</dd></div>
          <div><dt>可见页面</dt><dd>{status?.visible_page_count || 0}</dd></div>
          <div><dt>快照检索</dt><dd>{wikiAvailable ? `FTS ${status.dense_availability === "AVAILABLE" ? "+ Dense" : "+ Sparse fallback"}` : "等待组织"}</dd></div>
          <div><dt>快照数据</dt><dd><Status value={!wikiAvailable || qualitySnapshot ? "blocked" : "active"}>{!wikiAvailable ? "未组织" : qualitySnapshot ? "质量评测数据" : "项目发布数据"}</Status></dd></div>
        </dl>
        <nav className="wiki-mode-switch" aria-label="Wiki 工作模式">
          <button className={activeCenterTab === "project" ? "is-active" : ""} onClick={() => setCenterTab("project")} disabled={!wikiAvailable}><Sparkles size={15} />项目证据检索</button>
          <button className={activeCenterTab === "atlas" ? "is-active" : ""} onClick={() => setCenterTab("atlas")} disabled={!wikiAvailable}><Network size={15} />Wiki 快照浏览</button>
          <button className={activeCenterTab === "governance" ? "is-active" : ""} onClick={() => setCenterTab("governance")}><ShieldCheck size={15} />治理与 Builder</button>
        </nav>
      </header>

      <div className={`wiki-data-notice ${!wikiAvailable || qualitySnapshot ? "is-quality" : "is-live"}`} role="note">
        {!wikiAvailable ? <FileClock size={16} /> : activeCenterTab === "project" ? <DatabaseZap size={16} /> : qualitySnapshot ? <AlertTriangle size={16} /> : <ShieldCheck size={16} />}
        <span>
          <strong>{!wikiAvailable ? "当前项目尚未形成可查询 Wiki" : activeCenterTab === "project" ? "当前模式读取项目实时索引" : qualitySnapshot ? "当前 Wiki 是质量评测快照" : "当前 Wiki 是已发布项目快照"}</strong>
          <small>{!wikiAvailable ? "下方会只读盘点六类项目来源；构建候选与显式发布分开进行。" : activeCenterTab === "project" ? "结果来自当前仓库、会话与研究资产；不会使用左侧评测页面充当项目证据。" : qualitySnapshot ? "这些页面用于验证 Wiki 编译、导航和安全合同，不代表当前项目的即时知识；请在治理区重新组织当前项目。" : "页面、关系与原始证据都绑定当前发布 generation。"}</small>
        </span>
      </div>

      <div className={`wiki-workbench__body is-${activeCenterTab}`}>
        {activeCenterTab === "atlas" ? <aside className="wiki-atlas" aria-label="Wiki 知识目录">
          <header>
            <span><Boxes size={16} />发布快照图册</span>
            <small>{pagesQuery.data?.total || 0} 个匹配页面</small>
          </header>
          <form className="wiki-atlas-search" onSubmit={submitCatalog}>
            <label>
              <Search size={15} />
              <input value={catalogDraft} onChange={(event) => setCatalogDraft(event.target.value)} placeholder="筛选标题、事实或路径" aria-label="筛选 Wiki 页面" />
            </label>
            <button type="submit" aria-label="应用 Wiki 页面筛选"><ArrowRight size={15} /></button>
          </form>
          <div className="wiki-atlas-filters">
            <label><ListFilter size={13} /><select value={pageType} onChange={(event) => { setPageType(event.target.value); setPageIndex(0); }} aria-label="页面类型"><option value="">全部类型</option>{Object.entries(pageTypeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label><DatabaseZap size={13} /><select value={catalogSource} onChange={(event) => { setCatalogSource(event.target.value as WikiSourceDomain | ""); setPageIndex(0); }} aria-label="证据来源"><option value="">全部来源</option>{sourceOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            <label><BookOpenCheck size={13} /><input value={role} onChange={(event) => { setRole(event.target.value); setPageIndex(0); }} placeholder="证据角色" aria-label="证据角色" /></label>
          </div>
          <div className="wiki-atlas__scroll">
            {pagesQuery.isLoading ? <p className="wiki-inline-state"><LoaderCircle className="is-spinning" size={15} />正在读取目录…</p> : null}
            {!pagesQuery.isLoading && !groups.length ? <p className="wiki-inline-empty">当前筛选没有可见页面。</p> : null}
            {groups.map(([root, pages]) => (
              <section className="wiki-atlas-group" key={root}>
                <header><span>{directoryLabel(`/${root}`)}</span><small>{pages.length}</small></header>
                {pages.map((page) => <AtlasPageRow key={page.content_sha256} page={page} selected={selectedPath === page.logical_path} onSelect={() => selectPage(page.logical_path)} />)}
              </section>
            ))}
          </div>
          <footer className="wiki-atlas-pager">
            <button disabled={pageIndex === 0} onClick={() => setPageIndex(pageIndex - 1)} aria-label="上一页"><ChevronLeft size={15} /></button>
            <span>第 <b>{pageIndex + 1}</b> / {totalPages} 页</span>
            <button disabled={pageIndex + 1 >= totalPages} onClick={() => setPageIndex(pageIndex + 1)} aria-label="下一页"><ChevronRight size={15} /></button>
          </footer>
        </aside> : null}

        <section className={`wiki-center wiki-center--${activeCenterTab}`}>
          {activeCenterTab === "project" ? <ProjectEvidenceSearch projectId={projectId} /> : activeCenterTab === "atlas" ? (
            <>
              <form className="wiki-query-composer" onSubmit={submitNavigation}>
                <header>
                  <div><span><Waypoints size={17} /></span><div><strong>快照内关系导航</strong><small>仅在当前发布 Wiki 中检索页面、跟随关系并核验快照来源</small></div></div>
                  <Status value={qualitySnapshot ? "blocked" : "ready"}>{qualitySnapshot ? "评测快照" : "wiki_v1"}</Status>
                </header>
                <textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="在当前发布快照中查找页面关系……" aria-label="Wiki 快照查询" rows={3} />
                <div className="wiki-query-controls">
                  <label>任务<select value={queryClass} onChange={(event) => setQueryClass(event.target.value as WikiQueryClass)}>{queryClassOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
                  <fieldset><legend>限定来源（可选）</legend>{sourceOptions.map((item) => <button type="button" key={item.value} className={requiredSources.includes(item.value) ? "is-selected" : ""} onClick={() => toggleSource(item.value)} aria-pressed={requiredSources.includes(item.value)}>{item.label}</button>)}</fieldset>
                  <Button variant="primary" type="submit" disabled={!question.trim() || navigationMutation.isPending}>{navigationMutation.isPending ? <><LoaderCircle className="is-spinning" size={15} />正在导航快照</> : <><Waypoints size={15} />导航当前快照</>}</Button>
                </div>
              </form>
              {navigationMutation.isError ? <div className="wiki-query-error" role="alert"><AlertTriangle size={17} /><span><strong>导航请求未形成可信结果</strong><small>{navigationMutation.error.message}</small></span></div> : null}
              {navigationMutation.data ? <NavigationResult result={navigationMutation.data} onOpenPage={selectPage} /> : (
                <section className="wiki-navigation-primer">
                  <div className="wiki-primer-map">
                    <span><Search size={18} /></span><i /><span><GitFork size={18} /></span><i /><span><FileCheck2 size={18} /></span><i /><span><ShieldCheck size={18} /></span>
                  </div>
                  <h2>这是已发布快照的确定性导航</h2>
                  <p>Navigator 只在固定 generation 与 ACL 中检索 Wiki 页面、跟随结构关系并核验快照来源。若要查询当前仓库和会话，请使用“项目证据检索”。</p>
                  <dl><div><dt>目录检索</dt><dd>Exact · BM25 · Dense · Structural · RRF</dd></div><div><dt>受治理导航</dt><dd>预算、关系跳数、时间与来源约束</dd></div><div><dt>回答边界</dt><dd>逐声明引用，不支持则回退 retrieval-only</dd></div></dl>
                </section>
              )}
            </>
          ) : <GovernanceDrawer
            projectId={projectId}
            errorPage={errorPage}
            setErrorPage={setErrorPage}
            onPublished={() => {
              setCenterTab("atlas");
              setSearchParams((current) => {
                const next = new URLSearchParams(current);
                next.delete("page");
                return next;
              });
            }}
          />}
        </section>

        {activeCenterTab === "atlas" && status?.active_generation_id ? <PageInspector projectId={projectId} generationId={status.active_generation_id} page={pageQuery.data} onOpenPage={selectPage} /> : null}
      </div>
    </main>
  );
}
