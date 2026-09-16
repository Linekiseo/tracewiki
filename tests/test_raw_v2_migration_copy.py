from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.sources.schema import SCHEMA as V1_SCHEMA
from evidence_rag.sources.v2.contracts import RawV2ContractError, RawV2Reason
from evidence_rag.sources.v2.migration_v1 import (
    MIGRATION_V1_COPIER_VERSION,
    MigrationCategory,
    MigrationDerivedEntityClaim,
    MigrationInputMode,
    MigrationSafeCopyCheckpoint,
    MigrationSourceObjectClaim,
    MigrationStableVersionClaim,
    RawV1MigrationScanner,
    RawV1SafeCopier,
)
from evidence_rag.sources.v2.policy import RawV2ProjectPolicyAdapter
from evidence_rag.sources.v2.schema import initialize_raw_v2_schema

TS = "2026-09-01T00:00:00.000000Z"
SOURCE_REVISION = "333f1aa5d2147938247a39de91bc37aca6785ce3"
V2_TABLES = (
    "raw_blobs_v2",
    "raw_objects_v2",
    "source_events_v2",
    "raw_evidence_bindings_v2",
    "blocked_entities_v2",
    "raw_migration_runs_v2",
    "raw_migration_findings_v2",
)
V1_TABLES = ("raw_objects", "source_events", "raw_derivations", "blocked_entities")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


@dataclasses.dataclass(frozen=True)
class SourceFixture:
    db_path: Path
    blob_root: Path
    claims: tuple[MigrationSourceObjectClaim, ...]


@dataclasses.dataclass(frozen=True)
class TargetFixture:
    db_path: Path
    blob_root: Path
    connection: sqlite3.Connection
    schema_sha256: str


class _Authority:
    def project_exists(self, *, project_id: str) -> bool:
        return project_id in {"project-a", "project-b"}

    def resolve_object_acl_ref(self, *, project_id: str, source_acl_ref: str) -> str | None:
        if (project_id, source_acl_ref) in {
            ("project-a", "acl-a"),
            ("project-b", "acl-b"),
        }:
            return source_acl_ref
        return None

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None:
        del principal_id
        return {"project-a": ("acl-a",), "project-b": ("acl-b",)}.get(project_id)


def _policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


def _source(tmp_path: Path) -> SourceFixture:
    root = tmp_path / "source-blobs"
    root.mkdir(parents=True)
    facts = (
        ("raw-b", "project-a", "acl-a", "object-b", b"bravo"),
        ("raw-a", "project-a", "acl-a", "object-a", b"alpha"),
        ("raw-c", "project-b", "acl-b", "object-c", b"charlie"),
    )
    connection = sqlite3.connect(tmp_path / "source.sqlite3")
    connection.executescript(V1_SCHEMA)
    claims: list[MigrationSourceObjectClaim] = []
    for ordinal, (raw_id, project, acl, source_object, payload) in enumerate(facts, start=1):
        path = root / f"{raw_id}.bin"
        path.write_bytes(payload)
        digest = _sha256(payload)
        connection.execute(
            """INSERT INTO raw_objects(
                   id, project_id, source_type, source_instance, source_object_id,
                   source_version, source_uri, content_hash, media_type, byte_length,
                   storage_path, acl_ref, state, adapter_version, schema_version,
                   metadata_json, observed_at, tombstoned_at
               ) VALUES (?, ?, 'git', 'repo-a', ?, 'version-1', ?, ?,
                         'application/octet-stream', ?, ?, ?, 'active', 'adapter-v1',
                         'schema-v1', ?, ?, NULL)""",
            (
                raw_id,
                project,
                source_object,
                f"source://private/{raw_id}",
                digest,
                len(payload),
                str(path),
                acl,
                json.dumps({"private": f"object-secret-{ordinal}"}),
                TS,
            ),
        )
        event_source_object = source_object if raw_id != "raw-c" else "object-mismatch"
        connection.execute(
            """INSERT INTO source_events(
                   event_id, idempotency_key, source_type, source_instance, event_type,
                   source_object_id, source_version, event_time, observed_at, project_id,
                   acl_ref, content_hash, raw_object_id, payload_ref, trace_id,
                   schema_version, status, metadata_json, created_at
               ) VALUES (?, ?, 'git', 'repo-a', 'observed', ?, 'version-1', ?, ?, ?,
                         ?, ?, ?, ?, ?, 'schema-v1', 'persisted', ?, ?)""",
            (
                f"event-{raw_id}",
                f"mutation-{raw_id}",
                event_source_object,
                TS,
                TS,
                project,
                acl,
                digest,
                raw_id,
                f"payload://private/{raw_id}",
                f"private-trace-{raw_id}",
                json.dumps({"private": f"event-secret-{ordinal}"}),
                TS,
            ),
        )
        connection.execute(
            """INSERT INTO raw_derivations(
                   raw_object_id, derived_entity_id, derived_kind, generation_id,
                   derivation_version, created_at, invalidated_at
               ) VALUES (?, ?, 'chunk', 'generation-1', 'derive-v1', ?, NULL)""",
            (raw_id, f"entity-{raw_id}", TS),
        )
        claims.append(
            MigrationSourceObjectClaim(
                project_id=project,
                acl_ref=acl,
                source_type="git",
                source_instance="repo-a",
                source_object_id=source_object,
                source_version="version-1",
                content_hash=digest,
            )
        )
    connection.commit()
    connection.close()
    return SourceFixture(tmp_path / "source.sqlite3", root, tuple(claims))


