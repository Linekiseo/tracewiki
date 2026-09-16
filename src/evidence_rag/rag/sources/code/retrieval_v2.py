"""Published Code V2 exact and fielded-sparse retrieval.

The retriever consumes the existing :class:`CodeV2StoreMixin` APIs.  It does not
build a second lexical index and it never falls back to the legacy V1 tables.
``search`` implements the stable Code Source protocol; ``search_with_trace``
adds a frozen, non-contract diagnostic envelope for channel explanations.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Protocol

from ....models import EvidenceSearchRequest
from .contracts import (
    CodeCandidateRole,
    CodeChannelCompleteNoMatch,
    CodeChannelCompletePruned,
    CodeChannelCompleteWithHits,
    CodeChannelDisabled,
    CodeChannelError,
    CodeChannelFailure,
    CodeChannelOutcome,
    CodeChannelRank,
    CodeChannelScore,
    CodeChannelUnavailable,
    CodeDerivation,
    CodeFactStatus,
    CodeQueryProfile,
    CodeRelationNode,
    CodeRelationPath,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeReviewStatus,
    CodeSourceResult,
    CodeSourceStatus,
    CodeTask,
    CodeUncalibratedScore,
    CodeVersionAlignment,
)

RETRIEVER_VERSION = "code-exact-sparse-v1"
FUSION_POLICY = "deterministic-weighted-rrf-v1"
DIVERSIFICATION_POLICY = "entity-aware-canonical-representative-v1"
DEFAULT_RRF_K = 60
DEFAULT_EXACT_WEIGHT = 2.0
DEFAULT_SPARSE_WEIGHT = 1.0
_NOT_BUILT = frozenset({"", "not-built", "not_built", "disabled", "unavailable"})

_URI_RE = re.compile(r"(?:code|code-unit)://[^\s\"'<>]+")
_FULL_SHA_RE = re.compile(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})(?![0-9a-fA-F])")
_SHORT_SHA_RE = re.compile(
    r"(?:(?:commit|sha)\s*[:=@]\s*)?([0-9a-fA-F]{7,39})(?![0-9a-fA-F])",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(?<![\w.-])((?:\.{0,2}/)?(?:[\w@+.-]+/)+[\w@+.-]+\.[A-Za-z0-9]{1,12})")
_BASENAME_RE = re.compile(r"(?<![\w/.-])([\w@+-]+\.[A-Za-z0-9]{1,12})(?![\w/.-])")
_QUALIFIED_RE = re.compile(r"(?<![\w])([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_SEARCH_TOKEN_RE = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*|\d+(?:\.\d+)?|[\u3400-\u9fff]+|"
    r"===|!==|==|!=|<=|>=|=>|->|::|\+\+|--|&&|\|\||\?\?|[+\-*/%<>]=?",
    re.UNICODE,
)
_ERROR_CODE_RE = re.compile(
    r"(?<![\w])("
    r"(?:ERR|ERROR|E|TS|CS|ORA|HTTP|SQL)[-_]?[A-Z0-9]{2,}"
    r"|[A-Z][A-Z0-9]+(?:[-_][A-Z0-9]+)+"
    r")(?![\w])",
    re.IGNORECASE,
)
_STACK_PYTHON_RE = re.compile(
    r'File\s+["\']([^"\']+)["\']\s*,\s*line\s+(\d+)(?:\s*,\s*in\s+([A-Za-z_$][\w$]*))?',
    re.IGNORECASE,
)
_STACK_GENERIC_RE = re.compile(
    r"(?:at\s+)?(?:(?P<symbol>[A-Za-z_$][\w$.]*)\s+\()?"
    r"(?P<path>(?:[\w@+.-]+/)+[\w@+.-]+\.[A-Za-z0-9]{1,12})"
    r":(?P<line>\d+)(?::\d+)?\)?"
)
_TEST_SELECTOR_RE = re.compile(
    r"((?:[\w@+.-]+/)*[\w@+.-]+\.[A-Za-z0-9]{1,12})"
    r"::((?:[A-Za-z_$][\w$]*::)*[A-Za-z_$][\w$]*)"
)
_REF_RE = re.compile(r"\b(branch|tag)\s*[:=]\s*([^\s,;]+)", re.IGNORECASE)
_REFS_RE = re.compile(r"\brefs/(heads|tags)/([^\s,;]+)")
_WORD_BOUNDARY_TEMPLATE = r"(?<![A-Za-z0-9_$]){}(?![A-Za-z0-9_$])"

_OPERATOR_ALIASES = {
    "===": ("strict_eq", "equality"),
    "!==": ("strict_ne", "inequality"),
    "==": ("eq", "__eq__", "equality"),
    "!=": ("ne", "__ne__", "inequality"),
    "<=": ("le", "__le__"),
    ">=": ("ge", "__ge__"),
    "<": ("lt", "__lt__"),
    ">": ("gt", "__gt__"),
    "=>": ("arrow", "lambda"),
    "->": ("arrow", "returns"),
    "::": ("scope", "selector"),
    "++": ("increment",),
    "--": ("decrement",),
    "&&": ("and",),
    "||": ("or",),
    "??": ("nullish", "coalesce"),
    "+": ("plus", "add"),
    "-": ("minus", "subtract"),
    "*": ("multiply",),
    "/": ("divide",),
    "%": ("modulo",),
}
_STOP_IDENTIFIERS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "branch",
        "by",
        "code",
        "error",
        "file",
        "find",
        "for",
        "from",
        "function",
        "in",
        "is",
        "line",
        "method",
        "of",
        "on",
        "or",
        "show",
        "symbol",
        "tag",
        "test",
        "the",
        "to",
        "where",
        "which",
        "with",
    }
)
_FIELD_NAMES = ("qualified_name", "signature", "path", "identifiers", "doc", "body")
_IDENTIFYING_PRIORITIES = {
    "uri": 0,
    "qualified_name": 2,
    "exact_path": 3,
    "exact_symbol": 4,
    "error_code": 4,
    "basename": 5,
}


class CodeV2RetrievalStore(Protocol):
    """Narrow store surface consumed by the C2-05 retriever."""

    def active_code_generations(
        self,
        *,
        project_id: str | None = None,
        repository_ids: Sequence[str] | None = None,
    ) -> dict[str, str]: ...

    def get_code_index_publication(
        self,
        generation_id: str,
        *,
        repository_id: str | None = None,
        active_only: bool = False,
    ) -> dict[str, Any] | None: ...

    def lookup_code_units_exact(
        self,
        query: str | None = None,
        **scope: Any,
    ) -> list[dict[str, Any]]: ...

    def fielded_code_unit_search(
        self,
        query: str | Mapping[str, str],
        **scope: Any,
    ) -> list[dict[str, Any]]: ...


class CodeLocatorKind(StrEnum):
    URI = "uri"
    FULL_SHA = "full_sha"
    SHORT_SHA = "short_sha"
    QUALIFIED_NAME = "qualified_name"
    EXACT_PATH = "exact_path"
    EXACT_SYMBOL = "exact_symbol"
    BASENAME = "basename"
    STACK_FRAME = "stack_frame"
    TEST_SELECTOR = "test_selector"
    ERROR_CODE = "error_code"
    BRANCH = "branch"
    TAG = "tag"


@dataclass(frozen=True, slots=True)
class CodeLocatorTrace:
    kind: CodeLocatorKind
    value: str
    priority: int
    available: bool
    explanation: str


@dataclass(frozen=True, slots=True)
class CodeChannelHitTrace:
    retrieval_unit_id: str
    generation_id: str
    channel: CodeRetrievalChannel
    raw_rank: int
    raw_score: float
    matched_fields: tuple[str, ...]
    locator: str
    explanation: str
    entity_id: str = ""
    original_fused_rank: int | None = None
    diversified_rank: int | None = None
    representative: bool = False
    diversification_reason: str = ""


@dataclass(frozen=True, slots=True)
class CodeExactSparseTrace:
    query_id: str
    normalized_tokens: tuple[str, ...]
    locators: tuple[CodeLocatorTrace, ...]
    generation_scopes: tuple[str, ...]
    channel_hits: tuple[CodeChannelHitTrace, ...]
    fusion_policy: str
    unavailable_reason: str = ""
    diversification_policy: str = DIVERSIFICATION_POLICY


@dataclass(frozen=True, slots=True)
class CodeExactSparseSearchResult:
    result: CodeSourceResult
    trace: CodeExactSparseTrace


@dataclass(frozen=True, slots=True)
class _Locator:
    kind: CodeLocatorKind
    value: str
    priority: int
    path: str = ""
    repository_id: str = ""
    ref: str = ""
    symbol: str = ""
    start_line: int | None = None
    end_line: int | None = None
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class _GenerationScope:
    project_id: str
    repository_id: str
    generation_id: str
    commit_sha: str
    active_only: bool
    publication: Mapping[str, Any]

    def store_scope(self, allowed_acl_refs: Sequence[str] | None) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "repository_ids": [self.repository_id],
            "generation_id": self.generation_id,
            "allowed_acl_refs": allowed_acl_refs,
            "active_only": self.active_only,
        }


@dataclass(frozen=True, slots=True)
class _RankedHit:
    row: Mapping[str, Any]
    raw_score: float
    raw_rank: int
    matched_fields: tuple[str, ...]
    locator_kind: CodeLocatorKind | None
    explanation: str

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.row["generation_id"]), str(self.row["id"]))


@dataclass(frozen=True, slots=True)
class _DiversificationDecision:
    original_fused_rank: int
    diversified_rank: int | None
    representative: bool
    reason: str


@dataclass(slots=True)
class _ExactMatch:
    row: Mapping[str, Any]
    priority: int
    matched_fields: set[str]
    locator_kind: CodeLocatorKind
    locator_value: str
    explanation: str


@dataclass(frozen=True, slots=True)
class _ScopeResolution:
    scopes: tuple[_GenerationScope, ...]
    unavailable_reason: str = ""


def normalize_code_sparse_query(query: str) -> tuple[str, ...]:
    """Return bounded, deterministic query tokens for the existing Code FTS index."""

    if type(query) is not str:
        raise TypeError("query must be a string")
    normalized_query = unicodedata.normalize("NFC", query)
    result: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        token = unicodedata.normalize("NFC", value).strip()
        if not token or token in seen or len(token) > 256 or len(result) >= 96:
            return
        seen.add(token)
        result.append(token)

    for raw in _SEARCH_TOKEN_RE.findall(normalized_query):
        add(raw)
        folded = raw.casefold()
        add(folded)
        if raw in _OPERATOR_ALIASES:
            for alias in _OPERATOR_ALIASES[raw]:
                add(alias)
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", raw):
            if len(raw) > 2:
                for offset in range(len(raw) - 1):
                    add(raw[offset : offset + 2])
            continue
        for dotted in re.split(r"[./\\:]+", raw):
            for snake in re.split(r"_+", dotted):
                for part in re.split(
                    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])",
                    snake,
                ):
                    if part:
                        add(part)
                        add(part.casefold())
        error_match = _ERROR_CODE_RE.fullmatch(raw)
        if error_match:
            for component in re.findall(r"[A-Za-z]+|\d+", raw):
                add(component)
                add(component.casefold())
    return tuple(result)


class CodeExactSparseRetriever:
    """C2-05 exact + sparse retriever over a published Code V2 generation."""

    def __init__(
        self,
        store: CodeV2RetrievalStore,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        exact_weight: float = DEFAULT_EXACT_WEIGHT,
        sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
    ) -> None:
        if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k < 1:
            raise ValueError("rrf_k must be a positive integer")
        for name, value in (("exact_weight", exact_weight), ("sparse_weight", sparse_weight)):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite float")
        self.store = store
        self.rrf_k = rrf_k
        self.exact_weight = float(exact_weight)
        self.sparse_weight = float(sparse_weight)

    def search(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
    ) -> CodeSourceResult:
        return self.search_with_trace(request, profile).result

    def search_with_trace(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
    ) -> CodeExactSparseSearchResult:
        started = time.perf_counter()
        if not isinstance(request, EvidenceSearchRequest):
            raise TypeError("request must be an EvidenceSearchRequest")
        if profile is not None and not isinstance(profile, CodeQueryProfile):
            raise TypeError("profile must be a CodeQueryProfile")
        resolved_profile = profile or self.profile_for_request(request)
        if (
            resolved_profile.budget.exact_candidates == 0
            and resolved_profile.budget.sparse_candidates == 0
        ):
            raise ValueError("profile must enable exact or sparse candidates")

        query_id = self._query_id(request, resolved_profile)
        locators = _extract_locators(request.query, resolved_profile)
        normalized_tokens = normalize_code_sparse_query(request.query)
        locator_traces = _locator_traces(locators)

        try:
            resolution = self._resolve_scopes(request, resolved_profile, locators)
        except Exception as error:
            message = _safe_error_message("Code V2 scope resolution failed", error)
            return self._failed_response(
                request=request,
                profile=resolved_profile,
                query_id=query_id,
                normalized_tokens=normalized_tokens,
                locators=locator_traces,
                scopes=(),
                message=message,
                unavailable=False,
                started=started,
            )
        if resolution.unavailable_reason:
            return self._failed_response(
                request=request,
                profile=resolved_profile,
                query_id=query_id,
                normalized_tokens=normalized_tokens,
                locators=locator_traces,
                scopes=resolution.scopes,
                message=resolution.unavailable_reason,
                unavailable=True,
                started=started,
            )

        allowed_acl_refs = _allowed_acl_refs(request)
        exact_hits: tuple[_RankedHit, ...] = ()
        sparse_hits: tuple[_RankedHit, ...] = ()
        channel_errors: dict[CodeRetrievalChannel, tuple[bool, str]] = {}

        if resolved_profile.budget.exact_candidates:
            try:
                exact_hits = self._exact_search(
                    locators,
                    resolution.scopes,
                    allowed_acl_refs,
                    limit=resolved_profile.budget.exact_candidates,
                )
            except Exception as error:
                channel_errors[CodeRetrievalChannel.EXACT] = (
                    False,
                    _safe_error_message("Code V2 exact retrieval failed", error),
                )
        if resolved_profile.budget.sparse_candidates:
            try:
                sparse_hits = self._sparse_search(
                    normalized_tokens,
                    resolution.scopes,
                    allowed_acl_refs,
                    limit=resolved_profile.budget.sparse_candidates,
                )
            except Exception as error:
                channel_errors[CodeRetrievalChannel.SPARSE] = (
                    False,
                    _safe_error_message("Code V2 sparse retrieval failed", error),
                )

        try:
            candidates, diversification = self._fuse_candidates(
                resolution.scopes,
                exact_hits,
                sparse_hits,
                top_k=min(request.limit, resolved_profile.budget.total_candidates),
                explicit_version=(
                    _requested_reference(request, resolved_profile, locators)[0] is not None
                ),
            )
        except Exception as error:
            message = _safe_error_message("Code V2 result construction failed", error)
            return self._failed_response(
                request=request,
                profile=resolved_profile,
                query_id=query_id,
                normalized_tokens=normalized_tokens,
                locators=locator_traces,
                scopes=resolution.scopes,
                message=message,
                unavailable=False,
                started=started,
            )

        outcomes, errors, status = _outcomes(
            profile=resolved_profile,
            candidates=candidates,
            exact_hit_count=len(exact_hits),
            sparse_hit_count=len(sparse_hits),
            channel_errors=channel_errors,
        )
        result = CodeSourceResult(
            query_id=query_id,
            status=status,
            channel_outcomes=outcomes,
            candidates=candidates,
            context_blocks=(),
            index_version=_index_version(resolution.scopes),
            watermark=_watermark(resolution.scopes),
            latency_ms=_elapsed_ms(started),
            errors=errors,
        )
        trace = CodeExactSparseTrace(
            query_id=query_id,
            normalized_tokens=normalized_tokens,
            locators=locator_traces,
            generation_scopes=_scope_labels(resolution.scopes),
            channel_hits=_hit_traces(exact_hits, sparse_hits, diversification),
            fusion_policy=(
                f"{FUSION_POLICY}:k={self.rrf_k}:"
                f"exact={self.exact_weight:g}:sparse={self.sparse_weight:g}"
            ),
        )
        return CodeExactSparseSearchResult(result=result, trace=trace)

    def profile_for_request(self, request: EvidenceSearchRequest) -> CodeQueryProfile:
        target_ref = request.scope.commit or request.scope.branch or "current"
        return CodeQueryProfile(
            task=CodeTask.IMPLEMENTATION,
            target_ref=unicodedata.normalize("NFC", target_ref).strip() or "current",
        )

    def _resolve_scopes(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        locators: Sequence[_Locator],
    ) -> _ScopeResolution:
        repository_ids = tuple(dict.fromkeys(request.scope.repository_ids))
        uri_repositories = tuple(
            dict.fromkeys(locator.repository_id for locator in locators if locator.repository_id)
        )
        if len(uri_repositories) > 1:
            return _ScopeResolution((), "Code URI locators span more than one repository")
        if uri_repositories:
            uri_repository = uri_repositories[0]
            if repository_ids and uri_repository not in repository_ids:
                return _ScopeResolution((), "Code URI repository is outside the requested scope")
            repository_ids = (uri_repository,)

        reference, reference_error = _requested_reference(request, profile, locators)
        if reference_error:
            return _ScopeResolution((), reference_error)

        rows = self._generation_rows(
            project_id=request.scope.project_id,
            repository_ids=repository_ids or None,
            reference=reference,
        )
        if not rows:
            return _ScopeResolution((), "No Code V2 generation resolves inside the requested scope")

        if reference is None and repository_ids:
            resolved_repositories = {str(row["repository_id"]) for row in rows}
            if resolved_repositories != set(repository_ids):
                return _ScopeResolution(
                    (),
                    "An explicitly scoped repository has no active Code V2 generation",
                )

        scopes: list[_GenerationScope] = []
        for row in rows:
            repository_id = str(row["repository_id"])
            generation_id = str(row["generation_id"])
            project_id = str(row["project_id"])
            commit_sha = str(row["commit_sha"])
            active_only = bool(row["active_only"])
            if str(row["generation_status"]) != "published":
                return _ScopeResolution(
                    tuple(scopes),
                    "The resolved Code V2 generation is not published",
                )
            publication = self.store.get_code_index_publication(
                generation_id,
                repository_id=repository_id,
                active_only=active_only,
            )
            unavailable = _publication_unavailable_reason(
                publication,
                project_id=project_id,
                repository_id=repository_id,
                generation_id=generation_id,
            )
            if unavailable:
                return _ScopeResolution(tuple(scopes), unavailable)
            assert publication is not None
            scopes.append(
                _GenerationScope(
                    project_id=project_id,
                    repository_id=repository_id,
                    generation_id=generation_id,
                    commit_sha=commit_sha,
                    active_only=active_only,
                    publication=publication,
                )
            )
        return _ScopeResolution(tuple(sorted(scopes, key=_scope_sort_key)))

    def _generation_rows(
        self,
        *,
        project_id: str | None,
        repository_ids: Sequence[str] | None,
        reference: tuple[str, str] | None,
    ) -> list[dict[str, Any]]:
        connection = getattr(self.store, "connection", None)
        if connection is None:
            if reference is not None:
                return []
            active = self.store.active_code_generations(
                project_id=project_id,
                repository_ids=repository_ids,
            )
            return [
                {
                    "project_id": project_id or "unknown-project",
                    "repository_id": repository_id,
                    "generation_id": generation_id,
                    "commit_sha": generation_id,
                    "generation_status": "published",
                    "active_only": True,
                }
                for repository_id, generation_id in sorted(active.items())
            ]

        clauses = ["1=1"]
        values: list[Any] = []
        if project_id is not None:
            clauses.append("r.project_id=?")
            values.append(project_id)
        if repository_ids is not None:
            if not repository_ids:
                return []
            marks = ",".join("?" for _ in repository_ids)
            clauses.append(f"r.id IN ({marks})")
            values.extend(repository_ids)

        with connection() as db:
            if reference is None:
                rows = db.execute(
                    f"""
                    SELECT r.project_id, r.id AS repository_id,
                           r.active_generation_id AS generation_id,
                           g.commit_sha, g.status AS generation_status,
                           1 AS active_only
                    FROM repositories r
                    JOIN index_generations g
                      ON g.id=r.active_generation_id AND g.repository_id=r.id
                    WHERE r.active_generation_id IS NOT NULL
                      AND {" AND ".join(clauses)}
                    ORDER BY r.id
                    """,
                    values,
                ).fetchall()
                return [dict(row) for row in rows]

            kind, value = reference
            if kind == "tag":
                return []
            if kind == "generation":
                reference_clause = "g.id=?"
                reference_values: list[Any] = [value]
            elif kind == "full_sha":
                reference_clause = "lower(g.commit_sha)=?"
                reference_values = [value.casefold()]
            elif kind == "short_sha":
                reference_clause = "lower(g.commit_sha) LIKE ?"
                reference_values = [value.casefold() + "%"]
            elif kind == "branch":
                branch_heads = self._branch_heads(db, value, project_id, repository_ids)
                if not branch_heads:
                    return []
                marks = ",".join("(?, ?)" for _ in branch_heads)
                reference_clause = f"(g.repository_id, lower(g.commit_sha)) IN ({marks})"
                reference_values = [
                    item
                    for repository_id, sha in branch_heads
                    for item in (repository_id, sha.casefold())
                ]
            else:
                return []

            rows = db.execute(
                f"""
                SELECT r.project_id, r.id AS repository_id, g.id AS generation_id,
                       g.commit_sha, g.status AS generation_status,
                       CASE WHEN r.active_generation_id=g.id THEN 1 ELSE 0 END AS active_only
                FROM index_generations g
                JOIN repositories r ON r.id=g.repository_id
                WHERE {" AND ".join(clauses)}
                  AND {reference_clause}
                ORDER BY r.id, g.id
                """,
                [*values, *reference_values],
            ).fetchall()
        decoded = [dict(row) for row in rows]
        if kind in {"full_sha", "short_sha", "generation"} and len(decoded) > 1:
            return []
        return decoded

    @staticmethod
    def _branch_heads(
        db: Any,
        branch: str,
        project_id: str | None,
        repository_ids: Sequence[str] | None,
    ) -> list[tuple[str, str]]:
        clauses = ["1=1"]
        values: list[Any] = []
        if project_id is not None:
            clauses.append("project_id=?")
            values.append(project_id)
        if repository_ids is not None:
            marks = ",".join("?" for _ in repository_ids)
            clauses.append(f"id IN ({marks})")
            values.extend(repository_ids)
        repositories = db.execute(
            f"""
            SELECT id, default_branch, head_commit, active_generation_id
            FROM repositories WHERE {" AND ".join(clauses)}
            ORDER BY id
            """,
            values,
        ).fetchall()
        heads = {
            str(row["id"]): str(row["head_commit"])
            for row in repositories
            if str(row["default_branch"] or "") == branch and row["active_generation_id"]
        }
        has_branches = db.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='git_branches'
            """
        ).fetchone()
        if has_branches:
            branch_clauses = ["b.name=?"]
            branch_values: list[Any] = [branch]
            if repository_ids is not None:
                marks = ",".join("?" for _ in repository_ids)
                branch_clauses.append(f"b.repository_id IN ({marks})")
                branch_values.extend(repository_ids)
            rows = db.execute(
                f"""
                SELECT b.repository_id, b.head_sha
                FROM git_branches b
                JOIN repositories r ON r.id=b.repository_id
                WHERE {" AND ".join(branch_clauses)}
                  AND (? IS NULL OR r.project_id=?)
                ORDER BY b.repository_id
                """,
                [*branch_values, project_id, project_id],
            ).fetchall()
            heads.update((str(row["repository_id"]), str(row["head_sha"])) for row in rows)
        return sorted(heads.items())

    def _exact_search(
        self,
        locators: Sequence[_Locator],
        scopes: Sequence[_GenerationScope],
        allowed_acl_refs: Sequence[str] | None,
        *,
        limit: int,
    ) -> tuple[_RankedHit, ...]:
        identifying = [
            locator
            for locator in locators
            if locator.kind.value in _IDENTIFYING_PRIORITIES
            and not (
                locator.kind is CodeLocatorKind.URI
                and not locator.path
                and not locator.value.startswith("code-unit://")
            )
        ]
        if not identifying:
            return ()
        best_priority = min(locator.priority for locator in identifying)
        selected = [locator for locator in identifying if locator.priority == best_priority]
        accumulated: dict[tuple[str, str], _ExactMatch] = {}
        per_lookup_limit = max(limit * 4, 20)

        for scope in scopes:
            store_scope = scope.store_scope(allowed_acl_refs)
            for locator in selected:
                row_matches = self._lookup_locator(
                    locator,
                    store_scope,
                    limit=per_lookup_limit,
                )
                for row, matched_fields in row_matches:
                    key = (str(row["generation_id"]), str(row["id"]))
                    current = accumulated.get(key)
                    explanation = locator.explanation or (
                        f"exact {locator.kind.value} match for {locator.value}"
                    )
                    if current is None or locator.priority < current.priority:
                        accumulated[key] = _ExactMatch(
                            row=row,
                            priority=locator.priority,
                            matched_fields=set(matched_fields),
                            locator_kind=locator.kind,
                            locator_value=locator.value,
                            explanation=explanation,
                        )
                    else:
                        current.matched_fields.update(matched_fields)

        matches = list(accumulated.values())
        _annotate_ambiguity(matches)
        matches.sort(
            key=lambda item: (
                item.priority,
                str(item.row["repository_id"]),
                str(item.row["path"]),
                _optional_int(item.row.get("start_line")),
                int(item.row.get("ordinal") or 0),
                str(item.row["id"]),
                str(item.row["generation_id"]),
            )
        )
        matches = matches[:limit]
        total_priorities = max(_IDENTIFYING_PRIORITIES.values()) + 2
        return tuple(
            _RankedHit(
                row=item.row,
                raw_score=float((total_priorities - item.priority) / total_priorities),
                raw_rank=rank,
                matched_fields=tuple(sorted(item.matched_fields)),
                locator_kind=item.locator_kind,
                explanation=item.explanation,
            )
            for rank, item in enumerate(matches)
        )

    def _lookup_locator(
        self,
        locator: _Locator,
        store_scope: Mapping[str, Any],
        *,
        limit: int,
    ) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
        if locator.kind is CodeLocatorKind.URI:
            if locator.value.startswith("code-unit://"):
                return [
                    (row, ("id",))
                    for row in self.store.lookup_code_units_exact(
                        unit_id=locator.value,
                        **store_scope,
                        limit=limit,
                    )
                ]
            if locator.path:
                rows = self.store.lookup_code_units_exact(
                    path=locator.path,
                    **store_scope,
                    limit=limit,
                )
                return [(row, ("path",)) for row in _filter_locator_rows(rows, locator)]
            return []
        if locator.kind is CodeLocatorKind.QUALIFIED_NAME:
            return [
                (row, ("qualified_name",))
                for row in self.store.lookup_code_units_exact(
                    qualified_name=locator.value,
                    **store_scope,
                    limit=limit,
                )
            ]
        if locator.kind is CodeLocatorKind.EXACT_PATH:
            rows = self.store.lookup_code_units_exact(
                path=locator.path or locator.value,
                **store_scope,
                limit=limit,
            )
            return [(row, ("path",)) for row in _filter_locator_rows(rows, locator)]
        if locator.kind is CodeLocatorKind.BASENAME:
            rows = self.store.fielded_code_unit_search(
                {"path": locator.value},
                **store_scope,
                limit=limit,
            )
            return [
                (row, ("path",))
                for row in rows
                if PurePosixPath(str(row.get("path") or "")).name == locator.value
            ]
        if locator.kind in {CodeLocatorKind.EXACT_SYMBOL, CodeLocatorKind.ERROR_CODE}:
            return self._lookup_exact_token(
                locator.value,
                store_scope,
                error_code=locator.kind is CodeLocatorKind.ERROR_CODE,
                limit=limit,
            )
        return []

    def _lookup_exact_token(
        self,
        token: str,
        store_scope: Mapping[str, Any],
        *,
        error_code: bool,
        limit: int,
    ) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
        fields = ("identifiers", "qualified_name", "signature")
        if error_code:
            fields = (*fields, "body", "doc")
        matched: dict[tuple[str, str], tuple[dict[str, Any], set[str]]] = {}
        for field in fields:
            rows = self.store.fielded_code_unit_search(
                {field: token},
                **store_scope,
                limit=limit,
            )
            for row in rows:
                if not _field_has_exact_token(str(row.get(field) or ""), token, field=field):
                    continue
                key = (str(row["generation_id"]), str(row["id"]))
                if key not in matched:
                    matched[key] = (row, set())
                matched[key][1].add(field)
        ordered = sorted(
            matched.values(),
            key=lambda item: (
                str(item[0]["repository_id"]),
                str(item[0]["path"]),
                _optional_int(item[0].get("start_line")),
                str(item[0]["id"]),
            ),
        )
        return [(row, tuple(sorted(matched_fields))) for row, matched_fields in ordered]

    def _sparse_search(
        self,
        normalized_tokens: Sequence[str],
        scopes: Sequence[_GenerationScope],
        allowed_acl_refs: Sequence[str] | None,
        *,
        limit: int,
    ) -> tuple[_RankedHit, ...]:
        fts_tokens = [
            token for token in normalized_tokens if re.search(r"[A-Za-z0-9_\u3400-\u9fff]", token)
        ]
        if not fts_tokens:
            return ()
        query = " ".join(fts_tokens)
        rows: list[dict[str, Any]] = []
        per_scope_limit = max(limit * 4, 20)
        for scope in scopes:
            rows.extend(
                self.store.fielded_code_unit_search(
                    query,
                    **scope.store_scope(allowed_acl_refs),
                    limit=per_scope_limit,
                )
            )
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (str(row["generation_id"]), str(row["id"]))
            previous = unique.get(key)
            if previous is None or _sparse_sort_key(row) < _sparse_sort_key(previous):
                unique[key] = row
        ordered = sorted(unique.values(), key=_sparse_sort_key)[:limit]
        return tuple(
            _RankedHit(
                row=row,
                raw_score=_positive_bm25(row.get("sparse_rank")),
                raw_rank=rank,
                matched_fields=_matched_fields(row, normalized_tokens),
                locator_kind=None,
                explanation=(
                    "existing fielded FTS5 match; raw score is negated BM25 and "
                    "field weights remain store-owned"
                ),
            )
            for rank, row in enumerate(ordered)
        )

    def _fuse_candidates(
        self,
        scopes: Sequence[_GenerationScope],
        exact_hits: Sequence[_RankedHit],
        sparse_hits: Sequence[_RankedHit],
        *,
        top_k: int,
        explicit_version: bool,
    ) -> tuple[
        tuple[CodeRetrievalCandidate, ...],
        Mapping[tuple[str, str], _DiversificationDecision],
    ]:
        scope_by_key = {(scope.repository_id, scope.generation_id): scope for scope in scopes}
        exact_by_key = {hit.key: hit for hit in exact_hits}
        sparse_by_key = {hit.key: hit for hit in sparse_hits}
        rows_by_key = {hit.key: hit.row for hit in (*exact_hits, *sparse_hits)}
        fused: list[tuple[float, tuple[str, str], Mapping[str, Any]]] = []
        for key, row in rows_by_key.items():
            score = 0.0
            if exact := exact_by_key.get(key):
                score += self.exact_weight / (self.rrf_k + exact.raw_rank + 1)
            if sparse := sparse_by_key.get(key):
                score += self.sparse_weight / (self.rrf_k + sparse.raw_rank + 1)
            fused.append((float(score), key, row))
        fused.sort(
            key=lambda item: (
                -item[0],
                exact_by_key[item[1]].raw_rank if item[1] in exact_by_key else 10**9,
                sparse_by_key[item[1]].raw_rank if item[1] in sparse_by_key else 10**9,
                str(item[2]["repository_id"]),
                str(item[2]["path"]),
                _optional_int(item[2].get("start_line")),
                str(item[2]["id"]),
                str(item[2]["generation_id"]),
            )
        )

        entity_types = self._entity_types([item[2] for item in fused])
        selected, diversification = _diversify_fused_hits(
            fused,
            entity_types=entity_types,
            exact_by_key=exact_by_key,
            top_k=top_k,
        )
        candidates: list[CodeRetrievalCandidate] = []
        for source_rank, (fused_score, key, row) in enumerate(selected):
            scope = scope_by_key[(str(row["repository_id"]), str(row["generation_id"]))]
            _validate_row_scope(row, scope)
            stable_version = _stable_version(row, scope)
            if stable_version != scope.commit_sha:
                raise RuntimeError("retrieval unit version conflicts with its published generation")
            exact = exact_by_key.get(key)
            sparse = sparse_by_key.get(key)
            raw_scores: list[CodeChannelScore] = []
            raw_ranks: list[CodeChannelRank] = []
            if exact is not None:
                raw_scores.append(
                    CodeChannelScore(
                        channel=CodeRetrievalChannel.EXACT,
                        score=float(exact.raw_score),
                    )
                )
                raw_ranks.append(
                    CodeChannelRank(
                        channel=CodeRetrievalChannel.EXACT,
                        rank=exact.raw_rank,
                    )
                )
            if sparse is not None:
                raw_scores.append(
                    CodeChannelScore(
                        channel=CodeRetrievalChannel.SPARSE,
                        score=float(sparse.raw_score),
                    )
                )
                raw_ranks.append(
                    CodeChannelRank(
                        channel=CodeRetrievalChannel.SPARSE,
                        rank=sparse.raw_rank,
                    )
                )
            locator = _evidence_locator(row, scope)
            entity_id = str(row["entity_id"])
            relation_path = CodeRelationPath(
                nodes=(
                    CodeRelationNode(
                        entity_id=entity_id,
                        repository_id=scope.repository_id,
                        stable_version=stable_version,
                        source_generation=scope.generation_id,
                        locator=locator,
                        acl_ref=str(row["acl_ref"]),
                    ),
                ),
                edges=(),
                path_score=0.0,
            )
            candidates.append(
                CodeRetrievalCandidate(
                    entity_id=entity_id,
                    retrieval_unit_id=str(row["id"]),
                    repository_id=scope.repository_id,
                    entity_type=entity_types.get(
                        (entity_id, scope.generation_id),
                        str(row.get("unit_type") or "CodeRetrievalUnit"),
                    ),
                    stable_version=stable_version,
                    source_generation=scope.generation_id,
                    raw_channel_scores=tuple(raw_scores),
                    raw_channel_ranks=tuple(raw_ranks),
                    within_source_rank=source_rank,
                    source_fused_score=float(fused_score),
                    calibrated_relevance=CodeUncalibratedScore(
                        status="unavailable",
                        reason="calibration is deferred to the authorized C3 stage",
                    ),
                    version_alignment=(
                        CodeVersionAlignment.EXACT
                        if explicit_version
                        else CodeVersionAlignment.COMPATIBLE
                    ),
                    fact_status=CodeFactStatus.OBSERVED,
                    derivation=CodeDerivation.TREE_SITTER,
                    review_status=CodeReviewStatus.MACHINE_CONFIRMED,
                    role=CodeCandidateRole.TARGET,
                    relation_path=relation_path,
                    locator=locator,
                    token_estimate=max(0, int(row.get("token_count") or 0)),
                    acl_ref=str(row["acl_ref"]),
                )
            )
        return tuple(candidates), diversification

    def _entity_types(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> dict[tuple[str, str], str]:
        connection = getattr(self.store, "connection", None)
        if connection is None or not rows:
            return {}
        pairs = sorted({(str(row["entity_id"]), str(row["generation_id"])) for row in rows})
        found = []
        with connection() as db:
            for offset in range(0, len(pairs), 200):
                batch = pairs[offset : offset + 200]
                clauses = " OR ".join("(id=? AND generation_id=?)" for _ in batch)
                values = [item for pair in batch for item in pair]
                found.extend(
                    db.execute(
                        f"""
                        SELECT id, generation_id, entity_type FROM entities
                        WHERE {clauses}
                        """,
                        values,
                    ).fetchall()
                )
        return {
            (str(row["id"]), str(row["generation_id"])): str(row["entity_type"]) for row in found
        }

    def _failed_response(
        self,
        *,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        query_id: str,
        normalized_tokens: tuple[str, ...],
        locators: tuple[CodeLocatorTrace, ...],
        scopes: Sequence[_GenerationScope],
        message: str,
        unavailable: bool,
        started: float,
    ) -> CodeExactSparseSearchResult:
        channel_errors: dict[CodeRetrievalChannel, tuple[bool, str]] = {}
        if profile.budget.exact_candidates:
            channel_errors[CodeRetrievalChannel.EXACT] = (unavailable, message)
        if profile.budget.sparse_candidates:
            channel_errors[CodeRetrievalChannel.SPARSE] = (unavailable, message)
        outcomes, errors, status = _outcomes(
            profile=profile,
            candidates=(),
            exact_hit_count=0,
            sparse_hit_count=0,
            channel_errors=channel_errors,
        )
        result = CodeSourceResult(
            query_id=query_id,
            status=status,
            channel_outcomes=outcomes,
            candidates=(),
            context_blocks=(),
            index_version=RETRIEVER_VERSION,
            watermark=_watermark(scopes),
            latency_ms=_elapsed_ms(started),
            errors=errors,
        )
        trace = CodeExactSparseTrace(
            query_id=query_id,
            normalized_tokens=normalized_tokens,
            locators=locators,
            generation_scopes=_scope_labels(scopes),
            channel_hits=(),
            fusion_policy=(
                f"{FUSION_POLICY}:k={self.rrf_k}:"
                f"exact={self.exact_weight:g}:sparse={self.sparse_weight:g}"
            ),
            unavailable_reason=message,
        )
        return CodeExactSparseSearchResult(result=result, trace=trace)

    @staticmethod
    def _query_id(request: EvidenceSearchRequest, profile: CodeQueryProfile) -> str:
        payload = {
            "query": unicodedata.normalize("NFC", request.query),
            "scope": {
                **request.scope.model_dump(mode="json"),
                "allowed_acl_refs": sorted(set(request.scope.allowed_acl_refs)),
                "enforce_acl": request.scope.enforce_acl,
            },
            "limit": request.limit,
            "profile": profile.model_dump(mode="json"),
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return f"code-query://exact-sparse/sha256/{digest}"


def _extract_locators(query: str, profile: CodeQueryProfile) -> tuple[_Locator, ...]:
    normalized = unicodedata.normalize("NFC", query)
    locators: list[_Locator] = []
    uri_spans: list[tuple[int, int]] = []
    for match in _URI_RE.finditer(normalized):
        raw = match.group(0).rstrip("),.;]")
        uri_spans.append(match.span())
        locators.append(_parse_uri(raw))

    scrubbed = normalized
    for start, end in reversed(uri_spans):
        scrubbed = scrubbed[:start] + " " * (end - start) + scrubbed[end:]

    for match in _FULL_SHA_RE.finditer(scrubbed):
        locators.append(
            _Locator(
                kind=CodeLocatorKind.FULL_SHA,
                value=match.group(0).casefold(),
                priority=1,
                explanation="full commit SHA constrains the Code V2 generation",
            )
        )
    full_spans = {match.span() for match in _FULL_SHA_RE.finditer(scrubbed)}
    for match in _SHORT_SHA_RE.finditer(scrubbed):
        if any(start <= match.start(1) and match.end(1) <= end for start, end in full_spans):
            continue
        value = match.group(1)
        explicitly_labeled = match.group(0).casefold().lstrip().startswith(("commit", "sha"))
        if (
            len(value) < 12
            and not explicitly_labeled
            and scrubbed.strip().casefold() != value.casefold()
        ):
            continue
        locators.append(
            _Locator(
                kind=CodeLocatorKind.SHORT_SHA,
                value=value.casefold(),
                priority=1,
                explanation="short commit SHA requires a unique published generation",
            )
        )

    for match in _STACK_PYTHON_RE.finditer(scrubbed):
        path = _normalize_path(match.group(1))
        line = int(match.group(2))
        symbol = match.group(3) or ""
        locators.append(
            _Locator(
                kind=CodeLocatorKind.EXACT_PATH,
                value=path,
                path=path,
                symbol=symbol,
                start_line=line,
                end_line=line,
                priority=3,
                explanation="stack frame resolved by exact path, line, and optional symbol",
            )
        )
        locators.append(
            _Locator(
                kind=CodeLocatorKind.STACK_FRAME,
                value=_single_line(match.group(0)),
                path=path,
                symbol=symbol,
                start_line=line,
                end_line=line,
                priority=3,
                explanation="stack frame locator parsed",
            )
        )
    for match in _STACK_GENERIC_RE.finditer(scrubbed):
        path = _normalize_path(match.group("path"))
        line = int(match.group("line"))
        symbol = match.group("symbol") or ""
        locators.append(
            _Locator(
                kind=CodeLocatorKind.EXACT_PATH,
                value=path,
                path=path,
                symbol=symbol.rsplit(".", 1)[-1],
                start_line=line,
                end_line=line,
                priority=3,
                explanation="stack frame resolved by exact path, line, and optional symbol",
            )
        )
    for match in _TEST_SELECTOR_RE.finditer(scrubbed):
        path = _normalize_path(match.group(1))
        selector = match.group(2)
        locators.append(
            _Locator(
                kind=CodeLocatorKind.EXACT_PATH,
                value=path,
                path=path,
                symbol=selector.split("::")[-1],
                priority=3,
                explanation="test selector resolved by exact path and test symbol",
            )
        )
        locators.append(
            _Locator(
                kind=CodeLocatorKind.TEST_SELECTOR,
                value=f"{path}::{selector}",
                path=path,
                symbol=selector.split("::")[-1],
                priority=3,
                explanation="test selector locator parsed",
            )
        )

    for target in profile.target_paths:
        path = _normalize_path(target)
        kind = CodeLocatorKind.EXACT_PATH if "/" in path else CodeLocatorKind.BASENAME
        locators.append(
            _Locator(
                kind=kind,
                value=path,
                path=path if kind is CodeLocatorKind.EXACT_PATH else "",
                priority=_IDENTIFYING_PRIORITIES[kind.value],
                explanation="exact path target supplied by the resolved query profile",
            )
        )
    for match in _PATH_RE.finditer(scrubbed):
        path = _normalize_path(match.group(1))
        locators.append(
            _Locator(
                kind=CodeLocatorKind.EXACT_PATH,
                value=path,
                path=path,
                priority=3,
                explanation="exact repository-relative path parsed from the query",
            )
        )
    for match in _BASENAME_RE.finditer(scrubbed):
        if any(
            locator.kind is CodeLocatorKind.EXACT_PATH
            and PurePosixPath(locator.path).name == match.group(1)
            for locator in locators
        ):
            continue
        locators.append(
            _Locator(
                kind=CodeLocatorKind.BASENAME,
                value=match.group(1),
                priority=5,
                explanation="basename match retains every same-name path",
            )
        )

    for target in profile.target_identifiers:
        kind = CodeLocatorKind.QUALIFIED_NAME if "." in target else CodeLocatorKind.EXACT_SYMBOL
        locators.append(
            _Locator(
                kind=kind,
                value=target,
                priority=_IDENTIFYING_PRIORITIES[kind.value],
                explanation="identifier target supplied by the resolved query profile",
            )
        )
    path_extensions = {
        PurePosixPath(locator.value).suffix.casefold()
        for locator in locators
        if locator.kind in {CodeLocatorKind.EXACT_PATH, CodeLocatorKind.BASENAME}
    }
    for match in _QUALIFIED_RE.finditer(scrubbed):
        value = match.group(1)
        if PurePosixPath(value).suffix.casefold() in path_extensions:
            continue
        locators.append(
            _Locator(
                kind=CodeLocatorKind.QUALIFIED_NAME,
                value=value,
                priority=2,
                explanation="exact qualified name parsed from the query",
            )
        )
    for match in _ERROR_CODE_RE.finditer(scrubbed):
        locators.append(
            _Locator(
                kind=CodeLocatorKind.ERROR_CODE,
                value=match.group(1),
                priority=4,
                explanation="error code requires an exact indexed token match",
            )
        )

    stripped = scrubbed.strip()
    identifiers = _IDENTIFIER_RE.findall(scrubbed)
    for identifier in identifiers:
        folded = identifier.casefold()
        if folded in _STOP_IDENTIFIERS:
            continue
        code_shaped = (
            stripped == identifier
            or "_" in identifier
            or "$" in identifier
            or any(character.isupper() for character in identifier[1:])
            or identifier[:1].isupper()
        )
        if not code_shaped or _ERROR_CODE_RE.fullmatch(identifier):
            continue
        locators.append(
            _Locator(
                kind=CodeLocatorKind.EXACT_SYMBOL,
                value=identifier,
                priority=4,
                explanation="exact identifier token parsed from the query",
            )
        )

    for match in _REF_RE.finditer(scrubbed):
        kind = (
            CodeLocatorKind.BRANCH if match.group(1).casefold() == "branch" else CodeLocatorKind.TAG
        )
        locators.append(
            _Locator(
                kind=kind,
                value=match.group(2),
                priority=1,
                explanation=f"{kind.value} constrains version resolution",
            )
        )
    for match in _REFS_RE.finditer(scrubbed):
        kind = CodeLocatorKind.BRANCH if match.group(1) == "heads" else CodeLocatorKind.TAG
        locators.append(
            _Locator(
                kind=kind,
                value=match.group(2),
                priority=1,
                explanation=f"Git {kind.value} ref constrains version resolution",
            )
        )
    return _deduplicate_locators(locators)


def _parse_uri(uri: str) -> _Locator:
    if uri.startswith("code-unit://"):
        return _Locator(
            kind=CodeLocatorKind.URI,
            value=uri,
            priority=0,
            explanation="exact retrieval unit URI",
        )
    payload = uri.removeprefix("code://")
    base, _, fragment = payload.partition("#")
    repository_id = ""
    ref = ""
    path = ""
    if "@" in base:
        owner, remainder = base.rsplit("@", 1)
        ref, separator, path = remainder.partition("/")
        repository_id = owner
        if not separator:
            path = ""
    symbol_match = re.search(r"(?:^|[&;])symbol=([^&;]+)", fragment)
    line_match = re.search(r"(?:^|[&;])L(\d+)(?:-L?(\d+))?", fragment)
    start_line = int(line_match.group(1)) if line_match else None
    end_line = int(line_match.group(2) or line_match.group(1)) if line_match else None
    return _Locator(
        kind=CodeLocatorKind.URI,
        value=uri,
        path=_normalize_path(path) if path else "",
        repository_id=repository_id,
        ref=ref,
        symbol=symbol_match.group(1) if symbol_match else "",
        start_line=start_line,
        end_line=end_line,
        priority=0,
        explanation="Code URI has highest exact-locator priority",
    )


def _deduplicate_locators(locators: Sequence[_Locator]) -> tuple[_Locator, ...]:
    unique: dict[tuple[Any, ...], _Locator] = {}
    for locator in locators:
        key = (
            locator.kind,
            locator.value,
            locator.path,
            locator.repository_id,
            locator.ref,
            locator.symbol,
            locator.start_line,
            locator.end_line,
        )
        unique.setdefault(key, locator)
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (item.priority, item.kind.value, item.value),
        )
    )


