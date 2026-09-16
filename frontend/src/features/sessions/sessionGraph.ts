import {
  cleanEvidenceText,
  semanticFileTitle,
  structureTaskSummary,
} from "../../lib/presentation";
import type {
  CodexTimelineFileChange,
  CodexTimelineOperation,
  CodexTimelineTurn,
  SearchResult,
} from "../../lib/types";

export type SessionGraphNodeKind =
  | "goal"
  | "operation"
  | "artifact"
  | "validation"
  | "conclusion"
  | "risk"
  | "followup";

export type SessionGraphNode = {
  id: string;
  kind: SessionGraphNodeKind;
  title: string;
  subtitle: string;
  evidence?: string;
  facts?: Array<{ label: string; value: string }>;
  locator: string;
  x: number;
  y: number;
  source?: SearchResult;
  operation?: CodexTimelineOperation;
  file?: CodexTimelineFileChange;
};

export type SessionGraphEdge = {
  id: string;
  source: string;
  target: string;
  label: string;
  tone: "primary" | "success" | "risk" | "related";
};

export type SessionGraphModel = {
  nodes: SessionGraphNode[];
  edges: SessionGraphEdge[];
};

const stageX: Record<SessionGraphNodeKind, number> = {
  goal: 40,
  operation: 300,
  artifact: 560,
  validation: 820,
  conclusion: 1080,
  risk: 300,
  followup: 560,
};

/**
 * Lay the single-turn graph out as an evidence flow rather than a generic grid.
 * Nodes in the same stage share a visual rail and are distributed around the
 * centre line; risks are kept on a separate lower rail so they cannot cut
 * through the implementation/validation chain.
 */
export function arrangeSessionGraphNodes(
  nodes: SessionGraphNode[],
): SessionGraphNode[] {
  const mainKinds: SessionGraphNodeKind[] = [
    "goal",
    "operation",
    "artifact",
    "validation",
    "conclusion",
  ];
  const positions = new Map<string, { x: number; y: number }>();
  const centerY = 220;
  const rowGap = 156;

  mainKinds.forEach((kind) => {
    const stageNodes = nodes.filter((node) => node.kind === kind);
    const startY = centerY - ((stageNodes.length - 1) * rowGap) / 2;
    stageNodes.forEach((node, index) => {
      positions.set(node.id, {
        x: stageX[kind],
        y: Math.round(startY + index * rowGap),
      });
    });
  });

  const mainBottom = Math.max(
    centerY,
    ...Array.from(positions.values()).map((position) => position.y),
  );
  const exceptionY = mainBottom + 210;
  nodes
    .filter((node) => node.kind === "risk" || node.kind === "followup")
    .forEach((node) => {
      positions.set(node.id, { x: stageX[node.kind], y: exceptionY });
    });

  return nodes.map((node) => ({
    ...node,
    ...(positions.get(node.id) || { x: node.x, y: node.y }),
  }));
}

function sourceForPath(path: string, related: SearchResult[]) {
  const normalizedPath = path.replace(/^\/+/, "");
  return related.find((item) => {
    if (item.source !== "code") return false;
    const candidates = [item.path, item.subtitle]
      .filter(Boolean)
      .map((value) => String(value).replace(/^\/+/, ""));
    return (
      candidates.some(
        (candidate) =>
          candidate === normalizedPath ||
          candidate.endsWith(`/${normalizedPath}`) ||
          normalizedPath.endsWith(`/${candidate}`),
      ) ||
      item.locator.includes(`:${normalizedPath}#`) ||
      item.locator.endsWith(`:${normalizedPath}`)
    );
  });
}

function validationTitle(operation: CodexTimelineOperation, index: number) {
  const command = cleanEvidenceText(operation.command?.split("\n")[0], 72);
  if (command) return command;
  return cleanEvidenceText(operation.name, 72) || `验证记录 ${index + 1}`;
}

