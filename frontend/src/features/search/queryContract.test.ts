import { describe, expect, it } from "vitest";
import type { TrustedQueryResponse } from "../../lib/types";
import {
  queryBlockers,
  queryEngineRouting,
  queryPresentationMode,
  safeMetadataValue,
  sanitizeDisplayText,
} from "./queryContract";

function response(
  state: string,
  answerMode?: string,
  answer: Partial<TrustedQueryResponse["answer"]> = {},
): TrustedQueryResponse {
  return {
    query_id: `query://${state}`,
    state,
    answer: {
      status: state,
      answer_mode: answerMode,
      ...answer,
    },
  };
}

describe("query response compatibility", () => {
  it.each([
    [response("needs_clarification"), "clarification"],
    [response("partial", "partial"), "partial"],
    [response("refused", "refusal", { refusal: true }), "refusal"],
    [response("retrieval_only"), "retrieval_only"],
    [response("complete", "grounded_generation", { status: "supported" }), "grounded"],
    [response("complete", undefined, { status: "insufficient_evidence" }), "retrieval_only"],
  ] as const)("normalizes old and new response states", (payload, expected) => {
    expect(queryPresentationMode(payload)).toBe(expected);
  });

  it("does not render absolute local paths as metadata", () => {
    expect(safeMetadataValue("/Users/private/secret.db")).toBe("受限本地来源");
    expect(safeMetadataValue("file:///private/secret.db")).toBe("受限本地来源");
    expect(safeMetadataValue("commit:abc123")).toBe("commit:abc123");
  });

  it("fails closed when any response status drifts from the known contract", () => {
    expect(
      queryPresentationMode({
        query_id: "query://drift",
        state: "complete_v3",
        answer: {
          status: "supported",
          answer_mode: "grounded_generation",
          claim_verification: { supported: true },
        },
      }),
    ).toBe("unverified");
  });

  it("normalizes global and source engine routing without losing blockers", () => {
    const payload = {
      query_id: "query://routing",
      state: "retrieval_only",
      answer: { status: "retrieval_only", answer_mode: "retrieval_only" },
      source_status: {
        code: {
          status: "complete",
          routing: {
            requested: "v2",
            selected: "v1",
            fallback: "legacy_v1",
            blockers: ["calibration_unavailable:code"],
          },
        },
      },
      evidence_pack: {
        trace: {
          platform: {
            global_engine: {
              requested: "v2",
              selected: "v1",
              fallback: "legacy_v1",
              blockers: ["calibration_unavailable:global"],
            },
          },
        },
      },
    } as unknown as TrustedQueryResponse;

    expect(queryEngineRouting(payload)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ source: "global", requested: "v2", selected: "v1" }),
        expect.objectContaining({ source: "code", requested: "v2", selected: "v1" }),
      ]),
    );
    expect(queryBlockers(payload)).toEqual([
      "calibration_unavailable:global",
      "calibration_unavailable:code",
    ]);
  });

  it("prefers platform-governed source routing over legacy execution fields", () => {
    const payload: TrustedQueryResponse = {
      query_id: "query://governed-fallback",
      state: "refused",
      answer: { status: "refused", answer_mode: "refusal" },
      evidence_pack: {
        trace: {
          sources: {
            notebook: {
              status: "complete",
              engine_requested: "v1",
              engine_used: "v1",
            },
          },
          platform: {
            source_engine_routing: {
              notebook: {
                requested: "v2",
                selected: "v1",
                served_engine: "v1",
                fallback_reason: "authority_unavailable",
                reason_code: "verified_authority_unavailable",
              },
            },
          },
        },
      },
    };

    expect(queryEngineRouting(payload)).toEqual([
      expect.objectContaining({
        source: "notebook",
        requested: "v2",
        selected: "v1",
        fallback: "authority_unavailable",
        reason: "verified_authority_unavailable",
      }),
    ]);
  });

  it("prefers a nested release route when platform routing is absent", () => {
    const payload: TrustedQueryResponse = {
      query_id: "query://release-route-fallback",
      state: "refused",
      answer: { status: "refused", answer_mode: "refusal" },
      evidence_pack: {
        trace: {
          sources: {
            document: {
              status: "complete",
              engine_requested: "v1",
              engine_used: "v1",
              release_route: {
                requested_engine: "v2",
                selected_engine: "v1",
                served_engine: "v1",
                fallback_reason: "authority_unavailable",
                reason_code: "verified_authority_unavailable",
              },
            },
          },
        },
      },
    };

    expect(queryEngineRouting(payload)).toEqual([
      expect.objectContaining({
        source: "document",
        requested: "v2",
        selected: "v1",
        fallback: "authority_unavailable",
        reason: "verified_authority_unavailable",
      }),
    ]);
  });

  it("redacts credentials, ACL values and local paths inside display text", () => {
    const safe = sanitizeDisplayText(
      "token=top-secret ACL_REF=project:private at /Users/private/keys.env",
    );
    expect(safe).not.toMatch(/top-secret|project:private|Users\/private/);
    expect(safe).toContain("受限");
  });
});
