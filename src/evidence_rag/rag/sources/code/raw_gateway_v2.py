"""Code unit binding and selected reads through the reviewed raw V2 authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from ....sources.v2.contracts import (
    RawLogicalIdentityPayload,
    RawSelectorPayload,
    RawV2ContractError,
    RawV2Reason,
    SourceDomain,
    derive_raw_object_id,
    derive_selector_sha256,
    exact_bytes_sha256,
    validate_locator_id,
    validate_raw_object_id,
    validate_sha256_digest,
)
from ....sources.v2.policy import RawV2ProjectPolicyAdapter
from ....sources.v2.read_service import (
    RawEvidenceReadRequestV2,
    RawEvidenceReadResultV2,
    RawV2SelectedReadService,
)
from ....sources.v2.store import (
    RawBindingRecord,
    RawBindingWriteRequest,
    RawV2BindingStore,
)
from .unit_builder import (
    CodeSourceLineage,
    CodeUnitContextRef,
    CodeUnitRecord,
    CodeUnitSpan,
)

CODE_RAW_GATEWAY_V2_VERSION = "code-raw-gateway-v2"

_SOURCE_TYPE = "git"
_DERIVATION_KIND = "code_utf8_range"
_ADAPTER_VERSION = "code-git-raw-v2"
_SCHEMA_VERSION = "code-raw-binding-v2"
_REQUIRED_LINEAGE_ATTRIBUTES = (
    "entity_id",
    "generation_id",
    "raw_object_id",
    "snapshot_dirty",
    "snapshot_head_commit",
)


def _fail(reason: RawV2Reason, detail: str) -> NoReturn:
    raise RawV2ContractError(reason, detail)


@dataclass(frozen=True, slots=True)
class CodeRawBindingRequestV2:
    """Exact Code unit and complete file bytes supplied by a trusted caller."""

    unit: CodeUnitRecord
    raw_file_bytes: bytes
    observed_at: str


@dataclass(frozen=True, slots=True)
class CodeRawReadRequestV2:
    """Target Code authority plus the exact unit and binding receipt to read."""

    unit: CodeUnitRecord
    binding: RawBindingRecord
    project_id: str
    repository_id: str
    file_path: str
    ref: str
    blob_hash: str
    generation_id: str
    principal_id: str
    purpose: str
    trace_id: str


@dataclass(frozen=True, slots=True)
class _PreparedCodeUnit:
    raw_object_id: str
    raw_content_sha256: str
    selected_content_sha256: str
    selected_bytes: bytes
    selector: dict[str, object]
    selector_sha256: str


class CodeRawGatewayV2:
    """Bind one Code unit and delegate its selected evidence read to raw V2."""

    def __init__(
        self,
        *,
        policy: RawV2ProjectPolicyAdapter,
        binding_store: RawV2BindingStore,
        read_service: RawV2SelectedReadService,
    ) -> None:
        if type(policy) is not RawV2ProjectPolicyAdapter:
            raise TypeError("policy must be exact RawV2ProjectPolicyAdapter")
        if type(binding_store) is not RawV2BindingStore:
            raise TypeError("binding_store must be exact RawV2BindingStore")
        if type(read_service) is not RawV2SelectedReadService:
            raise TypeError("read_service must be exact RawV2SelectedReadService")
        self._policy = policy
        self._binding_store = binding_store
        self._read_service = read_service

    def bind_unit(self, *, request: CodeRawBindingRequestV2) -> RawBindingRecord:
        """Persist one exact Code UTF-8 range binding for an existing V2 object."""

        if type(request) is not CodeRawBindingRequestV2:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "binding request type is not frozen")
        prepared = self._prepare_unit(
            unit=request.unit,
            raw_file_bytes=request.raw_file_bytes,
        )
        record = self._binding_store.persist(
            request=RawBindingWriteRequest(
                project_id=request.unit.project_id,
                raw_object_id=prepared.raw_object_id,
                source_acl_ref=request.unit.acl_ref,
                source_version=request.unit.ref,
                stable_version=request.unit.source_lineage.blob_hash,
                generation_id=request.unit.generation_id,
                derived_entity_id=request.unit.entity_id,
                retrieval_unit_id=request.unit.unit_id,
                derivation_kind=_DERIVATION_KIND,
                derivation_version=CODE_RAW_GATEWAY_V2_VERSION,
                selector_kind="utf8_range_v2",
                selector=prepared.selector,
                parser_artifact_sha256=None,
                valid_from=None,
                valid_to=None,
                observed_at=request.observed_at,
                adapter_version=_ADAPTER_VERSION,
                schema_version=_SCHEMA_VERSION,
            ),
            selected_content=prepared.selected_bytes,
        )
        self._require_binding_coherence(
            unit=request.unit,
            binding=record,
            prepared=prepared,
        )
        return record

    def read_unit(self, *, request: CodeRawReadRequestV2) -> RawEvidenceReadResultV2:
        """Read one exact target through the shared selected-read service."""

        if type(request) is not CodeRawReadRequestV2:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "read request type is not frozen")
        prepared = self._prepare_unit(unit=request.unit, raw_file_bytes=None)
        self._require_binding_coherence(
            unit=request.unit,
            binding=request.binding,
            prepared=prepared,
        )
        return self._read_service.read_evidence_v2(
            request=RawEvidenceReadRequestV2(
                project_id=request.project_id,
                principal_id=request.principal_id,
                locator_id=request.binding.locator_id,
                expected_binding_sha256=request.binding.binding_sha256,
                expected_source_domain=SourceDomain.CODE,
                expected_source_type=_SOURCE_TYPE,
                expected_source_instance_id=request.repository_id,
                expected_source_object_id=request.file_path,
                expected_source_version=request.ref,
                expected_stable_version=request.blob_hash,
                expected_generation_id=request.generation_id,
                purpose=request.purpose,
                trace_id=request.trace_id,
            )
        )

    def _prepare_unit(
        self,
        *,
        unit: CodeUnitRecord,
        raw_file_bytes: bytes | None,
    ) -> _PreparedCodeUnit:
        if type(unit) is not CodeUnitRecord:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "Code unit type is not frozen")
        if type(unit.source_lineage) is not CodeSourceLineage:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "Code lineage type is not frozen")
        if type(unit.span) is not CodeUnitSpan or type(unit.context_ref) is not CodeUnitContextRef:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "Code locator type is not frozen")
        if (
            unit.context_ref.span != unit.span
            or unit.context_ref.parent_entity_id != unit.entity_id
        ):
            _fail(RawV2Reason.SOURCE_IDENTITY_MISMATCH, "Code context differs from its unit")
        if unit.source_lineage.ref != unit.ref:
            _fail(RawV2Reason.SOURCE_IDENTITY_MISMATCH, "Code lineage ref differs from its unit")

        attributes = dict(unit.source_lineage.attributes)
        if any(name not in attributes for name in _REQUIRED_LINEAGE_ATTRIBUTES):
            _fail(RawV2Reason.SOURCE_IDENTITY_MISMATCH, "Code lineage is incomplete")
        if (
            attributes["entity_id"] != unit.entity_id
            or attributes["generation_id"] != unit.generation_id
            or attributes["snapshot_head_commit"] != unit.ref
            or attributes["snapshot_dirty"] != "false"
        ):
            _fail(RawV2Reason.SOURCE_IDENTITY_MISMATCH, "Code lineage scope is inconsistent")

        raw_object_id = validate_raw_object_id(attributes["raw_object_id"])
        raw_digest = validate_sha256_digest(unit.source_lineage.file_content_hash)
        selector = self._selector(unit.span)
        selected_bytes = self._body_bytes(unit)
        selector_payload = RawSelectorPayload(
            selector_kind="utf8_range_v2",
            selector=selector,
        )
        selector_sha256 = derive_selector_sha256(selector_payload)

        if raw_file_bytes is not None:
            self._verify_file_bytes(
                unit=unit,
                raw_file_bytes=raw_file_bytes,
                raw_digest=raw_digest,
                selected_bytes=selected_bytes,
            )

        visibility = self._policy.resolve_object_visibility(
            project_id=unit.project_id,
            source_acl_ref=unit.acl_ref,
        )
        try:
            identity = RawLogicalIdentityPayload(
                project_id=unit.project_id,
                source_domain=SourceDomain.CODE,
                source_type=_SOURCE_TYPE,
                source_instance_id=unit.repository_id,
                source_object_id=unit.file_path,
                source_version=unit.ref,
                stable_version=unit.source_lineage.blob_hash,
                raw_content_sha256=raw_digest,
                acl_ref=visibility.acl_ref,
                visibility_partition_sha256=visibility.visibility_partition_sha256,
            )
        except RawV2ContractError:
            raise
        except (TypeError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "Code raw logical identity is not canonical",
            ) from error
        if derive_raw_object_id(identity) != raw_object_id:
            _fail(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "Code lineage raw object ID differs from exact source identity",
            )
        return _PreparedCodeUnit(
            raw_object_id=raw_object_id,
            raw_content_sha256=raw_digest,
            selected_content_sha256=exact_bytes_sha256(selected_bytes),
            selected_bytes=selected_bytes,
            selector=selector,
            selector_sha256=selector_sha256,
        )

    @staticmethod
    def _selector(span: CodeUnitSpan) -> dict[str, object]:
        if (
            type(span.start_byte) is not int
            or type(span.end_byte) is not int
            or type(span.start_line) is not int
            or type(span.end_line) is not int
            or span.start_byte < 0
            or span.end_byte < span.start_byte
            or span.start_line < 1
            or span.end_line < 1
        ):
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "Code unit range is invalid")
        return {
            "start_byte": span.start_byte,
            "end_byte": span.end_byte,
            "start_line": span.start_line,
            "end_line": span.end_line,
            "newline_policy": "preserve_v1",
        }

    @staticmethod
    def _body_bytes(unit: CodeUnitRecord) -> bytes:
        if type(unit.body) is not str:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "Code unit body must be exact text")
        try:
            return unit.body.encode("utf-8")
        except UnicodeEncodeError as error:
            raise RawV2ContractError(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "Code unit body is not valid UTF-8",
            ) from error

    @staticmethod
    def _verify_file_bytes(
        *,
        unit: CodeUnitRecord,
        raw_file_bytes: bytes,
        raw_digest: str,
        selected_bytes: bytes,
    ) -> None:
        if type(raw_file_bytes) is not bytes:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "raw file must be exact bytes")
        if exact_bytes_sha256(raw_file_bytes) != raw_digest:
            _fail(RawV2Reason.RAW_DIGEST_MISMATCH, "Code file digest differs from lineage")
        try:
            raw_file_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RawV2ContractError(
                RawV2Reason.UNSUPPORTED_MEDIA,
                "Code raw file is not strict UTF-8",
            ) from error
        span = unit.span
        if span.end_byte > len(raw_file_bytes):
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "Code byte range exceeds the file")
        try:
            raw_file_bytes[: span.start_byte].decode("utf-8")
            raw_file_bytes[: span.end_byte].decode("utf-8")
        except UnicodeDecodeError as error:
            raise RawV2ContractError(
                RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                "Code byte range splits a UTF-8 code point",
            ) from error
        expected_start_line = 1 + raw_file_bytes[: span.start_byte].count(b"\n")
        expected_end_line = 1 + raw_file_bytes[: span.end_byte].count(b"\n")
        if span.start_line != expected_start_line or span.end_line != expected_end_line:
            _fail(
                RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                "Code byte and line coordinates disagree",
            )
        if raw_file_bytes[span.start_byte : span.end_byte] != selected_bytes:
            _fail(
                RawV2Reason.SELECTED_DIGEST_MISMATCH,
                "Code unit body differs from the exact source range",
            )

    @staticmethod
    def _require_binding_coherence(
        *,
        unit: CodeUnitRecord,
        binding: RawBindingRecord,
        prepared: _PreparedCodeUnit,
    ) -> None:
        if type(binding) is not RawBindingRecord:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "binding receipt type is not frozen")
        try:
            validate_locator_id(binding.locator_id)
            validate_sha256_digest(binding.binding_sha256)
            validate_sha256_digest(binding.selector_sha256)
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "binding receipt identity is malformed",
            ) from error
        expected = {
            "project_id": unit.project_id,
            "raw_object_id": prepared.raw_object_id,
            "generation_id": unit.generation_id,
            "derived_entity_id": unit.entity_id,
            "retrieval_unit_id": unit.unit_id,
            "selector_sha256": prepared.selector_sha256,
            "raw_content_sha256": prepared.raw_content_sha256,
            "selected_content_sha256": prepared.selected_content_sha256,
        }
        if any(getattr(binding, name) != value for name, value in expected.items()):
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "Code unit and binding receipt differ",
            )


__all__ = [
    "CODE_RAW_GATEWAY_V2_VERSION",
    "CodeRawBindingRequestV2",
    "CodeRawGatewayV2",
    "CodeRawReadRequestV2",
]
