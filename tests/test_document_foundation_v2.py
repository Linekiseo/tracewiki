from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.document.adapter_v2 import (
    DocumentAdapterError,
    DocumentAdapterV2,
    DocumentParserProvenanceInput,
    DocumentSourceBlock,
    DocumentSourcePage,
    DocumentSourcePayload,
    canonical_document_source_bytes,
)
from evidence_rag.rag.sources.document.claims_v2 import (
    DocumentClaimEvidence,
    DocumentEvidenceRelationship,
    validate_claim,
)
from evidence_rag.rag.sources.document.contracts import (
    DocumentClaimStatus,
    DocumentEntityType,
    DocumentLayoutBlockType,
    DocumentOcrStatus,
    DocumentParseQuality,
    DocumentParserProviderStatus,
    DocumentSourceKind,
    canonical_sha256,
)
from evidence_rag.rag.sources.document.evaluation_v1 import (
    DOCUMENT_GOLDEN_AUTHORITY_SHA256,
    DOCUMENT_GOLDEN_PACKAGE_SHA256,
    EXPECTED_DOCUMENT_SLICE_COUNTS,
    DocumentGoldenError,
    DocumentReviewAvailability,
    DocumentReviewedRow,
    build_document_golden_v1,
    evaluate_reviewed_document_retrieval,
)
from evidence_rag.rag.sources.document.fixture_v1 import build_document_fixture_v1
from evidence_rag.rag.sources.document.store import (
    DocumentPublicationError,
    DocumentStorePathError,
    DocumentV2Store,
)


def test_document_golden_is_exact_content_addressed_and_multiformat() -> None:
    dataset = build_document_golden_v1()
    rebuilt = build_document_golden_v1()
    assert dataset == rebuilt
    assert dataset.package_sha256 == DOCUMENT_GOLDEN_PACKAGE_SHA256
    assert dataset.authority_sha256 == DOCUMENT_GOLDEN_AUTHORITY_SHA256
    assert len(dataset.cases) == 50
    assert Counter(item.slice for item in dataset.cases) == Counter(EXPECTED_DOCUMENT_SLICE_COUNTS)
    assert tuple(item.case_id for item in dataset.cases) == tuple(
        f"doc-v1-{index:03d}" for index in range(1, 51)
    )


def test_document_golden_release_descriptor_matches_programmatic_authority() -> None:
    dataset = build_document_golden_v1()
    bundle = build_document_fixture_v1()
    descriptor = json.loads(
        (Path(__file__).parent / "fixtures" / "document_golden_v1" / "release.json").read_text(
            encoding="utf-8"
        )
    )
    assert descriptor["dataset_id"] == dataset.dataset_id
    assert descriptor["dataset_version"] == dataset.dataset_version
    assert descriptor["case_count"] == len(dataset.cases)
    assert descriptor["package_sha256"] == dataset.package_sha256
    assert descriptor["authority_sha256"] == dataset.authority_sha256
    assert descriptor["fixture_recipe_sha256"] == bundle.recipe_sha256
    assert descriptor["slice_counts"] == dict(
        sorted(Counter(item.slice.value for item in dataset.cases).items())
    )
    assert descriptor["entity_counts"] == dict(
        sorted(
            Counter(
                entity.entity_type.value
                for publication in dataset.publications
                for entity in publication.entities
            ).items()
        )
    )
    assert {item.version.source_kind for item in dataset.publications} == {
        "markdown",
        "docx",
        "pdf_scanned",
        "html",
    }
    entities = dataset.entity_map()
    counts = Counter(item.entity_type for item in entities.values())
    assert counts[DocumentEntityType.PARAGRAPH] == 32
    assert counts[DocumentEntityType.CLAIM_CANDIDATE] == 9
    assert counts[DocumentEntityType.CLAIM] == 9
    assert counts[DocumentEntityType.TABLE_CELL_FACT] == 44
    assert counts[DocumentEntityType.FIGURE] == 4
    assert counts[DocumentEntityType.FORMULA] == 4
    assert counts[DocumentEntityType.CITATION_MENTION] == 10
    assert any(item.expected_empty for item in dataset.cases)
    assert sum(bool(item.hard_negative_entity_ids) for item in dataset.cases) == 24


def test_document_adapter_keeps_candidates_unaccepted_and_sanitizes_html() -> None:
    payload = DocumentSourcePayload(
        source_key="html-test",
        title="HTML Test",
        version_label="v1",
        source_kind=DocumentSourceKind.HTML,
        content=(
            "<nav>noise</nav><h1>Result</h1><p>Claim: F1 reaches 82 percent.</p>"
            "<script>exfiltrate()</script><p>Author text.</p>"
        ),
    )
    publication = DocumentAdapterV2().parse_bytes(
        canonical_document_source_bytes(payload),
        project_id="project-doc-test",
        acl_ref="public",
        generation_id="docgen-test",
    )
    assert "exfiltrate" not in publication.version.source_text
    claims = [
        item
        for item in publication.entities
        if item.entity_type in {DocumentEntityType.CLAIM, DocumentEntityType.CLAIM_CANDIDATE}
    ]
    assert [item.entity_type for item in claims] == [DocumentEntityType.CLAIM_CANDIDATE]
    assert claims[0].status == DocumentClaimStatus.CANDIDATE