def _requested_reference(
    request: EvidenceSearchRequest,
    profile: CodeQueryProfile,
    locators: Sequence[_Locator],
) -> tuple[tuple[str, str] | None, str]:
    references: list[tuple[str, str]] = []

    def add(value: str, kind: str | None = None) -> None:
        normalized = value.strip()
        if not normalized or normalized == "current":
            return
        if normalized.startswith("refs/heads/"):
            kind = "branch"
            normalized = normalized.removeprefix("refs/heads/")
        elif normalized.startswith("refs/tags/"):
            kind = "tag"
            normalized = normalized.removeprefix("refs/tags/")
        elif normalized.casefold().startswith("branch:"):
            kind = "branch"
            normalized = normalized.split(":", 1)[1]
        elif normalized.casefold().startswith("tag:"):
            kind = "tag"
            normalized = normalized.split(":", 1)[1]
        references.append((kind or _reference_kind(normalized), normalized))

    if request.scope.commit:
        add(request.scope.commit)
    if request.scope.branch:
        add(request.scope.branch, "branch")
    add(profile.target_ref)
    for locator in locators:
        if locator.kind is CodeLocatorKind.FULL_SHA:
            add(locator.value, "full_sha")
        elif locator.kind is CodeLocatorKind.SHORT_SHA:
            add(locator.value, "short_sha")
        elif locator.kind is CodeLocatorKind.BRANCH:
            add(locator.value, "branch")
        elif locator.kind is CodeLocatorKind.TAG:
            add(locator.value, "tag")
        elif locator.kind is CodeLocatorKind.URI and locator.ref:
            add(locator.ref)
    canonical = {(kind, value.casefold() if "sha" in kind else value) for kind, value in references}
    if not canonical:
        return None, ""
    if len(canonical) != 1:
        return None, "Conflicting Code version locators were supplied"
    kind, value = canonical.pop()
    if kind == "unknown":
        return None, "The requested Code ref is not available in current Code V2 metadata"
    if kind == "tag":
        return None, "Tag resolution is unavailable because Code V2 stores no tag publication map"
    return (kind, value), ""


