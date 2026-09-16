from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from evidence_rag.models import EvidenceSearchRequest, SearchScope
from evidence_rag.rag.sources.code import (
    RERANK_MODEL_CANDIDATES,
    CalibrationArtifactMismatchError,
    CalibrationSample,
    CodeCandidateRole,
    CodeChannelCompleteWithHits,
    CodeChannelDisabled,
    CodeChannelOutcomeStatus,
    CodeChannelRank,
    CodeChannelScore,
    CodeDerivation,
    CodeFactStatus,
    CodeHybridHitTrace,
    CodeHybridSearchResult,
    CodeHybridTrace,
    CodeQueryProfile,
    CodeRelationNode,
    CodeRelationPath,
    CodeRerankedRetriever,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeReviewStatus,
    CodeSourceResult,
    CodeSourceStatus,
    CodeTask,
    CodeUncalibratedScore,
    CodeVersionAlignment,
    DeterministicCodeReranker,
    apply_calibration,
    fit_calibration,
)

REPOSITORY = "repository://rerank"
GENERATION = "generation://rerank"
VERSION = "a" * 40
ACL = "acl://rerank"


def _profile(*, paths: tuple[str, ...] = (), identifiers: tuple[str, ...] = ()) -> CodeQueryProfile:
    return CodeQueryProfile(
        task=CodeTask.IMPLEMENTATION,
        target_paths=paths,
        target_identifiers=identifiers,
    )


def _request(query: str, *, limit: int = 5, acl: str = ACL) -> EvidenceSearchRequest:
    return EvidenceSearchRequest(
        query=query,
        scope=SearchScope(
            repository_ids=[REPOSITORY],
            commit=VERSION,
            allowed_acl_refs=[acl],
            enforce_acl=True,
        ),
        limit=limit,
    )


def _candidate(
    name: str,
    path: str,
    *,
    channels: tuple[tuple[CodeRetrievalChannel, int, float], ...],
    entity_id: str | None = None,
    generation: str = GENERATION,
    version: str = VERSION,
    acl: str = ACL,
    alignment: CodeVersionAlignment = CodeVersionAlignment.EXACT,
    span: tuple[int, int] = (1, 10),
    hybrid_rank: int = 0,
    entity_type: str = "Function",
) -> CodeRetrievalCandidate:
    locator = f"code://{REPOSITORY}@{version}/{path}#L{span[0]}-L{span[1]}"
    entity = entity_id or f"entity://rerank/{path}/{name}"
    relation_path = CodeRelationPath(
        nodes=(
            CodeRelationNode(
                entity_id=entity,
                repository_id=REPOSITORY,
                stable_version=version,
                source_generation=generation,
                locator=locator,
                acl_ref=acl,
            ),
        )
    )
    return CodeRetrievalCandidate(
        entity_id=entity,
        retrieval_unit_id=f"unit://rerank/{path}/{name}/{span[0]}",
        repository_id=REPOSITORY,
        entity_type=entity_type,
        stable_version=version,
        source_generation=generation,
        raw_channel_scores=tuple(
            CodeChannelScore(channel=channel, score=score) for channel, _rank, score in channels
        ),
        raw_channel_ranks=tuple(
            CodeChannelRank(channel=channel, rank=rank) for channel, rank, _score in channels
        ),
        within_source_rank=hybrid_rank,
        source_fused_score=float(1.0 / (hybrid_rank + 1)),
        calibrated_relevance=CodeUncalibratedScore(
            status="unavailable",
            reason="calibration not applied",
        ),
        version_alignment=alignment,
        fact_status=CodeFactStatus.OBSERVED,
        derivation=CodeDerivation.TREE_SITTER,
        review_status=CodeReviewStatus.MACHINE_CONFIRMED,
        role=CodeCandidateRole.TARGET,
        relation_path=relation_path,
        locator=locator,
        token_estimate=20,
        acl_ref=acl,
    )


