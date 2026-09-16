from __future__ import annotations

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.experiment.evaluation_v1 import (
    Answerability,
    EvaluationTriState,
    ExperimentEvaluationError,
    ExperimentEvaluationMetric,
    MetricDirection,
    ReviewedCandidate,
    ReviewedExperimentCaseRow,
    ReviewedOutcome,
    ReviewedReason,
    evaluate_reviewed_experiment_retrieval,
    load_experiment_golden_v1,
    perfect_reviewed_experiment_rows,
)


def _overall(result, metric: ExperimentEvaluationMetric):
    return next(item for item in result.overall.metrics if item.metric == metric)


def _candidates(dataset, *entity_ids: str) -> tuple[ReviewedCandidate, ...]:
    authority = dataset.authority_entities
    return tuple(
        ReviewedCandidate(
            entity_id=entity_id,
            locator=authority[entity_id].locator,
            acl_ref=authority[entity_id].acl_ref,
            rank=rank,
        )
        for rank, entity_id in enumerate(entity_ids, start=1)
    )


def test_evaluator_recomputes_all_frozen_denominators_and_eight_slices() -> None:
    dataset = load_experiment_golden_v1()
    rows = perfect_reviewed_experiment_rows(dataset)
    result = evaluate_reviewed_experiment_retrieval(rows, dataset=dataset)

    assert result.membership == dataset.case_membership
    assert len(result.slices) == 8
    assert [item.total_cases for item in result.slices] == [5, 7, 8, 8, 6, 4, 4, 3]
    expected = {
        ExperimentEvaluationMetric.EXACT_ENTITY_ACCURACY: 5,
        ExperimentEvaluationMetric.PREDICATE_ACCURACY: 28,
        ExperimentEvaluationMetric.NUMERIC_UNIT_DIRECTION_ACCURACY: 20,
        ExperimentEvaluationMetric.COMPARABILITY_ACCURACY: 9,
        ExperimentEvaluationMetric.REPRODUCTION_ACCURACY: 6,
        ExperimentEvaluationMetric.HARD_NEGATIVE_AVOIDANCE: 45,
        ExperimentEvaluationMetric.UNANSWERABLE_ACCURACY: 10,
        ExperimentEvaluationMetric.ZERO_RESULT_ACCURACY: 2,
    }
    for metric, denominator in expected.items():
        measured = _overall(result, metric)
        assert measured.state == EvaluationTriState.AVAILABLE
        assert measured.denominator == denominator
        assert measured.numerator == denominator
        assert measured.value == 1.0


@pytest.mark.parametrize(
    "membership",
    [
        (),
        ("experiment-v1-001",),
        tuple(f"experiment-v1-{ordinal:03d}" for ordinal in range(45, 0, -1)),
        (
            "experiment-v1-001",
            "experiment-v1-001",
            *tuple(f"experiment-v1-{ordinal:03d}" for ordinal in range(3, 46)),
        ),
        (*tuple(f"experiment-v1-{ordinal:03d}" for ordinal in range(1, 45)), "unknown"),
    ],
)
def test_evaluator_rejects_denominator_membership_attacks(
    membership: tuple[str, ...],
) -> None:
    dataset = load_experiment_golden_v1()
    rows = perfect_reviewed_experiment_rows(dataset)

    with pytest.raises(ExperimentEvaluationError):
        evaluate_reviewed_experiment_retrieval(
            rows,
            dataset=dataset,
            membership=membership,
        )


def test_rows_cannot_self_report_truth_status_or_denominator() -> None:
    dataset = load_experiment_golden_v1()
    payload = perfect_reviewed_experiment_rows(dataset)[0].model_dump(mode="json")

    for field, value in (
        ("truth", True),
        ("status", "AVAILABLE"),
        ("denominator", 0),
    ):
        with pytest.raises(ValidationError):
            ReviewedExperimentCaseRow.model_validate({**payload, field: value})


