"""Experiment E5 routing, security, observability, and release governance."""

from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts_v2 import canonical_sha256_v2

EXPERIMENT_ENGINE_HEADER = "X-RAG-Experiment-Engine"
EXPERIMENT_ROUTER_VERSION = "experiment-engine-router-v2"
EXPERIMENT_SECURITY_POLICY_VERSION = "experiment-security-policy-v2"
EXPERIMENT_RELEASE_POLICY_VERSION = "experiment-release-policy-v2"

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,511}")
_SAFE_REASON_RE = re.compile(r"[a-z][a-z0-9_]{0,79}")
_SECRET_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S{8,}))"
)


class ExperimentEngineV2(StrEnum):
    V1 = "v1"
    SHADOW = "shadow"
    CANARY = "canary"
    V2 = "v2"


class ExperimentReleaseStageV2(StrEnum):
    OFFLINE = "offline"
    SHADOW_INTERNAL_100 = "shadow_internal_100"
    CANARY_5 = "canary_5"
    CANARY_25 = "canary_25"
    OPT_IN_100 = "opt_in_100"
    DEFAULT_V2 = "default_v2"


class ExperimentReleaseDecisionV2(StrEnum):
    HOLD_DEFAULT_V1 = "hold_default_v1"
    PROMOTE_NEXT_STAGE = "promote_next_stage"
    ROLLBACK_TO_V1 = "rollback_to_v1"


class EvidenceAvailabilityV2(StrEnum):
    AVAILABLE = "available"
    PROVISIONAL = "provisional"
    UNAVAILABLE = "unavailable"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ExperimentRouteRequestV2(_Frozen):
    project_id: str
    request_id: str
    requested_engine: ExperimentEngineV2 | None = None
    project_override: ExperimentEngineV2 | None = None
    override_authorized: bool = False
    canary_percent: int = Field(default=0, ge=0, le=100)
    shadow_enabled: bool = False

    @model_validator(mode="after")
    def _ids(self) -> ExperimentRouteRequestV2:
        if _SAFE_ID_RE.fullmatch(self.project_id) is None:
            raise ValueError("project identity is invalid")
        if _SAFE_ID_RE.fullmatch(self.request_id) is None:
            raise ValueError("request identity is invalid")
        if self.project_override is not None and not self.override_authorized:
            raise ValueError("project override is not authorized")
        return self


class ExperimentRouteDecisionV2(_Frozen):
    engine: ExperimentEngineV2
    response_engine: ExperimentEngineV2
    execute_v1: bool
    execute_v2: bool
    canary_bucket: int = Field(ge=0, le=99)
    reason_code: str
    router_version: str = EXPERIMENT_ROUTER_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentRouteDecisionV2:
        if _SAFE_REASON_RE.fullmatch(self.reason_code) is None:
            raise ValueError("route reason is unsafe")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("route decision digest mismatch")
        return self


class ExperimentTraceV2(_Frozen):
    project_sha256: str
    request_sha256: str
    engine_requested: str
    engine_selected: str
    response_engine: str
    shadow_outcome: str | None
    fallback_reason: str | None
    generation_sha256: str | None
    candidate_count: int = Field(ge=0)
    elapsed_ms: float = Field(ge=0)
    security_policy_version: str = EXPERIMENT_SECURITY_POLICY_VERSION

    @model_validator(mode="after")
    def _safe(self) -> ExperimentTraceV2:
        if _SECRET_RE.search(self.model_dump_json()):
            raise ValueError("trace contains a secret")
        if (
            self.fallback_reason is not None
            and _SAFE_REASON_RE.fullmatch(self.fallback_reason) is None
        ):
            raise ValueError("trace fallback reason is unsafe")
        return self


