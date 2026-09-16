from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.code_history.store import CodeHistoryStore
from evidence_rag.experiments.models import (
    ExperimentCreate,
    MetricInput,
    RunCreate,
)
from evidence_rag.experiments.service import ExperimentService
from evidence_rag.experiments.store import ExperimentStore
from evidence_rag.platform.models import ExperimentAnalysisRequestV2, GlobalSearchRequest
from evidence_rag.platform.service import PlatformService
from evidence_rag.rag.sources.experiment.contracts_v2 import (
    ExperimentMetricRegistryEntryV2,
    MetricDirectionV2,
)
from evidence_rag.rag.sources.experiment.runtime_v2 import (
    ExperimentAnalysisSpecV2,
    ExperimentSourceRuntimeResultV2,
    ExperimentSourceRuntimeUnavailableV2,
    ExperimentSourceRuntimeV2,
)
from evidence_rag.rag.sources.experiment.store_v2 import ExperimentStoreV2
from evidence_rag.sources.models import SourceEventInput
from evidence_rag.sources.service import RawSourceService
from evidence_rag.sources.store import RawSourceStore
from evidence_rag.storage import SQLiteStore
from evidence_rag.workspace.service import WorkspaceService
from evidence_rag.workspace.store import WorkspaceStore


def _production_experiment_service(
    tmp_path: Path,
) -> tuple[ExperimentService, str, tuple[str, str]]:
    database = SQLiteStore(tmp_path / "formal.sqlite3")
    database.initialize()
    CodeHistoryStore(database).initialize()
    workspace_store = WorkspaceStore(database)
    workspace_store.initialize()
    workspace_store.create_project(
        {
            "id": "project-runtime",
            "name": "Runtime",
            "description": "",
            "owner": "tests",
            "acl_ref": "project:runtime",
            "classification": "internal",
            "status": "active",
            "settings": {},
        }
    )
    workspace = WorkspaceService(workspace_store)
    raw_store = RawSourceStore(database)
    raw_store.initialize()
    raw_service = RawSourceService(raw_store, tmp_path / "raw")
    experiment_store = ExperimentStore(database)
    experiment_store.initialize()
    service = ExperimentService(experiment_store, workspace, raw_service)

    experiment = service.create_experiment(
        ExperimentCreate(
            project_id="project-runtime",
            title="Runtime benchmark",
            objective="Compare two governed runs.",
            hypothesis="The candidate improves accuracy.",
            owner="tests",
            status="completed",
            tags=["runtime"],
        )
    )
    runs = []
    for external_id, seed, value in (
        ("run-external-a", 1, 0.80),
        ("run-external-b", 2, 0.90),
    ):
        run = service.create_run(
            RunCreate(
                experiment_id=experiment["id"],
                external_id=external_id,
                name=f"Run {seed}",
                status="completed",
                repository_id="repository-runtime",
                commit_sha="a" * 40,
                branch="main",
                dataset_id="dataset-runtime",
                dataset_version="v1",
                config={"seed": seed, "learning_rate": 0.01},
                environment={"python": "3.12", "cuda": "12.4"},
                command="python train.py",
                metrics=[
                    MetricInput(
                        name="accuracy",
                        value=value,
                        unit="ratio",
                        split="validation",
                        step=10,
                        higher_is_better=True,
                    )
                ],
            )
        )
        runs.append(run)

    source_id = "experiment-source://mlflow/runtime"
    tracking_uri = "memory://runtime-mlflow"
    experiment_store.upsert_source(
        {
            "id": source_id,
            "project_id": "project-runtime",
            "adapter_type": "mlflow",
            "tracking_uri": tracking_uri,
            "status": "ready",
            "stats": {"experiments": 1, "runs": 2},
        }
    )
    experiment_store.map_experiment(
        source_id,
        "external-experiment",
        experiment["id"],
        {"name": "Runtime benchmark"},
    )
    for run in runs:
        observation = experiment_store.list_run_observations(run["id"])[-1]
        raw_service.accept(
            SourceEventInput(
                source_type="mlflow",
                source_instance=tracking_uri,
                event_type="run.snapshot.observed",
                source_object_id=run["external_id"],
                source_version=observation["source_version"],
                project_id="project-runtime",
                acl_ref="project:runtime",
                source_uri=f"{tracking_uri}#/runs/{run['external_id']}",
                payload={"run_id": run["external_id"], "status": "completed"},
                adapter_version="test-mlflow-adapter-v1",
                schema_version="mlflow-run-v2",
                metadata={"experiment_id": "external-experiment"},
            )
        )
    return service, source_id, (runs[0]["id"], runs[1]["id"])


