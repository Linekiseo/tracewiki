"""Transport-neutral selected raw V2 read projection and privacy policy."""

from __future__ import annotations

import base64
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from .audit import RawV2ReadAuditor, RawV2ReadAuditRecord, RawV2ReadOutcome
from .contracts import (
    SIZE_LIMITS,
    RawV2ContractError,
    RawV2Reason,
    SizeLimit,
    SourceDomain,
    canonical_json_bytes,
    ensure_utf8_size,
    exact_bytes_sha256,
    validate_locator_id,
    validate_sha256_digest,
)
from .models import RawV2PublicError, RawV2SelectedReadPublicResponse
from .reader import RawManagedReadRequest, RawV2ManagedReader
from .router import RawV2PublicProjectionRouter, RawV2PublicResult
from .selector import RawSelectedReadResult, RawV2SelectorExecutor

RAW_V2_READ_SERVICE_VERSION = "raw-v2-selected-read-service-v1"


def _fail(reason: RawV2Reason, detail: str) -> None:
    raise RawV2ContractError(reason, detail)


def _canonical_text(value: str, *, limit: SizeLimit, label: str) -> str:
    if type(value) is not str:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} must be exact str")
    try:
        normalized = unicodedata.normalize("NFC", value)
        normalized.encode("utf-8")
    except UnicodeError as error:
        raise RawV2ContractError(
            RawV2Reason.CANONICALIZATION_MISMATCH,
            f"{label} must be valid UTF-8 text",
        ) from error
    if normalized != value or not value:
        _fail(
            RawV2Reason.CANONICALIZATION_MISMATCH,
            f"{label} must be non-empty canonical NFC text",
        )
    if any(ord(character) < 32 for character in value):
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} contains a C0 control")
    ensure_utf8_size(value, limit, label=label)
    return value


@dataclass(frozen=True, slots=True)
class RawEvidenceReadRequestV2:
    project_id: str
    principal_id: str
    locator_id: str
    expected_binding_sha256: str
    expected_source_domain: SourceDomain
    expected_source_type: str
    expected_source_instance_id: str
    expected_source_object_id: str
    expected_source_version: str
    expected_stable_version: str
    expected_generation_id: str
    purpose: str
    trace_id: str

    def __post_init__(self) -> None:
        _canonical_text(self.project_id, limit=SizeLimit.PORTABLE_ID, label="project ID")
        _canonical_text(self.principal_id, limit=SizeLimit.PORTABLE_ID, label="principal ID")
        validate_locator_id(self.locator_id)
        validate_sha256_digest(self.expected_binding_sha256)
        if type(self.expected_source_domain) is not SourceDomain:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "expected source domain is not frozen")
        _canonical_text(
            self.expected_source_type,
            limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
            label="expected source type",
        )
        _canonical_text(
            self.expected_source_instance_id,
            limit=SizeLimit.PORTABLE_ID,
            label="expected source instance ID",
        )
        _canonical_text(
            self.expected_source_object_id,
            limit=SizeLimit.SOURCE_OBJECT_ID,
            label="expected source object ID",
        )
        for value, label in (
            (self.expected_source_version, "expected source version"),
            (self.expected_stable_version, "expected stable version"),
        ):
            _canonical_text(
                value,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label=label,
            )
        _canonical_text(
            self.expected_generation_id,
            limit=SizeLimit.PORTABLE_ID,
            label="expected generation ID",
        )
        _canonical_text(self.purpose, limit=SizeLimit.PORTABLE_ID, label="read purpose")
        _canonical_text(self.trace_id, limit=SizeLimit.TRACE_MUTATION_ID, label="read trace ID")

    def _managed_request(self) -> RawManagedReadRequest:
        return RawManagedReadRequest(
            project_id=self.project_id,
            principal_id=self.principal_id,
            locator_id=self.locator_id,
            expected_binding_sha256=self.expected_binding_sha256,
            expected_source_domain=self.expected_source_domain,
            expected_source_type=self.expected_source_type,
            expected_source_instance_id=self.expected_source_instance_id,
            expected_source_object_id=self.expected_source_object_id,
            expected_source_version=self.expected_source_version,
            expected_stable_version=self.expected_stable_version,
            expected_generation_id=self.expected_generation_id,
        )

    def _audit_scope_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "expected_binding_sha256": self.expected_binding_sha256,
                "expected_generation_id": self.expected_generation_id,
                "expected_source_domain": self.expected_source_domain.value,
                "expected_source_instance_id": self.expected_source_instance_id,
                "expected_source_object_id": self.expected_source_object_id,
                "expected_source_type": self.expected_source_type,
                "expected_source_version": self.expected_source_version,
                "expected_stable_version": self.expected_stable_version,
                "locator_id": self.locator_id,
                "principal_id": self.principal_id,
                "project_id": self.project_id,
            }
        )


