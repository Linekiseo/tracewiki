import { describe, expect, it } from "vitest";
import type {
  CodeCommitDetail,
  CodeRefs,
  GitCommit,
  GraphSnapshot,
  Repository,
} from "../../lib/types";
import {
  buildCodeRagGraph,
  CODE_GRAPH_NETWORK_FOOTPRINT_HEIGHT,
  CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH,
  CODE_GRAPH_TYPES,
  layoutCodeRagNetwork,
  zoomCodeGraphView,
} from "./codeRagGraph";

const repository: Repository = {
  id: "repo://local/rag",
  project_id: "project-rag",
  name: "rag",
  source_type: "local",
  source_url: null,
  local_path: "/private/not-rendered",
  default_branch: "main",
  head_commit: "aaaa+dirty.snapshot",
  active_generation_id: "generation-7",
  status: "ready",
  updated_at: "2026-08-03T00:00:00Z",
  stats: {
    files: 4,
    symbols: 9,
    commits: 2,
    diff_hunks: 5,
    test_results: 1,
  },
};

const refs: CodeRefs = {
  repository_id: repository.id,
  head: { name: "main", sha: repository.head_commit, branch: "main" },
  head_sha: repository.head_commit,
  default_branch: "main",
  branches: [
    {
      id: "branch://main",
      name: "main",
      head_sha: "aaaa",
      is_default: true,
      observed_at: "2026-08-03T00:00:00Z",
    },
  ],
};

const history: GitCommit[] = [
  {
    id: "commit://aaaa",
    repository_id: repository.id,
    sha: "aaaa",
    author_name: "Ada",
    authored_at: "2026-08-03T00:00:00Z",
    committed_at: "2026-08-03T00:00:00Z",
    message: "graph panel",
    diff_count: 2,
    test_count: 1,
    parent_shas: ["bbbb"],
  },
  {
    id: "commit://bbbb",
    repository_id: repository.id,
    sha: "bbbb",
    author_name: "Ada",
    authored_at: "2026-08-02T00:00:00Z",
    committed_at: "2026-08-02T00:00:00Z",
    message: "parent",
    diff_count: 1,
    test_count: 0,
    parent_shas: [],
  },
];

const snapshot: GraphSnapshot = {
  domain: "code",
  mode: "relations",
  query: "",
  nodes: [
    {
      id: "file://panel",
      type: "FileVersion",
      label: "CodeRagGraphPanel.tsx",
      locator: "code://rag/CodeRagGraphPanel.tsx",
      domain: "code",
      repository_id: repository.id,
      version: repository.head_commit,
      path: "frontend/src/features/code/CodeRagGraphPanel.tsx",
      language: "tsx",
    },
    {
      id: "symbol://panel",
      type: "CodeSymbol",
      label: "CodeRagGraphPanel",
      locator: "code://rag/CodeRagGraphPanel.tsx#CodeRagGraphPanel",
      domain: "code",
      repository_id: repository.id,
      version: repository.head_commit,
      path: "frontend/src/features/code/CodeRagGraphPanel.tsx",
      start_line: 1,
      end_line: 40,
    },
    {
      id: "diff://panel",
      type: "DiffHunk",
      label: "CodeRagGraphPanel.tsx",
      locator: "diff://panel#L1",
      domain: "code",
      repository_id: repository.id,
      version: "aaaa",
    },
    {
      id: "test://panel",
      type: "TestResult",
      label: "vitest",
      locator: "test://panel",
      domain: "code",
      repository_id: repository.id,
      version: "aaaa",
    },
    {
      id: "file://other",
      type: "FileVersion",
      label: "foreign.py",
      locator: "code://other/foreign.py",
      domain: "code",
      repository_id: "repo://other",
    },
  ],
  edges: [
    {
      id: "edge://defines",
      source: "file://panel",
      predicate: "DEFINES",
      target: "symbol://panel",
      domain: "code",
    },
    {
      id: "edge://validates",
      source: "symbol://panel",
      predicate: "VALIDATED_BY",
      target: "test://panel",
      domain: "code",
    },
  ],
  metadata: {
    total_nodes: 9000,
    total_edges: 28000,
    visible_nodes: 5,
    visible_edges: 2,
    sampled: true,
  },
};

