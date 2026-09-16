from __future__ import annotations

import threading
from pathlib import Path

from evidence_rag.experiments.models import ExperimentCreate, MetricInput, RunCreate
from evidence_rag.platform.models import GlobalSearchRequest
from evidence_rag.rag.sources.document.contracts import canonical_sha256
from evidence_rag.rag.sources.document.release_v2 import (
    DocumentReleaseEvidence,
    DocumentReleaseStage,
    evaluate_document_release_v2,
)
from evidence_rag.rag.sources.experiment.governance_v2 import (
    EvidenceAvailabilityV2,
    ExperimentReleaseMetricV2,
    ExperimentReleaseStageV2,
    build_experiment_release_evidence_v2,
    evaluate_experiment_release_v2,
)
from evidence_rag.runtime import create_runtime
from evidence_rag.sources.models import SourceEventInput


def _document_release_evidence(
    *,
    observed: DocumentReleaseStage,
    proposed: DocumentReleaseStage,
    production_observation: bool,
) -> DocumentReleaseEvidence:
    values = {
        "evaluator_version": "document-release-evaluator-v2",
        "observed_stage": observed,
        "proposed_stage": proposed,
        "artifact_set_sha256": "sha256:" + "1" * 64,
        "golden_package_sha256": "sha256:" + "2" * 64,
        "retriever_version": "document-retriever-v2",
        "reranker_version": "document-reranker-v2",
        "context_builder_version": "document-context-v2",
        "production_observation": production_observation,
        "metrics": (),
        "guardrails": (),
    }
    return DocumentReleaseEvidence(
        evidence_id=("docrelease-" + canonical_sha256(values).removeprefix("sha256:")[:64]),
        **values,
    )


def test_five_source_router_defaults_off_and_installs_only_reproduced_authority(
    settings,
) -> None:
    runtime = create_runtime(settings)
    registry = runtime.source_runtime_v2
    for source in ("codex", "experiment", "notebook", "document", "workspace"):
        default = registry.route(
            source=source,
            project_id="project-rag",
            request_id=f"request-default-{source}",
            requested_engine="v2",
        )
        assert default.stage.value == "off"
        assert default.response_engine == "v1"
        assert default.execute_v2 is False
        assert default.authority_sha256 is None

    shadow_evidence = _document_release_evidence(
        observed=DocumentReleaseStage.ISOLATED_BASELINE,
        proposed=DocumentReleaseStage.SHADOW_INTERNAL_100,
        production_observation=True,
    )
    shadow_decision = evaluate_document_release_v2(shadow_evidence)
    authority = registry.install_release_authority(
        source="document",
        project_id="project-rag",
        evidence=shadow_evidence,
        decision=shadow_decision,
    )
    shadow = registry.route(
        source="document",
        project_id="project-rag",
        request_id="request-shadow",
        requested_engine=None,
    )
    assert shadow.stage.value == "shadow"
    assert shadow.execute_v1 and shadow.execute_v2 and shadow.shadow
    assert shadow.response_engine == "v1"
    assert shadow.authority_sha256 == authority.content_sha256

    unverified = shadow_decision.model_copy(update={"reason": "tampered"})
    try:
        registry.install_release_authority(
            source="document",
            project_id="project-rag",
            evidence=shadow_evidence,
            decision=unverified,
        )
    except ValueError as error:
        assert "evaluator-verifiable" in str(error)
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("tampered evaluator decision was accepted")