def _hybrid(
    candidates: tuple[CodeRetrievalCandidate, ...],
    *,
    generation_scope: str | None = None,
) -> CodeHybridSearchResult:
    channels = {rank.channel for candidate in candidates for rank in candidate.raw_channel_ranks}
    outcomes = []
    for channel in CodeRetrievalChannel:
        if channel in channels:
            ranks = [
                rank.rank
                for candidate in candidates
                for rank in candidate.raw_channel_ranks
                if rank.channel is channel
            ]
            outcomes.append(CodeChannelCompleteWithHits(channel=channel, hit_count=max(ranks) + 1))
        else:
            outcomes.append(CodeChannelDisabled(channel=channel, reason="disabled by fake hybrid"))
    result = CodeSourceResult(
        query_id="query://rerank",
        status=CodeSourceStatus.COMPLETE,
        channel_outcomes=tuple(outcomes),
        candidates=candidates,
        index_version="index-v1",
        watermark="watermark://rerank",
        latency_ms=1.0,
    )
    hits = tuple(
        CodeHybridHitTrace(
            retrieval_unit_id=candidate.retrieval_unit_id,
            entity_id=candidate.entity_id,
            locator=candidate.locator,
            raw_ranks=candidate.raw_channel_ranks,
            raw_scores=candidate.raw_channel_scores,
            fused_score=candidate.source_fused_score,
            exact_hard_signal=any(
                rank.channel is CodeRetrievalChannel.EXACT for rank in candidate.raw_channel_ranks
            ),
            explanation="fake hybrid evidence",
        )
        for candidate in candidates
    )
    scope = generation_scope or f"{REPOSITORY}@{VERSION}#{GENERATION}"
    return CodeHybridSearchResult(
        result=result,
        trace=CodeHybridTrace(
            query_id=result.query_id,
            exact_sparse=None,
            dense=None,
            generation_scopes=(scope,),
            channel_hits=hits,
            fusion_policy="fake-rrf",
            diversification_policy="fake-diversification",
        ),
    )


class _FakeHybrid:
    def __init__(self, response: CodeHybridSearchResult) -> None:
        self.response = response
        self.requests: list[EvidenceSearchRequest] = []

    def search_with_trace(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
    ) -> CodeHybridSearchResult:
        self.requests.append(request)
        return self.response


def _rerank(
    response: CodeHybridSearchResult,
    request: EvidenceSearchRequest,
    profile: CodeQueryProfile,
) -> tuple[tuple[CodeRetrievalCandidate, ...], tuple[Any, ...]]:
    return DeterministicCodeReranker().rerank(
        request,
        profile,
        response,
        top_k=request.limit,
    )


def test_exact_hard_signal_stays_ahead_of_dense_only_recovery() -> None:
    exact = _candidate(
        "target",
        "src/target.py",
        channels=((CodeRetrievalChannel.EXACT, 0, 1.0),),
        hybrid_rank=0,
    )
    dense = _candidate(
        "semantic_target",
        "src/semantic.py",
        channels=((CodeRetrievalChannel.DENSE, 0, 0.99),),
        hybrid_rank=1,
    )

    candidates, decisions = _rerank(
        _hybrid((exact, dense)),
        _request("target", limit=2),
        _profile(identifiers=("target",)),
    )

    assert [candidate.retrieval_unit_id for candidate in candidates] == [
        exact.retrieval_unit_id,
        dense.retrieval_unit_id,
    ]
    assert decisions[1].explanation_code == "dense_only_recovery"
    assert decisions[1].accepted is True


def test_same_name_path_hard_negative_is_demoted() -> None:
    admin = _candidate(
        "format_line",
        "src/admin/formatting.py",
        channels=((CodeRetrievalChannel.SPARSE, 0, 10.0),),
        hybrid_rank=0,
    )
    checkout = _candidate(
        "format_line",
        "src/checkout/formatting.py",
        channels=((CodeRetrievalChannel.SPARSE, 1, 9.0),),
        hybrid_rank=1,
    )
    request = _request("src/checkout/formatting.py format_line", limit=2)
    profile = _profile(
        paths=("src/checkout/formatting.py",),
        identifiers=("format_line",),
    )

    candidates, decisions = _rerank(_hybrid((admin, checkout)), request, profile)

    assert candidates[0].retrieval_unit_id == checkout.retrieval_unit_id
    assert decisions[0].card.path_conflict is True
    assert decisions[0].features.hard_negative_penalty < 0


def test_child_duplicate_suppression_prefers_canonical_symbol_span() -> None:
    entity = "entity://rerank/symbol/format_line"
    child = _candidate(
        "child",
        "src/formatting.py",
        entity_id=entity,
        span=(5, 6),
        channels=((CodeRetrievalChannel.SPARSE, 0, 10.0),),
        hybrid_rank=0,
    )
    canonical = _candidate(
        "canonical",
        "src/formatting.py",
        entity_id=entity,
        span=(1, 20),
        channels=((CodeRetrievalChannel.SPARSE, 1, 9.0),),
        hybrid_rank=1,
    )

    candidates, decisions = _rerank(
        _hybrid((child, canonical)),
        _request("formatting", limit=2),
        _profile(identifiers=("formatting",)),
    )

    assert candidates[0].retrieval_unit_id == canonical.retrieval_unit_id
    assert decisions[0].card.child_duplicate is True
    assert decisions[0].features.child_duplicate_penalty < 0
    assert decisions[1].card.canonical_symbol_span is True


