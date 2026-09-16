from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from evidence_rag.rag.sources.workspace.contracts import (
    WorkspaceEntityType,
    build_workspace_entity,
    workspace_locator,
)
from evidence_rag.rag.sources.workspace.evidence_v2 import (
    evaluate_workspace_coverage,
)
from evidence_rag.rag.sources.workspace.fixture_v1 import (
    build_workspace_fixture_v1,
)
from evidence_rag.rag.sources.workspace.intelligence_v2 import (
    derive_workspace_intelligence,
    intelligence_is_current,
)
from evidence_rag.rag.sources.workspace.planning_v2 import (
    dependency_path,
    evaluate_workspace_done_gate,
)
from evidence_rag.rag.sources.workspace.state_v2 import (
    build_workspace_snapshot,
    is_overdue,
    project_workspace_as_of,
    transition_workspace_entity,
)


def test_workspace_transition_matrix_concurrency_and_done_gate() -> None:
    fixture = build_workspace_fixture_v1()
    work = next(item for item in fixture.publication.entities if item.entity_id == "work-02")
    with pytest.raises(ValueError, match="concurrency"):
        transition_workspace_entity(
            work,
            target_status="done",
            expected_version=2,
            actor="reviewer",
            event_at="2026-07-29T08:10:00Z",
            effective_at="2026-07-29T08:10:00Z",
            reason="approved",
            done_gate_passed=True,
        )
    with pytest.raises(ValueError, match="acceptance gate"):
        transition_workspace_entity(
            work,
            target_status="done",
            expected_version=1,
            actor="reviewer",
            event_at="2026-07-29T08:10:00Z",
            effective_at="2026-07-29T08:10:00Z",
            reason="approved",
        )
    result = transition_workspace_entity(
        work,
        target_status="done",
        expected_version=1,
        actor="reviewer",
        event_at="2026-07-29T08:10:00Z",
        effective_at="2026-07-29T08:10:00Z",
        reason="approved",
        done_gate_passed=True,
    )
    assert result.entity.status == "done"
    assert result.entity.version == 2
    assert result.transition.metadata["before"] == "review"
    assert result.transition.metadata["after"] == "done"


def test_workspace_as_of_projection_is_deterministic_and_diagnostic() -> None:
    fixture = build_workspace_fixture_v1()
    current = next(item for item in fixture.publication.entities if item.entity_id == "work-01")
    initial = build_workspace_entity(
        **current.model_dump(
            mode="python",
            exclude={"content_sha256", "status", "effective_at"},
        ),
        status="backlog",
        effective_at="2026-07-20T00:00:00Z",
    )
    transition = next(
        item for item in fixture.publication.entities if item.entity_id == "transition-01"
    )
    first = project_workspace_as_of(
        (initial,),
        (transition,),
        as_of="2026-07-22T00:00:00Z",
    )
    second = project_workspace_as_of(
        (initial,),
        (transition,),
        as_of="2026-07-22T00:00:00Z",
    )
    assert first == second
    assert first.exact is True
    assert first.status_by_entity[initial.entity_id] == "running"
    mismatch = build_workspace_entity(
        **transition.model_dump(
            mode="python",
            exclude={"content_sha256", "metadata"},
        ),
        metadata={**transition.metadata, "before": "ready"},
    )
    projected = project_workspace_as_of(
        (initial,),
        (mismatch,),
        as_of="2026-07-22T00:00:00Z",
    )
    assert projected.exact is False
    assert projected.diagnostics == ("transition-chain-mismatch:transition-01",)


def test_workspace_as_of_projection_requires_aware_iso_and_normalizes_cutoff() -> None:
    fixture = build_workspace_fixture_v1()
    current = next(item for item in fixture.publication.entities if item.entity_id == "work-01")
    initial = build_workspace_entity(
        **current.model_dump(
            mode="python",
            exclude={"content_sha256", "status", "effective_at"},
        ),
        status="backlog",
        effective_at="2026-07-20T08:00:00+08:00",
    )
    transition = next(
        item for item in fixture.publication.entities if item.entity_id == "transition-01"
    )
    projected = project_workspace_as_of(
        (initial,),
        (transition,),
        as_of="2026-07-22T08:00:00+08:00",
    )
    assert projected.as_of == "2026-07-22T00:00:00Z"
    assert projected.exact is True
    with pytest.raises(ValueError, match="^as_of_timezone_required$"):
        project_workspace_as_of(
            (initial,),
            (transition,),
            as_of="2026-07-22T00:00:00",
        )
    with pytest.raises(ValueError, match="^as_of_invalid$"):
        project_workspace_as_of(
            (initial,),
            (transition,),
            as_of=" 2026-07-22T00:00:00Z",
        )


