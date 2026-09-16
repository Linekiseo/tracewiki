from __future__ import annotations

from evidence_rag.documents.models import DocumentIngestRequest
from evidence_rag.experiments.models import ExperimentCreate
from evidence_rag.platform.models import GlobalSearchRequest
from evidence_rag.rag.planner_v2 import build_query_plan, root_provenance_key
from evidence_rag.rag.sources.document.contracts import canonical_sha256
from evidence_rag.rag.sources.document.release_v2 import (
    DocumentReleaseEvidence,
    DocumentReleaseStage,
    evaluate_document_release_v2,
)
from evidence_rag.runtime import create_runtime
from evidence_rag.workspace.models import RelationCreate, RelationReview


def _document_shadow_release() -> DocumentReleaseEvidence:
    values = {
        "evaluator_version": "document-release-evaluator-v2",
        "observed_stage": DocumentReleaseStage.ISOLATED_BASELINE,
        "proposed_stage": DocumentReleaseStage.SHADOW_INTERNAL_100,
        "artifact_set_sha256": "sha256:" + "1" * 64,
        "golden_package_sha256": "sha256:" + "2" * 64,
        "retriever_version": "document-retriever-v2",
        "reranker_version": "document-reranker-v2",
        "context_builder_version": "document-context-v2",
        "production_observation": True,
        "metrics": (),
        "guardrails": (),
    }
    return DocumentReleaseEvidence(
        evidence_id="docrelease-" + canonical_sha256(values).removeprefix("sha256:")[:64],
        **values,
    )


def test_planner_limits_implicit_first_wave_and_preserves_explicit_scope() -> None:
    implicit = build_query_plan(
        query="What changed and why?",
        intent="rationale",
        requested_sources=[],
        limit=20,
        max_hops=2,
    )
    assert implicit["routed_sources"] == ["codex", "workspace"]
    assert implicit["fallback_sources"] == ["code", "document"]
    assert implicit["route_reason"] == "intent_default"
    assert implicit["max_source_waves"] == 2

    explicit = build_query_plan(
        query="Compare every available source",
        intent="global_synthesis",
        requested_sources=["document", "experiment"],
        limit=20,
        max_hops=2,
    )
    assert explicit["routed_sources"] == ["document", "experiment"]
    assert explicit["fallback_sources"] == []
    assert explicit["route_reason"] == "explicit_request"


def test_root_provenance_dedup_is_cross_source_and_selection_has_no_source_quota(
    settings,
) -> None:
    runtime = create_runtime(settings)
    duplicate_rows = [
        {
            "entity_id": "code-a",
            "source": "code",
            "version": "v1",
            "score": 0.8,
            "channels": ["dense"],
            "metadata": {"root_provenance": "artifact://shared-root"},
        },
        {
            "entity_id": "document-a",
            "source": "document",
            "version": "v1",
            "score": 0.9,
            "channels": ["lexical"],
            "metadata": {"root_provenance": "artifact://shared-root"},
        },
    ]
    assert root_provenance_key(duplicate_rows[0]) == root_provenance_key(duplicate_rows[1])
    deduplicated = runtime.platform._deduplicate(duplicate_rows)
    assert [item["entity_id"] for item in deduplicated] == ["document-a"]
    assert deduplicated[0]["deduplicated_sources"] == ["code", "document"]

    ranked = [
        {
            "entity_id": f"code-{index}",
            "source": "code",
            "score": score,
            "fusion_explanation": {},
        }
        for index, score in enumerate((0.95, 0.94, 0.93), start=1)
    ]
    ranked.append(
        {
            "entity_id": "document-irrelevant",
            "source": "document",
            "score": 0.1,
            "fusion_explanation": {},
        }
    )
    selected = runtime.platform._diversify(ranked, 3)
    assert [item["entity_id"] for item in selected] == ["code-1", "code-2", "code-3"]
    assert all(item["fusion_explanation"]["source_slot_reserved"] is False for item in selected)


