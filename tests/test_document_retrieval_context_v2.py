from __future__ import annotations

from pathlib import Path

from evidence_rag.rag.sources.document.context_builder import build_document_context
from evidence_rag.rag.sources.document.evaluation_v1 import (
    DocumentReviewAvailability,
    DocumentReviewedCandidate,
    DocumentReviewedRow,
    build_document_golden_v1,
    evaluate_reviewed_document_retrieval,
)
from evidence_rag.rag.sources.document.fixture_v1 import build_document_fixture_v1
from evidence_rag.rag.sources.document.retriever import (
    DOCUMENT_DENSE_PROFILE_VERSION,
    DocumentRetrieverV2,
)
from evidence_rag.rag.sources.document.store import DocumentV2Store
from evidence_rag.rag.sources.document.versioning_v2 import (
    compare_document_versions,
)


def _published_store(tmp_path: Path) -> tuple[DocumentV2Store, object]:
    root = tmp_path.resolve()
    store = DocumentV2Store(root / "document.sqlite", isolated_root=root)
    store.initialize()
    bundle = build_document_fixture_v1()
    for publication in bundle.publications:
        store.publish(publication)
    return store, bundle


def test_document_retrieval_channels_context_and_fail_closed_abstention(
    tmp_path: Path,
) -> None:
    store, bundle = _published_store(tmp_path)
    retriever = DocumentRetrieverV2(store)
    scope = bundle.publications[0].family.scope
    table = retriever.search(
        scope=scope,
        query="v1 Candidate NQ test Recall",
        limit=10,
    )
    assert table.candidates[0].entity_type == "table_cell_fact"
    assert {"exact", "sparse", "table", "dense_source"} & set(table.candidates[0].channels)
    assert table.trace["dense_profile"] == DOCUMENT_DENSE_PROFILE_VERSION
    assert table.trace["dense_source"] == "AVAILABLE"
    assert table.trace["query_included"] is False
    context = build_document_context(table)
    assert context.availability == "AVAILABLE"
    assert context.blocks[0].heading.startswith("TABLE")
    assert "Row path:" in context.blocks[0].body
    assert "Metric relation: unconfirmed" in context.blocks[0].body
    assert context.reasoning_included is False
    assert context.citation_map["DOC-001"] == context.blocks[0].locator

    refusal = retriever.search(
        scope=scope,
        query="Is OCR Recall 0.86 verified for the current NQ test split?",
        limit=10,
    )
    assert refusal.candidates == ()
    unavailable = build_document_context(refusal)
    assert unavailable.availability == "UNAVAILABLE"
    assert "authorized_document_evidence" in unavailable.missing

    missing_caption = retriever.search(
        scope=scope,
        query="What does the author caption of Figure 3 say?",
        limit=10,
    )
    assert missing_caption.candidates == ()


def test_document_context_budget_is_deterministic_and_line_safe(tmp_path: Path) -> None:
    store, bundle = _published_store(tmp_path)
    result = DocumentRetrieverV2(store).search(
        scope=bundle.publications[0].family.scope,
        query="Summarize v2 architecture results and recovery with section coverage",
        limit=10,
    )
    left = build_document_context(result, max_characters=700, max_block_characters=180)
    right = build_document_context(result, max_characters=700, max_block_characters=180)
    assert left == right
    assert left.character_count <= 700
    assert left.truncated is True
    assert all(block.body for block in left.blocks)


def test_document_full_50_case_baseline_has_honest_frozen_denominators(
    tmp_path: Path,
) -> None:
    store, bundle = _published_store(tmp_path)
    dataset = build_document_golden_v1()
    retriever = DocumentRetrieverV2(store)
    rows = []
    for case in dataset.cases:
        result = retriever.search(
            scope=bundle.publications[0].family.scope,
            query=case.query,
            limit=10,
        )
        rows.append(
            DocumentReviewedRow(
                case_id=case.case_id,
                availability=DocumentReviewAvailability.AVAILABLE,
                candidates=tuple(
                    DocumentReviewedCandidate(
                        entity_id=item.entity_id,
                        locator=item.locator,
                    )
                    for item in result.candidates
                ),
            )
        )
    report = evaluate_reviewed_document_retrieval(tuple(rows), dataset=dataset)
    metrics = {item.metric.value: item for item in report.metrics}
    assert metrics["retrieval_recall_at_10"].eligible_denominator == 50
    assert metrics["retrieval_recall_at_10"].numerator == 49
    assert metrics["claim_validation_precision"].eligible_denominator == 10
    assert metrics["claim_validation_precision"].numerator == 9
    assert metrics["table_header_path_accuracy"].numerator == 10
    assert metrics["numeric_unit_accuracy"].numerator == 10
    assert metrics["citation_resolution_accuracy"].numerator == 4
    assert metrics["version_alignment_accuracy"].numerator == 2
    assert metrics["hard_negative_avoidance"].numerator == 45
    assert metrics["unsupported_verification_rate"].numerator == 0
    assert all(item.status == "AVAILABLE" for item in report.metrics)


def test_document_version_alignment_marks_modified_claim_stale() -> None:
    bundle = build_document_fixture_v1()
    versions = tuple(
        item for item in bundle.publications if item.family.source_key == "evidence-pack-design"
    )
    comparison = compare_document_versions(*versions)
    assert comparison.baseline_version_id == versions[0].version.version_id
    assert comparison.candidate_version_id == versions[1].version.version_id
    assert comparison.modified_ids
    assert comparison.stale_claim_ids
    assert comparison.comparison_sha256.startswith("sha256:")


def test_document_acl_and_generation_are_required_exactly(tmp_path: Path) -> None:
    store, bundle = _published_store(tmp_path)
    retriever = DocumentRetrieverV2(store)
    scope = bundle.publications[0].family.scope
    assert retriever.search(scope=scope, query="EvidencePack", limit=5).candidates
    wrong_acl = scope.model_copy(update={"acl_ref": "project-other"})
    assert retriever.search(scope=wrong_acl, query="EvidencePack", limit=5).candidates == ()
    wrong_generation = scope.model_copy(update={"generation_id": "docgen-other"})
    assert retriever.search(scope=wrong_generation, query="EvidencePack", limit=5).candidates == ()
