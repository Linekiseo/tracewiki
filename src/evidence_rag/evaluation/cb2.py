from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import os
import platform
import resource
import shutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..rag.sources.code.contracts import (
    CodeQueryProfile,
    CodeRetrievalBudget,
    CodeTask,
)
from ..rag.sources.code.embedding_v2 import (
    LOCAL_HASH_MODEL,
    LOCAL_HASH_MODEL_REVISION,
    EmbeddingProfile,
)
from ..runtime import Runtime
from . import baseline, cb1
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
    repository_root,
    validate_golden_package,
)
from .models import CodeEvaluationRunRequest
from .store import EvaluationStore

CB2_SCHEMA_VERSION = "code-c-b2-dense-treatment-artifact-v1"
CB2_RUNNER_VERSION = "code-c-b2-runner-v1"
CB0_QUALIFIED_RUN_ID = cb1.CB0_QUALIFIED_RUN_ID
CB0_ARTIFACT_DIRECTORY = cb1.CB0_ARTIFACT_DIRECTORY
CB1_AUDIT_RUN_ID = "evaluation-run://project-code-golden-v2/33f76ed55a3d4c9ab1060a54bfa13d35"
CB1_AUDIT_ARTIFACT_DIRECTORY = "33f76ed55a3d4c9ab1060a54bfa13d35"

