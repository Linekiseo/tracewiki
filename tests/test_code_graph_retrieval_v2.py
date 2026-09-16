from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.rag.sources.code import (
    CodeTypedGraphRetriever as PublicCodeTypedGraphRetriever,
)
from evidence_rag.rag.sources.code.contracts import (
    CodeCandidateRole,
    CodeChannelRank,
    CodeChannelScore,
    CodeDerivation,
    CodeFactStatus,
    CodeRelationNode,
    CodeRelationPath,
    CodeRelationType,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeReviewStatus,
    CodeTask,
    CodeTraversalDirection,
    CodeUncalibratedScore,
    CodeVersionAlignment,
)
from evidence_rag.rag.sources.code.graph_retrieval_v2 import (
    HOP_DECAY,
    CodeGraphRetriever,
    CodeTypedGraphRetriever,
    GraphEdge,
    GraphNode,
    GraphPublication,
    GraphTraversalPath,
    GraphTraversalRequest,
    GraphTraversalStatus,
    SQLiteGraphAdjacencyProvider,
    fuse_graph_candidates,
)
from evidence_rag.rag.sources.code.graph_v2 import (
    CodeEdgeDerivationLayer,
    CodeGraphEntityType,
)
from evidence_rag.storage import SQLiteStore

REPOSITORY = "repository://alpha"
PROJECT = "project://alpha"
GENERATION = "generation://alpha"
VERSION = "a" * 40
ACL = "acl://alpha"
HIDDEN_ACL = "acl://hidden"


class MemoryAdjacencyProvider:
    def __init__(
        self,
        edges: tuple[GraphEdge, ...],
        *,
        publication: GraphPublication | None = None,
        prefilter: bool = True,
    ) -> None:
        self.edges = edges
        self.graph_publication = publication or _publication()
        self.prefilter = prefilter
        self.publication_calls = 0
        self.adjacency_calls = 0

    def publication(
        self,
        *,
        repository_id: str,
        generation_id: str,
    ) -> GraphPublication | None:
        self.publication_calls += 1
        return self.graph_publication

    def adjacent(
        self,
        *,
        node: GraphNode,
        directions: tuple[CodeTraversalDirection, ...],
        edge_types: tuple[CodeRelationType, ...],
        min_confidence: float,
        stable_version: str,
        generation_id: str,
        allowed_acl_refs: tuple[str, ...],
        limit: int,
    ) -> tuple[GraphEdge, ...]:
        self.adjacency_calls += 1
        if not self.prefilter:
            return self.edges[:limit]
        values = []
        for edge in self.edges:
            current = (
                edge.source
                if edge.traversal_direction is CodeTraversalDirection.OUTGOING
                else edge.target
            )
            if current.semantic_identity() != node.semantic_identity():
                continue
            if edge.traversal_direction not in directions or edge.edge_type not in edge_types:
                continue
            if edge.confidence < min_confidence:
                continue
            if any(
                endpoint.generation_id != generation_id
                or endpoint.stable_version != stable_version
                or endpoint.acl_ref not in allowed_acl_refs
                for endpoint in (edge.source, edge.target)
            ):
                continue
            values.append(edge)
        return tuple(values[:limit])


class AlternatingAdjacencyProvider(MemoryAdjacencyProvider):
    def adjacent(self, **kwargs: Any) -> tuple[GraphEdge, ...]:
        values = super().adjacent(**kwargs)
        if self.adjacency_calls % 2:
            return tuple(reversed(values))
        return values


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SlowAdjacencyProvider(MemoryAdjacencyProvider):
    def __init__(
        self,
        edges: tuple[GraphEdge, ...],
        *,
        clock: MutableClock,
    ) -> None:
        super().__init__(edges)
        self.clock = clock

    def adjacent(self, **kwargs: Any) -> tuple[GraphEdge, ...]:
        values = super().adjacent(**kwargs)
        self.clock.now = 2.0
        return values


def _publication(
    *,
    project_id: str = PROJECT,
    version: str = VERSION,
    ready: bool = True,
    reason: str = "",
) -> GraphPublication:
    return GraphPublication(
        project_id=project_id,
        repository_id=REPOSITORY,
        generation_id=GENERATION,
        stable_version=version,
        graph_version="graph_v2@1",
        ready=ready,
        unavailable_reason=reason,
    )


