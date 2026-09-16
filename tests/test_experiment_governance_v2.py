from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.rag.sources.experiment.governance_v2 import (
    EvidenceAvailabilityV2,
    ExperimentEngineV2,
    ExperimentReleaseDecisionV2,
    ExperimentReleaseEvidenceV2,
    ExperimentReleaseMetricV2,
    ExperimentReleaseStageV2,
    ExperimentRouteRequestV2,
    build_experiment_release_evidence_v2,
    current_experiment_release_hold_v2,
    evaluate_experiment_release_v2,
    execute_experiment_route_v2,
    route_experiment_engine_v2,
    validate_local_source_root_v2,
    validate_remote_mlflow_url_v2,
)


def test_default_shadow_canary_and_exactly_once_fallback() -> None:
    default = route_experiment_engine_v2(
        ExperimentRouteRequestV2(project_id="project-1", request_id="request-1")
    )
    assert default.engine == ExperimentEngineV2.V1
    assert default.execute_v2 is False
    shadow = route_experiment_engine_v2(
        ExperimentRouteRequestV2(
            project_id="project-1",
            request_id="request-1",
            shadow_enabled=True,
        )
    )
    calls = {"legacy": 0, "v2": 0}

    def legacy():
        calls["legacy"] += 1
        return "legacy"

    def broken():
        calls["v2"] += 1
        raise TimeoutError

    response, shadow_status, reason = execute_experiment_route_v2(
        shadow,
        legacy=legacy,
        v2=broken,
    )
    assert (response, shadow_status, reason) == ("legacy", "v2_failed", "timeout")
    assert calls == {"legacy": 1, "v2": 1}
    first = route_experiment_engine_v2(
        ExperimentRouteRequestV2(
            project_id="project-1",
            request_id="stable-request",
            canary_percent=25,
        )
    )
    second = route_experiment_engine_v2(
        ExperimentRouteRequestV2(
            project_id="project-1",
            request_id="stable-request",
            canary_percent=25,
        )
    )
    assert first.canary_bucket == second.canary_bucket


def test_source_security_rejects_escape_formal_db_and_private_remote(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    child = root / "mlruns"
    child.mkdir()
    assert validate_local_source_root_v2(child, allowed_root=root) == child.resolve()
    formal = root / "evidence-rag.sqlite3"
    formal.touch()
    with pytest.raises(ValueError):
        validate_local_source_root_v2(formal, allowed_root=root)
    with pytest.raises(ValueError):
        validate_local_source_root_v2(tmp_path, allowed_root=root)
    with pytest.raises(ValueError):
        validate_remote_mlflow_url_v2(
            "https://127.0.0.1/mlflow",
            allowed_hosts=frozenset({"127.0.0.1"}),
        )
    assert (
        validate_remote_mlflow_url_v2(
            "https://mlflow.example.test/api",
            allowed_hosts=frozenset({"mlflow.example.test"}),
        )
        == "https://mlflow.example.test/api"
    )


def _evidence(
    stage: ExperimentReleaseStageV2,
    *,
    availability: EvidenceAvailabilityV2 = EvidenceAvailabilityV2.AVAILABLE,
    synthetic: bool = False,
    production_observation: bool = True,
) -> ExperimentReleaseEvidenceV2:
    metric = ExperimentReleaseMetricV2(
        name="predicate_accuracy",
        numerator=9,
        denominator=10,
        value=0.9,
        threshold=0.8,
        comparison="gte",
        availability=availability,
        source_evidence_sha256="sha256:" + "a" * 64,
    )
    return build_experiment_release_evidence_v2(
        project_id="project-1",
        stage=stage,
        previous_evidence_sha256=None,
        baseline_artifact_sha256="sha256:" + "b" * 64,
        treatment_artifact_sha256="sha256:" + "c" * 64,
        metrics=(metric,),
        secret_leakage_count=0,
        acl_leakage_count=0,
        truth_drift_count=0,
        fallback_count=0,
        p95_latency_ms=100,
        storage_bytes=1000,
        rollback_rehearsed=True,
        synthetic=synthetic,
        production_observation=production_observation,
    )


def test_release_requires_available_thresholds_and_trusted_stage() -> None:
    evidence = _evidence(ExperimentReleaseStageV2.SHADOW_INTERNAL_100)
    result = evaluate_experiment_release_v2(
        evidence,
        deployed_stage=ExperimentReleaseStageV2.SHADOW_INTERNAL_100,
    )
    assert result.decision == ExperimentReleaseDecisionV2.PROMOTE_NEXT_STAGE
    provisional = _evidence(
        ExperimentReleaseStageV2.SHADOW_INTERNAL_100,
        availability=EvidenceAvailabilityV2.PROVISIONAL,
    )
    assert (
        evaluate_experiment_release_v2(
            provisional,
            deployed_stage=ExperimentReleaseStageV2.SHADOW_INTERNAL_100,
        ).decision
        == ExperimentReleaseDecisionV2.HOLD_DEFAULT_V1
    )
    no_production_observation = _evidence(
        ExperimentReleaseStageV2.SHADOW_INTERNAL_100,
        production_observation=False,
    )
    assert (
        evaluate_experiment_release_v2(
            no_production_observation,
            deployed_stage=ExperimentReleaseStageV2.SHADOW_INTERNAL_100,
        ).decision
        == ExperimentReleaseDecisionV2.HOLD_DEFAULT_V1
    )
    mismatch = evaluate_experiment_release_v2(
        _evidence(ExperimentReleaseStageV2.OFFLINE),
        deployed_stage=ExperimentReleaseStageV2.CANARY_25,
    )
    assert mismatch.decision == ExperimentReleaseDecisionV2.ROLLBACK_TO_V1


def test_current_truth_is_hold_default_v1() -> None:
    result = current_experiment_release_hold_v2()
    assert result.decision == ExperimentReleaseDecisionV2.HOLD_DEFAULT_V1
    assert result.default_engine == "v1"
    assert result.quality_qualified is False
