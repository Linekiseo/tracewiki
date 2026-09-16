"""Exact, on-demand historical Code materialization and symbol lineage.

This module is intentionally isolated from the application runtime and the
production database.  It reads immutable Git objects, reuses the Code parser
and C2 unit builder, and publishes complete historical snapshots through an
injectable store boundary.  The bundled SQLite store refuses non-temporary
database paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

from ....models import EvidenceSearchRequest
from ....parser import STRUCTURED_LANGUAGES, CodeParser, language_for_path
from .contracts import (
    CodeCandidateRole,
    CodeChannelRank,
    CodeChannelScore,
    CodeDerivation,
    CodeFactStatus,
    CodeRelationType,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
)
from .query_profile_v2 import (
    CodeOptionalHookResult,
    CodeOptionalHookStatus,
)
from .unit_builder import BUILDER_VERSION, CodeUnitBuilder, CodeUnitBuildRequest

HISTORY_CONTRACT_VERSION = "c6-history-materialization-v1"
HISTORY_SCHEMA_VERSION = "c6-history-publication-schema-v1"
LINEAGE_CONTRACT_VERSION = "c6-symbol-lineage-v1"
DEFAULT_HISTORY_PROFILE = "ast-symbols-v1"

_FULL_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SHA256_IDENTITY = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIRTY_VERSION = re.compile(
    r"^(?P<base>[0-9a-f]{40}(?:[0-9a-f]{24})?)"
    r"\+dirty\.(?P<manifest>[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?)$"
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SYMBOL_TOKEN = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_REF_DISALLOWED = frozenset({"HEAD", "current", "dirty", "worktree"})


class HistoryError(ValueError):
    """Base fail-closed history error."""


class HistoryScopeError(HistoryError):
    """Requested scope does not match governed repository/version/ACL data."""


class HistoryGitError(HistoryError):
    """A bounded read-only Git command failed."""


class HistoryPublicationError(RuntimeError):
    """A publication could not be atomically stored."""


class HistoryNamespaceKind(StrEnum):
    CURRENT = "current"
    HISTORY = "history"


class HistoryMaterializationStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    EMPTY = "empty"
    CACHE_HIT = "cache_hit"
    FAILED = "failed"


HistoryStatus = HistoryMaterializationStatus


class HistoricalFileStatus(StrEnum):
    MATERIALIZED = "materialized"
    SKIPPED_BINARY = "skipped_binary"
    SKIPPED_OVERSIZE = "skipped_oversize"
    SKIPPED_UNSUPPORTED = "skipped_unsupported"
    SKIPPED_INVALID_TEXT = "skipped_invalid_text"
    FAILED = "failed"


class HistoryDiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class HistoryDiagnosticCode(StrEnum):
    CACHE_HIT = "cache_hit"
    PATH_NOT_FOUND = "path_not_found"
    BINARY_SKIPPED = "binary_skipped"
    OVERSIZE_SKIPPED = "oversize_skipped"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    INVALID_UTF8 = "invalid_utf8"
    PARSE_BUILD_FAILED = "parse_build_failed"
    GIT_FAILED = "git_failed"
    SCOPE_MISMATCH = "scope_mismatch"
    NO_SYMBOLS = "no_symbols"
    AMBIGUOUS_LINEAGE = "ambiguous_lineage"
    REVIEW_REQUIRED = "review_required"


class HistoryPinReason(StrEnum):
    EXPLICIT = "explicit"
    REFERENCED = "referenced"
    HOT = "hot"


class LineageRelationType(StrEnum):
    SAME_SYMBOL_AS = CodeRelationType.SAME_SYMBOL_AS.value
    RENAMED_TO = CodeRelationType.RENAMED_TO.value
    MOVED_TO = CodeRelationType.MOVED_TO.value
    SPLIT_INTO = "SPLIT_INTO"
    MERGED_FROM = "MERGED_FROM"


class LineageStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANDIDATE = "candidate"
    REVIEW_REQUIRED = "review_required"
    NO_MATCH = "no_match"
    FAILED = "failed"


class LineageDiagnosticCode(StrEnum):
    NO_SOURCE_SYMBOLS = "no_source_symbols"
    NO_TARGET_SYMBOLS = "no_target_symbols"
    NO_MATCH = "no_match"
    AMBIGUOUS_SPLIT = "ambiguous_split"
    AMBIGUOUS_MERGE = "ambiguous_merge"
    SCOPE_MISMATCH = "scope_mismatch"


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


def _full_sha(value: object, field_name: str = "commit_sha") -> str:
    sha = _text(value, field_name).lower()
    if not _FULL_SHA.fullmatch(sha):
        raise ValueError(f"{field_name} must be a full Git commit SHA")
    return sha


def _sha256_identity(value: object, field_name: str) -> str:
    identity = _text(value, field_name).lower()
    if not _SHA256_IDENTITY.fullmatch(identity):
        raise ValueError(f"{field_name} must be a canonical sha256 identity")
    return identity


def _safe_path(value: object) -> str:
    path = _text(value, "path")
    if "\\" in path:
        raise ValueError("path must use POSIX separators")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ValueError("path must be a safe repository-relative path")
    normalized = candidate.as_posix()
    if normalized in {".", ""} or normalized.startswith("./"):
        raise ValueError("path must identify a repository-relative file or directory")
    return normalized


def _ordered_text(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} values must be unique")
    return tuple(sorted(normalized))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _namespace_segment(value: str) -> str:
    return quote(value, safe="._-")


@dataclass(frozen=True, slots=True)
class HistoryDiagnostic:
    code: HistoryDiagnosticCode
    severity: HistoryDiagnosticSeverity
    message: str
    path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", _text(self.message, "diagnostic message"))
        if self.path is not None:
            object.__setattr__(self, "path", _safe_path(self.path))


@dataclass(frozen=True, slots=True)
class LineageDiagnostic:
    code: LineageDiagnosticCode
    severity: HistoryDiagnosticSeverity
    message: str
    source_unit_id: str | None = None
    target_unit_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", _text(self.message, "diagnostic message"))
        for name in ("source_unit_id", "target_unit_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))


@dataclass(frozen=True, slots=True)
class HistoryMaterializationPolicy:
    policy_version: str = "c6-history-policy-v1"
    max_file_bytes: int = 1_000_000
    max_git_output_bytes: int = 8_000_000
    git_timeout_seconds: float = 10.0
    supported_languages: tuple[str, ...] = tuple(sorted(STRUCTURED_LANGUAGES))
    ttl_seconds: float | None = 7 * 24 * 60 * 60
    max_historical_publications: int = 32
    hot_access_threshold: int = 3
    retain_explicit_pins: bool = True
    retain_referenced_pins: bool = True
    retain_hot_publications: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version"))
        for name in (
            "max_file_bytes",
            "max_git_output_bytes",
            "max_historical_publications",
            "hot_access_threshold",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.git_timeout_seconds, (int, float)):
            raise TypeError("git_timeout_seconds must be numeric")
        if self.git_timeout_seconds <= 0 or self.git_timeout_seconds > 120:
            raise ValueError("git_timeout_seconds must be in (0, 120]")
        if self.ttl_seconds is not None and (
            not isinstance(self.ttl_seconds, (int, float)) or self.ttl_seconds < 0
        ):
            raise ValueError("ttl_seconds must be non-negative or None")
        object.__setattr__(
            self,
            "supported_languages",
            _ordered_text(self.supported_languages, "supported language"),
        )


@dataclass(frozen=True, slots=True)
class HistoryMaterializationSelection:
    """Canonical content-affecting path/parser/policy selection for one publication."""

    paths: tuple[str, ...]
    parser_version: str
    policy_version: str
    max_file_bytes: int
    max_git_output_bytes: int
    git_timeout_seconds: float
    supported_languages: tuple[str, ...]
    contract_version: str = "c6-history-selection-v1"

    def __post_init__(self) -> None:
        paths = tuple(_safe_path(path) for path in self.paths)
        if len(paths) != len(set(paths)):
            raise ValueError("selection paths must be unique")
        object.__setattr__(self, "paths", tuple(sorted(paths)))
        for name in ("parser_version", "policy_version", "contract_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        for name in ("max_file_bytes", "max_git_output_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(self.git_timeout_seconds, (int, float)):
            raise TypeError("git_timeout_seconds must be numeric")
        if self.git_timeout_seconds <= 0 or self.git_timeout_seconds > 120:
            raise ValueError("git_timeout_seconds must be in (0, 120]")
        object.__setattr__(self, "git_timeout_seconds", float(self.git_timeout_seconds))
        object.__setattr__(
            self,
            "supported_languages",
            _ordered_text(self.supported_languages, "supported language"),
        )

    @classmethod
    def from_request(
        cls,
        request: HistoryMaterializationRequest,
        *,
        parser_version: str,
    ) -> HistoryMaterializationSelection:
        if not isinstance(request, HistoryMaterializationRequest):
            raise TypeError("request must be HistoryMaterializationRequest")
        return cls(
            paths=request.paths,
            parser_version=parser_version,
            policy_version=request.policy.policy_version,
            max_file_bytes=request.policy.max_file_bytes,
            max_git_output_bytes=request.policy.max_git_output_bytes,
            git_timeout_seconds=float(request.policy.git_timeout_seconds),
            supported_languages=request.policy.supported_languages,
        )

    @property
    def identity(self) -> str:
        return _sha256(asdict(self))


@dataclass(frozen=True, slots=True)
class HistoryNamespace:
    contract_version: str
    kind: HistoryNamespaceKind
    project_id: str
    repository_id: str
    stable_version: str
    generation_id: str
    acl_ref: str
    name: str
    commit_sha: str | None
    dirty: bool

    def __post_init__(self) -> None:
        for name in (
            "contract_version",
            "project_id",
            "repository_id",
            "stable_version",
            "generation_id",
            "acl_ref",
            "name",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.kind is HistoryNamespaceKind.HISTORY:
            sha = _full_sha(self.commit_sha, "namespace commit_sha")
            object.__setattr__(self, "commit_sha", sha)
            if self.stable_version != sha:
                raise ValueError("historical stable_version must equal commit_sha")
            expected = f"code/history/{_namespace_segment(self.repository_id)}/{self.commit_sha}"
            if self.name != expected or self.dirty:
                raise ValueError("historical namespace must identify one clean commit")
        else:
            object.__setattr__(
                self,
                "commit_sha",
                _full_sha(self.commit_sha, "namespace commit_sha"),
            )
            expected = (
                f"code/current/{_namespace_segment(self.repository_id)}/"
                f"{_namespace_segment(self.generation_id)}"
            )
            if self.name != expected:
                raise ValueError("current namespace must identify its active generation")
            if self.dirty:
                match = _DIRTY_VERSION.fullmatch(self.stable_version)
                if match is None:
                    raise ValueError(
                        "dirty current versions require <base_sha>+dirty.<manifest_identity>"
                    )
                if match.group("base") != self.commit_sha:
                    raise ValueError("dirty current base SHA must equal commit_sha")
            elif self.stable_version != self.commit_sha:
                raise ValueError("clean current stable_version must equal commit_sha")

    @classmethod
    def history(
        cls,
        *,
        project_id: str,
        repository_id: str,
        commit_sha: str,
        generation_id: str,
        acl_ref: str,
    ) -> HistoryNamespace:
        sha = _full_sha(commit_sha)
        return cls(
            contract_version=HISTORY_CONTRACT_VERSION,
            kind=HistoryNamespaceKind.HISTORY,
            project_id=project_id,
            repository_id=repository_id,
            stable_version=sha,
            generation_id=generation_id,
            acl_ref=acl_ref,
            name=f"code/history/{_namespace_segment(repository_id)}/{sha}",
            commit_sha=sha,
            dirty=False,
        )

    @classmethod
    def current(
        cls,
        *,
        project_id: str,
        repository_id: str,
        stable_version: str,
        generation_id: str,
        acl_ref: str,
        commit_sha: str,
        dirty: bool = False,
    ) -> HistoryNamespace:
        return cls(
            contract_version=HISTORY_CONTRACT_VERSION,
            kind=HistoryNamespaceKind.CURRENT,
            project_id=project_id,
            repository_id=repository_id,
            stable_version=stable_version,
            generation_id=generation_id,
            acl_ref=acl_ref,
            name=(
                f"code/current/{_namespace_segment(repository_id)}/"
                f"{_namespace_segment(generation_id)}"
            ),
            commit_sha=commit_sha,
            dirty=dirty,
        )


@dataclass(frozen=True, slots=True)
class HistoryMaterializationRequest:
    project_id: str
    repository_id: str
    repository_root: str | Path
    ref: str
    generation_id: str
    acl_ref: str
    profile: str = DEFAULT_HISTORY_PROFILE
    commit_sha: str | None = None
    paths: tuple[str, ...] = ()
    schema_version: str = HISTORY_SCHEMA_VERSION
    builder_version: str = BUILDER_VERSION
    pin_reasons: tuple[HistoryPinReason, ...] = ()
    policy: HistoryMaterializationPolicy = field(default_factory=HistoryMaterializationPolicy)
    contract_version: str = HISTORY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "ref",
            "generation_id",
            "acl_ref",
            "profile",
            "schema_version",
            "builder_version",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.ref in _REF_DISALLOWED or "+dirty." in self.ref:
            raise ValueError("history requests require an explicit clean ref or full commit SHA")
        root = Path(self.repository_root).expanduser().resolve()
        object.__setattr__(self, "repository_root", str(root))
        if self.commit_sha is not None:
            object.__setattr__(self, "commit_sha", _full_sha(self.commit_sha))
        paths = tuple(_safe_path(path) for path in self.paths)
        if len(paths) != len(set(paths)):
            raise ValueError("paths must be unique")
        object.__setattr__(self, "paths", tuple(sorted(paths)))
        pins = tuple(HistoryPinReason(reason) for reason in self.pin_reasons)
        if len(pins) != len(set(pins)):
            raise ValueError("pin_reasons must be unique")
        object.__setattr__(self, "pin_reasons", tuple(sorted(pins, key=str)))
        if not isinstance(self.policy, HistoryMaterializationPolicy):
            raise TypeError("policy must be HistoryMaterializationPolicy")
        if self.contract_version != HISTORY_CONTRACT_VERSION:
            raise ValueError("unsupported history request contract version")


@dataclass(frozen=True, slots=True)
class HistoryProvenance:
    contract_version: str
    project_id: str
    repository_id: str
    requested_ref: str
    commit_sha: str
    generation_id: str
    acl_ref: str
    namespace: str
    profile: str
    parser_version: str
    builder_version: str
    schema_version: str
    policy_version: str
    selection_identity: str
    source_kind: str = "git-object"

    def __post_init__(self) -> None:
        for name in (
            "contract_version",
            "project_id",
            "repository_id",
            "requested_ref",
            "generation_id",
            "acl_ref",
            "namespace",
            "profile",
            "parser_version",
            "builder_version",
            "schema_version",
            "policy_version",
            "source_kind",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "commit_sha", _full_sha(self.commit_sha))
        object.__setattr__(
            self,
            "selection_identity",
            _sha256_identity(self.selection_identity, "selection_identity"),
        )


@dataclass(frozen=True, slots=True)
class HistoricalFile:
    contract_version: str
    project_id: str
    repository_id: str
    requested_ref: str
    commit_sha: str
    generation_id: str
    acl_ref: str
    namespace: str
    path: str
    language: str
    blob_hash: str
    size_bytes: int
    status: HistoricalFileStatus
    content: str | None
    content_hash: str | None
    provenance: HistoryProvenance
    diagnostics: tuple[HistoryDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "contract_version",
            "project_id",
            "repository_id",
            "requested_ref",
            "generation_id",
            "acl_ref",
            "namespace",
            "path",
            "language",
            "blob_hash",
        ):
            value = (
                _safe_path(getattr(self, name))
                if name == "path"
                else _text(getattr(self, name), name)
            )
            object.__setattr__(self, name, value)
        object.__setattr__(self, "commit_sha", _full_sha(self.commit_sha))
        if isinstance(self.size_bytes, bool) or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if self.status is HistoricalFileStatus.MATERIALIZED:
            if self.content is None or self.content_hash is None:
                raise ValueError("materialized files require exact content and content_hash")
            expected = "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()
            if self.content_hash != expected:
                raise ValueError("historical file content_hash does not match content")
        elif self.content is not None or self.content_hash is not None:
            raise ValueError("skipped historical files cannot carry content")
        _require_provenance_scope(self, self.provenance)


@dataclass(frozen=True, slots=True)
class HistoricalUnit:
    contract_version: str
    project_id: str
    repository_id: str
    requested_ref: str
    commit_sha: str
    generation_id: str
    acl_ref: str
    namespace: str
    unit_id: str
    entity_id: str
    file_path: str
    language: str
    unit_type: str
    ast_node_type: str
    symbol_name: str
    symbol_kind: str
    qualified_name: str
    signature: str | None
    structural_path: str
    start_line: int
    end_line: int
    body: str
    content: str
    content_hash: str
    locator: str
    neighbor_symbols: tuple[str, ...]
    builder_version: str
    parser_version: str
    quality_status: str
    provenance: HistoryProvenance

    def __post_init__(self) -> None:
        for name in (
            "contract_version",
            "project_id",
            "repository_id",
            "requested_ref",
            "generation_id",
            "acl_ref",
            "namespace",
            "unit_id",
            "entity_id",
            "file_path",
            "language",
            "unit_type",
            "ast_node_type",
            "structural_path",
            "content_hash",
            "locator",
            "builder_version",
            "parser_version",
            "quality_status",
        ):
            value = (
                _safe_path(getattr(self, name))
                if name == "file_path"
                else _text(getattr(self, name), name)
            )
            object.__setattr__(self, name, value)
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("historical unit content must contain source context")
        if not isinstance(self.body, str):
            raise TypeError("historical unit body must be a string")
        for name in ("symbol_name", "symbol_kind", "qualified_name"):
            value = getattr(self, name)
            if not isinstance(value, str) or _CONTROL.search(value):
                raise ValueError(f"{name} must be a control-free string")
        object.__setattr__(self, "signature", _optional_text(self.signature, "signature"))
        object.__setattr__(self, "commit_sha", _full_sha(self.commit_sha))
        if self.start_line < 1 or self.end_line < self.start_line:
            raise ValueError("unit line span must be one-based and ordered")
        expected_hash = "sha256:" + hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_hash != expected_hash:
            raise ValueError("historical unit content_hash does not match content")
        if f"@{self.commit_sha}/" not in self.locator:
            raise ValueError("historical unit locator must contain the exact target SHA")
        object.__setattr__(
            self,
            "neighbor_symbols",
            _ordered_text(self.neighbor_symbols, "neighbor symbol"),
        )
        _require_provenance_scope(self, self.provenance)

    @property
    def is_symbol(self) -> bool:
        return bool(self.symbol_name and self.qualified_name)


def _require_provenance_scope(
    value: HistoricalFile | HistoricalUnit,
    provenance: HistoryProvenance,
) -> None:
    if not isinstance(provenance, HistoryProvenance):
        raise TypeError("provenance must be HistoryProvenance")
    actual = (
        value.project_id,
        value.repository_id,
        value.requested_ref,
        value.commit_sha,
        value.generation_id,
        value.acl_ref,
        value.namespace,
    )
    expected = (
        provenance.project_id,
        provenance.repository_id,
        provenance.requested_ref,
        provenance.commit_sha,
        provenance.generation_id,
        provenance.acl_ref,
        provenance.namespace,
    )
    if actual != expected:
        raise ValueError("historical object scope conflicts with provenance")


@dataclass(frozen=True, slots=True)
class HistoryTrace:
    contract_version: str
    status: HistoryMaterializationStatus
    project_id: str
    repository_id: str
    requested_ref: str
    resolved_commit_sha: str
    generation_id: str
    acl_ref: str
    namespace: str
    profile: str
    content_key: str
    selection_identity: str
    cache_hit: bool
    git_argv: tuple[tuple[str, ...], ...]
    files_seen: int
    files_materialized: int
    files_skipped: int
    units_built: int
    diagnostics: tuple[HistoryDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "contract_version",
            "project_id",
            "repository_id",
            "requested_ref",
            "generation_id",
            "acl_ref",
            "namespace",
            "profile",
            "content_key",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self,
            "resolved_commit_sha",
            _full_sha(self.resolved_commit_sha, "resolved_commit_sha"),
        )
        object.__setattr__(
            self,
            "selection_identity",
            _sha256_identity(self.selection_identity, "selection_identity"),
        )
        object.__setattr__(
            self,
            "git_argv",
            tuple(tuple(_text(arg, "git argv") for arg in argv) for argv in self.git_argv),
        )
        for name in ("files_seen", "files_materialized", "files_skipped", "units_built"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.files_seen != self.files_materialized + self.files_skipped:
            raise ValueError("trace file accounting is inconsistent")
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


def history_content_key(
    *,
    repository_id: str,
    commit_sha: str,
    profile: str,
    generation_id: str,
    schema_version: str,
    builder_version: str,
    selection_identity: str,
) -> str:
    payload = {
        "builder_version": _text(builder_version, "builder_version"),
        "commit_sha": _full_sha(commit_sha),
        "generation_id": _text(generation_id, "generation_id"),
        "profile": _text(profile, "profile"),
        "repository_id": _text(repository_id, "repository_id"),
        "schema_version": _text(schema_version, "schema_version"),
        "selection_identity": _sha256_identity(
            selection_identity,
            "selection_identity",
        ),
    }
    return (
        "code-history-publication://sha256/" + hashlib.sha256(_canonical_json(payload)).hexdigest()
    )


@dataclass(frozen=True, slots=True)
class HistoryPublication:
    contract_version: str
    schema_version: str
    content_key: str
    project_id: str
    repository_id: str
    requested_ref: str
    commit_sha: str
    generation_id: str
    acl_ref: str
    profile: str
    namespace: HistoryNamespace
    selection: HistoryMaterializationSelection
    parser_version: str
    builder_version: str
    status: HistoryMaterializationStatus
    files: tuple[HistoricalFile, ...]
    units: tuple[HistoricalUnit, ...]
    diagnostics: tuple[HistoryDiagnostic, ...]
    trace: HistoryTrace
    pin_reasons: tuple[HistoryPinReason, ...] = ()
    created_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        for name in (
            "contract_version",
            "schema_version",
            "content_key",
            "project_id",
            "repository_id",
            "requested_ref",
            "generation_id",
            "acl_ref",
            "profile",
            "parser_version",
            "builder_version",
            "created_at",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "commit_sha", _full_sha(self.commit_sha))
        if not isinstance(self.namespace, HistoryNamespace):
            raise TypeError("namespace must be HistoryNamespace")
        if not isinstance(self.selection, HistoryMaterializationSelection):
            raise TypeError("selection must be HistoryMaterializationSelection")
        if self.selection.parser_version != self.parser_version:
            raise ValueError("selection parser_version must match publication parser_version")
        expected_scope = (
            self.project_id,
            self.repository_id,
            self.commit_sha,
            self.generation_id,
            self.acl_ref,
        )
        namespace_scope = (
            self.namespace.project_id,
            self.namespace.repository_id,
            self.namespace.commit_sha,
            self.namespace.generation_id,
            self.namespace.acl_ref,
        )
        if self.namespace.kind is not HistoryNamespaceKind.HISTORY:
            raise ValueError("historical publications require a historical namespace")
        if expected_scope != namespace_scope:
            raise ValueError("publication scope conflicts with namespace")
        expected_key = history_content_key(
            repository_id=self.repository_id,
            commit_sha=self.commit_sha,
            profile=self.profile,
            generation_id=self.generation_id,
            schema_version=self.schema_version,
            builder_version=self.builder_version,
            selection_identity=self.selection.identity,
        )
        if self.content_key != expected_key:
            raise ValueError("publication content_key does not match its exact version identity")
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "units", tuple(self.units))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        file_paths = {item.path for item in self.files}
        if len(file_paths) != len(self.files):
            raise ValueError("historical publication file paths must be unique")
        if any(unit.file_path not in file_paths for unit in self.units):
            raise ValueError("historical units must belong to a published file")
        for item in (*self.files, *self.units):
            scope = (
                item.project_id,
                item.repository_id,
                item.commit_sha,
                item.generation_id,
                item.acl_ref,
                item.namespace,
            )
            if scope != (*expected_scope, self.namespace.name):
                raise ValueError("historical publication contains mixed scope data")
        if not isinstance(self.trace, HistoryTrace):
            raise TypeError("trace must be HistoryTrace")
        trace_scope = (
            self.trace.project_id,
            self.trace.repository_id,
            self.trace.resolved_commit_sha,
            self.trace.generation_id,
            self.trace.acl_ref,
            self.trace.namespace,
            self.trace.content_key,
            self.trace.selection_identity,
        )
        if trace_scope != (
            *expected_scope,
            self.namespace.name,
            self.content_key,
            self.selection.identity,
        ):
            raise ValueError("publication trace conflicts with publication scope")
        pins = tuple(HistoryPinReason(reason) for reason in self.pin_reasons)
        if len(pins) != len(set(pins)):
            raise ValueError("publication pin_reasons must be unique")
        object.__setattr__(self, "pin_reasons", tuple(sorted(pins, key=str)))

    @property
    def publication_hash(self) -> str:
        return _sha256(
            {
                "content_key": self.content_key,
                "selection_identity": self.selection.identity,
                "files": [
                    {
                        "blob_hash": item.blob_hash,
                        "content_hash": item.content_hash,
                        "path": item.path,
                        "status": item.status.value,
                    }
                    for item in self.files
                ],
                "units": [
                    {
                        "content_hash": item.content_hash,
                        "locator": item.locator,
                        "unit_id": item.unit_id,
                    }
                    for item in self.units
                ],
            }
        )

    def exact_units(
        self,
        *,
        project_id: str,
        repository_id: str,
        commit_sha: str,
        generation_id: str,
        acl_ref: str,
    ) -> tuple[HistoricalUnit, ...]:
        self.require_scope(
            project_id=project_id,
            repository_id=repository_id,
            commit_sha=commit_sha,
            generation_id=generation_id,
            acl_ref=acl_ref,
        )
        return self.units

    def require_scope(
        self,
        *,
        project_id: str,
        repository_id: str,
        commit_sha: str,
        generation_id: str,
        acl_ref: str,
        selection_identity: str | None = None,
    ) -> None:
        expected = (
            self.project_id,
            self.repository_id,
            self.commit_sha,
            self.generation_id,
            self.acl_ref,
        )
        actual = (
            _text(project_id, "project_id"),
            _text(repository_id, "repository_id"),
            _full_sha(commit_sha),
            _text(generation_id, "generation_id"),
            _text(acl_ref, "acl_ref"),
        )
        if actual != expected:
            raise HistoryScopeError("historical publication scope/version/ACL mismatch")
        if (
            selection_identity is not None
            and _sha256_identity(selection_identity, "selection_identity")
            != self.selection.identity
        ):
            raise HistoryScopeError("historical publication selection mismatch")

    @property
    def selection_identity(self) -> str:
        return self.selection.identity


@dataclass(frozen=True, slots=True)
class HistoryEvictionResult:
    evicted_content_keys: tuple[str, ...]
    retained_content_keys: tuple[str, ...]
    publication_count: int
    commit_fact_count: int


@runtime_checkable
class HistoricalPublicationStore(Protocol):
    def get_exact(
        self,
        *,
        project_id: str,
        repository_id: str,
        commit_sha: str,
        generation_id: str,
        acl_ref: str,
        profile: str,
        schema_version: str,
        builder_version: str,
        selection_identity: str,
    ) -> HistoryPublication | None: ...

    def publish(self, publication: HistoryPublication) -> HistoryPublication: ...

    def evict(
        self,
        policy: HistoryMaterializationPolicy,
        *,
        now: float | None = None,
    ) -> HistoryEvictionResult: ...

    def has_commit_fact(self, repository_id: str, commit_sha: str) -> bool: ...

    def publication_count(self) -> int: ...

    def commit_fact_count(self) -> int: ...


@dataclass(slots=True)
class _MemoryPublication:
    publication: HistoryPublication
    created_epoch: float
    last_accessed_epoch: float
    access_count: int


class InMemoryHistoricalPublicationStore:
    """Copy-on-write in-memory implementation for isolated tests."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        self._clock = clock
        self.failpoint = failpoint
        self._publications: dict[str, _MemoryPublication] = {}
        self._commit_facts: dict[tuple[str, str], tuple[str, str]] = {}
        self._lock = threading.RLock()

    def get_exact(
        self,
        *,
        project_id: str,
        repository_id: str,
        commit_sha: str,
        generation_id: str,
        acl_ref: str,
        profile: str,
        schema_version: str,
        builder_version: str,
        selection_identity: str,
    ) -> HistoryPublication | None:
        key = history_content_key(
            repository_id=repository_id,
            commit_sha=commit_sha,
            profile=profile,
            generation_id=generation_id,
            schema_version=schema_version,
            builder_version=builder_version,
            selection_identity=selection_identity,
        )
        with self._lock:
            entry = self._publications.get(key)
            if entry is None:
                return None
            try:
                entry.publication.require_scope(
                    project_id=project_id,
                    repository_id=repository_id,
                    commit_sha=commit_sha,
                    generation_id=generation_id,
                    acl_ref=acl_ref,
                    selection_identity=selection_identity,
                )
            except HistoryScopeError:
                return None
            entry.last_accessed_epoch = self._clock()
            entry.access_count += 1
            return entry.publication

    def require_exact(self, **scope: str) -> HistoryPublication:
        publication = self.get_exact(**scope)
        if publication is None:
            raise HistoryScopeError("no exact authorized historical publication")
        return publication

    def publish(self, publication: HistoryPublication) -> HistoryPublication:
        if not isinstance(publication, HistoryPublication):
            raise TypeError("publication must be HistoryPublication")
        now = self._clock()
        with self._lock:
            existing = self._publications.get(publication.content_key)
            if existing is not None:
                _require_same_publication(existing.publication, publication)
                merged = _merge_publication_pins(existing.publication, publication)
                if merged is existing.publication:
                    return existing.publication
                publications = dict(self._publications)
                publications[publication.content_key] = _MemoryPublication(
                    publication=merged,
                    created_epoch=existing.created_epoch,
                    last_accessed_epoch=existing.last_accessed_epoch,
                    access_count=existing.access_count,
                )
                if self.failpoint is not None:
                    self.failpoint("after_pin_union")
                    self.failpoint("before_commit")
                self._publications = publications
                return merged
            facts = dict(self._commit_facts)
            publications = dict(self._publications)
            fact_key = (publication.repository_id, publication.commit_sha)
            fact_scope = (publication.project_id, publication.acl_ref)
            if fact_key in facts and facts[fact_key] != fact_scope:
                raise HistoryScopeError("commit fact repository/project/ACL scope conflict")
            facts[fact_key] = fact_scope
            if self.failpoint is not None:
                self.failpoint("after_commit_fact")
            publications[publication.content_key] = _MemoryPublication(
                publication=publication,
                created_epoch=now,
                last_accessed_epoch=now,
                access_count=1,
            )
            if self.failpoint is not None:
                self.failpoint("before_commit")
            self._commit_facts = facts
            self._publications = publications
            return publication

    def evict(
        self,
        policy: HistoryMaterializationPolicy,
        *,
        now: float | None = None,
    ) -> HistoryEvictionResult:
        if not isinstance(policy, HistoryMaterializationPolicy):
            raise TypeError("policy must be HistoryMaterializationPolicy")
        current = self._clock() if now is None else float(now)
        with self._lock:
            candidates = [
                (key, entry)
                for key, entry in self._publications.items()
                if entry.publication.namespace.kind is HistoryNamespaceKind.HISTORY
                and not _retained(entry.publication, entry.access_count, policy)
            ]
            evicted: set[str] = set()
            if policy.ttl_seconds is not None:
                evicted.update(
                    key
                    for key, entry in candidates
                    if current - entry.last_accessed_epoch >= policy.ttl_seconds
                )
            remaining_count = len(self._publications) - len(evicted)
            overflow = max(0, remaining_count - policy.max_historical_publications)
            if overflow:
                lru = sorted(
                    (
                        (entry.last_accessed_epoch, entry.created_epoch, key)
                        for key, entry in candidates
                        if key not in evicted
                    )
                )
                evicted.update(key for _, _, key in lru[:overflow])
            for key in evicted:
                self._publications.pop(key, None)
            retained = tuple(sorted(self._publications))
            return HistoryEvictionResult(
                evicted_content_keys=tuple(sorted(evicted)),
                retained_content_keys=retained,
                publication_count=len(retained),
                commit_fact_count=len(self._commit_facts),
            )

    def has_commit_fact(self, repository_id: str, commit_sha: str) -> bool:
        key = (_text(repository_id, "repository_id"), _full_sha(commit_sha))
        with self._lock:
            return key in self._commit_facts

    def publication_count(self) -> int:
        with self._lock:
            return len(self._publications)

    def commit_fact_count(self) -> int:
        with self._lock:
            return len(self._commit_facts)


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS c6_history_commit_facts (
    repository_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    requested_ref TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(repository_id, commit_sha)
);
CREATE TABLE IF NOT EXISTS c6_history_publications (
    content_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    profile TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    builder_version TEXT NOT NULL,
    selection_identity TEXT NOT NULL,
    namespace_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    created_epoch REAL NOT NULL,
    last_accessed_epoch REAL NOT NULL,
    access_count INTEGER NOT NULL CHECK(access_count >= 1),
    FOREIGN KEY(repository_id, commit_sha)
        REFERENCES c6_history_commit_facts(repository_id, commit_sha)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_c6_history_publications_exact
    ON c6_history_publications(
        project_id, repository_id, commit_sha, generation_id, acl_ref,
        profile, schema_version, builder_version, selection_identity
    );
CREATE INDEX IF NOT EXISTS idx_c6_history_publications_lru
    ON c6_history_publications(last_accessed_epoch, created_epoch);
"""


class SQLiteHistoricalPublicationStore:
    """Temporary-only SQLite implementation with transaction-scoped publication."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        raw_path = str(database_path)
        self._memory = raw_path == ":memory:"
        if self._memory:
            self.database_path = raw_path
        else:
            resolved = Path(database_path).expanduser().resolve()
            temp_root = Path(tempfile.gettempdir()).resolve()
            try:
                resolved.relative_to(temp_root)
            except ValueError as exc:
                raise ValueError("SQLite history test store only accepts temporary paths") from exc
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self.database_path = str(resolved)
        self._clock = clock
        self.failpoint = failpoint
        self._lock = threading.RLock()
        self._memory_db: sqlite3.Connection | None = None
        if self._memory:
            self._memory_db = self._new_connection()
        with self._connection() as db:
            db.executescript(_SQLITE_SCHEMA)

    def _new_connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, isolation_level=None, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _connection(self) -> _SQLiteConnection:
        if self._memory_db is not None:
            return _SQLiteConnection(self._memory_db, close=False)
        return _SQLiteConnection(self._new_connection(), close=True)

    def close(self) -> None:
        with self._lock:
            if self._memory_db is not None:
                self._memory_db.close()
                self._memory_db = None

    def get_exact(
        self,
        *,
        project_id: str,
        repository_id: str,
        commit_sha: str,
        generation_id: str,
        acl_ref: str,
        profile: str,
        schema_version: str,
        builder_version: str,
        selection_identity: str,
    ) -> HistoryPublication | None:
        key = history_content_key(
            repository_id=repository_id,
            commit_sha=commit_sha,
            profile=profile,
            generation_id=generation_id,
            schema_version=schema_version,
            builder_version=builder_version,
            selection_identity=selection_identity,
        )
        sha = _full_sha(commit_sha)
        now = self._clock()
        with self._lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    """SELECT payload_json FROM c6_history_publications
                       WHERE content_key=? AND project_id=? AND repository_id=?
                         AND commit_sha=? AND generation_id=? AND acl_ref=?
                         AND profile=? AND schema_version=? AND builder_version=?
                         AND selection_identity=?""",
                    (
                        key,
                        _text(project_id, "project_id"),
                        _text(repository_id, "repository_id"),
                        sha,
                        _text(generation_id, "generation_id"),
                        _text(acl_ref, "acl_ref"),
                        _text(profile, "profile"),
                        _text(schema_version, "schema_version"),
                        _text(builder_version, "builder_version"),
                        _sha256_identity(selection_identity, "selection_identity"),
                    ),
                ).fetchone()
                if row is not None:
                    db.execute(
                        """UPDATE c6_history_publications
                           SET last_accessed_epoch=?, access_count=access_count+1
                           WHERE content_key=?""",
                        (now, key),
                    )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return _publication_from_json(row["payload_json"]) if row else None

    def require_exact(self, **scope: str) -> HistoryPublication:
        publication = self.get_exact(**scope)
        if publication is None:
            raise HistoryScopeError("no exact authorized historical publication")
        return publication

    def publish(self, publication: HistoryPublication) -> HistoryPublication:
        if not isinstance(publication, HistoryPublication):
            raise TypeError("publication must be HistoryPublication")
        payload = _publication_to_json(publication)
        now = self._clock()
        with self._lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    "SELECT payload_json FROM c6_history_publications WHERE content_key=?",
                    (publication.content_key,),
                ).fetchone()
                if existing is not None:
                    stored = _publication_from_json(existing["payload_json"])
                    _require_same_publication(stored, publication)
                    merged = _merge_publication_pins(stored, publication)
                    if merged is not stored:
                        if self.failpoint is not None:
                            self.failpoint("after_pin_union")
                        db.execute(
                            """UPDATE c6_history_publications
                               SET payload_json=?
                               WHERE content_key=?""",
                            (_publication_to_json(merged), publication.content_key),
                        )
                        if self.failpoint is not None:
                            self.failpoint("before_commit")
                    db.execute("COMMIT")
                    return merged
                fact = db.execute(
                    """SELECT project_id, acl_ref FROM c6_history_commit_facts
                       WHERE repository_id=? AND commit_sha=?""",
                    (publication.repository_id, publication.commit_sha),
                ).fetchone()
                if fact and (fact["project_id"], fact["acl_ref"]) != (
                    publication.project_id,
                    publication.acl_ref,
                ):
                    raise HistoryScopeError("commit fact repository/project/ACL scope conflict")
                db.execute(
                    """INSERT OR IGNORE INTO c6_history_commit_facts
                       (repository_id, commit_sha, project_id, acl_ref, requested_ref, observed_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        publication.repository_id,
                        publication.commit_sha,
                        publication.project_id,
                        publication.acl_ref,
                        publication.requested_ref,
                        publication.created_at,
                    ),
                )
                if self.failpoint is not None:
                    self.failpoint("after_commit_fact")
                db.execute(
                    """INSERT INTO c6_history_publications
                       (content_key, project_id, repository_id, commit_sha, generation_id,
                        acl_ref, profile, schema_version, builder_version, selection_identity,
                        namespace_kind, payload_json, created_epoch, last_accessed_epoch,
                        access_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                    (
                        publication.content_key,
                        publication.project_id,
                        publication.repository_id,
                        publication.commit_sha,
                        publication.generation_id,
                        publication.acl_ref,
                        publication.profile,
                        publication.schema_version,
                        publication.builder_version,
                        publication.selection.identity,
                        publication.namespace.kind.value,
                        payload,
                        now,
                        now,
                    ),
                )
                if self.failpoint is not None:
                    self.failpoint("before_commit")
                db.execute("COMMIT")
                return publication
            except Exception as exc:
                db.execute("ROLLBACK")
                if isinstance(exc, (HistoryScopeError, HistoryPublicationError)):
                    raise
                raise HistoryPublicationError("atomic historical publication failed") from exc

    def evict(
        self,
        policy: HistoryMaterializationPolicy,
        *,
        now: float | None = None,
    ) -> HistoryEvictionResult:
        if not isinstance(policy, HistoryMaterializationPolicy):
            raise TypeError("policy must be HistoryMaterializationPolicy")
        current = self._clock() if now is None else float(now)
        with self._lock, self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                rows = db.execute(
                    """SELECT content_key, payload_json, created_epoch,
                              last_accessed_epoch, access_count
                       FROM c6_history_publications
                       ORDER BY last_accessed_epoch, created_epoch, content_key"""
                ).fetchall()
                candidates: list[sqlite3.Row] = []
                for row in rows:
                    publication = _publication_from_json(row["payload_json"])
                    if publication.namespace.kind is HistoryNamespaceKind.HISTORY and not _retained(
                        publication, int(row["access_count"]), policy
                    ):
                        candidates.append(row)
                evicted: set[str] = set()
                if policy.ttl_seconds is not None:
                    evicted.update(
                        str(row["content_key"])
                        for row in candidates
                        if current - float(row["last_accessed_epoch"]) >= policy.ttl_seconds
                    )
                remaining_count = len(rows) - len(evicted)
                overflow = max(0, remaining_count - policy.max_historical_publications)
                if overflow:
                    lru_keys = [
                        str(row["content_key"])
                        for row in candidates
                        if str(row["content_key"]) not in evicted
                    ]
                    evicted.update(lru_keys[:overflow])
                if evicted:
                    marks = ",".join("?" for _ in evicted)
                    db.execute(
                        f"DELETE FROM c6_history_publications WHERE content_key IN ({marks})",
                        tuple(sorted(evicted)),
                    )
                retained = tuple(
                    str(row["content_key"])
                    for row in db.execute(
                        "SELECT content_key FROM c6_history_publications ORDER BY content_key"
                    )
                )
                fact_count = int(
                    db.execute("SELECT count(*) FROM c6_history_commit_facts").fetchone()[0]
                )
                db.execute("COMMIT")
                return HistoryEvictionResult(
                    evicted_content_keys=tuple(sorted(evicted)),
                    retained_content_keys=retained,
                    publication_count=len(retained),
                    commit_fact_count=fact_count,
                )
            except Exception:
                db.execute("ROLLBACK")
                raise

    def has_commit_fact(self, repository_id: str, commit_sha: str) -> bool:
        with self._lock, self._connection() as db:
            row = db.execute(
                """SELECT 1 FROM c6_history_commit_facts
                   WHERE repository_id=? AND commit_sha=?""",
                (_text(repository_id, "repository_id"), _full_sha(commit_sha)),
            ).fetchone()
        return row is not None

    def publication_count(self) -> int:
        with self._lock, self._connection() as db:
            return int(db.execute("SELECT count(*) FROM c6_history_publications").fetchone()[0])

    def commit_fact_count(self) -> int:
        with self._lock, self._connection() as db:
            return int(db.execute("SELECT count(*) FROM c6_history_commit_facts").fetchone()[0])


class _SQLiteConnection:
    def __init__(self, connection: sqlite3.Connection, *, close: bool) -> None:
        self.connection = connection
        self.close_connection = close

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, *_args: object) -> None:
        if self.close_connection:
            self.connection.close()


