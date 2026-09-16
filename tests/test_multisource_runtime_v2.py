from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from release_authority_helpers import (
    install_document_v2_authority,
    install_experiment_v2_authority,
)

from evidence_rag.api import create_app
from evidence_rag.config import Settings
from evidence_rag.documents.models import DocumentIngestRequest
from evidence_rag.experiments.models import ExperimentCreate, MetricInput, RunCreate
from evidence_rag.platform.models import ExperimentAnalysisRequestV2, GlobalSearchRequest
from evidence_rag.rag.multisource_foundation_v2 import (
    CalibrationObservationV2,
    SourceExecutionStatusV2,
    fit_source_calibration_v2,
    plan_multisource_query_v2,
)
from evidence_rag.rag.multisource_runtime_v2 import (
    GlobalEngineOverrideError,
    MultiSourceRuntimeRequestV2,
    MultiSourceRuntimeV2,
    build_reviewed_calibration_artifact_v2,
    build_reviewed_calibration_bundle_v2,
    load_reviewed_calibration_bundle_v2,
    validate_global_engine_override,
)
from evidence_rag.runtime import create_runtime
from evidence_rag.workspace.models import RelationCreate, RelationReview


def _document(runtime, *, title: str = "Orion report"):
    return runtime.documents.ingest(
        DocumentIngestRequest(
            title=title,
            content="# Result\n\nClaim: Orion evidence requires reviewed support.",
        )
    )


def _request(
    *sources: str,
    acl_ref: str | None = None,
    experiment_ids: tuple[str, ...] = (),
    experiment_analysis: ExperimentAnalysisRequestV2 | None = None,
) -> MultiSourceRuntimeRequestV2:
    return MultiSourceRuntimeRequestV2(
        project_id="project-rag",
        request_id="runtime-test",
        question="Verify the Orion claim and metric",
        intent="claim_verification",
        acl_refs=(acl_ref,) if acl_ref else (),
        requested_sources=tuple(sources),
        experiment_ids=experiment_ids,
        experiment_analysis=experiment_analysis,
    )


def _reviewed_document_calibration_bundle():
    profile = fit_source_calibration_v2(
        "document",
        (
            CalibrationObservationV2(source="document", raw_score=0.05, relevant=False),
            CalibrationObservationV2(source="document", raw_score=0.2, relevant=False),
            CalibrationObservationV2(source="document", raw_score=0.7, relevant=True),
            CalibrationObservationV2(source="document", raw_score=0.95, relevant=True),
        ),
        bins=2,
    )
    artifact = build_reviewed_calibration_artifact_v2(
        source="document",
        artifact_id="reviewed-calibration://document/test-v1",
        dataset_sha256=profile.provenance_sha256,
        reviewer="test-review-authority",
        reviewed_at="2026-07-30T00:00:00Z",
        profile=profile,
    )
    return build_reviewed_calibration_bundle_v2({"document": artifact})


def _reviewed_calibration_bundle(*sources: str):
    artifacts = {}
    for source in sources:
        profile = fit_source_calibration_v2(
            source,
            (
                CalibrationObservationV2(source=source, raw_score=0.05, relevant=False),
                CalibrationObservationV2(source=source, raw_score=0.2, relevant=False),
                CalibrationObservationV2(source=source, raw_score=0.7, relevant=True),
                CalibrationObservationV2(source=source, raw_score=0.95, relevant=True),
            ),
            bins=2,
        )
        artifacts[source] = build_reviewed_calibration_artifact_v2(
            source=source,
            artifact_id=f"reviewed-calibration://{source}/projection-test-v1",
            dataset_sha256=profile.provenance_sha256,
            reviewer="projection-test-review-authority",
            reviewed_at="2026-07-30T00:00:00Z",
            profile=profile,
        )
    return build_reviewed_calibration_bundle_v2(artifacts)


