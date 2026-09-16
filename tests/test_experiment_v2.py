from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from release_authority_helpers import install_experiment_v2_authority

from evidence_rag.api import create_app
from evidence_rag.rag.experiment_v2 import (
    ExperimentEngineOverrideError,
    ExperimentStructuredRetrieverV2,
    build_experiment_run_snapshot_v2,
    compare_experiment_runs_v2,
    parse_experiment_query_v2,
    validate_experiment_engine_override,
)
from evidence_rag.sources.models import SourceEventInput


def _run(
    run_id: str,
    *,
    experiment_id: str = "experiment://fixture/main",
    dataset_id: str = "dataset-a",
    dataset_version: str = "v1",
    accuracy: float = 0.91,
    status: str = "completed",
    environment: dict[str, str] | None = None,
) -> dict:
    return {
        "id": run_id,
        "display_key": "RUN-" + run_id.rsplit("/", 1)[-1].upper(),
        "project_id": "project-rag",
        "experiment_id": experiment_id,
        "name": run_id.rsplit("/", 1)[-1],
        "status": status,
        "repository_id": None,
        "commit_sha": "a" * 40,
        "branch": "main",
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "config": {"seed": 7, "learning_rate": 0.001},
        "environment": environment or {"python": "3.13", "device": "cpu"},
        "started_at": "2026-07-29T00:00:00+00:00",
        "completed_at": "2026-07-29T00:05:00+00:00",
        "updated_at": "2026-07-29T00:05:00+00:00",
        "metrics": [
            {
                "id": f"metric://{run_id.rsplit('/', 1)[-1]}/accuracy",
                "name": "accuracy",
                "value": accuracy,
                "unit": "ratio",
                "split": "test",
                "step": 1,
                "higher_is_better": 1,
            },
            {
                "id": f"metric://{run_id.rsplit('/', 1)[-1]}/latency",
                "name": "p95_ms",
                "value": 120.0,
                "unit": "ms",
                "split": "test",
                "step": 1,
                "higher_is_better": 0,
            },
        ],
    }


def _publish_ready_experiment_source(
    runtime: Any,
    *,
    experiment_id: str,
    runs: tuple[dict, ...],
) -> str:
    source_id = "experiment-source://mlflow/authorized-v2"
    tracking_uri = "memory://authorized-v2-mlflow"
    runtime.experiments.store.upsert_source(
        {
            "id": source_id,
            "project_id": "project-rag",
            "adapter_type": "mlflow",
            "tracking_uri": tracking_uri,
            "status": "ready",
            "stats": {"experiments": 1, "runs": len(runs)},
        }
    )
    runtime.experiments.store.map_experiment(
        source_id,
        "external-authorized-experiment",
        experiment_id,
        {"name": "Authorized typed metric experiment"},
    )
    for run in runs:
        observation = runtime.experiments.store.list_run_observations(run["id"])[-1]
        runtime.experiments.sources.accept(
            SourceEventInput(
                source_type="mlflow",
                source_instance=tracking_uri,
                event_type="run.snapshot.observed",
                source_object_id=run["external_id"],
                source_version=observation["source_version"],
                project_id="project-rag",
                acl_ref="public",
                source_uri=f"{tracking_uri}#/runs/{run['external_id']}",
                payload={"run_id": run["external_id"], "status": "completed"},
                adapter_version="isolated-test-mlflow-adapter-v1",
                schema_version="mlflow-run-v2",
                metadata={"experiment_id": "external-authorized-experiment"},
            )
        )
    return source_id


