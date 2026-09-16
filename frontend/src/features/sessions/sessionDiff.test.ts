import { describe, expect, it } from "vitest";
import { findSessionDiffEvidence, parseSessionDiff } from "./sessionDiff";

describe("session diff audit", () => {
  it("parses additions, deletions, context, and original/new line numbers", () => {
    const diff = parseSessionDiff(
      [
        "@@ -10,4 +10,5 @@ def rank(items):",
        " def rank(items):",
        "-    return sorted(items)",
        "+    ranked = sorted(items)",
        "+    return ranked",
        " ",
      ].join("\n"),
    );

    expect(diff.hunks).toHaveLength(1);
    expect(diff.additions).toBe(2);
    expect(diff.deletions).toBe(1);
    expect(diff.hunks[0].lines[1]).toMatchObject({
      kind: "deletion",
      oldLine: 11,
      newLine: null,
      content: "    return sorted(items)",
    });
    expect(diff.hunks[0].lines[2]).toMatchObject({
      kind: "addition",
      oldLine: null,
      newLine: 11,
      content: "    ranked = sorted(items)",
    });
  });

  it("prefers the original recorded patch over secondary evidence", () => {
    const evidence = findSessionDiffEvidence(
      {
        id: "change-1",
        path: "src/ranker.py",
        change_type: "update",
        status: "completed",
        locator: "codex://change-1",
        patch: "@@ -1,1 +1,1 @@\n-old\n+new",
      },
      [
        {
          entity_id: "code://ranker",
          entity_type: "CodeFile",
          source: "code",
          title: "ranker.py",
          subtitle: "src/ranker.py",
          locator: "repo@sha:src/ranker.py#L1-L2",
          snippet: "@@ -1,1 +1,1 @@\n-a\n+b",
          status: "indexed",
          channels: ["lexical"],
          path: "src/ranker.py",
        },
      ],
      [],
    );

    expect(evidence).toMatchObject({
      origin: "recorded",
      label: "会话原始补丁",
    });
  });
});