def _write_bundle(path, bundle) -> None:
    path.write_text(
        json.dumps(
            bundle.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def test_reviewed_calibration_bundle_is_pinned_portable_and_fail_closed(tmp_path) -> None:
    bundle = _reviewed_document_calibration_bundle()
    path = (tmp_path / "reviewed-calibration.json").resolve()
    _write_bundle(path, bundle)

    loaded = load_reviewed_calibration_bundle_v2(
        path,
        expected_sha256=bundle.content_sha256,
    )
    assert tuple(loaded) == ("document",)
    assert loaded["document"].content_sha256 == bundle.artifacts[0].content_sha256

    with pytest.raises(ValueError, match="trust digest"):
        load_reviewed_calibration_bundle_v2(
            path,
            expected_sha256="sha256:" + "0" * 64,
        )

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["artifacts"][0]["reviewer"] = "untrusted-copy"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_reviewed_calibration_bundle_v2(
            path,
            expected_sha256=bundle.content_sha256,
        )

    _write_bundle(path, bundle)
    alias = tmp_path / "calibration-alias.json"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        load_reviewed_calibration_bundle_v2(
            alias.absolute(),
            expected_sha256=bundle.content_sha256,
        )


def test_runtime_loads_only_explicitly_pinned_reviewed_calibration(settings, tmp_path) -> None:
    bundle = _reviewed_document_calibration_bundle()
    path = (tmp_path / "reviewed-calibration.json").resolve()
    _write_bundle(path, bundle)
    configured = replace(
        settings,
        rag_multisource_calibration_bundle=path,
        rag_multisource_calibration_sha256=bundle.content_sha256,
    )

    runtime = create_runtime(configured)

    assert tuple(runtime.global_v2.calibration_artifacts) == ("document",)
    assert (
        runtime.global_v2.calibration_artifacts["document"].content_sha256
        == bundle.artifacts[0].content_sha256
    )


def test_calibration_deployment_configuration_is_explicit_and_paired(
    monkeypatch,
    tmp_path,
) -> None:
    bundle = _reviewed_document_calibration_bundle()
    path = (tmp_path / "reviewed-calibration.json").resolve()
    _write_bundle(path, bundle)
    monkeypatch.setenv("RAG_MULTISOURCE_CALIBRATION_BUNDLE", str(path))
    monkeypatch.setenv("RAG_MULTISOURCE_CALIBRATION_SHA256", bundle.content_sha256)

    configured = Settings.from_env(base_dir=tmp_path)

    assert configured.rag_multisource_calibration_bundle == path
    assert configured.rag_multisource_calibration_sha256 == bundle.content_sha256

    monkeypatch.delenv("RAG_MULTISOURCE_CALIBRATION_SHA256")
    with pytest.raises(ValueError, match="configured together"):
        Settings.from_env(base_dir=tmp_path)


def test_explicit_global_v2_executes_with_pinned_reviewed_calibration(
    settings,
    tmp_path,
) -> None:
    bundle = _reviewed_document_calibration_bundle()
    path = (tmp_path / "reviewed-calibration.json").resolve()
    _write_bundle(path, bundle)
    runtime = create_runtime(
        replace(
            settings,
            rag_multisource_calibration_bundle=path,
            rag_multisource_calibration_sha256=bundle.content_sha256,
        )
    )
    install_document_v2_authority(runtime)
    _document(runtime)
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]

    def unexpected_legacy() -> dict[str, object]:
        raise AssertionError("a governed, calibrated V2 execution must not invoke V1")

    response = runtime.global_v2.search(
        GlobalSearchRequest(
            query="Verify the Orion claim",
            project_id="project-rag",
            sources=["document"],
            allowed_acl_refs=[acl_ref],
            enforce_acl=True,
        ),
        engine_override="v2",
        fallback=unexpected_legacy,
        intent="claim_verification",
        request_id="calibrated-global-v2",
    )

    assert response["trace"]["global_engine"]["selected"] == "v2"
    assert response["trace"]["global_engine"]["served"] == "v2"
    assert response["trace"]["global_engine"]["fallback"] is None
    assert response["trace"]["global_engine"]["quality_qualified"] is False
    assert response["results"]
    assert {item["source"] for item in response["results"]} == {"document"}


def test_reviewed_calibration_without_source_authority_falls_back_before_v2_materialization(
    settings,
    tmp_path,
    monkeypatch,
) -> None:
    bundle = _reviewed_document_calibration_bundle()
    path = (tmp_path / "reviewed-calibration-without-authority.json").resolve()
    _write_bundle(path, bundle)
    runtime = create_runtime(
        replace(
            settings,
            rag_multisource_calibration_bundle=path,
            rag_multisource_calibration_sha256=bundle.content_sha256,
        )
    )
    _document(runtime)
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]
    calls = 0

    def legacy() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"results": [], "trace": {"engine": "v1"}}

    def unexpected_source_execution(**_: object) -> dict[str, object]:
        raise AssertionError("source V2 must not execute without release authority")

    monkeypatch.setattr(runtime.platform, "_retrieve_source", unexpected_source_execution)

    assert runtime.source_runtime_v2.materialized_sources == ()
    assert runtime.source_runtime_v2.derived_root_created is False
    response = runtime.global_v2.search(
        GlobalSearchRequest(
            query="Verify the Orion claim",
            project_id="project-rag",
            sources=["document"],
            allowed_acl_refs=[acl_ref],
            enforce_acl=True,
        ),
        engine_override="v2",
        fallback=legacy,
        intent="claim_verification",
        request_id="calibrated-without-source-authority",
    )

    assert calls == 1
    assert response["trace"]["global_engine"] == {
        "requested": "v2",
        "selected": "v1",
        "served": "v1",
        "status": "partial",
        "fallback": "legacy_v1",
        "blockers": ["source_release_authority_unavailable:document"],
        "runtime_version": "production-store-multisource-runtime-v2",
        "quality_qualified": False,
    }
    assert runtime.source_runtime_v2.materialized_sources == ()
    assert runtime.source_runtime_v2.derived_root_created is False


