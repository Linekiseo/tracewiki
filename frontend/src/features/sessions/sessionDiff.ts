import type {
  CodexTimelineFileChange,
  CodexTimelineOperation,
  SearchResult,
} from "../../lib/types";

export type SessionDiffLine = {
  id: string;
  kind: "addition" | "deletion" | "context" | "meta";
  marker: "+" | "-" | " " | "";
  oldLine: number | null;
  newLine: number | null;
  content: string;
};

export type SessionDiffHunk = {
  id: string;
  header: string;
  section: string;
  oldStart: number;
  oldCount: number;
  newStart: number;
  newCount: number;
  lines: SessionDiffLine[];
};

export type SessionDiff = {
  hunks: SessionDiffHunk[];
  additions: number;
  deletions: number;
  lineCount: number;
};

export type SessionDiffEvidence = {
  patch: string;
  origin: "recorded" | "source" | "command";
  label: string;
  truncated: boolean;
};

const HUNK_HEADER =
  /^@@ -(?<oldStart>\d+)(?:,(?<oldCount>\d+))? \+(?<newStart>\d+)(?:,(?<newCount>\d+))? @@(?<section>.*)$/;

function countValue(value: string | undefined) {
  return value === undefined ? 1 : Number(value);
}

export function parseSessionDiff(patch: string): SessionDiff {
  const hunks: SessionDiffHunk[] = [];
  let current: SessionDiffHunk | null = null;
  let oldLine = 0;
  let newLine = 0;
  let additions = 0;
  let deletions = 0;

  patch.split(/\r?\n/).forEach((line, index) => {
    const header = HUNK_HEADER.exec(line);
    if (header?.groups) {
      oldLine = Number(header.groups.oldStart);
      newLine = Number(header.groups.newStart);
      current = {
        id: `hunk-${index}-${oldLine}-${newLine}`,
        header: line,
        section: header.groups.section.trim(),
        oldStart: oldLine,
        oldCount: countValue(header.groups.oldCount),
        newStart: newLine,
        newCount: countValue(header.groups.newCount),
        lines: [],
      };
      hunks.push(current);
      return;
    }
    if (!current) return;

    const marker = line[0];
    if (marker === "+" && !line.startsWith("+++")) {
      current.lines.push({
        id: `${current.id}-line-${index}`,
        kind: "addition",
        marker: "+",
        oldLine: null,
        newLine: newLine || null,
        content: line.slice(1),
      });
      newLine += 1;
      additions += 1;
      return;
    }
    if (marker === "-" && !line.startsWith("---")) {
      current.lines.push({
        id: `${current.id}-line-${index}`,
        kind: "deletion",
        marker: "-",
        oldLine: oldLine || null,
        newLine: null,
        content: line.slice(1),
      });
      oldLine += 1;
      deletions += 1;
      return;
    }
    if (marker === " ") {
      current.lines.push({
        id: `${current.id}-line-${index}`,
        kind: "context",
        marker: " ",
        oldLine: oldLine || null,
        newLine: newLine || null,
        content: line.slice(1),
      });
      oldLine += 1;
      newLine += 1;
      return;
    }
    current.lines.push({
      id: `${current.id}-line-${index}`,
      kind: "meta",
      marker: "",
      oldLine: null,
      newLine: null,
      content: line,
    });
  });

  return {
    hunks,
    additions,
    deletions,
    lineCount: hunks.reduce((total, hunk) => total + hunk.lines.length, 0),
  };
}

function normalizedPath(value: string | undefined) {
  return (value || "").replaceAll("\\", "/").replace(/^\.?\//, "");
}

function looksLikeDiff(value: string | undefined) {
  return Boolean(
    value &&
      /^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/m.test(value) &&
      /^(?:\+[^+]|-[^-])/m.test(value),
  );
}

function pathMatches(value: string | undefined, path: string) {
  const normalized = normalizedPath(path);
  const candidate = normalizedPath(value);
  return candidate === normalized || candidate.endsWith(`/${normalized}`);
}

export function findSessionDiffEvidence(
  change: CodexTimelineFileChange,
  related: SearchResult[],
  operations: CodexTimelineOperation[],
): SessionDiffEvidence | null {
  if (looksLikeDiff(change.patch)) {
    return {
      patch: change.patch || "",
      origin: "recorded",
      label: "会话原始补丁",
      truncated: Boolean(change.patch_truncated),
    };
  }

  const source = related.find(
    (item) =>
      item.source === "code" &&
      (pathMatches(item.path, change.path) ||
        pathMatches(item.subtitle, change.path)) &&
      looksLikeDiff(item.snippet),
  );
  if (source?.snippet) {
    return {
      patch: source.snippet,
      origin: "source",
      label: "版本化代码证据",
      truncated: false,
    };
  }

  const command = operations.find(
    (item) =>
      looksLikeDiff(item.output) &&
      (item.command.includes(change.path) || item.output.includes(change.path)),
  );
  if (command?.output) {
    return {
      patch: command.output,
      origin: "command",
      label: "命令输出中的补丁",
      truncated: false,
    };
  }
  return null;
}