def test_router_covers_canary_on_stable_and_verified_lkg(settings) -> None:
    runtime = create_runtime(settings)
    registry = runtime.source_runtime_v2
    cases = (
        (
            DocumentReleaseStage.SHADOW_INTERNAL_100,
            DocumentReleaseStage.CANARY_5,
            None,
            "canary",
        ),
        (
            DocumentReleaseStage.CANARY_25,
            DocumentReleaseStage.OPT_IN_100,
            None,
            "on",
        ),
        (
            DocumentReleaseStage.OPT_IN_100,
            DocumentReleaseStage.DEFAULT_V2,
            "sha256:" + "9" * 64,
            "stable",
        ),
    )
    authority = None
    for observed, proposed, external, expected_stage in cases:
        evidence = _document_release_evidence(
            observed=observed,
            proposed=proposed,
            production_observation=True,
        )
        decision = evaluate_document_release_v2(evidence)
        authority = registry.install_release_authority(
            source="document",
            project_id="project-rag",
            evidence=evidence,
            decision=decision,
            external_authority_sha256=external,
        )
        route = registry.route(
            source="document",
            project_id="project-rag",
            request_id=f"request-{expected_stage}",
            requested_engine=("v2" if expected_stage == "on" else None),
        )
        assert route.stage.value == expected_stage
        if expected_stage == "on":
            assert route.response_engine == "v2"
        if expected_stage == "stable":
            assert route.response_engine == "v2"

    assert authority is not None
    lkg = registry.record_v2_success(
        project_id="project-rag",
        source="document",
        authority_sha256=authority.content_sha256,
        generation="sha256:" + "8" * 64,
        candidate_count=3,
    )
    assert registry.last_known_good(project_id="project-rag", source="document") == lkg
    snapshot = registry.operational_snapshot()
    assert snapshot["verified_lkg_count"] == 1
    assert "project-rag" not in str(snapshot)

    replacement_evidence = _document_release_evidence(
        observed=DocumentReleaseStage.OPT_IN_100,
        proposed=DocumentReleaseStage.DEFAULT_V2,
        production_observation=True,
    )
    registry.install_release_authority(
        source="document",
        project_id="project-rag",
        evidence=replacement_evidence,
        decision=evaluate_document_release_v2(replacement_evidence),
        external_authority_sha256="sha256:" + "7" * 64,
    )
    assert registry.last_known_good(project_id="project-rag", source="document") is None
    assert registry.operational_snapshot()["verified_lkg_count"] == 0


def test_experiment_authority_routes_the_evaluator_promoted_stage(settings) -> None:
    runtime = create_runtime(settings)
    evidence = build_experiment_release_evidence_v2(
        project_id="project-rag",
        stage=ExperimentReleaseStageV2.OFFLINE,
        previous_evidence_sha256=None,
        baseline_artifact_sha256="sha256:" + "1" * 64,
        treatment_artifact_sha256="sha256:" + "2" * 64,
        metrics=(
            ExperimentReleaseMetricV2(
                name="predicate_accuracy",
                numerator=9,
                denominator=10,
                value=0.9,
                threshold=0.8,
                comparison="gte",
                availability=EvidenceAvailabilityV2.AVAILABLE,
                source_evidence_sha256="sha256:" + "3" * 64,
            ),
        ),
        secret_leakage_count=0,
        acl_leakage_count=0,
        truth_drift_count=0,
        fallback_count=0,
        p95_latency_ms=100,
        storage_bytes=1_000,
        rollback_rehearsed=True,
        synthetic=False,
        production_observation=True,
    )
    decision = evaluate_experiment_release_v2(
        evidence,
        deployed_stage=ExperimentReleaseStageV2.OFFLINE,
    )
    authority = runtime.source_runtime_v2.install_release_authority(
        source="experiment",
        project_id="project-rag",
        evidence=evidence,
        decision=decision,
    )

    assert authority.stage.value == "shadow"
    route = runtime.source_runtime_v2.route(
        source="experiment",
        project_id="project-rag",
        request_id="experiment-shadow",
        requested_engine=None,
    )
    assert route.stage.value == "shadow"
    assert route.execute_v1 is True
    assert route.execute_v2 is True
    assert route.response_engine == "v1"


def test_shadow_failure_serves_v1_once_and_reports_exact_fallback(
    settings,
    monkeypatch,
) -> None:
    runtime = create_runtime(settings)
    evidence = _document_release_evidence(
        observed=DocumentReleaseStage.ISOLATED_BASELINE,
        proposed=DocumentReleaseStage.SHADOW_INTERNAL_100,
        production_observation=True,
    )
    runtime.source_runtime_v2.install_release_authority(
        source="document",
        project_id="project-rag",
        evidence=evidence,
        decision=evaluate_document_release_v2(evidence),
    )
    calls: list[str] = []

    def engine(*, source, request, limit, engine_requested, codex_scope=None):
        del source, request, limit, codex_scope
        calls.append(engine_requested)
        if engine_requested == "v2":
            raise TimeoutError("provider diagnostics are not exposed")
        return {
            "source": "document",
            "results": [],
            "context": None,
            "analysis": [],
            "comparisons": [],
            "selection_trace": {},
            "trace": {
                "status": "complete",
                "engine_used": "v1",
                "index_generation": [],
                "error_code": None,
            },
        }

    monkeypatch.setattr(runtime.platform, "_retrieve_source_engine", engine)
    result = runtime.platform._retrieve_source(
        source="document",
        request=GlobalSearchRequest(
            project_id="project-rag",
            query="shadow failure",
            sources=["document"],
        ),
        limit=5,
        engine_requested=None,
    )

    assert calls == ["v1", "v2"]
    route = result["trace"]["release_route"]
    assert route["served_engine"] == "v1"
    assert route["fallback_reason"] == "source_timeout"
    assert route["shadow_outcome"] == "v2_failed"
    assert route["lkg_status"] == "UNAVAILABLE"
    assert "provider diagnostics" not in str(route)


