"""Released 50-case Scientific Document Golden and reviewed-row evaluator."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    DocumentEntity,
    DocumentEntityType,
    DocumentPublication,
    canonical_sha256,
)
from .fixture_v1 import (
    DOCUMENT_GOLDEN_DATASET_ID,
    DOCUMENT_GOLDEN_DATASET_VERSION,
    DocumentFixtureBundle,
    build_document_fixture_v1,
)

DOCUMENT_GOLDEN_CASE_COUNT = 50
# Frozen after the programmatic fixture and case membership were independently rebuilt.
DOCUMENT_GOLDEN_PACKAGE_SHA256 = (
    "sha256:a0e3eba2692925d8c3afce1c9568dc61aa51bc956a2623f84e6bb61bc92cf6d0"
)
DOCUMENT_GOLDEN_AUTHORITY_SHA256 = (
    "sha256:48c3765e5daffba298144da9ef99ff49a950d428f32c78813a8ac50e1905f669"
)


class DocumentGoldenError(ValueError):
    """Raised when released truth or reviewed rows violate frozen authority."""


class DocumentGoldenSlice(StrEnum):
    LOCATION = "document_section_location"
    LOCAL_FACT = "local_fact"
    CLAIM = "claim_validation"
    TABLE = "table_numeric"
    FIGURE_FORMULA = "figure_formula"
    CITATION = "citation_resolution"
    SUMMARY = "global_summary"
    VERSION = "version_staleness"


EXPECTED_DOCUMENT_SLICE_COUNTS = {
    DocumentGoldenSlice.LOCATION: 8,
    DocumentGoldenSlice.LOCAL_FACT: 8,
    DocumentGoldenSlice.CLAIM: 10,
    DocumentGoldenSlice.TABLE: 10,
    DocumentGoldenSlice.FIGURE_FORMULA: 5,
    DocumentGoldenSlice.CITATION: 4,
    DocumentGoldenSlice.SUMMARY: 3,
    DocumentGoldenSlice.VERSION: 2,
}


class DocumentMetric(StrEnum):
    RETRIEVAL_RECALL_AT_10 = "retrieval_recall_at_10"
    LOCATOR_ACCURACY = "locator_accuracy"
    NDCG_AT_10 = "ndcg_at_10"
    MRR = "mrr"
    UNIT_TYPE_ACCURACY = "unit_type_accuracy"
    VERSION_ACCURACY = "version_accuracy"
    SECTION_COVERAGE = "section_coverage"
    CLAIM_VALIDATION_PRECISION = "claim_validation_precision"
    UNSUPPORTED_VERIFICATION_RATE = "unsupported_verification_rate"
    TABLE_HEADER_PATH_ACCURACY = "table_header_path_accuracy"
    NUMERIC_UNIT_ACCURACY = "numeric_unit_accuracy"
    FIGURE_FORMULA_GROUNDING = "figure_formula_grounding"
    CITATION_RESOLUTION_ACCURACY = "citation_resolution_accuracy"
    VERSION_ALIGNMENT_ACCURACY = "version_alignment_accuracy"
    HARD_NEGATIVE_AVOIDANCE = "hard_negative_avoidance"
    ABSTENTION_ACCURACY = "abstention_accuracy"


class DocumentMetricStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class DocumentReviewAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class _FrozenEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class DocumentGoldenCase(_FrozenEvaluation):
    case_id: str
    slice: DocumentGoldenSlice
    query: str
    expected_entity_ids: tuple[str, ...]
    expected_entity_types: tuple[DocumentEntityType, ...]
    hard_negative_entity_ids: tuple[str, ...] = ()
    eligible_metrics: tuple[DocumentMetric, ...]
    required_roles: tuple[str, ...]
    allowed_edge_types: tuple[str, ...] = ()
    expected_empty: bool = False

    @model_validator(mode="after")
    def _truth(self) -> DocumentGoldenCase:
        if len(set(self.expected_entity_ids)) != len(self.expected_entity_ids):
            raise ValueError("positive membership must be unique")
        if len(set(self.hard_negative_entity_ids)) != len(self.hard_negative_entity_ids):
            raise ValueError("hard-negative membership must be unique")
        if set(self.expected_entity_ids) & set(self.hard_negative_entity_ids):
            raise ValueError("positive and hard-negative membership overlap")
        if self.expected_empty != (not self.expected_entity_ids):
            raise ValueError("expected_empty must match positive membership")
        if len(set(self.eligible_metrics)) != len(self.eligible_metrics):
            raise ValueError("eligible metric membership must be unique")
        return self


class DocumentGoldenDataset(_FrozenEvaluation):
    dataset_id: str
    dataset_version: str
    package_sha256: str
    authority_sha256: str
    fixture_recipe_sha256: str
    publications: tuple[DocumentPublication, ...]
    cases: tuple[DocumentGoldenCase, ...]

    def package_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"package_sha256", "authority_sha256"},
        )

    def entity_map(self) -> dict[str, DocumentEntity]:
        return {
            entity.entity_id: entity
            for publication in self.publications
            for entity in publication.entities
        }

    @model_validator(mode="after")
    def _released_authority(self) -> DocumentGoldenDataset:
        if (
            self.dataset_id != DOCUMENT_GOLDEN_DATASET_ID
            or self.dataset_version != DOCUMENT_GOLDEN_DATASET_VERSION
        ):
            raise ValueError("unexpected Document Golden identity")
        if len(self.cases) != DOCUMENT_GOLDEN_CASE_COUNT:
            raise ValueError("Document Golden must contain exactly 50 cases")
        expected_ids = tuple(f"doc-v1-{index:03d}" for index in range(1, 51))
        if tuple(case.case_id for case in self.cases) != expected_ids:
            raise ValueError("Document Golden case membership/order is not canonical")
        if Counter(case.slice for case in self.cases) != Counter(EXPECTED_DOCUMENT_SLICE_COUNTS):
            raise ValueError("Document Golden slice counts are not canonical")
        entities = self.entity_map()
        if sum(len(item.entities) for item in self.publications) != len(entities):
            raise ValueError("Document Golden entity identities are not globally unique")
        for case in self.cases:
            if not set(case.expected_entity_ids).issubset(entities):
                raise ValueError("positive identity is outside released entity authority")
            if not set(case.hard_negative_entity_ids).issubset(entities):
                raise ValueError("hard negative is outside released entity authority")
            if case.expected_entity_ids and set(case.expected_entity_types) != {
                entities[item].entity_type for item in case.expected_entity_ids
            }:
                raise ValueError("expected entity types do not match frozen identities")
        expected_package = canonical_sha256(self.package_payload())
        if self.package_sha256 != expected_package:
            raise ValueError("Document Golden package digest mismatch")
        expected_authority = canonical_sha256(
            {
                "dataset_id": self.dataset_id,
                "dataset_version": self.dataset_version,
                "package_sha256": self.package_sha256,
                "case_membership": expected_ids,
                "slice_counts": {
                    key.value: value for key, value in EXPECTED_DOCUMENT_SLICE_COUNTS.items()
                },
                "publication_digests": [item.publication_sha256 for item in self.publications],
                "entity_ids": sorted(entities),
            }
        )
        if self.authority_sha256 != expected_authority:
            raise ValueError("Document Golden authority digest mismatch")
        if DOCUMENT_GOLDEN_PACKAGE_SHA256 and (
            self.package_sha256 != DOCUMENT_GOLDEN_PACKAGE_SHA256
        ):
            raise ValueError("Document Golden differs from released package")
        if DOCUMENT_GOLDEN_AUTHORITY_SHA256 and (
            self.authority_sha256 != DOCUMENT_GOLDEN_AUTHORITY_SHA256
        ):
            raise ValueError("Document Golden differs from released authority")
        return self


class DocumentReviewedCandidate(_FrozenEvaluation):
    entity_id: str
    locator: str


class DocumentReviewedRow(_FrozenEvaluation):
    case_id: str
    availability: DocumentReviewAvailability
    candidates: tuple[DocumentReviewedCandidate, ...] = Field(
        default=(),
        max_length=10,
    )
    diagnostic: str | None = None

    @model_validator(mode="after")
    def _availability(self) -> DocumentReviewedRow:
        if self.availability != DocumentReviewAvailability.AVAILABLE and self.candidates:
            raise ValueError("unavailable/error row cannot include candidates")
        if len({item.entity_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("reviewed candidate membership must be unique")
        return self


class DocumentMetricResult(_FrozenEvaluation):
    metric: DocumentMetric
    status: DocumentMetricStatus
    numerator: float
    eligible_denominator: int
    evaluated_denominator: int
    unavailable_count: int
    value: float | None


class DocumentSliceResult(_FrozenEvaluation):
    slice: DocumentGoldenSlice
    status: DocumentMetricStatus
    numerator: int
    eligible_denominator: int
    evaluated_denominator: int
    unavailable_count: int
    value: float | None


class DocumentEvaluationReport(_FrozenEvaluation):
    dataset_id: str
    dataset_version: str
    package_sha256: str
    authority_sha256: str
    case_count: int
    reviewed_row_count: int
    hard_negative_case_count: int
    metrics: tuple[DocumentMetricResult, ...]
    slices: tuple[DocumentSliceResult, ...]


def _base_metrics(*extra: DocumentMetric) -> tuple[DocumentMetric, ...]:
    return (
        DocumentMetric.RETRIEVAL_RECALL_AT_10,
        DocumentMetric.LOCATOR_ACCURACY,
        DocumentMetric.NDCG_AT_10,
        DocumentMetric.MRR,
        DocumentMetric.UNIT_TYPE_ACCURACY,
        DocumentMetric.VERSION_ACCURACY,
        DocumentMetric.HARD_NEGATIVE_AVOIDANCE,
        *extra,
    )


def _select(
    bundle: DocumentFixtureBundle,
    *,
    source_key: str,
    version: str,
    entity_type: DocumentEntityType,
    contains: str = "",
    label: str = "",
    status: str = "",
    occurrence: int = 1,
) -> DocumentEntity:
    matches = [
        entity
        for publication in bundle.publications
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
            f"missing Golden selection {source_key=} {version=} {entity_type=} "
            f"{contains=} {label=} {status=} {occurrence=}"
        )
    return matches[occurrence - 1]


def _case(
    index: int,
    slice_: DocumentGoldenSlice,
    query: str,
    expected: tuple[DocumentEntity, ...],
    hard: tuple[DocumentEntity, ...],
    metrics: tuple[DocumentMetric, ...],
    roles: tuple[str, ...],
    edges: tuple[str, ...] = (),
) -> DocumentGoldenCase:
    return DocumentGoldenCase(
        case_id=f"doc-v1-{index:03d}",
        slice=slice_,
        query=query,
        expected_entity_ids=tuple(item.entity_id for item in expected),
        expected_entity_types=tuple(sorted({item.entity_type for item in expected})),
        hard_negative_entity_ids=tuple(item.entity_id for item in hard),
        eligible_metrics=metrics,
        required_roles=roles,
        allowed_edge_types=edges,
        expected_empty=not expected,
    )


def _build_cases(bundle: DocumentFixtureBundle) -> tuple[DocumentGoldenCase, ...]:
    def s(**kwargs: Any) -> tuple[DocumentEntity, ...]:
        return _select(bundle, **kwargs)

    design = "evidence-pack-design"
    ablation = "reranker-ablation-report"
    scanned = "legacy-scanned-evaluation"
    security = "security-review"
    section = DocumentEntityType.SECTION
    paragraph = DocumentEntityType.PARAGRAPH
    claim = DocumentEntityType.CLAIM
    candidate = DocumentEntityType.CLAIM_CANDIDATE
    cell = DocumentEntityType.TABLE_CELL_FACT
    figure = DocumentEntityType.FIGURE
    formula = DocumentEntityType.FORMULA
    citation = DocumentEntityType.CITATION_MENTION
    reference = DocumentEntityType.REFERENCE_WORK
    summary = DocumentEntityType.SECTION_SUMMARY

    cases: list[DocumentGoldenCase] = []

    def add(
        slice_: DocumentGoldenSlice,
        query: str,
        expected: tuple[DocumentEntity, ...],
        hard: tuple[DocumentEntity, ...] = (),
        metrics: tuple[DocumentMetric, ...] = (),
        roles: tuple[str, ...] = ("source",),
        edges: tuple[str, ...] = (),
    ) -> None:
        cases.append(
            _case(
                len(cases) + 1,
                slice_,
                query,
                expected,
                hard,
                _base_metrics(*metrics),
                roles,
                edges,
            )
        )

    # 8 document/section location cases.
    add(
        DocumentGoldenSlice.LOCATION,
        "EvidencePack architecture section in v2",
        (s(source_key=design, version="v2", entity_type=section, label="Architecture"),),
        (s(source_key=design, version="v1", entity_type=section, label="Architecture"),),
    )
    add(
        DocumentGoldenSlice.LOCATION,
        "where is failed generation recovery defined",
        (s(source_key=design, version="v2", entity_type=section, label="Recovery"),),
    )
    add(
        DocumentGoldenSlice.LOCATION,
        "v1 Results section",
        (s(source_key=design, version="v1", entity_type=section, label="Results"),),
        (s(source_key=design, version="v2", entity_type=section, label="Results"),),
    )
    add(
        DocumentGoldenSlice.LOCATION,
        "Reranker Ablation Table section",
        (s(source_key=ablation, version="v1.3", entity_type=section, label="Ablation Table"),),
    )
    add(
        DocumentGoldenSlice.LOCATION,
        "legacy scanned Diagram section",
        (s(source_key=scanned, version="v0.9", entity_type=section, label="Diagram"),),
    )
    add(
        DocumentGoldenSlice.LOCATION,
        "Security Review Authorization section",
        (s(source_key=security, version="v1", entity_type=section, label="Authorization"),),
    )
    add(
        DocumentGoldenSlice.LOCATION,
        "v2 Retrieval subsection",
        (s(source_key=design, version="v2", entity_type=section, label="Retrieval"),),
    )
    add(
        DocumentGoldenSlice.LOCATION,
        "v1 Limitations section only",
        (s(source_key=design, version="v1", entity_type=section, label="Limitations"),),
        (s(source_key=design, version="v2", entity_type=section, label="Limitations"),),
    )

    # 8 local authored fact cases.
    add(
        DocumentGoldenSlice.LOCAL_FACT,
        "How does v2 apply project ACL before parent expansion?",
        (
            s(
                source_key=design,
                version="v2",
                entity_type=paragraph,
                contains="intersects project ACL",
            ),
        ),
        (
            s(
                source_key=design,
                version="v1",
                entity_type=paragraph,
                contains="inherits project ACL",
            ),
        ),
    )
    add(
        DocumentGoldenSlice.LOCAL_FACT,
        "What retrieval channels are calibrated in v2?",
        (s(source_key=design, version="v2", entity_type=paragraph, contains="calibrated sparse"),),
    )
    add(
        DocumentGoldenSlice.LOCAL_FACT,
        "What copied paragraph limitation appears in v1?",
        (s(source_key=design, version="v1", entity_type=paragraph, contains="copied paragraph"),),
        (s(source_key=security, version="v1", entity_type=paragraph, contains="copied paragraph"),),
    )
    add(
        DocumentGoldenSlice.LOCAL_FACT,
        "Which dataset and seeds are stated in the ablation Method paragraph?",
        (s(source_key=ablation, version="v1.3", entity_type=paragraph, contains="Three seeds"),),
    )
    add(
        DocumentGoldenSlice.LOCAL_FACT,
        "Why does the scanned 0.86 paragraph not validate current results?",
        (
            s(
                source_key=scanned,
                version="v0.9",
                entity_type=paragraph,
                contains="low-confidence OCR",
            ),
        ),
    )
    add(
        DocumentGoldenSlice.LOCAL_FACT,
        "Does metadata enrichment widen document ACL?",
        (
            s(
                source_key=security,
                version="v1",
                entity_type=paragraph,
                contains="reference enrichment",
            ),
        ),
    )
    add(
        DocumentGoldenSlice.LOCAL_FACT,
        "What happens after a failed generation?",
        (s(source_key=design, version="v2", entity_type=paragraph, contains="rolls back"),),
    )
    add(
        DocumentGoldenSlice.LOCAL_FACT,
        "copied paragraph provenance in Security Review",
        (s(source_key=security, version="v1", entity_type=paragraph, contains="copied paragraph"),),
        (s(source_key=design, version="v1", entity_type=paragraph, contains="copied paragraph"),),
    )

    # 10 claim/validation cases.
    claim_specs = [
        (
            "Does EvidencePack v2 preserve ACL generation and citations?",
            design,
            "v2",
            "preserves source ACL",
            "",
        ),
        ("What recall improvement did v1 report?", design, "v1", "8 percent", ""),
        ("What recall improvement did v2 report?", design, "v2", "5 percent", ""),
        ("What rollback claim was accepted in v2?", design, "v2", "rolls back", ""),
        ("What three-seed mean claim was accepted?", ablation, "v1.3", "three-seed mean", ""),
        (
            "What causal limitation does the ablation report state?",
            ablation,
            "v1.3",
            "does not establish",
            "",
        ),
        (
            "What unauthorized expansion claim passed review?",
            security,
            "v1",
            "Unauthorized parent",
            "",
        ),
    ]
    for query, source_key, version, contains, _ in claim_specs:
        accepted = s(
            source_key=source_key,
            version=version,
            entity_type=claim,
            contains=contains,
            status="accepted",
        )
        parser_candidate = s(
            source_key=source_key,
            version=version,
            entity_type=candidate,
            contains=contains,
        )
        add(
            DocumentGoldenSlice.CLAIM,
            query,
            (accepted,),
            (parser_candidate,),
            (
                DocumentMetric.CLAIM_VALIDATION_PRECISION,
                DocumentMetric.UNSUPPORTED_VERIFICATION_RATE,
            ),
            ("atomic_claim", "source_span", "review"),
            ("reports", "supports"),
        )
    add(
        DocumentGoldenSlice.CLAIM,
        "Is OCR Recall 0.86 verified for the current NQ test split?",
        (),
        (s(source_key=scanned, version="v0.9", entity_type=claim, contains="OCR suggests"),),
        (
            DocumentMetric.CLAIM_VALIDATION_PRECISION,
            DocumentMetric.UNSUPPORTED_VERIFICATION_RATE,
            DocumentMetric.ABSTENTION_ACCURACY,
        ),
        ("refusal", "missing_design_identity"),
    )
    add(
        DocumentGoldenSlice.CLAIM,
        "Which current reviewed claim, not the scanned copy, reports Recall 0.86?",
        (s(source_key=design, version="v2", entity_type=claim, contains="5 percent"),),
        (s(source_key=scanned, version="v0.9", entity_type=claim, contains="OCR suggests"),),
        (
            DocumentMetric.CLAIM_VALIDATION_PRECISION,
            DocumentMetric.UNSUPPORTED_VERIFICATION_RATE,
        ),
        ("atomic_claim", "version"),
    )
    accepted_acl = s(
        source_key=design,
        version="v2",
        entity_type=claim,
        contains="preserves source ACL",
    )
    candidate_acl = s(
        source_key=design,
        version="v2",
        entity_type=candidate,
        contains="preserves source ACL",
    )
    add(
        DocumentGoldenSlice.CLAIM,
        "Return the reviewed ACL claim, never the parser candidate",
        (accepted_acl,),
        (candidate_acl,),
        (
            DocumentMetric.CLAIM_VALIDATION_PRECISION,
            DocumentMetric.UNSUPPORTED_VERIFICATION_RATE,
        ),
        ("reviewed_claim",),
    )

    # 10 table/numeric cases.
    table_queries = [
        ("v1 Candidate NQ test Recall", design, "v1", "Candidate / Recall", 1),
        ("v1 Candidate NQ validation Recall", design, "v1", "Candidate / Recall", 2),
        ("v2 Baseline NQ test Recall", design, "v2", "Baseline / Recall", 1),
        ("v2 Candidate NQ test Latency", design, "v2", "Candidate / Latency", 1),
        ("ABC test seed 7 F1", ablation, "v1.3", "Candidate/Large / F1", 1),
        ("ABC test seed 11 F1", ablation, "v1.3", "Candidate/Large / F1", 2),
        ("ABC test seed 19 F1", ablation, "v1.3", "Candidate/Large / F1", 3),
        ("ABC validation seed 11 F1", ablation, "v1.3", "Candidate/Large / F1", 4),
        (
            "Which cell contains 82.1 percent plus footnote?",
            ablation,
            "v1.3",
            "Candidate/Large / F1",
            2,
        ),
    ]
    for query, source_key, version, label, occurrence in table_queries:
        target = s(
            source_key=source_key,
            version=version,
            entity_type=cell,
            label=label,
            occurrence=occurrence,
        )
        hard = ()
        if source_key == design and label == "Candidate / Recall":
            other = 2 if occurrence == 1 else 1
            hard = (
                s(
                    source_key=source_key,
                    version=version,
                    entity_type=cell,
                    label=label,
                    occurrence=other,
                ),
            )
        add(
            DocumentGoldenSlice.TABLE,
            query,
            (target,),
            hard,
            (
                DocumentMetric.TABLE_HEADER_PATH_ACCURACY,
                DocumentMetric.NUMERIC_UNIT_ACCURACY,
            ),
            ("table", "row_path", "column_path", "typed_value"),
            ("has_table", "has_row", "has_cell_fact"),
        )
    three_seed = tuple(
        s(
            source_key=ablation,
            version="v1.3",
            entity_type=cell,
            label="Candidate/Large / F1",
            occurrence=index,
        )
        for index in (1, 2, 3)
    )
    add(
        DocumentGoldenSlice.TABLE,
        "Recompute ABC test mean F1 from three seeds",
        three_seed,
        (
            s(
                source_key=ablation,
                version="v1.3",
                entity_type=cell,
                label="Candidate/Large / F1",
                occurrence=4,
            ),
        ),
        (
            DocumentMetric.TABLE_HEADER_PATH_ACCURACY,
            DocumentMetric.NUMERIC_UNIT_ACCURACY,
        ),
        ("three_seed_cells", "unit", "split"),
    )

    # 5 figure/formula cases.
    add(
        DocumentGoldenSlice.FIGURE_FORMULA,
        "What trend does Figure 2 in v1 show?",
        (s(source_key=design, version="v1", entity_type=figure, label="Figure 2"),),
        (s(source_key=design, version="v2", entity_type=figure, label="Figure 2"),),
        (DocumentMetric.FIGURE_FORMULA_GROUNDING,),
        ("author_caption",),
    )
    add(
        DocumentGoldenSlice.FIGURE_FORMULA,
        "What trend does Figure 2 in v2 show?",
        (s(source_key=design, version="v2", entity_type=figure, label="Figure 2"),),
        (s(source_key=design, version="v1", entity_type=figure, label="Figure 2"),),
        (DocumentMetric.FIGURE_FORMULA_GROUNDING,),
        ("author_caption",),
    )
    add(
        DocumentGoldenSlice.FIGURE_FORMULA,
        "What does the author caption of Figure 3 say?",
        (),
        (s(source_key=ablation, version="v1.3", entity_type=figure, label="Figure 3"),),
        (DocumentMetric.FIGURE_FORMULA_GROUNDING, DocumentMetric.ABSTENTION_ACCURACY),
        ("missing_author_caption",),
    )
    add(
        DocumentGoldenSlice.FIGURE_FORMULA,
        "How is mean defined in Formula 2?",
        (s(source_key=ablation, version="v1.3", entity_type=formula, label="Equation 2"),),
        (),
        (DocumentMetric.FIGURE_FORMULA_GROUNDING,),
        ("formula", "neighbor"),
    )
    add(
        DocumentGoldenSlice.FIGURE_FORMULA,
        "What is the provenance of scanned Figure 7?",
        (s(source_key=scanned, version="v0.9", entity_type=figure, label="Figure 7"),),
        (),
        (DocumentMetric.FIGURE_FORMULA_GROUNDING,),
        ("ocr_derived", "artifact"),
    )

    # 4 citation resolution cases.
    citation_cases = [
        ("Resolve [12] in v2 ACL claim", design, "v2", "12", 1),
        ("Resolve [31] in Security Review", security, "v1", "31", 1),
        ("Which work has DOI 10.1000/stats.21?", ablation, "v1.3", "21", 1),
    ]
    for query, source_key, version, marker, occurrence in citation_cases:
        mention = s(
            source_key=source_key,
            version=version,
            entity_type=citation,
            label=f"[{marker}]",
            occurrence=occurrence,
        )
        work = s(
            source_key=source_key,
            version=version,
            entity_type=reference,
            label=marker,
        )
        expected = (work,) if "DOI" in query else (mention, work)
        add(
            DocumentGoldenSlice.CITATION,
            query,
            expected,
            (),
            (DocumentMetric.CITATION_RESOLUTION_ACCURACY,),
            ("mention_context", "reference_work"),
            ("cites", "resolves_to"),
        )
    first = s(
        source_key=design,
        version="v1",
        entity_type=citation,
        label="[12]",
        occurrence=1,
    )
    repeated = s(
        source_key=design,
        version="v1",
        entity_type=citation,
        label="[12]",
        occurrence=2,
    )
    add(
        DocumentGoldenSlice.CITATION,
        "Resolve the first [12] mention in EvidencePack v1, not the reference-list echo",
        (first,),
        (repeated,),
        (DocumentMetric.CITATION_RESOLUTION_ACCURACY,),
        ("mention_context",),
    )

    # 3 structure-aware summary cases.
    add(
        DocumentGoldenSlice.SUMMARY,
        "Summarize v2 architecture results and recovery with section coverage",
        tuple(
            s(
                source_key=design,
                version="v2",
                entity_type=summary,
                occurrence=index,
            )
            for index in (1, 3, 4)
        ),
        (),
        (DocumentMetric.SECTION_COVERAGE,),
        ("section_summary", "source_backing"),
    )
    add(
        DocumentGoldenSlice.SUMMARY,
        "Summarize ablation method table and caveat",
        tuple(
            s(
                source_key=ablation,
                version="v1.3",
                entity_type=summary,
                occurrence=index,
            )
            for index in (1, 2, 3)
        ),
        (),
        (DocumentMetric.SECTION_COVERAGE,),
        ("section_summary", "source_backing"),
    )
    add(
        DocumentGoldenSlice.SUMMARY,
        "Summarize copied-paragraph provenance across reports",
        (
            s(source_key=design, version="v2", entity_type=paragraph, contains="copied paragraph"),
            s(
                source_key=ablation,
                version="v1.3",
                entity_type=paragraph,
                contains="copied paragraph",
            ),
            s(
                source_key=security,
                version="v1",
                entity_type=paragraph,
                contains="copied paragraph",
            ),
        ),
        (),
        (DocumentMetric.SECTION_COVERAGE,),
        ("provenance", "near_duplicate"),
    )

    # 2 version/staleness cases.
    old_claim = s(source_key=design, version="v1", entity_type=claim, contains="8 percent")
    new_claim = s(source_key=design, version="v2", entity_type=claim, contains="5 percent")
    add(
        DocumentGoldenSlice.VERSION,
        "Compare v1 and v2 Recall improvement claims",
        (old_claim, new_claim),
        (),
        (DocumentMetric.VERSION_ALIGNMENT_ACCURACY,),
        ("old_version", "new_version", "modified_claim"),
        ("same_logical_entity", "modified"),
    )
    add(
        DocumentGoldenSlice.VERSION,
        "Which old Recall claim became potentially stale after v2?",
        (old_claim,),
        (new_claim,),
        (DocumentMetric.VERSION_ALIGNMENT_ACCURACY,),
        ("stale_claim", "superseding_version"),
        ("supersedes", "modified"),
    )

    if len(cases) != 50:
        raise AssertionError(f"Document Golden case count is {len(cases)}, expected 50")
    return tuple(cases)


def build_document_golden_v1() -> DocumentGoldenDataset:
    bundle = build_document_fixture_v1()
    cases = _build_cases(bundle)
    base = {
        "dataset_id": DOCUMENT_GOLDEN_DATASET_ID,
        "dataset_version": DOCUMENT_GOLDEN_DATASET_VERSION,
        "fixture_recipe_sha256": bundle.recipe_sha256,
        "publications": bundle.publications,
        "cases": cases,
    }
    package = canonical_sha256(
        {
            "dataset_id": base["dataset_id"],
            "dataset_version": base["dataset_version"],
            "fixture_recipe_sha256": base["fixture_recipe_sha256"],
            "publications": [item.model_dump(mode="json") for item in bundle.publications],
            "cases": [item.model_dump(mode="json") for item in cases],
        }
    )
    entity_ids = sorted(
        entity.entity_id for publication in bundle.publications for entity in publication.entities
    )
    authority = canonical_sha256(
        {
            "dataset_id": DOCUMENT_GOLDEN_DATASET_ID,
            "dataset_version": DOCUMENT_GOLDEN_DATASET_VERSION,
            "package_sha256": package,
            "case_membership": tuple(f"doc-v1-{index:03d}" for index in range(1, 51)),
            "slice_counts": {
                key.value: value for key, value in EXPECTED_DOCUMENT_SLICE_COUNTS.items()
            },
            "publication_digests": [item.publication_sha256 for item in bundle.publications],
            "entity_ids": entity_ids,
        }
    )
    return DocumentGoldenDataset(
        **base,
        package_sha256=package,
        authority_sha256=authority,
    )


def _metric_result(
    metric: DocumentMetric,
    *,
    numerator: float,
    eligible: int,
    evaluated: int,
) -> DocumentMetricResult:
    unavailable = eligible - evaluated
    status = (
        DocumentMetricStatus.UNAVAILABLE
        if evaluated == 0
        else DocumentMetricStatus.PROVISIONAL
        if unavailable
        else DocumentMetricStatus.AVAILABLE
    )
    return DocumentMetricResult(
        metric=metric,
        status=status,
        numerator=numerator,
        eligible_denominator=eligible,
        evaluated_denominator=evaluated,
        unavailable_count=unavailable,
        value=None if evaluated == 0 else numerator / evaluated,
    )


def evaluate_reviewed_document_retrieval(
    rows: tuple[DocumentReviewedRow, ...],
    *,
    dataset: DocumentGoldenDataset | None = None,
    case_membership: tuple[str, ...] | None = None,
) -> DocumentEvaluationReport:
    dataset = dataset or build_document_golden_v1()
    cases = {case.case_id: case for case in dataset.cases}
    expected_membership = tuple(case.case_id for case in dataset.cases)
    membership = expected_membership if case_membership is None else case_membership
    if (
        not membership
        or membership != expected_membership
        or len(set(membership)) != len(membership)
    ):
        raise DocumentGoldenError("review membership must be the complete canonical 50 cases")
    if tuple(row.case_id for row in rows) != membership:
        raise DocumentGoldenError("review rows must exactly match canonical case order")
    entity_map = dataset.entity_map()
    metric_eligible: Counter[DocumentMetric] = Counter()
    metric_evaluated: Counter[DocumentMetric] = Counter()
    metric_numerator: defaultdict[DocumentMetric, float] = defaultdict(float)
    slice_eligible: Counter[DocumentGoldenSlice] = Counter()
    slice_evaluated: Counter[DocumentGoldenSlice] = Counter()
    slice_numerator: Counter[DocumentGoldenSlice] = Counter()

    for row in rows:
        case = cases[row.case_id]
        slice_eligible[case.slice] += 1
        for metric in case.eligible_metrics:
            metric_eligible[metric] += 1
        if row.availability != DocumentReviewAvailability.AVAILABLE:
            continue
        slice_evaluated[case.slice] += 1
        candidates: list[DocumentEntity] = []
        for reviewed in row.candidates:
            entity = entity_map.get(reviewed.entity_id)
            if entity is None or entity.locator != reviewed.locator:
                raise DocumentGoldenError("reviewed candidate identity/locator is not released")
            candidates.append(entity)
        candidate_ids = tuple(item.entity_id for item in candidates)
        positions = [
            candidate_ids.index(item) + 1
            for item in case.expected_entity_ids
            if item in candidate_ids
        ]
        full_hit = (
            not candidate_ids
            if case.expected_empty
            else set(case.expected_entity_ids).issubset(candidate_ids)
        )
        hard_safe = not set(case.hard_negative_entity_ids) & set(candidate_ids)
        if full_hit:
            slice_numerator[case.slice] += 1
        for metric in case.eligible_metrics:
            metric_evaluated[metric] += 1
            success = 0.0
            if metric in {
                DocumentMetric.RETRIEVAL_RECALL_AT_10,
                DocumentMetric.LOCATOR_ACCURACY,
                DocumentMetric.UNIT_TYPE_ACCURACY,
                DocumentMetric.VERSION_ACCURACY,
                DocumentMetric.SECTION_COVERAGE,
                DocumentMetric.CLAIM_VALIDATION_PRECISION,
                DocumentMetric.TABLE_HEADER_PATH_ACCURACY,
                DocumentMetric.NUMERIC_UNIT_ACCURACY,
                DocumentMetric.FIGURE_FORMULA_GROUNDING,
                DocumentMetric.CITATION_RESOLUTION_ACCURACY,
                DocumentMetric.VERSION_ALIGNMENT_ACCURACY,
                DocumentMetric.ABSTENTION_ACCURACY,
            }:
                success = float(full_hit)
            elif metric == DocumentMetric.HARD_NEGATIVE_AVOIDANCE:
                success = float(hard_safe)
            elif metric == DocumentMetric.UNSUPPORTED_VERIFICATION_RATE:
                success = float(bool(set(case.hard_negative_entity_ids) & set(candidate_ids)))
            elif metric == DocumentMetric.MRR:
                success = 0.0 if not positions else 1.0 / min(positions)
            elif metric == DocumentMetric.NDCG_AT_10 and positions:
                import math

                dcg = sum(1.0 / math.log2(position + 1) for position in positions)
                ideal = sum(
                    1.0 / math.log2(position + 1)
                    for position in range(1, len(case.expected_entity_ids) + 1)
                )
                success = dcg / ideal
            metric_numerator[metric] += success

    metrics = tuple(
        _metric_result(
            metric,
            numerator=metric_numerator[metric],
            eligible=metric_eligible[metric],
            evaluated=metric_evaluated[metric],
        )
        for metric in DocumentMetric
        if metric_eligible[metric]
    )
    slices = tuple(
        DocumentSliceResult(
            slice=slice_,
            status=(
                DocumentMetricStatus.UNAVAILABLE
                if slice_evaluated[slice_] == 0
                else DocumentMetricStatus.PROVISIONAL
                if slice_evaluated[slice_] < slice_eligible[slice_]
                else DocumentMetricStatus.AVAILABLE
            ),
            numerator=slice_numerator[slice_],
            eligible_denominator=slice_eligible[slice_],
            evaluated_denominator=slice_evaluated[slice_],
            unavailable_count=slice_eligible[slice_] - slice_evaluated[slice_],
            value=(
                None
                if slice_evaluated[slice_] == 0
                else slice_numerator[slice_] / slice_evaluated[slice_]
            ),
        )
        for slice_ in DocumentGoldenSlice
    )
    return DocumentEvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        package_sha256=dataset.package_sha256,
        authority_sha256=dataset.authority_sha256,
        case_count=len(dataset.cases),
        reviewed_row_count=len(rows),
        hard_negative_case_count=sum(bool(case.hard_negative_entity_ids) for case in dataset.cases),
        metrics=metrics,
        slices=slices,
    )
