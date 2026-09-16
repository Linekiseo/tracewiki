"""Side-effect-free Workspace shadow/canary/release evidence evaluator."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import canonical_sha256

WORKSPACE_RELEASE_EVALUATOR_VERSION = "workspace-release-evaluator-v2"


class WorkspaceReleaseStage(StrEnum):
    OFFLINE = "OFFLINE"
    ISOLATED_BASELINE = "ISOLATED_BASELINE"
    SHADOW_INTERNAL_100 = "SHADOW_INTERNAL_100"
    CANARY_5 = "CANARY_5"
    CANARY_25 = "CANARY_25"
    OPT_IN_100 = "OPT_IN_100"
    DEFAULT_V2 = "DEFAULT_V2"


class WorkspaceReleaseDecisionType(StrEnum):
    PROMOTE = "PROMOTE"
    HOLD_DEFAULT_V1 = "HOLD_DEFAULT_V1"
    ROLLBACK = "ROLLBACK"


class WorkspaceEvidenceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class _FrozenRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class WorkspaceReleaseMetric(_FrozenRelease):
    name: str
    status: WorkspaceEvidenceStatus
    value: float | None
    numerator: float = Field(ge=0)
    denominator: int = Field(ge=0)


class WorkspaceReleaseGuardrail(_FrozenRelease):
    name: str
    status: WorkspaceEvidenceStatus
    violations: int = Field(ge=0)


class WorkspaceReleaseEvidence(_FrozenRelease):
    evidence_id: str
    evaluator_version: str = WORKSPACE_RELEASE_EVALUATOR_VERSION
    observed_stage: WorkspaceReleaseStage
    proposed_stage: WorkspaceReleaseStage
    golden_package_sha256: str
    artifact_set_sha256: str
    retriever_version: str
    reranker_version: str
    context_builder_version: str
    metrics: tuple[WorkspaceReleaseMetric, ...]
    guardrails: tuple[WorkspaceReleaseGuardrail, ...]
    production_observation: bool = False

    @model_validator(mode="after")
    def _identity(self) -> WorkspaceReleaseEvidence:
        if len({item.name for item in self.metrics}) != len(self.metrics):
            raise ValueError("Workspace release metric names must be unique")
        if len({item.name for item in self.guardrails}) != len(self.guardrails):
            raise ValueError("Workspace release guardrail names must be unique")
        expected = "wsrelease-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"evidence_id"})
        ).removeprefix("sha256:")
        if self.evidence_id != expected:
            raise ValueError("Workspace release evidence identity mismatch")
        return self


class WorkspaceReleaseDecision(_FrozenRelease):
    decision: WorkspaceReleaseDecisionType
    observed_stage: WorkspaceReleaseStage
    proposed_stage: WorkspaceReleaseStage
    failed_metrics: tuple[str, ...]
    failed_guardrails: tuple[str, ...]
    reason: str
    rollback_sequence: tuple[str, ...]
    default_engine: str = "v1"
    side_effect_applied: bool = False


WORKSPACE_RELEASE_THRESHOLDS: dict[str, tuple[str, float]] = {
    "scope_resolution_accuracy": ("min", 0.98),
    "current_state_accuracy": ("min", 0.99),
    "temporal_accuracy": ("min", 0.97),
    "blocker_precision": ("min", 0.95),
    "evidence_coverage_precision": ("min", 0.95),
    "acceptance_gate_accuracy": ("min", 1.0),
    "authority_classification_accuracy": ("min", 1.0),
    "unsafe_mutation_rate": ("max", 0.0),
    "acl_leakage_rate": ("max", 0.0),
    "p95_latency_ms": ("max", 1_000.0),
}
WORKSPACE_REQUIRED_GUARDRAILS = (
    "secret_leakage",
    "unauthorized_leakage",
    "reasoning_leakage",
    "unsafe_mutation",
)
WORKSPACE_ROLLBACK_SEQUENCE = (
    "disable-workspace-v2-selection",
    "restore-last-known-good-workspace-generation",
    "invalidate-workspace-context-and-intelligence-cache",
    "verify-v1-control-plane-health",
)
_STAGE_ORDER = tuple(WorkspaceReleaseStage)


def build_workspace_release_evidence_v2(
    *,
    observed_stage: WorkspaceReleaseStage,
    proposed_stage: WorkspaceReleaseStage,
    golden_package_sha256: str,
    artifact_set_sha256: str,
    retriever_version: str,
    reranker_version: str,
    context_builder_version: str,
    metrics: tuple[WorkspaceReleaseMetric, ...],
    guardrails: tuple[WorkspaceReleaseGuardrail, ...],
    production_observation: bool = False,
) -> WorkspaceReleaseEvidence:
    values = {
        "evaluator_version": WORKSPACE_RELEASE_EVALUATOR_VERSION,
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
    draft = WorkspaceReleaseEvidence.model_construct(
        evidence_id="wsrelease-" + ("0" * 64),
        **values,
    )
    evidence_id = "wsrelease-" + canonical_sha256(
        draft.model_dump(mode="json", exclude={"evidence_id"})
    ).removeprefix("sha256:")
    return WorkspaceReleaseEvidence(evidence_id=evidence_id, **values)


def evaluate_workspace_release_v2(
    evidence: WorkspaceReleaseEvidence,
) -> WorkspaceReleaseDecision:
    observed_index = _STAGE_ORDER.index(evidence.observed_stage)
    proposed_index = _STAGE_ORDER.index(evidence.proposed_stage)
    metrics = {item.name: item for item in evidence.metrics}
    guardrails = {item.name: item for item in evidence.guardrails}
    failed_metrics: list[str] = []
    for name, (direction, threshold) in WORKSPACE_RELEASE_THRESHOLDS.items():
        metric = metrics.get(name)
        if (
            metric is None
            or metric.status != WorkspaceEvidenceStatus.AVAILABLE
            or metric.value is None
            or metric.denominator <= 0
            or (direction == "min" and metric.value < threshold)
            or (direction == "max" and metric.value > threshold)
        ):
            failed_metrics.append(name)
    failed_guardrails = [
        name
        for name in WORKSPACE_REQUIRED_GUARDRAILS
        if name not in guardrails
        or guardrails[name].status != WorkspaceEvidenceStatus.AVAILABLE
        or guardrails[name].violations != 0
    ]
    if proposed_index != observed_index + 1:
        failed_guardrails.append("release-stage-transition")
    deployed = evidence.observed_stage in {
        WorkspaceReleaseStage.CANARY_5,
        WorkspaceReleaseStage.CANARY_25,
        WorkspaceReleaseStage.OPT_IN_100,
        WorkspaceReleaseStage.DEFAULT_V2,
    }
    if evidence.production_observation and deployed and (failed_metrics or failed_guardrails):
        decision = WorkspaceReleaseDecisionType.ROLLBACK
        reason = "deployed-stage-guardrail-or-quality-failure"
    elif failed_metrics or failed_guardrails or not evidence.production_observation:
        decision = WorkspaceReleaseDecisionType.HOLD_DEFAULT_V1
        reason = (
            "production-observation-unavailable"
            if not evidence.production_observation and not failed_metrics and not failed_guardrails
            else "release-evidence-incomplete-or-below-threshold"
        )
    else:
        decision = WorkspaceReleaseDecisionType.PROMOTE
        reason = "next-stage-evidence-qualified"
    return WorkspaceReleaseDecision(
        decision=decision,
        observed_stage=evidence.observed_stage,
        proposed_stage=evidence.proposed_stage,
        failed_metrics=tuple(sorted(set(failed_metrics))),
        failed_guardrails=tuple(sorted(set(failed_guardrails))),
        reason=reason,
        rollback_sequence=WORKSPACE_ROLLBACK_SEQUENCE,
    )


__all__ = [
    "WORKSPACE_RELEASE_EVALUATOR_VERSION",
    "WORKSPACE_RELEASE_THRESHOLDS",
    "WORKSPACE_REQUIRED_GUARDRAILS",
    "WORKSPACE_ROLLBACK_SEQUENCE",
    "WorkspaceEvidenceStatus",
    "WorkspaceReleaseDecision",
    "WorkspaceReleaseDecisionType",
    "WorkspaceReleaseEvidence",
    "WorkspaceReleaseGuardrail",
    "WorkspaceReleaseMetric",
    "WorkspaceReleaseStage",
    "build_workspace_release_evidence_v2",
    "evaluate_workspace_release_v2",
]
