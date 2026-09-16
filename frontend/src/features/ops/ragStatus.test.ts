import { describe, expect, it } from "vitest";
import type { RagStatusResponse } from "../../lib/types";
import { normalizeRagStatus } from "./ragStatus";

const snapshotTime = "2026-07-31T08:00:00.000Z";
const now = Date.parse("2026-07-31T08:00:20.000Z");

function completeStatus(): RagStatusResponse {
  return {
    schema_version: "rag-ops-status-v1",
    generated_at: snapshotTime,
    stale: false,
    default_engine: "v1",
    quality_hold: true,
    canonical_release: {
      decision: "quality_hold",
      authority_id: "rag-canonical-production-release-authority-v2",
      authority_ready: false,
      authority_attested: false,
      integrity_sha256: `sha256:${"a".repeat(64)}`,
    },
    sources: {
      code: {
        v1_stage: "default_v1",
        v2_stage: "offline",
        fallback: { active: true, target: "v1" },
        last_known_good: {
          available: true,
          generation: `sha256:${"b".repeat(64)}`,
        },
      },
      codex: {
        v1: { stage: "serving" },
        v2: { stage: "shadow_internal_100" },
        fallback_to_v1: false,
        lkg: false,
      },
      experiment: {
        v1_stage: "active",
        v2_stage: "canary_5",
        fallback: false,
        last_known_good: true,
      },
      notebook: {
        v1_stage: "active",
        v2_stage: "offline",
        fallback: "v1",
        last_known_good: "notebook-generation-v1",
      },
      document: {
        v1_stage: "serving",
        v2_stage: "shadow",
        fallback: false,
        last_known_good: { available: false },
      },
      workspace: {
        v1_stage: "default",
        v2_stage: "not_qualified",
        fallback: { active: false },
        last_known_good: { status: "available" },
      },
    },
    component_switches: {
      version: "global-component-switches-v2",
      generator_prompt: true,
      context_packer: true,
      fusion: true,
      reranker: false,
      embedding_generation: true,
      source_retrievers: true,
      planner: false,
    },
    performance: {
      index: {
        index_version: "exact-memory-vector-index-v2",
        exact: true,
        active_generations: {
          code: `sha256:${"c".repeat(64)}`,
        },
      },
      cache: { state: "in_memory_ephemeral", hits: 2 },
      dashboard: {
        trace_count: 4,
        span_count: 7,
        concurrency: 1,
        hardware_profile: "repository-runtime-unqualified",
        latency: [
          ["global_v2_execution", { count: 4, p50: 12, p95: 28, maximum: 31 }],
          ["source_retrieval/code", { count: 3, p50: 3, p95: 7, maximum: 8 }],
        ],
      },
    },
    reviewed_calibration: {
      available: false,
      profile_count: 0,
    },
  };
}

