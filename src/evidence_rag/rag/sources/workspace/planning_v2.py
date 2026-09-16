"""Workspace dependency, blocker, acceptance, and done-gate policy."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from .contracts import (
    WorkspaceCheckStatus,
    WorkspaceEdge,
    WorkspaceEdgeType,
    WorkspaceEntity,
    WorkspaceEntityType,
    canonical_sha256,
)

WORKSPACE_PLANNING_POLICY_VERSION = "workspace-planning-policy-v2"


class _FrozenPlanning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkspaceDoneGate(_FrozenPlanning):
    work_item_id: str
    allowed: bool
    criterion_count: int
    passed_or_waived_count: int
    unresolved_dependency_ids: tuple[str, ...]
    unresolved_blocker_ids: tuple[str, ...]
    missing_required_output: bool
    reviewer_missing: bool
    reasons: tuple[str, ...]
    policy_sha256: str


class WorkspaceDependencyPath(_FrozenPlanning):
    start_id: str
    target_id: str
    path: tuple[str, ...]
    found: bool
    bounded: bool


def evaluate_workspace_done_gate(
    work_item: WorkspaceEntity,
    *,
    criteria: Iterable[WorkspaceEntity],
    checks: Iterable[WorkspaceEntity],
    dependencies: Iterable[WorkspaceEntity],
    blockers: Iterable[WorkspaceEntity],
    evidence_links: Iterable[WorkspaceEntity],
) -> WorkspaceDoneGate:
    if work_item.entity_type != WorkspaceEntityType.WORK_ITEM:
        raise ValueError("done gate requires a WorkItem")
    criterion_items = tuple(item for item in criteria if item.work_item_id == work_item.entity_id)
    check_map = {
        str(item.metadata.get("criterion_id")): item
        for item in checks
        if item.work_item_id == work_item.entity_id
    }
    passed = sum(
        check_map.get(item.entity_id) is not None
        and check_map[item.entity_id].status
        in {WorkspaceCheckStatus.PASS, WorkspaceCheckStatus.WAIVED}
        for item in criterion_items
    )
    unresolved_dependencies = tuple(
        sorted(
            item.entity_id
            for item in dependencies
            if item.work_item_id == work_item.entity_id
            and str(item.metadata.get("dependency_status")) != "satisfied"
        )
    )
    unresolved_blockers = tuple(
        sorted(
            item.entity_id
            for item in blockers
            if item.work_item_id == work_item.entity_id
            and item.status not in {"resolved", "dismissed"}
        )
    )
    accepted_outputs = tuple(
        item
        for item in evidence_links
        if item.work_item_id == work_item.entity_id
        and str(item.metadata.get("link_status")) == "accepted"
        and item.metadata.get("role") in {"output", "validation", "implementation"}
    )
    reviewer_missing = any(
        item.entity_id not in check_map
        or not str(check_map[item.entity_id].metadata.get("reviewed_by") or "")
        for item in criterion_items
    )
    reasons: list[str] = []
    if not criterion_items:
        reasons.append("acceptance-criteria-missing")
    elif passed != len(criterion_items):
        reasons.append("acceptance-check-incomplete")
    if unresolved_dependencies:
        reasons.append("dependency-unresolved")
    if unresolved_blockers:
        reasons.append("blocker-unresolved")
    if not accepted_outputs:
        reasons.append("required-output-missing")
    if reviewer_missing:
        reasons.append("reviewer-missing")
    policy_sha256 = canonical_sha256(
        {
            "policy_version": WORKSPACE_PLANNING_POLICY_VERSION,
            "work_item_id": work_item.entity_id,
            "criteria": [item.content_sha256 for item in criterion_items],
            "checks": [item.content_sha256 for item in check_map.values()],
            "dependencies": unresolved_dependencies,
            "blockers": unresolved_blockers,
            "outputs": [item.content_sha256 for item in accepted_outputs],
        }
    )
    return WorkspaceDoneGate(
        work_item_id=work_item.entity_id,
        allowed=not reasons,
        criterion_count=len(criterion_items),
        passed_or_waived_count=passed,
        unresolved_dependency_ids=unresolved_dependencies,
        unresolved_blocker_ids=unresolved_blockers,
        missing_required_output=not accepted_outputs,
        reviewer_missing=reviewer_missing,
        reasons=tuple(reasons),
        policy_sha256=policy_sha256,
    )


def dependency_path(
    edges: Iterable[WorkspaceEdge],
    *,
    start_id: str,
    target_id: str,
    max_hops: int = 8,
) -> WorkspaceDependencyPath:
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        if edge.current and edge.reviewed and edge.edge_type == WorkspaceEdgeType.DEPENDS_ON:
            adjacency[edge.source_id].append(edge.target_id)
    queue: deque[tuple[str, tuple[str, ...]]] = deque([(start_id, (start_id,))])
    seen = {start_id}
    bounded = False
    while queue:
        current, path = queue.popleft()
        if current == target_id:
            return WorkspaceDependencyPath(
                start_id=start_id,
                target_id=target_id,
                path=path,
                found=True,
                bounded=bounded,
            )
        if len(path) - 1 >= max_hops:
            bounded = True
            continue
        for neighbor in sorted(adjacency.get(current, ())):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, (*path, neighbor)))
    return WorkspaceDependencyPath(
        start_id=start_id,
        target_id=target_id,
        path=(),
        found=False,
        bounded=bounded,
    )


__all__ = [
    "WORKSPACE_PLANNING_POLICY_VERSION",
    "WorkspaceDependencyPath",
    "WorkspaceDoneGate",
    "dependency_path",
    "evaluate_workspace_done_gate",
]
