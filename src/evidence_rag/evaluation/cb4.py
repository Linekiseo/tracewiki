"""Code C-B4 Python semantic-edge production evaluation harness.

Preparation remains read-only and produces no metric value.  Production
execution requires the exact ``semantic_edges_v1`` class, the final C5-02
authorization block, an isolated portable database, and a unique immutable Run.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sqlite3
import sys
import tempfile
import time
import tracemalloc
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from . import baseline, cb1, cb2, cb3, cb5
from .golden import (
    DATASET_ID,
    DATASET_VERSION,
    EXPECTED_CASE_COUNT,
    EXPECTED_ELIGIBLE_COUNT,
    EXPECTED_INELIGIBLE_COUNT,
    PACKAGE_HASH,
    GoldenPackage,
    repository_root,
    validate_golden_package,
)

CB4_SCHEMA_VERSION = "code-c-b4-semantic-edge-treatment-artifact-v1"
CB4_RUNNER_VERSION = "code-c-b4-runner-v1"
CB4_PREPARATION_VERSION = "code-c-b4-prepared-v1"

C5_GATE_RELATIVE_PATH = "docs/rag-optimization/development/reviews/06_CODE_C5_GATE_REVIEW.md"
C5_DEVELOPMENT_AUTHORIZATION = "C5-02 AUTHORIZED"
C5_02_ENGINEERING_DECISION = "C5-02 engineering PASS"
C5_02_GATE_DECISION = "C-B4 PRODUCTION EXECUTION AUTHORIZED"
C5_02_GATE_SCHEMA_VERSION = "code-c-b4-c5-02-authorization-v1"
LABEL_QUALIFICATION_SCHEMA_VERSION = "code-c-b4-label-qualification-v1"
EDGE_LABEL_RECORD_SCHEMA_VERSION = "code-c-b4-edge-label-record-v1"
EDGE_LABEL_DATASET_SCHEMA_VERSION = "code-c-b4-edge-label-dataset-v1"
EDGE_LABEL_EVIDENCE_REF_SCHEMA_VERSION = "code-c-b4-edge-label-evidence-ref-v1"
EDGE_LABEL_SET_VERSION = "code-c-b4-human-edge-labels-v1"
MAX_EDGE_LABEL_RECORDS = 2_000
PRODUCTION_OUTPUT_ATTESTATION_SCHEMA_VERSION = "code-c-b4-production-output-attestation-v1"
PRODUCTION_CASE_ATTESTATION_SCHEMA_VERSION = "code-c-b4-production-case-attestation-v1"
PRODUCTION_INPUT_FIXTURE_SCHEMA_VERSION = "code-c-b4-production-input-fixture-v1"
PRODUCTION_OUTPUT_PIPELINE_PROFILE = "golden-python30-tree-sitter-conservative-v1"
PRODUCTION_EXECUTION_PIPELINE_PROFILE = "golden-python30-three-arm-production-v1"
PRODUCTION_OUTPUT_EDGE_COUNT = 42
EXPECTED_PRODUCTION_OUTPUT_ATTESTATION_HASH = (
    "sha256:23feb1ee5ecf3f59cb7aad63b487bae61321da0fc140d64ae27dfb43b8b9f064"
)
ATTESTED_TREE_SITTER_PARSER_VERSION = "tree-sitter-python-v1"
ATTESTED_TREE_SITTER_RESOLVER_VERSION = "conservative-resolver-v1"
CB4_TREATMENTS_REPORT_VERSION = "code-c-b4-treatments-v1"
CB4_COVERAGE_REPORT_VERSION = "code-c-b4-coverage-v1"
CB4_EDGE_QUALITY_REPORT_VERSION = "code-c-b4-edge-quality-v1"
CB4_RETRIEVAL_REPORT_VERSION = "code-c-b4-retrieval-v1"
CB4_PERFORMANCE_REPORT_VERSION = "code-c-b4-performance-v1"
CB4_SECURITY_REPORT_VERSION = "code-c-b4-security-v1"
CB4_ATTEMPT_AUDIT_VERSION = "code-c-b4-attempt-audit-v1"
CB4_SQLITE_SCHEMA_VERSION = 1
C5_02_GATE_REQUIRED_EVIDENCE = (
    C5_02_ENGINEERING_DECISION,
    "P0 findings: 0",
    "P1 findings: 0",
    C5_02_GATE_DECISION,
)

CB0_QUALIFIED_RUN_ID = cb1.CB0_QUALIFIED_RUN_ID
CB0_ARTIFACT_DIRECTORY = cb1.CB0_ARTIFACT_DIRECTORY
CB1_AUDIT_RUN_ID = cb2.CB1_AUDIT_RUN_ID
CB1_AUDIT_ARTIFACT_DIRECTORY = cb2.CB1_AUDIT_ARTIFACT_DIRECTORY
CB2_AUDIT_RUN_ID = cb5.CB2_AUDIT_RUN_ID
CB2_AUDIT_ARTIFACT_DIRECTORY = cb5.CB2_AUDIT_ARTIFACT_DIRECTORY
CB3_AUDIT_RUN_ID = "evaluation-run://project-code-golden-v2/92f8d8e190f449bab9ba253227473419"
CB3_AUDIT_ARTIFACT_DIRECTORY = "92f8d8e190f449bab9ba253227473419"
CB5_AUDIT_RUN_ID = cb3.CB5_AUDIT_RUN_ID
CB5_AUDIT_ARTIFACT_DIRECTORY = cb3.CB5_AUDIT_ARTIFACT_DIRECTORY

PUBLIC_CODE_MODULE = "evidence_rag.rag.sources.code"
SEMANTIC_EDGE_MODULE = f"{PUBLIC_CODE_MODULE}.semantic_edges_v1"
REAL_SEMANTIC_EDGE_CLASS = "SemanticEdgeTreatment"
REAL_SEMANTIC_EDGE_RESULT_CLASS = "SemanticEdgeTreatmentResult"
REAL_SEMANTIC_EDGE_SCOPE_CLASS = "SemanticEdgeScope"
REAL_TREE_SITTER_EDGE_CLASS = "TreeSitterConservativeEdge"
REAL_SEMANTIC_EDGE_FUNCTION = "treat_python_semantic_edges"
REAL_SEMANTIC_EVALUATION_FUNCTION = "evaluate_semantic_edges"
REAL_SEMANTIC_EDGE_VERSION = "c5-python-semantic-edges-v1"

TREATMENT_ORDER = (
    "tree_sitter_conservative",
    "scip_semantic",
    "merged_policy",
)
PYTHON_ELIGIBLE_CASE_IDS = (
    "code-golden-v2-001",
    "code-golden-v2-002",
    "code-golden-v2-003",
    "code-golden-v2-004",
    "code-golden-v2-005",
    "code-golden-v2-006",
    "code-golden-v2-008",
    "code-golden-v2-009",
    "code-golden-v2-011",
    "code-golden-v2-012",
    "code-golden-v2-013",
    "code-golden-v2-014",
    "code-golden-v2-015",
    "code-golden-v2-016",
    "code-golden-v2-017",
    "code-golden-v2-018",
    "code-golden-v2-019",
    "code-golden-v2-021",
    "code-golden-v2-022",
    "code-golden-v2-023",
    "code-golden-v2-025",
    "code-golden-v2-026",
    "code-golden-v2-028",
    "code-golden-v2-029",
    "code-golden-v2-030",
    "code-golden-v2-031",
    "code-golden-v2-032",
    "code-golden-v2-033",
    "code-golden-v2-034",
    "code-golden-v2-035",
)
TREATMENT_MATRIX: dict[str, dict[str, Any]] = {
    "tree_sitter_conservative": {
        "label": "Tree-sitter conservative",
        "language": "python",
        "tree_sitter_edges": True,
        "scip_edges": False,
        "production_invocation": "SemanticEdgeTreatment.treat(scip_result=None)",
        "merge_policy": "none",
        "conflict_policy": "retain-existing-edge-and-diagnose-v1",
    },
    "scip_semantic": {
        "label": "SCIP semantic",
        "language": "python",
        "tree_sitter_edges": False,
        "scip_edges": True,
        "production_invocation": "SemanticEdgeTreatment.treat(tree_sitter_edges=())",
        "merge_policy": "none",
        "conflict_policy": "diagnose-without-overwrite-v1",
    },
    "merged_policy": {
        "label": "merged policy",
        "language": "python",
        "tree_sitter_edges": True,
        "scip_edges": True,
        "production_invocation": "SemanticEdgeTreatment.treat(SCIP + Tree-sitter)",
        "merge_policy": "edge-identity-union-provenance-set-v1",
        "conflict_policy": "retain-both-provenances-and-diagnose-v1",
    },
}

SEMANTIC_RELATION_POLICY = {
    "reference_occurrence": "REFERENCES",
    "call_occurrence_with_reliable_role": "CALLS",
    "implementation_relationship": "IMPLEMENTS",
    "override_relationship": "OVERRIDES",
    "definition_or_occurrence_implies_runtime_call": False,
    "same_edge_multiple_resolvers": "retain-provenance-set",
    "conflict": "do-not-overwrite-existing-edge; emit-diagnostic",
    "precision_priority": "precision-before-coverage",
    "fallback": "SCIP failure delegates to Tree-sitter conservative",
}

EDGE_PRECISION_MINIMUM_SAMPLE = 200
ZERO_LABEL_UNAVAILABLE_METRICS = frozenset(
    (
        "edge_precision",
        "edge_coverage",
        "unresolved_reduction",
        "graph_noise_rate@10",
        "graph_harmful_candidate_rate@10",
    )
)
STRUCTURAL_OBSERVATIONS = frozenset(
    (
        "scip_legal_edge_count",
        "scip_legal_edge_relations",
        "conflict_rate",
        "provenance_merge_rate",
    )
)
METRIC_CONTRACT: dict[str, dict[str, Any]] = {
    "edge_precision": {
        "direction": "higher",
        "evidence": "human edge labels only",
        "availability": "unavailable",
        "reason": "no C-B4 human edge labels have been collected",
        "quality_metric": True,
        "acceptance_eligible": False,
        "sample_count": 0,
        "minimum_sample_count": EDGE_PRECISION_MINIMUM_SAMPLE,
        "required_minimum": EDGE_PRECISION_MINIMUM_SAMPLE,
        "all_fixture": False,
        "small_fixture_policy": (
            "label and report all fixture edges below 200 as PROVISIONAL, never AVAILABLE"
        ),
        "fabricated_sample_count_forbidden": True,
    },
    "edge_coverage": {
        "direction": "higher",
        "evidence": "published legal edges / governed resolvable relation opportunities",
        "availability": "unavailable",
        "reason": "zero real human edge labels cannot establish quality coverage",
        "quality_metric": True,
        "acceptance_eligible": False,
    },
    "scip_legal_edge_count": {
        "direction": "descriptive",
        "evidence": "SCIP-derived edges accepted by the registered edge ontology",
        "classification": "structural-observation",
        "quality_metric": False,
        "acceptance_eligible": False,
    },
    "scip_legal_edge_relations": {
        "direction": "descriptive",
        "evidence": "accepted SCIP edge counts grouped by registered relation",
        "classification": "structural-observation",
        "quality_metric": False,
        "acceptance_eligible": False,
    },
    "unresolved_reduction": {
        "direction": "higher",
        "evidence": "paired unresolved opportunities, Tree-sitter minus treatment",
        "availability": "unavailable",
        "reason": "zero real human edge labels cannot establish quality unresolved reduction",
        "quality_metric": True,
        "acceptance_eligible": False,
    },
    "conflict_rate": {
        "direction": "lower",
        "evidence": "diagnosed conflicting edge identities / merge candidates",
        "classification": "structural-observation",
        "quality_metric": False,
        "acceptance_eligible": False,
    },
    "provenance_merge_rate": {
        "direction": "descriptive",
        "evidence": "overlapping edges retaining both Tree-sitter and SCIP provenance",
        "classification": "structural-observation",
        "quality_metric": False,
        "acceptance_eligible": False,
    },
    "required_path_recall": {
        "direction": "higher",
        "evidence": "Golden required typed paths recovered on the Python slice",
    },
    "graph_noise_rate@10": {
        "direction": "lower",
        "evidence": "non-relevant graph-exclusive candidates / graph-exclusive candidates",
        "availability": "unavailable",
        "reason": "zero real human edge labels cannot establish graph noise",
        "quality_metric": True,
        "acceptance_eligible": False,
    },
    "graph_harmful_candidate_rate@10": {
        "direction": "lower",
        "evidence": "harmful graph-attributed candidates / graph-attributed candidates",
        "availability": "unavailable",
        "reason": "zero real human edge labels cannot establish semantic-edge graph harm",
        "quality_metric": True,
        "acceptance_eligible": False,
    },
    "ingest_time_ms": {
        "direction": "lower",
        "evidence": "per-input end-to-end isolated ingestion duration",
    },
    "peak_memory_bytes": {
        "direction": "lower",
        "evidence": "peak isolated-process memory with measurement method recorded",
    },
    "ingest_latency_ms_p95": {
        "direction": "lower",
        "evidence": "all per-input ingestion latency samples",
    },
    "retrieval_latency_ms_p95": {
        "direction": "lower",
        "evidence": "all Python-slice query latency samples",
        "same_case_denominator_required": True,
    },
    "expected_locator_recall@10": {
        "direction": "higher",
        "evidence": "Evaluation V2 locator annotations",
        "same_case_denominator_required": True,
    },
    "entity_recall@10": {
        "direction": "higher",
        "evidence": "Evaluation V2 entity annotations",
        "same_case_denominator_required": True,
    },
    "mrr@10": {
        "direction": "higher",
        "evidence": "Evaluation V2 graded judgments",
        "same_case_denominator_required": True,
    },
    "ndcg@10": {
        "direction": "higher",
        "evidence": "Evaluation V2 graded judgments",
        "same_case_denominator_required": True,
    },
    "harmful_candidate_rate@10": {
        "direction": "lower",
        "evidence": "Evaluation V2 harmful judgments",
        "same_case_denominator_required": True,
    },
    "unauthorized_candidate_rate": {
        "direction": "lower",
        "evidence": "caller ACL evaluated at every returned endpoint",
        "same_case_denominator_required": True,
    },
    "wrong_version_rate": {
        "direction": "lower",
        "evidence": "Evaluation V2 required version annotations",
        "same_case_denominator_required": True,
    },
}
REQUIRED_REPORTING = tuple(METRIC_CONTRACT)

REPORT_FILES = (
    "treatments.json",
    "coverage.json",
    "edge-quality.json",
    "retrieval.json",
    "performance.json",
    "security.json",
)
ARTIFACT_FILES = ("attempt-audit.json", "evaluation.sqlite3", *REPORT_FILES)
EXPECTED_ARTIFACT_ENTRIES = frozenset(("manifest.json", *ARTIFACT_FILES))

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EDGE_ID_RE = re.compile(r"^code-edge-v1:[0-9a-f]{64}$")
_CASE_ID_RE = re.compile(r"^code-golden-v2-[0-9]{3}$")
_ANNOTATOR_ID_RE = re.compile(r"^annotator:[a-z0-9][a-z0-9._-]{0,63}$")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_TEMPORARY_PATH_RE = re.compile(
    r"(?:/private)?/(?:tmp|var/folders)/|"
    r"(?:^|[\"'\s])(?:file://)?[A-Za-z]:[\\/](?:Temp|Users)[\\/]",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{8,}"
)
_GATE_ENGINEERING_RE = re.compile(
    r"^c5-02\s+engineering\s+(pass|fail)$",
    re.IGNORECASE,
)
_GATE_FINDINGS_RE = re.compile(
    r"^p([01])\s+findings\s*:\s*([0-9]+)$",
    re.IGNORECASE,
)
_GATE_EXECUTION_RE = re.compile(
    r"^c-b4\s+production\s+execution\s+(not\s+authorized|authorized)$",
    re.IGNORECASE,
)
_GATE_TIMESTAMP_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\b")


class CB4Error(RuntimeError):
    """The C-B4 preparation, execution, or verification boundary failed closed."""


@runtime_checkable
class SemanticEdgeTreatmentProtocol(Protocol):
    """Narrow seam for the in-progress production semantic-edge component.

    The harness deliberately avoids importing production request/result models
    at module import time.  Qualification additionally requires exact class
    identity; structural protocol conformance alone is never sufficient.
    """

    def treat(
        self,
        *,
        scip_result: Any | None,
        tree_sitter_edges: Sequence[Any],
        scope: Any,
        language: str = "python",
    ) -> Any:
        """Apply the production Python semantic-edge transform and merge policy."""


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
        raise CB4Error(f"unable to read C-B4 JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise CB4Error(f"C-B4 JSON must be an object: {path.name}")
    return value


def _path_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "size": None,
            "mtime_ns": None,
            "sha256": None,
        }
    if not path.is_file() or path.is_symlink():
        raise CB4Error(f"protected path is not a regular file: {path}")
    stat = path.stat()
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256_file(path),
    }


def _formal_database_state(root: Path) -> dict[str, Any]:
    database = (root / "var" / "evidence-rag.sqlite3").resolve()
    return {
        "path": "var/evidence-rag.sqlite3",
        **_path_state(database),
        "sidecars": {
            suffix: _path_state(Path(f"{database}{suffix}")) for suffix in ("-wal", "-shm")
        },
    }


def _tree_state(path: Path) -> tuple[tuple[str, int, int, str], ...]:
    if not path.exists():
        return ()
    if not path.is_dir() or path.is_symlink():
        raise CB4Error(f"protected Run tree is not a regular directory: {path}")
    records: list[tuple[str, int, int, str]] = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise CB4Error(f"protected Run tree contains a symlink: {item}")
        if item.is_file():
            stat = item.stat()
            records.append(
                (
                    item.relative_to(path).as_posix(),
                    stat.st_size,
                    stat.st_mtime_ns,
                    _sha256_file(item),
                )
            )
    return tuple(records)


def _require_repository_unchanged(
    *,
    database_before: Mapping[str, Any],
    database_after: Mapping[str, Any],
    runs_before: Sequence[tuple[str, int, int, str]],
    runs_after: Sequence[tuple[str, int, int, str]],
) -> None:
    if dict(database_before) != dict(database_after):
        raise CB4Error(
            "formal var/evidence-rag.sqlite3 hash/size/mtime changed during C-B4 read-only work"
        )
    if tuple(runs_before) != tuple(runs_after):
        raise CB4Error("evals/code/runs hash/size/mtime changed during C-B4 read-only work")


def _case_languages(package: GoldenPackage) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for case in package.eligible_cases:
        raw_languages = case.request.code_profile.query_profile.get("languages") or []
        languages = tuple(str(value).casefold() for value in raw_languages)
        if not languages:
            raise CB4Error(f"Golden case has no language scope: {case.canonical_id}")
        result[case.canonical_id] = languages
    return result


def _python_case_ids(package: GoldenPackage) -> tuple[str, ...]:
    languages = _case_languages(package)
    case_ids = tuple(
        case.canonical_id
        for case in package.eligible_cases
        if languages[case.canonical_id] == ("python",)
    )
    if case_ids != PYTHON_ELIGIBLE_CASE_IDS:
        raise CB4Error("released Golden v2 Python eligible membership is not the fixed 30 cases")
    return case_ids


def _unavailable_language_cases(package: GoldenPackage) -> dict[str, tuple[str, ...]]:
    languages = _case_languages(package)
    result = {
        language: tuple(
            case.canonical_id
            for case in package.eligible_cases
            if language in languages[case.canonical_id]
        )
        for language in ("javascript", "typescript")
    }
    if len(result["javascript"]) != 1 or len(result["typescript"]) != 2:
        raise CB4Error("released Golden v2 JS/TS membership differs from the fixed 1/2 split")
    return result


def require_same_python_case_denominator(
    memberships: Mapping[str, Sequence[str]],
    *,
    expected_case_ids: Sequence[str],
) -> dict[str, Any]:
    """Require all three treatments to use exactly the same 30 Python cases."""

    expected = tuple(expected_case_ids)
    if len(expected) != 30 or len(set(expected)) != 30:
        raise CB4Error("expected C-B4 denominator must contain 30 unique Python cases")
    if set(memberships) != set(TREATMENT_ORDER):
        raise CB4Error("C-B4 denominator must include exactly the three frozen treatment arms")
    normalized: dict[str, tuple[str, ...]] = {}
    for arm in TREATMENT_ORDER:
        observed = tuple(memberships[arm])
        if len(observed) != 30 or len(set(observed)) != 30:
            raise CB4Error(f"{arm} does not contain 30 unique Python cases")
        if observed != expected:
            raise CB4Error(f"{arm} differs from the fixed Python case membership or order")
        normalized[arm] = observed
    membership_hash = _fingerprint(list(expected))
    return {
        "scope": "released-golden-v2-python-eligible",
        "case_count": 30,
        "canonical_case_ids": list(expected),
        "case_membership_hash": membership_hash,
        "treatments": {
            arm: {
                "case_count": 30,
                "case_membership_hash": membership_hash,
            }
            for arm in TREATMENT_ORDER
        },
    }


@dataclass(frozen=True, slots=True)
class EdgeLabelRecord:
    """One portable, content-addressable human label for an exact production edge."""

    case_id: str
    edge_id: str
    source_entity_id: str
    source_entity_type: str
    target_entity_id: str
    target_entity_type: str
    project_id: str
    repository_id: str
    generation_id: str
    stable_version: str
    acl_ref: str
    relation: str
    production_edge_payload_hash: str
    gold_truth: str
    annotator_id: str
    annotation_provenance: str
    label_set_version: str = EDGE_LABEL_SET_VERSION
    eligible_status: str = "eligible"

    def __post_init__(self) -> None:
        for name in (
            "case_id",
            "edge_id",
            "source_entity_id",
            "source_entity_type",
            "target_entity_id",
            "target_entity_type",
            "project_id",
            "repository_id",
            "generation_id",
            "stable_version",
            "acl_ref",
            "relation",
            "production_edge_payload_hash",
            "gold_truth",
            "annotator_id",
            "annotation_provenance",
            "label_set_version",
            "eligible_status",
        ):
            _require_safe_label_text(name, getattr(self, name))
        if not _CASE_ID_RE.fullmatch(self.case_id):
            raise CB4Error("edge label case_id is not a canonical Golden v2 id")
        if not _EDGE_ID_RE.fullmatch(self.edge_id):
            raise CB4Error("edge label edge_id is not a canonical production id")
        module = _production_module()
        entity_type = getattr(module, "CodeGraphEntityType", None) if module is not None else None
        if not isinstance(entity_type, type):
            raise CB4Error("production semantic-edge entity ontology is unavailable")
        try:
            entity_type(self.source_entity_type)
            entity_type(self.target_entity_type)
        except (TypeError, ValueError) as exc:
            raise CB4Error("edge label endpoint type is not in the production ontology") from exc
        if self.relation not in {"REFERENCES", "CALLS", "IMPLEMENTS", "OVERRIDES"}:
            raise CB4Error("edge label relation is not in the semantic-edge ontology")
        if not _SHA256_RE.fullmatch(self.production_edge_payload_hash):
            raise CB4Error("edge label production payload hash must be a content SHA-256")
        if self.gold_truth not in {"correct", "incorrect", "graph_noise"}:
            raise CB4Error("uncertain or unknown gold truth is not a valid quality label")
        if not _ANNOTATOR_ID_RE.fullmatch(self.annotator_id):
            raise CB4Error("edge label annotator must use a bounded pseudonymous id")
        if self.label_set_version != EDGE_LABEL_SET_VERSION:
            raise CB4Error("edge label-set version is not the frozen C-B4 version")
        if self.eligible_status != "eligible":
            raise CB4Error("ineligible edge labels cannot enter the quality sample")
        if not _SHA256_RE.fullmatch(self.annotation_provenance):
            raise CB4Error("edge label provenance must be a content SHA-256")
        expected_edge_id = _canonical_edge_id(
            relation=self.relation,
            source_entity_id=self.source_entity_id,
            source_entity_type=self.source_entity_type,
            target_entity_id=self.target_entity_id,
            target_entity_type=self.target_entity_type,
            project_id=self.project_id,
            repository_id=self.repository_id,
            generation_id=self.generation_id,
            stable_version=self.stable_version,
            acl_ref=self.acl_ref,
        )
        if self.edge_id != expected_edge_id:
            raise CB4Error("edge label id does not match its canonical production edge")

    @classmethod
    def from_production(
        cls,
        *,
        case_id: str,
        edge: Any,
        label: Any,
        annotation_provenance: str,
    ) -> EdgeLabelRecord:
        """Copy immutable fields from exact production edge and label objects."""

        module = _production_module()
        edge_type = getattr(module, "SemanticEdge", None) if module is not None else None
        _, label_type, _ = _production_label_contract()
        if (
            not isinstance(edge_type, type)
            or edge_type.__module__ != SEMANTIC_EDGE_MODULE
            or type(edge) is not edge_type
            or type(label) is not label_type
            or label.edge_id != edge.edge_id
        ):
            raise CB4Error("edge label record requires matching exact production objects")
        source = edge.source
        target = edge.target
        if source.scope_identity() != target.scope_identity():
            raise CB4Error("edge label endpoints do not share one production scope")
        return cls(
            case_id=case_id,
            edge_id=edge.edge_id,
            source_entity_id=source.entity_id,
            source_entity_type=source.entity_type.value,
            target_entity_id=target.entity_id,
            target_entity_type=target.entity_type.value,
            project_id=source.project_id,
            repository_id=source.repository_id,
            generation_id=source.generation_id,
            stable_version=source.stable_version,
            acl_ref=source.acl_ref,
            relation=edge.edge_type.value,
            production_edge_payload_hash=_fingerprint(_production_edge_payload(edge)),
            gold_truth=label.verdict.value,
            annotator_id=label.reviewer,
            annotation_provenance=annotation_provenance,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> EdgeLabelRecord:
        expected = {
            "schema_version",
            "case_id",
            "edge_id",
            "source",
            "target",
            "scope",
            "relation",
            "production_edge_payload_hash",
            "gold_truth",
            "annotator_id",
            "annotation_provenance",
            "label_set_version",
            "eligible_status",
        }
        if (
            set(value) != expected
            or value.get("schema_version") != EDGE_LABEL_RECORD_SCHEMA_VERSION
        ):
            raise CB4Error("edge label record does not match the frozen schema")
        source = value.get("source")
        target = value.get("target")
        scope = value.get("scope")
        if (
            not isinstance(source, Mapping)
            or set(source) != {"entity_id", "entity_type"}
            or not isinstance(target, Mapping)
            or set(target) != {"entity_id", "entity_type"}
            or not isinstance(scope, Mapping)
            or set(scope)
            != {
                "project_id",
                "repository_id",
                "generation_id",
                "stable_version",
                "acl_ref",
            }
        ):
            raise CB4Error("edge label endpoints or scope do not match the frozen schema")
        return cls(
            case_id=value["case_id"],
            edge_id=value["edge_id"],
            source_entity_id=source["entity_id"],
            source_entity_type=source["entity_type"],
            target_entity_id=target["entity_id"],
            target_entity_type=target["entity_type"],
            project_id=scope["project_id"],
            repository_id=scope["repository_id"],
            generation_id=scope["generation_id"],
            stable_version=scope["stable_version"],
            acl_ref=scope["acl_ref"],
            relation=value["relation"],
            production_edge_payload_hash=value["production_edge_payload_hash"],
            gold_truth=value["gold_truth"],
            annotator_id=value["annotator_id"],
            annotation_provenance=value["annotation_provenance"],
            label_set_version=value["label_set_version"],
            eligible_status=value["eligible_status"],
        )

    @classmethod
    def from_attested_output(
        cls,
        *,
        case_id: str,
        output: Mapping[str, Any],
        gold_truth: str,
        annotator_id: str,
        annotation_provenance: str,
    ) -> EdgeLabelRecord:
        """Create a label only from one canonical attested production output."""

        canonical_output = _validate_production_edge_payload(output)
        source = canonical_output["source"]
        target = canonical_output["target"]
        scope = canonical_output["scope"]
        return cls(
            case_id=case_id,
            edge_id=canonical_output["edge_id"],
            source_entity_id=source["entity_id"],
            source_entity_type=source["entity_type"],
            target_entity_id=target["entity_id"],
            target_entity_type=target["entity_type"],
            project_id=scope["project_id"],
            repository_id=scope["repository_id"],
            generation_id=scope["generation_id"],
            stable_version=scope["stable_version"],
            acl_ref=scope["acl_ref"],
            relation=canonical_output["relation"],
            production_edge_payload_hash=_fingerprint(canonical_output),
            gold_truth=gold_truth,
            annotator_id=annotator_id,
            annotation_provenance=annotation_provenance,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EDGE_LABEL_RECORD_SCHEMA_VERSION,
            "case_id": self.case_id,
            "edge_id": self.edge_id,
            "source": {
                "entity_id": self.source_entity_id,
                "entity_type": self.source_entity_type,
            },
            "target": {
                "entity_id": self.target_entity_id,
                "entity_type": self.target_entity_type,
            },
            "scope": {
                "project_id": self.project_id,
                "repository_id": self.repository_id,
                "generation_id": self.generation_id,
                "stable_version": self.stable_version,
                "acl_ref": self.acl_ref,
            },
            "relation": self.relation,
            "production_edge_payload_hash": self.production_edge_payload_hash,
            "gold_truth": self.gold_truth,
            "annotator_id": self.annotator_id,
            "annotation_provenance": self.annotation_provenance,
            "label_set_version": self.label_set_version,
            "eligible_status": self.eligible_status,
        }


def _require_safe_label_text(name: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or not value.isascii()
        or any(ord(character) < 32 for character in value)
        or value.startswith(("/", "\\", "~/", "file://"))
        or _WINDOWS_ABSOLUTE_PATH_RE.match(value)
        or _TEMPORARY_PATH_RE.search(value)
        or _SECRET_RE.search(value)
    ):
        raise CB4Error(f"edge label {name} is unsafe or non-portable")


def _canonical_edge_id(
    *,
    relation: str,
    source_entity_id: str,
    source_entity_type: str,
    target_entity_id: str,
    target_entity_type: str,
    project_id: str,
    repository_id: str,
    generation_id: str,
    stable_version: str,
    acl_ref: str,
) -> str:
    source = (
        source_entity_id,
        source_entity_type,
        project_id,
        repository_id,
        generation_id,
        stable_version,
        acl_ref,
    )
    target = (
        target_entity_id,
        target_entity_type,
        project_id,
        repository_id,
        generation_id,
        stable_version,
        acl_ref,
    )
    payload = {"edge_type": relation, "source": source, "target": target}
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "code-edge-v1:" + hashlib.sha256(encoded).hexdigest()


def _production_edge_payload(edge: Any) -> dict[str, Any]:
    """Serialize every immutable production edge field used by the attestation."""

    module = _production_module()
    edge_type = getattr(module, "SemanticEdge", None) if module is not None else None
    if (
        not isinstance(edge_type, type)
        or edge_type.__module__ != SEMANTIC_EDGE_MODULE
        or type(edge) is not edge_type
    ):
        raise CB4Error("production output attestation requires exact SemanticEdge values")
    source = edge.source
    target = edge.target
    return {
        "edge_id": edge.edge_id,
        "canonical_hash": edge.canonical_hash,
        "relation": edge.edge_type.value,
        "source": {
            "entity_id": source.entity_id,
            "entity_type": source.entity_type.value,
            "locator": source.locator,
        },
        "target": {
            "entity_id": target.entity_id,
            "entity_type": target.entity_type.value,
            "locator": target.locator,
        },
        "scope": {
            "project_id": source.project_id,
            "repository_id": source.repository_id,
            "generation_id": source.generation_id,
            "stable_version": source.stable_version,
            "acl_ref": source.acl_ref,
        },
        "confidence": edge.confidence,
        "confidence_semantics": edge.confidence_semantics,
        "derivation_layer": edge.derivation_layer.value,
        "provenances": [
            {
                "derivation": provenance.derivation.value,
                "producer_version": provenance.producer_version,
                "artifact_id": provenance.artifact_id,
                "content_sha256": provenance.content_sha256,
            }
            for provenance in edge.provenances
        ],
        "evidence_locators": list(edge.evidence_locators),
        "review_status": edge.review_status.value,
        "fact_status": edge.fact_status.value,
    }


def _validate_production_edge_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "edge_id",
        "canonical_hash",
        "relation",
        "source",
        "target",
        "scope",
        "confidence",
        "confidence_semantics",
        "derivation_layer",
        "provenances",
        "evidence_locators",
        "review_status",
        "fact_status",
    }
    if set(value) != expected:
        raise CB4Error("attested production edge does not match the frozen payload schema")
    source = value.get("source")
    target = value.get("target")
    scope = value.get("scope")
    if (
        not isinstance(source, Mapping)
        or set(source) != {"entity_id", "entity_type", "locator"}
        or not isinstance(target, Mapping)
        or set(target) != {"entity_id", "entity_type", "locator"}
        or not isinstance(scope, Mapping)
        or set(scope)
        != {
            "project_id",
            "repository_id",
            "generation_id",
            "stable_version",
            "acl_ref",
        }
    ):
        raise CB4Error("attested production edge endpoint or scope is malformed")
    for name, text in (
        ("edge_id", value.get("edge_id")),
        ("canonical_hash", value.get("canonical_hash")),
        ("relation", value.get("relation")),
        ("source_entity_id", source.get("entity_id")),
        ("source_entity_type", source.get("entity_type")),
        ("source_locator", source.get("locator")),
        ("target_entity_id", target.get("entity_id")),
        ("target_entity_type", target.get("entity_type")),
        ("target_locator", target.get("locator")),
        *((name, scope.get(name)) for name in sorted(scope)),
        ("confidence_semantics", value.get("confidence_semantics")),
        ("derivation_layer", value.get("derivation_layer")),
        ("review_status", value.get("review_status")),
        ("fact_status", value.get("fact_status")),
    ):
        _require_safe_label_text(name, text)
    relation = value["relation"]
    if relation not in {"REFERENCES", "CALLS", "IMPLEMENTS", "OVERRIDES"}:
        raise CB4Error("attested production edge relation is not registered")
    expected_edge_id = _canonical_edge_id(
        relation=relation,
        source_entity_id=source["entity_id"],
        source_entity_type=source["entity_type"],
        target_entity_id=target["entity_id"],
        target_entity_type=target["entity_type"],
        project_id=scope["project_id"],
        repository_id=scope["repository_id"],
        generation_id=scope["generation_id"],
        stable_version=scope["stable_version"],
        acl_ref=scope["acl_ref"],
    )
    if value["edge_id"] != expected_edge_id or value[
        "canonical_hash"
    ] != expected_edge_id.removeprefix("code-edge-v1:"):
        raise CB4Error("attested production edge id does not match its complete payload")
    confidence = value.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= confidence <= 1.0
    ):
        raise CB4Error("attested production edge confidence is invalid")
    provenances = value.get("provenances")
    if not isinstance(provenances, list) or not provenances:
        raise CB4Error("attested production edge lacks provenance")
    for provenance in provenances:
        if not isinstance(provenance, Mapping) or set(provenance) != {
            "derivation",
            "producer_version",
            "artifact_id",
            "content_sha256",
        }:
            raise CB4Error("attested production edge provenance is malformed")
        for name, text in provenance.items():
            if name == "content_sha256" and text == "":
                continue
            _require_safe_label_text(f"provenance_{name}", text)
    locators = value.get("evidence_locators")
    if (
        not isinstance(locators, list)
        or not locators
        or any(not isinstance(locator, str) for locator in locators)
        or locators != sorted(set(locators))
    ):
        raise CB4Error("attested production edge locators are not canonical")
    for locator in locators:
        _require_safe_label_text("evidence_locator", locator)
    canonical = {
        **dict(value),
        "source": dict(source),
        "target": dict(target),
        "scope": dict(scope),
        "provenances": [dict(item) for item in provenances],
        "evidence_locators": list(locators),
    }
    return canonical


def _golden_case_fixture(case: Any) -> dict[str, Any]:
    profile = case.request.code_profile
    query_profile = profile.query_profile
    repositories = tuple(str(value) for value in query_profile.get("repository_ids") or ())
    acl_refs = tuple(str(value) for value in query_profile.get("acl_refs") or ())
    stable_version = str(query_profile.get("commit") or case.request.required_version or "")
    expected_locators = tuple(
        {
            "entity_id": str(locator["entity_id"]),
            "repository_id": str(locator["repository_id"]),
            "ref": str(locator["ref"]),
            "path": str(locator["path"]),
            "start_line": int(locator["start_line"]),
            "end_line": int(locator["end_line"]),
        }
        for locator in profile.expected_locators
    )
    if (
        len(repositories) != 1
        or len(acl_refs) != 1
        or not stable_version
        or any(
            locator["repository_id"] != repositories[0] or locator["ref"] != stable_version
            for locator in expected_locators
        )
    ):
        raise CB4Error(f"Golden case production fixture is ambiguous: {case.canonical_id}")
    seed = {
        "schema_version": PRODUCTION_INPUT_FIXTURE_SCHEMA_VERSION,
        "case_id": case.canonical_id,
        "golden_package_hash": PACKAGE_HASH,
        "pipeline_profile": PRODUCTION_OUTPUT_PIPELINE_PROFILE,
        "source_profile": {
            "source_class": case.source_class,
            "query_style": case.query_style,
            "slices": list(case.slices),
        },
        "project_id": case.request.project_id,
        "repository_id": repositories[0],
        "stable_version": stable_version,
        "acl_ref": acl_refs[0],
        "expected_locators": list(expected_locators),
    }
    seed_hash = _fingerprint(seed)
    generation_id = "generation:cb4:" + seed_hash.removeprefix("sha256:")[:32]
    tree_inputs = []
    for locator in expected_locators:
        base = locator["entity_id"].split("#", 1)[0]
        tree_inputs.append(
            {
                "relation": "REFERENCES",
                "source": {
                    "entity_id": f"{base}#file",
                    "entity_type": "FileVersion",
                    "locator": f"{base}#file",
                },
                "target": {
                    "entity_id": locator["entity_id"],
                    "entity_type": "CodeSymbol",
                    "locator": locator["entity_id"],
                },
                "evidence_locators": [locator["entity_id"]],
                "confidence": 0.95,
                "parser_version": ATTESTED_TREE_SITTER_PARSER_VERSION,
                "resolver_version": ATTESTED_TREE_SITTER_RESOLVER_VERSION,
            }
        )
    return {
        **seed,
        "generation_id": generation_id,
        "tree_sitter_inputs": tree_inputs,
    }


def _execute_production_case_fixture(
    *,
    fixture: Mapping[str, Any],
    component: Any,
) -> Any:
    scope, tree_edges = _production_case_inputs(fixture)
    return component.treat(
        scip_result=None,
        tree_sitter_edges=tree_edges,
        scope=scope,
        language="python",
    )


def _production_case_inputs(fixture: Mapping[str, Any]) -> tuple[Any, tuple[Any, ...]]:
    module = _production_module()
    if module is None:
        raise CB4Error("production semantic-edge module is unavailable")
    scope = module.SemanticEdgeScope(
        project_id=fixture["project_id"],
        repository_id=fixture["repository_id"],
        generation_id=fixture["generation_id"],
        stable_version=fixture["stable_version"],
        acl_ref=fixture["acl_ref"],
    )
    tree_edges = []
    for item in fixture["tree_sitter_inputs"]:
        source = module.SemanticEdgeEndpoint(
            **item["source"],
            project_id=scope.project_id,
            repository_id=scope.repository_id,
            generation_id=scope.generation_id,
            stable_version=scope.stable_version,
            acl_ref=scope.acl_ref,
        )
        target = module.SemanticEdgeEndpoint(
            **item["target"],
            project_id=scope.project_id,
            repository_id=scope.repository_id,
            generation_id=scope.generation_id,
            stable_version=scope.stable_version,
            acl_ref=scope.acl_ref,
        )
        tree_edges.append(
            module.TreeSitterConservativeEdge(
                edge_type=item["relation"],
                source=source,
                target=target,
                confidence=item["confidence"],
                evidence_locators=tuple(item["evidence_locators"]),
                parser_version=item["parser_version"],
                resolver_version=item["resolver_version"],
            )
        )
    return scope, tuple(tree_edges)


def _scip_module() -> Any:
    try:
        return importlib.import_module(f"{PUBLIC_CODE_MODULE}.scip_v1")
    except Exception as exc:
        raise CB4Error("production SCIP contract is unavailable") from exc


def _build_scip_case_fixture(fixture: Mapping[str, Any], scope: Any) -> Any:
    """Build one deterministic, persistence-free SCIP consumer result."""

    module = _scip_module()
    grouped: dict[str, list[tuple[Mapping[str, Any], Any]]] = {}
    linked_occurrences = []
    for locator in sorted(
        fixture["expected_locators"],
        key=lambda item: (item["path"], item["start_line"], item["entity_id"]),
    ):
        path = locator["path"]
        symbol_digest = hashlib.sha256(locator["entity_id"].encode("utf-8")).hexdigest()
        symbol = f"scip-python python cb4 1.0.0 {path}/symbol-{symbol_digest[:24]}."
        entity = module.ScipEntityRef(
            entity_id=locator["entity_id"],
            entity_type="CodeSymbol",
            project_id=scope.project_id,
            repository_id=scope.repository_id,
            generation_id=scope.generation_id,
            acl_ref=scope.acl_ref,
            relative_path=path,
            scip_symbol=symbol,
        )
        occurrence = module.ScipOccurrence(
            relative_path=path,
            source_range=module.ScipRange(
                start=module.ScipPosition(locator["start_line"], 1),
                end=module.ScipPosition(locator["end_line"], 2),
            ),
            symbol=symbol,
            symbol_roles=0,
            kind=module.ScipOccurrenceKind.REFERENCE,
            is_local=False,
        )
        linked = module.ScipLinkedOccurrence(
            occurrence=occurrence,
            symbol_link=module.ScipLink(
                status=module.ScipLinkStatus.RESOLVED,
                entity=entity,
            ),
        )
        grouped.setdefault(path, []).append((locator, linked))
        linked_occurrences.append(linked)

    linked_documents = []
    for path in sorted(grouped):
        items = grouped[path]
        base = items[0][0]["entity_id"].split("#", 1)[0]
        if any(item[0]["entity_id"].split("#", 1)[0] != base for item in items):
            raise CB4Error("SCIP fixture path maps to multiple file identities")
        document = module.ScipDocument(
            relative_path=path,
            language="python",
            position_encoding=1,
            occurrences=tuple(item[1].occurrence for item in items),
            symbols=(),
        )
        file_entity = module.ScipEntityRef(
            entity_id=f"{base}#file",
            entity_type="FileVersion",
            project_id=scope.project_id,
            repository_id=scope.repository_id,
            generation_id=scope.generation_id,
            acl_ref=scope.acl_ref,
            relative_path=path,
        )
        linked_documents.append(
            module.ScipLinkedDocument(
                document=document,
                file_link=module.ScipLink(
                    status=module.ScipLinkStatus.RESOLVED,
                    entity=file_entity,
                ),
            )
        )

    raw_bytes = _canonical_bytes(
        {
            "case_id": fixture["case_id"],
            "input_fixture_digest": _fingerprint(fixture),
            "occurrences": [
                {
                    "path": item.occurrence.relative_path,
                    "symbol": item.occurrence.symbol,
                    "entity_id": item.symbol_link.entity.entity_id,
                }
                for item in linked_occurrences
            ],
        }
    )
    raw_digest = hashlib.sha256(raw_bytes).hexdigest()
    return module.ScipConsumeResult(
        status=module.ScipStatus.COMPLETE,
        semantic_ready=False,
        documents=tuple(linked_documents),
        occurrences=tuple(linked_occurrences),
        external_symbols=(),
        relationships=(),
        diagnostics=(),
        provenance=module.ScipProvenance(
            derivation="scip",
            consumer_version=module.SCIP_CONSUMER_VERSION,
            protocol_version=1,
            indexer_name="scip-python",
            indexer_version="0.6.8",
            raw_object=module.ScipRawObject(
                raw_object_id=f"raw:cb4:{raw_digest}",
                content_sha256=raw_digest,
                byte_size=len(raw_bytes),
                source_name="index.scip",
            ),
        ),
        fallback=module.ScipFallbackTrace(
            attempted=False,
            engine="tree-sitter",
            outcome="not_used",
            reason="complete_fixed_python_fixture",
        ),
    )


def build_production_output_attestation(
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Run the fixed Golden Python30 fixtures through the exact production class."""

    resolved_root = (root or repository_root()).resolve()
    package = validate_golden_package(resolved_root)
    case_by_id = {case.canonical_id: case for case in package.cases}
    component_type = _production_component_type()
    if component_type is None:
        raise CB4Error("exact production semantic-edge component is unavailable")
    component = component_type()
    component_identity = require_production_component_identity(component)
    cases = []
    with tempfile.TemporaryDirectory(prefix="cb4-production-attestation-") as temporary:
        database_path = Path(temporary) / "evaluation.sqlite3"
        database = sqlite3.connect(database_path)
        try:
            database.execute(
                "CREATE TABLE isolation_guard "
                "(pipeline_profile TEXT NOT NULL, persistent_writes INTEGER NOT NULL)"
            )
            database.execute(
                "INSERT INTO isolation_guard VALUES (?, 0)",
                (PRODUCTION_OUTPUT_PIPELINE_PROFILE,),
            )
            database.commit()
        finally:
            database.close()
        for case_id in PYTHON_ELIGIBLE_CASE_IDS:
            fixture = _golden_case_fixture(case_by_id[case_id])
            result = _execute_production_case_fixture(
                fixture=fixture,
                component=component,
            )
            outputs = [_production_edge_payload(edge) for edge in result.edges]
            if len(outputs) != len(fixture["tree_sitter_inputs"]):
                raise CB4Error(f"production output membership drifted for {case_id}")
            case_payload = {
                "schema_version": PRODUCTION_CASE_ATTESTATION_SCHEMA_VERSION,
                "case_id": case_id,
                "input_fixture": fixture,
                "input_fixture_digest": _fingerprint(fixture),
                "production_result_hash": "sha256:" + result.result_hash,
                "output_count": len(outputs),
                "output_digest": _fingerprint(outputs),
                "outputs": outputs,
            }
            cases.append({**case_payload, "case_attestation_hash": _fingerprint(case_payload)})
    total_output_count = sum(case["output_count"] for case in cases)
    if total_output_count != PRODUCTION_OUTPUT_EDGE_COUNT:
        raise CB4Error("fixed Golden Python30 production output count drifted")
    payload = {
        "schema_version": PRODUCTION_OUTPUT_ATTESTATION_SCHEMA_VERSION,
        "pipeline_profile": PRODUCTION_OUTPUT_PIPELINE_PROFILE,
        "golden_dataset_id": DATASET_ID,
        "golden_dataset_version": DATASET_VERSION,
        "golden_package_hash": PACKAGE_HASH,
        "python_case_ids": list(PYTHON_ELIGIBLE_CASE_IDS),
        "python_case_membership_hash": _fingerprint(list(PYTHON_ELIGIBLE_CASE_IDS)),
        "component_identity": component_identity,
        "component_identity_hash": _fingerprint(component_identity),
        "isolation_profile": {
            "database": "ephemeral-temporary-sqlite",
            "formal_database_writes": False,
            "persistent_artifacts": False,
        },
        "case_count": len(cases),
        "total_output_count": total_output_count,
        "cases": cases,
    }
    attestation = {**payload, "attestation_hash": _fingerprint(payload)}
    if (
        EXPECTED_PRODUCTION_OUTPUT_ATTESTATION_HASH
        and attestation["attestation_hash"] != EXPECTED_PRODUCTION_OUTPUT_ATTESTATION_HASH
    ):
        raise CB4Error("production output attestation differs from the frozen C-B4 anchor")
    return attestation


