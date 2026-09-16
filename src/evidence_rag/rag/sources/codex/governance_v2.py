"""Codex append cursor, privacy propagation, and side-effect-free release control."""

from __future__ import annotations

import hashlib
import math
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import canonical_sha256
from .store_v2 import CodexV2Store

CODEX_APPEND_CURSOR_VERSION = "codex-append-cursor-v2"
CODEX_PRIVACY_TOMBSTONE_VERSION = "codex-privacy-tombstone-v2"
CODEX_RELEASE_EVALUATOR_VERSION = "codex-release-evaluator-v2"
CODEX_SHADOW_AGGREGATOR_VERSION = "codex-shadow-aggregator-v2"
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")


class _FrozenGovernance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CodexAppendDisposition(StrEnum):
    INITIAL = "INITIAL"
    APPENDED = "APPENDED"
    PARTIAL_ONLY = "PARTIAL_ONLY"
    UNCHANGED = "UNCHANGED"
    FULL_REINGEST_REQUIRED = "FULL_REINGEST_REQUIRED"


class CodexAppendReason(StrEnum):
    INITIAL_COMPLETE_PREFIX = "initial_complete_prefix"
    COMPLETE_LINES_APPENDED = "complete_lines_appended"
    PARTIAL_LINE_NOT_ADVANCED = "partial_line_not_advanced"
    SOURCE_UNCHANGED = "source_unchanged"
    SOURCE_TRUNCATED = "source_truncated"
    SOURCE_REPLACED = "source_replaced"
    VERSION_CHANGED = "version_changed"


class CodexAppendCursorV2(_FrozenGovernance):
    cursor_id: str
    project_id: str
    source_id: str
    generation_id: str
    acl_ref: str
    observed_size: int = Field(ge=0)
    observed_sha256: str
    published_offset: int = Field(ge=0)
    published_prefix_sha256: str
    last_complete_line_sha256: str | None
    partial_bytes: int = Field(ge=0)
    adapter_version: str
    cleaning_version: str
    builder_version: str
    previous_cursor_id: str | None
    disposition: CodexAppendDisposition
    reason: CodexAppendReason
    cursor_sha256: str
    version: str = CODEX_APPEND_CURSOR_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexAppendCursorV2:
        if any(
            _SAFE_ID_RE.fullmatch(value) is None
            for value in (
                self.project_id,
                self.source_id,
                self.generation_id,
                self.acl_ref,
                self.adapter_version,
                self.cleaning_version,
                self.builder_version,
            )
        ):
            raise ValueError("append cursor authority is not portable")
        if any(
            _SHA256_RE.fullmatch(value) is None
            for value in (
                self.observed_sha256,
                self.published_prefix_sha256,
                self.cursor_sha256,
            )
        ) or (
            self.last_complete_line_sha256 is not None
            and _SHA256_RE.fullmatch(self.last_complete_line_sha256) is None
        ):
            raise ValueError("append cursor digest is not canonical")
        if self.published_offset > self.observed_size:
            raise ValueError("append cursor advances beyond observed bytes")
        if self.partial_bytes != self.observed_size - self.published_offset:
            raise ValueError("append cursor partial byte count mismatch")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"cursor_id", "cursor_sha256"})
        )
        expected_id = "codexcursor-" + expected.removeprefix("sha256:")
        if self.cursor_sha256 != expected or self.cursor_id != expected_id:
            raise ValueError("append cursor identity mismatch")
        return self


class CodexPrivacyTombstoneV2(_FrozenGovernance):
    tombstone_id: str
    project_id: str
    source_id: str
    generation_id: str
    acl_ref: str
    reason_code: str
    source_cursor_id: str | None
    tombstone_sha256: str
    version: str = CODEX_PRIVACY_TOMBSTONE_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexPrivacyTombstoneV2:
        if any(
            _SAFE_ID_RE.fullmatch(value) is None
            for value in (
                self.project_id,
                self.source_id,
                self.generation_id,
                self.acl_ref,
                self.reason_code,
            )
        ):
            raise ValueError("privacy tombstone authority is not portable")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"tombstone_sha256", "tombstone_id"})
        )
        expected_id = "codextomb-" + expected.removeprefix("sha256:")
        if self.tombstone_sha256 != expected or self.tombstone_id != expected_id:
            raise ValueError("privacy tombstone identity mismatch")
        return self


