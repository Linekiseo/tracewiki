from __future__ import annotations

from pathlib import Path

from evidence_rag.rag.sources.workspace.context_builder import (
    build_workspace_context,
)
from evidence_rag.rag.sources.workspace.evaluation_v1 import (
    WorkspaceMetric,
    WorkspaceReviewAvailability,
    WorkspaceReviewedCandidate,
    WorkspaceReviewedRow,
    build_workspace_golden_v1,
    evaluate_reviewed_workspace_retrieval,
)
from evidence_rag.rag.sources.workspace.fixture_v1 import (
    build_workspace_fixture_v1,
)
from evidence_rag.rag.sources.workspace.retriever import (
    WorkspaceRetrieverV2,
)
from evidence_rag.rag.sources.workspace.store import WorkspaceV2Store


def _published(tmp_path: Path):
    fixture = build_workspace_fixture_v1()
    store = WorkspaceV2Store(tmp_path / "workspace.sqlite", isolated_root=tmp_path)
    store.publish(fixture.publication)
    return fixture, WorkspaceRetrieverV2(store)


def test_workspace_full_40_case_retrieval_and_honest_denominators(
    tmp_path: Path,
) -> None:
    fixture, retriever = _published(tmp_path)
    dataset = build_workspace_golden_v1()
    rows = []
    for case in dataset.cases:
        result = retriever.search(
            scope=fixture.publication.scope,
            query=case.query,
            as_of=case.as_of,
            limit=10,
            allowed_acl_refs=(fixture.publication.scope.acl_ref,),
            enforce_acl=True,
        )
        rows.append(
            WorkspaceReviewedRow(
                case_id=case.case_id,
                availability=WorkspaceReviewAvailability.AVAILABLE,
                candidates=tuple(
                    WorkspaceReviewedCandidate(
                        entity_id=item.entity_id,
                        locator=item.locator,
                    )
                    for item in result.candidates
                ),
            )
        )
        assert result.trace["read_only"] is True
        assert result.trace["mutation_applied"] is False
        assert result.trace["reasoning_included"] is False
    report = evaluate_reviewed_workspace_retrieval(tuple(rows), dataset=dataset)
    metrics = {item.metric: item for item in report.metrics}
    assert all(item.numerator == item.eligible_denominator for item in report.slices)
    assert metrics[WorkspaceMetric.SCOPE_RESOLUTION_ACCURACY].numerator == 40
    assert metrics[WorkspaceMetric.HARD_NEGATIVE_AVOIDANCE].numerator == 38
    assert metrics[WorkspaceMetric.HARD_NEGATIVE_AVOIDANCE].eligible_denominator == 40
    assert metrics[WorkspaceMetric.UNSAFE_MUTATION_RATE].numerator == 0
    assert metrics[WorkspaceMetric.ACL_LEAKAGE_RATE].numerator == 0


def test_workspace_duplicate_scope_acl_and_expired_intelligence_fail_closed(
    tmp_path: Path,
) -> None:
    fixture, retriever = _published(tmp_path)
    duplicate = retriever.search(
        scope=fixture.publication.scope,
        query="List all topics named RAG system design including archived history",
        limit=10,
        allowed_acl_refs=(fixture.publication.scope.acl_ref,),
        enforce_acl=True,
    )
    assert duplicate.ambiguous_scope is True
    assert {"topic-rag-active", "topic-rag-archived"}.issubset(
        {item.entity_id for item in duplicate.candidates}
    )
    denied = retriever.search(
        scope=fixture.publication.scope,
        query="Show evidence requirement security",
        limit=10,
        allowed_acl_refs=("workspace-private",),
        enforce_acl=True,
    )
    assert denied.candidates == ()
    assert denied.missing == ("project_acl_denied",)
    intelligence = retriever.search(
        scope=fixture.publication.scope,
        query="What is the current next action and readiness?",
        limit=10,
        allowed_acl_refs=(fixture.publication.scope.acl_ref,),
        enforce_acl=True,
    )
    assert "intelligence-expired" not in {item.entity_id for item in intelligence.candidates}
    private = retriever.search(
        scope=fixture.publication.scope,
        query="Show evidence coverage security",
        limit=10,
        allowed_acl_refs=(fixture.publication.scope.acl_ref,),
        enforce_acl=True,
    )
    assert "evidence-link-12" not in {item.entity_id for item in private.candidates}


def test_workspace_context_is_deterministic_budgeted_and_authority_visible(
    tmp_path: Path,
) -> None:
    fixture, retriever = _published(tmp_path)
    result = retriever.search(
        scope=fixture.publication.scope,
        query="What is the current next action and readiness?",
        limit=10,
        allowed_acl_refs=(fixture.publication.scope.acl_ref,),
        enforce_acl=True,
    )
    first = build_workspace_context(
        result,
        budget_characters=900,
        per_block_characters=220,
    )
    second = build_workspace_context(
        result,
        budget_characters=900,
        per_block_characters=220,
    )
    assert first == second
    assert first.availability in {"AVAILABLE", "PROVISIONAL"}
    assert first.blocks
    assert first.used_characters <= first.budget_characters
    assert first.reasoning_included is False
    assert first.mutation_applied is False
    assert set(first.citation_map) == {item.citation_id for item in first.blocks}
    assert all(item.authority.value in item.body for item in first.blocks)

    denied = retriever.search(
        scope=fixture.publication.scope,
        query="Show TASK-0001",
        allowed_acl_refs=(),
        enforce_acl=True,
    )
    unavailable = build_workspace_context(denied)
    assert unavailable.availability == "UNAVAILABLE"
    assert unavailable.blocks == ()
