from __future__ import annotations

import pytest

from evidence_rag.rag.sources.experiment.analysis_v2 import evaluate_reproduction_v2
from evidence_rag.rag.sources.experiment.contracts_v2 import (
    ExperimentMetricRegistryEntryV2,
    MetricDirectionV2,
    build_experiment_run_snapshot_v2,
)
from evidence_rag.rag.sources.experiment.query_v2 import parse_experiment_query_v2
from evidence_rag.rag.sources.experiment.semantic_v2 import (
    build_experiment_context_v2,
    build_experiment_surfaces_v2,
    retrieve_experiment_surfaces_v2,
)


def _snapshot(run_id: str, status: str = "completed"):
    return build_experiment_run_snapshot_v2(
        {
            "id": run_id,
            "experiment_id": "experiment-1",
            "status": status,
            "dataset_id": "dataset-1",
            "dataset_version": "v1",
            "config": {"seed": 1, "learning_rate": 0.1},
            "environment": {"python": "3.12"},
            "metrics": [{"name": "accuracy", "value": 0.9, "unit": "ratio"}],
        },
        project_id="project-1",
        source_id="source-1",
        generation_id="generation-1",
        acl_ref="acl-1",
        registry={
            "accuracy": ExperimentMetricRegistryEntryV2(
                canonical_name="accuracy",
                aliases=("acc",),
                description="Classification accuracy.",
                unit="ratio",
                direction=MetricDirectionV2.HIGHER,
                provenance="registry-v1",
            )
        },
        observed_at="2026-07-29T00:00:00Z",
    )


def _query(task: str = "best"):
    return parse_experiment_query_v2(
        {"task": task, "run_ids": ["run-1"], "limit": 20},
        project_id="project-1",
        source_id="source-1",
        acl_ref="acl-1",
    )


def test_structured_candidates_are_pinned_before_semantic_tail() -> None:
    first = _snapshot("run-1")
    second = _snapshot("run-2", status="failed")
    units = build_experiment_surfaces_v2(
        first,
        experiment_title="Vision benchmark",
        objective="Improve image classification",
    ) + build_experiment_surfaces_v2(
        second,
        experiment_title="Other benchmark",
    )
    result = retrieve_experiment_surfaces_v2(
        _query(),
        (first, second),
        units,
        semantic_query="accuracy vision",
        structured_snapshot_ids=(first.run_snapshot_id,),
    )
    assert result.pinned_count > 0
    assert all(
        candidate.pinned_structured for candidate in result.candidates[: result.pinned_count]
    )
    assert all(
        candidate.run_snapshot_id == first.run_snapshot_id
        for candidate in result.candidates[: result.pinned_count]
    )


def test_surface_excludes_secret_and_does_not_create_numeric_truth() -> None:
    snapshot = _snapshot("run-1")
    units = build_experiment_surfaces_v2(
        snapshot,
        experiment_title="Vision",
    )
    assert not any("0.9" in unit.text for unit in units)
    with pytest.raises(ValueError, match="unsafe|private"):
        build_experiment_surfaces_v2(
            snapshot,
            experiment_title="api_key=abcdefghijklmnop",
        )


def test_context_is_cited_budgeted_and_uses_precomputed_truth() -> None:
    snapshot = _snapshot("run-1")
    reproduction = evaluate_reproduction_v2(snapshot)
    context = build_experiment_context_v2(
        _query("reproduce"),
        (snapshot,),
        reproduction=(reproduction,),
        budget_tokens=500,
    )
    assert context.used_tokens <= 500
    assert context.citation_count > 0
    assert "identity" in context.role_coverage
    assert "observations" in context.role_coverage
    assert "missing" in context.role_coverage


def test_semantic_scope_mismatch_fails_closed() -> None:
    snapshot = _snapshot("run-1")
    units = build_experiment_surfaces_v2(snapshot, experiment_title="Vision")
    wrong = parse_experiment_query_v2(
        {"task": "search"},
        project_id="project-1",
        source_id="source-1",
        acl_ref="other-acl",
    )
    with pytest.raises(ValueError, match="scope"):
        retrieve_experiment_surfaces_v2(wrong, (snapshot,), units, semantic_query="vision")
