"""Product-first Experiment V2 read model and typed retriever.

The module intentionally derives immutable snapshots from the existing V1
tables.  It introduces no migration or background writer, so enabling it is a
reversible read-path choice while the production default remains V1.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..experiments.store import ExperimentStore

EXPERIMENT_ENGINE_HEADER = "X-RAG-Experiment-Engine"
EXPERIMENT_V2_VERSION = "experiment-typed-retrieval-v2"
EXPERIMENT_SNAPSHOT_VERSION = "experiment-run-snapshot-v2"
EXPERIMENT_QUERY_VERSION = "experiment-query-v2"

_ENGINE_VALUES = frozenset({"v1", "v2"})
_STATUS_VALUES = frozenset({"queued", "running", "completed", "failed", "cancelled"})
_OPERATORS = {"=": "eq", "==": "eq", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "best",
        "compare",
        "experiment",
        "for",
        "in",
        "is",
        "metric",
        "of",
        "on",
        "run",
        "show",
        "the",
        "to",
        "trend",
        "with",
        "比较",
        "实验",
        "指标",
        "最佳",
        "最优",
        "趋势",
        "运行",
    }
)
_TOKEN_RE = re.compile(r"[\w@./:-]+", re.UNICODE)
_NUMERIC_RE = re.compile(
    r"(?P<metric>[A-Za-z][A-Za-z0-9_.@/-]{0,99})\s*"
    r"(?P<operator>>=|<=|==|=|>|<)\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?:\s*(?P<unit>%|[A-Za-z][A-Za-z0-9_./-]{0,31}))?"
)
_FILTER_RE = re.compile(
    r"(?P<key>status|metric|dataset(?:_id)?|dataset_version|split|unit|experiment_id|run_id|"
    r"config\.[A-Za-z][A-Za-z0-9_.-]{0,63}|seed)\s*[:=]\s*"
    r"(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_UNIT_NORMALIZATION: dict[str, tuple[str, float]] = {
    "%": ("ratio", 0.01),
    "percent": ("ratio", 0.01),
    "percentage": ("ratio", 0.01),
    "ratio": ("ratio", 1.0),
    "ms": ("seconds", 0.001),
    "millisecond": ("seconds", 0.001),
    "milliseconds": ("seconds", 0.001),
    "s": ("seconds", 1.0),
    "sec": ("seconds", 1.0),
    "second": ("seconds", 1.0),
    "seconds": ("seconds", 1.0),
}
_EXPERIMENT_RELEASE_DECISION = {
    "stage": "OPT_IN",
    "default_engine": "v1",
    "decision": "HOLD_DEFAULT_V1",
    "quality_qualified": False,
    "rollback": "omit X-RAG-Experiment-Engine or set it to v1",
}


class ExperimentV2Error(ValueError):
    """Base error for the reversible Experiment V2 read path."""


class ExperimentEngineOverrideError(ExperimentV2Error):
    """Raised when a caller requests an unknown engine version."""


class ExperimentQueryError(ExperimentV2Error):
    """Raised for malformed or unsafe typed queries."""


class ComparisonState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NumericPredicateV2(_FrozenModel):
    metric: str = Field(min_length=1, max_length=100)
    operator: Literal["eq", "gt", "gte", "lt", "lte"]
    value: float
    unit: str | None = Field(default=None, max_length=32)
    split: str | None = Field(default=None, max_length=100)

    @field_validator("value")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("numeric predicate must be finite")
        return value


class ExperimentQueryV2(_FrozenModel):
    version: Literal["experiment-query-v2"] = EXPERIMENT_QUERY_VERSION
    raw: str = Field(min_length=1, max_length=2_000)
    mode: Literal["search", "best", "compare", "trend", "reproduce"] = "search"
    terms: tuple[str, ...] = ()
    metric_names: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    experiment_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    dataset_id: str | None = None
    dataset_version: str | None = None
    split: str | None = None
    unit: str | None = None
    config_filters: tuple[tuple[str, str], ...] = ()
    predicates: tuple[NumericPredicateV2, ...] = ()

    @model_validator(mode="after")
    def _bounded(self) -> ExperimentQueryV2:
        for values, label, maximum in (
            (self.terms, "terms", 32),
            (self.metric_names, "metric names", 12),
            (self.statuses, "statuses", 5),
            (self.experiment_ids, "experiment ids", 20),
            (self.run_ids, "run ids", 20),
            (self.config_filters, "config filters", 16),
            (self.predicates, "numeric predicates", 8),
        ):
            if len(values) > maximum:
                raise ValueError(f"too many {label}")
        return self


class MetricObservationV2(_FrozenModel):
    schema_version: Literal["experiment-metric-observation-v2"] = "experiment-metric-observation-v2"
    observation_id: str
    source_metric_id: str
    definition_id: str
    series_id: str
    run_id: str
    experiment_id: str
    name: str
    value: float
    unit: str | None = None
    split: str | None = None
    step: int | None = None
    higher_is_better: bool | None = None
    locator: str
    content_digest: str

    @field_validator("value")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("metric observation must be finite")
        return value


class ExperimentRunSnapshotV2(_FrozenModel):
    schema_version: Literal["experiment-run-snapshot-v2"] = EXPERIMENT_SNAPSHOT_VERSION
    run_id: str
    experiment_id: str
    project_id: str
    display_key: str
    name: str
    status: str
    dataset_id: str | None = None
    dataset_version: str | None = None
    repository_id: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    config: dict[str, Any]
    environment: dict[str, Any]
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str
    metrics: tuple[MetricObservationV2, ...]
    locator: str
    content_digest: str


class ExperimentComparabilityV2(_FrozenModel):
    schema_version: Literal["experiment-comparability-v2"] = "experiment-comparability-v2"
    state: ComparisonState
    comparable: bool
    run_ids: tuple[str, ...]
    metric: str | None = None
    reasons: tuple[str, ...]
    common_dataset: str | None = None
    common_dataset_version: str | None = None
    common_unit: str | None = None
    common_split: str | None = None
    content_digest: str


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(*values: Any) -> str:
    payload = "\x1f".join(_canonical(value) for value in values)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _safe_locator(kind: str, identity: str) -> str:
    token = hashlib.sha256(identity.encode()).hexdigest()
    return f"experiment-v2://{kind}/{token}"


def _semantic_tokens(value: str) -> set[str]:
    normalized = " ".join(value.casefold().split())
    words = {token for token in _TOKEN_RE.findall(normalized) if token not in _STOP_WORDS}
    compact = re.sub(r"\s+", " ", normalized)
    trigrams = {compact[index : index + 3] for index in range(max(0, len(compact) - 2))}
    return words | trigrams


def _semantic_similarity(left: str, right: str) -> float:
    left_tokens = _semantic_tokens(left)
    right_tokens = _semantic_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _metadata_completeness(snapshot: ExperimentRunSnapshotV2) -> float:
    fields = (
        snapshot.dataset_id,
        snapshot.dataset_version,
        snapshot.commit_sha,
        snapshot.config,
        snapshot.environment,
        snapshot.completed_at,
    )
    return sum(bool(value) for value in fields) / len(fields)


def validate_experiment_engine_override(value: str | None) -> str:
    normalized = (value or "v1").strip().lower()
    if normalized not in _ENGINE_VALUES:
        raise ExperimentEngineOverrideError(
            f"invalid {EXPERIMENT_ENGINE_HEADER}; expected v1 or v2"
        )
    return normalized


def parse_experiment_query_v2(raw: str) -> ExperimentQueryV2:
    query = raw.strip()
    if not query or len(query) > 2_000 or "\x00" in query:
        raise ExperimentQueryError("query must contain 1..2000 safe characters")
    lowered = query.casefold()
    mode: Literal["search", "best", "compare", "trend", "reproduce"] = "search"
    if any(token in lowered for token in ("best", "top", "最佳", "最优")):
        mode = "best"
    elif any(token in lowered for token in ("compare", " vs ", "比较", "对比")):
        mode = "compare"
    elif any(token in lowered for token in ("trend", "趋势")):
        mode = "trend"
    elif any(token in lowered for token in ("reproduce", "reproduction", "复现")):
        mode = "reproduce"

    filters: dict[str, list[str]] = defaultdict(list)
    config_filters: list[tuple[str, str]] = []
    spans: list[tuple[int, int]] = []
    for match in _FILTER_RE.finditer(query):
        key = match.group("key").casefold()
        value = match.group("value")
        spans.append(match.span())
        if key == "seed":
            config_filters.append(("seed", value))
        elif key.startswith("config."):
            config_filters.append((key.removeprefix("config."), value))
        elif key in {"dataset", "dataset_id"}:
            filters["dataset_id"].append(value)
        else:
            filters[key].append(value)

    predicates: list[NumericPredicateV2] = []
    for match in _NUMERIC_RE.finditer(query):
        if any(match.start() < end and match.end() > start for start, end in spans):
            continue
        spans.append(match.span())
        value = float(match.group("value"))
        if not math.isfinite(value):
            raise ExperimentQueryError("numeric predicate must be finite")
        predicates.append(
            NumericPredicateV2(
                metric=match.group("metric"),
                operator=_OPERATORS[match.group("operator")],
                value=value,
                unit=match.group("unit"),
                split=filters.get("split", [None])[-1],
            )
        )

    scrubbed = list(query)
    for start, end in spans:
        scrubbed[start:end] = " " * (end - start)
    terms = tuple(
        dict.fromkeys(
            token.casefold()
            for token in _TOKEN_RE.findall("".join(scrubbed))
            if token.casefold() not in _STOP_WORDS and len(token) <= 120
        )
    )
    metric_names = tuple(
        dict.fromkeys(
            (
                *(value.casefold() for value in filters.get("metric", [])),
                *(predicate.metric.casefold() for predicate in predicates),
            )
        )
    )
    statuses = tuple(
        dict.fromkeys(
            value.casefold()
            for value in filters.get("status", [])
            if value.casefold() in _STATUS_VALUES
        )
    )
    if len(statuses) != len(filters.get("status", [])):
        raise ExperimentQueryError("unknown run status")
    return ExperimentQueryV2(
        raw=query,
        mode=mode,
        terms=terms,
        metric_names=metric_names,
        statuses=statuses,
        experiment_ids=tuple(dict.fromkeys(filters.get("experiment_id", []))),
        run_ids=tuple(dict.fromkeys(filters.get("run_id", []))),
        dataset_id=(filters.get("dataset_id") or [None])[-1],
        dataset_version=(filters.get("dataset_version") or [None])[-1],
        split=(filters.get("split") or [None])[-1],
        unit=(filters.get("unit") or [None])[-1],
        config_filters=tuple(dict.fromkeys(config_filters)),
        predicates=tuple(predicates),
    )


def _metric_observation(run: dict[str, Any], metric: dict[str, Any]) -> MetricObservationV2:
    name = str(metric["name"])
    unit = str(metric["unit"]) if metric.get("unit") is not None else None
    split = str(metric["split"]) if metric.get("split") is not None else None
    step = int(metric["step"]) if metric.get("step") is not None else None
    raw_direction = metric.get("higher_is_better")
    higher = (
        raw_direction
        if isinstance(raw_direction, bool)
        else bool(raw_direction)
        if type(raw_direction) is int and raw_direction in {0, 1}
        else None
    )
    direction = "higher" if higher is True else "lower" if higher is False else "unknown"
    definition_id = (
        "metric-definition://" + _digest(name.casefold(), unit, direction).split(":", 1)[1]
    )
    series_id = "metric-series://" + _digest(run["id"], definition_id, split).split(":", 1)[1]
    payload = {
        "source_metric_id": metric["id"],
        "definition_id": definition_id,
        "series_id": series_id,
        "run_id": run["id"],
        "experiment_id": run["experiment_id"],
        "name": name,
        "value": float(metric["value"]),
        "unit": unit,
        "split": split,
        "step": step,
        "higher_is_better": higher,
    }
    content_digest = _digest(payload)
    observation_id = "metric-observation://" + content_digest.split(":", 1)[1]
    return MetricObservationV2(
        **payload,
        observation_id=observation_id,
        locator=_safe_locator("metric", observation_id),
        content_digest=content_digest,
    )


def build_experiment_run_snapshot_v2(run: dict[str, Any]) -> ExperimentRunSnapshotV2:
    metrics = tuple(
        sorted(
            (_metric_observation(run, metric) for metric in run.get("metrics", [])),
            key=lambda item: (item.name.casefold(), item.split or "", item.step or -1),
        )
    )
    payload = {
        "run_id": str(run["id"]),
        "experiment_id": str(run["experiment_id"]),
        "project_id": str(run["project_id"]),
        "display_key": str(run["display_key"]),
        "name": str(run["name"]),
        "status": str(run["status"]),
        "dataset_id": run.get("dataset_id"),
        "dataset_version": run.get("dataset_version"),
        "repository_id": run.get("repository_id"),
        "commit_sha": run.get("commit_sha"),
        "branch": run.get("branch"),
        "config": dict(run.get("config") or {}),
        "environment": dict(run.get("environment") or {}),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "updated_at": str(run["updated_at"]),
        "metric_digests": [metric.content_digest for metric in metrics],
    }
    content_digest = _digest(payload)
    return ExperimentRunSnapshotV2(
        **{key: value for key, value in payload.items() if key != "metric_digests"},
        metrics=metrics,
        locator=_safe_locator("run", str(run["id"])),
        content_digest=content_digest,
    )


def compare_experiment_runs_v2(
    snapshots: tuple[ExperimentRunSnapshotV2, ...], metric: str | None = None
) -> ExperimentComparabilityV2:
    reasons: list[str] = []
    if len(snapshots) < 2:
        reasons.append("requires_at_least_two_runs")
    if snapshots and len({item.experiment_id for item in snapshots}) != 1:
        reasons.append("different_experiments")
    if any(item.status != "completed" for item in snapshots):
        reasons.append("non_completed_run")
    datasets = {item.dataset_id for item in snapshots}
    dataset_versions = {item.dataset_version for item in snapshots}
    if None in datasets or len(datasets) != 1:
        reasons.append("dataset_mismatch_or_missing")
    if None in dataset_versions or len(dataset_versions) != 1:
        reasons.append("dataset_version_mismatch_or_missing")
    environment_digests = {_digest(item.environment) for item in snapshots}
    if len(environment_digests) > 1:
        reasons.append("environment_mismatch")

    normalized_metric = metric.casefold() if metric else None
    selected: list[MetricObservationV2] = []
    if normalized_metric:
        for snapshot in snapshots:
            matches = [
                item for item in snapshot.metrics if item.name.casefold() == normalized_metric
            ]
            if len(matches) != 1:
                reasons.append("metric_missing_or_ambiguous")
                break
            selected.append(matches[0])
        if selected:
            normalized_units = {_normalized_numeric(item.value, item.unit)[1] for item in selected}
            if len(normalized_units) != 1:
                reasons.append("metric_unit_mismatch")
            if len({item.split for item in selected}) != 1:
                reasons.append("metric_split_mismatch")
            if len({item.higher_is_better for item in selected}) != 1:
                reasons.append("metric_direction_mismatch")
            if any(item.higher_is_better is None for item in selected):
                reasons.append("metric_direction_unknown")
    reasons = list(dict.fromkeys(reasons))
    payload = {
        "run_ids": [item.run_id for item in snapshots],
        "metric": normalized_metric,
        "reasons": reasons,
        "dataset": next(iter(datasets)) if len(datasets) == 1 else None,
        "dataset_version": next(iter(dataset_versions)) if len(dataset_versions) == 1 else None,
        "unit": (
            _normalized_numeric(selected[0].value, selected[0].unit)[1]
            if selected
            and len({_normalized_numeric(item.value, item.unit)[1] for item in selected}) == 1
            else None
        ),
        "split": selected[0].split
        if selected and len({item.split for item in selected}) == 1
        else None,
    }
    return ExperimentComparabilityV2(
        state=ComparisonState.AVAILABLE if not reasons else ComparisonState.UNAVAILABLE,
        comparable=not reasons,
        run_ids=tuple(payload["run_ids"]),
        metric=normalized_metric,
        reasons=tuple(reasons),
        common_dataset=payload["dataset"],
        common_dataset_version=payload["dataset_version"],
        common_unit=payload["unit"],
        common_split=payload["split"],
        content_digest=_digest(payload),
    )


def _coerce_filter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _normalized_numeric(value: float, unit: str | None) -> tuple[float, str | None]:
    if unit is None:
        return value, None
    normalized = unit.casefold()
    family, factor = _UNIT_NORMALIZATION.get(normalized, (normalized, 1.0))
    return value * factor, family


def _predicate_matches(predicate: NumericPredicateV2, metric: MetricObservationV2) -> bool:
    if metric.name.casefold() != predicate.metric.casefold():
        return False
    metric_value, metric_unit = _normalized_numeric(metric.value, metric.unit)
    predicate_value, predicate_unit = _normalized_numeric(predicate.value, predicate.unit)
    if predicate.unit and metric_unit != predicate_unit:
        return False
    if predicate.split and (metric.split or "").casefold() != predicate.split.casefold():
        return False
    if predicate.operator == "eq":
        return math.isclose(metric_value, predicate_value, rel_tol=1e-12, abs_tol=1e-12)
    if predicate.operator == "gt":
        return metric_value > predicate_value
    if predicate.operator == "gte":
        return metric_value >= predicate_value
    if predicate.operator == "lt":
        return metric_value < predicate_value
    return metric_value <= predicate_value


class ExperimentStructuredRetrieverV2:
    """Read-only typed Experiment retriever over the existing V1 store."""

    def __init__(self, store: ExperimentStore) -> None:
        self.store = store

    def _project_visible(
        self,
        project_id: str,
        *,
        allowed_acl_refs: list[str],
        enforce_acl: bool,
    ) -> bool:
        if not enforce_acl:
            return True
        with self.store.database.connection() as db:
            project = db.execute(
                "SELECT acl_ref FROM projects WHERE id=?", (project_id,)
            ).fetchone()
        if not project:
            return False
        acl_ref = str(project["acl_ref"])
        return acl_ref == "public" or acl_ref in set(allowed_acl_refs)

    def _snapshots(self, project_id: str) -> tuple[ExperimentRunSnapshotV2, ...]:
        summaries = self.store.list_runs(project_id)[:2_000]
        snapshots: list[ExperimentRunSnapshotV2] = []
        for summary in summaries:
            run = self.store.get_run(str(summary["id"]))
            if run is None:
                continue
            try:
                snapshots.append(build_experiment_run_snapshot_v2(run))
            except (ValueError, TypeError, OverflowError):
                continue
        return tuple(snapshots)

    @staticmethod
    def _matches_filters(snapshot: ExperimentRunSnapshotV2, query: ExperimentQueryV2) -> bool:
        if query.mode == "best" and not query.statuses and snapshot.status != "completed":
            return False
        if query.statuses and snapshot.status not in query.statuses:
            return False
        if query.experiment_ids and snapshot.experiment_id not in query.experiment_ids:
            return False
        if (
            query.run_ids
            and snapshot.run_id not in query.run_ids
            and snapshot.display_key not in query.run_ids
        ):
            return False
        if query.dataset_id and snapshot.dataset_id != query.dataset_id:
            return False
        if query.dataset_version and snapshot.dataset_version != query.dataset_version:
            return False
        for key, expected in query.config_filters:
            if _coerce_filter_value(snapshot.config.get(key)) != expected:
                return False
        for predicate in query.predicates:
            if not any(_predicate_matches(predicate, metric) for metric in snapshot.metrics):
                return False
        return True

    @staticmethod
    def _text_score(
        snapshot: ExperimentRunSnapshotV2, query: ExperimentQueryV2
    ) -> tuple[int, list[str]]:
        metric_text = " ".join(
            f"{item.name} {item.value} {item.unit or ''} {item.split or ''}"
            for item in snapshot.metrics
        )
        haystack = " ".join(
            (
                snapshot.run_id,
                snapshot.experiment_id,
                snapshot.display_key,
                snapshot.name,
                snapshot.status,
                snapshot.dataset_id or "",
                snapshot.dataset_version or "",
                snapshot.commit_sha or "",
                _canonical(snapshot.config),
                metric_text,
            )
        ).casefold()
        matched = [term for term in query.terms if term in haystack]
        return len(matched), matched

    @staticmethod
    def _selected_metrics(
        snapshot: ExperimentRunSnapshotV2, query: ExperimentQueryV2
    ) -> tuple[MetricObservationV2, ...]:
        requested = set(query.metric_names)
        requested.update(predicate.metric.casefold() for predicate in query.predicates)
        if not requested and query.mode in {"best", "trend", "compare"}:
            metric_terms = set(query.terms)
            requested.update(
                item.name.casefold()
                for item in snapshot.metrics
                if item.name.casefold() in metric_terms
                or any(term in item.name.casefold() for term in metric_terms)
            )
        if requested:
            return tuple(item for item in snapshot.metrics if item.name.casefold() in requested)
        return ()

    def search(
        self,
        *,
        project_id: str,
        query: str,
        experiment_ids: list[str] | None = None,
        allowed_acl_refs: list[str] | None = None,
        enforce_acl: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        parsed = parse_experiment_query_v2(query)
        if experiment_ids:
            scoped_ids = tuple(dict.fromkeys(experiment_ids))
            scoped_id_set = set(scoped_ids)
            resolved_ids = (
                tuple(value for value in parsed.experiment_ids if value in scoped_id_set)
                if parsed.experiment_ids
                else scoped_ids
            )
            if not resolved_ids:
                return {
                    "results": [],
                    "trace": {
                        "engine": EXPERIMENT_V2_VERSION,
                        "status": "SCOPE_MISMATCH",
                        "parsed": parsed.model_dump(mode="json", exclude={"raw"}),
                        "candidate_runs": 0,
                        "read_only": True,
                        "schema_migration": False,
                    },
                }
            parsed = parsed.model_copy(update={"experiment_ids": resolved_ids})
        bounded_limit = min(100, max(1, int(limit)))
        if not self._project_visible(
            project_id,
            allowed_acl_refs=list(allowed_acl_refs or []),
            enforce_acl=enforce_acl,
        ):
            return {
                "results": [],
                "trace": {
                    "engine": EXPERIMENT_V2_VERSION,
                    "status": "ACL_DENIED",
                    "parsed": parsed.model_dump(mode="json", exclude={"raw"}),
                    "candidate_runs": 0,
                },
            }

        snapshots = self._snapshots(project_id)
        candidates: list[
            tuple[
                ExperimentRunSnapshotV2,
                int,
                float,
                list[str],
                tuple[MetricObservationV2, ...],
            ]
        ] = []
        structured = bool(
            parsed.statuses
            or parsed.experiment_ids
            or parsed.run_ids
            or parsed.dataset_id
            or parsed.dataset_version
            or parsed.config_filters
            or parsed.predicates
        )
        for snapshot in snapshots:
            if not self._matches_filters(snapshot, parsed):
                continue
            text_score, matched = self._text_score(snapshot, parsed)
            selected_metrics = self._selected_metrics(snapshot, parsed)
            surface = " ".join(
                (
                    snapshot.name,
                    snapshot.dataset_id or "",
                    snapshot.dataset_version or "",
                    _canonical(snapshot.config),
                    " ".join(metric.name for metric in snapshot.metrics),
                )
            )
            semantic_score = _semantic_similarity(query, surface)
            if (
                not structured
                and parsed.terms
                and text_score == 0
                and semantic_score < 0.02
                and not selected_metrics
            ):
                continue
            candidates.append((snapshot, text_score, semantic_score, matched, selected_metrics))

        best_metric = next(
            iter(
                parsed.metric_names
                or tuple(predicate.metric.casefold() for predicate in parsed.predicates)
            ),
            None,
        )
        if not best_metric and parsed.mode in {"best", "trend", "compare"}:
            for _snapshot, _score, _semantic, _matched, metrics in candidates:
                if metrics:
                    best_metric = metrics[0].name.casefold()
                    break

        ranking_blocker = None
        if parsed.mode == "best" and best_metric:
            selected_directions = [
                metric.higher_is_better
                for snapshot, _score, _semantic, _matched, _metrics in candidates
                for metric in snapshot.metrics
                if metric.name.casefold() == best_metric
            ]
            if not selected_directions or any(item is None for item in selected_directions):
                ranking_blocker = "metric_direction_unknown"

        if parsed.mode == "best" and best_metric and ranking_blocker is None:

            def best_key(
                item: tuple[
                    ExperimentRunSnapshotV2,
                    int,
                    float,
                    list[str],
                    tuple[MetricObservationV2, ...],
                ],
            ) -> tuple[float, int, float, str]:
                metrics = [
                    metric for metric in item[0].metrics if metric.name.casefold() == best_metric
                ]
                if not metrics:
                    return (float("-inf"), item[1], item[2], item[0].run_id)
                metric = metrics[-1]
                normalized, _unit = _normalized_numeric(metric.value, metric.unit)
                oriented = normalized if metric.higher_is_better is True else -normalized
                return (oriented, item[1], item[2], item[0].run_id)

            candidates.sort(key=best_key, reverse=True)
        else:
            candidates.sort(
                key=lambda item: (
                    item[1],
                    item[2],
                    _metadata_completeness(item[0]),
                    item[0].completed_at or "",
                    item[0].run_id,
                ),
                reverse=True,
            )

        results: list[dict[str, Any]] = []
        for rank, (snapshot, text_score, semantic_score, matched, selected_metrics) in enumerate(
            candidates, start=1
        ):
            selected = selected_metrics or snapshot.metrics[:4]
            metric_summary = ", ".join(
                f"{metric.name}={metric.value:g}{metric.unit or ''}" for metric in selected[:6]
            )
            score = round(
                min(
                    0.99,
                    0.42
                    + 0.06 * text_score
                    + 0.20 * semantic_score
                    + 0.10 * _metadata_completeness(snapshot)
                    + 0.08 * bool(structured)
                    + 0.01 / rank,
                ),
                6,
            )
            negative_reasons = tuple(
                reason
                for reason, present in (
                    ("incomplete_run", snapshot.status != "completed"),
                    ("missing_dataset_version", not snapshot.dataset_version),
                    ("missing_commit", not snapshot.commit_sha),
                )
                if present
            )
            results.append(
                {
                    "entity_id": snapshot.run_id,
                    "source": "experiment",
                    "entity_type": "ExperimentRunV2",
                    "title": snapshot.name,
                    "subtitle": f"{snapshot.display_key} · {snapshot.status}",
                    "snippet": " · ".join(
                        value
                        for value in (
                            f"dataset={snapshot.dataset_id}@{snapshot.dataset_version}",
                            metric_summary,
                        )
                        if value
                    )[:1_200],
                    "locator": snapshot.locator,
                    "status": snapshot.status,
                    "updated_at": snapshot.updated_at,
                    "version": snapshot.content_digest,
                    "score": score,
                    "channels": [
                        "experiment_typed_v2",
                        "experiment_semantic_surface_v1",
                        "experiment_profile_rerank_v1",
                        *(["numeric_filter"] if parsed.predicates else []),
                    ],
                    "matched_terms": matched,
                    "semantic_score": round(semantic_score, 6),
                    "metadata_completeness": round(_metadata_completeness(snapshot), 6),
                    "negative_reasons": list(negative_reasons),
                    "snapshot_digest": snapshot.content_digest,
                }
            )
            for metric in selected_metrics[:4]:
                results.append(
                    {
                        "entity_id": metric.source_metric_id,
                        "source": "experiment",
                        "entity_type": "MetricObservationV2",
                        "title": metric.name,
                        "subtitle": snapshot.display_key,
                        "snippet": f"{metric.name} = {metric.value:g}{metric.unit or ''}",
                        "locator": metric.locator,
                        "status": "observed",
                        "updated_at": snapshot.updated_at,
                        "version": metric.content_digest,
                        "score": min(0.999, score + 0.015),
                        "channels": ["experiment_metric_v2", "typed_exact"],
                        "matched_terms": [metric.name.casefold()],
                        "run_id": snapshot.run_id,
                        "snapshot_digest": snapshot.content_digest,
                    }
                )
            if len(results) >= bounded_limit * 2:
                break
        results.sort(key=lambda item: (float(item["score"]), str(item["entity_id"])), reverse=True)
        results = results[:bounded_limit]

        comparison = None
        selected_snapshots = tuple(item[0] for item in candidates[:20])
        if parsed.mode == "compare" or (best_metric and len(selected_snapshots) >= 2):
            comparison = compare_experiment_runs_v2(selected_snapshots, best_metric)
        context_runs = []
        for snapshot, _lexical, _semantic, _matched, selected_metrics in candidates[:8]:
            metrics = selected_metrics or snapshot.metrics[:4]
            context_runs.append(
                {
                    "run_id": snapshot.run_id,
                    "experiment_id": snapshot.experiment_id,
                    "status": snapshot.status,
                    "dataset": {
                        "id": snapshot.dataset_id,
                        "version": snapshot.dataset_version,
                    },
                    "commit": snapshot.commit_sha,
                    "metrics": [
                        {
                            "observation_id": metric.observation_id,
                            "name": metric.name,
                            "value": metric.value,
                            "unit": metric.unit,
                            "split": metric.split,
                            "higher_is_better": metric.higher_is_better,
                            "locator": metric.locator,
                        }
                        for metric in metrics[:6]
                    ],
                    "missing_roles": [
                        role
                        for role, missing in (
                            ("dataset_version", not snapshot.dataset_version),
                            ("commit", not snapshot.commit_sha),
                            ("environment", not snapshot.environment),
                            ("metric_observation", not metrics),
                        )
                        if missing
                    ],
                    "locator": snapshot.locator,
                    "snapshot_digest": snapshot.content_digest,
                }
            )
        return {
            "results": results,
            "context": {
                "schema_version": "experiment-structured-context-v1",
                "task": parsed.mode,
                "runs": context_runs,
                "comparison": comparison.model_dump(mode="json") if comparison else None,
                "retrieval_context_separated": True,
                "reasoning_included": False,
            },
            "trace": {
                "engine": EXPERIMENT_V2_VERSION,
                "status": "AVAILABLE",
                "parsed": parsed.model_dump(mode="json", exclude={"raw"}),
                "candidate_runs": len(candidates),
                "snapshot_count": len(snapshots),
                "comparison": comparison.model_dump(mode="json") if comparison else None,
                "ranking_status": ("unavailable" if ranking_blocker is not None else "available"),
                "ranking_blocker": ranking_blocker,
                "semantic_surface": "local-token-trigram-v1",
                "reranker": "experiment-profile-rerank-v1",
                "release": dict(_EXPERIMENT_RELEASE_DECISION),
                "read_only": True,
                "schema_migration": False,
            },
        }


__all__ = [
    "ComparisonState",
    "EXPERIMENT_ENGINE_HEADER",
    "EXPERIMENT_QUERY_VERSION",
    "EXPERIMENT_SNAPSHOT_VERSION",
    "EXPERIMENT_V2_VERSION",
    "ExperimentComparabilityV2",
    "ExperimentEngineOverrideError",
    "ExperimentQueryError",
    "ExperimentQueryV2",
    "ExperimentRunSnapshotV2",
    "ExperimentStructuredRetrieverV2",
    "ExperimentV2Error",
    "MetricObservationV2",
    "NumericPredicateV2",
    "build_experiment_run_snapshot_v2",
    "compare_experiment_runs_v2",
    "parse_experiment_query_v2",
    "validate_experiment_engine_override",
]
