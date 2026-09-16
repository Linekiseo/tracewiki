import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Beaker,
  BookOpen,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  ClipboardCheck,
  Code2,
  Database,
  FileCode2,
  FileText,
  FlaskConical,
  FolderOpen,
  GitBranch,
  Link2,
  ListFilter,
  MessageSquareText,
  MoreHorizontal,
  Paperclip,
  Play,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  type FormEvent,
  type ReactNode,
  type RefObject,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Button, Dialog, EmptyState, Status } from "../../components/ui";
import { api } from "../../lib/api";
import {
  cleanEvidenceText,
  humanizeSearchTitle,
  presentSearchSnippet,
  sourceDestination,
} from "../../lib/presentation";
import type {
  BindingCandidate,
  CodexSession,
  CodexTimelineTurn,
  Experiment,
  ExperimentRun,
  ResearchClaim,
  ScientificDocument,
  SearchResult,
} from "../../lib/types";
import { DocumentPreview, documentFormatLabel } from "./DocumentPreview";

export type AssetMode = "sessions" | "documents" | "experiments" | "evidence";
const ASSET_PAGE_SIZE = 24;

const assetTabs: Array<{ mode: AssetMode; label: string; icon: typeof FileText }> = [
  { mode: "sessions", label: "研发会话", icon: MessageSquareText },
  { mode: "documents", label: "科研文档", icon: FileText },
  { mode: "experiments", label: "实验与数据", icon: Beaker },
  { mode: "evidence", label: "证据工作台", icon: ShieldCheck },
];

function formatTime(value?: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    completed: "已完成",
    running: "进行中",
    in_progress: "进行中",
    failed: "失败",
    draft: "草稿",
    archived: "已归档",
    indexed: "已索引",
    reported: "待核验",
    verified: "已验证",
    partially_supported: "部分支持",
    contradicted: "存在矛盾",
    unreviewed: "待复核",
    confirmed: "已确认",
    rejected: "已拒绝",
  };
  return labels[value] || value;
}

function documentCategory(document: ScientificDocument) {
  const haystack = `${document.title} ${document.tags.join(" ")}`.toLowerCase();
  if (/论文|paper|survey|reference|literature/.test(haystack)) return "参考论文";
  if (/设计|design|architecture|方案/.test(haystack)) return "设计文档";
  if (/实验|experiment|result|benchmark/.test(haystack)) return "实验记录";
  return "过程记录";
}