PUBLIC_CODE_MODULE = "evidence_rag.rag.sources.code"
REAL_PUBLISHER_CLASS = "CodeDenseProfilePublisher"
REAL_RETRIEVER_CLASS = "CodeHybridV2Retriever"
SPARSE_INDEX_VERSION = cb1.SPARSE_INDEX_VERSION
TOP_K = 20
CHANNEL_BUDGET = {
    "total_candidates": 60,
    "exact_candidates": 20,
    "sparse_candidates": 40,
    "dense_candidates": 40,
    "graph_candidates": 0,
    "history_candidates": 0,
    "test_candidates": 0,
    "graph_node_budget": 0,
    "graph_edge_budget": 0,
    "context_token_budget": 4_000,
}
PROFILE_SNAPSHOT = {
    "batch_size": 64,
    "dimension": 384,
    "id": "code_nl/local-hash-v2",
    "instruction": "",
    "instruction_revision": "none-v1",
    "locality": "local",
    "max_tokens": 8_192,
    "model": LOCAL_HASH_MODEL,
    "normalization_revision": "local-hash-v2",
    "purpose": "code_nl",
    "redaction_policy": "deny-secrets-v1",
    "revision": "1",
}
PROVIDER_PROVENANCE = {
    "provider": "local-hash",
    "model": LOCAL_HASH_MODEL,
    "revision": LOCAL_HASH_MODEL_REVISION,
    "dimension": PROFILE_SNAPSHOT["dimension"],
    "locality": "local",
}
OPTIONAL_CANDIDATES = (
    {
        "candidate": "Qwen3-Embedding-0.6B",
        "status": "unavailable",
        "reason": "not installed locally; C-B2 preparation forbids downloads and network access",
    },
    {
        "candidate": "BGE-M3",
        "status": "unavailable",
        "reason": "not installed locally; C-B2 preparation forbids downloads and network access",
    },
    {
        "candidate": "remote embedding upper bound",
        "status": "unavailable",
        "reason": "remote providers are disabled and no network execution is authorized",
    },
)
CAPABILITY_BOUNDARY = {
    "available": [
        "active-generation ast-v2 retrieval units",
        "exact candidate retrieval",
        "fielded sparse candidate retrieval",
        "local-hash code_nl dense retrieval",
        "complete embedding cache/vector publication",
        "retrieval-unit identity and source locator projection",
    ],
    "unavailable": [
        {
            "capability": "learned local embedding candidates",
            "reason": "Qwen/BGE/code-specific models are optional and unavailable without a local install.",
        },
        {
            "capability": "remote embedding candidates",
            "reason": "C-B2 is offline-only unless a separately governed future run authorizes remote use.",
        },
        {
            "capability": "graph candidate expansion",
            "reason": "C-B2 isolates dense + exact/sparse; graph treatment belongs to C4.",
        },
        {
            "capability": "historical and validation retrieval",
            "reason": "The released 17 ineligible cases stay outside the fixed 33-case denominator.",
        },
    ],
}
REQUIRED_REPORTING = (
    "overall entity/locator recall, MRR, and nDCG",
    "exact entity/locator recall, MRR, and nDCG",
    "identifier entity/locator recall, MRR, and nDCG",
    "low-overlap entity/locator recall, MRR, and nDCG",
    "same-name harmful candidates and duplicates",
    "dense contribution and zero-result recovery",
    "embedding cache hits and misses",
    "vector and index bytes",
    "ingest, index, and query P95",
    "profile/model/revision/dimension/locality",
    "available CPU and RAM observations",
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

ExecutionMode = Literal["qualified", "test"]
PublisherFactory = Callable[[Runtime, EmbeddingProfile], Any]
RetrieverFactory = Callable[[Runtime, EmbeddingProfile], Any]


class CB2Error(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ComponentDescriptor:
    module: str
    class_name: str
    version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "module": self.module,
            "class": self.class_name,
            "version": self.version,
        }


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


@dataclass(frozen=True, slots=True)
class AuditAnchor:
    directory: Path
    manifest: dict[str, Any]
    verification: dict[str, Any]
    run: dict[str, Any]


@dataclass(slots=True)
class PreparedCB2:
    base: cb1.PreparedCB1
    profile: EmbeddingProfile
    publisher: ComponentDescriptor
    publications: tuple[dict[str, Any], ...]
    publication_results: tuple[dict[str, Any], ...]
    index_time_ms: tuple[float, ...]
    cache_hits: int
    cache_misses: int
    indexed_vectors: int
    skipped_vectors: int


_TASKS = {
    "exact_location": CodeTask.EXACT_LOCATION,
    "implementation": CodeTask.IMPLEMENTATION,
    "call_reference_path": CodeTask.CALL_PATH,
    "bug_localization": CodeTask.BUG_LOCALIZATION,
    "impact_analysis": CodeTask.IMPACT_ANALYSIS,
}


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
        raise CB2Error(f"unable to read artifact JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise CB2Error(f"artifact JSON must be an object: {path.name}")
    return value


def _relative_or_name(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _embedding_profile() -> EmbeddingProfile:
    profile = EmbeddingProfile(**PROFILE_SNAPSHOT)
    if profile.canonical_snapshot() != PROFILE_SNAPSHOT:
        raise CB2Error("C3-01 EmbeddingProfile changed the fixed C-B2 profile snapshot")
    return profile


def _load_cb1_audit_anchor(root: Path) -> AuditAnchor:
    directory = (root / "evals" / "code" / "runs" / CB1_AUDIT_ARTIFACT_DIRECTORY).resolve()
    if not directory.is_dir():
        raise CB2Error(f"C-B1 audit artifact is missing: {CB1_AUDIT_ARTIFACT_DIRECTORY}")
    try:
        verification = cb1.verify_artifact(directory, root=root)
    except Exception as exc:
        raise CB2Error(f"C-B1 audit verification failed: {exc}") from exc
    manifest = _read_json(directory / "manifest.json")
    package = validate_golden_package(root)
    if (
        verification.get("run_id") != CB1_AUDIT_RUN_ID
        or manifest.get("run_id") != CB1_AUDIT_RUN_ID
        or manifest.get("treatment_qualified") is not False
        or (manifest.get("acceptance") or {}).get("decision") != "not-qualified"
        or (manifest.get("dataset") or {}).get("package_hash") != PACKAGE_HASH
        or (manifest.get("dataset") or {}).get("case_membership_hash") != package.membership_hash
        or (manifest.get("dataset") or {}).get("eligible_cases") != EXPECTED_ELIGIBLE_COUNT
    ):
        raise CB2Error("C-B1 artifact does not match the fixed NOT QUALIFIED audit anchor")
    store = EvaluationStore(baseline._ReadOnlySQLiteStore(directory / "evaluation.sqlite3"))
    run = store.get_run(CB1_AUDIT_RUN_ID)
    if run is None:
        raise CB2Error("C-B1 audit SQLite does not contain the anchored Run")
    return AuditAnchor(
        directory=directory,
        manifest=manifest,
        verification=verification,
        run=run,
    )


def _public_class(name: str) -> tuple[type[Any] | None, str | None]:
    try:
        module = importlib.import_module(PUBLIC_CODE_MODULE)
        value = getattr(module, name)
    except (ImportError, AttributeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not inspect.isclass(value):
        return None, f"{PUBLIC_CODE_MODULE}.{name} is not a class"
    return value, None


def _component_contract(name: str) -> dict[str, Any]:
    component, error = _public_class(name)
    return {
        "module": PUBLIC_CODE_MODULE,
        "class": name,
        "status": "ready" if component is not None else "pending",
        "reason": error,
    }


def preparation_status(*, root: Path | None = None) -> dict[str, Any]:
    resolved_root = (root or repository_root()).resolve()
    package = validate_golden_package(resolved_root)
    cb0 = cb1._load_cb0_anchor(resolved_root)
    cb1_audit = _load_cb1_audit_anchor(resolved_root)
    publisher, publisher_error = _public_class(REAL_PUBLISHER_CLASS)
    retriever, retriever_error = _public_class(REAL_RETRIEVER_CLASS)
    return {
        "status": "PREPARED",
        "execution_status": "pending-c3-02-gate",
        "treatment_qualified": False,
        "artifact_created": False,
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
        "qualified_baseline": {
            "run_id": CB0_QUALIFIED_RUN_ID,
            "qualification_record_hash": cb0.verification["qualification_record_hash"],
            "manifest_canonical_hash": cb0.verification["manifest_canonical_hash"],
        },
        "audit_control": {
            "run_id": CB1_AUDIT_RUN_ID,
            "treatment_qualified": False,
            "manifest_canonical_hash": cb1_audit.verification["manifest_canonical_hash"],
            "qualification_use": "forbidden",
        },
        "config_contract": {
            "treatment": "C-B2 local dense + exact/sparse",
            "rag_code_unit_builder": "ast-v2",
            "publisher": _component_contract(REAL_PUBLISHER_CLASS),
            "retriever": _component_contract(REAL_RETRIEVER_CLASS),
            "profile": PROFILE_SNAPSHOT,
            "provider_provenance": PROVIDER_PROVENANCE,
            "top_k": TOP_K,
            "sparse_index_version": SPARSE_INDEX_VERSION,
            "channel_budget": CHANNEL_BUDGET,
            "graph_candidate": False,
            "capability_boundary": CAPABILITY_BOUNDARY,
            "optional_candidates": list(OPTIONAL_CANDIDATES),
        },
        "production_components": {
            "status": ("ready" if publisher is not None and retriever is not None else "pending"),
            "publisher_reason": publisher_error,
            "retriever_reason": retriever_error,
        },
        "qualification_policy": {
            "requires_c3_02_gate_approval": True,
            "requires_production_component_identity": True,
            "requires_complete_33_case_coverage": True,
            "requires_clean_artifact_security_scan": True,
            "requires_cb0_exact_identifier_locator_non_regression": True,
            "requires_strict_low_overlap_entity_recall_improvement_over_cb1_audit": True,
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
        root / "tests/test_code_cb2_run.py",
        root / "evals/code/code_golden_v2.py",
        root / "tests/fixtures/code_golden/v2/materializer.py",
    ]
    digest = hashlib.sha256()
    for path in sorted(set(paths), key=lambda item: item.as_posix()):
        if not path.is_file():
            raise CB2Error(f"required C-B2 implementation input is missing: {path}")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def workspace_input_fingerprint(root: Path, runs_dir: Path) -> dict[str, Any]:
    try:
        common = baseline.workspace_input_fingerprint(root, runs_dir)
    except Exception as exc:
        raise CB2Error(f"unable to fingerprint the C-B2 workspace: {exc}") from exc
    return {
        **common,
        "cb2_treatment_input_hash": _treatment_input_hash(root),
        "cb0_qualified_run_id": CB0_QUALIFIED_RUN_ID,
        "cb1_audit_run_id": CB1_AUDIT_RUN_ID,
        "cb1_audit_is_qualified_baseline": False,
        "profile_fingerprint": _fingerprint(PROFILE_SNAPSHOT),
    }


def _string_attribute(value: Any, names: Sequence[str], label: str) -> str:
    for name in names:
        observed = getattr(value, name, None)
        if isinstance(observed, str) and observed.strip():
            return observed.strip()
    raise CB2Error(f"component must declare a non-empty {label}")


def _publisher_descriptor(publisher: Any) -> ComponentDescriptor:
    module = importlib.import_module(type(publisher).__module__)
    real = type(publisher).__name__ == REAL_PUBLISHER_CLASS and (
        type(publisher).__module__ == PUBLIC_CODE_MODULE
        or type(publisher).__module__.startswith(f"{PUBLIC_CODE_MODULE}.")
    )
    version = _string_attribute(
        module if real else publisher,
        (
            "DENSE_PUBLISHER_VERSION",
            "DENSE_PUBLICATION_VERSION",
            "PUBLISHER_VERSION",
            "publisher_version",
            "version",
        ),
        "publisher version",
    )
    return ComponentDescriptor(
        module=type(publisher).__module__,
        class_name=type(publisher).__name__,
        version=version,
    )


def _retriever_descriptor(retriever: Any) -> RetrieverDescriptor:
    real = type(retriever).__name__ == REAL_RETRIEVER_CLASS and (
        type(retriever).__module__ == PUBLIC_CODE_MODULE
        or type(retriever).__module__.startswith(f"{PUBLIC_CODE_MODULE}.")
    )
    module = importlib.import_module(type(retriever).__module__) if real else retriever
    raw_channels = ("exact", "sparse", "dense") if real else getattr(retriever, "channels", None)
    if isinstance(raw_channels, (str, bytes)) or not isinstance(raw_channels, Sequence):
        raise CB2Error("retriever must declare exact/sparse/dense channels")
    channels = tuple(
        str(getattr(channel, "value", channel)).strip().casefold() for channel in raw_channels
    )
    if channels != ("exact", "sparse", "dense"):
        raise CB2Error("C-B2 channels must be exactly ('exact', 'sparse', 'dense')")
    descriptor = RetrieverDescriptor(
        module=type(retriever).__module__,
        class_name=type(retriever).__name__,
        version=_string_attribute(
            module,
            ("HYBRID_RETRIEVER_VERSION", "RETRIEVER_VERSION", "retriever_version", "version"),
            "retriever version",
        ),
        fusion=_string_attribute(
            module,
            (
                "HYBRID_FUSION_POLICY",
                "DENSE_FUSION_POLICY",
                "FUSION_POLICY",
                "fusion",
                "fusion_version",
            ),
            "fusion policy/version",
        ),
        channels=channels,
        index_family=(
            "ast-v2"
            if real
            else _string_attribute(
                retriever,
                ("index_family", "unit_builder", "INDEX_FAMILY"),
                "index family",
            )
        ),
    )
    if descriptor.index_family != "ast-v2":
        raise CB2Error("C-B2 retriever index_family must be ast-v2")
    return descriptor


def _construct_public_component(
    component: type[Any],
    *,
    runtime: Runtime,
    profile: EmbeddingProfile,
) -> Any:
    values = {
        "store": runtime.store,
        "runtime": runtime,
        "profile": profile,
        "embedding_profile": profile,
    }
    try:
        signature = inspect.signature(component)
    except (TypeError, ValueError) as exc:
        raise CB2Error(f"cannot inspect public component {component.__name__}") from exc
    kwargs: dict[str, Any] = {}
    for parameter in signature.parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if parameter.name in values:
            kwargs[parameter.name] = values[parameter.name]
        elif parameter.default is inspect.Parameter.empty:
            raise CB2Error(
                f"public {component.__name__} constructor has unsupported required "
                f"parameter: {parameter.name}"
            )
    try:
        return component(**kwargs)
    except Exception as exc:
        raise CB2Error(f"unable to construct public {component.__name__}") from exc


def _load_real_publisher(runtime: Runtime, profile: EmbeddingProfile) -> Any:
    component, error = _public_class(REAL_PUBLISHER_CLASS)
    if component is None:
        raise CB2Error(f"C3-02 is not ready: {error}")
    publisher = _construct_public_component(component, runtime=runtime, profile=profile)
    descriptor = _publisher_descriptor(publisher)
    if not _is_real_publisher(descriptor):
        raise CB2Error("qualified mode did not construct the public production publisher")
    return publisher


def _load_real_retriever(runtime: Runtime, profile: EmbeddingProfile) -> Any:
    component, error = _public_class(REAL_RETRIEVER_CLASS)
    if component is None:
        raise CB2Error(f"C3-02 is not ready: {error}")
    retriever = _construct_public_component(component, runtime=runtime, profile=profile)
    descriptor = _retriever_descriptor(retriever)
    if not _is_real_retriever(descriptor):
        raise CB2Error("qualified mode did not construct the public production retriever")
    return retriever


def _is_public_component(descriptor: ComponentDescriptor, class_name: str) -> bool:
    return descriptor.class_name == class_name and (
        descriptor.module == PUBLIC_CODE_MODULE
        or descriptor.module.startswith(f"{PUBLIC_CODE_MODULE}.")
    )


def _is_real_publisher(descriptor: ComponentDescriptor) -> bool:
    return _is_public_component(descriptor, REAL_PUBLISHER_CLASS)


def _is_real_retriever(descriptor: RetrieverDescriptor) -> bool:
    return descriptor.class_name == REAL_RETRIEVER_CLASS and (
        descriptor.module == PUBLIC_CODE_MODULE
        or descriptor.module.startswith(f"{PUBLIC_CODE_MODULE}.")
    )


def _publisher_method(publisher: Any) -> Callable[..., Any]:
    for name in ("publish", "publish_profile", "index_and_publish"):
        method = getattr(publisher, name, None)
        if callable(method):
            return method
    raise CB2Error("dense publisher exposes no supported publication method")


def _call_publisher(
    publisher: Any,
    profile: EmbeddingProfile,
    *,
    units: Sequence[Mapping[str, Any]],
    repository_ids: Sequence[str],
    generation_id: str | None,
    repository_id: str | None,
) -> Any:
    method = _publisher_method(publisher)
    signature = inspect.signature(method)
    values: dict[str, Any] = {
        "units": units,
        "profile": profile,
        "embedding_profile": profile,
        "project_id": PROJECT_ID,
        "repository_ids": list(repository_ids),
        "generation_id": generation_id,
        "repository_id": repository_id,
        "allowed_acl_refs": [ACL_REF],
        "acl_refs": [ACL_REF],
    }
    kwargs: dict[str, Any] = {}
    for parameter in signature.parameters.values():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if parameter.name in values and values[parameter.name] is not None:
            kwargs[parameter.name] = values[parameter.name]
        elif parameter.default is inspect.Parameter.empty:
            raise CB2Error(
                f"dense publisher method has unsupported required parameter: {parameter.name}"
            )
    try:
        return method(**kwargs)
    except Exception as exc:
        raise CB2Error("dense profile publication failed") from exc


def _publisher_result_summary(value: Any) -> dict[str, Any]:
    if hasattr(value, "canonical_snapshot"):
        value = value.canonical_snapshot()
    elif hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        raw = dict(value)
    elif value is None:
        raw = {}
    else:
        raw = {
            name: getattr(value, name)
            for name in (
                "cache_hits",
                "cache_misses",
                "indexed",
                "unit_count",
                "vector_count",
                "skipped",
                "fallbacks",
                "generation_id",
                "repository_id",
                "provider",
            )
            if hasattr(value, name)
        }
    summary: dict[str, Any] = {}
    for name in (
        "cache_hits",
        "cache_misses",
        "indexed",
        "unit_count",
        "vector_count",
        "skipped",
        "fallbacks",
        "generation_id",
        "repository_id",
        "publication_version",
        "status",
    ):
        observed = raw.get(name)
        if isinstance(observed, (str, int, float, bool)) or observed is None:
            summary[name] = observed
    provenance = raw.get("provenance") or raw.get("provider")
    if hasattr(provenance, "canonical_snapshot"):
        provenance = provenance.canonical_snapshot()
    if isinstance(provenance, Mapping):
        summary["provenance"] = {
            name: provenance.get(name)
            for name in ("provider", "model", "revision", "dimension", "locality")
        }
    return summary


def _result_counter(summary: Mapping[str, Any], name: str) -> int:
    value = summary.get(name)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CB2Error(f"dense publisher returned invalid {name}")
    return value


def _indexed_counter(summary: Mapping[str, Any]) -> int:
    name = "indexed" if summary.get("indexed") is not None else "vector_count"
    return _result_counter(summary, name)


def _ready_profile_state(
    publication: Mapping[str, Any],
    profile: EmbeddingProfile,
) -> Mapping[str, Any] | None:
    validation = publication.get("validation")
    profiles = validation.get("embedding_profiles") if isinstance(validation, Mapping) else None
    state = profiles.get(profile.id) if isinstance(profiles, Mapping) else None
    return state if isinstance(state, Mapping) else None


def _active_publications(runtime: Runtime) -> tuple[dict[str, Any], ...]:
    publications: list[dict[str, Any]] = []
    generations = runtime.store.active_code_generations(project_id=PROJECT_ID)
    if len(generations) != 3:
        raise CB2Error("isolated C-B2 database must expose three active Code generations")
    for repository_id, generation_id in sorted(generations.items()):
        publication = runtime.store.get_code_index_publication(
            generation_id,
            repository_id=repository_id,
            active_only=True,
        )
        if publication is None:
            raise CB2Error(f"missing active dense publication for {repository_id}")
        publications.append(publication)
    return tuple(publications)


def _publish_dense_profile(
    base: cb1.PreparedCB1,
    publisher: Any,
    profile: EmbeddingProfile,
) -> PreparedCB2:
    descriptor = _publisher_descriptor(publisher)
    repository_ids = sorted(source.repository_id for source in base.sources.values())
    method = _publisher_method(publisher)
    signature = inspect.signature(method)
    parameter_names = set(signature.parameters)
    per_generation = bool({"generation_id", "repository_id"} & parameter_names)
    summaries: list[dict[str, Any]] = []
    timings: list[float] = []
    if per_generation:
        for publication in base.publications:
            with base.runtime.store.connection() as database:
                units = [
                    dict(row)
                    for row in database.execute(
                        """SELECT * FROM code_retrieval_units
                           WHERE project_id=? AND repository_id=? AND generation_id=?
                           ORDER BY rowid""",
                        (
                            PROJECT_ID,
                            str(publication["repository_id"]),
                            str(publication["generation_id"]),
                        ),
                    ).fetchall()
                ]
            started = time.perf_counter()
            result = _call_publisher(
                publisher,
                profile,
                units=units,
                repository_ids=repository_ids,
                generation_id=str(publication["generation_id"]),
                repository_id=str(publication["repository_id"]),
            )
            timings.append(round((time.perf_counter() - started) * 1_000, 6))
            summary = _publisher_result_summary(result)
            summary.setdefault("generation_id", str(publication["generation_id"]))
            summary.setdefault("repository_id", str(publication["repository_id"]))
            summaries.append(summary)
    else:
        started = time.perf_counter()
        result = _call_publisher(
            publisher,
            profile,
            units=(),
            repository_ids=repository_ids,
            generation_id=None,
            repository_id=None,
        )
        timings.append(round((time.perf_counter() - started) * 1_000, 6))
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes, Mapping)):
            summaries.extend(_publisher_result_summary(item) for item in result)
        else:
            summaries.append(_publisher_result_summary(result))

    publications = _active_publications(base.runtime)
    observed = _dense_database_observation(base.database_path, profile)
    if (
        observed["unit_count"] <= 0
        or observed["vector_count"] != observed["unit_count"]
        or observed["vector_generation_count"] != len(publications)
        or observed["profile_count"] != 1
        or observed["model_count"] != 1
        or observed["dimension_count"] != 1
    ):
        raise CB2Error("dense publisher did not publish a complete single-profile vector set")
    for publication in publications:
        validation = publication.get("validation")
        capabilities = validation.get("capabilities") if isinstance(validation, Mapping) else {}
        state = _ready_profile_state(publication, profile)
        summary = next(
            (
                item
                for item in summaries
                if item.get("generation_id") == publication.get("generation_id")
            ),
            None,
        )
        expected_count = _indexed_counter(summary) if summary is not None else -1
        if (
            publication.get("status") != "published"
            or publication.get("embedding") in {None, "", "not-built"}
            or not isinstance(capabilities, Mapping)
            or capabilities.get("dense_retrieval") is not True
            or state is None
            or state.get("status") != "ready"
            or state.get("profile") != profile.canonical_snapshot()
            or state.get("provider") != PROVIDER_PROVENANCE
            or int(state.get("unit_count", -1)) != expected_count
            or int(state.get("vector_count", -1)) != expected_count
        ):
            raise CB2Error("dense publication gate is incomplete")

    return PreparedCB2(
        base=base,
        profile=profile,
        publisher=descriptor,
        publications=publications,
        publication_results=tuple(summaries),
        index_time_ms=tuple(timings),
        cache_hits=sum(_result_counter(item, "cache_hits") for item in summaries),
        cache_misses=sum(_result_counter(item, "cache_misses") for item in summaries),
        indexed_vectors=sum(_indexed_counter(item) for item in summaries),
        skipped_vectors=sum(_result_counter(item, "skipped") for item in summaries),
    )


def _query_profile(case: GoldenCase, request: Any) -> CodeQueryProfile:
    profile = case.request.code_profile
    if profile is None or profile.task not in _TASKS:
        raise CB2Error(f"unsupported Golden task for C-B2: {case.canonical_id}")
    return CodeQueryProfile(
        task=_TASKS[profile.task],
        target_ref=request.scope.commit or profile.expected_ref or "current",
        budget=CodeRetrievalBudget(**CHANNEL_BUDGET),
    )


class _CB2EvaluationRetriever(cb1._CB1EvaluationRetriever):
    def __init__(
        self,
        retriever: Any,
        runtime: Runtime,
        package: GoldenPackage,
        descriptor: RetrieverDescriptor,
        publisher: ComponentDescriptor,
        profile: EmbeddingProfile,
    ) -> None:
        super().__init__(retriever, runtime, package, descriptor)  # type: ignore[arg-type]
        self.publisher_descriptor = publisher
        self.embedding_profile = profile

    def search_evaluation(
        self,
        request: Any,
        *,
        graph_candidate_enabled: bool,
    ) -> dict[str, Any]:
        if graph_candidate_enabled:
            raise CB2Error("C-B2 is graph-candidate-off")
        case = self._case_by_question.get(str(request.query))
        if case is None:
            raise CB2Error("C-B2 retriever received a query outside released membership")
        search = getattr(self.retriever, "search", None)
        if not callable(search):
            raise CB2Error("C-B2 retriever has no callable search boundary")
        signature = inspect.signature(search)
        positional = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
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
            raise CB2Error("C-B2 search signature cannot accept an evaluation request")
        response = self._adapt_response(request, cb1._as_mapping(raw))
        trace = dict(response.get("trace") or {})
        if not isinstance(trace.get("dense_outcome"), str):
            raise CB2Error("retriever trace lacks dense_outcome execution evidence")
        trace.update(
            {
                "embedding_profile_id": self.embedding_profile.id,
                "embedding_profile_revision": self.embedding_profile.revision,
                "embedding_model": self.embedding_profile.model,
                "embedding_model_revision": LOCAL_HASH_MODEL_REVISION,
                "embedding_dimension": self.embedding_profile.dimension,
                "embedding_locality": self.embedding_profile.locality,
                "publisher_class": self.publisher_descriptor.class_name,
                "publisher_version": self.publisher_descriptor.version,
            }
        )
        return {**response, "trace": trace}

    def _adapt_code_source_result(
        self,
        request: Any,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        response, trace = super()._adapt_code_source_result(request, payload)
        dense = 0
        dense_only = 0
        for candidate in payload.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            channels = {
                cb1._channel_name(score.get("channel"))
                for score in candidate.get("raw_channel_scores") or []
                if isinstance(score, Mapping)
            }
            if "dense" in channels:
                dense += 1
                dense_only += int(channels == {"dense"})
        trace["dense_contribution_count"] = dense
        trace["dense_only_contribution_count"] = dense_only
        return response, trace


def _descriptor_config(
    retriever: RetrieverDescriptor,
    publisher: ComponentDescriptor,
) -> dict[str, Any]:
    return {
        "schema_version": "code-c-b2-config-snapshot-v1",
        "treatment": "C-B2 local dense + exact/sparse",
        "rag_code_unit_builder": "ast-v2",
        "publisher": publisher.to_dict(),
        "retriever": retriever.to_dict(),
        "profile": PROFILE_SNAPSHOT,
        "provider_provenance": PROVIDER_PROVENANCE,
        "top_k": TOP_K,
        "sparse_index_version": SPARSE_INDEX_VERSION,
        "channel_budget": CHANNEL_BUDGET,
        "graph_candidate": False,
        "semantic_resolver": "off",
        "reranker": "off",
        "context": "snippet-v1",
        "capability_boundary": CAPABILITY_BOUNDARY,
        "optional_candidates": list(OPTIONAL_CANDIDATES),
        "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "audit_control_run_id": CB1_AUDIT_RUN_ID,
        "audit_control_is_qualified_baseline": False,
    }


def _publication_view(publication: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: publication.get(name)
        for name in (
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


def _peak_rss_bytes() -> dict[str, Any]:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if not isinstance(value, (int, float)) or value < 0:
        return {"status": "unavailable", "value": None}
    multiplier = 1 if sys.platform == "darwin" else 1024
    return {
        "status": "available",
        "value": int(value * multiplier),
        "source": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
    }


def _physical_memory_bytes() -> dict[str, Any]:
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        return {"status": "unavailable", "value": None}
    if pages <= 0 or page_size <= 0:
        return {"status": "unavailable", "value": None}
    return {
        "status": "available",
        "value": pages * page_size,
        "source": "os.sysconf",
    }


def _compute_observation() -> dict[str, Any]:
    return {
        "cpu_logical_count": {
            "status": "available" if os.cpu_count() is not None else "unavailable",
            "value": os.cpu_count(),
        },
        "machine": platform.machine() or "unavailable",
        "system": platform.system() or "unavailable",
        "python_implementation": platform.python_implementation(),
        "physical_memory_bytes": _physical_memory_bytes(),
        "process_peak_rss_bytes": _peak_rss_bytes(),
        "gpu": {
            "status": "unavailable",
            "reason": "local-hash C-B2 does not use or probe a GPU",
        },
    }


def _percentile_values(values: Sequence[float], fraction: float) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "status": "unavailable",
            "value": None,
            "sample_count": 0,
            "reason": "no timing samples",
        }
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return {
        "status": "available",
        "value": ordered[lower] * (1.0 - weight) + ordered[upper] * weight,
        "sample_count": len(ordered),
    }


def _materialization_observation(prepared: PreparedCB2) -> dict[str, Any]:
    base = cb1._materialization_observation(prepared.base)
    database = _dense_database_observation(prepared.base.database_path, prepared.profile)
    observation = {
        **{name: value for name, value in base.items() if name != "observation_fingerprint"},
        "schema_version": "code-c-b2-materialization-v1",
        "treatment": "ast-v2 ingestion -> C2 publication -> embedding cache/vector "
        "publication -> hybrid retrieval",
        "profile": PROFILE_SNAPSHOT,
        "provider_provenance": PROVIDER_PROVENANCE,
        "publisher": prepared.publisher.to_dict(),
        "publications": [_publication_view(item) for item in prepared.publications],
        "dense_publication_results": list(prepared.publication_results),
        "embedding_cache": {
            "hits": prepared.cache_hits,
            "misses": prepared.cache_misses,
            "rows": database["cache_count"],
        },
        "vectors": {
            "indexed_reported": prepared.indexed_vectors,
            "skipped_reported": prepared.skipped_vectors,
            "rows": database["vector_count"],
            "complete_against_ast_units": (database["vector_count"] == database["unit_count"]),
        },
        "dense_index_time_ms": {
            "status": "available",
            "total": sum(prepared.index_time_ms),
            "samples": list(prepared.index_time_ms),
            "p95": _percentile_values(prepared.index_time_ms, 0.95),
            "clock": "time.perf_counter",
        },
        "optional_candidates": list(OPTIONAL_CANDIDATES),
        "network_used": False,
        "downloads_performed": False,
        "compute": _compute_observation(),
    }
    return {**observation, "observation_fingerprint": _fingerprint(observation)}


def _selector_members(package: GoldenPackage) -> dict[str, tuple[str, ...]]:
    return cb1._selector_members(package)


_QUALITY_METRICS = (
    ("entity_recall@10", "higher"),
    ("expected_locator_recall@10", "higher"),
    ("mrr@10", "higher"),
    ("ndcg@10", "higher"),
)
_DIAGNOSTIC_METRICS = (
    ("overall", "hard_negative_error@10", "lower"),
    ("overall", "harmful_candidate_rate@10", "lower"),
    ("overall", "entity_duplicate_rate@10", "lower"),
    ("overall", "unit_duplicate_rate@10", "lower"),
    ("same_name_hard_negative", "hard_negative_error@10", "lower"),
    ("same_name_hard_negative", "harmful_candidate_rate@10", "lower"),
)


def _comparison_report(
    treatment: dict[str, Any],
    control: dict[str, Any],
    package: GoldenPackage,
    *,
    role: str,
    control_run_id: str,
) -> dict[str, Any]:
    selectors = _selector_members(package)
    memberships = {
        name: cb1._membership_record(package, members) for name, members in selectors.items()
    }
    specifications = [
        (scope, metric_name, direction)
        for scope in ("overall", "exact", "identifier", "low_overlap")
        for metric_name, direction in _QUALITY_METRICS
    ]
    specifications.extend(_DIAGNOSTIC_METRICS)
    rows = []
    for scope, metric_name, direction in specifications:
        members = selectors[scope]
        rows.append(
            cb1._comparison_row(
                scope,
                metric_name,
                direction,
                cb1._aggregate_selected(control, members, metric_name),
                cb1._aggregate_selected(treatment, members, metric_name),
                memberships[scope],
            )
        )
    members = selectors["overall"]
    rows.append(
        cb1._comparison_row(
            "overall",
            "latency_p95_ms",
            "lower",
            cb1._percentile(control, members, 0.95),
            cb1._percentile(treatment, members, 0.95),
            memberships["overall"],
        )
    )
    return {
        "schema_version": "code-c-b2-comparison-v1",
        "control_role": role,
        "control_run_id": control_run_id,
        "control_is_qualified_baseline": role == "qualified-baseline",
        "treatment_run_id": treatment["id"],
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
        raise CB2Error(f"required comparison is missing: {scope}/{metric_name}") from exc


def _gate(
    name: str,
    *,
    passed: bool,
    reason: str | None,
    evidence: Any,
) -> dict[str, Any]:
    return {
        "gate": name,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "reason": None if passed else reason,
        "evidence": evidence,
    }


def _acceptance(
    run: dict[str, Any],
    cb0_comparison: dict[str, Any],
    cb1_comparison: dict[str, Any],
    retriever: RetrieverDescriptor,
    publisher: ComponentDescriptor,
    execution_mode: ExecutionMode,
    *,
    security_clean: bool,
) -> dict[str, Any]:
    gates = [
        _gate(
            "production_component_identity",
            passed=(
                execution_mode == "qualified"
                and _is_real_publisher(publisher)
                and _is_real_retriever(retriever)
            ),
            reason="qualification requires public production publisher and retriever identities",
            evidence={
                "execution_mode": execution_mode,
                "publisher": publisher.to_dict(),
                "retriever": retriever.to_dict(),
            },
        ),
        _gate(
            "complete_33_case_coverage",
            passed=(
                int(run.get("case_count") or 0) == EXPECTED_ELIGIBLE_COUNT
                and len(run.get("results") or []) == EXPECTED_ELIGIBLE_COUNT
            ),
            reason="qualification requires 33/33 released eligible results",
            evidence={
                "case_count": run.get("case_count"),
                "result_count": len(run.get("results") or []),
            },
        ),
        _gate(
            "artifact_security",
            passed=security_clean,
            reason="qualification requires a clean canonical artifact security scan",
            evidence={"clean": security_clean},
        ),
    ]
    for name, scope, metric_name in (
        ("cb0_exact_non_regression", "exact", "entity_recall@10"),
        ("cb0_identifier_non_regression", "identifier", "entity_recall@10"),
        ("cb0_locator_non_regression", "overall", "expected_locator_recall@10"),
    ):
        row = _comparison_lookup(cb0_comparison, scope, metric_name)
        delta = row["delta"]
        passed = delta.get("status") == "available" and float(delta["directional_delta"]) >= 0.0
        gates.append(
            _gate(
                name,
                passed=passed,
                reason="treatment regressed or comparison is unavailable against qualified C-B0",
                evidence=row,
            )
        )
    low_overlap = _comparison_lookup(cb1_comparison, "low_overlap", "entity_recall@10")
    low_delta = low_overlap["delta"]
    low_passed = (
        low_delta.get("status") == "available" and float(low_delta["directional_delta"]) > 0.0
    )
    gates.append(
        _gate(
            "cb1_audit_low_overlap_strict_improvement",
            passed=low_passed,
            reason=(
                "local-hash did not strictly improve low-overlap entity recall over "
                "the fixed NOT QUALIFIED C-B1 audit"
            ),
            evidence=low_overlap,
        )
    )
    return {
        "schema_version": "code-c-b2-acceptance-v1",
        "decision": "qualified" if all(item["passed"] for item in gates) else "not-qualified",
        "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "audit_control_run_id": CB1_AUDIT_RUN_ID,
        "audit_control_is_qualified_baseline": False,
        "gates": gates,
    }


def _coverage_report(run: dict[str, Any], package: GoldenPackage) -> dict[str, Any]:
    report = cb1._coverage_report(run, package)
    return {
        **report,
        "schema_version": "code-c-b2-coverage-v1",
        "capability_boundary": CAPABILITY_BOUNDARY,
    }


def _metrics_report(
    run: dict[str, Any],
    cb0: cb1.CB0Anchor,
    cb1_audit: AuditAnchor,
    package: GoldenPackage,
    retriever: RetrieverDescriptor,
    publisher: ComponentDescriptor,
    execution_mode: ExecutionMode,
    *,
    security_clean: bool,
) -> dict[str, Any]:
    cb0_comparison = _comparison_report(
        run,
        cb0.run,
        package,
        role="qualified-baseline",
        control_run_id=CB0_QUALIFIED_RUN_ID,
    )
    cb1_comparison = _comparison_report(
        run,
        cb1_audit.run,
        package,
        role="not-qualified-audit-control",
        control_run_id=CB1_AUDIT_RUN_ID,
    )
    return {
        "schema_version": "code-c-b2-metrics-v1",
        "run_id": run["id"],
        "authoritative_contract": "evaluation_metric_values",
        "summary": run["summary"],
        "metric_values": run["metric_values"],
        "cb0_qualified_baseline_comparison": cb0_comparison,
        "cb1_not_qualified_audit_comparison": cb1_comparison,
        "acceptance": _acceptance(
            run,
            cb0_comparison,
            cb1_comparison,
            retriever,
            publisher,
            execution_mode,
            security_clean=security_clean,
        ),
    }


def _slices_report(
    run: dict[str, Any],
    cb0: cb1.CB0Anchor,
    cb1_audit: AuditAnchor,
    package: GoldenPackage,
) -> dict[str, Any]:
    try:
        full = baseline._slices_report(run, package)
    except Exception as exc:
        raise CB2Error(f"unable to derive C-B2 slices: {exc}") from exc
    comparisons = {
        "cb0_qualified_baseline": _comparison_report(
            run,
            cb0.run,
            package,
            role="qualified-baseline",
            control_run_id=CB0_QUALIFIED_RUN_ID,
        ),
        "cb1_not_qualified_audit": _comparison_report(
            run,
            cb1_audit.run,
            package,
            role="not-qualified-audit-control",
            control_run_id=CB1_AUDIT_RUN_ID,
        ),
    }
    return {
        **full,
        "schema_version": "code-c-b2-slices-v1",
        "required_treatment_slices": {
            role: [
                row
                for row in comparison["comparisons"]
                if row["scope"]
                in {"overall", "exact", "identifier", "low_overlap", "same_name_hard_negative"}
            ]
            for role, comparison in comparisons.items()
        },
        "selector_contract": {
            "exact": "code_profile.task == exact_location",
            "identifier": "query_style == identifier_heavy",
            "low_overlap": "Golden slice contains low_lexical_overlap",
            "same_name_hard_negative": "Golden slice contains same_name_hard_negative",
        },
    }


def _case_candidate_count(run: dict[str, Any]) -> dict[str, int]:
    return {
        str(item["case_id"]): int((item.get("detail") or {}).get("candidate_count") or 0)
        for item in run.get("results") or []
    }


def _case_metric_values(run: dict[str, Any], metric_name: str) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for item in run.get("metric_values") or []:
        if (
            item.get("case_id") is not None
            and item.get("metric_name") == metric_name
            and not item.get("slice")
        ):
            values[str(item["case_id"])] = (
                float(item["value"]) if item.get("status") == "available" else None
            )
    return values


def _dense_effects(
    run: dict[str, Any],
    cb1_audit: AuditAnchor,
    package: GoldenPackage,
) -> dict[str, Any]:
    audit_counts = _case_candidate_count(cb1_audit.run)
    treatment_counts = _case_candidate_count(run)
    case_names = {
        f"evaluation-case://{PROJECT_ID}/{case.canonical_id}": case.canonical_id
        for case in package.eligible_cases
    }
    recoveries = [
        {
            "case_id": case_names[case_id],
            "cb1_candidate_count": audit_counts.get(case_id, 0),
            "cb2_candidate_count": treatment_counts.get(case_id, 0),
        }
        for case_id in sorted(case_names)
        if audit_counts.get(case_id, 0) == 0 and treatment_counts.get(case_id, 0) > 0
    ]
    contributions = []
    for result in run.get("results") or []:
        trace = (result.get("detail") or {}).get("trace") or {}
        count = int(trace.get("dense_contribution_count") or 0)
        dense_only = int(trace.get("dense_only_contribution_count") or 0)
        if count > 0 or dense_only > 0:
            contributions.append(
                {
                    "case_id": case_names[str(result["case_id"])],
                    "dense_contribution_count": count,
                    "dense_only_contribution_count": dense_only,
                    "dense_candidates": int(trace.get("dense_candidates") or 0),
                }
            )
    low_members = set(_selector_members(package)["low_overlap"])
    treatment_recall = _case_metric_values(run, "entity_recall@10")
    audit_recall = _case_metric_values(cb1_audit.run, "entity_recall@10")
    failed_low_overlap = [
        {
            "case_id": case_names[case_id],
            "cb1_entity_recall@10": audit_recall.get(case_id),
            "cb2_entity_recall@10": treatment_recall.get(case_id),
            "delta": (
                treatment_recall[case_id] - audit_recall[case_id]
                if treatment_recall.get(case_id) is not None
                and audit_recall.get(case_id) is not None
                else None
            ),
        }
        for case_id in sorted(low_members)
        if treatment_recall.get(case_id) is None
        or audit_recall.get(case_id) is None
        or treatment_recall[case_id] <= audit_recall[case_id]
    ]
    return {
        "dense_contribution": {
            "case_count": len(contributions),
            "cases": contributions,
        },
        "cb1_zero_result_recovery": {
            "audit_zero_result_count": sum(value == 0 for value in audit_counts.values()),
            "recovered_count": len(recoveries),
            "cases": recoveries,
        },
        "low_overlap_non_improvements": failed_low_overlap,
    }


def _error_analysis_report(
    run: dict[str, Any],
    cb0: cb1.CB0Anchor,
    cb1_audit: AuditAnchor,
    package: GoldenPackage,
) -> dict[str, Any]:
    try:
        full = baseline._error_analysis_report(run, package)
    except Exception as exc:
        raise CB2Error(f"unable to derive C-B2 error analysis: {exc}") from exc
    cb0_comparison = _comparison_report(
        run,
        cb0.run,
        package,
        role="qualified-baseline",
        control_run_id=CB0_QUALIFIED_RUN_ID,
    )
    cb1_comparison = _comparison_report(
        run,
        cb1_audit.run,
        package,
        role="not-qualified-audit-control",
        control_run_id=CB1_AUDIT_RUN_ID,
    )
    return {
        **full,
        "schema_version": "code-c-b2-error-analysis-v1",
        "same_name_comparison": {
            "cb0": [
                row
                for row in cb0_comparison["comparisons"]
                if row["scope"] == "same_name_hard_negative"
            ],
            "cb1_audit": [
                row
                for row in cb1_comparison["comparisons"]
                if row["scope"] == "same_name_hard_negative"
            ],
        },
        "duplicate_comparison": {
            "cb0": [
                row
                for row in cb0_comparison["comparisons"]
                if "duplicate_rate" in row["metric_name"]
            ],
            "cb1_audit": [
                row
                for row in cb1_comparison["comparisons"]
                if "duplicate_rate" in row["metric_name"]
            ],
        },
        **_dense_effects(run, cb1_audit, package),
        "unavailable_cases": [
            {"case_id": case.canonical_id, "reason": case.unavailable_reason}
            for case in package.ineligible_cases
        ],
    }


def _latency_report(
    run: dict[str, Any],
    cb0: cb1.CB0Anchor,
    cb1_audit: AuditAnchor,
    package: GoldenPackage,
    materialization: dict[str, Any],
) -> dict[str, Any]:
    cb0_comparison = _comparison_report(
        run,
        cb0.run,
        package,
        role="qualified-baseline",
        control_run_id=CB0_QUALIFIED_RUN_ID,
    )
    cb1_comparison = _comparison_report(
        run,
        cb1_audit.run,
        package,
        role="not-qualified-audit-control",
        control_run_id=CB1_AUDIT_RUN_ID,
    )
    source_times = list(
        ((materialization.get("ingest_time_ms") or {}).get("per_source") or {}).values()
    )
    index_times = list((materialization.get("dense_index_time_ms") or {}).get("samples") or [])
    return {
        "schema_version": "code-c-b2-latency-v1",
        "run_id": run["id"],
        "ingest": {
            "total_ms": (materialization.get("ingest_time_ms") or {}).get("value"),
            "p95_ms": _percentile_values(source_times, 0.95),
        },
        "dense_index": {
            "total_ms": (materialization.get("dense_index_time_ms") or {}).get("total"),
            "p95_ms": _percentile_values(index_times, 0.95),
        },
        "query_p95": {
            "cb0_qualified_baseline": _comparison_lookup(
                cb0_comparison, "overall", "latency_p95_ms"
            ),
            "cb1_not_qualified_audit": _comparison_lookup(
                cb1_comparison, "overall", "latency_p95_ms"
            ),
        },
        "cases": [
            {
                "case_id": result["name"],
                "latency_ms": result["latency_ms"],
                "candidate_count": (result.get("detail") or {}).get("candidate_count"),
                "exact_candidates": ((result.get("detail") or {}).get("trace") or {}).get(
                    "exact_candidates"
                ),
                "sparse_candidates": ((result.get("detail") or {}).get("trace") or {}).get(
                    "sparse_candidates"
                ),
                "dense_candidates": ((result.get("detail") or {}).get("trace") or {}).get(
                    "dense_candidates"
                ),
            }
            for result in run.get("results") or []
        ],
        "compute": materialization.get("compute"),
    }


def _dbstat_bytes(database: sqlite3.Connection, names: Sequence[str]) -> dict[str, Any]:
    return cb1._dbstat_bytes(database, names)


def _dense_database_observation(
    database_path: Path,
    profile: EmbeddingProfile,
) -> dict[str, int]:
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        row = database.execute(
            """SELECT
                   (SELECT count(*) FROM code_retrieval_units),
                   (SELECT count(*) FROM code_unit_vectors
                    WHERE profile=? AND model=? AND dimension=?),
                   (SELECT count(DISTINCT generation_id) FROM code_unit_vectors
                    WHERE profile=? AND model=? AND dimension=?),
                   (SELECT count(DISTINCT profile) FROM code_unit_vectors),
                   (SELECT count(DISTINCT model) FROM code_unit_vectors),
                   (SELECT count(DISTINCT dimension) FROM code_unit_vectors),
                   (SELECT count(*) FROM code_unit_embedding_cache
                    WHERE profile=? AND model=? AND dimension=?)""",
            (
                profile.id,
                profile.model,
                profile.dimension,
                profile.id,
                profile.model,
                profile.dimension,
                profile.id,
                profile.model,
                profile.dimension,
            ),
        ).fetchone()
    return {
        "unit_count": int(row[0]),
        "vector_count": int(row[1]),
        "vector_generation_count": int(row[2]),
        "profile_count": int(row[3]),
        "model_count": int(row[4]),
        "dimension_count": int(row[5]),
        "cache_count": int(row[6]),
    }


def _available_delta(treatment: int, control: Any, label: str) -> dict[str, Any]:
    if isinstance(control, bool) or not isinstance(control, int):
        return {
            "status": "unavailable",
            "treatment_minus_control": None,
            "reason": f"control does not expose comparable {label}",
        }
    return {
        "status": "available",
        "treatment_minus_control": treatment - control,
        "measurement": label,
    }


def _storage_report(
    database_path: Path,
    run_id: str,
    cb0: cb1.CB0Anchor,
    cb1_audit: AuditAnchor,
) -> dict[str, Any]:
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        page_size = int(database.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(database.execute("PRAGMA page_count").fetchone()[0])
        free_pages = int(database.execute("PRAGMA freelist_count").fetchone()[0])
        unit_count = int(
            database.execute("SELECT count(*) FROM code_retrieval_units").fetchone()[0]
        )
        fts_count = int(
            database.execute("SELECT count(*) FROM code_retrieval_units_fts").fetchone()[0]
        )
        vector_count = int(database.execute("SELECT count(*) FROM code_unit_vectors").fetchone()[0])
        cache_count = int(
            database.execute("SELECT count(*) FROM code_unit_embedding_cache").fetchone()[0]
        )
        sparse_logical = int(
            database.execute(
                """SELECT coalesce(sum(
                       length(content) + length(qualified_name) + length(signature)
                       + length(path) + length(identifiers) + length(doc) + length(body)
                   ), 0)
                   FROM code_retrieval_units"""
            ).fetchone()[0]
        )
        vector_logical = int(
            database.execute(
                "SELECT coalesce(sum(length(vector)), 0) FROM code_unit_vectors"
            ).fetchone()[0]
        )
        cache_logical = int(
            database.execute(
                "SELECT coalesce(sum(length(vector)), 0) FROM code_unit_embedding_cache"
            ).fetchone()[0]
        )
        sparse_physical = _dbstat_bytes(
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
        vector_physical = _dbstat_bytes(
            database,
            ("code_unit_vectors", "idx_code_unit_vectors_pool"),
        )
        cache_physical = _dbstat_bytes(
            database,
            ("code_unit_embedding_cache", "idx_code_embedding_cache_usage"),
        )
    cb0_storage = _read_json(cb0.directory / "storage.json")
    cb1_storage = _read_json(cb1_audit.directory / "storage.json")
    total_index_logical = sparse_logical + vector_logical
    return {
        "schema_version": "code-c-b2-storage-v1",
        "run_id": run_id,
        "database_bytes": database_path.stat().st_size,
        "sqlite_page_size": page_size,
        "sqlite_page_count": page_count,
        "sqlite_free_pages": free_pages,
        "sqlite_free_bytes": page_size * free_pages,
        "ast_unit_count": unit_count,
        "fts_row_count": fts_count,
        "vector_count": vector_count,
        "embedding_cache_count": cache_count,
        "exact_sparse_index_logical_bytes": sparse_logical,
        "exact_sparse_index_physical_bytes": sparse_physical,
        "vector_index_logical_bytes": vector_logical,
        "vector_index_physical_bytes": vector_physical,
        "embedding_cache_logical_bytes": cache_logical,
        "embedding_cache_physical_bytes": cache_physical,
        "total_retrieval_index_logical_bytes": total_index_logical,
        "cb0_qualified_baseline_comparison": {
            "run_id": CB0_QUALIFIED_RUN_ID,
            "logical_index_bytes_delta": _available_delta(
                total_index_logical,
                cb0_storage.get("active_code_index_logical_bytes"),
                "logical retrieval index bytes",
            ),
        },
        "cb1_not_qualified_audit_comparison": {
            "run_id": CB1_AUDIT_RUN_ID,
            "is_qualified_baseline": False,
            "logical_index_bytes_delta": _available_delta(
                total_index_logical,
                cb1_storage.get("exact_sparse_index_logical_bytes"),
                "logical retrieval index bytes",
            ),
            "vector_index_bytes_delta": _available_delta(
                vector_logical,
                cb1_storage.get("vector_index_logical_bytes"),
                "logical vector index bytes",
            ),
        },
    }


def _report_payloads(
    run: dict[str, Any],
    package: GoldenPackage,
    database_path: Path,
    cb0: cb1.CB0Anchor,
    cb1_audit: AuditAnchor,
    retriever: RetrieverDescriptor,
    publisher: ComponentDescriptor,
    execution_mode: ExecutionMode,
    *,
    security_clean: bool,
) -> dict[str, dict[str, Any]]:
    declared = (run.get("config") or {}).get("declared") or {}
    materialization = declared.get("materialization_observation")
    if not isinstance(materialization, dict):
        raise CB2Error("Run does not retain the materialization observation")
    return {
        "materialization.json": materialization,
        "coverage.json": _coverage_report(run, package),
        "metrics.json": _metrics_report(
            run,
            cb0,
            cb1_audit,
            package,
            retriever,
            publisher,
            execution_mode,
            security_clean=security_clean,
        ),
        "slices.json": _slices_report(run, cb0, cb1_audit, package),
        "error-analysis.json": _error_analysis_report(run, cb0, cb1_audit, package),
        "latency.json": _latency_report(run, cb0, cb1_audit, package, materialization),
        "storage.json": _storage_report(database_path, run["id"], cb0, cb1_audit),
    }


def _validate_run(
    run: dict[str, Any],
    loaded: LoadedGolden,
    retriever: RetrieverDescriptor,
    publisher: ComponentDescriptor,
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
        raise CB2Error("C-B2 did not produce the required completed 33-case graph-off Run")
    result_case_ids = [str(result["case_id"]) for result in run["results"]]
    if len(set(result_case_ids)) != EXPECTED_ELIGIBLE_COUNT or sorted(result_case_ids) != sorted(
        loaded.case_ids
    ):
        raise CB2Error("C-B2 result membership differs from the released denominator")
    declared = (run.get("config") or {}).get("declared") or {}
    config = declared.get("cb2_config_snapshot")
    if config != _descriptor_config(retriever, publisher):
        raise CB2Error("embedded C-B2 configuration differs from component observations")
    request = (run.get("config") or {}).get("request") or {}
    if (
        request.get("case_ids") != list(loaded.case_ids)
        or request.get("graph_candidate_enabled") is not False
        or int(request.get("limit_per_query") or 0) != TOP_K
    ):
        raise CB2Error("embedded C-B2 request differs from the fixed runner contract")

    profile = _embedding_profile()
    dense = _dense_database_observation(database_path, profile)
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        active = [
            str(row[0])
            for row in database.execute(
                """SELECT active_generation_id FROM repositories
                   WHERE project_id=? ORDER BY id""",
                (PROJECT_ID,),
            ).fetchall()
        ]
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
    if (
        len(active) != 3
        or dense["unit_count"] <= 0
        or fts != dense["unit_count"]
        or dense["vector_count"] != dense["unit_count"]
        or dense["vector_generation_count"] != 3
        or dense["profile_count"] != 1
        or dense["model_count"] != 1
        or dense["dimension_count"] != 1
    ):
        raise CB2Error("embedded C-B2 AST/FTS/vector index is incomplete")
    if len(publications) != 3:
        raise CB2Error("embedded C-B2 publication set is incomplete")
    for publication in publications:
        validation = publication["validation"]
        capabilities = validation.get("capabilities") if isinstance(validation, Mapping) else {}
        state = _ready_profile_state(publication, profile)
        if (
            publication["builder"] != cb1.AST_BUILDER_VERSION
            or publication["sparse"] != SPARSE_INDEX_VERSION
            or publication["embedding"] in {"", "not-built"}
            or publication["graph"] != "not-built"
            or publication["status"] != "published"
            or not isinstance(capabilities, Mapping)
            or capabilities.get("sparse_retrieval") is not True
            or capabilities.get("dense_retrieval") is not True
            or state is None
            or state.get("status") != "ready"
            or state.get("profile") != profile.canonical_snapshot()
            or state.get("provider") != PROVIDER_PROVENANCE
            or int(state.get("unit_count", -1)) != int(state.get("vector_count", -2))
        ):
            raise CB2Error("embedded C-B2 dense publication gate is incomplete")
    if sorted(run["snapshot"].get("observed_index_generations") or []) != sorted(active):
        raise CB2Error("Run snapshot generation set differs from embedded publications")
    expected_trace = {
        "retriever_version": retriever.version,
        "retriever_class": retriever.class_name,
        "fusion": retriever.fusion,
        "index_family": "ast-v2",
        "unit_builder_version": "ast-v2",
        "retrieval_unit_capable": True,
        "graph_candidate_enabled": False,
        "embedding_profile_id": profile.id,
        "embedding_profile_revision": profile.revision,
        "embedding_model": profile.model,
        "embedding_model_revision": LOCAL_HASH_MODEL_REVISION,
        "embedding_dimension": profile.dimension,
        "embedding_locality": profile.locality,
        "publisher_class": publisher.class_name,
        "publisher_version": publisher.version,
    }
    for result in run["results"]:
        trace = (result.get("detail") or {}).get("trace") or {}
        if any(trace.get(name) != value for name, value in expected_trace.items()):
            raise CB2Error("case result lacks the observed C-B2 component/profile contract")
        if any(
            not isinstance(trace.get(f"{channel}_outcome"), str)
            for channel in ("exact", "sparse", "dense")
        ):
            raise CB2Error("case result lacks exact/sparse/dense execution evidence")


def _artifact_file_records(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "sha256": _sha256_bytes((directory / name).read_bytes()),
            "bytes": (directory / name).stat().st_size,
        }
        for name in sorted(ARTIFACT_FILES)
    }


def _anchor_manifest(
    cb0: cb1.CB0Anchor,
    cb1_audit: AuditAnchor,
) -> dict[str, Any]:
    return {
        "qualified_baseline": {
            "run_id": CB0_QUALIFIED_RUN_ID,
            "artifact_directory": CB0_ARTIFACT_DIRECTORY,
            "qualification_record_hash": cb0.verification["qualification_record_hash"],
            "manifest_canonical_hash": cb0.verification["manifest_canonical_hash"],
            "artifact_set_hash": cb0.manifest["artifact_set_hash"],
        },
        "audit_control": {
            "run_id": CB1_AUDIT_RUN_ID,
            "artifact_directory": CB1_AUDIT_ARTIFACT_DIRECTORY,
            "treatment_qualified": False,
            "qualification_use": "forbidden",
            "manifest_canonical_hash": cb1_audit.verification["manifest_canonical_hash"],
            "artifact_set_hash": cb1_audit.manifest["artifact_set_hash"],
        },
    }


def _manifest(
    run: dict[str, Any],
    package: GoldenPackage,
    retriever: RetrieverDescriptor,
    publisher: ComponentDescriptor,
    cb0: cb1.CB0Anchor,
    cb1_audit: AuditAnchor,
    artifact_files: dict[str, dict[str, Any]],
    input_fingerprint: dict[str, Any],
    execution_mode: ExecutionMode,
    acceptance: dict[str, Any],
    *,
    c3_02_gate_approved: bool,
) -> dict[str, Any]:
    qualified = (
        execution_mode == "qualified"
        and c3_02_gate_approved
        and acceptance["decision"] == "qualified"
    )
    snapshot = run["snapshot"]
    config = _descriptor_config(retriever, publisher)
    return {
        "schema_version": CB2_SCHEMA_VERSION,
        "status": "completed",
        "execution_mode": execution_mode,
        "c3_02_gate_approved": c3_02_gate_approved,
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
        "controls": _anchor_manifest(cb0, cb1_audit),
        "config": config,
        "config_fingerprint": _fingerprint(config),
        "snapshot": {
            "snapshot_id": snapshot.get("snapshot_id"),
            "comparison_fingerprint": snapshot.get("comparison_fingerprint"),
            "case_membership_hash": snapshot.get("case_membership_hash"),
            "schema_version": snapshot.get("schema_version"),
            "observed_index_generations": snapshot.get("observed_index_generations"),
            "observed_retriever": snapshot.get("observed_retriever"),
        },
        "implementation": {
            "runner": CB2_RUNNER_VERSION,
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
            "network_used": False,
        },
    }


def _descriptors_from_manifest(
    manifest: dict[str, Any],
) -> tuple[RetrieverDescriptor, ComponentDescriptor]:
    config = manifest.get("config") or {}
    retriever = config.get("retriever") or {}
    publisher = config.get("publisher") or {}
    channels = retriever.get("channels")
    if not isinstance(channels, list):
        raise CB2Error("manifest retriever channels are invalid")
    return (
        RetrieverDescriptor(
            module=str(retriever.get("module") or ""),
            class_name=str(retriever.get("class") or ""),
            version=str(retriever.get("version") or ""),
            fusion=str(retriever.get("fusion") or ""),
            channels=tuple(str(value) for value in channels),
            index_family=str(retriever.get("index_family") or ""),
        ),
        ComponentDescriptor(
            module=str(publisher.get("module") or ""),
            class_name=str(publisher.get("class") or ""),
            version=str(publisher.get("version") or ""),
        ),
    )


def _require_exact_file_set(directory: Path) -> None:
    entries = {path.name for path in directory.iterdir()}
    if entries != EXPECTED_ARTIFACT_ENTRIES:
        raise CB2Error("artifact directory contains an unexpected or missing entry")
    if any(path.is_symlink() or not path.is_file() for path in directory.iterdir()):
        raise CB2Error("artifact entries must be regular files, not symlinks")


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
        manifest.get("schema_version") != CB2_SCHEMA_VERSION
        or manifest.get("status") != "completed"
    ):
        raise CB2Error("artifact is not a completed C-B2 treatment")
    execution_mode = manifest.get("execution_mode")
    if execution_mode not in {"qualified", "test"}:
        raise CB2Error("artifact execution_mode is invalid")
    if execution_mode == "test" and not allow_test_artifact:
        raise CB2Error("test-only C-B2 artifacts require allow_test_artifact=True")

    package = validate_golden_package(resolved_root)
    cb0 = cb1._load_cb0_anchor(resolved_root)
    cb1_audit = _load_cb1_audit_anchor(resolved_root)
    if manifest.get("controls") != _anchor_manifest(cb0, cb1_audit):
        raise CB2Error("manifest control anchors are invalid")
    expected_files = _artifact_file_records(directory)
    if manifest.get("artifacts") != expected_files or manifest.get(
        "artifact_set_hash"
    ) != _fingerprint(expected_files):
        raise CB2Error("artifact file-set fingerprint mismatch")

    database_path = directory / "evaluation.sqlite3"
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as database:
        quick_check = str(database.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_failures = database.execute("PRAGMA foreign_key_check").fetchall()
        run_ids = [
            str(row[0])
            for row in database.execute("SELECT id FROM evaluation_runs ORDER BY id").fetchall()
        ]
    if quick_check != "ok" or foreign_key_failures or len(run_ids) != 1:
        raise CB2Error("artifact SQLite integrity or single-Run contract failed")
    if manifest.get("run_id") != run_ids[0]:
        raise CB2Error("manifest Run identity differs from embedded SQLite")

    loaded = baseline._verify_embedded_golden(database_path, package)
    store = EvaluationStore(baseline._ReadOnlySQLiteStore(database_path))
    run = store.get_run(run_ids[0])
    if run is None:
        raise CB2Error("artifact database does not contain its Run")
    retriever, publisher = _descriptors_from_manifest(manifest)
    _validate_run(run, loaded, retriever, publisher, database_path)
    baseline.assert_terminal_immutability(database_path, run["id"])
    try:
        security = baseline._require_clean_security_scan(directory)
    except Exception as exc:
        raise CB2Error(f"artifact security scan failed: {exc}") from exc

    reports = _report_payloads(
        run,
        package,
        database_path,
        cb0,
        cb1_audit,
        retriever,
        publisher,
        execution_mode,
        security_clean=True,
    )
    for name, expected in reports.items():
        if _read_json(directory / name) != expected:
            raise CB2Error(f"artifact report cannot be recomputed: {name}")
    acceptance = reports["metrics.json"]["acceptance"]
    c3_02_gate_approved = manifest.get("c3_02_gate_approved")
    if type(c3_02_gate_approved) is not bool:
        raise CB2Error("manifest C3-02 Gate approval is invalid")
    audit = _read_json(directory / "attempt-audit.json")
    expected_qualified = (
        execution_mode == "qualified"
        and c3_02_gate_approved
        and acceptance["decision"] == "qualified"
    )
    expected_audit = {
        "schema_version": "code-c-b2-attempt-audit-v1",
        "status": "completed",
        "execution_mode": execution_mode,
        "c3_02_gate_approved": c3_02_gate_approved,
        "treatment_qualified": expected_qualified,
        "started_at": audit.get("started_at"),
        "completed_at": run["completed_at"],
        "run_id": run["id"],
        "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "audit_control_run_id": CB1_AUDIT_RUN_ID,
        "audit_control_is_qualified_baseline": False,
    }
    if audit != expected_audit or audit.get("started_at") is None:
        raise CB2Error("attempt audit identity or terminal state is invalid")
    declared = (run.get("config") or {}).get("declared") or {}
    input_fingerprint = declared.get("workspace_input_fingerprint")
    if (
        not isinstance(input_fingerprint, dict)
        or input_fingerprint.get("package_hash") != PACKAGE_HASH
        or input_fingerprint.get("cb0_qualified_run_id") != CB0_QUALIFIED_RUN_ID
        or input_fingerprint.get("cb1_audit_run_id") != CB1_AUDIT_RUN_ID
        or input_fingerprint.get("cb1_audit_is_qualified_baseline") is not False
        or not str(input_fingerprint.get("cb2_treatment_input_hash") or "").startswith("sha256:")
    ):
        raise CB2Error("embedded workspace input fingerprint is incomplete")
    expected_manifest = _manifest(
        run,
        package,
        retriever,
        publisher,
        cb0,
        cb1_audit,
        expected_files,
        input_fingerprint,
        execution_mode,
        acceptance,
        c3_02_gate_approved=c3_02_gate_approved,
    )
    if manifest != expected_manifest:
        raise CB2Error("manifest cannot be reconstructed from immutable C-B2 inputs")
    if manifest.get("treatment_qualified") is not expected_qualified:
        raise CB2Error("qualification flag differs from C-B2 acceptance and Gate state")
    if execution_mode == "qualified":
        if (
            not c3_02_gate_approved
            or not _is_real_publisher(publisher)
            or not _is_real_retriever(retriever)
        ):
            raise CB2Error("qualified artifact lacks C3-02 Gate and production identities")
    elif manifest.get("treatment_qualified") is not False:
        raise CB2Error("test-only artifacts can never be treatment-qualified")
    return {
        "status": "verified" if execution_mode == "qualified" else "verified-test-artifact",
        "run_id": run["id"],
        "execution_mode": execution_mode,
        "treatment_qualified": manifest["treatment_qualified"],
        "dataset_package_hash": PACKAGE_HASH,
        "case_membership_hash": package.membership_hash,
        "case_count": run["case_count"],
        "result_count": len(run["results"]),
        "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "audit_control_run_id": CB1_AUDIT_RUN_ID,
        "audit_control_is_qualified_baseline": False,
        "artifact_set_hash": manifest["artifact_set_hash"],
        "manifest_canonical_hash": _fingerprint(manifest),
        "acceptance": acceptance,
        "security_scan": security,
    }


def run_cb2(
    *,
    root: Path | None = None,
    runs_dir: Path | None = None,
    execution_mode: ExecutionMode = "qualified",
    publisher_factory: PublisherFactory | None = None,
    retriever_factory: RetrieverFactory | None = None,
    c3_02_gate_approved: bool = False,
) -> Path:
    resolved_root = (root or repository_root()).resolve()
    resolved_runs = (
        runs_dir.resolve()
        if runs_dir is not None
        else (resolved_root / "evals" / "code" / "runs").resolve()
    )
    repository_runs = (resolved_root / "evals" / "code" / "runs").resolve()
    if execution_mode == "test":
        if publisher_factory is None or retriever_factory is None:
            raise CB2Error("test execution requires explicit fake component factories")
        if resolved_runs == repository_runs or resolved_runs.is_relative_to(repository_runs):
            raise CB2Error("test execution may not write under evals/code/runs")
        if c3_02_gate_approved:
            raise CB2Error("test execution cannot claim C3-02 Gate approval")
    elif execution_mode == "qualified":
        if publisher_factory is not None or retriever_factory is not None:
            raise CB2Error("qualified execution forbids component injection")
        if not c3_02_gate_approved:
            raise CB2Error("qualified execution requires explicit C3-02 Gate approval")
    else:
        raise CB2Error("execution_mode must be qualified or test")

    cb0 = cb1._load_cb0_anchor(resolved_root)
    cb1_audit = _load_cb1_audit_anchor(resolved_root)
    include_production_hash = execution_mode == "qualified"
    production_before = cb1._production_database_state(
        resolved_root,
        include_hash=include_production_hash,
    )
    input_fingerprint = workspace_input_fingerprint(resolved_root, resolved_runs)
    final_dir: Path | None = None
    with tempfile.TemporaryDirectory(prefix="code-c-b2-") as temporary:
        temporary_root = Path(temporary).resolve()
        base = cb1.prepare_cb1_environment(
            temporary_root / "work",
            root=resolved_root,
        )
        c2_retriever = cb1._load_real_retriever(base.runtime)
        c2_descriptor = cb1._retriever_descriptor(c2_retriever)
        cb1._publish_real_treatment_index(base, c2_descriptor)
        profile = _embedding_profile()
        publisher = (
            publisher_factory(base.runtime, profile)
            if publisher_factory is not None
            else _load_real_publisher(base.runtime, profile)
        )
        prepared = _publish_dense_profile(base, publisher, profile)
        retriever = (
            retriever_factory(base.runtime, profile)
            if retriever_factory is not None
            else _load_real_retriever(base.runtime, profile)
        )
        retriever_descriptor = _retriever_descriptor(retriever)
        if execution_mode == "qualified" and (
            not _is_real_publisher(prepared.publisher)
            or not _is_real_retriever(retriever_descriptor)
        ):
            raise CB2Error("qualified execution did not use production C3-02 components")
        base.runtime.platform.code = _CB2EvaluationRetriever(
            retriever,
            base.runtime,
            base.package,
            retriever_descriptor,
            prepared.publisher,
            profile,
        )
        config_snapshot = _descriptor_config(
            retriever_descriptor,
            prepared.publisher,
        )
        request = CodeEvaluationRunRequest(
            project_id=PROJECT_ID,
            dataset_id=DATASET_ID,
            dataset_version=DATASET_VERSION,
            package_hash=PACKAGE_HASH,
            case_ids=list(base.loaded.case_ids),
            repository_ids=sorted(source.repository_id for source in base.sources.values()),
            graph_candidate_enabled=False,
            limit_per_query=TOP_K,
            config={
                "cb2_config_snapshot": config_snapshot,
                "workspace_input_fingerprint": input_fingerprint,
                "materialization_observation": _materialization_observation(prepared),
            },
        )
        run = base.runtime.evaluation.run_code(
            request,
            allowed_acl_refs=[ACL_REF],
            enforce_acl=True,
        )
        _validate_run(
            run,
            base.loaded,
            retriever_descriptor,
            prepared.publisher,
            base.database_path,
        )
        baseline.assert_terminal_immutability(base.database_path, run["id"])
        post_fingerprint = workspace_input_fingerprint(resolved_root, resolved_runs)
        if post_fingerprint != input_fingerprint:
            raise CB2Error("workspace input fingerprint drifted during C-B2 execution")
        if (
            cb1._production_database_state(
                resolved_root,
                include_hash=include_production_hash,
            )
            != production_before
        ):
            raise CB2Error("formal var/evidence-rag.sqlite3 changed during isolated C-B2")

        token = str(run["id"]).rsplit("/", 1)[-1]
        artifact_dir = temporary_root / "artifact" / token
        artifact_dir.mkdir(parents=True)
        database_artifact = artifact_dir / "evaluation.sqlite3"
        with base.runtime.store.connection() as database:
            database.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(base.database_path, database_artifact)
        try:
            baseline._sanitize_database(database_artifact, None)
        except Exception as exc:
            raise CB2Error(f"unable to sanitize portable C-B2 SQLite: {exc}") from exc
        persisted_store = EvaluationStore(baseline._ReadOnlySQLiteStore(database_artifact))
        persisted_run = persisted_store.get_run(run["id"])
        if persisted_run is None:
            raise CB2Error("portable database lost the completed C-B2 Run")
        _validate_run(
            persisted_run,
            base.loaded,
            retriever_descriptor,
            prepared.publisher,
            database_artifact,
        )
        baseline.assert_terminal_immutability(database_artifact, run["id"])

        reports = _report_payloads(
            persisted_run,
            base.package,
            database_artifact,
            cb0,
            cb1_audit,
            retriever_descriptor,
            prepared.publisher,
            execution_mode,
            security_clean=True,
        )
        for name, payload in reports.items():
            _write_json(artifact_dir / name, payload)
        acceptance = reports["metrics.json"]["acceptance"]
        qualified = (
            execution_mode == "qualified"
            and c3_02_gate_approved
            and acceptance["decision"] == "qualified"
        )
        _write_json(
            artifact_dir / "attempt-audit.json",
            {
                "schema_version": "code-c-b2-attempt-audit-v1",
                "status": "completed",
                "execution_mode": execution_mode,
                "c3_02_gate_approved": c3_02_gate_approved,
                "treatment_qualified": qualified,
                "started_at": persisted_run["started_at"],
                "completed_at": persisted_run["completed_at"],
                "run_id": persisted_run["id"],
                "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
                "audit_control_run_id": CB1_AUDIT_RUN_ID,
                "audit_control_is_qualified_baseline": False,
            },
        )
        try:
            baseline._require_clean_security_scan(artifact_dir)
        except Exception as exc:
            raise CB2Error(f"C-B2 artifact security scan failed: {exc}") from exc
        artifact_files = _artifact_file_records(artifact_dir)
        manifest = _manifest(
            persisted_run,
            base.package,
            retriever_descriptor,
            prepared.publisher,
            cb0,
            cb1_audit,
            artifact_files,
            input_fingerprint,
            execution_mode,
            acceptance,
            c3_02_gate_approved=c3_02_gate_approved,
        )
        _write_json(artifact_dir / "manifest.json", manifest)
        verify_artifact(
            artifact_dir,
            root=resolved_root,
            allow_test_artifact=execution_mode == "test",
        )
        if (
            cb1._production_database_state(
                resolved_root,
                include_hash=include_production_hash,
            )
            != production_before
        ):
            raise CB2Error("formal database changed during artifact verification")

        resolved_runs.mkdir(parents=True, exist_ok=True)
        final_dir = resolved_runs / token
        if final_dir.exists():
            raise CB2Error("artifact directory for the completed C-B2 Run already exists")
        artifact_dir.rename(final_dir)

    assert final_dir is not None
    verify_artifact(
        final_dir,
        root=resolved_root,
        allow_test_artifact=execution_mode == "test",
    )
    if (
        cb1._production_database_state(
            resolved_root,
            include_hash=include_production_hash,
        )
        != production_before
    ):
        raise CB2Error("formal database changed during final verify-only check")
    return final_dir


def _resolve_artifact(value: str, runs_dir: Path) -> Path:
    candidate = Path(value)
    if candidate.is_dir():
        return candidate.resolve()
    nested = runs_dir / value
    if nested.is_dir():
        return nested.resolve()
    raise CB2Error(f"C-B2 artifact not found: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Code C-B2 local dense + exact/sparse treatment")
    parser.add_argument("--repository-root", type=Path, default=repository_root())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "prepare",
        help="validate anchors and print PREPARED without creating an artifact",
    )
    run_parser = subparsers.add_parser(
        "run",
        help="run public production components after the C3-02 Gate",
    )
    run_parser.add_argument("--runs-dir", type=Path)
    run_parser.add_argument("--c3-02-gate-approved", action="store_true")
    verify_parser = subparsers.add_parser(
        "verify", help="verify an existing production C-B2 artifact"
    )
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()
    try:
        if args.command == "prepare":
            output = preparation_status(root=root)
        elif args.command == "run":
            artifact = run_cb2(
                root=root,
                runs_dir=args.runs_dir,
                c3_02_gate_approved=args.c3_02_gate_approved,
            )
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
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
