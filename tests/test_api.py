import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import evidence_rag.api as api_module
from evidence_rag.api import create_app
from evidence_rag.models import CodexIngestRequest, RepositoryIngestRequest
from evidence_rag.rag.release_control_plane_v2 import canonical_release_control_plane_v2
from evidence_rag.rag.sources.codex.governance_v2 import (
    CODEX_RELEASE_THRESHOLDS,
    CODEX_REQUIRED_GUARDRAILS,
    CodexEvidenceStatus,
    CodexReleaseGuardrailV2,
    CodexReleaseMetricV2,
    CodexReleaseStage,
    build_codex_release_evidence_v2,
    evaluate_codex_release_v2,
)


def test_module_level_asgi_app_does_not_open_the_default_database(tmp_path) -> None:
    isolated_data = tmp_path / "deferred-data"
    environment = {
        **os.environ,
        "RAG_DATA_DIR": str(isolated_data),
        "RAG_ALLOWED_LOCAL_ROOTS": str(tmp_path),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sqlite3; "
                "from pathlib import Path; "
                "sqlite3.connect = lambda *_args, **_kwargs: "
                "(_ for _ in ()).throw(AssertionError('sqlite opened during cold status')); "
                "Path.mkdir = lambda *_args, **_kwargs: "
                "(_ for _ in ()).throw(AssertionError('path created during cold status')); "
                "from fastapi.testclient import TestClient; "
                "from evidence_rag.api import app; "
                "assert app.state.runtime is None; "
                "client = TestClient(app); "
                "client.__enter__(); "
                "response = client.get('/v1/rag/status'); "
                "assert response.status_code == 200; "
                "assert app.state.runtime is None; "
                "client.__exit__(None, None, None)"
            ),
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not isolated_data.exists()


