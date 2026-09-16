"""Explicit, fail-closed production-store adapters for governed multi-source V2."""

from __future__ import annotations

import json
import re
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..platform.models import (
    ExperimentAnalysisRequestV2,
    GlobalSearchRequest,
    NotebookComparisonRequestV2,
    NotebookComparisonSelectorV2,
    NotebookQuerySpecV2,
    NotebookQueryTaskV2,
    NotebookSearchFiltersV2,
)
from ..platform.service import PlatformService
from ..query.intent import SOURCE_AUTHORITY
from .answer_v2 import AnswerClaimV2
from .evidence_graph_v2 import (
    EdgeFactStatusV2,
    EdgeReviewStatusV2,
    EvidenceEdgeV2,
    build_default_edge_registry_v2,
    build_evidence_edge_v2,
)
from .global_governance_v2 import ReleaseStageV2
from .global_switches_v2 import GlobalComponentSwitchesV2
from .multisource_foundation_v2 import (
    MULTISOURCE_CAPABILITY_VERSION,
    MULTISOURCE_DOMAINS,
    CapabilityRegistryV2,
    MultiSourceCandidateV2,
    MultiSourcePlanV2,
    MultiSourceRouteContractV2,
    MultiSourceScopeV2,
    SourceCalibrationProfileV2,
    SourceCapabilityV2,
    SourceExecutionStatusV2,
    SourceExecutionV2,
    plan_multisource_query_v2,
    source_entity_types_v2,
)
from .multisource_pipeline_v2 import (
    GovernedMultiSourcePipelineV2,
    MultiSourcePipelineRequestV2,
    MultiSourcePipelineResultV2,
    MultiSourceRetrieverV2,
    SourceCancellationTokenV2,
    SourceRetrievalBatchV2,
    build_source_batch_v2,
)
from .performance_v2 import PerformanceSpanV2
from .sources.experiment.contracts_v2 import canonical_sha256_v2

if TYPE_CHECKING:
    from .performance_runtime_v2 import RuntimePerformanceV2

MULTISOURCE_RUNTIME_VERSION = "production-store-multisource-runtime-v2"
GLOBAL_ENGINE_HEADER = "X-RAG-Engine"
SourceName = Literal["code", "codex", "experiment", "notebook", "document", "workspace"]


class GlobalEngineOverrideError(ValueError):
    """Raised when the additive global engine override is invalid."""


def validate_global_engine_override(value: str | None) -> Literal["v1", "v2"]:
    if value is None:
        return "v1"
    normalized = value.strip().casefold()
    if normalized not in {"v1", "v2"}:
        raise GlobalEngineOverrideError(f"{GLOBAL_ENGINE_HEADER} must be one of: v1, v2")
    return normalized  # type: ignore[return-value]


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class ReviewedCalibrationArtifactV2(_Frozen):
    """An explicit reviewed artifact binding a fitted profile to its reviewed dataset."""

    source: SourceName
    artifact_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    review_status: Literal["reviewed"] = "reviewed"
    profile: SourceCalibrationProfileV2
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ReviewedCalibrationArtifactV2:
        if self.profile.source != self.source:
            raise ValueError("calibration artifact source mismatch")
        if self.profile.provenance_sha256 != self.dataset_sha256:
            raise ValueError("calibration profile is not bound to the reviewed dataset")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("calibration artifact digest mismatch")
        return self


class ReviewedCalibrationBundleV2(_Frozen):
    """Content-addressed deployment input; reviewer trust is supplied by the pinned digest."""

    schema_version: Literal["multisource-reviewed-calibration-bundle-v2"] = (
        "multisource-reviewed-calibration-bundle-v2"
    )
    artifacts: tuple[ReviewedCalibrationArtifactV2, ...] = Field(min_length=1)
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ReviewedCalibrationBundleV2:
        sources = tuple(item.source for item in self.artifacts)
        if sources != tuple(sorted(set(sources))):
            raise ValueError("calibration bundle sources must be sorted and unique")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("calibration bundle digest mismatch")
        return self


def build_reviewed_calibration_artifact_v2(
    *,
    source: SourceName,
    artifact_id: str,
    dataset_sha256: str,
    reviewer: str,
    reviewed_at: str,
    profile: SourceCalibrationProfileV2,
) -> ReviewedCalibrationArtifactV2:
    payload = {
        "source": source,
        "artifact_id": artifact_id,
        "dataset_sha256": dataset_sha256,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "review_status": "reviewed",
        "profile": profile,
    }
    normalized = ReviewedCalibrationArtifactV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return ReviewedCalibrationArtifactV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def build_reviewed_calibration_bundle_v2(
    artifacts: Mapping[str, ReviewedCalibrationArtifactV2],
) -> ReviewedCalibrationBundleV2:
    ordered = tuple(artifacts[source] for source in sorted(artifacts))
    if any(source != artifact.source for source, artifact in artifacts.items()):
        raise ValueError("calibration bundle registry mismatch")
    payload = {
        "schema_version": "multisource-reviewed-calibration-bundle-v2",
        "artifacts": [item.model_dump(mode="json") for item in ordered],
    }
    return ReviewedCalibrationBundleV2(
        artifacts=ordered,
        content_sha256=canonical_sha256_v2(payload),
    )


def load_reviewed_calibration_bundle_v2(
    path: str | Path,
    *,
    expected_sha256: str,
) -> dict[str, ReviewedCalibrationArtifactV2]:
    """Load one explicitly pinned deployment bundle without learning trust from the file."""

    if re.fullmatch(r"sha256:[0-9a-f]{64}", expected_sha256) is None:
        raise ValueError("reviewed calibration trust digest is invalid")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("reviewed calibration bundle path must be absolute")
    lowered_name = candidate.name.casefold()
    if lowered_name.endswith((".sqlite", ".sqlite3", ".db", "-wal", "-shm", ".pyc")):
        raise ValueError("reviewed calibration bundle cannot be a database or sidecar")
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("reviewed calibration bundle path cannot contain symlinks")
    metadata = candidate.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2_000_000:
        raise ValueError("reviewed calibration bundle must be a bounded regular file")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite calibration value is forbidden: {value}")

    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("reviewed calibration bundle is not valid UTF-8 JSON") from error
    bundle = ReviewedCalibrationBundleV2.model_validate(payload)
    if bundle.content_sha256 != expected_sha256:
        raise ValueError("reviewed calibration bundle does not match the deployment trust digest")
    return {item.source: item for item in bundle.artifacts}


class ProductionSourceSnapshotV2(_Frozen):
    source: SourceName
    project_id: str
    indexed: bool
    row_count: int = Field(ge=0)
    index_generation: str
    watermark: str
    error_code: str | None = None
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ProductionSourceSnapshotV2:
        if self.indexed and self.row_count == 0:
            raise ValueError("indexed source snapshot needs observable rows")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("source snapshot digest mismatch")
        return self


class MultiSourceRuntimeRequestV2(_Frozen):
    project_id: str
    request_id: str
    question: str = Field(min_length=1, max_length=4_000)
    intent: str
    engine: Literal["v2"] = "v2"
    acl_refs: tuple[str, ...] = ()
    enforce_acl: bool = True
    requested_sources: tuple[SourceName, ...] = ()
    experiment_ids: tuple[str, ...] = ()
    experiment_analysis: ExperimentAnalysisRequestV2 | None = None
    notebook_query: NotebookQuerySpecV2 | None = None
    as_of: str | None = None
    target_version: str | None = None
    context_budget_tokens: int = Field(default=6_000, ge=256, le=32_000)
    deadline_ms: int = Field(default=3_000, ge=100, le=30_000, exclude=True)
    claims: tuple[AnswerClaimV2, ...] = ()

    @model_validator(mode="after")
    def _canonical(self) -> MultiSourceRuntimeRequestV2:
        if self.acl_refs != tuple(sorted(set(self.acl_refs))):
            raise ValueError("runtime ACL references must be sorted and unique")
        if self.requested_sources != tuple(dict.fromkeys(self.requested_sources)):
            raise ValueError("requested sources must be ordered and unique")
        if self.experiment_ids != tuple(sorted(set(self.experiment_ids))):
            raise ValueError("runtime experiment IDs must be sorted and unique")
        return self


