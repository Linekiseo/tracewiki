from __future__ import annotations

import copy
import copyreg
import gc
import io
import pickle
import threading
import weakref
from dataclasses import replace
from typing import Any

import pytest

from evidence_rag import retrieval as retrieval_module
from evidence_rag.models import EvidenceSearchRequest, SearchScope
from evidence_rag.rag.sources.code import (
    CodeChannelCompleteNoMatch,
    CodeChannelCompletePruned,
    CodeChannelCompleteWithHits,
    CodeChannelDisabled,
    CodeRetrievalChannel,
    CodeSourceRetrieverV1Adapter,
    CodeVersionAlignment,
    LegacyCodeResponseError,
)
from evidence_rag.retrieval import HybridRetriever, LegacySearchResponse
from evidence_rag.runtime import create_runtime


class _LegacyRetriever(HybridRetriever):
    def __init__(self, response: dict[str, Any]) -> None:
        super().__init__(store=None, embedder=None)  # type: ignore[arg-type]
        self.response = response
        self.requests: list[EvidenceSearchRequest] = []

    def search(self, request: EvidenceSearchRequest) -> dict[str, Any]:
        self.requests.append(request)
        return self.executed(request)

    def executed(
        self,
        request: EvidenceSearchRequest,
        response: dict[str, Any] | None = None,
        *,
        channel_entity_ids: dict[str, list[str]] | None = None,
    ) -> LegacySearchResponse:
        payload = copy.deepcopy(response if response is not None else self.response)
        if channel_entity_ids is None:
            channel_entity_ids = {}
            for channel, score_field, count_field in (
                ("lexical", "lexical_score", "lexical_candidates"),
                ("dense", "dense_score", "dense_matches"),
            ):
                returned = sorted(
                    (item for item in payload["results"] if channel in item.get("channels", ())),
                    key=lambda item: -item[score_field],
                )
                entity_ids = [item["entity_id"] for item in returned]
                hit_count = payload["trace"][count_field]
                entity_ids.extend(
                    f"code://hidden/{channel}/{index}"
                    for index in range(len(entity_ids), hit_count)
                )
                channel_entity_ids[channel] = entity_ids
        return self._attest_response(
            request,
            payload,
            channel_entity_ids=channel_entity_ids,
        )


class _EqualLegacyRetriever(_LegacyRetriever):
    hash_calls = 0
    equality_calls = 0

    def __hash__(self) -> int:
        type(self).hash_calls += 1
        return 1

    def __eq__(self, other: object) -> bool:
        type(self).equality_calls += 1
        return isinstance(other, _EqualLegacyRetriever)


def _item(
    entity_id: str,
    *,
    score: float,
    lexical_score: float,
    dense_score: float,
    channels: list[str],
    commit: str = "commit-a",
    generation: str = "gen-a",
    repository_id: str = "repository://alpha",
    acl_ref: str = "team:alpha",
    view_type: str = "symbol.raw",
) -> dict[str, Any]:
    name = entity_id.rsplit("/", 1)[-1]
    return {
        "entity_id": entity_id,
        "repository_id": repository_id,
        "generation_id": generation,
        "entity_type": "CodeSymbol",
        "view_type": view_type,
        "name": name,
        "qualified_name": f"pkg.{name}",
        "path": f"src/{name}.py",
        "language": "Python",
        "commit": commit,
        "start_line": 1,
        "end_line": 8,
        "evidence_locator": f"code://alpha@{commit}/src/{name}.py#L1-L8",
        "acl_ref": acl_ref,
        "metadata": {"kind": "function"},
        "lexical_score": lexical_score,
        "dense_score": dense_score,
        "score": score,
        "channels": channels,
        "edges": [],
        "snippet": f"def {name}():\n    return 1\n",
    }


