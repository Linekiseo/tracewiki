"""Production-only Code C-B3 typed-graph evaluation harness."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from ..models import EvidenceSearchRequest
from ..runtime import Runtime
from . import baseline, cb1, cb2, cb5
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
    repository_root,
    validate_golden_package,
)
from .models import CodeEvaluationRunRequest
from .store import EvaluationStore

CB3_SCHEMA_VERSION = "code-c-b3-typed-graph-treatment-artifact-v1"
CB3_RUNNER_VERSION = "code-c-b3-runner-v1"
CB3_PREPARATION_VERSION = "code-c-b3-prepared-v1"
C4_GATE_RELATIVE_PATH = "docs/rag-optimization/development/reviews/05_CODE_C4_GATE_REVIEW.md"
C4_GATE_DECISION = "C-B3 PRODUCTION EXECUTION AUTHORIZED"
C4_AUTHORIZATION_SCHEMA_VERSION = "code-c-b3-c4-02-authorization-v1"
C4_AUTHORIZATION_VERSION = "C4-02"
C4_AUTHORIZATION_EVIDENCE = {
    "review": "C4-02 第二轮极简复审",
    "gate_result": "C4-02 PASS",
    "findings": "P0/P1 findings: 0",
    "authorization": C4_GATE_DECISION,
}

CB0_QUALIFIED_RUN_ID = cb1.CB0_QUALIFIED_RUN_ID
CB0_ARTIFACT_DIRECTORY = cb1.CB0_ARTIFACT_DIRECTORY
CB1_AUDIT_RUN_ID = cb2.CB1_AUDIT_RUN_ID
CB1_AUDIT_ARTIFACT_DIRECTORY = cb2.CB1_AUDIT_ARTIFACT_DIRECTORY
CB2_AUDIT_RUN_ID = cb5.CB2_AUDIT_RUN_ID
CB2_AUDIT_ARTIFACT_DIRECTORY = cb5.CB2_AUDIT_ARTIFACT_DIRECTORY
CB5_AUDIT_RUN_ID = "evaluation-run://project-code-golden-v2/baf5b9eb49464ca79920030fd1c772de"
CB5_AUDIT_ARTIFACT_DIRECTORY = "baf5b9eb49464ca79920030fd1c772de"

PUBLIC_CODE_MODULE = "evidence_rag.rag.sources.code"
REAL_TYPED_GRAPH_CLASS = "CodeTypedGraphRetriever"
REAL_HYBRID_CLASS = cb2.REAL_RETRIEVER_CLASS
REAL_RERANKER_CLASS = cb5.REAL_RERANKER_CLASS

TOP_K = 20
TREATMENT_ORDER = (
    "graph_off",
    "graph_post_only",
    "typed_graph",
    "graph_reranker",
)
TREATMENT_MATRIX: dict[str, dict[str, Any]] = {
    "graph_off": {
        "label": "graph off",
        "graph_traversal": False,
        "graph_enters_candidates": False,
        "graph_enters_context_only": False,
        "reranker": False,
        "paired_graph_off": None,
    },
    "graph_post_only": {
        "label": "graph post-only",
        "graph_traversal": True,
        "graph_enters_candidates": False,
        "graph_enters_context_only": True,
        "reranker": False,
        "paired_graph_off": "graph_off",
    },
    "typed_graph": {
        "label": "typed graph",
        "graph_traversal": True,
        "graph_enters_candidates": True,
        "graph_enters_context_only": False,
        "reranker": False,
        "paired_graph_off": "graph_off",
    },
    "graph_reranker": {
        "label": "graph + reranker",
        "graph_traversal": True,
        "graph_enters_candidates": True,
        "graph_enters_context_only": False,
        "reranker": True,
        "paired_graph_off": "graph_off",
    },
}
GRAPH_POLICY = {
    "seed_candidates": 10,
    "beam_width": 20,
    "hop_decay": 0.72,
    "max_hops": 3,
    "min_confidence": 0.0,
    "graph_candidates": 30,
    "node_budget": 200,
    "edge_budget": 400,
    "cycle_policy": "path-local-entity-id-v1",
    "version_policy": "exact-or-explicit-transition-v1",
    "acl_policy": "authorize-both-edge-endpoints-and-hidden-middle-nodes-v1",
    "edge_type_policy": "registered-task-whitelist-v1",
}
GRAPH_PUBLICATION_VERSION = "code-ast-v2-typed-graph-publication-v1"
PRODUCTION_IDENTITY = {
    "public_module": PUBLIC_CODE_MODULE,
    "typed_graph_retriever_class": REAL_TYPED_GRAPH_CLASS,
    "traversal_request_class": "GraphTraversalRequest",
    "traversal_result_class": "GraphTraversalResult",
    "traversal_trace_class": "GraphTraversalTrace",
    "seed_retriever_class": REAL_HYBRID_CLASS,
    "optional_final_reranker_class": REAL_RERANKER_CLASS,
    "index_family": "ast-v2",
    "required_search_method": "search",
    "required_trace_method": "search_with_trace",
}

METRIC_CONTRACT: dict[str, dict[str, Any]] = {
    "required_path_recall": {
        "direction": "higher",
        "evidence": "typed required paths and returned typed directed edges",
    },
    "required_path_precision": {
        "direction": "higher",
        "evidence": "matched required edges / all returned graph edges",
    },
    "graph_only_recovery@10": {
        "direction": "higher",
        "evidence": "same-snapshot paired graph-on minus graph-off relevant targets",
    },
    "graph_noise_rate@10": {
        "direction": "lower",
        "evidence": "non-relevant graph-exclusive candidates / graph-exclusive candidates",
    },
    "graph_harmful_candidate_rate@10": {
        "direction": "lower",
        "evidence": "harmful graph-attributed candidates / graph-attributed candidates",
    },
    "accepted_path_length": {
        "direction": "descriptive",
        "evidence": "edge count for every accepted production path",
    },
    "accepted_path_type_accuracy": {
        "direction": "higher",
        "evidence": "accepted edges matching required registered edge types",
    },
    "accepted_path_direction_accuracy": {
        "direction": "higher",
        "evidence": "accepted edges matching required traversal directions",
    },
    "graph_pruned_type": {
        "direction": "descriptive",
        "evidence": "production traversal trace",
    },
    "graph_pruned_direction": {
        "direction": "descriptive",
        "evidence": "production traversal trace",
    },
    "graph_pruned_confidence": {
        "direction": "descriptive",
        "evidence": "production traversal trace and adjacency-provider predicate",
    },
    "graph_pruned_budget": {
        "direction": "descriptive",
        "evidence": "production traversal trace",
    },
    "graph_pruned_cycle": {
        "direction": "descriptive",
        "evidence": "production traversal trace",
    },
    "graph_pruned_acl": {
        "direction": "lower",
        "evidence": "production traversal trace; leakage must remain zero",
    },
    "graph_pruned_version": {
        "direction": "descriptive",
        "evidence": "production traversal trace",
    },
    "graph_pruned_deadline": {
        "direction": "lower",
        "evidence": "production traversal trace",
    },
    "graph_expanded_nodes": {
        "direction": "descriptive",
        "evidence": "production traversal trace",
    },
    "graph_expanded_edges": {
        "direction": "descriptive",
        "evidence": "production traversal trace",
    },
    "latency_ms_p95": {
        "direction": "lower",
        "evidence": "all 33 measured per-case latencies for each arm",
    },
    "graph_latency_ms_p95": {
        "direction": "lower",
        "evidence": "all graph traversal latency samples for each graph arm",
    },
    "expected_locator_recall@10": {
        "direction": "higher",
        "evidence": "Evaluation V2 locator annotations",
    },
    "entity_recall@10": {
        "direction": "higher",
        "evidence": "Evaluation V2 entity annotations",
    },
    "mrr@10": {
        "direction": "higher",
        "evidence": "Evaluation V2 graded judgments",
    },
    "ndcg@10": {
        "direction": "higher",
        "evidence": "Evaluation V2 graded judgments",
    },
    "harmful_candidate_rate@10": {
        "direction": "lower",
        "evidence": "Evaluation V2 harmful judgments",
    },
    "unauthorized_candidate_rate": {
        "direction": "lower",
        "evidence": "caller ACL evaluated at every returned endpoint",
    },
    "wrong_version_rate": {
        "direction": "lower",
        "evidence": "Evaluation V2 required version annotations",
    },
}
REQUIRED_REPORTING = tuple(METRIC_CONTRACT)

REPORT_FILES = (
    "treatments.json",
    "coverage.json",
    "metrics.json",
    "graph-analysis.json",
    "latency.json",
    "comparisons.json",
    "failure-slices.json",
    "security.json",
)
ARTIFACT_FILES = ("attempt-audit.json", "evaluation.sqlite3", *REPORT_FILES)
EXPECTED_ARTIFACT_ENTRIES = frozenset(("manifest.json", *ARTIFACT_FILES))

ExecutionMode = Literal["qualified"]
TypedGraphFactory = Callable[..., Any]

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TEMPORARY_PATH_RE = re.compile(
    r"(?:/private)?/(?:tmp|var/folders)/|"
    r"(?:^|[\"'\s])(?:file://)?[A-Za-z]:[\\/](?:Temp|Users)[\\/]",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{8,}"
)


class CB3Error(RuntimeError):
    """The prepared C-B3 boundary failed closed."""


@runtime_checkable
class TypedGraphRetrieverProtocol(Protocol):
    """Narrow structural seam for the in-progress production retriever.

    The concrete constructor and request types intentionally remain outside
    this module until C4-02 freezes them.  The final production class must be
    the exact class exported by :data:`PUBLIC_CODE_MODULE`, not a substitute.
    """

    def search(self, request: Any) -> Any:
        """Return candidates, typed paths/edges, and a bounded traversal trace."""

    def search_with_trace(self, request: Any) -> Any:
        """Return the production GraphTraversalResult for metric evidence."""


@dataclass(frozen=True, slots=True)
class AuditAnchor:
    run_id: str
    directory: Path
    manifest: dict[str, Any]
    verification: dict[str, Any]
    run: dict[str, Any]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CB3Error(f"unable to read artifact JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise CB3Error(f"artifact JSON must be an object: {path.name}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(_pretty_json_bytes(value))


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _validate_gate_authorization(root: Path) -> dict[str, Any]:
    path = (root / C4_GATE_RELATIVE_PATH).resolve()
    if not path.is_file() or path.is_symlink():
        raise CB3Error("C4-02 Gate authorization report is missing")
    text = path.read_text(encoding="utf-8")
    if any(value not in text for value in C4_AUTHORIZATION_EVIDENCE.values()):
        raise CB3Error("C4-02 Gate does not explicitly authorize C-B3 production execution")
    component = _production_component_contract()
    if component.get("status") != "ready":
        raise CB3Error("C4-02 Gate authorization lacks the production graph identity")
    public = importlib.import_module(PUBLIC_CODE_MODULE)
    graph_class = getattr(public, REAL_TYPED_GRAPH_CLASS, None)
    if not isinstance(graph_class, type):
        raise CB3Error("C4-02 Gate authorization lacks the exact public graph class")
    component_identity = {
        "public_module": PUBLIC_CODE_MODULE,
        "implementation_module": graph_class.__module__,
        "class": graph_class.__name__,
        "retriever_version": str(getattr(graph_class, "retriever_version", "")),
    }
    if (
        component_identity["class"] != REAL_TYPED_GRAPH_CLASS
        or not component_identity["retriever_version"]
    ):
        raise CB3Error("C4-02 Gate authorization production identity is invalid")
    evidence_hash = _fingerprint(
        {
            "evidence": C4_AUTHORIZATION_EVIDENCE,
            "component_identity": component_identity,
        }
    )
    authorization = {
        "schema_version": C4_AUTHORIZATION_SCHEMA_VERSION,
        "authorization_version": C4_AUTHORIZATION_VERSION,
        "decision": C4_GATE_DECISION,
        "status": "authorized",
        "evidence": C4_AUTHORIZATION_EVIDENCE,
        "evidence_hash": evidence_hash,
        "component_identity": component_identity,
    }
    return {
        **authorization,
        "snapshot_hash": _fingerprint(authorization),
        "report": C4_GATE_RELATIVE_PATH,
        "report_sha256": _sha256_file(path),
    }


def _validate_production_identity(
    production: Mapping[str, Any],
    treatment_identity: Mapping[str, Any],
) -> dict[str, Any]:
    if any(production.get(key) != value for key, value in PRODUCTION_IDENTITY.items()):
        raise CB3Error("artifact production identity contract drifted")
    observed_graph = production.get("observed_graph") or {}
    observed_hybrid = production.get("observed_hybrid") or {}
    observed_publisher = production.get("observed_dense_publisher") or {}
    observed_reranker = production.get("observed_reranker") or {}
    if (
        observed_graph.get("public_module") != PUBLIC_CODE_MODULE
        or observed_graph.get("class") != REAL_TYPED_GRAPH_CLASS
        or not str(observed_graph.get("implementation_module") or "").startswith(
            f"{PUBLIC_CODE_MODULE}."
        )
        or not str(observed_graph.get("retriever_version") or "")
        or observed_hybrid.get("class") != REAL_HYBRID_CLASS
        or observed_hybrid.get("index_family") != "ast-v2"
        or observed_hybrid.get("channels") != ["exact", "sparse", "dense"]
        or observed_publisher.get("class") != cb2.REAL_PUBLISHER_CLASS
        or observed_reranker.get("class") != REAL_RERANKER_CLASS
    ):
        raise CB3Error("artifact observed production identity is invalid")
    expected_treatment = {
        "hybrid": observed_hybrid,
        "dense_publisher": observed_publisher,
        "graph": observed_graph,
        "reranker": observed_reranker,
    }
    if dict(treatment_identity) != expected_treatment:
        raise CB3Error("artifact treatment and manifest production identities differ")
    return {
        "graph": dict(observed_graph),
        "hybrid": dict(observed_hybrid),
        "dense_publisher": dict(observed_publisher),
        "reranker": dict(observed_reranker),
    }


def _validate_authorization_snapshot(
    snapshot: Mapping[str, Any],
    production: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        snapshot.get("schema_version") != C4_AUTHORIZATION_SCHEMA_VERSION
        or snapshot.get("authorization_version") != C4_AUTHORIZATION_VERSION
        or snapshot.get("decision") != C4_GATE_DECISION
        or snapshot.get("status") != "authorized"
        or snapshot.get("evidence") != C4_AUTHORIZATION_EVIDENCE
    ):
        raise CB3Error("artifact authorization snapshot is not an approved C4-02 decision")
    component_identity = snapshot.get("component_identity") or {}
    observed_graph = production.get("observed_graph") or {}
    if (
        component_identity.get("public_module") != PUBLIC_CODE_MODULE
        or component_identity.get("class") != REAL_TYPED_GRAPH_CLASS
        or component_identity.get("implementation_module")
        != observed_graph.get("implementation_module")
        or component_identity.get("retriever_version") != observed_graph.get("retriever_version")
    ):
        raise CB3Error("artifact authorization component identity is invalid")
    evidence_hash = _fingerprint(
        {
            "evidence": C4_AUTHORIZATION_EVIDENCE,
            "component_identity": component_identity,
        }
    )
    if snapshot.get("evidence_hash") != evidence_hash:
        raise CB3Error("artifact authorization evidence hash is invalid")
    signed = {
        "schema_version": snapshot["schema_version"],
        "authorization_version": snapshot["authorization_version"],
        "decision": snapshot["decision"],
        "status": snapshot["status"],
        "evidence": snapshot["evidence"],
        "evidence_hash": snapshot["evidence_hash"],
        "component_identity": component_identity,
    }
    if snapshot.get("snapshot_hash") != _fingerprint(signed):
        raise CB3Error("artifact authorization snapshot hash is invalid")
    report_hash = snapshot.get("report_sha256")
    if snapshot.get("report") != C4_GATE_RELATIVE_PATH or not (
        isinstance(report_hash, str) and _SHA256_RE.fullmatch(report_hash)
    ):
        raise CB3Error("artifact authorization audit metadata is invalid")
    return {
        "mode": "normalized-v1",
        "decision": C4_GATE_DECISION,
        "snapshot_hash": snapshot["snapshot_hash"],
        "evidence_hash": evidence_hash,
    }


def _validate_artifact_authorization(
    manifest: Mapping[str, Any],
    attempt: Mapping[str, Any],
    treatment_identity: Mapping[str, Any],
) -> dict[str, Any]:
    production = manifest.get("production_identity") or {}
    identities = _validate_production_identity(production, treatment_identity)
    snapshot = manifest.get("gate_authorization") or {}
    if manifest.get("c4_02_gate_approved") is not True:
        raise CB3Error("artifact does not claim the required C4-02 approval")
    if snapshot.get("schema_version") == C4_AUTHORIZATION_SCHEMA_VERSION:
        result = _validate_authorization_snapshot(snapshot, production)
    else:
        if set(snapshot) != {"decision", "status", "report", "report_sha256"}:
            raise CB3Error("legacy artifact authorization fields are incomplete")
        report_hash = snapshot.get("report_sha256")
        if (
            snapshot.get("decision") != C4_GATE_DECISION
            or snapshot.get("status") != "authorized"
            or snapshot.get("report") != C4_GATE_RELATIVE_PATH
            or not isinstance(report_hash, str)
            or not _SHA256_RE.fullmatch(report_hash)
        ):
            raise CB3Error("legacy artifact authorization is not an approved C4-02 decision")
        result = {
            "mode": "strict-legacy-v1",
            "decision": C4_GATE_DECISION,
            "snapshot_hash": None,
            "evidence_hash": report_hash,
        }
    gate_records = [
        item
        for item in (manifest.get("acceptance") or {}).get("gates") or []
        if item.get("gate") == "c4_02_gate_authorized"
    ]
    if (
        len(gate_records) != 1
        or gate_records[0].get("passed") is not True
        or gate_records[0].get("status") != "passed"
        or gate_records[0].get("evidence") != snapshot
    ):
        raise CB3Error("artifact authorization lacks its passed acceptance evidence")
    if (
        attempt.get("c4_02_gate_approved") is not True
        or attempt.get("execution_mode") != "qualified"
        or attempt.get("gate_authorization") != snapshot
    ):
        raise CB3Error("artifact attempt audit does not bind the authorization snapshot")
    return {**result, "production_identity": identities}


def _tree_state(path: Path) -> tuple[tuple[str, int, int], ...]:
    if not path.exists():
        return ()
    return tuple(
        (
            item.relative_to(path).as_posix(),
            item.stat().st_size,
            item.stat().st_mtime_ns,
        )
        for item in sorted(path.rglob("*"))
        if item.is_file()
    )


def _formal_database_state(root: Path) -> dict[str, Any]:
    database = (root / "var" / "evidence-rag.sqlite3").resolve()
    sidecars = tuple(
        {
            "name": suffix,
            "exists": Path(f"{database}{suffix}").exists(),
        }
        for suffix in ("-wal", "-shm")
    )
    if not database.exists():
        return {
            "exists": False,
            "size": None,
            "mtime_ns": None,
            "sha256": None,
            "sidecars": sidecars,
        }
    stat = database.stat()
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256_file(database),
        "sidecars": sidecars,
    }


def _require_repository_unchanged(
    *,
    database_before: Mapping[str, Any],
    database_after: Mapping[str, Any],
    runs_before: Sequence[tuple[str, int, int]],
    runs_after: Sequence[tuple[str, int, int]],
) -> None:
    if dict(database_after) != dict(database_before):
        raise CB3Error("formal var/evidence-rag.sqlite3 state changed during C-B3 read-only work")
    if tuple(runs_after) != tuple(runs_before):
        raise CB3Error("evals/code/runs changed during C-B3 read-only work")


def _load_cb5_audit_anchor(root: Path, package: GoldenPackage) -> AuditAnchor:
    directory = (root / "evals" / "code" / "runs" / CB5_AUDIT_ARTIFACT_DIRECTORY).resolve()
    if not directory.is_dir():
        raise CB3Error(f"C-B5 audit artifact is missing: {CB5_AUDIT_ARTIFACT_DIRECTORY}")
    try:
        verification = cb5.verify_artifact(directory, root=root)
    except Exception as exc:
        raise CB3Error(f"C-B5 audit verification failed: {exc}") from exc
    manifest = _read_json(directory / "manifest.json")
    if (
        verification.get("run_id") != CB5_AUDIT_RUN_ID
        or manifest.get("run_id") != CB5_AUDIT_RUN_ID
        or manifest.get("execution_mode") != "qualified"
        or manifest.get("treatment_qualified") is not False
        or (manifest.get("acceptance") or {}).get("decision") != "not-qualified"
        or (manifest.get("dataset") or {}).get("package_hash") != PACKAGE_HASH
        or (manifest.get("dataset") or {}).get("case_membership_hash") != package.membership_hash
        or (manifest.get("dataset") or {}).get("eligible_cases") != EXPECTED_ELIGIBLE_COUNT
    ):
        raise CB3Error("C-B5 artifact does not match the fixed NOT QUALIFIED audit anchor")
    run = EvaluationStore(baseline._ReadOnlySQLiteStore(directory / "evaluation.sqlite3")).get_run(
        CB5_AUDIT_RUN_ID
    )
    if run is None:
        raise CB3Error("C-B5 audit SQLite does not contain the anchored Run")
    return AuditAnchor(
        run_id=CB5_AUDIT_RUN_ID,
        directory=directory,
        manifest=manifest,
        verification=verification,
        run=run,
    )


def _production_component_contract() -> dict[str, Any]:
    """Observe the public class without importing it when this module loads."""

    try:
        public = importlib.import_module(PUBLIC_CODE_MODULE)
    except Exception as exc:
        return {
            "status": "pending",
            "module": PUBLIC_CODE_MODULE,
            "class": REAL_TYPED_GRAPH_CLASS,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    interfaces = {
        name: isinstance(getattr(public, name, None), type)
        for name in (
            "GraphTraversalRequest",
            "GraphTraversalResult",
            "GraphTraversalTrace",
        )
    }
    observed_candidates = [
        name
        for name in ("CodeGraphRetriever", "CodeGraphV2Retriever")
        if isinstance(getattr(public, name, None), type)
    ]
    component = getattr(public, REAL_TYPED_GRAPH_CLASS, None)
    if not isinstance(component, type):
        return {
            "status": "pending",
            "module": PUBLIC_CODE_MODULE,
            "class": REAL_TYPED_GRAPH_CLASS,
            "interfaces": interfaces,
            "observed_in_progress_classes": observed_candidates,
            "reason": "public production class is not exported yet",
        }
    if not callable(getattr(component, "search", None)) or not callable(
        getattr(component, "search_with_trace", None)
    ):
        return {
            "status": "pending",
            "module": PUBLIC_CODE_MODULE,
            "class": REAL_TYPED_GRAPH_CLASS,
            "implementation_module": component.__module__,
            "interfaces": interfaces,
            "observed_in_progress_classes": observed_candidates,
            "reason": "public production class lacks search/search_with_trace",
        }
    return {
        "status": "ready",
        "module": PUBLIC_CODE_MODULE,
        "class": REAL_TYPED_GRAPH_CLASS,
        "implementation_module": component.__module__,
        "interfaces": interfaces,
        "observed_in_progress_classes": observed_candidates,
        "reason": None,
    }


def require_production_retriever_identity(retriever: Any) -> dict[str, str]:
    """Reject fakes, wrappers, subclasses, and private lookalikes."""

    try:
        public = importlib.import_module(PUBLIC_CODE_MODULE)
    except Exception as exc:
        raise CB3Error("production Code module is unavailable") from exc
    expected = getattr(public, REAL_TYPED_GRAPH_CLASS, None)
    if not isinstance(expected, type):
        raise CB3Error("public production CodeTypedGraphRetriever is unavailable")
    if type(retriever) is not expected:
        raise CB3Error("fake, injected, wrapped, or subclassed graph retriever is forbidden")
    if not isinstance(retriever, TypedGraphRetrieverProtocol):
        raise CB3Error("production CodeTypedGraphRetriever does not satisfy the search protocol")
    version = getattr(retriever, "retriever_version", None)
    if not isinstance(version, str) or not version:
        raise CB3Error("production CodeTypedGraphRetriever must expose retriever_version")
    return {
        "public_module": PUBLIC_CODE_MODULE,
        "implementation_module": type(retriever).__module__,
        "class": type(retriever).__name__,
        "retriever_version": version,
    }


def _canonical_case_ids(package: GoldenPackage) -> tuple[str, ...]:
    case_ids = tuple(sorted(case.canonical_id for case in package.eligible_cases))
    if len(case_ids) != EXPECTED_ELIGIBLE_COUNT or len(set(case_ids)) != len(case_ids):
        raise CB3Error("released Golden v2 eligible membership is not exactly 33 unique cases")
    return case_ids


def require_same_33_case_denominator(
    case_ids_by_treatment: Mapping[str, Sequence[str]],
    *,
    expected_case_ids: Sequence[str],
) -> dict[str, Any]:
    """Require all four arms to use exactly the same released 33 cases."""

    if set(case_ids_by_treatment) != set(TREATMENT_ORDER):
        raise CB3Error("C-B3 denominator must contain exactly the four frozen treatments")
    expected = tuple(sorted(str(item) for item in expected_case_ids))
    if len(expected) != EXPECTED_ELIGIBLE_COUNT or len(set(expected)) != len(expected):
        raise CB3Error("expected C-B3 denominator must contain 33 unique cases")
    for treatment in TREATMENT_ORDER:
        observed = tuple(str(item) for item in case_ids_by_treatment[treatment])
        if len(observed) != EXPECTED_ELIGIBLE_COUNT or len(set(observed)) != len(observed):
            raise CB3Error(f"{treatment} does not contain 33 unique cases")
        if tuple(sorted(observed)) != expected:
            raise CB3Error(f"{treatment} does not use the fixed shared 33-case membership")
    membership_hash = _fingerprint(list(expected))
    return {
        "case_count": EXPECTED_ELIGIBLE_COUNT,
        "canonical_case_ids": list(expected),
        "case_membership_hash": membership_hash,
        "treatments": {
            treatment: {
                "case_count": EXPECTED_ELIGIBLE_COUNT,
                "case_membership_hash": membership_hash,
            }
            for treatment in TREATMENT_ORDER
        },
    }


def same_denominator_delta(
    treatment: Mapping[str, Any],
    control: Mapping[str, Any],
    *,
    direction: Literal["higher", "lower"],
) -> dict[str, Any]:
    """Compute a delta only from real, same-membership, same-denominator metrics."""

    required = (
        "status",
        "value",
        "denominator",
        "membership_case_count",
        "case_membership_hash",
    )
    if any(key not in treatment or key not in control for key in required):
        raise CB3Error("delta inputs lack explicit value and denominator evidence")
    if treatment["status"] != "available" or control["status"] != "available":
        raise CB3Error("unavailable metrics cannot produce a C-B3 delta")
    if (
        treatment["membership_case_count"] != EXPECTED_ELIGIBLE_COUNT
        or control["membership_case_count"] != EXPECTED_ELIGIBLE_COUNT
        or treatment["case_membership_hash"] != control["case_membership_hash"]
    ):
        raise CB3Error("delta inputs do not share the fixed 33-case membership")
    if float(treatment["denominator"]) != float(control["denominator"]):
        raise CB3Error("delta inputs do not share the same metric denominator")
    raw = float(treatment["value"]) - float(control["value"])
    return {
        "status": "available",
        "treatment_minus_control": raw,
        "directional_delta": raw if direction == "higher" else -raw,
        "direction": direction,
        "denominator": float(treatment["denominator"]),
        "membership_case_count": EXPECTED_ELIGIBLE_COUNT,
        "case_membership_hash": treatment["case_membership_hash"],
    }


def _anchor_summary(
    *,
    run_id: str,
    verification: Mapping[str, Any],
    qualified: bool,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "treatment_qualified": qualified,
        "qualification_use": "required" if qualified else "forbidden",
        "manifest_canonical_hash": verification.get("manifest_canonical_hash"),
    }


def preparation_status(*, root: Path | None = None) -> dict[str, Any]:
    """Validate frozen inputs and report PREPARED without writing any state."""

    resolved_root = (root or repository_root()).resolve()
    runs_dir = (resolved_root / "evals" / "code" / "runs").resolve()
    database_before = _formal_database_state(resolved_root)
    runs_before = _tree_state(runs_dir)

    package = validate_golden_package(resolved_root)
    try:
        cb0 = cb1._load_cb0_anchor(resolved_root)
        cb1_audit = cb2._load_cb1_audit_anchor(resolved_root)
        cb2_audit = cb5._load_cb2_audit_anchor(resolved_root)
    except Exception as exc:
        raise CB3Error(f"unable to validate fixed C-B0/C-B1/C-B2 anchors: {exc}") from exc
    cb5_audit = _load_cb5_audit_anchor(resolved_root, package)
    case_ids = _canonical_case_ids(package)
    denominator = require_same_33_case_denominator(
        {name: case_ids for name in TREATMENT_ORDER},
        expected_case_ids=case_ids,
    )
    component = _production_component_contract()
    gate = _validate_gate_authorization(resolved_root)

    database_after = _formal_database_state(resolved_root)
    runs_after = _tree_state(runs_dir)
    _require_repository_unchanged(
        database_before=database_before,
        database_after=database_after,
        runs_before=runs_before,
        runs_after=runs_after,
    )

    return {
        "schema_version": CB3_PREPARATION_VERSION,
        "status": "PREPARED",
        "qualification_status": "NON-QUALIFIED",
        "execution_status": (
            "authorized-production-ready"
            if component["status"] == "ready"
            else "authorized-production-component-pending"
        ),
        "treatment_qualified": False,
        "run_created": False,
        "artifact_created": False,
        "metrics_created": False,
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
            "qualified_baseline": _anchor_summary(
                run_id=CB0_QUALIFIED_RUN_ID,
                verification=cb0.verification,
                qualified=True,
            ),
            "cb1_not_qualified_audit": _anchor_summary(
                run_id=CB1_AUDIT_RUN_ID,
                verification=cb1_audit.verification,
                qualified=False,
            ),
            "cb2_not_qualified_audit": _anchor_summary(
                run_id=CB2_AUDIT_RUN_ID,
                verification=cb2_audit.verification,
                qualified=False,
            ),
            "cb5_not_qualified_audit": _anchor_summary(
                run_id=CB5_AUDIT_RUN_ID,
                verification=cb5_audit.verification,
                qualified=False,
            ),
        },
        "treatment_contract": {
            "order": list(TREATMENT_ORDER),
            "arms": TREATMENT_MATRIX,
            "graph_policy": GRAPH_POLICY,
            "top_k": TOP_K,
            "same_snapshot_required": True,
            "same_ingestion_required": True,
            "same_33_case_denominator_required": True,
            "denominator": denominator,
        },
        "production_component": component,
        "production_identity": PRODUCTION_IDENTITY,
        "gate_authorization": gate,
        "execution_policy": {
            "prepared_only": False,
            "production_execution_authorized": True,
            "requires_explicit_c4_02_completion_gate": True,
            "requires_exact_public_production_identity": True,
            "component_injection_forbidden": True,
            "fake_or_substitute_qualification_forbidden": True,
            "test_artifact_execution_forbidden": True,
            "fresh_run_directory_required": True,
            "immutable_publish_required": True,
        },
        "required_reporting": {
            name: {**contract, "status": "pending"} for name, contract in METRIC_CONTRACT.items()
        },
        "artifact_policy": {
            "expected_entries": sorted(EXPECTED_ARTIFACT_ENTRIES),
            "canonical_json_required": True,
            "portable_sqlite_required": True,
            "secret_scan_required": True,
            "temporary_path_scan_required": True,
            "wal_shm_forbidden": True,
            "pyc_and_pycache_forbidden": True,
        },
        "isolation": {
            "database": "temporary-sqlite-only",
            "ingestion": "released-golden-v2-ast-v2-only",
            "formal_database_guard": {
                "protected": True,
                "hash_size_mtime_checked": True,
                "wal_shm_checked": True,
                "observed": database_after,
            },
            "repository_runs_guard": {
                "protected": True,
                "file_count": len(runs_after),
                "unchanged": True,
            },
        },
    }


@dataclass(slots=True)
class PreparedProduction:
    base: cb1.PreparedCB1
    dense: cb2.PreparedCB2
    hybrid: Any
    graph: Any
    reranker: Any
    hybrid_descriptor: cb2.RetrieverDescriptor
    publisher_descriptor: cb2.ComponentDescriptor
    graph_identity: dict[str, str]
    reranker_descriptor: cb5.RerankerDescriptor
    graph_publications: tuple[dict[str, Any], ...]


def _publish_isolated_graph(base: cb1.PreparedCB1, public: Any) -> tuple[dict[str, Any], ...]:
    registered = {item.value for item in public.CodeRelationType}
    generations = base.runtime.store.active_code_generations(project_id=PROJECT_ID)
    if len(generations) != 3:
        raise CB3Error("isolated C-B3 database must expose three active generations")
    publications: list[dict[str, Any]] = []
    with base.runtime.store.connection() as database:
        marks = ",".join("?" for _ in registered)
        invalid_types = database.execute(
            f"""SELECT edge_type, count(*) AS edge_count FROM edges
                WHERE generation_id IN ({",".join("?" for _ in generations.values())})
                  AND edge_type NOT IN ({marks})
                GROUP BY edge_type
                ORDER BY edge_type""",
            (*generations.values(), *sorted(registered)),
        ).fetchall()
        excluded_types = {str(row["edge_type"]): int(row["edge_count"]) for row in invalid_types}
        invalid_endpoints = int(
            database.execute(
                """SELECT count(*) FROM edges edge
                   LEFT JOIN entities source
                     ON source.id=edge.source_id
                    AND source.repository_id=edge.repository_id
                    AND source.generation_id=edge.generation_id
                   LEFT JOIN entities target
                     ON target.id=edge.target_id
                    AND target.repository_id=edge.repository_id
                    AND target.generation_id=edge.generation_id
                   WHERE edge.generation_id IN ({})
                     AND (
                       source.id IS NULL OR target.id IS NULL
                       OR source.commit_sha != target.commit_sha
                       OR source.acl_ref != target.acl_ref
                     )""".format(",".join("?" for _ in generations.values())),
                tuple(generations.values()),
            ).fetchone()[0]
        )
        if invalid_endpoints:
            raise CB3Error("isolated graph contains invalid versioned or ACL-crossing endpoints")

    for repository_id, generation_id in sorted(generations.items()):
        current = base.runtime.store.get_code_index_publication(
            generation_id,
            repository_id=repository_id,
            active_only=True,
        )
        if current is None or current.get("status") != "published":
            raise CB3Error(f"missing active production publication for {repository_id}")
        with base.runtime.store.connection() as database:
            edge_count = int(
                database.execute(
                    """SELECT count(*) FROM edges
                       WHERE repository_id=? AND generation_id=?""",
                    (repository_id, generation_id),
                ).fetchone()[0]
            )
            registered_edge_count = int(
                database.execute(
                    f"""SELECT count(*) FROM edges
                        WHERE repository_id=? AND generation_id=?
                          AND edge_type IN ({marks})""",
                    (repository_id, generation_id, *sorted(registered)),
                ).fetchone()[0]
            )
            excluded_edge_types = {
                str(row["edge_type"]): int(row["edge_count"])
                for row in database.execute(
                    f"""SELECT edge_type, count(*) AS edge_count FROM edges
                        WHERE repository_id=? AND generation_id=?
                          AND edge_type NOT IN ({marks})
                        GROUP BY edge_type
                        ORDER BY edge_type""",
                    (repository_id, generation_id, *sorted(registered)),
                ).fetchall()
            }
            entity_count = int(
                database.execute(
                    """SELECT count(*) FROM entities
                       WHERE repository_id=? AND generation_id=?""",
                    (repository_id, generation_id),
                ).fetchone()[0]
            )
        validation = dict(current.get("validation") or {})
        capabilities = dict(validation.get("capabilities") or {})
        capabilities["graph_retrieval"] = True
        validation["capabilities"] = capabilities
        validation["cb3_graph_publication"] = {
            "source": "production-ast-v2-ingestion-relations",
            "retriever_class": REAL_TYPED_GRAPH_CLASS,
            "retriever_version": str(public.CodeTypedGraphRetriever.retriever_version),
            "edge_count": edge_count,
            "registered_edge_count": registered_edge_count,
            "excluded_unregistered_edge_types": excluded_edge_types,
            "all_excluded_unregistered_edge_types": excluded_types,
            "entity_count": entity_count,
            "registry_closed": True,
            "endpoint_version_acl_integrity": True,
        }
        base.runtime.store.upsert_code_index_publication(
            {
                "generation_id": generation_id,
                "repository_id": repository_id,
                "project_id": PROJECT_ID,
                "builder": current["builder"],
                "sparse": current["sparse"],
                "embedding": current["embedding"],
                "graph": GRAPH_PUBLICATION_VERSION,
                "status": "published",
                "validation": validation,
            }
        )
        observed = public.SQLiteGraphAdjacencyProvider(base.database_path).publication(
            repository_id=repository_id,
            generation_id=generation_id,
        )
        if observed is None or not observed.ready:
            raise CB3Error(f"isolated graph publication is not production-ready: {repository_id}")
        publications.append(
            {
                "repository_id": repository_id,
                "generation_id": generation_id,
                "graph_version": observed.graph_version,
                "stable_version": observed.stable_version,
                "edge_count": edge_count,
                "registered_edge_count": registered_edge_count,
                "excluded_unregistered_edge_types": excluded_edge_types,
                "entity_count": entity_count,
                "ready": observed.ready,
            }
        )
    return tuple(publications)


def _prepare_production_environment(work_root: Path, root: Path) -> PreparedProduction:
    public = importlib.import_module(PUBLIC_CODE_MODULE)
    if getattr(public, REAL_TYPED_GRAPH_CLASS, None) is not public.CodeTypedGraphRetriever:
        raise CB3Error("public CodeTypedGraphRetriever export identity drifted")
    base = cb1.prepare_cb1_environment(work_root, root=root)
    exact_sparse = cb1._load_real_retriever(base.runtime)
    exact_descriptor = cb1._retriever_descriptor(exact_sparse)
    cb1._publish_real_treatment_index(base, exact_descriptor)
    profile = cb2._embedding_profile()
    publisher = cb2._load_real_publisher(base.runtime, profile)
    dense = cb2._publish_dense_profile(base, publisher, profile)
    hybrid = cb2._load_real_retriever(base.runtime, profile)
    hybrid_descriptor = cb2._retriever_descriptor(hybrid)
    graph_publications = _publish_isolated_graph(base, public)
    graph = public.CodeTypedGraphRetriever(public.SQLiteGraphAdjacencyProvider(base.database_path))
    graph_identity = require_production_retriever_identity(graph)
    reranker = cb5._load_real_reranker(base.runtime, hybrid, None)
    reranker_descriptor = cb5._reranker_descriptor(reranker)
    if type(reranker) is not public.CodeRerankedRetriever:
        raise CB3Error("qualified C-B3 did not construct the exact public reranker")
    if type(reranker.reranker) is not public.DeterministicCodeReranker:
        raise CB3Error("qualified C-B3 reranker does not use the current deterministic model")
    return PreparedProduction(
        base=base,
        dense=dense,
        hybrid=hybrid,
        graph=graph,
        reranker=reranker,
        hybrid_descriptor=hybrid_descriptor,
        publisher_descriptor=dense.publisher,
        graph_identity=graph_identity,
        reranker_descriptor=reranker_descriptor,
        graph_publications=graph_publications,
    )


def _graph_profile(public: Any, profile: Any) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    directions = (
        public.CodeTraversalDirection.INCOMING,
        public.CodeTraversalDirection.OUTGOING,
    )
    edge_types = tuple(
        edge_type
        for edge_type in public.CodeRelationType
        if profile.task in public.get_edge_spec(edge_type).allowed_tasks
    )
    if not edge_types:
        raise CB3Error(f"production graph registry has no edges for task {profile.task.value}")
    return directions, edge_types


def _normalized_graph_candidates(public: Any, results: Sequence[Any]) -> tuple[Any, ...]:
    candidates = [item.candidate for result in results for item in result.candidates]
    candidates.sort(
        key=lambda item: (
            -float(item.source_fused_score),
            item.repository_id,
            item.locator,
            item.retrieval_unit_id,
        )
    )
    normalized = []
    for rank, candidate in enumerate(candidates[: int(GRAPH_POLICY["graph_candidates"])]):
        payload = candidate.model_dump(mode="python", round_trip=True)
        payload["within_source_rank"] = rank
        payload["raw_channel_ranks"] = (
            public.CodeChannelRank(
                channel=public.CodeRetrievalChannel.GRAPH,
                rank=rank,
            ),
        )
        normalized.append(public.CodeRetrievalCandidate.model_validate(payload))
    return tuple(normalized)


def _traverse_graph(
    prepared: PreparedProduction,
    request: EvidenceSearchRequest,
    profile: Any,
    seeds: Sequence[Any],
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    public = importlib.import_module(PUBLIC_CODE_MODULE)
    allowed = tuple(sorted(str(item) for item in request.scope.allowed_acl_refs))
    if not seeds or not allowed:
        return (), ()
    directions, edge_types = _graph_profile(public, profile)
    grouped: dict[tuple[str, str], list[Any]] = {}
    for seed in seeds:
        grouped.setdefault(
            (str(seed.stable_version), str(seed.source_generation)),
            [],
        ).append(seed)
    results = []
    for (stable_version, generation_id), group in sorted(grouped.items()):
        traversal_request = public.GraphTraversalRequest(
            seed_candidates=tuple(group),
            task=profile.task,
            directions=directions,
            edge_types=edge_types,
            max_hops=int(GRAPH_POLICY["max_hops"]),
            beam_width=int(GRAPH_POLICY["beam_width"]),
            min_confidence=float(GRAPH_POLICY["min_confidence"]),
            stable_version=stable_version,
            generation_id=generation_id,
            allowed_acl_refs=allowed,
            node_budget=int(GRAPH_POLICY["node_budget"]),
            edge_budget=int(GRAPH_POLICY["edge_budget"]),
            candidate_budget=int(GRAPH_POLICY["graph_candidates"]),
            deadline=time.monotonic() + 1.5,
        )
        result = prepared.graph.search_with_trace(traversal_request)
        if type(result) is not public.GraphTraversalResult:
            raise CB3Error("production graph retriever returned a substituted result")
        if result.trace.status is public.GraphTraversalStatus.UNAVAILABLE:
            raise CB3Error(
                f"production graph traversal unavailable: {result.trace.unavailable_reason}"
            )
        results.append(result)
    return _normalized_graph_candidates(public, results), tuple(results)


def _graph_source_result(
    public: Any, hybrid: Any, candidates: Sequence[Any], latency: float
) -> Any:
    graph_ranks = [
        int(rank.rank)
        for candidate in candidates
        for rank in candidate.raw_channel_ranks
        if rank.channel is public.CodeRetrievalChannel.GRAPH
    ]
    graph_outcome = (
        public.CodeChannelCompleteWithHits(
            channel=public.CodeRetrievalChannel.GRAPH,
            hit_count=max(graph_ranks) + 1,
        )
        if graph_ranks
        else public.CodeChannelCompleteNoMatch(channel=public.CodeRetrievalChannel.GRAPH)
    )
    outcomes = tuple(
        graph_outcome if item.channel is public.CodeRetrievalChannel.GRAPH else item
        for item in hybrid.result.channel_outcomes
    )
    return hybrid.result.model_copy(
        update={
            "candidates": tuple(candidates),
            "channel_outcomes": outcomes,
            "index_version": (f"{hybrid.result.index_version}+{prepared_graph_version(public)}"),
            "latency_ms": float(hybrid.result.latency_ms) + float(latency),
        }
    )


def prepared_graph_version(public: Any) -> str:
    return f"{public.CodeTypedGraphRetriever.retriever_version}:{GRAPH_PUBLICATION_VERSION}"


def _relations_from_candidates(candidates: Sequence[Any]) -> list[dict[str, str]]:
    relations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        for edge in candidate.relation_path.edges:
            key = (
                str(edge.source_entity_id),
                str(edge.target_entity_id),
                str(edge.edge_type.value),
            )
            if key in seen:
                continue
            seen.add(key)
            relations.append(
                {
                    "source": key[0],
                    "target": key[1],
                    "edge_type": key[2],
                    "direction": str(edge.direction.value),
                }
            )
    return relations


def _evaluation_candidates(
    runtime: Runtime,
    request: EvidenceSearchRequest,
    candidates: Sequence[Any],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for candidate in candidates:
        unit = runtime.store.load_code_unit(
            candidate.retrieval_unit_id,
            project_id=request.scope.project_id,
            repository_id=candidate.repository_id,
            generation_id=candidate.source_generation,
            allowed_acl_refs=request.scope.allowed_acl_refs,
            active_only=True,
        )
        if unit is None:
            raise CB3Error("production candidate is outside the active authorized ast-v2 index")
        channels = [str(item.channel.value) for item in candidate.raw_channel_scores]
        values.append(
            {
                "entity_id": candidate.entity_id,
                "retrieval_unit_id": candidate.retrieval_unit_id,
                "entity_type": candidate.entity_type,
                "path": unit.get("path"),
                "start_line": unit.get("start_line"),
                "end_line": unit.get("end_line"),
                "locator": candidate.locator,
                "version": candidate.stable_version,
                "acl_ref": candidate.acl_ref,
                "channels": channels,
                "graph_only": channels == ["graph"],
                "context_roles": [str(candidate.role.value)],
                "rank": int(candidate.within_source_rank) + 1,
            }
        )
    return values


def _trace_record(result: Any) -> dict[str, Any]:
    trace = result.trace
    paths = []
    for path in trace.paths:
        paths.append(
            {
                "length": len(path.relation_path.edges),
                "edges": [
                    {
                        "source": edge.source_entity_id,
                        "target": edge.target_entity_id,
                        "edge_type": str(edge.edge_type.value),
                        "direction": str(edge.direction.value),
                    }
                    for edge in path.relation_path.edges
                ],
            }
        )
    return {
        "status": str(trace.status.value),
        "retriever_version": trace.retriever_version,
        "generation_id": trace.generation_id,
        "stable_version": trace.stable_version,
        "publication_versions": list(trace.publication_versions),
        "seed_count": trace.seed_count,
        "expanded_nodes": trace.expanded_nodes,
        "expanded_edges": trace.expanded_edges,
        "hops_completed": trace.hops_completed,
        "rejection_counts": dict(trace.rejection_counts),
        "node_budget_exhausted": trace.node_budget_exhausted,
        "edge_budget_exhausted": trace.edge_budget_exhausted,
        "deadline_exceeded": trace.deadline_exceeded,
        "latency_ms": trace.latency_ms,
        "paths": paths,
    }


class _CB3EvaluationRetriever:
    def __init__(self, prepared: PreparedProduction, arm: str) -> None:
        if arm not in TREATMENT_ORDER:
            raise CB3Error(f"unknown C-B3 treatment arm: {arm}")
        self.prepared = prepared
        self.arm = arm
        self._case_by_question = {
            case.request.question: case for case in prepared.base.package.eligible_cases
        }
        self.observations: dict[str, dict[str, Any]] = {}

    def search(self, request: Any) -> dict[str, Any]:
        return self.search_evaluation(request, graph_candidate_enabled=False)

    def search_evaluation(
        self,
        request: EvidenceSearchRequest,
        *,
        graph_candidate_enabled: bool,
    ) -> dict[str, Any]:
        expected_toggle = self.arm in {"typed_graph", "graph_reranker"}
        if graph_candidate_enabled is not expected_toggle:
            raise CB3Error(f"{self.arm} received the wrong graph-candidate toggle")
        case = self._case_by_question.get(str(request.query))
        if case is None:
            raise CB3Error("C-B3 retriever received a query outside released membership")
        profile = cb2._query_profile(case, request)
        seed_request = EvidenceSearchRequest(
            query=request.query,
            scope=request.scope,
            limit=int(GRAPH_POLICY["seed_candidates"]),
            include_edges=True,
        )
        started = time.perf_counter()
        hybrid = self.prepared.hybrid.search_with_trace(seed_request, profile)
        graph_candidates: tuple[Any, ...] = ()
        graph_results: tuple[Any, ...] = ()
        if TREATMENT_MATRIX[self.arm]["graph_traversal"]:
            graph_candidates, graph_results = _traverse_graph(
                self.prepared,
                request,
                profile,
                hybrid.result.candidates,
            )
        graph_latency = sum(float(item.trace.latency_ms) for item in graph_results)
        public = importlib.import_module(PUBLIC_CODE_MODULE)
        output_candidates = tuple(hybrid.result.candidates)
        rerank_decisions: Sequence[Any] = ()
        rerank_fallback = False
        rerank_reason = ""
        if TREATMENT_MATRIX[self.arm]["graph_enters_candidates"]:
            output_candidates = public.fuse_graph_candidates(
                hybrid.result.candidates,
                graph_candidates,
                limit=TOP_K,
            )
        if TREATMENT_MATRIX[self.arm]["reranker"] and output_candidates:
            source_result = _graph_source_result(
                public,
                hybrid,
                output_candidates,
                graph_latency,
            )
            fused = public.CodeHybridSearchResult(result=source_result, trace=hybrid.trace)
            try:
                output_candidates, rerank_decisions = self.prepared.reranker.reranker.rerank(
                    request,
                    profile,
                    fused,
                    top_k=TOP_K,
                    deadline=time.perf_counter()
                    + float(self.prepared.reranker.rerank_timeout_ms) / 1_000.0,
                    clock=time.perf_counter,
                )
            except Exception as exc:
                rerank_fallback = True
                rerank_reason = f"{type(exc).__name__}: {' '.join(str(exc).split())}"

        relations = _relations_from_candidates(
            tuple(graph_candidates)
            if TREATMENT_MATRIX[self.arm]["graph_traversal"]
            else tuple(output_candidates)
        )
        traces = [_trace_record(item) for item in graph_results]
        duration_ms = (time.perf_counter() - started) * 1_000.0
        channel_outcomes = {
            str(item.channel.value): str(item.status.value)
            for item in hybrid.result.channel_outcomes
        }
        channel_outcomes["graph"] = (
            "disabled"
            if not TREATMENT_MATRIX[self.arm]["graph_traversal"]
            else "complete_with_hits"
            if graph_candidates
            else "complete_no_match"
        )
        trace = {
            "duration_ms": duration_ms,
            "exact_outcome": channel_outcomes["exact"],
            "sparse_outcome": channel_outcomes["sparse"],
            "dense_outcome": channel_outcomes["dense"],
            "graph_outcome": channel_outcomes["graph"],
            "graph_candidate_enabled": expected_toggle,
            "retriever_class": REAL_TYPED_GRAPH_CLASS,
            "retriever_version": self.prepared.graph_identity["retriever_version"],
            "hybrid_retriever_class": self.prepared.hybrid_descriptor.class_name,
            "hybrid_retriever_version": self.prepared.hybrid_descriptor.version,
            "reranker_class": self.prepared.reranker_descriptor.class_name,
            "reranker_version": self.prepared.reranker_descriptor.version,
            "rerank_applied": bool(TREATMENT_MATRIX[self.arm]["reranker"]),
            "rerank_fallback": rerank_fallback,
            "rerank_fallback_reason": rerank_reason,
            "graph_latency_ms": graph_latency,
            "graph_expanded_nodes": sum(item["expanded_nodes"] for item in traces),
            "graph_expanded_edges": sum(item["expanded_edges"] for item in traces),
            "graph_path_count": sum(len(item["paths"]) for item in traces),
            "graph_rejection_counts": dict(
                sum((Counter(item["rejection_counts"]) for item in traces), Counter())
            ),
            "graph_traces": traces,
            "rerank_decisions": _jsonable(rerank_decisions),
        }
        self.observations[case.canonical_id] = {
            "case_id": case.canonical_id,
            "arm": self.arm,
            "graph_candidate_ids": [
                {
                    "entity_id": item.entity_id,
                    "retrieval_unit_id": item.retrieval_unit_id,
                }
                for item in graph_candidates
            ],
            "returned_candidates": _evaluation_candidates(
                self.prepared.base.runtime,
                request,
                output_candidates,
            ),
            "graph_traces": traces,
            "rerank_fallback": rerank_fallback,
            "rerank_fallback_reason": rerank_reason,
        }
        generations = self.prepared.base.runtime.store.active_code_generations(
            project_id=request.scope.project_id,
            repository_ids=request.scope.repository_ids or None,
        )
        return {
            "results": self.observations[case.canonical_id]["returned_candidates"],
            "relations": relations,
            "index_generation": sorted(generations.values()),
            "trace": trace,
        }


def _run_arm(
    prepared: PreparedProduction,
    arm: str,
    *,
    shared_input_fingerprint: str,
    gate: Mapping[str, Any],
    graph_off_run_id: str | None,
) -> tuple[dict[str, Any], _CB3EvaluationRetriever]:
    adapter = _CB3EvaluationRetriever(prepared, arm)
    prepared.base.runtime.platform.code = adapter
    request = CodeEvaluationRunRequest(
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        package_hash=PACKAGE_HASH,
        case_ids=list(prepared.base.loaded.case_ids),
        repository_ids=sorted(source.repository_id for source in prepared.base.sources.values()),
        graph_candidate_enabled=arm in {"typed_graph", "graph_reranker"},
        paired_graph_off_run_id=(
            graph_off_run_id if arm in {"typed_graph", "graph_reranker"} else None
        ),
        limit_per_query=TOP_K,
        config={
            "cb3_schema_version": CB3_SCHEMA_VERSION,
            "shared_input_fingerprint": shared_input_fingerprint,
            "production_identity": PRODUCTION_IDENTITY,
            "gate_report_sha256": gate["report_sha256"],
            "network_used": False,
            "downloads_performed": False,
        },
    )
    run = prepared.base.runtime.evaluation.run_code(
        request,
        allowed_acl_refs=[ACL_REF],
        enforce_acl=True,
    )
    if (
        run.get("status") != "completed"
        or int(run.get("case_count") or 0) != EXPECTED_ELIGIBLE_COUNT
        or len(run.get("results") or []) != EXPECTED_ELIGIBLE_COUNT
        or len(adapter.observations) != EXPECTED_ELIGIBLE_COUNT
    ):
        raise CB3Error(f"{arm} did not produce a complete real 33-case Run")
    baseline.assert_terminal_immutability(prepared.base.database_path, run["id"])
    return run, adapter


def _overall_metric(run: Mapping[str, Any], name: str) -> dict[str, Any]:
    matches = [
        item
        for item in run.get("metric_values") or []
        if item.get("case_id") is None
        and item.get("metric_name") == name
        and (item.get("slice") or {}).get("scope") == "overall"
    ]
    if name == "required_path_precision":
        matches = [
            item
            for item in matches
            if (item.get("slice") or {}).get("denominator") == "all_returned_edges"
        ]
    if len(matches) != 1:
        raise CB3Error(f"Run does not expose one authoritative overall {name} metric")
    return dict(matches[0])


def _metric_evidence(
    run: Mapping[str, Any],
    name: str,
    package: GoldenPackage,
) -> dict[str, Any]:
    metric = _overall_metric(run, name)
    return {
        "status": metric["status"],
        "value": metric.get("value"),
        "numerator": metric.get("numerator"),
        "denominator": metric.get("denominator"),
        "eligible_cases": metric.get("eligible_cases"),
        "available_cases": metric.get("available_cases"),
        "unavailable_cases": metric.get("unavailable_cases"),
        "unavailable_reason": metric.get("unavailable_reason"),
        "membership_case_count": EXPECTED_ELIGIBLE_COUNT,
        "case_membership_hash": package.membership_hash,
        "authoritative_source": "evaluation_metric_values",
    }


def _p95(values: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "status": "unavailable",
            "value": None,
            "sample_count": 0,
            "unavailable_reason": "no production timing samples",
        }
    position = (len(ordered) - 1) * 0.95
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return {
        "status": "available",
        "value": ordered[lower] * (1.0 - weight) + ordered[upper] * weight,
        "sample_count": len(ordered),
        "unavailable_reason": None,
    }


def _custom_metric(
    *,
    value: float | None,
    numerator: float,
    denominator: float,
    package: GoldenPackage,
    source: str,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "status": "available" if value is not None else "unavailable",
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "membership_case_count": EXPECTED_ELIGIBLE_COUNT,
        "case_membership_hash": package.membership_hash,
        "authoritative_source": source,
        "unavailable_reason": unavailable_reason if value is None else None,
    }


def _candidate_grade(case: GoldenCase, candidate: Mapping[str, Any]) -> int:
    entity_id = str(candidate.get("entity_id") or "")
    unit_id = str(candidate.get("retrieval_unit_id") or "")
    grades = [
        int(item.relevance_grade)
        for item in case.request.candidate_judgments
        if (item.entity_id and item.entity_id == entity_id)
        or (item.retrieval_unit_id and item.retrieval_unit_id == unit_id)
    ]
    if entity_id in case.request.forbidden_entity_ids:
        grades.append(-1)
    profile = case.request.code_profile
    if entity_id in case.request.expected_entity_ids or (
        profile is not None and unit_id in profile.expected_unit_ids
    ):
        grades.append(2)
    if profile is not None:
        for group in profile.acceptable_alternative_groups:
            if entity_id in group.entity_ids or unit_id in group.retrieval_unit_ids:
                grades.append(2)
    return max(grades) if grades else 0


def _graph_analysis(
    adapters: Mapping[str, _CB3EvaluationRetriever],
    runs: Mapping[str, dict[str, Any]],
    package: GoldenPackage,
    graph_publications: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    cases = {case.canonical_id: case for case in package.eligible_cases}
    arms: dict[str, Any] = {}
    custom: dict[str, dict[str, dict[str, Any]]] = {}
    rejection_groups = {
        "type": ("type", "edge_type"),
        "direction": ("direction",),
        "confidence": ("confidence",),
        "version": ("version", "generation"),
        "acl": ("acl", "authorization"),
        "cycle": ("cycle",),
        "budget": ("budget", "beam_width"),
    }
    for arm in TREATMENT_ORDER:
        observations = adapters[arm].observations
        traces = [
            trace for observation in observations.values() for trace in observation["graph_traces"]
        ]
        rejections: Counter[str] = Counter()
        for trace in traces:
            rejections.update(
                {
                    str(key): int(value)
                    for key, value in (trace.get("rejection_counts") or {}).items()
                }
            )
        paths = [
            (case_id, path)
            for case_id, observation in observations.items()
            for trace in observation["graph_traces"]
            for path in trace["paths"]
        ]
        path_lengths = [int(path["length"]) for _, path in paths]
        labeled_edges = 0
        type_matches = 0
        direction_matches = 0
        for case_id, path in paths:
            profile = cases[case_id].request.code_profile
            required = (
                [edge for required_path in profile.required_paths for edge in required_path.edges]
                if profile is not None
                else []
            )
            if not required:
                continue
            typed = {(edge.source, edge.target, edge.edge_type) for edge in required}
            directed = {
                (edge.source, edge.target, edge.edge_type, edge.direction) for edge in required
            }
            for edge in path["edges"]:
                labeled_edges += 1
                key = (edge["source"], edge["target"], edge["edge_type"])
                type_matches += int(key in typed)
                direction_matches += int((*key, edge["direction"]) in directed)

        graph_attributed = []
        graph_exclusive = []
        for case_id, observation in observations.items():
            case = cases[case_id]
            for candidate in observation["returned_candidates"][:10]:
                channels = {str(item) for item in candidate["channels"]}
                if "graph" not in channels:
                    continue
                record = (case, candidate, _candidate_grade(case, candidate))
                graph_attributed.append(record)
                if candidate["graph_only"]:
                    graph_exclusive.append(record)
        harmful_eligible = [
            item
            for item in graph_attributed
            if item[0].request.forbidden_entity_ids
            or any(judgment.relevance_grade < 0 for judgment in item[0].request.candidate_judgments)
        ]
        noise = _custom_metric(
            value=(
                sum(grade <= 0 for _, _, grade in graph_exclusive) / len(graph_exclusive)
                if graph_exclusive
                else None
            ),
            numerator=float(sum(grade <= 0 for _, _, grade in graph_exclusive)),
            denominator=float(len(graph_exclusive)),
            package=package,
            source="Golden-v2 graph-exclusive candidate judgments",
            unavailable_reason="no graph-exclusive candidate entered the top 10",
        )
        harmful = _custom_metric(
            value=(
                sum(grade < 0 for _, _, grade in harmful_eligible) / len(harmful_eligible)
                if harmful_eligible
                else None
            ),
            numerator=float(sum(grade < 0 for _, _, grade in harmful_eligible)),
            denominator=float(len(harmful_eligible)),
            package=package,
            source="Golden-v2 harmful annotations on graph-attributed candidates",
            unavailable_reason="no graph-attributed candidate had applicable harmful truth",
        )
        latency = _p95([float(trace["latency_ms"]) for trace in traces])
        graph_latency = {
            **latency,
            "membership_case_count": EXPECTED_ELIGIBLE_COUNT,
            "case_membership_hash": package.membership_hash,
            "authoritative_source": "production GraphTraversalTrace.latency_ms",
        }
        path_length = _custom_metric(
            value=(sum(path_lengths) / len(path_lengths) if path_lengths else None),
            numerator=float(sum(path_lengths)),
            denominator=float(len(path_lengths)),
            package=package,
            source="production GraphTraversalTrace.paths",
            unavailable_reason="no accepted graph paths",
        )
        type_accuracy = _custom_metric(
            value=(type_matches / labeled_edges if labeled_edges else None),
            numerator=float(type_matches),
            denominator=float(labeled_edges),
            package=package,
            source="production paths joined to released typed path truth",
            unavailable_reason="no accepted path edge had applicable released path truth",
        )
        direction_accuracy = _custom_metric(
            value=(direction_matches / labeled_edges if labeled_edges else None),
            numerator=float(direction_matches),
            denominator=float(labeled_edges),
            package=package,
            source="production paths joined to released directed path truth",
            unavailable_reason="no accepted path edge had applicable released direction truth",
        )
        prune_counts = {
            name: sum(
                count
                for key, count in rejections.items()
                if any(token in key.casefold() for token in tokens)
            )
            for name, tokens in rejection_groups.items()
        }
        prune_counts["budget"] += sum(
            bool(trace["node_budget_exhausted"]) + bool(trace["edge_budget_exhausted"])
            for trace in traces
        )
        prune_counts["deadline"] = sum(bool(trace["deadline_exceeded"]) for trace in traces)
        descriptive = {
            f"graph_pruned_{name}": _custom_metric(
                value=float(value),
                numerator=float(value),
                denominator=float(len(traces)),
                package=package,
                source="production GraphTraversalTrace",
            )
            for name, value in prune_counts.items()
        }
        descriptive["graph_expanded_nodes"] = _custom_metric(
            value=float(sum(int(trace["expanded_nodes"]) for trace in traces)),
            numerator=float(sum(int(trace["expanded_nodes"]) for trace in traces)),
            denominator=float(len(traces)),
            package=package,
            source="production GraphTraversalTrace",
        )
        descriptive["graph_expanded_edges"] = _custom_metric(
            value=float(sum(int(trace["expanded_edges"]) for trace in traces)),
            numerator=float(sum(int(trace["expanded_edges"]) for trace in traces)),
            denominator=float(len(traces)),
            package=package,
            source="production GraphTraversalTrace",
        )
        custom[arm] = {
            "graph_only_recovery@10": _metric_evidence(
                runs[arm], "graph_only_recovery@10", package
            ),
            "graph_noise_rate@10": noise,
            "graph_harmful_candidate_rate@10": harmful,
            "accepted_path_length": path_length,
            "accepted_path_type_accuracy": type_accuracy,
            "accepted_path_direction_accuracy": direction_accuracy,
            "graph_latency_ms_p95": graph_latency,
            **descriptive,
        }
        arms[arm] = {
            "run_id": runs[arm]["id"],
            "traversal_enabled": bool(TREATMENT_MATRIX[arm]["graph_traversal"]),
            "trace_count": len(traces),
            "accepted_path_count": len(paths),
            "accepted_path_lengths": path_lengths,
            "expanded_nodes": sum(int(trace["expanded_nodes"]) for trace in traces),
            "expanded_edges": sum(int(trace["expanded_edges"]) for trace in traces),
            "rejection_counts": dict(sorted(rejections.items())),
            "pruned_by_policy": prune_counts,
            "rerank_fallback_count": sum(
                bool(item["rerank_fallback"]) for item in observations.values()
            ),
            "metrics": custom[arm],
        }
    return {
        "schema_version": "code-c-b3-graph-analysis-v1",
        "graph_policy": GRAPH_POLICY,
        "trace_semantics": {
            "type_direction_version_acl_confidence": (
                "SQLite adjacency predicates filter these before traversal; trace counts "
                "record any retriever-level rejection that remains"
            ),
            "cycle_budget_deadline": "production traversal trace counters and booleans",
        },
        "publication_type_filtering": [
            {
                "repository_id": publication["repository_id"],
                "generation_id": publication["generation_id"],
                "total_edge_count": publication["edge_count"],
                "registered_edge_count": publication["registered_edge_count"],
                "excluded_unregistered_edge_types": publication["excluded_unregistered_edge_types"],
            }
            for publication in graph_publications
        ],
        "arms": arms,
        "_metric_records": custom,
    }


def _comparison_reports(
    runs: Mapping[str, dict[str, Any]],
    package: GoldenPackage,
    cb0: cb1.CB0Anchor,
    cb1_audit: Any,
    cb2_audit: Any,
    cb5_audit: AuditAnchor,
) -> dict[str, Any]:
    controls = {
        "cb0_qualified_baseline": (
            cb0.run,
            "qualified-baseline",
            CB0_QUALIFIED_RUN_ID,
        ),
        "cb1_not_qualified_audit": (
            cb1_audit.run,
            "not-qualified-audit-control",
            CB1_AUDIT_RUN_ID,
        ),
        "cb2_not_qualified_audit": (
            cb2_audit.run,
            "not-qualified-audit-control",
            CB2_AUDIT_RUN_ID,
        ),
        "cb5_not_qualified_audit": (
            cb5_audit.run,
            "not-qualified-audit-control",
            CB5_AUDIT_RUN_ID,
        ),
    }
    return {
        "schema_version": "code-c-b3-comparisons-v1",
        "same_released_membership": True,
        "case_membership_hash": package.membership_hash,
        "against_anchors": {
            control_name: {
                arm: cb2._comparison_report(
                    runs[arm],
                    control_run,
                    package,
                    role=role,
                    control_run_id=run_id,
                )
                for arm in TREATMENT_ORDER
            }
            for control_name, (control_run, role, run_id) in controls.items()
        },
        "within_cb3": {
            arm: cb2._comparison_report(
                runs[arm],
                runs["graph_off"],
                package,
                role="same-execution-graph-off-control",
                control_run_id=runs["graph_off"]["id"],
            )
            for arm in TREATMENT_ORDER[1:]
        },
    }


def _acceptance(
    runs: Mapping[str, dict[str, Any]],
    arm_metrics: Mapping[str, Mapping[str, Mapping[str, Any]]],
    graph_analysis: Mapping[str, Any],
    comparisons: Mapping[str, Any],
    gate: Mapping[str, Any],
    prepared: PreparedProduction,
) -> dict[str, Any]:
    primary = arm_metrics["graph_reranker"]
    cb0_rows = comparisons["against_anchors"]["cb0_qualified_baseline"]["graph_reranker"][
        "comparisons"
    ]
    non_regression_names = {
        "entity_recall@10",
        "expected_locator_recall@10",
        "mrr@10",
        "ndcg@10",
    }
    non_regression = [
        row
        for row in cb0_rows
        if row["scope"] == "overall" and row["metric_name"] in non_regression_names
    ]

    def available(name: str) -> float | None:
        record = primary[name]
        return float(record["value"]) if record["status"] == "available" else None

    graph_arm = graph_analysis["arms"]["graph_reranker"]
    gates = [
        (
            "c4_02_gate_authorized",
            gate.get("decision") == C4_GATE_DECISION,
            gate,
        ),
        (
            "exact_production_identities",
            prepared.graph_identity["class"] == REAL_TYPED_GRAPH_CLASS
            and prepared.hybrid_descriptor.class_name == REAL_HYBRID_CLASS
            and prepared.reranker_descriptor.class_name == REAL_RERANKER_CLASS,
            {
                "graph": prepared.graph_identity,
                "hybrid": prepared.hybrid_descriptor.to_dict(),
                "reranker": prepared.reranker_descriptor.to_dict(),
            },
        ),
        (
            "four_complete_shared_33_case_runs",
            len({run["id"] for run in runs.values()}) == 4
            and all(int(run["case_count"]) == EXPECTED_ELIGIBLE_COUNT for run in runs.values()),
            {arm: run["id"] for arm, run in runs.items()},
        ),
        (
            "required_path_truth",
            available("required_path_recall") is not None
            and available("required_path_recall") >= 0.75
            and available("required_path_precision") is not None
            and available("required_path_precision") >= 0.05,
            {
                "recall": primary["required_path_recall"],
                "precision": primary["required_path_precision"],
                "thresholds": {"recall": 0.75, "precision": 0.05},
            },
        ),
        (
            "core_retrieval_quality",
            all(
                available(name) is not None and available(name) >= threshold
                for name, threshold in {
                    "entity_recall@10": 0.80,
                    "expected_locator_recall@10": 0.80,
                    "mrr@10": 0.60,
                    "ndcg@10": 0.55,
                }.items()
            ),
            {
                name: primary[name]
                for name in (
                    "entity_recall@10",
                    "expected_locator_recall@10",
                    "mrr@10",
                    "ndcg@10",
                )
            },
        ),
        (
            "cb0_quality_non_regression",
            all(
                row["same_case_membership"] is True
                and row["metric_denominator_equal"] is True
                and row["delta"]["status"] == "available"
                and float(row["delta"]["treatment_minus_baseline"]) >= -0.02
                for row in non_regression
            ),
            non_regression,
        ),
        (
            "safety",
            available("wrong_version_rate") is not None
            and available("wrong_version_rate") <= 0.02
            and available("unauthorized_candidate_rate") == 0.0
            and available("harmful_candidate_rate@10") is not None
            and available("harmful_candidate_rate@10") <= 0.10,
            {
                "wrong_version_rate": primary["wrong_version_rate"],
                "unauthorized_candidate_rate": primary["unauthorized_candidate_rate"],
                "harmful_candidate_rate@10": primary["harmful_candidate_rate@10"],
            },
        ),
        (
            "latency_and_reranker",
            available("latency_ms_p95") is not None
            and available("latency_ms_p95") <= 1_500.0
            and int(graph_arm["rerank_fallback_count"]) == 0,
            {
                "latency_ms_p95": primary["latency_ms_p95"],
                "rerank_fallback_count": graph_arm["rerank_fallback_count"],
                "latency_threshold_ms": 1_500.0,
            },
        ),
    ]
    gate_records = [
        {
            "gate": name,
            "passed": bool(passed),
            "status": "passed" if passed else "failed",
            "evidence": evidence,
        }
        for name, passed, evidence in gates
    ]
    return {
        "schema_version": "code-c-b3-acceptance-v1",
        "decision": "qualified"
        if all(item["passed"] for item in gate_records)
        else "not-qualified",
        "gates": gate_records,
        "threshold_policy": "frozen-in-code-c-b3-runner-v1",
    }


def _anchor_manifest(
    cb0: cb1.CB0Anchor,
    cb1_audit: Any,
    cb2_audit: Any,
    cb5_audit: AuditAnchor,
) -> dict[str, Any]:
    values = {
        "qualified_baseline": (
            CB0_QUALIFIED_RUN_ID,
            CB0_ARTIFACT_DIRECTORY,
            True,
            cb0,
        ),
        "cb1_not_qualified_audit": (
            CB1_AUDIT_RUN_ID,
            CB1_AUDIT_ARTIFACT_DIRECTORY,
            False,
            cb1_audit,
        ),
        "cb2_not_qualified_audit": (
            CB2_AUDIT_RUN_ID,
            CB2_AUDIT_ARTIFACT_DIRECTORY,
            False,
            cb2_audit,
        ),
        "cb5_not_qualified_audit": (
            CB5_AUDIT_RUN_ID,
            CB5_AUDIT_ARTIFACT_DIRECTORY,
            False,
            cb5_audit,
        ),
    }
    return {
        name: {
            "run_id": run_id,
            "artifact_directory": directory,
            "treatment_qualified": qualified,
            "qualification_use": "required" if qualified else "forbidden",
            "manifest_canonical_hash": anchor.verification.get("manifest_canonical_hash"),
            "artifact_set_hash": anchor.manifest.get("artifact_set_hash"),
        }
        for name, (run_id, directory, qualified, anchor) in values.items()
    }


def _existing_cb3_artifacts(runs_dir: Path) -> list[str]:
    values: list[str] = []
    for manifest_path in sorted(runs_dir.glob("*/manifest.json")):
        try:
            manifest = _read_json(manifest_path)
        except CB3Error:
            continue
        if manifest.get("schema_version") == CB3_SCHEMA_VERSION:
            values.append(manifest_path.parent.name)
    return values


def _require_old_run_tree_unchanged(
    before: Sequence[tuple[str, int, int]],
    after: Sequence[tuple[str, int, int]],
    *,
    new_directory: str,
) -> None:
    retained = tuple(item for item in after if not item[0].startswith(f"{new_directory}/"))
    if retained != tuple(before):
        raise CB3Error("an existing evals/code/runs artifact changed during C-B3 execution")
    added = [item for item in after if item[0].startswith(f"{new_directory}/")]
    if not added:
        raise CB3Error("C-B3 did not publish exactly one new Run directory")


def _validate_execution_policy(
    *,
    root: Path,
    runs_dir: Path,
    execution_mode: str,
    typed_graph_factory: TypedGraphFactory | None,
    c4_02_gate_approved: bool,
) -> None:
    repository_runs = (root / "evals" / "code" / "runs").resolve()
    if execution_mode != "qualified":
        raise CB3Error("C-B3 has no test/fake execution mode")
    if typed_graph_factory is not None:
        raise CB3Error(
            "qualified C-B3 forbids fake, injected, wrapped, or subclassed graph retrievers"
        )
    if not c4_02_gate_approved:
        raise CB3Error("qualified C-B3 execution requires explicit C4-02 completion Gate approval")
    if runs_dir != repository_runs:
        raise CB3Error("qualified C-B3 must publish under the canonical evals/code/runs directory")


def execute_cb3(
    *,
    root: Path | None = None,
    runs_dir: Path | None = None,
    execution_mode: str = "qualified",
    typed_graph_factory: TypedGraphFactory | None = None,
    c4_02_gate_approved: bool = False,
) -> Path:
    """Execute one isolated production C-B3 and atomically publish one artifact."""

    resolved_root = (root or repository_root()).resolve()
    resolved_runs = (
        runs_dir.resolve()
        if runs_dir is not None
        else (resolved_root / "evals" / "code" / "runs").resolve()
    )
    _validate_execution_policy(
        root=resolved_root,
        runs_dir=resolved_runs,
        execution_mode=execution_mode,
        typed_graph_factory=typed_graph_factory,
        c4_02_gate_approved=c4_02_gate_approved,
    )
    gate = _validate_gate_authorization(resolved_root)
    component = _production_component_contract()
    if component["status"] != "ready":
        raise CB3Error("production CodeTypedGraphRetriever is not ready")
    if _existing_cb3_artifacts(resolved_runs):
        raise CB3Error("a production C-B3 artifact already exists; repeat execution is forbidden")

    database_before = _formal_database_state(resolved_root)
    runs_before = _tree_state(resolved_runs)
    package = validate_golden_package(resolved_root)
    try:
        cb0 = cb1._load_cb0_anchor(resolved_root)
        cb1_audit = cb2._load_cb1_audit_anchor(resolved_root)
        cb2_audit = cb5._load_cb2_audit_anchor(resolved_root)
    except Exception as exc:
        raise CB3Error(f"unable to validate fixed C-B0/C-B1/C-B2 anchors: {exc}") from exc
    cb5_audit = _load_cb5_audit_anchor(resolved_root, package)
    canonical_case_ids = _canonical_case_ids(package)
    final_dir: Path | None = None

    with tempfile.TemporaryDirectory(prefix="code-c-b3-") as temporary:
        temporary_root = Path(temporary).resolve()
        prepared = _prepare_production_environment(
            temporary_root / "work",
            resolved_root,
        )
        shared_input = {
            "dataset_package_hash": PACKAGE_HASH,
            "case_membership_hash": package.membership_hash,
            "case_ids": list(canonical_case_ids),
            "ast_builder": "ast-v2",
            "hybrid": prepared.hybrid_descriptor.to_dict(),
            "dense_publisher": prepared.publisher_descriptor.to_dict(),
            "graph": prepared.graph_identity,
            "reranker": prepared.reranker_descriptor.to_dict(),
            "graph_publications": list(prepared.graph_publications),
            "graph_policy": GRAPH_POLICY,
            "network_used": False,
            "downloads_performed": False,
        }
        shared_input_fingerprint = _fingerprint(shared_input)
        runs: dict[str, dict[str, Any]] = {}
        adapters: dict[str, _CB3EvaluationRetriever] = {}
        graph_off_run_id: str | None = None
        for arm in TREATMENT_ORDER:
            run, adapter = _run_arm(
                prepared,
                arm,
                shared_input_fingerprint=shared_input_fingerprint,
                gate=gate,
                graph_off_run_id=graph_off_run_id,
            )
            runs[arm] = run
            adapters[arm] = adapter
            if arm == "graph_off":
                graph_off_run_id = str(run["id"])

        case_ids_by_treatment = {
            arm: [str(result["name"]) for result in runs[arm]["results"]] for arm in TREATMENT_ORDER
        }
        denominator = require_same_33_case_denominator(
            case_ids_by_treatment,
            expected_case_ids=canonical_case_ids,
        )
        comparison_fingerprints = {
            str(run["snapshot"]["comparison_fingerprint"]) for run in runs.values()
        }
        observed_generations = {
            tuple(run["snapshot"]["observed_index_generations"]) for run in runs.values()
        }
        if len(comparison_fingerprints) != 1 or len(observed_generations) != 1:
            raise CB3Error("four C-B3 arms did not share one immutable production snapshot")
        if _formal_database_state(resolved_root) != database_before:
            raise CB3Error("formal var/evidence-rag.sqlite3 changed during isolated C-B3")
        if _tree_state(resolved_runs) != runs_before:
            raise CB3Error("evals/code/runs changed before C-B3 publication")

        primary = runs["graph_reranker"]
        token = str(primary["id"]).rsplit("/", 1)[-1]
        artifact_dir = temporary_root / "artifact" / token
        artifact_dir.mkdir(parents=True)
        portable_database = artifact_dir / "evaluation.sqlite3"
        with prepared.base.runtime.store.connection() as database:
            database.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(prepared.base.database_path, portable_database)
        try:
            baseline._sanitize_database(portable_database, temporary_root)
        except Exception as exc:
            raise CB3Error(f"unable to sanitize portable C-B3 SQLite: {exc}") from exc
        portable_store = EvaluationStore(baseline._ReadOnlySQLiteStore(portable_database))
        persisted_runs: dict[str, dict[str, Any]] = {}
        for arm, run in runs.items():
            persisted = portable_store.get_run(str(run["id"]))
            if persisted is None or persisted.get("status") != "completed":
                raise CB3Error(f"portable database lost the completed {arm} Run")
            baseline.assert_terminal_immutability(portable_database, str(run["id"]))
            persisted_runs[arm] = persisted

        graph_analysis = _graph_analysis(
            adapters,
            persisted_runs,
            package,
            prepared.graph_publications,
        )
        graph_metrics = graph_analysis.pop("_metric_records")
        native_names = {
            "expected_locator_recall@10": "expected_locator_recall@10",
            "entity_recall@10": "entity_recall@10",
            "mrr@10": "mrr@10",
            "ndcg@10": "ndcg@10",
            "required_path_recall": "required_path_recall",
            "required_path_precision": "required_path_precision",
            "harmful_candidate_rate@10": "harmful_candidate_rate@10",
            "unauthorized_candidate_rate": "unauthorized_leakage_rate@10",
            "wrong_version_rate": "wrong_version_rate",
            "latency_ms_p95": "latency_p95_ms",
        }
        arm_metrics = {
            arm: {
                **{
                    output_name: _metric_evidence(
                        persisted_runs[arm],
                        native_name,
                        package,
                    )
                    for output_name, native_name in native_names.items()
                },
                **graph_metrics[arm],
            }
            for arm in TREATMENT_ORDER
        }
        for arm, records in arm_metrics.items():
            if set(records) != set(REQUIRED_REPORTING):
                raise CB3Error(f"{arm} does not cover the complete C-B3 metric contract")

        comparisons = _comparison_reports(
            persisted_runs,
            package,
            cb0,
            cb1_audit,
            cb2_audit,
            cb5_audit,
        )
        acceptance = _acceptance(
            persisted_runs,
            arm_metrics,
            graph_analysis,
            comparisons,
            gate,
            prepared,
        )
        qualified = acceptance["decision"] == "qualified"
        controls = _anchor_manifest(cb0, cb1_audit, cb2_audit, cb5_audit)
        reports: dict[str, dict[str, Any]] = {
            "treatments.json": {
                "schema_version": "code-c-b3-treatments-v1",
                "order": list(TREATMENT_ORDER),
                "arms": {
                    arm: {
                        **TREATMENT_MATRIX[arm],
                        "run_id": persisted_runs[arm]["id"],
                        "case_count": persisted_runs[arm]["case_count"],
                        "graph_candidate_enabled": persisted_runs[arm]["graph_candidate_enabled"],
                        "paired_graph_off_run_id": persisted_runs[arm]["paired_graph_off_run_id"],
                    }
                    for arm in TREATMENT_ORDER
                },
                "production_identity": {
                    "hybrid": prepared.hybrid_descriptor.to_dict(),
                    "dense_publisher": prepared.publisher_descriptor.to_dict(),
                    "graph": prepared.graph_identity,
                    "reranker": prepared.reranker_descriptor.to_dict(),
                },
                "shared_input": shared_input,
                "shared_input_fingerprint": shared_input_fingerprint,
            },
            "coverage.json": {
                "schema_version": "code-c-b3-coverage-v1",
                "dataset": {
                    "id": DATASET_ID,
                    "version": DATASET_VERSION,
                    "package_hash": PACKAGE_HASH,
                    "case_membership_hash": package.membership_hash,
                    "total_cases": EXPECTED_CASE_COUNT,
                    "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
                    "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
                },
                "same_33_case_denominator": {
                    **denominator,
                    "case_ids_by_treatment": case_ids_by_treatment,
                },
                "required_path_truth": {
                    "available_cases": sum(
                        bool(case.request.code_profile and case.request.code_profile.required_paths)
                        for case in package.eligible_cases
                    ),
                    "unavailable_cases": sum(
                        not bool(
                            case.request.code_profile and case.request.code_profile.required_paths
                        )
                        for case in package.eligible_cases
                    ),
                    "unavailable_semantics": (
                        "metrics remain unavailable where released Golden v2 has no typed path truth"
                    ),
                },
            },
            "metrics.json": {
                "schema_version": "code-c-b3-metrics-v1",
                "source": "production",
                "simulated": False,
                "required_metric_names": list(REQUIRED_REPORTING),
                "arms": {
                    arm: {
                        "run_id": persisted_runs[arm]["id"],
                        "metrics": arm_metrics[arm],
                    }
                    for arm in TREATMENT_ORDER
                },
                "acceptance": acceptance,
            },
            "graph-analysis.json": graph_analysis,
            "latency.json": {
                "schema_version": "code-c-b3-latency-v1",
                "ingest": {
                    "total_ms": prepared.base.ingest_time_ms,
                    "per_source_ms": prepared.base.source_ingest_time_ms,
                    "p95_ms": _p95(list(prepared.base.source_ingest_time_ms.values())),
                },
                "dense_index": {
                    "total_ms": sum(prepared.dense.index_time_ms),
                    "samples_ms": list(prepared.dense.index_time_ms),
                    "p95_ms": _p95(prepared.dense.index_time_ms),
                },
                "query": {
                    arm: {
                        "p95_ms": arm_metrics[arm]["latency_ms_p95"],
                        "graph_p95_ms": arm_metrics[arm]["graph_latency_ms_p95"],
                        "cases": [
                            {
                                "case_id": result["name"],
                                "latency_ms": result["latency_ms"],
                            }
                            for result in persisted_runs[arm]["results"]
                        ],
                    }
                    for arm in TREATMENT_ORDER
                },
            },
            "comparisons.json": comparisons,
            "failure-slices.json": {
                "schema_version": "code-c-b3-failure-slices-v1",
                "arms": {
                    arm: baseline._error_analysis_report(persisted_runs[arm], package)
                    for arm in TREATMENT_ORDER
                },
            },
        }
        for name, payload in reports.items():
            _write_json(artifact_dir / name, payload)
        _write_json(
            artifact_dir / "attempt-audit.json",
            {
                "schema_version": "code-c-b3-attempt-audit-v1",
                "status": "completed",
                "execution_mode": "qualified",
                "c4_02_gate_approved": True,
                "gate_authorization": gate,
                "primary_run_id": primary["id"],
                "run_ids": {arm: run["id"] for arm, run in persisted_runs.items()},
                "treatment_qualified": qualified,
                "acceptance_decision": acceptance["decision"],
                "started_at": min(str(run["started_at"]) for run in persisted_runs.values()),
                "completed_at": max(str(run["completed_at"]) for run in persisted_runs.values()),
                "network_used": False,
                "downloads_performed": False,
            },
        )
        first_security_scan = baseline.scan_artifact_security(artifact_dir)
        if not first_security_scan.get("clean"):
            raise CB3Error("C-B3 artifact failed the portable security scan")
        _write_json(
            artifact_dir / "security.json",
            {
                **first_security_scan,
                "schema_version": "code-c-b3-security-v1",
                "canonical_json": True,
                "portable_sqlite": True,
                "wal_shm_absent": True,
                "pyc_pycache_absent": True,
                "network_used": False,
                "downloads_performed": False,
            },
        )
        if not baseline.scan_artifact_security(artifact_dir).get("clean"):
            raise CB3Error("C-B3 final security report introduced a scan finding")

        artifact_files = _artifact_file_records(artifact_dir)
        manifest = {
            "schema_version": CB3_SCHEMA_VERSION,
            "runner_version": CB3_RUNNER_VERSION,
            "status": "QUALIFIED" if qualified else "NOT QUALIFIED",
            "execution_mode": "qualified",
            "c4_02_gate_approved": True,
            "gate_authorization": gate,
            "treatment_qualified": qualified,
            "run_id": primary["id"],
            "run_ids": {arm: run["id"] for arm, run in persisted_runs.items()},
            "display_key": primary["display_key"],
            "started_at": min(str(run["started_at"]) for run in persisted_runs.values()),
            "completed_at": max(str(run["completed_at"]) for run in persisted_runs.values()),
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
            "controls": controls,
            "production_identity": {
                **PRODUCTION_IDENTITY,
                "observed_hybrid": prepared.hybrid_descriptor.to_dict(),
                "observed_dense_publisher": prepared.publisher_descriptor.to_dict(),
                "observed_graph": prepared.graph_identity,
                "observed_reranker": prepared.reranker_descriptor.to_dict(),
            },
            "shared_input_fingerprint": shared_input_fingerprint,
            "snapshot": {
                "comparison_fingerprint": next(iter(comparison_fingerprints)),
                "case_membership_hash": package.membership_hash,
                "observed_index_generations": list(next(iter(observed_generations))),
                "graph_publications": list(prepared.graph_publications),
            },
            "acceptance": acceptance,
            "artifact_files": artifact_files,
            "artifact_set_hash": _fingerprint(artifact_files),
            "security": {
                "clean": True,
                "network_used": False,
                "downloads_performed": False,
                "portable_placeholders": ["<redacted-temp-path>"],
            },
        }
        _write_json(artifact_dir / "manifest.json", manifest)
        verify_artifact(artifact_dir, root=resolved_root)
        if _formal_database_state(resolved_root) != database_before:
            raise CB3Error("formal database changed during C-B3 artifact verification")
        if _tree_state(resolved_runs) != runs_before:
            raise CB3Error("evals/code/runs changed before the atomic C-B3 publish")

        final_dir = resolved_runs / token
        if final_dir.exists():
            raise CB3Error("C-B3 primary Run directory already exists")
        artifact_dir.rename(final_dir)

    assert final_dir is not None
    verify_artifact(final_dir, root=resolved_root)
    if _formal_database_state(resolved_root) != database_before:
        raise CB3Error("formal database changed during final C-B3 verify-only")
    runs_after = _tree_state(resolved_runs)
    _require_old_run_tree_unchanged(
        runs_before,
        runs_after,
        new_directory=final_dir.name,
    )
    if _existing_cb3_artifacts(resolved_runs) != [final_dir.name]:
        raise CB3Error("C-B3 did not leave exactly one immutable production artifact")
    return final_dir


def run_cb3(**kwargs: Any) -> Path:
    """Compatibility spelling; execution remains governed by ``execute_cb3``."""

    return execute_cb3(**kwargs)


def _require_safe_artifact_tree(directory: Path) -> None:
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise CB3Error(f"artifact symlink is forbidden: {relative}")
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            raise CB3Error(f"compiled Python artifact is forbidden: {relative}")
        if path.name.endswith(("-wal", "-shm")):
            raise CB3Error(f"SQLite sidecar is forbidden: {relative}")
    observed = {
        path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()
    }
    if observed != EXPECTED_ARTIFACT_ENTRIES:
        missing = sorted(EXPECTED_ARTIFACT_ENTRIES - observed)
        extra = sorted(observed - EXPECTED_ARTIFACT_ENTRIES)
        raise CB3Error(f"C-B3 artifact set mismatch: missing={missing}, extra={extra}")


def _require_canonical_json(directory: Path) -> None:
    for path in sorted(directory.glob("*.json")):
        value = _read_json(path)
        if path.read_bytes() != _pretty_json_bytes(value):
            raise CB3Error(f"artifact JSON is not canonical: {path.name}")


def _require_portable_sqlite(path: Path) -> None:
    if not path.is_file():
        raise CB3Error("portable evaluation.sqlite3 is missing")
    try:
        with sqlite3.connect(
            f"file:{path.resolve()}?mode=ro&immutable=1",
            uri=True,
        ) as database:
            integrity = database.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise CB3Error("portable evaluation.sqlite3 failed integrity_check")
    except (OSError, sqlite3.Error) as exc:
        raise CB3Error("unable to read portable evaluation.sqlite3") from exc


def _require_clean_text_scan(directory: Path) -> None:
    for path in sorted(directory.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        if _TEMPORARY_PATH_RE.search(text):
            raise CB3Error(f"temporary or host-local path found in {path.name}")
        if _SECRET_RE.search(text):
            raise CB3Error(f"credential-shaped assignment found in {path.name}")
    try:
        scan = baseline.scan_artifact_security(directory)
    except Exception as exc:
        raise CB3Error(f"artifact security scan failed: {exc}") from exc
    if not scan.get("clean"):
        raise CB3Error("artifact secret or temporary-path scan is not clean")


def _artifact_file_records(directory: Path) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "size": (directory / name).stat().st_size,
            "sha256": _sha256_file(directory / name),
        }
        for name in sorted(ARTIFACT_FILES)
    ]


def verify_artifact(
    directory: Path,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Verify a production C-B3 artifact without modifying it."""

    resolved_root = (root or repository_root()).resolve()
    resolved = directory.resolve()
    if not resolved.is_dir():
        raise CB3Error(f"C-B3 artifact directory does not exist: {directory}")
    database_before = _formal_database_state(resolved_root)
    runs_before = _tree_state(resolved_root / "evals" / "code" / "runs")

    _require_safe_artifact_tree(resolved)
    _require_canonical_json(resolved)
    _require_portable_sqlite(resolved / "evaluation.sqlite3")
    _require_clean_text_scan(resolved)

    manifest = _read_json(resolved / "manifest.json")
    attempt = _read_json(resolved / "attempt-audit.json")
    treatments = _read_json(resolved / "treatments.json")
    treatment_identity = treatments.get("production_identity") or {}
    authorization = _validate_artifact_authorization(
        manifest,
        attempt,
        treatment_identity,
    )
    package = validate_golden_package(resolved_root)
    if (
        manifest.get("schema_version") != CB3_SCHEMA_VERSION
        or manifest.get("runner_version") != CB3_RUNNER_VERSION
        or manifest.get("execution_mode") != "qualified"
    ):
        raise CB3Error("artifact lacks the production C4-02 Gate/identity contract")
    dataset = manifest.get("dataset") or {}
    if (
        dataset.get("id") != DATASET_ID
        or dataset.get("version") != DATASET_VERSION
        or dataset.get("package_hash") != PACKAGE_HASH
        or dataset.get("case_membership_hash") != package.membership_hash
        or dataset.get("total_cases") != EXPECTED_CASE_COUNT
        or dataset.get("eligible_cases") != EXPECTED_ELIGIBLE_COUNT
        or dataset.get("ineligible_cases") != EXPECTED_INELIGIBLE_COUNT
    ):
        raise CB3Error("artifact dataset identity or fixed 50/33/17 membership drifted")
    controls = manifest.get("controls") or {}
    if (controls.get("qualified_baseline") or {}).get("run_id") != CB0_QUALIFIED_RUN_ID or (
        controls.get("qualified_baseline") or {}
    ).get("treatment_qualified") is not True:
        raise CB3Error("artifact is not anchored to the unique qualified C-B0 Run")
    expected_audits = {
        "cb1_not_qualified_audit": CB1_AUDIT_RUN_ID,
        "cb2_not_qualified_audit": CB2_AUDIT_RUN_ID,
        "cb5_not_qualified_audit": CB5_AUDIT_RUN_ID,
    }
    for name, run_id in expected_audits.items():
        control = controls.get(name) or {}
        if (
            control.get("run_id") != run_id
            or control.get("treatment_qualified") is not False
            or control.get("qualification_use") != "forbidden"
        ):
            raise CB3Error(f"artifact misuses {name} as a qualified baseline")

    coverage = _read_json(resolved / "coverage.json")
    denominator = coverage.get("same_33_case_denominator") or {}
    case_ids_by_treatment = denominator.get("case_ids_by_treatment")
    if not isinstance(case_ids_by_treatment, dict):
        raise CB3Error("artifact lacks per-treatment denominator evidence")
    require_same_33_case_denominator(
        case_ids_by_treatment,
        expected_case_ids=_canonical_case_ids(package),
    )

    treatment_arms = treatments.get("arms") or {}
    run_ids = manifest.get("run_ids")
    if (
        treatments.get("order") != list(TREATMENT_ORDER)
        or set(treatment_arms) != set(TREATMENT_ORDER)
        or not isinstance(run_ids, dict)
        or set(run_ids) != set(TREATMENT_ORDER)
        or len(set(run_ids.values())) != len(TREATMENT_ORDER)
        or run_ids.get("graph_reranker") != manifest.get("run_id")
    ):
        raise CB3Error("artifact does not contain four distinct frozen treatment Runs")
    portable_store = EvaluationStore(baseline._ReadOnlySQLiteStore(resolved / "evaluation.sqlite3"))
    portable_runs: dict[str, dict[str, Any]] = {}
    comparison_fingerprints: set[str] = set()
    observed_generation_sets: set[tuple[str, ...]] = set()
    for arm in TREATMENT_ORDER:
        arm_run_id = run_ids[arm]
        treatment = treatment_arms[arm] or {}
        run = portable_store.get_run(arm_run_id)
        if (
            treatment.get("run_id") != arm_run_id
            or run is None
            or run.get("status") != "completed"
            or int(run.get("case_count") or 0) != EXPECTED_ELIGIBLE_COUNT
            or len(run.get("results") or []) != EXPECTED_ELIGIBLE_COUNT
            or sorted(str(result["name"]) for result in run["results"])
            != sorted(_canonical_case_ids(package))
        ):
            raise CB3Error(f"portable database does not retain a valid {arm} Run")
        if arm in {"typed_graph", "graph_reranker"}:
            if run.get("paired_graph_off_run_id") != run_ids["graph_off"]:
                raise CB3Error(f"{arm} is not paired to the same production graph-off Run")
        elif run.get("paired_graph_off_run_id") is not None:
            raise CB3Error(f"{arm} must not claim paired graph-only recovery")
        comparison_fingerprints.add(str(run["snapshot"]["comparison_fingerprint"]))
        observed_generation_sets.add(tuple(run["snapshot"]["observed_index_generations"]))
        portable_runs[arm] = run
    if len(comparison_fingerprints) != 1 or len(observed_generation_sets) != 1:
        raise CB3Error("portable treatment Runs do not share one production snapshot")

    metrics = _read_json(resolved / "metrics.json")
    if set(metrics.get("required_metric_names") or ()) != set(REQUIRED_REPORTING):
        raise CB3Error("artifact does not cover the frozen C-B3 metric contract")
    if metrics.get("simulated") is not False or metrics.get("source") != "production":
        raise CB3Error("fake, substituted, or simulated C-B3 metrics are forbidden")
    metric_arms = metrics.get("arms") or {}
    if set(metric_arms) != set(TREATMENT_ORDER):
        raise CB3Error("artifact metrics do not cover all four C-B3 arms")
    for arm in TREATMENT_ORDER:
        arm_record = metric_arms[arm] or {}
        records = arm_record.get("metrics") or {}
        if arm_record.get("run_id") != run_ids[arm] or set(records) != set(REQUIRED_REPORTING):
            raise CB3Error(f"{arm} metric identity or contract is incomplete")
        for record in records.values():
            if (
                record.get("membership_case_count") != EXPECTED_ELIGIBLE_COUNT
                or record.get("case_membership_hash") != package.membership_hash
                or record.get("status") not in {"available", "unavailable"}
            ):
                raise CB3Error(f"{arm} metric lacks fixed-membership availability evidence")
    acceptance = metrics.get("acceptance") or {}
    if (
        acceptance != (manifest.get("acceptance") or {})
        or acceptance.get("decision") not in {"qualified", "not-qualified"}
        or (acceptance.get("decision") == "qualified")
        != (manifest.get("treatment_qualified") is True)
        or manifest.get("status")
        != ("QUALIFIED" if manifest.get("treatment_qualified") is True else "NOT QUALIFIED")
    ):
        raise CB3Error("artifact acceptance decision is inconsistent")
    security = _read_json(resolved / "security.json")
    if (
        security.get("clean") is not True
        or security.get("network_used") is not False
        or security.get("downloads_performed") is not False
        or security.get("wal_shm_absent") is not True
        or security.get("pyc_pycache_absent") is not True
    ):
        raise CB3Error("artifact security declaration is not clean")
    graph_analysis = _read_json(resolved / "graph-analysis.json")
    if set(graph_analysis.get("arms") or {}) != set(TREATMENT_ORDER):
        raise CB3Error("artifact graph analysis does not cover all four arms")
    if graph_analysis["arms"]["graph_off"].get("traversal_enabled") is not False or any(
        graph_analysis["arms"][arm].get("traversal_enabled") is not True
        for arm in TREATMENT_ORDER[1:]
    ):
        raise CB3Error("artifact graph treatment semantics drifted")

    expected_files = _artifact_file_records(resolved)
    if manifest.get("artifact_files") != expected_files:
        raise CB3Error("artifact hashes or sizes do not match the canonical manifest")
    if manifest.get("artifact_set_hash") != _fingerprint(expected_files):
        raise CB3Error("artifact_set_hash does not match the portable artifact set")
    run_id = manifest.get("run_id")
    if (
        not isinstance(run_id, str)
        or not run_id.startswith(f"evaluation-run://{PROJECT_ID}/")
        or resolved.name != run_id.rsplit("/", 1)[-1]
    ):
        raise CB3Error("artifact directory is not the immutable Run identity")

    database_after = _formal_database_state(resolved_root)
    runs_after = _tree_state(resolved_root / "evals" / "code" / "runs")
    _require_repository_unchanged(
        database_before=database_before,
        database_after=database_after,
        runs_before=runs_before,
        runs_after=runs_after,
    )
    return {
        "status": "verified",
        "run_id": run_id,
        "authorization_mode": authorization["mode"],
        "authorization_decision": authorization["decision"],
        "treatment_qualified": manifest.get("treatment_qualified") is True,
        "acceptance_decision": acceptance["decision"],
        "status_label": manifest["status"],
        "dataset_package_hash": PACKAGE_HASH,
        "case_membership_hash": package.membership_hash,
        "treatment_count": len(TREATMENT_ORDER),
        "case_count_per_treatment": EXPECTED_ELIGIBLE_COUNT,
        "qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "audit_control_run_ids": [
            CB1_AUDIT_RUN_ID,
            CB2_AUDIT_RUN_ID,
            CB5_AUDIT_RUN_ID,
        ],
        "audit_controls_are_qualified_baselines": False,
        "artifact_set_hash": manifest["artifact_set_hash"],
        "manifest_canonical_hash": _fingerprint(manifest),
    }