def _retained(
    publication: HistoryPublication,
    access_count: int,
    policy: HistoryMaterializationPolicy,
) -> bool:
    pins = set(publication.pin_reasons)
    return any(
        (
            publication.namespace.kind is HistoryNamespaceKind.CURRENT,
            policy.retain_explicit_pins and HistoryPinReason.EXPLICIT in pins,
            policy.retain_referenced_pins and HistoryPinReason.REFERENCED in pins,
            policy.retain_hot_publications
            and (HistoryPinReason.HOT in pins or access_count >= policy.hot_access_threshold),
        )
    )


def _require_same_publication(
    stored: HistoryPublication,
    challenger: HistoryPublication,
) -> None:
    if stored.selection != challenger.selection:
        raise HistoryScopeError("cached publication materialization selection mismatch")
    if stored.publication_hash != challenger.publication_hash:
        raise HistoryPublicationError("content key collision with different historical content")
    if (
        stored.project_id,
        stored.repository_id,
        stored.commit_sha,
        stored.generation_id,
        stored.acl_ref,
    ) != (
        challenger.project_id,
        challenger.repository_id,
        challenger.commit_sha,
        challenger.generation_id,
        challenger.acl_ref,
    ):
        raise HistoryScopeError("cached publication scope/version/ACL mismatch")


