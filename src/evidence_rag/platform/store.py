from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..embeddings import QUERY_EXPANSIONS
from ..storage import SQLiteStore, utc_now
from .schema import SCHEMA

SEARCH_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_.:/@+-]*|\d+(?:\.\d+)?|[\u3400-\u9fff]+",
    re.UNICODE,
)
SEARCH_STOPWORDS = {
    "about",
    "and",
    "current",
    "find",
    "how",
    "please",
    "the",
    "what",
    "which",
    "这个",
    "当前",
    "如何",
    "是否",
    "什么",
    "哪些",
    "相关",
    "进行",
    "需要",
}


class PlatformStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(SCHEMA)

    def resolve_scope(
        self,
        project_id: str,
        repository_ids: list[str],
        branch: str | None,
        commit: str | None,
        as_of: str | None,
    ) -> dict[str, Any]:
        clauses = ["project_id=?"]
        values: list[Any] = [project_id]
        if repository_ids:
            marks = ",".join("?" for _ in repository_ids)
            clauses.append(f"id IN ({marks})")
            values.extend(repository_ids)
        with self.database.connection() as db:
            repositories = [
                dict(row)
                for row in db.execute(
                    f"""SELECT id, name, default_branch, head_commit, active_generation_id,
                               acl_ref FROM repositories WHERE {" AND ".join(clauses)}""",
                    values,
                ).fetchall()
            ]
            resolved_commit = commit
            if not resolved_commit and len(repositories) == 1:
                repository = repositories[0]
                target_branch = branch or repository["default_branch"]
                if as_of:
                    row = db.execute(
                        """SELECT sha FROM git_commits
                           WHERE repository_id=? AND committed_at<=?
                           ORDER BY committed_at DESC LIMIT 1""",
                        (repository["id"], as_of),
                    ).fetchone()
                    resolved_commit = row["sha"] if row else None
                elif not branch and (
                    "+dirty." in str(repository["head_commit"])
                    or str(repository["head_commit"]).startswith("worktree-")
                ):
                    # The active Generation represents the local worktree, not merely its
                    # base branch.  Default current-code queries must not filter that snapshot
                    # out by resolving back to the clean branch head.
                    resolved_commit = repository["head_commit"]
                elif target_branch:
                    row = db.execute(
                        """SELECT head_sha FROM git_branches
                           WHERE repository_id=? AND name IN (?, ?)
                           ORDER BY is_default DESC LIMIT 1""",
                        (repository["id"], target_branch, f"origin/{target_branch}"),
                    ).fetchone()
                    resolved_commit = row["head_sha"] if row else repository["head_commit"]
                else:
                    resolved_commit = repository["head_commit"]
        return {
            "project_id": project_id,
            "repositories": repositories,
            "branch": branch
            or (repositories[0]["default_branch"] if len(repositories) == 1 else None),
            "commit": resolved_commit,
            "as_of": as_of or utc_now(),
        }

    def entity_scopes(self, entity_ids: list[str]) -> dict[str, dict[str, str | None]]:
        if not entity_ids:
            return {}
        marks = ",".join("?" for _ in entity_ids)
        scopes: dict[str, dict[str, str | None]] = {}
        queries = [
            (
                f"SELECT id, repository_id, NULL experiment_id, NULL document_id, NULL thread_id FROM entities WHERE id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT id, repository_id, NULL experiment_id, NULL document_id, NULL thread_id FROM git_commits WHERE id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT id, repository_id, NULL experiment_id, NULL document_id, NULL thread_id FROM diff_hunks WHERE id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT id, repository_id, NULL experiment_id, NULL document_id, NULL thread_id FROM test_results WHERE id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT id, NULL repository_id, id experiment_id, NULL document_id, NULL thread_id FROM experiments WHERE id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT id, repository_id, experiment_id, NULL document_id, NULL thread_id FROM experiment_runs WHERE id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT m.id, r.repository_id, r.experiment_id, NULL document_id, NULL thread_id FROM run_metrics m JOIN experiment_runs r ON r.id=m.run_id WHERE m.id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT a.id, r.repository_id, r.experiment_id, NULL document_id, NULL thread_id FROM run_artifacts a JOIN experiment_runs r ON r.id=a.run_id WHERE a.id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT id, NULL repository_id, NULL experiment_id, id document_id, NULL thread_id FROM scientific_documents WHERE id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT id, NULL repository_id, NULL experiment_id, document_id, NULL thread_id FROM claims WHERE id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT id, NULL repository_id, experiment_id, NULL document_id, run_id thread_id FROM notebook_runs WHERE id IN ({marks})",
                entity_ids,
            ),
            (
                f"SELECT i.id, NULL repository_id, NULL experiment_id, NULL document_id, t.thread_id FROM codex_items i JOIN codex_threads t ON t.id=i.thread_id AND t.generation_id=i.generation_id WHERE i.id IN ({marks})",
                entity_ids,
            ),
        ]
        with self.database.connection() as db:
            for sql, params in queries:
                for row in db.execute(sql, params).fetchall():
                    scopes[row["id"]] = dict(row)
        return scopes

    def structured_search(
        self,
        project_id: str,
        query: str,
        sources: list[str],
        limit: int,
        *,
        allowed_acl_refs: list[str] | None = None,
        enforce_acl: bool = False,
    ) -> list[dict[str, Any]]:
        allowed = set(allowed_acl_refs or ()) | {"public"}
        if enforce_acl:
            with self.database.connection() as db:
                project = db.execute(
                    "SELECT acl_ref FROM projects WHERE id=?", (project_id,)
                ).fetchone()
            if not project or (
                project["acl_ref"] != "public" and project["acl_ref"] not in allowed
            ):
                return []
        tokens = self._query_terms(query)
        if not tokens:
            return []

        def evidence_match(value: Any) -> int:
            text = str(value or "").casefold()
            return sum(token in text for token in tokens)

        rows: list[dict[str, Any]] = []
        with self.database.connection() as db:
            db.create_function("evidence_match", 1, evidence_match, deterministic=True)
            if not sources or "code" in sources:
                rows.extend(
                    dict(row)
                    for row in db.execute(
                        """SELECT c.id AS entity_id, 'code' AS source,
                                  'Commit' AS entity_type, substr(c.message,1,240) AS title,
                                  r.name || '@' || substr(c.sha,1,12) AS subtitle,
                                  c.message AS snippet, c.source_uri AS locator,
                                  'committed' AS status, c.committed_at AS updated_at,
                                  c.sha AS version
                           FROM git_commits c JOIN repositories r ON r.id=c.repository_id
                           WHERE c.project_id=? AND evidence_match(
                             coalesce(c.message,'') || ' ' || coalesce(c.sha,'')
                           ) > 0
                           UNION ALL
                           SELECT h.id, 'code', 'DiffHunk', h.path,
                                  r.name || '@' || substr(h.commit_sha,1,12),
                                  substr(h.patch,1,1200), h.source_locator, h.change_type,
                                  h.observed_at, h.commit_sha
                           FROM diff_hunks h JOIN repositories r ON r.id=h.repository_id
                           WHERE h.project_id=? AND evidence_match(
                             coalesce(h.path,'') || ' ' || coalesce(h.patch,'')
                           ) > 0
                           UNION ALL
                           SELECT t.id, 'code', 'TestResult', t.command,
                                  r.name || '@' || substr(t.commit_sha,1,12),
                                  t.command || ' · ' || t.status, t.id, t.status,
                                  t.observed_at, t.commit_sha
                           FROM test_results t JOIN repositories r ON r.id=t.repository_id
                           WHERE t.project_id=? AND evidence_match(
                             coalesce(t.command,'') || ' ' || coalesce(t.status,'')
                           ) > 0
                           LIMIT ?""",
                        (project_id, project_id, project_id, limit),
                    ).fetchall()
                )
            if not sources or "workspace" in sources:
                rows.extend(
                    dict(row)
                    for row in db.execute(
                        """SELECT id AS entity_id, 'workspace' AS source, 'ResearchTopic' AS entity_type,
                                  title, display_key AS subtitle,
                                  problem_statement || '\n' || objective AS snippet,
                                  display_key AS locator, status, updated_at, NULL AS version
                           FROM research_topics WHERE project_id=?
                             AND evidence_match(
                               coalesce(title,'') || ' ' || coalesce(problem_statement,'')
                               || ' ' || coalesce(objective,'')
                             ) > 0
                           UNION ALL
                           SELECT id, 'workspace', 'ResearchIteration', title, display_key,
                                  goal || '\n' || hypothesis, display_key, status, updated_at, NULL
                           FROM research_iterations WHERE project_id=?
                             AND evidence_match(
                               coalesce(title,'') || ' ' || coalesce(goal,'')
                               || ' ' || coalesce(hypothesis,'')
                             ) > 0
                           LIMIT ?""",
                        (project_id, project_id, limit),
                    ).fetchall()
                )
            if not sources or "experiment" in sources:
                rows.extend(
                    dict(row)
                    for row in db.execute(
                        """SELECT id AS entity_id, 'experiment' AS source,
                                  'Experiment' AS entity_type, title, display_key AS subtitle,
                                  objective || '\n' || hypothesis AS snippet, display_key AS locator,
                                  status, updated_at, NULL AS version
                           FROM experiments WHERE project_id=?
                             AND evidence_match(
                               coalesce(title,'') || ' ' || coalesce(objective,'')
                               || ' ' || coalesce(hypothesis,'')
                             ) > 0
                           UNION ALL
                           SELECT id, 'experiment', 'ExperimentRun', name, display_key,
                                  command || '\nconfig=' || config_json
                                    || '\ndataset=' || coalesce(dataset_id,'')
                                    || '@' || coalesce(dataset_version,''),
                                  display_key, status, updated_at,
                                  commit_sha
                           FROM experiment_runs WHERE project_id=?
                             AND evidence_match(
                               coalesce(name,'') || ' ' || coalesce(command,'')
                               || ' ' || coalesce(config_json,'')
                               || ' ' || coalesce(commit_sha,'')
                               || ' ' || coalesce(external_id,'')
                               || ' ' || coalesce(dataset_id,'')
                               || ' ' || coalesce(dataset_version,'')
                             ) > 0
                           UNION ALL
                           SELECT m.id, 'experiment', 'Metric', m.name, r.display_key,
                                  m.name || ' = ' || m.value, m.id, 'recorded', m.created_at,
                                  r.commit_sha
                           FROM run_metrics m JOIN experiment_runs r ON r.id=m.run_id
                           WHERE m.project_id=?
                             AND evidence_match(
                               coalesce(m.name,'') || ' ' || CAST(m.value AS TEXT)
                             ) > 0
                           LIMIT ?""",
                        (project_id, project_id, project_id, limit),
                    ).fetchall()
                )
            if not sources or "notebook" in sources:
                notebook_acl_clause = ""
                notebook_values: list[Any] = [project_id]
                if enforce_acl:
                    marks = ",".join("?" for _ in sorted(allowed))
                    notebook_acl_clause = f" AND t.acl_ref IN ({marks})"
                    notebook_values.extend(sorted(allowed))
                notebook_values.append(limit)
                rows.extend(
                    dict(row)
                    for row in db.execute(
                        f"""SELECT n.id AS entity_id, 'notebook' AS source,
                                   'NotebookRun' AS entity_type, t.name AS title,
                                   n.version AS subtitle,
                                   coalesce((SELECT group_concat(substr(c.source,1,500), '\n')
                                     FROM notebook_cells c
                                     WHERE c.notebook_run_id=n.id),'') AS snippet,
                                   n.id AS locator, n.status, n.created_at AS updated_at,
                                   n.version
                            FROM notebook_runs n
                            JOIN notebook_templates t ON t.id=n.template_id
                            WHERE n.project_id=?{notebook_acl_clause} AND (
                              evidence_match(coalesce(t.name,'')) > 0 OR EXISTS (
                                SELECT 1 FROM notebook_cells c
                                WHERE c.notebook_run_id=n.id
                                  AND evidence_match(coalesce(c.source,'')) > 0
                              )
                            ) LIMIT ?""",
                        notebook_values,
                    ).fetchall()
                )
            if not sources or "document" in sources:
                rows.extend(
                    dict(row)
                    for row in db.execute(
                        """SELECT id AS entity_id, 'document' AS source,
                                  'ScientificDocument' AS entity_type, title,
                                  display_key || ' · ' || version AS subtitle,
                                  substr(content, 1, 1200) AS snippet, source_uri AS locator,
                                  status, updated_at, version
                           FROM scientific_documents WHERE project_id=?
                             AND evidence_match(
                               coalesce(title,'') || ' ' || coalesce(content,'')
                             ) > 0
                           UNION ALL
                           SELECT c.id, 'document', 'Claim', c.content, c.display_key,
                                  c.content, c.source_locator, c.status, c.updated_at, d.version
                           FROM claims c JOIN scientific_documents d ON d.id=c.document_id
                           WHERE c.project_id=?
                             AND evidence_match(coalesce(c.content,'')) > 0
                           UNION ALL
                           SELECT t.id, 'document', 'Table', t.title,
                                  d.display_key || ' · Table ' || t.ordinal,
                                  coalesce(t.caption,'') || ' · ' ||
                                    coalesce((SELECT group_concat(value, ' | ')
                                      FROM document_table_cells cell WHERE cell.table_id=t.id),''),
                                  t.source_locator, 'extracted', t.created_at, d.version
                           FROM document_tables t
                           JOIN scientific_documents d ON d.id=t.document_id
                           WHERE t.project_id=? AND (
                             evidence_match(
                               coalesce(t.title,'') || ' ' || coalesce(t.caption,'')
                             ) > 0 OR EXISTS (
                               SELECT 1 FROM document_table_cells cell
                               WHERE cell.table_id=t.id
                                 AND evidence_match(coalesce(cell.value,'')) > 0
                             )
                           )
                           LIMIT ?""",
                        (project_id, project_id, project_id, limit),
                    ).fetchall()
                )
        if rows:
            ids = [str(row["entity_id"]) for row in rows]
            marks = ",".join("?" for _ in ids)
            with self.database.connection() as db:
                blocked = {
                    row["entity_id"]
                    for row in db.execute(
                        f"SELECT entity_id FROM blocked_entities WHERE entity_id IN ({marks})",
                        ids,
                    ).fetchall()
                }
            rows = [row for row in rows if row["entity_id"] not in blocked]
        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            haystack = " ".join(
                str(row.get(field) or "")
                for field in ("title", "subtitle", "snippet", "locator", "version")
            ).casefold()
            matched = [token for token in tokens if token in haystack]
            coverage = len(matched) / len(tokens)
            title = str(row["title"] or "").casefold()
            title_matches = sum(token in title for token in tokens)
            title_coverage = title_matches / len(tokens)
            row["score"] = round(min(1.0, 0.42 + 0.4 * coverage + 0.18 * title_coverage), 6)
            row["channels"] = ["structured_multi_term"]
            row["matched_terms"] = matched
            previous = unique.get(str(row["entity_id"]))
            if previous is None or row["score"] > previous["score"]:
                unique[str(row["entity_id"])] = row
        return sorted(unique.values(), key=lambda item: item["score"], reverse=True)[:limit]

    @staticmethod
    def _query_terms(query: str, limit: int = 16) -> list[str]:
        """Extract bounded multilingual terms for local structured-source retrieval.

        CJK questions do not contain spaces and SQLite's default tokenizer often treats an
        entire sentence as one token.  Character n-grams provide useful matching without
        pretending that a language model or external segmenter is available.
        """

        lowered = query.casefold()
        raw_tokens = [raw.casefold().strip("._:/@+-") for raw in SEARCH_TOKEN_RE.findall(query)]
        terms: list[str] = [
            token
            for token in raw_tokens
            if token
            and not re.fullmatch(r"[\u3400-\u9fff]+", token)
            and token not in SEARCH_STOPWORDS
        ]
        terms.extend(
            expansion
            for phrase, values in QUERY_EXPANSIONS.items()
            if phrase in lowered
            for expansion in values
        )
        for token in raw_tokens:
            if not token or token in SEARCH_STOPWORDS:
                continue
            if not re.fullmatch(r"[\u3400-\u9fff]+", token):
                continue
            if len(token) > 4:
                for width in (3, 2):
                    for offset in range(len(token) - width + 1):
                        part = token[offset : offset + width]
                        if part not in SEARCH_STOPWORDS:
                            terms.append(part)
            else:
                terms.append(token)
        return list(dict.fromkeys(terms))[:limit]

    def edges_for(
        self,
        entity_ids: list[str],
        *,
        project_id: str | None = None,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        if not entity_ids:
            return []
        if project_id:
            return self._project_edges_for(project_id, entity_ids, as_of=as_of)
        effective_at = self._edge_as_of(as_of)
        marks = ",".join("?" for _ in entity_ids)
        repeated = [*entity_ids, *entity_ids]
        edges: list[dict[str, Any]] = []
        with self.database.connection() as db:
            edges.extend(
                {
                    "id": row["id"],
                    "source": row["source_entity_id"],
                    "predicate": row["predicate"],
                    "target": row["target_entity_id"],
                    "derivation": row["derivation"],
                    "confidence": row["confidence"],
                    "review_status": row["review_status"],
                    "domain": "platform",
                }
                for row in db.execute(
                    f"""SELECT * FROM platform_edges
                        WHERE review_status!='rejected'
                          AND (source_entity_id IN ({marks}) OR target_entity_id IN ({marks}))
                        LIMIT 1000""",
                    repeated,
                ).fetchall()
                if self._platform_edge_is_effective(row, effective_at)
            )
            edges.extend(
                {
                    "id": row["id"],
                    "source": row["source_id"],
                    "predicate": row["edge_type"],
                    "target": row["target_id"],
                    "derivation": row["derivation"],
                    "confidence": row["confidence"],
                    "review_status": "confirmed",
                    "domain": "code",
                }
                for row in db.execute(
                    f"""SELECT e.* FROM edges e JOIN repositories r ON r.id=e.repository_id
                        WHERE e.generation_id=r.active_generation_id
                          AND (e.source_id IN ({marks}) OR e.target_id IN ({marks}))
                        LIMIT 1000""",
                    repeated,
                ).fetchall()
            )
            edges.extend(
                {
                    "id": row["id"],
                    "source": row["source_entity_id"],
                    "predicate": row["edge_type"],
                    "target": row["target_entity_id"],
                    "derivation": row["derivation"],
                    "confidence": row["confidence"],
                    "review_status": "confirmed",
                    "domain": "codex",
                }
                for row in db.execute(
                    f"""SELECT e.* FROM codex_edges e
                        JOIN codex_sources s ON s.id=e.source_id
                        WHERE e.generation_id=s.active_generation_id
                          AND (e.source_entity_id IN ({marks}) OR e.target_entity_id IN ({marks}))
                        LIMIT 1000""",
                    repeated,
                ).fetchall()
            )
            edges.extend(
                {
                    "id": row["id"],
                    "source": row["iteration_id"],
                    "predicate": row["role"],
                    "target": row["entity_id"],
                    "derivation": "deterministic",
                    "confidence": 1.0,
                    "review_status": "confirmed",
                    "domain": "workspace",
                }
                for row in db.execute(
                    f"""SELECT * FROM iteration_links
                        WHERE iteration_id IN ({marks}) OR entity_id IN ({marks}) LIMIT 1000""",
                    repeated,
                ).fetchall()
            )
        unique = {item["id"]: item for item in edges}
        return list(unique.values())

    def code_relation_snapshot(
        self,
        project_id: str,
        focus_entity_id: str,
        *,
        relation_types: list[str] | None = None,
        direction: str = "both",
        limit: int = 80,
    ) -> dict[str, Any]:
        """Return a project-scoped, direction-aware branch around one code node."""

        with self.database.connection() as db:
            focus = db.execute(
                """SELECT entity.id
                     FROM entities entity
                     JOIN repositories repository
                       ON repository.id=entity.repository_id
                      AND repository.active_generation_id=entity.generation_id
                    WHERE repository.project_id=? AND entity.id=?""",
                (project_id, focus_entity_id),
            ).fetchone()
            if not focus:
                return {
                    "nodes": [],
                    "edges": [],
                    "relation_counts": {},
                    "direction_counts": {},
                }
            count_rows = db.execute(
                """SELECT edge.edge_type, count(*) AS count
                     FROM edges edge
                     JOIN repositories repository
                       ON repository.id=edge.repository_id
                      AND repository.active_generation_id=edge.generation_id
                    WHERE repository.project_id=?
                      AND (edge.source_id=? OR edge.target_id=?)
                    GROUP BY edge.edge_type""",
                (project_id, focus_entity_id, focus_entity_id),
            ).fetchall()
            relation_counts = {str(row["edge_type"]): int(row["count"]) for row in count_rows}
            direction_row = db.execute(
                """SELECT
                       sum(CASE WHEN edge.target_id=? THEN 1 ELSE 0 END) AS incoming,
                       sum(CASE WHEN edge.source_id=? THEN 1 ELSE 0 END) AS outgoing
                     FROM edges edge
                     JOIN repositories repository
                       ON repository.id=edge.repository_id
                      AND repository.active_generation_id=edge.generation_id
                    WHERE repository.project_id=?
                      AND (edge.source_id=? OR edge.target_id=?)""",
                (
                    focus_entity_id,
                    focus_entity_id,
                    project_id,
                    focus_entity_id,
                    focus_entity_id,
                ),
            ).fetchone()
            direction_counts = {
                "incoming": int(direction_row["incoming"] or 0),
                "outgoing": int(direction_row["outgoing"] or 0),
            }
            wanted = sorted({item.upper() for item in (relation_types or []) if item})
            clauses = [
                "repository.project_id=?",
                "(edge.source_id=? OR edge.target_id=?)",
            ]
            values: list[Any] = [project_id, focus_entity_id, focus_entity_id]
            if wanted:
                clauses.append("edge.edge_type IN (" + ",".join("?" for _ in wanted) + ")")
                values.extend(wanted)
            if direction == "incoming":
                clauses.append("edge.target_id=?")
                values.append(focus_entity_id)
            elif direction == "outgoing":
                clauses.append("edge.source_id=?")
                values.append(focus_entity_id)
            values.append(max(1, limit * 4))
            selected_rows = db.execute(
                f"""SELECT edge.*
                      FROM edges edge
                      JOIN repositories repository
                        ON repository.id=edge.repository_id
                       AND repository.active_generation_id=edge.generation_id
                     WHERE {" AND ".join(clauses)}
                     ORDER BY edge.edge_type, edge.id
                     LIMIT ?""",
                values,
            ).fetchall()
        selected = [
            {
                "id": row["id"],
                "source": row["source_id"],
                "predicate": row["edge_type"],
                "target": row["target_id"],
                "derivation": row["derivation"],
                "confidence": row["confidence"],
                "review_status": "confirmed",
                "domain": "code",
            }
            for row in selected_rows
        ]
        node_ids = [focus_entity_id]
        visible_edges: list[dict[str, Any]] = []
        for edge in selected:
            endpoint = edge["target"] if edge["source"] == focus_entity_id else edge["source"]
            if endpoint not in node_ids:
                if len(node_ids) >= limit:
                    continue
                node_ids.append(endpoint)
            visible_edges.append(edge)
        return {
            "nodes": self.resolve_nodes(node_ids),
            "edges": visible_edges,
            "relation_counts": relation_counts,
            "direction_counts": direction_counts,
        }

    def _project_edges_for(
        self,
        project_id: str,
        entity_ids: list[str],
        *,
        as_of: str | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve project-scoped relations without scanning complete edge tables."""

        effective_at = self._edge_as_of(as_of)
        marks = ",".join("?" for _ in entity_ids)
        project_values = [project_id, *entity_ids, project_id, *entity_ids]
        edges: list[dict[str, Any]] = []
        with self.database.connection() as db:
            edges.extend(
                {
                    "id": row["id"],
                    "source": row["source_entity_id"],
                    "predicate": row["predicate"],
                    "target": row["target_entity_id"],
                    "derivation": row["derivation"],
                    "confidence": row["confidence"],
                    "review_status": row["review_status"],
                    "domain": "platform",
                }
                for row in db.execute(
                    f"""SELECT * FROM platform_edges
                        WHERE project_id=? AND review_status!='rejected'
                          AND source_entity_id IN ({marks})
                        UNION ALL
                        SELECT * FROM platform_edges
                        WHERE project_id=? AND review_status!='rejected'
                          AND target_entity_id IN ({marks})
                        LIMIT 1000""",
                    project_values,
                ).fetchall()
                if self._platform_edge_is_effective(row, effective_at)
            )
            edges.extend(
                {
                    "id": row["id"],
                    "source": row["source_id"],
                    "predicate": row["edge_type"],
                    "target": row["target_id"],
                    "derivation": row["derivation"],
                    "confidence": row["confidence"],
                    "review_status": "confirmed",
                    "domain": "code",
                }
                for row in db.execute(
                    f"""SELECT * FROM edges
                        WHERE generation_id IN (
                          SELECT active_generation_id FROM repositories WHERE project_id=?
                        ) AND source_id IN ({marks})
                        UNION ALL
                        SELECT * FROM edges
                        WHERE generation_id IN (
                          SELECT active_generation_id FROM repositories WHERE project_id=?
                        ) AND target_id IN ({marks})
                        LIMIT 1000""",
                    project_values,
                ).fetchall()
            )
            edges.extend(
                {
                    "id": row["id"],
                    "source": row["source_entity_id"],
                    "predicate": row["edge_type"],
                    "target": row["target_entity_id"],
                    "derivation": row["derivation"],
                    "confidence": row["confidence"],
                    "review_status": "confirmed",
                    "domain": "codex",
                }
                for row in db.execute(
                    f"""SELECT * FROM codex_edges
                        WHERE generation_id IN (
                          SELECT active_generation_id FROM codex_sources WHERE project_id=?
                        ) AND source_entity_id IN ({marks})
                        UNION ALL
                        SELECT * FROM codex_edges
                        WHERE generation_id IN (
                          SELECT active_generation_id FROM codex_sources WHERE project_id=?
                        ) AND target_entity_id IN ({marks})
                        LIMIT 1000""",
                    project_values,
                ).fetchall()
            )
            edges.extend(
                {
                    "id": row["id"],
                    "source": row["iteration_id"],
                    "predicate": row["role"],
                    "target": row["entity_id"],
                    "derivation": "deterministic",
                    "confidence": 1.0,
                    "review_status": "confirmed",
                    "domain": "workspace",
                }
                for row in db.execute(
                    f"""SELECT * FROM iteration_links
                        WHERE project_id=? AND iteration_id IN ({marks})
                        UNION ALL
                        SELECT * FROM iteration_links
                        WHERE project_id=? AND entity_id IN ({marks})
                        LIMIT 1000""",
                    project_values,
                ).fetchall()
            )
        unique = {item["id"]: item for item in edges}
        return list(unique.values())

    @staticmethod
    def _edge_as_of(value: str | None) -> datetime:
        raw = value or datetime.now(UTC).isoformat()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as error:
            raise ValueError("invalid_relation_as_of") from error
        if parsed.tzinfo is None:
            raise ValueError("invalid_relation_as_of")
        return parsed.astimezone(UTC)

    @classmethod
    def _platform_edge_is_effective(cls, row: Any, effective_at: datetime) -> bool:
        def boundary(name: str) -> datetime | None:
            raw = row[name]
            if raw is None:
                return None
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return None
            return parsed.astimezone(UTC)

        raw_from = row["valid_from"]
        raw_to = row["valid_to"]
        valid_from = boundary("valid_from")
        valid_to = boundary("valid_to")
        if raw_from is not None and valid_from is None:
            return False
        if raw_to is not None and valid_to is None:
            return False
        if valid_from is not None and valid_from > effective_at:
            return False
        return valid_to is None or effective_at < valid_to

    def neighbor_edges_page(
        self,
        entity_id: str,
        *,
        cursor: int,
        limit: int,
    ) -> dict[str, Any]:
        """Return a stable page of direct relations without a graph-size ceiling."""
        sql = """
            WITH neighbor_edges AS (
                SELECT id, source_entity_id AS source, predicate,
                       target_entity_id AS target, derivation, confidence,
                       review_status, 'platform' AS domain
                  FROM platform_edges
                 WHERE review_status!='rejected'
                   AND (
                     valid_from IS NULL OR (
                       datetime(valid_from) IS NOT NULL
                       AND (
                         substr(valid_from, -1)='Z'
                         OR substr(valid_from, -6, 1) IN ('+', '-')
                       )
                       AND datetime(valid_from)<=datetime(:as_of)
                     )
                   )
                   AND (
                     valid_to IS NULL OR (
                       datetime(valid_to) IS NOT NULL
                       AND (
                         substr(valid_to, -1)='Z'
                         OR substr(valid_to, -6, 1) IN ('+', '-')
                       )
                       AND datetime(valid_to)>datetime(:as_of)
                     )
                   )
                   AND (source_entity_id=:entity_id OR target_entity_id=:entity_id)
                UNION ALL
                SELECT edge.id, edge.source_id, edge.edge_type, edge.target_id,
                       edge.derivation, edge.confidence, 'confirmed', 'code'
                  FROM edges edge
                  JOIN repositories repository
                    ON repository.id=edge.repository_id
                   AND repository.active_generation_id=edge.generation_id
                 WHERE edge.source_id=:entity_id OR edge.target_id=:entity_id
                UNION ALL
                SELECT edge.id, edge.source_entity_id, edge.edge_type,
                       edge.target_entity_id, edge.derivation, edge.confidence,
                       'confirmed', 'codex'
                  FROM codex_edges edge
                  JOIN codex_sources source
                    ON source.id=edge.source_id
                   AND source.active_generation_id=edge.generation_id
                 WHERE edge.source_entity_id=:entity_id
                    OR edge.target_entity_id=:entity_id
                UNION ALL
                SELECT id, iteration_id, role, entity_id, 'deterministic', 1.0,
                       'confirmed', 'workspace'
                  FROM iteration_links
                 WHERE iteration_id=:entity_id OR entity_id=:entity_id
            )
            SELECT neighbor_edges.*, count(*) OVER () AS total_count
              FROM neighbor_edges
             ORDER BY domain, predicate, id
             LIMIT :limit OFFSET :cursor
        """
        effective_at = self._edge_as_of(None).isoformat()
        with self.database.connection() as db:
            rows = db.execute(
                sql,
                {
                    "as_of": effective_at,
                    "entity_id": entity_id,
                    "limit": limit,
                    "cursor": cursor,
                },
            ).fetchall()
        total = int(rows[0]["total_count"]) if rows else 0
        edges = [
            {
                key: row[key]
                for key in (
                    "id",
                    "source",
                    "predicate",
                    "target",
                    "derivation",
                    "confidence",
                    "review_status",
                    "domain",
                )
            }
            for row in rows
        ]
        next_cursor = cursor + len(edges)
        return {
            "edges": edges,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "total": total,
            "has_more": next_cursor < total,
        }

    def graph_snapshot(
        self,
        project_id: str,
        domain: str,
        query: str = "",
        limit: int = 36,
        *,
        metadata: dict[str, Any] | None = None,
        focus_entity_id: str = "",
    ) -> dict[str, Any]:
        metadata = metadata or self.graph_metadata(project_id, domain)
        if domain == "research":
            nodes, edges = self._research_graph(project_id, query, limit)
        elif domain == "document":
            nodes, edges = self._document_graph(project_id, query, limit)
        else:
            roots = self._graph_roots(
                project_id,
                domain,
                query,
                max(4, limit // 3),
                focus_entity_id=focus_entity_id,
            )
            edge_domain = "code" if domain == "code" else "codex"
            candidates = [
                edge
                for edge in self.edges_for(roots, project_id=project_id)
                if edge["domain"] in {edge_domain, "platform"}
            ]
            if domain == "codex":
                # A thread is adjacent to every turn.  Expanding every edge from
                # that root makes a large session consume the complete graph
                # budget before the requested turn or its evidence is reached.
                # Keep the already governed root window, then admit only direct
                # code bindings from that window.  Further exploration happens
                # through the existing per-node neighbour action.
                root_ids = set(roots)
                binding_predicates = {
                    "changed_path_maps_to",
                    "changed_symbol_maps_to",
                    "implemented_by",
                    "validated_by",
                    "supported_by",
                    "designed_by",
                }
                candidates = [
                    edge
                    for edge in candidates
                    if (edge["source"] in root_ids and edge["target"] in root_ids)
                    or (edge["predicate"] == "HAS_ITEM" and edge["source"] in root_ids)
                    or (
                        edge["predicate"] in binding_predicates
                        and (edge["source"] in root_ids or edge["target"] in root_ids)
                    )
                ]
            if domain == "code":
                root_ids = set(roots)

                def code_edge_priority(edge: dict[str, Any]) -> tuple[int, str, str]:
                    predicate = edge["predicate"]
                    if edge["domain"] == "code" and predicate == "DEFINES":
                        priority = 0 if edge["source"] in root_ids else 1
                    elif edge["domain"] == "code" and predicate in {"CALLS", "IMPORTS"}:
                        priority = 2
                    elif edge["domain"] == "code" and predicate == "HAS_COMMIT":
                        priority = 3
                    elif edge["domain"] == "code" and predicate == "CONTAINS":
                        priority = 4 if edge["target"] in root_ids else 5
                    elif predicate in {
                        "changed_path_maps_to",
                        "changed_symbol_maps_to",
                        "implemented_by",
                        "validated_by",
                        "supported_by",
                        "designed_by",
                    }:
                        priority = 6
                    elif predicate == "parent":
                        priority = 8
                    elif predicate == "contains_diff":
                        priority = 9
                    else:
                        priority = 7
                    return (priority, predicate, edge["id"])

                candidates.sort(key=code_edge_priority)
            node_ids = list(dict.fromkeys(roots))
            for edge in candidates:
                for endpoint in (edge["source"], edge["target"]):
                    if endpoint not in node_ids and len(node_ids) < limit:
                        node_ids.append(endpoint)
            allowed = set(node_ids)
            edges = [
                edge
                for edge in candidates
                if edge["source"] in allowed and edge["target"] in allowed
            ][: limit * 2]
            nodes = self.resolve_nodes(node_ids)
        return {
            "domain": domain,
            "mode": "relations",
            "query": query,
            "nodes": nodes,
            "edges": edges,
            "metadata": {
                **metadata,
                "visible_nodes": len(nodes),
                "visible_edges": len(edges),
                "sampled": metadata["total_nodes"] > len(nodes),
            },
        }

    def graph_metadata(self, project_id: str, domain: str) -> dict[str, Any]:
        if domain == "overview":
            code = self.graph_metadata(project_id, "code")
            codex = self.graph_metadata(project_id, "codex")
            research = self.graph_metadata(project_id, "research")
            return {
                "total_nodes": sum(item["total_nodes"] for item in (code, codex, research)),
                "total_edges": sum(item["total_edges"] for item in (code, codex, research)),
                "vector_views": sum(item["vector_views"] for item in (code, codex, research)),
                "embedding_model": next(
                    (
                        item["embedding_model"]
                        for item in (code, codex, research)
                        if item["embedding_model"]
                    ),
                    None,
                ),
            }
        with self.database.connection() as db:
            if domain == "code":
                repositories = db.execute(
                    """SELECT id, active_generation_id
                         FROM repositories
                        WHERE project_id=? AND active_generation_id IS NOT NULL""",
                    (project_id,),
                ).fetchall()
                total_nodes = 0
                total_edges = 0
                vector_views = 0
                embedding_model = None
                for repository in repositories:
                    parameters = (
                        repository["id"],
                        repository["active_generation_id"],
                    )
                    total_nodes += int(
                        db.execute(
                            """SELECT count(*) FROM entities
                                WHERE repository_id=? AND generation_id=?""",
                            parameters,
                        ).fetchone()[0]
                    )
                    total_edges += int(
                        db.execute(
                            """SELECT count(*) FROM edges
                                WHERE repository_id=? AND generation_id=?""",
                            parameters,
                        ).fetchone()[0]
                    )
                    vector_views += int(
                        db.execute(
                            """SELECT count(*) FROM search_views
                                WHERE repository_id=? AND generation_id=?""",
                            parameters,
                        ).fetchone()[0]
                    )
                    if embedding_model is None:
                        model = db.execute(
                            """SELECT embedding_model FROM search_views
                                WHERE repository_id=? AND generation_id=?
                                LIMIT 1""",
                            parameters,
                        ).fetchone()
                        embedding_model = model[0] if model else None
                return {
                    "total_nodes": total_nodes,
                    "total_edges": total_edges,
                    "vector_views": vector_views,
                    "embedding_model": embedding_model,
                }
            elif domain == "codex":
                row = db.execute(
                    """SELECT
                         (SELECT count(*) FROM codex_items item JOIN codex_sources source
                           ON source.id=item.source_id
                            AND source.active_generation_id=item.generation_id
                          WHERE source.project_id=?) +
                         (SELECT count(*) FROM codex_turns turn JOIN codex_sources source
                           ON source.id=turn.source_id
                            AND source.active_generation_id=turn.generation_id
                          WHERE source.project_id=?) +
                         (SELECT count(*) FROM codex_threads thread JOIN codex_sources source
                           ON source.id=thread.source_id
                            AND source.active_generation_id=thread.generation_id
                          WHERE source.project_id=?) AS total_nodes,
                         (SELECT count(*) FROM codex_edges edge JOIN codex_sources source
                           ON source.id=edge.source_id
                            AND source.active_generation_id=edge.generation_id
                          WHERE source.project_id=?) AS total_edges,
                         (SELECT count(*) FROM codex_search_views view JOIN codex_sources source
                           ON source.id=view.source_id
                            AND source.active_generation_id=view.generation_id
                          WHERE source.project_id=?) AS vector_views,
                         (SELECT min(view.embedding_model) FROM codex_search_views view
                           JOIN codex_sources source ON source.id=view.source_id
                            AND source.active_generation_id=view.generation_id
                          WHERE source.project_id=?) AS embedding_model""",
                    (project_id, project_id, project_id, project_id, project_id, project_id),
                ).fetchone()
            elif domain == "document":
                row = db.execute(
                    """SELECT
                         (SELECT count(*) FROM scientific_documents WHERE project_id=?) +
                         (SELECT count(*) FROM document_sections WHERE project_id=?) +
                         (SELECT count(*) FROM document_tables WHERE project_id=?) +
                         (SELECT count(*) FROM document_figures WHERE project_id=?) +
                         (SELECT count(*) FROM claims WHERE project_id=?) AS total_nodes,
                         (SELECT count(*) FROM document_sections WHERE project_id=?) +
                         (SELECT count(*) FROM document_tables WHERE project_id=?) +
                         (SELECT count(*) FROM document_figures WHERE project_id=?) +
                         (SELECT count(*) FROM claims WHERE project_id=?) +
                         (SELECT count(*) FROM claim_evidence WHERE project_id=?) AS total_edges,
                         0 AS vector_views, NULL AS embedding_model""",
                    (project_id,) * 10,
                ).fetchone()
            elif domain == "research":
                row = db.execute(
                    """SELECT
                         (SELECT count(*) FROM scientific_documents WHERE project_id=?) +
                         (SELECT count(*) FROM document_sections WHERE project_id=?) +
                         (SELECT count(*) FROM document_tables WHERE project_id=?) +
                         (SELECT count(*) FROM document_figures WHERE project_id=?) +
                         (SELECT count(*) FROM claims WHERE project_id=?) +
                         (SELECT count(*) FROM experiments WHERE project_id=?) +
                         (SELECT count(*) FROM experiment_runs WHERE project_id=?) +
                         (SELECT count(*) FROM run_metrics WHERE project_id=?) +
                         (SELECT count(*) FROM run_artifacts WHERE project_id=?) AS total_nodes,
                         (SELECT count(*) FROM document_sections WHERE project_id=?) +
                         (SELECT count(*) FROM document_tables WHERE project_id=?) +
                         (SELECT count(*) FROM document_figures WHERE project_id=?) +
                         (SELECT count(*) FROM claims WHERE project_id=?) +
                         (SELECT count(*) FROM claim_evidence WHERE project_id=?) +
                         (SELECT count(*) FROM experiment_runs WHERE project_id=?) +
                         (SELECT count(*) FROM run_metrics WHERE project_id=?) +
                         (SELECT count(*) FROM run_artifacts WHERE project_id=?) AS total_edges,
                         0 AS vector_views, NULL AS embedding_model""",
                    (project_id,) * 17,
                ).fetchone()
        return {
            "total_nodes": int(row["total_nodes"] or 0),
            "total_edges": int(row["total_edges"] or 0),
            "vector_views": int(row["vector_views"] or 0),
            "embedding_model": row["embedding_model"],
        }

    def _graph_roots(
        self,
        project_id: str,
        domain: str,
        query: str,
        limit: int,
        *,
        focus_entity_id: str = "",
    ) -> list[str]:
        term = f"%{query.strip()}%"
        with self.database.connection() as db:
            if domain == "code" and query.strip():
                exact_rows = db.execute(
                    """SELECT e.id FROM entities e JOIN repositories r
                         ON r.id=e.repository_id AND r.active_generation_id=e.generation_id
                       WHERE e.project_id=? AND e.path=?
                       ORDER BY CASE e.entity_type
                         WHEN 'FileVersion' THEN 0
                         WHEN 'CodeSymbol' THEN 1
                         ELSE 2
                       END, coalesce(e.start_line, 0), e.name
                       LIMIT ?""",
                    (project_id, query.strip(), limit),
                ).fetchall()
                rows = (
                    exact_rows
                    or db.execute(
                        """SELECT e.id FROM entities e JOIN repositories r
                         ON r.id=e.repository_id AND r.active_generation_id=e.generation_id
                       WHERE e.project_id=? AND (
                         e.name LIKE ? OR e.qualified_name LIKE ? OR e.path LIKE ?
                       )
                       ORDER BY CASE WHEN e.name=? THEN 0 ELSE 1 END, e.entity_type, e.name
                       LIMIT ?""",
                        (project_id, term, term, term, query.strip(), limit),
                    ).fetchall()
                )
            elif domain == "code":
                active_repositories = db.execute(
                    """SELECT id, active_generation_id
                         FROM repositories
                        WHERE project_id=? AND active_generation_id IS NOT NULL
                        ORDER BY name, id""",
                    (project_id,),
                ).fetchall()
                selected: list[Any] = []
                for repository in active_repositories:
                    selected.extend(
                        db.execute(
                            """SELECT id FROM entities
                                WHERE repository_id=? AND generation_id=?
                                  AND entity_type IN ('Repository', 'Commit')
                                ORDER BY CASE entity_type
                                  WHEN 'Repository' THEN 0 ELSE 1
                                END, entity_key DESC
                                LIMIT 2""",
                            (repository["id"], repository["active_generation_id"]),
                        ).fetchall()
                    )
                file_budget = max(0, limit - len(selected))
                repository_count = max(1, len(active_repositories))
                base_files = file_budget // repository_count
                remainder = file_budget % repository_count
                for index, repository in enumerate(active_repositories):
                    repository_limit = base_files + (1 if index < remainder else 0)
                    if repository_limit <= 0:
                        continue
                    selected.extend(
                        db.execute(
                            """SELECT file.id
                                 FROM entities file
                                WHERE file.repository_id=? AND file.generation_id=?
                                  AND file.entity_type='FileVersion'
                                ORDER BY
                                  CASE
                                    WHEN file.path LIKE 'src/%'
                                      OR file.path LIKE 'frontend/src/%'
                                      OR file.path LIKE 'app/%' THEN 0
                                    WHEN file.path LIKE 'tests/%'
                                      OR file.path LIKE 'test/%' THEN 1
                                    WHEN file.path LIKE 'docs/%' THEN 3
                                    WHEN file.path LIKE 'web/assets/%'
                                      OR file.path LIKE 'web/app/%'
                                      OR file.path LIKE 'dist/%'
                                      OR file.path LIKE 'build/%' THEN 4
                                    ELSE 2
                                  END,
                                  (SELECT count(*)
                                     FROM edges definition
                                    WHERE definition.repository_id=file.repository_id
                                      AND definition.generation_id=file.generation_id
                                      AND definition.source_id=file.id
                                      AND definition.edge_type='DEFINES') DESC,
                                  coalesce(file.path, ''),
                                  file.entity_key DESC
                                LIMIT ?""",
                            (
                                repository["id"],
                                repository["active_generation_id"],
                                repository_limit,
                            ),
                        ).fetchall()
                    )
                rows = selected[:limit]
            elif domain == "codex" and query.strip():
                thread = db.execute(
                    """SELECT thread.id, thread.thread_id, thread.source_id,
                              thread.generation_id
                         FROM codex_threads thread JOIN codex_sources source
                           ON source.id=thread.source_id
                          AND source.active_generation_id=thread.generation_id
                        WHERE source.project_id=?
                          AND (thread.id=? OR thread.thread_id=?)
                        ORDER BY thread.updated_at DESC LIMIT 1""",
                    (project_id, query.strip(), query.strip()),
                ).fetchone()
                if thread:
                    turn_limit = max(1, min(12, (limit - 1) // 3 or 1))
                    focused_turn = None
                    if focus_entity_id:
                        focused_turn = db.execute(
                            """SELECT turn.id FROM codex_turns turn
                               WHERE turn.id=? AND turn.thread_id=?
                                 AND turn.source_id=? AND turn.generation_id=?""",
                            (
                                focus_entity_id,
                                thread["id"],
                                thread["source_id"],
                                thread["generation_id"],
                            ),
                        ).fetchone()
                    turns = db.execute(
                        """SELECT id FROM codex_turns
                            WHERE thread_id=? AND source_id=? AND generation_id=?
                            ORDER BY ordinal DESC, turn_key DESC LIMIT ?""",
                        (
                            thread["id"],
                            thread["source_id"],
                            thread["generation_id"],
                            turn_limit - (1 if focused_turn else 0),
                        ),
                    ).fetchall()
                    turn_ids = list(
                        dict.fromkeys(
                            [
                                *([focused_turn["id"]] if focused_turn else []),
                                *(item["id"] for item in turns),
                            ]
                        )
                    )[:turn_limit]
                    if not turn_ids:
                        return [thread["id"]]
                    marks = ",".join("?" for _ in turn_ids)
                    items = db.execute(
                        f"""SELECT item.id FROM codex_items item
                              WHERE item.source_id=? AND item.generation_id=?
                                AND item.turn_id IN ({marks})
                              ORDER BY CASE item.item_type
                                WHEN 'UserGoal' THEN 0
                                WHEN 'DevelopmentEpisode' THEN 1
                                WHEN 'Patch' THEN 2
                                WHEN 'FileChange' THEN 3
                                WHEN 'ValidationResult' THEN 4
                                WHEN 'CommandExecution' THEN 5
                                WHEN 'AgentMessage' THEN 6
                                ELSE 9 END,
                                item.sequence DESC LIMIT ?""",
                        (
                            thread["source_id"],
                            thread["generation_id"],
                            *turn_ids,
                            max(0, limit - len(turn_ids) - 1),
                        ),
                    ).fetchall()
                    return [
                        thread["id"],
                        *turn_ids,
                        *(item["id"] for item in items),
                    ][:limit]
                rows = db.execute(
                    """SELECT item.id FROM codex_items item JOIN codex_sources source
                         ON source.id=item.source_id
                          AND source.active_generation_id=item.generation_id
                       WHERE source.project_id=? AND (
                         item.name LIKE ? OR item.content LIKE ?
                       )
                       ORDER BY item.sequence DESC LIMIT ?""",
                    (project_id, term, term, limit),
                ).fetchall()
            else:
                thread = db.execute(
                    """SELECT thread.id, thread.thread_id, thread.source_id, thread.generation_id
                       FROM codex_threads thread JOIN codex_sources source
                         ON source.id=thread.source_id
                          AND source.active_generation_id=thread.generation_id
                       WHERE source.project_id=?
                       ORDER BY thread.updated_at DESC LIMIT 1""",
                    (project_id,),
                ).fetchone()
                if not thread:
                    return []
                turns = db.execute(
                    """SELECT id FROM codex_turns
                       WHERE thread_id=? AND source_id=? AND generation_id=?
                       ORDER BY ordinal DESC LIMIT 3""",
                    (thread["id"], thread["source_id"], thread["generation_id"]),
                ).fetchall()
                turn_ids = [row["id"] for row in turns]
                if not turn_ids:
                    return [thread["id"]]
                marks = ",".join("?" for _ in turn_ids)
                items = db.execute(
                    f"""SELECT id FROM codex_items
                        WHERE generation_id=? AND turn_id IN (
                          SELECT turn_id FROM codex_turns WHERE id IN ({marks})
                        )
                        ORDER BY CASE item_type
                          WHEN 'UserGoal' THEN 0 WHEN 'DevelopmentEpisode' THEN 1
                          WHEN 'Patch' THEN 2 WHEN 'FileChange' THEN 3
                          WHEN 'ValidationResult' THEN 4 WHEN 'AgentMessage' THEN 5
                          ELSE 9 END, sequence DESC LIMIT ?""",
                    (thread["generation_id"], *turn_ids, max(0, limit - len(turn_ids) - 1)),
                ).fetchall()
                return [thread["id"], *turn_ids, *(row["id"] for row in items)]
        return [row["id"] for row in rows]

    def _document_graph(
        self,
        project_id: str,
        query: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        term = f"%{query.strip()}%"
        with self.database.connection() as db:
            if query.strip():
                documents = db.execute(
                    """SELECT DISTINCT document.id FROM scientific_documents document
                       LEFT JOIN document_sections section ON section.document_id=document.id
                       LEFT JOIN claims claim ON claim.document_id=document.id
                       WHERE document.project_id=? AND (
                         document.title LIKE ? OR document.content LIKE ?
                         OR section.title LIKE ? OR section.content LIKE ?
                         OR claim.content LIKE ?
                       ) ORDER BY document.updated_at DESC LIMIT 3""",
                    (project_id, term, term, term, term, term),
                ).fetchall()
            else:
                documents = db.execute(
                    """SELECT id FROM scientific_documents WHERE project_id=?
                       ORDER BY updated_at DESC LIMIT 3""",
                    (project_id,),
                ).fetchall()
            document_ids = [row["id"] for row in documents]
            if not document_ids:
                return [], []
            marks = ",".join("?" for _ in document_ids)
            sections = db.execute(
                f"""SELECT id, document_id FROM document_sections
                    WHERE document_id IN ({marks}) ORDER BY document_id, ordinal LIMIT ?""",
                (*document_ids, limit),
            ).fetchall()
            claims = db.execute(
                f"""SELECT id, document_id, section_id FROM claims
                    WHERE document_id IN ({marks}) ORDER BY updated_at DESC LIMIT ?""",
                (*document_ids, limit),
            ).fetchall()
            tables = db.execute(
                f"""SELECT id, document_id, section_id FROM document_tables
                    WHERE document_id IN ({marks}) ORDER BY ordinal LIMIT ?""",
                (*document_ids, limit),
            ).fetchall()
            figures = db.execute(
                f"""SELECT id, document_id FROM document_figures
                    WHERE document_id IN ({marks}) ORDER BY ordinal LIMIT ?""",
                (*document_ids, limit),
            ).fetchall()
            candidate_ids = [
                *document_ids,
                *(row["id"] for row in sections),
                *(row["id"] for row in claims),
                *(row["id"] for row in tables),
                *(row["id"] for row in figures),
            ][:limit]
            allowed = set(candidate_ids)
            edges: list[dict[str, Any]] = []

            def add_edge(source: str, predicate: str, target: str) -> None:
                if source in allowed and target in allowed:
                    edges.append(
                        {
                            "id": f"document-graph://{source}/{predicate}/{target}",
                            "source": source,
                            "predicate": predicate,
                            "target": target,
                            "derivation": "deterministic",
                            "confidence": 1.0,
                            "review_status": "confirmed",
                            "domain": "document",
                        }
                    )

            for row in sections:
                add_edge(row["document_id"], "CONTAINS_SECTION", row["id"])
            for row in claims:
                add_edge(row["section_id"] or row["document_id"], "ASSERTS", row["id"])
            for row in tables:
                add_edge(row["section_id"] or row["document_id"], "CONTAINS_TABLE", row["id"])
            for row in figures:
                add_edge(row["document_id"], "CONTAINS_FIGURE", row["id"])
            claim_ids = [row["id"] for row in claims if row["id"] in allowed]
            if claim_ids:
                claim_marks = ",".join("?" for _ in claim_ids)
                evidence = db.execute(
                    f"""SELECT id, claim_id, evidence_entity_id, relationship, confidence
                        FROM claim_evidence WHERE claim_id IN ({claim_marks})
                        ORDER BY created_at DESC LIMIT ?""",
                    (*claim_ids, limit),
                ).fetchall()
                for row in evidence:
                    if len(candidate_ids) < limit and row["evidence_entity_id"] not in allowed:
                        candidate_ids.append(row["evidence_entity_id"])
                        allowed.add(row["evidence_entity_id"])
                    if row["evidence_entity_id"] in allowed:
                        edges.append(
                            {
                                "id": row["id"],
                                "source": row["claim_id"],
                                "predicate": row["relationship"].upper(),
                                "target": row["evidence_entity_id"],
                                "derivation": "human_confirmed",
                                "confidence": row["confidence"],
                                "review_status": "confirmed",
                                "domain": "document",
                            }
                        )
        return self.resolve_nodes(candidate_ids), edges[: limit * 2]

    def _experiment_graph(
        self,
        project_id: str,
        query: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        term = f"%{query.strip()}%"
        with self.database.connection() as db:
            if query.strip():
                experiments = db.execute(
                    """SELECT DISTINCT experiment.id FROM experiments experiment
                       LEFT JOIN experiment_runs run ON run.experiment_id=experiment.id
                       WHERE experiment.project_id=? AND (
                         experiment.title LIKE ? OR experiment.objective LIKE ?
                         OR experiment.hypothesis LIKE ? OR run.name LIKE ?
                       ) ORDER BY experiment.updated_at DESC LIMIT 4""",
                    (project_id, term, term, term, term),
                ).fetchall()
            else:
                experiments = db.execute(
                    """SELECT id FROM experiments WHERE project_id=?
                       ORDER BY updated_at DESC LIMIT 4""",
                    (project_id,),
                ).fetchall()
            experiment_ids = [row["id"] for row in experiments]
            if not experiment_ids:
                return [], []
            marks = ",".join("?" for _ in experiment_ids)
            runs = db.execute(
                f"""SELECT id, experiment_id FROM experiment_runs
                    WHERE experiment_id IN ({marks})
                    ORDER BY updated_at DESC LIMIT ?""",
                (*experiment_ids, limit),
            ).fetchall()
            run_ids = [row["id"] for row in runs]
            metrics: list[Any] = []
            artifacts: list[Any] = []
            if run_ids:
                run_marks = ",".join("?" for _ in run_ids)
                metrics = db.execute(
                    f"""SELECT id, run_id FROM run_metrics
                        WHERE run_id IN ({run_marks}) ORDER BY created_at DESC LIMIT ?""",
                    (*run_ids, limit),
                ).fetchall()
                artifacts = db.execute(
                    f"""SELECT id, run_id FROM run_artifacts
                        WHERE run_id IN ({run_marks}) ORDER BY created_at DESC LIMIT ?""",
                    (*run_ids, limit),
                ).fetchall()
        candidate_ids = [
            *experiment_ids,
            *(row["id"] for row in runs),
            *(row["id"] for row in metrics),
            *(row["id"] for row in artifacts),
        ][:limit]
        allowed = set(candidate_ids)
        edges: list[dict[str, Any]] = []

        def add_edge(source: str, predicate: str, target: str) -> None:
            if source in allowed and target in allowed:
                edges.append(
                    {
                        "id": f"experiment-graph://{source}/{predicate}/{target}",
                        "source": source,
                        "predicate": predicate,
                        "target": target,
                        "derivation": "deterministic",
                        "confidence": 1.0,
                        "review_status": "confirmed",
                        "domain": "experiment",
                    }
                )

        for row in runs:
            add_edge(row["experiment_id"], "HAS_RUN", row["id"])
        for row in metrics:
            add_edge(row["run_id"], "REPORTS_METRIC", row["id"])
        for row in artifacts:
            add_edge(row["run_id"], "PRODUCES_ARTIFACT", row["id"])
        return self.resolve_nodes(candidate_ids), edges[: limit * 2]

    def _research_graph(
        self,
        project_id: str,
        query: str,
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        document_limit = max(6, limit // 2)
        experiment_limit = max(6, limit - document_limit)
        document_nodes, document_edges = self._document_graph(project_id, query, document_limit)
        experiment_nodes, experiment_edges = self._experiment_graph(
            project_id, query, experiment_limit
        )
        nodes_by_id = {node["id"]: node for node in [*document_nodes, *experiment_nodes]}
        node_ids = list(nodes_by_id)[:limit]
        allowed = set(node_ids)
        edges_by_id = {
            edge["id"]: edge
            for edge in [*document_edges, *experiment_edges, *self.edges_for(node_ids)]
            if edge["source"] in allowed and edge["target"] in allowed
        }
        return [nodes_by_id[node_id] for node_id in node_ids], list(edges_by_id.values())[
            : limit * 2
        ]

    def resolve_nodes(self, entity_ids: list[str]) -> list[dict[str, Any]]:
        if not entity_ids:
            return []

        def presentable_changed_path(value: Any, cwd: str) -> str | None:
            path = str(value or "").strip().replace("\\", "/")
            normalized_cwd = str(cwd or "").strip().replace("\\", "/").rstrip("/")
            if normalized_cwd and path.startswith(f"{normalized_cwd}/"):
                path = path[len(normalized_cwd) + 1 :]
            path = path.removeprefix("./")
            parts = path.split("/")
            if (
                not path
                or len(path) > 260
                or path.startswith("/")
                or re.match(r"^[A-Za-z]:/", path)
                or any(part in {"", ".", ".."} for part in parts)
                or any(token in path for token in ("\n", "@@", "{", "}", "://", "%"))
                or not re.fullmatch(r"[A-Za-z0-9_@+.,/ -]+", path)
            ):
                return None
            basename = parts[-1]
            if basename not in {
                "Dockerfile",
                "Makefile",
                "LICENSE",
                "README",
                "Procfile",
            } and not re.search(r"\.[A-Za-z][A-Za-z0-9_-]{0,11}$", basename):
                return None
            return path

        marks = ",".join("?" for _ in entity_ids)
        found: dict[str, dict[str, Any]] = {}
        with self.database.connection() as db:
            queries = [
                (
                    f"""SELECT e.id, e.entity_type AS type, e.name AS label,
                               e.source_uri AS locator, 'code' AS domain,
                               e.commit_sha AS version, e.repository_id,
                               e.qualified_name, e.path, e.language,
                               e.start_line, e.end_line, e.metadata_json
                        FROM entities e JOIN repositories r ON r.id=e.repository_id
                        WHERE e.generation_id=r.active_generation_id AND e.id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"""SELECT i.id, i.item_type AS type, i.name AS label,
                               i.source_locator AS locator, 'codex' AS domain,
                               t.updated_at AS version, i.metadata_json,
                               t.cwd AS thread_cwd
                        FROM codex_items i JOIN codex_sources s ON s.id=i.source_id
                        JOIN codex_threads t ON t.id=i.thread_id AND t.generation_id=i.generation_id
                        WHERE i.generation_id=s.active_generation_id AND i.id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"""SELECT t.id, 'CodexThread' AS type, t.title AS label,
                               t.source_file AS locator, 'codex' AS domain,
                               t.updated_at AS version
                        FROM codex_threads t JOIN codex_sources s ON s.id=t.source_id
                        WHERE t.generation_id=s.active_generation_id AND t.id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"""SELECT turn.id, 'CodexTurn' AS type,
                               CASE WHEN turn.goal!='' THEN substr(turn.goal,1,160)
                                    ELSE 'Turn ' || turn.ordinal END AS label,
                               turn.id AS locator, 'codex' AS domain,
                               turn.completed_at AS version
                        FROM codex_turns turn JOIN codex_sources source
                          ON source.id=turn.source_id
                        WHERE turn.generation_id=source.active_generation_id
                          AND turn.id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'ResearchTopic' type, title label, display_key locator, 'workspace' domain, updated_at version FROM research_topics WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'ResearchIteration' type, title label, display_key locator, 'workspace' domain, updated_at version FROM research_iterations WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'Experiment' type, title label, display_key locator, 'experiment' domain, updated_at version FROM experiments WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'ExperimentRun' type, name label, display_key locator, 'experiment' domain, commit_sha version FROM experiment_runs WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"""SELECT metric.id, 'Metric' type,
                               metric.name || ' = ' || CAST(metric.value AS TEXT) label,
                               metric.id locator, 'experiment' domain,
                               run.commit_sha version
                        FROM run_metrics metric JOIN experiment_runs run ON run.id=metric.run_id
                        WHERE metric.id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"""SELECT artifact.id, 'Artifact' type, artifact.name label,
                               artifact.uri locator, 'experiment' domain,
                               run.commit_sha version
                        FROM run_artifacts artifact
                        JOIN experiment_runs run ON run.id=artifact.run_id
                        WHERE artifact.id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'ScientificDocument' type, title label, source_uri locator, 'document' domain, version FROM scientific_documents WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'DocumentSection' type, title label, source_locator locator, 'document' domain, NULL version FROM document_sections WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'Claim' type, substr(content,1,160) label, source_locator locator, 'document' domain, status version FROM claims WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'Table' type, title label, source_locator locator, 'document' domain, extraction_confidence version FROM document_tables WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'TableCell' type, value label, source_locator locator, 'document' domain, NULL version FROM document_table_cells WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"""SELECT aggregation.id, 'MetricAggregation' type,
                               aggregation.display_key || ' · ' || aggregation.metric_name label,
                               cell.source_locator locator, 'document' domain,
                               aggregation.status version
                        FROM table_metric_aggregations aggregation
                        JOIN document_table_cells cell ON cell.id=aggregation.table_cell_id
                        WHERE aggregation.id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'Figure' type, caption label, source_locator locator, 'document' domain, NULL version FROM document_figures WHERE id IN ({marks})",
                    entity_ids,
                ),
                (
                    f"""SELECT id, 'Commit' type, substr(message,1,160) label,
                               source_uri locator, 'code' domain, sha version,
                               repository_id
                        FROM git_commits WHERE id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"""SELECT id, 'DiffHunk' type, path label, source_locator locator,
                               'code' domain, commit_sha version, repository_id, path
                        FROM diff_hunks WHERE id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"""SELECT id, 'TestResult' type, command label, id locator,
                               'code' domain, commit_sha version, repository_id
                        FROM test_results WHERE id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"""SELECT n.id, 'NotebookRun' type, t.name label, t.source_uri locator,
                               'experiment' domain, n.version
                        FROM notebook_runs n JOIN notebook_templates t ON t.id=n.template_id
                        WHERE n.id IN ({marks})""",
                    entity_ids,
                ),
                (
                    f"SELECT id, 'NotebookCell' type, substr(source,1,160) label, source_locator locator, 'experiment' domain, execution_count version FROM notebook_cells WHERE id IN ({marks})",
                    entity_ids,
                ),
            ]
            for sql, values in queries:
                for row in db.execute(sql, values).fetchall():
                    item = dict(row)
                    if "metadata_json" in item:
                        metadata = json.loads(item.pop("metadata_json") or "{}")
                        cwd = str(item.pop("thread_cwd", "") or "")
                        if item.get("type") in {"FileChange", "Patch"}:
                            candidates = metadata.get("paths") or []
                            if not candidates:
                                candidate = metadata.get("path") or metadata.get("file_path")
                                candidates = [candidate] if candidate else []
                            changed_paths = list(
                                dict.fromkeys(
                                    path
                                    for candidate in candidates
                                    if (path := presentable_changed_path(candidate, cwd))
                                )
                            )
                            if changed_paths:
                                item["path"] = changed_paths[0]
                                item["label"] = changed_paths[0]
                                item["metadata"] = {"changed_paths": changed_paths}
                        elif item.get("domain") == "code":
                            item["metadata"] = metadata
                    else:
                        item.pop("thread_cwd", None)
                    found[row["id"]] = item
        for entity_id in entity_ids:
            found.setdefault(
                entity_id,
                {
                    "id": entity_id,
                    "type": "ExternalEntity",
                    "label": entity_id,
                    "locator": entity_id,
                    "domain": "external",
                    "version": None,
                },
            )
        return list(found.values())

    def entity_acl_refs(self, entity_ids: list[str]) -> dict[str, str]:
        if not entity_ids:
            return {}
        marks = ",".join("?" for _ in entity_ids)
        queries = [
            f"SELECT id, acl_ref FROM entities WHERE id IN ({marks})",
            f"SELECT id, acl_ref FROM git_commits WHERE id IN ({marks})",
            f"SELECT id, acl_ref FROM diff_hunks WHERE id IN ({marks})",
            f"SELECT id, acl_ref FROM test_results WHERE id IN ({marks})",
            f"SELECT id, acl_ref FROM codex_items WHERE id IN ({marks})",
            f"""SELECT n.id, t.acl_ref FROM notebook_runs n
                JOIN notebook_templates t ON t.id=n.template_id WHERE n.id IN ({marks})""",
        ]
        project_queries = [
            ("research_topics", "id"),
            ("research_iterations", "id"),
            ("experiments", "id"),
            ("experiment_runs", "id"),
            ("run_metrics", "id"),
            ("run_artifacts", "id"),
            ("scientific_documents", "id"),
            ("claims", "id"),
            ("document_sections", "id"),
            ("document_pages", "id"),
            ("document_tables", "id"),
            ("document_table_cells", "id"),
            ("table_metric_aggregations", "id"),
            ("document_figures", "id"),
        ]
        found: dict[str, str] = {}
        with self.database.connection() as db:
            for sql in queries:
                for row in db.execute(sql, entity_ids).fetchall():
                    found[row["id"]] = row["acl_ref"]
            for table, id_column in project_queries:
                sql = f"""SELECT source.{id_column} AS id, p.acl_ref
                          FROM {table} source JOIN projects p ON p.id=source.project_id
                          WHERE source.{id_column} IN ({marks})"""
                for row in db.execute(sql, entity_ids).fetchall():
                    found[row["id"]] = row["acl_ref"]
        return found

    def scan_drift(self, project_id: str, update_claim_status: bool) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT c.id AS claim_id, c.display_key AS claim_key, c.content,
                          r.id AS run_id, r.display_key AS run_key, r.repository_id,
                          r.commit_sha AS original_commit, repo.head_commit AS current_commit,
                          r.dataset_id, r.dataset_version
                   FROM claims c
                   JOIN claim_evidence ce ON ce.claim_id=c.id
                        AND ce.relationship='supports' AND ce.evidence_type='experiment_run'
                   JOIN experiment_runs r ON r.id=ce.evidence_entity_id
                   JOIN repositories repo ON repo.id=r.repository_id
                   WHERE c.project_id=? AND r.commit_sha IS NOT NULL""",
                (project_id,),
            ).fetchall()
            history: dict[str, dict[str, Any]] = {}
            for repository_id in {row["repository_id"] for row in rows}:
                commits = db.execute(
                    """SELECT sha, parent_shas_json FROM git_commits
                       WHERE repository_id=? ORDER BY committed_at DESC""",
                    (repository_id,),
                ).fetchall()
                hunks = db.execute(
                    """SELECT commit_sha, path, affected_symbols_json FROM diff_hunks
                       WHERE repository_id=?""",
                    (repository_id,),
                ).fetchall()
                history[repository_id] = {
                    "parents": {
                        item["sha"]: json.loads(item["parent_shas_json"] or "[]")
                        for item in commits
                    },
                    "hunks": [dict(item) for item in hunks],
                }
        assessments = []
        now = utc_now()
        with self.database.transaction() as db:
            for row in rows:
                changed = row["original_commit"] != row["current_commit"]
                status, risk, reason, metadata = self._drift_assessment(
                    dict(row), history.get(row["repository_id"], {}), changed
                )
                item = {
                    **dict(row),
                    "status": status,
                    "risk_score": risk,
                    "reason": reason,
                    "metadata": metadata,
                }
                assessments.append(item)
                assessment_id = f"drift://{uuid4().hex}"
                db.execute(
                    """INSERT INTO drift_assessments
                       (id, project_id, claim_id, run_id, repository_id, original_commit,
                        current_commit, status, risk_score, reason, metadata_json, assessed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(claim_id, run_id, repository_id) DO UPDATE SET
                         original_commit=excluded.original_commit,
                         current_commit=excluded.current_commit,
                         status=excluded.status, risk_score=excluded.risk_score,
                         reason=excluded.reason, metadata_json=excluded.metadata_json,
                         assessed_at=excluded.assessed_at""",
                    (
                        assessment_id,
                        project_id,
                        row["claim_id"],
                        row["run_id"],
                        row["repository_id"],
                        row["original_commit"],
                        row["current_commit"],
                        status,
                        risk,
                        reason,
                        json.dumps(metadata, ensure_ascii=False),
                        now,
                    ),
                )
                if update_claim_status and status != "valid":
                    db.execute(
                        """UPDATE claims SET status='potentially_stale', updated_at=?
                           WHERE id=? AND status IN ('verified','partially_supported')""",
                        (now, row["claim_id"]),
                    )
        return assessments

    @staticmethod
    def _drift_assessment(
        row: dict[str, Any], history: dict[str, Any], changed: bool
    ) -> tuple[str, float, str, dict[str, Any]]:
        if not changed:
            return (
                "valid",
                0.0,
                "Supporting run commit matches the current indexed repository head.",
                {"historical_diff_available": True, "commit_path": [], "changed_paths": []},
            )
        parents: dict[str, list[str]] = history.get("parents", {})
        original = str(row["original_commit"]).split("+", 1)[0]
        current = str(row["current_commit"]).split("+", 1)[0]

        def resolve(value: str) -> str | None:
            if value in parents:
                return value
            matches = [sha for sha in parents if sha.startswith(value) or value.startswith(sha)]
            return matches[0] if len(matches) == 1 else None

        original_sha = resolve(original)
        current_sha = resolve(current)
        if not original_sha or not current_sha:
            return (
                "unknown_version",
                0.65,
                "One or both commits fall outside the indexed Git history; impact cannot be bounded.",
                {
                    "historical_diff_available": False,
                    "original_resolved": original_sha,
                    "current_resolved": current_sha,
                    "commit_path": [],
                    "changed_paths": [],
                },
            )

        queue: list[tuple[str, list[str]]] = [(current_sha, [])]
        visited: set[str] = set()
        commit_path: list[str] | None = None
        while queue and len(visited) <= 2_000:
            sha, newer = queue.pop(0)
            if sha in visited:
                continue
            visited.add(sha)
            if sha == original_sha:
                commit_path = newer
                break
            for parent in parents.get(sha, []):
                queue.append((parent, [sha, *newer]))
        if commit_path is None:
            return (
                "diverged",
                0.75,
                "The supporting commit is not an ancestor of the current indexed head.",
                {
                    "historical_diff_available": False,
                    "commit_path": [],
                    "changed_paths": [],
                },
            )

        relevant_hunks = [
            hunk for hunk in history.get("hunks", []) if hunk["commit_sha"] in commit_path
        ]
        changed_paths = sorted({hunk["path"] for hunk in relevant_hunks})
        affected_symbols = sorted(
            {
                symbol
                for hunk in relevant_hunks
                for symbol in json.loads(hunk.get("affected_symbols_json") or "[]")
            }
        )
        claim_text = str(row["content"]).casefold()
        candidates = [
            value
            for path in changed_paths
            for value in (path.casefold(), Path(path).name.casefold(), Path(path).stem.casefold())
        ] + [str(symbol).casefold() for symbol in affected_symbols]
        matched_terms = sorted(
            {term for term in candidates if len(term) >= 3 and term in claim_text}
        )
        metadata = {
            "historical_diff_available": bool(relevant_hunks),
            "commit_path": commit_path,
            "changed_paths": changed_paths,
            "diff_hunk_count": len(relevant_hunks),
            "affected_symbols": affected_symbols,
            "matched_terms": matched_terms,
        }
        if matched_terms:
            return (
                "impacted",
                0.9,
                "Indexed diffs modify paths or symbols explicitly referenced by the claim.",
                metadata,
            )
        if relevant_hunks:
            return (
                "potentially_stale",
                0.4,
                "The code version changed, but indexed diffs have no direct lexical overlap with the claim.",
                metadata,
            )
        return (
            "potentially_stale",
            0.55,
            "The commit path is known, but no diff hunks are available for impact analysis.",
            metadata,
        )

    def list_drift(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT d.*, c.display_key AS claim_key, c.content,
                          r.display_key AS run_key, repo.name AS repository_name
                   FROM drift_assessments d JOIN claims c ON c.id=d.claim_id
                   JOIN experiment_runs r ON r.id=d.run_id
                   JOIN repositories repo ON repo.id=d.repository_id
                   WHERE d.project_id=? ORDER BY d.risk_score DESC, d.assessed_at DESC""",
                (project_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            results.append(item)
        return results