def _runtime(
    tmp_path: Path,
) -> tuple[ExperimentSourceRuntimeV2, str, tuple[str, str], ExperimentStoreV2]:
    service, source_id, run_ids = _production_experiment_service(tmp_path)
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    store = ExperimentStoreV2(isolated / "experiment-v2.sqlite3", isolated_root=isolated)
    store.initialize()
    runtime = ExperimentSourceRuntimeV2(
        service,
        store,
        metric_registry={
            "accuracy": ExperimentMetricRegistryEntryV2(
                canonical_name="accuracy",
                aliases=("acc",),
                description="Validation accuracy.",
                unit="ratio",
                direction=MetricDirectionV2.HIGHER,
                provenance="runtime-registry-v1",
            )
        },
    )
    return runtime, source_id, run_ids, store


def test_default_stays_v1_without_materializing_v2(tmp_path: Path) -> None:
    runtime, _source_id, _run_ids, store = _runtime(tmp_path)
    calls = 0

    def legacy() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"engine": "v1"}

    result = runtime.execute(
        engine=None,
        legacy=legacy,
        project_id="not-read",
        allowed_acl_refs=(),
        source_id="not-read",
        query_request={},
        semantic_query="not-read",
    )
    assert result == {"engine": "v1"}
    assert calls == 1
    assert store.counts()["generations"] == 0
    store.close()


def test_explicit_v2_uses_formal_source_pipeline_context_and_analysis(
    tmp_path: Path,
) -> None:
    runtime, source_id, run_ids, store = _runtime(tmp_path)
    result = runtime.execute(
        engine="v2",
        legacy=lambda: pytest.fail("explicit V2 must not execute legacy"),
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        source_id=source_id,
        query_request={
            "task": "compare",
            "run_ids": list(run_ids),
            "statuses": ["completed"],
        },
        semantic_query="compare validation accuracy",
        analysis=ExperimentAnalysisSpecV2(
            metric_name="accuracy",
            baseline_run_id=run_ids[0],
            split="validation",
            treatment_config_keys=frozenset({"seed"}),
            controlled_config_keys=frozenset({"learning_rate"}),
        ),
    )
    assert isinstance(result, ExperimentSourceRuntimeResultV2)
    assert result.authority.formal_source_id == source_id
    assert result.authority.run_count == 2
    assert len(result.identities) == 2
    assert all(item.pipeline_run_id.startswith("run-") for item in result.identities)
    assert result.pipeline.semantic.candidates
    assert result.pipeline.context == result.context
    assert len(result.analysis.comparability) == 1
    assert len(result.analysis.comparisons) == 1
    assert result.analysis.comparisons[0].status == "available"
    assert result.analysis.comparisons[0].improvement is True
    assert "comparability" in result.context.role_coverage
    assert "computed" in result.context.role_coverage

    repeated = runtime.query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        source_id=source_id,
        query_request={
            "task": "compare",
            "run_ids": list(run_ids),
            "statuses": ["completed"],
        },
        semantic_query="compare validation accuracy",
        analysis=ExperimentAnalysisSpecV2(
            metric_name="accuracy",
            baseline_run_id=run_ids[0],
            split="validation",
            treatment_config_keys=frozenset({"seed"}),
            controlled_config_keys=frozenset({"learning_rate"}),
        ),
        expected_source_generation=result.authority.source_generation,
        expected_watermark=result.authority.watermark,
    )
    assert repeated.authority == result.authority
    assert store.counts()["generations"] == 1
    store.close()


@pytest.mark.parametrize(
    ("allowed", "expected_watermark", "reason"),
    [
        ((), None, "project_acl_denied"),
        (("project:runtime",), "sha256:" + ("0" * 64), "expected_watermark_mismatch"),
    ],
)
def test_explicit_v2_fails_closed_without_legacy_fallback(
    tmp_path: Path,
    allowed: tuple[str, ...],
    expected_watermark: str | None,
    reason: str,
) -> None:
    runtime, source_id, run_ids, store = _runtime(tmp_path)
    legacy_calls = 0

    def legacy() -> None:
        nonlocal legacy_calls
        legacy_calls += 1

    with pytest.raises(ExperimentSourceRuntimeUnavailableV2) as captured:
        runtime.execute(
            engine="v2",
            legacy=legacy,
            project_id="project-runtime",
            allowed_acl_refs=allowed,
            source_id=source_id,
            query_request={"task": "compare", "run_ids": list(run_ids)},
            semantic_query="accuracy",
            expected_watermark=expected_watermark,
        )
    assert captured.value.reason_code == reason
    assert legacy_calls == 0
    assert store.counts()["generations"] == 0
    store.close()