def test_generated_partial_parse_error_penalties_are_explicit() -> None:
    degraded = _candidate(
        "parse_error",
        "generated/client.py",
        entity_type="PartialChunk",
        channels=((CodeRetrievalChannel.DENSE, 0, 0.9),),
    )

    _candidates, decisions = _rerank(
        _hybrid((degraded,)),
        _request("client"),
        _profile(identifiers=("client",)),
    )

    card = decisions[0].card
    features = decisions[0].features
    assert card.unit_quality == "parse_error"
    assert card.parse_error and card.generated and card.partial
    assert features.parse_error_penalty < 0
    assert features.generated_penalty < 0
    assert features.partial_penalty < 0


@pytest.mark.parametrize(
    ("candidate_kwargs", "scope", "acl", "reason"),
    [
        (
            {"version": "b" * 40, "alignment": CodeVersionAlignment.MISMATCH},
            f"{REPOSITORY}@{'b' * 40}#{GENERATION}",
            ACL,
            "wrong_version",
        ),
        (
            {"generation": "generation://wrong"},
            f"{REPOSITORY}@{VERSION}#{GENERATION}",
            ACL,
            "wrong_generation",
        ),
        (
            {"acl": "acl://wrong"},
            f"{REPOSITORY}@{VERSION}#{GENERATION}",
            ACL,
            "wrong_acl",
        ),
    ],
)
def test_version_generation_acl_are_hard_gates(
    candidate_kwargs: dict[str, Any],
    scope: str,
    acl: str,
    reason: str,
) -> None:
    candidate = _candidate(
        "target",
        "src/target.py",
        channels=((CodeRetrievalChannel.EXACT, 0, 1.0),),
        **candidate_kwargs,
    )
    response = _hybrid((candidate,), generation_scope=scope)

    selected, decisions = _rerank(
        response,
        _request("target", acl=acl),
        _profile(identifiers=("target",)),
    )

    assert selected == ()
    assert decisions[0].accepted is False
    assert decisions[0].negative_reason == reason


def test_wrapper_is_deterministic_top_k_and_preserves_strict_contract() -> None:
    candidates = tuple(
        _candidate(
            f"target_{index}",
            f"src/target_{index}.py",
            channels=((CodeRetrievalChannel.DENSE, index, 1.0 - index / 10),),
            hybrid_rank=index,
        )
        for index in range(5)
    )
    fake = _FakeHybrid(_hybrid(candidates))
    wrapper = CodeRerankedRetriever(fake, input_budget=30, top_k=2)
    request = _request("target implementation", limit=4)
    profile = _profile(identifiers=("target",))

    first = wrapper.search_with_trace(request, profile)
    second = wrapper.search_with_trace(request, profile)

    assert fake.requests[0].limit == 30
    assert first.result.canonical_sha256() == second.result.canonical_sha256()
    assert len(first.result.candidates) == 2
    assert [candidate.within_source_rank for candidate in first.result.candidates] == [0, 1]
    assert CodeSourceResult.model_validate_json(first.result.model_dump_json()) == first.result
    assert first.trace.cross_encoder_availability == "unavailable"
    assert all(item.benchmark_status == "not-run" for item in RERANK_MODEL_CANDIDATES)


