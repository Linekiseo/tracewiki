from __future__ import annotations

import json
from typing import Any

from ..storage import SQLiteStore, utc_now
from .schema import SCHEMA


class CodexComparisonStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(SCHEMA)

    def save(self, record: dict[str, Any]) -> dict[str, Any]:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO codex_comparisons
                   (id, display_key, project_id, name, baseline_thread_id,
                    thread_ids_json, result_json, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record["name"],
                    record["baseline_thread_id"],
                    json.dumps(record["thread_ids"], ensure_ascii=False),
                    json.dumps(record["result"], ensure_ascii=False),
                    record["created_by"],
                    utc_now(),
                ),
            )
        return self.get(record["id"]) or {}

    def get(self, comparison_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT * FROM codex_comparisons WHERE id=?", (comparison_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def list(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM codex_comparisons WHERE project_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (project_id, limit),
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row) -> dict[str, Any]:
        item = dict(row)
        item["thread_ids"] = json.loads(item.pop("thread_ids_json"))
        item["result"] = json.loads(item.pop("result_json"))
        return item
