"""Released 60-case M5/M6 evaluator and immutable evidence bundle."""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .answer_v2 import EvidencePackV2, GroundedAnswerV2
from .evidence_graph_v2 import EvidenceTraversalTraceV2
from .global_governance_v2 import (
    GlobalReleaseEvidenceV2,
    SecurityMatrixReportV2,
)
from .multisource_foundation_v2 import (
    MultiSourceGoldenCaseV2,
    MultiSourceGoldenReleaseV2,
    MultiSourcePlanV2,
    RoleFusionResultV2,
    SourceCalibrationProfileV2,
    SourceExecutionStatusV2,
)
from .performance_v2 import PerformanceDashboardV2
from .sources.experiment.contracts_v2 import (
    canonical_json_bytes_v2,
    canonical_sha256_v2,
)

MULTISOURCE_EVALUATOR_VERSION = "multisource-released-evaluator-v2"
MULTISOURCE_EVIDENCE_BUNDLE_VERSION = "multisource-evidence-bundle-v2"


class AvailabilityV2(StrEnum):
    AVAILABLE = "available"
    PROVISIONAL = "provisional"
    UNAVAILABLE = "unavailable"


class CaseExecutionStateV2(StrEnum):
    REVIEWED = "reviewed"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class MultiSourceReviewedCaseV2(_Frozen):
    case_id: str
    execution_state: CaseExecutionStateV2
    plan: MultiSourcePlanV2 | None = None
    fusion: RoleFusionResultV2 | None = None
    traversal: EvidenceTraversalTraceV2 | None = None
    evidence_pack: EvidencePackV2 | None = None
    answer: GroundedAnswerV2 | None = None
    failure_reason: str | None = None
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> MultiSourceReviewedCaseV2:
        evidence = (
            self.plan,
            self.fusion,
            self.traversal,
            self.evidence_pack,
            self.answer,
        )
        if self.execution_state is CaseExecutionStateV2.REVIEWED:
            if any(item is None for item in evidence) or self.failure_reason is not None:
                raise ValueError("reviewed case requires complete evidence")
            assert self.plan is not None
            assert self.fusion is not None
            assert self.traversal is not None
            assert self.evidence_pack is not None
            if (
                self.traversal.plan_sha256 != self.plan.content_sha256
                or self.evidence_pack.plan_sha256 != self.plan.content_sha256
            ):
                raise ValueError("reviewed case plan identity mismatch")
        elif any(item is not None for item in evidence) or not self.failure_reason:
            raise ValueError("unavailable/error case cannot contain evaluated evidence")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("reviewed case digest mismatch")
        return self


def build_reviewed_case_v2(
    *,
    case_id: str,
    execution_state: CaseExecutionStateV2,
    plan: MultiSourcePlanV2 | None = None,
    fusion: RoleFusionResultV2 | None = None,
    traversal: EvidenceTraversalTraceV2 | None = None,
    evidence_pack: EvidencePackV2 | None = None,
    answer: GroundedAnswerV2 | None = None,
    failure_reason: str | None = None,
) -> MultiSourceReviewedCaseV2:
    payload = {
        "case_id": case_id,
        "execution_state": execution_state,
        "plan": plan,
        "fusion": fusion,
        "traversal": traversal,
        "evidence_pack": evidence_pack,
        "answer": answer,
        "failure_reason": failure_reason,
    }
    normalized = MultiSourceReviewedCaseV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return MultiSourceReviewedCaseV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


class MetricResultV2(_Frozen):
    metric: str
    numerator: float = Field(ge=0)
    denominator: int = Field(ge=0)
    eligible: int = Field(ge=0)
    evaluated: int = Field(ge=0)
    unavailable: int = Field(ge=0)
    value: float | None
    availability: AvailabilityV2

    @model_validator(mode="after")
    def _denominator(self) -> MetricResultV2:
        if self.eligible != self.evaluated + self.unavailable:
            raise ValueError("metric eligibility accounting mismatch")
        if self.denominator != self.evaluated:
            raise ValueError("metric denominator must equal evaluated cases")
        if self.availability is AvailabilityV2.UNAVAILABLE and self.value is not None:
            raise ValueError("unavailable metric cannot have a value")
        if self.availability is not AvailabilityV2.UNAVAILABLE:
            expected = self.numerator / self.denominator if self.denominator else None
            if expected is None or self.value is None or abs(expected - self.value) > 1e-12:
                raise ValueError("metric value mismatch")
        return self


class MultiSourceCaseScoreV2(_Frozen):
    case_id: str
    slice_name: str
    reviewed: bool
    source_route_precision: float | None
    source_route_recall: float | None
    required_role_coverage: float | None
    entity_recall_at_20: float | None
    path_recall: float | None
    counter_evidence_recall: float | None
    version_accuracy: float | None
    wrong_version_rate: float | None
    citation_precision: float | None
    citation_completeness: float | None
    expected_decision_accuracy: float | None
    unanswerable_acceptable: float | None
    planner_capability_violation: float | None
    timeout_truth_accuracy: float | None
    unauthorized_evidence_leakage: float | None


