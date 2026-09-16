"""Deterministic raw V2 authority contracts.

This module is deliberately additive and side-effect free.  It owns only canonical
value projection, timestamps, digests, identity payloads, typed IDs, source-domain
mapping, byte limits, and the frozen reason policy used by later raw V2 tasks.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Any, Self, cast

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StrictStr,
    field_validator,
    model_validator,
)

RAW_V2_CONTRACT_VERSION = "raw-authority-contract-v2"
CANONICAL_JSON_VERSION = "canonical-json-v1"
RAW_V2_VECTOR_SCHEMA_VERSION = "raw-v2-canonical-vectors-v1"

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RAW_OBJECT_ID_RE = re.compile(r"^raw-v2:[0-9a-f]{64}$")
_SOURCE_EVENT_ID_RE = re.compile(r"^source-event-v2:[0-9a-f]{64}$")
_LOCATOR_ID_RE = re.compile(r"^raw-locator-v2:[0-9a-f]{64}$")
_RFC3339_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T"
    r"(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d{1,6})?"
    r"(?P<zone>Z|[+-]\d{2}:\d{2})$"
)


class DigestDomain(StrEnum):
    VISIBILITY_PARTITION = "visibility-partition-v2"
    REQUEST_VISIBILITY_SCOPE = "request-visibility-scope-v2"
    RAW_LOGICAL_IDENTITY = "raw-logical-identity-v2"
    SOURCE_EVENT_IDEMPOTENCY = "source-event-idempotency-v2"
    RAW_SELECTOR = "raw-selector-v2"
    RAW_EVIDENCE_BINDING = "raw-evidence-binding-v2"


class SourceDomain(StrEnum):
    CODE = "code"
    CODEX = "codex"
    EXPERIMENT = "experiment"
    NOTEBOOK = "notebook"
    DOCUMENT = "document"
    WORKSPACE = "workspace"


class SizeLimit(StrEnum):
    PORTABLE_ID = "portable_id"
    SOURCE_OBJECT_ID = "source_object_id"
    MEDIA_TYPE = "media_type"
    TRACE_MUTATION_ID = "trace_mutation_id"
    REASON_DERIVATION_ADAPTER_SCHEMA = "reason_derivation_adapter_schema"
    CANONICAL_JSON = "canonical_json"
    SELECTED_OUTPUT = "selected_output"


class RawV2Reason(StrEnum):
    CANONICALIZATION_MISMATCH = "canonicalization_mismatch"
    CANONICAL_DUPLICATE_KEY = "canonical_duplicate_key"
    TIMESTAMP_INVALID = "timestamp_invalid"
    CONTRACT_SIZE_EXCEEDED = "contract_size_exceeded"
    EXPECTED_DIGEST_MISMATCH = "expected_digest_mismatch"
    VISIBILITY_MISMATCH = "visibility_mismatch"
    SOURCE_ADAPTER_UNREGISTERED = "source_adapter_unregistered"
    STABLE_VERSION_UNAVAILABLE = "stable_version_unavailable"
    NON_IDEMPOTENT_REJECTED = "non_idempotent_rejected"
    IDEMPOTENCY_PAYLOAD_CONFLICT = "idempotency_payload_conflict"
    LOGICAL_IDENTITY_PAYLOAD_CONFLICT = "logical_identity_payload_conflict"
    REFERENCE_ONLY_UNAVAILABLE = "reference_only_unavailable"
    BINDING_NOT_FOUND = "binding_not_found"
    BINDING_AMBIGUOUS = "binding_ambiguous"
    BINDING_DIGEST_MISMATCH = "binding_digest_mismatch"
    RAW_STATE_INVALID = "raw_state_invalid"
    RAW_NOT_FOUND = "raw_not_found"
    RAW_QUARANTINED = "raw_quarantined"
    RAW_TOMBSTONED = "raw_tombstoned"
    DERIVATION_INVALIDATED = "derivation_invalidated"
    GENERATION_MISMATCH = "generation_mismatch"
    STABLE_VERSION_MISMATCH = "stable_version_mismatch"
    SOURCE_IDENTITY_MISMATCH = "source_identity_mismatch"
    BLOB_UNAVAILABLE = "blob_unavailable"
    BLOB_CORRUPT = "blob_corrupt"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    STORAGE_OUTSIDE_ROOT = "storage_outside_root"
    STORAGE_SYMLINK = "storage_symlink"
    STORAGE_NOT_REGULAR = "storage_not_regular"
    BYTE_LENGTH_MISMATCH = "byte_length_mismatch"
    RAW_DIGEST_MISMATCH = "raw_digest_mismatch"
    SELECTOR_INVALID = "selector_invalid"
    SELECTOR_OUT_OF_BOUNDS = "selector_out_of_bounds"
    PARSE_ARTIFACT_MISSING = "parse_artifact_missing"
    PARSE_ARTIFACT_MISMATCH = "parse_artifact_mismatch"
    SELECTED_DIGEST_MISMATCH = "selected_digest_mismatch"
    UNSAFE_CONTENT = "unsafe_content"
    UNSUPPORTED_MEDIA = "unsupported_media"
    PROJECT_MISMATCH = "project_mismatch"
    ACL_DENIED = "acl_denied"
    MIGRATION_NOT_AUTHORIZED = "migration_not_authorized"
    MIGRATION_INPUT_CHANGED = "migration_input_changed"
    MIGRATION_UNCLASSIFIED = "migration_unclassified"
    MIGRATION_COUNT_MISMATCH = "migration_count_mismatch"


class ExceptionClass(StrEnum):
    INVALID_INPUT = "invalid_input"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    FORBIDDEN = "forbidden"
    INTEGRITY = "integrity"


class OutwardDisposition(StrEnum):
    INVALID_REQUEST = "invalid_request"
    CONFLICT = "conflict"
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"
    FORBIDDEN = "forbidden"
    INTERNAL_INTEGRITY_FAILURE = "internal_integrity_failure"


class AuditSeverity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


@dataclass(frozen=True, slots=True)
class ReasonPolicy:
    exception_class: ExceptionClass
    http_status: int
    audit_severity: AuditSeverity
    outward_disposition: OutwardDisposition


def _policy(
    exception_class: ExceptionClass,
    http_status: int,
    audit_severity: AuditSeverity,
    outward_disposition: OutwardDisposition,
) -> ReasonPolicy:
    return ReasonPolicy(
        exception_class=exception_class,
        http_status=http_status,
        audit_severity=audit_severity,
        outward_disposition=outward_disposition,
    )


REASON_POLICY: Mapping[RawV2Reason, ReasonPolicy] = MappingProxyType(
    {
        RawV2Reason.CANONICALIZATION_MISMATCH: _policy(
            ExceptionClass.INVALID_INPUT,
            422,
            AuditSeverity.P2,
            OutwardDisposition.INVALID_REQUEST,
        ),
        RawV2Reason.CANONICAL_DUPLICATE_KEY: _policy(
            ExceptionClass.INVALID_INPUT,
            422,
            AuditSeverity.P2,
            OutwardDisposition.INVALID_REQUEST,
        ),
        RawV2Reason.TIMESTAMP_INVALID: _policy(
            ExceptionClass.INVALID_INPUT,
            422,
            AuditSeverity.P2,
            OutwardDisposition.INVALID_REQUEST,
        ),
        RawV2Reason.CONTRACT_SIZE_EXCEEDED: _policy(
            ExceptionClass.INVALID_INPUT,
            413,
            AuditSeverity.P2,
            OutwardDisposition.INVALID_REQUEST,
        ),
        RawV2Reason.EXPECTED_DIGEST_MISMATCH: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P1,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.NON_IDEMPOTENT_REJECTED: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P1,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P1,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.BINDING_AMBIGUOUS: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P1,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.RAW_STATE_INVALID: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P1,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.GENERATION_MISMATCH: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P1,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.STABLE_VERSION_MISMATCH: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P1,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.SOURCE_IDENTITY_MISMATCH: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P0,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.MIGRATION_INPUT_CHANGED: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P1,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT: _policy(
            ExceptionClass.CONFLICT,
            409,
            AuditSeverity.P0,
            OutwardDisposition.CONFLICT,
        ),
        RawV2Reason.SOURCE_ADAPTER_UNREGISTERED: _policy(
            ExceptionClass.INVALID_INPUT,
            422,
            AuditSeverity.P1,
            OutwardDisposition.INVALID_REQUEST,
        ),
        RawV2Reason.STABLE_VERSION_UNAVAILABLE: _policy(
            ExceptionClass.INVALID_INPUT,
            422,
            AuditSeverity.P1,
            OutwardDisposition.INVALID_REQUEST,
        ),
        RawV2Reason.SELECTOR_INVALID: _policy(
            ExceptionClass.INVALID_INPUT,
            422,
            AuditSeverity.P1,
            OutwardDisposition.INVALID_REQUEST,
        ),
        RawV2Reason.MIGRATION_UNCLASSIFIED: _policy(
            ExceptionClass.INVALID_INPUT,
            422,
            AuditSeverity.P1,
            OutwardDisposition.INVALID_REQUEST,
        ),
        RawV2Reason.BINDING_NOT_FOUND: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P2,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.RAW_NOT_FOUND: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P1,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.RAW_QUARANTINED: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.RAW_TOMBSTONED: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.DERIVATION_INVALIDATED: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P1,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.REFERENCE_ONLY_UNAVAILABLE: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P1,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.VISIBILITY_MISMATCH: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.PROJECT_MISMATCH: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.ACL_DENIED: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.BLOB_UNAVAILABLE: _policy(
            ExceptionClass.UNAVAILABLE,
            503,
            AuditSeverity.P1,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.STORAGE_UNAVAILABLE: _policy(
            ExceptionClass.UNAVAILABLE,
            503,
            AuditSeverity.P1,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.PARSE_ARTIFACT_MISSING: _policy(
            ExceptionClass.UNAVAILABLE,
            404,
            AuditSeverity.P1,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.UNSAFE_CONTENT: _policy(
            ExceptionClass.FORBIDDEN,
            404,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.UNSUPPORTED_MEDIA: _policy(
            ExceptionClass.UNAVAILABLE,
            415,
            AuditSeverity.P1,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.BINDING_DIGEST_MISMATCH: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.BLOB_CORRUPT: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.STORAGE_OUTSIDE_ROOT: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.STORAGE_SYMLINK: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.STORAGE_NOT_REGULAR: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.BYTE_LENGTH_MISMATCH: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.RAW_DIGEST_MISMATCH: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.SELECTOR_OUT_OF_BOUNDS: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.PARSE_ARTIFACT_MISMATCH: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.SELECTED_DIGEST_MISMATCH: _policy(
            ExceptionClass.INTEGRITY,
            503,
            AuditSeverity.P0,
            OutwardDisposition.EVIDENCE_UNAVAILABLE,
        ),
        RawV2Reason.MIGRATION_NOT_AUTHORIZED: _policy(
            ExceptionClass.FORBIDDEN,
            403,
            AuditSeverity.P1,
            OutwardDisposition.FORBIDDEN,
        ),
        RawV2Reason.MIGRATION_COUNT_MISMATCH: _policy(
            ExceptionClass.INTEGRITY,
            500,
            AuditSeverity.P0,
            OutwardDisposition.INTERNAL_INTEGRITY_FAILURE,
        ),
    }
)

SIZE_LIMITS: Mapping[SizeLimit, int] = MappingProxyType(
    {
        SizeLimit.PORTABLE_ID: 240,
        SizeLimit.SOURCE_OBJECT_ID: 2_000,
        SizeLimit.MEDIA_TYPE: 200,
        SizeLimit.TRACE_MUTATION_ID: 240,
        SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA: 160,
        SizeLimit.CANONICAL_JSON: 65_536,
        SizeLimit.SELECTED_OUTPUT: 1_048_576,
    }
)

SOURCE_TYPE_TO_DOMAIN: Mapping[str, SourceDomain] = MappingProxyType(
    {
        "git": SourceDomain.CODE,
        "codex": SourceDomain.CODEX,
        "mlflow": SourceDomain.EXPERIMENT,
        "notebook": SourceDomain.NOTEBOOK,
        "document": SourceDomain.DOCUMENT,
        "workspace": SourceDomain.WORKSPACE,
    }
)

AUDIT_SEVERITIES = frozenset(AuditSeverity)
OUTWARD_DISPOSITIONS = frozenset(OutwardDisposition)
EXCEPTION_CLASSES = frozenset(ExceptionClass)


class RawV2ContractError(ValueError):
    """A typed, policy-bound raw V2 contract failure."""

    def __init__(self, reason: RawV2Reason, detail: str | None = None) -> None:
        if type(reason) is not RawV2Reason:
            raise TypeError("reason must be a frozen RawV2Reason")
        self.reason = reason
        self.policy = REASON_POLICY[reason]
        self.detail = detail
        super().__init__(reason.value if detail is None else f"{reason.value}: {detail}")


def _fail(reason: RawV2Reason, detail: str) -> None:
    raise RawV2ContractError(reason, detail)


def _normalize_string(value: str, *, label: str) -> str:
    try:
        normalized = unicodedata.normalize("NFC", value)
        normalized.encode("utf-8")
    except UnicodeError:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} is not valid UTF-8 text")
    return normalized


def _canonical_value(value: object, *, allow_none: bool, label: str) -> object:
    value_type = type(value)
    if value_type is type(None):
        if not allow_none:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} does not permit null")
        return None
    if value_type is bool or value_type is int:
        return value
    if value_type is str:
        return _normalize_string(cast(str, value), label=label)
    if value_type is list:
        return [
            _canonical_value(item, allow_none=allow_none, label=f"{label}[{index}]")
            for index, item in enumerate(cast(list[object], value))
        ]
    if value_type is dict:
        normalized: dict[str, object] = {}
        for raw_key, item in cast(dict[object, object], value).items():
            if type(raw_key) is not str:
                _fail(
                    RawV2Reason.CANONICALIZATION_MISMATCH,
                    f"{label} contains a non-string object key",
                )
            key = _normalize_string(raw_key, label=f"{label} key")
            if key in normalized:
                _fail(
                    RawV2Reason.CANONICAL_DUPLICATE_KEY,
                    f"{label} contains NFC-colliding keys",
                )
            normalized[key] = _canonical_value(
                item,
                allow_none=allow_none,
                label=f"{label}.{key}",
            )
        return normalized
    _fail(
        RawV2Reason.CANONICALIZATION_MISMATCH,
        f"{label} contains unsupported exact runtime type {value_type.__name__}",
    )


def canonical_json_bytes(value: object, *, allow_none: bool = False) -> bytes:
    """Project exact built-in JSON values to NFC canonical UTF-8 bytes."""

    projected = _canonical_value(value, allow_none=allow_none, label="canonical value")
    try:
        return json.dumps(
            projected,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise RawV2ContractError(
            RawV2Reason.CANONICALIZATION_MISMATCH,
            "canonical serialization failed",
        ) from error


def _reject_float(value: str) -> None:
    _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"binary float is forbidden: {value}")


def _reject_constant(value: str) -> None:
    _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"non-finite value is forbidden: {value}")


def _strict_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    normalized_keys: set[str] = set()
    for key, value in pairs:
        if key in result:
            _fail(RawV2Reason.CANONICAL_DUPLICATE_KEY, "JSON contains a duplicate raw key")
        normalized = _normalize_string(key, label="JSON object key")
        if normalized in normalized_keys:
            _fail(RawV2Reason.CANONICAL_DUPLICATE_KEY, "JSON contains NFC-colliding keys")
        normalized_keys.add(normalized)
        result[key] = value
    return result


def strict_json_loads(raw: bytes | str, *, allow_none: bool = False) -> object:
    """Strictly decode UTF-8 JSON, rejecting duplicate keys and binary numbers."""

    if type(raw) is bytes:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RawV2ContractError(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "JSON is not valid UTF-8",
            ) from error
    elif type(raw) is str:
        text = raw
    else:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "JSON input must be exact bytes or str")
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_strict_object_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except RawV2ContractError:
        raise
    except (json.JSONDecodeError, RecursionError, UnicodeError) as error:
        raise RawV2ContractError(
            RawV2Reason.CANONICALIZATION_MISMATCH,
            "JSON syntax is invalid",
        ) from error
    return _canonical_value(decoded, allow_none=allow_none, label="JSON value")


def verify_canonical_json_bytes(raw: bytes | str, *, allow_none: bool = False) -> object:
    """Decode and require byte-exact canonical representation."""

    value = strict_json_loads(raw, allow_none=allow_none)
    try:
        encoded = raw if type(raw) is bytes else raw.encode("utf-8")
    except UnicodeEncodeError as error:
        raise RawV2ContractError(
            RawV2Reason.CANONICALIZATION_MISMATCH,
            "JSON text is not valid UTF-8",
        ) from error
    if canonical_json_bytes(value, allow_none=allow_none) != encoded:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "JSON bytes are not canonical")
    return value


def canonical_timestamp(value: str) -> str:
    """Normalize strict aware RFC3339 text to UTC microsecond-Z form."""

    if type(value) is not str:
        _fail(RawV2Reason.TIMESTAMP_INVALID, "timestamp is not strict aware RFC3339 text")
    matched = _RFC3339_RE.fullmatch(value)
    if matched is None:
        _fail(RawV2Reason.TIMESTAMP_INVALID, "timestamp is not strict aware RFC3339 text")
    zone = matched.group("zone")
    if zone != "Z":
        offset_hours = int(zone[1:3])
        offset_minutes = int(zone[4:6])
        if offset_hours > 23 or offset_minutes > 59 or zone == "-00:00":
            _fail(RawV2Reason.TIMESTAMP_INVALID, "timestamp offset is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            _fail(RawV2Reason.TIMESTAMP_INVALID, "timestamp is timezone-naive")
        utc_value = parsed.astimezone(UTC)
        return utc_value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except RawV2ContractError:
        raise
    except (OverflowError, ValueError) as error:
        raise RawV2ContractError(
            RawV2Reason.TIMESTAMP_INVALID,
            "timestamp cannot be represented losslessly",
        ) from error


def verify_canonical_timestamp(value: str) -> str:
    canonical = canonical_timestamp(value)
    if canonical != value:
        _fail(RawV2Reason.TIMESTAMP_INVALID, "timestamp is valid but not canonical")
    return canonical


def exact_bytes_sha256(value: bytes) -> str:
    if type(value) is not bytes:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "payload must be exact bytes")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def H_HEX(domain: DigestDomain, value: object, *, allow_none: bool = False) -> str:
    if type(domain) is not DigestDomain:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "digest domain is not frozen")
    preimage = (
        domain.value.encode("ascii")
        + b"\x00"
        + canonical_json_bytes(value, allow_none=allow_none)
    )
    return hashlib.sha256(preimage).hexdigest()


def H(domain: DigestDomain, value: object, *, allow_none: bool = False) -> str:
    return "sha256:" + H_HEX(domain, value, allow_none=allow_none)


def validate_sha256_digest(value: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "digest is not lowercase sha256")
    return value


def _validate_typed_id(value: str, *, pattern: re.Pattern[str], label: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} is not canonical")
    return value


def validate_raw_object_id(value: str) -> str:
    return _validate_typed_id(value, pattern=_RAW_OBJECT_ID_RE, label="raw object ID")


def validate_source_event_id(value: str) -> str:
    return _validate_typed_id(value, pattern=_SOURCE_EVENT_ID_RE, label="source event ID")


def validate_locator_id(value: str) -> str:
    return _validate_typed_id(value, pattern=_LOCATOR_ID_RE, label="raw locator ID")


def ensure_utf8_size(value: str | bytes, limit: SizeLimit, *, label: str = "value") -> None:
    if type(limit) is not SizeLimit:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "size limit is not frozen")
    if type(value) is str:
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise RawV2ContractError(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                f"{label} is not valid UTF-8 text",
            ) from error
    elif type(value) is bytes:
        raw = value
    else:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} must be exact str or bytes")
    if len(raw) > SIZE_LIMITS[limit]:
        _fail(
            RawV2Reason.CONTRACT_SIZE_EXCEEDED,
            f"{label} exceeds {SIZE_LIMITS[limit]} UTF-8 bytes",
        )


def source_domain_for_type(source_type: str) -> SourceDomain:
    if type(source_type) is not str or source_type not in SOURCE_TYPE_TO_DOMAIN:
        _fail(RawV2Reason.SOURCE_ADAPTER_UNREGISTERED, "source type has no reviewed mapping")
    return SOURCE_TYPE_TO_DOMAIN[source_type]


def _authority_text(value: str, *, limit: SizeLimit, label: str) -> str:
    normalized = _normalize_string(value, label=label)
    if not normalized:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} must be non-empty")
    if any(ord(character) < 32 for character in normalized):
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} contains a C0 control")
    ensure_utf8_size(normalized, limit, label=label)
    return normalized


def normalize_portable_id(value: str, *, label: str = "portable identifier") -> str:
    """Normalize and validate one exact raw V2 opaque identifier."""

    if type(value) is not str:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} must be exact str")
    return _authority_text(value, limit=SizeLimit.PORTABLE_ID, label=label)


def _portable_id(value: str) -> str:
    return normalize_portable_id(value)


def _source_object_id(value: str) -> str:
    return _authority_text(value, limit=SizeLimit.SOURCE_OBJECT_ID, label="source object ID")


def _version_text(value: str) -> str:
    return _authority_text(
        value,
        limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
        label="versioned authority text",
    )


def _mutation_id(value: str) -> str:
    return _authority_text(value, limit=SizeLimit.TRACE_MUTATION_ID, label="mutation ID")


PortableId = Annotated[StrictStr, AfterValidator(_portable_id)]
SourceObjectId = Annotated[StrictStr, AfterValidator(_source_object_id)]
VersionText = Annotated[StrictStr, AfterValidator(_version_text)]
MutationId = Annotated[StrictStr, AfterValidator(_mutation_id)]
Digest = Annotated[StrictStr, AfterValidator(validate_sha256_digest)]
RawObjectId = Annotated[StrictStr, AfterValidator(validate_raw_object_id)]
CanonicalTime = Annotated[StrictStr, AfterValidator(canonical_timestamp)]


class _FrozenPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def canonical_value(self) -> dict[str, object]:
        projected = self.model_dump(mode="json", exclude_none=False)
        value = _canonical_value(projected, allow_none=True, label=type(self).__name__)
        if type(value) is not dict:
            raise AssertionError("payload projection must be an object")
        return cast(dict[str, object], value)

    def canonical_json_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_value(), allow_none=True)

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


class VisibilityPartitionPayload(_FrozenPayload):
    project_id: PortableId
    acl_ref: PortableId


def derive_visibility_partition_sha256(payload: VisibilityPartitionPayload) -> str:
    if type(payload) is not VisibilityPartitionPayload:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "visibility payload is not frozen")
    return H(DigestDomain.VISIBILITY_PARTITION, [payload.project_id, payload.acl_ref])


class RawLogicalIdentityPayload(_FrozenPayload):
    project_id: PortableId
    source_domain: SourceDomain
    source_type: VersionText
    source_instance_id: PortableId
    source_object_id: SourceObjectId
    source_version: VersionText
    stable_version: VersionText
    raw_content_sha256: Digest | None
    acl_ref: PortableId
    visibility_partition_sha256: Digest

    @model_validator(mode="after")
    def _identity_consistency(self) -> RawLogicalIdentityPayload:
        if source_domain_for_type(self.source_type) is not self.source_domain:
            raise ValueError("source domain does not match the reviewed source mapping")
        expected = derive_visibility_partition_sha256(
            VisibilityPartitionPayload(project_id=self.project_id, acl_ref=self.acl_ref)
        )
        if self.visibility_partition_sha256 != expected:
            raise ValueError(RawV2Reason.VISIBILITY_MISMATCH.value)
        return self


class SourceEventIdempotencyPayload(_FrozenPayload):
    project_id: PortableId
    source_domain: SourceDomain
    source_type: VersionText
    source_instance_id: PortableId
    source_object_id: SourceObjectId
    source_version: VersionText
    event_type: VersionText
    raw_content_sha256: Digest | None
    acl_ref: PortableId
    visibility_partition_sha256: Digest
    mutation_id: MutationId

    @model_validator(mode="after")
    def _event_consistency(self) -> SourceEventIdempotencyPayload:
        if source_domain_for_type(self.source_type) is not self.source_domain:
            raise ValueError("source domain does not match the reviewed source mapping")
        expected = derive_visibility_partition_sha256(
            VisibilityPartitionPayload(project_id=self.project_id, acl_ref=self.acl_ref)
        )
        if self.visibility_partition_sha256 != expected:
            raise ValueError(RawV2Reason.VISIBILITY_MISMATCH.value)
        return self


class RawSelectorPayload(_FrozenPayload):
    selector_kind: VersionText
    selector: dict[str, Any]

    @field_validator("selector", mode="before")
    @classmethod
    def _selector_contract(cls, value: object) -> dict[str, object]:
        projected = _canonical_value(value, allow_none=True, label="selector")
        if type(projected) is not dict:
            _fail(RawV2Reason.SELECTOR_INVALID, "selector must be an exact JSON object")
        raw = canonical_json_bytes(projected, allow_none=True)
        ensure_utf8_size(raw, SizeLimit.CANONICAL_JSON, label="selector")
        return cast(dict[str, object], projected)


class RawEvidenceBindingPayload(_FrozenPayload):
    project_id: PortableId
    source_domain: SourceDomain
    raw_object_id: RawObjectId
    source_version: VersionText
    stable_version: VersionText
    generation_id: PortableId
    derived_entity_id: PortableId
    retrieval_unit_id: PortableId
    derivation_kind: VersionText
    derivation_version: VersionText
    selector_sha256: Digest
    raw_content_sha256: Digest
    selected_content_sha256: Digest
    parser_artifact_sha256: Digest | None
    valid_from: CanonicalTime | None
    valid_to: CanonicalTime | None
    observed_at: CanonicalTime
    acl_ref: PortableId
    visibility_partition_sha256: Digest
    adapter_version: VersionText
    schema_version: VersionText

    @model_validator(mode="after")
    def _binding_consistency(self) -> RawEvidenceBindingPayload:
        expected = derive_visibility_partition_sha256(
            VisibilityPartitionPayload(project_id=self.project_id, acl_ref=self.acl_ref)
        )
        if self.visibility_partition_sha256 != expected:
            raise ValueError(RawV2Reason.VISIBILITY_MISMATCH.value)
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_from >= self.valid_to
        ):
            raise ValueError("valid_from must precede valid_to")
        return self


def derive_raw_object_id(payload: RawLogicalIdentityPayload) -> str:
    if type(payload) is not RawLogicalIdentityPayload:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "logical identity payload is not frozen")
    return "raw-v2:" + H_HEX(
        DigestDomain.RAW_LOGICAL_IDENTITY,
        payload.canonical_value(),
        allow_none=True,
    )


def derive_source_event_id(payload: SourceEventIdempotencyPayload) -> str:
    if type(payload) is not SourceEventIdempotencyPayload:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "event identity payload is not frozen")
    return "source-event-v2:" + H_HEX(
        DigestDomain.SOURCE_EVENT_IDEMPOTENCY,
        payload.canonical_value(),
        allow_none=True,
    )


def derive_selector_sha256(payload: RawSelectorPayload) -> str:
    if type(payload) is not RawSelectorPayload:
        _fail(RawV2Reason.SELECTOR_INVALID, "selector payload is not frozen")
    return H(DigestDomain.RAW_SELECTOR, payload.canonical_value(), allow_none=True)


def derive_locator_id(payload: RawEvidenceBindingPayload) -> str:
    if type(payload) is not RawEvidenceBindingPayload:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "binding payload is not frozen")
    return "raw-locator-v2:" + H_HEX(
        DigestDomain.RAW_EVIDENCE_BINDING,
        payload.canonical_value(),
        allow_none=True,
    )


__all__ = [
    "AUDIT_SEVERITIES",
    "CANONICAL_JSON_VERSION",
    "EXCEPTION_CLASSES",
    "OUTWARD_DISPOSITIONS",
    "RAW_V2_CONTRACT_VERSION",
    "RAW_V2_VECTOR_SCHEMA_VERSION",
    "REASON_POLICY",
    "SIZE_LIMITS",
    "SOURCE_TYPE_TO_DOMAIN",
    "AuditSeverity",
    "DigestDomain",
    "ExceptionClass",
    "OutwardDisposition",
    "PortableId",
    "RawEvidenceBindingPayload",
    "RawLogicalIdentityPayload",
    "RawSelectorPayload",
    "RawV2ContractError",
    "RawV2Reason",
    "ReasonPolicy",
    "SizeLimit",
    "SourceDomain",
    "SourceEventIdempotencyPayload",
    "VisibilityPartitionPayload",
    "H",
    "H_HEX",
    "canonical_json_bytes",
    "canonical_timestamp",
    "derive_locator_id",
    "derive_raw_object_id",
    "derive_selector_sha256",
    "derive_source_event_id",
    "derive_visibility_partition_sha256",
    "ensure_utf8_size",
    "exact_bytes_sha256",
    "normalize_portable_id",
    "source_domain_for_type",
    "strict_json_loads",
    "validate_locator_id",
    "validate_raw_object_id",
    "validate_sha256_digest",
    "validate_source_event_id",
    "verify_canonical_json_bytes",
    "verify_canonical_timestamp",
]