def _reference_kind(value: str) -> str:
    if value.startswith("generation://"):
        return "generation"
    if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        return "full_sha"
    if re.fullmatch(r"[0-9a-fA-F]{7,39}", value):
        return "short_sha"
    if value.startswith("refs/heads/"):
        return "branch"
    if value.startswith("refs/tags/"):
        return "tag"
    return "branch"


def _publication_unavailable_reason(
    publication: Mapping[str, Any] | None,
    *,
    project_id: str,
    repository_id: str,
    generation_id: str,
) -> str:
    if publication is None:
        return "The resolved Code V2 publication is missing or is not active"
    identity = (
        str(publication.get("project_id") or ""),
        str(publication.get("repository_id") or ""),
        str(publication.get("generation_id") or ""),
    )
    if identity != (project_id, repository_id, generation_id):
        return "The Code V2 publication provenance does not match the resolved scope"
    if str(publication.get("status") or "") != "published":
        return "The resolved Code V2 publication is not published"
    sparse = str(publication.get("sparse") or "").casefold()
    if sparse in _NOT_BUILT:
        return "The resolved Code V2 sparse publication is not built"
    validation = publication.get("validation")
    if isinstance(validation, Mapping):
        capabilities = validation.get("capabilities")
        if isinstance(capabilities, Mapping) and capabilities.get("sparse_retrieval") is False:
            return "The resolved Code V2 publication declares sparse retrieval unavailable"
    return ""