class CodexReleaseStage(StrEnum):
    OFFLINE = "OFFLINE"
    SHADOW_INTERNAL_100 = "SHADOW_INTERNAL_100"
    CANARY_5 = "CANARY_5"
    CANARY_25 = "CANARY_25"
    OPT_IN_100 = "OPT_IN_100"
    DEFAULT_V2 = "DEFAULT_V2"


class CodexReleaseDecisionType(StrEnum):
    PROMOTE = "PROMOTE"
    HOLD_DEFAULT_V1 = "HOLD_DEFAULT_V1"
    ROLLBACK = "ROLLBACK"


class CodexEvidenceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class CodexReleaseMetricV2(_FrozenGovernance):
    name: str
    status: CodexEvidenceStatus
    numerator: float = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None

    @model_validator(mode="after")
    def _availability(self) -> CodexReleaseMetricV2:
        if self.status is CodexEvidenceStatus.UNAVAILABLE:
            if self.denominator != 0 or self.numerator != 0 or self.value is not None:
                raise ValueError("unavailable release metric must have an empty denominator")
            return self
        if self.denominator <= 0 or self.value is None:
            raise ValueError("available/provisional release metric requires observations")
        if not math.isclose(
            self.numerator / self.denominator,
            self.value,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError("release metric numerator does not reproduce its value")
        return self


class CodexReleaseGuardrailV2(_FrozenGovernance):
    name: str
    status: CodexEvidenceStatus
    violations: int = Field(ge=0)


class CodexReleaseEvidenceV2(_FrozenGovernance):
    evidence_id: str
    observed_stage: CodexReleaseStage
    proposed_stage: CodexReleaseStage
    artifact_set_sha256: str
    golden_package_sha256: str
    component_set_sha256: str
    metrics: tuple[CodexReleaseMetricV2, ...]
    guardrails: tuple[CodexReleaseGuardrailV2, ...]
    production_observation: bool
    treatment_result_available: bool
    rollback_rehearsed: bool
    evaluator_version: str = CODEX_RELEASE_EVALUATOR_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexReleaseEvidenceV2:
        if len({item.name for item in self.metrics}) != len(self.metrics):
            raise ValueError("release metric names must be unique")
        if len({item.name for item in self.guardrails}) != len(self.guardrails):
            raise ValueError("release guardrail names must be unique")
        if any(
            _SHA256_RE.fullmatch(value) is None
            for value in (
                self.artifact_set_sha256,
                self.golden_package_sha256,
                self.component_set_sha256,
            )
        ):
            raise ValueError("release authority digest is not canonical")
        expected = "codexrelease-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"evidence_id"})
        ).removeprefix("sha256:")
        if self.evidence_id != expected:
            raise ValueError("release evidence identity mismatch")
        return self


class CodexReleaseDecisionV2(_FrozenGovernance):
    decision: CodexReleaseDecisionType
    observed_stage: CodexReleaseStage
    proposed_stage: CodexReleaseStage
    failed_metrics: tuple[str, ...]
    failed_guardrails: tuple[str, ...]
    reason: str
    rollback_sequence: tuple[str, ...]
    default_engine: str = "v1"
    side_effect_applied: bool = False


class CodexEngineSelectionV2(_FrozenGovernance):
    engine: str
    shadow_v2: bool
    bucket: int = Field(ge=0, le=99)
    reason: str
    override_authorized: bool = False
    circuit_open: bool = False
    default_engine: str = "v1"
    side_effect_applied: bool = False


class CodexShadowObservationV2(_FrozenGovernance):
    request_key_sha256: str
    stage: CodexReleaseStage
    v1_result_count: int = Field(ge=0)
    v2_result_count: int = Field(ge=0)
    overlap_count: int = Field(ge=0)
    v1_latency_ms: float = Field(ge=0)
    v2_latency_ms: float = Field(ge=0)
    fallback: bool
    secret_leakage: int = Field(default=0, ge=0)
    acl_leakage: int = Field(default=0, ge=0)


