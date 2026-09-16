from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..storage import SQLiteStore, utc_now
from .schema import SCHEMA

JSON_FIELDS = {"tags", "config", "environment", "metadata"}
OBSERVATION_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS experiment_run_observations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(run_id, source_version),
    FOREIGN KEY(run_id) REFERENCES experiment_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_experiment_run_observations
    ON experiment_run_observations(run_id, observed_at DESC);
"""


def _decode(row: sqlite3.Row | None, *fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in fields:
        item[field] = json.loads(item.pop(f"{field}_json") or "{}")
    return item


class ExperimentStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(SCHEMA)
            db.executescript(OBSERVATION_SCHEMA)

    def upsert_source(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO experiment_sources
                   (id, project_id, adapter_type, tracking_uri, status, stats_json,
                    last_error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id, adapter_type, tracking_uri) DO UPDATE SET
                     status=excluded.status, stats_json=excluded.stats_json,
                     last_error=excluded.last_error, updated_at=excluded.updated_at""",
                (
                    record["id"],
                    record["project_id"],
                    record["adapter_type"],
                    record["tracking_uri"],
                    record["status"],
                    json.dumps(record.get("stats", {}), ensure_ascii=False),
                    record.get("last_error"),
                    now,
                    now,
                ),
            )
            row = db.execute(
                """SELECT * FROM experiment_sources
                   WHERE project_id=? AND adapter_type=? AND tracking_uri=?""",
                (record["project_id"], record["adapter_type"], record["tracking_uri"]),
            ).fetchone()
        return _decode(row, "stats") or {}

    def source_mapping(self, source_id: str, external_id: str) -> str | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT experiment_id FROM external_experiment_mappings
                   WHERE source_id=? AND external_experiment_id=?""",
                (source_id, external_id),
            ).fetchone()
        return row["experiment_id"] if row else None

    def list_sources(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM experiment_sources WHERE project_id=?
                   ORDER BY updated_at DESC""",
                (project_id,),
            ).fetchall()
        return [_decode(row, "stats") or {} for row in rows]

    def find_repository(self, project_id: str, source_url: str | None) -> str | None:
        if not source_url:
            return None
        normalized = source_url.removesuffix(".git").rstrip("/")
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT id, source_url FROM repositories WHERE project_id=?",
                (project_id,),
            ).fetchall()
        for row in rows:
            candidate = str(row["source_url"] or "").removesuffix(".git").rstrip("/")
            if candidate and candidate == normalized:
                return row["id"]
        return None

    def map_experiment(
        self, source_id: str, external_id: str, experiment_id: str, metadata: dict[str, Any]
    ) -> None:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO external_experiment_mappings
                   (source_id, external_experiment_id, experiment_id, metadata_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_id, external_experiment_id) DO UPDATE SET
                     experiment_id=excluded.experiment_id,
                     metadata_json=excluded.metadata_json,
                     updated_at=excluded.updated_at""",
                (
                    source_id,
                    external_id,
                    experiment_id,
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    def create_experiment(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO experiments
                   (id, display_key, project_id, iteration_id, title, objective, hypothesis,
                    owner, status, tags_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record.get("iteration_id"),
                    record["title"],
                    record["objective"],
                    record["hypothesis"],
                    record["owner"],
                    record["status"],
                    json.dumps(record.get("tags", []), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_experiment(record["id"]) or {}

    def get_experiment(self, experiment_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT e.*,
                          (SELECT count(*) FROM experiment_runs r
                           WHERE r.experiment_id=e.id) AS run_count
                   FROM experiments e WHERE e.id=?""",
                (experiment_id,),
            ).fetchone()
        return _decode(row, "tags")

    def list_experiments(self, project_id: str, status: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE e.project_id=?" + (" AND e.status=?" if status else "")
        values = [project_id, status] if status else [project_id]
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT e.*,
                           (SELECT count(*) FROM experiment_runs r
                            WHERE r.experiment_id=e.id) AS run_count,
                           (SELECT count(*) FROM experiment_runs r
                            WHERE r.experiment_id=e.id AND r.status='completed') AS completed_runs
                    FROM experiments e {where} ORDER BY e.updated_at DESC""",
                values,
            ).fetchall()
        return [_decode(row, "tags") or {} for row in rows]

    def update_experiment(
        self, experiment_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        self._update("experiments", experiment_id, changes)
        return self.get_experiment(experiment_id)

    def resolve_commit(self, repository_id: str, commit_sha: str) -> str | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT e.id FROM entities e
                   JOIN repositories r ON r.id=e.repository_id
                   WHERE e.repository_id=? AND e.generation_id=r.active_generation_id
                     AND e.entity_type='Commit' AND e.commit_sha=? LIMIT 1""",
                (repository_id, commit_sha),
            ).fetchone()
            if row is None:
                row = db.execute(
                    """SELECT id FROM git_commits
                       WHERE repository_id=? AND (sha=? OR sha LIKE ?)
                       ORDER BY length(sha) LIMIT 1""",
                    (repository_id, commit_sha, f"{commit_sha}%"),
                ).fetchone()
        return row["id"] if row else None

    def create_run(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO experiment_runs
                   (id, display_key, project_id, experiment_id, external_id, name, status,
                    repository_id, commit_sha, commit_entity_id, branch, dataset_id,
                    dataset_version, config_json, environment_json, command, started_at,
                    completed_at, tags_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record["experiment_id"],
                    record.get("external_id"),
                    record["name"],
                    record["status"],
                    record.get("repository_id"),
                    record.get("commit_sha"),
                    record.get("commit_entity_id"),
                    record.get("branch"),
                    record.get("dataset_id"),
                    record.get("dataset_version"),
                    json.dumps(record.get("config", {}), ensure_ascii=False),
                    json.dumps(record.get("environment", {}), ensure_ascii=False),
                    record.get("command", ""),
                    record.get("started_at"),
                    record.get("completed_at"),
                    json.dumps(record.get("tags", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            for metric in record.get("metrics", []):
                db.execute(
                    """INSERT INTO run_metrics
                       (id, project_id, run_id, name, value, unit, split, step,
                        higher_is_better, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         value=excluded.value, unit=excluded.unit, split=excluded.split,
                         step=excluded.step,
                         higher_is_better=excluded.higher_is_better""",
                    (
                        metric["id"],
                        record["project_id"],
                        record["id"],
                        metric["name"],
                        metric["value"],
                        metric.get("unit"),
                        metric.get("split"),
                        metric.get("step"),
                        (
                            int(metric["higher_is_better"])
                            if metric.get("higher_is_better") is not None
                            else None
                        ),
                        now,
                    ),
                )
            for artifact in record.get("artifacts", []):
                db.execute(
                    """INSERT INTO run_artifacts
                       (id, project_id, run_id, name, uri, kind, checksum, media_type,
                        metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         name=excluded.name, uri=excluded.uri, kind=excluded.kind,
                         checksum=excluded.checksum, media_type=excluded.media_type,
                         metadata_json=excluded.metadata_json""",
                    (
                        artifact["id"],
                        record["project_id"],
                        record["id"],
                        artifact["name"],
                        artifact["uri"],
                        artifact["kind"],
                        artifact.get("checksum"),
                        artifact.get("media_type"),
                        json.dumps(artifact.get("metadata", {}), ensure_ascii=False),
                        now,
                    ),
                )
            self._record_observation(db, record, record["id"], now)
        return self.get_run(record["id"]) or {}

    def upsert_imported_run(self, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self.database.connection() as db:
            existing = db.execute(
                """SELECT id, status FROM experiment_runs
                   WHERE experiment_id=? AND external_id=?""",
                (record["experiment_id"], record["external_id"]),
            ).fetchone()
        if existing is None:
            return self.create_run(record), True
        run_id = existing["id"]
        now = utc_now()
        with self.database.transaction() as db:
            self._record_observation(db, record, run_id, now)
            current = db.execute(
                "SELECT status FROM experiment_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            existing_status = str(current["status"] or "").casefold() if current else "deleted"
            incoming_status = str(record.get("status") or "").casefold()
            if incoming_status == "deleted":
                db.execute(
                    "UPDATE experiment_runs SET status='deleted', updated_at=? WHERE id=?",
                    (now, run_id),
                )
            elif existing_status not in {"completed", "deleted"}:
                db.execute(
                    """UPDATE experiment_runs SET name=?, status=?, repository_id=?,
                         commit_sha=?, commit_entity_id=?, branch=?, dataset_id=?,
                         dataset_version=?, config_json=?, environment_json=?, command=?,
                         started_at=?, completed_at=?, tags_json=?, updated_at=? WHERE id=?""",
                    (
                        record["name"],
                        record["status"],
                        record.get("repository_id"),
                        record.get("commit_sha"),
                        record.get("commit_entity_id"),
                        record.get("branch"),
                        record.get("dataset_id"),
                        record.get("dataset_version"),
                        json.dumps(record.get("config", {}), ensure_ascii=False),
                        json.dumps(record.get("environment", {}), ensure_ascii=False),
                        record.get("command", ""),
                        record.get("started_at"),
                        record.get("completed_at"),
                        json.dumps(record.get("tags", {}), ensure_ascii=False),
                        now,
                        run_id,
                    ),
                )
                db.executemany(
                    """INSERT INTO run_metrics
                       (id, project_id, run_id, name, value, unit, split, step,
                        higher_is_better, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         value=excluded.value, unit=excluded.unit, split=excluded.split,
                         step=excluded.step,
                         higher_is_better=excluded.higher_is_better""",
                    [
                        (
                            item["id"],
                            record["project_id"],
                            run_id,
                            item["name"],
                            item["value"],
                            item.get("unit"),
                            item.get("split"),
                            item.get("step"),
                            (
                                int(item["higher_is_better"])
                                if item.get("higher_is_better") is not None
                                else None
                            ),
                            now,
                        )
                        for item in record.get("metrics", [])
                    ],
                )
                db.executemany(
                    """INSERT INTO run_artifacts
                       (id, project_id, run_id, name, uri, kind, checksum, media_type,
                        metadata_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         name=excluded.name, uri=excluded.uri, kind=excluded.kind,
                         checksum=excluded.checksum, media_type=excluded.media_type,
                         metadata_json=excluded.metadata_json""",
                    [
                        (
                            item["id"],
                            record["project_id"],
                            run_id,
                            item["name"],
                            item["uri"],
                            item["kind"],
                            item.get("checksum"),
                            item.get("media_type"),
                            json.dumps(item.get("metadata", {}), ensure_ascii=False),
                            now,
                        )
                        for item in record.get("artifacts", [])
                    ],
                )
        return self.get_run(run_id) or {}, False

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM experiment_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            metrics = db.execute(
                """SELECT * FROM run_metrics
                   WHERE run_id=? ORDER BY name, split, step, created_at""",
                (run_id,),
            ).fetchall()
            artifacts = db.execute(
                "SELECT * FROM run_artifacts WHERE run_id=? ORDER BY kind, name", (run_id,)
            ).fetchall()
        result = _decode(row, "config", "environment", "tags") or {}
        result["metrics"] = [dict(item) for item in metrics]
        result["artifacts"] = [_decode(item, "metadata") or {} for item in artifacts]
        return result

    def list_run_observations(self, run_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM experiment_run_observations
                   WHERE run_id=? ORDER BY observed_at, id""",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            results.append(item)
        return results

    def get_metric(self, metric_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM run_metrics WHERE id=?", (metric_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(
        self, project_id: str, *, experiment_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["r.project_id=?"]
        values: list[Any] = [project_id]
        if experiment_id:
            clauses.append("r.experiment_id=?")
            values.append(experiment_id)
        if status:
            clauses.append("r.status=?")
            values.append(status)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT r.*, e.title AS experiment_title,
                           (SELECT count(*) FROM run_metrics m WHERE m.run_id=r.id) metric_count,
                           (SELECT count(*) FROM run_artifacts a WHERE a.run_id=r.id) artifact_count
                    FROM experiment_runs r JOIN experiments e ON e.id=r.experiment_id
                    WHERE {" AND ".join(clauses)} ORDER BY r.created_at DESC""",
                values,
            ).fetchall()
        return [_decode(row, "config", "environment", "tags") or {} for row in rows]

    def update_run(self, run_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        self._update("experiment_runs", run_id, changes)
        return self.get_run(run_id)

    def save_comparison(self, record: dict[str, Any]) -> dict[str, Any]:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO experiment_comparisons
                   (id, display_key, project_id, experiment_id, name, baseline_run_id,
                    candidate_run_ids_json, result_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record["experiment_id"],
                    record["name"],
                    record["baseline_run_id"],
                    json.dumps(record["candidate_run_ids"], ensure_ascii=False),
                    json.dumps(record["result"], ensure_ascii=False),
                    utc_now(),
                ),
            )
        return record

    def stats(self, project_id: str) -> dict[str, int]:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT
                     (SELECT count(*) FROM experiments WHERE project_id=?) AS experiments,
                     (SELECT count(*) FROM experiment_runs WHERE project_id=?) AS runs,
                     (SELECT count(*) FROM experiment_runs
                      WHERE project_id=? AND status='completed') AS completed_runs,
                     (SELECT count(*) FROM experiment_runs
                      WHERE project_id=? AND commit_entity_id IS NOT NULL) AS version_bound_runs,
                     (SELECT count(*) FROM run_metrics WHERE project_id=?) AS metrics
                   """,
                (project_id, project_id, project_id, project_id, project_id),
            ).fetchone()
        return {key: int(value or 0) for key, value in dict(row).items()}

    def _update(self, table: str, record_id: str, changes: dict[str, Any]) -> None:
        if not changes:
            return
        fields = []
        values = []
        for key, value in changes.items():
            is_json = key in JSON_FIELDS
            fields.append(f"{key}_json=?" if is_json else f"{key}=?")
            values.append(json.dumps(value, ensure_ascii=False) if is_json else value)
        fields.append("updated_at=?")
        values.extend([utc_now(), record_id])
        with self.database.transaction() as db:
            db.execute(f"UPDATE {table} SET {', '.join(fields)} WHERE id=?", values)

    @staticmethod
    def _record_observation(
        db: sqlite3.Connection,
        record: dict[str, Any],
        run_id: str,
        observed_at: str,
    ) -> None:
        payload = {
            **record,
            "id": run_id,
        }
        source_version = str(record.get("observed_version") or "")
        if not source_version:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            source_version = "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()
        token = hashlib.sha256(f"{run_id}\x1f{source_version}".encode()).hexdigest()
        observation_id = f"run-observation://sha256:{token}"
        db.execute(
            """INSERT INTO experiment_run_observations
               (id, project_id, run_id, source_version, status, payload_json, observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id, source_version) DO NOTHING""",
            (
                observation_id,
                record["project_id"],
                run_id,
                source_version,
                str(record.get("status") or "unknown"),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                observed_at,
            ),
        )
