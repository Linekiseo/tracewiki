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
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from ..rag.sources.code.embedding_v2 import EmbeddingProfile
from ..runtime import Runtime
from . import baseline, cb1, cb2
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

CB5_SCHEMA_VERSION = "code-c-b5-reranker-calibration-treatment-artifact-v1"
CB5_RUNNER_VERSION = "code-c-b5-runner-v1"
CB0_QUALIFIED_RUN_ID = cb1.CB0_QUALIFIED_RUN_ID
CB0_ARTIFACT_DIRECTORY = cb1.CB0_ARTIFACT_DIRECTORY
CB1_AUDIT_RUN_ID = cb2.CB1_AUDIT_RUN_ID
CB1_AUDIT_ARTIFACT_DIRECTORY = cb2.CB1_AUDIT_ARTIFACT_DIRECTORY
CB2_AUDIT_RUN_ID = "evaluation-run://project-code-golden-v2/6fad2dd1c67846668ba38045ddae0064"
CB2_AUDIT_ARTIFACT_DIRECTORY = "6fad2dd1c67846668ba38045ddae0064"

PUBLIC_CODE_MODULE = cb2.PUBLIC_CODE_MODULE
REAL_PUBLISHER_CLASS = cb2.REAL_PUBLISHER_CLASS
REAL_HYBRID_CLASS = cb2.REAL_RETRIEVER_CLASS
REAL_RERANKER_CLASS = "CodeRerankedRetriever"
REAL_CALIBRATION_CLASS = "CalibrationArtifact"
TOP_K = cb2.TOP_K
CHANNEL_BUDGET = dict(cb2.CHANNEL_BUDGET)
CALIBRATION_MIN_FINAL_OBSERVATIONS = 100
CALIBRATION_BIN_COUNT = 10

FLAG_SNAPSHOT = {
    "unit_builder": "ast-v2",
    "local_dense_required": True,
    "hybrid_enabled": True,
    "reranker_enabled": True,
    "calibration_enabled": True,
    "rerank_timeout_ms": 100,
    "timeout_fallback": "preserve-hybrid-order-v1",
    "cross_encoder_enabled": False,
    "remote_model_enabled": False,
    "graph_candidate_enabled": False,
    "semantic_resolver": "off",
    "context": "snippet-v1",
}
PRODUCTION_IDENTITY = {
    "module": PUBLIC_CODE_MODULE,
    "publisher_class": REAL_PUBLISHER_CLASS,
    "hybrid_retriever_class": REAL_HYBRID_CLASS,
    "reranker_class": REAL_RERANKER_CLASS,
    "calibration_artifact_class": REAL_CALIBRATION_CLASS,
    "stage_order": [
        "ast-v2",
        "local-dense-ready",
        "exact-sparse-dense-hybrid",
        "deterministic-rerank",
    ],
}
CROSS_ENCODER_STATUS = {
    "status": "unavailable",
    "execution": "not-run",
    "reason": "C-B5 is deterministic and offline; no cross-encoder is installed or authorized.",
}
CAPABILITY_BOUNDARY = {
    "available": [
        "active-generation ast-v2 retrieval units",
        "exact, sparse, and local dense hybrid retrieval",
        "deterministic reranking with explicit timeout fallback",
        "versioned calibration artifact states",
        "candidate explanation codes and negative reason auditing",
    ],
    "unavailable": [
        {
            "capability": "cross-encoder reranking",
            "status": "unavailable",
            "execution": "not-run",
            "reason": CROSS_ENCODER_STATUS["reason"],
        },
        {
            "capability": "remote model reranking or calibration",
            "status": "disabled",
            "reason": "C-B5 is a local, deterministic treatment.",
        },
        {
            "capability": "historical, graph, and validation retrieval",
            "status": "out-of-scope",
            "reason": "The released 17 ineligible cases remain outside the fixed denominator.",
        },
    ],
}
REQUIRED_REPORTING = (
    "overall/exact/identifier/low-overlap entity and locator quality",
    "overall/exact/identifier/low-overlap MRR and nDCG",
    "same-name hard-negative, harmful candidate, and duplicate rates",
    "dense-only and rerank contribution",
    "timeout fallback rate and timeout P95",
    "explanation codes and negative reasons",
    "calibration ECE, Brier, provisional, and unavailable states",
    "ingest, index, and query costs",
    "true deltas against C-B0 and both NOT QUALIFIED audit controls",
)
REPORT_FILES = cb2.REPORT_FILES
ARTIFACT_FILES = cb2.ARTIFACT_FILES
EXPECTED_ARTIFACT_ENTRIES = frozenset(("manifest.json", *ARTIFACT_FILES))

ExecutionMode = Literal["qualified", "test"]
PublisherFactory = Callable[[Runtime, EmbeddingProfile], Any]
HybridFactory = Callable[[Runtime, EmbeddingProfile], Any]
CalibrationFactory = Callable[[Runtime], Any]
RerankerFactory = Callable[[Runtime, Any, Any], Any]


