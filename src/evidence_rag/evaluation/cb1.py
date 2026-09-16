from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..config import Settings
from ..models import RepositoryIngestRequest
from ..rag.sources.code.contracts import (
    CodeQueryProfile,
    CodeRetrievalBudget,
    CodeTask,
)
from ..rag.sources.code.unit_builder import BUILDER_VERSION as AST_BUILDER_VERSION
from ..runtime import Runtime, create_runtime
from . import baseline
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
    golden_materializer,
    load_golden_cases,
    repository_root,
    validate_golden_package,
)
from .models import CodeEvaluationRunRequest
from .store import EvaluationStore

CB1_SCHEMA_VERSION = "code-c-b1-treatment-artifact-v1"
CB1_RUNNER_VERSION = "code-c-b1-runner-v1"
CB0_QUALIFIED_RUN_ID = "evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061"
CB0_ARTIFACT_DIRECTORY = "5a92eafdff5d49e6aae8bb55fdc14061"
REAL_RETRIEVER_MODULE = "evidence_rag.rag.sources.code"
REAL_RETRIEVER_CLASS = "CodeExactSparseRetriever"
SPARSE_INDEX_VERSION = "fts5-code-v2"
TOP_K = 20
CHANNEL_BUDGET = {
    "total_candidates": 50,
    "exact_candidates": 20,
    "sparse_candidates": 40,
    "dense_candidates": 0,
    "graph_candidates": 0,
    "history_candidates": 0,
    "test_candidates": 0,
    "graph_node_budget": 0,
    "graph_edge_budget": 0,
    "context_token_budget": 4_000,
}
CAPABILITY_BOUNDARY = {
    "available": [
        "active-generation AST retrieval units",
        "exact candidate retrieval",
        "fielded sparse candidate retrieval",
        "retrieval-unit identity",
        "stable entity projection",
        "source locator projection",
    ],
    "unavailable": [
        {
            "capability": "neural dense retrieval",
            "reason": "C-B1 isolates AST Unit plus exact/sparse; C3 is outside this treatment.",
        },
        {
            "capability": "graph candidate expansion",
            "reason": "C-B1 is graph-candidate-off; graph treatment belongs to C4.",
        },
        {
            "capability": "historical non-active symbol/tag retrieval",
            "reason": "The released 17 ineligible cases remain outside the 33-case denominator.",
        },
        {
            "capability": "test/validation candidate retrieval",
            "reason": "C6 validation retrieval is outside this treatment.",
        },
    ],
}
REQUIRED_REPORTING = (
    "exact slice",
    "identifier slice",
    "low-overlap slice",
    "same-name hard negative",
    "duplicate",
    "index bytes",
    "ingest time",
    "retrieval P95",
    "locator accuracy",
)
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
EXPECTED_ARTIFACT_ENTRIES = frozenset(("manifest.json", *ARTIFACT_FILES))


class CB1Error(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RetrieverDescriptor:
    module: str
    class_name: str
    version: str
    fusion: str
    channels: tuple[str, ...]
    index_family: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "class": self.class_name,
            "version": self.version,
            "fusion": self.fusion,
            "channels": list(self.channels),
            "index_family": self.index_family,
        }


@dataclass(slots=True)
class PreparedCB1:
    work_root: Path
    database_path: Path
    runtime: Runtime
    package: GoldenPackage
    loaded: LoadedGolden
    sources: dict[str, Any]
    ingestion: tuple[dict[str, Any], ...]
    publications: tuple[dict[str, Any], ...]
    unit_count: int
    fts_row_count: int
    ingest_time_ms: float
    source_ingest_time_ms: dict[str, float]


@dataclass(frozen=True, slots=True)
class CB0Anchor:
    directory: Path
    manifest: dict[str, Any]
    verification: dict[str, Any]
    run: dict[str, Any]


