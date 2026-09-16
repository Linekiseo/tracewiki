from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import pytest

from evidence_rag.models import EvidenceSearchRequest, SearchScope
from evidence_rag.rag.sources.code import (
    CODE_QUERY_PROFILE_REGISTRY as PUBLIC_PROFILE_REGISTRY,
)
from evidence_rag.rag.sources.code import (
    CodeSourceFusionPipeline as PublicCodeSourceFusionPipeline,
)
from evidence_rag.rag.sources.code.contracts import (
    CodeCalibratedScore,
    CodeCandidateRole,
    CodeChannelCompleteNoMatch,
    CodeChannelCompleteWithHits,
    CodeChannelDisabled,
    CodeChannelRank,
    CodeChannelScore,
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
from evidence_rag.rag.sources.code.dense_v2 import (
    CodeHybridSearchResult,
    CodeHybridTrace,
)
from evidence_rag.rag.sources.code.graph_retrieval_v2 import GraphTraversalStatus
from evidence_rag.rag.sources.code.query_profile_v2 import (
    CODE_QUERY_PROFILE_REGISTRY,
    CodeMissingRolePolicy,
    CodeOptionalHookResult,
    CodeOptionalHookStatus,
    CodePipelineStageStatus,
    CodeSourceFusionPipeline,
    get_code_query_profile,
    infer_code_task,
    resolve_code_query_profile,
    rewrite_code_query,
)

REPOSITORY = "repository://alpha"
GENERATION = "generation://alpha"
VERSION = "a" * 40
ACL = "acl://alpha"
HIDDEN_ACL = "acl://hidden"


def _request(query: str, *, limit: int = 20, commit: str | None = None) -> EvidenceSearchRequest:
    return EvidenceSearchRequest(
        query=query,
        limit=limit,
        scope=SearchScope(
            project_id="project://alpha",
            repository_ids=[REPOSITORY],
            commit=commit,
            allowed_acl_refs=[ACL],
            enforce_acl=True,
        ),
    )


@pytest.mark.parametrize(
    "scope_update",
    [
        {"project_id": None},
        {"repository_ids": []},
        {"allowed_acl_refs": []},
        {"enforce_acl": False},
    ],
)
def test_production_pipeline_refuses_to_issue_ungoverned_scope_attestation(
    scope_update: dict[str, object],
) -> None:
    request = _request("implementation of target")
    request = request.model_copy(update={"scope": request.scope.model_copy(update=scope_update)})
    pipeline = CodeSourceFusionPipeline(FakeRetriever((_candidate("target"),)))

    with pytest.raises(ValueError, match="attestation"):
        pipeline.search_with_trace(request, task=CodeTask.IMPLEMENTATION)


def test_production_pipeline_issues_result_bound_scope_attestation() -> None:
    request = _request("implementation of target", commit=VERSION)
    pipeline = CodeSourceFusionPipeline(FakeRetriever((_candidate("target"),)))

    output = pipeline.search_with_trace(request, task=CodeTask.IMPLEMENTATION)

    attestation = output.scope_attestation
    assert attestation is not None
    assert attestation.project_id == "project://alpha"
    assert attestation.repository_ids == (REPOSITORY,)
    assert attestation.requested_commit == VERSION
    assert attestation.allowed_acl_refs == (ACL,)
    assert attestation.enforce_acl is True
    assert attestation.index_version == output.result.index_version
    assert attestation.watermark == output.result.watermark
    assert attestation.stable_versions == (VERSION,)
    assert attestation.source_generations == (GENERATION,)
    assert attestation.candidate_identities
    assert attestation.result_identity.startswith("sha256:")
    assert attestation.authority_tag.startswith("hmac-sha256:")


def _candidate(
    name: str,
    *,
    channel: CodeRetrievalChannel = CodeRetrievalChannel.EXACT,
    rank: int = 0,
    role: CodeCandidateRole = CodeCandidateRole.TARGET,
    entity_id: str | None = None,
    unit_id: str | None = None,
    version: str = VERSION,
    generation: str = GENERATION,
    acl_ref: str = ACL,
    alignment: CodeVersionAlignment = CodeVersionAlignment.EXACT,
    locator: str | None = None,
) -> CodeRetrievalCandidate:
    entity = entity_id or f"entity://{name}"
    unit = unit_id or f"unit://{name}"
    resolved_locator = locator or f"code://{REPOSITORY}@{version}/src/{name}.py#L1-L2"
    path = CodeRelationPath(
        nodes=(
            CodeRelationNode(
                entity_id=entity,
                repository_id=REPOSITORY,
                stable_version=version,
                source_generation=generation,
                locator=resolved_locator,
                acl_ref=acl_ref,
            ),
        ),
        edges=(),
        path_score=0.0,
    )
    return CodeRetrievalCandidate(
        entity_id=entity,
        retrieval_unit_id=unit,
        repository_id=REPOSITORY,
        entity_type="code_symbol",
        stable_version=version,
        source_generation=generation,
        raw_channel_scores=(CodeChannelScore(channel=channel, score=1.0),),
        raw_channel_ranks=(CodeChannelRank(channel=channel, rank=rank),),
        within_source_rank=rank,
        source_fused_score=1.0,
        calibrated_relevance=CodeUncalibratedScore(
            status="unavailable",
            reason="test candidate has no calibration artifact",
        ),
        version_alignment=alignment,
        fact_status=CodeFactStatus.OBSERVED,
        derivation=CodeDerivation.TREE_SITTER,
        review_status=CodeReviewStatus.MACHINE_CONFIRMED,
        role=role,
        relation_path=path,
        locator=resolved_locator,
        token_estimate=10,
        acl_ref=acl_ref,
    )


def _result(
    profile: CodeQueryProfile,
    candidates: tuple[CodeRetrievalCandidate, ...],
) -> CodeSourceResult:
    observed = {score.channel for candidate in candidates for score in candidate.raw_channel_scores}
    budgets = {
        CodeRetrievalChannel.EXACT: profile.budget.exact_candidates,
        CodeRetrievalChannel.SPARSE: profile.budget.sparse_candidates,
        CodeRetrievalChannel.DENSE: profile.budget.dense_candidates,
        CodeRetrievalChannel.GRAPH: profile.budget.graph_candidates,
        CodeRetrievalChannel.HISTORY: profile.budget.history_candidates,
        CodeRetrievalChannel.TEST: profile.budget.test_candidates,
    }
    outcomes = []
    for channel in CodeRetrievalChannel:
        if budgets[channel] == 0:
            outcomes.append(
                CodeChannelDisabled(
                    channel=channel,
                    reason="disabled by fake retriever profile",
                )
            )
        elif channel in observed:
            count = (
                max(
                    rank.rank
                    for candidate in candidates
                    for rank in candidate.raw_channel_ranks
                    if rank.channel is channel
                )
                + 1
            )
            outcomes.append(CodeChannelCompleteWithHits(channel=channel, hit_count=count))
        else:
            outcomes.append(CodeChannelCompleteNoMatch(channel=channel))
    return CodeSourceResult(
        query_id=f"query:{profile.task.value}",
        status=CodeSourceStatus.COMPLETE,
        channel_outcomes=tuple(outcomes),
        candidates=candidates,
        context_blocks=(),
        index_version="index://v2",
        watermark=VERSION,
        latency_ms=1.0,
        errors=(),
    )


class FakeRetriever:
    retriever_version = "fake-retriever-v1"

    def __init__(
        self,
        candidates: tuple[CodeRetrievalCandidate, ...],
        *,
        real_hybrid: bool = False,
    ) -> None:
        self.candidates = candidates
        self.real_hybrid = real_hybrid
        self.calls: list[tuple[EvidenceSearchRequest, CodeQueryProfile]] = []

    def search_with_trace(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
    ) -> Any:
        self.calls.append((request, profile))
        result = _result(profile, self.candidates)
        if not self.real_hybrid:
            return SimpleNamespace(result=result)
        trace = CodeHybridTrace(
            query_id=result.query_id,
            exact_sparse=None,
            dense=None,
            generation_scopes=(f"{REPOSITORY}@{VERSION}#{GENERATION}",),
            channel_hits=(),
            fusion_policy="fake-hybrid-fusion-v1",
            diversification_policy="fake-diversification-v1",
        )
        return CodeHybridSearchResult(result=result, trace=trace)


class FailingRetriever:
    retriever_version = "failing-retriever-v1"

    def search_with_trace(self, _request: Any, _profile: Any) -> Any:
        raise RuntimeError("seed backend exploded")


class FakeGraphRetriever:
    retriever_version = "fake-typed-graph-v1"

    def __init__(
        self,
        *,
        status: GraphTraversalStatus = GraphTraversalStatus.COMPLETE,
        role: CodeCandidateRole = CodeCandidateRole.DEPENDENCY,
        fail: bool = False,
    ) -> None:
        self.status = status
        self.role = role
        self.fail = fail
        self.calls: list[Any] = []

    def search_with_trace(self, request: Any) -> Any:
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("graph backend exploded")
        candidates = ()
        reason = ""
        if self.status is GraphTraversalStatus.COMPLETE:
            candidates = (
                _candidate(
                    f"graph-{request.task.value}",
                    channel=CodeRetrievalChannel.GRAPH,
                    role=self.role,
                    version=request.stable_version,
                    generation=request.generation_id,
                ),
            )
        elif self.status is GraphTraversalStatus.UNAVAILABLE:
            reason = "graph publication not built"
        trace = SimpleNamespace(status=self.status, unavailable_reason=reason)
        return SimpleNamespace(trace=trace, code_candidates=candidates)


class FakeHook:
    hook_version = "fake-hook-v1"

    def __init__(
        self,
        channel: CodeRetrievalChannel,
        candidates: tuple[CodeRetrievalCandidate, ...] = (),
        *,
        status: CodeOptionalHookStatus | None = None,
        fail: bool = False,
    ) -> None:
        self.channel = channel
        self.candidates = candidates
        self.status = status or (
            CodeOptionalHookStatus.COMPLETE
            if candidates
            else CodeOptionalHookStatus.COMPLETE_NO_MATCH
        )
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def expand(self, **kwargs: Any) -> CodeOptionalHookResult:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("hook backend exploded")
        return CodeOptionalHookResult(
            channel=self.channel,
            status=self.status,
            candidates=self.candidates,
            hook_version=self.hook_version,
            reason=(
                "hook unavailable" if self.status is CodeOptionalHookStatus.UNAVAILABLE else ""
            ),
        )


class FakeReranker:
    version = "fake-reranker-v1"
    model_version = "fake-model-v1"

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def rerank(self, _request: Any, _profile: Any, response: Any, **_kwargs: Any) -> Any:
        self.calls.append(response)
        return tuple(reversed(response.result.candidates)), ()


class FakeCalibrationArtifact:
    artifact_version = "fake-calibration-v1"


def _fake_calibration(_artifact: Any, **_kwargs: Any) -> Any:
    return SimpleNamespace(
        status="available",
        calibrated_score=0.75,
        artifact_hash="sha256:fake-calibration",
        reason="",
    )


def _events(response: Any) -> dict[str, Any]:
    return {event.stage: event for event in response.trace.events}


def test_registry_covers_all_eight_profiles_and_is_frozen() -> None:
    assert PUBLIC_PROFILE_REGISTRY is CODE_QUERY_PROFILE_REGISTRY
    assert PublicCodeSourceFusionPipeline is CodeSourceFusionPipeline
    assert set(CODE_QUERY_PROFILE_REGISTRY) == set(CodeTask)
    assert len(CODE_QUERY_PROFILE_REGISTRY) == 8

    for task, profile in CODE_QUERY_PROFILE_REGISTRY.items():
        resolved = resolve_code_query_profile("implementation of Checkout.quote", task)
        assert resolved.definition is profile
        assert resolved.contract.task is task
        assert resolved.contract.profile_version == profile.version
        assert profile.enabled_channels
        assert profile.required_roles
        assert profile.context_template.template_id.endswith("@v2")
        assert profile.adaptive_k.minimum <= profile.adaptive_k.maximum
        if CodeRetrievalChannel.GRAPH in profile.enabled_channels:
            assert profile.edge_types
            assert profile.directions
            assert profile.max_hops > 0
        with pytest.raises(FrozenInstanceError):
            profile.key = "mutated"  # type: ignore[misc]

    with pytest.raises(TypeError):
        CODE_QUERY_PROFILE_REGISTRY[CodeTask.EXACT_LOCATION] = get_code_query_profile(
            CodeTask.IMPLEMENTATION
        )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("checkout.PriceEngine.quote", CodeTask.EXACT_LOCATION),
        ("show implementation of quote", CodeTask.IMPLEMENTATION),
        ("who calls PriceEngine.quote", CodeTask.CALL_PATH),
        ("root cause of this traceback", CodeTask.BUG_LOCALIZATION),
        ("blast radius of quote", CodeTask.IMPACT_ANALYSIS),
        ("why changed in this commit", CodeTask.CHANGE_CONTEXT),
        ("historical implementation of quote", CodeTask.HISTORICAL),
        ("which test validates quote", CodeTask.TEST_VALIDATION),
    ],
)
def test_rewrite_and_route_are_deterministic(query: str, expected: CodeTask) -> None:
    snapshots = tuple(resolve_code_query_profile(query) for _ in range(5))

    assert infer_code_task(query) is expected
    assert all(snapshot == snapshots[0] for snapshot in snapshots)
    assert snapshots[0].definition.task is expected
    assert snapshots[0].rewrite.rewritten_query == " ".join(query.split())