def _target(tmp_path: Path) -> TargetFixture:
    root = tmp_path / "target-blobs"
    root.mkdir(parents=True)
    db_path = tmp_path / "target.sqlite3"
    connection = sqlite3.connect(db_path)
    snapshot = initialize_raw_v2_schema(connection)
    return TargetFixture(db_path, root, connection, snapshot.schema_sha256)


def _opener(
    source: SourceFixture,
    *,
    calls: list[sqlite3.Connection] | None = None,
) -> Callable[[], sqlite3.Connection]:
    def open_connection() -> sqlite3.Connection:
        connection = sqlite3.connect(source.db_path)
        if calls is not None:
            calls.append(connection)
        return connection

    return open_connection


def _freeze_scan(source: SourceFixture):
    connection = sqlite3.connect(source.db_path)
    try:
        binding = RawV1MigrationScanner.freeze_fixture(
            connection=connection,
            dataset_id="safe-copy-fixture-v1",
            source_revision=SOURCE_REVISION,
            blob_root=source.blob_root,
            source_claims=source.claims,
        )
    finally:
        connection.close()
    report = RawV1MigrationScanner.scan(binding=binding, opener=_opener(source))
    assert report.unclassified_count == 0
    assert {item.category for item in report.classifications} == {MigrationCategory.SAFE_COPY}
    return binding, report


def _plan(binding: Any, report: Any, target: TargetFixture, *, batch_size: int = 2):
    raw_fingerprints = tuple(
        item.row_fingerprint for item in report.classifications if item.table == "raw_objects"
    )
    derivation_fingerprints = tuple(
        item.row_fingerprint for item in report.classifications if item.table == "raw_derivations"
    )
    assert len(raw_fingerprints) == len(derivation_fingerprints) == 3
    stable_claims = tuple(
        MigrationStableVersionClaim(
            source_row_fingerprint=fingerprint,
            stable_version=f"stable-{index}",
        )
        for index, fingerprint in enumerate(raw_fingerprints, start=1)
    )
    projects_by_raw_order = ("project-a", "project-a", "project-b")
    derived_claims = tuple(
        MigrationDerivedEntityClaim(
            source_row_fingerprint=fingerprint,
            project_id=projects_by_raw_order[index],
        )
        for index, fingerprint in enumerate(derivation_fingerprints)
    )
    return RawV1SafeCopier.freeze_copy_plan(
        binding=binding,
        scan_report=report,
        target_schema_sha256=target.schema_sha256,
        batch_size=batch_size,
        stable_version_claims=stable_claims,
        derived_entity_claims=derived_claims,
    )


def _copy(
    source: SourceFixture,
    target: TargetFixture,
    *,
    binding: Any,
    report: Any,
    plan: Any,
    observer: Callable[[int, MigrationSafeCopyCheckpoint], None] | None = None,
    calls: list[sqlite3.Connection] | None = None,
):
    return RawV1SafeCopier.copy_fixture(
        binding=binding,
        scan_report=report,
        plan=plan,
        source_opener=_opener(source, calls=calls),
        target_connection=target.connection,
        target_blob_root=target.blob_root,
        policy=_policy(),
        clock=lambda: TS,
        batch_observer=observer,
    )


