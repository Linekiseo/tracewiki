"""Deterministic multi-topic Workspace fixture for the released Golden."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict

from .contracts import (
    WORKSPACE_POLICY_VERSION,
    WorkspaceAuthority,
    WorkspaceEdge,
    WorkspaceEdgeType,
    WorkspaceEntity,
    WorkspaceEntityType,
    WorkspacePublication,
    WorkspaceRetrievalUnit,
    WorkspaceScope,
    build_workspace_edge,
    build_workspace_entity,
    build_workspace_publication,
    build_workspace_retrieval_unit,
    canonical_sha256,
    workspace_locator,
)
from .evidence_v2 import evaluate_workspace_coverage
from .intelligence_v2 import derive_workspace_intelligence
from .state_v2 import build_workspace_snapshot

WORKSPACE_GOLDEN_DATASET_ID = "workspace-golden-v1"
WORKSPACE_GOLDEN_DATASET_VERSION = "v1"
WORKSPACE_FIXTURE_RECIPE_VERSION = "workspace-programmatic-fixture-v1"
WORKSPACE_GOLDEN_PROJECT_ID = "project-workspace-golden-v1"
WORKSPACE_GOLDEN_ACL_REF = "workspace-public"
WORKSPACE_GOLDEN_GENERATION_ID = "workspacegen-golden-v1"
WORKSPACE_FIXTURE_OBSERVED_AT = "2026-07-29T08:00:00Z"


class WorkspaceFixtureBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str = WORKSPACE_GOLDEN_DATASET_ID
    dataset_version: str = WORKSPACE_GOLDEN_DATASET_VERSION
    observed_at: str = WORKSPACE_FIXTURE_OBSERVED_AT
    publication: WorkspacePublication
    recipe_sha256: str
    entity_counts: dict[str, int]


def build_workspace_fixture_v1() -> WorkspaceFixtureBundle:
    scope = WorkspaceScope(
        project_id=WORKSPACE_GOLDEN_PROJECT_ID,
        acl_ref=WORKSPACE_GOLDEN_ACL_REF,
        generation_id=WORKSPACE_GOLDEN_GENERATION_ID,
        timezone="Asia/Shanghai",
    )
    entities: list[WorkspaceEntity] = []
    edges: list[WorkspaceEdge] = []
    entity_by_id: dict[str, WorkspaceEntity] = {}

    def add(
        stable_id: str,
        entity_type: WorkspaceEntityType,
        *,
        display_key: str,
        label: str,
        text: str,
        status: str,
        authority: WorkspaceAuthority = WorkspaceAuthority.AUTHORITATIVE,
        parent_id: str | None = None,
        topic_id: str | None = None,
        iteration_id: str | None = None,
        work_item_id: str | None = None,
        owner: str | None = None,
        assignee: str | None = None,
        priority: int | None = None,
        due_at: str | None = None,
        effective_at: str = "2026-07-20T00:00:00Z",
        expires_at: str | None = None,
        current: bool = True,
        archived: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceEntity:
        source = {
            "stable_id": stable_id,
            "entity_type": entity_type,
            "label": label,
            "text": text,
            "status": status,
            "metadata": metadata or {},
        }
        item = build_workspace_entity(
            entity_id=stable_id,
            stable_id=stable_id,
            entity_type=entity_type,
            scope=scope,
            version=1,
            display_key=display_key,
            label=label,
            text=text,
            status=status,
            authority=authority,
            parent_id=parent_id,
            topic_id=topic_id,
            iteration_id=iteration_id,
            work_item_id=work_item_id,
            owner=owner,
            assignee=assignee,
            priority=priority,
            due_at=due_at,
            effective_at=effective_at,
            expires_at=expires_at,
            current=current,
            archived=archived,
            locator=workspace_locator(
                scope=scope,
                entity_type=entity_type,
                stable_id=stable_id,
                version=1,
            ),
            source_sha256=canonical_sha256(source),
            metadata=metadata or {},
        )
        entities.append(item)
        entity_by_id[item.entity_id] = item
        return item

    def edge(
        edge_type: WorkspaceEdgeType,
        source_id: str,
        target_id: str,
        *,
        reviewed: bool = True,
        current: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceEdge:
        edge_key = edge_type.value.replace("risk", "hazard").replace("_", ".")
        stable = f"edge-{edge_key}-{source_id}-{target_id}"
        payload = {
            "edge_id": stable,
            "edge_type": edge_type,
            "scope": scope,
            "source_id": source_id,
            "target_id": target_id,
            "reviewed": reviewed,
            "current": current,
            "effective_at": "2026-07-20T00:00:00Z",
            "metadata": metadata or {},
        }
        item = build_workspace_edge(**payload)
        edges.append(item)
        return item

    project = add(
        "project-rag-control",
        WorkspaceEntityType.PROJECT,
        display_key="PROJECT-RAG",
        label="RAG Research Program",
        text="Evidence-first retrieval research across six governed sources.",
        status="active",
        owner="RAG Core",
        metadata={"classification": "internal", "timezone": "Asia/Shanghai"},
    )
    active_topic = add(
        "topic-rag-active",
        WorkspaceEntityType.TOPIC,
        display_key="TOPIC-ACTIVE",
        label="RAG system design",
        text="Improve evidence precision without weakening source authority.",
        status="active",
        parent_id=project.entity_id,
        topic_id="topic-rag-active",
        owner="RAG Core",
        priority=1,
    )
    archived_topic = add(
        "topic-rag-archived",
        WorkspaceEntityType.TOPIC,
        display_key="TOPIC-ARCHIVE",
        label="RAG system design",
        text="Archived initial exploration with obsolete assumptions.",
        status="archived",
        parent_id=project.entity_id,
        topic_id="topic-rag-archived",
        owner="Archive Owner",
        priority=5,
        archived=True,
        current=False,
        effective_at="2026-06-01T00:00:00Z",
    )
    edge(WorkspaceEdgeType.HAS_TOPIC, project.entity_id, active_topic.entity_id)
    edge(
        WorkspaceEdgeType.HAS_TOPIC,
        project.entity_id,
        archived_topic.entity_id,
        current=False,
    )
    iterations = (
        add(
            "iteration-alpha",
            WorkspaceEntityType.ITERATION,
            display_key="ITER-ALPHA",
            label="Typed control-plane foundation",
            text="Build structured state, planning graph, and evidence coverage.",
            status="active",
            parent_id=active_topic.entity_id,
            topic_id=active_topic.entity_id,
            iteration_id="iteration-alpha",
            owner="Research Lead",
            priority=1,
            due_at="2026-07-31T16:00:00+08:00",
            metadata={"progress": 80, "initial_status": "planned"},
        ),
        add(
            "iteration-beta",
            WorkspaceEntityType.ITERATION,
            display_key="ITER-BETA",
            label="Release validation",
            text="Validate quality gates and rollback evidence.",
            status="validating",
            parent_id=active_topic.entity_id,
            topic_id=active_topic.entity_id,
            iteration_id="iteration-beta",
            owner="QA Lead",
            priority=2,
            due_at="2026-08-04T16:00:00+08:00",
            metadata={"progress": 45, "initial_status": "planned"},
        ),
        add(
            "iteration-old",
            WorkspaceEntityType.ITERATION,
            display_key="ITER-OLD",
            label="Legacy substring prototype",
            text="Historical prototype superseded by typed retrieval.",
            status="archived",
            parent_id=archived_topic.entity_id,
            topic_id=archived_topic.entity_id,
            iteration_id="iteration-old",
            owner="Archive Owner",
            priority=5,
            archived=True,
            current=False,
            effective_at="2026-06-02T00:00:00Z",
            metadata={"progress": 100, "initial_status": "planned"},
        ),
    )
    for iteration in iterations:
        edge(
            WorkspaceEdgeType.HAS_ITERATION,
            iteration.parent_id or active_topic.entity_id,
            iteration.entity_id,
            current=iteration.current,
        )

    work_specs = (
        ("Structured current views", "running", 1, "Codex", "2026-07-29T18:00:00+08:00"),
        ("Review retrieval contract", "review", 1, "Reviewer", "2026-07-29T20:00:00+08:00"),
        ("Resolve ACL blocker", "blocked", 1, "Security", "2026-07-29T09:00:00+08:00"),
        ("Publish initial baseline", "done", 2, "QA", "2026-07-28T18:00:00+08:00"),
        ("Archive substring prototype", "cancelled", 5, "Archive Owner", None),
        ("Implement temporal snapshots", "ready", 1, "Codex", "2026-07-30T12:00:00+08:00"),
        ("Add dependency graph", "running", 2, "Codex", "2026-07-30T16:00:00+08:00"),
        ("Resolve stale evidence", "blocked", 2, "Research Lead", "2026-07-29T10:00:00+08:00"),
        ("Review evidence coverage", "review", 2, "Reviewer", "2026-07-31T12:00:00+08:00"),
        ("Implement decision store", "ready", 2, "Codex", "2026-08-01T12:00:00+08:00"),
        ("Persist intelligence runs", "backlog", 3, "Codex", "2026-08-02T12:00:00+08:00"),
        ("Validate as-of replay", "running", 2, "QA", "2026-07-31T09:00:00+08:00"),
        ("Test timezone boundary", "done", 3, "QA", "2026-07-28T23:00:00+08:00"),
        ("Test duplicate topics", "done", 3, "QA", "2026-07-28T23:00:00+08:00"),
        ("Review source watermarks", "review", 3, "Reviewer", "2026-08-01T18:00:00+08:00"),
        ("Build semantic auxiliary rank", "backlog", 4, "Codex", "2026-08-03T12:00:00+08:00"),
        ("Create rollback rehearsal", "ready", 3, "QA", "2026-08-03T18:00:00+08:00"),
        ("Validate secret redaction", "done", 1, "Security", "2026-07-28T18:00:00+08:00"),
        (
            "Inspect expired recommendation",
            "blocked",
            2,
            "Research Lead",
            "2026-07-29T07:00:00+08:00",
        ),
        ("Draft release decision", "backlog", 4, "Research Lead", "2026-08-05T12:00:00+08:00"),
    )
    work_items: list[WorkspaceEntity] = []
    for index, (title, status, priority, assignee, due_at) in enumerate(work_specs, 1):
        iteration = iterations[0] if index <= 12 else iterations[1]
        work = add(
            f"work-{index:02d}",
            WorkspaceEntityType.WORK_ITEM,
            display_key=f"TASK-{index:04d}",
            label=title,
            text=f"Objective: {title}. Acceptance requires reviewed observable evidence.",
            status=status,
            parent_id=iteration.entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iteration.entity_id,
            work_item_id=f"work-{index:02d}",
            owner="Research Lead",
            assignee=assignee,
            priority=priority,
            due_at=due_at,
            metadata={
                "kind": "development" if index % 3 else "review",
                "initial_status": "backlog",
                "version": 1,
            },
        )
        work_items.append(work)
        edge(WorkspaceEdgeType.HAS_WORK_ITEM, iteration.entity_id, work.entity_id)

    criteria: list[WorkspaceEntity] = []
    checks: list[WorkspaceEntity] = []
    for index, work in enumerate(work_items, 1):
        criterion = add(
            f"criterion-{index:02d}",
            WorkspaceEntityType.ACCEPTANCE_CRITERION,
            display_key=f"AC-{index:04d}",
            label=f"Reviewed evidence for {work.display_key}",
            text="Required output is linked and independently reviewed.",
            status="required",
            parent_id=work.entity_id,
            topic_id=work.topic_id,
            iteration_id=work.iteration_id,
            work_item_id=work.entity_id,
            metadata={"ordinal": 1},
        )
        check_status = "fail" if index in {4, 8} else "pass" if work.status == "done" else "unknown"
        check = add(
            f"check-{index:02d}",
            WorkspaceEntityType.ACCEPTANCE_CHECK,
            display_key=f"CHECK-{index:04d}",
            label=f"{check_status.upper()} {criterion.display_key}",
            text="Observed acceptance review result.",
            status=check_status,
            parent_id=criterion.entity_id,
            topic_id=work.topic_id,
            iteration_id=work.iteration_id,
            work_item_id=work.entity_id,
            metadata={
                "criterion_id": criterion.entity_id,
                "reviewed_by": "QA" if check_status != "unknown" else "",
                "evidence_ids": [f"evidence-{index:02d}"] if check_status == "pass" else [],
            },
        )
        criteria.append(criterion)
        checks.append(check)
        edge(WorkspaceEdgeType.HAS_ACCEPTANCE, work.entity_id, criterion.entity_id)
        edge(WorkspaceEdgeType.CHECKS, check.entity_id, criterion.entity_id)

    dependencies = (
        add(
            "dependency-01",
            WorkspaceEntityType.DEPENDENCY,
            display_key="DEP-0001",
            label="Structured views depend on ACL resolution",
            text="work-01 cannot complete until work-03 is resolved.",
            status="open",
            parent_id=work_items[0].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            work_item_id=work_items[0].entity_id,
            metadata={
                "dependency_id": work_items[2].entity_id,
                "dependency_status": "blocked",
            },
        ),
        add(
            "dependency-02",
            WorkspaceEntityType.DEPENDENCY,
            display_key="DEP-0002",
            label="Review depends on structured views",
            text="work-02 waits for work-01.",
            status="satisfied",
            parent_id=work_items[1].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            work_item_id=work_items[1].entity_id,
            metadata={
                "dependency_id": work_items[0].entity_id,
                "dependency_status": "satisfied",
            },
        ),
    )
    for item in dependencies:
        edge(
            WorkspaceEdgeType.DEPENDS_ON,
            item.work_item_id or "",
            item.entity_id,
        )
        edge(
            WorkspaceEdgeType.DEPENDS_ON,
            item.entity_id,
            str(item.metadata["dependency_id"]),
        )
    blockers = (
        add(
            "blocker-acl",
            WorkspaceEntityType.BLOCKER,
            display_key="BLOCK-ACL",
            label="ACL generation is ambiguous",
            text="Two visible generations prevent a unique governed scope.",
            status="open",
            parent_id=work_items[2].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            work_item_id=work_items[2].entity_id,
            owner="Security",
            priority=1,
            metadata={"severity": "high", "mitigation": "pin one governed generation"},
        ),
        add(
            "blocker-stale",
            WorkspaceEntityType.BLOCKER,
            display_key="BLOCK-STALE",
            label="Experiment evidence is stale",
            text="Pinned run was superseded by a newer generation.",
            status="open",
            parent_id=work_items[7].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            work_item_id=work_items[7].entity_id,
            owner="Research Lead",
            priority=2,
            metadata={"severity": "medium", "mitigation": "review fresh run"},
        ),
        add(
            "blocker-resolved",
            WorkspaceEntityType.BLOCKER,
            display_key="BLOCK-RESOLVED",
            label="Fixture parity mismatch",
            text="Resolved after canonical rebuild.",
            status="resolved",
            parent_id=work_items[6].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            work_item_id=work_items[6].entity_id,
            metadata={"severity": "low"},
        ),
    )
    for item in blockers:
        edge(WorkspaceEdgeType.BLOCKED_BY, item.work_item_id or "", item.entity_id)
    risks = (
        add(
            "hazard-default-switch",
            WorkspaceEntityType.RISK,
            display_key="RISK-0001",
            label="Premature default V2 switch",
            text="Quality evidence remains non-qualified.",
            status="open",
            parent_id=iterations[1].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[1].entity_id,
            owner="Release Owner",
            priority=1,
            metadata={"severity": "critical", "mitigation": "hold default V1"},
        ),
        add(
            "hazard-stale-intelligence",
            WorkspaceEntityType.RISK,
            display_key="RISK-0002",
            label="Expired intelligence may be mistaken for current advice",
            text="Derived recommendation must carry expiry and input lineage.",
            status="mitigating",
            parent_id=iterations[0].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            owner="Research Lead",
            priority=2,
        ),
    )
    for item in risks:
        edge(WorkspaceEdgeType.HAS_RISK, item.parent_id or "", item.entity_id)

    requirement_specs = (
        (
            "requirement-implementation",
            "Implementation and validation evidence",
            ("implementation", "validation"),
            ("code", "experiment"),
            2,
            2,
        ),
        (
            "requirement-publication",
            "Publication evidence",
            ("publication",),
            ("document",),
            1,
            1,
        ),
        (
            "requirement-reproduction",
            "Independent reproduction",
            ("reproduction",),
            ("experiment", "notebook"),
            1,
            1,
        ),
        (
            "requirement-rationale",
            "Decision rationale",
            ("rationale",),
            ("codex", "document"),
            1,
            1,
        ),
        (
            "requirement-security",
            "Security validation",
            ("security",),
            ("code",),
            1,
            1,
        ),
        (
            "requirement-release",
            "Release qualification",
            ("quality", "rollback"),
            ("experiment", "workspace"),
            2,
            2,
        ),
    )
    requirements: list[WorkspaceEntity] = []
    for index, (stable, label, roles, sources, minimum, independence) in enumerate(
        requirement_specs, 1
    ):
        requirement = add(
            stable,
            WorkspaceEntityType.EVIDENCE_REQUIREMENT,
            display_key=f"REQ-{index:04d}",
            label=label,
            text=f"Required roles: {', '.join(roles)}.",
            status="active",
            parent_id=iterations[0].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            owner="Research Lead",
            metadata={
                "purpose": "validation",
                "required_roles": roles,
                "allowed_source_domains": sources,
                "min_count": minimum,
                "min_independence_groups": independence,
                "max_age_days": 30,
                "review_rule": "reviewed",
                "version_rule": "pinned-generation",
            },
        )
        requirements.append(requirement)
        edge(WorkspaceEdgeType.HAS_REQUIREMENT, iterations[0].entity_id, requirement.entity_id)

    link_specs = (
        (1, "code-commit-a", "code", "implementation", "code-main", "accepted", True, True),
        (1, "run-a", "experiment", "validation", "run-a", "accepted", True, True),
        (2, "doc-paper-a", "document", "publication", "paper-a", "accepted", True, True),
        (3, "run-old", "experiment", "reproduction", "run-old", "stale", True, False),
        (4, "codex-episode-a", "codex", "rationale", "episode-a", "accepted", True, True),
        (5, "code-security-a", "code", "security", "code-main", "accepted", True, True),
        (6, "run-quality-a", "experiment", "quality", "run-a", "accepted", True, True),
        (6, "workspace-rollback-a", "workspace", "rollback", "workspace", "accepted", True, True),
        (1, "run-copy-a", "experiment", "validation", "run-a", "accepted", True, True),
        (2, "doc-superseded", "document", "publication", "paper-old", "superseded", True, False),
        (4, "codex-unreviewed", "codex", "rationale", "episode-b", "proposed", False, True),
        (5, "code-private", "code", "security", "private-code", "accepted", True, True),
    )
    evidence_links: list[WorkspaceEntity] = []
    for index, (
        requirement_index,
        external_id,
        source_domain,
        role,
        group,
        link_status,
        reviewed,
        fresh,
    ) in enumerate(link_specs, 1):
        requirement = requirements[requirement_index - 1]
        external_acl = "workspace-private" if external_id == "code-private" else scope.acl_ref
        link = add(
            f"evidence-link-{index:02d}",
            WorkspaceEntityType.EVIDENCE_LINK,
            display_key=f"LINK-{index:04d}",
            label=f"{role}: {external_id}",
            text="Pinned external evidence preview.",
            status=link_status,
            authority=WorkspaceAuthority.REVIEWED_RELATION,
            parent_id=requirement.entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            work_item_id=work_items[min(index - 1, len(work_items) - 1)].entity_id,
            owner="Reviewer",
            metadata={
                "requirement_id": requirement.entity_id,
                "external_entity_id": external_id,
                "external_version": "v1",
                "external_generation": "gen-2" if not fresh else "gen-1",
                "pinned_generation": "gen-1",
                "external_acl_ref": external_acl,
                "source_domain": source_domain,
                "role": role,
                "link_status": link_status,
                "reviewed": reviewed,
                "fresh": fresh,
                "independence_group": group,
                "observed_at": "2026-07-28T08:00:00Z",
                "evidence_snapshot_sha256": canonical_sha256(
                    {"external_id": external_id, "generation": "gen-1"}
                ),
            },
        )
        evidence_links.append(link)
        edge(
            WorkspaceEdgeType.SATISFIED_BY,
            requirement.entity_id,
            link.entity_id,
            reviewed=reviewed,
            current=link_status == "accepted",
        )

    decisions = (
        add(
            "decision-default-engine",
            WorkspaceEntityType.DECISION,
            display_key="DEC-0001",
            label="Keep V1 as default",
            text="Select V1 until source quality gates pass.",
            status="approved",
            authority=WorkspaceAuthority.AUTHORITATIVE,
            parent_id=iterations[1].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[1].entity_id,
            owner="Release Owner",
            metadata={
                "question": "Which engine is the safe default?",
                "alternatives": ["v1", "v2"],
                "selected": "v1",
                "rationale": "Current source controls are non-qualified.",
                "decided_by": "Release Owner",
                "decided_at": "2026-07-28T12:00:00Z",
                "reversible": True,
            },
        ),
        add(
            "decision-old-scoring",
            WorkspaceEntityType.DECISION,
            display_key="DEC-0002",
            label="Use raw cross-source scores",
            text="Obsolete decision superseded by calibrated fusion.",
            status="superseded",
            parent_id=iterations[2].entity_id,
            topic_id=archived_topic.entity_id,
            iteration_id=iterations[2].entity_id,
            owner="Archive Owner",
            current=False,
            archived=True,
            effective_at="2026-06-05T00:00:00Z",
            metadata={
                "question": "How should sources be fused?",
                "alternatives": ["raw-score", "source-local-calibration"],
                "selected": "raw-score",
                "rationale": "Prototype shortcut.",
                "decided_by": "Archive Owner",
                "decided_at": "2026-06-05T00:00:00Z",
                "reversible": True,
            },
        ),
        add(
            "decision-source-local",
            WorkspaceEntityType.DECISION,
            display_key="DEC-0003",
            label="Use source-local calibrated fusion",
            text="Avoid adding incomparable raw retrieval scores.",
            status="approved",
            parent_id=iterations[0].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            owner="Research Lead",
            metadata={
                "question": "How should sources be fused?",
                "alternatives": ["raw-score", "source-local-calibration"],
                "selected": "source-local-calibration",
                "rationale": "Scores are not comparable across source retrievers.",
                "decided_by": "Research Lead",
                "decided_at": "2026-07-24T00:00:00Z",
                "reversible": True,
            },
        ),
    )
    for item in decisions:
        edge(
            WorkspaceEdgeType.HAS_DECISION,
            item.parent_id or "",
            item.entity_id,
            current=item.current,
        )
    edge(
        WorkspaceEdgeType.SUPERSEDES,
        decisions[2].entity_id,
        decisions[1].entity_id,
    )
    outcomes = (
        add(
            "outcome-foundation",
            WorkspaceEntityType.OUTCOME,
            display_key="OUT-0001",
            label="Foundation engineering passed",
            text="Contracts, isolated persistence, and Golden are reproducible.",
            status="observed",
            authority=WorkspaceAuthority.OBSERVED_EXTERNAL,
            parent_id=iterations[0].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[0].entity_id,
            metadata={"source_domain": "workspace", "observed_at": WORKSPACE_FIXTURE_OBSERVED_AT},
        ),
        add(
            "outcome-quality-hold",
            WorkspaceEntityType.OUTCOME,
            display_key="OUT-0002",
            label="Quality remains on hold",
            text="Release stays on V1 pending qualified production evidence.",
            status="observed",
            authority=WorkspaceAuthority.OBSERVED_EXTERNAL,
            parent_id=iterations[1].entity_id,
            topic_id=active_topic.entity_id,
            iteration_id=iterations[1].entity_id,
            metadata={"source_domain": "release", "observed_at": WORKSPACE_FIXTURE_OBSERVED_AT},
        ),
    )
    for item in outcomes:
        edge(WorkspaceEdgeType.PRODUCES_OUTCOME, item.parent_id or "", item.entity_id)

    transitions: list[WorkspaceEntity] = []
    for index, work in enumerate(work_items[:12], 1):
        transition = add(
            f"transition-{index:02d}",
            WorkspaceEntityType.TRANSITION,
            display_key=f"TRANS-{index:04d}",
            label=f"backlog → {work.status}",
            text=f"Observed transition for {work.display_key}.",
            status="applied",
            parent_id=work.entity_id,
            topic_id=work.topic_id,
            iteration_id=work.iteration_id,
            work_item_id=work.entity_id,
            owner="RAG Core",
            effective_at=f"2026-07-{20 + (index % 7):02d}T08:00:00Z",
            metadata={
                "entity_id": work.entity_id,
                "entity_type": "work_item",
                "before": "backlog",
                "after": work.status,
                "expected_version": 1,
                "resulting_version": 2,
                "actor": "RAG Core",
                "event_at": f"2026-07-{20 + (index % 7):02d}T08:00:00Z",
                "effective_at": f"2026-07-{20 + (index % 7):02d}T08:00:00Z",
                "reason": "fixture-observed-transition",
                "causation_id": f"cause-{index:02d}",
                "correlation_id": "workspace-fixture",
            },
        )
        transitions.append(transition)
        edge(WorkspaceEdgeType.TRANSITION_OF, transition.entity_id, work.entity_id)

    snapshot = build_workspace_snapshot(
        scope=scope,
        entities=entities,
        as_of=WORKSPACE_FIXTURE_OBSERVED_AT,
        source_watermarks={
            "code": "codegen-1",
            "codex": "codexgen-1",
            "experiment": "experimentgen-1",
            "notebook": "notebookgen-1",
            "document": "documentgen-1",
            "workspace": scope.generation_id,
        },
    )
    entities.append(snapshot)
    entity_by_id[snapshot.entity_id] = snapshot
    for item in (*work_items, *requirements, *decisions):
        edge(WorkspaceEdgeType.SNAPSHOT_CONTAINS, snapshot.entity_id, item.entity_id)

    coverage = tuple(
        evaluate_workspace_coverage(
            requirement,
            evidence_links,
            observed_at=WORKSPACE_FIXTURE_OBSERVED_AT,
            allowed_acl_refs=(scope.acl_ref,),
            enforce_acl=True,
        )
        for requirement in requirements
    )
    intelligence, _ = derive_workspace_intelligence(
        scope=scope,
        snapshot=snapshot,
        entities=entities,
        coverage=coverage,
        generated_at=WORKSPACE_FIXTURE_OBSERVED_AT,
    )
    entities.append(intelligence)
    entity_by_id[intelligence.entity_id] = intelligence
    edge(WorkspaceEdgeType.DERIVED_FROM, intelligence.entity_id, snapshot.entity_id)
    expired_intelligence = add(
        "intelligence-expired",
        WorkspaceEntityType.INTELLIGENCE_RUN,
        display_key="INTEL-EXPIRED",
        label="Expired recommendation: switch default V2",
        text="Historical recommendation that is no longer current.",
        status="expired",
        authority=WorkspaceAuthority.DERIVED_INTELLIGENCE,
        parent_id=snapshot.entity_id,
        topic_id=active_topic.entity_id,
        iteration_id=iterations[1].entity_id,
        effective_at="2026-07-27T08:00:00Z",
        expires_at="2026-07-27T09:00:00Z",
        current=False,
        metadata={
            "policy_version": WORKSPACE_POLICY_VERSION,
            "input_snapshot_sha256": snapshot.content_sha256,
            "generated_at": "2026-07-27T08:00:00Z",
            "stage": "complete",
            "readiness_score": 100,
            "next_actions": ["switch_default_v2"],
            "reasons": ["obsolete-input"],
            "confidence": 0.5,
            "authoritative_state_mutated": False,
        },
    )
    edge(
        WorkspaceEdgeType.DERIVED_FROM,
        expired_intelligence.entity_id,
        snapshot.entity_id,
        current=False,
    )

    units: list[WorkspaceRetrievalUnit] = []
    for item in entities:
        structured = {
            "project_id": scope.project_id,
            "entity_type": item.entity_type,
            "display_key": item.display_key,
            "status": item.status,
            "owner": item.owner,
            "assignee": item.assignee,
            "priority": item.priority,
            "due_at": item.due_at,
            "topic_id": item.topic_id,
            "iteration_id": item.iteration_id,
            "work_item_id": item.work_item_id,
            "authority": item.authority,
            "current": item.current,
            "archived": item.archived,
            "effective_at": item.effective_at,
            "expires_at": item.expires_at,
        }
        unit_payload = {
            "unit_id": f"unit-{item.entity_id}",
            "entity_id": item.entity_id,
            "scope": scope,
            "unit_type": item.entity_type,
            "authority": item.authority,
            "exact_terms": tuple(
                value
                for value in (item.entity_id, item.stable_id, item.display_key, item.label)
                if value
            ),
            "sparse_text": " ".join(
                value
                for value in (
                    item.display_key,
                    item.label,
                    item.text,
                    item.status,
                    item.owner,
                    item.assignee,
                )
                if value
            ),
            "dense_text": f"{item.entity_type.value} {item.label} {item.text}",
            "structured": structured,
            "locator": item.locator,
        }
        units.append(build_workspace_retrieval_unit(**unit_payload))
    publication_payload = {
        "publication_id": "workspace-publication-golden-v1",
        "scope": scope,
        "entities": tuple(entities),
        "edges": tuple(edges),
        "retrieval_units": tuple(units),
        "published_at": WORKSPACE_FIXTURE_OBSERVED_AT,
    }
    publication = build_workspace_publication(**publication_payload)
    recipe_payload = {
        "recipe_version": WORKSPACE_FIXTURE_RECIPE_VERSION,
        "dataset_id": WORKSPACE_GOLDEN_DATASET_ID,
        "dataset_version": WORKSPACE_GOLDEN_DATASET_VERSION,
        "publication_sha256": publication.publication_sha256,
        "entity_counts": dict(sorted(Counter(item.entity_type.value for item in entities).items())),
        "edge_count": len(edges),
        "policy_version": WORKSPACE_POLICY_VERSION,
    }
    return WorkspaceFixtureBundle(
        publication=publication,
        recipe_sha256=canonical_sha256(recipe_payload),
        entity_counts=recipe_payload["entity_counts"],
    )


__all__ = [
    "WORKSPACE_FIXTURE_OBSERVED_AT",
    "WORKSPACE_FIXTURE_RECIPE_VERSION",
    "WORKSPACE_GOLDEN_ACL_REF",
    "WORKSPACE_GOLDEN_DATASET_ID",
    "WORKSPACE_GOLDEN_DATASET_VERSION",
    "WORKSPACE_GOLDEN_GENERATION_ID",
    "WORKSPACE_GOLDEN_PROJECT_ID",
    "WorkspaceFixtureBundle",
    "build_workspace_fixture_v1",
]