@dataclass(frozen=True, slots=True)
class RawV2ReadOutputPolicy:
    allowed_purposes: tuple[str, ...]
    max_public_bytes: int
    forbidden_byte_markers: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if type(self.allowed_purposes) is not tuple or not self.allowed_purposes:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "allowed purposes must be an exact non-empty tuple",
            )
        for purpose in self.allowed_purposes:
            _canonical_text(purpose, limit=SizeLimit.PORTABLE_ID, label="allowed purpose")
        if self.allowed_purposes != tuple(sorted(set(self.allowed_purposes))):
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "allowed purposes must be sorted and unique",
            )
        if (
            type(self.max_public_bytes) is not int
            or self.max_public_bytes <= 0
            or self.max_public_bytes > SIZE_LIMITS[SizeLimit.SELECTED_OUTPUT]
        ):
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "public byte ceiling must be an exact positive integer within the global limit",
            )
        if type(self.forbidden_byte_markers) is not tuple or not self.forbidden_byte_markers:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "forbidden byte markers must be an exact non-empty tuple",
            )
        if any(type(marker) is not bytes or not marker for marker in self.forbidden_byte_markers):
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "forbidden byte markers must contain exact non-empty bytes",
            )
        if self.forbidden_byte_markers != tuple(sorted(set(self.forbidden_byte_markers))):
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "forbidden byte markers must be sorted and unique",
            )

    def _authorize_purpose(self, purpose: str) -> None:
        if purpose not in self.allowed_purposes:
            _fail(RawV2Reason.ACL_DENIED, "read purpose is outside trusted policy")

    def _verify_selected(self, selected: RawSelectedReadResult) -> None:
        if type(selected) is not RawSelectedReadResult:
            raise TypeError("selected result must be exact RawSelectedReadResult")
        if (
            len(selected.selected_bytes) != selected.selected_byte_length
            or exact_bytes_sha256(selected.selected_bytes) != selected.selected_content_sha256
        ):
            _fail(
                RawV2Reason.SELECTED_DIGEST_MISMATCH,
                "selected bytes differ before public policy",
            )
        if selected.selected_byte_length > self.max_public_bytes:
            _fail(
                RawV2Reason.CONTRACT_SIZE_EXCEEDED,
                "selected bytes exceed trusted public-output ceiling",
            )
        if any(marker in selected.selected_bytes for marker in self.forbidden_byte_markers):
            _fail(RawV2Reason.UNSAFE_CONTENT, "selected bytes match trusted unsafe marker")


