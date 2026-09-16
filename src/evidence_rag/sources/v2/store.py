"""Fixture-safe raw V2 object, event, binding, and tombstone persistence.

This module owns only T1.1.4 object writes, T1.1.5 event writes, and T1.1.6
binding persistence/exact resolution plus T1.1.7 project-scoped tombstone
propagation. It is intentionally disconnected from application runtime, routers,
migration, source readers, selector execution, blob retention, and public
projection.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .contracts import (
    RawEvidenceBindingPayload,
    RawLogicalIdentityPayload,
    RawSelectorPayload,
    RawV2ContractError,
    RawV2Reason,
    SizeLimit,
    SourceDomain,
    SourceEventIdempotencyPayload,
    canonical_json_bytes,
    canonical_timestamp,
    derive_locator_id,
    derive_raw_object_id,
    derive_selector_sha256,
    derive_source_event_id,
    ensure_utf8_size,
    exact_bytes_sha256,
    source_domain_for_type,
    validate_locator_id,
    validate_raw_object_id,
    validate_sha256_digest,
    verify_canonical_json_bytes,
    verify_canonical_timestamp,
)
from .policy import RawV2ProjectPolicyAdapter
from .schema import RawV2SchemaError, inspect_raw_v2_schema

RAW_V2_OBJECT_STORE_VERSION = "raw-v2-object-store-v1"
RAW_V2_EVENT_STORE_VERSION = "raw-v2-event-store-v1"
RAW_V2_BINDING_STORE_VERSION = "raw-v2-binding-store-v1"
RAW_V2_TOMBSTONE_STORE_VERSION = "raw-v2-tombstone-store-v1"


@dataclass(frozen=True, slots=True)
class RawObjectWriteRequest:
    """Trusted server-side inputs whose computed authority fields are omitted."""

    project_id: str
    source_type: str
    source_instance_id: str
    source_object_id: str
    source_version: str
    stable_version: str
    source_acl_ref: str
    media_type: str
    adapter_version: str
    schema_version: str
    metadata_projection: dict[str, object]
    observed_at: str


@dataclass(frozen=True, slots=True)
class RawObjectRecord:
    """Internal write result with no physical location or source-reference field."""

    raw_object_id: str
    logical_identity_sha256: str
    project_id: str
    visibility_partition_sha256: str
    raw_content_sha256: str | None
    byte_length: int | None
    state: str
    created: bool


@dataclass(frozen=True, slots=True)
class RawEventWriteRequest:
    """Trusted event inputs with all computed authority fields omitted."""

    project_id: str
    source_type: str
    source_instance_id: str
    event_type: str
    mutation_id: str
    source_object_id: str
    source_version: str
    source_acl_ref: str
    raw_object_id: str | None
    event_time: str | None
    observed_at: str
    trace_id: str
    schema_version: str
    status: str
    metadata_projection: dict[str, object]


@dataclass(frozen=True, slots=True)
class RawEventRecord:
    """Internal event result without metadata, source reference, or storage fields."""

    event_id: str
    idempotency_sha256: str
    project_id: str
    visibility_partition_sha256: str
    raw_object_id: str | None
    raw_content_sha256: str | None
    status: str
    created: bool


@dataclass(frozen=True, slots=True)
class RawBindingWriteRequest:
    """Trusted binding inputs with all computed authority fields omitted."""

    project_id: str
    raw_object_id: str
    source_acl_ref: str
    source_version: str
    stable_version: str
    generation_id: str
    derived_entity_id: str
    retrieval_unit_id: str
    derivation_kind: str
    derivation_version: str
    selector_kind: str
    selector: dict[str, object]
    parser_artifact_sha256: str | None
    valid_from: str | None
    valid_to: str | None
    observed_at: str
    adapter_version: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class RawBindingResolveRequest:
    """Project-scoped exact binding filters; semantic lookups require generation."""

    project_id: str
    locator_id: str | None = None
    derived_entity_id: str | None = None
    retrieval_unit_id: str | None = None
    generation_id: str | None = None


@dataclass(frozen=True, slots=True)
class RawBindingRecord:
    """Internal active binding result with no selected bytes or canonical JSON."""

    locator_id: str
    binding_sha256: str
    project_id: str
    raw_object_id: str
    generation_id: str
    derived_entity_id: str
    retrieval_unit_id: str
    selector_sha256: str
    raw_content_sha256: str
    selected_content_sha256: str
    created: bool


@dataclass(frozen=True, slots=True)
class RawTombstoneRequest:
    """Trusted project-scoped revocation inputs; object authority is stored."""

    project_id: str
    raw_object_id: str
    source_acl_ref: str
    mutation_id: str
    reason_code: str
    actor_digest: str
    event_time: str | None
    observed_at: str
    trace_id: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class RawTombstoneRecord:
    """Committed tombstone receipt without binding identities or source bytes."""

    raw_object_id: str
    project_id: str
    event_id: str
    tombstoned_at: str
    invalidated_binding_count: int
    blocked_entity_count: int
    created: bool


@dataclass(frozen=True, slots=True)
class _PreparedObject:
    identity: RawLogicalIdentityPayload
    raw_object_id: str
    logical_identity_sha256: str
    raw_content_sha256: str | None
    byte_length: int | None
    media_type: str
    adapter_version: str
    schema_version: str
    metadata_json: str
    observed_at: str
    created_at: str
    state: str


@dataclass(frozen=True, slots=True)
class _PreparedEventInput:
    project_id: str
    source_domain: SourceDomain
    source_type: str
    source_instance_id: str
    event_type: str
    mutation_id: str
    source_object_id: str
    source_version: str
    acl_ref: str
    visibility_partition_sha256: str
    raw_object_id: str | None
    event_time: str | None
    observed_at: str
    trace_id: str
    schema_version: str
    status: str
    metadata_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class _PreparedEvent:
    identity: SourceEventIdempotencyPayload
    event_id: str
    idempotency_sha256: str
    raw_object_id: str | None
    event_time: str | None
    observed_at: str
    trace_id: str
    schema_version: str
    status: str
    metadata_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class _PreparedBindingInput:
    project_id: str
    raw_object_id: str
    acl_ref: str
    visibility_partition_sha256: str
    source_version: str
    stable_version: str
    generation_id: str
    derived_entity_id: str
    retrieval_unit_id: str
    derivation_kind: str
    derivation_version: str
    selector: RawSelectorPayload
    selector_sha256: str
    selector_json: str
    selected_content_sha256: str
    parser_artifact_sha256: str | None
    valid_from: str | None
    valid_to: str | None
    observed_at: str
    adapter_version: str
    schema_version: str
    created_at: str


@dataclass(frozen=True, slots=True)
class _PreparedBinding:
    identity: RawEvidenceBindingPayload
    locator_id: str
    binding_sha256: str
    selector_kind: str
    selector_json: str
    binding_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class _PreparedTombstoneInput:
    project_id: str
    raw_object_id: str
    acl_ref: str
    visibility_partition_sha256: str
    mutation_id: str
    reason_code: str
    actor_digest: str
    event_time: str | None
    observed_at: str
    trace_id: str
    schema_version: str
    created_at: str


@dataclass(frozen=True, slots=True)
class _TombstoneManifest:
    metadata_json: str
    invalidated_binding_count: int
    invalidated_locator_set_sha256: str
    blocked_entity_count: int
    blocked_entity_set_sha256: str


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
_OBJECT_SELECT = ", ".join(_OBJECT_COLUMNS)

_EVENT_COLUMNS = (
    "event_id",
    "idempotency_sha256",
    "project_id",
    "source_domain",
    "source_type",
    "source_instance_id",
    "event_type",
    "mutation_id",
    "source_object_id",
    "source_version",
    "raw_object_id",
    "raw_content_sha256",
    "acl_ref",
    "visibility_partition_sha256",
    "event_time",
    "observed_at",
    "trace_id",
    "schema_version",
    "status",
    "metadata_json",
    "created_at",
)
_EVENT_SELECT = ", ".join(_EVENT_COLUMNS)

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
_BINDING_SELECT = ", ".join(_BINDING_COLUMNS)


def _fail(reason: RawV2Reason, detail: str) -> None:
    raise RawV2ContractError(reason, detail)


def _authority_text(value: object, *, limit: SizeLimit, label: str) -> str:
    if type(value) is not str:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} must be exact str")
    try:
        normalized = unicodedata.normalize("NFC", value)
        normalized.encode("utf-8")
    except UnicodeError:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} is not valid UTF-8")
    if not normalized:
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} must be non-empty")
    if any(ord(character) < 32 for character in normalized):
        _fail(RawV2Reason.CANONICALIZATION_MISMATCH, f"{label} contains a C0 control")
    ensure_utf8_size(normalized, limit, label=label)
    return normalized


def _logical_digest(raw_object_id: str) -> str:
    prefix = "raw-v2:"
    if not raw_object_id.startswith(prefix):
        raise AssertionError("reviewed raw object ID prefix is missing")
    return "sha256:" + raw_object_id.removeprefix(prefix)


class RawV2ObjectStore:
    """Persist verified active bytes or no-bytes reference-only logical objects."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        blob_root: Path,
        policy: RawV2ProjectPolicyAdapter,
        clock: Callable[[], str],
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be an open sqlite3.Connection")
        if not isinstance(blob_root, Path):
            raise TypeError("blob_root must be a pathlib.Path")
        if not isinstance(policy, RawV2ProjectPolicyAdapter):
            raise TypeError("policy must be a RawV2ProjectPolicyAdapter")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._connection = connection
        self._root = blob_root
        self._policy = policy
        self._clock = clock
        self._root_identity = self._validate_root(initial=True)

    def persist_bytes(
        self,
        *,
        request: RawObjectWriteRequest,
        payload: bytes,
        expected_content_sha256: str | None = None,
    ) -> RawObjectRecord:
        """Persist exact bytes and one project/ACL-scoped active logical object."""

        if type(payload) is not bytes:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "payload must be exact bytes")
        digest = exact_bytes_sha256(payload)
        if expected_content_sha256 is not None:
            expected = validate_sha256_digest(expected_content_sha256)
            if expected != digest:
                _fail(
                    RawV2Reason.EXPECTED_DIGEST_MISMATCH,
                    "expected digest differs from exact payload bytes",
                )
        prepared = self._prepare(
            request=request,
            raw_content_sha256=digest,
            byte_length=len(payload),
            state="active",
        )
        self._require_ready()
        storage_key = self._storage_key(digest)
        existing_blob = self._fetch_blob(digest)
        if existing_blob is not None:
            self._assert_blob_row(
                existing_blob,
                digest=digest,
                byte_length=len(payload),
                storage_key=storage_key,
            )
            self._verify_blob_file(self._root / storage_key, payload, digest)
        else:
            self._publish_blob(payload=payload, digest=digest, storage_key=storage_key)
        return self._persist_object(prepared=prepared, storage_key=storage_key)

    def persist_reference(self, *, request: RawObjectWriteRequest) -> RawObjectRecord:
        """Persist one reference-only logical identity without any bytes or blob row."""

        prepared = self._prepare(
            request=request,
            raw_content_sha256=None,
            byte_length=None,
            state="reference_only",
        )
        self._require_ready()
        return self._persist_object(prepared=prepared, storage_key=None)

    def _validate_root(self, *, initial: bool) -> tuple[int, int]:
        if not self._root.is_absolute():
            _fail(RawV2Reason.BLOB_UNAVAILABLE, "blob root must be trusted absolute config")
        try:
            root_stat = self._root.lstat()
        except OSError as error:
            raise RawV2ContractError(
                RawV2Reason.BLOB_UNAVAILABLE,
                "blob root is unavailable",
            ) from error
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            _fail(RawV2Reason.BLOB_UNAVAILABLE, "blob root must be a non-symlink directory")
        identity = (int(root_stat.st_dev), int(root_stat.st_ino))
        if not initial and identity != self._root_identity:
            _fail(RawV2Reason.BLOB_UNAVAILABLE, "blob root identity changed")
        return identity

    def _require_ready(self) -> None:
        if self._connection.in_transaction:
            _fail(RawV2Reason.RAW_STATE_INVALID, "object write requires no caller transaction")
        self._validate_root(initial=False)
        self._connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = self._connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or int(foreign_keys[0]) != 1:
            _fail(RawV2Reason.RAW_STATE_INVALID, "SQLite foreign keys are unavailable")
        try:
            inspect_raw_v2_schema(self._connection)
        except RawV2SchemaError as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "raw V2 schema is not exact",
            ) from error

    def _prepare(
        self,
        *,
        request: RawObjectWriteRequest,
        raw_content_sha256: str | None,
        byte_length: int | None,
        state: str,
    ) -> _PreparedObject:
        if type(request) is not RawObjectWriteRequest:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "request type is not frozen")
        if type(request.metadata_projection) is not dict:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "metadata projection must be an exact JSON object",
            )
        metadata_raw = canonical_json_bytes(request.metadata_projection, allow_none=True)
        ensure_utf8_size(metadata_raw, SizeLimit.CANONICAL_JSON, label="metadata projection")
        visibility = self._policy.resolve_object_visibility(
            project_id=request.project_id,
            source_acl_ref=request.source_acl_ref,
        )
        source_domain = source_domain_for_type(request.source_type)
        identity = RawLogicalIdentityPayload(
            project_id=visibility.project_id,
            source_domain=source_domain,
            source_type=request.source_type,
            source_instance_id=request.source_instance_id,
            source_object_id=request.source_object_id,
            source_version=request.source_version,
            stable_version=request.stable_version,
            raw_content_sha256=raw_content_sha256,
            acl_ref=visibility.acl_ref,
            visibility_partition_sha256=visibility.visibility_partition_sha256,
        )
        raw_object_id = derive_raw_object_id(identity)
        return _PreparedObject(
            identity=identity,
            raw_object_id=raw_object_id,
            logical_identity_sha256=_logical_digest(raw_object_id),
            raw_content_sha256=raw_content_sha256,
            byte_length=byte_length,
            media_type=_authority_text(
                request.media_type,
                limit=SizeLimit.MEDIA_TYPE,
                label="media type",
            ),
            adapter_version=_authority_text(
                request.adapter_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="adapter version",
            ),
            schema_version=_authority_text(
                request.schema_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="schema version",
            ),
            metadata_json=metadata_raw.decode("utf-8"),
            observed_at=canonical_timestamp(request.observed_at),
            created_at=canonical_timestamp(self._clock()),
            state=state,
        )

    @staticmethod
    def _storage_key(digest: str) -> str:
        canonical = validate_sha256_digest(digest)
        digest_hex = canonical.removeprefix("sha256:")
        return f"{digest_hex[:2]}/{digest_hex[2:4]}/{digest_hex}"

    def _fetch_blob(self, digest: str) -> tuple[object, ...] | None:
        row = self._connection.execute(
            """SELECT blob_sha256, byte_length, storage_key, storage_state,
                      first_verified_at, last_verified_at, created_at
               FROM raw_blobs_v2 WHERE blob_sha256=?""",
            (digest,),
        ).fetchone()
        return None if row is None else tuple(row)

    @staticmethod
    def _assert_blob_row(
        row: tuple[object, ...],
        *,
        digest: str,
        byte_length: int,
        storage_key: str,
    ) -> None:
        if len(row) != 7 or tuple(row[:4]) != (
            digest,
            byte_length,
            storage_key,
            "available",
        ):
            _fail(RawV2Reason.BLOB_CORRUPT, "blob metadata conflicts with content address")
        try:
            for value in row[4:]:
                if type(value) is not str:
                    raise ValueError("blob verification timestamp is missing")
                verify_canonical_timestamp(value)
        except (RawV2ContractError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.BLOB_CORRUPT,
                "blob verification timestamps are invalid",
            ) from error

    def _ensure_digest_directories(self, storage_key: str) -> Path:
        parts = storage_key.split("/")
        if len(parts) != 3:
            raise AssertionError("content-address storage key is not canonical")
        cursor = self._root
        for component in parts[:2]:
            cursor = cursor / component
            try:
                current = cursor.lstat()
            except FileNotFoundError:
                try:
                    cursor.mkdir(mode=0o700)
                    current = cursor.lstat()
                except FileExistsError:
                    current = cursor.lstat()
                except OSError as error:
                    raise RawV2ContractError(
                        RawV2Reason.BLOB_UNAVAILABLE,
                        "content-address directory cannot be created",
                    ) from error
            except OSError as error:
                raise RawV2ContractError(
                    RawV2Reason.BLOB_UNAVAILABLE,
                    "content-address directory is unavailable",
                ) from error
            if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
                _fail(
                    RawV2Reason.BLOB_UNAVAILABLE,
                    "content-address directory must not traverse a symlink",
                )
        return cursor / parts[2]

    @staticmethod
    def _verify_blob_file(target: Path, payload: bytes, digest: str) -> None:
        try:
            target_stat = target.lstat()
        except OSError as error:
            raise RawV2ContractError(
                RawV2Reason.BLOB_CORRUPT,
                "content-addressed blob is missing",
            ) from error
        if stat.S_ISLNK(target_stat.st_mode) or not stat.S_ISREG(target_stat.st_mode):
            _fail(RawV2Reason.BLOB_CORRUPT, "content-addressed target is not a regular file")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(target, flags)
            with os.fdopen(descriptor, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode):
                    _fail(RawV2Reason.BLOB_CORRUPT, "opened blob is not a regular file")
                hasher = hashlib.sha256()
                length = 0
                while chunk := handle.read(1024 * 1024):
                    hasher.update(chunk)
                    length += len(chunk)
        except RawV2ContractError:
            raise
        except OSError as error:
            raise RawV2ContractError(
                RawV2Reason.BLOB_CORRUPT,
                "content-addressed blob cannot be read safely",
            ) from error
        observed_digest = "sha256:" + hasher.hexdigest()
        if length != len(payload) or observed_digest != digest:
            _fail(RawV2Reason.BLOB_CORRUPT, "content-addressed blob bytes do not match")

    def _publish_blob(self, *, payload: bytes, digest: str, storage_key: str) -> None:
        target = self._ensure_digest_directories(storage_key)
        try:
            target.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise RawV2ContractError(
                RawV2Reason.BLOB_UNAVAILABLE,
                "content-addressed target cannot be inspected",
            ) from error
        else:
            self._verify_blob_file(target, payload, digest)
            return

        temporary_name: str | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".raw-v2-object-",
                dir=target.parent,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary_name, target, follow_symlinks=False)
            except FileExistsError:
                self._verify_blob_file(target, payload, digest)
            else:
                directory_descriptor = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        except RawV2ContractError:
            raise
        except OSError as error:
            raise RawV2ContractError(
                RawV2Reason.BLOB_UNAVAILABLE,
                "content-addressed blob publication failed",
            ) from error
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    def _expected_object_values(self, prepared: _PreparedObject) -> dict[str, object]:
        identity = prepared.identity
        return {
            "raw_object_id": prepared.raw_object_id,
            "logical_identity_sha256": prepared.logical_identity_sha256,
            "project_id": identity.project_id,
            "source_domain": identity.source_domain.value,
            "source_type": identity.source_type,
            "source_instance_id": identity.source_instance_id,
            "source_object_id": identity.source_object_id,
            "source_version": identity.source_version,
            "stable_version": identity.stable_version,
            "acl_ref": identity.acl_ref,
            "visibility_partition_sha256": identity.visibility_partition_sha256,
            "raw_content_sha256": prepared.raw_content_sha256,
            "blob_sha256": prepared.raw_content_sha256,
            "media_type": prepared.media_type,
            "byte_length": prepared.byte_length,
            "state": prepared.state,
            "adapter_version": prepared.adapter_version,
            "schema_version": prepared.schema_version,
            "metadata_json": prepared.metadata_json,
            "observed_at": prepared.observed_at,
            "tombstoned_at": None,
        }

    def _fetch_objects(self, prepared: _PreparedObject) -> list[dict[str, object]]:
        rows = self._connection.execute(
            f"""SELECT {_OBJECT_SELECT} FROM raw_objects_v2
                WHERE raw_object_id=? OR logical_identity_sha256=?
                ORDER BY raw_object_id""",
            (prepared.raw_object_id, prepared.logical_identity_sha256),
        ).fetchall()
        return [dict(zip(_OBJECT_COLUMNS, tuple(row), strict=True)) for row in rows]

    def _insert_object(self, prepared: _PreparedObject) -> None:
        values = self._expected_object_values(prepared)
        values["created_at"] = prepared.created_at
        placeholders = ", ".join("?" for _ in _OBJECT_COLUMNS)
        self._connection.execute(
            f"INSERT INTO raw_objects_v2({_OBJECT_SELECT}) VALUES ({placeholders})",
            tuple(values[column] for column in _OBJECT_COLUMNS),
        )

    def _exact_existing_object(self, prepared: _PreparedObject) -> bool:
        rows = self._fetch_objects(prepared)
        if not rows:
            return False
        if len(rows) != 1:
            _fail(
                RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT,
                "multiple rows claim one logical object identity",
            )
        row = rows[0]
        expected = self._expected_object_values(prepared)
        if any(row[key] != value for key, value in expected.items()):
            _fail(
                RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT,
                "stored logical object differs from exact request payload",
            )
        try:
            created_at = row["created_at"]
            if type(created_at) is not str:
                raise ValueError("created_at is missing")
            verify_canonical_timestamp(created_at)
        except (RawV2ContractError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT,
                "stored logical object has invalid creation time",
            ) from error
        return True

    def _persist_object(
        self,
        *,
        prepared: _PreparedObject,
        storage_key: str | None,
    ) -> RawObjectRecord:
        created = False
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if prepared.state == "active":
                if storage_key is None or prepared.raw_content_sha256 is None:
                    raise AssertionError("active object requires a blob identity")
                blob = self._fetch_blob(prepared.raw_content_sha256)
                if blob is None:
                    self._connection.execute(
                        """INSERT INTO raw_blobs_v2(
                               blob_sha256, byte_length, storage_key, storage_state,
                               first_verified_at, last_verified_at, created_at
                           ) VALUES (?, ?, ?, 'available', ?, ?, ?)""",
                        (
                            prepared.raw_content_sha256,
                            prepared.byte_length,
                            storage_key,
                            prepared.created_at,
                            prepared.created_at,
                            prepared.created_at,
                        ),
                    )
                else:
                    self._assert_blob_row(
                        blob,
                        digest=prepared.raw_content_sha256,
                        byte_length=int(prepared.byte_length),
                        storage_key=storage_key,
                    )
            if not self._exact_existing_object(prepared):
                self._insert_object(prepared)
                created = True
            self._connection.commit()
        except RawV2ContractError:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        except sqlite3.Error as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise RawV2ContractError(
                RawV2Reason.BLOB_UNAVAILABLE,
                "raw V2 object transaction failed",
            ) from error
        except Exception:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        return RawObjectRecord(
            raw_object_id=prepared.raw_object_id,
            logical_identity_sha256=prepared.logical_identity_sha256,
            project_id=prepared.identity.project_id,
            visibility_partition_sha256=prepared.identity.visibility_partition_sha256,
            raw_content_sha256=prepared.raw_content_sha256,
            byte_length=prepared.byte_length,
            state=prepared.state,
            created=created,
        )


