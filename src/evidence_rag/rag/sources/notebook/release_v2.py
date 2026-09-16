"""Side-effect-free Notebook shadow/canary/release evidence evaluator."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import canonical_sha256

NOTEBOOK_RELEASE_EVALUATOR_VERSION = "notebook-release-evaluator-v2"


class NotebookReleaseStage(StrEnum):
    OFFLINE = "OFFLINE"
    ISOLATED_BASELINE = "ISOLATED_BASELINE"
    SHADOW_INTERNAL_100 = "SHADOW_INTERNAL_100"
    CANARY_5 = "CANARY_5"
    CANARY_25 = "CANARY_25"
    OPT_IN_100 = "OPT_IN_100"
    DEFAULT_V2 = "DEFAULT_V2"


class NotebookReleaseDecisionType(StrEnum):
    PROMOTE = "PROMOTE"
    HOLD_DEFAULT_V1 = "HOLD_DEFAULT_V1"
    ROLLBACK = "ROLLBACK"


class NotebookEvidenceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class _FrozenRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class NotebookReleaseMetric(_FrozenRelease):
    name: str
    status: NotebookEvidenceStatus
    value: float | None
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)


class NotebookReleaseGuardrail(_FrozenRelease):
    name: str
    status: NotebookEvidenceStatus
    violations: int = Field(ge=0)


class NotebookReleaseEvidence(_FrozenRelease):
    evidence_id: str
    evaluator_version: str = NOTEBOOK_RELEASE_EVALUATOR_VERSION
    observed_stage: NotebookReleaseStage
    proposed_stage: NotebookReleaseStage
    golden_package_sha256: str
    artifact_set_sha256: str
    retriever_version: str
    reranker_version: str
    context_builder_version: str
    metrics: tuple[NotebookReleaseMetric, ...]
    guardrails: tuple[NotebookReleaseGuardrail, ...]
    production_observation: bool = False

    @model_validator(mode="after")
    def _identity(self) -> NotebookReleaseEvidence:
        if len({item.name for item in self.metrics}) != len(self.metrics):
            raise ValueError("release metric names must be unique")
        if len({item.name for item in self.guardrails}) != len(self.guardrails):
            raise ValueError("release guardrail names must be unique")
        payload = self.model_dump(mode="json", exclude={"evidence_id"})
        expected = "nbrelease-" + canonical_sha256(payload).removeprefix("sha256:")
        if self.evidence_id != expected:
            raise ValueError("Notebook release evidence id mismatch")
        return self


class NotebookReleaseDecision(_FrozenRelease):
    decision: NotebookReleaseDecisionType
    observed_stage: NotebookReleaseStage
    proposed_stage: NotebookReleaseStage
    failed_metrics: tuple[str, ...]
    failed_guardrails: tuple[str, ...]
    reason: str
    default_engine: str = "v1"
    side_effect_applied: bool = False


NOTEBOOK_RELEASE_THRESHOLDS: dict[str, tuple[str, float]] = {
    "cell_recall_at_10": ("min", 0.90),
    "output_error_recall_at_10": ("min", 0.90),
    "parameter_accuracy": ("min", 0.98),
    "producer_output_path_recall": ("min", 0.95),
    "execution_order_accuracy": ("min", 0.98),
    "dependency_path_recall": ("min", 0.80),
    "stale_output_detection": ("min", 0.95),
    "cell_match_accuracy": ("min", 0.95),
    "locator_accuracy": ("min", 0.98),
    "p95_latency_ms": ("max", 1_000.0),
}
NOTEBOOK_REQUIRED_GUARDRAILS = (
    "secret_leakage",
    "unauthorized_leakage",
    "reasoning_leakage",
)
_STAGE_ORDER = tuple(NotebookReleaseStage)


def build_notebook_release_evidence_v2(
    *,
    observed_stage: NotebookReleaseStage,
    proposed_stage: NotebookReleaseStage,
    golden_package_sha256: str,
    artifact_set_sha256: str,
    retriever_version: str,
    reranker_version: str,
    context_builder_version: str,
    metrics: tuple[NotebookReleaseMetric, ...],
    guardrails: tuple[NotebookReleaseGuardrail, ...],
    production_observation: bool = False,
) -> NotebookReleaseEvidence:
    values = {
        "evaluator_version": NOTEBOOK_RELEASE_EVALUATOR_VERSION,
        "observed_stage": observed_stage,
        "proposed_stage": proposed_stage,
        "golden_package_sha256": golden_package_sha256,
        "artifact_set_sha256": artifact_set_sha256,
        "retriever_version": retriever_version,
        "reranker_version": reranker_version,
        "context_builder_version": context_builder_version,
        "metrics": metrics,
        "guardrails": guardrails,
        "production_observation": production_observation,
    }
    draft = NotebookReleaseEvidence.model_construct(
        evidence_id="nbrelease-" + ("0" * 64),
        **values,
    )
    evidence_id = "nbrelease-" + canonical_sha256(
        draft.model_dump(mode="json", exclude={"evidence_id"})
    ).removeprefix("sha256:")
    return NotebookReleaseEvidence(evidence_id=evidence_id, **values)


def evaluate_notebook_release_v2(
    evidence: NotebookReleaseEvidence,
) -> NotebookReleaseDecision:
    observed_index = _STAGE_ORDER.index(evidence.observed_stage)
    proposed_index = _STAGE_ORDER.index(evidence.proposed_stage)
    invalid_transition = proposed_index != observed_index + 1
    metrics = {item.name: item for item in evidence.metrics}
    guardrails = {item.name: item for item in evidence.guardrails}
    failed_metrics: list[str] = []
    for name, (direction, threshold) in NOTEBOOK_RELEASE_THRESHOLDS.items():
        metric = metrics.get(name)
        if (
            metric is None
            or metric.status != NotebookEvidenceStatus.AVAILABLE
            or metric.value is None
            or metric.denominator <= 0
            or (direction == "min" and metric.value < threshold)
            or (direction == "max" and metric.value > threshold)
        ):
            failed_metrics.append(name)
    failed_guardrails = [
        name
        for name in NOTEBOOK_REQUIRED_GUARDRAILS
        if name not in guardrails
        or guardrails[name].status != NotebookEvidenceStatus.AVAILABLE
        or guardrails[name].violations != 0
    ]
    if invalid_transition:
        failed_guardrails.append("release-stage-transition")
    if (
        evidence.production_observation
        and evidence.observed_stage
        in {
            NotebookReleaseStage.CANARY_5,
            NotebookReleaseStage.CANARY_25,
            NotebookReleaseStage.OPT_IN_100,
            NotebookReleaseStage.DEFAULT_V2,
        }
        and (failed_metrics or failed_guardrails)
    ):
        decision = NotebookReleaseDecisionType.ROLLBACK
        reason = "deployed-stage-guardrail-or-quality-failure"
    elif failed_metrics or failed_guardrails or not evidence.production_observation:
        decision = NotebookReleaseDecisionType.HOLD_DEFAULT_V1
        reason = (
            "production-observation-unavailable"
            if not evidence.production_observation and not failed_metrics and not failed_guardrails
            else "release-evidence-incomplete-or-below-threshold"
        )
    else:
        decision = NotebookReleaseDecisionType.PROMOTE
        reason = "next-stage-evidence-qualified"
    return NotebookReleaseDecision(
        decision=decision,
        observed_stage=evidence.observed_stage,
        proposed_stage=evidence.proposed_stage,
        failed_metrics=tuple(sorted(set(failed_metrics))),
        failed_guardrails=tuple(sorted(set(failed_guardrails))),
        reason=reason,
    )


__all__ = [
    "NOTEBOOK_RELEASE_EVALUATOR_VERSION",
    "NOTEBOOK_RELEASE_THRESHOLDS",
    "NOTEBOOK_REQUIRED_GUARDRAILS",
    "NotebookEvidenceStatus",
    "NotebookReleaseDecision",
    "NotebookReleaseDecisionType",
    "NotebookReleaseEvidence",
    "NotebookReleaseGuardrail",
    "NotebookReleaseMetric",
    "NotebookReleaseStage",
    "build_notebook_release_evidence_v2",
    "evaluate_notebook_release_v2",
]
