"""Strict Experiment E3 comparability, aggregation, and reproduction truth."""

from __future__ import annotations

import math
import statistics
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts_v2 import (
    ArtifactVerificationStateV2,
    ExperimentMetricObservationV2,
    ExperimentRunGroupV2,
    ExperimentRunSnapshotV2,
    MetricDirectionV2,
    canonical_sha256_v2,
)

EXPERIMENT_COMPARABILITY_POLICY_VERSION = "experiment-comparability-policy-v2"
EXPERIMENT_AGGREGATION_POLICY_VERSION = "experiment-aggregation-policy-v2"
EXPERIMENT_REPRODUCTION_POLICY_VERSION = "experiment-reproduction-policy-v2"


class ComparabilityDecisionV2(StrEnum):
    COMPARABLE = "comparable"
    COMPARABLE_WITH_CAVEATS = "comparable_with_caveats"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_METADATA = "insufficient_metadata"


class DifferenceClassV2(StrEnum):
    CONTROLLED = "controlled"
    TREATMENT = "treatment"
    UNKNOWN = "unknown"
    DISQUALIFYING = "disqualifying"


class ReproductionRoleStateV2(StrEnum):
    PRESENT_VERIFIED = "present_verified"
    PRESENT_UNVERIFIED = "present_unverified"
    MISSING = "missing"
    INACCESSIBLE = "inaccessible"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, StrEnum):
        return value.value
    return value


class ExperimentDifferenceV2(_Frozen):
    field: str
    classification: DifferenceClassV2
    baseline_sha256: str
    candidate_sha256: str
    baseline_locator: str
    candidate_locator: str


class ExperimentComparabilityResultV2(_Frozen):
    decision: ComparabilityDecisionV2
    baseline_snapshot_id: str
    candidate_snapshot_id: str
    metric_definition_id: str | None
    differences: tuple[ExperimentDifferenceV2, ...]
    missing_fields: tuple[str, ...]
    policy_version: str = EXPERIMENT_COMPARABILITY_POLICY_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentComparabilityResultV2:
        if self.missing_fields != tuple(sorted(set(self.missing_fields))):
            raise ValueError("missing comparability fields must be sorted and unique")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("comparability result digest mismatch")
        if self.decision in {
            ComparabilityDecisionV2.NOT_COMPARABLE,
            ComparabilityDecisionV2.INSUFFICIENT_METADATA,
        } and not (self.differences or self.missing_fields):
            raise ValueError("negative comparability decision requires evidence")
        return self


class ExperimentAggregationResultV2(_Frozen):
    status: Literal["available", "unavailable"]
    run_group_id: str
    metric_definition_id: str
    included_run_ids: tuple[str, ...]
    excluded_runs: tuple[tuple[str, str], ...]
    observation_ids: tuple[str, ...]
    function: Literal["mean", "median", "std", "min", "max", "count"]
    n: int
    value: float | None
    mean: float | None
    median: float | None
    std: float | None
    confidence_interval_95: tuple[float, float] | None
    unit: str | None
    split: str | None
    reason: str | None
    policy_version: str = EXPERIMENT_AGGREGATION_POLICY_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentAggregationResultV2:
        if self.n != len(self.observation_ids):
            raise ValueError("aggregation denominator does not match observations")
        if self.status == "available" and self.value is None:
            raise ValueError("available aggregation requires a value")
        if self.status == "unavailable" and self.reason is None:
            raise ValueError("unavailable aggregation requires a reason")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("aggregation digest mismatch")
        return self


class ExperimentReproductionRoleV2(_Frozen):
    role: Literal[
        "command",
        "code_commit",
        "dataset_version",
        "config_snapshot",
        "environment_snapshot",
        "seed",
        "required_artifacts",
    ]
    state: ReproductionRoleStateV2
    evidence_ids: tuple[str, ...]
    reason: str | None