def test_verified_authority_with_all_sources_unavailable_falls_back_once(
    settings,
    tmp_path,
    monkeypatch,
) -> None:
    bundle = _reviewed_document_calibration_bundle()
    path = (tmp_path / "reviewed-calibration-source-unavailable.json").resolve()
    _write_bundle(path, bundle)
    runtime = create_runtime(
        replace(
            settings,
            rag_multisource_calibration_bundle=path,
            rag_multisource_calibration_sha256=bundle.content_sha256,
        )
    )
    install_document_v2_authority(runtime)
    _document(runtime)
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]
    source_calls = 0
    legacy_calls = 0

    def unavailable_source(**_: object) -> dict[str, object]:
        nonlocal source_calls
        source_calls += 1
        return {
            "source": "document",
            "results": [],
            "context": None,
            "trace": {
                "status": "unavailable",
                "engine_used": None,
                "duration_ms": 0,
                "error_code": "source_unavailable",
            },
        }

    def legacy() -> dict[str, object]:
        nonlocal legacy_calls
        legacy_calls += 1
        return {"results": [], "trace": {"engine": "v1"}}

    monkeypatch.setattr(runtime.platform, "_retrieve_source", unavailable_source)
    response = runtime.global_v2.search(
        GlobalSearchRequest(
            query="Verify the Orion claim",
            project_id="project-rag",
            sources=["document"],
            allowed_acl_refs=[acl_ref],
            enforce_acl=True,
        ),
        engine_override="v2",
        fallback=legacy,
        intent="claim_verification",
        request_id="verified-authority-source-unavailable",
    )

    assert source_calls > 0
    assert legacy_calls == 1
    assert response["trace"]["global_engine"] == {
        "requested": "v2",
        "selected": "v1",
        "served": "v1",
        "status": "partial",
        "fallback": "legacy_v1",
        "blockers": ["all_sources_unavailable"],
        "runtime_version": "production-store-multisource-runtime-v2",
        "quality_qualified": False,
    }


def test_query_path_reads_and_writes_exact_index_embedding_and_scoped_cache(
    settings,
    tmp_path,
) -> None:
    bundle = _reviewed_document_calibration_bundle()
    path = (tmp_path / "reviewed-performance-calibration.json").resolve()
    _write_bundle(path, bundle)
    runtime = create_runtime(
        replace(
            settings,
            rag_multisource_calibration_bundle=path,
            rag_multisource_calibration_sha256=bundle.content_sha256,
        )
    )
    install_document_v2_authority(runtime)
    _document(runtime)
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]
    request = GlobalSearchRequest(
        query="Verify the Orion claim",
        project_id="project-rag",
        sources=["document"],
        allowed_acl_refs=[acl_ref],
        enforce_acl=True,
    )

    for index in range(2):
        response = runtime.global_v2.search(
            request,
            engine_override="v2",
            fallback=lambda: (_ for _ in ()).throw(
                AssertionError("governed V2 unexpectedly fell back")
            ),
            intent="claim_verification",
            request_id=f"performance-path-{index}",
        )
        assert response["results"]

    snapshot = runtime.performance_v2.operational_snapshot(
        reviewed_calibration_slices=("document:unspecified:unspecified",),
        required_calibration_slices=("document:unspecified:unspecified",),
    )
    assert snapshot["vector_index"]["vector_count"] > 0
    assert snapshot["embedding_cache"]["entry_count"] > 0
    assert snapshot["scoped_cache"]["entry_count"] > 0
    assert any(
        span.span == "source_retrieval" and span.cache_hit
        for span in runtime.performance_v2.spans()
    )