class MultiSourceEvaluationReportV2(_Frozen):
    golden_sha256: str
    reviewed_set_sha256: str
    case_count: int = Field(ge=0)
    reviewed_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)
    metrics: tuple[MetricResultV2, ...]
    slice_metrics: tuple[tuple[str, tuple[MetricResultV2, ...]], ...]
    case_scores: tuple[MultiSourceCaseScoreV2, ...]
    calibration_ece: tuple[tuple[str, float], ...]
    calibration_brier: tuple[tuple[str, float], ...]
    qualified: bool
    qualification_failures: tuple[str, ...]
    evaluator_version: str = MULTISOURCE_EVALUATOR_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> MultiSourceEvaluationReportV2:
        if self.case_count != (self.reviewed_count + self.error_count + self.unavailable_count):
            raise ValueError("evaluation case accounting mismatch")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("evaluation report digest mismatch")
        return self


_METRICS = (
    "source_route_precision",
    "source_route_recall",
    "required_role_coverage",
    "entity_recall_at_20",
    "path_recall",
    "counter_evidence_recall",
    "version_accuracy",
    "wrong_version_rate",
    "citation_precision",
    "citation_completeness",
    "expected_decision_accuracy",
    "unanswerable_acceptable",
    "planner_capability_violation",
    "timeout_truth_accuracy",
    "unauthorized_evidence_leakage",
)


def score_multisource_case_v2(
    truth: MultiSourceGoldenCaseV2,
    row: MultiSourceReviewedCaseV2,
) -> MultiSourceCaseScoreV2:
    if truth.case_id != row.case_id:
        raise ValueError("reviewed case does not match Golden truth")
    if row.execution_state is not CaseExecutionStateV2.REVIEWED:
        return MultiSourceCaseScoreV2(
            case_id=truth.case_id,
            slice_name=truth.slice_name,
            reviewed=False,
            **{metric: None for metric in _METRICS},
        )
    assert row.plan is not None
    assert row.fusion is not None
    assert row.traversal is not None
    assert row.evidence_pack is not None
    assert row.answer is not None
    if row.plan.question_sha256 != canonical_sha256_v2(truth.question):
        raise ValueError("reviewed plan question does not match Golden truth")
    routed = set(row.plan.source_routes)
    required_sources = set(truth.required_sources)
    selected = row.fusion.selected[:20]
    selected_entities = {item.entity_id for item in selected}
    required_entities = set(truth.required_entity_ids)
    selected_roles = {role for item in selected for role in item.matched_roles}
    required_roles = set(truth.required_roles)
    version_correct = sum(
        item.version_alignment in {"exact", "compatible", "historical_target", "not_applicable"}
        for item in selected
    )
    wrong_versions = sum(item.version_alignment == "mismatch" for item in selected)
    accepted_templates = set(row.plan.graph_templates) if row.traversal.accepted_paths else set()
    counter_eligible = truth.expected_decision == "potentially_stale"
    counter_found = any(item.counter_evidence for item in selected) or any(
        item.counter_evidence for item in row.traversal.accepted_paths
    )
    unanswerable_eligible = truth.expected_decision in {
        "unauthorized",
        "insufficient_evidence",
    }
    source_statuses = {item.status for item in row.fusion.source_status}
    timeout_eligible = truth.fault_injection == "retriever_timeout"
    timeout_honest = (
        SourceExecutionStatusV2.TIMEOUT in source_statuses
        and row.evidence_pack.decision.status == "source_unavailable"
    )
    precision_denominator = len(routed)
    answer_citation_denominator = row.answer.citation_precision_denominator
    answer_completeness_denominator = row.answer.citation_completeness_denominator
    unauthorized_leakage = sum(item.acl_ref not in row.plan.scope.acl_refs for item in selected)
    forbidden_leakage = len(selected_entities & set(truth.forbidden_entities))
    return MultiSourceCaseScoreV2(
        case_id=truth.case_id,
        slice_name=truth.slice_name,
        reviewed=True,
        source_route_precision=(
            len(routed & required_sources) / precision_denominator if precision_denominator else 0.0
        ),
        source_route_recall=(
            len(routed & required_sources) / len(required_sources) if required_sources else 1.0
        ),
        required_role_coverage=(
            len(selected_roles & required_roles) / len(required_roles) if required_roles else 1.0
        ),
        entity_recall_at_20=(
            len(selected_entities & required_entities) / len(required_entities)
            if required_entities
            else 1.0
        ),
        path_recall=(
            len(accepted_templates & set(truth.required_paths)) / len(truth.required_paths)
            if truth.required_paths
            else 1.0
        ),
        counter_evidence_recall=(float(counter_found) if counter_eligible else None),
        version_accuracy=version_correct / len(selected) if selected else 0.0,
        wrong_version_rate=wrong_versions / len(selected) if selected else 0.0,
        citation_precision=(
            row.answer.citation_precision_numerator / answer_citation_denominator
            if answer_citation_denominator
            else 0.0
        ),
        citation_completeness=(
            row.answer.citation_completeness_numerator / answer_completeness_denominator
            if answer_completeness_denominator
            else 0.0
        ),
        expected_decision_accuracy=float(
            row.evidence_pack.decision.status == truth.expected_decision
        ),
        unanswerable_acceptable=(
            float(
                row.evidence_pack.decision.answer_mode.value == "refusal"
                and row.answer.mode.value == "refusal"
            )
            if unanswerable_eligible
            else None
        ),
        planner_capability_violation=float(bool(row.plan.planning_gaps)),
        timeout_truth_accuracy=float(timeout_honest) if timeout_eligible else None,
        unauthorized_evidence_leakage=float(unauthorized_leakage + forbidden_leakage),
    )


