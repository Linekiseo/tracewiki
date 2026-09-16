"""Code C-B6 Diff-to-Symbol evaluation and one-shot production harness.

Preparation is read-only. Production execution is fail-closed and publishes one
new portable artifact only after the final C6-02 Gate and exact frozen public
``DiffSymbolMapper.map_hunk@version`` implementation identity both match.
Fake, injected, wrapped, monkeypatched, and subclassed components cannot satisfy
that identity contract.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import importlib
import inspect
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import tracemalloc
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from evidence_rag.code_identity_v1 import (
    CodeIdentityError,
    portable_code_payload,
    portable_constant_payload,
    portable_function_payload,
)

CB6_SCHEMA_VERSION = "code-c-b6-diff-symbol-audit-v1"
CB6_PREPARATION_VERSION = "code-c-b6-prepared-v1"
CB6_FIXTURE_VERSION = "code-c-b6-programmatic-git-fixture-v1"
CB6_LABEL_VERSION = "code-c-b6-canonical-label-record-v1"
CB6_METRIC_VERSION = "code-c-b6-metric-contract-v1"
CB6_PORTABLE_VERSION = "code-c-b6-portable-artifact-v1"
CB6_RESULTS_VERSION = "code-c-b6-production-results-v1"
CB6_PERFORMANCE_VERSION = "code-c-b6-performance-v1"
CB6_SECURITY_VERSION = "code-c-b6-security-v1"
CB6_SQLITE_SCHEMA_VERSION = 1

C6_GATE_RELATIVE_PATH = "docs/rag-optimization/development/reviews/07_CODE_C6_GATE_REVIEW.md"
C6_02_ENGINEERING_DECISION = "C6-02 engineering PASS"
C6_02_EXECUTION_DECISION = "C-B6 PRODUCTION EXECUTION AUTHORIZED"
C6_02_DEVELOPMENT_DECISION = "C6-02 AUTHORIZED"

PUBLIC_CODE_MODULE = "evidence_rag.rag.sources.code"
DIFF_SYMBOL_MODULE = f"{PUBLIC_CODE_MODULE}.diff_symbol_v2"
REAL_DIFF_SYMBOL_CLASS = "DiffSymbolMapper"
REAL_DIFF_SYMBOL_METHOD = "map_hunk"
REAL_DIFF_SYMBOL_VERSION = "c6-diff-symbol-mapper-v2"
PRODUCTION_COMPONENT_IDENTITY = (
    f"{DIFF_SYMBOL_MODULE}.{REAL_DIFF_SYMBOL_CLASS}."
    f"{REAL_DIFF_SYMBOL_METHOD}@{REAL_DIFF_SYMBOL_VERSION}"
)
CB6_RELEASED_METHOD_IMPLEMENTATION_SHA256 = (
    "sha256:ed93080d100f35acefdaaa3c00bb769d180ae10a74ab9fed0df649e95ce5bc9c"
)
CB6_RUNTIME_METHOD_AUTHORITY_VERSION = "code-c-b6-runtime-method-authority-v2"
CB6_RUNTIME_METHOD_AUTHORITY_SHA256 = (
    "sha256:e0fc9020844dd8b52d2fa077c1607bfc1ab5d55b9adb5f319ba161de7124e58b"
)
QUALIFICATION_CONTRACT = {
    "minimum_labeled_cases": 30,
    "minimum_precision": 0.9,
    "minimum_recall": 0.9,
    "minimum_f1": 0.9,
    "minimum_change_context_recall": 0.9,
    "maximum_false_affected_rate": 0.1,
    "small_sample_status": "PROVISIONAL NOT QUALIFIED",
}

CB0_QUALIFIED_RUN_ID = "evaluation-run://project-code-golden-v2/5a92eafdff5d49e6aae8bb55fdc14061"
AUDIT_CONTROL_RUN_IDS = {
    "C-B1": "evaluation-run://project-code-golden-v2/33f76ed55a3d4c9ab1060a54bfa13d35",
    "C-B2": "evaluation-run://project-code-golden-v2/6fad2dd1c67846668ba38045ddae0064",
    "C-B3": "evaluation-run://project-code-golden-v2/92f8d8e190f449bab9ba253227473419",
    "C-B4": "evaluation-run://project-code-golden-v2/a22b322a141741ae9a710aa347fbf032",
    "C-B5": "evaluation-run://project-code-golden-v2/baf5b9eb49464ca79920030fd1c772de",
}

TREATMENT_ORDER = (
    "line_overlap_legacy",
    "ast_enclosing",
    "ast_rename_lineage_merged",
)
TREATMENT_MATRIX = {
    "line_overlap_legacy": {
        "role": "legacy-control",
        "line_overlap": True,
        "ast_enclosing": False,
        "rename_detection": False,
        "lineage_merge": False,
    },
    "ast_enclosing": {
        "role": "ast-treatment",
        "line_overlap": True,
        "ast_enclosing": True,
        "rename_detection": False,
        "lineage_merge": False,
    },
    "ast_rename_lineage_merged": {
        "role": "merged-treatment",
        "line_overlap": True,
        "ast_enclosing": True,
        "rename_detection": True,
        "lineage_merge": True,
    },
}

METRIC_CONTRACT = {
    "diff_to_symbol_precision": {"unit": "ratio", "source": "canonical-label-records"},
    "diff_to_symbol_recall": {"unit": "ratio", "source": "canonical-label-records"},
    "diff_to_symbol_f1": {"unit": "ratio", "source": "canonical-label-records"},
    "old_side": {"unit": "precision-recall-f1", "source": "canonical-label-records"},
    "new_side": {"unit": "precision-recall-f1", "source": "canonical-label-records"},
    "introduced_slice": {"unit": "precision-recall-f1", "source": "canonical-label-records"},
    "removed_slice": {"unit": "precision-recall-f1", "source": "canonical-label-records"},
    "modified_slice": {"unit": "precision-recall-f1", "source": "canonical-label-records"},
    "renamed_slice": {"unit": "precision-recall-f1", "source": "canonical-label-records"},
    "change_context_recall": {"unit": "ratio", "source": "canonical-label-records"},
    "false_affected_symbol": {"unit": "count-and-ratio", "source": "canonical-label-records"},
    "ambiguity_noise": {"unit": "count-and-ratio", "source": "case-status-records"},
    "unavailable": {"unit": "count-and-ratio", "source": "case-status-records"},
    "latency_p50_p95": {"unit": "milliseconds", "source": "monotonic-observations"},
    "memory": {"unit": "bytes", "source": "peak-observations"},
}

FIXTURE_CASE_IDS = (
    "cb6-function-body",
    "cb6-function-signature",
    "cb6-class-level",
    "cb6-file-top-level",
    "cb6-rename",
    "cb6-add",
    "cb6-delete",
    "cb6-multi-symbol",
    "cb6-whitespace-only",
    "cb6-binary",
    "cb6-wrong-parent",
    "cb6-ambiguous",
)
FROZEN_FIXTURE_DIGEST = "sha256:d75b530049a249adf1fefb52bf21f3a84301301a8be9ceb0e6ca522d062d4518"
FROZEN_LABEL_MEMBERSHIP_DIGEST = (
    "sha256:48d74df8b1914af770e343f0a604bdb71dd5bdded7101cb1c2d7cf07a27ee4cf"
)
FROZEN_DENOMINATOR_MEMBERSHIP_DIGEST = (
    "sha256:609a11322a88a21b6db9b3c82ed2e908f537fed863997ab2017975fe8d6fb4da"
)

LabelStatus = Literal["labeled", "noise", "unavailable", "rejected", "ambiguous"]
ResultStatus = Literal["available", "noise", "unavailable", "rejected", "ambiguous"]
SymbolSide = Literal["old", "new"]
SymbolRole = Literal["introduced", "removed", "modified", "renamed"]

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CASE_RE = re.compile(r"^cb6-[a-z0-9-]+$")
_GATE_ENGINEERING_RE = re.compile(r"^c6-02\s+engineering\s+(pass|fail)$", re.IGNORECASE)
_GATE_FINDING_RE = re.compile(r"^p([01])\s+findings\s*:\s*([0-9]+)$", re.IGNORECASE)
_GATE_COMBINED_FINDING_RE = re.compile(
    r"^p0\s*/\s*p1(?:\s+(?:blocking\s+)?findings)?\s*[:=]\s*([0-9]+)$",
    re.IGNORECASE,
)
_GATE_EXECUTION_RE = re.compile(
    r"^c-b6\s+production\s+execution\s+(not\s+authorized|authorized)$",
    re.IGNORECASE,
)
_GATE_IDENTITY_RE = re.compile(
    r"^c-b6\s+production\s+component\s+identity\s*:\s*(\S+)$",
    re.IGNORECASE,
)
_GATE_TIMESTAMP_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\b")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_POSIX_ABSOLUTE_RE = re.compile(r"(?:^|[\s\"'=:(])/(?!/)[A-Za-z._~-][^\s]*")
_SECRET_KEY_RE = re.compile(r"(?i)^(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)$")
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{8,}"
)


class CB6Error(RuntimeError):
    """The C-B6 preparation, execution, or verification boundary failed closed."""


@runtime_checkable
class DiffSymbolMapperProtocol(Protocol):
    """Minimum future public surface expected from the production component."""

    def map_hunk(self, *args: Any, **kwargs: Any) -> Any:
        """Bind one canonical diff hunk to old/new symbol versions."""


@dataclass(frozen=True, slots=True)
class LineRange:
    start: int
    count: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.count < 0:
            raise CB6Error("line ranges must be non-negative")
        if self.count and self.start < 1:
            raise CB6Error("non-empty line ranges are one-based")

    @property
    def end(self) -> int:
        return self.start + self.count - 1 if self.count else self.start

    def to_dict(self) -> dict[str, int]:
        return {"start": self.start, "count": self.count, "end": self.end}


@dataclass(frozen=True, slots=True)
class SymbolLabel:
    symbol_id: str
    side: SymbolSide
    role: SymbolRole
    path: str
    qualified_name: str
    symbol_range: LineRange
    lineage_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol_id": self.symbol_id,
            "side": self.side,
            "role": self.role,
            "path": self.path,
            "qualified_name": self.qualified_name,
            "symbol_range": self.symbol_range.to_dict(),
            "lineage_id": self.lineage_id,
        }


@dataclass(frozen=True, slots=True)
class CanonicalLabelRecord:
    case_id: str
    old_commit_sha: str
    new_commit_sha: str
    target_parent_sha: str
    old_path: str | None
    new_path: str | None
    old_range: LineRange | None
    new_range: LineRange | None
    status: LabelStatus
    expected_symbols: tuple[SymbolLabel, ...]
    ambiguity_candidates: tuple[str, ...] = ()
    change_context_required: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CB6_LABEL_VERSION,
            "case_id": self.case_id,
            "old_commit_sha": self.old_commit_sha,
            "new_commit_sha": self.new_commit_sha,
            "target_parent_sha": self.target_parent_sha,
            "old_path": self.old_path,
            "new_path": self.new_path,
            "old_range": self.old_range.to_dict() if self.old_range is not None else None,
            "new_range": self.new_range.to_dict() if self.new_range is not None else None,
            "status": self.status,
            "expected_symbols": [item.to_dict() for item in self.expected_symbols],
            "ambiguity_candidates": list(self.ambiguity_candidates),
            "change_context_required": self.change_context_required,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class CB6Fixture:
    repository: Path
    commits: Mapping[str, str]
    labels: tuple[CanonicalLabelRecord, ...]
    membership_digest: str

    def canonical_payload(self) -> dict[str, Any]:
        """Return the portable fixture identity; the temporary path is excluded."""

        return {
            "schema_version": CB6_FIXTURE_VERSION,
            "repository_id": "cb6-programmatic-fixture",
            "commits": dict(sorted(self.commits.items())),
            "case_ids": [record.case_id for record in self.labels],
            "labels": [record.to_dict() for record in self.labels],
            "membership_digest": self.membership_digest,
        }


@dataclass(frozen=True, slots=True)
class ArmCaseResult:
    case_id: str
    old_commit_sha: str
    new_commit_sha: str
    status: ResultStatus
    symbols: tuple[SymbolLabel, ...]
    change_context_hit: bool | None
    duration_ns: int
    peak_memory_bytes: int

    def __post_init__(self) -> None:
        if self.duration_ns < 0 or self.peak_memory_bytes < 0:
            raise CB6Error("performance observations must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "old_commit_sha": self.old_commit_sha,
            "new_commit_sha": self.new_commit_sha,
            "status": self.status,
            "symbols": [item.to_dict() for item in self.symbols],
            "change_context_hit": self.change_context_hit,
            "duration_ns": self.duration_ns,
            "peak_memory_bytes": self.peak_memory_bytes,
        }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_json_text(value: Any) -> str:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def canonical_membership_digest(records: Sequence[CanonicalLabelRecord]) -> str:
    """Hash complete, ordered canonical records so label tampering is detectable."""

    ordered = sorted(records, key=lambda record: record.case_id)
    return _fingerprint(
        {
            "schema_version": CB6_FIXTURE_VERSION,
            "case_ids": [record.case_id for record in ordered],
            "records": [record.to_dict() for record in ordered],
        }
    )


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def validate_label_records(
    records: Sequence[CanonicalLabelRecord],
    *,
    expected_digest: str | None = None,
) -> str:
    """Validate canonical membership and optionally enforce its frozen digest."""

    if not records:
        raise CB6Error("C-B6 canonical membership must not be empty")
    case_ids = [record.case_id for record in records]
    if len(case_ids) != len(set(case_ids)):
        raise CB6Error("C-B6 canonical case ids must be unique")
    if tuple(case_ids) != tuple(sorted(case_ids)):
        raise CB6Error("C-B6 canonical records must be sorted by case id")
    if tuple(case_ids) != tuple(sorted(FIXTURE_CASE_IDS)):
        raise CB6Error("C-B6 canonical membership differs from the frozen fixture cases")

    for record in records:
        if not _CASE_RE.fullmatch(record.case_id):
            raise CB6Error(f"invalid C-B6 case id: {record.case_id}")
        for value in (
            record.old_commit_sha,
            record.new_commit_sha,
            record.target_parent_sha,
        ):
            if not _SHA_RE.fullmatch(value):
                raise CB6Error(f"case {record.case_id} contains a non-canonical Git SHA")
        for path in (record.old_path, record.new_path):
            if path is not None and not _safe_relative_path(path):
                raise CB6Error(f"case {record.case_id} contains a non-portable path")
        if record.status == "labeled" and not record.expected_symbols:
            raise CB6Error(f"labeled case {record.case_id} has zero canonical symbols")
        if record.status != "labeled" and record.expected_symbols:
            raise CB6Error(f"non-labeled case {record.case_id} cannot carry quality labels")
        if record.status == "ambiguous" and len(record.ambiguity_candidates) < 2:
            raise CB6Error("ambiguous cases require at least two transparent candidates")
        if record.status != "ambiguous" and record.ambiguity_candidates:
            raise CB6Error("only ambiguous cases may carry ambiguity candidates")
        if record.change_context_required != (record.status == "labeled"):
            raise CB6Error("change-context denominator must be exactly the labeled cases")

        identities: set[tuple[str, str, str]] = set()
        for symbol in record.expected_symbols:
            expected_path = record.old_path if symbol.side == "old" else record.new_path
            if symbol.path != expected_path:
                raise CB6Error(f"case {record.case_id} symbol path differs from its side")
            if not _safe_relative_path(symbol.path):
                raise CB6Error(f"case {record.case_id} symbol path is not portable")
            identity = (symbol.side, symbol.symbol_id, symbol.role)
            if identity in identities:
                raise CB6Error(f"case {record.case_id} repeats a canonical symbol label")
            identities.add(identity)

    digest = canonical_membership_digest(records)
    if expected_digest is not None and (
        not _DIGEST_RE.fullmatch(expected_digest) or digest != expected_digest
    ):
        raise CB6Error("C-B6 canonical label membership digest mismatch")
    return digest


def _git_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "C-B6 Fixture",
            "GIT_AUTHOR_EMAIL": "cb6-fixture@example.invalid",
            "GIT_COMMITTER_NAME": "C-B6 Fixture",
            "GIT_COMMITTER_EMAIL": "cb6-fixture@example.invalid",
            "LC_ALL": "C",
            "TZ": "UTC",
        }
    )
    return environment


def _git(repository: Path, *arguments: str, text: bool = True) -> str | bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            env=_git_environment(),
            check=True,
            capture_output=True,
            text=text,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CB6Error(f"programmatic Git fixture command failed: git {arguments[0]}") from exc
    return completed.stdout.strip() if text else completed.stdout


def _write_fixture_text(repository: Path, relative: str, content: str) -> None:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _commit(repository: Path, message: str, timestamp: str) -> str:
    _git(repository, "add", "--all")
    environment = _git_environment()
    environment["GIT_AUTHOR_DATE"] = timestamp
    environment["GIT_COMMITTER_DATE"] = timestamp
    try:
        subprocess.run(
            ["git", "commit", "--quiet", "--no-gpg-sign", "--message", message],
            cwd=repository,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CB6Error("unable to commit the programmatic C-B6 Git fixture") from exc
    value = _git(repository, "rev-parse", "--verify", "HEAD")
    assert isinstance(value, str)
    if not _SHA_RE.fullmatch(value):
        raise CB6Error("programmatic fixture did not produce a canonical SHA-1 commit")
    return value


def _line_number(source: str, needle: str) -> int:
    matches = [index for index, line in enumerate(source.splitlines(), start=1) if needle in line]
    if len(matches) != 1:
        raise CB6Error(f"fixture marker must occur exactly once: {needle}")
    return matches[0]


def _covering_range(source: str, first: str, last: str) -> LineRange:
    start = _line_number(source, first)
    end = _line_number(source, last)
    if end < start:
        raise CB6Error("fixture range markers are reversed")
    return LineRange(start=start, count=end - start + 1)


def _python_span(source: str, qualified_name: str) -> LineRange:
    lines = source.splitlines()
    if qualified_name == "<module>":
        return LineRange(start=1, count=len(lines))
    tree = ast.parse(source)
    matches: list[ast.AST] = []

    def visit(body: Sequence[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                current = (*prefix, node.name)
                if ".".join(current) == qualified_name:
                    matches.append(node)
                visit(node.body, current)

    visit(tree.body)
    if len(matches) != 1:
        raise CB6Error(f"fixture symbol must resolve exactly once: {qualified_name}")
    node = matches[0]
    end = getattr(node, "end_lineno", None)
    if not isinstance(end, int):
        raise CB6Error("Python AST did not provide an end line")
    return LineRange(start=node.lineno, count=end - node.lineno + 1)


def _symbol(
    *,
    source: str,
    path: str,
    qualified_name: str,
    side: SymbolSide,
    role: SymbolRole,
    lineage_id: str,
) -> SymbolLabel:
    return SymbolLabel(
        symbol_id=f"python:{path}:{qualified_name}",
        side=side,
        role=role,
        path=path,
        qualified_name=qualified_name,
        symbol_range=_python_span(source, qualified_name),
        lineage_id=lineage_id,
    )


def _fixture_sources() -> dict[str, str | bytes]:
    return {
        "base_calculator": """TAX_RATE = 0.10


