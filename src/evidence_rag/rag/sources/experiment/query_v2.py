"""Typed Experiment E2 query, parameterized compiler, and numeric evaluator."""

from __future__ import annotations

import json
import math
import re
import shlex
import statistics
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts_v2 import (
    ExperimentMetricDefinitionV2,
    ExperimentMetricObservationV2,
    ExperimentRunSnapshotV2,
    MetricDirectionV2,
    canonical_sha256_v2,
)

EXPERIMENT_QUERY_CONTRACT_VERSION = "experiment-query-contract-v2"
EXPERIMENT_QUERY_COMPILER_VERSION = "experiment-query-compiler-v2"
EXPERIMENT_NUMERIC_EVALUATOR_VERSION = "experiment-numeric-evaluator-v2"

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,511}")
_CONFIG_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")
_METRIC_EXPR_RE = re.compile(
    r"^metric\.(?P<name>[A-Za-z0-9_.@+-]+)"
    r"(?P<op>>=|<=|!=|=|>|<)"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<unit>%|[A-Za-z][A-Za-z0-9_.-]*)?$"
)
_FIELD_NAMES = frozenset(
    {
        "acl_ref",
        "experiment_id",
        "project_id",
        "repository_id",
        "run_id",
        "source_id",
        "status",
    }
)
_OP_SQL = {
    "eq": "=",
    "ne": "!=",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}
_UNIT_CONVERSIONS: dict[tuple[str, str], float] = {
    ("percent", "ratio"): 0.01,
    ("%", "ratio"): 0.01,
    ("ratio", "percent"): 100.0,
    ("ratio", "%"): 100.0,
    ("ms", "seconds"): 0.001,
    ("milliseconds", "seconds"): 0.001,
    ("seconds", "ms"): 1000.0,
    ("seconds", "milliseconds"): 1000.0,
}


class ExperimentQueryV2Error(ValueError):
    """Raised for malformed or unsafe Experiment V2 queries."""


class ExperimentQueryTaskV2(StrEnum):
    SEARCH = "search"
    BEST = "best"
    COMPARE = "compare"
    TREND = "trend"
    REPRODUCE = "reproduce"
    AGGREGATE = "aggregate"