def _metric(
    name: str,
    scores: tuple[MultiSourceCaseScoreV2, ...],
    eligible_count: int,
) -> MetricResultV2:
    values = tuple(value for score in scores if (value := getattr(score, name)) is not None)
    availability = (
        AvailabilityV2.UNAVAILABLE
        if not values
        else AvailabilityV2.AVAILABLE
        if len(values) == eligible_count
        else AvailabilityV2.PROVISIONAL
    )
    return MetricResultV2(
        metric=name,
        numerator=sum(values),
        denominator=len(values),
        eligible=eligible_count,
        evaluated=len(values),
        unavailable=eligible_count - len(values),
        value=sum(values) / len(values) if values else None,
        availability=availability,
    )


def evaluate_multisource_release_v2(
    golden: MultiSourceGoldenReleaseV2,
    rows: tuple[MultiSourceReviewedCaseV2, ...],
    *,
    calibrations: tuple[SourceCalibrationProfileV2, ...] = (),
) -> MultiSourceEvaluationReportV2:
    if tuple(item.case_id for item in rows) != tuple(item.case_id for item in golden.cases):
        raise ValueError("reviewed result membership must exactly match released Golden")
    scores = tuple(
        score_multisource_case_v2(truth, row) for truth, row in zip(golden.cases, rows, strict=True)
    )
    eligibility = {
        name: sum(
            name
            not in {"counter_evidence_recall", "unanswerable_acceptable", "timeout_truth_accuracy"}
            or (
                name == "counter_evidence_recall" and truth.expected_decision == "potentially_stale"
            )
            or (
                name == "unanswerable_acceptable"
                and truth.expected_decision in {"unauthorized", "insufficient_evidence"}
            )
            or (name == "timeout_truth_accuracy" and truth.fault_injection == "retriever_timeout")
            for truth in golden.cases
        )
        for name in _METRICS
    }
    metrics = tuple(_metric(name, scores, eligibility[name]) for name in _METRICS)
    slices: dict[str, list[MultiSourceCaseScoreV2]] = defaultdict(list)
    slice_truth: dict[str, list[MultiSourceGoldenCaseV2]] = defaultdict(list)
    for score in scores:
        slices[score.slice_name].append(score)
    for truth in golden.cases:
        slice_truth[truth.slice_name].append(truth)
    slice_metrics = tuple(
        (
            slice_name,
            tuple(
                _metric(
                    name,
                    tuple(slice_scores),
                    sum(
                        name
                        not in {
                            "counter_evidence_recall",
                            "unanswerable_acceptable",
                            "timeout_truth_accuracy",
                        }
                        or (
                            name == "counter_evidence_recall"
                            and truth.expected_decision == "potentially_stale"
                        )
                        or (
                            name == "unanswerable_acceptable"
                            and truth.expected_decision in {"unauthorized", "insufficient_evidence"}
                        )
                        or (
                            name == "timeout_truth_accuracy"
                            and truth.fault_injection == "retriever_timeout"
                        )
                        for truth in slice_truth[slice_name]
                    ),
                )
                for name in _METRICS
            ),
        )
        for slice_name, slice_scores in slices.items()
    )
    metric_map = {item.metric: item for item in metrics}
    thresholds = {
        "source_route_recall": (0.90, "minimum"),
        "required_role_coverage": (0.90, "minimum"),
        "entity_recall_at_20": (0.85, "minimum"),
        "path_recall": (0.80, "minimum"),
        "counter_evidence_recall": (0.90, "minimum"),
        "version_accuracy": (0.97, "minimum"),
        "wrong_version_rate": (0.02, "maximum"),
        "citation_precision": (0.95, "minimum"),
        "citation_completeness": (0.95, "minimum"),
        "unanswerable_acceptable": (0.90, "minimum"),
        "planner_capability_violation": (0.0, "maximum"),
        "unauthorized_evidence_leakage": (0.0, "maximum"),
    }
    failures: list[str] = []
    for name, (threshold, direction) in thresholds.items():
        result = metric_map[name]
        if result.availability is not AvailabilityV2.AVAILABLE:
            failures.append(f"{name}:not_available")
        elif (direction == "minimum" and result.value is not None and result.value < threshold) or (
            direction == "maximum" and result.value is not None and result.value > threshold
        ):
            failures.append(f"{name}:threshold")
    payload = {
        "golden_sha256": golden.content_sha256,
        "reviewed_set_sha256": canonical_sha256_v2([item.model_dump(mode="json") for item in rows]),
        "case_count": len(rows),
        "reviewed_count": sum(
            item.execution_state is CaseExecutionStateV2.REVIEWED for item in rows
        ),
        "error_count": sum(item.execution_state is CaseExecutionStateV2.ERROR for item in rows),
        "unavailable_count": sum(
            item.execution_state is CaseExecutionStateV2.UNAVAILABLE for item in rows
        ),
        "metrics": metrics,
        "slice_metrics": slice_metrics,
        "case_scores": scores,
        "calibration_ece": tuple(sorted((item.source, item.ece) for item in calibrations)),
        "calibration_brier": tuple(sorted((item.source, item.brier) for item in calibrations)),
        "qualified": not failures,
        "qualification_failures": tuple(failures),
        "evaluator_version": MULTISOURCE_EVALUATOR_VERSION,
    }
    normalized = MultiSourceEvaluationReportV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return MultiSourceEvaluationReportV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


