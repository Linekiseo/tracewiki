from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..storage import SQLiteStore, utc_now
from .schema import WORKSPACE_SCHEMA


def _decode(row: sqlite3.Row | None, *json_fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in json_fields:
        value = item.pop(f"{field}_json", None)
        item[field] = json.loads(value or "{}")
    return item


class WorkspaceStore:
    """Persistence boundary for planning entities and cross-source relations."""

    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(WORKSPACE_SCHEMA)

    def create_project(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO projects
                   (id, name, description, owner, acl_ref, classification, status,
                    settings_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["name"],
                    record["description"],
                    record["owner"],
                    record["acl_ref"],
                    record["classification"],
                    record.get("status", "active"),
                    json.dumps(record.get("settings", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_project(record["id"]) or {}

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return _decode(row, "settings")

    def list_projects(self) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT p.*,
                          (SELECT count(*) FROM research_topics t
                           WHERE t.project_id=p.id AND t.status!='archived') AS topic_count,
                          (SELECT count(*) FROM research_iterations i
                           WHERE i.project_id=p.id AND i.status IN ('active','validating'))
                              AS active_iteration_count,
                          (SELECT count(*) FROM research_work_items w
                           WHERE w.project_id=p.id
                             AND w.status IN ('ready','running','review','blocked'))
                              AS active_work_item_count,
                          (SELECT t.title FROM research_topics t
                           WHERE t.project_id=p.id AND t.status!='archived'
                           ORDER BY
                             CASE t.status WHEN 'active' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END,
                             t.updated_at DESC
                           LIMIT 1) AS current_topic,
                          (SELECT count(*) FROM repositories r
                           WHERE r.project_id=p.id) AS repositories,
                          (SELECT count(*) FROM codex_threads t
                           JOIN codex_sources s ON s.id=t.source_id
                           WHERE t.project_id=p.id
                             AND t.generation_id=s.active_generation_id) AS sessions,
                          (SELECT count(*) FROM experiments e
                           WHERE e.project_id=p.id AND e.status!='archived') AS experiments,
                          (SELECT count(*) FROM scientific_documents d
                           WHERE d.project_id=p.id) AS documents,
                          (SELECT count(*) FROM platform_edges pe
                           WHERE pe.project_id=p.id AND pe.review_status='unreviewed')
                              AS pending_reviews,
                          (SELECT max(activity_at) FROM (
                             SELECT a.created_at AS activity_at
                               FROM audit_events a WHERE a.project_id=p.id
                             UNION ALL
                             SELECT t.updated_at AS activity_at
                               FROM research_topics t WHERE t.project_id=p.id
                             UNION ALL
                             SELECT i.updated_at AS activity_at
                               FROM research_iterations i WHERE i.project_id=p.id
                             UNION ALL
                             SELECT w.updated_at AS activity_at
                               FROM research_work_items w WHERE w.project_id=p.id
                          )) AS last_activity_at
                   FROM projects p ORDER BY p.updated_at DESC"""
            ).fetchall()
        return [_decode(row, "settings") or {} for row in rows]

    def update_project(self, project_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        self._update("projects", project_id, changes, {"settings"})
        return self.get_project(project_id)

    def create_topic(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO research_topics
                   (id, display_key, project_id, title, problem_statement, objective,
                    status, priority, owner, tags_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record["title"],
                    record["problem_statement"],
                    record["objective"],
                    record["status"],
                    record["priority"],
                    record["owner"],
                    json.dumps(record.get("tags", []), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_topic(record["id"]) or {}

    def get_topic(self, topic_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT t.*,
                          (SELECT count(*) FROM research_iterations i
                           WHERE i.topic_id=t.id) AS iteration_count
                   FROM research_topics t WHERE t.id=?""",
                (topic_id,),
            ).fetchone()
        return _decode(row, "tags")

    def list_topics(
        self, project_id: str, *, status: str | None = None, query: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["t.project_id=?"]
        values: list[Any] = [project_id]
        if status:
            clauses.append("t.status=?")
            values.append(status)
        else:
            clauses.append("t.status!='archived'")
        if query:
            clauses.append("(t.title LIKE ? OR t.problem_statement LIKE ? OR t.objective LIKE ?)")
            term = f"%{query}%"
            values.extend([term, term, term])
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT t.*,
                           (SELECT count(*) FROM research_iterations i
                            WHERE i.topic_id=t.id) AS iteration_count,
                           (SELECT count(*) FROM research_iterations i
                            WHERE i.topic_id=t.id AND i.status IN ('active','validating'))
                               AS active_iteration_count
                    FROM research_topics t
                    WHERE {" AND ".join(clauses)}
                    ORDER BY t.priority ASC, t.updated_at DESC""",
                values,
            ).fetchall()
        return [_decode(row, "tags") or {} for row in rows]

    def update_topic(self, topic_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        self._update("research_topics", topic_id, changes, {"tags"})
        return self.get_topic(topic_id)

    def create_iteration(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO research_iterations
                   (id, display_key, project_id, topic_id, title, goal, hypothesis,
                    status, progress, starts_at, target_at, completed_at, summary,
                    owner, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record["topic_id"],
                    record["title"],
                    record["goal"],
                    record["hypothesis"],
                    record["status"],
                    record.get("progress", 0),
                    record.get("starts_at"),
                    record.get("target_at"),
                    record.get("completed_at"),
                    record.get("summary", ""),
                    record["owner"],
                    now,
                    now,
                ),
            )
        return self.get_iteration(record["id"]) or {}

    def get_iteration(self, iteration_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT i.*, t.title AS topic_title, t.display_key AS topic_key,
                          (SELECT count(*) FROM iteration_links l
                           WHERE l.iteration_id=i.id) AS evidence_count
                   FROM research_iterations i
                   JOIN research_topics t ON t.id=i.topic_id
                   WHERE i.id=?""",
                (iteration_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_iterations(
        self,
        project_id: str,
        *,
        topic_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["i.project_id=?"]
        values: list[Any] = [project_id]
        if topic_id:
            clauses.append("i.topic_id=?")
            values.append(topic_id)
        if status:
            clauses.append("i.status=?")
            values.append(status)
        else:
            clauses.append("i.status!='archived'")
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT i.*, t.title AS topic_title, t.display_key AS topic_key,
                           (SELECT count(*) FROM iteration_links l
                            WHERE l.iteration_id=i.id) AS evidence_count
                    FROM research_iterations i
                    JOIN research_topics t ON t.id=i.topic_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY CASE i.status
                      WHEN 'active' THEN 0 WHEN 'validating' THEN 1
                      WHEN 'planned' THEN 2 WHEN 'blocked' THEN 3 ELSE 4 END,
                      i.updated_at DESC""",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def update_iteration(self, iteration_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        self._update("research_iterations", iteration_id, changes, set())
        return self.get_iteration(iteration_id)

    def create_work_item(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO research_work_items
                   (id, display_key, project_id, topic_id, iteration_id, repository_id,
                    title, objective, acceptance_criteria_json, kind, status, priority,
                    assignee_type, assignee, workspace_path, base_ref, due_at, summary,
                    version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record.get("topic_id"),
                    record.get("iteration_id"),
                    record.get("repository_id"),
                    record["title"],
                    record.get("objective", ""),
                    json.dumps(record.get("acceptance_criteria", []), ensure_ascii=False),
                    record["kind"],
                    record["status"],
                    record["priority"],
                    record["assignee_type"],
                    record["assignee"],
                    record.get("workspace_path"),
                    record.get("base_ref"),
                    record.get("due_at"),
                    record.get("summary", ""),
                    now,
                    now,
                ),
            )
        return self.get_work_item(record["id"]) or {}

    def get_work_item(self, work_item_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT w.*, t.title AS topic_title, t.display_key AS topic_key,
                          i.title AS iteration_title, i.display_key AS iteration_key,
                          r.name AS repository_name
                   FROM research_work_items w
                   LEFT JOIN research_topics t ON t.id=w.topic_id
                   LEFT JOIN research_iterations i ON i.id=w.iteration_id
                   LEFT JOIN repositories r ON r.id=w.repository_id
                   WHERE w.id=?""",
                (work_item_id,),
            ).fetchone()
        return _decode(row, "acceptance_criteria")

    def list_work_items(
        self,
        project_id: str,
        *,
        topic_id: str | None = None,
        iteration_id: str | None = None,
        status: str | None = None,
        assignee_type: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses = ["w.project_id=?"]
        values: list[Any] = [project_id]
        for column, value in (
            ("w.topic_id", topic_id),
            ("w.iteration_id", iteration_id),
            ("w.status", status),
            ("w.assignee_type", assignee_type),
        ):
            if value:
                clauses.append(f"{column}=?")
                values.append(value)
        if not status:
            clauses.append("w.status!='cancelled'")
        values.append(limit)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT w.*, t.title AS topic_title, t.display_key AS topic_key,
                           i.title AS iteration_title, i.display_key AS iteration_key,
                           r.name AS repository_name
                    FROM research_work_items w
                    LEFT JOIN research_topics t ON t.id=w.topic_id
                    LEFT JOIN research_iterations i ON i.id=w.iteration_id
                    LEFT JOIN repositories r ON r.id=w.repository_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY CASE w.status
                      WHEN 'running' THEN 0 WHEN 'review' THEN 1 WHEN 'ready' THEN 2
                      WHEN 'blocked' THEN 3 WHEN 'backlog' THEN 4 ELSE 5 END,
                      w.priority ASC, w.updated_at DESC
                    LIMIT ?""",
                values,
            ).fetchall()
        return [_decode(row, "acceptance_criteria") or {} for row in rows]

    def update_work_item(
        self, work_item_id: str, expected_version: int, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        allowed = {
            "topic_id",
            "iteration_id",
            "repository_id",
            "title",
            "objective",
            "acceptance_criteria",
            "kind",
            "status",
            "priority",
            "assignee_type",
            "assignee",
            "workspace_path",
            "base_ref",
            "due_at",
            "summary",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            column = "acceptance_criteria_json" if key == "acceptance_criteria" else key
            assignments.append(f"{column}=?")
            values.append(
                json.dumps(value, ensure_ascii=False) if key == "acceptance_criteria" else value
            )
        if not assignments:
            return self.get_work_item(work_item_id)
        assignments.extend(["version=version+1", "updated_at=?"])
        values.extend([utc_now(), work_item_id, expected_version])
        with self.database.transaction() as db:
            result = db.execute(
                f"""UPDATE research_work_items SET {", ".join(assignments)}
                    WHERE id=? AND version=?""",
                values,
            )
        if result.rowcount == 0:
            return None
        return self.get_work_item(work_item_id)

    def create_iteration_link(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO iteration_links
                   (id, project_id, iteration_id, entity_id, entity_type, source_type,
                    role, status, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(iteration_id, entity_id, role) DO UPDATE SET
                     entity_type=excluded.entity_type,
                     source_type=excluded.source_type,
                     status=excluded.status,
                     metadata_json=excluded.metadata_json""",
                (
                    record["id"],
                    record["project_id"],
                    record["iteration_id"],
                    record["entity_id"],
                    record["entity_type"],
                    record["source_type"],
                    record["role"],
                    record["status"],
                    json.dumps(record.get("metadata", {}), ensure_ascii=False),
                    now,
                ),
            )
            row = db.execute(
                """SELECT * FROM iteration_links
                   WHERE iteration_id=? AND entity_id=? AND role=?""",
                (record["iteration_id"], record["entity_id"], record["role"]),
            ).fetchone()
        return _decode(row, "metadata") or {}

    def list_iteration_links(self, iteration_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM iteration_links WHERE iteration_id=?
                   ORDER BY created_at DESC""",
                (iteration_id,),
            ).fetchall()
        return [_decode(row, "metadata") or {} for row in rows]

    def delete_iteration_link(self, link_id: str) -> bool:
        with self.database.transaction() as db:
            result = db.execute("DELETE FROM iteration_links WHERE id=?", (link_id,))
        return result.rowcount > 0

    def entity_exists(self, entity_id: str) -> bool:
        checks = (
            ("SELECT 1 FROM projects WHERE id=?",),
            ("SELECT 1 FROM research_topics WHERE id=?",),
            ("SELECT 1 FROM research_iterations WHERE id=?",),
            ("SELECT 1 FROM research_work_items WHERE id=?",),
            (
                """SELECT 1 FROM entities e JOIN repositories r ON r.id=e.repository_id
                   WHERE e.id=? AND e.generation_id=r.active_generation_id""",
            ),
            (
                """SELECT 1 FROM codex_threads t JOIN codex_sources s ON s.id=t.source_id
                   WHERE t.id=? AND t.generation_id=s.active_generation_id""",
            ),
            (
                """SELECT 1 FROM codex_turns t JOIN codex_sources s ON s.id=t.source_id
                   WHERE t.id=? AND t.generation_id=s.active_generation_id""",
            ),
            (
                """SELECT 1 FROM codex_items i JOIN codex_sources s ON s.id=i.source_id
                   WHERE i.id=? AND i.generation_id=s.active_generation_id""",
            ),
            ("SELECT 1 FROM experiments WHERE id=?",),
            ("SELECT 1 FROM experiment_runs WHERE id=?",),
            ("SELECT 1 FROM run_metrics WHERE id=?",),
            ("SELECT 1 FROM run_artifacts WHERE id=?",),
            ("SELECT 1 FROM scientific_documents WHERE id=?",),
            ("SELECT 1 FROM document_sections WHERE id=?",),
            ("SELECT 1 FROM document_pages WHERE id=?",),
            ("SELECT 1 FROM document_tables WHERE id=?",),
            ("SELECT 1 FROM document_table_cells WHERE id=?",),
            ("SELECT 1 FROM table_metric_aggregations WHERE id=?",),
            ("SELECT 1 FROM document_figures WHERE id=?",),
            ("SELECT 1 FROM document_citations WHERE id=?",),
            ("SELECT 1 FROM claims WHERE id=?",),
            ("SELECT 1 FROM git_commits WHERE id=?",),
            ("SELECT 1 FROM diff_hunks WHERE id=?",),
            ("SELECT 1 FROM test_results WHERE id=?",),
            ("SELECT 1 FROM notebook_templates WHERE id=?",),
            ("SELECT 1 FROM notebook_runs WHERE id=?",),
            ("SELECT 1 FROM notebook_cells WHERE id=?",),
            ("SELECT 1 FROM notebook_outputs WHERE id=?",),
        )
        with self.database.connection() as db:
            return any(db.execute(sql[0], (entity_id,)).fetchone() for sql in checks)

    def create_relation(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO platform_edges
                   (id, project_id, source_entity_id, predicate, target_entity_id,
                    evidence_entity_id, derivation, confidence, review_status,
                    valid_from, valid_to, rule_version, metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["project_id"],
                    record["source_entity_id"],
                    record["predicate"],
                    record["target_entity_id"],
                    record.get("evidence_entity_id"),
                    record["derivation"],
                    record["confidence"],
                    record["review_status"],
                    record.get("valid_from", now),
                    record.get("valid_to"),
                    record.get("rule_version"),
                    json.dumps(record.get("metadata", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_relation(record["id"]) or {}

    def get_relation(self, edge_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM platform_edges WHERE id=?", (edge_id,)).fetchone()
        return _decode(row, "metadata")

    def list_relations(
        self,
        project_id: str,
        *,
        entity_id: str | None = None,
        review_status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id=?"]
        values: list[Any] = [project_id]
        if entity_id:
            clauses.append("(source_entity_id=? OR target_entity_id=?)")
            values.extend([entity_id, entity_id])
        if review_status:
            clauses.append("review_status=?")
            values.append(review_status)
        values.append(limit)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT * FROM platform_edges WHERE {" AND ".join(clauses)}
                    ORDER BY updated_at DESC LIMIT ?""",
                values,
            ).fetchall()
        return [_decode(row, "metadata") or {} for row in rows]

    def review_relation(
        self, edge_id: str, review_status: str, reviewer: str, note: str
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """UPDATE platform_edges SET review_status=?, reviewed_by=?,
                   reviewed_at=?, review_note=?, updated_at=? WHERE id=?""",
                (review_status, reviewer, now, note, now, edge_id),
            )
        return self.get_relation(edge_id)

    def create_audit(self, record: dict[str, Any]) -> None:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO audit_events
                   (id, project_id, actor, action, resource_type, resource_id,
                    detail_json, trace_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record.get("project_id"),
                    record["actor"],
                    record["action"],
                    record["resource_type"],
                    record["resource_id"],
                    json.dumps(record.get("detail", {}), ensure_ascii=False),
                    record.get("trace_id"),
                    utc_now(),
                ),
            )

    def list_audits(self, project_id: str | None, limit: int = 100) -> list[dict[str, Any]]:
        where = "WHERE project_id=?" if project_id else ""
        params: list[Any] = [project_id] if project_id else []
        params.append(limit)
        with self.database.connection() as db:
            rows = db.execute(
                f"SELECT * FROM audit_events {where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [_decode(row, "detail") or {} for row in rows]

    def list_query_history(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return the persisted, already-sanitized query interaction audit projection."""

        with self.database.connection() as db:
            rows = db.execute(
                """SELECT id, detail_json, trace_id, created_at
                     FROM audit_events
                    WHERE project_id=? AND resource_type='query_interaction'
                    ORDER BY created_at DESC LIMIT ?""",
                (project_id, limit),
            ).fetchall()
        return [_decode(row, "detail") or {} for row in rows]

    def project_source_stats(self, project_id: str) -> dict[str, int]:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT
                     (SELECT count(*) FROM repositories WHERE project_id=?) AS repositories,
                     (SELECT count(*) FROM codex_threads t JOIN codex_sources s ON s.id=t.source_id
                      WHERE t.project_id=? AND t.generation_id=s.active_generation_id) AS sessions,
                     (SELECT count(*) FROM experiments
                      WHERE project_id=? AND status!='archived') AS experiments,
                     (SELECT count(*) FROM scientific_documents
                      WHERE project_id=?) AS documents,
                     (SELECT count(*) FROM platform_edges WHERE project_id=?) AS relations,
                     (SELECT count(*) FROM platform_edges
                      WHERE project_id=? AND review_status='unreviewed') AS pending_reviews""",
                (
                    project_id,
                    project_id,
                    project_id,
                    project_id,
                    project_id,
                    project_id,
                ),
            ).fetchone()
        return {key: int(value or 0) for key, value in dict(row).items()}

    def _update(
        self, table: str, record_id: str, changes: dict[str, Any], json_fields: set[str]
    ) -> None:
        if not changes:
            return
        fields: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            column = f"{key}_json" if key in json_fields else key
            fields.append(f"{column}=?")
            values.append(json.dumps(value, ensure_ascii=False) if key in json_fields else value)
        fields.append("updated_at=?")
        values.extend([utc_now(), record_id])
        with self.database.transaction() as db:
            db.execute(f"UPDATE {table} SET {', '.join(fields)} WHERE id=?", values)