class ExperimentReproductionResultV2(_Frozen):
    status: Literal["reproducible", "insufficient_metadata"]
    run_snapshot_id: str
    roles: tuple[ExperimentReproductionRoleV2, ...] = Field(min_length=7, max_length=7)
    policy_version: str = EXPERIMENT_REPRODUCTION_POLICY_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentReproductionResultV2:
        if len({role.role for role in self.roles}) != 7:
            raise ValueError("reproduction roles are not complete")
        if self.status == "reproducible" and any(
            role.state is not ReproductionRoleStateV2.PRESENT_VERIFIED for role in self.roles
        ):
            raise ValueError("reproducible status requires every role verified")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("reproduction digest mismatch")
        return self


class ExperimentComparisonV2(_Frozen):
    status: Literal["available", "unavailable"]
    comparability: ExperimentComparabilityResultV2
    baseline_observation_id: str | None
    candidate_observation_id: str | None
    baseline_value: float | None
    candidate_value: float | None
    delta: float | None
    relative_delta: float | None
    improvement: bool | None
    unit: str | None
    reason: str | None
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ExperimentComparisonV2:
        if self.status == "unavailable" and any(
            value is not None for value in (self.delta, self.relative_delta, self.improvement)
        ):
            raise ValueError("unavailable comparison cannot assert change")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("comparison digest mismatch")
        return self


def _locator(snapshot: ExperimentRunSnapshotV2, field: str) -> str:
    return f"experiment-v2://snapshot/{snapshot.run_snapshot_id}/{field}"


def _hash_value(value: object) -> str:
    return canonical_sha256_v2({"value": value})


