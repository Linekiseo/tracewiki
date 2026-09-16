from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError
from release_authority_helpers import install_document_v2_authority

from evidence_rag.config import Settings
from evidence_rag.documents.models import DocumentIngestRequest
from evidence_rag.platform.models import GlobalSearchRequest
from evidence_rag.rag.global_switches_v2 import GlobalComponentSwitchesV2
from evidence_rag.rag.multisource_foundation_v2 import (
    CalibrationObservationV2,
    fit_source_calibration_v2,
)
from evidence_rag.rag.multisource_runtime_v2 import (
    MultiSourceRuntimeRequestV2,
    MultiSourceRuntimeV2,
    build_reviewed_calibration_artifact_v2,
)
from evidence_rag.runtime import create_runtime


def test_global_component_switches_are_strict_and_canonical() -> None:
    switches = GlobalComponentSwitchesV2(planner=False, reranker=False)
    assert switches.disabled_components == ("reranker", "planner")
    assert switches.rollback_blocker == "component_disabled:reranker"
    with pytest.raises(ValidationError):
        GlobalComponentSwitchesV2(planner="false")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "settings_field",
    [
        "rag_global_generator_prompt_v2",
        "rag_global_context_packer_v2",
        "rag_global_fusion_v2",
        "rag_global_reranker_v2",
        "rag_global_embedding_generation_v2",
        "rag_global_source_retrievers_v2",
        "rag_global_planner_v2",
    ],
)
def test_disabled_component_has_independent_v1_fallback_mode(
    settings,
    settings_field: str,
) -> None:
    runtime = create_runtime(replace(settings, **{settings_field: False}))
    component = settings_field.removeprefix("rag_global_").removesuffix("_v2")
    snapshot = runtime.global_v2.operational_snapshot()
    modes = dict(snapshot["component_switches"]["components"])  # type: ignore[index]
    assert modes[component] == "v1_fallback"
    assert all(mode == "enabled" for name, mode in modes.items() if name != component)
    assert runtime.performance_v2.spans() == ()