RetrieverFactory = Callable[[Runtime], Any]
ExecutionMode = Literal["qualified", "test"]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CB1Error(f"unable to read artifact JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise CB1Error(f"artifact JSON must be an object: {path.name}")
    return value


def _relative_or_name(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _cb0_artifact(root: Path) -> Path:
    return root / "evals" / "code" / "runs" / CB0_ARTIFACT_DIRECTORY


def _load_cb0_anchor(root: Path) -> CB0Anchor:
    directory = _cb0_artifact(root).resolve()
    if not directory.is_dir():
        raise CB1Error(f"qualified C-B0 artifact is missing: {CB0_ARTIFACT_DIRECTORY}")
    try:
        verification = baseline.verify_artifact(directory, root=root)
    except Exception as exc:
        raise CB1Error(f"C-B0 qualification verification failed: {exc}") from exc
    manifest = _read_json(directory / "manifest.json")
    if (
        verification.get("run_id") != CB0_QUALIFIED_RUN_ID
        or manifest.get("run_id") != CB0_QUALIFIED_RUN_ID
        or manifest.get("baseline_qualified") is not True
        or (manifest.get("dataset") or {}).get("package_hash") != PACKAGE_HASH
        or (manifest.get("dataset") or {}).get("case_membership_hash")
        != validate_golden_package(root).membership_hash
        or (manifest.get("dataset") or {}).get("eligible_cases") != EXPECTED_ELIGIBLE_COUNT
    ):
        raise CB1Error("C-B0 artifact does not match the fixed qualified baseline anchor")
    store = EvaluationStore(baseline._ReadOnlySQLiteStore(directory / "evaluation.sqlite3"))
    run = store.get_run(CB0_QUALIFIED_RUN_ID)
    if run is None:
        raise CB1Error("qualified C-B0 SQLite does not contain the anchored Run")
    return CB0Anchor(
        directory=directory,
        manifest=manifest,
        verification=verification,
        run=run,
    )


def _real_retriever_available() -> tuple[bool, str | None]:
    try:
        module = importlib.import_module(REAL_RETRIEVER_MODULE)
        retriever_class = getattr(module, REAL_RETRIEVER_CLASS)
    except (ImportError, AttributeError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if not inspect.isclass(retriever_class):
        return False, f"{REAL_RETRIEVER_MODULE}.{REAL_RETRIEVER_CLASS} is not a class"
    return True, None


def _real_retriever_contract() -> dict[str, Any]:
    ready, error = _real_retriever_available()
    contract: dict[str, Any] = {
        "module": REAL_RETRIEVER_MODULE,
        "class": REAL_RETRIEVER_CLASS,
        "version": {
            "status": "pending",
            "value": None,
            "source": "CodeExactSparseRetriever.retriever_version",
        },
        "fusion": {
            "status": "pending",
            "value": None,
            "source": "CodeExactSparseRetriever fusion policy",
        },
    }
    if not ready:
        return contract
    public_module = importlib.import_module(REAL_RETRIEVER_MODULE)
    retriever_class = getattr(public_module, REAL_RETRIEVER_CLASS)
    implementation_module = importlib.import_module(retriever_class.__module__)
    contract["version"] = {
        "status": "ready",
        "value": _string_attribute(
            implementation_module,
            ("RETRIEVER_VERSION",),
            "retriever_version",
        ),
        "source": f"{retriever_class.__module__}.RETRIEVER_VERSION",
    }
    contract["fusion"] = {
        "status": "ready",
        "value": _string_attribute(
            implementation_module,
            ("FUSION_POLICY",),
            "fusion policy/version",
        ),
        "source": f"{retriever_class.__module__}.FUSION_POLICY",
    }
    contract["availability_error"] = error
    return contract


def preparation_status(*, root: Path | None = None) -> dict[str, Any]:
    resolved_root = (root or repository_root()).resolve()
    package = validate_golden_package(resolved_root)
    anchor = _load_cb0_anchor(resolved_root)
    retriever_ready, retriever_error = _real_retriever_available()
    return {
        "status": "PREPARED",
        "execution_status": "pending",
        "treatment_qualified": False,
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
        "baseline": {
            "run_id": CB0_QUALIFIED_RUN_ID,
            "qualification_record_hash": anchor.verification["qualification_record_hash"],
            "manifest_canonical_hash": anchor.verification["manifest_canonical_hash"],
        },
        "config_contract": {
            "treatment": "C-B1 AST Unit + exact/sparse",
            "rag_code_unit_builder": "ast-v2",
            "rag_code_dense_index": False,
            "retriever": _real_retriever_contract(),
            "top_k": TOP_K,
            "sparse_index_version": SPARSE_INDEX_VERSION,
            "channel_budget": CHANNEL_BUDGET,
            "graph_candidate": False,
            "capability_boundary": CAPABILITY_BOUNDARY,
        },
        "real_retriever": {
            "status": "ready" if retriever_ready else "pending",
            "reason": retriever_error,
        },
        "required_reporting": {name: {"status": "pending"} for name in REQUIRED_REPORTING},
    }


def _treatment_input_hash(root: Path) -> str:
    paths = [
        *(
            path
            for path in (root / "src" / "evidence_rag" / "evaluation").rglob("*.py")
            if "__pycache__" not in path.parts
        ),
        *(
            path
            for path in (root / "src" / "evidence_rag" / "rag" / "sources" / "code").rglob("*.py")
            if "__pycache__" not in path.parts
        ),
        root / "src/evidence_rag/config.py",
        root / "src/evidence_rag/ingestion.py",
        root / "src/evidence_rag/parser.py",
        root / "src/evidence_rag/runtime.py",
        root / "src/evidence_rag/storage.py",
        root / "tests/test_code_cb1_run.py",
        root / "evals/code/code_golden_v2.py",
        root / "tests/fixtures/code_golden/v2/materializer.py",
    ]
    digest = hashlib.sha256()
    for path in sorted(set(paths), key=lambda item: item.as_posix()):
        if not path.is_file():
            raise CB1Error(f"required C-B1 implementation input is missing: {path}")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def workspace_input_fingerprint(root: Path, runs_dir: Path) -> dict[str, Any]:
    try:
        common = baseline.workspace_input_fingerprint(root, runs_dir)
    except Exception as exc:
        raise CB1Error(f"unable to fingerprint the C-B1 workspace: {exc}") from exc
    return {
        **common,
        "cb1_treatment_input_hash": _treatment_input_hash(root),
        "cb0_qualified_run_id": CB0_QUALIFIED_RUN_ID,
    }


def _settings(work_root: Path, root: Path) -> Settings:
    data_dir = work_root / "runtime"
    database_path = data_dir / "code-c-b1.sqlite3"
    production_database = (root / "var" / "evidence-rag.sqlite3").resolve()
    if database_path.resolve() == production_database:
        raise CB1Error("C-B1 may not use var/evidence-rag.sqlite3")
    return Settings(
        data_dir=data_dir,
        database_path=database_path,
        repository_cache=work_root / "repository-cache",
        web_dir=root / "web",
        allowed_local_roots=(work_root, root),
        project_root=root,
        embedding_dimensions=384,
        enforce_acl=True,
        rag_code_engine="v1",
        rag_code_shadow=False,
        rag_code_unit_builder="ast-v2",
        rag_code_embedding_profile="local-hash-v2",
        rag_code_dense_index=False,
        rag_code_graph=False,
        rag_code_semantic_resolver="off",
        rag_code_reranker="off",
        rag_code_context="snippet-v1",
        rag_code_canary_percent=0,
    )


def _active_publications(runtime: Runtime) -> tuple[dict[str, Any], ...]:
    publications: list[dict[str, Any]] = []
    generations = runtime.store.active_code_generations(project_id=PROJECT_ID)
    if len(generations) != 3:
        raise CB1Error("isolated C-B1 database must expose three active Code generations")
    for repository_id, generation_id in sorted(generations.items()):
        publication = runtime.store.get_code_index_publication(
            generation_id,
            repository_id=repository_id,
            active_only=True,
        )
        if publication is None:
            raise CB1Error(f"missing active AST publication for {repository_id}")
        if (
            publication.get("builder") != AST_BUILDER_VERSION
            or publication.get("status") != "published"
            or publication.get("embedding") != "not-built"
            or publication.get("graph") != "not-built"
        ):
            raise CB1Error(f"unexpected AST publication contract for {repository_id}")
        publications.append(publication)
    return tuple(publications)


def prepare_cb1_environment(
    work_root: Path,
    *,
    root: Path | None = None,
) -> PreparedCB1:
    resolved_root = (root or repository_root()).resolve()
    work_root = work_root.resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    if any(work_root.iterdir()):
        raise CB1Error("C-B1 work root must be empty")

    package = validate_golden_package(resolved_root)
    materializer = golden_materializer(resolved_root)
    sources = materializer.materialize_all(work_root / "materialized", resolved_root)
    try:
        baseline._verify_source_materialization(package, sources)
    except Exception as exc:
        raise CB1Error(f"Golden source materialization failed: {exc}") from exc

    settings = _settings(work_root, resolved_root)
    runtime = create_runtime(settings)
    try:
        baseline._create_project(runtime)
    except Exception as exc:
        raise CB1Error(f"unable to create isolated Golden project: {exc}") from exc

    ingestion_observations: list[dict[str, Any]] = []
    source_timings: dict[str, float] = {}
    ingest_started = time.perf_counter()
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
            raise CB1Error(f"RepositoryResolver identity mismatch: {source_class}")
        request = RepositoryIngestRequest(
            source=source.path,
            project_id=PROJECT_ID,
            acl_ref=ACL_REF,
            ignore=["generated", "vendor"],
            history_depth=history_depth,
        )
        source_started = time.perf_counter()
        workflow_id = runtime.ingestion.enqueue(request)
        runtime.ingestion.run(workflow_id, request)
        source_timings[source_class] = round(
            (time.perf_counter() - source_started) * 1_000,
            6,
        )
        try:
            observation = baseline._workflow_observation(runtime, workflow_id, source_class)
        except Exception as exc:
            raise CB1Error(f"production ingestion failed for {source_class}: {exc}") from exc
        if observation["repository_id"] != source.repository_id:
            raise CB1Error(f"ingestion repository mismatch: {source_class}")
        ingestion_observations.append(observation)
    ingest_time_ms = round((time.perf_counter() - ingest_started) * 1_000, 6)

    publications = _active_publications(runtime)
    with runtime.store.connection() as database:
        unit_count = int(
            database.execute(
                """SELECT count(*) FROM code_retrieval_units
                   WHERE project_id=?""",
                (PROJECT_ID,),
            ).fetchone()[0]
        )
        fts_count = int(
            database.execute("SELECT count(*) FROM code_retrieval_units_fts").fetchone()[0]
        )
    if unit_count <= 0 or fts_count != unit_count:
        raise CB1Error("AST Unit/FTS materialization is empty or inconsistent")

    loaded = load_golden_cases(runtime.evaluation.store, package)
    if (
        len(package.cases) != EXPECTED_CASE_COUNT
        or len(loaded.case_ids) != EXPECTED_ELIGIBLE_COUNT
        or len(package.ineligible_cases) != EXPECTED_INELIGIBLE_COUNT
        or tuple(sorted(loaded.canonical_case_ids))
        != tuple(sorted(case.canonical_id for case in package.eligible_cases))
    ):
        raise CB1Error("Golden loader failed the released 50/33/17 membership contract")
    if runtime.settings.database_path.resolve() != settings.database_path.resolve():
        raise CB1Error("runtime escaped the requested isolated SQLite path")
    return PreparedCB1(
        work_root=work_root,
        database_path=settings.database_path,
        runtime=runtime,
        package=package,
        loaded=loaded,
        sources=sources,
        ingestion=tuple(ingestion_observations),
        publications=publications,
        unit_count=unit_count,
        fts_row_count=fts_count,
        ingest_time_ms=ingest_time_ms,
        source_ingest_time_ms=source_timings,
    )


def _string_attribute(value: Any, names: Sequence[str], label: str) -> str:
    for name in names:
        observed = getattr(value, name, None)
        if isinstance(observed, str) and observed.strip():
            return observed.strip()
    raise CB1Error(f"retriever must declare a non-empty {label}")


def _retriever_descriptor(retriever: Any) -> RetrieverDescriptor:
    raw_channels = getattr(retriever, "channels", None)
    real_retriever = type(retriever).__name__ == REAL_RETRIEVER_CLASS and (
        type(retriever).__module__ == REAL_RETRIEVER_MODULE
        or type(retriever).__module__.startswith(f"{REAL_RETRIEVER_MODULE}.")
    )
    module = None
    if real_retriever:
        module = importlib.import_module(type(retriever).__module__)
        raw_channels = ("exact", "sparse")
    if isinstance(raw_channels, (str, bytes)) or not isinstance(raw_channels, Sequence):
        raise CB1Error("retriever must declare channels=('exact', 'sparse')")
    channels = tuple(
        str(getattr(channel, "value", channel)).strip().casefold() for channel in raw_channels
    )
    if channels != ("exact", "sparse"):
        raise CB1Error("C-B1 retriever channels must be exactly ('exact', 'sparse')")
    descriptor = RetrieverDescriptor(
        module=type(retriever).__module__,
        class_name=type(retriever).__name__,
        version=(
            _string_attribute(module, ("RETRIEVER_VERSION",), "retriever_version")
            if module is not None
            else _string_attribute(
                retriever,
                ("retriever_version", "version", "RETRIEVER_VERSION"),
                "retriever_version",
            )
        ),
        fusion=(
            _string_attribute(module, ("FUSION_POLICY",), "fusion policy/version")
            if module is not None
            else _string_attribute(
                retriever,
                ("fusion", "fusion_version", "FUSION_POLICY"),
                "fusion policy/version",
            )
        ),
        channels=channels,
        index_family=(
            "ast-v2"
            if real_retriever
            else _string_attribute(
                retriever,
                ("index_family", "unit_builder", "INDEX_FAMILY"),
                "index_family",
            )
        ),
    )
    if descriptor.index_family != "ast-v2":
        raise CB1Error("C-B1 retriever index_family must be ast-v2")
    return descriptor


def _load_real_retriever(runtime: Runtime) -> Any:
    try:
        module = importlib.import_module(REAL_RETRIEVER_MODULE)
        retriever_class = getattr(module, REAL_RETRIEVER_CLASS)
    except (ImportError, AttributeError) as exc:
        raise CB1Error(
            "C2-05 is not ready: public CodeExactSparseRetriever is not importable"
        ) from exc
    if not inspect.isclass(retriever_class):
        raise CB1Error("public CodeExactSparseRetriever export is not a class")
    try:
        retriever = retriever_class(runtime.store)
    except TypeError as exc:
        raise CB1Error(
            "CodeExactSparseRetriever must expose the evaluation constructor "
            "CodeExactSparseRetriever(SQLiteStore)"
        ) from exc
    descriptor = _retriever_descriptor(retriever)
    if (
        descriptor.module != REAL_RETRIEVER_MODULE
        and not descriptor.module.startswith(f"{REAL_RETRIEVER_MODULE}.")
    ) or descriptor.class_name != REAL_RETRIEVER_CLASS:
        raise CB1Error("qualified mode did not construct the real C2-05 retriever")
    return retriever


def _publish_real_treatment_index(
    prepared: PreparedCB1,
    descriptor: RetrieverDescriptor,
) -> None:
    """Promote only the validated isolated FTS index to the C-B1 treatment."""

    promoted: list[dict[str, Any]] = []
    for publication in prepared.publications:
        generation_id = str(publication["generation_id"])
        repository_id = str(publication["repository_id"])
        with prepared.runtime.store.connection() as database:
            unit_count = int(
                database.execute(
                    """SELECT count(*) FROM code_retrieval_units
                       WHERE repository_id=? AND generation_id=?""",
                    (repository_id, generation_id),
                ).fetchone()[0]
            )
            fts_count = int(
                database.execute(
                    """SELECT count(*)
                       FROM code_retrieval_units_fts f
                       JOIN code_retrieval_units u ON u.rowid=f.rowid
                       WHERE u.repository_id=? AND u.generation_id=?""",
                    (repository_id, generation_id),
                ).fetchone()[0]
            )
        if unit_count <= 0 or fts_count != unit_count:
            raise CB1Error(
                f"cannot publish C-B1 sparse treatment for incomplete index: {repository_id}"
            )
        validation = dict(publication.get("validation") or {})
        capabilities = dict(validation.get("capabilities") or {})
        capabilities["sparse_retrieval"] = True
        validation["capabilities"] = capabilities
        validation["cb1_treatment_publication"] = {
            "schema_version": "code-c-b1-sparse-publication-v1",
            "retriever_version": descriptor.version,
            "fusion": descriptor.fusion,
            "sparse_index_version": SPARSE_INDEX_VERSION,
            "unit_count": unit_count,
            "fts_row_count": fts_count,
            "isolated_evaluation_only": True,
        }
        promoted.append(
            prepared.runtime.store.upsert_code_index_publication(
                {
                    "generation_id": generation_id,
                    "repository_id": repository_id,
                    "project_id": publication["project_id"],
                    "builder": publication["builder"],
                    "sparse": SPARSE_INDEX_VERSION,
                    "embedding": publication["embedding"],
                    "graph": publication["graph"],
                    "status": "published",
                    "validation": validation,
                }
            )
        )
    prepared.publications = tuple(promoted)


_TASKS = {
    "exact_location": CodeTask.EXACT_LOCATION,
    "implementation": CodeTask.IMPLEMENTATION,
    "call_reference_path": CodeTask.CALL_PATH,
    "bug_localization": CodeTask.BUG_LOCALIZATION,
    "impact_analysis": CodeTask.IMPACT_ANALYSIS,
}


def _query_profile(case: GoldenCase, request: Any) -> CodeQueryProfile:
    profile = case.request.code_profile
    if profile is None or profile.task not in _TASKS:
        raise CB1Error(f"unsupported Golden task for C-B1: {case.canonical_id}")
    target_ref = request.scope.commit or profile.expected_ref or "current"
    return CodeQueryProfile(
        task=_TASKS[profile.task],
        target_ref=target_ref,
        budget=CodeRetrievalBudget(**CHANNEL_BUDGET),
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if not isinstance(value, Mapping):
        raise CB1Error("CodeExactSparseRetriever returned a non-mapping response")
    return dict(value)


def _channel_name(value: Any) -> str:
    return str(getattr(value, "value", value))


class _CB1EvaluationRetriever:
    """Strict adapter from the C2 CodeSourceResult seam to Evaluation V2."""

    def __init__(
        self,
        retriever: Any,
        runtime: Runtime,
        package: GoldenPackage,
        descriptor: RetrieverDescriptor,
    ) -> None:
        self.retriever = retriever
        self.runtime = runtime
        self.package = package
        self.descriptor = descriptor
        self._case_by_question = {case.request.question: case for case in package.eligible_cases}
        if len(self._case_by_question) != EXPECTED_ELIGIBLE_COUNT:
            raise CB1Error("Golden eligible questions are not unique")

    def search(self, request: Any) -> dict[str, Any]:
        return self.search_evaluation(request, graph_candidate_enabled=False)

    def search_evaluation(
        self,
        request: Any,
        *,
        graph_candidate_enabled: bool,
    ) -> dict[str, Any]:
        if graph_candidate_enabled:
            raise CB1Error("C-B1 is graph-candidate-off")
        case = self._case_by_question.get(str(request.query))
        if case is None:
            raise CB1Error("C-B1 retriever received a query outside released membership")
        search = getattr(self.retriever, "search", None)
        if not callable(search):
            raise CB1Error("C-B1 retriever has no callable search boundary")
        signature = inspect.signature(search)
        positional = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        accepts_varargs = any(
            parameter.kind is inspect.Parameter.VAR_POSITIONAL
            for parameter in signature.parameters.values()
        )
        if accepts_varargs or len(positional) >= 2:
            raw = search(request, _query_profile(case, request))
        elif len(positional) == 1:
            raw = search(request)
        else:
            raise CB1Error("C-B1 retriever search signature cannot accept an evaluation request")
        return self._adapt_response(request, _as_mapping(raw))

    def _adapt_response(self, request: Any, payload: dict[str, Any]) -> dict[str, Any]:
        if "results" in payload and "index_generation" in payload:
            response = dict(payload)
            raw_results = response.get("results")
            if not isinstance(raw_results, list):
                raise CB1Error("legacy-shaped C-B1 response results must be a list")
            results = [dict(item) for item in raw_results if isinstance(item, Mapping)]
            if len(results) != len(raw_results):
                raise CB1Error("legacy-shaped C-B1 response contains a non-mapping candidate")
            for item in results:
                if not (item.get("retrieval_unit_id") or item.get("unit_id")):
                    raise CB1Error("C-B1 candidate does not expose retrieval-unit identity")
            response["results"] = results
            trace = dict(response.get("trace") or {})
        elif "candidates" in payload and "index_version" in payload:
            response, trace = self._adapt_code_source_result(request, payload)
        else:
            raise CB1Error("C-B1 response must be Evaluation-shaped or a governed CodeSourceResult")

        generations = self.runtime.store.active_code_generations(
            project_id=request.scope.project_id,
            repository_ids=request.scope.repository_ids or None,
        )
        expected_generations = sorted(generations.values())
        observed_generations = sorted(str(item) for item in response.get("index_generation") or [])
        if observed_generations != expected_generations:
            raise CB1Error("retriever response generation set differs from active AST publications")
        trace.update(
            {
                "retriever_version": self.descriptor.version,
                "retriever_class": self.descriptor.class_name,
                "fusion": self.descriptor.fusion,
                "index_family": self.descriptor.index_family,
                "unit_builder_version": "ast-v2",
                "retrieval_unit_capable": True,
                "graph_candidate_enabled": False,
            }
        )
        for channel in self.descriptor.channels:
            if not isinstance(trace.get(f"{channel}_outcome"), str):
                raise CB1Error(f"retriever trace lacks {channel}_outcome execution evidence")
        return {**response, "trace": trace}

    def _adapt_code_source_result(
        self,
        request: Any,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        raw_candidates = payload.get("candidates")
        if not isinstance(raw_candidates, list):
            raise CB1Error("CodeSourceResult candidates must be a list after serialization")
        candidates: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        for raw_candidate in raw_candidates:
            if not isinstance(raw_candidate, Mapping):
                raise CB1Error("CodeSourceResult contains a non-mapping candidate")
            candidate = dict(raw_candidate)
            unit_id = str(candidate.get("retrieval_unit_id") or "")
            generation_id = str(candidate.get("source_generation") or "")
            if not unit_id or not generation_id:
                raise CB1Error("CodeSourceResult candidate lacks unit/generation identity")
            unit = self.runtime.store.load_code_unit(
                unit_id,
                project_id=request.scope.project_id,
                repository_id=str(candidate.get("repository_id") or ""),
                generation_id=generation_id,
                allowed_acl_refs=request.scope.allowed_acl_refs,
                active_only=True,
            )
            if unit is None:
                raise CB1Error(
                    "CodeSourceResult candidate retrieval unit is not in the active index"
                )
            raw_scores = candidate.get("raw_channel_scores") or []
            channels = [
                _channel_name(score.get("channel"))
                for score in raw_scores
                if isinstance(score, Mapping) and score.get("channel") is not None
            ]
            relation_path = candidate.get("relation_path") or {}
            for edge in relation_path.get("edges") or []:
                if isinstance(edge, Mapping):
                    relations.append(
                        {
                            "source": edge.get("source_entity_id"),
                            "target": edge.get("target_entity_id"),
                            "edge_type": _channel_name(edge.get("edge_type")),
                        }
                    )
            candidates.append(
                {
                    "entity_id": candidate.get("entity_id"),
                    "retrieval_unit_id": unit_id,
                    "entity_type": candidate.get("entity_type"),
                    "path": unit.get("path"),
                    "start_line": unit.get("start_line"),
                    "end_line": unit.get("end_line"),
                    "locator": candidate.get("locator"),
                    "version": candidate.get("stable_version"),
                    "acl_ref": candidate.get("acl_ref"),
                    "channels": channels,
                    "context_roles": [candidate.get("role")] if candidate.get("role") else [],
                    "rank": int(candidate.get("within_source_rank") or 0) + 1,
                }
            )
        outcomes = payload.get("channel_outcomes")
        if not isinstance(outcomes, list):
            raise CB1Error("CodeSourceResult channel_outcomes must be serialized as a list")
        trace: dict[str, Any] = {
            "duration_ms": payload.get("latency_ms"),
            "index_version": payload.get("index_version"),
        }
        for raw_outcome in outcomes:
            if not isinstance(raw_outcome, Mapping):
                raise CB1Error("CodeSourceResult has a non-mapping channel outcome")
            channel = _channel_name(raw_outcome.get("channel"))
            status = _channel_name(raw_outcome.get("status"))
            trace[f"{channel}_outcome"] = status
            if raw_outcome.get("hit_count") is not None:
                trace[f"{channel}_candidates"] = int(raw_outcome["hit_count"])
        generations = self.runtime.store.active_code_generations(
            project_id=request.scope.project_id,
            repository_ids=request.scope.repository_ids or None,
        )
        return (
            {
                "results": candidates,
                "relations": relations,
                "index_generation": sorted(generations.values()),
            },
            trace,
        )


def _descriptor_config(descriptor: RetrieverDescriptor) -> dict[str, Any]:
    return {
        "schema_version": "code-c-b1-config-snapshot-v2",
        "treatment": "C-B1 AST Unit + exact/sparse",
        "rag_code_unit_builder": "ast-v2",
        "rag_code_dense_index": False,
        "retriever": descriptor.to_dict(),
        "top_k": TOP_K,
        "sparse_index_version": SPARSE_INDEX_VERSION,
        "channel_budget": CHANNEL_BUDGET,
        "embedding": "disabled",
        "graph_candidate": False,
        "semantic_resolver": "off",
        "reranker": "off",
        "context": "snippet-v1",
        "capability_boundary": CAPABILITY_BOUNDARY,
        "baseline_run_id": CB0_QUALIFIED_RUN_ID,
    }


def _legacy_descriptor_config_v1(descriptor: RetrieverDescriptor) -> dict[str, Any]:
    """Reconstruct the exact immutable pre-dense-switch C-B1 config contract."""

    legacy = _descriptor_config(descriptor)
    legacy["schema_version"] = "code-c-b1-config-snapshot-v1"
    legacy.pop("rag_code_dense_index")
    return legacy


def _validate_descriptor_config_snapshot(
    config: object,
    descriptor: RetrieverDescriptor,
) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise CB1Error("embedded C-B1 configuration is not an object")
    current = _descriptor_config(descriptor)
    legacy = _legacy_descriptor_config_v1(descriptor)
    if config != current and config != legacy:
        raise CB1Error("embedded C-B1 configuration differs from the retriever observation")
    return config


def _safe_source_descriptor(source: Any) -> dict[str, Any]:
    descriptor = source.to_dict()
    descriptor.pop("path", None)
    return descriptor


def _materialization_observation(prepared: PreparedCB1) -> dict[str, Any]:
    publications = []
    for publication in prepared.publications:
        publications.append(
            {
                key: publication.get(key)
                for key in (
                    "generation_id",
                    "repository_id",
                    "project_id",
                    "builder",
                    "sparse",
                    "embedding",
                    "graph",
                    "status",
                    "validation",
                )
            }
        )
    observation = {
        "schema_version": "code-c-b1-materialization-v1",
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "package_hash": PACKAGE_HASH,
        "materializer": "tests/fixtures/code_golden/v2/materializer.py:materialize_all",
        "resolver": "RepositoryResolver",
        "parser": "CodeParser",
        "ingestion": "IngestionService",
        "unit_builder": "ast-v2",
        "database_isolated": True,
        "production_database_accessed": False,
        "sources": {
            source_class: _safe_source_descriptor(source)
            for source_class, source in sorted(prepared.sources.items())
        },
        "ingestion_observations": list(prepared.ingestion),
        "ingest_time_ms": {
            "status": "available",
            "value": prepared.ingest_time_ms,
            "per_source": prepared.source_ingest_time_ms,
            "clock": "time.perf_counter",
        },
        "cb0_ingest_time_comparison": {
            "baseline_run_id": CB0_QUALIFIED_RUN_ID,
            "baseline": {
                "status": "unavailable",
                "value": None,
                "reason": "Qualified C-B0 did not persist a total ingestion duration.",
            },
            "treatment": {
                "status": "available",
                "value": prepared.ingest_time_ms,
            },
            "delta": {
                "status": "unavailable",
                "treatment_minus_baseline": None,
                "reason": "C-B0 ingestion duration is unavailable; no delta is fabricated.",
            },
        },
        "ast_units": prepared.unit_count,
        "fts_rows": prepared.fts_row_count,
        "publications": publications,
    }
    return {**observation, "observation_fingerprint": _fingerprint(observation)}


def _case_map(package: GoldenPackage) -> dict[str, GoldenCase]:
    return {
        f"evaluation-case://{PROJECT_ID}/{case.canonical_id}": case
        for case in package.eligible_cases
    }


def _selector_members(package: GoldenPackage) -> dict[str, tuple[str, ...]]:
    selectors: dict[str, list[str]] = {
        "overall": [],
        "exact": [],
        "identifier": [],
        "low_overlap": [],
        "same_name_hard_negative": [],
    }
    for case in package.eligible_cases:
        case_id = f"evaluation-case://{PROJECT_ID}/{case.canonical_id}"
        selectors["overall"].append(case_id)
        profile = case.request.code_profile
        if profile is not None and profile.task == "exact_location":
            selectors["exact"].append(case_id)
        if case.query_style == "identifier_heavy":
            selectors["identifier"].append(case_id)
        if "low_lexical_overlap" in case.slices:
            selectors["low_overlap"].append(case_id)
        if "same_name_hard_negative" in case.slices:
            selectors["same_name_hard_negative"].append(case_id)
    return {name: tuple(sorted(values)) for name, values in selectors.items()}


def _membership_record(
    package: GoldenPackage,
    members: Sequence[str],
) -> dict[str, Any]:
    by_id = _case_map(package)
    canonical_ids = [by_id[case_id].canonical_id for case_id in members]
    return {
        "case_count": len(members),
        "case_membership_hash": _fingerprint(canonical_ids),
        "canonical_case_ids": canonical_ids,
    }


def _aggregate_selected(
    run: dict[str, Any],
    members: Sequence[str],
    metric_name: str,
) -> dict[str, Any]:
    member_set = set(members)
    items = [
        item
        for item in run.get("metric_values") or []
        if item.get("case_id") in member_set
        and item.get("metric_name") == metric_name
        and not item.get("slice")
    ]
    eligible = [item for item in items if item.get("eligible", True)]
    available = [item for item in eligible if item.get("status") == "available"]
    record: dict[str, Any] = {
        "metric_name": metric_name,
        "membership_case_count": len(members),
        "eligible_cases": len(eligible),
        "available_cases": len(available),
        "unavailable_cases": len(eligible) - len(available),
    }
    if not available:
        return {
            **record,
            "status": "unavailable",
            "value": None,
            "numerator": 0.0,
            "denominator": 0.0,
            "reason": (
                "metric unavailable for every eligible case in this fixed membership"
                if eligible
                else "metric is not applicable to this fixed membership"
            ),
        }
    numerators = [item.get("numerator") for item in available]
    denominators = [item.get("denominator") for item in available]
    if all(value is not None for value in (*numerators, *denominators)):
        numerator = sum(float(value) for value in numerators)
        denominator = sum(float(value) for value in denominators)
        value = (
            numerator / denominator
            if denominator
            else sum(float(item["value"]) for item in available) / len(available)
        )
    else:
        numerator = sum(float(item["value"]) for item in available)
        denominator = float(len(available))
        value = numerator / denominator
    return {
        **record,
        "status": "available",
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "reason": None,
    }


def _percentile(run: dict[str, Any], members: Sequence[str], fraction: float) -> dict[str, Any]:
    member_set = set(members)
    values = sorted(
        float(item["value"])
        for item in run.get("metric_values") or []
        if item.get("case_id") in member_set
        and item.get("metric_name") == "latency_ms"
        and not item.get("slice")
        and item.get("status") == "available"
    )
    record = {
        "metric_name": "latency_p95_ms",
        "membership_case_count": len(members),
        "eligible_cases": len(members),
        "available_cases": len(values),
        "unavailable_cases": len(members) - len(values),
    }
    if not values:
        return {
            **record,
            "status": "unavailable",
            "value": None,
            "numerator": 0.0,
            "denominator": 0.0,
            "reason": "no case in this fixed membership exposes latency",
        }
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    value = values[lower] * (1.0 - weight) + values[upper] * weight
    return {
        **record,
        "status": "available",
        "value": value,
        "numerator": value,
        "denominator": 1.0,
        "reason": None,
    }


_COMPARISON_SPECS = (
    ("overall", "entity_recall@10", "higher"),
    ("overall", "mrr@10", "higher"),
    ("overall", "expected_locator_recall@10", "higher"),
    ("overall", "hard_negative_error@10", "lower"),
    ("overall", "entity_duplicate_rate@10", "lower"),
    ("overall", "unit_duplicate_rate@10", "lower"),
    ("exact", "entity_recall@10", "higher"),
    ("exact", "expected_locator_recall@10", "higher"),
    ("identifier", "entity_recall@10", "higher"),
    ("identifier", "mrr@10", "higher"),
    ("low_overlap", "entity_recall@10", "higher"),
    ("low_overlap", "mrr@10", "higher"),
    ("same_name_hard_negative", "hard_negative_error@10", "lower"),
    ("same_name_hard_negative", "harmful_candidate_rate@10", "lower"),
)


def _comparison_row(
    scope: str,
    metric_name: str,
    direction: str,
    baseline_value: dict[str, Any],
    treatment_value: dict[str, Any],
    membership: dict[str, Any],
) -> dict[str, Any]:
    delta: dict[str, Any]
    if baseline_value.get("status") == "available" and treatment_value.get("status") == "available":
        absolute = float(treatment_value["value"]) - float(baseline_value["value"])
        directional = absolute if direction == "higher" else -absolute
        delta = {
            "status": "available",
            "treatment_minus_baseline": absolute,
            "directional_delta": directional,
            "direction": direction,
        }
    else:
        delta = {
            "status": "unavailable",
            "treatment_minus_baseline": None,
            "directional_delta": None,
            "direction": direction,
            "reason": "baseline and treatment must both expose this metric",
        }
    return {
        "scope": scope,
        "metric_name": metric_name,
        "membership": membership,
        "same_case_membership": True,
        "baseline": baseline_value,
        "treatment": treatment_value,
        "metric_denominator_equal": (
            baseline_value.get("denominator") == treatment_value.get("denominator")
        ),
        "delta": delta,
    }


def _comparison_report(
    treatment_run: dict[str, Any],
    baseline_run: dict[str, Any],
    package: GoldenPackage,
) -> dict[str, Any]:
    selectors = _selector_members(package)
    memberships = {
        name: _membership_record(package, members) for name, members in selectors.items()
    }
    rows = []
    for scope, metric_name, direction in _COMPARISON_SPECS:
        members = selectors[scope]
        rows.append(
            _comparison_row(
                scope,
                metric_name,
                direction,
                _aggregate_selected(baseline_run, members, metric_name),
                _aggregate_selected(treatment_run, members, metric_name),
                memberships[scope],
            )
        )
    members = selectors["overall"]
    rows.append(
        _comparison_row(
            "overall",
            "latency_p95_ms",
            "lower",
            _percentile(baseline_run, members, 0.95),
            _percentile(treatment_run, members, 0.95),
            memberships["overall"],
        )
    )
    return {
        "schema_version": "code-c-b1-comparison-v1",
        "baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "treatment_run_id": treatment_run["id"],
        "dataset_package_hash": PACKAGE_HASH,
        "released_eligible_membership_hash": package.membership_hash,
        "selectors": memberships,
        "comparisons": rows,
    }


def _comparison_lookup(
    comparison: dict[str, Any],
    scope: str,
    metric_name: str,
) -> dict[str, Any]:
    try:
        return next(
            row
            for row in comparison["comparisons"]
            if row["scope"] == scope and row["metric_name"] == metric_name
        )
    except StopIteration as exc:
        raise CB1Error(f"required comparison is missing: {scope}/{metric_name}") from exc


def _acceptance(comparison: dict[str, Any]) -> dict[str, Any]:
    specifications = (
        ("exact_non_regression", "exact", "entity_recall@10"),
        ("identifier_non_regression", "identifier", "entity_recall@10"),
        ("locator_non_regression", "overall", "expected_locator_recall@10"),
    )
    gates = []
    for name, scope, metric_name in specifications:
        row = _comparison_lookup(comparison, scope, metric_name)
        delta = row["delta"]
        if delta["status"] != "available":
            status = "unavailable"
            passed = False
            reason = "required baseline/treatment comparison is unavailable"
        else:
            passed = float(delta["directional_delta"]) >= 0.0
            status = "passed" if passed else "failed"
            reason = None if passed else "treatment regressed against qualified C-B0"
        gates.append(
            {
                "gate": name,
                "scope": scope,
                "metric_name": metric_name,
                "status": status,
                "passed": passed,
                "reason": reason,
                "delta": delta,
            }
        )
    return {
        "schema_version": "code-c-b1-acceptance-v1",
        "decision": "passed" if all(item["passed"] for item in gates) else "not-qualified",
        "gates": gates,
    }


def _coverage_report(run: dict[str, Any], package: GoldenPackage) -> dict[str, Any]:
    selectors = _selector_members(package)
    return {
        "schema_version": "code-c-b1-coverage-v1",
        "run_id": run["id"],
        "dataset": {
            "id": DATASET_ID,
            "version": DATASET_VERSION,
            "package_hash": PACKAGE_HASH,
            "case_membership_hash": package.membership_hash,
        },
        "total_cases": EXPECTED_CASE_COUNT,
        "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
        "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
        "result_count": len(run.get("results") or []),
        "enabled_case_names": [case.canonical_id for case in package.eligible_cases],
        "required_slice_membership": {
            name: _membership_record(package, members)
            for name, members in selectors.items()
            if name != "overall"
        },
        "ineligible": [
            {
                "case_id": case.canonical_id,
                "task": (
                    case.request.code_profile.task
                    if case.request.code_profile is not None
                    else None
                ),
                "source_class": case.source_class,
                "reason": case.unavailable_reason,
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
        ],
        "capability_boundary": CAPABILITY_BOUNDARY,
        "denominator_policy": {
            "eligible_case_count": EXPECTED_ELIGIBLE_COUNT,
            "case_membership_hash": package.membership_hash,
            "ineligible_cases_are_reported_not_silently_dropped": True,
        },
    }


def _metrics_report(
    run: dict[str, Any],
    baseline_run: dict[str, Any],
    package: GoldenPackage,
) -> dict[str, Any]:
    comparison = _comparison_report(run, baseline_run, package)
    return {
        "schema_version": "code-c-b1-metrics-v1",
        "run_id": run["id"],
        "authoritative_contract": "evaluation_metric_values",
        "summary": run["summary"],
        "metric_values": run["metric_values"],
        "cb0_comparison": comparison,
        "acceptance": _acceptance(comparison),
    }


def _slices_report(
    run: dict[str, Any],
    baseline_run: dict[str, Any],
    package: GoldenPackage,
) -> dict[str, Any]:
    try:
        full = baseline._slices_report(run, package)
    except Exception as exc:
        raise CB1Error(f"unable to derive treatment slices: {exc}") from exc
    comparison = _comparison_report(run, baseline_run, package)
    required = [
        row
        for row in comparison["comparisons"]
        if row["scope"] in {"exact", "identifier", "low_overlap", "same_name_hard_negative"}
    ]
    return {
        **full,
        "schema_version": "code-c-b1-slices-v1",
        "required_treatment_slices": required,
        "selector_contract": {
            "exact": "code_profile.task == exact_location",
            "identifier": "query_style == identifier_heavy",
            "low_overlap": "Golden slice contains low_lexical_overlap",
            "same_name_hard_negative": "Golden slice contains same_name_hard_negative",
        },
    }


def _error_analysis_report(
    run: dict[str, Any],
    baseline_run: dict[str, Any],
    package: GoldenPackage,
) -> dict[str, Any]:
    try:
        full = baseline._error_analysis_report(run, package)
    except Exception as exc:
        raise CB1Error(f"unable to derive treatment error analysis: {exc}") from exc
    comparison = _comparison_report(run, baseline_run, package)
    same_name = [
        row for row in comparison["comparisons"] if row["scope"] == "same_name_hard_negative"
    ]
    duplicate = [
        row
        for row in comparison["comparisons"]
        if row["scope"] == "overall" and "duplicate_rate" in row["metric_name"]
    ]
    return {
        **full,
        "schema_version": "code-c-b1-error-analysis-v1",
        "same_name_hard_negative_slice": same_name,
        "duplicate_comparison": duplicate,
        "unavailable_cases": [
            {
                "case_id": case.canonical_id,
                "reason": case.unavailable_reason,
            }
            for case in package.ineligible_cases
        ],
    }


def _latency_report(
    run: dict[str, Any],
    baseline_run: dict[str, Any],
    package: GoldenPackage,
) -> dict[str, Any]:
    comparison = _comparison_report(run, baseline_run, package)
    p95 = _comparison_lookup(comparison, "overall", "latency_p95_ms")
    return {
        "schema_version": "code-c-b1-latency-v1",
        "run_id": run["id"],
        "retrieval_p95": p95,
        "cases": [
            {
                "case_id": result["name"],
                "latency_ms": result["latency_ms"],
                "candidate_count": result["detail"].get("candidate_count"),
                "exact_candidates": result["detail"].get("trace", {}).get("exact_candidates"),
                "sparse_candidates": result["detail"].get("trace", {}).get("sparse_candidates"),
            }
            for result in run.get("results") or []
        ],
    }


def _dbstat_bytes(database: sqlite3.Connection, names: Sequence[str]) -> dict[str, Any]:
    marks = ",".join("?" for _ in names)
    try:
        value = int(
            database.execute(
                f"SELECT coalesce(sum(pgsize), 0) FROM dbstat WHERE name IN ({marks})",
                list(names),
            ).fetchone()[0]
        )
    except sqlite3.DatabaseError:
        return {
            "status": "unavailable",
            "value": None,
            "reason": "SQLite dbstat is not available in this runtime.",
        }
    return {"status": "available", "value": value, "source": "SQLite dbstat"}


def _storage_report(
    database_path: Path,
    run_id: str,
    cb0: CB0Anchor,
) -> dict[str, Any]:
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        page_size = int(database.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(database.execute("PRAGMA page_count").fetchone()[0])
        free_pages = int(database.execute("PRAGMA freelist_count").fetchone()[0])
        unit_count = int(
            database.execute("SELECT count(*) FROM code_retrieval_units").fetchone()[0]
        )
        vector_count = int(database.execute("SELECT count(*) FROM code_unit_vectors").fetchone()[0])
        fts_count = int(
            database.execute("SELECT count(*) FROM code_retrieval_units_fts").fetchone()[0]
        )
        logical_bytes = int(
            database.execute(
                """SELECT coalesce(sum(
                       length(content) + length(qualified_name) + length(signature)
                       + length(path) + length(identifiers) + length(doc) + length(body)
                   ), 0)
                   FROM code_retrieval_units"""
            ).fetchone()[0]
        )
        physical = _dbstat_bytes(
            database,
            (
                "code_retrieval_units",
                "idx_code_units_scope",
                "idx_code_units_entity",
                "idx_code_units_parent",
                "idx_code_units_exact_name",
                "idx_code_units_exact_path",
                "code_retrieval_units_fts_data",
                "code_retrieval_units_fts_idx",
                "code_retrieval_units_fts_docsize",
                "code_retrieval_units_fts_config",
            ),
        )
    cb0_storage = _read_json(cb0.directory / "storage.json")
    baseline_logical = cb0_storage.get("active_code_index_logical_bytes")
    logical_delta = (
        {
            "status": "available",
            "treatment_minus_baseline": logical_bytes - int(baseline_logical),
            "measurement": "logical indexed payload bytes",
        }
        if isinstance(baseline_logical, int)
        else {
            "status": "unavailable",
            "treatment_minus_baseline": None,
            "reason": "qualified C-B0 does not expose logical index bytes",
        }
    )
    return {
        "schema_version": "code-c-b1-storage-v1",
        "run_id": run_id,
        "database_bytes": database_path.stat().st_size,
        "sqlite_page_size": page_size,
        "sqlite_page_count": page_count,
        "sqlite_free_pages": free_pages,
        "sqlite_free_bytes": page_size * free_pages,
        "ast_unit_count": unit_count,
        "fts_row_count": fts_count,
        "vector_count": vector_count,
        "exact_sparse_index_logical_bytes": logical_bytes,
        "exact_sparse_index_physical_bytes": physical,
        "cb0_comparison": {
            "baseline_run_id": CB0_QUALIFIED_RUN_ID,
            "baseline_active_code_index_logical_bytes": baseline_logical,
            "treatment_exact_sparse_index_logical_bytes": logical_bytes,
            "logical_index_bytes_delta": logical_delta,
            "physical_index_bytes_delta": {
                "status": "unavailable",
                "value": None,
                "reason": (
                    "C-B0 and C-B1 do not expose the same SQLite physical index family; "
                    "both absolute values remain reported without inventing a delta."
                ),
            },
        },
    }


def _report_payloads(
    run: dict[str, Any],
    package: GoldenPackage,
    database_path: Path,
    cb0: CB0Anchor,
) -> dict[str, dict[str, Any]]:
    baseline_run = cb0.run
    declared = (run.get("config") or {}).get("declared") or {}
    materialization = declared.get("materialization_observation")
    if not isinstance(materialization, dict):
        raise CB1Error("Run does not retain the materialization observation")
    return {
        "materialization.json": materialization,
        "coverage.json": _coverage_report(run, package),
        "metrics.json": _metrics_report(run, baseline_run, package),
        "slices.json": _slices_report(run, baseline_run, package),
        "error-analysis.json": _error_analysis_report(run, baseline_run, package),
        "latency.json": _latency_report(run, baseline_run, package),
        "storage.json": _storage_report(database_path, run["id"], cb0),
    }


def _validate_run(
    run: dict[str, Any],
    loaded: LoadedGolden,
    descriptor: RetrieverDescriptor,
    database_path: Path,
) -> None:
    if (
        run.get("status") != "completed"
        or run.get("source_domain") != "code"
        or run.get("runner_version") != baseline.RUNNER_VERSION
        or run.get("dataset_id") != DATASET_ID
        or run.get("dataset_version") != DATASET_VERSION
        or run.get("dataset_package_hash") != PACKAGE_HASH
        or run.get("graph_candidate_enabled") is not False
        or int(run.get("case_count") or 0) != EXPECTED_ELIGIBLE_COUNT
        or len(run.get("results") or []) != EXPECTED_ELIGIBLE_COUNT
        or sorted(run.get("snapshot", {}).get("case_ids") or []) != sorted(loaded.case_ids)
    ):
        raise CB1Error("C-B1 did not produce the required completed 33-case graph-off Run")
    result_case_ids = [str(result["case_id"]) for result in run["results"]]
    if len(set(result_case_ids)) != EXPECTED_ELIGIBLE_COUNT or sorted(result_case_ids) != sorted(
        loaded.case_ids
    ):
        raise CB1Error("C-B1 result membership differs from the released denominator")
    declared = (run.get("config") or {}).get("declared") or {}
    _validate_descriptor_config_snapshot(declared.get("cb1_config_snapshot"), descriptor)
    request = (run.get("config") or {}).get("request") or {}
    if (
        request.get("case_ids") != list(loaded.case_ids)
        or request.get("graph_candidate_enabled") is not False
        or int(request.get("limit_per_query") or 0) != TOP_K
    ):
        raise CB1Error("embedded C-B1 request differs from the fixed runner contract")

    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        active = [
            str(row[0])
            for row in database.execute(
                """SELECT active_generation_id FROM repositories
                   WHERE project_id=? ORDER BY id""",
                (PROJECT_ID,),
            ).fetchall()
        ]
        units = int(database.execute("SELECT count(*) FROM code_retrieval_units").fetchone()[0])
        fts = int(database.execute("SELECT count(*) FROM code_retrieval_units_fts").fetchone()[0])
        publications = [
            {
                "builder": str(row[0]),
                "sparse": str(row[1]),
                "embedding": str(row[2]),
                "graph": str(row[3]),
                "status": str(row[4]),
                "validation": json.loads(str(row[5]) or "{}"),
            }
            for row in database.execute(
                """SELECT builder, sparse, embedding, graph, status, validation_json
                   FROM code_index_publications
                   WHERE project_id=? ORDER BY repository_id""",
                (PROJECT_ID,),
            ).fetchall()
        ]
    if len(active) != 3 or units <= 0 or fts != units:
        raise CB1Error("embedded C-B1 AST index is incomplete")
    if len(publications) != 3 or any(
        publication["builder"] != AST_BUILDER_VERSION
        or publication["embedding"] != "not-built"
        or publication["graph"] != "not-built"
        or publication["status"] != "published"
        for publication in publications
    ):
        raise CB1Error("embedded C-B1 publication set is incomplete")
    real_descriptor = descriptor.class_name == REAL_RETRIEVER_CLASS and (
        descriptor.module == REAL_RETRIEVER_MODULE
        or descriptor.module.startswith(f"{REAL_RETRIEVER_MODULE}.")
    )
    if real_descriptor:
        for publication in publications:
            validation = publication["validation"]
            capabilities = validation.get("capabilities") if isinstance(validation, Mapping) else {}
            treatment = (
                validation.get("cb1_treatment_publication")
                if isinstance(validation, Mapping)
                else {}
            )
            if (
                publication["sparse"] != SPARSE_INDEX_VERSION
                or not isinstance(capabilities, Mapping)
                or capabilities.get("sparse_retrieval") is not True
                or not isinstance(treatment, Mapping)
                or treatment.get("retriever_version") != descriptor.version
                or treatment.get("sparse_index_version") != SPARSE_INDEX_VERSION
                or treatment.get("isolated_evaluation_only") is not True
            ):
                raise CB1Error("real C-B1 Run lacks its validated isolated sparse publication")
    if sorted(run["snapshot"].get("observed_index_generations") or []) != sorted(active):
        raise CB1Error("Run snapshot generation set differs from embedded AST publications")
    for result in run["results"]:
        trace = result.get("detail", {}).get("trace", {})
        expected = {
            "retriever_version": descriptor.version,
            "retriever_class": descriptor.class_name,
            "fusion": descriptor.fusion,
            "index_family": "ast-v2",
            "unit_builder_version": "ast-v2",
            "retrieval_unit_capable": True,
            "graph_candidate_enabled": False,
        }
        if any(trace.get(key) != value for key, value in expected.items()):
            raise CB1Error("case result lacks the required observed C-B1 retriever contract")
        if any(
            not isinstance(trace.get(f"{channel}_outcome"), str) for channel in ("exact", "sparse")
        ):
            raise CB1Error("case result lacks exact/sparse channel execution evidence")


def _artifact_file_records(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "sha256": _sha256_bytes((directory / name).read_bytes()),
            "bytes": (directory / name).stat().st_size,
        }
        for name in sorted(ARTIFACT_FILES)
    }


def _manifest(
    run: dict[str, Any],
    package: GoldenPackage,
    descriptor: RetrieverDescriptor,
    cb0: CB0Anchor,
    artifact_files: dict[str, dict[str, Any]],
    input_fingerprint: dict[str, Any],
    execution_mode: ExecutionMode,
    acceptance: dict[str, Any],
    *,
    config_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    qualified = execution_mode == "qualified" and acceptance["decision"] == "passed"
    snapshot = run["snapshot"]
    resolved_config = config_snapshot or _descriptor_config(descriptor)
    return {
        "schema_version": CB1_SCHEMA_VERSION,
        "status": "completed",
        "execution_mode": execution_mode,
        "treatment_qualified": qualified,
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
        "baseline": {
            "run_id": CB0_QUALIFIED_RUN_ID,
            "artifact_directory": CB0_ARTIFACT_DIRECTORY,
            "qualification_record_hash": cb0.verification["qualification_record_hash"],
            "manifest_canonical_hash": cb0.verification["manifest_canonical_hash"],
            "artifact_set_hash": cb0.manifest["artifact_set_hash"],
        },
        "config": resolved_config,
        "config_fingerprint": _fingerprint(resolved_config),
        "snapshot": {
            "snapshot_id": snapshot.get("snapshot_id"),
            "comparison_fingerprint": snapshot.get("comparison_fingerprint"),
            "case_membership_hash": snapshot.get("case_membership_hash"),
            "schema_version": snapshot.get("schema_version"),
            "observed_index_generations": snapshot.get("observed_index_generations"),
            "observed_retriever": snapshot.get("observed_retriever"),
        },
        "implementation": {
            "runner": CB1_RUNNER_VERSION,
            "evaluation_runner": run["runner_version"],
            "input_fingerprint": input_fingerprint,
            "input_fingerprint_after": input_fingerprint,
            "input_fingerprint_matched": True,
        },
        "acceptance": acceptance,
        "artifacts": artifact_files,
        "artifact_set_hash": _fingerprint(artifact_files),
        "security": {
            **baseline.SECURITY_DECLARATION,
            "portable_placeholders": ["<redacted-temp-path>"],
        },
    }


def _descriptor_from_manifest(manifest: dict[str, Any]) -> RetrieverDescriptor:
    retriever = (manifest.get("config") or {}).get("retriever") or {}
    channels = retriever.get("channels")
    if not isinstance(channels, list):
        raise CB1Error("manifest retriever channels are invalid")
    return RetrieverDescriptor(
        module=str(retriever.get("module") or ""),
        class_name=str(retriever.get("class") or ""),
        version=str(retriever.get("version") or ""),
        fusion=str(retriever.get("fusion") or ""),
        channels=tuple(str(item) for item in channels),
        index_family=str(retriever.get("index_family") or ""),
    )


def _require_exact_file_set(directory: Path) -> None:
    entries = {path.name for path in directory.iterdir()}
    if entries != EXPECTED_ARTIFACT_ENTRIES:
        raise CB1Error("artifact directory contains an unexpected or missing entry")
    if any(path.is_symlink() or not path.is_file() for path in directory.iterdir()):
        raise CB1Error("artifact entries must be regular files, not symlinks")


def verify_artifact(
    directory: Path,
    *,
    root: Path | None = None,
    allow_test_artifact: bool = False,
) -> dict[str, Any]:
    directory = directory.resolve()
    resolved_root = (root or repository_root()).resolve()
    _require_exact_file_set(directory)
    manifest = _read_json(directory / "manifest.json")
    if (
        manifest.get("schema_version") != CB1_SCHEMA_VERSION
        or manifest.get("status") != "completed"
    ):
        raise CB1Error("artifact is not a completed C-B1 treatment")
    execution_mode = manifest.get("execution_mode")
    if execution_mode not in {"qualified", "test"}:
        raise CB1Error("artifact execution_mode is invalid")
    if execution_mode == "test" and not allow_test_artifact:
        raise CB1Error("test-only C-B1 artifacts require explicit allow_test_artifact=True")

    package = validate_golden_package(resolved_root)
    cb0 = _load_cb0_anchor(resolved_root)
    baseline_anchor = manifest.get("baseline") or {}
    if (
        baseline_anchor.get("run_id") != CB0_QUALIFIED_RUN_ID
        or baseline_anchor.get("artifact_directory") != CB0_ARTIFACT_DIRECTORY
        or baseline_anchor.get("qualification_record_hash")
        != cb0.verification["qualification_record_hash"]
        or baseline_anchor.get("manifest_canonical_hash")
        != cb0.verification["manifest_canonical_hash"]
        or baseline_anchor.get("artifact_set_hash") != cb0.manifest["artifact_set_hash"]
    ):
        raise CB1Error("manifest is not anchored to the fixed qualified C-B0 Run")

    expected_files = _artifact_file_records(directory)
    if manifest.get("artifacts") != expected_files or manifest.get(
        "artifact_set_hash"
    ) != _fingerprint(expected_files):
        raise CB1Error("artifact file-set fingerprint mismatch")
    database_path = directory / "evaluation.sqlite3"
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as database:
        quick_check = str(database.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_failures = database.execute("PRAGMA foreign_key_check").fetchall()
        run_ids = [
            str(row[0])
            for row in database.execute("SELECT id FROM evaluation_runs ORDER BY id").fetchall()
        ]
    if quick_check != "ok" or foreign_key_failures or len(run_ids) != 1:
        raise CB1Error("artifact SQLite integrity or single-Run contract failed")
    if manifest.get("run_id") != run_ids[0]:
        raise CB1Error("manifest Run identity differs from embedded SQLite")

    loaded = baseline._verify_embedded_golden(database_path, package)
    store = EvaluationStore(baseline._ReadOnlySQLiteStore(database_path))
    run = store.get_run(run_ids[0])
    if run is None:
        raise CB1Error("artifact database does not contain its Run")
    descriptor = _descriptor_from_manifest(manifest)
    _validate_run(run, loaded, descriptor, database_path)
    baseline.assert_terminal_immutability(database_path, run["id"])

    reports = _report_payloads(run, package, database_path, cb0)
    for name, expected in reports.items():
        if _read_json(directory / name) != expected:
            raise CB1Error(f"artifact report cannot be recomputed: {name}")
    metrics = reports["metrics.json"]
    acceptance = metrics["acceptance"]
    audit = _read_json(directory / "attempt-audit.json")
    expected_audit = {
        "schema_version": "code-c-b1-attempt-audit-v1",
        "status": "completed",
        "execution_mode": execution_mode,
        "treatment_qualified": (
            execution_mode == "qualified" and acceptance["decision"] == "passed"
        ),
        "started_at": audit.get("started_at"),
        "completed_at": run["completed_at"],
        "run_id": run["id"],
        "baseline_run_id": CB0_QUALIFIED_RUN_ID,
    }
    if audit != expected_audit or audit.get("started_at") is None:
        raise CB1Error("attempt audit identity or terminal state is invalid")

    declared = (run.get("config") or {}).get("declared") or {}
    config_snapshot = _validate_descriptor_config_snapshot(
        declared.get("cb1_config_snapshot"),
        descriptor,
    )
    input_fingerprint = declared.get("workspace_input_fingerprint")
    if (
        not isinstance(input_fingerprint, dict)
        or input_fingerprint.get("package_hash") != PACKAGE_HASH
        or input_fingerprint.get("cb0_qualified_run_id") != CB0_QUALIFIED_RUN_ID
        or not str(input_fingerprint.get("cb1_treatment_input_hash") or "").startswith("sha256:")
    ):
        raise CB1Error("embedded workspace input fingerprint is incomplete")
    expected_manifest = _manifest(
        run,
        package,
        descriptor,
        cb0,
        expected_files,
        input_fingerprint,
        execution_mode,
        acceptance,
        config_snapshot=config_snapshot,
    )
    if manifest != expected_manifest:
        raise CB1Error("manifest cannot be reconstructed from immutable C-B1 inputs")

    if execution_mode == "qualified":
        if descriptor.class_name != REAL_RETRIEVER_CLASS or (
            descriptor.module != REAL_RETRIEVER_MODULE
            and not descriptor.module.startswith(f"{REAL_RETRIEVER_MODULE}.")
        ):
            raise CB1Error("real artifact lacks the real C2-05 retriever path")
        expected_qualified = acceptance["decision"] == "passed"
        if manifest.get("treatment_qualified") is not expected_qualified:
            raise CB1Error("real artifact qualification flag differs from its acceptance gates")
    elif manifest.get("treatment_qualified") is not False:
        raise CB1Error("test-only artifacts can never be treatment-qualified")

    try:
        security = baseline._require_clean_security_scan(directory)
    except Exception as exc:
        raise CB1Error(f"artifact security scan failed: {exc}") from exc
    return {
        "status": "verified" if execution_mode == "qualified" else "verified-test-artifact",
        "run_id": run["id"],
        "execution_mode": execution_mode,
        "treatment_qualified": manifest["treatment_qualified"],
        "dataset_package_hash": PACKAGE_HASH,
        "case_membership_hash": package.membership_hash,
        "case_count": run["case_count"],
        "result_count": len(run["results"]),
        "baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "artifact_set_hash": manifest["artifact_set_hash"],
        "manifest_canonical_hash": _fingerprint(manifest),
        "acceptance": acceptance,
        "security_scan": security,
    }


def _production_database_state(
    root: Path,
    *,
    include_hash: bool = True,
) -> tuple[bool, int | None, int | None, str | None]:
    path = (root / "var" / "evidence-rag.sqlite3").resolve()
    if not path.exists():
        return False, None, None, None
    stat = path.stat()
    digest_value: str | None = None
    if include_hash:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        digest_value = "sha256:" + digest.hexdigest()
    return True, stat.st_size, stat.st_mtime_ns, digest_value


def run_cb1(
    *,
    root: Path | None = None,
    runs_dir: Path | None = None,
    execution_mode: ExecutionMode = "qualified",
    retriever_factory: RetrieverFactory | None = None,
) -> Path:
    resolved_root = (root or repository_root()).resolve()
    resolved_runs = (
        runs_dir.resolve()
        if runs_dir is not None
        else (resolved_root / "evals" / "code" / "runs").resolve()
    )
    repository_runs = (resolved_root / "evals" / "code" / "runs").resolve()
    if execution_mode == "test":
        if retriever_factory is None:
            raise CB1Error("test execution requires an explicit fake retriever factory")
        if resolved_runs == repository_runs or resolved_runs.is_relative_to(repository_runs):
            raise CB1Error("test execution may not write under evals/code/runs")
    elif execution_mode == "qualified":
        if retriever_factory is not None:
            raise CB1Error("qualified execution forbids retriever injection")
    else:
        raise CB1Error("execution_mode must be qualified or test")

    cb0 = _load_cb0_anchor(resolved_root)
    include_production_hash = execution_mode == "qualified"
    production_before = _production_database_state(
        resolved_root,
        include_hash=include_production_hash,
    )
    input_fingerprint = workspace_input_fingerprint(resolved_root, resolved_runs)
    final_dir: Path | None = None
    with tempfile.TemporaryDirectory(prefix="code-c-b1-") as temporary:
        temporary_root = Path(temporary).resolve()
        prepared = prepare_cb1_environment(temporary_root / "work", root=resolved_root)
        retriever = (
            retriever_factory(prepared.runtime)
            if retriever_factory is not None
            else _load_real_retriever(prepared.runtime)
        )
        descriptor = _retriever_descriptor(retriever)
        if execution_mode == "qualified" and (
            descriptor.class_name != REAL_RETRIEVER_CLASS
            or (
                descriptor.module != REAL_RETRIEVER_MODULE
                and not descriptor.module.startswith(f"{REAL_RETRIEVER_MODULE}.")
            )
        ):
            raise CB1Error("qualified execution did not use the real C2-05 retriever")
        if execution_mode == "qualified":
            _publish_real_treatment_index(prepared, descriptor)
        prepared.runtime.platform.code = _CB1EvaluationRetriever(
            retriever,
            prepared.runtime,
            prepared.package,
            descriptor,
        )
        config_snapshot = _descriptor_config(descriptor)
        request = CodeEvaluationRunRequest(
            project_id=PROJECT_ID,
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            package_hash=PACKAGE_HASH,
            case_ids=list(prepared.loaded.case_ids),
            repository_ids=sorted(source.repository_id for source in prepared.sources.values()),
            graph_candidate_enabled=False,
            limit_per_query=TOP_K,
            config={
                "cb1_config_snapshot": config_snapshot,
                "workspace_input_fingerprint": input_fingerprint,
                "materialization_observation": _materialization_observation(prepared),
            },
        )
        run = prepared.runtime.evaluation.run_code(
            request,
            allowed_acl_refs=[ACL_REF],
            enforce_acl=True,
        )
        _validate_run(
            run,
            prepared.loaded,
            descriptor,
            prepared.database_path,
        )
        baseline.assert_terminal_immutability(prepared.database_path, run["id"])
        post_fingerprint = workspace_input_fingerprint(resolved_root, resolved_runs)
        if post_fingerprint != input_fingerprint:
            raise CB1Error("workspace input fingerprint drifted during C-B1 execution")
        if (
            _production_database_state(
                resolved_root,
                include_hash=include_production_hash,
            )
            != production_before
        ):
            raise CB1Error("formal var/evidence-rag.sqlite3 changed during isolated C-B1 execution")

        token = str(run["id"]).rsplit("/", 1)[-1]
        artifact_dir = temporary_root / "artifact" / token
        artifact_dir.mkdir(parents=True)
        database_artifact = artifact_dir / "evaluation.sqlite3"
        with prepared.runtime.store.connection() as database:
            database.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(prepared.database_path, database_artifact)
        try:
            baseline._sanitize_database(database_artifact, None)
        except Exception as exc:
            raise CB1Error(f"unable to sanitize portable C-B1 SQLite: {exc}") from exc
        persisted_store = EvaluationStore(baseline._ReadOnlySQLiteStore(database_artifact))
        persisted_run = persisted_store.get_run(run["id"])
        if persisted_run is None:
            raise CB1Error("portable artifact database lost the completed C-B1 Run")
        _validate_run(
            persisted_run,
            prepared.loaded,
            descriptor,
            database_artifact,
        )
        baseline.assert_terminal_immutability(database_artifact, run["id"])

        reports = _report_payloads(
            persisted_run,
            prepared.package,
            database_artifact,
            cb0,
        )
        for name, payload in reports.items():
            _write_json(artifact_dir / name, payload)
        acceptance = reports["metrics.json"]["acceptance"]
        qualified = execution_mode == "qualified" and acceptance["decision"] == "passed"
        _write_json(
            artifact_dir / "attempt-audit.json",
            {
                "schema_version": "code-c-b1-attempt-audit-v1",
                "status": "completed",
                "execution_mode": execution_mode,
                "treatment_qualified": qualified,
                "started_at": persisted_run["started_at"],
                "completed_at": persisted_run["completed_at"],
                "run_id": persisted_run["id"],
                "baseline_run_id": CB0_QUALIFIED_RUN_ID,
            },
        )
        try:
            baseline._require_clean_security_scan(artifact_dir)
        except Exception as exc:
            raise CB1Error(f"C-B1 artifact security scan failed: {exc}") from exc
        artifact_files = _artifact_file_records(artifact_dir)
        manifest = _manifest(
            persisted_run,
            prepared.package,
            descriptor,
            cb0,
            artifact_files,
            input_fingerprint,
            execution_mode,
            acceptance,
        )
        _write_json(artifact_dir / "manifest.json", manifest)
        verify_artifact(
            artifact_dir,
            root=resolved_root,
            allow_test_artifact=execution_mode == "test",
        )
        if (
            _production_database_state(
                resolved_root,
                include_hash=include_production_hash,
            )
            != production_before
        ):
            raise CB1Error("formal var/evidence-rag.sqlite3 changed during artifact verification")

        resolved_runs.mkdir(parents=True, exist_ok=True)
        final_dir = resolved_runs / token
        if final_dir.exists():
            raise CB1Error("artifact directory for the completed C-B1 Run already exists")
        artifact_dir.rename(final_dir)

    assert final_dir is not None
    verify_artifact(
        final_dir,
        root=resolved_root,
        allow_test_artifact=execution_mode == "test",
    )
    if (
        _production_database_state(
            resolved_root,
            include_hash=include_production_hash,
        )
        != production_before
    ):
        raise CB1Error("formal var/evidence-rag.sqlite3 changed during final verify-only check")
    return final_dir


def _resolve_artifact(value: str, runs_dir: Path) -> Path:
    candidate = Path(value)
    if candidate.is_dir():
        return candidate.resolve()
    nested = runs_dir / value
    if nested.is_dir():
        return nested.resolve()
    raise CB1Error(f"C-B1 artifact not found: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Code C-B1 AST Unit + exact/sparse treatment")
    parser.add_argument("--repository-root", type=Path, default=repository_root())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "prepare",
        help="validate anchors and print a pending contract without creating artifacts",
    )
    run_parser = subparsers.add_parser(
        "run",
        help="run only the real public CodeExactSparseRetriever treatment",
    )
    run_parser.add_argument("--runs-dir", type=Path)
    verify_parser = subparsers.add_parser("verify", help="verify an existing real C-B1 artifact")
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()
    try:
        if args.command == "prepare":
            output = preparation_status(root=root)
        elif args.command == "run":
            artifact = run_cb1(root=root, runs_dir=args.runs_dir)
            output = verify_artifact(artifact, root=root)
            output["artifact"] = _relative_or_name(artifact, root)
        else:
            runs_dir = (
                args.runs_dir.resolve()
                if args.runs_dir
                else (root / "evals" / "code" / "runs").resolve()
            )
            output = verify_artifact(
                _resolve_artifact(args.artifact, runs_dir),
                root=root,
            )
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
