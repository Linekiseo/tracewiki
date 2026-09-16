from __future__ import annotations

import sqlite3
from collections.abc import Callable

import pytest

from evidence_rag.sources.v2.schema import (
    RAW_V2_INDEXES,
    RAW_V2_SCHEMA_VERSION,
    RAW_V2_TABLES,
    RawV2SchemaError,
    initialize_raw_v2_schema,
    inspect_raw_v2_schema,
)

EXPECTED_SCHEMA_SHA256 = "sha256:64971b83aa1dfac0aa40ef1b68e00bd85d352f43caa4e379d614ca5dbcfec6be"

EXPECTED_COLUMNS = {
    "raw_blobs_v2": (
        "blob_sha256",
        "byte_length",
        "storage_key",
        "storage_state",
        "first_verified_at",
        "last_verified_at",
        "created_at",
    ),
    "raw_objects_v2": (
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
    ),
    "source_events_v2": (
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
    ),
    "raw_evidence_bindings_v2": (
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
    ),
    "blocked_entities_v2": (
        "project_id",
        "entity_id",
        "raw_object_id",
        "reason_code",
        "blocked_at",
        "actor_digest",
    ),
    "raw_migration_runs_v2": (
        "migration_run_id",
        "mode",
        "source_revision_sha256",
        "source_schema_sha256",
        "plan_sha256",
        "status",
        "checkpoint_json",
        "counters_json",
        "findings_sha256",
        "report_sha256",
        "started_at",
        "completed_at",
    ),
    "raw_migration_findings_v2": (
        "migration_run_id",
        "finding_id",
        "severity",
        "category",
        "source_row_fingerprint",
        "resolution",
        "detail_digest",
    ),
}

EXPECTED_INDEX_COLUMNS = {
    "idx_raw_v2_lookup": (
        "project_id",
        "source_domain",
        "source_instance_id",
        "source_object_id",
        "source_version",
        "state",
    ),
    "idx_raw_v2_visibility": (
        "project_id",
        "visibility_partition_sha256",
        "state",
        "observed_at",
    ),
    "idx_events_v2_project_time": ("project_id", "observed_at", "event_id"),
    "idx_events_v2_object": (
        "project_id",
        "source_domain",
        "source_instance_id",
        "source_object_id",
        "source_version",
    ),
    "idx_bindings_v2_entity": (
        "project_id",
        "derived_entity_id",
        "generation_id",
        "invalidated_at",
    ),
    "idx_bindings_v2_unit": (
        "project_id",
        "retrieval_unit_id",
        "generation_id",
        "invalidated_at",
    ),
    "idx_bindings_v2_raw": ("project_id", "raw_object_id", "invalidated_at"),
}

EXPECTED_UNIQUE_COLUMN_SETS = {
    "raw_blobs_v2": {("blob_sha256",)},
    "raw_objects_v2": {
        ("raw_object_id",),
        ("logical_identity_sha256",),
        ("project_id", "raw_object_id"),
    },
    "source_events_v2": {("event_id",), ("idempotency_sha256",)},
    "raw_evidence_bindings_v2": {("locator_id",), ("binding_sha256",)},
    "blocked_entities_v2": {("project_id", "entity_id")},
    "raw_migration_runs_v2": {("migration_run_id",)},
    "raw_migration_findings_v2": {("migration_run_id", "finding_id")},
}


def _connect() -> sqlite3.Connection:
    database = sqlite3.connect(":memory:")
    database.execute("PRAGMA foreign_keys = ON")
    return database


def _insert(database: sqlite3.Connection, table: str, values: dict[str, object]) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    database.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


def _blob(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "blob_sha256": "sha256:blob",
        "byte_length": 4,
        "storage_key": "ab/cd/blob",
        "storage_state": "available",
        "first_verified_at": None,
        "last_verified_at": None,
        "created_at": "2026-01-01T00:00:00.000000Z",
    }
    values.update(updates)
    return values


