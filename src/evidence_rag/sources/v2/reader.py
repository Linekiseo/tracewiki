"""Shared descriptor-safe managed reader for raw V2 authority.

T1.2.1 owns logical binding resolution through authenticated raw bytes. T1.2.2
adds an explicitly invoked selected-read seam with a fresh post-selector authority
recheck. Neither path projects an outward response, writes audit state, falls back
to V1/Wiki content, or mounts itself into application runtime.
"""

from __future__ import annotations

import errno
import hashlib
import os
import sqlite3
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .contracts import (
    RawLogicalIdentityPayload,
    RawV2ContractError,
    RawV2Reason,
    SizeLimit,
    SourceDomain,
    derive_raw_object_id,
    ensure_utf8_size,
    normalize_portable_id,
    source_domain_for_type,
    validate_locator_id,
    validate_raw_object_id,
    validate_sha256_digest,
    verify_canonical_timestamp,
)
from .policy import ObjectVisibility, RawV2ProjectPolicyAdapter
from .schema import RawV2SchemaError, inspect_raw_v2_schema
from .store import RawBindingRecord, RawBindingResolveRequest, RawV2BindingStore

if TYPE_CHECKING:
    from .selector import RawSelectedReadResult, RawV2SelectorExecutor

RAW_V2_MANAGED_READER_VERSION = "raw-v2-managed-reader-v1"
_READ_CHUNK_BYTES = 1024 * 1024
_HAS_SAFE_DIR_FD = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
)

_BINDING_COLUMNS = (
    "locator_id",
    "binding_sha256",
    "project_id",
    "source_domain",
    "raw_object_id",
    "source_version",
    "stable_version",
    "generation_id",
    "derived_entity_id",
    "retrieval_unit_id",
    "derivation_kind",
    "derivation_version",
    "selector_kind",
    "selector_json",
    "selector_sha256",
    "raw_content_sha256",
    "selected_content_sha256",
    "parser_artifact_sha256",
    "acl_ref",
    "visibility_partition_sha256",
    "valid_from",
    "valid_to",
    "observed_at",
    "binding_json",
    "created_at",
    "invalidated_at",
    "invalidation_reason",
)
_OBJECT_COLUMNS = (
    "raw_object_id",
    "logical_identity_sha256",
    "project_id",
    "source_domain",
    "source_type",
    "source_instance_id",
    "source_object_id",
    "source_version",
    "stable_version",
    "acl_ref",
    "visibility_partition_sha256",
    "raw_content_sha256",
    "blob_sha256",
    "media_type",
    "byte_length",
    "state",
    "adapter_version",
    "schema_version",
    "metadata_json",
    "observed_at",
    "tombstoned_at",
    "created_at",
)
_BLOB_COLUMNS = (
    "blob_sha256",
    "byte_length",
    "storage_key",
    "storage_state",
    "first_verified_at",
    "last_verified_at",
    "created_at",
)


@dataclass(frozen=True, slots=True)
class RawManagedReadRequest:
    """Logical authority expectations; physical and ACL inputs are intentionally absent."""

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


@dataclass(frozen=True, slots=True)
class RawManagedReadResult:
    """Authenticated raw bytes plus the stored, not-yet-executed selector envelope."""

    project_id: str
    locator_id: str
    binding_sha256: str
    raw_object_id: str
    source_domain: SourceDomain
    source_type: str
    source_instance_id: str
    source_object_id: str
    source_version: str
    stable_version: str
    generation_id: str
    media_type: str
    raw_content_sha256: str
    byte_length: int
    selector_kind: str
    selector_json: str
    selector_sha256: str
    selected_content_sha256: str
    parser_artifact_sha256: str | None
    raw_bytes: bytes


@dataclass(frozen=True, slots=True)
class _PreparedReadRequest:
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