def _resolve_artifact(value: str, runs_dir: Path) -> Path:
    candidate = Path(value)
    if candidate.is_dir():
        return candidate.resolve()
    nested = runs_dir / value
    if nested.is_dir():
        return nested.resolve()
    raise CB3Error(f"C-B3 artifact not found: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Code C-B3 typed-graph production evaluation")
    parser.add_argument("--repository-root", type=Path, default=repository_root())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "prepare",
        help="validate fixed anchors and print PREPARED NON-QUALIFIED without writes",
    )
    execute_parser = subparsers.add_parser(
        "execute",
        help="production-only execution after a future C4-02 completion Gate",
    )
    execute_parser.add_argument("--runs-dir", type=Path)
    execute_parser.add_argument("--c4-02-gate-approved", action="store_true")
    verify_parser = subparsers.add_parser(
        "verify",
        help="verify an existing immutable production C-B3 artifact",
    )
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()
    try:
        if args.command == "prepare":
            output = preparation_status(root=root)
        elif args.command == "execute":
            artifact = execute_cb3(
                root=root,
                runs_dir=args.runs_dir,
                c4_02_gate_approved=args.c4_02_gate_approved,
            )
            output = verify_artifact(artifact, root=root)
            output["artifact"] = artifact.relative_to(root).as_posix()
        else:
            runs_dir = (
                args.runs_dir.resolve()
                if args.runs_dir is not None
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