def _object(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "raw_object_id": "raw-v2:object",
        "logical_identity_sha256": "sha256:logical",
        "project_id": "project-a",
        "source_domain": "code",
        "source_type": "git",
        "source_instance_id": "repository-a",
        "source_object_id": "README.md",
        "source_version": "commit-a",
        "stable_version": "commit-a",
        "acl_ref": "acl-a",
        "visibility_partition_sha256": "sha256:visibility",
        "raw_content_sha256": "sha256:blob",
        "blob_sha256": "sha256:blob",
        "media_type": "text/markdown",
        "byte_length": 4,
        "state": "active",
        "adapter_version": "git-v2",
        "schema_version": "raw-v2",
        "metadata_json": "{}",
        "observed_at": "2026-01-01T00:00:00.000000Z",
        "tombstoned_at": None,
        "created_at": "2026-01-01T00:00:00.000000Z",
    }
    values.update(updates)
    return values


def _event(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "event_id": "source-event-v2:event",
        "idempotency_sha256": "sha256:event",
        "project_id": "project-a",
        "source_domain": "code",
        "source_type": "git",
        "source_instance_id": "repository-a",
        "event_type": "upsert",
        "mutation_id": "mutation-a",
        "source_object_id": "README.md",
        "source_version": "commit-a",
        "raw_object_id": "raw-v2:object",
        "raw_content_sha256": "sha256:blob",
        "acl_ref": "acl-a",
        "visibility_partition_sha256": "sha256:visibility",
        "event_time": None,
        "observed_at": "2026-01-01T00:00:00.000000Z",
        "trace_id": "trace-a",
        "schema_version": "raw-v2",
        "status": "persisted",
        "metadata_json": "{}",
        "created_at": "2026-01-01T00:00:00.000000Z",
    }
    values.update(updates)
    return values


def _binding(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "locator_id": "raw-locator-v2:binding",
        "binding_sha256": "sha256:binding",
        "project_id": "project-a",
        "source_domain": "code",
        "raw_object_id": "raw-v2:object",
        "source_version": "commit-a",
        "stable_version": "commit-a",
        "generation_id": "generation-a",
        "derived_entity_id": "entity-a",
        "retrieval_unit_id": "unit-a",
        "derivation_kind": "whole-object",
        "derivation_version": "v1",
        "selector_kind": "whole_object_v2",
        "selector_json": "{}",
        "selector_sha256": "sha256:selector",
        "raw_content_sha256": "sha256:blob",
        "selected_content_sha256": "sha256:selected",
        "parser_artifact_sha256": None,
        "acl_ref": "acl-a",
        "visibility_partition_sha256": "sha256:visibility",
        "valid_from": None,
        "valid_to": None,
        "observed_at": "2026-01-01T00:00:00.000000Z",
        "binding_json": "{}",
        "created_at": "2026-01-01T00:00:00.000000Z",
        "invalidated_at": None,
        "invalidation_reason": None,
    }
    values.update(updates)
    return values


def _migration_run(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "migration_run_id": "migration-a",
        "mode": "fixture",
        "source_revision_sha256": "sha256:revision",
        "source_schema_sha256": "sha256:source-schema",
        "plan_sha256": "sha256:plan",
        "status": "planned",
        "checkpoint_json": "{}",
        "counters_json": "{}",
        "findings_sha256": None,
        "report_sha256": None,
        "started_at": "2026-01-01T00:00:00.000000Z",
        "completed_at": None,
    }
    values.update(updates)
    return values


