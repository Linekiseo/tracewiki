"""Released 120-case Wiki navigation Golden and authority-owned evaluator."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts_v1 import (
    WIKI_CONTRACT_VERSION,
    WikiSourceDomainV1,
    canonical_sha256_v1,
)
from .paths_v1 import validate_wiki_logical_path_v1

WIKI_GOLDEN_DATASET_ID = "agent-native-wiki-golden-v1"
WIKI_GOLDEN_DATASET_VERSION = "v1"
WIKI_GOLDEN_CASE_COUNT = 120
WIKI_EVALUATOR_VERSION = "wiki-reviewed-navigation-evaluator-v1"

# Reviewed constants.  They are generated once from the deterministic recipe and then
# pinned here; runtime code never learns an expected authority from the input under test.
WIKI_GOLDEN_PACKAGE_SHA256 = (
    "sha256:fb53aaed5318b4372354972a4a75b386689de2b0629446c13b3220436ffc874b"
)
WIKI_GOLDEN_AUTHORITY_SHA256 = (
    "sha256:9240346e72d969e5eac294053a2173b88efaa0a1ef40fc057ad5053a5f09d166"
)


class WikiGoldenError(ValueError):
    """Raised when released Wiki truth or reviewed observations are invalid."""


class WikiGoldenSliceV1(StrEnum):
    LOCAL_RAW_DETAIL = "local_raw_detail"
    BRIDGE_2HOP = "bridge_2hop"
    HIGH_FAN_IN = "high_fan_in"
    COMPARE_CAUSAL = "compare_causal"
    TEMPORAL_STALE = "temporal_stale"
    GLOBAL_AGGREGATE = "global_aggregate"
    EXPLORATORY = "exploratory"
    UNANSWERABLE = "unanswerable"
    ACL_SECURITY = "acl_security"
    INCREMENTAL_CONTRADICTION = "incremental_contradiction"


EXPECTED_WIKI_SLICE_COUNTS: dict[WikiGoldenSliceV1, int] = {
    WikiGoldenSliceV1.LOCAL_RAW_DETAIL: 15,
    WikiGoldenSliceV1.BRIDGE_2HOP: 20,
    WikiGoldenSliceV1.HIGH_FAN_IN: 15,
    WikiGoldenSliceV1.COMPARE_CAUSAL: 15,
    WikiGoldenSliceV1.TEMPORAL_STALE: 10,
    WikiGoldenSliceV1.GLOBAL_AGGREGATE: 10,
    WikiGoldenSliceV1.EXPLORATORY: 10,
    WikiGoldenSliceV1.UNANSWERABLE: 10,
    WikiGoldenSliceV1.ACL_SECURITY: 10,
    WikiGoldenSliceV1.INCREMENTAL_CONTRADICTION: 5,
}


class WikiMetricV1(StrEnum):
    PATH_RECALL = "path_recall"
    FULL_EVIDENCE = "full_evidence"
    OBLIGATION_COMPLETION = "obligation_completion"
    RAW_CITATION_BINDING = "raw_citation_binding"
    PREMATURE_STOP_AVOIDANCE = "premature_stop_avoidance"
    CORRECT_REFUSAL = "correct_refusal"
    ACL_SAFETY = "acl_safety"
    UNSUPPORTED_CLAIM_SAFETY = "unsupported_claim_safety"


class WikiMetricAvailabilityV1(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class WikiObservationStatusV1(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class WikiAnswerModeV1(StrEnum):
    GROUNDED = "grounded"
    REFUSAL = "refusal"
    NO_RESULT = "no_result"


class _FrozenEvaluation(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class WikiGoldenCaseV1(_FrozenEvaluation):
    case_id: str
    slice: WikiGoldenSliceV1
    query: str = Field(min_length=1, max_length=1_000)
    project_id: str
    requester_acl_refs: tuple[str, ...] = Field(min_length=1)
    as_of: str | None
    required_sources: tuple[WikiSourceDomainV1, ...] = Field(min_length=1)
    required_page_paths: tuple[str, ...]
    required_source_ref_ids: tuple[str, ...]
    forbidden_source_ref_ids: tuple[str, ...]
    evidence_obligations: tuple[str, ...]
    expected_answerable: bool
    refusal_reason: str | None
    eligible_metrics: tuple[WikiMetricV1, ...]
    content_sha256: str

    @model_validator(mode="after")
    def _case_identity(self) -> WikiGoldenCaseV1:
        for values, label in (
            (self.requester_acl_refs, "requester ACL refs"),
            (self.required_page_paths, "required page paths"),
            (self.required_source_ref_ids, "required source refs"),
            (self.forbidden_source_ref_ids, "forbidden source refs"),
            (self.evidence_obligations, "evidence obligations"),
            (self.eligible_metrics, "eligible metrics"),
        ):
            if values != tuple(
                sorted(
                    set(values), key=lambda item: item.value if isinstance(item, StrEnum) else item
                )
            ):
                raise WikiGoldenError(f"{label} must be sorted and unique")
        for path in self.required_page_paths:
            validate_wiki_logical_path_v1(path, allow_root=False)
        if self.expected_answerable:
            if (
                not self.required_page_paths
                or not self.required_source_ref_ids
                or self.refusal_reason
            ):
                raise WikiGoldenError(
                    "answerable Wiki cases require grounded path and raw-ref truth"
                )
        elif self.refusal_reason is None:
            raise WikiGoldenError("unanswerable Wiki cases require a frozen refusal reason")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiGoldenError("Wiki Golden case digest mismatch")
        return self


class WikiGoldenReleaseV1(_FrozenEvaluation):
    dataset_id: Literal[WIKI_GOLDEN_DATASET_ID] = WIKI_GOLDEN_DATASET_ID
    dataset_version: Literal[WIKI_GOLDEN_DATASET_VERSION] = WIKI_GOLDEN_DATASET_VERSION
    contract_version: Literal[WIKI_CONTRACT_VERSION] = WIKI_CONTRACT_VERSION
    cases: tuple[WikiGoldenCaseV1, ...] = Field(
        min_length=WIKI_GOLDEN_CASE_COUNT, max_length=WIKI_GOLDEN_CASE_COUNT
    )
    slice_counts: tuple[tuple[WikiGoldenSliceV1, int], ...]
    eligible_denominators: tuple[tuple[WikiMetricV1, int], ...]
    package_sha256: str
    authority_sha256: str

    @model_validator(mode="after")
    def _release_identity(self) -> WikiGoldenReleaseV1:
        expected_ids = tuple(f"wiki-v1-{index:03d}" for index in range(1, 121))
        if tuple(item.case_id for item in self.cases) != expected_ids:
            raise WikiGoldenError("Wiki Golden membership or order mismatch")
        actual_slices = Counter(item.slice for item in self.cases)
        if self.slice_counts != tuple(EXPECTED_WIKI_SLICE_COUNTS.items()):
            raise WikiGoldenError("Wiki Golden slice order or frozen counts mismatch")
        if dict(self.slice_counts) != actual_slices:
            raise WikiGoldenError("Wiki Golden case slice membership mismatch")
        actual_denominators: Counter[WikiMetricV1] = Counter()
        for item in self.cases:
            actual_denominators.update(item.eligible_metrics)
        if dict(self.eligible_denominators) != actual_denominators:
            raise WikiGoldenError("Wiki Golden eligible denominators mismatch")
        if self.eligible_denominators != tuple(
            (metric, actual_denominators[metric]) for metric in WikiMetricV1
        ):
            raise WikiGoldenError("Wiki Golden eligible denominators are not canonical")
        if self.package_sha256 != WIKI_GOLDEN_PACKAGE_SHA256:
            raise WikiGoldenError("Wiki Golden package does not match reviewed authority")
        if self.authority_sha256 != WIKI_GOLDEN_AUTHORITY_SHA256:
            raise WikiGoldenError("Wiki Golden truth authority does not match reviewed authority")
        return self


class WikiReviewedNavigationRowV1(_FrozenEvaluation):
    case_id: str
    status: WikiObservationStatusV1
    selected_page_paths: tuple[str, ...] = ()
    visited_page_paths: tuple[str, ...] = ()
    followed_link_ids: tuple[str, ...] = ()
    retrieved_source_ref_ids: tuple[str, ...] = ()
    cited_source_ref_ids: tuple[str, ...] = ()
    fulfilled_obligations: tuple[str, ...] = ()
    answer_mode: WikiAnswerModeV1 = WikiAnswerModeV1.NO_RESULT
    refusal_reason: str | None = None
    unsupported_claim_count: int = Field(default=0, ge=0)
    diagnostic_code: str | None = None

    @model_validator(mode="after")
    def _row_identity(self) -> WikiReviewedNavigationRowV1:
        for values, label in (
            (self.selected_page_paths, "selected paths"),
            (self.visited_page_paths, "visited paths"),
            (self.followed_link_ids, "followed links"),
            (self.retrieved_source_ref_ids, "retrieved refs"),
            (self.cited_source_ref_ids, "cited refs"),
            (self.fulfilled_obligations, "fulfilled obligations"),
        ):
            if values != tuple(dict.fromkeys(values)):
                raise WikiGoldenError(f"review row {label} must be ordered and unique")
        for path in (*self.selected_page_paths, *self.visited_page_paths):
            validate_wiki_logical_path_v1(path, allow_root=False)
        if self.status is WikiObservationStatusV1.AVAILABLE and self.diagnostic_code:
            raise WikiGoldenError("available observations cannot carry an error diagnostic")
        if self.status is not WikiObservationStatusV1.AVAILABLE and not self.diagnostic_code:
            raise WikiGoldenError(
                "unavailable/error observations require a bounded diagnostic code"
            )
        return self


class WikiMetricResultV1(_FrozenEvaluation):
    metric: WikiMetricV1
    availability: WikiMetricAvailabilityV1
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    value: float | None

    @model_validator(mode="after")
    def _metric_identity(self) -> WikiMetricResultV1:
        if self.evaluated + self.unavailable != self.denominator:
            raise WikiGoldenError("Wiki metric reviewed denominator mismatch")
        if self.numerator > self.evaluated:
            raise WikiGoldenError("Wiki metric numerator exceeds evaluated observations")
        if self.availability is WikiMetricAvailabilityV1.UNAVAILABLE:
            if self.evaluated != 0 or self.value is not None:
                raise WikiGoldenError("unavailable Wiki metric cannot report a value")
        else:
            expected = self.numerator / self.evaluated if self.evaluated else None
            if expected is None or self.value is None or abs(self.value - expected) > 1e-12:
                raise WikiGoldenError(
                    "Wiki metric value does not match honest reviewed denominator"
                )
        return self


class WikiEvaluationReportV1(_FrozenEvaluation):
    dataset_id: Literal[WIKI_GOLDEN_DATASET_ID] = WIKI_GOLDEN_DATASET_ID
    package_sha256: str
    authority_sha256: str
    evaluated_case_ids: tuple[str, ...]
    observation_set_sha256: str
    metrics: tuple[WikiMetricResultV1, ...]
    evaluator_version: Literal[WIKI_EVALUATOR_VERSION] = WIKI_EVALUATOR_VERSION
    production_authorized: Literal[False] = False
    qualification: Literal["FOUNDATION_ONLY_NON_QUALIFIED"] = "FOUNDATION_ONLY_NON_QUALIFIED"
    content_sha256: str

    @model_validator(mode="after")
    def _report_identity(self) -> WikiEvaluationReportV1:
        if tuple(item.metric for item in self.metrics) != tuple(WikiMetricV1):
            raise WikiGoldenError("Wiki report metric membership is incomplete")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiGoldenError("Wiki evaluation report digest mismatch")
        return self


class WikiSliceEvaluationV1(_FrozenEvaluation):
    slice: WikiGoldenSliceV1
    case_count: int = Field(ge=1)
    metrics: tuple[WikiMetricResultV1, ...]
    content_sha256: str

    @model_validator(mode="after")
    def _slice_report(self) -> WikiSliceEvaluationV1:
        metrics = tuple(item.metric for item in self.metrics)
        if metrics != tuple(sorted(set(metrics), key=lambda item: item.value)):
            raise WikiGoldenError("Wiki slice metrics are not canonical")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiGoldenError("Wiki slice report digest mismatch")
        return self


class WikiSliceEvaluationReportV1(_FrozenEvaluation):
    dataset_id: Literal[WIKI_GOLDEN_DATASET_ID] = WIKI_GOLDEN_DATASET_ID
    package_sha256: str
    authority_sha256: str
    slices: tuple[WikiSliceEvaluationV1, ...]
    evaluator_version: Literal[WIKI_EVALUATOR_VERSION] = WIKI_EVALUATOR_VERSION
    production_authorized: Literal[False] = False
    content_sha256: str

    @model_validator(mode="after")
    def _slice_set(self) -> WikiSliceEvaluationReportV1:
        if tuple(item.slice for item in self.slices) != tuple(WikiGoldenSliceV1):
            raise WikiGoldenError("Wiki slice report membership is incomplete")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiGoldenError("Wiki slice report digest mismatch")
        return self


def _case_metrics(answerable: bool) -> tuple[WikiMetricV1, ...]:
    metrics = {
        WikiMetricV1.ACL_SAFETY,
        WikiMetricV1.UNSUPPORTED_CLAIM_SAFETY,
    }
    if answerable:
        metrics.update(
            {
                WikiMetricV1.PATH_RECALL,
                WikiMetricV1.FULL_EVIDENCE,
                WikiMetricV1.OBLIGATION_COMPLETION,
                WikiMetricV1.RAW_CITATION_BINDING,
                WikiMetricV1.PREMATURE_STOP_AVOIDANCE,
            }
        )
    else:
        metrics.add(WikiMetricV1.CORRECT_REFUSAL)
    return tuple(sorted(metrics, key=lambda item: item.value))


def _build_wiki_golden_cases_v1() -> tuple[WikiGoldenCaseV1, ...]:
    slice_sequence: list[WikiGoldenSliceV1] = []
    for slice_name, count in EXPECTED_WIKI_SLICE_COUNTS.items():
        slice_sequence.extend([slice_name] * count)
    source_cycle = tuple(WikiSourceDomainV1)
    intent_by_slice = {
        WikiGoldenSliceV1.LOCAL_RAW_DETAIL: "components",
        WikiGoldenSliceV1.BRIDGE_2HOP: "capabilities",
        WikiGoldenSliceV1.HIGH_FAN_IN: "architecture",
        WikiGoldenSliceV1.COMPARE_CAUSAL: "experiments",
        WikiGoldenSliceV1.TEMPORAL_STALE: "changes",
        WikiGoldenSliceV1.GLOBAL_AGGREGATE: "findings",
        WikiGoldenSliceV1.EXPLORATORY: "issues",
        WikiGoldenSliceV1.UNANSWERABLE: "requirements",
        WikiGoldenSliceV1.ACL_SECURITY: "decisions",
        WikiGoldenSliceV1.INCREMENTAL_CONTRADICTION: "findings",
    }
    cases: list[WikiGoldenCaseV1] = []
    for index, slice_name in enumerate(slice_sequence, start=1):
        case_id = f"wiki-v1-{index:03d}"
        answerable = slice_name is not WikiGoldenSliceV1.UNANSWERABLE
        source_count = 1
        if slice_name in {
            WikiGoldenSliceV1.BRIDGE_2HOP,
            WikiGoldenSliceV1.COMPARE_CAUSAL,
            WikiGoldenSliceV1.TEMPORAL_STALE,
        }:
            source_count = 2
        elif slice_name in {
            WikiGoldenSliceV1.HIGH_FAN_IN,
            WikiGoldenSliceV1.GLOBAL_AGGREGATE,
            WikiGoldenSliceV1.EXPLORATORY,
            WikiGoldenSliceV1.INCREMENTAL_CONTRADICTION,
        }:
            source_count = 3
        required_sources = tuple(
            sorted(
                {
                    source_cycle[(index + offset - 1) % len(source_cycle)]
                    for offset in range(source_count)
                },
                key=lambda item: item.value,
            )
        )
        intent = intent_by_slice[slice_name]
        page_count = 0 if not answerable else 2 if source_count > 1 else 1
        required_paths = tuple(
            sorted(f"/{intent}/{case_id}-page-{offset}" for offset in range(1, page_count + 1))
        )
        required_refs = (
            tuple(
                sorted(
                    f"raw-{source.value}-{case_id}-{offset}"
                    for offset, source in enumerate(required_sources, 1)
                )
            )
            if answerable
            else ()
        )
        forbidden_refs = (
            (f"raw-private-{case_id}",)
            if slice_name in {WikiGoldenSliceV1.ACL_SECURITY, WikiGoldenSliceV1.UNANSWERABLE}
            else ()
        )
        obligations = [f"role:{source.value}" for source in required_sources]
        if slice_name in {WikiGoldenSliceV1.BRIDGE_2HOP, WikiGoldenSliceV1.HIGH_FAN_IN}:
            obligations.append("relation:bridge")
        if slice_name in {
            WikiGoldenSliceV1.COMPARE_CAUSAL,
            WikiGoldenSliceV1.INCREMENTAL_CONTRADICTION,
        }:
            obligations.append("relation:causal")
        if slice_name is WikiGoldenSliceV1.TEMPORAL_STALE:
            obligations.append("temporal:as-of")
        payload = {
            "case_id": case_id,
            "slice": slice_name,
            "query": f"Reviewed Wiki navigation case {case_id} for {slice_name.value}",
            "project_id": "project-wiki-golden-v1",
            "requester_acl_refs": ("team-a",),
            "as_of": "2026-08-01T00:00:00Z"
            if slice_name is WikiGoldenSliceV1.TEMPORAL_STALE
            else None,
            "required_sources": required_sources,
            "required_page_paths": required_paths,
            "required_source_ref_ids": required_refs,
            "forbidden_source_ref_ids": forbidden_refs,
            "evidence_obligations": tuple(sorted(obligations)),
            "expected_answerable": answerable,
            "refusal_reason": None if answerable else "insufficient_visible_evidence",
            "eligible_metrics": _case_metrics(answerable),
        }
        normalized = WikiGoldenCaseV1.model_construct(
            **payload, content_sha256="pending"
        ).model_dump(mode="json", exclude={"content_sha256"})
        cases.append(
            WikiGoldenCaseV1(
                **payload,
                content_sha256=canonical_sha256_v1(normalized),
            )
        )
    return tuple(cases)


def _golden_payloads_v1() -> tuple[tuple[WikiGoldenCaseV1, ...], str, str]:
    cases = _build_wiki_golden_cases_v1()
    package_sha256 = canonical_sha256_v1(
        {
            "cases": [item.model_dump(mode="json") for item in cases],
            "contract_version": WIKI_CONTRACT_VERSION,
            "dataset_id": WIKI_GOLDEN_DATASET_ID,
            "dataset_version": WIKI_GOLDEN_DATASET_VERSION,
            "slice_counts": [
                (item.value, count) for item, count in EXPECTED_WIKI_SLICE_COUNTS.items()
            ],
        }
    )
    denominators = Counter(metric for case in cases for metric in case.eligible_metrics)
    authority_sha256 = canonical_sha256_v1(
        {
            "case_digests": [item.content_sha256 for item in cases],
            "eligible_denominators": [
                (metric.value, denominators[metric]) for metric in WikiMetricV1
            ],
            "ordered_case_ids": [item.case_id for item in cases],
            "package_sha256": package_sha256,
            "slice_counts": [
                (item.value, count) for item, count in EXPECTED_WIKI_SLICE_COUNTS.items()
            ],
        }
    )
    return cases, package_sha256, authority_sha256


def build_wiki_golden_release_v1() -> WikiGoldenReleaseV1:
    cases, package_sha256, authority_sha256 = _golden_payloads_v1()
    denominators = Counter(metric for case in cases for metric in case.eligible_metrics)
    return WikiGoldenReleaseV1(
        cases=cases,
        slice_counts=tuple(EXPECTED_WIKI_SLICE_COUNTS.items()),
        eligible_denominators=tuple((metric, denominators[metric]) for metric in WikiMetricV1),
        package_sha256=package_sha256,
        authority_sha256=authority_sha256,
    )


def _metric_passes(
    case: WikiGoldenCaseV1, row: WikiReviewedNavigationRowV1, metric: WikiMetricV1
) -> bool:
    required_paths = set(case.required_page_paths)
    required_refs = set(case.required_source_ref_ids)
    forbidden_refs = set(case.forbidden_source_ref_ids)
    selected_paths = set(row.selected_page_paths)
    retrieved_refs = set(row.retrieved_source_ref_ids)
    cited_refs = set(row.cited_source_ref_ids)
    if metric is WikiMetricV1.PATH_RECALL:
        return required_paths.issubset(selected_paths)
    if metric is WikiMetricV1.FULL_EVIDENCE:
        return required_refs.issubset(retrieved_refs)
    if metric is WikiMetricV1.OBLIGATION_COMPLETION:
        return set(case.evidence_obligations).issubset(row.fulfilled_obligations)
    if metric is WikiMetricV1.RAW_CITATION_BINDING:
        return required_refs.issubset(cited_refs) and cited_refs.issubset(retrieved_refs)
    if metric is WikiMetricV1.PREMATURE_STOP_AVOIDANCE:
        return row.answer_mode is WikiAnswerModeV1.GROUNDED and required_refs.issubset(
            retrieved_refs
        )
    if metric is WikiMetricV1.CORRECT_REFUSAL:
        return (
            row.answer_mode is WikiAnswerModeV1.REFUSAL
            and row.refusal_reason == case.refusal_reason
        )
    if metric is WikiMetricV1.ACL_SAFETY:
        observed = selected_paths | retrieved_refs | cited_refs
        return not forbidden_refs.intersection(observed)
    if metric is WikiMetricV1.UNSUPPORTED_CLAIM_SAFETY:
        return row.unsupported_claim_count == 0
    raise AssertionError(f"Unhandled Wiki metric: {metric}")


def evaluate_reviewed_wiki_navigation_v1(
    release: WikiGoldenReleaseV1,
    rows: tuple[WikiReviewedNavigationRowV1, ...],
    *,
    case_membership: tuple[str, ...] | None = None,
) -> WikiEvaluationReportV1:
    """Recompute metrics from released truth; reviewed rows cannot provide labels."""

    expected_ids = tuple(item.case_id for item in release.cases)
    membership = expected_ids if case_membership is None else case_membership
    if membership != expected_ids:
        raise WikiGoldenError(
            "explicit Wiki evaluation membership must equal the released ordered set"
        )
    if tuple(item.case_id for item in rows) != expected_ids:
        raise WikiGoldenError(
            "reviewed Wiki rows must cover the released ordered membership exactly once"
        )
    case_by_id = {item.case_id: item for item in release.cases}
    numerators: Counter[WikiMetricV1] = Counter()
    evaluated: Counter[WikiMetricV1] = Counter()
    unavailable: Counter[WikiMetricV1] = Counter()
    for row in rows:
        case = case_by_id[row.case_id]
        for metric in case.eligible_metrics:
            if row.status is WikiObservationStatusV1.AVAILABLE:
                evaluated[metric] += 1
                numerators[metric] += int(_metric_passes(case, row, metric))
            else:
                unavailable[metric] += 1
    frozen_denominators = dict(release.eligible_denominators)
    metric_results: list[WikiMetricResultV1] = []
    for metric in WikiMetricV1:
        denominator = frozen_denominators[metric]
        if evaluated[metric] == 0:
            availability = WikiMetricAvailabilityV1.UNAVAILABLE
            value = None
        elif unavailable[metric]:
            availability = WikiMetricAvailabilityV1.PROVISIONAL
            value = numerators[metric] / evaluated[metric]
        else:
            availability = WikiMetricAvailabilityV1.AVAILABLE
            value = numerators[metric] / evaluated[metric]
        metric_results.append(
            WikiMetricResultV1(
                metric=metric,
                availability=availability,
                numerator=numerators[metric],
                denominator=denominator,
                evaluated=evaluated[metric],
                unavailable=unavailable[metric],
                value=value,
            )
        )
    report_payload = {
        "dataset_id": WIKI_GOLDEN_DATASET_ID,
        "package_sha256": release.package_sha256,
        "authority_sha256": release.authority_sha256,
        "evaluated_case_ids": expected_ids,
        "observation_set_sha256": canonical_sha256_v1(
            [item.model_dump(mode="json") for item in rows]
        ),
        "metrics": tuple(metric_results),
        "evaluator_version": WIKI_EVALUATOR_VERSION,
        "production_authorized": False,
        "qualification": "FOUNDATION_ONLY_NON_QUALIFIED",
    }
    return WikiEvaluationReportV1(
        **report_payload,
        content_sha256=canonical_sha256_v1(
            {
                **report_payload,
                "metrics": [item.model_dump(mode="json") for item in metric_results],
            }
        ),
    )


def evaluate_wiki_navigation_slices_v1(
    release: WikiGoldenReleaseV1,
    rows: tuple[WikiReviewedNavigationRowV1, ...],
) -> WikiSliceEvaluationReportV1:
    """Recompute every slice from the same complete released membership."""

    expected_ids = tuple(item.case_id for item in release.cases)
    if tuple(item.case_id for item in rows) != expected_ids:
        raise WikiGoldenError("Wiki slice evaluation requires the full ordered membership")
    row_by_id = {item.case_id: item for item in rows}
    slices: list[WikiSliceEvaluationV1] = []
    for slice_name in WikiGoldenSliceV1:
        cases = tuple(item for item in release.cases if item.slice is slice_name)
        denominators: Counter[WikiMetricV1] = Counter(
            metric for case in cases for metric in case.eligible_metrics
        )
        numerators: Counter[WikiMetricV1] = Counter()
        evaluated: Counter[WikiMetricV1] = Counter()
        unavailable: Counter[WikiMetricV1] = Counter()
        for case in cases:
            row = row_by_id[case.case_id]
            for metric in case.eligible_metrics:
                if row.status is WikiObservationStatusV1.AVAILABLE:
                    evaluated[metric] += 1
                    numerators[metric] += int(_metric_passes(case, row, metric))
                else:
                    unavailable[metric] += 1
        metric_results = []
        for metric in sorted(denominators, key=lambda item: item.value):
            if evaluated[metric] == 0:
                availability = WikiMetricAvailabilityV1.UNAVAILABLE
                value = None
            elif unavailable[metric]:
                availability = WikiMetricAvailabilityV1.PROVISIONAL
                value = numerators[metric] / evaluated[metric]
            else:
                availability = WikiMetricAvailabilityV1.AVAILABLE
                value = numerators[metric] / evaluated[metric]
            metric_results.append(
                WikiMetricResultV1(
                    metric=metric,
                    availability=availability,
                    numerator=numerators[metric],
                    denominator=denominators[metric],
                    evaluated=evaluated[metric],
                    unavailable=unavailable[metric],
                    value=value,
                )
            )
        slice_payload = {
            "slice": slice_name,
            "case_count": len(cases),
            "metrics": tuple(metric_results),
        }
        slices.append(
            WikiSliceEvaluationV1(
                **slice_payload,
                content_sha256=canonical_sha256_v1(
                    {
                        **slice_payload,
                        "slice": slice_name.value,
                        "metrics": [item.model_dump(mode="json") for item in metric_results],
                    }
                ),
            )
        )
    report_payload = {
        "dataset_id": WIKI_GOLDEN_DATASET_ID,
        "package_sha256": release.package_sha256,
        "authority_sha256": release.authority_sha256,
        "slices": tuple(slices),
        "evaluator_version": WIKI_EVALUATOR_VERSION,
        "production_authorized": False,
    }
    return WikiSliceEvaluationReportV1(
        **report_payload,
        content_sha256=canonical_sha256_v1(
            {
                **report_payload,
                "slices": [item.model_dump(mode="json") for item in slices],
            }
        ),
    )


def wiki_golden_debug_hashes_v1() -> tuple[str, str]:
    """Return rebuilt values for tests; never used as runtime expected authority."""

    _, package_sha256, authority_sha256 = _golden_payloads_v1()
    return package_sha256, authority_sha256