class ExperimentReleaseMetricV2(_Frozen):
    name: str
    numerator: float
    denominator: float
    value: float
    threshold: float
    comparison: Literal["gte", "lte", "eq"]
    availability: EvidenceAvailabilityV2
    source_evidence_sha256: str

    @model_validator(mode="after")
    def _consistent(self) -> ExperimentReleaseMetricV2:
        if self.denominator <= 0:
            raise ValueError("release metric denominator must be positive")
        expected = self.numerator / self.denominator
        if not math.isclose(expected, self.value, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("release metric numerator/denominator mismatch")
        return self

    @property
    def passes(self) -> bool:
        return self.availability is EvidenceAvailabilityV2.AVAILABLE and (
            self.value >= self.threshold
            if self.comparison == "gte"
            else self.value <= self.threshold
            if self.comparison == "lte"
            else math.isclose(self.value, self.threshold, abs_tol=1e-12)
        )


class ExperimentReleaseEvidenceV2(_Frozen):
    evidence_id: str
    project_id: str
    stage: ExperimentReleaseStageV2
    previous_evidence_sha256: str | None
    baseline_artifact_sha256: str
    treatment_artifact_sha256: str | None
    metrics: tuple[ExperimentReleaseMetricV2, ...]
    secret_leakage_count: int = Field(ge=0)
    acl_leakage_count: int = Field(ge=0)
    truth_drift_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)
    storage_bytes: int | None = Field(default=None, ge=0)
    rollback_rehearsed: bool
    synthetic: bool
    production_observation: bool = False
    content_sha256: str
    policy_version: str = EXPERIMENT_RELEASE_POLICY_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentReleaseEvidenceV2:
        payload = self.model_dump(
            mode="json",
            exclude={"evidence_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if (
            self.content_sha256 != digest
            or self.evidence_id != "experimentevidence-" + digest.removeprefix("sha256:")
        ):
            raise ValueError("release evidence identity mismatch")
        return self


class ExperimentReleaseResultV2(_Frozen):
    stage: ExperimentReleaseStageV2
    decision: ExperimentReleaseDecisionV2
    next_stage: ExperimentReleaseStageV2 | None
    reasons: tuple[str, ...]
    default_engine: Literal["v1"] = "v1"
    quality_qualified: bool
    rollback_sequence: tuple[str, ...]
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentReleaseResultV2:
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("release result digest mismatch")
        if self.default_engine != "v1":
            raise ValueError("repository engineering cannot change default engine")
        return self


def route_experiment_engine_v2(
    request: ExperimentRouteRequestV2,
) -> ExperimentRouteDecisionV2:
    bucket = (
        int.from_bytes(
            hashlib.sha256(f"{request.project_id}\x1f{request.request_id}".encode()).digest()[:8],
            "big",
        )
        % 100
    )
    selected = (
        request.requested_engine
        or request.project_override
        or (
            ExperimentEngineV2.CANARY
            if request.canary_percent > 0 and bucket < request.canary_percent
            else ExperimentEngineV2.SHADOW
            if request.shadow_enabled
            else ExperimentEngineV2.V1
        )
    )
    if selected is ExperimentEngineV2.V1:
        execute_v1, execute_v2, response, reason = True, False, selected, "default_v1"
    elif selected is ExperimentEngineV2.SHADOW:
        execute_v1, execute_v2, response, reason = True, True, ExperimentEngineV2.V1, "shadow"
    elif selected in {ExperimentEngineV2.CANARY, ExperimentEngineV2.V2}:
        execute_v1, execute_v2, response, reason = (
            True,
            True,
            ExperimentEngineV2.V2,
            ("stable_canary" if selected is ExperimentEngineV2.CANARY else "explicit_v2"),
        )
    else:  # pragma: no cover - exhaustive enum defense
        raise ValueError("engine selection is invalid")
    payload = {
        "engine": selected,
        "response_engine": response,
        "execute_v1": execute_v1,
        "execute_v2": execute_v2,
        "canary_bucket": bucket,
        "reason_code": reason,
        "router_version": EXPERIMENT_ROUTER_VERSION,
    }
    return ExperimentRouteDecisionV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            {
                key: value.value if isinstance(value, StrEnum) else value
                for key, value in payload.items()
            }
        ),
    )


def execute_experiment_route_v2[T](
    decision: ExperimentRouteDecisionV2,
    *,
    legacy: Callable[[], T],
    v2: Callable[[], T],
) -> tuple[T, str | None, str | None]:
    """Run legacy once and V2 at most once; shadow never changes the response."""

    legacy_result = legacy()
    if not decision.execute_v2:
        return legacy_result, None, None
    try:
        v2_result = v2()
    except Exception as exc:
        code = _exception_code(exc)
        return legacy_result, "v2_failed", code
    if decision.response_engine is ExperimentEngineV2.V1:
        return legacy_result, "shadow_completed", None
    return v2_result, None, None


def validate_local_source_root_v2(path: Path, *, allowed_root: Path) -> Path:
    if not path.is_absolute() or not allowed_root.is_absolute():
        raise ValueError("source path and allowed root must be absolute")
    if allowed_root.is_symlink() or path.is_symlink():
        raise ValueError("source path policy rejects symlinks")
    root = allowed_root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("source path escapes allowlisted root")
    if resolved.name == "evidence-rag.sqlite3" or resolved.name.endswith(("-wal", "-shm")):
        raise ValueError("formal database and sidecars are forbidden")
    return resolved