def test_explicit_v2_rejects_quarantined_latest_source_event(tmp_path: Path) -> None:
    runtime, source_id, run_ids, store = _runtime(tmp_path)
    source = runtime.service.store.list_sources("project-runtime")[0]
    run = runtime.service.store.get_run(run_ids[0])
    assert run is not None
    runtime.service.sources.accept(
        SourceEventInput(
            source_type="mlflow",
            source_instance=source["tracking_uri"],
            event_type="run.snapshot.observed",
            source_object_id=run["external_id"],
            source_version="sha256:" + ("2" * 64),
            project_id="project-runtime",
            acl_ref="project:runtime",
            payload={"api_key": "sk-proj-1234567890abcdefghijklmnop"},
            adapter_version="test-mlflow-adapter-v1",
            schema_version="mlflow-run-v2",
            metadata={"experiment_id": "external-experiment"},
        )
    )
    with pytest.raises(ExperimentSourceRuntimeUnavailableV2) as captured:
        runtime.query_v2(
            project_id="project-runtime",
            allowed_acl_refs=("project:runtime",),
            source_id=source_id,
            query_request={"task": "compare", "run_ids": list(run_ids)},
            semantic_query="accuracy",
        )
    assert captured.value.reason_code == "source_event_unsafe"
    assert store.counts()["generations"] == 0
    store.close()


def test_strict_e3_analysis_requires_explicit_baseline_and_candidates(
    tmp_path: Path,
) -> None:
    runtime, source_id, run_ids, store = _runtime(tmp_path)
    with pytest.raises(
        ExperimentSourceRuntimeUnavailableV2,
        match="compare_spec_incomplete",
    ):
        runtime.query_v2(
            project_id="project-runtime",
            allowed_acl_refs=("project:runtime",),
            source_id=source_id,
            query_request={
                "task": "compare",
                "run_ids": list(run_ids),
                "statuses": ["completed"],
            },
            semantic_query="compare validation accuracy",
            analysis=ExperimentAnalysisSpecV2(
                metric_name="accuracy",
                split="validation",
                fail_closed=True,
            ),
        )

    selected = runtime.query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        source_id=source_id,
        query_request={
            "task": "compare",
            "run_ids": list(run_ids),
            "statuses": ["completed"],
        },
        semantic_query="compare validation accuracy",
        analysis=ExperimentAnalysisSpecV2(
            metric_name="accuracy",
            baseline_run_id=run_ids[0],
            candidate_run_ids=(run_ids[1],),
            split="validation",
            fail_closed=True,
        ),
    )
    assert len(selected.analysis.comparisons) == 1
    assert selected.analysis.comparisons[0].improvement is True

    aggregated = runtime.query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        source_id=source_id,
        query_request={
            "task": "aggregate",
            "run_ids": list(run_ids),
            "statuses": ["completed"],
        },
        semantic_query="aggregate validation accuracy",
        analysis=ExperimentAnalysisSpecV2(
            metric_name="accuracy",
            run_ids=run_ids,
            aggregation="mean",
            split="validation",
            fail_closed=True,
        ),
    )
    assert aggregated.analysis.aggregations[0].function == "mean"
    assert aggregated.analysis.aggregations[0].n == 2

    reproduced = runtime.query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        source_id=source_id,
        query_request={
            "task": "reproduce",
            "run_ids": list(run_ids),
            "statuses": ["completed"],
        },
        semantic_query="reproduce selected runs",
        analysis=ExperimentAnalysisSpecV2(
            run_ids=run_ids,
            reproduction_roles=("seed",),
            fail_closed=True,
        ),
    )
    assert len(reproduced.analysis.reproduction) == 2
    assert reproduced.analysis.requested_reproduction_roles == ("seed",)
    store.close()


def test_platform_exposes_typed_e3_analysis_and_selection_trace(
    tmp_path: Path,
) -> None:
    runtime, _source_id, run_ids, store = _runtime(tmp_path)
    platform = object.__new__(PlatformService)
    response = platform._search_experiment_source_v2_with_facade(
        runtime,
        GlobalSearchRequest(
            query="compare validation accuracy",
            project_id="project-runtime",
            sources=["experiment"],
            allowed_acl_refs=["project:runtime"],
            enforce_acl=True,
            experiment_analysis=ExperimentAnalysisRequestV2(
                task="compare",
                metric="accuracy",
                baseline_run_id=run_ids[0],
                candidate_run_ids=[run_ids[1]],
                split="validation",
                treatment_config_keys=["seed"],
                controlled_config_keys=["learning_rate"],
            ),
        ),
        limit=20,
    )
    assert response["analysis"][0]["comparisons"][0]["status"] == "available"
    trace = response["selection_trace"]
    assert trace["explicit"] is True
    assert trace["baseline_run_id"] == run_ids[0]
    assert trace["candidate_run_ids"] == [run_ids[1]]
    assert trace["split"] == "validation"
    assert sorted(trace["selected_run_ids"]) == sorted(run_ids)
    assert "acl_ref" not in str(response["context"])
    assert "project:runtime" not in str(response)
    store.close()
