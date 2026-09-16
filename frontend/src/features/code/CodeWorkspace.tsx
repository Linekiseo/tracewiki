import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Box,
  Braces,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  Clock3,
  Code2,
  FileCode2,
  FolderInput,
  Files,
  GitBranch,
  GitCommitHorizontal,
  History,
  LoaderCircle,
  Network,
  Plus,
  RefreshCw,
  Search,
  SplitSquareHorizontal,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Button, Dialog, EmptyState, Status } from "../../components/ui";
import { api } from "../../lib/api";
import type {
  CodeComparison,
  CodeFileDetail,
  GitCommit,
  IngestionWorkflow,
  Repository,
} from "../../lib/types";
import {
  cleanEvidenceText,
  humanizeSearchTitle,
  presentSearchSnippet,
} from "../../lib/presentation";
import { CodeTree } from "./CodeTree";
import { CodeRagGraphPanel } from "./CodeRagGraphPanel";
import "./CodeWorkspace.css";

type ViewMode = "browse" | "graph" | "compare";
type RefTab = "current" | "branches" | "commits";

function repositoryLabel(repository: Repository) {
  if (repository.name.trim()) return repository.name;
  const source =
    repository.source_url || repository.local_path || repository.name;
  const normalized = source.replace(/[\\/]+$/, "");
  return (
    normalized.split(/[\\/]/).slice(-2).join("/") ||
    repository.name ||
    "未命名仓库"
  );
}

function repositoryIndexPresentation(repository?: Repository) {
  if (!repository) {
    return { label: "状态不可用", tone: "failed", detail: "未读取仓库" };
  }
  if (repository.status === "ready" && repository.active_generation_id) {
    return {
      label: "索引可用",
      tone: "completed",
      detail: "generation 已发布",
    };
  }
  if (repository.status === "indexing") {
    return {
      label: "正在索引",
      tone: "running",
      detail: "generation 尚未发布",
    };
  }
  if (repository.status === "failed" || repository.last_error) {
    return {
      label: "索引失败",
      tone: "failed",
      detail: "失败结果未标记为可用",
    };
  }
  return { label: "索引未发布", tone: "failed", detail: "没有可用 generation" };
}

const keywordPattern =
  /\b(class|def|function|const|let|var|return|import|from|export|async|await|if|else|elif|for|while|try|catch|except|raise|with|interface|type|public|private|fn|struct|impl|use|match|pub|mod|package|func|select|where|create|table)\b/g;
const tokenPattern =
  /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b\d+(?:\.\d+)?\b)/g;

function syntaxParts(line: string) {
  const commentIndex = (() => {
    const indexes = ["//", "#"]
      .map((token) => line.indexOf(token))
      .filter((index) => index >= 0);
    return indexes.length ? Math.min(...indexes) : -1;
  })();
  const code = commentIndex >= 0 ? line.slice(0, commentIndex) : line;
  const comment = commentIndex >= 0 ? line.slice(commentIndex) : "";
  const parts: Array<{ value: string; kind: string }> = [];
  let cursor = 0;
  for (const match of code.matchAll(tokenPattern)) {
    const index = match.index || 0;
    if (index > cursor)
      parts.push({ value: code.slice(cursor, index), kind: "plain" });
    parts.push({
      value: match[0],
      kind: /^\d/.test(match[0]) ? "number" : "string",
    });
    cursor = index + match[0].length;
  }
  if (cursor < code.length)
    parts.push({ value: code.slice(cursor), kind: "plain" });
  const expanded = parts.flatMap((part) => {
    if (part.kind !== "plain") return [part];
    const result: Array<{ value: string; kind: string }> = [];
    let offset = 0;
    for (const match of part.value.matchAll(keywordPattern)) {
      const index = match.index || 0;
      if (index > offset)
        result.push({ value: part.value.slice(offset, index), kind: "plain" });
      result.push({ value: match[0], kind: "keyword" });
      offset = index + match[0].length;
    }
    if (offset < part.value.length)
      result.push({ value: part.value.slice(offset), kind: "plain" });
    return result;
  });
  if (comment) expanded.push({ value: comment, kind: "comment" });
  return expanded;
}

function CodeViewer({ file }: { file: CodeFileDetail | undefined }) {
  if (!file) {
    return (
      <EmptyState
        icon={<FileCode2 size={23} />}
        title="选择一个文件"
        description="文件内容、Symbol 和版本上下文会在这里同步显示。"
      />
    );
  }
  if (file.binary) {
    return (
      <EmptyState
        icon={<Box size={23} />}
        title="二进制文件"
        description="该版本中的文件不能以文本方式预览。"
      />
    );
  }
  return (
    <div className="code-viewer" aria-label={`${file.path}代码内容`}>
      {(file.content || "").split("\n").map((line, index) => (
        <div className="code-line" key={`${index}-${line.slice(0, 12)}`}>
          <span className="code-line__number">{index + 1}</span>
          <code>
            {syntaxParts(line).map((part, partIndex) => (
              <span
                className={`syntax-${part.kind}`}
                key={`${partIndex}-${part.value}`}
              >
                {part.value}
              </span>
            ))}
          </code>
        </div>
      ))}
    </div>
  );
}

