from __future__ import annotations

import copy
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.code_history.models import TestResultCreate as CodeHistoryTestResultCreate
from evidence_rag.models import EvidenceSearchRequest, SearchScope
from evidence_rag.rag.sources.code import (
    CODE_PLATFORM_INTEGRATION_VERSION,
    CodeHistoryDiffExpansionHook,
    CodePlatformIntegration,
    CodeTestValidationExpansionHookV2,
    ScipConsumer,
)


def _ingest(
    client: TestClient,
    source: str,
    *,
    acl_ref: str | None = None,
) -> dict[str, Any]:
    body = {"source": source}
    if acl_ref is not None:
        body["acl_ref"] = acl_ref
    queued = client.post("/v1/ingestion/repositories", json=body)
    assert queued.status_code == 202
    workflow = client.get(f"/v1/ingestion/workflows/{queued.json()['workflow_id']}").json()
    assert workflow["status"] == "completed"
    resolved = str(Path(source).resolve())
    return next(
        repository
        for repository in client.get("/v1/repositories").json()
        if repository["local_path"] == resolved
    )


def _make_git_repository(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "C7 Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "c7@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "C7 platform fixture"],
        check=True,
    )


def _varint(value: int) -> bytes:
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _protobuf_bytes(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _protobuf_text(field: int, value: str) -> bytes:
    return _protobuf_bytes(field, value.encode())


def _local_scip_fixture() -> bytes:
    symbol = "scip-python python fixture 1.0.0 src/ranking.py/HybridRanker#"
    tool = _protobuf_text(1, "scip-python") + _protobuf_text(2, "0.6.8")
    metadata = _varint(8) + _varint(1) + _protobuf_bytes(2, tool)
    occurrence = _protobuf_bytes(1, b"\x04\x00\x04") + _protobuf_text(2, symbol)
    symbol_information = _protobuf_text(1, symbol) + _protobuf_text(
        6,
        "HybridRanker",
    )
    definition_document = (
        _protobuf_text(1, "src/ranking.py")
        + _protobuf_bytes(3, symbol_information)
        + _protobuf_text(4, "python")
    )
    reference_document = (
        _protobuf_text(1, "src/semantic_consumer.py")
        + _protobuf_bytes(2, occurrence)
        + _protobuf_text(4, "python")
    )
    return (
        _protobuf_bytes(1, metadata)
        + _protobuf_bytes(2, definition_document)
        + _protobuf_bytes(2, reference_document)
    )


def test_runtime_only_constructs_c5_c6_production_components_on_opt_in(
    settings,
) -> None:
    default_app = create_app(settings)
    default_runtime = default_app.state.runtime
    assert default_runtime.ingestion.code_semantic_consumer is None
    assert default_runtime.code_integration.v2_pipeline.history_hook is None
    assert default_runtime.code_integration.v2_pipeline.test_hook is None

    configured = replace(
        settings,
        data_dir=settings.data_dir.parent / "c5-c6-opt-in",
        database_path=settings.data_dir.parent / "c5-c6-opt-in/test.sqlite3",
        repository_cache=settings.data_dir.parent / "c5-c6-opt-in/repositories",
        rag_code_unit_builder="ast-v2",
        rag_code_graph=True,
        rag_code_semantic_resolver="scip-python",
    )
    runtime = create_app(configured).state.runtime
    assert isinstance(runtime.ingestion.code_semantic_consumer, ScipConsumer)
    assert runtime.ingestion.code_semantic_consumer.runner is None
    assert isinstance(
        runtime.code_integration.v2_pipeline.history_hook,
        CodeHistoryDiffExpansionHook,
    )
    assert isinstance(
        runtime.code_integration.v2_pipeline.test_hook,
        CodeTestValidationExpansionHookV2,
    )


def test_local_scip_and_exact_c6_hooks_reach_production_query(
    settings,
    sample_repository,
    monkeypatch,
) -> None:
    (sample_repository / "src/semantic_consumer.py").write_text(
        "def consume(value):\n    return value\n",
        encoding="utf-8",
    )
    (sample_repository / "index.scip").write_bytes(_local_scip_fixture())
    _make_git_repository(sample_repository)
    ranking = sample_repository / "src/ranking.py"
    ranking.write_text(
        ranking.read_text(encoding="utf-8").replace(
            "class HybridRanker:",
            "class HybridRanker:\n    version = 2",
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(sample_repository), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(sample_repository), "commit", "-q", "-m", "change ranker"],
        check=True,
    )
    configured = replace(
        settings,
        rag_code_unit_builder="ast-v2",
        rag_code_graph=True,
        rag_code_semantic_resolver="scip-python",
    )
    app = create_app(configured)
    with TestClient(app) as client:
        repository = _ingest(client, str(sample_repository))
        publication = app.state.runtime.store.get_code_index_publication(
            repository["active_generation_id"],
            repository_id=repository["id"],
            active_only=True,
        )
        assert publication is not None
        semantic = publication["validation"]["semantic_resolver"]
        assert semantic["external_execution"] is False
        assert semantic["index_status"] == "complete"
        assert semantic["materialized_scip_edge_count"] >= 1
        assert publication["validation"]["capabilities"]["scip_semantic_edges"] is True

        with app.state.runtime.store.connection() as database:
            stored_edges = [
                dict(row)
                for row in database.execute(
                    """
                    SELECT id, source_id, target_id, edge_type, derivation
                    FROM edges
                    WHERE repository_id=? AND generation_id=?
                    ORDER BY id
                    """,
                    (repository["id"], repository["active_generation_id"]),
                ).fetchall()
            ]
        scip_edge_ids = {edge["id"] for edge in stored_edges if edge["derivation"] == "scip"}
        assert scip_edge_ids

        semantic_body = _code_body(
            repository,
            query="find references to HybridRanker",
        )
        semantic_body["limit"] = 20
        semantic_response = client.post(
            "/v1/evidence/search",
            json=semantic_body,
            headers={"X-RAG-Code-Engine": "v2"},
        )
        semantic_body_without_edges = copy.deepcopy(semantic_body)
        semantic_body_without_edges["include_edges"] = False
        semantic_response_without_edges = client.post(
            "/v1/evidence/search",
            json=semantic_body_without_edges,
            headers={"X-RAG-Code-Engine": "v2"},
        )
        projected_edge_id = next(
            edge["id"] for result in semantic_response.json()["results"] for edge in result["edges"]
        )
        original_search = app.state.runtime.code_integration.v2_pipeline.search_with_trace

        def tamper_edge_locator(*args: Any, **kwargs: Any) -> Any:
            fusion = original_search(*args, **kwargs)
            with app.state.runtime.store.transaction() as database:
                database.execute(
                    """
                    UPDATE edges
                       SET evidence_locator=evidence_locator || '#tampered'
                     WHERE id=? AND generation_id=?
                    """,
                    (projected_edge_id, repository["active_generation_id"]),
                )
            return fusion

        monkeypatch.setattr(
            app.state.runtime.code_integration.v2_pipeline,
            "search_with_trace",
            tamper_edge_locator,
        )
        tampered_response = client.post(
            "/v1/evidence/search",
            json=semantic_body,
            headers={"X-RAG-Code-Engine": "v2"},
        )
        monkeypatch.setattr(
            app.state.runtime.code_integration.v2_pipeline,
            "search_with_trace",
            original_search,
        )
        history_response = client.post(
            "/v1/evidence/search",
            json=_code_body(repository, query="why changed HybridRanker"),
            headers={"X-RAG-Code-Engine": "v2"},
        )
        history_hook = app.state.runtime.code_integration.v2_pipeline.history_hook
        assert isinstance(history_hook, CodeHistoryDiffExpansionHook)

        def fail_history_source(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("fixture history source unavailable")

        monkeypatch.setattr(history_hook.mapper, "map_hunk", fail_history_source)
        history_lkg_response = client.post(
            "/v1/evidence/search",
            json=_code_body(repository, query="why changed HybridRanker"),
            headers={"X-RAG-Code-Engine": "v2"},
        )
        test_result = app.state.runtime.code_history.record_test(
            CodeHistoryTestResultCreate(
                project_id=repository["project_id"],
                repository_id=repository["id"],
                commit_sha=repository["head_commit"],
                command="pytest -q tests/test_ranking.py",
                status="passed",
                exit_code=0,
                framework="pytest",
                metadata={
                    "observation": "observed",
                    "generation_id": repository["active_generation_id"],
                    "target_symbols": ["HybridRanker"],
                    "environment_ref": "environment://local-fixture",
                },
                acl_ref=repository["acl_ref"],
            )
        )
        test_response = client.post(
            "/v1/evidence/search",
            json=_code_body(repository, query="which test validates HybridRanker"),
            headers={"X-RAG-Code-Engine": "v2"},
        )
        with app.state.runtime.store.transaction() as database:
            database.execute(
                "UPDATE test_results SET metadata_json='{' WHERE id=?",
                (test_result["id"],),
            )
        test_lkg_response = client.post(
            "/v1/evidence/search",
            json=_code_body(repository, query="which test validates HybridRanker"),
            headers={"X-RAG-Code-Engine": "v2"},
        )

    assert history_response.status_code == 200
    assert any("history" in result["channels"] for result in history_response.json()["results"])
    assert history_lkg_response.status_code == 200
    assert any("history" in result["channels"] for result in history_lkg_response.json()["results"])
    assert test_response.status_code == 200
    assert any("test" in result["channels"] for result in test_response.json()["results"])
    assert test_lkg_response.status_code == 200
    assert any("test" in result["channels"] for result in test_lkg_response.json()["results"])
    assert semantic_response.status_code == 200
    semantic_payload = semantic_response.json()
    assert any(
        result["path"] == "src/semantic_consumer.py" and "graph" in result["channels"]
        for result in semantic_payload["results"]
    )
    projected_edges = [edge for result in semantic_payload["results"] for edge in result["edges"]]
    stored_edges_by_id = {edge["id"]: edge for edge in stored_edges}
    assert projected_edges
    assert len({edge["id"] for edge in projected_edges}) == len(projected_edges)
    assert set(edge["id"] for edge in projected_edges) <= set(stored_edges_by_id)
    assert set(edge["id"] for edge in projected_edges) & scip_edge_ids
    for edge in projected_edges:
        stored_edge = stored_edges_by_id[edge["id"]]
        assert edge["project_id"] == repository["project_id"]
        assert edge["repository_id"] == repository["id"]
        assert edge["ref"] == repository["head_commit"]
        assert edge["commit"] == repository["head_commit"]
        assert edge["generation_id"] == repository["active_generation_id"]
        assert edge["source_id"] == stored_edge["source_id"]
        assert edge["target_id"] == stored_edge["target_id"]
        assert edge["edge_type"] == stored_edge["edge_type"]
        assert edge["review_status"] == "machine_confirmed"
        assert edge["evidence_locator"]
    edge_trace = semantic_payload["trace"]["code_v2"]["edge_projection"]
    assert edge_trace == {
        "status": "complete",
        "projected_edge_count": len(projected_edges),
        "deduplicated_edge_count": edge_trace["deduplicated_edge_count"],
    }
    assert 0 <= edge_trace["deduplicated_edge_count"] <= 200
    assert semantic_response_without_edges.status_code == 200
    without_edges_payload = semantic_response_without_edges.json()
    assert all(not result["edges"] for result in without_edges_payload["results"])
    assert without_edges_payload["trace"]["code_v2"]["edge_projection"] == {
        "status": "disabled",
        "projected_edge_count": 0,
        "deduplicated_edge_count": 0,
    }
    assert tampered_response.status_code == 200
    tampered_payload = tampered_response.json()
    tampered_routing = tampered_payload["trace"]["routing"]
    assert tampered_routing["engine_selected"] == "v1"
    assert tampered_routing["fallback"] == {
        "status": "succeeded",
        "reason": "attestation_or_projection_unavailable",
        "component": "CodeSourceFusionPipeline",
        "component_version": "code-source-fusion-pipeline-v2",
    }
    assert all(result["edges"] == [] for result in tampered_payload["results"])


def _publish_sparse_v2(runtime: Any, repository: dict[str, Any]) -> None:
    publication = runtime.store.get_code_index_publication(
        repository["active_generation_id"],
        repository_id=repository["id"],
        active_only=True,
    )
    assert publication is not None
    assert publication["sparse"] == "fts5-code-v2"
    assert publication["validation"]["capabilities"]["sparse_retrieval"] is True


def _code_body(repository: dict[str, Any], *, query: str = "HybridRanker") -> dict[str, Any]:
    return {
        "query": query,
        "scope": {
            "project_id": repository["project_id"],
            "repository_ids": [repository["id"]],
            "commit": repository["head_commit"],
        },
        "include_edges": True,
    }


def test_default_v1_is_one_legacy_call_and_invalid_header_fails_closed(
    settings,
    monkeypatch,
) -> None:
    app = create_app(settings)
    calls: list[EvidenceSearchRequest] = []
    fixed = {
        "query_id": "q-v1",
        "query": "rank",
        "resolved_scope": SearchScope().model_dump(),
        "index_generation": [],
        "total": 1,
        "results": [
            {
                "entity_id": "entity://legacy-rank",
                "name": "rank",
                "edges": [{"id": "edge://legacy-must-not-project"}],
            }
        ],
        "trace": {
            "duration_ms": 1.0,
            "lexical_candidates": 0,
            "dense_candidates": 0,
            "dense_matches": 0,
            "fusion": "weighted-hybrid-v2",
            "embedding_model": "local-hash-v2",
            "queried_embedding_models": ["local-hash-v2"],
        },
    }

    def legacy(request: EvidenceSearchRequest) -> dict[str, Any]:
        calls.append(request)
        return copy.deepcopy(fixed)

    monkeypatch.setattr(app.state.runtime.retriever, "search", legacy)
    with TestClient(app) as client:
        response = client.post("/v1/evidence/search", json={"query": "rank"})
        invalid = client.post(
            "/v1/evidence/search",
            json={"query": "rank"},
            headers={"X-RAG-Code-Engine": "V2"},
        )
        invalid_platform = client.post(
            "/v1/search",
            json={"query": "rank", "sources": ["document"]},
            headers={"X-RAG-Code-Engine": "v3"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert len(calls) == 1
    expected = copy.deepcopy(fixed)
    expected["results"][0]["edges"] = []
    assert {key: value for key, value in payload.items() if key != "trace"} == {
        key: value for key, value in expected.items() if key != "trace"
    }
    assert payload["trace"]["routing"] == {
        "integration_version": CODE_PLATFORM_INTEGRATION_VERSION,
        "engine_requested": "v1",
        "engine_selected": "v1",
        "selection_reason": "default",
        "canary_percent": 0,
        "canary_bucket": CodePlatformIntegration.canary_bucket(calls[0]),
        "fallback": {
            "status": "not_used",
            "reason": "none",
            "component": "CodeSourceFusionPipeline",
            "component_version": "code-source-fusion-pipeline-v2",
        },
        "shadow": {"status": "disabled"},
    }
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "X-RAG-Code-Engine must be exactly 'v1' or 'v2'"
    assert invalid_platform.status_code == 422
    assert len(calls) == 1


def test_header_v1_beats_canary_and_bucket_is_stable(settings, monkeypatch) -> None:
    configured = replace(settings, rag_code_canary_percent=100)
    app = create_app(configured)
    calls = 0
    fixed = {
        "query_id": "q-v1",
        "query": "rank",
        "resolved_scope": SearchScope().model_dump(),
        "index_generation": [],
        "total": 0,
        "results": [],
        "trace": {},
    }

    def legacy(_: EvidenceSearchRequest) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return copy.deepcopy(fixed)

    monkeypatch.setattr(app.state.runtime.retriever, "search", legacy)
    request = EvidenceSearchRequest(
        query="rank",
        scope=SearchScope(
            project_id="project-rag",
            repository_ids=["repository://b", "repository://a"],
            commit="a" * 40,
        ),
    )
    reversed_request = EvidenceSearchRequest(
        query="rank",
        scope=SearchScope(
            project_id="project-rag",
            repository_ids=["repository://a", "repository://b"],
            commit="a" * 40,
        ),
    )
    assert CodePlatformIntegration.canary_bucket(request) == (
        CodePlatformIntegration.canary_bucket(reversed_request)
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/evidence/search",
            json={"query": "rank"},
            headers={"X-RAG-Code-Engine": "v1"},
        )
    assert response.status_code == 200
    assert calls == 1
    routing = response.json()["trace"]["routing"]
    assert routing["engine_selected"] == "v1"
    assert routing["selection_reason"] == "header"
    assert routing["canary_bucket"] is None


def test_opted_in_v2_projects_v1_shape_for_direct_and_platform(
    settings,
    sample_repository,
) -> None:
    _make_git_repository(sample_repository)
    configured = replace(
        settings,
        rag_code_unit_builder="ast-v2",
        rag_code_context="structured-v2",
    )
    app = create_app(configured)
    with TestClient(app) as client:
        repository = _ingest(client, str(sample_repository))
        _publish_sparse_v2(app.state.runtime, repository)
        publication = app.state.runtime.store.get_code_index_publication(
            repository["active_generation_id"],
            repository_id=repository["id"],
            active_only=True,
        )
        assert publication is not None, repository
        assert publication["status"] == "published", publication
        assert publication["embedding"] != "not-built", publication
        assert publication["validation"]["capabilities"]["dense_retrieval"] is True
        direct = client.post(
            "/v1/evidence/search",
            json=_code_body(repository),
            headers={"X-RAG-Code-Engine": "v2"},
        )
        platform = client.post(
            "/v1/search",
            json={
                "query": "HybridRanker",
                "project_id": repository["project_id"],
                "sources": ["code", "document"],
                "repository_ids": [repository["id"]],
                "commit": repository["head_commit"],
                "include_lineage": False,
            },
            headers={"X-RAG-Code-Engine": "v2"},
        )

    assert direct.status_code == 200
    payload = direct.json()
    assert payload["results"]
    assert payload["trace"]["routing"]["engine_selected"] == "v2", payload["trace"]["routing"][
        "fallback"
    ]
    assert payload["trace"]["routing"]["selection_reason"] == "header"
    assert payload["trace"]["code_v2"]["scope_attestation"] == "verified"
    assert payload["trace"]["code_v2"]["context"]["status"] == "unavailable"
    expected_fields = {
        "entity_id",
        "repository_id",
        "generation_id",
        "entity_type",
        "view_type",
        "name",
        "qualified_name",
        "path",
        "language",
        "commit",
        "start_line",
        "end_line",
        "evidence_locator",
        "acl_ref",
        "metadata",
        "lexical_score",
        "dense_score",
        "score",
        "channels",
        "edges",
        "snippet",
    }
    assert set(payload["results"][0]) == expected_fields
    assert all(item["repository_id"] == repository["id"] for item in payload["results"])
    assert all(item["commit"] == repository["head_commit"] for item in payload["results"])
    assert all(item["acl_ref"] == repository["acl_ref"] for item in payload["results"])
    assert all(item["edges"] == [] for item in payload["results"])
    assert payload["trace"]["code_v2"]["edge_projection"] == {
        "status": "no_match",
        "projected_edge_count": 0,
        "deduplicated_edge_count": 0,
    }

    assert platform.status_code == 200
    global_payload = platform.json()
    assert global_payload["results"]
    assert {item["source"] for item in global_payload["results"]} == {"code"}
    assert global_payload["trace"]["sources"]["code"]["routing"]["engine_selected"] == "v2"
    assert global_payload["evidence_pack"]["citation_map"]


def test_multi_repository_scope_fails_closed_before_direct_or_platform_v2(
    settings,
    sample_repository,
    tmp_path,
    monkeypatch,
) -> None:
    second_repository = tmp_path / "sample-code-second"
    shutil.copytree(sample_repository, second_repository)
    _make_git_repository(sample_repository)
    _make_git_repository(second_repository)
    configured = replace(
        settings,
        rag_code_engine="v2",
        rag_code_unit_builder="ast-v2",
    )
    app = create_app(configured)
    runtime = app.state.runtime

    with TestClient(app) as client:
        first = _ingest(client, str(sample_repository), acl_ref="team:first")
        second = _ingest(client, str(second_repository), acl_ref="team:second")
        assert first["project_id"] == second["project_id"]
        assert first["id"] != second["id"]
        _publish_sparse_v2(runtime, first)
        _publish_sparse_v2(runtime, second)

        pipeline_calls = 0
        legacy_calls = 0
        original_pipeline = runtime.code_integration.v2_pipeline.search_with_trace
        original_legacy = runtime.retriever.search

        def count_pipeline(*args: Any, **kwargs: Any) -> Any:
            nonlocal pipeline_calls
            pipeline_calls += 1
            return original_pipeline(*args, **kwargs)

        def count_legacy(request: EvidenceSearchRequest) -> dict[str, Any]:
            nonlocal legacy_calls
            legacy_calls += 1
            return original_legacy(request)

        monkeypatch.setattr(
            runtime.code_integration.v2_pipeline,
            "search_with_trace",
            count_pipeline,
        )
        monkeypatch.setattr(runtime.retriever, "search", count_legacy)

        direct_project_only = client.post(
            "/v1/evidence/search",
            json={
                "query": "HybridRanker",
                "scope": {"project_id": first["project_id"]},
            },
            headers={"X-RAG-Code-Engine": "v2"},
        )
        assert legacy_calls == 1
        assert pipeline_calls == 0
        platform_project_only = client.post(
            "/v1/search",
            json={
                "query": "HybridRanker",
                "project_id": first["project_id"],
                "sources": ["code"],
                "include_lineage": False,
            },
            headers={"X-RAG-Code-Engine": "v2"},
        )
        assert legacy_calls == 2
        assert pipeline_calls == 0
        direct_multi_repository = client.post(
            "/v1/evidence/search",
            json={
                "query": "HybridRanker",
                "scope": {
                    "project_id": first["project_id"],
                    "repository_ids": [first["id"], second["id"]],
                    "commit": first["head_commit"],
                },
            },
            headers={"X-RAG-Code-Engine": "v2"},
        )
        assert legacy_calls == 3
        assert pipeline_calls == 0
        platform_multi_repository = client.post(
            "/v1/search",
            json={
                "query": "HybridRanker",
                "project_id": first["project_id"],
                "sources": ["code"],
                "repository_ids": [first["id"], second["id"]],
                "commit": first["head_commit"],
                "include_lineage": False,
            },
            headers={"X-RAG-Code-Engine": "v2"},
        )
        assert legacy_calls == 4
        assert pipeline_calls == 0

    for response in (
        direct_project_only,
        platform_project_only,
        direct_multi_repository,
        platform_multi_repository,
    ):
        assert response.status_code == 200
        payload = response.json()
        trace = (
            payload["trace"]
            if "routing" in payload["trace"]
            else payload["trace"]["sources"]["code"]
        )
        assert trace["routing"]["engine_selected"] == "v1"
        assert trace["routing"]["fallback"]["reason"] == "scope_unavailable"
        assert "scope_attestation" not in trace["code_v2"]
    assert legacy_calls == 4
    assert pipeline_calls == 0


def test_setting_and_canary_route_v2_while_zero_stays_v1(
    settings,
    sample_repository,
) -> None:
    _make_git_repository(sample_repository)
    base = replace(settings, rag_code_unit_builder="ast-v2")
    for index, (configured, expected_reason) in enumerate(
        (
            (replace(base, rag_code_engine="v2"), "setting"),
            (replace(base, rag_code_canary_percent=100), "canary"),
        )
    ):
        data_dir = settings.data_dir.parent / f"v2-route-{index}"
        configured = replace(
            configured,
            data_dir=data_dir,
            database_path=data_dir / "test.sqlite3",
            repository_cache=data_dir / "repositories",
        )
        app = create_app(configured)
        with TestClient(app) as client:
            repository = _ingest(client, str(sample_repository))
            _publish_sparse_v2(app.state.runtime, repository)
            response = client.post("/v1/evidence/search", json=_code_body(repository))
        routing = response.json()["trace"]["routing"]
        assert routing["engine_selected"] == "v2", routing
        assert routing["selection_reason"] == expected_reason

    zero_data = settings.data_dir.parent / "v2-route-zero"
    zero = replace(
        base,
        data_dir=zero_data,
        database_path=zero_data / "test.sqlite3",
        repository_cache=zero_data / "repositories",
    )
    with TestClient(create_app(zero)) as client:
        repository = _ingest(client, str(sample_repository))
        response = client.post("/v1/evidence/search", json=_code_body(repository))
    routing = response.json()["trace"]["routing"]
    assert routing["engine_selected"] == "v1"
    assert routing["canary_percent"] == 0


def test_v2_error_falls_back_with_one_legacy_execution(
    settings,
    monkeypatch,
) -> None:
    app = create_app(replace(settings, rag_code_engine="v2"))
    runtime = app.state.runtime
    calls = 0
    original = runtime.retriever.search

    def legacy(request: EvidenceSearchRequest) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(request)

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("secret must not enter trace")

    monkeypatch.setattr(runtime.retriever, "search", legacy)
    monkeypatch.setattr(runtime.code_integration, "_governed_request", lambda request: request)
    monkeypatch.setattr(runtime.code_integration.v2_pipeline, "search_with_trace", fail)
    with TestClient(app) as client:
        response = client.post("/v1/evidence/search", json={"query": "rank"})

    assert response.status_code == 200
    assert calls == 1
    routing = response.json()["trace"]["routing"]
    assert routing["engine_requested"] == "v2"
    assert routing["engine_selected"] == "v1"
    assert routing["selection_reason"] == "fallback"
    assert routing["fallback"] == {
        "status": "succeeded",
        "reason": "v2_error",
        "component": "CodeSourceFusionPipeline",
        "component_version": "code-source-fusion-pipeline-v2",
    }
    assert "secret" not in response.text


def test_shadow_reuses_the_one_legacy_response_and_runs_production_v2(
    settings,
    sample_repository,
) -> None:
    _make_git_repository(sample_repository)
    configured = replace(
        settings,
        rag_code_unit_builder="ast-v2",
        rag_code_shadow=True,
    )
    app = create_app(configured)
    runtime = app.state.runtime
    assert runtime.code_shadow is not None
    assert runtime.code_shadow.v2 is runtime.code_integration.shadow_v2

    with TestClient(app) as client:
        repository = _ingest(client, str(sample_repository))
        _publish_sparse_v2(runtime, repository)
        response = client.post("/v1/evidence/search", json=_code_body(repository))
        assert runtime.code_shadow.flush(1.0)

    routing = response.json()["trace"]["routing"]
    assert routing["engine_selected"] == "v1"
    assert routing["shadow"]["status"] == "enabled"
    observation = runtime.code_shadow.observations()[-1]
    assert observation.status == "compared"
    assert observation.legacy_result_count == observation.adapter_result_count
    assert observation.v2_result_count is not None
    runtime.code_shadow.close()
