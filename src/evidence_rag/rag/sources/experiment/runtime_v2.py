"""Production-store facade for the complete Experiment source pipeline.

Only normalized records and provenance held by the application's current
Experiment/RawSource/Workspace services are admitted.  The facade never reads
Golden labels or fixture recipes, keeps V1 as the default, and fails closed for
an explicit V2 request when source authority cannot be proven.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .analysis_v2 import (
    ExperimentAggregationResultV2,
    ExperimentComparabilityResultV2,
    ExperimentComparisonV2,
    ExperimentReproductionResultV2,
    aggregate_experiment_run_group_v2,
    compare_experiment_snapshots_v2,
    compare_metric_v2,
    evaluate_reproduction_v2,
)
from .contracts_v2 import (
    ExperimentMetricDefinitionV2,
    ExperimentMetricObservationV2,
    ExperimentMetricRegistryEntryV2,
    ExperimentRunSnapshotV2,
    build_experiment_run_group_v2,
    build_experiment_run_snapshot_v2,
    canonical_json_bytes_v2,
    canonical_sha256_v2,
)
from .pipeline_v2 import (
    EXPERIMENT_PIPELINE_VERSION,
    ExperimentBuildInputV2,
    ExperimentQueryResultV2,
    ExperimentSourcePipelineV2,
)
from .query_v2 import (
    ExperimentAggregationV2,
    ExperimentNumericResultV2,
    ExperimentQueryTaskV2,
    ExperimentQueryV2,
    ExperimentQueryV2Error,
    evaluate_experiment_numeric_v2,
    filter_experiment_snapshots_v2,
    parse_experiment_query_v2,
)
from .semantic_v2 import (
    ExperimentContextV2,
    ExperimentSemanticResultV2,
    build_experiment_context_v2,
)
from .store_v2 import (
    EXPERIMENT_STORE_SCHEMA_VERSION,
    ExperimentStoreV2,
    ExperimentStoreV2Error,
)

EXPERIMENT_SOURCE_RUNTIME_VERSION = "experiment-source-runtime-v2"
_EVENT_SCAN_LIMIT = 100_001


class ExperimentProductionServiceV2(Protocol):
    """The service shape already assembled by ``create_runtime``."""

    store: Any
    workspace: Any
    sources: Any


class ExperimentSourceRuntimeErrorV2(ValueError):
    """Base error whose public text is only a bounded reason code."""


class ExperimentSourceRuntimeUnavailableV2(ExperimentSourceRuntimeErrorV2):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ExperimentRuntimeIdentityV2:
    formal_experiment_id: str
    pipeline_experiment_id: str
    formal_run_id: str
    pipeline_run_id: str


@dataclass(frozen=True, slots=True)
class ExperimentSourceAuthorityV2:
    project_id: str
    formal_source_id: str
    pipeline_source_id: str
    acl_ref: str
    source_generation: str
    watermark: str
    observed_at: str
    run_count: int


@dataclass(frozen=True, slots=True)
class ExperimentAnalysisSpecV2:
    metric_name: str | None = None
    baseline_run_id: str | None = None
    candidate_run_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    aggregation: Literal["mean", "median", "std", "min", "max", "count"] | None = None
    split: str | None = None
    reproduction_roles: tuple[
        Literal[
            "command",
            "code_commit",
            "dataset_version",
            "config_snapshot",
            "environment_snapshot",
            "seed",
            "required_artifacts",
        ],
        ...,
    ] = ()
    treatment_config_keys: frozenset[str] = frozenset()
    controlled_config_keys: frozenset[str] = frozenset()
    fail_closed: bool = False


@dataclass(frozen=True, slots=True)
class ExperimentRuntimeAnalysisV2:
    numeric: tuple[ExperimentNumericResultV2, ...] = ()
    comparability: tuple[ExperimentComparabilityResultV2, ...] = ()
    comparisons: tuple[ExperimentComparisonV2, ...] = ()
    aggregations: tuple[ExperimentAggregationResultV2, ...] = ()
    reproduction: tuple[ExperimentReproductionResultV2, ...] = ()
    requested_reproduction_roles: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExperimentSourceRuntimeResultV2:
    authority: ExperimentSourceAuthorityV2
    identities: tuple[ExperimentRuntimeIdentityV2, ...]
    pipeline: ExperimentQueryResultV2
    context: ExperimentContextV2
    analysis: ExperimentRuntimeAnalysisV2
    selected_engine: str = "v2"
    runtime_version: str = EXPERIMENT_SOURCE_RUNTIME_VERSION


@dataclass(frozen=True, slots=True)
class _LoadedExperimentSource:
    authority: ExperimentSourceAuthorityV2
    identities: tuple[ExperimentRuntimeIdentityV2, ...]
    runs: tuple[Mapping[str, Any], ...]
    experiment_metadata: Mapping[str, Mapping[str, Any]]


def _authorized(acl_ref: str, allowed_acl_refs: Iterable[str]) -> bool:
    return acl_ref == "public" or acl_ref in set(allowed_acl_refs)


def _pipeline_identity(prefix: str, formal_identity: str) -> str:
    return prefix + "-" + hashlib.sha256(formal_identity.encode("utf-8")).hexdigest()


class ExperimentSourceRuntimeV2:
    """Materialize current formal source data into the existing isolated V2 chain."""

    def __init__(
        self,
        service: ExperimentProductionServiceV2,
        v2_store: ExperimentStoreV2,
        *,
        metric_registry: Mapping[str, ExperimentMetricRegistryEntryV2] | None = None,
    ) -> None:
        self.service = service
        self.v2_store = v2_store
        self.metric_registry = dict(metric_registry or {})
        self.pipeline = ExperimentSourcePipelineV2(v2_store)

    def execute(
        self,
        *,
        engine: str | None,
        legacy: Callable[[], object] | None,
        project_id: str,
        allowed_acl_refs: Iterable[str],
        source_id: str,
        query_request: Mapping[str, Any],
        semantic_query: str,
        analysis: ExperimentAnalysisSpecV2 | None = None,
        expected_source_generation: str | None = None,
        expected_watermark: str | None = None,
        budget_tokens: int = 1_500,
        dense_profile: str | None = None,
    ) -> object:
        """Keep the exact legacy result unless V2 is explicitly selected."""

        selected = "v1" if engine is None else engine
        if selected not in {"v1", "v2"}:
            raise ExperimentSourceRuntimeErrorV2("invalid_engine")
        if selected == "v1":
            if legacy is None:
                raise ExperimentSourceRuntimeErrorV2("legacy_callable_required")
            return legacy()
        return self.query_v2(
            project_id=project_id,
            allowed_acl_refs=allowed_acl_refs,
            source_id=source_id,
            query_request=query_request,
            semantic_query=semantic_query,
            analysis=analysis,
            expected_source_generation=expected_source_generation,
            expected_watermark=expected_watermark,
            budget_tokens=budget_tokens,
            dense_profile=dense_profile,
        )

    def query_v2(
        self,
        *,
        project_id: str,
        allowed_acl_refs: Iterable[str],
        source_id: str,
        query_request: Mapping[str, Any],
        semantic_query: str,
        analysis: ExperimentAnalysisSpecV2 | None = None,
        expected_source_generation: str | None = None,
        expected_watermark: str | None = None,
        budget_tokens: int = 1_500,
        dense_profile: str | None = None,
    ) -> ExperimentSourceRuntimeResultV2:
        allowed = tuple(dict.fromkeys(allowed_acl_refs))
        loaded = self._load(
            project_id=project_id,
            allowed_acl_refs=allowed,
            source_id=source_id,
            expected_source_generation=expected_source_generation,
            expected_watermark=expected_watermark,
        )
        translated = self._translate_query(query_request, loaded)
        try:
            query = parse_experiment_query_v2(
                translated,
                project_id=loaded.authority.project_id,
                source_id=loaded.authority.pipeline_source_id,
                acl_ref=loaded.authority.acl_ref,
            )
        except (ExperimentQueryV2Error, TypeError, ValueError) as exc:
            raise ExperimentSourceRuntimeUnavailableV2("typed_query_rejected") from exc
        if query.unresolved:
            raise ExperimentSourceRuntimeUnavailableV2("query_requires_clarification")

        try:
            self._ensure_generation(loaded)
            base = self.pipeline.query(
                query,
                semantic_query=semantic_query,
                budget_tokens=budget_tokens,
                dense_profile=dense_profile,
            )
            base = self._scope_result(query, base)
            snapshots = self._selected_snapshots(query, base)
            computed = self._analyze(
                query,
                snapshots,
                analysis or ExperimentAnalysisSpecV2(),
                loaded,
            )
            context = build_experiment_context_v2(
                query,
                snapshots,
                comparisons=computed.comparisons,
                aggregations=computed.aggregations,
                comparability=computed.comparability,
                reproduction=computed.reproduction,
                budget_tokens=budget_tokens,
            )
            result = self._replace_context(base, context)
        except ExperimentSourceRuntimeUnavailableV2:
            raise
        except (ExperimentStoreV2Error, TypeError, ValueError) as exc:
            raise ExperimentSourceRuntimeUnavailableV2("pipeline_rejected_source") from exc

        current = self._load(
            project_id=project_id,
            allowed_acl_refs=allowed,
            source_id=source_id,
            expected_source_generation=loaded.authority.source_generation,
            expected_watermark=loaded.authority.watermark,
        )
        if current.authority != loaded.authority or current.identities != loaded.identities:
            raise ExperimentSourceRuntimeUnavailableV2("source_changed_during_query")
        return ExperimentSourceRuntimeResultV2(
            authority=loaded.authority,
            identities=loaded.identities,
            pipeline=result,
            context=context,
            analysis=computed,
        )

    def _load(
        self,
        *,
        project_id: str,
        allowed_acl_refs: tuple[str, ...],
        source_id: str,
        expected_source_generation: str | None,
        expected_watermark: str | None,
    ) -> _LoadedExperimentSource:
        project = self.service.workspace.store.get_project(project_id)
        if project is None:
            raise ExperimentSourceRuntimeUnavailableV2("project_not_found")
        if str(project.get("status") or "") != "active":
            raise ExperimentSourceRuntimeUnavailableV2("project_inactive")
        if not _authorized(str(project.get("acl_ref") or ""), allowed_acl_refs):
            raise ExperimentSourceRuntimeUnavailableV2("project_acl_denied")

        sources = [
            item
            for item in self.service.store.list_sources(project_id)
            if item.get("id") == source_id
        ]
        if len(sources) != 1:
            raise ExperimentSourceRuntimeUnavailableV2("source_not_found")
        source = sources[0]
        if source.get("project_id") != project_id or source.get("status") != "ready":
            raise ExperimentSourceRuntimeUnavailableV2("source_not_ready")
        if self.service.sources is None:
            raise ExperimentSourceRuntimeUnavailableV2("raw_source_service_unavailable")

        all_events = self.service.sources.store.list_events(project_id, _EVENT_SCAN_LIMIT)
        if len(all_events) >= _EVENT_SCAN_LIMIT:
            raise ExperimentSourceRuntimeUnavailableV2("source_event_window_exceeded")
        matching = [
            item
            for item in all_events
            if item.get("source_type") == "mlflow"
            and item.get("source_instance") == source.get("tracking_uri")
        ]
        if not matching:
            raise ExperimentSourceRuntimeUnavailableV2("source_events_unavailable")
        latest_by_object: dict[str, dict[str, Any]] = {}
        for event in sorted(
            matching,
            key=lambda item: (str(item.get("observed_at") or ""), str(item.get("event_id") or "")),
        ):
            latest_by_object[str(event.get("source_object_id") or "")] = event
        events = tuple(latest_by_object[key] for key in sorted(latest_by_object))
        if any(item.get("status") != "persisted" for item in events):
            raise ExperimentSourceRuntimeUnavailableV2("source_event_unsafe")
        acl_refs = {str(item.get("acl_ref") or "") for item in events}
        if len(acl_refs) != 1:
            raise ExperimentSourceRuntimeUnavailableV2("source_acl_mixed")
        acl_ref = next(iter(acl_refs))
        if not _authorized(acl_ref, allowed_acl_refs):
            raise ExperimentSourceRuntimeUnavailableV2("source_acl_denied")
        for event in events:
            raw = self.service.sources.store.get_object(str(event.get("raw_object_id") or ""))
            if (
                raw is None
                or raw.get("state") != "active"
                or raw.get("project_id") != project_id
                or raw.get("acl_ref") != acl_ref
                or raw.get("source_version") != event.get("source_version")
                or raw.get("content_hash") != event.get("content_hash")
            ):
                raise ExperimentSourceRuntimeUnavailableV2("raw_source_authority_mismatch")

        experiment_map: dict[str, str] = {}
        for event in events:
            external_experiment_id = str((event.get("metadata") or {}).get("experiment_id") or "")
            mapped = self.service.store.source_mapping(source_id, external_experiment_id)
            if not external_experiment_id or mapped is None:
                raise ExperimentSourceRuntimeUnavailableV2("source_mapping_unavailable")
            experiment_map[external_experiment_id] = mapped
        mapped_experiment_ids = set(experiment_map.values())
        event_by_external_run = {str(item.get("source_object_id") or ""): item for item in events}
        listed_runs = [
            item
            for item in self.service.store.list_runs(project_id)
            if item.get("experiment_id") in mapped_experiment_ids
            and str(item.get("external_id") or "") in event_by_external_run
        ]
        if len(listed_runs) != len(events):
            raise ExperimentSourceRuntimeUnavailableV2("source_run_parity_mismatch")

        formal_runs: list[dict[str, Any]] = []
        for listed in listed_runs:
            run = self.service.store.get_run(str(listed.get("id") or ""))
            if run is None or run.get("project_id") != project_id:
                raise ExperimentSourceRuntimeUnavailableV2("source_run_unavailable")
            event = event_by_external_run[str(run.get("external_id") or "")]
            observations = self.service.store.list_run_observations(str(run["id"]))
            if not observations or observations[-1].get("source_version") != event.get(
                "source_version"
            ):
                raise ExperimentSourceRuntimeUnavailableV2("source_version_mismatch")
            formal_runs.append(run)
        formal_runs.sort(key=lambda item: str(item["id"]))

        pipeline_source_id = _pipeline_identity("experimentsource", source_id)
        experiment_ids = {
            str(run["experiment_id"]): _pipeline_identity(
                "experiment",
                str(run["experiment_id"]),
            )
            for run in formal_runs
        }
        run_ids = {str(run["id"]): _pipeline_identity("run", str(run["id"])) for run in formal_runs}
        identities = tuple(
            ExperimentRuntimeIdentityV2(
                formal_experiment_id=str(run["experiment_id"]),
                pipeline_experiment_id=experiment_ids[str(run["experiment_id"])],
                formal_run_id=str(run["id"]),
                pipeline_run_id=run_ids[str(run["id"])],
            )
            for run in formal_runs
        )
        adapted_runs = tuple(
            self._adapt_run(
                run,
                pipeline_run_id=run_ids[str(run["id"])],
                pipeline_experiment_id=experiment_ids[str(run["experiment_id"])],
            )
            for run in formal_runs
        )
        metadata: dict[str, dict[str, Any]] = {}
        for formal_experiment_id, pipeline_experiment_id in experiment_ids.items():
            experiment = self.service.store.get_experiment(formal_experiment_id)
            if experiment is None or experiment.get("project_id") != project_id:
                raise ExperimentSourceRuntimeUnavailableV2("experiment_metadata_unavailable")
            metadata[pipeline_experiment_id] = {
                "title": str(experiment.get("title") or pipeline_experiment_id),
                "objective": str(experiment.get("objective") or ""),
                "hypothesis": str(experiment.get("hypothesis") or ""),
                "tags": tuple(str(item) for item in experiment.get("tags") or ()),
                "run_names": {
                    run_ids[str(run["id"])]: str(run.get("name") or run_ids[str(run["id"])])
                    for run in formal_runs
                    if run.get("experiment_id") == formal_experiment_id
                },
            }

        source_generation = canonical_sha256_v2(
            {
                "source_id": source_id,
                "project_id": project_id,
                "status": source.get("status"),
                "updated_at": source.get("updated_at"),
                "stats": source.get("stats") or {},
            }
        )
        watermark = canonical_sha256_v2(
            {
                "source_generation": source_generation,
                "events": [
                    {
                        "object_id": item.get("source_object_id"),
                        "source_version": item.get("source_version"),
                        "content_hash": item.get("content_hash"),
                        "raw_object_id": item.get("raw_object_id"),
                    }
                    for item in events
                ],
                "runs": [
                    {
                        "id": run.get("id"),
                        "updated_at": run.get("updated_at"),
                        "observation": self.service.store.list_run_observations(str(run["id"]))[-1][
                            "source_version"
                        ],
                    }
                    for run in formal_runs
                ],
            }
        )
        if (
            expected_source_generation is not None
            and expected_source_generation != source_generation
        ):
            raise ExperimentSourceRuntimeUnavailableV2("expected_generation_mismatch")
        if expected_watermark is not None and expected_watermark != watermark:
            raise ExperimentSourceRuntimeUnavailableV2("expected_watermark_mismatch")
        observed_at = max(str(item.get("observed_at") or "") for item in events)
        authority = ExperimentSourceAuthorityV2(
            project_id=project_id,
            formal_source_id=source_id,
            pipeline_source_id=pipeline_source_id,
            acl_ref=acl_ref,
            source_generation=source_generation,
            watermark=watermark,
            observed_at=observed_at,
            run_count=len(adapted_runs),
        )
        return _LoadedExperimentSource(
            authority=authority,
            identities=identities,
            runs=adapted_runs,
            experiment_metadata=metadata,
        )

    @staticmethod
    def _adapt_run(
        run: Mapping[str, Any],
        *,
        pipeline_run_id: str,
        pipeline_experiment_id: str,
    ) -> dict[str, Any]:
        metrics = tuple(
            {
                "name": item["name"],
                "value": item["value"],
                "unit": item.get("unit"),
                "split": item.get("split"),
                "step": item.get("step"),
                "observed_at": item.get("created_at"),
            }
            for item in tuple(run.get("metrics") or ())
        )
        artifacts = tuple(
            {
                "name": item["name"],
                "uri": "artifact-ref:"
                + hashlib.sha256(str(item.get("uri") or "").encode("utf-8")).hexdigest(),
                "kind": item.get("kind") or "artifact",
                "checksum": item.get("checksum"),
                "authorized": False,
            }
            for item in tuple(run.get("artifacts") or ())
        )
        return {
            "id": pipeline_run_id,
            "experiment_id": pipeline_experiment_id,
            "status": run.get("status"),
            "repository_id": run.get("repository_id"),
            "commit_sha": run.get("commit_sha"),
            "branch": run.get("branch"),
            "dataset_id": run.get("dataset_id"),
            "dataset_version": run.get("dataset_version"),
            "config": dict(run.get("config") or {}),
            "environment": dict(run.get("environment") or {}),
            "command": run.get("command") or "",
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "metrics": metrics,
            "artifacts": artifacts,
        }

    def _translate_query(
        self,
        request: Mapping[str, Any],
        loaded: _LoadedExperimentSource,
    ) -> dict[str, Any]:
        translated = copy.deepcopy(dict(request))
        experiment_ids = {
            item.formal_experiment_id: item.pipeline_experiment_id for item in loaded.identities
        }
        run_ids = {item.formal_run_id: item.pipeline_run_id for item in loaded.identities}

        def translate_values(key: str, values: object) -> list[str]:
            mapping = experiment_ids if key == "experiment_ids" else run_ids
            source = tuple(values or ()) if isinstance(values, (list, tuple)) else ()
            if any(str(item) not in mapping for item in source):
                raise ExperimentSourceRuntimeUnavailableV2("query_identity_outside_source")
            return [mapping[str(item)] for item in source]

        if "experiment_ids" in translated:
            translated["experiment_ids"] = translate_values(
                "experiment_ids",
                translated["experiment_ids"],
            )
        if "run_ids" in translated:
            translated["run_ids"] = translate_values("run_ids", translated["run_ids"])
        fields = []
        for raw in tuple(translated.get("fields") or ()):
            field = dict(raw)
            name = str(field.get("field") or "")
            mapping = (
                experiment_ids
                if name == "experiment_id"
                else run_ids
                if name == "run_id"
                else {loaded.authority.formal_source_id: loaded.authority.pipeline_source_id}
                if name == "source_id"
                else None
            )
            if mapping is not None:
                value = field.get("value")
                if isinstance(value, (list, tuple)):
                    if any(str(item) not in mapping for item in value):
                        raise ExperimentSourceRuntimeUnavailableV2("query_identity_outside_source")
                    field["value"] = tuple(mapping[str(item)] for item in value)
                elif str(value) in mapping:
                    field["value"] = mapping[str(value)]
                else:
                    raise ExperimentSourceRuntimeUnavailableV2("query_identity_outside_source")
            fields.append(field)
        if fields:
            translated["fields"] = fields
        return translated

    def _ensure_generation(self, loaded: _LoadedExperimentSource) -> None:
        build = ExperimentBuildInputV2(
            project_id=loaded.authority.project_id,
            source_id=loaded.authority.pipeline_source_id,
            acl_ref=loaded.authority.acl_ref,
            watermark=loaded.authority.watermark,
            observed_at=loaded.authority.observed_at,
            runs=loaded.runs,
            experiment_metadata=loaded.experiment_metadata,
            registry=self.metric_registry,
            group_names=("runtime",),
        )
        expected_generation_id = self._expected_generation_id(loaded)
        active = self.v2_store.active_snapshots(
            project_id=loaded.authority.project_id,
            source_id=loaded.authority.pipeline_source_id,
            acl_ref=loaded.authority.acl_ref,
        )
        if active and {item.generation_id for item in active} == {expected_generation_id}:
            expected = tuple(
                sorted(
                    (
                        build_experiment_run_snapshot_v2(
                            run,
                            project_id=loaded.authority.project_id,
                            source_id=loaded.authority.pipeline_source_id,
                            generation_id=expected_generation_id,
                            acl_ref=loaded.authority.acl_ref,
                            registry=self.metric_registry,
                            observed_at=loaded.authority.observed_at,
                        )
                        for run in loaded.runs
                    ),
                    key=lambda item: item.run_snapshot_id,
                )
            )
            if tuple(item.content_sha256 for item in active) != tuple(
                item.content_sha256 for item in expected
            ):
                raise ExperimentSourceRuntimeUnavailableV2("active_generation_content_drift")
            return
        prepared = self.pipeline.prepare(build)
        if prepared.generation.generation_id != expected_generation_id:
            raise ExperimentSourceRuntimeUnavailableV2("generation_identity_mismatch")
        self.pipeline.publish(prepared)

    @staticmethod
    def _expected_generation_id(loaded: _LoadedExperimentSource) -> str:
        seed = {
            "project_id": loaded.authority.project_id,
            "source_id": loaded.authority.pipeline_source_id,
            "acl_ref": loaded.authority.acl_ref,
            "watermark": loaded.authority.watermark,
            "expected_snapshots": len(loaded.runs),
            "schema_version": EXPERIMENT_STORE_SCHEMA_VERSION,
        }
        return "experimentgeneration-" + hashlib.sha256(canonical_json_bytes_v2(seed)).hexdigest()

    def _selected_snapshots(
        self,
        query: ExperimentQueryV2,
        result: ExperimentQueryResultV2,
    ) -> tuple[ExperimentRunSnapshotV2, ...]:
        active = self.v2_store.active_snapshots(
            project_id=query.project_id,
            source_id=query.source_id,
            acl_ref=query.acl_ref,
        )
        by_id = {item.run_snapshot_id: item for item in active}
        selected_ids = tuple(
            dict.fromkeys(item.run_snapshot_id for item in result.semantic.candidates)
        )
        if not selected_ids:
            selected_ids = result.structured_snapshot_ids
        selected = tuple(by_id[item] for item in selected_ids if item in by_id)
        if not selected:
            raise ExperimentSourceRuntimeUnavailableV2("no_governed_evidence")
        return selected

    def _scope_result(
        self,
        query: ExperimentQueryV2,
        result: ExperimentQueryResultV2,
    ) -> ExperimentQueryResultV2:
        active = self.v2_store.active_snapshots(
            project_id=query.project_id,
            source_id=query.source_id,
            acl_ref=query.acl_ref,
        )
        governed = filter_experiment_snapshots_v2(query, active)
        governed_ids = {item.run_snapshot_id for item in governed}
        if not governed_ids:
            raise ExperimentSourceRuntimeUnavailableV2("no_governed_evidence")
        candidates = tuple(
            item.model_copy(update={"rank": rank})
            for rank, item in enumerate(
                (
                    candidate
                    for candidate in result.semantic.candidates
                    if candidate.run_snapshot_id in governed_ids
                ),
                start=1,
            )
        )
        if not candidates:
            raise ExperimentSourceRuntimeUnavailableV2("no_governed_evidence")
        semantic_payload = {
            "query_sha256": result.semantic.query_sha256,
            "candidates": candidates,
            "pinned_count": sum(item.pinned_structured for item in candidates),
            "semantic_tail_count": sum(not item.pinned_structured for item in candidates),
            "embedding_profile": result.semantic.embedding_profile,
            "fallback_reason": result.semantic.fallback_reason,
            "profile_version": result.semantic.profile_version,
            "reranker_version": result.semantic.reranker_version,
        }
        semantic = ExperimentSemanticResultV2(
            **semantic_payload,
            content_sha256=canonical_sha256_v2(
                {
                    **semantic_payload,
                    "candidates": [item.model_dump(mode="json") for item in candidates],
                }
            ),
        )
        return ExperimentQueryResultV2(
            query=result.query,
            compiled=result.compiled,
            structured_snapshot_ids=tuple(
                item for item in result.structured_snapshot_ids if item in governed_ids
            ),
            semantic=semantic,
            context=result.context,
            content_sha256=canonical_sha256_v2(
                {
                    "query": result.query.model_dump(mode="json"),
                    "compiled_plan_sha256": result.compiled.plan_sha256,
                    "structured_snapshot_ids": tuple(
                        item for item in result.structured_snapshot_ids if item in governed_ids
                    ),
                    "semantic_sha256": semantic.content_sha256,
                    "context_sha256": result.context.content_sha256,
                    "pipeline_version": EXPERIMENT_PIPELINE_VERSION,
                }
            ),
        )

    def _analyze(
        self,
        query: ExperimentQueryV2,
        snapshots: tuple[ExperimentRunSnapshotV2, ...],
        spec: ExperimentAnalysisSpecV2,
        loaded: _LoadedExperimentSource,
    ) -> ExperimentRuntimeAnalysisV2:
        warnings: list[str] = []
        if spec.fail_closed:
            self._validate_analysis_spec(query, spec)
            formal_to_pipeline = {
                item.formal_run_id: item.pipeline_run_id for item in loaded.identities
            }
            requested_formal_ids = (
                (spec.baseline_run_id, *spec.candidate_run_ids)
                if query.task is ExperimentQueryTaskV2.COMPARE
                else spec.run_ids
            )
            if any(item is None or item not in formal_to_pipeline for item in requested_formal_ids):
                raise ExperimentSourceRuntimeUnavailableV2("analysis_run_outside_authorized_source")
            selected_pipeline_ids = {item.run_id for item in snapshots}
            if {
                formal_to_pipeline[str(item)] for item in requested_formal_ids
            } != selected_pipeline_ids:
                raise ExperimentSourceRuntimeUnavailableV2("analysis_run_selection_mismatch")
        definition = self._metric_definition(snapshots, spec.metric_name)
        if spec.fail_closed and spec.metric_name is not None and definition is None:
            matches = self._metric_definition_matches(snapshots, spec.metric_name)
            raise ExperimentSourceRuntimeUnavailableV2(
                "metric_definition_ambiguous" if len(matches) > 1 else "metric_definition_not_found"
            )
        if (
            query.task
            in {
                ExperimentQueryTaskV2.BEST,
                ExperimentQueryTaskV2.COMPARE,
                ExperimentQueryTaskV2.TREND,
                ExperimentQueryTaskV2.AGGREGATE,
            }
            and definition is None
        ):
            warnings.append("metric_definition_required")

        numeric: list[ExperimentNumericResultV2] = []
        comparability: list[ExperimentComparabilityResultV2] = []
        comparisons: list[ExperimentComparisonV2] = []
        aggregations: list[ExperimentAggregationResultV2] = []
        reproduction: list[ExperimentReproductionResultV2] = []
        if definition is not None and query.task in {
            ExperimentQueryTaskV2.BEST,
            ExperimentQueryTaskV2.TREND,
        }:
            observations = tuple(
                item
                for snapshot in snapshots
                for item in snapshot.observations
                if item.definition_id == definition.definition_id
            )
            numeric.append(
                evaluate_experiment_numeric_v2(
                    definition,
                    observations,
                    operator=(
                        ExperimentAggregationV2.BEST
                        if query.task is ExperimentQueryTaskV2.BEST
                        else ExperimentAggregationV2.LATEST
                    ),
                )
            )
        elif definition is not None and query.task is ExperimentQueryTaskV2.COMPARE:
            formal_to_pipeline = {
                item.formal_run_id: item.pipeline_run_id for item in loaded.identities
            }
            baseline_id = (
                formal_to_pipeline.get(spec.baseline_run_id)
                if spec.baseline_run_id is not None
                else None
            )
            baseline = next(
                (item for item in snapshots if item.run_id == baseline_id),
                None,
            )
            if baseline is None:
                if spec.fail_closed:
                    raise ExperimentSourceRuntimeUnavailableV2("baseline_run_not_selected")
                warnings.append("baseline_run_required")
            else:
                selected_candidate_ids = {
                    formal_to_pipeline[item]
                    for item in spec.candidate_run_ids
                    if item in formal_to_pipeline
                }
                if spec.fail_closed and len(selected_candidate_ids) != len(spec.candidate_run_ids):
                    raise ExperimentSourceRuntimeUnavailableV2(
                        "candidate_run_outside_authorized_source"
                    )
                candidates = tuple(
                    candidate
                    for candidate in snapshots
                    if (not selected_candidate_ids or candidate.run_id in selected_candidate_ids)
                )
                if spec.fail_closed and len(candidates) != len(selected_candidate_ids):
                    raise ExperimentSourceRuntimeUnavailableV2("candidate_run_not_selected")
                for candidate in candidates:
                    if candidate.run_snapshot_id == baseline.run_snapshot_id:
                        continue
                    comparable = compare_experiment_snapshots_v2(
                        baseline,
                        candidate,
                        metric_definition_id=definition.definition_id,
                        treatment_config_keys=spec.treatment_config_keys,
                        controlled_config_keys=spec.controlled_config_keys,
                    )
                    comparability.append(comparable)
                    comparisons.append(
                        compare_metric_v2(
                            comparable,
                            self._latest_observation(baseline, definition, spec.split),
                            self._latest_observation(candidate, definition, spec.split),
                            direction=definition.direction,
                        )
                    )
        elif definition is not None and query.task is ExperimentQueryTaskV2.AGGREGATE:
            by_experiment: dict[str, list[ExperimentRunSnapshotV2]] = {}
            for snapshot in snapshots:
                by_experiment.setdefault(snapshot.experiment_id, []).append(snapshot)
            for experiment_id, members in sorted(by_experiment.items()):
                group = build_experiment_run_group_v2(
                    f"runtime:{experiment_id}",
                    tuple(members),
                )
                aggregations.append(
                    aggregate_experiment_run_group_v2(
                        group,
                        tuple(members),
                        metric_definition_id=definition.definition_id,
                        function=spec.aggregation or "mean",
                        split=spec.split,
                    )
                )
        if query.task is ExperimentQueryTaskV2.REPRODUCE:
            reproduction.extend(evaluate_reproduction_v2(item) for item in snapshots)
        return ExperimentRuntimeAnalysisV2(
            numeric=tuple(numeric),
            comparability=tuple(comparability),
            comparisons=tuple(comparisons),
            aggregations=tuple(aggregations),
            reproduction=tuple(reproduction),
            requested_reproduction_roles=spec.reproduction_roles,
            warnings=tuple(sorted(set(warnings))),
        )

    @staticmethod
    def _validate_analysis_spec(
        query: ExperimentQueryV2,
        spec: ExperimentAnalysisSpecV2,
    ) -> None:
        if query.task is ExperimentQueryTaskV2.COMPARE:
            if (
                spec.metric_name is None
                or spec.baseline_run_id is None
                or spec.split is None
                or not spec.candidate_run_ids
            ):
                raise ExperimentSourceRuntimeUnavailableV2("compare_spec_incomplete")
        elif query.task is ExperimentQueryTaskV2.AGGREGATE:
            if (
                spec.metric_name is None
                or spec.split is None
                or spec.aggregation is None
                or not spec.run_ids
            ):
                raise ExperimentSourceRuntimeUnavailableV2("aggregate_spec_incomplete")
        elif query.task is ExperimentQueryTaskV2.REPRODUCE:
            if not spec.run_ids or not spec.reproduction_roles:
                raise ExperimentSourceRuntimeUnavailableV2("reproduce_spec_incomplete")
        else:
            raise ExperimentSourceRuntimeUnavailableV2("analysis_task_not_supported")

    @staticmethod
    def _metric_definition_matches(
        snapshots: tuple[ExperimentRunSnapshotV2, ...],
        metric_name: str,
    ) -> tuple[ExperimentMetricDefinitionV2, ...]:
        lowered = metric_name.casefold()
        definitions = {
            item.definition_id: item for snapshot in snapshots for item in snapshot.definitions
        }
        return tuple(
            sorted(
                (
                    item
                    for item in definitions.values()
                    if item.canonical_name.casefold() == lowered
                    or lowered in {alias.casefold() for alias in item.aliases}
                ),
                key=lambda item: item.definition_id,
            )
        )

    @staticmethod
    def _metric_definition(
        snapshots: tuple[ExperimentRunSnapshotV2, ...],
        metric_name: str | None,
    ) -> ExperimentMetricDefinitionV2 | None:
        definitions = {
            item.definition_id: item for snapshot in snapshots for item in snapshot.definitions
        }
        if metric_name is not None:
            matches = ExperimentSourceRuntimeV2._metric_definition_matches(
                snapshots,
                metric_name,
            )
            return matches[0] if len(matches) == 1 else None
        canonical_names = {item.canonical_name for item in definitions.values()}
        return next(iter(definitions.values())) if len(canonical_names) == 1 else None

    @staticmethod
    def _latest_observation(
        snapshot: ExperimentRunSnapshotV2,
        definition: ExperimentMetricDefinitionV2,
        split: str | None,
    ) -> ExperimentMetricObservationV2 | None:
        matches = sorted(
            (
                item
                for item in snapshot.observations
                if item.definition_id == definition.definition_id
                and item.valid
                and (split is None or item.split == split)
            ),
            key=lambda item: (
                item.step is None,
                item.step if item.step is not None else -1,
                item.observed_at or "",
                item.observation_id,
            ),
        )
        return matches[-1] if matches else None

    @staticmethod
    def _replace_context(
        result: ExperimentQueryResultV2,
        context: ExperimentContextV2,
    ) -> ExperimentQueryResultV2:
        payload = {
            "query": result.query.model_dump(mode="json"),
            "compiled_plan_sha256": result.compiled.plan_sha256,
            "structured_snapshot_ids": result.structured_snapshot_ids,
            "semantic_sha256": result.semantic.content_sha256,
            "context_sha256": context.content_sha256,
            "pipeline_version": EXPERIMENT_PIPELINE_VERSION,
        }
        return ExperimentQueryResultV2(
            query=result.query,
            compiled=result.compiled,
            structured_snapshot_ids=result.structured_snapshot_ids,
            semantic=result.semantic,
            context=context,
            content_sha256=canonical_sha256_v2(payload),
        )


__all__ = [
    "EXPERIMENT_SOURCE_RUNTIME_VERSION",
    "ExperimentAnalysisSpecV2",
    "ExperimentProductionServiceV2",
    "ExperimentRuntimeAnalysisV2",
    "ExperimentRuntimeIdentityV2",
    "ExperimentSourceAuthorityV2",
    "ExperimentSourceRuntimeErrorV2",
    "ExperimentSourceRuntimeResultV2",
    "ExperimentSourceRuntimeUnavailableV2",
    "ExperimentSourceRuntimeV2",
]
