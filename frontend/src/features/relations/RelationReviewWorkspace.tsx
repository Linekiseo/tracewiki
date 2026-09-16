import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  Code2,
  FileDiff,
  GitBranch,
  Link2,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  Button,
  EmptyState,
  ExpandableText,
  Status,
} from "../../components/ui";
import { api } from "../../lib/api";
import { cleanEvidenceText } from "../../lib/presentation";
import type { BindingCandidate } from "../../lib/types";

type ReviewStatus = "unreviewed" | "confirmed" | "rejected";
type ReviewDecision = "confirmed" | "rejected";

function confidenceLabel(value: number) {
  if (value >= 0.9) return "高";
  if (value >= 0.7) return "中";
  return "低";
}

function relationLabel(value: string) {
  const labels: Record<string, string> = {
    path_suffix_unique: "路径唯一对应",
    path_exact: "路径完全对应",
    codex_patch_commit: "代码变更归属提交",
    changed_path_maps_to: "变更路径映射",
  };
  return labels[value] || value.replaceAll("_", " ");
}

function candidateTitle(item: BindingCandidate) {
  const source = cleanEvidenceText(item.source_title, 52) || "Codex 变更";
  const target =
    item.target_path?.split("/").pop() || item.repository_name || "代码实体";
  return `${source} → ${target}`;
}

function mapNodeHref(
  projectId: string,
  board: "codex" | "code",
  entityId: string,
) {
  const params = new URLSearchParams({ board, focus: entityId });
  return `/p/${encodeURIComponent(projectId)}/map?${params.toString()}`;
}

function EvidencePane({
  title,
  subtitle,
  content,
  kind,
}: {
  title: string;
  subtitle: string;
  content: string;
  kind: "source" | "target";
}) {
  return (
    <section className={`review-evidence-pane review-evidence-pane--${kind}`}>
      <header>
        <span>
          {kind === "source" ? <FileDiff size={17} /> : <Code2 size={17} />}
        </span>
        <div>
          <strong>{title}</strong>
          <small>{subtitle}</small>
        </div>
      </header>
      <ExpandableText
        className="review-evidence-pane__reader"
        label={kind === "source" ? "来源证据" : "目标证据"}
        title={`${title} · 完整证据`}
        description={subtitle}
        content={content}
        emptyText="没有可展示的证据内容。"
        previewSize="large"
      />
    </section>
  );
}