def test_typed_query_parser_freezes_filters_and_numeric_truth() -> None:
    parsed = parse_experiment_query_v2(
        "best accuracy accuracy>=0.88 ratio dataset=dataset-a "
        "dataset_version=v1 status=completed seed=7"
    )

    assert parsed.mode == "best"
    assert parsed.metric_names == ("accuracy",)
    assert parsed.dataset_id == "dataset-a"
    assert parsed.dataset_version == "v1"
    assert parsed.statuses == ("completed",)
    assert parsed.config_filters == (("seed", "7"),)
    assert len(parsed.predicates) == 1
    assert parsed.predicates[0].operator == "gte"
    assert parsed.predicates[0].value == 0.88

    explicit = parse_experiment_query_v2("best metric=accuracy accuracy>=90%")
    assert explicit.metric_names == ("accuracy",)
    assert explicit.predicates[0].unit == "%"


def test_snapshot_is_content_addressed_and_comparability_is_strict() -> None:
    first = build_experiment_run_snapshot_v2(_run("run://fixture/one", accuracy=0.91))
    repeated = build_experiment_run_snapshot_v2(_run("run://fixture/one", accuracy=0.91))
    second = build_experiment_run_snapshot_v2(_run("run://fixture/two", accuracy=0.87))

    assert first == repeated
    assert first.content_digest.startswith("sha256:")
    assert first.locator.startswith("experiment-v2://run/")
    assert first.metrics[0].observation_id.startswith("metric-observation://")
    comparable = compare_experiment_runs_v2((first, second), "accuracy")
    assert comparable.comparable is True
    assert comparable.reasons == ()
    percent_run = _run("run://fixture/percent", accuracy=91.0)
    percent_run["metrics"][0]["unit"] = "%"
    percent = build_experiment_run_snapshot_v2(percent_run)
    normalized = compare_experiment_runs_v2((first, percent), "accuracy")
    assert normalized.comparable is True
    assert normalized.common_unit == "ratio"

    wrong_dataset = build_experiment_run_snapshot_v2(
        _run("run://fixture/three", dataset_id="dataset-b")
    )
    rejected = compare_experiment_runs_v2((first, wrong_dataset), "accuracy")
    assert rejected.comparable is False
    assert "dataset_mismatch_or_missing" in rejected.reasons

    unknown_direction_run = _run("run://fixture/unknown", accuracy=0.95)
    unknown_direction_run["metrics"][0].pop("higher_is_better")
    unknown_direction = build_experiment_run_snapshot_v2(unknown_direction_run)
    direction_rejected = compare_experiment_runs_v2((first, unknown_direction), "accuracy")
    assert direction_rejected.comparable is False
    assert "metric_direction_unknown" in direction_rejected.reasons


def test_experiment_engine_validation_is_explicit() -> None:
    assert validate_experiment_engine_override(None) == "v1"
    assert validate_experiment_engine_override(" V2 ") == "v2"
    try:
        validate_experiment_engine_override("latest")
    except ExperimentEngineOverrideError as exc:
        assert "expected v1 or v2" in str(exc)
    else:  # pragma: no cover - documents the fail-closed contract
        raise AssertionError("invalid engine must fail")