class RawV2EventStore:
    """Persist project/ACL/content/mutation-scoped append-only source events."""

    _STATUS_TO_OBJECT_STATE = {
        "persisted": "active",
        "reference_only": "reference_only",
        "quarantined": "quarantined",
        "tombstone": "tombstoned",
    }

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        policy: RawV2ProjectPolicyAdapter,
        clock: Callable[[], str],
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be an open sqlite3.Connection")
        if not isinstance(policy, RawV2ProjectPolicyAdapter):
            raise TypeError("policy must be a RawV2ProjectPolicyAdapter")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._connection = connection
        self._policy = policy
        self._clock = clock

    def persist(self, *, request: RawEventWriteRequest) -> RawEventRecord:
        """Persist one exact event or return its byte-for-byte equivalent retry."""

        prepared_input = self._prepare_input(request)
        self._require_ready()
        created = False
        prepared: _PreparedEvent | None = None
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            raw_content_sha256, current_object_state = self._resolve_event_content(prepared_input)
            prepared = self._prepare_identity(
                prepared_input,
                raw_content_sha256=raw_content_sha256,
            )
            if not self._exact_existing_event(prepared):
                self._require_new_event_state(
                    prepared_input,
                    current_object_state=current_object_state,
                    raw_content_sha256=raw_content_sha256,
                )
                self._insert_event(prepared)
                created = True
            if not self._exact_existing_event(prepared):
                raise AssertionError("persisted event cannot be read back exactly")
            self._connection.commit()
        except RawV2ContractError:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        except sqlite3.Error as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "raw V2 event transaction failed",
            ) from error
        except Exception:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        if prepared is None:
            raise AssertionError("event preparation must precede a successful commit")
        return RawEventRecord(
            event_id=prepared.event_id,
            idempotency_sha256=prepared.idempotency_sha256,
            project_id=prepared.identity.project_id,
            visibility_partition_sha256=prepared.identity.visibility_partition_sha256,
            raw_object_id=prepared.raw_object_id,
            raw_content_sha256=prepared.identity.raw_content_sha256,
            status=prepared.status,
            created=created,
        )

    def _require_ready(self) -> None:
        if self._connection.in_transaction:
            _fail(RawV2Reason.RAW_STATE_INVALID, "event write requires no caller transaction")
        self._connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = self._connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or int(foreign_keys[0]) != 1:
            _fail(RawV2Reason.RAW_STATE_INVALID, "SQLite foreign keys are unavailable")
        try:
            inspect_raw_v2_schema(self._connection)
        except RawV2SchemaError as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "raw V2 schema is not exact",
            ) from error

    def _prepare_input(self, request: RawEventWriteRequest) -> _PreparedEventInput:
        if type(request) is not RawEventWriteRequest:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "request type is not frozen")
        if type(request.metadata_projection) is not dict:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "metadata projection must be an exact JSON object",
            )
        metadata_raw = canonical_json_bytes(request.metadata_projection, allow_none=True)
        ensure_utf8_size(metadata_raw, SizeLimit.CANONICAL_JSON, label="metadata projection")
        if type(request.status) is not str or request.status not in {
            *self._STATUS_TO_OBJECT_STATE,
            "rejected",
        }:
            _fail(RawV2Reason.RAW_STATE_INVALID, "event status is not reviewed")
        if request.status == "rejected":
            if request.raw_object_id is not None:
                _fail(RawV2Reason.RAW_STATE_INVALID, "rejected event cannot link a raw object")
            raw_object_id = None
        else:
            if request.raw_object_id is None:
                _fail(RawV2Reason.RAW_STATE_INVALID, "linked event requires a raw object")
            raw_object_id = validate_raw_object_id(request.raw_object_id)
        visibility = self._policy.resolve_object_visibility(
            project_id=request.project_id,
            source_acl_ref=request.source_acl_ref,
        )
        source_type = _authority_text(
            request.source_type,
            limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
            label="source type",
        )
        source_domain = source_domain_for_type(source_type)
        if type(request.mutation_id) is not str or not request.mutation_id:
            _fail(
                RawV2Reason.NON_IDEMPOTENT_REJECTED,
                "event requires a stable mutation ID",
            )
        event_time = None if request.event_time is None else canonical_timestamp(request.event_time)
        return _PreparedEventInput(
            project_id=visibility.project_id,
            source_domain=source_domain,
            source_type=source_type,
            source_instance_id=_authority_text(
                request.source_instance_id,
                limit=SizeLimit.PORTABLE_ID,
                label="source instance ID",
            ),
            event_type=_authority_text(
                request.event_type,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="event type",
            ),
            mutation_id=_authority_text(
                request.mutation_id,
                limit=SizeLimit.TRACE_MUTATION_ID,
                label="mutation ID",
            ),
            source_object_id=_authority_text(
                request.source_object_id,
                limit=SizeLimit.SOURCE_OBJECT_ID,
                label="source object ID",
            ),
            source_version=_authority_text(
                request.source_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="source version",
            ),
            acl_ref=visibility.acl_ref,
            visibility_partition_sha256=visibility.visibility_partition_sha256,
            raw_object_id=raw_object_id,
            event_time=event_time,
            observed_at=canonical_timestamp(request.observed_at),
            trace_id=_authority_text(
                request.trace_id,
                limit=SizeLimit.TRACE_MUTATION_ID,
                label="trace ID",
            ),
            schema_version=_authority_text(
                request.schema_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="event schema version",
            ),
            status=request.status,
            metadata_json=metadata_raw.decode("utf-8"),
            created_at=canonical_timestamp(self._clock()),
        )

    def _resolve_event_content(
        self,
        prepared: _PreparedEventInput,
    ) -> tuple[str | None, str | None]:
        if prepared.status == "rejected":
            if prepared.raw_object_id is not None:
                raise AssertionError("rejected event shape was not validated")
            return None, None
        if prepared.raw_object_id is None:
            raise AssertionError("linked event shape was not validated")
        columns = (
            "raw_object_id",
            "project_id",
            "source_domain",
            "source_type",
            "source_instance_id",
            "source_object_id",
            "source_version",
            "acl_ref",
            "visibility_partition_sha256",
            "raw_content_sha256",
            "blob_sha256",
            "byte_length",
            "state",
        )
        row = self._connection.execute(
            f"""SELECT {", ".join(columns)} FROM raw_objects_v2
                WHERE project_id=? AND raw_object_id=?""",
            (prepared.project_id, prepared.raw_object_id),
        ).fetchone()
        if row is None:
            _fail(RawV2Reason.RAW_STATE_INVALID, "linked raw object is unavailable in project")
        stored = dict(zip(columns, tuple(row), strict=True))
        expected_identity = {
            "raw_object_id": prepared.raw_object_id,
            "project_id": prepared.project_id,
            "source_domain": prepared.source_domain.value,
            "source_type": prepared.source_type,
            "source_instance_id": prepared.source_instance_id,
            "source_object_id": prepared.source_object_id,
            "source_version": prepared.source_version,
            "acl_ref": prepared.acl_ref,
            "visibility_partition_sha256": prepared.visibility_partition_sha256,
        }
        if any(stored[key] != value for key, value in expected_identity.items()):
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "linked raw object does not match event authority or status",
            )
        raw_digest = stored["raw_content_sha256"]
        blob_digest = stored["blob_sha256"]
        byte_length = stored["byte_length"]
        current_state = stored["state"]
        if type(current_state) is not str or current_state not in {
            "active",
            "reference_only",
            "quarantined",
            "tombstoned",
            "corrupt",
        }:
            _fail(RawV2Reason.RAW_STATE_INVALID, "raw object state is malformed")
        if current_state == "reference_only":
            if raw_digest is not None or blob_digest is not None or byte_length is not None:
                _fail(RawV2Reason.RAW_STATE_INVALID, "reference-only object has byte identity")
            return None, current_state
        if raw_digest is not None:
            if type(raw_digest) is not str:
                _fail(RawV2Reason.RAW_STATE_INVALID, "raw object content digest is malformed")
            try:
                raw_digest = validate_sha256_digest(raw_digest)
            except RawV2ContractError as error:
                raise RawV2ContractError(
                    RawV2Reason.RAW_STATE_INVALID,
                    "raw object content digest is malformed",
                ) from error
        if blob_digest is not None and blob_digest != raw_digest:
            _fail(RawV2Reason.RAW_STATE_INVALID, "raw object blob/content digests differ")
        if raw_digest is None and byte_length is not None:
            _fail(RawV2Reason.RAW_STATE_INVALID, "raw object length lacks content digest")
        if current_state == "active" and (
            raw_digest is None
            or blob_digest is None
            or type(byte_length) is not int
            or byte_length < 0
        ):
            _fail(RawV2Reason.RAW_STATE_INVALID, "active object byte invariant is incomplete")
        return raw_digest, current_state

    def _require_new_event_state(
        self,
        prepared: _PreparedEventInput,
        *,
        current_object_state: str | None,
        raw_content_sha256: str | None,
    ) -> None:
        if prepared.status == "rejected":
            if current_object_state is not None or raw_content_sha256 is not None:
                raise AssertionError("rejected event unexpectedly resolved object authority")
            return
        expected_state = self._STATUS_TO_OBJECT_STATE[prepared.status]
        if current_object_state != expected_state:
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "linked raw object does not match new event status",
            )
        if prepared.status in {"persisted", "quarantined"} and raw_content_sha256 is None:
            _fail(RawV2Reason.RAW_STATE_INVALID, "event status requires content digest")

    @staticmethod
    def _prepare_identity(
        prepared: _PreparedEventInput,
        *,
        raw_content_sha256: str | None,
    ) -> _PreparedEvent:
        identity = SourceEventIdempotencyPayload(
            project_id=prepared.project_id,
            source_domain=prepared.source_domain,
            source_type=prepared.source_type,
            source_instance_id=prepared.source_instance_id,
            source_object_id=prepared.source_object_id,
            source_version=prepared.source_version,
            event_type=prepared.event_type,
            raw_content_sha256=raw_content_sha256,
            acl_ref=prepared.acl_ref,
            visibility_partition_sha256=prepared.visibility_partition_sha256,
            mutation_id=prepared.mutation_id,
        )
        event_id = derive_source_event_id(identity)
        prefix = "source-event-v2:"
        if not event_id.startswith(prefix):
            raise AssertionError("reviewed source event ID prefix is missing")
        return _PreparedEvent(
            identity=identity,
            event_id=event_id,
            idempotency_sha256="sha256:" + event_id.removeprefix(prefix),
            raw_object_id=prepared.raw_object_id,
            event_time=prepared.event_time,
            observed_at=prepared.observed_at,
            trace_id=prepared.trace_id,
            schema_version=prepared.schema_version,
            status=prepared.status,
            metadata_json=prepared.metadata_json,
            created_at=prepared.created_at,
        )

    @staticmethod
    def _expected_event_values(prepared: _PreparedEvent) -> dict[str, object]:
        identity = prepared.identity
        return {
            "event_id": prepared.event_id,
            "idempotency_sha256": prepared.idempotency_sha256,
            "project_id": identity.project_id,
            "source_domain": identity.source_domain.value,
            "source_type": identity.source_type,
            "source_instance_id": identity.source_instance_id,
            "event_type": identity.event_type,
            "mutation_id": identity.mutation_id,
            "source_object_id": identity.source_object_id,
            "source_version": identity.source_version,
            "raw_object_id": prepared.raw_object_id,
            "raw_content_sha256": identity.raw_content_sha256,
            "acl_ref": identity.acl_ref,
            "visibility_partition_sha256": identity.visibility_partition_sha256,
            "event_time": prepared.event_time,
            "observed_at": prepared.observed_at,
            "trace_id": prepared.trace_id,
            "schema_version": prepared.schema_version,
            "status": prepared.status,
            "metadata_json": prepared.metadata_json,
        }

    def _fetch_events(self, prepared: _PreparedEvent) -> list[dict[str, object]]:
        rows = self._connection.execute(
            f"""SELECT {_EVENT_SELECT} FROM source_events_v2
                WHERE event_id=? OR idempotency_sha256=?
                ORDER BY event_id""",
            (prepared.event_id, prepared.idempotency_sha256),
        ).fetchall()
        return [dict(zip(_EVENT_COLUMNS, tuple(row), strict=True)) for row in rows]

    def _exact_existing_event(self, prepared: _PreparedEvent) -> bool:
        rows = self._fetch_events(prepared)
        if not rows:
            return False
        if len(rows) != 1:
            _fail(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "multiple rows claim one event identity",
            )
        row = rows[0]
        expected = self._expected_event_values(prepared)
        if any(row[key] != value for key, value in expected.items()):
            _fail(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "stored event differs from exact request payload",
            )
        try:
            created_at = row["created_at"]
            if type(created_at) is not str:
                raise ValueError("created_at is missing")
            verify_canonical_timestamp(created_at)
        except (RawV2ContractError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "stored event has invalid creation time",
            ) from error
        return True

    def _insert_event(self, prepared: _PreparedEvent) -> None:
        values = self._expected_event_values(prepared)
        values["created_at"] = prepared.created_at
        placeholders = ", ".join("?" for _ in _EVENT_COLUMNS)
        self._connection.execute(
            f"INSERT INTO source_events_v2({_EVENT_SELECT}) VALUES ({placeholders})",
            tuple(values[column] for column in _EVENT_COLUMNS),
        )