def _validate_stored_production_output_attestation(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CB4Error("artifact lacks complete production-output attestation")
    expected_keys = {
        "schema_version",
        "pipeline_profile",
        "golden_dataset_id",
        "golden_dataset_version",
        "golden_package_hash",
        "python_case_ids",
        "python_case_membership_hash",
        "component_identity",
        "component_identity_hash",
        "isolation_profile",
        "case_count",
        "total_output_count",
        "cases",
        "attestation_hash",
    }
    if set(value) != expected_keys:
        raise CB4Error("production-output attestation schema is incomplete")
    component_identity = value.get("component_identity")
    if not isinstance(component_identity, Mapping):
        raise CB4Error("production-output attestation component identity is missing")
    _validate_stored_production_identity(component_identity)
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != len(PYTHON_ELIGIBLE_CASE_IDS):
        raise CB4Error("production-output attestation case membership is incomplete")
    canonical_cases = []
    observed_edges: set[tuple[str, str]] = set()
    for expected_case_id, case in zip(PYTHON_ELIGIBLE_CASE_IDS, cases, strict=True):
        if not isinstance(case, Mapping):
            raise CB4Error("production case attestation is not an object")
        case_payload = {
            name: case.get(name)
            for name in (
                "schema_version",
                "case_id",
                "input_fixture",
                "input_fixture_digest",
                "production_result_hash",
                "output_count",
                "output_digest",
                "outputs",
            )
        }
        fixture = case_payload["input_fixture"]
        outputs = case_payload["outputs"]
        if (
            set(case) != {*case_payload, "case_attestation_hash"}
            or case_payload["schema_version"] != PRODUCTION_CASE_ATTESTATION_SCHEMA_VERSION
            or case_payload["case_id"] != expected_case_id
            or not isinstance(fixture, Mapping)
            or fixture.get("case_id") != expected_case_id
            or fixture.get("schema_version") != PRODUCTION_INPUT_FIXTURE_SCHEMA_VERSION
            or fixture.get("pipeline_profile") != PRODUCTION_OUTPUT_PIPELINE_PROFILE
            or case_payload["input_fixture_digest"] != _fingerprint(fixture)
            or not _SHA256_RE.fullmatch(str(case_payload["production_result_hash"] or ""))
            or not isinstance(outputs, list)
            or case_payload["output_count"] != len(outputs)
        ):
            raise CB4Error(f"production case attestation is invalid: {expected_case_id}")
        canonical_outputs = [
            _validate_production_edge_payload(output)
            if isinstance(output, Mapping)
            else (_raise_invalid_production_output())
            for output in outputs
        ]
        if case_payload["output_digest"] != _fingerprint(canonical_outputs) or case.get(
            "case_attestation_hash"
        ) != _fingerprint(case_payload):
            raise CB4Error(f"production case output digest is invalid: {expected_case_id}")
        for output in canonical_outputs:
            membership = (expected_case_id, output["edge_id"])
            if membership in observed_edges:
                raise CB4Error("production-output attestation contains duplicate membership")
            observed_edges.add(membership)
        canonical_cases.append(
            {
                **case_payload,
                "outputs": canonical_outputs,
                "case_attestation_hash": case["case_attestation_hash"],
            }
        )
    payload = {
        name: value.get(name) for name in expected_keys if name not in {"cases", "attestation_hash"}
    }
    payload["cases"] = canonical_cases
    if (
        payload["schema_version"] != PRODUCTION_OUTPUT_ATTESTATION_SCHEMA_VERSION
        or payload["pipeline_profile"] != PRODUCTION_OUTPUT_PIPELINE_PROFILE
        or payload["golden_dataset_id"] != DATASET_ID
        or payload["golden_dataset_version"] != DATASET_VERSION
        or payload["golden_package_hash"] != PACKAGE_HASH
        or payload["python_case_ids"] != list(PYTHON_ELIGIBLE_CASE_IDS)
        or payload["python_case_membership_hash"] != _fingerprint(list(PYTHON_ELIGIBLE_CASE_IDS))
        or payload["component_identity_hash"] != _fingerprint(component_identity)
        or payload["isolation_profile"]
        != {
            "database": "ephemeral-temporary-sqlite",
            "formal_database_writes": False,
            "persistent_artifacts": False,
        }
        or payload["case_count"] != len(PYTHON_ELIGIBLE_CASE_IDS)
        or payload["total_output_count"] != len(observed_edges)
        or payload["total_output_count"] != PRODUCTION_OUTPUT_EDGE_COUNT
        or value["attestation_hash"] != _fingerprint(payload)
        or (
            EXPECTED_PRODUCTION_OUTPUT_ATTESTATION_HASH
            and value["attestation_hash"] != EXPECTED_PRODUCTION_OUTPUT_ATTESTATION_HASH
        )
    ):
        raise CB4Error("production-output attestation digest or identity is invalid")
    return {**payload, "attestation_hash": value["attestation_hash"]}


def _raise_invalid_production_output() -> Any:
    raise CB4Error("production case attestation contains a non-object output")


def _attested_output_index(
    attestation: Mapping[str, Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {
        (case["case_id"], output["edge_id"]): output
        for case in attestation["cases"]
        for output in case["outputs"]
    }


def _production_output_attestation_ref(
    attestation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": attestation["schema_version"],
        "attestation_hash": attestation["attestation_hash"],
        "component_identity_hash": attestation["component_identity_hash"],
        "pipeline_profile": attestation["pipeline_profile"],
        "case_count": attestation["case_count"],
        "total_output_count": attestation["total_output_count"],
    }


def _edge_membership_record(record: EdgeLabelRecord) -> dict[str, Any]:
    value = record.to_dict()
    return {
        name: value[name]
        for name in (
            "case_id",
            "edge_id",
            "source",
            "target",
            "scope",
            "relation",
            "production_edge_payload_hash",
            "eligible_status",
        )
    }


def build_edge_label_dataset(
    records: Sequence[EdgeLabelRecord],
    *,
    fixture_edge_count: int,
    production_attestation: Mapping[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any] | None:
    """Build the only accepted nonzero label evidence from canonical records."""

    attestation = (
        build_production_output_attestation(root=root)
        if production_attestation is None
        else _validate_stored_production_output_attestation(production_attestation)
    )
    _require_edge_count("fixture_edge_count", fixture_edge_count)
    if fixture_edge_count != attestation["total_output_count"]:
        raise CB4Error("label fixture count differs from attested production outputs")
    values = tuple(records)
    if not values:
        return None
    if len(values) > MAX_EDGE_LABEL_RECORDS:
        raise CB4Error("edge label dataset exceeds the bounded record limit")
    if any(type(record) is not EdgeLabelRecord for record in values):
        raise CB4Error("edge label dataset requires exact frozen EdgeLabelRecord values")
    if len(values) > fixture_edge_count:
        raise CB4Error("edge label records cannot exceed the fixture edge count")
    edge_ids = tuple(record.edge_id for record in values)
    if len(set(edge_ids)) != len(edge_ids):
        raise CB4Error("duplicate edge label records are forbidden")
    python_case_ids = PYTHON_ELIGIBLE_CASE_IDS
    allowed_cases = set(python_case_ids)
    if any(record.case_id not in allowed_cases for record in values):
        raise CB4Error("edge label record is outside released Golden v2 Python membership")
    ordered = tuple(sorted(values, key=lambda item: (item.case_id, item.edge_id)))
    attested_outputs = _attested_output_index(attestation)
    for record in ordered:
        output = attested_outputs.get((record.case_id, record.edge_id))
        if output is None:
            raise CB4Error("edge label is not a production output for its Golden case")
        expected_record_edge = {
            "source_entity_id": output["source"]["entity_id"],
            "source_entity_type": output["source"]["entity_type"],
            "target_entity_id": output["target"]["entity_id"],
            "target_entity_type": output["target"]["entity_type"],
            "project_id": output["scope"]["project_id"],
            "repository_id": output["scope"]["repository_id"],
            "generation_id": output["scope"]["generation_id"],
            "stable_version": output["scope"]["stable_version"],
            "acl_ref": output["scope"]["acl_ref"],
            "relation": output["relation"],
            "production_edge_payload_hash": _fingerprint(output),
        }
        if any(
            getattr(record, name) != expected for name, expected in expected_record_edge.items()
        ):
            raise CB4Error("edge label payload differs from its attested production output")
    serialized = [record.to_dict() for record in ordered]
    membership = [_edge_membership_record(record) for record in ordered]
    payload = {
        "schema_version": EDGE_LABEL_DATASET_SCHEMA_VERSION,
        "label_set_version": EDGE_LABEL_SET_VERSION,
        "golden_dataset_id": DATASET_ID,
        "golden_dataset_version": DATASET_VERSION,
        "golden_package_hash": PACKAGE_HASH,
        "python_case_membership_hash": _fingerprint(list(python_case_ids)),
        "production_treatment_version": REAL_SEMANTIC_EDGE_VERSION,
        "production_output_attestation_hash": attestation["attestation_hash"],
        "production_component_identity_hash": attestation["component_identity_hash"],
        "fixture_edge_count": fixture_edge_count,
        "record_count": len(serialized),
        "eligible_edge_membership_hash": _fingerprint(membership),
        "records": serialized,
    }
    return {**payload, "dataset_hash": _fingerprint(payload)}


def _validate_stored_edge_label_dataset(
    value: Any,
    *,
    production_attestation: Mapping[str, Any],
    root: Path,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CB4Error("edge label evidence must contain canonical records")
    raw_records = value.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise CB4Error("nonzero label evidence requires complete canonical records")
    if len(raw_records) > MAX_EDGE_LABEL_RECORDS:
        raise CB4Error("edge label dataset exceeds the bounded record limit")
    try:
        records = tuple(
            EdgeLabelRecord.from_mapping(record)
            if isinstance(record, Mapping)
            else (_raise_invalid_edge_label_record())
            for record in raw_records
        )
    except (KeyError, TypeError) as exc:
        raise CB4Error("edge label dataset contains an invalid record") from exc
    fixture_edge_count = value.get("fixture_edge_count")
    _require_edge_count("fixture_edge_count", fixture_edge_count)
    canonical = build_edge_label_dataset(
        records,
        fixture_edge_count=fixture_edge_count,
        production_attestation=production_attestation,
        root=root,
    )
    if canonical is None or dict(value) != canonical:
        raise CB4Error("edge label dataset hash, count, version, or membership is invalid")
    return canonical


def _raise_invalid_edge_label_record() -> Any:
    raise CB4Error("edge label dataset contains a non-object record")


def _edge_label_evidence_ref(
    dataset: Mapping[str, Any] | None,
    *,
    production_attestation: Mapping[str, Any],
) -> dict[str, Any]:
    if dataset is None:
        return {
            "schema_version": EDGE_LABEL_EVIDENCE_REF_SCHEMA_VERSION,
            "status": "absent",
            "dataset_hash": None,
            "record_count": 0,
            "label_set_version": EDGE_LABEL_SET_VERSION,
            "production_output_attestation_hash": production_attestation["attestation_hash"],
        }
    return {
        "schema_version": EDGE_LABEL_EVIDENCE_REF_SCHEMA_VERSION,
        "status": "content-addressed",
        "dataset_hash": dataset["dataset_hash"],
        "record_count": dataset["record_count"],
        "label_set_version": dataset["label_set_version"],
        "production_output_attestation_hash": dataset["production_output_attestation_hash"],
    }


def edge_precision_sample_plan(
    *,
    fixture_edge_count: int,
    human_labeled_edge_count: int | None = None,
    labels: Sequence[EdgeLabelRecord] | None = None,
    label_dataset: Mapping[str, Any] | None = None,
    production_attestation: Mapping[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Derive availability from canonical records, never a nonzero caller count."""

    resolved_root = (root or repository_root()).resolve()
    attestation = (
        build_production_output_attestation(root=resolved_root)
        if production_attestation is None
        else _validate_stored_production_output_attestation(production_attestation)
    )
    _require_edge_count("fixture_edge_count", fixture_edge_count)
    if fixture_edge_count != attestation["total_output_count"]:
        raise CB4Error("sample plan fixture count differs from production attestation")
    if labels is not None and label_dataset is not None:
        raise CB4Error("provide label records or their canonical dataset, not both")
    if human_labeled_edge_count is not None and (labels is not None or label_dataset is not None):
        raise CB4Error("caller counts cannot accompany content-addressed label evidence")
    if human_labeled_edge_count not in {None, 0}:
        raise CB4Error("nonzero label counts require complete content-addressed records")
    if labels is not None:
        dataset = build_edge_label_dataset(
            labels,
            fixture_edge_count=fixture_edge_count,
            production_attestation=attestation,
            root=resolved_root,
        )
    else:
        dataset = _validate_stored_edge_label_dataset(
            label_dataset,
            production_attestation=attestation,
            root=resolved_root,
        )
    if dataset is not None and dataset["fixture_edge_count"] != fixture_edge_count:
        raise CB4Error("edge label dataset fixture membership does not match the sample plan")
    valid_label_count = dataset["record_count"] if dataset is not None else 0
    status = _canonical_label_qualification_status(valid_label_count)
    status_value = status.value
    all_fixture = fixture_edge_count > 0 and valid_label_count == fixture_edge_count
    acceptance_eligible = status_value == "available"
    evidence_ref = _edge_label_evidence_ref(
        dataset,
        production_attestation=attestation,
    )
    base = {
        "schema_version": LABEL_QUALIFICATION_SCHEMA_VERSION,
        "fixture_edge_count": fixture_edge_count,
        "sample_count": valid_label_count,
        "minimum_sample_count": EDGE_PRECISION_MINIMUM_SAMPLE,
        "required_minimum": EDGE_PRECISION_MINIMUM_SAMPLE,
        "all_fixture": all_fixture,
        "fabricated_sample_count": False,
        "quality_metrics_recordable": status_value != "unavailable",
        "production_quality_qualification_eligible": acceptance_eligible,
        "precision_gate_satisfied": acceptance_eligible,
        "acceptance_eligible": acceptance_eligible,
        "label_evidence_ref": evidence_ref,
        "label_validation": {
            "input_label_count": valid_label_count,
            "valid_label_count": valid_label_count,
            "rejected_label_count": 0,
            "validation_mode": (
                "canonical-content-addressed-records" if dataset is not None else "no-label-dataset"
            ),
            "dataset_hash": evidence_ref["dataset_hash"],
        },
    }
    if status_value == "unavailable":
        return {
            **base,
            "status": "unavailable",
            "sampling_scope": "none",
            "reason": "zero valid real human edge labels are available",
        }
    if status_value == "provisional":
        return {
            **base,
            "status": "provisional",
            "sampling_scope": "all-fixture" if all_fixture else "partial-real-label-sample",
            "reason": (
                "all fixture edges are labeled, but fewer than 200 valid real labels "
                "cannot satisfy the production quality Gate"
                if all_fixture
                else "fewer than 200 valid real human edge labels are provisional"
            ),
        }
    return {
        **base,
        "status": "available",
        "sampling_scope": "minimum-200-real-label-sample",
        "reason": "at least 200 unique content-addressed human edge labels are available",
    }


def _require_edge_count(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CB4Error(f"{name} must be a non-negative integer")


def _production_label_contract() -> tuple[Any, type[Any], Any]:
    """Load and pin the production enum/label protocol without an eager import."""

    module = _production_module()
    if module is None:
        raise CB4Error("production semantic-edge label contract is unavailable")
    status_type = getattr(module, "SemanticEdgeEvaluationStatus", None)
    label_type = getattr(module, "SemanticEdgePrecisionLabel", None)
    verdict_type = getattr(module, "SemanticEdgePrecisionVerdict", None)
    sample_size = getattr(module, "DEFAULT_SEMANTIC_EDGE_SAMPLE_SIZE", None)
    statuses = (
        getattr(getattr(status_type, "UNAVAILABLE", None), "value", None),
        getattr(getattr(status_type, "PROVISIONAL", None), "value", None),
        getattr(getattr(status_type, "AVAILABLE", None), "value", None),
    )
    if (
        not isinstance(status_type, type)
        or status_type.__module__ != SEMANTIC_EDGE_MODULE
        or not isinstance(label_type, type)
        or label_type.__module__ != SEMANTIC_EDGE_MODULE
        or not isinstance(verdict_type, type)
        or verdict_type.__module__ != SEMANTIC_EDGE_MODULE
        or statuses != ("unavailable", "provisional", "available")
        or sample_size != EDGE_PRECISION_MINIMUM_SAMPLE
    ):
        raise CB4Error("production semantic-edge label contract does not match C-B4")
    return status_type, label_type, verdict_type


def _canonical_label_qualification_status(valid_label_count: int) -> Any:
    """Return the production enum member for the canonical C-B4 label state."""

    _require_edge_count("valid_label_count", valid_label_count)
    status_type, _, _ = _production_label_contract()
    if valid_label_count == 0:
        return status_type.UNAVAILABLE
    if valid_label_count < EDGE_PRECISION_MINIMUM_SAMPLE:
        return status_type.PROVISIONAL
    return status_type.AVAILABLE


def edge_quality_acceptance_gate(
    *,
    label_qualification: Mapping[str, Any],
    treatment_qualified: bool,
    label_dataset: Mapping[str, Any] | None = None,
    production_attestation: Mapping[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Recompute quality acceptance from records and their canonical bytes."""

    if type(treatment_qualified) is not bool:
        raise CB4Error("treatment_qualified must be a bool")
    fixture_edge_count = label_qualification.get("fixture_edge_count")
    _require_edge_count("fixture_edge_count", fixture_edge_count)
    expected = edge_precision_sample_plan(
        fixture_edge_count=fixture_edge_count,
        label_dataset=label_dataset,
        production_attestation=production_attestation,
        root=root,
    )
    if dict(label_qualification) != expected:
        raise CB4Error("label qualification does not match recomputed content-addressed records")
    precision_gate_satisfied = expected["precision_gate_satisfied"]
    if treatment_qualified and not precision_gate_satisfied:
        raise CB4Error("PROVISIONAL or UNAVAILABLE labels cannot qualify production quality")
    return {
        "label_status": expected["status"],
        "label_evidence_ref": expected["label_evidence_ref"],
        "precision_gate_satisfied": precision_gate_satisfied,
        "production_quality_qualification_eligible": expected[
            "production_quality_qualification_eligible"
        ],
        "treatment_qualified": treatment_qualified,
    }


def _production_module() -> Any | None:
    try:
        return importlib.import_module(SEMANTIC_EDGE_MODULE)
    except Exception:
        return None


def _production_component_type() -> type[Any] | None:
    """Delay importing the exact production class until it is observed."""

    module = _production_module()
    component = getattr(module, REAL_SEMANTIC_EDGE_CLASS, None) if module is not None else None
    if not isinstance(component, type) or component.__module__ != SEMANTIC_EDGE_MODULE:
        return None
    return component


def _production_component_contract() -> dict[str, Any]:
    module = _production_module()
    component = _production_component_type()
    if module is None or component is None:
        return {
            "status": "pending",
            "module": SEMANTIC_EDGE_MODULE,
            "class": REAL_SEMANTIC_EDGE_CLASS,
            "import_mode": "delayed",
            "protocol": "SemanticEdgeTreatmentProtocol",
            "reason": "production semantic_edges_v1 exact component is not yet available",
        }
    try:
        public = importlib.import_module(PUBLIC_CODE_MODULE)
    except Exception as exc:
        return {
            "status": "pending",
            "module": SEMANTIC_EDGE_MODULE,
            "class": REAL_SEMANTIC_EDGE_CLASS,
            "reason": f"public Code package is unavailable: {type(exc).__name__}",
        }
    interfaces = {
        name: isinstance(getattr(module, name, None), type)
        for name in (
            REAL_SEMANTIC_EDGE_SCOPE_CLASS,
            REAL_SEMANTIC_EDGE_RESULT_CLASS,
            REAL_TREE_SITTER_EDGE_CLASS,
        )
    }
    functions = {
        name: callable(getattr(module, name, None))
        for name in (
            REAL_SEMANTIC_EDGE_FUNCTION,
            REAL_SEMANTIC_EVALUATION_FUNCTION,
        )
    }
    version = getattr(module, "SEMANTIC_EDGE_TREATMENT_VERSION", None)
    public_component = getattr(public, REAL_SEMANTIC_EDGE_CLASS, None)
    if (
        public_component is not component
        or not callable(getattr(component, "treat", None))
        or not all(interfaces.values())
        or not all(functions.values())
        or not isinstance(version, str)
        or not version
    ):
        return {
            "status": "pending",
            "module": SEMANTIC_EDGE_MODULE,
            "class": component.__name__,
            "implementation_module": component.__module__,
            "interfaces": interfaces,
            "functions": functions,
            "reason": "production semantic_edges_v1 public contract is incomplete",
        }
    return {
        "status": "ready",
        "public_module": PUBLIC_CODE_MODULE,
        "module": SEMANTIC_EDGE_MODULE,
        "class": component.__name__,
        "implementation_module": component.__module__,
        "version": version,
        "method": "treat",
        "functional_entry_point": REAL_SEMANTIC_EDGE_FUNCTION,
        "evaluation_entry_point": REAL_SEMANTIC_EVALUATION_FUNCTION,
        "interfaces": interfaces,
        "functions": functions,
        "import_mode": "delayed",
        "identity_policy": "exact-type-only",
    }


def _require_exact_production_contract(component: Mapping[str, Any]) -> dict[str, Any]:
    expected_interfaces = {
        REAL_SEMANTIC_EDGE_SCOPE_CLASS: True,
        REAL_SEMANTIC_EDGE_RESULT_CLASS: True,
        REAL_TREE_SITTER_EDGE_CLASS: True,
    }
    expected_functions = {
        REAL_SEMANTIC_EDGE_FUNCTION: True,
        REAL_SEMANTIC_EVALUATION_FUNCTION: True,
    }
    if (
        component.get("status") != "ready"
        or component.get("public_module") != PUBLIC_CODE_MODULE
        or component.get("module") != SEMANTIC_EDGE_MODULE
        or component.get("implementation_module") != SEMANTIC_EDGE_MODULE
        or component.get("class") != REAL_SEMANTIC_EDGE_CLASS
        or component.get("version") != REAL_SEMANTIC_EDGE_VERSION
        or component.get("method") != "treat"
        or component.get("functional_entry_point") != REAL_SEMANTIC_EDGE_FUNCTION
        or component.get("evaluation_entry_point") != REAL_SEMANTIC_EVALUATION_FUNCTION
        or component.get("interfaces") != expected_interfaces
        or component.get("functions") != expected_functions
        or component.get("identity_policy") != "exact-type-only"
    ):
        raise CB4Error("C5-02 completion Gate lacks the exact production class identity")
    return dict(component)


def require_production_component_identity(component: Any) -> dict[str, Any]:
    """Reject fake, injected, wrapped, monkeypatched, and subclass components."""

    actual_type = component if isinstance(component, type) else type(component)
    if actual_type.__module__ != SEMANTIC_EDGE_MODULE:
        raise CB4Error("fake, injected, wrapped, or subclass semantic-edge component is forbidden")
    production_type = _production_component_type()
    if production_type is None or actual_type is not production_type:
        raise CB4Error("fake, injected, wrapped, or subclass semantic-edge component is forbidden")
    contract = _production_component_contract()
    return _require_exact_production_contract(contract)


def _development_authorization(root: Path) -> dict[str, Any]:
    path = (root / C5_GATE_RELATIVE_PATH).resolve()
    if not path.is_file() or path.is_symlink():
        return {
            "status": "pending",
            "decision": C5_DEVELOPMENT_AUTHORIZATION,
            "report": C5_GATE_RELATIVE_PATH,
            "reason": "C5-01 Gate report is missing",
        }
    text = path.read_text(encoding="utf-8")
    required = ("C5-01 PASS", "P0 findings: 0", "P1 findings: 0", C5_DEVELOPMENT_AUTHORIZATION)
    if not all(value in text for value in required):
        return {
            "status": "pending",
            "decision": C5_DEVELOPMENT_AUTHORIZATION,
            "report": C5_GATE_RELATIVE_PATH,
            "report_sha256": _sha256_file(path),
            "reason": "C5-01 Gate does not contain the complete development authorization",
        }
    return {
        "status": "authorized-for-development-only",
        "decision": C5_DEVELOPMENT_AUTHORIZATION,
        "report": C5_GATE_RELATIVE_PATH,
        "report_sha256": _sha256_file(path),
        "production_evaluation_authorized": False,
    }


def _normalize_gate_line(value: str) -> str:
    """Normalize Markdown code ticks, case, and whitespace without fuzzy matching."""

    without_ticks = value.replace("`", " ").strip()
    return " ".join(without_ticks.split()).casefold()


def _gate_fence_state(lines: Sequence[str]) -> tuple[bool | None, ...]:
    inside = False
    states: list[bool | None] = []
    for raw in lines:
        if raw.strip().startswith("```"):
            states.append(None)
            inside = not inside
        else:
            states.append(inside)
    return tuple(states)


def _gate_block_timestamp(lines: Sequence[str], start: int) -> str | None:
    for raw in reversed(lines[:start]):
        if raw.lstrip().startswith("#"):
            match = _GATE_TIMESTAMP_RE.search(raw)
            return match.group(1) if match is not None else None
    return None


def _gate_decision_blocks(text: str) -> tuple[dict[str, Any], ...]:
    lines = text.splitlines()
    fence_states = _gate_fence_state(lines)
    blocks: list[dict[str, Any]] = []
    for start, raw in enumerate(lines):
        engineering = _GATE_ENGINEERING_RE.fullmatch(_normalize_gate_line(raw))
        if engineering is None:
            continue
        in_fence = fence_states[start] is True
        p0_values: list[str] = []
        p1_values: list[str] = []
        execution_values: list[str] = []
        for index in range(start + 1, min(len(lines), start + 25)):
            candidate_raw = lines[index]
            if _GATE_ENGINEERING_RE.fullmatch(_normalize_gate_line(candidate_raw)):
                break
            if in_fence:
                if fence_states[index] is None:
                    break
            elif (
                not candidate_raw.strip()
                or candidate_raw.lstrip().startswith("#")
                or fence_states[index] is None
            ):
                break
            normalized = _normalize_gate_line(candidate_raw)
            findings = _GATE_FINDINGS_RE.fullmatch(normalized)
            if findings is not None:
                target = p0_values if findings.group(1) == "0" else p1_values
                target.append(findings.group(2))
                continue
            execution = _GATE_EXECUTION_RE.fullmatch(normalized)
            if execution is not None:
                execution_values.append(" ".join(execution.group(1).split()).casefold())
        blocks.append(
            {
                "index": len(blocks),
                "line": start + 1,
                "timestamp": _gate_block_timestamp(lines, start),
                "engineering": engineering.group(1).casefold(),
                "p0_values": p0_values,
                "p1_values": p1_values,
                "execution_values": execution_values,
            }
        )
    return tuple(blocks)


def _parse_c5_02_gate_text(text: str) -> dict[str, Any]:
    """Select and validate the final exact C5-02 engineering decision block."""

    blocks = _gate_decision_blocks(text)
    if not blocks:
        return {
            "status": "not-authorized",
            "completion_status": "missing",
            "reason": "C5-02 engineering completion decision is missing",
            "decision_block_count": 0,
        }
    timestamped = tuple(block for block in blocks if block["timestamp"] is not None)
    final = (
        max(timestamped, key=lambda block: (block["timestamp"], block["index"]))
        if timestamped
        else blocks[-1]
    )
    unique_p0 = final["p0_values"]
    unique_p1 = final["p1_values"]
    unique_execution = final["execution_values"]
    exact = (
        final["engineering"] == "pass"
        and unique_p0 == ["0"]
        and unique_p1 == ["0"]
        and unique_execution == ["authorized"]
    )
    if exact:
        evidence = list(C5_02_GATE_REQUIRED_EVIDENCE)
        decision_record = {
            "completion_status": "passed",
            "evidence": evidence,
            "production_execution_authorized": True,
        }
        return {
            "status": "authorized",
            **decision_record,
            "decision_block_count": len(blocks),
            "selected_block_index": final["index"],
            "selected_block_line": final["line"],
            "selected_block_timestamp": final["timestamp"],
            "decision_block_hash": _fingerprint(decision_record),
        }
    if final["engineering"] != "pass":
        reason = "final C5-02 engineering decision is not PASS"
    elif unique_p0 != ["0"] or unique_p1 != ["0"]:
        reason = "final C5-02 decision does not contain exactly P0=0 and P1=0"
    elif unique_execution != ["authorized"]:
        reason = "final C-B4 production execution decision is not exact AUTHORIZED"
    else:
        reason = "final C5-02 authorization block is ambiguous"
    return {
        "status": "not-authorized",
        "completion_status": ("failed" if final["engineering"] == "fail" else "incomplete"),
        "reason": reason,
        "decision_block_count": len(blocks),
        "selected_block_index": final["index"],
        "selected_block_line": final["line"],
        "selected_block_timestamp": final["timestamp"],
        "engineering": final["engineering"],
        "p0_values": list(unique_p0),
        "p1_values": list(unique_p1),
        "execution_values": list(unique_execution),
        "production_execution_authorized": False,
    }


def _c5_02_execution_gate_status(root: Path) -> dict[str, Any]:
    path = (root / C5_GATE_RELATIVE_PATH).resolve()
    if not path.is_file() or path.is_symlink():
        return {
            "status": "not-authorized",
            "completion_status": "missing",
            "required_decision": C5_02_GATE_DECISION,
            "report": C5_GATE_RELATIVE_PATH,
            "reason": "C5-02 completion Gate authorization report is missing",
        }
    parsed = _parse_c5_02_gate_text(path.read_text(encoding="utf-8"))
    result = {
        **parsed,
        "required_decision": C5_02_GATE_DECISION,
        "report": C5_GATE_RELATIVE_PATH,
        "report_sha256": _sha256_file(path),
    }
    if parsed["status"] != "authorized":
        return result
    try:
        component = _require_exact_production_contract(_production_component_contract())
    except CB4Error as exc:
        return {
            **result,
            "status": "not-authorized",
            "reason": str(exc),
            "production_execution_authorized": False,
        }
    return {**result, "production_component": component}


def _validate_c5_02_execution_gate(root: Path) -> dict[str, Any]:
    status = _c5_02_execution_gate_status(root)
    if status.get("status") != "authorized":
        raise CB4Error(
            "C5-02 completion Gate has not authorized C-B4 production execution: "
            f"{status.get('reason') or 'exact final authorization is missing'}"
        )
    component = _require_exact_production_contract(status["production_component"])
    signed = {
        "schema_version": C5_02_GATE_SCHEMA_VERSION,
        "decision": C5_02_GATE_DECISION,
        "status": "authorized",
        "completion_status": "passed",
        "evidence": list(C5_02_GATE_REQUIRED_EVIDENCE),
        "decision_block_hash": status["decision_block_hash"],
        "production_component": component,
    }
    return {
        **signed,
        "snapshot_hash": _fingerprint(signed),
        "report": C5_GATE_RELATIVE_PATH,
        "report_sha256": status["report_sha256"],
    }


def _anchor_summary(
    *,
    run_id: str,
    artifact_directory: str,
    verification: Mapping[str, Any],
    qualified: bool,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "artifact_directory": artifact_directory,
        "treatment_qualified": qualified,
        "qualification_use": "required" if qualified else "forbidden",
        "manifest_canonical_hash": verification.get("manifest_canonical_hash"),
    }


def _load_controls(root: Path, package: GoldenPackage) -> dict[str, Any]:
    try:
        cb0 = cb1._load_cb0_anchor(root)
        cb1_audit = cb2._load_cb1_audit_anchor(root)
        cb2_audit = cb5._load_cb2_audit_anchor(root)
        cb5_audit = cb3._load_cb5_audit_anchor(root, package)
    except Exception as exc:
        raise CB4Error(f"unable to validate fixed C-B0/C-B1/C-B2/C-B5 anchors: {exc}") from exc

    cb3_directory = (root / "evals" / "code" / "runs" / CB3_AUDIT_ARTIFACT_DIRECTORY).resolve()
    if not cb3_directory.is_dir():
        raise CB4Error(f"C-B3 audit artifact is missing: {CB3_AUDIT_ARTIFACT_DIRECTORY}")
    try:
        cb3_verification = cb3.verify_artifact(cb3_directory, root=root)
    except Exception as exc:
        raise CB4Error(f"C-B3 audit verification failed: {exc}") from exc
    cb3_manifest = _read_json(cb3_directory / "manifest.json")
    if (
        cb3_verification.get("run_id") != CB3_AUDIT_RUN_ID
        or cb3_manifest.get("run_id") != CB3_AUDIT_RUN_ID
        or cb3_manifest.get("treatment_qualified") is not False
        or (cb3_manifest.get("acceptance") or {}).get("decision") != "not-qualified"
        or (cb3_manifest.get("dataset") or {}).get("package_hash") != PACKAGE_HASH
        or (cb3_manifest.get("dataset") or {}).get("case_membership_hash")
        != package.membership_hash
    ):
        raise CB4Error("C-B3 artifact does not match the fixed NOT QUALIFIED audit anchor")

    return {
        "qualified_baseline": _anchor_summary(
            run_id=CB0_QUALIFIED_RUN_ID,
            artifact_directory=CB0_ARTIFACT_DIRECTORY,
            verification=cb0.verification,
            qualified=True,
        ),
        "cb1_not_qualified_audit": _anchor_summary(
            run_id=CB1_AUDIT_RUN_ID,
            artifact_directory=CB1_AUDIT_ARTIFACT_DIRECTORY,
            verification=cb1_audit.verification,
            qualified=False,
        ),
        "cb2_not_qualified_audit": _anchor_summary(
            run_id=CB2_AUDIT_RUN_ID,
            artifact_directory=CB2_AUDIT_ARTIFACT_DIRECTORY,
            verification=cb2_audit.verification,
            qualified=False,
        ),
        "cb3_not_qualified_audit": _anchor_summary(
            run_id=CB3_AUDIT_RUN_ID,
            artifact_directory=CB3_AUDIT_ARTIFACT_DIRECTORY,
            verification=cb3_verification,
            qualified=False,
        ),
        "cb5_not_qualified_audit": _anchor_summary(
            run_id=CB5_AUDIT_RUN_ID,
            artifact_directory=CB5_AUDIT_ARTIFACT_DIRECTORY,
            verification=cb5_audit.verification,
            qualified=False,
        ),
    }


def preparation_status(*, root: Path | None = None) -> dict[str, Any]:
    """Validate the complete C-B4 contract and return without writing state."""

    resolved_root = (root or repository_root()).resolve()
    runs_dir = (resolved_root / "evals" / "code" / "runs").resolve()
    database_before = _formal_database_state(resolved_root)
    runs_before = _tree_state(runs_dir)

    package = validate_golden_package(resolved_root)
    controls = _load_controls(resolved_root, package)
    python_case_ids = _python_case_ids(package)
    unavailable = _unavailable_language_cases(package)
    denominator = require_same_python_case_denominator(
        {arm: python_case_ids for arm in TREATMENT_ORDER},
        expected_case_ids=python_case_ids,
    )
    production_component = _production_component_contract()
    production_attestation = build_production_output_attestation(root=resolved_root)
    if production_attestation["component_identity"] != production_component:
        raise CB4Error("production output attestation component identity drifted")
    development_gate = _development_authorization(resolved_root)
    execution_gate = _c5_02_execution_gate_status(resolved_root)

    database_after = _formal_database_state(resolved_root)
    runs_after = _tree_state(runs_dir)
    _require_repository_unchanged(
        database_before=database_before,
        database_after=database_after,
        runs_before=runs_before,
        runs_after=runs_after,
    )

    reporting = {
        name: {
            **contract,
            "status": (
                "unavailable"
                if name in ZERO_LABEL_UNAVAILABLE_METRICS
                else "pending-production-execution"
            ),
        }
        for name, contract in METRIC_CONTRACT.items()
    }
    return {
        "schema_version": CB4_PREPARATION_VERSION,
        "status": "PREPARED",
        "qualification_status": "NON-QUALIFIED",
        "execution_status": (
            "authorized-ready-for-single-production-run"
            if execution_gate.get("status") == "authorized"
            else "pending-c5-02-completion-gate"
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
        "controls": controls,
        "treatment_contract": {
            "order": list(TREATMENT_ORDER),
            "arms": TREATMENT_MATRIX,
            "semantic_relation_policy": SEMANTIC_RELATION_POLICY,
            "same_snapshot_required": True,
            "same_ingestion_required": True,
            "same_python_case_denominator_required": True,
            "denominator": denominator,
        },
        "language_scope": {
            "python": {
                "status": "available-for-future-treatment",
                "eligible_case_count": len(python_case_ids),
                "canonical_case_ids": list(python_case_ids),
            },
            "javascript": {
                "status": "unavailable",
                "eligible_case_count": len(unavailable["javascript"]),
                "canonical_case_ids": list(unavailable["javascript"]),
                "reason": "C-B4 is Python-first; no JavaScript SCIP treatment is available",
            },
            "typescript": {
                "status": "unavailable",
                "eligible_case_count": len(unavailable["typescript"]),
                "canonical_case_ids": list(unavailable["typescript"]),
                "reason": "C-B4 is Python-first; scip-typescript is not available",
            },
        },
        "production_component": production_component,
        "production_output_attestation": production_attestation,
        "development_gate": development_gate,
        "execution_gate": {
            **execution_gate,
            "development_authorization_is_execution_authorization": False,
        },
        "execution_policy": {
            "prepared_only": True,
            "production_execution_authorized": (execution_gate.get("status") == "authorized"),
            "zero_label_future_execution_records_unavailable": True,
            "zero_label_treatment_qualification": False,
            "nonzero_labels_require_persisted_canonical_records": True,
            "requires_explicit_c5_02_completion_gate": True,
            "requires_exact_public_production_identity": True,
            "component_injection_forbidden": True,
            "fake_wrapped_or_subclass_qualification_forbidden": True,
            "test_artifact_execution_forbidden": True,
            "fresh_run_directory_required": True,
            "immutable_publish_required": True,
        },
        "reporting_contract": reporting,
        "measurement_state": {
            "metric_values_created": False,
            "reported_metric_names": [],
            "zero_label_quality_metrics": {
                name: {
                    "status": "unavailable",
                    "value": None,
                    "acceptance_eligible": False,
                }
                for name in sorted(ZERO_LABEL_UNAVAILABLE_METRICS)
            },
            "structural_observations": {
                name: {
                    "status": "recordable-after-production-execution",
                    "value": None,
                    "quality_metric": False,
                    "acceptance_eligible": False,
                }
                for name in sorted(STRUCTURAL_OBSERVATIONS)
            },
            "edge_precision": edge_precision_sample_plan(
                fixture_edge_count=production_attestation["total_output_count"],
                human_labeled_edge_count=0,
                production_attestation=production_attestation,
                root=resolved_root,
            ),
        },
        "acceptance_policy": {
            "zero_label_quality_metrics_eligible": False,
            "nonzero_count_only_evidence_forbidden": True,
            "content_addressed_label_records_required": True,
            "label_status_contract": {
                "unavailable": "0 valid real labels",
                "provisional": "1..199 valid real labels",
                "available": "at least 200 valid real labels",
            },
            "required_minimum": EDGE_PRECISION_MINIMUM_SAMPLE,
            "maximum_attested_output_count": production_attestation["total_output_count"],
            "available_reachable": (
                production_attestation["total_output_count"] >= EDGE_PRECISION_MINIMUM_SAMPLE
            ),
            "provisional_quality_values_recordable": True,
            "provisional_precision_gate_eligible": False,
            "available_required_for_production_quality_qualification": True,
            "structural_observations_are_quality_metrics": False,
            "structural_observations_acceptance_eligible": False,
        },
        "artifact_policy": {
            "expected_entries": sorted(EXPECTED_ARTIFACT_ENTRIES),
            "label_qualification_schema_version": LABEL_QUALIFICATION_SCHEMA_VERSION,
            "edge_label_record_schema_version": EDGE_LABEL_RECORD_SCHEMA_VERSION,
            "edge_label_dataset_schema_version": EDGE_LABEL_DATASET_SCHEMA_VERSION,
            "edge_label_set_version": EDGE_LABEL_SET_VERSION,
            "production_output_attestation_schema_version": (
                PRODUCTION_OUTPUT_ATTESTATION_SCHEMA_VERSION
            ),
            "production_output_attestation_ref": (
                _production_output_attestation_ref(production_attestation)
            ),
            "maximum_label_records": MAX_EDGE_LABEL_RECORDS,
            "records_stored_once_in_manifest": True,
            "dataset_digest_from_canonical_bytes": True,
            "dataset_digest_commits_production_attestation": True,
            "labels_must_match_case_production_outputs": True,
            "complete_production_attestation_stored_in_manifest": True,
            "label_qualification_manifest_field_required": True,
            "exact_production_label_records_required": True,
            "quality_acceptance_manifest_field_required": True,
            "cross_files_reference_label_and_production_attestation_hashes": True,
            "canonical_json_required": True,
            "portable_sqlite_required": True,
            "secret_scan_required": True,
            "temporary_path_scan_required": True,
            "symlinks_forbidden": True,
            "wal_shm_forbidden": True,
            "pyc_and_pycache_forbidden": True,
        },
        "isolation": {
            "database": "temporary-sqlite-only-after-authorization",
            "formal_database_guard": {
                "protected": True,
                "hash_size_mtime_checked": True,
                "wal_shm_checked": True,
                "observed": database_after,
            },
            "repository_runs_guard": {
                "protected": True,
                "hash_size_mtime_checked": True,
                "file_count": len(runs_after),
                "unchanged": True,
            },
        },
    }


def prepare_cb4(*, root: Path | None = None) -> dict[str, Any]:
    """Public spelling for PREPARED-only orchestration."""

    return preparation_status(root=root)


def _validate_execution_policy(
    *,
    root: Path,
    runs_dir: Path,
    execution_mode: str,
    semantic_edge_component: Any | None,
    semantic_edge_factory: Any | None,
    c5_02_gate_approved: bool,
) -> dict[str, Any]:
    repository_runs = (root / "evals" / "code" / "runs").resolve()
    if execution_mode != "qualified":
        raise CB4Error("C-B4 has no test/fake execution mode")
    if semantic_edge_component is not None or semantic_edge_factory is not None:
        raise CB4Error(
            "qualified C-B4 forbids fake, injected, wrapped, or subclass semantic-edge components"
        )
    if not c5_02_gate_approved:
        raise CB4Error("C-B4 execution requires explicit C5-02 completion Gate approval")
    if runs_dir != repository_runs:
        raise CB4Error("qualified C-B4 must use the canonical evals/code/runs directory")
    return _validate_c5_02_execution_gate(root)


def _existing_cb4_run_ids(runs_dir: Path) -> tuple[str, ...]:
    values = []
    if not runs_dir.exists():
        return ()
    for directory in sorted(runs_dir.iterdir()):
        manifest_path = directory / "manifest.json"
        if not directory.is_dir() or directory.is_symlink() or not manifest_path.is_file():
            continue
        try:
            manifest = _read_json(manifest_path)
        except Exception:
            continue
        if manifest.get("schema_version") == CB4_SCHEMA_VERSION:
            values.append(directory.name)
    return tuple(values)


def _p95_milliseconds(values: Sequence[int]) -> float:
    if not values:
        raise CB4Error("P95 requires at least one observed duration")
    ordered = sorted(values)
    index = max(0, (95 * len(ordered) + 99) // 100 - 1)
    return round(ordered[index] / 1_000_000, 6)


def _measure_production_treatment(
    *,
    component: Any,
    scip_result: Any | None,
    tree_sitter_edges: Sequence[Any],
    scope: Any,
) -> tuple[Any, int, int]:
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    started = time.perf_counter_ns()
    try:
        result = component.treat(
            scip_result=scip_result,
            tree_sitter_edges=tuple(tree_sitter_edges),
            scope=scope,
            language="python",
        )
    finally:
        elapsed = time.perf_counter_ns() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
        if not already_tracing:
            tracemalloc.stop()
    return result, elapsed, peak_bytes


def _initialize_execution_database(
    path: Path,
    *,
    run_id: str,
    run_uri: str,
) -> sqlite3.Connection:
    database = sqlite3.connect(path)
    try:
        database.execute("PRAGMA journal_mode=DELETE")
        database.execute("PRAGMA synchronous=FULL")
        database.execute("PRAGMA foreign_keys=ON")
        database.execute(f"PRAGMA user_version={CB4_SQLITE_SCHEMA_VERSION}")
        database.executescript(
            """
            CREATE TABLE run_metadata (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            );
            CREATE TABLE treatment_results (
                case_id TEXT NOT NULL,
                treatment_arm TEXT NOT NULL,
                input_fixture_digest TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                result_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                support_status TEXT NOT NULL,
                edge_count INTEGER NOT NULL,
                conflict_count INTEGER NOT NULL,
                diagnostic_count INTEGER NOT NULL,
                unresolved_before INTEGER,
                unresolved_after INTEGER,
                treatment_latency_ns INTEGER NOT NULL,
                peak_memory_bytes INTEGER NOT NULL,
                ingest_latency_ns INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (case_id, treatment_arm)
            );
            CREATE TABLE semantic_edges (
                case_id TEXT NOT NULL,
                treatment_arm TEXT NOT NULL,
                edge_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                PRIMARY KEY (case_id, treatment_arm, edge_id),
                FOREIGN KEY (case_id, treatment_arm)
                    REFERENCES treatment_results (case_id, treatment_arm)
                    ON DELETE RESTRICT
            );
            """
        )
        for key, value in (
            ("run_id", run_id),
            ("run_uri", run_uri),
            ("pipeline_profile", PRODUCTION_EXECUTION_PIPELINE_PROFILE),
            ("sqlite_schema_version", CB4_SQLITE_SCHEMA_VERSION),
        ):
            database.execute(
                "INSERT INTO run_metadata (key, value_json) VALUES (?, ?)",
                (key, _canonical_bytes(value).decode("utf-8")),
            )
        database.commit()
    except Exception:
        database.close()
        raise
    return database


def _result_diagnostic_counts(result: Any) -> dict[str, int]:
    return {item.code.value: item.count for item in result.diagnostic_counts}


def _result_provenance_counts(result: Any) -> dict[str, int]:
    return {
        "tree_sitter_edge_count": sum(
            any(item.derivation.value == "tree_sitter" for item in edge.provenances)
            for edge in result.edges
        ),
        "scip_edge_count": sum(
            any(item.derivation.value == "scip" for item in edge.provenances)
            for edge in result.edges
        ),
        "multi_provenance_edge_count": sum(len(edge.provenances) > 1 for edge in result.edges),
        "provenance_record_count": sum(len(edge.provenances) for edge in result.edges),
    }


def _execute_three_arm_treatments(
    *,
    production_attestation: Mapping[str, Any],
    database: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    component_type = _production_component_type()
    if component_type is None:
        raise CB4Error("exact production semantic-edge component is unavailable")
    component = component_type()
    require_production_component_identity(component)
    production = _production_module()
    if production is None:
        raise CB4Error("production semantic-edge evaluator is unavailable")

    case_results = []
    performance_samples: dict[str, dict[str, list[int]]] = {
        arm: {"treatment_ns": [], "ingest_ns": [], "peak_memory_bytes": []}
        for arm in TREATMENT_ORDER
    }
    for case_attestation in production_attestation["cases"]:
        case_id = case_attestation["case_id"]
        fixture = case_attestation["input_fixture"]
        scope, tree_edges = _production_case_inputs(fixture)
        scip_result = _build_scip_case_fixture(fixture, scope)
        expected_entities = {locator["entity_id"] for locator in fixture["expected_locators"]}
        arms: dict[str, dict[str, Any]] = {}
        for arm, arm_scip, arm_tree in (
            ("tree_sitter_conservative", None, tree_edges),
            ("scip_semantic", scip_result, ()),
            ("merged_policy", scip_result, tree_edges),
        ):
            result, treatment_ns, peak_bytes = _measure_production_treatment(
                component=component,
                scip_result=arm_scip,
                tree_sitter_edges=arm_tree,
                scope=scope,
            )
            if type(result).__module__ != SEMANTIC_EDGE_MODULE:
                raise CB4Error("production treatment returned a non-production result")
            unresolved_before = (
                None
                if arm_scip is None
                else sum(
                    item.symbol_link.status.value != "resolved" for item in arm_scip.occurrences
                )
            )
            diagnostic_counts = _result_diagnostic_counts(result)
            unresolved_after = (
                None if arm_scip is None else diagnostic_counts.get("link_unresolved", 0)
            )
            evaluation = production.evaluate_semantic_edges(
                result,
                labels=(),
                eligible_edge_count=len(fixture["tree_sitter_inputs"]),
                unresolved_before=unresolved_before,
                unresolved_after=unresolved_after,
            )
            if evaluation.status.value != "unavailable" or any(
                value is not None
                for value in (
                    evaluation.quality_metrics.edge_precision,
                    evaluation.quality_metrics.coverage,
                    evaluation.quality_metrics.unresolved_reduction_count,
                    evaluation.quality_metrics.unresolved_reduction_rate,
                    evaluation.quality_metrics.graph_noise_count,
                    evaluation.quality_metrics.graph_noise_rate,
                    evaluation.quality_metrics.harmful_count,
                    evaluation.quality_metrics.harmful_rate,
                )
            ):
                raise CB4Error("zero-label production evaluation published a quality value")
            outputs = [_production_edge_payload(edge) for edge in result.edges]
            relation_counts = {
                relation: sum(output["relation"] == relation for output in outputs)
                for relation in sorted({output["relation"] for output in outputs})
            }
            matched_entities = expected_entities & {
                output["target"]["entity_id"] for output in outputs
            }
            provenance_counts = _result_provenance_counts(result)
            result_hash = "sha256:" + result.result_hash
            ingest_started = time.perf_counter_ns()
            database.execute(
                """
                INSERT INTO treatment_results (
                    case_id, treatment_arm, input_fixture_digest, generation_id,
                    result_hash, status, support_status, edge_count, conflict_count,
                    diagnostic_count, unresolved_before, unresolved_after,
                    treatment_latency_ns, peak_memory_bytes, ingest_latency_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    case_id,
                    arm,
                    case_attestation["input_fixture_digest"],
                    fixture["generation_id"],
                    result_hash,
                    result.status.value,
                    result.support_status.value,
                    len(outputs),
                    len(result.conflicts),
                    sum(diagnostic_counts.values()),
                    unresolved_before,
                    unresolved_after,
                    treatment_ns,
                    peak_bytes,
                ),
            )
            database.executemany(
                """
                INSERT INTO semantic_edges (
                    case_id, treatment_arm, edge_id, relation, payload_json, payload_hash
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        case_id,
                        arm,
                        output["edge_id"],
                        output["relation"],
                        _canonical_bytes(output).decode("utf-8"),
                        _fingerprint(output),
                    )
                    for output in outputs
                ),
            )
            database.commit()
            ingest_ns = time.perf_counter_ns() - ingest_started
            database.execute(
                """
                UPDATE treatment_results
                SET ingest_latency_ns=?
                WHERE case_id=? AND treatment_arm=?
                """,
                (ingest_ns, case_id, arm),
            )
            database.commit()
            performance_samples[arm]["treatment_ns"].append(treatment_ns)
            performance_samples[arm]["ingest_ns"].append(ingest_ns)
            performance_samples[arm]["peak_memory_bytes"].append(peak_bytes)
            arms[arm] = {
                "status": result.status.value,
                "support_status": result.support_status.value,
                "semantic_ready": result.semantic_ready,
                "result_hash": result_hash,
                "edge_count": len(outputs),
                "edge_ids": [output["edge_id"] for output in outputs],
                "output_digest": _fingerprint(outputs),
                "relation_counts": relation_counts,
                "diagnostic_counts": diagnostic_counts,
                "diagnostic_count": sum(diagnostic_counts.values()),
                "conflict_count": len(result.conflicts),
                "conflict_ids": [item.conflict_id for item in result.conflicts],
                "provenance_counts": provenance_counts,
                "eligible_input_count": len(fixture["tree_sitter_inputs"]),
                "matched_expected_entity_count": len(matched_entities),
                "unresolved_before": unresolved_before,
                "unresolved_after": unresolved_after,
                "quality_status": evaluation.status.value,
                "quality_values_created": False,
                "treatment_latency_ms": round(treatment_ns / 1_000_000, 6),
                "peak_memory_bytes": peak_bytes,
                "ingest_latency_ms": round(ingest_ns / 1_000_000, 6),
            }
            if arm == "tree_sitter_conservative" and (
                arms[arm]["edge_ids"]
                != [output["edge_id"] for output in case_attestation["outputs"]]
                or arms[arm]["output_digest"] != case_attestation["output_digest"]
            ):
                raise CB4Error(f"production Tree-sitter output drifted from attestation: {case_id}")
        case_results.append(
            {
                "case_id": case_id,
                "input_fixture_digest": case_attestation["input_fixture_digest"],
                "generation_id": fixture["generation_id"],
                "expected_input_count": len(fixture["tree_sitter_inputs"]),
                "treatments": arms,
            }
        )

    index_started = time.perf_counter_ns()
    database.execute(
        "CREATE INDEX semantic_edges_relation_idx "
        "ON semantic_edges (treatment_arm, relation, edge_id)"
    )
    database.execute("CREATE INDEX semantic_edges_payload_idx ON semantic_edges (payload_hash)")
    database.commit()
    index_ns = time.perf_counter_ns() - index_started
    aggregates: dict[str, dict[str, Any]] = {}
    for arm in TREATMENT_ORDER:
        values = [case["treatments"][arm] for case in case_results]
        relations = sorted({relation for value in values for relation in value["relation_counts"]})
        status_values = sorted({value["status"] for value in values})
        input_count = sum(value["eligible_input_count"] for value in values)
        matched_count = sum(value["matched_expected_entity_count"] for value in values)
        edge_count = sum(value["edge_count"] for value in values)
        conflict_count = sum(value["conflict_count"] for value in values)
        aggregate_provenance = {
            name: sum(value["provenance_counts"][name] for value in values)
            for name in (
                "tree_sitter_edge_count",
                "scip_edge_count",
                "multi_provenance_edge_count",
                "provenance_record_count",
            )
        }
        unresolved_values = [value for value in values if value["unresolved_before"] is not None]
        aggregates[arm] = {
            "classification": "non_quality_structural_counts",
            "case_count": len(values),
            "status_counts": {
                status: sum(value["status"] == status for value in values)
                for status in status_values
            },
            "eligible_input_count": input_count,
            "matched_expected_entity_count": matched_count,
            "structural_target_coverage": (matched_count / input_count if input_count else None),
            "legal_edge_count": edge_count,
            "relation_counts": {
                relation: sum(value["relation_counts"].get(relation, 0) for value in values)
                for relation in relations
            },
            "conflict_count": conflict_count,
            "conflict_rate": (
                conflict_count / (edge_count + conflict_count)
                if edge_count + conflict_count
                else 0.0
            ),
            "diagnostic_count": sum(value["diagnostic_count"] for value in values),
            "unresolved_case_count": len(unresolved_values),
            "unresolved_before": (
                sum(value["unresolved_before"] for value in unresolved_values)
                if unresolved_values
                else None
            ),
            "unresolved_after": (
                sum(value["unresolved_after"] for value in unresolved_values)
                if unresolved_values
                else None
            ),
            "provenance_counts": aggregate_provenance,
            "provenance_merge_rate": (
                aggregate_provenance["multi_provenance_edge_count"] / edge_count
                if edge_count
                else 0.0
            ),
            "result_set_hash": _fingerprint([value["result_hash"] for value in values]),
            "output_set_hash": _fingerprint([value["output_digest"] for value in values]),
            "treatment_time_ms": round(
                sum(performance_samples[arm]["treatment_ns"]) / 1_000_000,
                6,
            ),
            "treatment_latency_ms_p95": _p95_milliseconds(performance_samples[arm]["treatment_ns"]),
            "ingest_time_ms": round(
                sum(performance_samples[arm]["ingest_ns"]) / 1_000_000,
                6,
            ),
            "ingest_latency_ms_p95": _p95_milliseconds(performance_samples[arm]["ingest_ns"]),
            "peak_memory_bytes": max(performance_samples[arm]["peak_memory_bytes"]),
        }
    return case_results, aggregates, index_ns


def _unavailable_metric(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "value": None,
        "reason": reason,
        "acceptance_eligible": False,
    }


def _write_artifact_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_bytes(_pretty_json_bytes(dict(value)))


def _build_production_artifact(
    directory: Path,
    *,
    root: Path,
    run_id: str,
    run_uri: str,
    executed_at_utc: str,
    gate_authorization: Mapping[str, Any],
    formal_database_before: Mapping[str, Any],
    old_runs_before: Sequence[tuple[str, int, int, str]],
) -> dict[str, Any]:
    directory.mkdir(parents=False, exist_ok=False)
    package = validate_golden_package(root)
    controls = _load_controls(root, package)
    production_identity = dict(gate_authorization["production_component"])
    production_attestation = build_production_output_attestation(root=root)
    if production_attestation["component_identity"] != production_identity:
        raise CB4Error("authorized production identity differs from output attestation")
    label_qualification = edge_precision_sample_plan(
        fixture_edge_count=production_attestation["total_output_count"],
        human_labeled_edge_count=0,
        production_attestation=production_attestation,
        root=root,
    )
    quality_acceptance = edge_quality_acceptance_gate(
        label_qualification=label_qualification,
        treatment_qualified=False,
        label_dataset=None,
        production_attestation=production_attestation,
        root=root,
    )
    database_path = directory / "evaluation.sqlite3"
    database = _initialize_execution_database(
        database_path,
        run_id=run_id,
        run_uri=run_uri,
    )
    try:
        cases, aggregates, index_ns = _execute_three_arm_treatments(
            production_attestation=production_attestation,
            database=database,
        )
        database.execute("PRAGMA optimize")
        database.commit()
        database.execute("VACUUM")
    finally:
        database.close()
    if (
        len(cases) != len(PYTHON_ELIGIBLE_CASE_IDS)
        or any(aggregates[arm]["case_count"] != 30 for arm in TREATMENT_ORDER)
        or any(
            aggregates[arm]["legal_edge_count"] != PRODUCTION_OUTPUT_EDGE_COUNT
            for arm in TREATMENT_ORDER
        )
        or aggregates["tree_sitter_conservative"]["provenance_counts"]["tree_sitter_edge_count"]
        != PRODUCTION_OUTPUT_EDGE_COUNT
        or aggregates["scip_semantic"]["provenance_counts"]["scip_edge_count"]
        != PRODUCTION_OUTPUT_EDGE_COUNT
        or aggregates["merged_policy"]["provenance_counts"]["multi_provenance_edge_count"]
        != PRODUCTION_OUTPUT_EDGE_COUNT
    ):
        raise CB4Error("production three-arm structural result differs from the fixed contract")

    common = {
        "run_id": run_uri,
        "artifact_id": run_id,
        "executed_at_utc": executed_at_utc,
        "pipeline_profile": PRODUCTION_EXECUTION_PIPELINE_PROFILE,
    }
    treatments_report = {
        "schema_version": CB4_TREATMENTS_REPORT_VERSION,
        **common,
        "production_identity": production_identity,
        "production_output_attestation_ref": _production_output_attestation_ref(
            production_attestation
        ),
        "treatment_order": list(TREATMENT_ORDER),
        "same_input_profile_generation": True,
        "python_case_ids": list(PYTHON_ELIGIBLE_CASE_IDS),
        "python_case_membership_hash": _fingerprint(list(PYTHON_ELIGIBLE_CASE_IDS)),
        "cases": cases,
        "aggregate": aggregates,
        "language_scope": {
            "python": {
                "status": "executed",
                "case_count": 30,
            },
            "javascript": {
                "status": "unavailable",
                "case_ids": _unavailable_language_cases(package)["javascript"],
                "reason": "C-B4 production semantic treatment is Python-only",
            },
            "typescript": {
                "status": "unavailable",
                "case_ids": _unavailable_language_cases(package)["typescript"],
                "reason": "scip-typescript production treatment is unavailable",
            },
        },
    }
    zero_label_quality = {
        "edge_precision": _unavailable_metric("zero real human edge-label records"),
        "edge_coverage": _unavailable_metric(
            "zero real human labels cannot establish quality coverage"
        ),
        "unresolved_reduction": _unavailable_metric(
            "zero real human labels cannot establish quality unresolved reduction"
        ),
        "graph_noise_rate@10": _unavailable_metric(
            "zero real human labels cannot establish graph noise"
        ),
        "graph_harmful_candidate_rate@10": _unavailable_metric(
            "zero real human labels cannot establish graph harm"
        ),
    }
    structural_counts = {
        arm: {
            "classification": "non_quality_structural_counts",
            "legal_edge_count": aggregates[arm]["legal_edge_count"],
            "scip_legal_edge_count": aggregates[arm]["provenance_counts"]["scip_edge_count"],
            "relation_counts": aggregates[arm]["relation_counts"],
            "conflict_count": aggregates[arm]["conflict_count"],
            "conflict_rate": aggregates[arm]["conflict_rate"],
            "diagnostic_count": aggregates[arm]["diagnostic_count"],
            "unresolved_before": aggregates[arm]["unresolved_before"],
            "unresolved_after": aggregates[arm]["unresolved_after"],
            "provenance_counts": aggregates[arm]["provenance_counts"],
            "provenance_merge_rate": aggregates[arm]["provenance_merge_rate"],
        }
        for arm in TREATMENT_ORDER
    }
    coverage_report = {
        "schema_version": CB4_COVERAGE_REPORT_VERSION,
        **common,
        "quality_metrics": {
            name: zero_label_quality[name] for name in ("edge_coverage", "unresolved_reduction")
        },
        "required_path_recall": _unavailable_metric(
            "semantic-edge treatment does not execute a retrieval candidate ranking"
        ),
        "structural_target_coverage": {
            arm: {
                "classification": "non_quality_structural_observation",
                "expected_target_count": aggregates[arm]["eligible_input_count"],
                "matched_target_count": aggregates[arm]["matched_expected_entity_count"],
                "value": aggregates[arm]["structural_target_coverage"],
            }
            for arm in TREATMENT_ORDER
        },
        "same_python_case_denominator": {
            "case_count": 30,
            "case_membership_hash": _fingerprint(list(PYTHON_ELIGIBLE_CASE_IDS)),
        },
    }
    edge_quality_report = {
        "schema_version": CB4_EDGE_QUALITY_REPORT_VERSION,
        **common,
        "label_evidence_ref": label_qualification["label_evidence_ref"],
        "label_status": label_qualification["status"],
        "sample_count": label_qualification["sample_count"],
        "quality_acceptance": quality_acceptance,
        "quality_metrics": zero_label_quality,
        "structural_counts": structural_counts,
    }
    retrieval_metric_names = (
        "retrieval_latency_ms_p95",
        "expected_locator_recall@10",
        "entity_recall@10",
        "mrr@10",
        "ndcg@10",
        "harmful_candidate_rate@10",
        "unauthorized_candidate_rate",
        "wrong_version_rate",
    )
    retrieval_report = {
        "schema_version": CB4_RETRIEVAL_REPORT_VERSION,
        **common,
        "status": "not-executed",
        "reason": ("C-B4 isolates semantic-edge treatment; no retrieval ranking was executed"),
        "python_case_denominator": {
            "case_count": 30,
            "case_membership_hash": _fingerprint(list(PYTHON_ELIGIBLE_CASE_IDS)),
        },
        "metrics": {
            name: _unavailable_metric("retrieval phase is outside this semantic-edge treatment Run")
            for name in retrieval_metric_names
        },
    }
    performance_report = {
        "schema_version": CB4_PERFORMANCE_REPORT_VERSION,
        **common,
        "measurement_method": {
            "clock": "time.perf_counter_ns",
            "memory": "python-tracemalloc-peak-per-case-arm",
            "ingest": "isolated-portable-sqlite-transaction-per-case-arm",
            "index": "isolated-portable-sqlite-index-build",
        },
        "sample_count": len(PYTHON_ELIGIBLE_CASE_IDS) * len(TREATMENT_ORDER),
        "treatments": {
            arm: {
                "case_count": aggregates[arm]["case_count"],
                "treatment_time_ms": aggregates[arm]["treatment_time_ms"],
                "treatment_latency_ms_p95": aggregates[arm]["treatment_latency_ms_p95"],
                "ingest_time_ms": aggregates[arm]["ingest_time_ms"],
                "ingest_latency_ms_p95": aggregates[arm]["ingest_latency_ms_p95"],
                "peak_memory_bytes": aggregates[arm]["peak_memory_bytes"],
            }
            for arm in TREATMENT_ORDER
        },
        "index_build_time_ms": round(index_ns / 1_000_000, 6),
        "database_bytes": database_path.stat().st_size,
        "retrieval_latency_ms_p95": _unavailable_metric(
            "retrieval phase is outside this semantic-edge treatment Run"
        ),
    }
    security_report = {
        "schema_version": CB4_SECURITY_REPORT_VERSION,
        **common,
        "status": "clean",
        "isolation": {
            "database": "artifact-local-isolated-sqlite",
            "formal_database_writes": False,
            "old_run_writes": False,
            "network_execution": False,
        },
        "portable_contract": {
            "canonical_json": True,
            "absolute_or_temporary_paths": "absent",
            "credential_assignments": "absent",
            "symlinks": "absent",
            "wal_shm_sidecars": "absent",
            "pyc_or_pycache": "absent",
        },
        "formal_database_before": dict(formal_database_before),
        "old_run_tree_before": {
            "file_count": len(old_runs_before),
            "tree_hash": _fingerprint(old_runs_before),
        },
    }
    attempt_audit = {
        "schema_version": CB4_ATTEMPT_AUDIT_VERSION,
        **common,
        "status": "completed",
        "qualification_status": "NOT QUALIFIED",
        "treatment_qualified": False,
        "qualification_reason": "zero real human edge-label records",
        "gate_authorization": dict(gate_authorization),
        "label_evidence_ref": label_qualification["label_evidence_ref"],
        "label_status": label_qualification["status"],
        "sample_count": label_qualification["sample_count"],
        "quality_acceptance": quality_acceptance,
        "execution_guards": {
            "single_new_run": True,
            "no_overwrite": True,
            "exact_production_identity": True,
            "fixed_python_case_membership": True,
            "formal_database_hash_size_mtime_guard": True,
            "old_run_byte_mtime_guard": True,
        },
    }
    for filename, report in (
        ("treatments.json", treatments_report),
        ("coverage.json", coverage_report),
        ("edge-quality.json", edge_quality_report),
        ("retrieval.json", retrieval_report),
        ("performance.json", performance_report),
        ("security.json", security_report),
        ("attempt-audit.json", attempt_audit),
    ):
        _write_artifact_json(directory / filename, report)

    manifest = {
        "schema_version": CB4_SCHEMA_VERSION,
        "runner_version": CB4_RUNNER_VERSION,
        "status": "completed",
        "execution_mode": "qualified",
        "run_id": run_uri,
        "artifact_id": run_id,
        "executed_at_utc": executed_at_utc,
        "pipeline_profile": PRODUCTION_EXECUTION_PIPELINE_PROFILE,
        "treatment_qualified": False,
        "qualification_status": "NOT QUALIFIED",
        "qualification_reason": "zero real human edge-label records",
        "gate_authorization": dict(gate_authorization),
        "dataset": {
            "id": DATASET_ID,
            "version": DATASET_VERSION,
            "package_hash": PACKAGE_HASH,
            "total_cases": EXPECTED_CASE_COUNT,
            "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
            "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
            "python_treatment_cases": len(PYTHON_ELIGIBLE_CASE_IDS),
            "python_case_membership_hash": _fingerprint(list(PYTHON_ELIGIBLE_CASE_IDS)),
        },
        "controls": controls,
        "production_identity": production_identity,
        "production_output_attestation": production_attestation,
        "label_qualification": label_qualification,
        "quality_acceptance": quality_acceptance,
        "treatment_order": list(TREATMENT_ORDER),
        "treatment_aggregate": aggregates,
        "artifact_files": _artifact_file_records(directory),
    }
    _write_artifact_json(directory / "manifest.json", manifest)
    return manifest


def execute_cb4(
    *,
    root: Path | None = None,
    runs_dir: Path | None = None,
    execution_mode: str = "qualified",
    semantic_edge_component: Any | None = None,
    semantic_edge_factory: Any | None = None,
    c5_02_gate_approved: bool = False,
    labels: Sequence[EdgeLabelRecord] | None = None,
    fixture_edge_count: int | None = None,
) -> Path:
    """Execute and atomically publish the unique authorized production C-B4 Run."""

    resolved_root = (root or repository_root()).resolve()
    resolved_runs = (
        runs_dir.resolve()
        if runs_dir is not None
        else (resolved_root / "evals" / "code" / "runs").resolve()
    )
    gate_authorization = _validate_execution_policy(
        root=resolved_root,
        runs_dir=resolved_runs,
        execution_mode=execution_mode,
        semantic_edge_component=semantic_edge_component,
        semantic_edge_factory=semantic_edge_factory,
        c5_02_gate_approved=c5_02_gate_approved,
    )
    if labels is not None or fixture_edge_count is not None:
        raise CB4Error(
            "authorized production C-B4 has zero real labels; caller label evidence is forbidden"
        )
    if not resolved_runs.is_dir() or resolved_runs.is_symlink():
        raise CB4Error("canonical C-B4 Run directory is unavailable or unsafe")
    existing_cb4_runs = _existing_cb4_run_ids(resolved_runs)
    if existing_cb4_runs:
        raise CB4Error(
            "the unique production C-B4 Run already exists: " + ", ".join(existing_cb4_runs)
        )

    formal_database_before = _formal_database_state(resolved_root)
    old_runs_before = _tree_state(resolved_runs)
    run_directories_before = {
        item.name for item in resolved_runs.iterdir() if item.is_dir() and not item.is_symlink()
    }
    run_id = uuid.uuid4().hex
    run_uri = f"evaluation-run://project-code-golden-v2/{run_id}"
    target = resolved_runs / run_id
    if target.exists() or target.is_symlink():
        raise CB4Error("new C-B4 Run id unexpectedly collides with an existing path")
    executed_at_utc = datetime.now(UTC).replace(microsecond=0).isoformat()

    with tempfile.TemporaryDirectory(
        prefix=".code-c-b4-production-",
        dir=resolved_root.parent,
    ) as temporary:
        temporary_artifact = Path(temporary) / run_id
        _build_production_artifact(
            temporary_artifact,
            root=resolved_root,
            run_id=run_id,
            run_uri=run_uri,
            executed_at_utc=executed_at_utc,
            gate_authorization=gate_authorization,
            formal_database_before=formal_database_before,
            old_runs_before=old_runs_before,
        )
        verify_artifact(temporary_artifact, root=resolved_root)
        if _formal_database_state(resolved_root) != formal_database_before:
            raise CB4Error("formal database changed before C-B4 publication")
        if _tree_state(resolved_runs) != old_runs_before:
            raise CB4Error("an old Run changed before C-B4 publication")
        temporary_artifact.rename(target)
        for path in target.iterdir():
            path.chmod(0o444)
        target.chmod(0o555)

    formal_database_after = _formal_database_state(resolved_root)
    runs_after = _tree_state(resolved_runs)
    prefix = f"{run_id}/"
    old_runs_after = tuple(record for record in runs_after if not record[0].startswith(prefix))
    new_run_records = tuple(record for record in runs_after if record[0].startswith(prefix))
    run_directories_after = {
        item.name for item in resolved_runs.iterdir() if item.is_dir() and not item.is_symlink()
    }
    if formal_database_after != formal_database_before:
        raise CB4Error("formal database changed during C-B4 production execution")
    if old_runs_after != old_runs_before:
        raise CB4Error("an old Run changed during C-B4 production execution")
    if run_directories_after - run_directories_before != {run_id}:
        raise CB4Error("C-B4 execution did not publish exactly one new Run directory")
    if len(new_run_records) != len(EXPECTED_ARTIFACT_ENTRIES):
        raise CB4Error("published C-B4 Run does not contain the exact artifact file set")
    verify_artifact(target, root=resolved_root)
    if (
        _formal_database_state(resolved_root) != formal_database_before
        or tuple(
            record for record in _tree_state(resolved_runs) if not record[0].startswith(prefix)
        )
        != old_runs_before
    ):
        raise CB4Error("verify-only changed the formal database or an old Run")
    return target


def _require_safe_artifact_tree(directory: Path) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise CB4Error("C-B4 artifact must be a non-symlink directory")
    entries = {item.name for item in directory.iterdir()}
    if entries != EXPECTED_ARTIFACT_ENTRIES:
        raise CB4Error("C-B4 artifact entries differ from the frozen portable contract")
    for item in directory.rglob("*"):
        if item.is_symlink():
            raise CB4Error("C-B4 artifact may not contain symlinks")
        if item.is_dir() or item.suffix == ".pyc" or item.name == "__pycache__":
            raise CB4Error("C-B4 artifact may contain only the frozen top-level files")
        if not item.is_file():
            raise CB4Error("C-B4 artifact contains a non-regular entry")
    for suffix in ("-wal", "-shm"):
        if Path(f"{directory / 'evaluation.sqlite3'}{suffix}").exists():
            raise CB4Error("C-B4 portable SQLite may not have WAL/SHM sidecars")


def _require_canonical_json(directory: Path) -> None:
    for path in sorted(directory.glob("*.json")):
        value = _read_json(path)
        if path.read_bytes() != _pretty_json_bytes(value):
            raise CB4Error(f"C-B4 artifact JSON is not canonical: {path.name}")


def _require_portable_sqlite(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            header = stream.read(16)
    except OSError as exc:
        raise CB4Error("C-B4 evaluation.sqlite3 is not readable") from exc
    if header != b"SQLite format 3\x00":
        raise CB4Error("C-B4 evaluation.sqlite3 is not a SQLite database")
    try:
        database = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        try:
            quick_check = database.execute("PRAGMA quick_check").fetchone()
            journal_mode = database.execute("PRAGMA journal_mode").fetchone()
        finally:
            database.close()
    except sqlite3.Error as exc:
        raise CB4Error("C-B4 evaluation.sqlite3 is not portable/readable") from exc
    if quick_check != ("ok",) or not journal_mode or journal_mode[0].casefold() == "wal":
        raise CB4Error("C-B4 evaluation.sqlite3 failed portable integrity checks")


def _require_clean_text_scan(directory: Path) -> None:
    for path in sorted(directory.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        if _TEMPORARY_PATH_RE.search(text):
            raise CB4Error(f"C-B4 artifact contains a temporary/local path: {path.name}")
        if _SECRET_RE.search(text):
            raise CB4Error(f"C-B4 artifact contains secret-like material: {path.name}")
    try:
        scan = baseline.scan_artifact_security(directory)
    except Exception as exc:
        raise CB4Error(f"C-B4 artifact security scan failed: {exc}") from exc
    if not scan.get("clean"):
        raise CB4Error("C-B4 artifact secret or temporary-path scan is not clean")


def _artifact_file_records(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "bytes": (directory / name).stat().st_size,
            "sha256": _sha256_file(directory / name),
        }
        for name in sorted(ARTIFACT_FILES)
    }


def _validate_stored_production_identity(identity: Mapping[str, Any]) -> None:
    implementation_module = str(identity.get("implementation_module") or "")
    class_name = str(identity.get("class") or "")
    if (
        identity.get("public_module") != PUBLIC_CODE_MODULE
        or identity.get("module") != SEMANTIC_EDGE_MODULE
        or implementation_module != SEMANTIC_EDGE_MODULE
        or class_name != REAL_SEMANTIC_EDGE_CLASS
        or identity.get("functional_entry_point") != REAL_SEMANTIC_EDGE_FUNCTION
        or identity.get("evaluation_entry_point") != REAL_SEMANTIC_EVALUATION_FUNCTION
        or identity.get("version") != REAL_SEMANTIC_EDGE_VERSION
        or any(word in class_name.casefold() for word in ("fake", "wrapped", "subclass"))
        or identity.get("identity_policy") != "exact-type-only"
    ):
        raise CB4Error("C-B4 artifact production identity is not an exact production class")


def verify_artifact(
    directory: Path,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Verify a future immutable C-B4 artifact without consulting the live Gate."""

    if directory.is_symlink():
        raise CB4Error("C-B4 artifact must not be a symlink")
    resolved_root = (root or repository_root()).resolve()
    resolved = directory.resolve()
    runs_dir = (resolved_root / "evals" / "code" / "runs").resolve()
    database_before = _formal_database_state(resolved_root)
    runs_before = _tree_state(runs_dir)

    _require_safe_artifact_tree(resolved)
    _require_canonical_json(resolved)
    _require_portable_sqlite(resolved / "evaluation.sqlite3")
    _require_clean_text_scan(resolved)
    manifest = _read_json(resolved / "manifest.json")
    attempt = _read_json(resolved / "attempt-audit.json")
    gate = manifest.get("gate_authorization") or {}
    gate_signed = {
        "schema_version": gate.get("schema_version"),
        "decision": gate.get("decision"),
        "status": gate.get("status"),
        "completion_status": gate.get("completion_status"),
        "evidence": gate.get("evidence"),
        "decision_block_hash": gate.get("decision_block_hash"),
        "production_component": gate.get("production_component"),
    }
    decision_record = {
        "completion_status": "passed",
        "evidence": list(C5_02_GATE_REQUIRED_EVIDENCE),
        "production_execution_authorized": True,
    }
    if (
        manifest.get("schema_version") != CB4_SCHEMA_VERSION
        or manifest.get("status") != "completed"
        or manifest.get("execution_mode") != "qualified"
        or type(manifest.get("treatment_qualified")) is not bool
        or gate.get("schema_version") != C5_02_GATE_SCHEMA_VERSION
        or gate.get("decision") != C5_02_GATE_DECISION
        or gate.get("status") != "authorized"
        or gate.get("completion_status") != "passed"
        or gate.get("evidence") != list(C5_02_GATE_REQUIRED_EVIDENCE)
        or gate.get("decision_block_hash") != _fingerprint(decision_record)
        or gate.get("snapshot_hash") != _fingerprint(gate_signed)
        or not _SHA256_RE.fullmatch(str(gate.get("report_sha256") or ""))
        or gate.get("report") != C5_GATE_RELATIVE_PATH
        or attempt.get("gate_authorization") != gate
    ):
        raise CB4Error("C-B4 artifact lacks a self-contained C5-02 authorization snapshot")
    dataset = manifest.get("dataset") or {}
    if (
        dataset.get("id") != DATASET_ID
        or dataset.get("version") != DATASET_VERSION
        or dataset.get("package_hash") != PACKAGE_HASH
        or dataset.get("total_cases") != EXPECTED_CASE_COUNT
        or dataset.get("eligible_cases") != EXPECTED_ELIGIBLE_COUNT
        or dataset.get("ineligible_cases") != EXPECTED_INELIGIBLE_COUNT
        or dataset.get("python_treatment_cases") != 30
    ):
        raise CB4Error("C-B4 artifact dataset differs from released Golden v2")
    controls = manifest.get("controls") or {}
    if (controls.get("qualified_baseline") or {}).get("run_id") != CB0_QUALIFIED_RUN_ID or (
        controls.get("qualified_baseline") or {}
    ).get("treatment_qualified") is not True:
        raise CB4Error("C-B4 artifact does not bind the unique qualified C-B0 anchor")
    for name, run_id in (
        ("cb1_not_qualified_audit", CB1_AUDIT_RUN_ID),
        ("cb2_not_qualified_audit", CB2_AUDIT_RUN_ID),
        ("cb3_not_qualified_audit", CB3_AUDIT_RUN_ID),
        ("cb5_not_qualified_audit", CB5_AUDIT_RUN_ID),
    ):
        control = controls.get(name) or {}
        if (
            control.get("run_id") != run_id
            or control.get("treatment_qualified") is not False
            or control.get("qualification_use") != "forbidden"
        ):
            raise CB4Error(f"C-B4 artifact promotes the {name} control")
    production_identity = manifest.get("production_identity") or {}
    _validate_stored_production_identity(production_identity)
    if gate.get("production_component") != production_identity:
        raise CB4Error("C-B4 artifact Gate and manifest production identities differ")
    production_attestation = _validate_stored_production_output_attestation(
        manifest.get("production_output_attestation")
    )
    if production_attestation["component_identity"] != production_identity:
        raise CB4Error("C-B4 artifact production-output attestation identity is inconsistent")
    stored_label_qualification = manifest.get("label_qualification")
    if not isinstance(stored_label_qualification, Mapping):
        raise CB4Error("C-B4 artifact lacks canonical label qualification")
    label_dataset = _validate_stored_edge_label_dataset(
        manifest.get("edge_label_dataset"),
        production_attestation=production_attestation,
        root=resolved_root,
    )
    fixture_edge_count = stored_label_qualification.get("fixture_edge_count")
    _require_edge_count("fixture_edge_count", fixture_edge_count)
    label_qualification = edge_precision_sample_plan(
        fixture_edge_count=fixture_edge_count,
        label_dataset=label_dataset,
        production_attestation=production_attestation,
        root=resolved_root,
    )
    if dict(stored_label_qualification) != label_qualification:
        raise CB4Error("C-B4 artifact label count or state is not derived from its records")
    quality_acceptance = edge_quality_acceptance_gate(
        label_qualification=label_qualification,
        treatment_qualified=manifest["treatment_qualified"],
        label_dataset=label_dataset,
        production_attestation=production_attestation,
        root=resolved_root,
    )
    evidence_ref = label_qualification["label_evidence_ref"]
    edge_quality = _read_json(resolved / "edge-quality.json")
    if manifest.get("quality_acceptance") != quality_acceptance:
        raise CB4Error("C-B4 artifact label state and quality acceptance are inconsistent")
    for name, report in (("edge-quality.json", edge_quality), ("attempt-audit.json", attempt)):
        if (
            "edge_label_dataset" in report
            or report.get("label_evidence_ref") != evidence_ref
            or report.get("label_status") != label_qualification["status"]
            or report.get("sample_count") != label_qualification["sample_count"]
            or report.get("quality_acceptance") != quality_acceptance
        ):
            raise CB4Error(f"C-B4 {name} label evidence reference is inconsistent")
    expected_files = manifest.get("artifact_files")
    if expected_files != _artifact_file_records(resolved):
        raise CB4Error("C-B4 artifact file hashes or sizes differ from its manifest")

    database_after = _formal_database_state(resolved_root)
    runs_after = _tree_state(runs_dir)
    _require_repository_unchanged(
        database_before=database_before,
        database_after=database_after,
        runs_before=runs_before,
        runs_after=runs_after,
    )
    return {
        "status": "verified",
        "run_id": manifest.get("run_id"),
        "treatment_qualified": manifest["treatment_qualified"],
        "label_status": label_qualification["status"],
        "label_dataset_hash": evidence_ref["dataset_hash"],
        "production_output_attestation_hash": production_attestation["attestation_hash"],
        "label_sample_count": label_qualification["sample_count"],
        "precision_gate_satisfied": quality_acceptance["precision_gate_satisfied"],
        "gate_decision": C5_02_GATE_DECISION,
        "unique_qualified_baseline_run_id": CB0_QUALIFIED_RUN_ID,
        "audit_control_run_ids": [
            CB1_AUDIT_RUN_ID,
            CB2_AUDIT_RUN_ID,
            CB3_AUDIT_RUN_ID,
            CB5_AUDIT_RUN_ID,
        ],
        "manifest_canonical_hash": _fingerprint(manifest),
    }


def _resolve_artifact(value: str, runs_dir: Path) -> Path:
    candidate = Path(value)
    if candidate.is_dir():
        return candidate.resolve()
    nested = runs_dir / value
    if nested.is_dir():
        return nested.resolve()
    raise CB4Error(f"C-B4 artifact not found: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Code C-B4 Python semantic-edge production evaluation harness"
    )
    parser.add_argument("--repository-root", type=Path, default=repository_root())
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "prepare",
        help="print PREPARED NON-QUALIFIED without a Run, artifact, or metric value",
    )
    execute_parser = subparsers.add_parser(
        "execute",
        help="execute the unique production Run after the C5-02 completion Gate",
    )
    execute_parser.add_argument("--runs-dir", type=Path)
    execute_parser.add_argument("--c5-02-gate-approved", action="store_true")
    verify_parser = subparsers.add_parser(
        "verify",
        help="verify a future immutable production C-B4 artifact",
    )
    verify_parser.add_argument("artifact")
    verify_parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()
    try:
        if args.command == "prepare":
            output = prepare_cb4(root=root)
        elif args.command == "execute":
            artifact = execute_cb4(
                root=root,
                runs_dir=args.runs_dir,
                c5_02_gate_approved=args.c5_02_gate_approved,
            )
            output = verify_artifact(artifact, root=root)
        else:
            runs_dir = (
                args.runs_dir.resolve()
                if args.runs_dir is not None
                else (root / "evals" / "code" / "runs").resolve()
            )
            output = verify_artifact(_resolve_artifact(args.artifact, runs_dir), root=root)
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
