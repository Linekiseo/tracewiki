"""Deterministic, version-exact Diff hunk to Code symbol evidence.

The mapper is deliberately runtime and persistence independent.  It accepts
already materialized C6 historical units for the parent and target revisions,
maps only exact repository paths and versions, and emits registered C4
``CONTAINS``/``AFFECTS`` evidence.  Ambiguous alignments remain candidates and
never become graph assertions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Self

from .contracts import CodeRelationType
from .graph_v2 import (
    CodeEdgeDerivationLayer,
    CodeGraphEntityType,
    validate_edge_assertion,
)
from .history_v2 import HistoricalUnit, LineageStatus, SymbolLineage

DIFF_SYMBOL_CONTRACT_VERSION = "c6-diff-symbol-v2"
DIFF_SYMBOL_MAPPER_VERSION = "c6-diff-symbol-mapper-v2"
DIFF_SYMBOL_EVALUATION_VERSION = "c6-diff-symbol-evaluation-v1"

_FULL_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))?"
    r" \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


class DiffSymbolError(ValueError):
    """Base fail-closed Diff-to-Symbol error."""


class DiffSymbolScopeError(DiffSymbolError):
    """A hunk, versioned symbol, or lineage object has conflicting scope."""


class DiffChangeType(StrEnum):
    ADD = "add"
    DELETE = "delete"
    MODIFY = "modify"
    RENAME = "rename"


class DiffSymbolSide(StrEnum):
    OLD = "old"
    NEW = "new"


class DiffSymbolMatchStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANDIDATE = "candidate"


class DiffSymbolMatchKind(StrEnum):
    FUNCTION_BODY = "function_body"
    SIGNATURE = "signature"
    CLASS_LEVEL = "class_level"
    FILE_TOP_LEVEL = "file_top_level"
    LINE_ONLY = "line_only"


class DiffSymbolSignal(StrEnum):
    EXACT_OVERLAP = "exact_overlap"
    AST_ENCLOSING = "ast_enclosing"
    SIGNATURE_LINE = "signature_line"
    RENAME_PATH = "rename_path"
    ZERO_COUNT_BOUNDARY = "zero_count_boundary"
    LINE_ONLY = "line_only"
    FILE_TOP_LEVEL = "file_top_level"


class DiffSymbolDiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiffSymbolDiagnosticCode(StrEnum):
    AMBIGUOUS_SYMBOLS = "ambiguous_symbols"
    BINARY_UNAVAILABLE = "binary_unavailable"
    FILE_TOP_LEVEL = "file_top_level"
    LINE_ONLY = "line_only"
    MISSING_OLD_SIDE = "missing_old_side"
    MISSING_NEW_SIDE = "missing_new_side"
    NO_CHANGED_LINES = "no_changed_lines"
    NO_EXACT_PATH = "no_exact_path"
    NO_SYMBOL_MATCH = "no_symbol_match"
    WHITESPACE_ONLY_SKIPPED = "whitespace_only_skipped"


class DiffSymbolTreatmentStatus(StrEnum):
    MAPPED = "mapped"
    PARTIAL = "partial"
    NO_MATCH = "no_match"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


class DiffSymbolEvaluationLabel(StrEnum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    AMBIGUOUS = "ambiguous"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    NOT_EVALUATED = "not_evaluated"


class _CanonicalContract:
    def canonical_json_bytes(self) -> bytes:
        return _canonical_json(asdict(self))

    def canonical_sha256(self) -> str:
        return _sha256(self.canonical_json_bytes())


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip() or _CONTROL.search(value):
        raise ValueError(f"{field_name} must be non-empty, trimmed, and control-free")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _full_sha(value: object, field_name: str) -> str:
    sha = _text(value, field_name).lower()
    if not _FULL_SHA.fullmatch(sha):
        raise ValueError(f"{field_name} must be a full Git commit SHA")
    return sha


def _safe_path(value: object, field_name: str = "path") -> str:
    path = _text(value, field_name)
    if "\\" in path:
        raise ValueError(f"{field_name} must use POSIX separators")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ValueError(f"{field_name} must be repository relative")
    normalized = candidate.as_posix()
    if normalized in {"", "."} or normalized.startswith("./"):
        raise ValueError(f"{field_name} must identify a repository-relative file")
    return normalized


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes | str | object) -> str:
    if isinstance(value, str):
        payload = value.encode("utf-8")
    elif isinstance(value, bytes):
        payload = value
    else:
        payload = _canonical_json(value)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _unit_float(value: object, field_name: str) -> float:
    if type(value) is not float or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be an exact float in [0, 1]")
    return value


def _ordered_unique_text(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} values must be unique")
    return tuple(sorted(normalized))


def _ordered_lines(values: Iterable[int]) -> tuple[int, ...]:
    lines = tuple(values)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in lines):
        raise ValueError("changed lines must be positive integers")
    if len(lines) != len(set(lines)):
        raise ValueError("changed lines must be unique")
    return tuple(sorted(lines))


@dataclass(frozen=True, slots=True)
class DiffHunkProvenance(_CanonicalContract):
    project_id: str
    repository_id: str
    commit_sha: str
    parent_commit_sha: str
    target_commit_sha: str
    acl_ref: str
    source: str
    parser_version: str
    evidence_locator: str
    contract_version: str = DIFF_SYMBOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "acl_ref",
            "source",
            "parser_version",
            "evidence_locator",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("commit_sha", "parent_commit_sha", "target_commit_sha"):
            object.__setattr__(self, name, _full_sha(getattr(self, name), name))
        if self.commit_sha != self.target_commit_sha:
            raise ValueError("diff provenance commit_sha must equal target_commit_sha")
        if self.parent_commit_sha == self.target_commit_sha:
            raise ValueError("diff provenance requires an explicit version transition")


@dataclass(frozen=True, slots=True)
class DiffHunkVersion(_CanonicalContract):
    project_id: str
    repository_id: str
    commit_sha: str
    parent_commit_sha: str
    target_commit_sha: str
    parent_generation_id: str
    target_generation_id: str
    acl_ref: str
    path: str
    old_path: str | None
    change_type: DiffChangeType
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    patch: str
    patch_hash: str
    hunk_id: str
    provenance: DiffHunkProvenance
    is_binary: bool = False
    contract_version: str = DIFF_SYMBOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "parent_generation_id",
            "target_generation_id",
            "acl_ref",
            "hunk_id",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("commit_sha", "parent_commit_sha", "target_commit_sha"):
            object.__setattr__(self, name, _full_sha(getattr(self, name), name))
        if self.commit_sha != self.target_commit_sha:
            raise ValueError("DiffHunkVersion commit_sha must equal target_commit_sha")
        if self.parent_commit_sha == self.target_commit_sha:
            raise ValueError("DiffHunkVersion requires an explicit version transition")
        object.__setattr__(self, "path", _safe_path(self.path))
        if self.old_path is not None:
            object.__setattr__(self, "old_path", _safe_path(self.old_path, "old_path"))
        for name in ("old_start", "old_count", "new_start", "new_count"):
            _non_negative_int(getattr(self, name), name)
        if not isinstance(self.patch, str):
            raise TypeError("patch must be a string")
        if not isinstance(self.is_binary, bool):
            raise TypeError("is_binary must be a bool")
        if not isinstance(self.change_type, DiffChangeType):
            raise TypeError("change_type must be a DiffChangeType")
        expected_patch_hash = _sha256(self.patch)
        if self.patch_hash != expected_patch_hash or not _SHA256.fullmatch(self.patch_hash):
            raise ValueError("patch_hash must be the canonical SHA-256 of patch")
        if self.change_type is DiffChangeType.ADD:
            if self.old_count != 0 or self.old_path is not None:
                raise ValueError("add hunks require an absent zero-count old side")
        elif self.change_type is DiffChangeType.DELETE:
            if self.new_count != 0:
                raise ValueError("delete hunks require a zero-count new side")
            if self.old_path not in {None, self.path}:
                raise ValueError("delete old_path must be absent or equal path")
        elif self.change_type is DiffChangeType.MODIFY:
            if self.old_path not in {None, self.path}:
                raise ValueError("modify hunks cannot change path")
        elif self.change_type is DiffChangeType.RENAME and (
            self.old_path is None or self.old_path == self.path
        ):
            raise ValueError("rename hunks require distinct old_path and path")
        if not isinstance(self.provenance, DiffHunkProvenance):
            raise TypeError("provenance must be DiffHunkProvenance")
        if _provenance_scope(self.provenance) != _hunk_scope(self):
            raise ValueError("hunk scope conflicts with provenance")

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        repository_id: str,
        parent_commit_sha: str,
        target_commit_sha: str,
        parent_generation_id: str,
        target_generation_id: str,
        acl_ref: str,
        path: str,
        change_type: DiffChangeType,
        old_start: int,
        old_count: int,
        new_start: int,
        new_count: int,
        patch: str,
        old_path: str | None = None,
        is_binary: bool = False,
        source: str = "git-diff",
        parser_version: str = "unified-diff-v1",
        evidence_locator: str | None = None,
        hunk_id: str | None = None,
    ) -> Self:
        target = _full_sha(target_commit_sha, "target_commit_sha")
        parent = _full_sha(parent_commit_sha, "parent_commit_sha")
        normalized_path = _safe_path(path)
        locator = evidence_locator or (
            f"git-diff://{repository_id}/{parent}..{target}/{normalized_path}"
            f"#old={old_start},{old_count}&new={new_start},{new_count}"
        )
        provenance = DiffHunkProvenance(
            project_id=project_id,
            repository_id=repository_id,
            commit_sha=target,
            parent_commit_sha=parent,
            target_commit_sha=target,
            acl_ref=acl_ref,
            source=source,
            parser_version=parser_version,
            evidence_locator=locator,
        )
        patch_hash = _sha256(patch)
        identity = hunk_id or "diff-hunk://sha256/" + _sha256(
            {
                "acl_ref": acl_ref,
                "change_type": change_type.value,
                "new_count": new_count,
                "new_start": new_start,
                "old_count": old_count,
                "old_path": old_path,
                "old_start": old_start,
                "parent_commit_sha": parent,
                "patch_hash": patch_hash,
                "path": normalized_path,
                "project_id": project_id,
                "repository_id": repository_id,
                "target_commit_sha": target,
            }
        ).removeprefix("sha256:")
        return cls(
            project_id=project_id,
            repository_id=repository_id,
            commit_sha=target,
            parent_commit_sha=parent,
            target_commit_sha=target,
            parent_generation_id=parent_generation_id,
            target_generation_id=target_generation_id,
            acl_ref=acl_ref,
            path=normalized_path,
            old_path=old_path,
            change_type=change_type,
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            patch=patch,
            patch_hash=patch_hash,
            hunk_id=identity,
            provenance=provenance,
            is_binary=is_binary,
        )


def _hunk_scope(hunk: DiffHunkVersion) -> tuple[str, ...]:
    return (
        hunk.project_id,
        hunk.repository_id,
        hunk.commit_sha,
        hunk.parent_commit_sha,
        hunk.target_commit_sha,
        hunk.acl_ref,
    )


def _provenance_scope(provenance: DiffHunkProvenance) -> tuple[str, ...]:
    return (
        provenance.project_id,
        provenance.repository_id,
        provenance.commit_sha,
        provenance.parent_commit_sha,
        provenance.target_commit_sha,
        provenance.acl_ref,
    )


@dataclass(frozen=True, slots=True)
class SymbolVersionRef(_CanonicalContract):
    project_id: str
    repository_id: str
    commit_sha: str
    generation_id: str
    acl_ref: str
    namespace: str
    requested_ref: str
    symbol_version_id: str
    unit_id: str
    entity_id: str
    entity_type: CodeGraphEntityType
    path: str
    language: str
    symbol_name: str
    symbol_kind: str
    qualified_name: str
    signature: str | None
    unit_type: str
    ast_node_type: str
    structural_path: str
    start_line: int
    end_line: int
    evidence_locator: str
    contract_version: str = DIFF_SYMBOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "generation_id",
            "acl_ref",
            "namespace",
            "requested_ref",
            "symbol_version_id",
            "unit_id",
            "entity_id",
            "language",
            "unit_type",
            "ast_node_type",
            "structural_path",
            "evidence_locator",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "commit_sha", _full_sha(self.commit_sha, "commit_sha"))
        object.__setattr__(self, "path", _safe_path(self.path))
        for name in ("symbol_name", "symbol_kind", "qualified_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or _CONTROL.search(value):
                raise ValueError(f"{name} must be a control-free string")
        object.__setattr__(self, "signature", _optional_text(self.signature, "signature"))
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("symbol version line span must be one-based and ordered")
        if self.entity_type is CodeGraphEntityType.CODE_SYMBOL:
            if not self.symbol_name or not self.qualified_name:
                raise ValueError("CodeSymbol refs require explicit symbol identity")
        elif self.entity_type is CodeGraphEntityType.FILE_VERSION:
            if self.symbol_name or self.qualified_name:
                raise ValueError("file-top-level refs cannot masquerade as symbols")
        else:
            raise ValueError("Diff mapping targets only CodeSymbol or FileVersion refs")
        if f"@{self.commit_sha}/" not in self.evidence_locator:
            raise ValueError("symbol evidence locator must bind the exact commit SHA")

    @classmethod
    def from_historical_unit(cls, unit: HistoricalUnit) -> Self:
        if not isinstance(unit, HistoricalUnit):
            raise TypeError("unit must be a HistoricalUnit")
        if unit.unit_type not in {"symbol.ast_block", "file.surface"}:
            raise ValueError(
                "only explicit symbol.ast_block or file.surface historical units are targets"
            )
        if unit.unit_type == "symbol.ast_block" and not unit.is_symbol:
            raise ValueError("symbol.ast_block target lacks an explicit historical symbol")
        is_symbol = unit.unit_type == "symbol.ast_block" and unit.is_symbol
        entity_type = (
            CodeGraphEntityType.CODE_SYMBOL if is_symbol else CodeGraphEntityType.FILE_VERSION
        )
        identity_payload = {
            "acl_ref": unit.acl_ref,
            "commit_sha": unit.commit_sha,
            "generation_id": unit.generation_id,
            "path": unit.file_path,
            "project_id": unit.project_id,
            "qualified_name": unit.qualified_name if is_symbol else "",
            "repository_id": unit.repository_id,
            "span": [unit.start_line, unit.end_line],
            "unit_id": unit.unit_id,
        }
        prefix = "code-symbol-version" if is_symbol else "file-version"
        symbol_version_id = f"{prefix}://sha256/" + _sha256(identity_payload).removeprefix(
            "sha256:"
        )
        return cls(
            project_id=unit.project_id,
            repository_id=unit.repository_id,
            commit_sha=unit.commit_sha,
            generation_id=unit.generation_id,
            acl_ref=unit.acl_ref,
            namespace=unit.namespace,
            requested_ref=unit.requested_ref,
            symbol_version_id=symbol_version_id,
            unit_id=unit.unit_id,
            entity_id=unit.entity_id,
            entity_type=entity_type,
            path=unit.file_path,
            language=unit.language,
            symbol_name=unit.symbol_name if is_symbol else "",
            symbol_kind=unit.symbol_kind if is_symbol else "",
            qualified_name=unit.qualified_name if is_symbol else "",
            signature=unit.signature if is_symbol else None,
            unit_type=unit.unit_type,
            ast_node_type=unit.ast_node_type,
            structural_path=unit.structural_path,
            start_line=unit.start_line,
            end_line=unit.end_line,
            evidence_locator=unit.locator,
        )

    @property
    def is_symbol(self) -> bool:
        return self.entity_type is CodeGraphEntityType.CODE_SYMBOL


@dataclass(frozen=True, slots=True)
class DiffSymbolMatch(_CanonicalContract):
    match_id: str
    project_id: str
    repository_id: str
    parent_commit_sha: str
    target_commit_sha: str
    acl_ref: str
    hunk_id: str
    side: DiffSymbolSide
    change_role: str
    symbol: SymbolVersionRef
    status: DiffSymbolMatchStatus
    match_kind: DiffSymbolMatchKind
    confidence: float
    range_start: int
    range_count: int
    changed_lines: tuple[int, ...]
    overlap_start: int | None
    overlap_end: int | None
    overlap_line_count: int
    signals: tuple[DiffSymbolSignal, ...]
    review_required: bool
    evidence_locator: str
    explanation: str
    contract_version: str = DIFF_SYMBOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "match_id",
            "project_id",
            "repository_id",
            "acl_ref",
            "hunk_id",
            "change_role",
            "evidence_locator",
            "explanation",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("parent_commit_sha", "target_commit_sha"):
            object.__setattr__(self, name, _full_sha(getattr(self, name), name))
        _unit_float(self.confidence, "confidence")
        _non_negative_int(self.range_start, "range_start")
        _non_negative_int(self.range_count, "range_count")
        _non_negative_int(self.overlap_line_count, "overlap_line_count")
        object.__setattr__(self, "changed_lines", _ordered_lines(self.changed_lines))
        if not isinstance(self.side, DiffSymbolSide):
            raise TypeError("side must be a DiffSymbolSide")
        if not isinstance(self.status, DiffSymbolMatchStatus):
            raise TypeError("status must be a DiffSymbolMatchStatus")
        if not isinstance(self.match_kind, DiffSymbolMatchKind):
            raise TypeError("match_kind must be a DiffSymbolMatchKind")
        if any(not isinstance(item, DiffSymbolSignal) for item in self.signals):
            raise TypeError("signals must contain DiffSymbolSignal values")
        signals = tuple(sorted(set(self.signals), key=lambda item: item.value))
        if len(signals) != len(self.signals):
            raise ValueError("match signals must be unique")
        object.__setattr__(self, "signals", signals)
        if not isinstance(self.review_required, bool):
            raise TypeError("review_required must be a bool")
        if self.status is DiffSymbolMatchStatus.CONFIRMED and self.review_required:
            raise ValueError("confirmed matches cannot require review")
        if self.status is DiffSymbolMatchStatus.CANDIDATE and not self.review_required:
            raise ValueError("candidate matches must require review")
        if (self.overlap_start is None) != (self.overlap_end is None):
            raise ValueError("overlap start/end must both be present or absent")
        if self.overlap_start is not None:
            if self.overlap_start < 1 or self.overlap_end is None:
                raise ValueError("overlap lines must be positive")
            if self.overlap_end < self.overlap_start:
                raise ValueError("overlap line interval must be ordered")
            if self.overlap_line_count < 1:
                raise ValueError("non-empty overlap requires overlap_line_count")
        elif self.overlap_line_count != 0:
            raise ValueError("empty overlap requires zero overlap_line_count")
        expected_commit = (
            self.parent_commit_sha if self.side is DiffSymbolSide.OLD else self.target_commit_sha
        )
        if self.symbol.commit_sha != expected_commit:
            raise ValueError("match symbol is bound to the wrong side version")


@dataclass(frozen=True, slots=True)
class DiffSymbolEdge(_CanonicalContract):
    edge_id: str
    project_id: str
    repository_id: str
    parent_commit_sha: str
    target_commit_sha: str
    acl_ref: str
    relation_type: CodeRelationType
    source_id: str
    source_type: CodeGraphEntityType
    target_id: str
    target_type: CodeGraphEntityType
    hunk_id: str
    side: DiffSymbolSide | None
    change_role: str | None
    symbol_commit_sha: str | None
    range_start: int | None
    range_count: int | None
    overlap_start: int | None
    overlap_end: int | None
    overlap_line_count: int
    confidence: float
    evidence_locator: str
    patch_hash: str
    match_id: str | None
    signals: tuple[DiffSymbolSignal, ...]
    provenance: DiffHunkProvenance
    derivation: CodeEdgeDerivationLayer = CodeEdgeDerivationLayer.DETERMINISTIC
    contract_version: str = DIFF_SYMBOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "edge_id",
            "project_id",
            "repository_id",
            "acl_ref",
            "source_id",
            "target_id",
            "hunk_id",
            "evidence_locator",
            "patch_hash",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("parent_commit_sha", "target_commit_sha"):
            object.__setattr__(self, name, _full_sha(getattr(self, name), name))
        if self.symbol_commit_sha is not None:
            object.__setattr__(
                self,
                "symbol_commit_sha",
                _full_sha(self.symbol_commit_sha, "symbol_commit_sha"),
            )
        object.__setattr__(self, "change_role", _optional_text(self.change_role, "change_role"))
        object.__setattr__(self, "match_id", _optional_text(self.match_id, "match_id"))
        for name in ("range_start", "range_count"):
            value = getattr(self, name)
            if value is not None:
                _non_negative_int(value, name)
        _non_negative_int(self.overlap_line_count, "overlap_line_count")
        _unit_float(self.confidence, "confidence")
        if not isinstance(self.relation_type, CodeRelationType):
            raise TypeError("relation_type must be a registered CodeRelationType")
        if not isinstance(self.source_type, CodeGraphEntityType) or not isinstance(
            self.target_type,
            CodeGraphEntityType,
        ):
            raise TypeError("edge endpoint types must be CodeGraphEntityType values")
        if self.side is not None and not isinstance(self.side, DiffSymbolSide):
            raise TypeError("edge side must be a DiffSymbolSide or None")
        if any(not isinstance(item, DiffSymbolSignal) for item in self.signals):
            raise TypeError("edge signals must contain DiffSymbolSignal values")
        if not _SHA256.fullmatch(self.patch_hash):
            raise ValueError("edge patch_hash must be canonical")
        signals = tuple(sorted(set(self.signals), key=lambda item: item.value))
        if len(signals) != len(self.signals):
            raise ValueError("edge signals must be unique")
        object.__setattr__(self, "signals", signals)
        validate_edge_assertion(
            self.relation_type,
            source_type=self.source_type,
            target_type=self.target_type,
            derivation=self.derivation,
        )
        if self.relation_type is CodeRelationType.CONTAINS:
            if self.side is not None or self.match_id is not None:
                raise ValueError("commit containment is not side-specific match evidence")
            if (
                self.source_type is not CodeGraphEntityType.COMMIT
                or self.target_type is not CodeGraphEntityType.DIFF_HUNK
            ):
                raise ValueError("Diff containment must be Commit CONTAINS DiffHunk")
        elif self.relation_type is CodeRelationType.AFFECTS:
            if self.side is None or self.match_id is None or self.change_role is None:
                raise ValueError("AFFECTS evidence requires side, role, and match_id")
            if self.source_type is not CodeGraphEntityType.DIFF_HUNK:
                raise ValueError("Diff symbol AFFECTS evidence must originate at DiffHunk")
            expected = (
                self.parent_commit_sha
                if self.side is DiffSymbolSide.OLD
                else self.target_commit_sha
            )
            if self.symbol_commit_sha != expected:
                raise ValueError("AFFECTS symbol version conflicts with side")
        else:
            raise ValueError("DiffSymbolEdge supports only registered CONTAINS/AFFECTS edges")
        if (self.overlap_start is None) != (self.overlap_end is None):
            raise ValueError("edge overlap start/end must both be present or absent")
        if self.overlap_start is not None:
            if self.overlap_start < 1 or self.overlap_end is None:
                raise ValueError("edge overlap lines must be positive")
            if self.overlap_end < self.overlap_start or self.overlap_line_count < 1:
                raise ValueError("edge overlap interval/count is inconsistent")
        elif self.overlap_line_count != 0:
            raise ValueError("edge without overlap interval must have zero overlap count")
        if not isinstance(self.provenance, DiffHunkProvenance):
            raise TypeError("edge provenance must be DiffHunkProvenance")
        edge_scope = (
            self.project_id,
            self.repository_id,
            self.target_commit_sha,
            self.parent_commit_sha,
            self.target_commit_sha,
            self.acl_ref,
        )
        if _provenance_scope(self.provenance) != edge_scope:
            raise ValueError("edge scope conflicts with provenance")


@dataclass(frozen=True, slots=True)
class DiffSymbolDiagnostic(_CanonicalContract):
    diagnostic_id: str
    project_id: str
    repository_id: str
    parent_commit_sha: str
    target_commit_sha: str
    acl_ref: str
    hunk_id: str
    code: DiffSymbolDiagnosticCode
    severity: DiffSymbolDiagnosticSeverity
    message: str
    side: DiffSymbolSide | None = None
    candidate_ids: tuple[str, ...] = ()
    contract_version: str = DIFF_SYMBOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "diagnostic_id",
            "project_id",
            "repository_id",
            "acl_ref",
            "hunk_id",
            "message",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("parent_commit_sha", "target_commit_sha"):
            object.__setattr__(self, name, _full_sha(getattr(self, name), name))
        if not isinstance(self.code, DiffSymbolDiagnosticCode):
            raise TypeError("code must be a DiffSymbolDiagnosticCode")
        if not isinstance(self.severity, DiffSymbolDiagnosticSeverity):
            raise TypeError("severity must be a DiffSymbolDiagnosticSeverity")
        if self.side is not None and not isinstance(self.side, DiffSymbolSide):
            raise TypeError("diagnostic side must be a DiffSymbolSide or None")
        object.__setattr__(
            self,
            "candidate_ids",
            _ordered_unique_text(self.candidate_ids, "candidate_id"),
        )


@dataclass(frozen=True, slots=True)
class DiffSymbolTrace(_CanonicalContract):
    project_id: str
    repository_id: str
    parent_commit_sha: str
    target_commit_sha: str
    parent_generation_id: str
    target_generation_id: str
    acl_ref: str
    hunk_id: str
    status: DiffSymbolTreatmentStatus
    parent_refs_seen: int
    target_refs_seen: int
    parent_exact_path_refs: int
    target_exact_path_refs: int
    confirmed_matches: int
    candidate_matches: int
    affects_edges: int
    diagnostics: int
    canonical_input_sha256: str
    mapper_version: str = DIFF_SYMBOL_MAPPER_VERSION
    contract_version: str = DIFF_SYMBOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "parent_generation_id",
            "target_generation_id",
            "acl_ref",
            "hunk_id",
            "canonical_input_sha256",
            "mapper_version",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("parent_commit_sha", "target_commit_sha"):
            object.__setattr__(self, name, _full_sha(getattr(self, name), name))
        for name in (
            "parent_refs_seen",
            "target_refs_seen",
            "parent_exact_path_refs",
            "target_exact_path_refs",
            "confirmed_matches",
            "candidate_matches",
            "affects_edges",
            "diagnostics",
        ):
            _non_negative_int(getattr(self, name), name)
        if not isinstance(self.status, DiffSymbolTreatmentStatus):
            raise TypeError("status must be a DiffSymbolTreatmentStatus")
        if not _SHA256.fullmatch(self.canonical_input_sha256):
            raise ValueError("canonical_input_sha256 must be canonical")


@dataclass(frozen=True, slots=True)
class DiffSymbolTreatment(_CanonicalContract):
    status: DiffSymbolTreatmentStatus
    hunk: DiffHunkVersion
    matches: tuple[DiffSymbolMatch, ...]
    edges: tuple[DiffSymbolEdge, ...]
    diagnostics: tuple[DiffSymbolDiagnostic, ...]
    confirmed_lineages: tuple[SymbolLineage, ...]
    trace: DiffSymbolTrace
    contract_version: str = DIFF_SYMBOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "contract_version", _text(self.contract_version, "contract_version")
        )
        object.__setattr__(self, "matches", tuple(self.matches))
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "confirmed_lineages", tuple(self.confirmed_lineages))
        for values, field_name, key in (
            (self.matches, "matches", lambda item: item.match_id),
            (self.edges, "edges", lambda item: item.edge_id),
            (self.diagnostics, "diagnostics", lambda item: item.diagnostic_id),
            (self.confirmed_lineages, "confirmed_lineages", lambda item: item.lineage_id),
        ):
            identities = tuple(key(item) for item in values)
            if len(identities) != len(set(identities)):
                raise ValueError(f"{field_name} cannot contain duplicates")
        if self.trace.status is not self.status:
            raise ValueError("treatment status conflicts with trace")
        scope = (
            self.hunk.project_id,
            self.hunk.repository_id,
            self.hunk.parent_commit_sha,
            self.hunk.target_commit_sha,
            self.hunk.acl_ref,
            self.hunk.hunk_id,
        )
        for item in (*self.matches, *self.edges, *self.diagnostics):
            actual = (
                item.project_id,
                item.repository_id,
                item.parent_commit_sha,
                item.target_commit_sha,
                item.acl_ref,
                item.hunk_id,
            )
            if actual != scope:
                raise ValueError("treatment evidence scope conflicts with hunk")
        trace_scope = (
            self.trace.project_id,
            self.trace.repository_id,
            self.trace.parent_commit_sha,
            self.trace.target_commit_sha,
            self.trace.acl_ref,
            self.trace.hunk_id,
        )
        if trace_scope != scope:
            raise ValueError("treatment trace scope conflicts with hunk")
        if any(item.patch_hash != self.hunk.patch_hash for item in self.edges):
            raise ValueError("treatment edge patch evidence conflicts with hunk")
        confirmed = sum(item.status is DiffSymbolMatchStatus.CONFIRMED for item in self.matches)
        candidates = sum(item.status is DiffSymbolMatchStatus.CANDIDATE for item in self.matches)
        affects = sum(item.relation_type is CodeRelationType.AFFECTS for item in self.edges)
        if (
            self.trace.confirmed_matches != confirmed
            or self.trace.candidate_matches != candidates
            or self.trace.affects_edges != affects
            or self.trace.diagnostics != len(self.diagnostics)
        ):
            raise ValueError("treatment accounting conflicts with trace")
        if any(item.status is not LineageStatus.CONFIRMED for item in self.confirmed_lineages):
            raise ValueError("only explicit C6-01 confirmed lineage may be cited")


@dataclass(frozen=True, slots=True)
class DiffSymbolEvaluation(_CanonicalContract):
    evaluation_id: str
    project_id: str
    repository_id: str
    parent_commit_sha: str
    target_commit_sha: str
    acl_ref: str
    hunk_id: str
    label: DiffSymbolEvaluationLabel
    match_id: str | None
    expected_symbol_version_id: str | None
    observed_symbol_version_id: str | None
    notes: str | None = None
    evaluation_version: str = DIFF_SYMBOL_EVALUATION_VERSION
    contract_version: str = DIFF_SYMBOL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "evaluation_id",
            "project_id",
            "repository_id",
            "acl_ref",
            "hunk_id",
            "evaluation_version",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("parent_commit_sha", "target_commit_sha"):
            object.__setattr__(self, name, _full_sha(getattr(self, name), name))
        if not isinstance(self.label, DiffSymbolEvaluationLabel):
            raise TypeError("label must be a DiffSymbolEvaluationLabel")
        for name in (
            "match_id",
            "expected_symbol_version_id",
            "observed_symbol_version_id",
            "notes",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class _PatchLines:
    old_lines: tuple[int, ...]
    new_lines: tuple[int, ...]
    removed: tuple[str, ...]
    added: tuple[str, ...]
    parsed: bool


def _parse_patch_lines(hunk: DiffHunkVersion) -> _PatchLines:
    old_cursor = hunk.old_start
    new_cursor = hunk.new_start
    old_lines: list[int] = []
    new_lines: list[int] = []
    removed: list[str] = []
    added: list[str] = []
    parsed = False
    in_hunk = False
    for line in hunk.patch.splitlines():
        header = _HUNK_HEADER.match(line)
        if header:
            old_cursor = int(header.group("old_start"))
            new_cursor = int(header.group("new_start"))
            in_hunk = True
            parsed = True
            continue
        if not in_hunk and line.startswith(("diff ", "index ", "--- ", "+++ ")):
            continue
        if not in_hunk:
            in_hunk = True
        if line.startswith("\\"):
            continue
        if line.startswith("-") and not line.startswith("---"):
            if old_cursor > 0:
                old_lines.append(old_cursor)
            removed.append(line[1:])
            old_cursor += 1
            parsed = True
        elif line.startswith("+") and not line.startswith("+++"):
            if new_cursor > 0:
                new_lines.append(new_cursor)
            added.append(line[1:])
            new_cursor += 1
            parsed = True
        elif line.startswith(" "):
            old_cursor += 1
            new_cursor += 1
    return _PatchLines(
        old_lines=tuple(sorted(set(old_lines))),
        new_lines=tuple(sorted(set(new_lines))),
        removed=tuple(removed),
        added=tuple(added),
        parsed=parsed,
    )


def _whitespace_only(lines: _PatchLines) -> bool:
    payload = lines.removed + lines.added
    if not payload:
        return False
    normalized_removed = tuple("".join(item.split()) for item in lines.removed)
    normalized_added = tuple("".join(item.split()) for item in lines.added)
    if normalized_removed and normalized_added:
        return normalized_removed == normalized_added
    return all(not item for item in (normalized_removed + normalized_added))


def _diagnostic(
    hunk: DiffHunkVersion,
    *,
    code: DiffSymbolDiagnosticCode,
    severity: DiffSymbolDiagnosticSeverity,
    message: str,
    side: DiffSymbolSide | None = None,
    candidate_ids: Iterable[str] = (),
) -> DiffSymbolDiagnostic:
    candidates = tuple(sorted(set(candidate_ids)))
    payload = {
        "candidate_ids": candidates,
        "code": code.value,
        "hunk_id": hunk.hunk_id,
        "message": message,
        "side": side.value if side is not None else None,
    }
    return DiffSymbolDiagnostic(
        diagnostic_id="diff-symbol-diagnostic://sha256/" + _sha256(payload).removeprefix("sha256:"),
        project_id=hunk.project_id,
        repository_id=hunk.repository_id,
        parent_commit_sha=hunk.parent_commit_sha,
        target_commit_sha=hunk.target_commit_sha,
        acl_ref=hunk.acl_ref,
        hunk_id=hunk.hunk_id,
        code=code,
        severity=severity,
        message=message,
        side=side,
        candidate_ids=candidates,
    )


def _change_role(change_type: DiffChangeType, side: DiffSymbolSide) -> str:
    if change_type is DiffChangeType.ADD:
        return "introduced"
    if change_type is DiffChangeType.DELETE:
        return "removed"
    if change_type is DiffChangeType.RENAME:
        return "renamed"
    return "modified"


def _is_ast_ref(ref: SymbolVersionRef) -> bool:
    return ref.is_symbol and ref.unit_type == "symbol.ast_block" and bool(ref.ast_node_type)


def _ref_rank(ref: SymbolVersionRef) -> tuple[int, int, int, int]:
    kind_rank = {
        "function": 0,
        "method": 0,
        "test": 0,
        "class": 1,
        "interface": 1,
        "trait": 1,
        "enum": 1,
    }.get(ref.symbol_kind, 2)
    return (
        1 if not ref.is_symbol else 0,
        0 if _is_ast_ref(ref) else 1,
        ref.end_line - ref.start_line,
        kind_rank,
    )


def _match_kind(
    ref: SymbolVersionRef,
    *,
    changed_lines: tuple[int, ...],
    line_only: bool,
) -> DiffSymbolMatchKind:
    if not ref.is_symbol:
        return DiffSymbolMatchKind.FILE_TOP_LEVEL
    if line_only or not _is_ast_ref(ref):
        return DiffSymbolMatchKind.LINE_ONLY
    if ref.signature is not None and ref.start_line in changed_lines:
        return DiffSymbolMatchKind.SIGNATURE
    if ref.symbol_kind in {"class", "interface", "trait", "enum"}:
        return DiffSymbolMatchKind.CLASS_LEVEL
    return DiffSymbolMatchKind.FUNCTION_BODY


def _match_signals(
    hunk: DiffHunkVersion,
    ref: SymbolVersionRef,
    *,
    changed_lines: tuple[int, ...],
    parsed_lines: bool,
    zero_boundary: bool,
    line_only: bool,
) -> tuple[DiffSymbolSignal, ...]:
    signals: set[DiffSymbolSignal] = set()
    if parsed_lines and not zero_boundary:
        signals.add(DiffSymbolSignal.EXACT_OVERLAP)
    if _is_ast_ref(ref):
        signals.add(DiffSymbolSignal.AST_ENCLOSING)
    if ref.signature is not None and ref.start_line in changed_lines:
        signals.add(DiffSymbolSignal.SIGNATURE_LINE)
    if hunk.change_type is DiffChangeType.RENAME:
        signals.add(DiffSymbolSignal.RENAME_PATH)
    if zero_boundary:
        signals.add(DiffSymbolSignal.ZERO_COUNT_BOUNDARY)
    if line_only or not _is_ast_ref(ref):
        signals.add(DiffSymbolSignal.LINE_ONLY)
    if not ref.is_symbol:
        signals.add(DiffSymbolSignal.FILE_TOP_LEVEL)
    return tuple(sorted(signals, key=lambda item: item.value))


def _confidence(
    ref: SymbolVersionRef,
    *,
    kind: DiffSymbolMatchKind,
    zero_boundary: bool,
    parsed_lines: bool,
) -> float:
    if kind is DiffSymbolMatchKind.FILE_TOP_LEVEL:
        value = 0.6
    elif kind is DiffSymbolMatchKind.LINE_ONLY:
        value = 0.72
    elif kind is DiffSymbolMatchKind.SIGNATURE:
        value = 1.0
    elif _is_ast_ref(ref):
        value = 0.98
    else:
        value = 0.82
    if zero_boundary:
        value = min(value, 0.84)
    if not parsed_lines:
        value = min(value, 0.72)
    return float(value)


def _match_identity(
    hunk: DiffHunkVersion,
    side: DiffSymbolSide,
    ref: SymbolVersionRef,
    status: DiffSymbolMatchStatus,
    changed_lines: tuple[int, ...],
) -> str:
    payload = {
        "changed_lines": changed_lines,
        "hunk_id": hunk.hunk_id,
        "side": side.value,
        "status": status.value,
        "symbol_version_id": ref.symbol_version_id,
    }
    return "diff-symbol-match://sha256/" + _sha256(payload).removeprefix("sha256:")


def _build_match(
    hunk: DiffHunkVersion,
    *,
    side: DiffSymbolSide,
    ref: SymbolVersionRef,
    status: DiffSymbolMatchStatus,
    changed_lines: tuple[int, ...],
    range_start: int,
    range_count: int,
    parsed_lines: bool,
    zero_boundary: bool,
    line_only: bool,
) -> DiffSymbolMatch:
    kind = _match_kind(ref, changed_lines=changed_lines, line_only=line_only)
    signals = _match_signals(
        hunk,
        ref,
        changed_lines=changed_lines,
        parsed_lines=parsed_lines,
        zero_boundary=zero_boundary,
        line_only=line_only,
    )
    overlap_lines = (
        tuple(line for line in changed_lines if ref.start_line <= line <= ref.end_line)
        if not zero_boundary
        else ()
    )
    review_required = status is DiffSymbolMatchStatus.CANDIDATE
    explanation = (
        f"{side.value} lines map to exact {ref.entity_type.value} interval "
        f"{ref.start_line}-{ref.end_line}"
        if not review_required
        else (
            f"{side.value} lines overlap equally ranked versioned targets; "
            "candidate requires review"
        )
    )
    return DiffSymbolMatch(
        match_id=_match_identity(hunk, side, ref, status, changed_lines),
        project_id=hunk.project_id,
        repository_id=hunk.repository_id,
        parent_commit_sha=hunk.parent_commit_sha,
        target_commit_sha=hunk.target_commit_sha,
        acl_ref=hunk.acl_ref,
        hunk_id=hunk.hunk_id,
        side=side,
        change_role=_change_role(hunk.change_type, side),
        symbol=ref,
        status=status,
        match_kind=kind,
        confidence=_confidence(
            ref,
            kind=kind,
            zero_boundary=zero_boundary,
            parsed_lines=parsed_lines,
        ),
        range_start=range_start,
        range_count=range_count,
        changed_lines=changed_lines,
        overlap_start=min(overlap_lines) if overlap_lines else None,
        overlap_end=max(overlap_lines) if overlap_lines else None,
        overlap_line_count=len(overlap_lines),
        signals=signals,
        review_required=review_required,
        evidence_locator=hunk.provenance.evidence_locator,
        explanation=explanation,
    )


def _scope_ref(
    hunk: DiffHunkVersion,
    ref: SymbolVersionRef,
    *,
    side: DiffSymbolSide,
) -> None:
    expected_commit = (
        hunk.parent_commit_sha if side is DiffSymbolSide.OLD else hunk.target_commit_sha
    )
    expected_generation = (
        hunk.parent_generation_id if side is DiffSymbolSide.OLD else hunk.target_generation_id
    )
    actual = (
        ref.project_id,
        ref.repository_id,
        ref.commit_sha,
        ref.generation_id,
        ref.acl_ref,
    )
    expected = (
        hunk.project_id,
        hunk.repository_id,
        expected_commit,
        expected_generation,
        hunk.acl_ref,
    )
    if actual != expected:
        raise DiffSymbolScopeError(
            f"{side.value} symbol scope/version conflicts with DiffHunkVersion"
        )


def _normalize_refs(
    hunk: DiffHunkVersion,
    values: Iterable[HistoricalUnit | SymbolVersionRef],
    *,
    side: DiffSymbolSide,
) -> tuple[SymbolVersionRef, ...]:
    by_id: dict[str, SymbolVersionRef] = {}
    for value in values:
        if isinstance(value, HistoricalUnit):
            if value.unit_type not in {"symbol.ast_block", "file.surface"}:
                continue
            if value.unit_type == "symbol.ast_block" and not value.is_symbol:
                continue
            ref = SymbolVersionRef.from_historical_unit(value)
        elif isinstance(value, SymbolVersionRef):
            ref = value
        else:
            raise TypeError("symbol inputs must be HistoricalUnit or SymbolVersionRef")
        _scope_ref(hunk, ref, side=side)
        previous = by_id.get(ref.symbol_version_id)
        if previous is not None and previous != ref:
            raise DiffSymbolScopeError("duplicate symbol_version_id has conflicting evidence")
        by_id[ref.symbol_version_id] = ref
    return tuple(
        sorted(
            by_id.values(),
            key=lambda item: (
                item.path,
                item.start_line,
                item.end_line,
                item.entity_type.value,
                item.qualified_name,
                item.symbol_version_id,
            ),
        )
    )


def _side_lines(
    hunk: DiffHunkVersion,
    parsed: _PatchLines,
    *,
    side: DiffSymbolSide,
) -> tuple[tuple[int, ...], int, int, bool, bool]:
    if side is DiffSymbolSide.OLD:
        lines = parsed.old_lines
        start = hunk.old_start
        count = hunk.old_count
    else:
        lines = parsed.new_lines
        start = hunk.new_start
        count = hunk.new_count
    zero_boundary = count == 0
    if lines:
        return lines, start, count, True, zero_boundary
    if count > 0:
        return tuple(range(max(1, start), max(1, start) + count)), start, count, False, False
    return (max(1, start),), start, count, False, True


def _map_side(
    hunk: DiffHunkVersion,
    refs: tuple[SymbolVersionRef, ...],
    parsed: _PatchLines,
    *,
    side: DiffSymbolSide,
    expected_path: str,
) -> tuple[tuple[DiffSymbolMatch, ...], tuple[DiffSymbolDiagnostic, ...], int]:
    exact_refs = tuple(item for item in refs if item.path == expected_path)
    diagnostics: list[DiffSymbolDiagnostic] = []
    if not exact_refs:
        diagnostics.append(
            _diagnostic(
                hunk,
                code=DiffSymbolDiagnosticCode.NO_EXACT_PATH,
                severity=DiffSymbolDiagnosticSeverity.WARNING,
                message=f"no {side.value} version refs match exact path {expected_path}",
                side=side,
            )
        )
        return (), tuple(diagnostics), 0
    changed_lines, range_start, range_count, parsed_lines, zero_boundary = _side_lines(
        hunk,
        parsed,
        side=side,
    )
    confirmed_coverage: dict[str, set[int]] = {}
    ambiguous_coverage: dict[str, set[int]] = {}
    refs_by_id = {item.symbol_version_id: item for item in exact_refs}
    for line in changed_lines:
        containing = tuple(item for item in exact_refs if item.start_line <= line <= item.end_line)
        if not containing:
            continue
        best_rank = min(_ref_rank(item) for item in containing)
        best = tuple(item for item in containing if _ref_rank(item) == best_rank)
        target = confirmed_coverage if len(best) == 1 else ambiguous_coverage
        for item in best:
            target.setdefault(item.symbol_version_id, set()).add(line)
    matches: list[DiffSymbolMatch] = []
    line_only = not parsed_lines
    for symbol_id, covered in sorted(confirmed_coverage.items()):
        ref = refs_by_id[symbol_id]
        matches.append(
            _build_match(
                hunk,
                side=side,
                ref=ref,
                status=DiffSymbolMatchStatus.CONFIRMED,
                changed_lines=tuple(sorted(covered)),
                range_start=range_start,
                range_count=range_count,
                parsed_lines=parsed_lines,
                zero_boundary=zero_boundary,
                line_only=line_only,
            )
        )
    ambiguous_ids = tuple(
        symbol_id for symbol_id in sorted(ambiguous_coverage) if symbol_id not in confirmed_coverage
    )
    for symbol_id in ambiguous_ids:
        ref = refs_by_id[symbol_id]
        matches.append(
            _build_match(
                hunk,
                side=side,
                ref=ref,
                status=DiffSymbolMatchStatus.CANDIDATE,
                changed_lines=tuple(sorted(ambiguous_coverage[symbol_id])),
                range_start=range_start,
                range_count=range_count,
                parsed_lines=parsed_lines,
                zero_boundary=zero_boundary,
                line_only=line_only,
            )
        )
    if ambiguous_ids:
        diagnostics.append(
            _diagnostic(
                hunk,
                code=DiffSymbolDiagnosticCode.AMBIGUOUS_SYMBOLS,
                severity=DiffSymbolDiagnosticSeverity.WARNING,
                message=(f"{side.value} lines have multiple equally specific versioned targets"),
                side=side,
                candidate_ids=ambiguous_ids,
            )
        )
    if not matches:
        diagnostics.append(
            _diagnostic(
                hunk,
                code=DiffSymbolDiagnosticCode.NO_SYMBOL_MATCH,
                severity=DiffSymbolDiagnosticSeverity.WARNING,
                message=f"{side.value} changed range has no enclosing exact-path target",
                side=side,
            )
        )
    if line_only and matches:
        diagnostics.append(
            _diagnostic(
                hunk,
                code=DiffSymbolDiagnosticCode.LINE_ONLY,
                severity=DiffSymbolDiagnosticSeverity.INFO,
                message=f"{side.value} mapping used declared range because patch lines were absent",
                side=side,
            )
        )
    if any(item.match_kind is DiffSymbolMatchKind.FILE_TOP_LEVEL for item in matches):
        diagnostics.append(
            _diagnostic(
                hunk,
                code=DiffSymbolDiagnosticCode.FILE_TOP_LEVEL,
                severity=DiffSymbolDiagnosticSeverity.INFO,
                message=(
                    f"{side.value} change maps to FileVersion top-level evidence, not CodeSymbol"
                ),
                side=side,
            )
        )
    return (
        tuple(
            sorted(
                matches,
                key=lambda item: (
                    item.side.value,
                    item.status.value,
                    -item.confidence,
                    item.symbol.start_line,
                    item.symbol.end_line,
                    item.symbol.symbol_version_id,
                ),
            )
        ),
        tuple(sorted(diagnostics, key=lambda item: item.diagnostic_id)),
        len(exact_refs),
    )


def _edge_id(payload: Mapping[str, Any]) -> str:
    return "diff-symbol-edge://sha256/" + _sha256(payload).removeprefix("sha256:")


def _containment_edge(hunk: DiffHunkVersion) -> DiffSymbolEdge:
    commit_id = f"commit://{hunk.repository_id}@{hunk.target_commit_sha}"
    payload = {
        "hunk_id": hunk.hunk_id,
        "relation_type": CodeRelationType.CONTAINS.value,
        "source_id": commit_id,
        "target_id": hunk.hunk_id,
    }
    return DiffSymbolEdge(
        edge_id=_edge_id(payload),
        project_id=hunk.project_id,
        repository_id=hunk.repository_id,
        parent_commit_sha=hunk.parent_commit_sha,
        target_commit_sha=hunk.target_commit_sha,
        acl_ref=hunk.acl_ref,
        relation_type=CodeRelationType.CONTAINS,
        source_id=commit_id,
        source_type=CodeGraphEntityType.COMMIT,
        target_id=hunk.hunk_id,
        target_type=CodeGraphEntityType.DIFF_HUNK,
        hunk_id=hunk.hunk_id,
        side=None,
        change_role=None,
        symbol_commit_sha=None,
        range_start=None,
        range_count=None,
        overlap_start=None,
        overlap_end=None,
        overlap_line_count=0,
        confidence=1.0,
        evidence_locator=hunk.provenance.evidence_locator,
        patch_hash=hunk.patch_hash,
        match_id=None,
        signals=(),
        provenance=hunk.provenance,
    )


def _affects_edge(hunk: DiffHunkVersion, match: DiffSymbolMatch) -> DiffSymbolEdge:
    payload = {
        "hunk_id": hunk.hunk_id,
        "match_id": match.match_id,
        "relation_type": CodeRelationType.AFFECTS.value,
        "side": match.side.value,
        "target_id": match.symbol.symbol_version_id,
    }
    return DiffSymbolEdge(
        edge_id=_edge_id(payload),
        project_id=hunk.project_id,
        repository_id=hunk.repository_id,
        parent_commit_sha=hunk.parent_commit_sha,
        target_commit_sha=hunk.target_commit_sha,
        acl_ref=hunk.acl_ref,
        relation_type=CodeRelationType.AFFECTS,
        source_id=hunk.hunk_id,
        source_type=CodeGraphEntityType.DIFF_HUNK,
        target_id=match.symbol.symbol_version_id,
        target_type=match.symbol.entity_type,
        hunk_id=hunk.hunk_id,
        side=match.side,
        change_role=match.change_role,
        symbol_commit_sha=match.symbol.commit_sha,
        range_start=match.range_start,
        range_count=match.range_count,
        overlap_start=match.overlap_start,
        overlap_end=match.overlap_end,
        overlap_line_count=match.overlap_line_count,
        confidence=match.confidence,
        evidence_locator=match.evidence_locator,
        patch_hash=hunk.patch_hash,
        match_id=match.match_id,
        signals=match.signals,
        provenance=hunk.provenance,
    )


def _scope_lineage(hunk: DiffHunkVersion, lineage: SymbolLineage) -> None:
    actual = (
        lineage.project_id,
        lineage.repository_id,
        lineage.source_commit_sha,
        lineage.target_commit_sha,
        lineage.source_generation_id,
        lineage.target_generation_id,
        lineage.acl_ref,
        lineage.status,
        lineage.review_required,
    )
    expected = (
        hunk.project_id,
        hunk.repository_id,
        hunk.parent_commit_sha,
        hunk.target_commit_sha,
        hunk.parent_generation_id,
        hunk.target_generation_id,
        hunk.acl_ref,
        LineageStatus.CONFIRMED,
        False,
    )
    if actual != expected:
        raise DiffSymbolScopeError("confirmed lineage scope/version conflicts with hunk")


class DiffSymbolMapper:
    """Map one immutable diff hunk to exact old/new versioned symbol evidence."""

    mapper_version = DIFF_SYMBOL_MAPPER_VERSION

    def map_hunk(
        self,
        hunk: DiffHunkVersion,
        *,
        parent_symbols: Iterable[HistoricalUnit | SymbolVersionRef] = (),
        target_symbols: Iterable[HistoricalUnit | SymbolVersionRef] = (),
        confirmed_lineages: Iterable[SymbolLineage] = (),
    ) -> DiffSymbolTreatment:
        if not isinstance(hunk, DiffHunkVersion):
            raise TypeError("hunk must be a DiffHunkVersion")
        parent_refs = _normalize_refs(
            hunk,
            parent_symbols,
            side=DiffSymbolSide.OLD,
        )
        target_refs = _normalize_refs(
            hunk,
            target_symbols,
            side=DiffSymbolSide.NEW,
        )
        lineage_values = tuple(
            sorted(confirmed_lineages, key=lambda item: getattr(item, "lineage_id", ""))
        )
        for lineage in lineage_values:
            if not isinstance(lineage, SymbolLineage):
                raise TypeError("confirmed_lineages must contain SymbolLineage")
            _scope_lineage(hunk, lineage)
        parsed = _parse_patch_lines(hunk)
        diagnostics: list[DiffSymbolDiagnostic] = []
        matches: tuple[DiffSymbolMatch, ...] = ()
        parent_exact = 0
        target_exact = 0
        status: DiffSymbolTreatmentStatus
        if hunk.is_binary or "Binary files " in hunk.patch:
            diagnostics.append(
                _diagnostic(
                    hunk,
                    code=DiffSymbolDiagnosticCode.BINARY_UNAVAILABLE,
                    severity=DiffSymbolDiagnosticSeverity.WARNING,
                    message="binary diff has no honest line-to-symbol mapping",
                )
            )
            status = DiffSymbolTreatmentStatus.UNAVAILABLE
        elif _whitespace_only(parsed):
            diagnostics.append(
                _diagnostic(
                    hunk,
                    code=DiffSymbolDiagnosticCode.WHITESPACE_ONLY_SKIPPED,
                    severity=DiffSymbolDiagnosticSeverity.INFO,
                    message="whitespace-only hunk is intentionally not asserted as symbol impact",
                )
            )
            status = DiffSymbolTreatmentStatus.SKIPPED
        else:
            old_missing = hunk.change_type is DiffChangeType.ADD
            new_missing = hunk.change_type is DiffChangeType.DELETE
            side_matches: list[DiffSymbolMatch] = []
            if old_missing:
                diagnostics.append(
                    _diagnostic(
                        hunk,
                        code=DiffSymbolDiagnosticCode.MISSING_OLD_SIDE,
                        severity=DiffSymbolDiagnosticSeverity.INFO,
                        message="added hunk has no parent-side symbol version",
                        side=DiffSymbolSide.OLD,
                    )
                )
            else:
                old_path = hunk.old_path or hunk.path
                old_matches, old_diagnostics, parent_exact = _map_side(
                    hunk,
                    parent_refs,
                    parsed,
                    side=DiffSymbolSide.OLD,
                    expected_path=old_path,
                )
                side_matches.extend(old_matches)
                diagnostics.extend(old_diagnostics)
            if new_missing:
                diagnostics.append(
                    _diagnostic(
                        hunk,
                        code=DiffSymbolDiagnosticCode.MISSING_NEW_SIDE,
                        severity=DiffSymbolDiagnosticSeverity.INFO,
                        message="deleted hunk has no target-side symbol version",
                        side=DiffSymbolSide.NEW,
                    )
                )
            else:
                new_matches, new_diagnostics, target_exact = _map_side(
                    hunk,
                    target_refs,
                    parsed,
                    side=DiffSymbolSide.NEW,
                    expected_path=hunk.path,
                )
                side_matches.extend(new_matches)
                diagnostics.extend(new_diagnostics)
            matches = tuple(
                sorted(
                    side_matches,
                    key=lambda item: (
                        item.side.value,
                        item.status.value,
                        item.symbol.start_line,
                        item.symbol.end_line,
                        item.symbol.symbol_version_id,
                    ),
                )
            )
            confirmed_sides = {
                item.side for item in matches if item.status is DiffSymbolMatchStatus.CONFIRMED
            }
            required_sides = {
                side
                for side, missing in (
                    (DiffSymbolSide.OLD, old_missing),
                    (DiffSymbolSide.NEW, new_missing),
                )
                if not missing
            }
            has_candidates = any(item.status is DiffSymbolMatchStatus.CANDIDATE for item in matches)
            if required_sides and confirmed_sides == required_sides and not has_candidates:
                status = DiffSymbolTreatmentStatus.MAPPED
            elif matches:
                status = DiffSymbolTreatmentStatus.PARTIAL
            else:
                status = DiffSymbolTreatmentStatus.NO_MATCH
            if not parsed.parsed and not matches:
                diagnostics.append(
                    _diagnostic(
                        hunk,
                        code=DiffSymbolDiagnosticCode.NO_CHANGED_LINES,
                        severity=DiffSymbolDiagnosticSeverity.WARNING,
                        message="patch carried no parseable changed lines or enclosing range match",
                    )
                )
        edges: list[DiffSymbolEdge] = [_containment_edge(hunk)]
        edges.extend(
            _affects_edge(hunk, match)
            for match in matches
            if match.status is DiffSymbolMatchStatus.CONFIRMED
        )
        confirmed_old_units = {
            item.symbol.unit_id
            for item in matches
            if item.side is DiffSymbolSide.OLD and item.status is DiffSymbolMatchStatus.CONFIRMED
        }
        confirmed_new_units = {
            item.symbol.unit_id
            for item in matches
            if item.side is DiffSymbolSide.NEW and item.status is DiffSymbolMatchStatus.CONFIRMED
        }
        cited_lineages = tuple(
            sorted(
                (
                    lineage
                    for lineage in lineage_values
                    if lineage.source_unit_id in confirmed_old_units
                    and lineage.target_unit_id in confirmed_new_units
                ),
                key=lambda item: item.lineage_id,
            )
        )
        matches = tuple(sorted(matches, key=lambda item: item.match_id))
        edges_tuple = tuple(
            sorted({item.edge_id: item for item in edges}.values(), key=lambda x: x.edge_id)
        )
        diagnostics_tuple = tuple(
            sorted(
                {item.diagnostic_id: item for item in diagnostics}.values(),
                key=lambda item: item.diagnostic_id,
            )
        )
        canonical_input_sha256 = _sha256(
            {
                "confirmed_lineages": [item.lineage_id for item in lineage_values],
                "hunk": asdict(hunk),
                "mapper_version": self.mapper_version,
                "parent_refs": [asdict(item) for item in parent_refs],
                "target_refs": [asdict(item) for item in target_refs],
            }
        )
        trace = DiffSymbolTrace(
            project_id=hunk.project_id,
            repository_id=hunk.repository_id,
            parent_commit_sha=hunk.parent_commit_sha,
            target_commit_sha=hunk.target_commit_sha,
            parent_generation_id=hunk.parent_generation_id,
            target_generation_id=hunk.target_generation_id,
            acl_ref=hunk.acl_ref,
            hunk_id=hunk.hunk_id,
            status=status,
            parent_refs_seen=len(parent_refs),
            target_refs_seen=len(target_refs),
            parent_exact_path_refs=parent_exact,
            target_exact_path_refs=target_exact,
            confirmed_matches=sum(
                item.status is DiffSymbolMatchStatus.CONFIRMED for item in matches
            ),
            candidate_matches=sum(
                item.status is DiffSymbolMatchStatus.CANDIDATE for item in matches
            ),
            affects_edges=sum(
                item.relation_type is CodeRelationType.AFFECTS for item in edges_tuple
            ),
            diagnostics=len(diagnostics_tuple),
            canonical_input_sha256=canonical_input_sha256,
        )
        return DiffSymbolTreatment(
            status=status,
            hunk=hunk,
            matches=matches,
            edges=edges_tuple,
            diagnostics=diagnostics_tuple,
            confirmed_lineages=cited_lineages,
            trace=trace,
        )

    def map(
        self,
        hunk: DiffHunkVersion,
        *,
        old_symbols: Iterable[HistoricalUnit | SymbolVersionRef] = (),
        new_symbols: Iterable[HistoricalUnit | SymbolVersionRef] = (),
        confirmed_lineages: Iterable[SymbolLineage] = (),
    ) -> DiffSymbolTreatment:
        """Compatibility spelling using diff old/new terminology."""

        return self.map_hunk(
            hunk,
            parent_symbols=old_symbols,
            target_symbols=new_symbols,
            confirmed_lineages=confirmed_lineages,
        )


__all__ = [
    "DIFF_SYMBOL_CONTRACT_VERSION",
    "DIFF_SYMBOL_EVALUATION_VERSION",
    "DIFF_SYMBOL_MAPPER_VERSION",
    "DiffChangeType",
    "DiffHunkProvenance",
    "DiffHunkVersion",
    "DiffSymbolDiagnostic",
    "DiffSymbolDiagnosticCode",
    "DiffSymbolDiagnosticSeverity",
    "DiffSymbolEdge",
    "DiffSymbolError",
    "DiffSymbolEvaluation",
    "DiffSymbolEvaluationLabel",
    "DiffSymbolMapper",
    "DiffSymbolMatch",
    "DiffSymbolMatchKind",
    "DiffSymbolMatchStatus",
    "DiffSymbolScopeError",
    "DiffSymbolSide",
    "DiffSymbolSignal",
    "DiffSymbolTrace",
    "DiffSymbolTreatment",
    "DiffSymbolTreatmentStatus",
    "SymbolVersionRef",
]
