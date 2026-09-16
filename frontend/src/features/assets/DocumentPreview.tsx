import { useQuery } from "@tanstack/react-query";
import {
  Braces,
  Download,
  Eye,
  FileSearch,
  FileText,
  LoaderCircle,
  MessageSquareQuote,
} from "lucide-react";
import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Status } from "../../components/ui";
import { api } from "../../lib/api";
import { cleanEvidenceText } from "../../lib/presentation";
import type { ScientificDocument } from "../../lib/types";

type DocumentView = "reader" | "source" | "claims";

const formatLabels: Record<string, string> = {
  markdown: "Markdown",
  pdf: "PDF",
  docx: "Word",
  html: "HTML",
  text: "纯文本",
  inline_text: "纯文本",
};

const claimStatusLabels: Record<string, string> = {
  reported: "待核验",
  verified: "已验证",
  partially_supported: "部分支持",
  contradicted: "存在矛盾",
  superseded: "已替代",
  potentially_stale: "可能过期",
  insufficient_evidence: "证据不足",
};

function sourceType(document: ScientificDocument) {
  const value = document.source_type?.toLowerCase();
  if (
    value === "inline_text"
    && /^(?:#{1,6}\s+\S|\s*\|.+\|\s*$)/m.test(document.content || "")
  ) {
    return "markdown";
  }
  if (formatLabels[value]) return value;
  const suffix = document.source_uri.split(/[?#]/)[0].split(".").pop()?.toLowerCase();
  if (suffix === "md" || suffix === "markdown") return "markdown";
  if (suffix && formatLabels[suffix]) return suffix;
  return "text";
}

export function documentFormatLabel(document: ScientificDocument) {
  return formatLabels[sourceType(document)] || "文档";
}

function defaultFileName(document: ScientificDocument) {
  const original = document.source_uri.split(/[\\/]/).pop()?.split(/[?#]/)[0];
  if (original && original.includes(".")) return original;
  const extensions: Record<string, string> = {
    markdown: "md",
    pdf: "pdf",
    docx: "docx",
    html: "html",
    text: "txt",
    inline_text: "txt",
  };
  return `${document.title}.${extensions[sourceType(document)] || "txt"}`;
}

function textFromNode(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textFromNode).join("");
  if (node && typeof node === "object" && "props" in node) {
    return textFromNode((node as { props?: { children?: ReactNode } }).props?.children);
  }
  return "";
}

function headingId(children: ReactNode) {
  const slug = textFromNode(children)
    .trim()
    .toLocaleLowerCase()
    .replace(/[^\p{Letter}\p{Number}\s_-]/gu, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-");
  return `document-section-${slug || "untitled"}`;
}

const markdownComponents: Components = {
  h1: ({ children }) => <h1 id={headingId(children)}>{children}</h1>,
  h2: ({ children }) => <h2 id={headingId(children)}>{children}</h2>,
  h3: ({ children }) => <h3 id={headingId(children)}>{children}</h3>,
  h4: ({ children }) => <h4 id={headingId(children)}>{children}</h4>,
  h5: ({ children }) => <h5 id={headingId(children)}>{children}</h5>,
  h6: ({ children }) => <h6 id={headingId(children)}>{children}</h6>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  img: ({ alt, src }) => <img src={src} alt={alt || ""} loading="lazy" />,
  code: ({ children, className }) => {
    const inline = !className && !String(children).includes("\n");
    return inline ? (
      <code>{children}</code>
    ) : (
      <code className={className}>{children}</code>
    );
  },
};

function MarkdownReader({ content }: { content: string }) {
  return (
    <div className="markdown-document" data-testid="markdown-document">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

function ExtractedText({ content }: { content: string }) {
  return (
    <div className="plain-document">
      {content.split(/\n{2,}/).map((paragraph, index) => (
        <p key={`${index}-${paragraph.slice(0, 24)}`}>{paragraph}</p>
      ))}
    </div>
  );
}

function StructuredReader({ document }: { document: ScientificDocument }) {
  const sections = document.sections || [];
  const tables = document.tables || [];
  return (
    <div className="structured-document">
      {sections.map((section) => {
        const Heading = `h${Math.min(6, Math.max(2, section.level + 1))}` as
          | "h2"
          | "h3"
          | "h4"
          | "h5"
          | "h6";
        return (
          <section key={section.id} id={headingId(section.title)}>
            <Heading>{section.title}</Heading>
            <ExtractedText content={section.content} />
          </section>
        );
      })}
      {!sections.length ? <ExtractedText content={document.content || ""} /> : null}
      {tables.map((table) => {
        const rows = Array.from({ length: table.row_count }, (_, rowIndex) =>
          Array.from({ length: table.column_count }, (_, columnIndex) =>
            table.cells.find(
              (cell) => cell.row_index === rowIndex && cell.column_index === columnIndex,
            ),
          ),
        );
        return (
          <figure className="document-table" key={table.id}>
            <figcaption>{table.caption || table.title}</figcaption>
            <div>
              <table>
                <tbody>
                  {rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {row.map((cell, columnIndex) => {
                        const Cell = rowIndex === 0 || cell?.is_header ? "th" : "td";
                        return <Cell key={columnIndex}>{cell?.value || ""}</Cell>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </figure>
        );
      })}
    </div>
  );
}

function ClaimReader({ document }: { document: ScientificDocument }) {
  const claims = document.claims || [];
  return (
    <section className="document-claims">
      <header>
        <h3>研究主张</h3>
        <p>从当前版本中抽取出的可复核结论，可继续关联实验、代码与研发会话。</p>
      </header>
      <div className="claim-list">
        {claims.map((claim) => (
          <article key={claim.id}>
            <span>{claim.claim_type}</span>
            <p>{cleanEvidenceText(claim.content, 2000)}</p>
            <Status value={claim.status}>{claimStatusLabels[claim.status] || claim.status}</Status>
          </article>
        ))}
        {!claims.length ? (
          <div className="document-view-empty">
            <MessageSquareQuote size={22} />
            <strong>暂未提取研究主张</strong>
            <span>正文仍可正常阅读，后续可重新运行主张抽取。</span>
          </div>
        ) : null}
      </div>
    </section>
  );
}

export function DocumentPreview({ document }: { document: ScientificDocument }) {
  const type = sourceType(document);
  const sections = useMemo(() => document.sections || [], [document.sections]);
  const [view, setView] = useState<DocumentView>("reader");
  const [activeSectionId, setActiveSectionId] = useState(sections[0]?.id || "");
  const [downloading, setDownloading] = useState(false);
  const outlineRef = useRef<HTMLElement>(null);
  const workbenchRef = useRef<HTMLDivElement>(null);
  const needsOriginalPreview = type === "pdf" || type === "html";
  const rawQuery = useQuery({
    queryKey: ["document-content", document.id],
    queryFn: () => api.documents.content(document.id),
    enabled: needsOriginalPreview,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const [rawUrl, setRawUrl] = useState("");

  useEffect(() => {
    setView("reader");
    setActiveSectionId(sections[0]?.id || "");
  }, [document.id, sections]);

  useEffect(() => {
    if (!rawQuery.data) {
      setRawUrl("");
      return;
    }
    const value = URL.createObjectURL(rawQuery.data);
    setRawUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [rawQuery.data]);

  useEffect(() => {
    if (view !== "reader" || !sections.length) return;
    const scrollRoot = workbenchRef.current?.closest(".asset-detail-stage");
    if (!(scrollRoot instanceof HTMLElement)) return;
    const targets = sections
      .map((section) => ({
        id: section.id,
        node: window.document.getElementById(headingId(section.title)),
      }))
      .filter(
        (target): target is { id: string; node: HTMLElement } =>
          target.node instanceof HTMLElement,
      );
    if (!targets.length) return;

    let frame = 0;
    const updateActiveSection = () => {
      frame = 0;
      const activationLine = scrollRoot.getBoundingClientRect().top + 72;
      let current = targets[0].id;
      for (const target of targets) {
        if (target.node.getBoundingClientRect().top <= activationLine) {
          current = target.id;
        } else {
          break;
        }
      }
      setActiveSectionId((previous) => (previous === current ? previous : current));
    };
    const queueUpdate = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(updateActiveSection);
    };

    queueUpdate();
    scrollRoot.addEventListener("scroll", queueUpdate, { passive: true });
    window.addEventListener("resize", queueUpdate);
    return () => {
      scrollRoot.removeEventListener("scroll", queueUpdate);
      window.removeEventListener("resize", queueUpdate);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [document.id, sections, view]);

  useEffect(() => {
    if (!activeSectionId || !outlineRef.current) return;
    const activeButton = Array.from(
      outlineRef.current.querySelectorAll<HTMLButtonElement>("button[data-section-id]"),
    ).find((button) => button.dataset.sectionId === activeSectionId);
    if (!activeButton) return;
    const outline = outlineRef.current;
    const itemTop = activeButton.offsetTop;
    const itemBottom = itemTop + activeButton.offsetHeight;
    const visibleTop = outline.scrollTop + 38;
    const visibleBottom = outline.scrollTop + outline.clientHeight - 12;
    const scrollOutline = (top: number) => {
      if (typeof outline.scrollTo === "function") {
        outline.scrollTo({ top, behavior: "smooth" });
      } else {
        outline.scrollTop = top;
      }
    };
    if (itemTop < visibleTop) {
      scrollOutline(Math.max(0, itemTop - 46));
    } else if (itemBottom > visibleBottom) {
      scrollOutline(itemBottom - outline.clientHeight + 18);
    }
  }, [activeSectionId]);

  const sourceLabel = type === "markdown" ? "源码" : "提取文本";
  const readingStats = useMemo(() => {
    const content = document.content || "";
    return {
      characters: content.replace(/\s/g, "").length,
      sections: document.sections?.length || 0,
    };
  }, [document.content, document.sections]);

  async function downloadOriginal() {
    setDownloading(true);
    try {
      const blob = rawQuery.data || (await api.documents.content(document.id));
      const href = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = href;
      anchor.download = defaultFileName(document);
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(href), 0);
    } finally {
      setDownloading(false);
    }
  }

  function scrollToSection(section: NonNullable<ScientificDocument["sections"]>[number]) {
    setActiveSectionId(section.id);
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    window.document.getElementById(headingId(section.title))?.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "start",
    });
  }

  return (
    <div className="document-workbench" ref={workbenchRef}>
      <div className="document-toolbar" aria-label="文档阅读工具">
        <div className="document-view-switcher">
          <button
            className={view === "reader" ? "is-active" : ""}
            onClick={() => setView("reader")}
          >
            <Eye size={15} />
            阅读
          </button>
          <button
            className={view === "source" ? "is-active" : ""}
            onClick={() => setView("source")}
          >
            <Braces size={15} />
            {sourceLabel}
          </button>
          <button
            className={view === "claims" ? "is-active" : ""}
            onClick={() => setView("claims")}
          >
            <MessageSquareQuote size={15} />
            研究主张
            {document.claims?.length ? <em>{document.claims.length}</em> : null}
          </button>
        </div>
        <div className="document-toolbar__meta">
          <span>{readingStats.sections} 个章节</span>
          <span>{readingStats.characters.toLocaleString("zh-CN")} 字</span>
          <button onClick={downloadOriginal} disabled={downloading}>
            {downloading ? <LoaderCircle className="is-spinning" size={15} /> : <Download size={15} />}
            下载原文件
          </button>
        </div>
      </div>

      <div className={`document-layout ${view === "claims" ? "is-claims" : ""}`}>
        {view !== "claims" ? (
          <aside className="document-outline" ref={outlineRef}>
            <h3>文档目录</h3>
            {sections.map((section) => (
              <button
                aria-current={activeSectionId === section.id ? "location" : undefined}
                className={activeSectionId === section.id ? "is-active" : ""}
                data-section-id={section.id}
                key={section.id}
                onClick={() => scrollToSection(section)}
                style={{ paddingInlineStart: 10 + Math.max(0, section.level - 1) * 13 }}
                title={section.title}
              >
                {section.title || `第 ${section.start_line} 行`}
              </button>
            ))}
            {!sections.length ? <p>未提取章节目录</p> : null}
          </aside>
        ) : null}

        <main className="document-reading">
          {view === "source" ? (
            <pre className="document-source"><code>{document.content || "暂无可展示文本。"}</code></pre>
          ) : null}
          {view === "claims" ? <ClaimReader document={document} /> : null}
          {view === "reader" && type === "markdown" ? (
            <MarkdownReader content={document.content || ""} />
          ) : null}
          {view === "reader" && (type === "text" || type === "inline_text") ? (
            <ExtractedText content={document.content || ""} />
          ) : null}
          {view === "reader" && type === "docx" ? (
            <StructuredReader document={document} />
          ) : null}
          {view === "reader" && type === "pdf" ? (
            rawUrl ? (
              <object className="original-document-frame" data={rawUrl} type="application/pdf">
                <ExtractedText content={document.content || ""} />
              </object>
            ) : (
              <div className="document-view-loading">
                {rawQuery.isError ? <FileSearch size={22} /> : <LoaderCircle className="is-spinning" size={22} />}
                <strong>{rawQuery.isError ? "无法载入原始 PDF" : "正在载入 PDF"}</strong>
                <span>{rawQuery.isError ? "可切换到“提取文本”继续阅读。" : "首次打开较大的文件可能需要一点时间。"}</span>
              </div>
            )
          ) : null}
          {view === "reader" && type === "html" ? (
            rawUrl ? (
              <iframe
                className="original-document-frame"
                src={rawUrl}
                sandbox=""
                referrerPolicy="no-referrer"
                title={`${document.title} HTML 预览`}
              />
            ) : (
              <div className="document-view-loading">
                {rawQuery.isError ? <FileSearch size={22} /> : <LoaderCircle className="is-spinning" size={22} />}
                <strong>{rawQuery.isError ? "无法载入原始 HTML" : "正在载入 HTML"}</strong>
                <span>{rawQuery.isError ? "可切换到“提取文本”继续阅读。" : "预览会在隔离环境中打开。"}</span>
              </div>
            )
          ) : null}
          {view === "reader" && !["markdown", "text", "inline_text", "docx", "pdf", "html"].includes(type) ? (
            <div className="document-view-empty">
              <FileText size={22} />
              <strong>使用提取文本阅读此文件</strong>
              <span>当前格式暂不支持保真预览，正文内容仍可检索和阅读。</span>
            </div>
          ) : null}
        </main>
      </div>
    </div>
  );
}
