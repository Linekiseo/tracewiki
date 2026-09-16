from __future__ import annotations

import pytest

from evidence_rag.rag.sources.experiment.contracts_v2 import (
    ExperimentMetricRegistryEntryV2,
    MetricDirectionV2,
    build_experiment_run_snapshot_v2,
)
from evidence_rag.rag.sources.experiment.query_v2 import (
    ExperimentAggregationV2,
    ExperimentMetricPredicateV2,
    ExperimentOperatorV2,
    ExperimentQueryV2Error,
    compile_experiment_query_v2,
    evaluate_experiment_numeric_v2,
    parse_experiment_query_text_v2,
    parse_experiment_query_v2,
    select_observations_v2,
)


def _snapshot():
    return build_experiment_run_snapshot_v2(
        {
            "id": "run-1",
            "experiment_id": "experiment-1",
            "status": "completed",
            "dataset_id": "dataset-1",
            "dataset_version": "v1",
            "metrics": [
                {
                    "name": "accuracy",
                    "value": 80,
                    "unit": "percent",
                    "step": 1,
                    "history_availability": "full",
                },
                {
                    "name": "accuracy",
                    "value": 90,
                    "unit": "percent",
                    "step": 2,
                    "history_availability": "full",
                },
            ],
        },
        project_id="project-1",
        source_id="source-1",
        generation_id="generation-1",
        acl_ref="acl-1",
        registry={
            "accuracy": ExperimentMetricRegistryEntryV2(
                canonical_name="accuracy",
                aliases=("acc",),
                description="Accuracy.",
                unit="ratio",
                direction=MetricDirectionV2.HIGHER,
                provenance="registry-v1",
            )
        },
        observed_at="2026-07-29T00:00:00Z",
    )


def test_typed_query_compiles_values_as_parameters() -> None:
    query = parse_experiment_query_v2(
        {
            "task": "best",
            "statuses": ["completed"],
            "run_ids": ["run-1"],
            "metrics": [
                {
                    "metric": "accuracy",
                    "operator": "gte",
                    "value": 0.8,
                    "unit": "ratio",
                }
            ],
            "limit": 10,
        },
        project_id="project-1",
        source_id="source-1",
        acl_ref="acl-1",
    )
    compiled = compile_experiment_query_v2(query)
    assert "run-1" not in compiled.sql
    assert "accuracy" not in compiled.sql
    assert "?" in compiled.sql
    assert compiled.fallback_reason is None


def test_text_parser_is_bounded_and_unresolved_falls_back() -> None:
    query = parse_experiment_query_text_v2(
        "best status:completed metric.accuracy>=0.8ratio unexplained",
        project_id="project-1",
        source_id="source-1",
        acl_ref="acl-1",
    )
    assert query.unresolved == ("unexplained",)
    assert compile_experiment_query_v2(query).fallback_reason == "unresolved_predicates"
    with pytest.raises(ExperimentQueryV2Error):
        parse_experiment_query_text_v2(
            "SELECT * FROM runs",
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-1",
        )


def test_numeric_evaluator_handles_direction_units_and_zero_baseline() -> None:
    snapshot = _snapshot()
    definition = snapshot.definitions[0]
    selected = select_observations_v2(
        snapshot,
        ExperimentMetricPredicateV2(
            metric="accuracy",
            operator=ExperimentOperatorV2.GTE,
            value=0.8,
            step_policy="all",
        ),
    )
    best = evaluate_experiment_numeric_v2(
        definition,
        selected,
        operator=ExperimentAggregationV2.BEST,
    )
    assert best.status == "available"
    assert best.value == pytest.approx(0.9)
    zero_baseline = selected[0].model_copy(
        update={
            "numeric_value": 0.0,
            "content_sha256": selected[0].content_sha256,
        }
    )
    relative = evaluate_experiment_numeric_v2(
        definition,
        selected,
        operator=ExperimentAggregationV2.RELATIVE_DELTA,
        baseline=zero_baseline,
    )
    assert relative.status == "unavailable"
    assert relative.reason == "zero_baseline_relative_delta_undefined"


def test_unknown_direction_never_claims_best() -> None:
    snapshot = build_experiment_run_snapshot_v2(
        {
            "id": "run-2",
            "experiment_id": "experiment-1",
            "status": "completed",
            "metrics": [{"name": "mystery", "value": 1.0}],
        },
        project_id="project-1",
        source_id="source-1",
        generation_id="generation-1",
        acl_ref="acl-1",
        registry={},
        observed_at="2026-07-29T00:00:00Z",
    )
    result = evaluate_experiment_numeric_v2(
        snapshot.definitions[0],
        snapshot.observations,
        operator=ExperimentAggregationV2.BEST,
    )
    assert result.status == "unavailable"
    assert result.reason == "metric_direction_unknown"
