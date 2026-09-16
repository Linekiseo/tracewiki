from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from pathlib import Path

import pytest

from evidence_rag.sources.v2.contracts import (
    RawV2ContractError,
    RawV2Reason,
    canonical_json_bytes,
    exact_bytes_sha256,
)
from evidence_rag.sources.v2.policy import RawV2ProjectPolicyAdapter
from evidence_rag.sources.v2.schema import RAW_V2_TABLES, initialize_raw_v2_schema
from evidence_rag.sources.v2.store import (
    RAW_V2_EVENT_STORE_VERSION,
    RAW_V2_OBJECT_STORE_VERSION,
    RawEventRecord,
    RawEventWriteRequest,
    RawObjectRecord,
    RawObjectWriteRequest,
    RawV2EventStore,
    RawV2ObjectStore,
)

NOW = "2026-09-01T00:00:00.000000Z"
OBSERVED = "2026-08-31T23:59:00.000000Z"


class _Authority:
    def __init__(self, projects: set[str] | None = None) -> None:
        self.projects = projects or {"project-a", "project-b"}

    def project_exists(self, *, project_id: str) -> bool:
        return project_id in self.projects

    def resolve_object_acl_ref(
        self,
        *,
        project_id: str,
        source_acl_ref: str,
    ) -> str | None:
        if project_id not in self.projects:
            return None
        return {
            "source-public": "public",
            "source-private": f"team-{project_id[-1]}",
        }.get(source_acl_ref)

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None:
        del principal_id
        if project_id not in self.projects:
            return None
        return (f"team-{project_id[-1]}",)


def _policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    initialize_raw_v2_schema(connection)
    return connection


def _request(
    *,
    project_id: str = "project-a",
    source_acl_ref: str = "source-public",
    source_object_id: str = "object-1",
    source_version: str = "version-1",
    stable_version: str = "stable-1",
    media_type: str = "application/octet-stream",
    metadata_projection: dict[str, object] | None = None,
    observed_at: str = OBSERVED,
    adapter_version: str = "adapter-v1",
    schema_version: str = "source-schema-v1",
) -> RawObjectWriteRequest:
    return RawObjectWriteRequest(
        project_id=project_id,
        source_type="git",
        source_instance_id="repository-1",
        source_object_id=source_object_id,
        source_version=source_version,
        stable_version=stable_version,
        source_acl_ref=source_acl_ref,
        media_type=media_type,
        adapter_version=adapter_version,
        schema_version=schema_version,
        metadata_projection=metadata_projection or {},
        observed_at=observed_at,
    )


def _store(
    connection: sqlite3.Connection,
    root: Path,
) -> RawV2ObjectStore:
    return RawV2ObjectStore(
        connection=connection,
        blob_root=root,
        policy=_policy(),
        clock=lambda: NOW,
    )


def _event_request(
    *,
    project_id: str = "project-a",
    source_type: str = "git",
    source_instance_id: str = "repository-1",
    event_type: str = "raw.persisted",
    mutation_id: str = "mutation-1",
    source_object_id: str = "object-1",
    source_version: str = "version-1",
    source_acl_ref: str = "source-public",
    raw_object_id: str | None,
    event_time: str | None = "2026-08-31T23:58:00Z",
    observed_at: str = OBSERVED,
    trace_id: str = "trace-1",
    schema_version: str = "source-event-schema-v1",
    status: str = "persisted",
    metadata_projection: dict[str, object] | None = None,
) -> RawEventWriteRequest:
    return RawEventWriteRequest(
        project_id=project_id,
        source_type=source_type,
        source_instance_id=source_instance_id,
        event_type=event_type,
        mutation_id=mutation_id,
        source_object_id=source_object_id,
        source_version=source_version,
        source_acl_ref=source_acl_ref,
        raw_object_id=raw_object_id,
        event_time=event_time,
        observed_at=observed_at,
        trace_id=trace_id,
        schema_version=schema_version,
        status=status,
        metadata_projection=metadata_projection or {},
    )


def _event_store(
    connection: sqlite3.Connection,
    *,
    clock: Callable[[], str] | None = None,
) -> RawV2EventStore:
    selected_clock = clock or (lambda: NOW)
    return RawV2EventStore(
        connection=connection,
        policy=_policy(),
        clock=selected_clock,
    )


def _active_object(
    connection: sqlite3.Connection,
    root: Path,
    *,
    payload: bytes = b"event payload",
    object_request: RawObjectWriteRequest | None = None,
) -> RawObjectRecord:
    return _store(connection, root).persist_bytes(
        request=object_request or _request(),
        payload=payload,
    )


def _storage_key(payload: bytes) -> str:
    digest_hex = hashlib.sha256(payload).hexdigest()
    return f"{digest_hex[:2]}/{digest_hex[2:4]}/{digest_hex}"