def _request(*, commit: str | None = None) -> EvidenceSearchRequest:
    return EvidenceSearchRequest(
        query="rank implementation",
        scope=SearchScope(
            project_id="project-alpha",
            repository_ids=["repository://alpha"],
            commit=commit,
            allowed_acl_refs=["team:alpha"],
            enforce_acl=True,
        ),
        limit=3,
        include_edges=False,
    )


def _response(
    request: EvidenceSearchRequest,
    *,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    results = (
        items
        if items is not None
        else [
            _item(
                "code://alpha/rank",
                score=0.91,
                lexical_score=1.0,
                dense_score=0.61,
                channels=["dense", "lexical"],
                commit=request.scope.commit or "commit-a",
            ),
            _item(
                "code://alpha/normalize",
                score=0.81,
                lexical_score=0.75,
                dense_score=0.0,
                channels=["lexical"],
                commit=request.scope.commit or "commit-a",
            ),
            _item(
                "code://alpha/similarity",
                score=0.71,
                lexical_score=0.0,
                dense_score=0.82,
                channels=["dense"],
                commit=request.scope.commit or "commit-a",
            ),
        ]
    )
    generations = sorted({item["generation_id"] for item in results})
    return {
        "query_id": "q-11111111-2222-4333-8444-555555555555",
        "query": request.query,
        "resolved_scope": request.scope.model_dump(),
        "index_generation": generations,
        "total": len(results),
        "results": results,
        "trace": {
            "duration_ms": 12.5,
            "lexical_candidates": 5,
            "dense_candidates": 9,
            "dense_matches": 4,
            "fusion": "weighted-hybrid-v2",
            "embedding_model": "local-hash-v2",
            "queried_embedding_models": ["local-hash-v2"],
        },
    }


def _outcomes_by_channel(result) -> dict:
    return {outcome.channel: outcome for outcome in result.channel_outcomes}


def test_adapter_preserves_complete_legacy_top_k_and_maps_raw_channels() -> None:
    request = _request()
    response = _response(request)
    legacy = _LegacyRetriever(response)
    legacy_response = legacy.executed(request)
    snapshot = copy.deepcopy(legacy_response)
    adapter = CodeSourceRetrieverV1Adapter(legacy)

    result = adapter.adapt_response(request, legacy_response)

    assert legacy_response == snapshot
    assert [candidate.entity_id for candidate in result.candidates] == [
        item["entity_id"] for item in legacy_response["results"]
    ]
    assert [candidate.within_source_rank for candidate in result.candidates] == [0, 1, 2]
    rank, normalize, similarity = result.candidates
    assert {entry.channel: entry.rank for entry in rank.raw_channel_ranks} == {
        CodeRetrievalChannel.SPARSE: 0,
        CodeRetrievalChannel.DENSE: 1,
    }
    assert {entry.channel: entry.rank for entry in normalize.raw_channel_ranks} == {
        CodeRetrievalChannel.SPARSE: 1
    }
    assert {entry.channel: entry.rank for entry in similarity.raw_channel_ranks} == {
        CodeRetrievalChannel.DENSE: 0
    }
    assert {entry.channel: entry.score for entry in rank.raw_channel_scores} == {
        CodeRetrievalChannel.SPARSE: 1.0,
        CodeRetrievalChannel.DENSE: 0.61,
    }
    outcomes = _outcomes_by_channel(result)
    assert isinstance(outcomes[CodeRetrievalChannel.SPARSE], CodeChannelCompleteWithHits)
    assert outcomes[CodeRetrievalChannel.SPARSE].hit_count == 5
    assert isinstance(outcomes[CodeRetrievalChannel.DENSE], CodeChannelCompleteWithHits)
    assert outcomes[CodeRetrievalChannel.DENSE].hit_count == 4
    for channel in (
        CodeRetrievalChannel.EXACT,
        CodeRetrievalChannel.GRAPH,
        CodeRetrievalChannel.HISTORY,
        CodeRetrievalChannel.TEST,
    ):
        assert isinstance(outcomes[channel], CodeChannelDisabled)
    assert all(
        candidate.calibrated_relevance.status == "disabled" for candidate in result.candidates
    )
    assert all(len(candidate.relation_path.nodes) == 1 for candidate in result.candidates)
    assert all(candidate.relation_path.edges == () for candidate in result.candidates)


def test_adapter_empty_result_has_complete_no_match_for_ran_channels() -> None:
    request = _request()
    response = _response(request, items=[])
    response["trace"].update(
        lexical_candidates=0,
        dense_candidates=0,
        dense_matches=0,
    )

    legacy = _LegacyRetriever(response)
    result = CodeSourceRetrieverV1Adapter(legacy).adapt_response(request, legacy.executed(request))

    assert result.status == "complete"
    assert result.candidates == ()
    outcomes = _outcomes_by_channel(result)
    assert isinstance(outcomes[CodeRetrievalChannel.SPARSE], CodeChannelCompleteNoMatch)
    assert isinstance(outcomes[CodeRetrievalChannel.DENSE], CodeChannelCompleteNoMatch)


def test_raw_v1_unit_identity_is_deterministic_and_rebuildable() -> None:
    fields = {
        "entity_id": "code://alpha/rank",
        "repository_id": "repository://alpha",
        "stable_version": "commit-a",
        "source_generation": "gen-a",
        "view_type": "symbol.raw",
        "locator": "code://alpha@commit-a/src/rank.py#L1-L8",
    }

    first = CodeSourceRetrieverV1Adapter.retrieval_unit_id(**fields)
    second = CodeSourceRetrieverV1Adapter.retrieval_unit_id(**dict(reversed(fields.items())))
    changed = CodeSourceRetrieverV1Adapter.retrieval_unit_id(
        **{**fields, "source_generation": "gen-b"}
    )
    decomposed = CodeSourceRetrieverV1Adapter.retrieval_unit_id(
        **{
            **fields,
            "entity_id": "code://alpha/cafe\u0301",
            "locator": "code://alpha/src/cafe\u0301.py",
        }
    )
    composed = CodeSourceRetrieverV1Adapter.retrieval_unit_id(
        **{
            **fields,
            "entity_id": "code://alpha/café",
            "locator": "code://alpha/src/café.py",
        }
    )

    assert first == second
    assert first.startswith("code-unit://raw-v1/sha256/")
    assert first != fields["entity_id"]
    assert changed != first
    assert decomposed == composed


def test_commit_alignment_uses_only_request_scope_and_returned_commit() -> None:
    exact_request = _request(commit="commit-a")
    exact_response = _response(
        exact_request,
        items=[
            _item(
                "code://alpha/exact",
                score=0.9,
                lexical_score=1.0,
                dense_score=0.0,
                channels=["lexical"],
                commit="commit-a",
            ),
            _item(
                "code://alpha/mismatch",
                score=0.8,
                lexical_score=0.8,
                dense_score=0.0,
                channels=["lexical"],
                commit="commit-b",
            ),
        ],
    )
    exact_response["trace"]["lexical_candidates"] = 2
    exact_response["trace"]["dense_candidates"] = 0
    exact_response["trace"]["dense_matches"] = 0
    exact_legacy = _LegacyRetriever(exact_response)
    exact = CodeSourceRetrieverV1Adapter(exact_legacy).adapt_response(
        exact_request, exact_legacy.executed(exact_request)
    )

    current_request = _request()
    current_response = _response(current_request, items=[_response(current_request)["results"][0]])
    current_response["trace"]["lexical_candidates"] = 1
    current_response["trace"]["dense_candidates"] = 1
    current_response["trace"]["dense_matches"] = 1
    current_legacy = _LegacyRetriever(current_response)
    current = CodeSourceRetrieverV1Adapter(current_legacy).adapt_response(
        current_request, current_legacy.executed(current_request)
    )

    assert [candidate.version_alignment for candidate in exact.candidates] == [
        CodeVersionAlignment.EXACT,
        CodeVersionAlignment.MISMATCH,
    ]
    assert exact.watermark == "commit:commit-a"
    assert current.candidates[0].version_alignment is CodeVersionAlignment.COMPATIBLE
    assert current.watermark == "current"


def test_search_passes_project_repository_commit_and_acl_scope_unchanged() -> None:
    request = _request(commit="commit-a")
    response = _response(request, items=[_response(request)["results"][0]])
    response["trace"]["lexical_candidates"] = 1
    response["trace"]["dense_candidates"] = 1
    response["trace"]["dense_matches"] = 1
    legacy = _LegacyRetriever(response)
    adapter = CodeSourceRetrieverV1Adapter(legacy)

    result = adapter.search(request, adapter.profile_for_request(request))

    assert legacy.requests == [request]
    assert legacy.requests[0] is request
    assert legacy.requests[0].scope.project_id == "project-alpha"
    assert legacy.requests[0].scope.repository_ids == ["repository://alpha"]
    assert legacy.requests[0].scope.commit == "commit-a"
    assert legacy.requests[0].scope.allowed_acl_refs == ["team:alpha"]
    assert legacy.requests[0].scope.enforce_acl is True
    assert result.candidates[0].acl_ref == "team:alpha"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(total=4),
        lambda payload: payload["results"][0].update(score=1),
        lambda payload: payload["results"][0].update(channels=["graph"]),
        lambda payload: payload["results"][0].update(generation_id="gen-other"),
        lambda payload: payload["results"][0].update(repository_id="repository://other"),
        lambda payload: payload["results"][0].update(acl_ref="team:other"),
        lambda payload: payload["resolved_scope"].update(project_id="project-other"),
        lambda payload: payload["results"][1].update(entity_id=payload["results"][0]["entity_id"]),
    ],
)
def test_invalid_or_out_of_scope_legacy_payload_fails_closed(mutate) -> None:
    request = _request()
    response = _response(request)
    legacy = _LegacyRetriever(response)
    executed = legacy.executed(request)
    mutate(executed)

    with pytest.raises(LegacyCodeResponseError):
        CodeSourceRetrieverV1Adapter(legacy).adapt_response(request, executed)