def _merge_publication_pins(
    stored: HistoryPublication,
    challenger: HistoryPublication,
) -> HistoryPublication:
    pins = tuple(
        sorted(
            set((*stored.pin_reasons, *challenger.pin_reasons)),
            key=str,
        )
    )
    if pins == stored.pin_reasons:
        return stored
    return replace(stored, pin_reasons=pins)


def _rebind_requested_ref(
    publication: HistoryPublication,
    requested_ref: str,
) -> HistoryPublication:
    """Project cached immutable commit content onto the caller's explicit ref provenance."""

    ref = _text(requested_ref, "requested_ref")
    if publication.requested_ref == ref:
        return publication
    provenances: dict[HistoryProvenance, HistoryProvenance] = {}

    def rebind(provenance: HistoryProvenance) -> HistoryProvenance:
        if provenance not in provenances:
            provenances[provenance] = replace(provenance, requested_ref=ref)
        return provenances[provenance]

    files = tuple(
        replace(item, requested_ref=ref, provenance=rebind(item.provenance))
        for item in publication.files
    )
    units = tuple(
        replace(item, requested_ref=ref, provenance=rebind(item.provenance))
        for item in publication.units
    )
    trace = replace(publication.trace, requested_ref=ref)
    return replace(
        publication,
        requested_ref=ref,
        files=files,
        units=units,
        trace=trace,
    )


