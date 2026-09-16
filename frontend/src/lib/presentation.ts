import type { SearchResult } from "./types";

const technicalLabels =
  /\b(?:generic|button|textbox|combobox|option|article|complementary|strong|heading|paragraph|checkbox|navigation|banner|contentinfo)\s*:/gi;

export function cleanEvidenceText(value: string | null | undefined, limit = 260): string {
  if (!value) return "";
  const cleaned = value
    .replace(/\u001b\[[0-9;]*m/g, "")
    .replace(/\\+[nrt]/g, " ")
    .replace(/\\+"/g, '"')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(technicalLabels, " ")
    .replace(/\s+-\s+/g, " · ")
    .replace(/[{}\[\]]{2,}/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (cleaned.length <= limit) return cleaned;
  const boundary = cleaned.lastIndexOf(" ", limit);
  return `${cleaned.slice(0, boundary > limit * 0.65 ? boundary : limit).trim()}…`;
}

export function humanizeSearchTitle(title: string): string {
  const normalized = title.trim();
  const mapping: Record<string, string> = {
    工具结果: "执行结果",
    命令执行: "命令记录",
    exec: "命令记录",
    js: "浏览器操作",
    用户输入: "研究目标",
    助手回复: "Codex 回复",
  };
  return mapping[normalized] || normalized;
}

const sourceTitleByFilename: Record<string, string> = {
  "codex_store.py": "Codex 会话存储与检索实现",
  "embeddings.py": "向量表示与检索实现",
  "retrieval.py": "混合检索与结果排序",
  "parser.py": "会话与代码结构解析",
  "models.py": "研发证据数据模型",
  "service.py": "业务服务实现",
  "store.py": "数据持久化实现",
  "router.py": "接口路由实现",
  "platform.py": "平台接口与工作台服务",
  "project-network-model.js": "项目关系图交互模型",
  "project-network.js": "项目关系图界面",
  "source-workbenches.js": "多来源工作台交互实现",
  "rag-workbench.js": "RAG 分栏与证据交互实现",
  "app.js": "应用导航与会话交互实现",
  "PRODUCT_INFORMATION_ARCHITECTURE.md": "产品信息架构",
  "design-qa.md": "界面设计验收记录",
  "test_codex_sessions.py": "Codex 会话索引测试",
  "test_platform.py": "平台能力回归测试",
};

export function semanticFileTitle(path: string): string {
  const normalized = path.trim().replaceAll("\\", "/");
  const filename = normalized.split("/").at(-1) || normalized;
  if (sourceTitleByFilename[filename]) return sourceTitleByFilename[filename];
  const stem = filename.replace(/\.[A-Za-z0-9_-]+$/, "");
  const readable = stem
    .replace(/^test[_-]/, "")
    .replace(/[_-]+/g, " ")
    .trim();
  if (!readable) return "代码实现";
  if (/^test[_-]/.test(stem)) return `${readable} 测试`;
  return `${readable} 实现`;
}

export function semanticSourceTitle(item: SearchResult): string {
  const path = item.path || codePathFromResult(item);
  const title = humanizeSearchTitle(item.title);
  const normalizedTitle = title.replace(/\s+实现$/, "").trim();
  const identifierLike =
    !title ||
    /^[a-f0-9-]{12,}$/i.test(title) ||
    /[/\\][^/\\]+\.[A-Za-z0-9_-]+$/.test(title) ||
    /^[\w.-]+\.[A-Za-z0-9_-]+(?:#[\w.-]+)?$/.test(normalizedTitle);
  if (item.source === "code" && path && identifierLike) {
    return semanticFileTitle(path);
  }
  if (item.source === "workspace" && identifierLike) return "关联研究任务";
  if (item.source === "experiment" && identifierLike) return "关联实验结果";
  if (item.source === "document" && identifierLike) return "关联科研文档";
  return title || "未命名关联来源";
}

export function humanizeSessionTitle(
  title: string | null | undefined,
  goal?: string | null,
  updatedAt?: string | null,
): string {
  const value = cleanEvidenceText(title, 140);
  const identifierLike =
    !value ||
    /^(?:codex|thread|session)[\s:/_-]*(?:[a-f0-9-]{10,}|\d+)$/i.test(value) ||
    /^[a-f0-9]{12,}(?:-[a-f0-9]{4,})+$/i.test(value) ||
    /^(?:untitled|new|codex)\s+(?:thread|session)$/i.test(value);
  if (!identifierLike) {
    if (!/\s/.test(value) && (value.match(/[-_]/g) || []).length >= 2) {
      return value
        .replace(/[-_]+/g, " ")
        .replace(/\b\w/g, (character) => character.toUpperCase());
    }
    return value;
  }
  const goalTitle = cleanEvidenceText(goal, 88).split(/[。！？!?]/)[0]?.trim();
  if (goalTitle) return goalTitle;
  const stamp = updatedAt ? new Date(updatedAt) : null;
  const date =
    stamp && !Number.isNaN(stamp.getTime())
      ? new Intl.DateTimeFormat("zh-CN", {
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
        }).format(stamp)
      : "";
  return date ? `研发会话 · ${date}` : "未命名研发会话";
}

export type SummarySection = {
  key: "delivery" | "change" | "validation" | "decision" | "followup";
  title: string;
  items: string[];
};

export function structureTaskSummary(value: string | null | undefined): SummarySection[] {
  const raw = value || "";
  const candidates = raw
    .split(/\n+|(?<=[。！？!?])\s+(?=[^\s])/)
    .map((item) => cleanEvidenceText(item.replace(/^[-*#\d.)\s]+/, ""), 240))
    .filter((item) => item.length >= 4);
  const unique = candidates.filter(
    (item, index, all) => all.findIndex((candidate) => candidate === item) === index,
  );
  const buckets: Record<SummarySection["key"], string[]> = {
    delivery: [],
    change: [],
    validation: [],
    decision: [],
    followup: [],
  };
  unique.forEach((item) => {
    if (/待办|后续|下一步|风险|尚未|未完成|todo|follow[- ]?up/i.test(item)) {
      buckets.followup.push(item);
    } else if (/测试|验证|检查|通过|失败|构建|lint|pytest|vitest|test|build/i.test(item)) {
      buckets.validation.push(item);
    } else if (/决定|结论|取舍|原因|采用|选择|方案|decision/i.test(item)) {
      buckets.decision.push(item);
    } else if (/修改|新增|删除|重构|文件|接口|组件|页面|样式|数据|change|update|add|remove|refactor/i.test(item)) {
      buckets.change.push(item);
    } else {
      buckets.delivery.push(item);
    }
  });
  const definitions: Array<[SummarySection["key"], string]> = [
    ["delivery", "完成事项"],
    ["change", "关键变更"],
    ["validation", "验证结果"],
    ["decision", "决策与结论"],
    ["followup", "风险与后续"],
  ];
  return definitions
    .filter(([key]) => buckets[key].length)
    .map(([key, title]) => ({ key, title, items: buckets[key].slice(0, 6) }));
}

function codePathFromResult(item: SearchResult): string {
  if (item.path) return item.path.replace(/#(?:diff|L\d+(?:-L\d+)?|symbol=.*)$/i, "");
  const subtitle = cleanEvidenceText(item.subtitle, 260);
  if (/[/\\].+\.[A-Za-z0-9_-]+$/.test(subtitle) || /^[\w@+., -]+\.[A-Za-z0-9_-]+$/.test(subtitle)) {
    return subtitle;
  }
  const locator = decodeURIComponent(item.locator || "");
  const match = locator.match(/^[^:]+@[^:]+:(.+)$/);
  return (match?.[1] || "").replace(
    /#(?:diff|L\d+(?:-L\d+)?|symbol=.*)$/i,
    "",
  );
}

export function sourceDestination(projectId: string, item: SearchResult): string {
  const project = encodeURIComponent(projectId);
  const entity = encodeURIComponent(item.entity_id);
  if (item.source === "code") {
    const path = codePathFromResult(item);
    const params = new URLSearchParams();
    if (path) params.set("file", path);
    if (item.repository_id) params.set("repository", item.repository_id);
    return `/p/${project}/code${params.size ? `?${params.toString()}` : ""}`;
  }
  if (item.source === "codex") {
    const threadId = item.thread_id;
    return threadId
      ? `/p/${project}/sessions/${encodeURIComponent(threadId)}`
      : `/p/${project}/sessions?focus=${entity}`;
  }
  if (item.source === "document" || item.source === "experiment") {
    const params = new URLSearchParams({
      focus: item.entity_id,
      q: humanizeSearchTitle(item.title),
    });
    return `/p/${project}/${item.source === "document" ? "documents" : "experiments"}?${params.toString()}`;
  }
  return `/p/${project}/map?entity=${entity}`;
}

export function sourceGraphDestination(projectId: string, item: SearchResult): string {
  const params = new URLSearchParams({
    entity: item.entity_id,
    q: humanizeSearchTitle(item.title),
  });
  if (item.source === "code") {
    const path = codePathFromResult(item);
    if (path) params.set("code", path);
    if (item.repository_id) params.set("repository", item.repository_id);
  }
  return `/p/${encodeURIComponent(projectId)}/map?${params.toString()}`;
}

export function presentSearchSnippet(
  source: string,
  title: string,
  value: string | null | undefined,
  detailed = false,
): string {
  if (source !== "codex") return cleanEvidenceText(value, detailed ? 1200 : 260);
  const normalized = title.trim().toLowerCase();
  const paths = Array.from(
    (value || "").matchAll(
      /(?:Update File:|Add File:|Delete File:|["']?(?:path|file)["']?\s*[:=]\s*["']?)(\/?[\w./-]+\.[a-z0-9]+)/gi,
    ),
    (match) => match[1],
  ).filter((item, index, all) => all.indexOf(item) === index).slice(0, 3);
  if (normalized.includes("补丁") || normalized.includes("文件修改")) {
    return paths.length
      ? `修改了 ${paths.join("、")}${paths.length === 3 ? " 等文件" : ""}。打开结果可核对具体差异与验证状态。`
      : "Codex 产生了代码变更。打开结果可核对修改文件、差异与验证状态。";
  }
  if (
    normalized.includes("工具结果") ||
    normalized.includes("命令执行") ||
    normalized === "exec" ||
    normalized === "js"
  ) {
    return "Codex 已完成一项开发操作并返回结构化结果。打开结果可查看命令、状态与关联变更。";
  }
  return cleanEvidenceText(value, detailed ? 1200 : 260);
}
