from __future__ import annotations

import inspect
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
from evidence_rag.sources.v2.schema import initialize_raw_v2_schema
from evidence_rag.sources.v2.store import (
    RAW_V2_TOMBSTONE_STORE_VERSION,
    RawBindingRecord,
    RawBindingResolveRequest,
    RawBindingWriteRequest,
    RawObjectWriteRequest,
    RawTombstoneRecord,
    RawTombstoneRequest,
    RawV2BindingStore,
    RawV2ObjectStore,
    RawV2TombstoneStore,
)

NOW = "2026-09-01T00:00:00.000000Z"
LATER = "2026-09-01T00:00:10.000000Z"
OBSERVED = "2026-08-31T23:59:00.000000Z"
EVENT_TIME = "2026-08-31T23:58:00.000000Z"
ACTOR_DIGEST = "sha256:" + "a" * 64


class _Authority:
    projects = {"project-a", "project-b"}

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
        if source_acl_ref == "source-public":
            return "public"
        if source_acl_ref == "source-private":
            return f"team-{project_id[-1]}"
        return None

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None:
        del principal_id
        if project_id not in self.projects:
            return None
        return ()


class _FaultConnection(sqlite3.Connection):
    fail_contains: str | None = None

    def execute(self, sql: str, parameters: object = (), /):  # type: ignore[override]
        normalized = " ".join(sql.split())
        if self.fail_contains is not None and self.fail_contains in normalized:
            self.fail_contains = None
            raise sqlite3.OperationalError("injected tombstone transaction failure")
        return super().execute(sql, parameters)


def _policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


