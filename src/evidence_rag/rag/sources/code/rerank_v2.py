"""Deterministic Code V2 reranking and conservative score calibration.

This module is intentionally read-only.  It consumes the candidates and trace
already returned by :class:`CodeHybridV2Retriever`; it never queries the store
or widens the caller's repository, version, generation, or ACL scope.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from ....models import EvidenceSearchRequest
from .contracts import (
    CodeCandidateRole,
    CodeChannelCompletePruned,
    CodeChannelCompleteWithHits,
    CodeQueryProfile,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeSourceResult,
    CodeVersionAlignment,
)
from .dense_v2 import CodeHybridSearchResult, CodeHybridV2Retriever

DETERMINISTIC_RERANKER_VERSION = "code-deterministic-reranker-v1"
RERANK_MODEL_VERSION = "deterministic-rules-v1"
CALIBRATION_ARTIFACT_VERSION = "code-rerank-calibration-v1"
DEFAULT_INPUT_BUDGET = 50
DEFAULT_RERANK_TIMEOUT_MS = 100.0
DEFAULT_MIN_LABELED_SAMPLES = 30

_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_PATH_RE = re.compile(r"(?<![\w.-])((?:\.{0,2}/)?(?:[\w@+.-]+/)+[\w@+.-]+\.[A-Za-z0-9]{1,12})")
_SPAN_RE = re.compile(r"#L(\d+)(?:-L(\d+))?")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "code",
        "file",
        "find",
        "for",
        "from",
        "function",
        "in",
        "is",
        "method",
        "of",
        "on",
        "or",
        "show",
        "symbol",
        "the",
        "to",
        "where",
        "which",
        "with",
    }
)
_GENERATED_PARTS = frozenset({"generated", "gen", "vendor", "third_party", "node_modules"})


class RerankDeadlineExceeded(TimeoutError):
    """The deterministic rerank budget expired after hybrid retrieval completed."""


class CalibrationArtifactMismatchError(ValueError):
    """A calibration artifact does not match the active retrieval/rerank context."""


@dataclass(frozen=True, slots=True)
class RerankModelCandidate:
    """Availability truth for an optional rerank model; never a benchmark claim."""

    model: str
    locality: str
    availability: str
    benchmark_status: str
    reason: str

    def canonical_snapshot(self) -> dict[str, str]:
        return asdict(self)


RERANK_MODEL_CANDIDATES = (
    RerankModelCandidate(
        model=RERANK_MODEL_VERSION,
        locality="local",
        availability="available",
        benchmark_status="not-run",
        reason="deterministic offline feature baseline; no learned-model quality claim",
    ),
    RerankModelCandidate(
        model="cross-encoder",
        locality="local",
        availability="unavailable",
        benchmark_status="not-run",
        reason="no local cross-encoder artifact or provider is configured",
    ),
)


@dataclass(frozen=True, slots=True)
class EvidenceCard:
    """Frozen evidence copied or derived exclusively from one hybrid response."""

    query: str
    profile: CodeQueryProfile
    profile_version: str
    task: str
    retrieval_unit_id: str
    entity_id: str
    repository_id: str
    entity_type: str
    path: str
    signature: str
    locator: str
    stable_version: str
    source_generation: str
    acl_ref: str
    version_alignment: str
    exact_rank: int | None
    exact_score: float | None
    sparse_rank: int | None
    sparse_score: float | None
    dense_rank: int | None
    dense_score: float | None
    hybrid_rank: int
    hybrid_score: float
    unit_quality: str
    parse_error: bool
    generated: bool
    partial: bool
    matched_fields: tuple[str, ...]
    same_name_match: bool
    path_match: bool
    module_match: bool
    path_conflict: bool
    exact_uri_match: bool
    exact_qualified_match: bool
    exact_path_match: bool
    exact_hard_signal: bool
    canonical_symbol_span: bool
    child_duplicate: bool


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """Named deterministic contributions; values are ranking features, not probabilities."""

    exact_channel: float
    sparse_channel: float
    dense_channel: float
    raw_score_tiebreak: float
    exact_uri_signal: float
    exact_qualified_signal: float
    exact_path_signal: float
    same_name_signal: float
    path_signal: float
    module_signal: float
    canonical_span_signal: float
    hard_negative_penalty: float
    child_duplicate_penalty: float
    parse_error_penalty: float
    generated_penalty: float
    partial_penalty: float
    total: float


@dataclass(frozen=True, slots=True)
class RerankDecision:
    """One auditable accept/reject/rank decision."""

    card: EvidenceCard
    features: FeatureVector
    accepted: bool
    rerank_score: float
    original_rank: int
    reranked_rank: int | None
    explanation_code: str
    negative_reason: str
    necessity_role: CodeCandidateRole
    explanation: str


@dataclass(frozen=True, slots=True)
class CodeRerankTrace:
    query_id: str
    query: str
    profile: CodeQueryProfile
    reranker_version: str
    model_version: str
    input_budget: int
    input_count: int
    output_count: int
    decisions: tuple[RerankDecision, ...]
    fallback_used: bool
    fallback_reason: str
    cross_encoder_availability: Literal["unavailable"] = "unavailable"
    cross_encoder_benchmark_status: Literal["not-run"] = "not-run"


@dataclass(frozen=True, slots=True)
class CodeRerankedSearchResult:
    result: CodeSourceResult
    trace: CodeRerankTrace
    hybrid: CodeHybridSearchResult


def _finite_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _scope_parts(label: str) -> tuple[str, str, str] | None:
    prefix, separator, generation = label.rpartition("#")
    repository, version_separator, version = prefix.rpartition("@")
    if not separator or not version_separator or not repository or not version or not generation:
        return None
    return repository, version, generation


def _path_from_locator(locator: str) -> str:
    base = locator.split("#", 1)[0]
    if base.startswith(("code://", "code-unit://")) and "@" in base:
        remainder = base.rsplit("@", 1)[1]
        if "/" in remainder:
            return remainder.split("/", 1)[1]
    return ""


def _line_span(locator: str) -> int:
    match = _SPAN_RE.search(locator)
    if match is None:
        return -1
    start = int(match.group(1))
    end = int(match.group(2) or start)
    return max(0, end - start)


def _query_paths(query: str, profile: CodeQueryProfile) -> tuple[str, ...]:
    values = [*profile.target_paths, *_PATH_RE.findall(query)]
    return tuple(dict.fromkeys(value.removeprefix("./") for value in values if value))


def _query_identifiers(query: str, profile: CodeQueryProfile) -> tuple[str, ...]:
    values = list(profile.target_identifiers)
    values.extend(
        token
        for token in _IDENTIFIER_RE.findall(query)
        if token.casefold() not in _STOP_WORDS and len(token) > 1
    )
    return tuple(dict.fromkeys(value.casefold() for value in values if value))


def _module_tokens(paths: Sequence[str]) -> frozenset[str]:
    result: set[str] = set()
    for path in paths:
        parts = path.replace("\\", "/").split("/")
        for part in parts[:-1]:
            normalized = part.casefold()
            if normalized not in {"src", "lib", "app", ".", ".."}:
                result.add(normalized)
    return frozenset(result)


def _channel_maps(
    candidate: CodeRetrievalCandidate,
) -> tuple[dict[CodeRetrievalChannel, int], dict[CodeRetrievalChannel, float]]:
    ranks = {entry.channel: entry.rank for entry in candidate.raw_channel_ranks}
    scores = {entry.channel: entry.score for entry in candidate.raw_channel_scores}
    return ranks, scores


def _matched_fields(
    response: CodeHybridSearchResult,
) -> tuple[dict[tuple[str, str], tuple[str, ...]], dict[tuple[str, str], str]]:
    fields: dict[tuple[str, str], set[str]] = {}
    explanations: dict[tuple[str, str], list[str]] = {}
    exact_sparse = response.trace.exact_sparse
    if exact_sparse is not None:
        for hit in exact_sparse.trace.channel_hits:
            key = (hit.generation_id, hit.retrieval_unit_id)
            fields.setdefault(key, set()).update(hit.matched_fields)
            explanations.setdefault(key, []).append(hit.explanation)
    dense = response.trace.dense
    if dense is not None:
        for hit in dense.trace.channel_hits:
            key = (hit.generation_id, hit.retrieval_unit_id)
            explanations.setdefault(key, []).append(hit.explanation)
    field_snapshot = {key: tuple(sorted(value)) for key, value in fields.items()}
    explanation_snapshot = {
        key: " ".join(dict.fromkeys(value)) for key, value in explanations.items()
    }
    return field_snapshot, explanation_snapshot


def _hybrid_hard_signals(response: CodeHybridSearchResult) -> dict[tuple[str, str], bool]:
    generations = {
        candidate.retrieval_unit_id: candidate.source_generation
        for candidate in response.result.candidates
    }
    return {
        (generations.get(hit.retrieval_unit_id, ""), hit.retrieval_unit_id): hit.exact_hard_signal
        for hit in response.trace.channel_hits
    }


def _quality(
    candidate: CodeRetrievalCandidate,
    path: str,
    explanation: str,
) -> tuple[str, bool, bool, bool]:
    haystack = f"{candidate.entity_type} {candidate.retrieval_unit_id} {explanation}".casefold()
    path_parts = {part.casefold() for part in path.replace("\\", "/").split("/")}
    parse_error = "parse_error" in haystack or "parse error" in haystack
    generated = bool(path_parts & _GENERATED_PARTS) or "generated" in haystack
    partial = "partial" in haystack or "chunk" in candidate.entity_type.casefold()
    if parse_error:
        quality = "parse_error"
    elif generated:
        quality = "generated"
    elif partial:
        quality = "partial"
    else:
        quality = "canonical"
    return quality, parse_error, generated, partial


class DeterministicCodeReranker:
    """Explainable, stable rules over an existing hybrid result."""

    version = DETERMINISTIC_RERANKER_VERSION
    model_version = RERANK_MODEL_VERSION

    def rerank(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
        response: CodeHybridSearchResult,
        *,
        top_k: int,
        deadline: float | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> tuple[tuple[CodeRetrievalCandidate, ...], tuple[RerankDecision, ...]]:
        if not isinstance(request, EvidenceSearchRequest):
            raise TypeError("request must be an EvidenceSearchRequest")
        if not isinstance(profile, CodeQueryProfile):
            raise TypeError("profile must be a CodeQueryProfile")
        if not isinstance(response, CodeHybridSearchResult):
            raise TypeError("response must be a CodeHybridSearchResult")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        self._check_deadline(deadline, clock)

        fields_by_key, explanations_by_key = _matched_fields(response)
        hard_signals = _hybrid_hard_signals(response)
        query_paths = _query_paths(request.query, profile)
        query_identifiers = _query_identifiers(request.query, profile)
        query_modules = _module_tokens(query_paths)
        scope_parts = tuple(
            part
            for label in response.trace.generation_scopes
            if (part := _scope_parts(label)) is not None
        )
        allowed_generations = {part[2] for part in scope_parts}
        versions_by_repository: dict[str, set[str]] = {}
        for repository, version, _generation in scope_parts:
            versions_by_repository.setdefault(repository, set()).add(version)

        cards: list[EvidenceCard] = []
        for original_rank, candidate in enumerate(response.result.candidates):
            self._check_deadline(deadline, clock)
            key = (candidate.source_generation, candidate.retrieval_unit_id)
            matched_fields = fields_by_key.get(key, ())
            explanation = explanations_by_key.get(key, "")
            ranks, scores = _channel_maps(candidate)
            path = _path_from_locator(candidate.locator)
            path_folded = path.casefold()
            identity_haystack = " ".join(
                (
                    candidate.entity_id,
                    candidate.retrieval_unit_id,
                    candidate.locator,
                    path.rsplit("/", 1)[-1].rsplit(".", 1)[0],
                )
            ).casefold()
            same_name = any(value in identity_haystack for value in query_identifiers)
            path_match = any(
                path_folded == value.casefold() or path_folded.endswith("/" + value.casefold())
                for value in query_paths
            )
            candidate_modules = _module_tokens((path,))
            module_match = bool(query_modules & candidate_modules)
            path_conflict = bool(query_paths and same_name and not path_match and not module_match)
            locator_base = candidate.locator.split("#", 1)[0].casefold()
            query_folded = request.query.casefold()
            exact_uri = bool(
                CodeRetrievalChannel.EXACT in ranks
                and (candidate.locator.casefold() in query_folded or locator_base in query_folded)
            )
            exact_path = bool(
                CodeRetrievalChannel.EXACT in ranks and (path_match or "path" in matched_fields)
            )
            exact_qualified = bool(
                CodeRetrievalChannel.EXACT in ranks
                and {"qualified_name", "signature", "identifiers"} & set(matched_fields)
            )
            unit_quality, parse_error, generated, partial = _quality(candidate, path, explanation)
            cards.append(
                EvidenceCard(
                    query=request.query,
                    profile=profile,
                    profile_version=profile.profile_version,
                    task=profile.task.value,
                    retrieval_unit_id=candidate.retrieval_unit_id,
                    entity_id=candidate.entity_id,
                    repository_id=candidate.repository_id,
                    entity_type=candidate.entity_type,
                    path=path,
                    signature="",
                    locator=candidate.locator,
                    stable_version=candidate.stable_version,
                    source_generation=candidate.source_generation,
                    acl_ref=candidate.acl_ref,
                    version_alignment=candidate.version_alignment.value,
                    exact_rank=ranks.get(CodeRetrievalChannel.EXACT),
                    exact_score=scores.get(CodeRetrievalChannel.EXACT),
                    sparse_rank=ranks.get(CodeRetrievalChannel.SPARSE),
                    sparse_score=scores.get(CodeRetrievalChannel.SPARSE),
                    dense_rank=ranks.get(CodeRetrievalChannel.DENSE),
                    dense_score=scores.get(CodeRetrievalChannel.DENSE),
                    hybrid_rank=original_rank,
                    hybrid_score=candidate.source_fused_score,
                    unit_quality=unit_quality,
                    parse_error=parse_error,
                    generated=generated,
                    partial=partial,
                    matched_fields=matched_fields,
                    same_name_match=same_name,
                    path_match=path_match,
                    module_match=module_match,
                    path_conflict=path_conflict,
                    exact_uri_match=exact_uri,
                    exact_qualified_match=exact_qualified,
                    exact_path_match=exact_path,
                    exact_hard_signal=hard_signals.get(key, CodeRetrievalChannel.EXACT in ranks),
                    canonical_symbol_span=True,
                    child_duplicate=False,
                )
            )
        cards = self._mark_child_duplicates(cards)

        pending: list[tuple[EvidenceCard, FeatureVector, str, str, CodeCandidateRole, str]] = []
        rejected: dict[int, RerankDecision] = {}
        for card in cards:
            self._check_deadline(deadline, clock)
            negative_reason = self._hard_reject_reason(
                card,
                request,
                allowed_generations=allowed_generations,
                versions_by_repository=versions_by_repository,
            )
            features = self._features(card)
            if negative_reason:
                rejected[card.hybrid_rank] = RerankDecision(
                    card=card,
                    features=features,
                    accepted=False,
                    rerank_score=features.total,
                    original_rank=card.hybrid_rank,
                    reranked_rank=None,
                    explanation_code=f"reject_{negative_reason}",
                    negative_reason=negative_reason,
                    necessity_role=CodeCandidateRole.BACKGROUND,
                    explanation=(
                        "hard reject applied before ranking because candidate provenance "
                        f"failed {negative_reason}"
                    ),
                )
                continue
            explanation_code, role, explanation = self._explanation(card)
            pending.append((card, features, explanation_code, "", role, explanation))

        pending.sort(
            key=lambda item: (
                not item[0].exact_hard_signal,
                -item[1].total,
                item[0].exact_rank if item[0].exact_rank is not None else 10**9,
                item[0].sparse_rank if item[0].sparse_rank is not None else 10**9,
                item[0].dense_rank if item[0].dense_rank is not None else 10**9,
                item[0].hybrid_rank,
                item[0].repository_id,
                item[0].path,
                item[0].retrieval_unit_id,
                item[0].source_generation,
            )
        )
        selected = pending[:top_k]
        selected_candidates: list[CodeRetrievalCandidate] = []
        accepted: dict[int, RerankDecision] = {}
        candidates_by_rank = {
            rank: candidate for rank, candidate in enumerate(response.result.candidates)
        }
        for reranked_rank, (card, features, code, reason, role, explanation) in enumerate(selected):
            candidate = candidates_by_rank[card.hybrid_rank]
            payload = candidate.model_dump(mode="python", round_trip=True)
            payload.update(
                within_source_rank=reranked_rank,
                source_fused_score=max(0.0, round(features.total, 12)),
            )
            selected_candidates.append(CodeRetrievalCandidate.model_validate(payload))
            accepted[card.hybrid_rank] = RerankDecision(
                card=card,
                features=features,
                accepted=True,
                rerank_score=features.total,
                original_rank=card.hybrid_rank,
                reranked_rank=reranked_rank,
                explanation_code=code,
                negative_reason=reason,
                necessity_role=role,
                explanation=explanation,
            )

        not_selected = pending[top_k:]
        for card, features, _code, _reason, _role, _explanation in not_selected:
            rejected[card.hybrid_rank] = RerankDecision(
                card=card,
                features=features,
                accepted=False,
                rerank_score=features.total,
                original_rank=card.hybrid_rank,
                reranked_rank=None,
                explanation_code="output_budget_pruned",
                negative_reason="output_budget",
                necessity_role=CodeCandidateRole.BACKGROUND,
                explanation="candidate was valid but fell outside the deterministic top_k budget",
            )
        decisions = tuple(accepted.get(rank) or rejected[rank] for rank in range(len(cards)))
        return tuple(selected_candidates), decisions

    @staticmethod
    def _check_deadline(
        deadline: float | None,
        clock: Callable[[], float],
    ) -> None:
        if deadline is not None and clock() >= deadline:
            raise RerankDeadlineExceeded("deterministic rerank deadline exceeded")

    @staticmethod
    def _mark_child_duplicates(cards: Sequence[EvidenceCard]) -> list[EvidenceCard]:
        groups: dict[tuple[str, str, str], list[EvidenceCard]] = {}
        for card in cards:
            groups.setdefault(
                (card.repository_id, card.source_generation, card.entity_id), []
            ).append(card)
        result: list[EvidenceCard] = []
        canonical_ids: set[tuple[str, str, str, str]] = set()
        for group_key, group in groups.items():
            canonical = min(
                group,
                key=lambda card: (
                    not card.exact_hard_signal,
                    not card.path_match,
                    card.partial,
                    card.parse_error,
                    -_line_span(card.locator),
                    card.hybrid_rank,
                    card.retrieval_unit_id,
                ),
            )
            canonical_ids.add((*group_key, canonical.retrieval_unit_id))
        for card in cards:
            identity = (
                card.repository_id,
                card.source_generation,
                card.entity_id,
                card.retrieval_unit_id,
            )
            group_size = len(groups[(card.repository_id, card.source_generation, card.entity_id)])
            canonical = identity in canonical_ids
            result.append(
                replace(
                    card,
                    canonical_symbol_span=canonical,
                    child_duplicate=group_size > 1 and not canonical,
                )
            )
        return result

    @staticmethod
    def _hard_reject_reason(
        card: EvidenceCard,
        request: EvidenceSearchRequest,
        *,
        allowed_generations: set[str],
        versions_by_repository: Mapping[str, set[str]],
    ) -> str:
        if card.version_alignment == CodeVersionAlignment.MISMATCH.value:
            return "wrong_version"
        expected_versions = versions_by_repository.get(card.repository_id)
        if expected_versions and card.stable_version not in expected_versions:
            return "wrong_version"
        if request.scope.commit and card.stable_version != request.scope.commit:
            return "wrong_version"
        if allowed_generations and card.source_generation not in allowed_generations:
            return "wrong_generation"
        allowed_acl_refs = set(request.scope.allowed_acl_refs)
        if allowed_acl_refs and card.acl_ref not in allowed_acl_refs:
            return "wrong_acl"
        if request.scope.enforce_acl and not allowed_acl_refs:
            return "wrong_acl"
        return ""

    @staticmethod
    def _features(card: EvidenceCard) -> FeatureVector:
        exact_channel = 6.0 / (card.exact_rank + 1) if card.exact_rank is not None else 0.0
        sparse_channel = 2.0 / (card.sparse_rank + 1) if card.sparse_rank is not None else 0.0
        dense_channel = 1.25 / (card.dense_rank + 1) if card.dense_rank is not None else 0.0
        raw_values = (
            card.exact_score or 0.0,
            card.sparse_score or 0.0,
            card.dense_score or 0.0,
        )
        raw_score_tiebreak = min(
            0.25, sum(math.log1p(max(0.0, value)) for value in raw_values) * 0.01
        )
        exact_uri_signal = 6.0 if card.exact_uri_match else 0.0
        exact_qualified_signal = 3.0 if card.exact_qualified_match else 0.0
        exact_path_signal = 4.0 if card.exact_path_match else 0.0
        same_name_signal = 1.5 if card.same_name_match else 0.0
        path_signal = 2.5 if card.path_match else 0.0
        module_signal = 1.25 if card.module_match else 0.0
        canonical_span_signal = 0.5 if card.canonical_symbol_span else 0.0
        hard_negative_penalty = -5.0 if card.path_conflict else 0.0
        child_duplicate_penalty = -4.0 if card.child_duplicate else 0.0
        parse_error_penalty = -2.0 if card.parse_error else 0.0
        generated_penalty = -1.5 if card.generated else 0.0
        partial_penalty = -1.0 if card.partial else 0.0
        total = math.fsum(
            (
                exact_channel,
                sparse_channel,
                dense_channel,
                raw_score_tiebreak,
                exact_uri_signal,
                exact_qualified_signal,
                exact_path_signal,
                same_name_signal,
                path_signal,
                module_signal,
                canonical_span_signal,
                hard_negative_penalty,
                child_duplicate_penalty,
                parse_error_penalty,
                generated_penalty,
                partial_penalty,
            )
        )
        return FeatureVector(
            exact_channel=exact_channel,
            sparse_channel=sparse_channel,
            dense_channel=dense_channel,
            raw_score_tiebreak=raw_score_tiebreak,
            exact_uri_signal=exact_uri_signal,
            exact_qualified_signal=exact_qualified_signal,
            exact_path_signal=exact_path_signal,
            same_name_signal=same_name_signal,
            path_signal=path_signal,
            module_signal=module_signal,
            canonical_span_signal=canonical_span_signal,
            hard_negative_penalty=hard_negative_penalty,
            child_duplicate_penalty=child_duplicate_penalty,
            parse_error_penalty=parse_error_penalty,
            generated_penalty=generated_penalty,
            partial_penalty=partial_penalty,
            total=total,
        )

    @staticmethod
    def _explanation(
        card: EvidenceCard,
    ) -> tuple[str, CodeCandidateRole, str]:
        if card.child_duplicate:
            return (
                "child_duplicate_suppressed",
                CodeCandidateRole.BACKGROUND,
                "a canonical span for the same entity is preferred over this child duplicate",
            )
        if card.exact_uri_match:
            return (
                "exact_uri_signal",
                CodeCandidateRole.TARGET,
                "the query contains this candidate's governed URI locator",
            )
        if card.exact_path_match:
            return (
                "exact_path_signal",
                CodeCandidateRole.TARGET,
                "the candidate has an exact-channel path match",
            )
        if card.exact_qualified_match:
            return (
                "exact_qualified_signal",
                CodeCandidateRole.TARGET,
                "the exact channel matched a qualified name, signature, or identifier field",
            )
        if card.exact_hard_signal:
            return (
                "exact_hard_signal",
                CodeCandidateRole.TARGET,
                "the existing hybrid trace marks an exact hard signal",
            )
        if card.dense_rank is not None and card.exact_rank is None and card.sparse_rank is None:
            return (
                "dense_only_recovery",
                CodeCandidateRole.TARGET
                if card.same_name_match or card.path_match or card.module_match
                else CodeCandidateRole.BACKGROUND,
                "dense-only evidence is retained as recovery evidence below exact hard signals",
            )
        if card.path_conflict:
            return (
                "same_name_path_conflict",
                CodeCandidateRole.BACKGROUND,
                "the name overlaps but the requested path/module context conflicts",
            )
        return (
            "deterministic_feature_rank",
            CodeCandidateRole.TARGET
            if card.same_name_match or card.path_match or card.module_match
            else CodeCandidateRole.BACKGROUND,
            "stable channel, locator, name, path, quality, and duplicate features were applied",
        )


def _result_with_candidates(
    base: CodeSourceResult,
    candidates: Sequence[CodeRetrievalCandidate],
) -> CodeSourceResult:
    observed_channels = {
        rank.channel for candidate in candidates for rank in candidate.raw_channel_ranks
    }
    outcomes = []
    for outcome in base.channel_outcomes:
        if (
            isinstance(outcome, CodeChannelCompleteWithHits)
            and outcome.channel not in observed_channels
        ):
            outcomes.append(
                CodeChannelCompletePruned(
                    channel=outcome.channel,
                    hit_count=outcome.hit_count,
                )
            )
        else:
            outcomes.append(outcome)
    candidate_keys = {
        (candidate.entity_id, candidate.retrieval_unit_id) for candidate in candidates
    }
    context_blocks = tuple(
        block
        for block in base.context_blocks
        if (block.entity_id, block.retrieval_unit_id) in candidate_keys
    )
    payload = base.model_dump(mode="python", round_trip=True)
    payload.update(
        candidates=tuple(candidates),
        context_blocks=context_blocks,
        channel_outcomes=tuple(outcomes),
    )
    return CodeSourceResult.model_validate(payload)


class CodeRerankedRetriever:
    """Read-only wrapper that falls back to the original hybrid RRF on rerank failure."""

    def __init__(
        self,
        hybrid_retriever: CodeHybridV2Retriever,
        *,
        reranker: DeterministicCodeReranker | None = None,
        input_budget: int = DEFAULT_INPUT_BUDGET,
        top_k: int | None = None,
        rerank_timeout_ms: float = DEFAULT_RERANK_TIMEOUT_MS,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not callable(getattr(hybrid_retriever, "search_with_trace", None)):
            raise TypeError("hybrid_retriever must expose search_with_trace()")
        if isinstance(input_budget, bool) or not isinstance(input_budget, int):
            raise TypeError("input_budget must be an integer")
        if not 30 <= input_budget <= 60:
            raise ValueError("input_budget must be between 30 and 60")
        if top_k is not None and (
            isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1
        ):
            raise ValueError("top_k must be a positive integer or None")
        timeout = _finite_number(rerank_timeout_ms, "rerank_timeout_ms")
        if timeout <= 0:
            raise ValueError("rerank_timeout_ms must be positive")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.hybrid_retriever = hybrid_retriever
        self.reranker = reranker or DeterministicCodeReranker()
        self.input_budget = input_budget
        self.top_k = top_k
        self.rerank_timeout_ms = timeout
        self.clock = clock

    def search(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
        *,
        deadline: float | None = None,
    ) -> CodeSourceResult:
        return self.search_with_trace(request, profile, deadline=deadline).result

    def search_with_trace(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
        *,
        deadline: float | None = None,
    ) -> CodeRerankedSearchResult:
        if not isinstance(request, EvidenceSearchRequest):
            raise TypeError("request must be an EvidenceSearchRequest")
        query_profile = profile
        if query_profile is None:
            profile_factory = getattr(
                getattr(self.hybrid_retriever, "exact_sparse_retriever", None),
                "profile_for_request",
                None,
            )
            if not callable(profile_factory):
                raise TypeError("profile is required when the hybrid wrapper has no profile router")
            query_profile = profile_factory(request)
        if not isinstance(query_profile, CodeQueryProfile):
            raise TypeError("profile must be a CodeQueryProfile")

        expanded = EvidenceSearchRequest(
            query=request.query,
            scope=request.scope,
            limit=min(50, self.input_budget),
            include_edges=request.include_edges,
        )
        hybrid = self.hybrid_retriever.search_with_trace(expanded, query_profile)
        output_limit = min(request.limit, self.top_k or request.limit)
        fallback_candidates = []
        for rank, candidate in enumerate(hybrid.result.candidates[:output_limit]):
            payload = candidate.model_dump(mode="python", round_trip=True)
            payload["within_source_rank"] = rank
            fallback_candidates.append(CodeRetrievalCandidate.model_validate(payload))
        fallback_result = _result_with_candidates(hybrid.result, fallback_candidates)
        effective_deadline = (
            deadline if deadline is not None else self.clock() + self.rerank_timeout_ms / 1_000.0
        )
        try:
            candidates, decisions = self.reranker.rerank(
                request,
                query_profile,
                hybrid,
                top_k=output_limit,
                deadline=effective_deadline,
                clock=self.clock,
            )
            result = _result_with_candidates(hybrid.result, candidates)
            fallback_used = False
            fallback_reason = ""
        except Exception as error:
            result = fallback_result
            decisions = ()
            fallback_used = True
            detail = " ".join(str(error).split())
            fallback_reason = (
                f"{type(error).__name__}: {detail}" if detail else type(error).__name__
            )
        trace = CodeRerankTrace(
            query_id=hybrid.result.query_id,
            query=request.query,
            profile=query_profile,
            reranker_version=str(getattr(self.reranker, "version", DETERMINISTIC_RERANKER_VERSION)),
            model_version=str(getattr(self.reranker, "model_version", RERANK_MODEL_VERSION)),
            input_budget=self.input_budget,
            input_count=len(hybrid.result.candidates),
            output_count=len(result.candidates),
            decisions=decisions,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )
        return CodeRerankedSearchResult(result=result, trace=trace, hybrid=hybrid)


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """One offline calibration observation; ``relevant=None`` means unlabeled."""

    score: float
    rank: int
    relevant: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", _finite_number(self.score, "score"))
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 0:
            raise ValueError("rank must be a non-negative integer")
        if self.relevant is not None and not isinstance(self.relevant, bool):
            raise TypeError("relevant must be a bool or None")


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    lower: float
    upper: float
    calibrated_score: float
    count: int
    positive_count: int

    def __post_init__(self) -> None:
        lower = _finite_number(self.lower, "lower")
        upper = _finite_number(self.upper, "upper")
        calibrated = _finite_number(self.calibrated_score, "calibrated_score")
        if lower > upper:
            raise ValueError("calibration bin lower must not exceed upper")
        if not 0.0 <= calibrated <= 1.0:
            raise ValueError("calibrated_score must be between zero and one")
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise ValueError("calibration bin count must be positive")
        if (
            isinstance(self.positive_count, bool)
            or not isinstance(self.positive_count, int)
            or not 0 <= self.positive_count <= self.count
        ):
            raise ValueError("positive_count must be between zero and count")
        object.__setattr__(self, "lower", lower)
        object.__setattr__(self, "upper", upper)
        object.__setattr__(self, "calibrated_score", calibrated)


@dataclass(frozen=True, slots=True)
class CalibrationArtifact:
    """Version-bound deterministic artifact.  Scores are not declared probabilities."""

    artifact_version: str
    reranker_version: str
    profile_version: str
    model_version: str
    index_version: str
    status: Literal["available", "unavailable"]
    method: Literal["unavailable", "rank_percentile_empirical", "monotonic_empirical_bins"]
    provisional: bool
    sample_count: int
    labeled_count: int
    max_rank: int
    bins: tuple[CalibrationBin, ...]
    ece: float | None
    brier: float | None
    unavailable_reason: str

    def __post_init__(self) -> None:
        if self.artifact_version != CALIBRATION_ARTIFACT_VERSION:
            raise ValueError("unsupported calibration artifact version")
        for name in (
            "reranker_version",
            "profile_version",
            "model_version",
            "index_version",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")
        if self.sample_count < 0 or self.labeled_count < 0:
            raise ValueError("calibration counts must be non-negative")
        if self.labeled_count > self.sample_count:
            raise ValueError("labeled_count cannot exceed sample_count")
        if self.max_rank < 0:
            raise ValueError("max_rank must be non-negative")
        if self.status == "unavailable":
            if self.bins or not self.unavailable_reason:
                raise ValueError("unavailable artifacts require a reason and no bins")
        elif not self.bins or self.unavailable_reason:
            raise ValueError("available artifacts require bins and no unavailable reason")
        for name in ("ece", "brier"):
            value = getattr(self, name)
            if value is not None:
                normalized = _finite_number(value, name)
                if not 0.0 <= normalized <= 1.0:
                    raise ValueError(f"{name} must be between zero and one")

    def canonical_json_bytes(self) -> bytes:
        return json.dumps(
            asdict(self),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def canonical_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json_bytes()).hexdigest()

    @property
    def canonical_hash(self) -> str:
        return f"sha256:{self.canonical_sha256()}"


@dataclass(frozen=True, slots=True)
class CalibrationApplication:
    status: Literal["available", "unavailable"]
    calibrated_score: float | None
    method: str
    provisional: bool
    artifact_hash: str
    reason: str


def _coerce_sample(value: CalibrationSample | Mapping[str, Any]) -> CalibrationSample:
    if isinstance(value, CalibrationSample):
        return value
    if isinstance(value, Mapping):
        return CalibrationSample(
            score=value["score"],
            rank=value["rank"],
            relevant=value.get("relevant"),
        )
    raise TypeError("calibration samples must be CalibrationSample objects or mappings")


def _chunks[T](values: Sequence[T], count: int) -> tuple[tuple[T, ...], ...]:
    if not values:
        return ()
    count = max(1, min(count, len(values)))
    return tuple(
        tuple(values[start:end])
        for index in range(count)
        if (start := round(index * len(values) / count))
        < (end := round((index + 1) * len(values) / count))
    )


def _pava(rates: Sequence[float], weights: Sequence[int]) -> tuple[float, ...]:
    blocks: list[list[float]] = []
    for index, (rate, weight) in enumerate(zip(rates, weights, strict=True)):
        blocks.append([float(index), float(index), rate * weight, float(weight)])
        while len(blocks) >= 2:
            previous = blocks[-2][2] / blocks[-2][3]
            current = blocks[-1][2] / blocks[-1][3]
            if previous <= current:
                break
            right = blocks.pop()
            left = blocks.pop()
            blocks.append([left[0], right[1], left[2] + right[2], left[3] + right[3]])
    result = [0.0] * len(rates)
    for start, end, positives, weight in blocks:
        fitted = positives / weight
        for index in range(int(start), int(end) + 1):
            result[index] = fitted
    return tuple(result)


def fit_calibration(
    samples: Sequence[CalibrationSample | Mapping[str, Any]],
    *,
    profile_version: str,
    model_version: str,
    index_version: str,
    reranker_version: str = DETERMINISTIC_RERANKER_VERSION,
    min_labeled_samples: int = DEFAULT_MIN_LABELED_SAMPLES,
    bin_count: int = 10,
) -> CalibrationArtifact:
    """Fit deterministic empirical bins without external ML dependencies."""

    if not all((profile_version, model_version, index_version, reranker_version)):
        raise ValueError("calibration context versions must be non-empty")
    if (
        isinstance(min_labeled_samples, bool)
        or not isinstance(min_labeled_samples, int)
        or min_labeled_samples < 2
    ):
        raise ValueError("min_labeled_samples must be an integer of at least two")
    if isinstance(bin_count, bool) or not isinstance(bin_count, int) or bin_count < 2:
        raise ValueError("bin_count must be an integer of at least two")
    normalized = tuple(_coerce_sample(sample) for sample in samples)
    labeled = tuple(sample for sample in normalized if sample.relevant is not None)
    max_rank = max((sample.rank for sample in normalized), default=0)
    base = {
        "artifact_version": CALIBRATION_ARTIFACT_VERSION,
        "reranker_version": reranker_version,
        "profile_version": profile_version,
        "model_version": model_version,
        "index_version": index_version,
        "sample_count": len(normalized),
        "labeled_count": len(labeled),
        "max_rank": max_rank,
    }
    if not normalized:
        return CalibrationArtifact(
            **base,
            status="unavailable",
            method="unavailable",
            provisional=True,
            bins=(),
            ece=None,
            brier=None,
            unavailable_reason="no calibration samples",
        )

    if len(labeled) < min_labeled_samples:
        ranked = sorted(
            normalized,
            key=lambda sample: (
                1.0 - sample.rank / max(1, max_rank + 1),
                sample.score,
                -sample.rank,
            ),
        )
        groups = _chunks(ranked, min(bin_count, max(2, int(math.sqrt(len(ranked))))))
        bins: list[CalibrationBin] = []
        for group in groups:
            percentiles = tuple(1.0 - sample.rank / max(1, max_rank + 1) for sample in group)
            group_labeled = tuple(sample for sample in group if sample.relevant is not None)
            calibrated_score = (
                sum(bool(sample.relevant) for sample in group_labeled) / len(group_labeled)
                if group_labeled
                else math.fsum(percentiles) / len(percentiles)
            )
            bins.append(
                CalibrationBin(
                    lower=min(percentiles),
                    upper=max(percentiles),
                    calibrated_score=calibrated_score,
                    count=len(group),
                    positive_count=sum(bool(sample.relevant) for sample in group_labeled),
                )
            )
        return CalibrationArtifact(
            **base,
            status="available",
            method="rank_percentile_empirical",
            provisional=True,
            bins=tuple(bins),
            ece=None,
            brier=None,
            unavailable_reason="",
        )

    ordered = sorted(labeled, key=lambda sample: (sample.score, sample.rank))
    groups = _chunks(ordered, bin_count)
    observed = tuple(
        sum(bool(sample.relevant) for sample in group) / len(group) for group in groups
    )
    fitted = _pava(observed, tuple(len(group) for group in groups))
    bins = tuple(
        CalibrationBin(
            lower=min(sample.score for sample in group),
            upper=max(sample.score for sample in group),
            calibrated_score=fitted[index],
            count=len(group),
            positive_count=sum(bool(sample.relevant) for sample in group),
        )
        for index, group in enumerate(groups)
    )
    prediction_by_sample: list[tuple[float, bool]] = []
    for index, group in enumerate(groups):
        prediction_by_sample.extend((fitted[index], bool(sample.relevant)) for sample in group)
    ece = math.fsum(
        len(group) / len(ordered) * abs(fitted[index] - observed[index])
        for index, group in enumerate(groups)
    )
    brier = math.fsum(
        (prediction - float(label)) ** 2 for prediction, label in prediction_by_sample
    ) / len(prediction_by_sample)
    return CalibrationArtifact(
        **base,
        status="available",
        method="monotonic_empirical_bins",
        provisional=False,
        bins=bins,
        ece=float(ece),
        brier=float(brier),
        unavailable_reason="",
    )


def apply_calibration(
    artifact: CalibrationArtifact,
    *,
    score: float,
    rank: int,
    profile_version: str,
    model_version: str,
    index_version: str,
    reranker_version: str = DETERMINISTIC_RERANKER_VERSION,
) -> CalibrationApplication:
    """Apply a context-matched artifact and return a non-probability score label."""

    if not isinstance(artifact, CalibrationArtifact):
        raise TypeError("artifact must be a CalibrationArtifact")
    expected = (
        reranker_version,
        profile_version,
        model_version,
        index_version,
    )
    actual = (
        artifact.reranker_version,
        artifact.profile_version,
        artifact.model_version,
        artifact.index_version,
    )
    if actual != expected:
        raise CalibrationArtifactMismatchError(
            "calibration artifact profile/model/index/reranker version mismatch"
        )
    normalized_score = _finite_number(score, "score")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
        raise ValueError("rank must be a non-negative integer")
    if artifact.status == "unavailable":
        return CalibrationApplication(
            status="unavailable",
            calibrated_score=None,
            method=artifact.method,
            provisional=artifact.provisional,
            artifact_hash=artifact.canonical_hash,
            reason=artifact.unavailable_reason,
        )
    feature = (
        1.0 - rank / max(1, artifact.max_rank + 1)
        if artifact.method == "rank_percentile_empirical"
        else normalized_score
    )
    selected = min(
        artifact.bins,
        key=lambda bin_: (
            0.0
            if bin_.lower <= feature <= bin_.upper
            else min(abs(feature - bin_.lower), abs(feature - bin_.upper)),
            bin_.lower,
            bin_.upper,
        ),
    )
    return CalibrationApplication(
        status="available",
        calibrated_score=selected.calibrated_score,
        method=artifact.method,
        provisional=artifact.provisional,
        artifact_hash=artifact.canonical_hash,
        reason="empirical calibration score; not a verified-truth or probability claim",
    )


__all__ = [
    "CALIBRATION_ARTIFACT_VERSION",
    "DETERMINISTIC_RERANKER_VERSION",
    "RERANK_MODEL_CANDIDATES",
    "RERANK_MODEL_VERSION",
    "CalibrationApplication",
    "CalibrationArtifact",
    "CalibrationArtifactMismatchError",
    "CalibrationBin",
    "CalibrationSample",
    "CodeRerankTrace",
    "CodeRerankedRetriever",
    "CodeRerankedSearchResult",
    "DeterministicCodeReranker",
    "EvidenceCard",
    "FeatureVector",
    "RerankDeadlineExceeded",
    "RerankDecision",
    "RerankModelCandidate",
    "apply_calibration",
    "fit_calibration",
]