function RefPicker({
  currentRef,
  branches,
  commits,
  open,
  onOpenChange,
  onSelect,
  onCompare,
  onSync,
  syncing,
}: {
  currentRef: string;
  branches: Array<{ name: string; head_sha: string }>;
  commits: GitCommit[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (ref: string) => void;
  onCompare: () => void;
  onSync: () => void;
  syncing: boolean;
}) {
  const [tab, setTab] = useState<RefTab>("current");
  const [query, setQuery] = useState("");
  const [commitPage, setCommitPage] = useState(0);
  const filteredCommits = commits.filter(
    (commit) =>
      commit.message.toLowerCase().includes(query.toLowerCase()) ||
      commit.sha.toLowerCase().startsWith(query.toLowerCase()),
  );
  const commitPageCount = Math.max(1, Math.ceil(filteredCommits.length / 20));
  const pagedCommits = filteredCommits.slice(
    commitPage * 20,
    commitPage * 20 + 20,
  );
  useEffect(() => {
    setCommitPage(0);
  }, [query, tab]);
  useEffect(() => {
    if (commitPage >= commitPageCount) setCommitPage(commitPageCount - 1);
  }, [commitPage, commitPageCount]);
  return (
    <div className="ref-picker">
      <button
        className="ref-picker__trigger"
        onClick={() => onOpenChange(!open)}
      >
        <GitBranch size={16} />
        <span>{currentRef === "HEAD" ? "当前版本" : currentRef}</span>
        <ChevronDown size={15} />
      </button>
      {open ? (
        <section className="ref-popover" aria-label="分支与提交">
          <div className="ref-popover__tabs">
            {(["current", "branches", "commits"] as RefTab[]).map((value) => (
              <button
                className={tab === value ? "is-active" : ""}
                key={value}
                onClick={() => setTab(value)}
              >
                {value === "current"
                  ? "当前"
                  : value === "branches"
                    ? "分支"
                    : "提交"}
              </button>
            ))}
          </div>
          {tab === "current" ? (
            <button
              className="ref-option is-selected"
              onClick={() => {
                onSelect("HEAD");
                onOpenChange(false);
              }}
            >
              <CircleDot size={16} />
              <span>
                <strong>当前工作版本</strong>
                <small>HEAD · 默认进入</small>
              </span>
              <Check size={15} />
            </button>
          ) : null}
          {tab === "branches"
            ? branches.map((branch) => (
                <button
                  className={`ref-option ${currentRef === branch.name ? "is-selected" : ""}`}
                  key={branch.name}
                  onClick={() => {
                    onSelect(branch.name);
                    onOpenChange(false);
                  }}
                >
                  <GitBranch size={16} />
                  <span>
                    <strong>{branch.name}</strong>
                    <small>{branch.head_sha.slice(0, 10)}</small>
                  </span>
                  {currentRef === branch.name ? <Check size={15} /> : null}
                </button>
              ))
            : null}
          {tab === "commits" ? (
            <>
              <label className="ref-search">
                <Search size={15} />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索提交或 SHA"
                />
              </label>
              <div className="ref-commit-list">
                {pagedCommits.map((commit) => (
                  <button
                    className={`ref-option ${currentRef === commit.sha ? "is-selected" : ""}`}
                    key={commit.sha}
                    onClick={() => {
                      onSelect(commit.sha);
                      onOpenChange(false);
                    }}
                  >
                    <GitCommitHorizontal size={16} />
                    <span>
                      <strong>{commit.message}</strong>
                      <small>
                        {commit.sha.slice(0, 8)} · {commit.author_name}
                      </small>
                    </span>
                  </button>
                ))}
              </div>
              {filteredCommits.length > 20 ? (
                <nav className="ref-commit-pager" aria-label="提交记录分页">
                  <button
                    type="button"
                    disabled={commitPage === 0}
                    onClick={() =>
                      setCommitPage((value) => Math.max(0, value - 1))
                    }
                  >
                    <ChevronLeft size={14} />
                  </button>
                  <span>
                    {commitPage + 1} / {commitPageCount}
                  </span>
                  <button
                    type="button"
                    disabled={commitPage + 1 >= commitPageCount}
                    onClick={() =>
                      setCommitPage((value) =>
                        Math.min(commitPageCount - 1, value + 1),
                      )
                    }
                  >
                    <ChevronRight size={14} />
                  </button>
                </nav>
              ) : null}
            </>
          ) : null}
          <div className="ref-popover__actions">
            <button onClick={onSync} disabled={syncing}>
              <RefreshCw size={16} className={syncing ? "is-spinning" : ""} />
              {syncing ? "同步中" : "同步分支"}
            </button>
            <button onClick={onCompare}>
              <SplitSquareHorizontal size={16} />
              比较两个版本
            </button>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function ImportRepositoryDialog({
  open,
  projectId,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  open: boolean;
  projectId: string;
  pending: boolean;
  error?: string;
  onClose: () => void;
  onSubmit: (source: string, branch: string) => void;
}) {
  const [sourceMode, setSourceMode] = useState<"local" | "remote">("local");
  const [source, setSource] = useState("");
  const [branch, setBranch] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (source.trim()) onSubmit(source.trim(), branch.trim());
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="导入代码仓库"
      description="支持本地 Git 目录或远程 Git 地址。项目与权限会自动关联。"
    >
      <form className="form repository-import" onSubmit={submit}>
        <div
          className="repository-import__source-tabs"
          role="tablist"
          aria-label="仓库来源"
        >
          <button
            type="button"
            className={sourceMode === "local" ? "is-active" : ""}
            onClick={() => {
              setSourceMode("local");
              setSource("");
            }}
          >
            <FolderInput size={16} />
            本地目录
          </button>
          <button
            type="button"
            className={sourceMode === "remote" ? "is-active" : ""}
            onClick={() => {
              setSourceMode("remote");
              setSource("");
            }}
          >
            <GitBranch size={16} />
            Git 地址
          </button>
        </div>
        <p className="repository-import__project">
          导入后归入项目 <strong>{projectId}</strong>
          ，该项目的会话、文档与实验可直接绑定此仓库。
        </p>
        <label>
          <span>
            {sourceMode === "local" ? "本地仓库目录" : "Git 仓库地址"}{" "}
            <b>必填</b>
          </span>
          <input
            autoFocus
            value={source}
            onChange={(event) => setSource(event.target.value)}
            placeholder={
              sourceMode === "local"
                ? "/Users/name/research-project"
                : "https://github.com/org/research-project.git"
            }
          />
        </label>
        <label>
          <span>
            指定分支 <em>可选</em>
          </span>
          <input
            value={branch}
            onChange={(event) => setBranch(event.target.value)}
            placeholder="留空时自动识别默认分支"
          />
        </label>
        {error ? <p className="form-error">{error}</p> : null}
        <div className="dialog__actions">
          <Button type="button" variant="quiet" onClick={onClose}>
            取消
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={!source.trim() || pending}
          >
            <FolderInput size={16} />
            {pending ? "正在加入队列" : "开始导入"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function IndexTaskRail({
  workflow,
  open,
  onToggle,
}: {
  workflow?: IngestionWorkflow;
  open: boolean;
  onToggle: () => void;
}) {
  const stageIndex =
    workflow?.status === "completed"
      ? 3
      : workflow?.stage === "publish"
        ? 2
        : 1;
  const stages = [
    { title: "解析与采集", detail: "读取文件、提交与基础元数据" },
    { title: "理解与关联", detail: "建立 Symbol、调用与来源关系" },
    { title: "验证与入库", detail: "校验索引并发布可检索版本" },
  ];
  return (
    <section className={`index-task-rail ${open ? "is-open" : ""}`}>
      <button className="index-task-rail__handle" onClick={onToggle}>
        <span>
          {workflow?.status === "failed" ? (
            <AlertCircle size={17} />
          ) : workflow?.status === "completed" ? (
            <Check size={17} />
          ) : (
            <LoaderCircle size={17} />
          )}
          <strong>索引任务</strong>
          <small>
            {workflow
              ? workflow.status === "completed"
                ? "已完成"
                : workflow.stage
              : "暂无任务"}
          </small>
        </span>
        {open ? <X size={16} /> : <ChevronRight size={16} />}
      </button>
      {open ? (
        <div className="index-stages">
          {stages.map((stage, index) => (
            <article
              className={`${index < stageIndex ? "is-done" : index === stageIndex ? "is-current" : ""}`}
              key={stage.title}
            >
              <span>
                {index < stageIndex ? <Check size={14} /> : index + 1}
              </span>
              <div>
                <strong>{stage.title}</strong>
                <small>{stage.detail}</small>
              </div>
              <em>
                {index < stageIndex
                  ? "完成"
                  : index === stageIndex
                    ? "进行中"
                    : "待开始"}
              </em>
            </article>
          ))}
          {workflow?.error ? (
            <p className="index-error">
              {cleanEvidenceText(workflow.error, 260)}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function CompareView({
  comparison,
  selectedPath,
  onSelectPath,
}: {
  comparison?: CodeComparison;
  selectedPath: string | null;
  onSelectPath: (path: string) => void;
}) {
  if (!comparison) {
    return (
      <EmptyState
        icon={<SplitSquareHorizontal size={24} />}
        title="选择两个版本进行比较"
        description="文件变化和 Diff 会在同一工作区中联动显示。"
      />
    );
  }
  return (
    <div className="compare-workspace">
      <aside className="changed-files">
        <header>{comparison.changed_files.length} 个变更文件</header>
        {comparison.changed_files.map((file) => (
          <button
            className={selectedPath === file.path ? "is-selected" : ""}
            key={`${file.status}-${file.path}`}
            onClick={() => onSelectPath(file.path)}
          >
            <span className={`diff-status diff-status--${file.status[0]}`}>
              {file.status[0]}
            </span>
            <span>{file.path}</span>
          </button>
        ))}
      </aside>
      <pre className="diff-viewer">
        {(comparison.diff || "选择左侧文件查看统一 Diff")
          .split("\n")
          .map((line, index) => (
            <code
              className={
                line.startsWith("+")
                  ? "diff-add"
                  : line.startsWith("-")
                    ? "diff-delete"
                    : line.startsWith("@@")
                      ? "diff-hunk"
                      : ""
              }
              key={`${index}-${line}`}
            >
              {line || " "}
            </code>
          ))}
      </pre>
    </div>
  );
}

export function CodeWorkspace() {
  const { projectId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const requestedFile = searchParams.get("file");
  const requestedRepository = searchParams.get("repository");
  const client = useQueryClient();
  const [importOpen, setImportOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [queuedWorkflow, setQueuedWorkflow] = useState("");
  const repositoriesQuery = useQuery({
    queryKey: ["repositories", projectId],
    queryFn: () => api.code.repositories(projectId),
    refetchInterval: queuedWorkflow ? 2500 : false,
  });
  const repositories = repositoriesQuery.data || [];
  const [repositoryId, setRepositoryId] = useState(requestedRepository || "");
  const repository: Repository | undefined =
    repositories.find((item) => item.id === repositoryId) || repositories[0];
  const activeRepositoryId = repository?.id || "";
  const [currentRef, setCurrentRef] = useState("HEAD");
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [treeQuery, setTreeQuery] = useState("");
  const [refOpen, setRefOpen] = useState(false);
  const [mode, setMode] = useState<ViewMode>("browse");
  const [indexOpen, setIndexOpen] = useState(false);
  const [baseRef, setBaseRef] = useState("HEAD");
  const [targetRef, setTargetRef] = useState("HEAD");

  useEffect(() => {
    if (
      requestedRepository &&
      repositories.some((item) => item.id === requestedRepository)
    ) {
      setRepositoryId(requestedRepository);
      return;
    }
    if (!repositoryId && repositories[0]) setRepositoryId(repositories[0].id);
  }, [repositories, repositoryId, requestedRepository]);
  useEffect(() => {
    setCurrentRef("HEAD");
    setSelectedPath(null);
    setMode("browse");
  }, [activeRepositoryId]);

  const refsQuery = useQuery({
    queryKey: ["code-refs", activeRepositoryId],
    queryFn: () => api.code.refs(activeRepositoryId),
    enabled: Boolean(activeRepositoryId),
  });
  const historyQuery = useQuery({
    queryKey: ["code-history", activeRepositoryId],
    queryFn: () => api.code.history(activeRepositoryId),
    enabled: Boolean(activeRepositoryId),
  });
  const graphQuery = useQuery({
    queryKey: ["code-rag-graph", projectId, activeRepositoryId],
    queryFn: () => api.graph.snapshot(projectId, 500, "code", "relations"),
    enabled: Boolean(projectId && activeRepositoryId && mode === "graph"),
    placeholderData: (previous) => previous,
    retry: false,
  });
  const latestCommitSha = historyQuery.data?.[0]?.sha || "";
  const graphCommitDetailQuery = useQuery({
    queryKey: ["code-rag-commit-detail", activeRepositoryId, latestCommitSha],
    queryFn: () => api.code.commitDetail(activeRepositoryId, latestCommitSha),
    enabled: Boolean(activeRepositoryId && latestCommitSha && mode === "graph"),
    retry: false,
  });
  const filesQuery = useQuery({
    queryKey: ["code-files", activeRepositoryId, currentRef],
    queryFn: () => api.code.files(activeRepositoryId, currentRef),
    enabled: Boolean(activeRepositoryId && mode === "browse"),
  });
  useEffect(() => {
    const files = filesQuery.data || [];
    if (requestedFile && files.some((file) => file.path === requestedFile)) {
      setSelectedPath(requestedFile);
      return;
    }
    if (!selectedPath && files.length) {
      const preferred =
        files.find((file) => /(^|\/)README\.md$/i.test(file.path)) ||
        files.find((file) => /\.(py|ts|tsx|rs|go|java)$/i.test(file.path)) ||
        files[0];
      setSelectedPath(preferred.path);
    }
  }, [filesQuery.data, requestedFile, selectedPath]);
  const fileQuery = useQuery({
    queryKey: ["code-file", activeRepositoryId, currentRef, selectedPath],
    queryFn: () =>
      api.code.file(activeRepositoryId, selectedPath || "", currentRef),
    enabled: Boolean(activeRepositoryId && selectedPath && mode === "browse"),
  });
  const workflowsQuery = useQuery({
    queryKey: ["ingestion-workflows"],
    queryFn: api.code.workflows,
    refetchInterval: queuedWorkflow ? 2000 : false,
  });
  const workflow = workflowsQuery.data?.find(
    (item) =>
      item.repository_id === activeRepositoryId && item.kind === "repository",
  );
  const queued = workflowsQuery.data?.find(
    (item) => item.id === queuedWorkflow,
  );
  useEffect(() => {
    if (queued && ["completed", "failed"].includes(queued.status)) {
      setQueuedWorkflow("");
      client.invalidateQueries({ queryKey: ["repositories", projectId] });
    }
  }, [client, projectId, queued]);
  const importMutation = useMutation({
    mutationFn: ({ source, branch }: { source: string; branch: string }) =>
      api.code.ingestRepository(projectId, source, branch),
    onSuccess: (result) => {
      setQueuedWorkflow(result.workflow_id);
      setImportOpen(false);
      client.invalidateQueries({ queryKey: ["ingestion-workflows"] });
    },
  });
  const syncMutation = useMutation({
    mutationFn: () => api.code.syncHistory(activeRepositoryId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["code-refs", activeRepositoryId] });
      client.invalidateQueries({
        queryKey: ["code-history", activeRepositoryId],
      });
      client.invalidateQueries({
        queryKey: ["code-files", activeRepositoryId],
      });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => api.code.deleteRepository(activeRepositoryId),
    onSuccess: () => {
      setDeleteOpen(false);
      setRepositoryId("");
      setSelectedPath(null);
      client.invalidateQueries({ queryKey: ["repositories", projectId] });
      client.invalidateQueries({ queryKey: ["ingestion-workflows"] });
    },
  });
  const relatedQuery = useQuery({
    queryKey: ["code-related", projectId, selectedPath],
    queryFn: () =>
      api.search.project(projectId, selectedPath?.split("/").pop() || ""),
    enabled: Boolean(projectId && selectedPath && mode === "browse"),
  });
  const comparisonListQuery = useQuery({
    queryKey: ["code-compare", activeRepositoryId, baseRef, targetRef],
    queryFn: () => api.code.compare(activeRepositoryId, baseRef, targetRef),
    enabled: Boolean(
      activeRepositoryId && mode === "compare" && baseRef !== targetRef,
    ),
  });
  const comparisonFileQuery = useQuery({
    queryKey: [
      "code-compare-file",
      activeRepositoryId,
      baseRef,
      targetRef,
      selectedPath,
    ],
    queryFn: () =>
      api.code.compare(
        activeRepositoryId,
        baseRef,
        targetRef,
        selectedPath || "",
      ),
    enabled: Boolean(
      activeRepositoryId &&
      mode === "compare" &&
      baseRef !== targetRef &&
      selectedPath,
    ),
  });

  const openCompare = () => {
    const fallbackTarget =
      refsQuery.data?.branches[0]?.name ||
      historyQuery.data?.[0]?.sha ||
      "HEAD";
    setBaseRef("HEAD");
    setTargetRef(currentRef === "HEAD" ? fallbackTarget : currentRef);
    setMode("compare");
    setRefOpen(false);
    setSelectedPath(null);
  };
  const selectCompareFile = (path: string) => setSelectedPath(path);
  const selectRepository = (value: string) => {
    setRepositoryId(value);
    setCurrentRef("HEAD");
  };
  const comparison = comparisonListQuery.data
    ? {
        ...comparisonListQuery.data,
        ...(comparisonFileQuery.data
          ? {
              path: comparisonFileQuery.data.path,
              diff: comparisonFileQuery.data.diff,
              base_file: comparisonFileQuery.data.base_file,
              target_file: comparisonFileQuery.data.target_file,
            }
          : {}),
      }
    : undefined;
  const related = (relatedQuery.data?.results || [])
    .filter((item) => item.source !== "code")
    .slice(0, 8);

  if (repositoriesQuery.isLoading) {
    return <div className="page-loading">正在读取项目代码仓库…</div>;
  }

  if (repositoriesQuery.isError) {
    return (
      <EmptyState
        icon={<AlertCircle size={24} />}
        title="代码仓库不可用"
        description="当前身份未授权、项目不存在或仓库服务不可用；不会显示缓存仓库或开放写操作。"
        action={
          <Button
            variant="secondary"
            onClick={() => void repositoriesQuery.refetch()}
          >
            <RefreshCw size={16} />
            重试读取
          </Button>
        }
      />
    );
  }

  if (!repositories.length) {
    return (
      <>
        <EmptyState
          icon={<Code2 size={24} />}
          title="项目还没有代码仓库"
          description="导入本地 Git 目录或远程仓库后，再浏览文件、分支、提交与 Symbol。"
          action={
            <Button variant="primary" onClick={() => setImportOpen(true)}>
              <FolderInput size={16} />
              导入仓库
            </Button>
          }
        />
        <ImportRepositoryDialog
          open={importOpen}
          projectId={projectId}
          pending={importMutation.isPending}
          error={
            importMutation.error instanceof Error
              ? importMutation.error.message
              : undefined
          }
          onClose={() => setImportOpen(false)}
          onSubmit={(source, branch) =>
            importMutation.mutate({ source, branch })
          }
        />
      </>
    );
  }

  return (
    <div
      className={`code-workspace ${mode === "graph" ? "is-graph-mode" : ""}`}
    >
      <header className="workspace-toolbar">
        <div>
          <h1>代码与版本</h1>
          <span>
            {repository
              ? `${repository.name} · ${repository.default_branch || "HEAD"}`
              : "读取仓库中…"}
          </span>
        </div>
        <div className="workspace-toolbar__controls">
          <select
            aria-label="选择仓库"
            value={activeRepositoryId}
            onChange={(event) => selectRepository(event.target.value)}
          >
            {repositories.map((item) => (
              <option key={item.id} value={item.id}>
                {repositoryLabel(item).replace(/\.git$/, "")}
              </option>
            ))}
          </select>
          <Button variant="quiet" onClick={() => setImportOpen(true)}>
            <Plus size={16} />
            导入仓库
          </Button>
          <Button
            variant="quiet"
            onClick={() => setDeleteOpen(true)}
            disabled={!activeRepositoryId}
          >
            <Trash2 size={16} />
            移除仓库
          </Button>
          <nav className="code-view-switch" aria-label="代码工作区视图">
            <button
              type="button"
              className={mode === "browse" ? "is-active" : ""}
              onClick={() => setMode("browse")}
            >
              <Files size={15} />
              文件
            </button>
            <button
              type="button"
              className={mode === "graph" ? "is-active" : ""}
              onClick={() => setMode("graph")}
            >
              <Network size={15} />
              仓库图谱
            </button>
            <button
              type="button"
              className={mode === "compare" ? "is-active" : ""}
              onClick={openCompare}
            >
              <SplitSquareHorizontal size={15} />
              比较
            </button>
          </nav>
          {mode === "browse" ? (
            <RefPicker
              currentRef={currentRef}
              branches={refsQuery.data?.branches || []}
              commits={historyQuery.data || []}
              open={refOpen}
              onOpenChange={setRefOpen}
              onSelect={(ref) => {
                setCurrentRef(ref);
                setSelectedPath(null);
              }}
              onCompare={openCompare}
              onSync={() => syncMutation.mutate()}
              syncing={syncMutation.isPending}
            />
          ) : mode === "compare" ? (
            <Button variant="quiet" onClick={() => setMode("browse")}>
              <X size={16} />
              退出比较
            </Button>
          ) : null}
        </div>
      </header>

      {repository ? (
        <section
          className="code-repository-summary"
          aria-label="仓库索引权威状态"
        >
          <header>
            <div>
              <Code2 size={18} />
              <span>
                <strong>{repository.name}</strong>
                <small>项目仓库 · ACL 范围内</small>
              </span>
            </div>
            <Status value={repositoryIndexPresentation(repository).tone}>
              {repositoryIndexPresentation(repository).label}
            </Status>
          </header>
          <dl>
            <div>
              <dt>Ref</dt>
              <dd>
                {refsQuery.isError
                  ? "读取失败"
                  : currentRef === "HEAD"
                    ? `${refsQuery.data?.default_branch || repository.default_branch || "HEAD"} · ${(refsQuery.data?.head_sha || repository.head_commit || "未发现").slice(0, 10)}`
                    : currentRef}
              </dd>
            </div>
            <div>
              <dt>Index</dt>
              <dd>{repositoryIndexPresentation(repository).detail}</dd>
            </div>
            <div>
              <dt>Generation</dt>
              <dd>{repository.active_generation_id || "未发布"}</dd>
            </div>
            <div>
              <dt>文件 / Symbol</dt>
              <dd>
                {repository.stats.files || 0} / {repository.stats.symbols || 0}
              </dd>
            </div>
          </dl>
          {repository.last_error || refsQuery.isError ? (
            <p className="code-repository-summary__error" role="alert">
              <AlertCircle size={15} />
              {repository.last_error
                ? "最近索引失败；详细错误未在仓库总览展开。"
                : "Ref 状态读取失败；当前不会假定 HEAD 或分支可用。"}
            </p>
          ) : null}
          <div className="code-repository-summary__actions">
            <button
              type="button"
              onClick={() => {
                void repositoriesQuery.refetch();
                void refsQuery.refetch();
              }}
              disabled={repositoriesQuery.isFetching || refsQuery.isFetching}
            >
              <RefreshCw
                size={14}
                className={
                  repositoriesQuery.isFetching || refsQuery.isFetching
                    ? "is-spinning"
                    : ""
                }
              />
              刷新状态
            </button>
            <button type="button" onClick={() => setIndexOpen(true)}>
              <History size={14} /> 查看索引任务
            </button>
          </div>
        </section>
      ) : null}

      {mode === "compare" ? (
        <>
          <div className="compare-toolbar">
            <label>
              <span>基准</span>
              <select
                value={baseRef}
                onChange={(event) => setBaseRef(event.target.value)}
              >
                <option value="HEAD">当前版本</option>
                {(refsQuery.data?.branches || []).map((branch) => (
                  <option value={branch.name} key={`base-${branch.name}`}>
                    {branch.name}
                  </option>
                ))}
              </select>
            </label>
            <ChevronRight size={18} />
            <label>
              <span>目标</span>
              <select
                value={targetRef}
                onChange={(event) => setTargetRef(event.target.value)}
              >
                <option value="HEAD">当前版本</option>
                {(refsQuery.data?.branches || []).map((branch) => (
                  <option value={branch.name} key={`target-${branch.name}`}>
                    {branch.name}
                  </option>
                ))}
                {(historyQuery.data || []).slice(0, 40).map((commit) => (
                  <option value={commit.sha} key={commit.sha}>
                    {commit.sha.slice(0, 8)} · {commit.message}
                  </option>
                ))}
              </select>
            </label>
            <span className="historical-note">只读比较，不修改工作树</span>
          </div>
          <CompareView
            comparison={comparison}
            selectedPath={selectedPath}
            onSelectPath={selectCompareFile}
          />
        </>
      ) : mode === "graph" && repository ? (
        <CodeRagGraphPanel
          repository={repository}
          refs={refsQuery.data}
          history={historyQuery.data || []}
          commitDetails={
            graphCommitDetailQuery.data ? [graphCommitDetailQuery.data] : []
          }
          snapshot={graphQuery.data}
          loading={graphQuery.isLoading}
          error={graphQuery.isError}
          onRetry={() => void graphQuery.refetch()}
          onOpenFile={(path, version) => {
            const matchingCommit = (historyQuery.data || []).find(
              (commit) => commit.sha === version,
            );
            setCurrentRef(matchingCommit?.sha || "HEAD");
            setSelectedPath(path);
            setMode("browse");
          }}
        />
      ) : (
        <div className="code-browser">
          <aside className="code-sidebar">
            <label className="code-tree-search">
              <Search size={15} />
              <input
                value={treeQuery}
                onChange={(event) => setTreeQuery(event.target.value)}
                placeholder="筛选文件"
              />
            </label>
            <div className="code-sidebar__summary">
              <Files size={15} />
              <span>
                {filesQuery.data?.length || repository?.stats.files || 0} 个文件
              </span>
              {currentRef !== "HEAD" ? <em>历史只读版本</em> : null}
            </div>
            <CodeTree
              files={filesQuery.data || []}
              query={treeQuery}
              selectedPath={selectedPath}
              onSelect={setSelectedPath}
            />
            {filesQuery.isError ? (
              <p className="form-error code-tree-error">
                {filesQuery.error instanceof Error
                  ? filesQuery.error.message
                  : "文件列表载入失败"}
              </p>
            ) : null}
          </aside>

          <section className="code-main">
            <header className="file-toolbar">
              <div className="file-breadcrumb">
                {(selectedPath || "选择文件")
                  .split("/")
                  .map((part, index, parts) => (
                    <Fragment key={`${part}-${index}`}>
                      <span>{part}</span>
                      {index < parts.length - 1 ? (
                        <ChevronRight size={13} />
                      ) : null}
                    </Fragment>
                  ))}
              </div>
              <span className="file-ref">
                {fileQuery.data?.commit_sha?.slice(0, 9) ||
                  repository?.head_commit.slice(0, 9)}
              </span>
            </header>
            <CodeViewer file={fileQuery.data} />
          </section>

          {selectedPath ? (
            <aside className="file-inspector">
              <header>
                <div>
                  <FileCode2 size={18} />
                  <h2>文件摘要</h2>
                </div>
                <button
                  onClick={() => setSelectedPath(null)}
                  aria-label="关闭文件详情"
                >
                  <X size={17} />
                </button>
              </header>
              <div className="file-inspector__scroll">
                <section>
                  <h3>{selectedPath.split("/").pop()}</h3>
                  <p>
                    {fileQuery.data?.language || "文本"}文件
                    {fileQuery.data?.symbols?.length
                      ? `，包含 ${fileQuery.data.symbols.length} 个可定位 Symbol。`
                      : "，当前版本未识别到结构化 Symbol。"}
                  </p>
                  <dl className="file-facts">
                    <div>
                      <dt>路径</dt>
                      <dd>{selectedPath}</dd>
                    </div>
                    <div>
                      <dt>版本</dt>
                      <dd>{currentRef === "HEAD" ? "当前版本" : currentRef}</dd>
                    </div>
                    <div>
                      <dt>大小</dt>
                      <dd>
                        {fileQuery.data?.size
                          ? `${Math.ceil(fileQuery.data.size / 1024)} KB`
                          : "—"}
                      </dd>
                    </div>
                  </dl>
                  <Link
                    className="button button--secondary code-whiteboard-link"
                    to={`/p/${encodeURIComponent(projectId)}/map?code=${encodeURIComponent(selectedPath)}&repository=${encodeURIComponent(activeRepositoryId)}`}
                  >
                    <Braces size={16} />
                    在代码白板查看逻辑与证据
                  </Link>
                </section>
                {fileQuery.data?.symbols?.length ? (
                  <section>
                    <h3>重要 Symbol</h3>
                    <div className="symbol-list">
                      {(fileQuery.data?.symbols || [])
                        .slice(0, 12)
                        .map((symbol) => (
                          <button
                            key={`${symbol.qualified_name}-${symbol.start_line}`}
                          >
                            <Braces size={15} />
                            <span>
                              <strong>{symbol.name}</strong>
                              <small>
                                {symbol.kind} · L{symbol.start_line}–
                                {symbol.end_line}
                              </small>
                            </span>
                          </button>
                        ))}
                    </div>
                  </section>
                ) : null}
                {related.length ? (
                  <section>
                    <h3>关联证据</h3>
                    <div className="related-evidence">
                      {related.map((item) => (
                        <button key={item.entity_id}>
                          <span
                            className={`source-mark source-mark--${item.source}`}
                          >
                            {item.source === "codex"
                              ? "会"
                              : item.source === "experiment"
                                ? "实"
                                : "文"}
                          </span>
                          <span>
                            <strong>{humanizeSearchTitle(item.title)}</strong>
                            <small>
                              {presentSearchSnippet(
                                item.source,
                                item.title,
                                item.snippet,
                              )}
                            </small>
                          </span>
                        </button>
                      ))}
                    </div>
                  </section>
                ) : null}
              </div>
            </aside>
          ) : null}
        </div>
      )}
      <IndexTaskRail
        workflow={workflow}
        open={indexOpen}
        onToggle={() => setIndexOpen(!indexOpen)}
      />
      <ImportRepositoryDialog
        open={importOpen}
        projectId={projectId}
        pending={importMutation.isPending}
        error={
          importMutation.error instanceof Error
            ? importMutation.error.message
            : undefined
        }
        onClose={() => setImportOpen(false)}
        onSubmit={(source, branch) => importMutation.mutate({ source, branch })}
      />
      <Dialog
        open={deleteOpen}
        onClose={() => !deleteMutation.isPending && setDeleteOpen(false)}
        title="移除代码仓库"
        description="将删除该仓库的索引、版本历史与关系数据；不会删除原始本地目录。"
      >
        <div className="form">
          <p>
            确认从项目中移除{" "}
            <strong>
              {repository ? repositoryLabel(repository) : "该仓库"}
            </strong>
            ？
          </p>
          {deleteMutation.error instanceof Error ? (
            <p className="form-error">{deleteMutation.error.message}</p>
          ) : null}
          <div className="dialog__actions">
            <Button
              variant="quiet"
              onClick={() => setDeleteOpen(false)}
              disabled={deleteMutation.isPending}
            >
              取消
            </Button>
            <Button
              variant="danger"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              <Trash2 size={16} />
              {deleteMutation.isPending ? "正在移除" : "确认移除"}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