def _node(
    name: str,
    *,
    version: str = VERSION,
    generation: str = GENERATION,
    acl_ref: str = ACL,
    retrieval_unit: bool = True,
    entity_type: CodeGraphEntityType = CodeGraphEntityType.CODE_SYMBOL,
) -> GraphNode:
    return GraphNode(
        entity_id=f"entity://{name}",
        repository_id=REPOSITORY,
        entity_type=entity_type,
        stable_version=version,
        generation_id=generation,
        locator=f"code://{name}",
        acl_ref=acl_ref,
        retrieval_unit_id=f"unit://{name}" if retrieval_unit else None,
        token_estimate=10,
    )


def _edge(
    source: GraphNode,
    target: GraphNode,
    *,
    name: str | None = None,
    confidence: float = 1.0,
    direction: CodeTraversalDirection = CodeTraversalDirection.OUTGOING,
    edge_type: CodeRelationType = CodeRelationType.CALLS,
    layer: CodeEdgeDerivationLayer = CodeEdgeDerivationLayer.STATIC,
    derivation: CodeDerivation = CodeDerivation.TREE_SITTER,
    review_status: CodeReviewStatus = CodeReviewStatus.MACHINE_CONFIRMED,
) -> GraphEdge:
    return GraphEdge(
        edge_id=f"edge://{name or source.entity_id + '->' + target.entity_id}",
        edge_type=edge_type,
        traversal_direction=direction,
        source=source,
        target=target,
        confidence=confidence,
        derivation_layer=layer,
        derivation=derivation,
        review_status=review_status,
        fact_status=CodeFactStatus.MACHINE_CONFIRMED,
        locator=source.locator,
    )


def _candidate(
    name: str,
    *,
    rank: int = 0,
    channel: CodeRetrievalChannel = CodeRetrievalChannel.EXACT,
    locator: str | None = None,
    score: float = 1.0,
    acl_ref: str = ACL,
) -> CodeRetrievalCandidate:
    entity_id = f"entity://{name}"
    unit_id = f"unit://{name}"
    resolved_locator = locator or f"code://{name}"
    relation_path = CodeRelationPath(
        nodes=(
            CodeRelationNode(
                entity_id=entity_id,
                repository_id=REPOSITORY,
                stable_version=VERSION,
                source_generation=GENERATION,
                locator=resolved_locator,
                acl_ref=acl_ref,
            ),
        ),
        edges=(),
        path_score=0.0,
    )
    return CodeRetrievalCandidate(
        entity_id=entity_id,
        retrieval_unit_id=unit_id,
        repository_id=REPOSITORY,
        entity_type=CodeGraphEntityType.CODE_SYMBOL.value,
        stable_version=VERSION,
        source_generation=GENERATION,
        raw_channel_scores=(CodeChannelScore(channel=channel, score=score),),
        raw_channel_ranks=(CodeChannelRank(channel=channel, rank=rank),),
        within_source_rank=rank,
        source_fused_score=score,
        calibrated_relevance=CodeUncalibratedScore(
            status="unavailable",
            reason="test candidate is not calibrated",
        ),
        version_alignment=CodeVersionAlignment.EXACT,
        fact_status=CodeFactStatus.OBSERVED,
        derivation=CodeDerivation.TREE_SITTER,
        review_status=CodeReviewStatus.MACHINE_CONFIRMED,
        role=CodeCandidateRole.TARGET,
        relation_path=relation_path,
        locator=resolved_locator,
        token_estimate=10,
        acl_ref=acl_ref,
    )