def _v2_authored_objects(database: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    placeholders = ",".join("?" for _ in (*RAW_V2_TABLES, *RAW_V2_INDEXES))
    rows = database.execute(
        f"""SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name IN ({placeholders})
            ORDER BY type, name""",
        (*RAW_V2_TABLES, *RAW_V2_INDEXES),
    ).fetchall()
    return tuple(tuple(row) for row in rows)


def test_initializer_creates_exact_reviewed_schema_and_portable_digest() -> None:
    database = _connect()
    snapshot = initialize_raw_v2_schema(database)

    assert snapshot.schema_version == RAW_V2_SCHEMA_VERSION
    assert snapshot.schema_sha256 == EXPECTED_SCHEMA_SHA256
    assert snapshot.table_names == RAW_V2_TABLES
    assert snapshot.index_names == RAW_V2_INDEXES
    assert len(_v2_authored_objects(database)) == 14

    for table, expected_columns in EXPECTED_COLUMNS.items():
        observed = tuple(row[1] for row in database.execute(f"PRAGMA table_info({table})"))
        assert observed == expected_columns
    for index, expected_columns in EXPECTED_INDEX_COLUMNS.items():
        observed = tuple(row[2] for row in database.execute(f"PRAGMA index_info({index})"))
        assert observed == expected_columns
    for table, expected_unique_sets in EXPECTED_UNIQUE_COLUMN_SETS.items():
        observed_unique_sets = {
            tuple(
                item[2]
                for item in database.execute(f"PRAGMA index_info({index_row[1]})")
            )
            for index_row in database.execute(f"PRAGMA index_list({table})")
            if index_row[2]
        }
        assert observed_unique_sets == expected_unique_sets

    foreign_keys = {
        table: {
            (row[2], row[3], row[4])
            for row in database.execute(f"PRAGMA foreign_key_list({table})")
        }
        for table in RAW_V2_TABLES
    }
    assert foreign_keys["raw_objects_v2"] == {
        ("raw_blobs_v2", "blob_sha256", "blob_sha256")
    }
    assert foreign_keys["source_events_v2"] == {
        ("raw_objects_v2", "project_id", "project_id"),
        ("raw_objects_v2", "raw_object_id", "raw_object_id"),
    }
    assert foreign_keys["raw_evidence_bindings_v2"] == foreign_keys["source_events_v2"]
    assert foreign_keys["blocked_entities_v2"] == foreign_keys["source_events_v2"]
    assert foreign_keys["raw_migration_findings_v2"] == {
        ("raw_migration_runs_v2", "migration_run_id", "migration_run_id")
    }
    assert not database.execute("PRAGMA foreign_key_check").fetchall()

    associated = database.execute(
        """SELECT type, name FROM sqlite_master
           WHERE type IN ('trigger', 'view')
             AND (name IN ({}) OR tbl_name IN ({}))""".format(
            ",".join("?" for _ in RAW_V2_TABLES),
            ",".join("?" for _ in RAW_V2_TABLES),
        ),
        (*RAW_V2_TABLES, *RAW_V2_TABLES),
    ).fetchall()
    assert associated == []


def test_valid_state_and_related_row_matrix() -> None:
    database = _connect()
    initialize_raw_v2_schema(database)
    _insert(database, "raw_blobs_v2", _blob())
    _insert(database, "raw_objects_v2", _object())
    _insert(
        database,
        "raw_objects_v2",
        _object(
            raw_object_id="raw-v2:reference",
            logical_identity_sha256="sha256:reference",
            source_object_id="reference.md",
            raw_content_sha256=None,
            blob_sha256=None,
            byte_length=None,
            state="reference_only",
        ),
    )
    _insert(database, "source_events_v2", _event())
    _insert(database, "raw_evidence_bindings_v2", _binding())
    _insert(
        database,
        "blocked_entities_v2",
        {
            "project_id": "project-a",
            "entity_id": "entity-a",
            "raw_object_id": "raw-v2:object",
            "reason_code": "tombstoned",
            "blocked_at": "2026-01-01T00:00:00.000000Z",
            "actor_digest": "sha256:actor",
        },
    )
    _insert(database, "raw_migration_runs_v2", _migration_run())
    _insert(
        database,
        "raw_migration_findings_v2",
        {
            "migration_run_id": "migration-a",
            "finding_id": "finding-a",
            "severity": "P1",
            "category": "fixture",
            "source_row_fingerprint": "sha256:row",
            "resolution": "safe_to_copy",
            "detail_digest": "sha256:detail",
        },
    )

    counts = {
        table: database.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in RAW_V2_TABLES
    }
    assert counts == {
        "raw_blobs_v2": 1,
        "raw_objects_v2": 2,
        "source_events_v2": 1,
        "raw_evidence_bindings_v2": 1,
        "blocked_entities_v2": 1,
        "raw_migration_runs_v2": 1,
        "raw_migration_findings_v2": 1,
    }


@pytest.mark.parametrize(
    "values",
    [
        _blob(byte_length=-1),
        _blob(storage_state="unknown"),
        _blob(storage_key=None),
    ],
)
def test_blob_checks_fail_without_rows(values: dict[str, object]) -> None:
    database = _connect()
    initialize_raw_v2_schema(database)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(database, "raw_blobs_v2", values)
    assert database.execute("SELECT count(*) FROM raw_blobs_v2").fetchone()[0] == 0


@pytest.mark.parametrize(
    "updates",
    [
        {"source_domain": "unknown"},
        {"state": "unknown"},
        {"byte_length": -1},
        {"blob_sha256": None},
        {"raw_content_sha256": None},
        {"byte_length": None},
        {
            "state": "reference_only",
            "raw_content_sha256": "sha256:blob",
            "blob_sha256": None,
            "byte_length": None,
        },
        {
            "state": "reference_only",
            "raw_content_sha256": None,
            "blob_sha256": "sha256:blob",
            "byte_length": None,
        },
        {
            "state": "reference_only",
            "raw_content_sha256": None,
            "blob_sha256": None,
            "byte_length": 0,
        },
        {"raw_content_sha256": "sha256:different"},
    ],
)
def test_object_state_checks_fail_without_rows(updates: dict[str, object]) -> None:
    database = _connect()
    initialize_raw_v2_schema(database)
    _insert(database, "raw_blobs_v2", _blob())
    with pytest.raises(sqlite3.IntegrityError):
        _insert(database, "raw_objects_v2", _object(**updates))
    assert database.execute("SELECT count(*) FROM raw_objects_v2").fetchone()[0] == 0


@pytest.mark.parametrize(
    "updates",
    [
        {"status": "unknown"},
        {"raw_content_sha256": None},
        {"status": "quarantined", "raw_content_sha256": None},
        {"status": "reference_only", "raw_content_sha256": "sha256:blob"},
        {"project_id": "project-b"},
        {"raw_object_id": "raw-v2:missing"},
    ],
)
def test_event_checks_and_project_foreign_key_fail_without_rows(
    updates: dict[str, object],
) -> None:
    database = _connect()
    initialize_raw_v2_schema(database)
    _insert(database, "raw_blobs_v2", _blob())
    _insert(database, "raw_objects_v2", _object())
    with pytest.raises(sqlite3.IntegrityError):
        _insert(database, "source_events_v2", _event(**updates))
    assert database.execute("SELECT count(*) FROM source_events_v2").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("table", "values"),
    [
        ("raw_evidence_bindings_v2", _binding(project_id="project-b")),
        ("raw_evidence_bindings_v2", _binding(raw_object_id="raw-v2:missing")),
        (
            "raw_evidence_bindings_v2",
            _binding(
                valid_from="2026-01-02T00:00:00.000000Z",
                valid_to="2026-01-01T00:00:00.000000Z",
            ),
        ),
        (
            "raw_evidence_bindings_v2",
            _binding(
                valid_from="2026-01-01T00:00:00.000000Z",
                valid_to="2026-01-01T00:00:00.000000Z",
            ),
        ),
        (
            "blocked_entities_v2",
            {
                "project_id": "project-b",
                "entity_id": "entity-a",
                "raw_object_id": "raw-v2:object",
                "reason_code": "tombstoned",
                "blocked_at": "2026-01-01T00:00:00.000000Z",
                "actor_digest": "sha256:actor",
            },
        ),
    ],
)
def test_binding_and_blocked_checks_fail_without_rows(
    table: str,
    values: dict[str, object],
) -> None:
    database = _connect()
    initialize_raw_v2_schema(database)
    _insert(database, "raw_blobs_v2", _blob())
    _insert(database, "raw_objects_v2", _object())
    with pytest.raises(sqlite3.IntegrityError):
        _insert(database, table, values)
    assert database.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("table", "values"),
    [
        (
            "raw_migration_runs_v2",
            _migration_run(migration_run_id="migration-invalid-mode", mode="production"),
        ),
        (
            "raw_migration_runs_v2",
            _migration_run(migration_run_id="migration-invalid-status", status="complete"),
        ),
        (
            "raw_migration_findings_v2",
            {
                "migration_run_id": "missing-run",
                "finding_id": "finding-a",
                "severity": "P1",
                "category": "fixture",
                "source_row_fingerprint": "sha256:row",
                "resolution": "safe_to_copy",
                "detail_digest": "sha256:detail",
            },
        ),
        (
            "raw_migration_findings_v2",
            {
                "migration_run_id": "migration-a",
                "finding_id": "finding-a",
                "severity": "P3",
                "category": "fixture",
                "source_row_fingerprint": "sha256:row",
                "resolution": "safe_to_copy",
                "detail_digest": "sha256:detail",
            },
        ),
        (
            "raw_migration_findings_v2",
            {
                "migration_run_id": "migration-a",
                "finding_id": "finding-a",
                "severity": "P1",
                "category": "fixture",
                "source_row_fingerprint": "sha256:row",
                "resolution": "guessed",
                "detail_digest": "sha256:detail",
            },
        ),
    ],
)
def test_migration_ledger_checks_fail_without_rows(
    table: str,
    values: dict[str, object],
) -> None:
    database = _connect()
    initialize_raw_v2_schema(database)
    _insert(database, "raw_migration_runs_v2", _migration_run())
    before = database.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        _insert(database, table, values)
    assert database.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == before