def validate_remote_mlflow_url_v2(
    url: str,
    *,
    allowed_hosts: frozenset[str],
) -> str:
    candidate = url
    for _ in range(4):
        decoded = unquote(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    parts = urlsplit(candidate)
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.port
        not in {
            None,
            443,
        }
    ):
        raise ValueError("remote MLflow URL is not HTTPS-safe")
    host = (parts.hostname or "").casefold().rstrip(".")
    if host not in {item.casefold().rstrip(".") for item in allowed_hosts}:
        raise ValueError("remote MLflow host is not allowlisted")
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ValueError("private or special remote address is forbidden")
    if parts.fragment:
        raise ValueError("remote MLflow URL fragment is forbidden")
    return candidate


def build_safe_experiment_trace_v2(
    *,
    project_id: str,
    request_id: str,
    decision: ExperimentRouteDecisionV2,
    shadow_outcome: str | None,
    fallback_reason: str | None,
    generation_id: str | None,
    candidate_count: int,
    elapsed_ms: float,
) -> ExperimentTraceV2:
    return ExperimentTraceV2(
        project_sha256=canonical_sha256_v2(project_id),
        request_sha256=canonical_sha256_v2(request_id),
        engine_requested=decision.engine.value,
        engine_selected=decision.engine.value,
        response_engine=decision.response_engine.value,
        shadow_outcome=shadow_outcome,
        fallback_reason=fallback_reason,
        generation_sha256=(
            canonical_sha256_v2(generation_id) if generation_id is not None else None
        ),
        candidate_count=candidate_count,
        elapsed_ms=elapsed_ms,
    )


_STAGES = tuple(ExperimentReleaseStageV2)
_ROLLBACK_SEQUENCE = (
    "route_all_requests_to_v1",
    "disable_project_and_request_v2_overrides",
    "stop_new_v2_generation_publish",
    "activate_last_known_good_generation_for_audit_only",
    "verify_v1_response_contract",
    "retain_failed_evidence_and_additive_tables",
)


def evaluate_experiment_release_v2(
    evidence: ExperimentReleaseEvidenceV2,
    *,
    deployed_stage: ExperimentReleaseStageV2,
) -> ExperimentReleaseResultV2:
    reasons: list[str] = []
    decision = ExperimentReleaseDecisionV2.HOLD_DEFAULT_V1
    next_stage: ExperimentReleaseStageV2 | None = None
    if evidence.stage is not deployed_stage:
        reasons.append("trusted_stage_mismatch")
        if _STAGES.index(deployed_stage) >= _STAGES.index(ExperimentReleaseStageV2.CANARY_5):
            decision = ExperimentReleaseDecisionV2.ROLLBACK_TO_V1
    if evidence.synthetic:
        reasons.append("synthetic_evidence_not_release_eligible")
    if not evidence.production_observation:
        reasons.append("production_observation_unavailable")
    for field, count in (
        ("secret_leakage", evidence.secret_leakage_count),
        ("acl_leakage", evidence.acl_leakage_count),
        ("truth_drift", evidence.truth_drift_count),
    ):
        if count:
            reasons.append(f"{field}_nonzero")
            if _STAGES.index(deployed_stage) >= _STAGES.index(ExperimentReleaseStageV2.CANARY_5):
                decision = ExperimentReleaseDecisionV2.ROLLBACK_TO_V1
    if not evidence.rollback_rehearsed:
        reasons.append("rollback_not_rehearsed")
    if not evidence.metrics:
        reasons.append("release_metrics_missing")
    for metric in evidence.metrics:
        if metric.availability is not EvidenceAvailabilityV2.AVAILABLE:
            reasons.append(f"metric_{metric.name}_{metric.availability.value}")
        elif not metric.passes:
            reasons.append(f"metric_{metric.name}_threshold_failed")
    if not reasons and evidence.stage is not ExperimentReleaseStageV2.DEFAULT_V2:
        decision = ExperimentReleaseDecisionV2.PROMOTE_NEXT_STAGE
        next_stage = _STAGES[_STAGES.index(evidence.stage) + 1]
    elif not reasons:
        reasons.append("default_v2_requires_external_release_authority")
    payload = {
        "stage": evidence.stage,
        "decision": decision,
        "next_stage": next_stage,
        "reasons": tuple(sorted(reasons)),
        "default_engine": "v1",
        "quality_qualified": False,
        "rollback_sequence": _ROLLBACK_SEQUENCE,
    }
    return ExperimentReleaseResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            {
                key: value.value if isinstance(value, StrEnum) else value
                for key, value in payload.items()
            }
        ),
    )


