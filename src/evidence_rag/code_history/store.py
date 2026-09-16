from __future__ import annotations

import json
from typing import Any

from ..storage import SQLiteStore, utc_now
from .schema import SCHEMA


class CodeHistoryStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(SCHEMA)

    def repository(self, repository_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM repositories WHERE id=?", (repository_id,)).fetchone()
        return dict(row) if row else None

    def update_default_branch(self, repository_id: str, default_branch: str) -> None:
        with self.database.transaction() as db:
            db.execute(
                """UPDATE repositories
                   SET default_branch=?, updated_at=?
                   WHERE id=?""",
                (default_branch, utc_now(), repository_id),
            )

    def replace_history(
        self,
        repository_id: str,
        commits: list[dict[str, Any]],
        branches: list[dict[str, Any]],
        hunks: list[dict[str, Any]],
    ) -> None:
        with self.database.transaction() as db:
            db.executemany(
                """INSERT INTO git_commits
                   (id, project_id, repository_id, sha, tree_hash, parent_shas_json,
                    author_name, author_email_hash, authored_at, committed_at, message,
                    source_uri, acl_ref, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(repository_id, sha) DO UPDATE SET
                     tree_hash=excluded.tree_hash,
                     parent_shas_json=excluded.parent_shas_json,
                     author_name=excluded.author_name,
                     author_email_hash=excluded.author_email_hash,
                     authored_at=excluded.authored_at,
                     committed_at=excluded.committed_at,
                     message=excluded.message,
                     source_uri=excluded.source_uri,
                     observed_at=excluded.observed_at""",
                [
                    (
                        item["id"],
                        item["project_id"],
                        repository_id,
                        item["sha"],
                        item.get("tree_hash"),
                        json.dumps(item["parent_shas"]),
                        item.get("author_name"),
                        item.get("author_email_hash"),
                        item.get("authored_at"),
                        item.get("committed_at"),
                        item["message"],
                        item["source_uri"],
                        item["acl_ref"],
                        item["observed_at"],
                    )
                    for item in commits
                ],
            )
            db.executemany(
                """INSERT INTO git_branches
                   (id, repository_id, name, head_sha, is_default, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(repository_id, name) DO UPDATE SET
                     head_sha=excluded.head_sha, is_default=excluded.is_default,
                     observed_at=excluded.observed_at""",
                [
                    (
                        item["id"],
                        repository_id,
                        item["name"],
                        item["head_sha"],
                        int(item["is_default"]),
                        item["observed_at"],
                    )
                    for item in branches
                ],
            )
            branch_names = [item["name"] for item in branches]
            if branch_names:
                marks = ",".join("?" for _ in branch_names)
                db.execute(
                    f"""DELETE FROM git_branches
                        WHERE repository_id=? AND name NOT IN ({marks})""",
                    (repository_id, *branch_names),
                )
            else:
                db.execute(
                    "DELETE FROM git_branches WHERE repository_id=?",
                    (repository_id,),
                )
            commit_ids = [item["id"] for item in commits]
            if commit_ids:
                marks = ",".join("?" for _ in commit_ids)
                db.execute(f"DELETE FROM diff_hunks WHERE commit_id IN ({marks})", commit_ids)
            db.executemany(
                """INSERT INTO diff_hunks
                   (id, project_id, repository_id, commit_id, commit_sha, parent_sha,
                    path, old_path, change_type, old_start, old_count, new_start,
                    new_count, patch, patch_hash, source_locator, acl_ref,
                    affected_symbols_json, raw_object_id, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"],
                        item["project_id"],
                        repository_id,
                        item["commit_id"],
                        item["commit_sha"],
                        item.get("parent_sha"),
                        item["path"],
                        item.get("old_path"),
                        item["change_type"],
                        item.get("old_start"),
                        item.get("old_count"),
                        item.get("new_start"),
                        item.get("new_count"),
                        item["patch"],
                        item["patch_hash"],
                        item["source_locator"],
                        item["acl_ref"],
                        json.dumps(item.get("affected_symbols", [])),
                        item.get("raw_object_id"),
                        item["observed_at"],
                    )
                    for item in hunks
                ],
            )
            self._replace_history_edges(db, repository_id, commits, hunks)

    def _replace_history_edges(self, db, repository_id, commits, hunks) -> None:
        now = utc_now()
        evidence_ids = [item["id"] for item in commits] + [item["id"] for item in hunks]
        if evidence_ids:
            marks = ",".join("?" for _ in evidence_ids)
            db.execute(
                f"DELETE FROM platform_edges WHERE evidence_entity_id IN ({marks})",
                evidence_ids,
            )
        edges: list[tuple[Any, ...]] = []
        for commit in commits:
            for parent_sha in commit["parent_shas"]:
                parent_id = commit["id"].rsplit("/", 1)[0] + "/" + parent_sha
                edge_id = "edge://history/" + commit["sha"] + "/parent/" + parent_sha
                edges.append(
                    (
                        edge_id,
                        commit["project_id"],
                        commit["id"],
                        "parent",
                        parent_id,
                        commit["id"],
                        "deterministic",
                        1.0,
                        "confirmed",
                        now,
                        None,
                        "git-history-v1",
                        "{}",
                        now,
                        now,
                    )
                )
        for hunk in hunks:
            edge_id = (
                "edge://history/" + hunk["commit_sha"] + "/hunk/" + hunk["id"].rsplit("/", 1)[-1]
            )
            edges.append(
                (
                    edge_id,
                    hunk["project_id"],
                    hunk["commit_id"],
                    "contains_diff",
                    hunk["id"],
                    hunk["id"],
                    "deterministic",
                    1.0,
                    "confirmed",
                    now,
                    None,
                    "git-diff-v1",
                    "{}",
                    now,
                    now,
                )
            )
        db.executemany(
            """INSERT OR IGNORE INTO platform_edges
               (id, project_id, source_entity_id, predicate, target_entity_id,
                evidence_entity_id, derivation, confidence, review_status, valid_from,
                valid_to, rule_version, metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            edges,
        )

    def create_test_result(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO test_results
                   (id, project_id, repository_id, commit_id, commit_sha, command,
                    status, exit_code, duration_ms, stdout_ref, stderr_ref, framework,
                    metadata_json, acl_ref, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["project_id"],
                    record["repository_id"],
                    record.get("commit_id"),
                    record["commit_sha"],
                    record["command"],
                    record["status"],
                    record.get("exit_code"),
                    record.get("duration_ms"),
                    record.get("stdout_ref"),
                    record.get("stderr_ref"),
                    record.get("framework"),
                    json.dumps(record.get("metadata", {}), ensure_ascii=False),
                    record["acl_ref"],
                    now,
                ),
            )
            if record.get("commit_id"):
                db.execute(
                    """INSERT OR IGNORE INTO platform_edges
                       (id, project_id, source_entity_id, predicate, target_entity_id,
                        evidence_entity_id, derivation, confidence, review_status,
                        valid_from, rule_version, metadata_json, created_at, updated_at)
                       VALUES (?, ?, ?, 'validated_by', ?, ?, 'deterministic', 1.0,
                               'confirmed', ?, 'test-result-v1', '{}', ?, ?)""",
                    (
                        "edge://test/" + record["id"].rsplit("/", 1)[-1],
                        record["project_id"],
                        record["commit_id"],
                        record["id"],
                        record["id"],
                        now,
                        now,
                        now,
                    ),
                )
            row = db.execute("SELECT * FROM test_results WHERE id=?", (record["id"],)).fetchone()
        return self._test(row)

    def resolve_commit(self, repository_id: str, sha: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT * FROM git_commits
                   WHERE repository_id=? AND (sha=? OR sha LIKE ?)
                   ORDER BY length(sha) LIMIT 1""",
                (repository_id, sha, f"{sha}%"),
            ).fetchone()
        return self._commit(row) if row else None

    def list_commits(self, repository_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT c.*,
                          (SELECT count(*) FROM diff_hunks h WHERE h.commit_id=c.id) diff_count,
                          (SELECT count(*) FROM test_results t WHERE t.commit_id=c.id) test_count
                   FROM git_commits c WHERE repository_id=?
                   ORDER BY committed_at DESC LIMIT ?""",
                (repository_id, limit),
            ).fetchall()
        return [self._commit(row) for row in rows]

    def list_branches(self, repository_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM git_branches WHERE repository_id=? AND name!='origin'
                   ORDER BY is_default DESC, name COLLATE NOCASE""",
                (repository_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "is_default": bool(row["is_default"]),
            }
            for row in rows
        ]

    def commit_detail(self, repository_id: str, sha: str) -> dict[str, Any] | None:
        commit = self.resolve_commit(repository_id, sha)
        if not commit:
            return None
        with self.database.connection() as db:
            hunks = db.execute(
                "SELECT * FROM diff_hunks WHERE commit_id=? ORDER BY path, new_start",
                (commit["id"],),
            ).fetchall()
            tests = db.execute(
                "SELECT * FROM test_results WHERE commit_id=? ORDER BY observed_at DESC",
                (commit["id"],),
            ).fetchall()
        commit["diff_hunks"] = [self._hunk(row) for row in hunks]
        commit["test_results"] = [self._test(row) for row in tests]
        return commit

    @staticmethod
    def _commit(row) -> dict[str, Any]:
        item = dict(row)
        item["parent_shas"] = json.loads(item.pop("parent_shas_json") or "[]")
        return item

    @staticmethod
    def _hunk(row) -> dict[str, Any]:
        item = dict(row)
        item["affected_symbols"] = json.loads(item.pop("affected_symbols_json") or "[]")
        return item

    @staticmethod
    def _test(row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        return item