def _request(
    *,
    seeds: tuple[CodeRetrievalCandidate, ...] | None = None,
    task: CodeTask = CodeTask.CALL_PATH,
    directions: tuple[CodeTraversalDirection, ...] = (CodeTraversalDirection.OUTGOING,),
    edge_types: tuple[CodeRelationType, ...] = (CodeRelationType.CALLS,),
    max_hops: int = 3,
    beam_width: int = 8,
    min_confidence: float = 0.0,
    project_id: str = PROJECT,
    target_ref: str | None = None,
    stable_version: str = VERSION,
    generation_id: str = GENERATION,
    node_budget: int = 20,
    edge_budget: int = 20,
    deadline: float | None = None,
) -> GraphTraversalRequest:
    return GraphTraversalRequest(
        seed_candidates=(_candidate("a"),) if seeds is None else seeds,
        project_id=project_id,
        task=task,
        directions=directions,
        edge_types=edge_types,
        max_hops=max_hops,
        beam_width=beam_width,
        min_confidence=min_confidence,
        target_ref=target_ref or stable_version,
        stable_version=stable_version,
        generation_id=generation_id,
        allowed_acl_refs=(ACL,),
        node_budget=node_budget,
        edge_budget=edge_budget,
        candidate_budget=20,
        deadline=deadline,
    )


def _chain() -> tuple[GraphNode, GraphNode, GraphNode, GraphNode, tuple[GraphEdge, ...]]:
    a, b, c, d = (_node(name) for name in ("a", "b", "c", "d"))
    return (
        a,
        b,
        c,
        d,
        (
            _edge(a, b, name="ab"),
            _edge(b, c, name="bc"),
            _edge(c, d, name="cd"),
        ),
    )


@pytest.mark.parametrize(
    ("max_hops", "expected"),
    [
        (0, ()),
        (1, ("entity://b",)),
        (2, ("entity://b", "entity://c")),
        (3, ("entity://b", "entity://c", "entity://d")),
    ],
)
def test_hop_limits_produce_distinct_bounded_results(
    max_hops: int,
    expected: tuple[str, ...],
) -> None:
    _a, _b, _c, _d, edges = _chain()
    provider = MemoryAdjacencyProvider(edges)

    response = CodeGraphRetriever(provider).search_with_trace(_request(max_hops=max_hops))

    assert tuple(item.entity_id for item in response.candidates) == expected
    if max_hops:
        scores = [item.candidate.source_fused_score for item in response.candidates]
        assert scores == pytest.approx([HOP_DECAY, HOP_DECAY**2, HOP_DECAY**3][:max_hops])
        expected_edge_ids = tuple(
            tuple(f"edge://{name}" for name in ("ab", "bc", "cd")[:hop])
            for hop in range(1, max_hops + 1)
        )
        assert tuple(item.path.edge_ids for item in response.candidates) == expected_edge_ids
        assert (
            tuple(
                tuple(edge.edge_id for edge in item.candidate.relation_path.edges)
                for item in response.candidates
            )
            == expected_edge_ids
        )
    else:
        assert provider.publication_calls == 0


def test_incoming_and_outgoing_follow_stored_orientation() -> None:
    a, b = _node("a"), _node("b")
    outgoing = _edge(a, b, name="ab-out")
    incoming = _edge(
        a,
        b,
        name="ab-in",
        direction=CodeTraversalDirection.INCOMING,
    )
    provider = MemoryAdjacencyProvider((outgoing, incoming))

    outgoing_response = CodeGraphRetriever(provider).search_with_trace(
        _request(seeds=(_candidate("a"),), max_hops=1)
    )
    incoming_response = CodeGraphRetriever(provider).search_with_trace(
        _request(
            seeds=(_candidate("b"),),
            directions=(CodeTraversalDirection.INCOMING,),
            max_hops=1,
        )
    )

    assert [item.entity_id for item in outgoing_response.candidates] == ["entity://b"]
    assert [item.entity_id for item in incoming_response.candidates] == ["entity://a"]
    assert (
        incoming_response.candidates[0].candidate.relation_path.edges[0].direction
        is CodeTraversalDirection.INCOMING
    )
    assert incoming_response.candidates[0].path.edge_ids == ("edge://ab-in",)