describe("normalizeRagStatus", () => {
  it("keeps release, sources and all seven component switches independent", () => {
    const model = normalizeRagStatus(completeStatus(), now);

    expect(model.defaultEngine.value).toBe("V1");
    expect(model.qualityHold.value).toBe("ACTIVE");
    expect(model.releaseDecision.value).toBe("QUALITY HOLD");
    expect(model.freshness.value).toBe("CURRENT");
    expect(model.reviewedCalibration.value).toBe("UNAVAILABLE");
    expect(model.sources).toHaveLength(6);
    expect(model.sources[0].fallback.value).toBe("ACTIVE → V1");
    expect(model.sources[1].v2.value).toBe("SHADOW INTERNAL 100%");
    expect(model.switches).toHaveLength(7);
    expect(model.switches.find((item) => item.key === "reranker")?.state.value).toBe(
      "DISABLED",
    );
    expect(model.switches.find((item) => item.key === "generator_prompt")?.state.value).toBe(
      "ENABLED",
    );
    expect(model.performance.latency).toEqual([
      { name: "Global V2", count: 4, p50: 12, p95: 28, maximum: 31 },
      { name: "来源检索 / 代码", count: 3, p50: 3, p95: 7, maximum: 8 },
    ]);
  });

  it("fails closed and never reflects secrets or paths from unknown fields", () => {
    const payload: RagStatusResponse = {
      generated_at: snapshotTime,
      default_engine: "v3",
      canonical_release: {
        decision: "release",
        authority_id: "/Users/private/release-authority",
        integrity: "secret=sk-proj-should-not-render",
      },
      sources: {
        code: {
          v1_stage: "token-private-stage",
          v2_stage: "/private/runtime/stage",
          fallback: { active: true, target: "unknown-engine" },
          last_known_good: "/private/index/sidecar",
        },
      },
      component_switches: {
        generator_prompt: true,
      },
      performance: {
        index: { version: "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" },
        cache: { state: "acl-team-secret" },
        dashboard: { hardware_profile: "/Users/private/hardware" },
      },
    };

    const model = normalizeRagStatus(payload, now);
    const renderedModel = JSON.stringify(model);

    expect(model.defaultEngine.value).toBe("UNKNOWN");
    expect(model.releaseAuthority.value).toBe("UNKNOWN");
    expect(model.releaseIntegrity.value).toBe("UNKNOWN");
    expect(model.sources[0].v1.value).toBe("UNKNOWN");
    expect(model.sources[0].lastKnownGood.value).toBe("UNKNOWN");
    expect(model.switches.find((item) => item.key === "planner")?.state.value).toBe(
      "UNKNOWN",
    );
    expect(renderedModel).not.toContain("/Users/private");
    expect(renderedModel).not.toContain("sk-proj");
    expect(renderedModel).not.toContain("ghp_");
    expect(renderedModel).not.toContain("acl-team-secret");
    expect(renderedModel).not.toContain("sidecar");
  });

  it("marks old and future-dated responses as non-current", () => {
    const old = completeStatus();
    old.generated_at = "2026-07-31T07:40:00.000Z";
    old.stale = false;
    expect(normalizeRagStatus(old, now).freshness.value).toBe("STALE");

    const future = completeStatus();
    future.generated_at = "2026-07-31T09:00:00.000Z";
    future.stale = false;
    expect(normalizeRagStatus(future, now).freshness.value).toBe("UNKNOWN");
  });

  it("reads the canonical release fields from the runtime status v2 projection", () => {
    const model = normalizeRagStatus(
      {
        schema_version: "rag-ops-status-v1",
        status_version: "rag-runtime-release-status-v2",
        generated_at: snapshotTime,
        default_engine: "v1",
        quality_hold: true,
        decision: "quality_hold",
        release_authority: {
          decision: "quality_hold",
          authority_id: "rag-canonical-production-release-authority-v2",
          authority_ready: false,
          authority_attested: false,
          integrity_sha256: `sha256:${"e".repeat(64)}`,
        },
        source_snapshot: {
          status: "unavailable",
          sources: [{ source: "code", availability: "unavailable" }],
        },
        sources: Object.fromEntries(
          (["code", "codex", "experiment", "notebook", "document", "workspace"] as const).map(
            (source) => [
              source,
              {
                source,
                v1_stage: "DEFAULT_V1",
                v2_stage: "NO_RELEASE",
                quality_state: "QUALITY_HOLD",
                fallback: "DEFAULT_V1",
                last_known_good: "UNAVAILABLE",
                authority_availability: "UNAVAILABLE",
                runtime_availability: "AVAILABLE",
              },
            ],
          ),
        ),
        component_switches: {
          version: "global-component-switches-v2",
          generator_prompt: true,
          context_packer: true,
          fusion: true,
          reranker: true,
          embedding_generation: true,
          source_retrievers: true,
          planner: true,
        },
        performance: {
          index: {
            availability: "AVAILABLE",
            status: "AVAILABLE",
            index_version: "exact-memory-vector-index-v2",
            exact: true,
          },
          cache: {
            availability: "AVAILABLE",
            status: "AVAILABLE",
            state: "in_memory_ephemeral",
            hits: 0,
          },
          dashboard: {
            availability: "AVAILABLE",
            trace_count: 0,
            span_count: 0,
            latency_availability: "UNAVAILABLE",
          },
          latency: {
            availability: "UNAVAILABLE",
            exact_aggregation_available: false,
            trace_count: 0,
            span_count: 0,
          },
        },
        performance_snapshot: {
          status: "unavailable",
          qualified: false,
          production_observation: false,
        },
        reviewed_calibration: {
          available: false,
          status: "UNAVAILABLE",
          profile_count: 0,
        },
      },
      now,
    );

    expect(model.schemaVersion).toBe("rag-ops-status-v1");
    expect(model.releaseDecision.value).toBe("QUALITY HOLD");
    expect(model.releaseAuthority.value).toBe(
      "rag-canonical-production-release-authority-v2",
    );
    expect(model.releaseIntegrity.value).toBe("DIGEST PROVIDED");
    expect(model.performance.dashboard.value).toBe("UNAVAILABLE");
    expect(model.sources[0].v1.value).toBe("DEFAULT V1");
    expect(model.sources[0].v2.value).toBe("NO RELEASE");
    expect(model.sources[0].fallback.value).toBe("DEFAULT → V1");
    expect(model.sources[0].lastKnownGood.value).toBe("UNAVAILABLE");
    expect(model.switches).toHaveLength(7);
    expect(model.performance.index.value).toBe("AVAILABLE");
    expect(model.performance.cache.value).toBe("IN MEMORY EPHEMERAL");
    expect(model.freshness.value).toBe("CURRENT");
  });
});