def test_document_pdf_layout_contract_verifies_digest_bbox_and_mixed_page_status() -> None:
    block = DocumentSourceBlock(
        block_id="a" * 32,
        ordinal=1,
        block_type=DocumentLayoutBlockType.HEADER,
        text="Native header",
        bbox=(54.0, 42.0, 220.0, 66.0),
        text_sha256="sha256:" + hashlib.sha256(b"Native header").hexdigest(),
        source="native",
        confidence=0.96,
        parser_name="pypdf",
        parser_version="6.14.2",
        bbox_method="pdf-text-matrix-v1",
    )
    page_identity = "sha256:" + "1" * 64
    layout_sha256 = canonical_sha256(
        {
            "page_identity": page_identity,
            "blocks": [block.model_dump(mode="json")],
        }
    )
    native = DocumentSourcePage(
        page_number=1,
        text="# Native header\n\nNative evidence.",
        width=612.0,
        height=792.0,
        page_identity=page_identity,
        page_content_sha256="sha256:" + "2" * 64,
        scan_detected=False,
        ocr_status=DocumentOcrStatus.NOT_REQUIRED,
        blocks=(block,),
        layout_payload_sha256=layout_sha256,
    )
    unavailable = DocumentSourcePage(
        page_number=2,
        width=612.0,
        height=792.0,
        page_identity="sha256:" + "3" * 64,
        page_content_sha256="sha256:" + "4" * 64,
        scan_detected=True,
        ocr_status=DocumentOcrStatus.UNAVAILABLE,
        blocks=(),
        layout_payload_sha256=canonical_sha256(
            {"page_identity": "sha256:" + "3" * 64, "blocks": []}
        ),
        parser_provenance=DocumentParserProvenanceInput(
            provider_name="local-unavailable",
            provider_version="unavailable-v1",
            provider_status=DocumentParserProviderStatus.UNAVAILABLE,
            input_sha256="sha256:" + "5" * 64,
            output_sha256="sha256:" + "6" * 64,
        ),
    )
    payload = DocumentSourcePayload(
        source_key="mixed-layout",
        title="Mixed layout",
        version_label="v1",
        source_kind=DocumentSourceKind.PDF_MIXED,
        pages=(native, unavailable),
    )
    publication = DocumentAdapterV2().parse_bytes(
        canonical_document_source_bytes(payload),
        project_id="project-doc-test",
        acl_ref="public",
        generation_id="docgen-test",
    )
    assert publication.version.source_kind == DocumentSourceKind.PDF_MIXED
    assert publication.version.parse_quality == DocumentParseQuality.DEGRADED
    layout = [
        item for item in publication.entities if item.entity_type == DocumentEntityType.LAYOUT_BLOCK
    ]
    assert len(layout) == 1
    assert layout[0].metadata["bbox"] == [54.0, 42.0, 220.0, 66.0]
    assert layout[0].metadata["page_identity"] == page_identity

    with pytest.raises(ValidationError, match="layout payload digest mismatch"):
        DocumentSourcePage.model_validate(
            {
                **native.model_dump(mode="python"),
                "layout_payload_sha256": "sha256:" + "f" * 64,
            }
        )


@pytest.mark.parametrize(
    "unsafe",
    (
        "api_key=abcdefghijklmno",
        "/Users/example/private/report.pdf",
        r"C:\private\report.pdf",
        r"\\server\share\report.pdf",
        "%252FUsers%252Fexample%252Freport.pdf",
        "%255C%255Cserver%255Cshare%255Creport.pdf",
    ),
)
def test_document_adapter_rejects_secret_and_raw_or_encoded_absolute_paths(
    unsafe: str,
) -> None:
    payload = DocumentSourcePayload(
        source_key="unsafe",
        title="Unsafe",
        version_label="v1",
        source_kind=DocumentSourceKind.MARKDOWN,
        content=f"# Unsafe\n\n{unsafe}",
    )
    with pytest.raises(DocumentAdapterError):
        DocumentAdapterV2().parse_bytes(
            canonical_document_source_bytes(payload),
            project_id="project-doc-test",
            acl_ref="public",
            generation_id="docgen-test",
        )


def test_document_table_facts_preserve_header_paths_units_and_footnotes() -> None:
    bundle = build_document_fixture_v1()
    cells = [
        item
        for publication in bundle.publications
        if publication.family.source_key == "reranker-ablation-report"
        for item in publication.entities
        if item.entity_type == DocumentEntityType.TABLE_CELL_FACT
        and item.label == "Candidate/Large / F1"
    ]
    assert len(cells) == 4
    assert {item.row_path[-1] for item in cells} == {
        "Seed=7",
        "Seed=11",
        "Seed=19",
    }
    assert all(item.column_path == ("F1",) for item in cells)
    assert [item.numeric_center for item in cells] == [81.7, 82.1, 82.5, 84.9]
    assert cells[1].unit == "percent"
    assert cells[1].footnotes == ("*",)
    assert cells[1].metadata["metric_confirmed"] is False