def test_adapter_requires_authentic_evidence_from_the_same_retriever_instance() -> None:
    request = _request()
    response = _response(request)
    signer = _LegacyRetriever(response)
    executed = signer.executed(request)

    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(signer).adapt_response(request, response)

    other = _LegacyRetriever(response)
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(other).adapt_response(request, executed)
    other_executed = other.executed(request)
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(signer).adapt_response(request, other_executed)

    forged = LegacySearchResponse(
        executed,
        replace(executed.execution_evidence, attestation=b"forged"),
    )
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(signer).adapt_response(request, forged)


@pytest.mark.parametrize(
    "operation",
    [
        copy.copy,
        copy.deepcopy,
        pickle.dumps,
    ],
)
def test_hybrid_retriever_authority_cannot_be_copied_or_pickled(operation) -> None:
    request = _request()
    retriever = _LegacyRetriever(_response(request))

    with pytest.raises(
        TypeError,
        match="HybridRetriever authority cannot be copied or serialized",
    ):
        operation(retriever)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("__reduce__", ()),
        ("__reduce_ex__", (pickle.HIGHEST_PROTOCOL,)),
        ("__getstate__", ()),
        ("__setstate__", ({"store": None, "embedder": None},)),
    ],
)
def test_hybrid_retriever_reduce_and_state_protocols_are_fixed_failures(
    method: str,
    args: tuple[object, ...],
) -> None:
    retriever = HybridRetriever(store=None, embedder=None)  # type: ignore[arg-type]
    with pytest.raises(
        TypeError,
        match="HybridRetriever authority cannot be copied or serialized",
    ):
        getattr(retriever, method)(*args)