def _rows(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, list[list[Any]]]:
    result: dict[str, list[list[Any]]] = {}
    for table in tables:
        columns = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]
        selected = connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY {', '.join(columns)}"
        ).fetchall()
        result[table] = [list(row) for row in selected]
    return result


def _source_rows(source: SourceFixture) -> dict[str, list[list[Any]]]:
    connection = sqlite3.connect(source.db_path)
    try:
        return _rows(connection, V1_TABLES)
    finally:
        connection.close()


def _tree_projection(root: Path) -> list[list[str]]:
    result: list[list[str]] = []
    for path in sorted(root.rglob("*")):
        kind = "symlink" if path.is_symlink() else "dir" if path.is_dir() else "file"
        digest = _sha256(path.read_bytes()) if kind == "file" else ""
        result.append([path.relative_to(root).as_posix(), kind, digest])
    return result


def _authority_projection(target: TargetFixture) -> dict[str, Any]:
    return {
        "rows": _rows(target.connection, V2_TABLES),
        "tree": _tree_projection(target.blob_root),
    }


def _run_status(target: TargetFixture) -> str:
    row = target.connection.execute("SELECT status FROM raw_migration_runs_v2").fetchone()
    assert row is not None
    return str(row[0])


def test_clean_copy_recomputes_authority_and_never_writes_v1(tmp_path: Path) -> None:
    source = _source(tmp_path / "source")
    target = _target(tmp_path / "target")
    binding, report = _freeze_scan(source)
    plan = _plan(binding, report, target)
    source_rows_before = _source_rows(source)
    source_tree_before = _tree_projection(source.blob_root)
    trace: list[str] = []
    target.connection.set_trace_callback(trace.append)

    result = _copy(source, target, binding=binding, report=report, plan=plan)

    assert MIGRATION_V1_COPIER_VERSION == "raw-v1-fixture-safe-copier-v1"
    assert result.status == "copied"
    assert result.copied_object_count == 3
    assert result.copied_event_count == 2
    assert result.skipped_event_count == 1
    assert result.binding_candidate_count == 3
    assert len(result.binding_candidates) == 3
    assert all(item.selector_kind == "whole_object" for item in result.binding_candidates)
    assert all(
        item.raw_content_sha256 == item.selected_content_sha256
        for item in result.binding_candidates
    )
    assert math.ceil(3 / plan.batch_size) == 2
    assert sum(statement == "BEGIN IMMEDIATE" for statement in trace) == 3

    checkpoint_raw, counters_raw, status = target.connection.execute(
        "SELECT checkpoint_json, counters_json, status FROM raw_migration_runs_v2"
    ).fetchone()
    checkpoint = json.loads(checkpoint_raw)
    counters = json.loads(counters_raw)
    assert set(checkpoint) == {"copier_version", "last_source_row_fingerprint"}
    assert checkpoint["copier_version"] == MIGRATION_V1_COPIER_VERSION
    assert checkpoint["last_source_row_fingerprint"].startswith("sha256:")
    assert counters == {
        "binding_candidate_count": 3,
        "copied_event_count": 2,
        "copied_object_count": 3,
        "eligible_object_count": 3,
        "skipped_event_count": 1,
    }
    assert status == "copied"
    serialized_ledger = checkpoint_raw + counters_raw
    for forbidden in (
        "raw-a",
        "raw-b",
        "raw-c",
        "source://",
        "payload://",
        "private-trace",
        "object-secret",
        "event-secret",
        str(source.blob_root),
    ):
        assert forbidden not in serialized_ledger

    objects = target.connection.execute(
        "SELECT raw_object_id, metadata_json, raw_content_sha256, blob_sha256 FROM raw_objects_v2"
    ).fetchall()
    assert len(objects) == 3
    assert all(str(row[0]).startswith("raw-v2:") for row in objects)
    assert all(row[0] not in {"raw-a", "raw-b", "raw-c"} for row in objects)
    assert all(row[1] == "{}" and row[2] == row[3] for row in objects)
    events = target.connection.execute(
        "SELECT event_id, metadata_json, trace_id FROM source_events_v2 ORDER BY event_id"
    ).fetchall()
    assert len(events) == 2
    assert all(str(row[0]).startswith("source-event-v2:") for row in events)
    assert all(row[1] == "{}" and str(row[2]).startswith("migration-event:") for row in events)
    assert target.connection.execute(
        "SELECT COUNT(*) FROM raw_evidence_bindings_v2"
    ).fetchone() == (0,)
    assert target.connection.execute("SELECT COUNT(*) FROM blocked_entities_v2").fetchone() == (0,)
    assert target.connection.execute(
        "SELECT COUNT(*) FROM raw_migration_findings_v2"
    ).fetchone() == (0,)
    assert _source_rows(source) == source_rows_before
    assert _tree_projection(source.blob_root) == source_tree_before
    assert not (set(binding.allowed_operations) & set(binding.forbidden_operations))


