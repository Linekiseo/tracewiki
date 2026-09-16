import {
  ArrowRight,
  Beaker,
  BookOpen,
  Braces,
  Code2,
  FileText,
  GitBranch,
  MessageSquareText,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";

const modules = {
  code: { title: "代码与版本", description: "版本浏览、代码摘要、分支与索引任务正在迁移到新的项目上下文。", hash: "repository", icon: Code2 },
  sessions: { title: "研发会话", description: "Codex 会话时间线与跨来源节点将在此项目范围内呈现。", hash: "codex", icon: MessageSquareText },
  experiments: { title: "实验与数据", description: "实验计划、运行、指标和结果文档统一进入研究迭代。", hash: "experiments", icon: Beaker },
  documents: { title: "科研文档", description: "参考论文、设计文档、实验记录和过程文档按科研语义分类。", hash: "documents", icon: FileText },
  evidence: { title: "证据工作台", description: "代码、会话、实验与文档证据将在同一检查流中核对。", hash: "evidence", icon: BookOpen },
  relations: { title: "关系复核", description: "只呈现需要人工判断的跨来源候选关系。", hash: "relations", icon: GitBranch },
  codex: { title: "Codex 联动", description: "管理项目上下文、执行任务、权限与结构化结果回传。", hash: "integration", icon: Braces },
} as const;

export function LegacyGateway() {
  const { projectId = "", module = "" } = useParams();
  const meta = modules[module as keyof typeof modules] || modules.evidence;
  const Icon = meta.icon;
  return (
    <div className="gateway-page">
      <header className="page-heading">
        <div>
          <span className="eyebrow">PROJECT MODULE</span>
          <h1>{meta.title}</h1>
          <p>{meta.description}</p>
        </div>
      </header>
      <section className="gateway-card">
        <span className="gateway-card__icon"><Icon size={28} /></span>
        <div>
          <h2>新界面正在分模块迁移</h2>
          <p>当前项目上下文已经锁定。迁移期间可进入原功能处理存量数据，旧页面不会再承担项目切换与全局导航。</p>
        </div>
        <a href={`/legacy?project=${encodeURIComponent(projectId)}#${meta.hash}`}>
          打开现有功能 <ArrowRight size={17} />
        </a>
      </section>
      <section className="migration-notes">
        <article><strong>项目范围</strong><span>所有查询和写入均绑定当前项目，不再使用固定 project-rag。</span></article>
        <article><strong>任务式交互</strong><span>默认浏览，只有明确进入编辑或执行动作时才出现表单。</span></article>
        <article><strong>跨来源组织</strong><span>代码、会话、实验与文档围绕研究问题交叉连接。</span></article>
      </section>
      <Link className="text-link" to={`/p/${encodeURIComponent(projectId)}/overview`}>返回项目概览</Link>
    </div>
  );
}
