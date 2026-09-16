from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .codex_store import CodexStoreMixin
from .db_schema import BASE_SCHEMA
from .models import (
    EdgeRecord,
    EntityRecord,
    SearchScope,
    SearchViewRecord,
)
from .rag.sources.code.schema import CODE_V2_SCHEMA
from .rag.sources.code.store import CodeV2StoreMixin


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteStore(CodeV2StoreMixin, CodexStoreMixin):
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def connection(self) -> Iterable[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterable[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            # Journal mode is persistent database metadata. Reasserting it on every
            # request turns otherwise read-only connections into schema-lock
            # contenders and can starve the session UI under concurrent polling.
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(BASE_SCHEMA)
            db.executescript(CODE_V2_SCHEMA)

    def create_workflow(self, workflow_id: str, source: str, request: dict[str, Any]) -> None:
        now = utc_now()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO workflows
                   (id, source, status, stage, progress, request_json, created_at, updated_at)
                   VALUES (?, ?, 'queued', 'discover', 0, ?, ?, ?)""",
                (workflow_id, source, json.dumps(request, ensure_ascii=False), now, now),
            )

    def update_workflow(
        self,
        workflow_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        repository_id: str | None = None,
        generation_id: str | None = None,
        counters: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [utc_now()]
        for column, value in (
            ("status", status),
            ("stage", stage),
            ("progress", progress),
            ("repository_id", repository_id),
            ("generation_id", generation_id),
            ("error", error),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if counters is not None:
            fields.append("counters_json = ?")
            values.append(json.dumps(counters, ensure_ascii=False))
        values.append(workflow_id)
        with self.transaction() as db:
            db.execute(f"UPDATE workflows SET {', '.join(fields)} WHERE id = ?", values)

    def prepare_generation(
        self,
        *,
        generation_id: str,
        repository: dict[str, Any],
        parser_versions: dict[str, str],
    ) -> None:
        now = utc_now()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO repositories
                   (id, project_id, name, source_type, source_url, local_path,
                    default_branch, head_commit, acl_ref, status, stats_json,
                    created_at, updated_at, last_error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexing', '{}', ?, ?, NULL)
                   ON CONFLICT(id) DO UPDATE SET
                     project_id=excluded.project_id,
                     name=excluded.name,
                     source_type=excluded.source_type,
                     source_url=excluded.source_url,
                     local_path=excluded.local_path,
                     default_branch=excluded.default_branch,
                     head_commit=excluded.head_commit,
                     acl_ref=excluded.acl_ref,
                     status='indexing',
                     updated_at=excluded.updated_at,
                     last_error=NULL""",
                (
                    repository["id"],
                    repository["project_id"],
                    repository["name"],
                    repository["source_type"],
                    repository.get("source_url"),
                    repository["local_path"],
                    repository.get("default_branch"),
                    repository["head_commit"],
                    repository["acl_ref"],
                    now,
                    now,
                ),
            )
            db.execute(
                """INSERT INTO index_generations
                   (id, repository_id, commit_sha, status, parser_versions_json, started_at)
                   VALUES (?, ?, ?, 'building', ?, ?)""",
                (
                    generation_id,
                    repository["id"],
                    repository["head_commit"],
                    json.dumps(parser_versions, ensure_ascii=False),
                    now,
                ),
            )

    def publish_generation(
        self,
        *,
        repository_id: str,
        generation_id: str,
        entities: Sequence[EntityRecord],
        edges: Sequence[EdgeRecord],
        views: Sequence[SearchViewRecord],
        counts: dict[str, int],
        validation: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self.transaction() as db:
            db.executemany(
                """INSERT INTO entities
                   (id, repository_id, generation_id, project_id, entity_type, name,
                    qualified_name, path, language, commit_sha, blob_hash, content_hash,
                    start_line, end_line, source_uri, acl_ref, content, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.id,
                        item.repository_id,
                        item.generation_id,
                        item.project_id,
                        item.entity_type,
                        item.name,
                        item.qualified_name,
                        item.path,
                        item.language,
                        item.commit_sha,
                        item.blob_hash,
                        item.content_hash,
                        item.start_line,
                        item.end_line,
                        item.source_uri,
                        item.acl_ref,
                        item.content,
                        json.dumps(item.metadata, ensure_ascii=False),
                    )
                    for item in entities
                ],
            )
            db.executemany(
                """INSERT INTO edges
                   (id, repository_id, generation_id, source_id, target_id, edge_type,
                    derivation, confidence, evidence_locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.id,
                        item.repository_id,
                        item.generation_id,
                        item.source_id,
                        item.target_id,
                        item.edge_type,
                        item.derivation,
                        item.confidence,
                        item.evidence_locator,
                    )
                    for item in edges
                ],
            )
            db.executemany(
                """INSERT INTO search_views
                   (id, entity_id, repository_id, generation_id, project_id, view_type,
                    name, path, language, content, vector, embedding_model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.id,
                        item.entity_id,
                        item.repository_id,
                        item.generation_id,
                        item.project_id,
                        item.view_type,
                        item.name,
                        item.path,
                        item.language,
                        item.content,
                        item.vector,
                        item.embedding_model,
                    )
                    for item in views
                ],
            )
            db.execute(
                """UPDATE index_generations
                   SET status='published', counts_json=?, validation_json=?, completed_at=?
                   WHERE id=?""",
                (
                    json.dumps(counts, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False),
                    now,
                    generation_id,
                ),
            )
            db.execute(
                """UPDATE repositories
                   SET active_generation_id=?, status='ready', stats_json=?, updated_at=?,
                       last_error=NULL
                   WHERE id=?""",
                (generation_id, json.dumps(counts, ensure_ascii=False), now, repository_id),
            )

    def fail_generation(
        self, generation_id: str | None, repository_id: str | None, error: str
    ) -> None:
        now = utc_now()
        with self.transaction() as db:
            if generation_id:
                db.execute(
                    "UPDATE index_generations SET status='failed', error=?, completed_at=? WHERE id=?",
                    (error, now, generation_id),
                )
            if repository_id:
                db.execute(
                    "UPDATE repositories SET status='failed', last_error=?, updated_at=? WHERE id=?",
                    (error, now, repository_id),
                )

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        return self._workflow_dict(row) if row else None

    def list_workflows(self, limit: int = 20, *, kind: str | None = None) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM workflows ORDER BY created_at DESC LIMIT ?",
                (limit if kind is None else max(limit * 5, 100),),
            ).fetchall()
        items = [self._workflow_dict(row) for row in rows]
        if kind is not None:
            items = [item for item in items if item["kind"] == kind]
        return items[:limit]

    def _workflow_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["counters"] = json.loads(result.pop("counters_json"))
        result["kind"] = (
            "codex"
            if result["id"].startswith("wf-codex-") or result["source"].startswith("codex:")
            else "repository"
        )
        return result

    def recover_interrupted_workflows(self) -> int:
        """Close workflows orphaned by a previous process before accepting new work."""
        now = utc_now()
        with self.transaction() as db:
            interrupted = db.execute(
                """SELECT repository_id, generation_id FROM workflows
                   WHERE status IN ('queued', 'running')"""
            ).fetchall()
            building_generations = db.execute(
                """SELECT repository_id, id AS generation_id FROM index_generations
                   WHERE status='building'"""
            ).fetchall()
            cursor = db.execute(
                """UPDATE workflows
                   SET status='failed',
                       stage='interrupted',
                       error=coalesce(error, 'Service restarted before the workflow completed'),
                       updated_at=?
                   WHERE status IN ('queued', 'running')""",
                (now,),
            )
            affected = {
                (item["repository_id"], item["generation_id"])
                for item in [*interrupted, *building_generations]
            }
            for repository_id, generation_id in affected:
                if generation_id:
                    db.execute(
                        """UPDATE index_generations
                           SET status='failed', error=?, completed_at=?
                           WHERE id=? AND status='building'""",
                        ("Service restarted before the workflow completed", now, generation_id),
                    )
                if repository_id and not repository_id.startswith("codex-source://"):
                    db.execute(
                        """UPDATE repositories
                           SET status=CASE
                                 WHEN active_generation_id IS NULL THEN 'failed'
                                 ELSE 'ready'
                               END,
                               last_error=?,
                               updated_at=?
                           WHERE id=?""",
                        ("Service restarted before the workflow completed", now, repository_id),
                    )
        return int(cursor.rowcount)

    def list_repositories(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM repositories ORDER BY updated_at DESC").fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["stats"] = json.loads(item.pop("stats_json"))
            results.append(item)
        return results

    def delete_repository(self, repository_id: str) -> dict[str, Any] | None:
        """Remove one repository and repository-owned derived data atomically.

        Research tasks and experiment runs are retained and detached from the
        deleted repository. Cross-source evidence links that point at deleted
        code entities are removed so later graph reads cannot return stale
        nodes.
        """

        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM repositories WHERE id=?",
                (repository_id,),
            ).fetchone()
            if row is None:
                return None
            active = db.execute(
                """SELECT count(*) FROM workflows
                   WHERE repository_id=? AND status IN ('queued', 'running')""",
                (repository_id,),
            ).fetchone()[0]
            if active:
                raise ValueError("repository has an active ingestion workflow")

            db.execute("CREATE TEMP TABLE _deleted_repository_entities(id TEXT PRIMARY KEY)")
            db.execute(
                """INSERT OR IGNORE INTO _deleted_repository_entities(id)
                   SELECT id FROM entities WHERE repository_id=?""",
                (repository_id,),
            )
            db.execute("CREATE TEMP TABLE _deleted_repository_raw(id TEXT PRIMARY KEY)")
            db.execute(
                """INSERT OR IGNORE INTO _deleted_repository_raw(id)
                   SELECT id FROM raw_objects
                   WHERE source_instance=? OR source_object_id=?""",
                (repository_id, repository_id),
            )

            removed: dict[str, int] = {}

            def execute(name: str, sql: str, values: Sequence[Any] = ()) -> None:
                cursor = db.execute(sql, values)
                removed[name] = max(0, int(cursor.rowcount))

            execute(
                "claim_evidence",
                """DELETE FROM claim_evidence
                   WHERE evidence_entity_id IN (
                     SELECT id FROM _deleted_repository_entities
                   )""",
            )
            execute(
                "iteration_links",
                """DELETE FROM iteration_links
                   WHERE entity_id IN (SELECT id FROM _deleted_repository_entities)""",
            )
            execute(
                "platform_edges",
                """DELETE FROM platform_edges
                   WHERE source_entity_id IN (SELECT id FROM _deleted_repository_entities)
                      OR target_entity_id IN (SELECT id FROM _deleted_repository_entities)
                      OR evidence_entity_id IN (SELECT id FROM _deleted_repository_entities)""",
            )
            execute(
                "binding_candidates",
                "DELETE FROM binding_candidates WHERE repository_id=?",
                (repository_id,),
            )
            execute(
                "unmatched_changes",
                "DELETE FROM unmatched_changes WHERE repository_id=?",
                (repository_id,),
            )
            execute(
                "drift_assessments",
                "DELETE FROM drift_assessments WHERE repository_id=?",
                (repository_id,),
            )
            execute(
                "research_work_items",
                "UPDATE research_work_items SET repository_id=NULL WHERE repository_id=?",
                (repository_id,),
            )
            execute(
                "experiment_runs",
                """UPDATE experiment_runs
                   SET repository_id=NULL, commit_entity_id=NULL
                   WHERE repository_id=?""",
                (repository_id,),
            )
            execute(
                "diff_hunks",
                "DELETE FROM diff_hunks WHERE repository_id=?",
                (repository_id,),
            )
            execute(
                "test_results",
                "DELETE FROM test_results WHERE repository_id=?",
                (repository_id,),
            )
            execute(
                "git_branches",
                "DELETE FROM git_branches WHERE repository_id=?",
                (repository_id,),
            )
            execute(
                "git_commits",
                "DELETE FROM git_commits WHERE repository_id=?",
                (repository_id,),
            )
            execute(
                "blocked_entities",
                """DELETE FROM blocked_entities
                   WHERE raw_object_id IN (SELECT id FROM _deleted_repository_raw)
                      OR entity_id IN (SELECT id FROM _deleted_repository_entities)""",
            )
            execute(
                "raw_derivations",
                """DELETE FROM raw_derivations
                   WHERE raw_object_id IN (SELECT id FROM _deleted_repository_raw)
                      OR generation_id IN (
                        SELECT id FROM index_generations WHERE repository_id=?
                      )""",
                (repository_id,),
            )
            execute(
                "source_events",
                """DELETE FROM source_events
                   WHERE source_instance=?
                      OR raw_object_id IN (SELECT id FROM _deleted_repository_raw)""",
                (repository_id,),
            )
            execute(
                "raw_objects",
                "DELETE FROM raw_objects WHERE id IN (SELECT id FROM _deleted_repository_raw)",
            )
            execute(
                "search_views",
                "DELETE FROM search_views WHERE repository_id=?",
                (repository_id,),
            )
            execute("edges", "DELETE FROM edges WHERE repository_id=?", (repository_id,))
            execute("entities", "DELETE FROM entities WHERE repository_id=?", (repository_id,))
            execute(
                "index_generations",
                "DELETE FROM index_generations WHERE repository_id=?",
                (repository_id,),
            )
            execute("workflows", "DELETE FROM workflows WHERE repository_id=?", (repository_id,))
            execute("repositories", "DELETE FROM repositories WHERE id=?", (repository_id,))

        item = dict(row)
        item["stats"] = json.loads(item.pop("stats_json"))
        item["removed"] = removed
        item["deleted"] = True
        return item

    def stats(self) -> dict[str, Any]:
        with self.connection() as db:
            repositories = db.execute("SELECT count(*) FROM repositories").fetchone()[0]
            active = db.execute(
                "SELECT count(*) FROM repositories WHERE status='ready'"
            ).fetchone()[0]
            failed = db.execute("SELECT count(*) FROM workflows WHERE status='failed'").fetchone()[
                0
            ]
            counts = db.execute(
                """SELECT
                     coalesce(sum(json_extract(stats_json, '$.files')), 0) AS files,
                     coalesce(sum(json_extract(stats_json, '$.symbols')), 0) AS symbols,
                     coalesce(sum(json_extract(stats_json, '$.edges')), 0) AS edges
                   FROM repositories WHERE status='ready'"""
            ).fetchone()
        return {
            "repositories": repositories,
            "active_repositories": active,
            "failed_workflows": failed,
            "files": counts["files"],
            "symbols": counts["symbols"],
            "edges": counts["edges"],
        }

    def _scope_sql(self, scope: SearchScope, alias: str = "sv") -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if scope.project_id:
            clauses.append(f"{alias}.project_id = ?")
            values.append(scope.project_id)
        if scope.repository_ids:
            marks = ",".join("?" for _ in scope.repository_ids)
            clauses.append(f"{alias}.repository_id IN ({marks})")
            values.extend(scope.repository_ids)
        if scope.languages:
            marks = ",".join("?" for _ in scope.languages)
            clauses.append(f"{alias}.language IN ({marks})")
            values.extend(scope.languages)
        if scope.entity_types:
            marks = ",".join("?" for _ in scope.entity_types)
            clauses.append(f"e.entity_type IN ({marks})")
            values.extend(scope.entity_types)
        if scope.commit:
            clauses.append("e.commit_sha = ?")
            values.append(scope.commit)
        if scope.enforce_acl:
            allowed = list(dict.fromkeys([*scope.allowed_acl_refs, "public"]))
            if allowed:
                marks = ",".join("?" for _ in allowed)
                clauses.append(f"e.acl_ref IN ({marks})")
                values.extend(allowed)
            else:
                clauses.append("0=1")
        return clauses, values

    def lexical_search(
        self, fts_query: str, scope: SearchScope, limit: int
    ) -> list[dict[str, Any]]:
        clauses, values = self._scope_sql(scope)
        where = " AND ".join(["search_views_fts MATCH ?", *clauses])
        params: list[Any] = [fts_query, *values, limit]
        sql = f"""
            SELECT sv.*, e.entity_type, e.qualified_name, e.commit_sha, e.start_line,
                   e.end_line, e.source_uri, e.acl_ref, e.metadata_json,
                   bm25(search_views_fts, 8.0, 3.0, 1.0) AS lexical_rank
            FROM search_views_fts
            JOIN search_views sv ON sv.rowid = search_views_fts.rowid
            JOIN repositories r ON r.id = sv.repository_id
            JOIN entities e ON e.id = sv.entity_id AND e.generation_id = sv.generation_id
            WHERE {where} AND sv.generation_id = r.active_generation_id
              AND NOT EXISTS (
                SELECT 1 FROM blocked_entities b WHERE b.entity_id=e.id
              )
            ORDER BY lexical_rank
            LIMIT ?
        """
        with self.connection() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._search_row(row) for row in rows]

    def dense_candidates(
        self, scope: SearchScope, limit: int | None = None
    ) -> list[dict[str, Any]]:
        clauses, values = self._scope_sql(scope)
        where = " AND ".join(["sv.generation_id = r.active_generation_id", *clauses])
        limit_sql = " LIMIT ?" if limit is not None else ""
        sql = f"""
            SELECT sv.*, e.entity_type, e.qualified_name, e.commit_sha, e.start_line,
                   e.end_line, e.source_uri, e.acl_ref, e.metadata_json
            FROM search_views sv
            JOIN repositories r ON r.id = sv.repository_id
            JOIN entities e ON e.id = sv.entity_id AND e.generation_id = sv.generation_id
            WHERE {where}
              AND NOT EXISTS (
                SELECT 1 FROM blocked_entities b WHERE b.entity_id=e.id
              )
            {limit_sql}
        """
        params = [*values, limit] if limit is not None else values
        with self.connection() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._search_row(row) for row in rows]

    def vectors_for_entities(self, entity_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Return active vector views for a bounded set of indexed entities."""

        if not entity_ids:
            return []
        marks = ",".join("?" for _ in entity_ids)
        with self.connection() as db:
            rows = db.execute(
                f"""SELECT sv.entity_id, sv.view_type, sv.vector, sv.embedding_model
                      FROM search_views sv
                      JOIN repositories r ON r.id=sv.repository_id
                     WHERE sv.generation_id=r.active_generation_id
                       AND sv.entity_id IN ({marks})
                       AND NOT EXISTS (
                         SELECT 1 FROM blocked_entities b WHERE b.entity_id=sv.entity_id
                       )
                     ORDER BY sv.entity_id, sv.view_type""",
                list(entity_ids),
            ).fetchall()
        return [dict(row) for row in rows]

    def _search_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def edges_for_entities(
        self, entity_ids: Sequence[str], repository_ids: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        if not entity_ids:
            return []
        entity_marks = ",".join("?" for _ in entity_ids)
        clauses = [
            "e.generation_id = r.active_generation_id",
            f"(e.source_id IN ({entity_marks}) OR e.target_id IN ({entity_marks}))",
        ]
        params: list[Any] = [*entity_ids, *entity_ids]
        if repository_ids:
            repo_marks = ",".join("?" for _ in repository_ids)
            clauses.append(f"e.repository_id IN ({repo_marks})")
            params.extend(repository_ids)
        with self.connection() as db:
            rows = db.execute(
                f"""SELECT e.* FROM edges e
                    JOIN repositories r ON r.id=e.repository_id
                    WHERE {" AND ".join(clauses)}
                    LIMIT 500""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_files(self, repository_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT e.id, e.path, e.language, e.content_hash, e.blob_hash,
                          e.commit_sha, e.metadata_json
                   FROM entities e JOIN repositories r ON r.id=e.repository_id
                   WHERE e.repository_id=? AND e.generation_id=r.active_generation_id
                         AND e.entity_type='FileVersion'
                         AND NOT EXISTS (
                           SELECT 1 FROM blocked_entities b WHERE b.entity_id=e.id
                         )
                   ORDER BY e.path""",
                (repository_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            results.append(item)
        return results

    def get_file(self, repository_id: str, path: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT e.* FROM entities e JOIN repositories r ON r.id=e.repository_id
                   WHERE e.repository_id=? AND e.path=?
                         AND e.generation_id=r.active_generation_id
                         AND e.entity_type='FileVersion'
                         AND NOT EXISTS (
                           SELECT 1 FROM blocked_entities b WHERE b.entity_id=e.id
                         )""",
                (repository_id, path),
            ).fetchone()
            if not row:
                return None
            symbols = db.execute(
                """SELECT id, name, qualified_name, entity_type, start_line, end_line,
                          source_uri, metadata_json
                   FROM entities
                   WHERE repository_id=? AND generation_id=? AND path=?
                         AND entity_type='CodeSymbol'
                   ORDER BY start_line""",
                (repository_id, row["generation_id"], path),
            ).fetchall()
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        result["symbols"] = []
        for symbol in symbols:
            item = dict(symbol)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result["symbols"].append(item)
        return result

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT e.* FROM entities e JOIN repositories r ON r.id=e.repository_id
                   WHERE e.id=? AND e.generation_id=r.active_generation_id
                     AND NOT EXISTS (
                       SELECT 1 FROM blocked_entities b WHERE b.entity_id=e.id
                     )
                   LIMIT 1""",
                (entity_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result
