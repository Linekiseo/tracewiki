"""Isolated, exact SQLite schema initialization for raw V2 authority.

The module accepts a caller-owned connection and is intentionally not wired into the
application-wide initializer.  It creates structure only: no database path, row,
runtime default, migration, or release behavior is owned here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .contracts import canonical_json_bytes, exact_bytes_sha256

RAW_V2_SCHEMA_VERSION = "raw-v2-sqlite-schema-v1"

_RAW_V2_TABLE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "raw_blobs_v2",
        r"""
        CREATE TABLE raw_blobs_v2 (
          blob_sha256 TEXT PRIMARY KEY,
          byte_length INTEGER NOT NULL CHECK(byte_length >= 0),
          storage_key TEXT,
          storage_state TEXT NOT NULL
            CHECK(storage_state IN ('available','quarantined','missing','corrupt')),
          first_verified_at TEXT,
          last_verified_at TEXT,
          created_at TEXT NOT NULL,
          CHECK(
            (storage_state='available' AND storage_key IS NOT NULL)
            OR storage_state!='available'
          )
        )
        """,
    ),
    (
        "raw_objects_v2",
        r"""
        CREATE TABLE raw_objects_v2 (
          raw_object_id TEXT PRIMARY KEY,
          logical_identity_sha256 TEXT NOT NULL UNIQUE,
          project_id TEXT NOT NULL,
          source_domain TEXT NOT NULL
            CHECK(source_domain IN ('code','codex','experiment','notebook','document','workspace')),
          source_type TEXT NOT NULL,
          source_instance_id TEXT NOT NULL,
          source_object_id TEXT NOT NULL,
          source_version TEXT NOT NULL,
          stable_version TEXT NOT NULL,
          acl_ref TEXT NOT NULL,
          visibility_partition_sha256 TEXT NOT NULL,
          raw_content_sha256 TEXT,
          blob_sha256 TEXT,
          media_type TEXT NOT NULL,
          byte_length INTEGER CHECK(byte_length >= 0),
          state TEXT NOT NULL
            CHECK(state IN ('active','reference_only','quarantined','tombstoned','corrupt')),
          adapter_version TEXT NOT NULL,
          schema_version TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          tombstoned_at TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(project_id, raw_object_id),
          FOREIGN KEY(blob_sha256) REFERENCES raw_blobs_v2(blob_sha256),
          CHECK(
            (state='active' AND blob_sha256 IS NOT NULL
                            AND raw_content_sha256 IS NOT NULL AND byte_length IS NOT NULL)
            OR state!='active'
          ),
          CHECK(
            (state='reference_only' AND blob_sha256 IS NULL
                                    AND raw_content_sha256 IS NULL AND byte_length IS NULL)
            OR state!='reference_only'
          ),
          CHECK(
            blob_sha256 IS NULL OR raw_content_sha256 IS NULL
            OR blob_sha256=raw_content_sha256
          )
        )
        """,
    ),
    (
        "source_events_v2",
        r"""
        CREATE TABLE source_events_v2 (
          event_id TEXT PRIMARY KEY,
          idempotency_sha256 TEXT NOT NULL UNIQUE,
          project_id TEXT NOT NULL,
          source_domain TEXT NOT NULL,
          source_type TEXT NOT NULL,
          source_instance_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          mutation_id TEXT NOT NULL,
          source_object_id TEXT NOT NULL,
          source_version TEXT NOT NULL,
          raw_object_id TEXT,
          raw_content_sha256 TEXT,
          acl_ref TEXT NOT NULL,
          visibility_partition_sha256 TEXT NOT NULL,
          event_time TEXT,
          observed_at TEXT NOT NULL,
          trace_id TEXT NOT NULL,
          schema_version TEXT NOT NULL,
          status TEXT NOT NULL
            CHECK(status IN ('persisted','reference_only','quarantined','rejected','tombstone')),
          metadata_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          CHECK(
            (status IN ('persisted','quarantined') AND raw_content_sha256 IS NOT NULL)
            OR (status='reference_only' AND raw_content_sha256 IS NULL)
            OR status IN ('rejected','tombstone')
          ),
          FOREIGN KEY(project_id, raw_object_id)
            REFERENCES raw_objects_v2(project_id, raw_object_id)
        )
        """,
    ),
    (
        "raw_evidence_bindings_v2",
        r"""
        CREATE TABLE raw_evidence_bindings_v2 (
          locator_id TEXT PRIMARY KEY,
          binding_sha256 TEXT NOT NULL UNIQUE,
          project_id TEXT NOT NULL,
          source_domain TEXT NOT NULL,
          raw_object_id TEXT NOT NULL,
          source_version TEXT NOT NULL,
          stable_version TEXT NOT NULL,
          generation_id TEXT NOT NULL,
          derived_entity_id TEXT NOT NULL,
          retrieval_unit_id TEXT NOT NULL,
          derivation_kind TEXT NOT NULL,
          derivation_version TEXT NOT NULL,
          selector_kind TEXT NOT NULL,
          selector_json TEXT NOT NULL,
          selector_sha256 TEXT NOT NULL,
          raw_content_sha256 TEXT NOT NULL,
          selected_content_sha256 TEXT NOT NULL,
          parser_artifact_sha256 TEXT,
          acl_ref TEXT NOT NULL,
          visibility_partition_sha256 TEXT NOT NULL,
          valid_from TEXT,
          valid_to TEXT,
          observed_at TEXT NOT NULL,
          binding_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          invalidated_at TEXT,
          invalidation_reason TEXT,
          FOREIGN KEY(project_id, raw_object_id)
            REFERENCES raw_objects_v2(project_id, raw_object_id),
          CHECK(valid_from IS NULL OR valid_to IS NULL OR valid_from < valid_to)
        )
        """,
    ),
    (
        "blocked_entities_v2",
        r"""
        CREATE TABLE blocked_entities_v2 (
          project_id TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          raw_object_id TEXT NOT NULL,
          reason_code TEXT NOT NULL,
          blocked_at TEXT NOT NULL,
          actor_digest TEXT NOT NULL,
          PRIMARY KEY(project_id, entity_id),
          FOREIGN KEY(project_id, raw_object_id)
            REFERENCES raw_objects_v2(project_id, raw_object_id)
        )
        """,
    ),
    (
        "raw_migration_runs_v2",
        r"""
        CREATE TABLE raw_migration_runs_v2 (
          migration_run_id TEXT PRIMARY KEY,
          mode TEXT NOT NULL CHECK(mode IN ('fixture','mirror','authorized-production')),
          source_revision_sha256 TEXT NOT NULL,
          source_schema_sha256 TEXT NOT NULL,
          plan_sha256 TEXT NOT NULL,
          status TEXT NOT NULL
            CHECK(status IN ('planned','scanning','classified','copied','verified',
                             'shadow_ready','failed','rolled_back')),
          checkpoint_json TEXT NOT NULL,
          counters_json TEXT NOT NULL,
          findings_sha256 TEXT,
          report_sha256 TEXT,
          started_at TEXT NOT NULL,
          completed_at TEXT
        )
        """,
    ),
    (
        "raw_migration_findings_v2",
        r"""
        CREATE TABLE raw_migration_findings_v2 (
          migration_run_id TEXT NOT NULL,
          finding_id TEXT NOT NULL,
          severity TEXT NOT NULL CHECK(severity IN ('P0','P1','P2')),
          category TEXT NOT NULL,
          source_row_fingerprint TEXT NOT NULL,
          resolution TEXT NOT NULL
            CHECK(resolution IN ('reingest_required','owner_mapping_required',
                                 'repair_required','safe_to_copy','excluded_by_policy')),
          detail_digest TEXT NOT NULL,
          PRIMARY KEY(migration_run_id, finding_id),
          FOREIGN KEY(migration_run_id) REFERENCES raw_migration_runs_v2(migration_run_id)
        )
        """,
    ),
)

_RAW_V2_INDEX_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    (
        "idx_raw_v2_lookup",
        "raw_objects_v2",
        r"""
        CREATE INDEX idx_raw_v2_lookup
          ON raw_objects_v2(project_id, source_domain, source_instance_id,
                            source_object_id, source_version, state)
        """,
    ),
    (
        "idx_raw_v2_visibility",
        "raw_objects_v2",
        r"""
        CREATE INDEX idx_raw_v2_visibility
          ON raw_objects_v2(project_id, visibility_partition_sha256, state, observed_at)
        """,
    ),
    (
        "idx_events_v2_project_time",
        "source_events_v2",
        r"""
        CREATE INDEX idx_events_v2_project_time
          ON source_events_v2(project_id, observed_at, event_id)
        """,
    ),
    (
        "idx_events_v2_object",
        "source_events_v2",
        r"""
        CREATE INDEX idx_events_v2_object
          ON source_events_v2(project_id, source_domain, source_instance_id,
                              source_object_id, source_version)
        """,
    ),
    (
        "idx_bindings_v2_entity",
        "raw_evidence_bindings_v2",
        r"""
        CREATE INDEX idx_bindings_v2_entity
          ON raw_evidence_bindings_v2(project_id, derived_entity_id,
                                      generation_id, invalidated_at)
        """,
    ),
    (
        "idx_bindings_v2_unit",
        "raw_evidence_bindings_v2",
        r"""
        CREATE INDEX idx_bindings_v2_unit
          ON raw_evidence_bindings_v2(project_id, retrieval_unit_id,
                                      generation_id, invalidated_at)
        """,
    ),
    (
        "idx_bindings_v2_raw",
        "raw_evidence_bindings_v2",
        r"""
        CREATE INDEX idx_bindings_v2_raw
          ON raw_evidence_bindings_v2(project_id, raw_object_id, invalidated_at)
        """,
    ),
)

RAW_V2_TABLES = tuple(name for name, _sql in _RAW_V2_TABLE_DEFINITIONS)
RAW_V2_INDEXES = tuple(name for name, _table, _sql in _RAW_V2_INDEX_DEFINITIONS)
RAW_V2_SCHEMA_STATEMENTS = tuple(
    sql for _name, sql in _RAW_V2_TABLE_DEFINITIONS
) + tuple(sql for _name, _table, sql in _RAW_V2_INDEX_DEFINITIONS)


class RawV2SchemaError(RuntimeError):
    """The caller-owned database does not satisfy the exact raw V2 schema contract."""


@dataclass(frozen=True, slots=True)
class RawV2SchemaSnapshot:
    schema_version: str
    schema_sha256: str
    table_names: tuple[str, ...]
    index_names: tuple[str, ...]


def _validate_connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be an open sqlite3.Connection")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _normalize_sql(value: str) -> str:
    normalized = " ".join(value.replace("\r\n", "\n").replace("\r", "\n").split())
    return normalized.removesuffix(";").rstrip()


def _expected_authored_objects() -> list[dict[str, str]]:
    objects = [
        {
            "type": "table",
            "name": name,
            "table_name": name,
            "sql": _normalize_sql(sql),
        }
        for name, sql in _RAW_V2_TABLE_DEFINITIONS
    ]
    objects.extend(
        {
            "type": "index",
            "name": name,
            "table_name": table,
            "sql": _normalize_sql(sql),
        }
        for name, table, sql in _RAW_V2_INDEX_DEFINITIONS
    )
    return sorted(objects, key=lambda item: (item["type"], item["name"]))


def _scoped_master_rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    names = (*RAW_V2_TABLES, *RAW_V2_INDEXES)
    name_placeholders = ",".join("?" for _ in names)
    table_placeholders = ",".join("?" for _ in RAW_V2_TABLES)
    rows = connection.execute(
        f"""SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name IN ({name_placeholders})
               OR tbl_name IN ({table_placeholders})
            ORDER BY type, name""",
        (*names, *RAW_V2_TABLES),
    ).fetchall()
    return [tuple(row) for row in rows]


def _actual_authored_objects(connection: sqlite3.Connection) -> list[dict[str, str]]:
    objects: list[dict[str, str]] = []
    for object_type, name, table_name, sql in _scoped_master_rows(connection):
        if object_type == "index" and sql is None:
            continue
        if not all(type(item) is str for item in (object_type, name, table_name, sql)):
            raise RawV2SchemaError("raw V2 sqlite_master contains an invalid scoped object")
        objects.append(
            {
                "type": object_type,
                "name": name,
                "table_name": table_name,
                "sql": _normalize_sql(sql),
            }
        )
    return sorted(objects, key=lambda item: (item["type"], item["name"]))


def _has_scoped_objects(connection: sqlite3.Connection) -> bool:
    return bool(_scoped_master_rows(connection))


def _column_projection(connection: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    return [
        {
            "position": int(row[0]),
            "name": str(row[1]),
            "declared_type": str(row[2]),
            "not_null": bool(row[3]),
            "default": row[4],
            "primary_key_position": int(row[5]),
        }
        for row in rows
    ]


def _foreign_key_projection(
    connection: sqlite3.Connection,
    table: str,
) -> list[dict[str, object]]:
    rows = connection.execute(f"PRAGMA foreign_key_list({_quote_identifier(table)})").fetchall()
    return [
        {
            "id": int(row[0]),
            "position": int(row[1]),
            "parent_table": str(row[2]),
            "from_column": str(row[3]),
            "to_column": str(row[4]),
            "on_update": str(row[5]),
            "on_delete": str(row[6]),
            "match": str(row[7]),
        }
        for row in sorted(rows, key=lambda item: (int(item[0]), int(item[1])))
    ]


def _index_projection(connection: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    for row in connection.execute(f"PRAGMA index_list({_quote_identifier(table)})").fetchall():
        name = str(row[1])
        origin = str(row[3])
        columns = [
            str(item[2])
            for item in connection.execute(
                f"PRAGMA index_info({_quote_identifier(name)})"
            ).fetchall()
        ]
        projected.append(
            {
                "name": name if origin == "c" else None,
                "unique": bool(row[2]),
                "origin": origin,
                "partial": bool(row[4]),
                "columns": columns,
            }
        )
    return sorted(
        projected,
        key=lambda item: (
            str(item["origin"]),
            "" if item["name"] is None else str(item["name"]),
            tuple(str(column) for column in item["columns"]),
        ),
    )


def _schema_projection(connection: sqlite3.Connection) -> dict[str, object]:
    return {
        "schema_version": RAW_V2_SCHEMA_VERSION,
        "authored_objects": _actual_authored_objects(connection),
        "tables": [
            {
                "name": table,
                "columns": _column_projection(connection, table),
                "foreign_keys": _foreign_key_projection(connection, table),
                "indexes": _index_projection(connection, table),
            }
            for table in RAW_V2_TABLES
        ],
    }


def _assert_exact_schema(connection: sqlite3.Connection) -> None:
    if _actual_authored_objects(connection) != _expected_authored_objects():
        raise RawV2SchemaError("raw V2 schema is partial, drifted, or has unreviewed objects")
    for table in RAW_V2_TABLES:
        violations = connection.execute(
            f"PRAGMA foreign_key_check({_quote_identifier(table)})"
        ).fetchall()
        if violations:
            raise RawV2SchemaError("raw V2 schema contains foreign-key violations")


def inspect_raw_v2_schema(connection: sqlite3.Connection) -> RawV2SchemaSnapshot:
    """Inspect and require one exact raw V2 schema without changing the connection."""

    _validate_connection(connection)
    _assert_exact_schema(connection)
    projection = _schema_projection(connection)
    digest = exact_bytes_sha256(canonical_json_bytes(projection, allow_none=True))
    return RawV2SchemaSnapshot(
        schema_version=RAW_V2_SCHEMA_VERSION,
        schema_sha256=digest,
        table_names=RAW_V2_TABLES,
        index_names=RAW_V2_INDEXES,
    )


def initialize_raw_v2_schema(connection: sqlite3.Connection) -> RawV2SchemaSnapshot:
    """Create an empty exact schema or verify an exact, write-free re-initialization."""

    _validate_connection(connection)
    if connection.in_transaction:
        raise RawV2SchemaError("raw V2 initialization requires no active caller transaction")
    connection.execute("PRAGMA foreign_keys = ON")
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys is None or int(foreign_keys[0]) != 1:
        raise RawV2SchemaError("SQLite foreign-key enforcement is unavailable")
    if _has_scoped_objects(connection):
        return inspect_raw_v2_schema(connection)

    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in RAW_V2_SCHEMA_STATEMENTS:
            connection.execute(statement)
        snapshot = inspect_raw_v2_schema(connection)
        connection.commit()
        return snapshot
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise


__all__ = [
    "RAW_V2_INDEXES",
    "RAW_V2_SCHEMA_STATEMENTS",
    "RAW_V2_SCHEMA_VERSION",
    "RAW_V2_TABLES",
    "RawV2SchemaError",
    "RawV2SchemaSnapshot",
    "initialize_raw_v2_schema",
    "inspect_raw_v2_schema",
]