def test_lazy_public_app_initializes_runtime_once_for_concurrent_non_status_requests(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    create = api_module.create_runtime

    def counted_create(configured):
        nonlocal calls
        calls += 1
        return create(configured)

    monkeypatch.setattr(api_module, "create_runtime", counted_create)
    app = create_app(settings, defer_runtime=True)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: client.get("/health"), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert calls == 1


def test_cold_status_does_not_create_runtime_database_or_directory(
    settings,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cold_data = tmp_path / "cold-status-data"
    configured = replace(
        settings,
        data_dir=cold_data,
        database_path=cold_data / "status.sqlite3",
        repository_cache=cold_data / "repositories",
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cold status created the application Runtime")

    monkeypatch.setattr(api_module, "create_runtime", forbidden)
    app = create_app(configured, defer_runtime=True)
    with TestClient(app) as client:
        response = client.get("/v1/rag/status")

    assert response.status_code == 200
    assert response.json()["sources"]["code"]["v2_stage"] == "NO_RELEASE"
    assert response.json()["performance"]["latency"]["availability"] == "UNAVAILABLE"
    assert app.state.runtime is None
    assert not cold_data.exists()


def test_rag_status_uses_read_only_canonical_authority_and_no_operational_run(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = replace(
        settings,
        api_token="status-token",
        enforce_acl=True,
        deployment_mode="production",
        rag_code_engine="v2",
        rag_code_canary_percent=100,
        rag_global_generator_prompt_v2=True,
        rag_global_context_packer_v2=True,
        rag_global_fusion_v2=True,
        rag_global_reranker_v2=True,
        rag_global_embedding_generation_v2=True,
        rag_global_source_retrievers_v2=True,
        rag_global_planner_v2=True,
    )
    app = create_app(configured)
    runtime = app.state.runtime
    assert runtime.release_control_plane is canonical_release_control_plane_v2()
    assert runtime.settings.rag_code_engine == "v1"
    assert runtime.settings.rag_code_canary_percent == 0
    with pytest.raises(AttributeError):
        runtime.release_control_plane = object()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("status endpoint executed an operational subsystem")

    snapshots = 0
    operational_snapshot = runtime.global_v2.operational_snapshot

    def observed_snapshot():
        nonlocal snapshots
        snapshots += 1
        return operational_snapshot()

    monkeypatch.setattr(runtime.store, "stats", forbidden)
    monkeypatch.setattr(runtime.global_v2, "run_v2", forbidden)
    monkeypatch.setattr(runtime.global_v2, "operational_snapshot", observed_snapshot)
    monkeypatch.setattr(runtime.source_runtime_v2, "get", forbidden)
    monkeypatch.setattr(runtime.source_runtime_v2, "operation", forbidden)
    assert runtime.source_runtime_v2.materialized_sources == ()
    assert runtime.source_runtime_v2.derived_root_created is False

    with TestClient(app) as client:
        response = client.get(
            "/v1/rag/status",
            headers={"Authorization": "Bearer status-token"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_engine"] == "v1"
    assert payload["quality_hold"] is True
    assert payload["decision"] == "QUALITY_HOLD"
    assert payload["release_authority"]["authority_id"] == (
        "rag-canonical-production-release-authority-v2"
    )
    assert payload["release_authority"]["authority_attested"] is False
    assert payload["release_authority"]["reviewed_source_count"] == 0
    assert payload["release_authority"]["reviewed_gate_count"] == 0
    assert payload["source_snapshot"]["status"] == "UNAVAILABLE"
    assert payload["rollback_snapshot"]["status"] == "UNAVAILABLE"
    assert payload["performance_snapshot"]["status"] == "UNAVAILABLE"
    assert payload["schema_version"] == "rag-ops-status-v1"
    assert payload["generated_at"].endswith("Z")
    assert set(payload["sources"]) >= {
        "code",
        "codex",
        "experiment",
        "notebook",
        "document",
        "workspace",
    }
    assert {
        payload["sources"][source]["v2_stage"]
        for source in ("code", "codex", "experiment", "notebook", "document", "workspace")
    } == {"NO_RELEASE"}
    assert payload["component_switches"]["planner"] is True
    assert payload["performance"]["index"]["exact"] is True
    assert payload["performance"]["latency"]["availability"] == "UNAVAILABLE"
    assert payload["reviewed_calibration"]["availability"] == "UNAVAILABLE"
    assert payload["verify_only"] is True
    assert payload["sanitized"] is True
    assert snapshots == 1
    assert runtime.source_runtime_v2.materialized_sources == ()
    assert runtime.source_runtime_v2.derived_root_created is False
    assert "/Users/" not in response.text
    assert "allowed_acl_refs" not in response.text
    assert '"query"' not in response.text
    assert "super-secret" not in response.text


def test_rag_status_sanitizes_operational_observer_failure(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)

    def fail():
        raise RuntimeError("secret=super-secret /Users/private/project allowed_acl_refs=restricted")

    monkeypatch.setattr(app.state.runtime.global_v2, "operational_snapshot", fail)
    with TestClient(app) as client:
        response = client.get("/v1/rag/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_engine"] == "v1"
    assert payload["quality_hold"] is True
    assert payload["sources"]["codex"]["v2_stage"] == "NO_RELEASE"
    assert payload["component_switches"]["availability"] == "UNAVAILABLE"
    assert payload["performance"]["index"]["availability"] == "UNAVAILABLE"
    assert "super-secret" not in response.text
    assert "/Users/private" not in response.text
    assert "restricted" not in response.text


def test_ingestion_and_evidence_search_api(settings, sample_repository) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        )
        assert response.status_code == 202
        workflow = client.get(f"/v1/ingestion/workflows/{response.json()['workflow_id']}").json()
        assert workflow["status"] == "completed"

        repository = client.get("/v1/repositories").json()[0]
        search = client.post(
            "/v1/evidence/search",
            json={
                "query": "HybridRanker rank",
                "scope": {"repository_ids": [repository["id"]]},
                "include_edges": True,
            },
        )
        assert search.status_code == 200
        payload = search.json()
        assert payload["results"]
        assert payload["trace"]["fusion"] == "weighted-hybrid-v2"
        assert payload["trace"]["dense_matches"] <= payload["trace"]["dense_candidates"]

        answer = client.post(
            "/v1/query",
            json={"question": "How are ranking scores combined?", "mode": "evidence"},
        ).json()
        assert answer["answer"]["status"] == "retrieval_only"
        assert answer["evidence_pack"]["citation_map"]


def test_repository_binding_is_project_scoped_without_rebinding_legacy_id(
    settings, sample_repository
) -> None:
    with TestClient(create_app(settings)) as client:
        client.post("/v1/projects", json={"id": "project-other", "name": "Other"})
        first = client.post(
            "/v1/ingestion/repositories",
            json={
                "source": str(sample_repository),
                "project_id": "project-rag",
                "acl_ref": "project:project-rag",
            },
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{first['workflow_id']}").json()["status"]
            == "completed"
        )
        rag_repository = client.get(
            "/v1/repositories", params={"project_id": "project-rag"}
        ).json()[0]
        assert rag_repository["binding_scope"] == "legacy_primary"

        second = client.post(
            "/v1/ingestion/repositories",
            json={
                "source": str(sample_repository),
                "project_id": "project-other",
                "acl_ref": "project:project-other",
            },
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{second['workflow_id']}").json()["status"]
            == "completed"
        )
        other_repository = client.get(
            "/v1/repositories", params={"project_id": "project-other"}
        ).json()[0]

        assert other_repository["id"] != rag_repository["id"]
        assert (
            other_repository["physical_repository_id"] == rag_repository["physical_repository_id"]
        )
        assert other_repository["binding_scope"] == "project_scoped"
        assert [
            item["id"]
            for item in client.get("/v1/repositories", params={"project_id": "project-rag"}).json()
        ] == [rag_repository["id"]]

        repeated = client.post(
            "/v1/ingestion/repositories",
            json={
                "source": str(sample_repository),
                "project_id": "project-rag",
                "acl_ref": "project:project-rag",
            },
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{repeated['workflow_id']}").json()["status"]
            == "completed"
        )
        assert (
            client.get("/v1/repositories", params={"project_id": "project-rag"}).json()[0]["id"]
            == rag_repository["id"]
        )

        unknown_project = client.post(
            "/v1/ingestion/repositories",
            json={
                "source": str(sample_repository),
                "project_id": "project-missing",
                "acl_ref": "project:project-missing",
            },
        )
        assert unknown_project.status_code == 404


def test_explicit_codex_v2_without_authority_returns_exact_v1_once(
    settings,
    sample_repository,
    sample_codex_home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    assert runtime.source_runtime_v2.materialized_sources == ()
    assert runtime.source_runtime_v2.derived_root_created is False

    with TestClient(app) as client:
        ingest = client.post(
            "/v1/ingestion/codex",
            json={
                "source": str(sample_codex_home),
                "project_path": str(sample_repository),
            },
        )
        assert ingest.status_code == 202, ingest.text
        workflow = client.get(f"/v1/ingestion/workflows/{ingest.json()['workflow_id']}").json()
        assert workflow["status"] == "completed"

        calls = 0
        legacy_search = runtime.codex_retriever.search

        def counted_legacy(request):
            nonlocal calls
            calls += 1
            return legacy_search(request)

        monkeypatch.setattr(runtime.codex_retriever, "search", counted_legacy)
        query = {"query": "validate rerank retrieval"}
        default = client.post("/v1/codex/search", json=query)
        assert default.status_code == 200, default.text
        assert runtime.source_runtime_v2.materialized_sources == ()
        assert runtime.source_runtime_v2.derived_root_created is False

        explicit = client.post(
            "/v1/codex/search",
            headers={"X-RAG-Codex-Engine": "v2"},
            json=query,
        )
        assert explicit.status_code == 200, explicit.text
        default_payload = default.json()
        explicit_payload = explicit.json()
        assert explicit_payload["results"] == default_payload["results"]
        assert explicit_payload["total"] == default_payload["total"]
        assert explicit_payload.get("context") == default_payload.get("context")
        route = explicit_payload["trace"]["release_route"]
        assert route["stage"] == "off"
        assert route["requested_engine"] == "v2"
        assert route["served_engine"] == "v1"
        assert route["fallback_reason"] == "authority_unavailable"
        assert route["lkg_status"] == "UNAVAILABLE"
        assert "runtime_version" not in explicit_payload["trace"]
        assert all(
            not str(item.get("episode_id") or "").startswith("codex-v2://episode/")
            for item in explicit_payload["results"]
        )
        platform = client.post(
            "/v1/search",
            headers={"X-RAG-Codex-Engine": "v2"},
            json={
                "query": "validate rerank retrieval",
                "sources": ["codex"],
            },
        )
        assert platform.status_code == 200, platform.text
        assert platform.json()["results"]
        platform_route = platform.json()["trace"]["sources"]["codex"]["release_route"]
        assert platform_route["stage"] == route["stage"]
        assert platform_route["served_engine"] == route["served_engine"]
        assert platform_route["authority_sha256"] == route["authority_sha256"]
        assert calls == 3
        assert runtime.source_runtime_v2.materialized_sources == ()
        assert runtime.source_runtime_v2.derived_root_created is False


def test_verified_codex_authority_runs_direct_and_platform_v2_without_legacy(
    settings,
    sample_repository,
    sample_codex_home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    with TestClient(app) as client:
        ingest = client.post(
            "/v1/ingestion/codex",
            json={
                "source": str(sample_codex_home),
                "project_path": str(sample_repository),
            },
        )
        assert ingest.status_code == 202, ingest.text
        workflow = client.get(f"/v1/ingestion/workflows/{ingest.json()['workflow_id']}").json()
        assert workflow["status"] == "completed"

        evidence = build_codex_release_evidence_v2(
            observed_stage=CodexReleaseStage.CANARY_25,
            proposed_stage=CodexReleaseStage.OPT_IN_100,
            artifact_set_sha256="sha256:" + "1" * 64,
            golden_package_sha256="sha256:" + "2" * 64,
            component_set_sha256="sha256:" + "3" * 64,
            metrics=tuple(
                CodexReleaseMetricV2(
                    name=name,
                    status=CodexEvidenceStatus.AVAILABLE,
                    numerator=threshold,
                    denominator=1,
                    value=threshold,
                )
                for name, (_direction, threshold) in CODEX_RELEASE_THRESHOLDS.items()
            ),
            guardrails=tuple(
                CodexReleaseGuardrailV2(
                    name=name,
                    status=CodexEvidenceStatus.AVAILABLE,
                    violations=0,
                )
                for name in CODEX_REQUIRED_GUARDRAILS
            ),
            production_observation=True,
            treatment_result_available=True,
            rollback_rehearsed=True,
        )
        decision = evaluate_codex_release_v2(evidence)
        authority = runtime.source_runtime_v2.install_release_authority(
            source="codex",
            project_id="project-rag",
            evidence=evidence,
            decision=decision,
        )
        assert authority.stage.value == "on"

        legacy_calls = 0
        legacy_search = runtime.codex_retriever.search

        def counted_legacy(request):
            nonlocal legacy_calls
            legacy_calls += 1
            return legacy_search(request)

        v2_calls = 0
        v2_search = runtime.platform._search_codex_source_v2

        def counted_v2(*args, **kwargs):
            nonlocal v2_calls
            v2_calls += 1
            return v2_search(*args, **kwargs)

        monkeypatch.setattr(runtime.codex_retriever, "search", counted_legacy)
        monkeypatch.setattr(runtime.platform, "_search_codex_source_v2", counted_v2)

        direct = client.post(
            "/v1/codex/search",
            headers={"X-RAG-Codex-Engine": "v2"},
            json={"query": "validate rerank retrieval"},
        )
        platform = client.post(
            "/v1/search",
            headers={"X-RAG-Codex-Engine": "v2"},
            json={
                "query": "validate rerank retrieval",
                "sources": ["codex"],
            },
        )

        assert direct.status_code == 200, direct.text
        assert platform.status_code == 200, platform.text
        assert direct.json()["results"]
        assert platform.json()["results"]
        direct_route = direct.json()["trace"]["release_route"]
        platform_route = platform.json()["trace"]["sources"]["codex"]["release_route"]
        for route in (direct_route, platform_route):
            assert route["stage"] == "on"
            assert route["requested_engine"] == "v2"
            assert route["served_engine"] == "v2"
            assert route["authority_sha256"] == authority.content_sha256
            assert route["fallback_reason"] is None
        assert direct_route["router_version"] == platform_route["router_version"]
        assert legacy_calls == 0
        assert v2_calls == 2
        assert runtime.source_runtime_v2.materialized_sources == ("codex",)
        status_payload = client.get("/v1/rag/status").json()
        assert status_payload["quality_hold"] is True
        assert status_payload["sources"]["codex"]["v2_stage"] == "NO_RELEASE"
        assert status_payload["sources"]["release_authority_count"] == 1


def test_query_provider_error_uses_stable_public_code(settings, monkeypatch) -> None:
    app = create_app(settings)

    def fail(*_args, **_kwargs):
        raise RuntimeError(
            "api_key=super-secret /Users/private/research allowed_acl_refs=restricted"
        )

    monkeypatch.setattr(app.state.runtime.answers, "answer", fail)
    with TestClient(app) as client:
        response = client.post("/v1/query", json={"question": "What changed?"})

    assert response.status_code == 502
    assert response.json() == {"detail": "answer_provider_failed"}
    assert "super-secret" not in response.text
    assert "/Users/private" not in response.text
    assert "restricted" not in response.text


def test_ingestion_workflows_are_classified_filtered_and_recovered(
    settings, sample_repository
) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    repository_id = runtime.ingestion.enqueue(
        RepositoryIngestRequest(source=str(sample_repository))
    )
    codex_id = runtime.codex_ingestion.enqueue(CodexIngestRequest(source=str(settings.codex_home)))

    with TestClient(app) as client:
        repository_items = client.get(
            "/v1/ingestion/workflows", params={"kind": "repository"}
        ).json()
        codex_items = client.get("/v1/ingestion/workflows", params={"kind": "codex"}).json()

    assert [item["id"] for item in repository_items] == [repository_id]
    assert repository_items[0]["kind"] == "repository"
    assert [item["id"] for item in codex_items] == [codex_id]
    assert codex_items[0]["kind"] == "codex"

    assert runtime.store.recover_interrupted_workflows() == 2
    recovered = runtime.store.get_workflow(repository_id)
    assert recovered and recovered["status"] == "failed"
    assert recovered["stage"] == "interrupted"


def test_repository_delete_removes_index_but_keeps_local_source(
    settings, sample_repository
) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/ingestion/repositories",
            json={"source": str(sample_repository), "project_id": "project-rag"},
        )
        assert response.status_code == 202
        repository = client.get(
            "/v1/repositories",
            params={"project_id": "project-rag"},
        ).json()[0]

        deleted = client.delete(
            "/v1/repositories/by-id",
            params={"repository_id": repository["id"]},
        )

        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert deleted.json()["removed"]["repositories"] == 1
        assert (
            client.get(
                "/v1/repositories",
                params={"project_id": "project-rag"},
            ).json()
            == []
        )
        assert sample_repository.is_dir()
        assert (sample_repository / "src" / "ranking.py").is_file()


def test_typed_experiment_and_notebook_endpoints_default_to_v1_without_materializing(
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(settings)
    calls: list[tuple[str, str]] = []
    retrieve_engine = app.state.runtime.platform._retrieve_source_engine

    def counted_engine(**kwargs):
        calls.append((kwargs["source"], kwargs["engine_requested"]))
        return retrieve_engine(**kwargs)

    monkeypatch.setattr(
        app.state.runtime.platform,
        "_retrieve_source_engine",
        counted_engine,
    )
    assert app.state.runtime.source_runtime_v2.materialized_sources == ()
    assert app.state.runtime.source_runtime_v2.derived_root_created is False
    with TestClient(app) as client:
        experiment_request = {
            "query": "compare accuracy",
            "analysis": {
                "task": "compare",
                "metric": "accuracy",
                "baseline_run_id": "run-baseline",
                "candidate_run_ids": ["run-candidate"],
                "split": "validation",
            },
        }
        experiment = client.post(
            "/v1/experiments/search",
            json=experiment_request,
        )
        assert experiment.status_code == 200
        assert experiment.json()["trace"]["release_route"]["served_engine"] == "v1"
        assert experiment.json()["trace"]["release_route"]["stage"] == "off"

        notebook = client.post(
            "/v1/notebooks/search",
            json={"query": "threshold parameter"},
        )
        assert notebook.status_code == 200
        assert notebook.json()["trace"]["release_route"]["served_engine"] == "v1"
        assert notebook.json()["trace"]["release_route"]["stage"] == "off"

        explicit_experiment = client.post(
            "/v1/experiments/search",
            headers={"X-RAG-Experiment-Engine": "v2"},
            json=experiment_request,
        )
        explicit_notebook = client.post(
            "/v1/notebooks/search",
            headers={"X-RAG-Notebook-Engine": "v2"},
            json={"query": "threshold parameter"},
        )
        assert explicit_experiment.status_code == 200
        assert explicit_notebook.status_code == 200
        for response in (explicit_experiment, explicit_notebook):
            route = response.json()["trace"]["release_route"]
            assert route["served_engine"] == "v1"
            assert route["stage"] == "off"
            assert route["fallback_reason"] == "authority_unavailable"
        platform_responses = []
        for source, header in (
            ("experiment", "X-RAG-Experiment-Engine"),
            ("notebook", "X-RAG-Notebook-Engine"),
        ):
            response = client.post(
                "/v1/search",
                headers={header: "v2"},
                json={"query": "authority absent", "sources": [source]},
            )
            assert response.status_code == 200
            platform_responses.append(response)
        for direct, platform_response, source in zip(
            (explicit_experiment, explicit_notebook),
            platform_responses,
            ("experiment", "notebook"),
            strict=True,
        ):
            direct_route = direct.json()["trace"]["release_route"]
            platform_route = platform_response.json()["trace"]["sources"][source]["release_route"]
            assert platform_route["stage"] == direct_route["stage"]
            assert platform_route["served_engine"] == direct_route["served_engine"]
            assert platform_route["authority_sha256"] == direct_route["authority_sha256"]
        assert calls == [
            ("experiment", "v1"),
            ("notebook", "v1"),
            ("experiment", "v1"),
            ("notebook", "v1"),
            ("experiment", "v1"),
            ("notebook", "v1"),
        ]
        assert app.state.runtime.source_runtime_v2.materialized_sources == ()
        assert app.state.runtime.source_runtime_v2.derived_root_created is False


def test_typed_notebook_api_does_not_project_filters_without_release_authority(
    settings,
    tmp_path,
) -> None:
    notebook_path = tmp_path / "typed-filter.ipynb"
    notebook_path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {"language_info": {"name": "python"}},
                "cells": [
                    {
                        "id": "parameters",
                        "cell_type": "code",
                        "metadata": {"tags": ["parameters"]},
                        "source": ["threshold = 0.8\n"],
                        "execution_count": 1,
                        "outputs": [],
                    },
                    {
                        "id": "failure",
                        "cell_type": "code",
                        "metadata": {},
                        "source": ["raise RuntimeError(threshold)\n"],
                        "execution_count": 2,
                        "outputs": [
                            {
                                "output_type": "error",
                                "ename": "RuntimeError",
                                "evalue": "0.8",
                                "traceback": ["RuntimeError: 0.8"],
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    with TestClient(create_app(settings)) as client:
        ingested = client.post(
            "/v1/notebooks/ingest",
            json={"source": str(notebook_path), "version": "typed-v1"},
        )
        assert ingested.status_code == 201, ingested.text
        response = client.post(
            "/v1/notebooks/search",
            headers={"X-RAG-Notebook-Engine": "v2"},
            json={
                "query": "threshold parameter",
                "spec": {
                    "filters": {
                        "statuses": ["failed"],
                        "parameter_names": ["threshold"],
                    }
                },
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "schema_version" not in payload
        assert payload["selection_trace"] == {}
        assert payload["trace"]["release_route"]["served_engine"] == "v1"
        assert payload["trace"]["release_route"]["fallback_reason"] == "authority_unavailable"
        assert client.app.state.runtime.source_runtime_v2.materialized_sources == ()
        assert "allowed_acl_refs" not in response.text


def test_typed_notebook_api_does_not_project_comparisons_without_release_authority(
    settings,
    tmp_path,
) -> None:
    notebook_path = tmp_path / "typed-compare.ipynb"

    def notebook_payload(value: int) -> dict:
        return {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {"language_info": {"name": "python"}},
            "cells": [
                {
                    "id": "value",
                    "cell_type": "code",
                    "metadata": {"tags": ["parameters"]},
                    "source": [f"value = {value}\n"],
                    "execution_count": 1,
                    "outputs": [
                        {
                            "output_type": "execute_result",
                            "execution_count": 1,
                            "data": {"text/plain": [str(value)]},
                            "metadata": {},
                        }
                    ],
                }
            ],
        }

    with TestClient(create_app(settings)) as client:
        notebook_path.write_text(json.dumps(notebook_payload(1)), encoding="utf-8")
        baseline = client.post(
            "/v1/notebooks/ingest",
            json={"source": str(notebook_path), "version": "baseline"},
        ).json()
        notebook_path.write_text(json.dumps(notebook_payload(2)), encoding="utf-8")
        candidate = client.post(
            "/v1/notebooks/ingest",
            json={"source": str(notebook_path), "version": "candidate"},
        ).json()
        selector = {
            "baseline": {
                "template_id": baseline["template_id"],
                "revision_id": baseline["id"],
                "execution_id": baseline["id"],
            },
            "candidate": {
                "template_id": candidate["template_id"],
                "revision_id": candidate["id"],
                "execution_id": candidate["id"],
            },
        }
        response = client.post(
            "/v1/notebooks/search",
            headers={"X-RAG-Notebook-Engine": "v2"},
            json={
                "query": "compare notebook revisions",
                "spec": {
                    "task": "compare",
                    "filters": {"template_ids": [baseline["template_id"]]},
                    "comparison": selector,
                },
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["comparisons"] == []
        assert payload["selection_trace"] == {}
        assert payload["trace"]["release_route"]["served_engine"] == "v1"
        assert payload["trace"]["release_route"]["fallback_reason"] == "authority_unavailable"

        selector["candidate"]["execution_id"] = baseline["id"]
        mismatch = client.post(
            "/v1/notebooks/search",
            headers={"X-RAG-Notebook-Engine": "v2"},
            json={
                "query": "compare notebook revisions",
                "spec": {
                    "task": "compare",
                    "comparison": selector,
                },
            },
        )
        assert mismatch.status_code == 200
        assert mismatch.json()["comparisons"] == []
        assert mismatch.json()["trace"]["release_route"]["served_engine"] == "v1"
        assert client.app.state.runtime.source_runtime_v2.materialized_sources == ()