def helper(value: int) -> int:
    return int(value + 1)


class Calculator:
    mode = "basic"

    def compute(self, amount: int) -> int:
        subtotal = amount + 1
        return subtotal


def alpha(value: int) -> int:
    return value + 1


def beta(value: int) -> int:
    return value - 1
""",
        "target_calculator": """TAX_RATE = 0.20


def helper(value: int, scale: int = 1) -> int:
    return int(value + scale)


class Calculator:
    mode = "precise"

    def compute(self, amount: int) -> int:
        subtotal = amount + 2
        return subtotal


def alpha(value: int) -> int:
    return value + 2


def beta(value: int) -> int:
    return value - 2
""",
        "base_legacy": '''"""Rename fixture with enough stable content for Git rename detection."""

CONSTANT = 7


def legacy_name(value: int) -> int:
    """Return one adjusted value."""
    adjusted = value + CONSTANT
    return adjusted


def stable_helper(value: int) -> int:
    return value * 2
''',
        "target_modern": '''"""Rename fixture with enough stable content for Git rename detection."""

CONSTANT = 7


def modern_name(value: int) -> int:
    """Return one adjusted value."""
    adjusted = value + CONSTANT
    return adjusted


def stable_helper(value: int) -> int:
    return value * 2
''',
        "added": """def introduced(value: int) -> int:
    return value * 3
""",
        "removed": """def removed(value: int) -> int:
    return value / 3
""",
        "base_spacing": """def spacing() -> int:
    return 1
""",
        "target_spacing": """def spacing() -> int:
    return  1
""",
        "base_ambiguous": """def left() -> int:
    return 1


# shared ownership marker


def right() -> int:
    return 2
""",
        "target_ambiguous": """def left() -> int:
    return 1


# shared ownership marker changed


def right() -> int:
    return 2