def _tree_projection(root: Path) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    if not root.exists() and not root.is_symlink():
        return projected
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        names.sort()
        files.sort()
        for name in [*names, *files]:
            item = parent / name
            relative = item.relative_to(root).as_posix()
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode):
                projected.append(
                    {"kind": "symlink", "path": relative, "target": os.readlink(item)}
                )
            elif stat.S_ISDIR(mode):
                projected.append({"kind": "directory", "path": relative})
            elif stat.S_ISREG(mode):
                raw = item.read_bytes()
                projected.append(
                    {
                        "kind": "file",
                        "path": relative,
                        "byte_length": len(raw),
                        "sha256": exact_bytes_sha256(raw),
                    }
                )
            else:
                projected.append({"kind": "other", "path": relative})
    return sorted(projected, key=lambda item: (str(item["path"]), str(item["kind"])))


def _database_projection(connection: sqlite3.Connection) -> dict[str, object]:
    projected: dict[str, object] = {}
    for table in RAW_V2_TABLES:
        columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
        rows = [
            list(row)
            for row in connection.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid'
            ).fetchall()
        ]
        projected[table] = {"columns": columns, "rows": rows}
    return projected


def _state_sha256(connection: sqlite3.Connection, root: Path) -> str:
    return exact_bytes_sha256(
        canonical_json_bytes(
            {
                "database": _database_projection(connection),
                "blob_tree": _tree_projection(root),
            },
            allow_none=True,
        )
    )


def _row(connection: sqlite3.Connection, table: str) -> dict[str, object]:
    cursor = connection.execute(f'SELECT * FROM "{table}"')
    columns = [str(item[0]) for item in cursor.description or ()]
    values = cursor.fetchone()
    assert values is not None
    return dict(zip(columns, values, strict=True))


def _count(connection: sqlite3.Connection, table: str) -> int:
    value = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    assert value is not None
    return int(value[0])


def _assert_reason(error: pytest.ExceptionInfo[RawV2ContractError], reason: RawV2Reason) -> None:
    assert error.value.reason is reason


