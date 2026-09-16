"""Workspace requirement coverage with review, freshness, and independence policy."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from .contracts import WorkspaceEntity, WorkspaceEntityType, canonical_sha256

WORKSPACE_COVERAGE_POLICY_VERSION = "workspace-evidence-coverage-v2"


class _FrozenCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkspaceCoverageResult(_FrozenCoverage):
    requirement_id: str
    satisfied: bool
    accepted_link_ids: tuple[str, ...]
    missing_count: int
    missing_roles: tuple[str, ...]
    stale_link_ids: tuple[str, ...]
    rejected_link_ids: tuple[str, ...]
    unauthorized_link_ids: tuple[str, ...]
    independence_groups: tuple[str, ...]
    policy_sha256: str


def evaluate_workspace_coverage(
    requirement: WorkspaceEntity,
    links: Iterable[WorkspaceEntity],
    *,
    observed_at: str,
    allowed_acl_refs: tuple[str, ...],
    enforce_acl: bool,
) -> WorkspaceCoverageResult:
    if requirement.entity_type != WorkspaceEntityType.EVIDENCE_REQUIREMENT:
        raise ValueError("coverage requires an EvidenceRequirement")
    metadata = requirement.metadata
    required_roles = tuple(str(item) for item in metadata.get("required_roles") or ())
    allowed_sources = set(str(item) for item in metadata.get("allowed_source_domains") or ())
    min_count = int(metadata.get("min_count") or 1)
    min_independent = int(metadata.get("min_independence_groups") or 1)
    max_age_days = int(metadata.get("max_age_days") or 36500)
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    accepted: list[WorkspaceEntity] = []
    stale: list[str] = []
    rejected: list[str] = []
    unauthorized: list[str] = []
    for link in links:
        if (
            link.entity_type != WorkspaceEntityType.EVIDENCE_LINK
            or link.metadata.get("requirement_id") != requirement.entity_id
        ):
            continue
        if enforce_acl and link.metadata.get("external_acl_ref") not in set(allowed_acl_refs):
            unauthorized.append(link.entity_id)
            continue
        if link.metadata.get("link_status") != "accepted" or not link.metadata.get("reviewed"):
            rejected.append(link.entity_id)
            continue
        observed_link = datetime.fromisoformat(
            str(link.metadata["observed_at"]).replace("Z", "+00:00")
        )
        age_days = (observed - observed_link).total_seconds() / 86_400
        if (
            not link.metadata.get("fresh")
            or age_days > max_age_days
            or link.metadata.get("external_generation") != link.metadata.get("pinned_generation")
        ):
            stale.append(link.entity_id)
            continue
        if allowed_sources and str(link.metadata.get("source_domain")) not in allowed_sources:
            rejected.append(link.entity_id)
            continue
        accepted.append(link)
    roles = {str(item.metadata.get("role")) for item in accepted}
    groups = tuple(sorted({str(item.metadata.get("independence_group")) for item in accepted}))
    missing_roles = tuple(sorted(set(required_roles) - roles))
    missing_count = max(0, min_count - len(accepted))
    satisfied = not missing_roles and not missing_count and len(groups) >= min_independent
    policy_sha256 = canonical_sha256(
        {
            "policy_version": WORKSPACE_COVERAGE_POLICY_VERSION,
            "requirement": requirement.content_sha256,
            "accepted": [
                item.content_sha256 for item in sorted(accepted, key=lambda x: x.entity_id)
            ],
            "stale": sorted(stale),
            "rejected": sorted(rejected),
            "unauthorized": sorted(unauthorized),
            "observed_at": observed_at,
        }
    )
    return WorkspaceCoverageResult(
        requirement_id=requirement.entity_id,
        satisfied=satisfied,
        accepted_link_ids=tuple(sorted(item.entity_id for item in accepted)),
        missing_count=missing_count,
        missing_roles=missing_roles,
        stale_link_ids=tuple(sorted(stale)),
        rejected_link_ids=tuple(sorted(rejected)),
        unauthorized_link_ids=tuple(sorted(unauthorized)),
        independence_groups=groups,
        policy_sha256=policy_sha256,
    )


__all__ = [
    "WORKSPACE_COVERAGE_POLICY_VERSION",
    "WorkspaceCoverageResult",
    "evaluate_workspace_coverage",
]