class CodexShadowAggregateV2(_FrozenGovernance):
    count: int = Field(ge=0)
    overlap_numerator: int = Field(ge=0)
    overlap_denominator: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    secret_leakage: int = Field(ge=0)
    acl_leakage: int = Field(ge=0)
    mean_v1_latency_ms: float = Field(ge=0)
    mean_v2_latency_ms: float = Field(ge=0)
    observations_sha256: str
    version: str = CODEX_SHADOW_AGGREGATOR_VERSION


class CodexCircuitStatus(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"


class CodexCircuitDecisionV2(_FrozenGovernance):
    status: CodexCircuitStatus
    reasons: tuple[str, ...]
    observation_count: int = Field(ge=0)
    aggregate_sha256: str


class CodexExecutionOutcomeV2(_FrozenGovernance):
    response: Any
    served_engine: str
    v1_calls: int = Field(ge=0, le=1)
    v2_calls: int = Field(ge=0, le=1)
    fallback: bool
    shadow_executed: bool
    error_code: str | None


CODEX_RELEASE_THRESHOLDS: dict[str, tuple[str, float]] = {
    "thread_recall_at_5": ("min", 0.90),
    "episode_recall_at_5": ("min", 0.85),
    "event_order_accuracy": ("min", 0.98),
    "call_result_pair_accuracy": ("min", 0.98),
    "patch_status_accuracy": ("min", 0.98),
    "validation_precision": ("min", 1.00),
    "patch_validation_path_recall": ("min", 0.85),
    "false_validated_rate": ("max", 0.0),
    "duplicate_context_ratio": ("max", 0.15),
    "p95_latency_ms": ("max", 1_500.0),
}
CODEX_REQUIRED_GUARDRAILS = (
    "reasoning_leakage",
    "secret_leakage",
    "acl_leakage",
)
CODEX_ROLLBACK_SEQUENCE = (
    "disable-codex-v2-selection",
    "restore-last-known-good-codex-generation",
    "invalidate-codex-v2-derived-cache",
    "verify-codex-v1-health",
)
_STAGES = tuple(CodexReleaseStage)


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def advance_codex_append_cursor_v2(
    content: bytes,
    *,
    project_id: str,
    source_id: str,
    generation_id: str,
    acl_ref: str,
    adapter_version: str = "codex-session-adapter-v1",
    cleaning_version: str = "codex-observable-cleaning-v1",
    builder_version: str = "codex-derived-builder-v2",
    previous: CodexAppendCursorV2 | None = None,
) -> CodexAppendCursorV2:
    """Advance only through complete lines; replacement/truncation keeps the LKG cursor."""

    if b"\x00" in content:
        raise ValueError("Codex append source contains NUL bytes")
    observed_sha = _sha256_bytes(content)
    if previous is not None and (
        previous.project_id,
        previous.source_id,
        previous.generation_id,
        previous.acl_ref,
    ) != (project_id, source_id, generation_id, acl_ref):
        raise ValueError("append cursor scope mismatch")
    if previous is None:
        last_newline = content.rfind(b"\n")
        published_offset = last_newline + 1 if last_newline >= 0 else 0
        disposition = CodexAppendDisposition.INITIAL
        reason = CodexAppendReason.INITIAL_COMPLETE_PREFIX
        previous_cursor_id = None
    elif (
        previous.adapter_version,
        previous.cleaning_version,
        previous.builder_version,
    ) != (adapter_version, cleaning_version, builder_version):
        published_offset = 0
        disposition = CodexAppendDisposition.FULL_REINGEST_REQUIRED
        reason = CodexAppendReason.VERSION_CHANGED
        previous_cursor_id = previous.cursor_id
    elif len(content) < previous.observed_size:
        published_offset = 0
        disposition = CodexAppendDisposition.FULL_REINGEST_REQUIRED
        reason = CodexAppendReason.SOURCE_TRUNCATED
        previous_cursor_id = previous.cursor_id
    elif _sha256_bytes(content[: previous.observed_size]) != previous.observed_sha256:
        published_offset = 0
        disposition = CodexAppendDisposition.FULL_REINGEST_REQUIRED
        reason = CodexAppendReason.SOURCE_REPLACED
        previous_cursor_id = previous.cursor_id
    elif len(content) == previous.observed_size:
        published_offset = previous.published_offset
        disposition = CodexAppendDisposition.UNCHANGED
        reason = CodexAppendReason.SOURCE_UNCHANGED
        previous_cursor_id = previous.cursor_id
    else:
        last_newline = content.rfind(b"\n", previous.published_offset)
        if last_newline + 1 > previous.published_offset:
            published_offset = last_newline + 1
            disposition = CodexAppendDisposition.APPENDED
            reason = CodexAppendReason.COMPLETE_LINES_APPENDED
        else:
            published_offset = previous.published_offset
            disposition = CodexAppendDisposition.PARTIAL_ONLY
            reason = CodexAppendReason.PARTIAL_LINE_NOT_ADVANCED
        previous_cursor_id = previous.cursor_id
    prefix = content[:published_offset]
    complete_lines = prefix.rstrip(b"\n").splitlines()
    last_line_sha = _sha256_bytes(complete_lines[-1]) if complete_lines else None
    values: dict[str, Any] = {
        "project_id": project_id,
        "source_id": source_id,
        "generation_id": generation_id,
        "acl_ref": acl_ref,
        "observed_size": len(content),
        "observed_sha256": observed_sha,
        "published_offset": published_offset,
        "published_prefix_sha256": _sha256_bytes(prefix),
        "last_complete_line_sha256": last_line_sha,
        "partial_bytes": len(content) - published_offset,
        "adapter_version": adapter_version,
        "cleaning_version": cleaning_version,
        "builder_version": builder_version,
        "previous_cursor_id": previous_cursor_id,
        "disposition": disposition,
        "reason": reason,
        "version": CODEX_APPEND_CURSOR_VERSION,
    }
    digest = canonical_sha256(values)
    return CodexAppendCursorV2(
        cursor_id="codexcursor-" + digest.removeprefix("sha256:"),
        cursor_sha256=digest,
        **values,
    )


def advance_and_publish_codex_append_cursor_v2(
    store: CodexV2Store,
    content: bytes,
    *,
    project_id: str,
    source_id: str,
    generation_id: str,
    acl_ref: str,
    adapter_version: str = "codex-session-adapter-v1",
    cleaning_version: str = "codex-observable-cleaning-v1",
    builder_version: str = "codex-derived-builder-v2",
) -> tuple[CodexAppendCursorV2, dict[str, Any]]:
    """Resume the exact persisted head and publish the next LKG-safe cursor."""

    previous = store.get_append_cursor_head(
        project_id=project_id,
        source_id=source_id,
        generation_id=generation_id,
        acl_ref=acl_ref,
    )
    exact_previous = (
        CodexAppendCursorV2.model_validate(
            previous.model_dump(mode="python", round_trip=True)  # type: ignore[attr-defined]
        )
        if previous is not None
        else None
    )
    cursor = advance_codex_append_cursor_v2(
        content,
        project_id=project_id,
        source_id=source_id,
        generation_id=generation_id,
        acl_ref=acl_ref,
        adapter_version=adapter_version,
        cleaning_version=cleaning_version,
        builder_version=builder_version,
        previous=exact_previous,
    )
    return cursor, store.publish_append_cursor(cursor)


def build_codex_privacy_tombstone_v2(
    *,
    project_id: str,
    source_id: str,
    generation_id: str,
    acl_ref: str,
    reason_code: str,
    source_cursor_id: str | None = None,
) -> CodexPrivacyTombstoneV2:
    if reason_code not in {"source_deleted", "privacy_request", "acl_revoked"}:
        raise ValueError("privacy tombstone reason is not allowlisted")
    values = {
        "project_id": project_id,
        "source_id": source_id,
        "generation_id": generation_id,
        "acl_ref": acl_ref,
        "reason_code": reason_code,
        "source_cursor_id": source_cursor_id,
        "version": CODEX_PRIVACY_TOMBSTONE_VERSION,
    }
    digest = canonical_sha256(values)
    return CodexPrivacyTombstoneV2(
        tombstone_id="codextomb-" + digest.removeprefix("sha256:"),
        tombstone_sha256=digest,
        **values,
    )


def propagate_codex_privacy_tombstone_v2(
    store: CodexV2Store,
    tombstone: CodexPrivacyTombstoneV2,
) -> dict[str, Any]:
    """Deactivate source-derived episodes, units, and edges in one store transaction."""

    result = store.tombstone(
        project_id=tombstone.project_id,
        source_id=tombstone.source_id,
        generation_id=tombstone.generation_id,
        acl_ref=tombstone.acl_ref,
        source_cursor_id=tombstone.source_cursor_id,
        reason=tombstone.reason_code,
    )
    remaining = store.active_derived_counts(
        project_id=tombstone.project_id,
        source_id=tombstone.source_id,
        generation_id=tombstone.generation_id,
        acl_ref=tombstone.acl_ref,
    )
    if any(remaining.values()):
        raise RuntimeError("privacy tombstone propagation left active derived state")
    return {
        **result,
        "source_tombstone_id": tombstone.tombstone_id,
        "active_derived_counts": remaining,
    }


def select_codex_engine_v2(
    *,
    project_id: str,
    request_id: str,
    stage: CodexReleaseStage,
    explicit_engine: str | None = None,
    override_authorized: bool = False,
    circuit_open: bool = False,
) -> CodexEngineSelectionV2:
    if explicit_engine not in {None, "v1", "v2"}:
        raise ValueError("Codex engine override must be v1 or v2")
    bucket = (
        int.from_bytes(
            hashlib.sha256(f"{project_id}\x1f{request_id}".encode()).digest()[:8],
            "big",
        )
        % 100
    )
    if circuit_open:
        return CodexEngineSelectionV2(
            engine="v1",
            shadow_v2=False,
            bucket=bucket,
            reason="codex-v2-circuit-open",
            override_authorized=override_authorized,
            circuit_open=True,
        )
    if explicit_engine is not None:
        if not override_authorized:
            return CodexEngineSelectionV2(
                engine="v1",
                shadow_v2=False,
                bucket=bucket,
                reason="engine-override-not-authorized",
                override_authorized=False,
            )
        if explicit_engine == "v2" and stage not in {
            CodexReleaseStage.OPT_IN_100,
            CodexReleaseStage.DEFAULT_V2,
        }:
            return CodexEngineSelectionV2(
                engine="v1",
                shadow_v2=False,
                bucket=bucket,
                reason="v2-not-release-authorized",
                override_authorized=True,
            )
        return CodexEngineSelectionV2(
            engine=explicit_engine,
            shadow_v2=False,
            bucket=bucket,
            reason="explicit-opt-in" if explicit_engine == "v2" else "explicit-v1",
            override_authorized=True,
        )
    if stage is CodexReleaseStage.SHADOW_INTERNAL_100:
        return CodexEngineSelectionV2(
            engine="v1",
            shadow_v2=True,
            bucket=bucket,
            reason="internal-shadow-default-v1",
        )
    percent = {
        CodexReleaseStage.CANARY_5: 5,
        CodexReleaseStage.CANARY_25: 25,
        CodexReleaseStage.OPT_IN_100: 0,
        CodexReleaseStage.DEFAULT_V2: 100,
    }.get(stage, 0)
    selected = bucket < percent
    return CodexEngineSelectionV2(
        engine="v2" if selected else "v1",
        shadow_v2=False,
        bucket=bucket,
        reason="stable-canary" if percent else "default-v1-hold",
    )


def aggregate_codex_shadow_v2(
    observations: tuple[CodexShadowObservationV2, ...],
    *,
    max_observations: int = 10_000,
) -> CodexShadowAggregateV2:
    if len(observations) > max_observations:
        raise ValueError("shadow aggregation exceeds its bounded window")
    overlap_denominator = sum(
        max(item.v1_result_count, item.v2_result_count) for item in observations
    )
    return CodexShadowAggregateV2(
        count=len(observations),
        overlap_numerator=sum(item.overlap_count for item in observations),
        overlap_denominator=overlap_denominator,
        fallback_count=sum(item.fallback for item in observations),
        secret_leakage=sum(item.secret_leakage for item in observations),
        acl_leakage=sum(item.acl_leakage for item in observations),
        mean_v1_latency_ms=(
            sum(item.v1_latency_ms for item in observations) / len(observations)
            if observations
            else 0.0
        ),
        mean_v2_latency_ms=(
            sum(item.v2_latency_ms for item in observations) / len(observations)
            if observations
            else 0.0
        ),
        observations_sha256=canonical_sha256(
            [item.model_dump(mode="json") for item in observations]
        ),
    )


def evaluate_codex_circuit_v2(
    aggregate: CodexShadowAggregateV2,
    *,
    minimum_observations: int = 20,
    maximum_fallback_ratio: float = 0.10,
    maximum_latency_multiplier: float = 2.0,
) -> CodexCircuitDecisionV2:
    reasons: list[str] = []
    if aggregate.secret_leakage:
        reasons.append("secret-leakage")
    if aggregate.acl_leakage:
        reasons.append("acl-leakage")
    if aggregate.count >= minimum_observations:
        if aggregate.fallback_count / aggregate.count > maximum_fallback_ratio:
            reasons.append("fallback-rate")
        if (
            aggregate.mean_v1_latency_ms > 0
            and aggregate.mean_v2_latency_ms
            > aggregate.mean_v1_latency_ms * maximum_latency_multiplier
        ):
            reasons.append("latency-regression")
    return CodexCircuitDecisionV2(
        status=CodexCircuitStatus.OPEN if reasons else CodexCircuitStatus.CLOSED,
        reasons=tuple(sorted(reasons)),
        observation_count=aggregate.count,
        aggregate_sha256=canonical_sha256(aggregate.model_dump(mode="json")),
    )


def execute_codex_with_fallback_v2(
    selection: CodexEngineSelectionV2,
    *,
    run_v1: Any,
    run_v2: Any,
) -> CodexExecutionOutcomeV2:
    """Execute each selected engine at most once and never expose exception text."""

    if selection.shadow_v2:
        v1_response = run_v1()
        try:
            run_v2()
            error_code = None
        except Exception:
            error_code = "codex-v2-shadow-failed"
        return CodexExecutionOutcomeV2(
            response=v1_response,
            served_engine="v1",
            v1_calls=1,
            v2_calls=1,
            fallback=False,
            shadow_executed=True,
            error_code=error_code,
        )
    if selection.engine == "v2":
        try:
            return CodexExecutionOutcomeV2(
                response=run_v2(),
                served_engine="v2",
                v1_calls=0,
                v2_calls=1,
                fallback=False,
                shadow_executed=False,
                error_code=None,
            )
        except Exception:
            return CodexExecutionOutcomeV2(
                response=run_v1(),
                served_engine="v1",
                v1_calls=1,
                v2_calls=1,
                fallback=True,
                shadow_executed=False,
                error_code="codex-v2-execution-failed",
            )
    return CodexExecutionOutcomeV2(
        response=run_v1(),
        served_engine="v1",
        v1_calls=1,
        v2_calls=0,
        fallback=False,
        shadow_executed=False,
        error_code=None,
    )


def build_codex_release_evidence_v2(
    *,
    observed_stage: CodexReleaseStage,
    proposed_stage: CodexReleaseStage,
    artifact_set_sha256: str,
    golden_package_sha256: str,
    component_set_sha256: str,
    metrics: tuple[CodexReleaseMetricV2, ...],
    guardrails: tuple[CodexReleaseGuardrailV2, ...],
    production_observation: bool,
    treatment_result_available: bool,
    rollback_rehearsed: bool = False,
) -> CodexReleaseEvidenceV2:
    values = {
        "observed_stage": observed_stage,
        "proposed_stage": proposed_stage,
        "artifact_set_sha256": artifact_set_sha256,
        "golden_package_sha256": golden_package_sha256,
        "component_set_sha256": component_set_sha256,
        "metrics": metrics,
        "guardrails": guardrails,
        "production_observation": production_observation,
        "treatment_result_available": treatment_result_available,
        "rollback_rehearsed": rollback_rehearsed,
        "evaluator_version": CODEX_RELEASE_EVALUATOR_VERSION,
    }
    return CodexReleaseEvidenceV2(
        evidence_id="codexrelease-"
        + canonical_sha256(
            {
                key: (
                    [item.model_dump(mode="json") for item in value]
                    if isinstance(value, tuple)
                    else value
                )
                for key, value in values.items()
            }
        ).removeprefix("sha256:"),
        **values,
    )


def evaluate_codex_release_v2(
    evidence: CodexReleaseEvidenceV2,
) -> CodexReleaseDecisionV2:
    observed_index = _STAGES.index(evidence.observed_stage)
    proposed_index = _STAGES.index(evidence.proposed_stage)
    metrics = {item.name: item for item in evidence.metrics}
    guardrails = {item.name: item for item in evidence.guardrails}
    failed_metrics = []
    for name, (direction, threshold) in CODEX_RELEASE_THRESHOLDS.items():
        metric = metrics.get(name)
        if (
            metric is None
            or metric.status is not CodexEvidenceStatus.AVAILABLE
            or metric.value is None
            or metric.denominator <= 0
            or (direction == "min" and metric.value < threshold)
            or (direction == "max" and metric.value > threshold)
        ):
            failed_metrics.append(name)
    failed_guardrails = [
        name
        for name in CODEX_REQUIRED_GUARDRAILS
        if name not in guardrails
        or guardrails[name].status is not CodexEvidenceStatus.AVAILABLE
        or guardrails[name].violations != 0
    ]
    if proposed_index != observed_index + 1:
        failed_guardrails.append("release-stage-transition")
    if not evidence.rollback_rehearsed:
        failed_guardrails.append("rollback-rehearsal")
    deployed = evidence.observed_stage in {
        CodexReleaseStage.CANARY_5,
        CodexReleaseStage.CANARY_25,
        CodexReleaseStage.OPT_IN_100,
        CodexReleaseStage.DEFAULT_V2,
    }
    failing = bool(failed_metrics or failed_guardrails)
    if deployed and evidence.production_observation and failing:
        decision = CodexReleaseDecisionType.ROLLBACK
        reason = "deployed-stage-evidence-failed"
    elif failing or not evidence.production_observation or not evidence.treatment_result_available:
        decision = CodexReleaseDecisionType.HOLD_DEFAULT_V1
        reason = (
            "treatment-result-unavailable"
            if not evidence.treatment_result_available
            else "production-observation-unavailable"
            if not evidence.production_observation
            else "release-evidence-incomplete-or-below-threshold"
        )
    else:
        decision = CodexReleaseDecisionType.PROMOTE
        reason = "next-stage-evidence-qualified"
    return CodexReleaseDecisionV2(
        decision=decision,
        observed_stage=evidence.observed_stage,
        proposed_stage=evidence.proposed_stage,
        failed_metrics=tuple(sorted(set(failed_metrics))),
        failed_guardrails=tuple(sorted(set(failed_guardrails))),
        reason=reason,
        rollback_sequence=CODEX_ROLLBACK_SEQUENCE,
    )


__all__ = [
    "CODEX_APPEND_CURSOR_VERSION",
    "CODEX_PRIVACY_TOMBSTONE_VERSION",
    "CODEX_RELEASE_EVALUATOR_VERSION",
    "CODEX_RELEASE_THRESHOLDS",
    "CODEX_REQUIRED_GUARDRAILS",
    "CODEX_ROLLBACK_SEQUENCE",
    "CODEX_SHADOW_AGGREGATOR_VERSION",
    "CodexAppendCursorV2",
    "CodexAppendDisposition",
    "CodexAppendReason",
    "CodexCircuitDecisionV2",
    "CodexCircuitStatus",
    "CodexEngineSelectionV2",
    "CodexEvidenceStatus",
    "CodexExecutionOutcomeV2",
    "CodexPrivacyTombstoneV2",
    "CodexReleaseDecisionType",
    "CodexReleaseDecisionV2",
    "CodexReleaseEvidenceV2",
    "CodexReleaseGuardrailV2",
    "CodexReleaseMetricV2",
    "CodexReleaseStage",
    "CodexShadowAggregateV2",
    "CodexShadowObservationV2",
    "advance_codex_append_cursor_v2",
    "advance_and_publish_codex_append_cursor_v2",
    "aggregate_codex_shadow_v2",
    "build_codex_privacy_tombstone_v2",
    "build_codex_release_evidence_v2",
    "evaluate_codex_release_v2",
    "evaluate_codex_circuit_v2",
    "execute_codex_with_fallback_v2",
    "propagate_codex_privacy_tombstone_v2",
    "select_codex_engine_v2",
]