@dataclass(frozen=True, slots=True)
class _ManagedReadContext:
    prepared: _PreparedReadRequest
    binding: dict[str, object]
    raw_object: dict[str, object]
    record: RawBindingRecord
    source_domain: SourceDomain
    raw_digest: str
    byte_length: int
    raw_bytes: bytes
    binding_store: RawV2BindingStore


def _fail(reason: RawV2Reason, detail: str) -> None:
    raise RawV2ContractError(reason, detail)


def _canonical_text(value: object, *, limit: SizeLimit, label: str) -> str:
    if type(value) is not str:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} must be exact str")
    try:
        normalized = unicodedata.normalize("NFC", value)
        normalized.encode("utf-8")
    except UnicodeError:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} is not valid UTF-8")
    if not normalized or any(ord(character) < 32 for character in normalized):
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} is empty or contains C0")
    ensure_utf8_size(normalized, limit, label=label)
    return normalized


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return (int(metadata.st_dev), int(metadata.st_ino))


class RawV2ManagedReader:
    """Resolve one exact V2 binding and read its managed blob without path escape."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        blob_root: Path,
        policy: RawV2ProjectPolicyAdapter,
        max_raw_bytes: int,
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be an open sqlite3.Connection")
        if not isinstance(blob_root, Path):
            raise TypeError("blob_root must be a pathlib.Path")
        if not isinstance(policy, RawV2ProjectPolicyAdapter):
            raise TypeError("policy must be a RawV2ProjectPolicyAdapter")
        if type(max_raw_bytes) is not int or max_raw_bytes <= 0:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "max_raw_bytes must be an exact positive integer",
            )
        self._connection = connection
        self._root = blob_root
        self._policy = policy
        self._max_raw_bytes = max_raw_bytes
        root_descriptor = self._open_root_chain()
        try:
            self._root_identity = _identity(os.fstat(root_descriptor))
        finally:
            os.close(root_descriptor)

    def read(self, *, request: RawManagedReadRequest) -> RawManagedReadResult:
        """Return exact authenticated raw bytes; never apply a selector or fallback."""

        context = self._read_context(request)
        self._recheck_context(context)
        return self._context_result(context)

    def read_selected(
        self,
        *,
        request: RawManagedReadRequest,
        selector_executor: RawV2SelectorExecutor,
    ) -> RawSelectedReadResult:
        """Apply the stored selector and recheck authority before selected output."""

        from .selector import RawV2SelectorExecutor

        if type(selector_executor) is not RawV2SelectorExecutor:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "selector executor type is not the frozen trusted executor",
            )
        context = self._read_context(request)
        raw_result = self._context_result(context)
        selected = selector_executor._execute(raw=raw_result)
        self._recheck_context(context)
        return selector_executor._result(raw=raw_result, selected=selected)

    def _read_context(self, request: RawManagedReadRequest) -> _ManagedReadContext:
        prepared = self._prepare_request(request)
        self._require_ready()
        scope = self._policy.resolve_requester_scope(
            project_id=prepared.project_id,
            principal_id=prepared.principal_id,
        )
        binding = self._binding_row(prepared)
        raw_object = self._object_row(prepared, binding)
        source_domain = self._verify_source_identity(prepared, binding, raw_object)
        self._authorize(scope=scope, binding=binding, raw_object=raw_object)
        self._verify_raw_state(raw_object)
        raw_digest, byte_length = self._verify_raw_identity(
            prepared,
            binding,
            raw_object,
            source_domain=source_domain,
        )
        self._verify_derivation(prepared, binding, raw_object)
        storage_key = self._verify_blob_metadata(
            raw_object=raw_object,
            raw_digest=raw_digest,
            byte_length=byte_length,
        )
        binding_store = RawV2BindingStore(
            connection=self._connection,
            policy=self._policy,
            clock=lambda: "1970-01-01T00:00:00.000000Z",
        )
        record = binding_store.resolve(
            request=RawBindingResolveRequest(
                project_id=prepared.project_id,
                locator_id=prepared.locator_id,
            )
        )
        if record is None:
            _fail(
                RawV2Reason.DERIVATION_INVALIDATED,
                "binding became inactive before managed storage read",
            )
        self._verify_resolved_record(prepared, binding, record)
        if byte_length > self._max_raw_bytes:
            _fail(
                RawV2Reason.CONTRACT_SIZE_EXCEEDED,
                "stored raw byte length exceeds trusted reader ceiling",
            )
        raw_bytes = self._read_managed_blob(
            storage_key=storage_key,
            expected_digest=raw_digest,
            expected_length=byte_length,
        )
        return _ManagedReadContext(
            prepared=prepared,
            binding=binding,
            raw_object=raw_object,
            record=record,
            source_domain=source_domain,
            raw_digest=raw_digest,
            byte_length=byte_length,
            raw_bytes=raw_bytes,
            binding_store=binding_store,
        )

    def _recheck_context(self, context: _ManagedReadContext) -> None:
        context.binding_store.recheck_before_output(record=context.record)
        current_object = self._object_row(context.prepared, context.binding)
        if current_object != context.raw_object:
            _fail(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "raw object authority changed before output",
            )
        self._verify_raw_state(current_object)

    def _context_result(self, context: _ManagedReadContext) -> RawManagedReadResult:
        return self._result(
            prepared=context.prepared,
            binding=context.binding,
            raw_object=context.raw_object,
            record=context.record,
            source_domain=context.source_domain,
            raw_digest=context.raw_digest,
            byte_length=context.byte_length,
            raw_bytes=context.raw_bytes,
        )

    def _prepare_request(self, request: RawManagedReadRequest) -> _PreparedReadRequest:
        if type(request) is not RawManagedReadRequest:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "request type is not frozen")
        if type(request.expected_source_domain) is not SourceDomain:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "expected source domain must be a frozen SourceDomain",
            )
        expected_source_type = _canonical_text(
            request.expected_source_type,
            limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
            label="expected source type",
        )
        try:
            mapped_domain = source_domain_for_type(expected_source_type)
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "expected source type is not registered",
            ) from error
        if mapped_domain is not request.expected_source_domain:
            _fail(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "expected source type and domain differ",
            )
        return _PreparedReadRequest(
            project_id=normalize_portable_id(request.project_id, label="project ID"),
            principal_id=normalize_portable_id(request.principal_id, label="principal ID"),
            locator_id=validate_locator_id(request.locator_id),
            expected_binding_sha256=validate_sha256_digest(request.expected_binding_sha256),
            expected_source_domain=request.expected_source_domain,
            expected_source_type=expected_source_type,
            expected_source_instance_id=normalize_portable_id(
                request.expected_source_instance_id,
                label="expected source instance ID",
            ),
            expected_source_object_id=_canonical_text(
                request.expected_source_object_id,
                limit=SizeLimit.SOURCE_OBJECT_ID,
                label="expected source object ID",
            ),
            expected_source_version=_canonical_text(
                request.expected_source_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="expected source version",
            ),
            expected_stable_version=_canonical_text(
                request.expected_stable_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="expected stable version",
            ),
            expected_generation_id=normalize_portable_id(
                request.expected_generation_id,
                label="expected generation ID",
            ),
        )

    def _require_ready(self) -> None:
        if self._connection.in_transaction:
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "managed read requires no caller transaction",
            )
        try:
            inspect_raw_v2_schema(self._connection)
        except RawV2SchemaError as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "raw V2 schema is not exact",
            ) from error

    def _fetch_one(
        self,
        *,
        table: str,
        columns: tuple[str, ...],
        where: str,
        values: tuple[object, ...],
    ) -> dict[str, object] | None:
        try:
            row = self._connection.execute(
                f"SELECT {', '.join(columns)} FROM {table} WHERE {where}",
                values,
            ).fetchone()
        except sqlite3.Error as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "raw V2 authority query failed",
            ) from error
        return None if row is None else dict(zip(columns, tuple(row), strict=True))

    def _binding_row(self, request: _PreparedReadRequest) -> dict[str, object]:
        row = self._fetch_one(
            table="raw_evidence_bindings_v2",
            columns=_BINDING_COLUMNS,
            where="project_id=? AND locator_id=?",
            values=(request.project_id, request.locator_id),
        )
        if row is None:
            _fail(RawV2Reason.BINDING_NOT_FOUND, "project locator has no stored binding")
        try:
            locator_id = validate_locator_id(row["locator_id"])
            binding_sha256 = validate_sha256_digest(row["binding_sha256"])
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "stored binding locator or digest is malformed",
            ) from error
        if locator_id != request.locator_id or row["project_id"] != request.project_id:
            _fail(RawV2Reason.BINDING_DIGEST_MISMATCH, "stored binding scope differs")
        if binding_sha256 != request.expected_binding_sha256:
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "expected binding digest differs from stored binding",
            )
        return row

    def _object_row(
        self,
        request: _PreparedReadRequest,
        binding: dict[str, object],
    ) -> dict[str, object]:
        try:
            raw_object_id = validate_raw_object_id(binding["raw_object_id"])
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "binding raw object ID is malformed",
            ) from error
        row = self._fetch_one(
            table="raw_objects_v2",
            columns=_OBJECT_COLUMNS,
            where="project_id=? AND raw_object_id=?",
            values=(request.project_id, raw_object_id),
        )
        if row is None:
            _fail(RawV2Reason.RAW_NOT_FOUND, "binding raw object is absent")
        return row

    @staticmethod
    def _verify_source_identity(
        request: _PreparedReadRequest,
        binding: dict[str, object],
        raw_object: dict[str, object],
    ) -> SourceDomain:
        try:
            stored_domain = SourceDomain(raw_object["source_domain"])
        except (TypeError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "stored source domain is invalid",
            ) from error
        expected = {
            "source_domain": request.expected_source_domain.value,
            "source_type": request.expected_source_type,
            "source_instance_id": request.expected_source_instance_id,
            "source_object_id": request.expected_source_object_id,
            "source_version": request.expected_source_version,
        }
        if any(raw_object[key] != value for key, value in expected.items()):
            _fail(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "stored raw source identity differs from expected identity",
            )
        if (
            stored_domain is not request.expected_source_domain
            or binding["source_domain"] != stored_domain.value
            or binding["source_version"] != request.expected_source_version
        ):
            _fail(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "binding and raw source identity differ",
            )
        return stored_domain

    def _authorize(
        self,
        *,
        scope: object,
        binding: dict[str, object],
        raw_object: dict[str, object],
    ) -> None:
        if (
            binding["acl_ref"] != raw_object["acl_ref"]
            or binding["visibility_partition_sha256"] != raw_object["visibility_partition_sha256"]
        ):
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "binding and raw visibility authority differ",
            )
        try:
            visibility = ObjectVisibility(
                project_id=raw_object["project_id"],
                acl_ref=raw_object["acl_ref"],
            )
        except (RawV2ContractError, TypeError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.VISIBILITY_MISMATCH,
                "stored raw visibility is malformed",
            ) from error
        self._policy.authorize(
            scope=scope,  # type: ignore[arg-type]
            object_visibility=visibility,
            stored_visibility_partition_sha256=raw_object["visibility_partition_sha256"],
        )

    @staticmethod
    def _verify_raw_state(raw_object: dict[str, object]) -> None:
        state = raw_object["state"]
        if state == "quarantined":
            _fail(RawV2Reason.RAW_QUARANTINED, "raw object is quarantined")
        if state == "tombstoned" or raw_object["tombstoned_at"] is not None:
            _fail(RawV2Reason.RAW_TOMBSTONED, "raw object is tombstoned")
        if state == "reference_only":
            _fail(
                RawV2Reason.REFERENCE_ONLY_UNAVAILABLE,
                "reference-only object has no managed bytes",
            )
        if state != "active":
            _fail(RawV2Reason.RAW_STATE_INVALID, "raw object is not active")

    @staticmethod
    def _verify_raw_identity(
        request: _PreparedReadRequest,
        binding: dict[str, object],
        raw_object: dict[str, object],
        *,
        source_domain: SourceDomain,
    ) -> tuple[str, int]:
        try:
            raw_object_id = validate_raw_object_id(raw_object["raw_object_id"])
            raw_digest = validate_sha256_digest(raw_object["raw_content_sha256"])
            blob_digest = validate_sha256_digest(raw_object["blob_sha256"])
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "active raw byte identity is malformed",
            ) from error
        byte_length = raw_object["byte_length"]
        if type(byte_length) is not int or byte_length < 0:
            _fail(RawV2Reason.BYTE_LENGTH_MISMATCH, "stored raw byte length is invalid")
        if raw_digest != blob_digest or binding["raw_content_sha256"] != raw_digest:
            _fail(
                RawV2Reason.RAW_DIGEST_MISMATCH,
                "binding, object and blob digests differ",
            )
        try:
            identity = RawLogicalIdentityPayload(
                project_id=raw_object["project_id"],
                source_domain=source_domain,
                source_type=raw_object["source_type"],
                source_instance_id=raw_object["source_instance_id"],
                source_object_id=raw_object["source_object_id"],
                source_version=raw_object["source_version"],
                stable_version=raw_object["stable_version"],
                raw_content_sha256=raw_digest,
                acl_ref=raw_object["acl_ref"],
                visibility_partition_sha256=raw_object["visibility_partition_sha256"],
            )
            expected_raw_object_id = derive_raw_object_id(identity)
        except (RawV2ContractError, TypeError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "stored raw logical identity is malformed",
            ) from error
        expected_logical_digest = "sha256:" + expected_raw_object_id.removeprefix("raw-v2:")
        if (
            raw_object_id != expected_raw_object_id
            or binding["raw_object_id"] != expected_raw_object_id
            or raw_object["logical_identity_sha256"] != expected_logical_digest
            or raw_object["project_id"] != request.project_id
        ):
            _fail(
                RawV2Reason.SOURCE_IDENTITY_MISMATCH,
                "stored raw logical identity digest differs",
            )
        return raw_digest, byte_length

    def _verify_derivation(
        self,
        request: _PreparedReadRequest,
        binding: dict[str, object],
        raw_object: dict[str, object],
    ) -> None:
        if binding["invalidated_at"] is not None or binding["invalidation_reason"] is not None:
            _fail(RawV2Reason.DERIVATION_INVALIDATED, "binding is invalidated")
        blocked = self._fetch_one(
            table="blocked_entities_v2",
            columns=("project_id",),
            where="project_id=? AND entity_id=?",
            values=(request.project_id, binding["derived_entity_id"]),
        )
        if blocked is not None:
            _fail(RawV2Reason.DERIVATION_INVALIDATED, "derived entity is blocked")
        if binding["generation_id"] != request.expected_generation_id:
            _fail(RawV2Reason.GENERATION_MISMATCH, "binding generation differs")
        if (
            binding["stable_version"] != request.expected_stable_version
            or raw_object["stable_version"] != request.expected_stable_version
        ):
            _fail(RawV2Reason.STABLE_VERSION_MISMATCH, "stored stable version differs")

    def _verify_blob_metadata(
        self,
        *,
        raw_object: dict[str, object],
        raw_digest: str,
        byte_length: int,
    ) -> str:
        row = self._fetch_one(
            table="raw_blobs_v2",
            columns=_BLOB_COLUMNS,
            where="blob_sha256=?",
            values=(raw_digest,),
        )
        if row is None:
            _fail(RawV2Reason.STORAGE_UNAVAILABLE, "raw blob metadata is absent")
        state = row["storage_state"]
        if state == "quarantined":
            _fail(RawV2Reason.RAW_QUARANTINED, "raw blob is quarantined")
        if state != "available":
            _fail(RawV2Reason.STORAGE_UNAVAILABLE, "raw blob storage is unavailable")
        try:
            blob_digest = validate_sha256_digest(row["blob_sha256"])
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_DIGEST_MISMATCH,
                "blob metadata digest is malformed",
            ) from error
        if blob_digest != raw_digest or raw_object["blob_sha256"] != raw_digest:
            _fail(RawV2Reason.RAW_DIGEST_MISMATCH, "blob metadata digest differs")
        if type(row["byte_length"]) is not int or row["byte_length"] != byte_length:
            _fail(RawV2Reason.BYTE_LENGTH_MISMATCH, "blob metadata length differs")
        digest_hex = raw_digest.removeprefix("sha256:")
        expected_key = f"{digest_hex[:2]}/{digest_hex[2:4]}/{digest_hex}"
        if type(row["storage_key"]) is not str or row["storage_key"] != expected_key:
            _fail(
                RawV2Reason.STORAGE_OUTSIDE_ROOT,
                "blob storage key is not exact content-addressed authority",
            )
        try:
            for key in ("first_verified_at", "last_verified_at", "created_at"):
                verify_canonical_timestamp(row[key])
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.STORAGE_UNAVAILABLE,
                "blob verification timestamps are invalid",
            ) from error
        return expected_key

    @staticmethod
    def _verify_resolved_record(
        request: _PreparedReadRequest,
        binding: dict[str, object],
        record: RawBindingRecord,
    ) -> None:
        expected = {
            "project_id": request.project_id,
            "locator_id": request.locator_id,
            "binding_sha256": request.expected_binding_sha256,
            "raw_object_id": binding["raw_object_id"],
            "generation_id": request.expected_generation_id,
            "selector_sha256": binding["selector_sha256"],
            "raw_content_sha256": binding["raw_content_sha256"],
            "selected_content_sha256": binding["selected_content_sha256"],
        }
        if any(getattr(record, key) != value for key, value in expected.items()):
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "resolved canonical binding differs from managed read authority",
            )

    def _open_root_chain(self) -> int:
        if not self._root.is_absolute() or any(
            component in {"", ".", ".."} for component in self._root.parts[1:]
        ):
            _fail(
                RawV2Reason.STORAGE_OUTSIDE_ROOT,
                "managed root must be an exact absolute path without traversal",
            )
        if not _HAS_SAFE_DIR_FD:
            _fail(
                RawV2Reason.STORAGE_UNAVAILABLE,
                "platform lacks required no-follow directory descriptor support",
            )
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        anchor = self._root.anchor
        try:
            descriptor = os.open(anchor, flags)
        except OSError as error:
            raise RawV2ContractError(
                RawV2Reason.STORAGE_UNAVAILABLE,
                "managed root anchor is unavailable",
            ) from error
        try:
            for component in self._root.parts[1:]:
                child = self._open_directory_at(descriptor, component, flags=flags)
                os.close(descriptor)
                descriptor = child
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                _fail(RawV2Reason.STORAGE_NOT_REGULAR, "managed root is not a directory")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _lstat_at(parent_descriptor: int, component: str) -> os.stat_result:
        try:
            return os.stat(component, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as error:
            raise RawV2ContractError(
                RawV2Reason.STORAGE_UNAVAILABLE,
                "managed storage component is unavailable",
            ) from error

    @classmethod
    def _classify_open_error(
        cls,
        *,
        parent_descriptor: int,
        component: str,
        expected_directory: bool,
        error: OSError,
    ) -> RawV2ContractError:
        try:
            current = os.stat(component, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError:
            return RawV2ContractError(
                RawV2Reason.STORAGE_UNAVAILABLE,
                "managed storage component cannot be opened",
            )
        if stat.S_ISLNK(current.st_mode) or error.errno == errno.ELOOP:
            return RawV2ContractError(
                RawV2Reason.STORAGE_SYMLINK,
                "managed storage component is a symlink",
            )
        expected = (
            stat.S_ISDIR(current.st_mode) if expected_directory else stat.S_ISREG(current.st_mode)
        )
        if not expected:
            return RawV2ContractError(
                RawV2Reason.STORAGE_NOT_REGULAR,
                "managed storage component has the wrong file type",
            )
        return RawV2ContractError(
            RawV2Reason.STORAGE_UNAVAILABLE,
            "managed storage component changed during open",
        )

    @classmethod
    def _open_directory_at(cls, parent_descriptor: int, component: str, *, flags: int) -> int:
        before = cls._lstat_at(parent_descriptor, component)
        if stat.S_ISLNK(before.st_mode):
            _fail(RawV2Reason.STORAGE_SYMLINK, "managed directory component is a symlink")
        if not stat.S_ISDIR(before.st_mode):
            _fail(
                RawV2Reason.STORAGE_NOT_REGULAR,
                "managed directory component is not a directory",
            )
        try:
            descriptor = os.open(component, flags, dir_fd=parent_descriptor)
        except OSError as error:
            raise cls._classify_open_error(
                parent_descriptor=parent_descriptor,
                component=component,
                expected_directory=True,
                error=error,
            ) from error
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISDIR(opened.st_mode):
                _fail(
                    RawV2Reason.STORAGE_NOT_REGULAR,
                    "opened managed component is not a directory",
                )
            if _identity(opened) != _identity(before):
                _fail(
                    RawV2Reason.STORAGE_OUTSIDE_ROOT,
                    "managed directory identity changed during open",
                )
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    @classmethod
    def _open_file_at(cls, parent_descriptor: int, component: str) -> int:
        before = cls._lstat_at(parent_descriptor, component)
        if stat.S_ISLNK(before.st_mode):
            _fail(RawV2Reason.STORAGE_SYMLINK, "managed blob target is a symlink")
        if not stat.S_ISREG(before.st_mode):
            _fail(RawV2Reason.STORAGE_NOT_REGULAR, "managed blob target is not regular")
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(component, flags, dir_fd=parent_descriptor)
        except OSError as error:
            raise cls._classify_open_error(
                parent_descriptor=parent_descriptor,
                component=component,
                expected_directory=False,
                error=error,
            ) from error
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                _fail(RawV2Reason.STORAGE_NOT_REGULAR, "opened managed blob is not regular")
            if _identity(opened) != _identity(before):
                _fail(
                    RawV2Reason.STORAGE_OUTSIDE_ROOT,
                    "managed blob identity changed during open",
                )
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _read_managed_blob(
        self,
        *,
        storage_key: str,
        expected_digest: str,
        expected_length: int,
    ) -> bytes:
        parts = storage_key.split("/")
        if len(parts) != 3 or any(
            not component or component in {".", ".."} or "/" in component or "\\" in component
            for component in parts
        ):
            _fail(RawV2Reason.STORAGE_OUTSIDE_ROOT, "storage key has unsafe components")
        root_descriptor = self._open_root_chain()
        descriptor = root_descriptor
        try:
            if _identity(os.fstat(root_descriptor)) != self._root_identity:
                _fail(RawV2Reason.STORAGE_OUTSIDE_ROOT, "managed root identity changed")
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                directory_flags |= os.O_CLOEXEC
            for component in parts[:2]:
                child = self._open_directory_at(
                    descriptor,
                    component,
                    flags=directory_flags,
                )
                if descriptor != root_descriptor:
                    os.close(descriptor)
                descriptor = child
            file_descriptor = self._open_file_at(descriptor, parts[2])
            try:
                opened = os.fstat(file_descriptor)
                if opened.st_size != expected_length:
                    _fail(
                        RawV2Reason.BYTE_LENGTH_MISMATCH,
                        "managed blob stat length differs from authority",
                    )
                hasher = hashlib.sha256()
                chunks: list[bytes] = []
                observed_length = 0
                while observed_length <= expected_length:
                    read_size = min(
                        _READ_CHUNK_BYTES,
                        expected_length + 1 - observed_length,
                    )
                    if read_size <= 0:
                        break
                    try:
                        chunk = os.read(file_descriptor, read_size)
                    except OSError as error:
                        raise RawV2ContractError(
                            RawV2Reason.STORAGE_UNAVAILABLE,
                            "managed blob read failed",
                        ) from error
                    if not chunk:
                        break
                    chunks.append(chunk)
                    hasher.update(chunk)
                    observed_length += len(chunk)
                if observed_length == expected_length:
                    try:
                        extra = os.read(file_descriptor, 1)
                    except OSError as error:
                        raise RawV2ContractError(
                            RawV2Reason.STORAGE_UNAVAILABLE,
                            "managed blob final read failed",
                        ) from error
                    if extra:
                        observed_length += len(extra)
                if observed_length != expected_length:
                    _fail(
                        RawV2Reason.BYTE_LENGTH_MISMATCH,
                        "managed blob byte length differs from authority",
                    )
                observed_digest = "sha256:" + hasher.hexdigest()
                if observed_digest != expected_digest:
                    _fail(
                        RawV2Reason.RAW_DIGEST_MISMATCH,
                        "managed blob SHA-256 differs from authority",
                    )
                return b"".join(chunks)
            finally:
                os.close(file_descriptor)
        finally:
            if descriptor != root_descriptor:
                os.close(descriptor)
            os.close(root_descriptor)

    @staticmethod
    def _result(
        *,
        prepared: _PreparedReadRequest,
        binding: dict[str, object],
        raw_object: dict[str, object],
        record: RawBindingRecord,
        source_domain: SourceDomain,
        raw_digest: str,
        byte_length: int,
        raw_bytes: bytes,
    ) -> RawManagedReadResult:
        selector_json = binding["selector_json"]
        parser_digest = binding["parser_artifact_sha256"]
        try:
            selector_kind = _canonical_text(
                binding["selector_kind"],
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="stored selector kind",
            )
            if type(selector_json) is not str:
                raise TypeError("selector JSON is not text")
            selector_sha256 = validate_sha256_digest(binding["selector_sha256"])
            selected_digest = validate_sha256_digest(binding["selected_content_sha256"])
            canonical_parser_digest = (
                None if parser_digest is None else validate_sha256_digest(parser_digest)
            )
            media_type = _canonical_text(
                raw_object["media_type"],
                limit=SizeLimit.MEDIA_TYPE,
                label="stored media type",
            )
        except (RawV2ContractError, TypeError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "managed read result authority is malformed",
            ) from error
        return RawManagedReadResult(
            project_id=prepared.project_id,
            locator_id=record.locator_id,
            binding_sha256=record.binding_sha256,
            raw_object_id=record.raw_object_id,
            source_domain=source_domain,
            source_type=prepared.expected_source_type,
            source_instance_id=prepared.expected_source_instance_id,
            source_object_id=prepared.expected_source_object_id,
            source_version=prepared.expected_source_version,
            stable_version=prepared.expected_stable_version,
            generation_id=prepared.expected_generation_id,
            media_type=media_type,
            raw_content_sha256=raw_digest,
            byte_length=byte_length,
            selector_kind=selector_kind,
            selector_json=selector_json,
            selector_sha256=selector_sha256,
            selected_content_sha256=selected_digest,
            parser_artifact_sha256=canonical_parser_digest,
            raw_bytes=raw_bytes,
        )


__all__ = [
    "RAW_V2_MANAGED_READER_VERSION",
    "RawManagedReadRequest",
    "RawManagedReadResult",
    "RawV2ManagedReader",
]
