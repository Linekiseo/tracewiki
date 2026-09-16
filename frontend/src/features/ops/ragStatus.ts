import type {
  QuerySource,
  RagComponentSwitch,
  RagStatusResponse,
} from "../../lib/types";

export type OpsTone = "reported" | "neutral" | "warning" | "danger" | "unknown";

export type OpsField = {
  value: string;
  detail: string;
  tone: OpsTone;
};

export type OpsSourceRow = {
  source: QuerySource;
  label: string;
  v1: OpsField;
  v2: OpsField;
  fallback: OpsField;
  lastKnownGood: OpsField;
};

export type OpsSwitchRow = {
  key: RagComponentSwitch;
  label: string;
  state: OpsField;
};

export type OpsLatencyRow = {
  name: string;
  count: number | null;
  p50: number | null;
  p95: number | null;
  maximum: number | null;
};

export type RagOpsViewModel = {
  schemaVersion: string;
  snapshotTime: string | null;
  freshness: OpsField;
  defaultEngine: OpsField;
  qualityHold: OpsField;
  releaseDecision: OpsField;
  releaseAuthority: OpsField;
  releaseIntegrity: OpsField;
  reviewedCalibration: OpsField;
  sources: OpsSourceRow[];
  switchesVersion: string;
  switches: OpsSwitchRow[];
  performance: {
    index: OpsField;
    cache: OpsField;
    dashboard: OpsField;
    latency: OpsLatencyRow[];
  };
};

export const RAG_STATUS_STALE_AFTER_MS = 5 * 60 * 1000;

const SOURCE_LABELS: Record<QuerySource, string> = {
  code: "代码",
  codex: "Codex 会话",
  experiment: "实验",
  notebook: "Notebook",
  document: "文档",
  workspace: "工作区",
};

const SOURCE_ORDER = Object.keys(SOURCE_LABELS) as QuerySource[];

const SWITCH_LABELS: Record<RagComponentSwitch, string> = {
  generator_prompt: "生成器 Prompt",
  context_packer: "上下文打包",
  fusion: "多源融合",
  reranker: "重排序",
  embedding_generation: "Embedding 生成",
  source_retrievers: "来源检索器",
  planner: "查询规划器",
};

const SWITCH_ORDER = Object.keys(SWITCH_LABELS) as RagComponentSwitch[];

const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$/;
const SHA256_IDENTIFIER = /^sha256:[0-9a-f]{64}$/;
const SENSITIVE_VALUE =
  /(bearer|token|secret|password|credential|api[-_]?key|private[-_]?key|ghp_|sk-proj)/i;

const STAGE_DETAILS: Record<string, { value: string; detail: string; tone: OpsTone }> = {
  default_v1: {
    value: "DEFAULT V1",
    detail: "服务报告默认走 V1",
    tone: "neutral",
  },
  no_release: {
    value: "NO RELEASE",
    detail: "规范发布 authority 没有该来源的可用发布",
    tone: "danger",
  },
  v1_default: {
    value: "DEFAULT V1",
    detail: "服务报告默认走 V1",
    tone: "neutral",
  },
  default: {
    value: "DEFAULT",
    detail: "服务报告为默认阶段；版本由列标题限定",
    tone: "neutral",
  },
  active: { value: "ACTIVE", detail: "服务报告为活动阶段", tone: "reported" },
  serving: { value: "SERVING", detail: "服务报告正在服务", tone: "reported" },
  standby: { value: "STANDBY", detail: "服务报告处于待命阶段", tone: "neutral" },
  offline: { value: "OFFLINE", detail: "未进入在线发布阶段", tone: "warning" },
  disabled: { value: "DISABLED", detail: "服务报告已禁用", tone: "danger" },
  unavailable: { value: "UNAVAILABLE", detail: "服务报告不可用", tone: "danger" },
  not_ready: { value: "NOT READY", detail: "服务报告尚未就绪", tone: "warning" },
  not_qualified: {
    value: "NOT QUALIFIED",
    detail: "服务报告尚未通过资格判定",
    tone: "warning",
  },
  quality_hold: {
    value: "QUALITY HOLD",
    detail: "质量保持中，不代表发布",
    tone: "warning",
  },
  observe_only: {
    value: "OBSERVE ONLY",
    detail: "仅观察，不参与默认路由",
    tone: "warning",
  },
  shadow: { value: "SHADOW", detail: "影子阶段，不代表默认发布", tone: "warning" },
  shadow_internal: {
    value: "SHADOW INTERNAL",
    detail: "内部影子阶段，不代表默认发布",
    tone: "warning",
  },
  shadow_internal_100: {
    value: "SHADOW INTERNAL 100%",
    detail: "全量内部影子阶段，不代表默认发布",
    tone: "warning",
  },
  shadow_100: {
    value: "SHADOW 100%",
    detail: "全量影子阶段，不代表默认发布",
    tone: "warning",
  },
  canary_5: { value: "CANARY 5%", detail: "金丝雀阶段，不代表默认发布", tone: "warning" },
  canary_25: {
    value: "CANARY 25%",
    detail: "金丝雀阶段，不代表默认发布",
    tone: "warning",
  },
  canary_50: {
    value: "CANARY 50%",
    detail: "金丝雀阶段，不代表默认发布",
    tone: "warning",
  },
  default_v2: {
    value: "DEFAULT V2",
    detail: "仅转述服务端阶段；浏览器不验证发布资格",
    tone: "reported",
  },
  production: {
    value: "PRODUCTION",
    detail: "仅转述服务端阶段；浏览器不验证生产授权",
    tone: "reported",
  },
};