def test_task_whitelist_and_semantic_calls_fail_closed() -> None:
    with pytest.raises(ValueError, match="not allowed"):
        _request(task=CodeTask.HISTORICAL, max_hops=1)

    a, b = _node("a"), _node("b")
    semantic_call = _edge(
        a,
        b,
        layer=CodeEdgeDerivationLayer.SEMANTIC,
        derivation=CodeDerivation.VECTOR,
    )
    provider = MemoryAdjacencyProvider((semantic_call,), prefilter=False)
    response = CodeGraphRetriever(provider).search_with_trace(_request(max_hops=1))

    assert response.candidates == ()
    assert dict(response.trace.rejection_counts)["registry"] == 1


def test_low_confidence_edges_are_not_traversed() -> None:
    a, b = _node("a"), _node("b")
    provider = MemoryAdjacencyProvider((_edge(a, b, confidence=0.49),))

    response = CodeGraphRetriever(provider).search_with_trace(
        _request(max_hops=1, min_confidence=0.5)
    )

    assert response.candidates == ()
    assert response.trace.status is GraphTraversalStatus.NO_MATCH


def test_cycle_prevention_and_high_degree_beam_are_deterministic() -> None:
    a, b, c, d = (_node(name) for name in ("a", "b", "c", "d"))
    edges = (
        _edge(a, b, name="ab"),
        _edge(b, a, name="ba-cycle"),
        _edge(b, d, name="bd", confidence=0.8),
        _edge(b, c, name="bc", confidence=0.9),
    )
    provider = AlternatingAdjacencyProvider(edges)
    retriever = CodeGraphRetriever(provider, latency_clock=lambda: 0.0)
    request = _request(max_hops=2, beam_width=1)

    first = retriever.search_with_trace(request)
    second = retriever.search_with_trace(request)

    assert first == second
    assert [item.entity_id for item in first.candidates] == [
        "entity://b",
        "entity://c",
    ]
    assert dict(first.trace.rejection_counts)["cycle"] == 1
    assert dict(first.trace.rejection_counts)["beam_width"] >= 1


def test_version_and_publication_mismatches_fail_closed() -> None:
    a = _node("a")
    different_version = _node("b", version="b" * 40)
    unsafe = MemoryAdjacencyProvider(
        (_edge(a, different_version),),
        prefilter=False,
    )
    edge_response = CodeGraphRetriever(unsafe).search_with_trace(_request(max_hops=1))
    assert edge_response.candidates == ()
    assert dict(edge_response.trace.rejection_counts)["version"] == 1

    wrong_publication = MemoryAdjacencyProvider(
        (),
        publication=_publication(version="c" * 40),
    )
    publication_response = CodeGraphRetriever(wrong_publication).search_with_trace(
        _request(max_hops=1)
    )
    assert publication_response.trace.status is GraphTraversalStatus.UNAVAILABLE
    assert "stable version" in publication_response.trace.unavailable_reason

    wrong_project = MemoryAdjacencyProvider(
        (),
        publication=_publication(project_id="project://other"),
    )
    project_response = CodeGraphRetriever(wrong_project).search_with_trace(_request(max_hops=1))
    assert project_response.trace.status is GraphTraversalStatus.UNAVAILABLE
    assert "project provenance" in project_response.trace.unavailable_reason


@pytest.mark.parametrize(
    "review_status",
    [CodeReviewStatus.UNREVIEWED, CodeReviewStatus.REJECTED],
)
def test_generation_and_unreviewed_edges_fail_closed(
    review_status: CodeReviewStatus,
) -> None:
    a = _node("a")
    other_generation = _node("b", generation="generation://other")
    generation_response = CodeGraphRetriever(
        MemoryAdjacencyProvider((_edge(a, other_generation),), prefilter=False)
    ).search_with_trace(_request(max_hops=1))
    assert generation_response.candidates == ()
    assert dict(generation_response.trace.rejection_counts)["generation"] == 1

    b = _node("b")
    unreviewed_response = CodeGraphRetriever(
        MemoryAdjacencyProvider(
            (
                _edge(
                    a,
                    b,
                    review_status=review_status,
                ),
            ),
            prefilter=False,
        )
    ).search_with_trace(_request(max_hops=1))
    assert unreviewed_response.candidates == ()
    assert dict(unreviewed_response.trace.rejection_counts)["review"] == 1