def test_simple_exact_fast_path_never_invokes_dense_or_graph() -> None:
    exact = FakeRetriever((_candidate("quote"),))
    hybrid = FakeRetriever((_candidate("dense-quote", channel=CodeRetrievalChannel.DENSE),))
    graph = FakeGraphRetriever()
    pipeline = CodeSourceFusionPipeline(
        exact,
        hybrid_retriever=hybrid,
        graph_retriever=graph,
    )

    response = pipeline.search_with_trace(_request("checkout.PriceEngine.quote"))

    assert response.profile.definition.task is CodeTask.EXACT_LOCATION
    assert len(exact.calls) == 1
    assert hybrid.calls == []
    assert graph.calls == []
    assert response.result.candidates[0].retrieval_unit_id == "unit://quote"
    assert _events(response)["dense"].status is CodePipelineStageStatus.SKIPPED
    assert _events(response)["graph"].status is CodePipelineStageStatus.SKIPPED


def test_pipeline_accepts_existing_code_query_profile_api() -> None:
    existing = CodeQueryProfile(
        task=CodeTask.EXACT_LOCATION,
        target_identifiers=("ExplicitTarget",),
        target_ref="main",
    )
    retriever = FakeRetriever((_candidate("target"),))

    response = CodeSourceFusionPipeline(retriever).search_with_trace(
        _request("checkout.PriceEngine.quote"),
        existing,
    )

    assert response.profile.contract.task is CodeTask.EXACT_LOCATION
    assert "ExplicitTarget" in response.profile.contract.target_identifiers
    assert response.profile.contract.target_ref == "main"
    assert retriever.calls[0][1] == response.profile.contract


