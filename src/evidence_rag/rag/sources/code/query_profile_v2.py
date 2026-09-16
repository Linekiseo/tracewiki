"""Deterministic Code query profiles and production source-fusion orchestration.

This module is deliberately read-only.  It composes the published exact/sparse,
dense hybrid, typed graph, reranking, and calibration boundaries without
creating a second resolver, graph registry, or storage path.  History and test
evidence stay behind explicit optional hook protocols; absence is always
reported and is never represented as a successful channel.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from ....models import EvidenceSearchRequest
from .contracts import (
    CodeCalibratedScore,
    CodeCalibrationStatus,
    CodeCandidateRole,
    CodeChannelCompleteNoMatch,
    CodeChannelCompletePruned,
    CodeChannelCompleteWithHits,
    CodeChannelDisabled,
    CodeChannelError,
    CodeChannelFailure,
    CodeChannelOutcome,
    CodeChannelRank,
    CodeChannelTimeout,
    CodeChannelUnavailable,
    CodeQueryProfile,
    CodeRelationType,
    CodeRetrievalBudget,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeSourceResult,
    CodeSourceStatus,
    CodeTask,
    CodeTraversalDirection,
    CodeUncalibratedScore,
    CodeVersionAlignment,
)

PROFILE_REGISTRY_VERSION = "code-query-profile-registry-v2"
PIPELINE_VERSION = "code-source-fusion-pipeline-v2"
REWRITE_VERSION = "code-query-rewrite-v2"
SOURCE_SCOPE_ATTESTATION_VERSION = "code-source-scope-attestation-v1"

_SOURCE_SCOPE_AUTHORITY_KEY = secrets.token_bytes(32)

_PATH_RE = re.compile(r"(?<![\w.-])((?:\.{0,2}/)?(?:[\w@+.-]+/)+[\w@+.-]+\.[A-Za-z0-9]{1,12})")
_UNSAFE_PATH_RE = re.compile(
    r"(?<![\w.-])((?:/|(?:\.\./)+)(?:[\w@+.-]+/)*[\w@+.-]+\.[A-Za-z0-9]{1,12})"
)
_URI_PATH_RE = re.compile(r"(?:code|code-unit)://[^\s\"'<>]+@[^/\s]+/([^#\s\"'<>]+)")
_QUALIFIED_RE = re.compile(r"(?<![\w])([A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+)")
_IDENTIFIER_RE = re.compile(r"(?<![\w$])([A-Za-z_$][A-Za-z0-9_$]*)(?![\w$])")
_SIMPLE_IDENTIFIER_RE = re.compile(r"(?:[A-Za-z_$][A-Za-z0-9_$]*)(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*")
_LOCATION_RE = re.compile(
    r"^(?:(?:code|code-unit)://\S+|(?:\.{0,2}/)?(?:[\w@+.-]+/)+"
    r"[\w@+.-]+\.[A-Za-z0-9]{1,12}(?::\d+(?::\d+)?)?)$"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_STOP_IDENTIFIERS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "by",
        "call",
        "called",
        "change",
        "code",
        "does",
        "error",
        "file",
        "find",
        "for",
        "from",
        "function",
        "history",
        "how",
        "impact",
        "implementation",
        "in",
        "is",
        "line",
        "method",
        "of",
        "on",
        "or",
        "show",
        "symbol",
        "test",
        "tests",
        "the",
        "this",
        "to",
        "validation",
        "what",
        "where",
        "which",
        "with",
    }
)

_TASK_KEYWORDS: tuple[tuple[CodeTask, tuple[str, ...]], ...] = (
    (
        CodeTask.HISTORICAL,
        (
            "historical",
            "history",
            "previous version",
            "used to",
            "when did",
            "rename history",
        ),
    ),
    (
        CodeTask.TEST_VALIDATION,
        (
            "test validation",
            "validation result",
            "test result",
            "coverage",
            "which test",
            "failing test",
        ),
    ),
    (
        CodeTask.CHANGE_CONTEXT,
        ("change context", "why changed", "diff context", "change set", "commit context"),
    ),
    (
        CodeTask.IMPACT_ANALYSIS,
        ("impact", "affected by", "dependents", "blast radius", "what breaks"),
    ),
    (
        CodeTask.BUG_LOCALIZATION,
        ("bug", "stack trace", "traceback", "exception", "root cause", "failing"),
    ),
    (
        CodeTask.CALL_PATH,
        ("call path", "callers", "callees", "who calls", "references to", "reference path"),
    ),
    (
        CodeTask.EXACT_LOCATION,
        ("exact location", "where is", "locate ", "definition of"),
    ),
    (
        CodeTask.IMPLEMENTATION,
        ("implementation", "how does", "how is", "source for"),
    ),
)


class CodeRewritePolicy(StrEnum):
    """Published deterministic rewrite family."""

    EXACT_LOCATOR = "exact_locator_v2"
    IMPLEMENTATION = "implementation_v2"
    CALL_REFERENCE = "call_reference_v2"
    BUG_SIGNAL = "bug_signal_v2"
    IMPACT = "impact_v2"
    CHANGE = "change_v2"
    HISTORICAL = "historical_v2"
    TEST_VALIDATION = "test_validation_v2"


class CodeMissingRolePolicy(StrEnum):
    """Result posture when a profile cannot produce a required evidence role."""

    DEGRADE = "degrade"
    REFUSE = "refuse"


class CodeCalibrationPolicy(StrEnum):
    """Whether calibration is deliberately disabled or attempted when supplied."""

    DISABLED = "disabled"
    OPTIONAL = "optional"


class CodePipelineStageStatus(StrEnum):
    """Frozen trace vocabulary for every run/skip/failure/fallback decision."""

    COMPLETE = "complete"
    COMPLETE_NO_MATCH = "complete_no_match"
    SKIPPED = "skipped"
    STOPPED = "stopped"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"
    DEGRADED = "degraded"
    REFUSED = "refused"


class CodeOptionalHookStatus(StrEnum):
    """Allowed outcomes from history and test-validation hooks."""

    COMPLETE = "complete"
    COMPLETE_NO_MATCH = "complete_no_match"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CodeAdaptiveK:
    """Deterministic output-size policy bounded by the request and candidate pool."""

    minimum: int
    default: int
    maximum: int
    graph_bonus: int = 2
    role_bonus: int = 1

    def __post_init__(self) -> None:
        values = (self.minimum, self.default, self.maximum, self.graph_bonus, self.role_bonus)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise TypeError("adaptive-k values must be integers")
        if not 1 <= self.minimum <= self.default <= self.maximum <= 50:
            raise ValueError("adaptive-k requires 1 <= minimum <= default <= maximum <= 50")
        if self.graph_bonus < 0 or self.role_bonus < 0:
            raise ValueError("adaptive-k bonuses must be non-negative")

    def resolve(
        self,
        *,
        request_limit: int,
        available: int,
        graph_recovered: bool,
        observed_roles: int,
        exact_fast_path: bool,
    ) -> int:
        if available <= 0:
            return 0
        if exact_fast_path:
            desired = self.minimum
        else:
            desired = self.default
            if graph_recovered:
                desired += self.graph_bonus
            desired += max(0, observed_roles - 1) * self.role_bonus
        return min(request_limit, available, self.maximum, max(self.minimum, desired))


@dataclass(frozen=True, slots=True)
class CodeContextTemplate:
    """Reader-facing role ordering and bounded context policy."""

    template_id: str
    role_order: tuple[CodeCandidateRole, ...]
    per_role_limit: int
    token_budget: int

    def __post_init__(self) -> None:
        if not self.template_id.strip():
            raise ValueError("context template_id must be non-empty")
        if len(self.role_order) != len(set(self.role_order)):
            raise ValueError("context role_order must not contain duplicates")
        if (
            isinstance(self.per_role_limit, bool)
            or not isinstance(self.per_role_limit, int)
            or self.per_role_limit < 1
        ):
            raise ValueError("context per_role_limit must be a positive integer")
        if (
            isinstance(self.token_budget, bool)
            or not isinstance(self.token_budget, int)
            or self.token_budget < 0
        ):
            raise ValueError("context token_budget must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class CodeQueryProfileV2:
    """One immutable registry entry plus its strict contract projection."""

    key: str
    version: str
    task: CodeTask
    rewrite_policy: CodeRewritePolicy
    enabled_channels: frozenset[CodeRetrievalChannel]
    budget: CodeRetrievalBudget
    directions: tuple[CodeTraversalDirection, ...]
    edge_types: tuple[CodeRelationType, ...]
    max_hops: int
    required_roles: tuple[CodeCandidateRole, ...]
    adaptive_k: CodeAdaptiveK
    context_template: CodeContextTemplate
    missing_role_policy: CodeMissingRolePolicy
    calibration_policy: CodeCalibrationPolicy

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.version.strip():
            raise ValueError("profile key and version must be non-empty")
        if len(self.required_roles) != len(set(self.required_roles)):
            raise ValueError("required_roles must not contain duplicates")
        budget_by_channel = {
            CodeRetrievalChannel.EXACT: self.budget.exact_candidates,
            CodeRetrievalChannel.SPARSE: self.budget.sparse_candidates,
            CodeRetrievalChannel.DENSE: self.budget.dense_candidates,
            CodeRetrievalChannel.GRAPH: self.budget.graph_candidates,
            CodeRetrievalChannel.HISTORY: self.budget.history_candidates,
            CodeRetrievalChannel.TEST: self.budget.test_candidates,
        }
        actual = frozenset(channel for channel, value in budget_by_channel.items() if value > 0)
        if actual != self.enabled_channels:
            raise ValueError("enabled_channels must exactly match non-zero channel budgets")
        if self.max_hops > 0 and CodeRetrievalChannel.GRAPH not in self.enabled_channels:
            raise ValueError("positive-hop profiles must enable graph retrieval")
        if self.max_hops == 0 and (self.directions or self.edge_types):
            raise ValueError("zero-hop profiles cannot publish graph filters")

    def contract(
        self,
        rewrite: CodeQueryRewrite,
        *,
        target_ref: str,
    ) -> CodeQueryProfile:
        """Project the richer registry entry onto the published strict contract."""

        return CodeQueryProfile(
            profile_version=self.version,
            task=self.task,
            target_identifiers=rewrite.target_identifiers,
            target_paths=rewrite.accepted_paths,
            target_ref=target_ref,
            directions=self.directions,
            edge_types=self.edge_types,
            max_hops=self.max_hops,
            require_tests=CodeRetrievalChannel.TEST in self.enabled_channels,
            include_history=CodeRetrievalChannel.HISTORY in self.enabled_channels,
            budget=self.budget,
        )


@dataclass(frozen=True, slots=True)
class CodeQueryRewrite:
    """Canonical query rewrite with accepted and rejected locator inputs separated."""

    version: str
    policy: CodeRewritePolicy
    original_query: str
    rewritten_query: str
    target_identifiers: tuple[str, ...]
    accepted_paths: tuple[str, ...]
    rejected_paths: tuple[str, ...]
    exact_fast_path: bool


@dataclass(frozen=True, slots=True)
class ResolvedCodeQueryProfile:
    """A deterministic registry resolution ready for retrieval."""

    definition: CodeQueryProfileV2
    rewrite: CodeQueryRewrite
    contract: CodeQueryProfile


@dataclass(frozen=True, slots=True)
class CodePipelineTraceEvent:
    """One immutable pipeline transition."""

    stage: str
    status: CodePipelineStageStatus
    reason: str
    input_count: int = 0
    output_count: int = 0
    component_version: str = ""


@dataclass(frozen=True, slots=True)
class CodeSourceFusionTrace:
    """Complete frozen routing and fallback trace for a fused Code request."""

    pipeline_version: str
    registry_version: str
    profile_key: str
    profile_version: str
    task: CodeTask
    rewrite: CodeQueryRewrite
    events: tuple[CodePipelineTraceEvent, ...]
    required_roles: tuple[CodeCandidateRole, ...]
    missing_roles: tuple[CodeCandidateRole, ...]
    requested_k: int
    adaptive_k: int
    refused: bool


class CodeSourceScopeAttestationError(ValueError):
    """A production source-scope attestation is absent, malformed, or invalid."""


@dataclass(frozen=True, slots=True)
class CodeSourceBlockPublicationIdentity:
    """Production-issued identity for one exact source-bearing context block."""

    block_id: str
    project_id: str
    repository_id: str
    stable_version: str
    source_generation: str
    entity_id: str
    retrieval_unit_id: str
    repository_path: str
    locator: str
    acl_ref: str
    watermark: str
    role: CodeCandidateRole
    content_identity: str
    block_identity: str


@dataclass(frozen=True, slots=True)
class CodeSourceScopeAttestation:
    """Opaque production authority over request scope and final result publication."""

    attestation_version: str
    task: CodeTask
    project_id: str
    repository_ids: tuple[str, ...]
    requested_commit: str | None
    requested_branch: str | None
    target_ref: str
    allowed_acl_refs: tuple[str, ...]
    enforce_acl: bool
    index_version: str
    watermark: str
    stable_versions: tuple[str, ...]
    source_generations: tuple[str, ...]
    requested_paths: tuple[str, ...]
    publication_paths: tuple[str, ...]
    candidate_identities: tuple[str, ...]
    context_blocks: tuple[CodeSourceBlockPublicationIdentity, ...]
    result_identity: str
    authority_tag: str


@dataclass(frozen=True, slots=True)
class CodeSourceFusionSearchResult:
    """Strict result paired with its immutable production orchestration trace."""

    result: CodeSourceResult
    trace: CodeSourceFusionTrace
    profile: ResolvedCodeQueryProfile
    scope_attestation: CodeSourceScopeAttestation | None = None


def _attestation_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeSourceScopeAttestationError(f"{field_name} must be a string")
    if (
        not value
        or value != value.strip()
        or _CONTROL_RE.search(value)
        or unicodedata.normalize("NFC", value) != value
    ):
        raise CodeSourceScopeAttestationError(
            f"{field_name} must be non-empty, trimmed, control-free, and NFC"
        )
    return value


def _attestation_sha256(value: bytes | str) -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _attested_locator_path(
    locator: str,
    stable_version: str,
    *,
    require_exact: bool = False,
) -> str:
    base = _attestation_text(locator, "locator").partition("#")[0]
    if not base.startswith("code://"):
        raise CodeSourceScopeAttestationError(
            "context attestation requires code:// repository locators"
        )
    _owner, marker, remainder = base.removeprefix("code://").rpartition("@")
    ref, slash, path = remainder.partition("/") if marker else ("", "", "")
    if not marker or not slash or ref != stable_version or not path:
        if require_exact:
            raise CodeSourceScopeAttestationError(
                "source block locator is missing an exact ref/path"
            )
        return "__locator_unavailable__/" + hashlib.sha256(locator.encode("utf-8")).hexdigest()
    return path


def _result_identity(result: CodeSourceResult) -> str:
    return _attestation_sha256(result.canonical_json_bytes())


def _candidate_identities(result: CodeSourceResult) -> tuple[str, ...]:
    return tuple("sha256:" + candidate.canonical_sha256() for candidate in result.candidates)


def _context_publication_identities(
    result: CodeSourceResult,
    *,
    project_id: str,
) -> tuple[CodeSourceBlockPublicationIdentity, ...]:
    values = []
    for block in result.context_blocks:
        values.append(
            CodeSourceBlockPublicationIdentity(
                block_id=block.block_id,
                project_id=project_id,
                repository_id=block.repository_id,
                stable_version=block.stable_version,
                source_generation=block.source_generation,
                entity_id=block.entity_id,
                retrieval_unit_id=block.retrieval_unit_id,
                repository_path=_attested_locator_path(
                    block.locator,
                    block.stable_version,
                    require_exact=True,
                ),
                locator=block.locator,
                acl_ref=block.acl_ref,
                watermark=result.watermark,
                role=block.role,
                content_identity=_attestation_sha256(block.content.encode("utf-8")),
                block_identity=_attestation_sha256(block.canonical_json_bytes()),
            )
        )
    return tuple(values)


def _attestation_payload(attestation: CodeSourceScopeAttestation) -> bytes:
    payload = {
        "allowed_acl_refs": list(attestation.allowed_acl_refs),
        "attestation_version": attestation.attestation_version,
        "candidate_identities": list(attestation.candidate_identities),
        "context_blocks": [
            {
                "acl_ref": item.acl_ref,
                "block_id": item.block_id,
                "block_identity": item.block_identity,
                "content_identity": item.content_identity,
                "entity_id": item.entity_id,
                "locator": item.locator,
                "project_id": item.project_id,
                "repository_id": item.repository_id,
                "repository_path": item.repository_path,
                "retrieval_unit_id": item.retrieval_unit_id,
                "role": item.role.value,
                "source_generation": item.source_generation,
                "stable_version": item.stable_version,
                "watermark": item.watermark,
            }
            for item in attestation.context_blocks
        ],
        "enforce_acl": attestation.enforce_acl,
        "index_version": attestation.index_version,
        "project_id": attestation.project_id,
        "publication_paths": list(attestation.publication_paths),
        "repository_ids": list(attestation.repository_ids),
        "requested_branch": attestation.requested_branch,
        "requested_commit": attestation.requested_commit,
        "requested_paths": list(attestation.requested_paths),
        "result_identity": attestation.result_identity,
        "source_generations": list(attestation.source_generations),
        "stable_versions": list(attestation.stable_versions),
        "target_ref": attestation.target_ref,
        "task": attestation.task.value,
        "watermark": attestation.watermark,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _authority_tag(attestation: CodeSourceScopeAttestation) -> str:
    digest = hmac.new(
        _SOURCE_SCOPE_AUTHORITY_KEY,
        _attestation_payload(attestation),
        hashlib.sha256,
    ).hexdigest()
    return "hmac-sha256:" + digest


def _governed_scope_values(
    request: EvidenceSearchRequest,
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    try:
        project_id = _attestation_text(
            request.scope.project_id,
            "scope.project_id",
        )
    except CodeSourceScopeAttestationError as error:
        raise CodeSourceScopeAttestationError(
            "scope attestation requires a governed project_id"
        ) from error
    repositories = tuple(
        sorted(
            _attestation_text(value, "scope.repository_id")
            for value in request.scope.repository_ids
        )
    )
    allowed_acl_refs = tuple(
        sorted(
            _attestation_text(value, "scope.allowed_acl_ref")
            for value in request.scope.allowed_acl_refs
        )
    )
    if not repositories or len(repositories) != len(set(repositories)):
        raise CodeSourceScopeAttestationError("scope attestation requires unique repository_ids")
    if (
        request.scope.enforce_acl is not True
        or not allowed_acl_refs
        or len(allowed_acl_refs) != len(set(allowed_acl_refs))
    ):
        raise CodeSourceScopeAttestationError(
            "scope attestation requires enforce_acl=true and unique allowed ACL refs"
        )
    return project_id, repositories, allowed_acl_refs


def _issue_code_source_scope_attestation(
    *,
    request: EvidenceSearchRequest,
    result: CodeSourceResult,
    resolved: ResolvedCodeQueryProfile,
) -> CodeSourceScopeAttestation:
    project_id, repository_ids, allowed_acl_refs = _governed_scope_values(request)
    if any(
        candidate.repository_id not in repository_ids or candidate.acl_ref not in allowed_acl_refs
        for candidate in result.candidates
    ):
        raise CodeSourceScopeAttestationError(
            "final candidates conflict with governed repository/ACL scope"
        )
    if any(
        block.repository_id not in repository_ids or block.acl_ref not in allowed_acl_refs
        for block in result.context_blocks
    ):
        raise CodeSourceScopeAttestationError(
            "final context blocks conflict with governed repository/ACL scope"
        )
    context_blocks = _context_publication_identities(
        result,
        project_id=project_id,
    )
    publication_paths = tuple(
        sorted(
            {
                _attested_locator_path(
                    candidate.locator,
                    candidate.stable_version,
                )
                for candidate in result.candidates
            }
            | {item.repository_path for item in context_blocks}
        )
    )
    unsigned = CodeSourceScopeAttestation(
        attestation_version=SOURCE_SCOPE_ATTESTATION_VERSION,
        task=resolved.definition.task,
        project_id=project_id,
        repository_ids=repository_ids,
        requested_commit=request.scope.commit,
        requested_branch=request.scope.branch,
        target_ref=resolved.contract.target_ref,
        allowed_acl_refs=allowed_acl_refs,
        enforce_acl=True,
        index_version=result.index_version,
        watermark=result.watermark,
        stable_versions=tuple(
            sorted({candidate.stable_version for candidate in result.candidates})
        ),
        source_generations=tuple(
            sorted({candidate.source_generation for candidate in result.candidates})
        ),
        requested_paths=tuple(sorted(resolved.rewrite.accepted_paths)),
        publication_paths=publication_paths,
        candidate_identities=_candidate_identities(result),
        context_blocks=context_blocks,
        result_identity=_result_identity(result),
        authority_tag="hmac-sha256:" + "0" * 64,
    )
    return replace(unsigned, authority_tag=_authority_tag(unsigned))


def _verify_code_source_scope_attestation(
    source: CodeSourceFusionSearchResult,
) -> CodeSourceScopeAttestation:
    attestation = source.scope_attestation
    if not isinstance(attestation, CodeSourceScopeAttestation):
        raise CodeSourceScopeAttestationError("exact production scope attestation is required")
    if attestation.attestation_version != SOURCE_SCOPE_ATTESTATION_VERSION:
        raise CodeSourceScopeAttestationError("unknown scope attestation version")
    expected_tag = _authority_tag(replace(attestation, authority_tag="hmac-sha256:" + "0" * 64))
    if not hmac.compare_digest(attestation.authority_tag, expected_tag):
        raise CodeSourceScopeAttestationError("scope attestation authority mismatch")
    result = source.result
    if (
        attestation.task is not source.trace.task
        or attestation.task is not source.profile.definition.task
        or attestation.index_version != result.index_version
        or attestation.watermark != result.watermark
        or attestation.result_identity != _result_identity(result)
        or attestation.candidate_identities != _candidate_identities(result)
    ):
        raise CodeSourceScopeAttestationError(
            "scope attestation does not bind the exact fusion result"
        )
    expected_context = _context_publication_identities(
        result,
        project_id=attestation.project_id,
    )
    expected_paths = tuple(
        sorted(
            {
                _attested_locator_path(
                    candidate.locator,
                    candidate.stable_version,
                )
                for candidate in result.candidates
            }
            | {item.repository_path for item in expected_context}
        )
    )
    if (
        attestation.context_blocks != expected_context
        or attestation.publication_paths != expected_paths
        or attestation.stable_versions
        != tuple(sorted({candidate.stable_version for candidate in result.candidates}))
        or attestation.source_generations
        != tuple(sorted({candidate.source_generation for candidate in result.candidates}))
        or any(
            candidate.repository_id not in attestation.repository_ids
            or candidate.acl_ref not in attestation.allowed_acl_refs
            for candidate in result.candidates
        )
        or any(
            block.repository_id not in attestation.repository_ids
            or block.acl_ref not in attestation.allowed_acl_refs
            for block in result.context_blocks
        )
        or attestation.enforce_acl is not True
        or not attestation.project_id
        or not attestation.repository_ids
        or not attestation.allowed_acl_refs
    ):
        raise CodeSourceScopeAttestationError("scope attestation publication manifest mismatch")
    return attestation


@dataclass(frozen=True, slots=True)
class CodeOptionalHookResult:
    """Strict return envelope for an optional history or test hook."""

    channel: CodeRetrievalChannel
    status: CodeOptionalHookStatus
    candidates: tuple[CodeRetrievalCandidate, ...] = ()
    hook_version: str = "unavailable"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.channel not in {
            CodeRetrievalChannel.HISTORY,
            CodeRetrievalChannel.TEST,
        }:
            raise ValueError("optional hooks are limited to history and test channels")
        if not self.hook_version.strip():
            raise ValueError("hook_version must be non-empty")
        for candidate in self.candidates:
            channels = {score.channel for score in candidate.raw_channel_scores}
            if self.channel not in channels:
                raise ValueError("hook candidates must carry the hook channel as a raw signal")
        if self.status is CodeOptionalHookStatus.COMPLETE and not self.candidates:
            raise ValueError("complete hook results require candidates")
        if self.status is not CodeOptionalHookStatus.COMPLETE and self.candidates:
            raise ValueError("non-complete hook results cannot carry candidates")
        if (
            self.status
            in {
                CodeOptionalHookStatus.UNAVAILABLE,
                CodeOptionalHookStatus.TIMEOUT,
                CodeOptionalHookStatus.ERROR,
            }
            and not self.reason.strip()
        ):
            raise ValueError("failed hook results require a reason")


@runtime_checkable
class CodeHistoryExpansionHook(Protocol):
    """Optional read-only history candidate producer."""

    hook_version: str

    def expand(
        self,
        *,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        seeds: tuple[CodeRetrievalCandidate, ...],
        limit: int,
        deadline: float | None,
    ) -> CodeOptionalHookResult: ...


@runtime_checkable
class CodeTestValidationExpansionHook(Protocol):
    """Optional read-only test/validation candidate producer."""

    hook_version: str

    def expand(
        self,
        *,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        seeds: tuple[CodeRetrievalCandidate, ...],
        limit: int,
        deadline: float | None,
    ) -> CodeOptionalHookResult: ...


@runtime_checkable
class CodeProfiledRetriever(Protocol):
    """Narrow existing exact/sparse or hybrid retrieval boundary."""

    def search_with_trace(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
    ) -> Any: ...


@runtime_checkable
class CodeCandidateReranker(Protocol):
    """Existing deterministic reranker boundary."""

    version: str
    model_version: str

    def rerank(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        response: Any,
        *,
        top_k: int,
        deadline: float | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> Any: ...


def _budget(
    *,
    total: int,
    exact: int,
    sparse: int,
    dense: int,
    graph: int = 0,
    history: int = 0,
    test: int = 0,
    nodes: int = 0,
    edges: int = 0,
    context: int = 4_000,
) -> CodeRetrievalBudget:
    return CodeRetrievalBudget(
        total_candidates=total,
        exact_candidates=exact,
        sparse_candidates=sparse,
        dense_candidates=dense,
        graph_candidates=graph,
        history_candidates=history,
        test_candidates=test,
        graph_node_budget=nodes,
        graph_edge_budget=edges,
        context_token_budget=context,
    )


def _template(
    name: str,
    roles: tuple[CodeCandidateRole, ...],
    *,
    per_role: int = 4,
    tokens: int = 4_000,
) -> CodeContextTemplate:
    return CodeContextTemplate(
        template_id=f"code-context/{name}@v2",
        role_order=roles,
        per_role_limit=per_role,
        token_budget=tokens,
    )


def _profile_registry() -> Mapping[CodeTask, CodeQueryProfileV2]:
    version = "code-query-profile-v2"
    target = (CodeCandidateRole.TARGET,)
    target_dependency = (CodeCandidateRole.TARGET, CodeCandidateRole.DEPENDENCY)
    target_test = (CodeCandidateRole.TARGET, CodeCandidateRole.TEST)
    target_history = (CodeCandidateRole.TARGET, CodeCandidateRole.HISTORY)
    profiles = {
        CodeTask.EXACT_LOCATION: CodeQueryProfileV2(
            key="exact-location",
            version=version,
            task=CodeTask.EXACT_LOCATION,
            rewrite_policy=CodeRewritePolicy.EXACT_LOCATOR,
            enabled_channels=frozenset({CodeRetrievalChannel.EXACT}),
            budget=_budget(total=20, exact=20, sparse=0, dense=0, context=2_000),
            directions=(),
            edge_types=(),
            max_hops=0,
            required_roles=target,
            adaptive_k=CodeAdaptiveK(1, 3, 8, graph_bonus=0),
            context_template=_template("exact-location", target, per_role=8, tokens=2_000),
            missing_role_policy=CodeMissingRolePolicy.DEGRADE,
            calibration_policy=CodeCalibrationPolicy.DISABLED,
        ),
        CodeTask.IMPLEMENTATION: CodeQueryProfileV2(
            key="implementation",
            version=version,
            task=CodeTask.IMPLEMENTATION,
            rewrite_policy=CodeRewritePolicy.IMPLEMENTATION,
            enabled_channels=frozenset(
                {
                    CodeRetrievalChannel.EXACT,
                    CodeRetrievalChannel.SPARSE,
                    CodeRetrievalChannel.DENSE,
                    CodeRetrievalChannel.GRAPH,
                }
            ),
            budget=_budget(
                total=50,
                exact=20,
                sparse=40,
                dense=40,
                graph=12,
                nodes=48,
                edges=96,
            ),
            directions=(
                CodeTraversalDirection.INCOMING,
                CodeTraversalDirection.OUTGOING,
            ),
            edge_types=(
                CodeRelationType.DEFINES,
                CodeRelationType.IMPORTS,
                CodeRelationType.IMPLEMENTS,
                CodeRelationType.OVERRIDES,
            ),
            max_hops=1,
            required_roles=target,
            adaptive_k=CodeAdaptiveK(3, 8, 14),
            context_template=_template(
                "implementation",
                (CodeCandidateRole.TARGET, CodeCandidateRole.DEPENDENCY),
            ),
            missing_role_policy=CodeMissingRolePolicy.DEGRADE,
            calibration_policy=CodeCalibrationPolicy.OPTIONAL,
        ),
        CodeTask.CALL_PATH: CodeQueryProfileV2(
            key="call-reference-path",
            version=version,
            task=CodeTask.CALL_PATH,
            rewrite_policy=CodeRewritePolicy.CALL_REFERENCE,
            enabled_channels=frozenset(
                {
                    CodeRetrievalChannel.EXACT,
                    CodeRetrievalChannel.SPARSE,
                    CodeRetrievalChannel.DENSE,
                    CodeRetrievalChannel.GRAPH,
                }
            ),
            budget=_budget(
                total=50,
                exact=20,
                sparse=36,
                dense=32,
                graph=24,
                nodes=96,
                edges=192,
            ),
            directions=(
                CodeTraversalDirection.INCOMING,
                CodeTraversalDirection.OUTGOING,
            ),
            edge_types=(CodeRelationType.CALLS, CodeRelationType.REFERENCES),
            max_hops=3,
            required_roles=target_dependency,
            adaptive_k=CodeAdaptiveK(4, 10, 18, graph_bonus=4),
            context_template=_template("call-path", target_dependency, per_role=8),
            missing_role_policy=CodeMissingRolePolicy.DEGRADE,
            calibration_policy=CodeCalibrationPolicy.OPTIONAL,
        ),
        CodeTask.BUG_LOCALIZATION: CodeQueryProfileV2(
            key="bug-localization",
            version=version,
            task=CodeTask.BUG_LOCALIZATION,
            rewrite_policy=CodeRewritePolicy.BUG_SIGNAL,
            enabled_channels=frozenset(
                {
                    CodeRetrievalChannel.EXACT,
                    CodeRetrievalChannel.SPARSE,
                    CodeRetrievalChannel.DENSE,
                    CodeRetrievalChannel.GRAPH,
                    CodeRetrievalChannel.TEST,
                }
            ),
            budget=_budget(
                total=50,
                exact=20,
                sparse=40,
                dense=40,
                graph=24,
                test=16,
                nodes=96,
                edges=192,
            ),
            directions=(
                CodeTraversalDirection.INCOMING,
                CodeTraversalDirection.OUTGOING,
            ),
            edge_types=(
                CodeRelationType.CALLS,
                CodeRelationType.REFERENCES,
                CodeRelationType.TESTS,
                CodeRelationType.COVERS,
            ),
            max_hops=2,
            required_roles=target,
            adaptive_k=CodeAdaptiveK(5, 12, 20, graph_bonus=3),
            context_template=_template(
                "bug-localization",
                (
                    CodeCandidateRole.TARGET,
                    CodeCandidateRole.TEST,
                    CodeCandidateRole.DEPENDENCY,
                ),
            ),
            missing_role_policy=CodeMissingRolePolicy.DEGRADE,
            calibration_policy=CodeCalibrationPolicy.OPTIONAL,
        ),
        CodeTask.IMPACT_ANALYSIS: CodeQueryProfileV2(
            key="impact-analysis",
            version=version,
            task=CodeTask.IMPACT_ANALYSIS,
            rewrite_policy=CodeRewritePolicy.IMPACT,
            enabled_channels=frozenset(
                {
                    CodeRetrievalChannel.EXACT,
                    CodeRetrievalChannel.SPARSE,
                    CodeRetrievalChannel.DENSE,
                    CodeRetrievalChannel.GRAPH,
                }
            ),
            budget=_budget(
                total=50,
                exact=16,
                sparse=36,
                dense=36,
                graph=28,
                nodes=128,
                edges=256,
            ),
            directions=(
                CodeTraversalDirection.INCOMING,
                CodeTraversalDirection.OUTGOING,
            ),
            edge_types=(
                CodeRelationType.IMPORTS,
                CodeRelationType.CALLS,
                CodeRelationType.REFERENCES,
                CodeRelationType.TESTS,
                CodeRelationType.AFFECTS,
            ),
            max_hops=3,
            required_roles=target_dependency,
            adaptive_k=CodeAdaptiveK(6, 14, 24, graph_bonus=5),
            context_template=_template("impact", target_dependency, per_role=10),
            missing_role_policy=CodeMissingRolePolicy.REFUSE,
            calibration_policy=CodeCalibrationPolicy.OPTIONAL,
        ),
        CodeTask.CHANGE_CONTEXT: CodeQueryProfileV2(
            key="change-context",
            version=version,
            task=CodeTask.CHANGE_CONTEXT,
            rewrite_policy=CodeRewritePolicy.CHANGE,
            enabled_channels=frozenset(
                {
                    CodeRetrievalChannel.EXACT,
                    CodeRetrievalChannel.SPARSE,
                    CodeRetrievalChannel.DENSE,
                    CodeRetrievalChannel.GRAPH,
                    CodeRetrievalChannel.HISTORY,
                }
            ),
            budget=_budget(
                total=50,
                exact=16,
                sparse=36,
                dense=32,
                graph=20,
                history=20,
                nodes=96,
                edges=192,
            ),
            directions=(
                CodeTraversalDirection.INCOMING,
                CodeTraversalDirection.OUTGOING,
            ),
            edge_types=(
                CodeRelationType.AFFECTS,
                CodeRelationType.VALIDATED_BY,
                CodeRelationType.FAILED_VALIDATION,
                CodeRelationType.SAME_SYMBOL_AS,
                CodeRelationType.RENAMED_TO,
                CodeRelationType.MOVED_TO,
            ),
            max_hops=2,
            required_roles=target_history,
            adaptive_k=CodeAdaptiveK(5, 12, 20, graph_bonus=3),
            context_template=_template("change-context", target_history, per_role=8),
            missing_role_policy=CodeMissingRolePolicy.DEGRADE,
            calibration_policy=CodeCalibrationPolicy.OPTIONAL,
        ),
        CodeTask.HISTORICAL: CodeQueryProfileV2(
            key="historical",
            version=version,
            task=CodeTask.HISTORICAL,
            rewrite_policy=CodeRewritePolicy.HISTORICAL,
            enabled_channels=frozenset(
                {
                    CodeRetrievalChannel.EXACT,
                    CodeRetrievalChannel.SPARSE,
                    CodeRetrievalChannel.DENSE,
                    CodeRetrievalChannel.GRAPH,
                    CodeRetrievalChannel.HISTORY,
                }
            ),
            budget=_budget(
                total=50,
                exact=16,
                sparse=32,
                dense=28,
                graph=20,
                history=28,
                nodes=96,
                edges=192,
            ),
            directions=(
                CodeTraversalDirection.INCOMING,
                CodeTraversalDirection.OUTGOING,
            ),
            edge_types=(
                CodeRelationType.CONTAINS,
                CodeRelationType.AFFECTS,
                CodeRelationType.VALIDATED_BY,
                CodeRelationType.FAILED_VALIDATION,
                CodeRelationType.SAME_SYMBOL_AS,
                CodeRelationType.RENAMED_TO,
                CodeRelationType.MOVED_TO,
            ),
            max_hops=3,
            required_roles=(CodeCandidateRole.HISTORY,),
            adaptive_k=CodeAdaptiveK(5, 12, 22, graph_bonus=4),
            context_template=_template(
                "historical",
                (CodeCandidateRole.HISTORY, CodeCandidateRole.TARGET),
                per_role=10,
            ),
            missing_role_policy=CodeMissingRolePolicy.REFUSE,
            calibration_policy=CodeCalibrationPolicy.OPTIONAL,
        ),
        CodeTask.TEST_VALIDATION: CodeQueryProfileV2(
            key="test-validation",
            version=version,
            task=CodeTask.TEST_VALIDATION,
            rewrite_policy=CodeRewritePolicy.TEST_VALIDATION,
            enabled_channels=frozenset(
                {
                    CodeRetrievalChannel.EXACT,
                    CodeRetrievalChannel.SPARSE,
                    CodeRetrievalChannel.DENSE,
                    CodeRetrievalChannel.GRAPH,
                    CodeRetrievalChannel.TEST,
                }
            ),
            budget=_budget(
                total=50,
                exact=16,
                sparse=36,
                dense=32,
                graph=24,
                test=28,
                nodes=112,
                edges=224,
            ),
            directions=(
                CodeTraversalDirection.INCOMING,
                CodeTraversalDirection.OUTGOING,
            ),
            edge_types=(
                CodeRelationType.TESTS,
                CodeRelationType.COVERS,
                CodeRelationType.VALIDATED_BY,
                CodeRelationType.FAILED_VALIDATION,
            ),
            max_hops=2,
            required_roles=target_test,
            adaptive_k=CodeAdaptiveK(5, 12, 20, graph_bonus=3),
            context_template=_template("test-validation", target_test, per_role=10),
            missing_role_policy=CodeMissingRolePolicy.REFUSE,
            calibration_policy=CodeCalibrationPolicy.OPTIONAL,
        ),
    }
    if set(profiles) != set(CodeTask):
        raise RuntimeError("Code query profile registry must cover every CodeTask")
    return MappingProxyType(profiles)


CODE_QUERY_PROFILE_REGISTRY = _profile_registry()
CODE_QUERY_PROFILES = CODE_QUERY_PROFILE_REGISTRY
CODE_QUERY_PROFILE_REGISTRY_BY_KEY: Mapping[str, CodeQueryProfileV2] = MappingProxyType(
    {profile.key: profile for profile in CODE_QUERY_PROFILE_REGISTRY.values()}
)


def get_code_query_profile(task: CodeTask | str) -> CodeQueryProfileV2:
    """Return the immutable registry profile for a task or published key."""

    if isinstance(task, str) and task in CODE_QUERY_PROFILE_REGISTRY_BY_KEY:
        return CODE_QUERY_PROFILE_REGISTRY_BY_KEY[task]
    try:
        resolved = CodeTask(task)
    except (TypeError, ValueError) as error:
        raise ValueError(f"unknown Code query profile: {task!r}") from error
    return CODE_QUERY_PROFILE_REGISTRY[resolved]


def infer_code_task(query: str) -> CodeTask:
    """Infer one task using a stable, published keyword order."""

    normalized = _normalize_query(query)
    folded = normalized.casefold()
    if _is_simple_exact_query(normalized):
        return CodeTask.EXACT_LOCATION
    for task, keywords in _TASK_KEYWORDS:
        if any(keyword in folded for keyword in keywords):
            return task
    return CodeTask.IMPLEMENTATION


def rewrite_code_query(
    query: str,
    profile: CodeQueryProfileV2 | CodeTask | str,
) -> CodeQueryRewrite:
    """Apply the deterministic rewrite and fail closed on control input."""

    definition = (
        profile if isinstance(profile, CodeQueryProfileV2) else get_code_query_profile(profile)
    )
    normalized = _normalize_query(query)
    non_uri_query = _URI_PATH_RE.sub(" ", normalized)
    raw_paths = [
        *_URI_PATH_RE.findall(normalized),
        *_UNSAFE_PATH_RE.findall(non_uri_query),
        *_PATH_RE.findall(non_uri_query),
    ]
    accepted_paths: list[str] = []
    rejected_paths: list[str] = []
    for raw in raw_paths:
        path = _safe_relative_path(raw)
        if path is None:
            rejected_paths.append(raw)
        elif path not in accepted_paths:
            accepted_paths.append(path)
    accepted_paths = [
        path
        for path in accepted_paths
        if not any(
            rejected.replace("\\", "/").endswith("/" + path)
            or rejected.replace("\\", "/").lstrip("./") == path
            for rejected in rejected_paths
        )
    ]

    identifier_source = normalized
    for rejected_path in sorted(set(rejected_paths), key=lambda value: (-len(value), value)):
        identifier_source = identifier_source.replace(rejected_path, " ")
    identifier_source = " ".join(identifier_source.split())
    rewritten_query = identifier_source or "unresolved locator input"

    identifiers: list[str] = []
    for value in (
        *_QUALIFIED_RE.findall(identifier_source),
        *_IDENTIFIER_RE.findall(identifier_source),
    ):
        if value.casefold() in _STOP_IDENTIFIERS:
            continue
        if "/" in value or value in identifiers:
            continue
        identifiers.append(value)

    return CodeQueryRewrite(
        version=REWRITE_VERSION,
        policy=definition.rewrite_policy,
        original_query=query,
        rewritten_query=rewritten_query,
        target_identifiers=tuple(sorted(identifiers)),
        accepted_paths=tuple(sorted(accepted_paths)),
        rejected_paths=tuple(sorted(set(rejected_paths))),
        exact_fast_path=(
            definition.task is CodeTask.EXACT_LOCATION and _is_simple_exact_query(normalized)
        ),
    )


def resolve_code_query_profile(
    request_or_query: EvidenceSearchRequest | str,
    task: CodeTask | str | CodeQueryProfile | CodeQueryProfileV2 | None = None,
) -> ResolvedCodeQueryProfile:
    """Resolve task, deterministic rewrite, and strict contract without I/O."""

    if isinstance(request_or_query, EvidenceSearchRequest):
        request = request_or_query
        query = request.query
        target_ref = request.scope.commit or request.scope.branch or "current"
    elif isinstance(request_or_query, str):
        query = request_or_query
        target_ref = "current"
    else:
        raise TypeError("request_or_query must be EvidenceSearchRequest or str")

    provided_contract = task if isinstance(task, CodeQueryProfile) else None
    if isinstance(task, CodeQueryProfileV2):
        definition = task
    elif provided_contract is not None:
        definition = get_code_query_profile(provided_contract.task)
    else:
        definition = get_code_query_profile(task or infer_code_task(query))
    rewrite = rewrite_code_query(query, definition)
    contract = definition.contract(rewrite, target_ref=_normalize_target_ref(target_ref))
    if provided_contract is not None:
        contract = contract.model_copy(
            update={
                "target_identifiers": tuple(
                    sorted(
                        set(contract.target_identifiers) | set(provided_contract.target_identifiers)
                    )
                ),
                "target_paths": tuple(
                    sorted(set(contract.target_paths) | set(provided_contract.target_paths))
                ),
                "target_ref": provided_contract.target_ref,
            }
        )
    return ResolvedCodeQueryProfile(
        definition=definition,
        rewrite=rewrite,
        contract=contract,
    )


@dataclass(slots=True)
class _ChannelState:
    kind: str
    hit_count: int = 0
    message: str = ""


@dataclass(frozen=True, slots=True)
class _FailedRetrievalResponse:
    result: CodeSourceResult


class CodeSourceFusionPipeline:
    """Synchronous production composition of the existing Code retrieval stages."""

    pipeline_version = PIPELINE_VERSION

    def __init__(
        self,
        exact_sparse_retriever: CodeProfiledRetriever,
        *,
        hybrid_retriever: CodeProfiledRetriever | None = None,
        graph_retriever: Any | None = None,
        reranker: CodeCandidateReranker | None = None,
        calibration_artifact: Any | None = None,
        calibration_function: Callable[..., Any] | None = None,
        history_hook: CodeHistoryExpansionHook | None = None,
        test_hook: CodeTestValidationExpansionHook | None = None,
        clock: Callable[[], float] = time.monotonic,
        latency_clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not callable(getattr(exact_sparse_retriever, "search_with_trace", None)):
            raise TypeError("exact_sparse_retriever must expose search_with_trace()")
        for name, value in (
            ("hybrid_retriever", hybrid_retriever),
            ("graph_retriever", graph_retriever),
        ):
            if value is not None and not callable(getattr(value, "search_with_trace", None)):
                raise TypeError(f"{name} must expose search_with_trace()")
        if reranker is not None and not callable(getattr(reranker, "rerank", None)):
            raise TypeError("reranker must expose rerank()")
        for name, hook in (("history_hook", history_hook), ("test_hook", test_hook)):
            if hook is not None and not callable(getattr(hook, "expand", None)):
                raise TypeError(f"{name} must expose expand()")
        if calibration_function is not None and not callable(calibration_function):
            raise TypeError("calibration_function must be callable")
        if not callable(clock) or not callable(latency_clock):
            raise TypeError("clock and latency_clock must be callable")
        self.exact_sparse_retriever = exact_sparse_retriever
        self.hybrid_retriever = hybrid_retriever
        self.graph_retriever = graph_retriever
        self.reranker = reranker
        self.calibration_artifact = calibration_artifact
        self.calibration_function = calibration_function
        self.history_hook = history_hook
        self.test_hook = test_hook
        self.clock = clock
        self.latency_clock = latency_clock

    def search(
        self,
        request: EvidenceSearchRequest,
        profile: CodeTask | str | CodeQueryProfile | CodeQueryProfileV2 | None = None,
        *,
        task: CodeTask | str | None = None,
        deadline: float | None = None,
    ) -> CodeSourceResult:
        return self.search_with_trace(
            request,
            profile,
            task=task,
            deadline=deadline,
        ).result

    def search_with_trace(
        self,
        request: EvidenceSearchRequest,
        profile: CodeTask | str | CodeQueryProfile | CodeQueryProfileV2 | None = None,
        *,
        task: CodeTask | str | None = None,
        deadline: float | None = None,
    ) -> CodeSourceFusionSearchResult:
        started = self.latency_clock()
        if not isinstance(request, EvidenceSearchRequest):
            raise TypeError("request must be an EvidenceSearchRequest")
        _governed_scope_values(request)
        if profile is not None and task is not None:
            raise ValueError("pass profile or task, not both")
        if deadline is not None:
            if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
                raise TypeError("deadline must be a finite monotonic timestamp")
            deadline = float(deadline)
            if not math.isfinite(deadline):
                raise ValueError("deadline must be a finite monotonic timestamp")

        resolved = resolve_code_query_profile(request, profile if profile is not None else task)
        definition = resolved.definition
        contract = resolved.contract
        events: list[CodePipelineTraceEvent] = [
            CodePipelineTraceEvent(
                stage="profile",
                status=CodePipelineStageStatus.COMPLETE,
                reason=(
                    f"resolved {definition.key}; accepted_paths="
                    f"{len(resolved.rewrite.accepted_paths)}; "
                    f"rejected_paths={len(resolved.rewrite.rejected_paths)}"
                ),
                component_version=definition.version,
            )
        ]
        expanded_request = EvidenceSearchRequest(
            query=resolved.rewrite.rewritten_query,
            scope=request.scope,
            limit=min(50, contract.budget.total_candidates),
            include_edges=request.include_edges,
        )

        base_response: Any | None = None
        hybrid_response: Any | None = None
        base_result: CodeSourceResult
        dense_missing = False
        if resolved.rewrite.exact_fast_path:
            base_response = self._run_retriever(
                self.exact_sparse_retriever,
                expanded_request,
                contract,
                stage="exact-sparse",
                events=events,
            )
            base_result = _response_result(base_response)
            events.append(
                CodePipelineTraceEvent(
                    stage="dense",
                    status=CodePipelineStageStatus.SKIPPED,
                    reason="simple exact identifier/location fast path disables dense retrieval",
                )
            )
        elif (
            CodeRetrievalChannel.DENSE in definition.enabled_channels
            and self.hybrid_retriever is not None
        ):
            try:
                hybrid_response = self.hybrid_retriever.search_with_trace(
                    expanded_request,
                    contract,
                )
                base_response = hybrid_response
                base_result = _response_result(hybrid_response)
                if base_result.status in {
                    CodeSourceStatus.TIMEOUT,
                    CodeSourceStatus.UNAVAILABLE,
                }:
                    dense_missing = True
                    events.append(
                        CodePipelineTraceEvent(
                            stage="hybrid-seed-fusion",
                            status=CodePipelineStageStatus.FALLBACK,
                            reason=(
                                f"hybrid returned {base_result.status.value}; "
                                "exact/sparse fallback invoked"
                            ),
                            component_version=_component_version(self.hybrid_retriever),
                        )
                    )
                    base_response = self._run_retriever(
                        self.exact_sparse_retriever,
                        expanded_request,
                        contract,
                        stage="exact-sparse-fallback",
                        events=events,
                    )
                    base_result = _response_result(base_response)
                    hybrid_response = None
                else:
                    events.append(
                        CodePipelineTraceEvent(
                            stage="hybrid-seed-fusion",
                            status=_result_stage_status(base_result),
                            reason="exact, sparse, and dense seed fusion completed",
                            output_count=len(base_result.candidates),
                            component_version=_component_version(self.hybrid_retriever),
                        )
                    )
            except Exception as error:
                dense_missing = True
                events.append(
                    CodePipelineTraceEvent(
                        stage="hybrid-seed-fusion",
                        status=CodePipelineStageStatus.FALLBACK,
                        reason=_safe_message(
                            "hybrid retrieval failed; exact/sparse fallback", error
                        ),
                        component_version=_component_version(self.hybrid_retriever),
                    )
                )
                base_response = self._run_retriever(
                    self.exact_sparse_retriever,
                    expanded_request,
                    contract,
                    stage="exact-sparse-fallback",
                    events=events,
                )
                base_result = _response_result(base_response)
        else:
            dense_missing = CodeRetrievalChannel.DENSE in definition.enabled_channels
            if dense_missing:
                events.append(
                    CodePipelineTraceEvent(
                        stage="dense",
                        status=CodePipelineStageStatus.UNAVAILABLE,
                        reason="dense channel enabled but no hybrid retriever was injected",
                    )
                )
            base_response = self._run_retriever(
                self.exact_sparse_retriever,
                expanded_request,
                contract,
                stage="exact-sparse-fallback" if dense_missing else "exact-sparse",
                events=events,
            )
            base_result = _response_result(base_response)

        states = _states_from_result(base_result)
        if dense_missing:
            states[CodeRetrievalChannel.DENSE] = _ChannelState(
                "unavailable",
                message="dense channel enabled but hybrid retrieval was unavailable",
            )
        for channel in (CodeRetrievalChannel.EXACT, CodeRetrievalChannel.SPARSE):
            events.append(_channel_event(channel, states[channel]))
        if not resolved.rewrite.exact_fast_path and not dense_missing:
            events.append(
                _channel_event(CodeRetrievalChannel.DENSE, states[CodeRetrievalChannel.DENSE])
            )

        candidates = list(base_result.candidates)
        graph_recovered = False
        if CodeRetrievalChannel.GRAPH not in definition.enabled_channels:
            states[CodeRetrievalChannel.GRAPH] = _ChannelState("disabled")
            events.append(
                CodePipelineTraceEvent(
                    stage="graph",
                    status=CodePipelineStageStatus.SKIPPED,
                    reason="graph channel disabled by the resolved profile",
                    input_count=len(candidates),
                )
            )
        elif self.graph_retriever is None:
            message = "graph channel enabled but no typed graph retriever was injected"
            states[CodeRetrievalChannel.GRAPH] = _ChannelState("unavailable", message=message)
            events.append(
                CodePipelineTraceEvent(
                    stage="graph",
                    status=CodePipelineStageStatus.UNAVAILABLE,
                    reason=message,
                    input_count=len(candidates),
                )
            )
        elif not candidates:
            base_failed = base_result.status in {
                CodeSourceStatus.PARTIAL,
                CodeSourceStatus.TIMEOUT,
                CodeSourceStatus.UNAVAILABLE,
            }
            message = (
                "graph stopped because seed retrieval produced no authorized candidates"
                if base_failed
                else "graph expansion requires at least one authorized seed"
            )
            states[CodeRetrievalChannel.GRAPH] = _ChannelState(
                "unavailable" if base_failed else "complete_no_match",
                message=message if base_failed else "",
            )
            events.append(
                CodePipelineTraceEvent(
                    stage="graph",
                    status=(
                        CodePipelineStageStatus.STOPPED
                        if base_failed
                        else CodePipelineStageStatus.SKIPPED
                    ),
                    reason=message,
                )
            )
        else:
            graph_candidates, graph_state, graph_event = self._expand_graph(
                expanded_request,
                contract,
                tuple(candidates),
                deadline=deadline,
            )
            states[CodeRetrievalChannel.GRAPH] = graph_state
            events.append(graph_event)
            if graph_state.kind in {"complete_hits", "complete_pruned"}:
                try:
                    candidates = list(
                        _fuse_graph(
                            candidates,
                            graph_candidates,
                            limit=contract.budget.total_candidates,
                        )
                    )
                    graph_recovered = bool(graph_candidates)
                except Exception as error:
                    message = _safe_message(
                        "graph fusion failed; seed order retained",
                        error,
                    )
                    states[CodeRetrievalChannel.GRAPH] = _ChannelState(
                        "error",
                        message=message,
                    )
                    events.append(
                        CodePipelineTraceEvent(
                            stage="graph-fusion",
                            status=CodePipelineStageStatus.FALLBACK,
                            reason=message,
                            input_count=len(candidates) + len(graph_candidates),
                            output_count=len(candidates),
                        )
                    )

        candidates = self._run_hook(
            channel=CodeRetrievalChannel.HISTORY,
            hook=self.history_hook,
            request=expanded_request,
            contract=contract,
            enabled=CodeRetrievalChannel.HISTORY in definition.enabled_channels,
            limit=contract.budget.history_candidates,
            candidates=candidates,
            states=states,
            events=events,
            deadline=deadline,
        )
        candidates = self._run_hook(
            channel=CodeRetrievalChannel.TEST,
            hook=self.test_hook,
            request=expanded_request,
            contract=contract,
            enabled=CodeRetrievalChannel.TEST in definition.enabled_channels,
            limit=contract.budget.test_candidates,
            candidates=candidates,
            states=states,
            events=events,
            deadline=deadline,
        )

        governed, rejected = _govern_and_deduplicate(
            candidates,
            request,
            allow_history=CodeRetrievalChannel.HISTORY in definition.enabled_channels,
        )
        events.append(
            CodePipelineTraceEvent(
                stage="entity-unit-version-dedup",
                status=CodePipelineStageStatus.COMPLETE,
                reason=f"removed {rejected} duplicate or out-of-scope candidates",
                input_count=len(candidates),
                output_count=len(governed),
            )
        )
        candidates = list(governed)

        if self.reranker is None:
            events.append(
                CodePipelineTraceEvent(
                    stage="rerank",
                    status=CodePipelineStageStatus.SKIPPED,
                    reason="no reranker was injected; stable fused order retained",
                    input_count=len(candidates),
                    output_count=len(candidates),
                )
            )
        elif not candidates:
            events.append(
                CodePipelineTraceEvent(
                    stage="rerank",
                    status=CodePipelineStageStatus.SKIPPED,
                    reason="rerank skipped because the governed candidate pool is empty",
                )
            )
        elif hybrid_response is None:
            events.append(
                CodePipelineTraceEvent(
                    stage="rerank",
                    status=CodePipelineStageStatus.FALLBACK,
                    reason="existing reranker requires a hybrid trace; stable fused order retained",
                    input_count=len(candidates),
                    output_count=len(candidates),
                    component_version=_component_version(self.reranker),
                )
            )
        else:
            try:
                rerank_input = _rerank_response(
                    hybrid_response,
                    base_result,
                    candidates,
                    states,
                )
                reranked = self.reranker.rerank(
                    expanded_request,
                    contract,
                    rerank_input,
                    top_k=len(candidates),
                    deadline=deadline,
                    clock=self.clock,
                )
                values = reranked[0] if isinstance(reranked, tuple) else reranked
                candidates = list(values)
                events.append(
                    CodePipelineTraceEvent(
                        stage="rerank",
                        status=CodePipelineStageStatus.COMPLETE,
                        reason="injected reranker completed",
                        input_count=len(governed),
                        output_count=len(candidates),
                        component_version=_component_version(self.reranker),
                    )
                )
            except Exception as error:
                candidates = list(governed)
                events.append(
                    CodePipelineTraceEvent(
                        stage="rerank",
                        status=CodePipelineStageStatus.FALLBACK,
                        reason=_safe_message("rerank failed; stable fused order retained", error),
                        input_count=len(governed),
                        output_count=len(candidates),
                        component_version=_component_version(self.reranker),
                    )
                )

        candidates, calibration_event = self._calibrate(
            candidates,
            definition=definition,
            index_version=base_result.index_version,
        )
        events.append(calibration_event)

        observed_roles = len({candidate.role for candidate in candidates})
        adaptive_k = definition.adaptive_k.resolve(
            request_limit=request.limit,
            available=len(candidates),
            graph_recovered=graph_recovered,
            observed_roles=observed_roles,
            exact_fast_path=resolved.rewrite.exact_fast_path,
        )
        hard_exact_count = sum(
            CodeRetrievalChannel.EXACT in {rank.channel for rank in candidate.raw_channel_ranks}
            for candidate in candidates
        )
        adaptive_k = min(
            request.limit,
            len(candidates),
            max(adaptive_k, hard_exact_count),
        )
        candidates = list(
            _adaptive_select(
                candidates,
                adaptive_k,
                required_roles=definition.required_roles,
            )
        )
        events.append(
            CodePipelineTraceEvent(
                stage="adaptive-k",
                status=CodePipelineStageStatus.COMPLETE,
                reason=(
                    f"selected k={adaptive_k}; profile target range "
                    f"[{definition.adaptive_k.minimum},{definition.adaptive_k.maximum}], "
                    "with exact-hard-signal and required-role preservation"
                ),
                input_count=len(governed),
                output_count=len(candidates),
            )
        )

        present_roles = {candidate.role for candidate in candidates}
        missing_roles = tuple(
            role for role in definition.required_roles if role not in present_roles
        )
        refused = False
        if missing_roles and definition.missing_role_policy is CodeMissingRolePolicy.REFUSE:
            refused = True
            message = "required roles missing: " + ", ".join(role.value for role in missing_roles)
            failure_channel = _missing_role_channel(missing_roles[0])
            states[failure_channel] = _ChannelState("unavailable", message=message)
            candidates = []
            events.append(
                CodePipelineTraceEvent(
                    stage="required-roles",
                    status=CodePipelineStageStatus.REFUSED,
                    reason=message,
                    input_count=adaptive_k,
                )
            )
        elif missing_roles:
            events.append(
                CodePipelineTraceEvent(
                    stage="required-roles",
                    status=CodePipelineStageStatus.DEGRADED,
                    reason="required roles missing: "
                    + ", ".join(role.value for role in missing_roles),
                    input_count=len(candidates),
                    output_count=len(candidates),
                )
            )
        else:
            events.append(
                CodePipelineTraceEvent(
                    stage="required-roles",
                    status=CodePipelineStageStatus.COMPLETE,
                    reason="all required roles are present",
                    input_count=len(candidates),
                    output_count=len(candidates),
                )
            )

        final_candidates = _canonicalize_candidates(candidates)
        result = _build_result(
            base=base_result,
            candidates=final_candidates,
            states=states,
            latency_ms=_elapsed_ms(started, self.latency_clock),
        )
        events.append(
            CodePipelineTraceEvent(
                stage="result",
                status=_result_stage_status(result),
                reason=f"strict CodeSourceResult status={result.status.value}",
                output_count=len(result.candidates),
                component_version=PIPELINE_VERSION,
            )
        )
        trace = CodeSourceFusionTrace(
            pipeline_version=PIPELINE_VERSION,
            registry_version=PROFILE_REGISTRY_VERSION,
            profile_key=definition.key,
            profile_version=definition.version,
            task=definition.task,
            rewrite=resolved.rewrite,
            events=tuple(events),
            required_roles=definition.required_roles,
            missing_roles=missing_roles,
            requested_k=request.limit,
            adaptive_k=adaptive_k,
            refused=refused,
        )
        attestation = _issue_code_source_scope_attestation(
            request=request,
            result=result,
            resolved=resolved,
        )
        return CodeSourceFusionSearchResult(
            result=result,
            trace=trace,
            profile=resolved,
            scope_attestation=attestation,
        )

    def _run_retriever(
        self,
        retriever: CodeProfiledRetriever,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        *,
        stage: str,
        events: list[CodePipelineTraceEvent],
    ) -> Any:
        try:
            response = retriever.search_with_trace(request, profile)
            result = _response_result(response)
        except Exception as error:
            message = _safe_message("retrieval failed", error)
            events.append(
                CodePipelineTraceEvent(
                    stage=stage,
                    status=CodePipelineStageStatus.ERROR,
                    reason=message,
                    component_version=_component_version(retriever),
                )
            )
            return _FailedRetrievalResponse(
                result=_failed_retrieval_result(
                    request=request,
                    profile=profile,
                    message=message,
                )
            )
        events.append(
            CodePipelineTraceEvent(
                stage=stage,
                status=_result_stage_status(result),
                reason=f"retriever returned {result.status.value}",
                output_count=len(result.candidates),
                component_version=_component_version(retriever),
            )
        )
        return response

    def _expand_graph(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        seeds: tuple[CodeRetrievalCandidate, ...],
        *,
        deadline: float | None,
    ) -> tuple[tuple[CodeRetrievalCandidate, ...], _ChannelState, CodePipelineTraceEvent]:
        from .graph_retrieval_v2 import GraphTraversalRequest, GraphTraversalStatus

        grouped: dict[tuple[str, str, str], list[CodeRetrievalCandidate]] = {}
        for seed in seeds:
            key = (seed.repository_id, seed.stable_version, seed.source_generation)
            grouped.setdefault(key, []).append(seed)
        graph_candidates: list[CodeRetrievalCandidate] = []
        statuses: list[Any] = []
        messages: list[str] = []
        for (_repository, version, generation), values in sorted(grouped.items()):
            if deadline is not None and self.clock() >= deadline:
                statuses.append(GraphTraversalStatus.DEADLINE)
                messages.append("graph deadline reached before grouped traversal")
                break
            group_seeds = tuple(values[:10])
            allowed_acl_refs = tuple(sorted({seed.acl_ref for seed in group_seeds}))
            graph_request = GraphTraversalRequest(
                seed_candidates=group_seeds,
                project_id=str(request.scope.project_id),
                task=profile.task,
                directions=profile.directions,
                edge_types=profile.edge_types,
                max_hops=profile.max_hops,
                beam_width=min(64, max(8, profile.budget.graph_candidates)),
                min_confidence=0.0,
                target_ref=version,
                stable_version=version,
                generation_id=generation,
                allowed_acl_refs=allowed_acl_refs,
                node_budget=profile.budget.graph_node_budget,
                edge_budget=profile.budget.graph_edge_budget,
                candidate_budget=profile.budget.graph_candidates,
                deadline=deadline,
            )
            try:
                response = self.graph_retriever.search_with_trace(graph_request)
            except Exception as error:
                statuses.append("error")
                messages.append(_safe_message("typed graph traversal failed", error))
                continue
            statuses.append(response.trace.status)
            if response.trace.unavailable_reason:
                messages.append(response.trace.unavailable_reason)
            graph_candidates.extend(response.code_candidates)

        if any(status == "error" for status in statuses):
            message = "; ".join(messages) or "typed graph traversal failed"
            state = _ChannelState("error", message=message)
            graph_candidates = []
            trace_status = CodePipelineStageStatus.ERROR
        elif any(status is GraphTraversalStatus.DEADLINE for status in statuses):
            message = "; ".join(messages) or "typed graph traversal deadline exceeded"
            state = _ChannelState("timeout", message=message)
            graph_candidates = []
            trace_status = CodePipelineStageStatus.TIMEOUT
        elif any(status is GraphTraversalStatus.UNAVAILABLE for status in statuses):
            message = "; ".join(messages) or "typed graph traversal unavailable"
            state = _ChannelState("unavailable", message=message)
            graph_candidates = []
            trace_status = CodePipelineStageStatus.UNAVAILABLE
        elif graph_candidates:
            graph_candidates = list(_canonicalize_candidates(graph_candidates))
            state = _ChannelState("complete_hits", hit_count=len(graph_candidates))
            trace_status = CodePipelineStageStatus.COMPLETE
            message = "typed graph candidates entered candidate generation"
        else:
            state = _ChannelState("complete_no_match")
            trace_status = CodePipelineStageStatus.COMPLETE_NO_MATCH
            message = "typed graph traversal completed with no candidates"
        event = CodePipelineTraceEvent(
            stage="graph",
            status=trace_status,
            reason=message,
            input_count=len(seeds),
            output_count=len(graph_candidates),
            component_version=_component_version(self.graph_retriever),
        )
        return tuple(graph_candidates), state, event

    def _run_hook(
        self,
        *,
        channel: CodeRetrievalChannel,
        hook: Any | None,
        request: EvidenceSearchRequest,
        contract: CodeQueryProfile,
        enabled: bool,
        limit: int,
        candidates: list[CodeRetrievalCandidate],
        states: dict[CodeRetrievalChannel, _ChannelState],
        events: list[CodePipelineTraceEvent],
        deadline: float | None,
    ) -> list[CodeRetrievalCandidate]:
        stage = channel.value
        if not enabled:
            states[channel] = _ChannelState("disabled")
            events.append(
                CodePipelineTraceEvent(
                    stage=stage,
                    status=CodePipelineStageStatus.SKIPPED,
                    reason=f"{stage} channel disabled by the resolved profile",
                    input_count=len(candidates),
                )
            )
            return candidates
        if hook is None:
            message = f"{stage} channel enabled but no optional hook was injected"
            states[channel] = _ChannelState("unavailable", message=message)
            events.append(
                CodePipelineTraceEvent(
                    stage=stage,
                    status=CodePipelineStageStatus.UNAVAILABLE,
                    reason=message,
                    input_count=len(candidates),
                )
            )
            return candidates
        if deadline is not None and self.clock() >= deadline:
            message = f"{stage} deadline reached before hook invocation"
            states[channel] = _ChannelState("timeout", message=message)
            events.append(
                CodePipelineTraceEvent(
                    stage=stage,
                    status=CodePipelineStageStatus.TIMEOUT,
                    reason=message,
                    input_count=len(candidates),
                    component_version=_component_version(hook),
                )
            )
            return candidates
        try:
            response = hook.expand(
                request=request,
                profile=contract,
                seeds=tuple(candidates[:10]),
                limit=limit,
                deadline=deadline,
            )
            if not isinstance(response, CodeOptionalHookResult):
                raise TypeError("optional hook must return CodeOptionalHookResult")
            if response.channel is not channel:
                raise ValueError("optional hook returned the wrong channel")
        except Exception as error:
            message = _safe_message(f"{stage} hook failed", error)
            states[channel] = _ChannelState("error", message=message)
            events.append(
                CodePipelineTraceEvent(
                    stage=stage,
                    status=CodePipelineStageStatus.ERROR,
                    reason=message,
                    input_count=len(candidates),
                    component_version=_component_version(hook),
                )
            )
            return candidates

        status_map = {
            CodeOptionalHookStatus.COMPLETE: ("complete_hits", CodePipelineStageStatus.COMPLETE),
            CodeOptionalHookStatus.COMPLETE_NO_MATCH: (
                "complete_no_match",
                CodePipelineStageStatus.COMPLETE_NO_MATCH,
            ),
            CodeOptionalHookStatus.UNAVAILABLE: (
                "unavailable",
                CodePipelineStageStatus.UNAVAILABLE,
            ),
            CodeOptionalHookStatus.TIMEOUT: ("timeout", CodePipelineStageStatus.TIMEOUT),
            CodeOptionalHookStatus.ERROR: ("error", CodePipelineStageStatus.ERROR),
        }
        state_kind, trace_status = status_map[response.status]
        states[channel] = _ChannelState(
            state_kind,
            hit_count=len(response.candidates),
            message=response.reason,
        )
        merged = list(
            _merge_candidate_pools(
                candidates,
                response.candidates,
                limit=contract.budget.total_candidates,
            )
        )
        events.append(
            CodePipelineTraceEvent(
                stage=stage,
                status=trace_status,
                reason=response.reason or f"{stage} hook completed",
                input_count=len(candidates),
                output_count=len(response.candidates),
                component_version=response.hook_version,
            )
        )
        return merged

    def _calibrate(
        self,
        candidates: Sequence[CodeRetrievalCandidate],
        *,
        definition: CodeQueryProfileV2,
        index_version: str,
    ) -> tuple[list[CodeRetrievalCandidate], CodePipelineTraceEvent]:
        if not candidates:
            return [], CodePipelineTraceEvent(
                stage="calibration",
                status=CodePipelineStageStatus.SKIPPED,
                reason="calibration skipped because the candidate pool is empty",
            )
        if definition.calibration_policy is CodeCalibrationPolicy.DISABLED:
            values = [
                _candidate_with_calibration(
                    candidate,
                    CodeUncalibratedScore(
                        status=CodeCalibrationStatus.DISABLED,
                        reason="calibration disabled by the resolved query profile",
                    ),
                )
                for candidate in candidates
            ]
            return values, CodePipelineTraceEvent(
                stage="calibration",
                status=CodePipelineStageStatus.SKIPPED,
                reason="calibration disabled by the resolved query profile",
                input_count=len(values),
                output_count=len(values),
            )
        if self.calibration_artifact is None:
            values = [
                _candidate_with_calibration(
                    candidate,
                    CodeUncalibratedScore(
                        status=CodeCalibrationStatus.UNAVAILABLE,
                        reason="no calibration artifact was provided",
                    ),
                )
                for candidate in candidates
            ]
            return values, CodePipelineTraceEvent(
                stage="calibration",
                status=CodePipelineStageStatus.UNAVAILABLE,
                reason="no calibration artifact was provided",
                input_count=len(values),
                output_count=len(values),
            )

        calibrate = self.calibration_function
        if calibrate is None:
            from .rerank_v2 import apply_calibration

            calibrate = apply_calibration
        reranker_version = _component_version(self.reranker) or "unavailable"
        model_version = str(getattr(self.reranker, "model_version", "unavailable"))
        calibrated: list[CodeRetrievalCandidate] = []
        try:
            for rank, candidate in enumerate(candidates):
                application = calibrate(
                    self.calibration_artifact,
                    score=candidate.source_fused_score,
                    rank=rank,
                    profile_version=definition.version,
                    model_version=model_version,
                    index_version=index_version,
                    reranker_version=reranker_version,
                )
                if (
                    getattr(application, "status", None) == "available"
                    and getattr(application, "calibrated_score", None) is not None
                ):
                    relevance = CodeCalibratedScore(
                        score=float(application.calibrated_score),
                        calibration_version=str(application.artifact_hash),
                    )
                else:
                    relevance = CodeUncalibratedScore(
                        status=CodeCalibrationStatus.UNAVAILABLE,
                        reason=str(getattr(application, "reason", "calibration unavailable")),
                    )
                calibrated.append(_candidate_with_calibration(candidate, relevance))
        except Exception as error:
            message = _safe_message("calibration unavailable", error)
            calibrated = [
                _candidate_with_calibration(
                    candidate,
                    CodeUncalibratedScore(
                        status=CodeCalibrationStatus.UNAVAILABLE,
                        reason=message,
                    ),
                )
                for candidate in candidates
            ]
            return calibrated, CodePipelineTraceEvent(
                stage="calibration",
                status=CodePipelineStageStatus.UNAVAILABLE,
                reason=message,
                input_count=len(candidates),
                output_count=len(calibrated),
            )
        available = all(
            candidate.calibrated_relevance.status is CodeCalibrationStatus.CALIBRATED
            for candidate in calibrated
        )
        return calibrated, CodePipelineTraceEvent(
            stage="calibration",
            status=(
                CodePipelineStageStatus.COMPLETE
                if available
                else CodePipelineStageStatus.UNAVAILABLE
            ),
            reason=(
                "calibration artifact applied"
                if available
                else "calibration artifact reported unavailable"
            ),
            input_count=len(candidates),
            output_count=len(calibrated),
            component_version=str(
                getattr(self.calibration_artifact, "artifact_version", "unknown")
            ),
        )


def _normalize_query(query: str) -> str:
    if not isinstance(query, str):
        raise TypeError("query must be a str")
    value = unicodedata.normalize("NFC", query)
    if _CONTROL_RE.search(value):
        raise ValueError("query contains unsupported control characters")
    value = " ".join(value.split())
    if not value:
        raise ValueError("query must contain a non-whitespace character")
    return value


def _normalize_target_ref(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or _CONTROL_RE.search(normalized):
        return "current"
    return " ".join(normalized.split())


def _safe_relative_path(value: str) -> str | None:
    normalized = unicodedata.normalize("NFC", value).replace("\\", "/")
    normalized = normalized.split("#", 1)[0]
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = tuple(part for part in normalized.split("/") if part not in {"", "."})
    if (
        not parts
        or normalized.startswith("/")
        or any(part == ".." for part in parts)
        or _CONTROL_RE.search(normalized)
    ):
        return None
    return "/".join(parts)


def _is_simple_exact_query(query: str) -> bool:
    stripped = query.strip()
    return bool(
        _SIMPLE_IDENTIFIER_RE.fullmatch(stripped)
        or _LOCATION_RE.fullmatch(stripped)
        or stripped.startswith(("code://", "code-unit://"))
    )


def _response_result(response: Any) -> CodeSourceResult:
    result = (
        response if isinstance(response, CodeSourceResult) else getattr(response, "result", None)
    )
    if not isinstance(result, CodeSourceResult):
        raise TypeError("retriever response must expose a strict CodeSourceResult")
    return result


def _failed_retrieval_result(
    *,
    request: EvidenceSearchRequest,
    profile: CodeQueryProfile,
    message: str,
) -> CodeSourceResult:
    enabled = {
        CodeRetrievalChannel.EXACT: profile.budget.exact_candidates,
        CodeRetrievalChannel.SPARSE: profile.budget.sparse_candidates,
    }
    outcomes: list[CodeChannelOutcome] = []
    errors: list[CodeChannelFailure] = []
    for channel in CodeRetrievalChannel:
        if enabled.get(channel, 0):
            outcomes.append(CodeChannelError(channel=channel, error=message))
            errors.append(CodeChannelFailure(channel=channel, kind="error", message=message))
        else:
            outcomes.append(
                CodeChannelDisabled(
                    channel=channel,
                    reason="channel did not run after seed retrieval failed",
                )
            )
    return CodeSourceResult(
        query_id=f"query:retrieval-error:{profile.task.value}",
        status=CodeSourceStatus.UNAVAILABLE,
        channel_outcomes=tuple(outcomes),
        candidates=(),
        context_blocks=(),
        index_version="unavailable",
        watermark=profile.target_ref or request.scope.commit or request.scope.branch or "current",
        latency_ms=0.0,
        errors=tuple(errors),
    )


def _component_version(component: Any | None) -> str:
    if component is None:
        return ""
    for name in ("pipeline_version", "retriever_version", "version", "hook_version"):
        value = getattr(component, name, "")
        if value:
            return str(value)
    return type(component).__name__


def _safe_message(prefix: str, error: Exception) -> str:
    detail = " ".join(str(error).split())
    message = f"{prefix}: {type(error).__name__}"
    if detail:
        message += f": {detail}"
    return message[:4_000]


def _result_stage_status(result: CodeSourceResult) -> CodePipelineStageStatus:
    if result.status is CodeSourceStatus.COMPLETE:
        return (
            CodePipelineStageStatus.COMPLETE
            if result.candidates
            else CodePipelineStageStatus.COMPLETE_NO_MATCH
        )
    if result.status is CodeSourceStatus.PARTIAL:
        return CodePipelineStageStatus.DEGRADED
    if result.status is CodeSourceStatus.TIMEOUT:
        return CodePipelineStageStatus.TIMEOUT
    return CodePipelineStageStatus.UNAVAILABLE


def _states_from_result(result: CodeSourceResult) -> dict[CodeRetrievalChannel, _ChannelState]:
    states: dict[CodeRetrievalChannel, _ChannelState] = {}
    for outcome in result.channel_outcomes:
        if isinstance(outcome, CodeChannelDisabled):
            states[outcome.channel] = _ChannelState("disabled", message=outcome.reason)
        elif isinstance(outcome, CodeChannelCompleteNoMatch):
            states[outcome.channel] = _ChannelState("complete_no_match")
        elif isinstance(outcome, CodeChannelCompletePruned):
            states[outcome.channel] = _ChannelState(
                "complete_pruned",
                hit_count=outcome.hit_count,
            )
        elif isinstance(outcome, CodeChannelCompleteWithHits):
            states[outcome.channel] = _ChannelState(
                "complete_hits",
                hit_count=outcome.hit_count,
            )
        elif isinstance(outcome, CodeChannelTimeout):
            states[outcome.channel] = _ChannelState("timeout", message=outcome.error)
        elif isinstance(outcome, CodeChannelUnavailable):
            states[outcome.channel] = _ChannelState("unavailable", message=outcome.error)
        elif isinstance(outcome, CodeChannelError):
            states[outcome.channel] = _ChannelState("error", message=outcome.error)
        else:
            raise TypeError(f"unsupported Code channel outcome: {type(outcome).__name__}")
    return states


def _channel_event(
    channel: CodeRetrievalChannel,
    state: _ChannelState,
) -> CodePipelineTraceEvent:
    status_by_kind = {
        "disabled": CodePipelineStageStatus.SKIPPED,
        "complete_no_match": CodePipelineStageStatus.COMPLETE_NO_MATCH,
        "complete_pruned": CodePipelineStageStatus.COMPLETE,
        "complete_hits": CodePipelineStageStatus.COMPLETE,
        "unavailable": CodePipelineStageStatus.UNAVAILABLE,
        "timeout": CodePipelineStageStatus.TIMEOUT,
        "error": CodePipelineStageStatus.ERROR,
    }
    return CodePipelineTraceEvent(
        stage=channel.value,
        status=status_by_kind[state.kind],
        reason=state.message or f"{channel.value} channel {state.kind.replace('_', ' ')}",
        output_count=state.hit_count,
    )


def _fuse_graph(
    seeds: Sequence[CodeRetrievalCandidate],
    graph: Sequence[CodeRetrievalCandidate],
    *,
    limit: int,
) -> tuple[CodeRetrievalCandidate, ...]:
    from .graph_retrieval_v2 import fuse_graph_candidates

    return fuse_graph_candidates(seeds, graph, limit=limit)


def _candidate_priority(candidate: CodeRetrievalCandidate) -> tuple[Any, ...]:
    channels = {rank.channel for rank in candidate.raw_channel_ranks}
    alignment = {
        CodeVersionAlignment.EXACT: 0,
        CodeVersionAlignment.COMPATIBLE: 1,
        CodeVersionAlignment.HISTORICAL: 2,
        CodeVersionAlignment.UNKNOWN: 3,
        CodeVersionAlignment.MISMATCH: 4,
    }[candidate.version_alignment]
    return (
        CodeRetrievalChannel.EXACT not in channels,
        alignment,
        candidate.within_source_rank,
        candidate.repository_id,
        candidate.locator,
        candidate.source_generation,
    )


def _merge_candidate_pools(
    primary: Sequence[CodeRetrievalCandidate],
    additions: Sequence[CodeRetrievalCandidate],
    *,
    limit: int,
) -> tuple[CodeRetrievalCandidate, ...]:
    merged: dict[tuple[str, str], CodeRetrievalCandidate] = {}
    for candidate in (*primary, *additions):
        key = (candidate.entity_id, candidate.retrieval_unit_id)
        current = merged.get(key)
        if current is None:
            merged[key] = candidate
        elif (
            current.repository_id,
            current.stable_version,
            current.source_generation,
            current.acl_ref,
        ) == (
            candidate.repository_id,
            candidate.stable_version,
            candidate.source_generation,
            candidate.acl_ref,
        ):
            merged[key] = _merge_candidate_signals(current, candidate)
        elif _candidate_priority(candidate) < _candidate_priority(current):
            merged[key] = candidate
    ordered = sorted(merged.values(), key=_candidate_priority)
    return tuple(ordered[:limit])


def _merge_candidate_signals(
    left: CodeRetrievalCandidate,
    right: CodeRetrievalCandidate,
) -> CodeRetrievalCandidate:
    preferred, other = (
        (left, right) if _candidate_priority(left) <= _candidate_priority(right) else (right, left)
    )
    scores = {score.channel: score for score in preferred.raw_channel_scores}
    ranks = {rank.channel: rank for rank in preferred.raw_channel_ranks}
    for score in other.raw_channel_scores:
        current = scores.get(score.channel)
        if current is None or score.score > current.score:
            scores[score.channel] = score
    for rank in other.raw_channel_ranks:
        current = ranks.get(rank.channel)
        if current is None or rank.rank < current.rank:
            ranks[rank.channel] = rank
    payload = preferred.model_dump(mode="python", round_trip=True)
    hook_channels = {
        score.channel
        for score in other.raw_channel_scores
        if score.channel
        in {
            CodeRetrievalChannel.HISTORY,
            CodeRetrievalChannel.TEST,
        }
    }
    payload.update(
        raw_channel_scores=tuple(scores.values()),
        raw_channel_ranks=tuple(ranks.values()),
        source_fused_score=max(left.source_fused_score, right.source_fused_score),
    )
    if hook_channels and other.role in {
        CodeCandidateRole.HISTORY,
        CodeCandidateRole.TEST,
    }:
        payload.update(
            role=other.role,
            fact_status=other.fact_status,
            derivation=other.derivation,
        )
    return CodeRetrievalCandidate.model_validate(payload)


def _govern_and_deduplicate(
    candidates: Sequence[CodeRetrievalCandidate],
    request: EvidenceSearchRequest,
    *,
    allow_history: bool,
) -> tuple[tuple[CodeRetrievalCandidate, ...], int]:
    allowed_acl_refs = set(request.scope.allowed_acl_refs)
    allowed_repositories = set(request.scope.repository_ids)
    accepted: list[CodeRetrievalCandidate] = []
    rejected = 0
    for candidate in candidates:
        if allowed_acl_refs and candidate.acl_ref not in allowed_acl_refs:
            rejected += 1
            continue
        if request.scope.enforce_acl and not allowed_acl_refs:
            rejected += 1
            continue
        if allowed_repositories and candidate.repository_id not in allowed_repositories:
            rejected += 1
            continue
        if request.scope.commit and candidate.stable_version != request.scope.commit:
            rejected += 1
            continue
        if candidate.version_alignment is CodeVersionAlignment.MISMATCH and not (
            allow_history
            and candidate.role is CodeCandidateRole.HISTORY
            and CodeRetrievalChannel.HISTORY
            in {rank.channel for rank in candidate.raw_channel_ranks}
        ):
            rejected += 1
            continue
        accepted.append(candidate)
    deduplicated = _merge_candidate_pools(accepted, (), limit=50)
    rejected += len(accepted) - len(deduplicated)
    return deduplicated, rejected


def _rerank_response(
    hybrid_response: Any,
    base_result: CodeSourceResult,
    candidates: Sequence[CodeRetrievalCandidate],
    states: Mapping[CodeRetrievalChannel, _ChannelState],
) -> Any:
    from .dense_v2 import CodeHybridSearchResult

    if not isinstance(hybrid_response, CodeHybridSearchResult):
        raise TypeError("existing deterministic reranker requires CodeHybridSearchResult")
    result = _build_result(
        base=base_result,
        candidates=_canonicalize_candidates(candidates),
        states=states,
        latency_ms=base_result.latency_ms,
    )
    return replace(hybrid_response, result=result)


def _candidate_with_calibration(
    candidate: CodeRetrievalCandidate,
    relevance: Any,
) -> CodeRetrievalCandidate:
    payload = candidate.model_dump(mode="python", round_trip=True)
    payload["calibrated_relevance"] = relevance
    return CodeRetrievalCandidate.model_validate(payload)


def _adaptive_select(
    candidates: Sequence[CodeRetrievalCandidate],
    limit: int,
    *,
    required_roles: Sequence[CodeCandidateRole],
) -> tuple[CodeRetrievalCandidate, ...]:
    if limit <= 0:
        return ()
    ordered = list(candidates)
    selected = ordered[:limit]
    for role in required_roles:
        if any(candidate.role is role for candidate in selected):
            continue
        replacement = next(
            (candidate for candidate in ordered[limit:] if candidate.role is role),
            None,
        )
        if replacement is None:
            continue
        protected_roles = set(required_roles)
        role_counts = {
            selected_role: sum(candidate.role is selected_role for candidate in selected)
            for selected_role in protected_roles
        }
        replace_index = next(
            (
                index
                for index in range(len(selected) - 1, -1, -1)
                if (
                    selected[index].role not in protected_roles
                    or role_counts[selected[index].role] > 1
                )
                and CodeRetrievalChannel.EXACT
                not in {rank.channel for rank in selected[index].raw_channel_ranks}
            ),
            None,
        )
        if replace_index is not None:
            selected[replace_index] = replacement
    selected.sort(key=lambda candidate: ordered.index(candidate))
    return tuple(selected)


def _missing_role_channel(role: CodeCandidateRole) -> CodeRetrievalChannel:
    if role is CodeCandidateRole.HISTORY:
        return CodeRetrievalChannel.HISTORY
    if role is CodeCandidateRole.TEST:
        return CodeRetrievalChannel.TEST
    if role is CodeCandidateRole.DEPENDENCY:
        return CodeRetrievalChannel.GRAPH
    return CodeRetrievalChannel.EXACT


def _canonicalize_candidates(
    candidates: Sequence[CodeRetrievalCandidate],
) -> tuple[CodeRetrievalCandidate, ...]:
    ordered = list(candidates)
    ranks_by_channel: dict[CodeRetrievalChannel, set[int]] = {
        channel: set() for channel in CodeRetrievalChannel
    }
    next_rank = {channel: 0 for channel in CodeRetrievalChannel}
    result: list[CodeRetrievalCandidate] = []
    for source_rank, candidate in enumerate(ordered):
        ranks: list[CodeChannelRank] = []
        for raw_rank in candidate.raw_channel_ranks:
            channel = raw_rank.channel
            rank = raw_rank.rank
            if rank in ranks_by_channel[channel]:
                while next_rank[channel] in ranks_by_channel[channel]:
                    next_rank[channel] += 1
                rank = next_rank[channel]
            ranks_by_channel[channel].add(rank)
            next_rank[channel] = max(next_rank[channel], rank + 1)
            ranks.append(CodeChannelRank(channel=channel, rank=rank))
        payload = candidate.model_dump(mode="python", round_trip=True)
        payload.update(within_source_rank=source_rank, raw_channel_ranks=tuple(ranks))
        result.append(CodeRetrievalCandidate.model_validate(payload))
    return tuple(result)


def _build_result(
    *,
    base: CodeSourceResult,
    candidates: Sequence[CodeRetrievalCandidate],
    states: Mapping[CodeRetrievalChannel, _ChannelState],
    latency_ms: float,
) -> CodeSourceResult:
    observed: dict[CodeRetrievalChannel, list[int]] = {
        channel: [] for channel in CodeRetrievalChannel
    }
    for candidate in candidates:
        for rank in candidate.raw_channel_ranks:
            observed[rank.channel].append(rank.rank)
    outcomes: list[CodeChannelOutcome] = []
    errors: list[CodeChannelFailure] = []
    completed = False
    timeouts = False
    failures = False
    for channel in CodeRetrievalChannel:
        state = states.get(channel, _ChannelState("disabled"))
        channel_ranks = observed[channel]
        if state.kind == "disabled":
            outcome: CodeChannelOutcome = CodeChannelDisabled(
                channel=channel,
                reason="channel disabled by the resolved Code query profile",
            )
        elif state.kind == "unavailable":
            message = state.message or f"{channel.value} channel unavailable"
            outcome = CodeChannelUnavailable(channel=channel, error=message)
            errors.append(CodeChannelFailure(channel=channel, kind="unavailable", message=message))
            failures = True
        elif state.kind == "timeout":
            message = state.message or f"{channel.value} channel timed out"
            outcome = CodeChannelTimeout(channel=channel, error=message)
            errors.append(CodeChannelFailure(channel=channel, kind="timeout", message=message))
            failures = True
            timeouts = True
        elif state.kind == "error":
            message = state.message or f"{channel.value} channel failed"
            outcome = CodeChannelError(channel=channel, error=message)
            errors.append(CodeChannelFailure(channel=channel, kind="error", message=message))
            failures = True
        else:
            completed = True
            hit_count = max(state.hit_count, max(channel_ranks, default=-1) + 1)
            if channel_ranks:
                outcome = CodeChannelCompleteWithHits(channel=channel, hit_count=hit_count)
            elif hit_count:
                outcome = CodeChannelCompletePruned(channel=channel, hit_count=hit_count)
            else:
                outcome = CodeChannelCompleteNoMatch(channel=channel)
        outcomes.append(outcome)
    if completed and failures:
        status = CodeSourceStatus.PARTIAL
    elif failures and timeouts:
        status = CodeSourceStatus.TIMEOUT
    elif failures:
        status = CodeSourceStatus.UNAVAILABLE
    else:
        status = CodeSourceStatus.COMPLETE
    if status in {CodeSourceStatus.TIMEOUT, CodeSourceStatus.UNAVAILABLE}:
        candidates = ()
    candidates_by_key = {
        (candidate.entity_id, candidate.retrieval_unit_id): candidate for candidate in candidates
    }
    context_blocks = []
    for block in base.context_blocks:
        candidate = candidates_by_key.get((block.entity_id, block.retrieval_unit_id))
        if candidate is None:
            continue
        candidate_provenance = (
            candidate.repository_id,
            candidate.role,
            candidate.stable_version,
            candidate.source_generation,
            candidate.relation_path,
            candidate.locator,
            candidate.acl_ref,
        )
        block_provenance = (
            block.repository_id,
            block.role,
            block.stable_version,
            block.source_generation,
            block.relation_path,
            block.locator,
            block.acl_ref,
        )
        if candidate_provenance == block_provenance:
            context_blocks.append(block)
    payload = base.model_dump(mode="python", round_trip=True)
    payload.update(
        status=status,
        channel_outcomes=tuple(outcomes),
        candidates=tuple(candidates),
        context_blocks=tuple(context_blocks),
        latency_ms=latency_ms,
        errors=tuple(errors),
        fallback={"status": "not_used"},
    )
    return CodeSourceResult.model_validate(payload)


def _elapsed_ms(started: float, clock: Callable[[], float]) -> float:
    return max(0.0, float((clock() - started) * 1_000.0))


__all__ = [
    "CODE_QUERY_PROFILES",
    "CODE_QUERY_PROFILE_REGISTRY",
    "CODE_QUERY_PROFILE_REGISTRY_BY_KEY",
    "PIPELINE_VERSION",
    "PROFILE_REGISTRY_VERSION",
    "REWRITE_VERSION",
    "SOURCE_SCOPE_ATTESTATION_VERSION",
    "CodeAdaptiveK",
    "CodeCalibrationPolicy",
    "CodeCandidateReranker",
    "CodeContextTemplate",
    "CodeHistoryExpansionHook",
    "CodeMissingRolePolicy",
    "CodeOptionalHookResult",
    "CodeOptionalHookStatus",
    "CodePipelineStageStatus",
    "CodePipelineTraceEvent",
    "CodeProfiledRetriever",
    "CodeQueryProfileV2",
    "CodeQueryRewrite",
    "CodeRewritePolicy",
    "CodeSourceFusionPipeline",
    "CodeSourceFusionSearchResult",
    "CodeSourceFusionTrace",
    "CodeSourceBlockPublicationIdentity",
    "CodeSourceScopeAttestation",
    "CodeSourceScopeAttestationError",
    "CodeTestValidationExpansionHook",
    "ResolvedCodeQueryProfile",
    "get_code_query_profile",
    "infer_code_task",
    "resolve_code_query_profile",
    "rewrite_code_query",
]