@dataclass(frozen=True, slots=True)
class _GitResult:
    stdout: bytes
    argv: tuple[str, ...]


class _BoundedGit:
    def __init__(self, root: Path, policy: HistoryMaterializationPolicy) -> None:
        self.root = root
        self.policy = policy
        self.argv_trace: list[tuple[str, ...]] = []

    def run(
        self,
        args: Sequence[str],
        *,
        output_limit: int | None = None,
    ) -> _GitResult:
        argv = ("git", *tuple(args))
        self.argv_trace.append(argv)
        limit = output_limit or self.policy.max_git_output_bytes
        environment = {
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
        process = subprocess.Popen(
            argv,
            cwd=self.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        output = bytearray()
        error = bytearray()
        deadline = time.monotonic() + self.policy.git_timeout_seconds
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise HistoryGitError(
                        f"Git command timed out after {self.policy.git_timeout_seconds:g}s"
                    )
                events = selector.select(min(remaining, 0.1))
                if not events and process.poll() is not None:
                    for key in list(selector.get_map().values()):
                        chunk = os.read(key.fileobj.fileno(), 65_536)
                        if chunk:
                            (output if key.data == "stdout" else error).extend(chunk)
                        selector.unregister(key.fileobj)
                    break
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = output if key.data == "stdout" else error
                    target.extend(chunk)
                    if len(output) > limit or len(error) > min(limit, 262_144):
                        process.kill()
                        process.wait()
                        raise HistoryGitError("Git command exceeded its bounded output limit")
            return_code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        finally:
            selector.close()
            if process.poll() is None:
                process.kill()
                process.wait()
        if return_code != 0:
            detail = error.decode("utf-8", errors="replace").strip()
            raise HistoryGitError(
                f"Git command failed with exit {return_code}: {detail[:500] or 'no stderr'}"
            )
        return _GitResult(stdout=bytes(output), argv=argv)


@dataclass(frozen=True, slots=True)
class _TreeBlob:
    path: str
    blob_hash: str
    size_bytes: int


class HistoricalMaterializer:
    """Resolve one explicit ref and atomically publish exact historical units."""

    def __init__(
        self,
        store: HistoricalPublicationStore,
        *,
        parser: CodeParser | None = None,
        builder: CodeUnitBuilder | None = None,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        if not isinstance(store, HistoricalPublicationStore):
            raise TypeError("store must implement HistoricalPublicationStore")
        self.store = store
        self.parser = parser or CodeParser()
        self.builder = builder or CodeUnitBuilder()
        self.clock = clock

    def resolve_ref(self, request: HistoryMaterializationRequest) -> str:
        root = self._validate_root(request)
        git = _BoundedGit(root, request.policy)
        return self._resolve_ref(git, request)

    def materialize(self, request: HistoryMaterializationRequest) -> HistoryPublication:
        if not isinstance(request, HistoryMaterializationRequest):
            raise TypeError("request must be HistoryMaterializationRequest")
        if self.builder.builder_version != request.builder_version:
            raise HistoryScopeError("requested builder_version does not match the injected builder")
        root = self._validate_root(request)
        git = _BoundedGit(root, request.policy)
        sha = self._resolve_ref(git, request)
        namespace = HistoryNamespace.history(
            project_id=request.project_id,
            repository_id=request.repository_id,
            commit_sha=sha,
            generation_id=request.generation_id,
            acl_ref=request.acl_ref,
        )
        selection = HistoryMaterializationSelection.from_request(
            request,
            parser_version=self.parser.parser_version,
        )
        content_key = history_content_key(
            repository_id=request.repository_id,
            commit_sha=sha,
            profile=request.profile,
            generation_id=request.generation_id,
            schema_version=request.schema_version,
            builder_version=request.builder_version,
            selection_identity=selection.identity,
        )
        cached = self.store.get_exact(
            project_id=request.project_id,
            repository_id=request.repository_id,
            commit_sha=sha,
            generation_id=request.generation_id,
            acl_ref=request.acl_ref,
            profile=request.profile,
            schema_version=request.schema_version,
            builder_version=request.builder_version,
            selection_identity=selection.identity,
        )
        if cached is not None:
            if cached.selection != selection:
                raise HistoryScopeError("cached publication materialization selection mismatch")
            if request.pin_reasons:
                cached = self.store.publish(replace(cached, pin_reasons=request.pin_reasons))
            cached = _rebind_requested_ref(cached, request.ref)
            diagnostic = HistoryDiagnostic(
                code=HistoryDiagnosticCode.CACHE_HIT,
                severity=HistoryDiagnosticSeverity.INFO,
                message="Exact historical publication reused without parsing or rebuilding.",
            )
            trace = replace(
                cached.trace,
                status=HistoryMaterializationStatus.CACHE_HIT,
                cache_hit=True,
                git_argv=tuple(git.argv_trace),
                diagnostics=tuple((*cached.trace.diagnostics, diagnostic)),
            )
            return replace(cached, trace=trace)

        provenance = HistoryProvenance(
            contract_version=request.contract_version,
            project_id=request.project_id,
            repository_id=request.repository_id,
            requested_ref=request.ref,
            commit_sha=sha,
            generation_id=request.generation_id,
            acl_ref=request.acl_ref,
            namespace=namespace.name,
            profile=request.profile,
            parser_version=self.parser.parser_version,
            builder_version=self.builder.builder_version,
            schema_version=request.schema_version,
            policy_version=request.policy.policy_version,
            selection_identity=selection.identity,
        )
        blobs = self._tree(git, sha, request.paths)
        files: list[HistoricalFile] = []
        units: list[HistoricalUnit] = []
        diagnostics: list[HistoryDiagnostic] = []
        found_paths = {item.path for item in blobs}
        for requested_path in request.paths:
            if not any(
                path == requested_path or path.startswith(requested_path.rstrip("/") + "/")
                for path in found_paths
            ):
                diagnostics.append(
                    HistoryDiagnostic(
                        code=HistoryDiagnosticCode.PATH_NOT_FOUND,
                        severity=HistoryDiagnosticSeverity.WARNING,
                        message="Requested path does not exist at the resolved commit.",
                        path=requested_path,
                    )
                )
        for blob in blobs:
            historical_file, built = self._materialize_blob(
                git=git,
                request=request,
                sha=sha,
                namespace=namespace,
                provenance=provenance,
                blob=blob,
            )
            files.append(historical_file)
            units.extend(built)
            diagnostics.extend(historical_file.diagnostics)
        files.sort(key=lambda item: item.path)
        units.sort(
            key=lambda item: (
                item.file_path,
                item.start_line,
                item.end_line,
                item.structural_path,
                item.unit_id,
            )
        )
        materialized_count = sum(item.status is HistoricalFileStatus.MATERIALIZED for item in files)
        skipped_count = len(files) - materialized_count
        if not files or not materialized_count:
            status = HistoryMaterializationStatus.EMPTY
        elif skipped_count or diagnostics:
            status = HistoryMaterializationStatus.PARTIAL
        else:
            status = HistoryMaterializationStatus.COMPLETE
        trace = HistoryTrace(
            contract_version=request.contract_version,
            status=status,
            project_id=request.project_id,
            repository_id=request.repository_id,
            requested_ref=request.ref,
            resolved_commit_sha=sha,
            generation_id=request.generation_id,
            acl_ref=request.acl_ref,
            namespace=namespace.name,
            profile=request.profile,
            content_key=content_key,
            selection_identity=selection.identity,
            cache_hit=False,
            git_argv=tuple(git.argv_trace),
            files_seen=len(files),
            files_materialized=materialized_count,
            files_skipped=skipped_count,
            units_built=len(units),
            diagnostics=tuple(diagnostics),
        )
        publication = HistoryPublication(
            contract_version=request.contract_version,
            schema_version=request.schema_version,
            content_key=content_key,
            project_id=request.project_id,
            repository_id=request.repository_id,
            requested_ref=request.ref,
            commit_sha=sha,
            generation_id=request.generation_id,
            acl_ref=request.acl_ref,
            profile=request.profile,
            namespace=namespace,
            selection=selection,
            parser_version=self.parser.parser_version,
            builder_version=self.builder.builder_version,
            status=status,
            files=tuple(files),
            units=tuple(units),
            diagnostics=tuple(diagnostics),
            trace=trace,
            pin_reasons=request.pin_reasons,
            created_at=self.clock(),
        )
        return self.store.publish(publication)

    def git_rename_hints(
        self,
        *,
        request: HistoryMaterializationRequest,
        source_commit_sha: str,
        target_commit_sha: str,
        source_generation_id: str | None = None,
        target_generation_id: str | None = None,
    ) -> tuple[GitRenameHint, ...]:
        root = self._validate_root(request)
        source = _full_sha(source_commit_sha, "source_commit_sha")
        target = _full_sha(target_commit_sha, "target_commit_sha")
        if source == target:
            raise HistoryScopeError("rename hints require an explicit version transition")
        git = _BoundedGit(root, request.policy)
        for sha in (source, target):
            resolved = self._resolve_exact_sha(git, sha)
            if resolved != sha:
                raise HistoryScopeError("rename hint version did not resolve exactly")
        result = git.run(
            (
                "diff",
                "--name-status",
                "--find-renames",
                "--no-ext-diff",
                source,
                target,
                "--",
            )
        )
        hints: list[GitRenameHint] = []
        for line in result.stdout.decode("utf-8", errors="strict").splitlines():
            fields = line.split("\t")
            if len(fields) != 3 or not fields[0].startswith("R"):
                continue
            similarity_text = fields[0][1:]
            similarity = int(similarity_text) / 100 if similarity_text.isdigit() else 1.0
            hints.append(
                GitRenameHint(
                    project_id=request.project_id,
                    repository_id=request.repository_id,
                    source_commit_sha=source,
                    target_commit_sha=target,
                    source_generation_id=source_generation_id or request.generation_id,
                    target_generation_id=target_generation_id or request.generation_id,
                    acl_ref=request.acl_ref,
                    old_path=_safe_path(fields[1]),
                    new_path=_safe_path(fields[2]),
                    similarity=float(similarity),
                    evidence_locator=f"git-diff://{request.repository_id}/{source}..{target}",
                )
            )
        return tuple(sorted(hints, key=lambda item: (item.old_path, item.new_path)))

    @staticmethod
    def _validate_root(request: HistoryMaterializationRequest) -> Path:
        root = Path(request.repository_root).resolve()
        if not root.is_dir():
            raise HistoryScopeError("repository_root is not a directory")
        return root

    @staticmethod
    def _resolve_exact_sha(git: _BoundedGit, sha: str) -> str:
        result = git.run(
            ("rev-parse", "--verify", "--end-of-options", f"{sha}^{{commit}}"),
            output_limit=256,
        )
        resolved = result.stdout.decode("ascii", errors="strict").strip().lower()
        return _full_sha(resolved, "resolved commit SHA")

    def _resolve_ref(
        self,
        git: _BoundedGit,
        request: HistoryMaterializationRequest,
    ) -> str:
        top = git.run(("rev-parse", "--show-toplevel"), output_limit=4096)
        try:
            top_level = Path(top.stdout.decode("utf-8", errors="strict").strip()).resolve()
        except (UnicodeDecodeError, OSError) as exc:
            raise HistoryScopeError("repository root could not be verified") from exc
        if top_level != git.root:
            raise HistoryScopeError("repository_root must be the exact Git worktree root")
        result = git.run(
            ("rev-parse", "--verify", "--end-of-options", f"{request.ref}^{{commit}}"),
            output_limit=256,
        )
        sha = _full_sha(
            result.stdout.decode("ascii", errors="strict").strip().lower(),
            "resolved commit SHA",
        )
        if request.commit_sha is not None and sha != request.commit_sha:
            raise HistoryScopeError("explicit ref resolved to a different commit SHA")
        return sha

    @staticmethod
    def _tree(
        git: _BoundedGit,
        sha: str,
        paths: tuple[str, ...],
    ) -> tuple[_TreeBlob, ...]:
        result = git.run(
            (
                "ls-tree",
                "-r",
                "-l",
                "-z",
                sha,
                *(("--", *paths) if paths else ()),
            )
        )
        blobs: list[_TreeBlob] = []
        for record in result.stdout.split(b"\0"):
            if not record or b"\t" not in record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            fields = metadata.decode("ascii", errors="strict").split()
            if len(fields) != 4 or fields[1] != "blob":
                continue
            path = _safe_path(raw_path.decode("utf-8", errors="strict"))
            if not fields[3].isdigit():
                continue
            blobs.append(
                _TreeBlob(
                    path=path,
                    blob_hash=_text(fields[2], "blob_hash"),
                    size_bytes=int(fields[3]),
                )
            )
        return tuple(sorted(blobs, key=lambda item: item.path))

    def _materialize_blob(
        self,
        *,
        git: _BoundedGit,
        request: HistoryMaterializationRequest,
        sha: str,
        namespace: HistoryNamespace,
        provenance: HistoryProvenance,
        blob: _TreeBlob,
    ) -> tuple[HistoricalFile, tuple[HistoricalUnit, ...]]:
        language = language_for_path(blob.path)
        if language is None or language not in request.policy.supported_languages:
            diagnostic = HistoryDiagnostic(
                code=HistoryDiagnosticCode.UNSUPPORTED_LANGUAGE,
                severity=HistoryDiagnosticSeverity.INFO,
                message="File language is outside the historical Code materialization profile.",
                path=blob.path,
            )
            return (
                self._skipped_file(
                    request,
                    sha,
                    namespace,
                    provenance,
                    blob,
                    language or "unknown",
                    HistoricalFileStatus.SKIPPED_UNSUPPORTED,
                    diagnostic,
                ),
                (),
            )
        if blob.size_bytes > request.policy.max_file_bytes:
            diagnostic = HistoryDiagnostic(
                code=HistoryDiagnosticCode.OVERSIZE_SKIPPED,
                severity=HistoryDiagnosticSeverity.WARNING,
                message="File exceeds the configured historical materialization byte limit.",
                path=blob.path,
            )
            return (
                self._skipped_file(
                    request,
                    sha,
                    namespace,
                    provenance,
                    blob,
                    language,
                    HistoricalFileStatus.SKIPPED_OVERSIZE,
                    diagnostic,
                ),
                (),
            )
        result = git.run(
            ("show", f"{sha}:{blob.path}"),
            output_limit=request.policy.max_file_bytes,
        )
        raw = result.stdout
        if b"\0" in raw:
            diagnostic = HistoryDiagnostic(
                code=HistoryDiagnosticCode.BINARY_SKIPPED,
                severity=HistoryDiagnosticSeverity.WARNING,
                message="Git blob contains NUL bytes and was treated as binary.",
                path=blob.path,
            )
            return (
                self._skipped_file(
                    request,
                    sha,
                    namespace,
                    provenance,
                    blob,
                    language,
                    HistoricalFileStatus.SKIPPED_BINARY,
                    diagnostic,
                ),
                (),
            )
        try:
            content = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            diagnostic = HistoryDiagnostic(
                code=HistoryDiagnosticCode.INVALID_UTF8,
                severity=HistoryDiagnosticSeverity.WARNING,
                message="Git blob is not valid UTF-8 and was not parsed as source text.",
                path=blob.path,
            )
            return (
                self._skipped_file(
                    request,
                    sha,
                    namespace,
                    provenance,
                    blob,
                    language,
                    HistoricalFileStatus.SKIPPED_INVALID_TEXT,
                    diagnostic,
                ),
                (),
            )
        try:
            parsed, built, source_uri = self._parse_and_build(
                request=request,
                namespace=namespace,
                blob=blob,
                sha=sha,
                content=content,
            )
        except Exception as exc:
            diagnostic = HistoryDiagnostic(
                code=HistoryDiagnosticCode.PARSE_BUILD_FAILED,
                severity=HistoryDiagnosticSeverity.ERROR,
                message=f"Historical parser/unit build failed: {type(exc).__name__}: {exc}",
                path=blob.path,
            )
            return (
                self._skipped_file(
                    request,
                    sha,
                    namespace,
                    provenance,
                    blob,
                    language,
                    HistoricalFileStatus.FAILED,
                    diagnostic,
                ),
                (),
            )
        historical_file = HistoricalFile(
            contract_version=request.contract_version,
            project_id=request.project_id,
            repository_id=request.repository_id,
            requested_ref=request.ref,
            commit_sha=sha,
            generation_id=request.generation_id,
            acl_ref=request.acl_ref,
            namespace=namespace.name,
            path=blob.path,
            language=language,
            blob_hash=blob.blob_hash,
            size_bytes=len(raw),
            status=HistoricalFileStatus.MATERIALIZED,
            content=content,
            content_hash=parsed.content_hash,
            provenance=provenance,
        )
        symbols = sorted(
            parsed.symbols,
            key=lambda item: (
                item.start_line,
                item.end_line,
                item.qualified_name,
                item.name,
            ),
        )
        neighbor_map = _symbol_neighbors(symbols)
        historical_units: list[HistoricalUnit] = []
        for record in built.records:
            symbol = _owning_symbol(symbols, record.span.start_line, record.span.end_line)
            symbol_name = symbol.name if symbol is not None else ""
            qualified_name = symbol.qualified_name if symbol is not None else ""
            symbol_kind = symbol.kind if symbol is not None else ""
            locator = f"{source_uri}#L{record.span.start_line}-L{record.span.end_line}"
            historical_units.append(
                HistoricalUnit(
                    contract_version=request.contract_version,
                    project_id=request.project_id,
                    repository_id=request.repository_id,
                    requested_ref=request.ref,
                    commit_sha=sha,
                    generation_id=request.generation_id,
                    acl_ref=request.acl_ref,
                    namespace=namespace.name,
                    unit_id=record.unit_id,
                    entity_id=record.entity_id,
                    file_path=record.file_path,
                    language=record.language,
                    unit_type=record.unit_type,
                    ast_node_type=record.ast_node_type,
                    symbol_name=symbol_name,
                    symbol_kind=symbol_kind,
                    qualified_name=qualified_name,
                    signature=record.signature,
                    structural_path=record.structural_path,
                    start_line=record.span.start_line,
                    end_line=record.span.end_line,
                    body=record.body,
                    content=record.content,
                    content_hash=record.content_hash,
                    locator=locator,
                    neighbor_symbols=(
                        neighbor_map.get(symbol.qualified_name, ()) if symbol is not None else ()
                    ),
                    builder_version=record.builder_version,
                    parser_version=record.parser_version or self.parser.parser_version,
                    quality_status=record.quality_status,
                    provenance=provenance,
                )
            )
        return historical_file, tuple(historical_units)

    def _parse_and_build(
        self,
        *,
        request: HistoryMaterializationRequest,
        namespace: HistoryNamespace,
        blob: _TreeBlob,
        sha: str,
        content: str,
    ) -> tuple[Any, Any, str]:
        parsed = self.parser.parse(blob.path, content, blob_hash=blob.blob_hash)
        source_uri = (
            f"code://{_namespace_segment(request.repository_id)}@{sha}/"
            f"{quote(blob.path, safe='/._-')}"
        )
        entity_digest = hashlib.sha256(
            _canonical_json(
                {
                    "commit_sha": sha,
                    "path": blob.path,
                    "repository_id": request.repository_id,
                }
            )
        ).hexdigest()
        build_request = CodeUnitBuildRequest.from_parsed_file(
            parsed,
            project_id=request.project_id,
            repository_id=request.repository_id,
            generation_id=request.generation_id,
            entity_id=f"file-version://sha256/{entity_digest}",
            ref=sha,
            acl_ref=request.acl_ref,
            qualified_name=_module_name(blob.path),
            source_uri=source_uri,
            lineage_attributes={
                "commit_sha": sha,
                "history_namespace": namespace.name,
                "profile": request.profile,
                "requested_ref": request.ref,
                "schema_version": request.schema_version,
            },
        )
        return parsed, self.builder.build(build_request), source_uri

    @staticmethod
    def _skipped_file(
        request: HistoryMaterializationRequest,
        sha: str,
        namespace: HistoryNamespace,
        provenance: HistoryProvenance,
        blob: _TreeBlob,
        language: str,
        status: HistoricalFileStatus,
        diagnostic: HistoryDiagnostic,
    ) -> HistoricalFile:
        return HistoricalFile(
            contract_version=request.contract_version,
            project_id=request.project_id,
            repository_id=request.repository_id,
            requested_ref=request.ref,
            commit_sha=sha,
            generation_id=request.generation_id,
            acl_ref=request.acl_ref,
            namespace=namespace.name,
            path=blob.path,
            language=language,
            blob_hash=blob.blob_hash,
            size_bytes=blob.size_bytes,
            status=status,
            content=None,
            content_hash=None,
            provenance=provenance,
            diagnostics=(diagnostic,),
        )


HistoryMaterializer = HistoricalMaterializer


def _module_name(path: str) -> str:
    value = PurePosixPath(path)
    without_suffix = value.with_suffix("").as_posix()
    parts = without_suffix.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or value.stem


def _owning_symbol(symbols: Sequence[Any], start_line: int, end_line: int) -> Any | None:
    candidates = [
        symbol
        for symbol in symbols
        if symbol.start_line <= start_line and symbol.end_line >= end_line
    ]
    if not candidates:
        candidates = [
            symbol
            for symbol in symbols
            if symbol.start_line == start_line
            and not (end_line < symbol.start_line or start_line > symbol.end_line)
        ]
    return min(
        candidates,
        key=lambda item: (
            item.end_line - item.start_line,
            item.start_line,
            item.qualified_name,
        ),
        default=None,
    )


def _symbol_neighbors(symbols: Sequence[Any]) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for index, symbol in enumerate(symbols):
        neighbors: list[str] = []
        if index:
            neighbors.append(symbols[index - 1].qualified_name)
        if index + 1 < len(symbols):
            neighbors.append(symbols[index + 1].qualified_name)
        result[symbol.qualified_name] = tuple(sorted(set(neighbors)))
    return result


@dataclass(frozen=True, slots=True)
class GitRenameHint:
    project_id: str
    repository_id: str
    source_commit_sha: str
    target_commit_sha: str
    source_generation_id: str
    target_generation_id: str
    acl_ref: str
    old_path: str
    new_path: str
    similarity: float
    evidence_locator: str

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "source_generation_id",
            "target_generation_id",
            "acl_ref",
            "evidence_locator",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "source_commit_sha", _full_sha(self.source_commit_sha))
        object.__setattr__(self, "target_commit_sha", _full_sha(self.target_commit_sha))
        if self.source_commit_sha == self.target_commit_sha:
            raise ValueError("Git rename hints require an explicit version transition")
        object.__setattr__(self, "old_path", _safe_path(self.old_path))
        object.__setattr__(self, "new_path", _safe_path(self.new_path))
        if type(self.similarity) is not float or not 0.0 <= self.similarity <= 1.0:
            raise ValueError("rename similarity must be an exact float in [0, 1]")


@dataclass(frozen=True, slots=True)
class SymbolLineageRequest:
    project_id: str
    repository_id: str
    source_commit_sha: str
    target_commit_sha: str
    source_generation_id: str
    target_generation_id: str
    acl_ref: str
    candidate_threshold: float = 0.3
    confirmation_threshold: float = 0.6
    max_candidates_per_symbol: int = 5
    contract_version: str = LINEAGE_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "source_generation_id",
            "target_generation_id",
            "acl_ref",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "source_commit_sha", _full_sha(self.source_commit_sha))
        object.__setattr__(self, "target_commit_sha", _full_sha(self.target_commit_sha))
        if self.source_commit_sha == self.target_commit_sha:
            raise ValueError("symbol lineage requires an explicit version transition")
        for name in ("candidate_threshold", "confirmation_threshold"):
            value = getattr(self, name)
            if type(value) is not float or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be an exact float in [0, 1]")
        if self.confirmation_threshold < self.candidate_threshold:
            raise ValueError("confirmation_threshold must be >= candidate_threshold")
        if (
            isinstance(self.max_candidates_per_symbol, bool)
            or not 1 <= self.max_candidates_per_symbol <= 50
        ):
            raise ValueError("max_candidates_per_symbol must be in [1, 50]")


@dataclass(frozen=True, slots=True)
class LineageSignal:
    name: str
    weight: float
    value: float
    contribution: float
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "signal name"))
        object.__setattr__(self, "explanation", _text(self.explanation, "signal explanation"))
        for name in ("weight", "value", "contribution"):
            value = getattr(self, name)
            if type(value) is not float or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be an exact float in [0, 1]")