def _connection(
    path: Path | None = None,
    *,
    factory: type[sqlite3.Connection] = sqlite3.Connection,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    connection = sqlite3.connect(
        ":memory:" if path is None else path,
        timeout=10,
        factory=factory,
        check_same_thread=check_same_thread,
    )
    connection.execute("PRAGMA busy_timeout = 10000")
    initialize_raw_v2_schema(connection)
    return connection


def _object_request(
    *,
    project_id: str = "project-a",
    source_acl_ref: str = "source-public",
    source_object_id: str = "object-1",
    source_version: str = "version-1",
    stable_version: str = "stable-1",
) -> RawObjectWriteRequest:
    return RawObjectWriteRequest(
        project_id=project_id,
        source_type="git",
        source_instance_id="repository-1",
        source_object_id=source_object_id,
        source_version=source_version,
        stable_version=stable_version,
        source_acl_ref=source_acl_ref,
        media_type="text/plain",
        adapter_version="git-adapter-v2",
        schema_version="raw-object-v2",
        metadata_projection={},
        observed_at=OBSERVED,
    )


def _object_store(connection: sqlite3.Connection, root: Path) -> RawV2ObjectStore:
    return RawV2ObjectStore(
        connection=connection,
        blob_root=root,
        policy=_policy(),
        clock=lambda: NOW,
    )


def _binding_request(
    *,
    raw_object_id: str,
    project_id: str = "project-a",
    source_acl_ref: str = "source-public",
    source_version: str = "version-1",
    stable_version: str = "stable-1",
    generation_id: str = "generation-1",
    derived_entity_id: str = "entity-1",
    retrieval_unit_id: str = "unit-1",
    selector_start: int = 1,
) -> RawBindingWriteRequest:
    return RawBindingWriteRequest(
        project_id=project_id,
        raw_object_id=raw_object_id,
        source_acl_ref=source_acl_ref,
        source_version=source_version,
        stable_version=stable_version,
        generation_id=generation_id,
        derived_entity_id=derived_entity_id,
        retrieval_unit_id=retrieval_unit_id,
        derivation_kind="source_slice",
        derivation_version="derivation-v1",
        selector_kind="line_range",
        selector={"end_line": selector_start + 1, "start_line": selector_start},
        parser_artifact_sha256=None,
        valid_from=None,
        valid_to=None,
        observed_at=OBSERVED,
        adapter_version="git-adapter-v2",
        schema_version="raw-binding-v2",
    )


def _binding_store(
    connection: sqlite3.Connection,
    *,
    clock: Callable[[], str] | None = None,
) -> RawV2BindingStore:
    return RawV2BindingStore(
        connection=connection,
        policy=_policy(),
        clock=clock or (lambda: NOW),
    )


def _tombstone_request(
    *,
    raw_object_id: str,
    project_id: str = "project-a",
    source_acl_ref: str = "source-public",
    mutation_id: str = "delete-mutation-1",
    reason_code: str = "source_deleted",
    actor_digest: str = ACTOR_DIGEST,
    event_time: str | None = EVENT_TIME,
    observed_at: str = OBSERVED,
    trace_id: str = "trace-delete-1",
    schema_version: str = "raw-tombstone-v1",
) -> RawTombstoneRequest:
    return RawTombstoneRequest(
        project_id=project_id,
        raw_object_id=raw_object_id,
        source_acl_ref=source_acl_ref,
        mutation_id=mutation_id,
        reason_code=reason_code,
        actor_digest=actor_digest,
        event_time=event_time,
        observed_at=observed_at,
        trace_id=trace_id,
        schema_version=schema_version,
    )


def _tombstone_store(
    connection: sqlite3.Connection,
    *,
    clock: Callable[[], str] | None = None,
) -> RawV2TombstoneStore:
    return RawV2TombstoneStore(
        connection=connection,
        policy=_policy(),
        clock=clock or (lambda: NOW),
    )


def _active_object(
    connection: sqlite3.Connection,
    root: Path,
    *,
    project_id: str = "project-a",
    source_object_id: str = "object-1",
    payload: bytes = b"raw tombstone payload",
):
    return _object_store(connection, root).persist_bytes(
        request=_object_request(
            project_id=project_id,
            source_object_id=source_object_id,
        ),
        payload=payload,
    )


def _binding(
    connection: sqlite3.Connection,
    *,
    raw_object_id: str,
    project_id: str = "project-a",
    entity_id: str = "entity-1",
    unit_id: str = "unit-1",
    selector_start: int = 1,
    selected_content: bytes = b"selected tombstone content",
) -> RawBindingRecord:
    return _binding_store(connection).persist(
        request=_binding_request(
            raw_object_id=raw_object_id,
            project_id=project_id,
            derived_entity_id=entity_id,
            retrieval_unit_id=unit_id,
            selector_start=selector_start,
        ),
        selected_content=selected_content,
    )


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
                projected.append({"kind": "symlink", "path": relative, "target": os.readlink(item)})
            elif stat.S_ISDIR(mode):
                projected.append({"kind": "directory", "path": relative})
            elif stat.S_ISREG(mode):
                payload = item.read_bytes()
                projected.append(
                    {
                        "kind": "file",
                        "path": relative,
                        "byte_length": len(payload),
                        "sha256": exact_bytes_sha256(payload),
                    }
                )
            else:
                projected.append({"kind": "other", "path": relative})
    return sorted(projected, key=lambda item: (str(item["path"]), str(item["kind"])))


def _database_projection(connection: sqlite3.Connection) -> dict[str, object]:
    table_names = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    projected: dict[str, object] = {}
    for table in table_names:
        columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
        rows = [
            list(row)
            for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
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


def _row(
    connection: sqlite3.Connection,
    table: str,
    *,
    where: str = "1=1",
    parameters: tuple[object, ...] = (),
) -> dict[str, object]:
    cursor = connection.execute(f'SELECT * FROM "{table}" WHERE {where}', parameters)
    row = cursor.fetchone()
    assert row is not None
    return dict(zip((str(item[0]) for item in cursor.description or ()), row, strict=True))


def _rows(
    connection: sqlite3.Connection,
    table: str,
    *,
    where: str = "1=1",
    parameters: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    cursor = connection.execute(f'SELECT * FROM "{table}" WHERE {where} ORDER BY rowid', parameters)
    columns = [str(item[0]) for item in cursor.description or ()]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _count(
    connection: sqlite3.Connection,
    table: str,
    *,
    where: str = "1=1",
    parameters: tuple[object, ...] = (),
) -> int:
    row = connection.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}', parameters).fetchone()
    assert row is not None
    return int(row[0])


def _assert_reason(
    error: pytest.ExceptionInfo[RawV2ContractError],
    *reasons: RawV2Reason,
) -> None:
    assert error.value.reason in reasons


def test_tombstone_api_is_minimal_internal_and_has_no_filesystem_coupling() -> None:
    assert RAW_V2_TOMBSTONE_STORE_VERSION == "raw-v2-tombstone-store-v1"
    assert {item.name for item in fields(RawTombstoneRequest)} == {
        "project_id",
        "raw_object_id",
        "source_acl_ref",
        "mutation_id",
        "reason_code",
        "actor_digest",
        "event_time",
        "observed_at",
        "trace_id",
        "schema_version",
    }
    assert {item.name for item in fields(RawTombstoneRecord)} == {
        "raw_object_id",
        "project_id",
        "event_id",
        "tombstoned_at",
        "invalidated_binding_count",
        "blocked_entity_count",
        "created",
    }
    source = inspect.getsource(RawV2TombstoneStore)
    assert "open(" not in source
    assert "os.open" not in source
    assert "Path(" not in source
    assert "DELETE FROM raw_blobs_v2" not in source
    assert "UPDATE raw_blobs_v2" not in source


def test_atomic_tombstone_invalidates_all_active_bindings_blocks_unique_entities_and_events(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    bindings = [
        _binding(
            connection,
            raw_object_id=object_record.raw_object_id,
            entity_id="entity-1",
            unit_id="unit-1",
            selector_start=1,
        ),
        _binding(
            connection,
            raw_object_id=object_record.raw_object_id,
            entity_id="entity-1",
            unit_id="unit-2",
            selector_start=3,
        ),
        _binding(
            connection,
            raw_object_id=object_record.raw_object_id,
            entity_id="entity-2",
            unit_id="unit-3",
            selector_start=5,
        ),
    ]
    tree_before = _tree_projection(root)
    blob_rows_before = _rows(connection, "raw_blobs_v2")

    result = _tombstone_store(connection).tombstone(
        request=_tombstone_request(raw_object_id=object_record.raw_object_id)
    )

    assert result == RawTombstoneRecord(
        raw_object_id=object_record.raw_object_id,
        project_id="project-a",
        event_id=result.event_id,
        tombstoned_at=OBSERVED,
        invalidated_binding_count=3,
        blocked_entity_count=2,
        created=True,
    )
    assert result.event_id.startswith("source-event-v2:")
    object_row = _row(connection, "raw_objects_v2")
    assert object_row["state"] == "tombstoned"
    assert object_row["tombstoned_at"] == OBSERVED
    binding_rows = _rows(connection, "raw_evidence_bindings_v2")
    assert {row["locator_id"] for row in binding_rows} == {
        binding.locator_id for binding in bindings
    }
    assert {(row["invalidated_at"], row["invalidation_reason"]) for row in binding_rows} == {
        (OBSERVED, "source_deleted")
    }
    block_rows = _rows(connection, "blocked_entities_v2")
    assert {row["entity_id"] for row in block_rows} == {"entity-1", "entity-2"}
    assert {
        (
            row["project_id"],
            row["raw_object_id"],
            row["reason_code"],
            row["blocked_at"],
            row["actor_digest"],
        )
        for row in block_rows
    } == {
        (
            "project-a",
            object_record.raw_object_id,
            "source_deleted",
            OBSERVED,
            ACTOR_DIGEST,
        )
    }
    event = _row(connection, "source_events_v2")
    assert event["event_id"] == result.event_id
    assert event["project_id"] == "project-a"
    assert event["source_domain"] == "code"
    assert event["source_type"] == "git"
    assert event["source_instance_id"] == "repository-1"
    assert event["event_type"] == "tombstone"
    assert event["mutation_id"] == "delete-mutation-1"
    assert event["source_object_id"] == "object-1"
    assert event["source_version"] == "version-1"
    assert event["raw_object_id"] == object_record.raw_object_id
    assert event["raw_content_sha256"] == object_record.raw_content_sha256
    assert event["status"] == "tombstone"
    locator_ids = sorted(binding.locator_id for binding in bindings)
    entity_ids = ["entity-1", "entity-2"]
    assert json.loads(str(event["metadata_json"])) == {
        "actor_digest": ACTOR_DIGEST,
        "blocked_entity_count": 2,
        "blocked_entity_set_sha256": exact_bytes_sha256(canonical_json_bytes(entity_ids)),
        "invalidated_binding_count": 3,
        "invalidated_locator_set_sha256": exact_bytes_sha256(canonical_json_bytes(locator_ids)),
        "reason_code": "source_deleted",
    }
    assert _rows(connection, "raw_blobs_v2") == blob_rows_before
    assert _tree_projection(root) == tree_before


def test_active_object_without_bindings_tombstones_without_fabricating_blocks(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    result = _tombstone_store(connection).tombstone(
        request=_tombstone_request(raw_object_id=object_record.raw_object_id)
    )
    assert result.invalidated_binding_count == 0
    assert result.blocked_entity_count == 0
    assert _count(connection, "blocked_entities_v2") == 0
    assert _count(connection, "source_events_v2") == 1


def test_reference_only_object_tombstones_without_bytes_or_blocks(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _object_store(connection, root).persist_reference(request=_object_request())
    assert object_record.raw_content_sha256 is None
    tree_before = _tree_projection(root)
    result = _tombstone_store(connection).tombstone(
        request=_tombstone_request(raw_object_id=object_record.raw_object_id)
    )
    assert result.invalidated_binding_count == 0
    assert result.blocked_entity_count == 0
    assert _row(connection, "raw_objects_v2")["state"] == "tombstoned"
    assert _row(connection, "source_events_v2")["raw_content_sha256"] is None
    assert _tree_projection(root) == tree_before


@pytest.mark.parametrize("state", ["quarantined", "corrupt"])
def test_unreadable_but_well_formed_object_can_still_be_revoked(
    tmp_path: Path,
    state: str,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    connection.execute(
        "UPDATE raw_objects_v2 SET state=? WHERE project_id=? AND raw_object_id=?",
        (state, "project-a", object_record.raw_object_id),
    )
    connection.commit()
    result = _tombstone_store(connection).tombstone(
        request=_tombstone_request(raw_object_id=object_record.raw_object_id)
    )
    assert result.created is True
    assert _row(connection, "raw_objects_v2")["state"] == "tombstoned"


def test_exact_retry_with_changed_clock_is_write_free(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    _binding(connection, raw_object_id=object_record.raw_object_id)
    request = _tombstone_request(raw_object_id=object_record.raw_object_id)
    first = _tombstone_store(connection, clock=lambda: NOW).tombstone(request=request)
    state_before = _state_sha256(connection, root)
    second = _tombstone_store(connection, clock=lambda: LATER).tombstone(request=request)
    assert second == replace(first, created=False)
    assert _state_sha256(connection, root) == state_before
    assert _count(connection, "source_events_v2") == 1


@pytest.mark.parametrize(
    "change",
    [
        {"mutation_id": "delete-mutation-2"},
        {"reason_code": "retention_expired"},
        {"actor_digest": "sha256:" + "b" * 64},
        {"event_time": "2026-08-31T23:57:00Z"},
        {"observed_at": "2026-08-31T23:59:30Z"},
        {"trace_id": "trace-delete-2"},
        {"schema_version": "raw-tombstone-v2"},
    ],
)
def test_already_tombstoned_different_request_is_never_a_second_success(
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    _binding(connection, raw_object_id=object_record.raw_object_id)
    request = _tombstone_request(raw_object_id=object_record.raw_object_id)
    _tombstone_store(connection).tombstone(request=request)
    state_before = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError):
        _tombstone_store(connection).tombstone(request=replace(request, **change))
    assert _state_sha256(connection, root) == state_before
    assert _count(connection, "source_events_v2") == 1


@pytest.mark.parametrize(
    ("project_id", "source_acl_ref", "raw_object_id"),
    [
        ("project-b", "source-public", None),
        ("project-a", "source-private", None),
        ("project-a", "source-public", "raw-v2:" + "0" * 64),
    ],
)
def test_wrong_project_acl_or_absent_object_has_zero_side_effects(
    tmp_path: Path,
    project_id: str,
    source_acl_ref: str,
    raw_object_id: str | None,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    _binding(connection, raw_object_id=object_record.raw_object_id)
    request = _tombstone_request(
        raw_object_id=raw_object_id or object_record.raw_object_id,
        project_id=project_id,
        source_acl_ref=source_acl_ref,
    )
    state_before = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError):
        _tombstone_store(connection).tombstone(request=request)
    assert _state_sha256(connection, root) == state_before


@pytest.mark.parametrize(
    "change",
    [
        {"mutation_id": ""},
        {"reason_code": ""},
        {"actor_digest": "sha256:not-a-digest"},
        {"observed_at": "not-a-time"},
        {"event_time": "not-a-time"},
        {"trace_id": ""},
        {"schema_version": ""},
    ],
)
def test_invalid_request_fails_before_transaction_or_state_change(
    tmp_path: Path,
    change: dict[str, object],
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    request = _tombstone_request(raw_object_id=object_record.raw_object_id)
    state_before = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError):
        _tombstone_store(connection).tombstone(request=replace(request, **change))
    assert _state_sha256(connection, root) == state_before
    assert connection.in_transaction is False


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("source_domain", "wrong"),
        ("raw_content_sha256", "sha256:" + "1" * 64),
        ("blob_sha256", "sha256:" + "2" * 64),
        ("observed_at", "not-a-time"),
        ("tombstoned_at", OBSERVED),
    ],
)
def test_malformed_object_authority_fails_without_further_mutation(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA ignore_check_constraints = ON")
    connection.execute(
        f"UPDATE raw_objects_v2 SET {column}=? WHERE project_id=? AND raw_object_id=?",
        (value, "project-a", object_record.raw_object_id),
    )
    connection.commit()
    connection.execute("PRAGMA ignore_check_constraints = OFF")
    connection.execute("PRAGMA foreign_keys = ON")
    state_before = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError):
        _tombstone_store(connection).tombstone(
            request=_tombstone_request(raw_object_id=object_record.raw_object_id)
        )
    assert _state_sha256(connection, root) == state_before


def test_active_caller_transaction_and_schema_drift_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "blobs"
    root.mkdir()

    active = _connection()
    object_record = _active_object(active, root)
    request = _tombstone_request(raw_object_id=object_record.raw_object_id)
    active.execute("BEGIN")
    with pytest.raises(RawV2ContractError) as error:
        _tombstone_store(active).tombstone(request=request)
    _assert_reason(error, RawV2Reason.RAW_STATE_INVALID)
    active.rollback()
    assert _row(active, "raw_objects_v2")["state"] == "active"

    drifted = _connection()
    other_root = tmp_path / "other-blobs"
    other_root.mkdir()
    other = _active_object(drifted, other_root)
    drifted.execute("DROP INDEX idx_bindings_v2_raw")
    state_before = _state_sha256(drifted, other_root)
    with pytest.raises(RawV2ContractError) as drift_error:
        _tombstone_store(drifted).tombstone(
            request=_tombstone_request(raw_object_id=other.raw_object_id)
        )
    _assert_reason(drift_error, RawV2Reason.RAW_STATE_INVALID)
    assert _state_sha256(drifted, other_root) == state_before


@pytest.mark.parametrize("tamper", ["object", "binding", "block", "event"])
def test_exact_retry_detects_partial_or_tampered_final_state_without_repair(
    tmp_path: Path,
    tamper: str,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    _binding(connection, raw_object_id=object_record.raw_object_id)
    request = _tombstone_request(raw_object_id=object_record.raw_object_id)
    _tombstone_store(connection).tombstone(request=request)
    if tamper == "object":
        connection.execute("UPDATE raw_objects_v2 SET tombstoned_at=?", (LATER,))
    elif tamper == "binding":
        connection.execute("UPDATE raw_evidence_bindings_v2 SET invalidation_reason='tampered'")
    elif tamper == "block":
        connection.execute(
            "UPDATE blocked_entities_v2 SET actor_digest=?",
            ("sha256:" + "b" * 64,),
        )
    else:
        connection.execute("UPDATE source_events_v2 SET metadata_json='{}'")
    connection.commit()
    state_before = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError):
        _tombstone_store(connection).tombstone(request=request)
    assert _state_sha256(connection, root) == state_before


def test_block_is_project_scoped_and_prevents_future_target_project_binding(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_a = _active_object(
        connection,
        root,
        project_id="project-a",
        source_object_id="object-a",
        payload=b"project a payload",
    )
    binding_a = _binding(
        connection,
        raw_object_id=object_a.raw_object_id,
        project_id="project-a",
        entity_id="shared-entity",
    )
    object_b = _active_object(
        connection,
        root,
        project_id="project-b",
        source_object_id="object-b",
        payload=b"project b payload",
    )
    binding_b = _binding(
        connection,
        raw_object_id=object_b.raw_object_id,
        project_id="project-b",
        entity_id="shared-entity",
    )

    tombstones = _tombstone_store(connection)
    tombstones.tombstone(
        request=_tombstone_request(
            raw_object_id=object_a.raw_object_id,
            project_id="project-a",
        )
    )
    assert tombstones.is_entity_blocked(project_id="project-a", entity_id="shared-entity")
    assert not tombstones.is_entity_blocked(project_id="project-b", entity_id="shared-entity")
    assert tombstones.count_blocked(project_id="project-a") == 1
    assert tombstones.count_blocked(project_id="project-b") == 0
    assert (
        _binding_store(connection).resolve(
            request=RawBindingResolveRequest(
                project_id="project-a", locator_id=binding_a.locator_id
            )
        )
        is None
    )
    assert (
        _binding_store(connection).resolve(
            request=RawBindingResolveRequest(
                project_id="project-b", locator_id=binding_b.locator_id
            )
        )
        is not None
    )

    replacement_a = _active_object(
        connection,
        root,
        project_id="project-a",
        source_object_id="object-a-2",
        payload=b"replacement a payload",
    )
    state_before = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError) as error:
        _binding(
            connection,
            raw_object_id=replacement_a.raw_object_id,
            project_id="project-a",
            entity_id="shared-entity",
            unit_id="replacement-unit-a",
        )
    _assert_reason(error, RawV2Reason.RAW_STATE_INVALID)
    assert _state_sha256(connection, root) == state_before

    replacement_b = _active_object(
        connection,
        root,
        project_id="project-b",
        source_object_id="object-b-2",
        payload=b"replacement b payload",
    )
    peer = _binding(
        connection,
        raw_object_id=replacement_b.raw_object_id,
        project_id="project-b",
        entity_id="shared-entity",
        unit_id="replacement-unit-b",
    )
    assert peer.created is True


def test_previously_invalidated_binding_is_not_rewritten_or_counted(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    old = _binding(
        connection,
        raw_object_id=object_record.raw_object_id,
        entity_id="old-entity",
        unit_id="old-unit",
        selector_start=1,
    )
    active = _binding(
        connection,
        raw_object_id=object_record.raw_object_id,
        entity_id="active-entity",
        unit_id="active-unit",
        selector_start=4,
    )
    connection.execute(
        """UPDATE raw_evidence_bindings_v2
           SET invalidated_at='2026-08-31T23:00:00.000000Z',
               invalidation_reason='superseded'
           WHERE locator_id=?""",
        (old.locator_id,),
    )
    connection.commit()
    old_before = _row(
        connection,
        "raw_evidence_bindings_v2",
        where="locator_id=?",
        parameters=(old.locator_id,),
    )
    result = _tombstone_store(connection).tombstone(
        request=_tombstone_request(raw_object_id=object_record.raw_object_id)
    )
    assert result.invalidated_binding_count == 1
    assert result.blocked_entity_count == 1
    assert (
        _row(
            connection,
            "raw_evidence_bindings_v2",
            where="locator_id=?",
            parameters=(old.locator_id,),
        )
        == old_before
    )
    active_after = _row(
        connection,
        "raw_evidence_bindings_v2",
        where="locator_id=?",
        parameters=(active.locator_id,),
    )
    assert active_after["invalidated_at"] == OBSERVED
    assert _rows(connection, "blocked_entities_v2")[0]["entity_id"] == "active-entity"


def test_same_entity_across_multiple_active_bindings_is_blocked_once(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    for index in range(3):
        _binding(
            connection,
            raw_object_id=object_record.raw_object_id,
            entity_id="one-entity",
            unit_id=f"unit-{index}",
            selector_start=index * 2 + 1,
        )
    result = _tombstone_store(connection).tombstone(
        request=_tombstone_request(raw_object_id=object_record.raw_object_id)
    )
    assert result.invalidated_binding_count == 3
    assert result.blocked_entity_count == 1
    assert _count(connection, "blocked_entities_v2") == 1


def test_output_recheck_succeeds_without_tombstone(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    _binding(connection, raw_object_id=object_record.raw_object_id)
    store = _binding_store(connection)
    resolved = store.resolve(
        request=RawBindingResolveRequest(
            project_id="project-a",
            derived_entity_id="entity-1",
            generation_id="generation-1",
        )
    )
    assert resolved is not None
    assert store.recheck_before_output(record=resolved) == resolved


def test_controlled_read_tombstone_race_returns_no_post_tombstone_bytes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "race.sqlite3"
    root = tmp_path / "blobs"
    root.mkdir()
    seed = _connection(database)
    object_record = _active_object(seed, root)
    binding = _binding(seed, raw_object_id=object_record.raw_object_id)
    seed.close()

    reader_connection = _connection(database)
    writer_connection = _connection(database)
    reader = _binding_store(reader_connection)
    resolved = reader.resolve(
        request=RawBindingResolveRequest(project_id="project-a", locator_id=binding.locator_id)
    )
    assert resolved is not None
    provisional_selected_bytes = b"must not escape after tombstone"
    returned: list[bytes] = []

    _tombstone_store(writer_connection).tombstone(
        request=_tombstone_request(raw_object_id=object_record.raw_object_id)
    )
    with pytest.raises(RawV2ContractError):
        reader.recheck_before_output(record=resolved)
        returned.append(provisional_selected_bytes)
    assert returned == []
    assert (
        reader.resolve(
            request=RawBindingResolveRequest(project_id="project-a", locator_id=binding.locator_id)
        )
        is None
    )
    reader_connection.close()
    writer_connection.close()


def test_output_recheck_rejects_a_caller_transaction_to_avoid_stale_snapshot(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    binding = _binding(connection, raw_object_id=object_record.raw_object_id)
    resolved = _binding_store(connection).resolve(
        request=RawBindingResolveRequest(project_id="project-a", locator_id=binding.locator_id)
    )
    assert resolved is not None
    connection.execute("BEGIN")
    with pytest.raises(RawV2ContractError) as error:
        _binding_store(connection).recheck_before_output(record=resolved)
    _assert_reason(error, RawV2Reason.RAW_STATE_INVALID)
    connection.rollback()


@pytest.mark.parametrize(
    "failure_marker",
    [
        "UPDATE raw_objects_v2 SET state='tombstoned'",
        "UPDATE raw_evidence_bindings_v2 SET invalidated_at=",
        "INSERT INTO blocked_entities_v2",
        "INSERT INTO source_events_v2",
    ],
)
def test_failure_at_every_mutation_stage_rolls_back_all_state(
    tmp_path: Path,
    failure_marker: str,
) -> None:
    connection = _connection(factory=_FaultConnection)
    assert isinstance(connection, _FaultConnection)
    root = tmp_path / "blobs"
    root.mkdir()
    object_record = _active_object(connection, root)
    _binding(connection, raw_object_id=object_record.raw_object_id)
    request = _tombstone_request(raw_object_id=object_record.raw_object_id)
    state_before = _state_sha256(connection, root)
    connection.fail_contains = failure_marker
    with pytest.raises(RawV2ContractError):
        _tombstone_store(connection).tombstone(request=request)
    assert connection.in_transaction is False
    assert _state_sha256(connection, root) == state_before


def test_two_connections_same_tombstone_serialize_to_one_create_and_one_retry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent.sqlite3"
    root = tmp_path / "blobs"
    root.mkdir()
    seed = _connection(database)
    object_record = _active_object(seed, root)
    _binding(seed, raw_object_id=object_record.raw_object_id)
    seed.close()
    barrier = threading.Barrier(2)

    def run() -> RawTombstoneRecord:
        connection = _connection(database, check_same_thread=False)
        try:
            barrier.wait(timeout=10)
            return _tombstone_store(connection).tombstone(
                request=_tombstone_request(raw_object_id=object_record.raw_object_id)
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: run(), range(2)))
    assert sorted(result.created for result in results) == [False, True]
    assert results[0].event_id == results[1].event_id
    check = _connection(database)
    assert _count(check, "source_events_v2") == 1
    assert _count(check, "blocked_entities_v2") == 1
    assert _row(check, "raw_objects_v2")["state"] == "tombstoned"
    check.close()


def test_two_connections_different_mutations_yield_one_success_and_one_conflict(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent-conflict.sqlite3"
    root = tmp_path / "blobs"
    root.mkdir()
    seed = _connection(database)
    object_record = _active_object(seed, root)
    _binding(seed, raw_object_id=object_record.raw_object_id)
    seed.close()
    barrier = threading.Barrier(2)

    def run(mutation_id: str) -> str:
        connection = _connection(database, check_same_thread=False)
        try:
            barrier.wait(timeout=10)
            _tombstone_store(connection).tombstone(
                request=_tombstone_request(
                    raw_object_id=object_record.raw_object_id,
                    mutation_id=mutation_id,
                )
            )
            return "created"
        except RawV2ContractError:
            return "conflict"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ("mutation-a", "mutation-b")))
    assert sorted(results) == ["conflict", "created"]
    check = _connection(database)
    assert _count(check, "source_events_v2") == 1
    assert _row(check, "raw_objects_v2")["state"] == "tombstoned"
    check.close()


def test_unrelated_v1_v2_migration_peer_project_and_blob_state_are_conserved(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    connection.execute("CREATE TABLE legacy_raw_v1(id TEXT PRIMARY KEY, payload TEXT)")
    connection.execute("INSERT INTO legacy_raw_v1 VALUES ('legacy-1', 'unchanged')")
    connection.execute(
        """INSERT INTO raw_migration_runs_v2(
               migration_run_id, mode, source_revision_sha256, source_schema_sha256,
               plan_sha256, status, checkpoint_json, counters_json, started_at
           ) VALUES ('migration-1','fixture',?,?,?,?,?,?,?)""",
        (
            "sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
            "sha256:" + "3" * 64,
            "planned",
            "{}",
            "{}",
            OBSERVED,
        ),
    )
    connection.execute(
        """INSERT INTO raw_migration_findings_v2(
               migration_run_id, finding_id, severity, category,
               source_row_fingerprint, resolution, detail_digest
           ) VALUES ('migration-1','finding-1','P2','fixture',?,
                     'safe_to_copy',?)""",
        ("sha256:" + "4" * 64, "sha256:" + "5" * 64),
    )
    connection.commit()
    target = _active_object(connection, root, source_object_id="target")
    _binding(connection, raw_object_id=target.raw_object_id, entity_id="target-entity")
    unrelated = _active_object(
        connection,
        root,
        source_object_id="unrelated",
        payload=b"unrelated payload",
    )
    unrelated_binding = _binding(
        connection,
        raw_object_id=unrelated.raw_object_id,
        entity_id="unrelated-entity",
        unit_id="unrelated-unit",
    )
    peer = _active_object(
        connection,
        root,
        project_id="project-b",
        source_object_id="peer",
        payload=b"peer payload",
    )
    peer_binding = _binding(
        connection,
        raw_object_id=peer.raw_object_id,
        project_id="project-b",
        entity_id="target-entity",
    )
    legacy_before = _rows(connection, "legacy_raw_v1")
    migration_before = (
        _rows(connection, "raw_migration_runs_v2"),
        _rows(connection, "raw_migration_findings_v2"),
    )
    blob_rows_before = _rows(connection, "raw_blobs_v2")
    tree_before = _tree_projection(root)
    unrelated_before = _row(
        connection,
        "raw_objects_v2",
        where="raw_object_id=?",
        parameters=(unrelated.raw_object_id,),
    )
    peer_before = _row(
        connection,
        "raw_objects_v2",
        where="raw_object_id=?",
        parameters=(peer.raw_object_id,),
    )

    _tombstone_store(connection).tombstone(
        request=_tombstone_request(raw_object_id=target.raw_object_id)
    )

    assert _rows(connection, "legacy_raw_v1") == legacy_before
    assert (
        _rows(connection, "raw_migration_runs_v2"),
        _rows(connection, "raw_migration_findings_v2"),
    ) == migration_before
    assert _rows(connection, "raw_blobs_v2") == blob_rows_before
    assert _tree_projection(root) == tree_before
    assert (
        _row(
            connection,
            "raw_objects_v2",
            where="raw_object_id=?",
            parameters=(unrelated.raw_object_id,),
        )
        == unrelated_before
    )
    assert (
        _row(
            connection,
            "raw_objects_v2",
            where="raw_object_id=?",
            parameters=(peer.raw_object_id,),
        )
        == peer_before
    )
    store = _binding_store(connection)
    assert (
        store.resolve(
            request=RawBindingResolveRequest(
                project_id="project-a", locator_id=unrelated_binding.locator_id
            )
        )
        is not None
    )
    assert (
        store.resolve(
            request=RawBindingResolveRequest(
                project_id="project-b", locator_id=peer_binding.locator_id
            )
        )
        is not None
    )