def test_unknown_direction_missing_and_hard_negative_are_scored_from_authority() -> None:
    dataset = load_experiment_golden_v1()
    rows = list(perfect_reviewed_experiment_rows(dataset))

    unknown_index = 18
    unknown = rows[unknown_index]
    altered_truth = unknown.observed_metric_truth[0].model_copy(
        update={"direction": MetricDirection.HIGHER}
    )
    rows[unknown_index] = unknown.model_copy(update={"observed_metric_truth": (altered_truth,)})

    hard_case = dataset.cases[12]
    hard_entity = hard_case.hard_negatives[0].entity
    hard_row = rows[12]
    rows[12] = hard_row.model_copy(
        update={
            "candidates": (
                *hard_row.candidates,
                ReviewedCandidate(
                    entity_id=hard_entity.entity_id,
                    locator=hard_entity.locator,
                    acl_ref=hard_entity.acl_ref,
                    rank=len(hard_row.candidates) + 1,
                ),
            )
        }
    )
    result = evaluate_reviewed_experiment_retrieval(rows, dataset=dataset)

    numeric = _overall(
        result,
        ExperimentEvaluationMetric.NUMERIC_UNIT_DIRECTION_ACCURACY,
    )
    hard_negative = _overall(
        result,
        ExperimentEvaluationMetric.HARD_NEGATIVE_AVOIDANCE,
    )
    assert numeric.numerator == numeric.denominator - 2
    assert hard_negative.numerator == hard_negative.denominator - 1
    assert dataset.cases[19].metric_truth[0].missing is True
    assert dataset.cases[19].metric_truth[0].canonical_value is None


def test_unavailable_and_system_error_remain_distinct_and_keep_denominators() -> None:
    dataset = load_experiment_golden_v1()
    rows = list(perfect_reviewed_experiment_rows(dataset))
    rows[0] = rows[0].model_copy(
        update={
            "outcome": ReviewedOutcome.UNAVAILABLE,
            "candidates": (),
            "unavailable_reason": ReviewedReason.UNSUPPORTED_V1,
            "error_reason": None,
        }
    )
    rows[1] = rows[1].model_copy(
        update={
            "outcome": ReviewedOutcome.ERROR,
            "candidates": (),
            "unavailable_reason": None,
            "error_reason": ReviewedReason.SYSTEM_ERROR,
            "observed_answerability": Answerability.ANSWERABLE,
        }
    )
    result = evaluate_reviewed_experiment_retrieval(rows, dataset=dataset)
    exact = _overall(result, ExperimentEvaluationMetric.EXACT_ENTITY_ACCURACY)

    assert exact.denominator == 5
    assert exact.unavailable == 1
    assert exact.error == 1
    assert exact.available == 3
    assert exact.numerator == 3


def test_candidate_truth_rejects_unlisted_false_positives_and_removed_positives() -> None:
    dataset = load_experiment_golden_v1()
    rows = list(perfect_reviewed_experiment_rows(dataset))

    exact_case = dataset.cases[0]
    unexpected = dataset.cases[1].expected_entities[0].entity_id
    rows[0] = rows[0].model_copy(
        update={
            "candidates": _candidates(
                dataset,
                exact_case.expected_candidate_order[0],
                unexpected,
            )
        }
    )
    wrong_kind = dataset.cases[4].acceptable_alternative_ids[0]
    rows[1] = rows[1].model_copy(update={"candidates": _candidates(dataset, wrong_kind)})
    result = evaluate_reviewed_experiment_retrieval(rows, dataset=dataset)

    exact_result = result.case_results[0].correctness
    removed_result = result.case_results[1].correctness
    assert not exact_result[ExperimentEvaluationMetric.EXACT_ENTITY_ACCURACY]
    assert not exact_result[ExperimentEvaluationMetric.PREDICATE_ACCURACY]
    assert not exact_result[ExperimentEvaluationMetric.HARD_NEGATIVE_AVOIDANCE]
    assert not removed_result[ExperimentEvaluationMetric.EXACT_ENTITY_ACCURACY]
    assert not removed_result[ExperimentEvaluationMetric.PREDICATE_ACCURACY]
    assert not removed_result[ExperimentEvaluationMetric.HARD_NEGATIVE_AVOIDANCE]