class _BoundaryStop(RuntimeError):
    pass


@pytest.mark.parametrize("stop_after", [1, 2, 3])
def test_every_batch_boundary_resumes_to_clean_projection(
    tmp_path: Path,
    stop_after: int,
) -> None:
    clean_source = _source(tmp_path / "clean-source")
    clean_target = _target(tmp_path / "clean-target")
    clean_binding, clean_report = _freeze_scan(clean_source)
    clean_plan = _plan(clean_binding, clean_report, clean_target, batch_size=1)
    clean_result = _copy(
        clean_source,
        clean_target,
        binding=clean_binding,
        report=clean_report,
        plan=clean_plan,
    )
    clean_projection = _authority_projection(clean_target)

    resumed_source = _source(tmp_path / f"resumed-source-{stop_after}")
    resumed_target = _target(tmp_path / f"resumed-target-{stop_after}")
    binding, report = _freeze_scan(resumed_source)
    plan = _plan(binding, report, resumed_target, batch_size=1)

    def interrupt(batch_number: int, checkpoint: MigrationSafeCopyCheckpoint) -> None:
        assert checkpoint.last_source_row_fingerprint.startswith("sha256:")
        if batch_number == stop_after:
            raise _BoundaryStop(f"stopped after {batch_number}")

    with pytest.raises(_BoundaryStop, match=f"stopped after {stop_after}"):
        _copy(
            resumed_source,
            resumed_target,
            binding=binding,
            report=report,
            plan=plan,
            observer=interrupt,
        )
    resumed_result = _copy(
        resumed_source,
        resumed_target,
        binding=binding,
        report=report,
        plan=plan,
    )

    assert resumed_result.identity_sha256 == clean_result.identity_sha256
    assert _authority_projection(resumed_target) == clean_projection
    assert _run_status(resumed_target) == "copied"


@pytest.mark.parametrize(
    ("table", "column", "reason"),
    [
        ("raw_objects_v2", "metadata_json", RawV2Reason.LOGICAL_IDENTITY_PAYLOAD_CONFLICT),
        ("source_events_v2", "metadata_json", RawV2Reason.IDEMPOTENCY_PAYLOAD_CONFLICT),
    ],
)
def test_exact_retry_is_stable_and_different_payload_never_overwrites(
    tmp_path: Path,
    table: str,
    column: str,
    reason: RawV2Reason,
) -> None:
    source = _source(tmp_path / table)
    target = _target(tmp_path / f"target-{table}")
    binding, report = _freeze_scan(source)
    plan = _plan(binding, report, target)
    first = _copy(source, target, binding=binding, report=report, plan=plan)
    second = _copy(source, target, binding=binding, report=report, plan=plan)
    assert second.identity_sha256 == first.identity_sha256

    target.connection.execute(f"UPDATE {table} SET {column}='{{\"tampered\":true}}' WHERE rowid=1")
    target.connection.commit()
    protected_before = {
        name: rows
        for name, rows in _rows(target.connection, V2_TABLES).items()
        if name != "raw_migration_runs_v2"
    }
    tree_before = _tree_projection(target.blob_root)
    with pytest.raises(RawV2ContractError) as failure:
        _copy(source, target, binding=binding, report=report, plan=plan)
    assert failure.value.reason is reason
    protected_after = {
        name: rows
        for name, rows in _rows(target.connection, V2_TABLES).items()
        if name != "raw_migration_runs_v2"
    }
    assert protected_after == protected_before
    assert _tree_projection(target.blob_root) == tree_before
    assert _run_status(target) == "failed"