@pytest.mark.parametrize(
    ("task", "role"),
    [
        (CodeTask.CALL_PATH, CodeCandidateRole.DEPENDENCY),
        (CodeTask.IMPACT_ANALYSIS, CodeCandidateRole.DEPENDENCY),
        (CodeTask.TEST_VALIDATION, CodeCandidateRole.TEST),
    ],
)
def test_call_impact_and_test_profiles_use_graph_for_candidate_generation(
    task: CodeTask,
    role: CodeCandidateRole,
) -> None:
    exact = FakeRetriever((_candidate("target"),))
    hybrid = FakeRetriever((_candidate("target"),))
    graph = FakeGraphRetriever(role=role)
    test_hook = (
        FakeHook(
            CodeRetrievalChannel.TEST,
            (
                _candidate(
                    "observed-test",
                    channel=CodeRetrievalChannel.TEST,
                    role=CodeCandidateRole.TEST,
                ),
            ),
        )
        if task is CodeTask.TEST_VALIDATION
        else None
    )
    pipeline = CodeSourceFusionPipeline(
        exact,
        hybrid_retriever=hybrid,
        graph_retriever=graph,
        test_hook=test_hook,
    )

    response = pipeline.search_with_trace(
        _request("query requiring expansion"),
        task=task,
    )

    assert len(graph.calls) == 1
    assert graph.calls[0].edge_types == CODE_QUERY_PROFILE_REGISTRY[task].edge_types
    assert any(
        CodeRetrievalChannel.GRAPH in {score.channel for score in candidate.raw_channel_scores}
        for candidate in response.result.candidates
    )
    assert role in {candidate.role for candidate in response.result.candidates}
    assert _events(response)["graph"].status is CodePipelineStageStatus.COMPLETE
    assert not response.trace.refused


