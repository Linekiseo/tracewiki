from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..storage import SQLiteStore, utc_now
from .schema import SCHEMA


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(row: sqlite3.Row, *fields: str) -> dict[str, Any]:
    item = dict(row)
    for field in fields:
        item[field] = json.loads(item.pop(f"{field}_json") or "{}")
    return item


class EvaluationRunImmutableError(ValueError):
    pass


class EvaluationStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(SCHEMA)
            self._ensure_columns(
                db,
                "evaluation_cases",
                {
                    "expected_paths_json": "TEXT NOT NULL DEFAULT '[]'",
                    "expected_commit_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                    "forbidden_entity_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                },
            )
            self._ensure_columns(
                db,
                "evaluation_results",
                {
                    "evidence_path_recall": "REAL NOT NULL DEFAULT 1.0",
                    "commit_accuracy": "REAL NOT NULL DEFAULT 1.0",
                    "wrong_version_rate": "REAL NOT NULL DEFAULT 0.0",
                    "unauthorized_leakage": "REAL NOT NULL DEFAULT 0.0",
                },
            )
            self._ensure_columns(
                db,
                "evaluation_runs",
                {
                    "source_domain": "TEXT NOT NULL DEFAULT 'global'",
                    "runner_version": "TEXT NOT NULL DEFAULT 'evaluation-v1'",
                    "dataset_id": "TEXT",
                    "dataset_version": "TEXT",
                    "dataset_package_hash": "TEXT",
                    "graph_candidate_enabled": "INTEGER",
                    "paired_graph_off_run_id": "TEXT",
                    "snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
                    "config_json": "TEXT NOT NULL DEFAULT '{}'",
                    "failure_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            )
            self._ensure_columns(
                db,
                "evaluation_case_profiles",
                {
                    "dataset_id": "TEXT",
                    "dataset_version": "TEXT",
                    "dataset_package_hash": "TEXT",
                    "required_paths_json": "TEXT NOT NULL DEFAULT '[]'",
                    "acceptable_alternative_groups_json": "TEXT NOT NULL DEFAULT '[]'",
                },
            )
            self._ensure_columns(
                db,
                "evaluation_metric_values",
                {
                    "numerator": "REAL",
                    "denominator": "REAL",
                    "eligible": "INTEGER NOT NULL DEFAULT 1",
                    "total_cases": "INTEGER",
                    "eligible_cases": "INTEGER",
                    "available_cases": "INTEGER",
                    "unavailable_cases": "INTEGER",
                },
            )
            db.commit()

    @staticmethod
    def _ensure_columns(db: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    @staticmethod
    def _require_run_running(db: sqlite3.Connection, run_id: str) -> None:
        row = db.execute("SELECT status FROM evaluation_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise EvaluationRunImmutableError("evaluation run not found")
        if row["status"] != "running":
            raise EvaluationRunImmutableError(
                f"evaluation run is terminal and immutable: {row['status']}"
            )

    def create_case(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO evaluation_cases
                   (id, display_key, project_id, name, question, expected_sources_json,
                    expected_entity_ids_json, expected_paths_json,
                    expected_commit_ids_json, forbidden_entity_ids_json,
                    required_version, tags_json, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record["name"],
                    record["question"],
                    _json(record["expected_sources"]),
                    _json(record["expected_entity_ids"]),
                    _json(record["expected_paths"]),
                    _json(record["expected_commit_ids"]),
                    _json(record["forbidden_entity_ids"]),
                    record.get("required_version"),
                    _json(record["tags"]),
                    int(record["enabled"]),
                    now,
                    now,
                ),
            )
            profile = record.get("code_profile")
            if profile:
                db.execute(
                    """INSERT INTO evaluation_case_profiles
                       (case_id, source_domain, task, dataset_id, dataset_version,
                        dataset_package_hash, query_profile_json,
                        expected_unit_ids_json, expected_entity_types_json,
                        expected_locators_json, required_paths_json,
                        required_edge_types_json, acceptable_alternative_groups_json,
                        expected_context_roles_json, expected_ref, expected_answer_mode,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record["id"],
                        profile["source_domain"],
                        profile["task"],
                        profile.get("dataset_id"),
                        profile.get("dataset_version"),
                        profile.get("dataset_package_hash"),
                        _json(profile["query_profile"]),
                        _json(profile["expected_unit_ids"]),
                        _json(profile["expected_entity_types"]),
                        _json(profile["expected_locators"]),
                        _json(profile["required_paths"]),
                        _json(profile["required_edge_types"]),
                        _json(profile["acceptable_alternative_groups"]),
                        _json(profile["expected_context_roles"]),
                        profile.get("expected_ref"),
                        profile.get("expected_answer_mode"),
                        now,
                        now,
                    ),
                )
            for judgment in record.get("candidate_judgments", []):
                target = (
                    f"{judgment.get('entity_id') or ''}\0{judgment.get('retrieval_unit_id') or ''}"
                )
                judgment_id = hashlib.sha256(f"{record['id']}\0{target}".encode()).hexdigest()
                db.execute(
                    """INSERT INTO evaluation_candidate_judgments
                       (id, case_id, entity_id, retrieval_unit_id, relevance_grade,
                        necessity_role, note, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"evaluation-judgment://sha256:{judgment_id}",
                        record["id"],
                        judgment.get("entity_id"),
                        judgment.get("retrieval_unit_id"),
                        judgment["relevance_grade"],
                        judgment.get("necessity_role"),
                        judgment.get("note"),
                        now,
                        now,
                    ),
                )
        return self.get_case(record["id"]) or {}

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM evaluation_cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                return None
            item = self._decode_case(row)
            self._attach_code_contract(db, item)
        return item

    def list_cases(
        self, project_id: str, *, enabled_only: bool = False, case_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["project_id=?"]
        values: list[Any] = [project_id]
        if enabled_only:
            clauses.append("enabled=1")
        if case_ids:
            marks = ",".join("?" for _ in case_ids)
            clauses.append(f"id IN ({marks})")
            values.extend(case_ids)
        with self.database.connection() as db:
            rows = db.execute(
                f"SELECT * FROM evaluation_cases WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC",
                values,
            ).fetchall()
            items = [self._decode_case(row) for row in rows]
            for item in items:
                self._attach_code_contract(db, item)
        return items

    @staticmethod
    def _decode_case(row: sqlite3.Row) -> dict[str, Any]:
        return _decode(
            row,
            "expected_sources",
            "expected_entity_ids",
            "expected_paths",
            "expected_commit_ids",
            "forbidden_entity_ids",
            "tags",
        )

    @staticmethod
    def _attach_code_contract(db: sqlite3.Connection, item: dict[str, Any]) -> None:
        profile = db.execute(
            "SELECT * FROM evaluation_case_profiles WHERE case_id=?", (item["id"],)
        ).fetchone()
        if profile:
            decoded_profile = _decode(
                profile,
                "query_profile",
                "expected_unit_ids",
                "expected_entity_types",
                "expected_locators",
                "required_paths",
                "required_edge_types",
                "acceptable_alternative_groups",
                "expected_context_roles",
            )
            for field in ("case_id", "created_at", "updated_at"):
                decoded_profile.pop(field, None)
            item["code_profile"] = decoded_profile
        else:
            item["code_profile"] = None
        judgments = db.execute(
            """SELECT entity_id, retrieval_unit_id, relevance_grade, necessity_role, note
               FROM evaluation_candidate_judgments
               WHERE case_id=? ORDER BY entity_id, retrieval_unit_id""",
            (item["id"],),
        ).fetchall()
        item["candidate_judgments"] = [dict(row) for row in judgments]

    def create_run(
        self,
        run_id: str,
        display_key: str,
        project_id: str,
        case_count: int,
        *,
        source_domain: str = "global",
        runner_version: str = "evaluation-v1",
        dataset_id: str | None = None,
        dataset_version: str | None = None,
        dataset_package_hash: str | None = None,
        graph_candidate_enabled: bool | None = None,
        paired_graph_off_run_id: str | None = None,
        snapshot: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO evaluation_runs
                   (id, display_key, project_id, status, case_count, source_domain,
                    runner_version, dataset_id, dataset_version, dataset_package_hash,
                    graph_candidate_enabled, paired_graph_off_run_id,
                    snapshot_json, config_json, started_at)
                   VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    display_key,
                    project_id,
                    case_count,
                    source_domain,
                    runner_version,
                    dataset_id,
                    dataset_version,
                    dataset_package_hash,
                    (int(graph_candidate_enabled) if graph_candidate_enabled is not None else None),
                    paired_graph_off_run_id,
                    _json(snapshot or {}),
                    _json(config or {}),
                    utc_now(),
                ),
            )

    def update_run_context(
        self,
        run_id: str,
        *,
        snapshot: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        if snapshot is not None:
            fields.append("snapshot_json=?")
            values.append(_json(snapshot))
        if config is not None:
            fields.append("config_json=?")
            values.append(_json(config))
        if not fields:
            return
        values.append(run_id)
        with self.database.transaction() as db:
            self._require_run_running(db, run_id)
            db.execute(f"UPDATE evaluation_runs SET {', '.join(fields)} WHERE id=?", values)

    def add_result(self, record: dict[str, Any]) -> None:
        with self.database.transaction() as db:
            self._require_run_running(db, record["evaluation_run_id"])
            db.execute(
                """INSERT INTO evaluation_results
                   (id, evaluation_run_id, case_id, passed, source_recall, entity_recall,
                    citation_completeness, version_accuracy, evidence_path_recall,
                    commit_accuracy, wrong_version_rate, unauthorized_leakage, latency_ms,
                    result_entity_ids_json, detail_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["evaluation_run_id"],
                    record["case_id"],
                    int(record["passed"]),
                    record["source_recall"],
                    record["entity_recall"],
                    record["citation_completeness"],
                    record["version_accuracy"],
                    record["evidence_path_recall"],
                    record["commit_accuracy"],
                    record["wrong_version_rate"],
                    record["unauthorized_leakage"],
                    record["latency_ms"],
                    _json(record["result_entity_ids"]),
                    _json(record["detail"]),
                    utc_now(),
                ),
            )

    def add_metric_values(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        with self.database.transaction() as db:
            run_ids = {str(record["evaluation_run_id"]) for record in records}
            if len(run_ids) != 1:
                raise ValueError("metric batch must belong to exactly one evaluation run")
            self._require_run_running(db, next(iter(run_ids)))
            for record in records:
                slice_json = _json(record.get("slice", {}))
                digest = hashlib.sha256(
                    (
                        f"{record['evaluation_run_id']}\0{record.get('case_id') or ''}\0"
                        f"{record['source_domain']}\0{record['metric_name']}\0{slice_json}"
                    ).encode()
                ).hexdigest()
                db.execute(
                    """INSERT INTO evaluation_metric_values
                       (id, evaluation_run_id, case_id, source_domain, metric_name,
                        slice_json, value, status, unavailable_reason, numerator,
                        denominator, eligible, total_cases, eligible_cases,
                        available_cases, unavailable_cases, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        f"evaluation-metric://sha256:{digest}",
                        record["evaluation_run_id"],
                        record.get("case_id"),
                        record["source_domain"],
                        record["metric_name"],
                        slice_json,
                        record.get("value"),
                        record.get("status", "available"),
                        record.get("unavailable_reason"),
                        record.get("numerator"),
                        record.get("denominator"),
                        int(record.get("eligible", True)),
                        record.get("total_cases"),
                        record.get("eligible_cases"),
                        record.get("available_cases"),
                        record.get("unavailable_cases"),
                        utc_now(),
                    ),
                )

    def complete_run(self, run_id: str, summary: dict[str, Any]) -> None:
        with self.database.transaction() as db:
            self._require_run_running(db, run_id)
            cursor = db.execute(
                """UPDATE evaluation_runs SET status='completed', summary_json=?, completed_at=?
                   WHERE id=? AND status='running'""",
                (_json(summary), utc_now(), run_id),
            )
            if cursor.rowcount != 1:
                raise EvaluationRunImmutableError("evaluation run could not be completed")

    def fail_run(self, run_id: str, error: Exception) -> None:
        failure = {"type": type(error).__name__, "message": str(error)}
        with self.database.transaction() as db:
            self._require_run_running(db, run_id)
            cursor = db.execute(
                """UPDATE evaluation_runs
                   SET status='failed', failure_json=?, completed_at=?
                   WHERE id=? AND status='running'""",
                (_json(failure), utc_now(), run_id),
            )
            if cursor.rowcount != 1:
                raise EvaluationRunImmutableError("evaluation run could not be failed")

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM evaluation_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            results = db.execute(
                """SELECT r.*, c.display_key AS case_key, c.name, c.question
                   FROM evaluation_results r JOIN evaluation_cases c ON c.id=r.case_id
                   WHERE r.evaluation_run_id=? ORDER BY r.created_at""",
                (run_id,),
            ).fetchall()
            metrics = db.execute(
                """SELECT evaluation_run_id, case_id, source_domain, metric_name,
                          slice_json, value, status, unavailable_reason,
                          numerator, denominator, eligible, total_cases,
                          eligible_cases, available_cases, unavailable_cases
                   FROM evaluation_metric_values
                   WHERE evaluation_run_id=?
                   ORDER BY case_id, metric_name, slice_json""",
                (run_id,),
            ).fetchall()
        item = _decode(row, "summary", "snapshot", "config", "failure")
        if item.get("graph_candidate_enabled") is not None:
            item["graph_candidate_enabled"] = bool(item["graph_candidate_enabled"])
        item["results"] = [_decode(result, "result_entity_ids", "detail") for result in results]
        item["metric_values"] = [_decode(metric, "slice") for metric in metrics]
        for metric in item["metric_values"]:
            metric["eligible"] = bool(metric["eligible"])
        return item

    def list_runs(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM evaluation_runs WHERE project_id=?
                   ORDER BY started_at DESC LIMIT ?""",
                (project_id, limit),
            ).fetchall()
        items = [_decode(row, "summary", "snapshot", "config", "failure") for row in rows]
        for item in items:
            if item.get("graph_candidate_enabled") is not None:
                item["graph_candidate_enabled"] = bool(item["graph_candidate_enabled"])
        return items

    def code_snapshot(
        self,
        project_id: str,
        repository_ids: list[str] | None = None,
        *,
        dataset_id: str,
        dataset_version: str,
        dataset_package_hash: str,
        case_ids: list[str],
        evaluator_config: dict[str, Any],
    ) -> dict[str, Any]:
        clauses = ["r.project_id=?"]
        values: list[Any] = [project_id]
        if repository_ids:
            marks = ",".join("?" for _ in repository_ids)
            clauses.append(f"r.id IN ({marks})")
            values.extend(repository_ids)
        with self.database.connection() as db:
            repositories = db.execute(
                f"""SELECT r.id, r.head_commit, r.active_generation_id, r.updated_at,
                           g.commit_sha AS generation_commit,
                           g.parser_versions_json, g.counts_json, g.validation_json
                    FROM repositories r
                    LEFT JOIN index_generations g ON g.id=r.active_generation_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY r.id""",
                values,
            ).fetchall()
            schema_version = int(db.execute("PRAGMA schema_version").fetchone()[0])
        if repository_ids:
            found_repository_ids = {str(row["id"]) for row in repositories}
            missing_repository_ids = sorted(set(repository_ids) - found_repository_ids)
            if missing_repository_ids:
                raise RuntimeError(
                    "cannot snapshot unknown evaluation repositories: "
                    + ", ".join(missing_repository_ids)
                )
        repository_state = []
        for row in repositories:
            item = dict(row)
            for field in ("parser_versions", "counts", "validation"):
                item[field] = json.loads(item.pop(f"{field}_json") or "{}")
            repository_state.append(item)
        membership = sorted(case_ids)
        return {
            "kind": "code-evaluation-input-v2",
            "dataset": {
                "id": dataset_id,
                "version": dataset_version,
                "package_hash": dataset_package_hash,
            },
            "case_ids": membership,
            "case_membership_hash": self.fingerprint(membership),
            "schema_version": schema_version,
            "repositories": repository_state,
            "implementation_state": self.implementation_state(),
            "evaluator_config": evaluator_config,
            "observed_index_generations": [],
            "observed_retriever": {},
        }

    @staticmethod
    def fingerprint(value: Any) -> str:
        return "sha256:" + hashlib.sha256(_json(value).encode()).hexdigest()

    def finalize_code_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        observed_index_generations: list[str],
        observed_retriever: dict[str, Any],
    ) -> dict[str, Any]:
        finalized = deepcopy(snapshot)
        finalized["observed_index_generations"] = sorted(set(observed_index_generations))
        finalized["observed_retriever"] = observed_retriever
        finalized["snapshot_id"] = self.fingerprint(finalized)

        comparison = deepcopy(finalized)
        comparison.pop("snapshot_id", None)
        evaluator_config = dict(comparison.get("evaluator_config") or {})
        evaluator_config.pop("graph_candidate_enabled", None)
        evaluator_config.pop("paired_graph_off_run_id", None)
        comparison["evaluator_config"] = evaluator_config
        observed = dict(comparison.get("observed_retriever") or {})
        observed.pop("graph_candidate_enabled", None)
        comparison["observed_retriever"] = observed
        finalized["comparison_fingerprint"] = self.fingerprint(comparison)
        return finalized

    @staticmethod
    def implementation_state() -> dict[str, Any]:
        repository_root = Path(__file__).resolve().parents[3]

        def git(*args: str) -> bytes:
            completed = subprocess.run(
                ["git", "-C", str(repository_root), *args],
                check=True,
                capture_output=True,
            )
            return completed.stdout

        try:
            root = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve()
            head_commit = git("rev-parse", "HEAD").decode().strip()
            head_tree = git("rev-parse", "HEAD^{tree}").decode().strip()
            workspace_status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
            tracked_changes = git("diff", "--binary", "HEAD", "--")
            untracked_output = git("ls-files", "--others", "--exclude-standard", "-z")
        except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as exc:
            raise RuntimeError("unable to observe evaluator Git/workspace state") from exc

        untracked_digest = hashlib.sha256()
        for raw_relative in sorted(item for item in untracked_output.split(b"\0") if item):
            path = root / raw_relative.decode()
            if not path.is_file():
                continue
            untracked_digest.update(raw_relative)
            untracked_digest.update(b"\0")
            untracked_digest.update(path.read_bytes())
            untracked_digest.update(b"\0")
        workspace_state_digest = hashlib.sha256()
        workspace_state_digest.update(tracked_changes)
        workspace_state_digest.update(b"\0")
        workspace_state_digest.update(untracked_digest.digest())

        paths = sorted(
            [
                *(
                    path
                    for path in (root / "src/evidence_rag/evaluation").rglob("*")
                    if path.is_file() and "__pycache__" not in path.parts
                ),
                root / "tests/test_code_evaluation_v2.py",
            ],
            key=lambda path: path.as_posix(),
        )
        content_digest = hashlib.sha256()
        for path in paths:
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix().encode()
            content_digest.update(relative)
            content_digest.update(b"\0")
            content_digest.update(path.read_bytes())
            content_digest.update(b"\0")
        return {
            # The physical checkout path is operational metadata, not part of the
            # evaluator identity. Persisting it makes an otherwise identical run
            # differ across machines and can leak a developer or CI workspace.
            "repository_root": "<repository-root>",
            "head_commit": head_commit,
            "head_tree": head_tree,
            "workspace_dirty": bool(workspace_status),
            "workspace_status_hash": "sha256:" + hashlib.sha256(workspace_status).hexdigest(),
            "workspace_state_hash": "sha256:" + workspace_state_digest.hexdigest(),
            "c0_content_hash": "sha256:" + content_digest.hexdigest(),
        }

    def entity_types(self, entity_ids: list[str]) -> dict[str, str]:
        if not entity_ids:
            return {}
        marks = ",".join("?" for _ in entity_ids)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT id, entity_type FROM entities
                    WHERE id IN ({marks})""",
                entity_ids,
            ).fetchall()
        return {str(row["id"]): str(row["entity_type"]) for row in rows}

    def entity_acl_refs(self, entity_ids: list[str]) -> dict[str, str]:
        if not entity_ids:
            return {}
        marks = ",".join("?" for _ in entity_ids)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT id, acl_ref FROM entities
                    WHERE id IN ({marks})""",
                entity_ids,
            ).fetchall()
        return {str(row["id"]): str(row["acl_ref"]) for row in rows}

    def record_query(
        self, project_id: str, query: str, source_count: int, result_count: int, latency_ms: float
    ) -> None:
        from uuid import uuid4

        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO query_events
                   (id, project_id, query_text, source_count, result_count, latency_ms, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"query-event://{uuid4().hex}",
                    project_id,
                    query[:2_000],
                    source_count,
                    result_count,
                    latency_ms,
                    utc_now(),
                ),
            )

    def stats(self, project_id: str) -> dict[str, Any]:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT
                     (SELECT count(*) FROM evaluation_cases WHERE project_id=?) cases,
                     (SELECT count(*) FROM evaluation_runs WHERE project_id=?) runs,
                     (SELECT avg(json_extract(summary_json,'$.pass_rate')) FROM evaluation_runs
                      WHERE project_id=? AND status='completed') avg_pass_rate,
                     (SELECT avg(latency_ms) FROM query_events WHERE project_id=?) query_latency_ms,
                     (SELECT count(*) FROM query_events WHERE project_id=?) queries""",
                (project_id, project_id, project_id, project_id, project_id),
            ).fetchone()
        item = dict(row)
        item["cases"] = int(item["cases"] or 0)
        item["runs"] = int(item["runs"] or 0)
        item["queries"] = int(item["queries"] or 0)
        item["avg_pass_rate"] = round(float(item["avg_pass_rate"] or 0), 4)
        item["query_latency_ms"] = round(float(item["query_latency_ms"] or 0), 2)
        return item