def compare_experiment_snapshots_v2(
    baseline: ExperimentRunSnapshotV2,
    candidate: ExperimentRunSnapshotV2,
    *,
    metric_definition_id: str | None,
    treatment_config_keys: frozenset[str] = frozenset(),
    controlled_config_keys: frozenset[str] = frozenset(),
) -> ExperimentComparabilityResultV2:
    """Classify every relevant difference before any improvement is computed."""

    if (
        baseline.project_id != candidate.project_id
        or baseline.acl_ref != candidate.acl_ref
        or baseline.source_id != candidate.source_id
    ):
        raise ValueError("comparability inputs cross governed scope")
    differences: list[ExperimentDifferenceV2] = []
    missing: set[str] = set()

    def compare_field(
        field: str,
        left: object,
        right: object,
        classification: DifferenceClassV2,
        *,
        required: bool = True,
    ) -> None:
        if required and (left is None or right is None):
            missing.add(field)
            return
        if left != right:
            differences.append(
                ExperimentDifferenceV2(
                    field=field,
                    classification=classification,
                    baseline_sha256=_hash_value(left),
                    candidate_sha256=_hash_value(right),
                    baseline_locator=_locator(baseline, field),
                    candidate_locator=_locator(candidate, field),
                )
            )

    compare_field(
        "experiment_id",
        baseline.experiment_id,
        candidate.experiment_id,
        DifferenceClassV2.DISQUALIFYING,
    )
    compare_field(
        "dataset.id",
        baseline.dataset.dataset_id,
        candidate.dataset.dataset_id,
        DifferenceClassV2.DISQUALIFYING,
    )
    compare_field(
        "dataset.version",
        baseline.dataset.version,
        candidate.dataset.version,
        DifferenceClassV2.DISQUALIFYING,
    )
    compare_field(
        "dataset.split",
        baseline.dataset.split,
        candidate.dataset.split,
        DifferenceClassV2.DISQUALIFYING,
        required=False,
    )
    compare_field(
        "dataset.preprocessing",
        baseline.dataset.preprocessing_sha256,
        candidate.dataset.preprocessing_sha256,
        DifferenceClassV2.DISQUALIFYING,
        required=False,
    )
    compare_field(
        "repository_id",
        baseline.repository_id,
        candidate.repository_id,
        DifferenceClassV2.DISQUALIFYING,
    )
    compare_field(
        "commit_sha",
        baseline.commit_sha,
        candidate.commit_sha,
        DifferenceClassV2.TREATMENT,
    )
    compare_field(
        "environment",
        baseline.environment.compatibility_sha256,
        candidate.environment.compatibility_sha256,
        DifferenceClassV2.UNKNOWN,
    )
    if baseline.status != "completed":
        differences.append(
            ExperimentDifferenceV2(
                field="baseline.status",
                classification=DifferenceClassV2.DISQUALIFYING,
                baseline_sha256=_hash_value(baseline.status),
                candidate_sha256=_hash_value("completed"),
                baseline_locator=_locator(baseline, "status"),
                candidate_locator=_locator(candidate, "status"),
            )
        )
    if candidate.status != "completed":
        differences.append(
            ExperimentDifferenceV2(
                field="candidate.status",
                classification=DifferenceClassV2.DISQUALIFYING,
                baseline_sha256=_hash_value("completed"),
                candidate_sha256=_hash_value(candidate.status),
                baseline_locator=_locator(baseline, "status"),
                candidate_locator=_locator(candidate, "status"),
            )
        )
    left_config = dict(baseline.config.values)
    right_config = dict(candidate.config.values)
    for key in sorted(left_config.keys() | right_config.keys()):
        if key not in left_config or key not in right_config:
            missing.add(f"config.{key}")
            continue
        if key in treatment_config_keys:
            classification = DifferenceClassV2.TREATMENT
        elif key in controlled_config_keys:
            classification = DifferenceClassV2.CONTROLLED
        else:
            classification = DifferenceClassV2.UNKNOWN
        compare_field(
            f"config.{key}",
            left_config[key],
            right_config[key],
            classification,
            required=False,
        )
    if metric_definition_id is not None:
        left_def = {item.definition_id: item for item in baseline.definitions}.get(
            metric_definition_id
        )
        right_def = {item.definition_id: item for item in candidate.definitions}.get(
            metric_definition_id
        )
        if left_def is None or right_def is None:
            missing.add("metric_definition")
        else:
            compare_field(
                "metric.definition",
                left_def.content_sha256,
                right_def.content_sha256,
                DifferenceClassV2.DISQUALIFYING,
            )
            compare_field(
                "metric.unit",
                left_def.unit,
                right_def.unit,
                DifferenceClassV2.DISQUALIFYING,
                required=False,
            )
    if missing:
        decision = ComparabilityDecisionV2.INSUFFICIENT_METADATA
    elif any(item.classification is DifferenceClassV2.DISQUALIFYING for item in differences):
        decision = ComparabilityDecisionV2.NOT_COMPARABLE
    elif any(item.classification is DifferenceClassV2.UNKNOWN for item in differences):
        decision = ComparabilityDecisionV2.COMPARABLE_WITH_CAVEATS
    else:
        decision = ComparabilityDecisionV2.COMPARABLE
    payload = {
        "decision": decision,
        "baseline_snapshot_id": baseline.run_snapshot_id,
        "candidate_snapshot_id": candidate.run_snapshot_id,
        "metric_definition_id": metric_definition_id,
        "differences": tuple(sorted(differences, key=lambda item: item.field)),
        "missing_fields": tuple(sorted(missing)),
        "policy_version": EXPERIMENT_COMPARABILITY_POLICY_VERSION,
    }
    return ExperimentComparabilityResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(_json_value(payload)),
    )


