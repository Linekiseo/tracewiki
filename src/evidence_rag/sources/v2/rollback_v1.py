"""Fixture-only M1-M7 rollback drill and portable BF-14 matrix.

The drill operates on caller-owned SQLite/blob fixtures. It never discovers,
opens, or mutates application, mirror, or production state. A successful
execution discards the exact raw V2 fixture, returns the built-in V1-only
runtime policy, and proves the legacy V1 authority digest is unchanged.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .contracts import (
    RawV2ContractError,
    canonical_json_bytes,
    exact_bytes_sha256,
    strict_json_loads,
    validate_sha256_digest,
)
from .migration_artifact_v1 import (
    MigrationArtifactStatus,
    RawV1MigrationArtifactVerifier,
)
from .migration_v1 import (
    MigrationInputBinding,
    MigrationInputMode,
    MigrationSafeCopyPlan,
    MigrationSafeCopyResult,
    MigrationScanReport,
    MigrationScanStatus,
    MigrationSeverity,
    RawV1MigrationScanner,
)
from .runtime_control import (
    RawV2ReadMode,
    RawV2RuntimePolicy,
    RawV2RuntimeScope,
    RawV2WriteMode,
)
from .schema import RAW_V2_INDEXES, RAW_V2_TABLES, inspect_raw_v2_schema

RAW_V2_ROLLBACK_DRILL_VERSION = "raw-v2-fixture-rollback-drill-v1"
RAW_V2_ROLLBACK_MATRIX_SCHEMA_VERSION = "rag-g1-raw-rollback-drill-v1"
RAW_V2_ROLLBACK_MATRIX_ARTIFACT_VERSION = "raw-v2-fixture-m1-m7-matrix-v1"

_V1_TABLES = (
    "blocked_entities",
    "raw_derivations",
    "raw_objects",
    "source_events",
)
_BINDING_AUTHORITY = object()
_RESULT_AUTHORITY = object()
_MATRIX_LIMITATIONS = (
    "fixture-only-m1-m7",
    "no-mirror-or-formal-database-access",
    "no-application-runtime-activation",
    "no-g8-production-rollback-authority",
    "no-cutover-or-release-authority",
)


class RawV2RollbackInputMode(StrEnum):
    FIXTURE = "fixture"
    MIRROR = "mirror"
    AUTHORIZED_PRODUCTION = "authorized-production"


class RawV2RollbackStage(StrEnum):
    M1_SCHEMA_PREPARED = "M1_SCHEMA_PREPARED"
    M2_V1_SCANNED = "M2_V1_SCANNED"
    M3_CLASSIFIED = "M3_CLASSIFIED"
    M4_SAFE_ROWS_COPIED = "M4_SAFE_ROWS_COPIED"
    M5_COUNTS_AND_DIGESTS_VERIFIED = "M5_COUNTS_AND_DIGESTS_VERIFIED"
    M6_DUAL_WRITE_DARK = "M6_DUAL_WRITE_DARK"
    M7_V2_SHADOW_READ = "M7_V2_SHADOW_READ"


_STAGES = tuple(RawV2RollbackStage)
_STAGE_RANK = {stage: index + 1 for index, stage in enumerate(_STAGES)}


class RawV2RollbackStatus(StrEnum):
    ROLLBACK_PASS = "ROLLBACK_PASS"


class RawV2RollbackAction(StrEnum):
    DISABLE_V2_WRITE = "disable_v2_write"
    DISABLE_V2_READ = "disable_v2_read"
    RETAIN_RUNTIME_EVIDENCE = "retain_runtime_evidence"
    DROP_V2_FIXTURE_SCHEMA = "drop_v2_fixture_schema"
    REMOVE_V2_FIXTURE_BLOBS = "remove_v2_fixture_blobs"
    VERIFY_V1_UNCHANGED = "verify_v1_unchanged"


class RawV2RollbackErrorCode(StrEnum):
    NOT_AUTHORIZED = "rollback_not_authorized"
    CONTRACT_INVALID = "rollback_contract_invalid"
    V1_AUTHORITY_CHANGED = "rollback_v1_authority_changed"
    STAGE_EVIDENCE_INVALID = "rollback_stage_evidence_invalid"
    V2_FIXTURE_INVALID = "rollback_v2_fixture_invalid"
    V2_BLOB_INVALID = "rollback_v2_blob_invalid"
    ROLLBACK_INCOMPLETE = "rollback_incomplete"
    MATRIX_INVALID = "rollback_matrix_invalid"


class RawV2RollbackError(ValueError):
    """Typed rollback rejection without echoing fixture or policy values."""

    def __init__(self, code: RawV2RollbackErrorCode) -> None:
        if type(code) is not RawV2RollbackErrorCode:
            raise TypeError("code must be an exact RawV2RollbackErrorCode")
        self.code = code
        super().__init__(code.value)


def _fail(code: RawV2RollbackErrorCode) -> None:
    raise RawV2RollbackError(code)


def _digest(value: object, *, allow_none: bool = False) -> str:
    return exact_bytes_sha256(canonical_json_bytes(value, allow_none=allow_none))


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _normalize_sql(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)
    return " ".join(value.replace("\r\n", "\n").replace("\r", "\n").split()).removesuffix(";")


def _sqlite_value(value: object) -> object:
    if value is None or type(value) in {str, int, bool}:
        return value
    if type(value) is bytes:
        raw = bytes(value)
        return {
            "sqlite_blob_length": len(raw),
            "sqlite_blob_sha256": exact_bytes_sha256(raw),
        }
    _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)


def _require_connection(value: object) -> sqlite3.Connection:
    if not isinstance(value, sqlite3.Connection):
        _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)
    return value


def _require_callable(value: object) -> None:
    if not callable(value):
        _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)


@dataclass(frozen=True, slots=True)
class RawV1AuthoritySnapshot:
    schema_sha256: str
    data_sha256: str
    authority_sha256: str
    table_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        for value in (self.schema_sha256, self.data_sha256, self.authority_sha256):
            try:
                validate_sha256_digest(value)
            except RawV2ContractError:
                _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)
        if (
            type(self.table_counts) is not tuple
            or tuple(name for name, _count in self.table_counts) != _V1_TABLES
            or any(type(count) is not int or count < 0 for _name, count in self.table_counts)
        ):
            _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)

    def canonical_value(self) -> dict[str, object]:
        return {
            "authority_sha256": self.authority_sha256,
            "data_sha256": self.data_sha256,
            "schema_sha256": self.schema_sha256,
            "table_counts": [list(item) for item in self.table_counts],
        }


def snapshot_v1_authority(connection: sqlite3.Connection) -> RawV1AuthoritySnapshot:
    """Return a digest-only snapshot of every reviewed legacy V1 schema object and row."""

    database = _require_connection(connection)
    table_rows = database.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?) ORDER BY name",
        _V1_TABLES,
    ).fetchall()
    if tuple(str(row[0]) for row in table_rows) != _V1_TABLES:
        _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)

    placeholders = ",".join("?" for _ in _V1_TABLES)
    authored = database.execute(
        f"""SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name IN ({placeholders}) OR tbl_name IN ({placeholders})
            ORDER BY type, name""",
        (*_V1_TABLES, *_V1_TABLES),
    ).fetchall()
    schema_projection: list[dict[str, object]] = []
    for object_type, name, table_name, sql in authored:
        if not all(type(item) is str for item in (object_type, name, table_name)):
            _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)
        schema_projection.append(
            {
                "name": name,
                "sql": _normalize_sql(sql),
                "table_name": table_name,
                "type": object_type,
            }
        )
    for table in _V1_TABLES:
        columns = database.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        if not columns:
            _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)
        schema_projection.append(
            {
                "columns": [
                    {
                        "default": _sqlite_value(row[4]),
                        "name": str(row[1]),
                        "not_null": bool(row[3]),
                        "primary_key_order": int(row[5]),
                        "type": str(row[2]),
                    }
                    for row in columns
                ],
                "foreign_keys": [
                    [
                        int(row[0]),
                        int(row[1]),
                        str(row[2]),
                        str(row[3]),
                        str(row[4]),
                        str(row[5]),
                        str(row[6]),
                        str(row[7]),
                    ]
                    for row in database.execute(
                        f"PRAGMA foreign_key_list({_quote_identifier(table)})"
                    ).fetchall()
                ],
                "table": table,
            }
        )

    data_projection: list[dict[str, object]] = []
    table_counts: list[tuple[str, int]] = []
    for table in _V1_TABLES:
        info = database.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        columns = tuple(str(row[1]) for row in info)
        primary = tuple(
            str(row[1])
            for row in sorted((row for row in info if int(row[5]) > 0), key=lambda row: int(row[5]))
        )
        order = primary or columns
        select_columns = ",".join(_quote_identifier(item) for item in columns)
        order_columns = ",".join(_quote_identifier(item) for item in order)
        rows = database.execute(
            f"SELECT {select_columns} FROM {_quote_identifier(table)} ORDER BY {order_columns}"
        ).fetchall()
        table_counts.append((table, len(rows)))
        data_projection.append(
            {
                "columns": list(columns),
                "rows": [[_sqlite_value(value) for value in row] for row in rows],
                "table": table,
            }
        )

    schema_sha256 = _digest(schema_projection, allow_none=True)
    data_sha256 = _digest(data_projection, allow_none=True)
    counts = tuple(table_counts)
    authority_sha256 = _digest(
        {
            "data_sha256": data_sha256,
            "schema_sha256": schema_sha256,
            "table_counts": [list(item) for item in counts],
        }
    )
    return RawV1AuthoritySnapshot(schema_sha256, data_sha256, authority_sha256, counts)


@dataclass(frozen=True, slots=True)
class _V2FixtureState:
    schema_sha256: str
    row_set_sha256: str
    state_sha256: str
    table_counts: tuple[tuple[str, int], ...]
    scoped_object_count: int


def _v2_fixture_state(connection: sqlite3.Connection) -> _V2FixtureState:
    database = _require_connection(connection)
    try:
        schema = inspect_raw_v2_schema(database)
    except Exception as error:
        raise RawV2RollbackError(RawV2RollbackErrorCode.V2_FIXTURE_INVALID) from error
    projections: list[dict[str, object]] = []
    counts: list[tuple[str, int]] = []
    for table in RAW_V2_TABLES:
        info = database.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
        columns = tuple(str(row[1]) for row in info)
        selected = ",".join(_quote_identifier(item) for item in columns)
        ordered = ",".join(_quote_identifier(item) for item in columns)
        rows = database.execute(
            f"SELECT {selected} FROM {_quote_identifier(table)} ORDER BY {ordered}"
        ).fetchall()
        counts.append((table, len(rows)))
        projections.append(
            {
                "rows": [[_sqlite_value(value) for value in row] for row in rows],
                "table": table,
            }
        )
    names = (*RAW_V2_TABLES, *RAW_V2_INDEXES)
    placeholders = ",".join("?" for _ in names)
    table_placeholders = ",".join("?" for _ in RAW_V2_TABLES)
    scoped_count = int(
        database.execute(
            f"""SELECT COUNT(*) FROM sqlite_master
                WHERE name IN ({placeholders}) OR tbl_name IN ({table_placeholders})""",
            (*names, *RAW_V2_TABLES),
        ).fetchone()[0]
    )
    table_counts = tuple(counts)
    row_set_sha256 = _digest(projections, allow_none=True)
    state_sha256 = _digest(
        {
            "row_set_sha256": row_set_sha256,
            "schema_sha256": schema.schema_sha256,
            "scoped_object_count": scoped_count,
            "table_counts": [list(item) for item in table_counts],
        }
    )
    return _V2FixtureState(
        schema_sha256=schema.schema_sha256,
        row_set_sha256=row_set_sha256,
        state_sha256=state_sha256,
        table_counts=table_counts,
        scoped_object_count=scoped_count,
    )


@dataclass(frozen=True, slots=True)
class _BlobFixtureState:
    root: Path
    relative_files: tuple[str, ...]
    file_count: int
    total_bytes: int
    set_sha256: str


def _fixture_root(value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
    try:
        root = value.resolve(strict=True)
    except OSError:
        _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
    if root != value or not root.is_dir() or root.is_symlink():
        _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
    current = root
    while current != current.parent:
        if current.is_symlink():
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        current = current.parent
    return root


def _portable_storage_key(value: object) -> str:
    if type(value) is not str or not value or "\\" in value:
        _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
    key = PurePosixPath(value)
    if key.is_absolute() or any(part in {"", ".", ".."} for part in key.parts):
        _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
    return key.as_posix()


def _blob_fixture_state(connection: sqlite3.Connection, root_value: Path) -> _BlobFixtureState:
    database = _require_connection(connection)
    root = _fixture_root(root_value)
    rows = database.execute(
        """SELECT blob_sha256, byte_length, storage_key
           FROM raw_blobs_v2 ORDER BY storage_key"""
    ).fetchall()
    manifest: list[dict[str, object]] = []
    relative_files: list[str] = []
    total_bytes = 0
    for blob_sha256, byte_length, storage_key in rows:
        key = _portable_storage_key(storage_key)
        try:
            digest = validate_sha256_digest(blob_sha256)
        except RawV2ContractError:
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        if type(byte_length) is not int or byte_length < 0:
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        path = root.joinpath(*PurePosixPath(key).parts)
        try:
            metadata = path.lstat()
        except OSError:
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            payload = path.read_bytes()
        except (OSError, ValueError):
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        if len(payload) != byte_length or exact_bytes_sha256(payload) != digest:
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        relative_files.append(key)
        total_bytes += byte_length
        manifest.append(
            {
                "blob_sha256": digest,
                "byte_length": byte_length,
                "storage_key_sha256": _digest(key),
            }
        )
    if len(relative_files) != len(set(relative_files)):
        _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)

    observed: list[str] = []
    for path in sorted(root.rglob("*")):
        try:
            metadata = path.lstat()
        except OSError:
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        if stat.S_ISLNK(metadata.st_mode):
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
        observed.append(path.relative_to(root).as_posix())
    if tuple(observed) != tuple(relative_files):
        _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
    return _BlobFixtureState(
        root=root,
        relative_files=tuple(relative_files),
        file_count=len(relative_files),
        total_bytes=total_bytes,
        set_sha256=_digest(manifest),
    )


def _scan_evidence(
    *,
    connection: sqlite3.Connection,
    binding: MigrationInputBinding,
    report: MigrationScanReport | None,
) -> dict[str, object]:
    if type(binding) is not MigrationInputBinding or binding.mode is not MigrationInputMode.FIXTURE:
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    try:
        rebound = RawV1MigrationScanner.freeze_fixture(
            connection=connection,
            dataset_id=binding.dataset_id,
            source_revision=binding.source_revision,
            blob_root=binding.blob_root,
            source_claims=binding.source_claims,
        )
    except Exception as error:
        raise RawV2RollbackError(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID) from error
    if rebound != binding:
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    value: dict[str, object] = {
        "migration_input_sha256": _digest(
            {
                "allowed_operations": [item.value for item in binding.allowed_operations],
                "blob_root_sha256": _digest(binding.blob_root),
                "dataset_id": binding.dataset_id,
                "forbidden_operations": [item.value for item in binding.forbidden_operations],
                "logical_schema_sha256": binding.logical_schema_sha256,
                "mode": binding.mode.value,
                "row_watermark_sha256": binding.row_watermark_sha256,
                "source_claim_set_sha256": binding.source_claim_set_sha256,
                "source_claims": [item.canonical_value() for item in binding.source_claims],
                "source_revision": binding.source_revision,
                "source_snapshot_sha256": binding.source_snapshot_sha256,
            }
        )
    }
    if report is None:
        return value
    if (
        type(report) is not MigrationScanReport
        or report.dataset_id != binding.dataset_id
        or report.source_revision != binding.source_revision
        or report.start_watermark_sha256 != binding.row_watermark_sha256
        or report.end_watermark_sha256 != binding.row_watermark_sha256
        or report.source_before_sha256 != binding.source_snapshot_sha256
        or report.source_after_sha256 != binding.source_snapshot_sha256
        or not report.stable_input
        or report.unclassified_count != 0
        or report.status is MigrationScanStatus.DRY_RUN_FAIL
        or any(item.severity is MigrationSeverity.P0 for item in report.classifications)
    ):
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    value["scan_identity_sha256"] = report.identity_sha256
    return value


def _copy_evidence(
    *,
    v2_state: _V2FixtureState,
    binding: MigrationInputBinding,
    report: MigrationScanReport,
    plan: MigrationSafeCopyPlan,
    result: MigrationSafeCopyResult,
) -> dict[str, object]:
    if (
        type(plan) is not MigrationSafeCopyPlan
        or type(result) is not MigrationSafeCopyResult
        or plan.source_snapshot_sha256 != binding.source_snapshot_sha256
        or plan.scan_identity_sha256 != report.identity_sha256
        or plan.target_schema_sha256 != v2_state.schema_sha256
        or result.plan_sha256 != plan.plan_sha256
        or result.status != "copied"
    ):
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    counts = dict(v2_state.table_counts)
    if (
        result.copied_object_count != counts["raw_objects_v2"]
        or result.copied_event_count != counts["source_events_v2"]
        or counts["raw_migration_runs_v2"] != 1
        or result.copied_object_count <= 0
    ):
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    return {
        "copy_plan_sha256": plan.plan_sha256,
        "copy_result_sha256": result.identity_sha256,
    }


def _artifact_evidence(
    *,
    artifact: bytes,
    binding: MigrationInputBinding,
) -> dict[str, object]:
    if type(artifact) is not bytes:
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    try:
        verification = RawV1MigrationArtifactVerifier.verify_bytes(
            artifact,
            expected_source_snapshot_sha256=binding.source_snapshot_sha256,
        )
        value = strict_json_loads(artifact)
    except Exception as error:
        raise RawV2RollbackError(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID) from error
    if (
        verification.status is not MigrationArtifactStatus.DRY_RUN_PASS
        or type(value) is not dict
        or value.get("rollback_ready") is not True
    ):
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    return {
        "migration_artifact_content_sha256": verification.content_sha256,
        "migration_artifact_verification_sha256": verification.verification_sha256,
    }


def _runtime_evidence(
    *,
    stage: RawV2RollbackStage,
    policy: RawV2RuntimePolicy,
    scope: RawV2RuntimeScope,
    retained_evidence_sha256: str,
) -> dict[str, object]:
    if type(policy) is not RawV2RuntimePolicy or type(scope) is not RawV2RuntimeScope:
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    try:
        evidence = validate_sha256_digest(retained_evidence_sha256)
    except RawV2ContractError:
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    modes = policy.modes_for(scope)
    if stage is RawV2RollbackStage.M6_DUAL_WRITE_DARK:
        if (
            modes.write_mode is not RawV2WriteMode.DUAL_WRITE_DARK
            or modes.read_mode is not RawV2ReadMode.V1
        ):
            _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    elif stage is RawV2RollbackStage.M7_V2_SHADOW_READ:
        if (
            modes.write_mode is not RawV2WriteMode.DUAL_WRITE_DARK
            or modes.read_mode is not RawV2ReadMode.V2_SHADOW
        ):
            _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    else:
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)
    return {
        "active_policy_sha256": policy.content_sha256,
        "retained_evidence_sha256": evidence,
        "scope_sha256": scope.scope_sha256,
    }


def _validate_exact_evidence_shape(
    *,
    stage: RawV2RollbackStage,
    migration_binding: MigrationInputBinding | None,
    scan_report: MigrationScanReport | None,
    copy_plan: MigrationSafeCopyPlan | None,
    copy_result: MigrationSafeCopyResult | None,
    migration_artifact: bytes | None,
    runtime_policy: RawV2RuntimePolicy | None,
    runtime_scope: RawV2RuntimeScope | None,
    retained_evidence_sha256: str | None,
) -> None:
    rank = _STAGE_RANK[stage]
    required = (
        rank >= 2,
        rank >= 3,
        rank >= 4,
        rank >= 4,
        rank >= 5,
        rank >= 6,
        rank >= 6,
        rank >= 6,
    )
    observed = tuple(
        value is not None
        for value in (
            migration_binding,
            scan_report,
            copy_plan,
            copy_result,
            migration_artifact,
            runtime_policy,
            runtime_scope,
            retained_evidence_sha256,
        )
    )
    if observed != required:
        _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)


@dataclass(frozen=True, slots=True, init=False)
class RawV2FixtureRollbackBinding:
    stage: RawV2RollbackStage
    m0_v1_authority: RawV1AuthoritySnapshot
    v2_state_sha256: str
    v2_schema_sha256: str
    v2_scoped_object_count: int
    v2_blob_set_sha256: str
    v2_blob_file_count: int
    v2_blob_total_bytes: int
    stage_evidence_sha256: str
    active_policy_sha256: str | None
    retained_evidence_sha256: str | None
    blob_root: str
    blob_relative_files: tuple[str, ...]
    _authority: object

    def __init__(
        self,
        *,
        _authority: object,
        stage: RawV2RollbackStage,
        m0_v1_authority: RawV1AuthoritySnapshot,
        v2_state: _V2FixtureState,
        blob_state: _BlobFixtureState,
        stage_evidence_sha256: str,
        active_policy_sha256: str | None,
        retained_evidence_sha256: str | None,
    ) -> None:
        if _authority is not _BINDING_AUTHORITY:
            raise TypeError("rollback binding must be created by RawV2RollbackDrill.freeze")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "m0_v1_authority", m0_v1_authority)
        object.__setattr__(self, "v2_state_sha256", v2_state.state_sha256)
        object.__setattr__(self, "v2_schema_sha256", v2_state.schema_sha256)
        object.__setattr__(self, "v2_scoped_object_count", v2_state.scoped_object_count)
        object.__setattr__(self, "v2_blob_set_sha256", blob_state.set_sha256)
        object.__setattr__(self, "v2_blob_file_count", blob_state.file_count)
        object.__setattr__(self, "v2_blob_total_bytes", blob_state.total_bytes)
        object.__setattr__(self, "stage_evidence_sha256", stage_evidence_sha256)
        object.__setattr__(self, "active_policy_sha256", active_policy_sha256)
        object.__setattr__(self, "retained_evidence_sha256", retained_evidence_sha256)
        object.__setattr__(self, "blob_root", os.fspath(blob_state.root))
        object.__setattr__(self, "blob_relative_files", blob_state.relative_files)
        object.__setattr__(self, "_authority", _authority)


def _actions(stage: RawV2RollbackStage) -> tuple[RawV2RollbackAction, ...]:
    actions = [
        RawV2RollbackAction.DISABLE_V2_WRITE,
        RawV2RollbackAction.DISABLE_V2_READ,
    ]
    if _STAGE_RANK[stage] >= 6:
        actions.append(RawV2RollbackAction.RETAIN_RUNTIME_EVIDENCE)
    actions.extend(
        (
            RawV2RollbackAction.DROP_V2_FIXTURE_SCHEMA,
            RawV2RollbackAction.REMOVE_V2_FIXTURE_BLOBS,
            RawV2RollbackAction.VERIFY_V1_UNCHANGED,
        )
    )
    return tuple(actions)


@dataclass(frozen=True, slots=True, init=False)
class RawV2RollbackResult:
    stage: RawV2RollbackStage
    status: RawV2RollbackStatus
    m0_v1_authority_sha256: str
    v1_before_authority_sha256: str
    v1_after_authority_sha256: str
    stage_evidence_sha256: str
    v2_before_state_sha256: str
    v2_before_scoped_object_count: int
    v2_before_blob_file_count: int
    v2_before_blob_total_bytes: int
    v2_after_state_sha256: str
    v2_after_scoped_object_count: int
    v2_after_blob_file_count: int
    active_policy_sha256: str | None
    rollback_policy_sha256: str
    retained_evidence_sha256: str | None
    write_disabled: bool
    read_disabled: bool
    reverse_v1_write_count: int
    formal_table_drop_count: int
    actions: tuple[RawV2RollbackAction, ...]
    result_sha256: str
    _authority: object

    def __init__(self, *, _authority: object, values: Mapping[str, object]) -> None:
        if _authority is not _RESULT_AUTHORITY:
            raise TypeError("rollback result must be created by RawV2RollbackDrill.execute")
        for field_name, value in values.items():
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "_authority", _authority)

    def canonical_value(self) -> dict[str, object]:
        return {
            "actions": [item.value for item in self.actions],
            "active_policy_sha256": self.active_policy_sha256,
            "formal_table_drop_count": self.formal_table_drop_count,
            "m0_v1_authority_sha256": self.m0_v1_authority_sha256,
            "read_disabled": self.read_disabled,
            "result_sha256": self.result_sha256,
            "retained_evidence_sha256": self.retained_evidence_sha256,
            "reverse_v1_write_count": self.reverse_v1_write_count,
            "rollback_policy_sha256": self.rollback_policy_sha256,
            "stage": self.stage.value,
            "stage_evidence_sha256": self.stage_evidence_sha256,
            "status": self.status.value,
            "v1_after_authority_sha256": self.v1_after_authority_sha256,
            "v1_before_authority_sha256": self.v1_before_authority_sha256,
            "v2_after_blob_file_count": self.v2_after_blob_file_count,
            "v2_after_scoped_object_count": self.v2_after_scoped_object_count,
            "v2_after_state_sha256": self.v2_after_state_sha256,
            "v2_before_blob_file_count": self.v2_before_blob_file_count,
            "v2_before_blob_total_bytes": self.v2_before_blob_total_bytes,
            "v2_before_scoped_object_count": self.v2_before_scoped_object_count,
            "v2_before_state_sha256": self.v2_before_state_sha256,
            "write_disabled": self.write_disabled,
        }


@dataclass(frozen=True, slots=True)
class RawV2RollbackExecution:
    policy: RawV2RuntimePolicy
    result: RawV2RollbackResult

    def __post_init__(self) -> None:
        if (
            type(self.policy) is not RawV2RuntimePolicy
            or type(self.result) is not RawV2RollbackResult
        ):
            _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)


@dataclass(frozen=True, slots=True)
class RawV2RollbackMatrixVerification:
    status: RawV2RollbackStatus
    content_sha256: str
    stage_result_set_sha256: str
    invariant_result_sha256: str
    verification_sha256: str


class RawV2RollbackDrill:
    """Freeze and execute one exact fixture rollback after an injected M1-M7 failure."""

    @staticmethod
    def freeze(
        *,
        input_mode: RawV2RollbackInputMode,
        stage: RawV2RollbackStage,
        m0_v1_authority: RawV1AuthoritySnapshot,
        v1_opener: Callable[[], sqlite3.Connection],
        v2_opener: Callable[[], sqlite3.Connection],
        v2_blob_root: Path,
        migration_binding: MigrationInputBinding | None = None,
        scan_report: MigrationScanReport | None = None,
        copy_plan: MigrationSafeCopyPlan | None = None,
        copy_result: MigrationSafeCopyResult | None = None,
        migration_artifact: bytes | None = None,
        runtime_policy: RawV2RuntimePolicy | None = None,
        runtime_scope: RawV2RuntimeScope | None = None,
        retained_evidence_sha256: str | None = None,
    ) -> RawV2FixtureRollbackBinding:
        if (
            type(input_mode) is not RawV2RollbackInputMode
            or input_mode is not RawV2RollbackInputMode.FIXTURE
        ):
            _fail(RawV2RollbackErrorCode.NOT_AUTHORIZED)
        if (
            type(stage) is not RawV2RollbackStage
            or type(m0_v1_authority) is not RawV1AuthoritySnapshot
        ):
            _fail(RawV2RollbackErrorCode.CONTRACT_INVALID)
        _require_callable(v1_opener)
        _require_callable(v2_opener)
        _validate_exact_evidence_shape(
            stage=stage,
            migration_binding=migration_binding,
            scan_report=scan_report,
            copy_plan=copy_plan,
            copy_result=copy_result,
            migration_artifact=migration_artifact,
            runtime_policy=runtime_policy,
            runtime_scope=runtime_scope,
            retained_evidence_sha256=retained_evidence_sha256,
        )

        v1 = _require_connection(v1_opener())
        v2: sqlite3.Connection | None = None
        try:
            v2 = _require_connection(v2_opener())
            current_v1 = snapshot_v1_authority(v1)
            if current_v1 != m0_v1_authority:
                _fail(RawV2RollbackErrorCode.V1_AUTHORITY_CHANGED)
            if v2.in_transaction:
                _fail(RawV2RollbackErrorCode.V2_FIXTURE_INVALID)
            v2_state = _v2_fixture_state(v2)
            blob_state = _blob_fixture_state(v2, v2_blob_root)
            rank = _STAGE_RANK[stage]
            row_total = sum(count for _table, count in v2_state.table_counts)
            if (rank <= 3 and (row_total != 0 or blob_state.file_count != 0)) or (
                rank >= 4 and row_total == 0
            ):
                _fail(RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID)

            evidence: dict[str, object] = {
                "m0_v1_authority_sha256": m0_v1_authority.authority_sha256,
                "stage": stage.value,
                "v2_blob_set_sha256": blob_state.set_sha256,
                "v2_state_sha256": v2_state.state_sha256,
            }
            if rank >= 2:
                assert migration_binding is not None
                evidence.update(
                    _scan_evidence(
                        connection=v1,
                        binding=migration_binding,
                        report=scan_report,
                    )
                )
            if rank >= 4:
                assert migration_binding is not None
                assert scan_report is not None
                assert copy_plan is not None
                assert copy_result is not None
                evidence.update(
                    _copy_evidence(
                        v2_state=v2_state,
                        binding=migration_binding,
                        report=scan_report,
                        plan=copy_plan,
                        result=copy_result,
                    )
                )
            if rank >= 5:
                assert migration_binding is not None
                assert migration_artifact is not None
                evidence.update(
                    _artifact_evidence(artifact=migration_artifact, binding=migration_binding)
                )
            active_policy_sha256: str | None = None
            retained: str | None = None
            if rank >= 6:
                assert runtime_policy is not None
                assert runtime_scope is not None
                assert retained_evidence_sha256 is not None
                runtime = _runtime_evidence(
                    stage=stage,
                    policy=runtime_policy,
                    scope=runtime_scope,
                    retained_evidence_sha256=retained_evidence_sha256,
                )
                evidence.update(runtime)
                active_policy_sha256 = str(runtime["active_policy_sha256"])
                retained = str(runtime["retained_evidence_sha256"])
            return RawV2FixtureRollbackBinding(
                _authority=_BINDING_AUTHORITY,
                stage=stage,
                m0_v1_authority=m0_v1_authority,
                v2_state=v2_state,
                blob_state=blob_state,
                stage_evidence_sha256=_digest(evidence),
                active_policy_sha256=active_policy_sha256,
                retained_evidence_sha256=retained,
            )
        finally:
            if v2 is not None and v2 is not v1:
                v2.close()
            v1.close()

    @staticmethod
    def execute(
        *,
        binding: RawV2FixtureRollbackBinding,
        v1_opener: Callable[[], sqlite3.Connection],
        v2_opener: Callable[[], sqlite3.Connection],
    ) -> RawV2RollbackExecution:
        if (
            type(binding) is not RawV2FixtureRollbackBinding
            or binding._authority is not _BINDING_AUTHORITY
        ):
            _fail(RawV2RollbackErrorCode.NOT_AUTHORIZED)
        _require_callable(v1_opener)
        _require_callable(v2_opener)
        v1 = _require_connection(v1_opener())
        v2: sqlite3.Connection | None = None
        try:
            v2 = _require_connection(v2_opener())
            before = snapshot_v1_authority(v1)
            if before != binding.m0_v1_authority:
                _fail(RawV2RollbackErrorCode.V1_AUTHORITY_CHANGED)
            if v2.in_transaction:
                _fail(RawV2RollbackErrorCode.V2_FIXTURE_INVALID)
            v2_state = _v2_fixture_state(v2)
            blob_state = _blob_fixture_state(v2, Path(binding.blob_root))
            if (
                v2_state.state_sha256 != binding.v2_state_sha256
                or v2_state.schema_sha256 != binding.v2_schema_sha256
                or v2_state.scoped_object_count != binding.v2_scoped_object_count
                or blob_state.set_sha256 != binding.v2_blob_set_sha256
                or blob_state.relative_files != binding.blob_relative_files
                or blob_state.file_count != binding.v2_blob_file_count
                or blob_state.total_bytes != binding.v2_blob_total_bytes
            ):
                _fail(RawV2RollbackErrorCode.V2_FIXTURE_INVALID)

            rollback_policy = RawV2RuntimePolicy.default()
            try:
                v2.execute("PRAGMA foreign_keys = ON")
                v2.execute("BEGIN IMMEDIATE")
                for table in reversed(RAW_V2_TABLES):
                    v2.execute(f"DROP TABLE {_quote_identifier(table)}")
                if _scoped_v2_object_count(v2) != 0:
                    _fail(RawV2RollbackErrorCode.ROLLBACK_INCOMPLETE)
                v2.commit()
            except Exception:
                if v2.in_transaction:
                    v2.rollback()
                raise

            for key in blob_state.relative_files:
                path = blob_state.root.joinpath(*PurePosixPath(key).parts)
                try:
                    metadata = path.lstat()
                    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
                        _fail(RawV2RollbackErrorCode.V2_BLOB_INVALID)
                    path.unlink()
                except OSError as error:
                    raise RawV2RollbackError(RawV2RollbackErrorCode.ROLLBACK_INCOMPLETE) from error
            directories = sorted(
                (path for path in blob_state.root.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            )
            for directory in directories:
                try:
                    directory.rmdir()
                except OSError:
                    _fail(RawV2RollbackErrorCode.ROLLBACK_INCOMPLETE)
            if any(blob_state.root.iterdir()) or _scoped_v2_object_count(v2) != 0:
                _fail(RawV2RollbackErrorCode.ROLLBACK_INCOMPLETE)

            after = snapshot_v1_authority(v1)
            if after != binding.m0_v1_authority or after != before:
                _fail(RawV2RollbackErrorCode.V1_AUTHORITY_CHANGED)
            after_state_sha256 = _digest({"blob_file_count": 0, "scoped_object_count": 0})
            unsigned: dict[str, object] = {
                "actions": _actions(binding.stage),
                "active_policy_sha256": binding.active_policy_sha256,
                "formal_table_drop_count": 0,
                "m0_v1_authority_sha256": binding.m0_v1_authority.authority_sha256,
                "read_disabled": True,
                "retained_evidence_sha256": binding.retained_evidence_sha256,
                "reverse_v1_write_count": 0,
                "rollback_policy_sha256": rollback_policy.content_sha256,
                "stage": binding.stage,
                "stage_evidence_sha256": binding.stage_evidence_sha256,
                "status": RawV2RollbackStatus.ROLLBACK_PASS,
                "v1_after_authority_sha256": after.authority_sha256,
                "v1_before_authority_sha256": before.authority_sha256,
                "v2_after_blob_file_count": 0,
                "v2_after_scoped_object_count": 0,
                "v2_after_state_sha256": after_state_sha256,
                "v2_before_blob_file_count": binding.v2_blob_file_count,
                "v2_before_blob_total_bytes": binding.v2_blob_total_bytes,
                "v2_before_scoped_object_count": binding.v2_scoped_object_count,
                "v2_before_state_sha256": binding.v2_state_sha256,
                "write_disabled": True,
            }
            canonical_unsigned = {
                key: (
                    value.value
                    if isinstance(value, StrEnum)
                    else [item.value for item in value]
                    if key == "actions"
                    else value
                )
                for key, value in unsigned.items()
            }
            values = {**unsigned, "result_sha256": _digest(canonical_unsigned, allow_none=True)}
            result = RawV2RollbackResult(_authority=_RESULT_AUTHORITY, values=values)
            return RawV2RollbackExecution(policy=rollback_policy, result=result)
        finally:
            if v2 is not None and v2 is not v1:
                v2.close()
            v1.close()


def _scoped_v2_object_count(connection: sqlite3.Connection) -> int:
    names = (*RAW_V2_TABLES, *RAW_V2_INDEXES)
    placeholders = ",".join("?" for _ in names)
    table_placeholders = ",".join("?" for _ in RAW_V2_TABLES)
    return int(
        connection.execute(
            f"""SELECT COUNT(*) FROM sqlite_master
                WHERE name IN ({placeholders}) OR tbl_name IN ({table_placeholders})""",
            (*names, *RAW_V2_TABLES),
        ).fetchone()[0]
    )


_RESULT_FIELDS = frozenset(
    {
        "actions",
        "active_policy_sha256",
        "formal_table_drop_count",
        "m0_v1_authority_sha256",
        "read_disabled",
        "result_sha256",
        "retained_evidence_sha256",
        "reverse_v1_write_count",
        "rollback_policy_sha256",
        "stage",
        "stage_evidence_sha256",
        "status",
        "v1_after_authority_sha256",
        "v1_before_authority_sha256",
        "v2_after_blob_file_count",
        "v2_after_scoped_object_count",
        "v2_after_state_sha256",
        "v2_before_blob_file_count",
        "v2_before_blob_total_bytes",
        "v2_before_scoped_object_count",
        "v2_before_state_sha256",
        "write_disabled",
    }
)
_MATRIX_FIELDS = frozenset(
    {
        "artifact_version",
        "content_sha256",
        "invariant_results",
        "limitations",
        "mode",
        "production_database_accessed",
        "schema_version",
        "stage_result_set_sha256",
        "stage_results",
        "status",
    }
)


def _matrix_invariants(entries: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    default_policy_sha256 = RawV2RuntimePolicy.default().content_sha256
    checks = (
        ("BF14-01-seven-stages", len(entries), 7),
        (
            "BF14-02-v1-before-equals-m0",
            all(
                item["v1_before_authority_sha256"] == item["m0_v1_authority_sha256"]
                for item in entries
            ),
            True,
        ),
        (
            "BF14-03-v1-after-equals-m0",
            all(
                item["v1_after_authority_sha256"] == item["m0_v1_authority_sha256"]
                for item in entries
            ),
            True,
        ),
        (
            "BF14-04-v2-scoped-objects-zero",
            sum(int(item["v2_after_scoped_object_count"]) for item in entries),
            0,
        ),
        (
            "BF14-05-v2-blob-files-zero",
            sum(int(item["v2_after_blob_file_count"]) for item in entries),
            0,
        ),
        (
            "BF14-06-write-disabled",
            all(item["write_disabled"] is True for item in entries),
            True,
        ),
        (
            "BF14-07-read-disabled",
            all(item["read_disabled"] is True for item in entries),
            True,
        ),
        (
            "BF14-08-reverse-v1-write-zero",
            sum(int(item["reverse_v1_write_count"]) for item in entries),
            0,
        ),
        (
            "BF14-09-formal-table-drop-zero",
            sum(int(item["formal_table_drop_count"]) for item in entries),
            0,
        ),
        (
            "BF14-10-m6-m7-evidence-retained",
            all(
                (item["retained_evidence_sha256"] is not None) == (index >= 5)
                for index, item in enumerate(entries)
            ),
            True,
        ),
        (
            "BF14-11-default-policy-restored",
            all(item["rollback_policy_sha256"] == default_policy_sha256 for item in entries),
            True,
        ),
    )
    return [
        {
            "expected": expected,
            "id": identifier,
            "observed": observed,
            "passed": observed == expected,
        }
        for identifier, observed, expected in checks
    ]


def _validate_result_value(value: object, expected_stage: RawV2RollbackStage) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != _RESULT_FIELDS:
        _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
    item = value
    if (
        item["stage"] != expected_stage.value
        or item["status"] != RawV2RollbackStatus.ROLLBACK_PASS.value
    ):
        _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
    unsigned = dict(item)
    claimed = unsigned.pop("result_sha256")
    if type(claimed) is not str or _digest(unsigned, allow_none=True) != claimed:
        _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
    for field in (
        "m0_v1_authority_sha256",
        "result_sha256",
        "rollback_policy_sha256",
        "stage_evidence_sha256",
        "v1_after_authority_sha256",
        "v1_before_authority_sha256",
        "v2_after_state_sha256",
        "v2_before_state_sha256",
    ):
        try:
            validate_sha256_digest(item[field])  # type: ignore[arg-type]
        except (RawV2ContractError, TypeError):
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
    for field in ("active_policy_sha256", "retained_evidence_sha256"):
        if item[field] is not None:
            try:
                validate_sha256_digest(item[field])  # type: ignore[arg-type]
            except (RawV2ContractError, TypeError):
                _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
    for field in (
        "formal_table_drop_count",
        "reverse_v1_write_count",
        "v2_after_blob_file_count",
        "v2_after_scoped_object_count",
        "v2_before_blob_file_count",
        "v2_before_blob_total_bytes",
        "v2_before_scoped_object_count",
    ):
        if type(item[field]) is not int or int(item[field]) < 0:
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
    if type(item["write_disabled"]) is not bool or type(item["read_disabled"]) is not bool:
        _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
    if item["actions"] != [action.value for action in _actions(expected_stage)]:
        _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
    rank = _STAGE_RANK[expected_stage]
    if (item["active_policy_sha256"] is not None) != (rank >= 6) or (
        item["retained_evidence_sha256"] is not None
    ) != (rank >= 6):
        _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
    return item


class RawV2RollbackMatrix:
    """Build and verify a canonical database-free M1-M7 BF-14 matrix."""

    @staticmethod
    def build(results: tuple[RawV2RollbackResult, ...]) -> bytes:
        if (
            type(results) is not tuple
            or len(results) != len(_STAGES)
            or any(type(item) is not RawV2RollbackResult for item in results)
            or tuple(item.stage for item in results) != _STAGES
        ):
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
        entries = [item.canonical_value() for item in results]
        for stage, entry in zip(_STAGES, entries, strict=True):
            _validate_result_value(entry, stage)
        invariants = _matrix_invariants(entries)
        if any(item["passed"] is not True for item in invariants):
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
        stage_result_set_sha256 = _digest(entries, allow_none=True)
        unsigned: dict[str, object] = {
            "artifact_version": RAW_V2_ROLLBACK_MATRIX_ARTIFACT_VERSION,
            "invariant_results": invariants,
            "limitations": list(_MATRIX_LIMITATIONS),
            "mode": RawV2RollbackInputMode.FIXTURE.value,
            "production_database_accessed": False,
            "schema_version": RAW_V2_ROLLBACK_MATRIX_SCHEMA_VERSION,
            "stage_result_set_sha256": stage_result_set_sha256,
            "stage_results": entries,
            "status": RawV2RollbackStatus.ROLLBACK_PASS.value,
        }
        value = {**unsigned, "content_sha256": _digest(unsigned, allow_none=True)}
        return canonical_json_bytes(value, allow_none=True) + b"\n"

    @staticmethod
    def verify_bytes(payload: bytes) -> RawV2RollbackMatrixVerification:
        if type(payload) is not bytes:
            raise TypeError("rollback matrix payload must be bytes")
        try:
            parsed = strict_json_loads(payload, allow_none=True)
            canonical = canonical_json_bytes(parsed, allow_none=True) + b"\n"
        except Exception as error:
            raise RawV2RollbackError(RawV2RollbackErrorCode.MATRIX_INVALID) from error
        if payload != canonical or type(parsed) is not dict or frozenset(parsed) != _MATRIX_FIELDS:
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
        if (
            parsed["schema_version"] != RAW_V2_ROLLBACK_MATRIX_SCHEMA_VERSION
            or parsed["artifact_version"] != RAW_V2_ROLLBACK_MATRIX_ARTIFACT_VERSION
            or parsed["mode"] != RawV2RollbackInputMode.FIXTURE.value
            or parsed["production_database_accessed"] is not False
            or parsed["status"] != RawV2RollbackStatus.ROLLBACK_PASS.value
            or parsed["limitations"] != list(_MATRIX_LIMITATIONS)
        ):
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
        unsigned = dict(parsed)
        claimed_content = unsigned.pop("content_sha256")
        if (
            type(claimed_content) is not str
            or _digest(unsigned, allow_none=True) != claimed_content
        ):
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
        raw_entries = parsed["stage_results"]
        if type(raw_entries) is not list or len(raw_entries) != len(_STAGES):
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
        entries = [
            _validate_result_value(value, stage)
            for stage, value in zip(_STAGES, raw_entries, strict=True)
        ]
        stage_set = _digest(entries, allow_none=True)
        if parsed["stage_result_set_sha256"] != stage_set:
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
        invariants = _matrix_invariants(entries)
        if parsed["invariant_results"] != invariants or any(
            item["passed"] is not True for item in invariants
        ):
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
        serialized = canonical.decode("utf-8")
        if "://" in serialized or "\\" in serialized or "\u0000" in serialized:
            _fail(RawV2RollbackErrorCode.MATRIX_INVALID)
        invariant_digest = _digest(invariants)
        verification_sha256 = _digest(
            {
                "content_sha256": claimed_content,
                "invariant_result_sha256": invariant_digest,
                "stage_result_set_sha256": stage_set,
                "status": RawV2RollbackStatus.ROLLBACK_PASS.value,
            }
        )
        return RawV2RollbackMatrixVerification(
            status=RawV2RollbackStatus.ROLLBACK_PASS,
            content_sha256=claimed_content,
            stage_result_set_sha256=stage_set,
            invariant_result_sha256=invariant_digest,
            verification_sha256=verification_sha256,
        )

    @staticmethod
    def verify_file(path: Path) -> RawV2RollbackMatrixVerification:
        if not isinstance(path, Path):
            raise TypeError("rollback matrix path must be a pathlib.Path")
        return RawV2RollbackMatrix.verify_bytes(path.read_bytes())


__all__ = [
    "RAW_V2_ROLLBACK_DRILL_VERSION",
    "RAW_V2_ROLLBACK_MATRIX_ARTIFACT_VERSION",
    "RAW_V2_ROLLBACK_MATRIX_SCHEMA_VERSION",
    "RawV1AuthoritySnapshot",
    "RawV2FixtureRollbackBinding",
    "RawV2RollbackAction",
    "RawV2RollbackDrill",
    "RawV2RollbackError",
    "RawV2RollbackErrorCode",
    "RawV2RollbackExecution",
    "RawV2RollbackInputMode",
    "RawV2RollbackMatrix",
    "RawV2RollbackMatrixVerification",
    "RawV2RollbackResult",
    "RawV2RollbackStage",
    "RawV2RollbackStatus",
    "snapshot_v1_authority",
]