describe("buildCodeRagGraph", () => {
  it("composes Git authority, worktree projection and graph entities", () => {
    const model = buildCodeRagGraph({ repository, refs, history, snapshot });

    expect(new Set(model.nodes.map((node) => node.type))).toEqual(
      new Set(CODE_GRAPH_TYPES),
    );
    expect(model.nodes.some((node) => node.id === "file://other")).toBe(false);
    expect(model.edges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ predicate: "PARENT" }),
        expect.objectContaining({ predicate: "DEFINES", origin: "graph" }),
        expect.objectContaining({ predicate: "VALIDATED_BY", origin: "graph" }),
        expect.objectContaining({
          predicate: "HAS_SNAPSHOT",
          origin: "projection",
        }),
      ]),
    );
    expect(model.counts.FileVersion).toBe(4);
    expect(model.counts.CodeSymbol).toBe(9);
    expect(model.counts.DiffHunk).toBe(5);
    expect(model.sampled).toBe(true);
  });

  it("filters nodes and relations without inventing missing endpoints", () => {
    const model = buildCodeRagGraph({
      repository,
      refs,
      history,
      snapshot,
      query: "CodeRagGraphPanel",
      enabledTypes: new Set([
        "Repository",
        "WorktreeSnapshot",
        "FileVersion",
        "CodeSymbol",
      ]),
      enabledRelations: new Set(["DEFINES"]),
    });

    expect(model.nodes.map((node) => node.type)).toEqual(
      expect.arrayContaining([
        "Repository",
        "WorktreeSnapshot",
        "FileVersion",
        "CodeSymbol",
      ]),
    );
    expect(model.edges).toHaveLength(1);
    expect(model.edges[0].predicate).toBe("DEFINES");
  });

  it("loads real DiffHunk and TestResult nodes from commit detail", () => {
    const detail: CodeCommitDetail = {
      ...history[0],
      diff_hunks: [
        {
          id: "diff://latest",
          repository_id: repository.id,
          commit_id: history[0].id,
          commit_sha: history[0].sha,
          parent_sha: history[1].sha,
          path: "frontend/src/features/code/CodeRagGraphPanel.tsx",
          change_type: "M",
          new_start: 20,
          new_count: 4,
          source_locator: "rag@aaaa:CodeRagGraphPanel.tsx#diff",
          affected_symbols: ["CodeRagGraphPanel"],
        },
      ],
      test_results: [
        {
          id: "test://latest",
          repository_id: repository.id,
          commit_id: history[0].id,
          commit_sha: history[0].sha,
          command: "npm test",
          status: "passed",
          exit_code: 0,
        },
      ],
    };
    const model = buildCodeRagGraph({
      repository,
      refs,
      history,
      snapshot: { ...snapshot, nodes: [], edges: [] },
      commitDetails: [detail],
    });

    expect(model.nodes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "diff://latest", type: "DiffHunk" }),
        expect.objectContaining({ id: "test://latest", type: "TestResult" }),
      ]),
    );
    expect(model.edges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          source: history[0].id,
          predicate: "CONTAINS",
          target: "diff://latest",
        }),
        expect.objectContaining({
          source: history[0].id,
          predicate: "VALIDATED_BY",
          target: "test://latest",
        }),
      ]),
    );
  });

  it("produces a deterministic bounded network layout", () => {
    const model = buildCodeRagGraph({ repository, refs, history, snapshot });
    const first = layoutCodeRagNetwork(model.nodes, model.edges);
    const second = layoutCodeRagNetwork(model.nodes, model.edges);

    expect(first).toEqual(second);
    expect(
      new Set(Object.values(first).map(({ x, y }) => `${x}:${y}`)).size,
    ).toBe(model.nodes.length);
    for (const point of Object.values(first)) {
      expect(point.x).toBeGreaterThanOrEqual(24);
      expect(point.y).toBeGreaterThanOrEqual(24);
      expect(point.x + CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH).toBeLessThanOrEqual(
        1280 - 24,
      );
      expect(point.y + CODE_GRAPH_NETWORK_FOOTPRINT_HEIGHT).toBeLessThanOrEqual(
        820 - 24,
      );
    }
    const placed = Object.values(first);
    for (let left = 0; left < placed.length; left += 1) {
      for (let right = left + 1; right < placed.length; right += 1) {
        const overlapX =
          CODE_GRAPH_NETWORK_FOOTPRINT_WIDTH +
          8 -
          Math.abs(placed[left].x - placed[right].x);
        const overlapY =
          CODE_GRAPH_NETWORK_FOOTPRINT_HEIGHT +
          8 -
          Math.abs(placed[left].y - placed[right].y);
        expect(overlapX > 0 && overlapY > 0).toBe(false);
      }
    }
  });

  it("keeps the zoom anchor fixed in graph coordinates", () => {
    const view = { x: 40, y: -18, scale: 0.8 };
    const anchor = { x: 510, y: 320 };
    const before = {
      x: (anchor.x - view.x) / view.scale,
      y: (anchor.y - view.y) / view.scale,
    };
    const next = zoomCodeGraphView(view, anchor, 1.42);

    expect((anchor.x - next.x) / next.scale).toBeCloseTo(before.x, 8);
    expect((anchor.y - next.y) / next.scale).toBeCloseTo(before.y, 8);
  });
});