@pytest.mark.parametrize(
    "payload",
    [b"", b"raw-v2", b"x" * 1_100_000],
    ids=["empty", "small", "large"],
)
def test_active_bytes_compute_digest_length_key_and_exact_expected(
    tmp_path: Path,
    payload: bytes,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    expected = exact_bytes_sha256(payload)

    result = store.persist_bytes(
        request=_request(source_object_id=f"object-{len(payload)}"),
        payload=payload,
        expected_content_sha256=expected,
    )

    assert RAW_V2_OBJECT_STORE_VERSION == "raw-v2-object-store-v1"
    assert result.raw_content_sha256 == expected
    assert result.byte_length == len(payload)
    assert result.state == "active"
    assert result.created is True
    blob = _row(connection, "raw_blobs_v2")
    assert blob["blob_sha256"] == expected
    assert blob["byte_length"] == len(payload)
    assert blob["storage_key"] == _storage_key(payload)
    assert blob["storage_state"] == "available"
    assert blob["first_verified_at"] == NOW
    assert blob["last_verified_at"] == NOW
    assert (root / _storage_key(payload)).read_bytes() == payload
    raw = _row(connection, "raw_objects_v2")
    assert raw["raw_object_id"] == result.raw_object_id
    assert raw["raw_content_sha256"] == expected
    assert raw["blob_sha256"] == expected
    assert raw["byte_length"] == len(payload)


def test_wrong_expected_digest_rejects_before_any_side_effect(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist_bytes(
            request=_request(),
            payload=b"actual",
            expected_content_sha256=exact_bytes_sha256(b"claimed"),
        )

    _assert_reason(error, RawV2Reason.EXPECTED_DIGEST_MISMATCH)
    assert _state_sha256(connection, root) == before
    assert list(root.iterdir()) == []


def test_portable_object_identity_storage_key_and_projection_golden(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    payload = b"portable raw v2 object\n"
    request = _request(
        source_object_id="golden-object",
        metadata_projection={"kind": "golden", "ordinal": 1},
    )

    result = store.persist_bytes(request=request, payload=payload)
    storage_key = str(_row(connection, "raw_blobs_v2")["storage_key"])
    projection = {
        "raw_object_id": result.raw_object_id,
        "logical_identity_sha256": result.logical_identity_sha256,
        "visibility_partition_sha256": result.visibility_partition_sha256,
        "raw_content_sha256": result.raw_content_sha256,
        "byte_length": result.byte_length,
        "storage_key": storage_key,
        "state": result.state,
    }

    assert result.raw_object_id == (
        "raw-v2:94529561e88a1e1d36593e98bace26883167f719fa371c533794219893f39bda"
    )
    assert result.logical_identity_sha256 == (
        "sha256:94529561e88a1e1d36593e98bace26883167f719fa371c533794219893f39bda"
    )
    assert result.visibility_partition_sha256 == (
        "sha256:1d31fe07810c29b464a7986e600815c1be11f4b5f4beb3a1d9c05e52f56a3398"
    )
    assert result.raw_content_sha256 == (
        "sha256:498b2d8aa3ff1cff0861904fddab0a521f32ded9e9053b18347248e834b780c4"
    )
    assert storage_key == (
        "49/8b/498b2d8aa3ff1cff0861904fddab0a521f32ded9e9053b18347248e834b780c4"
    )
    assert exact_bytes_sha256(canonical_json_bytes(projection)) == (
        "sha256:7fab87d198e31803a501f8754ae3ade32253af2db967f3930b2b38980dcedbce"
    )
    assert not hasattr(store, "persist_event")
    assert _count(connection, "source_events_v2") == 0


def test_physical_dedupe_keeps_project_and_acl_logical_authority_separate(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    payload = b"shared exact bytes"

    public_a = store.persist_bytes(
        request=_request(
            project_id="project-a",
            source_acl_ref="source-public",
            metadata_projection={"owner": "project-a-public"},
        ),
        payload=payload,
    )
    private_a = store.persist_bytes(
        request=_request(
            project_id="project-a",
            source_acl_ref="source-private",
            metadata_projection={"owner": "project-a-private"},
        ),
        payload=payload,
    )
    public_b = store.persist_bytes(
        request=_request(
            project_id="project-b",
            source_acl_ref="source-public",
            metadata_projection={"owner": "project-b-public"},
        ),
        payload=payload,
    )

    assert len({public_a.raw_object_id, private_a.raw_object_id, public_b.raw_object_id}) == 3
    assert public_a.raw_content_sha256 == private_a.raw_content_sha256 == public_b.raw_content_sha256
    assert _count(connection, "raw_blobs_v2") == 1
    assert _count(connection, "raw_objects_v2") == 3
    assert len([item for item in _tree_projection(root) if item["kind"] == "file"]) == 1
    rows = connection.execute(
        "SELECT project_id, acl_ref, metadata_json FROM raw_objects_v2 ORDER BY project_id, acl_ref"
    ).fetchall()
    assert rows == [
        ("project-a", "public", '{"owner":"project-a-public"}'),
        ("project-a", "team-a", '{"owner":"project-a-private"}'),
        ("project-b", "public", '{"owner":"project-b-public"}'),
    ]


def test_exact_retry_is_write_free_and_result_has_no_internal_location(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    request = _request(metadata_projection={"kind": "reviewed"})
    payload = b"idempotent"

    first = store.persist_bytes(request=request, payload=payload)
    before = _state_sha256(connection, root)
    second = store.persist_bytes(request=request, payload=payload)

    assert first.created is True
    assert second == replace(first, created=False)
    assert _state_sha256(connection, root) == before
    assert {item.name for item in fields(RawObjectRecord)} == {
        "raw_object_id",
        "logical_identity_sha256",
        "project_id",
        "visibility_partition_sha256",
        "raw_content_sha256",
        "byte_length",
        "state",
        "created",
    }
    serialized_names = json.dumps(second.__dict__ if hasattr(second, "__dict__") else {})
    assert "storage" not in serialized_names
    assert "path" not in serialized_names
    assert "uri" not in serialized_names


@pytest.mark.parametrize(
    "changed",
    [
        {"media_type": "text/plain"},
        {"metadata_projection": {"different": True}},
        {"observed_at": "2026-08-31T23:58:00.000000Z"},
        {"adapter_version": "adapter-v2"},
        {"schema_version": "source-schema-v2"},
    ],
    ids=["media", "metadata", "observed", "adapter", "schema"],
)
def test_same_logical_identity_different_payload_is_exact_conflict(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    request = _request(metadata_projection={"reviewed": True})
    payload = b"same identity bytes"
    store.persist_bytes(request=request, payload=payload)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist_bytes(request=replace(request, **changed), payload=payload)

    _assert_reason(error, RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT)
    assert _state_sha256(connection, root) == before


def test_tampered_existing_object_conflicts_without_repair(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    request = _request()
    payload = b"tamper target"
    result = store.persist_bytes(request=request, payload=payload)
    connection.execute(
        "UPDATE raw_objects_v2 SET metadata_json=? WHERE raw_object_id=?",
        ('{"tampered":true}', result.raw_object_id),
    )
    connection.commit()
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist_bytes(request=request, payload=payload)

    _assert_reason(error, RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT)
    assert _state_sha256(connection, root) == before


def test_reference_only_has_no_digest_blob_length_or_files_and_retries_exactly(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    request = _request(metadata_projection={"reference_kind": "opaque-source-id"})

    first = store.persist_reference(request=request)
    before = _state_sha256(connection, root)
    second = store.persist_reference(request=request)

    assert first.state == "reference_only"
    assert first.raw_content_sha256 is None
    assert first.byte_length is None
    assert first.created is True
    assert second == replace(first, created=False)
    assert _state_sha256(connection, root) == before
    assert _count(connection, "raw_blobs_v2") == 0
    raw = _row(connection, "raw_objects_v2")
    assert raw["raw_content_sha256"] is None
    assert raw["blob_sha256"] is None
    assert raw["byte_length"] is None
    assert raw["state"] == "reference_only"
    assert _tree_projection(root) == []

    with pytest.raises(RawV2ContractError) as error:
        store.persist_reference(
            request=replace(request, metadata_projection={"reference_kind": "changed"})
        )
    _assert_reason(error, RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT)
    assert _state_sha256(connection, root) == before


def test_empty_bytes_are_active_and_not_reference_only(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)

    active = store.persist_bytes(request=_request(source_object_id="empty"), payload=b"")
    reference = store.persist_reference(request=_request(source_object_id="reference"))

    assert active.state == "active" and active.byte_length == 0
    assert active.raw_content_sha256 == exact_bytes_sha256(b"")
    assert reference.state == "reference_only" and reference.byte_length is None
    assert active.raw_object_id != reference.raw_object_id


def test_request_api_has_no_storage_key_path_or_computed_partition_fields() -> None:
    assert {item.name for item in fields(RawObjectWriteRequest)} == {
        "project_id",
        "source_type",
        "source_instance_id",
        "source_object_id",
        "source_version",
        "stable_version",
        "source_acl_ref",
        "media_type",
        "adapter_version",
        "schema_version",
        "metadata_projection",
        "observed_at",
    }


def test_root_symlink_is_rejected_before_use(tmp_path: Path) -> None:
    connection = _connection()
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(RawV2ContractError) as error:
        _store(connection, alias)

    _assert_reason(error, RawV2Reason.BLOB_UNAVAILABLE)
    assert _count(connection, "raw_blobs_v2") == 0
    assert list(real.iterdir()) == []


def test_intermediate_symlink_escape_is_rejected_without_outside_write(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    payload = b"prefix symlink"
    key = _storage_key(payload)
    (root / key.split("/")[0]).symlink_to(outside, target_is_directory=True)
    store = _store(connection, root)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist_bytes(request=_request(), payload=payload)

    _assert_reason(error, RawV2Reason.BLOB_UNAVAILABLE)
    assert _state_sha256(connection, root) == before
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("target_kind", ["symlink", "directory"])
def test_existing_non_regular_target_is_never_overwritten(
    tmp_path: Path,
    target_kind: str,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    payload = b"final target attack"
    target = root / _storage_key(payload)
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside sentinel")
    if target_kind == "symlink":
        target.symlink_to(outside)
    else:
        target.mkdir()
    store = _store(connection, root)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist_bytes(request=_request(), payload=payload)

    _assert_reason(error, RawV2Reason.BLOB_CORRUPT)
    assert _state_sha256(connection, root) == before
    assert outside.read_bytes() == b"outside sentinel"


def test_existing_corrupt_content_address_is_detected_not_overwritten(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    payload = b"expected bytes"
    target = root / _storage_key(payload)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"wrong bytes")
    store = _store(connection, root)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist_bytes(request=_request(), payload=payload)

    _assert_reason(error, RawV2Reason.BLOB_CORRUPT)
    assert _state_sha256(connection, root) == before
    assert target.read_bytes() == b"wrong bytes"


def test_existing_blob_row_requires_exact_available_file_and_metadata(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    payload = b"missing stored blob"
    digest = exact_bytes_sha256(payload)
    connection.execute(
        """INSERT INTO raw_blobs_v2(
               blob_sha256, byte_length, storage_key, storage_state,
               first_verified_at, last_verified_at, created_at
           ) VALUES (?, ?, ?, 'available', ?, ?, ?)""",
        (digest, len(payload), _storage_key(payload), NOW, NOW, NOW),
    )
    connection.commit()
    store = _store(connection, root)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist_bytes(request=_request(), payload=payload)

    _assert_reason(error, RawV2Reason.BLOB_CORRUPT)
    assert _state_sha256(connection, root) == before


def test_active_caller_transaction_and_schema_drift_fail_before_blob_mutation(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    connection.execute("BEGIN")
    with pytest.raises(RawV2ContractError) as transaction_error:
        store.persist_bytes(request=_request(), payload=b"transaction")
    _assert_reason(transaction_error, RawV2Reason.RAW_STATE_INVALID)
    assert _tree_projection(root) == []
    connection.rollback()

    connection.execute(
        """CREATE TRIGGER unreviewed_object_trigger
           AFTER INSERT ON raw_objects_v2 BEGIN SELECT 1; END"""
    )
    connection.commit()
    with pytest.raises(RawV2ContractError) as drift_error:
        store.persist_bytes(request=_request(), payload=b"schema drift")
    _assert_reason(drift_error, RawV2Reason.RAW_STATE_INVALID)
    assert _tree_projection(root) == []


def test_filesystem_failure_leaves_database_unchanged(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    payload = b"directory failure"
    prefix = root / _storage_key(payload).split("/")[0]
    prefix.write_bytes(b"not a directory")
    store = _store(connection, root)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist_bytes(request=_request(), payload=payload)

    _assert_reason(error, RawV2Reason.BLOB_UNAVAILABLE)
    assert _state_sha256(connection, root) == before


def test_database_failure_rolls_back_rows_returns_no_id_and_preserves_verified_orphan(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    payload = b"published before database failure"
    store = _store(connection, root)
    database_before = _database_projection(connection)

    def deny_blob_insert(action: int, table: str, *unused: object) -> int:
        del unused
        if action == sqlite3.SQLITE_INSERT and table == "raw_blobs_v2":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_blob_insert)
    try:
        with pytest.raises(RawV2ContractError) as error:
            store.persist_bytes(request=_request(), payload=payload)
    finally:
        connection.set_authorizer(None)

    _assert_reason(error, RawV2Reason.BLOB_UNAVAILABLE)
    assert connection.in_transaction is False
    assert _database_projection(connection) == database_before
    files = [item for item in _tree_projection(root) if item["kind"] == "file"]
    assert files == [
        {
            "kind": "file",
            "path": _storage_key(payload),
            "byte_length": len(payload),
            "sha256": exact_bytes_sha256(payload),
        }
    ]


def test_v1_user_version_and_non_object_v2_tables_are_conserved(tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE legacy_raw(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO legacy_raw VALUES ('legacy-1', 'unchanged')")
    connection.execute("PRAGMA user_version = 77")
    connection.commit()
    initialize_raw_v2_schema(connection)
    legacy_before = connection.execute("SELECT * FROM legacy_raw").fetchall()
    root = tmp_path / "blobs"
    root.mkdir()

    _store(connection, root).persist_bytes(request=_request(), payload=b"new v2")

    assert connection.execute("SELECT * FROM legacy_raw").fetchall() == legacy_before
    assert connection.execute("PRAGMA user_version").fetchone() == (77,)
    for table in (
        "source_events_v2",
        "raw_evidence_bindings_v2",
        "blocked_entities_v2",
        "raw_migration_runs_v2",
        "raw_migration_findings_v2",
    ):
        assert _count(connection, table) == 0


def test_invalid_runtime_values_fail_before_filesystem_or_rows(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    store = _store(connection, root)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as byte_error:
        store.persist_bytes(request=_request(), payload=bytearray(b"not exact bytes"))  # type: ignore[arg-type]
    _assert_reason(byte_error, RawV2Reason.CANONICALIZATION_MISMATCH)

    with pytest.raises(RawV2ContractError) as digest_error:
        store.persist_bytes(
            request=_request(),
            payload=b"payload",
            expected_content_sha256="SHA256:not-canonical",
        )
    _assert_reason(digest_error, RawV2Reason.CANONICALIZATION_MISMATCH)

    with pytest.raises(RawV2ContractError) as metadata_error:
        store.persist_bytes(
            request=replace(_request(), metadata_projection={"bad": 1.5}),
            payload=b"payload",
        )
    _assert_reason(metadata_error, RawV2Reason.CANONICALIZATION_MISMATCH)
    assert _state_sha256(connection, root) == before


def test_two_connections_serialize_to_one_exact_blob_and_object(tmp_path: Path) -> None:
    database = tmp_path / "fixture.sqlite3"
    root = tmp_path / "blobs"
    root.mkdir()
    bootstrap = sqlite3.connect(database)
    initialize_raw_v2_schema(bootstrap)
    bootstrap.close()
    payload = b"concurrent exact write"
    request = _request()
    barrier = threading.Barrier(2)

    def write() -> RawObjectRecord:
        connection = sqlite3.connect(database, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        store = _store(connection, root)
        barrier.wait(timeout=5.0)
        try:
            return store.persist_bytes(request=request, payload=payload)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: write(), range(2)))

    verified = sqlite3.connect(database)
    assert len({item.raw_object_id for item in results}) == 1
    assert sorted(item.created for item in results) == [False, True]
    assert _count(verified, "raw_blobs_v2") == 1
    assert _count(verified, "raw_objects_v2") == 1
    assert len([item for item in _tree_projection(root) if item["kind"] == "file"]) == 1
    verified.close()


def test_event_api_has_only_trusted_inputs_and_internal_non_location_result() -> None:
    assert RAW_V2_EVENT_STORE_VERSION == "raw-v2-event-store-v1"
    assert {item.name for item in fields(RawEventWriteRequest)} == {
        "project_id",
        "source_type",
        "source_instance_id",
        "event_type",
        "mutation_id",
        "source_object_id",
        "source_version",
        "source_acl_ref",
        "raw_object_id",
        "event_time",
        "observed_at",
        "trace_id",
        "schema_version",
        "status",
        "metadata_projection",
    }
    assert {item.name for item in fields(RawEventRecord)} == {
        "event_id",
        "idempotency_sha256",
        "project_id",
        "visibility_partition_sha256",
        "raw_object_id",
        "raw_content_sha256",
        "status",
        "created",
    }
    forbidden = {
        "source_domain",
        "raw_content_sha256",
        "visibility_partition_sha256",
        "event_id",
        "idempotency_sha256",
        "storage_key",
        "path",
        "uri",
    }
    assert forbidden.isdisjoint(item.name for item in fields(RawEventWriteRequest))


def test_persisted_event_derives_object_authority_and_portable_golden(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    payload = b"portable raw v2 event\n"
    raw = _active_object(
        connection,
        root,
        payload=payload,
        object_request=_request(source_object_id="golden-event-object"),
    )
    event = _event_store(connection).persist(
        request=_event_request(
            source_object_id="golden-event-object",
            event_type="raw.persisted",
            mutation_id="mutation-golden-1",
            raw_object_id=raw.raw_object_id,
            metadata_projection={"kind": "golden", "ordinal": 1},
        )
    )

    assert event.event_id == (
        "source-event-v2:0a17ed2a6897d7abfac285ae4a02c0ac6e26ce04bb9958b550cebfd498545f5f"
    )
    assert event.idempotency_sha256 == (
        "sha256:0a17ed2a6897d7abfac285ae4a02c0ac6e26ce04bb9958b550cebfd498545f5f"
    )
    assert event.visibility_partition_sha256 == (
        "sha256:1d31fe07810c29b464a7986e600815c1be11f4b5f4beb3a1d9c05e52f56a3398"
    )
    assert event.raw_content_sha256 == (
        "sha256:f941469494ef628706d0be43984218d9c5559d2a95c3b2f40f63748c2d6f2a39"
    )
    projection = {
        "event_id": event.event_id,
        "idempotency_sha256": event.idempotency_sha256,
        "project_id": event.project_id,
        "visibility_partition_sha256": event.visibility_partition_sha256,
        "raw_content_sha256": event.raw_content_sha256,
        "status": event.status,
    }
    assert exact_bytes_sha256(canonical_json_bytes(projection, allow_none=True)) == (
        "sha256:b8ae0dcc0a12bef31f2fdf45a14724965e238a0979c9a8a328f2c196f0d30602"
    )
    row = _row(connection, "source_events_v2")
    assert row["event_id"] == event.event_id
    assert row["raw_object_id"] == raw.raw_object_id
    assert row["raw_content_sha256"] == raw.raw_content_sha256
    assert row["acl_ref"] == "public"
    assert row["source_domain"] == "code"
    assert row["event_time"] == "2026-08-31T23:58:00.000000Z"
    assert row["metadata_json"] == '{"kind":"golden","ordinal":1}'


def test_reference_only_and_rejected_event_semantics(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    reference = _store(connection, root).persist_reference(
        request=_request(source_object_id="reference-object")
    )

    reference_event = _event_store(connection).persist(
        request=_event_request(
            source_object_id="reference-object",
            event_type="raw.reference-observed",
            mutation_id="mutation-reference",
            raw_object_id=reference.raw_object_id,
            status="reference_only",
        )
    )
    rejected_event = _event_store(connection).persist(
        request=_event_request(
            source_object_id="rejected-object",
            event_type="raw.rejected",
            mutation_id="mutation-rejected",
            raw_object_id=None,
            status="rejected",
            metadata_projection={"reason": "policy-refusal"},
        )
    )

    assert reference_event.raw_object_id == reference.raw_object_id
    assert reference_event.raw_content_sha256 is None
    assert reference_event.status == "reference_only"
    assert rejected_event.raw_object_id is None
    assert rejected_event.raw_content_sha256 is None
    assert rejected_event.status == "rejected"
    assert _count(connection, "source_events_v2") == 2
    assert _count(connection, "raw_blobs_v2") == 0
    assert _tree_projection(root) == []


@pytest.mark.parametrize(
    ("object_state", "event_status"),
    [("quarantined", "quarantined"), ("tombstoned", "tombstone")],
)
def test_quarantine_and_tombstone_events_record_existing_state_without_transition(
    tmp_path: Path,
    object_state: str,
    event_status: str,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    connection.execute(
        "UPDATE raw_objects_v2 SET state=?, tombstoned_at=? WHERE raw_object_id=?",
        (object_state, NOW if object_state == "tombstoned" else None, raw.raw_object_id),
    )
    connection.commit()
    object_before = _row(connection, "raw_objects_v2")

    result = _event_store(connection).persist(
        request=_event_request(
            event_type=f"raw.{event_status}",
            mutation_id=f"mutation-{event_status}",
            raw_object_id=raw.raw_object_id,
            status=event_status,
        )
    )

    assert result.status == event_status
    assert _row(connection, "raw_objects_v2") == object_before


def test_same_mutation_is_project_and_acl_scoped_while_blob_is_shared(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    payload = b"shared event bytes"
    cases = [
        ("project-a", "source-public"),
        ("project-a", "source-private"),
        ("project-b", "source-public"),
    ]
    events: list[RawEventRecord] = []
    for project_id, acl_ref in cases:
        raw = _active_object(
            connection,
            root,
            payload=payload,
            object_request=_request(project_id=project_id, source_acl_ref=acl_ref),
        )
        events.append(
            _event_store(connection).persist(
                request=_event_request(
                    project_id=project_id,
                    source_acl_ref=acl_ref,
                    raw_object_id=raw.raw_object_id,
                )
            )
        )

    assert len({event.event_id for event in events}) == 3
    assert len({event.visibility_partition_sha256 for event in events}) == 3
    assert len({event.raw_content_sha256 for event in events}) == 1
    assert _count(connection, "raw_blobs_v2") == 1
    assert _count(connection, "raw_objects_v2") == 3
    assert _count(connection, "source_events_v2") == 3


def test_exact_event_retry_is_write_free_and_ignores_only_created_at(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    times = iter((NOW, "2026-09-01T00:00:01Z"))
    store = _event_store(connection, clock=lambda: next(times))
    request = _event_request(
        raw_object_id=raw.raw_object_id,
        metadata_projection={"reviewed": True},
    )

    first = store.persist(request=request)
    connection.execute(
        "UPDATE raw_objects_v2 SET state='tombstoned', tombstoned_at=? WHERE raw_object_id=?",
        (NOW, raw.raw_object_id),
    )
    connection.commit()
    before = _state_sha256(connection, root)
    second = store.persist(request=request)

    assert first.created is True
    assert second == replace(first, created=False)
    assert _state_sha256(connection, root) == before
    assert _count(connection, "source_events_v2") == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"event_time": "2026-08-31T23:57:00Z"},
        {"observed_at": "2026-08-31T23:58:30Z"},
        {"trace_id": "trace-different"},
        {"schema_version": "source-event-schema-v2"},
        {"metadata_projection": {"different": True}},
    ],
    ids=["event-time", "observed", "trace", "schema", "metadata"],
)
def test_same_event_identity_different_payload_is_typed_conflict(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _event_store(connection)
    request = _event_request(
        raw_object_id=raw.raw_object_id,
        metadata_projection={"reviewed": True},
    )
    store.persist(request=request)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist(request=replace(request, **changed))

    _assert_reason(error, RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT)
    assert _state_sha256(connection, root) == before


def test_same_event_identity_different_raw_object_is_conflict(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    payload = b"same event identity bytes"
    first_raw = _active_object(connection, root, payload=payload)
    second_raw = _active_object(
        connection,
        root,
        payload=payload,
        object_request=_request(stable_version="stable-2"),
    )
    store = _event_store(connection)
    request = _event_request(raw_object_id=first_raw.raw_object_id)
    store.persist(request=request)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist(request=replace(request, raw_object_id=second_raw.raw_object_id))

    _assert_reason(error, RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT)
    assert _state_sha256(connection, root) == before


def test_tampered_existing_event_is_never_repaired_as_retry(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _event_store(connection)
    request = _event_request(raw_object_id=raw.raw_object_id)
    result = store.persist(request=request)
    connection.execute(
        "UPDATE source_events_v2 SET metadata_json=? WHERE event_id=?",
        ('{"tampered":true}', result.event_id),
    )
    connection.commit()
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist(request=request)

    _assert_reason(error, RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT)
    assert _state_sha256(connection, root) == before


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ({"project_id": "project-b"}, RawV2Reason.RAW_STATE_INVALID),
        ({"source_object_id": "other-object"}, RawV2Reason.RAW_STATE_INVALID),
        ({"source_version": "version-other"}, RawV2Reason.RAW_STATE_INVALID),
        ({"source_acl_ref": "source-private"}, RawV2Reason.RAW_STATE_INVALID),
        ({"status": "reference_only"}, RawV2Reason.RAW_STATE_INVALID),
    ],
    ids=["project", "object", "version", "acl", "status"],
)
def test_linked_event_requires_exact_project_source_acl_and_object_state(
    tmp_path: Path,
    changed: dict[str, object],
    reason: RawV2Reason,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _event_store(connection)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as error:
        store.persist(request=replace(_event_request(raw_object_id=raw.raw_object_id), **changed))

    _assert_reason(error, reason)
    assert _state_sha256(connection, root) == before


def test_rejected_and_linked_status_shapes_fail_closed_without_rows(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _event_store(connection)
    before = _state_sha256(connection, root)

    invalid_requests = (
        _event_request(raw_object_id=None, status="persisted"),
        _event_request(raw_object_id=raw.raw_object_id, status="rejected"),
        _event_request(raw_object_id=raw.raw_object_id, status="unknown"),
    )
    for request in invalid_requests:
        with pytest.raises(RawV2ContractError) as error:
            store.persist(request=request)
        _assert_reason(error, RawV2Reason.RAW_STATE_INVALID)
        assert _state_sha256(connection, root) == before


def test_event_canonicalizes_time_unicode_and_empty_metadata(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    result = _event_store(connection).persist(
        request=_event_request(
            event_type="raw.é",
            mutation_id="mutation-é",
            raw_object_id=raw.raw_object_id,
            event_time="2026-09-01T07:58:00+08:00",
            observed_at="2026-09-01T07:59:00+08:00",
            trace_id="trace-é",
            metadata_projection={},
        )
    )
    row = _row(connection, "source_events_v2")
    assert result.created is True
    assert row["event_time"] == "2026-08-31T23:58:00.000000Z"
    assert row["observed_at"] == OBSERVED
    assert row["event_type"] == "raw.é"
    assert row["metadata_json"] == "{}"


def test_invalid_event_values_fail_before_rows_or_blob_changes(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _event_store(connection)
    before = _state_sha256(connection, root)
    cases = (
        (
            _event_request(raw_object_id=raw.raw_object_id, mutation_id=""),
            RawV2Reason.NON_IDEMPOTENT_REJECTED,
        ),
        (_event_request(raw_object_id=raw.raw_object_id, event_time="not-time"), RawV2Reason.TIMESTAMP_INVALID),
        (
            _event_request(raw_object_id=raw.raw_object_id, metadata_projection={"bad": 1.5}),
            RawV2Reason.CANONICALIZATION_MISMATCH,
        ),
    )
    for request, reason in cases:
        with pytest.raises(RawV2ContractError) as error:
            store.persist(request=request)
        _assert_reason(error, reason)
        assert _state_sha256(connection, root) == before


def test_event_active_transaction_and_schema_drift_fail_before_mutation(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _event_store(connection)
    request = _event_request(raw_object_id=raw.raw_object_id)
    before = _state_sha256(connection, root)

    connection.execute("BEGIN")
    with pytest.raises(RawV2ContractError) as transaction_error:
        store.persist(request=request)
    _assert_reason(transaction_error, RawV2Reason.RAW_STATE_INVALID)
    connection.rollback()
    assert _state_sha256(connection, root) == before

    connection.execute(
        """CREATE TRIGGER unreviewed_event_trigger
           AFTER INSERT ON source_events_v2 BEGIN SELECT 1; END"""
    )
    connection.commit()
    with pytest.raises(RawV2ContractError) as schema_error:
        store.persist(request=request)
    _assert_reason(schema_error, RawV2Reason.RAW_STATE_INVALID)
    assert _state_sha256(connection, root) == before


def test_event_database_failure_rolls_back_and_returns_no_id(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _event_store(connection)
    before = _state_sha256(connection, root)

    def deny_event_insert(action: int, table: str, *unused: object) -> int:
        del unused
        if action == sqlite3.SQLITE_INSERT and table == "source_events_v2":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_event_insert)
    try:
        with pytest.raises(RawV2ContractError) as error:
            store.persist(request=_event_request(raw_object_id=raw.raw_object_id))
    finally:
        connection.set_authorizer(None)

    _assert_reason(error, RawV2Reason.RAW_STATE_INVALID)
    assert connection.in_transaction is False
    assert _state_sha256(connection, root) == before


def test_event_write_conserves_v1_and_all_non_event_v2_state(tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE legacy_events(id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("INSERT INTO legacy_events VALUES ('legacy-1', 'unchanged')")
    connection.execute("PRAGMA user_version = 91")
    connection.commit()
    initialize_raw_v2_schema(connection)
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    legacy_before = connection.execute("SELECT * FROM legacy_events").fetchall()
    non_event_before = {
        table: _database_projection(connection)[table]
        for table in RAW_V2_TABLES
        if table != "source_events_v2"
    }
    tree_before = _tree_projection(root)

    _event_store(connection).persist(request=_event_request(raw_object_id=raw.raw_object_id))

    assert connection.execute("SELECT * FROM legacy_events").fetchall() == legacy_before
    assert connection.execute("PRAGMA user_version").fetchone() == (91,)
    assert {
        table: _database_projection(connection)[table]
        for table in RAW_V2_TABLES
        if table != "source_events_v2"
    } == non_event_before
    assert _tree_projection(root) == tree_before


def test_two_connections_serialize_to_one_exact_event(tmp_path: Path) -> None:
    database = tmp_path / "events.sqlite3"
    root = tmp_path / "blobs"
    root.mkdir()
    bootstrap = sqlite3.connect(database)
    initialize_raw_v2_schema(bootstrap)
    raw = _active_object(bootstrap, root)
    bootstrap.close()
    request = _event_request(raw_object_id=raw.raw_object_id)
    barrier = threading.Barrier(2)

    def write() -> RawEventRecord:
        connection = sqlite3.connect(database, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        store = _event_store(connection)
        barrier.wait(timeout=5.0)
        try:
            return store.persist(request=request)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: write(), range(2)))

    verified = sqlite3.connect(database)
    assert len({item.event_id for item in results}) == 1
    assert sorted(item.created for item in results) == [False, True]
    assert _count(verified, "source_events_v2") == 1
    assert _count(verified, "raw_objects_v2") == 1
    assert _count(verified, "raw_blobs_v2") == 1
    verified.close()