def _source_task_parameters_v2(
    request: MultiSourceRuntimeRequestV2,
) -> tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...]:
    by_source: dict[str, dict[str, tuple[str, ...]]] = {}

    def store(source: str, key: str, values: list[str] | tuple[str, ...]) -> None:
        normalized = tuple(sorted(set(values)))
        if normalized:
            by_source.setdefault(source, {})[key] = normalized

    store("experiment", "experiment_ids", request.experiment_ids)
    analysis = request.experiment_analysis
    if analysis is not None:
        store("experiment", "spec_task", [analysis.task])
        for key in ("metric", "baseline_run_id", "split", "aggregation"):
            value = getattr(analysis, key)
            if value is not None:
                store("experiment", key, [value])
        for key in (
            "candidate_run_ids",
            "run_ids",
            "roles",
            "treatment_config_keys",
            "controlled_config_keys",
        ):
            store("experiment", key, list(getattr(analysis, key)))
    notebook = request.notebook_query
    if notebook is not None:
        store("notebook", "spec_task", [str(notebook.task)])
        for key, values in notebook.filters.model_dump(mode="python").items():
            store("notebook", key, list(values))
        if notebook.comparison is not None:
            for side in ("baseline", "candidate"):
                selector = getattr(notebook.comparison, side)
                for key in ("template_id", "revision_id", "execution_id"):
                    store("notebook", f"{side}_{key}", [getattr(selector, key)])
    return tuple(
        (source, tuple(sorted(parameters.items())))
        for source, parameters in sorted(by_source.items())
    )


@dataclass(frozen=True, slots=True)
class MultiSourceRuntimePreparationV2:
    status: Literal["ready", "partial", "unavailable"]
    blockers: tuple[str, ...]
    scope: MultiSourceScopeV2 | None
    registry: CapabilityRegistryV2 | None
    source_snapshots: Mapping[str, ProductionSourceSnapshotV2]
    pipeline_request: MultiSourcePipelineRequestV2 | None
    routed_sources: tuple[str, ...]
    retrievers: Mapping[str, MultiSourceRetrieverV2]
    calibrations: Mapping[str, SourceCalibrationProfileV2]
    edge_provider: ProductionEvidenceEdgeProviderV2 | None

    @property
    def ready(self) -> bool:
        return (
            self.status == "ready"
            and self.pipeline_request is not None
            and self.registry is not None
            and self.edge_provider is not None
        )


@dataclass(frozen=True, slots=True)
class MultiSourceRuntimeExecutionV2:
    status: Literal["completed", "partial", "unavailable"]
    blockers: tuple[str, ...]
    preparation: MultiSourceRuntimePreparationV2
    pipeline_result: MultiSourcePipelineResultV2 | None


_SNAPSHOT_QUERIES: dict[str, tuple[tuple[str, str], ...]] = {
    "code": (
        (
            "repositories",
            """SELECT id, coalesce(active_generation_id,''), status, updated_at
               FROM repositories WHERE project_id=? ORDER BY id""",
        ),
    ),
    "codex": (
        (
            "codex_sources",
            """SELECT id, coalesce(active_generation_id,''), status, updated_at
               FROM codex_sources WHERE project_id=? ORDER BY id""",
        ),
    ),
    "experiment": (
        (
            "experiment_runs",
            """SELECT id, coalesce(updated_at,''), status, updated_at
               FROM experiment_runs WHERE project_id=? ORDER BY id""",
        ),
    ),
    "notebook": (
        (
            "notebook_runs",
            """SELECT id, content_hash, status, created_at
               FROM notebook_runs WHERE project_id=? ORDER BY id""",
        ),
    ),
    "document": (
        (
            "scientific_documents",
            """SELECT id, content_hash, status, updated_at
               FROM scientific_documents WHERE project_id=? ORDER BY id""",
        ),
    ),
    "workspace": (
        (
            "projects",
            """SELECT id, coalesce(updated_at,''), status, updated_at
               FROM projects WHERE id=? ORDER BY id""",
        ),
        (
            "research_topics",
            """SELECT id, coalesce(updated_at,''), status, updated_at
               FROM research_topics WHERE project_id=? ORDER BY id""",
        ),
        (
            "research_iterations",
            """SELECT id, coalesce(updated_at,''), status, updated_at
               FROM research_iterations WHERE project_id=? ORDER BY id""",
        ),
        (
            "research_work_items",
            """SELECT id, cast(version AS TEXT), status, updated_at
               FROM research_work_items WHERE project_id=? ORDER BY id""",
        ),
    ),
}

_CAPABILITY_SPEC: dict[str, dict[str, Any]] = {
    "code": {
        "tasks": (
            "bug_localization",
            "call_path",
            "change_context",
            "exact_location",
            "historical",
            "impact_analysis",
            "implementation",
            "test_validation",
        ),
        "channels": ("dense", "exact", "graph", "sparse"),
        "as_of": True,
        "numeric": False,
    },
    "codex": {
        "tasks": ("failure_retry", "process", "rationale", "validation"),
        "channels": ("dense", "event_graph", "exact", "sparse"),
        "as_of": False,
        "numeric": False,
    },
    "experiment": {
        "tasks": ("aggregate", "compare", "reproduce", "search"),
        "channels": ("exact", "numeric", "sparse", "structured"),
        "as_of": False,
        "numeric": True,
    },
    "notebook": {
        "tasks": (
            "code",
            "compare",
            "error",
            "lineage",
            "locate",
            "output",
            "parameter",
            "reproduction",
        ),
        "channels": ("exact", "execution_graph", "sparse", "structured"),
        "as_of": False,
        "numeric": True,
    },
    "document": {
        "tasks": (
            "citation",
            "claim",
            "figure_formula",
            "local_fact",
            "locate",
            "summary",
            "table",
            "version",
        ),
        "channels": ("citation_graph", "exact", "sparse", "structured"),
        "as_of": True,
        "numeric": False,
    },
    "workspace": {
        "tasks": (
            "blocker",
            "coverage",
            "current",
            "decision",
            "intelligence",
            "locate",
            "temporal",
            "work",
        ),
        "channels": ("exact", "structured", "temporal_graph"),
        "as_of": True,
        "numeric": False,
    },
}


def _source_snapshot(
    platform: PlatformService,
    *,
    project_id: str,
    source: SourceName,
) -> ProductionSourceSnapshotV2:
    rows: list[tuple[str, str, str, str, str]] = []
    error_code = None
    try:
        with platform.store.database.connection() as db:
            for table, query in _SNAPSHOT_QUERIES[source]:
                for row in db.execute(query, (project_id,)).fetchall():
                    values = tuple(str(value or "") for value in tuple(row))
                    rows.append((table, *values))
    except Exception as error:
        error_code = f"snapshot_error:{type(error).__name__}"
        rows = []
    generational_source = source in {"code", "codex"}
    has_active_generation = any(row[2] for row in rows) if generational_source else bool(rows)
    indexed = bool(rows) and has_active_generation and error_code is None
    if indexed:
        snapshot_digest = canonical_sha256_v2(
            {
                "source": source,
                "project_id": project_id,
                "rows": rows,
            }
        )
        index_generation = "store-snapshot-v2:" + snapshot_digest.removeprefix("sha256:")
        watermark = max((row[4] for row in rows if row[4]), default="unknown")
    else:
        index_generation = f"not-indexed:{source}"
        watermark = "unavailable"
        error_code = error_code or "not_indexed"
    payload = {
        "source": source,
        "project_id": project_id,
        "indexed": indexed,
        "row_count": len(rows),
        "index_generation": index_generation,
        "watermark": watermark,
        "error_code": error_code,
    }
    return ProductionSourceSnapshotV2(
        **payload,
        content_sha256=canonical_sha256_v2(payload),
    )


def _adapter_component(platform: PlatformService, source: str) -> Any | None:
    if source == "code":
        integration = platform.code_integration
        return (
            integration
            if integration is not None and getattr(integration, "v2_pipeline", None) is not None
            else None
        )
    registry = getattr(platform, "source_runtime_v2", None)
    if registry is not None:
        component = registry.component_class(source)
        if component is not None:
            return component
    return {
        "codex": platform.codex_v2,
        "experiment": platform.experiment_v2,
        "notebook": platform.notebook_v2,
        "document": platform.document_v2,
        "workspace": platform.workspace_v2,
    }[source]


def _build_capability(
    *,
    platform: PlatformService,
    snapshot: ProductionSourceSnapshotV2,
    calibration: ReviewedCalibrationArtifactV2 | None,
) -> SourceCapabilityV2:
    source = snapshot.source
    component = _adapter_component(platform, source)
    if component is None or not snapshot.indexed:
        health: Literal["ready", "partial", "unavailable"] = "unavailable"
    elif calibration is None:
        health = "partial"
    else:
        health = "ready"
    spec = _CAPABILITY_SPEC[source]
    payload = {
        "domain": source,
        "retriever_version": (
            f"{component.__module__}.{component.__qualname__}"
            if isinstance(component, type)
            else f"{type(component).__module__}.{type(component).__qualname__}"
            if component is not None
            else "adapter_unavailable"
        ),
        "supported_tasks": tuple(sorted(spec["tasks"])),
        "channels": tuple(sorted(spec["channels"])),
        "entity_types": source_entity_types_v2(source),
        "filters": ("acl", "project", "version"),
        "time_semantics": "production_store_observed_time",
        "version_semantics": "production_store_snapshot_generation",
        "supports_as_of": bool(spec["as_of"]),
        "supports_numeric": bool(spec["numeric"]),
        "supports_relation_expansion": True,
        "calibration_version": (
            calibration.profile.calibration_version
            if calibration is not None
            else "calibration_unavailable"
        ),
        "index_generation": snapshot.index_generation,
        "watermark": snapshot.watermark,
        "acl_model": "inherited",
        "health": health,
    }
    return SourceCapabilityV2(
        **payload,
        content_sha256=canonical_sha256_v2(payload),
    )


