from __future__ import annotations

from evidence_rag.rag.evidence_graph_v2 import (
    EdgeFactStatusV2,
    EdgeReviewStatusV2,
    build_default_edge_registry_v2,
    build_evidence_edge_v2,
    traverse_evidence_graph_v2,
)
from evidence_rag.rag.multisource_foundation_v2 import (
    MultiSourceCandidateV2,
    MultiSourceScopeV2,
    build_default_capability_registry_v2,
    plan_multisource_query_v2,
)


def _candidate(identity: str, source: str, role: str, entity_type: str):
    tasks = {
        "code": "test_validation",
        "codex": "validation",
        "experiment": "aggregate",
        "notebook": "output",
        "document": "claim",
        "workspace": "coverage",
    }
    return MultiSourceCandidateV2(
        candidate_id=identity,
        entity_id=identity,
        retrieval_unit_id=f"unit-{identity}",
        parent_entity_id=None,
        source_instance=f"{source}-1",
        retrieval_domain=source,
        fact_type=f"{source}.fact",
        entity_type=entity_type,
        task=tasks[source],
        title=identity,
        snippet="evidence",
        locator=f"{source}://{identity}",
        stable_version="v1",
        source_generation=f"{source}-g1",
        raw_or_derived="raw_fact",
        derivation="source_record",
        review_status="reviewed",
        fact_status="verified",
        channel_scores=(("exact", 1.0),),
        calibrated_relevance=0.9,
        calibration_version=f"{source}-cal-v2",
        matched_roles=(role,),
        authority=1,
        version_alignment="exact",
        acl_ref="acl-1",
        token_estimate=100,
        root_provenance=identity,
    )


def _plan():
    domains = ("code", "codex", "experiment", "notebook", "document", "workspace")
    registry = build_default_capability_registry_v2(
        generations={domain: f"{domain}-g1" for domain in domains},
        watermarks={domain: f"{domain}-w1" for domain in domains},
    )
    return plan_multisource_query_v2(
        question="Is the claim supported?",
        intent="claim_verification",
        scope=MultiSourceScopeV2(project_id="project-1", acl_refs=("acl-1",)),
        registry=registry,
    )


def test_typed_beam_accepts_reviewed_path_and_tracks_roles() -> None:
    plan = _plan()
    claim = _candidate("claim", "document", "claim", "claim")
    run = _candidate(
        "run",
        "experiment",
        "metric_observation",
        "ExperimentMetricObservationV2",
    )
    edge = build_evidence_edge_v2(
        predicate="SUPPORTED_BY",
        source=claim,
        target=run,
        derivation="human_confirmed",
        review_status=EdgeReviewStatusV2.REVIEWED,
        fact_status=EdgeFactStatusV2.VERIFIED,
        confidence=0.9,
        evidence_locator="relation://claim-run",
    )
    result = traverse_evidence_graph_v2(
        plan,
        (claim, run),
        (edge,),
        build_default_edge_registry_v2(),
        seed_ids=("claim",),
    )
    assert result.accepted_paths
    assert result.accepted_paths[0].verified
    assert "metric_observation" in result.accepted_paths[0].matched_roles
    assert result.accepted_paths[0].predicates == ("SUPPORTED_BY",)
    assert result.accepted_paths[0].typed_relations == (
        "document.fact[claim]-SUPPORTED_BY->experiment.fact[ExperimentMetricObservationV2]",
    )


def test_unreviewed_acl_version_and_cycle_edges_are_pruned() -> None:
    plan = _plan()
    claim = _candidate("claim", "document", "claim", "claim")
    run = _candidate(
        "run",
        "experiment",
        "metric_observation",
        "ExperimentMetricObservationV2",
    )
    unreviewed = build_evidence_edge_v2(
        predicate="SUPPORTED_BY",
        source=claim,
        target=run,
        derivation="human_confirmed",
        review_status=EdgeReviewStatusV2.UNREVIEWED,
        fact_status=EdgeFactStatusV2.CANDIDATE,
        confidence=0.9,
        evidence_locator="relation://unreviewed",
    )
    back = build_evidence_edge_v2(
        predicate="SUPPORTS",
        source=run,
        target=claim,
        derivation="human_confirmed",
        review_status=EdgeReviewStatusV2.REVIEWED,
        fact_status=EdgeFactStatusV2.VERIFIED,
        confidence=0.9,
        evidence_locator="relation://cycle",
    )
    result = traverse_evidence_graph_v2(
        plan,
        (claim, run),
        (unreviewed, back),
        build_default_edge_registry_v2(),
        seed_ids=("claim",),
    )
    assert result.pruned_review == 1
    assert result.accepted_paths == ()
    assert result.corrective_rounds


def test_edge_builder_rejects_fact_placeholder_instead_of_erasing_typed_semantics() -> None:
    claim = _candidate("claim", "document", "claim", "document.fact")
    run = _candidate(
        "run",
        "experiment",
        "metric_observation",
        "ExperimentMetricObservationV2",
    )
    try:
        build_evidence_edge_v2(
            predicate="SUPPORTED_BY",
            source=claim,
            target=run,
            derivation="human_confirmed",
            review_status=EdgeReviewStatusV2.REVIEWED,
            fact_status=EdgeFactStatusV2.VERIFIED,
            confidence=0.9,
            evidence_locator="relation://invalid",
        )
    except ValueError as error:
        assert "typed relation" in str(error)
    else:
        raise AssertionError("collapsed fact placeholder was accepted as a typed endpoint")


def test_verified_claim_relation_requires_reviewed_authority() -> None:
    claim = _candidate("claim", "document", "claim", "claim")
    run = _candidate(
        "run",
        "experiment",
        "metric_observation",
        "ExperimentMetricObservationV2",
    )
    try:
        build_evidence_edge_v2(
            predicate="SUPPORTED_BY",
            source=claim,
            target=run,
            derivation="human_confirmed",
            review_status=EdgeReviewStatusV2.UNREVIEWED,
            fact_status=EdgeFactStatusV2.VERIFIED,
            confidence=0.9,
            evidence_locator="relation://unreviewed-verified",
        )
    except ValueError as error:
        assert "reviewed authority" in str(error)
    else:
        raise AssertionError("unreviewed claim relation was promoted to verified")


def test_registered_inverse_relation_preserves_types_and_prunes_cycle() -> None:
    plan = _plan()
    claim = _candidate("claim", "document", "claim", "claim")
    run = _candidate(
        "run",
        "experiment",
        "metric_observation",
        "ExperimentMetricObservationV2",
    )
    forward = build_evidence_edge_v2(
        predicate="SUPPORTED_BY",
        source=claim,
        target=run,
        derivation="human_confirmed",
        review_status=EdgeReviewStatusV2.REVIEWED,
        fact_status=EdgeFactStatusV2.VERIFIED,
        confidence=0.9,
        evidence_locator="relation://forward",
    )
    inverse = build_evidence_edge_v2(
        predicate="SUPPORTS",
        source=run,
        target=claim,
        derivation="human_confirmed",
        review_status=EdgeReviewStatusV2.REVIEWED,
        fact_status=EdgeFactStatusV2.VERIFIED,
        confidence=0.9,
        evidence_locator="relation://inverse",
    )
    result = traverse_evidence_graph_v2(
        plan,
        (claim, run),
        (forward, inverse),
        build_default_edge_registry_v2(),
        seed_ids=("claim",),
    )
    assert result.accepted_paths
    assert result.pruned_cycle == 1
