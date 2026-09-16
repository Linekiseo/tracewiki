from __future__ import annotations

import json
import sqlite3
from typing import Any
from uuid import uuid4

from ..storage import SQLiteStore, utc_now
from .schema import SCHEMA


def _candidate(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["target_symbol_ids"] = json.loads(item.pop("target_symbol_ids_json") or "[]")
    item["signals"] = json.loads(item.pop("signals_json") or "{}")
    return item


class BindingStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(SCHEMA)

    def create_scan(self, scan_id: str, project_id: str, request: dict[str, Any]) -> None:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO binding_scans
                   (id, project_id, status, request_json, started_at)
                   VALUES (?, ?, 'running', ?, ?)""",
                (scan_id, project_id, json.dumps(request, ensure_ascii=False), utc_now()),
            )

    def complete_scan(self, scan_id: str, counts: dict[str, int]) -> None:
        with self.database.transaction() as db:
            db.execute(
                """UPDATE binding_scans SET status='completed', counts_json=?, completed_at=?
                   WHERE id=?""",
                (json.dumps(counts, ensure_ascii=False), utc_now(), scan_id),
            )

    def update_scan_progress(self, scan_id: str, counts: dict[str, int]) -> None:
        with self.database.transaction() as db:
            db.execute(
                """UPDATE binding_scans SET counts_json=?
                   WHERE id=? AND status='running'""",
                (json.dumps(counts, ensure_ascii=False), scan_id),
            )

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM binding_scans WHERE id=?", (scan_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json") or "{}")
        result["counts"] = json.loads(result.pop("counts_json") or "{}")
        return result

    def fail_scan(self, scan_id: str, error: str) -> None:
        with self.database.transaction() as db:
            db.execute(
                """UPDATE binding_scans SET status='failed', error=?, completed_at=? WHERE id=?""",
                (error, utc_now(), scan_id),
            )

    def source_changes(self, project_id: str, thread_ids: list[str]) -> list[dict[str, Any]]:
        clauses = [
            "t.project_id=?",
            "i.item_type IN ('FileChange','Patch')",
            "i.generation_id=s.active_generation_id",
        ]
        values: list[Any] = [project_id]
        if thread_ids:
            marks = ",".join("?" for _ in thread_ids)
            clauses.append(f"(t.thread_id IN ({marks}) OR t.id IN ({marks}))")
            values.extend([*thread_ids, *thread_ids])
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT i.id, i.thread_id, i.turn_id, i.item_type, i.name,
                           i.content, i.timestamp, i.metadata_json,
                           t.thread_id AS raw_thread_id, t.title AS thread_title, t.cwd
                    FROM codex_items i
                    JOIN codex_sources s ON s.id=i.source_id
                    JOIN codex_threads t ON t.id=i.thread_id AND t.source_id=i.source_id
                         AND t.generation_id=i.generation_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY i.timestamp DESC, i.sequence DESC""",
                values,
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            results.append(item)
        return results

    def repository_snapshots(
        self, project_id: str, repository_ids: list[str]
    ) -> list[dict[str, Any]]:
        clauses = ["r.project_id=?", "r.status='ready'", "r.active_generation_id IS NOT NULL"]
        values: list[Any] = [project_id]
        if repository_ids:
            marks = ",".join("?" for _ in repository_ids)
            clauses.append(f"r.id IN ({marks})")
            values.extend(repository_ids)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT r.id, r.name, r.local_path, r.head_commit, r.active_generation_id,
                           e.id AS commit_id
                    FROM repositories r
                    LEFT JOIN entities e ON e.repository_id=r.id
                         AND e.generation_id=r.active_generation_id AND e.entity_type='Commit'
                    WHERE {" AND ".join(clauses)} ORDER BY r.updated_at DESC""",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def files_for_repository(self, repository_id: str, generation_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT id, path, commit_sha, language FROM entities
                   WHERE repository_id=? AND generation_id=? AND entity_type='FileVersion'""",
                (repository_id, generation_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def symbols_for_file(self, repository_id: str, generation_id: str, path: str) -> list[str]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT id FROM entities
                   WHERE repository_id=? AND generation_id=? AND path=?
                     AND entity_type='CodeSymbol' ORDER BY start_line""",
                (repository_id, generation_id, path),
            ).fetchall()
        return [row["id"] for row in rows]

    def history_candidates(
        self, repository_id: str, path: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT h.*, c.committed_at, c.message, c.source_uri AS commit_uri
                   FROM diff_hunks h JOIN git_commits c ON c.id=h.commit_id
                   WHERE h.repository_id=? AND (h.path=? OR h.old_path=?)
                   ORDER BY c.committed_at DESC LIMIT ?""",
                (repository_id, path, path, limit),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["affected_symbols"] = json.loads(item.pop("affected_symbols_json") or "[]")
            results.append(item)
        return results

    def resolve_history_commit(self, repository_id: str, sha: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT * FROM git_commits
                   WHERE repository_id=? AND (sha=? OR sha LIKE ?)
                   ORDER BY length(sha) LIMIT 1""",
                (repository_id, sha, f"{sha}%"),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["parent_shas"] = json.loads(item.pop("parent_shas_json") or "[]")
        return item

    def record_unmatched(self, record: dict[str, Any]) -> None:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO unmatched_changes
                   (id, project_id, scan_id, source_entity_id, source_thread_id,
                    changed_path, repository_id, reason, signals_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_entity_id, changed_path, repository_id) DO UPDATE SET
                     scan_id=excluded.scan_id, reason=excluded.reason,
                     signals_json=excluded.signals_json, created_at=excluded.created_at""",
                (
                    record["id"],
                    record["project_id"],
                    record["scan_id"],
                    record["source_entity_id"],
                    record["source_thread_id"],
                    record["changed_path"],
                    record.get("repository_id"),
                    record["reason"],
                    json.dumps(record.get("signals", {}), ensure_ascii=False),
                    utc_now(),
                ),
            )

    def publish_scan(
        self,
        scan_id: str,
        *,
        candidates: list[dict[str, Any]],
        unmatched: list[dict[str, Any]],
        counts: dict[str, int],
    ) -> None:
        """Publish one completed scan atomically.

        Candidate discovery can be CPU intensive for large Codex histories.  The
        old row-at-a-time implementation exposed a partially populated review
        queue when a request was interrupted.  This transaction keeps discovery
        read-only until all rows and the completed scan marker can be committed
        together.
        """

        now = utc_now()
        candidate_sql = """INSERT INTO binding_candidates
                   (id, project_id, scan_id, binding_type, source_entity_id,
                    source_thread_id, source_turn_id, source_item_type, source_title,
                    changed_path, repository_id, repository_name, target_entity_id,
                    target_generation_id, target_path, target_commit_id, target_commit_sha,
                    target_symbol_ids_json, derivation, confidence, review_status,
                    signals_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'unreviewed', ?, ?, ?)
                   ON CONFLICT(source_entity_id, repository_id, changed_path, target_entity_id)
                   DO UPDATE SET
                     scan_id=excluded.scan_id,
                     source_title=excluded.source_title,
                     target_generation_id=excluded.target_generation_id,
                     target_commit_id=excluded.target_commit_id,
                     target_commit_sha=excluded.target_commit_sha,
                     target_symbol_ids_json=excluded.target_symbol_ids_json,
                     derivation=excluded.derivation,
                     confidence=excluded.confidence,
                     signals_json=excluded.signals_json,
                     updated_at=excluded.updated_at"""
        candidate_rows = [
            (
                record["id"],
                record["project_id"],
                scan_id,
                record["binding_type"],
                record["source_entity_id"],
                record["source_thread_id"],
                record.get("source_turn_id"),
                record["source_item_type"],
                record["source_title"],
                record["changed_path"],
                record["repository_id"],
                record["repository_name"],
                record["target_entity_id"],
                record["target_generation_id"],
                record["target_path"],
                record.get("target_commit_id"),
                record.get("target_commit_sha"),
                json.dumps(record.get("target_symbol_ids", []), ensure_ascii=False),
                record["derivation"],
                record["confidence"],
                json.dumps(record.get("signals", {}), ensure_ascii=False),
                now,
                now,
            )
            for record in candidates
        ]
        unmatched_sql = """INSERT INTO unmatched_changes
                   (id, project_id, scan_id, source_entity_id, source_thread_id,
                    changed_path, repository_id, reason, signals_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_entity_id, changed_path, repository_id) DO UPDATE SET
                     scan_id=excluded.scan_id, reason=excluded.reason,
                     signals_json=excluded.signals_json, created_at=excluded.created_at"""
        unmatched_rows = [
            (
                record["id"],
                record["project_id"],
                scan_id,
                record["source_entity_id"],
                record["source_thread_id"],
                record["changed_path"],
                record.get("repository_id"),
                record["reason"],
                json.dumps(record.get("signals", {}), ensure_ascii=False),
                now,
            )
            for record in unmatched
        ]
        with self.database.transaction() as db:
            scan = db.execute(
                "SELECT project_id FROM binding_scans WHERE id=? AND status='running'",
                (scan_id,),
            ).fetchone()
            if scan is None:
                raise ValueError("binding scan is not running")
            project_id = str(scan["project_id"])
            # A completed scan is the authority for the current review queue.
            # Preserve human decisions, but atomically discard stale unreviewed
            # candidates and unmatched rows from older scans before publishing
            # the newly derived set.
            db.execute(
                "DELETE FROM binding_candidates WHERE project_id=? AND review_status='unreviewed'",
                (project_id,),
            )
            db.execute("DELETE FROM unmatched_changes WHERE project_id=?", (project_id,))
            if candidate_rows:
                db.executemany(candidate_sql, candidate_rows)
            if unmatched_rows:
                db.executemany(unmatched_sql, unmatched_rows)
            db.execute(
                """UPDATE binding_scans SET status='completed', counts_json=?, completed_at=?
                   WHERE id=? AND status='running'""",
                (json.dumps(counts, ensure_ascii=False), now, scan_id),
            )

    def list_unmatched(self, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM unmatched_changes WHERE project_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (project_id, limit),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["signals"] = json.loads(item.pop("signals_json") or "{}")
            results.append(item)
        return results

    def upsert_candidate(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO binding_candidates
                   (id, project_id, scan_id, binding_type, source_entity_id,
                    source_thread_id, source_turn_id, source_item_type, source_title,
                    changed_path, repository_id, repository_name, target_entity_id,
                    target_generation_id, target_path, target_commit_id, target_commit_sha,
                    target_symbol_ids_json, derivation, confidence, review_status,
                    signals_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'unreviewed', ?, ?, ?)
                   ON CONFLICT(source_entity_id, repository_id, changed_path, target_entity_id)
                   DO UPDATE SET
                     scan_id=excluded.scan_id,
                     source_title=excluded.source_title,
                     target_generation_id=excluded.target_generation_id,
                     target_commit_id=excluded.target_commit_id,
                     target_commit_sha=excluded.target_commit_sha,
                     target_symbol_ids_json=excluded.target_symbol_ids_json,
                     derivation=excluded.derivation,
                     confidence=excluded.confidence,
                     signals_json=excluded.signals_json,
                     updated_at=excluded.updated_at""",
                (
                    record["id"],
                    record["project_id"],
                    record["scan_id"],
                    record["binding_type"],
                    record["source_entity_id"],
                    record["source_thread_id"],
                    record.get("source_turn_id"),
                    record["source_item_type"],
                    record["source_title"],
                    record["changed_path"],
                    record["repository_id"],
                    record["repository_name"],
                    record["target_entity_id"],
                    record["target_generation_id"],
                    record["target_path"],
                    record.get("target_commit_id"),
                    record.get("target_commit_sha"),
                    json.dumps(record.get("target_symbol_ids", []), ensure_ascii=False),
                    record["derivation"],
                    record["confidence"],
                    json.dumps(record.get("signals", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = db.execute(
                """SELECT * FROM binding_candidates
                   WHERE source_entity_id=? AND repository_id=? AND changed_path=?
                     AND target_entity_id=?""",
                (
                    record["source_entity_id"],
                    record["repository_id"],
                    record["changed_path"],
                    record["target_entity_id"],
                ),
            ).fetchone()
        return _candidate(row) or {}

    def get_candidate(self, binding_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT * FROM binding_candidates WHERE id=?", (binding_id,)
            ).fetchone()
            if row is None:
                return None
            source = db.execute(
                """SELECT content, source_locator, metadata_json FROM codex_items
                   WHERE id=? ORDER BY item_key DESC LIMIT 1""",
                (row["source_entity_id"],),
            ).fetchone()
            target = db.execute(
                """SELECT content, source_uri, language, start_line, end_line
                   FROM entities WHERE id=? AND generation_id=? LIMIT 1""",
                (row["target_entity_id"], row["target_generation_id"]),
            ).fetchone()
            history_target = None
            if target is None and row["binding_type"] == "codex_patch_commit":
                history_target = db.execute(
                    """SELECT id, message AS content, source_uri, NULL language,
                              NULL start_line, NULL end_line
                       FROM git_commits WHERE id=?""",
                    (row["target_entity_id"],),
                ).fetchone()
        result = _candidate(row) or {}
        if source:
            result["source"] = {
                "content": source["content"],
                "locator": source["source_locator"],
                "metadata": json.loads(source["metadata_json"] or "{}"),
            }
        if target or history_target:
            result["target"] = dict(target or history_target)
        return result

    def list_candidates(
        self,
        project_id: str,
        *,
        review_status: str | None = None,
        thread_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id=?"]
        values: list[Any] = [project_id]
        if review_status:
            clauses.append("review_status=?")
            values.append(review_status)
        if thread_id:
            clauses.append("source_thread_id=?")
            values.append(thread_id)
        values.append(limit)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT * FROM binding_candidates WHERE {" AND ".join(clauses)}
                    ORDER BY CASE review_status WHEN 'unreviewed' THEN 0 ELSE 1 END,
                             confidence DESC, updated_at DESC LIMIT ?""",
                values,
            ).fetchall()
        return [_candidate(row) or {} for row in rows]

    def review(
        self, binding_id: str, decision: str, reviewer: str, note: str
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """UPDATE binding_candidates SET review_status=?, reviewed_by=?,
                   reviewed_at=?, review_note=?, updated_at=? WHERE id=?""",
                (decision, reviewer, now, note, now, binding_id),
            )
        return self.get_candidate(binding_id)

    def confirm_all(
        self,
        project_id: str,
        *,
        expected_pending: int,
        reviewer: str,
        note: str,
    ) -> dict[str, Any]:
        """Confirm one immutable snapshot of the pending queue atomically."""

        now = utc_now()
        with self.database.transaction() as db:
            rows = db.execute(
                """SELECT * FROM binding_candidates
                   WHERE project_id=? AND review_status='unreviewed'
                   ORDER BY confidence DESC, updated_at DESC, id""",
                (project_id,),
            ).fetchall()
            if len(rows) != expected_pending:
                raise ValueError(
                    "pending relation queue changed; refresh the queue before confirming all"
                )
            if not rows:
                raise ValueError("no pending binding candidates to confirm")

            invalid = db.execute(
                """SELECT bc.id
                   FROM binding_candidates bc
                   WHERE bc.project_id=? AND bc.review_status='unreviewed'
                     AND NOT (
                       EXISTS (
                         SELECT 1 FROM codex_items i
                         JOIN codex_sources s ON s.id=i.source_id
                         WHERE i.id=bc.source_entity_id
                           AND i.generation_id=s.active_generation_id
                           AND s.project_id=bc.project_id
                       )
                       AND EXISTS (
                         SELECT 1 FROM repositories r
                         WHERE r.id=bc.repository_id
                           AND r.project_id=bc.project_id
                           AND r.status='ready'
                           AND r.active_generation_id=bc.target_generation_id
                       )
                       AND (
                         (bc.binding_type='codex_patch_commit' AND EXISTS (
                           SELECT 1 FROM git_commits c
                           WHERE c.id=bc.target_entity_id
                             AND c.repository_id=bc.repository_id
                         ))
                         OR
                         (bc.binding_type!='codex_patch_commit' AND EXISTS (
                           SELECT 1 FROM entities e
                           WHERE e.id=bc.target_entity_id
                             AND e.repository_id=bc.repository_id
                             AND e.generation_id=bc.target_generation_id
                         ))
                       )
                     )
                   LIMIT 1""",
                (project_id,),
            ).fetchone()
            if invalid:
                raise ValueError(
                    "pending relation queue contains stale or unavailable evidence; rescan first"
                )

            relation_rows: list[tuple[Any, ...]] = []
            relation_updates: list[tuple[Any, ...]] = []
            audit_rows: list[tuple[Any, ...]] = []
            candidate_ids = [str(row["id"]) for row in rows]
            relation_groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
            for row in rows:
                candidate = dict(row)
                predicate = (
                    "applied_as"
                    if candidate["binding_type"] == "codex_patch_commit"
                    else "changed_path_maps_to"
                )
                key = (
                    candidate["source_entity_id"],
                    predicate,
                    candidate["target_entity_id"],
                    candidate["source_entity_id"],
                )
                relation_groups.setdefault(key, []).append(candidate)

            existing_rows = db.execute(
                """SELECT * FROM platform_edges WHERE project_id=?""",
                (project_id,),
            ).fetchall()
            existing_by_key = {
                (
                    str(row["source_entity_id"]),
                    str(row["predicate"]),
                    str(row["target_entity_id"]),
                    str(row["evidence_entity_id"]),
                ): row
                for row in existing_rows
            }
            for (source_id, predicate, target_id, evidence_id), group in relation_groups.items():
                primary = group[0]
                existing = existing_by_key.get((source_id, predicate, target_id, evidence_id))
                if existing and existing["review_status"] != "confirmed":
                    raise ValueError(
                        "pending relation queue conflicts with an existing unconfirmed relation"
                    )
                relation_id = str(existing["id"]) if existing else f"edge://{uuid4().hex}"
                existing_metadata = json.loads(str(existing["metadata_json"])) if existing else {}
                binding_ids = sorted(
                    {
                        *[str(value) for value in existing_metadata.get("binding_ids", [])],
                        *(
                            [str(existing_metadata["binding_id"])]
                            if existing_metadata.get("binding_id")
                            else []
                        ),
                        *[str(candidate["id"]) for candidate in group],
                    }
                )
                changed_paths = sorted(
                    {
                        *[str(value) for value in existing_metadata.get("changed_paths", [])],
                        *(
                            [str(existing_metadata["changed_path"])]
                            if existing_metadata.get("changed_path")
                            else []
                        ),
                        *[str(candidate["changed_path"]) for candidate in group],
                    }
                )
                metadata = {
                    "binding_id": primary["id"],
                    "binding_ids": binding_ids,
                    "changed_path": primary["changed_path"],
                    "changed_paths": changed_paths,
                    "target_commit_sha": primary["target_commit_sha"],
                    "commit_context_only": primary["binding_type"] != "codex_patch_commit",
                    "bulk_review": True,
                }
                confidence = max(float(candidate["confidence"]) for candidate in group)
                if existing:
                    relation_updates.append(
                        (
                            max(confidence, float(existing["confidence"])),
                            json.dumps(metadata, ensure_ascii=False),
                            reviewer,
                            now,
                            note,
                            now,
                            relation_id,
                        )
                    )
                else:
                    relation_rows.append(
                        (
                            relation_id,
                            project_id,
                            source_id,
                            predicate,
                            target_id,
                            evidence_id,
                            "human_confirmed",
                            confidence,
                            "confirmed",
                            now,
                            None,
                            "codex-path-binding-v1",
                            json.dumps(metadata, ensure_ascii=False),
                            reviewer,
                            now,
                            note,
                            now,
                            now,
                        )
                    )
                audit_rows.append(
                    (
                        f"audit://{uuid4().hex}",
                        project_id,
                        reviewer,
                        "relation.created",
                        "relation",
                        relation_id,
                        json.dumps(
                            {"predicate": predicate, "binding_ids": binding_ids},
                            ensure_ascii=False,
                        ),
                        uuid4().hex,
                        now,
                    )
                )
            db.executemany(
                """UPDATE platform_edges
                   SET derivation='human_confirmed', confidence=?, review_status='confirmed',
                       metadata_json=?, reviewed_by=?, reviewed_at=?, review_note=?, updated_at=?
                   WHERE id=?""",
                relation_updates,
            )
            db.executemany(
                """INSERT INTO platform_edges
                   (id, project_id, source_entity_id, predicate, target_entity_id,
                    evidence_entity_id, derivation, confidence, review_status,
                    valid_from, valid_to, rule_version, metadata_json,
                    reviewed_by, reviewed_at, review_note, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                relation_rows,
            )
            db.executemany(
                """INSERT INTO audit_events
                   (id, project_id, actor, action, resource_type, resource_id,
                    detail_json, trace_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                audit_rows,
            )
            placeholders = ",".join("?" for _ in candidate_ids)
            cursor = db.execute(
                f"""UPDATE binding_candidates
                    SET review_status='confirmed', reviewed_by=?, reviewed_at=?,
                        review_note=?, updated_at=?
                    WHERE project_id=? AND review_status='unreviewed'
                      AND id IN ({placeholders})""",
                (reviewer, now, note, now, project_id, *candidate_ids),
            )
            if cursor.rowcount != len(candidate_ids):
                raise ValueError("pending relation queue changed; no bulk decisions were saved")
            db.execute(
                """INSERT INTO audit_events
                   (id, project_id, actor, action, resource_type, resource_id,
                    detail_json, trace_id, created_at)
                   VALUES (?, ?, ?, 'binding.reviewed.bulk', 'binding_queue', ?, ?, ?, ?)""",
                (
                    f"audit://{uuid4().hex}",
                    project_id,
                    reviewer,
                    project_id,
                    json.dumps(
                        {"decision": "confirmed", "reviewed": len(candidate_ids)},
                        ensure_ascii=False,
                    ),
                    uuid4().hex,
                    now,
                ),
            )

        return {
            "project_id": project_id,
            "decision": "confirmed",
            "reviewed": len(candidate_ids),
            "relations_created": len(relation_rows),
            "remaining_pending": 0,
        }

    def stats(self, project_id: str) -> dict[str, int]:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT count(*) AS total,
                          sum(CASE WHEN review_status='unreviewed' THEN 1 ELSE 0 END) AS pending,
                          sum(CASE WHEN review_status='confirmed' THEN 1 ELSE 0 END) AS confirmed,
                          sum(CASE WHEN review_status='rejected' THEN 1 ELSE 0 END) AS rejected,
                          sum(CASE WHEN review_status='unreviewed' AND confidence>=0.9
                                   THEN 1 ELSE 0 END) AS high_confidence
                   FROM binding_candidates WHERE project_id=?""",
                (project_id,),
            ).fetchone()
        return {key: int(value or 0) for key, value in dict(row).items()}
