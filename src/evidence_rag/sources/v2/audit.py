"""Immutable privacy-safe audit records for selected raw V2 reads."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from .contracts import (
    AuditSeverity,
    OutwardDisposition,
    RawV2ContractError,
    RawV2Reason,
    SourceDomain,
    canonical_json_bytes,
    exact_bytes_sha256,
    normalize_portable_id,
    validate_sha256_digest,
    verify_canonical_timestamp,
)

RAW_V2_READ_AUDIT_VERSION = "raw-v2-read-audit-v1"
RAW_V2_AUDIT_HMAC_MINIMUM_KEY_BYTES = 32
_TRACE_TOKEN_DOMAIN = b"raw-v2-read-audit-trace-token-v1"
_REQUEST_SCOPE_TOKEN_DOMAIN = b"raw-v2-read-audit-request-scope-token-v1"


class RawV2ReadOutcome(StrEnum):
    OBSERVED = "OBSERVED"
    REJECTED = "REJECTED"


def _canonical_authority_text(value: str, *, label: str) -> str:
    canonical = normalize_portable_id(value, label=label)
    if canonical != value:
        raise RawV2ContractError(
            RawV2Reason.CANONICALIZATION_MISMATCH,
            f"{label} must already be canonical",
        )
    return canonical


def _hmac_sha256(*, key: bytes, domain: bytes, payload: bytes) -> str:
    if type(payload) is not bytes:
        raise TypeError("audit token payload must be exact bytes")
    digest = hmac.new(key, domain + b"\x00" + payload, hashlib.sha256).hexdigest()
    return "sha256:" + digest


@dataclass(frozen=True, slots=True)
class RawV2ReadAuditRecord:
    schema_version: str
    audit_sha256: str
    occurred_at: str
    outcome: RawV2ReadOutcome
    purpose: str
    trace_token_sha256: str
    request_scope_token_sha256: str
    public_status_code: int
    public_disposition: OutwardDisposition | None
    source_domain: SourceDomain
    reason: RawV2Reason | None
    audit_severity: AuditSeverity | None
    selected_receipt_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != RAW_V2_READ_AUDIT_VERSION:
            raise ValueError("audit schema version is not frozen")
        validate_sha256_digest(self.audit_sha256)
        verify_canonical_timestamp(self.occurred_at)
        if type(self.outcome) is not RawV2ReadOutcome:
            raise TypeError("audit outcome must be a frozen RawV2ReadOutcome")
        _canonical_authority_text(self.purpose, label="read purpose")
        validate_sha256_digest(self.trace_token_sha256)
        validate_sha256_digest(self.request_scope_token_sha256)
        if type(self.public_status_code) is not int or not 100 <= self.public_status_code <= 599:
            raise TypeError("audit public status must be an exact HTTP integer")
        if type(self.source_domain) is not SourceDomain:
            raise TypeError("audit source domain must be frozen")

        if self.outcome is RawV2ReadOutcome.OBSERVED:
            if self.public_status_code != 200 or self.public_disposition is not None:
                raise ValueError("observed audit requires exact success projection")
            if self.reason is not None or self.audit_severity is not None:
                raise ValueError("observed audit cannot carry rejection fields")
            if self.selected_receipt_sha256 is None:
                raise ValueError("observed audit requires a selected receipt")
            validate_sha256_digest(self.selected_receipt_sha256)
        else:
            if type(self.reason) is not RawV2Reason:
                raise TypeError("rejected audit requires a frozen reason")
            if type(self.audit_severity) is not AuditSeverity:
                raise TypeError("rejected audit requires a frozen severity")
            if type(self.public_disposition) is not OutwardDisposition:
                raise TypeError("rejected audit requires a frozen public disposition")
            if self.selected_receipt_sha256 is not None:
                raise ValueError("rejected audit cannot claim selected evidence")
            policy = RawV2ContractError(self.reason).policy
            if (
                self.public_status_code != policy.http_status
                or self.public_disposition is not policy.outward_disposition
                or self.audit_severity is not policy.audit_severity
            ):
                raise ValueError("rejected audit differs from the frozen reason policy")
        self.verify()

    def _unsigned_value(self) -> dict[str, object]:
        return {
            "audit_severity": (None if self.audit_severity is None else self.audit_severity.value),
            "occurred_at": self.occurred_at,
            "outcome": self.outcome.value,
            "public_disposition": (
                None if self.public_disposition is None else self.public_disposition.value
            ),
            "public_status_code": self.public_status_code,
            "purpose": self.purpose,
            "reason": None if self.reason is None else self.reason.value,
            "request_scope_token_sha256": self.request_scope_token_sha256,
            "schema_version": self.schema_version,
            "selected_receipt_sha256": self.selected_receipt_sha256,
            "source_domain": self.source_domain.value,
            "trace_token_sha256": self.trace_token_sha256,
        }

    def canonical_value(self) -> dict[str, object]:
        return {"audit_sha256": self.audit_sha256, **self._unsigned_value()}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_value(), allow_none=True)

    def verify(self) -> RawV2ReadAuditRecord:
        expected = exact_bytes_sha256(canonical_json_bytes(self._unsigned_value(), allow_none=True))
        if self.audit_sha256 != expected:
            raise ValueError("audit self-digest mismatch")
        return self


class RawV2ReadAuditor:
    """Trusted keyed constructor; raw request values never enter the record."""

    def __init__(self, *, hmac_key: bytes, clock: Callable[[], str]) -> None:
        if type(hmac_key) is not bytes or len(hmac_key) < RAW_V2_AUDIT_HMAC_MINIMUM_KEY_BYTES:
            raise TypeError("audit HMAC key must be exact bytes with at least 32 bytes")
        if not callable(clock):
            raise TypeError("audit clock must be callable")
        self._key = hmac_key
        self._clock = clock

    def observed(
        self,
        *,
        purpose: str,
        trace_id: str,
        request_scope_bytes: bytes,
        source_domain: SourceDomain,
        selected_receipt_sha256: str,
    ) -> RawV2ReadAuditRecord:
        return self._record(
            purpose=purpose,
            trace_id=trace_id,
            request_scope_bytes=request_scope_bytes,
            source_domain=source_domain,
            outcome=RawV2ReadOutcome.OBSERVED,
            public_status_code=200,
            public_disposition=None,
            reason=None,
            audit_severity=None,
            selected_receipt_sha256=selected_receipt_sha256,
        )

    def rejected(
        self,
        *,
        purpose: str,
        trace_id: str,
        request_scope_bytes: bytes,
        source_domain: SourceDomain,
        error: RawV2ContractError,
    ) -> RawV2ReadAuditRecord:
        if type(error) is not RawV2ContractError:
            raise TypeError("audit rejection requires an exact RawV2ContractError")
        return self._record(
            purpose=purpose,
            trace_id=trace_id,
            request_scope_bytes=request_scope_bytes,
            source_domain=source_domain,
            outcome=RawV2ReadOutcome.REJECTED,
            public_status_code=error.policy.http_status,
            public_disposition=error.policy.outward_disposition,
            reason=error.reason,
            audit_severity=error.policy.audit_severity,
            selected_receipt_sha256=None,
        )

    def _record(
        self,
        *,
        purpose: str,
        trace_id: str,
        request_scope_bytes: bytes,
        source_domain: SourceDomain,
        outcome: RawV2ReadOutcome,
        public_status_code: int,
        public_disposition: OutwardDisposition | None,
        reason: RawV2Reason | None,
        audit_severity: AuditSeverity | None,
        selected_receipt_sha256: str | None,
    ) -> RawV2ReadAuditRecord:
        canonical_purpose = _canonical_authority_text(purpose, label="read purpose")
        canonical_trace = _canonical_authority_text(trace_id, label="read trace ID")
        if type(request_scope_bytes) is not bytes:
            raise TypeError("request scope must be exact canonical bytes")
        if type(source_domain) is not SourceDomain:
            raise TypeError("source domain must be frozen")
        occurred_at = verify_canonical_timestamp(self._clock())
        trace_token = _hmac_sha256(
            key=self._key,
            domain=_TRACE_TOKEN_DOMAIN,
            payload=canonical_trace.encode("utf-8"),
        )
        scope_token = _hmac_sha256(
            key=self._key,
            domain=_REQUEST_SCOPE_TOKEN_DOMAIN,
            payload=request_scope_bytes,
        )
        unsigned = {
            "audit_severity": None if audit_severity is None else audit_severity.value,
            "occurred_at": occurred_at,
            "outcome": outcome.value,
            "public_disposition": (
                None if public_disposition is None else public_disposition.value
            ),
            "public_status_code": public_status_code,
            "purpose": canonical_purpose,
            "reason": None if reason is None else reason.value,
            "request_scope_token_sha256": scope_token,
            "schema_version": RAW_V2_READ_AUDIT_VERSION,
            "selected_receipt_sha256": selected_receipt_sha256,
            "source_domain": source_domain.value,
            "trace_token_sha256": trace_token,
        }
        return RawV2ReadAuditRecord(
            schema_version=RAW_V2_READ_AUDIT_VERSION,
            audit_sha256=exact_bytes_sha256(canonical_json_bytes(unsigned, allow_none=True)),
            occurred_at=occurred_at,
            outcome=outcome,
            purpose=canonical_purpose,
            trace_token_sha256=trace_token,
            request_scope_token_sha256=scope_token,
            public_status_code=public_status_code,
            public_disposition=public_disposition,
            source_domain=source_domain,
            reason=reason,
            audit_severity=audit_severity,
            selected_receipt_sha256=selected_receipt_sha256,
        )


__all__ = [
    "RAW_V2_AUDIT_HMAC_MINIMUM_KEY_BYTES",
    "RAW_V2_READ_AUDIT_VERSION",
    "RawV2ReadAuditRecord",
    "RawV2ReadAuditor",
    "RawV2ReadOutcome",
]
