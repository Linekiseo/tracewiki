"""Persistent, explainable Workspace intelligence derived from frozen inputs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict

from .contracts import (
    WORKSPACE_POLICY_VERSION,
    WorkspaceAuthority,
    WorkspaceEntity,
    WorkspaceEntityType,
    WorkspaceScope,
    build_workspace_entity,
    canonical_sha256,
    workspace_locator,
)
from .evidence_v2 import WorkspaceCoverageResult

WORKSPACE_INTELLIGENCE_VERSION = "workspace-intelligence-v2"


class _FrozenIntelligence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkspaceIntelligenceSummary(_FrozenIntelligence):
    stage: str
    readiness_score: int
    blockers: tuple[str, ...]
    next_actions: tuple[str, ...]
    reasons: tuple[str, ...]
    confidence: float


def derive_workspace_intelligence(
    *,
    scope: WorkspaceScope,
    snapshot: WorkspaceEntity,
    entities: Iterable[WorkspaceEntity],
    coverage: Iterable[WorkspaceCoverageResult],
    generated_at: str,
    ttl_minutes: int = 60,
) -> tuple[WorkspaceEntity, WorkspaceIntelligenceSummary]:
    entity_items = tuple(entities)
    work = tuple(item for item in entity_items if item.entity_type == WorkspaceEntityType.WORK_ITEM)
    states = Counter(item.status for item in work)
    coverage_items = tuple(coverage)
    blockers = tuple(
        sorted(
            item.display_key
            for item in entity_items
            if item.entity_type == WorkspaceEntityType.BLOCKER
            and item.status not in {"resolved", "dismissed"}
        )
    )
    missing_requirements = tuple(
        item.requirement_id for item in coverage_items if not item.satisfied
    )
    if not any(item.entity_type == WorkspaceEntityType.TOPIC for item in entity_items):
        stage = "define"
    elif not any(
        item.entity_type == WorkspaceEntityType.ITERATION and item.current for item in entity_items
    ):
        stage = "plan"
    elif states.get("review") or states.get("done"):
        stage = "review"
    elif states.get("running") or states.get("blocked"):
        stage = "execute"
    else:
        stage = "validate"
    readiness = 20
    readiness += 20 * bool(work)
    readiness += 20 * bool(states.get("running") or states.get("review") or states.get("done"))
    readiness += 25 * int(bool(coverage_items) and all(item.satisfied for item in coverage_items))
    readiness += 15 * bool(
        any(
            item.entity_type == WorkspaceEntityType.DECISION and item.status == "approved"
            for item in entity_items
        )
    )
    readiness = min(100, int(readiness))
    actions: list[str] = []
    reasons: list[str] = []
    if blockers:
        actions.append("resolve_blockers")
        reasons.append("explicit-unresolved-blocker")
    if missing_requirements:
        actions.append("collect_missing_evidence")
        reasons.append("evidence-requirement-not-satisfied")
    if states.get("review"):
        actions.append("review_work_items")
        reasons.append("work-items-await-review")
    if not actions:
        actions.append("continue_current_iteration")
        reasons.append("no-hard-control-plane-blocker")
    summary = WorkspaceIntelligenceSummary(
        stage=stage,
        readiness_score=readiness,
        blockers=blockers,
        next_actions=tuple(actions),
        reasons=tuple(reasons),
        confidence=1.0,
    )
    input_sha256 = canonical_sha256(
        {
            "snapshot": snapshot.content_sha256,
            "entities": sorted(item.content_sha256 for item in entity_items),
            "coverage": sorted(item.policy_sha256 for item in coverage_items),
            "policy_version": WORKSPACE_POLICY_VERSION,
        }
    )
    generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    expires_at = (generated + timedelta(minutes=ttl_minutes)).isoformat().replace("+00:00", "Z")
    stable_id = f"intelligence-{input_sha256.removeprefix('sha256:')[:20]}"
    intelligence = build_workspace_entity(
        entity_id=stable_id,
        stable_id=stable_id,
        entity_type=WorkspaceEntityType.INTELLIGENCE_RUN,
        scope=scope,
        version=1,
        display_key=f"INTEL-{stable_id[-8:].upper()}",
        label=f"Workspace intelligence: {stage}",
        text="; ".join(actions),
        status="current",
        authority=WorkspaceAuthority.DERIVED_INTELLIGENCE,
        effective_at=generated_at,
        expires_at=expires_at,
        locator=workspace_locator(
            scope=scope,
            entity_type=WorkspaceEntityType.INTELLIGENCE_RUN,
            stable_id=stable_id,
            version=1,
        ),
        source_sha256=input_sha256,
        metadata={
            "policy_version": WORKSPACE_POLICY_VERSION,
            "intelligence_version": WORKSPACE_INTELLIGENCE_VERSION,
            "input_snapshot_sha256": snapshot.content_sha256,
            "input_entity_sha256": sorted(item.content_sha256 for item in entity_items),
            "input_coverage_sha256": sorted(item.policy_sha256 for item in coverage_items),
            "generated_at": generated_at,
            "stage": summary.stage,
            "readiness_score": summary.readiness_score,
            "blockers": summary.blockers,
            "next_actions": summary.next_actions,
            "reasons": summary.reasons,
            "confidence": summary.confidence,
            "authoritative_state_mutated": False,
        },
    )
    return intelligence, summary


def intelligence_is_current(entity: WorkspaceEntity, *, observed_at: str) -> bool:
    if entity.entity_type != WorkspaceEntityType.INTELLIGENCE_RUN or entity.expires_at is None:
        return False
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    expires = datetime.fromisoformat(entity.expires_at.replace("Z", "+00:00"))
    return entity.current and observed <= expires


__all__ = [
    "WORKSPACE_INTELLIGENCE_VERSION",
    "WorkspaceIntelligenceSummary",
    "derive_workspace_intelligence",
    "intelligence_is_current",
]
