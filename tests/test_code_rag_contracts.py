from __future__ import annotations

import ast
import math
from dataclasses import fields, replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.config import Settings
from evidence_rag.rag.sources.code import (
    CodeCalibratedScore,
    CodeChannelCompleteNoMatch,
    CodeChannelCompletePruned,
    CodeChannelCompleteWithHits,
    CodeChannelDisabled,
    CodeChannelError,
    CodeChannelFailure,
    CodeChannelRank,
    CodeChannelScore,
    CodeChannelTimeout,
    CodeChannelUnavailable,
    CodeContextBlock,
    CodeDerivation,
    CodeFactStatus,
    CodeFallbackFailed,
    CodeFallbackFailure,
    CodeFallbackSucceeded,
    CodeQueryProfile,
    CodeRelationHop,
    CodeRelationLocator,
    CodeRelationNode,
    CodeRelationPath,
    CodeRetrievalBudget,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeReviewStatus,
    CodeSourceResult,
    CodeSourceStatus,
    CodeUncalibratedScore,
)
from evidence_rag.rag.sources.code import contracts as code_contracts

_CODE_ENV_NAMES = (
    "RAG_CODE_ENGINE",
    "RAG_CODE_SHADOW",
    "RAG_CODE_UNIT_BUILDER",
    "RAG_CODE_EMBEDDING_PROFILE",
    "RAG_CODE_DENSE_INDEX",
    "RAG_CODE_GRAPH",
    "RAG_CODE_SEMANTIC_RESOLVER",
    "RAG_CODE_RERANKER",
    "RAG_CODE_CONTEXT",
    "RAG_CODE_CANARY_PERCENT",
)


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        data_dir=data_dir,
        database_path=data_dir / "test.sqlite3",
        repository_cache=data_dir / "repositories",
        web_dir=tmp_path / "web",
        allowed_local_roots=(tmp_path,),
    )


def _graph_budget() -> CodeRetrievalBudget:
    return CodeRetrievalBudget(
        total_candidates=120,
        exact_candidates=20,
        sparse_candidates=40,
        dense_candidates=40,
        graph_candidates=40,
        history_candidates=20,
        test_candidates=20,
        graph_node_budget=120,
        graph_edge_budget=240,
        context_token_budget=4_000,
    )


def _node(
    name: str,
    *,
    repository_id: str = "repository://repo",
    version: str = "abc",
    generation: str = "generation://code-v1",
    acl_ref: str = "project:project-rag",
    locator: str | None = None,
    entity_id: str | None = None,
) -> CodeRelationNode:
    return CodeRelationNode(
        entity_id=entity_id or f"code://repo@{version}/src/{name}.py#symbol={name}",
        repository_id=repository_id,
        stable_version=version,
        source_generation=generation,
        locator=locator or f"code://repo@{version}/src/{name}.py#L1-L8",
        acl_ref=acl_ref,
    )


def _hop(
    source: CodeRelationNode,
    target: CodeRelationNode,
    *,
    edge_type: str = "CALLS",
    direction: str = "outgoing",
    hop: int = 1,
    **updates: object,
) -> CodeRelationHop:
    payload: dict[str, object] = {
        "source_entity_id": source.entity_id,
        "target_entity_id": target.entity_id,
        "source_repository_id": source.repository_id,
        "target_repository_id": target.repository_id,
        "edge_type": edge_type,
        "direction": direction,
        "hop": hop,
        "confidence": 0.95,
        "source_version": source.stable_version,
        "target_version": target.stable_version,
        "source_generation": source.source_generation,
        "target_generation": target.source_generation,
        "source_acl_ref": source.acl_ref,
        "target_acl_ref": target.acl_ref,
        "derivation": "tree_sitter",
        "review_status": "machine_confirmed",
        "fact_status": "machine_confirmed",
        "locator": {
            "locator": source.locator,
            "owner_entity_id": source.entity_id,
            "repository_id": source.repository_id,
            "stable_version": source.stable_version,
            "source_generation": source.source_generation,
            "acl_ref": source.acl_ref,
        },
    }
    payload.update(updates)
    return CodeRelationHop.model_validate(payload)


def _direct_path(
    entity_id: str = "code://repo@abc/src/ranking.py#symbol=rank",
    *,
    repository_id: str = "repository://repo",
    version: str = "abc",
    generation: str = "generation://code-v1",
    acl_ref: str = "project:project-rag",
    locator: str = "code://repo@abc/src/ranking.py#L20-L28",
) -> CodeRelationPath:
    return CodeRelationPath(
        nodes=[
            CodeRelationNode(
                entity_id=entity_id,
                repository_id=repository_id,
                stable_version=version,
                source_generation=generation,
                locator=locator,
                acl_ref=acl_ref,
            )
        ],
        path_score=0.0,
    )


def _outgoing_path() -> CodeRelationPath:
    source = _node("search")
    target = _node(
        "rank",
        entity_id="code://repo@abc/src/ranking.py#symbol=rank",
        locator="code://repo@abc/src/ranking.py#L20-L28",
    )
    return CodeRelationPath(
        nodes=[source, target],
        edges=[_hop(source, target)],
        path_score=0.68,
    )


def _candidate_payload(
    *,
    path: CodeRelationPath | None = None,
    score_order: tuple[str, ...] = ("exact", "sparse"),
    rank_order: tuple[str, ...] = ("sparse", "exact"),
) -> dict[str, object]:
    relation_path = path or _direct_path()
    terminal = relation_path.nodes[-1]
    scores = {"exact": 1.0, "sparse": 0.75}
    ranks = {"exact": 0, "sparse": 0}
    return {
        "entity_id": terminal.entity_id,
        "retrieval_unit_id": "unit://sha256/1234",
        "repository_id": terminal.repository_id,
        "entity_type": "CodeSymbol",
        "stable_version": terminal.stable_version,
        "source_generation": terminal.source_generation,
        "raw_channel_scores": [
            {"channel": channel, "score": scores[channel]} for channel in score_order
        ],
        "raw_channel_ranks": [
            {"channel": channel, "rank": ranks[channel]} for channel in rank_order
        ],
        "within_source_rank": 0,
        "source_fused_score": 0.91,
        "calibrated_relevance": {
            "status": "calibrated",
            "score": 0.87,
            "calibration_version": "code-calibration-v1",
        },
        "version_alignment": "exact",
        "fact_status": "machine_confirmed",
        "derivation": "tree_sitter",
        "review_status": "machine_confirmed",
        "role": "target",
        "relation_path": relation_path.model_dump(mode="json"),
        "locator": terminal.locator,
        "token_estimate": 96,
        "acl_ref": terminal.acl_ref,
    }


def _candidate(**updates: object) -> CodeRetrievalCandidate:
    payload = _candidate_payload()
    payload.update(updates)
    return CodeRetrievalCandidate.model_validate(payload)