@dataclass(frozen=True, slots=True)
class LineageCandidate:
    contract_version: str
    candidate_id: str
    project_id: str
    repository_id: str
    source_commit_sha: str
    target_commit_sha: str
    source_generation_id: str
    target_generation_id: str
    acl_ref: str
    source_unit_id: str
    target_unit_id: str
    source_locator: str
    target_locator: str
    source_qualified_name: str
    target_qualified_name: str
    source_path: str
    target_path: str
    relation_type: LineageRelationType
    score: float
    status: LineageStatus
    review_required: bool
    signals: tuple[LineageSignal, ...]
    explanation_trace: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "contract_version",
            "candidate_id",
            "project_id",
            "repository_id",
            "source_generation_id",
            "target_generation_id",
            "acl_ref",
            "source_unit_id",
            "target_unit_id",
            "source_locator",
            "target_locator",
            "source_qualified_name",
            "target_qualified_name",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "source_commit_sha", _full_sha(self.source_commit_sha))
        object.__setattr__(self, "target_commit_sha", _full_sha(self.target_commit_sha))
        if self.source_commit_sha == self.target_commit_sha:
            raise ValueError("lineage candidates require an explicit version transition")
        object.__setattr__(self, "source_path", _safe_path(self.source_path))
        object.__setattr__(self, "target_path", _safe_path(self.target_path))
        if f"@{self.source_commit_sha}/" not in self.source_locator:
            raise ValueError("source lineage locator must bind the exact source SHA")
        if f"@{self.target_commit_sha}/" not in self.target_locator:
            raise ValueError("target lineage locator must bind the exact target SHA")
        if type(self.score) is not float or not 0.0 <= self.score <= 1.0:
            raise ValueError("lineage candidate score must be an exact float in [0, 1]")
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(
            self,
            "explanation_trace",
            tuple(_text(item, "explanation trace") for item in self.explanation_trace),
        )
        split_merge = self.relation_type in {
            LineageRelationType.SPLIT_INTO,
            LineageRelationType.MERGED_FROM,
        }
        if split_merge and (self.status is LineageStatus.CONFIRMED or not self.review_required):
            raise ValueError("split/merge lineage must remain review-required candidates")
        if self.status is LineageStatus.CONFIRMED and self.review_required:
            raise ValueError("confirmed lineage cannot require review")
        if self.status is LineageStatus.CONFIRMED:
            raise ValueError("confirmed links must use the SymbolLineage contract")