def test_graph_and_history_hook_failure_fall_back_without_false_availability() -> None:
    target = _candidate("target")
    pipeline = CodeSourceFusionPipeline(
        FakeRetriever((target,)),
        hybrid_retriever=FakeRetriever((target,)),
        graph_retriever=FakeGraphRetriever(fail=True),
        history_hook=FakeHook(CodeRetrievalChannel.HISTORY, fail=True),
    )

    response = pipeline.search_with_trace(
        _request("change context for target"),
        task=CodeTask.CHANGE_CONTEXT,
    )

    assert response.result.status is CodeSourceStatus.PARTIAL
    assert response.result.candidates
    assert all(
        CodeRetrievalChannel.HISTORY
        not in {score.channel for score in candidate.raw_channel_scores}
        for candidate in response.result.candidates
    )
    assert _events(response)["graph"].status is CodePipelineStageStatus.ERROR
    assert _events(response)["history"].status is CodePipelineStageStatus.ERROR
    assert _events(response)["required-roles"].status is CodePipelineStageStatus.DEGRADED


def test_seed_retrieval_failure_returns_strict_unavailable_with_stop_trace() -> None:
    response = CodeSourceFusionPipeline(
        FailingRetriever(),
        graph_retriever=FakeGraphRetriever(),
    ).search_with_trace(
        _request("checkout.PriceEngine.quote"),
        task=CodeTask.EXACT_LOCATION,
    )

    assert response.result.status is CodeSourceStatus.UNAVAILABLE
    assert response.result.candidates == ()
    assert _events(response)["exact-sparse"].status is CodePipelineStageStatus.ERROR
    assert _events(response)["calibration"].status is CodePipelineStageStatus.SKIPPED
    assert (
        CodeSourceResult.model_validate_json(response.result.model_dump_json()) == response.result
    )