function operationForPath(path: string, operations: CodexTimelineOperation[]) {
  const normalizedPath = path.replace(/^\/+/, "");
  const basename = normalizedPath.split("/").at(-1) || normalizedPath;
  const expectsJavaScript = /\.(?:js|jsx|ts|tsx)$/i.test(normalizedPath);
  return operations
    .filter((item) => item.kind !== "validation")
    .map((item) => {
      const command = item.command || "";
      const exact = command.includes(normalizedPath);
      const basenameHit = command.includes(basename);
      const readsContent = /\b(?:sed\s+-n|cat|head|tail)\b/.test(command);
      const readsDiff = /\bgit\s+diff\b/.test(command);
      const searchesCode = /\brg\s+-n\b/.test(command);
      const aggregateOnly = /\b(?:wc\s+-l|find)\b/.test(command);
      const codeLikeOutput =
        /\b(?:function|class|const|let|def|import|export|return)\b|[{};]/.test(
          item.output || "",
        );
      const mismatchedOutput =
        expectsJavaScript &&
        /^\s*(?:<!doctype|<html|---\s*(?:name|description):)/i.test(
          item.output || "",
        );
      return {
        item,
        score:
          (exact ? 12 : basenameHit ? 3 : 0) +
          (item.output ? 2 : 0) +
          (item.kind === "command" ? 1 : 0) +
          (readsDiff ? 10 : readsContent ? 8 : searchesCode ? 6 : 0) +
          (codeLikeOutput ? 5 : 0) +
          (command.length < 320 ? 2 : 0) -
          (aggregateOnly ? 8 : 0) -
          (mismatchedOutput ? 24 : 0),
      };
    })
    .filter((candidate) => candidate.score > 2)
    .sort((left, right) => right.score - left.score)[0]?.item;
}

