"""Task-specific, provenance-preserving Code context assembly.

The builder in this module is deliberately pure and storage independent.  It
does not retrieve, hydrate, summarize, or infer source text.  The only source
text it may render is an existing :class:`CodeContextBlock` carried by the
strict input result.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from .contracts import (
    CodeCandidateRole,
    CodeContextBlock,
    CodeRelationPath,
    CodeRelationType,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeSourceResult,
    CodeSourceStatus,
    CodeTask,
    CodeVersionAlignment,
)
from .query_profile_v2 import (
    CodeSourceBlockPublicationIdentity,
    CodeSourceFusionSearchResult,
    CodeSourceFusionTrace,
    CodeSourceScopeAttestation,
    CodeSourceScopeAttestationError,
    _verify_code_source_scope_attestation,
)

CONTEXT_BUILDER_CONTRACT_VERSION = "c7-code-task-context-v1"
CONTEXT_BUILDER_VERSION = "c7-code-task-context-builder-v1"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DIRTY_VERSION = re.compile(
    r"^(?P<base>[0-9a-f]{40}(?:[0-9a-f]{24})?)"
    r"\+dirty\.(?P<manifest>[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?)$"
)
_CLEAN_VERSION = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_LINE_FRAGMENT = re.compile(r"^L(?P<start>[1-9]\d*)-L(?P<end>[1-9]\d*)$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ContextBuildError(ValueError):
    """Base fail-closed context construction error."""


class ContextBuildScopeError(ContextBuildError):
    """Input provenance conflicts with the caller's frozen scope attestation."""


class ContextBuildLocatorError(ContextBuildScopeError):
    """A source locator cannot prove a safe repository-relative location."""


class ContextBuildRefusalError(ContextBuildError):
    """The requested task is not eligible for the C7-01 builder."""

    def __init__(self, refusal: ContextRefusal) -> None:
        self.refusal = refusal
        super().__init__(refusal.reason)


class ContextRole(StrEnum):
    """Closed task-role vocabulary used by the four authorized templates."""

    TARGET_SYMBOL = "target_symbol"
    SIGNATURE_DOC = "signature_doc"
    RELEVANT_AST_BLOCKS = "relevant_ast_blocks"
    REQUIRED_IMPORTS_TYPES = "required_imports_types"
    DIRECT_CALLERS_CALLEES = "direct_callers_callees"
    RELATED_TESTS = "related_tests"
    VERSION_LOCATOR = "version_locator"

    ERROR_STACK = "error_stack"
    SUSPECT_TARGET = "suspect_target"
    GRAPH_PATH = "graph_path"
    RELEVANT_BLOCK = "relevant_block"
    RECENT_DIFF = "recent_diff"
    FAILED_PASSED_VALIDATION = "failed_passed_validation"
    ALTERNATIVE_SUSPECTS = "alternative_suspects"

    CHANGED_SYMBOL = "changed_symbol"
    INCOMING_REFERENCES_CALLS = "incoming_references_calls"
    IMPLEMENTATIONS_OVERRIDES = "implementations_overrides"
    TESTS_COVERAGE = "tests_coverage"
    AFFECTED_FILES_MODULES = "affected_files_modules"
    UNRESOLVED_UNKNOWN = "unresolved_unknown"

    EDITABLE_TARGET = "editable_target"
    CONTRACT_TYPE_PARENT = "contract_type_parent"
    CALLERS_DEPENDENCIES = "callers_dependencies"
    NEARBY_TESTS = "nearby_tests"
    VALIDATION_REQUIREMENT = "validation_requirement"
    DO_NOT_EDIT_GENERATED = "do_not_edit_generated"


class ContextBuildStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ContextMissingStatus(StrEnum):
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    PRUNED = "pruned"


class ContextRefusalCode(StrEnum):
    UNKNOWN_TASK = "unknown_task"
    UNSUPPORTED_TASK = "unsupported_task"
    UPSTREAM_REFUSAL = "upstream_refusal"


class ContextMetricStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


_IMPLEMENTATION_ROLES = (
    ContextRole.TARGET_SYMBOL,
    ContextRole.SIGNATURE_DOC,
    ContextRole.RELEVANT_AST_BLOCKS,
    ContextRole.REQUIRED_IMPORTS_TYPES,
    ContextRole.DIRECT_CALLERS_CALLEES,
    ContextRole.RELATED_TESTS,
    ContextRole.VERSION_LOCATOR,
)
_BUG_LOCALIZATION_ROLES = (
    ContextRole.ERROR_STACK,
    ContextRole.SUSPECT_TARGET,
    ContextRole.GRAPH_PATH,
    ContextRole.RELEVANT_BLOCK,
    ContextRole.RECENT_DIFF,
    ContextRole.FAILED_PASSED_VALIDATION,
    ContextRole.ALTERNATIVE_SUSPECTS,
)
_IMPACT_ROLES = (
    ContextRole.CHANGED_SYMBOL,
    ContextRole.INCOMING_REFERENCES_CALLS,
    ContextRole.IMPLEMENTATIONS_OVERRIDES,
    ContextRole.TESTS_COVERAGE,
    ContextRole.AFFECTED_FILES_MODULES,
    ContextRole.UNRESOLVED_UNKNOWN,
)
_MODIFICATION_ROLES = (
    ContextRole.EDITABLE_TARGET,
    ContextRole.CONTRACT_TYPE_PARENT,
    ContextRole.CALLERS_DEPENDENCIES,
    ContextRole.NEARBY_TESTS,
    ContextRole.RECENT_DIFF,
    ContextRole.VALIDATION_REQUIREMENT,
    ContextRole.DO_NOT_EDIT_GENERATED,
)

CODE_TASK_CONTEXT_ROLE_ORDER: Mapping[CodeTask, tuple[ContextRole, ...]] = MappingProxyType(
    {
        CodeTask.IMPLEMENTATION: _IMPLEMENTATION_ROLES,
        CodeTask.BUG_LOCALIZATION: _BUG_LOCALIZATION_ROLES,
        CodeTask.IMPACT_ANALYSIS: _IMPACT_ROLES,
        CodeTask.CHANGE_CONTEXT: _MODIFICATION_ROLES,
    }
)

_TEMPLATE_IDS: Mapping[CodeTask, str] = MappingProxyType(
    {
        CodeTask.IMPLEMENTATION: "code-task-context/implementation@v1",
        CodeTask.BUG_LOCALIZATION: "code-task-context/bug-localization@v1",
        CodeTask.IMPACT_ANALYSIS: "code-task-context/impact@v1",
        CodeTask.CHANGE_CONTEXT: "code-task-context/modification@v1",
    }
)

_CODE_ENTITY_TYPES = frozenset(
    {
        "codesymbol",
        "coderetrievalunit",
        "fileversion",
        "packageModule".casefold(),
        "typeentity",
        "dependency",
        "configkey",
    }
)
_TEST_ENTITY_TYPES = frozenset({"testcase", "testresult", "coverage", "validationtarget"})
_HISTORY_ENTITY_TYPES = frozenset({"commit", "gitcommit", "worktree", "diffhunk", "changeset"})
_GENERATED_ENTITY_TYPES = frozenset(
    {"generated", "generatedfile", "vendored", "vendor", "readonly", "donotedit"}
)
_TEST_RELATIONS = frozenset(
    {
        CodeRelationType.TESTS,
        CodeRelationType.COVERS,
        CodeRelationType.VALIDATED_BY,
        CodeRelationType.FAILED_VALIDATION,
    }
)
_HISTORY_RELATIONS = frozenset(
    {
        CodeRelationType.SAME_SYMBOL_AS,
        CodeRelationType.RENAMED_TO,
        CodeRelationType.MOVED_TO,
    }
)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {field.name: _canonical_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical_value(item) for item in value)
    return value


class _CanonicalContextContract:
    def canonical_json_bytes(self) -> bytes:
        return json.dumps(
            _canonical_value(self),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def canonical_sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_json_bytes()).hexdigest()


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if (
        not value
        or value != value.strip()
        or any(
            unicodedata.category(character).startswith("C")
            or (character.isspace() and character != " ")
            for character in value
        )
    ):
        raise ValueError(f"{field_name} must be non-empty, trimmed, and control-free")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{field_name} must be NFC normalized")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _positive_int(value: object, field_name: str) -> int:
    result = _non_negative_int(value, field_name)
    if result == 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return result


def _safe_path(value: object, field_name: str = "repository_path") -> str:
    path = _text(value, field_name)
    if "\\" in path:
        raise ValueError(f"{field_name} must use POSIX separators")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ValueError(f"{field_name} must be repository relative")
    normalized = candidate.as_posix()
    if normalized in {"", "."} or normalized.startswith("./") or normalized != path:
        raise ValueError(f"{field_name} must identify a repository-relative path")
    return normalized