@dataclass(frozen=True, slots=True)
class SymbolLineage:
    contract_version: str
    lineage_id: str
    project_id: str
    repository_id: str
    source_commit_sha: str
    target_commit_sha: str
    source_generation_id: str
    target_generation_id: str
    acl_ref: str
    source_unit_id: str
    target_unit_id: str
    source_locator: str
    target_locator: str
    relation_type: CodeRelationType
    confidence: float
    status: LineageStatus
    review_required: bool
    signals: tuple[LineageSignal, ...]
    explanation_trace: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "contract_version",
            "lineage_id",
            "project_id",
            "repository_id",
            "source_generation_id",
            "target_generation_id",
            "acl_ref",
            "source_unit_id",
            "target_unit_id",
            "source_locator",
            "target_locator",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "source_commit_sha", _full_sha(self.source_commit_sha))
        object.__setattr__(self, "target_commit_sha", _full_sha(self.target_commit_sha))
        if self.source_commit_sha == self.target_commit_sha:
            raise ValueError("symbol lineage requires an explicit version transition")
        if f"@{self.source_commit_sha}/" not in self.source_locator:
            raise ValueError("source lineage locator must bind the exact source SHA")
        if f"@{self.target_commit_sha}/" not in self.target_locator:
            raise ValueError("target lineage locator must bind the exact target SHA")
        if self.relation_type not in {
            CodeRelationType.SAME_SYMBOL_AS,
            CodeRelationType.RENAMED_TO,
            CodeRelationType.MOVED_TO,
        }:
            raise ValueError("confirmed lineage uses only the registered C4 lineage ontology")
        if type(self.confidence) is not float or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("lineage confidence must be an exact float in [0, 1]")
        if self.status is not LineageStatus.CONFIRMED or self.review_required:
            raise ValueError("SymbolLineage represents only automatically confirmed links")
        object.__setattr__(self, "signals", tuple(self.signals))
        object.__setattr__(
            self,
            "explanation_trace",
            tuple(_text(item, "explanation trace") for item in self.explanation_trace),
        )


@dataclass(frozen=True, slots=True)
class SymbolLineageTrace:
    contract_version: str
    project_id: str
    repository_id: str
    source_commit_sha: str
    target_commit_sha: str
    source_generation_id: str
    target_generation_id: str
    acl_ref: str
    source_symbols: int
    target_symbols: int
    comparisons: int
    confirmed: int
    candidates: int
    scoring_version: str = "c6-symbol-lineage-deterministic-v1"

    def __post_init__(self) -> None:
        for name in (
            "contract_version",
            "project_id",
            "repository_id",
            "source_generation_id",
            "target_generation_id",
            "acl_ref",
            "scoring_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "source_commit_sha", _full_sha(self.source_commit_sha))
        object.__setattr__(self, "target_commit_sha", _full_sha(self.target_commit_sha))
        if self.source_commit_sha == self.target_commit_sha:
            raise ValueError("lineage trace requires an explicit version transition")
        for name in (
            "source_symbols",
            "target_symbols",
            "comparisons",
            "confirmed",
            "candidates",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.comparisons > self.source_symbols * self.target_symbols:
            raise ValueError("lineage trace comparison accounting is inconsistent")


@dataclass(frozen=True, slots=True)
class SymbolLineageResult:
    status: LineageStatus
    lineages: tuple[SymbolLineage, ...]
    candidates: tuple[LineageCandidate, ...]
    diagnostics: tuple[LineageDiagnostic, ...]
    trace: SymbolLineageTrace

    def __post_init__(self) -> None:
        object.__setattr__(self, "lineages", tuple(self.lineages))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if not isinstance(self.trace, SymbolLineageTrace):
            raise TypeError("trace must be SymbolLineageTrace")
        if self.trace.confirmed != len(self.lineages):
            raise ValueError("lineage result confirmed count conflicts with trace")
        if self.trace.candidates != len(self.candidates):
            raise ValueError("lineage result candidate count conflicts with trace")
        if self.status is LineageStatus.CONFIRMED and not self.lineages:
            raise ValueError("confirmed result requires at least one confirmed lineage")
        if self.status is LineageStatus.REVIEW_REQUIRED and not self.candidates:
            raise ValueError("review-required result requires at least one candidate")
        if self.status is LineageStatus.NO_MATCH and (self.lineages or self.candidates):
            raise ValueError("no-match result cannot contain lineage links or candidates")


class SymbolLineageResolver:
    """Deterministically score exact, rename, move, and review-only lineage."""

    def resolve(
        self,
        request: SymbolLineageRequest,
        source_units: Iterable[HistoricalUnit],
        target_units: Iterable[HistoricalUnit],
        *,
        rename_hints: Iterable[GitRenameHint] = (),
    ) -> SymbolLineageResult:
        if not isinstance(request, SymbolLineageRequest):
            raise TypeError("request must be SymbolLineageRequest")
        sources = _canonical_symbol_units(source_units)
        targets = _canonical_symbol_units(target_units)
        hints = tuple(rename_hints)
        self._validate_scope(request, sources, targets, hints)
        diagnostics: list[LineageDiagnostic] = []
        if not sources:
            diagnostics.append(
                LineageDiagnostic(
                    code=LineageDiagnosticCode.NO_SOURCE_SYMBOLS,
                    severity=HistoryDiagnosticSeverity.WARNING,
                    message="Source version contains no materialized symbol units.",
                )
            )
        if not targets:
            diagnostics.append(
                LineageDiagnostic(
                    code=LineageDiagnosticCode.NO_TARGET_SYMBOLS,
                    severity=HistoryDiagnosticSeverity.WARNING,
                    message="Target version contains no materialized symbol units.",
                )
            )
        hint_pairs = {(hint.old_path, hint.new_path): hint for hint in hints}
        scored: list[LineageCandidate] = []
        comparisons = 0
        for source in sources:
            source_candidates: list[LineageCandidate] = []
            for target in targets:
                comparisons += 1
                candidate = _score_candidate(request, source, target, hint_pairs)
                if candidate is not None and candidate.score >= request.candidate_threshold:
                    source_candidates.append(candidate)
            source_candidates.sort(key=_candidate_sort_key)
            scored.extend(source_candidates[: request.max_candidates_per_symbol])

        by_source: dict[str, list[LineageCandidate]] = {}
        by_target: dict[str, list[LineageCandidate]] = {}
        for candidate in scored:
            by_source.setdefault(candidate.source_unit_id, []).append(candidate)
            by_target.setdefault(candidate.target_unit_id, []).append(candidate)
        ambiguous_source = {
            key
            for key, values in by_source.items()
            if len([item for item in values if item.score >= request.confirmation_threshold]) > 1
        }
        ambiguous_target = {
            key
            for key, values in by_target.items()
            if len([item for item in values if item.score >= request.confirmation_threshold]) > 1
        }
        confirmed: list[SymbolLineage] = []
        review: list[LineageCandidate] = []
        used_targets: set[str] = set()
        for candidate in sorted(scored, key=_candidate_sort_key):
            deterministic = _deterministic_confirmation(candidate)
            ambiguous = (
                candidate.source_unit_id in ambiguous_source
                or candidate.target_unit_id in ambiguous_target
            )
            if (
                deterministic
                and candidate.score >= request.confirmation_threshold
                and not ambiguous
                and candidate.target_unit_id not in used_targets
            ):
                confirmed.append(_confirmed_lineage(candidate))
                used_targets.add(candidate.target_unit_id)
            else:
                review.append(
                    replace(
                        candidate,
                        status=LineageStatus.REVIEW_REQUIRED,
                        review_required=True,
                        explanation_trace=tuple(
                            (*candidate.explanation_trace, "Automatic confirmation gate not met.")
                        ),
                    )
                )

        for source_id in sorted(ambiguous_source):
            values = sorted(by_source[source_id], key=_candidate_sort_key)
            for item in values:
                review.append(
                    replace(
                        item,
                        candidate_id=_candidate_id(
                            item.source_unit_id,
                            item.target_unit_id,
                            LineageRelationType.SPLIT_INTO,
                        ),
                        relation_type=LineageRelationType.SPLIT_INTO,
                        status=LineageStatus.REVIEW_REQUIRED,
                        review_required=True,
                        explanation_trace=tuple(
                            (
                                *item.explanation_trace,
                                "Multiple targets make this a split candidate.",
                            )
                        ),
                    )
                )
            diagnostics.append(
                LineageDiagnostic(
                    code=LineageDiagnosticCode.AMBIGUOUS_SPLIT,
                    severity=HistoryDiagnosticSeverity.WARNING,
                    message="One source symbol has multiple strong target candidates.",
                    source_unit_id=source_id,
                )
            )
        for target_id in sorted(ambiguous_target):
            values = sorted(by_target[target_id], key=_candidate_sort_key)
            for item in values:
                review.append(
                    replace(
                        item,
                        candidate_id=_candidate_id(
                            item.source_unit_id,
                            item.target_unit_id,
                            LineageRelationType.MERGED_FROM,
                        ),
                        relation_type=LineageRelationType.MERGED_FROM,
                        status=LineageStatus.REVIEW_REQUIRED,
                        review_required=True,
                        explanation_trace=tuple(
                            (
                                *item.explanation_trace,
                                "Multiple sources make this a merge candidate.",
                            )
                        ),
                    )
                )
            diagnostics.append(
                LineageDiagnostic(
                    code=LineageDiagnosticCode.AMBIGUOUS_MERGE,
                    severity=HistoryDiagnosticSeverity.WARNING,
                    message="One target symbol has multiple strong source candidates.",
                    target_unit_id=target_id,
                )
            )
        confirmed = sorted(confirmed, key=_lineage_sort_key)
        deduplicated_review = {
            item.candidate_id: item for item in sorted(review, key=_candidate_sort_key)
        }
        candidates = tuple(sorted(deduplicated_review.values(), key=_candidate_sort_key))
        if confirmed:
            status = LineageStatus.CONFIRMED
        elif candidates:
            status = LineageStatus.REVIEW_REQUIRED
        else:
            status = LineageStatus.NO_MATCH
            if sources and targets:
                diagnostics.append(
                    LineageDiagnostic(
                        code=LineageDiagnosticCode.NO_MATCH,
                        severity=HistoryDiagnosticSeverity.INFO,
                        message="No symbol pair met the deterministic lineage candidate threshold.",
                    )
                )
        trace = SymbolLineageTrace(
            contract_version=request.contract_version,
            project_id=request.project_id,
            repository_id=request.repository_id,
            source_commit_sha=request.source_commit_sha,
            target_commit_sha=request.target_commit_sha,
            source_generation_id=request.source_generation_id,
            target_generation_id=request.target_generation_id,
            acl_ref=request.acl_ref,
            source_symbols=len(sources),
            target_symbols=len(targets),
            comparisons=comparisons,
            confirmed=len(confirmed),
            candidates=len(candidates),
        )
        return SymbolLineageResult(
            status=status,
            lineages=tuple(confirmed),
            candidates=candidates,
            diagnostics=tuple(diagnostics),
            trace=trace,
        )

    @staticmethod
    def _validate_scope(
        request: SymbolLineageRequest,
        sources: tuple[HistoricalUnit, ...],
        targets: tuple[HistoricalUnit, ...],
        hints: tuple[GitRenameHint, ...],
    ) -> None:
        source_scope = (
            request.project_id,
            request.repository_id,
            request.source_commit_sha,
            request.source_generation_id,
            request.acl_ref,
        )
        target_scope = (
            request.project_id,
            request.repository_id,
            request.target_commit_sha,
            request.target_generation_id,
            request.acl_ref,
        )
        for unit in sources:
            if _unit_scope(unit) != source_scope:
                raise HistoryScopeError("source lineage unit scope/version/ACL mismatch")
        for unit in targets:
            if _unit_scope(unit) != target_scope:
                raise HistoryScopeError("target lineage unit scope/version/ACL mismatch")
        for hint in hints:
            hint_scope = (
                hint.project_id,
                hint.repository_id,
                hint.source_commit_sha,
                hint.target_commit_sha,
                hint.source_generation_id,
                hint.target_generation_id,
                hint.acl_ref,
            )
            expected = (
                request.project_id,
                request.repository_id,
                request.source_commit_sha,
                request.target_commit_sha,
                request.source_generation_id,
                request.target_generation_id,
                request.acl_ref,
            )
            if hint_scope != expected:
                raise HistoryScopeError("Git rename hint scope/version/ACL mismatch")


def _canonical_symbol_units(units: Iterable[HistoricalUnit]) -> tuple[HistoricalUnit, ...]:
    values = tuple(units)
    if not all(isinstance(item, HistoricalUnit) for item in values):
        raise TypeError("lineage units must contain HistoricalUnit values")
    symbols = [item for item in values if item.is_symbol and item.unit_type == "symbol.ast_block"]
    unique: dict[tuple[str, str], HistoricalUnit] = {}
    for item in symbols:
        unique[(item.qualified_name, item.locator)] = item
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.qualified_name,
                item.file_path,
                item.start_line,
                item.unit_id,
            ),
        )
    )