def test_production_search_uses_bounded_source_waves_and_honest_empty_status(
    settings,
) -> None:
    runtime = create_runtime(settings)
    runtime.experiments.create_experiment(
        ExperimentCreate(
            title="Phoenix metric validation",
            objective="Validate the Phoenix ranking metric.",
        )
    )
    runtime.documents.ingest(
        DocumentIngestRequest(
            title="Phoenix report",
            content="# Result\n\nClaim: The Phoenix metric report records the validation.",
        )
    )

    response = runtime.platform.search(
        GlobalSearchRequest(
            query="Phoenix metric report",
            include_lineage=False,
            limit=10,
        ),
        record_event=False,
    )
    planner = response["trace"]["planner"]
    assert planner["routed_sources"] == ["experiment", "document"]
    assert len(planner["fallback_sources"]) <= 2
    assert set(response["trace"]["sources"]) <= {
        *planner["routed_sources"],
        *planner["fallback_sources"],
    }
    assert planner["source_waves"][0]["execution_mode"] == "parallel"
    assert response["trace"]["execution"]["max_workers"] <= 4
    assert response["trace"]["score_is_calibrated_probability"] is False

    empty = runtime.platform.search(
        GlobalSearchRequest(
            query="definitely-absent-evidence-token",
            sources=["document"],
            include_lineage=False,
        ),
        record_event=False,
    )
    assert empty["trace"]["sources"]["document"]["status"] == "no_match"
    assert empty["trace"]["status"] == "COMPLETE"
    assert empty["trace"]["planner"]["corrective_attempts"] == []


def test_verified_shadow_fault_falls_back_without_replacing_production_adapter(
    settings,
    monkeypatch,
) -> None:
    runtime = create_runtime(settings)
    evidence = _document_shadow_release()
    runtime.source_runtime_v2.install_release_authority(
        source="document",
        project_id="project-rag",
        evidence=evidence,
        decision=evaluate_document_release_v2(evidence),
    )
    document_facade = runtime.source_runtime_v2.get("document")

    def fail_document_search(**_: object) -> dict[str, object]:
        raise RuntimeError("isolated source fault")

    monkeypatch.setattr(
        document_facade,
        "search",
        fail_document_search,
    )
    response = runtime.platform.search(
        GlobalSearchRequest(
            query="metric report",
            sources=["document", "experiment"],
            include_lineage=False,
        ),
        record_event=False,
        document_engine_override="v2",
    )
    document_trace = response["trace"]["sources"]["document"]
    assert document_trace["status"] == "no_match"
    assert document_trace["engine_used"] == "v1"
    assert document_trace["release_route"]["served_engine"] == "v1"
    assert document_trace["release_route"]["fallback_reason"] == "internal_v2_error"
    assert document_trace["release_route"]["shadow_outcome"] == "v2_failed"
    assert runtime.source_runtime_v2.get("document") is document_facade
    assert response["trace"]["sources"]["experiment"]["status"] == "no_match"
    assert response["trace"]["status"] == "COMPLETE"


def test_graph_expands_only_reviewed_relations_after_scope_checks(settings) -> None:
    runtime = create_runtime(settings)
    experiment = runtime.experiments.create_experiment(
        ExperimentCreate(title="Orion validation target")
    )
    document = runtime.documents.ingest(
        DocumentIngestRequest(
            title="Orion report",
            content="# Result\n\nClaim: Orion evidence requires a reviewed relation.",
        )
    )
    claim = document["claims"][0]
    relation = runtime.workspace.create_relation(
        RelationCreate(
            source_entity_id=claim["id"],
            predicate="supported_by",
            target_entity_id=experiment["id"],
            derivation="llm_inferred",
            confidence=0.9,
            review_status="unreviewed",
        )
    )
    request = GlobalSearchRequest(
        query="Orion evidence",
        sources=["document"],
        include_lineage=True,
        max_hops=2,
    )

    unreviewed = runtime.platform.search(request, record_event=False)
    assert relation["id"] not in {edge["id"] for edge in unreviewed["evidence_pack"]["relations"]}
    assert unreviewed["trace"]["relation_expansion"]["pruned_review"] >= 1
    assert all(
        "confirmed_relation" not in item["channels"]
        for item in unreviewed["evidence_pack"]["related_entities"]
    )

    runtime.workspace.review_relation(
        relation["id"],
        RelationReview(review_status="confirmed", reviewer="test-reviewer"),
    )
    reviewed = runtime.platform.search(request, record_event=False)
    assert relation["id"] in {edge["id"] for edge in reviewed["evidence_pack"]["relations"]}
    related = reviewed["evidence_pack"]["related_entities"]
    assert any(
        item["entity_id"] == experiment["id"] and "confirmed_relation" in item["channels"]
        for item in related
    )