def _candidate_for_path(
    path: CodeRelationPath,
    **updates: object,
) -> CodeRetrievalCandidate:
    payload = _candidate_payload(path=path)
    payload.update(updates)
    return CodeRetrievalCandidate.model_validate(payload)


def _context(
    candidate: CodeRetrievalCandidate,
    *,
    block_id: str = "code-context://query-1/target-1",
    content: str = "def rank(items):\n    return sorted(items)\n",
    **updates: object,
) -> CodeContextBlock:
    payload: dict[str, object] = {
        "block_id": block_id,
        "entity_id": candidate.entity_id,
        "retrieval_unit_id": candidate.retrieval_unit_id,
        "repository_id": candidate.repository_id,
        "role": candidate.role,
        "content": content,
        "stable_version": candidate.stable_version,
        "source_generation": candidate.source_generation,
        "relation_path": candidate.relation_path.model_dump(mode="json"),
        "locator": candidate.locator,
        "token_estimate": 18,
        "acl_ref": candidate.acl_ref,
    }
    payload.update(updates)
    return CodeContextBlock.model_validate(payload)


def _outcomes(
    overrides: dict[CodeRetrievalChannel | str, object] | None = None,
) -> list[object]:
    replacements = {
        CodeRetrievalChannel(channel): value for channel, value in (overrides or {}).items()
    }
    return [
        replacements.get(
            channel,
            CodeChannelDisabled(channel=channel, reason="not requested by profile"),
        )
        for channel in CodeRetrievalChannel
    ]


def _hit_outcomes() -> list[object]:
    return _outcomes(
        {
            "exact": CodeChannelCompleteWithHits(channel="exact", hit_count=1),
            "sparse": CodeChannelCompleteWithHits(channel="sparse", hit_count=1),
        }
    )


def _no_match_outcomes() -> list[object]:
    return _outcomes(
        {
            "exact": CodeChannelCompleteNoMatch(channel="exact"),
            "sparse": CodeChannelCompleteNoMatch(channel="sparse"),
        }
    )


def _channel_failure(
    channel: str,
    kind: str,
    message: str,
) -> CodeChannelFailure:
    return CodeChannelFailure(channel=channel, kind=kind, message=message)


def _result(**updates: object) -> CodeSourceResult:
    payload: dict[str, object] = {
        "query_id": "code-query://1",
        "status": "complete",
        "channel_outcomes": _no_match_outcomes(),
        "candidates": [],
        "context_blocks": [],
        "index_version": "code-index-v1",
        "watermark": "repository://repo@abc",
        "latency_ms": 0.0,
        "errors": [],
    }
    payload.update(updates)
    return CodeSourceResult.model_validate(payload)