def test_edge_identity_tampering_and_path_id_mismatch_fail_closed() -> None:
    a, b, c = (_node(name) for name in ("a", "b", "c"))
    response = CodeGraphRetriever(
        MemoryAdjacencyProvider(
            (
                _edge(a, b, name="shared"),
                _edge(a, c, name="shared"),
            ),
            prefilter=False,
        )
    ).search_with_trace(_request(max_hops=1))

    assert len(response.candidates) == 1
    assert dict(response.trace.rejection_counts)["edge_identity"] == 1
    selected = response.candidates[0]
    with pytest.raises(ValueError, match="exactly match"):
        GraphTraversalPath(
            seed_retrieval_unit_id=selected.path.seed_retrieval_unit_id,
            edge_ids=("edge://forged",),
            relation_path=selected.candidate.relation_path,
            explanation=selected.path.explanation,
        )

    chain_response = CodeGraphRetriever(
        MemoryAdjacencyProvider((_edge(a, b, name="ab"), _edge(b, c, name="bc")))
    ).search_with_trace(_request(max_hops=2))
    path_payload = chain_response.candidates[-1].candidate.relation_path.model_dump(
        mode="python",
        round_trip=True,
    )
    path_payload["edges"][1]["edge_id"] = "edge://ab"
    with pytest.raises(ValueError, match="repeat a stored edge identity"):
        CodeRelationPath.model_validate(path_payload)


def test_acl_hidden_middle_node_blocks_visible_descendants() -> None:
    a = _node("a")
    hidden = _node("hidden", acl_ref=HIDDEN_ACL)
    visible = _node("visible")
    provider = MemoryAdjacencyProvider(
        (
            _edge(a, hidden, name="a-hidden"),
            _edge(hidden, visible, name="hidden-visible"),
        )
    )

    response = CodeGraphRetriever(provider).search_with_trace(_request(max_hops=2))

    assert response.candidates == ()
    assert response.trace.nodes_examined == 0

    tampered = CodeGraphRetriever(
        MemoryAdjacencyProvider((_edge(a, hidden, name="acl-tamper"),), prefilter=False)
    ).search_with_trace(_request(max_hops=1))
    assert tampered.candidates == ()
    assert dict(tampered.trace.rejection_counts)["acl"] == 1


def test_node_and_edge_budgets_stop_expansion() -> None:
    _a, _b, _c, _d, edges = _chain()
    node_limited = CodeGraphRetriever(MemoryAdjacencyProvider(edges)).search_with_trace(
        _request(max_hops=3, node_budget=1)
    )
    edge_limited = CodeGraphRetriever(MemoryAdjacencyProvider(edges)).search_with_trace(
        _request(max_hops=3, edge_budget=1)
    )

    assert [item.entity_id for item in node_limited.candidates] == ["entity://b"]
    assert node_limited.trace.node_budget_exhausted is True
    assert node_limited.trace.nodes_examined == 1
    assert [item.entity_id for item in edge_limited.candidates] == ["entity://b"]
    assert edge_limited.trace.edge_budget_exhausted is True
    assert edge_limited.trace.edges_examined == 1


def test_deadline_and_empty_seeds_do_not_touch_adjacency() -> None:
    provider = MemoryAdjacencyProvider(())
    deadline_response = CodeGraphRetriever(
        provider,
        clock=lambda: 2.0,
    ).search_with_trace(_request(max_hops=1, deadline=1.0))
    empty_response = CodeGraphRetriever(provider).search_with_trace(_request(seeds=(), max_hops=2))

    assert deadline_response.trace.status is GraphTraversalStatus.DEADLINE
    assert deadline_response.trace.deadline_exceeded is True
    assert empty_response.trace.status is GraphTraversalStatus.NO_MATCH
    assert provider.adjacency_calls == 0


@pytest.mark.parametrize("return_edge", [False, True])
def test_deadline_after_slow_adjacency_discards_empty_or_nonempty_result(
    return_edge: bool,
) -> None:
    clock = MutableClock()
    a, b = _node("a"), _node("b")
    edges = (_edge(a, b, name="slow-ab"),) if return_edge else ()
    provider = SlowAdjacencyProvider(edges, clock=clock)

    response = CodeGraphRetriever(provider, clock=clock).search_with_trace(
        _request(max_hops=1, deadline=1.0)
    )

    assert response.trace.status is GraphTraversalStatus.DEADLINE
    assert response.trace.deadline_exceeded is True
    assert response.trace.paths == ()
    assert response.candidates == ()
    assert response.trace.edges_examined == 0
    assert provider.adjacency_calls == 1