def test_strict_identity_registry_ignores_equal_retriever_hash_and_equality() -> None:
    request = _request()
    payload = _response(request)
    _EqualLegacyRetriever.hash_calls = 0
    _EqualLegacyRetriever.equality_calls = 0
    first = _EqualLegacyRetriever(payload)
    second = _EqualLegacyRetriever(payload)

    first_authority = first._authority()
    second_authority = second._authority()
    assert first_authority is not second_authority
    assert first_authority.execution_evidence_key != second_authority.execution_evidence_key
    assert retrieval_module._HYBRID_AUTHORITIES[id(first)].reference() is first
    assert retrieval_module._HYBRID_AUTHORITIES[id(second)].reference() is second
    assert _EqualLegacyRetriever.hash_calls == 0
    assert _EqualLegacyRetriever.equality_calls == 0

    first_response = first.executed(request)
    second_response = second.executed(request)
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(second).adapt_response(request, first_response)
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(first).adapt_response(request, second_response)
    assert HybridRetriever(store=None, embedder=None)._authority()  # type: ignore[arg-type]
    assert _EqualLegacyRetriever.hash_calls == 0
    assert _EqualLegacyRetriever.equality_calls == 0


def test_strict_retriever_registry_supports_concurrent_registration_and_lookup() -> None:
    request = _request()
    payload = _response(request)
    retrievers: list[_EqualLegacyRetriever] = []
    failures: list[BaseException] = []
    output_lock = threading.Lock()

    def construct_and_lookup() -> None:
        try:
            retriever = _EqualLegacyRetriever(payload)
            assert retriever._authority() is retriever._authority()
            with output_lock:
                retrievers.append(retriever)
        except BaseException as error:
            with output_lock:
                failures.append(error)

    workers = [threading.Thread(target=construct_and_lookup) for _ in range(32)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert failures == []
    assert len(retrievers) == 32
    assert len({id(retriever._authority()) for retriever in retrievers}) == 32
    assert len({retriever._authority().execution_evidence_key for retriever in retrievers}) == 32


def test_retriever_registry_is_weak_and_gc_removes_the_exact_entry() -> None:
    retriever = HybridRetriever(store=None, embedder=None)  # type: ignore[arg-type]
    identity = id(retriever)
    reference = weakref.ref(retriever)
    assert retrieval_module._HYBRID_AUTHORITIES[identity].reference() is retriever

    del retriever
    gc.collect()

    assert reference() is None
    assert identity not in retrieval_module._HYBRID_AUTHORITIES


def test_stale_retriever_callback_and_unregistered_clone_cannot_remove_or_gain_authority() -> None:
    old = HybridRetriever(store=None, embedder=None)  # type: ignore[arg-type]
    current = HybridRetriever(store=None, embedder=None)  # type: ignore[arg-type]
    old_reference = retrieval_module._HYBRID_AUTHORITIES[id(old)].reference
    current_entry = retrieval_module._HYBRID_AUTHORITIES[id(current)]

    retrieval_module._remove_hybrid_authority(id(current), old_reference)

    assert retrieval_module._HYBRID_AUTHORITIES[id(current)] is current_entry
    assert current._authority() is current_entry.authority
    clone = HybridRetriever.__new__(HybridRetriever)
    clone.__dict__.update(current.__dict__)
    assert id(clone) not in retrieval_module._HYBRID_AUTHORITIES
    with pytest.raises(
        retrieval_module.LegacyExecutionEvidenceError,
        match="HybridRetriever authority is unavailable",
    ):
        clone._authority()

    collision = HybridRetriever.__new__(HybridRetriever)
    collision_identity = id(collision)
    with retrieval_module._HYBRID_AUTHORITY_LOCK:
        retrieval_module._HYBRID_AUTHORITIES[collision_identity] = (
            retrieval_module._HYBRID_AUTHORITIES[id(old)]
        )
    try:
        with pytest.raises(
            retrieval_module.LegacyExecutionEvidenceError,
            match="HybridRetriever authority is unavailable",
        ):
            retrieval_module._register_hybrid_authority(
                collision,
                current_entry.authority,
            )
    finally:
        with retrieval_module._HYBRID_AUTHORITY_LOCK:
            retrieval_module._HYBRID_AUTHORITIES.pop(collision_identity, None)


def test_copy_attempt_cannot_create_an_adapter_with_replayed_authority() -> None:
    request = _request()
    retriever = _LegacyRetriever(_response(request))
    response = retriever.executed(request)

    with pytest.raises(TypeError, match="authority cannot be copied"):
        copied = copy.copy(retriever)
        CodeSourceRetrieverV1Adapter(copied).adapt_response(request, response)


def test_copyreg_constructor_and_state_cannot_transfer_retriever_authority() -> None:
    request = _request()
    original = HybridRetriever(store=None, embedder=None)  # type: ignore[arg-type]
    response = original._attest_response(
        request,
        _response(request),
        channel_entity_ids={
            "lexical": ["code://alpha/rank", "code://alpha/normalize"],
            "dense": ["code://alpha/similarity", "code://alpha/rank"],
        },
    )
    sentinel = object()
    previous = copyreg.dispatch_table.get(HybridRetriever, sentinel)

    def reducer(value: HybridRetriever):
        return (
            HybridRetriever,
            (value.store, value.embedder),
            dict(value.__dict__),
        )

    try:
        copyreg.pickle(HybridRetriever, reducer)
        payload = pickle.dumps(original)
        with pytest.raises(
            TypeError,
            match="HybridRetriever authority cannot be copied or serialized",
        ):
            pickle.loads(payload)
        with pytest.raises(
            TypeError,
            match="HybridRetriever authority cannot be copied or serialized",
        ):
            copy.copy(original)
    finally:
        if previous is sentinel:
            del copyreg.dispatch_table[HybridRetriever]
        else:
            copyreg.dispatch_table[HybridRetriever] = previous

    assert original.verify_execution_evidence(request, response) is response.execution_evidence


def test_local_pickler_state_restore_is_rejected_without_poisoning_signer() -> None:
    request = _request()
    original = HybridRetriever(store=None, embedder=None)  # type: ignore[arg-type]
    response = original._attest_response(
        request,
        _response(request),
        channel_entity_ids={
            "lexical": ["code://alpha/rank", "code://alpha/normalize"],
            "dense": ["code://alpha/similarity", "code://alpha/rank"],
        },
    )

    def reducer(value: HybridRetriever):
        return (
            HybridRetriever,
            (value.store, value.embedder),
            dict(value.__dict__),
        )

    target = io.BytesIO()
    pickler = pickle.Pickler(target)
    pickler.dispatch_table = {HybridRetriever: reducer}
    pickler.dump(original)
    with pytest.raises(
        TypeError,
        match="HybridRetriever authority cannot be copied or serialized",
    ):
        pickle.loads(target.getvalue())
    assert original.verify_execution_evidence(request, response) is response.execution_evidence


def test_constructor_only_and_new_state_clones_never_share_attestation_authority() -> None:
    request = _request()
    original = HybridRetriever(store=None, embedder=None)  # type: ignore[arg-type]
    original_response = original._attest_response(
        request,
        _response(request),
        channel_entity_ids={
            "lexical": ["code://alpha/rank", "code://alpha/normalize"],
            "dense": ["code://alpha/similarity", "code://alpha/rank"],
        },
    )

    def independent_reducer(value: HybridRetriever):
        return HybridRetriever, (value.store, value.embedder)

    target = io.BytesIO()
    pickler = pickle.Pickler(target)
    pickler.dispatch_table = {HybridRetriever: independent_reducer}
    pickler.dump(original)
    independent = pickle.loads(target.getvalue())
    state_copied = HybridRetriever(store=None, embedder=None)  # type: ignore[arg-type]
    state_copied.__dict__.update(original.__dict__)
    unregistered = HybridRetriever.__new__(HybridRetriever)
    unregistered.__dict__.update(original.__dict__)

    for clone in (independent, state_copied, unregistered):
        with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
            CodeSourceRetrieverV1Adapter(clone).adapt_response(request, original_response)

    independent_response = independent._attest_response(
        request,
        _response(request),
        channel_entity_ids={
            "lexical": ["code://alpha/rank", "code://alpha/normalize"],
            "dense": ["code://alpha/similarity", "code://alpha/rank"],
        },
    )
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(original).adapt_response(request, independent_response)
    assert set(original.__dict__) == {"store", "embedder"}


def test_attested_response_deepcopy_keeps_evidence_but_remains_fail_closed() -> None:
    request = _request()
    retriever = _LegacyRetriever(_response(request))
    executed = retriever.executed(request)
    copied = copy.deepcopy(executed)

    result = CodeSourceRetrieverV1Adapter(retriever).adapt_response(request, copied)
    assert [candidate.entity_id for candidate in result.candidates] == [
        item["entity_id"] for item in executed["results"]
    ]

    plain_copy = copy.deepcopy(dict(executed))
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(retriever).adapt_response(request, plain_copy)

    copied["results"][0]["snippet"] = "mutated copied payload"
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(retriever).adapt_response(request, copied)

    evidence_mutated = LegacySearchResponse(
        executed,
        replace(
            executed.execution_evidence,
            response_tag=bytes([executed.execution_evidence.response_tag[0] ^ 1])
            + executed.execution_evidence.response_tag[1:],
        ),
    )
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(retriever).adapt_response(
            request,
            evidence_mutated,
        )


@pytest.mark.parametrize(
    "scope",
    [
        SearchScope(
            project_id="project-alpha",
            repository_ids=["repository://alpha"],
            allowed_acl_refs=["team:alpha"],
            enforce_acl=False,
        ),
        SearchScope(
            project_id="project-alpha",
            repository_ids=["repository://alpha"],
            allowed_acl_refs=["team:beta"],
            enforce_acl=True,
        ),
    ],
)
def test_hidden_acl_execution_scope_cannot_replay_under_same_public_scope(
    scope: SearchScope,
) -> None:
    signed_request = _request()
    response = _response(signed_request)
    legacy = _LegacyRetriever(response)
    executed = legacy.executed(signed_request)
    replay_request = EvidenceSearchRequest(
        query=signed_request.query,
        scope=scope,
        limit=signed_request.limit,
        include_edges=signed_request.include_edges,
    )

    assert replay_request.scope.model_dump() == signed_request.scope.model_dump()
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(legacy).adapt_response(replay_request, executed)


def test_attested_pruned_hits_are_not_reported_as_no_match() -> None:
    request = _request()
    response = _response(
        request,
        items=[
            _item(
                "code://alpha/dense-only",
                score=0.8,
                lexical_score=0.0,
                dense_score=0.7,
                channels=["dense"],
            )
        ],
    )
    response["trace"].update(
        lexical_candidates=3,
        dense_candidates=1,
        dense_matches=1,
    )
    legacy = _LegacyRetriever(response)
    executed = legacy.executed(
        request,
        channel_entity_ids={
            "lexical": [
                "code://hidden/lexical/0",
                "code://hidden/lexical/1",
                "code://hidden/lexical/2",
            ],
            "dense": ["code://alpha/dense-only"],
        },
    )

    result = CodeSourceRetrieverV1Adapter(legacy).adapt_response(request, executed)
    outcomes = _outcomes_by_channel(result)

    assert isinstance(outcomes[CodeRetrievalChannel.SPARSE], CodeChannelCompletePruned)
    assert outcomes[CodeRetrievalChannel.SPARSE].hit_count == 3
    assert isinstance(outcomes[CodeRetrievalChannel.DENSE], CodeChannelCompleteWithHits)


def test_attested_raw_rank_preserves_gap_with_top_k_one_and_tied_scores() -> None:
    request = _request().model_copy(update={"limit": 1})
    returned_id = "code://alpha/returned"
    response = _response(
        request,
        items=[
            _item(
                returned_id,
                score=0.8,
                lexical_score=0.5,
                dense_score=0.0,
                channels=["lexical"],
            )
        ],
    )
    response["trace"].update(
        lexical_candidates=3,
        dense_candidates=0,
        dense_matches=0,
    )
    legacy = _LegacyRetriever(response)
    executed = legacy.executed(
        request,
        channel_entity_ids={
            "lexical": [
                "code://alpha/tied-first",
                returned_id,
                "code://alpha/tied-last",
            ],
            "dense": [],
        },
    )

    result = CodeSourceRetrieverV1Adapter(legacy).adapt_response(request, executed)

    assert result.candidates[0].raw_channel_ranks[0].rank == 1
    assert _outcomes_by_channel(result)[CodeRetrievalChannel.SPARSE].hit_count == 3


def test_trace_count_and_response_mutation_after_execution_fail_closed() -> None:
    request = _request()
    legacy = _LegacyRetriever(_response(request))
    trace_mutated = legacy.executed(request)
    trace_mutated["trace"]["lexical_candidates"] = 999
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(legacy).adapt_response(request, trace_mutated)

    payload_mutated = legacy.executed(request)
    payload_mutated["results"][0]["snippet"] = "tampered after retrieval"
    with pytest.raises(LegacyCodeResponseError, match="evidence verification"):
        CodeSourceRetrieverV1Adapter(legacy).adapt_response(request, payload_mutated)


def test_platform_rejects_adapter_or_shadow_bound_to_a_different_legacy_instance(
    settings,
) -> None:
    runtime = create_runtime(settings)
    other_runtime = create_runtime(settings)
    from evidence_rag.platform.service import PlatformService

    with pytest.raises(ValueError, match="endpoint legacy retriever"):
        PlatformService(
            runtime.platform.store,
            runtime.retriever,
            runtime.codex_retriever,
            code_v1_adapter=other_runtime.code_v1_adapter,
        )

    mismatched_shadow = other_runtime.code_shadow
    if mismatched_shadow is None:
        from evidence_rag.rag.sources.code import CodeShadowRunner

        mismatched_shadow = CodeShadowRunner(
            other_runtime.code_v1_adapter,
            timeout_ms=150.0,
        )
    with pytest.raises(ValueError, match="shadow runner"):
        PlatformService(
            runtime.platform.store,
            runtime.retriever,
            runtime.codex_retriever,
            code_v1_adapter=runtime.code_v1_adapter,
            code_shadow=mismatched_shadow,
        )
    mismatched_shadow.close()


def test_runtime_wires_legacy_adapter_and_optional_shadow(settings) -> None:
    legacy_runtime = create_runtime(settings)
    assert legacy_runtime.code_v1_adapter.legacy is legacy_runtime.retriever
    assert legacy_runtime.code_shadow is None
    assert legacy_runtime.platform.code is legacy_runtime.retriever
    assert legacy_runtime.platform.code_shadow is None

    shadow_settings = replace(
        settings,
        rag_code_engine="v2",
        rag_code_shadow=True,
    )
    shadow_runtime = create_runtime(shadow_settings)
    assert shadow_runtime.settings.rag_code_engine == "v2"
    assert shadow_runtime.code_v1_adapter.legacy is shadow_runtime.retriever
    assert shadow_runtime.code_shadow is not None
    assert shadow_runtime.platform.code is shadow_runtime.retriever
    assert shadow_runtime.platform.code_shadow is shadow_runtime.code_shadow
    shadow_runtime.code_shadow.close()