def _ordered_unique_text(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_text(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} values must be unique")
    return tuple(sorted(normalized))


def _validated_version(value: object, field_name: str = "stable_version") -> str:
    version = _text(value, field_name)
    if _CLEAN_VERSION.fullmatch(version) is None and _DIRTY_VERSION.fullmatch(version) is None:
        raise ValueError(
            f"{field_name} must be a lowercase full SHA or <full-sha>+dirty.<manifest>"
        )
    return version


@dataclass(frozen=True, slots=True)
class ContextBuildScope(_CanonicalContextContract):
    """Caller expectation checked against production-issued scope authority."""

    project_id: str
    repository_id: str
    stable_version: str
    source_generation: str
    acl_ref: str
    index_version: str
    watermark: str
    allowed_path_prefixes: tuple[str, ...] = ()
    allowed_acl_refs: tuple[str, ...] = ()
    enforce_acl: bool = True
    requested_commit: str | None = None
    requested_branch: str | None = None
    target_ref: str | None = None
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "repository_id",
            "source_generation",
            "acl_ref",
            "index_version",
            "watermark",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self,
            "stable_version",
            _validated_version(self.stable_version),
        )
        prefixes = tuple(
            sorted(_safe_path(value, "allowed_path_prefix") for value in self.allowed_path_prefixes)
        )
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("allowed_path_prefixes must be unique")
        object.__setattr__(self, "allowed_path_prefixes", prefixes)
        allowed_acl_refs = self.allowed_acl_refs or (self.acl_ref,)
        object.__setattr__(
            self,
            "allowed_acl_refs",
            _ordered_unique_text(allowed_acl_refs, "allowed_acl_ref"),
        )
        if self.acl_ref not in self.allowed_acl_refs:
            raise ValueError("acl_ref must be present in allowed_acl_refs")
        if type(self.enforce_acl) is not bool or self.enforce_acl is not True:
            raise ValueError("ContextBuildScope requires enforce_acl=true")
        object.__setattr__(
            self,
            "requested_commit",
            _optional_text(self.requested_commit, "requested_commit"),
        )
        object.__setattr__(
            self,
            "requested_branch",
            _optional_text(self.requested_branch, "requested_branch"),
        )
        object.__setattr__(
            self,
            "target_ref",
            _optional_text(self.target_ref, "target_ref"),
        )
        if self.requested_commit is not None:
            _validated_version(self.requested_commit, "requested_commit")
        if self.target_ref is not None and self.target_ref != self.stable_version:
            raise ValueError("target_ref must equal the exact stable_version")

    def permits_path(self, path: str) -> bool:
        safe = _safe_path(path)
        if not self.allowed_path_prefixes:
            return True
        return any(
            safe == prefix or safe.startswith(prefix + "/") for prefix in self.allowed_path_prefixes
        )


@dataclass(frozen=True, slots=True)
class ContextBudget(_CanonicalContextContract):
    """Deterministic source rendering ceilings."""

    max_tokens: int = 4_000
    max_characters: int = 16_000
    per_role_block_limit: int = 8
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _non_negative_int(self.max_tokens, "max_tokens")
        _non_negative_int(self.max_characters, "max_characters")
        _positive_int(self.per_role_block_limit, "per_role_block_limit")
        object.__setattr__(
            self,
            "contract_version",
            _text(self.contract_version, "contract_version"),
        )


@dataclass(frozen=True, slots=True)
class ContextRefusal(_CanonicalContextContract):
    code: ContextRefusalCode
    requested_task: str
    reason: str
    builder_version: str = CONTEXT_BUILDER_VERSION
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.code, ContextRefusalCode):
            raise TypeError("code must be a ContextRefusalCode")
        for name in ("requested_task", "reason", "builder_version", "contract_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class ContextVersion(_CanonicalContextContract):
    stable_version: str
    source_generation: str
    watermark: str
    dirty: bool
    base_version: str | None
    dirty_manifest: str | None
    display: str
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("source_generation", "watermark", "display"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self,
            "stable_version",
            _validated_version(self.stable_version),
        )
        object.__setattr__(
            self,
            "contract_version",
            _text(self.contract_version, "contract_version"),
        )
        if type(self.dirty) is not bool:
            raise TypeError("dirty must be a bool")
        object.__setattr__(
            self,
            "base_version",
            _optional_text(self.base_version, "base_version"),
        )
        object.__setattr__(
            self,
            "dirty_manifest",
            _optional_text(self.dirty_manifest, "dirty_manifest"),
        )
        if self.dirty != bool(self.base_version and self.dirty_manifest):
            raise ValueError("dirty version requires exact base_version and dirty_manifest")
        dirty_match = _DIRTY_VERSION.fullmatch(self.stable_version)
        if self.dirty != (dirty_match is not None):
            raise ValueError("dirty flag must exactly match stable_version")
        if dirty_match is not None and (
            self.base_version != dirty_match.group("base")
            or self.dirty_manifest != dirty_match.group("manifest")
        ):
            raise ValueError("dirty components must exactly match stable_version")


@dataclass(frozen=True, slots=True)
class ContextCitation(_CanonicalContextContract):
    """Stable entity citation tied to one untouched retrieval candidate."""

    entity_id: str
    retrieval_unit_id: str
    source_candidate_identity: str
    within_source_rank: int
    repository_id: str
    repository_path: str
    locator: str
    start_line: int | None
    end_line: int | None
    stable_version: str
    source_generation: str
    acl_ref: str
    candidate_role: CodeCandidateRole
    context_roles: tuple[ContextRole, ...]
    relation_path: CodeRelationPath
    path_explanation: str | None
    source_block_id: str | None
    dirty: bool
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "entity_id",
            "retrieval_unit_id",
            "repository_id",
            "locator",
            "source_generation",
            "acl_ref",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self,
            "stable_version",
            _validated_version(self.stable_version),
        )
        if not _SHA256.fullmatch(self.source_candidate_identity):
            raise ValueError("source_candidate_identity must be a canonical sha256 identity")
        object.__setattr__(
            self,
            "repository_path",
            _safe_path(self.repository_path),
        )
        _non_negative_int(self.within_source_rank, "within_source_rank")
        if (self.start_line is None) != (self.end_line is None):
            raise ValueError("citation line bounds must both be present or absent")
        if self.start_line is not None and (
            isinstance(self.start_line, bool)
            or isinstance(self.end_line, bool)
            or self.start_line < 1
            or self.end_line < self.start_line
        ):
            raise ValueError("citation line bounds must be one-based and ordered")
        if not isinstance(self.candidate_role, CodeCandidateRole):
            raise TypeError("candidate_role must be a CodeCandidateRole")
        roles = tuple(self.context_roles)
        if len(roles) != len(set(roles)) or any(
            not isinstance(role, ContextRole) for role in roles
        ):
            raise ValueError("context_roles must be unique ContextRole values")
        object.__setattr__(self, "context_roles", roles)
        if not isinstance(self.relation_path, CodeRelationPath):
            raise TypeError("relation_path must be a CodeRelationPath")
        object.__setattr__(
            self,
            "path_explanation",
            _optional_text(self.path_explanation, "path_explanation"),
        )
        if bool(self.relation_path.edges) != bool(self.path_explanation):
            raise ValueError("path explanation availability must match the existing relation path")
        object.__setattr__(
            self,
            "source_block_id",
            _optional_text(self.source_block_id, "source_block_id"),
        )
        if type(self.dirty) is not bool:
            raise TypeError("dirty must be a bool")
        if self.dirty != (_DIRTY_VERSION.fullmatch(self.stable_version) is not None):
            raise ValueError("citation dirty flag must exactly match stable_version")


@dataclass(frozen=True, slots=True)
class RetrievalContextCandidate(_CanonicalContextContract):
    """Raw governed candidate retained without rewriting rank, channels, or path."""

    candidate: CodeRetrievalCandidate
    citation: ContextCitation
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, CodeRetrievalCandidate):
            raise TypeError("candidate must be a CodeRetrievalCandidate")
        if not isinstance(self.citation, ContextCitation):
            raise TypeError("citation must be a ContextCitation")
        expected = (
            self.candidate.entity_id,
            self.candidate.retrieval_unit_id,
            _candidate_identity(self.candidate),
            self.candidate.within_source_rank,
            self.candidate.repository_id,
            self.candidate.locator,
            self.candidate.stable_version,
            self.candidate.source_generation,
            self.candidate.acl_ref,
            self.candidate.role,
            self.candidate.relation_path,
        )
        actual = (
            self.citation.entity_id,
            self.citation.retrieval_unit_id,
            self.citation.source_candidate_identity,
            self.citation.within_source_rank,
            self.citation.repository_id,
            self.citation.locator,
            self.citation.stable_version,
            self.citation.source_generation,
            self.citation.acl_ref,
            self.citation.candidate_role,
            self.citation.relation_path,
        )
        if actual != expected:
            raise ContextBuildScopeError("retrieval citation conflicts with its source candidate")
        object.__setattr__(
            self,
            "contract_version",
            _text(self.contract_version, "contract_version"),
        )