def test_plan_and_target_gates_reject_before_source_open_or_target_write(tmp_path: Path) -> None:
    source = _source(tmp_path / "source")
    target = _target(tmp_path / "target")
    binding, report = _freeze_scan(source)
    plan = _plan(binding, report, target)
    raw_claims = plan.stable_version_claims
    with pytest.raises(RawV2ContractError) as missing:
        RawV1SafeCopier.freeze_copy_plan(
            binding=binding,
            scan_report=report,
            target_schema_sha256=target.schema_sha256,
            batch_size=2,
            stable_version_claims=raw_claims[:-1],
            derived_entity_claims=plan.derived_entity_claims,
        )
    assert missing.value.reason is RawV2Reason.STABLE_VERSION_UNAVAILABLE

    calls: list[sqlite3.Connection] = []
    invalid_binding = dataclasses.replace(binding, mode=MigrationInputMode.MIRROR)
    before = _authority_projection(target)
    with pytest.raises(RawV2ContractError) as unauthorized:
        _copy(
            source,
            target,
            binding=invalid_binding,
            report=report,
            plan=plan,
            calls=calls,
        )
    assert unauthorized.value.reason is RawV2Reason.MIGRATION_NOT_AUTHORIZED
    assert calls == []
    assert _authority_projection(target) == before

    target.connection.execute("DROP INDEX idx_raw_v2_lookup")
    target.connection.commit()
    drifted_before = _rows(target.connection, V2_TABLES)
    with pytest.raises(RawV2ContractError) as schema_drift:
        _copy(source, target, binding=binding, report=report, plan=plan, calls=calls)
    assert schema_drift.value.reason is RawV2Reason.RAW_STATE_INVALID
    assert calls == []
    assert _rows(target.connection, V2_TABLES) == drifted_before


@pytest.mark.parametrize("drift", ["row", "blob"])
def test_source_or_blob_drift_invalidates_before_object_copy(tmp_path: Path, drift: str) -> None:
    source = _source(tmp_path / drift)
    target = _target(tmp_path / f"target-{drift}")
    binding, report = _freeze_scan(source)
    plan = _plan(binding, report, target)
    if drift == "row":
        connection = sqlite3.connect(source.db_path)
        connection.execute(
            "UPDATE raw_objects SET observed_at=? WHERE id='raw-a'",
            ("2026-09-01T00:00:01.000000Z",),
        )
        connection.commit()
        connection.close()
    else:
        (source.blob_root / "raw-a.bin").write_bytes(b"ALPHA")

    with pytest.raises(RawV2ContractError) as changed:
        _copy(source, target, binding=binding, report=report, plan=plan)
    assert changed.value.reason is RawV2Reason.MIGRATION_INPUT_CHANGED
    assert target.connection.execute("SELECT COUNT(*) FROM raw_objects_v2").fetchone() == (0,)
    assert target.connection.execute("SELECT COUNT(*) FROM source_events_v2").fetchone() == (0,)
    assert target.connection.execute("SELECT COUNT(*) FROM raw_blobs_v2").fetchone() == (0,)


def test_source_drift_after_committed_boundary_marks_run_failed(tmp_path: Path) -> None:
    source = _source(tmp_path / "source")
    target = _target(tmp_path / "target")
    binding, report = _freeze_scan(source)
    plan = _plan(binding, report, target, batch_size=1)
    changed = False

    def mutate_after_first_batch(
        batch_number: int,
        checkpoint: MigrationSafeCopyCheckpoint,
    ) -> None:
        nonlocal changed
        assert checkpoint.last_source_row_fingerprint.startswith("sha256:")
        if batch_number == 1:
            connection = sqlite3.connect(source.db_path)
            connection.execute(
                "UPDATE raw_objects SET observed_at=? WHERE id='raw-c'",
                ("2026-09-01T00:00:01.000000Z",),
            )
            connection.commit()
            connection.close()
            changed = True

    with pytest.raises(RawV2ContractError) as failure:
        _copy(
            source,
            target,
            binding=binding,
            report=report,
            plan=plan,
            observer=mutate_after_first_batch,
        )
    assert changed
    assert failure.value.reason is RawV2Reason.MIGRATION_INPUT_CHANGED
    assert _run_status(target) == "failed"
