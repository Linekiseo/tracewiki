"""Canonical, fail-closed production release authority for RAG V2.

The control plane is intentionally verify-only.  It has no release, routing,
shadow, canary, rollback, database, network, or artifact-discovery side
effects.  Legacy evaluators may produce useful engineering integrity digests,
but only this module interprets the fixed reviewed evidence registry as
production authority.

The repository's reviewed evidence registry is currently empty.  Consequently
every public operation in this module returns ``QUALITY_HOLD`` with V1 as the
default engine.  No caller-authored boolean, checksum, copied package, or
caller-provided "trusted" argument can create an authority attestation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .multisource_release_package_v2 import (
    MULTISOURCE_RELEASE_PACKAGE_FILES,
    MultiSourceReleasePackageVerificationV2,
    verify_multisource_release_package_v2,
)
from .release_admission_v2 import (
    _PACKAGE_FILES,
    AuthorityFreshness,
    EvidenceAvailability,
    ImmutableSourceArtifactReferenceV2,
    ReleaseAdmissionEnvelopeV2,
    ReleaseAdmissionPackageVerificationV2,
    ReleaseDecision,
    ReleaseGateEvidenceV2,
    VerificationStatus,
    _unavailable_gate_evidence_v2,
    build_current_repository_quality_hold_v2,
    evaluate_release_admission_v2,
    fixed_reviewed_release_evidence_authority_registry_v2,
    inspect_current_production_authority_v2,
    verify_release_admission_package_v2,
)
from .sources.experiment.contracts_v2 import canonical_sha256_v2

RELEASE_CONTROL_PLANE_VERSION = "rag-release-control-plane-v2"
RUNTIME_RELEASE_STATUS_VERSION = "rag-runtime-release-status-v2"
RUNTIME_OPERATIONAL_STATUS_VERSION = "rag-ops-status-v1"
CANONICAL_RELEASE_AUTHORITY_ID = "rag-canonical-production-release-authority-v2"
DEFAULT_ENGINE = "v1"
_SHA256_PREFIX = "sha256:"
_RELEASE_SOURCES = ("code", "codex", "experiment", "notebook", "document", "workspace")
_COMPONENT_SWITCHES = (
    "generator_prompt",
    "context_packer",
    "fusion",
    "reranker",
    "embedding_generation",
    "source_retrievers",
    "planner",
)
_PUBLIC_REASON_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,127}")
_PUBLIC_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}")
_MAX_OPERATIONAL_COUNT = 1_000_000_000


class ReleaseControlPlaneError(ValueError):
    """The canonical release authority could not verify the requested input."""


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


def _is_sha256(value: str) -> bool:
    return (
        value.startswith(_SHA256_PREFIX)
        and len(value) == len(_SHA256_PREFIX) + 64
        and all(character in "0123456789abcdef" for character in value[7:])
    )


class ReleaseControlPlaneStatusV2(_Frozen):
    """Side-effect-free snapshot of the only production release authority."""

    control_plane_version: Literal["rag-release-control-plane-v2"] = RELEASE_CONTROL_PLANE_VERSION
    authority_id: Literal["rag-canonical-production-release-authority-v2"] = (
        CANONICAL_RELEASE_AUTHORITY_ID
    )
    reviewed_registry_sha256: str
    reviewed_source_count: int = Field(ge=0)
    reviewed_gate_count: int = Field(ge=0)
    production_authority_current: bool
    authority_ready: Literal[False] = False
    authority_attested: Literal[False] = False
    authority_attestation_sha256: None = None
    decision: Literal[ReleaseDecision.QUALITY_HOLD] = ReleaseDecision.QUALITY_HOLD
    default_engine: Literal["v1"] = DEFAULT_ENGINE
    quality_hold: Literal[True] = True
    side_effect_free: Literal[True] = True
    blockers: tuple[str, ...]
    integrity_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if not _is_sha256(self.reviewed_registry_sha256):
            raise ValueError("reviewed registry integrity digest is malformed")
        if self.blockers != tuple(sorted(set(self.blockers))) or not self.blockers:
            raise ValueError("control-plane blockers must be sorted, unique, and nonempty")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"integrity_sha256"}))
        if self.integrity_sha256 != expected:
            raise ValueError("control-plane status integrity digest mismatch")
        return self


class ReviewedEvidenceAdmissionV2(_Frozen):
    """Result of comparing immutable evidence with the fixed reviewed registry."""

    control_plane_version: Literal["rag-release-control-plane-v2"] = RELEASE_CONTROL_PLANE_VERSION
    authority_id: Literal["rag-canonical-production-release-authority-v2"] = (
        CANONICAL_RELEASE_AUTHORITY_ID
    )
    reviewed_registry_sha256: str
    source_inputs_sha256: str
    gate_inputs_sha256: str
    admission_integrity_sha256: str
    authority_attestation_sha256: None = None
    authority_attested: Literal[False] = False
    reviewed_evidence_admitted: Literal[False] = False
    decision: Literal[ReleaseDecision.QUALITY_HOLD] = ReleaseDecision.QUALITY_HOLD
    default_engine: Literal["v1"] = DEFAULT_ENGINE
    quality_hold: Literal[True] = True
    promotion_performed: Literal[False] = False
    verify_only: Literal[True] = True
    blockers: tuple[str, ...]
    integrity_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> Self:
        for value in (
            self.reviewed_registry_sha256,
            self.source_inputs_sha256,
            self.gate_inputs_sha256,
            self.admission_integrity_sha256,
        ):
            if not _is_sha256(value):
                raise ValueError("reviewed evidence integrity digest is malformed")
        if self.blockers != tuple(sorted(set(self.blockers))) or not self.blockers:
            raise ValueError("reviewed evidence blockers must be sorted, unique, and nonempty")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"integrity_sha256"}))
        if self.integrity_sha256 != expected:
            raise ValueError("reviewed evidence admission integrity digest mismatch")
        return self


class ExactReleasePackageVerificationV2(_Frozen):
    """Verify-only result for one portable package with exact file membership."""

    control_plane_version: Literal["rag-release-control-plane-v2"] = RELEASE_CONTROL_PLANE_VERSION
    authority_id: Literal["rag-canonical-production-release-authority-v2"] = (
        CANONICAL_RELEASE_AUTHORITY_ID
    )
    package_kind: Literal["release-admission", "multisource-audit"]
    package_integrity_sha256: str
    authority_attestation_sha256: None = None
    authority_attested: Literal[False] = False
    decision: Literal[ReleaseDecision.QUALITY_HOLD] = ReleaseDecision.QUALITY_HOLD
    default_engine: Literal["v1"] = DEFAULT_ENGINE
    quality_hold: Literal[True] = True
    exact_files: Literal[True] = True
    portable: Literal[True] = True
    verify_only: Literal[True] = True
    retrieval_executed: Literal[False] = False
    database_accessed: Literal[False] = False
    network_accessed: Literal[False] = False
    integrity_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if not _is_sha256(self.package_integrity_sha256):
            raise ValueError("package integrity digest is malformed")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"integrity_sha256"}))
        if self.integrity_sha256 != expected:
            raise ValueError("exact package verification integrity digest mismatch")
        return self


class ReleaseSourceStatusV2(_Frozen):
    """Sanitized release-evidence availability for one production source."""

    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    authority_status: AuthorityFreshness
    availability: EvidenceAvailability
    quality_qualified: Literal[False] = False
    production_observation: Literal[False] = False
    evidence_integrity_sha256: None = None
    reason_code: str

    @model_validator(mode="after")
    def _safe(self) -> Self:
        if _PUBLIC_REASON_PATTERN.fullmatch(self.reason_code) is None:
            raise ValueError("source snapshot reason is not public-safe")
        return self


class ReleaseSourceSnapshotV2(_Frozen):
    """Complete source snapshot from the reviewed production authority only."""

    status: Literal[VerificationStatus.UNAVAILABLE] = VerificationStatus.UNAVAILABLE
    reason_code: Literal["reviewed-release-authority-unavailable"] = (
        "reviewed-release-authority-unavailable"
    )
    sources: tuple[ReleaseSourceStatusV2, ...] = Field(min_length=6, max_length=6)
    integrity_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if tuple(item.source for item in self.sources) != _RELEASE_SOURCES:
            raise ValueError("release source snapshot membership mismatch")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"integrity_sha256"}))
        if self.integrity_sha256 != expected:
            raise ValueError("release source snapshot integrity digest mismatch")
        return self


class ReleaseGateSnapshotV2(_Frozen):
    """Sanitized reviewed evidence status for one global release gate."""

    gate: Literal["performance", "rollback"]
    status: Literal[VerificationStatus.UNAVAILABLE] = VerificationStatus.UNAVAILABLE
    qualified: Literal[False] = False
    production_observation: Literal[False] = False
    evidence_integrity_sha256: None = None
    reason_code: str
    integrity_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if _PUBLIC_REASON_PATTERN.fullmatch(self.reason_code) is None:
            raise ValueError("release gate snapshot reason is not public-safe")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"integrity_sha256"}))
        if self.integrity_sha256 != expected:
            raise ValueError("release gate snapshot integrity digest mismatch")
        return self


OperationalAvailabilityV2 = Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]


class RuntimeOperationalSourceStatusV2(_Frozen):
    """Project-free source posture derived only from canonical authority."""

    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    v1_stage: Literal["DEFAULT_V1"] = "DEFAULT_V1"
    v2_stage: Literal["NO_RELEASE"] = "NO_RELEASE"
    quality_state: Literal["QUALITY_HOLD"] = "QUALITY_HOLD"
    fallback: Literal["DEFAULT_V1"] = "DEFAULT_V1"
    last_known_good: OperationalAvailabilityV2
    authority_availability: OperationalAvailabilityV2
    runtime_availability: OperationalAvailabilityV2


class RuntimeOperationalSourcesV2(_Frozen):
    """Exact six-source membership for the public operational contract."""

    code: RuntimeOperationalSourceStatusV2
    codex: RuntimeOperationalSourceStatusV2
    experiment: RuntimeOperationalSourceStatusV2
    notebook: RuntimeOperationalSourceStatusV2
    document: RuntimeOperationalSourceStatusV2
    workspace: RuntimeOperationalSourceStatusV2
    registry_version: str | None = None
    router_version: str | None = None
    release_authority_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    verified_lkg_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    materialized_source_count: int | None = Field(default=None, ge=0, le=6)
    derived_root_created: bool | None = None
    active_operation_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)

    @model_validator(mode="after")
    def _membership(self) -> Self:
        for source in _RELEASE_SOURCES:
            if getattr(self, source).source != source:
                raise ValueError("operational source status membership mismatch")
        return self


class RuntimeOperationalComponentSwitchesV2(_Frozen):
    """Seven independent switch observations; None means UNKNOWN."""

    version: str | None = None
    availability: OperationalAvailabilityV2
    generator_prompt: bool | None = None
    context_packer: bool | None = None
    fusion: bool | None = None
    reranker: bool | None = None
    embedding_generation: bool | None = None
    source_retrievers: bool | None = None
    planner: bool | None = None


class RuntimeOperationalIndexV2(_Frozen):
    availability: OperationalAvailabilityV2
    status: OperationalAvailabilityV2
    index_version: str | None = None
    exact: bool | None = None
    namespace_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    generation_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    active_namespace_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    vector_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)


class RuntimeOperationalCacheV2(_Frozen):
    availability: OperationalAvailabilityV2
    status: OperationalAvailabilityV2
    state: Literal["in_memory_ephemeral", "unavailable", "unknown"]
    embedding_cache_version: str | None = None
    scoped_cache_version: str | None = None
    embedding_entry_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    scoped_entry_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    hits: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)


class RuntimeOperationalLatencyV2(_Frozen):
    availability: OperationalAvailabilityV2
    exact_aggregation_available: bool | None = None
    trace_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    span_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)


class RuntimeOperationalDashboardV2(_Frozen):
    availability: OperationalAvailabilityV2
    trace_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    span_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    latency_availability: OperationalAvailabilityV2


class RuntimeOperationalCalibrationV2(_Frozen):
    availability: OperationalAvailabilityV2
    available: bool | None = None
    status: OperationalAvailabilityV2
    profile_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    required_slice_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)
    unavailable_slice_count: int | None = Field(default=None, ge=0, le=_MAX_OPERATIONAL_COUNT)


class RuntimeOperationalPerformanceV2(_Frozen):
    runtime_version: str | None = None
    index: RuntimeOperationalIndexV2
    cache: RuntimeOperationalCacheV2
    latency: RuntimeOperationalLatencyV2
    dashboard: RuntimeOperationalDashboardV2
    calibration: RuntimeOperationalCalibrationV2


class RuntimeReleaseStatusV2(_Frozen):
    """Public, read-only status projection for Runtime, API, and CLI."""

    schema_version: Literal["rag-ops-status-v1"] = RUNTIME_OPERATIONAL_STATUS_VERSION
    operational_version: Literal["rag-ops-status-v1"] = RUNTIME_OPERATIONAL_STATUS_VERSION
    status_version: Literal["rag-runtime-release-status-v2"] = RUNTIME_RELEASE_STATUS_VERSION
    generated_at: datetime
    stale: Literal[False] = False
    stale_after_seconds: Literal[300] = 300
    default_engine: Literal["v1"] = DEFAULT_ENGINE
    quality_hold: Literal[True] = True
    decision: Literal[ReleaseDecision.QUALITY_HOLD] = ReleaseDecision.QUALITY_HOLD
    blockers: tuple[str, ...]
    release_authority: ReleaseControlPlaneStatusV2
    release_integrity_sha256: str
    source_snapshot: ReleaseSourceSnapshotV2
    rollback_snapshot: ReleaseGateSnapshotV2
    performance_snapshot: ReleaseGateSnapshotV2
    sources: RuntimeOperationalSourcesV2
    component_switches: RuntimeOperationalComponentSwitchesV2
    performance: RuntimeOperationalPerformanceV2
    reviewed_calibration: RuntimeOperationalCalibrationV2
    verify_only: Literal[True] = True
    sanitized: Literal[True] = True
    integrity_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> Self:
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("runtime status timestamp must be timezone-aware")
        if self.blockers != tuple(sorted(set(self.blockers))) or not self.blockers:
            raise ValueError("runtime release blockers must be sorted, unique, and nonempty")
        if self.release_authority.blockers != self.blockers:
            raise ValueError("runtime release blockers differ from canonical authority")
        if (
            self.release_authority.default_engine != self.default_engine
            or self.release_authority.quality_hold != self.quality_hold
            or self.release_authority.decision != self.decision
        ):
            raise ValueError("runtime release status differs from canonical authority")
        if self.rollback_snapshot.gate != "rollback":
            raise ValueError("runtime rollback snapshot is mislabeled")
        if self.performance_snapshot.gate != "performance":
            raise ValueError("runtime performance snapshot is mislabeled")
        if self.reviewed_calibration != self.performance.calibration:
            raise ValueError("runtime calibration projections differ")
        if not _is_sha256(self.release_integrity_sha256):
            raise ValueError("runtime release integrity digest is malformed")
        expected = canonical_sha256_v2(self.model_dump(mode="json", exclude={"integrity_sha256"}))
        if self.integrity_sha256 != expected:
            raise ValueError("runtime release status integrity digest mismatch")
        return self


def _dump_sequence(values: Sequence[BaseModel]) -> list[object]:
    return [value.model_dump(mode="json") for value in values]


def _status_snapshot(
    admission: ReleaseAdmissionEnvelopeV2 | None = None,
) -> ReleaseControlPlaneStatusV2:
    resolved_admission = admission or build_current_repository_quality_hold_v2()
    registry = fixed_reviewed_release_evidence_authority_registry_v2()
    production_authority_current = all(
        item.authority_status is AuthorityFreshness.CURRENT for item in resolved_admission.sources
    )
    blockers = {*resolved_admission.blockers, "reviewed-release-authority-unavailable"}
    if not production_authority_current:
        blockers.add("production-authority-not-current")
    payload = {
        "control_plane_version": RELEASE_CONTROL_PLANE_VERSION,
        "authority_id": CANONICAL_RELEASE_AUTHORITY_ID,
        "reviewed_registry_sha256": registry.content_sha256,
        "reviewed_source_count": len(registry.sources),
        "reviewed_gate_count": len(registry.gates),
        "production_authority_current": production_authority_current,
        "authority_ready": False,
        "authority_attested": False,
        "authority_attestation_sha256": None,
        "decision": ReleaseDecision.QUALITY_HOLD,
        "default_engine": DEFAULT_ENGINE,
        "quality_hold": True,
        "side_effect_free": True,
        "blockers": tuple(sorted(blockers)),
    }
    return ReleaseControlPlaneStatusV2(
        **payload,
        integrity_sha256=canonical_sha256_v2(payload),
    )


def _source_snapshot(admission: ReleaseAdmissionEnvelopeV2) -> ReleaseSourceSnapshotV2:
    source_rows = tuple(
        ReleaseSourceStatusV2(
            source=item.source,
            authority_status=item.authority_status,
            availability=item.availability,
            reason_code=item.reason_code,
        )
        for item in admission.sources
    )
    payload = {
        "status": VerificationStatus.UNAVAILABLE,
        "reason_code": "reviewed-release-authority-unavailable",
        "sources": source_rows,
    }
    normalized = ReleaseSourceSnapshotV2.model_construct(
        integrity_sha256="pending",
        **payload,
    ).model_dump(mode="json", exclude={"integrity_sha256"})
    return ReleaseSourceSnapshotV2(
        **payload,
        integrity_sha256=canonical_sha256_v2(normalized),
    )


def _gate_snapshot(
    admission: ReleaseAdmissionEnvelopeV2,
    *,
    gate: Literal["performance", "rollback"],
) -> ReleaseGateSnapshotV2:
    evidence = next(item for item in admission.gates if item.gate == gate)
    payload = {
        "gate": gate,
        "status": VerificationStatus.UNAVAILABLE,
        "qualified": False,
        "production_observation": False,
        "evidence_integrity_sha256": None,
        "reason_code": evidence.reason_code,
    }
    normalized = ReleaseGateSnapshotV2.model_construct(
        integrity_sha256="pending",
        **payload,
    ).model_dump(mode="json", exclude={"integrity_sha256"})
    return ReleaseGateSnapshotV2(
        **payload,
        integrity_sha256=canonical_sha256_v2(normalized),
    )


def _safe_identifier(value: object) -> str | None:
    if not isinstance(value, str) or _PUBLIC_IDENTIFIER_PATTERN.fullmatch(value) is None:
        return None
    lowered = value.casefold()
    if any(
        marker in lowered
        for marker in (
            "bearer",
            "credential",
            "password",
            "private_key",
            "secret",
            "token",
        )
    ):
        return None
    return value


def _bounded_count(value: object, *, maximum: int = _MAX_OPERATIONAL_COUNT) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        return None
    return value


def _record(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(key, str)}


def _availability(value: object) -> OperationalAvailabilityV2:
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}:
            return normalized  # type: ignore[return-value]
    return "UNKNOWN"


def _default_operational_sources(
    source_snapshot: ReleaseSourceSnapshotV2,
    raw_sources: object = None,
) -> RuntimeOperationalSourcesV2:
    raw = _record(raw_sources)
    registry_version = _safe_identifier(raw.get("registry_version"))
    router_version = _safe_identifier(raw.get("router_version"))
    closed = raw.get("closed")
    registry_availability: OperationalAvailabilityV2 = (
        "UNAVAILABLE"
        if raw_sources is None
        else "AVAILABLE"
        if registry_version is not None and closed is False
        else "UNAVAILABLE"
        if closed is True
        else "UNKNOWN"
    )
    verified_lkg_count = _bounded_count(raw.get("verified_lkg_count"))
    per_source_lkg: OperationalAvailabilityV2 = (
        "UNAVAILABLE" if verified_lkg_count == 0 or raw_sources is None else "UNKNOWN"
    )
    authority_by_source = {item.source: item for item in source_snapshot.sources}
    rows: dict[str, RuntimeOperationalSourceStatusV2] = {}
    for source in _RELEASE_SOURCES:
        evidence = authority_by_source[source]
        authority_availability: OperationalAvailabilityV2 = (
            "AVAILABLE"
            if (
                evidence.authority_status is AuthorityFreshness.CURRENT
                and evidence.availability is EvidenceAvailability.CURRENT
            )
            else "UNAVAILABLE"
        )
        rows[source] = RuntimeOperationalSourceStatusV2(
            source=source,  # type: ignore[arg-type]
            last_known_good=per_source_lkg,
            authority_availability=authority_availability,
            runtime_availability=registry_availability,
        )
    return RuntimeOperationalSourcesV2(
        **rows,
        registry_version=registry_version,
        router_version=router_version,
        release_authority_count=_bounded_count(raw.get("release_authority_count")),
        verified_lkg_count=verified_lkg_count,
        materialized_source_count=_bounded_count(
            raw.get("materialized_source_count"),
            maximum=6,
        ),
        derived_root_created=(
            raw.get("derived_root_created")
            if isinstance(raw.get("derived_root_created"), bool)
            else None
        ),
        active_operation_count=_bounded_count(raw.get("active_operation_count")),
    )


def _operational_switches(raw_switches: object) -> RuntimeOperationalComponentSwitchesV2:
    raw = _record(raw_switches)
    observed: dict[str, bool | None] = {name: None for name in _COMPONENT_SWITCHES}
    components = raw.get("components")
    if isinstance(components, (list, tuple)):
        for item in components[: len(_COMPONENT_SWITCHES)]:
            if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] in observed:
                mode = item[1]
                observed[str(item[0])] = (
                    True
                    if mode == "enabled"
                    else False
                    if mode in {"deterministic_baseline", "v1_fallback"}
                    else None
                )
    switch_availability: OperationalAvailabilityV2 = (
        "UNAVAILABLE"
        if raw_switches is None
        else "AVAILABLE"
        if _safe_identifier(raw.get("version")) is not None
        and all(value is not None for value in observed.values())
        else "UNKNOWN"
    )
    return RuntimeOperationalComponentSwitchesV2(
        version=_safe_identifier(raw.get("version")),
        availability=switch_availability,
        **observed,
    )


def _unavailable_performance() -> RuntimeOperationalPerformanceV2:
    calibration = RuntimeOperationalCalibrationV2(
        availability="UNAVAILABLE",
        available=False,
        status="UNAVAILABLE",
    )
    return RuntimeOperationalPerformanceV2(
        index=RuntimeOperationalIndexV2(
            availability="UNAVAILABLE",
            status="UNAVAILABLE",
        ),
        cache=RuntimeOperationalCacheV2(
            availability="UNAVAILABLE",
            status="UNAVAILABLE",
            state="unavailable",
        ),
        latency=RuntimeOperationalLatencyV2(
            availability="UNAVAILABLE",
            exact_aggregation_available=False,
        ),
        dashboard=RuntimeOperationalDashboardV2(
            availability="UNAVAILABLE",
            latency_availability="UNAVAILABLE",
        ),
        calibration=calibration,
    )


def _operational_performance(
    raw_performance: object,
    *,
    reviewed_calibration_count: object,
    required_calibration_slice_count: object,
) -> RuntimeOperationalPerformanceV2:
    raw = _record(raw_performance)
    if not raw:
        return _unavailable_performance()
    vector = _record(raw.get("vector_index"))
    embedding = _record(raw.get("embedding_cache"))
    scoped = _record(raw.get("scoped_cache"))
    index_version = _safe_identifier(vector.get("index_version"))
    index_available = index_version is not None
    embedding_version = _safe_identifier(embedding.get("cache_version"))
    scoped_version = _safe_identifier(scoped.get("cache_version"))
    cache_available = embedding_version is not None and scoped_version is not None
    trace_count = _bounded_count(raw.get("trace_count"))
    span_count = _bounded_count(raw.get("span_count"))
    dashboard_available = trace_count is not None and span_count is not None
    calibration_availability = _availability(raw.get("calibration_availability"))
    reviewed_count = _bounded_count(reviewed_calibration_count)
    required_count = _bounded_count(required_calibration_slice_count)
    unavailable_count = _bounded_count(raw.get("unavailable_calibration_slice_count"))
    calibration = RuntimeOperationalCalibrationV2(
        availability=calibration_availability,
        available=(
            True
            if calibration_availability == "AVAILABLE"
            else False
            if calibration_availability == "UNAVAILABLE"
            else None
        ),
        status=calibration_availability,
        profile_count=reviewed_count,
        required_slice_count=required_count,
        unavailable_slice_count=unavailable_count,
    )
    return RuntimeOperationalPerformanceV2(
        runtime_version=_safe_identifier(raw.get("runtime_version")),
        index=RuntimeOperationalIndexV2(
            availability="AVAILABLE" if index_available else "UNKNOWN",
            status="AVAILABLE" if index_available else "UNKNOWN",
            index_version=index_version,
            exact=True if index_available and index_version.startswith("exact-") else None,
            namespace_count=_bounded_count(vector.get("namespace_count")),
            generation_count=_bounded_count(vector.get("generation_count")),
            active_namespace_count=_bounded_count(vector.get("active_namespace_count")),
            vector_count=_bounded_count(vector.get("vector_count")),
        ),
        cache=RuntimeOperationalCacheV2(
            availability="AVAILABLE" if cache_available else "UNKNOWN",
            status="AVAILABLE" if cache_available else "UNKNOWN",
            state="in_memory_ephemeral" if cache_available else "unknown",
            embedding_cache_version=embedding_version,
            scoped_cache_version=scoped_version,
            embedding_entry_count=_bounded_count(embedding.get("entry_count")),
            scoped_entry_count=_bounded_count(scoped.get("entry_count")),
            hits=_bounded_count(raw.get("cache_hits")),
        ),
        latency=RuntimeOperationalLatencyV2(
            # The global snapshot intentionally exposes no exact distribution.
            availability="UNAVAILABLE",
            exact_aggregation_available=False,
            trace_count=trace_count,
            span_count=span_count,
        ),
        dashboard=RuntimeOperationalDashboardV2(
            availability="AVAILABLE" if dashboard_available else "UNKNOWN",
            trace_count=trace_count,
            span_count=span_count,
            latency_availability="UNAVAILABLE",
        ),
        calibration=calibration,
    )


def _operational_projection(
    source_snapshot: ReleaseSourceSnapshotV2,
    operational_snapshot: object | None,
) -> tuple[
    RuntimeOperationalSourcesV2,
    RuntimeOperationalComponentSwitchesV2,
    RuntimeOperationalPerformanceV2,
]:
    raw = _record(operational_snapshot)
    sources = _default_operational_sources(source_snapshot, raw.get("sources"))
    switches = _operational_switches(raw.get("component_switches"))
    performance = _operational_performance(
        raw.get("performance"),
        reviewed_calibration_count=raw.get("reviewed_calibration_count"),
        required_calibration_slice_count=raw.get("required_calibration_slice_count"),
    )
    return sources, switches, performance


def _runtime_status_snapshot(
    operational_snapshot: object | None = None,
) -> RuntimeReleaseStatusV2:
    admission = build_current_repository_quality_hold_v2()
    authority = _status_snapshot(admission)
    source_snapshot = _source_snapshot(admission)
    sources, switches, performance = _operational_projection(
        source_snapshot,
        operational_snapshot,
    )
    payload = {
        "schema_version": RUNTIME_OPERATIONAL_STATUS_VERSION,
        "operational_version": RUNTIME_OPERATIONAL_STATUS_VERSION,
        "status_version": RUNTIME_RELEASE_STATUS_VERSION,
        "generated_at": datetime.now(UTC),
        "stale": False,
        "stale_after_seconds": 300,
        "default_engine": DEFAULT_ENGINE,
        "quality_hold": True,
        "decision": ReleaseDecision.QUALITY_HOLD,
        "blockers": authority.blockers,
        "release_authority": authority,
        "release_integrity_sha256": admission.content_sha256,
        "source_snapshot": source_snapshot,
        "rollback_snapshot": _gate_snapshot(admission, gate="rollback"),
        "performance_snapshot": _gate_snapshot(admission, gate="performance"),
        "sources": sources,
        "component_switches": switches,
        "performance": performance,
        "reviewed_calibration": performance.calibration,
        "verify_only": True,
        "sanitized": True,
    }
    normalized = RuntimeReleaseStatusV2.model_construct(
        integrity_sha256="pending",
        **payload,
    ).model_dump(mode="json", exclude={"integrity_sha256"})
    return RuntimeReleaseStatusV2(
        **payload,
        integrity_sha256=canonical_sha256_v2(normalized),
    )


_CANONICAL_CONSTRUCTION_TOKEN = object()
_CANONICAL_RELEASE_CONTROL_PLANE_INSTANCE: object | None = None


class CanonicalReleaseControlPlaneV2:
    """Non-subclassable process singleton for canonical release decisions."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("canonical release control plane cannot be subclassed")

    def __new__(
        cls,
        construction_token: object | None = None,
    ) -> CanonicalReleaseControlPlaneV2:
        global _CANONICAL_RELEASE_CONTROL_PLANE_INSTANCE
        if construction_token is not _CANONICAL_CONSTRUCTION_TOKEN:
            raise ReleaseControlPlaneError(
                "use canonical_release_control_plane_v2(); caller authorities are forbidden"
            )
        if _CANONICAL_RELEASE_CONTROL_PLANE_INSTANCE is None:
            _CANONICAL_RELEASE_CONTROL_PLANE_INSTANCE = super().__new__(cls)
        if not isinstance(
            _CANONICAL_RELEASE_CONTROL_PLANE_INSTANCE,
            CanonicalReleaseControlPlaneV2,
        ):
            raise ReleaseControlPlaneError("canonical release authority identity is invalid")
        return _CANONICAL_RELEASE_CONTROL_PLANE_INSTANCE

    def current_status(self) -> ReleaseControlPlaneStatusV2:
        return _status_snapshot()

    def runtime_status(
        self,
        operational_snapshot: object | None = None,
    ) -> RuntimeReleaseStatusV2:
        return _runtime_status_snapshot(operational_snapshot)

    def admit_reviewed_evidence(
        self,
        *,
        artifacts: Sequence[ImmutableSourceArtifactReferenceV2] = (),
        gates: Sequence[ReleaseGateEvidenceV2] | None = None,
    ) -> ReviewedEvidenceAdmissionV2:
        if any(not isinstance(item, ImmutableSourceArtifactReferenceV2) for item in artifacts):
            raise ReleaseControlPlaneError(
                "reviewed evidence admission requires frozen source references"
            )
        if gates is not None and any(not isinstance(item, ReleaseGateEvidenceV2) for item in gates):
            raise ReleaseControlPlaneError(
                "reviewed evidence admission requires frozen gate evidence"
            )
        authority = inspect_current_production_authority_v2()
        if not artifacts and gates is None:
            admission = build_current_repository_quality_hold_v2()
            gate_inputs = _unavailable_gate_evidence_v2()
        else:
            gate_inputs = _unavailable_gate_evidence_v2() if gates is None else tuple(gates)
            admission = evaluate_release_admission_v2(
                authority=authority,
                artifacts=artifacts,
                gates=gate_inputs,
            )
        return self._held_admission(
            admission=admission,
            artifacts=artifacts,
            gates=gate_inputs,
        )

    def verify_exact_package(
        self,
        package_dir: Path,
    ) -> ExactReleasePackageVerificationV2:
        try:
            names = tuple(sorted(path.name for path in package_dir.iterdir()))
        except OSError as exc:
            raise ReleaseControlPlaneError("exact release package is unavailable") from exc
        if names == tuple(sorted(_PACKAGE_FILES)):
            verified = verify_release_admission_package_v2(package_dir)
            return self._held_package(
                package_kind="release-admission",
                verified=verified,
            )
        if names == tuple(sorted(MULTISOURCE_RELEASE_PACKAGE_FILES)):
            verified = verify_multisource_release_package_v2(package_dir)
            return self._held_package(
                package_kind="multisource-audit",
                verified=verified,
            )
        raise ReleaseControlPlaneError("exact release package membership mismatch")

    @staticmethod
    def _held_admission(
        *,
        admission: ReleaseAdmissionEnvelopeV2,
        artifacts: Sequence[ImmutableSourceArtifactReferenceV2],
        gates: Sequence[ReleaseGateEvidenceV2],
    ) -> ReviewedEvidenceAdmissionV2:
        registry = fixed_reviewed_release_evidence_authority_registry_v2()
        blockers = tuple(sorted({*admission.blockers, "canonical-reviewed-registry-empty"}))
        payload = {
            "control_plane_version": RELEASE_CONTROL_PLANE_VERSION,
            "authority_id": CANONICAL_RELEASE_AUTHORITY_ID,
            "reviewed_registry_sha256": registry.content_sha256,
            "source_inputs_sha256": canonical_sha256_v2(_dump_sequence(artifacts)),
            "gate_inputs_sha256": canonical_sha256_v2(_dump_sequence(gates)),
            "admission_integrity_sha256": admission.content_sha256,
            "authority_attestation_sha256": None,
            "authority_attested": False,
            "reviewed_evidence_admitted": False,
            "decision": ReleaseDecision.QUALITY_HOLD,
            "default_engine": DEFAULT_ENGINE,
            "quality_hold": True,
            "promotion_performed": False,
            "verify_only": True,
            "blockers": blockers,
        }
        return ReviewedEvidenceAdmissionV2(
            **payload,
            integrity_sha256=canonical_sha256_v2(payload),
        )

    @staticmethod
    def _held_package(
        *,
        package_kind: Literal["release-admission", "multisource-audit"],
        verified: ReleaseAdmissionPackageVerificationV2 | MultiSourceReleasePackageVerificationV2,
    ) -> ExactReleasePackageVerificationV2:
        package_integrity = (
            verified.artifact_set_sha256
            if isinstance(verified, ReleaseAdmissionPackageVerificationV2)
            else verified.package_set_sha256
        )
        payload = {
            "control_plane_version": RELEASE_CONTROL_PLANE_VERSION,
            "authority_id": CANONICAL_RELEASE_AUTHORITY_ID,
            "package_kind": package_kind,
            "package_integrity_sha256": package_integrity,
            "authority_attestation_sha256": None,
            "authority_attested": False,
            "decision": ReleaseDecision.QUALITY_HOLD,
            "default_engine": DEFAULT_ENGINE,
            "quality_hold": True,
            "exact_files": True,
            "portable": True,
            "verify_only": True,
            "retrieval_executed": False,
            "database_accessed": False,
            "network_accessed": False,
        }
        return ExactReleasePackageVerificationV2(
            **payload,
            integrity_sha256=canonical_sha256_v2(payload),
        )