@dataclass(frozen=True, slots=True)
class RetrievalContext(_CanonicalContextContract):
    """Original ranked retrieval evidence, separate from reader-facing assembly."""

    task: CodeTask
    query_id: str
    source_status: CodeSourceStatus
    index_version: str
    watermark: str
    source_result_identity: str
    candidates: tuple[RetrievalContextCandidate, ...]
    fusion_trace: CodeSourceFusionTrace | None
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.task, CodeTask):
            raise TypeError("task must be a CodeTask")
        if not isinstance(self.source_status, CodeSourceStatus):
            raise TypeError("source_status must be a CodeSourceStatus")
        for name in ("query_id", "index_version", "watermark", "contract_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not _SHA256.fullmatch(self.source_result_identity):
            raise ValueError("source_result_identity must be a canonical sha256 identity")
        values = tuple(self.candidates)
        ranks = [item.candidate.within_source_rank for item in values]
        if ranks != list(range(len(values))):
            raise ValueError("retrieval candidates must preserve contiguous source rank")
        object.__setattr__(self, "candidates", values)
        if self.fusion_trace is not None and self.fusion_trace.task is not self.task:
            raise ValueError("fusion trace task conflicts with retrieval context task")


@dataclass(frozen=True, slots=True)
class ContextRoleBlock(_CanonicalContextContract):
    """One canonical source block serving one or more ordered task roles."""

    block_id: str
    parent_entity_id: str
    source_block_id: str
    source_content_identity: str
    source_text: str
    rendered_locator: str
    roles: tuple[ContextRole, ...]
    retrieval_unit_ids: tuple[str, ...]
    citation: ContextCitation
    truncated: bool
    original_character_count: int
    rendered_character_count: int
    token_estimate: int
    rendered_line_count: int
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "block_id",
            "parent_entity_id",
            "source_block_id",
            "rendered_locator",
            "contract_version",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not _SHA256.fullmatch(self.source_content_identity):
            raise ValueError("source_content_identity must be a canonical sha256 identity")
        if (
            not isinstance(self.source_text, str)
            or not self.source_text.strip()
            or _CONTROL.search(self.source_text)
        ):
            raise ValueError("source_text must contain original, supported source characters")
        roles = tuple(self.roles)
        if not roles or len(roles) != len(set(roles)):
            raise ValueError("role blocks require unique roles")
        object.__setattr__(self, "roles", roles)
        object.__setattr__(
            self,
            "retrieval_unit_ids",
            _ordered_unique_text(self.retrieval_unit_ids, "retrieval_unit_id"),
        )
        if not isinstance(self.citation, ContextCitation):
            raise TypeError("citation must be a ContextCitation")
        if self.parent_entity_id != self.citation.entity_id:
            raise ValueError("parent_entity_id must equal the stable cited entity")
        if self.source_block_id != self.citation.source_block_id:
            raise ValueError("source_block_id must equal the cited source block")
        if self.retrieval_unit_ids != (self.citation.retrieval_unit_id,):
            raise ValueError("a role block may cite only its exact source retrieval unit")
        if self.roles != self.citation.context_roles:
            raise ValueError("block roles must exactly match citation context roles")
        if type(self.truncated) is not bool:
            raise TypeError("truncated must be a bool")
        for name in (
            "original_character_count",
            "rendered_character_count",
            "token_estimate",
            "rendered_line_count",
        ):
            _non_negative_int(getattr(self, name), name)
        if self.rendered_character_count != len(self.source_text):
            raise ValueError("rendered_character_count must equal source_text length")
        if self.original_character_count < self.rendered_character_count:
            raise ValueError("rendered source cannot exceed original source length")
        if self.truncated != (self.original_character_count > self.rendered_character_count):
            raise ValueError("truncated must exactly describe source character pruning")
        if self.rendered_line_count != len(self.source_text.splitlines()):
            raise ValueError("rendered_line_count must equal the rendered source")
        if self.token_estimate != _token_estimate(self.source_text):
            raise ValueError("token_estimate must equal the deterministic source estimate")
        if not self.truncated and self.source_content_identity != _source_identity(
            self.source_text
        ):
            raise ValueError("untruncated source text must match its original content identity")
        if not self.truncated and self.rendered_locator != self.citation.locator:
            raise ValueError("untruncated source must retain the exact candidate locator")
        if self.truncated and self.rendered_locator == self.citation.locator:
            raise ValueError("truncated source requires an exact narrowed rendered locator")


@dataclass(frozen=True, slots=True)
class ContextMissingRole(_CanonicalContextContract):
    role: ContextRole
    status: ContextMissingStatus
    reason: str
    candidate_identities: tuple[str, ...] = ()
    source_block_ids: tuple[str, ...] = ()
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.role, ContextRole):
            raise TypeError("role must be a ContextRole")
        if not isinstance(self.status, ContextMissingStatus):
            raise TypeError("status must be a ContextMissingStatus")
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        object.__setattr__(
            self,
            "candidate_identities",
            _ordered_unique_text(self.candidate_identities, "candidate_identity"),
        )
        if any(not _SHA256.fullmatch(value) for value in self.candidate_identities):
            raise ValueError("candidate identities must be canonical sha256 values")
        object.__setattr__(
            self,
            "source_block_ids",
            _ordered_unique_text(self.source_block_ids, "source_block_id"),
        )
        object.__setattr__(
            self,
            "contract_version",
            _text(self.contract_version, "contract_version"),
        )


@dataclass(frozen=True, slots=True)
class ContextBudgetTrace(_CanonicalContextContract):
    max_tokens: int
    max_characters: int
    used_tokens: int
    used_characters: int
    selected_blocks: int
    pruned_block_ids: tuple[str, ...]
    truncated_block_ids: tuple[str, ...]
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in (
            "max_tokens",
            "max_characters",
            "used_tokens",
            "used_characters",
            "selected_blocks",
        ):
            _non_negative_int(getattr(self, name), name)
        if self.used_tokens > self.max_tokens or self.used_characters > self.max_characters:
            raise ValueError("budget trace usage cannot exceed configured ceilings")
        for name in ("pruned_block_ids", "truncated_block_ids"):
            object.__setattr__(
                self,
                name,
                _ordered_unique_text(getattr(self, name), name.removesuffix("s")),
            )
        if not set(self.truncated_block_ids).isdisjoint(self.pruned_block_ids):
            raise ValueError("a block cannot be both selected-truncated and pruned")
        object.__setattr__(
            self,
            "contract_version",
            _text(self.contract_version, "contract_version"),
        )


@dataclass(frozen=True, slots=True)
class ContextMetric(_CanonicalContextContract):
    """Tri-state metric: computed structure or explicitly unavailable quality."""

    status: ContextMetricStatus
    value: float | None
    reason: str
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.status, ContextMetricStatus):
            raise TypeError("status must be a ContextMetricStatus")
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        object.__setattr__(
            self,
            "contract_version",
            _text(self.contract_version, "contract_version"),
        )
        if self.status is ContextMetricStatus.UNAVAILABLE:
            if self.value is not None:
                raise ValueError("unavailable metrics must not carry a value")
            return
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not 0.0 <= float(self.value) <= 1.0
        ):
            raise ValueError("available metrics require a finite value in [0, 1]")
        object.__setattr__(self, "value", float(self.value))


