import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ScientificDocument } from "../../lib/types";
import { DocumentPreview, documentFormatLabel } from "./DocumentPreview";

afterEach(cleanup);

const markdownDocument: ScientificDocument = {
  id: "document://project-rag/readme/v1",
  display_key: "DOC-README",
  project_id: "project-rag",
  title: "项目说明",
  version: "v1",
  source_type: "markdown",
  source_uri: "/tmp/README.md",
  content: [
    "# 项目说明",
    "",
    "这是 **可阅读的正文**。",
    "",
    "- 第一项",
    "- 第二项",
    "",
    "| 指标 | 值 |",
    "| --- | ---: |",
    "| 准确率 | 91.4% |",
    "",
    "```ts",
    "const ready = true;",
    "```",
    "",
    "## 使用方式",
    "",
    "按目录定位章节。",
  ].join("\n"),
  authors: ["Research Team"],
  tags: ["design"],
  status: "indexed",
  created_at: "2026-07-26T00:00:00Z",
  updated_at: "2026-07-26T00:00:00Z",
  sections: [
    {
      id: "section://readme/1",
      title: "项目说明",
      level: 1,
      content: "这是可阅读的正文。",
      start_line: 1,
      end_line: 14,
    },
    {
      id: "section://readme/2",
      title: "使用方式",
      level: 2,
      content: "按目录定位章节。",
      start_line: 16,
      end_line: 18,
    },
  ],
  claims: [],
  tables: [],
};

function renderPreview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <DocumentPreview document={markdownDocument} />
    </QueryClientProvider>,
  );
}

describe("DocumentPreview", () => {
  it("renders Markdown as a readable document with GFM tables and code", () => {
    renderPreview();

    expect(screen.getByRole("heading", { name: "项目说明", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("可阅读的正文")).toBeInTheDocument();
    expect(screen.getByRole("table")).toHaveTextContent("准确率");
    expect(screen.getByText("const ready = true;")).toBeInTheDocument();
    expect(screen.getByTestId("markdown-document")).not.toHaveTextContent("# 项目说明");
    expect(documentFormatLabel(markdownDocument)).toBe("Markdown");
  });

  it("switches between rendered Markdown, source text, and claims", async () => {
    const user = userEvent.setup();
    renderPreview();

    await user.click(screen.getByRole("button", { name: "源码" }));
    expect(document.querySelector(".document-source")).toHaveTextContent("# 项目说明");

    await user.click(screen.getByRole("button", { name: "研究主张" }));
    expect(screen.getByText("暂未提取研究主张")).toBeInTheDocument();
  });

  it("marks the current outline entry and updates it when a section is selected", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      renderPreview();

      expect(screen.getByRole("button", { name: "项目说明" }))
        .toHaveAttribute("aria-current", "location");
      await user.click(screen.getByRole("button", { name: "使用方式" }));

      expect(screen.getByRole("button", { name: "使用方式" }))
        .toHaveAttribute("aria-current", "location");
      expect(scrollIntoView).toHaveBeenCalledWith({
        behavior: "smooth",
        block: "start",
      });
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });
});
