"""Immutable Experiment E1 entities derived from current V1/adapter records."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

EXPERIMENT_ENTITY_CONTRACT_VERSION = "experiment-entity-contract-v2"
EXPERIMENT_SNAPSHOT_BUILDER_VERSION = "experiment-snapshot-builder-v2"
EXPERIMENT_METRIC_REGISTRY_VERSION = "experiment-metric-registry-v2"

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,511}")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|password|secret|credential|private[_-]?key)"
)
_PRIVATE_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S{8,})|"
    r"(?:^|[\s\"'(])/(?:Users|home|private|tmp|var|etc|root|Volumes)(?:/|\b)|"
    r"(?i:(?:^|[\s\"'(])[A-Z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+))"
)
_ENVIRONMENT_KEYS = frozenset(
    {
        "cuda",
        "device",
        "os",
        "package_lock",
        "platform",
        "python",
        "runtime",
    }
)
_UNIT_CONVERSIONS: dict[str, tuple[str, float]] = {
    "%": ("ratio", 0.01),
    "percent": ("ratio", 0.01),
    "ratio": ("ratio", 1.0),
    "ms": ("seconds", 0.001),
    "millisecond": ("seconds", 0.001),
    "milliseconds": ("seconds", 0.001),
    "s": ("seconds", 1.0),
    "sec": ("seconds", 1.0),
    "seconds": ("seconds", 1.0),
}


def canonical_json_bytes_v2(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256_v2(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes_v2(value)).hexdigest()


def _canonical_source_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return {
            "invalid_nonfinite": (
                "nan" if math.isnan(value) else "positive_inf" if value > 0 else "negative_inf"
            )
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_source_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_source_value(child) for child in value]
    return value


def _safe_id(value: object, label: str) -> str:
    text = str(value)
    if text != text.strip() or _SAFE_ID_RE.fullmatch(text) is None:
        raise ValueError(f"{label} is not a portable identity")
    return text


def _safe_text(value: object, label: str, *, limit: int = 8_000) -> str:
    text = unicodedata.normalize("NFC", str(value))
    if (
        not text
        or text != text.strip()
        or len(text) > limit
        or any(ord(character) < 32 and character not in "\t\n" for character in text)
        or _PRIVATE_RE.search(text)
    ):
        raise ValueError(f"{label} is unsafe")
    return text


class _FrozenEntity(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class MetricDirectionV2(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"
    UNKNOWN = "unknown"


class MetricDefinitionStatusV2(StrEnum):
    MAPPED = "mapped"
    UNKNOWN = "unknown"


class MetricHistoryAvailabilityV2(StrEnum):
    FULL = "full"
    SUMMARY_ONLY = "summary_only"


class ArtifactVerificationStateV2(StrEnum):
    PRESENT_VERIFIED = "present_verified"
    PRESENT_UNVERIFIED = "present_unverified"
    MISSING = "missing"
    INACCESSIBLE = "inaccessible"


class ExperimentMetricRegistryEntryV2(_FrozenEntity):
    canonical_name: str
    aliases: tuple[str, ...]
    description: str
    unit: str | None
    direction: MetricDirectionV2
    valid_min: float | None = None
    valid_max: float | None = None
    required_dimensions: tuple[str, ...] = ()
    default_aggregation: str = "final"
    provenance: str

    @model_validator(mode="after")
    def _canonical(self) -> ExperimentMetricRegistryEntryV2:
        _safe_id(self.canonical_name, "metric canonical name")
        if self.aliases != tuple(sorted(set(self.aliases))):
            raise ValueError("metric aliases must be sorted and unique")
        if (
            self.valid_min is not None
            and self.valid_max is not None
            and self.valid_min > self.valid_max
        ):
            raise ValueError("metric valid range is reversed")
        if self.default_aggregation not in {
            "best",
            "count",
            "final",
            "latest",
            "max",
            "mean",
            "median",
            "min",
            "std",
        }:
            raise ValueError("metric aggregation is not allowlisted")
        _safe_text(self.description, "metric description")
        _safe_id(self.provenance, "metric provenance")
        return self


class ExperimentMetricDefinitionV2(_FrozenEntity):
    definition_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    description: str
    unit: str | None
    direction: MetricDirectionV2
    status: MetricDefinitionStatusV2
    valid_min: float | None
    valid_max: float | None
    required_dimensions: tuple[str, ...]
    default_aggregation: str
    provenance: str
    content_sha256: str
    registry_version: str = EXPERIMENT_METRIC_REGISTRY_VERSION
    contract_version: str = EXPERIMENT_ENTITY_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentMetricDefinitionV2:
        payload = self.model_dump(
            mode="json",
            exclude={"definition_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        expected = "experimentmetricdef-" + digest.removeprefix("sha256:")
        if self.content_sha256 != digest or self.definition_id != expected:
            raise ValueError("metric definition identity mismatch")
        if (
            self.status is MetricDefinitionStatusV2.UNKNOWN
            and self.direction is not MetricDirectionV2.UNKNOWN
        ):
            raise ValueError("unknown definition cannot assert metric direction")
        return self


class ExperimentMetricObservationV2(_FrozenEntity):
    observation_id: str
    series_id: str
    definition_id: str
    run_snapshot_id: str
    experiment_id: str
    run_id: str
    raw_name: str
    raw_value: str
    numeric_value: float | None
    raw_unit: str | None
    canonical_unit: str | None
    split: str | None
    dimensions: tuple[tuple[str, str], ...]
    step: int | None
    observed_at: str | None
    aggregation_role: str
    history_availability: MetricHistoryAvailabilityV2
    valid: bool
    invalid_reason: str | None
    source_locator: str
    content_sha256: str
    contract_version: str = EXPERIMENT_ENTITY_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentMetricObservationV2:
        if self.dimensions != tuple(sorted(set(self.dimensions))):
            raise ValueError("metric dimensions must be sorted and unique")
        if self.valid != (self.numeric_value is not None and self.invalid_reason is None):
            raise ValueError("metric observation validity is inconsistent")
        payload = self.model_dump(
            mode="json",
            exclude={"observation_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        expected = "experimentobs-" + digest.removeprefix("sha256:")
        if self.content_sha256 != digest or self.observation_id != expected:
            raise ValueError("metric observation identity mismatch")
        return self


class ExperimentDatasetVersionV2(_FrozenEntity):
    dataset_version_id: str
    dataset_id: str | None
    version: str | None
    split: str | None
    preprocessing_sha256: str | None
    completeness: str
    content_sha256: str
    contract_version: str = EXPERIMENT_ENTITY_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentDatasetVersionV2:
        if self.completeness not in {"complete", "missing_id", "missing_version"}:
            raise ValueError("dataset completeness is invalid")
        payload = self.model_dump(
            mode="json",
            exclude={"dataset_version_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if (
            self.content_sha256 != digest
            or self.dataset_version_id != "experimentdataset-" + digest.removeprefix("sha256:")
        ):
            raise ValueError("dataset version identity mismatch")
        return self


class ExperimentConfigSnapshotV2(_FrozenEntity):
    config_snapshot_id: str
    values: tuple[tuple[str, Any], ...]
    redacted_keys: tuple[str, ...]
    content_sha256: str
    contract_version: str = EXPERIMENT_ENTITY_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentConfigSnapshotV2:
        if tuple(key for key, _ in self.values) != tuple(sorted({key for key, _ in self.values})):
            raise ValueError("config keys must be sorted and unique")
        if self.redacted_keys != tuple(sorted(set(self.redacted_keys))):
            raise ValueError("redacted config keys must be sorted and unique")
        payload = self.model_dump(
            mode="json",
            exclude={"config_snapshot_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if (
            self.content_sha256 != digest
            or self.config_snapshot_id != "experimentconfig-" + digest.removeprefix("sha256:")
        ):
            raise ValueError("config snapshot identity mismatch")
        return self


class ExperimentEnvironmentSnapshotV2(_FrozenEntity):
    environment_snapshot_id: str
    values: tuple[tuple[str, str], ...]
    omitted_keys: tuple[str, ...]
    compatibility_sha256: str
    content_sha256: str
    contract_version: str = EXPERIMENT_ENTITY_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentEnvironmentSnapshotV2:
        if tuple(key for key, _ in self.values) != tuple(sorted({key for key, _ in self.values})):
            raise ValueError("environment keys must be sorted and unique")
        payload = self.model_dump(
            mode="json",
            exclude={"environment_snapshot_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if (
            self.compatibility_sha256 != canonical_sha256_v2({"values": self.values})
            or self.content_sha256 != digest
            or self.environment_snapshot_id != "experimentenv-" + digest.removeprefix("sha256:")
        ):
            raise ValueError("environment snapshot identity mismatch")
        return self


class ExperimentArtifactVersionV2(_FrozenEntity):
    artifact_version_id: str
    run_id: str
    name: str
    kind: str
    locator: str
    source_uri_sha256: str
    checksum: str | None
    verification_state: ArtifactVerificationStateV2
    content_sha256: str
    contract_version: str = EXPERIMENT_ENTITY_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentArtifactVersionV2:
        if self.checksum is not None and _SHA256_RE.fullmatch(self.checksum) is None:
            raise ValueError("artifact checksum is not canonical")
        if (
            self.verification_state is ArtifactVerificationStateV2.PRESENT_VERIFIED
            and self.checksum is None
        ):
            raise ValueError("verified artifact requires a checksum")
        payload = self.model_dump(
            mode="json",
            exclude={"artifact_version_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if (
            self.content_sha256 != digest
            or self.artifact_version_id != "experimentartifact-" + digest.removeprefix("sha256:")
        ):
            raise ValueError("artifact version identity mismatch")
        return self


class ExperimentRunSnapshotV2(_FrozenEntity):
    run_snapshot_id: str
    project_id: str
    source_id: str
    generation_id: str
    acl_ref: str
    experiment_id: str
    run_id: str
    source_run_sha256: str
    status: str
    observed_at: str
    repository_id: str | None
    commit_sha: str | None
    branch: str | None
    command_present: bool
    dataset: ExperimentDatasetVersionV2
    config: ExperimentConfigSnapshotV2
    environment: ExperimentEnvironmentSnapshotV2
    definitions: tuple[ExperimentMetricDefinitionV2, ...]
    observations: tuple[ExperimentMetricObservationV2, ...]
    artifacts: tuple[ExperimentArtifactVersionV2, ...]
    content_sha256: str
    builder_version: str = EXPERIMENT_SNAPSHOT_BUILDER_VERSION
    contract_version: str = EXPERIMENT_ENTITY_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentRunSnapshotV2:
        if len({item.definition_id for item in self.definitions}) != len(self.definitions):
            raise ValueError("run snapshot has duplicate metric definitions")
        definition_ids = {item.definition_id for item in self.definitions}
        if any(item.definition_id not in definition_ids for item in self.observations):
            raise ValueError("run snapshot observation has no definition")
        if any(item.run_snapshot_id != self.run_snapshot_id for item in self.observations):
            raise ValueError("run snapshot observation is cross-bound")
        if any(item.run_id != self.run_id for item in self.artifacts):
            raise ValueError("run snapshot artifact is cross-bound")
        payload = self.model_dump(
            mode="json",
            exclude={"run_snapshot_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        expected = "experimentsnapshot-" + canonical_sha256_v2(
            {
                "project_id": self.project_id,
                "source_id": self.source_id,
                "generation_id": self.generation_id,
                "run_id": self.run_id,
                "source_run_sha256": self.source_run_sha256,
                "builder_version": self.builder_version,
            }
        ).removeprefix("sha256:")
        if self.content_sha256 != digest or self.run_snapshot_id != expected:
            raise ValueError("run snapshot identity mismatch")
        return self


class ExperimentRunGroupMemberV2(_FrozenEntity):
    run_snapshot_id: str
    run_id: str
    seed: int | None
    fold: int | None
    repeat: int | None
    included: bool
    exclusion_reason: str | None


class ExperimentRunGroupV2(_FrozenEntity):
    run_group_id: str
    project_id: str
    experiment_id: str
    name: str
    members: tuple[ExperimentRunGroupMemberV2, ...] = Field(min_length=1)
    content_sha256: str
    contract_version: str = EXPERIMENT_ENTITY_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> ExperimentRunGroupV2:
        if tuple(item.run_snapshot_id for item in self.members) != tuple(
            sorted({item.run_snapshot_id for item in self.members})
        ):
            raise ValueError("run group membership must be sorted and unique")
        payload = self.model_dump(
            mode="json",
            exclude={"run_group_id", "content_sha256"},
        )
        digest = canonical_sha256_v2(payload)
        if (
            self.content_sha256 != digest
            or self.run_group_id != "experimentgroup-" + digest.removeprefix("sha256:")
        ):
            raise ValueError("run group identity mismatch")
        return self


def _content_addressed(model: type[_FrozenEntity], prefix: str, payload: dict[str, Any]):
    digest = canonical_sha256_v2(payload)
    return model(
        **payload,
        **{
            next(
                name for name in model.model_fields if name.endswith("_id") and name not in payload
            ): prefix + digest.removeprefix("sha256:"),
            "content_sha256": digest,
        },
    )


def build_metric_definition_v2(
    raw_name: str,
    registry: Mapping[str, ExperimentMetricRegistryEntryV2],
) -> ExperimentMetricDefinitionV2:
    normalized = raw_name.casefold()
    matches = [
        entry
        for entry in registry.values()
        if normalized == entry.canonical_name.casefold()
        or normalized in {alias.casefold() for alias in entry.aliases}
    ]
    if len(matches) > 1:
        raise ValueError("metric alias is ambiguous")
    if matches:
        entry = matches[0]
        status = MetricDefinitionStatusV2.MAPPED
    else:
        entry = ExperimentMetricRegistryEntryV2(
            canonical_name=re.sub(r"[^a-z0-9_.-]+", "-", normalized).strip("-") or "unknown",
            aliases=(),
            description="Unknown metric definition retained without direction.",
            unit=None,
            direction=MetricDirectionV2.UNKNOWN,
            provenance="unmapped",
        )
        status = MetricDefinitionStatusV2.UNKNOWN
    payload = {
        **entry.model_dump(mode="python"),
        "status": status,
        "registry_version": EXPERIMENT_METRIC_REGISTRY_VERSION,
        "contract_version": EXPERIMENT_ENTITY_CONTRACT_VERSION,
    }
    digest = canonical_sha256_v2(payload)
    return ExperimentMetricDefinitionV2(
        definition_id="experimentmetricdef-" + digest.removeprefix("sha256:"),
        content_sha256=digest,
        **payload,
    )


def _flatten_config(
    value: Mapping[str, Any],
    *,
    prefix: str = "",
) -> tuple[list[tuple[str, Any]], list[str]]:
    values: list[tuple[str, Any]] = []
    redacted: list[str] = []
    for key, child in sorted(value.items()):
        path = f"{prefix}.{key}" if prefix else str(key)
        _safe_id(path, "config key")
        if _SECRET_KEY_RE.search(path):
            redacted.append(path)
            continue
        if isinstance(child, Mapping):
            nested_values, nested_redacted = _flatten_config(child, prefix=path)
            values.extend(nested_values)
            redacted.extend(nested_redacted)
            continue
        canonical_json_bytes_v2(child)
        if isinstance(child, str) and _PRIVATE_RE.search(child):
            redacted.append(path)
            continue
        if isinstance(child, list) and any(
            isinstance(item, str) and _PRIVATE_RE.search(item) for item in child
        ):
            redacted.append(path)
            continue
        values.append((path, child))
    return values, redacted


def _config_snapshot(config: Mapping[str, Any]) -> ExperimentConfigSnapshotV2:
    values, redacted = _flatten_config(config)
    payload = {
        "values": tuple(values),
        "redacted_keys": tuple(sorted(redacted)),
        "contract_version": EXPERIMENT_ENTITY_CONTRACT_VERSION,
    }
    digest = canonical_sha256_v2(payload)
    return ExperimentConfigSnapshotV2(
        config_snapshot_id="experimentconfig-" + digest.removeprefix("sha256:"),
        content_sha256=digest,
        **payload,
    )


def _environment_snapshot(
    environment: Mapping[str, Any],
) -> ExperimentEnvironmentSnapshotV2:
    values: list[tuple[str, str]] = []
    omitted: list[str] = []
    for key, value in sorted(environment.items()):
        if key not in _ENVIRONMENT_KEYS or _SECRET_KEY_RE.search(key):
            omitted.append(key)
            continue
        values.append((key, _safe_text(value, f"environment {key}", limit=1_000)))
    compatibility = canonical_sha256_v2({"values": tuple(values)})
    payload = {
        "values": tuple(values),
        "omitted_keys": tuple(sorted(omitted)),
        "compatibility_sha256": compatibility,
        "contract_version": EXPERIMENT_ENTITY_CONTRACT_VERSION,
    }
    digest = canonical_sha256_v2(payload)
    return ExperimentEnvironmentSnapshotV2(
        environment_snapshot_id="experimentenv-" + digest.removeprefix("sha256:"),
        content_sha256=digest,
        **payload,
    )


def _dataset_version(run: Mapping[str, Any]) -> ExperimentDatasetVersionV2:
    dataset_id = run.get("dataset_id")
    version = run.get("dataset_version")
    completeness = (
        "missing_id" if dataset_id is None else "missing_version" if version is None else "complete"
    )
    payload = {
        "dataset_id": str(dataset_id) if dataset_id is not None else None,
        "version": str(version) if version is not None else None,
        "split": str(run["dataset_split"]) if run.get("dataset_split") is not None else None,
        "preprocessing_sha256": (
            str(run["preprocessing_sha256"])
            if run.get("preprocessing_sha256") is not None
            else None
        ),
        "completeness": completeness,
        "contract_version": EXPERIMENT_ENTITY_CONTRACT_VERSION,
    }
    digest = canonical_sha256_v2(payload)
    return ExperimentDatasetVersionV2(
        dataset_version_id="experimentdataset-" + digest.removeprefix("sha256:"),
        content_sha256=digest,
        **payload,
    )


def build_experiment_run_snapshot_v2(
    run: Mapping[str, Any],
    *,
    project_id: str,
    source_id: str,
    generation_id: str,
    acl_ref: str,
    registry: Mapping[str, ExperimentMetricRegistryEntryV2],
    observed_at: str,
) -> ExperimentRunSnapshotV2:
    """Build one immutable snapshot; unknown direction is never inferred from V1 defaults."""

    for value, label in (
        (project_id, "project"),
        (source_id, "source"),
        (generation_id, "generation"),
        (acl_ref, "ACL"),
        (run.get("id"), "run"),
        (run.get("experiment_id"), "experiment"),
    ):
        _safe_id(value, label)
    source_run_sha = canonical_sha256_v2(_canonical_source_value(run))
    dataset = _dataset_version(run)
    config = _config_snapshot(dict(run.get("config") or {}))
    environment = _environment_snapshot(dict(run.get("environment") or {}))
    raw_metrics = tuple(run.get("metrics") or ())
    definitions_by_name = {
        str(metric["name"]): build_metric_definition_v2(
            str(metric["name"]),
            registry,
        )
        for metric in raw_metrics
    }
    definitions = tuple(
        sorted(
            {item.definition_id: item for item in definitions_by_name.values()}.values(),
            key=lambda item: item.definition_id,
        )
    )
    snapshot_seed = canonical_sha256_v2(
        {
            "project_id": project_id,
            "source_id": source_id,
            "generation_id": generation_id,
            "run_id": run["id"],
            "source_run_sha256": source_run_sha,
            "builder_version": EXPERIMENT_SNAPSHOT_BUILDER_VERSION,
        }
    )
    run_snapshot_id = "experimentsnapshot-" + snapshot_seed.removeprefix("sha256:")
    observations: list[ExperimentMetricObservationV2] = []
    for index, metric in enumerate(raw_metrics):
        raw_name = str(metric["name"])
        definition = definitions_by_name[raw_name]
        raw_value = repr(metric.get("value"))
        value = metric.get("value")
        valid = type(value) in {int, float} and math.isfinite(float(value))
        numeric = float(value) if valid else None
        raw_unit = str(metric["unit"]) if metric.get("unit") is not None else None
        canonical_unit = (
            _UNIT_CONVERSIONS.get(raw_unit.casefold(), (raw_unit.casefold(), 1.0))[0]
            if raw_unit
            else definition.unit
        )
        if valid and raw_unit and raw_unit.casefold() in _UNIT_CONVERSIONS:
            numeric *= _UNIT_CONVERSIONS[raw_unit.casefold()][1]  # type: ignore[operator]
        dimensions = tuple(
            sorted(
                (str(key), str(item)) for key, item in dict(metric.get("dimensions") or {}).items()
            )
        )
        series_id = "experimentseries-" + canonical_sha256_v2(
            {
                "run_snapshot_id": run_snapshot_id,
                "definition_id": definition.definition_id,
                "split": metric.get("split"),
                "dimensions": dimensions,
            }
        ).removeprefix("sha256:")
        payload = {
            "series_id": series_id,
            "definition_id": definition.definition_id,
            "run_snapshot_id": run_snapshot_id,
            "experiment_id": str(run["experiment_id"]),
            "run_id": str(run["id"]),
            "raw_name": raw_name,
            "raw_value": raw_value,
            "numeric_value": numeric,
            "raw_unit": raw_unit,
            "canonical_unit": canonical_unit,
            "split": str(metric["split"]) if metric.get("split") is not None else None,
            "dimensions": dimensions,
            "step": int(metric["step"]) if metric.get("step") is not None else None,
            "observed_at": (
                str(metric["observed_at"]) if metric.get("observed_at") is not None else None
            ),
            "aggregation_role": str(metric.get("aggregation_role") or "observed"),
            "history_availability": MetricHistoryAvailabilityV2(
                metric.get("history_availability") or "summary_only"
            ),
            "valid": valid,
            "invalid_reason": None if valid else "non_finite_or_non_numeric",
            "source_locator": (
                "experiment-v2://observation/"
                + canonical_sha256_v2(
                    {
                        "run_id": run["id"],
                        "metric": raw_name,
                        "index": index,
                    }
                ).removeprefix("sha256:")
            ),
            "contract_version": EXPERIMENT_ENTITY_CONTRACT_VERSION,
        }
        digest = canonical_sha256_v2(payload)
        observations.append(
            ExperimentMetricObservationV2(
                observation_id="experimentobs-" + digest.removeprefix("sha256:"),
                content_sha256=digest,
                **payload,
            )
        )
    artifacts: list[ExperimentArtifactVersionV2] = []
    for artifact in tuple(run.get("artifacts") or ()):
        uri = _safe_text(artifact.get("uri"), "artifact URI", limit=2_000)
        checksum = artifact.get("checksum")
        if checksum is not None:
            checksum = str(checksum)
        state = (
            ArtifactVerificationStateV2.PRESENT_VERIFIED
            if checksum is not None
            and _SHA256_RE.fullmatch(checksum) is not None
            and artifact.get("authorized") is True
            else ArtifactVerificationStateV2.PRESENT_UNVERIFIED
        )
        payload = {
            "run_id": str(run["id"]),
            "name": _safe_text(artifact.get("name"), "artifact name", limit=240),
            "kind": _safe_id(artifact.get("kind") or "artifact", "artifact kind"),
            "locator": (
                "experiment-v2://artifact/" + canonical_sha256_v2(uri).removeprefix("sha256:")
            ),
            "source_uri_sha256": canonical_sha256_v2(uri),
            "checksum": checksum,
            "verification_state": state,
            "contract_version": EXPERIMENT_ENTITY_CONTRACT_VERSION,
        }
        digest = canonical_sha256_v2(payload)
        artifacts.append(
            ExperimentArtifactVersionV2(
                artifact_version_id="experimentartifact-" + digest.removeprefix("sha256:"),
                content_sha256=digest,
                **payload,
            )
        )
    payload = {
        "project_id": project_id,
        "source_id": source_id,
        "generation_id": generation_id,
        "acl_ref": acl_ref,
        "experiment_id": str(run["experiment_id"]),
        "run_id": str(run["id"]),
        "source_run_sha256": source_run_sha,
        "status": _safe_id(run.get("status") or "unknown", "run status"),
        "observed_at": _safe_text(observed_at, "snapshot observed time", limit=100),
        "repository_id": (
            _safe_id(run["repository_id"], "repository") if run.get("repository_id") else None
        ),
        "commit_sha": str(run["commit_sha"]) if run.get("commit_sha") else None,
        "branch": _safe_text(run["branch"], "branch", limit=240) if run.get("branch") else None,
        "command_present": bool(str(run.get("command") or "").strip()),
        "dataset": dataset,
        "config": config,
        "environment": environment,
        "definitions": definitions,
        "observations": tuple(sorted(observations, key=lambda item: item.observation_id)),
        "artifacts": tuple(sorted(artifacts, key=lambda item: item.artifact_version_id)),
        "builder_version": EXPERIMENT_SNAPSHOT_BUILDER_VERSION,
        "contract_version": EXPERIMENT_ENTITY_CONTRACT_VERSION,
    }
    content = canonical_sha256_v2(
        {
            key: (
                value.model_dump(mode="json")
                if isinstance(value, BaseModel)
                else [item.model_dump(mode="json") for item in value]
                if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
                else value
            )
            for key, value in payload.items()
        }
    )
    return ExperimentRunSnapshotV2(
        run_snapshot_id=run_snapshot_id,
        content_sha256=content,
        **payload,
    )


def build_experiment_run_group_v2(
    name: str,
    snapshots: tuple[ExperimentRunSnapshotV2, ...],
) -> ExperimentRunGroupV2:
    if not snapshots:
        raise ValueError("run group requires snapshots")
    project_ids = {item.project_id for item in snapshots}
    experiment_ids = {item.experiment_id for item in snapshots}
    if len(project_ids) != 1 or len(experiment_ids) != 1:
        raise ValueError("run group cannot cross project or experiment")
    members = tuple(
        sorted(
            (
                ExperimentRunGroupMemberV2(
                    run_snapshot_id=item.run_snapshot_id,
                    run_id=item.run_id,
                    seed=(
                        int(dict(item.config.values)["seed"])
                        if type(dict(item.config.values).get("seed")) is int
                        else None
                    ),
                    fold=(
                        int(dict(item.config.values)["fold"])
                        if type(dict(item.config.values).get("fold")) is int
                        else None
                    ),
                    repeat=(
                        int(dict(item.config.values)["repeat"])
                        if type(dict(item.config.values).get("repeat")) is int
                        else None
                    ),
                    included=item.status == "completed",
                    exclusion_reason=None
                    if item.status == "completed"
                    else f"status:{item.status}",
                )
                for item in snapshots
            ),
            key=lambda item: item.run_snapshot_id,
        )
    )
    payload = {
        "project_id": next(iter(project_ids)),
        "experiment_id": next(iter(experiment_ids)),
        "name": _safe_text(name, "run group name", limit=240),
        "members": members,
        "contract_version": EXPERIMENT_ENTITY_CONTRACT_VERSION,
    }
    content = canonical_sha256_v2(
        {
            **payload,
            "members": [item.model_dump(mode="json") for item in members],
        }
    )
    return ExperimentRunGroupV2(
        run_group_id="experimentgroup-" + content.removeprefix("sha256:"),
        content_sha256=content,
        **payload,
    )


__all__ = [
    "EXPERIMENT_ENTITY_CONTRACT_VERSION",
    "EXPERIMENT_METRIC_REGISTRY_VERSION",
    "EXPERIMENT_SNAPSHOT_BUILDER_VERSION",
    "ArtifactVerificationStateV2",
    "ExperimentArtifactVersionV2",
    "ExperimentConfigSnapshotV2",
    "ExperimentDatasetVersionV2",
    "ExperimentEnvironmentSnapshotV2",
    "ExperimentMetricDefinitionV2",
    "ExperimentMetricObservationV2",
    "ExperimentMetricRegistryEntryV2",
    "ExperimentRunGroupMemberV2",
    "ExperimentRunGroupV2",
    "ExperimentRunSnapshotV2",
    "MetricDefinitionStatusV2",
    "MetricDirectionV2",
    "MetricHistoryAvailabilityV2",
    "build_experiment_run_group_v2",
    "build_experiment_run_snapshot_v2",
    "build_metric_definition_v2",
    "canonical_json_bytes_v2",
    "canonical_sha256_v2",
]