def test_default_platform_search_does_not_materialize_full_source_v2(settings) -> None:
    runtime = create_runtime(settings)
    try:
        assert runtime.source_runtime_v2.materialized_sources == ()
        assert runtime.source_runtime_v2.derived_root_created is False

        response = runtime.platform.search(
            GlobalSearchRequest(
                query="current workspace",
                sources=["workspace"],
                limit=5,
            ),
            record_event=False,
        )

        assert response["trace"]["workspace_engine"] == "v1"
        assert runtime.source_runtime_v2.materialized_sources == ()
        assert runtime.source_runtime_v2.derived_root_created is False
    finally:
        runtime.close()


def test_global_probe_cannot_bypass_authority_or_duplicate_legacy(
    settings,
    monkeypatch,
) -> None:
    runtime = create_runtime(settings)
    calls: list[str] = []

    def engine(*, source, request, limit, engine_requested, codex_scope=None):
        del source, request, limit, codex_scope
        calls.append(engine_requested)
        return {
            "source": "document",
            "results": [],
            "context": None,
            "analysis": [],
            "comparisons": [],
            "selection_trace": {},
            "trace": {
                "status": "complete",
                "engine_used": engine_requested,
                "index_generation": [],
                "error_code": None,
            },
        }

    monkeypatch.setattr(runtime.platform, "_retrieve_source_engine", engine)
    request = GlobalSearchRequest(
        project_id="project-rag",
        query="authority absent",
        sources=["document"],
    )
    probe = runtime.platform._retrieve_source(
        source="document",
        request=request,
        limit=5,
        engine_requested="v2",
        governed_global_authority=True,
    )
    assert calls == []
    assert probe["trace"]["status"] == "unavailable"
    assert probe["trace"]["release_route"]["served_engine"] == "none"
    assert probe["trace"]["release_route"]["authority_sha256"] is None

    fallback = runtime.platform._retrieve_source(
        source="document",
        request=request,
        limit=5,
        engine_requested=None,
    )
    assert calls == ["v1"]
    assert fallback["trace"]["release_route"]["served_engine"] == "v1"


def test_missing_registry_fails_closed_to_v1_exactly_once(settings, monkeypatch) -> None:
    runtime = create_runtime(settings)
    runtime.platform.source_runtime_v2 = None
    calls: list[str] = []

    def engine(*, source, request, limit, engine_requested, codex_scope=None):
        del source, request, limit, codex_scope
        calls.append(engine_requested)
        return {
            "source": "workspace",
            "results": [],
            "context": None,
            "analysis": [],
            "comparisons": [],
            "selection_trace": {},
            "trace": {"status": "complete", "engine_used": engine_requested},
        }

    monkeypatch.setattr(runtime.platform, "_retrieve_source_engine", engine)
    result = runtime.platform._retrieve_source(
        source="workspace",
        request=GlobalSearchRequest(
            project_id="project-rag",
            query="registry absent",
            sources=["workspace"],
        ),
        limit=5,
        engine_requested="v2",
    )
    assert calls == ["v1"]
    assert result["trace"]["release_route"]["served_engine"] == "v1"
    assert result["trace"]["release_route"]["fallback_reason"] == "authority_unavailable"


def test_explicit_codex_v2_materializes_owned_isolated_store_and_cleans_it(
    settings,
) -> None:
    runtime = create_runtime(settings)
    derived_database: Path | None = None
    try:
        assert runtime.source_runtime_v2.derived_root_created is False
        facade = runtime.source_runtime_v2.get("codex")
        assert facade.derived_store is not None
        derived_database = facade.derived_store.database_path
        assert runtime.source_runtime_v2.materialized_sources == ("codex",)
        assert runtime.source_runtime_v2.derived_root_created is True
        assert derived_database.exists()
        assert derived_database != settings.database_path
        assert derived_database.name == "codex-v2.sqlite3"
    finally:
        runtime.close()
    assert derived_database is not None
    assert derived_database.exists() is False


