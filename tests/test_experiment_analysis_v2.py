from __future__ import annotations

from evidence_rag.rag.sources.experiment.analysis_v2 import (
    ComparabilityDecisionV2,
    aggregate_experiment_run_group_v2,
    compare_experiment_snapshots_v2,
    compare_metric_v2,
    evaluate_reproduction_v2,
)
from evidence_rag.rag.sources.experiment.contracts_v2 import (
    ExperimentMetricRegistryEntryV2,
    MetricDirectionV2,
    build_experiment_run_group_v2,
    build_experiment_run_snapshot_v2,
)

REGISTRY = {
    "accuracy": ExperimentMetricRegistryEntryV2(
        canonical_name="accuracy",
        aliases=("acc",),
        description="Accuracy.",
        unit="ratio",
        direction=MetricDirectionV2.HIGHER,
        provenance="registry-v1",
    )
}


def _snapshot(
    run_id: str,
    generation: str,
    value: float,
    *,
    status: str = "completed",
    dataset_version: str | None = "v1",
    seed: int = 1,
):
    return build_experiment_run_snapshot_v2(
        {
            "id": run_id,
            "experiment_id": "experiment-1",
            "status": status,
            "dataset_id": "dataset-1",
            "dataset_version": dataset_version,
            "repository_id": "repo-1",
            "commit_sha": "a" * 40,
            "command": "python train.py",
            "config": {"seed": seed, "learning_rate": 0.1},
            "environment": {"python": "3.12"},
            "metrics": [{"name": "accuracy", "value": value, "unit": "ratio"}],
            "artifacts": [
                {
                    "name": "model",
                    "kind": "model",
                    "uri": "artifact://model",
                    "checksum": "sha256:" + "b" * 64,
                    "authorized": True,
                }
            ],
        },
        project_id="project-1",
        source_id="source-1",
        generation_id=generation,
        acl_ref="acl-1",
        registry=REGISTRY,
        observed_at="2026-07-29T00:00:00Z",
    )


def test_strict_comparability_and_improvement() -> None:
    baseline = _snapshot("run-a", "generation-1", 0.8)
    candidate = _snapshot("run-b", "generation-1", 0.9, seed=2)
    definition_id = baseline.definitions[0].definition_id
    comparison = compare_experiment_snapshots_v2(
        baseline,
        candidate,
        metric_definition_id=definition_id,
        treatment_config_keys=frozenset({"seed"}),
        controlled_config_keys=frozenset({"learning_rate"}),
    )
    assert comparison.decision == ComparabilityDecisionV2.COMPARABLE
    result = compare_metric_v2(
        comparison,
        baseline.observations[0],
        candidate.observations[0],
        direction=MetricDirectionV2.HIGHER,
    )
    assert result.status == "available"
    assert result.improvement is True


def test_missing_or_different_dataset_never_claims_improvement() -> None:
    baseline = _snapshot("run-a", "generation-1", 0.8)
    candidate = _snapshot(
        "run-b",
        "generation-1",
        0.99,
        dataset_version=None,
    )
    comparison = compare_experiment_snapshots_v2(
        baseline,
        candidate,
        metric_definition_id=baseline.definitions[0].definition_id,
    )
    assert comparison.decision == ComparabilityDecisionV2.INSUFFICIENT_METADATA
    result = compare_metric_v2(
        comparison,
        baseline.observations[0],
        candidate.observations[0],
        direction=MetricDirectionV2.HIGHER,
    )
    assert result.status == "unavailable"
    assert result.improvement is None


def test_group_aggregation_is_explicit_and_excludes_failed() -> None:
    first = _snapshot("run-a", "generation-1", 0.8)
    second = _snapshot("run-b", "generation-1", 1.0)
    failed = _snapshot("run-c", "generation-1", 10.0, status="failed")
    group = build_experiment_run_group_v2("seeds", (first, second, failed))
    result = aggregate_experiment_run_group_v2(
        group,
        (first, second, failed),
        metric_definition_id=first.definitions[0].definition_id,
    )
    assert result.status == "available"
    assert result.n == 2
    assert result.value == 0.9
    assert ("run-c", "status:failed") in result.excluded_runs


def test_reproduction_roles_require_verified_artifact_and_metadata() -> None:
    complete = _snapshot("run-a", "generation-1", 0.8)
    assert evaluate_reproduction_v2(complete).status == "reproducible"
    incomplete = _snapshot(
        "run-b",
        "generation-1",
        0.8,
        dataset_version=None,
    )
    result = evaluate_reproduction_v2(incomplete)
    assert result.status == "insufficient_metadata"
    assert any(role.role == "dataset_version" and role.state == "missing" for role in result.roles)
