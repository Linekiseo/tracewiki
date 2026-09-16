"""M7 performance contracts, exact vector baseline, caches, and observability."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .sources.experiment.contracts_v2 import canonical_sha256_v2

VECTOR_INDEX_VERSION = "vector-index-contract-v2"
EXACT_VECTOR_INDEX_VERSION = "exact-memory-vector-index-v2"
EMBEDDING_CACHE_VERSION = "content-addressed-embedding-cache-v2"
LAYERED_CACHE_VERSION = "scoped-layered-cache-v2"
PERFORMANCE_DASHBOARD_VERSION = "multisource-performance-dashboard-v2"
ANN_DECISION_VERSION = "ann-admission-decision-v2"
PERFORMANCE_EVIDENCE_AUTHORITY_VERSION = "performance-evidence-authority-v1"
PERFORMANCE_EVIDENCE_ATTESTATION_VERSION = "performance-evidence-attestation-v1"
PERFORMANCE_EVIDENCE_VERIFIER_VERSION = "performance-evidence-verifier-v1"
_PERFORMANCE_SAMPLE_KINDS = ("build", "latency", "query", "recall", "storage")
_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class VectorMetadataV2(_Frozen):
    project_id: str
    acl_refs: tuple[str, ...] = Field(min_length=1)
    source: str
    entity_id: str
    retrieval_unit_id: str
    stable_version: str
    generation: str
    content_sha256: str

    @model_validator(mode="after")
    def _canonical(self) -> VectorMetadataV2:
        if self.acl_refs != tuple(sorted(set(self.acl_refs))):
            raise ValueError("vector ACL references must be sorted and unique")
        if not self.content_sha256.startswith("sha256:"):
            raise ValueError("vector metadata requires a content digest")
        return self


class VectorRecordV2(_Frozen):
    vector_id: str
    vector: tuple[float, ...] = Field(min_length=1)
    metadata: VectorMetadataV2
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> VectorRecordV2:
        if any(not math.isfinite(value) for value in self.vector):
            raise ValueError("vector values must be finite")
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        if self.content_sha256 != canonical_sha256_v2(payload):
            raise ValueError("vector record digest mismatch")
        return self


class VectorSearchFiltersV2(_Frozen):
    project_id: str
    acl_refs: tuple[str, ...] = Field(min_length=1)
    sources: tuple[str, ...] = ()
    stable_versions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _canonical(self) -> VectorSearchFiltersV2:
        for values in (self.acl_refs, self.sources, self.stable_versions):
            if values != tuple(sorted(set(values))):
                raise ValueError("vector search filters must be sorted and unique")
        return self


class VectorSearchHitV2(_Frozen):
    vector_id: str
    score: float = Field(ge=-1, le=1)
    metadata: VectorMetadataV2


class VectorSearchResultV2(_Frozen):
    namespace: str
    generation: str | None
    index_version: str
    exact: bool
    considered: int = Field(ge=0)
    filtered: int = Field(ge=0)
    hits: tuple[VectorSearchHitV2, ...]
    unavailable_reason: str | None


@runtime_checkable
class VectorIndexV2(Protocol):
    """Derived-index API; the durable entity store remains authoritative."""

    def upsert(
        self,
        namespace: str,
        generation: str,
        vectors: tuple[VectorRecordV2, ...],
    ) -> None: ...

    def search(
        self,
        namespace: str,
        query_vector: tuple[float, ...],
        filters: VectorSearchFiltersV2,
        k: int,
    ) -> VectorSearchResultV2: ...

    def delete_generation(self, namespace: str, generation: str) -> None: ...

    def activate_generation(self, namespace: str, generation: str) -> None: ...


class ExactMemoryVectorIndexV2:
    """Deterministic correctness baseline with metadata filtering before scoring."""

    index_version = EXACT_VECTOR_INDEX_VERSION

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict[str, VectorRecordV2]] = {}
        self._active: dict[str, str] = {}
        self._dimensions: dict[tuple[str, str], int] = {}

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        if not value or len(value) > 160 or any(char.isspace() for char in value):
            raise ValueError(f"invalid {label}")
        return value

    def upsert(
        self,
        namespace: str,
        generation: str,
        vectors: tuple[VectorRecordV2, ...],
    ) -> None:
        namespace = self._identifier(namespace, "namespace")
        generation = self._identifier(generation, "generation")
        if not vectors:
            raise ValueError("vector upsert cannot be empty")
        if any(item.metadata.generation != generation for item in vectors):
            raise ValueError("vector generation mismatch")
        dimensions = {len(item.vector) for item in vectors}
        if len(dimensions) != 1:
            raise ValueError("mixed vector dimensions")
        if len({item.vector_id for item in vectors}) != len(vectors):
            raise ValueError("duplicate vector identity")
        key = (namespace, generation)
        expected = self._dimensions.get(key)
        dimension = next(iter(dimensions))
        if expected is not None and expected != dimension:
            raise ValueError("vector generation dimension mismatch")
        staged = dict(self._records.get(key, {}))
        staged.update({item.vector_id: item for item in vectors})
        self._records[key] = staged
        self._dimensions[key] = dimension

    def activate_generation(self, namespace: str, generation: str) -> None:
        namespace = self._identifier(namespace, "namespace")
        generation = self._identifier(generation, "generation")
        if (namespace, generation) not in self._records:
            raise ValueError("cannot activate missing generation")
        self._active[namespace] = generation

    def delete_generation(self, namespace: str, generation: str) -> None:
        namespace = self._identifier(namespace, "namespace")
        generation = self._identifier(generation, "generation")
        if self._active.get(namespace) == generation:
            raise ValueError("cannot delete active generation")
        self._records.pop((namespace, generation), None)
        self._dimensions.pop((namespace, generation), None)

    def search(
        self,
        namespace: str,
        query_vector: tuple[float, ...],
        filters: VectorSearchFiltersV2,
        k: int,
    ) -> VectorSearchResultV2:
        namespace = self._identifier(namespace, "namespace")
        if k < 1 or k > 1_000:
            raise ValueError("invalid vector result limit")
        if not query_vector or any(not math.isfinite(item) for item in query_vector):
            raise ValueError("query vector must be finite and non-empty")
        generation = self._active.get(namespace)
        if generation is None:
            return VectorSearchResultV2(
                namespace=namespace,
                generation=None,
                index_version=self.index_version,
                exact=True,
                considered=0,
                filtered=0,
                hits=(),
                unavailable_reason="active_generation_unavailable",
            )
        key = (namespace, generation)
        if len(query_vector) != self._dimensions[key]:
            raise ValueError("query vector dimension mismatch")
        records = tuple(self._records[key].values())
        allowed_acl = set(filters.acl_refs)
        candidates = tuple(
            item
            for item in records
            if item.metadata.project_id == filters.project_id
            and bool(set(item.metadata.acl_refs) & allowed_acl)
            and (not filters.sources or item.metadata.source in filters.sources)
            and (
                not filters.stable_versions
                or item.metadata.stable_version in filters.stable_versions
            )
        )
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        hits: list[VectorSearchHitV2] = []
        for item in candidates:
            item_norm = math.sqrt(sum(value * value for value in item.vector))
            score = (
                sum(left * right for left, right in zip(query_vector, item.vector, strict=True))
                / (query_norm * item_norm)
                if query_norm and item_norm
                else 0.0
            )
            hits.append(
                VectorSearchHitV2(
                    vector_id=item.vector_id,
                    score=max(-1.0, min(1.0, score)),
                    metadata=item.metadata,
                )
            )
        hits.sort(key=lambda item: (-item.score, item.vector_id))
        return VectorSearchResultV2(
            namespace=namespace,
            generation=generation,
            index_version=self.index_version,
            exact=True,
            considered=len(records),
            filtered=len(records) - len(candidates),
            hits=tuple(hits[:k]),
            unavailable_reason=None,
        )

    def operational_snapshot(self) -> dict[str, int | str]:
        """Return bounded counters only; namespaces and generations stay private."""

        return {
            "index_version": self.index_version,
            "namespace_count": len({namespace for namespace, _ in self._records}),
            "generation_count": len(self._records),
            "active_namespace_count": len(self._active),
            "vector_count": sum(len(records) for records in self._records.values()),
        }


class EmbeddingCacheKeyV2(_Frozen):
    content_sha256: str
    retrieval_unit_builder_version: str
    embedding_model_revision: str
    normalization_version: str
    key_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> EmbeddingCacheKeyV2:
        if not self.content_sha256.startswith("sha256:"):
            raise ValueError("embedding cache content hash is invalid")
        if self.key_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"key_sha256"})
        ):
            raise ValueError("embedding cache key digest mismatch")
        return self


def build_embedding_cache_key_v2(
    *,
    content_sha256: str,
    retrieval_unit_builder_version: str,
    embedding_model_revision: str,
    normalization_version: str,
) -> EmbeddingCacheKeyV2:
    payload = {
        "content_sha256": content_sha256,
        "retrieval_unit_builder_version": retrieval_unit_builder_version,
        "embedding_model_revision": embedding_model_revision,
        "normalization_version": normalization_version,
    }
    return EmbeddingCacheKeyV2(
        **payload,
        key_sha256=canonical_sha256_v2(payload),
    )


class EmbeddingCacheEntryV2(_Frozen):
    key: EmbeddingCacheKeyV2
    vector: tuple[float, ...] = Field(min_length=1)
    dimension: int = Field(ge=1)
    provider: str
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> EmbeddingCacheEntryV2:
        if len(self.vector) != self.dimension:
            raise ValueError("embedding cache dimension mismatch")
        if any(not math.isfinite(item) for item in self.vector):
            raise ValueError("embedding cache contains non-finite values")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("embedding cache entry digest mismatch")
        return self


class ContentAddressedEmbeddingCacheV2:
    def __init__(self) -> None:
        self._entries: dict[str, EmbeddingCacheEntryV2] = {}

    def get(self, key: EmbeddingCacheKeyV2) -> EmbeddingCacheEntryV2 | None:
        return self._entries.get(key.key_sha256)

    def put(self, entry: EmbeddingCacheEntryV2) -> None:
        existing = self._entries.get(entry.key.key_sha256)
        if existing is not None and existing != entry:
            raise ValueError("embedding cache collision")
        self._entries[entry.key.key_sha256] = entry

    def invalidate_model(self, embedding_model_revision: str) -> int:
        matches = tuple(
            key
            for key, entry in self._entries.items()
            if entry.key.embedding_model_revision == embedding_model_revision
        )
        for key in matches:
            del self._entries[key]
        return len(matches)

    def operational_snapshot(self) -> dict[str, int | str]:
        return {
            "cache_version": EMBEDDING_CACHE_VERSION,
            "entry_count": len(self._entries),
        }


class CacheLayerV2(StrEnum):
    QUERY_PLAN = "query_plan"
    QUERY_EMBEDDING = "query_embedding"
    SOURCE_RETRIEVAL = "source_retrieval"
    RERANK = "rerank"
    CONTEXT_PACK = "context_pack"
    GENERATION = "generation"


class ScopedCacheKeyV2(_Frozen):
    layer: CacheLayerV2
    project_id: str
    subject_authority_sha256: str
    scope_sha256: str
    active_generations: tuple[tuple[str, str], ...]
    component_versions: tuple[tuple[str, str], ...]
    input_sha256: str
    key_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> ScopedCacheKeyV2:
        if self.active_generations != tuple(sorted(set(self.active_generations))):
            raise ValueError("cache generations must be sorted and unique")
        if self.component_versions != tuple(sorted(set(self.component_versions))):
            raise ValueError("cache versions must be sorted and unique")
        if self.key_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"key_sha256"})
        ):
            raise ValueError("scoped cache key digest mismatch")
        return self


def build_scoped_cache_key_v2(
    *,
    layer: CacheLayerV2,
    project_id: str,
    subject_authority_sha256: str,
    scope_sha256: str,
    active_generations: Mapping[str, str],
    component_versions: Mapping[str, str],
    input_sha256: str,
) -> ScopedCacheKeyV2:
    payload = {
        "layer": layer,
        "project_id": project_id,
        "subject_authority_sha256": subject_authority_sha256,
        "scope_sha256": scope_sha256,
        "active_generations": tuple(sorted(active_generations.items())),
        "component_versions": tuple(sorted(component_versions.items())),
        "input_sha256": input_sha256,
    }
    return ScopedCacheKeyV2(
        **payload,
        key_sha256=canonical_sha256_v2(
            ScopedCacheKeyV2.model_construct(key_sha256="pending", **payload).model_dump(
                mode="json", exclude={"key_sha256"}
            )
        ),
    )


class LayeredScopedCacheV2:
    def __init__(self) -> None:
        self._values: dict[tuple[CacheLayerV2, str], Any] = {}
        self._keys: dict[tuple[CacheLayerV2, str], ScopedCacheKeyV2] = {}

    def get(self, key: ScopedCacheKeyV2) -> Any | None:
        return self._values.get((key.layer, key.key_sha256))

    def put(self, key: ScopedCacheKeyV2, value: Any) -> None:
        if value is None:
            raise ValueError("cache cannot store null")
        identity = (key.layer, key.key_sha256)
        self._values[identity] = value
        self._keys[identity] = key

    def invalidate(
        self,
        *,
        layer: CacheLayerV2 | None = None,
        project_id: str | None = None,
    ) -> int:
        matches = tuple(
            key
            for key in self._values
            if (layer is None or key[0] is layer)
            and (project_id is None or self._keys[key].project_id == project_id)
        )
        for key in matches:
            del self._values[key]
            del self._keys[key]
        return len(matches)

    def put_keyed(self, key: ScopedCacheKeyV2, value: Any) -> None:
        self.put(key, value)

    def operational_snapshot(self) -> dict[str, object]:
        counts = tuple(
            sorted(
                (
                    layer.value,
                    sum(key_layer is layer for key_layer, _ in self._values),
                )
                for layer in CacheLayerV2
            )
        )
        return {
            "cache_version": LAYERED_CACHE_VERSION,
            "entry_count": len(self._values),
            "layers": counts,
        }


class PerformanceSpanV2(_Frozen):
    trace_id: str
    span: str
    source: str | None
    latency_ms: float = Field(ge=0)
    candidate_count: int = Field(ge=0)
    filtered_count: int = Field(ge=0)
    token_count: int = Field(ge=0)
    cost_units: float = Field(ge=0)
    cache_hit: bool
    error_code: str | None
    index_generation: str | None


class DistributionV2(_Frozen):
    count: int = Field(ge=0)
    p50: float = Field(ge=0)
    p95: float = Field(ge=0)
    p99: float = Field(ge=0)
    maximum: float = Field(ge=0)


class PerformanceDashboardV2(_Frozen):
    trace_count: int = Field(ge=0)
    span_count: int = Field(ge=0)
    latency: tuple[tuple[str, DistributionV2], ...]
    candidate_total: int = Field(ge=0)
    filtered_total: int = Field(ge=0)
    token_total: int = Field(ge=0)
    cost_total: float = Field(ge=0)
    cache_hits: int = Field(ge=0)
    error_counts: tuple[tuple[str, int], ...]
    active_generations: tuple[tuple[str, str], ...]
    data_scale: tuple[tuple[str, int], ...]
    hardware_profile: str
    concurrency: int = Field(ge=1)
    cache_state: str
    calibration_availability: Literal["AVAILABLE", "UNAVAILABLE"] = "UNAVAILABLE"
    unavailable_calibration_slices: tuple[str, ...] = ()
    dashboard_version: str = PERFORMANCE_DASHBOARD_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> PerformanceDashboardV2:
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("performance dashboard digest mismatch")
        return self


def _percentile(values: tuple[float, ...], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[rank]


def build_performance_dashboard_v2(
    spans: Iterable[PerformanceSpanV2],
    *,
    active_generations: Mapping[str, str],
    data_scale: Mapping[str, int],
    hardware_profile: str,
    concurrency: int,
    cache_state: str,
    reviewed_calibration_slices: Iterable[str] = (),
    required_calibration_slices: Iterable[str] = (),
) -> PerformanceDashboardV2:
    rows = tuple(spans)
    reviewed_slices = frozenset(reviewed_calibration_slices)
    required_slices = frozenset(required_calibration_slices)
    unavailable_slices = tuple(sorted(required_slices - reviewed_slices))
    grouped: dict[str, list[float]] = defaultdict(list)
    errors: dict[str, int] = defaultdict(int)
    for item in rows:
        grouped[item.span].append(item.latency_ms)
        if item.error_code:
            errors[item.error_code] += 1
    latency = tuple(
        (
            span,
            DistributionV2(
                count=len(values),
                p50=_percentile(tuple(values), 0.50),
                p95=_percentile(tuple(values), 0.95),
                p99=_percentile(tuple(values), 0.99),
                maximum=max(values),
            ),
        )
        for span, values in sorted(grouped.items())
    )
    payload = {
        "trace_count": len({item.trace_id for item in rows}),
        "span_count": len(rows),
        "latency": latency,
        "candidate_total": sum(item.candidate_count for item in rows),
        "filtered_total": sum(item.filtered_count for item in rows),
        "token_total": sum(item.token_count for item in rows),
        "cost_total": sum(item.cost_units for item in rows),
        "cache_hits": sum(item.cache_hit for item in rows),
        "error_counts": tuple(sorted(errors.items())),
        "active_generations": tuple(sorted(active_generations.items())),
        "data_scale": tuple(sorted(data_scale.items())),
        "hardware_profile": hardware_profile,
        "concurrency": concurrency,
        "cache_state": cache_state,
        "calibration_availability": (
            "AVAILABLE" if required_slices and not unavailable_slices else "UNAVAILABLE"
        ),
        "unavailable_calibration_slices": unavailable_slices,
        "dashboard_version": PERFORMANCE_DASHBOARD_VERSION,
    }
    normalized = PerformanceDashboardV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return PerformanceDashboardV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def _canonical_measurement_timestamp(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("performance measurement timestamp must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("performance measurement timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("performance measurement timestamp must be canonical UTC")
    canonical = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if value != canonical:
        raise ValueError("performance measurement timestamp must use millisecond UTC format")
    return parsed


def _sample_denominators(
    values: Mapping[str, int],
) -> tuple[tuple[str, int], ...]:
    normalized = tuple(sorted(values.items()))
    if tuple(name for name, _ in normalized) != _PERFORMANCE_SAMPLE_KINDS:
        raise ValueError("performance sample denominators are incomplete")
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count <= 0
        for _, count in normalized
    ):
        raise ValueError("performance sample denominators must be positive integers")
    return normalized


class PerformanceEvidenceAuthorityV2(_Frozen):
    runner_sha256: str = Field(pattern=_SHA256_PATTERN)
    component_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    query_membership_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    authority_version: Literal["performance-evidence-authority-v1"] = (
        PERFORMANCE_EVIDENCE_AUTHORITY_VERSION
    )
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> PerformanceEvidenceAuthorityV2:
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("performance evidence authority digest mismatch")
        return self


def build_performance_evidence_authority_v2(
    *,
    runner_sha256: str,
    component_sha256: str,
    dataset_sha256: str,
    query_membership_sha256: str,
    source_artifact_sha256: str,
) -> PerformanceEvidenceAuthorityV2:
    payload = {
        "runner_sha256": runner_sha256,
        "component_sha256": component_sha256,
        "dataset_sha256": dataset_sha256,
        "query_membership_sha256": query_membership_sha256,
        "source_artifact_sha256": source_artifact_sha256,
        "authority_version": PERFORMANCE_EVIDENCE_AUTHORITY_VERSION,
    }
    return PerformanceEvidenceAuthorityV2(
        **payload,
        content_sha256=canonical_sha256_v2(payload),
    )


class PerformanceEvidenceAttestationV2(_Frozen):
    runner_sha256: str = Field(pattern=_SHA256_PATTERN)
    component_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    query_membership_sha256: str = Field(pattern=_SHA256_PATTERN)
    sample_denominators: tuple[tuple[str, int], ...]
    measurement_started_at: str
    measurement_completed_at: str
    measurement_window_seconds: float = Field(gt=0)
    source_artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    production_observation: bool
    attestation_version: Literal["performance-evidence-attestation-v1"] = (
        PERFORMANCE_EVIDENCE_ATTESTATION_VERSION
    )
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> PerformanceEvidenceAttestationV2:
        denominators = _sample_denominators(dict(self.sample_denominators))
        if self.sample_denominators != denominators:
            raise ValueError("performance sample denominators must be sorted and unique")
        started = _canonical_measurement_timestamp(self.measurement_started_at)
        completed = _canonical_measurement_timestamp(self.measurement_completed_at)
        observed_window = (completed - started).total_seconds()
        if observed_window <= 0 or not math.isclose(
            observed_window,
            self.measurement_window_seconds,
            rel_tol=0,
            abs_tol=1e-6,
        ):
            raise ValueError("performance measurement window mismatch")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("performance evidence attestation digest mismatch")
        return self


def build_performance_evidence_attestation_v2(
    *,
    runner_sha256: str,
    component_sha256: str,
    dataset_sha256: str,
    query_membership_sha256: str,
    sample_denominators: Mapping[str, int],
    measurement_started_at: str,
    measurement_completed_at: str,
    measurement_window_seconds: float,
    source_artifact_sha256: str,
    production_observation: bool,
) -> PerformanceEvidenceAttestationV2:
    payload = {
        "runner_sha256": runner_sha256,
        "component_sha256": component_sha256,
        "dataset_sha256": dataset_sha256,
        "query_membership_sha256": query_membership_sha256,
        "sample_denominators": _sample_denominators(sample_denominators),
        "measurement_started_at": measurement_started_at,
        "measurement_completed_at": measurement_completed_at,
        "measurement_window_seconds": measurement_window_seconds,
        "source_artifact_sha256": source_artifact_sha256,
        "production_observation": production_observation,
        "attestation_version": PERFORMANCE_EVIDENCE_ATTESTATION_VERSION,
    }
    normalized = PerformanceEvidenceAttestationV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return PerformanceEvidenceAttestationV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


class PerformanceEvidenceVerificationV2(_Frozen):
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    production_qualified: bool
    reasons: tuple[str, ...]
    verifier_version: Literal["performance-evidence-verifier-v1"] = (
        PERFORMANCE_EVIDENCE_VERIFIER_VERSION
    )
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _identity(self) -> PerformanceEvidenceVerificationV2:
        if self.production_qualified != (self.status == "AVAILABLE" and not self.reasons):
            raise ValueError("performance verification status mismatch")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("performance evidence verification digest mismatch")
        return self


def verify_performance_evidence_v2(
    attestation: PerformanceEvidenceAttestationV2 | None,
    *,
    authority: PerformanceEvidenceAuthorityV2 | None,
    dataset_sha256: str,
    query_membership_sha256: str,
    sample_denominators: Mapping[str, int],
) -> PerformanceEvidenceVerificationV2:
    reasons: list[str] = []
    expected_denominators: tuple[tuple[str, int], ...] | None
    try:
        expected_denominators = _sample_denominators(sample_denominators)
    except (TypeError, ValueError):
        expected_denominators = None
        reasons.append("sample_denominator_invalid")
    if authority is None:
        reasons.append("trusted_performance_authority_missing")
    else:
        if authority.dataset_sha256 != dataset_sha256:
            reasons.append("trusted_authority_dataset_mismatch")
        if authority.query_membership_sha256 != query_membership_sha256:
            reasons.append("trusted_authority_query_membership_mismatch")
    if attestation is None:
        reasons.append("performance_attestation_missing")
    if authority is not None and attestation is not None:
        for field in (
            "runner_sha256",
            "component_sha256",
            "dataset_sha256",
            "query_membership_sha256",
            "source_artifact_sha256",
        ):
            if getattr(attestation, field) != getattr(authority, field):
                reasons.append(f"{field}_mismatch")
    if attestation is not None:
        if attestation.dataset_sha256 != dataset_sha256:
            reasons.append("benchmark_dataset_mismatch")
        if attestation.query_membership_sha256 != query_membership_sha256:
            reasons.append("benchmark_query_membership_mismatch")
        if (
            expected_denominators is not None
            and attestation.sample_denominators != expected_denominators
        ):
            reasons.append("benchmark_sample_denominator_mismatch")
        if not attestation.production_observation:
            reasons.append("engineering_evidence_only")
    reasons = list(dict.fromkeys(reasons))
    payload = {
        "status": "AVAILABLE" if not reasons else "UNAVAILABLE",
        "production_qualified": not reasons,
        "reasons": tuple(reasons),
        "verifier_version": PERFORMANCE_EVIDENCE_VERIFIER_VERSION,
    }
    normalized = PerformanceEvidenceVerificationV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return PerformanceEvidenceVerificationV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


class AnnBenchmarkV2(_Frozen):
    backend: str
    exact_baseline_version: str
    dataset_sha256: str
    vector_count: int = Field(ge=0)
    dimensions: int = Field(ge=0)
    concurrency: int = Field(ge=1)
    query_count: int = Field(ge=0)
    query_membership_sha256: str = Field(pattern=_SHA256_PATTERN)
    recall_at_k: float | None = Field(default=None, ge=0, le=1)
    latency_p95_ms: float | None = Field(default=None, ge=0)
    build_time_ms: float | None = Field(default=None, ge=0)
    storage_bytes: int | None = Field(default=None, ge=0)
    status: str
    provenance: str
    performance_availability: Literal["AVAILABLE", "UNAVAILABLE"] = "UNAVAILABLE"
    evidence_reason: str | None = None
    evidence_attestation: PerformanceEvidenceAttestationV2 | None = None
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> AnnBenchmarkV2:
        if self.status == "available" and (
            self.recall_at_k is None
            or self.latency_p95_ms is None
            or self.build_time_ms is None
            or self.storage_bytes is None
        ):
            raise ValueError("available ANN benchmark requires complete measurements")
        if self.status == "available" and self.performance_availability != "AVAILABLE":
            raise ValueError("available ANN benchmark requires qualified performance evidence")
        if self.performance_availability == "AVAILABLE" and (
            self.status != "available"
            or self.evidence_attestation is None
            or not self.evidence_attestation.production_observation
            or self.evidence_reason is not None
        ):
            raise ValueError("qualified performance evidence is internally inconsistent")
        if self.evidence_attestation is not None:
            denominators = dict(self.evidence_attestation.sample_denominators)
            if (
                self.evidence_attestation.dataset_sha256 != self.dataset_sha256
                or self.evidence_attestation.query_membership_sha256 != self.query_membership_sha256
                or denominators["query"] != self.query_count
                or denominators["recall"] != self.query_count
                or denominators["latency"] != self.query_count
            ):
                raise ValueError("ANN benchmark attestation denominator mismatch")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("ANN benchmark digest mismatch")
        return self


class AnnQueryObservationV2(_Frozen):
    query_id: str
    exact_top_k: tuple[str, ...] = Field(min_length=1)
    candidate_top_k: tuple[str, ...] = Field(min_length=1)
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def _canonical(self) -> AnnQueryObservationV2:
        if len(set(self.exact_top_k)) != len(self.exact_top_k):
            raise ValueError("exact benchmark results contain duplicates")
        if len(set(self.candidate_top_k)) != len(self.candidate_top_k):
            raise ValueError("candidate benchmark results contain duplicates")
        return self


def build_ann_query_membership_sha256_v2(
    observations: tuple[AnnQueryObservationV2, ...],
) -> str:
    query_ids = tuple(item.query_id for item in observations)
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("ANN benchmark query membership contains duplicates")
    return canonical_sha256_v2(query_ids)


def build_ann_benchmark_v2(
    *,
    backend: str,
    dataset_sha256: str,
    vector_count: int,
    dimensions: int,
    concurrency: int,
    observations: tuple[AnnQueryObservationV2, ...],
    build_time_ms: float | None,
    storage_bytes: int | None,
    provenance: str,
    evidence_attestation: PerformanceEvidenceAttestationV2 | None = None,
    performance_authority: PerformanceEvidenceAuthorityV2 | None = None,
) -> AnnBenchmarkV2:
    query_membership_sha256 = build_ann_query_membership_sha256_v2(observations)
    denominators = {
        "build": 1,
        "latency": len(observations),
        "query": len(observations),
        "recall": len(observations),
        "storage": 1,
    }
    verification = verify_performance_evidence_v2(
        evidence_attestation,
        authority=performance_authority,
        dataset_sha256=dataset_sha256,
        query_membership_sha256=query_membership_sha256,
        sample_denominators=denominators,
    )
    if not observations:
        payload = {
            "backend": backend,
            "exact_baseline_version": EXACT_VECTOR_INDEX_VERSION,
            "dataset_sha256": dataset_sha256,
            "vector_count": vector_count,
            "dimensions": dimensions,
            "concurrency": concurrency,
            "query_count": 0,
            "query_membership_sha256": query_membership_sha256,
            "recall_at_k": None,
            "latency_p95_ms": None,
            "build_time_ms": None,
            "storage_bytes": None,
            "status": "unavailable",
            "provenance": provenance,
            "performance_availability": "UNAVAILABLE",
            "evidence_reason": ";".join(verification.reasons),
            "evidence_attestation": evidence_attestation,
        }
    else:
        recalls = tuple(
            len(set(item.exact_top_k) & set(item.candidate_top_k)) / len(item.exact_top_k)
            for item in observations
        )
        complete = build_time_ms is not None and storage_bytes is not None
        payload = {
            "backend": backend,
            "exact_baseline_version": EXACT_VECTOR_INDEX_VERSION,
            "dataset_sha256": dataset_sha256,
            "vector_count": vector_count,
            "dimensions": dimensions,
            "concurrency": concurrency,
            "query_count": len(observations),
            "query_membership_sha256": query_membership_sha256,
            "recall_at_k": sum(recalls) / len(recalls) if complete else None,
            "latency_p95_ms": (
                _percentile(tuple(item.latency_ms for item in observations), 0.95)
                if complete
                else None
            ),
            "build_time_ms": build_time_ms if complete else None,
            "storage_bytes": storage_bytes if complete else None,
            "status": (
                "available"
                if complete and verification.production_qualified
                else (
                    "engineering"
                    if complete
                    and evidence_attestation is not None
                    and not evidence_attestation.production_observation
                    else ("unavailable" if complete else "provisional")
                )
            ),
            "provenance": provenance,
            "performance_availability": (verification.status if complete else "UNAVAILABLE"),
            "evidence_reason": (
                None
                if complete and verification.production_qualified
                else ";".join(verification.reasons)
            ),
            "evidence_attestation": evidence_attestation,
        }
    normalized = AnnBenchmarkV2.model_construct(content_sha256="pending", **payload).model_dump(
        mode="json", exclude={"content_sha256"}
    )
    return AnnBenchmarkV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


class AnnAdmissionDecisionV2(_Frozen):
    decision: str
    triggered_conditions: tuple[str, ...]
    exact_fallback_required: bool
    metadata_filter_required: bool
    generation_switch_required: bool
    benchmark_required: bool
    qualified: bool
    version: str = ANN_DECISION_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> AnnAdmissionDecisionV2:
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("ANN admission decision digest mismatch")
        return self


def decide_ann_admission_v2(
    *,
    active_vector_count: int,
    dense_p95_ms: float,
    concurrency_pressure: bool,
    representation_pressure: bool,
    benchmark: AnnBenchmarkV2 | None = None,
    performance_authority: PerformanceEvidenceAuthorityV2 | None = None,
) -> AnnAdmissionDecisionV2:
    if active_vector_count < 0 or not math.isfinite(dense_p95_ms) or dense_p95_ms < 0:
        raise ValueError("invalid ANN decision inputs")
    triggered = tuple(
        name
        for name, active in (
            ("active_vectors_gt_100k", active_vector_count > 100_000),
            ("dense_p95_gt_500ms", dense_p95_ms > 500),
            ("concurrency_pressure", concurrency_pressure),
            ("representation_pressure", representation_pressure),
        )
        if active
    )
    evidence_ok = False
    if benchmark is not None:
        verification = verify_performance_evidence_v2(
            benchmark.evidence_attestation,
            authority=performance_authority,
            dataset_sha256=benchmark.dataset_sha256,
            query_membership_sha256=benchmark.query_membership_sha256,
            sample_denominators={
                "build": 1,
                "latency": benchmark.query_count,
                "query": benchmark.query_count,
                "recall": benchmark.query_count,
                "storage": 1,
            },
        )
        evidence_ok = (
            benchmark.performance_availability == "AVAILABLE" and verification.production_qualified
        )
    benchmark_ok = (
        benchmark is not None
        and benchmark.status == "available"
        and evidence_ok
        and benchmark.recall_at_k is not None
        and benchmark.recall_at_k >= 0.95
        and benchmark.latency_p95_ms is not None
        and benchmark.latency_p95_ms < dense_p95_ms
    )
    if not triggered:
        decision = "KEEP_EXACT"
    elif not benchmark_ok:
        decision = "REVIEW_ANN"
    else:
        decision = "ANN_ELIGIBLE"
    payload = {
        "decision": decision,
        "triggered_conditions": triggered,
        "exact_fallback_required": True,
        "metadata_filter_required": True,
        "generation_switch_required": True,
        "benchmark_required": bool(triggered),
        "qualified": decision == "ANN_ELIGIBLE",
        "version": ANN_DECISION_VERSION,
    }
    normalized = AnnAdmissionDecisionV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return AnnAdmissionDecisionV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


__all__ = [
    "ANN_DECISION_VERSION",
    "EMBEDDING_CACHE_VERSION",
    "EXACT_VECTOR_INDEX_VERSION",
    "LAYERED_CACHE_VERSION",
    "PERFORMANCE_DASHBOARD_VERSION",
    "PERFORMANCE_EVIDENCE_ATTESTATION_VERSION",
    "PERFORMANCE_EVIDENCE_AUTHORITY_VERSION",
    "PERFORMANCE_EVIDENCE_VERIFIER_VERSION",
    "VECTOR_INDEX_VERSION",
    "AnnAdmissionDecisionV2",
    "AnnBenchmarkV2",
    "AnnQueryObservationV2",
    "CacheLayerV2",
    "ContentAddressedEmbeddingCacheV2",
    "DistributionV2",
    "EmbeddingCacheEntryV2",
    "EmbeddingCacheKeyV2",
    "ExactMemoryVectorIndexV2",
    "LayeredScopedCacheV2",
    "PerformanceDashboardV2",
    "PerformanceEvidenceAttestationV2",
    "PerformanceEvidenceAuthorityV2",
    "PerformanceEvidenceVerificationV2",
    "PerformanceSpanV2",
    "ScopedCacheKeyV2",
    "VectorIndexV2",
    "VectorMetadataV2",
    "VectorRecordV2",
    "VectorSearchFiltersV2",
    "VectorSearchHitV2",
    "VectorSearchResultV2",
    "build_embedding_cache_key_v2",
    "build_ann_benchmark_v2",
    "build_ann_query_membership_sha256_v2",
    "build_performance_dashboard_v2",
    "build_performance_evidence_attestation_v2",
    "build_performance_evidence_authority_v2",
    "build_scoped_cache_key_v2",
    "decide_ann_admission_v2",
    "verify_performance_evidence_v2",
]