def test_runtime_close_defers_cleanup_without_waiting_for_inflight_operation(settings) -> None:
    runtime = create_runtime(settings)
    operation_started = threading.Event()
    release_operation = threading.Event()
    close_started = threading.Event()
    close_finished = threading.Event()

    def hold_operation() -> None:
        with runtime.source_runtime_v2.operation("codex"):
            operation_started.set()
            assert release_operation.wait(timeout=2)

    def close_runtime() -> None:
        close_started.set()
        runtime.close()
        close_finished.set()

    worker = threading.Thread(target=hold_operation)
    closer = threading.Thread(target=close_runtime)
    worker.start()
    assert operation_started.wait(timeout=2)
    assert runtime.source_runtime_v2.derived_root_created is True
    closer.start()
    assert close_started.wait(timeout=2)
    assert close_finished.wait(timeout=2)
    assert runtime.source_runtime_v2.materialized_sources == ("codex",)

    release_operation.set()
    worker.join(timeout=2)
    closer.join(timeout=2)
    assert worker.is_alive() is False
    assert closer.is_alive() is False
    assert close_finished.is_set()
    assert runtime.source_runtime_v2.materialized_sources == ()


def test_unverified_experiment_override_falls_back_to_v1_without_materializing_v2(
    settings,
) -> None:
    runtime = create_runtime(settings)
    try:
        project = runtime.workspace.store.get_project("project-rag")
        assert project is not None
        project_acl = str(project["acl_ref"])
        experiment = runtime.experiments.create_experiment(
            ExperimentCreate(
                project_id="project-rag",
                title="Governed production facade",
                objective="Exercise the complete Experiment V2 runtime.",
                status="completed",
            )
        )
        run = runtime.experiments.create_run(
            RunCreate(
                experiment_id=experiment["id"],
                external_id="governed-run-1",
                name="governed candidate",
                status="completed",
                dataset_id="dataset-runtime",
                dataset_version="v1",
                config={"seed": 7},
                environment={"python": "3.13"},
                metrics=[
                    MetricInput(
                        name="accuracy",
                        value=0.91,
                        unit="ratio",
                        split="validation",
                        step=1,
                        higher_is_better=True,
                    )
                ],
            )
        )
        source_id = "experiment-source://mlflow/governed"
        tracking_uri = "memory://governed-mlflow"
        runtime.experiments.store.upsert_source(
            {
                "id": source_id,
                "project_id": "project-rag",
                "adapter_type": "mlflow",
                "tracking_uri": tracking_uri,
                "status": "ready",
                "stats": {"experiments": 1, "runs": 1},
            }
        )
        runtime.experiments.store.map_experiment(
            source_id,
            "external-governed-experiment",
            experiment["id"],
            {"name": experiment["title"]},
        )
        observation = runtime.experiments.store.list_run_observations(run["id"])[-1]
        runtime.sources.accept(
            SourceEventInput(
                source_type="mlflow",
                source_instance=tracking_uri,
                event_type="run.snapshot.observed",
                source_object_id="governed-run-1",
                source_version=observation["source_version"],
                project_id="project-rag",
                acl_ref=project_acl,
                source_uri=f"{tracking_uri}#/runs/governed-run-1",
                payload={"run_id": "governed-run-1", "status": "completed"},
                adapter_version="production-facade-test-v1",
                schema_version="mlflow-run-v2",
                metadata={"experiment_id": "external-governed-experiment"},
            )
        )

        response = runtime.platform.search(
            GlobalSearchRequest(
                query="accuracy completed",
                sources=["experiment"],
                experiment_ids=[experiment["id"]],
                limit=8,
            ),
            experiment_engine_override="v2",
            record_event=False,
        )

        source_trace = response["trace"]["sources"]["experiment"]
        assert source_trace["engine_used"] == "v1"
        assert source_trace["release_route"]["stage"] == "off"
        assert source_trace["release_route"]["served_engine"] == "v1"
        assert source_trace["release_route"]["authority_sha256"] is None
        assert response["results"]
        assert {item["source"] for item in response["results"]} == {"experiment"}
        assert runtime.source_runtime_v2.materialized_sources == ()
        assert runtime.source_runtime_v2.derived_root_created is False

    finally:
        runtime.close()