def build_experiment_release_evidence_v2(
    *,
    project_id: str,
    stage: ExperimentReleaseStageV2,
    previous_evidence_sha256: str | None,
    baseline_artifact_sha256: str,
    treatment_artifact_sha256: str | None,
    metrics: tuple[ExperimentReleaseMetricV2, ...],
    secret_leakage_count: int,
    acl_leakage_count: int,
    truth_drift_count: int,
    fallback_count: int,
    p95_latency_ms: float | None,
    storage_bytes: int | None,
    rollback_rehearsed: bool,
    synthetic: bool,
    production_observation: bool = False,
) -> ExperimentReleaseEvidenceV2:
    payload = {
        "project_id": project_id,
        "stage": stage,
        "previous_evidence_sha256": previous_evidence_sha256,
        "baseline_artifact_sha256": baseline_artifact_sha256,
        "treatment_artifact_sha256": treatment_artifact_sha256,
        "metrics": metrics,
        "secret_leakage_count": secret_leakage_count,
        "acl_leakage_count": acl_leakage_count,
        "truth_drift_count": truth_drift_count,
        "fallback_count": fallback_count,
        "p95_latency_ms": p95_latency_ms,
        "storage_bytes": storage_bytes,
        "rollback_rehearsed": rollback_rehearsed,
        "synthetic": synthetic,
        "production_observation": production_observation,
        "policy_version": EXPERIMENT_RELEASE_POLICY_VERSION,
    }
    normalized = ExperimentReleaseEvidenceV2.model_construct(
        evidence_id="pending",
        content_sha256="pending",
        **payload,
    ).model_dump(
        mode="json",
        exclude={"evidence_id", "content_sha256"},
    )
    digest = canonical_sha256_v2(normalized)
    return ExperimentReleaseEvidenceV2(
        evidence_id="experimentevidence-" + digest.removeprefix("sha256:"),
        content_sha256=digest,
        **payload,
    )


def current_experiment_release_hold_v2() -> ExperimentReleaseResultV2:
    """Represent current repository truth: engineering exists, quality does not qualify."""

    payload = {
        "stage": ExperimentReleaseStageV2.OFFLINE,
        "decision": ExperimentReleaseDecisionV2.HOLD_DEFAULT_V1,
        "next_stage": None,
        "reasons": (
            "baseline_non_qualified",
            "no_verified_treatment_artifact",
            "production_canary_not_authorized",
        ),
        "default_engine": "v1",
        "quality_qualified": False,
        "rollback_sequence": _ROLLBACK_SEQUENCE,
    }
    return ExperimentReleaseResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            {
                key: value.value if isinstance(value, StrEnum) else value
                for key, value in payload.items()
            }
        ),
    )


def _exception_code(exc: Exception) -> str:
    name = type(exc).__name__.casefold()
    allowed = {
        "permissionerror": "permission_denied",
        "timeouterror": "timeout",
        "valueerror": "invalid_v2_result",
    }
    return allowed.get(name, "internal_v2_error")


__all__ = [
    "EXPERIMENT_ENGINE_HEADER",
    "EXPERIMENT_RELEASE_POLICY_VERSION",
    "EXPERIMENT_ROUTER_VERSION",
    "EXPERIMENT_SECURITY_POLICY_VERSION",
    "EvidenceAvailabilityV2",
    "ExperimentEngineV2",
    "ExperimentReleaseDecisionV2",
    "ExperimentReleaseEvidenceV2",
    "ExperimentReleaseMetricV2",
    "ExperimentReleaseResultV2",
    "ExperimentReleaseStageV2",
    "ExperimentRouteDecisionV2",
    "ExperimentRouteRequestV2",
    "ExperimentTraceV2",
    "build_safe_experiment_trace_v2",
    "build_experiment_release_evidence_v2",
    "current_experiment_release_hold_v2",
    "evaluate_experiment_release_v2",
    "execute_experiment_route_v2",
    "route_experiment_engine_v2",
    "validate_local_source_root_v2",
    "validate_remote_mlflow_url_v2",
]