def _nested_values(value: object) -> list[object]:
    values = [value]
    if isinstance(value, dict):
        for item in value.values():
            values.extend(_nested_values(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.extend(_nested_values(item))
    return values


def test_contracts_round_trip_and_canonical_hash_without_null_sentinels() -> None:
    profile = CodeQueryProfile(
        task="impact_analysis",
        target_identifiers=["EdgeRecord"],
        target_paths=["src/evidence_rag/models.py"],
        target_ref="abc",
        directions=["incoming"],
        edge_types=["REFERENCES", "TYPE_OF", "CALLS", "TESTS"],
        max_hops=3,
        require_tests=True,
        include_history=True,
        budget=_graph_budget(),
    )
    candidate = _candidate_for_path(_outgoing_path())
    context = _context(candidate)
    result = _result(
        candidates=[candidate],
        context_blocks=[context],
        channel_outcomes=_hit_outcomes(),
        latency_ms=12.5,
    )

    for model in (profile, candidate, context, result):
        assert type(model).model_validate_json(model.model_dump_json()) == model
        assert None not in _nested_values(model.model_dump(mode="json"))
        assert len(model.canonical_sha256()) == 64


def test_query_profile_rejects_invalid_types_enums_whitespace_and_duplicates() -> None:
    with pytest.raises(ValidationError, match="task"):
        CodeQueryProfile(task="invented")
    with pytest.raises(ValidationError, match="max_hops"):
        CodeQueryProfile(task="implementation", max_hops="2")
    with pytest.raises(ValidationError, match="require_tests"):
        CodeQueryProfile(task="implementation", require_tests=1)
    with pytest.raises(ValidationError, match="less than or equal to 4"):
        CodeQueryProfile(task="implementation", max_hops=5)
    with pytest.raises(ValidationError, match="leading or trailing"):
        CodeQueryProfile(task="implementation", target_identifiers=[" rank"])
    with pytest.raises(ValidationError, match="non-ASCII or control whitespace"):
        CodeQueryProfile(task="implementation", target_identifiers=["rank\u00a0symbol"])
    with pytest.raises(ValidationError, match="canonical duplicates"):
        CodeQueryProfile(task="implementation", target_identifiers=["rank", "rank"])
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CodeQueryProfile.model_validate({"task": "implementation", "source": "code"})


def test_profile_graph_controls_are_bidirectional_and_allow_four_hops() -> None:
    with pytest.raises(ValidationError, match="zero-hop"):
        CodeQueryProfile(
            task="implementation",
            budget=CodeRetrievalBudget(
                graph_candidates=10,
                graph_node_budget=10,
                graph_edge_budget=20,
            ),
        )
    with pytest.raises(ValidationError, match="zero-hop"):
        CodeQueryProfile(
            task="implementation",
            directions=["outgoing"],
            edge_types=["CALLS"],
        )
    with pytest.raises(ValidationError, match="positive-hop"):
        CodeQueryProfile(task="implementation", max_hops=1)
    with pytest.raises(ValidationError, match="active graph budgets"):
        CodeQueryProfile(
            task="implementation",
            max_hops=1,
            directions=["outgoing"],
            edge_types=["CALLS"],
        )

    profile = CodeQueryProfile(
        task="call_path",
        max_hops=4,
        directions=["incoming", "outgoing"],
        edge_types=["CALLS", "REFERENCES"],
        budget=CodeRetrievalBudget(
            graph_candidates=40,
            graph_node_budget=120,
            graph_edge_budget=240,
        ),
    )
    assert profile.max_hops == 4
    assert profile.directions == ("incoming", "outgoing")


def test_budget_rejects_negative_wrong_type_and_inconsistent_graph_limits() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        CodeRetrievalBudget(total_candidates=-1)
    with pytest.raises(ValidationError, match="exact_candidates"):
        CodeRetrievalBudget(exact_candidates="20")
    with pytest.raises(ValidationError, match="must not exceed"):
        CodeRetrievalBudget(total_candidates=10, exact_candidates=11)
    with pytest.raises(ValidationError, match="non-zero graph candidate"):
        CodeRetrievalBudget(graph_node_budget=10)
    with pytest.raises(ValidationError, match="non-zero graph node and edge"):
        CodeRetrievalBudget(graph_candidates=10)


def test_outgoing_and_incoming_multi_hop_paths_validate_all_endpoint_provenance() -> None:
    a, b, c = (_node(name) for name in ("a", "b", "c"))
    outgoing = CodeRelationPath(
        nodes=[a, b, c],
        edges=[_hop(a, b, hop=1), _hop(b, c, hop=2)],
    )
    incoming = CodeRelationPath(
        nodes=[c, b, a],
        edges=[
            _hop(b, c, direction="incoming", hop=1),
            _hop(a, b, direction="incoming", hop=2),
        ],
    )

    assert outgoing.nodes[-1] == c
    assert incoming.nodes[-1] == a


def test_path_rejects_endpoint_direction_hop_and_node_edge_mismatch() -> None:
    source, target = _node("source"), _node("target")
    edge = _hop(source, target).model_dump(mode="json")
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        CodeRelationHop.model_validate({**edge, "hop": 0})
    with pytest.raises(ValidationError, match="direction"):
        CodeRelationHop.model_validate({**edge, "direction": "sideways"})
    with pytest.raises(ValidationError, match="exactly one edge"):
        CodeRelationPath(nodes=[source, target], edges=[])
    with pytest.raises(ValidationError, match="contiguous"):
        CodeRelationPath(
            nodes=[source, target],
            edges=[CodeRelationHop.model_validate({**edge, "hop": 2})],
        )
    with pytest.raises(ValidationError, match="does not match governed nodes"):
        CodeRelationPath(
            nodes=[source, target],
            edges=[CodeRelationHop.model_validate({**edge, "direction": "incoming"})],
        )


def test_ordinary_cycle_and_lineage_cycle_are_rejected() -> None:
    a, b = _node("a"), _node("b")
    with pytest.raises(ValidationError, match="repeat the same governed node"):
        CodeRelationPath(
            nodes=[a, b, a],
            edges=[_hop(a, b, hop=1), _hop(b, a, hop=2)],
        )

    lineage_id = "code-symbol://repo/rank"
    v1 = _node(
        "rank",
        version="abc",
        generation="generation://v1",
        entity_id=lineage_id,
    )
    v2 = _node(
        "rank",
        version="def",
        generation="generation://v2",
        entity_id=lineage_id,
    )
    with pytest.raises(ValidationError, match="repeat the same governed node"):
        CodeRelationPath(
            nodes=[v1, v2, v1],
            edges=[
                _hop(v1, v2, edge_type="SAME_SYMBOL_AS", hop=1),
                _hop(v2, v1, edge_type="SAME_SYMBOL_AS", hop=2),
            ],
        )


def test_lineage_transition_is_explicit_and_ordinary_edges_cannot_cross_versions() -> None:
    lineage_id = "code-symbol://repo/rank"
    v1 = _node(
        "rank",
        version="abc",
        generation="generation://v1",
        entity_id=lineage_id,
    )
    v2 = _node(
        "rank",
        version="def",
        generation="generation://v2",
        entity_id=lineage_id,
    )
    lineage = CodeRelationPath(
        nodes=[v1, v2],
        edges=[_hop(v1, v2, edge_type="SAME_SYMBOL_AS")],
    )
    assert lineage.edges[0].source_version == "abc"
    assert lineage.edges[0].target_version == "def"

    with pytest.raises(ValidationError, match="cannot cross stable versions"):
        _hop(v1, v2, edge_type="CALLS")
    with pytest.raises(ValidationError, match="explicit version transition"):
        _hop(v1, replace_node(v2, stable_version="abc"), edge_type="RENAMED_TO")


def replace_node(node: CodeRelationNode, **updates: object) -> CodeRelationNode:
    return node.model_copy(update=updates)


def test_calls_or_references_cannot_bypass_repeated_entity_lineage_rule() -> None:
    entity_id = "code-symbol://repo/rank"
    v1 = _node(
        "rank",
        version="abc",
        generation="generation://v1",
        entity_id=entity_id,
    )
    helper = _node("helper", version="abc", generation="generation://v1")
    v2 = _node(
        "rank",
        version="def",
        generation="generation://v2",
        entity_id=entity_id,
    )
    with pytest.raises(ValidationError, match="all-lineage"):
        CodeRelationPath(
            nodes=[v1, helper, v2],
            edges=[
                _hop(v1, helper, edge_type="CALLS", hop=1),
                _hop(helper, v2, edge_type="SAME_SYMBOL_AS", hop=2),
            ],
        )


def test_path_rejects_generation_acl_and_node_hop_provenance_mismatch() -> None:
    source, target = _node("source"), _node("target")
    with pytest.raises(ValidationError, match="cannot cross source generations"):
        _hop(source, target, target_generation="generation://other")
    with pytest.raises(ValidationError, match="authorized ACL boundary"):
        _hop(source, target, target_acl_ref="project:private")

    edge = _hop(source, target)
    changed_target = target.model_copy(update={"source_generation": "generation://other"})
    with pytest.raises(ValidationError, match="does not match governed nodes"):
        CodeRelationPath(nodes=[source, changed_target], edges=[edge])


def test_hop_locator_owner_and_repository_provenance_are_enforced() -> None:
    source, target = _node("source"), _node("target")
    valid = _hop(source, target)
    locator = valid.locator.model_dump(mode="json")

    for field, value in (
        ("owner_entity_id", "code://repo@abc/src/other.py#symbol=other"),
        ("repository_id", "repository://other"),
        ("stable_version", "def"),
        ("source_generation", "generation://other"),
        ("acl_ref", "project:private"),
    ):
        with pytest.raises(ValidationError, match="stored source endpoint"):
            _hop(source, target, locator={**locator, field: value})

    with pytest.raises(ValidationError, match="same repository"):
        _hop(source, target, target_repository_id="repository://other")

    changed_target = target.model_copy(update={"repository_id": "repository://other"})
    with pytest.raises(ValidationError, match="does not match governed nodes"):
        CodeRelationPath(nodes=[source, changed_target], edges=[valid])


def test_path_revalidates_locator_owner_and_incoming_uses_stored_source() -> None:
    source, target = _node("source"), _node("target")
    incoming = _hop(source, target, direction="incoming")
    path = CodeRelationPath(nodes=[target, source], edges=[incoming])
    assert path.edges[0].locator.owner_entity_id == source.entity_id
    assert path.edges[0].locator.owner_entity_id != path.nodes[0].entity_id

    traversal_left_owner = {
        "locator": target.locator,
        "owner_entity_id": target.entity_id,
        "repository_id": target.repository_id,
        "stable_version": target.stable_version,
        "source_generation": target.source_generation,
        "acl_ref": target.acl_ref,
    }
    with pytest.raises(ValidationError, match="stored source endpoint"):
        _hop(
            source,
            target,
            direction="incoming",
            locator=traversal_left_owner,
        )

    bad_locator = incoming.locator.model_copy(update={"repository_id": "repository://other"})
    trusted_invalid_hop = CodeRelationHop.model_construct(
        **{
            **incoming.model_dump(mode="python"),
            "locator": bad_locator,
        }
    )
    with pytest.raises(ValidationError, match="stored source endpoint"):
        CodeRelationPath(nodes=[target, source], edges=[trusted_invalid_hop])
    trusted_invalid_path = CodeRelationPath.model_construct(
        nodes=(target, source),
        edges=(trusted_invalid_hop,),
        path_score=0.0,
    )
    with pytest.raises(ValueError, match="path stored source node"):
        trusted_invalid_path.validate_path()


def test_four_hop_path_boundary_is_accepted_and_five_hops_are_rejected() -> None:
    nodes = [_node(f"n{index}") for index in range(5)]
    path = CodeRelationPath(
        nodes=nodes,
        edges=[_hop(nodes[index], nodes[index + 1], hop=index + 1) for index in range(4)],
    )
    assert len(path.edges) == 4

    sixth = _node("n5")
    with pytest.raises(ValidationError, match="at most 5 items|too_long"):
        CodeRelationPath(
            nodes=[*nodes, sixth],
            edges=[
                *path.edges,
                _hop(nodes[-1], sixth, hop=4),
            ],
        )


def test_candidate_terminal_entity_version_generation_locator_and_acl_must_align() -> None:
    candidate = _candidate_for_path(_outgoing_path())
    assert candidate.entity_id == candidate.relation_path.nodes[-1].entity_id

    for field, value in (
        ("entity_id", "code://repo@abc/other"),
        ("repository_id", "repository://other"),
        ("stable_version", "def"),
        ("source_generation", "generation://other"),
        ("locator", "code://repo@abc/other#L1"),
        ("acl_ref", "project:private"),
    ):
        with pytest.raises(ValidationError, match="must match terminal node"):
            _candidate_for_path(candidate.relation_path, **{field: value})


def test_raw_channel_entries_are_deeply_frozen_and_canonical() -> None:
    first = CodeRetrievalCandidate.model_validate(
        _candidate_payload(
            score_order=("exact", "sparse"),
            rank_order=("exact", "sparse"),
        )
    )
    second = CodeRetrievalCandidate.model_validate(
        _candidate_payload(
            score_order=("sparse", "exact"),
            rank_order=("sparse", "exact"),
        )
    )

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
    assert first.canonical_json_bytes() == second.canonical_json_bytes()
    assert first.canonical_sha256() == second.canonical_sha256()
    assert [entry.channel for entry in first.raw_channel_scores] == ["exact", "sparse"]
    with pytest.raises(AttributeError):
        first.raw_channel_scores.clear()
    with pytest.raises(ValidationError, match="frozen"):
        first.raw_channel_scores[0].score = 0.0


def test_candidate_copy_update_revalidates_nested_invariants() -> None:
    candidate = _candidate()
    copied = candidate.model_copy(update={"source_fused_score": 0.5})
    assert copied.source_fused_score == 0.5

    with pytest.raises(ValidationError, match="at least 1 item"):
        candidate.model_copy(update={"raw_channel_scores": []})
    with pytest.raises(ValidationError, match="exactly the same channels"):
        candidate.model_copy(
            update={
                "raw_channel_ranks": [
                    CodeChannelRank(channel="exact", rank=0),
                ]
            }
        )


def test_deprecated_copy_is_disabled_for_gate_sensitive_contracts() -> None:
    candidate = _candidate()
    path = candidate.relation_path
    context = _context(candidate)
    outcome = CodeChannelCompleteWithHits(channel="exact", hit_count=1)
    result = _result()

    for model, update in (
        (candidate, {"raw_channel_scores": ()}),
        (path, {"nodes": ()}),
        (context, {"repository_id": "repository://other"}),
        (outcome, {"hit_count": 0}),
        (result, {"channel_outcomes": ()}),
    ):
        with pytest.raises(TypeError, match=r"copy\(\) is disabled"):
            model.copy(update=update)


def test_model_copy_revalidates_all_gate_sensitive_contracts() -> None:
    candidate = _candidate()
    path = candidate.relation_path
    context = _context(candidate)
    outcome = CodeChannelCompleteWithHits(channel="exact", hit_count=1)
    result = _result()

    with pytest.raises(ValidationError, match="at least 1 item"):
        candidate.model_copy(update={"raw_channel_scores": ()})
    with pytest.raises(ValidationError, match="at least 1 item"):
        path.model_copy(update={"nodes": ()})
    with pytest.raises(ValidationError, match="must match terminal node"):
        context.model_copy(update={"repository_id": "repository://other"})
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        outcome.model_copy(update={"hit_count": 0})
    with pytest.raises(ValidationError, match="at least 6 items"):
        result.model_copy(update={"channel_outcomes": ()})


def test_candidate_rejects_unknown_duplicate_mismatched_and_invalid_raw_channels() -> None:
    with pytest.raises(ValidationError, match="channel"):
        _candidate(
            raw_channel_scores=[{"channel": "unknown", "score": 1.0}],
            raw_channel_ranks=[{"channel": "unknown", "rank": 0}],
        )
    with pytest.raises(ValidationError, match="must not repeat"):
        _candidate(
            raw_channel_scores=[
                {"channel": "exact", "score": 1.0},
                {"channel": "exact", "score": 0.5},
            ]
        )
    with pytest.raises(ValidationError, match="exactly the same channels"):
        _candidate(raw_channel_ranks=[{"channel": "exact", "rank": 0}])
    with pytest.raises(ValidationError, match="score"):
        _candidate(
            raw_channel_scores=[
                {"channel": "exact", "score": True},
                {"channel": "sparse", "score": 0.5},
            ]
        )
    with pytest.raises(ValidationError, match="rank"):
        _candidate(
            raw_channel_ranks=[
                {"channel": "exact", "rank": True},
                {"channel": "sparse", "rank": 2},
            ]
        )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_all_numeric_contracts_reject_nan_and_infinity(value: float) -> None:
    source, target = _node("source"), _node("target")
    with pytest.raises(ValidationError, match="finite number"):
        CodeChannelScore(channel="exact", score=value)
    with pytest.raises(ValidationError, match="finite number"):
        _candidate(source_fused_score=value)
    with pytest.raises(ValidationError, match="finite number"):
        CodeCalibratedScore(score=value, calibration_version="calibration-v1")
    with pytest.raises(ValidationError, match="finite number"):
        _hop(source, target, confidence=value)
    with pytest.raises(ValidationError, match="finite number"):
        CodeRelationPath(nodes=[source], path_score=value)
    with pytest.raises(ValidationError, match="finite number"):
        _result(latency_ms=value)


@pytest.mark.parametrize("value", [1, True, "1.0"])
def test_all_float_contract_fields_require_exact_float_input(value: object) -> None:
    source, target = _node("source"), _node("target")
    with pytest.raises(ValidationError, match="exact float"):
        CodeChannelScore(channel="exact", score=value)
    with pytest.raises(ValidationError, match="exact float"):
        _candidate(source_fused_score=value)
    with pytest.raises(ValidationError, match="exact float"):
        CodeCalibratedScore(score=value, calibration_version="calibration-v1")
    with pytest.raises(ValidationError, match="exact float"):
        _hop(source, target, confidence=value)
    with pytest.raises(ValidationError, match="exact float"):
        CodeRelationPath(nodes=[source], path_score=value)
    with pytest.raises(ValidationError, match="exact float"):
        _result(latency_ms=value)


def test_negative_zero_is_canonicalized_across_all_float_contract_fields() -> None:
    source, target = _node("source"), _node("target")
    negative_models = (
        CodeChannelScore(channel="exact", score=-0.0),
        _candidate(source_fused_score=-0.0),
        CodeCalibratedScore(score=-0.0, calibration_version="calibration-v1"),
        _hop(source, target, confidence=-0.0),
        CodeRelationPath(nodes=[source], path_score=-0.0),
        _result(latency_ms=-0.0),
    )
    positive_models = (
        CodeChannelScore(channel="exact", score=0.0),
        _candidate(source_fused_score=0.0),
        CodeCalibratedScore(score=0.0, calibration_version="calibration-v1"),
        _hop(source, target, confidence=0.0),
        CodeRelationPath(nodes=[source], path_score=0.0),
        _result(latency_ms=0.0),
    )

    for negative, positive in zip(negative_models, positive_models, strict=True):
        assert negative == positive
        assert negative.canonical_json_bytes() == positive.canonical_json_bytes()
        assert negative.canonical_sha256() == positive.canonical_sha256()


def test_candidate_rejects_identity_score_rank_token_and_calibration_violations() -> None:
    payload = _candidate_payload()
    with pytest.raises(ValidationError, match="must differ"):
        _candidate(retrieval_unit_id=payload["entity_id"])
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _candidate(source_fused_score=-0.01)
    with pytest.raises(ValidationError, match="token_estimate"):
        _candidate(token_estimate=True)
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        _candidate(
            calibrated_relevance={
                "status": "calibrated",
                "score": 1.01,
                "calibration_version": "calibration-v1",
            }
        )
    with pytest.raises(ValidationError, match="version_alignment"):
        _candidate(version_alignment="probably")


def test_candidate_calibration_has_explicit_non_null_disabled_and_unavailable_states() -> None:
    disabled = _candidate(
        calibrated_relevance={
            "status": "disabled",
            "reason": "rag_code_reranker=off",
        }
    )
    unavailable = _candidate(
        calibrated_relevance={
            "status": "unavailable",
            "reason": "calibration artifact is not loaded",
        }
    )
    assert isinstance(disabled.calibrated_relevance, CodeUncalibratedScore)
    assert isinstance(unavailable.calibrated_relevance, CodeUncalibratedScore)
    assert "score" not in disabled.model_dump(mode="json")["calibrated_relevance"]
    assert None not in _nested_values(disabled.model_dump(mode="json"))


def test_candidate_preserves_fact_derivation_review_acl_and_version_separately() -> None:
    candidate = _candidate(
        fact_status=CodeFactStatus.INFERRED,
        derivation=CodeDerivation.SCIP,
        review_status=CodeReviewStatus.UNREVIEWED,
        version_alignment="historical",
    )
    assert candidate.fact_status is CodeFactStatus.INFERRED
    assert candidate.derivation is CodeDerivation.SCIP
    assert candidate.review_status is CodeReviewStatus.UNREVIEWED
    assert candidate.version_alignment == "historical"
    assert candidate.stable_version == "abc"
    assert candidate.source_generation == "generation://code-v1"
    assert candidate.acl_ref == "project:project-rag"


def test_context_content_requires_substance_but_preserves_code_layout() -> None:
    candidate = _candidate()
    with pytest.raises(ValidationError, match="non-whitespace"):
        _context(candidate, content=" \n\t\u00a0")
    with pytest.raises(ValidationError, match="control character"):
        _context(candidate, content="rank\x00")

    content = "\n\tdef rank(items):\n\t    return items\n"
    block = _context(candidate, content=content)
    assert block.content == content


def test_context_must_reference_returned_candidate_with_identical_provenance() -> None:
    candidate = _candidate()
    matching = _context(candidate)
    result = _result(
        candidates=[candidate],
        context_blocks=[matching],
        channel_outcomes=_hit_outcomes(),
    )
    assert result.context_blocks == (matching,)

    other = _candidate(
        entity_id="code://repo@abc/src/other.py#symbol=other",
        retrieval_unit_id="unit://other",
        locator="code://repo@abc/src/other.py#L1",
        relation_path=_direct_path(
            "code://repo@abc/src/other.py#symbol=other",
            locator="code://repo@abc/src/other.py#L1",
        ).model_dump(mode="json"),
    )
    with pytest.raises(ValidationError, match="reference a returned candidate"):
        _result(
            candidates=[candidate],
            context_blocks=[_context(other)],
            channel_outcomes=_hit_outcomes(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "history"),
        ("repository_id", "repository://other"),
        ("stable_version", "def"),
        ("source_generation", "generation://other"),
        ("acl_ref", "project:private"),
        ("locator", "code://repo@abc/src/ranking.py#L1"),
    ],
)
def test_context_cross_provenance_is_rejected(field: str, value: str) -> None:
    candidate = _candidate()
    path_updates: dict[str, object] = {}
    if field in {
        "repository_id",
        "stable_version",
        "source_generation",
        "acl_ref",
        "locator",
    }:
        terminal_updates = {
            {
                "repository_id": "repository_id",
                "stable_version": "stable_version",
                "source_generation": "source_generation",
                "acl_ref": "acl_ref",
                "locator": "locator",
            }[field]: value
        }
        terminal = candidate.relation_path.nodes[-1].model_copy(update=terminal_updates)
        path_updates["relation_path"] = CodeRelationPath(nodes=[terminal]).model_dump(mode="json")
    block = _context(candidate, **path_updates, **{field: value})

    with pytest.raises(ValidationError, match="conflicts with candidate"):
        _result(
            candidates=[candidate],
            context_blocks=[block],
            channel_outcomes=_hit_outcomes(),
        )


def test_context_path_conflict_and_semantic_duplicate_block_id_bypass_are_rejected() -> None:
    candidate = _candidate()
    seed = _node("seed")
    terminal = candidate.relation_path.nodes[-1]
    alternate_path = CodeRelationPath(
        nodes=[seed, terminal],
        edges=[_hop(seed, terminal)],
    )
    conflicting = _context(
        candidate,
        relation_path=alternate_path.model_dump(mode="json"),
    )
    with pytest.raises(ValidationError, match="conflicts with candidate"):
        _result(
            candidates=[candidate],
            context_blocks=[conflicting],
            channel_outcomes=_hit_outcomes(),
        )

    first = _context(candidate, block_id="block://1")
    second = _context(
        candidate,
        block_id="block://2",
        content="different rendering cannot bypass provenance dedup",
    )
    with pytest.raises(ValidationError, match="semantic duplicates"):
        _result(
            candidates=[candidate],
            context_blocks=[first, second],
            channel_outcomes=_hit_outcomes(),
        )


def test_complete_no_match_unavailable_and_timeout_cannot_carry_context() -> None:
    candidate = _candidate()
    block = _context(candidate)
    with pytest.raises(ValidationError, match="without candidates cannot carry context"):
        _result(context_blocks=[block])

    unavailable_outcomes = _outcomes(
        {
            "exact": CodeChannelUnavailable(
                channel="exact",
                error="index is unavailable",
            )
        }
    )
    unavailable_errors = [_channel_failure("exact", "unavailable", "index is unavailable")]
    with pytest.raises(ValidationError, match="unavailable status cannot carry"):
        _result(
            status="unavailable",
            candidates=[candidate],
            context_blocks=[block],
            channel_outcomes=unavailable_outcomes,
            errors=unavailable_errors,
        )

    timeout_outcomes = _outcomes(
        {"dense": CodeChannelTimeout(channel="dense", error="deadline exceeded")}
    )
    with pytest.raises(ValidationError, match="timeout status cannot carry"):
        _result(
            status="timeout",
            candidates=[candidate],
            context_blocks=[block],
            channel_outcomes=timeout_outcomes,
            errors=[_channel_failure("dense", "timeout", "deadline exceeded")],
        )


def test_channel_outcome_discriminated_state_matrix_and_canonical_order() -> None:
    outcomes = _outcomes(
        {
            "exact": CodeChannelCompleteNoMatch(channel="exact"),
            "sparse": CodeChannelCompleteWithHits(channel="sparse", hit_count=1),
            "dense": CodeChannelTimeout(channel="dense", error="deadline exceeded"),
            "graph": CodeChannelError(channel="graph", error="graph failed"),
            "history": CodeChannelUnavailable(
                channel="history",
                error="history is not indexed",
            ),
        }
    )
    candidate = _candidate(
        raw_channel_scores=[CodeChannelScore(channel="sparse", score=0.75)],
        raw_channel_ranks=[CodeChannelRank(channel="sparse", rank=0)],
    )
    errors = [
        _channel_failure("history", "unavailable", "history is not indexed"),
        _channel_failure("graph", "error", "graph failed"),
        _channel_failure("dense", "timeout", "deadline exceeded"),
    ]
    result = _result(
        status="partial",
        candidates=[candidate],
        channel_outcomes=list(reversed(outcomes)),
        errors=list(reversed(errors)),
    )

    assert [outcome.channel for outcome in result.channel_outcomes] == list(CodeRetrievalChannel)
    assert [error.channel for error in result.errors] == ["dense", "graph", "history"]
    assert type(result).model_validate_json(result.model_dump_json()) == result

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        CodeChannelCompleteWithHits(channel="exact", hit_count=0)
    with pytest.raises(ValidationError, match="status"):
        CodeSourceResult.model_validate(
            {
                **result.model_dump(mode="json"),
                "channel_outcomes": [
                    {"channel": "exact", "status": "maybe"},
                    *result.model_dump(mode="json")["channel_outcomes"][1:],
                ],
            }
        )


def test_channel_outcomes_require_every_unique_registered_channel() -> None:
    outcomes = _no_match_outcomes()
    with pytest.raises(ValidationError, match="at least 6 items"):
        _result(channel_outcomes=outcomes[:-1])
    with pytest.raises(ValidationError, match="must not repeat"):
        _result(channel_outcomes=[*outcomes[:-1], outcomes[0]])


def test_complete_with_hits_and_candidate_raw_channels_must_agree() -> None:
    candidate = _candidate()
    with pytest.raises(ValidationError, match="complete_with_hits outcomes"):
        _result(candidates=[candidate], channel_outcomes=_no_match_outcomes())
    with pytest.raises(ValidationError, match="returned channel candidate"):
        _result(
            channel_outcomes=_outcomes(
                {"exact": CodeChannelCompleteWithHits(channel="exact", hit_count=1)}
            )
        )


def test_complete_pruned_requires_positive_hits_and_forbids_returned_candidates() -> None:
    pruned = CodeChannelCompletePruned(channel="exact", hit_count=3)
    result = _result(
        channel_outcomes=_outcomes({"exact": pruned}),
    )

    assert result.channel_outcomes[0] == pruned
    assert type(result).model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        CodeChannelCompletePruned(channel="exact", hit_count=0)

    candidate = _candidate()
    with pytest.raises(ValidationError, match="complete_with_hits outcomes"):
        _result(
            candidates=[candidate],
            channel_outcomes=_outcomes({"exact": pruned}),
        )


@pytest.mark.parametrize("raw_rank", [1, 2])
def test_raw_channel_rank_must_be_below_total_hit_count(raw_rank: int) -> None:
    candidate = _candidate(
        raw_channel_scores=[CodeChannelScore(channel="exact", score=1.0)],
        raw_channel_ranks=[CodeChannelRank(channel="exact", rank=raw_rank)],
    )
    with pytest.raises(ValidationError, match="below channel hit_count"):
        _result(
            candidates=[candidate],
            channel_outcomes=_outcomes(
                {"exact": CodeChannelCompleteWithHits(channel="exact", hit_count=1)}
            ),
        )


def test_returned_raw_channel_ranks_must_be_unique_per_channel() -> None:
    first = _candidate(
        raw_channel_scores=[CodeChannelScore(channel="exact", score=1.0)],
        raw_channel_ranks=[CodeChannelRank(channel="exact", rank=0)],
    )
    second = _candidate(
        retrieval_unit_id="unit://sha256/5678",
        within_source_rank=1,
        raw_channel_scores=[CodeChannelScore(channel="exact", score=0.8)],
        raw_channel_ranks=[CodeChannelRank(channel="exact", rank=0)],
    )
    with pytest.raises(ValidationError, match="unique within each channel"):
        _result(
            candidates=[first, second],
            channel_outcomes=_outcomes(
                {"exact": CodeChannelCompleteWithHits(channel="exact", hit_count=2)}
            ),
        )


def test_source_ranks_are_contiguous_and_canonicalize_candidate_order() -> None:
    first = _candidate(
        raw_channel_scores=[CodeChannelScore(channel="exact", score=1.0)],
        raw_channel_ranks=[CodeChannelRank(channel="exact", rank=0)],
    )
    second = _candidate(
        retrieval_unit_id="unit://sha256/5678",
        within_source_rank=1,
        raw_channel_scores=[CodeChannelScore(channel="exact", score=0.8)],
        raw_channel_ranks=[CodeChannelRank(channel="exact", rank=1)],
    )
    outcomes = _outcomes({"exact": CodeChannelCompleteWithHits(channel="exact", hit_count=2)})

    ordered = _result(candidates=[first, second], channel_outcomes=outcomes)
    reversed_input = _result(
        candidates=[second, first],
        channel_outcomes=list(reversed(outcomes)),
    )
    assert ordered.candidates == (first, second)
    assert ordered == reversed_input
    assert ordered.canonical_json_bytes() == reversed_input.canonical_json_bytes()
    assert ordered.canonical_sha256() == reversed_input.canonical_sha256()

    duplicate = second.model_copy(update={"within_source_rank": 0})
    with pytest.raises(ValidationError, match="must be unique"):
        _result(candidates=[first, duplicate], channel_outcomes=outcomes)

    gap = second.model_copy(update={"within_source_rank": 2})
    with pytest.raises(ValidationError, match="zero-based and contiguous"):
        _result(candidates=[first, gap], channel_outcomes=outcomes)

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        second.model_copy(update={"within_source_rank": -1})


def test_overall_status_matrix_includes_partial_zero_hit() -> None:
    partial_outcomes = _outcomes(
        {
            "exact": CodeChannelCompleteNoMatch(channel="exact"),
            "dense": CodeChannelError(channel="dense", error="dense failed"),
        }
    )
    partial = _result(
        status="partial",
        channel_outcomes=partial_outcomes,
        errors=[_channel_failure("dense", "error", "dense failed")],
    )
    assert partial.candidates == ()

    timeout = _result(
        status="timeout",
        channel_outcomes=_outcomes(
            {"dense": CodeChannelTimeout(channel="dense", error="deadline exceeded")}
        ),
        errors=[_channel_failure("dense", "timeout", "deadline exceeded")],
    )
    assert timeout.status is CodeSourceStatus.TIMEOUT

    unavailable = _result(
        status="unavailable",
        channel_outcomes=_outcomes(
            {
                "exact": CodeChannelUnavailable(
                    channel="exact",
                    error="index unavailable",
                )
            }
        ),
        errors=[_channel_failure("exact", "unavailable", "index unavailable")],
    )
    assert unavailable.status is CodeSourceStatus.UNAVAILABLE

    with pytest.raises(ValidationError, match="completed and failed"):
        _result(status="partial")
    with pytest.raises(ValidationError, match="no completed channel and a timeout"):
        _result(
            status="timeout",
            channel_outcomes=_outcomes(
                {
                    "exact": CodeChannelUnavailable(
                        channel="exact",
                        error="index unavailable",
                    )
                }
            ),
            errors=[_channel_failure("exact", "unavailable", "index unavailable")],
        )
    with pytest.raises(ValidationError, match="non-timeout failures"):
        _result(
            status="unavailable",
            channel_outcomes=_outcomes(
                {"dense": CodeChannelTimeout(channel="dense", error="deadline")}
            ),
            errors=[_channel_failure("dense", "timeout", "deadline")],
        )


def test_errors_must_exactly_mirror_channel_and_failed_fallback() -> None:
    outcomes = _outcomes({"dense": CodeChannelError(channel="dense", error="dense failed")})
    with pytest.raises(ValidationError, match="exactly mirror"):
        _result(status="unavailable", channel_outcomes=outcomes)
    with pytest.raises(ValidationError, match="exactly mirror"):
        _result(
            status="unavailable",
            channel_outcomes=outcomes,
            errors=[_channel_failure("dense", "error", "different text")],
        )

    fallback = CodeFallbackFailed(engine="v1", reason="legacy store failed")
    result = _result(
        status="unavailable",
        channel_outcomes=outcomes,
        errors=[
            _channel_failure("dense", "error", "dense failed"),
            CodeFallbackFailure(engine="v1", message="legacy store failed"),
        ],
        fallback=fallback,
    )
    assert result.fallback.status == "failed"


def test_fallback_matrix_rejects_illegal_overall_status_combinations() -> None:
    completed = _result(fallback=CodeFallbackSucceeded(engine="v1", reason="v2 unavailable"))
    assert completed.fallback.status == "succeeded"

    partial_outcomes = _outcomes(
        {
            "exact": CodeChannelCompleteNoMatch(channel="exact"),
            "dense": CodeChannelError(channel="dense", error="dense failed"),
        }
    )
    with pytest.raises(ValidationError, match="successful fallback requires complete"):
        _result(
            status="partial",
            channel_outcomes=partial_outcomes,
            errors=[_channel_failure("dense", "error", "dense failed")],
            fallback=CodeFallbackSucceeded(engine="v1", reason="v2 failed"),
        )
    with pytest.raises(ValidationError, match="failed fallback cannot accompany complete"):
        _result(
            errors=[CodeFallbackFailure(engine="v1", message="legacy failed")],
            fallback=CodeFallbackFailed(engine="v1", reason="legacy failed"),
        )
    with pytest.raises(ValidationError, match="successful fallback requires complete"):
        _result(
            status="unavailable",
            channel_outcomes=_outcomes(
                {
                    "exact": CodeChannelUnavailable(
                        channel="exact",
                        error="index unavailable",
                    )
                }
            ),
            errors=[_channel_failure("exact", "unavailable", "index unavailable")],
            fallback=CodeFallbackSucceeded(engine="v1", reason="v2 unavailable"),
        )


def test_source_result_canonicalizes_outcomes_and_errors_across_input_order() -> None:
    outcomes = _outcomes(
        {
            "exact": CodeChannelCompleteNoMatch(channel="exact"),
            "dense": CodeChannelError(channel="dense", error="dense failed"),
        }
    )
    errors = [_channel_failure("dense", "error", "dense failed")]
    first = _result(
        status="partial",
        channel_outcomes=outcomes,
        errors=errors,
    )
    second = _result(
        status="partial",
        channel_outcomes=list(reversed(outcomes)),
        errors=list(reversed(errors)),
    )
    assert first == second
    assert first.canonical_json_bytes() == second.canonical_json_bytes()
    assert first.canonical_sha256() == second.canonical_sha256()


@pytest.mark.parametrize(
    "value",
    [
        " leading",
        "trailing ",
        "line\nbreak",
        "tab\tinside",
        "nul\x00inside",
        "nonbreaking\u00a0space",
        "zero\u200bwidth",
    ],
)
def test_identity_version_locator_acl_error_and_profile_controls_reject_bad_text(
    value: str,
) -> None:
    for field in (
        "entity_id",
        "repository_id",
        "stable_version",
        "source_generation",
        "locator",
        "acl_ref",
    ):
        payload = _node("rank").model_dump(mode="json")
        payload[field] = value
        with pytest.raises(ValidationError):
            CodeRelationNode.model_validate(payload)
    locator_payload = _hop(_node("source"), _node("target")).locator.model_dump(mode="json")
    for field in (
        "locator",
        "owner_entity_id",
        "repository_id",
        "stable_version",
        "source_generation",
        "acl_ref",
    ):
        with pytest.raises(ValidationError):
            CodeRelationLocator.model_validate({**locator_payload, field: value})
    with pytest.raises(ValidationError):
        CodeQueryProfile(task="implementation", profile_version=value)
    with pytest.raises(ValidationError):
        CodeChannelError(channel="dense", error=value)


def test_contract_text_is_nfc_canonical_for_identity_and_hashing() -> None:
    composed = _node(
        "café",
        entity_id="code://repo/src/café.py#symbol=café",
        locator="code://repo/src/café.py#L1",
    )
    decomposed = _node(
        "cafe\u0301",
        entity_id="code://repo/src/cafe\u0301.py#symbol=cafe\u0301",
        locator="code://repo/src/cafe\u0301.py#L1",
    )
    assert composed == decomposed
    assert composed.canonical_json_bytes() == decomposed.canonical_json_bytes()
    assert composed.canonical_sha256() == decomposed.canonical_sha256()


def test_calibrated_score_rejects_wrong_status_and_non_probability() -> None:
    with pytest.raises(ValidationError, match="status"):
        CodeCalibratedScore(
            status="disabled",
            score=0.5,
            calibration_version="calibration-v1",
        )
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        CodeCalibratedScore(score=1.1, calibration_version="calibration-v1")


def test_settings_default_all_v1_and_existing_explicit_calls_remain_compatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in _CODE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    direct = _settings(tmp_path)
    loaded = Settings.from_env(base_dir=tmp_path)
    expected = {
        "rag_code_engine": "v1",
        "rag_code_shadow": False,
        "rag_code_unit_builder": "raw-v1",
        "rag_code_embedding_profile": "local-hash-v2",
        "rag_code_dense_index": True,
        "rag_code_graph": False,
        "rag_code_semantic_resolver": "off",
        "rag_code_reranker": "off",
        "rag_code_context": "snippet-v1",
        "rag_code_canary_percent": 0,
    }
    assert {name: getattr(direct, name) for name in expected} == expected
    assert {name: getattr(loaded, name) for name in expected} == expected


def test_settings_environment_overrides_all_code_contract_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    overrides = {
        "RAG_CODE_ENGINE": "v2",
        "RAG_CODE_SHADOW": "true",
        "RAG_CODE_UNIT_BUILDER": "ast-v2",
        "RAG_CODE_EMBEDDING_PROFILE": "qwen-code.v3",
        "RAG_CODE_DENSE_INDEX": "false",
        "RAG_CODE_GRAPH": "true",
        "RAG_CODE_SEMANTIC_RESOLVER": "scip-python",
        "RAG_CODE_RERANKER": "profile",
        "RAG_CODE_CONTEXT": "structured-v2",
        "RAG_CODE_CANARY_PERCENT": "25",
    }
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    loaded = Settings.from_env(base_dir=tmp_path)
    assert loaded.rag_code_engine == "v2"
    assert loaded.rag_code_shadow is True
    assert loaded.rag_code_unit_builder == "ast-v2"
    assert loaded.rag_code_embedding_profile == "qwen-code.v3"
    assert loaded.rag_code_dense_index is False
    assert loaded.rag_code_graph is True
    assert loaded.rag_code_semantic_resolver == "scip-python"
    assert loaded.rag_code_reranker == "profile"
    assert loaded.rag_code_context == "structured-v2"
    assert loaded.rag_code_canary_percent == 25


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RAG_CODE_ENGINE", "V1"),
        ("RAG_CODE_SHADOW", "1"),
        ("RAG_CODE_SHADOW", "TRUE"),
        ("RAG_CODE_UNIT_BUILDER", "raw"),
        ("RAG_CODE_EMBEDDING_PROFILE", " "),
        ("RAG_CODE_EMBEDDING_PROFILE", "qwen/profile"),
        ("RAG_CODE_EMBEDDING_PROFILE", "qwen\nprofile"),
        ("RAG_CODE_DENSE_INDEX", "yes"),
        ("RAG_CODE_GRAPH", "yes"),
        ("RAG_CODE_SEMANTIC_RESOLVER", "scip"),
        ("RAG_CODE_RERANKER", "local"),
        ("RAG_CODE_CONTEXT", "snippet"),
        ("RAG_CODE_CANARY_PERCENT", "-1"),
        ("RAG_CODE_CANARY_PERCENT", "101"),
        ("RAG_CODE_CANARY_PERCENT", "1.0"),
        ("RAG_CODE_CANARY_PERCENT", " 5"),
    ],
)
def test_settings_invalid_environment_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    for env_name in _CODE_ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.from_env(base_dir=tmp_path)


