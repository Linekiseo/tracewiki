"""Isolated additive Experiment V2 store.

This module owns only ``experiment_v2_*`` tables.  It never migrates or opens
the application's default database, and publishing is generation-atomic.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .contracts_v2 import (
    ExperimentRunGroupV2,
    ExperimentRunSnapshotV2,
    canonical_json_bytes_v2,
    canonical_sha256_v2,
)

EXPERIMENT_STORE_SCHEMA_VERSION = "experiment-v2-schema-1"
EXPERIMENT_STORE_OWNER = "experiment-v2-isolated-store"

_FORMAL_DB_NAME = "evidence-rag.sqlite3"
_SIDECAR_SUFFIXES = ("-shm", "-wal")


class ExperimentStoreV2Error(RuntimeError):
    """Raised when isolated storage or publication invariants fail."""


@dataclass(frozen=True, slots=True)
class ExperimentGenerationV2:
    generation_id: str
    project_id: str
    source_id: str
    acl_ref: str
    watermark: str
    status: Literal["preparing", "published", "failed", "rolled_back"]
    expected_snapshots: int
    actual_snapshots: int
    content_sha256: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ExperimentPublishReceiptV2:
    generation_id: str
    content_sha256: str
    snapshots: int
    definitions: int
    observations: int
    artifacts: int
    groups: int
    units: int
    previous_generation_id: str | None
    idempotent: bool


_DDL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS experiment_v2_schema_versions (
    version TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;
CREATE TABLE IF NOT EXISTS experiment_v2_generations (
    generation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    watermark TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('preparing','published','failed','rolled_back')
    ),
    expected_snapshots INTEGER NOT NULL CHECK (expected_snapshots >= 0),
    actual_snapshots INTEGER NOT NULL DEFAULT 0 CHECK (actual_snapshots >= 0),
    content_sha256 TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS experiment_v2_one_active_generation
ON experiment_v2_generations(project_id, source_id)
WHERE status = 'published';
CREATE TABLE IF NOT EXISTS experiment_v2_run_snapshots (
    run_snapshot_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    source_run_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0,1)),
    FOREIGN KEY(generation_id)
        REFERENCES experiment_v2_generations(generation_id)
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS experiment_v2_snapshot_source_version
ON experiment_v2_run_snapshots(
    project_id, source_id, run_id, source_run_sha256, generation_id
);
CREATE INDEX IF NOT EXISTS experiment_v2_active_runs
ON experiment_v2_run_snapshots(project_id, source_id, experiment_id, run_id)
WHERE active = 1;
CREATE TABLE IF NOT EXISTS experiment_v2_metric_definitions (
    definition_id TEXT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS experiment_v2_metric_aliases (
    definition_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    PRIMARY KEY(definition_id, alias),
    FOREIGN KEY(definition_id)
        REFERENCES experiment_v2_metric_definitions(definition_id)
) STRICT;
CREATE TABLE IF NOT EXISTS experiment_v2_metric_observations (
    observation_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    run_snapshot_id TEXT NOT NULL,
    series_id TEXT NOT NULL,
    definition_id TEXT NOT NULL,
    numeric_value REAL,
    canonical_unit TEXT,
    split TEXT,
    step INTEGER,
    observed_at TEXT,
    valid INTEGER NOT NULL CHECK (valid IN (0,1)),
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0,1)),
    FOREIGN KEY(generation_id)
        REFERENCES experiment_v2_generations(generation_id),
    FOREIGN KEY(run_snapshot_id)
        REFERENCES experiment_v2_run_snapshots(run_snapshot_id),
    FOREIGN KEY(definition_id)
        REFERENCES experiment_v2_metric_definitions(definition_id)
) STRICT;
CREATE INDEX IF NOT EXISTS experiment_v2_active_observations
ON experiment_v2_metric_observations(definition_id, canonical_unit, split, numeric_value)
WHERE active = 1 AND valid = 1;
CREATE TABLE IF NOT EXISTS experiment_v2_condition_snapshots (
    condition_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('dataset','config','environment')),
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS experiment_v2_artifact_versions (
    artifact_version_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    run_snapshot_id TEXT NOT NULL,
    verification_state TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0,1)),
    FOREIGN KEY(generation_id)
        REFERENCES experiment_v2_generations(generation_id),
    FOREIGN KEY(run_snapshot_id)
        REFERENCES experiment_v2_run_snapshots(run_snapshot_id)
) STRICT;
CREATE TABLE IF NOT EXISTS experiment_v2_run_groups (
    run_group_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS experiment_v2_run_group_members (
    run_group_id TEXT NOT NULL,
    run_snapshot_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    included INTEGER NOT NULL CHECK (included IN (0,1)),
    exclusion_reason TEXT,
    seed INTEGER,
    fold INTEGER,
    repeat INTEGER,
    PRIMARY KEY(run_group_id, run_snapshot_id),
    FOREIGN KEY(run_group_id) REFERENCES experiment_v2_run_groups(run_group_id),
    FOREIGN KEY(run_snapshot_id)
        REFERENCES experiment_v2_run_snapshots(run_snapshot_id)
) STRICT;
CREATE TABLE IF NOT EXISTS experiment_v2_retrieval_units (
    unit_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    run_snapshot_id TEXT NOT NULL,
    unit_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0,1)),
    FOREIGN KEY(generation_id)
        REFERENCES experiment_v2_generations(generation_id),
    FOREIGN KEY(run_snapshot_id)
        REFERENCES experiment_v2_run_snapshots(run_snapshot_id)
) STRICT;
CREATE INDEX IF NOT EXISTS experiment_v2_active_units
ON experiment_v2_retrieval_units(project_id, source_id, unit_type)
WHERE active = 1;
CREATE TABLE IF NOT EXISTS experiment_v2_backfill_cursors (
    cursor_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    after_run_id TEXT,
    rows_examined INTEGER NOT NULL,
    rows_created INTEGER NOT NULL,
    orphan_count INTEGER NOT NULL,
    parity_mismatch_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    content_sha256 TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS experiment_v2_release_records (
    release_record_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    decision TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL
) STRICT;
"""