def _allowed_acl_refs(request: EvidenceSearchRequest) -> Sequence[str] | None:
    if request.scope.enforce_acl or request.scope.allowed_acl_refs:
        return tuple(dict.fromkeys(request.scope.allowed_acl_refs))
    return None


def _filter_locator_rows(
    rows: Sequence[dict[str, Any]],
    locator: _Locator,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        start = row.get("start_line")
        end = row.get("end_line")
        if (
            locator.start_line is not None
            and start is not None
            and end is not None
            and (
                int(end) < locator.start_line
                or int(start) > (locator.end_line or locator.start_line)
            )
        ):
            continue
        if locator.symbol and not any(
            _field_has_exact_token(str(row.get(field) or ""), locator.symbol, field=field)
            for field in ("qualified_name", "signature", "identifiers")
        ):
            continue
        filtered.append(row)
    return filtered


def _field_has_exact_token(text: str, token: str, *, field: str) -> bool:
    if field == "qualified_name":
        return text == token or text.rsplit(".", 1)[-1] == token
    if field == "path":
        return text == token or PurePosixPath(text).name == token
    pattern = _WORD_BOUNDARY_TEMPLATE.format(re.escape(token))
    return re.search(pattern, text, re.IGNORECASE) is not None


def _annotate_ambiguity(matches: Sequence[_ExactMatch]) -> None:
    groups: dict[tuple[CodeLocatorKind, int, str], list[_ExactMatch]] = {}
    for item in matches:
        if item.locator_kind not in {
            CodeLocatorKind.QUALIFIED_NAME,
            CodeLocatorKind.EXACT_SYMBOL,
            CodeLocatorKind.ERROR_CODE,
            CodeLocatorKind.BASENAME,
        }:
            continue
        groups.setdefault(
            (item.locator_kind, item.priority, item.locator_value.casefold()),
            [],
        ).append(item)
    for (kind, _, _), items in groups.items():
        paths = sorted({str(item.row.get("path") or "") for item in items})
        if len(paths) < 2:
            continue
        display = ", ".join(paths[:8])
        if len(paths) > 8:
            display += f", and {len(paths) - 8} more"
        for item in items:
            item.explanation = (
                f"ambiguous {kind.value}; retained path/module hard negatives "
                f"without silent selection: {display}"
            )


def _matched_fields(
    row: Mapping[str, Any],
    normalized_tokens: Sequence[str],
) -> tuple[str, ...]:
    tokens = [
        token.casefold()
        for token in normalized_tokens
        if re.search(r"[A-Za-z0-9_\u3400-\u9fff]", token)
    ]
    matched = []
    for field in _FIELD_NAMES:
        text = str(row.get(field) or "").casefold()
        if any(_text_has_normalized_token(text, token) for token in tokens):
            matched.append(field)
    return tuple(matched)


def _text_has_normalized_token(text: str, token: str) -> bool:
    if re.search(r"[\u3400-\u9fff]", token):
        return token in text
    return (
        re.search(
            _WORD_BOUNDARY_TEMPLATE.format(re.escape(token)),
            text,
            re.IGNORECASE,
        )
        is not None
    )


def _positive_bm25(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return float(max(0.0, -score))


def _sparse_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    try:
        score = float(row.get("sparse_rank") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return (
        score,
        str(row["repository_id"]),
        str(row["path"]),
        _optional_int(row.get("start_line")),
        int(row.get("ordinal") or 0),
        str(row["id"]),
        str(row["generation_id"]),
    )


def _evidence_locator(row: Mapping[str, Any], scope: _GenerationScope) -> str:
    metadata = row.get("metadata")
    source_uri = ""
    if isinstance(metadata, Mapping):
        lineage = metadata.get("source_lineage")
        if isinstance(lineage, Mapping):
            source_uri = str(lineage.get("source_uri") or "")
    if not source_uri:
        source_uri = f"code://{scope.repository_id}@{_stable_version(row, scope)}/{row['path']}"
    source_uri = source_uri.split("#", 1)[0]
    start = row.get("start_line")
    end = row.get("end_line")
    if start is not None:
        end_value = end if end is not None else start
        return f"{source_uri}#L{int(start)}-L{int(end_value)}"
    return f"{source_uri}#unit={row['id']}"


def _stable_version(row: Mapping[str, Any], scope: _GenerationScope) -> str:
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping):
        ref = str(metadata.get("ref") or "")
        if ref:
            return ref
        lineage = metadata.get("source_lineage")
        if isinstance(lineage, Mapping):
            ref = str(lineage.get("ref") or "")
            if ref:
                return ref
    return scope.commit_sha


def _validate_row_scope(row: Mapping[str, Any], scope: _GenerationScope) -> None:
    actual = (
        str(row.get("project_id") or ""),
        str(row.get("repository_id") or ""),
        str(row.get("generation_id") or ""),
    )
    expected = (scope.project_id, scope.repository_id, scope.generation_id)
    if actual != expected:
        raise RuntimeError("retrieval unit provenance conflicts with its resolved scope")
    if not str(row.get("acl_ref") or ""):
        raise RuntimeError("retrieval unit is missing ACL provenance")


def _outcomes(
    *,
    profile: CodeQueryProfile,
    candidates: Sequence[CodeRetrievalCandidate],
    exact_hit_count: int,
    sparse_hit_count: int,
    channel_errors: Mapping[CodeRetrievalChannel, tuple[bool, str]],
) -> tuple[tuple[CodeChannelOutcome, ...], tuple[CodeChannelFailure, ...], CodeSourceStatus]:
    candidate_channels = {
        score.channel for candidate in candidates for score in candidate.raw_channel_scores
    }
    budgets = {
        CodeRetrievalChannel.EXACT: profile.budget.exact_candidates,
        CodeRetrievalChannel.SPARSE: profile.budget.sparse_candidates,
    }
    hit_counts = {
        CodeRetrievalChannel.EXACT: exact_hit_count,
        CodeRetrievalChannel.SPARSE: sparse_hit_count,
    }
    outcomes: list[CodeChannelOutcome] = []
    errors: list[CodeChannelFailure] = []
    for channel in CodeRetrievalChannel:
        if channel not in budgets:
            outcomes.append(
                CodeChannelDisabled(
                    channel=channel,
                    reason=f"{RETRIEVER_VERSION} does not implement this deferred channel",
                )
            )
            continue
        if not budgets[channel]:
            outcomes.append(
                CodeChannelDisabled(
                    channel=channel,
                    reason="channel disabled by the resolved query profile budget",
                )
            )
            continue
        if channel in channel_errors:
            unavailable, message = channel_errors[channel]
            if unavailable:
                outcomes.append(CodeChannelUnavailable(channel=channel, error=message))
                errors.append(
                    CodeChannelFailure(
                        channel=channel,
                        kind="unavailable",
                        message=message,
                    )
                )
            else:
                outcomes.append(CodeChannelError(channel=channel, error=message))
                errors.append(
                    CodeChannelFailure(
                        channel=channel,
                        kind="error",
                        message=message,
                    )
                )
            continue
        hit_count = hit_counts[channel]
        if hit_count == 0:
            outcomes.append(CodeChannelCompleteNoMatch(channel=channel))
        elif channel in candidate_channels:
            outcomes.append(CodeChannelCompleteWithHits(channel=channel, hit_count=hit_count))
        else:
            outcomes.append(CodeChannelCompletePruned(channel=channel, hit_count=hit_count))

    completed = any(
        isinstance(
            outcome,
            (
                CodeChannelCompleteNoMatch,
                CodeChannelCompletePruned,
                CodeChannelCompleteWithHits,
            ),
        )
        for outcome in outcomes
    )
    failed = bool(errors)
    if completed and failed:
        status = CodeSourceStatus.PARTIAL
    elif failed:
        status = CodeSourceStatus.UNAVAILABLE
    else:
        status = CodeSourceStatus.COMPLETE
    return tuple(outcomes), tuple(errors), status


def _diversify_fused_hits(
    fused: Sequence[tuple[float, tuple[str, str], Mapping[str, Any]]],
    *,
    entity_types: Mapping[tuple[str, str], str],
    exact_by_key: Mapping[tuple[str, str], _RankedHit],
    top_k: int,
) -> tuple[
    tuple[tuple[float, tuple[str, str], Mapping[str, Any]], ...],
    Mapping[tuple[str, str], _DiversificationDecision],
]:
    """Select one real canonical unit per entity before admitting child units."""

    if not fused:
        return (), {}

    indexed = tuple(enumerate(fused))
    groups: dict[
        tuple[str, str, str],
        list[tuple[int, tuple[float, tuple[str, str], Mapping[str, Any]]]],
    ] = {}
    for original_rank, item in indexed:
        row = item[2]
        entity_key = (
            str(row["repository_id"]),
            str(row["generation_id"]),
            str(row["entity_id"]),
        )
        groups.setdefault(entity_key, []).append((original_rank, item))

    representatives: list[
        tuple[
            int,
            tuple[str, str, str],
            tuple[int, tuple[float, tuple[str, str], Mapping[str, Any]]],
        ]
    ] = []
    representative_keys: set[tuple[str, str]] = set()
    representative_reasons: dict[tuple[str, str], str] = {}
    for entity_key, members in groups.items():
        entity_id = entity_key[2]
        generation_id = entity_key[1]
        entity_type = entity_types.get((entity_id, generation_id), "")
        representative = min(
            members,
            key=lambda member: _representative_sort_key(
                member[1],
                entity_type=entity_type,
                exact_by_key=exact_by_key,
            ),
        )
        best_group_rank = min(member[0] for member in members)
        representatives.append((best_group_rank, entity_key, representative))
        representative_keys.add(representative[1][1])
        representative_reasons[representative[1][1]] = _representative_reason(
            representative[1][2],
            entity_type=entity_type,
            member_count=len(members),
        )

    representatives.sort(
        key=lambda item: (
            item[0],
            item[1],
            item[2][0],
            item[2][1][1],
        )
    )
    ordered: list[tuple[float, tuple[str, str], Mapping[str, Any]]] = [
        representative[1] for _, _, representative in representatives
    ]
    ordered.extend(item for _, item in indexed if item[1] not in representative_keys)
    selected = tuple(ordered[:top_k])
    diversified_ranks = {item[1]: rank for rank, item in enumerate(selected)}

    decisions: dict[tuple[str, str], _DiversificationDecision] = {}
    original_ranks = {item[1]: rank for rank, item in indexed}
    for _, item in indexed:
        key = item[1]
        representative = key in representative_keys
        diversified_rank = diversified_ranks.get(key)
        if representative:
            reason = representative_reasons[key]
            if diversified_rank is None:
                reason += "; distinct-entity representative pruned by top_k"
            else:
                reason += "; selected in the distinct-entity pass"
        elif diversified_rank is None:
            reason = (
                "same-entity child deferred by top_k until after all "
                "distinct-entity representatives"
            )
        else:
            reason = "same-entity child appended only after all distinct-entity representatives"
        decisions[key] = _DiversificationDecision(
            original_fused_rank=original_ranks[key],
            diversified_rank=diversified_rank,
            representative=representative,
            reason=reason,
        )
    return selected, decisions


def _representative_sort_key(
    item: tuple[float, tuple[str, str], Mapping[str, Any]],
    *,
    entity_type: str,
    exact_by_key: Mapping[tuple[str, str], _RankedHit],
) -> tuple[Any, ...]:
    fused_score, key, row = item
    exact = exact_by_key.get(key)
    context_ref = row.get("context_ref")
    structural_path = ""
    if isinstance(context_ref, Mapping):
        structural_path = str(context_ref.get("structural_path") or "")
    start_line = _optional_int(row.get("start_line"))
    end_line = _optional_int(row.get("end_line"))
    span = 0
    if start_line != 2**31 - 1 and end_line != 2**31 - 1:
        span = max(0, end_line - start_line)
    return (
        _canonical_unit_tier(row, entity_type),
        exact is None,
        exact.raw_rank if exact is not None else 10**9,
        -fused_score,
        -span,
        structural_path,
        start_line,
        end_line,
        int(row.get("ordinal") or 0),
        str(row.get("unit_type") or ""),
        str(row.get("ast_node_type") or ""),
        str(row["id"]),
        str(row["generation_id"]),
    )


def _canonical_unit_tier(row: Mapping[str, Any], entity_type: str) -> int:
    unit_type = str(row.get("unit_type") or "")
    ast_node_type = str(row.get("ast_node_type") or "").casefold()
    if entity_type == "CodeSymbol":
        if unit_type == "symbol.ast_block" and (
            "definition" in ast_node_type
            or "declaration" in ast_node_type
            or ast_node_type
            in {
                "class_specifier",
                "function_item",
                "impl_item",
                "method_definition",
                "trait_item",
            }
        ):
            return 0
        if unit_type == "symbol.ast_block":
            return 1
        return 2
    if entity_type == "FileVersion":
        return 0 if unit_type == "file.surface" else 1
    return 0


def _representative_reason(
    row: Mapping[str, Any],
    *,
    entity_type: str,
    member_count: int,
) -> str:
    child_count = max(0, member_count - 1)
    unit_type = str(row.get("unit_type") or "unknown")
    ast_node_type = str(row.get("ast_node_type") or "unknown")
    if entity_type == "CodeSymbol" and _canonical_unit_tier(row, entity_type) == 0:
        choice = "canonical CodeSymbol symbol.ast_block definition span"
    elif entity_type == "FileVersion" and unit_type == "file.surface":
        choice = "canonical FileVersion file.surface"
    else:
        choice = f"best available {entity_type or 'entity'} unit {unit_type}/{ast_node_type}"
    return f"{choice}; diversified ahead of {child_count} same-entity child unit(s)"


def _hit_traces(
    exact_hits: Sequence[_RankedHit],
    sparse_hits: Sequence[_RankedHit],
    diversification: Mapping[tuple[str, str], _DiversificationDecision],
) -> tuple[CodeChannelHitTrace, ...]:
    traces = []
    for channel, hits in (
        (CodeRetrievalChannel.EXACT, exact_hits),
        (CodeRetrievalChannel.SPARSE, sparse_hits),
    ):
        for hit in hits:
            decision = diversification.get(hit.key)
            traces.append(
                CodeChannelHitTrace(
                    retrieval_unit_id=str(hit.row["id"]),
                    generation_id=str(hit.row["generation_id"]),
                    channel=channel,
                    raw_rank=hit.raw_rank,
                    raw_score=hit.raw_score,
                    matched_fields=hit.matched_fields,
                    locator=str(hit.row.get("path") or hit.row["id"]),
                    explanation=hit.explanation,
                    entity_id=str(hit.row["entity_id"]),
                    original_fused_rank=(
                        decision.original_fused_rank if decision is not None else None
                    ),
                    diversified_rank=(decision.diversified_rank if decision is not None else None),
                    representative=decision.representative if decision is not None else False,
                    diversification_reason=decision.reason if decision is not None else "",
                )
            )
    return tuple(traces)


def _locator_traces(locators: Sequence[_Locator]) -> tuple[CodeLocatorTrace, ...]:
    traces = []
    for locator in locators:
        unsupported_uri = (
            locator.kind is CodeLocatorKind.URI
            and not locator.path
            and not locator.value.startswith("code-unit://")
        )
        if locator.kind is CodeLocatorKind.TAG:
            explanation = "tag resolution is unavailable in the current Code V2 schema"
        elif unsupported_uri:
            explanation = (
                "entity-only Code URI is unavailable; exact retrieval falls back "
                "to lower-priority parsed locators"
            )
        else:
            explanation = locator.explanation
        traces.append(
            CodeLocatorTrace(
                kind=locator.kind,
                value=_single_line(locator.value),
                priority=locator.priority,
                available=locator.kind is not CodeLocatorKind.TAG and not unsupported_uri,
                explanation=explanation,
            )
        )
    return tuple(traces)


def _index_version(scopes: Sequence[_GenerationScope]) -> str:
    versions = sorted({str(scope.publication.get("sparse") or "") for scope in scopes})
    if not versions:
        return RETRIEVER_VERSION
    digest = hashlib.sha256("\0".join(versions).encode()).hexdigest()[:16]
    return f"{RETRIEVER_VERSION}:sparse-set-{digest}"


def _watermark(scopes: Sequence[_GenerationScope]) -> str:
    if not scopes:
        return "code-v2:unresolved"
    payload = "\0".join(
        f"{scope.repository_id}@{scope.commit_sha}#{scope.generation_id}"
        for scope in sorted(scopes, key=_scope_sort_key)
    )
    return f"code-v2-watermark://sha256/{hashlib.sha256(payload.encode()).hexdigest()}"


def _scope_labels(scopes: Sequence[_GenerationScope]) -> tuple[str, ...]:
    return tuple(
        f"{scope.repository_id}@{scope.commit_sha}#{scope.generation_id}"
        for scope in sorted(scopes, key=_scope_sort_key)
    )


def _scope_sort_key(scope: _GenerationScope) -> tuple[str, str]:
    return (scope.repository_id, scope.generation_id)


def _normalize_path(value: str) -> str:
    path = unicodedata.normalize("NFC", value).replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path


def _optional_int(value: object) -> int:
    return int(value) if value is not None else 2**31 - 1


def _elapsed_ms(started: float) -> float:
    return float(max(0.0, round((time.perf_counter() - started) * 1000.0, 6)))


def _single_line(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    cleaned = "".join(
        " " if character.isspace() or unicodedata.category(character).startswith("C") else character
        for character in normalized
    )
    return " ".join(cleaned.split())


def _safe_error_message(prefix: str, error: Exception) -> str:
    detail = _single_line(str(error))
    message = f"{prefix}: {type(error).__name__}"
    if detail:
        message += f": {detail}"
    return message[:4_000]


__all__ = [
    "CodeChannelHitTrace",
    "CodeExactSparseRetriever",
    "CodeExactSparseSearchResult",
    "CodeExactSparseTrace",
    "CodeLocatorKind",
    "CodeLocatorTrace",
    "CodeV2RetrievalStore",
    "FUSION_POLICY",
    "RETRIEVER_VERSION",
    "normalize_code_sparse_query",
]
