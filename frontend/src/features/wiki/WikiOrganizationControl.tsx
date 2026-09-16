import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Boxes,
  CheckCircle2,
  DatabaseZap,
  FileStack,
  GitCompareArrows,
  LoaderCircle,
  Network,
  ScanSearch,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button, Status } from "../../components/ui";
import { api } from "../../lib/api";
import type { WikiOrganizationStage, WikiSourceDomain } from "../../lib/types";

const sourceLabels: Record<WikiSourceDomain, string> = {
  code: "代码",
  codex: "会话",
  experiment: "实验",
  notebook: "Notebook",
  document: "文档",
  workspace: "工作区",
};

const typeLabels: Record<string, string> = {
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
  source: "原始来源",
};

function compact(value?: string | null, width = 16) {
  if (!value) return "—";
  const normalized = value.startsWith("sha256:") ? value.slice(7) : value;
  return normalized.length > width ? `${normalized.slice(0, width)}…` : normalized;
}

export function WikiOrganizationControl({
  projectId,
  onPublished,
}: {
  projectId: string;
  onPublished: () => void;
}) {
  const queryClient = useQueryClient();
  const [staged, setStaged] = useState<WikiOrganizationStage | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const previewQuery = useQuery({
    queryKey: ["wiki-organization-preview", projectId],
    queryFn: ({ signal }) => api.wiki.organizationPreview(projectId, signal),
    enabled: Boolean(projectId),
    retry: false,
  });
  const stageMutation = useMutation({
    mutationFn: () => api.wiki.organizationStage(projectId),
    onSuccess: (result) => {
      setStaged(result);
      setConfirmed(false);
    },
  });
  const publishMutation = useMutation({
    mutationFn: (result: WikiOrganizationStage) =>
      api.wiki.publish({
        project_id: projectId,
        generation_id: result.generation_id,
        expected_manifest_sha256: result.manifest_sha256,
        reviewer_authority_sha256: result.reviewer_authority_sha256,
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["wiki-status", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["wiki-pages", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["wiki-organization-preview", projectId] }),
      ]);
      setStaged(null);
      setConfirmed(false);
      onPublished();
    },
  });

  useEffect(() => {
    setStaged(null);
    setConfirmed(false);
  }, [projectId]);

  const preview = previewQuery.data;
  const sourcesReady = preview?.source_summaries.filter(
    (item) => item.availability === "AVAILABLE",
  ).length || 0;
  const roles = useMemo(
    () => (preview?.knowledge_role_counts || []).slice().sort((a, b) => b[1] - a[1]),
    [preview?.knowledge_role_counts],
  );
  const error = stageMutation.error || publishMutation.error;

  return (
    <section className="wiki-organization" aria-label="当前项目知识组织">
      <header className="wiki-organization__header">
        <div>
          <span><Network size={18} /></span>
          <div>
            <small>LIVE PROJECT ORGANIZATION</small>
            <h2>把当前项目编译成可审阅知识结构</h2>
            <p>盘点六类真实来源，按架构、能力、变更、决策、实验、流程和需求组织；预览不会写入，发布必须再次确认。</p>
          </div>
        </div>
        <Status value={preview?.active_snapshot_kind === "live_project" ? "active" : "blocked"}>
          {preview?.active_snapshot_kind === "live_project" ? "当前已是项目知识" : "当前不是项目知识"}
        </Status>
      </header>

      {previewQuery.isLoading ? (
        <div className="wiki-organization__state" role="status">
          <LoaderCircle className="is-spinning" size={18} />正在只读盘点当前项目来源…
        </div>
      ) : previewQuery.isError ? (
        <div className="wiki-organization__state is-error" role="alert">
          <AlertTriangle size={18} />无法形成可信项目盘点：{previewQuery.error.message}
        </div>
      ) : preview ? (
        <>
          <div className="wiki-organization__metrics">
            <article><ScanSearch size={17} /><span><small>已发现来源</small><strong>{sourcesReady}/6</strong></span></article>
            <article><DatabaseZap size={17} /><span><small>组织候选</small><strong>{preview.compiled_candidate_count}/{preview.candidate_count}</strong></span></article>
            <article><FileStack size={17} /><span><small>预期页面</small><strong>{preview.page_count}</strong></span></article>
            <article><GitCompareArrows size={17} /><span><small>替换变化</small><strong>+{preview.added_page_count} / −{preview.removed_page_count}</strong></span></article>
          </div>

          <div className="wiki-organization__sources">
            {preview.source_summaries.map((source) => (
              <article className={`is-${source.availability.toLowerCase()}`} key={source.source}>
                <header>
                  <strong>{sourceLabels[source.source]}</strong>
                  <Status value={source.availability === "AVAILABLE" ? "active" : source.availability === "EMPTY" ? "partial" : "blocked"}>
                    {source.availability === "AVAILABLE" ? "已盘点" : source.availability === "EMPTY" ? "当前为空" : "不可用"}
                  </Status>
                </header>
                <dl>
                  <div><dt>原始实体</dt><dd>{source.raw_entity_count}</dd></div>
                  <div><dt>知识候选</dt><dd>{source.candidate_count}</dd></div>
                </dl>
                <p>{source.roles.length ? source.roles.slice(0, 3).join(" · ") : source.diagnostic || "等待来源数据"}</p>
                <small>{compact(source.generation_id, 22)}</small>
              </article>
            ))}
          </div>

          <div className="wiki-organization__structure">
            <section>
              <header><Boxes size={15} /><strong>知识页面结构</strong><small>{preview.page_count} 页</small></header>
              <div className="wiki-organization__bars">
                {preview.page_type_counts.map(([type, count]) => (
                  <div key={type}>
                    <span>{typeLabels[type] || type}</span>
                    <i><b style={{ width: `${Math.max(6, count / Math.max(preview.page_count, 1) * 100)}%` }} /></i>
                    <strong>{count}</strong>
                  </div>
                ))}
              </div>
            </section>
            <section>
              <header><Network size={15} /><strong>跨来源知识角色</strong><small>{roles.length} 类</small></header>
              <div className="wiki-organization__roles">
                {roles.slice(0, 12).map(([role, count]) => <span key={role}>{role}<b>{count}</b></span>)}
                {!roles.length ? <p>当前来源尚未形成跨来源角色。</p> : null}
              </div>
            </section>
          </div>

          {preview.quarantined_candidate_count || preview.warnings.length ? (
            <div className="wiki-organization__warning">
              <AlertTriangle size={16} />
              <span>
                <strong>{preview.quarantined_candidate_count} 个候选被隔离</strong>
                <small>{preview.warnings.join(" · ") || "不安全或无效内容不会进入知识页。"}</small>
              </span>
            </div>
          ) : (
            <div className="wiki-organization__clean"><ShieldCheck size={16} />全部候选通过当前编译安全合同。</div>
          )}

          <footer className="wiki-organization__actions">
            <div>
              <small>候选 generation</small>
              <strong>{compact(preview.generation_id, 28)}</strong>
              <span>保留 {preview.retained_page_count} 页；显式发布前不会替换当前快照。</span>
            </div>
            {!staged ? (
              <Button
                variant="primary"
                disabled={!preview.candidate_count || stageMutation.isPending}
                onClick={() => stageMutation.mutate()}
              >
                {stageMutation.isPending ? <><LoaderCircle className="is-spinning" size={15} />正在构建候选快照</> : <><ArrowRight size={15} />构建并进入审阅</>}
              </Button>
            ) : (
              <div className="wiki-organization__publish">
                <label>
                  <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
                  我已核对六源计数、隔离项与页面变化，确认切换为当前项目知识快照
                </label>
                <Button
                  variant="primary"
                  disabled={!confirmed || publishMutation.isPending}
                  onClick={() => publishMutation.mutate(staged)}
                >
                  {publishMutation.isPending ? <><LoaderCircle className="is-spinning" size={15} />正在原子发布</> : <><CheckCircle2 size={15} />确认发布 {staged.page_count} 页</>}
                </Button>
              </div>
            )}
          </footer>
          {staged ? (
            <div className="wiki-organization__staged" role="status">
              <ShieldCheck size={16} />候选快照已暂存并通过 manifest 校验；当前 active generation 尚未改变。
            </div>
          ) : null}
          {error ? <div className="wiki-organization__state is-error" role="alert"><AlertTriangle size={16} />{error.message}</div> : null}
        </>
      ) : null}
    </section>
  );
}