class MultiSourceEvidenceBundleV2(_Frozen):
    golden: MultiSourceGoldenReleaseV2
    evaluation: MultiSourceEvaluationReportV2
    security: SecurityMatrixReportV2
    performance: PerformanceDashboardV2
    release: GlobalReleaseEvidenceV2
    retrieval_executed: bool
    production_observation: bool
    default_engine: str
    bundle_version: str = MULTISOURCE_EVIDENCE_BUNDLE_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> MultiSourceEvidenceBundleV2:
        if self.evaluation.golden_sha256 != self.golden.content_sha256:
            raise ValueError("bundle Golden/evaluation mismatch")
        if self.release.multisource_golden_sha256 != self.golden.content_sha256:
            raise ValueError("bundle Golden/release mismatch")
        if self.default_engine != self.release.default_engine:
            raise ValueError("bundle default engine mismatch")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("multisource evidence bundle digest mismatch")
        return self


def build_multisource_bundle_v2(
    *,
    golden: MultiSourceGoldenReleaseV2,
    evaluation: MultiSourceEvaluationReportV2,
    security: SecurityMatrixReportV2,
    performance: PerformanceDashboardV2,
    release: GlobalReleaseEvidenceV2,
    retrieval_executed: bool,
    production_observation: bool,
) -> MultiSourceEvidenceBundleV2:
    payload = {
        "golden": golden,
        "evaluation": evaluation,
        "security": security,
        "performance": performance,
        "release": release,
        "retrieval_executed": retrieval_executed,
        "production_observation": production_observation,
        "default_engine": release.default_engine,
        "bundle_version": MULTISOURCE_EVIDENCE_BUNDLE_VERSION,
    }
    normalized = MultiSourceEvidenceBundleV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return MultiSourceEvidenceBundleV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def serialize_multisource_bundle_v2(bundle: MultiSourceEvidenceBundleV2) -> bytes:
    return canonical_json_bytes_v2(bundle.model_dump(mode="json"))


def verify_multisource_bundle_v2(payload: bytes) -> MultiSourceEvidenceBundleV2:
    import json

    parsed = json.loads(payload)
    bundle = MultiSourceEvidenceBundleV2.model_validate(parsed)
    if serialize_multisource_bundle_v2(bundle) != payload:
        raise ValueError("multisource evidence bundle is not canonical")
    return bundle


__all__ = [
    "MULTISOURCE_EVALUATOR_VERSION",
    "MULTISOURCE_EVIDENCE_BUNDLE_VERSION",
    "AvailabilityV2",
    "CaseExecutionStateV2",
    "MetricResultV2",
    "MultiSourceCaseScoreV2",
    "MultiSourceEvaluationReportV2",
    "MultiSourceEvidenceBundleV2",
    "MultiSourceReviewedCaseV2",
    "build_multisource_bundle_v2",
    "build_reviewed_case_v2",
    "evaluate_multisource_release_v2",
    "score_multisource_case_v2",
    "serialize_multisource_bundle_v2",
    "verify_multisource_bundle_v2",
]