def _build_registry(capabilities: list[SourceCapabilityV2]) -> CapabilityRegistryV2:
    payload = {
        "capabilities": [item.model_dump(mode="json") for item in capabilities],
        "registry_version": MULTISOURCE_CAPABILITY_VERSION,
    }
    return CapabilityRegistryV2(
        capabilities=tuple(capabilities),
        content_sha256=canonical_sha256_v2(payload),
    )


@dataclass(frozen=True, slots=True)
class _CompiledSourceTaskV2:
    candidate_task: str
    experiment_ids: tuple[str, ...] = ()
    experiment_analysis: ExperimentAnalysisRequestV2 | None = None
    notebook_query: NotebookQuerySpecV2 | None = None
    typed: bool = False


_NOTEBOOK_OUTPUT_TYPES = (
    "binary_omitted",
    "display",
    "error",
    "execute_result",
    "stream",
)


def _compile_experiment_task_v2(
    *,
    task: str,
    parameters: Mapping[str, tuple[str, ...]],
) -> tuple[_CompiledSourceTaskV2 | None, str | None]:
    allowed = {
        "aggregation",
        "baseline_run_id",
        "candidate_run_ids",
        "controlled_config_keys",
        "experiment_ids",
        "metric",
        "roles",
        "run_ids",
        "spec_task",
        "split",
        "treatment_config_keys",
    }
    if set(parameters) - allowed:
        return None, "typed_spec_invalid"
    experiment_ids = parameters.get("experiment_ids", ())
    requested_task = parameters.get("spec_task", ())
    if task == "search":
        if requested_task:
            return None, "typed_spec_task_mismatch"
        return (
            _CompiledSourceTaskV2(
                candidate_task="search",
                experiment_ids=experiment_ids,
            ),
            None,
        )
    if task not in {"aggregate", "compare", "reproduce"}:
        return None, "platform_task_unsupported"
    if not requested_task:
        return None, "explicit_spec_required"
    if requested_task != (task,):
        return None, "typed_spec_task_mismatch"
    scalars: dict[str, str] = {}
    for key in ("metric", "baseline_run_id", "split", "aggregation"):
        values = parameters.get(key, ())
        if len(values) > 1:
            return None, "typed_spec_invalid"
        if values:
            scalars[key] = values[0]
    try:
        analysis = ExperimentAnalysisRequestV2(
            task=task,  # type: ignore[arg-type]
            **scalars,
            candidate_run_ids=list(parameters.get("candidate_run_ids", ())),
            run_ids=list(parameters.get("run_ids", ())),
            roles=list(parameters.get("roles", ())),
            treatment_config_keys=list(parameters.get("treatment_config_keys", ())),
            controlled_config_keys=list(parameters.get("controlled_config_keys", ())),
        )
    except ValueError:
        return None, "typed_spec_invalid"
    return (
        _CompiledSourceTaskV2(
            candidate_task=task,
            experiment_ids=experiment_ids,
            experiment_analysis=analysis,
            typed=True,
        ),
        None,
    )


def _notebook_identity_scope(filters: NotebookSearchFiltersV2) -> bool:
    return bool(
        filters.template_ids
        or filters.revision_ids
        or filters.execution_ids
        or filters.cell_ids
        or filters.parameter_ids
        or filters.output_ids
    )


def _compile_notebook_task_v2(
    *,
    task: str,
    parameters: Mapping[str, tuple[str, ...]],
) -> tuple[_CompiledSourceTaskV2 | None, str | None]:
    filter_fields = set(NotebookSearchFiltersV2.model_fields)
    selector_fields = {
        f"{side}_{key}"
        for side in ("baseline", "candidate")
        for key in ("template_id", "revision_id", "execution_id")
    }
    if set(parameters) - {"spec_task", *filter_fields, *selector_fields}:
        return None, "typed_spec_invalid"
    requested_task = parameters.get("spec_task", ())
    if task == "locate":
        if requested_task and requested_task != ("search",):
            return None, "typed_spec_task_mismatch"
        filters = NotebookSearchFiltersV2(
            **{field: list(parameters.get(field, ())) for field in filter_fields}
        )
        return (
            _CompiledSourceTaskV2(
                candidate_task="search",
                notebook_query=NotebookQuerySpecV2(
                    task=NotebookQueryTaskV2.SEARCH,
                    filters=filters,
                ),
            ),
            None,
        )
    if task == "compare":
        if not requested_task:
            return None, "explicit_spec_required"
        if requested_task != ("compare",):
            return None, "typed_spec_task_mismatch"
        try:
            selectors = {
                side: NotebookComparisonSelectorV2(
                    **{
                        key: parameters[f"{side}_{key}"][0]
                        for key in ("template_id", "revision_id", "execution_id")
                        if len(parameters.get(f"{side}_{key}", ())) == 1
                    }
                )
                for side in ("baseline", "candidate")
            }
            spec = NotebookQuerySpecV2(
                task=NotebookQueryTaskV2.COMPARE,
                filters=NotebookSearchFiltersV2(
                    **{field: list(parameters.get(field, ())) for field in filter_fields}
                ),
                comparison=NotebookComparisonRequestV2(**selectors),
            )
        except (KeyError, ValueError):
            return None, "typed_spec_invalid"
        return (
            _CompiledSourceTaskV2(
                candidate_task=task,
                notebook_query=spec,
                typed=True,
            ),
            None,
        )
    execution_task = "lineage" if task == "dataflow" else task
    if execution_task not in {
        "code",
        "error",
        "lineage",
        "output",
        "parameter",
        "reproduction",
    }:
        return None, "platform_task_unsupported"
    if not requested_task:
        return None, "explicit_spec_required"
    if requested_task != (execution_task,) or any(key in parameters for key in selector_fields):
        return None, "typed_spec_task_mismatch"
    try:
        filters = NotebookSearchFiltersV2(
            **{field: list(parameters.get(field, ())) for field in filter_fields}
        )
    except ValueError:
        return None, "typed_spec_invalid"
    if execution_task in {"output", "error"} and not _notebook_identity_scope(filters):
        return None, "notebook_identity_required"
    if execution_task == "reproduction" and not filters.execution_ids:
        return None, "notebook_execution_id_required"
    if execution_task in {"code", "lineage"} and not filters.cell_ids:
        return None, "notebook_cell_id_required"
    if execution_task == "parameter" and not (filters.parameter_ids or filters.parameter_names):
        return None, "notebook_parameter_required"
    filter_payload = filters.model_dump(mode="python")
    if execution_task == "output" and not (filters.output_ids or filters.output_types):
        filter_payload["output_types"] = list(_NOTEBOOK_OUTPUT_TYPES)
    if execution_task == "error":
        if filters.output_types and filters.output_types != ["error"]:
            return None, "notebook_error_filter_mismatch"
        if filters.statuses and filters.statuses != ["failed"]:
            return None, "notebook_error_filter_mismatch"
        filter_payload["output_types"] = ["error"]
        filter_payload["statuses"] = ["failed"]
    compiled = NotebookQuerySpecV2(
        task=NotebookQueryTaskV2(execution_task),
        filters=NotebookSearchFiltersV2.model_validate(filter_payload),
    )
    return (
        _CompiledSourceTaskV2(
            candidate_task=task,
            notebook_query=compiled,
            typed=True,
        ),
        None,
    )