_CANONICAL_RELEASE_CONTROL_PLANE = CanonicalReleaseControlPlaneV2(_CANONICAL_CONSTRUCTION_TOKEN)


def canonical_release_control_plane_v2() -> CanonicalReleaseControlPlaneV2:
    """Return the unique canonical release authority for this process."""

    return _CANONICAL_RELEASE_CONTROL_PLANE


def current_release_status_v2() -> ReleaseControlPlaneStatusV2:
    """Return current fail-closed release status without external side effects."""

    return _CANONICAL_RELEASE_CONTROL_PLANE.current_status()


def current_runtime_release_status_v2() -> RuntimeReleaseStatusV2:
    """Return the sanitized Runtime/API/CLI release status projection."""

    return _CANONICAL_RELEASE_CONTROL_PLANE.runtime_status()


def runtime_release_status_from_operational_snapshot_v2(
    operational_snapshot: object,
) -> RuntimeReleaseStatusV2:
    """Merge allowlisted in-memory observations under canonical release truth."""

    return _CANONICAL_RELEASE_CONTROL_PLANE.runtime_status(operational_snapshot)


def admit_immutable_reviewed_evidence_v2(
    *,
    artifacts: Sequence[ImmutableSourceArtifactReferenceV2] = (),
    gates: Sequence[ReleaseGateEvidenceV2] | None = None,
) -> ReviewedEvidenceAdmissionV2:
    """Admit only immutable evidence matching the fixed reviewed registry."""

    return _CANONICAL_RELEASE_CONTROL_PLANE.admit_reviewed_evidence(
        artifacts=artifacts,
        gates=gates,
    )