def test_query_asgi_preserves_reviewed_fact_and_rejects_untyped_experiment_path(
    settings,
    tmp_path,
    monkeypatch,
) -> None:
    bundle = _reviewed_calibration_bundle("document", "experiment")
    path = (tmp_path / "reviewed-projection-calibration.json").resolve()
    _write_bundle(path, bundle)
    app = create_app(
        replace(
            settings,
            rag_multisource_calibration_bundle=path,
            rag_multisource_calibration_sha256=bundle.content_sha256,
        )
    )
    runtime = app.state.runtime
    install_document_v2_authority(runtime)
    install_experiment_v2_authority(runtime)
    document = _document(runtime, title="Reviewed Orion claim")
    experiment = runtime.experiments.create_experiment(
        ExperimentCreate(title="Reviewed Orion validation")
    )
    run = runtime.experiments.create_run(
        RunCreate(
            experiment_id=experiment["id"],
            name="Orion supporting run",
            metrics=[MetricInput(name="accuracy", value=0.91)],
        )
    )
    relation = runtime.workspace.create_relation(
        RelationCreate(
            source_entity_id=document["claims"][0]["id"],
            predicate="SUPPORTED_BY",
            target_entity_id=run["id"],
            derivation="human_confirmed",
            confidence=0.95,
            review_status="unreviewed",
        )
    )
    runtime.workspace.review_relation(
        relation["id"],
        RelationReview(review_status="confirmed", reviewer="projection-test"),
    )
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]

    rows = {
        "document": [
            {
                "entity_id": document["claims"][0]["id"],
                "source": "document",
                "entity_type": "Claim",
                "title": "Reviewed Orion claim",
                "snippet": "Orion evidence requires reviewed support.",
                "locator": "document://reviewed-orion/claim/1",
                "version": "document-reviewed-v1",
                "status": "accepted",
                "fact_status": "verified",
                "review_status": "reviewed",
                "derivation": "human_confirmed",
                "raw_or_derived": "derived_fact",
                "score": 0.95,
                "channels": ["reviewed_document"],
                "roles": ["claim", "source_location"],
                "acl_ref": acl_ref,
            }
        ],
        "experiment": [
            {
                "entity_id": run["id"],
                "source": "experiment",
                "entity_type": "ExperimentRun",
                "title": "Orion supporting run",
                "snippet": "The governed run contains the supporting observation.",
                "locator": "experiment://reviewed-orion/run/1",
                "version": "experiment-observed-v1",
                "status": "completed",
                "fact_status": "reported",
                "review_status": "unreviewed",
                "derivation": "deterministic",
                "raw_or_derived": "raw_fact",
                "score": 0.9,
                "channels": ["experiment_run"],
                "roles": ["run", "run_or_metric"],
                "acl_ref": acl_ref,
            }
        ],
    }

    def governed_source_projection(
        *,
        source,
        request,
        limit,
        engine_requested,
        **_kwargs,
    ):
        assert request.project_id == "project-rag"
        assert engine_requested == "v2"
        return {
            "source": source,
            "results": rows[source][:limit],
            "context": {"reasoning_included": False},
            "trace": {
                "status": "complete",
                "engine_used": "v2",
                "duration_ms": 0.1,
                "error_code": None,
            },
        }

    monkeypatch.setattr(runtime.platform, "_retrieve_source", governed_source_projection)
    with TestClient(app) as client:
        response = client.post(
            "/v1/query",
            headers={"X-RAG-Engine": "v2"},
            json={
                "question": "Verify the reviewed Orion claim with its supporting run.",
                "mode": "evidence",
                "intent": "claim_verification",
                "include": ["document", "experiment"],
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    verified = {item["entity_id"]: item for item in payload["evidence_pack"]["verified_facts"]}
    assert document["claims"][0]["id"] in verified
    assert verified[document["claims"][0]["id"]]["fact_status"] == "verified"
    assert verified[document["claims"][0]["id"]]["review_status"] == "reviewed"
    assert verified[document["claims"][0]["id"]]["derivation"] == "human_confirmed"
    assert run["id"] not in verified
    supporting = {
        item["entity_id"]: item for item in payload["evidence_pack"]["supporting_evidence"]
    }
    assert run["id"] not in supporting
    assert payload["evidence_pack"]["relations"] == []


def test_prepare_fails_closed_without_reviewed_calibration_artifact(
    settings,
    monkeypatch,
) -> None:
    runtime = create_runtime(settings)
    install_document_v2_authority(runtime)
    _document(runtime)
    facade = MultiSourceRuntimeV2(platform=runtime.platform)
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]

    def unexpected_retrieval(**_: object) -> dict[str, object]:
        raise AssertionError("prepare must not execute source retrieval")

    monkeypatch.setattr(runtime.platform, "_retrieve_source", unexpected_retrieval)
    preparation = facade.prepare(_request("document", acl_ref=acl_ref))

    assert preparation.status == "partial"
    assert preparation.blockers == ("calibration_unavailable:document",)
    assert preparation.routed_sources == ("document",)
    document_capability = next(
        item for item in preparation.registry.capabilities if item.domain == "document"
    )
    assert document_capability.health == "partial"
    assert document_capability.calibration_version == "calibration_unavailable"

    execution = facade.run_v2(_request("document", acl_ref=acl_ref))
    assert execution.status == "partial"
    assert execution.pipeline_result is None
    assert execution.blockers == ("calibration_unavailable:document",)


def test_real_document_adapter_preserves_store_identity_acl_locator_and_uncalibrated_state(
    settings,
) -> None:
    runtime = create_runtime(settings)
    install_document_v2_authority(runtime)
    document = _document(runtime)
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]
    preparation = MultiSourceRuntimeV2(platform=runtime.platform).prepare(
        _request("document", acl_ref=acl_ref)
    )
    assert preparation.scope is not None
    plan = plan_multisource_query_v2(
        question="Verify the Orion claim and metric",
        intent="claim_verification",
        scope=preparation.scope,
        registry=preparation.registry,
        requested_sources=("document",),
    )

    batch = preparation.retrievers["document"].retrieve(
        plan=plan,
        scope=preparation.scope,
        budget=20,
        required_roles=plan.required_roles,
        corrective_round=0,
    )

    assert batch.execution.status is SourceExecutionStatusV2.COMPLETE
    assert (
        batch.execution.index_generation
        == preparation.source_snapshots["document"].index_generation
    )
    assert batch.candidates
    claim = next(item for item in batch.candidates if item.entity_id == document["claims"][0]["id"])
    assert claim.retrieval_domain == "document"
    assert claim.acl_ref == acl_ref
    assert claim.locator
    assert claim.source_generation == batch.execution.index_generation
    assert claim.calibrated_relevance == 0
    assert claim.calibration_version == "calibration_unavailable"
    assert {"claim", "source_location"} <= set(claim.matched_roles)