def _unit_scope(unit: HistoricalUnit) -> tuple[str, str, str, str, str]:
    return (
        unit.project_id,
        unit.repository_id,
        unit.commit_sha,
        unit.generation_id,
        unit.acl_ref,
    )


def _score_candidate(
    request: SymbolLineageRequest,
    source: HistoricalUnit,
    target: HistoricalUnit,
    hint_pairs: Mapping[tuple[str, str], GitRenameHint],
) -> LineageCandidate | None:
    exact_name_path = (
        source.qualified_name == target.qualified_name and source.file_path == target.file_path
    )
    rename_hint = hint_pairs.get((source.file_path, target.file_path))
    signature_value = _signature_similarity(
        source.signature,
        target.signature,
        source.symbol_name,
        target.symbol_name,
    )
    body_value = _body_similarity(
        source.body,
        target.body,
        source.symbol_name,
        target.symbol_name,
    )
    neighbor_value = _jaccard(
        {_leaf_name(item) for item in source.neighbor_symbols},
        {_leaf_name(item) for item in target.neighbor_symbols},
    )
    signals = (
        _signal(
            "exact-qualified-name-path",
            0.4,
            1.0 if exact_name_path else 0.0,
            "Qualified name and repository path match exactly.",
        ),
        _signal(
            "same-symbol-name",
            0.3,
            1.0 if source.symbol_name == target.symbol_name else 0.0,
            "The parsed symbol name is unchanged across versions.",
        ),
        _signal(
            "same-containing-path",
            0.25,
            1.0 if source.file_path == target.file_path else 0.0,
            "The containing repository-relative path is unchanged.",
        ),
        _signal(
            "git-rename",
            0.2,
            rename_hint.similarity if rename_hint is not None else 0.0,
            "Git rename detection links the containing paths.",
        ),
        _signal(
            "signature",
            0.15,
            signature_value,
            "Normalized symbol signatures were compared deterministically.",
        ),
        _signal(
            "ast-body",
            0.2,
            body_value,
            "Normalized AST-unit bodies were compared deterministically.",
        ),
        _signal(
            "neighbor-symbols",
            0.05,
            neighbor_value,
            "Neighbor symbol-name overlap provides bounded surrounding context.",
        ),
    )
    score = round(sum(item.contribution for item in signals), 12)
    same_leaf = _leaf_name(source.qualified_name) == _leaf_name(target.qualified_name)
    if exact_name_path:
        relation = LineageRelationType.SAME_SYMBOL_AS
    elif source.file_path != target.file_path and same_leaf:
        relation = LineageRelationType.MOVED_TO
    elif source.qualified_name != target.qualified_name:
        relation = LineageRelationType.RENAMED_TO
    else:
        relation = LineageRelationType.SAME_SYMBOL_AS
    trace = tuple(
        f"{item.name}={item.value:.6f}*{item.weight:.6f}->{item.contribution:.6f}"
        for item in signals
        if item.value > 0.0
    )
    return LineageCandidate(
        contract_version=request.contract_version,
        candidate_id=_candidate_id(source.unit_id, target.unit_id, relation),
        project_id=request.project_id,
        repository_id=request.repository_id,
        source_commit_sha=request.source_commit_sha,
        target_commit_sha=request.target_commit_sha,
        source_generation_id=request.source_generation_id,
        target_generation_id=request.target_generation_id,
        acl_ref=request.acl_ref,
        source_unit_id=source.unit_id,
        target_unit_id=target.unit_id,
        source_locator=source.locator,
        target_locator=target.locator,
        source_qualified_name=source.qualified_name,
        target_qualified_name=target.qualified_name,
        source_path=source.file_path,
        target_path=target.file_path,
        relation_type=relation,
        score=float(min(1.0, score)),
        status=LineageStatus.CANDIDATE,
        review_required=True,
        signals=signals,
        explanation_trace=trace or ("No positive lineage signal.",),
    )


def _signal(
    name: str,
    weight: float,
    value: float,
    explanation: str,
) -> LineageSignal:
    bounded = min(1.0, max(0.0, float(value)))
    return LineageSignal(
        name=name,
        weight=float(weight),
        value=float(round(bounded, 12)),
        contribution=float(round(weight * bounded, 12)),
        explanation=explanation,
    )


def _signature_similarity(
    left: str | None,
    right: str | None,
    left_name: str,
    right_name: str,
) -> float:
    if not left or not right:
        return 0.0
    normalized_left = _normalized_signature(left, left_name)
    normalized_right = _normalized_signature(right, right_name)
    return SequenceMatcher(None, normalized_left, normalized_right, autojunk=False).ratio()


def _normalized_signature(value: str, symbol_name: str) -> str:
    compact = " ".join(value.split())
    if symbol_name:
        compact = re.sub(
            rf"(?<![A-Za-z0-9_$]){re.escape(symbol_name)}(?![A-Za-z0-9_$])",
            "<symbol>",
            compact,
            count=1,
        )
    return compact


def _body_similarity(
    left: str,
    right: str,
    left_name: str,
    right_name: str,
) -> float:
    left_normalized = re.sub(
        rf"(?<![A-Za-z0-9_$]){re.escape(left_name)}(?![A-Za-z0-9_$])",
        "<symbol>",
        left,
        count=1,
    )
    right_normalized = re.sub(
        rf"(?<![A-Za-z0-9_$]){re.escape(right_name)}(?![A-Za-z0-9_$])",
        "<symbol>",
        right,
        count=1,
    )
    token_pattern = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*|\d+(?:\.\d+)?|[^\s]")
    left_tokens = token_pattern.findall(left_normalized)
    right_tokens = token_pattern.findall(right_normalized)
    if not left_tokens or not right_tokens:
        return 1.0 if left_normalized.strip() == right_normalized.strip() else 0.0
    return SequenceMatcher(None, left_tokens, right_tokens, autojunk=False).ratio()


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def _leaf_name(qualified_name: str) -> str:
    return qualified_name.rsplit(".", 1)[-1].rsplit("::", 1)[-1]


def _candidate_id(
    source_unit_id: str,
    target_unit_id: str,
    relation_type: LineageRelationType,
) -> str:
    digest = hashlib.sha256(
        _canonical_json(
            {
                "relation_type": relation_type.value,
                "source_unit_id": source_unit_id,
                "target_unit_id": target_unit_id,
            }
        )
    ).hexdigest()
    return f"symbol-lineage-candidate://sha256/{digest}"


def _deterministic_confirmation(candidate: LineageCandidate) -> bool:
    values = {signal.name: signal.value for signal in candidate.signals}
    exact = values["exact-qualified-name-path"] == 1.0
    git_rename = values["git-rename"] >= 0.5
    same_body = values["ast-body"] >= 0.999999
    same_signature = values["signature"] >= 0.999999
    return exact or (git_rename and same_body) or (same_body and same_signature)


def _confirmed_lineage(candidate: LineageCandidate) -> SymbolLineage:
    relation = CodeRelationType(candidate.relation_type.value)
    digest = hashlib.sha256(
        _canonical_json(
            {
                "candidate_id": candidate.candidate_id,
                "relation_type": relation.value,
                "source_commit_sha": candidate.source_commit_sha,
                "target_commit_sha": candidate.target_commit_sha,
            }
        )
    ).hexdigest()
    return SymbolLineage(
        contract_version=candidate.contract_version,
        lineage_id=f"symbol-lineage://sha256/{digest}",
        project_id=candidate.project_id,
        repository_id=candidate.repository_id,
        source_commit_sha=candidate.source_commit_sha,
        target_commit_sha=candidate.target_commit_sha,
        source_generation_id=candidate.source_generation_id,
        target_generation_id=candidate.target_generation_id,
        acl_ref=candidate.acl_ref,
        source_unit_id=candidate.source_unit_id,
        target_unit_id=candidate.target_unit_id,
        source_locator=candidate.source_locator,
        target_locator=candidate.target_locator,
        relation_type=relation,
        confidence=candidate.score,
        status=LineageStatus.CONFIRMED,
        review_required=False,
        signals=candidate.signals,
        explanation_trace=tuple(
            (*candidate.explanation_trace, "Deterministic confirmation gate satisfied.")
        ),
    )


_RELATION_SORT = {
    relation: index
    for index, relation in enumerate(
        (
            LineageRelationType.SAME_SYMBOL_AS,
            LineageRelationType.RENAMED_TO,
            LineageRelationType.MOVED_TO,
            LineageRelationType.SPLIT_INTO,
            LineageRelationType.MERGED_FROM,
        )
    )
}


def _candidate_sort_key(candidate: LineageCandidate) -> tuple[Any, ...]:
    return (
        -candidate.score,
        _RELATION_SORT[candidate.relation_type],
        candidate.source_qualified_name,
        candidate.target_qualified_name,
        candidate.source_locator,
        candidate.target_locator,
        candidate.candidate_id,
    )


def _lineage_sort_key(lineage: SymbolLineage) -> tuple[Any, ...]:
    return (
        -lineage.confidence,
        lineage.relation_type.value,
        lineage.source_locator,
        lineage.target_locator,
        lineage.lineage_id,
    )


@runtime_checkable
class CodeHistoryHookStore(Protocol):
    """Production read surface for exact current-generation history expansion."""

    def connection(self) -> Any: ...

    def load_code_unit(
        self,
        unit_id: str,
        **scope: Any,
    ) -> dict[str, Any] | None: ...