def test_missing_dependency_refuses_impact_profile_and_returns_strict_partial() -> None:
    target = _candidate("target")
    pipeline = CodeSourceFusionPipeline(
        FakeRetriever((target,)),
        hybrid_retriever=FakeRetriever((target,)),
        graph_retriever=FakeGraphRetriever(status=GraphTraversalStatus.NO_MATCH),
    )

    response = pipeline.search_with_trace(
        _request("impact of target"),
        task=CodeTask.IMPACT_ANALYSIS,
    )

    assert (
        CODE_QUERY_PROFILE_REGISTRY[CodeTask.IMPACT_ANALYSIS].missing_role_policy
        is CodeMissingRolePolicy.REFUSE
    )
    assert response.trace.refused
    assert response.trace.missing_roles == (CodeCandidateRole.DEPENDENCY,)
    assert response.result.status is CodeSourceStatus.PARTIAL
    assert response.result.candidates == ()
    assert _events(response)["required-roles"].status is CodePipelineStageStatus.REFUSED
    assert (
        CodeSourceResult.model_validate_json(response.result.model_dump_json()) == response.result
    )


def test_adaptive_k_is_bounded_and_keeps_required_graph_role() -> None:
    sparse = tuple(
        _candidate(
            f"candidate-{index}",
            channel=CodeRetrievalChannel.SPARSE,
            rank=index,
        )
        for index in range(30)
    )
    graph = FakeGraphRetriever(role=CodeCandidateRole.DEPENDENCY)
    pipeline = CodeSourceFusionPipeline(
        FakeRetriever(sparse),
        hybrid_retriever=FakeRetriever(sparse),
        graph_retriever=graph,
    )

    response = pipeline.search_with_trace(
        _request("call path for candidate", limit=50),
        task=CodeTask.CALL_PATH,
    )

    policy = CODE_QUERY_PROFILE_REGISTRY[CodeTask.CALL_PATH].adaptive_k
    assert policy.minimum <= response.trace.adaptive_k <= policy.maximum
    assert len(response.result.candidates) == response.trace.adaptive_k
    assert CodeCandidateRole.DEPENDENCY in {
        candidate.role for candidate in response.result.candidates
    }
    assert [candidate.within_source_rank for candidate in response.result.candidates] == list(
        range(len(response.result.candidates))
    )