class ProductionSourceRetrieverV2:
    """Convert one real PlatformService V2 response into the governed source contract."""

    def __init__(
        self,
        *,
        platform: PlatformService,
        source: SourceName,
        snapshot: ProductionSourceSnapshotV2,
        project_acl_ref: str,
        performance_runtime: RuntimePerformanceV2 | None = None,
        embedding_generation: bool = True,
    ) -> None:
        self.platform = platform
        self.source = source
        self.snapshot = snapshot
        self.project_acl_ref = project_acl_ref
        self.performance_runtime = performance_runtime
        self.embedding_generation = embedding_generation

    def retrieve(
        self,
        *,
        plan: MultiSourcePlanV2,
        scope: MultiSourceScopeV2,
        budget: int,
        required_roles: tuple[str, ...],
        corrective_round: int,
        cancellation: SourceCancellationTokenV2 | None = None,
    ) -> SourceRetrievalBatchV2:
        if cancellation is not None and cancellation.cancelled():
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.TIMEOUT,
                cancellation.reason or "source_cancelled",
            )
        if plan.scope != scope or scope.project_id != self.snapshot.project_id:
            raise ValueError("production source scope mismatch")
        if self.source not in plan.source_routes:
            raise ValueError("production source was not routed")
        contract = next(item for item in plan.source_contracts if item.source == self.source)
        compiled, compile_error = self._compile_task(contract)
        if compiled is None:
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.UNAVAILABLE,
                f"typed_task_unavailable:{self.source}:{contract.task}:{compile_error}",
            )
        if not self.snapshot.indexed:
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.NOT_INDEXED,
                self.snapshot.error_code or "not_indexed",
            )
        current = _source_snapshot(
            self.platform,
            project_id=scope.project_id,
            source=self.source,
        )
        if cancellation is not None and cancellation.cancelled():
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.TIMEOUT,
                cancellation.reason or "source_cancelled",
            )
        if current.content_sha256 != self.snapshot.content_sha256:
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.PARTIAL,
                "source_generation_changed",
            )
        subquestion = next(
            (item.question for item in plan.subquestions if self.source in item.sources),
            "",
        )
        if not subquestion:
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.PARTIAL,
                "source_subquestion_missing",
            )
        query = subquestion
        if corrective_round:
            query = (
                f"{subquestion} [corrective_round={corrective_round}; "
                f"required_roles={','.join(required_roles)}]"
            )
        query = query[:2_000]
        request = GlobalSearchRequest(
            query=query,
            project_id=scope.project_id,
            sources=[self.source],
            limit=min(100, max(1, budget)),
            include_lineage=False,
            max_hops=0,
            commit=scope.target_version,
            experiment_ids=list(compiled.experiment_ids),
            experiment_analysis=compiled.experiment_analysis,
            notebook_query=compiled.notebook_query,
            as_of=scope.as_of,
            allowed_acl_refs=list(scope.acl_refs),
            enforce_acl=True,
        )
        retrieval_started = time.perf_counter()
        if cancellation is not None and cancellation.cancelled():
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.TIMEOUT,
                cancellation.reason or "source_cancelled",
            )
        try:
            response = self.platform._retrieve_source(
                source=self.source,
                request=request,
                limit=request.limit,
                engine_requested="v2",
                governed_global_authority=True,
            )
        except PermissionError:
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.UNAUTHORIZED,
                "source_unauthorized",
                latency_ms=(time.perf_counter() - retrieval_started) * 1000,
            )
        except TimeoutError:
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.TIMEOUT,
                "source_timeout",
                latency_ms=(time.perf_counter() - retrieval_started) * 1000,
            )
        except Exception as error:
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.PARTIAL,
                f"source_error:{type(error).__name__}",
                latency_ms=(time.perf_counter() - retrieval_started) * 1000,
            )
        if cancellation is not None and cancellation.cancelled():
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.TIMEOUT,
                cancellation.reason or "source_cancelled",
                latency_ms=(time.perf_counter() - retrieval_started) * 1000,
            )
        trace = response["trace"]
        raw_status = str(trace.get("status") or "error")
        status = self._execution_status(raw_status)
        if trace.get("engine_used") != "v2":
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.UNAVAILABLE,
                "v2_engine_unavailable",
                latency_ms=float(trace.get("duration_ms") or 0.0),
            )
        rows = response.get("results")
        rows = rows if isinstance(rows, list) else []
        if rows and compiled.typed and not self._typed_response_matches(response, compiled):
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.UNAVAILABLE,
                f"typed_task_response_mismatch:{self.source}:{contract.task}",
                latency_ms=float(trace.get("duration_ms") or 0.0),
            )
        after = _source_snapshot(
            self.platform,
            project_id=scope.project_id,
            source=self.source,
        )
        if cancellation is not None and cancellation.cancelled():
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.TIMEOUT,
                cancellation.reason or "source_cancelled",
                latency_ms=(time.perf_counter() - retrieval_started) * 1000,
            )
        if after.content_sha256 != self.snapshot.content_sha256:
            return self._empty_batch(
                plan,
                SourceExecutionStatusV2.PARTIAL,
                "source_generation_changed",
                latency_ms=float(trace.get("duration_ms") or 0.0),
            )
        candidates, rejected = self._candidates(
            rows,
            plan=plan,
            scope=scope,
            required_roles=required_roles,
            candidate_task=compiled.candidate_task,
        )
        cache_hit = False
        if self.performance_runtime is not None and self.embedding_generation:
            candidates, cache_hit = self.performance_runtime.augment_candidates(
                question=query,
                plan_sha256=plan.content_sha256,
                scope=scope,
                source=self.source,
                generation=self.snapshot.index_generation,
                candidates=candidates,
            )
        if rejected and status is SourceExecutionStatusV2.COMPLETE:
            status = SourceExecutionStatusV2.PARTIAL
        if not rows and status is SourceExecutionStatusV2.COMPLETE:
            status = SourceExecutionStatusV2.NO_MATCHING_EVIDENCE
        execution = SourceExecutionV2(
            source=self.source,
            status=status,
            candidate_count=len(candidates),
            coverage=(len(candidates) / len(rows) if rows else 0.0),
            index_generation=self.snapshot.index_generation,
            watermark=self.snapshot.watermark,
            latency_ms=float(trace.get("duration_ms") or 0.0),
            error_code=(
                f"candidate_validation_failed:{rejected}" if rejected else trace.get("error_code")
            ),
        )
        if self.performance_runtime is not None:
            self.performance_runtime.record(
                PerformanceSpanV2(
                    trace_id=(
                        "trace-"
                        + canonical_sha256_v2(
                            {
                                "plan_sha256": plan.content_sha256,
                                "source": self.source,
                                "round": corrective_round,
                            }
                        ).removeprefix("sha256:")[:32]
                    ),
                    span="source_retrieval",
                    source=self.source,
                    latency_ms=max(
                        0.0,
                        (time.perf_counter() - retrieval_started) * 1_000,
                    ),
                    candidate_count=len(candidates),
                    filtered_count=rejected,
                    token_count=0,
                    cost_units=0.0,
                    cache_hit=cache_hit,
                    error_code=(
                        None
                        if status
                        in {
                            SourceExecutionStatusV2.COMPLETE,
                            SourceExecutionStatusV2.NO_MATCHING_EVIDENCE,
                        }
                        else status.value
                        if status.value
                        in {
                            "partial",
                            "timeout",
                            "unavailable",
                            "unauthorized",
                            "not_indexed",
                        }
                        else "internal_error"
                    ),
                    index_generation=self.snapshot.content_sha256,
                )
            )
        return build_source_batch_v2(
            source=self.source,
            plan=plan,
            candidates=candidates,
            execution=execution,
        )

    def _compile_task(
        self,
        contract: MultiSourceRouteContractV2,
    ) -> tuple[_CompiledSourceTaskV2 | None, str | None]:
        parameters = contract.task_parameter_map()
        if self.source == "experiment":
            return _compile_experiment_task_v2(
                task=contract.task,
                parameters=parameters,
            )
        if self.source == "notebook":
            return _compile_notebook_task_v2(
                task=contract.task,
                parameters=parameters,
            )
        if parameters:
            return None, "platform_task_unsupported"
        return _CompiledSourceTaskV2(candidate_task=contract.task), None

    def _typed_response_matches(
        self,
        response: Mapping[str, Any],
        compiled: _CompiledSourceTaskV2,
    ) -> bool:
        if self.source == "experiment":
            return self._experiment_response_matches(response, compiled)
        if self.source == "notebook":
            return self._notebook_response_matches(response, compiled)
        return True

    @staticmethod
    def _experiment_response_matches(
        response: Mapping[str, Any],
        compiled: _CompiledSourceTaskV2,
    ) -> bool:
        analysis = compiled.experiment_analysis
        selection = response.get("selection_trace")
        if analysis is None or not isinstance(selection, dict):
            return False
        expected = analysis.model_dump(mode="json")
        if selection.get("explicit") is not True or selection.get("task") != analysis.task:
            return False
        for field in (
            "metric",
            "baseline_run_id",
            "candidate_run_ids",
            "run_ids",
            "split",
            "aggregation",
            "roles",
        ):
            if selection.get(field) != expected[field]:
                return False
        envelopes = response.get("analysis")
        result_key = {
            "aggregate": "aggregations",
            "compare": "comparisons",
            "reproduce": "reproduction",
        }[analysis.task]
        return bool(
            isinstance(envelopes, list)
            and envelopes
            and all(
                isinstance(envelope, dict) and envelope.get(result_key) for envelope in envelopes
            )
        )

    @staticmethod
    def _notebook_response_matches(
        response: Mapping[str, Any],
        compiled: _CompiledSourceTaskV2,
    ) -> bool:
        spec = compiled.notebook_query
        selection = response.get("selection_trace")
        trace = response.get("trace")
        if spec is None or not isinstance(selection, dict) or not isinstance(trace, dict):
            return False
        if str(trace.get("task") or "") != spec.task:
            return False
        if spec.task is NotebookQueryTaskV2.COMPARE:
            comparison = selection.get("comparison")
            requested = comparison.get("requested") if isinstance(comparison, dict) else None
            return bool(
                spec.comparison is not None
                and requested == spec.comparison.model_dump(mode="json")
                and response.get("comparisons")
            )
        requested_filters = selection.get("requested_filters")
        if not isinstance(requested_filters, dict):
            return False
        expected_filters = spec.filters.model_dump(mode="json")
        exact_fields = {"statuses", "parameter_names", "output_types"}
        identity_fields = set(expected_filters) - exact_fields
        for field in exact_fields:
            if requested_filters.get(field, []) != expected_filters[field]:
                return False
        for field in identity_fields:
            expected_values = expected_filters[field]
            actual_values = requested_filters.get(field, [])
            if bool(actual_values) != bool(expected_values):
                return False
            if expected_values and len(actual_values) != len(expected_values):
                return False
        return True

    def _candidates(
        self,
        rows: list[dict[str, Any]],
        *,
        plan: MultiSourcePlanV2,
        scope: MultiSourceScopeV2,
        required_roles: tuple[str, ...],
        candidate_task: str,
    ) -> tuple[tuple[MultiSourceCandidateV2, ...], int]:
        entity_ids = [str(row.get("entity_id") or "") for row in rows]
        acl_by_id = self.platform.store.entity_acl_refs(
            [entity_id for entity_id in entity_ids if entity_id]
        )
        scopes = self.platform.store.entity_scopes(
            [entity_id for entity_id in entity_ids if entity_id]
        )
        allowed = set(scope.acl_refs)
        contract = next(item for item in plan.source_contracts if item.source == self.source)
        candidates: list[MultiSourceCandidateV2] = []
        rejected = 0
        for row in rows:
            entity_id = str(row.get("entity_id") or "")
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            locator = str(row.get("locator") or "")
            stable_version = str(
                row.get("version") or row.get("content_digest") or row.get("snapshot_digest") or ""
            )
            acl_ref = self._acl_ref(row, metadata, acl_by_id)
            if (
                not entity_id
                or str(row.get("source") or "") != self.source
                or not locator
                or not stable_version
                or acl_ref not in allowed
                or not self.platform._in_scope(
                    row,
                    scopes.get(entity_id, {}),
                    GlobalSearchRequest(
                        query="scope-validation",
                        project_id=scope.project_id,
                        sources=[self.source],
                        commit=scope.target_version,
                        as_of=scope.as_of,
                        allowed_acl_refs=list(scope.acl_refs),
                        enforce_acl=True,
                    ),
                )
            ):
                rejected += 1
                continue
            alignment = self._version_alignment(
                source=self.source,
                stable_version=stable_version,
                target_version=scope.target_version,
            )
            if alignment == "mismatch":
                rejected += 1
                continue
            raw_score = float(row.get("score") or 0.0)
            channel_scores = [("source_score", raw_score)]
            for name in ("lexical_score", "dense_score", "semantic_score"):
                if isinstance(row.get(name), (int, float)):
                    channel_scores.append((name, float(row[name])))
            actual_roles = self._observable_roles(row, metadata, locator)
            matched_roles = tuple(role for role in required_roles if role in actual_roles)
            root = str(
                row.get("root_provenance")
                or metadata.get("root_provenance")
                or metadata.get("root_entity_id")
                or row.get("content_digest")
                or row.get("snapshot_digest")
                or entity_id
            )
            retrieval_unit_id = str(row.get("retrieval_unit_id") or entity_id)
            identity = canonical_sha256_v2(
                {
                    "source": self.source,
                    "entity_id": entity_id,
                    "retrieval_unit_id": retrieval_unit_id,
                    "generation": self.snapshot.index_generation,
                    "locator": locator,
                }
            )
            status = str(row.get("fact_status") or row.get("status") or "unreviewed")
            entity_type = str(row.get("entity_type") or "").strip()
            if not entity_type or not contract.accepts_entity_type(entity_type):
                rejected += 1
                continue
            raw_or_derived = row.get("raw_or_derived")
            if raw_or_derived not in {"raw_fact", "derived_fact"}:
                raw_or_derived = "derived_fact"
            authority = SOURCE_AUTHORITY.get(plan.intent, {}).get(self.source, 0.5)
            candidates.append(
                MultiSourceCandidateV2(
                    candidate_id="candidate-" + identity.removeprefix("sha256:"),
                    entity_id=entity_id,
                    retrieval_unit_id=retrieval_unit_id,
                    parent_entity_id=(
                        str(metadata.get("parent_id")) if metadata.get("parent_id") else None
                    ),
                    source_instance=f"{scope.project_id}:{self.source}",
                    retrieval_domain=self.source,
                    fact_type=(
                        str(row.get("fact_type"))
                        if str(row.get("fact_type") or "").startswith(f"{self.source}.")
                        else f"{self.source}.{entity_type}"
                    ),
                    entity_type=entity_type,
                    task=candidate_task,
                    title=str(row.get("title") or entity_id)[:1_000],
                    snippet=str(row.get("snippet") or "")[:8_000],
                    locator=locator,
                    stable_version=stable_version,
                    source_generation=self.snapshot.index_generation,
                    raw_or_derived=raw_or_derived,
                    derivation=str(row.get("derivation") or "production_retrieval_projection"),
                    review_status=str(row.get("review_status") or "unreviewed"),
                    fact_status=status,
                    channel_scores=tuple(channel_scores),
                    calibrated_relevance=0.0,
                    calibration_version="calibration_unavailable",
                    matched_roles=matched_roles,
                    authority=max(0.0, min(1.0, float(authority))),
                    version_alignment=alignment,
                    acl_ref=acl_ref,
                    token_estimate=max(
                        1,
                        min(
                            8_000,
                            (len(str(row.get("title") or "")) + len(str(row.get("snippet") or "")))
                            // 4
                            + 1,
                        ),
                    ),
                    root_provenance=root,
                    counter_evidence=status.casefold()
                    in {
                        "contradicted",
                        "failed",
                        "rejected",
                        "blocked",
                        "cancelled",
                        "potentially_stale",
                    },
                )
            )
        return tuple(candidates), rejected

    def _acl_ref(
        self,
        row: dict[str, Any],
        metadata: dict[str, Any],
        acl_by_id: dict[str, str],
    ) -> str:
        entity_id = str(row.get("entity_id") or "")
        direct = row.get("acl_ref") or acl_by_id.get(entity_id)
        if direct:
            return str(direct)
        parent_ids = [
            str(value)
            for value in (
                metadata.get("document_id"),
                metadata.get("run_id"),
                row.get("run_id"),
            )
            if value
        ]
        inherited = self.platform.store.entity_acl_refs(parent_ids)
        for parent_id in parent_ids:
            if inherited.get(parent_id):
                return str(inherited[parent_id])
        if self.source == "workspace" and metadata.get("project_id") == self.snapshot.project_id:
            return self.project_acl_ref
        return ""

    @staticmethod
    def _observable_roles(
        row: dict[str, Any],
        metadata: dict[str, Any],
        locator: str,
    ) -> set[str]:
        roles = {
            str(role) for role in (row.get("roles") if isinstance(row.get("roles"), list) else ())
        }
        channels = row.get("channels") if isinstance(row.get("channels"), list) else ()
        roles.update(str(channel) for channel in channels)
        if row.get("evidence_role"):
            roles.add(str(row["evidence_role"]))
        entity_type = str(row.get("entity_type") or "").casefold()
        source = str(row.get("source") or "")
        if locator:
            roles.add("source_location")
        if source == "experiment":
            if "metric" in entity_type:
                roles.update(("metric", "run_or_metric"))
            if "run" in entity_type:
                roles.update(("run", "run_or_metric"))
        if source == "document" and "claim" in entity_type:
            roles.add("claim")
        if source == "code" and row.get("version"):
            roles.update(("current_code", "version"))
        if source == "workspace" and metadata.get("project_id"):
            roles.add("current_state")
        return roles

    @staticmethod
    def _version_alignment(
        *,
        source: str,
        stable_version: str,
        target_version: str | None,
    ) -> Literal[
        "exact", "compatible", "historical_target", "mismatch", "unknown", "not_applicable"
    ]:
        if not target_version:
            return "not_applicable"
        if (
            stable_version == target_version
            or stable_version.startswith(target_version)
            or target_version.startswith(stable_version)
        ):
            return "exact"
        return "mismatch" if source == "code" else "unknown"

    @staticmethod
    def _execution_status(raw_status: str) -> SourceExecutionStatusV2:
        normalized = raw_status.casefold()
        return {
            "complete": SourceExecutionStatusV2.COMPLETE,
            "partial": SourceExecutionStatusV2.PARTIAL,
            "timeout": SourceExecutionStatusV2.TIMEOUT,
            "unavailable": SourceExecutionStatusV2.UNAVAILABLE,
            "unauthorized": SourceExecutionStatusV2.UNAUTHORIZED,
            "not_indexed": SourceExecutionStatusV2.NOT_INDEXED,
            "no_match": SourceExecutionStatusV2.NO_MATCHING_EVIDENCE,
            "error": SourceExecutionStatusV2.PARTIAL,
        }.get(normalized, SourceExecutionStatusV2.PARTIAL)

    def _empty_batch(
        self,
        plan: MultiSourcePlanV2,
        status: SourceExecutionStatusV2,
        error_code: str,
        *,
        latency_ms: float = 0.0,
    ) -> SourceRetrievalBatchV2:
        return build_source_batch_v2(
            source=self.source,
            plan=plan,
            candidates=(),
            execution=SourceExecutionV2(
                source=self.source,
                status=status,
                candidate_count=0,
                coverage=None,
                index_generation=self.snapshot.index_generation,
                watermark=self.snapshot.watermark,
                latency_ms=max(0.0, latency_ms),
                error_code=error_code,
            ),
        )