@dataclass(frozen=True, slots=True)
class ContextBuildTrace(_CanonicalContextContract):
    builder_version: str
    template_id: str
    task: CodeTask
    input_candidates: int
    input_source_blocks: int
    mapped_candidates: int
    unique_parent_sources: int
    selected_source_blocks: int
    locator_validations: int
    path_explanations: int
    duplicate_candidate_identities: tuple[str, ...]
    distractor_candidate_identities: tuple[str, ...]
    fulfilled_roles: tuple[ContextRole, ...]
    missing_roles: tuple[ContextRole, ...]
    source_missing_candidate_roles: tuple[CodeCandidateRole, ...]
    budget: ContextBudgetTrace
    context_precision: ContextMetric
    context_recall: ContextMetric
    role_coverage: ContextMetric
    token_efficiency: ContextMetric
    locator_correctness: ContextMetric
    answer_utility: ContextMetric
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name in ("builder_version", "template_id", "contract_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.task, CodeTask):
            raise TypeError("task must be a CodeTask")
        for name in (
            "input_candidates",
            "input_source_blocks",
            "mapped_candidates",
            "unique_parent_sources",
            "selected_source_blocks",
            "locator_validations",
            "path_explanations",
        ):
            _non_negative_int(getattr(self, name), name)
        for name in ("duplicate_candidate_identities", "distractor_candidate_identities"):
            values = _ordered_unique_text(getattr(self, name), name.removesuffix("s"))
            if any(not _SHA256.fullmatch(value) for value in values):
                raise ValueError(f"{name} must contain canonical sha256 values")
            object.__setattr__(self, name, values)
        for name in ("fulfilled_roles", "missing_roles"):
            values = tuple(getattr(self, name))
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
            object.__setattr__(self, name, values)
        if set(self.fulfilled_roles) & set(self.missing_roles):
            raise ValueError("fulfilled and missing roles must be disjoint")
        source_missing = tuple(self.source_missing_candidate_roles)
        if len(source_missing) != len(set(source_missing)):
            raise ValueError("source_missing_candidate_roles must be unique")
        object.__setattr__(self, "source_missing_candidate_roles", source_missing)
        if not isinstance(self.budget, ContextBudgetTrace):
            raise TypeError("budget must be a ContextBudgetTrace")
        if self.selected_source_blocks != self.budget.selected_blocks:
            raise ValueError("selected block accounting conflicts with budget trace")
        for name in (
            "context_precision",
            "context_recall",
            "role_coverage",
            "token_efficiency",
            "locator_correctness",
            "answer_utility",
        ):
            if not isinstance(getattr(self, name), ContextMetric):
                raise TypeError(f"{name} must be a ContextMetric")
        if (
            self.context_precision.status is not ContextMetricStatus.UNAVAILABLE
            or self.context_recall.status is not ContextMetricStatus.UNAVAILABLE
            or self.answer_utility.status is not ContextMetricStatus.UNAVAILABLE
        ):
            raise ValueError("quality metrics requiring labels or answers must remain unavailable")

    @property
    def duplicate_rate(self) -> float | None:
        if self.input_candidates == 0:
            return None
        return len(self.duplicate_candidate_identities) / self.input_candidates

    @property
    def distractor_rate(self) -> float | None:
        if self.input_candidates == 0:
            return None
        return len(self.distractor_candidate_identities) / self.input_candidates

    @property
    def required_role_coverage(self) -> float | None:
        return self.role_coverage.value


@dataclass(frozen=True, slots=True)
class ComprehensionContext(_CanonicalContextContract):
    """Reader-facing source blocks in deterministic task-role order."""

    task: CodeTask
    template_id: str
    role_order: tuple[ContextRole, ...]
    blocks: tuple[ContextRoleBlock, ...]
    missing_roles: tuple[ContextMissingRole, ...]
    version: ContextVersion
    status: ContextBuildStatus
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.task, CodeTask):
            raise TypeError("task must be a CodeTask")
        for name in ("template_id", "contract_version"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        roles = tuple(self.role_order)
        if roles != CODE_TASK_CONTEXT_ROLE_ORDER[self.task]:
            raise ValueError("role_order must equal the frozen task template")
        object.__setattr__(self, "role_order", roles)
        blocks = tuple(self.blocks)
        role_rank = {role: index for index, role in enumerate(roles)}
        if any(
            tuple(sorted(block.roles, key=role_rank.__getitem__)) != block.roles for block in blocks
        ):
            raise ValueError("block roles must follow the frozen template order")
        if (
            tuple(
                sorted(
                    blocks,
                    key=lambda block: (
                        min(role_rank[role] for role in block.roles),
                        block.citation.within_source_rank,
                        block.block_id,
                    ),
                )
            )
            != blocks
        ):
            raise ValueError("comprehension blocks must follow the frozen template order")
        object.__setattr__(self, "blocks", blocks)
        missing = tuple(self.missing_roles)
        if tuple(item.role for item in missing) != tuple(
            role for role in roles if role not in {hit for block in blocks for hit in block.roles}
        ):
            raise ValueError("missing roles must exactly cover unfulfilled template roles")
        object.__setattr__(self, "missing_roles", missing)
        if not isinstance(self.version, ContextVersion):
            raise TypeError("version must be a ContextVersion")
        if not isinstance(self.status, ContextBuildStatus):
            raise TypeError("status must be a ContextBuildStatus")
        expected_status = (
            ContextBuildStatus.UNAVAILABLE
            if not blocks
            else ContextBuildStatus.PARTIAL
            if missing
            else ContextBuildStatus.COMPLETE
        )
        if self.status is not expected_status:
            raise ValueError("comprehension status conflicts with blocks/missing roles")


@dataclass(frozen=True, slots=True)
class CodeTaskContextBuild(_CanonicalContextContract):
    retrieval: RetrievalContext
    comprehension: ComprehensionContext
    trace: ContextBuildTrace
    contract_version: str = CONTEXT_BUILDER_CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.retrieval, RetrievalContext):
            raise TypeError("retrieval must be a RetrievalContext")
        if not isinstance(self.comprehension, ComprehensionContext):
            raise TypeError("comprehension must be a ComprehensionContext")
        if not isinstance(self.trace, ContextBuildTrace):
            raise TypeError("trace must be a ContextBuildTrace")
        if not (self.retrieval.task is self.comprehension.task is self.trace.task):
            raise ValueError("retrieval, comprehension, and trace tasks must match")
        object.__setattr__(
            self,
            "contract_version",
            _text(self.contract_version, "contract_version"),
        )


@dataclass(frozen=True, slots=True)
class _LocatorInfo:
    path: str
    start_line: int | None
    end_line: int | None


@dataclass(frozen=True, slots=True)
class _CandidateEntry:
    candidate: CodeRetrievalCandidate
    source_block: CodeContextBlock | None
    citation: ContextCitation
    roles: tuple[ContextRole, ...]
    primary_target: bool
    exact_signal: bool


@dataclass(frozen=True, slots=True)
class _DraftBlock:
    parent_entity_id: str
    source_block: CodeContextBlock
    citation: ContextCitation
    roles: tuple[ContextRole, ...]
    retrieval_unit_ids: tuple[str, ...]
    hard_signal: bool
    priority: tuple[Any, ...]
    source_content_identity: str
    block_id: str