def test_result_and_trace_are_frozen_strict_and_graph_only() -> None:
    a, b = _node("a"), _node("b")
    response = CodeGraphRetriever(
        MemoryAdjacencyProvider((_edge(a, b, name="ab"),))
    ).search_with_trace(_request(max_hops=1))
    item = response.candidates[0]

    assert response.trace.paths == (item.path,)
    assert response.code_candidates == (item.candidate,)
    assert {score.channel for score in item.candidate.raw_channel_scores} == {
        CodeRetrievalChannel.GRAPH
    }
    assert "hop 1 outgoing CALLS" in item.path.explanation
    assert "locator=code://a" in item.path.explanation
    with pytest.raises(FrozenInstanceError):
        item.explanation = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        response.trace.status = GraphTraversalStatus.NO_MATCH  # type: ignore[misc]


def test_production_identity_and_trace_metrics_are_public() -> None:
    retriever = CodeTypedGraphRetriever(
        MemoryAdjacencyProvider(()),
        latency_clock=lambda: 1.0,
    )
    response = retriever.search_with_trace(_request(seeds=(), max_hops=1))

    assert type(retriever).__name__ == "CodeTypedGraphRetriever"
    assert PublicCodeTypedGraphRetriever is CodeTypedGraphRetriever
    assert retriever.retriever_version
    assert retriever.traverse(_request(seeds=(), max_hops=1)).trace.status is (
        GraphTraversalStatus.NO_MATCH
    )
    assert response.trace.expanded_nodes == 0
    assert response.trace.expanded_edges == 0
    assert response.trace.graph_only_recovered == 0
    assert response.trace.latency_ms == 0.0


def test_hybrid_fusion_preserves_exact_signal_rank_locator_and_path() -> None:
    a, b, c = (_node(name) for name in ("a", "b", "c"))
    graph_response = CodeGraphRetriever(
        MemoryAdjacencyProvider(
            (
                _edge(a, b, name="ab"),
                _edge(b, c, name="bc"),
            )
        )
    ).search_with_trace(_request(max_hops=2))
    exact_b = _candidate("b", locator="code://exact-b", score=0.25)

    fused = fuse_graph_candidates(
        (exact_b,),
        graph_response.candidates,
        limit=3,
    )

    assert [item.entity_id for item in fused] == ["entity://b", "entity://c"]
    assert fused[0].locator == "code://exact-b"
    assert fused[0].relation_path.nodes[-1].locator == "code://exact-b"
    assert tuple(edge.edge_id for edge in fused[0].relation_path.edges) == ("edge://ab",)
    assert fused[0].source_fused_score == 0.25
    assert {rank.channel: rank.rank for rank in fused[0].raw_channel_ranks} == {
        CodeRetrievalChannel.EXACT: 0,
        CodeRetrievalChannel.GRAPH: 0,
    }
    assert [item.within_source_rank for item in fused] == [0, 1]


def _sqlite_unit(name: str) -> dict[str, Any]:
    body = f"def {name}(): pass"
    return {
        "id": f"unit://{name}",
        "entity_id": f"entity://{name}",
        "parent_unit_id": None,
        "repository_id": REPOSITORY,
        "generation_id": GENERATION,
        "project_id": PROJECT,
        "unit_type": "symbol",
        "ast_node_type": "function_definition",
        "ordinal": 0,
        "language": "python",
        "path": f"src/{name}.py",
        "qualified_name": name,
        "signature": f"{name}()",
        "identifiers": [name],
        "doc": "",
        "body": body,
        "content": body,
        "context_ref": {},
        "start_line": 1,
        "end_line": 1,
        "start_byte": 0,
        "end_byte": len(body),
        "token_count": 4,
        "content_hash": f"hash-unit-{name}",
        "builder_version": "test-builder",
        "quality_status": "ready",
        "acl_ref": ACL,
        "metadata": {},
    }