class ProductionEvidenceEdgeProviderV2:
    """Resolve only real, reviewed, in-scope edges between retrieved candidates."""

    def __init__(self, platform: PlatformService, project_id: str) -> None:
        self.platform = platform
        self.project_id = project_id
        self.last_blockers: tuple[str, ...] = ()

    def resolve(
        self,
        *,
        plan: MultiSourcePlanV2,
        candidates: tuple[MultiSourceCandidateV2, ...],
    ) -> tuple[EvidenceEdgeV2, ...]:
        self.last_blockers = ()
        if plan.scope.project_id != self.project_id or not candidates:
            return ()
        by_entity = {item.entity_id: item for item in candidates}
        try:
            stored = self.platform.store.edges_for(
                sorted(by_entity),
                project_id=self.project_id,
            )
        except Exception as error:
            self.last_blockers = (f"edge_store_unavailable:{type(error).__name__}",)
            return ()
        registry = build_default_edge_registry_v2()
        edge_types = {item.predicate: item for item in registry.edge_types}
        resolved: list[EvidenceEdgeV2] = []
        for edge in stored:
            source = by_entity.get(str(edge.get("source") or ""))
            target = by_entity.get(str(edge.get("target") or ""))
            predicate = str(edge.get("predicate") or "").upper()
            definition = edge_types.get(predicate)
            derivation = str(edge.get("derivation") or "")
            review_status = str(edge.get("review_status") or "").casefold()
            if (
                source is None
                or target is None
                or not str(edge.get("id") or "")
                or definition is None
                or review_status not in {"confirmed", "reviewed"}
                or derivation not in definition.allowed_derivations
                or source.acl_ref != target.acl_ref
                or source.acl_ref not in plan.scope.acl_refs
                or source.version_alignment == "mismatch"
                or target.version_alignment == "mismatch"
            ):
                continue
            fact_status = (
                EdgeFactStatusV2.VERIFIED
                if derivation in {"human_confirmed", "deterministic_after_review"}
                else EdgeFactStatusV2.CANDIDATE
            )
            try:
                resolved.append(
                    build_evidence_edge_v2(
                        predicate=predicate,
                        source=source,
                        target=target,
                        derivation=derivation,
                        review_status=EdgeReviewStatusV2.REVIEWED,
                        fact_status=fact_status,
                        confidence=float(edge.get("confidence") or 0.0),
                        evidence_locator=str(edge.get("id") or ""),
                    )
                )
            except ValueError:
                continue
        if plan.graph_templates and len(candidates) > 1 and not resolved:
            self.last_blockers = ("reviewed_edge_evidence_unavailable",)
        return tuple(sorted(resolved, key=lambda item: item.edge_id))