def aggregate_experiment_run_group_v2(
    group: ExperimentRunGroupV2,
    snapshots: tuple[ExperimentRunSnapshotV2, ...],
    *,
    metric_definition_id: str,
    function: Literal["mean", "median", "std", "min", "max", "count"] = "mean",
    split: str | None = None,
) -> ExperimentAggregationResultV2:
    """Aggregate one final observation per explicitly included group member."""

    by_id = {item.run_snapshot_id: item for item in snapshots}
    included_run_ids: list[str] = []
    observation_ids: list[str] = []
    values: list[float] = []
    units: set[str | None] = set()
    excluded: list[tuple[str, str]] = []
    for member in group.members:
        if not member.included:
            excluded.append((member.run_id, member.exclusion_reason or "explicitly_excluded"))
            continue
        snapshot = by_id.get(member.run_snapshot_id)
        if snapshot is None:
            excluded.append((member.run_id, "snapshot_missing"))
            continue
        if snapshot.status != "completed":
            excluded.append((member.run_id, f"status:{snapshot.status}"))
            continue
        observations = sorted(
            (
                item
                for item in snapshot.observations
                if item.definition_id == metric_definition_id
                and item.valid
                and item.numeric_value is not None
                and (split is None or item.split == split)
            ),
            key=lambda item: (
                item.step is None,
                item.step if item.step is not None else -1,
                item.observed_at or "",
                item.observation_id,
            ),
        )
        if not observations:
            excluded.append((member.run_id, "valid_observation_missing"))
            continue
        observation = observations[-1]
        units.add(observation.canonical_unit)
        included_run_ids.append(member.run_id)
        observation_ids.append(observation.observation_id)
        values.append(observation.numeric_value)
    reason: str | None = None
    value: float | None = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    confidence: tuple[float, float] | None = None
    if not values:
        reason = "no_included_observations"
    elif len(units) != 1:
        reason = "unit_mismatch"
    else:
        mean = statistics.fmean(values)
        median = statistics.median(values)
        std = statistics.stdev(values) if len(values) > 1 else 0.0
        if len(values) > 1:
            half_width = 1.96 * std / math.sqrt(len(values))
            confidence = (mean - half_width, mean + half_width)
        value = {
            "mean": mean,
            "median": median,
            "std": std,
            "min": min(values),
            "max": max(values),
            "count": float(len(values)),
        }[function]
    status: Literal["available", "unavailable"] = (
        "available" if value is not None and math.isfinite(value) else "unavailable"
    )
    payload = {
        "status": status,
        "run_group_id": group.run_group_id,
        "metric_definition_id": metric_definition_id,
        "included_run_ids": tuple(included_run_ids),
        "excluded_runs": tuple(sorted(excluded)),
        "observation_ids": tuple(observation_ids),
        "function": function,
        "n": len(observation_ids),
        "value": value,
        "mean": mean,
        "median": median,
        "std": std,
        "confidence_interval_95": confidence,
        "unit": next(iter(units)) if len(units) == 1 else None,
        "split": split,
        "reason": reason,
        "policy_version": EXPERIMENT_AGGREGATION_POLICY_VERSION,
    }
    return ExperimentAggregationResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(payload),
    )


def evaluate_reproduction_v2(
    snapshot: ExperimentRunSnapshotV2,
) -> ExperimentReproductionResultV2:
    config = dict(snapshot.config.values)
    artifact_ids = tuple(item.artifact_version_id for item in snapshot.artifacts)
    artifacts_state = (
        ReproductionRoleStateV2.MISSING
        if not snapshot.artifacts
        else ReproductionRoleStateV2.INACCESSIBLE
        if any(
            item.verification_state is ArtifactVerificationStateV2.INACCESSIBLE
            for item in snapshot.artifacts
        )
        else ReproductionRoleStateV2.PRESENT_VERIFIED
        if all(
            item.verification_state is ArtifactVerificationStateV2.PRESENT_VERIFIED
            for item in snapshot.artifacts
        )
        else ReproductionRoleStateV2.PRESENT_UNVERIFIED
    )

    def role(
        name: str,
        present: bool,
        evidence: tuple[str, ...],
        *,
        verified: bool = True,
        reason: str | None = None,
    ) -> ExperimentReproductionRoleV2:
        state = (
            ReproductionRoleStateV2.PRESENT_VERIFIED
            if present and verified
            else ReproductionRoleStateV2.PRESENT_UNVERIFIED
            if present
            else ReproductionRoleStateV2.MISSING
        )
        return ExperimentReproductionRoleV2(
            role=name,  # type: ignore[arg-type]
            state=state,
            evidence_ids=evidence,
            reason=None if state is ReproductionRoleStateV2.PRESENT_VERIFIED else reason,
        )

    roles = (
        role(
            "command",
            snapshot.command_present,
            (snapshot.run_snapshot_id,) if snapshot.command_present else (),
            reason="command_missing",
        ),
        role(
            "code_commit",
            bool(snapshot.repository_id and snapshot.commit_sha),
            (snapshot.run_snapshot_id,) if snapshot.repository_id and snapshot.commit_sha else (),
            reason="repository_or_commit_missing",
        ),
        role(
            "dataset_version",
            snapshot.dataset.completeness == "complete",
            (snapshot.dataset.dataset_version_id,),
            reason=f"dataset:{snapshot.dataset.completeness}",
        ),
        role(
            "config_snapshot",
            bool(snapshot.config.values),
            (snapshot.config.config_snapshot_id,),
            reason="config_missing",
        ),
        role(
            "environment_snapshot",
            bool(snapshot.environment.values),
            (snapshot.environment.environment_snapshot_id,),
            reason="environment_missing",
        ),
        role(
            "seed",
            type(config.get("seed")) is int,
            (snapshot.config.config_snapshot_id,) if type(config.get("seed")) is int else (),
            reason="seed_missing",
        ),
        ExperimentReproductionRoleV2(
            role="required_artifacts",
            state=artifacts_state,
            evidence_ids=artifact_ids,
            reason=None
            if artifacts_state is ReproductionRoleStateV2.PRESENT_VERIFIED
            else "artifact_content_not_verified",
        ),
    )
    status: Literal["reproducible", "insufficient_metadata"] = (
        "reproducible"
        if all(item.state is ReproductionRoleStateV2.PRESENT_VERIFIED for item in roles)
        else "insufficient_metadata"
    )
    payload = {
        "status": status,
        "run_snapshot_id": snapshot.run_snapshot_id,
        "roles": roles,
        "policy_version": EXPERIMENT_REPRODUCTION_POLICY_VERSION,
    }
    return ExperimentReproductionResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(_json_value(payload)),
    )


