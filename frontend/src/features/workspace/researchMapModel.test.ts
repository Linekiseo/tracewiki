import { describe, expect, it } from "vitest";
import type { ProjectDashboard } from "../../lib/types";
import {
  buildResearchGraph,
  buildCodexBoard,
  buildCodexOverviewBoard,
  buildCodeBoard,
  buildCodeCollaborationBoard,
  buildInspectorContextMap,
  buildCodeRelationBoard,
  buildCodeVectorBoard,
  buildEvidenceReviewProfile,
  buildResearchOverview,
  buildTopicBoard,
  codeNodeBehaviorFacts,
  connectedEdges,
  describeCodeNode,
  graphBounds,
  predicateLabel,
  type ResearchMapNode,
} from "./researchMapModel";
import {
  buildVectorLayout,
  simulateVectorLayoutStep,
  vectorLayoutBounds,
} from "./codeVectorGraphModel";

const dashboard = {
  project: {
    id: "project-rag",
    name: "多源协同 RAG",
    description: "科研项目",
    owner: "researcher",
    acl_ref: "project:project-rag",
    classification: "internal",
    status: "active",
    created_at: "2026-07-24T00:00:00Z",
    updated_at: "2026-07-24T00:00:00Z",
    topic_count: 1,
    active_iteration_count: 1,
    settings: {},
  },
  stats: {
    repositories: 1,
    sessions: 1,
    relations: 1,
    pending_reviews: 1,
    topics: 1,
    active_topics: 1,
    iterations: 1,
    active_iterations: 1,
    linked_evidence: 1,
    work_items: 1,
    active_work_items: 1,
  },
  recent_topics: [
    {
      id: "topic://one",
      display_key: "H1",
      project_id: "project-rag",
      title: "研究假设",
      problem_statement: "问题",
      objective: "目标",
      status: "active",
      priority: 1,
      owner: "researcher",
      updated_at: "2026-07-24T00:00:00Z",
      tags: [],
    },
  ],
  active_iterations: [
    {
      id: "iteration://one",
      display_key: "I1",
      title: "验证迭代",
      status: "running",
      progress: 40,
      evidence_count: 2,
      updated_at: "2026-07-24T00:00:00Z",
    },
  ],
  active_work_items: [
    {
      id: "work://one",
      display_key: "W1",
      project_id: "project-rag",
      title: "实现任务",
      objective: "完成实现",
      kind: "development",
      status: "ready",
      summary: "",
      updated_at: "2026-07-24T00:00:00Z",
    },
  ],
  recent_activity: [],
} satisfies ProjectDashboard;