def _canonical_json(value: object) -> str:
    return canonical_json_bytes_v2(value).decode("utf-8")


def _require_isolated_path(path: Path, root: Path) -> tuple[Path, Path]:
    if not path.is_absolute() or not root.is_absolute():
        raise ExperimentStoreV2Error("database and isolated root must be absolute")
    if path.name == _FORMAL_DB_NAME or path.name.endswith(_SIDECAR_SUFFIXES):
        raise ExperimentStoreV2Error("formal database or sidecar is forbidden")
    if path.exists() or any(Path(str(path) + suffix).exists() for suffix in _SIDECAR_SUFFIXES):
        raise ExperimentStoreV2Error("database path must be new and sidecar-free")
    if root.is_symlink() or any(parent.is_symlink() for parent in [root, *root.parents]):
        raise ExperimentStoreV2Error("isolated root cannot contain symlinks")
    root_resolved = root.resolve(strict=True)
    if not root_resolved.is_dir():
        raise ExperimentStoreV2Error("isolated root must be a directory")
    parent = path.parent.resolve(strict=True)
    if not parent.is_relative_to(root_resolved):
        raise ExperimentStoreV2Error("database must remain inside the isolated root")
    return path, root_resolved


class ExperimentStoreV2:
    """Additive store restricted to a caller-created isolated root."""

    def __init__(self, database: Path, *, isolated_root: Path) -> None:
        self.database, self.isolated_root = _require_isolated_path(
            database,
            isolated_root,
        )
        # The process-local derived index is owned by the runtime registry and
        # source operations are serialized there.  Disabling SQLite's creator
        # thread check lets the ASGI worker that materializes the facade differ
        # from a later worker (or the lifespan thread that closes it) without
        # weakening transaction serialization.
        self._connection = sqlite3.connect(self.database, check_same_thread=False)
        self._connection_lock = threading.RLock()
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._closed = False

    def initialize(self) -> None:
        with self._connection_lock:
            self._connection.executescript(_DDL)
            self._connection.execute(
                """
                INSERT OR IGNORE INTO experiment_v2_schema_versions(version, owner)
                VALUES (?, ?)
                """,
                (EXPERIMENT_STORE_SCHEMA_VERSION, EXPERIMENT_STORE_OWNER),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._connection_lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def __enter__(self) -> ExperimentStoreV2:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def prepare_generation(
        self,
        *,
        project_id: str,
        source_id: str,
        acl_ref: str,
        watermark: str,
        expected_snapshots: int,
    ) -> ExperimentGenerationV2:
        if expected_snapshots < 0:
            raise ExperimentStoreV2Error("expected snapshot count cannot be negative")
        seed = {
            "project_id": project_id,
            "source_id": source_id,
            "acl_ref": acl_ref,
            "watermark": watermark,
            "expected_snapshots": expected_snapshots,
            "schema_version": EXPERIMENT_STORE_SCHEMA_VERSION,
        }
        generation_id = (
            "experimentgeneration-" + hashlib.sha256(canonical_json_bytes_v2(seed)).hexdigest()
        )
        existing = self._connection.execute(
            "SELECT * FROM experiment_v2_generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        if existing is not None:
            if existing["status"] != "preparing":
                raise ExperimentStoreV2Error("generation identity is already terminal")
            return self._generation(existing)
        self._connection.execute(
            """
            INSERT INTO experiment_v2_generations(
                generation_id, project_id, source_id, acl_ref, watermark,
                status, expected_snapshots
            ) VALUES (?, ?, ?, ?, ?, 'preparing', ?)
            """,
            (
                generation_id,
                project_id,
                source_id,
                acl_ref,
                watermark,
                expected_snapshots,
            ),
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT * FROM experiment_v2_generations WHERE generation_id = ?",
            (generation_id,),
        ).fetchone()
        assert row is not None
        return self._generation(row)

    def publish(
        self,
        generation: ExperimentGenerationV2,
        snapshots: Iterable[ExperimentRunSnapshotV2],
        *,
        groups: Iterable[ExperimentRunGroupV2] = (),
        retrieval_units: Iterable[dict[str, Any]] = (),
    ) -> ExperimentPublishReceiptV2:
        snapshot_items = tuple(snapshots)
        group_items = tuple(groups)
        unit_items = tuple(retrieval_units)
        if len(snapshot_items) != generation.expected_snapshots:
            raise ExperimentStoreV2Error("generation snapshot count mismatch")
        if any(
            (
                item.generation_id != generation.generation_id
                or item.project_id != generation.project_id
                or item.source_id != generation.source_id
                or item.acl_ref != generation.acl_ref
            )
            for item in snapshot_items
        ):
            raise ExperimentStoreV2Error("snapshot scope does not match generation")
        if len({item.run_snapshot_id for item in snapshot_items}) != len(snapshot_items):
            raise ExperimentStoreV2Error("generation contains duplicate snapshots")
        snapshot_ids = {item.run_snapshot_id for item in snapshot_items}
        if any(
            member.run_snapshot_id not in snapshot_ids
            for group in group_items
            for member in group.members
        ):
            raise ExperimentStoreV2Error("run group references an unpublished snapshot")
        for unit in unit_items:
            required = {
                "unit_id",
                "run_snapshot_id",
                "unit_type",
                "content_sha256",
                "payload",
            }
            if set(unit) != required:
                raise ExperimentStoreV2Error("retrieval unit shape is not canonical")
            if unit["run_snapshot_id"] not in snapshot_ids:
                raise ExperimentStoreV2Error("retrieval unit is orphaned")
            payload = dict(unit["payload"])
            embedded_unit_id = payload.pop("unit_id", None)
            embedded_digest = payload.pop("content_sha256", None)
            valid_embedded = (
                embedded_unit_id == unit["unit_id"]
                and embedded_digest == unit["content_sha256"]
                and canonical_sha256_v2(payload) == unit["content_sha256"]
            )
            valid_generic = (
                embedded_unit_id is None
                and embedded_digest is None
                and canonical_sha256_v2(unit["payload"]) == unit["content_sha256"]
            )
            if not (valid_embedded or valid_generic):
                raise ExperimentStoreV2Error("retrieval unit content digest mismatch")

        generation_payload = {
            "generation": generation.generation_id,
            "snapshots": [
                {"id": item.run_snapshot_id, "digest": item.content_sha256}
                for item in sorted(snapshot_items, key=lambda item: item.run_snapshot_id)
            ],
            "groups": [
                {"id": item.run_group_id, "digest": item.content_sha256}
                for item in sorted(group_items, key=lambda item: item.run_group_id)
            ],
            "units": [
                {"id": str(item["unit_id"]), "digest": str(item["content_sha256"])}
                for item in sorted(unit_items, key=lambda item: str(item["unit_id"]))
            ],
        }
        generation_digest = canonical_sha256_v2(generation_payload)
        row = self._connection.execute(
            "SELECT * FROM experiment_v2_generations WHERE generation_id = ?",
            (generation.generation_id,),
        ).fetchone()
        if row is None:
            raise ExperimentStoreV2Error("generation was not prepared by this store")
        if row["status"] == "published":
            if row["content_sha256"] != generation_digest:
                raise ExperimentStoreV2Error("published generation content drift")
            return self._receipt(
                generation.generation_id,
                generation_digest,
                previous_generation_id=None,
                idempotent=True,
            )
        if row["status"] != "preparing":
            raise ExperimentStoreV2Error("generation is not publishable")

        previous = self._connection.execute(
            """
            SELECT generation_id FROM experiment_v2_generations
            WHERE project_id = ? AND source_id = ? AND status = 'published'
            """,
            (generation.project_id, generation.source_id),
        ).fetchone()
        previous_id = str(previous["generation_id"]) if previous else None
        try:
            with self._connection:
                for snapshot in snapshot_items:
                    self._insert_snapshot(snapshot)
                for group in group_items:
                    self._insert_group(group)
                for unit in unit_items:
                    self._insert_unit(generation, unit)
                if previous_id:
                    for table in (
                        "experiment_v2_run_snapshots",
                        "experiment_v2_metric_observations",
                        "experiment_v2_artifact_versions",
                        "experiment_v2_retrieval_units",
                    ):
                        self._connection.execute(
                            f"UPDATE {table} SET active = 0 WHERE generation_id = ?",
                            (previous_id,),
                        )
                    self._connection.execute(
                        """
                        UPDATE experiment_v2_generations
                        SET status = 'rolled_back'
                        WHERE generation_id = ?
                        """,
                        (previous_id,),
                    )
                for table in (
                    "experiment_v2_run_snapshots",
                    "experiment_v2_metric_observations",
                    "experiment_v2_artifact_versions",
                    "experiment_v2_retrieval_units",
                ):
                    self._connection.execute(
                        f"UPDATE {table} SET active = 1 WHERE generation_id = ?",
                        (generation.generation_id,),
                    )
                self._connection.execute(
                    """
                    UPDATE experiment_v2_generations
                    SET status = 'published', actual_snapshots = ?,
                        content_sha256 = ?, published_at = CURRENT_TIMESTAMP
                    WHERE generation_id = ? AND status = 'preparing'
                    """,
                    (
                        len(snapshot_items),
                        generation_digest,
                        generation.generation_id,
                    ),
                )
        except (sqlite3.Error, ValueError, TypeError) as exc:
            self.fail_generation(generation.generation_id, "publish_transaction_failed")
            raise ExperimentStoreV2Error("generation publication failed") from exc
        return self._receipt(
            generation.generation_id,
            generation_digest,
            previous_generation_id=previous_id,
            idempotent=False,
        )

    def fail_generation(self, generation_id: str, error_code: str) -> None:
        if not error_code or any(character.isspace() for character in error_code):
            raise ExperimentStoreV2Error("error code is not safe")
        with self._connection:
            self._connection.execute(
                """
                UPDATE experiment_v2_generations
                SET status = 'failed', error_code = ?
                WHERE generation_id = ? AND status = 'preparing'
                """,
                (error_code, generation_id),
            )

    def rollback_to(
        self,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
    ) -> None:
        target = self._connection.execute(
            """
            SELECT generation_id FROM experiment_v2_generations
            WHERE generation_id = ? AND project_id = ? AND source_id = ?
              AND status IN ('published','rolled_back')
            """,
            (generation_id, project_id, source_id),
        ).fetchone()
        if target is None:
            raise ExperimentStoreV2Error("rollback target is unavailable")
        with self._connection:
            current = self._connection.execute(
                """
                SELECT generation_id FROM experiment_v2_generations
                WHERE project_id = ? AND source_id = ? AND status = 'published'
                """,
                (project_id, source_id),
            ).fetchone()
            if current and current["generation_id"] != generation_id:
                self._set_generation_active(str(current["generation_id"]), active=False)
                self._connection.execute(
                    "UPDATE experiment_v2_generations SET status='rolled_back' WHERE generation_id=?",
                    (current["generation_id"],),
                )
            self._set_generation_active(generation_id, active=True)
            self._connection.execute(
                """
                UPDATE experiment_v2_generations
                SET status='published', error_code=NULL
                WHERE generation_id=?
                """,
                (generation_id,),
            )

    def active_snapshots(
        self,
        *,
        project_id: str,
        source_id: str,
        acl_ref: str,
    ) -> tuple[ExperimentRunSnapshotV2, ...]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM experiment_v2_run_snapshots
            WHERE project_id=? AND source_id=? AND acl_ref=? AND active=1
            ORDER BY run_snapshot_id
            """,
            (project_id, source_id, acl_ref),
        ).fetchall()
        return tuple(
            ExperimentRunSnapshotV2.model_validate_json(row["payload_json"]) for row in rows
        )

    def active_units(
        self,
        *,
        project_id: str,
        source_id: str,
        acl_ref: str,
    ) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute(
            """
            SELECT unit_id, unit_type, content_sha256, payload_json
            FROM experiment_v2_retrieval_units
            WHERE project_id=? AND source_id=? AND acl_ref=? AND active=1
            ORDER BY unit_id
            """,
            (project_id, source_id, acl_ref),
        ).fetchall()
        return tuple(
            {
                "unit_id": row["unit_id"],
                "unit_type": row["unit_type"],
                "content_sha256": row["content_sha256"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        )

    def record_backfill_cursor(
        self,
        *,
        project_id: str,
        source_id: str,
        source_version: str,
        after_run_id: str | None,
        rows_examined: int,
        rows_created: int,
        orphan_count: int,
        parity_mismatch_count: int,
        status: str,
    ) -> str:
        if min(rows_examined, rows_created, orphan_count, parity_mismatch_count) < 0:
            raise ExperimentStoreV2Error("backfill counters cannot be negative")
        payload = {
            "project_id": project_id,
            "source_id": source_id,
            "source_version": source_version,
            "after_run_id": after_run_id,
            "rows_examined": rows_examined,
            "rows_created": rows_created,
            "orphan_count": orphan_count,
            "parity_mismatch_count": parity_mismatch_count,
            "status": status,
        }
        digest = canonical_sha256_v2(payload)
        cursor_id = "experimentcursor-" + digest.removeprefix("sha256:")
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO experiment_v2_backfill_cursors
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cursor_id,
                    project_id,
                    source_id,
                    source_version,
                    after_run_id,
                    rows_examined,
                    rows_created,
                    orphan_count,
                    parity_mismatch_count,
                    status,
                    digest,
                ),
            )
        return cursor_id

    def record_release(self, payload: dict[str, Any]) -> str:
        required = {"project_id", "stage", "decision"}
        if not required.issubset(payload):
            raise ExperimentStoreV2Error("release record is incomplete")
        digest = canonical_sha256_v2(payload)
        record_id = "experimentrelease-" + digest.removeprefix("sha256:")
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO experiment_v2_release_records
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    payload["project_id"],
                    payload["stage"],
                    payload["decision"],
                    digest,
                    _canonical_json(payload),
                ),
            )
        return record_id

    def counts(self) -> dict[str, int]:
        names = (
            "generations",
            "run_snapshots",
            "metric_definitions",
            "metric_observations",
            "condition_snapshots",
            "artifact_versions",
            "run_groups",
            "run_group_members",
            "retrieval_units",
            "backfill_cursors",
            "release_records",
        )
        return {
            name: int(
                self._connection.execute(f"SELECT COUNT(*) FROM experiment_v2_{name}").fetchone()[0]
            )
            for name in names
        }

    def _insert_snapshot(self, snapshot: ExperimentRunSnapshotV2) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO experiment_v2_run_snapshots
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                snapshot.run_snapshot_id,
                snapshot.generation_id,
                snapshot.project_id,
                snapshot.source_id,
                snapshot.acl_ref,
                snapshot.experiment_id,
                snapshot.run_id,
                snapshot.status,
                snapshot.source_run_sha256,
                snapshot.content_sha256,
                snapshot.model_dump_json(),
            ),
        )
        for definition in snapshot.definitions:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO experiment_v2_metric_definitions
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    definition.definition_id,
                    definition.content_sha256,
                    definition.canonical_name,
                    definition.direction.value,
                    definition.status.value,
                    definition.model_dump_json(),
                ),
            )
            for alias in definition.aliases:
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO experiment_v2_metric_aliases
                    VALUES (?, ?)
                    """,
                    (definition.definition_id, alias),
                )
        for condition_id, kind, content_sha256, payload in (
            (
                snapshot.dataset.dataset_version_id,
                "dataset",
                snapshot.dataset.content_sha256,
                snapshot.dataset.model_dump_json(),
            ),
            (
                snapshot.config.config_snapshot_id,
                "config",
                snapshot.config.content_sha256,
                snapshot.config.model_dump_json(),
            ),
            (
                snapshot.environment.environment_snapshot_id,
                "environment",
                snapshot.environment.content_sha256,
                snapshot.environment.model_dump_json(),
            ),
        ):
            self._connection.execute(
                """
                INSERT OR IGNORE INTO experiment_v2_condition_snapshots
                VALUES (?, ?, ?, ?)
                """,
                (condition_id, kind, content_sha256, payload),
            )
        for observation in snapshot.observations:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO experiment_v2_metric_observations
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    observation.observation_id,
                    snapshot.generation_id,
                    snapshot.run_snapshot_id,
                    observation.series_id,
                    observation.definition_id,
                    observation.numeric_value,
                    observation.canonical_unit,
                    observation.split,
                    observation.step,
                    observation.observed_at,
                    int(observation.valid),
                    observation.content_sha256,
                    observation.model_dump_json(),
                ),
            )
        for artifact in snapshot.artifacts:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO experiment_v2_artifact_versions
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    artifact.artifact_version_id,
                    snapshot.generation_id,
                    snapshot.run_snapshot_id,
                    artifact.verification_state.value,
                    artifact.content_sha256,
                    artifact.model_dump_json(),
                ),
            )

    def _insert_group(self, group: ExperimentRunGroupV2) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO experiment_v2_run_groups VALUES (?, ?, ?, ?, ?)
            """,
            (
                group.run_group_id,
                group.project_id,
                group.experiment_id,
                group.content_sha256,
                group.model_dump_json(),
            ),
        )
        for member in group.members:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO experiment_v2_run_group_members
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group.run_group_id,
                    member.run_snapshot_id,
                    member.run_id,
                    int(member.included),
                    member.exclusion_reason,
                    member.seed,
                    member.fold,
                    member.repeat,
                ),
            )

    def _insert_unit(
        self,
        generation: ExperimentGenerationV2,
        unit: dict[str, Any],
    ) -> None:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO experiment_v2_retrieval_units
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                unit["unit_id"],
                generation.generation_id,
                generation.project_id,
                generation.source_id,
                generation.acl_ref,
                unit["run_snapshot_id"],
                unit["unit_type"],
                unit["content_sha256"],
                _canonical_json(unit["payload"]),
            ),
        )

    def _set_generation_active(self, generation_id: str, *, active: bool) -> None:
        for table in (
            "experiment_v2_run_snapshots",
            "experiment_v2_metric_observations",
            "experiment_v2_artifact_versions",
            "experiment_v2_retrieval_units",
        ):
            self._connection.execute(
                f"UPDATE {table} SET active=? WHERE generation_id=?",
                (int(active), generation_id),
            )

    def _receipt(
        self,
        generation_id: str,
        content_sha256: str,
        *,
        previous_generation_id: str | None,
        idempotent: bool,
    ) -> ExperimentPublishReceiptV2:
        counts = {
            "snapshots": self._count("experiment_v2_run_snapshots", generation_id),
            "observations": self._count(
                "experiment_v2_metric_observations",
                generation_id,
            ),
            "artifacts": self._count("experiment_v2_artifact_versions", generation_id),
            "units": self._count("experiment_v2_retrieval_units", generation_id),
        }
        definition_count = self._connection.execute(
            """
            SELECT COUNT(DISTINCT definition_id)
            FROM experiment_v2_metric_observations
            WHERE generation_id=?
            """,
            (generation_id,),
        ).fetchone()[0]
        group_count = self._connection.execute(
            """
            SELECT COUNT(DISTINCT gm.run_group_id)
            FROM experiment_v2_run_group_members gm
            JOIN experiment_v2_run_snapshots rs
              ON rs.run_snapshot_id=gm.run_snapshot_id
            WHERE rs.generation_id=?
            """,
            (generation_id,),
        ).fetchone()[0]
        return ExperimentPublishReceiptV2(
            generation_id=generation_id,
            content_sha256=content_sha256,
            definitions=int(definition_count),
            groups=int(group_count),
            previous_generation_id=previous_generation_id,
            idempotent=idempotent,
            **counts,
        )

    def _count(self, table: str, generation_id: str) -> int:
        return int(
            self._connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE generation_id=?",
                (generation_id,),
            ).fetchone()[0]
        )

    @staticmethod
    def _generation(row: sqlite3.Row) -> ExperimentGenerationV2:
        return ExperimentGenerationV2(
            generation_id=row["generation_id"],
            project_id=row["project_id"],
            source_id=row["source_id"],
            acl_ref=row["acl_ref"],
            watermark=row["watermark"],
            status=row["status"],
            expected_snapshots=row["expected_snapshots"],
            actual_snapshots=row["actual_snapshots"],
            content_sha256=row["content_sha256"],
            error_code=row["error_code"],
        )


__all__ = [
    "EXPERIMENT_STORE_OWNER",
    "EXPERIMENT_STORE_SCHEMA_VERSION",
    "ExperimentGenerationV2",
    "ExperimentPublishReceiptV2",
    "ExperimentStoreV2",
    "ExperimentStoreV2Error",
]
