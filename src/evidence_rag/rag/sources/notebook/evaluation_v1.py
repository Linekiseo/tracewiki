"""Released Notebook Golden v1 and reviewed-row evaluator."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import canonical_sha256
from .fixture_v1 import (
    NOTEBOOK_GOLDEN_DATASET_ID,
    NOTEBOOK_GOLDEN_DATASET_VERSION,
    NotebookFixtureEntity,
    build_notebook_fixture_v1,
)
from .store import NotebookV2Store

NOTEBOOK_GOLDEN_CASE_COUNT = 40
NOTEBOOK_GOLDEN_PACKAGE_SHA256 = (
    "sha256:49b52bb046aa12f8882c5056592350493377cb691a9fdb5c007f3cf7681fecb3"
)
NOTEBOOK_GOLDEN_AUTHORITY_SHA256 = (
    "sha256:3dd567bf2b749a4209b92307c41bb8b3791ed0a3cf52efd1740e59cecd79ab33"
)


class NotebookGoldenError(ValueError):
    """Raised when released Notebook truth or reviewed rows are malformed."""


class NotebookGoldenSlice(StrEnum):
    LOCATION = "notebook_cell_location"
    CODE_LOGIC = "code_logic"
    PARAMETER = "parameter_config"
    OUTPUT = "output_table_figure"
    ERROR_RETRY = "error_retry"
    LINEAGE = "dataflow_lineage"
    COMPARE = "version_compare"
    REPRODUCTION = "reproduction_unanswerable_security"


EXPECTED_NOTEBOOK_SLICE_COUNTS = {
    NotebookGoldenSlice.LOCATION: 5,
    NotebookGoldenSlice.CODE_LOGIC: 6,
    NotebookGoldenSlice.PARAMETER: 5,
    NotebookGoldenSlice.OUTPUT: 6,
    NotebookGoldenSlice.ERROR_RETRY: 6,
    NotebookGoldenSlice.LINEAGE: 5,
    NotebookGoldenSlice.COMPARE: 4,
    NotebookGoldenSlice.REPRODUCTION: 3,
}


class NotebookMetric(StrEnum):
    RETRIEVAL_RECALL_AT_10 = "retrieval_recall_at_10"
    CELL_RECALL_AT_10 = "cell_recall_at_10"
    OUTPUT_ERROR_RECALL_AT_10 = "output_error_recall_at_10"
    PARAMETER_ACCURACY = "parameter_accuracy"
    PRODUCER_OUTPUT_PATH_RECALL = "producer_output_path_recall"
    EXECUTION_ORDER_ACCURACY = "execution_order_accuracy"
    STALE_OUTPUT_DETECTION = "stale_output_detection"
    CELL_MATCH_ACCURACY = "cell_match_accuracy"
    REPRODUCTION_ROLE_COVERAGE = "reproduction_role_coverage"
    LOCATOR_ACCURACY = "locator_accuracy"
    HARD_NEGATIVE_AVOIDANCE = "hard_negative_avoidance"
    UNANSWERABLE_ACCURACY = "unanswerable_accuracy"


class NotebookMetricStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class NotebookReviewAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class _FrozenEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class NotebookGoldenCase(_FrozenEvaluation):
    case_id: str
    slice: NotebookGoldenSlice
    query: str
    expected_entity_ids: tuple[str, ...]
    hard_negative_entity_ids: tuple[str, ...] = ()
    eligible_metrics: tuple[NotebookMetric, ...]
    expected_empty: bool = False

    @model_validator(mode="after")
    def _case_truth(self) -> NotebookGoldenCase:
        if len(set(self.expected_entity_ids)) != len(self.expected_entity_ids):
            raise ValueError("expected entity membership must be unique")
        if len(set(self.hard_negative_entity_ids)) != len(self.hard_negative_entity_ids):
            raise ValueError("hard-negative membership must be unique")
        if set(self.expected_entity_ids) & set(self.hard_negative_entity_ids):
            raise ValueError("positive and hard-negative identities overlap")
        if self.expected_empty != (not self.expected_entity_ids):
            raise ValueError("expected_empty must match empty positive membership")
        if len(set(self.eligible_metrics)) != len(self.eligible_metrics):
            raise ValueError("eligible metric membership must be unique")
        return self


class NotebookGoldenDataset(_FrozenEvaluation):
    dataset_id: str
    dataset_version: str
    package_sha256: str
    authority_sha256: str
    fixture_recipe_sha256: str
    entities: tuple[NotebookFixtureEntity, ...]
    cases: tuple[NotebookGoldenCase, ...]

    def package_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"package_sha256", "authority_sha256"},
        )

    @model_validator(mode="after")
    def _released_truth(self) -> NotebookGoldenDataset:
        if self.dataset_id != NOTEBOOK_GOLDEN_DATASET_ID:
            raise ValueError("unexpected Notebook Golden dataset id")
        if self.dataset_version != NOTEBOOK_GOLDEN_DATASET_VERSION:
            raise ValueError("unexpected Notebook Golden dataset version")
        if len(self.cases) != NOTEBOOK_GOLDEN_CASE_COUNT:
            raise ValueError("Notebook Golden must contain exactly 40 cases")
        expected_ids = tuple(f"nb-v1-{index:03d}" for index in range(1, 41))
        if tuple(item.case_id for item in self.cases) != expected_ids:
            raise ValueError("Notebook Golden case membership/order is not canonical")
        if Counter(item.slice for item in self.cases) != Counter(EXPECTED_NOTEBOOK_SLICE_COUNTS):
            raise ValueError("Notebook Golden slice counts are not canonical")
        entity_ids = tuple(item.entity_id for item in self.entities)
        if len(entity_ids) != len(set(entity_ids)) or entity_ids != tuple(sorted(entity_ids)):
            raise ValueError("Notebook entity authority must be unique and ordered")
        authority = set(entity_ids)
        for case in self.cases:
            if not set(case.expected_entity_ids).issubset(authority):
                raise ValueError("case positive identity is outside released entity authority")
            if not set(case.hard_negative_entity_ids).issubset(authority):
                raise ValueError("case hard negative is outside released entity authority")
        expected_package = canonical_sha256(self.package_payload())
        if self.package_sha256 != expected_package:
            raise ValueError("Notebook Golden package digest mismatch")
        expected_authority = canonical_sha256(
            {
                "dataset_id": self.dataset_id,
                "dataset_version": self.dataset_version,
                "package_sha256": self.package_sha256,
                "case_membership": expected_ids,
                "slice_counts": {
                    key.value: value for key, value in EXPECTED_NOTEBOOK_SLICE_COUNTS.items()
                },
                "entity_ids": entity_ids,
            }
        )
        if self.authority_sha256 != expected_authority:
            raise ValueError("Notebook Golden authority digest mismatch")
        return self


class NotebookReviewedCandidate(_FrozenEvaluation):
    entity_id: str
    locator: str


class NotebookReviewedRow(_FrozenEvaluation):
    case_id: str
    availability: NotebookReviewAvailability
    candidates: tuple[NotebookReviewedCandidate, ...] = Field(max_length=10)
    diagnostic: str | None = None

    @model_validator(mode="after")
    def _availability_contract(self) -> NotebookReviewedRow:
        if self.availability != NotebookReviewAvailability.AVAILABLE and self.candidates:
            raise ValueError("unavailable/error rows must not contain candidates")
        if len({item.entity_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("reviewed candidates must be unique")
        return self


class NotebookMetricResult(_FrozenEvaluation):
    metric: NotebookMetric
    status: NotebookMetricStatus
    numerator: int
    eligible_denominator: int
    evaluated_denominator: int
    unavailable_count: int
    value: float | None


class NotebookSliceResult(_FrozenEvaluation):
    slice: NotebookGoldenSlice
    status: NotebookMetricStatus
    numerator: int
    eligible_denominator: int
    evaluated_denominator: int
    unavailable_count: int
    value: float | None


class NotebookEvaluationReport(_FrozenEvaluation):
    dataset_id: str
    dataset_version: str
    package_sha256: str
    authority_sha256: str
    case_count: int
    metrics: tuple[NotebookMetricResult, ...]
    slices: tuple[NotebookSliceResult, ...]
    hard_negative_case_count: int
    reviewed_row_count: int


class NotebookFoundationProbe(_FrozenEvaluation):
    status: str
    package_sha256: str
    authority_sha256: str
    fixture_recipe_sha256: str
    case_count: int
    slice_counts: dict[str, int]
    publication_count: int
    template_count: int
    revision_count: int
    execution_count: int
    cell_version_count: int
    cell_execution_count: int
    parameter_count: int
    artifact_count: int
    database_sidecars: tuple[str, ...]
    secret_or_path_leakage: int
    baseline_run_created: bool
    baseline_metrics_created: bool


def _eligible(*metrics: NotebookMetric) -> tuple[NotebookMetric, ...]:
    return (
        NotebookMetric.RETRIEVAL_RECALL_AT_10,
        NotebookMetric.LOCATOR_ACCURACY,
        *metrics,
    )


def _case_specs() -> tuple[
    tuple[NotebookGoldenSlice, str, tuple[str, ...], tuple[str, ...], tuple[NotebookMetric, ...]],
    ...,
]:
    cell = NotebookMetric.CELL_RECALL_AT_10
    output = NotebookMetric.OUTPUT_ERROR_RECALL_AT_10
    parameter = NotebookMetric.PARAMETER_ACCURACY
    lineage = NotebookMetric.PRODUCER_OUTPUT_PATH_RECALL
    order = NotebookMetric.EXECUTION_ORDER_ACCURACY
    stale = NotebookMetric.STALE_OUTPUT_DETECTION
    match = NotebookMetric.CELL_MATCH_ACCURACY
    reproduce = NotebookMetric.REPRODUCTION_ROLE_COVERAGE
    hard = NotebookMetric.HARD_NEGATIVE_AVOIDANCE
    unanswerable = NotebookMetric.UNANSWERABLE_ACCURACY
    return (
        (
            NotebookGoldenSlice.LOCATION,
            "Locate the parameters cell.",
            ("cell.retry.parameters",),
            (),
            _eligible(cell),
        ),
        (
            NotebookGoldenSlice.LOCATION,
            "Locate the score implementation cell.",
            ("cell.retry.score",),
            ("cell.distractor.score",),
            _eligible(cell, hard),
        ),
        (
            NotebookGoldenSlice.LOCATION,
            "Locate the figure-producing cell.",
            ("cell.retry.figure",),
            (),
            _eligible(cell),
        ),
        (
            NotebookGoldenSlice.LOCATION,
            "Locate the cell that failed before retry.",
            ("cell.failed.error-or-retry",),
            ("execution.retry",),
            _eligible(cell, hard),
        ),
        (
            NotebookGoldenSlice.LOCATION,
            "Locate the successful retry execution.",
            ("execution.retry",),
            ("execution.failed",),
            _eligible(hard),
        ),
        (
            NotebookGoldenSlice.CODE_LOGIC,
            "Where are records defined?",
            ("cell.retry.load-data",),
            ("cell.retry.narrative",),
            _eligible(cell, hard),
        ),
        (
            NotebookGoldenSlice.CODE_LOGIC,
            "How is score computed?",
            ("cell.retry.score",),
            ("cell.distractor.score", "cell.retry.narrative"),
            _eligible(cell, hard),
        ),
        (
            NotebookGoldenSlice.CODE_LOGIC,
            "Where is the summary mapping constructed?",
            ("cell.retry.table",),
            (),
            _eligible(cell),
        ),
        (
            NotebookGoldenSlice.CODE_LOGIC,
            "Which cell reads the external dependency?",
            ("cell.retry.external-dependency",),
            (),
            _eligible(cell),
        ),
        (
            NotebookGoldenSlice.CODE_LOGIC,
            "Which revision added the quality gate?",
            ("cell.moved.new-quality-check",),
            (),
            _eligible(cell),
        ),
        (
            NotebookGoldenSlice.CODE_LOGIC,
            "Which code cell defines future_score but was not run?",
            ("cell.retry.never-ran",),
            ("cell.retry.narrative",),
            _eligible(cell, hard),
        ),
        (
            NotebookGoldenSlice.PARAMETER,
            "What typed value was used for seed?",
            ("parameter.retry.seed",),
            ("parameter.distractor.seed",),
            _eligible(parameter, hard),
        ),
        (
            NotebookGoldenSlice.PARAMETER,
            "What numeric threshold configured the run?",
            ("parameter.retry.threshold",),
            ("parameter.distractor.threshold",),
            _eligible(parameter, hard),
        ),
        (
            NotebookGoldenSlice.PARAMETER,
            "Which dataset parameter was observed?",
            ("parameter.retry.dataset",),
            (),
            _eligible(parameter),
        ),
        (
            NotebookGoldenSlice.PARAMETER,
            "In the distractor notebook, seed is which string parameter?",
            ("parameter.distractor.seed",),
            ("parameter.retry.seed",),
            _eligible(parameter, hard),
        ),
        (
            NotebookGoldenSlice.PARAMETER,
            "Compare numeric seed with the same-looking string seed.",
            ("parameter.retry.seed", "parameter.distractor.seed"),
            (),
            _eligible(parameter),
        ),
        (
            NotebookGoldenSlice.OUTPUT,
            "Retrieve the score stream output.",
            ("artifact.retry.score.0",),
            ("cell.retry.narrative",),
            _eligible(output, hard),
        ),
        (
            NotebookGoldenSlice.OUTPUT,
            "Retrieve the table execute result.",
            ("artifact.retry.table.0",),
            (),
            _eligible(output),
        ),
        (
            NotebookGoldenSlice.OUTPUT,
            "Retrieve the safe figure representation, not binary bytes.",
            ("artifact.retry.figure.0",),
            (),
            _eligible(output),
        ),
        (
            NotebookGoldenSlice.OUTPUT,
            "What output proves the retry succeeded?",
            ("artifact.retry.error-or-retry.0",),
            ("artifact.failed.error-or-retry.0",),
            _eligible(output, hard),
        ),
        (
            NotebookGoldenSlice.OUTPUT,
            "Identify the stale old-kernel output.",
            ("artifact.failed.stale-output.0",),
            (),
            _eligible(output, stale),
        ),
        (
            NotebookGoldenSlice.OUTPUT,
            "Retrieve the unconfirmed metric-like output.",
            ("artifact.retry.metric-like.0",),
            (),
            _eligible(output),
        ),
        (
            NotebookGoldenSlice.ERROR_RETRY,
            "Retrieve the RuntimeError evidence.",
            ("artifact.failed.error-or-retry.0",),
            ("artifact.retry.error-or-retry.0",),
            _eligible(output, hard),
        ),
        (
            NotebookGoldenSlice.ERROR_RETRY,
            "Which execution failed?",
            ("execution.failed",),
            ("execution.retry",),
            _eligible(hard),
        ),
        (
            NotebookGoldenSlice.ERROR_RETRY,
            "Which later output shows recovery?",
            ("artifact.retry.error-or-retry.0",),
            ("artifact.failed.error-or-retry.0",),
            _eligible(output, hard),
        ),
        (
            NotebookGoldenSlice.ERROR_RETRY,
            "Trace failure then successful retry in order.",
            ("execution.failed", "execution.retry"),
            (),
            _eligible(order),
        ),
        (
            NotebookGoldenSlice.ERROR_RETRY,
            "Detect output with no current execution count.",
            ("cell-execution.failed.stale-output",),
            (),
            _eligible(stale),
        ),
        (
            NotebookGoldenSlice.ERROR_RETRY,
            "Identify the code cell that never executed.",
            ("cell-execution.retry.never-ran",),
            ("artifact.failed.stale-output.0",),
            _eligible(hard),
        ),
        (
            NotebookGoldenSlice.LINEAGE,
            "Trace records producer to score consumer.",
            ("cell.retry.load-data", "cell.retry.score"),
            (),
            _eligible(cell, lineage),
        ),
        (
            NotebookGoldenSlice.LINEAGE,
            "Trace score producer to summary output cell.",
            ("cell.retry.score", "cell.retry.table"),
            (),
            _eligible(cell, lineage),
        ),
        (
            NotebookGoldenSlice.LINEAGE,
            "Trace threshold parameter to the table cell.",
            ("parameter.retry.threshold", "cell.retry.table"),
            ("parameter.distractor.threshold",),
            _eligible(lineage, hard),
        ),
        (
            NotebookGoldenSlice.LINEAGE,
            "Order external dependency before score execution.",
            ("cell-execution.retry.external-dependency", "cell-execution.retry.score"),
            (),
            _eligible(lineage, order),
        ),
        (
            NotebookGoldenSlice.LINEAGE,
            "Select the training score, not same-name distractor.",
            ("cell.retry.score",),
            ("cell.distractor.score",),
            _eligible(cell, lineage, hard),
        ),
        (
            NotebookGoldenSlice.COMPARE,
            "Match score cell across moved revision.",
            ("cell.retry.score", "cell.moved.score"),
            (),
            _eligible(cell, match),
        ),
        (
            NotebookGoldenSlice.COMPARE,
            "Match table cell across ordinal shift.",
            ("cell.retry.table", "cell.moved.table"),
            (),
            _eligible(cell, match),
        ),
        (
            NotebookGoldenSlice.COMPARE,
            "Which cell was inserted only in revision two?",
            ("cell.moved.new-quality-check",),
            (),
            _eligible(cell, match),
        ),
        (
            NotebookGoldenSlice.COMPARE,
            "Show one authored revision with failed and retry executions.",
            ("revision.failed", "execution.failed", "execution.retry"),
            (),
            _eligible(match),
        ),
        (
            NotebookGoldenSlice.REPRODUCTION,
            "Collect revision, parameters, and successful execution for reproduction.",
            (
                "revision.retry",
                "parameter.retry.seed",
                "parameter.retry.threshold",
                "parameter.retry.dataset",
                "execution.retry",
            ),
            (),
            _eligible(reproduce),
        ),
        (
            NotebookGoldenSlice.REPRODUCTION,
            "Which confirmed Experiment Metric was produced by validation_accuracy text?",
            (),
            ("artifact.retry.metric-like.0",),
            (unanswerable, hard),
        ),
        (
            NotebookGoldenSlice.REPRODUCTION,
            "Reveal the private absolute input path from the notebook.",
            (),
            ("cell.retry.external-dependency",),
            (unanswerable, hard),
        ),
    )


def _build_dataset_payload() -> tuple[
    str,
    str,
    str,
    tuple[NotebookFixtureEntity, ...],
    tuple[NotebookGoldenCase, ...],
]:
    fixture = build_notebook_fixture_v1()
    aliases = fixture.alias_map()
    cases: list[NotebookGoldenCase] = []
    for index, (slice_name, query, positives, negatives, metrics) in enumerate(
        _case_specs(), start=1
    ):
        cases.append(
            NotebookGoldenCase(
                case_id=f"nb-v1-{index:03d}",
                slice=slice_name,
                query=query,
                expected_entity_ids=tuple(aliases[name] for name in positives),
                hard_negative_entity_ids=tuple(aliases[name] for name in negatives),
                eligible_metrics=metrics,
                expected_empty=not positives,
            )
        )
    base = {
        "dataset_id": NOTEBOOK_GOLDEN_DATASET_ID,
        "dataset_version": NOTEBOOK_GOLDEN_DATASET_VERSION,
        "fixture_recipe_sha256": fixture.recipe_sha256,
        "entities": [item.model_dump(mode="json") for item in fixture.entities],
        "cases": [item.model_dump(mode="json") for item in cases],
    }
    package_sha256 = canonical_sha256(base)
    authority_sha256 = canonical_sha256(
        {
            "dataset_id": NOTEBOOK_GOLDEN_DATASET_ID,
            "dataset_version": NOTEBOOK_GOLDEN_DATASET_VERSION,
            "package_sha256": package_sha256,
            "case_membership": [item.case_id for item in cases],
            "slice_counts": {
                key.value: value for key, value in EXPECTED_NOTEBOOK_SLICE_COUNTS.items()
            },
            "entity_ids": [item.entity_id for item in fixture.entities],
        }
    )
    return (
        package_sha256,
        authority_sha256,
        fixture.recipe_sha256,
        fixture.entities,
        tuple(cases),
    )


def load_notebook_golden_v1() -> NotebookGoldenDataset:
    package, authority, recipe, entities, cases = _build_dataset_payload()
    if package != NOTEBOOK_GOLDEN_PACKAGE_SHA256:
        raise NotebookGoldenError("Notebook Golden package differs from released digest")
    if authority != NOTEBOOK_GOLDEN_AUTHORITY_SHA256:
        raise NotebookGoldenError("Notebook Golden authority differs from released digest")
    return NotebookGoldenDataset(
        dataset_id=NOTEBOOK_GOLDEN_DATASET_ID,
        dataset_version=NOTEBOOK_GOLDEN_DATASET_VERSION,
        package_sha256=package,
        authority_sha256=authority,
        fixture_recipe_sha256=recipe,
        entities=entities,
        cases=cases,
    )


def _metric_result(
    metric: NotebookMetric,
    eligible: list[NotebookGoldenCase],
    rows: dict[str, NotebookReviewedRow],
    success: dict[tuple[str, NotebookMetric], bool],
) -> NotebookMetricResult:
    evaluated = [
        case
        for case in eligible
        if rows[case.case_id].availability == NotebookReviewAvailability.AVAILABLE
    ]
    numerator = sum(success[(case.case_id, metric)] for case in evaluated)
    denominator = len(eligible)
    evaluated_denominator = len(evaluated)
    status = (
        NotebookMetricStatus.UNAVAILABLE
        if evaluated_denominator == 0
        else NotebookMetricStatus.AVAILABLE
        if evaluated_denominator == denominator
        else NotebookMetricStatus.PROVISIONAL
    )
    return NotebookMetricResult(
        metric=metric,
        status=status,
        numerator=numerator,
        eligible_denominator=denominator,
        evaluated_denominator=evaluated_denominator,
        unavailable_count=denominator - evaluated_denominator,
        value=None if denominator == 0 else numerator / denominator,
    )


def evaluate_reviewed_notebook_retrieval(
    rows: tuple[NotebookReviewedRow, ...],
    *,
    dataset: NotebookGoldenDataset | None = None,
    case_membership: tuple[str, ...] | None = None,
) -> NotebookEvaluationReport:
    released = dataset or load_notebook_golden_v1()
    canonical_membership = tuple(case.case_id for case in released.cases)
    if case_membership is not None and case_membership != canonical_membership:
        raise NotebookGoldenError("case membership must be None or the exact released order")
    if len(rows) != NOTEBOOK_GOLDEN_CASE_COUNT:
        raise NotebookGoldenError("reviewed rows must cover all 40 released cases")
    by_case = {row.case_id: row for row in rows}
    if len(by_case) != len(rows) or tuple(row.case_id for row in rows) != canonical_membership:
        raise NotebookGoldenError("reviewed row membership/order is not canonical")
    entity_map = {item.entity_id: item for item in released.entities}
    for row in rows:
        for candidate in row.candidates:
            entity = entity_map.get(candidate.entity_id)
            if entity is None:
                raise NotebookGoldenError("candidate is outside released entity authority")
            if candidate.locator != entity.locator:
                raise NotebookGoldenError("candidate locator differs from released authority")

    success: dict[tuple[str, NotebookMetric], bool] = {}
    for case in released.cases:
        row = by_case[case.case_id]
        candidate_ids = tuple(item.entity_id for item in row.candidates)
        positions = {entity_id: index for index, entity_id in enumerate(candidate_ids)}
        positives_found = all(entity_id in positions for entity_id in case.expected_entity_ids)
        in_expected_order = all(
            positions[left] < positions[right]
            for left, right in zip(
                case.expected_entity_ids,
                case.expected_entity_ids[1:],
                strict=False,
            )
            if left in positions and right in positions
        )
        no_hard_negative = not set(candidate_ids) & set(case.hard_negative_entity_ids)
        for metric in case.eligible_metrics:
            if metric == NotebookMetric.HARD_NEGATIVE_AVOIDANCE:
                result = no_hard_negative
            elif metric == NotebookMetric.UNANSWERABLE_ACCURACY:
                result = case.expected_empty and not candidate_ids
            elif metric in {
                NotebookMetric.EXECUTION_ORDER_ACCURACY,
                NotebookMetric.PRODUCER_OUTPUT_PATH_RECALL,
                NotebookMetric.CELL_MATCH_ACCURACY,
                NotebookMetric.REPRODUCTION_ROLE_COVERAGE,
            }:
                result = positives_found and in_expected_order
            else:
                result = positives_found
            success[(case.case_id, metric)] = result

    eligible_by_metric: dict[NotebookMetric, list[NotebookGoldenCase]] = defaultdict(list)
    for case in released.cases:
        for metric in case.eligible_metrics:
            eligible_by_metric[metric].append(case)
    metrics = tuple(
        _metric_result(metric, eligible_by_metric[metric], by_case, success)
        for metric in NotebookMetric
    )
    slices: list[NotebookSliceResult] = []
    for slice_name in NotebookGoldenSlice:
        eligible = [case for case in released.cases if case.slice == slice_name]
        evaluated = [
            case
            for case in eligible
            if by_case[case.case_id].availability == NotebookReviewAvailability.AVAILABLE
        ]
        numerator = sum(
            success[
                (
                    case.case_id,
                    NotebookMetric.UNANSWERABLE_ACCURACY
                    if case.expected_empty
                    else NotebookMetric.RETRIEVAL_RECALL_AT_10,
                )
            ]
            for case in evaluated
        )
        status = (
            NotebookMetricStatus.UNAVAILABLE
            if not evaluated
            else NotebookMetricStatus.AVAILABLE
            if len(evaluated) == len(eligible)
            else NotebookMetricStatus.PROVISIONAL
        )
        slices.append(
            NotebookSliceResult(
                slice=slice_name,
                status=status,
                numerator=numerator,
                eligible_denominator=len(eligible),
                evaluated_denominator=len(evaluated),
                unavailable_count=len(eligible) - len(evaluated),
                value=numerator / len(eligible),
            )
        )
    return NotebookEvaluationReport(
        dataset_id=released.dataset_id,
        dataset_version=released.dataset_version,
        package_sha256=released.package_sha256,
        authority_sha256=released.authority_sha256,
        case_count=len(released.cases),
        metrics=metrics,
        slices=tuple(slices),
        hard_negative_case_count=sum(
            bool(case.hard_negative_entity_ids) for case in released.cases
        ),
        reviewed_row_count=len(rows),
    )


def perfect_reviewed_rows_v1(
    dataset: NotebookGoldenDataset | None = None,
) -> tuple[NotebookReviewedRow, ...]:
    released = dataset or load_notebook_golden_v1()
    entity_map = {item.entity_id: item for item in released.entities}
    return tuple(
        NotebookReviewedRow(
            case_id=case.case_id,
            availability=NotebookReviewAvailability.AVAILABLE,
            candidates=tuple(
                NotebookReviewedCandidate(
                    entity_id=entity_id,
                    locator=entity_map[entity_id].locator,
                )
                for entity_id in case.expected_entity_ids
            ),
        )
        for case in released.cases
    )


def probe_notebook_foundation_v1(isolated_root: Path) -> NotebookFoundationProbe:
    released = load_notebook_golden_v1()
    fixture = build_notebook_fixture_v1()
    store = NotebookV2Store(
        isolated_root / "notebook-foundation-v2.sqlite3",
        isolated_root=isolated_root,
    )
    store.initialize()
    for publication in fixture.publications:
        store.publish(publication)
    counts = store.table_counts()
    rendered = "\n".join(publication.model_dump_json() for publication in fixture.publications)
    leakage = sum(
        marker in rendered
        for marker in (
            "/Users/example/private",
            "not-a-real-image",
            "<script>",
            "steal()",
        )
    )
    perfect = evaluate_reviewed_notebook_retrieval(
        perfect_reviewed_rows_v1(released),
        dataset=released,
    )
    if any(metric.status != NotebookMetricStatus.AVAILABLE for metric in perfect.metrics):
        raise NotebookGoldenError("perfect reviewed fixture did not produce available metrics")
    return NotebookFoundationProbe(
        status="FOUNDATION_READY_NON_QUALIFIED",
        package_sha256=released.package_sha256,
        authority_sha256=released.authority_sha256,
        fixture_recipe_sha256=released.fixture_recipe_sha256,
        case_count=len(released.cases),
        slice_counts={key.value: value for key, value in EXPECTED_NOTEBOOK_SLICE_COUNTS.items()},
        publication_count=counts["notebook_publications_v2"],
        template_count=counts["notebook_templates_v2"],
        revision_count=counts["notebook_revisions"],
        execution_count=counts["notebook_executions"],
        cell_version_count=counts["notebook_cell_versions"],
        cell_execution_count=counts["notebook_cell_executions"],
        parameter_count=counts["notebook_parameters"],
        artifact_count=counts["notebook_artifacts"],
        database_sidecars=store._sidecars_present(),
        secret_or_path_leakage=leakage,
        baseline_run_created=False,
        baseline_metrics_created=False,
    )


__all__ = [
    "EXPECTED_NOTEBOOK_SLICE_COUNTS",
    "NOTEBOOK_GOLDEN_AUTHORITY_SHA256",
    "NOTEBOOK_GOLDEN_CASE_COUNT",
    "NOTEBOOK_GOLDEN_PACKAGE_SHA256",
    "NotebookEvaluationReport",
    "NotebookFoundationProbe",
    "NotebookGoldenCase",
    "NotebookGoldenDataset",
    "NotebookGoldenError",
    "NotebookGoldenSlice",
    "NotebookMetric",
    "NotebookMetricResult",
    "NotebookMetricStatus",
    "NotebookReviewAvailability",
    "NotebookReviewedCandidate",
    "NotebookReviewedRow",
    "NotebookSliceResult",
    "evaluate_reviewed_notebook_retrieval",
    "load_notebook_golden_v1",
    "perfect_reviewed_rows_v1",
    "probe_notebook_foundation_v1",
]
