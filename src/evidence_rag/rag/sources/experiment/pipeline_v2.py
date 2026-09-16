"""End-to-end isolated Experiment V2 ingestion and query pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts_v2 import (
    ExperimentMetricRegistryEntryV2,
    ExperimentRunGroupV2,
    ExperimentRunSnapshotV2,
    build_experiment_run_group_v2,
    build_experiment_run_snapshot_v2,
    canonical_sha256_v2,
)
from .query_v2 import (
    CompiledExperimentQueryV2,
    ExperimentQueryV2,
    compile_experiment_query_v2,
    filter_experiment_snapshots_v2,
)
from .semantic_v2 import (
    ExperimentContextV2,
    ExperimentSemanticResultV2,
    ExperimentSurfaceUnitV2,
    build_experiment_context_v2,
    build_experiment_surfaces_v2,
    retrieve_experiment_surfaces_v2,
    surface_records_v2,
)
from .store_v2 import (
    ExperimentGenerationV2,
    ExperimentPublishReceiptV2,
    ExperimentStoreV2,
)

EXPERIMENT_PIPELINE_VERSION = "experiment-source-pipeline-v2"


@dataclass(frozen=True, slots=True)
class ExperimentBuildInputV2:
    project_id: str
    source_id: str
    acl_ref: str
    watermark: str
    observed_at: str
    runs: tuple[Mapping[str, Any], ...]
    experiment_metadata: Mapping[str, Mapping[str, Any]]
    registry: Mapping[str, ExperimentMetricRegistryEntryV2]
    group_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExperimentPreparedGenerationV2:
    generation: ExperimentGenerationV2
    snapshots: tuple[ExperimentRunSnapshotV2, ...]
    groups: tuple[ExperimentRunGroupV2, ...]
    surfaces: tuple[ExperimentSurfaceUnitV2, ...]
    parity_sha256: str
    pipeline_version: str = EXPERIMENT_PIPELINE_VERSION


@dataclass(frozen=True, slots=True)
class ExperimentQueryResultV2:
    query: ExperimentQueryV2
    compiled: CompiledExperimentQueryV2
    structured_snapshot_ids: tuple[str, ...]
    semantic: ExperimentSemanticResultV2
    context: ExperimentContextV2
    content_sha256: str
    pipeline_version: str = EXPERIMENT_PIPELINE_VERSION


class ExperimentSourcePipelineV2:
    """Coordinate exact snapshots, additive publication, retrieval, and context."""

    def __init__(self, store: ExperimentStoreV2) -> None:
        self.store = store

    def prepare(self, source: ExperimentBuildInputV2) -> ExperimentPreparedGenerationV2:
        if not source.runs:
            raise ValueError("empty Experiment generation cannot be published")
        run_ids = tuple(str(item.get("id")) for item in source.runs)
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("source generation contains duplicate run IDs")
        generation = self.store.prepare_generation(
            project_id=source.project_id,
            source_id=source.source_id,
            acl_ref=source.acl_ref,
            watermark=source.watermark,
            expected_snapshots=len(source.runs),
        )
        snapshots = tuple(
            sorted(
                (
                    build_experiment_run_snapshot_v2(
                        item,
                        project_id=source.project_id,
                        source_id=source.source_id,
                        generation_id=generation.generation_id,
                        acl_ref=source.acl_ref,
                        registry=source.registry,
                        observed_at=source.observed_at,
                    )
                    for item in source.runs
                ),
                key=lambda item: item.run_snapshot_id,
            )
        )
        by_experiment: dict[str, list[ExperimentRunSnapshotV2]] = {}
        for snapshot in snapshots:
            by_experiment.setdefault(snapshot.experiment_id, []).append(snapshot)
        groups: list[ExperimentRunGroupV2] = []
        for name in source.group_names:
            for experiment_id, members in sorted(by_experiment.items()):
                groups.append(
                    build_experiment_run_group_v2(
                        f"{name}:{experiment_id}",
                        tuple(members),
                    )
                )
        surfaces: list[ExperimentSurfaceUnitV2] = []
        for snapshot in snapshots:
            metadata = dict(source.experiment_metadata.get(snapshot.experiment_id) or {})
            surfaces.extend(
                build_experiment_surfaces_v2(
                    snapshot,
                    experiment_title=str(metadata.get("title") or snapshot.experiment_id),
                    objective=(
                        str(metadata["objective"])
                        if metadata.get("objective") is not None
                        else None
                    ),
                    hypothesis=(
                        str(metadata["hypothesis"])
                        if metadata.get("hypothesis") is not None
                        else None
                    ),
                    run_name=(
                        str(metadata["run_names"].get(snapshot.run_id))
                        if isinstance(metadata.get("run_names"), Mapping)
                        and snapshot.run_id in metadata["run_names"]
                        else None
                    ),
                    tags=tuple(str(item) for item in metadata.get("tags") or ()),
                )
            )
        parity = canonical_sha256_v2(
            {
                "source_run_ids": sorted(run_ids),
                "snapshot_run_ids": sorted(item.run_id for item in snapshots),
                "source_count": len(source.runs),
                "snapshot_count": len(snapshots),
                "pipeline_version": EXPERIMENT_PIPELINE_VERSION,
            }
        )
        if sorted(run_ids) != sorted(item.run_id for item in snapshots):
            self.store.fail_generation(generation.generation_id, "run_id_parity_mismatch")
            raise ValueError("source/snapshot run parity mismatch")
        return ExperimentPreparedGenerationV2(
            generation=generation,
            snapshots=snapshots,
            groups=tuple(sorted(groups, key=lambda item: item.run_group_id)),
            surfaces=tuple(sorted(surfaces, key=lambda item: item.unit_id)),
            parity_sha256=parity,
        )

    def publish(
        self,
        prepared: ExperimentPreparedGenerationV2,
    ) -> ExperimentPublishReceiptV2:
        return self.store.publish(
            prepared.generation,
            prepared.snapshots,
            groups=prepared.groups,
            retrieval_units=surface_records_v2(prepared.surfaces),
        )

    def query(
        self,
        query: ExperimentQueryV2,
        *,
        semantic_query: str,
        budget_tokens: int = 1_500,
        dense_profile: str | None = None,
    ) -> ExperimentQueryResultV2:
        snapshots = self.store.active_snapshots(
            project_id=query.project_id,
            source_id=query.source_id,
            acl_ref=query.acl_ref,
        )
        raw_units = self.store.active_units(
            project_id=query.project_id,
            source_id=query.source_id,
            acl_ref=query.acl_ref,
        )
        units = tuple(ExperimentSurfaceUnitV2.model_validate(item["payload"]) for item in raw_units)
        structured = filter_experiment_snapshots_v2(query, snapshots)
        structured_ids = tuple(item.run_snapshot_id for item in structured)
        semantic = retrieve_experiment_surfaces_v2(
            query,
            snapshots,
            units,
            semantic_query=semantic_query,
            structured_snapshot_ids=structured_ids,
            dense_profile=dense_profile,
        )
        selected_ids = tuple(dict.fromkeys(item.run_snapshot_id for item in semantic.candidates))
        selected = tuple(
            snapshot
            for identity in selected_ids
            for snapshot in snapshots
            if snapshot.run_snapshot_id == identity
        )
        if not selected:
            selected = structured
        if not selected:
            raise ValueError("no governed Experiment evidence is available")
        context = build_experiment_context_v2(
            query,
            selected,
            budget_tokens=budget_tokens,
        )
        compiled = compile_experiment_query_v2(query)
        payload = {
            "query": query.model_dump(mode="json"),
            "compiled_plan_sha256": compiled.plan_sha256,
            "structured_snapshot_ids": structured_ids,
            "semantic_sha256": semantic.content_sha256,
            "context_sha256": context.content_sha256,
            "pipeline_version": EXPERIMENT_PIPELINE_VERSION,
        }
        return ExperimentQueryResultV2(
            query=query,
            compiled=compiled,
            structured_snapshot_ids=structured_ids,
            semantic=semantic,
            context=context,
            content_sha256=canonical_sha256_v2(payload),
        )


__all__ = [
    "EXPERIMENT_PIPELINE_VERSION",
    "ExperimentBuildInputV2",
    "ExperimentPreparedGenerationV2",
    "ExperimentQueryResultV2",
    "ExperimentSourcePipelineV2",
]
