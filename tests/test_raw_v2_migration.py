from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.sources.schema import SCHEMA as V1_SCHEMA
from evidence_rag.sources.v2.contracts import RawV2ContractError, RawV2Reason
from evidence_rag.sources.v2.migration_v1 import (
    ALLOWED_FIXTURE_OPERATIONS,
    MIGRATION_V1_SCANNER_VERSION,
    MigrationCategory,
    MigrationInputMode,
    MigrationOperation,
    MigrationResolution,
    MigrationScanStatus,
    MigrationSeverity,
    MigrationSourceObjectClaim,
    RawV1MigrationScanner,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures/raw_v2/migration-scanner-v1.json"
FIXTURE_SHA256 = "sha256:ddddb54ce06e9a1ba7f4e385788cc1afd2af2bcaa428ee7a3890271c4ddba101"
CLASSIFICATION_SET_SHA256 = (
    "sha256:2aa2c7ef6d489e2df00e759a309688419e5450d0c28e960c527679c944029732"
)
SOURCE_REVISION = "73d2b5cdcbe93cf6233992c98cc08283a7d6c02a"
TABLE_COLUMNS = {
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


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _load_fixture() -> dict[str, Any]:
    raw = FIXTURE_PATH.read_bytes()
    assert _sha256(raw) == FIXTURE_SHA256
    value = json.loads(raw)
    assert type(value) is dict
    return value


def _expand(value: Any, *, fixture: dict[str, Any], blob_root: Path) -> Any:
    if type(value) is list:
        return [_expand(item, fixture=fixture, blob_root=blob_root) for item in value]
    if type(value) is dict:
        return {
            key: _expand(item, fixture=fixture, blob_root=blob_root)
            for key, item in value.items()
        }
    if type(value) is str and value.startswith("$ROOT/"):
        return str(blob_root / value.removeprefix("$ROOT/"))
    if type(value) is str and value.startswith("$BLOB:"):
        name = value.removeprefix("$BLOB:")
        return _sha256(fixture["blobs"][name].encode())
    if type(value) is str and value.startswith("$SHA256:"):
        return _sha256(value.removeprefix("$SHA256:").encode())
    return value


@dataclasses.dataclass(frozen=True)
class MaterializedFixture:
    db_path: Path
    blob_root: Path
    claims: tuple[MigrationSourceObjectClaim, ...]
    secrets: tuple[str, ...]


def _materialize(
    tmp_path: Path,
    *,
    keep_raw_ids: set[str] | None = None,
    keep_event_ids: set[str] | None = None,
    keep_derived_ids: set[str] | None = None,
    keep_block_ids: set[str] | None = None,
) -> MaterializedFixture:
    fixture = _load_fixture()
    blob_root = tmp_path / "blobs"
    blob_root.mkdir(parents=True)
    for name, content in fixture["blobs"].items():
        (blob_root / name).write_bytes(content.encode())
    expanded = _expand(fixture, fixture=fixture, blob_root=blob_root)
    rows = {
        "raw_objects": [
            row
            for row in expanded["raw_objects"]
            if keep_raw_ids is None or row["id"] in keep_raw_ids
        ],
        "source_events": [
            row
            for row in expanded["source_events"]
            if keep_event_ids is None or row["event_id"] in keep_event_ids
        ],
        "raw_derivations": [
            row
            for row in expanded["raw_derivations"]
            if keep_derived_ids is None or row["derived_entity_id"] in keep_derived_ids
        ],
        "blocked_entities": [
            row
            for row in expanded["blocked_entities"]
            if keep_block_ids is None or row["entity_id"] in keep_block_ids
        ],
    }
    db_path = tmp_path / "fixture.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.executescript(V1_SCHEMA)
    for table, table_rows in rows.items():
        columns = TABLE_COLUMNS[table]
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            [tuple(row[column] for column in columns) for row in table_rows],
        )
    connection.commit()
    connection.close()
    kept_keys = {
        (
            row["source_instance"],
            row["source_object_id"],
            row["source_version"],
            row["content_hash"],
        )
        for row in rows["raw_objects"]
    }
    claims = tuple(
        MigrationSourceObjectClaim(**claim)
        for claim in expanded["source_claims"]
        if (
            claim["source_instance"],
            claim["source_object_id"],
            claim["source_version"],
            claim["content_hash"],
        )
        in kept_keys
    )
    secrets = tuple(
        sorted(
            {
                str(value)
                for table_rows in rows.values()
                for row in table_rows
                for key, value in row.items()
                if value is not None
                and key
                in {
                    "acl_ref",
                    "metadata_json",
                    "payload_ref",
                    "reason",
                    "source_uri",
                    "storage_path",
                    "trace_id",
                }
            }
        )
    )
    return MaterializedFixture(db_path, blob_root, claims, secrets)


def _tree_digest(root: Path) -> str:
    values: list[list[str]] = []
    for candidate in sorted(root.rglob("*")):
        kind = "symlink" if candidate.is_symlink() else "dir" if candidate.is_dir() else "file"
        digest = _sha256(candidate.read_bytes()) if kind == "file" else ""
        values.append([candidate.relative_to(root).as_posix(), kind, digest])
    return _sha256(json.dumps(values, separators=(",", ":")).encode())


def _database_rows(db_path: Path) -> dict[str, list[tuple[Any, ...]]]:
    connection = sqlite3.connect(db_path)
    try:
        return {
            table: connection.execute(
                f"SELECT {', '.join(columns)} FROM {table} ORDER BY {', '.join(columns)}"
            ).fetchall()
            for table, columns in TABLE_COLUMNS.items()
        }
    finally:
        connection.close()


def _freeze(materialized: MaterializedFixture):
    connection = sqlite3.connect(materialized.db_path)
    try:
        binding = RawV1MigrationScanner.freeze_fixture(
            connection=connection,
            dataset_id="fixture-migration-v1",
            source_revision=SOURCE_REVISION,
            blob_root=materialized.blob_root,
            source_claims=materialized.claims,
        )
        assert connection.execute("SELECT 1").fetchone() == (1,)
        return binding
    finally:
        connection.close()


def _opener(
    materialized: MaterializedFixture,
    *,
    calls: list[sqlite3.Connection],
    trace: list[str] | None = None,
) -> Callable[[], sqlite3.Connection]:
    def open_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(materialized.db_path)
        if trace is not None:
            connection.set_trace_callback(trace.append)
        calls.append(connection)
        return connection

    return open_connection


def _assert_closed(connection: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def _minimal(tmp_path: Path, *, raw_id: str = "raw-safe") -> MaterializedFixture:
    event_ids = {"event-safe"} if raw_id == "raw-safe" else set()
    derived_ids = {"entity-safe"} if raw_id == "raw-safe" else set()
    return _materialize(
        tmp_path,
        keep_raw_ids={raw_id},
        keep_event_ids=event_ids,
        keep_derived_ids=derived_ids,
        keep_block_ids=set(),
    )


def test_fixture_contract_and_all_eight_categories_are_conserved(tmp_path: Path) -> None:
    materialized = _materialize(tmp_path)
    before_rows = _database_rows(materialized.db_path)
    before_db = _sha256(materialized.db_path.read_bytes())
    before_blobs = _tree_digest(materialized.blob_root)
    binding = _freeze(materialized)
    calls: list[sqlite3.Connection] = []
    trace: list[str] = []

    report = RawV1MigrationScanner.scan(
        binding=binding,
        opener=_opener(materialized, calls=calls, trace=trace),
    )

    assert MIGRATION_V1_SCANNER_VERSION == "raw-v1-fixture-migration-scanner-v1"
    assert report.status is MigrationScanStatus.DRY_RUN_FAIL
    assert report.stable_input is True
    assert report.start_watermark_sha256 == binding.row_watermark_sha256
    assert report.end_watermark_sha256 == binding.row_watermark_sha256
    assert report.source_before_sha256 == binding.source_snapshot_sha256
    assert report.source_after_sha256 == binding.source_snapshot_sha256
    assert dict(report.row_counts) == {
        "blocked_entities": 3,
        "raw_derivations": 3,
        "raw_objects": 8,
        "source_events": 3,
    }
    assert len(report.classifications) == 17
    assert report.unclassified_count == 0
    raw_categories = {
        item.category
        for item in report.classifications
        if item.table == "raw_objects"
    }
    assert raw_categories == set(MigrationCategory)
    assert dict(report.raw_classification_counts) == {
        category.value: 1 for category in MigrationCategory
    }
    assert report.classification_set_sha256 == CLASSIFICATION_SET_SHA256
    assert len(calls) == 1
    _assert_closed(calls[0])
    assert not any(
        statement.lstrip().upper().startswith(f"{token} ")
        for statement in trace
        for token in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "ATTACH")
    )
    assert _database_rows(materialized.db_path) == before_rows
    assert _sha256(materialized.db_path.read_bytes()) == before_db
    assert _tree_digest(materialized.blob_root) == before_blobs


def test_swallowed_global_unique_history_never_infers_scope_or_generation(tmp_path: Path) -> None:
    materialized = _materialize(tmp_path)
    binding = _freeze(materialized)
    report = RawV1MigrationScanner.scan(
        binding=binding,
        opener=_opener(materialized, calls=[]),
    )
    history = next(
        item
        for item in report.classifications
        if item.table == "raw_objects"
        and item.category is MigrationCategory.HISTORY_UNRECOVERABLE
    )
    assert history.resolution is MigrationResolution.REINGEST_REQUIRED
    assert history.severity is MigrationSeverity.P0
    serialized = json.dumps(dataclasses.asdict(report), sort_keys=True)
    for secret in (*materialized.secrets, str(materialized.blob_root), "project-b"):
        assert secret not in serialized


def test_clean_fixture_is_byte_stable_pass_and_report_is_frozen(tmp_path: Path) -> None:
    materialized = _minimal(tmp_path)
    binding = _freeze(materialized)
    first = RawV1MigrationScanner.scan(
        binding=binding,
        opener=_opener(materialized, calls=[]),
    )
    second = RawV1MigrationScanner.scan(
        binding=binding,
        opener=_opener(materialized, calls=[]),
    )
    assert first == second
    assert first.identity_sha256 == second.identity_sha256
    assert first.status is MigrationScanStatus.DRY_RUN_PASS
    assert {item.category for item in first.classifications} == {MigrationCategory.SAFE_COPY}
    with pytest.raises(FrozenInstanceError):
        first.stable_input = False  # type: ignore[misc]


def test_repair_or_reingest_without_p0_is_hold(tmp_path: Path) -> None:
    materialized = _minimal(tmp_path, raw_id="raw-storage")
    report = RawV1MigrationScanner.scan(
        binding=_freeze(materialized),
        opener=_opener(materialized, calls=[]),
    )
    assert report.status is MigrationScanStatus.DRY_RUN_HOLD
    assert report.classifications[0].category is MigrationCategory.STORAGE_INVALID
    assert report.classifications[0].resolution is MigrationResolution.REPAIR_REQUIRED
    assert report.classifications[0].severity is MigrationSeverity.P1


@pytest.mark.parametrize(
    "mode",
    [
        MigrationInputMode.MIRROR,
        MigrationInputMode.SANITIZED_MIRROR,
        MigrationInputMode.AUTHORIZED_PRODUCTION,
    ],
)
def test_nonfixture_modes_are_rejected_before_opener(tmp_path: Path, mode: MigrationInputMode) -> None:
    materialized = _minimal(tmp_path)
    binding = dataclasses.replace(_freeze(materialized), mode=mode)
    calls: list[sqlite3.Connection] = []
    with pytest.raises(RawV2ContractError) as raised:
        RawV1MigrationScanner.scan(
            binding=binding,
            opener=_opener(materialized, calls=calls),
        )
    assert raised.value.reason is RawV2Reason.MIGRATION_NOT_AUTHORIZED
    assert calls == []


def test_permission_or_binding_drift_is_rejected_before_opener(tmp_path: Path) -> None:
    materialized = _minimal(tmp_path)
    binding = _freeze(materialized)
    variants = (
        dataclasses.replace(
            binding,
            allowed_operations=(*binding.allowed_operations, MigrationOperation.WRITE_V1),
        ),
        dataclasses.replace(binding, source_snapshot_sha256="sha256:" + "f" * 64),
        dataclasses.replace(binding, source_claims=binding.source_claims[:-1]),
    )
    for variant in variants:
        calls: list[sqlite3.Connection] = []
        with pytest.raises(RawV2ContractError) as raised:
            RawV1MigrationScanner.scan(
                binding=variant,
                opener=_opener(materialized, calls=calls),
            )
        assert raised.value.reason is RawV2Reason.MIGRATION_NOT_AUTHORIZED
        assert calls == []


def test_symlink_blob_root_is_rejected_before_opener(tmp_path: Path) -> None:
    materialized = _minimal(tmp_path)
    link = tmp_path / "blob-link"
    link.symlink_to(materialized.blob_root, target_is_directory=True)
    binding = dataclasses.replace(_freeze(materialized), blob_root=str(link))
    calls: list[sqlite3.Connection] = []
    with pytest.raises(RawV2ContractError) as raised:
        RawV1MigrationScanner.scan(
            binding=binding,
            opener=_opener(materialized, calls=calls),
        )
    assert raised.value.reason is RawV2Reason.MIGRATION_NOT_AUTHORIZED
    assert calls == []


def test_schema_or_bound_row_drift_fails_closed(tmp_path: Path) -> None:
    schema_fixture = _minimal(tmp_path / "schema")
    schema_binding = _freeze(schema_fixture)
    connection = sqlite3.connect(schema_fixture.db_path)
    connection.execute("ALTER TABLE raw_objects ADD COLUMN unauthorized TEXT")
    connection.commit()
    connection.close()
    with pytest.raises(RawV2ContractError) as schema_error:
        RawV1MigrationScanner.scan(
            binding=schema_binding,
            opener=_opener(schema_fixture, calls=[]),
        )
    assert schema_error.value.reason is RawV2Reason.MIGRATION_INPUT_CHANGED

    row_fixture = _minimal(tmp_path / "row")
    row_binding = _freeze(row_fixture)
    connection = sqlite3.connect(row_fixture.db_path)
    connection.execute(
        "UPDATE raw_objects SET metadata_json = ? WHERE id = ?",
        ('{"external":"drift"}', "raw-safe"),
    )
    connection.commit()
    connection.close()
    with pytest.raises(RawV2ContractError) as row_error:
        RawV1MigrationScanner.scan(
            binding=row_binding,
            opener=_opener(row_fixture, calls=[]),
        )
    assert row_error.value.reason is RawV2Reason.MIGRATION_INPUT_CHANGED


def test_between_watermark_external_mutation_invalidates_entire_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evidence_rag.sources.v2 import migration_v1

    materialized = _minimal(tmp_path)
    binding = _freeze(materialized)
    original = migration_v1._classify_rows

    def mutate_after_classification(*args: Any, **kwargs: Any):
        result = original(*args, **kwargs)
        peer = sqlite3.connect(materialized.db_path)
        peer.execute(
            "UPDATE raw_objects SET metadata_json = ? WHERE id = ?",
            ('{"external":"between-watermarks"}', "raw-safe"),
        )
        peer.commit()
        peer.close()
        return result

    monkeypatch.setattr(migration_v1, "_classify_rows", mutate_after_classification)
    calls: list[sqlite3.Connection] = []
    with pytest.raises(RawV2ContractError) as raised:
        RawV1MigrationScanner.scan(
            binding=binding,
            opener=_opener(materialized, calls=calls),
        )
    assert raised.value.reason is RawV2Reason.MIGRATION_INPUT_CHANGED
    assert len(calls) == 1
    _assert_closed(calls[0])


@pytest.mark.parametrize(
    "case",
    ["missing", "outside", "symlink", "not_regular", "length", "digest"],
)
def test_blob_failures_are_storage_invalid_without_path_disclosure(
    tmp_path: Path,
    case: str,
) -> None:
    materialized = _minimal(tmp_path, raw_id="raw-safe")
    connection = sqlite3.connect(materialized.db_path)
    stored_path = materialized.blob_root / "safe.bin"
    content_hash = _sha256(stored_path.read_bytes())
    byte_length = stored_path.stat().st_size
    if case == "missing":
        stored_path = materialized.blob_root / "does-not-exist.bin"
    elif case == "outside":
        stored_path = tmp_path / "outside.bin"
        stored_path.write_bytes(b"safe-bytes")
    elif case == "symlink":
        link = materialized.blob_root / "linked.bin"
        link.symlink_to(materialized.blob_root / "safe.bin")
        stored_path = link
    elif case == "not_regular":
        stored_path = materialized.blob_root / "directory"
        stored_path.mkdir()
    elif case == "length":
        byte_length += 1
    elif case == "digest":
        content_hash = _sha256(b"not-safe-bytes")
    connection.execute(
        "UPDATE raw_objects SET storage_path = ?, byte_length = ?, content_hash = ? WHERE id = ?",
        (str(stored_path), byte_length, content_hash, "raw-safe"),
    )
    connection.commit()
    connection.close()
    claim = dataclasses.replace(materialized.claims[0], content_hash=content_hash)
    materialized = dataclasses.replace(materialized, claims=(claim,))
    report = RawV1MigrationScanner.scan(
        binding=_freeze(materialized),
        opener=_opener(materialized, calls=[]),
    )
    assert report.status is MigrationScanStatus.DRY_RUN_HOLD
    assert report.classifications[0].category is MigrationCategory.STORAGE_INVALID
    serialized = json.dumps(dataclasses.asdict(report), sort_keys=True)
    assert str(stored_path) not in serialized
    assert str(materialized.blob_root) not in serialized


def test_scanner_never_exports_sensitive_v1_values(tmp_path: Path) -> None:
    materialized = _materialize(tmp_path)
    report = RawV1MigrationScanner.scan(
        binding=_freeze(materialized),
        opener=_opener(materialized, calls=[]),
    )
    serialized = json.dumps(dataclasses.asdict(report), sort_keys=True)
    forbidden_names = {
        "acl_ref",
        "metadata_json",
        "payload_ref",
        "project_id",
        "reason",
        "source_uri",
        "storage_path",
        "trace_id",
    }
    assert forbidden_names.isdisjoint(serialized)
    for secret in materialized.secrets:
        assert secret not in serialized


def test_frozen_operations_are_exact_and_exclude_writes() -> None:
    assert ALLOWED_FIXTURE_OPERATIONS == (
        MigrationOperation.HASH_CONTROLLED_BLOB,
        MigrationOperation.READ_V1_METADATA,
        MigrationOperation.READ_V1_SCHEMA,
    )
    assert MigrationOperation.WRITE_V1 not in ALLOWED_FIXTURE_OPERATIONS
    assert MigrationOperation.WRITE_V2 not in ALLOWED_FIXTURE_OPERATIONS
    assert MigrationOperation.CONNECT_PRODUCTION not in ALLOWED_FIXTURE_OPERATIONS
    assert MigrationOperation.EXPORT_RAW_VALUE not in ALLOWED_FIXTURE_OPERATIONS
    assert os.fspath(FIXTURE_PATH).endswith("tests/fixtures/raw_v2/migration-scanner-v1.json")