describe("research map model", () => {
  it("builds a fail-closed evidence profile with directed, resolvable relations", () => {
    const focus = {
      id: "codex://thread/one",
      type: "CodexThread",
      domain: "codex" as const,
      label: "来源会话",
      meta: "会话元数据",
      locator: "",
      x: 0,
      y: 0,
      source: "graph" as const,
    };
    const target = {
      ...focus,
      id: "codex://thread/two",
      label: "目标会话",
    };
    const edge = {
      id: "relation://one/two",
      source: focus.id,
      predicate: "continues_to",
      target: target.id,
      label: "继续",
      tone: "codex" as const,
      review_status: "unreviewed",
    };
    const profile = buildEvidenceReviewProfile({
      node: focus,
      edges: [
        edge,
        { ...edge, id: "relation://missing", target: "missing://node" },
      ],
      nodesById: new Map([
        [focus.id, focus],
        [target.id, target],
      ]),
      projectId: "project-rag",
    });

    expect(profile.identity).toEqual({ value: focus.id, available: true });
    expect(profile.source.value).toBe("关系图谱载荷");
    expect(profile.scope.value).toBe("project-rag");
    expect(profile.acl).toEqual({ value: "ACL 未提供", available: false });
    expect(profile.version.available).toBe(false);
    expect(profile.citation.available).toBe(false);
    expect(profile.summary).toEqual({
      value: "内容摘要未提供",
      available: false,
    });
    expect(profile.outgoing).toHaveLength(1);
    expect(profile.outgoing[0]).toMatchObject({
      direction: "outgoing",
      source: { id: focus.id },
      target: { id: target.id },
    });
    expect(profile.reviewCandidateId).toBeUndefined();
  });

  it("exposes only a server-backed review candidate id", () => {
    const source: ResearchMapNode = {
      id: "codex://thread/one",
      type: "CodexThread",
      domain: "codex" as const,
      label: "来源会话",
      meta: "",
      locator: "codex://thread/one",
      x: 0,
      y: 0,
      source: "binding" as const,
    };
    const target: ResearchMapNode = {
      ...source,
      id: "code://file/one",
      label: "目标文件",
      domain: "code",
    };
    const profile = buildEvidenceReviewProfile({
      node: source,
      edges: [
        {
          id: "binding-edge://one",
          source: source.id,
          predicate: "changed_path_maps_to",
          target: target.id,
          label: "变更映射",
          tone: "review",
          review_status: "unreviewed",
          reviewCandidateId: "binding-one",
        },
      ],
      nodesById: new Map([
        [source.id, source],
        [target.id, target],
      ]),
      projectId: "project-rag",
      projectAclRef: "project:project-rag",
    });

    expect(profile.reviewCandidateId).toBe("binding-one");
    expect(profile.acl).toEqual({
      value: "project:project-rag",
      available: true,
    });
  });

  it("assembles real workspace, code and Codex nodes with cross-source bindings", () => {
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: "codex://thread/thread-one",
          type: "CodexThread",
          label: "实现研究任务",
          locator: "codex://thread/thread-one",
          domain: "codex",
        },
      ],
      graphEdges: [],
      bindings: [
        {
          id: "binding://one",
          project_id: "project-rag",
          source_title: "实现研究任务",
          source_thread_id: "thread-one",
          source_item_type: "FileChange",
          changed_path: "src/app.ts",
          repository_name: "rag",
          target_entity_id: "code://rag/src/app.ts",
          target_path: "src/app.ts",
          target_commit_sha: "abcdef123456",
          derivation: "path_match",
          confidence: 0.96,
          review_status: "unreviewed",
          created_at: "2026-07-24T00:00:00Z",
        },
      ],
    });

    expect(result.nodes.some((node) => node.domain === "workspace")).toBe(true);
    expect(result.nodes.some((node) => node.domain === "codex")).toBe(true);
    expect(result.nodes.some((node) => node.domain === "code")).toBe(true);
    expect(
      result.edges.some((edge) => edge.predicate === "changed_path_maps_to"),
    ).toBe(true);
    expect(
      connectedEdges("codex://thread/thread-one", result.edges),
    ).toHaveLength(1);
    const codex = result.nodes.find(
      (node) => node.id === "codex://thread/thread-one",
    )!;
    const code = result.nodes.find(
      (node) => node.id === "code://rag/src/app.ts",
    )!;
    expect(Math.hypot(codex.x - code.x, codex.y - code.y)).toBeLessThan(520);
    expect(graphBounds(result.nodes).width).toBeGreaterThan(500);
    expect(graphBounds(result.nodes).height).toBeGreaterThan(350);
    expect(
      new Set(result.nodes.map((node) => `${node.x}:${node.y}`)).size,
    ).toBe(result.nodes.length);
  });

  it("projects governed session-to-file impacts without collapsing files onto a shared commit", () => {
    const shared = {
      id: "binding://one",
      project_id: "project-rag",
      source_title: "修复图谱交互",
      source_thread_id: "thread-impact",
      source_entity_id: "codex://thread/thread-impact/turn/one/item/change",
      source_item_type: "FileChange",
      changed_path: "nactive",
      repository_id: "repo-rag",
      repository_name: "rag",
      target_entity_id: "code://repo-rag@abcdef123456",
      target_path: "frontend/src/features/workspace/ResearchMap.tsx",
      target_commit_sha: "abcdef123456",
      derivation: "path_exact",
      confidence: 0.97,
      review_status: "confirmed",
      created_at: "2026-08-02T00:00:00Z",
    } as const;
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [],
      graphEdges: [],
      bindings: [
        shared,
        {
          ...shared,
          id: "binding://two",
          source_entity_id:
            "codex://thread/thread-impact/turn/one/item/change-two",
          target_path: "frontend/src/styles/app.css",
        },
      ],
    });
    const thread = "codex://thread/thread-impact";
    const fileNodes = result.nodes.filter(
      (node) => node.domain === "code" && node.type === "FileVersion",
    );
    const directImpacts = result.edges.filter(
      (edge) =>
        edge.source === thread && edge.predicate === "changed_path_maps_to",
    );

    expect(fileNodes.map((node) => node.path).sort()).toEqual([
      "frontend/src/features/workspace/ResearchMap.tsx",
      "frontend/src/styles/app.css",
    ]);
    expect(new Set(fileNodes.map((node) => node.id)).size).toBe(2);
    expect(directImpacts).toHaveLength(2);
    expect(result.nodes.some((node) => node.label === "nactive")).toBe(false);

    const overview = buildResearchOverview(result.nodes, result.edges);
    expect(
      overview.edges.filter(
        (edge) =>
          edge.source === thread && edge.predicate === "changed_path_maps_to",
      ),
    ).toHaveLength(2);
    const overviewProject = overview.nodes.find(
      (node) => node.type === "ResearchProject",
    )!;
    const overviewThread = overview.nodes.find((node) => node.id === thread)!;
    const overviewFiles = overview.nodes.filter(
      (node) => node.domain === "code",
    );
    expect(overviewProject.x).toBeLessThan(overviewThread.x);
    expect(overviewFiles.every((node) => node.x > overviewThread.x)).toBe(true);
    expect(
      Math.abs(overviewFiles[0].x - overviewFiles[1].x) >= 220 ||
        Math.abs(overviewFiles[0].y - overviewFiles[1].y) >= 88,
    ).toBe(true);
  });

  it("drops bindings whose governed target cannot identify a repository-relative code file", () => {
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [],
      graphEdges: [],
      bindings: [
        {
          id: "binding://unsafe",
          project_id: "project-rag",
          source_title: "错误路径",
          source_thread_id: "thread-unsafe",
          source_item_type: "FileChange",
          changed_path: "../../secret",
          repository_name: "rag",
          target_entity_id: "code://repo-rag@abcdef123456",
          target_path: "/Users/example/private.py",
          target_commit_sha: "abcdef123456",
          derivation: "path_exact",
          confidence: 0.97,
          review_status: "unreviewed",
          created_at: "2026-08-02T00:00:00Z",
        },
      ],
    });

    expect(result.nodes.some((node) => node.domain === "codex")).toBe(false);
    expect(
      result.edges.some((edge) => edge.predicate === "changed_path_maps_to"),
    ).toBe(false);
  });

  it("presents stable relation labels", () => {
    expect(predicateLabel("HAS_TURN")).toBe("包含轮次");
    expect(predicateLabel("changed_path_maps_to")).toBe("变更映射");
    expect(predicateLabel("REFERENCES")).toBe("引用");
  });

  it("lays out incoming and outgoing code relations as a directed branch", () => {
    const focus = {
      id: "code://service#symbol=rank",
      type: "CodeSymbol",
      domain: "code" as const,
      label: "rank",
      meta: "src/service.py · L20–30",
      locator: "service.py#L20-L30",
      x: 0,
      y: 0,
      source: "graph" as const,
    };
    const caller = {
      ...focus,
      id: "code://api#symbol=search",
      label: "search",
    };
    const target = {
      ...focus,
      id: "code://score#symbol=normalize",
      label: "normalize",
    };
    const branch = buildCodeRelationBoard(
      [focus, caller, target],
      [
        {
          id: "edge://incoming",
          source: caller.id,
          predicate: "CALLS",
          target: focus.id,
          label: "调用",
          tone: "code",
        },
        {
          id: "edge://outgoing",
          source: focus.id,
          predicate: "REFERENCES",
          target: target.id,
          label: "引用",
          tone: "code",
        },
      ],
      focus.id,
    );

    expect(branch.nodes.find((node) => node.id === caller.id)?.x).toBeLessThan(
      0,
    );
    expect(branch.nodes.find((node) => node.id === focus.id)?.x).toBe(0);
    expect(
      branch.nodes.find((node) => node.id === target.id)?.x,
    ).toBeGreaterThan(0);
    expect(
      branch.edges.find((edge) => edge.id === "edge://incoming")?.label,
    ).toBe("被调用");
  });

  it("builds a deterministic force-layout model without conflating vector and static edges", () => {
    const nodes = [
      {
        id: "query://one",
        type: "SemanticQuery",
        domain: "query" as const,
        label: "ranking",
        meta: "vector query",
        locator: "query://one",
        x: 0,
        y: 0,
        source: "graph" as const,
      },
      {
        id: "code://rank",
        type: "CodeFunction",
        domain: "code" as const,
        label: "rank",
        meta: "ranking.py",
        locator: "ranking.py#rank",
        repositoryId: "repo://one",
        path: "src/ranking.py",
        x: 0,
        y: 0,
        source: "graph" as const,
      },
      {
        id: "code://search",
        type: "CodeMethod",
        domain: "code" as const,
        label: "search",
        meta: "service.py",
        locator: "service.py#search",
        repositoryId: "repo://one",
        path: "src/service.py",
        x: 0,
        y: 0,
        source: "graph" as const,
      },
    ];
    const edges = [
      {
        id: "semantic://one",
        source: "query://one",
        predicate: "SEMANTIC_MATCH",
        target: "code://rank",
        confidence: 0.9,
        label: "语义匹配",
        tone: "query" as const,
      },
      {
        id: "call://one",
        source: "code://search",
        predicate: "CALLS",
        target: "code://rank",
        confidence: 1,
        label: "调用",
        tone: "code" as const,
      },
    ];
    const first = buildVectorLayout(nodes, edges, "query://one");
    const second = buildVectorLayout(nodes, edges, "query://one");

    expect(first.points.get("query://one")).toMatchObject({ x: 0, y: 0 });
    expect([...first.points.values()]).toEqual([...second.points.values()]);
    expect(
      first.links.find((link) => link.predicate === "CALLS")?.strength,
    ).toBeGreaterThan(
      first.links.find((link) => link.predicate === "SEMANTIC_MATCH")
        ?.strength || 0,
    );

    for (let tick = 0; tick < 20; tick += 1) {
      simulateVectorLayoutStep(first.points, first.links, 0.7);
    }
    const bounds = vectorLayoutBounds(first.points);
    expect(Number.isFinite(bounds.minX)).toBe(true);
    expect(bounds.maxX).toBeGreaterThan(bounds.minX);
    expect(bounds.maxY).toBeGreaterThan(bounds.minY);
  });

  it("keeps pairwise semantic links in the vector board", () => {
    const baseNode = {
      type: "CodeFunction",
      domain: "code" as const,
      meta: "src/ranking.py",
      locator: "src/ranking.py",
      x: 0,
      y: 0,
      source: "graph" as const,
    };
    const nodes = [
      { ...baseNode, id: "code://rank", label: "rank" },
      { ...baseNode, id: "code://search", label: "search" },
      { ...baseNode, id: "code://outside", label: "outside" },
    ];
    const graph = buildCodeVectorBoard(
      nodes,
      [
        {
          id: "semantic://pair",
          source: "code://rank",
          predicate: "SEMANTIC_SIMILAR",
          target: "code://search",
          confidence: 0.72,
          label: "向量相似",
          tone: "code",
        },
        {
          id: "semantic://outside",
          source: "code://rank",
          predicate: "SEMANTIC_SIMILAR",
          target: "code://outside",
          confidence: 0.61,
          label: "向量相似",
          tone: "code",
        },
      ],
      ["code://rank", "code://search"],
    );

    expect(graph.nodes.map((node) => node.id)).toEqual([
      "code://rank",
      "code://search",
    ]);
    expect(graph.edges.map((edge) => edge.id)).toEqual(["semantic://pair"]);
  });

  it("groups duplicate evidence links without drawing duplicate paths", () => {
    const binding = {
      id: "binding://one",
      project_id: "project-rag",
      source_title: "实现研究任务",
      source_thread_id: "thread-one",
      source_item_type: "FileChange",
      changed_path: "src/app.ts",
      repository_name: "rag",
      target_entity_id: "code://rag/src/app.ts",
      target_path: "src/app.ts",
      target_commit_sha: "abcdef123456",
      derivation: "path_match",
      confidence: 0.96,
      review_status: "unreviewed",
      created_at: "2026-07-24T00:00:00Z",
    } as const;
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [],
      graphEdges: [],
      bindings: [binding, { ...binding, id: "binding://two" }],
    });
    const grouped = result.edges.find(
      (edge) => edge.predicate === "changed_path_maps_to",
    );
    expect(grouped?.occurrences).toBe(2);
    expect(
      result.edges.filter((edge) => edge.predicate === "changed_path_maps_to"),
    ).toHaveLength(1);
  });

  it("lays out real source nodes as a bounded relationship network", () => {
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: "code://rag/src/app.ts#symbol=run",
          type: "CodeSymbol",
          label: "run",
          locator: "code://rag@abcdef123456/src/app.ts#symbol=run",
          path: "src/app.ts",
          domain: "code",
        },
      ],
      graphEdges: [],
      bindings: [],
    });
    const overview = buildResearchOverview(result.nodes, result.edges);
    const project = overview.nodes.find(
      (node) => node.type === "ResearchProject",
    );
    const codeNode = overview.nodes.find(
      (node) => node.type === "CodeSymbol" && node.label === "run",
    );
    expect(overview.nodes.some((node) => node.type.endsWith("Frame"))).toBe(
      false,
    );
    expect(project).toMatchObject({ x: 0, y: 0 });
    expect(codeNode?.groupKey).toBeNull();
    expect(overview.nodes.length).toBeGreaterThan(2);
    expect(
      overview.edges
        .filter((edge) => edge.scope)
        .every((edge) => edge.source === "workspace://project/project-rag"),
    ).toBe(true);

    const codeBoard = buildCodeBoard(result.nodes, result.edges);
    expect(
      codeBoard.nodes.some((node) => node.type === "CodeRepositoryBoard"),
    ).toBe(true);
    const directory = codeBoard.nodes.find(
      (node) => node.type === "CodeDirectoryGroup",
    );
    expect(directory?.label).toBe("src");
    const file = directory?.children?.find(
      (child) => child.type === "CodeFileGroup",
    );
    expect(file?.label).toBe("app.ts");
    expect(file?.meta).toBe("src/app.ts");
    const codeIds = new Set(codeBoard.nodes.map((node) => node.id));
    expect(
      codeBoard.edges.every(
        (edge) => codeIds.has(edge.source) && codeIds.has(edge.target),
      ),
    ).toBe(true);
  });

  it("preserves repository, file, class, method and precise Codex change hierarchy", () => {
    const repositoryId = "repo://rag";
    const fileId = "code://rag@abcdef123456/src/service.ts";
    const classId = `${fileId}#symbol=Service`;
    const methodId = `${fileId}#symbol=Service.run`;
    const functionId = `${fileId}#symbol=createService`;
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: repositoryId,
          type: "Repository",
          label: "rag",
          locator: "/tmp/rag",
          domain: "code",
          version: "abcdef123456",
          repository_id: repositoryId,
          metadata: { branch: "main" },
        },
        {
          id: "git://rag/commit/abcdef123456",
          type: "Commit",
          label: "abcdef123456",
          locator: "git://rag/commit/abcdef123456",
          domain: "code",
          version: "abcdef123456",
          repository_id: repositoryId,
        },
        {
          id: fileId,
          type: "FileVersion",
          label: "service.ts",
          locator: `${fileId}:1-80`,
          domain: "code",
          version: "abcdef123456",
          repository_id: repositoryId,
          path: "src/service.ts",
          language: "typescript",
          start_line: 1,
          end_line: 80,
        },
        {
          id: classId,
          type: "CodeSymbol",
          label: "Service",
          locator: `${fileId}:4-44`,
          domain: "code",
          version: "abcdef123456",
          repository_id: repositoryId,
          path: "src/service.ts",
          language: "typescript",
          qualified_name: "Service",
          start_line: 4,
          end_line: 44,
          metadata: { kind: "class" },
        },
        {
          id: methodId,
          type: "CodeSymbol",
          label: "run",
          locator: `${fileId}:8-18`,
          domain: "code",
          version: "abcdef123456",
          repository_id: repositoryId,
          path: "src/service.ts",
          language: "typescript",
          qualified_name: "Service.run",
          start_line: 8,
          end_line: 18,
          metadata: { kind: "method" },
        },
        {
          id: functionId,
          type: "CodeSymbol",
          label: "createService",
          locator: `${fileId}:52-58`,
          domain: "code",
          version: "abcdef123456",
          repository_id: repositoryId,
          path: "src/service.ts",
          language: "typescript",
          qualified_name: "createService",
          start_line: 52,
          end_line: 58,
          metadata: { kind: "function" },
        },
      ],
      graphEdges: [
        {
          id: "edge://file-class",
          source: fileId,
          predicate: "DEFINES",
          target: classId,
          domain: "code",
        },
        {
          id: "edge://file-method",
          source: fileId,
          predicate: "DEFINES",
          target: methodId,
          domain: "code",
        },
        {
          id: "edge://file-function",
          source: fileId,
          predicate: "DEFINES",
          target: functionId,
          domain: "code",
        },
      ],
      bindings: [
        {
          id: "binding://precise",
          project_id: "project-rag",
          source_entity_id: "codex://item/change-one",
          source_title: "实现 Service",
          source_thread_id: "thread-service",
          source_turn_id: "codex://turn/3",
          source_item_type: "FileChange",
          changed_path: "src/service.ts",
          repository_id: repositoryId,
          repository_name: "rag",
          target_entity_id: fileId,
          target_symbol_ids: [methodId],
          target_path: "src/service.ts",
          target_commit_sha: "abcdef123456",
          derivation: "path_and_symbol_match",
          confidence: 0.98,
          review_status: "confirmed",
          created_at: "2026-07-24T00:00:00Z",
        },
      ],
    });

    expect(result.nodes.find((node) => node.id === classId)?.type).toBe(
      "CodeClass",
    );
    expect(result.nodes.find((node) => node.id === methodId)?.type).toBe(
      "CodeMethod",
    );
    expect(result.nodes.find((node) => node.id === functionId)?.type).toBe(
      "CodeFunction",
    );
    expect(
      result.nodes.some((node) => node.id === "codex://item/change-one"),
    ).toBe(true);
    expect(
      result.edges.some(
        (edge) =>
          edge.source === "codex://item/change-one" &&
          edge.predicate === "changed_symbol_maps_to" &&
          edge.target === methodId,
      ),
    ).toBe(true);

    const summary = buildCodeBoard(result.nodes, result.edges);
    expect(summary.nodes.some((node) => node.type === "Repository")).toBe(true);
    expect(
      summary.nodes
        .find((node) => node.type === "CodeDirectoryGroup")
        ?.children?.map((child) => child.type),
    ).toContain("CodeFileGroup");

    const focused = buildCodeBoard(
      result.nodes,
      result.edges,
      "src/service.ts",
    );
    expect(focused.nodes.some((node) => node.type === "CodeFileFocus")).toBe(
      true,
    );
    expect(focused.nodes.some((node) => node.type === "CodeClass")).toBe(true);
    expect(focused.nodes.some((node) => node.type === "CodeMethod")).toBe(true);
    expect(
      focused.edges.some(
        (edge) =>
          edge.source === classId &&
          edge.predicate === "belongs_to_class" &&
          edge.target === methodId,
      ),
    ).toBe(true);
    expect(
      focused.nodes.some((node) => node.id === "codex://item/change-one"),
    ).toBe(true);
  });

  it("groups a large service file by responsibility instead of producing a method wall", () => {
    const repositoryId = "repo://rag";
    const fileId = "code://rag/src/evidence_rag/workspace/service.py";
    const names = [
      "create_project",
      "update_project",
      "_require_project",
      "create_topic",
      "update_topic",
      "delete_topic",
      "create_iteration",
      "update_iteration",
      "create_work_item",
      "transition_work_item",
      "_audit",
    ];
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: repositoryId,
          type: "Repository",
          label: "rag",
          locator: "/tmp/rag",
          domain: "code",
          repository_id: repositoryId,
        },
        {
          id: fileId,
          type: "FileVersion",
          label: "service.py",
          locator: fileId,
          domain: "code",
          repository_id: repositoryId,
          path: "src/evidence_rag/workspace/service.py",
          language: "python",
          metadata: { imports: ["typing", ".store"] },
        },
        ...names.map((name, index) => ({
          id: `${fileId}#symbol=WorkspaceService.${name}`,
          type: "CodeSymbol",
          label: name,
          locator: `${fileId}#L${index * 10 + 1}`,
          domain: "code",
          repository_id: repositoryId,
          path: "src/evidence_rag/workspace/service.py",
          language: "python",
          qualified_name: `WorkspaceService.${name}`,
          start_line: index * 10 + 1,
          end_line: index * 10 + 8,
          metadata: {
            kind: "method",
            calls:
              name === "create_project" ? ["_require_project", "_audit"] : [],
          },
        })),
      ],
      graphEdges: [
        {
          id: "edge://audit",
          source: `${fileId}#symbol=WorkspaceService.create_project`,
          predicate: "CALLS",
          target: `${fileId}#symbol=WorkspaceService._audit`,
          domain: "code",
        },
      ],
      bindings: [],
    });
    const board = buildCodeBoard(
      result.nodes,
      result.edges,
      "src/evidence_rag/workspace/service.py",
      repositoryId,
    );
    const groups = board.nodes.filter(
      (node) => node.type === "CodeResponsibilityGroup",
    );

    expect(groups.length).toBeGreaterThanOrEqual(5);
    expect(board.nodes.some((node) => node.type === "CodeMethod")).toBe(false);
    expect(
      groups.find((node) => node.label === "科研项目")?.children?.length,
    ).toBe(3);
    expect(board.edges.some((edge) => edge.predicate === "CALLS")).toBe(true);
    const workItemGroup = groups.find((node) => node.label === "工作任务")!;
    const context = buildInspectorContextMap(
      workItemGroup,
      board.nodes,
      board.edges,
    );
    expect(context.focusLevel).toBe(4);
    expect(context.totalLevels).toBe(5);
    expect(context.children.every((item) => item.level === 5)).toBe(true);
    expect(context.trail.some((item) => item.node.type === "Commit")).toBe(
      false,
    );
    expect(
      describeCodeNode(result.nodes.find((node) => node.id === fileId)!),
    ).toContain("业务规则");
    expect(
      codeNodeBehaviorFacts(
        result.nodes.find((node) => node.label === "create_project")!,
      ),
    ).toContain("直接调用 _require_project、_audit");
  });

  it("groups a model-heavy file into expandable type capabilities instead of a class wall", () => {
    const repositoryId = "repo://rag";
    const fileId = "code://rag/src/evidence_rag/models.py";
    const classNames = [
      "RepositoryIngestRequest",
      "EvidenceSearchRequest",
      "SearchScope",
      "CodexIngestRequest",
      "CodexSearchRequest",
      "CodexSearchScope",
      "ParsedFile",
      "ParsedSymbol",
      "QueryRequest",
      "QueryResult",
      "EvaluationMetric",
      "ExecutionStatus",
    ];
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: fileId,
          type: "FileVersion",
          label: "models.py",
          locator: fileId,
          domain: "code",
          repository_id: repositoryId,
          path: "src/evidence_rag/models.py",
          language: "python",
        },
        ...classNames.map((name, index) => ({
          id: `${fileId}#symbol=${name}`,
          type: "CodeSymbol",
          label: name,
          locator: `${fileId}#L${index * 8 + 1}`,
          domain: "code",
          repository_id: repositoryId,
          path: "src/evidence_rag/models.py",
          language: "python",
          qualified_name: name,
          start_line: index * 8 + 1,
          end_line: index * 8 + 7,
          metadata: { kind: "class" },
        })),
      ],
      graphEdges: [],
      bindings: [],
    });
    const board = buildCodeBoard(
      result.nodes,
      result.edges,
      "src/evidence_rag/models.py",
      repositoryId,
    );
    const groups = board.nodes.filter(
      (node) => node.type === "CodeResponsibilityGroup",
    );

    expect(board.nodes.some((node) => node.type === "CodeClass")).toBe(false);
    expect(groups.length).toBeGreaterThanOrEqual(3);
    expect(groups.some((node) => node.label === "输入与请求")).toBe(true);
    expect(groups.some((node) => node.label === "结果与响应")).toBe(true);
    expect(
      groups.reduce((total, node) => total + (node.children?.length || 0), 0),
    ).toBe(classNames.length);
    const ids = new Set(board.nodes.map((node) => node.id));
    expect(
      board.edges.every((edge) => ids.has(edge.source) && ids.has(edge.target)),
    ).toBe(true);
  });

  it("explains common technical helpers in user-facing language", () => {
    const makeNode = (label: string) => ({
      id: `code://rag/storage.py#symbol=${label}`,
      type: "CodeFunction",
      domain: "code" as const,
      label,
      meta: "src/evidence_rag/storage.py",
      locator: `code://rag/storage.py#symbol=${label}`,
      path: "src/evidence_rag/storage.py",
      x: 0,
      y: 0,
      source: "graph" as const,
    });

    expect(describeCodeNode(makeNode("transaction"))).toContain("异常时回滚");
    expect(describeCodeNode(makeNode("enqueue"))).toContain("执行队列");
    expect(describeCodeNode(makeNode("utc_now"))).toContain("UTC 时间戳");
  });

  it("keeps cross-file calls on file boundaries while expansion reveals internal structure", () => {
    const repositoryId = "repo://rag";
    const classId = "code://rag/src/service.ts#symbol=Service";
    const methodId = "code://rag/src/service.ts#symbol=Service.run";
    const helperId = "code://rag/src/helper.ts#symbol=loadConfig";
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: classId,
          type: "CodeSymbol",
          label: "Service",
          locator: classId,
          domain: "code",
          repository_id: repositoryId,
          path: "src/service.ts",
          qualified_name: "Service",
          start_line: 3,
          metadata: { kind: "class" },
        },
        {
          id: methodId,
          type: "CodeSymbol",
          label: "run",
          locator: methodId,
          domain: "code",
          repository_id: repositoryId,
          path: "src/service.ts",
          qualified_name: "Service.run",
          start_line: 8,
          metadata: { kind: "method" },
        },
        {
          id: helperId,
          type: "CodeSymbol",
          label: "loadConfig",
          locator: helperId,
          domain: "code",
          repository_id: repositoryId,
          path: "src/helper.ts",
          qualified_name: "loadConfig",
          start_line: 4,
          metadata: { kind: "function" },
        },
      ],
      graphEdges: [
        {
          id: "edge://cross-file-call",
          source: methodId,
          predicate: "CALLS",
          target: helperId,
          derivation: "static_analysis",
          confidence: 1,
          domain: "code",
        },
      ],
      bindings: [],
    });
    const serviceKey = `${repositoryId}::src/service.ts`;
    const helperKey = `${repositoryId}::src/helper.ts`;

    const collapsed = buildCodeCollaborationBoard(result.nodes, result.edges, {
      autoExpand: false,
    });
    expect(collapsed.stats.files).toBe(2);
    expect(collapsed.stats.crossFileRelations).toBe(1);
    expect(
      collapsed.nodes.filter((node) => node.type === "CodeFileContainer"),
    ).toHaveLength(2);
    expect(collapsed.nodes.some((node) => node.id === methodId)).toBe(false);
    expect(
      collapsed.edges.find((edge) => edge.predicate === "CALLS")?.label,
    ).toBe("跨文件调用");

    const serviceExpanded = buildCodeCollaborationBoard(
      result.nodes,
      result.edges,
      {
        autoExpand: false,
        expandedFileKeys: [serviceKey],
      },
    );
    const partialCall = serviceExpanded.edges.find(
      (edge) => edge.predicate === "CALLS",
    );
    expect(partialCall?.source).toContain("board://code/collaboration/");
    expect(partialCall?.target).toContain("board://code/collaboration/");

    const expanded = buildCodeCollaborationBoard(result.nodes, result.edges, {
      autoExpand: false,
      expandedFileKeys: [serviceKey, helperKey],
    });
    expect(
      expanded.nodes.find((node) => node.id === methodId)?.parentId,
    ).toBeTruthy();
    expect(
      expanded.edges.some(
        (edge) =>
          edge.source.includes("board://code/collaboration/") &&
          edge.predicate === "CALLS" &&
          edge.target.includes("board://code/collaboration/"),
      ),
    ).toBe(true);
    expect(
      expanded.edges.some(
        (edge) =>
          edge.source === classId &&
          edge.predicate === "belongs_to_class" &&
          edge.target === methodId,
      ),
    ).toBe(true);
    const method = expanded.nodes.find((node) => node.id === methodId);
    expect(method).toBeTruthy();
    const context = buildInspectorContextMap(
      method!,
      expanded.nodes,
      expanded.edges,
    );
    expect(context.trail.map((item) => item.node.type)).toEqual([
      "Repository",
      "CodeFileContainer",
      "CodeClass",
      "CodeMethod",
    ]);
    expect(context.focusLevel).toBe(4);
    expect(context.totalLevels).toBe(4);
    expect(context.relations).toHaveLength(0);
    const serviceContainer = expanded.nodes.find(
      (node) =>
        node.type === "CodeFileContainer" && node.path === "src/service.ts",
    );
    expect(serviceContainer).toBeTruthy();
    const fileContext = buildInspectorContextMap(
      serviceContainer!,
      expanded.nodes,
      expanded.edges,
      1,
    );
    expect(fileContext.childTotal).toBe(2);
    expect(fileContext.children).toHaveLength(1);
    expect(
      buildInspectorContextMap(
        serviceContainer!,
        expanded.nodes,
        expanded.edges,
      ).children.map((item) => item.level),
    ).toEqual([3, 4]);
  });

  it("keeps the collaboration canvas limited to files with actual relationships", () => {
    const repositoryId = "repo://rag";
    const serviceId = "code://rag/src/service.ts#symbol=run";
    const helperId = "code://rag/src/helper.ts#symbol=load";
    const orphanId = "code://rag/docs/notes.md";
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: repositoryId,
          type: "Repository",
          label: "rag",
          locator: "/tmp/rag",
          domain: "code",
          repository_id: repositoryId,
        },
        {
          id: serviceId,
          type: "CodeSymbol",
          label: "run",
          locator: serviceId,
          domain: "code",
          repository_id: repositoryId,
          path: "src/service.ts",
          metadata: { kind: "function" },
        },
        {
          id: helperId,
          type: "CodeSymbol",
          label: "load",
          locator: helperId,
          domain: "code",
          repository_id: repositoryId,
          path: "src/helper.ts",
          metadata: { kind: "function" },
        },
        {
          id: orphanId,
          type: "FileVersion",
          label: "notes.md",
          locator: orphanId,
          domain: "code",
          repository_id: repositoryId,
          path: "docs/notes.md",
        },
      ],
      graphEdges: [
        {
          id: "edge://call",
          source: serviceId,
          predicate: "CALLS",
          target: helperId,
          domain: "code",
        },
      ],
      bindings: [],
    });
    const board = buildCodeCollaborationBoard(result.nodes, result.edges, {
      autoExpand: false,
    });
    const files = board.nodes.filter(
      (node) => node.type === "CodeFileContainer",
    );

    expect(files).toHaveLength(2);
    expect(board.stats.files).toBe(3);
    expect(board.nodes.some((node) => node.type === "CodeDirectoryGroup")).toBe(
      false,
    );
    expect(board.nodes.some((node) => node.id === repositoryId)).toBe(false);
    expect(board.nodes.some((node) => node.id === orphanId)).toBe(false);
    expect(board.edges.some((edge) => edge.predicate === "CALLS")).toBe(true);
    expect(
      board.edges.every(
        (edge) =>
          board.nodes.some((node) => node.id === edge.source) &&
          board.nodes.some((node) => node.id === edge.target),
      ),
    ).toBe(true);
  });

  it("removes orphan edges and merges overlapping cross-board relations", () => {
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: "codex://thread/thread-one",
          type: "CodexThread",
          label: "实现研究任务",
          locator: "codex://thread/thread-one",
          domain: "codex",
        },
      ],
      graphEdges: [],
      bindings: [
        {
          id: "binding://one",
          project_id: "project-rag",
          source_title: "实现研究任务",
          source_thread_id: "thread-one",
          source_item_type: "FileChange",
          changed_path: "src/app.ts",
          repository_name: "rag",
          target_entity_id: "code://rag/src/app.ts",
          target_path: "src/app.ts",
          target_commit_sha: "abcdef123456",
          derivation: "path_match",
          confidence: 0.96,
          review_status: "unreviewed",
          created_at: "2026-07-24T00:00:00Z",
        },
      ],
    });
    const relation = result.edges.find(
      (edge) => edge.predicate === "changed_path_maps_to",
    )!;
    const overview = buildResearchOverview(result.nodes, [
      relation,
      { ...relation, id: "binding://confirmed", review_status: "confirmed" },
      { ...relation, id: "binding://orphan", target: "missing://node" },
    ]);
    const overviewIds = new Set(overview.nodes.map((node) => node.id));
    expect(
      overview.edges.every(
        (edge) => overviewIds.has(edge.source) && overviewIds.has(edge.target),
      ),
    ).toBe(true);
    expect(
      overview.edges.filter(
        (edge) => edge.predicate === "changed_path_maps_to",
      ),
    ).toHaveLength(1);
  });

  it("separates project scope from real topic-to-session-to-code evidence", () => {
    const threadId = "codex://thread/thread-one";
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: threadId,
          type: "CodexThread",
          label: "实现研究任务",
          locator: threadId,
          domain: "codex",
        },
      ],
      graphEdges: [],
      bindings: [
        {
          id: "binding://one",
          project_id: "project-rag",
          source_title: "实现研究任务",
          source_thread_id: threadId,
          source_item_type: "FileChange",
          changed_path: "src/app.ts",
          repository_name: "rag",
          target_entity_id: "code://rag/src/app.ts",
          target_path: "src/app.ts",
          target_commit_sha: "abcdef123456",
          derivation: "path_match",
          confidence: 0.96,
          review_status: "unreviewed",
          created_at: "2026-07-24T00:00:00Z",
        },
      ],
    });
    expect(
      result.nodes.some((node) => node.id.includes("codex://thread/codex://")),
    ).toBe(false);

    const topicBoard = buildTopicBoard(
      result.nodes,
      result.edges,
      "topic://one",
    );
    expect(topicBoard.topicId).toBe("topic://one");
    expect(
      topicBoard.nodes.some((node) => node.type === "TopicCodexScope"),
    ).toBe(true);
    expect(
      topicBoard.nodes.some((node) => node.type === "TopicCodeScope"),
    ).toBe(true);
    expect(
      topicBoard.edges.some(
        (edge) => edge.scope && edge.review_status === "scope",
      ),
    ).toBe(true);
    const binding = topicBoard.edges.find(
      (edge) => edge.predicate === "changed_path_maps_to",
    );
    expect(binding?.review_status).toBe("unreviewed");
    expect(binding?.occurrences).toBe(1);
  });

  it("builds a drillable serial Codex chain with real turns, items and mapped files", () => {
    const threadId = "codex://thread/thread-one";
    const turnId = `${threadId}/turn/turn-one`;
    const goalId = `${turnId}/item/goal-one`;
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: threadId,
          type: "CodexThread",
          label: "实现研究任务",
          locator: threadId,
          domain: "codex",
        },
        {
          id: turnId,
          type: "CodexTurn",
          label: "实现图谱白板",
          locator: turnId,
          domain: "codex",
          version: "2026-07-24T01:00:00Z",
        },
        {
          id: goalId,
          type: "UserGoal",
          label: "完善互联",
          locator: goalId,
          domain: "codex",
        },
        {
          id: "code://rag@abcdef123456/src/app.ts#symbol=run",
          type: "CodeSymbol",
          label: "run",
          locator: "code://rag@abcdef123456/src/app.ts#symbol=run",
          domain: "code",
        },
      ],
      graphEdges: [
        {
          id: "edge://turn",
          source: threadId,
          predicate: "HAS_TURN",
          target: turnId,
          derivation: "deterministic",
          confidence: 1,
          review_status: "confirmed",
          domain: "codex",
        },
        {
          id: "edge://goal",
          source: turnId,
          predicate: "HAS_ITEM",
          target: goalId,
          derivation: "deterministic",
          confidence: 1,
          review_status: "confirmed",
          domain: "codex",
        },
      ],
      bindings: [
        {
          id: "binding://one",
          project_id: "project-rag",
          source_title: "实现研究任务",
          source_thread_id: threadId,
          source_item_type: "FileChange",
          changed_path: "src/app.ts",
          repository_name: "rag",
          target_entity_id: "code://rag@abcdef123456/src/app.ts#symbol=run",
          target_path: "src/app.ts",
          target_commit_sha: "abcdef123456",
          derivation: "path_match",
          confidence: 0.96,
          review_status: "confirmed",
          created_at: "2026-07-24T00:00:00Z",
        },
      ],
    });

    const sessionBoard = buildCodexBoard(result.nodes, result.edges, threadId);
    expect(sessionBoard.threadId).toBe(threadId);
    expect(sessionBoard.nodes.some((node) => node.id === turnId)).toBe(true);
    expect(sessionBoard.nodes.some((node) => node.id === goalId)).toBe(true);
    expect(
      sessionBoard.nodes.some((node) => node.type === "CodeFileGroup"),
    ).toBe(true);
    expect(
      sessionBoard.edges.some((edge) => edge.predicate === "HAS_TURN"),
    ).toBe(true);
    expect(
      sessionBoard.edges.some(
        (edge) => edge.predicate === "changed_path_maps_to",
      ),
    ).toBe(true);
    const nodeIds = new Set(sessionBoard.nodes.map((node) => node.id));
    expect(
      sessionBoard.edges.every(
        (edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target),
      ),
    ).toBe(true);
  });

  it("shows governed FileChange paths as code impact before cross-source binding", () => {
    const threadId = "codex://thread/thread-change";
    const turnId = `${threadId}/turn/turn-change`;
    const changeId = `${turnId}/item/change-one`;
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: threadId,
          type: "CodexThread",
          label: "修改分页",
          locator: threadId,
          domain: "codex",
        },
        {
          id: turnId,
          type: "CodexTurn",
          label: "修复长列表",
          locator: turnId,
          domain: "codex",
        },
        {
          id: changeId,
          type: "FileChange",
          label: "frontend/src/App.tsx",
          path: "frontend/src/App.tsx",
          locator: changeId,
          domain: "codex",
        },
      ],
      graphEdges: [
        {
          id: "edge://turn-change",
          source: threadId,
          predicate: "HAS_TURN",
          target: turnId,
          domain: "codex",
        },
        {
          id: "edge://file-change",
          source: turnId,
          predicate: "HAS_ITEM",
          target: changeId,
          domain: "codex",
        },
      ],
      bindings: [],
    });

    const sessionBoard = buildCodexBoard(result.nodes, result.edges, threadId);
    const fileGroup = sessionBoard.nodes.find(
      (node) => node.type === "CodeFileGroup",
    );
    expect(fileGroup?.path).toBe("frontend/src/App.tsx");
    expect(fileGroup?.meta).toBe("frontend/src/App.tsx");
    expect(
      sessionBoard.edges.some(
        (edge) =>
          edge.source === changeId && edge.predicate === "changed_path_maps_to",
      ),
    ).toBe(true);
  });

  it("projects Patch paths into code groups without exposing database artifacts", () => {
    const threadId = "codex://thread/thread-patch";
    const turnId = `${threadId}/turn/turn-patch`;
    const patchId = `${turnId}/item/patch-one`;
    const databasePatchId = `${turnId}/item/patch-database`;
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: threadId,
          type: "CodexThread",
          label: "完善智能查询",
          locator: threadId,
          domain: "codex",
        },
        {
          id: turnId,
          type: "CodexTurn",
          label: "实现查询编排",
          locator: turnId,
          domain: "codex",
        },
        {
          id: patchId,
          type: "Patch",
          label: "src/evidence_rag/query/service.py",
          path: "src/evidence_rag/query/service.py",
          locator: patchId,
          domain: "codex",
        },
        {
          id: databasePatchId,
          type: "Patch",
          label: "var/evidence-rag.sqlite3",
          path: "var/evidence-rag.sqlite3",
          locator: databasePatchId,
          domain: "codex",
        },
      ],
      graphEdges: [
        {
          id: "edge://turn-patch",
          source: threadId,
          predicate: "HAS_TURN",
          target: turnId,
          domain: "codex",
        },
        {
          id: "edge://patch",
          source: turnId,
          predicate: "HAS_ITEM",
          target: patchId,
          domain: "codex",
        },
        {
          id: "edge://database-patch",
          source: turnId,
          predicate: "HAS_ITEM",
          target: databasePatchId,
          domain: "codex",
        },
      ],
      bindings: [],
    });

    const sessionBoard = buildCodexBoard(result.nodes, result.edges, threadId);
    const codePaths = sessionBoard.nodes
      .filter((node) => node.type === "CodeFileGroup")
      .map((node) => node.path);
    expect(codePaths).toContain("src/evidence_rag/query/service.py");
    expect(codePaths).not.toContain("var/evidence-rag.sqlite3");
    expect(
      sessionBoard.edges.some(
        (edge) =>
          edge.source === patchId && edge.predicate === "changed_path_maps_to",
      ),
    ).toBe(true);
  });

  it("keeps the topic focus visible above its research branch", () => {
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [],
      graphEdges: [],
      bindings: [],
    });
    const board = buildTopicBoard(result.nodes, result.edges, "topic://one");
    const topic = board.nodes.find((node) => node.id === "topic://one")!;
    const iteration = board.nodes.find(
      (node) => node.type === "ResearchIteration",
    )!;
    const work = board.nodes.find((node) => node.type.endsWith("Work"))!;

    expect(topic.y).toBeLessThan(iteration.y);
    expect(iteration.y).toBeLessThan(work.y);
    expect(
      board.edges.every(
        (edge) =>
          board.nodes.some((node) => node.id === edge.source) &&
          board.nodes.some((node) => node.id === edge.target),
      ),
    ).toBe(true);
  });

  it("turns Codex rollout paths into a readable session identity", () => {
    const rollout =
      "/Users/researcher/.codex/sessions/2026/07/24/rollout-2026-07-24T01-00-00-thread1234.jsonl";
    const result = buildResearchGraph({
      dashboard,
      graphNodes: [
        {
          id: "codex://thread/thread1234",
          type: "CodexThread",
          label: rollout,
          locator: rollout,
          domain: "codex",
        },
      ],
      graphEdges: [],
      bindings: [],
    });
    const session = result.nodes.find((node) => node.type === "CodexThread")!;

    expect(session.label).toContain("Codex 会话");
    expect(session.label).not.toContain(".jsonl");
    const selectionBoard = buildCodexBoard(result.nodes, result.edges);
    expect(selectionBoard.threadId).toBeNull();
    expect(selectionBoard.nodes).toHaveLength(1);
    expect(selectionBoard.nodes[0].label).toBe("选择会话查看代码影响");
  });

  it("renders real session nodes inside impact frames before a thread is focused", () => {
    const sessions = [
      {
        id: "session-active",
        thread_id: "thread-active",
        project_id: "project-rag",
        title: "实现图谱总览",
        cwd: "/workspace/rag",
        status: "active",
        started_at: "2026-08-02T01:00:00Z",
        updated_at: "2026-08-02T02:00:00Z",
        turn_count: 7,
        item_count: 18,
        file_change_count: 0,
        command_count: 3,
        metadata: {},
      },
      {
        id: "session-changed",
        thread_id: "thread-changed",
        project_id: "project-rag",
        title: "修复代码白板",
        cwd: "/workspace/rag",
        status: "completed",
        started_at: "2026-08-01T01:00:00Z",
        updated_at: "2026-08-01T02:00:00Z",
        turn_count: 4,
        item_count: 12,
        file_change_count: 5,
        command_count: 2,
        metadata: {},
      },
    ];
    const board = buildCodexOverviewBoard(sessions, 110);
    const frames = board.nodes.filter(
      (node) => node.type === "SessionImpactFrame",
    );
    const threads = board.nodes.filter((node) => node.type === "CodexThread");

    expect(board.stats.totalSessions).toBe(110);
    expect(board.stats.visibleSessions).toBe(2);
    expect(frames.map((frame) => frame.label)).toEqual([
      "正在进行",
      "产生代码变更",
    ]);
    expect(threads).toHaveLength(2);
    expect(
      threads.every((thread) =>
        frames.some((frame) => frame.id === thread.groupKey),
      ),
    ).toBe(true);
  });

  it("shows a bounded sparse file set even when no relation has been extracted yet", () => {
    const nodes = Array.from({ length: 3 }, (_, index) => ({
      id: `code://rag/src/file-${index}.py`,
      type: "FileVersion",
      domain: "code" as const,
      label: `file-${index}.py`,
      meta: `src/file-${index}.py`,
      locator: `code://rag/src/file-${index}.py`,
      repositoryId: "repo://rag",
      path: `src/file-${index}.py`,
      x: 0,
      y: 0,
      source: "graph" as const,
    }));
    const board = buildCodeCollaborationBoard(nodes, [], { autoExpand: false });

    expect(board.stats.files).toBe(3);
    expect(board.stats.visibleFiles).toBe(3);
    expect(
      board.nodes.filter((node) => node.type === "CodeFileContainer"),
    ).toHaveLength(3);
    expect(board.edges).toHaveLength(0);
    expect(new Set(board.nodes.map((node) => `${node.x}:${node.y}`)).size).toBe(
      board.nodes.length,
    );
  });

  it("summarizes a dense multi-file network as linked modules without orphan edges or card overlap", () => {
    const nodes = Array.from({ length: 48 }, (_, index) => {
      const path = `src/evidence_rag/module_${index % 24}/file_${index}.py`;
      return {
        id: `code://rag/${path}#symbol=run_${index}`,
        type: "CodeFunction",
        domain: "code" as const,
        label: `run_${index}`,
        meta: path,
        locator: `code://rag/${path}#symbol=run_${index}`,
        repositoryId: "repo://rag",
        path,
        startLine: index + 1,
        x: 0,
        y: 0,
        source: "graph" as const,
      };
    });
    const edges = nodes.flatMap((node, index) => {
      const next = nodes[(index + 1) % nodes.length];
      const across = nodes[(index + 7) % nodes.length];
      return [
        {
          id: `edge://import/${index}`,
          source: node.id,
          predicate: "IMPORTS",
          target: next.id,
          label: "导入",
          tone: "code" as const,
        },
        {
          id: `edge://call/${index}`,
          source: node.id,
          predicate: "CALLS",
          target: across.id,
          label: "调用",
          tone: "code" as const,
        },
      ];
    });
    const board = buildCodeCollaborationBoard(nodes, edges, {
      autoExpand: false,
      relationTypes: ["IMPORTS"],
    });
    const modules = board.nodes.filter(
      (node) => node.type === "CodeModuleFrame",
    );
    const files = board.nodes.filter((node) => node.type === "CodeFileGroup");
    const ids = new Set(board.nodes.map((node) => node.id));

    expect(board.stats.mode).toBe("modules");
    expect(board.stats.files).toBe(48);
    expect(board.stats.modules).toBe(24);
    expect(board.stats.visibleModules).toBe(14);
    expect(modules).toHaveLength(14);
    expect(files).toHaveLength(28);
    expect(
      files.every((file) =>
        modules.some((module) => module.id === file.groupKey),
      ),
    ).toBe(true);
    expect(
      board.edges.every((edge) => ids.has(edge.source) && ids.has(edge.target)),
    ).toBe(true);
    expect(board.edges.every((edge) => edge.predicate === "IMPORTS")).toBe(
      true,
    );
    modules.forEach((left, leftIndex) => {
      modules.slice(leftIndex + 1).forEach((right) => {
        const separatedX =
          Math.abs(left.x - right.x) >=
          ((left.width || 306) + (right.width || 306)) / 2;
        const separatedY =
          Math.abs(left.y - right.y) >=
          ((left.height || 174) + (right.height || 174)) / 2;
        expect(separatedX || separatedY).toBe(true);
      });
    });
    const bounds = graphBounds(modules);
    expect(bounds.width).toBeGreaterThan(700);
    expect(bounds.width).toBeLessThan(12_000);
    expect(bounds.height).toBeGreaterThan(400);
    expect(bounds.height).toBeLessThan(20_000);

    const expandedBoard = buildCodeCollaborationBoard(nodes, edges, {
      autoExpand: false,
      relationTypes: ["IMPORTS"],
      showAllModules: true,
    });
    expect(expandedBoard.stats.modules).toBe(24);
    expect(expandedBoard.stats.visibleModules).toBe(24);
    expect(
      expandedBoard.nodes.filter((node) => node.type === "CodeModuleFrame"),
    ).toHaveLength(24);
    expect(
      expandedBoard.nodes.filter((node) => node.type === "CodeFileGroup"),
    ).toHaveLength(48);
  });
});