""",
        "base_binary": b"\x00CB6\x01old\xff",
        "target_binary": b"\x00CB6\x02new\xff",
    }


def _build_labels(
    commits: Mapping[str, str], source: Mapping[str, str | bytes]
) -> tuple[CanonicalLabelRecord, ...]:
    seed = commits["seed"]
    base = commits["base"]
    target = commits["target"]
    old_calc = str(source["base_calculator"])
    new_calc = str(source["target_calculator"])
    old_legacy = str(source["base_legacy"])
    new_modern = str(source["target_modern"])
    added = str(source["added"])
    removed = str(source["removed"])
    old_spacing = str(source["base_spacing"])
    new_spacing = str(source["target_spacing"])
    old_ambiguous = str(source["base_ambiguous"])
    new_ambiguous = str(source["target_ambiguous"])

    def record(
        *,
        case_id: str,
        old_path: str | None,
        new_path: str | None,
        old_range: LineRange | None,
        new_range: LineRange | None,
        status: LabelStatus,
        expected: Sequence[SymbolLabel] = (),
        candidates: Sequence[str] = (),
        old_sha: str = base,
        note: str,
    ) -> CanonicalLabelRecord:
        return CanonicalLabelRecord(
            case_id=case_id,
            old_commit_sha=old_sha,
            new_commit_sha=target,
            target_parent_sha=base,
            old_path=old_path,
            new_path=new_path,
            old_range=old_range,
            new_range=new_range,
            status=status,
            expected_symbols=tuple(expected),
            ambiguity_candidates=tuple(candidates),
            change_context_required=status == "labeled",
            note=note,
        )

    records = [
        record(
            case_id="cb6-function-body",
            old_path="src/calculator.py",
            new_path="src/calculator.py",
            old_range=_covering_range(old_calc, "subtotal = amount + 1", "subtotal = amount + 1"),
            new_range=_covering_range(new_calc, "subtotal = amount + 2", "subtotal = amount + 2"),
            status="labeled",
            expected=(
                _symbol(
                    source=old_calc,
                    path="src/calculator.py",
                    qualified_name="Calculator.compute",
                    side="old",
                    role="modified",
                    lineage_id="lineage:calculator-compute",
                ),
                _symbol(
                    source=new_calc,
                    path="src/calculator.py",
                    qualified_name="Calculator.compute",
                    side="new",
                    role="modified",
                    lineage_id="lineage:calculator-compute",
                ),
            ),
            note="Python method body change",
        ),
        record(
            case_id="cb6-function-signature",
            old_path="src/calculator.py",
            new_path="src/calculator.py",
            old_range=_covering_range(old_calc, "def helper(", "return int(value + 1)"),
            new_range=_covering_range(new_calc, "def helper(", "return int(value + scale)"),
            status="labeled",
            expected=(
                _symbol(
                    source=old_calc,
                    path="src/calculator.py",
                    qualified_name="helper",
                    side="old",
                    role="modified",
                    lineage_id="lineage:helper",
                ),
                _symbol(
                    source=new_calc,
                    path="src/calculator.py",
                    qualified_name="helper",
                    side="new",
                    role="modified",
                    lineage_id="lineage:helper",
                ),
            ),
            note="Python function signature and body change",
        ),
        record(
            case_id="cb6-class-level",
            old_path="src/calculator.py",
            new_path="src/calculator.py",
            old_range=_covering_range(old_calc, 'mode = "basic"', 'mode = "basic"'),
            new_range=_covering_range(new_calc, 'mode = "precise"', 'mode = "precise"'),
            status="labeled",
            expected=(
                _symbol(
                    source=old_calc,
                    path="src/calculator.py",
                    qualified_name="Calculator",
                    side="old",
                    role="modified",
                    lineage_id="lineage:calculator",
                ),
                _symbol(
                    source=new_calc,
                    path="src/calculator.py",
                    qualified_name="Calculator",
                    side="new",
                    role="modified",
                    lineage_id="lineage:calculator",
                ),
            ),
            note="Class-level attribute change outside a method",
        ),
        record(
            case_id="cb6-file-top-level",
            old_path="src/calculator.py",
            new_path="src/calculator.py",
            old_range=_covering_range(old_calc, "TAX_RATE = 0.10", "TAX_RATE = 0.10"),
            new_range=_covering_range(new_calc, "TAX_RATE = 0.20", "TAX_RATE = 0.20"),
            status="labeled",
            expected=(
                _symbol(
                    source=old_calc,
                    path="src/calculator.py",
                    qualified_name="<module>",
                    side="old",
                    role="modified",
                    lineage_id="lineage:calculator-module",
                ),
                _symbol(
                    source=new_calc,
                    path="src/calculator.py",
                    qualified_name="<module>",
                    side="new",
                    role="modified",
                    lineage_id="lineage:calculator-module",
                ),
            ),
            note="File-top-level assignment change",
        ),
        record(
            case_id="cb6-rename",
            old_path="src/legacy.py",
            new_path="src/modern.py",
            old_range=_covering_range(old_legacy, "def legacy_name(", "def legacy_name("),
            new_range=_covering_range(new_modern, "def modern_name(", "def modern_name("),
            status="labeled",
            expected=(
                _symbol(
                    source=old_legacy,
                    path="src/legacy.py",
                    qualified_name="legacy_name",
                    side="old",
                    role="renamed",
                    lineage_id="lineage:renamed-function",
                ),
                _symbol(
                    source=new_modern,
                    path="src/modern.py",
                    qualified_name="modern_name",
                    side="new",
                    role="renamed",
                    lineage_id="lineage:renamed-function",
                ),
            ),
            note="Git file rename plus Python symbol rename",
        ),
        record(
            case_id="cb6-add",
            old_path=None,
            new_path="src/added.py",
            old_range=LineRange(0, 0),
            new_range=LineRange(1, len(added.splitlines())),
            status="labeled",
            expected=(
                _symbol(
                    source=added,
                    path="src/added.py",
                    qualified_name="introduced",
                    side="new",
                    role="introduced",
                    lineage_id="lineage:introduced",
                ),
            ),
            note="Added Python file and function",
        ),
        record(
            case_id="cb6-delete",
            old_path="src/removed.py",
            new_path=None,
            old_range=LineRange(1, len(removed.splitlines())),
            new_range=LineRange(0, 0),
            status="labeled",
            expected=(
                _symbol(
                    source=removed,
                    path="src/removed.py",
                    qualified_name="removed",
                    side="old",
                    role="removed",
                    lineage_id="lineage:removed",
                ),
            ),
            note="Deleted Python file and function",
        ),
        record(
            case_id="cb6-multi-symbol",
            old_path="src/calculator.py",
            new_path="src/calculator.py",
            old_range=_covering_range(old_calc, "def alpha(", "return value - 1"),
            new_range=_covering_range(new_calc, "def alpha(", "return value - 2"),
            status="labeled",
            expected=tuple(
                _symbol(
                    source=source_text,
                    path="src/calculator.py",
                    qualified_name=name,
                    side=side,
                    role="modified",
                    lineage_id=f"lineage:{name}",
                )
                for side, source_text in (("old", old_calc), ("new", new_calc))
                for name in ("alpha", "beta")
            ),
            note="One hunk range crosses two sibling functions",
        ),
        record(
            case_id="cb6-whitespace-only",
            old_path="src/spacing.py",
            new_path="src/spacing.py",
            old_range=_covering_range(old_spacing, "return 1", "return 1"),
            new_range=_covering_range(new_spacing, "return  1", "return  1"),
            status="noise",
            note="Whitespace-only textual change has zero quality labels",
        ),
        record(
            case_id="cb6-binary",
            old_path="assets/blob.bin",
            new_path="assets/blob.bin",
            old_range=None,
            new_range=None,
            status="unavailable",
            note="Binary diff must remain honestly unavailable",
        ),
        record(
            case_id="cb6-wrong-parent",
            old_path="src/calculator.py",
            new_path="src/calculator.py",
            old_range=None,
            new_range=None,
            status="rejected",
            old_sha=seed,
            note="Supplied parent is not the target commit parent",
        ),
        record(
            case_id="cb6-ambiguous",
            old_path="src/ambiguous.py",
            new_path="src/ambiguous.py",
            old_range=_covering_range(
                old_ambiguous,
                "# shared ownership marker",
                "# shared ownership marker",
            ),
            new_range=_covering_range(
                new_ambiguous,
                "# shared ownership marker changed",
                "# shared ownership marker changed",
            ),
            status="ambiguous",
            candidates=(
                "python:src/ambiguous.py:left",
                "python:src/ambiguous.py:right",
            ),
            note="Top-level comment between two symbols has no canonical sole owner",
        ),
    ]
    return tuple(sorted(records, key=lambda item: item.case_id))


def build_programmatic_fixture(destination: Path) -> CB6Fixture:
    """Create a deterministic, offline Git fixture below a caller-owned temp path."""

    repository = destination.resolve()
    if repository.exists() and any(repository.iterdir()):
        raise CB6Error("programmatic C-B6 fixture destination must be empty")
    repository.mkdir(parents=True, exist_ok=True)
    _git(repository, "init", "--quiet", "--initial-branch=main", "--object-format=sha1")
    _git(repository, "config", "core.autocrlf", "false")
    _git(repository, "config", "core.filemode", "false")

    _write_fixture_text(repository, "README.md", "C-B6 deterministic fixture\n")
    seed = _commit(repository, "seed", "2001-01-01T00:00:00+00:00")

    source = _fixture_sources()
    _write_fixture_text(repository, "src/calculator.py", str(source["base_calculator"]))
    _write_fixture_text(repository, "src/legacy.py", str(source["base_legacy"]))
    _write_fixture_text(repository, "src/removed.py", str(source["removed"]))
    _write_fixture_text(repository, "src/spacing.py", str(source["base_spacing"]))
    _write_fixture_text(repository, "src/ambiguous.py", str(source["base_ambiguous"]))
    binary_path = repository / "assets/blob.bin"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_bytes(bytes(source["base_binary"]))
    base = _commit(repository, "base", "2001-01-02T00:00:00+00:00")

    _write_fixture_text(repository, "src/calculator.py", str(source["target_calculator"]))
    (repository / "src/legacy.py").rename(repository / "src/modern.py")
    _write_fixture_text(repository, "src/modern.py", str(source["target_modern"]))
    _write_fixture_text(repository, "src/added.py", str(source["added"]))
    (repository / "src/removed.py").unlink()
    _write_fixture_text(repository, "src/spacing.py", str(source["target_spacing"]))
    _write_fixture_text(repository, "src/ambiguous.py", str(source["target_ambiguous"]))
    binary_path.write_bytes(bytes(source["target_binary"]))
    target = _commit(repository, "target", "2001-01-03T00:00:00+00:00")

    commits = {"seed": seed, "base": base, "target": target}
    labels = _build_labels(commits, source)
    digest = validate_label_records(labels)
    fixture = CB6Fixture(
        repository=repository,
        commits=commits,
        labels=labels,
        membership_digest=digest,
    )
    validate_programmatic_fixture(fixture)
    return fixture


def _git_blob(repository: Path, commit: str, path: str) -> bytes | None:
    try:
        value = _git(repository, "show", f"{commit}:{path}", text=False)
    except CB6Error:
        return None
    assert isinstance(value, bytes)
    return value


def validate_programmatic_fixture(fixture: CB6Fixture) -> dict[str, Any]:
    """Validate Git ancestry, blobs, ranges, rename evidence, and membership."""

    repository = fixture.repository.resolve()
    if not (repository / ".git").is_dir():
        raise CB6Error("C-B6 fixture is not a Git worktree")
    commits = dict(fixture.commits)
    if set(commits) != {"seed", "base", "target"}:
        raise CB6Error("C-B6 fixture commit membership is incomplete")
    for name, sha in commits.items():
        if not _SHA_RE.fullmatch(sha):
            raise CB6Error(f"C-B6 fixture {name} commit is not a full SHA")
        observed = _git(repository, "rev-parse", "--verify", f"{sha}^{{commit}}")
        if observed != sha:
            raise CB6Error(f"C-B6 fixture {name} commit cannot be resolved exactly")
    if _git(repository, "rev-parse", f"{commits['target']}^") != commits["base"]:
        raise CB6Error("C-B6 fixture target parent differs from canonical base")
    if _git(repository, "rev-parse", f"{commits['base']}^") != commits["seed"]:
        raise CB6Error("C-B6 fixture base parent differs from canonical seed")

    digest = validate_label_records(
        fixture.labels,
        expected_digest=fixture.membership_digest,
    )
    if (
        digest != FROZEN_LABEL_MEMBERSHIP_DIGEST
        or _fingerprint(fixture.canonical_payload()) != FROZEN_FIXTURE_DIGEST
    ):
        raise CB6Error("C-B6 fixture or canonical labels differ from the frozen contract")
    for record in fixture.labels:
        if record.case_id == "cb6-wrong-parent":
            if record.old_commit_sha == record.target_parent_sha:
                raise CB6Error("wrong-parent case accidentally uses the correct target parent")
            continue
        if record.old_commit_sha != record.target_parent_sha:
            raise CB6Error(f"case {record.case_id} does not use the target parent")
        for side, commit, path, line_range in (
            ("old", record.old_commit_sha, record.old_path, record.old_range),
            ("new", record.new_commit_sha, record.new_path, record.new_range),
        ):
            if path is None:
                if line_range is None or line_range.count != 0:
                    raise CB6Error(f"case {record.case_id} missing side must use an empty range")
                continue
            blob = _git_blob(repository, commit, path)
            if blob is None:
                raise CB6Error(f"case {record.case_id} {side} blob is missing")
            if line_range is not None and line_range.count:
                try:
                    line_count = len(blob.decode("utf-8").splitlines())
                except UnicodeDecodeError as exc:
                    raise CB6Error("binary cases cannot carry textual ranges") from exc
                if line_range.end > line_count:
                    raise CB6Error(f"case {record.case_id} {side} range exceeds its blob")

    rename_status = _git(
        repository,
        "diff",
        "--name-status",
        "--find-renames=40%",
        commits["base"],
        commits["target"],
        "--",
        "src/legacy.py",
        "src/modern.py",
    )
    if not isinstance(rename_status, str) or not rename_status.startswith("R"):
        raise CB6Error("C-B6 fixture did not retain Git rename evidence")
    whitespace_diff = _git(
        repository,
        "diff",
        "--ignore-all-space",
        commits["base"],
        commits["target"],
        "--",
        "src/spacing.py",
    )
    if whitespace_diff:
        raise CB6Error("C-B6 whitespace-only fixture contains a semantic text change")
    return {
        "status": "valid",
        "commit_count": 3,
        "case_count": len(fixture.labels),
        "labeled_case_count": sum(record.status == "labeled" for record in fixture.labels),
        "canonical_symbol_label_count": sum(
            len(record.expected_symbols) for record in fixture.labels
        ),
        "membership_digest": digest,
        "temporary_repository_in_portable_identity": False,
    }


def _symbol_key(case_id: str, symbol: SymbolLabel) -> tuple[str, str, str, str]:
    return case_id, symbol.side, symbol.symbol_id, symbol.role


def require_same_denominator(
    labels: Sequence[CanonicalLabelRecord],
    arms: Mapping[str, Sequence[ArmCaseResult]],
) -> dict[str, Any]:
    """Require all arms to use the same case and old/new commit membership."""

    if tuple(arms) != TREATMENT_ORDER:
        raise CB6Error("C-B6 arms differ from the frozen three-arm order")
    label_map = {record.case_id: record for record in labels}
    expected_ids = tuple(sorted(label_map))
    if expected_ids != tuple(sorted(FIXTURE_CASE_IDS)):
        raise CB6Error("C-B6 denominator is not the frozen fixture membership")
    arm_digests: dict[str, str] = {}
    for arm, results in arms.items():
        ordered = sorted(results, key=lambda result: result.case_id)
        result_ids = tuple(result.case_id for result in ordered)
        if result_ids != expected_ids or len(result_ids) != len(set(result_ids)):
            raise CB6Error(f"C-B6 arm {arm} differs from the canonical denominator")
        membership: list[dict[str, str]] = []
        for result in ordered:
            record = label_map[result.case_id]
            if (
                result.old_commit_sha != record.old_commit_sha
                or result.new_commit_sha != record.new_commit_sha
            ):
                raise CB6Error(f"C-B6 arm {arm} commit membership drifted")
            membership.append(
                {
                    "case_id": result.case_id,
                    "old_commit_sha": result.old_commit_sha,
                    "new_commit_sha": result.new_commit_sha,
                }
            )
        arm_digests[arm] = _fingerprint(membership)
    if len(set(arm_digests.values())) != 1:
        raise CB6Error("C-B6 arms do not share one parent/target membership")
    if next(iter(arm_digests.values())) != FROZEN_DENOMINATOR_MEMBERSHIP_DIGEST:
        raise CB6Error("C-B6 denominator differs from the frozen parent/target membership")
    return {
        "case_count": len(expected_ids),
        "case_ids": list(expected_ids),
        "membership_digest": next(iter(arm_digests.values())),
        "arm_membership_digests": arm_digests,
        "same_denominator": True,
        "same_parent_target_membership": True,
    }


def _prf(
    expected: set[tuple[str, str, str, str]],
    predicted: set[tuple[str, str, str, str]],
) -> dict[str, int | float | None]:
    true_positive = len(expected & predicted)
    false_positive = len(predicted - expected)
    false_negative = len(expected - predicted)
    precision = true_positive / len(predicted) if predicted else None
    recall = true_positive / len(expected) if expected else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 12) if precision is not None else None,
        "recall": round(recall, 12) if recall is not None else None,
        "f1": round(f1, 12) if f1 is not None else None,
    }


def _nearest_rank(values: Sequence[int], percentile: int) -> int:
    if not values:
        raise CB6Error("percentiles require at least one observation")
    ordered = sorted(values)
    index = max(0, (percentile * len(ordered) + 99) // 100 - 1)
    return ordered[index]


_PRODUCTION_METRIC_TOKEN = object()


def calculate_arm_metrics(
    labels: Sequence[CanonicalLabelRecord],
    results: Sequence[ArmCaseResult],
    *,
    arm: str,
    measurement_source: str = "unit-test-fixture-values",
    _production_token: object | None = None,
) -> dict[str, Any]:
    """Calculate metric-contract values only from canonical labels and observations."""

    if arm not in TREATMENT_ORDER:
        raise CB6Error(f"unknown C-B6 treatment arm: {arm}")
    production_result = (
        measurement_source == "production-cb6-run" and _production_token is _PRODUCTION_METRIC_TOKEN
    )
    if measurement_source.startswith("production") and not production_result:
        raise CB6Error("prepared C-B6 metric math cannot claim production evidence")
    label_map = {record.case_id: record for record in labels}
    result_map = {result.case_id: result for result in results}
    if len(result_map) != len(results) or set(result_map) != set(label_map):
        raise CB6Error("C-B6 metric results differ from canonical case membership")
    for case_id, result in result_map.items():
        record = label_map[case_id]
        if (
            result.old_commit_sha != record.old_commit_sha
            or result.new_commit_sha != record.new_commit_sha
        ):
            raise CB6Error(f"C-B6 result {case_id} commit membership drifted")

    expected = {
        _symbol_key(record.case_id, symbol)
        for record in labels
        for symbol in record.expected_symbols
    }
    predicted = {
        _symbol_key(result.case_id, symbol) for result in results for symbol in result.symbols
    }
    overall = _prf(expected, predicted)
    by_side = {
        side: _prf(
            {item for item in expected if item[1] == side},
            {item for item in predicted if item[1] == side},
        )
        for side in ("old", "new")
    }
    by_role = {
        role: _prf(
            {item for item in expected if item[3] == role},
            {item for item in predicted if item[3] == role},
        )
        for role in ("introduced", "removed", "modified", "renamed")
    }

    context_cases = [record.case_id for record in labels if record.change_context_required]
    context_hits = sum(result_map[case_id].change_context_hit is True for case_id in context_cases)
    status_counts = Counter(record.status for record in labels)
    observed_status_counts = Counter(result.status for result in results)
    false_affected = int(overall["false_positive"])
    false_affected_rate = false_affected / len(predicted) if predicted else 0.0
    durations = [result.duration_ns for result in results]
    memory = [result.peak_memory_bytes for result in results]
    return {
        "schema_version": CB6_METRIC_VERSION,
        "arm": arm,
        "measurement_source": measurement_source,
        "production_result": production_result,
        "quality_claim": (
            "derived-production-diff-binding-audit" if production_result else "forbidden"
        ),
        "label_status": {
            "record_count": len(labels),
            "status_counts": dict(sorted(status_counts.items())),
            "labeled_case_count": status_counts["labeled"],
            "canonical_symbol_label_count": len(expected),
            "membership_digest": canonical_membership_digest(labels),
            "labels_are_programmatic_fixture_truth": True,
            "labels_are_production_results": False,
        },
        "overall": overall,
        "by_side": by_side,
        "by_role": by_role,
        "change_context": {
            "required_case_count": len(context_cases),
            "hit_count": context_hits,
            "recall": round(context_hits / len(context_cases), 12) if context_cases else None,
        },
        "false_affected_symbol": {
            "count": false_affected,
            "predicted_symbol_count": len(predicted),
            "rate": round(false_affected_rate, 12),
        },
        "ambiguity_noise_unavailable": {
            "expected_status_counts": dict(sorted(status_counts.items())),
            "observed_status_counts": dict(sorted(observed_status_counts.items())),
            "ambiguity_case_count": status_counts["ambiguous"],
            "noise_case_count": status_counts["noise"],
            "unavailable_case_count": status_counts["unavailable"],
            "rejected_case_count": status_counts["rejected"],
            "observed_ambiguity_rate": round(
                observed_status_counts["ambiguous"] / len(results), 12
            ),
            "observed_noise_rate": round(observed_status_counts["noise"] / len(results), 12),
            "observed_unavailable_rate": round(
                observed_status_counts["unavailable"] / len(results), 12
            ),
        },
        "performance": {
            "observation_count": len(results),
            "latency_ms": {
                "p50": round(_nearest_rank(durations, 50) / 1_000_000, 6),
                "p95": round(_nearest_rank(durations, 95) / 1_000_000, 6),
            },
            "memory_bytes": {
                "p50": _nearest_rank(memory, 50),
                "p95": _nearest_rank(memory, 95),
                "peak": max(memory),
            },
        },
    }


def calculate_test_fixture_metrics(
    labels: Sequence[CanonicalLabelRecord],
    arms: Mapping[str, Sequence[ArmCaseResult]],
) -> dict[str, Any]:
    """Calculate all three arms while explicitly retaining non-production provenance."""

    denominator = require_same_denominator(labels, arms)
    return {
        "schema_version": CB6_METRIC_VERSION,
        "measurement_source": "unit-test-fixture-values",
        "production_result": False,
        "quality_claim": "forbidden",
        "denominator": denominator,
        "arms": {arm: calculate_arm_metrics(labels, arms[arm], arm=arm) for arm in TREATMENT_ORDER},
    }


_PRODUCTION_INTERFACE_NAMES = (
    "DiffHunkVersion",
    "SymbolVersionRef",
    "DiffSymbolTreatment",
    "DiffSymbolEvaluation",
)


@dataclass(frozen=True, slots=True)
class _FrozenProductionBinding:
    module_object: Any
    public_module_object: Any
    component_type: type[Any]
    method_function: Any
    interface_types: tuple[tuple[str, type[Any]], ...]
    version: str
    implementation_digest: str
    identity: str


def _code_identity(value: Any) -> Any:
    if inspect.iscode(value):
        return portable_code_payload(value)
    return portable_constant_payload(value)


def _method_implementation_digest(method: Any) -> str | None:
    if not inspect.isfunction(method):
        return None
    try:
        payload = portable_function_payload(method)
    except CodeIdentityError:
        return None
    return _fingerprint(
        {
            "module": method.__module__,
            "qualname": method.__qualname__,
            "implementation": payload,
        }
    )


def _capture_production_binding() -> tuple[_FrozenProductionBinding | None, str | None]:
    """Capture the trusted public objects once while this module is imported."""

    try:
        module = importlib.import_module(DIFF_SYMBOL_MODULE)
        public = importlib.import_module(PUBLIC_CODE_MODULE)
        component = getattr(module, REAL_DIFF_SYMBOL_CLASS)
        public_component = getattr(public, REAL_DIFF_SYMBOL_CLASS)
        method = inspect.getattr_static(component, REAL_DIFF_SYMBOL_METHOD)
        version = module.DIFF_SYMBOL_MAPPER_VERSION
        interface_types = tuple(
            (name, getattr(module, name)) for name in _PRODUCTION_INTERFACE_NAMES
        )
    except Exception as exc:
        return None, f"production identity capture failed: {type(exc).__name__}"
    implementation_digest = _method_implementation_digest(method)
    if (
        not isinstance(component, type)
        or component.__module__ != DIFF_SYMBOL_MODULE
        or component.__name__ != REAL_DIFF_SYMBOL_CLASS
        or public_component is not component
        or not inspect.isfunction(method)
        or method.__module__ != DIFF_SYMBOL_MODULE
        or method.__qualname__ != f"{REAL_DIFF_SYMBOL_CLASS}.{REAL_DIFF_SYMBOL_METHOD}"
        or version != REAL_DIFF_SYMBOL_VERSION
        or getattr(component, "mapper_version", None) != REAL_DIFF_SYMBOL_VERSION
        or any(not isinstance(interface, type) for _, interface in interface_types)
        or implementation_digest is None
        or implementation_digest != CB6_RUNTIME_METHOD_AUTHORITY_SHA256
    ):
        return None, "production identity capture did not match the frozen public contract"
    return (
        _FrozenProductionBinding(
            module_object=module,
            public_module_object=public,
            component_type=component,
            method_function=method,
            interface_types=interface_types,
            version=version,
            implementation_digest=implementation_digest,
            identity=PRODUCTION_COMPONENT_IDENTITY,
        ),
        None,
    )


_FROZEN_PRODUCTION_BINDING, _PRODUCTION_BINDING_CAPTURE_ERROR = _capture_production_binding()


def _production_observation() -> dict[str, Any]:
    binding = _FROZEN_PRODUCTION_BINDING
    if binding is None:
        return {
            "capture_ready": False,
            "capture_error": _PRODUCTION_BINDING_CAPTURE_ERROR,
        }
    try:
        module = importlib.import_module(DIFF_SYMBOL_MODULE)
        public = importlib.import_module(PUBLIC_CODE_MODULE)
        component = getattr(module, REAL_DIFF_SYMBOL_CLASS)
        public_component = getattr(public, REAL_DIFF_SYMBOL_CLASS)
        method = inspect.getattr_static(component, REAL_DIFF_SYMBOL_METHOD)
        version = module.DIFF_SYMBOL_MAPPER_VERSION
        mapper_version = getattr(component, "mapper_version", None)
        interfaces = {
            name: getattr(module, name, None) is expected
            for name, expected in binding.interface_types
        }
    except Exception as exc:
        return {
            "capture_ready": True,
            "observation_ready": False,
            "observation_error": f"production identity observation failed: {type(exc).__name__}",
        }
    implementation_digest = _method_implementation_digest(method)
    checks = {
        "module_object_exact": module is binding.module_object,
        "public_module_object_exact": public is binding.public_module_object,
        "class_object_exact": component is binding.component_type,
        "public_export_exact": public_component is binding.component_type,
        "method_function_exact": method is binding.method_function,
        "method_implementation_exact": (implementation_digest == binding.implementation_digest),
        "module_version_exact": version == binding.version == REAL_DIFF_SYMBOL_VERSION,
        "class_version_exact": mapper_version == binding.version,
        "implementation_module_exact": (
            getattr(component, "__module__", None) == DIFF_SYMBOL_MODULE
            and getattr(method, "__module__", None) == DIFF_SYMBOL_MODULE
        ),
        "implementation_name_exact": (
            getattr(component, "__name__", None) == REAL_DIFF_SYMBOL_CLASS
            and getattr(method, "__qualname__", None)
            == f"{REAL_DIFF_SYMBOL_CLASS}.{REAL_DIFF_SYMBOL_METHOD}"
        ),
        "interfaces_exact": all(interfaces.values()),
    }
    return {
        "capture_ready": True,
        "observation_ready": True,
        "component": component,
        "method": method,
        "version": version,
        "implementation_digest": implementation_digest,
        "interfaces": interfaces,
        "checks": checks,
        "all_exact": all(checks.values()),
    }


def _production_module() -> Any | None:
    binding = _FROZEN_PRODUCTION_BINDING
    if binding is None:
        return None
    try:
        module = importlib.import_module(DIFF_SYMBOL_MODULE)
    except Exception:
        return None
    return module if module is binding.module_object else None


def _production_component_type() -> type[Any] | None:
    binding = _FROZEN_PRODUCTION_BINDING
    observation = _production_observation()
    if binding is None or observation.get("all_exact") is not True:
        return None
    return binding.component_type


def production_component_contract() -> dict[str, Any]:
    """Require the frozen module/class/original-method/full-version binding."""

    binding = _FROZEN_PRODUCTION_BINDING
    observation = _production_observation()
    base = {
        "module": DIFF_SYMBOL_MODULE,
        "class": REAL_DIFF_SYMBOL_CLASS,
        "method": REAL_DIFF_SYMBOL_METHOD,
        "version": REAL_DIFF_SYMBOL_VERSION,
        "identity": PRODUCTION_COMPONENT_IDENTITY,
        "identity_policy": "frozen-module-class-method-version-exact",
        "import_mode": "captured-at-cb6-import",
    }
    if binding is None:
        return {
            "status": "pending",
            **base,
            "reason": _PRODUCTION_BINDING_CAPTURE_ERROR
            or "production diff_symbol_v2 identity capture is unavailable",
        }
    checks = dict(observation.get("checks") or {})
    interfaces = dict(observation.get("interfaces") or {})
    if observation.get("all_exact") is not True:
        return {
            "status": "pending",
            **base,
            "implementation_module": getattr(observation.get("component"), "__module__", None),
            "method_implementation_sha256": CB6_RELEASED_METHOD_IMPLEMENTATION_SHA256,
            "identity_checks": checks,
            "interfaces": interfaces,
            "reason": "production diff_symbol_v2 frozen object binding changed",
        }
    return {
        "status": "ready",
        **base,
        "implementation_module": binding.component_type.__module__,
        "method_implementation_sha256": CB6_RELEASED_METHOD_IMPLEMENTATION_SHA256,
        "identity_checks": checks,
        "interfaces": interfaces,
    }


def require_production_component_identity(component: Any) -> dict[str, Any]:
    """Reject every object except an instance bound to the frozen original method."""

    binding = _FROZEN_PRODUCTION_BINDING
    if (
        binding is None
        or isinstance(component, type)
        or type(component) is not binding.component_type
    ):
        raise CB6Error(
            "fake, injected, wrapped, monkeypatched, or subclassed Diff-to-Symbol "
            "component is forbidden"
        )
    contract = production_component_contract()
    try:
        bound_method = getattr(component, REAL_DIFF_SYMBOL_METHOD)
    except Exception as exc:
        raise CB6Error("production Diff-to-Symbol bound method is unavailable") from exc
    bound_method_exact = (
        inspect.ismethod(bound_method)
        and bound_method.__self__ is component
        and bound_method.__func__ is binding.method_function
        and _method_implementation_digest(bound_method.__func__) == binding.implementation_digest
    )
    if (
        contract.get("status") != "ready"
        or contract.get("module") != DIFF_SYMBOL_MODULE
        or contract.get("implementation_module") != DIFF_SYMBOL_MODULE
        or contract.get("class") != REAL_DIFF_SYMBOL_CLASS
        or contract.get("method") != REAL_DIFF_SYMBOL_METHOD
        or contract.get("version") != REAL_DIFF_SYMBOL_VERSION
        or contract.get("identity") != PRODUCTION_COMPONENT_IDENTITY
        or contract.get("identity_policy") != "frozen-module-class-method-version-exact"
        or contract.get("method_implementation_sha256")
        != CB6_RELEASED_METHOD_IMPLEMENTATION_SHA256
        or not all((contract.get("identity_checks") or {}).values())
        or not all((contract.get("interfaces") or {}).values())
        or not bound_method_exact
    ):
        raise CB6Error(
            "C6-02 Gate lacks the exact frozen production module/class/map_hunk@version identity"
        )
    return {**contract, "bound_method_exact": True}


_UNIFIED_DIFF_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))?"
    r" \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_PROJECT_ID = "project-code-cb6-v1"
_REPOSITORY_ID = "repository-cb6-programmatic-fixture"
_ACL_REF = "acl:cb6-evaluation"
_PARENT_GENERATION_ID = "generation-cb6-parent"
_TARGET_GENERATION_ID = "generation-cb6-target"


@dataclass(frozen=True, slots=True)
class _ProductionFixtureData:
    parent_units: tuple[Any, ...]
    target_units: tuple[Any, ...]
    parent_refs: tuple[Any, ...]
    target_refs: tuple[Any, ...]
    confirmed_lineages: tuple[Any, ...]
    lineage_snapshot: dict[str, Any]
    symbol_catalog: Mapping[tuple[str, str, int, int], str]


def _materialize_production_fixture(fixture: CB6Fixture) -> _ProductionFixtureData:
    history = importlib.import_module("evidence_rag.rag.sources.code.history_v2")
    production = _production_module()
    if production is None:
        raise CB6Error("frozen production diff_symbol_v2 module is unavailable")
    parent_paths = (
        "src/calculator.py",
        "src/legacy.py",
        "src/removed.py",
        "src/spacing.py",
        "src/ambiguous.py",
    )
    target_paths = (
        "src/calculator.py",
        "src/modern.py",
        "src/added.py",
        "src/spacing.py",
        "src/ambiguous.py",
    )
    materializer = history.HistoricalMaterializer(
        history.InMemoryHistoricalPublicationStore(),
        clock=lambda: "2001-01-03T00:00:00+00:00",
    )
    parent_request = history.HistoryMaterializationRequest(
        project_id=_PROJECT_ID,
        repository_id=_REPOSITORY_ID,
        repository_root=fixture.repository,
        ref=fixture.commits["base"],
        commit_sha=fixture.commits["base"],
        generation_id=_PARENT_GENERATION_ID,
        acl_ref=_ACL_REF,
        paths=parent_paths,
    )
    target_request = history.HistoryMaterializationRequest(
        project_id=_PROJECT_ID,
        repository_id=_REPOSITORY_ID,
        repository_root=fixture.repository,
        ref=fixture.commits["target"],
        commit_sha=fixture.commits["target"],
        generation_id=_TARGET_GENERATION_ID,
        acl_ref=_ACL_REF,
        paths=target_paths,
    )
    parent = materializer.materialize(parent_request)
    target = materializer.materialize(target_request)
    parent_refs = tuple(
        production.SymbolVersionRef.from_historical_unit(unit)
        for unit in parent.units
        if unit.unit_type in {"symbol.ast_block", "file.surface"}
        and (unit.unit_type != "symbol.ast_block" or unit.is_symbol)
    )
    target_refs = tuple(
        production.SymbolVersionRef.from_historical_unit(unit)
        for unit in target.units
        if unit.unit_type in {"symbol.ast_block", "file.surface"}
        and (unit.unit_type != "symbol.ast_block" or unit.is_symbol)
    )
    rename_hints = materializer.git_rename_hints(
        request=parent_request,
        source_commit_sha=fixture.commits["base"],
        target_commit_sha=fixture.commits["target"],
        source_generation_id=_PARENT_GENERATION_ID,
        target_generation_id=_TARGET_GENERATION_ID,
    )
    lineage_request = history.SymbolLineageRequest(
        project_id=_PROJECT_ID,
        repository_id=_REPOSITORY_ID,
        source_commit_sha=fixture.commits["base"],
        target_commit_sha=fixture.commits["target"],
        source_generation_id=_PARENT_GENERATION_ID,
        target_generation_id=_TARGET_GENERATION_ID,
        acl_ref=_ACL_REF,
    )
    lineage = history.SymbolLineageResolver().resolve(
        lineage_request,
        parent.units,
        target.units,
        rename_hints=rename_hints,
    )
    if not lineage.lineages:
        raise CB6Error("production fixture produced no confirmed symbol lineages")
    catalog: dict[tuple[str, str, int, int], str] = {}
    for commit, paths in (
        (fixture.commits["base"], parent_paths),
        (fixture.commits["target"], target_paths),
    ):
        for path in paths:
            blob = _git_blob(fixture.repository, commit, path)
            if blob is None:
                continue
            try:
                source = blob.decode("utf-8")
            except UnicodeDecodeError:
                continue
            tree = ast.parse(source)

            def visit(
                body: Sequence[ast.stmt],
                prefix: tuple[str, ...] = (),
                *,
                observed_commit: str = commit,
                observed_path: str = path,
            ) -> None:
                for node in body:
                    if isinstance(
                        node,
                        (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                    ):
                        current = (*prefix, node.name)
                        end = getattr(node, "end_lineno", None)
                        if isinstance(end, int):
                            catalog[(observed_commit, observed_path, node.lineno, end)] = ".".join(
                                current
                            )
                        visit(
                            node.body,
                            current,
                            observed_commit=observed_commit,
                            observed_path=observed_path,
                        )

            visit(tree.body)
    return _ProductionFixtureData(
        parent_units=tuple(parent.units),
        target_units=tuple(target.units),
        parent_refs=parent_refs,
        target_refs=target_refs,
        confirmed_lineages=tuple(lineage.lineages),
        lineage_snapshot={
            "status": lineage.status.value,
            "confirmed_count": len(lineage.lineages),
            "candidate_count": len(lineage.candidates),
            "diagnostic_count": len(lineage.diagnostics),
            "confirmed_lineage_ids": [item.lineage_id for item in lineage.lineages],
            "rename_hint_count": len(rename_hints),
            "source_symbol_count": lineage.trace.source_symbols,
            "target_symbol_count": lineage.trace.target_symbols,
            "comparison_count": lineage.trace.comparisons,
        },
        symbol_catalog=catalog,
    )


def _range_intersects(start: int, count: int, selected: LineRange | None) -> bool:
    if selected is None:
        return False
    if count == 0:
        return selected.count == 0 or selected.start <= start <= selected.end
    if selected.count == 0:
        return start <= selected.start <= start + count - 1
    return not (start + count - 1 < selected.start or selected.end < start)


def _unified_patch(
    old_source: str,
    new_source: str,
    record: CanonicalLabelRecord,
) -> str:
    lines = list(
        difflib.unified_diff(
            old_source.splitlines(),
            new_source.splitlines(),
            fromfile=record.old_path or "/dev/null",
            tofile=record.new_path or "/dev/null",
            n=0,
            lineterm="",
        )
    )
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("@@"):
            if current:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current:
        blocks.append(current)
    selected: list[str] = []
    for block in blocks:
        match = _UNIFIED_DIFF_HEADER_RE.match(block[0])
        if match is None:
            continue
        old_start = int(match.group("old_start"))
        old_count = int(match.group("old_count") or "1")
        new_start = int(match.group("new_start"))
        new_count = int(match.group("new_count") or "1")
        if _range_intersects(
            old_start,
            old_count,
            record.old_range,
        ) or _range_intersects(new_start, new_count, record.new_range):
            selected.extend(block)
    if not selected:
        raise CB6Error(f"canonical case {record.case_id} has no selected unified diff")
    return "\n".join(selected) + "\n"


def _case_sources(
    fixture: CB6Fixture,
    record: CanonicalLabelRecord,
) -> tuple[str, str]:
    old_blob = (
        _git_blob(fixture.repository, fixture.commits["base"], record.old_path)
        if record.old_path is not None
        else None
    )
    new_blob = (
        _git_blob(fixture.repository, fixture.commits["target"], record.new_path)
        if record.new_path is not None
        else None
    )
    try:
        return (
            old_blob.decode("utf-8") if old_blob is not None else "",
            new_blob.decode("utf-8") if new_blob is not None else "",
        )
    except UnicodeDecodeError as exc:
        raise CB6Error(f"case {record.case_id} unexpectedly requires binary text") from exc


def _production_hunk(
    fixture: CB6Fixture,
    record: CanonicalLabelRecord,
) -> Any:
    production = _production_module()
    if production is None:
        raise CB6Error("frozen production diff_symbol_v2 module is unavailable")
    if record.case_id == "cb6-binary":
        patch = "Binary files a/assets/blob.bin and b/assets/blob.bin differ\n"
    elif record.case_id == "cb6-wrong-parent":
        source = _fixture_sources()
        surrogate = replace(
            next(item for item in fixture.labels if item.case_id == "cb6-file-top-level"),
            old_commit_sha=record.old_commit_sha,
        )
        patch = _unified_patch(
            str(source["base_calculator"]),
            str(source["target_calculator"]),
            surrogate,
        )
    else:
        old_source, new_source = _case_sources(fixture, record)
        patch = _unified_patch(old_source, new_source, record)
    if record.status == "rejected":
        old_range = LineRange(1, 1)
        new_range = LineRange(1, 1)
    else:
        old_range = record.old_range or LineRange(0, 0)
        new_range = record.new_range or LineRange(0, 0)
    if record.case_id == "cb6-rename":
        change_type = production.DiffChangeType.RENAME
        path = record.new_path
        old_path = record.old_path
    elif record.case_id == "cb6-add":
        change_type = production.DiffChangeType.ADD
        path = record.new_path
        old_path = None
    elif record.case_id == "cb6-delete":
        change_type = production.DiffChangeType.DELETE
        path = record.old_path
        old_path = None
    else:
        change_type = production.DiffChangeType.MODIFY
        path = record.new_path or record.old_path
        old_path = None
    if path is None:
        raise CB6Error(f"case {record.case_id} lacks a production hunk path")
    return production.DiffHunkVersion.create(
        project_id=_PROJECT_ID,
        repository_id=_REPOSITORY_ID,
        parent_commit_sha=record.old_commit_sha,
        target_commit_sha=record.new_commit_sha,
        parent_generation_id=_PARENT_GENERATION_ID,
        target_generation_id=_TARGET_GENERATION_ID,
        acl_ref=_ACL_REF,
        path=path,
        old_path=old_path,
        change_type=change_type,
        old_start=old_range.start,
        old_count=old_range.count,
        new_start=new_range.start,
        new_count=new_range.count,
        patch=patch,
        is_binary=record.case_id == "cb6-binary",
        source="programmatic-git-diff",
        parser_version="unified-diff-v1",
    )


def _line_only_refs(refs: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(
        replace(
            ref,
            unit_type=("legacy.line-symbol" if ref.is_symbol else ref.unit_type),
            ast_node_type=("legacy_line" if ref.is_symbol else ref.ast_node_type),
        )
        for ref in refs
    )


def _prediction_from_match(
    match: Any,
    data: _ProductionFixtureData,
) -> SymbolLabel:
    symbol = match.symbol
    if not symbol.is_symbol:
        qualified_name = "<module>"
    else:
        qualified_name = data.symbol_catalog.get(
            (
                symbol.commit_sha,
                symbol.path,
                symbol.start_line,
                symbol.end_line,
            ),
            symbol.qualified_name,
        )
    return SymbolLabel(
        symbol_id=f"python:{symbol.path}:{qualified_name}",
        side=match.side.value,
        role=match.change_role,
        path=symbol.path,
        qualified_name=qualified_name,
        symbol_range=LineRange(
            start=symbol.start_line,
            count=symbol.end_line - symbol.start_line + 1,
        ),
        lineage_id=f"observed:{symbol.unit_id}",
    )


def _measure_mapper_call(
    mapper: Any,
    hunk: Any,
    *,
    parent_symbols: Sequence[Any],
    target_symbols: Sequence[Any],
    confirmed_lineages: Sequence[Any],
) -> tuple[Any, int, int]:
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    tracemalloc.reset_peak()
    started = time.perf_counter_ns()
    try:
        treatment = mapper.map_hunk(
            hunk,
            parent_symbols=parent_symbols,
            target_symbols=target_symbols,
            confirmed_lineages=confirmed_lineages,
        )
    finally:
        duration = time.perf_counter_ns() - started
        _, peak = tracemalloc.get_traced_memory()
        if not already_tracing:
            tracemalloc.stop()
    return treatment, duration, peak


def _production_result_status(treatment: Any) -> ResultStatus:
    status = treatment.status.value
    if status == "skipped":
        return "noise"
    if status == "unavailable":
        return "unavailable"
    if any(match.status.value == "candidate" for match in treatment.matches):
        return "ambiguous"
    return "available"


def _execute_production_treatments(
    fixture: CB6Fixture,
) -> tuple[
    dict[str, tuple[ArmCaseResult, ...]],
    dict[str, list[dict[str, Any]]],
    dict[str, Any],
]:
    validate_programmatic_fixture(fixture)
    data = _materialize_production_fixture(fixture)
    binding = _FROZEN_PRODUCTION_BINDING
    if binding is None:
        raise CB6Error("frozen production DiffSymbolMapper is unavailable")
    mapper = binding.component_type()
    component = require_production_component_identity(mapper)
    production = _production_module()
    if production is None:
        raise CB6Error("frozen production diff_symbol_v2 module is unavailable")
    parent_line_refs = _line_only_refs(data.parent_refs)
    target_line_refs = _line_only_refs(data.target_refs)
    arms: dict[str, tuple[ArmCaseResult, ...]] = {}
    raw_arms: dict[str, list[dict[str, Any]]] = {}
    label_map = {record.case_id: record for record in fixture.labels}
    for arm in TREATMENT_ORDER:
        case_results: list[ArmCaseResult] = []
        raw_results: list[dict[str, Any]] = []
        for record in fixture.labels:
            if record.case_id == "cb6-wrong-parent":
                started = time.perf_counter_ns()
                if record.old_commit_sha == record.target_parent_sha:
                    raise CB6Error("wrong-parent case unexpectedly passed parent preflight")
                duration = time.perf_counter_ns() - started
                result = ArmCaseResult(
                    case_id=record.case_id,
                    old_commit_sha=record.old_commit_sha,
                    new_commit_sha=record.new_commit_sha,
                    status="rejected",
                    symbols=(),
                    change_context_hit=None,
                    duration_ns=duration,
                    peak_memory_bytes=0,
                )
                raw_results.append(
                    {
                        "case_id": record.case_id,
                        "status": "rejected",
                        "reason": "supplied parent is not the target commit parent",
                        "production_mapper_called": False,
                        "old_commit_sha": record.old_commit_sha,
                        "target_parent_sha": record.target_parent_sha,
                        "new_commit_sha": record.new_commit_sha,
                        "result": result.to_dict(),
                    }
                )
                case_results.append(result)
                continue
            hunk = _production_hunk(fixture, record)
            if arm == "line_overlap_legacy":
                parent_symbols = parent_line_refs
                target_symbols = target_line_refs
                lineages: tuple[Any, ...] = ()
            else:
                parent_symbols = data.parent_units
                target_symbols = data.target_units
                lineages = data.confirmed_lineages if arm == "ast_rename_lineage_merged" else ()
            treatment, duration, peak = _measure_mapper_call(
                mapper,
                hunk,
                parent_symbols=parent_symbols,
                target_symbols=target_symbols,
                confirmed_lineages=lineages,
            )
            predictions = tuple(
                sorted(
                    (
                        _prediction_from_match(match, data)
                        for match in treatment.matches
                        if match.status.value == "confirmed"
                    ),
                    key=lambda item: (item.side, item.symbol_id, item.role),
                )
            )
            expected_keys = {
                _symbol_key(record.case_id, symbol) for symbol in record.expected_symbols
            }
            predicted_keys = {_symbol_key(record.case_id, symbol) for symbol in predictions}
            result = ArmCaseResult(
                case_id=record.case_id,
                old_commit_sha=record.old_commit_sha,
                new_commit_sha=record.new_commit_sha,
                status=_production_result_status(treatment),
                symbols=predictions,
                change_context_hit=(
                    expected_keys.issubset(predicted_keys)
                    if record.change_context_required
                    else None
                ),
                duration_ns=duration,
                peak_memory_bytes=peak,
            )
            raw_results.append(
                {
                    "case_id": record.case_id,
                    "production_mapper_called": True,
                    "hunk_id": hunk.hunk_id,
                    "treatment": json.loads(treatment.canonical_json_bytes()),
                    "result": result.to_dict(),
                }
            )
            case_results.append(result)
        ordered = tuple(sorted(case_results, key=lambda item: item.case_id))
        if tuple(item.case_id for item in ordered) != tuple(sorted(label_map)):
            raise CB6Error(f"production arm {arm} lost canonical case membership")
        arms[arm] = ordered
        raw_arms[arm] = sorted(raw_results, key=lambda item: item["case_id"])
    denominator = require_same_denominator(fixture.labels, arms)
    return (
        arms,
        raw_arms,
        {
            "production_component": component,
            "lineage": data.lineage_snapshot,
            "denominator": denominator,
        },
    )


def _production_metrics(
    fixture: CB6Fixture,
    arms: Mapping[str, Sequence[ArmCaseResult]],
) -> dict[str, Any]:
    denominator = require_same_denominator(fixture.labels, arms)
    return {
        "schema_version": CB6_METRIC_VERSION,
        "measurement_source": "production-cb6-run",
        "production_result": True,
        "scope": "independent-diff-binding-quality-audit",
        "replaces_cb0": False,
        "denominator": denominator,
        "arms": {
            arm: calculate_arm_metrics(
                fixture.labels,
                arms[arm],
                arm=arm,
                measurement_source="production-cb6-run",
                _production_token=_PRODUCTION_METRIC_TOKEN,
            )
            for arm in TREATMENT_ORDER
        },
    }


def _quality_decision(metrics: Mapping[str, Any]) -> dict[str, Any]:
    treatment = metrics["arms"]["ast_rename_lineage_merged"]
    overall = treatment["overall"]
    context = treatment["change_context"]
    false_affected = treatment["false_affected_symbol"]
    labeled_cases = treatment["label_status"]["labeled_case_count"]
    checks = {
        "sample_size": labeled_cases >= QUALIFICATION_CONTRACT["minimum_labeled_cases"],
        "precision": (
            overall["precision"] is not None
            and overall["precision"] >= QUALIFICATION_CONTRACT["minimum_precision"]
        ),
        "recall": (
            overall["recall"] is not None
            and overall["recall"] >= QUALIFICATION_CONTRACT["minimum_recall"]
        ),
        "f1": (overall["f1"] is not None and overall["f1"] >= QUALIFICATION_CONTRACT["minimum_f1"]),
        "change_context_recall": (
            context["recall"] is not None
            and context["recall"] >= QUALIFICATION_CONTRACT["minimum_change_context_recall"]
        ),
        "false_affected_rate": (
            false_affected["rate"] <= QUALIFICATION_CONTRACT["maximum_false_affected_rate"]
        ),
    }
    qualified = all(checks.values())
    if qualified:
        status = "QUALIFIED"
        reason = "all frozen C-B6 quality and sample-size gates passed"
    elif not checks["sample_size"]:
        status = QUALIFICATION_CONTRACT["small_sample_status"]
        reason = "the fixed 8-case labeled fixture is below the 30-case qualification floor"
    else:
        status = "NOT QUALIFIED"
        reason = "one or more frozen C-B6 quality gates did not pass"
    return {
        "status": status,
        "treatment_qualified": qualified,
        "treatment_arm": "ast_rename_lineage_merged",
        "contract": QUALIFICATION_CONTRACT,
        "checks": checks,
        "reason": reason,
        "engineering_pass_is_quality_qualification": False,
    }


def _normalize_gate_line(value: str) -> str:
    without_markdown = value.replace("`", " ").replace("*", " ").strip()
    return " ".join(without_markdown.split()).casefold()


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
        identity_values: list[str] = []
        for index in range(start + 1, min(len(lines), start + 30)):
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
            finding = _GATE_FINDING_RE.fullmatch(normalized)
            if finding is not None:
                target = p0_values if finding.group(1) == "0" else p1_values
                target.append(finding.group(2))
                continue
            combined = _GATE_COMBINED_FINDING_RE.fullmatch(normalized)
            if combined is not None:
                p0_values.append(combined.group(1))
                p1_values.append(combined.group(1))
                continue
            execution = _GATE_EXECUTION_RE.fullmatch(normalized)
            if execution is not None:
                execution_values.append(" ".join(execution.group(1).split()).casefold())
                continue
            identity = _GATE_IDENTITY_RE.fullmatch(normalized)
            if identity is not None:
                identity_values.append(identity.group(1))
        blocks.append(
            {
                "index": len(blocks),
                "line": start + 1,
                "timestamp": _gate_block_timestamp(lines, start),
                "engineering": engineering.group(1).casefold(),
                "p0_values": p0_values,
                "p1_values": p1_values,
                "execution_values": execution_values,
                "identity_values": identity_values,
            }
        )
    return tuple(blocks)


def parse_c6_02_authorization(text: str) -> dict[str, Any]:
    """Select the final exact C6-02 production authorization block."""

    blocks = _gate_decision_blocks(text)
    if not blocks:
        return {
            "status": "not-authorized",
            "engineering_status": "missing",
            "production_execution_authorized": False,
            "decision_block_count": 0,
            "reason": "final C6-02 engineering PASS decision block is missing",
        }
    timestamped = tuple(block for block in blocks if block["timestamp"] is not None)
    final = (
        max(timestamped, key=lambda block: (block["timestamp"], block["index"]))
        if timestamped
        else blocks[-1]
    )
    exact = (
        final["engineering"] == "pass"
        and final["p0_values"] == ["0"]
        and final["p1_values"] == ["0"]
        and final["execution_values"] == ["authorized"]
        and final["identity_values"] == [PRODUCTION_COMPONENT_IDENTITY.casefold()]
    )
    evidence = {
        "engineering": final["engineering"],
        "p0_values": list(final["p0_values"]),
        "p1_values": list(final["p1_values"]),
        "execution_values": list(final["execution_values"]),
        "identity_values": list(final["identity_values"]),
    }
    if exact:
        signed = {
            "engineering_decision": C6_02_ENGINEERING_DECISION,
            "p0_findings": 0,
            "p1_findings": 0,
            "execution_decision": C6_02_EXECUTION_DECISION,
            "production_component_identity": PRODUCTION_COMPONENT_IDENTITY,
        }
        return {
            "status": "authorized",
            "engineering_status": "passed",
            "production_execution_authorized": True,
            "decision_block_count": len(blocks),
            "selected_block_index": final["index"],
            "selected_block_line": final["line"],
            "selected_block_timestamp": final["timestamp"],
            "decision_record": signed,
            "decision_block_hash": _fingerprint(signed),
        }
    if final["engineering"] != "pass":
        reason = "final C6-02 engineering decision is not PASS"
    elif final["p0_values"] != ["0"] or final["p1_values"] != ["0"]:
        reason = "final C6-02 block does not contain exactly P0=0 and P1=0"
    elif final["execution_values"] != ["authorized"]:
        reason = "final C-B6 production execution decision is not exact AUTHORIZED"
    elif final["identity_values"] != [PRODUCTION_COMPONENT_IDENTITY.casefold()]:
        reason = "final C-B6 production component identity is missing or not exact"
    else:
        reason = "final C6-02 authorization block is ambiguous"
    return {
        "status": "not-authorized",
        "engineering_status": ("failed" if final["engineering"] == "fail" else "incomplete"),
        "production_execution_authorized": False,
        "decision_block_count": len(blocks),
        "selected_block_index": final["index"],
        "selected_block_line": final["line"],
        "selected_block_timestamp": final["timestamp"],
        "reason": reason,
        **evidence,
    }


def _gate_status(root: Path) -> dict[str, Any]:
    path = (root / C6_GATE_RELATIVE_PATH).resolve()
    expected = root.resolve() / C6_GATE_RELATIVE_PATH
    if path != expected or not path.is_file() or path.is_symlink():
        return {
            "status": "not-authorized",
            "engineering_status": "missing",
            "production_execution_authorized": False,
            "report": C6_GATE_RELATIVE_PATH,
            "reason": "C6 Gate report is missing, redirected, or symlinked",
        }
    text = path.read_text(encoding="utf-8")
    parsed = parse_c6_02_authorization(text)
    development_authorized = C6_02_DEVELOPMENT_DECISION in text
    result = {
        **parsed,
        "report": C6_GATE_RELATIVE_PATH,
        "report_sha256": _sha256_file(path),
        "development_authorization_observed": development_authorized,
        "development_authorization_is_production_authorization": False,
    }
    if parsed["status"] != "authorized":
        return result
    component = production_component_contract()
    if component.get("status") != "ready":
        return {
            **result,
            "status": "not-authorized",
            "production_execution_authorized": False,
            "production_component": component,
            "reason": "exact production diff_symbol_v2 component is not ready",
        }
    return {**result, "production_component": component}


def _default_repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _path_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "mode": stat.st_mode,
    }


def _tree_state(path: Path) -> dict[str, tuple[int, int, int]]:
    if not path.exists():
        return {}
    return {
        item.relative_to(path).as_posix(): (
            item.stat().st_size,
            item.stat().st_mtime_ns,
            item.stat().st_mode,
        )
        for item in path.rglob("*")
        if item.is_file()
    }


def preparation_status(*, root: Path | None = None) -> dict[str, Any]:
    """Return read-only readiness, or report the already-published one-shot Run."""

    resolved_root = (root or _default_repository_root()).resolve()
    runs_dir = resolved_root / "evals/code/runs"
    runs_before = _tree_state(runs_dir)

    gate = _gate_status(resolved_root)
    component = production_component_contract()
    existing_runs = _existing_cb6_run_directories(runs_dir)
    if len(existing_runs) > 1:
        raise CB6Error("more than one C-B6 production Run exists")
    existing_verification = verify_artifact(existing_runs[0]) if existing_runs else None
    ready = (
        gate.get("status") == "authorized"
        and component.get("status") == "ready"
        and not existing_runs
    )

    runs_after = _tree_state(runs_dir)
    if runs_after != runs_before:
        raise CB6Error("existing Run tree changed during C-B6 preparation")

    return {
        "schema_version": CB6_PREPARATION_VERSION,
        "status": (
            "PREPARED NON-QUALIFIED"
            if not existing_runs
            else "PRODUCTION RUN PRESENT NON-QUALIFIED"
        ),
        "preparation_status": "PREPARED" if not existing_runs else "COMPLETED",
        "qualification_status": (
            "NON-QUALIFIED"
            if existing_verification is None
            else existing_verification["qualification_status"]
        ),
        "execution_status": (
            "production-run-already-exists"
            if existing_runs
            else (
                "authorized-ready-for-single-production-run"
                if ready
                else "production-execution-not-authorized"
            )
        ),
        "treatment_qualified": (
            False if existing_verification is None else existing_verification["treatment_qualified"]
        ),
        "run_created": bool(existing_runs),
        "artifact_created": bool(existing_runs),
        "metrics_created": bool(existing_runs),
        "fixture_created": False,
        "controls": {
            "qualified_global_retrieval_baseline": {
                "name": "C-B0",
                "run_id": CB0_QUALIFIED_RUN_ID,
                "treatment_qualified": True,
                "scope": "global-retrieval-anchor-only",
                "replaced_by_cb6": False,
            },
            "audit_controls": {
                name: {
                    "run_id": run_id,
                    "treatment_qualified": False,
                    "qualification_use": "forbidden",
                }
                for name, run_id in AUDIT_CONTROL_RUN_IDS.items()
            },
            "cb6_scope": {
                "kind": "independent-diff-binding-quality-audit",
                "replaces_cb0": False,
            },
        },
        "fixture_contract": {
            "schema_version": CB6_FIXTURE_VERSION,
            "construction": "programmatic-temporary-git-only",
            "network_required": False,
            "developer_home_required": False,
            "case_count": len(FIXTURE_CASE_IDS),
            "case_ids": list(sorted(FIXTURE_CASE_IDS)),
            "canonical_labels_created_during_prepare": False,
            "coverage": [
                "python-function-body",
                "python-function-signature",
                "python-class-level",
                "python-file-top-level",
                "rename",
                "add",
                "delete",
                "multi-symbol",
                "whitespace-only",
                "binary",
                "wrong-parent",
                "ambiguous",
            ],
        },
        "treatment_contract": {
            "order": list(TREATMENT_ORDER),
            "arms": TREATMENT_MATRIX,
            "same_eligible_denominator_required": True,
            "same_parent_target_membership_required": True,
            "production_identity_required_for_ast_arms": True,
            "fake_injected_wrapped_or_subclassed_forbidden": True,
        },
        "metric_contract": {
            name: {
                **contract,
                "status": (
                    "production-observed" if existing_runs else "pending-production-execution"
                ),
            }
            for name, contract in METRIC_CONTRACT.items()
        },
        "label_contract": {
            "status": (
                "canonical-labels-published"
                if existing_runs
                else "pending-programmatic-fixture-construction"
            ),
            "quality_values_created": bool(existing_runs),
            "canonical_records_required": True,
            "label_count_must_be_derived": True,
            "zero_labels_cannot_claim_quality": True,
            "tamper_evident_membership_digest_required": True,
        },
        "production_component": component,
        "execution_gate": gate,
        "execution_policy": {
            "prepared_only_now": not existing_runs,
            "production_execution_authorized": (
                gate.get("status") == "authorized" and component.get("status") == "ready"
            ),
            "requires_c6_02_engineering_pass": True,
            "requires_p0_p1_zero": True,
            "requires_literal_cb6_execution_authorization": True,
            "requires_exact_component_identity": True,
            "requires_one_fresh_run": True,
            "requires_temporary_isolated_sqlite": True,
            "formal_database_write_forbidden": True,
            "old_run_overwrite_forbidden": True,
            "canonical_portable_secret_free_absolute_path_free": True,
            "verification_is_read_only": True,
        },
        "repository_guards": {
            "formal_database": {
                "ownership": "EXTERNAL_MUTABLE_SERVICE_OWNED",
                "observed": False,
            },
            "formal_database_sidecars": {
                "ownership": "EXTERNAL_MUTABLE_SERVICE_OWNED",
                "observed": False,
            },
            "existing_run_file_count": len(runs_after),
            "existing_run_tree_unchanged": True,
            "cb6_production_run_count": len(existing_runs),
        },
        "non_execution_reasons": [
            reason
            for reason in (
                gate.get("reason") if gate.get("status") != "authorized" else None,
                (component.get("reason") if component.get("status") != "ready" else None),
                ("the one-shot C-B6 production Run already exists" if existing_runs else None),
            )
            if reason
        ],
        "existing_run_verification": existing_verification,
    }


def prepare_cb6(*, root: Path | None = None) -> dict[str, Any]:
    """Public PREPARED-only orchestration spelling."""

    return preparation_status(root=root)


def _portable_string(value: str) -> bool:
    if "\x00" in value or _SECRET_RE.search(value):
        return False
    return not (
        _POSIX_ABSOLUTE_RE.search(value)
        or value.startswith("file://")
        or _WINDOWS_ABSOLUTE_RE.match(value)
    )


def validate_portable_payload(value: Any) -> None:
    """Reject absolute paths, Path objects, non-finite JSON, and secret-like material."""

    if isinstance(value, Path):
        raise CB6Error("portable C-B6 payloads cannot contain Path objects")
    if isinstance(value, str):
        if not _portable_string(value):
            raise CB6Error("portable C-B6 payload contains an absolute path or secret")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CB6Error("portable C-B6 payload contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not _portable_string(key)
                or _SECRET_KEY_RE.fullmatch(key)
            ):
                raise CB6Error("portable C-B6 payload contains an invalid key")
            validate_portable_payload(item)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            validate_portable_payload(item)
        return
    raise CB6Error(f"portable C-B6 payload contains unsupported type: {type(value).__name__}")


ARTIFACT_FILES = (
    "labels.json",
    "results.json",
    "metrics.json",
    "performance.json",
    "security.json",
    "evaluation.sqlite3",
)
EXPECTED_ARTIFACT_ENTRIES = frozenset(("manifest.json", *ARTIFACT_FILES))


def _write_canonical_json(path: Path, payload: Mapping[str, Any]) -> None:
    validate_portable_payload(payload)
    path.write_text(_canonical_json_text(payload), encoding="utf-8")


def _integrity_file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    if path.is_symlink() or not path.is_file():
        raise CB6Error(f"integrity guard requires a regular file: {path.name}")
    stat = path.stat()
    return {
        "exists": True,
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "mode": stat.st_mode,
        "sha256": _sha256_file(path),
    }


def _integrity_tree_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_dir():
        raise CB6Error("canonical Run root must be a non-symlink directory")
    state: dict[str, dict[str, Any]] = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise CB6Error("existing Run tree contains a symlink")
        if item.is_file():
            stat = item.stat()
            state[item.relative_to(path).as_posix()] = {
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "mode": stat.st_mode,
                "sha256": _sha256_file(item),
            }
    return state


def _existing_cb6_run_directories(runs_dir: Path) -> tuple[Path, ...]:
    if not runs_dir.exists():
        return ()
    found: list[Path] = []
    for candidate in sorted(runs_dir.iterdir()):
        manifest = candidate / "manifest.json"
        if candidate.is_symlink() or not candidate.is_dir() or not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("schema_version") == CB6_SCHEMA_VERSION:
            found.append(candidate.resolve())
    return tuple(found)


def _create_portable_database(
    path: Path,
    *,
    run_id: str,
    component: Mapping[str, Any],
    fixture: CB6Fixture,
    arms: Mapping[str, Sequence[ArmCaseResult]],
    metrics: Mapping[str, Any],
    qualification: Mapping[str, Any],
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE cb6_runs (
                run_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                production_component_identity TEXT NOT NULL,
                method_implementation_sha256 TEXT NOT NULL,
                fixture_membership_digest TEXT NOT NULL,
                qualification_status TEXT NOT NULL,
                treatment_qualified INTEGER NOT NULL CHECK (treatment_qualified IN (0, 1))
            );
            CREATE TABLE cb6_case_results (
                run_id TEXT NOT NULL,
                arm TEXT NOT NULL,
                case_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, arm, case_id),
                FOREIGN KEY (run_id) REFERENCES cb6_runs(run_id)
            );
            CREATE TABLE cb6_arm_metrics (
                run_id TEXT NOT NULL,
                arm TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, arm),
                FOREIGN KEY (run_id) REFERENCES cb6_runs(run_id)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO cb6_runs VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                CB6_SQLITE_SCHEMA_VERSION,
                component["identity"],
                component["method_implementation_sha256"],
                fixture.membership_digest,
                qualification["status"],
                int(bool(qualification["treatment_qualified"])),
            ),
        )
        for arm in TREATMENT_ORDER:
            for result in arms[arm]:
                connection.execute(
                    "INSERT INTO cb6_case_results VALUES (?, ?, ?, ?, ?)",
                    (
                        run_id,
                        arm,
                        result.case_id,
                        result.status,
                        _canonical_json_text(result.to_dict()).rstrip("\n"),
                    ),
                )
            connection.execute(
                "INSERT INTO cb6_arm_metrics VALUES (?, ?, ?)",
                (
                    run_id,
                    arm,
                    _canonical_json_text(metrics["arms"][arm]).rstrip("\n"),
                ),
            )
        connection.commit()
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check != ("ok",):
            raise CB6Error("new isolated C-B6 SQLite failed quick_check")
    finally:
        connection.close()
    if Path(f"{path}-wal").exists() or Path(f"{path}-shm").exists():
        raise CB6Error("isolated C-B6 SQLite left WAL/SHM sidecars")


def _artifact_payloads(
    *,
    run_id: str,
    fixture: CB6Fixture,
    arms: Mapping[str, Sequence[ArmCaseResult]],
    raw_arms: Mapping[str, Sequence[Mapping[str, Any]]],
    evidence: Mapping[str, Any],
    metrics: Mapping[str, Any],
    qualification: Mapping[str, Any],
    gate: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    fixture_payload = fixture.canonical_payload()
    fixture_validation = validate_programmatic_fixture(fixture)
    status_counts = Counter(record.status for record in fixture.labels)
    labels_payload = {
        "schema_version": CB6_LABEL_VERSION,
        "run_id": run_id,
        "source": "fixed-programmatic-temporary-git-fixture",
        "fixture": fixture_payload,
        "fixture_digest": _fingerprint(fixture_payload),
        "membership_digest": fixture.membership_digest,
        "record_count": len(fixture.labels),
        "labeled_case_count": status_counts["labeled"],
        "canonical_symbol_label_count": sum(
            len(record.expected_symbols) for record in fixture.labels
        ),
        "status_counts": dict(sorted(status_counts.items())),
        "validation": fixture_validation,
        "labels_are_production_results": False,
        "label_tamper_detection": "complete-canonical-record-membership-sha256",
    }
    results_payload = {
        "schema_version": CB6_RESULTS_VERSION,
        "run_id": run_id,
        "execution_status": "completed",
        "production_execution": True,
        "production_component": evidence["production_component"],
        "production_component_identity": PRODUCTION_COMPONENT_IDENTITY,
        "method_implementation_sha256": evidence["production_component"][
            "method_implementation_sha256"
        ],
        "fixture_digest": labels_payload["fixture_digest"],
        "label_membership_digest": fixture.membership_digest,
        "treatment_order": list(TREATMENT_ORDER),
        "treatment_matrix": TREATMENT_MATRIX,
        "denominator": evidence["denominator"],
        "lineage": evidence["lineage"],
        "arms": {arm: list(raw_arms[arm]) for arm in TREATMENT_ORDER},
        "result_case_counts": {arm: len(arms[arm]) for arm in TREATMENT_ORDER},
        "wrong_parent_policy": "preflight-rejected-before-production-mapper",
        "status_reporting": "expected-label-status-and-observed-production-status-both-preserved",
        "gate_authorization_snapshot": {
            "status": gate["status"],
            "engineering_status": gate["engineering_status"],
            "production_execution_authorized": gate["production_execution_authorized"],
            "selected_block_timestamp": gate.get("selected_block_timestamp"),
            "decision_record": gate["decision_record"],
            "decision_block_hash": gate["decision_block_hash"],
            "report": gate["report"],
            "report_sha256": gate["report_sha256"],
        },
    }
    metrics_payload = {
        **metrics,
        "run_id": run_id,
        "fixture_digest": labels_payload["fixture_digest"],
        "label_membership_digest": fixture.membership_digest,
        "qualification": qualification,
        "qualification_source": "fixed-c-b6-contract",
    }
    performance_payload = {
        "schema_version": CB6_PERFORMANCE_VERSION,
        "run_id": run_id,
        "measurement_source": "per-case-perf-counter-ns-and-tracemalloc-peak",
        "observation_count_per_arm": len(fixture.labels),
        "arms": {
            arm: {
                "summary": metrics["arms"][arm]["performance"],
                "observations": [
                    {
                        "case_id": result.case_id,
                        "duration_ns": result.duration_ns,
                        "peak_memory_bytes": result.peak_memory_bytes,
                    }
                    for result in arms[arm]
                ],
            }
            for arm in TREATMENT_ORDER
        },
    }
    for payload in (
        labels_payload,
        results_payload,
        metrics_payload,
        performance_payload,
    ):
        validate_portable_payload(payload)
    return labels_payload, results_payload, metrics_payload, performance_payload


def _build_production_artifact(
    directory: Path,
    *,
    run_id: str,
    fixture: CB6Fixture,
    arms: Mapping[str, Sequence[ArmCaseResult]],
    raw_arms: Mapping[str, Sequence[Mapping[str, Any]]],
    evidence: Mapping[str, Any],
    metrics: Mapping[str, Any],
    qualification: Mapping[str, Any],
    gate: Mapping[str, Any],
) -> None:
    if directory.exists():
        raise CB6Error("new C-B6 artifact staging directory already exists")
    directory.mkdir(mode=0o755)
    labels, results, metric_payload, performance = _artifact_payloads(
        run_id=run_id,
        fixture=fixture,
        arms=arms,
        raw_arms=raw_arms,
        evidence=evidence,
        metrics=metrics,
        qualification=qualification,
        gate=gate,
    )
    for name, payload in (
        ("labels.json", labels),
        ("results.json", results),
        ("metrics.json", metric_payload),
        ("performance.json", performance),
    ):
        _write_canonical_json(directory / name, payload)
    _create_portable_database(
        directory / "evaluation.sqlite3",
        run_id=run_id,
        component=evidence["production_component"],
        fixture=fixture,
        arms=arms,
        metrics=metric_payload,
        qualification=qualification,
    )
    security = {
        "schema_version": CB6_SECURITY_VERSION,
        "run_id": run_id,
        "status": "clean",
        "finding_count": 0,
        "checks": {
            "canonical_json": "passed",
            "portable_relative_identifiers": "passed",
            "absolute_path_free": "passed",
            "secret_free": "passed",
            "regular_files_only": "passed",
            "symlink_free": "passed",
            "wal_shm_free": "passed",
            "pyc_free": "passed",
            "isolated_sqlite": "passed",
        },
    }
    _write_canonical_json(directory / "security.json", security)
    artifact_files = {
        name: {
            "bytes": (directory / name).stat().st_size,
            "sha256": _sha256_file(directory / name),
        }
        for name in ARTIFACT_FILES
    }
    controls = {
        "qualified_global_retrieval_baseline": {
            "name": "C-B0",
            "run_id": CB0_QUALIFIED_RUN_ID,
            "treatment_qualified": True,
            "scope": "global-retrieval-anchor-only",
            "replaced_by_cb6": False,
        },
        "audit_controls": {
            name: {
                "run_id": control_run_id,
                "treatment_qualified": False,
                "qualification_use": "forbidden",
            }
            for name, control_run_id in AUDIT_CONTROL_RUN_IDS.items()
        },
        "cb6_scope": {
            "kind": "independent-diff-binding-quality-audit",
            "replaces_cb0": False,
        },
    }
    manifest = {
        "schema_version": CB6_SCHEMA_VERSION,
        "portable_schema_version": CB6_PORTABLE_VERSION,
        "run_id": run_id,
        "execution_status": "completed",
        "production_execution": True,
        "production_component_identity": PRODUCTION_COMPONENT_IDENTITY,
        "method_implementation_sha256": evidence["production_component"][
            "method_implementation_sha256"
        ],
        "fixture_digest": labels["fixture_digest"],
        "label_membership_digest": fixture.membership_digest,
        "denominator_membership_digest": evidence["denominator"]["membership_digest"],
        "same_eligible_denominator": True,
        "same_parent_target_membership": True,
        "qualification_status": qualification["status"],
        "treatment_qualified": qualification["treatment_qualified"],
        "engineering_pass_is_quality_qualification": False,
        "controls": controls,
        "artifact_files": artifact_files,
        "artifact_set_hash": _fingerprint(artifact_files),
        "verification_contract": "canonical-portable-verify-only-read-only",
    }
    _write_canonical_json(directory / "manifest.json", manifest)


def verify_artifact(directory: Path) -> dict[str, Any]:
    """Verify one C-B6 artifact read-only; never consult or mutate live state."""

    if directory.is_symlink() or not directory.is_dir():
        raise CB6Error("C-B6 artifact must be a non-symlink directory")
    artifact = directory.resolve()
    entries = {item.name for item in artifact.iterdir()}
    if entries != EXPECTED_ARTIFACT_ENTRIES:
        raise CB6Error("C-B6 artifact entries differ from the portable contract")
    for item in artifact.iterdir():
        if item.is_symlink() or not item.is_file():
            raise CB6Error("C-B6 artifact may contain only regular top-level files")

    payloads: dict[str, dict[str, Any]] = {}
    for name in ("manifest.json", *ARTIFACT_FILES[:-1]):
        path = artifact / name
        try:
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise CB6Error(f"unable to read C-B6 artifact JSON: {name}") from exc
        if not isinstance(value, dict):
            raise CB6Error(f"C-B6 artifact JSON must be an object: {name}")
        if raw != _canonical_json_text(value):
            raise CB6Error(f"C-B6 artifact JSON is not canonical: {name}")
        validate_portable_payload(value)
        payloads[name] = value

    manifest = payloads["manifest.json"]
    if (
        manifest.get("schema_version") != CB6_SCHEMA_VERSION
        or manifest.get("portable_schema_version") != CB6_PORTABLE_VERSION
        or manifest.get("execution_status") != "completed"
        or manifest.get("production_execution") is not True
        or manifest.get("production_component_identity") != PRODUCTION_COMPONENT_IDENTITY
        or manifest.get("method_implementation_sha256")
        != CB6_RELEASED_METHOD_IMPLEMENTATION_SHA256
        or manifest.get("treatment_qualified") not in {True, False}
    ):
        raise CB6Error("C-B6 manifest does not describe a completed production audit")
    files = manifest.get("artifact_files")
    if not isinstance(files, dict) or set(files) != set(ARTIFACT_FILES):
        raise CB6Error("C-B6 manifest file membership differs from the portable contract")
    for name in ARTIFACT_FILES:
        metadata = files.get(name)
        path = artifact / name
        if (
            not isinstance(metadata, dict)
            or metadata.get("bytes") != path.stat().st_size
            or metadata.get("sha256") != _sha256_file(path)
        ):
            raise CB6Error(f"C-B6 artifact hash or size mismatch: {name}")
    if manifest.get("artifact_set_hash") != _fingerprint(files):
        raise CB6Error("C-B6 artifact set hash mismatch")

    labels = payloads["labels.json"]
    results = payloads["results.json"]
    metrics = payloads["metrics.json"]
    performance = payloads["performance.json"]
    security = payloads["security.json"]
    run_id = manifest.get("run_id")
    if (
        not isinstance(run_id, str)
        or not run_id.startswith("evaluation-run://project-code-cb6-v1/")
        or any(payload.get("run_id") != run_id for payload in payloads.values())
    ):
        raise CB6Error("C-B6 artifact run identity is inconsistent")
    fixture_payload = labels.get("fixture")
    records = fixture_payload.get("labels") if isinstance(fixture_payload, dict) else None
    case_ids = fixture_payload.get("case_ids") if isinstance(fixture_payload, dict) else None
    if not isinstance(records, list) or not isinstance(case_ids, list):
        raise CB6Error("C-B6 labels payload lacks canonical records")
    recomputed_membership = _fingerprint(
        {
            "schema_version": CB6_FIXTURE_VERSION,
            "case_ids": case_ids,
            "records": records,
        }
    )
    status_counts = Counter(record.get("status") for record in records if isinstance(record, dict))
    symbol_label_count = sum(
        len(record.get("expected_symbols", [])) for record in records if isinstance(record, dict)
    )
    if (
        len(records) != len(FIXTURE_CASE_IDS)
        or case_ids != list(sorted(FIXTURE_CASE_IDS))
        or recomputed_membership != labels.get("membership_digest")
        or recomputed_membership != fixture_payload.get("membership_digest")
        or recomputed_membership != manifest.get("label_membership_digest")
        or recomputed_membership != FROZEN_LABEL_MEMBERSHIP_DIGEST
        or labels.get("fixture_digest") != FROZEN_FIXTURE_DIGEST
        or labels.get("labeled_case_count") != 8
        or labels.get("canonical_symbol_label_count") != 16
        or status_counts
        != Counter(
            {
                "labeled": 8,
                "noise": 1,
                "unavailable": 1,
                "rejected": 1,
                "ambiguous": 1,
            }
        )
        or symbol_label_count != 16
    ):
        raise CB6Error("C-B6 canonical label membership or counts are invalid")

    result_arms = results.get("arms")
    metric_arms = metrics.get("arms")
    performance_arms = performance.get("arms")
    denominator = metrics.get("denominator")
    if (
        not isinstance(result_arms, dict)
        or set(result_arms) != set(TREATMENT_ORDER)
        or not isinstance(metric_arms, dict)
        or set(metric_arms) != set(TREATMENT_ORDER)
        or not isinstance(performance_arms, dict)
        or set(performance_arms) != set(TREATMENT_ORDER)
        or results.get("treatment_order") != list(TREATMENT_ORDER)
        or not isinstance(denominator, dict)
        or denominator.get("same_denominator") is not True
        or denominator.get("same_parent_target_membership") is not True
        or denominator.get("case_ids") != list(sorted(FIXTURE_CASE_IDS))
        or denominator.get("membership_digest") != manifest.get("denominator_membership_digest")
        or denominator.get("membership_digest") != FROZEN_DENOMINATOR_MEMBERSHIP_DIGEST
    ):
        raise CB6Error("C-B6 three-arm denominator or membership is invalid")
    for arm in TREATMENT_ORDER:
        arm_results = result_arms[arm]
        if (
            not isinstance(arm_results, list)
            or len(arm_results) != len(FIXTURE_CASE_IDS)
            or [item.get("case_id") for item in arm_results] != list(sorted(FIXTURE_CASE_IDS))
            or metric_arms[arm].get("production_result") is not True
            or metric_arms[arm].get("measurement_source") != "production-cb6-run"
            or metric_arms[arm].get("label_status", {}).get("membership_digest")
            != recomputed_membership
            or performance_arms[arm].get("summary") != metric_arms[arm].get("performance")
        ):
            raise CB6Error(f"C-B6 production arm payload is inconsistent: {arm}")
        for item in arm_results:
            result = item.get("result")
            if not isinstance(result, dict) or result.get("case_id") != item.get("case_id"):
                raise CB6Error(f"C-B6 raw/result case mismatch: {arm}")
            mapper_called = item.get("production_mapper_called")
            if item.get("case_id") == "cb6-wrong-parent":
                if mapper_called is not False or result.get("status") != "rejected":
                    raise CB6Error("C-B6 wrong-parent policy is not preserved")
            elif mapper_called is not True:
                raise CB6Error("C-B6 production mapper call evidence is missing")
    qualification = metrics.get("qualification")
    if (
        metrics.get("production_result") is not True
        or metrics.get("measurement_source") != "production-cb6-run"
        or not isinstance(qualification, dict)
        or qualification.get("status") != manifest.get("qualification_status")
        or qualification.get("treatment_qualified") != manifest.get("treatment_qualified")
        or qualification.get("engineering_pass_is_quality_qualification") is not False
        or labels.get("fixture_digest") != manifest.get("fixture_digest")
        or results.get("production_component_identity") != PRODUCTION_COMPONENT_IDENTITY
        or results.get("method_implementation_sha256")
        != manifest.get("method_implementation_sha256")
        or security.get("status") != "clean"
        or security.get("finding_count") != 0
        or set((security.get("checks") or {}).values()) != {"passed"}
    ):
        raise CB6Error("C-B6 metrics, qualification, identity, or security payload is invalid")
    controls = manifest.get("controls") or {}
    baseline = controls.get("qualified_global_retrieval_baseline") or {}
    audit_controls = controls.get("audit_controls") or {}
    if (
        baseline.get("run_id") != CB0_QUALIFIED_RUN_ID
        or baseline.get("treatment_qualified") is not True
        or baseline.get("replaced_by_cb6") is not False
        or set(audit_controls) != set(AUDIT_CONTROL_RUN_IDS)
        or any(
            item.get("treatment_qualified") is not False
            or item.get("qualification_use") != "forbidden"
            for item in audit_controls.values()
        )
        or (controls.get("cb6_scope") or {}).get("replaces_cb0") is not False
    ):
        raise CB6Error("C-B6 artifact misstates the C-B0/audit control anchor")

    database = artifact / "evaluation.sqlite3"
    if Path(f"{database}-wal").exists() or Path(f"{database}-shm").exists():
        raise CB6Error("portable C-B6 SQLite may not contain WAL/SHM sidecars")
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            run_row = connection.execute(
                """
                SELECT run_id, production_component_identity,
                       method_implementation_sha256, fixture_membership_digest,
                       qualification_status, treatment_qualified
                FROM cb6_runs
                """
            ).fetchall()
            case_count = connection.execute("SELECT COUNT(*) FROM cb6_case_results").fetchone()
            metric_count = connection.execute("SELECT COUNT(*) FROM cb6_arm_metrics").fetchone()
            database_dump = "\n".join(connection.iterdump())
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise CB6Error("C-B6 portable SQLite is not readable") from exc
    if quick_check != ("ok",):
        raise CB6Error("C-B6 portable SQLite failed quick_check")
    expected_run_row = (
        run_id,
        PRODUCTION_COMPONENT_IDENTITY,
        manifest["method_implementation_sha256"],
        recomputed_membership,
        manifest["qualification_status"],
        int(manifest["treatment_qualified"]),
    )
    if (
        run_row != [expected_run_row]
        or case_count != (len(TREATMENT_ORDER) * len(FIXTURE_CASE_IDS),)
        or metric_count != (len(TREATMENT_ORDER),)
        or not _portable_string(database_dump)
    ):
        raise CB6Error("C-B6 portable SQLite membership or content is invalid")
    for item in artifact.iterdir():
        if item.suffix == ".pyc" or item.name.endswith(("-wal", "-shm")):
            raise CB6Error("C-B6 artifact contains a forbidden generated file")
    return {
        "status": "verified",
        "verification_mode": "read-only",
        "run_id": run_id,
        "treatment_qualified": manifest["treatment_qualified"],
        "qualification_status": manifest["qualification_status"],
        "production_component_identity": PRODUCTION_COMPONENT_IDENTITY,
        "method_implementation_sha256": manifest["method_implementation_sha256"],
        "fixture_digest": manifest["fixture_digest"],
        "label_membership_digest": recomputed_membership,
        "denominator_membership_digest": manifest["denominator_membership_digest"],
        "metrics": {
            arm: {
                "overall": metric_arms[arm]["overall"],
                "by_side": metric_arms[arm]["by_side"],
                "by_role": metric_arms[arm]["by_role"],
                "change_context": metric_arms[arm]["change_context"],
                "false_affected_symbol": metric_arms[arm]["false_affected_symbol"],
                "ambiguity_noise_unavailable": metric_arms[arm]["ambiguity_noise_unavailable"],
                "performance": metric_arms[arm]["performance"],
            }
            for arm in TREATMENT_ORDER
        },
        "artifact_file_count": len(EXPECTED_ARTIFACT_ENTRIES),
    }


def execute_cb6(
    *,
    root: Path | None = None,
    runs_dir: Path | None = None,
    execution_mode: str = "qualified",
    diff_symbol_component: Any | None = None,
    diff_symbol_factory: Any | None = None,
    c6_02_gate_approved: bool = False,
) -> Path:
    """Execute and atomically publish the one authorized production C-B6 Run."""

    resolved_root = (root or _default_repository_root()).resolve()
    canonical_runs = (resolved_root / "evals/code/runs").resolve()
    resolved_runs = (runs_dir or canonical_runs).resolve()
    if execution_mode != "qualified":
        raise CB6Error("C-B6 has no test/fake artifact execution mode")
    if diff_symbol_component is not None or diff_symbol_factory is not None:
        raise CB6Error(
            "qualified C-B6 forbids fake, injected, wrapped, monkeypatched, or subclassed "
            "Diff-to-Symbol components"
        )
    if not c6_02_gate_approved:
        raise CB6Error("C-B6 execution requires explicit C6-02 completion Gate approval")
    if resolved_runs != canonical_runs:
        raise CB6Error("qualified C-B6 must use the canonical evals/code/runs directory")
    gate = _gate_status(resolved_root)
    if gate.get("status") != "authorized":
        raise CB6Error(
            "C6-02 completion Gate has not authorized C-B6 production execution: "
            f"{gate.get('reason') or 'exact final authorization is missing'}"
        )
    component_type = _production_component_type()
    if component_type is None:
        raise CB6Error("exact production Diff-to-Symbol component is unavailable")
    try:
        component = component_type()
    except Exception as exc:
        raise CB6Error("exact production Diff-to-Symbol component cannot be constructed") from exc
    require_production_component_identity(component)
    if not canonical_runs.is_dir() or canonical_runs.is_symlink():
        raise CB6Error("canonical evals/code/runs directory is missing or redirected")
    existing_cb6 = _existing_cb6_run_directories(canonical_runs)
    if existing_cb6:
        raise CB6Error("C-B6 production Run already exists; overwrite or rerun is forbidden")

    production_database = resolved_root / "var/evidence-rag.sqlite3"
    database_before = _integrity_file_state(production_database)
    sidecars_before = {
        suffix: _integrity_file_state(Path(f"{production_database}{suffix}"))
        for suffix in ("-wal", "-shm")
    }
    old_runs_before = _integrity_tree_state(canonical_runs)
    artifact_name = uuid.uuid4().hex
    run_id = f"evaluation-run://project-code-cb6-v1/{artifact_name}"
    final_directory = canonical_runs / artifact_name

    with tempfile.TemporaryDirectory(prefix="cb6-production-") as temporary:
        workspace = Path(temporary)
        fixture = build_programmatic_fixture(workspace / "fixture")
        arms, raw_arms, evidence = _execute_production_treatments(fixture)
        metrics = _production_metrics(fixture, arms)
        qualification = _quality_decision(metrics)
        staging = workspace / artifact_name
        _build_production_artifact(
            staging,
            run_id=run_id,
            fixture=fixture,
            arms=arms,
            raw_arms=raw_arms,
            evidence=evidence,
            metrics=metrics,
            qualification=qualification,
            gate=gate,
        )
        verify_artifact(staging)
        if staging.stat().st_dev != canonical_runs.stat().st_dev:
            raise CB6Error("C-B6 staging and canonical Run root are not on one filesystem")
        if final_directory.exists():
            raise CB6Error("fresh C-B6 Run id collided with an existing path")
        gate_after = _gate_status(resolved_root)
        if gate_after.get("status") != "authorized" or gate_after.get(
            "decision_block_hash"
        ) != gate.get("decision_block_hash"):
            raise CB6Error("C6-02 authorization changed during C-B6 execution")
        require_production_component_identity(component)
        if (
            _integrity_file_state(production_database) != database_before
            or {
                suffix: _integrity_file_state(Path(f"{production_database}{suffix}"))
                for suffix in ("-wal", "-shm")
            }
            != sidecars_before
            or _integrity_tree_state(canonical_runs) != old_runs_before
        ):
            raise CB6Error("formal database or existing Run tree changed before publication")
        os.replace(staging, final_directory)

    verified = verify_artifact(final_directory)
    database_after = _integrity_file_state(production_database)
    sidecars_after = {
        suffix: _integrity_file_state(Path(f"{production_database}{suffix}"))
        for suffix in ("-wal", "-shm")
    }
    new_runs = _integrity_tree_state(canonical_runs)
    if database_after != database_before or sidecars_after != sidecars_before:
        raise CB6Error("formal database changed during C-B6 production execution")
    if any(new_runs.get(name) != state for name, state in old_runs_before.items()):
        raise CB6Error("an existing Run file changed during C-B6 production execution")
    added = set(new_runs) - set(old_runs_before)
    expected_added = {f"{artifact_name}/{name}" for name in EXPECTED_ARTIFACT_ENTRIES}
    if added != expected_added or verified["run_id"] != run_id:
        raise CB6Error("C-B6 publication did not add exactly one canonical artifact")
    return final_directory