export function RelationReviewWorkspace() {
  const { projectId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const client = useQueryClient();
  const requestedStatus = searchParams.get("status");
  const initialStatus: ReviewStatus =
    requestedStatus === "confirmed" || requestedStatus === "rejected"
      ? requestedStatus
      : "unreviewed";
  const [status, setStatus] = useState<ReviewStatus>(initialStatus);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState(
    searchParams.get("relation") || "",
  );
  const [note, setNote] = useState("");
  const [pendingDecision, setPendingDecision] = useState<ReviewDecision | null>(
    null,
  );
  const [notice, setNotice] = useState("");
  const [bulkConfirmationOpen, setBulkConfirmationOpen] = useState(false);
  const [page, setPage] = useState(0);
  const pageSize = 25;
  const requestedRelation = searchParams.get("relation") || "";
  useEffect(() => {
    const nextStatus: ReviewStatus =
      requestedStatus === "confirmed" || requestedStatus === "rejected"
        ? requestedStatus
        : "unreviewed";
    if (status !== nextStatus) setStatus(nextStatus);
    if (selectedId !== requestedRelation) setSelectedId(requestedRelation);
    setNote("");
    setPendingDecision(null);
    // URL state is the review-navigation authority for browser back/forward.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedRelation, requestedStatus]);
  const candidatesQuery = useQuery({
    queryKey: ["relation-review", projectId, status],
    queryFn: () => api.evidence.bindings(projectId, status),
    enabled: Boolean(projectId),
  });
  const statsQuery = useQuery({
    queryKey: ["binding-stats", projectId],
    queryFn: () => api.evidence.bindingStats(projectId),
    enabled: Boolean(projectId),
  });
  const candidates = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (candidatesQuery.data || []).filter(
      (item) =>
        !needle ||
        `${item.source_title} ${item.changed_path} ${item.target_path} ${item.repository_name}`
          .toLowerCase()
          .includes(needle),
    );
  }, [candidatesQuery.data, query]);
  const pageCount = Math.max(1, Math.ceil(candidates.length / pageSize));
  const pagedCandidates = candidates.slice(
    page * pageSize,
    (page + 1) * pageSize,
  );

  const selectCandidate = (
    candidateId: string,
    replace = false,
    preserveNotice = false,
  ) => {
    setSelectedId(candidateId);
    setNote("");
    setPendingDecision(null);
    if (!preserveNotice) setNotice("");
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.set("status", status);
        if (candidateId) next.set("relation", candidateId);
        else next.delete("relation");
        return next;
      },
      { replace },
    );
  };

  const selectStatus = (nextStatus: ReviewStatus) => {
    setStatus(nextStatus);
    setSelectedId("");
    setNote("");
    setPendingDecision(null);
    setNotice("");
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("status", nextStatus);
      next.delete("relation");
      return next;
    });
  };

  useEffect(() => {
    if (!candidatesQuery.data) return;
    // A deep-linked relation is authoritative. Never substitute another candidate when
    // the requested id is missing; the detail request renders an explicit fail-closed error.
    if (requestedRelation) return;
    if (
      !selectedId ||
      !candidatesQuery.data.some((item) => item.id === selectedId)
    ) {
      selectCandidate(candidatesQuery.data[0]?.id || "", true, true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidatesQuery.data, requestedRelation, selectedId]);
  useEffect(() => {
    setPage(0);
  }, [query, status]);

  const selectedIndex = candidates.findIndex((item) => item.id === selectedId);
  const selected = (candidatesQuery.data || []).find(
    (item) => item.id === selectedId,
  );
  useEffect(() => {
    if (selectedIndex >= 0) setPage(Math.floor(selectedIndex / pageSize));
  }, [selectedId, selectedIndex]);
  const detailQuery = useQuery({
    queryKey: ["binding-detail", selectedId],
    queryFn: () => api.evidence.binding(selectedId),
    enabled: Boolean(selectedId),
  });
  const detailCandidate = detailQuery.data || selected;
  const detail =
    detailCandidate &&
    detailCandidate.project_id === projectId &&
    detailCandidate.review_status === status
      ? detailCandidate
      : undefined;
  const invalidRelation = Boolean(
    selectedId &&
    (detailQuery.isError ||
      (detailQuery.isSuccess && detailCandidate && !detail)),
  );
  const reviewMutation = useMutation({
    mutationFn: (decision: ReviewDecision) =>
      api.evidence.reviewBinding(selectedId, decision, note),
    onSuccess: async () => {
      const reviewedTitle = detail ? candidateTitle(detail) : selectedId;
      const next =
        selectedIndex >= 0
          ? candidates[selectedIndex + 1] || candidates[selectedIndex - 1]
          : undefined;
      setPendingDecision(null);
      selectCandidate(next?.id || "", true, true);
      setNotice(`已保存 ${reviewedTitle} 的复核判定。`);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["relation-review", projectId] }),
        client.invalidateQueries({ queryKey: ["binding-stats", projectId] }),
        client.invalidateQueries({ queryKey: ["binding-detail", selectedId] }),
      ]);
    },
  });
  const scanMutation = useMutation({
    mutationFn: async () => {
      const started = await api.evidence.scanBindings(projectId);
      setNotice("候选扫描已转入后台；复核队列仍可浏览，进度会持续更新。");
      for (let attempt = 0; attempt < 600; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        const current = await api.evidence.bindingScanStatus(started.scan_id);
        const processed = current.counts.source_items_processed || 0;
        const total = current.counts.source_items || 0;
        if (current.status === "running") {
          setNotice(
            `后台扫描进行中：已处理 ${processed}/${total || "…"} 条源变更，解析 ${current.counts.paths || 0} 个路径引用。`,
          );
          continue;
        }
        if (current.status === "failed") {
          throw new Error(current.error || "候选扫描失败");
        }
        return current;
      }
      throw new Error("候选扫描仍在后台运行，请稍后刷新复核队列。");
    },
    onSuccess: async (result) => {
      setNotice(
        `候选扫描完成：检查 ${result.counts.source_items || 0} 条源变更，生成或刷新 ${result.counts.candidates || 0} 条候选关系。`,
      );
      await Promise.all([
        client.invalidateQueries({ queryKey: ["relation-review", projectId] }),
        client.invalidateQueries({ queryKey: ["binding-stats", projectId] }),
      ]);
    },
  });
  const bulkReviewMutation = useMutation({
    mutationFn: () =>
      api.evidence.reviewAllBindings(projectId, statsQuery.data?.pending || 0),
    onSuccess: async (result) => {
      setBulkConfirmationOpen(false);
      selectCandidate("", true, true);
      setNotice(
        `已一次确认 ${result.reviewed} 条关系，并建立 ${result.relations_created} 条可审计关系。`,
      );
      await Promise.all([
        client.invalidateQueries({ queryKey: ["relation-review", projectId] }),
        client.invalidateQueries({ queryKey: ["binding-stats", projectId] }),
      ]);
    },
  });
  const moveSelection = (offset: number) => {
    if (selectedIndex < 0) return;
    const next = Math.min(
      candidates.length - 1,
      Math.max(0, selectedIndex + offset),
    );
    selectCandidate(candidates[next]?.id || "");
  };
  const movePage = (nextPage: number) => {
    const next = Math.min(pageCount - 1, Math.max(0, nextPage));
    setPage(next);
    selectCandidate(candidates[next * pageSize]?.id || "");
  };

  return (
    <div
      className="relation-review"
      onKeyDown={(event) => {
        const target = event.target as HTMLElement;
        if (target.closest("input, textarea, select, [contenteditable='true']"))
          return;
        if (event.key === "[" && selectedIndex > 0) moveSelection(-1);
        if (event.key === "]" && selectedIndex < candidates.length - 1)
          moveSelection(1);
      }}
    >
      <aside className="review-queue">
        <header>
          <h1>复核队列</h1>
          <span title={statsQuery.isError ? "复核统计载入失败" : undefined}>
            {statsQuery.data?.pending || 0} 项待处理
          </span>
        </header>
        <label className="review-search">
          <Search size={16} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索关系或文件"
          />
        </label>
        <nav aria-label="复核状态">
          {(
            [
              ["unreviewed", "待复核", statsQuery.data?.pending],
              ["confirmed", "已确认", statsQuery.data?.confirmed],
              ["rejected", "已拒绝", statsQuery.data?.rejected],
            ] as const
          ).map(([value, label, count]) => (
            <button
              className={status === value ? "is-active" : ""}
              key={value}
              onClick={() => selectStatus(value)}
            >
              {label}
              <span>{count || 0}</span>
            </button>
          ))}
        </nav>
        <div className="review-queue__tools">
          <Button
            type="button"
            variant="secondary"
            disabled={scanMutation.isPending || bulkReviewMutation.isPending}
            onClick={() => {
              scanMutation.reset();
              setNotice("");
              scanMutation.mutate();
            }}
          >
            <RefreshCw
              className={scanMutation.isPending ? "is-spinning" : ""}
              size={15}
            />
            {scanMutation.isPending ? "后台扫描中…" : "扫描候选"}
          </Button>
          <Button
            type="button"
            variant="primary"
            disabled={
              status !== "unreviewed" ||
              !statsQuery.data?.pending ||
              scanMutation.isPending ||
              bulkReviewMutation.isPending
            }
            onClick={() => {
              bulkReviewMutation.reset();
              setBulkConfirmationOpen(true);
            }}
          >
            <CheckCircle2 size={15} />
            全部通过
          </Button>
        </div>
        {scanMutation.isError ? (
          <div className="review-inline-state is-error" role="alert">
            <AlertTriangle size={18} />
            <div>
              <strong>候选扫描失败</strong>
              <span>
                {scanMutation.error instanceof Error
                  ? scanMutation.error.message
                  : "请稍后重试"}
              </span>
            </div>
          </div>
        ) : null}
        {(statsQuery.data?.pending || 0) >
        (candidatesQuery.data?.length || 0) ? (
          <p className="review-queue__limit-note">
            当前显示置信度最高的 {candidatesQuery.data?.length || 0}{" "}
            条；“全部通过”会处理完整的 {statsQuery.data?.pending || 0}{" "}
            条待复核队列。
          </p>
        ) : null}
        <div className="review-queue__list">
          {candidatesQuery.isPending ? (
            <div className="review-inline-state" role="status">
              <LoaderCircle className="is-spinning" size={20} />
              正在载入复核队列…
            </div>
          ) : null}
          {candidatesQuery.isError ? (
            <div className="review-inline-state is-error" role="alert">
              <AlertTriangle size={20} />
              <div>
                <strong>复核队列载入失败</strong>
                <span>请检查服务状态后重试。</span>
              </div>
            </div>
          ) : null}
          {pagedCandidates.map((item) => (
            <button
              className={item.id === selectedId ? "is-selected" : ""}
              key={item.id}
              onClick={() => selectCandidate(item.id)}
            >
              <span
                className={`review-confidence review-confidence--${confidenceLabel(item.confidence)}`}
              >
                {item.confidence.toFixed(2)}
              </span>
              <strong>{candidateTitle(item)}</strong>
              <small>
                {item.repository_name} · {relationLabel(item.derivation)}
              </small>
              <ChevronRight size={16} />
            </button>
          ))}
          {candidatesQuery.isSuccess && !candidates.length ? (
            <EmptyState
              icon={<ShieldCheck size={22} />}
              title={`没有${status === "unreviewed" ? "待复核" : status === "confirmed" ? "已确认" : "已拒绝"}关系`}
              description={
                status === "unreviewed"
                  ? "点击“扫描候选”从已摄取的 Code 与 Codex 变更生成复核关系。"
                  : "切换状态查看其他复核记录。"
              }
            />
          ) : null}
        </div>
        {candidates.length > pageSize ? (
          <footer className="review-queue__pagination">
            <button disabled={page === 0} onClick={() => movePage(page - 1)}>
              <ArrowLeft size={15} />
            </button>
            <span>
              {page + 1} / {pageCount}
            </span>
            <button
              disabled={page >= pageCount - 1}
              onClick={() => movePage(page + 1)}
            >
              <ArrowRight size={15} />
            </button>
          </footer>
        ) : null}
      </aside>

      <main className="review-stage">
        <header className="review-stage__nav">
          <button
            disabled={selectedIndex <= 0}
            onClick={() => moveSelection(-1)}
          >
            <ArrowLeft size={16} />
            上一条
          </button>
          <span>
            {selectedIndex >= 0 ? selectedIndex + 1 : 0} / {candidates.length}
          </span>
          <button
            disabled={
              selectedIndex < 0 || selectedIndex === candidates.length - 1
            }
            onClick={() => moveSelection(1)}
          >
            下一条
            <ArrowRight size={16} />
          </button>
        </header>

        {selectedId && detailQuery.isPending && !selected ? (
          <div className="review-stage-state" role="status">
            <LoaderCircle className="is-spinning" size={24} />
            <strong>正在核验关系标识</strong>
            <span>{selectedId}</span>
          </div>
        ) : invalidRelation ? (
          <div className="review-stage-state is-error" role="alert">
            <AlertTriangle size={26} />
            <strong>无法打开指定关系</strong>
            <p>
              关系不存在、不属于当前项目，或不在“
              {status === "unreviewed"
                ? "待复核"
                : status === "confirmed"
                  ? "已确认"
                  : "已拒绝"}
              ”范围。
            </p>
            <code>{selectedId}</code>
            <Button
              type="button"
              variant="secondary"
              onClick={() => selectCandidate("", true)}
            >
              返回当前队列
            </Button>
          </div>
        ) : detail ? (
          <div className="review-stage__scroll">
            <header className="review-subject">
              <div>
                <h2>{candidateTitle(detail)}</h2>
                <p>{detail.changed_path}</p>
              </div>
              <Status value={detail.review_status}>
                {detail.review_status === "unreviewed"
                  ? "待复核"
                  : detail.review_status === "confirmed"
                    ? "已确认"
                    : "已拒绝"}
              </Status>
            </header>

            <section className="relation-pair" aria-label="候选关系">
              <article>
                <span className="relation-pair__icon relation-pair__icon--source">
                  <FileDiff size={21} />
                </span>
                <div>
                  <small>源实体 · Codex 变更</small>
                  <strong>{cleanEvidenceText(detail.source_title, 90)}</strong>
                  <p>{detail.source?.locator || detail.source_thread_id}</p>
                  {detail.source_entity_id ? (
                    <a
                      href={mapNodeHref(
                        projectId,
                        "codex",
                        detail.source_entity_id,
                      )}
                    >
                      在图谱查看来源
                    </a>
                  ) : null}
                </div>
              </article>
              <div className="relation-pair__edge">
                <span>{relationLabel(detail.derivation)}</span>
                <ArrowRight size={23} />
              </div>
              <article>
                <span className="relation-pair__icon relation-pair__icon--target">
                  <Code2 size={21} />
                </span>
                <div>
                  <small>目标实体 · 版本化代码</small>
                  <strong>{detail.target_path}</strong>
                  <p>{detail.target_commit_sha?.slice(0, 12)}</p>
                  <a
                    href={mapNodeHref(
                      projectId,
                      "code",
                      detail.target_entity_id,
                    )}
                  >
                    在图谱查看目标
                  </a>
                </div>
              </article>
            </section>

            <section className="version-alignment">
              <span>
                <GitBranch size={16} />
                源路径
                <strong>{detail.changed_path}</strong>
              </span>
              <span
                className={
                  detail.changed_path === detail.target_path ? "is-aligned" : ""
                }
              >
                {detail.changed_path === detail.target_path ? (
                  <Check size={15} />
                ) : (
                  <Sparkles size={15} />
                )}
                {detail.changed_path === detail.target_path
                  ? "版本路径一致"
                  : "通过派生规则匹配"}
              </span>
              <span>
                目标版本
                <strong>
                  {detail.target_commit_sha?.slice(0, 12) || "当前索引"}
                </strong>
              </span>
            </section>

            <section className="review-evidence">
              <header>
                <h3>证据对照</h3>
                <span>仅根据以下真实来源作出复核决定</span>
              </header>
              <div>
                <EvidencePane
                  kind="source"
                  title={detail.source_title || "Codex 变更"}
                  subtitle={detail.source?.locator || detail.source_thread_id}
                  content={detail.source?.content || detail.changed_path}
                />
                <EvidencePane
                  kind="target"
                  title={detail.target_path || "代码实体"}
                  subtitle={
                    detail.target?.source_uri || detail.target_commit_sha
                  }
                  content={detail.target?.content || detail.target_path}
                />
              </div>
            </section>
          </div>
        ) : (
          <EmptyState
            icon={<Link2 size={23} />}
            title="选择一条候选关系"
            description="源实体、目标实体和证据对照会在这里展开。"
          />
        )}
      </main>

      <aside className="review-decision">
        <header>
          <ShieldCheck size={18} />
          <h2>复核判定</h2>
        </header>
        {notice ? (
          <div className="review-notice" role="status">
            <CheckCircle2 size={17} /> {notice}
          </div>
        ) : null}
        {bulkConfirmationOpen ? (
          <section
            className="review-confirmation review-confirmation--bulk"
            role="alertdialog"
            aria-modal="true"
            aria-label="确认全部通过"
          >
            <strong>确认全部待复核关系？</strong>
            <p>
              将一次确认当前完整队列中的
              <code>{statsQuery.data?.pending || 0}</code>
              条关系，而不只是左侧显示的前 500 条。
            </p>
            <span>
              服务端会重新核对队列数量、来源 generation
              和目标版本；扫描仍在变化或任一证据失效时，整批操作不会保存。
            </span>
            {bulkReviewMutation.isError ? (
              <span role="alert">
                批量确认失败：
                {bulkReviewMutation.error instanceof Error
                  ? bulkReviewMutation.error.message
                  : "请刷新队列后重试"}
              </span>
            ) : null}
            <div>
              <Button
                type="button"
                variant="secondary"
                disabled={bulkReviewMutation.isPending}
                onClick={() => setBulkConfirmationOpen(false)}
              >
                取消
              </Button>
              <Button
                type="button"
                variant="primary"
                disabled={
                  bulkReviewMutation.isPending || !statsQuery.data?.pending
                }
                onClick={() => bulkReviewMutation.mutate()}
              >
                {bulkReviewMutation.isPending
                  ? "正在全部确认…"
                  : `确认全部 ${statsQuery.data?.pending || 0} 条`}
              </Button>
            </div>
          </section>
        ) : null}
        {detail ? (
          <>
            <section>
              <span>系统建议</span>
              <strong>
                {detail.confidence >= 0.7 ? "建议确认" : "需要谨慎检查"}
              </strong>
              <dl>
                <div>
                  <dt>置信度</dt>
                  <dd>
                    {detail.confidence.toFixed(2)} ·{" "}
                    {confidenceLabel(detail.confidence)}
                  </dd>
                </div>
                <div>
                  <dt>匹配依据</dt>
                  <dd>{relationLabel(detail.derivation)}</dd>
                </div>
                <div>
                  <dt>仓库</dt>
                  <dd>{detail.repository_name}</dd>
                </div>
              </dl>
            </section>
            <section>
              <span>判定检查</span>
              <ul>
                <li
                  className={
                    detail.changed_path === detail.target_path ? "is-pass" : ""
                  }
                >
                  <Check size={15} />
                  变更路径与目标路径
                </li>
                <li className={detail.target_commit_sha ? "is-pass" : ""}>
                  <Check size={15} />
                  目标版本可定位
                </li>
                <li
                  className={
                    detail.source?.content && detail.target?.content
                      ? "is-pass"
                      : ""
                  }
                >
                  <Check size={15} />
                  两侧证据可读取
                </li>
              </ul>
            </section>
            <section className="review-note">
              <label htmlFor="review-note">复核备注（拒绝时必填）</label>
              <textarea
                id="review-note"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="记录确认依据或拒绝原因"
              />
            </section>
            <div className="review-actions">
              <Button
                variant="primary"
                disabled={
                  reviewMutation.isPending ||
                  detail.review_status !== "unreviewed"
                }
                onClick={() => {
                  reviewMutation.reset();
                  setPendingDecision("confirmed");
                }}
              >
                <Check size={16} />
                确认关系
              </Button>
              <Button
                variant="secondary"
                disabled={
                  reviewMutation.isPending ||
                  detail.review_status !== "unreviewed"
                }
                onClick={() => {
                  reviewMutation.reset();
                  setPendingDecision("rejected");
                }}
              >
                <X size={16} />
                拒绝
              </Button>
            </div>
            {pendingDecision ? (
              <section
                className="review-confirmation"
                role="alertdialog"
                aria-modal="true"
                aria-label="确认复核判定"
              >
                <strong>
                  {pendingDecision === "confirmed"
                    ? "确认建立人工关系？"
                    : "确认拒绝候选关系？"}
                </strong>
                <p>
                  对象：<code>{detail.id}</code>
                </p>
                <p>审计主体由服务端可信身份写入，不接受浏览器自报 reviewer。</p>
                {pendingDecision === "rejected" && !note.trim() ? (
                  <span role="alert">拒绝前必须填写原因。</span>
                ) : null}
                {reviewMutation.isError ? (
                  <span role="alert">
                    保存失败：
                    {reviewMutation.error instanceof Error
                      ? reviewMutation.error.message
                      : "请稍后重试"}
                  </span>
                ) : null}
                <div>
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={reviewMutation.isPending}
                    onClick={() => setPendingDecision(null)}
                  >
                    返回检查
                  </Button>
                  <Button
                    type="button"
                    variant={
                      pendingDecision === "confirmed" ? "primary" : "danger"
                    }
                    disabled={
                      reviewMutation.isPending ||
                      (pendingDecision === "rejected" && !note.trim())
                    }
                    onClick={() => reviewMutation.mutate(pendingDecision)}
                  >
                    {reviewMutation.isPending
                      ? "正在保存…"
                      : pendingDecision === "confirmed"
                        ? "确认提交"
                        : "确认拒绝"}
                  </Button>
                </div>
              </section>
            ) : null}
          </>
        ) : (
          <p>选择候选关系后再作出判定。</p>
        )}
      </aside>
    </div>
  );
}