def test_exact_reinitialization_is_write_free_and_preserves_v1() -> None:
    database = _connect()
    database.executescript(
        """
        CREATE TABLE raw_objects (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX idx_raw_objects_project ON raw_objects(project_id);
        INSERT INTO raw_objects VALUES ('v1-object', 'project-a', 'keep-me');
        PRAGMA user_version = 77;
        """
    )
    database.row_factory = sqlite3.Row
    first = initialize_raw_v2_schema(database)
    _insert(database, "raw_blobs_v2", _blob())
    database.commit()

    before_schema = tuple(
        tuple(row)
        for row in database.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
    )
    before_v1 = tuple(tuple(row) for row in database.execute("SELECT * FROM raw_objects"))
    before_v2 = tuple(tuple(row) for row in database.execute("SELECT * FROM raw_blobs_v2"))
    before_changes = database.total_changes
    statements: list[str] = []
    database.set_trace_callback(statements.append)
    second = initialize_raw_v2_schema(database)
    database.set_trace_callback(None)

    assert second == first
    assert inspect_raw_v2_schema(database) == first
    assert tuple(
        tuple(row)
        for row in database.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
    ) == before_schema
    assert tuple(tuple(row) for row in database.execute("SELECT * FROM raw_objects")) == before_v1
    assert tuple(tuple(row) for row in database.execute("SELECT * FROM raw_blobs_v2")) == before_v2
    assert database.execute("PRAGMA user_version").fetchone()[0] == 77
    assert database.total_changes == before_changes
    assert not any(
        statement.lstrip().upper().startswith(("BEGIN", "COMMIT", "CREATE", "DROP", "ALTER"))
        for statement in statements
    )
    assert database.row_factory is sqlite3.Row
    assert database.execute("SELECT 1").fetchone()[0] == 1


