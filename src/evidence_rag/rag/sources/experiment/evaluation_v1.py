"""Released Experiment Golden-v1 contracts and deterministic offline evaluator.

This module owns only E0-01 foundation authority.  It does not retrieve,
connect to SQLite, create an Evaluation Run, execute E-B0, or qualify a
baseline.  Loader and evaluator inputs are strict, frozen, content-addressed,
and fail closed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self
from urllib.parse import unquote

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from .fixture_v1 import (
    EXPERIMENT_FIXTURE_ACL_REF,
    EXPERIMENT_FIXTURE_EXPERIMENT_ID,
    ExperimentFixtureRecipe,
    FixtureMetric,
    FixtureMetricPoint,
    deterministic_experiment_recipe,
)

EXPERIMENT_GOLDEN_DATASET_ID = "experiment-golden-v1"
EXPERIMENT_GOLDEN_DATASET_VERSION = "experiment-golden-v1"
EXPERIMENT_GOLDEN_SCHEMA_VERSION = "experiment-evaluation-foundation-v1"
EXPERIMENT_GOLDEN_CASE_COUNT = 45
EXPERIMENT_GOLDEN_MANIFEST = "manifest.json"
EXPERIMENT_GOLDEN_CASES = "cases.jsonl"
EXPERIMENT_GOLDEN_RECIPE = "fixture_recipe.json"

EXPERIMENT_GOLDEN_AUTHORITY_HASH = (
    "sha256:cad053ebb0686c6a413a743fe9d0e142f2d61546cb18a2bb0d2a9b82af8d29e1"
)
EXPERIMENT_GOLDEN_PACKAGE_HASH = (
    "sha256:e7de1be4caf88af99c3b36de7e13c4a2ba3d15d4e40e5e5e0459ecf3d583a81f"
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WINDOWS_ABSOLUTE_RE = re.compile(r"(?i)(?:^|[\s\"'(])(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])")
_POSIX_ABSOLUTE_RE = re.compile(
    r"(?:^|[\s\"'(])/(?:Users|home|private|tmp|var|opt|etc|root|mnt|Volumes)(?:/|$)"
)
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_PAYMENT_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_CREDENTIAL_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s\"']{8,})|"
    r"SECRET_SENTINEL|EXPERIMENT_FIXTURE_SECRET)"
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _validate_text(value: str) -> str:
    if (
        unicodedata.normalize("NFC", value) != value
        or value != value.strip()
        or _CONTROL_RE.search(value)
    ):
        raise ValueError("text must be NFC, trimmed, and free of control characters")
    return value


def _validate_identifier(value: str) -> str:
    value = _validate_text(value)
    if _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError("identifier is not canonical")
    return value


def _validate_sha256(value: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("expected sha256:<lowercase-hex>")
    return value


def _safe_relative_path(value: str) -> str:
    value = _validate_text(value)
    path = PurePosixPath(value)
    if (
        "\\" in value
        or path.is_absolute()
        or not path.parts
        or "." in path.parts
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise ValueError("file path must be canonical and package relative")
    if value.endswith(("-wal", "-shm", ".sqlite3", ".pyc")) or "__pycache__" in path.parts:
        raise ValueError("mutable database or compiled files are forbidden")
    return value


Identifier = Annotated[StrictStr, AfterValidator(_validate_identifier)]
Sha256 = Annotated[StrictStr, AfterValidator(_validate_sha256)]
SafeRelativePath = Annotated[StrictStr, AfterValidator(_safe_relative_path)]
ContractText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=8_000),
    AfterValidator(_validate_text),
]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
Scalar = StrictBool | StrictInt | StrictFloat | StrictStr


class ExperimentEvaluationError(ValueError):
    """Fail-closed package, authority, reviewed-row, or evaluation error."""


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))

    def canonical_sha256(self) -> str:
        return _sha256(self.canonical_json_bytes())

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        del deep
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(self, **_: Any) -> Self:
        raise TypeError("deprecated copy() is disabled; use validated model_copy()")


class ExperimentGoldenSlice(StrEnum):
    EXACT = "exact"
    STRUCTURED_FILTER = "structured_filter"
    METRIC_NUMERIC = "metric_numeric"
    COMPARISON = "comparison"
    REPRODUCTION = "reproduction"
    FAILURE_STATUS = "failure_status"
    AGGREGATION_TREND = "aggregation_trend"
    UNANSWERABLE_INCOMPARABLE_ACL = "unanswerable_incomparable_acl"


EXPECTED_SLICE_COUNTS: Mapping[ExperimentGoldenSlice, int] = {
    ExperimentGoldenSlice.EXACT: 5,
    ExperimentGoldenSlice.STRUCTURED_FILTER: 7,
    ExperimentGoldenSlice.METRIC_NUMERIC: 8,
    ExperimentGoldenSlice.COMPARISON: 8,
    ExperimentGoldenSlice.REPRODUCTION: 6,
    ExperimentGoldenSlice.FAILURE_STATUS: 4,
    ExperimentGoldenSlice.AGGREGATION_TREND: 4,
    ExperimentGoldenSlice.UNANSWERABLE_INCOMPARABLE_ACL: 3,
}


class Answerability(StrEnum):
    ANSWERABLE = "answerable"
    UNANSWERABLE = "unanswerable"
    ACL_DENIED = "acl_denied"
    INSUFFICIENT_METADATA = "insufficient_metadata"


class EntityKind(StrEnum):
    EXPERIMENT = "experiment"
    RUN = "run"
    METRIC_DEFINITION = "metric_definition"
    METRIC_SERIES = "metric_series"
    METRIC_OBSERVATION = "metric_observation"
    ARTIFACT = "artifact"


class PredicateOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    MAX = "max"
    MIN = "min"
    TOP = "top"
    BOTTOM = "bottom"
    LATEST = "latest"
    FINAL = "final"
    BEST = "best"
    MEAN = "mean"
    MEDIAN = "median"
    STD = "std"
    COUNT = "count"
    DELTA = "delta"
    RELATIVE_DELTA = "relative_delta"


class PredicateValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"


class MetricDirection(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"
    UNKNOWN = "unknown"


class ComparabilityState(StrEnum):
    COMPARABLE = "comparable"
    COMPARABLE_WITH_CAVEATS = "comparable_with_caveats"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_METADATA = "insufficient_metadata"


class DifferenceRole(StrEnum):
    CONTROLLED = "controlled"
    TREATMENT = "treatment"
    UNKNOWN = "unknown"
    DISQUALIFYING = "disqualifying"


class ReproductionRoleName(StrEnum):
    COMMAND = "command"
    CODE_COMMIT = "code_commit"
    DATASET_VERSION = "dataset_version"
    CONFIG_SNAPSHOT = "config_snapshot"
    ENVIRONMENT_SNAPSHOT = "environment_snapshot"
    SEED = "seed"
    REQUIRED_ARTIFACTS = "required_artifacts"


class ReproductionRoleState(StrEnum):
    PRESENT_VERIFIED = "present_verified"
    PRESENT_UNVERIFIED = "present_unverified"
    MISSING = "missing"
    INACCESSIBLE = "inaccessible"


class HardNegativeReason(StrEnum):
    WRONG_RUN = "wrong_run"
    WRONG_DATASET = "wrong_dataset"
    WRONG_UNIT = "wrong_unit"
    FAILED_HIGH_SCORE = "failed_high_score"
    RUNNING_INELIGIBLE = "running_ineligible"
    STRING_NUMERIC_CONFUSION = "string_numeric_confusion"
    UNKNOWN_DIRECTION = "unknown_direction"
    URI_NOT_VERIFIED = "uri_not_verified"
    ACL_SCOPE = "acl_scope"
    MISSING_IS_NOT_ZERO = "missing_is_not_zero"


class ExperimentEvaluationMetric(StrEnum):
    EXACT_ENTITY_ACCURACY = "exact_entity_accuracy"
    PREDICATE_ACCURACY = "predicate_accuracy"
    NUMERIC_UNIT_DIRECTION_ACCURACY = "numeric_unit_direction_accuracy"
    COMPARABILITY_ACCURACY = "comparability_accuracy"
    REPRODUCTION_ACCURACY = "reproduction_accuracy"
    HARD_NEGATIVE_AVOIDANCE = "hard_negative_avoidance"
    UNANSWERABLE_ACCURACY = "unanswerable_accuracy"
    ZERO_RESULT_ACCURACY = "zero_result_accuracy"


class EvaluationTriState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class ReviewedOutcome(StrEnum):
    RETURNED = "returned"
    ZERO_RESULT = "zero_result"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ReviewedReason(StrEnum):
    UNSUPPORTED_V1 = "unsupported_v1"
    SYSTEM_ERROR = "system_error"
    ACL_DENIED = "acl_denied"
    MISSING_METADATA = "missing_metadata"
    NO_MATCH = "no_match"


class TypedPredicate(_FrozenContract):
    predicate_id: Identifier
    field: ContractText
    operator: PredicateOperator
    value_type: PredicateValueType
    value: Scalar | tuple[StrictStr, ...]
    unit: Literal["ratio", "percent", "ms", "score"] | None = None
    conversion_policy: Literal["none", "ratio_percent_explicit", "ms_seconds_explicit"] = "none"

    @model_validator(mode="after")
    def _value_matches_type(self) -> Self:
        expected: dict[PredicateValueType, type[Any]] = {
            PredicateValueType.STRING: str,
            PredicateValueType.INTEGER: int,
            PredicateValueType.FLOAT: float,
            PredicateValueType.BOOLEAN: bool,
            PredicateValueType.STRING_LIST: tuple,
        }
        if type(self.value) is not expected[self.value_type]:
            raise ValueError("typed predicate value does not match value_type")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("predicate float must be finite")
        if isinstance(self.value, tuple) and (
            not self.value or len(self.value) != len(set(self.value))
        ):
            raise ValueError("string-list predicate must be non-empty and unique")
        if self.conversion_policy == "ratio_percent_explicit" and self.unit not in {
            "ratio",
            "percent",
        }:
            raise ValueError("ratio/percent conversion requires a compatible unit")
        return self


class ExpectedEntity(_FrozenContract):
    entity_id: ContractText
    kind: EntityKind
    experiment_id: ContractText
    run_id: ContractText | None = None
    locator: ContractText
    external_id: Identifier | None = None
    display_key: Identifier | None = None
    acl_ref: ContractText

    @model_validator(mode="after")
    def _locator_matches_identity(self) -> Self:
        if self.experiment_id != EXPERIMENT_FIXTURE_EXPERIMENT_ID:
            raise ValueError("entity crosses the released logical Experiment")
        if self.kind == EntityKind.EXPERIMENT:
            if self.entity_id != self.experiment_id or self.run_id is not None:
                raise ValueError("Experiment entity identity mismatch")
            if self.locator != self.experiment_id:
                raise ValueError("Experiment locator mismatch")
        elif self.kind == EntityKind.METRIC_DEFINITION:
            if self.run_id is not None:
                raise ValueError("MetricDefinition cannot be scoped to one Run")
            if not self.entity_id.startswith("metric-definition://fixture/"):
                raise ValueError("MetricDefinition identity mismatch")
            name = self.entity_id.removeprefix("metric-definition://fixture/")
            if self.locator != f"{self.experiment_id}/metric-definitions/{name}":
                raise ValueError("MetricDefinition locator mismatch")
        else:
            if self.run_id is None or not self.run_id.startswith("run://fixture/"):
                raise ValueError("run-scoped entity requires a fixture run_id")
            run_slug = self.run_id.removeprefix("run://fixture/")
            prefix = f"{self.experiment_id}/runs/{run_slug}"
            if self.kind == EntityKind.RUN:
                if self.entity_id != self.run_id or self.locator != prefix:
                    raise ValueError("Run identity/locator mismatch")
            elif self.kind == EntityKind.METRIC_SERIES:
                identity_prefix = f"metric-series://fixture/{run_slug}/"
                if not self.entity_id.startswith(identity_prefix) or not self.entity_id.endswith(
                    "/test"
                ):
                    raise ValueError("MetricSeries identity mismatch")
                metric_name = self.entity_id.removeprefix(identity_prefix).removesuffix("/test")
                if self.locator != f"{prefix}/metric-series/{metric_name}/test":
                    raise ValueError("MetricSeries locator crosses its declared run")
            elif self.kind == EntityKind.METRIC_OBSERVATION:
                identity_prefix = f"metric-observation://fixture/{run_slug}/"
                if not self.entity_id.startswith(identity_prefix):
                    raise ValueError("Metric observation identity mismatch")
                suffix = self.entity_id.removeprefix(identity_prefix)
                parts = suffix.split("/")
                if (
                    len(parts) != 4
                    or parts[1] != "test"
                    or parts[2] != "step"
                    or not parts[3].isascii()
                    or not parts[3].isdigit()
                ):
                    raise ValueError("MetricObservation canonical identity mismatch")
                metric_name = parts[0]
                step = parts[3]
                expected = f"{prefix}/metric-series/{metric_name}/test/observations/step/{step}"
                if self.locator != expected:
                    raise ValueError("Metric locator crosses its declared run")
            elif self.kind == EntityKind.ARTIFACT:
                if not self.entity_id.startswith("artifact://fixture/"):
                    raise ValueError("Artifact identity mismatch")
                if not self.locator.startswith(prefix + "/artifacts/"):
                    raise ValueError("Artifact locator crosses its declared run")
        return self


class MetricTruth(_FrozenContract):
    run_id: ContractText
    definition_id: ContractText
    series_id: ContractText | None
    observation_ids: tuple[ContractText, ...]
    observation_locators: tuple[ContractText, ...]
    metric_name: Identifier
    raw_value: StrictFloat | None
    canonical_value: StrictFloat | None
    raw_unit: Literal["ratio", "percent", "ms", "score"]
    canonical_unit: Literal["ratio", "percent", "ms", "score"]
    direction: MetricDirection
    split: Literal["test"]
    step: StrictInt | None
    observed_at_ms: tuple[PositiveInt, ...] = ()
    steps: tuple[PositiveInt, ...] = ()
    raw_series: tuple[StrictFloat, ...] = ()
    canonical_series: tuple[StrictFloat, ...] = ()
    aggregation: Literal["raw", "final", "mean", "trend", "missing"] = "raw"
    missing: StrictBool = False

    @model_validator(mode="after")
    def _numeric_truth_is_explicit(self) -> Self:
        values = tuple(
            value
            for value in (
                self.raw_value,
                self.canonical_value,
                *self.raw_series,
                *self.canonical_series,
            )
            if value is not None
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("metric truth must be finite")
        if self.missing:
            if (
                self.raw_value is not None
                or self.canonical_value is not None
                or self.series_id is not None
                or self.observation_ids
                or self.observation_locators
                or self.observed_at_ms
                or self.steps
                or self.raw_series
                or self.canonical_series
            ):
                raise ValueError("missing metric cannot be encoded as numeric zero")
            if self.aggregation != "missing":
                raise ValueError("missing metric requires missing aggregation")
        else:
            lengths = {
                len(self.observation_ids),
                len(self.observation_locators),
                len(self.observed_at_ms),
                len(self.steps),
                len(self.raw_series),
                len(self.canonical_series),
            }
            if (
                self.raw_value is None
                or self.canonical_value is None
                or self.series_id is None
                or lengths == {0}
                or len(lengths) != 1
            ):
                raise ValueError("present metric requires complete observation authority")
            if self.steps != tuple(range(1, len(self.steps) + 1)):
                raise ValueError("MetricTruth steps must be contiguous and ordered")
            if self.observed_at_ms != tuple(sorted(self.observed_at_ms)) or len(
                self.observed_at_ms
            ) != len(set(self.observed_at_ms)):
                raise ValueError("MetricTruth times must be unique and ordered")
            if self.step != self.steps[-1]:
                raise ValueError("MetricTruth final step mismatch")
        if self.raw_unit != self.canonical_unit and {
            self.raw_unit,
            self.canonical_unit,
        } != {"ratio", "percent"}:
            raise ValueError("only explicit ratio/percent normalization is released")
        return self


class ComparabilityDifference(_FrozenContract):
    field: ContractText
    role: DifferenceRole
    baseline: Scalar | None
    candidate: Scalar | None
    reason: ContractText


class ComparabilityTruth(_FrozenContract):
    state: ComparabilityState
    baseline_run_id: ContractText
    candidate_run_ids: tuple[ContractText, ...]
    differences: tuple[ComparabilityDifference, ...]
    missing_fields: tuple[ContractText, ...] = ()

    @model_validator(mode="after")
    def _decision_has_reason(self) -> Self:
        if not self.candidate_run_ids or len(self.candidate_run_ids) != len(
            set(self.candidate_run_ids)
        ):
            raise ValueError("comparison requires unique candidate runs")
        if self.state == ComparabilityState.INSUFFICIENT_METADATA and not self.missing_fields:
            raise ValueError("insufficient comparison requires missing_fields")
        if self.state == ComparabilityState.NOT_COMPARABLE and not any(
            item.role == DifferenceRole.DISQUALIFYING for item in self.differences
        ):
            raise ValueError("not_comparable requires a disqualifying difference")
        return self


class ReproductionRole(_FrozenContract):
    role: ReproductionRoleName
    state: ReproductionRoleState
    locator: ContractText | None
    reason: ContractText | None = None

    @model_validator(mode="after")
    def _role_evidence_is_explicit(self) -> Self:
        if self.state in {
            ReproductionRoleState.MISSING,
            ReproductionRoleState.INACCESSIBLE,
        }:
            if self.locator is not None or self.reason is None:
                raise ValueError("missing/inaccessible role requires reason and no locator")
        elif self.locator is None:
            raise ValueError("present reproduction role requires locator")
        return self


class ReproductionTruth(_FrozenContract):
    run_id: ContractText
    roles: tuple[ReproductionRole, ...]
    missing_roles: tuple[ReproductionRoleName, ...]

    @model_validator(mode="after")
    def _role_set_is_complete(self) -> Self:
        if tuple(role.role for role in self.roles) != tuple(ReproductionRoleName):
            raise ValueError("reproduction roles must be complete and canonically ordered")
        missing = tuple(
            role.role
            for role in self.roles
            if role.state in {ReproductionRoleState.MISSING, ReproductionRoleState.INACCESSIBLE}
        )
        if self.missing_roles != missing:
            raise ValueError("missing reproduction roles mismatch")
        return self


class HardNegative(_FrozenContract):
    entity: ExpectedEntity
    reason: HardNegativeReason


class ExperimentGoldenCase(_FrozenContract):
    case_id: Identifier
    primary_slice: ExperimentGoldenSlice
    question: ContractText
    task: Identifier
    answerability: Answerability
    experiment_id: Literal["experiment://fixture/experiment-v1-main"] = (
        EXPERIMENT_FIXTURE_EXPERIMENT_ID
    )
    acl_ref: Literal["project:project-experiment-fixture-v1"] = EXPERIMENT_FIXTURE_ACL_REF
    expected_entities: tuple[ExpectedEntity, ...]
    predicate: TypedPredicate
    metric_truth: tuple[MetricTruth, ...] = ()
    comparability_truth: ComparabilityTruth | None = None
    reproduction_truth: ReproductionTruth | None = None
    hard_negatives: tuple[HardNegative, ...]
    forbidden_candidate_ids: tuple[ContractText, ...]
    acceptable_alternative_ids: tuple[ContractText, ...] = ()
    expected_candidate_order: tuple[ContractText, ...]
    candidate_order_rule: Literal["expected_then_declared_alternatives"] = (
        "expected_then_declared_alternatives"
    )
    eligible_metrics: tuple[ExperimentEvaluationMetric, ...]
    expects_zero_result: StrictBool = False
    unavailable_reason: ReviewedReason | None = None

    @model_validator(mode="after")
    def _case_truth_is_closed(self) -> Self:
        if not self.hard_negatives:
            raise ValueError("every case requires a hard negative")
        negative_ids = tuple(item.entity.entity_id for item in self.hard_negatives)
        if len(negative_ids) != len(set(negative_ids)):
            raise ValueError("duplicate hard-negative membership")
        if self.forbidden_candidate_ids != negative_ids:
            raise ValueError("forbidden candidates must equal hard-negative membership")
        positive_ids = tuple(item.entity_id for item in self.expected_entities)
        if len(positive_ids) != len(set(positive_ids)):
            raise ValueError("duplicate expected entity")
        if self.expected_candidate_order != positive_ids:
            raise ValueError("expected candidate rank/order authority mismatch")
        if len(self.acceptable_alternative_ids) != len(set(self.acceptable_alternative_ids)):
            raise ValueError("duplicate acceptable alternative")
        if set(positive_ids) & set(negative_ids):
            raise ValueError("positive and hard-negative identities overlap")
        if set(self.acceptable_alternative_ids) & (set(positive_ids) | set(negative_ids)):
            raise ValueError("acceptable alternatives overlap positive/hard-negative truth")
        if len(self.eligible_metrics) != len(set(self.eligible_metrics)):
            raise ValueError("duplicate eligible metric")
        for entity in (*self.expected_entities, *(item.entity for item in self.hard_negatives)):
            if entity.experiment_id != self.experiment_id or entity.acl_ref != self.acl_ref:
                raise ValueError("case entity crosses Experiment or ACL scope")
        if (
            self.answerability in {Answerability.UNANSWERABLE, Answerability.ACL_DENIED}
            and self.expected_entities
        ):
            raise ValueError("unanswerable/ACL case cannot release hidden positive identities")
        if self.expects_zero_result and self.answerability == Answerability.ANSWERABLE:
            raise ValueError("answerable case cannot require a zero result")
        if (
            self.primary_slice == ExperimentGoldenSlice.COMPARISON
            and self.comparability_truth is None
        ):
            raise ValueError("comparison case requires comparability truth")
        if (
            self.primary_slice == ExperimentGoldenSlice.REPRODUCTION
            and self.reproduction_truth is None
        ):
            raise ValueError("reproduction case requires role truth")
        return self


def _run_slug(run_id: str) -> str:
    return run_id.removeprefix("run://fixture/")


def _display_key(external_id: str) -> str:
    return "run-fixture-" + external_id.rsplit("-", 1)[-1]


def _run_entity(recipe: ExperimentFixtureRecipe, run_id: str) -> ExpectedEntity:
    run = next(item for item in recipe.runs if item.logical_run_id == run_id)
    slug = _run_slug(run_id)
    return ExpectedEntity(
        entity_id=run_id,
        kind=EntityKind.RUN,
        experiment_id=recipe.logical_experiment_id,
        run_id=run_id,
        locator=f"{recipe.logical_experiment_id}/runs/{slug}",
        external_id=run.external_id,
        display_key=_display_key(run.external_id),
        acl_ref=recipe.acl_ref,
    )


def _experiment_entity(recipe: ExperimentFixtureRecipe) -> ExpectedEntity:
    return ExpectedEntity(
        entity_id=recipe.logical_experiment_id,
        kind=EntityKind.EXPERIMENT,
        experiment_id=recipe.logical_experiment_id,
        locator=recipe.logical_experiment_id,
        acl_ref=recipe.acl_ref,
    )


def _metric_entity(
    recipe: ExperimentFixtureRecipe, run_id: str, metric_name: str
) -> ExpectedEntity:
    run = next(item for item in recipe.runs if item.logical_run_id == run_id)
    metric = next(item for item in run.metrics if item.name == metric_name)
    return _metric_observation_entity(recipe, metric, metric.latest)


def _metric_observation_entity(
    recipe: ExperimentFixtureRecipe,
    metric: FixtureMetric,
    point: FixtureMetricPoint,
) -> ExpectedEntity:
    run = next(item for item in recipe.runs if item.logical_run_id == metric.run_id)
    return ExpectedEntity(
        entity_id=point.observation_id,
        kind=EntityKind.METRIC_OBSERVATION,
        experiment_id=recipe.logical_experiment_id,
        run_id=metric.run_id,
        locator=point.locator,
        external_id=run.external_id,
        display_key=_display_key(run.external_id),
        acl_ref=recipe.acl_ref,
    )


def _metric_definition_entity(
    recipe: ExperimentFixtureRecipe,
    metric_name: str,
) -> ExpectedEntity:
    definition = next(item for item in recipe.metric_definitions if item.name == metric_name)
    return ExpectedEntity(
        entity_id=definition.definition_id,
        kind=EntityKind.METRIC_DEFINITION,
        experiment_id=recipe.logical_experiment_id,
        locator=definition.locator,
        acl_ref=recipe.acl_ref,
    )


def _metric_series_entity(
    recipe: ExperimentFixtureRecipe,
    run_id: str,
    metric_name: str,
) -> ExpectedEntity:
    run = next(item for item in recipe.runs if item.logical_run_id == run_id)
    metric = next(item for item in run.metrics if item.name == metric_name)
    return ExpectedEntity(
        entity_id=metric.series_id,
        kind=EntityKind.METRIC_SERIES,
        experiment_id=recipe.logical_experiment_id,
        run_id=run_id,
        locator=metric.locator,
        external_id=run.external_id,
        display_key=_display_key(run.external_id),
        acl_ref=recipe.acl_ref,
    )


def _artifact_entity(
    recipe: ExperimentFixtureRecipe, run_id: str, artifact_name: str
) -> ExpectedEntity:
    run = next(item for item in recipe.runs if item.logical_run_id == run_id)
    artifact = next(item for item in run.artifacts if item.name == artifact_name)
    slug = _run_slug(run_id)
    return ExpectedEntity(
        entity_id=artifact.artifact_id,
        kind=EntityKind.ARTIFACT,
        experiment_id=recipe.logical_experiment_id,
        run_id=run_id,
        locator=f"{recipe.logical_experiment_id}/runs/{slug}/artifacts/{artifact.name}",
        external_id=run.external_id,
        display_key=_display_key(run.external_id),
        acl_ref=recipe.acl_ref,
    )


def _metric_truth(
    recipe: ExperimentFixtureRecipe,
    run_id: str,
    metric_name: str,
    *,
    aggregation: Literal["raw", "final", "mean", "trend", "missing"] = "final",
    missing: bool = False,
) -> MetricTruth:
    run = next(item for item in recipe.runs if item.logical_run_id == run_id)
    definition = next(item for item in recipe.metric_definitions if item.name == metric_name)
    direction = MetricDirection(definition.direction)
    if missing:
        return MetricTruth(
            run_id=run_id,
            definition_id=definition.definition_id,
            series_id=None,
            observation_ids=(),
            observation_locators=(),
            metric_name=definition.name,
            raw_value=None,
            canonical_value=None,
            raw_unit=definition.canonical_unit,
            canonical_unit=definition.canonical_unit,
            direction=direction,
            split=definition.split,
            step=None,
            observed_at_ms=(),
            steps=(),
            raw_series=(),
            canonical_series=(),
            aggregation="missing",
            missing=True,
        )
    metric = next(item for item in run.metrics if item.name == metric_name)
    raw_series = tuple(point.value for point in metric.points)
    canonical_series = tuple(
        value / 100.0
        if metric.unit == "percent" and definition.canonical_unit == "ratio"
        else value
        for value in raw_series
    )
    raw_value = raw_series[-1]
    canonical_value = canonical_series[-1]
    if aggregation == "mean":
        raw_value = sum(raw_series) / len(raw_series)
        canonical_value = sum(canonical_series) / len(canonical_series)
    return MetricTruth(
        run_id=run_id,
        definition_id=definition.definition_id,
        series_id=metric.series_id,
        observation_ids=tuple(point.observation_id for point in metric.points),
        observation_locators=tuple(point.locator for point in metric.points),
        metric_name=definition.name,
        raw_value=raw_value,
        canonical_value=canonical_value,
        raw_unit=metric.unit,
        canonical_unit=definition.canonical_unit,
        direction=direction,
        split=metric.split,
        step=metric.latest.step,
        observed_at_ms=tuple(point.observed_at_ms for point in metric.points),
        steps=tuple(point.step for point in metric.points),
        raw_series=raw_series,
        canonical_series=canonical_series,
        aggregation=aggregation,
        missing=False,
    )


def _predicate(
    ordinal: int,
    field: str,
    operator: PredicateOperator,
    value_type: PredicateValueType,
    value: Scalar | tuple[str, ...],
    *,
    unit: Literal["ratio", "percent", "ms", "score"] | None = None,
    conversion_policy: Literal["none", "ratio_percent_explicit", "ms_seconds_explicit"] = "none",
) -> TypedPredicate:
    return TypedPredicate(
        predicate_id=f"predicate-{ordinal:03d}",
        field=field,
        operator=operator,
        value_type=value_type,
        value=value,
        unit=unit,
        conversion_policy=conversion_policy,
    )


def _difference(
    field: str,
    role: DifferenceRole,
    baseline: Scalar | None,
    candidate: Scalar | None,
    reason: str,
) -> ComparabilityDifference:
    return ComparabilityDifference(
        field=field,
        role=role,
        baseline=baseline,
        candidate=candidate,
        reason=reason,
    )


def _comparison(
    baseline: str,
    candidate: str,
    state: ComparabilityState,
    differences: tuple[ComparabilityDifference, ...],
    missing: tuple[str, ...] = (),
) -> ComparabilityTruth:
    return ComparabilityTruth(
        state=state,
        baseline_run_id=baseline,
        candidate_run_ids=(candidate,),
        differences=differences,
        missing_fields=missing,
    )


def _reproduction(
    recipe: ExperimentFixtureRecipe,
    run_id: str,
    *,
    artifact_state: ReproductionRoleState = ReproductionRoleState.PRESENT_VERIFIED,
) -> ReproductionTruth:
    run = next(item for item in recipe.runs if item.logical_run_id == run_id)
    prefix = f"{recipe.logical_experiment_id}/runs/{_run_slug(run_id)}"
    state_by_role: dict[ReproductionRoleName, ReproductionRoleState] = {
        ReproductionRoleName.COMMAND: (
            ReproductionRoleState.PRESENT_VERIFIED if run.command else ReproductionRoleState.MISSING
        ),
        ReproductionRoleName.CODE_COMMIT: (
            ReproductionRoleState.PRESENT_VERIFIED
            if run.commit_sha
            else ReproductionRoleState.MISSING
        ),
        ReproductionRoleName.DATASET_VERSION: ReproductionRoleState.PRESENT_VERIFIED,
        ReproductionRoleName.CONFIG_SNAPSHOT: ReproductionRoleState.PRESENT_VERIFIED,
        ReproductionRoleName.ENVIRONMENT_SNAPSHOT: (
            ReproductionRoleState.PRESENT_VERIFIED
            if run.environment
            else ReproductionRoleState.MISSING
        ),
        ReproductionRoleName.SEED: ReproductionRoleState.PRESENT_VERIFIED,
        ReproductionRoleName.REQUIRED_ARTIFACTS: (
            artifact_state if run.artifacts else ReproductionRoleState.MISSING
        ),
    }
    roles: list[ReproductionRole] = []
    for role in ReproductionRoleName:
        state = state_by_role[role]
        missing = state in {
            ReproductionRoleState.MISSING,
            ReproductionRoleState.INACCESSIBLE,
        }
        roles.append(
            ReproductionRole(
                role=role,
                state=state,
                locator=None if missing else f"{prefix}/reproduction/{role}",
                reason=f"{role} is unavailable" if missing else None,
            )
        )
    return ReproductionTruth(
        run_id=run_id,
        roles=tuple(roles),
        missing_roles=tuple(
            role.role
            for role in roles
            if role.state in {ReproductionRoleState.MISSING, ReproductionRoleState.INACCESSIBLE}
        ),
    )


def _eligible_for_slice(
    primary_slice: ExperimentGoldenSlice,
    *,
    answerability: Answerability,
) -> tuple[ExperimentEvaluationMetric, ...]:
    eligible = {ExperimentEvaluationMetric.HARD_NEGATIVE_AVOIDANCE}
    if primary_slice == ExperimentGoldenSlice.EXACT:
        eligible.update(
            {
                ExperimentEvaluationMetric.EXACT_ENTITY_ACCURACY,
                ExperimentEvaluationMetric.PREDICATE_ACCURACY,
            }
        )
    if primary_slice in {
        ExperimentGoldenSlice.STRUCTURED_FILTER,
        ExperimentGoldenSlice.METRIC_NUMERIC,
        ExperimentGoldenSlice.FAILURE_STATUS,
        ExperimentGoldenSlice.AGGREGATION_TREND,
    }:
        eligible.add(ExperimentEvaluationMetric.PREDICATE_ACCURACY)
    if primary_slice in {
        ExperimentGoldenSlice.METRIC_NUMERIC,
        ExperimentGoldenSlice.COMPARISON,
        ExperimentGoldenSlice.AGGREGATION_TREND,
    }:
        eligible.add(ExperimentEvaluationMetric.NUMERIC_UNIT_DIRECTION_ACCURACY)
    if primary_slice == ExperimentGoldenSlice.COMPARISON:
        eligible.add(ExperimentEvaluationMetric.COMPARABILITY_ACCURACY)
    if primary_slice == ExperimentGoldenSlice.REPRODUCTION:
        eligible.add(ExperimentEvaluationMetric.REPRODUCTION_ACCURACY)
    if primary_slice == ExperimentGoldenSlice.UNANSWERABLE_INCOMPARABLE_ACL:
        eligible.update(
            {
                ExperimentEvaluationMetric.UNANSWERABLE_ACCURACY,
                ExperimentEvaluationMetric.ZERO_RESULT_ACCURACY,
            }
        )
    if answerability != Answerability.ANSWERABLE:
        eligible.add(ExperimentEvaluationMetric.UNANSWERABLE_ACCURACY)
    return tuple(metric for metric in ExperimentEvaluationMetric if metric in eligible)


def build_experiment_golden_cases(
    recipe: ExperimentFixtureRecipe | None = None,
) -> tuple[ExperimentGoldenCase, ...]:
    """Build the canonical 45 reviewed case contracts from the frozen recipe."""

    frozen = recipe or deterministic_experiment_recipe()
    run_ids = {run.name: run.logical_run_id for run in frozen.runs}
    baseline11 = run_ids["baseline-seed-11"]
    baseline22 = run_ids["baseline-seed-22"]
    baseline33 = run_ids["baseline-seed-33"]
    treatment11 = run_ids["treatment-seed-11"]
    treatment22 = run_ids["treatment-seed-22"]
    treatment33 = run_ids["treatment-seed-33"]
    dataset_mismatch = run_ids["dataset-v2-mismatch"]
    unit_mismatch = run_ids["percent-unit-mismatch"]
    failed = run_ids["failed-high-score"]
    running = run_ids["running-candidate"]
    missing = run_ids["missing-commit-environment"]
    typed_string = run_ids["typed-config-string-one"]

    cases: list[ExperimentGoldenCase] = []

    def add(
        primary_slice: ExperimentGoldenSlice,
        question: str,
        task: str,
        expected: tuple[ExpectedEntity, ...],
        predicate: TypedPredicate,
        *,
        answerability: Answerability = Answerability.ANSWERABLE,
        metric_truth: tuple[MetricTruth, ...] = (),
        comparison: ComparabilityTruth | None = None,
        reproduction: ReproductionTruth | None = None,
        negative: ExpectedEntity | None = None,
        negative_reason: HardNegativeReason = HardNegativeReason.WRONG_RUN,
        acceptable: tuple[str, ...] = (),
        expects_zero: bool = False,
        unavailable_reason: ReviewedReason | None = None,
    ) -> None:
        ordinal = len(cases) + 1
        decoy = negative or _run_entity(
            frozen,
            running if all(item.entity_id != running for item in expected) else failed,
        )
        hard = HardNegative(entity=decoy, reason=negative_reason)
        eligible = list(
            _eligible_for_slice(
                primary_slice,
                answerability=answerability,
            )
        )
        if (
            comparison is not None
            and ExperimentEvaluationMetric.COMPARABILITY_ACCURACY not in eligible
        ):
            eligible.append(ExperimentEvaluationMetric.COMPARABILITY_ACCURACY)
        if not expects_zero and ExperimentEvaluationMetric.ZERO_RESULT_ACCURACY in eligible:
            eligible.remove(ExperimentEvaluationMetric.ZERO_RESULT_ACCURACY)
        eligible = [metric for metric in ExperimentEvaluationMetric if metric in eligible]
        cases.append(
            ExperimentGoldenCase(
                case_id=f"experiment-v1-{ordinal:03d}",
                primary_slice=primary_slice,
                question=question,
                task=task,
                answerability=answerability,
                expected_entities=expected,
                predicate=predicate,
                metric_truth=metric_truth,
                comparability_truth=comparison,
                reproduction_truth=reproduction,
                hard_negatives=(hard,),
                forbidden_candidate_ids=(hard.entity.entity_id,),
                acceptable_alternative_ids=acceptable,
                expected_candidate_order=tuple(entity.entity_id for entity in expected),
                eligible_metrics=tuple(eligible),
                expects_zero_result=expects_zero,
                unavailable_reason=unavailable_reason,
            )
        )

    # 1-5: exact Experiment/Run/Metric identities.
    add(
        ExperimentGoldenSlice.EXACT,
        "Locate the frozen Experiment by its canonical logical ID.",
        "exact_experiment",
        (_experiment_entity(frozen),),
        _predicate(
            1,
            "experiment_id",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            frozen.logical_experiment_id,
        ),
    )
    for ordinal, run_id, label in (
        (2, baseline11, "baseline seed 11"),
        (3, treatment22, "treatment seed 22"),
        (4, failed, "failed high-score"),
    ):
        entity = _run_entity(frozen, run_id)
        add(
            ExperimentGoldenSlice.EXACT,
            f"Locate the {label} Run by external ID {entity.external_id}.",
            "exact_run",
            (entity,),
            _predicate(
                ordinal,
                "run.external_id",
                PredicateOperator.EQ,
                PredicateValueType.STRING,
                entity.external_id or "",
            ),
        )
    add(
        ExperimentGoldenSlice.EXACT,
        "Locate the final test nDCG observation for treatment seed 11.",
        "exact_metric_observation",
        (_metric_entity(frozen, treatment11, "ndcg_at_10"),),
        _predicate(
            5,
            "metric.observation_id",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            _metric_truth(frozen, treatment11, "ndcg_at_10").observation_ids[-1],
        ),
        metric_truth=(_metric_truth(frozen, treatment11, "ndcg_at_10"),),
        acceptable=(_metric_series_entity(frozen, treatment11, "ndcg_at_10").entity_id,),
    )

    # 6-12: typed structured conditions.
    structured_specs = (
        (
            "Find baseline Runs with seed 11.",
            "run.group",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "baseline",
            (baseline11,),
            running,
            HardNegativeReason.WRONG_RUN,
        ),
        (
            "Find completed treatment Runs.",
            "run.group",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "treatment",
            (treatment11, treatment22, treatment33),
            running,
            HardNegativeReason.RUNNING_INELIGIBLE,
        ),
        (
            "Find the Run on DatasetVersion dataset-v2.",
            "dataset.version",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "dataset-v2",
            (dataset_mismatch,),
            treatment11,
            HardNegativeReason.WRONG_DATASET,
        ),
        (
            "Find Runs whose workers config is numeric 1.",
            "config.workers",
            PredicateOperator.EQ,
            PredicateValueType.INTEGER,
            1,
            (baseline11, baseline22, baseline33, treatment11, treatment22, treatment33),
            typed_string,
            HardNegativeReason.STRING_NUMERIC_CONFUSION,
        ),
        (
            "Find failed Runs.",
            "run.status",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "failed",
            (failed,),
            running,
            HardNegativeReason.RUNNING_INELIGIBLE,
        ),
        (
            "Find running Runs.",
            "run.status",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "running",
            (running,),
            failed,
            HardNegativeReason.FAILED_HIGH_SCORE,
        ),
        (
            "Find the Run missing a code commit.",
            "run.commit_sha",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "missing",
            (missing,),
            treatment11,
            HardNegativeReason.WRONG_RUN,
        ),
    )
    for (
        question,
        field,
        operator,
        value_type,
        value,
        expected_ids,
        negative_id,
        reason,
    ) in structured_specs:
        ordinal = len(cases) + 1
        add(
            ExperimentGoldenSlice.STRUCTURED_FILTER,
            question,
            "structured_filter",
            tuple(_run_entity(frozen, run_id) for run_id in expected_ids),
            _predicate(ordinal, field, operator, value_type, value),
            negative=_run_entity(frozen, negative_id),
            negative_reason=reason,
        )

    # 13-20: numeric, range, rank, unit, step, time-series, unknown direction, missing.
    numeric_specs: tuple[
        tuple[
            str, str, PredicateOperator, PredicateValueType, Scalar, str, str, HardNegativeReason
        ],
        ...,
    ] = (
        (
            "Find the highest eligible final test nDCG.",
            "metric.value",
            PredicateOperator.MAX,
            PredicateValueType.FLOAT,
            0.87,
            treatment33,
            "ndcg_at_10",
            HardNegativeReason.FAILED_HIGH_SCORE,
        ),
        (
            "Find final test nDCG greater than or equal to 0.86.",
            "metric.value",
            PredicateOperator.GTE,
            PredicateValueType.FLOAT,
            0.86,
            treatment11,
            "ndcg_at_10",
            HardNegativeReason.WRONG_RUN,
        ),
        (
            "Find the lowest completed baseline latency.",
            "metric.value",
            PredicateOperator.MIN,
            PredicateValueType.FLOAT,
            117.7,
            baseline33,
            "latency_ms",
            HardNegativeReason.WRONG_RUN,
        ),
        (
            "Return the percent-unit nDCG observation without treating 86 as ratio 0.86.",
            "metric.unit",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "percent",
            unit_mismatch,
            "ndcg_at_10",
            HardNegativeReason.WRONG_UNIT,
        ),
        (
            "Return the final step of treatment seed 22 nDCG.",
            "metric.step",
            PredicateOperator.FINAL,
            PredicateValueType.INTEGER,
            3,
            treatment22,
            "ndcg_at_10",
            HardNegativeReason.WRONG_RUN,
        ),
        (
            "Return the complete nDCG time series for baseline seed 11.",
            "metric.step",
            PredicateOperator.LATEST,
            PredicateValueType.INTEGER,
            3,
            baseline11,
            "ndcg_at_10",
            HardNegativeReason.WRONG_RUN,
        ),
        (
            "Return mystery_score but do not infer a higher-is-better direction.",
            "metric.direction",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "unknown",
            missing,
            "mystery_score",
            HardNegativeReason.UNKNOWN_DIRECTION,
        ),
        (
            "Distinguish a missing latency observation from numeric zero.",
            "metric.value",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "missing",
            missing,
            "latency_ms",
            HardNegativeReason.MISSING_IS_NOT_ZERO,
        ),
    )
    for question, field, operator, value_type, value, run_id, metric_name, reason in numeric_specs:
        ordinal = len(cases) + 1
        truth = _metric_truth(
            frozen,
            run_id,
            metric_name,
            missing=ordinal == 20,
        )
        negative_id = (
            failed
            if reason == HardNegativeReason.FAILED_HIGH_SCORE
            else (unit_mismatch if reason == HardNegativeReason.WRONG_UNIT else running)
        )
        add(
            ExperimentGoldenSlice.METRIC_NUMERIC,
            question,
            "metric_numeric",
            (
                (_run_entity(frozen, run_id),)
                if truth.missing
                else (_metric_entity(frozen, run_id, metric_name),)
            ),
            _predicate(
                ordinal,
                field,
                operator,
                value_type,
                value,
                unit=truth.raw_unit,
                conversion_policy=(
                    "ratio_percent_explicit" if reason == HardNegativeReason.WRONG_UNIT else "none"
                ),
            ),
            answerability=(
                Answerability.INSUFFICIENT_METADATA
                if ordinal in {19, 20}
                else Answerability.ANSWERABLE
            ),
            metric_truth=(truth,),
            negative=_run_entity(frozen, negative_id),
            negative_reason=reason,
        )

    # 21-28: four-state comparison and condition hard negatives.
    comparison_specs = (
        (
            treatment11,
            ComparabilityState.COMPARABLE,
            (
                _difference(
                    "config.reranker",
                    DifferenceRole.TREATMENT,
                    "bm25",
                    "hybrid",
                    "declared treatment",
                ),
            ),
            (),
        ),
        (
            treatment22,
            ComparabilityState.COMPARABLE,
            (
                _difference(
                    "config.reranker",
                    DifferenceRole.TREATMENT,
                    "bm25",
                    "hybrid",
                    "declared treatment",
                ),
            ),
            (),
        ),
        (
            dataset_mismatch,
            ComparabilityState.NOT_COMPARABLE,
            (
                _difference(
                    "dataset_version",
                    DifferenceRole.DISQUALIFYING,
                    "dataset-v1",
                    "dataset-v2",
                    "different DatasetVersion",
                ),
            ),
            (),
        ),
        (
            unit_mismatch,
            ComparabilityState.NOT_COMPARABLE,
            (
                _difference(
                    "metric.unit",
                    DifferenceRole.DISQUALIFYING,
                    "ratio",
                    "percent",
                    "same name has different unit",
                ),
            ),
            (),
        ),
        (
            failed,
            ComparabilityState.NOT_COMPARABLE,
            (
                _difference(
                    "run.status",
                    DifferenceRole.DISQUALIFYING,
                    "completed",
                    "failed",
                    "failed Run is excluded",
                ),
            ),
            (),
        ),
        (
            running,
            ComparabilityState.NOT_COMPARABLE,
            (
                _difference(
                    "run.status",
                    DifferenceRole.DISQUALIFYING,
                    "completed",
                    "running",
                    "running Run is incomplete",
                ),
            ),
            (),
        ),
        (
            missing,
            ComparabilityState.INSUFFICIENT_METADATA,
            (
                _difference(
                    "environment",
                    DifferenceRole.UNKNOWN,
                    "present",
                    None,
                    "candidate environment missing",
                ),
            ),
            ("code_commit", "environment_snapshot"),
        ),
        (
            typed_string,
            ComparabilityState.NOT_COMPARABLE,
            (
                _difference(
                    "config.workers",
                    DifferenceRole.DISQUALIFYING,
                    1,
                    "1",
                    "typed config values differ",
                ),
            ),
            (),
        ),
    )
    for candidate, state, differences, missing_fields in comparison_specs:
        ordinal = len(cases) + 1
        metric_name = "mystery_score" if candidate == missing else "ndcg_at_10"
        answerability = (
            Answerability.INSUFFICIENT_METADATA
            if state == ComparabilityState.INSUFFICIENT_METADATA
            else Answerability.ANSWERABLE
        )
        add(
            ExperimentGoldenSlice.COMPARISON,
            f"Compare baseline seed 11 with {_run_slug(candidate)}.",
            "run_comparison",
            (
                _run_entity(frozen, baseline11),
                _run_entity(frozen, candidate),
            ),
            _predicate(
                ordinal,
                "comparison.candidate_run_id",
                PredicateOperator.EQ,
                PredicateValueType.STRING,
                candidate,
            ),
            answerability=answerability,
            metric_truth=(_metric_truth(frozen, candidate, metric_name),),
            comparison=_comparison(
                baseline11,
                candidate,
                state,
                differences,
                missing_fields,
            ),
            negative=_run_entity(frozen, failed if candidate != failed else running),
            negative_reason=(
                HardNegativeReason.STRING_NUMERIC_CONFUSION
                if candidate == typed_string
                else HardNegativeReason.WRONG_RUN
            ),
        )

    # 29-34: reproduction roles and artifact located-versus-verified truth.
    reproduction_specs = (
        (
            treatment11,
            ReproductionRoleState.PRESENT_VERIFIED,
            Answerability.ANSWERABLE,
            "Reproduce treatment seed 11 with its verified evaluation artifact.",
        ),
        (
            missing,
            ReproductionRoleState.PRESENT_UNVERIFIED,
            Answerability.INSUFFICIENT_METADATA,
            "Assess reproduction for the URI-only artifact Run.",
        ),
        (
            missing,
            ReproductionRoleState.PRESENT_UNVERIFIED,
            Answerability.INSUFFICIENT_METADATA,
            "List missing code and environment roles.",
        ),
        (
            baseline11,
            ReproductionRoleState.MISSING,
            Answerability.INSUFFICIENT_METADATA,
            "Assess reproduction when the required artifact role is absent.",
        ),
        (
            treatment22,
            ReproductionRoleState.MISSING,
            Answerability.INSUFFICIENT_METADATA,
            "Return the seed and command roles but flag the absent artifact.",
        ),
        (
            treatment11,
            ReproductionRoleState.PRESENT_VERIFIED,
            Answerability.ANSWERABLE,
            "Distinguish verified checksum content from a mere artifact locator.",
        ),
    )
    for run_id, artifact_state, answerability, question in reproduction_specs:
        ordinal = len(cases) + 1
        expected: tuple[ExpectedEntity, ...] = (_run_entity(frozen, run_id),)
        if run_id == treatment11:
            expected += (_artifact_entity(frozen, run_id, "evaluation-report.json"),)
        reproduction = _reproduction(
            frozen,
            run_id,
            artifact_state=artifact_state,
        )
        add(
            ExperimentGoldenSlice.REPRODUCTION,
            question,
            "reproduction",
            expected,
            _predicate(
                ordinal,
                "reproduction.run_id",
                PredicateOperator.EQ,
                PredicateValueType.STRING,
                run_id,
            ),
            answerability=answerability,
            reproduction=reproduction,
            negative=_run_entity(frozen, running),
            negative_reason=(
                HardNegativeReason.URI_NOT_VERIFIED
                if artifact_state == ReproductionRoleState.PRESENT_UNVERIFIED
                else HardNegativeReason.WRONG_RUN
            ),
        )

    # 35-38: failed/running status truth and unsupported/error distinction.
    failure_specs = (
        (
            "Exclude the failed 0.99 Run from best completed nDCG.",
            treatment33,
            "completed",
            failed,
            HardNegativeReason.FAILED_HIGH_SCORE,
        ),
        (
            "Exclude the running candidate from completed ranking.",
            treatment33,
            "completed",
            running,
            HardNegativeReason.RUNNING_INELIGIBLE,
        ),
        (
            "Return the failed Run when status=failed is requested exactly.",
            failed,
            "failed",
            running,
            HardNegativeReason.RUNNING_INELIGIBLE,
        ),
        (
            "Return the running Run state without turning unsupported_v1 into system_error.",
            running,
            "running",
            failed,
            HardNegativeReason.FAILED_HIGH_SCORE,
        ),
    )
    for question, expected_id, status_value, negative_id, reason in failure_specs:
        ordinal = len(cases) + 1
        add(
            ExperimentGoldenSlice.FAILURE_STATUS,
            question,
            "failure_status",
            (_run_entity(frozen, expected_id),),
            _predicate(
                ordinal,
                "run.status",
                PredicateOperator.EQ,
                PredicateValueType.STRING,
                status_value,
            ),
            negative=_run_entity(frozen, negative_id),
            negative_reason=reason,
            unavailable_reason=ReviewedReason.UNSUPPORTED_V1 if ordinal == 38 else None,
        )

    # 39-42: deterministic group aggregation and trends.
    aggregation_specs = (
        ("Compute baseline seed mean final nDCG.", baseline11, PredicateOperator.MEAN, "mean"),
        ("Compute treatment seed mean final nDCG.", treatment11, PredicateOperator.MEAN, "mean"),
        ("Return the baseline seed 11 nDCG trend.", baseline11, PredicateOperator.LATEST, "trend"),
        (
            "Aggregate only completed treatment Runs and exclude failed/running Runs.",
            treatment33,
            PredicateOperator.MEAN,
            "mean",
        ),
    )
    for question, run_id, operator, aggregation in aggregation_specs:
        ordinal = len(cases) + 1
        truth = _metric_truth(
            frozen,
            run_id,
            "ndcg_at_10",
            aggregation=aggregation,
        )
        add(
            ExperimentGoldenSlice.AGGREGATION_TREND,
            question,
            "aggregation_trend",
            (_metric_entity(frozen, run_id, "ndcg_at_10"),),
            _predicate(
                ordinal,
                "metric.value",
                operator,
                PredicateValueType.FLOAT,
                truth.canonical_value or 0.0,
                unit="ratio",
            ),
            metric_truth=(truth,),
            negative=_run_entity(frozen, failed),
            negative_reason=HardNegativeReason.FAILED_HIGH_SCORE,
        )

    # 43-45: correct zero, insufficient comparability, and ACL refusal.
    add(
        ExperimentGoldenSlice.UNANSWERABLE_INCOMPARABLE_ACL,
        "Locate Run external ID expv1-run-999, which does not exist.",
        "unanswerable",
        (),
        _predicate(
            43, "run.external_id", PredicateOperator.EQ, PredicateValueType.STRING, "expv1-run-999"
        ),
        answerability=Answerability.UNANSWERABLE,
        negative=_run_entity(frozen, treatment11),
        negative_reason=HardNegativeReason.WRONG_RUN,
        expects_zero=True,
        unavailable_reason=ReviewedReason.NO_MATCH,
    )
    add(
        ExperimentGoldenSlice.UNANSWERABLE_INCOMPARABLE_ACL,
        "Can missing-commit-environment be declared an improvement over baseline seed 11?",
        "incomparable",
        (_run_entity(frozen, missing),),
        _predicate(
            44,
            "comparison.state",
            PredicateOperator.EQ,
            PredicateValueType.STRING,
            "insufficient_metadata",
        ),
        answerability=Answerability.INSUFFICIENT_METADATA,
        comparison=_comparison(
            baseline11,
            missing,
            ComparabilityState.INSUFFICIENT_METADATA,
            (
                _difference(
                    "environment",
                    DifferenceRole.UNKNOWN,
                    "present",
                    None,
                    "candidate metadata missing",
                ),
            ),
            ("code_commit", "environment_snapshot"),
        ),
        negative=_run_entity(frozen, failed),
        negative_reason=HardNegativeReason.WRONG_RUN,
        expects_zero=False,
        unavailable_reason=ReviewedReason.MISSING_METADATA,
    )
    add(
        ExperimentGoldenSlice.UNANSWERABLE_INCOMPARABLE_ACL,
        "Return a Run from a project scope the caller is not authorized to read.",
        "acl_denied",
        (),
        _predicate(45, "acl.authorized", PredicateOperator.EQ, PredicateValueType.BOOLEAN, False),
        answerability=Answerability.ACL_DENIED,
        negative=_run_entity(frozen, treatment11),
        negative_reason=HardNegativeReason.ACL_SCOPE,
        expects_zero=True,
        unavailable_reason=ReviewedReason.ACL_DENIED,
    )

    return tuple(cases)


class ReleasedFixtureFile(_FrozenContract):
    path: SafeRelativePath
    sha256: Sha256
    size: NonNegativeInt
    kind: Literal["golden_cases", "fixture_recipe"]


class ReleasedExperimentManifest(_FrozenContract):
    schema_version: Literal["experiment-evaluation-foundation-v1"]
    dataset_id: Literal["experiment-golden-v1"]
    dataset_version: Literal["experiment-golden-v1"]
    released: Literal[True]
    release_record_id: Identifier
    package_hash: Sha256
    case_count: Literal[45]
    primary_slice_counts: dict[ExperimentGoldenSlice, PositiveInt]
    case_ids: tuple[Identifier, ...]
    case_membership_digest: Sha256
    hard_negative_membership_digest: Sha256
    fixture_recipe_digest: Sha256
    eligible_metric_case_ids: dict[ExperimentEvaluationMetric, tuple[Identifier, ...]]
    file_digests: dict[SafeRelativePath, Sha256]
    files: tuple[ReleasedFixtureFile, ...]
    label_policy_version: Literal["experiment-label-policy-v1"]
    denominator_policy_version: Literal["experiment-denominator-policy-v1"]
    locator_policy_version: Literal["experiment-locator-policy-v1"]
    comparability_policy_version: Literal["experiment-comparability-truth-v1"]
    created_from_code_identity: Literal["experiment-e0-01-foundation-contract-v1"]

    @model_validator(mode="after")
    def _manifest_shape(self) -> Self:
        if self.primary_slice_counts != dict(EXPECTED_SLICE_COUNTS):
            raise ValueError("released slice counts mismatch")
        canonical_ids = tuple(
            f"experiment-v1-{ordinal:03d}" for ordinal in range(1, EXPERIMENT_GOLDEN_CASE_COUNT + 1)
        )
        if self.case_ids != canonical_ids:
            raise ValueError("released case membership/order mismatch")
        if set(self.eligible_metric_case_ids) != set(ExperimentEvaluationMetric):
            raise ValueError("eligible metric denominator authority is incomplete")
        for case_ids in self.eligible_metric_case_ids.values():
            if not case_ids or len(case_ids) != len(set(case_ids)):
                raise ValueError("eligible metric membership must be non-empty and unique")
            if tuple(case_id for case_id in self.case_ids if case_id in case_ids) != case_ids:
                raise ValueError("eligible metric membership is not canonically ordered")
        paths = tuple(item.path for item in self.files)
        if paths != (EXPERIMENT_GOLDEN_CASES, EXPERIMENT_GOLDEN_RECIPE):
            raise ValueError("released file membership/order mismatch")
        if self.file_digests != {item.path: item.sha256 for item in self.files}:
            raise ValueError("file_digests disagree with file authority")
        return self


class ExperimentGoldenDataset(_FrozenContract):
    dataset_id: Literal["experiment-golden-v1"]
    dataset_version: Literal["experiment-golden-v1"]
    schema_version: Literal["experiment-evaluation-foundation-v1"]
    package_hash: Sha256
    authority_hash: Sha256
    release_record_id: Identifier
    manifest: ReleasedExperimentManifest
    recipe: ExperimentFixtureRecipe
    cases: tuple[ExperimentGoldenCase, ...]

    @model_validator(mode="after")
    def _released_authority(self) -> Self:
        if self.package_hash != self.manifest.package_hash:
            raise ValueError("dataset/manifest package hash mismatch")
        if len(self.cases) != EXPERIMENT_GOLDEN_CASE_COUNT:
            raise ValueError("Experiment Golden-v1 requires exactly 45 cases")
        case_ids = tuple(case.case_id for case in self.cases)
        if case_ids != self.manifest.case_ids:
            raise ValueError("case membership differs from manifest")
        if Counter(case.primary_slice for case in self.cases) != Counter(EXPECTED_SLICE_COUNTS):
            raise ValueError("case slice counts differ from released authority")
        authority_hash = _sha256(
            b"".join(case.canonical_json_bytes() + b"\n" for case in self.cases)
        )
        if authority_hash != self.authority_hash:
            raise ValueError("case authority digest mismatch")
        if (
            EXPERIMENT_GOLDEN_AUTHORITY_HASH != "sha256:" + "0" * 64
            and authority_hash != EXPERIMENT_GOLDEN_AUTHORITY_HASH
        ):
            raise ValueError("case authority differs from frozen release constant")
        if (
            EXPERIMENT_GOLDEN_PACKAGE_HASH != "sha256:" + "0" * 64
            and self.package_hash != EXPERIMENT_GOLDEN_PACKAGE_HASH
        ):
            raise ValueError("package differs from frozen release constant")
        expected_eligible = {
            metric: tuple(case.case_id for case in self.cases if metric in case.eligible_metrics)
            for metric in ExperimentEvaluationMetric
        }
        if self.manifest.eligible_metric_case_ids != expected_eligible:
            raise ValueError("per-case eligibility/denominator drift")
        _validate_dataset_entities(self)
        return self

    @property
    def case_membership(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    @property
    def case_by_id(self) -> Mapping[str, ExperimentGoldenCase]:
        return {case.case_id: case for case in self.cases}

    @property
    def authority_entities(self) -> Mapping[str, ExpectedEntity]:
        entities = _recipe_authority_entities(self.recipe)
        for case in self.cases:
            for entity in (
                *case.expected_entities,
                *(item.entity for item in case.hard_negatives),
            ):
                previous = entities.get(entity.entity_id)
                if previous is not None and previous != entity:
                    raise ExperimentEvaluationError("entity authority is contradictory")
                entities[entity.entity_id] = entity
        return entities


def _recipe_authority_entities(
    recipe: ExperimentFixtureRecipe,
) -> dict[str, ExpectedEntity]:
    entities: dict[str, ExpectedEntity] = {}

    def add(entity: ExpectedEntity) -> None:
        if entity.entity_id in entities:
            raise ValueError("fixture authority identity is duplicated")
        entities[entity.entity_id] = entity

    add(_experiment_entity(recipe))
    for definition in recipe.metric_definitions:
        add(_metric_definition_entity(recipe, definition.name))
    for run in recipe.runs:
        add(_run_entity(recipe, run.logical_run_id))
        for metric in run.metrics:
            add(_metric_series_entity(recipe, run.logical_run_id, metric.name))
            for point in metric.points:
                add(_metric_observation_entity(recipe, metric, point))
        for artifact in run.artifacts:
            add(_artifact_entity(recipe, run.logical_run_id, artifact.name))
    return entities


def _recompute_metric_truth(
    recipe: ExperimentFixtureRecipe,
    released: MetricTruth,
) -> MetricTruth:
    run = next(
        (item for item in recipe.runs if item.logical_run_id == released.run_id),
        None,
    )
    definition = next(
        (
            item
            for item in recipe.metric_definitions
            if item.definition_id == released.definition_id
        ),
        None,
    )
    if run is None or definition is None or released.metric_name != definition.name:
        raise ValueError("MetricTruth crosses Run/MetricDefinition authority")
    if released.missing:
        recomputed = _metric_truth(
            recipe,
            released.run_id,
            definition.name,
            missing=True,
        )
    else:
        series = next(
            (item for item in run.metrics if item.series_id == released.series_id),
            None,
        )
        if (
            series is None
            or series.definition_id != definition.definition_id
            or series.run_id != run.logical_run_id
        ):
            raise ValueError("MetricTruth crosses MetricSeries authority")
        recomputed = _metric_truth(
            recipe,
            released.run_id,
            definition.name,
            aggregation=released.aggregation,
        )
    if recomputed != released:
        raise ValueError("MetricTruth differs from released per-observation identity authority")
    return recomputed


def _validate_dataset_entities(dataset: ExperimentGoldenDataset) -> None:
    recipe = dataset.recipe
    run_ids = {run.logical_run_id for run in recipe.runs}
    authority = _recipe_authority_entities(recipe)
    for case in dataset.cases:
        referenced = (
            *case.expected_entities,
            *(item.entity for item in case.hard_negatives),
        )
        for entity in referenced:
            if authority.get(entity.entity_id) != entity:
                raise ValueError("case entity differs from fixture identity authority")
        for entity_id in case.acceptable_alternative_ids:
            if entity_id not in authority:
                raise ValueError("acceptable alternative is absent from fixture authority")
        for truth in case.metric_truth:
            _recompute_metric_truth(recipe, truth)
        if case.comparability_truth is not None:
            comparison = case.comparability_truth
            if comparison.baseline_run_id not in run_ids or any(
                run_id not in run_ids for run_id in comparison.candidate_run_ids
            ):
                raise ValueError("comparison truth crosses Experiment membership")
        if case.reproduction_truth is not None and case.reproduction_truth.run_id not in run_ids:
            raise ValueError("reproduction truth crosses Run membership")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExperimentEvaluationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ExperimentEvaluationError(f"non-standard JSON number is forbidden: {value}")


def _parse_json_bytes(value: bytes, *, source: str) -> Any:
    try:
        return json.loads(
            value,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ExperimentEvaluationError):
            raise
        raise ExperimentEvaluationError(f"invalid JSON in {source}: {exc}") from exc


def _scan_string(value: str, *, source: str) -> None:
    decoded = value
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    candidates = (value, decoded)
    for candidate in candidates:
        if _CREDENTIAL_RE.search(candidate):
            raise ExperimentEvaluationError(f"credential/secret sentinel in {source}")
        if _EMAIL_RE.search(candidate):
            raise ExperimentEvaluationError(f"email address in {source}")
        if _PAYMENT_RE.search(candidate):
            raise ExperimentEvaluationError(f"payment-card-like data in {source}")
        if (
            candidate.startswith("/")
            or candidate.casefold().startswith("file://")
            or _POSIX_ABSOLUTE_RE.search(candidate)
            or _WINDOWS_ABSOLUTE_RE.search(candidate)
            or candidate.startswith("\\\\")
        ):
            raise ExperimentEvaluationError(f"absolute or temporary path in {source}")


def _security_scan(value: Any, *, source: str) -> None:
    if isinstance(value, str):
        _scan_string(value, source=source)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ExperimentEvaluationError(f"NaN/Infinity in {source}")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _security_scan(key, source=source)
            _security_scan(item, source=source)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for item in value:
            _security_scan(item, source=source)


def _package_files(root: Path) -> tuple[str, ...]:
    if root.is_symlink():
        raise ExperimentEvaluationError("package root cannot be a symlink")
    result: list[str] = []

    def walk(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = directory / entry.name
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise ExperimentEvaluationError("symlink file/component is forbidden")
                if stat.S_ISDIR(info.st_mode):
                    walk(path)
                elif stat.S_ISREG(info.st_mode):
                    result.append(path.relative_to(root).as_posix())
                else:
                    raise ExperimentEvaluationError("non-regular package component is forbidden")

    walk(root)
    return tuple(sorted(result))


def _package_identity(
    manifest_payload: Mapping[str, Any],
    file_bytes: Mapping[str, bytes],
) -> str:
    identity_manifest = dict(manifest_payload)
    identity_manifest.pop("package_hash", None)
    identity_manifest.pop("release_record_id", None)
    payload = bytearray(_canonical_json(identity_manifest))
    for path in sorted(file_bytes):
        payload.extend(b"\x00")
        payload.extend(path.encode("utf-8"))
        payload.extend(b"\x00")
        payload.extend(file_bytes[path])
    return _sha256(bytes(payload))


def _membership_digest(values: Sequence[str]) -> str:
    return _sha256(_canonical_json(list(values)))


def _default_fixture_root() -> Path:
    return Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "experiment_golden"


def build_experiment_golden_v1(
    root: str | Path,
    *,
    recipe: ExperimentFixtureRecipe | None = None,
) -> ReleasedExperimentManifest:
    """Deterministically materialize only the portable released package files."""

    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    if _package_files(target):
        raise ExperimentEvaluationError("Golden build target must be empty")
    frozen_recipe = recipe or deterministic_experiment_recipe()
    cases = build_experiment_golden_cases(frozen_recipe)
    case_bytes = b"".join(case.canonical_json_bytes() + b"\n" for case in cases)
    recipe_bytes = frozen_recipe.canonical_json_bytes() + b"\n"
    (target / EXPERIMENT_GOLDEN_CASES).write_bytes(case_bytes)
    (target / EXPERIMENT_GOLDEN_RECIPE).write_bytes(recipe_bytes)
    file_bytes = {
        EXPERIMENT_GOLDEN_CASES: case_bytes,
        EXPERIMENT_GOLDEN_RECIPE: recipe_bytes,
    }
    file_rows = (
        ReleasedFixtureFile(
            path=EXPERIMENT_GOLDEN_CASES,
            sha256=_sha256(case_bytes),
            size=len(case_bytes),
            kind="golden_cases",
        ),
        ReleasedFixtureFile(
            path=EXPERIMENT_GOLDEN_RECIPE,
            sha256=_sha256(recipe_bytes),
            size=len(recipe_bytes),
            kind="fixture_recipe",
        ),
    )
    case_ids = tuple(case.case_id for case in cases)
    eligible = {
        metric: tuple(case.case_id for case in cases if metric in case.eligible_metrics)
        for metric in ExperimentEvaluationMetric
    }
    hard_negative_rows = [
        {
            "case_id": case.case_id,
            "entity_ids": list(case.forbidden_candidate_ids),
        }
        for case in cases
    ]
    payload: dict[str, Any] = {
        "schema_version": EXPERIMENT_GOLDEN_SCHEMA_VERSION,
        "dataset_id": EXPERIMENT_GOLDEN_DATASET_ID,
        "dataset_version": EXPERIMENT_GOLDEN_DATASET_VERSION,
        "released": True,
        "release_record_id": "release-pending",
        "package_hash": "sha256:" + "0" * 64,
        "case_count": EXPERIMENT_GOLDEN_CASE_COUNT,
        "primary_slice_counts": {key.value: value for key, value in EXPECTED_SLICE_COUNTS.items()},
        "case_ids": list(case_ids),
        "case_membership_digest": _membership_digest(case_ids),
        "hard_negative_membership_digest": _sha256(_canonical_json(hard_negative_rows)),
        "fixture_recipe_digest": frozen_recipe.canonical_sha256(),
        "eligible_metric_case_ids": {key.value: list(value) for key, value in eligible.items()},
        "file_digests": {item.path: item.sha256 for item in file_rows},
        "files": [item.model_dump(mode="json") for item in file_rows],
        "label_policy_version": "experiment-label-policy-v1",
        "denominator_policy_version": "experiment-denominator-policy-v1",
        "locator_policy_version": "experiment-locator-policy-v1",
        "comparability_policy_version": "experiment-comparability-truth-v1",
        "created_from_code_identity": "experiment-e0-01-foundation-contract-v1",
    }
    package_hash = _package_identity(payload, file_bytes)
    payload["package_hash"] = package_hash
    payload["release_record_id"] = f"release-{package_hash.removeprefix('sha256:')[:32]}"
    manifest = ReleasedExperimentManifest.model_validate(payload)
    (target / EXPERIMENT_GOLDEN_MANIFEST).write_bytes(manifest.canonical_json_bytes() + b"\n")
    return manifest


def load_experiment_golden_v1(
    root: str | Path | None = None,
) -> ExperimentGoldenDataset:
    """Load and fail-closed verify one portable released Golden package."""

    package_root = Path(root) if root is not None else _default_fixture_root()
    if not package_root.is_dir():
        raise ExperimentEvaluationError("Golden package directory is missing")
    files = _package_files(package_root)
    manifest_path = package_root / EXPERIMENT_GOLDEN_MANIFEST
    if EXPERIMENT_GOLDEN_MANIFEST not in files:
        raise ExperimentEvaluationError("Golden manifest is missing")
    manifest_raw = manifest_path.read_bytes()
    manifest_payload = _parse_json_bytes(manifest_raw, source=EXPERIMENT_GOLDEN_MANIFEST)
    _security_scan(manifest_payload, source=EXPERIMENT_GOLDEN_MANIFEST)
    try:
        manifest = ReleasedExperimentManifest.model_validate(manifest_payload)
    except ValueError as exc:
        raise ExperimentEvaluationError(f"invalid Golden manifest: {exc}") from exc
    expected_files = tuple(
        sorted((EXPERIMENT_GOLDEN_MANIFEST, *(item.path for item in manifest.files)))
    )
    if files != expected_files:
        missing = sorted(set(expected_files) - set(files))
        extra = sorted(set(files) - set(expected_files))
        raise ExperimentEvaluationError(
            f"Golden file membership mismatch; missing={missing}, extra={extra}"
        )
    declared_bytes: dict[str, bytes] = {}
    for item in manifest.files:
        path = package_root / item.path
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ExperimentEvaluationError("declared package file is not a regular file")
        value = path.read_bytes()
        if len(value) != item.size or _sha256(value) != item.sha256:
            raise ExperimentEvaluationError(f"digest/size mismatch for {item.path}")
        payload = _parse_json_bytes(
            value if item.kind == "fixture_recipe" else b"{}",
            source=item.path,
        )
        if item.kind == "fixture_recipe":
            _security_scan(payload, source=item.path)
        else:
            for line_number, line in enumerate(value.splitlines(), start=1):
                if not line:
                    raise ExperimentEvaluationError("empty Golden JSONL row")
                row_payload = _parse_json_bytes(
                    line,
                    source=f"{item.path}:{line_number}",
                )
                _security_scan(row_payload, source=f"{item.path}:{line_number}")
        declared_bytes[item.path] = value
    package_hash = _package_identity(manifest_payload, declared_bytes)
    if package_hash != manifest.package_hash:
        raise ExperimentEvaluationError("content-addressed package hash mismatch")
    expected_release = f"release-{package_hash.removeprefix('sha256:')[:32]}"
    if manifest.release_record_id != expected_release:
        raise ExperimentEvaluationError("release record identity mismatch")

    recipe_payload = _parse_json_bytes(
        declared_bytes[EXPERIMENT_GOLDEN_RECIPE],
        source=EXPERIMENT_GOLDEN_RECIPE,
    )
    try:
        recipe = ExperimentFixtureRecipe.model_validate(recipe_payload)
    except ValueError as exc:
        raise ExperimentEvaluationError(f"invalid fixture recipe: {exc}") from exc
    if recipe.canonical_sha256() != manifest.fixture_recipe_digest:
        raise ExperimentEvaluationError("fixture recipe digest mismatch")

    cases: list[ExperimentGoldenCase] = []
    for line_number, line in enumerate(
        declared_bytes[EXPERIMENT_GOLDEN_CASES].splitlines(),
        start=1,
    ):
        try:
            cases.append(
                ExperimentGoldenCase.model_validate(
                    _parse_json_bytes(
                        line,
                        source=f"{EXPERIMENT_GOLDEN_CASES}:{line_number}",
                    )
                )
            )
        except ValueError as exc:
            raise ExperimentEvaluationError(
                f"invalid Golden case row {line_number}: {exc}"
            ) from exc
    authority_hash = _sha256(b"".join(case.canonical_json_bytes() + b"\n" for case in cases))
    if manifest.case_membership_digest != _membership_digest(tuple(case.case_id for case in cases)):
        raise ExperimentEvaluationError("case membership digest mismatch")
    hard_negative_rows = [
        {
            "case_id": case.case_id,
            "entity_ids": list(case.forbidden_candidate_ids),
        }
        for case in cases
    ]
    if manifest.hard_negative_membership_digest != _sha256(_canonical_json(hard_negative_rows)):
        raise ExperimentEvaluationError("hard-negative membership mismatch")
    try:
        return ExperimentGoldenDataset(
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            schema_version=manifest.schema_version,
            package_hash=manifest.package_hash,
            authority_hash=authority_hash,
            release_record_id=manifest.release_record_id,
            manifest=manifest,
            recipe=recipe,
            cases=tuple(cases),
        )
    except ValueError as exc:
        raise ExperimentEvaluationError(f"released Golden authority mismatch: {exc}") from exc


def verify_experiment_golden_v1(root: str | Path | None = None) -> ExperimentGoldenDataset:
    """Alias emphasizing verify-only, portable package behavior."""

    return load_experiment_golden_v1(root)


class ReviewedCandidate(_FrozenContract):
    entity_id: ContractText
    locator: ContractText
    acl_ref: ContractText
    rank: PositiveInt


class ReviewedExperimentCaseRow(_FrozenContract):
    dataset_id: Literal["experiment-golden-v1"]
    dataset_version: Literal["experiment-golden-v1"]
    package_hash: Sha256
    case_id: Identifier
    reviewed: Literal[True]
    outcome: ReviewedOutcome
    candidates: tuple[ReviewedCandidate, ...] = ()
    observed_answerability: Answerability
    observed_predicate: TypedPredicate | None = None
    observed_metric_truth: tuple[MetricTruth, ...] = ()
    observed_comparability: ComparabilityTruth | None = None
    observed_reproduction: ReproductionTruth | None = None
    unavailable_reason: ReviewedReason | None = None
    error_reason: ReviewedReason | None = None

    @model_validator(mode="after")
    def _outcome_shape(self) -> Self:
        ranks = tuple(candidate.rank for candidate in self.candidates)
        if ranks != tuple(range(1, len(ranks) + 1)):
            raise ValueError("reviewed candidates require contiguous canonical ranks")
        if len({candidate.entity_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("reviewed candidates contain duplicate entities")
        if self.outcome == ReviewedOutcome.RETURNED:
            if not self.candidates or self.unavailable_reason or self.error_reason:
                raise ValueError("returned outcome requires candidates and no failure reason")
        elif self.outcome == ReviewedOutcome.ZERO_RESULT:
            if self.candidates or self.unavailable_reason or self.error_reason:
                raise ValueError("zero_result requires no candidates or failure reason")
        elif self.outcome == ReviewedOutcome.UNAVAILABLE:
            if self.candidates or self.unavailable_reason is None or self.error_reason:
                raise ValueError("unavailable outcome requires only unavailable_reason")
            if self.unavailable_reason == ReviewedReason.SYSTEM_ERROR:
                raise ValueError("system_error is not an unavailable reason")
        elif self.outcome == ReviewedOutcome.ERROR and (
            self.candidates
            or self.unavailable_reason
            or self.error_reason != ReviewedReason.SYSTEM_ERROR
        ):
            raise ValueError("error outcome requires explicit system_error")
        return self


class ExperimentCaseEvaluation(_FrozenContract):
    case_id: Identifier
    primary_slice: ExperimentGoldenSlice
    outcome: ReviewedOutcome
    eligible_metrics: tuple[ExperimentEvaluationMetric, ...]
    correctness: dict[ExperimentEvaluationMetric, StrictBool]


class ExperimentMetricResult(_FrozenContract):
    metric: ExperimentEvaluationMetric
    slice: ExperimentGoldenSlice | Literal["overall"]
    state: EvaluationTriState
    numerator: NonNegativeInt
    denominator: NonNegativeInt
    value: StrictFloat | None
    available: NonNegativeInt
    unavailable: NonNegativeInt
    zero_result: NonNegativeInt
    error: NonNegativeInt
    reason: ContractText | None = None

    @model_validator(mode="after")
    def _counts_are_honest(self) -> Self:
        if self.available + self.unavailable + self.error != self.denominator:
            raise ValueError("metric availability counts do not equal frozen denominator")
        if self.zero_result > self.available:
            raise ValueError("zero results must be available evaluated cases")
        if self.numerator > self.denominator:
            raise ValueError("metric numerator exceeds denominator")
        if self.state == EvaluationTriState.AVAILABLE:
            if self.denominator == 0 or self.value is None or self.reason is not None:
                raise ValueError("AVAILABLE metric shape is invalid")
            if not math.isclose(
                self.value,
                self.numerator / self.denominator,
                rel_tol=0.0,
                abs_tol=1e-15,
            ):
                raise ValueError("metric value differs from numerator/denominator")
        else:
            if self.value is not None or self.reason is None:
                raise ValueError("UNAVAILABLE/ERROR metric requires reason and no value")
        return self


class ExperimentSliceReport(_FrozenContract):
    slice: ExperimentGoldenSlice | Literal["overall"]
    total_cases: NonNegativeInt
    available_cases: NonNegativeInt
    unavailable_cases: NonNegativeInt
    zero_result_cases: NonNegativeInt
    error_cases: NonNegativeInt
    metrics: tuple[ExperimentMetricResult, ...]

    @model_validator(mode="after")
    def _case_counts_are_honest(self) -> Self:
        if self.available_cases + self.unavailable_cases + self.error_cases != self.total_cases:
            raise ValueError("slice availability counts do not equal total cases")
        if self.zero_result_cases > self.available_cases:
            raise ValueError("slice zero_result count exceeds available cases")
        if tuple(item.metric for item in self.metrics) != tuple(ExperimentEvaluationMetric):
            raise ValueError("slice report metric order/membership mismatch")
        return self


class ExperimentEvaluationArtifact(_FrozenContract):
    schema_version: Literal["experiment-evaluation-result-v1"] = "experiment-evaluation-result-v1"
    dataset_id: Literal["experiment-golden-v1"]
    dataset_version: Literal["experiment-golden-v1"]
    package_hash: Sha256
    authority_hash: Sha256
    evaluator_version: Literal["experiment-deterministic-evaluator-v1"] = (
        "experiment-deterministic-evaluator-v1"
    )
    membership: tuple[Identifier, ...]
    case_results: tuple[ExperimentCaseEvaluation, ...]
    overall: ExperimentSliceReport
    slices: tuple[ExperimentSliceReport, ...]

    @model_validator(mode="after")
    def _complete_report(self) -> Self:
        if len(self.membership) != EXPERIMENT_GOLDEN_CASE_COUNT:
            raise ValueError("evaluation artifact requires frozen full membership")
        if tuple(item.case_id for item in self.case_results) != self.membership:
            raise ValueError("case result membership/order mismatch")
        if tuple(item.slice for item in self.slices) != tuple(ExperimentGoldenSlice):
            raise ValueError("evaluation artifact requires all eight slices")
        return self


class ExperimentEvaluationPreparation(_FrozenContract):
    schema_version: Literal["experiment-evaluation-preparation-v1"] = (
        "experiment-evaluation-preparation-v1"
    )
    status: Literal["PREPARED"] = "PREPARED"
    foundation_status: Literal["E0-01 IMPLEMENTED / AWAITING FOUNDATION GATE"] = (
        "E0-01 IMPLEMENTED / AWAITING FOUNDATION GATE"
    )
    dataset_id: Literal["experiment-golden-v1"]
    dataset_version: Literal["experiment-golden-v1"]
    package_hash: Sha256
    authority_hash: Sha256
    case_count: Literal[45]
    slice_counts: dict[ExperimentGoldenSlice, PositiveInt]
    eligible_denominators: dict[ExperimentEvaluationMetric, PositiveInt]
    evaluation_run_created: Literal[False] = False
    baseline_run_created: Literal[False] = False
    metrics_published: Literal[False] = False
    qualification_created: Literal[False] = False


def prepare_experiment_evaluation_v1(
    root: str | Path | None = None,
) -> ExperimentEvaluationPreparation:
    dataset = load_experiment_golden_v1(root)
    return ExperimentEvaluationPreparation(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        package_hash=dataset.package_hash,
        authority_hash=dataset.authority_hash,
        case_count=EXPERIMENT_GOLDEN_CASE_COUNT,
        slice_counts=dict(EXPECTED_SLICE_COUNTS),
        eligible_denominators={
            metric: len(dataset.manifest.eligible_metric_case_ids[metric])
            for metric in ExperimentEvaluationMetric
        },
    )


def _validate_evaluation_membership(
    dataset: ExperimentGoldenDataset,
    rows: Sequence[ReviewedExperimentCaseRow],
    membership: Sequence[str] | None,
) -> tuple[str, ...]:
    expected = dataset.case_membership
    if membership is None:
        selected = expected
    else:
        selected = tuple(membership)
        if not selected:
            raise ExperimentEvaluationError(
                "explicit empty membership is forbidden; use None for frozen full membership"
            )
        if len(selected) != len(set(selected)):
            raise ExperimentEvaluationError("duplicate evaluation membership")
        unknown = sorted(set(selected) - set(expected))
        if unknown:
            raise ExperimentEvaluationError(f"unknown evaluation case IDs: {unknown}")
        if selected != expected:
            raise ExperimentEvaluationError(
                "explicit membership must equal the complete canonical released order"
            )
    row_ids = tuple(row.case_id for row in rows)
    if row_ids != selected:
        raise ExperimentEvaluationError(
            "reviewed rows must exactly match canonical membership and order"
        )
    return selected


def _validate_reviewed_rows(
    dataset: ExperimentGoldenDataset,
    rows: Sequence[ReviewedExperimentCaseRow],
) -> None:
    authority = dataset.authority_entities
    for row in rows:
        if (
            row.dataset_id != dataset.dataset_id
            or row.dataset_version != dataset.dataset_version
            or row.package_hash != dataset.package_hash
        ):
            raise ExperimentEvaluationError("reviewed row dataset/package identity mismatch")
        case = dataset.case_by_id.get(row.case_id)
        if case is None:
            raise ExperimentEvaluationError("reviewed row has unknown case_id")
        for candidate in row.candidates:
            entity = authority.get(candidate.entity_id)
            if entity is None:
                raise ExperimentEvaluationError(
                    f"candidate {candidate.entity_id} is outside released authority"
                )
            if candidate.locator != entity.locator:
                raise ExperimentEvaluationError("candidate locator/identity mismatch")
            if candidate.acl_ref != entity.acl_ref or candidate.acl_ref != case.acl_ref:
                raise ExperimentEvaluationError("candidate crosses released ACL scope")


def _candidate_truth_correct(
    case: ExperimentGoldenCase,
    row: ReviewedExperimentCaseRow,
) -> bool:
    candidate_ids = tuple(candidate.entity_id for candidate in row.candidates)
    if case.answerability in {Answerability.UNANSWERABLE, Answerability.ACL_DENIED}:
        return not candidate_ids
    if row.outcome != ReviewedOutcome.RETURNED:
        return False
    expected = case.expected_candidate_order
    if candidate_ids[: len(expected)] != expected:
        return False
    if len(candidate_ids) < len(expected):
        return False
    alternatives = case.acceptable_alternative_ids
    extras = candidate_ids[len(expected) :]
    if any(entity_id not in alternatives for entity_id in extras):
        return False
    return extras == tuple(entity_id for entity_id in alternatives if entity_id in extras)


def _case_correctness(
    dataset: ExperimentGoldenDataset,
    case: ExperimentGoldenCase,
    row: ReviewedExperimentCaseRow,
) -> dict[ExperimentEvaluationMetric, bool]:
    candidate_ids = {candidate.entity_id for candidate in row.candidates}
    hard_ids = set(case.forbidden_candidate_ids)
    candidate_correct = _candidate_truth_correct(case, row)
    recomputed_metric_truth = tuple(
        _recompute_metric_truth(dataset.recipe, truth) for truth in case.metric_truth
    )
    result: dict[ExperimentEvaluationMetric, bool] = {}
    for metric in case.eligible_metrics:
        if row.outcome == ReviewedOutcome.ERROR or (
            row.outcome == ReviewedOutcome.UNAVAILABLE
            and metric
            not in {
                ExperimentEvaluationMetric.UNANSWERABLE_ACCURACY,
                ExperimentEvaluationMetric.HARD_NEGATIVE_AVOIDANCE,
                ExperimentEvaluationMetric.ZERO_RESULT_ACCURACY,
            }
        ):
            result[metric] = False
        elif metric == ExperimentEvaluationMetric.EXACT_ENTITY_ACCURACY:
            result[metric] = bool(case.expected_candidate_order) and candidate_correct
        elif metric == ExperimentEvaluationMetric.PREDICATE_ACCURACY:
            result[metric] = candidate_correct and row.observed_predicate == case.predicate
        elif metric == ExperimentEvaluationMetric.NUMERIC_UNIT_DIRECTION_ACCURACY:
            result[metric] = (
                candidate_correct and row.observed_metric_truth == recomputed_metric_truth
            )
        elif metric == ExperimentEvaluationMetric.COMPARABILITY_ACCURACY:
            result[metric] = (
                candidate_correct and row.observed_comparability == case.comparability_truth
            )
        elif metric == ExperimentEvaluationMetric.REPRODUCTION_ACCURACY:
            result[metric] = (
                candidate_correct and row.observed_reproduction == case.reproduction_truth
            )
        elif metric == ExperimentEvaluationMetric.HARD_NEGATIVE_AVOIDANCE:
            result[metric] = candidate_correct and not bool(candidate_ids & hard_ids)
        elif metric == ExperimentEvaluationMetric.UNANSWERABLE_ACCURACY:
            outcome_ok = {
                Answerability.UNANSWERABLE: row.outcome == ReviewedOutcome.ZERO_RESULT,
                Answerability.ACL_DENIED: (
                    row.outcome == ReviewedOutcome.UNAVAILABLE
                    and row.unavailable_reason == ReviewedReason.ACL_DENIED
                ),
                Answerability.INSUFFICIENT_METADATA: (
                    row.observed_answerability == Answerability.INSUFFICIENT_METADATA
                    and row.outcome
                    in {
                        ReviewedOutcome.RETURNED,
                        ReviewedOutcome.ZERO_RESULT,
                        ReviewedOutcome.UNAVAILABLE,
                    }
                ),
                Answerability.ANSWERABLE: row.outcome == ReviewedOutcome.RETURNED,
            }[case.answerability]
            result[metric] = (
                candidate_correct
                and row.observed_answerability == case.answerability
                and outcome_ok
            )
        elif metric == ExperimentEvaluationMetric.ZERO_RESULT_ACCURACY:
            if case.expects_zero_result:
                result[metric] = candidate_correct and (
                    row.outcome == ReviewedOutcome.ZERO_RESULT
                    or (
                        case.answerability == Answerability.ACL_DENIED
                        and row.outcome == ReviewedOutcome.UNAVAILABLE
                        and row.unavailable_reason == ReviewedReason.ACL_DENIED
                    )
                )
            else:
                result[metric] = candidate_correct and row.outcome != ReviewedOutcome.ZERO_RESULT
        else:  # pragma: no cover - exhaustive enum guard
            raise ExperimentEvaluationError(f"unsupported evaluator metric: {metric}")
    return result


def _metric_result(
    metric: ExperimentEvaluationMetric,
    label: ExperimentGoldenSlice | Literal["overall"],
    cases: Sequence[ExperimentGoldenCase],
    row_by_id: Mapping[str, ReviewedExperimentCaseRow],
    correctness_by_id: Mapping[str, Mapping[ExperimentEvaluationMetric, bool]],
) -> ExperimentMetricResult:
    eligible = [case for case in cases if metric in case.eligible_metrics]
    denominator = len(eligible)
    numerator = sum(int(correctness_by_id[case.case_id][metric]) for case in eligible)
    available = sum(
        row_by_id[case.case_id].outcome in {ReviewedOutcome.RETURNED, ReviewedOutcome.ZERO_RESULT}
        for case in eligible
    )
    unavailable = sum(
        row_by_id[case.case_id].outcome == ReviewedOutcome.UNAVAILABLE for case in eligible
    )
    error = sum(row_by_id[case.case_id].outcome == ReviewedOutcome.ERROR for case in eligible)
    zero_result = sum(
        row_by_id[case.case_id].outcome == ReviewedOutcome.ZERO_RESULT for case in eligible
    )
    if denominator == 0:
        state = EvaluationTriState.UNAVAILABLE
        value = None
        reason = "no released eligible cases in this slice"
    elif error == denominator:
        state = EvaluationTriState.ERROR
        value = None
        reason = "all released eligible cases ended in system error"
    elif available == 0:
        state = EvaluationTriState.UNAVAILABLE
        value = None
        reason = "all released eligible cases are unavailable"
    else:
        state = EvaluationTriState.AVAILABLE
        value = numerator / denominator
        reason = None
    return ExperimentMetricResult(
        metric=metric,
        slice=label,
        state=state,
        numerator=numerator,
        denominator=denominator,
        value=value,
        available=available,
        unavailable=unavailable,
        zero_result=zero_result,
        error=error,
        reason=reason,
    )


def _slice_report(
    label: ExperimentGoldenSlice | Literal["overall"],
    cases: Sequence[ExperimentGoldenCase],
    row_by_id: Mapping[str, ReviewedExperimentCaseRow],
    correctness_by_id: Mapping[str, Mapping[ExperimentEvaluationMetric, bool]],
) -> ExperimentSliceReport:
    rows = [row_by_id[case.case_id] for case in cases]
    return ExperimentSliceReport(
        slice=label,
        total_cases=len(cases),
        available_cases=sum(
            row.outcome in {ReviewedOutcome.RETURNED, ReviewedOutcome.ZERO_RESULT} for row in rows
        ),
        unavailable_cases=sum(row.outcome == ReviewedOutcome.UNAVAILABLE for row in rows),
        zero_result_cases=sum(row.outcome == ReviewedOutcome.ZERO_RESULT for row in rows),
        error_cases=sum(row.outcome == ReviewedOutcome.ERROR for row in rows),
        metrics=tuple(
            _metric_result(
                metric,
                label,
                cases,
                row_by_id,
                correctness_by_id,
            )
            for metric in ExperimentEvaluationMetric
        ),
    )


def evaluate_reviewed_experiment_retrieval(
    rows: Sequence[ReviewedExperimentCaseRow],
    *,
    dataset: ExperimentGoldenDataset | None = None,
    membership: Sequence[str] | None = None,
) -> ExperimentEvaluationArtifact:
    """Recompute all metrics from released authority and strict reviewed rows.

    ``membership=None`` is the sole spelling for the complete released set.
    Any explicit deletion, empty selection, duplication, reordering, or unknown
    ID is rejected rather than changing a denominator.
    """

    authority = dataset or load_experiment_golden_v1()
    selected = _validate_evaluation_membership(authority, rows, membership)
    _validate_reviewed_rows(authority, rows)
    row_by_id = {row.case_id: row for row in rows}
    correctness_by_id = {
        case.case_id: _case_correctness(authority, case, row_by_id[case.case_id])
        for case in authority.cases
    }
    case_results = tuple(
        ExperimentCaseEvaluation(
            case_id=case.case_id,
            primary_slice=case.primary_slice,
            outcome=row_by_id[case.case_id].outcome,
            eligible_metrics=case.eligible_metrics,
            correctness=correctness_by_id[case.case_id],
        )
        for case in authority.cases
    )
    overall = _slice_report(
        "overall",
        authority.cases,
        row_by_id,
        correctness_by_id,
    )
    slices = tuple(
        _slice_report(
            primary_slice,
            tuple(case for case in authority.cases if case.primary_slice == primary_slice),
            row_by_id,
            correctness_by_id,
        )
        for primary_slice in ExperimentGoldenSlice
    )
    return ExperimentEvaluationArtifact(
        dataset_id=authority.dataset_id,
        dataset_version=authority.dataset_version,
        package_hash=authority.package_hash,
        authority_hash=authority.authority_hash,
        membership=selected,
        case_results=case_results,
        overall=overall,
        slices=slices,
    )


def perfect_reviewed_experiment_rows(
    dataset: ExperimentGoldenDataset | None = None,
) -> tuple[ReviewedExperimentCaseRow, ...]:
    """Build deterministic reviewed rows that exactly match released authority.

    This is a fixture/testing helper, not a retriever and not a baseline Run.
    """

    authority = dataset or load_experiment_golden_v1()
    rows: list[ReviewedExperimentCaseRow] = []
    for case in authority.cases:
        if case.answerability == Answerability.UNANSWERABLE:
            outcome = ReviewedOutcome.ZERO_RESULT
            candidates: tuple[ReviewedCandidate, ...] = ()
            unavailable = None
        elif case.answerability == Answerability.ACL_DENIED:
            outcome = ReviewedOutcome.UNAVAILABLE
            candidates = ()
            unavailable = ReviewedReason.ACL_DENIED
        elif case.expects_zero_result:
            outcome = ReviewedOutcome.ZERO_RESULT
            candidates = ()
            unavailable = None
        else:
            outcome = ReviewedOutcome.RETURNED
            candidates = tuple(
                ReviewedCandidate(
                    entity_id=entity.entity_id,
                    locator=entity.locator,
                    acl_ref=entity.acl_ref,
                    rank=rank,
                )
                for rank, entity in enumerate(case.expected_entities, start=1)
            )
            unavailable = None
        rows.append(
            ReviewedExperimentCaseRow(
                dataset_id=authority.dataset_id,
                dataset_version=authority.dataset_version,
                package_hash=authority.package_hash,
                case_id=case.case_id,
                reviewed=True,
                outcome=outcome,
                candidates=candidates,
                observed_answerability=case.answerability,
                observed_predicate=case.predicate,
                observed_metric_truth=case.metric_truth,
                observed_comparability=case.comparability_truth,
                observed_reproduction=case.reproduction_truth,
                unavailable_reason=unavailable,
            )
        )
    return tuple(rows)


__all__ = [
    "EXPECTED_SLICE_COUNTS",
    "EXPERIMENT_GOLDEN_AUTHORITY_HASH",
    "EXPERIMENT_GOLDEN_CASE_COUNT",
    "EXPERIMENT_GOLDEN_DATASET_ID",
    "EXPERIMENT_GOLDEN_DATASET_VERSION",
    "EXPERIMENT_GOLDEN_PACKAGE_HASH",
    "EXPERIMENT_GOLDEN_SCHEMA_VERSION",
    "Answerability",
    "ComparabilityDifference",
    "ComparabilityState",
    "ComparabilityTruth",
    "DifferenceRole",
    "EntityKind",
    "EvaluationTriState",
    "ExpectedEntity",
    "ExperimentCaseEvaluation",
    "ExperimentEvaluationArtifact",
    "ExperimentEvaluationError",
    "ExperimentEvaluationMetric",
    "ExperimentEvaluationPreparation",
    "ExperimentGoldenCase",
    "ExperimentGoldenDataset",
    "ExperimentGoldenSlice",
    "ExperimentMetricResult",
    "ExperimentSliceReport",
    "HardNegative",
    "HardNegativeReason",
    "MetricDirection",
    "MetricTruth",
    "PredicateOperator",
    "PredicateValueType",
    "ReleasedExperimentManifest",
    "ReproductionRole",
    "ReproductionRoleName",
    "ReproductionRoleState",
    "ReproductionTruth",
    "ReviewedCandidate",
    "ReviewedExperimentCaseRow",
    "ReviewedOutcome",
    "ReviewedReason",
    "TypedPredicate",
    "build_experiment_golden_cases",
    "build_experiment_golden_v1",
    "evaluate_reviewed_experiment_retrieval",
    "load_experiment_golden_v1",
    "perfect_reviewed_experiment_rows",
    "prepare_experiment_evaluation_v1",
    "verify_experiment_golden_v1",
]
