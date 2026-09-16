"""Fixture-only legacy raw V1 scanner and safe-copy checkpoint worker.

The scanner implements M0, M2, and M3 without writes.  The safe copier implements
only M4 against caller-owned V1/V2 fixtures; it is not connected to runtime,
production authorization, reconciliation, cutover, or public artifact publishing.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from .contracts import (
    SOURCE_TYPE_TO_DOMAIN,
    RawSelectorPayload,
    RawV2ContractError,
    RawV2Reason,
    canonical_json_bytes,
    canonical_timestamp,
    derive_selector_sha256,
    exact_bytes_sha256,
    normalize_portable_id,
    validate_sha256_digest,
)
from .policy import RawV2ProjectPolicyAdapter
from .schema import RawV2SchemaError, inspect_raw_v2_schema
from .store import (
    RawEventWriteRequest,
    RawObjectWriteRequest,
    RawV2EventStore,
    RawV2ObjectStore,
)

MIGRATION_V1_SCANNER_VERSION = "raw-v1-fixture-migration-scanner-v1"
MIGRATION_V1_COPIER_VERSION = "raw-v1-fixture-safe-copier-v1"
_MAX_COPY_BATCH_SIZE = 256


class MigrationInputMode(StrEnum):
    FIXTURE = "fixture"
    MIRROR = "mirror"
    SANITIZED_MIRROR = "sanitized_mirror"
    AUTHORIZED_PRODUCTION = "authorized_production"


class MigrationOperation(StrEnum):
    HASH_CONTROLLED_BLOB = "hash_controlled_blob"
    READ_V1_METADATA = "read_v1_metadata"
    READ_V1_SCHEMA = "read_v1_schema"
    CONNECT_PRODUCTION = "connect_production"
    EXPORT_RAW_VALUE = "export_raw_value"
    WRITE_V1 = "write_v1"
    WRITE_V2 = "write_v2"


class MigrationCategory(StrEnum):
    SAFE_COPY = "safe_copy"
    REFERENCE_ONLY = "reference_only"
    QUARANTINED_EXISTING = "quarantined_existing"
    STORAGE_INVALID = "storage_invalid"
    PROJECT_ACL_AMBIGUOUS = "project_acl_ambiguous"
    GENERATION_AMBIGUOUS = "generation_ambiguous"
    COLLISION_EVIDENCE = "collision_evidence"
    HISTORY_UNRECOVERABLE = "history_unrecoverable"


class MigrationResolution(StrEnum):
    SAFE_TO_COPY = "safe_to_copy"
    EXCLUDED_BY_POLICY = "excluded_by_policy"
    REPAIR_REQUIRED = "repair_required"
    OWNER_MAPPING_REQUIRED = "owner_mapping_required"
    REINGEST_REQUIRED = "reingest_required"


class MigrationSeverity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class MigrationScanStatus(StrEnum):
    DRY_RUN_PASS = "dry_run_pass"
    DRY_RUN_HOLD = "dry_run_hold"
    DRY_RUN_FAIL = "dry_run_fail"


ALLOWED_FIXTURE_OPERATIONS = (
    MigrationOperation.HASH_CONTROLLED_BLOB,
    MigrationOperation.READ_V1_METADATA,
    MigrationOperation.READ_V1_SCHEMA,
)
FORBIDDEN_FIXTURE_OPERATIONS = (
    MigrationOperation.CONNECT_PRODUCTION,
    MigrationOperation.EXPORT_RAW_VALUE,
    MigrationOperation.WRITE_V1,
    MigrationOperation.WRITE_V2,
)

_CATEGORY_POLICY: Mapping[
    MigrationCategory,
    tuple[MigrationResolution, MigrationSeverity],
] = MappingProxyType(
    {
        MigrationCategory.SAFE_COPY: (
            MigrationResolution.SAFE_TO_COPY,
            MigrationSeverity.P2,
        ),
        MigrationCategory.REFERENCE_ONLY: (
            MigrationResolution.EXCLUDED_BY_POLICY,
            MigrationSeverity.P2,
        ),
        MigrationCategory.QUARANTINED_EXISTING: (
            MigrationResolution.REINGEST_REQUIRED,
            MigrationSeverity.P1,
        ),
        MigrationCategory.STORAGE_INVALID: (
            MigrationResolution.REPAIR_REQUIRED,
            MigrationSeverity.P1,
        ),
        MigrationCategory.PROJECT_ACL_AMBIGUOUS: (
            MigrationResolution.OWNER_MAPPING_REQUIRED,
            MigrationSeverity.P0,
        ),
        MigrationCategory.GENERATION_AMBIGUOUS: (
            MigrationResolution.REINGEST_REQUIRED,
            MigrationSeverity.P1,
        ),
        MigrationCategory.COLLISION_EVIDENCE: (
            MigrationResolution.REINGEST_REQUIRED,
            MigrationSeverity.P0,
        ),
        MigrationCategory.HISTORY_UNRECOVERABLE: (
            MigrationResolution.REINGEST_REQUIRED,
            MigrationSeverity.P0,
        ),
    }
)

_TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "raw_objects": (
            "id",
            "project_id",
            "source_type",
            "source_instance",
            "source_object_id",
            "source_version",
            "source_uri",
            "content_hash",
            "media_type",
            "byte_length",
            "storage_path",
            "acl_ref",
            "state",
            "adapter_version",
            "schema_version",
            "metadata_json",
            "observed_at",
            "tombstoned_at",
        ),
        "source_events": (
            "event_id",
            "idempotency_key",
            "source_type",
            "source_instance",
            "event_type",
            "source_object_id",
            "source_version",
            "event_time",
            "observed_at",
            "project_id",
            "acl_ref",
            "content_hash",
            "raw_object_id",
            "payload_ref",
            "trace_id",
            "schema_version",
            "status",
            "metadata_json",
            "created_at",
        ),
        "raw_derivations": (
            "raw_object_id",
            "derived_entity_id",
            "derived_kind",
            "generation_id",
            "derivation_version",
            "created_at",
            "invalidated_at",
        ),
        "blocked_entities": ("entity_id", "raw_object_id", "reason", "blocked_at"),
    }
)
_TABLE_ORDER = tuple(_TABLE_COLUMNS)
_ORDER_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "raw_objects": ("id",),
        "source_events": ("event_id",),
        "raw_derivations": ("raw_object_id", "derived_entity_id", "derivation_version"),
        "blocked_entities": ("entity_id",),
    }
)


def _fail(reason: RawV2Reason, detail: str) -> None:
    raise RawV2ContractError(reason, detail)


def _digest(value: object) -> str:
    return exact_bytes_sha256(canonical_json_bytes(value, allow_none=True))


@dataclass(frozen=True, slots=True)
class MigrationSourceObjectClaim:
    project_id: str
    acl_ref: str
    source_type: str
    source_instance: str
    source_object_id: str
    source_version: str
    content_hash: str

    def canonical_value(self) -> list[str]:
        return [
            self.project_id,
            self.acl_ref,
            self.source_type,
            self.source_instance,
            self.source_object_id,
            self.source_version,
            self.content_hash,
        ]

    @property
    def global_key(self) -> tuple[str, str, str, str]:
        return (
            self.source_instance,
            self.source_object_id,
            self.source_version,
            self.content_hash,
        )


@dataclass(frozen=True, slots=True)
class MigrationInputBinding:
    mode: MigrationInputMode
    dataset_id: str
    source_revision: str
    blob_root: str
    logical_schema_sha256: str
    row_watermark_sha256: str
    source_claim_set_sha256: str
    source_snapshot_sha256: str
    source_claims: tuple[MigrationSourceObjectClaim, ...]
    allowed_operations: tuple[MigrationOperation, ...]
    forbidden_operations: tuple[MigrationOperation, ...]


@dataclass(frozen=True, slots=True)
class MigrationClassification:
    table: str
    row_fingerprint: str
    category: MigrationCategory
    resolution: MigrationResolution
    severity: MigrationSeverity
    detail_sha256: str

    def canonical_value(self) -> list[str]:
        return [
            self.table,
            self.row_fingerprint,
            self.category.value,
            self.resolution.value,
            self.severity.value,
            self.detail_sha256,
        ]


@dataclass(frozen=True, slots=True)
class MigrationScanReport:
    scanner_version: str
    dataset_id: str
    source_revision: str
    status: MigrationScanStatus
    row_counts: tuple[tuple[str, int], ...]
    raw_classification_counts: tuple[tuple[str, int], ...]
    all_classification_counts: tuple[tuple[str, int], ...]
    unclassified_count: int
    start_watermark_sha256: str
    end_watermark_sha256: str
    stable_input: bool
    source_before_sha256: str
    source_after_sha256: str
    classification_set_sha256: str
    classifications: tuple[MigrationClassification, ...]

    def canonical_value(self) -> dict[str, object]:
        return {
            "all_classification_counts": [list(item) for item in self.all_classification_counts],
            "classification_set_sha256": self.classification_set_sha256,
            "classifications": [item.canonical_value() for item in self.classifications],
            "dataset_id": self.dataset_id,
            "end_watermark_sha256": self.end_watermark_sha256,
            "raw_classification_counts": [list(item) for item in self.raw_classification_counts],
            "row_counts": [list(item) for item in self.row_counts],
            "scanner_version": self.scanner_version,
            "source_after_sha256": self.source_after_sha256,
            "source_before_sha256": self.source_before_sha256,
            "source_revision": self.source_revision,
            "stable_input": self.stable_input,
            "start_watermark_sha256": self.start_watermark_sha256,
            "status": self.status.value,
            "unclassified_count": self.unclassified_count,
        }

    @property
    def identity_sha256(self) -> str:
        return _digest(self.canonical_value())


def _canonical_claim(claim: MigrationSourceObjectClaim) -> MigrationSourceObjectClaim:
    if type(claim) is not MigrationSourceObjectClaim:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "source claim type is not frozen")
    source_type = normalize_portable_id(claim.source_type, label="migration source type")
    if source_type not in SOURCE_TYPE_TO_DOMAIN:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "source claim type is unregistered")
    return MigrationSourceObjectClaim(
        project_id=normalize_portable_id(claim.project_id, label="migration claim project"),
        acl_ref=normalize_portable_id(claim.acl_ref, label="migration claim ACL"),
        source_type=source_type,
        source_instance=normalize_portable_id(
            claim.source_instance,
            label="migration claim source instance",
        ),
        source_object_id=normalize_portable_id(
            claim.source_object_id,
            label="migration claim source object",
        ),
        source_version=normalize_portable_id(
            claim.source_version,
            label="migration claim source version",
        ),
        content_hash=validate_sha256_digest(claim.content_hash),
    )


def _canonical_claims(
    claims: tuple[MigrationSourceObjectClaim, ...],
) -> tuple[MigrationSourceObjectClaim, ...]:
    if type(claims) is not tuple:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "source claims must be an exact tuple")
    canonical = tuple(
        sorted((_canonical_claim(claim) for claim in claims), key=lambda x: x.canonical_value())
    )
    if len(set(canonical)) != len(canonical):
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "source claims contain duplicates")
    return canonical


def _claim_set_sha256(claims: Sequence[MigrationSourceObjectClaim]) -> str:
    return _digest([claim.canonical_value() for claim in claims])


def _source_snapshot_sha256(
    *,
    logical_schema_sha256: str,
    row_watermark_sha256: str,
    source_claim_set_sha256: str,
) -> str:
    return _digest(
        {
            "logical_schema_sha256": logical_schema_sha256,
            "row_watermark_sha256": row_watermark_sha256,
            "scanner_version": MIGRATION_V1_SCANNER_VERSION,
            "source_claim_set_sha256": source_claim_set_sha256,
        }
    )


def _canonical_blob_root(value: str | os.PathLike[str]) -> str:
    try:
        root = Path(value)
    except TypeError:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture blob root is invalid")
    if not root.is_absolute() or not root.exists() or not root.is_dir() or root.is_symlink():
        _fail(
            RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture blob root is not an absolute directory"
        )
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture blob root cannot be resolved")
    if resolved != root:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture blob root traverses a symlink")
    current = root
    while current != current.parent:
        if current.is_symlink():
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture blob root has a symlink parent")
        current = current.parent
    return str(root)


def _schema_profile(
    connection: sqlite3.Connection,
    *,
    failure_reason: RawV2Reason,
) -> list[dict[str, object]]:
    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    }
    if table_names != set(_TABLE_COLUMNS):
        _fail(failure_reason, "legacy logical table set changed")
    profile: list[dict[str, object]] = []
    for table in _TABLE_ORDER:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        names = tuple(row[1] for row in rows)
        if names != _TABLE_COLUMNS[table]:
            _fail(failure_reason, "legacy logical column set changed")
        profile.append(
            {
                "columns": [
                    {
                        "default": row[4],
                        "name": row[1],
                        "not_null": row[3],
                        "primary_key_order": row[5],
                        "type": row[2],
                    }
                    for row in rows
                ],
                "table": table,
            }
        )
    return profile


def _fingerprint_value(value: object) -> object:
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is bytes:
        raw = bytes(value)
        return {"sqlite_blob_length": len(raw), "sqlite_blob_sha256": exact_bytes_sha256(raw)}
    if type(value) is float:
        return {"sqlite_float_hex": value.hex()}
    return {"sqlite_type": type(value).__name__}


def _fingerprint_row_value(
    *,
    table: str,
    column: str,
    value: object,
    blob_root: Path,
) -> object:
    if table != "raw_objects" or column != "storage_path" or type(value) is not str:
        return _fingerprint_value(value)
    candidate = Path(value)
    try:
        relative = candidate.relative_to(blob_root)
    except ValueError:
        return {"outside_blob_root_path_sha256": exact_bytes_sha256(value.encode("utf-8"))}
    if ".." in relative.parts:
        return {"invalid_relative_path_sha256": exact_bytes_sha256(value.encode("utf-8"))}
    return {"blob_root_relative_path": relative.as_posix()}


def _row_fingerprint(
    table: str,
    row: Mapping[str, object],
    *,
    blob_root: Path,
) -> str:
    return _digest(
        {
            "table": table,
            "values": [
                _fingerprint_row_value(
                    table=table,
                    column=column,
                    value=row[column],
                    blob_root=blob_root,
                )
                for column in _TABLE_COLUMNS[table]
            ],
        }
    )


def _read_rows(
    connection: sqlite3.Connection,
) -> dict[str, tuple[dict[str, object], ...]]:
    result: dict[str, tuple[dict[str, object], ...]] = {}
    for table in _TABLE_ORDER:
        columns = _TABLE_COLUMNS[table]
        order = _ORDER_COLUMNS[table]
        fetched = connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY {', '.join(order)}"
        ).fetchall()
        result[table] = tuple(dict(zip(columns, row, strict=True)) for row in fetched)
    return result


def _row_watermark_sha256(
    rows: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    blob_root: Path,
) -> str:
    return _digest(
        [
            {
                "row_fingerprints": [
                    _row_fingerprint(table, row, blob_root=blob_root) for row in rows[table]
                ],
                "table": table,
            }
            for table in _TABLE_ORDER
        ]
    )


def _valid_portable(value: object, *, source_type: bool = False) -> bool:
    if type(value) is not str:
        return False
    try:
        canonical = normalize_portable_id(value, label="legacy migration value")
    except RawV2ContractError:
        return False
    return canonical == value and (not source_type or value in SOURCE_TYPE_TO_DOMAIN)


def _raw_global_key(row: Mapping[str, object]) -> tuple[object, object, object, object]:
    return (
        row["source_instance"],
        row["source_object_id"],
        row["source_version"],
        row["content_hash"],
    )


def _blob_is_valid(row: Mapping[str, object], *, blob_root: Path) -> bool:
    storage_path = row["storage_path"]
    byte_length = row["byte_length"]
    content_hash = row["content_hash"]
    if type(storage_path) is not str or type(byte_length) is not int or byte_length < 0:
        return False
    try:
        expected_digest = validate_sha256_digest(content_hash)  # type: ignore[arg-type]
    except (RawV2ContractError, TypeError):
        return False
    candidate = Path(storage_path)
    if not candidate.is_absolute():
        return False
    try:
        relative = candidate.relative_to(blob_root)
    except ValueError:
        return False
    if ".." in relative.parts or relative == Path("."):
        return False
    current = blob_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    try:
        if not candidate.is_file() or candidate.stat().st_size != byte_length:
            return False
        digest = hashlib.sha256()
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    return f"sha256:{digest.hexdigest()}" == expected_digest


def _raw_category(
    row: Mapping[str, object],
    *,
    events_by_raw: Mapping[object, Sequence[Mapping[str, object]]],
    derivations_by_raw: Mapping[object, Sequence[Mapping[str, object]]],
    claims_by_key: Mapping[
        tuple[object, object, object, object], Sequence[MigrationSourceObjectClaim]
    ],
    blob_root: Path,
) -> MigrationCategory:
    authority_fields_valid = all(
        (
            _valid_portable(row["project_id"]),
            _valid_portable(row["acl_ref"]),
            _valid_portable(row["source_type"], source_type=True),
            _valid_portable(row["source_instance"]),
            _valid_portable(row["source_object_id"]),
            _valid_portable(row["source_version"]),
        )
    )
    if not authority_fields_valid:
        return MigrationCategory.PROJECT_ACL_AMBIGUOUS
    if any(
        event["project_id"] != row["project_id"] or event["acl_ref"] != row["acl_ref"]
        for event in events_by_raw.get(row["id"], ())
    ):
        return MigrationCategory.COLLISION_EVIDENCE
    claims = tuple(claims_by_key.get(_raw_global_key(row), ()))
    if len({(claim.project_id, claim.acl_ref) for claim in claims}) > 1:
        return MigrationCategory.HISTORY_UNRECOVERABLE
    if not claims:
        return MigrationCategory.HISTORY_UNRECOVERABLE
    claim = claims[0]
    if (
        claim.project_id != row["project_id"]
        or claim.acl_ref != row["acl_ref"]
        or claim.source_type != row["source_type"]
    ):
        return MigrationCategory.PROJECT_ACL_AMBIGUOUS
    if row["state"] == "quarantined":
        return MigrationCategory.QUARANTINED_EXISTING
    if row["storage_path"] is None:
        return MigrationCategory.REFERENCE_ONLY
    if not _blob_is_valid(row, blob_root=blob_root):
        return MigrationCategory.STORAGE_INVALID
    if any(
        derivation["invalidated_at"] is None and not _valid_portable(derivation["generation_id"])
        for derivation in derivations_by_raw.get(row["id"], ())
    ):
        return MigrationCategory.GENERATION_AMBIGUOUS
    return MigrationCategory.SAFE_COPY


def _classification(
    *,
    table: str,
    row: Mapping[str, object],
    category: MigrationCategory,
    blob_root: Path,
) -> MigrationClassification:
    resolution, severity = _CATEGORY_POLICY[category]
    fingerprint = _row_fingerprint(table, row, blob_root=blob_root)
    return MigrationClassification(
        table=table,
        row_fingerprint=fingerprint,
        category=category,
        resolution=resolution,
        severity=severity,
        detail_sha256=_digest(
            {
                "category": category.value,
                "row_fingerprint": fingerprint,
                "scanner_version": MIGRATION_V1_SCANNER_VERSION,
                "table": table,
            }
        ),
    )


def _linked_category(
    raw_category: MigrationCategory,
    *,
    generation_missing: bool = False,
    global_block: bool = False,
) -> MigrationCategory:
    if raw_category in {
        MigrationCategory.COLLISION_EVIDENCE,
        MigrationCategory.HISTORY_UNRECOVERABLE,
    }:
        return raw_category
    if global_block:
        return MigrationCategory.PROJECT_ACL_AMBIGUOUS
    if raw_category is MigrationCategory.PROJECT_ACL_AMBIGUOUS:
        return raw_category
    if generation_missing:
        return MigrationCategory.GENERATION_AMBIGUOUS
    return raw_category


def _classify_rows(
    rows: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    binding: MigrationInputBinding,
) -> tuple[MigrationClassification, ...]:
    raw_by_id = {row["id"]: row for row in rows["raw_objects"]}
    events_by_raw: dict[object, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows["source_events"]:
        events_by_raw[row["raw_object_id"]].append(row)
    derivations_by_raw: dict[object, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows["raw_derivations"]:
        derivations_by_raw[row["raw_object_id"]].append(row)
    claims_by_key: dict[
        tuple[object, object, object, object],
        list[MigrationSourceObjectClaim],
    ] = defaultdict(list)
    for claim in binding.source_claims:
        claims_by_key[claim.global_key].append(claim)
    raw_categories: dict[object, MigrationCategory] = {}
    classified: list[MigrationClassification] = []
    root = Path(binding.blob_root)
    for row in rows["raw_objects"]:
        category = _raw_category(
            row,
            events_by_raw=events_by_raw,
            derivations_by_raw=derivations_by_raw,
            claims_by_key=claims_by_key,
            blob_root=root,
        )
        raw_categories[row["id"]] = category
        classified.append(
            _classification(table="raw_objects", row=row, category=category, blob_root=root)
        )
    for row in rows["source_events"]:
        linked = raw_by_id.get(row["raw_object_id"])
        if linked is None:
            category = MigrationCategory.HISTORY_UNRECOVERABLE
        elif not all(
            (
                _valid_portable(row["project_id"]),
                _valid_portable(row["acl_ref"]),
                _valid_portable(row["source_type"], source_type=True),
            )
        ):
            category = MigrationCategory.PROJECT_ACL_AMBIGUOUS
        elif row["project_id"] != linked["project_id"] or row["acl_ref"] != linked["acl_ref"]:
            category = MigrationCategory.COLLISION_EVIDENCE
        else:
            category = _linked_category(raw_categories[row["raw_object_id"]])
        classified.append(
            _classification(table="source_events", row=row, category=category, blob_root=root)
        )
    for row in rows["raw_derivations"]:
        if row["raw_object_id"] not in raw_by_id:
            category = MigrationCategory.HISTORY_UNRECOVERABLE
        else:
            category = _linked_category(
                raw_categories[row["raw_object_id"]],
                generation_missing=(
                    row["invalidated_at"] is None and not _valid_portable(row["generation_id"])
                ),
            )
        classified.append(
            _classification(table="raw_derivations", row=row, category=category, blob_root=root)
        )
    for row in rows["blocked_entities"]:
        if row["raw_object_id"] not in raw_by_id:
            category = MigrationCategory.HISTORY_UNRECOVERABLE
        else:
            category = _linked_category(
                raw_categories[row["raw_object_id"]],
                global_block=True,
            )
        classified.append(
            _classification(table="blocked_entities", row=row, category=category, blob_root=root)
        )
    return tuple(classified)


def _validate_binding(binding: MigrationInputBinding) -> None:
    if type(binding) is not MigrationInputBinding or binding.mode is not MigrationInputMode.FIXTURE:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "migration input is not fixture-only")
    if binding.allowed_operations != ALLOWED_FIXTURE_OPERATIONS:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture operation allowlist changed")
    if binding.forbidden_operations != FORBIDDEN_FIXTURE_OPERATIONS:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture forbidden operation set changed")
    try:
        dataset_id = normalize_portable_id(binding.dataset_id, label="migration dataset")
        source_revision = normalize_portable_id(
            binding.source_revision,
            label="migration source revision",
        )
        blob_root = _canonical_blob_root(binding.blob_root)
        schema_digest = validate_sha256_digest(binding.logical_schema_sha256)
        watermark = validate_sha256_digest(binding.row_watermark_sha256)
        claim_digest = validate_sha256_digest(binding.source_claim_set_sha256)
        source_snapshot = validate_sha256_digest(binding.source_snapshot_sha256)
        claims = _canonical_claims(binding.source_claims)
    except RawV2ContractError as error:
        if error.reason is RawV2Reason.MIGRATION_NOT_AUTHORIZED:
            raise
        raise RawV2ContractError(
            RawV2Reason.MIGRATION_NOT_AUTHORIZED,
            "fixture binding is not canonical",
        ) from error
    if (
        dataset_id != binding.dataset_id
        or source_revision != binding.source_revision
        or blob_root != binding.blob_root
        or claims != binding.source_claims
        or _claim_set_sha256(claims) != claim_digest
        or _source_snapshot_sha256(
            logical_schema_sha256=schema_digest,
            row_watermark_sha256=watermark,
            source_claim_set_sha256=claim_digest,
        )
        != source_snapshot
    ):
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture binding digest changed")


class RawV1MigrationScanner:
    """Freeze and scan only caller-owned SQLite/blob fixtures."""

    @staticmethod
    def freeze_fixture(
        *,
        connection: sqlite3.Connection,
        dataset_id: str,
        source_revision: str,
        blob_root: str | os.PathLike[str],
        source_claims: tuple[MigrationSourceObjectClaim, ...],
    ) -> MigrationInputBinding:
        if not isinstance(connection, sqlite3.Connection):
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture connection is not SQLite")
        try:
            canonical_dataset = normalize_portable_id(dataset_id, label="migration dataset")
            canonical_revision = normalize_portable_id(
                source_revision,
                label="migration source revision",
            )
            canonical_root = _canonical_blob_root(blob_root)
            canonical_claims = _canonical_claims(source_claims)
        except RawV2ContractError as error:
            if error.reason is RawV2Reason.MIGRATION_NOT_AUTHORIZED:
                raise
            raise RawV2ContractError(
                RawV2Reason.MIGRATION_NOT_AUTHORIZED,
                "fixture freeze input is not canonical",
            ) from error
        schema_sha256 = _digest(
            _schema_profile(connection, failure_reason=RawV2Reason.MIGRATION_UNCLASSIFIED)
        )
        rows = _read_rows(connection)
        watermark = _row_watermark_sha256(rows, blob_root=Path(canonical_root))
        claim_sha256 = _claim_set_sha256(canonical_claims)
        source_snapshot = _source_snapshot_sha256(
            logical_schema_sha256=schema_sha256,
            row_watermark_sha256=watermark,
            source_claim_set_sha256=claim_sha256,
        )
        binding = MigrationInputBinding(
            mode=MigrationInputMode.FIXTURE,
            dataset_id=canonical_dataset,
            source_revision=canonical_revision,
            blob_root=canonical_root,
            logical_schema_sha256=schema_sha256,
            row_watermark_sha256=watermark,
            source_claim_set_sha256=claim_sha256,
            source_snapshot_sha256=source_snapshot,
            source_claims=canonical_claims,
            allowed_operations=ALLOWED_FIXTURE_OPERATIONS,
            forbidden_operations=FORBIDDEN_FIXTURE_OPERATIONS,
        )
        _validate_binding(binding)
        return binding

    @staticmethod
    def scan(
        *,
        binding: MigrationInputBinding,
        opener: Callable[[], sqlite3.Connection],
    ) -> MigrationScanReport:
        _validate_binding(binding)
        if not callable(opener):
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture opener is not callable")
        connection = opener()
        if not isinstance(connection, sqlite3.Connection):
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "fixture opener did not return SQLite")
        try:
            connection.execute("PRAGMA query_only = ON")
            if connection.execute("PRAGMA query_only").fetchone() != (1,):
                _fail(
                    RawV2Reason.MIGRATION_NOT_AUTHORIZED, "SQLite query-only mode was not enabled"
                )
            schema_sha256 = _digest(
                _schema_profile(
                    connection,
                    failure_reason=RawV2Reason.MIGRATION_INPUT_CHANGED,
                )
            )
            if schema_sha256 != binding.logical_schema_sha256:
                _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "fixture logical schema changed")
            start_rows = _read_rows(connection)
            start_watermark = _row_watermark_sha256(
                start_rows,
                blob_root=Path(binding.blob_root),
            )
            source_before = _source_snapshot_sha256(
                logical_schema_sha256=schema_sha256,
                row_watermark_sha256=start_watermark,
                source_claim_set_sha256=binding.source_claim_set_sha256,
            )
            if (
                start_watermark != binding.row_watermark_sha256
                or source_before != binding.source_snapshot_sha256
            ):
                _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "fixture changed after freeze")
            classifications = _classify_rows(start_rows, binding=binding)
            end_schema_sha256 = _digest(
                _schema_profile(
                    connection,
                    failure_reason=RawV2Reason.MIGRATION_INPUT_CHANGED,
                )
            )
            end_rows = _read_rows(connection)
            end_watermark = _row_watermark_sha256(
                end_rows,
                blob_root=Path(binding.blob_root),
            )
            source_after = _source_snapshot_sha256(
                logical_schema_sha256=end_schema_sha256,
                row_watermark_sha256=end_watermark,
                source_claim_set_sha256=binding.source_claim_set_sha256,
            )
            if (
                end_schema_sha256 != schema_sha256
                or end_watermark != start_watermark
                or end_watermark != binding.row_watermark_sha256
                or source_after != source_before
                or source_after != binding.source_snapshot_sha256
            ):
                _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "fixture changed during scan")
            row_counts = tuple(
                sorted((table, len(table_rows)) for table, table_rows in start_rows.items())
            )
            expected_count = sum(count for _, count in row_counts)
            if len(classifications) != expected_count:
                _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration row conservation failed")
            raw_counts = Counter(
                item.category.value for item in classifications if item.table == "raw_objects"
            )
            all_counts = Counter(item.category.value for item in classifications)
            if any(item.category not in MigrationCategory for item in classifications):
                _fail(RawV2Reason.MIGRATION_UNCLASSIFIED, "migration category is unknown")
            classification_set_sha256 = _digest(
                [item.canonical_value() for item in classifications]
            )
            severities = {item.severity for item in classifications}
            resolutions = {item.resolution for item in classifications}
            if MigrationSeverity.P0 in severities:
                status = MigrationScanStatus.DRY_RUN_FAIL
            elif resolutions & {
                MigrationResolution.REPAIR_REQUIRED,
                MigrationResolution.REINGEST_REQUIRED,
            }:
                status = MigrationScanStatus.DRY_RUN_HOLD
            else:
                status = MigrationScanStatus.DRY_RUN_PASS
            return MigrationScanReport(
                scanner_version=MIGRATION_V1_SCANNER_VERSION,
                dataset_id=binding.dataset_id,
                source_revision=binding.source_revision,
                status=status,
                row_counts=row_counts,
                raw_classification_counts=tuple(sorted(raw_counts.items())),
                all_classification_counts=tuple(sorted(all_counts.items())),
                unclassified_count=0,
                start_watermark_sha256=start_watermark,
                end_watermark_sha256=end_watermark,
                stable_input=True,
                source_before_sha256=source_before,
                source_after_sha256=source_after,
                classification_set_sha256=classification_set_sha256,
                classifications=classifications,
            )
        finally:
            connection.close()


@dataclass(frozen=True, slots=True)
class MigrationStableVersionClaim:
    source_row_fingerprint: str
    stable_version: str

    def canonical_value(self) -> list[str]:
        return [self.source_row_fingerprint, self.stable_version]


@dataclass(frozen=True, slots=True)
class MigrationDerivedEntityClaim:
    source_row_fingerprint: str
    project_id: str

    def canonical_value(self) -> list[str]:
        return [self.source_row_fingerprint, self.project_id]


@dataclass(frozen=True, slots=True)
class MigrationSafeCopyPlan:
    copier_version: str
    source_snapshot_sha256: str
    scan_identity_sha256: str
    target_schema_sha256: str
    batch_size: int
    stable_version_claims: tuple[MigrationStableVersionClaim, ...]
    derived_entity_claims: tuple[MigrationDerivedEntityClaim, ...]
    plan_sha256: str

    def canonical_value(self) -> dict[str, object]:
        return {
            "batch_size": self.batch_size,
            "copier_version": self.copier_version,
            "derived_entity_claims": [
                item.canonical_value() for item in self.derived_entity_claims
            ],
            "scan_identity_sha256": self.scan_identity_sha256,
            "source_snapshot_sha256": self.source_snapshot_sha256,
            "stable_version_claims": [
                item.canonical_value() for item in self.stable_version_claims
            ],
            "target_schema_sha256": self.target_schema_sha256,
        }


@dataclass(frozen=True, slots=True)
class MigrationSafeCopyCheckpoint:
    copier_version: str
    last_source_row_fingerprint: str | None

    def canonical_value(self) -> dict[str, object]:
        return {
            "copier_version": self.copier_version,
            "last_source_row_fingerprint": self.last_source_row_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class MigrationBindingCandidate:
    candidate_sha256: str
    source_row_fingerprint: str
    project_id: str
    raw_object_id: str
    generation_id: str
    derived_entity_id: str
    derivation_kind: str
    derivation_version: str
    selector_kind: str
    selector_sha256: str
    raw_content_sha256: str
    selected_content_sha256: str

    def canonical_value(self) -> list[str]:
        return [
            self.candidate_sha256,
            self.source_row_fingerprint,
            self.project_id,
            self.raw_object_id,
            self.generation_id,
            self.derived_entity_id,
            self.derivation_kind,
            self.derivation_version,
            self.selector_kind,
            self.selector_sha256,
            self.raw_content_sha256,
            self.selected_content_sha256,
        ]


@dataclass(frozen=True, slots=True)
class MigrationSafeCopyResult:
    migration_run_id: str
    plan_sha256: str
    status: str
    copied_object_count: int
    copied_event_count: int
    skipped_event_count: int
    binding_candidate_count: int
    object_set_sha256: str
    event_set_sha256: str
    binding_candidate_set_sha256: str
    checkpoint_sha256: str
    counters_sha256: str
    binding_candidates: tuple[MigrationBindingCandidate, ...]

    def canonical_value(self) -> dict[str, object]:
        return {
            "binding_candidate_count": self.binding_candidate_count,
            "binding_candidate_set_sha256": self.binding_candidate_set_sha256,
            "binding_candidates": [item.canonical_value() for item in self.binding_candidates],
            "checkpoint_sha256": self.checkpoint_sha256,
            "copied_event_count": self.copied_event_count,
            "copied_object_count": self.copied_object_count,
            "counters_sha256": self.counters_sha256,
            "event_set_sha256": self.event_set_sha256,
            "migration_run_id": self.migration_run_id,
            "object_set_sha256": self.object_set_sha256,
            "plan_sha256": self.plan_sha256,
            "skipped_event_count": self.skipped_event_count,
            "status": self.status,
        }

    @property
    def identity_sha256(self) -> str:
        return _digest(self.canonical_value())


@dataclass(frozen=True, slots=True)
class _PreparedMigrationRaw:
    source_row_fingerprint: str
    object_value: object
    payload: bytes
    storage_key: str
    event_inputs: tuple[object, ...]
    skipped_event_count: int
    binding_candidates: tuple[MigrationBindingCandidate, ...]


def _canonical_json_text(value: object) -> str:
    return canonical_json_bytes(value, allow_none=True).decode("utf-8")


def _validate_scan_report(
    binding: MigrationInputBinding,
    report: MigrationScanReport,
) -> None:
    if type(report) is not MigrationScanReport:
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "scan report type is not frozen")
    if (
        report.scanner_version != MIGRATION_V1_SCANNER_VERSION
        or report.dataset_id != binding.dataset_id
        or report.source_revision != binding.source_revision
        or report.start_watermark_sha256 != binding.row_watermark_sha256
        or report.end_watermark_sha256 != binding.row_watermark_sha256
        or report.source_before_sha256 != binding.source_snapshot_sha256
        or report.source_after_sha256 != binding.source_snapshot_sha256
        or not report.stable_input
        or report.unclassified_count != 0
    ):
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "scan report does not match input binding")
    if any(item.severity is MigrationSeverity.P0 for item in report.classifications):
        _fail(RawV2Reason.MIGRATION_UNCLASSIFIED, "P0 scan report cannot enter safe copy")


def _canonical_stable_claim(
    claim: MigrationStableVersionClaim,
) -> MigrationStableVersionClaim:
    if type(claim) is not MigrationStableVersionClaim:
        _fail(RawV2Reason.STABLE_VERSION_UNAVAILABLE, "stable version claim type changed")
    try:
        fingerprint = validate_sha256_digest(claim.source_row_fingerprint)
        stable_version = normalize_portable_id(
            claim.stable_version,
            label="migration stable version",
        )
    except RawV2ContractError as error:
        raise RawV2ContractError(
            RawV2Reason.STABLE_VERSION_UNAVAILABLE,
            "stable version claim is not canonical",
        ) from error
    return MigrationStableVersionClaim(fingerprint, stable_version)


def _canonical_derived_claim(
    claim: MigrationDerivedEntityClaim,
) -> MigrationDerivedEntityClaim:
    if type(claim) is not MigrationDerivedEntityClaim:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "derived entity claim type changed")
    try:
        return MigrationDerivedEntityClaim(
            source_row_fingerprint=validate_sha256_digest(claim.source_row_fingerprint),
            project_id=normalize_portable_id(
                claim.project_id,
                label="migration derived entity project",
            ),
        )
    except RawV2ContractError as error:
        raise RawV2ContractError(
            RawV2Reason.MIGRATION_NOT_AUTHORIZED,
            "derived entity claim is not canonical",
        ) from error


def _copy_plan_digest(plan: MigrationSafeCopyPlan) -> str:
    return _digest(plan.canonical_value())


def _validate_copy_plan(
    *,
    binding: MigrationInputBinding,
    report: MigrationScanReport,
    plan: MigrationSafeCopyPlan,
) -> None:
    if type(plan) is not MigrationSafeCopyPlan:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "copy plan type is not frozen")
    stable = tuple(
        sorted(
            (_canonical_stable_claim(item) for item in plan.stable_version_claims),
            key=lambda x: x.canonical_value(),
        )
    )
    derived = tuple(
        sorted(
            (_canonical_derived_claim(item) for item in plan.derived_entity_claims),
            key=lambda x: x.canonical_value(),
        )
    )
    if (
        plan.copier_version != MIGRATION_V1_COPIER_VERSION
        or plan.source_snapshot_sha256 != binding.source_snapshot_sha256
        or plan.scan_identity_sha256 != report.identity_sha256
        or type(plan.batch_size) is not int
        or not 1 <= plan.batch_size <= _MAX_COPY_BATCH_SIZE
        or stable != plan.stable_version_claims
        or derived != plan.derived_entity_claims
        or len({item.source_row_fingerprint for item in stable}) != len(stable)
        or len({item.source_row_fingerprint for item in derived}) != len(derived)
    ):
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "copy plan authority changed")
    try:
        validate_sha256_digest(plan.target_schema_sha256)
        validate_sha256_digest(plan.plan_sha256)
    except RawV2ContractError as error:
        raise RawV2ContractError(
            RawV2Reason.MIGRATION_NOT_AUTHORIZED,
            "copy plan digest is invalid",
        ) from error
    if _copy_plan_digest(plan) != plan.plan_sha256:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "copy plan digest changed")


def _database_path(connection: sqlite3.Connection) -> Path | None:
    rows = connection.execute("PRAGMA database_list").fetchall()
    for _, name, path in rows:
        if name == "main" and type(path) is str and path:
            return Path(path).resolve()
    return None


def _read_controlled_blob(
    row: Mapping[str, object],
    *,
    blob_root: Path,
) -> bytes:
    stored_path = row["storage_path"]
    stored_length = row["byte_length"]
    stored_digest = row["content_hash"]
    if type(stored_path) is not str or type(stored_length) is not int or stored_length < 0:
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "safe-copy blob locator changed")
    try:
        expected_digest = validate_sha256_digest(stored_digest)  # type: ignore[arg-type]
    except (RawV2ContractError, TypeError) as error:
        raise RawV2ContractError(
            RawV2Reason.MIGRATION_INPUT_CHANGED,
            "safe-copy blob digest changed",
        ) from error
    candidate = Path(stored_path)
    try:
        relative = candidate.relative_to(blob_root)
    except ValueError as error:
        raise RawV2ContractError(
            RawV2Reason.MIGRATION_INPUT_CHANGED,
            "safe-copy blob left controlled root",
        ) from error
    if not candidate.is_absolute() or relative == Path(".") or ".." in relative.parts:
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "safe-copy blob locator is invalid")
    cursor = blob_root
    for part in relative.parts:
        cursor = cursor / part
        try:
            current = cursor.lstat()
        except OSError as error:
            raise RawV2ContractError(
                RawV2Reason.MIGRATION_INPUT_CHANGED,
                "safe-copy blob is unavailable",
            ) from error
        if stat.S_ISLNK(current.st_mode):
            _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "safe-copy blob traverses symlink")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(candidate, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "safe-copy blob is not regular")
            payload = stream.read()
    except RawV2ContractError:
        raise
    except OSError as error:
        raise RawV2ContractError(
            RawV2Reason.MIGRATION_INPUT_CHANGED,
            "safe-copy blob cannot be read",
        ) from error
    observed_digest = exact_bytes_sha256(payload)
    if len(payload) != stored_length or observed_digest != expected_digest:
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "safe-copy blob bytes changed")
    return payload


def _event_is_reconstructible(
    event: Mapping[str, object],
    raw: Mapping[str, object],
    *,
    observed_digest: str,
) -> bool:
    return all(
        (
            event["raw_object_id"] == raw["id"],
            event["project_id"] == raw["project_id"],
            event["acl_ref"] == raw["acl_ref"],
            event["source_type"] == raw["source_type"],
            event["source_instance"] == raw["source_instance"],
            event["source_object_id"] == raw["source_object_id"],
            event["source_version"] == raw["source_version"],
            event["content_hash"] == observed_digest,
            event["status"] == "persisted",
            type(event["idempotency_key"]) is str,
            bool(event["idempotency_key"]),
        )
    )


def _checkpoint_from_text(value: object) -> MigrationSafeCopyCheckpoint:
    if type(value) is not str:
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "migration checkpoint is missing")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError) as error:
        raise RawV2ContractError(
            RawV2Reason.MIGRATION_INPUT_CHANGED,
            "migration checkpoint is malformed",
        ) from error
    if type(parsed) is not dict or set(parsed) != {
        "copier_version",
        "last_source_row_fingerprint",
    }:
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "migration checkpoint shape changed")
    last = parsed["last_source_row_fingerprint"]
    if last is not None:
        try:
            last = validate_sha256_digest(last)
        except (RawV2ContractError, TypeError) as error:
            raise RawV2ContractError(
                RawV2Reason.MIGRATION_INPUT_CHANGED,
                "migration checkpoint fingerprint changed",
            ) from error
    checkpoint = MigrationSafeCopyCheckpoint(
        copier_version=str(parsed["copier_version"]),
        last_source_row_fingerprint=last,
    )
    if checkpoint.copier_version != MIGRATION_V1_COPIER_VERSION:
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "migration checkpoint version changed")
    if _canonical_json_text(checkpoint.canonical_value()) != value:
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "migration checkpoint is not canonical")
    return checkpoint


def _copy_counters(
    prepared: Sequence[_PreparedMigrationRaw],
    *,
    copied_count: int,
) -> dict[str, int]:
    prefix = prepared[:copied_count]
    return {
        "binding_candidate_count": sum(len(item.binding_candidates) for item in prefix),
        "copied_event_count": sum(len(item.event_inputs) for item in prefix),
        "copied_object_count": copied_count,
        "eligible_object_count": len(prepared),
        "skipped_event_count": sum(item.skipped_event_count for item in prefix),
    }


class RawV1SafeCopier:
    """Copy scanner-approved V1 rows into one caller-owned raw V2 fixture."""

    @staticmethod
    def freeze_copy_plan(
        *,
        binding: MigrationInputBinding,
        scan_report: MigrationScanReport,
        target_schema_sha256: str,
        batch_size: int,
        stable_version_claims: tuple[MigrationStableVersionClaim, ...],
        derived_entity_claims: tuple[MigrationDerivedEntityClaim, ...] = (),
    ) -> MigrationSafeCopyPlan:
        _validate_binding(binding)
        _validate_scan_report(binding, scan_report)
        if type(batch_size) is not int or not 1 <= batch_size <= _MAX_COPY_BATCH_SIZE:
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "copy batch size is outside contract")
        if type(stable_version_claims) is not tuple or type(derived_entity_claims) is not tuple:
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "copy claims must be exact tuples")
        safe_raw_fingerprints = {
            item.row_fingerprint
            for item in scan_report.classifications
            if item.table == "raw_objects" and item.category is MigrationCategory.SAFE_COPY
        }
        stable = tuple(
            sorted(
                (_canonical_stable_claim(item) for item in stable_version_claims),
                key=lambda item: item.canonical_value(),
            )
        )
        stable_fingerprints = {item.source_row_fingerprint for item in stable}
        if len(stable_fingerprints) != len(stable) or stable_fingerprints != safe_raw_fingerprints:
            _fail(
                RawV2Reason.STABLE_VERSION_UNAVAILABLE,
                "every safe raw row requires one exact stable version",
            )
        safe_derivation_fingerprints = {
            item.row_fingerprint
            for item in scan_report.classifications
            if item.table == "raw_derivations" and item.category is MigrationCategory.SAFE_COPY
        }
        derived = tuple(
            sorted(
                (_canonical_derived_claim(item) for item in derived_entity_claims),
                key=lambda item: item.canonical_value(),
            )
        )
        if len({item.source_row_fingerprint for item in derived}) != len(derived) or not {
            item.source_row_fingerprint for item in derived
        }.issubset(safe_derivation_fingerprints):
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "derived entity claims exceed safe rows")
        plan = MigrationSafeCopyPlan(
            copier_version=MIGRATION_V1_COPIER_VERSION,
            source_snapshot_sha256=binding.source_snapshot_sha256,
            scan_identity_sha256=scan_report.identity_sha256,
            target_schema_sha256=validate_sha256_digest(target_schema_sha256),
            batch_size=batch_size,
            stable_version_claims=stable,
            derived_entity_claims=derived,
            plan_sha256="sha256:" + "0" * 64,
        )
        plan = MigrationSafeCopyPlan(
            **{
                **{
                    field: getattr(plan, field)
                    for field in plan.__dataclass_fields__
                    if field != "plan_sha256"
                },
                "plan_sha256": _copy_plan_digest(plan),
            }
        )
        _validate_copy_plan(binding=binding, report=scan_report, plan=plan)
        return plan

    @staticmethod
    def copy_fixture(
        *,
        binding: MigrationInputBinding,
        scan_report: MigrationScanReport,
        plan: MigrationSafeCopyPlan,
        source_opener: Callable[[], sqlite3.Connection],
        target_connection: sqlite3.Connection,
        target_blob_root: Path,
        policy: RawV2ProjectPolicyAdapter,
        clock: Callable[[], str],
        batch_observer: Callable[[int, MigrationSafeCopyCheckpoint], None] | None = None,
    ) -> MigrationSafeCopyResult:
        _validate_binding(binding)
        _validate_scan_report(binding, scan_report)
        _validate_copy_plan(binding=binding, report=scan_report, plan=plan)
        if not isinstance(target_connection, sqlite3.Connection):
            raise TypeError("target_connection must be an open sqlite3.Connection")
        if not isinstance(target_blob_root, Path):
            raise TypeError("target_blob_root must be a pathlib.Path")
        if not callable(source_opener) or not callable(clock):
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "copy fixture callbacks are invalid")
        if batch_observer is not None and not callable(batch_observer):
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "batch observer is not callable")
        if target_connection.in_transaction:
            _fail(RawV2Reason.RAW_STATE_INVALID, "target requires no caller transaction")
        try:
            target_schema = inspect_raw_v2_schema(target_connection)
        except RawV2SchemaError as error:
            raise RawV2ContractError(
                RawV2Reason.RAW_STATE_INVALID,
                "target raw V2 schema is not exact",
            ) from error
        if target_schema.schema_sha256 != plan.target_schema_sha256:
            _fail(RawV2Reason.RAW_STATE_INVALID, "target raw V2 schema digest changed")
        object_store = RawV2ObjectStore(
            connection=target_connection,
            blob_root=target_blob_root,
            policy=policy,
            clock=clock,
        )
        event_store = RawV2EventStore(
            connection=target_connection,
            policy=policy,
            clock=clock,
        )
        object_store._require_ready()
        event_store._require_ready()

        source = source_opener()
        if not isinstance(source, sqlite3.Connection):
            _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "source opener did not return SQLite")
        run_initialized = False
        migration_run_id = RawV1SafeCopier._migration_run_id(binding, plan)
        try:
            source.execute("PRAGMA query_only = ON")
            if source.execute("PRAGMA query_only").fetchone() != (1,):
                _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "source query-only mode unavailable")
            source_path = _database_path(source)
            target_path = _database_path(target_connection)
            if source_path is not None and source_path == target_path:
                _fail(
                    RawV2Reason.MIGRATION_NOT_AUTHORIZED,
                    "source and target databases are identical",
                )
            if Path(binding.blob_root) == target_blob_root.resolve():
                _fail(
                    RawV2Reason.MIGRATION_NOT_AUTHORIZED,
                    "source and target blob roots are identical",
                )
            start_rows = RawV1SafeCopier._validated_source_rows(source, binding, scan_report)
            prepared = RawV1SafeCopier._prepare_rows(
                rows=start_rows,
                binding=binding,
                report=scan_report,
                plan=plan,
                object_store=object_store,
                event_store=event_store,
            )
            checkpoint, status = RawV1SafeCopier._initialize_or_resume(
                target_connection=target_connection,
                binding=binding,
                report=scan_report,
                plan=plan,
                prepared=prepared,
                migration_run_id=migration_run_id,
                clock=clock,
            )
            run_initialized = True
            resume_index = RawV1SafeCopier._resume_index(prepared, checkpoint)
            if status == "failed":
                _fail(RawV2Reason.RAW_STATE_INVALID, "failed migration run cannot resume")
            if status == "copied":
                RawV1SafeCopier._verify_prepared(
                    prepared=prepared,
                    object_store=object_store,
                    event_store=event_store,
                )
            else:
                batch_number = resume_index // plan.batch_size
                for offset in range(resume_index, len(prepared), plan.batch_size):
                    batch = prepared[offset : offset + plan.batch_size]
                    RawV1SafeCopier._commit_batch(
                        batch=batch,
                        copied_count=offset + len(batch),
                        all_prepared=prepared,
                        final=offset + len(batch) == len(prepared),
                        migration_run_id=migration_run_id,
                        target_connection=target_connection,
                        object_store=object_store,
                        event_store=event_store,
                        clock=clock,
                    )
                    batch_number += 1
                    checkpoint = MigrationSafeCopyCheckpoint(
                        copier_version=MIGRATION_V1_COPIER_VERSION,
                        last_source_row_fingerprint=batch[-1].source_row_fingerprint,
                    )
                    if batch_observer is not None:
                        batch_observer(batch_number, checkpoint)
                if not prepared:
                    RawV1SafeCopier._finish_empty_run(
                        target_connection=target_connection,
                        migration_run_id=migration_run_id,
                        clock=clock,
                    )
            RawV1SafeCopier._validate_source_end(source, binding)
            return RawV1SafeCopier._result(
                target_connection=target_connection,
                migration_run_id=migration_run_id,
                plan=plan,
                prepared=prepared,
            )
        except RawV2ContractError:
            if target_connection.in_transaction:
                target_connection.rollback()
            if run_initialized:
                RawV1SafeCopier._mark_failed(
                    target_connection=target_connection,
                    migration_run_id=migration_run_id,
                    clock=clock,
                )
            raise
        finally:
            source.close()

    @staticmethod
    def _migration_run_id(binding: MigrationInputBinding, plan: MigrationSafeCopyPlan) -> str:
        digest = _digest(
            {
                "mode": binding.mode.value,
                "plan_sha256": plan.plan_sha256,
                "scan_identity_sha256": plan.scan_identity_sha256,
                "source_snapshot_sha256": binding.source_snapshot_sha256,
            }
        )
        return "raw-migration-v2:" + digest.removeprefix("sha256:")

    @staticmethod
    def _validated_source_rows(
        source: sqlite3.Connection,
        binding: MigrationInputBinding,
        report: MigrationScanReport,
    ) -> dict[str, tuple[dict[str, object], ...]]:
        schema_sha256 = _digest(
            _schema_profile(source, failure_reason=RawV2Reason.MIGRATION_INPUT_CHANGED)
        )
        rows = _read_rows(source)
        watermark = _row_watermark_sha256(rows, blob_root=Path(binding.blob_root))
        snapshot = _source_snapshot_sha256(
            logical_schema_sha256=schema_sha256,
            row_watermark_sha256=watermark,
            source_claim_set_sha256=binding.source_claim_set_sha256,
        )
        classifications = _classify_rows(rows, binding=binding)
        if (
            schema_sha256 != binding.logical_schema_sha256
            or watermark != binding.row_watermark_sha256
            or snapshot != binding.source_snapshot_sha256
            or classifications != report.classifications
            or _digest([item.canonical_value() for item in classifications])
            != report.classification_set_sha256
        ):
            _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "source changed after scan")
        return rows

    @staticmethod
    def _prepare_rows(
        *,
        rows: Mapping[str, Sequence[Mapping[str, object]]],
        binding: MigrationInputBinding,
        report: MigrationScanReport,
        plan: MigrationSafeCopyPlan,
        object_store: RawV2ObjectStore,
        event_store: RawV2EventStore,
    ) -> tuple[_PreparedMigrationRaw, ...]:
        classification = {
            (item.table, item.row_fingerprint): item for item in report.classifications
        }
        stable_versions = {
            item.source_row_fingerprint: item.stable_version for item in plan.stable_version_claims
        }
        derived_projects = {
            item.source_row_fingerprint: item.project_id for item in plan.derived_entity_claims
        }
        events_by_raw: dict[object, list[Mapping[str, object]]] = defaultdict(list)
        for event in rows["source_events"]:
            events_by_raw[event["raw_object_id"]].append(event)
        derivations_by_raw: dict[object, list[Mapping[str, object]]] = defaultdict(list)
        for derivation in rows["raw_derivations"]:
            derivations_by_raw[derivation["raw_object_id"]].append(derivation)
        safe_rows: list[tuple[str, Mapping[str, object]]] = []
        root = Path(binding.blob_root)
        for raw in rows["raw_objects"]:
            fingerprint = _row_fingerprint("raw_objects", raw, blob_root=root)
            item = classification.get(("raw_objects", fingerprint))
            if item is not None and item.category is MigrationCategory.SAFE_COPY:
                safe_rows.append((fingerprint, raw))
        safe_rows.sort(key=lambda item: (str(item[1]["project_id"]), str(item[1]["id"])))
        prepared_rows: list[_PreparedMigrationRaw] = []
        for fingerprint, raw in safe_rows:
            payload = _read_controlled_blob(raw, blob_root=root)
            digest = exact_bytes_sha256(payload)
            stable_version = stable_versions.get(fingerprint)
            if stable_version is None:
                _fail(RawV2Reason.STABLE_VERSION_UNAVAILABLE, "safe raw stable version is missing")
            object_value = object_store._prepare(
                request=RawObjectWriteRequest(
                    project_id=str(raw["project_id"]),
                    source_type=str(raw["source_type"]),
                    source_instance_id=str(raw["source_instance"]),
                    source_object_id=str(raw["source_object_id"]),
                    source_version=str(raw["source_version"]),
                    stable_version=stable_version,
                    source_acl_ref=str(raw["acl_ref"]),
                    media_type=str(raw["media_type"]),
                    adapter_version=str(raw["adapter_version"]),
                    schema_version=str(raw["schema_version"]),
                    metadata_projection={},
                    observed_at=str(raw["observed_at"]),
                ),
                raw_content_sha256=digest,
                byte_length=len(payload),
                state="active",
            )
            event_inputs: list[object] = []
            skipped_events = 0
            for event in sorted(events_by_raw.get(raw["id"], ()), key=lambda x: str(x["event_id"])):
                event_fingerprint = _row_fingerprint("source_events", event, blob_root=root)
                event_class = classification.get(("source_events", event_fingerprint))
                if (
                    event_class is None
                    or event_class.category is not MigrationCategory.SAFE_COPY
                    or not _event_is_reconstructible(event, raw, observed_digest=digest)
                ):
                    skipped_events += 1
                    continue
                try:
                    prepared_event = event_store._prepare_input(
                        RawEventWriteRequest(
                            project_id=str(raw["project_id"]),
                            source_type=str(raw["source_type"]),
                            source_instance_id=str(raw["source_instance"]),
                            event_type=str(event["event_type"]),
                            mutation_id=str(event["idempotency_key"]),
                            source_object_id=str(raw["source_object_id"]),
                            source_version=str(raw["source_version"]),
                            source_acl_ref=str(raw["acl_ref"]),
                            raw_object_id=object_value.raw_object_id,  # type: ignore[attr-defined]
                            event_time=None
                            if event["event_time"] is None
                            else str(event["event_time"]),
                            observed_at=str(event["observed_at"]),
                            trace_id="migration-event:" + event_fingerprint.removeprefix("sha256:"),
                            schema_version=str(event["schema_version"]),
                            status="persisted",
                            metadata_projection={},
                        )
                    )
                except RawV2ContractError:
                    skipped_events += 1
                else:
                    event_inputs.append(prepared_event)
            candidates: list[MigrationBindingCandidate] = []
            selector = RawSelectorPayload(selector_kind="whole_object", selector={})
            selector_sha256 = derive_selector_sha256(selector)
            for derivation in sorted(
                derivations_by_raw.get(raw["id"], ()),
                key=lambda x: (str(x["derived_entity_id"]), str(x["derivation_version"])),
            ):
                derivation_fingerprint = _row_fingerprint(
                    "raw_derivations",
                    derivation,
                    blob_root=root,
                )
                derivation_class = classification.get(("raw_derivations", derivation_fingerprint))
                claimed_project = derived_projects.get(derivation_fingerprint)
                if (
                    derivation_class is None
                    or derivation_class.category is not MigrationCategory.SAFE_COPY
                    or claimed_project != raw["project_id"]
                    or derivation["invalidated_at"] is not None
                ):
                    continue
                try:
                    generation = normalize_portable_id(
                        derivation["generation_id"],  # type: ignore[arg-type]
                        label="migration generation",
                    )
                    entity = normalize_portable_id(
                        derivation["derived_entity_id"],  # type: ignore[arg-type]
                        label="migration derived entity",
                    )
                    kind = normalize_portable_id(
                        derivation["derived_kind"],  # type: ignore[arg-type]
                        label="migration derivation kind",
                    )
                    version = normalize_portable_id(
                        derivation["derivation_version"],  # type: ignore[arg-type]
                        label="migration derivation version",
                    )
                except (RawV2ContractError, TypeError):
                    continue
                candidate_facts = {
                    "derived_entity_id": entity,
                    "derivation_kind": kind,
                    "derivation_version": version,
                    "generation_id": generation,
                    "project_id": claimed_project,
                    "raw_content_sha256": digest,
                    "raw_object_id": object_value.raw_object_id,  # type: ignore[attr-defined]
                    "selected_content_sha256": digest,
                    "selector_sha256": selector_sha256,
                    "source_row_fingerprint": derivation_fingerprint,
                }
                candidates.append(
                    MigrationBindingCandidate(
                        candidate_sha256=_digest(candidate_facts),
                        source_row_fingerprint=derivation_fingerprint,
                        project_id=str(claimed_project),
                        raw_object_id=object_value.raw_object_id,  # type: ignore[attr-defined]
                        generation_id=generation,
                        derived_entity_id=entity,
                        derivation_kind=kind,
                        derivation_version=version,
                        selector_kind="whole_object",
                        selector_sha256=selector_sha256,
                        raw_content_sha256=digest,
                        selected_content_sha256=digest,
                    )
                )
            prepared_rows.append(
                _PreparedMigrationRaw(
                    source_row_fingerprint=fingerprint,
                    object_value=object_value,
                    payload=payload,
                    storage_key=object_store._storage_key(digest),
                    event_inputs=tuple(event_inputs),
                    skipped_event_count=skipped_events,
                    binding_candidates=tuple(candidates),
                )
            )
        return tuple(prepared_rows)

    @staticmethod
    def _initialize_or_resume(
        *,
        target_connection: sqlite3.Connection,
        binding: MigrationInputBinding,
        report: MigrationScanReport,
        plan: MigrationSafeCopyPlan,
        prepared: Sequence[_PreparedMigrationRaw],
        migration_run_id: str,
        clock: Callable[[], str],
    ) -> tuple[MigrationSafeCopyCheckpoint, str]:
        initial = MigrationSafeCopyCheckpoint(MIGRATION_V1_COPIER_VERSION, None)
        initial_checkpoint = _canonical_json_text(initial.canonical_value())
        initial_counters = _canonical_json_text(_copy_counters(prepared, copied_count=0))
        source_revision_sha256 = _digest({"source_revision": binding.source_revision})
        row = target_connection.execute(
            """SELECT mode, source_revision_sha256, source_schema_sha256,
                      plan_sha256, status, checkpoint_json, counters_json,
                      findings_sha256, report_sha256
               FROM raw_migration_runs_v2 WHERE migration_run_id=?""",
            (migration_run_id,),
        ).fetchone()
        if row is not None:
            expected_static = (
                "fixture",
                source_revision_sha256,
                binding.logical_schema_sha256,
                plan.plan_sha256,
            )
            if (
                tuple(row[:4]) != expected_static
                or row[7] != report.classification_set_sha256
                or row[8] is not None
            ):
                _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "migration run authority conflicts")
            checkpoint = _checkpoint_from_text(row[5])
            status = str(row[4])
            if status not in {"classified", "copied", "failed"}:
                _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "migration run status changed")
            copied_count = RawV1SafeCopier._resume_index(prepared, checkpoint)
            if status == "copied" and copied_count != len(prepared):
                _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "copied checkpoint is incomplete")
            expected_counters = _canonical_json_text(
                _copy_counters(prepared, copied_count=copied_count)
            )
            if row[6] != expected_counters:
                _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration resume counters changed")
            return checkpoint, status
        started_at = canonical_timestamp(clock())
        try:
            target_connection.execute("BEGIN IMMEDIATE")
            target_connection.execute(
                """INSERT INTO raw_migration_runs_v2(
                       migration_run_id, mode, source_revision_sha256,
                       source_schema_sha256, plan_sha256, status, checkpoint_json,
                       counters_json, findings_sha256, report_sha256, started_at,
                       completed_at
                   ) VALUES (?, 'fixture', ?, ?, ?, 'classified', ?, ?, ?, NULL, ?, NULL)""",
                (
                    migration_run_id,
                    source_revision_sha256,
                    binding.logical_schema_sha256,
                    plan.plan_sha256,
                    initial_checkpoint,
                    initial_counters,
                    report.classification_set_sha256,
                    started_at,
                ),
            )
            target_connection.commit()
        except Exception:
            if target_connection.in_transaction:
                target_connection.rollback()
            raise
        return initial, "classified"

    @staticmethod
    def _resume_index(
        prepared: Sequence[_PreparedMigrationRaw],
        checkpoint: MigrationSafeCopyCheckpoint,
    ) -> int:
        if checkpoint.last_source_row_fingerprint is None:
            return 0
        matches = [
            index
            for index, item in enumerate(prepared)
            if item.source_row_fingerprint == checkpoint.last_source_row_fingerprint
        ]
        if len(matches) != 1:
            _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "checkpoint is outside safe row order")
        return matches[0] + 1

    @staticmethod
    def _commit_batch(
        *,
        batch: Sequence[_PreparedMigrationRaw],
        copied_count: int,
        all_prepared: Sequence[_PreparedMigrationRaw],
        final: bool,
        migration_run_id: str,
        target_connection: sqlite3.Connection,
        object_store: RawV2ObjectStore,
        event_store: RawV2EventStore,
        clock: Callable[[], str],
    ) -> None:
        for item in batch:
            digest = item.object_value.raw_content_sha256  # type: ignore[attr-defined]
            if type(digest) is not str:
                raise AssertionError("safe migration object lacks content digest")
            existing_blob = object_store._fetch_blob(digest)
            if existing_blob is not None:
                object_store._assert_blob_row(
                    existing_blob,
                    digest=digest,
                    byte_length=len(item.payload),
                    storage_key=item.storage_key,
                )
                object_store._verify_blob_file(
                    object_store._root / item.storage_key,
                    item.payload,
                    digest,
                )
            else:
                object_store._publish_blob(
                    payload=item.payload,
                    digest=digest,
                    storage_key=item.storage_key,
                )
        try:
            target_connection.execute("BEGIN IMMEDIATE")
            for item in batch:
                prepared_object = item.object_value
                digest = prepared_object.raw_content_sha256  # type: ignore[attr-defined]
                blob = object_store._fetch_blob(digest)
                if blob is None:
                    target_connection.execute(
                        """INSERT INTO raw_blobs_v2(
                               blob_sha256, byte_length, storage_key, storage_state,
                               first_verified_at, last_verified_at, created_at
                           ) VALUES (?, ?, ?, 'available', ?, ?, ?)""",
                        (
                            digest,
                            len(item.payload),
                            item.storage_key,
                            prepared_object.created_at,  # type: ignore[attr-defined]
                            prepared_object.created_at,  # type: ignore[attr-defined]
                            prepared_object.created_at,  # type: ignore[attr-defined]
                        ),
                    )
                else:
                    object_store._assert_blob_row(
                        blob,
                        digest=digest,
                        byte_length=len(item.payload),
                        storage_key=item.storage_key,
                    )
                if not object_store._exact_existing_object(prepared_object):
                    object_store._insert_object(prepared_object)
                for prepared_input in item.event_inputs:
                    raw_digest, current_state = event_store._resolve_event_content(prepared_input)
                    prepared_event = event_store._prepare_identity(
                        prepared_input,
                        raw_content_sha256=raw_digest,
                    )
                    if not event_store._exact_existing_event(prepared_event):
                        event_store._require_new_event_state(
                            prepared_input,
                            current_object_state=current_state,
                            raw_content_sha256=raw_digest,
                        )
                        event_store._insert_event(prepared_event)
            checkpoint = MigrationSafeCopyCheckpoint(
                MIGRATION_V1_COPIER_VERSION,
                batch[-1].source_row_fingerprint,
            )
            counters = _copy_counters(all_prepared, copied_count=copied_count)
            status = "copied" if final else "classified"
            completed_at = canonical_timestamp(clock()) if final else None
            target_connection.execute(
                """UPDATE raw_migration_runs_v2
                   SET status=?, checkpoint_json=?, counters_json=?, completed_at=?
                   WHERE migration_run_id=?""",
                (
                    status,
                    _canonical_json_text(checkpoint.canonical_value()),
                    _canonical_json_text(counters),
                    completed_at,
                    migration_run_id,
                ),
            )
            target_connection.commit()
        except Exception:
            if target_connection.in_transaction:
                target_connection.rollback()
            raise

    @staticmethod
    def _verify_prepared(
        *,
        prepared: Sequence[_PreparedMigrationRaw],
        object_store: RawV2ObjectStore,
        event_store: RawV2EventStore,
    ) -> None:
        for item in prepared:
            prepared_object = item.object_value
            digest = prepared_object.raw_content_sha256  # type: ignore[attr-defined]
            blob = object_store._fetch_blob(digest)
            if blob is None:
                _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "copied blob row is missing")
            object_store._assert_blob_row(
                blob,
                digest=digest,
                byte_length=len(item.payload),
                storage_key=item.storage_key,
            )
            object_store._verify_blob_file(
                object_store._root / item.storage_key,
                item.payload,
                digest,
            )
            if not object_store._exact_existing_object(prepared_object):
                _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "copied object row is missing")
            for prepared_input in item.event_inputs:
                raw_digest, _ = event_store._resolve_event_content(prepared_input)
                prepared_event = event_store._prepare_identity(
                    prepared_input,
                    raw_content_sha256=raw_digest,
                )
                if not event_store._exact_existing_event(prepared_event):
                    _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "copied event row is missing")

    @staticmethod
    def _finish_empty_run(
        *,
        target_connection: sqlite3.Connection,
        migration_run_id: str,
        clock: Callable[[], str],
    ) -> None:
        target_connection.execute("BEGIN IMMEDIATE")
        target_connection.execute(
            "UPDATE raw_migration_runs_v2 SET status='copied', completed_at=? WHERE migration_run_id=?",
            (canonical_timestamp(clock()), migration_run_id),
        )
        target_connection.commit()

    @staticmethod
    def _validate_source_end(source: sqlite3.Connection, binding: MigrationInputBinding) -> None:
        schema_sha256 = _digest(
            _schema_profile(source, failure_reason=RawV2Reason.MIGRATION_INPUT_CHANGED)
        )
        rows = _read_rows(source)
        watermark = _row_watermark_sha256(rows, blob_root=Path(binding.blob_root))
        snapshot = _source_snapshot_sha256(
            logical_schema_sha256=schema_sha256,
            row_watermark_sha256=watermark,
            source_claim_set_sha256=binding.source_claim_set_sha256,
        )
        if (
            schema_sha256 != binding.logical_schema_sha256
            or watermark != binding.row_watermark_sha256
            or snapshot != binding.source_snapshot_sha256
        ):
            _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "source changed during safe copy")

    @staticmethod
    def _mark_failed(
        *,
        target_connection: sqlite3.Connection,
        migration_run_id: str,
        clock: Callable[[], str],
    ) -> None:
        row = target_connection.execute(
            "SELECT 1 FROM raw_migration_runs_v2 WHERE migration_run_id=?",
            (migration_run_id,),
        ).fetchone()
        if row is None:
            return
        target_connection.execute("BEGIN IMMEDIATE")
        target_connection.execute(
            "UPDATE raw_migration_runs_v2 SET status='failed', completed_at=? WHERE migration_run_id=?",
            (canonical_timestamp(clock()), migration_run_id),
        )
        target_connection.commit()

    @staticmethod
    def _result(
        *,
        target_connection: sqlite3.Connection,
        migration_run_id: str,
        plan: MigrationSafeCopyPlan,
        prepared: Sequence[_PreparedMigrationRaw],
    ) -> MigrationSafeCopyResult:
        row = target_connection.execute(
            "SELECT status, checkpoint_json, counters_json FROM raw_migration_runs_v2 WHERE migration_run_id=?",
            (migration_run_id,),
        ).fetchone()
        if row is None or row[0] != "copied":
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration run is not copied")
        checkpoint = _checkpoint_from_text(row[1])
        counters_expected = _copy_counters(prepared, copied_count=len(prepared))
        counters_text = _canonical_json_text(counters_expected)
        if row[2] != counters_text:
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration counters changed")
        object_ids = [item.object_value.raw_object_id for item in prepared]  # type: ignore[attr-defined]
        event_ids: list[str] = []
        candidates = tuple(candidate for item in prepared for candidate in item.binding_candidates)
        for item in prepared:
            for prepared_input in item.event_inputs:
                raw_digest = item.object_value.raw_content_sha256  # type: ignore[attr-defined]
                event_ids.append(
                    RawV2EventStore._prepare_identity(
                        prepared_input,
                        raw_content_sha256=raw_digest,
                    ).event_id
                )
        return MigrationSafeCopyResult(
            migration_run_id=migration_run_id,
            plan_sha256=plan.plan_sha256,
            status="copied",
            copied_object_count=counters_expected["copied_object_count"],
            copied_event_count=counters_expected["copied_event_count"],
            skipped_event_count=counters_expected["skipped_event_count"],
            binding_candidate_count=counters_expected["binding_candidate_count"],
            object_set_sha256=_digest(object_ids),
            event_set_sha256=_digest(event_ids),
            binding_candidate_set_sha256=_digest([item.canonical_value() for item in candidates]),
            checkpoint_sha256=_digest(checkpoint.canonical_value()),
            counters_sha256=_digest(counters_expected),
            binding_candidates=candidates,
        )