class ExperimentOperatorV2(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"


class ExperimentAggregationV2(StrEnum):
    LATEST = "latest"
    FINAL = "final"
    BEST = "best"
    MAX = "max"
    MIN = "min"
    MEAN = "mean"
    MEDIAN = "median"
    STD = "std"
    COUNT = "count"
    DELTA = "delta"
    RELATIVE_DELTA = "relative_delta"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ExperimentFieldPredicateV2(_Frozen):
    field: str
    operator: ExperimentOperatorV2
    value: str | bool | int | tuple[str, ...]

    @model_validator(mode="after")
    def _safe(self) -> ExperimentFieldPredicateV2:
        if self.field not in _FIELD_NAMES and not self.field.startswith("config."):
            raise ValueError("field is not allowlisted")
        if (
            self.field.startswith("config.")
            and _CONFIG_KEY_RE.fullmatch(self.field.removeprefix("config.")) is None
        ):
            raise ValueError("config field is not canonical")
        if self.operator is ExperimentOperatorV2.IN:
            if not isinstance(self.value, tuple) or not self.value or len(self.value) > 50:
                raise ValueError("in predicate requires 1..50 values")
        elif isinstance(self.value, tuple):
            raise ValueError("tuple value requires in operator")
        return self


class ExperimentMetricPredicateV2(_Frozen):
    metric: str
    operator: ExperimentOperatorV2
    value: float | None = None
    values: tuple[float, ...] = ()
    unit: str | None = None
    split: str | None = None
    step_policy: Literal["latest", "final", "best", "all"] = "final"
    aggregation: ExperimentAggregationV2 = ExperimentAggregationV2.FINAL

    @model_validator(mode="after")
    def _consistent(self) -> ExperimentMetricPredicateV2:
        if _ID_RE.fullmatch(self.metric) is None:
            raise ValueError("metric identity is not canonical")
        if self.operator is ExperimentOperatorV2.IN:
            if self.value is not None or not self.values or len(self.values) > 50:
                raise ValueError("metric in predicate values are invalid")
        elif self.value is None or self.values:
            raise ValueError("metric predicate requires one numeric value")
        return self


class ExperimentDatasetPredicateV2(_Frozen):
    dataset_id: str | None = None
    version: str | None = None
    split: str | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> ExperimentDatasetPredicateV2:
        if self.dataset_id is None and self.version is None and self.split is None:
            raise ValueError("dataset predicate cannot be empty")
        return self


class ExperimentQueryV2(_Frozen):
    task: ExperimentQueryTaskV2 = ExperimentQueryTaskV2.SEARCH
    project_id: str
    source_id: str
    acl_ref: str
    experiment_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    fields: tuple[ExperimentFieldPredicateV2, ...] = ()
    metrics: tuple[ExperimentMetricPredicateV2, ...] = ()
    dataset: ExperimentDatasetPredicateV2 | None = None
    commit_sha: str | None = None
    started_after: str | None = None
    started_before: str | None = None
    group_by: Literal["run", "experiment", "seed", "dataset", "metric"] = "run"
    limit: int = Field(default=20, ge=1, le=200)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    unresolved: tuple[str, ...] = ()
    contract_version: str = EXPERIMENT_QUERY_CONTRACT_VERSION

    @model_validator(mode="after")
    def _canonical(self) -> ExperimentQueryV2:
        for identity, label in (
            (self.project_id, "project"),
            (self.source_id, "source"),
            (self.acl_ref, "ACL"),
        ):
            if _ID_RE.fullmatch(identity) is None:
                raise ValueError(f"{label} identity is not canonical")
        for values, label, maximum in (
            (self.experiment_ids, "experiment IDs", 50),
            (self.run_ids, "run IDs", 50),
            (self.statuses, "statuses", 10),
            (self.fields, "field predicates", 24),
            (self.metrics, "metric predicates", 16),
            (self.unresolved, "unresolved predicates", 16),
        ):
            if len(values) > maximum:
                raise ValueError(f"too many {label}")
        if self.experiment_ids != tuple(sorted(set(self.experiment_ids))):
            raise ValueError("experiment IDs must be sorted and unique")
        if self.run_ids != tuple(sorted(set(self.run_ids))):
            raise ValueError("run IDs must be sorted and unique")
        if self.statuses != tuple(sorted(set(self.statuses))):
            raise ValueError("statuses must be sorted and unique")
        if self.confidence < 1 and not self.unresolved:
            raise ValueError("low-confidence query requires unresolved predicates")
        return self


@dataclass(frozen=True, slots=True)
class CompiledExperimentQueryV2:
    sql: str
    parameters: tuple[object, ...]
    plan_sha256: str
    query_sha256: str
    compiler_version: str
    fallback_reason: str | None


class ExperimentNumericResultV2(_Frozen):
    status: Literal["available", "unavailable"]
    operator: ExperimentAggregationV2
    metric_definition_id: str
    included_observation_ids: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]
    numerator: float | None
    denominator: float | None
    value: float | None
    unit: str | None
    direction: MetricDirectionV2
    reason: str | None
    evaluator_version: str = EXPERIMENT_NUMERIC_EVALUATOR_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentNumericResultV2:
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        if self.content_sha256 != canonical_sha256_v2(payload):
            raise ValueError("numeric result digest mismatch")
        if self.status == "available" and self.value is None:
            raise ValueError("available numeric result requires a value")
        if self.status == "unavailable" and self.reason is None:
            raise ValueError("unavailable numeric result requires a reason")
        return self