@pytest.mark.parametrize(
    ("settings_field", "component"),
    [
        ("rag_global_generator_prompt_v2", "generator_prompt"),
        ("rag_global_context_packer_v2", "context_packer"),
        ("rag_global_fusion_v2", "fusion"),
        ("rag_global_reranker_v2", "reranker"),
        ("rag_global_embedding_generation_v2", "embedding_generation"),
        ("rag_global_source_retrievers_v2", "source_retrievers"),
        ("rag_global_planner_v2", "planner"),
    ],
)
def test_explicit_v2_component_rollback_preflights_before_all_v2_work(
    settings,
    monkeypatch,
    settings_field: str,
    component: str,
) -> None:
    runtime = create_runtime(replace(settings, **{settings_field: False}))
    install_document_v2_authority(runtime)
    runtime.documents.ingest(
        DocumentIngestRequest(
            title="Isolated rollback authority",
            content="# Result\n\nClaim: rollback must happen before V2 work.",
        )
    )
    profile = fit_source_calibration_v2(
        "document",
        (
            CalibrationObservationV2(source="document", raw_score=0.1, relevant=False),
            CalibrationObservationV2(source="document", raw_score=0.9, relevant=True),
        ),
        bins=2,
    )
    artifact = build_reviewed_calibration_artifact_v2(
        source="document",
        artifact_id="reviewed-calibration://document/rollback-preflight-v1",
        dataset_sha256=profile.provenance_sha256,
        reviewer="isolated-test-review-authority",
        reviewed_at="2026-07-31T00:00:00Z",
        profile=profile,
    )
    facade = MultiSourceRuntimeV2(
        platform=runtime.platform,
        calibration_artifacts={"document": artifact},
        performance_runtime=runtime.performance_v2,
        component_switches=runtime.global_v2.component_switches,
    )
    acl_ref = runtime.workspace.store.get_project("project-rag")["acl_ref"]
    runtime_request = MultiSourceRuntimeRequestV2(
        project_id="project-rag",
        request_id=f"direct-{component}-rollback",
        question="rollback preflight",
        intent="claim_verification",
        acl_refs=(acl_ref,),
        requested_sources=("document",),
    )
    preparation = facade.prepare(runtime_request)
    assert preparation.status == "unavailable"
    assert preparation.blockers == (f"component_disabled:{component}",)
    assert preparation.scope is None
    assert preparation.registry is None
    assert preparation.source_snapshots == {}
    assert preparation.pipeline_request is None
    assert preparation.edge_provider is None
    execution = facade.run_v2(runtime_request)
    assert execution.status == "unavailable"
    assert execution.blockers == (f"component_disabled:{component}",)
    assert execution.pipeline_result is None
    assert runtime.performance_v2.spans() == ()
    counters = {
        name: 0
        for name in (
            "prepare",
            "run_v2",
            "plan",
            "pipeline",
            "source",
            "performance",
            "materialization",
        )
    }

    def forbidden(label: str):
        def fail(*_args, **_kwargs):
            counters[label] += 1
            raise AssertionError(f"{label} must not run after component rollback preflight")

        return fail

    monkeypatch.setattr(facade, "prepare", forbidden("prepare"))
    monkeypatch.setattr(facade, "run_v2", forbidden("run_v2"))
    monkeypatch.setattr(
        "evidence_rag.rag.multisource_runtime_v2.plan_multisource_query_v2",
        forbidden("plan"),
    )
    monkeypatch.setattr(
        "evidence_rag.rag.multisource_runtime_v2.GovernedMultiSourcePipelineV2",
        forbidden("pipeline"),
    )
    monkeypatch.setattr(runtime.platform, "_retrieve_source", forbidden("source"))
    monkeypatch.setattr(runtime.performance_v2, "record", forbidden("performance"))
    monkeypatch.setattr(
        runtime.source_runtime_v2,
        "_build",
        forbidden("materialization"),
    )
    legacy_calls = 0

    def legacy() -> dict[str, object]:
        nonlocal legacy_calls
        legacy_calls += 1
        return {"results": [], "trace": {"engine": "v1"}}

    assert runtime.source_runtime_v2.materialized_sources == ()
    assert runtime.source_runtime_v2.derived_root_created is False
    response = facade.search(
        GlobalSearchRequest(
            project_id="project-rag",
            query="rollback preflight",
            sources=["document"],
            allowed_acl_refs=[acl_ref],
            enforce_acl=True,
        ),
        engine_override="v2",
        fallback=legacy,
    )

    assert legacy_calls == 1
    assert counters == {name: 0 for name in counters}
    assert response["trace"]["global_engine"] == {  # type: ignore[index]
        "requested": "v2",
        "selected": "v1",
        "served": "v1",
        "status": "unavailable",
        "fallback": "legacy_v1",
        "blockers": [f"component_disabled:{component}"],
        "runtime_version": "production-store-multisource-runtime-v2",
        "quality_qualified": False,
    }
    assert runtime.source_runtime_v2.materialized_sources == ()
    assert runtime.source_runtime_v2.derived_root_created is False
    assert runtime.performance_v2.spans() == ()


def test_v1_default_does_not_execute_or_observe_global_v2(settings) -> None:
    runtime = create_runtime(replace(settings, rag_global_planner_v2=False))
    result = runtime.global_v2.search(
        GlobalSearchRequest(
            project_id="project-rag",
            query="default remains v1",
            sources=["document"],
        ),
        engine_override=None,
        fallback=lambda: {"results": [], "trace": {"engine": "v1"}},
    )
    assert result["trace"]["engine"] == "v1"  # type: ignore[index]
    assert runtime.performance_v2.spans() == ()


def test_global_component_switch_environment_is_exact(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RAG_GLOBAL_PLANNER_V2", "false")
    monkeypatch.setenv("RAG_GLOBAL_FUSION_V2", "true")
    settings = Settings.from_env(base_dir=tmp_path)
    assert settings.rag_global_planner_v2 is False
    assert settings.rag_global_fusion_v2 is True

    monkeypatch.setenv("RAG_GLOBAL_PLANNER_V2", "0")
    with pytest.raises(ValueError, match="exactly"):
        Settings.from_env(base_dir=tmp_path)