def test_prepared_adapter_rejects_store_generation_change(settings) -> None:
    runtime = create_runtime(settings)
    _document(runtime)
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]
    preparation = MultiSourceRuntimeV2(platform=runtime.platform).prepare(
        _request("document", acl_ref=acl_ref)
    )
    assert preparation.scope is not None
    plan = plan_multisource_query_v2(
        question="Verify the Orion claim and metric",
        intent="claim_verification",
        scope=preparation.scope,
        registry=preparation.registry,
        requested_sources=("document",),
    )
    runtime.documents.ingest(
        DocumentIngestRequest(
            title="Changed store snapshot",
            content="# Result\n\nClaim: A later document changes the source generation.",
        )
    )

    batch = preparation.retrievers["document"].retrieve(
        plan=plan,
        scope=preparation.scope,
        budget=20,
        required_roles=plan.required_roles,
        corrective_round=0,
    )

    assert batch.candidates == ()
    assert batch.execution.status is SourceExecutionStatusV2.PARTIAL
    assert batch.execution.error_code == "source_generation_changed"


def test_real_adapter_isolates_source_exception_as_partial(settings, monkeypatch) -> None:
    runtime = create_runtime(settings)
    _document(runtime)
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]
    preparation = MultiSourceRuntimeV2(platform=runtime.platform).prepare(
        _request("document", acl_ref=acl_ref)
    )
    assert preparation.scope is not None
    plan = plan_multisource_query_v2(
        question="Verify the Orion claim and metric",
        intent="claim_verification",
        scope=preparation.scope,
        registry=preparation.registry,
        requested_sources=("document",),
    )

    def failed_source(**_: object) -> None:
        raise RuntimeError("isolated source failure")

    monkeypatch.setattr(runtime.platform, "_retrieve_source", failed_source)
    batch = preparation.retrievers["document"].retrieve(
        plan=plan,
        scope=preparation.scope,
        budget=20,
        required_roles=plan.required_roles,
        corrective_round=0,
    )

    assert batch.candidates == ()
    assert batch.execution.status is SourceExecutionStatusV2.PARTIAL
    assert batch.execution.error_code == "source_error:RuntimeError"