def test_entity_unit_version_acl_dedup_preserves_exact_governed_candidate() -> None:
    target = _candidate("target", locator="code://exact-target")
    duplicate_old = _candidate(
        "old-target",
        channel=CodeRetrievalChannel.HISTORY,
        role=CodeCandidateRole.HISTORY,
        entity_id=target.entity_id,
        unit_id=target.retrieval_unit_id,
        version="b" * 40,
        generation="generation://old",
        alignment=CodeVersionAlignment.HISTORICAL,
    )
    hidden = _candidate(
        "hidden-history",
        channel=CodeRetrievalChannel.HISTORY,
        rank=1,
        role=CodeCandidateRole.HISTORY,
        acl_ref=HIDDEN_ACL,
    )
    visible = _candidate(
        "visible-history",
        channel=CodeRetrievalChannel.HISTORY,
        rank=2,
        role=CodeCandidateRole.HISTORY,
    )
    history = FakeHook(
        CodeRetrievalChannel.HISTORY,
        (duplicate_old, hidden, visible),
    )
    pipeline = CodeSourceFusionPipeline(
        FakeRetriever((target,)),
        hybrid_retriever=FakeRetriever((target,)),
        graph_retriever=FakeGraphRetriever(status=GraphTraversalStatus.NO_MATCH),
        history_hook=history,
    )

    response = pipeline.search_with_trace(
        _request("change context for target"),
        task=CodeTask.CHANGE_CONTEXT,
    )

    pairs = [
        (candidate.entity_id, candidate.retrieval_unit_id)
        for candidate in response.result.candidates
    ]
    assert len(pairs) == len(set(pairs))
    kept_target = next(
        candidate
        for candidate in response.result.candidates
        if candidate.retrieval_unit_id == target.retrieval_unit_id
    )
    assert kept_target.stable_version == VERSION
    assert kept_target.source_generation == GENERATION
    assert kept_target.acl_ref == ACL
    assert kept_target.locator == "code://exact-target"
    assert all(candidate.acl_ref == ACL for candidate in response.result.candidates)
    assert "unit://hidden-history" not in {
        candidate.retrieval_unit_id for candidate in response.result.candidates
    }


def test_rerank_and_calibration_are_real_injected_stages_and_result_is_strict() -> None:
    first = _candidate("first", channel=CodeRetrievalChannel.SPARSE, rank=0)
    second = _candidate("second", channel=CodeRetrievalChannel.SPARSE, rank=1)
    hybrid = FakeRetriever((first, second), real_hybrid=True)
    reranker = FakeReranker()
    pipeline = CodeSourceFusionPipeline(
        FakeRetriever((first, second)),
        hybrid_retriever=hybrid,
        graph_retriever=FakeGraphRetriever(status=GraphTraversalStatus.NO_MATCH),
        reranker=reranker,
        calibration_artifact=FakeCalibrationArtifact(),
        calibration_function=_fake_calibration,
    )

    response = pipeline.search_with_trace(
        _request("implementation of service"),
        task=CodeTask.IMPLEMENTATION,
    )

    assert len(reranker.calls) == 1
    assert response.result.candidates[0].retrieval_unit_id == "unit://second"
    assert all(
        isinstance(candidate.calibrated_relevance, CodeCalibratedScore)
        and candidate.calibrated_relevance.score == 0.75
        for candidate in response.result.candidates
    )
    assert _events(response)["rerank"].status is CodePipelineStageStatus.COMPLETE
    assert _events(response)["calibration"].status is CodePipelineStageStatus.COMPLETE
    assert CodeSourceResult.model_validate(response.result.model_dump()) == response.result


def test_negative_path_has_zero_accepted_paths_and_honestly_degrades() -> None:
    rewrite = rewrite_code_query("../secrets.py", CodeTask.EXACT_LOCATION)
    assert rewrite.accepted_paths == ()
    assert rewrite.rejected_paths == ("../secrets.py",)
    assert rewrite.target_identifiers == ()
    assert rewrite.rewritten_query == "unresolved locator input"

    pipeline = CodeSourceFusionPipeline(FakeRetriever(()))
    response = pipeline.search_with_trace(
        _request("../secrets.py"),
        task=CodeTask.EXACT_LOCATION,
    )

    assert response.profile.rewrite.accepted_paths == ()
    assert response.result.candidates == ()
    assert response.trace.missing_roles == (CodeCandidateRole.TARGET,)
    assert not response.trace.refused
    assert _events(response)["required-roles"].status is CodePipelineStageStatus.DEGRADED
    assert "accepted_paths=0" in _events(response)["profile"].reason


def test_path_rewrite_accepts_governed_uri_path_but_rejects_absolute_path() -> None:
    governed = rewrite_code_query(
        f"code://{REPOSITORY}@{VERSION}/src/checkout/pricing.py#L10-L12",
        CodeTask.EXACT_LOCATION,
    )
    absolute = rewrite_code_query(
        "/private/repository/src/checkout/pricing.py",
        CodeTask.EXACT_LOCATION,
    )

    assert governed.accepted_paths == ("src/checkout/pricing.py",)
    assert governed.rejected_paths == ()
    assert absolute.accepted_paths == ()
    assert absolute.rejected_paths == ("/private/repository/src/checkout/pricing.py",)


def test_control_character_query_has_no_accepted_execution_path() -> None:
    with pytest.raises(ValueError, match="control"):
        resolve_code_query_profile("where is Foo\x00bar")