def test_unrelated_v1_foreign_key_violation_is_not_claimed_or_repaired() -> None:
    database = sqlite3.connect(":memory:")
    database.executescript(
        """
        PRAGMA foreign_keys = OFF;
        CREATE TABLE v1_parent (id TEXT PRIMARY KEY);
        CREATE TABLE v1_child (
            id TEXT PRIMARY KEY,
            parent_id TEXT NOT NULL REFERENCES v1_parent(id)
        );
        INSERT INTO v1_child VALUES ('child-a', 'missing-parent');
        """
    )
    before = tuple(database.execute("SELECT * FROM v1_child").fetchall())

    snapshot = initialize_raw_v2_schema(database)

    assert snapshot.schema_sha256 == EXPECTED_SCHEMA_SHA256
    assert tuple(database.execute("SELECT * FROM v1_child").fetchall()) == before
    assert database.execute("PRAGMA foreign_key_check(v1_child)").fetchall()
    for table in RAW_V2_TABLES:
        assert not database.execute(f"PRAGMA foreign_key_check({table})").fetchall()


@pytest.mark.parametrize(
    "prepare",
    [
        lambda db: db.execute(
            "CREATE TABLE raw_blobs_v2 (blob_sha256 TEXT PRIMARY KEY)"
        ),
        lambda db: db.execute("CREATE VIEW raw_blobs_v2 AS SELECT 1 AS blob_sha256"),
    ],
)
def test_partial_or_wrong_same_name_schema_fails_without_repair(
    prepare: Callable[[sqlite3.Connection], object],
) -> None:
    database = _connect()
    prepare(database)
    before = tuple(
        tuple(row)
        for row in database.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
    )
    with pytest.raises(RawV2SchemaError):
        initialize_raw_v2_schema(database)
    after = tuple(
        tuple(row)
        for row in database.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        )
    )
    assert after == before