def test_document_claim_policy_requires_reviewed_layered_evidence() -> None:
    bundle = build_document_fixture_v1()
    claim = next(
        item
        for publication in bundle.publications
        for item in publication.entities
        if item.entity_type == DocumentEntityType.CLAIM
    )
    insufficient, checks = validate_claim(claim, ())
    assert insufficient == DocumentClaimStatus.INSUFFICIENT_EVIDENCE
    assert checks["L1_identity"] is False
    support = DocumentClaimEvidence(
        claim_id=claim.entity_id,
        evidence_entity_id="metric-reviewed",
        relationship=DocumentEvidenceRelationship.SUPPORTS,
        identity_match=True,
        numeric_match=True,
        design_comparable=True,
        current=True,
        reviewed=True,
    )
    verified, checks = validate_claim(claim, (support,))
    assert verified == DocumentClaimStatus.VERIFIED
    assert all(
        checks[name]
        for name in ("L0_source", "L1_identity", "L2_numeric", "L3_design", "L4_current")
    )
    refute = support.model_copy(
        update={
            "evidence_entity_id": "metric-refute",
            "relationship": DocumentEvidenceRelationship.REFUTES,
        }
    )
    contradicted, _ = validate_claim(claim, (support, refute))
    assert contradicted == DocumentClaimStatus.CONTRADICTED


def test_document_isolated_store_is_atomic_idempotent_governed_and_tombstoned(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    store = DocumentV2Store(root / "document.sqlite", isolated_root=root)
    store.initialize()
    bundle = build_document_fixture_v1()
    for publication in bundle.publications:
        assert store.publish(publication)["status"] == "published"
        assert store.publish(publication)["status"] == "already_published"
    counts = store.table_counts()
    assert counts["document_families_v2"] == 4
    assert counts["document_versions_v2"] == 5
    assert counts["document_entities_v2"] == 175
    assert counts["document_table_cell_facts_v2"] == 44
    assert counts["document_publications_v2"] == 5
    assert store.database_sidecars() == ()
    scope = bundle.publications[0].family.scope
    assert len(store.list_publications(scope)) == 5
    target = bundle.publications[-1]
    store.tombstone(
        scope,
        family_id=target.family.family_id,
        version_id=target.version.version_id,
        reason="source-deleted",
    )
    assert len(store.list_publications(scope)) == 4

    alt_id = "docpub-" + "f" * 32
    payload = target.model_dump(mode="python", round_trip=True)
    payload["publication_id"] = alt_id
    payload["publication_sha256"] = canonical_sha256(
        {
            "publication_id": alt_id,
            "source_payload_sha256": target.source_payload_sha256,
            "family": target.family.model_dump(mode="json"),
            "version": target.version.model_dump(mode="json"),
            "entities": [item.model_dump(mode="json") for item in target.entities],
            "edges": [item.model_dump(mode="json") for item in target.edges],
            "retrieval_units": [item.model_dump(mode="json") for item in target.retrieval_units],
        }
    )
    alternate = type(target).model_validate(payload)
    with pytest.raises(DocumentPublicationError):
        store.publish(alternate)
    assert store.table_counts()["document_publications_v2"] == 5


def test_document_store_rejects_formal_sidecar_escape_and_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    with pytest.raises(DocumentStorePathError):
        DocumentV2Store(root / "evidence-rag.sqlite3", isolated_root=root)
    with pytest.raises(DocumentStorePathError):
        DocumentV2Store(root / "document.sqlite-wal", isolated_root=root)
    with pytest.raises(DocumentStorePathError):
        DocumentV2Store(root.parent / "outside.sqlite", isolated_root=root)
    real = root / "real"
    real.mkdir()
    alias = root / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(DocumentStorePathError):
        DocumentV2Store(alias / "document.sqlite", isolated_root=root)


def test_document_evaluator_freezes_membership_and_ignores_no_self_reported_truth() -> None:
    dataset = build_document_golden_v1()
    unavailable = tuple(
        DocumentReviewedRow(
            case_id=case.case_id,
            availability=DocumentReviewAvailability.UNAVAILABLE,
            diagnostic="not-reviewed",
        )
        for case in dataset.cases
    )
    report = evaluate_reviewed_document_retrieval(unavailable, dataset=dataset)
    assert all(item.status == "UNAVAILABLE" for item in report.metrics)
    with pytest.raises(DocumentGoldenError):
        evaluate_reviewed_document_retrieval(
            unavailable,
            dataset=dataset,
            case_membership=(),
        )
    with pytest.raises(ValidationError):
        DocumentReviewedRow.model_validate(
            {
                "case_id": "doc-v1-001",
                "availability": "AVAILABLE",
                "candidates": [],
                "expected_entity_ids": ["forged"],
            }
        )
