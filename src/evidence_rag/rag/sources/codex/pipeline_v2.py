"""Source-authenticated orchestration for the isolated Codex X2-X5 pipeline."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ....models import CodexItemRecord, CodexTurnRecord
from .context_v2 import (
    CODEX_CONTEXT_BUILDER_VERSION,
    CodexTaskContextV2,
    build_codex_task_context_v2,
)
from .contracts import (
    CODEX_EVENT_NORMALIZER_VERSION,
    CodexEventNormalizationResult,
    canonical_sha256,
)
from .episode_v2 import (
    CODEX_EPISODE_BUILDER_VERSION,
    CodexEpisodeBuildResult,
    build_codex_episodes_v2,
)
from .event_normalizer import normalize_codex_events_v1
from .observable_adapter_v2 import (
    CODEX_OBSERVABLE_ADAPTER_VERSION,
    CodexObservableThreadV2,
    adapt_production_codex_items_v2,
)
from .retrieval_v2 import (
    CODEX_RERANKER_VERSION,
    CODEX_RETRIEVER_VERSION,
    CodexCalibrationArtifactV2,
    CodexCandidateChannel,
    CodexDenseCacheEntryV2,
    CodexDenseProfileV2,
    CodexQueryTask,
    CodexRetrievalResultV2,
    LocalCodexDenseCacheV2,
    build_codex_dense_profile_v2,
    dense_purpose_for_task_v2,
    retrieve_codex_v2,
)
from .store_v2 import CODEX_V2_SCHEMA_VERSION, CodexV2Store
from .units_v2 import (
    CODEX_RETRIEVAL_UNIT_BUILDER_VERSION,
    CODEX_TEMPORAL_GRAPH_VERSION,
    CodexRetrievalPublicationV2,
    build_codex_retrieval_publication_v2,
)

CODEX_PIPELINE_VERSION = "codex-source-pipeline-v2"
CODEX_PIPELINE_COMPONENT_SET_SHA256 = canonical_sha256(
    {
        "adapter": CODEX_OBSERVABLE_ADAPTER_VERSION,
        "normalizer": CODEX_EVENT_NORMALIZER_VERSION,
        "episode_builder": CODEX_EPISODE_BUILDER_VERSION,
        "unit_builder": CODEX_RETRIEVAL_UNIT_BUILDER_VERSION,
        "graph": CODEX_TEMPORAL_GRAPH_VERSION,
        "store": CODEX_V2_SCHEMA_VERSION,
        "retriever": CODEX_RETRIEVER_VERSION,
        "reranker": CODEX_RERANKER_VERSION,
        "context_builder": CODEX_CONTEXT_BUILDER_VERSION,
        "pipeline": CODEX_PIPELINE_VERSION,
    }
)


class _FrozenPipeline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CodexThreadPipelineBundleV2(_FrozenPipeline):
    adapter_result_sha256: str
    observable_thread: CodexObservableThreadV2
    normalization: CodexEventNormalizationResult
    episodes: CodexEpisodeBuildResult
    publication: CodexRetrievalPublicationV2
    component_set_sha256: str = CODEX_PIPELINE_COMPONENT_SET_SHA256
    bundle_sha256: str
    pipeline_version: str = CODEX_PIPELINE_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexThreadPipelineBundleV2:
        if self.component_set_sha256 != CODEX_PIPELINE_COMPONENT_SET_SHA256:
            raise ValueError("Codex pipeline component identity mismatch")
        if (
            self.episodes.normalization_sha256 != self.normalization.canonical_sha256()
            or self.publication.normalization_sha256 != self.normalization.canonical_sha256()
            or self.publication.episode_result_sha256 != self.episodes.result_sha256
            or self.publication.thread_id != self.observable_thread.thread_id
        ):
            raise ValueError("Codex pipeline source chain is not exact")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"bundle_sha256"}))
        if self.bundle_sha256 != expected:
            raise ValueError("Codex pipeline bundle identity mismatch")
        return self


class CodexPipelinePublicationReceiptV2(_FrozenPipeline):
    bundle_sha256: str
    publication_sha256: str
    operation: str
    publication_id: str
    generation_id: str
    episode_count: int = Field(ge=0)
    unit_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    receipt_sha256: str
    schema_version: str = CODEX_V2_SCHEMA_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexPipelinePublicationReceiptV2:
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"receipt_sha256"}))
        if self.receipt_sha256 != expected:
            raise ValueError("Codex publication receipt identity mismatch")
        return self


class CodexPipelineQueryResultV2(_FrozenPipeline):
    bundle_sha256: str
    retrieval: CodexRetrievalResultV2
    context: CodexTaskContextV2
    persisted_dense_entries: int = Field(ge=0)
    result_sha256: str
    pipeline_version: str = CODEX_PIPELINE_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexPipelineQueryResultV2:
        if (
            self.context.retrieval_result_sha256 != self.retrieval.result_sha256
            or self.context.publication_sha256 != self.retrieval.publication_sha256
        ):
            raise ValueError("Codex query context does not bind its retrieval result")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"result_sha256"}))
        if self.result_sha256 != expected:
            raise ValueError("Codex pipeline query identity mismatch")
        return self


class _StoreBackedCodexDenseCacheV2(LocalCodexDenseCacheV2):
    """Read through the isolated store while retaining deterministic local behavior."""

    def __init__(
        self,
        store: CodexV2Store,
        profile: CodexDenseProfileV2,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
        acl_ref: str,
    ) -> None:
        super().__init__(
            profile,
            project_id=project_id,
            source_id=source_id,
            generation_id=generation_id,
            acl_ref=acl_ref,
        )
        self._store = store
        self._loaded_cache_ids: set[str] = set()

    def encode(self, content: str, *, unit_id: str) -> CodexDenseCacheEntryV2:
        content_sha256 = canonical_sha256(content)
        key = (self.profile.profile_sha256, content_sha256, unit_id)
        existing = self._entries.get(key)
        if existing is not None:
            return existing
        persisted = self._store.get_dense_cache(
            project_id=self.project_id,
            source_id=self.source_id,
            generation_id=self.generation_id,
            acl_ref=self.acl_ref,
            unit_id=unit_id,
            profile_sha256=self.profile.profile_sha256,
            content_sha256=content_sha256,
        )
        if persisted is not None:
            if len(persisted.vector) != self.profile.dimensions:
                raise ValueError("persisted dense cache dimensions do not match profile")
            self._entries[key] = persisted
            self._loaded_cache_ids.add(persisted.cache_id)
            return persisted
        return super().encode(content, unit_id=unit_id)

    @property
    def new_entries(self) -> tuple[CodexDenseCacheEntryV2, ...]:
        return tuple(item for item in self.entries if item.cache_id not in self._loaded_cache_ids)


def build_codex_thread_pipeline_v2(
    records: Iterable[CodexItemRecord],
    *,
    project_id: str,
    thread_id: str,
    turns: Iterable[CodexTurnRecord] = (),
    manual_overrides: Mapping[str, str] | None = None,
    gap_seconds: int = 1_800,
) -> CodexThreadPipelineBundleV2:
    """Build one exact production thread without evaluation labels or storage I/O."""

    adapter = adapt_production_codex_items_v2(
        records,
        project_id=project_id,
        turns=turns,
    )
    matches = tuple(item for item in adapter.thread_sets if item.thread_id == thread_id)
    if len(matches) != 1:
        raise ValueError("Codex pipeline requires one exact observable thread")
    observable_thread = matches[0]
    normalization = normalize_codex_events_v1(observable_thread.observable_items)
    episodes = build_codex_episodes_v2(
        observable_thread.observable_items,
        normalization,
        manual_overrides=manual_overrides,
        gap_seconds=gap_seconds,
    )
    publication = build_codex_retrieval_publication_v2(
        observable_thread.observable_items,
        normalization,
        episodes,
        manual_overrides=manual_overrides,
        gap_seconds=gap_seconds,
    )
    values = {
        "adapter_result_sha256": adapter.result_sha256,
        "observable_thread": observable_thread,
        "normalization": normalization,
        "episodes": episodes,
        "publication": publication,
        "component_set_sha256": CODEX_PIPELINE_COMPONENT_SET_SHA256,
        "pipeline_version": CODEX_PIPELINE_VERSION,
    }
    return CodexThreadPipelineBundleV2(
        **values,
        bundle_sha256=canonical_sha256(
            {
                key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
                for key, value in values.items()
            }
        ),
    )


def publish_codex_thread_pipeline_v2(
    store: CodexV2Store,
    bundle: CodexThreadPipelineBundleV2,
    *,
    authority_watermark: str | None = None,
    source_snapshot_sha256: str | None = None,
) -> CodexPipelinePublicationReceiptV2:
    exact = CodexThreadPipelineBundleV2.model_validate(
        bundle.model_dump(mode="python", round_trip=True)
    )
    summary = store.publish_pipeline_bundle(
        exact,
        authority_watermark=authority_watermark
        or canonical_sha256(
            {
                "project_id": exact.publication.project_id,
                "source_id": exact.publication.source_id,
                "generation_id": exact.publication.generation_id,
                "thread_id": exact.publication.thread_id,
                "acl_ref": exact.publication.acl_ref,
                "publication_sha256": exact.publication.publication_sha256,
            }
        ),
        source_snapshot_sha256=source_snapshot_sha256 or exact.observable_thread.content_sha256,
    )
    values = {
        "bundle_sha256": exact.bundle_sha256,
        "publication_sha256": exact.publication.publication_sha256,
        "operation": str(summary["operation"]),
        "publication_id": str(summary["publication_id"]),
        "generation_id": str(summary["generation_id"]),
        "episode_count": len(exact.episodes.episodes),
        "unit_count": len(exact.publication.units),
        "edge_count": len(exact.publication.edges),
        "schema_version": CODEX_V2_SCHEMA_VERSION,
    }
    return CodexPipelinePublicationReceiptV2(
        **values,
        receipt_sha256=canonical_sha256(values),
    )


def query_codex_thread_pipeline_v2(
    bundle: CodexThreadPipelineBundleV2,
    query: str,
    *,
    task: CodexQueryTask | str,
    final_k: int = 12,
    budget_chars: int = 12_000,
    enabled_channels: frozenset[CodexCandidateChannel] | None = None,
    calibration: CodexCalibrationArtifactV2 | None = None,
    store: CodexV2Store | None = None,
) -> CodexPipelineQueryResultV2:
    exact = CodexThreadPipelineBundleV2.model_validate(
        bundle.model_dump(mode="python", round_trip=True)
    )
    dense_profile = build_codex_dense_profile_v2(purpose=dense_purpose_for_task_v2(task))
    cache_scope = {
        "project_id": exact.publication.project_id,
        "source_id": exact.publication.source_id,
        "generation_id": exact.publication.generation_id,
        "acl_ref": exact.publication.acl_ref,
    }
    dense_cache = (
        LocalCodexDenseCacheV2(dense_profile, **cache_scope)
        if store is None
        else _StoreBackedCodexDenseCacheV2(store, dense_profile, **cache_scope)
    )
    retrieval = retrieve_codex_v2(
        exact.publication,
        query,
        task=task,
        dense_profile=dense_profile,
        cache=dense_cache,
        final_k=final_k,
        enabled_channels=enabled_channels,
        calibration=calibration,
    )
    context = build_codex_task_context_v2(
        exact.publication,
        retrieval,
        budget_chars=budget_chars,
    )
    persisted_dense_entries = 0
    if store is not None:
        if not isinstance(dense_cache, _StoreBackedCodexDenseCacheV2):
            raise RuntimeError("Codex dense cache is not store-backed")
        for entry in dense_cache.new_entries:
            store.put_dense_cache(entry)
        persisted_dense_entries = len(dense_cache.entries)
    values = {
        "bundle_sha256": exact.bundle_sha256,
        "retrieval": retrieval,
        "context": context,
        "persisted_dense_entries": persisted_dense_entries,
        "pipeline_version": CODEX_PIPELINE_VERSION,
    }
    return CodexPipelineQueryResultV2(
        **values,
        result_sha256=canonical_sha256(
            {
                key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
                for key, value in values.items()
            }
        ),
    )


__all__ = [
    "CODEX_PIPELINE_COMPONENT_SET_SHA256",
    "CODEX_PIPELINE_VERSION",
    "CodexPipelinePublicationReceiptV2",
    "CodexPipelineQueryResultV2",
    "CodexThreadPipelineBundleV2",
    "build_codex_thread_pipeline_v2",
    "publish_codex_thread_pipeline_v2",
    "query_codex_thread_pipeline_v2",
]