def parse_experiment_query_v2(
    request: dict[str, Any],
    *,
    project_id: str,
    source_id: str,
    acl_ref: str,
) -> ExperimentQueryV2:
    """Validate an explicit request without accepting SQL or unknown fields."""

    allowed = {
        "task",
        "experiment_ids",
        "run_ids",
        "statuses",
        "fields",
        "metrics",
        "dataset",
        "commit_sha",
        "started_after",
        "started_before",
        "group_by",
        "limit",
        "confidence",
        "unresolved",
    }
    unknown = set(request) - allowed
    if unknown:
        raise ExperimentQueryV2Error("unknown query fields: " + ",".join(sorted(unknown)))
    if any(
        token in str(request).casefold()
        for token in ("select ", " union ", " join ", "pragma ", "attach ")
    ):
        raise ExperimentQueryV2Error("raw SQL is forbidden")
    payload = {
        **request,
        "project_id": project_id,
        "source_id": source_id,
        "acl_ref": acl_ref,
        "experiment_ids": tuple(sorted(set(request.get("experiment_ids") or ()))),
        "run_ids": tuple(sorted(set(request.get("run_ids") or ()))),
        "statuses": tuple(sorted(set(request.get("statuses") or ()))),
        "fields": tuple(request.get("fields") or ()),
        "metrics": tuple(request.get("metrics") or ()),
        "unresolved": tuple(request.get("unresolved") or ()),
    }
    try:
        return ExperimentQueryV2.model_validate(payload)
    except ValueError as exc:
        raise ExperimentQueryV2Error("typed Experiment query is invalid") from exc


def parse_experiment_query_text_v2(
    text: str,
    *,
    project_id: str,
    source_id: str,
    acl_ref: str,
) -> ExperimentQueryV2:
    """Parse a bounded deterministic key/value syntax; ambiguous text stays unresolved."""

    if not text or len(text) > 2_000 or "\x00" in text:
        raise ExperimentQueryV2Error("query text is not bounded")
    if any(token in text.casefold() for token in ("select ", " union ", " join ", ";", "--")):
        raise ExperimentQueryV2Error("SQL-like input is forbidden")
    task = ExperimentQueryTaskV2.SEARCH
    experiments: list[str] = []
    runs: list[str] = []
    statuses: list[str] = []
    fields: list[ExperimentFieldPredicateV2] = []
    metrics: list[ExperimentMetricPredicateV2] = []
    dataset_values: dict[str, str] = {}
    unresolved: list[str] = []
    limit = 20
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ExperimentQueryV2Error("query quoting is invalid") from exc
    for token in tokens:
        lowered = token.casefold()
        if lowered in {item.value for item in ExperimentQueryTaskV2}:
            task = ExperimentQueryTaskV2(lowered)
            continue
        metric = _METRIC_EXPR_RE.fullmatch(token)
        if metric:
            metrics.append(
                ExperimentMetricPredicateV2(
                    metric=metric.group("name"),
                    operator=ExperimentOperatorV2(
                        {
                            "=": "eq",
                            "!=": "ne",
                            ">": "gt",
                            ">=": "gte",
                            "<": "lt",
                            "<=": "lte",
                        }[metric.group("op")]
                    ),
                    value=float(metric.group("value")),
                    unit=metric.group("unit"),
                )
            )
            continue
        if ":" not in token:
            unresolved.append(token[:120])
            continue
        key, value = token.split(":", 1)
        key = key.casefold()
        if key == "experiment":
            experiments.append(value)
        elif key == "run":
            runs.append(value)
        elif key == "status":
            statuses.append(value)
        elif key in {"dataset", "dataset_version", "split"}:
            dataset_values[
                {"dataset": "dataset_id", "dataset_version": "version"}.get(key, key)
            ] = value
        elif key == "commit":
            fields.append(
                ExperimentFieldPredicateV2(
                    field="repository_id",
                    operator=ExperimentOperatorV2.EQ,
                    value=value,
                )
            )
        elif key == "limit" and value.isdigit():
            limit = int(value)
        elif key.startswith("config."):
            fields.append(
                ExperimentFieldPredicateV2(
                    field=key,
                    operator=ExperimentOperatorV2.EQ,
                    value=value,
                )
            )
        else:
            unresolved.append(token[:120])
    return ExperimentQueryV2(
        task=task,
        project_id=project_id,
        source_id=source_id,
        acl_ref=acl_ref,
        experiment_ids=tuple(sorted(set(experiments))),
        run_ids=tuple(sorted(set(runs))),
        statuses=tuple(sorted(set(statuses))),
        fields=tuple(fields),
        metrics=tuple(metrics),
        dataset=ExperimentDatasetPredicateV2(**dataset_values) if dataset_values else None,
        limit=limit,
        confidence=1.0 if not unresolved else max(0.0, 1 - 0.15 * len(unresolved)),
        unresolved=tuple(unresolved),
    )