def _selected_receipt_sha256(selected: RawSelectedReadResult) -> str:
    return exact_bytes_sha256(
        canonical_json_bytes(
            {
                "binding_sha256": selected.binding_sha256,
                "generation_id": selected.generation_id,
                "locator_id": selected.locator_id,
                "media_type": selected.media_type,
                "raw_content_sha256": selected.raw_content_sha256,
                "raw_object_id": selected.raw_object_id,
                "selected_byte_length": selected.selected_byte_length,
                "selected_content_sha256": selected.selected_content_sha256,
                "selector_kind": selected.selector_kind,
                "selector_sha256": selected.selector_sha256,
                "source_domain": selected.source_domain.value,
                "source_type": selected.source_type,
                "source_version": selected.source_version,
                "stable_version": selected.stable_version,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class RawEvidenceReadResultV2:
    selected: RawSelectedReadResult | None
    public: RawV2PublicResult
    audit: RawV2ReadAuditRecord

    def __post_init__(self) -> None:
        if type(self.public) is not RawV2PublicResult:
            raise TypeError("public result must be exact RawV2PublicResult")
        if type(self.audit) is not RawV2ReadAuditRecord:
            raise TypeError("audit must be exact RawV2ReadAuditRecord")
        if self.public.status_code != self.audit.public_status_code:
            raise ValueError("public and audit statuses differ")
        if self.selected is None:
            if type(self.public.body) is not RawV2PublicError:
                raise TypeError("rejected result requires an exact public error")
            if self.audit.outcome is not RawV2ReadOutcome.REJECTED:
                raise ValueError("rejected result requires a rejected audit")
            if self.public.body.code is not self.audit.public_disposition:
                raise ValueError("public and audit dispositions differ")
            return
        if type(self.selected) is not RawSelectedReadResult:
            raise TypeError("selected result must be exact RawSelectedReadResult")
        if type(self.public.body) is not RawV2SelectedReadPublicResponse:
            raise TypeError("observed result requires an exact selected public body")
        if self.audit.outcome is not RawV2ReadOutcome.OBSERVED:
            raise ValueError("observed result requires an observed audit")
        if self.public.body.audit_sha256 != self.audit.audit_sha256:
            raise ValueError("public success is not bound to its audit")
        if self.audit.selected_receipt_sha256 != _selected_receipt_sha256(self.selected):
            raise ValueError("audit selected receipt differs from selected evidence")
        body = self.public.body
        expected_fields = (
            (body.locator_id, self.selected.locator_id),
            (body.binding_sha256, self.selected.binding_sha256),
            (body.raw_object_id, self.selected.raw_object_id),
            (body.source_domain, self.selected.source_domain),
            (body.source_type, self.selected.source_type),
            (body.source_version, self.selected.source_version),
            (body.stable_version, self.selected.stable_version),
            (body.generation_id, self.selected.generation_id),
            (body.media_type, self.selected.media_type),
            (body.raw_content_sha256, self.selected.raw_content_sha256),
            (body.selector_kind, self.selected.selector_kind),
            (body.selector_sha256, self.selected.selector_sha256),
            (body.selected_content_sha256, self.selected.selected_content_sha256),
            (body.selected_byte_length, self.selected.selected_byte_length),
            (
                body.selected_base64,
                base64.b64encode(self.selected.selected_bytes).decode("ascii"),
            ),
        )
        if any(public_value != selected_value for public_value, selected_value in expected_fields):
            raise ValueError("public success differs from selected evidence")


class RawV2SelectedReadService:
    """Join exact managed selection, public policy, audit and projection."""

    def __init__(
        self,
        *,
        reader: RawV2ManagedReader,
        selector_executor: RawV2SelectorExecutor,
        output_policy: RawV2ReadOutputPolicy,
        audit_hmac_key: bytes,
        clock: Callable[[], str],
    ) -> None:
        if type(reader) is not RawV2ManagedReader:
            raise TypeError("reader must be exact RawV2ManagedReader")
        if type(selector_executor) is not RawV2SelectorExecutor:
            raise TypeError("selector executor must be exact RawV2SelectorExecutor")
        if type(output_policy) is not RawV2ReadOutputPolicy:
            raise TypeError("output policy must be exact RawV2ReadOutputPolicy")
        self._reader = reader
        self._selector_executor = selector_executor
        self._output_policy = output_policy
        self._auditor = RawV2ReadAuditor(hmac_key=audit_hmac_key, clock=clock)
        self._router = RawV2PublicProjectionRouter()

    def read_evidence_v2(self, *, request: RawEvidenceReadRequestV2) -> RawEvidenceReadResultV2:
        if type(request) is not RawEvidenceReadRequestV2:
            raise TypeError("request must be exact RawEvidenceReadRequestV2")
        scope_bytes = request._audit_scope_bytes()
        try:
            self._output_policy._authorize_purpose(request.purpose)
            selected = self._reader.read_selected(
                request=request._managed_request(),
                selector_executor=self._selector_executor,
            )
            self._output_policy._verify_selected(selected)
            selected_receipt = _selected_receipt_sha256(selected)
            audit = self._auditor.observed(
                purpose=request.purpose,
                trace_id=request.trace_id,
                request_scope_bytes=scope_bytes,
                source_domain=request.expected_source_domain,
                selected_receipt_sha256=selected_receipt,
            )
            body = self._public_body(selected=selected, audit=audit)
            public = self._router.selected_read(response=body)
            return RawEvidenceReadResultV2(selected=selected, public=public, audit=audit)
        except RawV2ContractError as error:
            public = self._router.error(error)
            audit = self._auditor.rejected(
                purpose=request.purpose,
                trace_id=request.trace_id,
                request_scope_bytes=scope_bytes,
                source_domain=request.expected_source_domain,
                error=error,
            )
            return RawEvidenceReadResultV2(selected=None, public=public, audit=audit)

    @staticmethod
    def _public_body(
        *,
        selected: RawSelectedReadResult,
        audit: RawV2ReadAuditRecord,
    ) -> RawV2SelectedReadPublicResponse:
        return RawV2SelectedReadPublicResponse(
            locator_id=selected.locator_id,
            binding_sha256=selected.binding_sha256,
            raw_object_id=selected.raw_object_id,
            source_domain=selected.source_domain,
            source_type=selected.source_type,
            source_version=selected.source_version,
            stable_version=selected.stable_version,
            generation_id=selected.generation_id,
            media_type=selected.media_type,
            raw_content_sha256=selected.raw_content_sha256,
            selector_kind=selected.selector_kind,
            selector_sha256=selected.selector_sha256,
            selected_content_sha256=selected.selected_content_sha256,
            selected_byte_length=selected.selected_byte_length,
            selected_base64=base64.b64encode(selected.selected_bytes).decode("ascii"),
            evidence_state="OBSERVED",
            audit_sha256=audit.audit_sha256,
        )


__all__ = [
    "RAW_V2_READ_SERVICE_VERSION",
    "RawEvidenceReadRequestV2",
    "RawEvidenceReadResultV2",
    "RawV2ReadOutputPolicy",
    "RawV2SelectedReadService",
]