class RawV2BindingStore:
    """Persist and exactly resolve active project/generation raw bindings."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        policy: RawV2ProjectPolicyAdapter,
        clock: Callable[[], str],
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be an open sqlite3.Connection")
        if not isinstance(policy, RawV2ProjectPolicyAdapter):
            raise TypeError("policy must be a RawV2ProjectPolicyAdapter")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._connection = connection
        self._policy = policy
        self._clock = clock

    def persist(
        self,
        *,
        request: RawBindingWriteRequest,
        selected_content: bytes,
    ) -> RawBindingRecord:
        """Persist one exact active binding without retaining selected bytes."""

        prepared_input = self._prepare_input(
            request=request,
            selected_content=selected_content,
        )
        self._require_write_ready()
        prepared: _PreparedBinding | None = None
        created = False
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            source_domain, raw_content_sha256 = self._resolve_binding_object(prepared_input)
            self._require_entity_unblocked(
                project_id=prepared_input.project_id,
                derived_entity_id=prepared_input.derived_entity_id,
            )
            prepared = self._prepare_binding(
                prepared_input,
                source_domain=source_domain,
                raw_content_sha256=raw_content_sha256,
            )
            if not self._exact_existing_binding(prepared):
                self._insert_binding(prepared)
                created = True
            record = self._exact_existing_binding(prepared)
            if record is None:
                raise AssertionError("persisted binding cannot be read back exactly")
            self._connection.commit()
        except RawV2ContractError:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        except sqlite3.Error as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "raw V2 binding transaction failed",
            ) from error
        except Exception:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        if prepared is None:
            raise AssertionError("binding preparation must precede a successful commit")
        return RawBindingRecord(
            locator_id=prepared.locator_id,
            binding_sha256=prepared.binding_sha256,
            project_id=prepared.identity.project_id,
            raw_object_id=prepared.identity.raw_object_id,
            generation_id=prepared.identity.generation_id,
            derived_entity_id=prepared.identity.derived_entity_id,
            retrieval_unit_id=prepared.identity.retrieval_unit_id,
            selector_sha256=prepared.identity.selector_sha256,
            raw_content_sha256=prepared.identity.raw_content_sha256,
            selected_content_sha256=prepared.identity.selected_content_sha256,
            created=created,
        )

    def resolve(
        self,
        *,
        request: RawBindingResolveRequest,
    ) -> RawBindingRecord | None:
        """Resolve zero or one exact active binding; never choose a latest row."""

        filters = self._prepare_resolve_request(request)
        self._require_read_ready()
        rows = self._select_active_rows(filters)
        if not rows:
            return None
        if len(rows) != 1:
            _fail(
                RawV2Reason.BINDING_AMBIGUOUS,
                "binding filters identify more than one active binding",
            )
        return self._record_from_row(rows[0])

    def recheck_before_output(self, *, record: RawBindingRecord) -> RawBindingRecord:
        """Freshly re-resolve one locator immediately before selected bytes escape."""

        if type(record) is not RawBindingRecord:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "binding record type is not frozen")
        if self._connection.in_transaction:
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "output recheck requires a fresh non-transactional snapshot",
            )
        resolved = self.resolve(
            request=RawBindingResolveRequest(
                project_id=record.project_id,
                locator_id=record.locator_id,
            )
        )
        if resolved is None:
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "binding is no longer active immediately before output",
            )
        authority_fields = (
            "locator_id",
            "binding_sha256",
            "project_id",
            "raw_object_id",
            "generation_id",
            "derived_entity_id",
            "retrieval_unit_id",
            "selector_sha256",
            "raw_content_sha256",
            "selected_content_sha256",
        )
        if any(getattr(resolved, field) != getattr(record, field) for field in authority_fields):
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "binding identity changed before selected output",
            )
        return resolved

    def list_active(
        self,
        *,
        project_id: str,
        derived_entity_id: str,
        generation_id: str,
    ) -> tuple[RawBindingRecord, ...]:
        """List exact active entity/generation bindings in locator order."""

        filters = {
            "project_id": _authority_text(
                project_id,
                limit=SizeLimit.PORTABLE_ID,
                label="project ID",
            ),
            "derived_entity_id": _authority_text(
                derived_entity_id,
                limit=SizeLimit.PORTABLE_ID,
                label="derived entity ID",
            ),
            "generation_id": _authority_text(
                generation_id,
                limit=SizeLimit.PORTABLE_ID,
                label="generation ID",
            ),
        }
        self._require_read_ready()
        rows = self._select_active_rows(filters)
        return tuple(self._record_from_row(row) for row in rows)

    def _require_write_ready(self) -> None:
        if self._connection.in_transaction:
            _fail(RawV2Reason.RAW_STATE_INVALID, "binding write requires no caller transaction")
        self._connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = self._connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or int(foreign_keys[0]) != 1:
            _fail(RawV2Reason.RAW_STATE_INVALID, "SQLite foreign keys are unavailable")
        self._require_read_ready()

    def _require_read_ready(self) -> None:
        try:
            inspect_raw_v2_schema(self._connection)
        except RawV2SchemaError as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "raw V2 schema is not exact",
            ) from error

    def _prepare_input(
        self,
        *,
        request: RawBindingWriteRequest,
        selected_content: bytes,
    ) -> _PreparedBindingInput:
        if type(request) is not RawBindingWriteRequest:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "request type is not frozen")
        if type(selected_content) is not bytes:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "selected content must be exact bytes",
            )
        ensure_utf8_size(
            selected_content,
            SizeLimit.SELECTED_OUTPUT,
            label="selected content",
        )
        if type(request.selector) is not dict:
            _fail(RawV2Reason.SELECTOR_INVALID, "selector must be an exact JSON object")
        try:
            selector = RawSelectorPayload(
                selector_kind=request.selector_kind,
                selector=request.selector,
            )
        except RawV2ContractError:
            raise
        except (TypeError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "selector payload is not canonical",
            ) from error
        visibility = self._policy.resolve_object_visibility(
            project_id=request.project_id,
            source_acl_ref=request.source_acl_ref,
        )
        parser_digest = (
            None
            if request.parser_artifact_sha256 is None
            else validate_sha256_digest(request.parser_artifact_sha256)
        )
        valid_from = None if request.valid_from is None else canonical_timestamp(request.valid_from)
        valid_to = None if request.valid_to is None else canonical_timestamp(request.valid_to)
        return _PreparedBindingInput(
            project_id=visibility.project_id,
            raw_object_id=validate_raw_object_id(request.raw_object_id),
            acl_ref=visibility.acl_ref,
            visibility_partition_sha256=visibility.visibility_partition_sha256,
            source_version=_authority_text(
                request.source_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="source version",
            ),
            stable_version=_authority_text(
                request.stable_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="stable version",
            ),
            generation_id=_authority_text(
                request.generation_id,
                limit=SizeLimit.PORTABLE_ID,
                label="generation ID",
            ),
            derived_entity_id=_authority_text(
                request.derived_entity_id,
                limit=SizeLimit.PORTABLE_ID,
                label="derived entity ID",
            ),
            retrieval_unit_id=_authority_text(
                request.retrieval_unit_id,
                limit=SizeLimit.PORTABLE_ID,
                label="retrieval unit ID",
            ),
            derivation_kind=_authority_text(
                request.derivation_kind,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="derivation kind",
            ),
            derivation_version=_authority_text(
                request.derivation_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="derivation version",
            ),
            selector=selector,
            selector_sha256=derive_selector_sha256(selector),
            selector_json=selector.canonical_json_bytes().decode("utf-8"),
            selected_content_sha256=exact_bytes_sha256(selected_content),
            parser_artifact_sha256=parser_digest,
            valid_from=valid_from,
            valid_to=valid_to,
            observed_at=canonical_timestamp(request.observed_at),
            adapter_version=_authority_text(
                request.adapter_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="adapter version",
            ),
            schema_version=_authority_text(
                request.schema_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="binding schema version",
            ),
            created_at=canonical_timestamp(self._clock()),
        )

    def _resolve_binding_object(
        self,
        prepared: _PreparedBindingInput,
    ) -> tuple[SourceDomain, str]:
        columns = (
            "project_id",
            "source_domain",
            "source_version",
            "stable_version",
            "acl_ref",
            "visibility_partition_sha256",
            "raw_content_sha256",
            "blob_sha256",
            "byte_length",
            "state",
            "tombstoned_at",
        )
        row = self._connection.execute(
            f"""SELECT {", ".join(columns)} FROM raw_objects_v2
                WHERE project_id=? AND raw_object_id=?""",
            (prepared.project_id, prepared.raw_object_id),
        ).fetchone()
        if row is None:
            _fail(RawV2Reason.RAW_STATE_INVALID, "binding raw object is unavailable in project")
        stored = dict(zip(columns, tuple(row), strict=True))
        if stored["state"] == "reference_only":
            _fail(
                RawV2Reason.REFERENCE_ONLY_UNAVAILABLE,
                "reference-only object cannot become raw evidence binding",
            )
        if stored["state"] != "active":
            _fail(RawV2Reason.RAW_STATE_INVALID, "binding requires an active raw object")
        if stored["tombstoned_at"] is not None:
            _fail(RawV2Reason.RAW_STATE_INVALID, "active raw object has a tombstone marker")
        expected = {
            "project_id": prepared.project_id,
            "source_version": prepared.source_version,
            "stable_version": prepared.stable_version,
            "acl_ref": prepared.acl_ref,
            "visibility_partition_sha256": prepared.visibility_partition_sha256,
        }
        if any(stored[key] != value for key, value in expected.items()):
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "binding raw object authority differs from request",
            )
        try:
            source_domain = SourceDomain(stored["source_domain"])
            raw_digest = validate_sha256_digest(stored["raw_content_sha256"])
        except (RawV2ContractError, TypeError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "active raw object has malformed identity",
            ) from error
        if (
            stored["blob_sha256"] != raw_digest
            or type(stored["byte_length"]) is not int
            or stored["byte_length"] < 0
        ):
            _fail(RawV2Reason.RAW_STATE_INVALID, "active raw object byte identity is invalid")
        self._verify_blob_metadata(
            digest=raw_digest,
            byte_length=stored["byte_length"],
            failure_reason=RawV2Reason.RAW_STATE_INVALID,
        )
        return source_domain, raw_digest

    def _require_entity_unblocked(
        self,
        *,
        project_id: str,
        derived_entity_id: str,
    ) -> None:
        row = self._connection.execute(
            """SELECT 1 FROM blocked_entities_v2
               WHERE project_id=? AND entity_id=?""",
            (project_id, derived_entity_id),
        ).fetchone()
        if row is not None:
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "binding entity is blocked in this project",
            )

    @staticmethod
    def _prepare_binding(
        prepared: _PreparedBindingInput,
        *,
        source_domain: SourceDomain,
        raw_content_sha256: str,
    ) -> _PreparedBinding:
        try:
            identity = RawEvidenceBindingPayload(
                project_id=prepared.project_id,
                source_domain=source_domain,
                raw_object_id=prepared.raw_object_id,
                source_version=prepared.source_version,
                stable_version=prepared.stable_version,
                generation_id=prepared.generation_id,
                derived_entity_id=prepared.derived_entity_id,
                retrieval_unit_id=prepared.retrieval_unit_id,
                derivation_kind=prepared.derivation_kind,
                derivation_version=prepared.derivation_version,
                selector_sha256=prepared.selector_sha256,
                raw_content_sha256=raw_content_sha256,
                selected_content_sha256=prepared.selected_content_sha256,
                parser_artifact_sha256=prepared.parser_artifact_sha256,
                valid_from=prepared.valid_from,
                valid_to=prepared.valid_to,
                observed_at=prepared.observed_at,
                acl_ref=prepared.acl_ref,
                visibility_partition_sha256=prepared.visibility_partition_sha256,
                adapter_version=prepared.adapter_version,
                schema_version=prepared.schema_version,
            )
        except RawV2ContractError:
            raise
        except (TypeError, ValueError) as error:
            raise RawV2ContractError(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "binding identity is not canonical",
            ) from error
        locator_id = derive_locator_id(identity)
        prefix = "raw-locator-v2:"
        if not locator_id.startswith(prefix):
            raise AssertionError("reviewed raw locator ID prefix is missing")
        return _PreparedBinding(
            identity=identity,
            locator_id=locator_id,
            binding_sha256="sha256:" + locator_id.removeprefix(prefix),
            selector_kind=prepared.selector.selector_kind,
            selector_json=prepared.selector_json,
            binding_json=identity.canonical_json_bytes().decode("utf-8"),
            created_at=prepared.created_at,
        )

    @staticmethod
    def _expected_binding_values(prepared: _PreparedBinding) -> dict[str, object]:
        identity = prepared.identity
        return {
            "locator_id": prepared.locator_id,
            "binding_sha256": prepared.binding_sha256,
            "project_id": identity.project_id,
            "source_domain": identity.source_domain.value,
            "raw_object_id": identity.raw_object_id,
            "source_version": identity.source_version,
            "stable_version": identity.stable_version,
            "generation_id": identity.generation_id,
            "derived_entity_id": identity.derived_entity_id,
            "retrieval_unit_id": identity.retrieval_unit_id,
            "derivation_kind": identity.derivation_kind,
            "derivation_version": identity.derivation_version,
            "selector_kind": prepared.selector_kind,
            "selector_json": prepared.selector_json,
            "selector_sha256": identity.selector_sha256,
            "raw_content_sha256": identity.raw_content_sha256,
            "selected_content_sha256": identity.selected_content_sha256,
            "parser_artifact_sha256": identity.parser_artifact_sha256,
            "acl_ref": identity.acl_ref,
            "visibility_partition_sha256": identity.visibility_partition_sha256,
            "valid_from": identity.valid_from,
            "valid_to": identity.valid_to,
            "observed_at": identity.observed_at,
            "binding_json": prepared.binding_json,
            "invalidated_at": None,
            "invalidation_reason": None,
        }

    def _fetch_bindings(self, prepared: _PreparedBinding) -> list[dict[str, object]]:
        rows = self._connection.execute(
            f"""SELECT {_BINDING_SELECT} FROM raw_evidence_bindings_v2
                WHERE locator_id=? OR binding_sha256=?
                ORDER BY locator_id""",
            (prepared.locator_id, prepared.binding_sha256),
        ).fetchall()
        return [dict(zip(_BINDING_COLUMNS, tuple(row), strict=True)) for row in rows]

    def _exact_existing_binding(
        self,
        prepared: _PreparedBinding,
    ) -> RawBindingRecord | None:
        rows = self._fetch_bindings(prepared)
        if not rows:
            return None
        if len(rows) != 1:
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "multiple rows claim one binding identity",
            )
        row = rows[0]
        expected = self._expected_binding_values(prepared)
        if any(row[key] != value for key, value in expected.items()):
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "stored binding differs from exact request payload",
            )
        return self._record_from_row(row)

    def _insert_binding(self, prepared: _PreparedBinding) -> None:
        values = self._expected_binding_values(prepared)
        values["created_at"] = prepared.created_at
        placeholders = ", ".join("?" for _ in _BINDING_COLUMNS)
        self._connection.execute(
            f"INSERT INTO raw_evidence_bindings_v2({_BINDING_SELECT}) VALUES ({placeholders})",
            tuple(values[column] for column in _BINDING_COLUMNS),
        )

    @staticmethod
    def _prepare_resolve_request(
        request: RawBindingResolveRequest,
    ) -> dict[str, str]:
        if type(request) is not RawBindingResolveRequest:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "resolve request type is not frozen")
        filters = {
            "project_id": _authority_text(
                request.project_id,
                limit=SizeLimit.PORTABLE_ID,
                label="project ID",
            )
        }
        if request.locator_id is not None:
            filters["locator_id"] = validate_locator_id(request.locator_id)
        if request.derived_entity_id is not None:
            filters["derived_entity_id"] = _authority_text(
                request.derived_entity_id,
                limit=SizeLimit.PORTABLE_ID,
                label="derived entity ID",
            )
        if request.retrieval_unit_id is not None:
            filters["retrieval_unit_id"] = _authority_text(
                request.retrieval_unit_id,
                limit=SizeLimit.PORTABLE_ID,
                label="retrieval unit ID",
            )
        if request.generation_id is not None:
            filters["generation_id"] = _authority_text(
                request.generation_id,
                limit=SizeLimit.PORTABLE_ID,
                label="generation ID",
            )
        if not any(
            key in filters for key in ("locator_id", "derived_entity_id", "retrieval_unit_id")
        ):
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "binding resolve requires locator, entity, or retrieval unit",
            )
        if "locator_id" not in filters and "generation_id" not in filters:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "entity or unit binding resolve requires generation",
            )
        return filters

    def _select_active_rows(
        self,
        filters: dict[str, str],
    ) -> list[dict[str, object]]:
        clauses = [
            "project_id=?",
            "invalidated_at IS NULL",
            "NOT EXISTS (SELECT 1 FROM blocked_entities_v2 AS blocked "
            "WHERE blocked.project_id=raw_evidence_bindings_v2.project_id "
            "AND blocked.entity_id=raw_evidence_bindings_v2.derived_entity_id)",
        ]
        values: list[str] = [filters["project_id"]]
        for column in (
            "locator_id",
            "derived_entity_id",
            "retrieval_unit_id",
            "generation_id",
        ):
            if column in filters:
                clauses.append(f"{column}=?")
                values.append(filters[column])
        rows = self._connection.execute(
            f"""SELECT {_BINDING_SELECT} FROM raw_evidence_bindings_v2
                WHERE {" AND ".join(clauses)}
                ORDER BY locator_id""",
            tuple(values),
        ).fetchall()
        return [dict(zip(_BINDING_COLUMNS, tuple(row), strict=True)) for row in rows]

    def _record_from_row(self, row: dict[str, object]) -> RawBindingRecord:
        try:
            if row["invalidated_at"] is not None or row["invalidation_reason"] is not None:
                raise ValueError("active binding has invalidation state")
            selector_json = row["selector_json"]
            binding_json = row["binding_json"]
            if type(selector_json) is not str or type(binding_json) is not str:
                raise ValueError("binding canonical JSON is missing")
            verify_canonical_json_bytes(selector_json.encode("utf-8"), allow_none=True)
            verify_canonical_json_bytes(binding_json.encode("utf-8"), allow_none=True)
            selector = RawSelectorPayload.model_validate_json(selector_json)
            identity = RawEvidenceBindingPayload.model_validate_json(binding_json)
            if selector.canonical_json_bytes().decode("utf-8") != selector_json:
                raise ValueError("selector JSON is not exact canonical payload")
            if identity.canonical_json_bytes().decode("utf-8") != binding_json:
                raise ValueError("binding JSON is not exact canonical payload")
            locator_id = validate_locator_id(row["locator_id"])
            binding_sha256 = validate_sha256_digest(row["binding_sha256"])
            expected_locator = derive_locator_id(identity)
            expected_binding_sha256 = "sha256:" + expected_locator.removeprefix("raw-locator-v2:")
            if locator_id != expected_locator or binding_sha256 != expected_binding_sha256:
                raise ValueError("binding locator/digest does not match canonical identity")
            if row["selector_kind"] != selector.selector_kind:
                raise ValueError("selector kind differs from canonical selector")
            selector_sha256 = derive_selector_sha256(selector)
            if row["selector_sha256"] != selector_sha256:
                raise ValueError("selector digest differs from canonical selector")
            expected = {
                "project_id": identity.project_id,
                "source_domain": identity.source_domain.value,
                "raw_object_id": identity.raw_object_id,
                "source_version": identity.source_version,
                "stable_version": identity.stable_version,
                "generation_id": identity.generation_id,
                "derived_entity_id": identity.derived_entity_id,
                "retrieval_unit_id": identity.retrieval_unit_id,
                "derivation_kind": identity.derivation_kind,
                "derivation_version": identity.derivation_version,
                "selector_sha256": identity.selector_sha256,
                "raw_content_sha256": identity.raw_content_sha256,
                "selected_content_sha256": identity.selected_content_sha256,
                "parser_artifact_sha256": identity.parser_artifact_sha256,
                "acl_ref": identity.acl_ref,
                "visibility_partition_sha256": identity.visibility_partition_sha256,
                "valid_from": identity.valid_from,
                "valid_to": identity.valid_to,
                "observed_at": identity.observed_at,
            }
            if any(row[key] != value for key, value in expected.items()):
                raise ValueError("binding row differs from canonical identity")
            created_at = row["created_at"]
            if type(created_at) is not str:
                raise ValueError("binding creation time is missing")
            verify_canonical_timestamp(created_at)
            self._verify_record_object(row)
        except Exception as error:
            if (
                isinstance(error, RawV2ContractError)
                and error.reason is RawV2Reason.BINDING_DIGEST_MISMATCH
            ):
                raise
            raise RawV2ContractError(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "stored binding identity failed exact verification",
            ) from error
        return RawBindingRecord(
            locator_id=locator_id,
            binding_sha256=binding_sha256,
            project_id=identity.project_id,
            raw_object_id=identity.raw_object_id,
            generation_id=identity.generation_id,
            derived_entity_id=identity.derived_entity_id,
            retrieval_unit_id=identity.retrieval_unit_id,
            selector_sha256=identity.selector_sha256,
            raw_content_sha256=identity.raw_content_sha256,
            selected_content_sha256=identity.selected_content_sha256,
            created=False,
        )

    def _verify_record_object(self, binding: dict[str, object]) -> None:
        columns = (
            "source_domain",
            "source_version",
            "stable_version",
            "acl_ref",
            "visibility_partition_sha256",
            "raw_content_sha256",
            "blob_sha256",
            "byte_length",
            "state",
            "tombstoned_at",
        )
        row = self._connection.execute(
            f"""SELECT {", ".join(columns)} FROM raw_objects_v2
                WHERE project_id=? AND raw_object_id=?""",
            (binding["project_id"], binding["raw_object_id"]),
        ).fetchone()
        if row is None:
            _fail(RawV2Reason.BINDING_DIGEST_MISMATCH, "binding raw object is unavailable")
        stored = dict(zip(columns, tuple(row), strict=True))
        expected = {
            "source_domain": binding["source_domain"],
            "source_version": binding["source_version"],
            "stable_version": binding["stable_version"],
            "acl_ref": binding["acl_ref"],
            "visibility_partition_sha256": binding["visibility_partition_sha256"],
            "raw_content_sha256": binding["raw_content_sha256"],
            "blob_sha256": binding["raw_content_sha256"],
            "state": "active",
            "tombstoned_at": None,
        }
        if any(stored[key] != value for key, value in expected.items()):
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "binding raw object no longer matches stored identity",
            )
        if type(stored["byte_length"]) is not int or stored["byte_length"] < 0:
            _fail(
                RawV2Reason.BINDING_DIGEST_MISMATCH,
                "binding raw object byte length is invalid",
            )
        self._verify_blob_metadata(
            digest=binding["raw_content_sha256"],
            byte_length=stored["byte_length"],
            failure_reason=RawV2Reason.BINDING_DIGEST_MISMATCH,
        )

    def _verify_blob_metadata(
        self,
        *,
        digest: object,
        byte_length: object,
        failure_reason: RawV2Reason,
    ) -> None:
        row = self._connection.execute(
            """SELECT blob_sha256, byte_length, storage_key, storage_state,
                      first_verified_at, last_verified_at, created_at
               FROM raw_blobs_v2 WHERE blob_sha256=?""",
            (digest,),
        ).fetchone()
        if row is None:
            _fail(failure_reason, "binding blob metadata is unavailable")
        blob_digest, stored_length, storage_key, storage_state, *timestamps = tuple(row)
        if (
            blob_digest != digest
            or stored_length != byte_length
            or type(storage_key) is not str
            or not storage_key
            or storage_state != "available"
        ):
            _fail(failure_reason, "binding blob metadata is not available and exact")
        try:
            for timestamp in timestamps:
                if type(timestamp) is not str:
                    raise ValueError("blob verification timestamp is missing")
                verify_canonical_timestamp(timestamp)
        except (RawV2ContractError, ValueError) as error:
            raise RawV2ContractError(
                failure_reason,
                "binding blob verification timestamps are invalid",
            ) from error


class RawV2TombstoneStore:
    """Atomically revoke one project object and all of its active bindings."""

    _MANIFEST_KEYS = frozenset(
        {
            "actor_digest",
            "reason_code",
            "invalidated_binding_count",
            "invalidated_locator_set_sha256",
            "blocked_entity_count",
            "blocked_entity_set_sha256",
        }
    )

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        policy: RawV2ProjectPolicyAdapter,
        clock: Callable[[], str],
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be an open sqlite3.Connection")
        if not isinstance(policy, RawV2ProjectPolicyAdapter):
            raise TypeError("policy must be a RawV2ProjectPolicyAdapter")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._connection = connection
        self._policy = policy
        self._clock = clock

    def tombstone(self, *, request: RawTombstoneRequest) -> RawTombstoneRecord:
        """Commit one exact tombstone, propagation set, block set, and audit event."""

        prepared = self._prepare_input(request)
        self._require_write_ready()
        event_store = RawV2EventStore(
            connection=self._connection,
            policy=self._policy,
            clock=self._clock,
        )
        result: RawTombstoneRecord | None = None
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            stored_object = self._fetch_and_verify_object(prepared)
            raw_content_sha256 = stored_object["raw_content_sha256"]
            if raw_content_sha256 is not None and type(raw_content_sha256) is not str:
                raise AssertionError("verified content digest must be exact str or None")

            base_event = self._prepare_event(
                prepared,
                stored_object=stored_object,
                raw_content_sha256=raw_content_sha256,
                metadata_json="{}",
            )
            event_rows = event_store._fetch_events(base_event)
            if event_rows:
                if len(event_rows) != 1:
                    _fail(
                        RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                        "multiple rows claim one tombstone event identity",
                    )
                metadata_json = event_rows[0]["metadata_json"]
                manifest = self._parse_manifest(
                    metadata_json,
                    prepared=prepared,
                )
                exact_event = self._prepare_event(
                    prepared,
                    stored_object=stored_object,
                    raw_content_sha256=raw_content_sha256,
                    metadata_json=manifest.metadata_json,
                )
                if not event_store._exact_existing_event(exact_event):
                    raise AssertionError("fetched tombstone event disappeared in transaction")
                self._verify_final_state(
                    prepared,
                    stored_object=stored_object,
                    event=exact_event,
                    event_store=event_store,
                    manifest=manifest,
                )
                result = self._record(
                    prepared,
                    event=exact_event,
                    manifest=manifest,
                    created=False,
                )
                self._connection.commit()
            else:
                if stored_object["state"] == "tombstoned":
                    _fail(
                        RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                        "object was tombstoned by a different exact request",
                    )
                active_bindings = self._active_binding_identities(prepared)
                locator_ids = [locator_id for locator_id, _entity_id in active_bindings]
                entity_ids = sorted({entity_id for _locator_id, entity_id in active_bindings})
                manifest = self._build_manifest(
                    prepared,
                    locator_ids=locator_ids,
                    entity_ids=entity_ids,
                )
                exact_event = self._prepare_event(
                    prepared,
                    stored_object=stored_object,
                    raw_content_sha256=raw_content_sha256,
                    metadata_json=manifest.metadata_json,
                )
                self._apply_tombstone(
                    prepared,
                    prior_state=str(stored_object["state"]),
                    active_bindings=active_bindings,
                    entity_ids=entity_ids,
                )
                event_store._insert_event(exact_event)
                final_object = self._fetch_and_verify_object(prepared)
                self._verify_final_state(
                    prepared,
                    stored_object=final_object,
                    event=exact_event,
                    event_store=event_store,
                    manifest=manifest,
                )
                result = self._record(
                    prepared,
                    event=exact_event,
                    manifest=manifest,
                    created=True,
                )
                self._connection.commit()
        except RawV2ContractError:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        except sqlite3.Error as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "raw V2 tombstone transaction failed",
            ) from error
        except Exception:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        if result is None:
            raise AssertionError("tombstone result must precede a successful commit")
        return result

    def is_entity_blocked(self, *, project_id: str, entity_id: str) -> bool:
        """Return only whether one exact entity is blocked in one project."""

        project = _authority_text(
            project_id,
            limit=SizeLimit.PORTABLE_ID,
            label="project ID",
        )
        entity = _authority_text(
            entity_id,
            limit=SizeLimit.PORTABLE_ID,
            label="entity ID",
        )
        self._require_read_ready()
        return (
            self._connection.execute(
                """SELECT 1 FROM blocked_entities_v2
                   WHERE project_id=? AND entity_id=?""",
                (project, entity),
            ).fetchone()
            is not None
        )

    def count_blocked(self, *, project_id: str) -> int:
        """Count blocked entities for exactly one project."""

        project = _authority_text(
            project_id,
            limit=SizeLimit.PORTABLE_ID,
            label="project ID",
        )
        self._require_read_ready()
        row = self._connection.execute(
            "SELECT COUNT(*) FROM blocked_entities_v2 WHERE project_id=?",
            (project,),
        ).fetchone()
        if row is None or type(row[0]) is not int or row[0] < 0:
            _fail(RawV2Reason.RAW_STATE_INVALID, "blocked entity count is malformed")
        return row[0]

    def _prepare_input(self, request: RawTombstoneRequest) -> _PreparedTombstoneInput:
        if type(request) is not RawTombstoneRequest:
            _fail(RawV2Reason.CANONICALIZATION_MISMATCH, "request type is not frozen")
        visibility = self._policy.resolve_object_visibility(
            project_id=request.project_id,
            source_acl_ref=request.source_acl_ref,
        )
        return _PreparedTombstoneInput(
            project_id=visibility.project_id,
            raw_object_id=validate_raw_object_id(request.raw_object_id),
            acl_ref=visibility.acl_ref,
            visibility_partition_sha256=visibility.visibility_partition_sha256,
            mutation_id=_authority_text(
                request.mutation_id,
                limit=SizeLimit.TRACE_MUTATION_ID,
                label="mutation ID",
            ),
            reason_code=_authority_text(
                request.reason_code,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="tombstone reason code",
            ),
            actor_digest=validate_sha256_digest(request.actor_digest),
            event_time=(
                None if request.event_time is None else canonical_timestamp(request.event_time)
            ),
            observed_at=canonical_timestamp(request.observed_at),
            trace_id=_authority_text(
                request.trace_id,
                limit=SizeLimit.TRACE_MUTATION_ID,
                label="trace ID",
            ),
            schema_version=_authority_text(
                request.schema_version,
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="tombstone schema version",
            ),
            created_at=canonical_timestamp(self._clock()),
        )

    def _require_write_ready(self) -> None:
        if self._connection.in_transaction:
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "tombstone write requires no caller transaction",
            )
        self._connection.execute("PRAGMA foreign_keys = ON")
        foreign_keys = self._connection.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or int(foreign_keys[0]) != 1:
            _fail(RawV2Reason.RAW_STATE_INVALID, "SQLite foreign keys are unavailable")
        self._require_read_ready()

    def _require_read_ready(self) -> None:
        try:
            inspect_raw_v2_schema(self._connection)
        except RawV2SchemaError as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "raw V2 schema is not exact",
            ) from error

    def _fetch_and_verify_object(
        self,
        prepared: _PreparedTombstoneInput,
    ) -> dict[str, object]:
        row = self._connection.execute(
            f"""SELECT {_OBJECT_SELECT} FROM raw_objects_v2
                WHERE project_id=? AND raw_object_id=?""",
            (prepared.project_id, prepared.raw_object_id),
        ).fetchone()
        if row is None:
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "tombstone raw object is unavailable in project",
            )
        stored = dict(zip(_OBJECT_COLUMNS, tuple(row), strict=True))
        expected_authority = {
            "raw_object_id": prepared.raw_object_id,
            "project_id": prepared.project_id,
            "acl_ref": prepared.acl_ref,
            "visibility_partition_sha256": prepared.visibility_partition_sha256,
        }
        if any(stored[key] != value for key, value in expected_authority.items()):
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "tombstone raw object authority differs from request",
            )
        try:
            source_domain = SourceDomain(stored["source_domain"])
            source_type = _authority_text(
                stored["source_type"],
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="stored source type",
            )
            source_instance_id = _authority_text(
                stored["source_instance_id"],
                limit=SizeLimit.PORTABLE_ID,
                label="stored source instance ID",
            )
            source_object_id = _authority_text(
                stored["source_object_id"],
                limit=SizeLimit.SOURCE_OBJECT_ID,
                label="stored source object ID",
            )
            source_version = _authority_text(
                stored["source_version"],
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="stored source version",
            )
            stable_version = _authority_text(
                stored["stable_version"],
                limit=SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA,
                label="stored stable version",
            )
            raw_content_sha256 = (
                None
                if stored["raw_content_sha256"] is None
                else validate_sha256_digest(stored["raw_content_sha256"])
            )
            identity = RawLogicalIdentityPayload(
                project_id=prepared.project_id,
                source_domain=source_domain,
                source_type=source_type,
                source_instance_id=source_instance_id,
                source_object_id=source_object_id,
                source_version=source_version,
                stable_version=stable_version,
                raw_content_sha256=raw_content_sha256,
                acl_ref=prepared.acl_ref,
                visibility_partition_sha256=prepared.visibility_partition_sha256,
            )
            if derive_raw_object_id(identity) != prepared.raw_object_id:
                raise ValueError("stored logical identity does not derive raw object ID")
            if validate_sha256_digest(stored["logical_identity_sha256"]) != _logical_digest(
                prepared.raw_object_id
            ):
                raise ValueError("stored logical digest differs from raw object ID")
            verify_canonical_timestamp(stored["observed_at"])
            verify_canonical_timestamp(stored["created_at"])
            metadata_json = stored["metadata_json"]
            if type(metadata_json) is not str:
                raise ValueError("stored object metadata is missing")
            verify_canonical_json_bytes(metadata_json, allow_none=True)
        except Exception as error:
            if (
                isinstance(error, RawV2ContractError)
                and error.reason is RawV2Reason.RAW_STATE_INVALID
            ):
                raise
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "tombstone raw object has malformed canonical authority",
            ) from error

        state = stored["state"]
        if type(state) is not str or state not in {
            "active",
            "reference_only",
            "quarantined",
            "tombstoned",
            "corrupt",
        }:
            _fail(RawV2Reason.RAW_STATE_INVALID, "raw object state is malformed")
        blob_digest = stored["blob_sha256"]
        byte_length = stored["byte_length"]
        if raw_content_sha256 is None:
            if blob_digest is not None or byte_length is not None:
                _fail(
                    RawV2Reason.RAW_STATE_INVALID,
                    "raw object has partial byte identity",
                )
        else:
            if blob_digest is not None and blob_digest != raw_content_sha256:
                _fail(
                    RawV2Reason.RAW_STATE_INVALID,
                    "raw object blob/content digests differ",
                )
            if byte_length is not None and (type(byte_length) is not int or byte_length < 0):
                _fail(RawV2Reason.RAW_STATE_INVALID, "raw object byte length is malformed")
        if state == "active" and (
            raw_content_sha256 is None
            or blob_digest != raw_content_sha256
            or type(byte_length) is not int
            or byte_length < 0
        ):
            _fail(RawV2Reason.RAW_STATE_INVALID, "active raw object byte identity is incomplete")
        if state == "reference_only" and (
            raw_content_sha256 is not None or blob_digest is not None or byte_length is not None
        ):
            _fail(RawV2Reason.RAW_STATE_INVALID, "reference-only object has byte identity")
        tombstoned_at = stored["tombstoned_at"]
        if state == "tombstoned":
            if type(tombstoned_at) is not str:
                _fail(RawV2Reason.RAW_STATE_INVALID, "tombstoned object lacks marker")
            try:
                verify_canonical_timestamp(tombstoned_at)
            except RawV2ContractError as error:
                raise RawV2ContractError(
                    RawV2Reason.RAW_STATE_INVALID,
                    "tombstone marker is not canonical",
                ) from error
        elif tombstoned_at is not None:
            _fail(RawV2Reason.RAW_STATE_INVALID, "live object has a tombstone marker")
        return stored

    @staticmethod
    def _prepare_event(
        prepared: _PreparedTombstoneInput,
        *,
        stored_object: dict[str, object],
        raw_content_sha256: str | None,
        metadata_json: str,
    ) -> _PreparedEvent:
        event_input = _PreparedEventInput(
            project_id=prepared.project_id,
            source_domain=SourceDomain(stored_object["source_domain"]),
            source_type=str(stored_object["source_type"]),
            source_instance_id=str(stored_object["source_instance_id"]),
            event_type="tombstone",
            mutation_id=prepared.mutation_id,
            source_object_id=str(stored_object["source_object_id"]),
            source_version=str(stored_object["source_version"]),
            acl_ref=prepared.acl_ref,
            visibility_partition_sha256=prepared.visibility_partition_sha256,
            raw_object_id=prepared.raw_object_id,
            event_time=prepared.event_time,
            observed_at=prepared.observed_at,
            trace_id=prepared.trace_id,
            schema_version=prepared.schema_version,
            status="tombstone",
            metadata_json=metadata_json,
            created_at=prepared.created_at,
        )
        return RawV2EventStore._prepare_identity(
            event_input,
            raw_content_sha256=raw_content_sha256,
        )

    @staticmethod
    def _set_sha256(values: list[str]) -> str:
        return exact_bytes_sha256(canonical_json_bytes(values))

    def _build_manifest(
        self,
        prepared: _PreparedTombstoneInput,
        *,
        locator_ids: list[str],
        entity_ids: list[str],
    ) -> _TombstoneManifest:
        if locator_ids != sorted(set(locator_ids)):
            raise AssertionError("active locator manifest must be sorted and unique")
        if entity_ids != sorted(set(entity_ids)):
            raise AssertionError("blocked entity manifest must be sorted and unique")
        invalidated_digest = self._set_sha256(locator_ids)
        blocked_digest = self._set_sha256(entity_ids)
        metadata = {
            "actor_digest": prepared.actor_digest,
            "blocked_entity_count": len(entity_ids),
            "blocked_entity_set_sha256": blocked_digest,
            "invalidated_binding_count": len(locator_ids),
            "invalidated_locator_set_sha256": invalidated_digest,
            "reason_code": prepared.reason_code,
        }
        raw = canonical_json_bytes(metadata)
        ensure_utf8_size(raw, SizeLimit.CANONICAL_JSON, label="tombstone metadata")
        return _TombstoneManifest(
            metadata_json=raw.decode("utf-8"),
            invalidated_binding_count=len(locator_ids),
            invalidated_locator_set_sha256=invalidated_digest,
            blocked_entity_count=len(entity_ids),
            blocked_entity_set_sha256=blocked_digest,
        )

    def _parse_manifest(
        self,
        metadata_json: object,
        *,
        prepared: _PreparedTombstoneInput,
    ) -> _TombstoneManifest:
        try:
            if type(metadata_json) is not str:
                raise ValueError("tombstone metadata is missing")
            ensure_utf8_size(
                metadata_json,
                SizeLimit.CANONICAL_JSON,
                label="stored tombstone metadata",
            )
            value = verify_canonical_json_bytes(metadata_json)
            if type(value) is not dict or frozenset(value) != self._MANIFEST_KEYS:
                raise ValueError("tombstone metadata keys differ")
            if value["actor_digest"] != prepared.actor_digest:
                raise ValueError("tombstone actor differs")
            if value["reason_code"] != prepared.reason_code:
                raise ValueError("tombstone reason differs")
            invalidated_count = value["invalidated_binding_count"]
            blocked_count = value["blocked_entity_count"]
            if (
                type(invalidated_count) is not int
                or invalidated_count < 0
                or type(blocked_count) is not int
                or blocked_count < 0
            ):
                raise ValueError("tombstone manifest counts are malformed")
            invalidated_digest = validate_sha256_digest(value["invalidated_locator_set_sha256"])
            blocked_digest = validate_sha256_digest(value["blocked_entity_set_sha256"])
        except Exception as error:
            raise RawV2ContractError(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "stored tombstone manifest differs from exact request",
            ) from error
        return _TombstoneManifest(
            metadata_json=metadata_json,
            invalidated_binding_count=invalidated_count,
            invalidated_locator_set_sha256=invalidated_digest,
            blocked_entity_count=blocked_count,
            blocked_entity_set_sha256=blocked_digest,
        )

    def _active_binding_identities(
        self,
        prepared: _PreparedTombstoneInput,
    ) -> list[tuple[str, str]]:
        rows = self._connection.execute(
            """SELECT locator_id, derived_entity_id, invalidation_reason
               FROM raw_evidence_bindings_v2
               WHERE project_id=? AND raw_object_id=? AND invalidated_at IS NULL
               ORDER BY locator_id""",
            (prepared.project_id, prepared.raw_object_id),
        ).fetchall()
        identities: list[tuple[str, str]] = []
        try:
            for locator_id, entity_id, invalidation_reason in rows:
                if invalidation_reason is not None:
                    raise ValueError("active binding has an invalidation reason")
                identities.append(
                    (
                        validate_locator_id(locator_id),
                        _authority_text(
                            entity_id,
                            limit=SizeLimit.PORTABLE_ID,
                            label="stored derived entity ID",
                        ),
                    )
                )
        except Exception as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "active tombstone binding identity is malformed",
            ) from error
        return identities

    def _apply_tombstone(
        self,
        prepared: _PreparedTombstoneInput,
        *,
        prior_state: str,
        active_bindings: list[tuple[str, str]],
        entity_ids: list[str],
    ) -> None:
        object_cursor = self._connection.execute(
            """UPDATE raw_objects_v2 SET state='tombstoned', tombstoned_at=?
               WHERE project_id=? AND raw_object_id=? AND state=?
                 AND tombstoned_at IS NULL""",
            (
                prepared.observed_at,
                prepared.project_id,
                prepared.raw_object_id,
                prior_state,
            ),
        )
        if object_cursor.rowcount != 1:
            _fail(RawV2Reason.RAW_STATE_INVALID, "raw object tombstone update lost authority")
        binding_cursor = self._connection.execute(
            """UPDATE raw_evidence_bindings_v2 SET invalidated_at=?, invalidation_reason=?
               WHERE project_id=? AND raw_object_id=? AND invalidated_at IS NULL""",
            (
                prepared.observed_at,
                prepared.reason_code,
                prepared.project_id,
                prepared.raw_object_id,
            ),
        )
        if binding_cursor.rowcount != len(active_bindings):
            _fail(
                RawV2Reason.RAW_STATE_INVALID,
                "active binding set changed during tombstone propagation",
            )
        for entity_id in entity_ids:
            self._connection.execute(
                """INSERT INTO blocked_entities_v2(
                       project_id, entity_id, raw_object_id, reason_code,
                       blocked_at, actor_digest
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id, entity_id) DO UPDATE SET
                       raw_object_id=excluded.raw_object_id,
                       reason_code=excluded.reason_code,
                       blocked_at=excluded.blocked_at,
                       actor_digest=excluded.actor_digest""",
                (
                    prepared.project_id,
                    entity_id,
                    prepared.raw_object_id,
                    prepared.reason_code,
                    prepared.observed_at,
                    prepared.actor_digest,
                ),
            )

    def _verify_final_state(
        self,
        prepared: _PreparedTombstoneInput,
        *,
        stored_object: dict[str, object],
        event: _PreparedEvent,
        event_store: RawV2EventStore,
        manifest: _TombstoneManifest,
    ) -> None:
        if (
            stored_object["state"] != "tombstoned"
            or stored_object["tombstoned_at"] != prepared.observed_at
        ):
            _fail(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "object tombstone state differs from exact request",
            )
        active = self._connection.execute(
            """SELECT 1 FROM raw_evidence_bindings_v2
               WHERE project_id=? AND raw_object_id=? AND invalidated_at IS NULL
               LIMIT 1""",
            (prepared.project_id, prepared.raw_object_id),
        ).fetchone()
        if active is not None:
            _fail(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "active binding remains after tombstone propagation",
            )
        invalidated_rows = self._connection.execute(
            """SELECT locator_id FROM raw_evidence_bindings_v2
               WHERE project_id=? AND raw_object_id=?
                 AND invalidated_at=? AND invalidation_reason=?
               ORDER BY locator_id""",
            (
                prepared.project_id,
                prepared.raw_object_id,
                prepared.observed_at,
                prepared.reason_code,
            ),
        ).fetchall()
        try:
            locator_ids = [validate_locator_id(row[0]) for row in invalidated_rows]
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "invalidated binding manifest is malformed",
            ) from error
        if (
            len(locator_ids) != manifest.invalidated_binding_count
            or self._set_sha256(locator_ids) != manifest.invalidated_locator_set_sha256
        ):
            _fail(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "invalidated binding manifest differs from tombstone event",
            )
        blocked_rows = self._connection.execute(
            """SELECT entity_id FROM blocked_entities_v2
               WHERE project_id=? AND raw_object_id=? AND reason_code=?
                 AND blocked_at=? AND actor_digest=?
               ORDER BY entity_id""",
            (
                prepared.project_id,
                prepared.raw_object_id,
                prepared.reason_code,
                prepared.observed_at,
                prepared.actor_digest,
            ),
        ).fetchall()
        try:
            entity_ids = [
                _authority_text(
                    row[0],
                    limit=SizeLimit.PORTABLE_ID,
                    label="stored blocked entity ID",
                )
                for row in blocked_rows
            ]
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "blocked entity manifest is malformed",
            ) from error
        if (
            len(entity_ids) != manifest.blocked_entity_count
            or self._set_sha256(entity_ids) != manifest.blocked_entity_set_sha256
        ):
            _fail(
                RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT,
                "blocked entity manifest differs from tombstone event",
            )
        if not event_store._exact_existing_event(event):
            raise AssertionError("tombstone event is unavailable after propagation")

    @staticmethod
    def _record(
        prepared: _PreparedTombstoneInput,
        *,
        event: _PreparedEvent,
        manifest: _TombstoneManifest,
        created: bool,
    ) -> RawTombstoneRecord:
        return RawTombstoneRecord(
            raw_object_id=prepared.raw_object_id,
            project_id=prepared.project_id,
            event_id=event.event_id,
            tombstoned_at=prepared.observed_at,
            invalidated_binding_count=manifest.invalidated_binding_count,
            blocked_entity_count=manifest.blocked_entity_count,
            created=created,
        )


__all__ = [
    "RAW_V2_BINDING_STORE_VERSION",
    "RAW_V2_EVENT_STORE_VERSION",
    "RAW_V2_OBJECT_STORE_VERSION",
    "RAW_V2_TOMBSTONE_STORE_VERSION",
    "RawBindingRecord",
    "RawBindingResolveRequest",
    "RawBindingWriteRequest",
    "RawEventRecord",
    "RawEventWriteRequest",
    "RawObjectRecord",
    "RawObjectWriteRequest",
    "RawTombstoneRecord",
    "RawTombstoneRequest",
    "RawV2BindingStore",
    "RawV2EventStore",
    "RawV2ObjectStore",
    "RawV2TombstoneStore",
]