def test_platform_explicit_v2_without_authority_falls_back_to_v1(settings, monkeypatch) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    engine_calls: list[str] = []
    retrieve_source_engine = runtime.platform._retrieve_source_engine

    def tracked_retrieve_source_engine(**kwargs):
        if kwargs["source"] == "experiment":
            engine_calls.append(kwargs["engine_requested"])
        return retrieve_source_engine(**kwargs)

    monkeypatch.setattr(runtime.platform, "_retrieve_source_engine", tracked_retrieve_source_engine)
    with TestClient(app) as client:
        experiment = client.post(
            "/v1/experiments",
            json={
                "title": "Typed metric experiment",
                "objective": "select accurate completed runs",
                "status": "running",
            },
        ).json()
        common = {
            "experiment_id": experiment["id"],
            "dataset_id": "dataset-a",
            "dataset_version": "v1",
            "environment": {"python": "3.13", "device": "cpu"},
            "status": "completed",
        }
        first = client.post(
            "/v1/experiments/runs",
            json={
                **common,
                "name": "candidate-high",
                "config": {"seed": 7},
                "metrics": [
                    {
                        "name": "accuracy",
                        "value": 0.91,
                        "unit": "ratio",
                        "split": "test",
                    }
                ],
            },
        ).json()
        client.post(
            "/v1/experiments/runs",
            json={
                **common,
                "name": "candidate-low",
                "config": {"seed": 8},
                "metrics": [
                    {
                        "name": "accuracy",
                        "value": 0.72,
                        "unit": "ratio",
                        "split": "test",
                    }
                ],
            },
        )

        response = client.post(
            "/v1/search",
            headers={"X-RAG-Experiment-Engine": "v2"},
            json={
                "query": "best accuracy accuracy>=0.88 ratio dataset=dataset-a status=completed",
                "sources": ["experiment"],
                "limit": 10,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["trace"]["experiment_engine"] == "v1"
        assert payload["trace"]["source_engine_routing"]["experiment"] == {
            "requested": "v2",
            "selected": "v1",
            "served": "v1",
        }
        route = payload["trace"]["sources"]["experiment"]["release_route"]
        assert route["stage"] == "off"
        assert route["requested_engine"] == "v2"
        assert route["selected_engine"] == "v1"
        assert route["response_engine"] == "v1"
        assert route["served_engine"] == "v1"
        assert route["execute_v1"] is True
        assert route["execute_v2"] is False
        assert route["fallback_reason"] == "authority_unavailable"
        assert route["authority_sha256"] is None
        assert engine_calls == ["v1"]
        assert "experiment-typed-retrieval-v2" not in response.text
        assert all(
            not str(item.get("locator") or "").startswith("experiment-v2://")
            for item in payload["results"]
        )
        assert first["id"] not in {
            item["entity_id"]
            for item in payload["results"]
            if str(item.get("locator") or "").startswith("experiment-v2://")
        }
        assert client.app.state.runtime.source_runtime_v2.materialized_sources == ()
        assert client.app.state.runtime.source_runtime_v2.derived_root_created is False

        engine_calls.clear()
        default_response = client.post(
            "/v1/search",
            json={"query": "candidate-high", "sources": ["experiment"], "limit": 5},
        )
        assert default_response.status_code == 200
        assert default_response.json()["trace"]["experiment_engine"] == "v1"
        assert engine_calls == ["v1"]

        invalid = client.post(
            "/v1/search",
            headers={"X-RAG-Experiment-Engine": "latest"},
            json={"query": "accuracy", "sources": ["experiment"]},
        )
        assert invalid.status_code == 422

        invalid_answer = client.post(
            "/v1/query",
            headers={"X-RAG-Experiment-Engine": "future"},
            json={"question": "accuracy", "include": ["experiment"]},
        )
        assert invalid_answer.status_code == 422


def test_direct_and_platform_explicit_v2_with_evaluator_authority_serve_isolated_v2(
    settings, monkeypatch
) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    install_experiment_v2_authority(runtime)
    engine_calls: list[str] = []
    retrieve_source_engine = runtime.platform._retrieve_source_engine

    def tracked_retrieve_source_engine(**kwargs):
        if kwargs["source"] == "experiment":
            engine_calls.append(kwargs["engine_requested"])
        return retrieve_source_engine(**kwargs)

    monkeypatch.setattr(runtime.platform, "_retrieve_source_engine", tracked_retrieve_source_engine)
    assert runtime.source_runtime_v2.materialized_sources == ()
    assert runtime.source_runtime_v2.derived_root_created is False

    with TestClient(app) as client:
        experiment = client.post(
            "/v1/experiments",
            json={"title": "Authorized typed metric experiment", "status": "running"},
        ).json()
        runs: list[dict] = []
        for external_id, name, seed, accuracy in (
            ("authorized-baseline", "authorized-baseline", 7, 0.81),
            ("authorized-candidate", "authorized-candidate", 8, 0.91),
        ):
            created = client.post(
                "/v1/experiments/runs",
                json={
                    "experiment_id": experiment["id"],
                    "external_id": external_id,
                    "name": name,
                    "dataset_id": "dataset-a",
                    "dataset_version": "v1",
                    "status": "completed",
                    "config": {"seed": seed, "learning_rate": 0.001},
                    "environment": {"python": "3.13", "device": "cpu"},
                    "metrics": [
                        {
                            "name": "accuracy",
                            "value": accuracy,
                            "unit": "ratio",
                            "split": "validation",
                            "higher_is_better": True,
                        }
                    ],
                },
            )
            assert created.status_code == 201, created.text
            runs.append(created.json())
        source_id = _publish_ready_experiment_source(
            runtime,
            experiment_id=experiment["id"],
            runs=tuple(runs),
        )
        assert runtime.experiments.store.list_sources("project-rag")[0]["status"] == "ready"
        assert (
            runtime.experiments.store.source_mapping(source_id, "external-authorized-experiment")
            == experiment["id"]
        )

        direct_response = client.post(
            "/v1/experiments/search",
            headers={"X-RAG-Experiment-Engine": "v2"},
            json={
                "query": "compare validation accuracy",
                "experiment_ids": [experiment["id"]],
                "analysis": {
                    "task": "compare",
                    "metric": "accuracy",
                    "baseline_run_id": runs[0]["id"],
                    "candidate_run_ids": [runs[1]["id"]],
                    "split": "validation",
                    "treatment_config_keys": ["seed"],
                    "controlled_config_keys": ["learning_rate"],
                },
                "limit": 10,
            },
        )
        assert direct_response.status_code == 200, direct_response.text
        direct_payload = direct_response.json()
        direct_route = direct_payload["trace"]["release_route"]
        assert direct_route["stage"] == "on"
        assert direct_route["requested_engine"] == "v2"
        assert direct_route["selected_engine"] == "v2"
        assert direct_route["served_engine"] == "v2"
        assert direct_route["execute_v1"] is False
        assert direct_route["execute_v2"] is True
        assert direct_route["fallback_reason"] is None
        assert direct_route["authority_sha256"].startswith("sha256:")
        assert direct_payload["trace"]["runtime_version"] == "experiment-source-runtime-v2"
        assert direct_payload["trace"]["source_count"] == 1
        assert direct_payload["selection_trace"]["selected_run_ids"] == sorted(
            run["id"] for run in runs
        )
        assert direct_payload["results"]

        response = client.post(
            "/v1/search",
            headers={"X-RAG-Experiment-Engine": "v2"},
            json={
                "query": "validation accuracy",
                "sources": ["experiment"],
                "experiment_ids": [experiment["id"]],
                "limit": 10,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["trace"]["experiment_engine"] == "v2"
        assert payload["trace"]["source_engine_routing"]["experiment"] == {
            "requested": "v2",
            "selected": "v2",
            "served": "v2",
        }
        route = payload["trace"]["sources"]["experiment"]["release_route"]
        assert route["stage"] == "on"
        assert route["requested_engine"] == "v2"
        assert route["selected_engine"] == "v2"
        assert route["served_engine"] == "v2"
        assert route["execute_v1"] is False
        assert route["execute_v2"] is True
        assert route["fallback_reason"] is None
        assert route["authority_sha256"] == direct_route["authority_sha256"]
        assert payload["trace"]["sources"]["experiment"]["runtime_version"] == (
            "experiment-source-runtime-v2"
        )
        assert payload["results"]
        assert all(
            item["metadata"]["runtime_version"] == "experiment-source-runtime-v2"
            for item in payload["results"]
        )
        assert engine_calls == ["v2", "v2"]
        assert runtime.source_runtime_v2.materialized_sources == ("experiment",)
        assert runtime.source_runtime_v2.derived_root_created is True
        facade = runtime.source_runtime_v2.get("experiment")
        assert facade.v2_store.database != settings.database_path
        assert facade.v2_store.database.is_relative_to(facade.v2_store.isolated_root)

        default_route = runtime.source_runtime_v2.route(
            source="experiment",
            project_id="project-rag",
            request_id="experiment-default-remains-v1",
            requested_engine=None,
        )
        assert default_route.selected_engine == "v1"
        assert default_route.response_engine == "v1"
        assert default_route.execute_v1 is True
        assert default_route.execute_v2 is False
        assert engine_calls == ["v2", "v2"]

        status = client.get("/v1/rag/status")
        assert status.status_code == 200, status.text
        assert status.json()["decision"] == "QUALITY_HOLD"
        assert status.json()["default_engine"] == "v1"
        assert status.json()["quality_hold"] is True


def test_best_mode_excludes_failed_runs_by_default_and_builds_context(settings) -> None:
    with TestClient(create_app(settings)) as client:
        experiment = client.post(
            "/v1/experiments",
            json={"title": "Best run safety", "status": "running"},
        ).json()
        common = {
            "experiment_id": experiment["id"],
            "dataset_id": "dataset-a",
            "dataset_version": "v1",
            "metrics": [
                {
                    "name": "accuracy",
                    "value": 0.8,
                    "unit": "ratio",
                    "split": "test",
                }
            ],
        }
        client.post(
            "/v1/experiments/runs",
            json={**common, "name": "completed candidate", "status": "completed"},
        )
        failed = client.post(
            "/v1/experiments/runs",
            json={
                **common,
                "name": "failed candidate with misleading score",
                "status": "failed",
                "metrics": [
                    {
                        "name": "accuracy",
                        "value": 0.99,
                        "unit": "ratio",
                        "split": "test",
                    }
                ],
            },
        ).json()
        result = client.app.state.runtime.experiment_v2.search(
            project_id="project-rag",
            query="best metric=accuracy dataset=dataset-a",
        )
        assert failed["id"] not in {item["entity_id"] for item in result["results"]}
        assert result["context"]["schema_version"] == "experiment-structured-context-v1"
        assert result["context"]["reasoning_included"] is False
        assert result["context"]["runs"]


def test_request_experiment_scope_is_not_projected_without_release_authority(settings) -> None:
    with TestClient(create_app(settings)) as client:
        first = client.post(
            "/v1/experiments",
            json={"title": "Scoped A", "status": "running"},
        ).json()
        second = client.post(
            "/v1/experiments",
            json={"title": "Scoped B", "status": "running"},
        ).json()
        created = client.post(
            "/v1/experiments/runs",
            json={
                "experiment_id": second["id"],
                "name": "Forbidden scope run",
                "status": "completed",
            },
        )
        assert created.status_code == 201, created.text

        response = client.post(
            "/v1/search",
            headers={"X-RAG-Experiment-Engine": "v2"},
            json={
                "query": f"experiment_id:{second['id']}",
                "sources": ["experiment"],
                "experiment_ids": [first["id"]],
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["results"] == []
        trace = payload["trace"]["sources"]["experiment"]
        assert trace["status"] == "no_match"
        assert trace["release_route"]["served_engine"] == "v1"
        assert trace["release_route"]["fallback_reason"] == "authority_unavailable"
        assert client.app.state.runtime.source_runtime_v2.materialized_sources == ()


def test_v2_retriever_acl_is_fail_closed(settings) -> None:
    from evidence_rag.runtime import create_runtime

    runtime = create_runtime(settings)
    with runtime.store.transaction() as db:
        db.execute("UPDATE projects SET acl_ref='team:secret' WHERE id='project-rag'")
    response = ExperimentStructuredRetrieverV2(runtime.experiments.store).search(
        project_id="project-rag",
        query="accuracy",
        allowed_acl_refs=[],
        enforce_acl=True,
        limit=10,
    )
    assert response["results"] == []
    assert response["trace"]["status"] == "ACL_DENIED"