def test_workspace_timezone_overdue_is_explicit() -> None:
    assert is_overdue(
        due_at="2026-07-29T09:00:00+08:00",
        observed_at="2026-07-29T01:00:01Z",
        project_timezone="Asia/Shanghai",
        terminal=False,
    )
    assert not is_overdue(
        due_at="2026-07-29T09:00:00+08:00",
        observed_at="2026-07-29T01:00:01Z",
        project_timezone="Asia/Shanghai",
        terminal=True,
    )


def test_workspace_done_gate_dependency_and_coverage_are_fail_closed() -> None:
    fixture = build_workspace_fixture_v1()
    entities = fixture.publication.entities
    entity_map = {item.entity_id: item for item in entities}

    def typed(entity_type: WorkspaceEntityType):
        return tuple(item for item in entities if item.entity_type == entity_type)

    gate = evaluate_workspace_done_gate(
        entity_map["work-01"],
        criteria=typed(WorkspaceEntityType.ACCEPTANCE_CRITERION),
        checks=typed(WorkspaceEntityType.ACCEPTANCE_CHECK),
        dependencies=typed(WorkspaceEntityType.DEPENDENCY),
        blockers=typed(WorkspaceEntityType.BLOCKER),
        evidence_links=typed(WorkspaceEntityType.EVIDENCE_LINK),
    )
    assert gate.allowed is False
    assert gate.unresolved_dependency_ids == ("dependency-01",)
    assert "acceptance-check-incomplete" in gate.reasons
    path = dependency_path(
        fixture.publication.edges,
        start_id="work-01",
        target_id="work-03",
    )
    assert path.path == ("work-01", "dependency-01", "work-03")
    assert path.found is True

    requirements = typed(WorkspaceEntityType.EVIDENCE_REQUIREMENT)
    links = typed(WorkspaceEntityType.EVIDENCE_LINK)
    implementation = evaluate_workspace_coverage(
        next(item for item in requirements if item.entity_id == "requirement-implementation"),
        links,
        observed_at=fixture.observed_at,
        allowed_acl_refs=(fixture.publication.scope.acl_ref,),
        enforce_acl=True,
    )
    assert implementation.satisfied is True
    assert implementation.independence_groups == ("code-main", "run-a")
    reproduction = evaluate_workspace_coverage(
        next(item for item in requirements if item.entity_id == "requirement-reproduction"),
        links,
        observed_at=fixture.observed_at,
        allowed_acl_refs=(fixture.publication.scope.acl_ref,),
        enforce_acl=True,
    )
    assert reproduction.satisfied is False
    assert reproduction.missing_roles == ("reproduction",)
    security = evaluate_workspace_coverage(
        next(item for item in requirements if item.entity_id == "requirement-security"),
        links,
        observed_at=fixture.observed_at,
        allowed_acl_refs=(fixture.publication.scope.acl_ref,),
        enforce_acl=True,
    )
    assert security.satisfied is True
    assert security.unauthorized_link_ids == ("evidence-link-12",)


def test_workspace_snapshot_and_intelligence_are_content_addressed_and_expire() -> None:
    fixture = build_workspace_fixture_v1()
    authoritative = tuple(
        item
        for item in fixture.publication.entities
        if item.entity_type
        not in {
            WorkspaceEntityType.INTELLIGENCE_RUN,
            WorkspaceEntityType.SNAPSHOT,
        }
    )
    snapshot = build_workspace_snapshot(
        scope=fixture.publication.scope,
        entities=authoritative,
        as_of=fixture.observed_at,
        source_watermarks={"workspace": fixture.publication.publication_sha256},
    )
    requirements = tuple(
        item
        for item in authoritative
        if item.entity_type == WorkspaceEntityType.EVIDENCE_REQUIREMENT
    )
    links = tuple(
        item for item in authoritative if item.entity_type == WorkspaceEntityType.EVIDENCE_LINK
    )
    coverage = tuple(
        evaluate_workspace_coverage(
            requirement,
            links,
            observed_at=fixture.observed_at,
            allowed_acl_refs=(fixture.publication.scope.acl_ref,),
            enforce_acl=True,
        )
        for requirement in requirements
    )
    intelligence, summary = derive_workspace_intelligence(
        scope=fixture.publication.scope,
        snapshot=snapshot,
        entities=authoritative,
        coverage=coverage,
        generated_at=fixture.observed_at,
        ttl_minutes=60,
    )
    assert intelligence.authority == "D_DERIVED_INTELLIGENCE"
    assert intelligence.metadata["authoritative_state_mutated"] is False
    assert summary.stage == "review"
    assert summary.readiness_score == 75
    assert intelligence_is_current(
        intelligence,
        observed_at=fixture.observed_at,
    )
    assert not intelligence_is_current(
        intelligence,
        observed_at=(
            datetime.fromisoformat(fixture.observed_at.replace("Z", "+00:00")) + timedelta(hours=2)
        ).isoformat(),
    )
    assert (
        workspace_locator(
            scope=fixture.publication.scope,
            entity_type=WorkspaceEntityType.SNAPSHOT,
            stable_id=snapshot.stable_id,
            version=1,
        )
        == snapshot.locator
    )
