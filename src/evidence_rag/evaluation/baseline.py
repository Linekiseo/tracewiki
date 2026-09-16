from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ..config import Settings
from ..models import RepositoryIngestRequest
from ..retrieval import HybridRetriever
from ..runtime import Runtime, create_runtime
from ..security import secret_findings
from ..storage import SQLiteStore, utc_now
from ..workspace.models import ProjectCreate
from .golden import (
    ACL_REF,
    DATASET_ID,
    DATASET_VERSION,
    EXPECTED_CASE_COUNT,
    EXPECTED_ELIGIBLE_COUNT,
    EXPECTED_INELIGIBLE_COUNT,
    PACKAGE_HASH,
    PROJECT_ID,
    GoldenCase,
    GoldenPackage,
    LoadedGolden,
    evaluation_case_id,
    golden_adapter,
    golden_materializer,
    load_golden_cases,
    repository_root,
    validate_golden_package,
)
from .models import CodeEvaluationRunRequest
from .store import EvaluationStore

BASELINE_SCHEMA_VERSION = "code-c0-03-baseline-artifact-v1"
QUALIFICATION_SCHEMA_VERSION = "code-c0-03-qualification-record-v1"
QUALIFICATION_GENESIS_HASH = "sha256:" + ("0" * 64)
QUALIFICATION_DIRECTORY = "_qualification"
QUALIFICATION_AUTHORITY = "code-c0-03-controlled-local-ledger"
RUNNER_VERSION = "code-evaluation-v2.1"
BASELINE_CONFIG = {
    "retriever": "HybridRetriever",
    "fusion": "weighted-hybrid-v2",
    "embedding": "local-hash-v2",
    "views": ["file.raw", "symbol.raw"],
    "graph_candidate": False,
    "graph_post_expand": "record_separately",
    "top_k": 20,
}
REPORT_FILES = (
    "materialization.json",
    "coverage.json",
    "metrics.json",
    "slices.json",
    "error-analysis.json",
    "latency.json",
    "storage.json",
)
ARTIFACT_FILES = ("attempt-audit.json", "evaluation.sqlite3", *REPORT_FILES)
TEMPORARY_PATH_PATTERNS = (
    "/tmp",
    "/private/tmp",
    "/var/folders",
    "/private/var/folders",
)
TEMPORARY_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_<>])/(?:private/)?(?:tmp|var/folders)"
    r"(?:/[^\s`\"'<>),;\]]*)?"
)
PASSWORD_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b(?:password|passwd|client[_-]?secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b
    \s*[:=]\s*
    (?:
        (?P<quote>["'])(?P<quoted>[^"'\r\n]{8,})(?P=quote)
        |
        (?P<bare>[A-Za-z0-9._~+/\-=]{12,})
    )
    """
)
SECURITY_DECLARATION = {
    "credentials_included": False,
    "absolute_temporary_paths_included": False,
    "scanner_scope": "all SQLite TEXT columns and all artifact JSON files",
    "scanner_rule_ids": [
        "portable-system-temp-roots-v1",
        "high-confidence-credential-assignments-v1",
    ],
}
QUALIFICATION_THREAT_MODEL = {
    "protected_changes": (
        "Any manifest or artifact-map modification, alone or together, fails unless a new "
        "controlled qualification record is appended for the new canonical manifest digest."
    ),
    "trusted_inputs": [
        "released Code Golden v2 package anchor",
        "runner verification contract in this implementation",
        "append-only qualification record directory rooted at the fixed genesis hash",
    ],
    "out_of_scope": (
        "An actor able to rewrite the implementation and the complete qualification ledger "
        "history is out of scope because commits and cryptographic signing are prohibited "
        "for C0-03."
    ),
}


class BaselineError(RuntimeError):
    pass


@dataclass(slots=True)
class PreparedBaseline:
    work_root: Path
    database_path: Path
    runtime: Runtime
    package: GoldenPackage
    loaded: LoadedGolden
    sources: dict[str, Any]
    ingestion: tuple[dict[str, Any], ...]
    observed_views: tuple[str, ...]
    observed_embedding_models: tuple[str, ...]


class _ObservedHybridRetriever:
    """Baseline-only observation shim around the unchanged production retriever."""

    def __init__(self, retriever: HybridRetriever) -> None:
        if type(retriever) is not HybridRetriever:
            raise BaselineError("C0-03 requires the production HybridRetriever")
        self.retriever = retriever

    def search(self, request: Any) -> dict[str, Any]:
        return self.retriever.search(request)

    def search_evaluation(
        self,
        request: Any,
        *,
        graph_candidate_enabled: bool,
    ) -> dict[str, Any]:
        if graph_candidate_enabled:
            raise BaselineError("the C0-03 V1 baseline is graph-candidate-off")
        response = self.retriever.search(request)
        trace = dict(response.get("trace") or {})
        observed = {
            "retriever_version": type(self.retriever).__name__,
            "fusion": trace.get("fusion"),
            "embedding_model": trace.get("embedding_model"),
            "view_profile": ",".join(BASELINE_CONFIG["views"]),
            "baseline_config_version": "code-c0-03-v1",
            "graph_candidate_enabled": False,
        }
        trace.update(observed)
        return {**response, "trace": trace}


class _ReadOnlySQLiteStore(SQLiteStore):
    """EvaluationStore-compatible reader that never enables WAL on an artifact."""

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{self.database_path.resolve()}?mode=ro",
            uri=True,
            timeout=30,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BaselineError(f"artifact JSON must be an object: {path.name}")
    return value


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise BaselineError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BaselineError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BaselineError(f"{field} must include a timezone")
    return parsed


def _qualification_record_hash(record: dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "record_hash"}
    return _fingerprint(body)


def _qualification_dir(runs_dir: Path) -> Path:
    return runs_dir.resolve() / QUALIFICATION_DIRECTORY


def _read_qualification_ledger(directory: Path) -> list[dict[str, Any]]:
    records_dir = directory.resolve() / "records"
    if not records_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(records_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            raise BaselineError("qualification ledger contains an unexpected entry")
        record = _read_json(path)
        expected_hash = _qualification_record_hash(record)
        if (
            record.get("schema_version") != QUALIFICATION_SCHEMA_VERSION
            or record.get("record_hash") != expected_hash
            or path.name != f"{expected_hash.removeprefix('sha256:')}.json"
            or record.get("hash_scope") != "canonical record excluding record_hash"
            or record.get("authority") != QUALIFICATION_AUTHORITY
            or record.get("threat_model") != QUALIFICATION_THREAT_MODEL
            or record.get("decision") not in {"qualified", "revoked"}
        ):
            raise BaselineError("qualification record identity or policy is invalid")
        _parse_timestamp(record.get("recorded_at"), field="qualification recorded_at")
        records.append(record)

    records.sort(key=lambda item: int(item.get("sequence") or 0))
    previous = QUALIFICATION_GENESIS_HASH
    for expected_sequence, record in enumerate(records, 1):
        if (
            record.get("sequence") != expected_sequence
            or record.get("previous_record_hash") != previous
        ):
            raise BaselineError("qualification ledger is not one complete linear hash chain")
        previous = str(record["record_hash"])
    return records


def _latest_qualification_decisions(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for record in records:
        decisions[str(record["run_id"])] = record
    return decisions


def _write_content_addressed_record(
    ledger_dir: Path,
    record: dict[str, Any],
) -> Path:
    records_dir = ledger_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / f"{record['record_hash'].removeprefix('sha256:')}.json"
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != payload:
            raise BaselineError("content-addressed qualification record collision") from None
    return path


def append_qualification_record(
    runs_dir: Path,
    artifact_dir: Path,
    *,
    decision: str,
    reason_code: str,
) -> dict[str, Any]:
    if decision not in {"qualified", "revoked"}:
        raise BaselineError("qualification decision must be qualified or revoked")
    ledger_dir = _qualification_dir(runs_dir)
    records = _read_qualification_ledger(ledger_dir)
    manifest = _read_json(artifact_dir.resolve() / "manifest.json")
    run_id = str(manifest.get("run_id") or "")
    if not run_id:
        raise BaselineError("qualification subject has no Run identity")

    database_path = artifact_dir.resolve() / "evaluation.sqlite3"
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as database:
        row = database.execute("SELECT id FROM evaluation_runs").fetchall()
    if [str(item[0]) for item in row] != [run_id]:
        raise BaselineError("qualification subject differs from its embedded Run")

    latest = _latest_qualification_decisions(records)
    if decision == "qualified":
        active = sorted(
            existing_run_id
            for existing_run_id, record in latest.items()
            if record["decision"] == "qualified" and existing_run_id != run_id
        )
        if active:
            raise BaselineError(f"another baseline remains qualified: {active[0]}")
    previous_hash = records[-1]["record_hash"] if records else QUALIFICATION_GENESIS_HASH
    record = {
        "schema_version": QUALIFICATION_SCHEMA_VERSION,
        "hash_scope": "canonical record excluding record_hash",
        "sequence": len(records) + 1,
        "previous_record_hash": previous_hash,
        "recorded_at": utc_now(),
        "authority": QUALIFICATION_AUTHORITY,
        "decision": decision,
        "reason_code": reason_code,
        "run_id": run_id,
        "artifact_directory": artifact_dir.name,
        "manifest_canonical_hash": _fingerprint(manifest),
        "artifact_set_hash": manifest.get("artifact_set_hash"),
        "dataset_package_hash": (manifest.get("dataset") or {}).get("package_hash"),
        "config_fingerprint": manifest.get("config_fingerprint"),
        "threat_model": QUALIFICATION_THREAT_MODEL,
    }
    record["record_hash"] = _qualification_record_hash(record)
    _write_content_addressed_record(ledger_dir, record)
    return record


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BaselineError(f"unable to observe workspace with git: {' '.join(args)}") from exc
    return result.stdout


def _artifact_exclusions(root: Path, runs_dir: Path) -> list[str]:
    try:
        relative = runs_dir.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return []
    return [f":(exclude){relative}/**"]


def workspace_input_fingerprint(root: Path, runs_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    exclusions = _artifact_exclusions(root, runs_dir)
    pathspec = [".", *exclusions]
    head = _git(root, "rev-parse", "HEAD").decode().strip()
    tree = _git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    worktrees = _git(root, "worktree", "list", "--porcelain")
    status = _git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        *pathspec,
    )
    tracked = _git(root, "diff", "--binary", "HEAD", "--", *pathspec)
    untracked = _git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *pathspec,
    )
    untracked_digest = hashlib.sha256()
    for raw_relative in sorted(item for item in untracked.split(b"\0") if item):
        relative = raw_relative.decode("utf-8", errors="surrogateescape")
        path = root / relative
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        untracked_digest.update(raw_relative)
        untracked_digest.update(b"\0")
        untracked_digest.update(path.read_bytes())
        untracked_digest.update(b"\0")

    workspace_state = hashlib.sha256()
    workspace_state.update(tracked)
    workspace_state.update(b"\0")
    workspace_state.update(untracked_digest.digest())

    implementation_paths = [
        *(
            path
            for path in (root / "src" / "evidence_rag" / "evaluation").rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        ),
        root / "src/evidence_rag/repository.py",
        root / "src/evidence_rag/parser.py",
        root / "src/evidence_rag/ingestion.py",
        root / "src/evidence_rag/retrieval.py",
        root / "src/evidence_rag/storage.py",
        root / "src/evidence_rag/runtime.py",
        root / "evals/code/code_golden_v2.py",
        root / "tests/fixtures/code_golden/v2/materializer.py",
    ]
    implementation_digest = hashlib.sha256()
    for path in sorted(set(implementation_paths), key=lambda item: item.as_posix()):
        if not path.is_file():
            raise BaselineError(f"required C0 input is missing: {path.relative_to(root)}")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        implementation_digest.update(relative)
        implementation_digest.update(b"\0")
        implementation_digest.update(path.read_bytes())
        implementation_digest.update(b"\0")

    return {
        "head_commit": head,
        "head_tree": tree,
        "worktree_list_hash": _sha256_bytes(worktrees),
        "workspace_dirty": bool(status),
        "workspace_status_hash": _sha256_bytes(status),
        "workspace_state_hash": "sha256:" + workspace_state.hexdigest(),
        "implementation_input_hash": "sha256:" + implementation_digest.hexdigest(),
        "package_hash": PACKAGE_HASH,
    }


def require_unchanged_workspace(
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    if before != after:
        changed = sorted(
            key for key in set(before) | set(after) if before.get(key) != after.get(key)
        )
        raise BaselineError(
            "workspace input fingerprint drifted during the baseline"
            + (f": {', '.join(changed)}" if changed else "")
        )


def _create_project(runtime: Runtime) -> None:
    if runtime.workspace.store.get_project(PROJECT_ID):
        raise BaselineError("isolated baseline database unexpectedly already has the project")
    runtime.workspace.create_project(
        ProjectCreate(
            id=PROJECT_ID,
            name="Code Golden v2 Baseline",
            description="Isolated C0-03 offline evaluation project.",
            owner="RAG C0-03",
            acl_ref=ACL_REF,
            classification="internal",
        ),
        audit=False,
    )


def _workflow_observation(runtime: Runtime, workflow_id: str, source_class: str) -> dict[str, Any]:
    with runtime.store.connection() as database:
        row = database.execute(
            """SELECT status, stage, repository_id, generation_id, counters_json, error
               FROM workflows WHERE id=?""",
            (workflow_id,),
        ).fetchone()
    if row is None:
        raise BaselineError(f"missing ingestion workflow for {source_class}")
    item = dict(row)
    item["counters"] = json.loads(item.pop("counters_json") or "{}")
    if item["status"] != "completed" or item["error"]:
        raise BaselineError(f"production ingestion failed for {source_class}: {item['error']}")
    return {"source_class": source_class, **item}


def _verify_source_materialization(package: GoldenPackage, sources: dict[str, Any]) -> None:
    source_contracts = {
        str(item["source_class"]): item for item in package.source_manifest["source_classes"]
    }
    if set(sources) != set(source_contracts):
        raise BaselineError("materializer did not produce exactly the three released sources")
    adapter = golden_adapter(package.repository_root)
    leakage = package.source_manifest["leakage_policy"]
    for source_class, source in sources.items():
        contract = source_contracts[source_class]
        if (
            source.repository_id != contract["repository_id"]
            or source.remote_url != contract["remote_url"]
            or source.resolved_ref != contract["active_ref"]
            or bool(source.dirty) != bool(contract["dirty"])
        ):
            raise BaselineError(f"materialized source identity drift: {source_class}")
        findings = adapter.scan_ingest_root(
            Path(source.path),
            forbidden_literals=leakage["ingest_content_forbidden_literals"],
            reject_fixture_metadata_paths=source_class
            in {"controlled_multilingual", "historical_error"},
        )
        if findings:
            raise BaselineError(f"Golden metadata leaked into {source_class}: {findings[0]}")


def prepare_baseline_environment(
    work_root: Path,
    *,
    root: Path | None = None,
) -> PreparedBaseline:
    resolved_root = (root or repository_root()).resolve()
    work_root = work_root.resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    if any(work_root.iterdir()):
        raise BaselineError("baseline work root must be empty")

    package = validate_golden_package(resolved_root)
    materializer = golden_materializer(resolved_root)
    sources = materializer.materialize_all(work_root / "materialized", resolved_root)
    _verify_source_materialization(package, sources)

    data_dir = work_root / "runtime"
    database_path = data_dir / "code-c0-03.sqlite3"
    production_database = (resolved_root / "var" / "evidence-rag.sqlite3").resolve()
    if database_path.resolve() == production_database:
        raise BaselineError("C0-03 may not use var/evidence-rag.sqlite3")
    settings = Settings(
        data_dir=data_dir,
        database_path=database_path,
        repository_cache=work_root / "repository-cache",
        web_dir=resolved_root / "web",
        allowed_local_roots=(work_root, resolved_root),
        project_root=resolved_root,
        embedding_dimensions=384,
        enforce_acl=True,
    )
    runtime = create_runtime(settings)
    _create_project(runtime)

    ingestion_observations: list[dict[str, Any]] = []
    for source_class, source in sorted(sources.items()):
        history_depth = 1 if source_class == "current_project_snapshot" else 3
        snapshot = runtime.ingestion.resolver.resolve(
            source.path,
            project_id=PROJECT_ID,
            acl_ref=ACL_REF,
            history_depth=history_depth,
        )
        if (
            snapshot.id != source.repository_id
            or snapshot.head_commit != source.resolved_ref
            or snapshot.base_commit != source.base_commit
            or snapshot.dirty is not source.dirty
        ):
            raise BaselineError(f"RepositoryResolver identity mismatch: {source_class}")
        request = RepositoryIngestRequest(
            source=source.path,
            project_id=PROJECT_ID,
            acl_ref=ACL_REF,
            ignore=["generated", "vendor"],
            history_depth=history_depth,
        )
        workflow_id = runtime.ingestion.enqueue(request)
        runtime.ingestion.run(workflow_id, request)
        observation = _workflow_observation(runtime, workflow_id, source_class)
        if observation["repository_id"] != source.repository_id:
            raise BaselineError(f"ingestion repository mismatch: {source_class}")
        ingestion_observations.append(observation)

    with runtime.store.connection() as database:
        repositories = database.execute(
            """SELECT id, head_commit, active_generation_id, status
               FROM repositories WHERE project_id=? ORDER BY id""",
            (PROJECT_ID,),
        ).fetchall()
        views = database.execute(
            """SELECT DISTINCT sv.view_type
               FROM search_views sv JOIN repositories r ON r.id=sv.repository_id
               WHERE r.project_id=? AND sv.generation_id=r.active_generation_id
               ORDER BY sv.view_type""",
            (PROJECT_ID,),
        ).fetchall()
        models = database.execute(
            """SELECT DISTINCT sv.embedding_model
               FROM search_views sv JOIN repositories r ON r.id=sv.repository_id
               WHERE r.project_id=? AND sv.generation_id=r.active_generation_id
               ORDER BY sv.embedding_model""",
            (PROJECT_ID,),
        ).fetchall()
    if len(repositories) != 3 or any(row["status"] != "ready" for row in repositories):
        raise BaselineError("the isolated database does not have three ready repositories")
    observed_views = tuple(str(row["view_type"]) for row in views)
    observed_models = tuple(str(row["embedding_model"]) for row in models)
    if observed_views != tuple(BASELINE_CONFIG["views"]):
        raise BaselineError(f"unexpected production Search Views: {observed_views}")
    if observed_models != (str(BASELINE_CONFIG["embedding"]),):
        raise BaselineError(f"unexpected production embedding model: {observed_models}")

    loaded = load_golden_cases(runtime.evaluation.store, package)
    if (
        len(package.cases) != EXPECTED_CASE_COUNT
        or len(loaded.case_ids) != EXPECTED_ELIGIBLE_COUNT
        or len(package.ineligible_cases) != EXPECTED_INELIGIBLE_COUNT
    ):
        raise BaselineError("Golden loader failed the 50/33/17 membership contract")
    return PreparedBaseline(
        work_root=work_root,
        database_path=database_path,
        runtime=runtime,
        package=package,
        loaded=loaded,
        sources=sources,
        ingestion=tuple(ingestion_observations),
        observed_views=observed_views,
        observed_embedding_models=observed_models,
    )


def _case_by_evaluation_id(package: GoldenPackage) -> dict[str, GoldenCase]:
    return {f"evaluation-case://{PROJECT_ID}/{case.canonical_id}": case for case in package.cases}


def _metric_map(run: dict[str, Any]) -> dict[tuple[str | None, str, str], dict[str, Any]]:
    return {
        (
            item.get("case_id"),
            str(item["metric_name"]),
            json.dumps(item.get("slice") or {}, sort_keys=True, separators=(",", ":")),
        ): item
        for item in run["metric_values"]
    }


def _case_metric(
    metrics: dict[tuple[str | None, str, str], dict[str, Any]],
    case_id: str,
    name: str,
    slice_value: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    key = json.dumps(slice_value or {}, sort_keys=True, separators=(",", ":"))
    return metrics.get((case_id, name, key))


def _metric_value(metric: dict[str, Any] | None) -> float | None:
    if not metric or metric.get("status") != "available":
        return None
    return float(metric["value"])


def _coverage_report(run: dict[str, Any], package: GoldenPackage) -> dict[str, Any]:
    ineligible = [
        {
            "case_id": case.canonical_id,
            "task": case.request.code_profile.task if case.request.code_profile else None,
            "source_class": case.source_class,
            "reason": case.unavailable_reason,
            "smoke": case.smoke,
            "validation_targets": [
                {
                    "target_id": target["target_id"],
                    "treatment_status": target["treatment_status"],
                    "unavailable_reason": target["unavailable_reason"],
                }
                for target in case.validation_targets
            ],
        }
        for case in package.ineligible_cases
    ]
    return {
        "schema_version": "code-c0-03-coverage-v1",
        "run_id": run["id"],
        "dataset": {
            "id": DATASET_ID,
            "version": DATASET_VERSION,
            "package_hash": PACKAGE_HASH,
        },
        "total_cases": EXPECTED_CASE_COUNT,
        "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
        "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
        "result_count": len(run["results"]),
        "enabled_case_names": [case.canonical_id for case in package.eligible_cases],
        "eligible_distribution": {
            "task": {
                task: sum(
                    case.request.code_profile is not None and case.request.code_profile.task == task
                    for case in package.eligible_cases
                )
                for task in sorted(
                    {
                        case.request.code_profile.task
                        for case in package.eligible_cases
                        if case.request.code_profile is not None
                    }
                )
            },
            "language": {
                language: sum(case.query_language == language for case in package.eligible_cases)
                for language in sorted({case.query_language for case in package.eligible_cases})
            },
            "query_style": {
                style: sum(case.query_style == style for case in package.eligible_cases)
                for style in sorted({case.query_style for case in package.eligible_cases})
            },
        },
        "rich_path_inventory": {
            "catalog_available": sum(
                item["availability"] == "available"
                for item in package.source_manifest["paths"].values()
            ),
            "catalog_unavailable": sum(
                item["availability"] == "unavailable"
                for item in package.source_manifest["paths"].values()
            ),
            "eligible_cases_with_rich_paths": sum(
                bool(case.request.code_profile and case.request.code_profile.required_paths)
                for case in package.eligible_cases
            ),
        },
        "ineligible": ineligible,
        "unavailable_capabilities": [
            {
                "capability": "versioned retrieval-unit identity",
                "status": "unavailable",
                "affected_eligible_cases": EXPECTED_ELIGIBLE_COUNT,
                "reason": "Code Golden v2 has no C2 retrieval-unit annotations.",
            },
            {
                "capability": "paired graph-only recovery",
                "status": "unavailable",
                "affected_positive_eligible_cases": 29,
                "not_applicable_cases": 4,
                "reason": "C0-03 is an unpaired graph-off baseline.",
            },
            {
                "capability": "stable validation artifact retrieval",
                "status": "unavailable",
                "affected_ineligible_cases": 7,
                "reason": "Validation observations are materialized but not indexed as TestResult targets.",
            },
            {
                "capability": "historical non-active CodeSymbol retrieval and tag resolution",
                "status": "unavailable",
                "affected_ineligible_cases": 10,
                "reason": "C0-01 only retrieves the active generation and does not resolve Git tags.",
            },
        ],
    }


def _metrics_report(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "code-c0-03-metrics-v1",
        "run_id": run["id"],
        "authoritative_contract": "evaluation_metric_values",
        "summary": run["summary"],
        "metric_values": run["metric_values"],
    }


def _materialization_report(run: dict[str, Any]) -> dict[str, Any]:
    observation = run.get("config", {}).get("declared", {}).get("materialization_observation")
    if not isinstance(observation, dict):
        raise BaselineError("Run does not retain the materialization observation")
    return observation


def _slices_report(run: dict[str, Any], package: GoldenPackage) -> dict[str, Any]:
    aggregates = [item for item in run["metric_values"] if item.get("case_id") is None]
    cases = _case_by_evaluation_id(package)
    case_metrics = [item for item in run["metric_values"] if item.get("case_id") is not None]

    def aggregate(dimension: str, value: str, case_ids: set[str]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in case_metrics:
            if item["case_id"] not in case_ids:
                continue
            metric_slice = json.dumps(
                item.get("slice") or {},
                sort_keys=True,
                separators=(",", ":"),
            )
            grouped.setdefault((str(item["metric_name"]), metric_slice), []).append(item)
        output: list[dict[str, Any]] = []
        for (name, raw_slice), items in sorted(grouped.items()):
            eligible = [item for item in items if item.get("eligible", True)]
            available = [item for item in eligible if item["status"] == "available"]
            record: dict[str, Any] = {
                "source_domain": "code",
                "metric_name": name,
                "slice": {
                    dimension: value,
                    **json.loads(raw_slice),
                },
                "total_cases": len(case_ids),
                "eligible_cases": len(eligible),
                "available_cases": len(available),
                "unavailable_cases": len(eligible) - len(available),
                "eligible": bool(eligible),
            }
            if available:
                numerators = [item.get("numerator") for item in available]
                denominators = [item.get("denominator") for item in available]
                if all(item is not None for item in [*numerators, *denominators]):
                    numerator = sum(float(item) for item in numerators)
                    denominator = sum(float(item) for item in denominators)
                    metric_value = (
                        numerator / denominator
                        if denominator
                        else sum(float(item["value"]) for item in available) / len(available)
                    )
                else:
                    numerator = sum(float(item["value"]) for item in available)
                    denominator = float(len(available))
                    metric_value = numerator / denominator
                record.update(
                    status="available",
                    value=metric_value,
                    numerator=numerator,
                    denominator=denominator,
                    unavailable_reason=None,
                )
            else:
                record.update(
                    status="unavailable",
                    value=None,
                    numerator=0.0,
                    denominator=0.0,
                    unavailable_reason=(
                        "metric unavailable for every eligible case in this derived slice"
                        if eligible
                        else "metric is not applicable to this derived slice"
                    ),
                )
            output.append(record)
        latency_values = sorted(
            float(item["value"])
            for item in case_metrics
            if item["case_id"] in case_ids
            and item["metric_name"] == "latency_ms"
            and not item.get("slice")
            and item["status"] == "available"
        )
        for name, fraction in (("latency_p50_ms", 0.50), ("latency_p95_ms", 0.95)):
            latency_record: dict[str, Any] = {
                "source_domain": "code",
                "metric_name": name,
                "slice": {dimension: value},
                "total_cases": len(case_ids),
                "eligible_cases": len(case_ids),
                "available_cases": len(latency_values),
                "unavailable_cases": len(case_ids) - len(latency_values),
                "eligible": bool(case_ids),
            }
            if latency_values:
                position = (len(latency_values) - 1) * fraction
                lower = int(position)
                upper = min(lower + 1, len(latency_values) - 1)
                weight = position - lower
                percentile = latency_values[lower] * (1.0 - weight) + latency_values[upper] * weight
                latency_record.update(
                    status="available",
                    value=percentile,
                    numerator=percentile,
                    denominator=1.0,
                    unavailable_reason=None,
                )
            else:
                latency_record.update(
                    status="unavailable",
                    value=None,
                    numerator=0.0,
                    denominator=0.0,
                    unavailable_reason="no case in this derived slice exposes latency",
                )
            output.append(latency_record)
        return output

    derived: list[dict[str, Any]] = []
    eligible_cases = {case_id: case for case_id, case in cases.items() if case.eligible}
    dimensions = {
        "language": sorted({case.query_language for case in eligible_cases.values()}),
        "query_style": sorted({case.query_style for case in eligible_cases.values()}),
        "golden_slice": sorted(
            {slice_name for case in eligible_cases.values() for slice_name in case.slices}
        ),
    }
    for dimension, values in dimensions.items():
        for value in values:
            if dimension == "language":
                members = {
                    case_id
                    for case_id, case in eligible_cases.items()
                    if case.query_language == value
                }
            elif dimension == "query_style":
                members = {
                    case_id for case_id, case in eligible_cases.items() if case.query_style == value
                }
            else:
                members = {
                    case_id for case_id, case in eligible_cases.items() if value in case.slices
                }
            derived.extend(aggregate(dimension, value, members))
    return {
        "schema_version": "code-c0-03-slices-v1",
        "run_id": run["id"],
        "slice_dimensions": ["overall", "task", "language", "query_style", "golden_slice"],
        "authoritative_c0_01_aggregates": aggregates,
        "derived_language_style_and_golden_slice_aggregates": derived,
    }


def _error_analysis_report(run: dict[str, Any], package: GoldenPackage) -> dict[str, Any]:
    cases = _case_by_evaluation_id(package)
    metrics = _metric_map(run)
    result_by_case = {str(result["case_id"]): result for result in run["results"]}
    misses: list[dict[str, Any]] = []
    wrong_or_missing: list[dict[str, Any]] = []
    harmful: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    zero_results: list[dict[str, Any]] = []

    for case_id, result in sorted(result_by_case.items()):
        case = cases[case_id]
        entity_recall = _metric_value(_case_metric(metrics, case_id, "entity_recall@20"))
        mrr = _metric_value(_case_metric(metrics, case_id, "mrr@10"))
        path_recall = _metric_value(_case_metric(metrics, case_id, "required_path_recall"))
        hard_negative = _metric_value(_case_metric(metrics, case_id, "hard_negative_error@10"))
        duplicate_rate = _metric_value(_case_metric(metrics, case_id, "entity_duplicate_rate@10"))
        missing_version = _case_metric(metrics, case_id, "missing_version_rate")
        wrong_version = _case_metric(metrics, case_id, "wrong_version_rate")
        candidate_count = int(result["detail"].get("candidate_count") or 0)
        answer_mode = (
            case.request.code_profile.expected_answer_mode
            if case.request.code_profile
            else "direct"
        )
        unexpected_zero_result = candidate_count == 0 and answer_mode == "direct"
        miss_score = sum(
            (
                1.0 - entity_recall if entity_recall is not None else 0.0,
                1.0 - mrr if mrr is not None else 0.0,
                1.0 - path_recall if path_recall is not None else 0.0,
                hard_negative or 0.0,
                float(unexpected_zero_result),
                float(not result["passed"]),
            )
        )
        record = {
            "case_id": case.canonical_id,
            "task": case.request.code_profile.task if case.request.code_profile else None,
            "query_language": case.query_language,
            "query_style": case.query_style,
            "slices": list(case.slices),
            "expected_answer_mode": answer_mode,
            "passed": bool(result["passed"]),
            "miss_score": round(miss_score, 6),
            "entity_recall@20": entity_recall,
            "mrr@10": mrr,
            "required_path_recall": path_recall,
            "candidate_count": candidate_count,
            "result_entity_ids": result["result_entity_ids"],
        }
        if not result["passed"]:
            misses.append(record)
        if missing_version and missing_version["status"] != "unavailable":
            missing_value = float(missing_version["value"])
        else:
            missing_value = None
        if wrong_version and wrong_version["status"] != "unavailable":
            wrong_value = float(wrong_version["value"])
        else:
            wrong_value = None
        version_unavailable = any(
            metric is not None and metric["status"] == "unavailable"
            for metric in (missing_version, wrong_version)
        )
        if (missing_value or 0.0) > 0 or (wrong_value or 0.0) > 0 or version_unavailable:
            wrong_or_missing.append(
                {
                    "case_id": case.canonical_id,
                    "missing_version_rate": missing_value,
                    "missing_version_status": (
                        missing_version["status"] if missing_version else None
                    ),
                    "missing_version_unavailable_reason": (
                        missing_version.get("unavailable_reason") if missing_version else None
                    ),
                    "wrong_version_rate": wrong_value,
                    "wrong_version_status": wrong_version["status"] if wrong_version else None,
                    "wrong_version_unavailable_reason": (
                        wrong_version.get("unavailable_reason") if wrong_version else None
                    ),
                }
            )
        if (hard_negative or 0.0) > 0:
            harmful.append(record)
        if (duplicate_rate or 0.0) > 0:
            duplicates.append({**record, "entity_duplicate_rate@10": duplicate_rate})
        if candidate_count == 0:
            zero_results.append(record)

    misses.sort(key=lambda item: (-float(item["miss_score"]), str(item["case_id"])))
    same_name = [item for item in misses if "same_name_hard_negative" in item["slices"]]
    graph_required = [
        item
        for item in misses
        if "graph_required" in item["slices"] and item["expected_answer_mode"] == "direct"
    ]
    channel_metrics = [
        item
        for item in run["metric_values"]
        if item.get("case_id") is None
        and item["metric_name"] == "candidate_count_by_channel"
        and item["slice"].get("scope") == "overall"
    ]
    return {
        "schema_version": "code-c0-03-error-analysis-v1",
        "run_id": run["id"],
        "top_misses": misses[:20],
        "same_name_errors": same_name,
        "wrong_or_missing_version": wrong_or_missing,
        "graph_required_misses": graph_required,
        "harmful_candidates": harmful,
        "duplicate_candidates": duplicates,
        "zero_results": zero_results,
        "candidate_channels": channel_metrics,
    }


def _aggregate_metric(
    run: dict[str, Any],
    name: str,
    slice_value: dict[str, Any],
) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in run["metric_values"]
            if item.get("case_id") is None
            and item["metric_name"] == name
            and item["slice"] == slice_value
        ),
        None,
    )


def _latency_report(run: dict[str, Any]) -> dict[str, Any]:
    case_latency = [
        {
            "case_id": result["name"],
            "latency_ms": result["latency_ms"],
            "candidate_count": result["detail"].get("candidate_count"),
            "dense_candidates": result["detail"].get("trace", {}).get("dense_candidates"),
            "dense_matches": result["detail"].get("trace", {}).get("dense_matches"),
        }
        for result in run["results"]
    ]
    return {
        "schema_version": "code-c0-03-latency-v1",
        "run_id": run["id"],
        "p50": _aggregate_metric(run, "latency_p50_ms", {"scope": "overall"}),
        "p95": _aggregate_metric(run, "latency_p95_ms", {"scope": "overall"}),
        "dense_full_scan_time_ms": {
            "status": "unavailable",
            "reason": (
                "HybridRetriever v1 exposes dense candidate and match counts but does not "
                "separately time the dense full scan."
            ),
        },
        "cases": case_latency,
    }


def _storage_report(database_path: Path, run_id: str) -> dict[str, Any]:
    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        page_size = int(database.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(database.execute("PRAGMA page_count").fetchone()[0])
        free_pages = int(database.execute("PRAGMA freelist_count").fetchone()[0])
        active_views = database.execute(
            """SELECT sv.view_type, count(*) AS rows,
                      coalesce(sum(length(sv.content)), 0) AS content_bytes,
                      coalesce(sum(length(sv.vector)), 0) AS vector_bytes
               FROM search_views sv JOIN repositories r ON r.id=sv.repository_id
               WHERE r.project_id=? AND sv.generation_id=r.active_generation_id
               GROUP BY sv.view_type ORDER BY sv.view_type""",
            (PROJECT_ID,),
        ).fetchall()
        repositories = database.execute(
            """SELECT r.id, r.head_commit, r.active_generation_id,
                      g.parser_versions_json, g.counts_json
               FROM repositories r
               LEFT JOIN index_generations g ON g.id=r.active_generation_id
               WHERE r.project_id=? ORDER BY r.id""",
            (PROJECT_ID,),
        ).fetchall()
        physical_index_bytes: int | None
        try:
            physical_index_bytes = int(
                database.execute(
                    """SELECT coalesce(sum(pgsize), 0) FROM dbstat
                       WHERE name LIKE 'search_views%' OR name='idx_views_active'"""
                ).fetchone()[0]
            )
        except sqlite3.DatabaseError:
            physical_index_bytes = None

    view_records = [dict(row) for row in active_views]
    logical_bytes = sum(
        int(item["content_bytes"]) + int(item["vector_bytes"]) for item in view_records
    )
    repository_records = []
    for row in repositories:
        item = dict(row)
        item["parser_versions"] = json.loads(item.pop("parser_versions_json") or "{}")
        item["counts"] = json.loads(item.pop("counts_json") or "{}")
        repository_records.append(item)
    return {
        "schema_version": "code-c0-03-storage-v1",
        "run_id": run_id,
        "database_bytes": database_path.stat().st_size,
        "sqlite_page_size": page_size,
        "sqlite_page_count": page_count,
        "sqlite_free_pages": free_pages,
        "sqlite_free_bytes": page_size * free_pages,
        "active_code_index_logical_bytes": logical_bytes,
        "active_code_index_physical_bytes": (
            {
                "status": "available",
                "value": physical_index_bytes,
                "source": "SQLite dbstat",
            }
            if physical_index_bytes is not None
            else {
                "status": "unavailable",
                "reason": "SQLite dbstat is not available in this runtime.",
            }
        ),
        "active_search_views": view_records,
        "active_repositories": repository_records,
    }


def _report_payloads(
    run: dict[str, Any],
    package: GoldenPackage,
    database_path: Path,
) -> dict[str, dict[str, Any]]:
    return {
        "materialization.json": _materialization_report(run),
        "coverage.json": _coverage_report(run, package),
        "metrics.json": _metrics_report(run),
        "slices.json": _slices_report(run, package),
        "error-analysis.json": _error_analysis_report(run, package),
        "latency.json": _latency_report(run),
        "storage.json": _storage_report(database_path, run["id"]),
    }


def _safe_identifier(value: str) -> str:
    return value.replace('"', '""')


def _sanitize_text(value: str, forbidden_root: Path | None) -> str:
    sanitized = value
    if forbidden_root is not None:
        sanitized = sanitized.replace(
            str(forbidden_root.resolve()),
            "<system-temp>/code-c0-03",
        )
    return TEMPORARY_PATH_RE.sub("<redacted-temp-path>", sanitized)


def _credential_categories(value: str) -> list[str]:
    categories = list(secret_findings(value))
    identifier_values = {
        "password",
        "passwd",
        "token",
        "secret",
        "api_key",
        "access_token",
        "request.password",
        "request.token",
        "config.password",
        "config.token",
    }
    for match in PASSWORD_ASSIGNMENT_RE.finditer(value):
        assigned = str(match.group("quoted") or match.group("bare") or "").strip().lower()
        if assigned in identifier_values or assigned.startswith(("${", "<", "os.environ")):
            continue
        categories.append("credential_assignment")
        break
    return sorted(set(categories))


def _database_security_scan(database_path: Path) -> dict[str, Any]:
    path_findings: list[dict[str, Any]] = []
    credential_findings: list[dict[str, Any]] = []
    column_count = 0
    value_count = 0
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        tables = database.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        ).fetchall()
        for (raw_table,) in tables:
            table = str(raw_table)
            safe_table = _safe_identifier(table)
            columns = database.execute(f'PRAGMA table_info("{safe_table}")').fetchall()
            for column in columns:
                name = str(column[1])
                if "TEXT" not in str(column[2] or "").upper():
                    continue
                column_count += 1
                safe_column = _safe_identifier(name)
                values = database.execute(
                    f"""SELECT rowid, "{safe_column}" FROM "{safe_table}"
                        WHERE "{safe_column}" IS NOT NULL"""
                ).fetchall()
                for rowid, raw_value in values:
                    value_count += 1
                    value = str(raw_value)
                    if TEMPORARY_PATH_RE.search(value):
                        path_findings.append({"table": table, "column": name, "rowid": int(rowid)})
                    categories = _credential_categories(value)
                    if categories:
                        credential_findings.append(
                            {
                                "table": table,
                                "column": name,
                                "rowid": int(rowid),
                                "categories": categories,
                            }
                        )
    return {
        "text_columns_scanned": column_count,
        "text_values_scanned": value_count,
        "temporary_path_findings": path_findings,
        "credential_findings": credential_findings,
    }


def scan_artifact_security(directory: Path) -> dict[str, Any]:
    directory = directory.resolve()
    database_path = directory / "evaluation.sqlite3"
    database_scan = _database_security_scan(database_path)
    json_path_findings: list[str] = []
    json_credential_findings: list[dict[str, Any]] = []
    json_files = sorted(path for path in directory.rglob("*.json") if path.is_file())
    for path in json_files:
        content = path.read_text(encoding="utf-8")
        relative = path.relative_to(directory).as_posix()
        if TEMPORARY_PATH_RE.search(content):
            json_path_findings.append(relative)
        categories = _credential_categories(content)
        if categories:
            json_credential_findings.append({"file": relative, "categories": categories})
    temporary_count = len(database_scan["temporary_path_findings"]) + len(json_path_findings)
    credential_count = len(database_scan["credential_findings"]) + len(json_credential_findings)
    return {
        "schema_version": "code-c0-03-security-scan-v1",
        "scope": SECURITY_DECLARATION["scanner_scope"],
        "sqlite_text_columns_scanned": database_scan["text_columns_scanned"],
        "sqlite_text_values_scanned": database_scan["text_values_scanned"],
        "json_files_scanned": len(json_files),
        "temporary_path_findings": temporary_count,
        "credential_assignment_findings": credential_count,
        "clean": temporary_count == 0 and credential_count == 0,
        "finding_locations": {
            "sqlite_temporary_paths": database_scan["temporary_path_findings"],
            "json_temporary_paths": json_path_findings,
            "sqlite_credentials": database_scan["credential_findings"],
            "json_credentials": json_credential_findings,
        },
    }


def _require_clean_security_scan(directory: Path) -> dict[str, Any]:
    scan = scan_artifact_security(directory)
    if not scan["clean"]:
        raise BaselineError(
            "artifact security scan failed: "
            f"temporary_paths={scan['temporary_path_findings']}, "
            f"credential_assignments={scan['credential_assignment_findings']}"
        )
    return scan


def _sanitize_database(database_path: Path, forbidden_root: Path | None) -> None:
    changed_diff_rows: dict[int, tuple[str, str | None]] = {}
    with sqlite3.connect(database_path) as database:
        database.row_factory = sqlite3.Row
        table_names = {
            str(row["name"])
            for row in database.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'
                   ORDER BY name"""
            )
        }
        if "diff_hunks" in table_names:
            for row in database.execute(
                "SELECT rowid, patch, raw_object_id FROM diff_hunks"
            ).fetchall():
                patch = str(row["patch"])
                sanitized_patch = _sanitize_text(patch, forbidden_root)
                if sanitized_patch != patch:
                    changed_diff_rows[int(row["rowid"])] = (
                        sanitized_patch,
                        str(row["raw_object_id"]) if row["raw_object_id"] else None,
                    )

        for table in sorted(table_names):
            safe_table = _safe_identifier(table)
            columns = database.execute(f'PRAGMA table_info("{safe_table}")').fetchall()
            for column in columns:
                name = str(column[1])
                if "TEXT" not in str(column[2] or "").upper():
                    continue
                safe_column = _safe_identifier(name)
                rows = database.execute(
                    f"""SELECT rowid, "{safe_column}" FROM "{safe_table}"
                        WHERE "{safe_column}" IS NOT NULL"""
                ).fetchall()
                for row in rows:
                    value = str(row[safe_column])
                    sanitized = _sanitize_text(value, forbidden_root)
                    if sanitized != value:
                        database.execute(
                            f"""UPDATE "{safe_table}" SET "{safe_column}"=?
                                WHERE rowid=?""",
                            (sanitized, int(row["rowid"])),
                        )

        for rowid, (patch, raw_object_id) in changed_diff_rows.items():
            patch_bytes = patch.encode("utf-8")
            patch_hash = _sha256_bytes(patch_bytes)
            database.execute(
                "UPDATE diff_hunks SET patch_hash=? WHERE rowid=?",
                (patch_hash, rowid),
            )
            if raw_object_id:
                raw = database.execute(
                    "SELECT metadata_json FROM raw_objects WHERE id=?",
                    (raw_object_id,),
                ).fetchone()
                if raw is None:
                    raise BaselineError("sanitized diff lost its raw provenance")
                metadata = json.loads(str(raw["metadata_json"]) or "{}")
                metadata["artifact_sanitization"] = {
                    "schema_version": "code-c0-03-stable-placeholder-v1",
                    "payload_hash_recomputed": True,
                    "raw_identity_retained": True,
                }
                database.execute(
                    """UPDATE raw_objects
                       SET content_hash=?, byte_length=?, storage_path=?, metadata_json=?
                       WHERE id=?""",
                    (
                        patch_hash,
                        len(patch_bytes),
                        f"<artifact-raw>/{patch_hash.removeprefix('sha256:')}",
                        json.dumps(
                            metadata,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        raw_object_id,
                    ),
                )
        database.commit()
        database.execute("VACUUM")
        database.execute("PRAGMA journal_mode=DELETE")

    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        for rowid, (patch, raw_object_id) in changed_diff_rows.items():
            row = database.execute(
                "SELECT patch, patch_hash FROM diff_hunks WHERE rowid=?",
                (rowid,),
            ).fetchone()
            expected_hash = _sha256_bytes(patch.encode("utf-8"))
            if row is None or row["patch"] != patch or row["patch_hash"] != expected_hash:
                raise BaselineError("sanitized diff patch provenance is inconsistent")
            if raw_object_id:
                raw = database.execute(
                    "SELECT content_hash, byte_length FROM raw_objects WHERE id=?",
                    (raw_object_id,),
                ).fetchone()
                if (
                    raw is None
                    or raw["content_hash"] != expected_hash
                    or int(raw["byte_length"]) != len(patch.encode("utf-8"))
                ):
                    raise BaselineError("sanitized raw diff provenance is inconsistent")
    scan = _database_security_scan(database_path)
    if scan["temporary_path_findings"]:
        raise BaselineError("sanitized evaluation database still contains a temporary path")
    if scan["credential_findings"]:
        raise BaselineError("sanitized evaluation database contains a credential assignment")


def assert_terminal_immutability(database_path: Path, run_id: str) -> None:
    operations = (
        ("UPDATE evaluation_runs SET summary_json=summary_json WHERE id=?", (run_id,)),
        ("DELETE FROM evaluation_runs WHERE id=?", (run_id,)),
        (
            """UPDATE evaluation_results SET passed=passed
               WHERE evaluation_run_id=?""",
            (run_id,),
        ),
        (
            """DELETE FROM evaluation_results WHERE evaluation_run_id=?""",
            (run_id,),
        ),
        (
            """UPDATE evaluation_metric_values SET value=value
               WHERE evaluation_run_id=?""",
            (run_id,),
        ),
        (
            """DELETE FROM evaluation_metric_values WHERE evaluation_run_id=?""",
            (run_id,),
        ),
        (
            """INSERT INTO evaluation_metric_values
               (id, evaluation_run_id, case_id, source_domain, metric_name, slice_json,
                value, status, eligible, created_at)
               VALUES ('immutability-probe', ?, NULL, 'code', 'probe', '{}',
                       0.0, 'available', 1, 'probe')""",
            (run_id,),
        ),
    )
    for sql, params in operations:
        with sqlite3.connect(database_path) as database:
            try:
                database.execute(sql, params)
            except sqlite3.DatabaseError:
                database.rollback()
            else:
                database.rollback()
                raise BaselineError("terminal evaluation data accepted a prohibited mutation")


def _validate_completed_run(run: dict[str, Any], loaded: LoadedGolden) -> None:
    if (
        run.get("status") != "completed"
        or run.get("source_domain") != "code"
        or run.get("runner_version") != RUNNER_VERSION
        or run.get("dataset_id") != DATASET_ID
        or run.get("dataset_version") != DATASET_VERSION
        or run.get("dataset_package_hash") != PACKAGE_HASH
        or run.get("graph_candidate_enabled") is not False
        or int(run.get("case_count") or 0) != EXPECTED_ELIGIBLE_COUNT
        or len(run.get("results") or []) != EXPECTED_ELIGIBLE_COUNT
    ):
        raise BaselineError("C0-01 did not produce the required completed 33-case graph-off Run")
    if sorted(run["snapshot"].get("case_ids") or []) != sorted(loaded.case_ids):
        raise BaselineError("completed Run snapshot membership differs from the Golden loader")
    observed = run["snapshot"].get("observed_retriever") or {}
    required_observations = {
        "graph_candidate_enabled": False,
        "fusion": ["weighted-hybrid-v2"],
        "embedding_model": ["local-hash-v2"],
        "view_profile": ["file.raw,symbol.raw"],
        "retriever_version": ["HybridRetriever"],
    }
    for key, expected in required_observations.items():
        if observed.get(key) != expected:
            raise BaselineError(f"Run lacks the required observed retriever field: {key}")


def _released_loaded_golden(package: GoldenPackage) -> LoadedGolden:
    return LoadedGolden(
        package=package,
        case_ids=tuple(
            sorted(evaluation_case_id(case.canonical_id) for case in package.eligible_cases)
        ),
        canonical_case_ids=tuple(sorted(case.canonical_id for case in package.eligible_cases)),
    )


def _normalized_judgments(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        values,
        key=lambda item: (
            str(item.get("entity_id") or ""),
            str(item.get("retrieval_unit_id") or ""),
        ),
    )


def _verify_embedded_golden(
    database_path: Path,
    package: GoldenPackage,
) -> LoadedGolden:
    store = EvaluationStore(_ReadOnlySQLiteStore(database_path))
    observed_cases = store.list_cases(PROJECT_ID)
    by_name = {str(case["name"]): case for case in observed_cases}
    if len(observed_cases) != EXPECTED_CASE_COUNT or set(by_name) != {
        case.canonical_id for case in package.cases
    }:
        raise BaselineError("embedded Golden does not contain the released 50 cases")

    fields = (
        "project_id",
        "name",
        "question",
        "expected_sources",
        "expected_entity_ids",
        "expected_paths",
        "expected_commit_ids",
        "forbidden_entity_ids",
        "required_version",
        "tags",
        "enabled",
        "code_profile",
        "candidate_judgments",
    )
    for case in package.cases:
        observed = by_name[case.canonical_id]
        expected = case.request.model_dump()
        if observed["id"] != evaluation_case_id(case.canonical_id):
            raise BaselineError(f"embedded Golden case identity drift: {case.canonical_id}")
        for field in fields:
            observed_value = observed[field]
            expected_value = expected[field]
            if field == "candidate_judgments":
                observed_value = _normalized_judgments(observed_value)
                expected_value = _normalized_judgments(expected_value)
            if observed_value != expected_value:
                raise BaselineError(
                    f"embedded Golden payload drift for {case.canonical_id}: {field}"
                )

    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        counts = {
            table: int(database.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in (
                "evaluation_cases",
                "evaluation_case_profiles",
                "evaluation_candidate_judgments",
            )
        }
        other_projects = int(
            database.execute(
                "SELECT count(*) FROM evaluation_cases WHERE project_id<>?",
                (PROJECT_ID,),
            ).fetchone()[0]
        )
    expected_judgments = sum(len(case.request.candidate_judgments) for case in package.cases)
    if (
        counts
        != {
            "evaluation_cases": EXPECTED_CASE_COUNT,
            "evaluation_case_profiles": EXPECTED_CASE_COUNT,
            "evaluation_candidate_judgments": expected_judgments,
        }
        or other_projects
    ):
        raise BaselineError("embedded Golden table membership differs from the release")
    return _released_loaded_golden(package)


def _actual_repository_snapshot(database_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        database.row_factory = sqlite3.Row
        rows = database.execute(
            """SELECT r.id, r.head_commit, r.active_generation_id,
                      g.commit_sha AS generation_commit,
                      g.parser_versions_json, g.counts_json
               FROM repositories r
               JOIN index_generations g ON g.id=r.active_generation_id
               WHERE r.project_id=?
               ORDER BY r.id""",
            (PROJECT_ID,),
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "head_commit": str(row["head_commit"]),
            "active_generation_id": str(row["active_generation_id"]),
            "generation_commit": str(row["generation_commit"]),
            "parser_versions": json.loads(str(row["parser_versions_json"]) or "{}"),
            "counts": json.loads(str(row["counts_json"]) or "{}"),
        }
        for row in rows
    ]


def _observed_runner_contract(run: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    generations: set[str] = set()
    retriever_values: dict[str, set[Any]] = {
        "baseline_config_version": set(),
        "embedding_model": set(),
        "fusion": set(),
        "retriever_version": set(),
        "view_profile": set(),
    }
    graph_values: set[bool] = set()
    for result in run.get("results") or []:
        detail = result.get("detail") or {}
        generations.update(str(item) for item in detail.get("index_generation") or [])
        trace = detail.get("trace") or {}
        for field in retriever_values:
            if trace.get(field) is not None:
                retriever_values[field].add(trace[field])
        if trace.get("graph_candidate_enabled") is not None:
            graph_values.add(bool(trace["graph_candidate_enabled"]))
    observed = {field: sorted(values) for field, values in retriever_values.items()}
    observed["graph_candidate_enabled"] = (
        next(iter(graph_values)) if len(graph_values) == 1 else None
    )
    return sorted(generations), observed


def _verify_run_identity_and_contract(
    run: dict[str, Any],
    database_path: Path,
    loaded: LoadedGolden,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _validate_completed_run(run, loaded)
    prefix = f"evaluation-run://{PROJECT_ID}/"
    if not str(run["id"]).startswith(prefix):
        raise BaselineError("embedded Run identity has the wrong project namespace")
    token = str(run["id"]).removeprefix(prefix)
    try:
        parsed_uuid = UUID(token)
    except ValueError as exc:
        raise BaselineError("embedded Run identity does not end in a UUID") from exc
    if parsed_uuid.hex != token or run.get("display_key") != f"CODE-EVAL-{token[:8].upper()}":
        raise BaselineError("embedded Run display identity is inconsistent")
    if run.get("project_id") != PROJECT_ID or run.get("paired_graph_off_run_id") is not None:
        raise BaselineError("embedded Run project or pairing identity is invalid")
    started = _parse_timestamp(run.get("started_at"), field="Run started_at")
    completed = _parse_timestamp(run.get("completed_at"), field="Run completed_at")
    if completed < started:
        raise BaselineError("embedded Run completed before it started")

    declared = (run.get("config") or {}).get("declared") or {}
    if {key: declared.get(key) for key in BASELINE_CONFIG} != BASELINE_CONFIG:
        raise BaselineError("embedded Run declared configuration differs from C0-03")
    request = (run.get("config") or {}).get("request") or {}
    if (
        request.get("case_ids") != list(loaded.case_ids)
        or sorted(request.get("repository_ids") or [])
        != sorted(item["id"] for item in _actual_repository_snapshot(database_path))
        or request.get("graph_candidate_enabled") is not False
        or int(request.get("limit_per_query") or 0) != int(BASELINE_CONFIG["top_k"])
    ):
        raise BaselineError("embedded Run request differs from the fixed runner contract")

    repositories = _actual_repository_snapshot(database_path)
    expected_repository_ids = sorted(
        str(item["repository_id"]) for item in loaded.package.source_manifest["source_classes"]
    )
    if [item["id"] for item in repositories] != expected_repository_ids:
        raise BaselineError("embedded repositories differ from the released source contract")
    snapshot_repositories = [
        {
            key: item.get(key)
            for key in (
                "id",
                "head_commit",
                "active_generation_id",
                "generation_commit",
                "parser_versions",
                "counts",
            )
        }
        for item in run["snapshot"].get("repositories") or []
    ]
    if snapshot_repositories != repositories:
        raise BaselineError("Run repository snapshot differs from the embedded index")

    actual_generations = sorted(item["active_generation_id"] for item in repositories)
    result_generations, observed_retriever = _observed_runner_contract(run)
    if (
        sorted(run["snapshot"].get("observed_index_generations") or []) != actual_generations
        or result_generations != actual_generations
        or run["snapshot"].get("observed_retriever") != observed_retriever
    ):
        raise BaselineError("Run generation or retriever observation is not reconstructable")
    return repositories, observed_retriever


def _materialization_observation(prepared: PreparedBaseline) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for source_class, source in sorted(prepared.sources.items()):
        descriptor = source.to_dict()
        descriptor.pop("path", None)
        sources[source_class] = descriptor
    observation = {
        "schema_version": "code-c0-03-materialization-observation-v1",
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "package_hash": PACKAGE_HASH,
        "materializer": "tests/fixtures/code_golden/v2/materializer.py:materialize_all",
        "resolver": "RepositoryResolver",
        "parser": "CodeParser",
        "ingestion": "IngestionService",
        "database_isolated": True,
        "sources": sources,
        "ingestion_observations": list(prepared.ingestion),
        "observed_views": list(prepared.observed_views),
        "observed_embedding_models": list(prepared.observed_embedding_models),
    }
    return {**observation, "observation_fingerprint": _fingerprint(observation)}


def _artifact_file_records(directory: Path, names: list[str]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name in sorted(names):
        path = directory / name
        records[name] = {
            "sha256": _sha256_bytes(path.read_bytes()),
            "bytes": path.stat().st_size,
        }
    return records


def _safe_implementation_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    state = snapshot.get("implementation_state") or {}
    return {
        key: state.get(key)
        for key in (
            "head_commit",
            "head_tree",
            "workspace_dirty",
            "workspace_status_hash",
            "workspace_state_hash",
            "c0_content_hash",
        )
    }


def _manifest(
    run: dict[str, Any],
    package: GoldenPackage,
    pre_fingerprint: dict[str, Any],
    post_fingerprint: dict[str, Any],
    artifact_files: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    snapshot = run["snapshot"]
    repositories = [
        {
            "id": item["id"],
            "head_commit": item["head_commit"],
            "active_generation_id": item["active_generation_id"],
            "generation_commit": item["generation_commit"],
            "parser_versions": item["parser_versions"],
            "counts": item["counts"],
        }
        for item in snapshot.get("repositories") or []
    ]
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "status": "completed",
        "baseline_qualified": True,
        "run_id": run["id"],
        "display_key": run["display_key"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "project_id": PROJECT_ID,
        "dataset": {
            "id": DATASET_ID,
            "version": DATASET_VERSION,
            "package_hash": PACKAGE_HASH,
            "release_record_id": package.release_record_id,
            "case_membership_hash": package.membership_hash,
            "total_cases": EXPECTED_CASE_COUNT,
            "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
            "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
        },
        "config": BASELINE_CONFIG,
        "config_fingerprint": _fingerprint(BASELINE_CONFIG),
        "snapshot": {
            "snapshot_id": snapshot.get("snapshot_id"),
            "comparison_fingerprint": snapshot.get("comparison_fingerprint"),
            "case_membership_hash": snapshot.get("case_membership_hash"),
            "schema_version": snapshot.get("schema_version"),
            "observed_index_generations": snapshot.get("observed_index_generations"),
            "observed_retriever": snapshot.get("observed_retriever"),
            "repositories": repositories,
        },
        "implementation": {
            "runner_version": run["runner_version"],
            "snapshot_state": _safe_implementation_state(snapshot),
            "input_fingerprint": pre_fingerprint,
            "input_fingerprint_after": post_fingerprint,
            "input_fingerprint_matched": pre_fingerprint == post_fingerprint,
        },
        "artifacts": artifact_files,
        "artifact_set_hash": _fingerprint(artifact_files),
        "security": SECURITY_DECLARATION,
    }


def _attempt_id() -> str:
    return f"attempt-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid4().hex[:8]}"


def _redact_error(message: str, temporary_root: Path | None) -> str:
    if temporary_root is not None:
        message = message.replace(str(temporary_root), "<system-temp>/code-c0-03")
    return message[:4_000]


def _invalidate_attempt(
    attempt_dir: Path,
    attempt_id: str,
    started_at: str,
    error: Exception,
    *,
    temporary_root: Path | None,
    run_id: str | None,
) -> None:
    failure = {
        "schema_version": "code-c0-03-attempt-audit-v1",
        "attempt_id": attempt_id,
        "status": "failed",
        "baseline_qualified": False,
        "started_at": started_at,
        "completed_at": utc_now(),
        "run_id": run_id,
        "failure": {
            "type": type(error).__name__,
            "message": _redact_error(str(error), temporary_root),
        },
    }
    _write_json(attempt_dir / "attempt-audit.json", failure)


def _refuse_second_baseline(runs_dir: Path) -> None:
    latest = _latest_qualification_decisions(
        _read_qualification_ledger(_qualification_dir(runs_dir))
    )
    for run_id, record in sorted(latest.items()):
        if (
            record["decision"] == "qualified"
            and record.get("dataset_package_hash") == PACKAGE_HASH
            and record.get("config_fingerprint") == _fingerprint(BASELINE_CONFIG)
        ):
            raise BaselineError(f"a qualified C0-03 baseline already exists: {run_id}")


def run_baseline(
    *,
    root: Path | None = None,
    runs_dir: Path | None = None,
) -> Path:
    resolved_root = (root or repository_root()).resolve()
    resolved_runs = (
        runs_dir.resolve()
        if runs_dir is not None
        else (resolved_root / "evals" / "code" / "runs").resolve()
    )
    resolved_runs.mkdir(parents=True, exist_ok=True)
    _refuse_second_baseline(resolved_runs)

    attempt_id = _attempt_id()
    attempt_dir = resolved_runs / attempt_id
    attempt_dir.mkdir()
    started_at = utc_now()
    _write_json(
        attempt_dir / "attempt-audit.json",
        {
            "schema_version": "code-c0-03-attempt-audit-v1",
            "attempt_id": attempt_id,
            "status": "running",
            "baseline_qualified": False,
            "started_at": started_at,
        },
    )
    temporary_root: Path | None = None
    run_id: str | None = None
    final_dir: Path | None = None
    qualification_recorded = False
    try:
        pre_fingerprint = workspace_input_fingerprint(resolved_root, resolved_runs)
        with tempfile.TemporaryDirectory(prefix="code-c0-03-") as temporary:
            temporary_root = Path(temporary).resolve()
            prepared = prepare_baseline_environment(temporary_root / "work", root=resolved_root)
            prepared.runtime.platform.code = _ObservedHybridRetriever(prepared.runtime.retriever)
            request = CodeEvaluationRunRequest(
                project_id=PROJECT_ID,
                dataset_id=DATASET_ID,
                dataset_version=DATASET_VERSION,
                package_hash=PACKAGE_HASH,
                case_ids=list(prepared.loaded.case_ids),
                repository_ids=sorted(source.repository_id for source in prepared.sources.values()),
                graph_candidate_enabled=False,
                limit_per_query=int(BASELINE_CONFIG["top_k"]),
                config={
                    **BASELINE_CONFIG,
                    "workspace_input_fingerprint": pre_fingerprint,
                    "observed_views_before_run": list(prepared.observed_views),
                    "observed_embedding_models_before_run": list(
                        prepared.observed_embedding_models
                    ),
                    "materialization_observation": _materialization_observation(prepared),
                },
            )
            run = prepared.runtime.evaluation.run_code(
                request,
                allowed_acl_refs=[ACL_REF],
                enforce_acl=True,
            )
            run_id = str(run["id"])
            _validate_completed_run(run, prepared.loaded)
            assert_terminal_immutability(prepared.database_path, run_id)

            post_fingerprint = workspace_input_fingerprint(resolved_root, resolved_runs)
            require_unchanged_workspace(pre_fingerprint, post_fingerprint)

            database_artifact = attempt_dir / "evaluation.sqlite3"
            with prepared.runtime.store.connection() as database:
                database.execute("PRAGMA wal_checkpoint(FULL)")
            shutil.copy2(prepared.database_path, database_artifact)
            _sanitize_database(database_artifact, temporary_root)
            persisted_store = EvaluationStore(_ReadOnlySQLiteStore(database_artifact))
            persisted_run = persisted_store.get_run(run_id)
            if persisted_run is None:
                raise BaselineError("sanitized artifact database lost the completed Run")
            _verify_run_identity_and_contract(
                persisted_run,
                database_artifact,
                prepared.loaded,
            )
            assert_terminal_immutability(database_artifact, run_id)

            reports = _report_payloads(persisted_run, prepared.package, database_artifact)
            for name, payload in reports.items():
                _write_json(attempt_dir / name, payload)
            completed_audit = {
                "schema_version": "code-c0-03-attempt-audit-v1",
                "attempt_id": attempt_id,
                "status": "completed",
                "baseline_qualified": True,
                "started_at": started_at,
                "completed_at": persisted_run["completed_at"],
                "run_id": run_id,
            }
            _write_json(attempt_dir / "attempt-audit.json", completed_audit)
            _require_clean_security_scan(attempt_dir)
            artifact_files = _artifact_file_records(attempt_dir, list(ARTIFACT_FILES))
            manifest = _manifest(
                persisted_run,
                prepared.package,
                pre_fingerprint,
                post_fingerprint,
                artifact_files,
            )
            _write_json(attempt_dir / "manifest.json", manifest)
            _verify_artifact_contents(attempt_dir, root=resolved_root)

        assert run_id is not None
        final_dir = resolved_runs / run_id.rsplit("/", 1)[-1]
        if final_dir.exists():
            raise BaselineError("artifact directory for the completed Run already exists")
        attempt_dir.rename(final_dir)
        append_qualification_record(
            resolved_runs,
            final_dir,
            decision="qualified",
            reason_code="c0-03-p0-09-p0-10-remediated",
        )
        qualification_recorded = True
        verify_artifact(
            final_dir,
            root=resolved_root,
            qualification_dir=_qualification_dir(resolved_runs),
        )
        return final_dir
    except Exception as exc:
        if qualification_recorded and final_dir is not None and final_dir.exists():
            append_qualification_record(
                resolved_runs,
                final_dir,
                decision="revoked",
                reason_code="post-qualification-verification-failed",
            )
        if attempt_dir.exists():
            _invalidate_attempt(
                attempt_dir,
                attempt_id,
                started_at,
                exc,
                temporary_root=temporary_root,
                run_id=run_id,
            )
        if isinstance(exc, BaselineError):
            raise
        raise BaselineError(f"C0-03 baseline attempt failed: {type(exc).__name__}: {exc}") from exc


def _qualification_ledger_candidates(
    directory: Path,
    root: Path,
    explicit: Path | None,
) -> list[Path]:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.resolve())
    candidates.extend(
        [
            (root / "evals" / "code" / "runs" / QUALIFICATION_DIRECTORY).resolve(),
            (directory.parent / QUALIFICATION_DIRECTORY).resolve(),
        ]
    )
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique and candidate.is_dir():
            unique.append(candidate)
    return unique


def _require_qualification_record(
    directory: Path,
    root: Path,
    manifest: dict[str, Any],
    *,
    qualification_dir: Path | None,
) -> dict[str, Any]:
    run_id = str(manifest.get("run_id") or "")
    matching: list[dict[str, Any]] = []
    for ledger in _qualification_ledger_candidates(
        directory,
        root,
        qualification_dir,
    ):
        latest = _latest_qualification_decisions(_read_qualification_ledger(ledger))
        if run_id in latest:
            matching.append(latest[run_id])
    if not matching:
        raise BaselineError("artifact has no controlled qualification record")
    identities = {_fingerprint(record) for record in matching}
    if len(identities) != 1:
        raise BaselineError("artifact has conflicting qualification ledger decisions")
    record = matching[0]
    if record["decision"] == "revoked":
        raise BaselineError(f"artifact qualification was revoked: {record.get('reason_code')}")
    if (
        record.get("manifest_canonical_hash") != _fingerprint(manifest)
        or record.get("artifact_set_hash") != manifest.get("artifact_set_hash")
        or record.get("dataset_package_hash") != PACKAGE_HASH
        or record.get("config_fingerprint") != _fingerprint(BASELINE_CONFIG)
        or record.get("artifact_directory") != run_id.rsplit("/", 1)[-1]
    ):
        raise BaselineError("manifest is not anchored by its qualification record")
    completed = _parse_timestamp(manifest.get("completed_at"), field="manifest completed_at")
    recorded = _parse_timestamp(record.get("recorded_at"), field="qualification recorded_at")
    if recorded < completed:
        raise BaselineError("qualification predates the completed Run")
    return record


def _verify_artifact_contents(
    directory: Path,
    *,
    root: Path,
) -> dict[str, Any]:
    directory = directory.resolve()
    manifest = _read_json(directory / "manifest.json")
    if (
        manifest.get("schema_version") != BASELINE_SCHEMA_VERSION
        or manifest.get("status") != "completed"
        or manifest.get("baseline_qualified") is not True
    ):
        raise BaselineError("artifact is not a qualified completed C0-03 baseline")
    package = validate_golden_package(root)
    expected_artifacts = _artifact_file_records(directory, list(ARTIFACT_FILES))
    artifact_files = manifest.get("artifacts")
    if artifact_files != expected_artifacts or manifest.get("artifact_set_hash") != _fingerprint(
        expected_artifacts
    ):
        raise BaselineError("artifact file-set fingerprint mismatch")
    for name, record in sorted(expected_artifacts.items()):
        path = directory / name
        if _sha256_bytes(path.read_bytes()) != record.get(
            "sha256"
        ) or path.stat().st_size != record.get("bytes"):
            raise BaselineError(f"artifact digest mismatch: {name}")

    database_path = directory / "evaluation.sqlite3"
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        quick_check = str(database.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_failures = database.execute("PRAGMA foreign_key_check").fetchall()
        run_ids = [
            str(row[0])
            for row in database.execute("SELECT id FROM evaluation_runs ORDER BY id").fetchall()
        ]
    if quick_check != "ok" or foreign_key_failures:
        raise BaselineError("artifact SQLite integrity check failed")
    if len(run_ids) != 1:
        raise BaselineError("artifact SQLite must contain exactly one Run")
    run_id = run_ids[0]
    if manifest.get("run_id") != run_id:
        raise BaselineError("manifest Run identity differs from embedded SQLite")

    store = EvaluationStore(_ReadOnlySQLiteStore(database_path))
    run = store.get_run(run_id)
    if run is None:
        raise BaselineError("artifact database does not contain its Run")
    loaded = _verify_embedded_golden(database_path, package)
    _verify_run_identity_and_contract(run, database_path, loaded)
    assert_terminal_immutability(database_path, run_id)

    result_case_ids = [str(result["case_id"]) for result in run["results"]]
    if (
        len(result_case_ids) != EXPECTED_ELIGIBLE_COUNT
        or len(set(result_case_ids)) != EXPECTED_ELIGIBLE_COUNT
        or sorted(result_case_ids) != list(loaded.case_ids)
    ):
        raise BaselineError("embedded results differ from the released eligible membership")

    expected_reports = _report_payloads(run, package, database_path)
    for name, expected in expected_reports.items():
        if _read_json(directory / name) != expected:
            raise BaselineError(f"artifact report cannot be recomputed: {name}")

    audit = _read_json(directory / "attempt-audit.json")
    audit_started = _parse_timestamp(audit.get("started_at"), field="attempt started_at")
    run_started = _parse_timestamp(run.get("started_at"), field="Run started_at")
    if audit != {
        "schema_version": "code-c0-03-attempt-audit-v1",
        "attempt_id": audit.get("attempt_id"),
        "status": "completed",
        "baseline_qualified": True,
        "started_at": audit.get("started_at"),
        "completed_at": run["completed_at"],
        "run_id": run_id,
    } or not str(audit.get("attempt_id") or "").startswith("attempt-"):
        raise BaselineError("attempt audit identity or terminal state is invalid")
    if audit_started > run_started:
        raise BaselineError("attempt audit starts after the embedded Run")

    declared = (run.get("config") or {}).get("declared") or {}
    input_fingerprint = declared.get("workspace_input_fingerprint")
    required_fingerprint_fields = {
        "head_commit",
        "head_tree",
        "worktree_list_hash",
        "workspace_dirty",
        "workspace_status_hash",
        "workspace_state_hash",
        "implementation_input_hash",
        "package_hash",
    }
    if (
        not isinstance(input_fingerprint, dict)
        or set(input_fingerprint) != required_fingerprint_fields
        or input_fingerprint.get("package_hash") != PACKAGE_HASH
    ):
        raise BaselineError("embedded workspace input fingerprint is incomplete")
    expected_manifest = _manifest(
        run,
        package,
        input_fingerprint,
        input_fingerprint,
        expected_artifacts,
    )
    if manifest != expected_manifest:
        raise BaselineError(
            "manifest cannot be reconstructed from package, SQLite, reports, and runner contract"
        )

    security_scan = _require_clean_security_scan(directory)
    return {
        "status": "content-verified",
        "run_id": run_id,
        "dataset_package_hash": PACKAGE_HASH,
        "case_count": run["case_count"],
        "result_count": len(run["results"]),
        "artifact_set_hash": manifest["artifact_set_hash"],
        "manifest_canonical_hash": _fingerprint(manifest),
        "security_scan": security_scan,
    }


def verify_artifact(
    directory: Path,
    *,
    root: Path | None = None,
    qualification_dir: Path | None = None,
) -> dict[str, Any]:
    directory = directory.resolve()
    resolved_root = (root or repository_root()).resolve()
    manifest = _read_json(directory / "manifest.json")
    qualification = _require_qualification_record(
        directory,
        resolved_root,
        manifest,
        qualification_dir=qualification_dir,
    )
    verified = _verify_artifact_contents(directory, root=resolved_root)
    return {
        **verified,
        "status": "verified",
        "qualification_record_hash": qualification["record_hash"],
    }


def _prepare_summary(prepared: PreparedBaseline) -> dict[str, Any]:
    return {
        "status": "prepared",
        "dataset_package_hash": prepared.package.package_hash,
        "total_cases": len(prepared.package.cases),
        "eligible_cases": len(prepared.loaded.case_ids),
        "ineligible_cases": len(prepared.package.ineligible_cases),
        "repositories": [
            {
                "source_class": item["source_class"],
                "repository_id": item["repository_id"],
                "generation_id": item["generation_id"],
                "status": item["status"],
            }
            for item in prepared.ingestion
        ],
        "views": list(prepared.observed_views),
        "embedding_models": list(prepared.observed_embedding_models),
        "database_isolated": True,
    }


def _resolve_artifact(value: str, runs_dir: Path) -> Path:
    candidate = Path(value)
    if candidate.is_dir():
        return candidate
    nested = runs_dir / value
    if nested.is_dir():
        return nested
    raise BaselineError(f"baseline artifact not found: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Code C0-03 V1 baseline")
    parser.add_argument("--repository-root", type=Path, default=repository_root())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the frozen Golden v2 release")
    subparsers.add_parser("prepare", help="materialize and ingest in a disposable isolated DB")
    run_parser = subparsers.add_parser("run", help="create the unique qualified baseline")
    run_parser.add_argument("--runs-dir", type=Path)
    verify_parser = subparsers.add_parser("verify", help="verify a durable baseline artifact")
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("--runs-dir", type=Path)
    revoke_parser = subparsers.add_parser(
        "revoke",
        help="append a controlled revocation record without modifying the Run artifact",
    )
    revoke_parser.add_argument("artifact")
    revoke_parser.add_argument("--reason-code", required=True)
    revoke_parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()
    try:
        if args.command == "validate":
            package = validate_golden_package(root)
            output = {
                "status": "validated",
                **package.validation_summary,
                "membership_hash": package.membership_hash,
            }
        elif args.command == "prepare":
            with tempfile.TemporaryDirectory(prefix="code-c0-03-prepare-") as temporary:
                prepared = prepare_baseline_environment(Path(temporary) / "work", root=root)
                output = _prepare_summary(prepared)
        elif args.command == "run":
            artifact = run_baseline(root=root, runs_dir=args.runs_dir)
            output = verify_artifact(artifact, root=root)
            try:
                output["artifact"] = artifact.relative_to(root).as_posix()
            except ValueError:
                output["artifact"] = artifact.as_posix()
        elif args.command == "verify":
            runs_dir = (
                args.runs_dir.resolve()
                if args.runs_dir
                else (root / "evals" / "code" / "runs").resolve()
            )
            output = verify_artifact(_resolve_artifact(args.artifact, runs_dir), root=root)
        else:
            runs_dir = (
                args.runs_dir.resolve()
                if args.runs_dir
                else (root / "evals" / "code" / "runs").resolve()
            )
            record = append_qualification_record(
                runs_dir,
                _resolve_artifact(args.artifact, runs_dir),
                decision="revoked",
                reason_code=args.reason_code,
            )
            output = {
                "status": "revoked",
                "run_id": record["run_id"],
                "qualification_record_hash": record["record_hash"],
            }
    except Exception as exc:
        failure = {
            "status": "failed",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