def _source_identity(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _candidate_identity(candidate: CodeRetrievalCandidate) -> str:
    return "sha256:" + candidate.canonical_sha256()


def _block_identity(
    *,
    parent_entity_id: str,
    source_block_id: str,
    source_content_identity: str,
    source_candidate_identity: str,
    retrieval_unit_id: str,
    locator: str,
    roles: tuple[ContextRole, ...],
) -> str:
    payload = json.dumps(
        {
            "locator": locator,
            "parent_entity_id": parent_entity_id,
            "retrieval_unit_id": retrieval_unit_id,
            "roles": [role.value for role in roles],
            "source_block_id": source_block_id,
            "source_candidate_identity": source_candidate_identity,
            "source_content_identity": source_content_identity,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "code-task-context-block://sha256/" + hashlib.sha256(payload).hexdigest()


def _token_estimate(content: str) -> int:
    return max(1, (len(content.encode("utf-8")) + 3) // 4)


def _version(scope: ContextBuildScope) -> ContextVersion:
    _validated_version(scope.stable_version)
    dirty_match = _DIRTY_VERSION.fullmatch(scope.stable_version)
    if dirty_match is None:
        return ContextVersion(
            stable_version=scope.stable_version,
            source_generation=scope.source_generation,
            watermark=scope.watermark,
            dirty=False,
            base_version=None,
            dirty_manifest=None,
            display=(
                f"stable_version={scope.stable_version}; "
                f"generation={scope.source_generation}; dirty=false"
            ),
        )
    base = dirty_match.group("base")
    manifest = dirty_match.group("manifest")
    return ContextVersion(
        stable_version=scope.stable_version,
        source_generation=scope.source_generation,
        watermark=scope.watermark,
        dirty=True,
        base_version=base,
        dirty_manifest=manifest,
        display=(
            "dirty worktree snapshot; "
            f"base_commit={base}; manifest={manifest}; "
            f"generation={scope.source_generation}; not equivalent to a Git commit"
        ),
    )


def _locator_info(
    candidate: CodeRetrievalCandidate,
    scope: ContextBuildScope,
) -> _LocatorInfo:
    locator = _text(candidate.locator, "locator")
    if not locator.startswith("code://"):
        raise ContextBuildLocatorError(
            "candidate locator must be an exact code:// source locator with a repository path"
        )
    if "%" in locator:
        raise ContextBuildLocatorError(
            "candidate locator must use canonical NFC IRI text without percent aliases"
        )
    base, separator, fragment = locator.partition("#")
    payload = base.removeprefix("code://")
    owner, marker, remainder = payload.rpartition("@")
    if not marker:
        raise ContextBuildLocatorError("candidate locator must bind an exact stable version")
    ref, slash, encoded_path = remainder.partition("/")
    if not slash:
        raise ContextBuildLocatorError("candidate locator must include a repository-relative path")
    if owner != candidate.repository_id:
        raise ContextBuildLocatorError("candidate locator repository owner mismatch")
    if ref != candidate.stable_version:
        raise ContextBuildLocatorError("candidate locator stable version mismatch")
    try:
        path = _safe_path(encoded_path)
    except (TypeError, ValueError) as error:
        raise ContextBuildLocatorError(str(error)) from error
    if not scope.permits_path(path):
        raise ContextBuildScopeError("candidate path is outside the attested path prefixes")
    start_line: int | None = None
    end_line: int | None = None
    if separator:
        line_match = _LINE_FRAGMENT.fullmatch(fragment)
        if line_match is None:
            raise ContextBuildLocatorError(
                "candidate locator fragment must be canonical #L<start>-L<end>"
            )
        start_line = int(line_match.group("start"))
        end_line = int(line_match.group("end"))
        if end_line < start_line:
            raise ContextBuildLocatorError("candidate locator line span is reversed")
    canonical = f"code://{candidate.repository_id}@{candidate.stable_version}/{path}" + (
        f"#{fragment}" if separator else ""
    )
    if locator != canonical:
        raise ContextBuildLocatorError("candidate locator is not canonical round-trip text")
    return _LocatorInfo(path=path, start_line=start_line, end_line=end_line)


def _path_explanation(path: CodeRelationPath) -> str | None:
    if not path.edges:
        return None
    rendered = []
    for edge in path.edges:
        rendered.append(
            f"hop={edge.hop} direction={edge.direction.value} "
            f"relation={edge.edge_type.value} "
            f"source={edge.source_entity_id} target={edge.target_entity_id} "
            f"confidence={edge.confidence:.6f} locator={edge.locator.locator}"
        )
    return "; ".join(rendered) + f"; path_score={path.path_score:.12f}"


def _validate_result(result: CodeSourceResult) -> None:
    try:
        validated = CodeSourceResult.model_validate(
            result.model_dump(mode="python", round_trip=True)
        )
    except Exception as error:
        raise ContextBuildScopeError("source result is not a strict CodeSourceResult") from error
    if validated != result:
        raise ContextBuildScopeError("source result is not in canonical strict form")


def _validate_fusion(
    source: CodeSourceFusionSearchResult,
    task: CodeTask,
) -> None:
    if not isinstance(source.trace, CodeSourceFusionTrace):
        raise ContextBuildScopeError("fusion input must carry a CodeSourceFusionTrace")
    profile = source.profile
    if not (source.trace.task is profile.definition.task is profile.contract.task is task):
        raise ContextBuildScopeError("fusion task/profile/trace mismatch")
    if (
        source.trace.profile_key != profile.definition.key
        or source.trace.profile_version != profile.definition.version
    ):
        raise ContextBuildScopeError("fusion trace profile identity mismatch")
    if source.trace.refused:
        raise ContextBuildRefusalError(
            ContextRefusal(
                code=ContextRefusalCode.UPSTREAM_REFUSAL,
                requested_task=task.value,
                reason="upstream Code source fusion refused the required evidence roles",
            )
        )


def _validate_production_attestation(
    source: CodeSourceFusionSearchResult,
    scope: ContextBuildScope,
) -> Mapping[str, CodeSourceBlockPublicationIdentity]:
    try:
        attestation = _verify_code_source_scope_attestation(source)
    except CodeSourceScopeAttestationError as error:
        raise ContextBuildScopeError(str(error)) from error
    if not isinstance(attestation, CodeSourceScopeAttestation):
        raise ContextBuildScopeError("production scope attestation is unavailable")
    expected_scope = (
        scope.project_id,
        (scope.repository_id,),
        scope.requested_commit,
        scope.requested_branch,
        scope.target_ref,
        scope.allowed_acl_refs,
        scope.enforce_acl,
        scope.index_version,
        scope.watermark,
        (scope.stable_version,),
        (scope.source_generation,),
    )
    actual_scope = (
        attestation.project_id,
        attestation.repository_ids,
        attestation.requested_commit,
        attestation.requested_branch,
        attestation.target_ref,
        attestation.allowed_acl_refs,
        attestation.enforce_acl,
        attestation.index_version,
        attestation.watermark,
        attestation.stable_versions,
        attestation.source_generations,
    )
    if actual_scope != expected_scope:
        raise ContextBuildScopeError(
            "production attestation project/repository/ref/ACL/version scope conflicts "
            "with the requested build scope"
        )
    manifests = {item.block_id: item for item in attestation.context_blocks}
    if len(manifests) != len(attestation.context_blocks):
        raise ContextBuildScopeError(
            "production publication manifest contains duplicate block identities"
        )
    return MappingProxyType(manifests)


def _validate_candidate_scope(
    candidate: CodeRetrievalCandidate,
    scope: ContextBuildScope,
) -> _LocatorInfo:
    expected = (
        scope.repository_id,
        scope.stable_version,
        scope.source_generation,
        scope.acl_ref,
    )
    actual = (
        candidate.repository_id,
        candidate.stable_version,
        candidate.source_generation,
        candidate.acl_ref,
    )
    if actual != expected:
        raise ContextBuildScopeError(
            "candidate repository/version/generation/ACL conflicts with scope attestation"
        )
    if candidate.version_alignment is not CodeVersionAlignment.EXACT:
        raise ContextBuildScopeError("candidate version alignment must be exact")
    terminal = candidate.relation_path.nodes[-1]
    if (
        terminal.entity_id != candidate.entity_id
        or terminal.repository_id != candidate.repository_id
        or terminal.stable_version != candidate.stable_version
        or terminal.source_generation != candidate.source_generation
        or terminal.locator != candidate.locator
        or terminal.acl_ref != candidate.acl_ref
    ):
        raise ContextBuildScopeError("candidate terminal path provenance mismatch")
    if any(
        node.repository_id != scope.repository_id or node.acl_ref != scope.acl_ref
        for node in candidate.relation_path.nodes
    ):
        raise ContextBuildScopeError("relation path crosses repository or ACL scope")
    return _locator_info(candidate, scope)


def _validate_source_block(
    candidate: CodeRetrievalCandidate,
    block: CodeContextBlock,
    info: _LocatorInfo,
    *,
    scope: ContextBuildScope,
    publication: CodeSourceBlockPublicationIdentity,
) -> None:
    expected = (
        candidate.entity_id,
        candidate.retrieval_unit_id,
        candidate.repository_id,
        candidate.role,
        candidate.stable_version,
        candidate.source_generation,
        candidate.relation_path,
        candidate.locator,
        candidate.acl_ref,
    )
    actual = (
        block.entity_id,
        block.retrieval_unit_id,
        block.repository_id,
        block.role,
        block.stable_version,
        block.source_generation,
        block.relation_path,
        block.locator,
        block.acl_ref,
    )
    if actual != expected:
        raise ContextBuildScopeError("source block provenance conflicts with its candidate")
    if info.start_line is None or info.end_line is None:
        raise ContextBuildLocatorError("source-bearing blocks require an exact canonical line span")
    line_count = len(block.content.splitlines())
    if line_count != info.end_line - info.start_line + 1:
        raise ContextBuildLocatorError(
            "untruncated source line count must be exactly equal to the locator span"
        )
    expected_publication = (
        block.block_id,
        scope.project_id,
        block.repository_id,
        block.stable_version,
        block.source_generation,
        block.entity_id,
        block.retrieval_unit_id,
        info.path,
        block.locator,
        block.acl_ref,
        scope.watermark,
        block.role,
        _source_identity(block.content),
        "sha256:" + hashlib.sha256(block.canonical_json_bytes()).hexdigest(),
    )
    actual_publication = (
        publication.block_id,
        publication.project_id,
        publication.repository_id,
        publication.stable_version,
        publication.source_generation,
        publication.entity_id,
        publication.retrieval_unit_id,
        publication.repository_path,
        publication.locator,
        publication.acl_ref,
        publication.watermark,
        publication.role,
        publication.content_identity,
        publication.block_identity,
    )
    if actual_publication != expected_publication:
        raise ContextBuildScopeError(
            "source block bytes or publication provenance conflict with production authority"
        )


def _candidate_relations(candidate: CodeRetrievalCandidate) -> frozenset[CodeRelationType]:
    return frozenset(edge.edge_type for edge in candidate.relation_path.edges)


def _terminal_is_stored_source(candidate: CodeRetrievalCandidate) -> bool:
    if not candidate.relation_path.edges:
        return False
    edge = candidate.relation_path.edges[-1]
    terminal = candidate.relation_path.nodes[-1]
    return edge.source_entity_id == terminal.entity_id


def _is_test(candidate: CodeRetrievalCandidate, relations: frozenset[CodeRelationType]) -> bool:
    return (
        candidate.role is CodeCandidateRole.TEST
        or candidate.entity_type.casefold() in _TEST_ENTITY_TYPES
        or bool(relations & _TEST_RELATIONS)
    )


def _is_history(
    candidate: CodeRetrievalCandidate,
    relations: frozenset[CodeRelationType],
) -> bool:
    return (
        candidate.role is CodeCandidateRole.HISTORY
        or candidate.entity_type.casefold() in _HISTORY_ENTITY_TYPES
        or bool(relations & _HISTORY_RELATIONS)
    )


def _roles_for(
    task: CodeTask,
    candidate: CodeRetrievalCandidate,
    *,
    primary_target: bool,
) -> tuple[ContextRole, ...]:
    relations = _candidate_relations(candidate)
    entity_type = candidate.entity_type.casefold()
    is_code = entity_type in _CODE_ENTITY_TYPES
    is_test = _is_test(candidate, relations)
    is_history = _is_history(candidate, relations)
    roles: set[ContextRole] = set()

    if task is CodeTask.IMPLEMENTATION:
        if primary_target:
            roles.update(
                {
                    ContextRole.TARGET_SYMBOL,
                    ContextRole.SIGNATURE_DOC,
                    ContextRole.RELEVANT_AST_BLOCKS,
                    ContextRole.VERSION_LOCATOR,
                }
            )
        elif candidate.role in {CodeCandidateRole.TARGET, CodeCandidateRole.DEPENDENCY} and is_code:
            roles.add(ContextRole.RELEVANT_AST_BLOCKS)
        if relations & {
            CodeRelationType.IMPORTS,
            CodeRelationType.TYPE_OF,
            CodeRelationType.IMPLEMENTS,
            CodeRelationType.OVERRIDES,
        }:
            roles.add(ContextRole.REQUIRED_IMPORTS_TYPES)
        if relations & {CodeRelationType.CALLS, CodeRelationType.REFERENCES}:
            roles.add(ContextRole.DIRECT_CALLERS_CALLEES)
        if is_test:
            roles.add(ContextRole.RELATED_TESTS)

    elif task is CodeTask.BUG_LOCALIZATION:
        if candidate.entity_type.casefold() == "testresult":
            roles.add(ContextRole.ERROR_STACK)
        if primary_target:
            roles.update({ContextRole.SUSPECT_TARGET, ContextRole.RELEVANT_BLOCK})
        elif candidate.role is CodeCandidateRole.TARGET:
            roles.update({ContextRole.ALTERNATIVE_SUSPECTS, ContextRole.RELEVANT_BLOCK})
        elif candidate.role is CodeCandidateRole.DEPENDENCY and is_code:
            roles.add(ContextRole.RELEVANT_BLOCK)
        if candidate.relation_path.edges:
            roles.add(ContextRole.GRAPH_PATH)
        if is_history:
            roles.add(ContextRole.RECENT_DIFF)
        if is_test:
            roles.add(ContextRole.FAILED_PASSED_VALIDATION)

    elif task is CodeTask.IMPACT_ANALYSIS:
        if primary_target:
            roles.add(ContextRole.CHANGED_SYMBOL)
        if relations & {
            CodeRelationType.CALLS,
            CodeRelationType.REFERENCES,
        } and _terminal_is_stored_source(candidate):
            roles.add(ContextRole.INCOMING_REFERENCES_CALLS)
        if relations & {
            CodeRelationType.IMPLEMENTS,
            CodeRelationType.OVERRIDES,
        } and _terminal_is_stored_source(candidate):
            roles.add(ContextRole.IMPLEMENTATIONS_OVERRIDES)
        if is_test:
            roles.add(ContextRole.TESTS_COVERAGE)
        if entity_type in {"fileversion", "packagemodule"} or relations & {
            CodeRelationType.AFFECTS,
            CodeRelationType.IMPORTS,
        }:
            roles.add(ContextRole.AFFECTED_FILES_MODULES)
        if entity_type in {"unresolved", "unknown"}:
            roles.add(ContextRole.UNRESOLVED_UNKNOWN)

    elif task is CodeTask.CHANGE_CONTEXT:
        if primary_target:
            roles.add(ContextRole.EDITABLE_TARGET)
        if relations & {
            CodeRelationType.DEFINES,
            CodeRelationType.CONTAINS,
            CodeRelationType.PARENT_OF,
            CodeRelationType.TYPE_OF,
            CodeRelationType.IMPLEMENTS,
            CodeRelationType.OVERRIDES,
        }:
            roles.add(ContextRole.CONTRACT_TYPE_PARENT)
        if candidate.role is CodeCandidateRole.DEPENDENCY or relations & {
            CodeRelationType.CALLS,
            CodeRelationType.REFERENCES,
            CodeRelationType.IMPORTS,
        }:
            roles.add(ContextRole.CALLERS_DEPENDENCIES)
        if is_test:
            roles.add(ContextRole.NEARBY_TESTS)
            roles.add(ContextRole.VALIDATION_REQUIREMENT)
        if is_history:
            roles.add(ContextRole.RECENT_DIFF)
        if entity_type in _GENERATED_ENTITY_TYPES:
            roles.add(ContextRole.DO_NOT_EDIT_GENERATED)

    order = {role: index for index, role in enumerate(CODE_TASK_CONTEXT_ROLE_ORDER[task])}
    return tuple(sorted(roles, key=order.__getitem__))


def _citation(
    candidate: CodeRetrievalCandidate,
    *,
    info: _LocatorInfo,
    roles: tuple[ContextRole, ...],
    source_block: CodeContextBlock | None,
    dirty: bool,
) -> ContextCitation:
    return ContextCitation(
        entity_id=candidate.entity_id,
        retrieval_unit_id=candidate.retrieval_unit_id,
        source_candidate_identity=_candidate_identity(candidate),
        within_source_rank=candidate.within_source_rank,
        repository_id=candidate.repository_id,
        repository_path=info.path,
        locator=candidate.locator,
        start_line=info.start_line,
        end_line=info.end_line,
        stable_version=candidate.stable_version,
        source_generation=candidate.source_generation,
        acl_ref=candidate.acl_ref,
        candidate_role=candidate.role,
        context_roles=roles,
        relation_path=candidate.relation_path,
        path_explanation=_path_explanation(candidate.relation_path),
        source_block_id=source_block.block_id if source_block is not None else None,
        dirty=dirty,
    )


def _candidate_priority(
    entry: _CandidateEntry,
    role_rank: Mapping[ContextRole, int],
) -> tuple[Any, ...]:
    first_role = min((role_rank[role] for role in entry.roles), default=len(role_rank))
    return (
        0 if entry.primary_target else 1,
        0 if entry.exact_signal else 1,
        first_role,
        entry.candidate.within_source_rank,
        entry.candidate.locator,
        entry.candidate.retrieval_unit_id,
    )


def _draft_blocks(
    entries: tuple[_CandidateEntry, ...],
    role_order: tuple[ContextRole, ...],
) -> tuple[tuple[_DraftBlock, ...], tuple[str, ...]]:
    role_rank = {role: index for index, role in enumerate(role_order)}
    drafts: list[_DraftBlock] = []
    duplicate_identities: list[str] = []
    seen: set[tuple[object, ...]] = set()
    for entry in sorted(entries, key=lambda item: _candidate_priority(item, role_rank)):
        if entry.source_block is None or not entry.roles:
            continue
        block = entry.source_block
        candidate_identity = _candidate_identity(entry.candidate)
        content_identity = _source_identity(block.content)
        equivalence = (
            candidate_identity,
            "sha256:" + hashlib.sha256(block.canonical_json_bytes()).hexdigest(),
            content_identity,
            entry.candidate.locator,
            entry.candidate.entity_id,
            entry.candidate.retrieval_unit_id,
            entry.candidate.role,
            entry.roles,
            entry.citation.canonical_sha256(),
        )
        if equivalence in seen:
            duplicate_identities.append(candidate_identity)
            continue
        seen.add(equivalence)
        block_id = _block_identity(
            parent_entity_id=entry.candidate.entity_id,
            source_block_id=block.block_id,
            source_content_identity=content_identity,
            source_candidate_identity=candidate_identity,
            retrieval_unit_id=entry.candidate.retrieval_unit_id,
            locator=entry.candidate.locator,
            roles=entry.roles,
        )
        drafts.append(
            _DraftBlock(
                parent_entity_id=entry.candidate.entity_id,
                source_block=block,
                citation=entry.citation,
                roles=entry.roles,
                retrieval_unit_ids=(entry.candidate.retrieval_unit_id,),
                hard_signal=entry.primary_target or entry.exact_signal,
                priority=_candidate_priority(entry, role_rank),
                source_content_identity=content_identity,
                block_id=block_id,
            )
        )
    return (
        tuple(sorted(drafts, key=lambda draft: (*draft.priority, draft.block_id))),
        tuple(sorted(set(duplicate_identities))),
    )


def _truncated_locator(citation: ContextCitation, rendered_lines: int) -> str | None:
    if (
        citation.start_line is None
        or citation.end_line is None
        or rendered_lines < 1
        or citation.start_line + rendered_lines - 1 > citation.end_line
    ):
        return None
    base, separator, fragment = citation.locator.partition("#")
    if not separator:
        return None
    match = _LINE_FRAGMENT.search(fragment)
    if match is None:
        return None
    replacement = f"L{citation.start_line}-L{citation.start_line + rendered_lines - 1}"
    updated = fragment[: match.start()] + replacement + fragment[match.end() :]
    return f"{base}#{updated}"


def _line_safe_prefix(
    content: str,
    *,
    citation: ContextCitation,
    remaining_tokens: int,
    remaining_characters: int,
) -> tuple[str, str] | None:
    if remaining_tokens <= 0 or remaining_characters <= 0:
        return None
    selected: list[str] = []
    selected_characters = 0
    selected_bytes = 0
    for line in content.splitlines(keepends=True):
        candidate_characters = selected_characters + len(line)
        candidate_bytes = selected_bytes + len(line.encode("utf-8"))
        candidate_tokens = max(1, (candidate_bytes + 3) // 4)
        if candidate_characters > remaining_characters or candidate_tokens > remaining_tokens:
            break
        selected.append(line)
        selected_characters = candidate_characters
        selected_bytes = candidate_bytes
    if not selected:
        return None
    rendered = "".join(selected)
    if rendered == content:
        return rendered, citation.locator
    locator = _truncated_locator(citation, len(selected))
    if locator is None:
        return None
    return rendered, locator


def _select_blocks(
    drafts: tuple[_DraftBlock, ...],
    *,
    role_order: tuple[ContextRole, ...],
    budget: ContextBudget,
) -> tuple[tuple[ContextRoleBlock, ...], ContextBudgetTrace]:
    role_counts = {role: 0 for role in role_order}
    selected: list[ContextRoleBlock] = []
    pruned: list[str] = []
    truncated: list[str] = []
    used_tokens = 0
    used_characters = 0

    for draft in drafts:
        eligible_roles = tuple(
            role for role in draft.roles if role_counts[role] < budget.per_role_block_limit
        )
        if not eligible_roles:
            pruned.append(draft.block_id)
            continue
        content = draft.source_block.content
        tokens = _token_estimate(content)
        remaining_tokens = budget.max_tokens - used_tokens
        remaining_characters = budget.max_characters - used_characters
        rendered = content
        rendered_locator = draft.citation.locator
        is_truncated = False
        if tokens > remaining_tokens or len(content) > remaining_characters:
            if not draft.hard_signal:
                pruned.append(draft.block_id)
                continue
            prefix = _line_safe_prefix(
                content,
                citation=draft.citation,
                remaining_tokens=remaining_tokens,
                remaining_characters=remaining_characters,
            )
            if prefix is None:
                pruned.append(draft.block_id)
                continue
            rendered, rendered_locator = prefix
            is_truncated = rendered != content
            tokens = _token_estimate(rendered)
        block = ContextRoleBlock(
            block_id=draft.block_id,
            parent_entity_id=draft.parent_entity_id,
            source_block_id=draft.source_block.block_id,
            source_content_identity=draft.source_content_identity,
            source_text=rendered,
            rendered_locator=rendered_locator,
            roles=eligible_roles,
            retrieval_unit_ids=draft.retrieval_unit_ids,
            citation=replace(draft.citation, context_roles=eligible_roles),
            truncated=is_truncated,
            original_character_count=len(content),
            rendered_character_count=len(rendered),
            token_estimate=tokens,
            rendered_line_count=len(rendered.splitlines()),
        )
        selected.append(block)
        for role in eligible_roles:
            role_counts[role] += 1
        used_tokens += tokens
        used_characters += len(rendered)
        if is_truncated:
            truncated.append(draft.block_id)

    return (
        tuple(selected),
        ContextBudgetTrace(
            max_tokens=budget.max_tokens,
            max_characters=budget.max_characters,
            used_tokens=used_tokens,
            used_characters=used_characters,
            selected_blocks=len(selected),
            pruned_block_ids=tuple(sorted(pruned)),
            truncated_block_ids=tuple(sorted(truncated)),
        ),
    )


def _missing_roles(
    *,
    role_order: tuple[ContextRole, ...],
    blocks: tuple[ContextRoleBlock, ...],
    entries: tuple[_CandidateEntry, ...],
    drafts: tuple[_DraftBlock, ...],
) -> tuple[ContextMissingRole, ...]:
    fulfilled = {role for block in blocks for role in block.roles}
    output: list[ContextMissingRole] = []
    selected_ids = {block.block_id for block in blocks}
    for role in role_order:
        if role in fulfilled:
            continue
        role_entries = [entry for entry in entries if role in entry.roles]
        role_drafts = [draft for draft in drafts if role in draft.roles]
        candidate_ids = tuple(
            sorted(_candidate_identity(entry.candidate) for entry in role_entries)
        )
        source_ids = tuple(
            sorted(
                {
                    entry.source_block.block_id
                    for entry in role_entries
                    if entry.source_block is not None
                }
            )
        )
        if role_drafts and any(draft.block_id not in selected_ids for draft in role_drafts):
            status = ContextMissingStatus.PRUNED
            reason = "governed source evidence was pruned by role or token/character budget"
        elif role_entries and not source_ids:
            status = ContextMissingStatus.UNAVAILABLE
            reason = "retrieval candidate exists but carries no original CodeContextBlock source"
        else:
            status = ContextMissingStatus.MISSING
            reason = (
                "no existing relation path was supplied for this role"
                if role is ContextRole.GRAPH_PATH
                else "retrieval contained no governed evidence for this role"
            )
        output.append(
            ContextMissingRole(
                role=role,
                status=status,
                reason=reason,
                candidate_identities=candidate_ids,
                source_block_ids=source_ids,
            )
        )
    return tuple(output)


def _resolve_task(
    source: CodeSourceFusionSearchResult | CodeSourceResult,
    task: CodeTask | str | None,
) -> CodeTask:
    if isinstance(source, CodeSourceFusionSearchResult):
        inferred = source.trace.task
        if task is None:
            resolved = inferred
        else:
            try:
                resolved = CodeTask(task)
            except (TypeError, ValueError) as error:
                refusal = ContextRefusal(
                    code=ContextRefusalCode.UNKNOWN_TASK,
                    requested_task=str(task),
                    reason=f"unknown Code task: {task!r}",
                )
                raise ContextBuildRefusalError(refusal) from error
            if resolved is not inferred:
                raise ContextBuildScopeError("requested task conflicts with fusion trace task")
    else:
        if task is None:
            raise ContextBuildRefusalError(
                ContextRefusal(
                    code=ContextRefusalCode.UNKNOWN_TASK,
                    requested_task="<missing>",
                    reason="strict CodeSourceResult input requires an explicit CodeTask",
                )
            )
        try:
            resolved = CodeTask(task)
        except (TypeError, ValueError) as error:
            refusal = ContextRefusal(
                code=ContextRefusalCode.UNKNOWN_TASK,
                requested_task=str(task),
                reason=f"unknown Code task: {task!r}",
            )
            raise ContextBuildRefusalError(refusal) from error
    if resolved not in CODE_TASK_CONTEXT_ROLE_ORDER:
        raise ContextBuildRefusalError(
            ContextRefusal(
                code=ContextRefusalCode.UNSUPPORTED_TASK,
                requested_task=resolved.value,
                reason=(
                    f"Code task {resolved.value!r} has no authorized C7-01 "
                    "task-specific context template"
                ),
            )
        )
    return resolved


class CodeTaskContextBuilder:
    """Build RetrievalContext and ComprehensionContext without performing retrieval."""

    builder_version = CONTEXT_BUILDER_VERSION
    contract_version = CONTEXT_BUILDER_CONTRACT_VERSION

    def build(
        self,
        source: CodeSourceFusionSearchResult | CodeSourceResult,
        *,
        scope: ContextBuildScope,
        task: CodeTask | str | None = None,
        budget: ContextBudget | None = None,
    ) -> CodeTaskContextBuild:
        if not isinstance(scope, ContextBuildScope):
            raise TypeError("scope must be a frozen ContextBuildScope attestation")
        if isinstance(source, CodeSourceResult):
            raise ContextBuildScopeError(
                "bare CodeSourceResult has no exact production scope attestation"
            )
        if not isinstance(source, CodeSourceFusionSearchResult):
            raise TypeError("source must be an exact production CodeSourceFusionSearchResult")
        resolved_task = _resolve_task(source, task)
        result = source.result
        _validate_fusion(source, resolved_task)
        _validate_result(result)
        publication_manifests = _validate_production_attestation(source, scope)
        if result.index_version != scope.index_version or result.watermark != scope.watermark:
            raise ContextBuildScopeError(
                "result index_version/watermark conflicts with attestation"
            )

        if budget is None:
            tokens = source.profile.definition.context_template.token_budget
            budget = ContextBudget(max_tokens=tokens, max_characters=tokens * 4)
        if not isinstance(budget, ContextBudget):
            raise TypeError("budget must be a ContextBudget")

        version = _version(scope)
        source_blocks = {
            (block.entity_id, block.retrieval_unit_id): block for block in result.context_blocks
        }
        primary_target = next(
            (
                candidate.retrieval_unit_id
                for candidate in result.candidates
                if candidate.role is CodeCandidateRole.TARGET
            ),
            None,
        )
        entries: list[_CandidateEntry] = []
        retrieval_items: list[RetrievalContextCandidate] = []
        for candidate in result.candidates:
            info = _validate_candidate_scope(candidate, scope)
            block = source_blocks.get((candidate.entity_id, candidate.retrieval_unit_id))
            if block is not None:
                publication = publication_manifests.get(block.block_id)
                if publication is None:
                    raise ContextBuildScopeError(
                        "source block is absent from the production publication manifest"
                    )
                _validate_source_block(
                    candidate,
                    block,
                    info,
                    scope=scope,
                    publication=publication,
                )
            roles = _roles_for(
                resolved_task,
                candidate,
                primary_target=candidate.retrieval_unit_id == primary_target,
            )
            exact_signal = any(
                rank.channel is CodeRetrievalChannel.EXACT for rank in candidate.raw_channel_ranks
            )
            citation = _citation(
                candidate,
                info=info,
                roles=roles,
                source_block=block,
                dirty=version.dirty,
            )
            entry = _CandidateEntry(
                candidate=candidate,
                source_block=block,
                citation=citation,
                roles=roles,
                primary_target=candidate.retrieval_unit_id == primary_target,
                exact_signal=exact_signal,
            )
            entries.append(entry)
            retrieval_items.append(
                RetrievalContextCandidate(candidate=candidate, citation=citation)
            )

        entry_values = tuple(entries)
        role_order = CODE_TASK_CONTEXT_ROLE_ORDER[resolved_task]
        drafts, duplicate_ids = _draft_blocks(entry_values, role_order)
        selected, budget_trace = _select_blocks(
            drafts,
            role_order=role_order,
            budget=budget,
        )
        role_rank = {role: index for index, role in enumerate(role_order)}
        selected = tuple(
            sorted(
                selected,
                key=lambda block: (
                    min(role_rank[role] for role in block.roles),
                    block.citation.within_source_rank,
                    block.block_id,
                ),
            )
        )
        missing = _missing_roles(
            role_order=role_order,
            blocks=selected,
            entries=entry_values,
            drafts=drafts,
        )
        status = (
            ContextBuildStatus.UNAVAILABLE
            if not selected
            else ContextBuildStatus.PARTIAL
            if missing
            else ContextBuildStatus.COMPLETE
        )
        retrieval = RetrievalContext(
            task=resolved_task,
            query_id=result.query_id,
            source_status=result.status,
            index_version=result.index_version,
            watermark=result.watermark,
            source_result_identity="sha256:" + result.canonical_sha256(),
            candidates=tuple(retrieval_items),
            fusion_trace=source.trace,
        )
        comprehension = ComprehensionContext(
            task=resolved_task,
            template_id=_TEMPLATE_IDS[resolved_task],
            role_order=role_order,
            blocks=selected,
            missing_roles=missing,
            version=version,
            status=status,
        )
        fulfilled = tuple(
            role for role in role_order if any(role in block.roles for block in selected)
        )
        missing_role_values = tuple(item.role for item in missing)
        distractors = tuple(
            sorted(
                _candidate_identity(entry.candidate) for entry in entry_values if not entry.roles
            )
        )
        source_missing_roles = source.trace.missing_roles
        role_coverage_value = len(fulfilled) / len(role_order)
        role_coverage = ContextMetric(
            status=ContextMetricStatus.AVAILABLE,
            value=role_coverage_value,
            reason="computed from selected source blocks and frozen template roles",
        )
        if budget_trace.max_tokens:
            token_efficiency = ContextMetric(
                status=ContextMetricStatus.AVAILABLE,
                value=budget_trace.used_tokens / budget_trace.max_tokens,
                reason="computed from rendered source tokens and configured token ceiling",
            )
        else:
            token_efficiency = ContextMetric(
                status=ContextMetricStatus.UNAVAILABLE,
                value=None,
                reason="token efficiency is unavailable for a zero-token ceiling",
            )
        locator_correctness = (
            ContextMetric(
                status=ContextMetricStatus.AVAILABLE,
                value=1.0,
                reason="every selected block passed canonical locator and exact span validation",
            )
            if selected
            else ContextMetric(
                status=ContextMetricStatus.UNAVAILABLE,
                value=None,
                reason="no selected source block has a rendered locator to validate",
            )
        )
        trace = ContextBuildTrace(
            builder_version=self.builder_version,
            template_id=_TEMPLATE_IDS[resolved_task],
            task=resolved_task,
            input_candidates=len(entry_values),
            input_source_blocks=len(result.context_blocks),
            mapped_candidates=sum(bool(entry.roles) for entry in entry_values),
            unique_parent_sources=len({draft.parent_entity_id for draft in drafts}),
            selected_source_blocks=len(selected),
            locator_validations=len(selected),
            path_explanations=sum(
                bool(entry.candidate.relation_path.edges) for entry in entry_values
            ),
            duplicate_candidate_identities=duplicate_ids,
            distractor_candidate_identities=distractors,
            fulfilled_roles=fulfilled,
            missing_roles=missing_role_values,
            source_missing_candidate_roles=source_missing_roles,
            budget=budget_trace,
            context_precision=ContextMetric(
                status=ContextMetricStatus.UNAVAILABLE,
                value=None,
                reason="context precision requires independent human relevance labels",
            ),
            context_recall=ContextMetric(
                status=ContextMetricStatus.UNAVAILABLE,
                value=None,
                reason="context recall requires an independent complete relevance set",
            ),
            role_coverage=role_coverage,
            token_efficiency=token_efficiency,
            locator_correctness=locator_correctness,
            answer_utility=ContextMetric(
                status=ContextMetricStatus.UNAVAILABLE,
                value=None,
                reason="C7-01 does not generate or grade an answer",
            ),
        )
        return CodeTaskContextBuild(
            retrieval=retrieval,
            comprehension=comprehension,
            trace=trace,
        )


__all__ = [
    "CODE_TASK_CONTEXT_ROLE_ORDER",
    "CONTEXT_BUILDER_CONTRACT_VERSION",
    "CONTEXT_BUILDER_VERSION",
    "CodeTaskContextBuild",
    "CodeTaskContextBuilder",
    "ComprehensionContext",
    "ContextBudget",
    "ContextBudgetTrace",
    "ContextBuildError",
    "ContextBuildLocatorError",
    "ContextBuildRefusalError",
    "ContextBuildScope",
    "ContextBuildScopeError",
    "ContextBuildStatus",
    "ContextBuildTrace",
    "ContextCitation",
    "ContextMetric",
    "ContextMetricStatus",
    "ContextMissingRole",
    "ContextMissingStatus",
    "ContextRefusal",
    "ContextRefusalCode",
    "ContextRole",
    "ContextRoleBlock",
    "ContextVersion",
    "RetrievalContext",
    "RetrievalContextCandidate",
]