def compile_experiment_query_v2(
    query: ExperimentQueryV2,
) -> CompiledExperimentQueryV2:
    """Compile only allowlisted fields to SQL with all user values bound."""

    if query.unresolved:
        return CompiledExperimentQueryV2(
            sql="",
            parameters=(),
            plan_sha256=canonical_sha256_v2(
                {
                    "status": "unresolved",
                    "compiler": EXPERIMENT_QUERY_COMPILER_VERSION,
                }
            ),
            query_sha256=canonical_sha256_v2(query.model_dump(mode="json")),
            compiler_version=EXPERIMENT_QUERY_COMPILER_VERSION,
            fallback_reason="unresolved_predicates",
        )
    clauses = ["rs.active=1", "rs.project_id=?", "rs.source_id=?", "rs.acl_ref=?"]
    parameters: list[object] = [query.project_id, query.source_id, query.acl_ref]

    def _in_clause(column: str, values: tuple[str, ...]) -> None:
        if values:
            clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
            parameters.extend(values)

    _in_clause("rs.experiment_id", query.experiment_ids)
    _in_clause("rs.run_id", query.run_ids)
    _in_clause("rs.status", query.statuses)
    column_registry = {
        "project_id": "rs.project_id",
        "source_id": "rs.source_id",
        "acl_ref": "rs.acl_ref",
        "experiment_id": "rs.experiment_id",
        "run_id": "rs.run_id",
        "status": "rs.status",
    }
    for predicate in query.fields:
        if predicate.field.startswith("config."):
            key = predicate.field.removeprefix("config.")
            if predicate.operator not in {
                ExperimentOperatorV2.EQ,
                ExperimentOperatorV2.NE,
            }:
                raise ExperimentQueryV2Error("config fields support only eq/ne in SQL")
            clauses.append(
                "EXISTS (SELECT 1 FROM experiment_v2_condition_snapshots cs "
                "WHERE cs.condition_id=json_extract(rs.payload_json,'$.config.config_snapshot_id') "
                "AND cs.kind='config' "
                "AND json_extract(cs.payload_json,'$.values') LIKE ?)"
            )
            rendered = json.dumps([key, predicate.value], separators=(",", ":"))[1:-1]
            parameters.append(f"%{rendered}%")
            continue
        column = column_registry.get(predicate.field)
        if column is None:
            raise ExperimentQueryV2Error("field has no SQL compiler mapping")
        if predicate.operator is ExperimentOperatorV2.IN:
            assert isinstance(predicate.value, tuple)
            clauses.append(f"{column} IN ({','.join('?' for _ in predicate.value)})")
            parameters.extend(predicate.value)
        elif predicate.operator.value in _OP_SQL:
            clauses.append(f"{column} {_OP_SQL[predicate.operator.value]} ?")
            parameters.append(predicate.value)
        else:
            raise ExperimentQueryV2Error("field operator is not SQL-compilable")
    for predicate in query.metrics:
        if predicate.operator is ExperimentOperatorV2.IN:
            metric_operator = f"IN ({','.join('?' for _ in predicate.values)})"
            metric_values: tuple[object, ...] = predicate.values
        else:
            metric_operator = _OP_SQL[predicate.operator.value] + " ?"
            assert predicate.value is not None
            metric_values = (predicate.value,)
        split_clause = " AND mo.split=?" if predicate.split is not None else ""
        clauses.append(
            "EXISTS (SELECT 1 FROM experiment_v2_metric_observations mo "
            "JOIN experiment_v2_metric_definitions md "
            "ON md.definition_id=mo.definition_id "
            "WHERE mo.run_snapshot_id=rs.run_snapshot_id AND mo.active=1 AND mo.valid=1 "
            f"AND (md.canonical_name=? OR EXISTS (SELECT 1 FROM "
            "experiment_v2_metric_aliases ma WHERE ma.definition_id=md.definition_id "
            "AND ma.alias=?)) "
            f"AND mo.numeric_value {metric_operator}{split_clause})"
        )
        parameters.extend((predicate.metric, predicate.metric, *metric_values))
        if predicate.split is not None:
            parameters.append(predicate.split)
    if query.dataset is not None:
        for key, value in (
            ("dataset_id", query.dataset.dataset_id),
            ("version", query.dataset.version),
            ("split", query.dataset.split),
        ):
            if value is not None:
                clauses.append("json_extract(rs.payload_json, '$.dataset." + key + "') = ?")
                parameters.append(value)
    if query.commit_sha is not None:
        clauses.append("json_extract(rs.payload_json, '$.commit_sha') = ?")
        parameters.append(query.commit_sha)
    sql = (
        "SELECT rs.payload_json FROM experiment_v2_run_snapshots rs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY rs.experiment_id, rs.run_id, rs.run_snapshot_id LIMIT ?"
    )
    parameters.append(query.limit)
    plan_payload = {
        "sql": sql,
        "parameter_types": [type(item).__name__ for item in parameters],
        "compiler": EXPERIMENT_QUERY_COMPILER_VERSION,
    }
    return CompiledExperimentQueryV2(
        sql=sql,
        parameters=tuple(parameters),
        plan_sha256=canonical_sha256_v2(plan_payload),
        query_sha256=canonical_sha256_v2(query.model_dump(mode="json")),
        compiler_version=EXPERIMENT_QUERY_COMPILER_VERSION,
        fallback_reason=None,
    )