def verify_exact_release_package_v2(
    package_dir: Path,
) -> ExactReleasePackageVerificationV2:
    """Verify portable bytes with exact canonical file membership."""

    return _CANONICAL_RELEASE_CONTROL_PLANE.verify_exact_package(package_dir)


__all__ = [
    "CANONICAL_RELEASE_AUTHORITY_ID",
    "RELEASE_CONTROL_PLANE_VERSION",
    "RUNTIME_OPERATIONAL_STATUS_VERSION",
    "RUNTIME_RELEASE_STATUS_VERSION",
    "CanonicalReleaseControlPlaneV2",
    "ExactReleasePackageVerificationV2",
    "RuntimeOperationalCalibrationV2",
    "RuntimeOperationalComponentSwitchesV2",
    "RuntimeOperationalPerformanceV2",
    "RuntimeOperationalSourceStatusV2",
    "RuntimeOperationalSourcesV2",
    "ReleaseControlPlaneError",
    "ReleaseControlPlaneStatusV2",
    "ReleaseGateSnapshotV2",
    "ReleaseSourceSnapshotV2",
    "ReleaseSourceStatusV2",
    "ReviewedEvidenceAdmissionV2",
    "RuntimeReleaseStatusV2",
    "admit_immutable_reviewed_evidence_v2",
    "canonical_release_control_plane_v2",
    "current_release_status_v2",
    "current_runtime_release_status_v2",
    "runtime_release_status_from_operational_snapshot_v2",
    "verify_exact_release_package_v2",
]
