from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..storage import SQLiteStore, utc_now
from .schema import SCHEMA


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item


class RawSourceStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(SCHEMA)

    def put_object(self, record: dict[str, Any]) -> dict[str, Any]:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO raw_objects
                   (id, project_id, source_type, source_instance, source_object_id,
                    source_version, source_uri, content_hash, media_type, byte_length,
                    storage_path, acl_ref, state, adapter_version, schema_version,
                    metadata_json, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_instance, source_object_id, source_version, content_hash)
                   DO NOTHING""",
                (
                    record["id"],
                    record["project_id"],
                    record["source_type"],
                    record["source_instance"],
                    record["source_object_id"],
                    record["source_version"],
                    record.get("source_uri"),
                    record["content_hash"],
                    record["media_type"],
                    record["byte_length"],
                    record.get("storage_path"),
                    record["acl_ref"],
                    record["state"],
                    record["adapter_version"],
                    record["schema_version"],
                    json.dumps(record.get("metadata", {}), ensure_ascii=False),
                    record["observed_at"],
                ),
            )
            row = db.execute(
                """SELECT * FROM raw_objects
                   WHERE source_instance=? AND source_object_id=?
                     AND source_version=? AND content_hash=?""",
                (
                    record["source_instance"],
                    record["source_object_id"],
                    record["source_version"],
                    record["content_hash"],
                ),
            ).fetchone()
        return _decode(row) or {}

    def create_event(self, record: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        created = False
        with self.database.transaction() as db:
            cursor = db.execute(
                """INSERT INTO source_events
                   (event_id, idempotency_key, source_type, source_instance, event_type,
                    source_object_id, source_version, event_time, observed_at, project_id,
                    acl_ref, content_hash, raw_object_id, payload_ref, trace_id,
                    schema_version, status, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(idempotency_key) DO NOTHING""",
                (
                    record["event_id"],
                    record["idempotency_key"],
                    record["source_type"],
                    record["source_instance"],
                    record["event_type"],
                    record["source_object_id"],
                    record["source_version"],
                    record.get("event_time"),
                    record["observed_at"],
                    record["project_id"],
                    record["acl_ref"],
                    record["content_hash"],
                    record.get("raw_object_id"),
                    record.get("payload_ref"),
                    record["trace_id"],
                    record["schema_version"],
                    record["status"],
                    json.dumps(record.get("metadata", {}), ensure_ascii=False),
                    utc_now(),
                ),
            )
            created = cursor.rowcount > 0
            row = db.execute(
                "SELECT * FROM source_events WHERE idempotency_key=?",
                (record["idempotency_key"],),
            ).fetchone()
        return _decode(row) or {}, created

    def link_derivations(
        self,
        raw_object_id: str,
        entity_ids: list[str],
        *,
        kind: str,
        generation_id: str | None,
        derivation_version: str,
    ) -> None:
        now = utc_now()
        with self.database.transaction() as db:
            db.executemany(
                """INSERT INTO raw_derivations
                   (raw_object_id, derived_entity_id, derived_kind, generation_id,
                    derivation_version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(raw_object_id, derived_entity_id, derivation_version)
                   DO NOTHING""",
                [
                    (
                        raw_object_id,
                        entity_id,
                        kind,
                        generation_id,
                        derivation_version,
                        now,
                    )
                    for entity_id in dict.fromkeys(entity_ids)
                ],
            )

    def tombstone(self, raw_object_id: str, reason: str, actor: str) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM raw_objects WHERE id=?", (raw_object_id,)).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE raw_objects
                   SET state='tombstoned', tombstoned_at=?,
                       metadata_json=json_set(metadata_json, '$.tombstone_reason', ?, '$.actor', ?)
                   WHERE id=?""",
                (now, reason, actor, raw_object_id),
            )
            derived = db.execute(
                """SELECT derived_entity_id FROM raw_derivations
                   WHERE raw_object_id=? AND invalidated_at IS NULL""",
                (raw_object_id,),
            ).fetchall()
            db.execute(
                "UPDATE raw_derivations SET invalidated_at=? WHERE raw_object_id=?",
                (now, raw_object_id),
            )
            db.executemany(
                """INSERT INTO blocked_entities(entity_id, raw_object_id, reason, blocked_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(entity_id) DO UPDATE SET raw_object_id=excluded.raw_object_id,
                     reason=excluded.reason, blocked_at=excluded.blocked_at""",
                [(item["derived_entity_id"], raw_object_id, reason, now) for item in derived],
            )
            updated = db.execute(
                "SELECT * FROM raw_objects WHERE id=?", (raw_object_id,)
            ).fetchone()
        result = _decode(updated) or {}
        result["blocked_entities"] = len(derived)
        return result

    def get_object(self, raw_object_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM raw_objects WHERE id=?", (raw_object_id,)).fetchone()
            if row is None:
                return None
            count = db.execute(
                "SELECT count(*) FROM raw_derivations WHERE raw_object_id=?",
                (raw_object_id,),
            ).fetchone()[0]
        item = _decode(row) or {}
        item["derived_count"] = count
        return item

    def list_objects(
        self, project_id: str, *, state: str | None, limit: int
    ) -> list[dict[str, Any]]:
        clauses = ["project_id=?"]
        values: list[Any] = [project_id]
        if state:
            clauses.append("state=?")
            values.append(state)
        values.append(limit)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT raw_objects.*,
                           (SELECT count(*) FROM raw_derivations d
                            WHERE d.raw_object_id=raw_objects.id) AS derived_count
                    FROM raw_objects WHERE {" AND ".join(clauses)}
                    ORDER BY observed_at DESC LIMIT ?""",
                values,
            ).fetchall()
        return [_decode(row) or {} for row in rows]

    def list_events(self, project_id: str, limit: int) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM source_events WHERE project_id=?
                   ORDER BY observed_at DESC LIMIT ?""",
                (project_id, limit),
            ).fetchall()
        return [_decode(row) or {} for row in rows]

    def stats(self, project_id: str) -> dict[str, Any]:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT count(*) objects,
                          sum(CASE WHEN state='active' THEN 1 ELSE 0 END) active,
                          sum(CASE WHEN state='quarantined' THEN 1 ELSE 0 END) quarantined,
                          sum(CASE WHEN state='tombstoned' THEN 1 ELSE 0 END) tombstoned,
                          coalesce(sum(byte_length), 0) bytes
                   FROM raw_objects WHERE project_id=?""",
                (project_id,),
            ).fetchone()
            events = db.execute(
                "SELECT count(*) FROM source_events WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            blocked = db.execute("SELECT count(*) FROM blocked_entities").fetchone()[0]
            by_type = {
                item["source_type"]: int(item["count"])
                for item in db.execute(
                    """SELECT source_type, count(*) AS count FROM raw_objects
                       WHERE project_id=? GROUP BY source_type ORDER BY source_type""",
                    (project_id,),
                ).fetchall()
            }
        return {
            **{key: int(value or 0) for key, value in dict(row).items()},
            "events": int(events),
            "blocked_entities": int(blocked),
            "by_type": by_type,
        }