@pytest.mark.parametrize(
    "value",
    [" profile", "profile ", "qwen/profile", "qwen\nprofile", "qwen\x00profile", ""],
)
def test_settings_direct_embedding_profile_uses_same_fullmatch_validator(
    tmp_path: Path,
    value: str,
) -> None:
    with pytest.raises(ValueError, match="fully match"):
        replace(_settings(tmp_path), rag_code_embedding_profile=value)


def test_settings_direct_enum_bool_and_percentage_fail_fast(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="rag_code_engine"):
        replace(settings, rag_code_engine="v3")
    with pytest.raises(TypeError, match="rag_code_shadow"):
        replace(settings, rag_code_shadow=1)
    with pytest.raises(TypeError, match="rag_code_dense_index"):
        replace(settings, rag_code_dense_index=1)
    with pytest.raises(TypeError, match="rag_code_canary_percent"):
        replace(settings, rag_code_canary_percent=True)
    with pytest.raises(ValueError, match="between 0 and 100"):
        replace(settings, rag_code_canary_percent=101)


def test_code_settings_and_contract_have_no_secret_configuration_surface() -> None:
    code_setting_names = {
        item.name for item in fields(Settings) if item.name.startswith("rag_code_")
    }
    forbidden_markers = ("secret", "password", "credential", "api_key", "access_key")
    assert len(code_setting_names) == 10
    assert not any(marker in name for name in code_setting_names for marker in forbidden_markers)
    public_contract_fields = {
        name
        for model in (
            CodeQueryProfile,
            CodeRetrievalBudget,
            CodeRetrievalCandidate,
            CodeRelationNode,
            CodeRelationPath,
            CodeContextBlock,
            CodeSourceResult,
        )
        for name in model.model_fields
    }
    assert not any(
        marker in name for name in public_contract_fields for marker in forbidden_markers
    )


def test_code_contract_import_boundary_has_no_cross_source_or_runtime_dependency() -> None:
    contract_path = Path(code_contracts.__file__).resolve()
    tree = ast.parse(contract_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported_modules <= {
        "__future__",
        "collections.abc",
        "enum",
        "hashlib",
        "json",
        "pydantic",
        "typing",
        "unicodedata",
    }
    assert not any(
        module.startswith(
            (
                "evidence_rag.platform",
                "evidence_rag.documents",
                "evidence_rag.codex",
                "evidence_rag.runtime",
                "evidence_rag.storage",
                "evidence_rag.store",
            )
        )
        for module in imported_modules
    )
