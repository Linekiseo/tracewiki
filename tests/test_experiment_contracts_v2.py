from __future__ import annotations

import pytest

from evidence_rag.rag.sources.experiment.contracts_v2 import (
    ArtifactVerificationStateV2,
    ExperimentMetricRegistryEntryV2,
    MetricDefinitionStatusV2,
    MetricDirectionV2,
    build_experiment_run_group_v2,
    build_experiment_run_snapshot_v2,
)


def _registry():
    return {
        "accuracy": ExperimentMetricRegistryEntryV2(
            canonical_name="accuracy",
            aliases=("acc",),
            description="Accuracy on the declared evaluation split.",
            unit="ratio",
            direction=MetricDirectionV2.HIGHER,
            valid_min=0.0,
            valid_max=1.0,
            required_dimensions=("split",),
            default_aggregation="final",
            provenance="reviewed-registry",
        )
    }


def _run(run_id: str = "run-a", *, status: str = "completed") -> dict:
    return {
        "id": run_id,
        "experiment_id": "experiment-a",
        "status": status,
        "repository_id": "repository-a",
        "commit_sha": "a" * 40,
        "branch": "main",
        "command": "python train.py",
        "dataset_id": "dataset-a",
        "dataset_version": "v1",
        "dataset_split": "test",
        "config": {
            "seed": 11,
            "optimizer": {"name": "adam", "password": "do-not-store"},
        },
        "environment": {
            "python": "3.13",
            "device": "cpu",
            "hostname": "private-host",
        },
        "metrics": [
            {
                "name": "acc",
                "value": 91.0,
                "unit": "%",
                "split": "test",
                "step": 2,
                "observed_at": "2026-07-29T00:00:00Z",
                "history_availability": "full",
            },
            {
                "name": "mystery",
                "value": 4.0,
                "unit": "score",
            },
        ],
        "artifacts": [
            {
                "name": "model",
                "kind": "checkpoint",
                "uri": "artifact://model-a",
                "checksum": "sha256:" + ("1" * 64),
                "authorized": True,
            }
        ],
    }


def _snapshot(run: dict):
    return build_experiment_run_snapshot_v2(
        run,
        project_id="project-a",
        source_id="experiment-source-a",
        generation_id="generation-a",
        acl_ref="project:project-a",
        registry=_registry(),
        observed_at="2026-07-29T00:01:00Z",
    )


def test_snapshot_freezes_registry_numeric_conditions_and_artifact_truth() -> None:
    snapshot = _snapshot(_run())
    assert snapshot == _snapshot(_run())
    definitions = {item.canonical_name: item for item in snapshot.definitions}
    assert definitions["accuracy"].direction is MetricDirectionV2.HIGHER
    assert definitions["mystery"].status is MetricDefinitionStatusV2.UNKNOWN
    assert definitions["mystery"].direction is MetricDirectionV2.UNKNOWN
    accuracy = next(item for item in snapshot.observations if item.raw_name == "acc")
    assert accuracy.numeric_value == pytest.approx(0.91)
    assert accuracy.canonical_unit == "ratio"
    assert "optimizer.password" in snapshot.config.redacted_keys
    assert "hostname" in snapshot.environment.omitted_keys
    assert snapshot.artifacts[0].verification_state is ArtifactVerificationStateV2.PRESENT_VERIFIED
    assert snapshot.artifacts[0].locator.startswith("experiment-v2://artifact/")


def test_nonfinite_and_secret_values_fail_closed_or_are_explicitly_invalid() -> None:
    run = _run()
    run["metrics"][0]["value"] = float("nan")
    snapshot = _snapshot(run)
    invalid = next(item for item in snapshot.observations if item.raw_name == "acc")
    assert not invalid.valid
    assert invalid.numeric_value is None
    assert invalid.invalid_reason == "non_finite_or_non_numeric"

    run = _run()
    run["artifacts"][0]["uri"] = "/Users/private/model.bin"
    with pytest.raises(ValueError, match="unsafe"):
        _snapshot(run)


def test_run_group_is_explicit_and_excludes_non_completed_runs() -> None:
    first = _snapshot(_run("run-a"))
    failed = _snapshot(_run("run-b", status="failed"))
    group = build_experiment_run_group_v2("seed repeats", (first, failed))
    assert [item.included for item in group.members] == [True, False]
    assert group.members[1].exclusion_reason == "status:failed"