def test_scope_and_project_acl_are_fail_closed(settings) -> None:
    runtime = create_runtime(settings)
    _document(runtime)
    with runtime.platform.store.database.transaction() as db:
        db.execute("UPDATE projects SET acl_ref='team-secret' WHERE id='project-rag'")

    preparation = MultiSourceRuntimeV2(platform=runtime.platform).prepare(_request("document"))
    assert preparation.status == "unavailable"
    assert preparation.scope is None
    assert preparation.blockers == ("unauthorized",)


def test_global_engine_override_defaults_to_v1_and_rejects_unknown_values() -> None:
    assert validate_global_engine_override(None) == "v1"
    assert validate_global_engine_override(" V2 ") == "v2"

    try:
        validate_global_engine_override("latest")
    except GlobalEngineOverrideError as error:
        assert "X-RAG-Engine" in str(error)
    else:
        raise AssertionError("unknown global engine must fail closed")


def test_explicit_global_v2_falls_back_to_legacy_once_without_calibration(settings) -> None:
    runtime = create_runtime(settings)
    _document(runtime)
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]
    calls = 0

    def legacy() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "query_id": "global-query://legacy",
            "query": "Orion",
            "project_id": "project-rag",
            "total": 0,
            "results": [],
            "evidence_pack": {
                "citation_map": {},
                "relations": [],
                "related_entities": [],
                "context": None,
                "source_contexts": {},
                "query_plan": None,
                "conflicts_and_staleness": [],
                "global_context": None,
            },
            "trace": {"sources": {}, "status": "COMPLETE"},
            "index_generations": [],
        }

    response = runtime.global_v2.search(
        GlobalSearchRequest(
            query="Orion",
            project_id="project-rag",
            sources=["document"],
            allowed_acl_refs=[acl_ref],
            enforce_acl=True,
        ),
        engine_override="v2",
        fallback=legacy,
    )

    assert calls == 1
    assert response["query_id"] == "global-query://legacy"
    assert response["trace"]["global_engine"] == {
        "requested": "v2",
        "selected": "v1",
        "served": "v1",
        "status": "partial",
        "fallback": "legacy_v1",
        "blockers": [
            "source_release_authority_unavailable:document",
            "calibration_unavailable:document",
        ],
        "runtime_version": "production-store-multisource-runtime-v2",
        "quality_qualified": False,
    }


def test_default_global_engine_uses_only_legacy(settings, monkeypatch) -> None:
    runtime = create_runtime(settings)
    calls = 0

    def unexpected_v2(_: object) -> None:
        raise AssertionError("default V1 must not prepare or execute V2")

    monkeypatch.setattr(runtime.global_v2, "run_v2", unexpected_v2)

    def legacy() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"trace": {"engine": "v1"}}

    response = runtime.global_v2.search(
        GlobalSearchRequest(query="default path"),
        engine_override=None,
        fallback=legacy,
    )

    assert calls == 1
    assert response == {"trace": {"engine": "v1"}}


