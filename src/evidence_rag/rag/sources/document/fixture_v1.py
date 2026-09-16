"""Deterministic multi-format Scientific Document Golden fixture."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Self

from pydantic import BaseModel, ConfigDict

from .adapter_v2 import (
    DocumentAdapterV2,
    DocumentSourcePage,
    DocumentSourcePayload,
    canonical_document_source_bytes,
)
from .claims_v2 import (
    DocumentClaimReview,
    DocumentClaimReviewDecision,
    review_claims,
)
from .contracts import (
    DocumentEntity,
    DocumentEntityType,
    DocumentPublication,
    DocumentSourceKind,
    canonical_sha256,
)

DOCUMENT_GOLDEN_DATASET_ID = "document-golden-v1"
DOCUMENT_GOLDEN_DATASET_VERSION = "v1"
DOCUMENT_GOLDEN_PROJECT_ID = "project-document-golden-v1"
DOCUMENT_GOLDEN_ACL_REF = "public"
DOCUMENT_GOLDEN_GENERATION_ID = "docgen-golden-v1"
DOCUMENT_FIXTURE_RECIPE_VERSION = "document-programmatic-fixture-v1"


class _FrozenFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class DocumentFixtureAlias(_FrozenFixture):
    name: str
    entity_id: str


class DocumentFixtureBundle(_FrozenFixture):
    recipe_version: str = DOCUMENT_FIXTURE_RECIPE_VERSION
    dataset_id: str = DOCUMENT_GOLDEN_DATASET_ID
    dataset_version: str = DOCUMENT_GOLDEN_DATASET_VERSION
    publications: tuple[DocumentPublication, ...]
    aliases: tuple[DocumentFixtureAlias, ...]
    recipe_sha256: str

    def alias_map(self) -> dict[str, str]:
        return {item.name: item.entity_id for item in self.aliases}

    def entity_map(self) -> dict[str, DocumentEntity]:
        return {
            entity.entity_id: entity
            for publication in self.publications
            for entity in publication.entities
        }


def _payloads() -> tuple[DocumentSourcePayload, ...]:
    design_v1 = DocumentSourcePayload(
        source_key="evidence-pack-design",
        title="Evidence Pack Design",
        version_label="v1",
        source_kind=DocumentSourceKind.MARKDOWN,
        authors=("Research Team",),
        tags=("design", "retrieval"),
        content=(
            "# Architecture\n\n"
            "EvidencePack is a bounded collection of source blocks with stable citations. "
            "It inherits project ACL from every source [12].\n\n"
            "Claim: EvidencePack preserves source ACL and stable citations [12].\n\n"
            "## Retrieval\n\n"
            "Exact labels route first; sparse narrative and graph parents expand only after "
            "the precise evidence unit is found.\n\n"
            "# Results\n\n"
            "Claim: Candidate improves Recall by 8 percent on NQ test split.\n\n"
            "| Model | Dataset | Split | Recall | Latency |\n"
            "|---|---|---|---:|---:|\n"
            "| Baseline | NQ | test | 0.78 | 41 ms |\n"
            "| Candidate | NQ | test | 0.86 | 49 ms |\n"
            "| Candidate | NQ | validation | 0.91 | 44 ms |\n\n"
            "Figure 2: Recall rises while latency remains below the 50 ms budget.\n\n"
            "Equation (1): score = sparse + lambda * graph; lambda=regularization weight\n\n"
            "# Limitations\n\n"
            "The copied paragraph appears in several reports and must retain provenance.\n\n"
            "REF [12] Evidence Retrieval Foundations | DOI:10.1000/evidence.12"
        ),
    )
    design_v2 = DocumentSourcePayload(
        source_key="evidence-pack-design",
        title="Evidence Pack Design",
        version_label="v2",
        source_kind=DocumentSourceKind.MARKDOWN,
        authors=("Research Team",),
        tags=("design", "retrieval", "rollback"),
        content=(
            "# Architecture\n\n"
            "EvidencePack is a bounded collection of source blocks with stable citations. "
            "It intersects project ACL before every parent expansion [12].\n\n"
            "Claim: EvidencePack preserves source ACL, generation and stable citations [12].\n\n"
            "## Retrieval\n\n"
            "Exact labels route first; calibrated sparse and graph candidates expand after "
            "the precise evidence unit is found.\n\n"
            "# Results\n\n"
            "Claim: Candidate improves Recall by 5 percent on NQ test split.\n\n"
            "| Model | Dataset | Split | Recall | Latency |\n"
            "|---|---|---|---:|---:|\n"
            "| Baseline | NQ | test | 0.81 | 39 ms |\n"
            "| Candidate | NQ | test | 0.86 | 47 ms |\n"
            "| Candidate | NQ | validation | 0.92 | 43 ms |\n\n"
            "Figure 2: Recall is unchanged for Candidate but the baseline improved.\n\n"
            "Equation (1): score = exact + sparse + lambda * graph; lambda=reviewed edge weight\n\n"
            "# Recovery\n\n"
            "Claim: A failed generation rolls back to the last known good index.\n\n"
            "# Limitations\n\n"
            "The copied paragraph appears in several reports and must retain provenance.\n\n"
            "REF [12] Evidence Retrieval Foundations | DOI:10.1000/evidence.12\n"
            "REF [15] Safe Index Publication | DOI:10.1000/index.15"
        ),
    )
    ablation_docx = DocumentSourcePayload(
        source_key="reranker-ablation-report",
        title="Reranker Ablation Report",
        version_label="v1.3",
        source_kind=DocumentSourceKind.DOCX,
        authors=("Evaluation Group",),
        tags=("ablation", "table"),
        content=(
            "# Method\n\n"
            "Three seeds share dataset ABC version 2 and the same candidate configuration.\n\n"
            "Claim: The three-seed mean F1 is 82.1 percent on ABC test.\n\n"
            "# Ablation Table\n\n"
            "| Variant | Dataset | Split | Seed | F1 | Note |\n"
            "|---|---|---|---:|---:|---|\n"
            "| Candidate/Large | ABC-v2 | test | 7 | 81.7 % | |\n"
            "| Candidate/Large | ABC-v2 | test | 11 | 82.1 % | * |\n"
            "| Candidate/Large | ABC-v2 | test | 19 | 82.5 % | |\n"
            "| Candidate/Large | ABC-v2 | validation | 11 | 84.9 % | wrong split |\n\n"
            "Figure 3: \n\n"
            "Formula (2): mean = sum(seed_value) / seed_count\n\n"
            "# Caveat\n\n"
            "Claim: The result does not establish causal improvement over every baseline.\n\n"
            "The copied paragraph appears in several reports and must retain provenance [21].\n\n"
            "REF [21] Statistical Reporting Guide | DOI:10.1000/stats.21"
        ),
    )
    scanned = DocumentSourcePayload(
        source_key="legacy-scanned-evaluation",
        title="Legacy Scanned Evaluation",
        version_label="v0.9",
        source_kind=DocumentSourceKind.PDF_SCANNED,
        authors=("Archive Team",),
        tags=("scanned", "ocr"),
        artifact_sha256="sha256:" + "3" * 64,
        pages=(
            DocumentSourcePage(
                page_number=1,
                text="",
                ocr_text=(
                    "# Archived Result\n\n"
                    "Claim: OCR suggests Recall 0.86 on an unspecified split.\n\n"
                    "The number 0.86 is low-confidence OCR and cannot validate current results."
                ),
            ),
            DocumentSourcePage(
                page_number=2,
                text="",
                ocr_text=(
                    "# Diagram\n\n"
                    "Figure 7: OCR-derived trend points upward, author caption unavailable.\n\n"
                    "Formula (7): legacy_score = observed / unknown_denominator\n\n"
                    "REF [12] Evidence Retrieval Foundations | DOI:10.1000/evidence.12"
                ),
            ),
        ),
    )
    html = DocumentSourcePayload(
        source_key="security-review",
        title="Security Review",
        version_label="v1",
        source_kind=DocumentSourceKind.HTML,
        authors=("Security Reviewer",),
        tags=("security", "acl"),
        content=(
            "<nav>Ignore navigation noise</nav><h1>Authorization</h1>"
            "<p>All document units inherit version ACL; reference enrichment never widens it.</p>"
            "<p>Claim: Unauthorized parent expansion is rejected before context assembly.</p>"
            "<script>exfiltrate()</script><h2>Review</h2>"
            "<p>The copied paragraph appears in several reports and must retain provenance [31].</p>"
            "<p>REF [31] Access Control Review | DOI:10.1000/acl.31</p>"
        ),
    )
    return design_v1, design_v2, ablation_docx, scanned, html


def _review(publication: DocumentPublication, publication_index: int) -> DocumentPublication:
    candidates = tuple(
        item
        for item in publication.entities
        if item.entity_type == DocumentEntityType.CLAIM_CANDIDATE
    )
    reviews = tuple(
        DocumentClaimReview(
            candidate_id=item.entity_id,
            decision=DocumentClaimReviewDecision.ACCEPT,
            reviewer_id="golden-reviewer",
            review_nonce=f"fixture-{publication_index}-{index}",
        )
        for index, item in enumerate(candidates, 1)
    )
    return review_claims(publication, reviews) if reviews else publication


def _publications() -> tuple[DocumentPublication, ...]:
    adapter = DocumentAdapterV2()
    return tuple(
        _review(
            adapter.parse_bytes(
                canonical_document_source_bytes(payload),
                project_id=DOCUMENT_GOLDEN_PROJECT_ID,
                acl_ref=DOCUMENT_GOLDEN_ACL_REF,
                generation_id=DOCUMENT_GOLDEN_GENERATION_ID,
            ),
            index,
        )
        for index, payload in enumerate(_payloads(), 1)
    )


def _find(
    publications: tuple[DocumentPublication, ...],
    *,
    source_key: str,
    version: str,
    entity_type: DocumentEntityType,
    contains: str = "",
    label: str = "",
    status: str = "",
    occurrence: int = 1,
) -> str:
    matches = [
        entity
        for publication in publications
        if publication.family.source_key == source_key
        and publication.version.version_label == version
        for entity in publication.entities
        if entity.entity_type == entity_type
        and (not contains or contains.casefold() in entity.source_text.casefold())
        and (not label or (entity.label or "").casefold() == label.casefold())
        and (not status or entity.status == status)
    ]
    if len(matches) < occurrence:
        raise AssertionError(
            f"fixture alias not found: {source_key=} {version=} {entity_type=} "
            f"{contains=} {label=} {status=} {occurrence=}"
        )
    return matches[occurrence - 1].entity_id


def _aliases(publications: tuple[DocumentPublication, ...]) -> tuple[DocumentFixtureAlias, ...]:
    specs = {
        "design_v1_architecture": dict(
            source_key="evidence-pack-design",
            version="v1",
            entity_type=DocumentEntityType.SECTION,
            label="Architecture",
        ),
        "design_v2_architecture": dict(
            source_key="evidence-pack-design",
            version="v2",
            entity_type=DocumentEntityType.SECTION,
            label="Architecture",
        ),
        "design_v1_acl_paragraph": dict(
            source_key="evidence-pack-design",
            version="v1",
            entity_type=DocumentEntityType.PARAGRAPH,
            contains="inherits project ACL",
        ),
        "design_v2_acl_paragraph": dict(
            source_key="evidence-pack-design",
            version="v2",
            entity_type=DocumentEntityType.PARAGRAPH,
            contains="intersects project ACL",
        ),
        "design_v1_recall_claim": dict(
            source_key="evidence-pack-design",
            version="v1",
            entity_type=DocumentEntityType.CLAIM,
            contains="8 percent",
            status="accepted",
        ),
        "design_v2_recall_claim": dict(
            source_key="evidence-pack-design",
            version="v2",
            entity_type=DocumentEntityType.CLAIM,
            contains="5 percent",
            status="accepted",
        ),
        "design_v2_rollback_claim": dict(
            source_key="evidence-pack-design",
            version="v2",
            entity_type=DocumentEntityType.CLAIM,
            contains="rolls back",
            status="accepted",
        ),
        "design_v1_candidate_recall": dict(
            source_key="evidence-pack-design",
            version="v1",
            entity_type=DocumentEntityType.TABLE_CELL_FACT,
            label="Candidate / Recall",
            occurrence=1,
        ),
        "design_v1_validation_recall": dict(
            source_key="evidence-pack-design",
            version="v1",
            entity_type=DocumentEntityType.TABLE_CELL_FACT,
            label="Candidate / Recall",
            occurrence=2,
        ),
        "design_v2_candidate_recall": dict(
            source_key="evidence-pack-design",
            version="v2",
            entity_type=DocumentEntityType.TABLE_CELL_FACT,
            label="Candidate / Recall",
            occurrence=1,
        ),
        "design_v1_figure2": dict(
            source_key="evidence-pack-design",
            version="v1",
            entity_type=DocumentEntityType.FIGURE,
            label="Figure 2",
        ),
        "design_v2_formula1": dict(
            source_key="evidence-pack-design",
            version="v2",
            entity_type=DocumentEntityType.FORMULA,
            label="Equation 1",
        ),
        "design_v2_citation12": dict(
            source_key="evidence-pack-design",
            version="v2",
            entity_type=DocumentEntityType.CITATION_MENTION,
            label="[12]",
            occurrence=1,
        ),
        "design_v2_reference12": dict(
            source_key="evidence-pack-design",
            version="v2",
            entity_type=DocumentEntityType.REFERENCE_WORK,
            label="12",
        ),
        "ablation_claim_mean": dict(
            source_key="reranker-ablation-report",
            version="v1.3",
            entity_type=DocumentEntityType.CLAIM,
            contains="three-seed mean",
            status="accepted",
        ),
        "ablation_seed11_f1": dict(
            source_key="reranker-ablation-report",
            version="v1.3",
            entity_type=DocumentEntityType.TABLE_CELL_FACT,
            label="Candidate/Large / F1",
            occurrence=2,
        ),
        "ablation_wrong_split_f1": dict(
            source_key="reranker-ablation-report",
            version="v1.3",
            entity_type=DocumentEntityType.TABLE_CELL_FACT,
            label="Candidate/Large / F1",
            occurrence=4,
        ),
        "ablation_figure3": dict(
            source_key="reranker-ablation-report",
            version="v1.3",
            entity_type=DocumentEntityType.FIGURE,
            label="Figure 3",
        ),
        "ablation_formula2": dict(
            source_key="reranker-ablation-report",
            version="v1.3",
            entity_type=DocumentEntityType.FORMULA,
            label="Equation 2",
        ),
        "scanned_ocr_claim": dict(
            source_key="legacy-scanned-evaluation",
            version="v0.9",
            entity_type=DocumentEntityType.CLAIM,
            contains="OCR suggests",
            status="accepted",
        ),
        "scanned_figure7": dict(
            source_key="legacy-scanned-evaluation",
            version="v0.9",
            entity_type=DocumentEntityType.FIGURE,
            label="Figure 7",
        ),
        "security_acl_paragraph": dict(
            source_key="security-review",
            version="v1",
            entity_type=DocumentEntityType.PARAGRAPH,
            contains="inherit version ACL",
        ),
        "security_claim": dict(
            source_key="security-review",
            version="v1",
            entity_type=DocumentEntityType.CLAIM,
            contains="Unauthorized parent",
            status="accepted",
        ),
        "security_reference31": dict(
            source_key="security-review",
            version="v1",
            entity_type=DocumentEntityType.REFERENCE_WORK,
            label="31",
        ),
    }
    return tuple(
        DocumentFixtureAlias(name=name, entity_id=_find(publications, **spec))
        for name, spec in sorted(specs.items())
    )


def build_document_fixture_v1() -> DocumentFixtureBundle:
    publications = _publications()
    aliases = _aliases(publications)
    recipe_payload = {
        "recipe_version": DOCUMENT_FIXTURE_RECIPE_VERSION,
        "dataset_id": DOCUMENT_GOLDEN_DATASET_ID,
        "dataset_version": DOCUMENT_GOLDEN_DATASET_VERSION,
        "publication_digests": [item.publication_sha256 for item in publications],
        "aliases": [item.model_dump(mode="json") for item in aliases],
    }
    return DocumentFixtureBundle(
        publications=publications,
        aliases=aliases,
        recipe_sha256=canonical_sha256(recipe_payload),
    )
