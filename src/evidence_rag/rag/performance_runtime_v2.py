"""Bounded, in-memory M7 performance runtime.

This module deliberately owns no durable state.  It makes the reviewed M7
index/cache/dashboard contracts reachable from the application Runtime while
keeping the V1 default and release qualification unchanged.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping
from threading import Lock
from typing import TYPE_CHECKING

from .performance_v2 import (
    CacheLayerV2,
    ContentAddressedEmbeddingCacheV2,
    EmbeddingCacheEntryV2,
    ExactMemoryVectorIndexV2,
    LayeredScopedCacheV2,
    PerformanceDashboardV2,
    PerformanceSpanV2,
    VectorMetadataV2,
    VectorRecordV2,
    VectorSearchFiltersV2,
    build_embedding_cache_key_v2,
    build_performance_dashboard_v2,
    build_scoped_cache_key_v2,
)
from .sources.experiment.contracts_v2 import canonical_sha256_v2

if TYPE_CHECKING:
    from .multisource_foundation_v2 import MultiSourceCandidateV2, MultiSourceScopeV2

PERFORMANCE_RUNTIME_VERSION = "bounded-performance-runtime-v2"
_TRACE_ID_PATTERN = re.compile(r"^trace-[0-9a-f]{32}$")
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_SPANS = frozenset(
    {
        "global_v2_execution",
        "source_retrieval",
        "rerank",
        "context_pack",
        "generation",
    }
)
_SAFE_SOURCES = frozenset({"code", "codex", "experiment", "notebook", "document", "workspace"})
_SAFE_ERROR_CODES = frozenset(
    {"unavailable", "partial", "timeout", "unauthorized", "not_indexed", "internal_error"}
)
_SAFE_SCALE_KEYS = frozenset({"entities", "candidates", "vectors", "queries", "tokens", "bytes"})
_SAFE_HARDWARE_PROFILES = frozenset({"repository-runtime-unqualified", "production-attested"})
_SAFE_CACHE_STATES = frozenset({"in_memory_ephemeral", "cold", "warm", "mixed"})
_EMBEDDING_DIMENSION = 32
_EMBEDDING_MODEL_REVISION = "deterministic-hash-exact-r1"
_EMBEDDING_NORMALIZATION_VERSION = "signed-byte-buckets-v1"


def _safe_optional_member(
    value: str | None,
    *,
    allowed: frozenset[str],
    label: str,
) -> None:
    if value is not None and value not in allowed:
        raise ValueError(f"performance {label} is not allowlisted")


class RuntimePerformanceV2:
    """Own exact-index/cache baselines and a bounded sanitized span buffer."""

    runtime_version = PERFORMANCE_RUNTIME_VERSION

    def __init__(self, *, max_spans: int = 2_048) -> None:
        if isinstance(max_spans, bool) or not 1 <= max_spans <= 100_000:
            raise ValueError("performance span capacity is invalid")
        self.vector_index = ExactMemoryVectorIndexV2()
        self.embedding_cache = ContentAddressedEmbeddingCacheV2()
        self.layered_cache = LayeredScopedCacheV2()
        self._spans: deque[PerformanceSpanV2] = deque(maxlen=max_spans)
        self._lock = Lock()

    @staticmethod
    def _embedding(value: str) -> tuple[float, ...]:
        """Build a deterministic local baseline vector without external I/O."""

        buckets = [0.0] * _EMBEDDING_DIMENSION
        encoded = value.casefold().encode("utf-8", errors="ignore")
        if not encoded:
            return tuple(buckets)
        for index, byte in enumerate(encoded):
            bucket = (byte + index * 17) % _EMBEDDING_DIMENSION
            buckets[bucket] += 1.0 if byte % 2 == 0 else -1.0
        norm = sum(item * item for item in buckets) ** 0.5
        return tuple(item / norm for item in buckets) if norm else tuple(buckets)

    def _cached_embedding(self, value: str, *, content_sha256: str) -> tuple[float, ...]:
        key = build_embedding_cache_key_v2(
            content_sha256=content_sha256,
            retrieval_unit_builder_version="multisource-candidate-v2",
            embedding_model_revision=_EMBEDDING_MODEL_REVISION,
            normalization_version=_EMBEDDING_NORMALIZATION_VERSION,
        )
        with self._lock:
            cached = self.embedding_cache.get(key)
        if cached is not None:
            return cached.vector
        vector = self._embedding(value)
        payload = {
            "key": key.model_dump(mode="json"),
            "vector": vector,
            "dimension": len(vector),
            "provider": "local_deterministic",
        }
        entry = EmbeddingCacheEntryV2(
            key=key,
            vector=vector,
            dimension=len(vector),
            provider="local_deterministic",
            content_sha256=canonical_sha256_v2(payload),
        )
        with self._lock:
            self.embedding_cache.put(entry)
        return vector

    def augment_candidates(
        self,
        *,
        question: str,
        plan_sha256: str,
        scope: MultiSourceScopeV2,
        source: str,
        generation: str,
        candidates: tuple[MultiSourceCandidateV2, ...],
    ) -> tuple[tuple[MultiSourceCandidateV2, ...], bool]:
        """Read/write the scoped cache, embedding cache, and exact index on query path."""

        question_sha256 = canonical_sha256_v2(question)
        authority_sha256 = canonical_sha256_v2(scope.acl_refs)
        scope_sha256 = canonical_sha256_v2(scope.model_dump(mode="json"))
        candidates_sha256 = canonical_sha256_v2(
            [
                (
                    item.candidate_id,
                    item.fact_type,
                    item.entity_type,
                    item.stable_version,
                    item.source_generation,
                )
                for item in candidates
            ]
        )
        cache_key = build_scoped_cache_key_v2(
            layer=CacheLayerV2.SOURCE_RETRIEVAL,
            project_id=scope.project_id,
            subject_authority_sha256=authority_sha256,
            scope_sha256=scope_sha256,
            active_generations={source: generation},
            component_versions={
                "embedding": _EMBEDDING_MODEL_REVISION,
                "exact_index": self.vector_index.index_version,
                "plan": plan_sha256,
            },
            input_sha256=canonical_sha256_v2(
                {
                    "question_sha256": question_sha256,
                    "candidates_sha256": candidates_sha256,
                }
            ),
        )
        with self._lock:
            cached = self.layered_cache.get(cache_key)
        if cached is not None:
            return tuple(cached), True
        if not candidates:
            with self._lock:
                self.layered_cache.put(cache_key, ())
            return (), False

        namespace = (
            "multisource-"
            + source
            + "-"
            + canonical_sha256_v2(scope.project_id).removeprefix("sha256:")[:16]
        )
        exact_generation = canonical_sha256_v2(
            {
                "source": source,
                "generation": generation,
                "candidates_sha256": candidates_sha256,
            }
        )
        records: list[VectorRecordV2] = []
        for item in candidates:
            content_sha256 = canonical_sha256_v2(
                {
                    "title": item.title,
                    "snippet": item.snippet,
                    "fact_type": item.fact_type,
                    "entity_type": item.entity_type,
                }
            )
            vector = self._cached_embedding(
                f"{item.title}\n{item.snippet}",
                content_sha256=content_sha256,
            )
            metadata = VectorMetadataV2(
                project_id=scope.project_id,
                acl_refs=(item.acl_ref,),
                source=source,
                entity_id=item.entity_id,
                retrieval_unit_id=item.retrieval_unit_id,
                stable_version=item.stable_version,
                generation=exact_generation,
                content_sha256=content_sha256,
            )
            payload = {
                "vector_id": item.candidate_id,
                "vector": vector,
                "metadata": metadata.model_dump(mode="json"),
            }
            records.append(
                VectorRecordV2(
                    vector_id=item.candidate_id,
                    vector=vector,
                    metadata=metadata,
                    content_sha256=canonical_sha256_v2(payload),
                )
            )
        query_vector = self._cached_embedding(
            question,
            content_sha256=question_sha256,
        )
        with self._lock:
            self.vector_index.upsert(namespace, exact_generation, tuple(records))
            self.vector_index.activate_generation(namespace, exact_generation)
            result = self.vector_index.search(
                namespace,
                query_vector,
                VectorSearchFiltersV2(
                    project_id=scope.project_id,
                    acl_refs=scope.acl_refs,
                    sources=(source,),
                    stable_versions=(),
                ),
                len(candidates),
            )
        score_by_id = {item.vector_id: (item.score + 1.0) / 2.0 for item in result.hits}
        augmented = tuple(
            sorted(
                (
                    item.model_copy(
                        update={
                            "channel_scores": tuple(
                                (
                                    (name, score)
                                    for name, score in item.channel_scores
                                    if name != "exact_dense"
                                )
                            )
                            + (("exact_dense", score_by_id.get(item.candidate_id, 0.0)),)
                        }
                    )
                    for item in candidates
                ),
                key=lambda item: (
                    -score_by_id.get(item.candidate_id, 0.0),
                    item.candidate_id,
                ),
            )
        )
        with self._lock:
            self.layered_cache.put(cache_key, augmented)
        return augmented, False

    def record(self, span: PerformanceSpanV2) -> None:
        """Record one already-aggregated span after strict identifier checks."""

        if _TRACE_ID_PATTERN.fullmatch(span.trace_id) is None:
            raise ValueError("performance trace must be an internal digest identifier")
        if span.span not in _SAFE_SPANS:
            raise ValueError("performance span name is not allowlisted")
        _safe_optional_member(span.source, allowed=_SAFE_SOURCES, label="source")
        _safe_optional_member(
            span.error_code,
            allowed=_SAFE_ERROR_CODES,
            label="error code",
        )
        if (
            span.index_generation is not None
            and _DIGEST_PATTERN.fullmatch(span.index_generation) is None
        ):
            raise ValueError("performance generation must be a content digest")
        with self._lock:
            self._spans.append(span)

    def spans(self) -> tuple[PerformanceSpanV2, ...]:
        with self._lock:
            return tuple(self._spans)

    def clear_spans(self) -> int:
        with self._lock:
            count = len(self._spans)
            self._spans.clear()
        return count

    def dashboard(
        self,
        *,
        active_generations: Mapping[str, str] | None = None,
        data_scale: Mapping[str, int] | None = None,
        hardware_profile: str = "repository-runtime-unqualified",
        concurrency: int = 1,
        cache_state: str = "in_memory_ephemeral",
        reviewed_calibration_slices: Iterable[str] = (),
        required_calibration_slices: Iterable[str] = (),
    ) -> PerformanceDashboardV2:
        if hardware_profile not in _SAFE_HARDWARE_PROFILES:
            raise ValueError("performance hardware profile is not allowlisted")
        if cache_state not in _SAFE_CACHE_STATES:
            raise ValueError("performance cache state is not allowlisted")
        generations = dict(active_generations or {})
        if any(source not in _SAFE_SOURCES for source in generations):
            raise ValueError("performance generation source is not allowlisted")
        if any(_DIGEST_PATTERN.fullmatch(value) is None for value in generations.values()):
            raise ValueError("performance generation value must be a content digest")
        scale = dict(data_scale or {})
        if any(key not in _SAFE_SCALE_KEYS for key in scale):
            raise ValueError("performance scale key is not allowlisted")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10**15
            for value in scale.values()
        ):
            raise ValueError("performance scale value is invalid")
        return build_performance_dashboard_v2(
            self.spans(),
            active_generations=generations,
            data_scale=scale,
            hardware_profile=hardware_profile,
            concurrency=concurrency,
            cache_state=cache_state,
            reviewed_calibration_slices=reviewed_calibration_slices,
            required_calibration_slices=required_calibration_slices,
        )

    def operational_snapshot(
        self,
        *,
        reviewed_calibration_slices: Iterable[str] = (),
        required_calibration_slices: Iterable[str] = (),
    ) -> dict[str, object]:
        """Expose counters and versions only; never query, ACL, or locator content."""

        dashboard = self.dashboard(
            reviewed_calibration_slices=reviewed_calibration_slices,
            required_calibration_slices=required_calibration_slices,
        )
        with self._lock:
            vector = self.vector_index.operational_snapshot()
            embedding = self.embedding_cache.operational_snapshot()
            scoped = self.layered_cache.operational_snapshot()
        return {
            "runtime_version": self.runtime_version,
            "status": "QUALITY_HOLD",
            "span_count": dashboard.span_count,
            "trace_count": dashboard.trace_count,
            "cache_hits": dashboard.cache_hits,
            "calibration_availability": dashboard.calibration_availability,
            "unavailable_calibration_slice_count": len(dashboard.unavailable_calibration_slices),
            "vector_index": vector,
            "embedding_cache": embedding,
            "scoped_cache": scoped,
        }


__all__ = [
    "PERFORMANCE_RUNTIME_VERSION",
    "RuntimePerformanceV2",
]