def select_observations_v2(
    snapshot: ExperimentRunSnapshotV2,
    predicate: ExperimentMetricPredicateV2,
) -> tuple[ExperimentMetricObservationV2, ...]:
    definitions = {item.definition_id: item for item in snapshot.definitions}
    matching = [
        item
        for item in snapshot.observations
        if item.valid
        and (
            definitions[item.definition_id].canonical_name.casefold() == predicate.metric.casefold()
            or predicate.metric.casefold()
            in {alias.casefold() for alias in definitions[item.definition_id].aliases}
        )
        and (predicate.split is None or item.split == predicate.split)
    ]
    matching.sort(
        key=lambda item: (
            item.step is None,
            item.step if item.step is not None else -1,
            item.observed_at or "",
            item.observation_id,
        )
    )
    if predicate.step_policy in {"latest", "final"} and matching:
        return (matching[-1],)
    if predicate.step_policy == "best" and matching:
        definition = definitions[matching[0].definition_id]
        if definition.direction is MetricDirectionV2.UNKNOWN:
            return ()
        reverse = definition.direction is MetricDirectionV2.HIGHER
        return (
            sorted(
                matching,
                key=lambda item: (
                    -(item.numeric_value or 0) if reverse else item.numeric_value or 0,
                    item.observation_id,
                ),
            )[0],
        )
    return tuple(matching)


