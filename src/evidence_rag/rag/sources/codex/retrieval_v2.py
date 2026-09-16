"""Task-profiled Codex retrieval, local dense cache, graph expansion, and rerank."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import canonical_sha256
from .units_v2 import (
    CodexEvidenceLevel,
    CodexRetrievalPublicationV2,
    CodexRetrievalUnitV2,
    CodexTemporalEdgeType,
    CodexUnitRole,
)

CODEX_QUERY_PROFILE_VERSION = "codex-query-profile-v2"
CODEX_DENSE_PROFILE_VERSION = "codex-local-dense-profile-v2"
CODEX_RETRIEVER_VERSION = "codex-retriever-v2"
CODEX_RERANKER_VERSION = "codex-source-reranker-v2"
CODEX_CALIBRATION_VERSION = "codex-score-calibration-v2"

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*|[0-9]+")
_PATH_RE = re.compile(r"\b(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\b")
_SECRET_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|(?i:(?:api[_-]?key|access[_-]?token|password|secret)"
    r"\s*[:=]\s*\S{8,}))"
)
_ABSOLUTE_RE = re.compile(
    r"(?:^|\s)(?:/(?:Users|home|private|var|tmp|etc)(?:/|\b)|"
    r"[A-Za-z]:[\\/]|[\\/]{2}[^\\/\s]+[\\/][^\\/\s]+)",
    re.IGNORECASE,
)


class _FrozenRetrieval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CodexQueryTask(StrEnum):
    PROCESS = "process"
    RATIONALE = "rationale"
    VALIDATION = "validation"
    FAILURE_RETRY = "failure_retry"


class CodexCandidateChannel(StrEnum):
    EXACT = "exact"
    SPARSE = "sparse"
    DENSE = "dense"
    GRAPH = "graph"


class CodexDensePurpose(StrEnum):
    GOAL = "goal"
    RATIONALE = "rationale"
    EPISODE = "episode"
    FAILURE = "failure"
    CHANGE = "change"


class CodexCandidateNecessity(StrEnum):
    REQUIRED = "required"
    SUPPORTING = "supporting"
    INCIDENTAL = "incidental"


_ROLE_QUOTAS: dict[CodexQueryTask, tuple[tuple[CodexUnitRole, int], ...]] = {
    CodexQueryTask.PROCESS: (
        (CodexUnitRole.GOAL, 2),
        (CodexUnitRole.ACTION, 4),
        (CodexUnitRole.CHANGE, 3),
        (CodexUnitRole.VALIDATION, 3),
        (CodexUnitRole.OUTCOME, 2),
    ),
    CodexQueryTask.RATIONALE: (
        (CodexUnitRole.GOAL, 2),
        (CodexUnitRole.DECISION, 4),
        (CodexUnitRole.PLAN, 3),
        (CodexUnitRole.ACTION, 2),
        (CodexUnitRole.OUTCOME, 2),
    ),
    CodexQueryTask.VALIDATION: (
        (CodexUnitRole.VALIDATION, 6),
        (CodexUnitRole.CHANGE, 3),
        (CodexUnitRole.ACTION, 3),
        (CodexUnitRole.FAILURE, 3),
    ),
    CodexQueryTask.FAILURE_RETRY: (
        (CodexUnitRole.FAILURE, 6),
        (CodexUnitRole.ACTION, 5),
        (CodexUnitRole.VALIDATION, 3),
        (CodexUnitRole.CHANGE, 2),
    ),
}


class CodexQueryProfileV2(_FrozenRetrieval):
    task: CodexQueryTask
    query_sha256: str
    normalized_tokens: tuple[str, ...]
    exact_paths: tuple[str, ...]
    exact_identifiers: tuple[str, ...]
    exact_commands: tuple[str, ...]
    exact_tools: tuple[str, ...]
    exact_frameworks: tuple[str, ...]
    exact_thread_ids: tuple[str, ...]
    desired_states: tuple[str, ...]
    order_intents: tuple[str, ...]
    role_quotas: tuple[tuple[CodexUnitRole, int], ...]
    candidate_k: int = Field(ge=8, le=256)
    final_k: int = Field(ge=1, le=64)
    graph_depth: int = Field(ge=0, le=3)
    profile_sha256: str
    version: str = CODEX_QUERY_PROFILE_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexQueryProfileV2:
        if self.normalized_tokens != tuple(sorted(set(self.normalized_tokens))):
            raise ValueError("query tokens must be sorted and unique")
        if self.role_quotas != _ROLE_QUOTAS[self.task]:
            raise ValueError("query role quotas are not the frozen task profile")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"profile_sha256"}))
        if self.profile_sha256 != expected:
            raise ValueError("query profile identity mismatch")
        return self


class CodexDenseProfileV2(_FrozenRetrieval):
    profile_id: str
    purpose: CodexDensePurpose
    dimensions: int = Field(default=64, ge=16, le=1_024)
    algorithm: str = "local-hash-signed-bow-v2"
    tokenizer: str = "codex-identifier-tokenizer-v2"
    remote_execution: bool = False
    profile_sha256: str
    version: str = CODEX_DENSE_PROFILE_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexDenseProfileV2:
        if self.remote_execution:
            raise ValueError("Codex V2 dense profile must be local/offline")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"profile_id", "profile_sha256"})
        )
        expected_id = "codexdense-" + expected.removeprefix("sha256:")[:24]
        if self.profile_sha256 != expected or self.profile_id != expected_id:
            raise ValueError("dense profile identity mismatch")
        return self


class CodexDenseCacheEntryV2(_FrozenRetrieval):
    cache_id: str
    project_id: str
    source_id: str
    generation_id: str
    acl_ref: str
    unit_id: str
    profile_sha256: str
    content_sha256: str
    vector: tuple[float, ...] = Field(min_length=16, max_length=1_024)
    vector_sha256: str
    version: str = CODEX_DENSE_PROFILE_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexDenseCacheEntryV2:
        if any(not math.isfinite(value) for value in self.vector):
            raise ValueError("dense cache vector must be finite")
        vector_sha = canonical_sha256(self.vector)
        expected = "codexvec-" + canonical_sha256(
            {
                "project_id": self.project_id,
                "source_id": self.source_id,
                "generation_id": self.generation_id,
                "acl_ref": self.acl_ref,
                "unit_id": self.unit_id,
                "profile_sha256": self.profile_sha256,
                "content_sha256": self.content_sha256,
                "vector_sha256": vector_sha,
            }
        ).removeprefix("sha256:")
        if self.vector_sha256 != vector_sha or self.cache_id != expected:
            raise ValueError("dense cache entry identity mismatch")
        return self


class CodexRetrievalCandidateV2(_FrozenRetrieval):
    unit_id: str
    role: CodexUnitRole
    episode_id: str | None
    channels: tuple[CodexCandidateChannel, ...] = Field(min_length=1)
    exact_score: float = Field(ge=0, le=1)
    sparse_score: float = Field(ge=0, le=1)
    dense_score: float = Field(ge=-1, le=1)
    graph_score: float = Field(ge=0, le=1)
    fused_score: float = Field(ge=0)
    rerank_score: float
    relevance_probability: float = Field(ge=0, le=1)
    necessity: CodexCandidateNecessity
    base_rank: int = Field(ge=1)
    rank_delta: int
    rank: int = Field(ge=1)
    hard_negative_reasons: tuple[str, ...]
    graph_path: tuple[str, ...]
    scope_proof_sha256: str
    trace_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> CodexRetrievalCandidateV2:
        if self.channels != tuple(sorted(set(self.channels), key=str)):
            raise ValueError("candidate channels must be sorted and unique")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"trace_sha256"}))
        if self.trace_sha256 != expected:
            raise ValueError("candidate trace identity mismatch")
        return self


class CodexRetrievalResultV2(_FrozenRetrieval):
    task: CodexQueryTask
    query_profile_sha256: str
    dense_profile_sha256: str
    calibration_sha256: str | None
    publication_sha256: str
    enabled_channels: tuple[CodexCandidateChannel, ...] = Field(min_length=1)
    candidates: tuple[CodexRetrievalCandidateV2, ...]
    candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    adaptive_candidate_k: int = Field(ge=8, le=256)
    result_sha256: str
    retriever_version: str = CODEX_RETRIEVER_VERSION
    reranker_version: str = CODEX_RERANKER_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexRetrievalResultV2:
        if self.enabled_channels != tuple(sorted(set(self.enabled_channels), key=str)):
            raise ValueError("enabled retrieval channels must be sorted and unique")
        if self.selected_count != len(self.candidates):
            raise ValueError("selected candidate count mismatch")
        if tuple(item.rank for item in self.candidates) != tuple(
            range(1, len(self.candidates) + 1)
        ):
            raise ValueError("candidate ranks are not contiguous")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"result_sha256"}))
        if self.result_sha256 != expected:
            raise ValueError("retrieval result digest mismatch")
        return self


class CodexCalibrationPointV2(_FrozenRetrieval):
    raw_score: float
    relevance: int = Field(ge=0, le=2)


class CodexCalibrationArtifactV2(_FrozenRetrieval):
    task: CodexQueryTask
    points: tuple[CodexCalibrationPointV2, ...] = Field(min_length=4)
    threshold: float
    brier_score: float = Field(ge=0)
    artifact_sha256: str
    version: str = CODEX_CALIBRATION_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexCalibrationArtifactV2:
        if tuple((item.raw_score, item.relevance) for item in self.points) != tuple(
            sorted((item.raw_score, item.relevance) for item in self.points)
        ):
            raise ValueError("calibration points must be deterministically sorted")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"artifact_sha256"}))
        if self.artifact_sha256 != expected:
            raise ValueError("calibration artifact identity mismatch")
        return self


def _safe_query(query: str) -> str:
    value = unicodedata.normalize("NFC", query)
    if (
        not value
        or value != value.strip()
        or len(value) > 4_096
        or _SECRET_RE.search(value)
        or _ABSOLUTE_RE.search(value)
        or any(ord(character) < 32 and character not in "\t\n" for character in value)
    ):
        raise ValueError("query is unsafe or non-canonical")
    return value


def build_codex_query_profile_v2(
    query: str,
    *,
    task: CodexQueryTask | str,
    final_k: int = 12,
) -> CodexQueryProfileV2:
    value = _safe_query(query)
    task_value = CodexQueryTask(task)
    tokens = tuple(sorted(set(token.casefold() for token in _TOKEN_RE.findall(value))))
    paths = tuple(sorted(set(_PATH_RE.findall(value))))
    commands = tuple(
        sorted(
            token
            for token in tokens
            if token in {"pytest", "ruff", "mypy", "git", "uv", "python", "node", "npm"}
        )
    )
    tools = tuple(
        sorted(
            token
            for token in tokens
            if token
            in {
                "bash",
                "browser",
                "computer",
                "pytest",
                "python",
                "shell",
                "terminal",
                "tool",
            }
        )
    )
    frameworks = tuple(
        sorted(
            token
            for token in tokens
            if token
            in {
                "django",
                "fastapi",
                "flask",
                "next.js",
                "nextjs",
                "pydantic",
                "pytest",
                "react",
                "sqlalchemy",
                "swiftui",
            }
        )
    )
    thread_ids = tuple(sorted(token for token in tokens if token.startswith("thread-")))
    desired_states = tuple(
        sorted(
            set(tokens)
            & {
                "applied",
                "cancelled",
                "completed",
                "failed",
                "invoked",
                "passed",
                "timeout",
                "unknown",
            }
        )
    )
    order_intents = tuple(
        value for value in ("first", "latest", "before", "after") if value in tokens
    )
    identifiers = tuple(
        sorted(
            token
            for token in tokens
            if (
                "_" in token
                or "." in token
                or ":" in token
                or token.startswith(("class", "def", "test_"))
            )
            and token not in paths
        )
    )
    candidate_k = min(256, max(32, final_k * 6 + len(paths) * 8 + len(identifiers) * 2))
    graph_depth = 2 if task_value in {CodexQueryTask.PROCESS, CodexQueryTask.FAILURE_RETRY} else 1
    payload = {
        "task": task_value,
        "query_sha256": canonical_sha256(value),
        "normalized_tokens": tokens,
        "exact_paths": paths,
        "exact_identifiers": identifiers,
        "exact_commands": commands,
        "exact_tools": tools,
        "exact_frameworks": frameworks,
        "exact_thread_ids": thread_ids,
        "desired_states": desired_states,
        "order_intents": order_intents,
        "role_quotas": _ROLE_QUOTAS[task_value],
        "candidate_k": candidate_k,
        "final_k": final_k,
        "graph_depth": graph_depth,
        "version": CODEX_QUERY_PROFILE_VERSION,
    }
    return CodexQueryProfileV2(
        **payload,
        profile_sha256=canonical_sha256(
            {
                key: (
                    [[str(role), count] for role, count in value] if key == "role_quotas" else value
                )
                for key, value in payload.items()
            }
        ),
    )


def build_codex_dense_profile_v2(
    *,
    purpose: CodexDensePurpose | str = CodexDensePurpose.EPISODE,
    dimensions: int = 64,
) -> CodexDenseProfileV2:
    payload = {
        "purpose": CodexDensePurpose(purpose),
        "dimensions": dimensions,
        "algorithm": "local-hash-signed-bow-v2",
        "tokenizer": "codex-identifier-tokenizer-v2",
        "remote_execution": False,
        "version": CODEX_DENSE_PROFILE_VERSION,
    }
    digest = canonical_sha256(payload)
    return CodexDenseProfileV2(
        profile_id="codexdense-" + digest.removeprefix("sha256:")[:24],
        profile_sha256=digest,
        **payload,
    )


class LocalCodexDenseCacheV2:
    """Content-addressed local cache; entries can also be persisted by CodexV2Store."""

    def __init__(
        self,
        profile: CodexDenseProfileV2,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
        acl_ref: str,
    ) -> None:
        self.profile = profile
        self.project_id = project_id
        self.source_id = source_id
        self.generation_id = generation_id
        self.acl_ref = acl_ref
        self._entries: dict[tuple[str, str, str], CodexDenseCacheEntryV2] = {}

    def encode(self, content: str, *, unit_id: str) -> CodexDenseCacheEntryV2:
        content_sha = canonical_sha256(content)
        key = (self.profile.profile_sha256, content_sha, unit_id)
        if key in self._entries:
            return self._entries[key]
        vector = [0.0] * self.profile.dimensions
        for token in _TOKEN_RE.findall(content.casefold()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.profile.dimensions
            sign = -1.0 if digest[4] & 1 else 1.0
            vector[index] += sign
        norm = math.sqrt(sum(item * item for item in vector))
        if norm:
            vector = [item / norm for item in vector]
        values = tuple(vector)
        vector_sha = canonical_sha256(values)
        entry = CodexDenseCacheEntryV2(
            cache_id="codexvec-"
            + canonical_sha256(
                {
                    "project_id": self.project_id,
                    "source_id": self.source_id,
                    "generation_id": self.generation_id,
                    "acl_ref": self.acl_ref,
                    "unit_id": unit_id,
                    "profile_sha256": self.profile.profile_sha256,
                    "content_sha256": content_sha,
                    "vector_sha256": vector_sha,
                }
            ).removeprefix("sha256:"),
            project_id=self.project_id,
            source_id=self.source_id,
            generation_id=self.generation_id,
            acl_ref=self.acl_ref,
            unit_id=unit_id,
            profile_sha256=self.profile.profile_sha256,
            content_sha256=content_sha,
            vector=values,
            vector_sha256=vector_sha,
        )
        self._entries[key] = entry
        return entry

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> tuple[CodexDenseCacheEntryV2, ...]:
        return tuple(sorted(self._entries.values(), key=lambda item: item.cache_id))


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise ValueError("dense vectors use different profiles")
    return sum(a * b for a, b in zip(left, right, strict=True))


def _exact_score(unit: CodexRetrievalUnitV2, profile: CodexQueryProfileV2) -> float:
    text_tokens = set(_TOKEN_RE.findall(unit.search_text.casefold()))
    argv = {value.casefold() for value in unit.command_argv}
    targets = {value.casefold() for value in unit.targets}
    tools = {value.casefold() for value in unit.tool_names}
    checks: list[bool] = []
    checks.extend(path.casefold() in targets for path in profile.exact_paths)
    checks.extend(identifier.casefold() in text_tokens for identifier in profile.exact_identifiers)
    checks.extend(command.casefold() in argv for command in profile.exact_commands)
    checks.extend(tool.casefold() in tools for tool in profile.exact_tools)
    checks.extend(framework.casefold() in text_tokens for framework in profile.exact_frameworks)
    checks.extend(thread_id == unit.thread_id.casefold() for thread_id in profile.exact_thread_ids)
    if not checks:
        return 0.0
    return sum(checks) / len(checks)


def _sparse_scores(
    units: tuple[CodexRetrievalUnitV2, ...],
    profile: CodexQueryProfileV2,
) -> dict[str, float]:
    if not profile.normalized_tokens:
        return {}
    documents = {
        unit.unit_id: Counter(token.casefold() for token in _TOKEN_RE.findall(unit.search_text))
        for unit in units
    }
    document_frequency = Counter(token for counts in documents.values() for token in set(counts))
    raw: dict[str, float] = {}
    unit_by_id = {unit.unit_id: unit for unit in units}
    for unit_id, counts in documents.items():
        score = 0.0
        length = max(1, sum(counts.values()))
        for token in profile.normalized_tokens:
            tf = counts[token] / length
            idf = math.log((len(units) + 1) / (document_frequency[token] + 1)) + 1.0
            score += tf * idf
        unit = unit_by_id[unit_id]
        if unit.state in profile.desired_states:
            score += 0.5
        argv = {value.casefold() for value in unit.command_argv}
        tools = {value.casefold() for value in unit.tool_names}
        if any(command in argv for command in profile.exact_commands):
            score += 0.5
        if any(tool in tools for tool in profile.exact_tools):
            score += 0.35
        raw[unit_id] = score
    maximum = max(raw.values(), default=0.0)
    return {unit_id: (score / maximum if maximum else 0.0) for unit_id, score in raw.items()}


def _graph_scores(
    publication: CodexRetrievalPublicationV2,
    seed_ids: set[str],
    *,
    depth: int,
) -> tuple[dict[str, float], dict[str, tuple[str, ...]]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in publication.edges:
        if edge.edge_type in {
            CodexTemporalEdgeType.FOLLOWS,
            CodexTemporalEdgeType.EXECUTES,
            CodexTemporalEdgeType.OUTPUT_OF,
            CodexTemporalEdgeType.APPLIES_CHANGE,
            CodexTemporalEdgeType.VALIDATES,
            CodexTemporalEdgeType.RETRIES,
            CodexTemporalEdgeType.SUPERSEDES,
        }:
            adjacency[edge.from_unit_id].add(edge.to_unit_id)
            adjacency[edge.to_unit_id].add(edge.from_unit_id)
    scores: dict[str, float] = {}
    paths: dict[str, tuple[str, ...]] = {}
    frontier = set(seed_ids)
    visited = set(seed_ids)
    frontier_paths = {unit_id: (unit_id,) for unit_id in seed_ids}
    for level in range(1, depth + 1):
        next_frontier: set[str] = set()
        for unit_id in sorted(frontier):
            for neighbor in sorted(adjacency.get(unit_id, ())):
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                next_frontier.add(neighbor)
                scores[neighbor] = max(scores.get(neighbor, 0.0), 1.0 / (level + 1))
                paths[neighbor] = (*frontier_paths[unit_id], neighbor)
        frontier = next_frontier
        frontier_paths = {unit_id: paths[unit_id] for unit_id in frontier}
    return scores, paths


def _role_weight(task: CodexQueryTask, role: CodexUnitRole) -> float:
    preferred = dict(_ROLE_QUOTAS[task])
    return min(0.35, preferred.get(role, 0) * 0.05)


def dense_purpose_for_task_v2(
    task: CodexQueryTask | str,
) -> CodexDensePurpose:
    return {
        CodexQueryTask.PROCESS: CodexDensePurpose.EPISODE,
        CodexQueryTask.RATIONALE: CodexDensePurpose.RATIONALE,
        CodexQueryTask.VALIDATION: CodexDensePurpose.CHANGE,
        CodexQueryTask.FAILURE_RETRY: CodexDensePurpose.FAILURE,
    }[CodexQueryTask(task)]


def _relevance_probability(
    score: float,
    calibration: CodexCalibrationArtifactV2 | None,
) -> float:
    if calibration is None:
        return 1.0 / (1.0 + math.exp(-4.0 * (score - 0.35)))
    nearest = sorted(
        calibration.points,
        key=lambda item: (abs(item.raw_score - score), item.raw_score, item.relevance),
    )[: min(6, len(calibration.points))]
    weighted = [
        (1.0 / (abs(item.raw_score - score) + 1e-9), item.relevance / 2.0) for item in nearest
    ]
    denominator = sum(weight for weight, _ in weighted)
    return (
        sum(weight * relevance for weight, relevance in weighted) / denominator
        if denominator
        else 0.0
    )


def retrieve_codex_v2(
    publication: CodexRetrievalPublicationV2,
    query: str,
    *,
    task: CodexQueryTask | str,
    dense_profile: CodexDenseProfileV2 | None = None,
    cache: LocalCodexDenseCacheV2 | None = None,
    calibration: CodexCalibrationArtifactV2 | None = None,
    final_k: int = 12,
    enabled_channels: frozenset[CodexCandidateChannel] | None = None,
) -> CodexRetrievalResultV2:
    publication = CodexRetrievalPublicationV2.model_validate(
        publication.model_dump(mode="python", round_trip=True)
    )
    profile = build_codex_query_profile_v2(query, task=task, final_k=final_k)
    task_value = CodexQueryTask(task)
    purpose = dense_purpose_for_task_v2(task_value)
    dense = dense_profile or build_codex_dense_profile_v2(purpose=purpose)
    dense_cache = cache or LocalCodexDenseCacheV2(
        dense,
        project_id=publication.project_id,
        source_id=publication.source_id,
        generation_id=publication.generation_id,
        acl_ref=publication.acl_ref,
    )
    if dense.purpose is not purpose:
        raise ValueError("dense profile purpose does not match the Codex query task")
    if dense_cache.profile != dense:
        raise ValueError("dense cache does not match the requested profile")
    if (
        dense_cache.project_id,
        dense_cache.source_id,
        dense_cache.generation_id,
        dense_cache.acl_ref,
    ) != (
        publication.project_id,
        publication.source_id,
        publication.generation_id,
        publication.acl_ref,
    ):
        raise ValueError("dense cache scope does not match the publication")
    if calibration is not None:
        calibration = CodexCalibrationArtifactV2.model_validate(
            calibration.model_dump(mode="python", round_trip=True)
        )
        if calibration.task is not task_value:
            raise ValueError("calibration artifact does not match the query task")
    enabled = (
        frozenset(CodexCandidateChannel)
        if enabled_channels is None
        else frozenset(enabled_channels)
    )
    if not enabled:
        raise ValueError("at least one Codex retrieval channel must be enabled")
    units = publication.units
    sparse = _sparse_scores(units, profile) if CodexCandidateChannel.SPARSE in enabled else {}
    query_vector = (
        dense_cache.encode(
            query,
            unit_id="codexquery-" + profile.query_sha256.removeprefix("sha256:"),
        ).vector
        if CodexCandidateChannel.DENSE in enabled
        else ()
    )
    exact: dict[str, float] = {}
    dense_scores: dict[str, float] = {}
    for unit in units:
        exact[unit.unit_id] = (
            _exact_score(unit, profile) if CodexCandidateChannel.EXACT in enabled else 0.0
        )
        dense_scores[unit.unit_id] = (
            _cosine(
                query_vector,
                dense_cache.encode(unit.search_text, unit_id=unit.unit_id).vector,
            )
            if CodexCandidateChannel.DENSE in enabled
            else 0.0
        )
    seed_ids = {
        unit.unit_id
        for unit in sorted(
            units,
            key=lambda item: (
                -(
                    exact[item.unit_id] * 0.45
                    + sparse.get(item.unit_id, 0.0) * 0.35
                    + max(0.0, dense_scores[item.unit_id]) * 0.20
                ),
                item.unit_id,
            ),
        )[: max(4, profile.final_k)]
        if exact[unit.unit_id] > 0 or sparse.get(unit.unit_id, 0.0) > 0
    }
    graph, graph_paths = (
        _graph_scores(publication, seed_ids, depth=profile.graph_depth)
        if CodexCandidateChannel.GRAPH in enabled
        else ({}, {})
    )
    channel_scores = {
        "exact": exact,
        "sparse": sparse,
        "dense": dense_scores,
        "graph": graph,
    }
    channel_ranks: dict[str, dict[str, int]] = {}
    for name, score_map in channel_scores.items():
        ranked = sorted(
            (
                (unit_id, score)
                for unit_id, score in score_map.items()
                if score > 0 and CodexCandidateChannel(name) in enabled
            ),
            key=lambda item: (-item[1], item[0]),
        )
        channel_ranks[name] = {unit_id: rank for rank, (unit_id, _) in enumerate(ranked, start=1)}
    weights = {"exact": 0.38, "sparse": 0.28, "dense": 0.20, "graph": 0.14}
    maximum_rrf = sum(
        weight / 61 for name, weight in weights.items() if CodexCandidateChannel(name) in enabled
    )
    superseded = {
        edge.from_unit_id
        for edge in publication.edges
        if edge.edge_type is CodexTemporalEdgeType.SUPERSEDES
    }
    content_counts = Counter(unit.content_sha256 for unit in units)
    rows: list[tuple[CodexRetrievalUnitV2, dict[str, float], tuple[str, ...], int]] = []
    for unit in units:
        scores = {
            "exact": exact[unit.unit_id],
            "sparse": sparse.get(unit.unit_id, 0.0),
            "dense": dense_scores[unit.unit_id],
            "graph": graph.get(unit.unit_id, 0.0),
        }
        rrf = sum(
            weights[name] / (60 + channel_ranks[name][unit.unit_id])
            for name in weights
            if unit.unit_id in channel_ranks[name]
        )
        fused = rrf / maximum_rrf if maximum_rrf else 0.0
        reasons: list[str] = []
        penalty = 0.0
        if unit.evidence_level is CodexEvidenceLevel.CANDIDATE_ONLY:
            reasons.append("candidate_only")
            penalty += 0.25
        if unit.state in {"unknown", "unlinked", "claim_only", "plan_only"}:
            reasons.append("unverified_state")
            penalty += 0.15
        if unit.state in {"invoked", "command_invoked", "apply_invoked"}:
            reasons.append(
                "validation_without_exit"
                if unit.role is CodexUnitRole.VALIDATION
                else "patch_without_success"
                if unit.role is CodexUnitRole.CHANGE
                else "call_without_result"
            )
            penalty += 0.2
        if unit.state == "target_unknown":
            reasons.append("validation_target_unknown")
            penalty += 0.25
        if unit.unit_id in superseded:
            reasons.append("superseded")
            penalty += 0.2
        if content_counts[unit.content_sha256] > 1:
            reasons.append("duplicate")
            penalty += 0.1
        if unit.search_text.endswith("…"):
            reasons.append("truncated")
            penalty += 0.1
        if profile.task is CodexQueryTask.VALIDATION and unit.role in {
            CodexUnitRole.DECISION,
            CodexUnitRole.PLAN,
        }:
            reasons.append("wrong_role_for_validation")
            penalty += 0.2
        reranked = fused + _role_weight(profile.task, unit.role) - penalty
        scores["fused"] = fused
        scores["reranked"] = reranked
        channels = tuple(
            sorted(
                (
                    CodexCandidateChannel(name)
                    for name in ("exact", "sparse", "dense", "graph")
                    if scores[name] > 0
                ),
                key=str,
            )
        )
        if channels:
            rows.append((unit, scores, tuple(sorted(reasons)), 0))
    base_order = sorted(
        rows,
        key=lambda item: (-item[1]["fused"], item[0].ordinal, item[0].unit_id),
    )
    base_rank_by_unit = {item[0].unit_id: rank for rank, item in enumerate(base_order, start=1)}
    rows = [
        (unit, scores, reasons, base_rank_by_unit[unit.unit_id])
        for unit, scores, reasons, _ in rows
    ]
    adaptive_candidate_k = min(
        profile.candidate_k,
        max(profile.final_k * 2, len(seed_ids) * 4, 8),
    )
    rows.sort(key=lambda item: (-item[1]["reranked"], item[0].ordinal, item[0].unit_id))
    selected: list[CodexRetrievalCandidateV2] = []
    quota_used: Counter[CodexUnitRole] = Counter()
    quotas = dict(profile.role_quotas)
    required_roles = {
        CodexQueryTask.PROCESS: {
            CodexUnitRole.GOAL,
            CodexUnitRole.ACTION,
            CodexUnitRole.CHANGE,
            CodexUnitRole.VALIDATION,
            CodexUnitRole.OUTCOME,
        },
        CodexQueryTask.RATIONALE: {
            CodexUnitRole.GOAL,
            CodexUnitRole.DECISION,
            CodexUnitRole.PLAN,
        },
        CodexQueryTask.VALIDATION: {CodexUnitRole.VALIDATION},
        CodexQueryTask.FAILURE_RETRY: {
            CodexUnitRole.FAILURE,
            CodexUnitRole.ACTION,
        },
    }[profile.task]
    supporting_roles = {role for role, quota in profile.role_quotas if quota > 0}
    for unit, scores, reasons, base_rank in rows[:adaptive_candidate_k]:
        quota = quotas.get(unit.role, profile.final_k)
        if quota_used[unit.role] >= quota:
            continue
        quota_used[unit.role] += 1
        rank = len(selected) + 1
        necessity = (
            CodexCandidateNecessity.REQUIRED
            if unit.role in required_roles
            else CodexCandidateNecessity.SUPPORTING
            if unit.role in supporting_roles
            else CodexCandidateNecessity.INCIDENTAL
        )
        relevance_probability = _relevance_probability(
            scores["reranked"],
            calibration,
        )
        payload: dict[str, Any] = {
            "unit_id": unit.unit_id,
            "role": unit.role,
            "episode_id": unit.episode_id,
            "channels": tuple(
                sorted(
                    (
                        CodexCandidateChannel(name)
                        for name in ("exact", "sparse", "dense", "graph")
                        if scores[name] > 0
                    ),
                    key=str,
                )
            ),
            "exact_score": scores["exact"],
            "sparse_score": scores["sparse"],
            "dense_score": scores["dense"],
            "graph_score": scores["graph"],
            "fused_score": scores["fused"],
            "rerank_score": scores["reranked"],
            "relevance_probability": relevance_probability,
            "necessity": necessity,
            "base_rank": base_rank,
            "rank_delta": base_rank - rank,
            "rank": rank,
            "hard_negative_reasons": reasons,
            "graph_path": graph_paths.get(unit.unit_id, ()),
            "scope_proof_sha256": canonical_sha256(
                {
                    "project_id": unit.project_id,
                    "source_id": unit.source_id,
                    "generation_id": unit.generation_id,
                    "thread_id": unit.thread_id,
                    "acl_ref": unit.acl_ref,
                    "unit_id": unit.unit_id,
                    "content_sha256": unit.content_sha256,
                }
            ),
        }
        selected.append(
            CodexRetrievalCandidateV2(
                **payload,
                trace_sha256=canonical_sha256(payload),
            )
        )
        if len(selected) >= profile.final_k:
            break
    values = {
        "task": profile.task,
        "query_profile_sha256": profile.profile_sha256,
        "dense_profile_sha256": dense.profile_sha256,
        "calibration_sha256": calibration.artifact_sha256 if calibration else None,
        "publication_sha256": publication.publication_sha256,
        "enabled_channels": tuple(sorted(enabled, key=str)),
        "candidates": tuple(selected),
        "candidate_count": len(rows),
        "selected_count": len(selected),
        "adaptive_candidate_k": adaptive_candidate_k,
        "retriever_version": CODEX_RETRIEVER_VERSION,
        "reranker_version": CODEX_RERANKER_VERSION,
    }
    return CodexRetrievalResultV2(
        **values,
        result_sha256=canonical_sha256(
            {
                key: (
                    [item.model_dump(mode="json") for item in value]
                    if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
                    else value
                )
                for key, value in values.items()
            }
        ),
    )


def build_codex_calibration_v2(
    task: CodexQueryTask | str,
    judged_scores: Iterable[tuple[float, int]],
) -> CodexCalibrationArtifactV2:
    points = tuple(
        sorted(
            (
                CodexCalibrationPointV2(raw_score=float(score), relevance=int(relevance))
                for score, relevance in judged_scores
            ),
            key=lambda item: (item.raw_score, item.relevance),
        )
    )
    if len(points) < 4:
        raise ValueError("calibration requires at least four reviewed points")
    task_value = CodexQueryTask(task)
    positive_scores = [item.raw_score for item in points if item.relevance > 0]
    threshold = (
        sum(positive_scores) / len(positive_scores)
        if positive_scores
        else max(item.raw_score for item in points) + 1.0
    )
    minimum = min(item.raw_score for item in points)
    maximum = max(item.raw_score for item in points)
    span = maximum - minimum
    brier = sum(
        (
            ((item.raw_score - minimum) / span if span else 0.5)
            - (1.0 if item.relevance > 0 else 0.0)
        )
        ** 2
        for item in points
    ) / len(points)
    payload = {
        "task": task_value,
        "points": points,
        "threshold": threshold,
        "brier_score": brier,
        "version": CODEX_CALIBRATION_VERSION,
    }
    return CodexCalibrationArtifactV2(
        **payload,
        artifact_sha256=canonical_sha256(
            {
                key: (
                    [item.model_dump(mode="json") for item in value] if key == "points" else value
                )
                for key, value in payload.items()
            }
        ),
    )


__all__ = [
    "CODEX_CALIBRATION_VERSION",
    "CODEX_DENSE_PROFILE_VERSION",
    "CODEX_QUERY_PROFILE_VERSION",
    "CODEX_RERANKER_VERSION",
    "CODEX_RETRIEVER_VERSION",
    "CodexCalibrationArtifactV2",
    "CodexCalibrationPointV2",
    "CodexCandidateChannel",
    "CodexCandidateNecessity",
    "CodexDenseCacheEntryV2",
    "CodexDensePurpose",
    "CodexDenseProfileV2",
    "CodexQueryProfileV2",
    "CodexQueryTask",
    "CodexRetrievalCandidateV2",
    "CodexRetrievalResultV2",
    "LocalCodexDenseCacheV2",
    "build_codex_calibration_v2",
    "build_codex_dense_profile_v2",
    "build_codex_query_profile_v2",
    "dense_purpose_for_task_v2",
    "retrieve_codex_v2",
]
