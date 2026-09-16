"""Side-effect-free Document shadow/canary/release evidence evaluator."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import canonical_sha256

DOCUMENT_RELEASE_EVALUATOR_VERSION = "document-release-evaluator-v2"


class DocumentReleaseStage(StrEnum):
    OFFLINE = "OFFLINE"
    ISOLATED_BASELINE = "ISOLATED_BASELINE"
    SHADOW_INTERNAL_100 = "SHADOW_INTERNAL_100"
    CANARY_5 = "CANARY_5"
    CANARY_25 = "CANARY_25"
    OPT_IN_100 = "OPT_IN_100"
    DEFAULT_V2 = "DEFAULT_V2"


DOCUMENT_RELEASE_STAGES = tuple(DocumentReleaseStage)


class DocumentEvidenceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class DocumentReleaseAction(StrEnum):
    HOLD_DEFAULT_V1 = "HOLD_DEFAULT_V1"
    PROMOTE = "PROMOTE"
    ROLLBACK = "ROLLBACK"


class _FrozenRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DocumentReleaseMetric(_FrozenRelease):
    name: str
    status: DocumentEvidenceStatus
    numerator: float
    denominator: int = Field(ge=1)
    value: float
    threshold: float
    direction: str = Field(pattern=r"^(min|max)$")


class DocumentReleaseGuardrail(_FrozenRelease):
    name: str
    status: DocumentEvidenceStatus
    violations: int = Field(ge=0)


class DocumentReleaseEvidence(_FrozenRelease):
    evidence_id: str
    evaluator_version: str = DOCUMENT_RELEASE_EVALUATOR_VERSION
    observed_stage: DocumentReleaseStage
    proposed_stage: DocumentReleaseStage
    artifact_set_sha256: str
    golden_package_sha256: str
    retriever_version: str
    reranker_version: str
    context_builder_version: str
    production_observation: bool
    metrics: tuple[DocumentReleaseMetric, ...]
    guardrails: tuple[DocumentReleaseGuardrail, ...]

    @model_validator(mode="after")
    def _identity(self) -> DocumentReleaseEvidence:
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"evidence_id"}))
        if self.evidence_id != "docrelease-" + expected.removeprefix("sha256:")[:64]:
            raise ValueError("document release evidence identity mismatch")
        return self


class DocumentReleaseDecision(_FrozenRelease):
    action: DocumentReleaseAction
    observed_stage: DocumentReleaseStage
    proposed_stage: DocumentReleaseStage
    default_engine: str = "v1"
    reason: str
    failed_metrics: tuple[str, ...]
    failed_guardrails: tuple[str, ...]
    side_effect_applied: bool = False
    rollback_sequence: tuple[str, ...] = (
        "disable-document-v2-selection",
        "restore-last-known-good-generation",
        "invalidate-derived-context-cache",
        "verify-v1-health",
    )


def _next_stage(stage: DocumentReleaseStage) -> DocumentReleaseStage | None:
    index = DOCUMENT_RELEASE_STAGES.index(stage)
    return None if index + 1 == len(DOCUMENT_RELEASE_STAGES) else DOCUMENT_RELEASE_STAGES[index + 1]


def evaluate_document_release_v2(
    evidence: DocumentReleaseEvidence,
) -> DocumentReleaseDecision:
    evidence = DocumentReleaseEvidence.model_validate(
        evidence.model_dump(mode="python", round_trip=True)
    )
    next_stage = _next_stage(evidence.observed_stage)
    no_skip = next_stage == evidence.proposed_stage
    failed_metrics = tuple(
        sorted(
            metric.name
            for metric in evidence.metrics
            if metric.status != DocumentEvidenceStatus.AVAILABLE
            or (metric.direction == "min" and metric.value < metric.threshold)
            or (metric.direction == "max" and metric.value > metric.threshold)
        )
    )
    failed_guardrails = tuple(
        sorted(
            item.name
            for item in evidence.guardrails
            if item.status != DocumentEvidenceStatus.AVAILABLE or item.violations
        )
    )
    if not no_skip:
        action = (
            DocumentReleaseAction.ROLLBACK
            if DOCUMENT_RELEASE_STAGES.index(evidence.observed_stage)
            >= DOCUMENT_RELEASE_STAGES.index(DocumentReleaseStage.CANARY_5)
            else DocumentReleaseAction.HOLD_DEFAULT_V1
        )
        reason = "release-stage-transition-is-not-next"
    elif failed_metrics or failed_guardrails:
        action = (
            DocumentReleaseAction.ROLLBACK
            if DOCUMENT_RELEASE_STAGES.index(evidence.observed_stage)
            >= DOCUMENT_RELEASE_STAGES.index(DocumentReleaseStage.CANARY_5)
            else DocumentReleaseAction.HOLD_DEFAULT_V1
        )
        reason = "release-evidence-incomplete-or-below-threshold"
    elif (
        DOCUMENT_RELEASE_STAGES.index(evidence.proposed_stage)
        > DOCUMENT_RELEASE_STAGES.index(DocumentReleaseStage.ISOLATED_BASELINE)
        and not evidence.production_observation
    ):
        action = DocumentReleaseAction.HOLD_DEFAULT_V1
        reason = "production-observation-required"
    elif evidence.proposed_stage == DocumentReleaseStage.DEFAULT_V2:
        action = DocumentReleaseAction.HOLD_DEFAULT_V1
        reason = "default-v2-requires-external-release-authorization"
    else:
        action = DocumentReleaseAction.PROMOTE
        reason = "next-engineering-stage-evidence-passed"
    return DocumentReleaseDecision(
        action=action,
        observed_stage=evidence.observed_stage,
        proposed_stage=evidence.proposed_stage,
        reason=reason,
        failed_metrics=failed_metrics,
        failed_guardrails=failed_guardrails,
    )