function graphEvidencePreview(value?: string | null, limit = 280) {
  if (!value) return "";
  const normalized = value
    .replace(/\u001b\[[0-9;]*m/g, "")
    .replace(/\\n/g, "\n")
    .replace(/\r/g, "")
    .trim();
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, limit).trimEnd()}…`;
}

function changeTypeLabel(value: string) {
  return (
    {
      add: "新增",
      added: "新增",
      create: "新增",
      created: "新增",
      update: "修改",
      updated: "修改",
      modify: "修改",
      modified: "修改",
      delete: "删除",
      deleted: "删除",
      remove: "删除",
      removed: "删除",
      rename: "重命名",
      renamed: "重命名",
      patch: "修改",
    }[value.toLowerCase()] || value
  );
}

export function buildSessionGraph(
  turn: CodexTimelineTurn,
  related: SearchResult[],
): SessionGraphModel {
  const operations = turn.operations || [];
  const commandOperations = operations.filter(
    (item) => item.kind !== "validation",
  );
  const validations = operations
    .filter((item) => item.kind === "validation")
    .slice(0, 2);
  const files: CodexTimelineFileChange[] = turn.file_changes?.length
    ? turn.file_changes
    : turn.files.map((path) => ({
        id: `${turn.id}:${path}`,
        path,
        change_type: "update",
        status: "completed",
        locator: turn.locator || turn.id,
      }));
  const visibleFiles = files
    .filter(
      (item, index, all) =>
        all.findIndex((candidate) => candidate.path === item.path) === index,
    )
    .map((file, index) => {
      const source = sourceForPath(file.path, related);
      const operation = operationForPath(file.path, operations);
      const codeLike =
        /\.(?:py|js|jsx|ts|tsx|go|rs|java|kt|swift|rb|php|c|cc|cpp|h)$/i.test(
          file.path,
        );
      return {
        file,
        index,
        score:
          (codeLike ? 1000 : 0) + (source ? 100 : 0) + (operation ? 30 : 0),
      };
    })
    .sort((left, right) => right.score - left.score || left.index - right.index)
    .slice(0, 3)
    .map((candidate) => candidate.file);
  const summary = structureTaskSummary(turn.summary);
  const conclusionCandidate =
    summary.find((item) => item.key === "decision")?.items[0] ||
    summary.find((item) => item.key === "validation")?.items[0] ||
    summary.find((item) => item.key === "delivery")?.items[0] ||
    "本轮目标、代码变更与验证记录已形成可审计证据链。";
  const cleanedConclusion = cleanEvidenceText(conclusionCandidate, 92);
  const conclusion =
    cleanedConclusion.length >= 5
      ? cleanedConclusion
      : validations[0]
        ? `${validationTitle(validations[0], 0)} ${
            validations[0].status === "failed" ? "未通过" : "已通过"
          }`
        : "本轮目标、代码变更与验证记录已形成可审计证据链。";
  const risk =
    summary.find((item) => item.key === "followup")?.items[0] ||
    (turn.status === "failed" ? "本轮存在失败记录，需要继续定位根因。" : "");

  const nodes: SessionGraphNode[] = [
    {
      id: "goal",
      kind: "goal",
      title: cleanEvidenceText(turn.goal, 78) || "完成当前研发目标",
      subtitle: "本轮目标",
      locator: turn.locator || turn.id,
      x: 30,
      y: 236,
    },
    {
      id: "operation-cluster",
      kind: "operation",
      title: "解析、实现与索引操作",
      subtitle: `${commandOperations.length || turn.command_count} 条执行记录`,
      evidence: graphEvidencePreview(
        commandOperations
          .slice(0, 3)
          .map((item) => item.command || item.name)
          .filter(Boolean)
          .join("\n"),
      ),
      facts: [
        {
          label: "命令/工具",
          value: String(commandOperations.length || turn.command_count),
        },
        {
          label: "验证",
          value: String(validations.length || turn.validation_count),
        },
      ],
      locator: commandOperations[0]?.locator || turn.locator || turn.id,
      x: 235,
      y: 184,
    },
  ];

  visibleFiles.forEach((file, index) => {
    const source = sourceForPath(file.path, related);
    const evidenceOperation = operationForPath(file.path, operations);
    nodes.push({
      id: `file:${file.path}`,
      kind: "artifact",
      title: semanticFileTitle(file.path),
      subtitle: file.path,
      evidence: graphEvidencePreview(
        source?.snippet ||
          evidenceOperation?.output ||
          evidenceOperation?.command ||
          `${changeTypeLabel(file.change_type)}文件\n${file.path}\n打开源码可核对完整变更内容。`,
      ),
      facts: [
        { label: "变更", value: changeTypeLabel(file.change_type) },
        {
          label: "证据",
          value: source
            ? "代码片段"
            : evidenceOperation
              ? "执行输出"
              : "文件事件",
        },
        {
          label: "定位",
          value:
            typeof source?.start_line === "number"
              ? `L${source.start_line}${source.end_line ? `–${source.end_line}` : ""}`
              : "可回查",
        },
      ],
      locator: source?.locator || evidenceOperation?.locator || file.locator,
      x: 450,
      y: 112 + index * 138,
      source,
      operation: evidenceOperation,
      file,
    });
  });

  if (validations.length) {
    validations.forEach((operation, index) => {
      nodes.push({
        id: `validation:${operation.id}`,
        kind: "validation",
        title: validationTitle(operation, index),
        subtitle:
          operation.status === "failed"
            ? "验证失败 · 查看输出"
            : "验证通过 · 可回查原始输出",
        evidence: graphEvidencePreview(operation.output || operation.command),
        facts: [
          {
            label: "退出码",
            value:
              typeof operation.exit_code === "number"
                ? String(operation.exit_code)
                : "未记录",
          },
          {
            label: "状态",
            value: operation.status === "failed" ? "失败" : "通过",
          },
        ],
        locator: operation.locator,
        x: 655,
        y: 170 + index * 154,
        operation,
      });
    });
  } else {
    nodes.push({
      id: "validation-summary",
      kind: "validation",
      title: `${turn.validation_count} 项验证记录`,
      subtitle: turn.validation_count ? "已记录验证结果" : "尚未记录验证",
      facts: [{ label: "验证", value: String(turn.validation_count) }],
      locator: turn.locator || turn.id,
      x: 655,
      y: 220,
    });
  }

  nodes.push({
    id: "conclusion",
    kind: "conclusion",
    title: conclusion,
    subtitle: "本轮结论",
    locator: turn.locator || turn.id,
    x: 850,
    y: 220,
  });

  if (risk) {
    nodes.push(
      {
        id: "risk",
        kind: "risk",
        title: cleanEvidenceText(risk, 90),
        subtitle: "风险与后续",
        locator: turn.locator || turn.id,
        x: 285,
        y: 486,
      },
      {
        id: "followup",
        kind: "followup",
        title: "待跟进优化任务",
        subtitle: "从风险生成后续行动",
        locator: turn.locator || turn.id,
        x: 560,
        y: 506,
      },
    );
  }

  const edges: SessionGraphEdge[] = [
    {
      id: "goal-operation",
      source: "goal",
      target: "operation-cluster",
      label: "拆解为任务",
      tone: "primary",
    },
  ];

  visibleFiles.forEach((file) => {
    edges.push({
      id: `operation-${file.path}`,
      source: "operation-cluster",
      target: `file:${file.path}`,
      label: "产出代码",
      tone: "primary",
    });
  });

  const validationNodes = nodes.filter((node) => node.kind === "validation");
  const artifactNodes = nodes.filter((node) => node.kind === "artifact");
  validationNodes.forEach((validation, index) => {
    const artifact = artifactNodes[index % Math.max(artifactNodes.length, 1)];
    edges.push({
      id: `artifact-validation-${validation.id}`,
      source: artifact?.id || "operation-cluster",
      target: validation.id,
      label: "验证",
      tone: "success",
    });
    edges.push({
      id: `validation-conclusion-${validation.id}`,
      source: validation.id,
      target: "conclusion",
      label: "支持结论",
      tone: "success",
    });
  });

  if (risk) {
    edges.push(
      {
        id: "goal-risk",
        source: "goal",
        target: "risk",
        label: "发现风险",
        tone: "risk",
      },
      {
        id: "risk-followup",
        source: "risk",
        target: "followup",
        label: "生成后续",
        tone: "risk",
      },
    );
  }

  return { nodes: arrangeSessionGraphNodes(nodes), edges };
}