const PERFORMANCE_LABELS: Record<string, string> = {
  global_v2_execution: "Global V2",
  source_retrieval: "来源检索",
  rerank: "重排序",
  context_pack: "上下文打包",
  generation: "生成",
  "source_retrieval/code": "来源检索 / 代码",
  "source_retrieval/codex": "来源检索 / Codex",
  "source_retrieval/experiment": "来源检索 / 实验",
  "source_retrieval/notebook": "来源检索 / Notebook",
  "source_retrieval/document": "来源检索 / 文档",
  "source_retrieval/workspace": "来源检索 / 工作区",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function getPath(value: unknown, path: string): unknown {
  let current: unknown = value;
  for (const member of path.split(".")) {
    const record = asRecord(current);
    if (!record || !(member in record)) return undefined;
    current = record[member];
  }
  return current;
}

function first(value: unknown, paths: string[]): unknown {
  for (const path of paths) {
    const result = getPath(value, path);
    if (result !== undefined && result !== null) return result;
  }
  return undefined;
}

function safeIdentifier(value: unknown): string | null {
  if (
    typeof value !== "string" ||
    !SAFE_IDENTIFIER.test(value) ||
    SENSITIVE_VALUE.test(value) ||
    value.includes("/") ||
    value.includes("\\")
  ) {
    return null;
  }
  return value;
}

function safeDigest(value: unknown): string | null {
  return typeof value === "string" && SHA256_IDENTIFIER.test(value) ? value : null;
}

function normalizeMember(value: unknown): string | null {
  const identifier = safeIdentifier(value);
  return identifier
    ? identifier.toLowerCase().replaceAll("-", "_").replaceAll(".", "_")
    : null;
}

function finiteNumber(value: unknown, maximum = 1_000_000_000): number | null {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0 &&
    value <= maximum
    ? value
    : null;
}

function integer(value: unknown, maximum = 1_000_000_000): number | null {
  const number = finiteNumber(value, maximum);
  return number !== null && Number.isInteger(number) ? number : null;
}

function unknownField(detail: string): OpsField {
  return { value: "UNKNOWN", detail, tone: "unknown" };
}

function stageField(value: unknown): OpsField {
  const member = normalizeMember(value);
  if (!member) return unknownField("字段缺失、格式不受支持或已被安全过滤");
  const known = STAGE_DETAILS[member];
  if (known) return known;
  return {
    value: "UNRECOGNIZED",
    detail: `服务返回未纳入允许列表的阶段 ${member.toUpperCase()}；按未知处理`,
    tone: "unknown",
  };
}

function defaultEngineField(value: unknown): OpsField {
  if (value === "v1") {
    return { value: "V1", detail: "服务报告的默认引擎", tone: "neutral" };
  }
  if (value === "v2") {
    return {
      value: "V2 · SERVER REPORTED",
      detail: "仅转述服务端值；不构成验证或发布证明",
      tone: "reported",
    };
  }
  return unknownField("默认引擎缺失或不在 V1/V2 允许列表中");
}

function qualityHoldField(value: unknown): OpsField {
  if (value === true) {
    return {
      value: "ACTIVE",
      detail: "质量保持生效；不得解释为发布完成",
      tone: "warning",
    };
  }
  if (value === false) {
    return {
      value: "NOT ACTIVE",
      detail: "仅表示服务报告未保持；不构成可信或发布证明",
      tone: "neutral",
    };
  }
  return unknownField("缺少严格布尔值；按未确认处理");
}

function releaseDecisionField(value: unknown): OpsField {
  const member = normalizeMember(value);
  if (member === "quality_hold" || member === "hold") {
    return {
      value: "QUALITY HOLD",
      detail: "规范发布决策保持，不代表发布完成",
      tone: "warning",
    };
  }
  if (member === "approved" || member === "release" || member === "default_v2") {
    return {
      value: `${member.toUpperCase().replaceAll("_", " ")} · SERVER REPORTED`,
      detail: "浏览器未验证授权与完整性，不提升为发布完成结论",
      tone: "reported",
    };
  }
  return unknownField("规范发布决策缺失或无法识别");
}

function releaseAuthorityField(release: Record<string, unknown> | null): OpsField {
  if (!release) return unknownField("规范发布 authority 缺失");
  const identifier = safeIdentifier(first(release, ["authority_id", "authority"]));
  if (!identifier) return unknownField("authority 缺失、格式不受支持或已被安全过滤");
  const ready = first(release, ["authority_ready", "ready"]);
  const attested = first(release, ["authority_attested", "attested"]);
  if (ready === false || attested === false) {
    return {
      value: identifier,
      detail: "服务报告 authority 未就绪或未证明",
      tone: "warning",
    };
  }
  if (ready === true && attested === true) {
    return {
      value: identifier,
      detail: "服务报告已就绪且已证明；浏览器未独立复验",
      tone: "reported",
    };
  }
  return {
    value: identifier,
    detail: "authority 状态未提供严格布尔证明",
    tone: "unknown",
  };
}

function releaseIntegrityField(release: Record<string, unknown> | null): OpsField {
  if (!release) return unknownField("规范发布 integrity 缺失");
  const verified = first(release, ["integrity_verified", "integrity.verified"]);
  if (verified === false) {
    return { value: "FAILED", detail: "服务报告完整性未通过", tone: "danger" };
  }
  const status = normalizeMember(first(release, ["integrity_status", "integrity"]));
  if (status === "failed" || status === "invalid" || status === "mismatch") {
    return { value: "FAILED", detail: "服务报告完整性未通过", tone: "danger" };
  }
  const digest = safeDigest(first(release, ["integrity_sha256", "content_sha256"]));
  if (verified === true || status === "verified" || status === "valid") {
    return {
      value: "VERIFIED · SERVER REPORTED",
      detail: "浏览器未独立复算完整性",
      tone: "reported",
    };
  }
  if (digest) {
    return {
      value: "DIGEST PROVIDED",
      detail: "服务提供了 SHA-256 摘要；浏览器未复算",
      tone: "neutral",
    };
  }
  return unknownField("完整性结论或摘要缺失");
}

function reviewedCalibrationField(value: unknown): OpsField {
  const record = asRecord(value);
  if (!record) return unknownField("reviewed calibration 状态缺失");
  const available = first(record, ["available", "reviewed"]);
  const profiles = integer(first(record, ["profile_count", "source_count"]), 10_000);
  const suffix = profiles === null ? "" : ` · ${profiles} profile`;
  if (available === true) {
    return {
      value: `AVAILABLE${suffix}`,
      detail: "服务报告 reviewed calibration 可用；浏览器不验证内容",
      tone: "reported",
    };
  }
  if (available === false) {
    return {
      value: "UNAVAILABLE",
      detail: "没有可用的 reviewed calibration",
      tone: "danger",
    };
  }
  return unknownField("缺少严格布尔 availability；按不可确认处理");
}

function sourceRecord(
  payload: RagStatusResponse,
  source: QuerySource,
): Record<string, unknown> | null {
  const rawSources =
    payload.sources ??
    payload.source_status ??
    payload.source_snapshot?.sources;
  if (Array.isArray(rawSources)) {
    const match = rawSources.find((item) => item.source === source || item.name === source);
    return asRecord(match);
  }
  return asRecord(asRecord(rawSources)?.[source]);
}

function fallbackField(value: unknown): OpsField {
  if (value === false) {
    return { value: "NOT TRIGGERED", detail: "服务报告未触发 fallback", tone: "neutral" };
  }
  if (value === true) {
    return {
      value: "ACTIVE → V1",
      detail: "服务报告已触发 V1 fallback",
      tone: "warning",
    };
  }
  const member = normalizeMember(value);
  if (member === "none" || member === "not_triggered" || member === "inactive") {
    return { value: "NOT TRIGGERED", detail: "服务报告未触发 fallback", tone: "neutral" };
  }
  if (member === "v1" || member === "fallback_v1" || member === "active") {
    return { value: "ACTIVE → V1", detail: "服务报告 fallback 到 V1", tone: "warning" };
  }
  if (member === "default_v1") {
    return {
      value: "DEFAULT → V1",
      detail: "规范默认路由为 V1；不表示本次请求触发了 fallback",
      tone: "neutral",
    };
  }
  const record = asRecord(value);
  if (!record) return unknownField("fallback 状态缺失或格式不受支持");
  const active = first(record, ["active", "used", "triggered", "enabled"]);
  const target = first(record, ["target", "engine"]);
  if (active === false) {
    return { value: "NOT TRIGGERED", detail: "服务报告未触发 fallback", tone: "neutral" };
  }
  if (active === true && target === "v1") {
    return { value: "ACTIVE → V1", detail: "服务报告 fallback 到 V1", tone: "warning" };
  }
  if (active === true) {
    return {
      value: "ACTIVE · TARGET UNKNOWN",
      detail: "fallback 已触发，但目标未通过允许列表校验",
      tone: "warning",
    };
  }
  return unknownField("缺少严格布尔 fallback 状态");
}

function lastKnownGoodField(value: unknown): OpsField {
  if (value === false || value === null) {
    return { value: "UNAVAILABLE", detail: "服务报告没有 LKG", tone: "danger" };
  }
  if (value === true) {
    return {
      value: "AVAILABLE",
      detail: "服务报告存在 LKG；未暴露其标识",
      tone: "reported",
    };
  }
  const member = normalizeMember(value);
  if (member === "none" || member === "unavailable" || member === "missing") {
    return { value: "UNAVAILABLE", detail: "服务报告没有 LKG", tone: "danger" };
  }
  if (safeDigest(value) || safeIdentifier(value)) {
    return {
      value: "AVAILABLE",
      detail: "服务提供了安全格式的 LKG 标识；界面不回显",
      tone: "reported",
    };
  }
  const record = asRecord(value);
  if (!record) return unknownField("LKG 状态缺失、格式不受支持或已被安全过滤");
  const available = first(record, ["available", "active"]);
  const status = normalizeMember(record.status);
  const generation = first(record, ["generation", "generation_id", "index_generation"]);
  const hasSafeGeneration = Boolean(safeDigest(generation) || safeIdentifier(generation));
  if (available === false || status === "unavailable" || status === "missing") {
    return { value: "UNAVAILABLE", detail: "服务报告没有 LKG", tone: "danger" };
  }
  if (available === true || status === "available" || status === "active" || hasSafeGeneration) {
    return {
      value: "AVAILABLE",
      detail: "服务报告存在 LKG；界面不回显其标识",
      tone: "reported",
    };
  }
  return unknownField("缺少严格的 LKG availability");
}

function sourceRow(payload: RagStatusResponse, source: QuerySource): OpsSourceRow {
  const record = sourceRecord(payload, source);
  if (!record) {
    const missing = unknownField("该来源没有返回状态");
    return {
      source,
      label: SOURCE_LABELS[source],
      v1: missing,
      v2: missing,
      fallback: missing,
      lastKnownGood: missing,
    };
  }
  return {
    source,
    label: SOURCE_LABELS[source],
    v1: stageField(first(record, ["v1_stage", "stage_v1", "v1.stage", "engines.v1.stage"])),
    v2: stageField(
      first(record, ["v2_stage", "stage_v2", "v2.stage", "engines.v2.stage", "stage"]),
    ),
    fallback: fallbackField(
      first(record, ["fallback", "fallback_to_v1", "v2.fallback", "routing.fallback"]),
    ),
    lastKnownGood: lastKnownGoodField(
      first(record, ["last_known_good", "lkg", "v2.last_known_good"]),
    ),
  };
}

function switchRows(payload: RagStatusResponse): OpsSwitchRow[] {
  const switches = asRecord(payload.component_switches);
  return SWITCH_ORDER.map((key) => {
    const value = switches?.[key];
    let state: OpsField;
    if (value === true) {
      state = { value: "ENABLED", detail: "服务报告此组件已启用", tone: "reported" };
    } else if (value === false) {
      state = { value: "DISABLED", detail: "服务报告此组件已禁用", tone: "danger" };
    } else {
      state = unknownField("缺少严格布尔值；不继承其他组件状态");
    }
    return { key, label: SWITCH_LABELS[key], state };
  });
}

function normalizePerformanceLabel(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const key = value.toLowerCase();
  return PERFORMANCE_LABELS[key] || null;
}

function latencyRows(value: unknown): OpsLatencyRow[] {
  const rows: Array<[unknown, unknown]> = [];
  if (Array.isArray(value)) {
    value.forEach((item) => {
      if (Array.isArray(item) && item.length >= 2) {
        rows.push([item[0], item[1]]);
        return;
      }
      const record = asRecord(item);
      if (record) rows.push([first(record, ["span", "name"]), record]);
    });
  } else {
    const record = asRecord(value);
    if (record) Object.entries(record).forEach((entry) => rows.push(entry));
  }
  return rows.flatMap(([rawName, rawDistribution]) => {
    const name = normalizePerformanceLabel(rawName);
    const distribution = asRecord(rawDistribution);
    if (!name || !distribution) return [];
    return [{
      name,
      count: integer(distribution.count),
      p50: finiteNumber(first(distribution, ["p50", "p50_ms"])),
      p95: finiteNumber(first(distribution, ["p95", "p95_ms"])),
      maximum: finiteNumber(first(distribution, ["maximum", "max", "max_ms"])),
    }];
  }).slice(0, 12);
}

function performanceIndexField(performance: Record<string, unknown> | null): OpsField {
  if (!performance) return unknownField("performance index 状态缺失");
  const index = asRecord(performance.index);
  const version = safeIdentifier(
    first(index || performance, ["version", "index_version"]),
  ) || safeIdentifier(typeof performance.index === "string" ? performance.index : null);
  const status = normalizeMember(first(index, ["status", "state"]));
  const exact = first(index, ["exact"]);
  const active = first(index, ["active_generations"]);
  const activeCount = Array.isArray(active)
    ? active.length
    : asRecord(active)
      ? Object.keys(asRecord(active) || {}).length
      : null;
  if (!version && !status && exact === undefined && activeCount === null) {
    return unknownField("performance index 字段缺失或已被安全过滤");
  }
  const details = [
    version ? `版本 ${version}` : null,
    exact === true ? "exact" : exact === false ? "non-exact" : null,
    activeCount === null ? null : `${activeCount} active generation`,
  ].filter(Boolean);
  return {
    value: status ? status.toUpperCase().replaceAll("_", " ") : version ? "REPORTED" : "UNKNOWN",
    detail: details.join(" · ") || "仅转述服务端 index 状态",
    tone:
      status === "unavailable" || status === "disabled"
        ? "danger"
        : status || version
          ? "neutral"
          : "unknown",
  };
}

function performanceCacheField(
  performance: Record<string, unknown> | null,
  dashboard: Record<string, unknown> | null,
): OpsField {
  if (!performance) return unknownField("performance cache 状态缺失");
  const cache = asRecord(performance.cache);
  const state = normalizeMember(
    first(cache, ["state", "status", "cache_state"]) ??
      first(dashboard, ["cache_state"]) ??
      performance.cache_state,
  );
  const hits = integer(first(cache, ["hits", "cache_hits"]) ?? first(dashboard, ["cache_hits"]));
  const allowed = new Set(["in_memory_ephemeral", "cold", "warm", "mixed", "unavailable"]);
  if (!state || !allowed.has(state)) {
    return unknownField("cache state 缺失或不在允许列表中");
  }
  return {
    value: state.toUpperCase().replaceAll("_", " "),
    detail: hits === null ? "服务报告的 cache state" : `${hits} cache hit`,
    tone: state === "unavailable" ? "danger" : state === "cold" ? "warning" : "neutral",
  };
}

function performanceDashboardField(
  performance: Record<string, unknown> | null,
  dashboard: Record<string, unknown> | null,
): OpsField {
  if (!performance) return unknownField("performance dashboard 状态缺失");
  const source = dashboard || performance;
  const traces = integer(first(source, ["trace_count"]));
  const spans = integer(first(source, ["span_count"]));
  const concurrency = integer(first(source, ["concurrency"]), 100_000);
  const profile = safeIdentifier(first(source, ["hardware_profile"]));
  if (traces === null && spans === null && concurrency === null && !profile) {
    return unknownField("dashboard 聚合字段缺失或已被安全过滤");
  }
  const details = [
    traces === null ? null : `${traces} trace`,
    spans === null ? null : `${spans} span`,
    concurrency === null ? null : `并发 ${concurrency}`,
    profile ? `profile ${profile}` : null,
  ].filter(Boolean);
  return {
    value: "AVAILABLE",
    detail: details.join(" · "),
    tone: "neutral",
  };
}

function performanceView(payload: RagStatusResponse): RagOpsViewModel["performance"] {
  const performance = asRecord(payload.performance);
  const gate = asRecord(payload.performance_snapshot);
  const dashboard = asRecord(performance?.dashboard);
  const latency = first(dashboard, ["latency"]) ?? first(performance, ["latency"]);
  const gateStatus = normalizeMember(first(gate, ["status"]));
  const gateDashboard: OpsField | null = gateStatus
    ? {
        value: gateStatus.toUpperCase().replaceAll("_", " "),
        detail:
          gateStatus === "unavailable"
            ? "规范 performance gate 不可用；没有生产观测证明"
            : "仅转述规范 performance gate；浏览器未复验",
        tone: gateStatus === "unavailable" ? "danger" : "neutral",
      }
    : null;
  return {
    index: performanceIndexField(performance),
    cache: performanceCacheField(performance, dashboard),
    dashboard: gateDashboard || performanceDashboardField(performance, dashboard),
    latency: latencyRows(latency),
  };
}

function freshnessField(payload: RagStatusResponse, now: number): {
  snapshotTime: string | null;
  field: OpsField;
} {
  const rawTimestamp = payload.generated_at ?? payload.observed_at ?? payload.snapshot_at;
  if (!rawTimestamp) {
    return {
      snapshotTime: null,
      field: unknownField("响应没有可验证的快照时间；不得视为新鲜"),
    };
  }
  const parsed = Date.parse(rawTimestamp);
  if (!Number.isFinite(parsed)) {
    return {
      snapshotTime: null,
      field: unknownField("快照时间格式无效；不得视为新鲜"),
    };
  }
  const snapshotTime = new Date(parsed).toISOString();
  const serverWindow = finiteNumber(payload.stale_after_seconds, 86_400);
  const staleAfter = serverWindow === null
    ? RAG_STATUS_STALE_AFTER_MS
    : serverWindow * 1000;
  const age = Math.max(0, now - parsed);
  if (payload.stale === true || age > staleAfter) {
    return {
      snapshotTime,
      field: {
        value: "STALE",
        detail: `快照超出 ${Math.round(staleAfter / 1000)} 秒新鲜度窗口`,
        tone: "danger",
      },
    };
  }
  if (now < parsed - 60_000) {
    return {
      snapshotTime,
      field: unknownField("快照时间明显晚于浏览器时间；新鲜度不可确认"),
    };
  }
  return {
    snapshotTime,
    field: {
      value: "CURRENT",
      detail: "快照时间位于前端新鲜度窗口内",
      tone: "reported",
    },
  };
}

export function normalizeRagStatus(
  payload: RagStatusResponse,
  now = Date.now(),
): RagOpsViewModel {
  const release = asRecord(
    payload.canonical_release ??
      payload.release ??
      payload.release_authority,
  );
  const freshness = freshnessField(payload, now);
  return {
    schemaVersion:
      safeIdentifier(payload.schema_version ?? payload.status_version) || "UNKNOWN",
    snapshotTime: freshness.snapshotTime,
    freshness: freshness.field,
    defaultEngine: defaultEngineField(
      payload.default_engine ?? first(release, ["default_engine"]),
    ),
    qualityHold: qualityHoldField(
      payload.quality_hold ?? first(release, ["quality_hold"]),
    ),
    releaseDecision: releaseDecisionField(
      first(release, ["decision"]) ?? payload.decision,
    ),
    releaseAuthority: releaseAuthorityField(release),
    releaseIntegrity: releaseIntegrityField(release),
    reviewedCalibration: reviewedCalibrationField(payload.reviewed_calibration),
    sources: SOURCE_ORDER.map((source) => sourceRow(payload, source)),
    switchesVersion:
      safeIdentifier(first(payload.component_switches, ["version"])) || "UNKNOWN",
    switches: switchRows(payload),
    performance: performanceView(payload),
  };
}