def filter_experiment_snapshots_v2(
    query: ExperimentQueryV2,
    snapshots: tuple[ExperimentRunSnapshotV2, ...],
) -> tuple[ExperimentRunSnapshotV2, ...]:
    """Apply the typed AST in memory with the same scope-first semantics."""

    if query.unresolved:
        return ()

    def matches(snapshot: ExperimentRunSnapshotV2) -> bool:
        if (
            snapshot.project_id != query.project_id
            or snapshot.source_id != query.source_id
            or snapshot.acl_ref != query.acl_ref
        ):
            return False
        if query.experiment_ids and snapshot.experiment_id not in query.experiment_ids:
            return False
        if query.run_ids and snapshot.run_id not in query.run_ids:
            return False
        if query.statuses and snapshot.status not in query.statuses:
            return False
        if query.dataset is not None:
            if (
                query.dataset.dataset_id is not None
                and snapshot.dataset.dataset_id != query.dataset.dataset_id
            ):
                return False
            if (
                query.dataset.version is not None
                and snapshot.dataset.version != query.dataset.version
            ):
                return False
            if query.dataset.split is not None and snapshot.dataset.split != query.dataset.split:
                return False
        if query.commit_sha is not None and snapshot.commit_sha != query.commit_sha:
            return False
        config = dict(snapshot.config.values)
        static = {
            "project_id": snapshot.project_id,
            "source_id": snapshot.source_id,
            "acl_ref": snapshot.acl_ref,
            "experiment_id": snapshot.experiment_id,
            "run_id": snapshot.run_id,
            "status": snapshot.status,
            "repository_id": snapshot.repository_id,
        }
        for predicate in query.fields:
            actual = (
                config.get(predicate.field.removeprefix("config."))
                if predicate.field.startswith("config.")
                else static[predicate.field]
            )
            values = predicate.value
            if predicate.operator is ExperimentOperatorV2.IN:
                if actual not in values:
                    return False
            elif (predicate.operator is ExperimentOperatorV2.EQ and actual != values) or (
                predicate.operator is ExperimentOperatorV2.NE and actual == values
            ):
                return False
            elif predicate.operator in {
                ExperimentOperatorV2.GT,
                ExperimentOperatorV2.GTE,
                ExperimentOperatorV2.LT,
                ExperimentOperatorV2.LTE,
            }:
                if type(actual) not in {int, float} or type(values) not in {int, float}:
                    return False
                if not {
                    ExperimentOperatorV2.GT: actual > values,
                    ExperimentOperatorV2.GTE: actual >= values,
                    ExperimentOperatorV2.LT: actual < values,
                    ExperimentOperatorV2.LTE: actual <= values,
                }[predicate.operator]:
                    return False
        for predicate in query.metrics:
            observations = select_observations_v2(snapshot, predicate)
            if not observations:
                return False
            actual_values = tuple(
                observation.numeric_value
                for observation in observations
                if observation.numeric_value is not None
            )
            if not actual_values:
                return False
            if predicate.operator is ExperimentOperatorV2.IN:
                if not any(value in predicate.values for value in actual_values):
                    return False
            else:
                assert predicate.value is not None
                if not any(
                    {
                        ExperimentOperatorV2.EQ: value == predicate.value,
                        ExperimentOperatorV2.NE: value != predicate.value,
                        ExperimentOperatorV2.GT: value > predicate.value,
                        ExperimentOperatorV2.GTE: value >= predicate.value,
                        ExperimentOperatorV2.LT: value < predicate.value,
                        ExperimentOperatorV2.LTE: value <= predicate.value,
                    }[predicate.operator]
                    for value in actual_values
                ):
                    return False
        return True

    return tuple(
        sorted(
            (snapshot for snapshot in snapshots if matches(snapshot)),
            key=lambda item: (item.experiment_id, item.run_id, item.run_snapshot_id),
        )[: query.limit]
    )


