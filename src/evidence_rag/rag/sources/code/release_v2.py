"""Frozen C7-03 release evidence contracts and a fail-closed evaluator.

This module evaluates evidence only.  It never reads a Run or database, changes
configuration, advances a publication pointer, or executes a rollback action.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

RELEASE_CONTRACT_VERSION = "c7-code-release-contract-v2"
RELEASE_EVIDENCE_VERSION = "c7-code-release-evidence-v2"
RELEASE_SOURCE_VERSION = "c7-code-release-source-v2"
RELEASE_METRIC_VERSION = "c7-code-release-metric-v2"
RELEASE_GUARDRAIL_VERSION = "c7-code-release-guardrail-v2"
RELEASE_ARTIFACT_VERSION = "c7-code-release-artifact-v2"
RELEASE_DECISION_VERSION = "c7-code-release-decision-v2"
RELEASE_POLICY_VERSION = "c7-code-release-policy-v1"
RELEASE_ATTESTATION_VERSION = "c7-code-release-attestation-v2"
RELEASE_EVALUATOR_VERSION = "c7-code-release-evaluator-v2"
SHADOW_AGGREGATE_VERSION = "c7-code-shadow-aggregate-v2"
LATENCY_EVIDENCE_VERSION = "c7-code-latency-evidence-v2"
COST_EVIDENCE_VERSION = "c7-code-cost-evidence-v2"
ROLLBACK_PLAN_VERSION = "c7-code-rollback-plan-v1"

CB0_QUALIFIED_RUN_ID = "evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061"
CB6_PROVISIONAL_RUN_ID = "evaluation-run://project-code-cb6-v1/06eea9107f274fe3b13d77bd68b8a00e"
DEFAULT_RUNTIME_ENGINE = "v1"
MAX_EVIDENCE_AGE_SECONDS = 86_400
MAX_SHADOW_BATCHES = 1_024
MAX_SHADOW_SAMPLES = 10_000
MAX_SHADOW_LATENCY_MS = 120_000.0

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HMAC_SHA256_RE = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_EMBEDDED_ABSOLUTE_RE = re.compile(r"/(?:Users|home|root|tmp|var|private|etc|opt|Volumes)/")
_SECRET_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:password|passwd|client[_-]?secret|api[_-]?key|access[_-]?token|"
    r"auth[_-]?token)\s*[:=]\s*\S{8,}"
    r"|(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}"
    r"|gh[oprsu]_[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{16}"
    r")"
)
_PERSONAL_DATA_RE = re.compile(
    r"(?i)(?:"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
    r"|\b(?:\+?\d[\s().-]*){10,15}\b"
    r")"
)


def _validate_safe_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if normalized != normalized.strip():
        raise ValueError("release text must not have leading or trailing whitespace")
    if not normalized:
        raise ValueError("release text must not be empty")
    for character in normalized:
        if unicodedata.category(character).startswith("C"):
            raise ValueError("release text must not contain control characters")
    lowered = normalized.lower()
    if lowered.startswith("file://"):
        raise ValueError("release evidence must not contain file URIs")
    scheme_separator = normalized.find("://")
    scheme_remainder = normalized[scheme_separator + 3 :] if scheme_separator >= 0 else normalized
    if (
        normalized.startswith(("/", "\\", "~"))
        or scheme_remainder.startswith(("/", "\\", "~"))
        or _WINDOWS_ABSOLUTE_RE.match(normalized)
        or _WINDOWS_ABSOLUTE_RE.match(scheme_remainder)
        or _EMBEDDED_ABSOLUTE_RE.search(normalized)
    ):
        raise ValueError("release evidence must not contain absolute paths")
    if _SECRET_RE.search(normalized):
        raise ValueError("release evidence must not contain secret material")
    if _PERSONAL_DATA_RE.search(normalized):
        raise ValueError("release evidence must not contain personal data")
    return normalized


def _validate_sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("digest must be lowercase sha256:<64 hex>")
    return value


def _validate_hmac_sha256(value: str) -> str:
    if not _HMAC_SHA256_RE.fullmatch(value):
        raise ValueError("signature must be lowercase hmac-sha256:<64 hex>")
    return value


SafeText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=2_000),
    AfterValidator(_validate_safe_text),
]
Sha256Digest = Annotated[StrictStr, AfterValidator(_validate_sha256)]
HMACSha256Signature = Annotated[StrictStr, AfterValidator(_validate_hmac_sha256)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
NonNegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
ShadowLatency = Annotated[
    float,
    Field(
        ge=0,
        le=MAX_SHADOW_LATENCY_MS,
        allow_inf_nan=False,
        strict=True,
    ),
]


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class _ReleaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json_bytes(self.model_dump(mode="json"))

    def canonical_sha256(self) -> str:
        return _sha256(self.canonical_json_bytes())

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        del deep
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(self, **_: Any) -> Self:
        raise TypeError("deprecated copy() is disabled; use validated model_copy()")


class ReleaseStage(StrEnum):
    OFFLINE = "OFFLINE"
    SHADOW_INTERNAL_100 = "SHADOW_INTERNAL_100"
    CANARY_5 = "CANARY_5"
    CANARY_25 = "CANARY_25"
    OPT_IN_100 = "OPT_IN_100"
    DEFAULT_V2 = "DEFAULT_V2"


RELEASE_STAGE_ORDER = (
    ReleaseStage.OFFLINE,
    ReleaseStage.SHADOW_INTERNAL_100,
    ReleaseStage.CANARY_5,
    ReleaseStage.CANARY_25,
    ReleaseStage.OPT_IN_100,
    ReleaseStage.DEFAULT_V2,
)
_STAGE_INDEX = MappingProxyType({stage: index for index, stage in enumerate(RELEASE_STAGE_ORDER)})
MINIMUM_STAGE_SAMPLES = MappingProxyType(
    {
        ReleaseStage.OFFLINE: 33,
        ReleaseStage.SHADOW_INTERNAL_100: 100,
        ReleaseStage.CANARY_5: 200,
        ReleaseStage.CANARY_25: 500,
        ReleaseStage.OPT_IN_100: 1_000,
        ReleaseStage.DEFAULT_V2: 1_000,
    }
)


def _validate_stage_parent_binding(
    release_stage: ReleaseStage,
    parent_evidence_digest: str | None,
) -> None:
    if release_stage is ReleaseStage.OFFLINE:
        if parent_evidence_digest is not None:
            raise ValueError("OFFLINE issuance must not claim parent evidence")
    elif parent_evidence_digest is None:
        raise ValueError("non-OFFLINE issuance requires parent evidence digest")


class EvidenceClass(StrEnum):
    PRODUCTION = "PRODUCTION"
    TEST = "TEST"


class EvidenceStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class MetricUnit(StrEnum):
    RATIO = "ratio"
    MILLISECONDS = "milliseconds"


class ReleaseMetricName(StrEnum):
    SYMBOL_RECALL_AT_10 = "symbol_recall_at_10"
    FILE_RECALL_AT_10 = "file_recall_at_10"
    MRR_AT_10 = "mrr_at_10"
    REQUIRED_PATH_RECALL = "required_path_recall"
    EXACT_COMMIT_ACCURACY = "exact_commit_accuracy"
    WRONG_VERSION_RATE = "wrong_version_rate"
    FALSE_VALIDATION_RATE = "false_validation_rate"
    LOCATOR_ACCURACY = "locator_accuracy"
    HARMFUL_CANDIDATE_RATE_AT_10 = "harmful_candidate_rate_at_10"
    LATENCY_P95_MS = "latency_p95_ms"
    UNAUTHORIZED_LEAKAGE_RATE = "unauthorized_leakage_rate"
    SECRET_LEAKAGE_RATE = "secret_leakage_rate"


class ThresholdOperator(StrEnum):
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    EQUAL = "equal"


@dataclass(frozen=True, slots=True)
class GateRequirement:
    name: ReleaseMetricName
    operator: ThresholdOperator
    threshold: float
    minimum_denominator: int
    unit: MetricUnit


GATE_REQUIREMENTS = (
    GateRequirement(
        ReleaseMetricName.SYMBOL_RECALL_AT_10,
        ThresholdOperator.GREATER_THAN_OR_EQUAL,
        0.85,
        44,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.FILE_RECALL_AT_10,
        ThresholdOperator.GREATER_THAN_OR_EQUAL,
        0.90,
        33,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.MRR_AT_10,
        ThresholdOperator.GREATER_THAN_OR_EQUAL,
        0.70,
        29,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.REQUIRED_PATH_RECALL,
        ThresholdOperator.GREATER_THAN_OR_EQUAL,
        0.75,
        14,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.EXACT_COMMIT_ACCURACY,
        ThresholdOperator.GREATER_THAN_OR_EQUAL,
        0.95,
        38,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.WRONG_VERSION_RATE,
        ThresholdOperator.LESS_THAN_OR_EQUAL,
        0.02,
        224,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.FALSE_VALIDATION_RATE,
        ThresholdOperator.EQUAL,
        0.0,
        120,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.LOCATOR_ACCURACY,
        ThresholdOperator.GREATER_THAN_OR_EQUAL,
        0.98,
        224,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.HARMFUL_CANDIDATE_RATE_AT_10,
        ThresholdOperator.LESS_THAN_OR_EQUAL,
        0.05,
        92,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.LATENCY_P95_MS,
        ThresholdOperator.LESS_THAN_OR_EQUAL,
        1_500.0,
        33,
        MetricUnit.MILLISECONDS,
    ),
    GateRequirement(
        ReleaseMetricName.UNAUTHORIZED_LEAKAGE_RATE,
        ThresholdOperator.EQUAL,
        0.0,
        224,
        MetricUnit.RATIO,
    ),
    GateRequirement(
        ReleaseMetricName.SECRET_LEAKAGE_RATE,
        ThresholdOperator.EQUAL,
        0.0,
        224,
        MetricUnit.RATIO,
    ),
)
GATE_REQUIREMENT_BY_NAME = MappingProxyType(
    {requirement.name: requirement for requirement in GATE_REQUIREMENTS}
)


class GuardrailName(StrEnum):
    EXACT_IDENTIFIER_NON_REGRESSION = "exact_identifier_non_regression"
    V1_SCHEMA_COMPATIBILITY = "v1_schema_compatibility"
    LAST_KNOWN_GOOD_INGESTION = "last_known_good_ingestion"
    SHADOW_EXCEPTION_RATE = "shadow_exception_rate"
    INDEX_SIZE_BUDGET = "index_size_budget"
    MODEL_UNAVAILABLE_FALLBACK = "model_unavailable_fallback"
    SCIP_EXTERNAL_SEMANTIC_FALLBACK = "scip_external_semantic_fallback"
    ACL_SECURITY_SUITE = "acl_security_suite"


GUARDRAIL_ORDER = tuple(GuardrailName)


class GuardrailStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ReleaseArtifactKind(StrEnum):
    CODE_GOLDEN = "code_golden"
    C_B0 = "c_b0"
    C_B1 = "c_b1"
    C_B2 = "c_b2"
    C_B3 = "c_b3"
    C_B4 = "c_b4"
    C_B5 = "c_b5"
    C_B6 = "c_b6"
    SELECTED_MODEL_ADR = "selected_model_adr"
    SCIP_ADR = "scip_adr"
    SECURITY_REPORT = "security_report"
    CANARY_REPORT = "canary_report"
    ROLLBACK_REHEARSAL = "rollback_rehearsal"


ARTIFACT_ORDER = tuple(ReleaseArtifactKind)


class ArtifactDisposition(StrEnum):
    QUALIFIED = "QUALIFIED"
    SELECTED = "SELECTED"
    PASS = "PASS"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    PROVISIONAL_NOT_QUALIFIED = "PROVISIONAL_NOT_QUALIFIED"
    UNAVAILABLE = "UNAVAILABLE"


REQUIRED_ARTIFACT_DISPOSITION = MappingProxyType(
    {
        ReleaseArtifactKind.CODE_GOLDEN: ArtifactDisposition.QUALIFIED,
        ReleaseArtifactKind.C_B0: ArtifactDisposition.QUALIFIED,
        ReleaseArtifactKind.C_B1: ArtifactDisposition.QUALIFIED,
        ReleaseArtifactKind.C_B2: ArtifactDisposition.QUALIFIED,
        ReleaseArtifactKind.C_B3: ArtifactDisposition.QUALIFIED,
        ReleaseArtifactKind.C_B4: ArtifactDisposition.QUALIFIED,
        ReleaseArtifactKind.C_B5: ArtifactDisposition.QUALIFIED,
        ReleaseArtifactKind.C_B6: ArtifactDisposition.QUALIFIED,
        ReleaseArtifactKind.SELECTED_MODEL_ADR: ArtifactDisposition.SELECTED,
        ReleaseArtifactKind.SCIP_ADR: ArtifactDisposition.SELECTED,
        ReleaseArtifactKind.SECURITY_REPORT: ArtifactDisposition.PASS,
        ReleaseArtifactKind.CANARY_REPORT: ArtifactDisposition.PASS,
        ReleaseArtifactKind.ROLLBACK_REHEARSAL: ArtifactDisposition.PASS,
    }
)


class EvidenceSource(_ReleaseModel):
    contract_version: Literal[RELEASE_SOURCE_VERSION] = RELEASE_SOURCE_VERSION
    release_stage: ReleaseStage
    parent_evidence_digest: Sha256Digest | None = None
    artifact_id: SafeText
    artifact_version: SafeText
    artifact_digest: Sha256Digest
    immutable: Literal[True] = True

    @model_validator(mode="after")
    def validate_stage_parent(self) -> Self:
        _validate_stage_parent_binding(
            self.release_stage,
            self.parent_evidence_digest,
        )
        return self


class EvidenceAttestation(_ReleaseModel):
    contract_version: Literal[RELEASE_ATTESTATION_VERSION] = RELEASE_ATTESTATION_VERSION
    release_stage: ReleaseStage
    parent_evidence_digest: Sha256Digest | None = None
    verifier_id: SafeText
    verifier_version: SafeText
    subject_digest: Sha256Digest
    source_artifact_digest: Sha256Digest
    source_artifact_version: SafeText
    signature: HMACSha256Signature

    @model_validator(mode="after")
    def validate_stage_parent(self) -> Self:
        _validate_stage_parent_binding(
            self.release_stage,
            self.parent_evidence_digest,
        )
        return self

    def signing_payload_bytes(self) -> bytes:
        return _canonical_json_bytes(self.model_dump(mode="json", exclude={"signature"}))


class EvidenceVerifier(Protocol):
    verifier_id: str
    verifier_version: str
    test_only: bool

    def verify(self, attestation: EvidenceAttestation) -> bool: ...


@dataclass(frozen=True, slots=True)
class TestHMACSHA256Verifier:
    """Complete deterministic verifier reserved for explicitly marked test evidence."""

    verifier_id: str
    verifier_version: str
    key: bytes = field(repr=False)
    test_only: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        _validate_safe_text(self.verifier_id)
        _validate_safe_text(self.verifier_version)
        if type(self.key) is not bytes or len(self.key) < 32:
            raise ValueError("test verifier key must contain at least 32 bytes")

    def verify(self, attestation: EvidenceAttestation) -> bool:
        expected = hmac.new(
            self.key,
            attestation.signing_payload_bytes(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(
            attestation.signature,
            f"hmac-sha256:{expected}",
        )


def create_test_attestation(
    *,
    subject_digest: str,
    source: EvidenceSource,
    verifier: TestHMACSHA256Verifier,
    release_stage: ReleaseStage,
    parent_evidence_digest: str | None,
) -> EvidenceAttestation:
    """Sign one immutable component for an explicitly test-only fixture."""

    if (
        source.release_stage is not release_stage
        or source.parent_evidence_digest != parent_evidence_digest
    ):
        raise ValueError("test attestation stage/parent must match its immutable source")
    unsigned = {
        "contract_version": RELEASE_ATTESTATION_VERSION,
        "release_stage": release_stage,
        "parent_evidence_digest": parent_evidence_digest,
        "verifier_id": verifier.verifier_id,
        "verifier_version": verifier.verifier_version,
        "subject_digest": _validate_sha256(subject_digest),
        "source_artifact_digest": source.artifact_digest,
        "source_artifact_version": source.artifact_version,
    }
    signature = hmac.new(
        verifier.key,
        _canonical_json_bytes(unsigned),
        hashlib.sha256,
    ).hexdigest()
    return EvidenceAttestation(
        **unsigned,
        signature=f"hmac-sha256:{signature}",
    )


class _AttestedReleaseModel(_ReleaseModel):
    release_stage: ReleaseStage
    parent_evidence_digest: Sha256Digest | None = None
    attestation: EvidenceAttestation | None = None

    @model_validator(mode="after")
    def validate_stage_parent(self) -> Self:
        _validate_stage_parent_binding(
            self.release_stage,
            self.parent_evidence_digest,
        )
        source = getattr(self, "source", None)
        if source is not None and (
            source.release_stage is not self.release_stage
            or source.parent_evidence_digest != self.parent_evidence_digest
        ):
            raise ValueError("component stage/parent must match its immutable source")
        if self.attestation is not None and (
            self.attestation.release_stage is not self.release_stage
            or self.attestation.parent_evidence_digest != self.parent_evidence_digest
        ):
            raise ValueError("component stage/parent must match its attestation")
        return self

    def attested_payload_bytes(self) -> bytes:
        return _canonical_json_bytes(self.model_dump(mode="json", exclude={"attestation"}))

    def attested_digest(self) -> str:
        return _sha256(self.attested_payload_bytes())


class ReleaseMetric(_AttestedReleaseModel):
    contract_version: Literal[RELEASE_METRIC_VERSION] = RELEASE_METRIC_VERSION
    name: ReleaseMetricName
    value: NonNegativeFloat | None
    status: EvidenceStatus
    numerator: NonNegativeFloat
    denominator: NonNegativeInt
    unit: MetricUnit
    source: EvidenceSource

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.status is EvidenceStatus.UNAVAILABLE:
            if self.value is not None or self.numerator != 0 or self.denominator != 0:
                raise ValueError(
                    "UNAVAILABLE metric must have null value and zero numerator/denominator"
                )
            return self
        if self.value is None or self.denominator == 0:
            raise ValueError("AVAILABLE/PROVISIONAL metric requires value and positive denominator")
        if self.unit is MetricUnit.RATIO:
            if self.numerator > self.denominator:
                raise ValueError("ratio numerator must not exceed denominator")
            computed = self.numerator / self.denominator
            if not math.isclose(self.value, computed, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("ratio value contradicts numerator/denominator")
        elif not math.isclose(
            self.value,
            self.numerator,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "millisecond metric numerator must equal the reported percentile value"
            )
        return self


class ReleaseGuardrail(_AttestedReleaseModel):
    contract_version: Literal[RELEASE_GUARDRAIL_VERSION] = RELEASE_GUARDRAIL_VERSION
    name: GuardrailName
    status: GuardrailStatus
    numerator: NonNegativeInt
    denominator: NonNegativeInt
    source: EvidenceSource

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.status is GuardrailStatus.UNKNOWN:
            if self.numerator != 0 or self.denominator != 0:
                raise ValueError("UNKNOWN guardrail must have zero counts")
            return self
        if self.denominator == 0 or self.numerator > self.denominator:
            raise ValueError("known guardrail status requires consistent positive counts")
        if self.status is GuardrailStatus.PASS and self.numerator != self.denominator:
            raise ValueError("PASS guardrail numerator must equal denominator")
        if self.status is GuardrailStatus.FAIL and self.numerator == self.denominator:
            raise ValueError("FAIL guardrail must contain at least one failure")
        return self


class ReleaseArtifact(_AttestedReleaseModel):
    contract_version: Literal[RELEASE_ARTIFACT_VERSION] = RELEASE_ARTIFACT_VERSION
    kind: ReleaseArtifactKind
    artifact_id: SafeText
    evidence_status: EvidenceStatus
    disposition: ArtifactDisposition
    source: EvidenceSource | None = None
    reason: SafeText

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.evidence_status is EvidenceStatus.AVAILABLE and self.source is None:
            raise ValueError("AVAILABLE artifact requires an immutable source")
        if self.evidence_status is EvidenceStatus.UNAVAILABLE and (
            self.source is not None or self.attestation is not None
        ):
            raise ValueError("UNAVAILABLE artifact must not claim source or attestation")
        return self


class ReleaseLatencyEvidence(_AttestedReleaseModel):
    contract_version: Literal[LATENCY_EVIDENCE_VERSION] = LATENCY_EVIDENCE_VERSION
    status: EvidenceStatus
    sample_count: NonNegativeInt
    p50_ms: NonNegativeFloat | None
    p95_ms: NonNegativeFloat | None
    p99_ms: NonNegativeFloat | None
    source: EvidenceSource | None = None

    @model_validator(mode="after")
    def validate_latency(self) -> Self:
        values = (self.p50_ms, self.p95_ms, self.p99_ms)
        if self.status is EvidenceStatus.UNAVAILABLE:
            if self.sample_count != 0 or any(value is not None for value in values):
                raise ValueError("UNAVAILABLE latency must have zero samples and null percentiles")
            if self.source is not None or self.attestation is not None:
                raise ValueError("UNAVAILABLE latency must not claim source or attestation")
            return self
        if self.sample_count == 0 or any(value is None for value in values):
            raise ValueError("known latency requires samples and all percentiles")
        p50, p95, p99 = values
        if not (p50 <= p95 <= p99):  # type: ignore[operator]
            raise ValueError("latency percentiles must be monotonic")
        if self.source is None:
            raise ValueError("known latency requires an immutable source")
        return self


class ReleaseCostEvidence(_AttestedReleaseModel):
    contract_version: Literal[COST_EVIDENCE_VERSION] = COST_EVIDENCE_VERSION
    status: EvidenceStatus
    index_bytes: NonNegativeInt | None
    ingest_time_ms: NonNegativeFloat | None
    source: EvidenceSource | None = None

    @model_validator(mode="after")
    def validate_cost(self) -> Self:
        if self.status is EvidenceStatus.UNAVAILABLE:
            if self.index_bytes is not None or self.ingest_time_ms is not None:
                raise ValueError("UNAVAILABLE cost must have null values")
            if self.source is not None or self.attestation is not None:
                raise ValueError("UNAVAILABLE cost must not claim source or attestation")
            return self
        if self.index_bytes is None or self.ingest_time_ms is None or self.source is None:
            raise ValueError("known cost requires both values and an immutable source")
        return self


class ShadowRate(_ReleaseModel):
    status: EvidenceStatus
    numerator: NonNegativeInt
    denominator: NonNegativeInt
    value: NonNegativeFloat | None

    @model_validator(mode="after")
    def validate_rate(self) -> Self:
        if self.status is EvidenceStatus.UNAVAILABLE:
            if self.numerator != 0 or self.denominator != 0 or self.value is not None:
                raise ValueError("UNAVAILABLE shadow rate must be 0/0 with null value")
            return self
        if self.denominator == 0 or self.numerator > self.denominator or self.value is None:
            raise ValueError("known shadow rate requires consistent counts and value")
        if not math.isclose(
            self.value,
            self.numerator / self.denominator,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("shadow rate contradicts numerator/denominator")
        return self


class ShadowEvidenceAggregate(_AttestedReleaseModel):
    contract_version: Literal[SHADOW_AGGREGATE_VERSION] = SHADOW_AGGREGATE_VERSION
    total_samples: NonNegativeInt
    latency_samples: NonNegativeInt
    latency_status: EvidenceStatus
    p50_ms: NonNegativeFloat | None
    p95_ms: NonNegativeFloat | None
    p99_ms: NonNegativeFloat | None
    error_rate: ShadowRate
    exception_rate: ShadowRate
    fallback_rate: ShadowRate
    compatibility_failure_rate: ShadowRate
    security_violation_rate: ShadowRate
    unauthorized_leakage_rate: ShadowRate
    secret_leakage_rate: ShadowRate
    model_unavailable_rate: ShadowRate
    semantic_fallback_rate: ShadowRate
    source: EvidenceSource | None = None

    @model_validator(mode="after")
    def validate_aggregate(self) -> Self:
        if self.latency_samples > self.total_samples:
            raise ValueError("latency denominator exceeds total shadow samples")
        values = (self.p50_ms, self.p95_ms, self.p99_ms)
        if self.latency_status is EvidenceStatus.UNAVAILABLE:
            if self.latency_samples != 0 or any(value is not None for value in values):
                raise ValueError("UNAVAILABLE shadow latency must be empty")
        else:
            if self.latency_samples == 0 or any(value is None for value in values):
                raise ValueError("known shadow latency requires samples and percentiles")
            p50, p95, p99 = values
            if not (p50 <= p95 <= p99):  # type: ignore[operator]
                raise ValueError("shadow percentiles must be monotonic")
        rates = (
            self.error_rate,
            self.exception_rate,
            self.fallback_rate,
            self.compatibility_failure_rate,
            self.security_violation_rate,
            self.unauthorized_leakage_rate,
            self.secret_leakage_rate,
            self.model_unavailable_rate,
            self.semantic_fallback_rate,
        )
        if any(rate.denominator > self.total_samples for rate in rates):
            raise ValueError("shadow rate denominator exceeds total samples")
        if self.total_samples == 0 and self.source is not None:
            raise ValueError("zero-sample shadow evidence must not claim a source")
        if self.total_samples > 0 and self.source is None:
            raise ValueError("non-empty shadow evidence requires an immutable source")
        return self


class SanitizedShadowCounters(_ReleaseModel):
    """Bounded counters only; query text, ACL refs, paths, and identities are not fields."""

    sample_count: NonNegativeInt
    latency_ms: tuple[ShadowLatency, ...] = ()
    error_count: NonNegativeInt | None = None
    exception_count: NonNegativeInt | None = None
    fallback_count: NonNegativeInt | None = None
    compatibility_failure_count: NonNegativeInt | None = None
    security_violation_count: NonNegativeInt | None = None
    unauthorized_leakage_count: NonNegativeInt | None = None
    secret_leakage_count: NonNegativeInt | None = None
    model_unavailable_count: NonNegativeInt | None = None
    semantic_fallback_count: NonNegativeInt | None = None

    @model_validator(mode="after")
    def validate_counters(self) -> Self:
        if len(self.latency_ms) > self.sample_count:
            raise ValueError("latency observations exceed the batch sample count")
        for field_name in (
            "error_count",
            "exception_count",
            "fallback_count",
            "compatibility_failure_count",
            "security_violation_count",
            "unauthorized_leakage_count",
            "secret_leakage_count",
            "model_unavailable_count",
            "semantic_fallback_count",
        ):
            count = getattr(self, field_name)
            if count is not None and count > self.sample_count:
                raise ValueError(f"{field_name} exceeds the batch sample count")
        return self


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    position = max(0, math.ceil(percentile * len(values)) - 1)
    return float(values[position])


def _shadow_rate(
    counters: Sequence[SanitizedShadowCounters],
    field_name: str,
    total_samples: int,
) -> ShadowRate:
    numerator = 0
    denominator = 0
    for counter in counters:
        value = getattr(counter, field_name)
        if value is not None:
            numerator += value
            denominator += counter.sample_count
    if denominator == 0:
        return ShadowRate(
            status=EvidenceStatus.UNAVAILABLE,
            numerator=0,
            denominator=0,
            value=None,
        )
    status = (
        EvidenceStatus.AVAILABLE if denominator == total_samples else EvidenceStatus.PROVISIONAL
    )
    return ShadowRate(
        status=status,
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator,
    )


def aggregate_shadow_evidence(
    counters: Sequence[SanitizedShadowCounters],
    *,
    release_stage: ReleaseStage,
    parent_evidence_digest: str | None,
    source: EvidenceSource | None = None,
) -> ShadowEvidenceAggregate:
    """Aggregate bounded sanitized counters without retaining request-level content."""

    if isinstance(counters, (str, bytes)) or not isinstance(counters, Sequence):
        raise TypeError("shadow counters must be a bounded sequence")
    if len(counters) > MAX_SHADOW_BATCHES:
        raise ValueError("too many shadow counter batches")
    validated = tuple(
        SanitizedShadowCounters.model_validate(counter.model_dump(mode="python"))
        if isinstance(counter, SanitizedShadowCounters)
        else SanitizedShadowCounters.model_validate(counter)
        for counter in counters
    )
    total_samples = sum(counter.sample_count for counter in validated)
    if total_samples > MAX_SHADOW_SAMPLES:
        raise ValueError("shadow sample bound exceeded")
    latencies = sorted(latency for counter in validated for latency in counter.latency_ms)
    if not latencies:
        latency_status = EvidenceStatus.UNAVAILABLE
        p50 = p95 = p99 = None
    else:
        latency_status = (
            EvidenceStatus.AVAILABLE
            if len(latencies) == total_samples
            else EvidenceStatus.PROVISIONAL
        )
        p50 = _nearest_rank(latencies, 0.50)
        p95 = _nearest_rank(latencies, 0.95)
        p99 = _nearest_rank(latencies, 0.99)
    effective_source = source if total_samples else None
    return ShadowEvidenceAggregate(
        release_stage=release_stage,
        parent_evidence_digest=parent_evidence_digest,
        total_samples=total_samples,
        latency_samples=len(latencies),
        latency_status=latency_status,
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        error_rate=_shadow_rate(validated, "error_count", total_samples),
        exception_rate=_shadow_rate(validated, "exception_count", total_samples),
        fallback_rate=_shadow_rate(validated, "fallback_count", total_samples),
        compatibility_failure_rate=_shadow_rate(
            validated,
            "compatibility_failure_count",
            total_samples,
        ),
        security_violation_rate=_shadow_rate(
            validated,
            "security_violation_count",
            total_samples,
        ),
        unauthorized_leakage_rate=_shadow_rate(
            validated,
            "unauthorized_leakage_count",
            total_samples,
        ),
        secret_leakage_rate=_shadow_rate(
            validated,
            "secret_leakage_count",
            total_samples,
        ),
        model_unavailable_rate=_shadow_rate(
            validated,
            "model_unavailable_count",
            total_samples,
        ),
        semantic_fallback_rate=_shadow_rate(
            validated,
            "semantic_fallback_count",
            total_samples,
        ),
        source=effective_source,
    )


class ReleaseVersions(_ReleaseModel):
    code_golden_version: SafeText
    evaluation_profile_version: SafeText
    builder_version: SafeText
    index_version: SafeText
    model_version: SafeText
    scip_version: SafeText


class ArtifactVersionExpectation(_ReleaseModel):
    kind: ReleaseArtifactKind
    artifact_version: SafeText


class ReleasePolicy(_ReleaseModel):
    contract_version: Literal[RELEASE_POLICY_VERSION] = RELEASE_POLICY_VERSION
    expected_versions: ReleaseVersions
    artifact_versions: tuple[ArtifactVersionExpectation, ...]
    metric_artifact_version: SafeText
    guardrail_artifact_version: SafeText
    latency_artifact_version: SafeText
    cost_artifact_version: SafeText
    shadow_artifact_version: SafeText
    release_artifact_version: SafeText
    maximum_evidence_age_seconds: Literal[MAX_EVIDENCE_AGE_SECONDS] = MAX_EVIDENCE_AGE_SECONDS

    @model_validator(mode="after")
    def validate_artifact_versions(self) -> Self:
        kinds = tuple(item.kind for item in self.artifact_versions)
        if kinds != ARTIFACT_ORDER:
            raise ValueError("policy artifact versions must contain every kind in canonical order")
        return self


class RollbackAction(StrEnum):
    SET_ENGINE_V1 = "rag_code_engine=v1"
    DISABLE_RERANKER = "disable_reranker"
    DISABLE_GRAPH = "disable_graph"
    SWITCH_EMBEDDING_PROFILE = "switch_embedding_profile"
    SWITCH_V2_PUBLICATION_POINTER = "switch_v2_publication_pointer"
    PRESERVE_EVIDENCE = "preserve_evidence"
    PRESERVE_STABLE_ENTITY_GENERATION = "preserve_stable_entity_generation"


ROLLBACK_ACTIONS = (
    RollbackAction.SET_ENGINE_V1,
    RollbackAction.DISABLE_RERANKER,
    RollbackAction.DISABLE_GRAPH,
    RollbackAction.SWITCH_EMBEDDING_PROFILE,
    RollbackAction.SWITCH_V2_PUBLICATION_POINTER,
    RollbackAction.PRESERVE_EVIDENCE,
    RollbackAction.PRESERVE_STABLE_ENTITY_GENERATION,
)


class RollbackPlan(_ReleaseModel):
    contract_version: Literal[ROLLBACK_PLAN_VERSION] = ROLLBACK_PLAN_VERSION
    actions: tuple[RollbackAction, ...] = ROLLBACK_ACTIONS

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.actions != ROLLBACK_ACTIONS:
            raise ValueError("rollback actions must use the frozen fail-safe order")
        return self


class ReleaseEvidence(_AttestedReleaseModel):
    contract_version: Literal[RELEASE_EVIDENCE_VERSION] = RELEASE_EVIDENCE_VERSION
    evidence_class: EvidenceClass
    stage: ReleaseStage
    generated_at: datetime
    expires_at: datetime
    previous_evidence_digest: Sha256Digest | None = None
    versions: ReleaseVersions
    sample_count: NonNegativeInt
    metrics: tuple[ReleaseMetric, ...]
    guardrails: tuple[ReleaseGuardrail, ...]
    artifacts: tuple[ReleaseArtifact, ...]
    latency: ReleaseLatencyEvidence
    cost: ReleaseCostEvidence
    shadow: ShadowEvidenceAggregate | None
    rollback_plan: RollbackPlan = RollbackPlan()
    source: EvidenceSource | None = None

    @field_validator("generated_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: datetime, info: Any) -> datetime:
        return _utc_datetime(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if self.generated_at >= self.expires_at:
            raise ValueError("release evidence must expire after it is generated")
        if (
            self.release_stage is not self.stage
            or self.parent_evidence_digest != self.previous_evidence_digest
        ):
            raise ValueError("release snapshot issuance must match its stage and parent")
        if self.stage is ReleaseStage.OFFLINE:
            if self.previous_evidence_digest is not None:
                raise ValueError("OFFLINE evidence must not claim previous-stage evidence")
        elif self.previous_evidence_digest is None:
            raise ValueError("non-OFFLINE evidence requires previous-stage evidence digest")
        metric_names = tuple(metric.name for metric in self.metrics)
        if metric_names != tuple(
            sorted(metric_names, key=lambda name: list(ReleaseMetricName).index(name))
        ) or len(metric_names) != len(set(metric_names)):
            raise ValueError("release metrics must be unique and in canonical order")
        guardrail_names = tuple(guardrail.name for guardrail in self.guardrails)
        if guardrail_names != tuple(
            sorted(guardrail_names, key=lambda name: GUARDRAIL_ORDER.index(name))
        ) or len(guardrail_names) != len(set(guardrail_names)):
            raise ValueError("release guardrails must be unique and in canonical order")
        artifact_kinds = tuple(artifact.kind for artifact in self.artifacts)
        if artifact_kinds != tuple(
            sorted(artifact_kinds, key=lambda kind: ARTIFACT_ORDER.index(kind))
        ) or len(artifact_kinds) != len(set(artifact_kinds)):
            raise ValueError("release artifacts must be unique and in canonical order")
        return self


class ReleaseDecisionStatus(StrEnum):
    PROMOTION_APPROVED = "PROMOTION_APPROVED"
    HOLD_DEFAULT_V1 = "HOLD_DEFAULT_V1"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"


class ReleaseDecision(_ReleaseModel):
    contract_version: Literal[RELEASE_DECISION_VERSION] = RELEASE_DECISION_VERSION
    evaluator_version: Literal[RELEASE_EVALUATOR_VERSION] = RELEASE_EVALUATOR_VERSION
    status: ReleaseDecisionStatus
    evidence_stage: ReleaseStage
    trusted_deployed_stage: ReleaseStage | None
    next_stage: ReleaseStage | None
    evidence_digest: Sha256Digest | None
    blockers: tuple[SafeText, ...]
    runtime_engine: Literal["v1"] = DEFAULT_RUNTIME_ENGINE
    configuration_changed: Literal[False] = False
    engineering_pass_is_release_quality: Literal[False] = False
    release_quality_passed: StrictBool
    rollback_plan: RollbackPlan = RollbackPlan()

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        approved = self.status is ReleaseDecisionStatus.PROMOTION_APPROVED
        if self.release_quality_passed is not approved:
            raise ValueError("release_quality_passed contradicts decision status")
        if approved and self.blockers:
            raise ValueError("approved promotion must not contain blockers")
        if not approved and not self.blockers:
            raise ValueError("fail-closed decision must contain blockers")
        if approved and self.trusted_deployed_stage is not self.evidence_stage:
            raise ValueError("approved promotion must match trusted deployed stage")
        expected_next = (
            RELEASE_STAGE_ORDER[_STAGE_INDEX[self.evidence_stage] + 1]
            if _STAGE_INDEX[self.evidence_stage] + 1 < len(RELEASE_STAGE_ORDER)
            else None
        )
        if approved and self.next_stage is not expected_next:
            raise ValueError("approved promotion has an invalid next stage")
        if not approved and self.next_stage is not None:
            raise ValueError("blocked decision must not expose a next stage")
        return self


def _artifact_version_map(policy: ReleasePolicy) -> dict[ReleaseArtifactKind, str]:
    return {item.kind: item.artifact_version for item in policy.artifact_versions}


def _verifier_registry(
    verifiers: Sequence[EvidenceVerifier],
) -> tuple[dict[tuple[str, str], EvidenceVerifier], list[str]]:
    registry: dict[tuple[str, str], EvidenceVerifier] = {}
    blockers: list[str] = []
    for verifier in verifiers:
        try:
            verifier_id = _validate_safe_text(verifier.verifier_id)
            verifier_version = _validate_safe_text(verifier.verifier_version)
            if type(verifier.test_only) is not bool or not callable(verifier.verify):
                raise ValueError("invalid verifier contract")
        except (AttributeError, TypeError, ValueError):
            blockers.append("verifier:invalid_contract")
            continue
        key = (verifier_id, verifier_version)
        if key in registry:
            blockers.append(f"verifier:{key[0]}@{key[1]}:duplicate")
        registry[key] = verifier
    return registry, blockers


def _attestation_blockers(
    *,
    component_name: str,
    component: _AttestedReleaseModel,
    source: EvidenceSource | None,
    evidence_class: EvidenceClass,
    allow_test_evidence: bool,
    registry: Mapping[tuple[str, str], EvidenceVerifier],
    expected_stage: ReleaseStage,
    expected_parent_digest: str | None,
) -> list[str]:
    blockers: list[str] = []
    attestation = component.attestation
    if (
        component.release_stage is not expected_stage
        or component.parent_evidence_digest != expected_parent_digest
    ):
        blockers.append(f"{component_name}:component_stage_parent_mismatch")
    if source is None:
        return [*blockers, f"{component_name}:immutable_source_missing"]
    if (
        source.release_stage is not expected_stage
        or source.parent_evidence_digest != expected_parent_digest
    ):
        blockers.append(f"{component_name}:source_stage_parent_mismatch")
    if attestation is None:
        return [*blockers, f"{component_name}:attestation_missing"]
    if (
        attestation.release_stage is not expected_stage
        or attestation.parent_evidence_digest != expected_parent_digest
    ):
        blockers.append(f"{component_name}:attestation_stage_parent_mismatch")
    if (
        attestation.subject_digest != component.attested_digest()
        or attestation.source_artifact_digest != source.artifact_digest
        or attestation.source_artifact_version != source.artifact_version
    ):
        blockers.append(f"{component_name}:attestation_binding_mismatch")
    if blockers:
        return blockers
    verifier = registry.get((attestation.verifier_id, attestation.verifier_version))
    if verifier is None:
        return [f"{component_name}:verifier_unavailable"]
    if evidence_class is EvidenceClass.TEST:
        if not allow_test_evidence:
            blockers.append("test_evidence:not_authorized")
        if not verifier.test_only:
            blockers.append(f"{component_name}:test_verifier_required")
    elif verifier.test_only:
        blockers.append(f"{component_name}:test_verifier_forbidden_in_production")
    if blockers:
        return blockers
    try:
        verified = verifier.verify(attestation)
    except TimeoutError:
        return [f"{component_name}:verifier_timeout"]
    except Exception:
        return [f"{component_name}:verifier_error"]
    if not verified:
        blockers.append(f"{component_name}:signature_invalid")
    return blockers


def _threshold_passes(requirement: GateRequirement, value: float) -> bool:
    if requirement.operator is ThresholdOperator.GREATER_THAN_OR_EQUAL:
        return value >= requirement.threshold
    if requirement.operator is ThresholdOperator.LESS_THAN_OR_EQUAL:
        return value <= requirement.threshold
    return value == requirement.threshold


def _evaluate_single_snapshot(
    evidence: ReleaseEvidence,
    *,
    policy: ReleasePolicy | None,
    evaluated_at: datetime,
    allow_test_evidence: bool,
    registry: Mapping[tuple[str, str], EvidenceVerifier],
) -> list[str]:
    blockers: list[str] = []
    if (
        policy is not None
        and evidence.source is not None
        and evidence.source.artifact_version != policy.release_artifact_version
    ):
        blockers.append("release_snapshot:artifact_version_mismatch")
    blockers.extend(
        _attestation_blockers(
            component_name="release_snapshot",
            component=evidence,
            source=evidence.source,
            evidence_class=evidence.evidence_class,
            allow_test_evidence=allow_test_evidence,
            registry=registry,
            expected_stage=evidence.stage,
            expected_parent_digest=evidence.previous_evidence_digest,
        )
    )
    if policy is None:
        blockers.append("release_policy:missing")
    else:
        if evidence.versions != policy.expected_versions:
            blockers.append("release_versions:stale_or_mismatched")
    if evidence.evidence_class is EvidenceClass.TEST and not allow_test_evidence:
        blockers.append("test_evidence:not_authorized")
    if evidence.generated_at > evaluated_at:
        blockers.append("freshness:evidence_from_future")
    if evidence.expires_at <= evaluated_at:
        blockers.append("freshness:evidence_expired")
    if (evaluated_at - evidence.generated_at).total_seconds() > MAX_EVIDENCE_AGE_SECONDS:
        blockers.append("freshness:evidence_too_old")
    if evidence.sample_count < MINIMUM_STAGE_SAMPLES[evidence.stage]:
        blockers.append(f"stage:{evidence.stage.value}:sample_count_below_minimum")

    metrics = {metric.name: metric for metric in evidence.metrics}
    for requirement in GATE_REQUIREMENTS:
        component_name = f"metric:{requirement.name.value}"
        metric = metrics.get(requirement.name)
        if metric is None:
            blockers.append(f"{component_name}:missing")
            continue
        if metric.unit is not requirement.unit:
            blockers.append(f"{component_name}:unit_mismatch")
        if metric.status is not EvidenceStatus.AVAILABLE:
            blockers.append(f"{component_name}:status_{metric.status.value.lower()}")
        if metric.denominator < requirement.minimum_denominator:
            blockers.append(f"{component_name}:denominator_below_minimum")
        if metric.value is not None and not _threshold_passes(requirement, metric.value):
            blockers.append(f"{component_name}:threshold_failed")
        if policy is not None and metric.source.artifact_version != (
            policy.metric_artifact_version
        ):
            blockers.append(f"{component_name}:artifact_version_mismatch")
        blockers.extend(
            _attestation_blockers(
                component_name=component_name,
                component=metric,
                source=metric.source,
                evidence_class=evidence.evidence_class,
                allow_test_evidence=allow_test_evidence,
                registry=registry,
                expected_stage=evidence.stage,
                expected_parent_digest=evidence.previous_evidence_digest,
            )
        )

    guardrails = {guardrail.name: guardrail for guardrail in evidence.guardrails}
    for name in GUARDRAIL_ORDER:
        component_name = f"guardrail:{name.value}"
        guardrail = guardrails.get(name)
        if guardrail is None:
            blockers.append(f"{component_name}:missing")
            continue
        if guardrail.status is not GuardrailStatus.PASS:
            blockers.append(f"{component_name}:status_{guardrail.status.value.lower()}")
        if guardrail.denominator < MINIMUM_STAGE_SAMPLES[evidence.stage]:
            blockers.append(f"{component_name}:denominator_below_stage_minimum")
        if policy is not None and guardrail.source.artifact_version != (
            policy.guardrail_artifact_version
        ):
            blockers.append(f"{component_name}:artifact_version_mismatch")
        blockers.extend(
            _attestation_blockers(
                component_name=component_name,
                component=guardrail,
                source=guardrail.source,
                evidence_class=evidence.evidence_class,
                allow_test_evidence=allow_test_evidence,
                registry=registry,
                expected_stage=evidence.stage,
                expected_parent_digest=evidence.previous_evidence_digest,
            )
        )

    artifact_versions = {} if policy is None else _artifact_version_map(policy)
    artifacts = {artifact.kind: artifact for artifact in evidence.artifacts}
    for kind in ARTIFACT_ORDER:
        component_name = f"artifact:{kind.value}"
        artifact = artifacts.get(kind)
        if artifact is None:
            blockers.append(f"{component_name}:missing")
            continue
        expected_disposition = REQUIRED_ARTIFACT_DISPOSITION[kind]
        if artifact.disposition is not expected_disposition:
            blockers.append(f"{component_name}:disposition_{artifact.disposition.value.lower()}")
        if artifact.evidence_status is not EvidenceStatus.AVAILABLE:
            blockers.append(f"{component_name}:status_{artifact.evidence_status.value.lower()}")
        if (
            artifact.source is not None
            and policy is not None
            and artifact.source.artifact_version != artifact_versions[kind]
        ):
            blockers.append(f"{component_name}:artifact_version_mismatch")
        blockers.extend(
            _attestation_blockers(
                component_name=component_name,
                component=artifact,
                source=artifact.source,
                evidence_class=evidence.evidence_class,
                allow_test_evidence=allow_test_evidence,
                registry=registry,
                expected_stage=evidence.stage,
                expected_parent_digest=evidence.previous_evidence_digest,
            )
        )

    latency_name = "latency_summary"
    if evidence.latency.status is not EvidenceStatus.AVAILABLE:
        blockers.append(f"{latency_name}:status_{evidence.latency.status.value.lower()}")
    if (
        evidence.latency.sample_count
        < GATE_REQUIREMENT_BY_NAME[ReleaseMetricName.LATENCY_P95_MS].minimum_denominator
    ):
        blockers.append(f"{latency_name}:denominator_below_minimum")
    if (
        policy is not None
        and evidence.latency.source is not None
        and evidence.latency.source.artifact_version != policy.latency_artifact_version
    ):
        blockers.append(f"{latency_name}:artifact_version_mismatch")
    blockers.extend(
        _attestation_blockers(
            component_name=latency_name,
            component=evidence.latency,
            source=evidence.latency.source,
            evidence_class=evidence.evidence_class,
            allow_test_evidence=allow_test_evidence,
            registry=registry,
            expected_stage=evidence.stage,
            expected_parent_digest=evidence.previous_evidence_digest,
        )
    )
    p95_metric = metrics.get(ReleaseMetricName.LATENCY_P95_MS)
    if (
        p95_metric is not None
        and p95_metric.value is not None
        and (
            evidence.latency.p95_ms != p95_metric.value
            or evidence.latency.sample_count != p95_metric.denominator
            or evidence.latency.source != p95_metric.source
        )
    ):
        blockers.append("latency_summary:metric_binding_mismatch")

    cost_name = "cost_summary"
    if evidence.cost.status is not EvidenceStatus.AVAILABLE:
        blockers.append(f"{cost_name}:status_{evidence.cost.status.value.lower()}")
    if (
        policy is not None
        and evidence.cost.source is not None
        and evidence.cost.source.artifact_version != policy.cost_artifact_version
    ):
        blockers.append(f"{cost_name}:artifact_version_mismatch")
    blockers.extend(
        _attestation_blockers(
            component_name=cost_name,
            component=evidence.cost,
            source=evidence.cost.source,
            evidence_class=evidence.evidence_class,
            allow_test_evidence=allow_test_evidence,
            registry=registry,
            expected_stage=evidence.stage,
            expected_parent_digest=evidence.previous_evidence_digest,
        )
    )

    if evidence.shadow is not None:
        shadow_name = "shadow_evidence"
        if (
            policy is not None
            and evidence.shadow.source is not None
            and evidence.shadow.source.artifact_version != policy.shadow_artifact_version
        ):
            blockers.append(f"{shadow_name}:artifact_version_mismatch")
        blockers.extend(
            _attestation_blockers(
                component_name=shadow_name,
                component=evidence.shadow,
                source=evidence.shadow.source,
                evidence_class=evidence.evidence_class,
                allow_test_evidence=allow_test_evidence,
                registry=registry,
                expected_stage=evidence.stage,
                expected_parent_digest=evidence.previous_evidence_digest,
            )
        )
        if evidence.shadow.total_samples < MINIMUM_STAGE_SAMPLES[evidence.stage]:
            blockers.append(f"{shadow_name}:sample_count_below_minimum")
        if evidence.shadow.total_samples != evidence.sample_count:
            blockers.append(f"{shadow_name}:snapshot_denominator_mismatch")
        if evidence.shadow.latency_status is not EvidenceStatus.AVAILABLE:
            blockers.append(
                f"{shadow_name}:latency_status_{evidence.shadow.latency_status.value.lower()}"
            )
        for field_name in (
            "error_rate",
            "exception_rate",
            "fallback_rate",
            "compatibility_failure_rate",
            "security_violation_rate",
            "unauthorized_leakage_rate",
            "secret_leakage_rate",
            "model_unavailable_rate",
            "semantic_fallback_rate",
        ):
            rate = getattr(evidence.shadow, field_name)
            if rate.status is not EvidenceStatus.AVAILABLE:
                blockers.append(f"{shadow_name}:{field_name}:status_{rate.status.value.lower()}")
        if (
            evidence.shadow.exception_rate.value is not None
            and evidence.shadow.exception_rate.value > 0.01
        ):
            blockers.append(f"{shadow_name}:exception_rate_threshold_failed")
        for field_name in (
            "compatibility_failure_rate",
            "security_violation_rate",
            "unauthorized_leakage_rate",
            "secret_leakage_rate",
        ):
            rate = getattr(evidence.shadow, field_name)
            if rate.value not in {None, 0.0}:
                blockers.append(f"{shadow_name}:{field_name}:threshold_failed")
    elif evidence.stage is not ReleaseStage.OFFLINE:
        blockers.append("shadow_evidence:missing")

    return blockers


def _stage_from_untrusted(value: object) -> ReleaseStage | None:
    raw_stage: object
    if isinstance(value, ReleaseEvidence):
        raw_stage = value.stage
    elif isinstance(value, Mapping):
        raw_stage = value.get("stage")
    else:
        return None
    try:
        return ReleaseStage(raw_stage)
    except (TypeError, ValueError):
        pass
    return None


def _trusted_stage_authority(value: object) -> ReleaseStage | None:
    if isinstance(value, ReleaseStage):
        return value
    if type(value) is str:
        try:
            return ReleaseStage(value)
        except ValueError:
            pass
    return None


def _fail_closed_decision(
    *,
    evidence_stage: ReleaseStage,
    severity_stage: ReleaseStage,
    trusted_deployed_stage: ReleaseStage | None,
    blockers: Sequence[str],
    evidence_digest: str | None,
) -> ReleaseDecision:
    status = (
        ReleaseDecisionStatus.ROLLBACK_REQUIRED
        if _STAGE_INDEX[severity_stage] >= _STAGE_INDEX[ReleaseStage.CANARY_5]
        else ReleaseDecisionStatus.HOLD_DEFAULT_V1
    )
    return ReleaseDecision(
        status=status,
        evidence_stage=evidence_stage,
        trusted_deployed_stage=trusted_deployed_stage,
        next_stage=None,
        evidence_digest=evidence_digest,
        blockers=tuple(sorted(set(blockers))),
        release_quality_passed=False,
    )


def evaluate_release(
    evidence: ReleaseEvidence | Mapping[str, Any],
    *,
    deployed_stage: ReleaseStage | str,
    policy: ReleasePolicy | Mapping[str, Any] | None = None,
    verifiers: Sequence[EvidenceVerifier] = (),
    prior_evidence: Sequence[ReleaseEvidence | Mapping[str, Any]] = (),
    evaluated_at: datetime | None = None,
    allow_test_evidence: bool = False,
    verification_timed_out: bool = False,
) -> ReleaseDecision:
    """Verify one complete stage chain and return a deterministic no-side-effect decision."""

    trusted_stage = _trusted_stage_authority(deployed_stage)
    untrusted_stage = _stage_from_untrusted(evidence)
    if trusted_stage is None:
        return _fail_closed_decision(
            evidence_stage=untrusted_stage or ReleaseStage.DEFAULT_V2,
            severity_stage=ReleaseStage.DEFAULT_V2,
            trusted_deployed_stage=None,
            blockers=("trusted_deployed_stage:invalid",),
            evidence_digest=None,
        )
    try:
        candidate = ReleaseEvidence.model_validate(
            evidence.model_dump(mode="python", round_trip=True)
            if isinstance(evidence, ReleaseEvidence)
            else evidence
        )
    except (TypeError, ValueError, ValidationError):
        return _fail_closed_decision(
            evidence_stage=untrusted_stage or trusted_stage,
            severity_stage=trusted_stage,
            trusted_deployed_stage=trusted_stage,
            blockers=("release_contract:invalid_or_tampered",),
            evidence_digest=None,
        )
    if evaluated_at is None:
        return _fail_closed_decision(
            evidence_stage=candidate.stage,
            severity_stage=trusted_stage,
            trusted_deployed_stage=trusted_stage,
            blockers=("evaluated_at:missing",),
            evidence_digest=candidate.canonical_sha256(),
        )
    try:
        observed_at = _utc_datetime(
            evaluated_at,
            field_name="evaluated_at",
        )
    except (AttributeError, TypeError, ValueError):
        return _fail_closed_decision(
            evidence_stage=candidate.stage,
            severity_stage=trusted_stage,
            trusted_deployed_stage=trusted_stage,
            blockers=("evaluated_at:invalid",),
            evidence_digest=candidate.canonical_sha256(),
        )
    blockers: list[str] = []
    if candidate.stage is not trusted_stage:
        blockers.append("trusted_deployed_stage:candidate_stage_mismatch")
    if type(allow_test_evidence) is not bool:
        blockers.append("test_evidence:authorization_flag_invalid")
        allow_test_evidence = False
    if type(verification_timed_out) is not bool:
        blockers.append("release_verification:timeout_flag_invalid")
        verification_timed_out = True
    try:
        registry, verifier_blockers = _verifier_registry(verifiers)
    except (AttributeError, TypeError, ValueError):
        registry = {}
        verifier_blockers = ["verifier_registry:invalid"]
    blockers.extend(verifier_blockers)
    validated_policy: ReleasePolicy | None
    if policy is None:
        validated_policy = None
    else:
        try:
            validated_policy = ReleasePolicy.model_validate(
                policy.model_dump(mode="python", round_trip=True)
                if isinstance(policy, ReleasePolicy)
                else policy
            )
        except (TypeError, ValueError, ValidationError):
            validated_policy = None
            blockers.append("release_policy:invalid_or_tampered")
    if verification_timed_out:
        blockers.append("release_verification:timeout")

    if (
        isinstance(prior_evidence, (str, bytes, Mapping))
        or not isinstance(prior_evidence, Sequence)
        or len(prior_evidence) > len(RELEASE_STAGE_ORDER) - 1
    ):
        return _fail_closed_decision(
            evidence_stage=candidate.stage,
            severity_stage=trusted_stage,
            trusted_deployed_stage=trusted_stage,
            blockers=(*blockers, "previous_stage:chain_invalid_or_unbounded"),
            evidence_digest=candidate.canonical_sha256(),
        )
    parsed_prior: list[ReleaseEvidence] = []
    for item in prior_evidence:
        try:
            parsed_prior.append(
                ReleaseEvidence.model_validate(
                    item.model_dump(mode="python", round_trip=True)
                    if isinstance(item, ReleaseEvidence)
                    else item
                )
            )
        except (TypeError, ValueError, ValidationError):
            blockers.append("previous_stage:invalid_or_tampered")

    expected_prior_stages = RELEASE_STAGE_ORDER[: _STAGE_INDEX[candidate.stage]]
    actual_prior_stages = tuple(item.stage for item in parsed_prior)
    if len(parsed_prior) != len(prior_evidence) or actual_prior_stages != (expected_prior_stages):
        blockers.append("previous_stage:chain_missing_or_skipped")
    chain = [*parsed_prior, candidate]
    for index, item in enumerate(chain):
        expected_digest = None if index == 0 else chain[index - 1].canonical_sha256()
        if item.previous_evidence_digest != expected_digest:
            blockers.append(f"stage:{item.stage.value}:previous_digest_mismatch")
        snapshot_blockers = _evaluate_single_snapshot(
            item,
            policy=validated_policy,
            evaluated_at=observed_at,
            allow_test_evidence=allow_test_evidence,
            registry=registry,
        )
        if index < len(chain) - 1 and snapshot_blockers:
            blockers.append(f"previous_stage:{item.stage.value}:unverified")
        else:
            blockers.extend(snapshot_blockers)

    digest = candidate.canonical_sha256()
    if blockers:
        return _fail_closed_decision(
            evidence_stage=candidate.stage,
            severity_stage=trusted_stage,
            trusted_deployed_stage=trusted_stage,
            blockers=blockers,
            evidence_digest=digest,
        )
    next_index = _STAGE_INDEX[candidate.stage] + 1
    next_stage = RELEASE_STAGE_ORDER[next_index] if next_index < len(RELEASE_STAGE_ORDER) else None
    return ReleaseDecision(
        status=ReleaseDecisionStatus.PROMOTION_APPROVED,
        evidence_stage=candidate.stage,
        trusted_deployed_stage=trusted_stage,
        next_stage=next_stage,
        evidence_digest=digest,
        blockers=(),
        release_quality_passed=True,
    )


_CURRENT_OBSERVED_AT = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
_CURRENT_CB_RUN_IDS = MappingProxyType(
    {
        ReleaseArtifactKind.C_B0: CB0_QUALIFIED_RUN_ID,
        ReleaseArtifactKind.C_B1: (
            "evaluation-run://project-code-golden-v2/33f76ed55a3d4c9ab1060a54bfa13d35"
        ),
        ReleaseArtifactKind.C_B2: (
            "evaluation-run://project-code-golden-v2/6fad2dd1c67846668ba38045ddae0064"
        ),
        ReleaseArtifactKind.C_B3: (
            "evaluation-run://project-code-golden-v2/92f8d8e190f449bab9ba253227473419"
        ),
        ReleaseArtifactKind.C_B4: (
            "evaluation-run://project-code-golden-v2/a22b322a141741ae9a710aa347fbf032"
        ),
        ReleaseArtifactKind.C_B5: (
            "evaluation-run://project-code-golden-v2/baf5b9eb49464ca79920030fd1c772de"
        ),
        ReleaseArtifactKind.C_B6: CB6_PROVISIONAL_RUN_ID,
    }
)


def current_release_evidence(
    *,
    observed_at: datetime = _CURRENT_OBSERVED_AT,
) -> ReleaseEvidence:
    """Return the fixed reviewed C7 input truth without consulting mutable services."""

    observed_at = _utc_datetime(observed_at, field_name="observed_at")
    artifacts: list[ReleaseArtifact] = []
    for kind in ARTIFACT_ORDER:
        if kind is ReleaseArtifactKind.CODE_GOLDEN:
            artifact_id = "dataset://code-golden-v2/released"
            disposition = ArtifactDisposition.QUALIFIED
            reason = "release_attestation_unavailable"
        elif kind in _CURRENT_CB_RUN_IDS:
            artifact_id = _CURRENT_CB_RUN_IDS[kind]
            if kind is ReleaseArtifactKind.C_B0:
                disposition = ArtifactDisposition.QUALIFIED
                reason = "only_qualified_global_baseline_but_release_attestation_unavailable"
            elif kind is ReleaseArtifactKind.C_B6:
                disposition = ArtifactDisposition.PROVISIONAL_NOT_QUALIFIED
                reason = "verified_provisional_not_qualified"
            else:
                disposition = ArtifactDisposition.NOT_QUALIFIED
                reason = "verified_not_qualified"
        else:
            artifact_id = f"artifact://c7-release/{kind.value}"
            disposition = ArtifactDisposition.UNAVAILABLE
            reason = "required_release_artifact_unavailable"
        artifacts.append(
            ReleaseArtifact(
                release_stage=ReleaseStage.OFFLINE,
                parent_evidence_digest=None,
                kind=kind,
                artifact_id=artifact_id,
                evidence_status=EvidenceStatus.UNAVAILABLE,
                disposition=disposition,
                reason=reason,
            )
        )
    zero_shadow = aggregate_shadow_evidence(
        (),
        release_stage=ReleaseStage.OFFLINE,
        parent_evidence_digest=None,
    )
    return ReleaseEvidence(
        release_stage=ReleaseStage.OFFLINE,
        parent_evidence_digest=None,
        evidence_class=EvidenceClass.PRODUCTION,
        stage=ReleaseStage.OFFLINE,
        generated_at=observed_at,
        expires_at=observed_at + timedelta(days=1),
        versions=ReleaseVersions(
            code_golden_version="code-golden-v2",
            evaluation_profile_version="unavailable",
            builder_version="unavailable",
            index_version="unavailable",
            model_version="unselected",
            scip_version="unselected",
        ),
        sample_count=0,
        metrics=(),
        guardrails=(),
        artifacts=tuple(artifacts),
        latency=ReleaseLatencyEvidence(
            release_stage=ReleaseStage.OFFLINE,
            parent_evidence_digest=None,
            status=EvidenceStatus.UNAVAILABLE,
            sample_count=0,
            p50_ms=None,
            p95_ms=None,
            p99_ms=None,
        ),
        cost=ReleaseCostEvidence(
            release_stage=ReleaseStage.OFFLINE,
            parent_evidence_digest=None,
            status=EvidenceStatus.UNAVAILABLE,
            index_bytes=None,
            ingest_time_ms=None,
        ),
        shadow=zero_shadow,
    )


__all__ = [
    "ARTIFACT_ORDER",
    "CB0_QUALIFIED_RUN_ID",
    "CB6_PROVISIONAL_RUN_ID",
    "COST_EVIDENCE_VERSION",
    "DEFAULT_RUNTIME_ENGINE",
    "GATE_REQUIREMENTS",
    "GATE_REQUIREMENT_BY_NAME",
    "GUARDRAIL_ORDER",
    "LATENCY_EVIDENCE_VERSION",
    "MAX_EVIDENCE_AGE_SECONDS",
    "MAX_SHADOW_BATCHES",
    "MAX_SHADOW_SAMPLES",
    "MINIMUM_STAGE_SAMPLES",
    "RELEASE_ATTESTATION_VERSION",
    "RELEASE_ARTIFACT_VERSION",
    "RELEASE_CONTRACT_VERSION",
    "RELEASE_DECISION_VERSION",
    "RELEASE_EVALUATOR_VERSION",
    "RELEASE_EVIDENCE_VERSION",
    "RELEASE_GUARDRAIL_VERSION",
    "RELEASE_METRIC_VERSION",
    "RELEASE_POLICY_VERSION",
    "RELEASE_SOURCE_VERSION",
    "RELEASE_STAGE_ORDER",
    "REQUIRED_ARTIFACT_DISPOSITION",
    "ROLLBACK_ACTIONS",
    "ROLLBACK_PLAN_VERSION",
    "SHADOW_AGGREGATE_VERSION",
    "ArtifactDisposition",
    "ArtifactVersionExpectation",
    "EvidenceAttestation",
    "EvidenceClass",
    "EvidenceSource",
    "EvidenceStatus",
    "EvidenceVerifier",
    "GateRequirement",
    "GuardrailName",
    "GuardrailStatus",
    "MetricUnit",
    "ReleaseArtifact",
    "ReleaseArtifactKind",
    "ReleaseCostEvidence",
    "ReleaseDecision",
    "ReleaseDecisionStatus",
    "ReleaseEvidence",
    "ReleaseGuardrail",
    "ReleaseLatencyEvidence",
    "ReleaseMetric",
    "ReleaseMetricName",
    "ReleasePolicy",
    "ReleaseStage",
    "ReleaseVersions",
    "RollbackAction",
    "RollbackPlan",
    "SanitizedShadowCounters",
    "ShadowEvidenceAggregate",
    "ShadowRate",
    "TestHMACSHA256Verifier",
    "ThresholdOperator",
    "aggregate_shadow_evidence",
    "create_test_attestation",
    "current_release_evidence",
    "evaluate_release",
]