class CB5Error(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RerankerDescriptor:
    module: str
    class_name: str
    version: str
    policy: str
    fallback_policy: str

    def to_dict(self) -> dict[str, str]:
        return {
            "module": self.module,
            "class": self.class_name,
            "version": self.version,
            "policy": self.policy,
            "fallback_policy": self.fallback_policy,
        }


@dataclass(frozen=True, slots=True)
class CalibrationDescriptor:
    module: str
    class_name: str
    version: str
    status: str
    profile_version: str
    model_version: str
    index_version: str
    reranker_version: str
    method: str
    provisional: bool
    sample_count: int
    labeled_count: int
    ece: float | None
    brier: float | None
    unavailable_reason: str
    artifact_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "class": self.class_name,
            "version": self.version,
            "status": self.status,
            "profile_version": self.profile_version,
            "model_version": self.model_version,
            "index_version": self.index_version,
            "reranker_version": self.reranker_version,
            "method": self.method,
            "provisional": self.provisional,
            "sample_count": self.sample_count,
            "labeled_count": self.labeled_count,
            "ece": self.ece,
            "brier": self.brier,
            "unavailable_reason": self.unavailable_reason,
            "artifact_fingerprint": self.artifact_fingerprint,
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
        raise CB5Error(f"unable to read artifact JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise CB5Error(f"artifact JSON must be an object: {path.name}")
    return value


def _relative_or_name(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _load_cb2_audit_anchor(root: Path) -> cb2.AuditAnchor:
    directory = (root / "evals" / "code" / "runs" / CB2_AUDIT_ARTIFACT_DIRECTORY).resolve()
    if not directory.is_dir():
        raise CB5Error(f"C-B2 audit artifact is missing: {CB2_AUDIT_ARTIFACT_DIRECTORY}")
    try:
        verification = cb2.verify_artifact(directory, root=root)
    except Exception as exc:
        raise CB5Error(f"C-B2 audit verification failed: {exc}") from exc
    manifest = _read_json(directory / "manifest.json")
    package = validate_golden_package(root)
    if (
        verification.get("run_id") != CB2_AUDIT_RUN_ID
        or manifest.get("run_id") != CB2_AUDIT_RUN_ID
        or manifest.get("treatment_qualified") is not False
        or (manifest.get("acceptance") or {}).get("decision") != "not-qualified"
        or (manifest.get("dataset") or {}).get("package_hash") != PACKAGE_HASH
        or (manifest.get("dataset") or {}).get("case_membership_hash") != package.membership_hash
        or (manifest.get("dataset") or {}).get("eligible_cases") != EXPECTED_ELIGIBLE_COUNT
    ):
        raise CB5Error("C-B2 artifact does not match the fixed NOT QUALIFIED audit anchor")
    store = EvaluationStore(baseline._ReadOnlySQLiteStore(directory / "evaluation.sqlite3"))
    run = store.get_run(CB2_AUDIT_RUN_ID)
    if run is None:
        raise CB5Error("C-B2 audit SQLite does not contain the anchored Run")
    return cb2.AuditAnchor(
        directory=directory,
        manifest=manifest,
        verification=verification,
        run=run,
    )


def _component_contract(name: str) -> dict[str, Any]:
    component, error = cb2._public_class(name)
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
    cb1_audit = cb2._load_cb1_audit_anchor(resolved_root)
    cb2_audit = _load_cb2_audit_anchor(resolved_root)
    components = {
        name: _component_contract(class_name)
        for name, class_name in (
            ("publisher", REAL_PUBLISHER_CLASS),
            ("hybrid_retriever", REAL_HYBRID_CLASS),
            ("reranker", REAL_RERANKER_CLASS),
            ("calibration_artifact", REAL_CALIBRATION_CLASS),
        )
    }
    production_ready = all(item["status"] == "ready" for item in components.values())
    return {
        "status": "PREPARED",
        "execution_status": "pending-c3-03-gate",
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
        "controls": {
            "qualified_baseline": {
                "run_id": CB0_QUALIFIED_RUN_ID,
                "treatment_qualified": True,
                "qualification_record_hash": cb0.verification["qualification_record_hash"],
                "manifest_canonical_hash": cb0.verification["manifest_canonical_hash"],
            },
            "cb1_not_qualified_audit": {
                "run_id": CB1_AUDIT_RUN_ID,
                "treatment_qualified": False,
                "qualification_use": "forbidden",
                "manifest_canonical_hash": cb1_audit.verification["manifest_canonical_hash"],
            },
            "cb2_not_qualified_audit": {
                "run_id": CB2_AUDIT_RUN_ID,
                "treatment_qualified": False,
                "qualification_use": "forbidden",
                "manifest_canonical_hash": cb2_audit.verification["manifest_canonical_hash"],
            },
        },
        "config_contract": {
            "treatment": "C-B5 deterministic reranker plus calibration",
            "production_identity": PRODUCTION_IDENTITY,
            "flag_snapshot": FLAG_SNAPSHOT,
            "profile": cb2.PROFILE_SNAPSHOT,
            "provider_provenance": cb2.PROVIDER_PROVENANCE,
            "top_k": TOP_K,
            "channel_budget": CHANNEL_BUDGET,
            "capability_boundary": CAPABILITY_BOUNDARY,
            "cross_encoder": CROSS_ENCODER_STATUS,
        },
        "production_components": {
            "status": "ready" if production_ready else "pending",
            "components": components,
            "calibration_artifact_at_run": "required-versioned-public-artifact",
        },
        "qualification_policy": {
            "requires_c3_03_gate_approval": True,
            "requires_public_production_identities": True,
            "requires_complete_33_case_coverage": True,
            "requires_clean_artifact_security_scan": True,
            "requires_cb0_exact_identifier_locator_non_regression": True,
            "requires_hard_negative_or_harmful_non_regression": True,
            "requires_correct_timeout_fallback": True,
            "provisional_calibration_allowed": True,
            "provisional_probability_claim_allowed": False,
            "cross_encoder_required": False,
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
        root / "tests/test_code_cb5_run.py",
        root / "evals/code/code_golden_v2.py",
        root / "tests/fixtures/code_golden/v2/materializer.py",
    ]
    digest = hashlib.sha256()
    for path in sorted(set(paths), key=lambda item: item.as_posix()):
        if not path.is_file():
            raise CB5Error(f"required C-B5 implementation input is missing: {path}")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def workspace_input_fingerprint(root: Path, runs_dir: Path) -> dict[str, Any]:
    try:
        common = baseline.workspace_input_fingerprint(root, runs_dir)
    except Exception as exc:
        raise CB5Error(f"unable to fingerprint the C-B5 workspace: {exc}") from exc
    return {
        **common,
        "cb5_treatment_input_hash": _treatment_input_hash(root),
        "cb0_qualified_run_id": CB0_QUALIFIED_RUN_ID,
        "cb1_audit_run_id": CB1_AUDIT_RUN_ID,
        "cb2_audit_run_id": CB2_AUDIT_RUN_ID,
        "cb1_audit_is_qualified_baseline": False,
        "cb2_audit_is_qualified_baseline": False,
        "flag_snapshot_fingerprint": _fingerprint(FLAG_SNAPSHOT),
        "production_identity_fingerprint": _fingerprint(PRODUCTION_IDENTITY),
    }


def _json_snapshot(value: Any) -> dict[str, Any]:
    if hasattr(value, "canonical_snapshot"):
        value = value.canonical_snapshot()
    elif hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    elif isinstance(value, Mapping):
        value = dict(value)
    else:
        value = {
            name: getattr(value, name)
            for name in (
                "version",
                "artifact_version",
                "calibration_version",
                "status",
                "method",
                "schema_version",
            )
            if hasattr(value, name)
        }
    if not isinstance(value, Mapping):
        raise CB5Error("calibration artifact must expose a canonical mapping snapshot")
    snapshot = dict(value)
    try:
        _canonical_bytes(snapshot)
    except (TypeError, ValueError) as exc:
        raise CB5Error("calibration artifact snapshot is not canonical JSON") from exc
    return snapshot


def _string_from_sources(
    sources: Sequence[Any],
    names: Sequence[str],
    *,
    label: str,
) -> str:
    for source in sources:
        for name in names:
            if isinstance(source, Mapping):
                observed = source.get(name)
            else:
                observed = getattr(source, name, None)
            if isinstance(observed, str) and observed.strip():
                return observed.strip()
    raise CB5Error(f"component must declare a non-empty {label}")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise CB5Error(f"component trace contains a non-JSON value: {type(value).__name__}")


def _reranker_descriptor(reranker: Any) -> RerankerDescriptor:
    real = type(reranker).__name__ == REAL_RERANKER_CLASS and (
        type(reranker).__module__ == PUBLIC_CODE_MODULE
        or type(reranker).__module__.startswith(f"{PUBLIC_CODE_MODULE}.")
    )
    module = importlib.import_module(type(reranker).__module__) if real else reranker
    nested = getattr(reranker, "reranker", None)
    return RerankerDescriptor(
        module=type(reranker).__module__,
        class_name=type(reranker).__name__,
        version=_string_from_sources(
            (reranker, nested, module),
            (
                "reranker_version",
                "RERANKER_VERSION",
                "RERANK_VERSION",
                "DETERMINISTIC_RERANKER_VERSION",
                "version",
            ),
            label="reranker version",
        ),
        policy=_string_from_sources(
            (reranker, nested, module),
            (
                "rerank_policy",
                "RERANK_POLICY",
                "RERANKER_POLICY",
                "model_version",
                "RERANK_MODEL_VERSION",
                "policy",
            ),
            label="rerank policy",
        ),
        fallback_policy=(
            FLAG_SNAPSHOT["timeout_fallback"]
            if real
            else _string_from_sources(
                (reranker, module),
                (
                    "fallback_policy",
                    "RERANK_FALLBACK_POLICY",
                    "TIMEOUT_FALLBACK_POLICY",
                ),
                label="rerank fallback policy",
            )
        ),
    )


def _calibration_descriptor(artifact: Any) -> CalibrationDescriptor:
    snapshot = _json_snapshot(artifact)
    version = _string_from_sources(
        (artifact, snapshot),
        ("calibration_version", "artifact_version", "version"),
        label="calibration artifact version",
    )
    status = _string_from_sources(
        (artifact, snapshot),
        ("status", "calibration_status"),
        label="calibration artifact status",
    ).casefold()
    provisional = bool(snapshot.get("provisional", status == "provisional"))
    if status == "available" and provisional:
        status = "provisional"
    if status not in {"ready", "calibrated", "available", "provisional", "unavailable"}:
        raise CB5Error("calibration artifact status must be available, provisional, or unavailable")
    ece = snapshot.get("ece")
    brier = snapshot.get("brier")
    for name, value in (("ece", ece), ("brier", brier)):
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= float(value) <= 1.0
        ):
            raise CB5Error(f"calibration artifact {name} must be null or a unit float")
    return CalibrationDescriptor(
        module=type(artifact).__module__,
        class_name=type(artifact).__name__,
        version=version,
        status=status,
        profile_version=str(snapshot.get("profile_version") or ""),
        model_version=str(snapshot.get("model_version") or ""),
        index_version=str(snapshot.get("index_version") or ""),
        reranker_version=str(snapshot.get("reranker_version") or ""),
        method=str(snapshot.get("method") or "unavailable"),
        provisional=provisional,
        sample_count=int(snapshot.get("sample_count") or 0),
        labeled_count=int(snapshot.get("labeled_count") or 0),
        ece=float(ece) if ece is not None else None,
        brier=float(brier) if brier is not None else None,
        unavailable_reason=str(snapshot.get("unavailable_reason") or ""),
        artifact_fingerprint=_fingerprint(snapshot),
    )


def _is_public_identity(module: str, class_name: str, expected: str) -> bool:
    return class_name == expected and (
        module == PUBLIC_CODE_MODULE or module.startswith(f"{PUBLIC_CODE_MODULE}.")
    )


def _is_real_reranker(descriptor: RerankerDescriptor) -> bool:
    return _is_public_identity(descriptor.module, descriptor.class_name, REAL_RERANKER_CLASS)


def _is_real_calibration(descriptor: CalibrationDescriptor) -> bool:
    return _is_public_identity(
        descriptor.module,
        descriptor.class_name,
        REAL_CALIBRATION_CLASS,
    )


def _load_calibration_artifact(path: Path) -> Any:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise CB5Error("calibration artifact path must be a regular file")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CB5Error("calibration artifact must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise CB5Error("calibration artifact JSON must be an object")
    component, error = cb2._public_class(REAL_CALIBRATION_CLASS)
    if component is None:
        raise CB5Error(f"C3-03 is not ready: {error}")
    module = importlib.import_module(component.__module__)
    bin_component = getattr(module, "CalibrationBin", None)
    raw_bins = payload.get("bins")
    if (
        inspect.isclass(bin_component)
        and isinstance(raw_bins, list)
        and all(isinstance(item, Mapping) for item in raw_bins)
    ):
        payload["bins"] = tuple(bin_component(**dict(item)) for item in raw_bins)
    for name in ("model_validate", "from_dict", "from_json_dict", "load_dict"):
        method = getattr(component, name, None)
        if callable(method):
            try:
                return method(payload)
            except Exception as exc:
                raise CB5Error(f"unable to load calibration artifact via {name}") from exc
    try:
        return component(**payload)
    except Exception as exc:
        raise CB5Error("unable to construct public CalibrationArtifact from JSON") from exc


def _construct_reranker(
    component: type[Any],
    *,
    runtime: Runtime,
    hybrid: Any,
    calibration: Any,
) -> Any:
    values = {
        "store": runtime.store,
        "runtime": runtime,
        "retriever": hybrid,
        "base_retriever": hybrid,
        "hybrid_retriever": hybrid,
        "calibration": calibration,
        "calibration_artifact": calibration,
        "timeout_ms": FLAG_SNAPSHOT["rerank_timeout_ms"],
        "rerank_timeout_ms": FLAG_SNAPSHOT["rerank_timeout_ms"],
        "fallback_policy": FLAG_SNAPSHOT["timeout_fallback"],
        "flag_snapshot": FLAG_SNAPSHOT,
        "config": FLAG_SNAPSHOT,
    }
    try:
        signature = inspect.signature(component)
    except (TypeError, ValueError) as exc:
        raise CB5Error(f"cannot inspect public component {component.__name__}") from exc
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
            raise CB5Error(
                f"public {component.__name__} constructor has unsupported required "
                f"parameter: {parameter.name}"
            )
    try:
        return component(**kwargs)
    except Exception as exc:
        raise CB5Error(f"unable to construct public {component.__name__}") from exc


def _load_real_reranker(runtime: Runtime, hybrid: Any, calibration: Any) -> Any:
    component, error = cb2._public_class(REAL_RERANKER_CLASS)
    if component is None:
        raise CB5Error(f"C3-03 is not ready: {error}")
    reranker = _construct_reranker(
        component,
        runtime=runtime,
        hybrid=hybrid,
        calibration=calibration,
    )
    if not _is_real_reranker(_reranker_descriptor(reranker)):
        raise CB5Error("qualified mode did not construct the public production reranker")
    return reranker


def _call_search(
    component: Any, request: Any, profile: Any
) -> tuple[dict[str, Any], dict[str, Any], float]:
    method = getattr(component, "search_with_trace", None)
    if not callable(method):
        method = getattr(component, "search", None)
    if not callable(method):
        raise CB5Error("retrieval component has no callable search boundary")
    signature = inspect.signature(method)
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
    started = time.perf_counter()
    try:
        raw = (
            method(request, profile) if accepts_varargs or len(positional) >= 2 else method(request)
        )
    except Exception as exc:
        raise CB5Error(f"{type(component).__name__} search failed") from exc
    elapsed_ms = (time.perf_counter() - started) * 1_000
    serialized = _jsonable(raw)
    if not isinstance(serialized, Mapping):
        raise CB5Error("serialized retrieval result is not a mapping")
    serialized = dict(serialized)
    nested = serialized.get("result")
    if isinstance(nested, Mapping):
        trace = serialized.get("trace")
        return (
            dict(nested),
            dict(trace) if isinstance(trace, Mapping) else {},
            elapsed_ms,
        )
    trace = serialized.get("trace")
    return serialized, dict(trace) if isinstance(trace, Mapping) else {}, elapsed_ms


def _raw_candidates(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("candidates")
    if values is None:
        values = payload.get("results")
    if not isinstance(values, list):
        raise CB5Error("retrieval response candidates must be a list")
    if any(not isinstance(item, Mapping) for item in values):
        raise CB5Error("retrieval response contains a non-mapping candidate")
    return [dict(item) for item in values]


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(candidate.get("source_generation") or candidate.get("version") or ""),
        str(candidate.get("retrieval_unit_id") or candidate.get("unit_id") or ""),
    )


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, (list, tuple, set)):
        return []
    values: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            observed = item.get("code") or item.get("reason") or item.get("id")
        else:
            observed = item
        if observed is not None and str(observed):
            values.append(str(observed))
    return values


def _trace_value(trace: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in trace:
            return trace[name]
    return None


def _judgment_map(case: GoldenCase) -> dict[tuple[str, str], int]:
    values: dict[tuple[str, str], int] = {}
    for raw in case.request.candidate_judgments:
        judgment = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        entity_id = str(judgment.get("entity_id") or "")
        unit_id = str(judgment.get("retrieval_unit_id") or "")
        grade = int(judgment.get("relevance_grade") or 0)
        if entity_id:
            values[("entity", entity_id)] = grade
        if unit_id:
            values[("unit", unit_id)] = grade
    return values


def _candidate_calibration(
    candidates: Sequence[Mapping[str, Any]],
    case: GoldenCase,
    calibration: CalibrationDescriptor,
) -> tuple[list[dict[str, Any]], list[str]]:
    judgments = _judgment_map(case)
    observations: list[dict[str, Any]] = []
    unavailable_reasons: list[str] = []
    for candidate in candidates:
        raw = candidate.get("calibrated_relevance")
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(mode="json")
        if not isinstance(raw, Mapping):
            unavailable_reasons.append("candidate omitted calibrated_relevance")
            continue
        status = str(raw.get("status") or "").casefold()
        if status == "calibrated":
            score = raw.get("score")
            version = str(raw.get("calibration_version") or "")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not 0.0 <= float(score) <= 1.0
                or version != calibration.version
            ):
                raise CB5Error("candidate calibration score/version violates the artifact")
            entity_id = str(candidate.get("entity_id") or "")
            unit_id = str(candidate.get("retrieval_unit_id") or "")
            grade = judgments.get(("unit", unit_id))
            if grade is None:
                grade = judgments.get(("entity", entity_id))
            if grade is not None:
                observations.append(
                    {
                        "score": float(score),
                        "label": int(grade > 0),
                        "grade": grade,
                    }
                )
        elif status in {"disabled", "unavailable"}:
            unavailable_reasons.append(str(raw.get("reason") or status))
        else:
            unavailable_reasons.append("candidate calibration state is unavailable")
    return observations, unavailable_reasons


def _decision_calibration(
    decisions: Sequence[Mapping[str, Any]],
    case: GoldenCase,
    artifact: Any,
    calibration: CalibrationDescriptor,
    *,
    profile_version: str,
    model_version: str,
    index_version: str,
    reranker_version: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not _is_real_calibration(calibration):
        return [], []
    try:
        module = importlib.import_module(type(artifact).__module__)
        apply_calibration = module.apply_calibration
        mismatch_error = module.CalibrationArtifactMismatchError
    except (ImportError, AttributeError) as exc:
        raise CB5Error("public calibration artifact has no apply_calibration boundary") from exc
    judgments = _judgment_map(case)
    observations: list[dict[str, Any]] = []
    unavailable_reasons: list[str] = []
    for decision in decisions:
        if decision.get("accepted") is not True or decision.get("reranked_rank") is None:
            continue
        card = decision.get("card")
        if not isinstance(card, Mapping):
            raise CB5Error("rerank decision lacks its evidence card")
        try:
            application = apply_calibration(
                artifact,
                score=float(decision["rerank_score"]),
                rank=int(decision["reranked_rank"]),
                profile_version=profile_version,
                model_version=model_version,
                index_version=index_version,
                reranker_version=reranker_version,
            )
        except mismatch_error as exc:
            unavailable_reasons.append(f"{type(exc).__name__}: {' '.join(str(exc).split())}")
            continue
        except Exception as exc:
            raise CB5Error("calibration artifact does not match the rerank context") from exc
        application_snapshot = (
            asdict(application)
            if is_dataclass(application) and not isinstance(application, type)
            else _json_snapshot(application)
        )
        if application_snapshot.get("artifact_hash") != calibration.artifact_fingerprint:
            raise CB5Error("calibration application returned a different artifact identity")
        status = str(application_snapshot.get("status") or "")
        if status == "available":
            score = application_snapshot.get("calibrated_score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not 0.0 <= float(score) <= 1.0
            ):
                raise CB5Error("calibration application returned an invalid score")
            entity_id = str(card.get("entity_id") or "")
            unit_id = str(card.get("retrieval_unit_id") or "")
            grade = judgments.get(("unit", unit_id))
            if grade is None:
                grade = judgments.get(("entity", entity_id))
            if grade is not None:
                observations.append(
                    {
                        "score": float(score),
                        "label": int(grade > 0),
                        "grade": grade,
                    }
                )
        else:
            unavailable_reasons.append(
                str(
                    application_snapshot.get("reason")
                    or calibration.unavailable_reason
                    or "calibration unavailable"
                )
            )
    return observations, unavailable_reasons


def _contribution_counts(
    hybrid_candidates: Sequence[Mapping[str, Any]],
    reranked_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    hybrid_order = [_candidate_key(item) for item in hybrid_candidates]
    reranked_order = [_candidate_key(item) for item in reranked_candidates]
    before = {key: index for index, key in enumerate(hybrid_order)}
    hybrid_keys = set(hybrid_order)
    reranked_keys = set(reranked_order)
    changed = 0
    promoted = 0
    demoted = 0
    for index, key in enumerate(reranked_order):
        previous = before.get(key)
        if previous is None or previous == index:
            continue
        changed += 1
        promoted += int(index < previous)
        demoted += int(index > previous)
    dense = 0
    dense_only = 0
    for candidate in reranked_candidates:
        scores = candidate.get("raw_channel_scores") or []
        channels = {
            cb1._channel_name(score.get("channel"))
            for score in scores
            if isinstance(score, Mapping)
        }
        dense += int("dense" in channels)
        dense_only += int(channels == {"dense"})
    return {
        "dense_contribution_count": dense,
        "dense_only_contribution_count": dense_only,
        "rerank_changed_count": changed,
        "rerank_promoted_count": promoted,
        "rerank_demoted_count": demoted,
        "rerank_removed_count": len(hybrid_keys - reranked_keys),
        "rerank_added_count": len(reranked_keys - hybrid_keys),
    }


class _CB5EvaluationRetriever(cb1._CB1EvaluationRetriever):
    def __init__(
        self,
        hybrid: Any,
        reranker: Any,
        runtime: Runtime,
        package: GoldenPackage,
        hybrid_descriptor: cb2.RetrieverDescriptor,
        publisher_descriptor: cb2.ComponentDescriptor,
        reranker_descriptor: RerankerDescriptor,
        calibration_artifact: Any,
        calibration_descriptor: CalibrationDescriptor,
        profile: EmbeddingProfile,
    ) -> None:
        super().__init__(hybrid, runtime, package, hybrid_descriptor)  # type: ignore[arg-type]
        self.hybrid = hybrid
        self.reranker = reranker
        self.publisher_descriptor = publisher_descriptor
        self.reranker_descriptor = reranker_descriptor
        self.calibration_artifact = calibration_artifact
        self.calibration_descriptor = calibration_descriptor
        self.embedding_profile = profile

    def search_evaluation(
        self,
        request: Any,
        *,
        graph_candidate_enabled: bool,
    ) -> dict[str, Any]:
        if graph_candidate_enabled:
            raise CB5Error("C-B5 is graph-candidate-off")
        case = self._case_by_question.get(str(request.query))
        if case is None:
            raise CB5Error("C-B5 retriever received a query outside released membership")
        profile = cb2._query_profile(case, request)
        hybrid_payload, _hybrid_trace, _hybrid_elapsed_ms = _call_search(
            self.hybrid,
            request,
            profile,
        )
        reranked_payload, rerank_trace, rerank_elapsed_ms = _call_search(
            self.reranker,
            request,
            profile,
        )
        hybrid_candidates = _raw_candidates(hybrid_payload)
        reranked_candidates = _raw_candidates(reranked_payload)
        hybrid_order = [_candidate_key(item) for item in hybrid_candidates]
        reranked_order = [_candidate_key(item) for item in reranked_candidates]
        outcome = str(
            _trace_value(
                rerank_trace,
                ("rerank_outcome", "outcome", "status"),
            )
            or (
                reranked_payload.get("rerank_outcome")
                if isinstance(reranked_payload, Mapping)
                else ""
            )
            or "complete"
        ).casefold()
        outcome = outcome.replace("-", "_")
        fallback_used = bool(rerank_trace.get("fallback_used"))
        fallback_reason = str(rerank_trace.get("fallback_reason") or "")
        timed_out = (
            outcome in {"timeout", "timed_out"}
            or bool(rerank_trace.get("timed_out"))
            or (fallback_used and "deadline" in fallback_reason.casefold())
        )
        fallback_raw = _trace_value(
            rerank_trace,
            ("fallback_status", "timeout_fallback_status", "fallback"),
        )
        fallback_status = str(
            fallback_raw
            or (
                "succeeded"
                if fallback_used and reranked_order == hybrid_order
                else ("failed" if fallback_used else "not_used")
            )
        ).casefold()
        fallback_status = fallback_status.replace("-", "_")
        fallback_correct = not fallback_used or (
            fallback_status == "succeeded" and reranked_order == hybrid_order
        )
        if timed_out and not fallback_correct:
            raise CB5Error("rerank timeout did not preserve the hybrid candidate order")

        decisions = [
            dict(item) for item in rerank_trace.get("decisions") or [] if isinstance(item, Mapping)
        ]
        calibration_observations, calibration_unavailable = _decision_calibration(
            decisions,
            case,
            self.calibration_artifact,
            self.calibration_descriptor,
            profile_version=profile.profile_version,
            model_version=self.reranker_descriptor.policy,
            index_version=str(hybrid_payload.get("index_version") or ""),
            reranker_version=self.reranker_descriptor.version,
        )
        if not _is_real_calibration(self.calibration_descriptor):
            calibration_observations, calibration_unavailable = _candidate_calibration(
                reranked_candidates,
                case,
                self.calibration_descriptor,
            )
        contribution = _contribution_counts(hybrid_candidates, reranked_candidates)
        explanation_codes: list[str] = []
        negative_reasons: list[str] = []
        for candidate in reranked_candidates:
            explanation_codes.extend(
                _strings(
                    candidate.get("explanation_codes")
                    or candidate.get("rerank_explanation_codes")
                    or candidate.get("explanations")
                )
            )
            negative_reasons.extend(
                _strings(
                    candidate.get("negative_reasons")
                    or candidate.get("negative_reason_codes")
                    or candidate.get("negative_reason")
                )
            )
        for decision in decisions:
            explanation_codes.extend(_strings(decision.get("explanation_code")))
            negative_reasons.extend(_strings(decision.get("negative_reason")))
        explanation_codes.extend(
            _strings(
                _trace_value(
                    rerank_trace,
                    ("explanation_codes", "rerank_explanation_codes"),
                )
            )
        )
        negative_reasons.extend(
            _strings(
                _trace_value(
                    rerank_trace,
                    ("negative_reasons", "negative_reason_codes"),
                )
            )
        )
        response = self._adapt_response(request, reranked_payload)
        trace = dict(response.get("trace") or {})
        latency = _trace_value(
            rerank_trace,
            ("rerank_latency_ms", "duration_ms", "latency_ms", "elapsed_ms"),
        )
        cross_encoder_status = str(rerank_trace.get("cross_encoder_availability") or "unavailable")
        cross_encoder_execution = str(
            rerank_trace.get("cross_encoder_benchmark_status") or "not-run"
        )
        if cross_encoder_status != "unavailable" or cross_encoder_execution != "not-run":
            raise CB5Error("C-B5 cross-encoder must remain unavailable and not-run")
        trace.update(
            {
                "embedding_profile_id": self.embedding_profile.id,
                "embedding_profile_revision": self.embedding_profile.revision,
                "embedding_model": self.embedding_profile.model,
                "embedding_model_revision": cb2.LOCAL_HASH_MODEL_REVISION,
                "embedding_dimension": self.embedding_profile.dimension,
                "embedding_locality": self.embedding_profile.locality,
                "publisher_class": self.publisher_descriptor.class_name,
                "publisher_version": self.publisher_descriptor.version,
                "reranker_class": self.reranker_descriptor.class_name,
                "reranker_version": self.reranker_descriptor.version,
                "rerank_policy": self.reranker_descriptor.policy,
                "rerank_outcome": "timeout" if timed_out else outcome,
                "duration_ms": rerank_elapsed_ms,
                "rerank_latency_ms": (
                    float(latency)
                    if isinstance(latency, (int, float)) and not isinstance(latency, bool)
                    else rerank_elapsed_ms
                ),
                "fallback_status": fallback_status,
                "timeout_fallback_valid": fallback_correct,
                "fallback_reason": fallback_reason,
                "calibration_artifact_class": self.calibration_descriptor.class_name,
                "calibration_version": self.calibration_descriptor.version,
                "calibration_artifact_status": self.calibration_descriptor.status,
                "calibration_artifact_fingerprint": (
                    self.calibration_descriptor.artifact_fingerprint
                ),
                "calibration_profile_version": (self.calibration_descriptor.profile_version),
                "calibration_model_version": self.calibration_descriptor.model_version,
                "calibration_index_version": self.calibration_descriptor.index_version,
                "calibration_reranker_version": (self.calibration_descriptor.reranker_version),
                "calibration_observations": calibration_observations,
                "calibration_unavailable_reasons": calibration_unavailable,
                "explanation_codes": sorted(set(explanation_codes)),
                "negative_reasons": sorted(set(negative_reasons)),
                "cross_encoder_status": "unavailable",
                "cross_encoder_execution": "not-run",
                **contribution,
            }
        )
        return {**response, "trace": trace}


def _descriptor_config(
    hybrid: cb2.RetrieverDescriptor,
    publisher: cb2.ComponentDescriptor,
    reranker: RerankerDescriptor,
    calibration: CalibrationDescriptor,
) -> dict[str, Any]:
    return {
        "schema_version": "code-c-b5-config-snapshot-v1",
        "treatment": "C-B5 deterministic reranker plus calibration",
        "production_identity": PRODUCTION_IDENTITY,
        "flag_snapshot": FLAG_SNAPSHOT,
        "publisher": publisher.to_dict(),
        "hybrid_retriever": hybrid.to_dict(),
        "reranker": reranker.to_dict(),
        "calibration_artifact": calibration.to_dict(),
        "profile": cb2.PROFILE_SNAPSHOT,
        "provider_provenance": cb2.PROVIDER_PROVENANCE,
        "top_k": TOP_K,
        "sparse_index_version": cb2.SPARSE_INDEX_VERSION,
        "channel_budget": CHANNEL_BUDGET,
        "capability_boundary": CAPABILITY_BOUNDARY,
        "cross_encoder": CROSS_ENCODER_STATUS,
        "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "audit_control_run_ids": [CB1_AUDIT_RUN_ID, CB2_AUDIT_RUN_ID],
        "audit_controls_are_qualified_baselines": False,
    }


def _materialization_observation(
    prepared: cb2.PreparedCB2,
    reranker: RerankerDescriptor,
    calibration: CalibrationDescriptor,
) -> dict[str, Any]:
    base = cb2._materialization_observation(prepared)
    observation = {
        **{name: value for name, value in base.items() if name != "observation_fingerprint"},
        "schema_version": "code-c-b5-materialization-v1",
        "treatment": (
            "ast-v2 ingestion -> local dense ready -> exact/sparse/dense hybrid "
            "-> deterministic rerank"
        ),
        "stage_order": PRODUCTION_IDENTITY["stage_order"],
        "flag_snapshot": FLAG_SNAPSHOT,
        "reranker": reranker.to_dict(),
        "calibration_artifact": calibration.to_dict(),
        "cross_encoder": CROSS_ENCODER_STATUS,
        "network_used": False,
        "downloads_performed": False,
    }
    return {**observation, "observation_fingerprint": _fingerprint(observation)}


def _comparison_report(
    treatment: dict[str, Any],
    control: dict[str, Any],
    package: GoldenPackage,
    *,
    role: str,
    control_run_id: str,
) -> dict[str, Any]:
    report = cb2._comparison_report(
        treatment,
        control,
        package,
        role=role,
        control_run_id=control_run_id,
    )
    return {**report, "schema_version": "code-c-b5-comparison-v1"}


def _comparisons(
    run: dict[str, Any],
    package: GoldenPackage,
    cb0: cb1.CB0Anchor,
    cb1_audit: cb2.AuditAnchor,
    cb2_audit: cb2.AuditAnchor,
) -> dict[str, dict[str, Any]]:
    return {
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
        "cb2_not_qualified_audit": _comparison_report(
            run,
            cb2_audit.run,
            package,
            role="not-qualified-audit-control",
            control_run_id=CB2_AUDIT_RUN_ID,
        ),
    }


def _result_traces(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict((result.get("detail") or {}).get("trace") or {}) for result in run.get("results") or []
    ]


def _calibration_report(
    run: dict[str, Any],
    calibration: CalibrationDescriptor,
) -> dict[str, Any]:
    observations = [
        dict(item)
        for trace in _result_traces(run)
        for item in trace.get("calibration_observations") or []
        if isinstance(item, Mapping)
    ]
    unavailable_reasons = [
        str(reason)
        for trace in _result_traces(run)
        for reason in trace.get("calibration_unavailable_reasons") or []
        if str(reason)
    ]
    if not observations:
        artifact_has_evidence = calibration.status != "unavailable" and calibration.sample_count > 0
        report_status = (
            "provisional"
            if artifact_has_evidence and calibration.provisional
            else ("available" if artifact_has_evidence else "unavailable")
        )
        return {
            "artifact": calibration.to_dict(),
            "status": report_status,
            "observation_count": 0,
            "ece": {
                "status": (report_status if calibration.ece is not None else "unavailable"),
                "value": calibration.ece,
                "reason": (
                    None
                    if calibration.ece is not None
                    else "artifact and returned judged candidates expose no ECE"
                ),
            },
            "brier": {
                "status": (report_status if calibration.brier is not None else "unavailable"),
                "value": calibration.brier,
                "reason": (
                    None
                    if calibration.brier is not None
                    else "artifact and returned judged candidates expose no Brier score"
                ),
            },
            "unavailable_reasons": dict(sorted(Counter(unavailable_reasons).items())),
            "probability_claim_allowed": False,
            "interpretation": (
                "provisional empirical scores only; probability claims are forbidden"
                if report_status == "provisional"
                else (
                    "empirical calibration scores are reported without a probability claim"
                    if report_status == "available"
                    else "no probability claim; calibration evidence is unavailable"
                )
            ),
        }
    squared = [(float(item["score"]) - float(item["label"])) ** 2 for item in observations]
    bins: list[dict[str, Any]] = []
    weighted_error = 0.0
    for index in range(CALIBRATION_BIN_COUNT):
        lower = index / CALIBRATION_BIN_COUNT
        upper = (index + 1) / CALIBRATION_BIN_COUNT
        selected = [
            item
            for item in observations
            if lower <= float(item["score"]) <= upper
            and (index == CALIBRATION_BIN_COUNT - 1 or float(item["score"]) < upper)
        ]
        if not selected:
            continue
        mean_score = sum(float(item["score"]) for item in selected) / len(selected)
        empirical_rate = sum(int(item["label"]) for item in selected) / len(selected)
        absolute_gap = abs(mean_score - empirical_rate)
        weighted_error += absolute_gap * len(selected) / len(observations)
        bins.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(selected),
                "mean_score": mean_score,
                "empirical_positive_rate": empirical_rate,
                "absolute_gap": absolute_gap,
            }
        )
    provisional = (
        calibration.status == "provisional"
        or len(observations) < CALIBRATION_MIN_FINAL_OBSERVATIONS
    )
    return {
        "artifact": calibration.to_dict(),
        "status": "provisional" if provisional else "available",
        "observation_count": len(observations),
        "minimum_final_observations": CALIBRATION_MIN_FINAL_OBSERVATIONS,
        "ece": {
            "status": "provisional" if provisional else "available",
            "value": weighted_error,
            "bin_count": CALIBRATION_BIN_COUNT,
            "bins": bins,
        },
        "brier": {
            "status": "provisional" if provisional else "available",
            "value": sum(squared) / len(squared),
        },
        "unavailable_reasons": dict(sorted(Counter(unavailable_reasons).items())),
        "probability_claim_allowed": False,
        "interpretation": (
            "provisional calibrated scores only; probability claims are forbidden"
            if provisional
            else (
                "calibration metrics satisfy the fixed sample contract; "
                "scores remain empirical and are not probability claims"
            )
        ),
    }


def _timeout_report(run: dict[str, Any]) -> dict[str, Any]:
    traces = _result_traces(run)
    fallback_traces = [trace for trace in traces if trace.get("fallback_status") != "not_used"]
    timeout_traces = [trace for trace in traces if trace.get("rerank_outcome") == "timeout"]
    samples = [
        float(trace["rerank_latency_ms"])
        for trace in timeout_traces
        if isinstance(trace.get("rerank_latency_ms"), (int, float))
        and not isinstance(trace.get("rerank_latency_ms"), bool)
    ]
    invalid = [
        index
        for index, trace in enumerate(timeout_traces)
        if trace.get("fallback_status") != "succeeded"
        or trace.get("timeout_fallback_valid") is not True
    ]
    return {
        "fallback_rate": {
            "numerator": len(fallback_traces),
            "denominator": len(traces),
            "value": len(fallback_traces) / len(traces) if traces else 0.0,
        },
        "timeout_fallback_rate": {
            "numerator": len(timeout_traces),
            "denominator": len(traces),
            "value": len(timeout_traces) / len(traces) if traces else 0.0,
        },
        "timeout_count": len(timeout_traces),
        "fallback_valid_count": len(timeout_traces) - len(invalid),
        "invalid_timeout_fallback_count": len(invalid),
        "all_timeout_fallbacks_valid": not invalid,
        "timeout_p95_ms": cb2._percentile_values(samples, 0.95),
        "policy": FLAG_SNAPSHOT["timeout_fallback"],
        "timeout_budget_ms": FLAG_SNAPSHOT["rerank_timeout_ms"],
        "fallback_reasons": dict(
            sorted(
                Counter(
                    str(trace.get("fallback_reason"))
                    for trace in fallback_traces
                    if trace.get("fallback_reason")
                ).items()
            )
        ),
    }


def _contribution_report(run: dict[str, Any]) -> dict[str, Any]:
    cases = []
    totals = Counter()
    for result, trace in zip(run.get("results") or [], _result_traces(run), strict=True):
        values = {
            name: int(trace.get(name) or 0)
            for name in (
                "dense_contribution_count",
                "dense_only_contribution_count",
                "rerank_changed_count",
                "rerank_promoted_count",
                "rerank_demoted_count",
                "rerank_removed_count",
                "rerank_added_count",
            )
        }
        totals.update(values)
        if any(values.values()):
            cases.append({"case_id": result["name"], **values})
    return {
        "totals": dict(sorted(totals.items())),
        "case_count_with_contribution": len(cases),
        "cases": cases,
    }


def _explanation_report(run: dict[str, Any]) -> dict[str, Any]:
    explanations = Counter(
        str(code) for trace in _result_traces(run) for code in trace.get("explanation_codes") or []
    )
    negative_reasons = Counter(
        str(code) for trace in _result_traces(run) for code in trace.get("negative_reasons") or []
    )
    return {
        "explanation_codes": dict(sorted(explanations.items())),
        "negative_reasons": dict(sorted(negative_reasons.items())),
        "cross_encoder": CROSS_ENCODER_STATUS,
    }


def _gate(name: str, *, passed: bool, reason: str, evidence: Any) -> dict[str, Any]:
    return {
        "gate": name,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "reason": None if passed else reason,
        "evidence": evidence,
    }


def _non_regression(row: Mapping[str, Any]) -> bool:
    delta = row.get("delta")
    return (
        isinstance(delta, Mapping)
        and delta.get("status") == "available"
        and float(delta.get("directional_delta") or 0.0) >= 0.0
    )


def _acceptance(
    run: dict[str, Any],
    comparisons: dict[str, dict[str, Any]],
    publisher: cb2.ComponentDescriptor,
    hybrid: cb2.RetrieverDescriptor,
    reranker: RerankerDescriptor,
    calibration: CalibrationDescriptor,
    execution_mode: ExecutionMode,
    *,
    c3_03_gate_approved: bool,
    security_clean: bool,
) -> dict[str, Any]:
    cb0 = comparisons["cb0_qualified_baseline"]
    timeout = _timeout_report(run)
    calibration_metrics = _calibration_report(run, calibration)
    identity_ok = (
        execution_mode == "qualified"
        and c3_03_gate_approved
        and cb2._is_real_publisher(publisher)
        and cb2._is_real_retriever(hybrid)
        and _is_real_reranker(reranker)
        and _is_real_calibration(calibration)
    )
    gates = [
        _gate(
            "production_component_identity_and_c3_03_gate",
            passed=identity_ok,
            reason="qualification requires the Gate and all four public production identities",
            evidence={
                "execution_mode": execution_mode,
                "c3_03_gate_approved": c3_03_gate_approved,
                "publisher": publisher.to_dict(),
                "hybrid_retriever": hybrid.to_dict(),
                "reranker": reranker.to_dict(),
                "calibration_artifact": calibration.to_dict(),
            },
        ),
        _gate(
            "complete_33_case_coverage",
            passed=(
                int(run.get("case_count") or 0) == EXPECTED_ELIGIBLE_COUNT
                and len(run.get("results") or []) == EXPECTED_ELIGIBLE_COUNT
            ),
            reason="qualification requires all 33 released eligible results",
            evidence={
                "case_count": run.get("case_count"),
                "result_count": len(run.get("results") or []),
            },
        ),
        _gate(
            "artifact_security",
            passed=security_clean,
            reason="qualification requires a clean portable artifact security scan",
            evidence={"clean": security_clean},
        ),
    ]
    for gate_name, scope, metric_name in (
        ("cb0_exact_non_regression", "exact", "entity_recall@10"),
        ("cb0_identifier_non_regression", "identifier", "entity_recall@10"),
        ("cb0_locator_non_regression", "overall", "expected_locator_recall@10"),
    ):
        row = cb2._comparison_lookup(cb0, scope, metric_name)
        gates.append(
            _gate(
                gate_name,
                passed=_non_regression(row),
                reason="treatment regressed or the fixed C-B0 comparison is unavailable",
                evidence=row,
            )
        )
    hard_negative = cb2._comparison_lookup(
        cb0,
        "same_name_hard_negative",
        "hard_negative_error@10",
    )
    harmful = cb2._comparison_lookup(
        cb0,
        "overall",
        "harmful_candidate_rate@10",
    )
    gates.extend(
        [
            _gate(
                "hard_negative_or_harmful_non_regression",
                passed=_non_regression(hard_negative) or _non_regression(harmful),
                reason="both hard-negative and harmful-candidate rates regressed or are unavailable",
                evidence={"hard_negative": hard_negative, "harmful": harmful},
            ),
            _gate(
                "timeout_fallback_correctness",
                passed=timeout["all_timeout_fallbacks_valid"],
                reason="at least one timeout failed to preserve the hybrid ordering",
                evidence=timeout,
            ),
            _gate(
                "calibration_evidence_and_claim_honesty",
                passed=(
                    calibration_metrics["status"] in {"provisional", "available"}
                    and (
                        calibration_metrics["status"] != "provisional"
                        or calibration_metrics["probability_claim_allowed"] is False
                    )
                ),
                reason="calibration is unavailable or provisional scores claim probabilities",
                evidence=calibration_metrics,
            ),
        ]
    )
    return {
        "schema_version": "code-c-b5-acceptance-v1",
        "decision": "qualified" if all(item["passed"] for item in gates) else "not-qualified",
        "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "audit_control_run_ids": [CB1_AUDIT_RUN_ID, CB2_AUDIT_RUN_ID],
        "audit_controls_are_qualified_baselines": False,
        "gates": gates,
    }


def _metrics_report(
    run: dict[str, Any],
    package: GoldenPackage,
    cb0: cb1.CB0Anchor,
    cb1_audit: cb2.AuditAnchor,
    cb2_audit: cb2.AuditAnchor,
    publisher: cb2.ComponentDescriptor,
    hybrid: cb2.RetrieverDescriptor,
    reranker: RerankerDescriptor,
    calibration: CalibrationDescriptor,
    execution_mode: ExecutionMode,
    *,
    c3_03_gate_approved: bool,
    security_clean: bool,
) -> dict[str, Any]:
    comparisons = _comparisons(run, package, cb0, cb1_audit, cb2_audit)
    return {
        "schema_version": "code-c-b5-metrics-v1",
        "run_id": run["id"],
        "authoritative_contract": "evaluation_metric_values",
        "summary": run["summary"],
        "metric_values": run["metric_values"],
        "comparisons": comparisons,
        "calibration": _calibration_report(run, calibration),
        "timeout_fallback": _timeout_report(run),
        "contribution": _contribution_report(run),
        "explanations": _explanation_report(run),
        "acceptance": _acceptance(
            run,
            comparisons,
            publisher,
            hybrid,
            reranker,
            calibration,
            execution_mode,
            c3_03_gate_approved=c3_03_gate_approved,
            security_clean=security_clean,
        ),
    }


def _slices_report(
    run: dict[str, Any],
    package: GoldenPackage,
    cb0: cb1.CB0Anchor,
    cb1_audit: cb2.AuditAnchor,
    cb2_audit: cb2.AuditAnchor,
) -> dict[str, Any]:
    try:
        full = baseline._slices_report(run, package)
    except Exception as exc:
        raise CB5Error(f"unable to derive C-B5 slices: {exc}") from exc
    comparisons = _comparisons(run, package, cb0, cb1_audit, cb2_audit)
    return {
        **full,
        "schema_version": "code-c-b5-slices-v1",
        "required_treatment_slices": {
            role: [
                row
                for row in comparison["comparisons"]
                if row["scope"]
                in {
                    "overall",
                    "exact",
                    "identifier",
                    "low_overlap",
                    "same_name_hard_negative",
                }
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


def _error_analysis_report(
    run: dict[str, Any],
    package: GoldenPackage,
    cb0: cb1.CB0Anchor,
    cb1_audit: cb2.AuditAnchor,
    cb2_audit: cb2.AuditAnchor,
) -> dict[str, Any]:
    try:
        full = baseline._error_analysis_report(run, package)
    except Exception as exc:
        raise CB5Error(f"unable to derive C-B5 error analysis: {exc}") from exc
    comparisons = _comparisons(run, package, cb0, cb1_audit, cb2_audit)
    return {
        **full,
        "schema_version": "code-c-b5-error-analysis-v1",
        "same_name_hard_negative": {
            role: [
                row
                for row in comparison["comparisons"]
                if row["scope"] == "same_name_hard_negative"
            ]
            for role, comparison in comparisons.items()
        },
        "harmful_and_duplicates": {
            role: [
                row
                for row in comparison["comparisons"]
                if "harmful_candidate" in row["metric_name"]
                or "duplicate_rate" in row["metric_name"]
            ]
            for role, comparison in comparisons.items()
        },
        "contribution": _contribution_report(run),
        "explanations": _explanation_report(run),
        "unavailable_cases": [
            {"case_id": case.canonical_id, "reason": case.unavailable_reason}
            for case in package.ineligible_cases
        ],
    }


def _latency_report(
    run: dict[str, Any],
    package: GoldenPackage,
    cb0: cb1.CB0Anchor,
    cb1_audit: cb2.AuditAnchor,
    cb2_audit: cb2.AuditAnchor,
    materialization: dict[str, Any],
) -> dict[str, Any]:
    comparisons = _comparisons(run, package, cb0, cb1_audit, cb2_audit)
    source_times = list(
        ((materialization.get("ingest_time_ms") or {}).get("per_source") or {}).values()
    )
    index_times = list((materialization.get("dense_index_time_ms") or {}).get("samples") or [])
    return {
        "schema_version": "code-c-b5-latency-v1",
        "run_id": run["id"],
        "ingest": {
            "total_ms": (materialization.get("ingest_time_ms") or {}).get("value"),
            "p95_ms": cb2._percentile_values(source_times, 0.95),
        },
        "dense_index": {
            "total_ms": (materialization.get("dense_index_time_ms") or {}).get("total"),
            "p95_ms": cb2._percentile_values(index_times, 0.95),
        },
        "query_p95": {
            role: cb2._comparison_lookup(comparison, "overall", "latency_p95_ms")
            for role, comparison in comparisons.items()
        },
        "rerank_timeout": _timeout_report(run),
        "cases": [
            {
                "case_id": result["name"],
                "latency_ms": result["latency_ms"],
                "candidate_count": (result.get("detail") or {}).get("candidate_count"),
                "rerank_latency_ms": trace.get("rerank_latency_ms"),
                "rerank_outcome": trace.get("rerank_outcome"),
                "fallback_status": trace.get("fallback_status"),
            }
            for result, trace in zip(run.get("results") or [], _result_traces(run), strict=True)
        ],
        "compute": materialization.get("compute"),
    }


def _storage_report(
    database_path: Path,
    run_id: str,
    cb0: cb1.CB0Anchor,
    cb1_audit: cb2.AuditAnchor,
    cb2_audit: cb2.AuditAnchor,
) -> dict[str, Any]:
    report = cb2._storage_report(database_path, run_id, cb0, cb1_audit)
    cb2_storage = _read_json(cb2_audit.directory / "storage.json")
    treatment_logical = report.get("total_retrieval_index_logical_bytes")
    control_logical = cb2_storage.get("total_retrieval_index_logical_bytes")
    return {
        **report,
        "schema_version": "code-c-b5-storage-v1",
        "cb2_not_qualified_audit_comparison": {
            "run_id": CB2_AUDIT_RUN_ID,
            "is_qualified_baseline": False,
            "logical_index_bytes_delta": cb2._available_delta(
                int(treatment_logical),
                control_logical,
                "logical retrieval index bytes",
            ),
        },
    }


def _coverage_report(run: dict[str, Any], package: GoldenPackage) -> dict[str, Any]:
    report = cb2._coverage_report(run, package)
    return {
        **report,
        "schema_version": "code-c-b5-coverage-v1",
        "capability_boundary": CAPABILITY_BOUNDARY,
    }


def _report_payloads(
    run: dict[str, Any],
    package: GoldenPackage,
    database_path: Path,
    cb0: cb1.CB0Anchor,
    cb1_audit: cb2.AuditAnchor,
    cb2_audit: cb2.AuditAnchor,
    publisher: cb2.ComponentDescriptor,
    hybrid: cb2.RetrieverDescriptor,
    reranker: RerankerDescriptor,
    calibration: CalibrationDescriptor,
    execution_mode: ExecutionMode,
    *,
    c3_03_gate_approved: bool,
    security_clean: bool,
) -> dict[str, dict[str, Any]]:
    declared = (run.get("config") or {}).get("declared") or {}
    materialization = declared.get("materialization_observation")
    if not isinstance(materialization, dict):
        raise CB5Error("Run does not retain the C-B5 materialization observation")
    return {
        "materialization.json": materialization,
        "coverage.json": _coverage_report(run, package),
        "metrics.json": _metrics_report(
            run,
            package,
            cb0,
            cb1_audit,
            cb2_audit,
            publisher,
            hybrid,
            reranker,
            calibration,
            execution_mode,
            c3_03_gate_approved=c3_03_gate_approved,
            security_clean=security_clean,
        ),
        "slices.json": _slices_report(run, package, cb0, cb1_audit, cb2_audit),
        "error-analysis.json": _error_analysis_report(
            run,
            package,
            cb0,
            cb1_audit,
            cb2_audit,
        ),
        "latency.json": _latency_report(
            run,
            package,
            cb0,
            cb1_audit,
            cb2_audit,
            materialization,
        ),
        "storage.json": _storage_report(
            database_path,
            run["id"],
            cb0,
            cb1_audit,
            cb2_audit,
        ),
    }


def _validate_run(
    run: dict[str, Any],
    loaded: LoadedGolden,
    publisher: cb2.ComponentDescriptor,
    hybrid: cb2.RetrieverDescriptor,
    reranker: RerankerDescriptor,
    calibration: CalibrationDescriptor,
    database_path: Path,
) -> None:
    try:
        cb2._validate_run(run, loaded, hybrid, publisher, database_path)
    except Exception as exc:
        raise CB5Error(f"embedded AST/dense/hybrid contract failed: {exc}") from exc
    declared = (run.get("config") or {}).get("declared") or {}
    if declared.get("cb5_config_snapshot") != _descriptor_config(
        hybrid,
        publisher,
        reranker,
        calibration,
    ):
        raise CB5Error("embedded C-B5 configuration differs from component observations")
    traces = _result_traces(run)
    if len(traces) != EXPECTED_ELIGIBLE_COUNT:
        raise CB5Error("C-B5 result trace count differs from the fixed denominator")
    expected = {
        "reranker_class": reranker.class_name,
        "reranker_version": reranker.version,
        "rerank_policy": reranker.policy,
        "calibration_artifact_class": calibration.class_name,
        "calibration_version": calibration.version,
        "calibration_artifact_status": calibration.status,
        "calibration_artifact_fingerprint": calibration.artifact_fingerprint,
        "calibration_profile_version": calibration.profile_version,
        "calibration_model_version": calibration.model_version,
        "calibration_index_version": calibration.index_version,
        "calibration_reranker_version": calibration.reranker_version,
        "cross_encoder_status": "unavailable",
        "cross_encoder_execution": "not-run",
    }
    for trace in traces:
        if any(trace.get(name) != value for name, value in expected.items()):
            raise CB5Error("case result lacks the observed reranker/calibration identity")
        if (
            not isinstance(trace.get("rerank_outcome"), str)
            or trace.get("fallback_status")
            not in {
                "not_used",
                "succeeded",
                "failed",
            }
            or type(trace.get("timeout_fallback_valid")) is not bool
            or not isinstance(trace.get("explanation_codes"), list)
            or not isinstance(trace.get("negative_reasons"), list)
        ):
            raise CB5Error("case result lacks exhaustive C-B5 outcome evidence")
        if (
            trace.get("rerank_outcome") == "timeout"
            and trace.get("timeout_fallback_valid") is not True
        ):
            raise CB5Error("case result contains an invalid timeout fallback")


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
    cb1_audit: cb2.AuditAnchor,
    cb2_audit: cb2.AuditAnchor,
) -> dict[str, Any]:
    return {
        "qualified_baseline": {
            "run_id": CB0_QUALIFIED_RUN_ID,
            "artifact_directory": CB0_ARTIFACT_DIRECTORY,
            "treatment_qualified": True,
            "qualification_record_hash": cb0.verification["qualification_record_hash"],
            "manifest_canonical_hash": cb0.verification["manifest_canonical_hash"],
            "artifact_set_hash": cb0.manifest["artifact_set_hash"],
        },
        "cb1_not_qualified_audit": {
            "run_id": CB1_AUDIT_RUN_ID,
            "artifact_directory": CB1_AUDIT_ARTIFACT_DIRECTORY,
            "treatment_qualified": False,
            "qualification_use": "forbidden",
            "manifest_canonical_hash": cb1_audit.verification["manifest_canonical_hash"],
            "artifact_set_hash": cb1_audit.manifest["artifact_set_hash"],
        },
        "cb2_not_qualified_audit": {
            "run_id": CB2_AUDIT_RUN_ID,
            "artifact_directory": CB2_AUDIT_ARTIFACT_DIRECTORY,
            "treatment_qualified": False,
            "qualification_use": "forbidden",
            "manifest_canonical_hash": cb2_audit.verification["manifest_canonical_hash"],
            "artifact_set_hash": cb2_audit.manifest["artifact_set_hash"],
        },
    }


def _manifest(
    run: dict[str, Any],
    package: GoldenPackage,
    cb0: cb1.CB0Anchor,
    cb1_audit: cb2.AuditAnchor,
    cb2_audit: cb2.AuditAnchor,
    publisher: cb2.ComponentDescriptor,
    hybrid: cb2.RetrieverDescriptor,
    reranker: RerankerDescriptor,
    calibration: CalibrationDescriptor,
    artifact_files: dict[str, dict[str, Any]],
    input_fingerprint: dict[str, Any],
    execution_mode: ExecutionMode,
    acceptance: dict[str, Any],
    *,
    c3_03_gate_approved: bool,
) -> dict[str, Any]:
    qualified = (
        execution_mode == "qualified"
        and c3_03_gate_approved
        and acceptance["decision"] == "qualified"
    )
    snapshot = run["snapshot"]
    config = _descriptor_config(hybrid, publisher, reranker, calibration)
    return {
        "schema_version": CB5_SCHEMA_VERSION,
        "status": "completed",
        "execution_mode": execution_mode,
        "c3_03_gate_approved": c3_03_gate_approved,
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
        "controls": _anchor_manifest(cb0, cb1_audit, cb2_audit),
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
            "runner": CB5_RUNNER_VERSION,
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
) -> tuple[
    cb2.ComponentDescriptor,
    cb2.RetrieverDescriptor,
    RerankerDescriptor,
    CalibrationDescriptor,
]:
    config = manifest.get("config") or {}
    publisher = config.get("publisher") or {}
    hybrid = config.get("hybrid_retriever") or {}
    reranker = config.get("reranker") or {}
    calibration = config.get("calibration_artifact") or {}
    channels = hybrid.get("channels")
    if not isinstance(channels, list):
        raise CB5Error("manifest hybrid retriever channels are invalid")
    return (
        cb2.ComponentDescriptor(
            module=str(publisher.get("module") or ""),
            class_name=str(publisher.get("class") or ""),
            version=str(publisher.get("version") or ""),
        ),
        cb2.RetrieverDescriptor(
            module=str(hybrid.get("module") or ""),
            class_name=str(hybrid.get("class") or ""),
            version=str(hybrid.get("version") or ""),
            fusion=str(hybrid.get("fusion") or ""),
            channels=tuple(str(value) for value in channels),
            index_family=str(hybrid.get("index_family") or ""),
        ),
        RerankerDescriptor(
            module=str(reranker.get("module") or ""),
            class_name=str(reranker.get("class") or ""),
            version=str(reranker.get("version") or ""),
            policy=str(reranker.get("policy") or ""),
            fallback_policy=str(reranker.get("fallback_policy") or ""),
        ),
        CalibrationDescriptor(
            module=str(calibration.get("module") or ""),
            class_name=str(calibration.get("class") or ""),
            version=str(calibration.get("version") or ""),
            status=str(calibration.get("status") or ""),
            profile_version=str(calibration.get("profile_version") or ""),
            model_version=str(calibration.get("model_version") or ""),
            index_version=str(calibration.get("index_version") or ""),
            reranker_version=str(calibration.get("reranker_version") or ""),
            method=str(calibration.get("method") or ""),
            provisional=bool(calibration.get("provisional")),
            sample_count=int(calibration.get("sample_count") or 0),
            labeled_count=int(calibration.get("labeled_count") or 0),
            ece=(float(calibration["ece"]) if calibration.get("ece") is not None else None),
            brier=(float(calibration["brier"]) if calibration.get("brier") is not None else None),
            unavailable_reason=str(calibration.get("unavailable_reason") or ""),
            artifact_fingerprint=str(calibration.get("artifact_fingerprint") or ""),
        ),
    )


def _require_exact_file_set(directory: Path) -> None:
    entries = {path.name for path in directory.iterdir()}
    if entries != EXPECTED_ARTIFACT_ENTRIES:
        raise CB5Error("artifact directory contains an unexpected or missing entry")
    if any(path.is_symlink() or not path.is_file() for path in directory.iterdir()):
        raise CB5Error("artifact entries must be regular files, not symlinks")


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
        manifest.get("schema_version") != CB5_SCHEMA_VERSION
        or manifest.get("status") != "completed"
    ):
        raise CB5Error("artifact is not a completed C-B5 treatment")
    execution_mode = manifest.get("execution_mode")
    if execution_mode not in {"qualified", "test"}:
        raise CB5Error("artifact execution_mode is invalid")
    if execution_mode == "test" and not allow_test_artifact:
        raise CB5Error("test-only C-B5 artifacts require allow_test_artifact=True")

    package = validate_golden_package(resolved_root)
    cb0 = cb1._load_cb0_anchor(resolved_root)
    cb1_audit = cb2._load_cb1_audit_anchor(resolved_root)
    cb2_audit = _load_cb2_audit_anchor(resolved_root)
    if manifest.get("controls") != _anchor_manifest(cb0, cb1_audit, cb2_audit):
        raise CB5Error("manifest control anchors are invalid")
    expected_files = _artifact_file_records(directory)
    if manifest.get("artifacts") != expected_files or manifest.get(
        "artifact_set_hash"
    ) != _fingerprint(expected_files):
        raise CB5Error("artifact file-set fingerprint mismatch")

    database_path = directory / "evaluation.sqlite3"
    with sqlite3.connect(f"file:{database_path}?mode=ro", uri=True) as database:
        quick_check = str(database.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_failures = database.execute("PRAGMA foreign_key_check").fetchall()
        run_ids = [
            str(row[0])
            for row in database.execute("SELECT id FROM evaluation_runs ORDER BY id").fetchall()
        ]
    if quick_check != "ok" or foreign_key_failures or len(run_ids) != 1:
        raise CB5Error("artifact SQLite integrity or single-Run contract failed")
    if manifest.get("run_id") != run_ids[0]:
        raise CB5Error("manifest Run identity differs from embedded SQLite")

    loaded = baseline._verify_embedded_golden(database_path, package)
    store = EvaluationStore(baseline._ReadOnlySQLiteStore(database_path))
    run = store.get_run(run_ids[0])
    if run is None:
        raise CB5Error("artifact database does not contain its Run")
    publisher, hybrid, reranker, calibration = _descriptors_from_manifest(manifest)
    _validate_run(
        run,
        loaded,
        publisher,
        hybrid,
        reranker,
        calibration,
        database_path,
    )
    baseline.assert_terminal_immutability(database_path, run["id"])
    try:
        security = baseline._require_clean_security_scan(directory)
    except Exception as exc:
        raise CB5Error(f"artifact security scan failed: {exc}") from exc
    gate = manifest.get("c3_03_gate_approved")
    if type(gate) is not bool:
        raise CB5Error("manifest C3-03 Gate approval is invalid")
    reports = _report_payloads(
        run,
        package,
        database_path,
        cb0,
        cb1_audit,
        cb2_audit,
        publisher,
        hybrid,
        reranker,
        calibration,
        execution_mode,
        c3_03_gate_approved=gate,
        security_clean=True,
    )
    for name, expected in reports.items():
        if _read_json(directory / name) != expected:
            raise CB5Error(f"artifact report cannot be recomputed: {name}")
    acceptance = reports["metrics.json"]["acceptance"]
    expected_qualified = (
        execution_mode == "qualified" and gate and acceptance["decision"] == "qualified"
    )
    audit = _read_json(directory / "attempt-audit.json")
    expected_audit = {
        "schema_version": "code-c-b5-attempt-audit-v1",
        "status": "completed",
        "execution_mode": execution_mode,
        "c3_03_gate_approved": gate,
        "treatment_qualified": expected_qualified,
        "started_at": audit.get("started_at"),
        "completed_at": run["completed_at"],
        "run_id": run["id"],
        "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "audit_control_run_ids": [CB1_AUDIT_RUN_ID, CB2_AUDIT_RUN_ID],
        "audit_controls_are_qualified_baselines": False,
    }
    if audit != expected_audit or audit.get("started_at") is None:
        raise CB5Error("attempt audit identity or terminal state is invalid")
    declared = (run.get("config") or {}).get("declared") or {}
    input_fingerprint = declared.get("workspace_input_fingerprint")
    if (
        not isinstance(input_fingerprint, dict)
        or input_fingerprint.get("package_hash") != PACKAGE_HASH
        or input_fingerprint.get("cb0_qualified_run_id") != CB0_QUALIFIED_RUN_ID
        or input_fingerprint.get("cb1_audit_run_id") != CB1_AUDIT_RUN_ID
        or input_fingerprint.get("cb2_audit_run_id") != CB2_AUDIT_RUN_ID
        or input_fingerprint.get("cb1_audit_is_qualified_baseline") is not False
        or input_fingerprint.get("cb2_audit_is_qualified_baseline") is not False
        or not str(input_fingerprint.get("cb5_treatment_input_hash") or "").startswith("sha256:")
    ):
        raise CB5Error("embedded workspace input fingerprint is incomplete")
    expected_manifest = _manifest(
        run,
        package,
        cb0,
        cb1_audit,
        cb2_audit,
        publisher,
        hybrid,
        reranker,
        calibration,
        expected_files,
        input_fingerprint,
        execution_mode,
        acceptance,
        c3_03_gate_approved=gate,
    )
    if manifest != expected_manifest:
        raise CB5Error("manifest cannot be reconstructed from immutable C-B5 inputs")
    if manifest.get("treatment_qualified") is not expected_qualified:
        raise CB5Error("qualification flag differs from C-B5 acceptance and Gate state")
    if execution_mode == "qualified":
        if not (
            gate
            and cb2._is_real_publisher(publisher)
            and cb2._is_real_retriever(hybrid)
            and _is_real_reranker(reranker)
            and _is_real_calibration(calibration)
        ):
            raise CB5Error("qualified artifact lacks C3-03 Gate and production identities")
    elif manifest.get("treatment_qualified") is not False:
        raise CB5Error("test-only artifacts can never be treatment-qualified")
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
        "audit_control_run_ids": [CB1_AUDIT_RUN_ID, CB2_AUDIT_RUN_ID],
        "audit_controls_are_qualified_baselines": False,
        "artifact_set_hash": manifest["artifact_set_hash"],
        "manifest_canonical_hash": _fingerprint(manifest),
        "acceptance": acceptance,
        "security_scan": security,
    }


def _validate_execution_policy(
    *,
    resolved_root: Path,
    resolved_runs: Path,
    execution_mode: ExecutionMode,
    publisher_factory: PublisherFactory | None,
    hybrid_factory: HybridFactory | None,
    calibration_factory: CalibrationFactory | None,
    reranker_factory: RerankerFactory | None,
    calibration_artifact: Any | None,
    calibration_artifact_path: Path | None,
    c3_03_gate_approved: bool,
) -> None:
    repository_runs = (resolved_root / "evals" / "code" / "runs").resolve()
    factories = (
        publisher_factory,
        hybrid_factory,
        calibration_factory,
        reranker_factory,
    )
    if execution_mode == "test":
        if any(factory is None for factory in factories):
            raise CB5Error("test execution requires all four explicit fake factories")
        if calibration_artifact is not None or calibration_artifact_path is not None:
            raise CB5Error("test execution obtains calibration only from its fake factory")
        if resolved_runs == repository_runs or resolved_runs.is_relative_to(repository_runs):
            raise CB5Error("test execution may not write under evals/code/runs")
        if c3_03_gate_approved:
            raise CB5Error("test execution cannot claim C3-03 Gate approval")
    elif execution_mode == "qualified":
        if any(factory is not None for factory in factories):
            raise CB5Error("qualified execution forbids component factory injection")
        if not c3_03_gate_approved:
            raise CB5Error("qualified execution requires explicit C3-03 Gate approval")
        if (calibration_artifact is None) == (calibration_artifact_path is None):
            raise CB5Error(
                "qualified execution requires exactly one versioned calibration artifact input"
            )
    else:
        raise CB5Error("execution_mode must be qualified or test")


def _require_unchanged(before: dict[str, Any], after: dict[str, Any]) -> None:
    if after != before:
        raise CB5Error("workspace input fingerprint drifted during C-B5 execution")


def run_cb5(
    *,
    root: Path | None = None,
    runs_dir: Path | None = None,
    execution_mode: ExecutionMode = "qualified",
    publisher_factory: PublisherFactory | None = None,
    hybrid_factory: HybridFactory | None = None,
    calibration_factory: CalibrationFactory | None = None,
    reranker_factory: RerankerFactory | None = None,
    calibration_artifact: Any | None = None,
    calibration_artifact_path: Path | None = None,
    c3_03_gate_approved: bool = False,
) -> Path:
    resolved_root = (root or repository_root()).resolve()
    resolved_runs = (
        runs_dir.resolve()
        if runs_dir is not None
        else (resolved_root / "evals" / "code" / "runs").resolve()
    )
    _validate_execution_policy(
        resolved_root=resolved_root,
        resolved_runs=resolved_runs,
        execution_mode=execution_mode,
        publisher_factory=publisher_factory,
        hybrid_factory=hybrid_factory,
        calibration_factory=calibration_factory,
        reranker_factory=reranker_factory,
        calibration_artifact=calibration_artifact,
        calibration_artifact_path=calibration_artifact_path,
        c3_03_gate_approved=c3_03_gate_approved,
    )
    production_calibration: Any | None = None
    if execution_mode == "qualified":
        production_calibration = (
            _load_calibration_artifact(calibration_artifact_path)
            if calibration_artifact_path is not None
            else calibration_artifact
        )
        if production_calibration is None or not _is_real_calibration(
            _calibration_descriptor(production_calibration)
        ):
            raise CB5Error("qualified execution requires the public production CalibrationArtifact")
    cb0 = cb1._load_cb0_anchor(resolved_root)
    cb1_audit = cb2._load_cb1_audit_anchor(resolved_root)
    cb2_audit = _load_cb2_audit_anchor(resolved_root)
    production_before = cb1._production_database_state(
        resolved_root,
        include_hash=execution_mode == "qualified",
    )
    input_fingerprint = workspace_input_fingerprint(resolved_root, resolved_runs)
    final_dir: Path | None = None
    with tempfile.TemporaryDirectory(prefix="code-c-b5-") as temporary:
        temporary_root = Path(temporary).resolve()
        base = cb1.prepare_cb1_environment(
            temporary_root / "work",
            root=resolved_root,
        )
        exact_sparse = cb1._load_real_retriever(base.runtime)
        exact_sparse_descriptor = cb1._retriever_descriptor(exact_sparse)
        cb1._publish_real_treatment_index(base, exact_sparse_descriptor)
        profile = cb2._embedding_profile()
        publisher = (
            publisher_factory(base.runtime, profile)
            if publisher_factory is not None
            else cb2._load_real_publisher(base.runtime, profile)
        )
        prepared = cb2._publish_dense_profile(base, publisher, profile)
        hybrid = (
            hybrid_factory(base.runtime, profile)
            if hybrid_factory is not None
            else cb2._load_real_retriever(base.runtime, profile)
        )
        hybrid_descriptor = cb2._retriever_descriptor(hybrid)
        calibration = (
            calibration_factory(base.runtime)
            if calibration_factory is not None
            else production_calibration
        )
        if calibration is None:
            raise CB5Error("calibration artifact resolution returned no artifact")
        calibration_descriptor = _calibration_descriptor(calibration)
        reranker = (
            reranker_factory(base.runtime, hybrid, calibration)
            if reranker_factory is not None
            else _load_real_reranker(base.runtime, hybrid, calibration)
        )
        reranker_descriptor = _reranker_descriptor(reranker)
        if execution_mode == "qualified" and not (
            cb2._is_real_publisher(prepared.publisher)
            and cb2._is_real_retriever(hybrid_descriptor)
            and _is_real_reranker(reranker_descriptor)
            and _is_real_calibration(calibration_descriptor)
        ):
            raise CB5Error("qualified execution did not use all production C3-03 identities")
        base.runtime.platform.code = _CB5EvaluationRetriever(
            hybrid,
            reranker,
            base.runtime,
            base.package,
            hybrid_descriptor,
            prepared.publisher,
            reranker_descriptor,
            calibration,
            calibration_descriptor,
            profile,
        )
        config_snapshot = _descriptor_config(
            hybrid_descriptor,
            prepared.publisher,
            reranker_descriptor,
            calibration_descriptor,
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
                "cb2_config_snapshot": cb2._descriptor_config(
                    hybrid_descriptor,
                    prepared.publisher,
                ),
                "cb5_config_snapshot": config_snapshot,
                "workspace_input_fingerprint": input_fingerprint,
                "materialization_observation": _materialization_observation(
                    prepared,
                    reranker_descriptor,
                    calibration_descriptor,
                ),
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
            prepared.publisher,
            hybrid_descriptor,
            reranker_descriptor,
            calibration_descriptor,
            base.database_path,
        )
        baseline.assert_terminal_immutability(base.database_path, run["id"])
        _require_unchanged(
            input_fingerprint,
            workspace_input_fingerprint(resolved_root, resolved_runs),
        )
        if (
            cb1._production_database_state(
                resolved_root,
                include_hash=execution_mode == "qualified",
            )
            != production_before
        ):
            raise CB5Error("formal var/evidence-rag.sqlite3 changed during isolated C-B5")

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
            raise CB5Error(f"unable to sanitize portable C-B5 SQLite: {exc}") from exc
        persisted_store = EvaluationStore(baseline._ReadOnlySQLiteStore(database_artifact))
        persisted_run = persisted_store.get_run(run["id"])
        if persisted_run is None:
            raise CB5Error("portable database lost the completed C-B5 Run")
        _validate_run(
            persisted_run,
            base.loaded,
            prepared.publisher,
            hybrid_descriptor,
            reranker_descriptor,
            calibration_descriptor,
            database_artifact,
        )
        baseline.assert_terminal_immutability(database_artifact, run["id"])
        reports = _report_payloads(
            persisted_run,
            base.package,
            database_artifact,
            cb0,
            cb1_audit,
            cb2_audit,
            prepared.publisher,
            hybrid_descriptor,
            reranker_descriptor,
            calibration_descriptor,
            execution_mode,
            c3_03_gate_approved=c3_03_gate_approved,
            security_clean=True,
        )
        for name, payload in reports.items():
            _write_json(artifact_dir / name, payload)
        acceptance = reports["metrics.json"]["acceptance"]
        qualified = (
            execution_mode == "qualified"
            and c3_03_gate_approved
            and acceptance["decision"] == "qualified"
        )
        _write_json(
            artifact_dir / "attempt-audit.json",
            {
                "schema_version": "code-c-b5-attempt-audit-v1",
                "status": "completed",
                "execution_mode": execution_mode,
                "c3_03_gate_approved": c3_03_gate_approved,
                "treatment_qualified": qualified,
                "started_at": persisted_run["started_at"],
                "completed_at": persisted_run["completed_at"],
                "run_id": persisted_run["id"],
                "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
                "audit_control_run_ids": [CB1_AUDIT_RUN_ID, CB2_AUDIT_RUN_ID],
                "audit_controls_are_qualified_baselines": False,
            },
        )
        try:
            baseline._require_clean_security_scan(artifact_dir)
        except Exception as exc:
            raise CB5Error(f"C-B5 artifact security scan failed: {exc}") from exc
        artifact_files = _artifact_file_records(artifact_dir)
        manifest = _manifest(
            persisted_run,
            base.package,
            cb0,
            cb1_audit,
            cb2_audit,
            prepared.publisher,
            hybrid_descriptor,
            reranker_descriptor,
            calibration_descriptor,
            artifact_files,
            input_fingerprint,
            execution_mode,
            acceptance,
            c3_03_gate_approved=c3_03_gate_approved,
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
                include_hash=execution_mode == "qualified",
            )
            != production_before
        ):
            raise CB5Error("formal database changed during artifact verification")
        resolved_runs.mkdir(parents=True, exist_ok=True)
        final_dir = resolved_runs / token
        if final_dir.exists():
            raise CB5Error("artifact directory for the completed C-B5 Run already exists")
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
            include_hash=execution_mode == "qualified",
        )
        != production_before
    ):
        raise CB5Error("formal database changed during final verify-only check")
    return final_dir


def _resolve_artifact(value: str, runs_dir: Path) -> Path:
    candidate = Path(value)
    if candidate.is_dir():
        return candidate.resolve()
    nested = runs_dir / value
    if nested.is_dir():
        return nested.resolve()
    raise CB5Error(f"C-B5 artifact not found: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Code C-B5 deterministic reranker/calibration treatment"
    )
    parser.add_argument("--repository-root", type=Path, default=repository_root())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "prepare",
        help="validate fixed anchors and print PREPARED without creating an artifact",
    )
    run_parser = subparsers.add_parser(
        "run",
        help="run public production components after the C3-03 Gate",
    )
    run_parser.add_argument("--runs-dir", type=Path)
    run_parser.add_argument("--calibration-artifact", type=Path, required=True)
    run_parser.add_argument("--c3-03-gate-approved", action="store_true")
    verify_parser = subparsers.add_parser(
        "verify",
        help="verify an existing production C-B5 artifact",
    )
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()
    try:
        if args.command == "prepare":
            output = preparation_status(root=root)
        elif args.command == "run":
            artifact = run_cb5(
                root=root,
                runs_dir=args.runs_dir,
                calibration_artifact_path=args.calibration_artifact,
                c3_03_gate_approved=args.c3_03_gate_approved,
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