class _ExplodingReranker:
    version = "exploding-reranker"
    model_version = "exploding-model"

    def rerank(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("synthetic failure")


def test_exception_fallback_returns_original_hybrid_rrf_without_request_failure() -> None:
    first = _candidate(
        "first",
        "src/first.py",
        channels=((CodeRetrievalChannel.DENSE, 0, 0.9),),
        hybrid_rank=0,
    )
    second = _candidate(
        "second",
        "src/second.py",
        channels=((CodeRetrievalChannel.DENSE, 1, 0.8),),
        hybrid_rank=1,
    )
    wrapper = CodeRerankedRetriever(
        _FakeHybrid(_hybrid((first, second))),
        reranker=_ExplodingReranker(),
        input_budget=30,
    )

    response = wrapper.search_with_trace(_request("anything", limit=1), _profile())

    assert response.trace.fallback_used is True
    assert "RuntimeError" in response.trace.fallback_reason
    assert response.result.candidates[0].source_fused_score == first.source_fused_score
    assert response.result.candidates[0].retrieval_unit_id == first.retrieval_unit_id


def test_deadline_fallback_is_injectable_without_threads() -> None:
    candidate = _candidate(
        "target",
        "src/target.py",
        channels=((CodeRetrievalChannel.DENSE, 0, 0.9),),
    )
    ticks = iter((0.0, 1.0))
    wrapper = CodeRerankedRetriever(
        _FakeHybrid(_hybrid((candidate,))),
        input_budget=30,
        rerank_timeout_ms=10.0,
        clock=lambda: next(ticks),
    )

    response = wrapper.search_with_trace(_request("target"), _profile())

    assert response.trace.fallback_used is True
    assert "RerankDeadlineExceeded" in response.trace.fallback_reason
    assert response.result.candidates == (candidate,)


def test_calibration_no_data_and_small_sample_are_conservative() -> None:
    empty = fit_calibration(
        (),
        profile_version="profile-v1",
        model_version="model-v1",
        index_version="index-v1",
    )
    assert empty.status == "unavailable"
    assert empty.bins == ()
    assert (
        apply_calibration(
            empty,
            score=1.0,
            rank=0,
            profile_version="profile-v1",
            model_version="model-v1",
            index_version="index-v1",
        ).status
        == "unavailable"
    )

    small = fit_calibration(
        (
            CalibrationSample(score=3.0, rank=0, relevant=True),
            CalibrationSample(score=2.0, rank=1, relevant=False),
            CalibrationSample(score=1.0, rank=2),
        ),
        profile_version="profile-v1",
        model_version="model-v1",
        index_version="index-v1",
    )
    applied = apply_calibration(
        small,
        score=3.0,
        rank=0,
        profile_version="profile-v1",
        model_version="model-v1",
        index_version="index-v1",
    )
    assert small.method == "rank_percentile_empirical"
    assert small.provisional is True
    assert small.ece is None
    assert applied.status == "available"
    assert "not a verified-truth or probability claim" in applied.reason
    assert small.canonical_hash == replace(small).canonical_hash


def test_large_labeled_calibration_is_monotonic_and_has_ece_brier() -> None:
    samples = tuple(
        CalibrationSample(
            score=float(index),
            rank=39 - index,
            relevant=index >= 20,
        )
        for index in range(40)
    )
    artifact = fit_calibration(
        samples,
        profile_version="profile-v1",
        model_version="model-v1",
        index_version="index-v1",
        min_labeled_samples=30,
        bin_count=8,
    )

    scores = [bin_.calibrated_score for bin_ in artifact.bins]
    assert artifact.method == "monotonic_empirical_bins"
    assert artifact.provisional is False
    assert scores == sorted(scores)
    assert artifact.ece is not None and 0.0 <= artifact.ece <= 1.0
    assert artifact.brier is not None and 0.0 <= artifact.brier <= 1.0


@pytest.mark.parametrize(
    ("profile", "model", "index"),
    [
        ("profile-v2", "model-v1", "index-v1"),
        ("profile-v1", "model-v2", "index-v1"),
        ("profile-v1", "model-v1", "index-v2"),
    ],
)
def test_calibration_rejects_profile_model_or_index_version_change(
    profile: str,
    model: str,
    index: str,
) -> None:
    artifact = fit_calibration(
        (CalibrationSample(score=1.0, rank=0),),
        profile_version="profile-v1",
        model_version="model-v1",
        index_version="index-v1",
    )

    with pytest.raises(CalibrationArtifactMismatchError):
        apply_calibration(
            artifact,
            score=1.0,
            rank=0,
            profile_version=profile,
            model_version=model,
            index_version=index,
        )


def test_rerank_result_prunes_channel_outcome_when_hard_gate_removes_all_hits() -> None:
    wrong = _candidate(
        "wrong",
        "src/wrong.py",
        channels=((CodeRetrievalChannel.EXACT, 0, 1.0),),
        acl="acl://wrong",
    )
    wrapper = CodeRerankedRetriever(_FakeHybrid(_hybrid((wrong,))), input_budget=30)

    response = wrapper.search_with_trace(_request("wrong"), _profile())
    exact = next(
        outcome
        for outcome in response.result.channel_outcomes
        if outcome.channel is CodeRetrievalChannel.EXACT
    )

    assert response.result.candidates == ()
    assert exact.status is CodeChannelOutcomeStatus.COMPLETE_PRUNED