def test_missing_or_extra_authored_index_fails_without_repair() -> None:
    for mutation in ("missing", "extra"):
        database = _connect()
        initialize_raw_v2_schema(database)
        if mutation == "missing":
            database.execute("DROP INDEX idx_raw_v2_lookup")
        else:
            database.execute(
                "CREATE INDEX idx_raw_v2_unreviewed ON raw_objects_v2(project_id)"
            )
        before = tuple(
            tuple(row)
            for row in database.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            )
        )
        with pytest.raises(RawV2SchemaError):
            initialize_raw_v2_schema(database)
        after = tuple(
            tuple(row)
            for row in database.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            )
        )
        assert after == before


def test_mid_ddl_failure_rolls_back_every_v2_object() -> None:
    database = _connect()

    def deny_source_events(
        action: int,
        argument_one: str | None,
        _argument_two: str | None,
        _database_name: str | None,
        _trigger_name: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_CREATE_TABLE and argument_one == "source_events_v2":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    database.set_authorizer(deny_source_events)
    with pytest.raises(sqlite3.DatabaseError):
        initialize_raw_v2_schema(database)
    database.set_authorizer(None)

    names = {
        row[0]
        for row in database.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    }
    assert names.isdisjoint(RAW_V2_TABLES)
    assert names.isdisjoint(RAW_V2_INDEXES)
    assert not database.in_transaction


def test_active_caller_transaction_is_rejected_and_left_owned_by_caller() -> None:
    database = _connect()
    database.execute("CREATE TABLE caller_state (value TEXT NOT NULL)")
    database.execute("INSERT INTO caller_state VALUES ('uncommitted')")
    assert database.in_transaction

    with pytest.raises(RawV2SchemaError):
        initialize_raw_v2_schema(database)

    assert database.in_transaction
    assert database.execute("SELECT value FROM caller_state").fetchone()[0] == "uncommitted"
    assert _v2_authored_objects(database) == ()
    database.rollback()
    assert database.execute("SELECT count(*) FROM caller_state").fetchone()[0] == 0