def test_declared_alternative_is_allowed_only_after_all_ordered_positives() -> None:
    dataset = load_experiment_golden_v1()
    perfect = list(perfect_reviewed_experiment_rows(dataset))
    case = dataset.cases[4]
    expected = case.expected_candidate_order[0]
    alternative = case.acceptable_alternative_ids[0]

    accepted = perfect.copy()
    accepted[4] = accepted[4].model_copy(
        update={"candidates": _candidates(dataset, expected, alternative)}
    )
    accepted_result = evaluate_reviewed_experiment_retrieval(
        accepted,
        dataset=dataset,
    )
    assert all(accepted_result.case_results[4].correctness.values())

    reordered = perfect.copy()
    reordered[4] = reordered[4].model_copy(
        update={"candidates": _candidates(dataset, alternative, expected)}
    )
    reordered_result = evaluate_reviewed_experiment_retrieval(
        reordered,
        dataset=dataset,
    )
    assert not reordered_result.case_results[4].correctness[
        ExperimentEvaluationMetric.EXACT_ENTITY_ACCURACY
    ]
    assert not reordered_result.case_results[4].correctness[
        ExperimentEvaluationMetric.PREDICATE_ACCURACY
    ]

    alternative_only = perfect.copy()
    alternative_only[4] = alternative_only[4].model_copy(
        update={"candidates": _candidates(dataset, alternative)}
    )
    alternative_only_result = evaluate_reviewed_experiment_retrieval(
        alternative_only,
        dataset=dataset,
    )
    assert not alternative_only_result.case_results[4].correctness[
        ExperimentEvaluationMetric.EXACT_ENTITY_ACCURACY
    ]


def test_correct_observed_truth_with_wrong_candidate_fails_all_applicable_truth_metrics() -> None:
    dataset = load_experiment_golden_v1()
    rows = list(perfect_reviewed_experiment_rows(dataset))
    case_index = 20
    case = dataset.cases[case_index]
    wrong = next(
        entity_id
        for entity_id, entity in dataset.authority_entities.items()
        if entity.kind.value == "run"
        and entity_id not in case.expected_candidate_order
        and entity_id not in case.forbidden_candidate_ids
    )
    rows[case_index] = rows[case_index].model_copy(
        update={"candidates": _candidates(dataset, wrong)}
    )
    result = evaluate_reviewed_experiment_retrieval(rows, dataset=dataset)
    correctness = result.case_results[case_index].correctness

    assert not correctness[ExperimentEvaluationMetric.NUMERIC_UNIT_DIRECTION_ACCURACY]
    assert not correctness[ExperimentEvaluationMetric.COMPARABILITY_ACCURACY]
    assert not correctness[ExperimentEvaluationMetric.HARD_NEGATIVE_AVOIDANCE]


def test_multi_positive_order_and_candidate_rank_contract_are_frozen() -> None:
    dataset = load_experiment_golden_v1()
    rows = list(perfect_reviewed_experiment_rows(dataset))
    case_index = 6
    expected = dataset.cases[case_index].expected_candidate_order
    rows[case_index] = rows[case_index].model_copy(
        update={"candidates": _candidates(dataset, *reversed(expected))}
    )
    result = evaluate_reviewed_experiment_retrieval(rows, dataset=dataset)
    assert not result.case_results[case_index].correctness[
        ExperimentEvaluationMetric.PREDICATE_ACCURACY
    ]

    payload = perfect_reviewed_experiment_rows(dataset)[0].model_dump(mode="json")
    payload["candidates"][0]["rank"] = 2
    with pytest.raises(ValidationError):
        ReviewedExperimentCaseRow.model_validate(payload)


def test_series_and_aggregation_truth_is_recomputed_from_observation_identities() -> None:
    dataset = load_experiment_golden_v1()
    rows = list(perfect_reviewed_experiment_rows(dataset))
    case_index = 38
    row = rows[case_index]
    payload = row.observed_metric_truth[0].model_dump(mode="json")
    payload["raw_series"][0] = 999.0
    altered = type(row.observed_metric_truth[0]).model_validate(payload)
    rows[case_index] = row.model_copy(update={"observed_metric_truth": (altered,)})
    result = evaluate_reviewed_experiment_retrieval(rows, dataset=dataset)

    correctness = result.case_results[case_index].correctness
    assert not correctness[ExperimentEvaluationMetric.NUMERIC_UNIT_DIRECTION_ACCURACY]
    assert correctness[ExperimentEvaluationMetric.PREDICATE_ACCURACY]