class CodeHistoryDiffExpansionHook:
    """Exact governed history hook backed by stored Git facts and C6 diff mapping."""

    hook_version = "c6-production-history-diff-hook-v1"

    def __init__(
        self,
        store: CodeHistoryHookStore,
        *,
        mapper: Any | None = None,
    ) -> None:
        if not isinstance(store, CodeHistoryHookStore):
            raise TypeError("store must implement CodeHistoryHookStore")
        if mapper is None:
            from .diff_symbol_v2 import DiffSymbolMapper

            mapper = DiffSymbolMapper()
        if not callable(getattr(mapper, "map_hunk", None)):
            raise TypeError("mapper must expose map_hunk()")
        self.store = store
        self.mapper = mapper
        self._lkg: dict[tuple[Any, ...], tuple[tuple[str, str], ...]] = {}
        self._lock = threading.RLock()

    def expand(
        self,
        *,
        request: EvidenceSearchRequest,
        profile: Any,
        seeds: tuple[CodeRetrievalCandidate, ...],
        limit: int,
        deadline: float | None,
    ) -> CodeOptionalHookResult:
        if not isinstance(request, EvidenceSearchRequest):
            raise TypeError("request must be an EvidenceSearchRequest")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("history hook limit must be a positive integer")
        if deadline is not None and time.monotonic() >= deadline:
            return self._result(
                CodeOptionalHookStatus.TIMEOUT,
                reason="history hook deadline reached before exact scope resolution",
            )
        try:
            scope = self._scope(request, profile, seeds)
            seed_rows = self._seed_rows(scope, seeds)
        except (HistoryError, TypeError, ValueError) as error:
            return self._result(
                CodeOptionalHookStatus.UNAVAILABLE,
                reason=f"history scope unavailable: {type(error).__name__}",
            )

        key = (
            scope["project_id"],
            scope["repository_id"],
            scope["commit_sha"],
            scope["generation_id"],
            scope["acl_ref"],
            request.query,
            tuple(sorted((seed.entity_id, seed.retrieval_unit_id) for seed in seeds)),
        )
        try:
            selected = self._select(
                scope,
                seed_rows,
                limit=limit,
                deadline=deadline,
            )
        except TimeoutError:
            return self._result(
                CodeOptionalHookStatus.TIMEOUT,
                reason="history diff mapping exceeded the governed deadline",
            )
        except Exception as error:
            del error
            with self._lock:
                cached = self._lkg.get(key)
            if cached is None:
                return self._result(
                    CodeOptionalHookStatus.UNAVAILABLE,
                    reason="history expansion failed without an exact same-scope LKG",
                )
            selected = tuple(
                seed
                for seed, _ in seed_rows
                if (seed.entity_id, seed.retrieval_unit_id) in set(cached)
            )
            return self._from_candidates(
                selected,
                reason="exact same-scope history LKG reused after source failure",
            )

        identities = tuple(sorted((seed.entity_id, seed.retrieval_unit_id) for seed in selected))
        with self._lock:
            self._lkg[key] = identities
        return self._from_candidates(selected)

    def _scope(
        self,
        request: EvidenceSearchRequest,
        profile: Any,
        seeds: tuple[CodeRetrievalCandidate, ...],
    ) -> dict[str, str]:
        repositories = tuple(request.scope.repository_ids)
        commit = request.scope.commit
        project_id = request.scope.project_id
        target_ref = str(getattr(profile, "target_ref", "") or "")
        if (
            len(repositories) != 1
            or not project_id
            or commit is None
            or _FULL_SHA.fullmatch(commit) is None
            or target_ref != commit
            or request.scope.enforce_acl is not True
        ):
            raise HistoryScopeError("history requires one governed exact commit scope")
        repository_id = repositories[0]
        with self.store.connection() as database:
            repository = database.execute(
                """
                SELECT project_id, id, active_generation_id, head_commit, acl_ref, status
                FROM repositories
                WHERE id=? AND project_id=?
                """,
                (repository_id, project_id),
            ).fetchone()
            if repository is None:
                raise HistoryScopeError("history repository is unavailable")
            generation_id = str(repository["active_generation_id"] or "")
            generation = database.execute(
                """
                SELECT commit_sha, status
                FROM index_generations
                WHERE id=? AND repository_id=?
                """,
                (generation_id, repository_id),
            ).fetchone()
        acl_ref = str(repository["acl_ref"] or "")
        allowed = set(request.scope.allowed_acl_refs)
        if (
            str(repository["head_commit"]) != commit
            or str(repository["status"]) != "ready"
            or generation is None
            or str(generation["commit_sha"]) != commit
            or str(generation["status"]) != "published"
            or not acl_ref
            or acl_ref not in allowed
        ):
            raise HistoryScopeError("history repository/generation/ACL watermark mismatch")
        if any(
            (
                seed.repository_id,
                seed.stable_version,
                seed.source_generation,
                seed.acl_ref,
            )
            != (repository_id, commit, generation_id, acl_ref)
            for seed in seeds
        ):
            raise HistoryScopeError("history seeds conflict with the governed watermark")
        return {
            "project_id": project_id,
            "repository_id": repository_id,
            "commit_sha": commit,
            "generation_id": generation_id,
            "acl_ref": acl_ref,
        }

    def _seed_rows(
        self,
        scope: Mapping[str, str],
        seeds: tuple[CodeRetrievalCandidate, ...],
    ) -> tuple[tuple[CodeRetrievalCandidate, dict[str, Any]], ...]:
        values = []
        for seed in seeds:
            row = self.store.load_code_unit(
                seed.retrieval_unit_id,
                project_id=scope["project_id"],
                repository_id=scope["repository_id"],
                generation_id=scope["generation_id"],
                allowed_acl_refs=(scope["acl_ref"],),
                active_only=True,
            )
            if row is None:
                raise HistoryScopeError("history seed retrieval unit is unavailable")
            actual = (
                str(row.get("entity_id") or ""),
                str(row.get("repository_id") or ""),
                str(row.get("generation_id") or ""),
                str(row.get("acl_ref") or ""),
            )
            expected = (
                seed.entity_id,
                seed.repository_id,
                seed.source_generation,
                seed.acl_ref,
            )
            if actual != expected:
                raise HistoryScopeError("history seed retrieval unit provenance mismatch")
            values.append((seed, row))
        return tuple(values)

    def _select(
        self,
        scope: Mapping[str, str],
        seed_rows: tuple[tuple[CodeRetrievalCandidate, dict[str, Any]], ...],
        *,
        limit: int,
        deadline: float | None,
    ) -> tuple[CodeRetrievalCandidate, ...]:
        from .diff_symbol_v2 import (
            DiffChangeType,
            DiffHunkVersion,
            DiffSymbolMatchStatus,
            DiffSymbolSide,
            SymbolVersionRef,
        )
        from .graph_v2 import CodeGraphEntityType

        refs: list[SymbolVersionRef] = []
        seed_by_unit: dict[str, CodeRetrievalCandidate] = {}
        paths = set()
        for seed, row in seed_rows:
            unit_type = str(row.get("unit_type") or "")
            qualified_name = str(row.get("qualified_name") or "")
            if unit_type == "symbol.ast_block" and qualified_name:
                entity_type = CodeGraphEntityType.CODE_SYMBOL
                symbol_name = qualified_name.rsplit(".", 1)[-1]
                symbol_kind = str(row.get("ast_node_type") or "unknown")
            elif unit_type == "file.surface":
                entity_type = CodeGraphEntityType.FILE_VERSION
                symbol_name = ""
                symbol_kind = ""
                qualified_name = ""
            else:
                continue
            path = str(row.get("path") or "")
            if not path:
                continue
            version_id = (
                "code-symbol-version://sha256/"
                + hashlib.sha256(
                    "\x1f".join(
                        (
                            seed.entity_id,
                            seed.retrieval_unit_id,
                            scope["commit_sha"],
                            scope["generation_id"],
                        )
                    ).encode()
                ).hexdigest()
            )
            refs.append(
                SymbolVersionRef(
                    project_id=scope["project_id"],
                    repository_id=scope["repository_id"],
                    commit_sha=scope["commit_sha"],
                    generation_id=scope["generation_id"],
                    acl_ref=scope["acl_ref"],
                    namespace=(f"code/current/{scope['repository_id']}/{scope['generation_id']}"),
                    requested_ref=scope["commit_sha"],
                    symbol_version_id=version_id,
                    unit_id=seed.retrieval_unit_id,
                    entity_id=seed.entity_id,
                    entity_type=entity_type,
                    path=path,
                    language=str(row.get("language") or "unknown"),
                    symbol_name=symbol_name,
                    symbol_kind=symbol_kind,
                    qualified_name=qualified_name,
                    signature=str(row.get("signature") or "") or None,
                    unit_type=unit_type,
                    ast_node_type=str(row.get("ast_node_type") or "unknown"),
                    structural_path=str(
                        (row.get("metadata") or {}).get("structural_path") or unit_type
                    ),
                    start_line=int(row.get("start_line") or 1),
                    end_line=int(row.get("end_line") or row.get("start_line") or 1),
                    evidence_locator=seed.locator,
                )
            )
            seed_by_unit[seed.retrieval_unit_id] = seed
            paths.add(path)
        if not refs:
            return ()

        marks = ",".join("?" for _ in paths)
        with self.store.connection() as database:
            rows = database.execute(
                f"""
                SELECT *
                FROM diff_hunks
                WHERE project_id=? AND repository_id=? AND commit_sha=? AND acl_ref=?
                  AND (path IN ({marks}) OR old_path IN ({marks}))
                ORDER BY path, new_start, id
                LIMIT ?
                """,
                (
                    scope["project_id"],
                    scope["repository_id"],
                    scope["commit_sha"],
                    scope["acl_ref"],
                    *sorted(paths),
                    *sorted(paths),
                    min(64, max(limit * 4, limit)),
                ),
            ).fetchall()

        selected: dict[str, CodeRetrievalCandidate] = {}
        for row in rows:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("history diff mapping deadline exceeded")
            parent_sha = str(row["parent_sha"] or "")
            if _FULL_SHA.fullmatch(parent_sha) is None:
                continue
            change_type = _history_diff_change_type(str(row["change_type"]))
            old_start = int(row["old_start"] or 0)
            old_count = int(row["old_count"] or 0)
            new_start = int(row["new_start"] or 0)
            new_count = int(row["new_count"] or 0)
            old_path = str(row["old_path"] or "") or None
            if change_type is DiffChangeType.ADD:
                old_start, old_count, old_path = 0, 0, None
            elif change_type is DiffChangeType.DELETE:
                new_start, new_count = 0, 0
                old_path = old_path or str(row["path"])
            elif change_type is DiffChangeType.MODIFY:
                old_path = None
            hunk = DiffHunkVersion.create(
                project_id=scope["project_id"],
                repository_id=scope["repository_id"],
                parent_commit_sha=parent_sha,
                target_commit_sha=scope["commit_sha"],
                parent_generation_id=f"history:{parent_sha}",
                target_generation_id=scope["generation_id"],
                acl_ref=scope["acl_ref"],
                path=str(row["path"]),
                old_path=old_path,
                change_type=change_type,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                patch=str(row["patch"]),
                evidence_locator=str(row["source_locator"]),
                hunk_id=str(row["id"]),
                is_binary="Binary files " in str(row["patch"]),
            )
            treatment = self.mapper.map_hunk(
                hunk,
                target_symbols=refs,
            )
            for match in treatment.matches:
                if (
                    match.side is DiffSymbolSide.NEW
                    and match.status is DiffSymbolMatchStatus.CONFIRMED
                    and match.symbol.unit_id in seed_by_unit
                ):
                    selected[match.symbol.unit_id] = seed_by_unit[match.symbol.unit_id]
                    if len(selected) >= limit:
                        break
            if len(selected) >= limit:
                break
        return tuple(selected[key] for key in sorted(selected))

    def _from_candidates(
        self,
        candidates: Sequence[CodeRetrievalCandidate],
        *,
        reason: str = "",
    ) -> CodeOptionalHookResult:
        expanded = tuple(
            _history_hook_candidate(candidate, rank) for rank, candidate in enumerate(candidates)
        )
        return self._result(
            (
                CodeOptionalHookStatus.COMPLETE
                if expanded
                else CodeOptionalHookStatus.COMPLETE_NO_MATCH
            ),
            candidates=expanded,
            reason=reason,
        )

    def _result(
        self,
        status: CodeOptionalHookStatus,
        *,
        candidates: tuple[CodeRetrievalCandidate, ...] = (),
        reason: str = "",
    ) -> CodeOptionalHookResult:
        return CodeOptionalHookResult(
            channel=CodeRetrievalChannel.HISTORY,
            status=status,
            candidates=candidates,
            hook_version=self.hook_version,
            reason=reason,
        )


def _history_diff_change_type(value: str) -> Any:
    from .diff_symbol_v2 import DiffChangeType

    normalized = value.strip().upper()
    if normalized.startswith(("R", "C")):
        return DiffChangeType.RENAME
    return {
        "A": DiffChangeType.ADD,
        "D": DiffChangeType.DELETE,
        "M": DiffChangeType.MODIFY,
        "T": DiffChangeType.MODIFY,
    }.get(normalized, DiffChangeType.MODIFY)


def _history_hook_candidate(
    candidate: CodeRetrievalCandidate,
    rank: int,
) -> CodeRetrievalCandidate:
    payload = candidate.model_dump(mode="python", round_trip=True)
    scores = [
        item
        for item in candidate.raw_channel_scores
        if item.channel is not CodeRetrievalChannel.HISTORY
    ]
    ranks = [
        item
        for item in candidate.raw_channel_ranks
        if item.channel is not CodeRetrievalChannel.HISTORY
    ]
    scores.append(
        CodeChannelScore(
            channel=CodeRetrievalChannel.HISTORY,
            score=1.0 / (rank + 1),
        )
    )
    ranks.append(CodeChannelRank(channel=CodeRetrievalChannel.HISTORY, rank=rank))
    payload.update(
        raw_channel_scores=tuple(scores),
        raw_channel_ranks=tuple(ranks),
        within_source_rank=rank,
        source_fused_score=max(candidate.source_fused_score, 1.0 / (rank + 1)),
        role=CodeCandidateRole.HISTORY,
        fact_status=CodeFactStatus.OBSERVED,
        derivation=CodeDerivation.RULE,
    )
    return CodeRetrievalCandidate.model_validate(payload)


def _publication_to_json(publication: HistoryPublication) -> str:
    return _canonical_json(asdict(publication)).decode("utf-8")


def _publication_from_json(payload: str) -> HistoryPublication:
    raw = json.loads(payload)
    provenance_by_key: dict[tuple[str, ...], HistoryProvenance] = {}

    def provenance(value: Mapping[str, Any]) -> HistoryProvenance:
        key = tuple(str(value[name]) for name in sorted(value))
        if key not in provenance_by_key:
            provenance_by_key[key] = HistoryProvenance(**value)
        return provenance_by_key[key]

    files = tuple(
        HistoricalFile(
            **{
                **value,
                "status": HistoricalFileStatus(value["status"]),
                "provenance": provenance(value["provenance"]),
                "diagnostics": tuple(
                    HistoryDiagnostic(
                        code=HistoryDiagnosticCode(item["code"]),
                        severity=HistoryDiagnosticSeverity(item["severity"]),
                        message=item["message"],
                        path=item.get("path"),
                    )
                    for item in value.get("diagnostics", ())
                ),
            }
        )
        for value in raw["files"]
    )
    units = tuple(
        HistoricalUnit(
            **{
                **value,
                "provenance": provenance(value["provenance"]),
                "neighbor_symbols": tuple(value.get("neighbor_symbols", ())),
            }
        )
        for value in raw["units"]
    )
    diagnostics = tuple(
        HistoryDiagnostic(
            code=HistoryDiagnosticCode(item["code"]),
            severity=HistoryDiagnosticSeverity(item["severity"]),
            message=item["message"],
            path=item.get("path"),
        )
        for item in raw.get("diagnostics", ())
    )
    namespace_raw = raw["namespace"]
    namespace = HistoryNamespace(
        **{
            **namespace_raw,
            "kind": HistoryNamespaceKind(namespace_raw["kind"]),
        }
    )
    selection_raw = raw["selection"]
    selection = HistoryMaterializationSelection(
        **{
            **selection_raw,
            "paths": tuple(selection_raw["paths"]),
            "supported_languages": tuple(selection_raw["supported_languages"]),
        }
    )
    trace_raw = raw["trace"]
    trace = HistoryTrace(
        **{
            **trace_raw,
            "status": HistoryMaterializationStatus(trace_raw["status"]),
            "git_argv": tuple(tuple(item) for item in trace_raw["git_argv"]),
            "diagnostics": tuple(
                HistoryDiagnostic(
                    code=HistoryDiagnosticCode(item["code"]),
                    severity=HistoryDiagnosticSeverity(item["severity"]),
                    message=item["message"],
                    path=item.get("path"),
                )
                for item in trace_raw.get("diagnostics", ())
            ),
        }
    )
    return HistoryPublication(
        **{
            **raw,
            "namespace": namespace,
            "selection": selection,
            "status": HistoryMaterializationStatus(raw["status"]),
            "files": files,
            "units": units,
            "diagnostics": diagnostics,
            "trace": trace,
            "pin_reasons": tuple(HistoryPinReason(item) for item in raw["pin_reasons"]),
        }
    )


__all__ = [
    "DEFAULT_HISTORY_PROFILE",
    "HISTORY_CONTRACT_VERSION",
    "HISTORY_SCHEMA_VERSION",
    "LINEAGE_CONTRACT_VERSION",
    "GitRenameHint",
    "HistoricalFile",
    "HistoricalFileStatus",
    "HistoricalMaterializer",
    "HistoricalPublicationStore",
    "HistoricalUnit",
    "CodeHistoryDiffExpansionHook",
    "CodeHistoryHookStore",
    "HistoryDiagnostic",
    "HistoryDiagnosticCode",
    "HistoryDiagnosticSeverity",
    "HistoryError",
    "HistoryEvictionResult",
    "HistoryGitError",
    "HistoryMaterializationPolicy",
    "HistoryMaterializationRequest",
    "HistoryMaterializationSelection",
    "HistoryMaterializationStatus",
    "HistoryMaterializer",
    "HistoryNamespace",
    "HistoryNamespaceKind",
    "HistoryPinReason",
    "HistoryProvenance",
    "HistoryPublication",
    "HistoryPublicationError",
    "HistoryScopeError",
    "HistoryStatus",
    "HistoryTrace",
    "InMemoryHistoricalPublicationStore",
    "LineageCandidate",
    "LineageDiagnostic",
    "LineageDiagnosticCode",
    "LineageRelationType",
    "LineageSignal",
    "LineageStatus",
    "SQLiteHistoricalPublicationStore",
    "SymbolLineage",
    "SymbolLineageRequest",
    "SymbolLineageResolver",
    "SymbolLineageResult",
    "SymbolLineageTrace",
    "history_content_key",
]