def compare_metric_v2(
    comparability: ExperimentComparabilityResultV2,
    baseline: ExperimentMetricObservationV2 | None,
    candidate: ExperimentMetricObservationV2 | None,
    *,
    direction: MetricDirectionV2,
) -> ExperimentComparisonV2:
    reason: str | None = None
    delta: float | None = None
    relative: float | None = None
    improvement: bool | None = None
    baseline_value = baseline.numeric_value if baseline and baseline.valid else None
    candidate_value = candidate.numeric_value if candidate and candidate.valid else None
    if comparability.decision in {
        ComparabilityDecisionV2.NOT_COMPARABLE,
        ComparabilityDecisionV2.INSUFFICIENT_METADATA,
    }:
        reason = f"comparability:{comparability.decision.value}"
    elif baseline_value is None or candidate_value is None:
        reason = "valid_observation_missing"
    elif (
        baseline is None
        or candidate is None
        or baseline.definition_id != candidate.definition_id
        or baseline.canonical_unit != candidate.canonical_unit
        or baseline.split != candidate.split
    ):
        reason = "observation_identity_mismatch"
    elif direction is MetricDirectionV2.UNKNOWN:
        reason = "metric_direction_unknown"
    else:
        delta = candidate_value - baseline_value
        relative = None if baseline_value == 0 else delta / baseline_value
        improvement = delta > 0 if direction is MetricDirectionV2.HIGHER else delta < 0
    status: Literal["available", "unavailable"] = (
        "available" if improvement is not None else "unavailable"
    )
    payload = {
        "status": status,
        "comparability": comparability,
        "baseline_observation_id": baseline.observation_id if baseline else None,
        "candidate_observation_id": candidate.observation_id if candidate else None,
        "baseline_value": baseline_value,
        "candidate_value": candidate_value,
        "delta": delta,
        "relative_delta": relative,
        "improvement": improvement,
        "unit": baseline.canonical_unit if baseline else None,
        "reason": reason,
    }
    return ExperimentComparisonV2(
        **payload,
        content_sha256=canonical_sha256_v2(_json_value(payload)),
    )


__all__ = [
    "EXPERIMENT_AGGREGATION_POLICY_VERSION",
    "EXPERIMENT_COMPARABILITY_POLICY_VERSION",
    "EXPERIMENT_REPRODUCTION_POLICY_VERSION",
    "ComparabilityDecisionV2",
    "DifferenceClassV2",
    "ExperimentAggregationResultV2",
    "ExperimentComparabilityResultV2",
    "ExperimentComparisonV2",
    "ExperimentDifferenceV2",
    "ExperimentReproductionResultV2",
    "ExperimentReproductionRoleV2",
    "ReproductionRoleStateV2",
    "aggregate_experiment_run_group_v2",
    "compare_experiment_snapshots_v2",
    "compare_metric_v2",
    "evaluate_reproduction_v2",
]