def _sqlite_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "graph.sqlite3")
    store.initialize()
    with store.transaction() as db:
        db.execute(
            """
            INSERT INTO repositories(
                id, project_id, name, source_type, local_path, head_commit,
                acl_ref, status, active_generation_id, created_at, updated_at
            ) VALUES (?, ?, 'alpha', 'local', '/tmp/alpha', ?, ?,
                      'ready', ?, 'now', 'now')
            """,
            (REPOSITORY, PROJECT, VERSION, ACL, GENERATION),
        )
        db.execute(
            """
            INSERT INTO index_generations(
                id, repository_id, commit_sha, status, started_at, completed_at
            ) VALUES (?, ?, ?, 'published', 'now', 'now')
            """,
            (GENERATION, REPOSITORY, VERSION),
        )
        for name in ("a", "b"):
            db.execute(
                """
                INSERT INTO entities(
                    id, repository_id, generation_id, project_id, entity_type,
                    name, qualified_name, path, language, commit_sha,
                    content_hash, source_uri, acl_ref, content
                ) VALUES (?, ?, ?, ?, 'CodeSymbol', ?, ?, ?, 'python', ?,
                          ?, ?, ?, ?)
                """,
                (
                    f"entity://{name}",
                    REPOSITORY,
                    GENERATION,
                    PROJECT,
                    name,
                    name,
                    f"src/{name}.py",
                    VERSION,
                    f"hash-{name}",
                    f"code://{name}",
                    ACL,
                    f"def {name}(): pass",
                ),
            )
    store.insert_code_units([_sqlite_unit("a"), _sqlite_unit("b")])
    with store.transaction() as db:
        db.execute(
            """
            INSERT INTO edges(
                id, repository_id, generation_id, source_id, target_id,
                edge_type, derivation, confidence, evidence_locator
            ) VALUES ('edge://ab', ?, ?, 'entity://a', 'entity://b',
                      'CALLS', 'static_analysis', 0.9, 'code://a')
            """,
            (REPOSITORY, GENERATION),
        )
    store.upsert_code_index_publication(
        {
            "generation_id": GENERATION,
            "repository_id": REPOSITORY,
            "project_id": PROJECT,
            "builder": "test-builder",
            "sparse": "sparse@1",
            "embedding": "not-built",
            "graph": "graph_v2@1",
            "status": "published",
            "validation": {
                "capabilities": {
                    "graph_retrieval": True,
                }
            },
        }
    )
    return store


def test_sqlite_provider_reads_published_graph_in_query_only_mode(
    tmp_path: Path,
) -> None:
    store = _sqlite_store(tmp_path)
    provider = SQLiteGraphAdjacencyProvider(store.database_path)

    response = CodeGraphRetriever(provider).search_with_trace(
        _request(max_hops=1, min_confidence=0.5)
    )

    assert [item.entity_id for item in response.candidates] == ["entity://b"]
    assert response.candidates[0].candidate.retrieval_unit_id == "unit://b"
    assert response.candidates[0].path.edge_ids == ("edge://ab",)
    assert response.code_candidates[0].relation_path.edges[0].edge_id == "edge://ab"
    assert response.trace.publication_versions == (f"{REPOSITORY}:graph_v2@1",)
    connection = provider._connect()
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(Exception, match="readonly|read-only|query_only"):
            connection.execute("DELETE FROM edges")
    finally:
        connection.close()


def test_sqlite_provider_rejects_unknown_stored_edge_type(tmp_path: Path) -> None:
    store = _sqlite_store(tmp_path)
    with store.transaction() as database:
        database.execute(
            "UPDATE edges SET edge_type='UNKNOWN' WHERE id='edge://ab' AND generation_id=?",
            (GENERATION,),
        )

    response = CodeGraphRetriever(
        SQLiteGraphAdjacencyProvider(store.database_path)
    ).search_with_trace(_request(max_hops=1))

    assert response.candidates == ()
    assert response.trace.status is GraphTraversalStatus.NO_MATCH