function summaryLines(value: string, limit = 5) {
  const seen = new Set<string>();
  const lines: string[] = [];
  for (const raw of value.split("\n")) {
    const line = cleanEvidenceText(raw.replace(/^[-*#\d.\s]+/, ""), 180);
    if (!line || line.length < 4 || seen.has(line)) continue;
    seen.add(line);
    lines.push(line);
    if (lines.length >= limit) break;
  }
  return lines;
}

function AssetTabs({ mode, projectId }: { mode: AssetMode; projectId: string }) {
  const navigate = useNavigate();
  return (
    <nav className="asset-tabs" aria-label="研究资料类型">
      {assetTabs.map((item) => (
        <button
          className={mode === item.mode ? "is-active" : ""}
          key={item.mode}
          onClick={() => navigate(`/p/${encodeURIComponent(projectId)}/${item.mode}`)}
        >
          <item.icon size={17} />
          {item.label}
        </button>
      ))}
    </nav>
  );
}

function MasterPane({
  mode,
  query,
  onQuery,
  count,
  action,
  children,
}: {
  mode: AssetMode;
  query: string;
  onQuery: (value: string) => void;
  count: number;
  action?: ReactNode;
  children: ReactNode;
}) {
  const placeholders: Record<AssetMode, string> = {
    sessions: "搜索会话标题或目标",
    documents: "搜索文档、作者或分类",
    experiments: "搜索实验或研究目标",
    evidence: "搜索主张或复核对象",
  };
  return (
    <aside className="asset-master">
      <div className="asset-master__tools">
        <label>
          <Search size={16} />
          <input
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder={placeholders[mode]}
          />
        </label>
        <button aria-label="筛选">
          <ListFilter size={17} />
        </button>
        {action}
      </div>
      <div className="asset-master__count">{count} 项</div>
      <div className="asset-master__list">{children}</div>
    </aside>
  );
}

function RelatedRail({
  results,
  projectId,
  onClose,
  closeButtonRef,
}: {
  results: SearchResult[];
  projectId: string;
  onClose: () => void;
  closeButtonRef?: RefObject<HTMLButtonElement | null>;
}) {
  const navigate = useNavigate();
  const groups = useMemo(() => {
    const output = new Map<string, SearchResult[]>();
    results.forEach((item) => {
      const group = output.get(item.source) || [];
      group.push(item);
      output.set(item.source, group);
    });
    return output;
  }, [results]);
  const labels: Record<string, string> = {
    code: "代码文件",
    codex: "研发会话",
    experiment: "实验结果",
    document: "科研文档",
    workspace: "研究任务",
  };
  return (
    <aside className="related-rail" id="related-sources-panel" aria-label="相关来源">
      <header>
        <span>
          <Link2 size={18} />
          <strong>相关来源</strong>
        </span>
        <button
          ref={closeButtonRef}
          onClick={onClose}
          aria-label="收起相关来源"
          aria-controls="related-sources-panel"
          aria-expanded="true"
          title="收起相关来源"
        >
          <ChevronRight size={17} />
        </button>
      </header>
      <div className="related-rail__scroll">
        {[...groups.entries()].map(([source, items]) => (
          <section key={source}>
            <h3>
              {labels[source] || source}
              <span>{items.length}</span>
            </h3>
            {items.slice(0, 5).map((item) => (
              <button
                key={item.entity_id}
                onClick={() => navigate(sourceDestination(projectId, item))}
                aria-label={`打开来源：${humanizeSearchTitle(item.title)}`}
              >
                <span className={`source-mark source-mark--${source}`}>
                  {source === "code"
                    ? "</>"
                    : source === "codex"
                      ? "会"
                      : source === "experiment"
                        ? "实"
                        : source === "document"
                          ? "文"
                          : "研"}
                </span>
                <span>
                  <strong>{humanizeSearchTitle(item.title)}</strong>
                  <small>{presentSearchSnippet(item.source, item.title, item.snippet)}</small>
                </span>
              </button>
            ))}
          </section>
        ))}
        {!results.length ? (
          <div className="related-empty">
            <Link2 size={21} />
            <strong>还没有跨来源关联</strong>
            <span>完成索引或证据绑定后会自动出现在这里。</span>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

export function RelatedRailTrigger({
  count,
  onOpen,
  buttonRef,
}: {
  count: number;
  onOpen: () => void;
  buttonRef?: RefObject<HTMLButtonElement | null>;
}) {
  return (
    <aside className="related-rail-collapsed" aria-label="相关来源已收起">
      <button
        ref={buttonRef}
        className="related-rail-trigger"
        type="button"
        onClick={onOpen}
        aria-label={`展开相关来源，共 ${count} 项`}
        aria-controls="related-sources-panel"
        aria-expanded="false"
        title="展开相关来源"
      >
        <ChevronLeft className="related-rail-trigger__arrow" size={18} aria-hidden="true" />
        <Link2 size={17} aria-hidden="true" />
        <span>相关来源</span>
        <em aria-hidden="true">{count}</em>
      </button>
    </aside>
  );
}

function SessionListItem({
  session,
  selected,
  onSelect,
}: {
  session: CodexSession;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button className={`asset-row asset-row--session ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="asset-row__icon">
        <MessageSquareText size={17} />
      </span>
      <span className="asset-row__body">
        <strong>{cleanEvidenceText(session.title, 110) || "未命名研发会话"}</strong>
        <small>
          {session.turn_count} 轮研究 · {session.file_change_count} 项代码变更
        </small>
        <em>{formatTime(session.updated_at)}</em>
      </span>
      <Status value={session.status}>{statusLabel(session.status)}</Status>
    </button>
  );
}

function TurnNavigator({
  turns,
  selected,
  onSelect,
}: {
  turns: CodexTimelineTurn[];
  selected: number;
  onSelect: (value: number) => void;
}) {
  const turn = turns[selected];
  return (
    <div className="turn-navigator">
      <button
        disabled={selected === 0}
        onClick={() => onSelect(Math.max(0, selected - 1))}
        aria-label="上一轮"
      >
        <ChevronLeft size={17} />
      </button>
      <div>
        <strong>研究轮次 {turn?.ordinal || 1}</strong>
        <span>{selected + 1} / {turns.length || 1}</span>
      </div>
      <button
        disabled={selected >= turns.length - 1}
        onClick={() => onSelect(Math.min(turns.length - 1, selected + 1))}
        aria-label="下一轮"
      >
        <ChevronRight size={17} />
      </button>
    </div>
  );
}

function ResearchPhase({
  icon,
  title,
  meta,
  children,
}: {
  icon: ReactNode;
  title: string;
  meta?: string;
  children: ReactNode;
}) {
  return (
    <section className="research-phase">
      <header>
        <span>{icon}</span>
        <h3>{title}</h3>
        {meta ? <small>{meta}</small> : null}
      </header>
      <div>{children}</div>
    </section>
  );
}

function SessionDetail({
  session,
  turnIndex,
  onTurnIndex,
}: {
  session: ReturnType<typeof api.sessions.timeline> extends Promise<infer T> ? T : never;
  turnIndex: number;
  onTurnIndex: (value: number) => void;
}) {
  const turn = session.turns[turnIndex] || session.turns[0];
  const implementation = summaryLines(turn?.summary || "", 6);
  return (
    <article className="asset-detail session-detail">
      <header className="asset-detail__header">
        <div className="asset-detail__title">
          <span className="asset-detail__type asset-detail__type--session">
            <MessageSquareText size={18} />
          </span>
          <div>
            <h2>{cleanEvidenceText(session.title, 180) || "未命名研发会话"}</h2>
            <p>
              Codex 会话 · {session.turn_count} 个有效轮次 · 更新于 {formatTime(session.updated_at)}
            </p>
          </div>
        </div>
        <div className="asset-detail__actions">
          <Button
            variant="primary"
            onClick={() => {
              const prompt = encodeURIComponent(
                `继续项目 ${session.project_id} 的研发任务：${cleanEvidenceText(turn?.goal, 240)}`,
              );
              window.location.href = `codex://new?path=${encodeURIComponent(session.cwd)}&prompt=${prompt}`;
            }}
          >
            <Play size={16} />
            继续会话
          </Button>
          <Button variant="secondary">
            <Link2 size={16} />
            关联证据
          </Button>
          <button className="more-button" aria-label="更多操作">
            <MoreHorizontal size={18} />
          </button>
        </div>
      </header>
      <TurnNavigator turns={session.turns} selected={turnIndex} onSelect={onTurnIndex} />
      {turn ? (
        <div className="research-story">
          <ResearchPhase icon={<CircleAlert size={18} />} title="目标" meta={statusLabel(turn.status)}>
            <p>{cleanEvidenceText(turn.goal, 900) || "本轮没有记录明确目标。"}</p>
          </ResearchPhase>
          <ResearchPhase icon={<Code2 size={18} />} title="实现" meta={`${turn.files.length} 个关联文件`}>
            {implementation.length ? (
              <ul>
                {implementation.map((item) => <li key={item}>{item}</li>)}
              </ul>
            ) : (
              <p>本轮没有形成可展示的实现摘要。</p>
            )}
            {turn.files.length ? (
              <div className="phase-files">
                {turn.files.slice(0, 8).map((path) => (
                  <button key={path}>
                    <FileCode2 size={15} />
                    {path.split("/").slice(-2).join("/")}
                  </button>
                ))}
              </div>
            ) : null}
          </ResearchPhase>
          <ResearchPhase
            icon={<FlaskConical size={18} />}
            title="验证"
            meta={`${turn.validation_count} 项验证`}
          >
            <div className="verification-facts">
              <div>
                <strong>{turn.command_count}</strong>
                <span>开发操作</span>
              </div>
              <div>
                <strong>{turn.validation_count}</strong>
                <span>测试与检查</span>
              </div>
              <div>
                <strong>{turn.files.length}</strong>
                <span>关联文件</span>
              </div>
            </div>
          </ResearchPhase>
          <ResearchPhase icon={<ClipboardCheck size={18} />} title="结论">
            <p className="conclusion-text">
              {cleanEvidenceText(turn.summary, 1200) || "本轮尚未形成可复核结论。"}
            </p>
          </ResearchPhase>
        </div>
      ) : (
        <EmptyState
          icon={<MessageSquareText size={23} />}
          title="会话还没有有效轮次"
          description="同步完成后会按研究目标、实现、验证和结论组织。"
        />
      )}
    </article>
  );
}

function DocumentListItem({
  document,
  selected,
  onSelect,
}: {
  document: ScientificDocument;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button className={`asset-row asset-row--document ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="asset-row__icon">
        <FileText size={17} />
      </span>
      <span className="asset-row__body">
        <strong>{document.title}</strong>
        <small>{documentFormatLabel(document)} · {documentCategory(document)} · {document.version}</small>
        <em>{formatTime(document.updated_at)}</em>
      </span>
      <Status value={document.status}>{statusLabel(document.status)}</Status>
    </button>
  );
}

function DocumentDetail({ document }: { document: ScientificDocument }) {
  const category = documentCategory(document);
  return (
    <article className="asset-detail document-detail">
      <header className="asset-detail__header">
        <div className="asset-detail__title">
          <span className="asset-detail__type asset-detail__type--document">
            <FileText size={18} />
          </span>
          <div>
            <h2>{document.title}</h2>
            <p>{documentFormatLabel(document)} · {category} · {document.version} · {document.authors.join("、") || "未记录作者"}</p>
          </div>
        </div>
        <div className="asset-detail__actions">
          <Button variant="secondary">
            <Link2 size={16} />
            关联证据
          </Button>
          <button className="more-button" aria-label="更多操作">
            <MoreHorizontal size={18} />
          </button>
        </div>
      </header>
      <DocumentPreview document={document} />
    </article>
  );
}

function ExperimentListItem({
  experiment,
  selected,
  onSelect,
}: {
  experiment: Experiment;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button className={`asset-row asset-row--experiment ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="asset-row__icon">
        <Beaker size={17} />
      </span>
      <span className="asset-row__body">
        <strong>{experiment.title}</strong>
        <small>{experiment.run_count || 0} 次运行 · {cleanEvidenceText(experiment.objective, 62) || "未填写目标"}</small>
        <em>{formatTime(experiment.updated_at)}</em>
      </span>
      <Status value={experiment.status}>{statusLabel(experiment.status)}</Status>
    </button>
  );
}

function ExperimentDetail({
  experiment,
  runs,
}: {
  experiment: Experiment;
  runs: ExperimentRun[];
}) {
  return (
    <article className="asset-detail experiment-detail">
      <header className="asset-detail__header">
        <div className="asset-detail__title">
          <span className="asset-detail__type asset-detail__type--experiment">
            <Beaker size={18} />
          </span>
          <div>
            <h2>{experiment.title}</h2>
            <p>{experiment.owner} · 更新于 {formatTime(experiment.updated_at)}</p>
          </div>
        </div>
        <div className="asset-detail__actions">
          <Button variant="primary">
            <Play size={16} />
            交给 Codex
          </Button>
          <Button variant="secondary">
            <Database size={16} />
            导入结果
          </Button>
        </div>
      </header>
      <div className="experiment-brief">
        <section>
          <h3>研究目标</h3>
          <p>{experiment.objective || "尚未补充研究目标。"}</p>
        </section>
        <section>
          <h3>待验证假设</h3>
          <p>{experiment.hypothesis || "尚未补充假设，可在实验详情中完善。"}</p>
        </section>
      </div>
      <section className="run-section">
        <header>
          <div>
            <h3>实验运行</h3>
            <p>配置、代码版本、数据集与结果统一保留</p>
          </div>
          <span>{runs.length} 次运行</span>
        </header>
        <div className="run-table">
          <div className="run-table__head">
            <span>运行</span><span>状态</span><span>代码版本</span><span>核心指标</span><span>完成时间</span>
          </div>
          {runs.map((run) => (
            <button key={run.id}>
              <span>
                <strong>{run.name}</strong>
                <small>{run.display_key}</small>
              </span>
              <Status value={run.status}>{statusLabel(run.status)}</Status>
              <code>{run.commit_sha?.slice(0, 9) || run.branch || "—"}</code>
              <span className="run-metrics">
                {(run.metrics || []).slice(0, 2).map((metric) => (
                  <em key={metric.id}>{metric.name} {metric.value}{metric.unit || ""}</em>
                ))}
                {!run.metrics?.length ? "—" : null}
              </span>
              <span>{formatTime(run.completed_at)}</span>
            </button>
          ))}
          {!runs.length ? (
            <div className="run-empty">
              <Database size={21} />
              <strong>还没有实验结果</strong>
              <span>可以批量导入本地结果，或交给 Codex 执行实验任务。</span>
            </div>
          ) : null}
        </div>
      </section>
    </article>
  );
}

function EvidenceListItem({
  item,
  selected,
  onSelect,
}: {
  item: ResearchClaim | BindingCandidate;
  selected: boolean;
  onSelect: () => void;
}) {
  const isBinding = "target_path" in item;
  return (
    <button className={`asset-row asset-row--evidence ${selected ? "is-selected" : ""}`} onClick={onSelect}>
      <span className="asset-row__icon">
        {isBinding ? <Link2 size={17} /> : <ShieldCheck size={17} />}
      </span>
      <span className="asset-row__body">
        <strong>
          {isBinding
            ? `${item.source_item_type} → ${item.target_path}`
            : cleanEvidenceText(item.content, 98)}
        </strong>
        <small>
          {isBinding
            ? `${item.repository_name} · 置信度 ${Math.round(item.confidence * 100)}%`
            : `${item.claim_type} · 证据 ${item.evidence?.length || 0} 项`}
        </small>
        <em>{formatTime("updated_at" in item ? item.updated_at : item.created_at)}</em>
      </span>
      <Status value={isBinding ? item.review_status : item.status}>
        {statusLabel(isBinding ? item.review_status : item.status)}
      </Status>
    </button>
  );
}

function EvidenceDetail({
  item,
  onReview,
  reviewing,
}: {
  item: ResearchClaim | BindingCandidate;
  onReview: (decision: "confirmed" | "rejected") => void;
  reviewing: boolean;
}) {
  const isBinding = "target_path" in item;
  return (
    <article className="asset-detail evidence-detail">
      <header className="asset-detail__header">
        <div className="asset-detail__title">
          <span className="asset-detail__type asset-detail__type--evidence">
            {isBinding ? <Link2 size={18} /> : <ShieldCheck size={18} />}
          </span>
          <div>
            <h2>{isBinding ? "跨来源关系复核" : "研究主张与证据"}</h2>
            <p>{isBinding ? "Codex 变更与版本化代码的候选绑定" : `${item.claim_type} · ${item.display_key}`}</p>
          </div>
        </div>
      </header>
      {isBinding ? (
        <div className="binding-review">
          <section>
            <h3>来源</h3>
            <div className="binding-entity">
              <MessageSquareText size={18} />
              <div>
                <strong>{item.source_title}</strong>
                <p>{item.changed_path}</p>
              </div>
            </div>
          </section>
          <div className="binding-arrow"><ChevronRight size={19} /></div>
          <section>
            <h3>目标</h3>
            <div className="binding-entity">
              <FileCode2 size={18} />
              <div>
                <strong>{item.target_path}</strong>
                <p>{item.repository_name} · {item.target_commit_sha.slice(0, 10)}</p>
              </div>
            </div>
          </section>
          <dl className="binding-facts">
            <div><dt>匹配依据</dt><dd>{item.derivation}</dd></div>
            <div><dt>置信度</dt><dd>{Math.round(item.confidence * 100)}%</dd></div>
            <div><dt>当前状态</dt><dd>{statusLabel(item.review_status)}</dd></div>
          </dl>
          {item.review_status === "unreviewed" ? (
            <footer>
              <Button variant="danger" disabled={reviewing} onClick={() => onReview("rejected")}>
                <X size={16} />拒绝
              </Button>
              <Button variant="primary" disabled={reviewing} onClick={() => onReview("confirmed")}>
                <Check size={16} />确认关系
              </Button>
            </footer>
          ) : null}
        </div>
      ) : (
        <div className="claim-detail">
          <section>
            <h3>主张内容</h3>
            <p>{item.content}</p>
          </section>
          <section>
            <h3>关联证据</h3>
            {(item.evidence || []).map((evidence) => (
              <div className="claim-evidence-row" key={evidence.id}>
                <Paperclip size={16} />
                <span>
                  <strong>{evidence.evidence_type}</strong>
                  <small>{evidence.relationship} · {Math.round(evidence.confidence * 100)}%</small>
                </span>
              </div>
            ))}
            {!item.evidence?.length ? <p className="compact-empty">该主张还没有绑定证据。</p> : null}
          </section>
        </div>
      )}
    </article>
  );
}

function UploadDocumentsDialog({
  open,
  projectId,
  onClose,
}: {
  open: boolean;
  projectId: string;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const [category, setCategory] = useState("过程记录");
  const mutation = useMutation({
    mutationFn: () => {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      form.append("project_id", projectId);
      form.append("tags", category);
      form.append("extract_claims", "true");
      return api.documents.uploadBatch(form);
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["documents", projectId] });
      setFiles([]);
      onClose();
    },
  });
  return (
    <Dialog
      open={open}
      title="批量加入科研文档"
      description="直接读取本地文件，系统自动建立目录、主张和证据索引。"
      onClose={onClose}
    >
      <form
        className="form upload-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (files.length) mutation.mutate();
        }}
      >
        <label className="file-drop">
          <Upload size={24} />
          <strong>{files.length ? `已选择 ${files.length} 个文件` : "选择一个或多个文档"}</strong>
          <span>支持 PDF、DOCX、Markdown、HTML 和纯文本；本地直读不设置产品级大小上限。</span>
          <input
            type="file"
            multiple
            accept=".pdf,.docx,.md,.markdown,.txt,.html,.htm"
            onChange={(event) => setFiles(Array.from(event.target.files || []))}
          />
        </label>
        {files.length ? (
          <div className="selected-files">
            {files.slice(0, 12).map((file) => (
              <span key={`${file.name}-${file.size}`}>
                <FileText size={15} />{file.name}<em>{Math.ceil(file.size / 1024)} KB</em>
              </span>
            ))}
          </div>
        ) : null}
        <label>
          <span>文档分类</span>
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option>参考论文</option>
            <option>设计文档</option>
            <option>实验记录</option>
            <option>过程记录</option>
          </select>
        </label>
        {mutation.error ? <p className="form-error">{mutation.error.message}</p> : null}
        <footer>
          <Button type="button" variant="quiet" onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={!files.length || mutation.isPending}>
            {mutation.isPending ? "正在加入…" : `加入 ${files.length || ""} 个文档`}
          </Button>
        </footer>
      </form>
    </Dialog>
  );
}

function CreateExperimentDialog({
  open,
  projectId,
  onClose,
}: {
  open: boolean;
  projectId: string;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const mutation = useMutation({
    mutationFn: () => api.experiments.create(projectId, title.trim(), objective.trim()),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["experiments", projectId] });
      setTitle("");
      setObjective("");
      onClose();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (title.trim()) mutation.mutate();
  };
  return (
    <Dialog
      open={open}
      title="新建实验"
      description="先记录要验证的实验，运行方式、数据与指标随后由系统或 Codex 补全。"
      onClose={onClose}
    >
      <form className="form" onSubmit={submit}>
        <label>
          <span>实验名称 <b>必填</b></span>
          <input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：协同奖励消融实验" />
        </label>
        <label>
          <span>研究目标 <em>可选</em></span>
          <textarea value={objective} onChange={(event) => setObjective(event.target.value)} rows={3} placeholder="这次实验需要回答什么问题？" />
        </label>
        {mutation.error ? <p className="form-error">{mutation.error.message}</p> : null}
        <footer>
          <Button type="button" variant="quiet" onClick={onClose}>取消</Button>
          <Button type="submit" variant="primary" disabled={!title.trim() || mutation.isPending}>创建实验</Button>
        </footer>
      </form>
    </Dialog>
  );
}

export function ResearchAssetsWorkspace({ mode }: { mode: AssetMode }) {
  const { projectId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const requestedFocus = searchParams.get("focus");
  const requestedQuery = searchParams.get("q") || "";
  const [query, setQuery] = useState(requestedQuery);
  const [assetPage, setAssetPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(requestedFocus);
  const [turnIndex, setTurnIndex] = useState(0);
  const [relatedOpen, setRelatedOpen] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [experimentOpen, setExperimentOpen] = useState(false);
  const relatedCloseButtonRef = useRef<HTMLButtonElement>(null);
  const relatedTriggerButtonRef = useRef<HTMLButtonElement>(null);
  const relatedFocusTarget = useRef<"open" | "closed" | null>(null);
  const client = useQueryClient();

  useEffect(() => {
    setQuery(requestedQuery);
    setAssetPage(0);
    setSelectedId(requestedFocus);
    setTurnIndex(0);
    setRelatedOpen(true);
  }, [mode, requestedFocus, requestedQuery]);

  useEffect(() => {
    if (relatedFocusTarget.current === "open" && relatedOpen) {
      relatedCloseButtonRef.current?.focus();
      relatedFocusTarget.current = null;
    }
    if (relatedFocusTarget.current === "closed" && !relatedOpen) {
      relatedTriggerButtonRef.current?.focus();
      relatedFocusTarget.current = null;
    }
  }, [relatedOpen]);

  const sessionsQuery = useQuery({
    queryKey: ["sessions", projectId],
    queryFn: () => api.sessions.list(projectId),
    enabled: mode === "sessions",
  });
  const documentsQuery = useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.documents.list(projectId),
    enabled: mode === "documents",
  });
  const experimentsQuery = useQuery({
    queryKey: ["experiments", projectId],
    queryFn: () => api.experiments.list(projectId),
    enabled: mode === "experiments",
  });
  const claimsQuery = useQuery({
    queryKey: ["claims", projectId],
    queryFn: () => api.evidence.claims(projectId),
    enabled: mode === "evidence",
  });
  const bindingsQuery = useQuery({
    queryKey: ["bindings", projectId],
    queryFn: () => api.evidence.bindings(projectId),
    enabled: mode === "evidence",
  });

  const sessions = (sessionsQuery.data || []).filter((item) =>
    `${item.title} ${item.cwd}`.toLowerCase().includes(query.toLowerCase()),
  );
  const documents = (documentsQuery.data || []).filter((item) =>
    `${item.title} ${item.authors.join(" ")} ${documentCategory(item)}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const experiments = (experimentsQuery.data || []).filter((item) =>
    `${item.title} ${item.objective}`.toLowerCase().includes(query.toLowerCase()),
  );
  const evidenceItems: Array<ResearchClaim | BindingCandidate> = [
    ...(bindingsQuery.data || []),
    ...(claimsQuery.data || []),
  ].filter((item) =>
    ("target_path" in item ? `${item.source_title} ${item.target_path}` : item.content)
      .toLowerCase()
      .includes(query.toLowerCase()),
  );

  const currentItems: Array<{ id: string }> =
    mode === "sessions"
      ? sessions
      : mode === "documents"
        ? documents
        : mode === "experiments"
          ? experiments
          : evidenceItems;
  const assetPageCount = Math.max(
    1,
    Math.ceil(currentItems.length / ASSET_PAGE_SIZE),
  );
  const assetPageStart = assetPage * ASSET_PAGE_SIZE;
  const pagedSessions = sessions.slice(
    assetPageStart,
    assetPageStart + ASSET_PAGE_SIZE,
  );
  const pagedDocuments = documents.slice(
    assetPageStart,
    assetPageStart + ASSET_PAGE_SIZE,
  );
  const pagedExperiments = experiments.slice(
    assetPageStart,
    assetPageStart + ASSET_PAGE_SIZE,
  );
  const pagedEvidenceItems = evidenceItems.slice(
    assetPageStart,
    assetPageStart + ASSET_PAGE_SIZE,
  );
  useEffect(() => {
    setAssetPage(0);
  }, [mode, query]);
  useEffect(() => {
    if (assetPage >= assetPageCount) setAssetPage(assetPageCount - 1);
  }, [assetPage, assetPageCount]);
  useEffect(() => {
    if (!selectedId && currentItems[0]) setSelectedId(currentItems[0].id);
    if (selectedId && !currentItems.some((item) => item.id === selectedId)) {
      setSelectedId(currentItems[0]?.id || null);
    }
  }, [currentItems, selectedId]);

  const selectedSession = sessions.find((item) => item.id === selectedId);
  const selectedDocument = documents.find((item) => item.id === selectedId);
  const selectedExperiment = experiments.find((item) => item.id === selectedId);
  const selectedEvidence = evidenceItems.find((item) => item.id === selectedId);
  const timelineQuery = useQuery({
    queryKey: ["session-timeline", selectedSession?.thread_id],
    queryFn: () => api.sessions.timeline(selectedSession?.thread_id || ""),
    enabled: Boolean(mode === "sessions" && selectedSession),
  });
  const documentQuery = useQuery({
    queryKey: ["document", selectedDocument?.id],
    queryFn: () => api.documents.get(selectedDocument?.id || ""),
    enabled: Boolean(mode === "documents" && selectedDocument),
  });
  const runsQuery = useQuery({
    queryKey: ["experiment-runs", projectId, selectedExperiment?.id],
    queryFn: () => api.experiments.runs(projectId, selectedExperiment?.id),
    enabled: Boolean(mode === "experiments" && selectedExperiment),
  });
  const selectedSearchText =
    selectedSession?.title ||
    selectedDocument?.title ||
    selectedExperiment?.title ||
    (selectedEvidence
      ? "target_path" in selectedEvidence
        ? selectedEvidence.target_path
        : selectedEvidence.content
      : "");
  const relatedQuery = useQuery({
    queryKey: ["asset-related", projectId, mode, selectedId],
    queryFn: () => api.search.project(projectId, cleanEvidenceText(selectedSearchText, 90)),
    enabled: Boolean(selectedId && selectedSearchText),
  });
  const sourceForMode: Record<AssetMode, string> = {
    sessions: "codex",
    documents: "document",
    experiments: "experiment",
    evidence: "",
  };
  const relatedResults = (relatedQuery.data?.results || [])
    .filter((item) => item.source !== sourceForMode[mode])
    .slice(0, 18);
  const reviewMutation = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "confirmed" | "rejected" }) =>
      api.evidence.reviewBinding(id, decision),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["bindings", projectId] });
    },
  });

  const modeMeta: Record<AssetMode, { title: string; description: string }> = {
    sessions: { title: "研发会话", description: "把 Codex 执行过程整理成可复核的研究记录" },
    documents: { title: "科研文档", description: "按科研用途管理论文、设计、实验与过程文档" },
    experiments: { title: "实验与数据", description: "从研究假设到运行、指标与结果证据" },
    evidence: { title: "证据工作台", description: "确认跨来源关系，复核主张是否获得真实支持" },
  };
  const count = currentItems.length;
  const action =
    mode === "documents" ? (
      <button className="master-action" onClick={() => setUploadOpen(true)} aria-label="批量加入文档">
        <Upload size={17} />
      </button>
    ) : mode === "experiments" ? (
      <button className="master-action" onClick={() => setExperimentOpen(true)} aria-label="新建实验">
        <Plus size={17} />
      </button>
    ) : undefined;
  const relatedStateClass = selectedId
    ? relatedOpen
      ? "has-related"
      : "has-related-collapsed"
    : "";
  const openRelated = () => {
    relatedFocusTarget.current = "open";
    setRelatedOpen(true);
  };
  const closeRelated = () => {
    relatedFocusTarget.current = "closed";
    setRelatedOpen(false);
  };

  return (
    <div className={`assets-workspace ${relatedStateClass}`}>
      <header className="assets-header">
        <div>
          <h1>{modeMeta[mode].title}</h1>
          <p>{modeMeta[mode].description}</p>
        </div>
      </header>
      <AssetTabs mode={mode} projectId={projectId} />
      <div className="assets-body">
        <MasterPane
          mode={mode}
          query={query}
          onQuery={setQuery}
          count={count}
          action={action}
        >
          {mode === "sessions"
            ? pagedSessions.map((item) => (
                <SessionListItem
                  key={item.id}
                  session={item}
                  selected={selectedId === item.id}
                  onSelect={() => {
                    setSelectedId(item.id);
                    setTurnIndex(0);
                  }}
                />
              ))
            : null}
          {mode === "documents"
            ? pagedDocuments.map((item) => (
                <DocumentListItem
                  key={item.id}
                  document={item}
                  selected={selectedId === item.id}
                  onSelect={() => setSelectedId(item.id)}
                />
              ))
            : null}
          {mode === "experiments"
            ? pagedExperiments.map((item) => (
                <ExperimentListItem
                  key={item.id}
                  experiment={item}
                  selected={selectedId === item.id}
                  onSelect={() => setSelectedId(item.id)}
                />
              ))
            : null}
          {mode === "evidence"
            ? pagedEvidenceItems.map((item) => (
                <EvidenceListItem
                  key={item.id}
                  item={item}
                  selected={selectedId === item.id}
                  onSelect={() => setSelectedId(item.id)}
                />
              ))
            : null}
          {count > ASSET_PAGE_SIZE ? (
            <nav className="collection-pager asset-list-pager" aria-label="资料列表分页">
              <button
                type="button"
                disabled={assetPage === 0}
                onClick={() => setAssetPage((value) => Math.max(0, value - 1))}
              >
                <ChevronLeft size={15} />
              </button>
              <span>
                {assetPage + 1} / {assetPageCount}
              </span>
              <button
                type="button"
                disabled={assetPage + 1 >= assetPageCount}
                onClick={() =>
                  setAssetPage((value) => Math.min(assetPageCount - 1, value + 1))
                }
              >
                <ChevronRight size={15} />
              </button>
            </nav>
          ) : null}
          {!count ? (
            <div className="asset-list-empty">
              {mode === "documents" ? (
                <><FolderOpen size={22} /><strong>还没有科研文档</strong><span>批量加入本地资料后会自动分类和建立索引。</span></>
              ) : mode === "experiments" ? (
                <><Beaker size={22} /><strong>还没有实验</strong><span>先记录一个要验证的问题，再交给 Codex 或导入结果。</span></>
              ) : mode === "evidence" ? (
                <><ShieldCheck size={22} /><strong>暂无待复核证据</strong><span>文档主张和跨来源绑定候选会集中出现在这里。</span></>
              ) : (
                <><MessageSquareText size={22} /><strong>没有匹配会话</strong><span>同步 Codex 会话后会按研究轮次组织。</span></>
              )}
            </div>
          ) : null}
        </MasterPane>

        <main className="asset-detail-stage">
          {mode === "sessions" && timelineQuery.data ? (
            <SessionDetail session={timelineQuery.data} turnIndex={turnIndex} onTurnIndex={setTurnIndex} />
          ) : null}
          {mode === "documents" && documentQuery.data ? (
            <DocumentDetail document={documentQuery.data} />
          ) : null}
          {mode === "experiments" && selectedExperiment ? (
            <ExperimentDetail experiment={selectedExperiment} runs={runsQuery.data || []} />
          ) : null}
          {mode === "evidence" && selectedEvidence ? (
            <EvidenceDetail
              item={selectedEvidence}
              reviewing={reviewMutation.isPending}
              onReview={(decision) =>
                "target_path" in selectedEvidence
                  ? reviewMutation.mutate({ id: selectedEvidence.id, decision })
                  : undefined
              }
            />
          ) : null}
          {!selectedId ? (
            <EmptyState
              icon={
                mode === "sessions" ? <MessageSquareText size={23} /> :
                mode === "documents" ? <FileText size={23} /> :
                mode === "experiments" ? <Beaker size={23} /> :
                <ShieldCheck size={23} />
              }
              title={`选择${modeMeta[mode].title.replace("与数据", "")}`}
              description="列表负责定位对象，详情区只展示当前需要理解和处理的信息。"
            />
          ) : null}
        </main>
        {selectedId && relatedOpen ? (
          <RelatedRail
            results={relatedResults}
            projectId={projectId}
            onClose={closeRelated}
            closeButtonRef={relatedCloseButtonRef}
          />
        ) : null}
        {selectedId && !relatedOpen ? (
          <RelatedRailTrigger
            count={relatedResults.length}
            onOpen={openRelated}
            buttonRef={relatedTriggerButtonRef}
          />
        ) : null}
      </div>
      <UploadDocumentsDialog open={uploadOpen} projectId={projectId} onClose={() => setUploadOpen(false)} />
      <CreateExperimentDialog open={experimentOpen} projectId={projectId} onClose={() => setExperimentOpen(false)} />
    </div>
  );
}