def test_edge_provider_uses_only_real_reviewed_acl_and_version_valid_edges(
    settings,
    monkeypatch,
) -> None:
    runtime = create_runtime(settings)
    install_document_v2_authority(runtime)
    install_experiment_v2_authority(runtime)
    document = _document(runtime)
    experiment = runtime.experiments.create_experiment(
        ExperimentCreate(title="Orion metric validation")
    )
    run = runtime.experiments.create_run(
        RunCreate(
            experiment_id=experiment["id"],
            name="Orion candidate",
            metrics=[MetricInput(name="accuracy", value=0.91)],
        )
    )
    relation = runtime.workspace.create_relation(
        RelationCreate(
            source_entity_id=document["claims"][0]["id"],
            predicate="SUPPORTED_BY",
            target_entity_id=run["id"],
            derivation="human_confirmed",
            confidence=0.9,
            review_status="unreviewed",
        )
    )
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]
    analysis = ExperimentAnalysisRequestV2(
        task="aggregate",
        metric="accuracy",
        run_ids=[run["id"]],
        split="all",
        aggregation="mean",
    )
    preparation = MultiSourceRuntimeV2(platform=runtime.platform).prepare(
        _request(
            "document",
            "experiment",
            acl_ref=acl_ref,
            experiment_ids=(experiment["id"],),
            experiment_analysis=analysis,
        )
    )
    assert preparation.scope is not None
    plan = plan_multisource_query_v2(
        question="Verify the Orion claim and metric",
        intent="claim_verification",
        scope=preparation.scope,
        registry=preparation.registry,
        requested_sources=("document", "experiment"),
    )
    document_batch = preparation.retrievers["document"].retrieve(
        plan=plan,
        scope=preparation.scope,
        budget=20,
        required_roles=plan.required_roles,
        corrective_round=0,
    )

    def typed_experiment_projection(**_kwargs):
        return {
            "source": "experiment",
            "results": [
                {
                    "entity_id": run["id"],
                    "source": "experiment",
                    "entity_type": "ExperimentRun",
                    "title": "Orion candidate",
                    "snippet": "The explicit aggregate selected this governed run.",
                    "locator": f"experiment://{experiment['id']}/{run['id']}",
                    "version": "experiment-observed-v1",
                    "status": "observed",
                    "fact_status": "observed",
                    "score": 0.9,
                    "roles": ["run", "run_or_metric"],
                    "acl_ref": acl_ref,
                }
            ],
            "analysis": [{"aggregations": [{"metric": "accuracy", "value": 0.91}]}],
            "selection_trace": {
                "explicit": True,
                "task": analysis.task,
                "metric": analysis.metric,
                "baseline_run_id": analysis.baseline_run_id,
                "candidate_run_ids": analysis.candidate_run_ids,
                "run_ids": analysis.run_ids,
                "split": analysis.split,
                "aggregation": analysis.aggregation,
                "roles": analysis.roles,
            },
            "trace": {
                "status": "complete",
                "engine_used": "v2",
                "duration_ms": 0.1,
                "error_code": None,
            },
        }

    monkeypatch.setattr(runtime.platform, "_retrieve_source", typed_experiment_projection)
    experiment_batch = preparation.retrievers["experiment"].retrieve(
        plan=plan,
        scope=preparation.scope,
        budget=20,
        required_roles=plan.required_roles,
        corrective_round=0,
    )
    batches = (document_batch, experiment_batch)
    candidates = tuple(candidate for batch in batches for candidate in batch.candidates)
    assert document["claims"][0]["id"] in {item.entity_id for item in candidates}
    assert run["id"] in {item.entity_id for item in candidates}

    assert preparation.edge_provider.resolve(plan=plan, candidates=candidates) == ()
    assert preparation.edge_provider.last_blockers == ("reviewed_edge_evidence_unavailable",)

    runtime.workspace.review_relation(
        relation["id"],
        RelationReview(review_status="confirmed", reviewer="runtime-test"),
    )
    reviewed = preparation.edge_provider.resolve(plan=plan, candidates=candidates)
    assert len(reviewed) == 1
    assert reviewed[0].review_status.value == "reviewed"
    assert reviewed[0].fact_status.value == "verified"