def evaluate_experiment_numeric_v2(
    definition: ExperimentMetricDefinitionV2,
    observations: tuple[ExperimentMetricObservationV2, ...],
    *,
    operator: ExperimentAggregationV2,
    target_unit: str | None = None,
    baseline: ExperimentMetricObservationV2 | None = None,
) -> ExperimentNumericResultV2:
    """Compute numeric truth only from typed, valid observations."""

    included: list[tuple[str, float]] = []
    excluded: list[tuple[str, str]] = []
    output_unit = target_unit or definition.unit
    for observation in observations:
        if not observation.valid or observation.numeric_value is None:
            excluded.append((observation.observation_id, observation.invalid_reason or "invalid"))
            continue
        value = observation.numeric_value
        source_unit = observation.canonical_unit
        if output_unit and source_unit and source_unit != output_unit:
            factor = _UNIT_CONVERSIONS.get((source_unit.casefold(), output_unit.casefold()))
            if factor is None:
                excluded.append((observation.observation_id, "unit_not_convertible"))
                continue
            value *= factor
        included.append((observation.observation_id, value))
    reason: str | None = None
    value: float | None = None
    numerator: float | None = None
    denominator: float | None = None
    if not included:
        reason = "no_valid_observations"
    else:
        values = [item[1] for item in included]
        if operator in {ExperimentAggregationV2.BEST}:
            if definition.direction is MetricDirectionV2.UNKNOWN:
                reason = "metric_direction_unknown"
            else:
                value = (
                    max(values) if definition.direction is MetricDirectionV2.HIGHER else min(values)
                )
        elif operator is ExperimentAggregationV2.MAX:
            value = max(values)
        elif operator is ExperimentAggregationV2.MIN:
            value = min(values)
        elif operator in {
            ExperimentAggregationV2.FINAL,
            ExperimentAggregationV2.LATEST,
        }:
            value = values[-1]
        elif operator is ExperimentAggregationV2.MEAN:
            value = statistics.fmean(values)
        elif operator is ExperimentAggregationV2.MEDIAN:
            value = statistics.median(values)
        elif operator is ExperimentAggregationV2.STD:
            value = statistics.stdev(values) if len(values) > 1 else 0.0
        elif operator is ExperimentAggregationV2.COUNT:
            value = float(len(values))
        elif operator in {
            ExperimentAggregationV2.DELTA,
            ExperimentAggregationV2.RELATIVE_DELTA,
        }:
            if baseline is None or not baseline.valid or baseline.numeric_value is None:
                reason = "baseline_unavailable"
            else:
                baseline_value = baseline.numeric_value
                source_unit = baseline.canonical_unit
                if output_unit and source_unit and source_unit != output_unit:
                    factor = _UNIT_CONVERSIONS.get((source_unit.casefold(), output_unit.casefold()))
                    if factor is None:
                        reason = "baseline_unit_not_convertible"
                    else:
                        baseline_value *= factor
                if reason is None:
                    numerator = values[-1] - baseline_value
                    denominator = baseline_value
                    if operator is ExperimentAggregationV2.DELTA:
                        value = numerator
                    elif baseline_value == 0:
                        reason = "zero_baseline_relative_delta_undefined"
                    else:
                        value = numerator / baseline_value
                        output_unit = "ratio"
        else:
            reason = "operator_not_supported"
    status: Literal["available", "unavailable"] = (
        "available" if value is not None and math.isfinite(value) else "unavailable"
    )
    if status == "unavailable" and reason is None:
        reason = "numeric_result_non_finite"
        value = None
    payload = {
        "status": status,
        "operator": operator,
        "metric_definition_id": definition.definition_id,
        "included_observation_ids": tuple(item[0] for item in included),
        "excluded": tuple(sorted(excluded)),
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "unit": output_unit,
        "direction": definition.direction,
        "reason": reason,
        "evaluator_version": EXPERIMENT_NUMERIC_EVALUATOR_VERSION,
    }
    return ExperimentNumericResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(
            {
                key: item.value if isinstance(item, StrEnum) else item
                for key, item in payload.items()
            }
        ),
    )


__all__ = [
    "EXPERIMENT_NUMERIC_EVALUATOR_VERSION",
    "EXPERIMENT_QUERY_COMPILER_VERSION",
    "EXPERIMENT_QUERY_CONTRACT_VERSION",
    "CompiledExperimentQueryV2",
    "ExperimentAggregationV2",
    "ExperimentDatasetPredicateV2",
    "ExperimentFieldPredicateV2",
    "ExperimentMetricPredicateV2",
    "ExperimentNumericResultV2",
    "ExperimentOperatorV2",
    "ExperimentQueryTaskV2",
    "ExperimentQueryV2",
    "ExperimentQueryV2Error",
    "compile_experiment_query_v2",
    "evaluate_experiment_numeric_v2",
    "filter_experiment_snapshots_v2",
    "parse_experiment_query_text_v2",
    "parse_experiment_query_v2",
    "select_observations_v2",
]