class MultiSourceRuntimeV2:
    """Prepare and explicitly execute governed V2 without changing the V1 default."""

    def __init__(
        self,
        *,
        platform: PlatformService,
        calibration_artifacts: Mapping[str, ReviewedCalibrationArtifactV2] | None = None,
        performance_runtime: RuntimePerformanceV2 | None = None,
        component_switches: GlobalComponentSwitchesV2 | None = None,
        max_workers: int = 4,
        deadline_seconds: float = 3.0,
    ) -> None:
        self.platform = platform
        self.calibration_artifacts = dict(calibration_artifacts or {})
        self.performance_runtime = performance_runtime
        self.component_switches = component_switches or GlobalComponentSwitchesV2()
        self.max_workers = max_workers
        self.deadline_seconds = deadline_seconds
        for source, artifact in self.calibration_artifacts.items():
            if source not in MULTISOURCE_DOMAINS or artifact.source != source:
                raise ValueError("calibration artifact registry mismatch")

    def _component_rollback_preparation(
        self,
    ) -> MultiSourceRuntimePreparationV2 | None:
        blocker = self.component_switches.rollback_blocker
        if blocker is None:
            return None
        return MultiSourceRuntimePreparationV2(
            status="unavailable",
            blockers=(blocker,),
            scope=None,
            registry=None,
            source_snapshots={},
            pipeline_request=None,
            routed_sources=(),
            retrievers={},
            calibrations={},
            edge_provider=None,
        )

    def prepare(
        self,
        request: MultiSourceRuntimeRequestV2,
    ) -> MultiSourceRuntimePreparationV2:
        rollback = self._component_rollback_preparation()
        if rollback is not None:
            return rollback
        project, scope_blockers = self._scope(request)
        project_acl_ref = str(project.get("acl_ref") or "") if project else ""
        snapshots = {
            source: _source_snapshot(
                self.platform,
                project_id=request.project_id,
                source=source,
            )
            for source in MULTISOURCE_DOMAINS
        }
        capabilities = [
            _build_capability(
                platform=self.platform,
                snapshot=snapshots[source],
                calibration=self.calibration_artifacts.get(source),
            )
            for source in MULTISOURCE_DOMAINS
        ]
        registry = _build_registry(capabilities)
        edge_provider = ProductionEvidenceEdgeProviderV2(
            self.platform,
            request.project_id,
        )
        if scope_blockers:
            return MultiSourceRuntimePreparationV2(
                status="unavailable",
                blockers=tuple(scope_blockers),
                scope=None,
                registry=registry,
                source_snapshots=snapshots,
                pipeline_request=None,
                routed_sources=(),
                retrievers={},
                calibrations={},
                edge_provider=edge_provider,
            )
        scope = MultiSourceScopeV2(
            project_id=request.project_id,
            acl_refs=tuple(sorted({*request.acl_refs, project_acl_ref})),
            as_of=request.as_of,
            target_version=request.target_version,
            source_task_parameters=_source_task_parameters_v2(request),
        )
        pipeline_request = MultiSourcePipelineRequestV2(
            project_id=request.project_id,
            request_id=request.request_id,
            question=request.question,
            intent=request.intent,
            scope=scope,
            requested_sources=request.requested_sources,
            context_budget_tokens=request.context_budget_tokens,
            claims=request.claims,
        )
        plan = plan_multisource_query_v2(
            question=request.question,
            intent=request.intent,
            scope=scope,
            registry=registry,
            requested_sources=request.requested_sources,
            context_budget_tokens=request.context_budget_tokens,
        )
        blockers: list[str] = []
        retrievers: dict[str, MultiSourceRetrieverV2] = {}
        calibrations: dict[str, SourceCalibrationProfileV2] = {}
        source_registry = getattr(self.platform, "source_runtime_v2", None)
        authority_check = getattr(source_registry, "global_v2_authorized", None)
        for source in plan.source_routes:
            if source == "code":
                continue
            if not callable(authority_check) or not authority_check(
                project_id=request.project_id,
                source=source,
            ):
                blockers.append(f"source_release_authority_unavailable:{source}")
        for source in request.requested_sources:
            snapshot = snapshots[source]
            if not snapshot.indexed:
                blockers.append(f"not_indexed:{source}")
            elif _adapter_component(self.platform, source) is None:
                blockers.append(f"adapter_unavailable:{source}")
            elif self.calibration_artifacts.get(source) is None:
                blockers.append(f"calibration_unavailable:{source}")
        for source in plan.source_routes:
            snapshot = snapshots[source]
            if not snapshot.indexed:
                blockers.append(f"not_indexed:{source}")
                continue
            if _adapter_component(self.platform, source) is None:
                blockers.append(f"adapter_unavailable:{source}")
                continue
            retrievers[source] = ProductionSourceRetrieverV2(
                platform=self.platform,
                source=source,
                snapshot=snapshot,
                project_acl_ref=project_acl_ref,
                performance_runtime=self.performance_runtime,
                embedding_generation=self.component_switches.embedding_generation,
            )
            artifact = self.calibration_artifacts.get(source)
            if artifact is None:
                blockers.append(f"calibration_unavailable:{source}")
            else:
                calibrations[source] = artifact.profile
        if not plan.source_routes:
            blockers.append("no_capable_source")
        status: Literal["ready", "partial", "unavailable"]
        if not plan.source_routes or not retrievers:
            status = "unavailable"
        elif blockers:
            status = "partial"
        else:
            status = "ready"
        return MultiSourceRuntimePreparationV2(
            status=status,
            blockers=tuple(dict.fromkeys(blockers)),
            scope=scope,
            registry=registry,
            source_snapshots=snapshots,
            pipeline_request=pipeline_request,
            routed_sources=plan.source_routes,
            retrievers=retrievers,
            calibrations=calibrations,
            edge_provider=edge_provider,
        )

    def run_v2(
        self,
        request: MultiSourceRuntimeRequestV2,
        *,
        deployed_stage: ReleaseStageV2 = ReleaseStageV2.OFFLINE,
    ) -> MultiSourceRuntimeExecutionV2:
        rollback = self._component_rollback_preparation()
        if rollback is not None:
            return MultiSourceRuntimeExecutionV2(
                status="unavailable",
                blockers=rollback.blockers,
                preparation=rollback,
                pipeline_result=None,
            )
        started_at = time.perf_counter()
        preparation = self.prepare(request)
        if not preparation.ready or preparation.pipeline_request is None:
            execution = MultiSourceRuntimeExecutionV2(
                status=("unavailable" if preparation.status == "unavailable" else "partial"),
                blockers=preparation.blockers,
                preparation=preparation,
                pipeline_result=None,
            )
        else:
            assert preparation.registry is not None
            assert preparation.edge_provider is not None
            pipeline = GovernedMultiSourcePipelineV2(
                registry=preparation.registry,
                retrievers=preparation.retrievers,
                calibrations=preparation.calibrations,
                edge_provider=preparation.edge_provider,
                component_switches=self.component_switches,
                max_workers=self.max_workers,
                deadline_seconds=request.deadline_ms / 1_000,
            )
            result = pipeline.run(
                preparation.pipeline_request,
                deployed_stage=deployed_stage,
                explicit_v2=True,
            )
            blockers = tuple(
                dict.fromkeys(
                    (
                        *preparation.blockers,
                        *preparation.edge_provider.last_blockers,
                    )
                )
            )
            status: Literal["completed", "partial", "unavailable"] = (
                "completed" if result.status == "completed" and not blockers else "partial"
            )
            execution = MultiSourceRuntimeExecutionV2(
                status=status,
                blockers=blockers,
                preparation=preparation,
                pipeline_result=result,
            )
        self._record_performance(request, execution, started_at=started_at)
        return execution

    def _record_performance(
        self,
        request: MultiSourceRuntimeRequestV2,
        execution: MultiSourceRuntimeExecutionV2,
        *,
        started_at: float,
    ) -> None:
        """Record aggregate runtime facts without retaining query or ACL content."""

        if self.performance_runtime is None:
            return
        result = execution.pipeline_result
        selected = (
            len(result.fusion.selected) if result is not None and result.fusion is not None else 0
        )
        tokens = (
            result.evidence_pack.used_tokens
            if result is not None and result.evidence_pack is not None
            else 0
        )
        trace_sha256 = canonical_sha256_v2(
            {
                "project_id": request.project_id,
                "request_id": request.request_id,
                "runtime_version": MULTISOURCE_RUNTIME_VERSION,
            }
        )
        self.performance_runtime.record(
            PerformanceSpanV2(
                trace_id=f"trace-{trace_sha256.removeprefix('sha256:')[:32]}",
                span="global_v2_execution",
                source=None,
                latency_ms=max(0.0, (time.perf_counter() - started_at) * 1_000),
                candidate_count=selected,
                filtered_count=0,
                token_count=tokens,
                cost_units=0.0,
                cache_hit=False,
                error_code=(None if execution.status == "completed" else execution.status),
                index_generation=None,
            )
        )

    def search(
        self,
        request: GlobalSearchRequest,
        *,
        engine_override: str | None,
        fallback: Callable[[], dict[str, Any]],
        intent: str | None = None,
        request_id: str | None = None,
        context_budget_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Route one global request without changing or duplicating the V1 default."""

        engine = validate_global_engine_override(engine_override)
        if engine == "v1":
            return fallback()
        rollback_blocker = self.component_switches.rollback_blocker
        if rollback_blocker is not None:
            return self._fallback_to_legacy(
                fallback,
                status="unavailable",
                blockers=(rollback_blocker,),
            )
        execution = self.run_v2(
            MultiSourceRuntimeRequestV2(
                project_id=request.project_id,
                request_id=request_id or f"global-runtime-{uuid4().hex}",
                question=request.query,
                intent=intent or request.intent or "global_synthesis",
                acl_refs=tuple(sorted(set(request.allowed_acl_refs))),
                enforce_acl=request.enforce_acl,
                requested_sources=tuple(request.sources),
                experiment_ids=tuple(sorted(set(request.experiment_ids))),
                experiment_analysis=(
                    request.experiment_analysis.model_copy(deep=True)
                    if request.experiment_analysis is not None
                    else None
                ),
                notebook_query=(
                    request.notebook_query.model_copy(deep=True)
                    if request.notebook_query is not None
                    else None
                ),
                as_of=request.as_of,
                target_version=request.commit,
                context_budget_tokens=(
                    context_budget_tokens
                    if context_budget_tokens is not None
                    else min(32_000, max(256, request.limit * 512))
                ),
                deadline_ms=(
                    request.deadline_ms
                    if request.deadline_ms is not None
                    else round(self.deadline_seconds * 1_000)
                ),
            )
        )
        pipeline_result = execution.pipeline_result
        source_status = pipeline_result.trace.source_status if pipeline_result is not None else ()
        all_sources_unavailable = (
            pipeline_result is not None
            and pipeline_result.trace.candidate_count == 0
            and bool(source_status)
            and all(
                status
                in {
                    SourceExecutionStatusV2.NOT_INDEXED.value,
                    SourceExecutionStatusV2.PARTIAL.value,
                    SourceExecutionStatusV2.TIMEOUT.value,
                    SourceExecutionStatusV2.UNAVAILABLE.value,
                    SourceExecutionStatusV2.UNAUTHORIZED.value,
                }
                for _, status in source_status
            )
        )
        if (
            pipeline_result is None
            or pipeline_result.evidence_pack is None
            or pipeline_result.fusion is None
            or pipeline_result.plan is None
            or all_sources_unavailable
        ):
            blockers = list(execution.blockers)
            if all_sources_unavailable:
                blockers.append("all_sources_unavailable")
            return self._fallback_to_legacy(
                fallback,
                status=execution.status,
                blockers=tuple(dict.fromkeys(blockers)),
            )
        return self._project_v2(request, execution)

    @staticmethod
    def _fallback_to_legacy(
        fallback: Callable[[], dict[str, Any]],
        *,
        status: str,
        blockers: tuple[str, ...],
    ) -> dict[str, Any]:
        legacy = fallback()
        trace = dict(legacy.get("trace") or {})
        trace["global_engine"] = {
            "requested": "v2",
            "selected": "v1",
            "served": "v1",
            "status": status,
            "fallback": "legacy_v1",
            "blockers": list(blockers),
            "runtime_version": MULTISOURCE_RUNTIME_VERSION,
            "quality_qualified": False,
        }
        legacy["trace"] = trace
        return legacy

    def operational_snapshot(self) -> dict[str, object]:
        """Return sanitized release/runtime counters for Runtime or API projection."""

        reviewed_slices = tuple(
            sorted(
                f"{source}:{artifact.profile.task}:{artifact.profile.entity_type}"
                for source, artifact in self.calibration_artifacts.items()
            )
        )
        required_slices = tuple(
            f"{source}:unspecified:unspecified" for source in MULTISOURCE_DOMAINS
        )
        source_registry = getattr(self.platform, "source_runtime_v2", None)
        source_snapshot = (
            source_registry.operational_snapshot()
            if source_registry is not None
            and callable(getattr(source_registry, "operational_snapshot", None))
            else {
                "status": "UNAVAILABLE",
                "materialized_source_count": 0,
            }
        )
        performance = (
            self.performance_runtime.operational_snapshot(
                reviewed_calibration_slices=reviewed_slices,
                required_calibration_slices=required_slices,
            )
            if self.performance_runtime is not None
            else {
                "status": "UNAVAILABLE",
                "calibration_availability": "UNAVAILABLE",
            }
        )
        return {
            "runtime_version": MULTISOURCE_RUNTIME_VERSION,
            "status": "QUALITY_HOLD",
            "default_engine": "v1",
            "quality_qualified": False,
            "reviewed_calibration_count": len(self.calibration_artifacts),
            "required_calibration_slice_count": len(required_slices),
            "component_switches": self.component_switches.operational_snapshot(),
            "sources": source_snapshot,
            "performance": performance,
        }

    @staticmethod
    def _project_v2(
        request: GlobalSearchRequest,
        execution: MultiSourceRuntimeExecutionV2,
    ) -> dict[str, Any]:
        result = execution.pipeline_result
        if result is None or result.evidence_pack is None or result.fusion is None:
            raise ValueError("completed global V2 execution is missing governed evidence")
        pack = result.evidence_pack
        selected = {item.entity_id: item for item in result.fusion.selected}
        selected_by_candidate = {item.candidate_id: item for item in result.fusion.selected}
        facts = (
            *pack.verified_facts,
            *pack.reported_facts,
            *pack.observed_facts,
            *pack.inferred_facts,
        )
        rows: list[dict[str, Any]] = []
        for fact in facts:
            candidate = selected.get(fact.entity_id)
            if candidate is None:
                raise ValueError("global V2 projection candidate identity mismatch")
            rows.append(
                {
                    "entity_id": fact.entity_id,
                    "source": fact.source,
                    "entity_type": fact.entity_type,
                    "title": candidate.title,
                    "subtitle": fact.status.value,
                    "snippet": fact.text,
                    "locator": fact.locator,
                    "version": fact.stable_version,
                    "status": fact.status.value,
                    # The evidence-pack status is a comprehension partition.  The
                    # candidate fields below remain the governed source truth used
                    # by QueryService to decide whether evidence is actually
                    # review-qualified.  In particular, reported/inferred evidence
                    # must never become verified merely because it survived fusion.
                    "fact_status": candidate.fact_status,
                    "review_status": candidate.review_status,
                    "derivation": candidate.derivation,
                    "raw_or_derived": candidate.raw_or_derived,
                    "score": candidate.calibrated_relevance,
                    "channels": [name for name, _ in candidate.channel_scores],
                    "authority": candidate.authority,
                    "version_alignment": candidate.version_alignment,
                    "repository_id": None,
                    "path": None,
                    "start_line": None,
                    "end_line": None,
                    "thread_id": None,
                    "roles": list(fact.matched_roles),
                    "content_digest": fact.content_sha256,
                    "metadata": {
                        "retrieval_unit_id": fact.retrieval_unit_id,
                        "root_provenance": fact.root_provenance,
                        "source_generation": fact.source_generation,
                        "counter_evidence": fact.counter_evidence,
                        "governance": {
                            "acl_authorized": True,
                            "review_status_preserved": True,
                            "version_alignment": candidate.version_alignment,
                        },
                    },
                }
            )
        citations_by_fact = {item.fact_id: item for item in pack.citations}
        citation_map = {}
        for fact in facts:
            citation = citations_by_fact[fact.fact_id]
            citation_map[citation.citation_id] = {
                "entity_id": fact.entity_id,
                "source": fact.source,
                "locator": citation.locator,
                "version": citation.stable_version,
            }
        source_traces = {
            source: {
                "status": status,
                "index_generation": generation,
                "watermark": watermark,
                "candidate_count": sum(item["source"] == source for item in rows),
            }
            for source, status, generation, watermark in pack.source_status
        }
        generations = sorted(
            {generation for _, _, generation, _ in pack.source_status if generation is not None}
        )
        accepted_edge_ids = {
            edge_id
            for path in (result.traversal.accepted_paths if result.traversal is not None else ())
            for edge_id in path.edge_ids
        }
        typed_edges = (
            execution.preparation.edge_provider.resolve(
                plan=result.plan,
                candidates=result.fusion.selected,
            )
            if result.plan is not None and accepted_edge_ids
            else ()
        )
        relations = []
        projected_entity_ids = {str(item["entity_id"]) for item in rows}
        for edge in typed_edges:
            if edge.edge_id not in accepted_edge_ids:
                continue
            source = selected_by_candidate.get(edge.source_candidate_id)
            target = selected_by_candidate.get(edge.target_candidate_id)
            if source is None or target is None:
                raise ValueError("global V2 projection traversal endpoint mismatch")
            if (
                source.entity_id not in projected_entity_ids
                or target.entity_id not in projected_entity_ids
            ):
                continue
            relations.append(
                {
                    "id": edge.edge_id,
                    "predicate": edge.predicate.casefold(),
                    "source": source.entity_id,
                    "target": target.entity_id,
                    "derivation": edge.derivation,
                    # ProductionEvidenceEdgeProviderV2 has already required a
                    # confirmed/reviewed stored edge plus matching ACL and
                    # versions.  "confirmed" is the established QueryService
                    # compatibility value; typed_review_status retains the V2
                    # contract value without weakening that admission check.
                    "review_status": "confirmed",
                    "typed_review_status": edge.review_status.value,
                    "fact_status": edge.fact_status.value,
                    "confidence": edge.confidence,
                    "source_version": edge.source_version,
                    "target_version": edge.target_version,
                    "evidence_locator": edge.evidence_locator,
                    "typed_relation": True,
                    "acl_authorized": True,
                    "version_authorized": True,
                }
            )
        relations.sort(key=lambda item: item["id"])
        return {
            "query_id": f"global-v2-query://{result.trace.trace_id}",
            "query": request.query,
            "project_id": request.project_id,
            "total": len(rows),
            "results": rows,
            "evidence_pack": {
                "citation_map": citation_map,
                "relations": relations,
                "related_entities": [],
                "context": {
                    "schema_version": "governed-multisource-context-v2",
                    "evidence_pack_sha256": pack.content_sha256,
                    "decision": pack.decision.model_dump(mode="json"),
                    "used_tokens": pack.used_tokens,
                    "token_budget": pack.token_budget,
                },
                "source_contexts": {},
                "query_plan": result.plan.model_dump(mode="json") if result.plan else None,
                "conflicts_and_staleness": list(pack.version_differences),
                "global_context": {
                    "answer": None,
                    "answer_handoff": (
                        {
                            "authority": result.answer.authority.value,
                            "mode": result.answer.mode.value,
                            "content_sha256": result.answer.content_sha256,
                        }
                        if result.answer is not None
                        else None
                    ),
                    "final_answer_authority": "query_service",
                    "evidence_pack_sha256": pack.content_sha256,
                },
            },
            "trace": {
                "sources": source_traces,
                "fusion": "governed-role-fusion-v2",
                "planner": {
                    "version": result.pipeline_version,
                    "plan_digest": result.plan.content_sha256 if result.plan else None,
                    "routed_sources": list(execution.preparation.routed_sources),
                },
                "status": execution.status.upper(),
                "global_engine": {
                    "requested": "v2",
                    "selected": "v2",
                    "served": "v2",
                    "status": execution.status,
                    "fallback": None,
                    "blockers": list(execution.blockers),
                    "runtime_version": MULTISOURCE_RUNTIME_VERSION,
                    "quality_qualified": False,
                },
                "governed_trace": result.trace.model_dump(mode="json"),
                "release": {
                    "decision": "HOLD_DEFAULT_V1",
                    "default_engine": "v1",
                    "quality_qualified": False,
                    "rollback": f"omit {GLOBAL_ENGINE_HEADER}",
                },
            },
            "index_generations": generations,
        }

    def _scope(
        self,
        request: MultiSourceRuntimeRequestV2,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        try:
            with self.platform.store.database.connection() as db:
                row = db.execute(
                    "SELECT id, acl_ref, status FROM projects WHERE id=?",
                    (request.project_id,),
                ).fetchone()
        except Exception as error:
            return None, [f"project_scope_unavailable:{type(error).__name__}"]
        if row is None:
            return None, ["project_not_found"]
        project = dict(row)
        if str(project.get("status") or "") != "active":
            return project, ["project_unavailable"]
        project_acl_ref = str(project.get("acl_ref") or "")
        if not project_acl_ref:
            return project, ["project_acl_unavailable"]
        if (
            request.enforce_acl
            and project_acl_ref != "public"
            and project_acl_ref not in set(request.acl_refs)
        ):
            return project, ["unauthorized"]
        return project, []


__all__ = [
    "GLOBAL_ENGINE_HEADER",
    "MULTISOURCE_RUNTIME_VERSION",
    "GlobalEngineOverrideError",
    "MultiSourceRuntimeExecutionV2",
    "MultiSourceRuntimePreparationV2",
    "MultiSourceRuntimeRequestV2",
    "MultiSourceRuntimeV2",
    "ProductionEvidenceEdgeProviderV2",
    "ProductionSourceRetrieverV2",
    "ProductionSourceSnapshotV2",
    "ReviewedCalibrationArtifactV2",
    "ReviewedCalibrationBundleV2",
    "build_reviewed_calibration_artifact_v2",
    "build_reviewed_calibration_bundle_v2",
    "load_reviewed_calibration_bundle_v2",
    "validate_global_engine_override",
]
