from __future__ import annotations

import math

import pytest

from evidence_rag.rag.performance_v2 import (
    AnnQueryObservationV2,
    CacheLayerV2,
    ContentAddressedEmbeddingCacheV2,
    EmbeddingCacheEntryV2,
    ExactMemoryVectorIndexV2,
    LayeredScopedCacheV2,
    PerformanceSpanV2,
    VectorMetadataV2,
    VectorRecordV2,
    VectorSearchFiltersV2,
    build_ann_benchmark_v2,
    build_ann_query_membership_sha256_v2,
    build_embedding_cache_key_v2,
    build_performance_dashboard_v2,
    build_performance_evidence_attestation_v2,
    build_performance_evidence_authority_v2,
    build_scoped_cache_key_v2,
    decide_ann_admission_v2,
)
from evidence_rag.rag.sources.experiment.contracts_v2 import canonical_sha256_v2


def _record(
    identity: str,
    vector: tuple[float, ...],
    *,
    project: str = "project-a",
    acl: tuple[str, ...] = ("team-a",),
    generation: str = "generation-1",
) -> VectorRecordV2:
    metadata = VectorMetadataV2(
        project_id=project,
        acl_refs=acl,
        source="code",
        entity_id=f"entity-{identity}",
        retrieval_unit_id=f"unit-{identity}",
        stable_version="commit-" + "a" * 40,
        generation=generation,
        content_sha256="sha256:" + "1" * 64,
    )
    payload = {
        "vector_id": identity,
        "vector": vector,
        "metadata": metadata.model_dump(mode="json"),
    }
    return VectorRecordV2(
        vector_id=identity,
        vector=vector,
        metadata=metadata,
        content_sha256=canonical_sha256_v2(payload),
    )


def test_exact_vector_index_filters_before_scoring_and_switches_generation() -> None:
    index = ExactMemoryVectorIndexV2()
    index.upsert(
        "multisource",
        "generation-1",
        (
            _record("visible", (1.0, 0.0)),
            _record("denied", (1.0, 0.0), acl=("team-b",)),
            _record("other-project", (1.0, 0.0), project="project-b"),
        ),
    )
    unavailable = index.search(
        "multisource",
        (1.0, 0.0),
        VectorSearchFiltersV2(project_id="project-a", acl_refs=("team-a",)),
        10,
    )
    assert unavailable.unavailable_reason == "active_generation_unavailable"

    index.activate_generation("multisource", "generation-1")
    result = index.search(
        "multisource",
        (1.0, 0.0),
        VectorSearchFiltersV2(project_id="project-a", acl_refs=("team-a",)),
        10,
    )
    assert [item.vector_id for item in result.hits] == ["visible"]
    assert result.considered == 3
    assert result.filtered == 2

    index.upsert(
        "multisource",
        "generation-2",
        (_record("new", (0.0, 1.0), generation="generation-2"),),
    )
    index.activate_generation("multisource", "generation-2")
    assert (
        index.search(
            "multisource",
            (0.0, 1.0),
            VectorSearchFiltersV2(project_id="project-a", acl_refs=("team-a",)),
            1,
        )
        .hits[0]
        .vector_id
        == "new"
    )
    with pytest.raises(ValueError, match="active generation"):
        index.delete_generation("multisource", "generation-2")
    index.delete_generation("multisource", "generation-1")


def test_vector_index_rejects_mixed_dimensions_and_nonfinite() -> None:
    index = ExactMemoryVectorIndexV2()
    with pytest.raises(ValueError, match="mixed vector dimensions"):
        index.upsert(
            "multisource",
            "generation-1",
            (_record("a", (1.0,)), _record("b", (1.0, 2.0))),
        )
    payload = _record("a", (1.0,)).model_dump(mode="json")
    payload["vector"] = [math.nan]
    payload["content_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        VectorRecordV2.model_validate(payload)


def test_embedding_and_scoped_caches_bind_versions_acl_and_generation() -> None:
    key = build_embedding_cache_key_v2(
        content_sha256="sha256:" + "a" * 64,
        retrieval_unit_builder_version="builder-v2",
        embedding_model_revision="model-r1",
        normalization_version="normalization-v2",
    )
    payload = {
        "key": key.model_dump(mode="json"),
        "vector": (0.1, 0.2),
        "dimension": 2,
        "provider": "local",
    }
    entry = EmbeddingCacheEntryV2(
        key=key,
        vector=(0.1, 0.2),
        dimension=2,
        provider="local",
        content_sha256=canonical_sha256_v2(payload),
    )
    embeddings = ContentAddressedEmbeddingCacheV2()
    embeddings.put(entry)
    assert embeddings.get(key) == entry
    assert embeddings.invalidate_model("model-r1") == 1

    one = build_scoped_cache_key_v2(
        layer=CacheLayerV2.SOURCE_RETRIEVAL,
        project_id="project-a",
        subject_authority_sha256="sha256:" + "b" * 64,
        scope_sha256="sha256:" + "c" * 64,
        active_generations={"code": "generation-1"},
        component_versions={"retriever": "v2"},
        input_sha256="sha256:" + "d" * 64,
    )
    two = one.model_copy(
        update={
            "subject_authority_sha256": "sha256:" + "e" * 64,
            "key_sha256": "sha256:" + "f" * 64,
        }
    )
    cache = LayeredScopedCacheV2()
    cache.put_keyed(one, {"ids": ["one"]})
    assert cache.get(one) == {"ids": ["one"]}
    assert cache.get(two) is None
    assert cache.invalidate(project_id="project-a") == 1


def test_dashboard_recomputes_percentiles_cost_and_error_counts() -> None:
    spans = tuple(
        PerformanceSpanV2(
            trace_id=f"trace-{(index - 1) // 2}",
            span="SOURCE_RETRIEVAL/code",
            source="code",
            latency_ms=float(index),
            candidate_count=10,
            filtered_count=1,
            token_count=2,
            cost_units=0.25,
            cache_hit=index % 2 == 0,
            error_code="timeout" if index == 20 else None,
            index_generation="generation-1",
        )
        for index in range(1, 21)
    )
    dashboard = build_performance_dashboard_v2(
        spans,
        active_generations={"code": "generation-1"},
        data_scale={"code": 1_598},
        hardware_profile="test-cpu",
        concurrency=2,
        cache_state="warm",
    )
    distribution = dict(dashboard.latency)["SOURCE_RETRIEVAL/code"]
    assert (distribution.p50, distribution.p95, distribution.p99) == (10.0, 19.0, 20.0)
    assert dashboard.trace_count == 10
    assert dashboard.cost_total == 5.0
    assert dashboard.error_counts == (("timeout", 1),)


def test_ann_decision_is_review_only_without_complete_benchmark() -> None:
    decision = decide_ann_admission_v2(
        active_vector_count=100_001,
        dense_p95_ms=650,
        concurrency_pressure=False,
        representation_pressure=False,
    )
    assert decision.decision == "REVIEW_ANN"
    assert decision.qualified is False
    observations = tuple(
        AnnQueryObservationV2(
            query_id=f"query-{index}",
            exact_top_k=("a", "b", "c"),
            candidate_top_k=("a", "b", "c"),
            latency_ms=float(index),
        )
        for index in range(1, 101)
    )
    benchmark = build_ann_benchmark_v2(
        backend="candidate-ann",
        dataset_sha256="sha256:" + "a" * 64,
        vector_count=100_001,
        dimensions=768,
        concurrency=8,
        observations=observations,
        build_time_ms=1_000,
        storage_bytes=123_456,
        provenance="isolated-reviewed-benchmark",
    )
    assert benchmark.status == "unavailable"
    assert benchmark.performance_availability == "UNAVAILABLE"
    assert "performance_attestation_missing" in str(benchmark.evidence_reason)
    admitted = decide_ann_admission_v2(
        active_vector_count=100_001,
        dense_p95_ms=650,
        concurrency_pressure=True,
        representation_pressure=False,
        benchmark=benchmark,
    )
    assert admitted.decision == "REVIEW_ANN"
    assert admitted.qualified is False

    query_membership_sha256 = build_ann_query_membership_sha256_v2(observations)
    authority = build_performance_evidence_authority_v2(
        runner_sha256="sha256:" + "1" * 64,
        component_sha256="sha256:" + "2" * 64,
        dataset_sha256="sha256:" + "a" * 64,
        query_membership_sha256=query_membership_sha256,
        source_artifact_sha256="sha256:" + "3" * 64,
    )
    attestation = build_performance_evidence_attestation_v2(
        runner_sha256=authority.runner_sha256,
        component_sha256=authority.component_sha256,
        dataset_sha256=authority.dataset_sha256,
        query_membership_sha256=authority.query_membership_sha256,
        sample_denominators={
            "build": 1,
            "latency": 100,
            "query": 100,
            "recall": 100,
            "storage": 1,
        },
        measurement_started_at="2026-07-29T00:00:00.000Z",
        measurement_completed_at="2026-07-29T00:01:00.000Z",
        measurement_window_seconds=60,
        source_artifact_sha256=authority.source_artifact_sha256,
        production_observation=True,
    )
    production_benchmark = build_ann_benchmark_v2(
        backend="candidate-ann",
        dataset_sha256=authority.dataset_sha256,
        vector_count=100_001,
        dimensions=768,
        concurrency=8,
        observations=observations,
        build_time_ms=1_000,
        storage_bytes=123_456,
        provenance="production-observation",
        evidence_attestation=attestation,
        performance_authority=authority,
    )
    assert production_benchmark.status == "available"
    assert production_benchmark.performance_availability == "AVAILABLE"
    assert (
        decide_ann_admission_v2(
            active_vector_count=100_001,
            dense_p95_ms=650,
            concurrency_pressure=True,
            representation_pressure=False,
            benchmark=production_benchmark,
        ).decision
        == "REVIEW_ANN"
    )
    admitted = decide_ann_admission_v2(
        active_vector_count=100_001,
        dense_p95_ms=650,
        concurrency_pressure=True,
        representation_pressure=False,
        benchmark=production_benchmark,
        performance_authority=authority,
    )
    assert admitted.decision == "ANN_ELIGIBLE"
    assert admitted.exact_fallback_required is True
    wrong_authority = build_performance_evidence_authority_v2(
        runner_sha256=authority.runner_sha256,
        component_sha256=authority.component_sha256,
        dataset_sha256=authority.dataset_sha256,
        query_membership_sha256=authority.query_membership_sha256,
        source_artifact_sha256="sha256:" + "9" * 64,
    )
    assert (
        decide_ann_admission_v2(
            active_vector_count=100_001,
            dense_p95_ms=650,
            concurrency_pressure=True,
            representation_pressure=False,
            benchmark=production_benchmark,
            performance_authority=wrong_authority,
        ).decision
        == "REVIEW_ANN"
    )


def test_performance_attestation_is_fail_closed_and_engineering_is_not_production() -> None:
    observations = (
        AnnQueryObservationV2(
            query_id="query-1",
            exact_top_k=("a",),
            candidate_top_k=("a",),
            latency_ms=1,
        ),
    )
    membership = build_ann_query_membership_sha256_v2(observations)
    authority = build_performance_evidence_authority_v2(
        runner_sha256="sha256:" + "1" * 64,
        component_sha256="sha256:" + "2" * 64,
        dataset_sha256="sha256:" + "3" * 64,
        query_membership_sha256=membership,
        source_artifact_sha256="sha256:" + "4" * 64,
    )
    engineering = build_performance_evidence_attestation_v2(
        runner_sha256=authority.runner_sha256,
        component_sha256=authority.component_sha256,
        dataset_sha256=authority.dataset_sha256,
        query_membership_sha256=membership,
        sample_denominators={
            "build": 1,
            "latency": 1,
            "query": 1,
            "recall": 1,
            "storage": 1,
        },
        measurement_started_at="2026-07-29T00:00:00.000Z",
        measurement_completed_at="2026-07-29T00:00:01.000Z",
        measurement_window_seconds=1,
        source_artifact_sha256=authority.source_artifact_sha256,
        production_observation=False,
    )
    benchmark = build_ann_benchmark_v2(
        backend="candidate-ann",
        dataset_sha256=authority.dataset_sha256,
        vector_count=200_000,
        dimensions=2,
        concurrency=1,
        observations=observations,
        build_time_ms=1,
        storage_bytes=1,
        provenance="isolated-synthetic",
        evidence_attestation=engineering,
        performance_authority=authority,
    )
    assert benchmark.status == "engineering"
    assert benchmark.performance_availability == "UNAVAILABLE"
    assert "engineering_evidence_only" in str(benchmark.evidence_reason)
    assert (
        decide_ann_admission_v2(
            active_vector_count=200_000,
            dense_p95_ms=600,
            concurrency_pressure=False,
            representation_pressure=False,
            benchmark=benchmark,
            performance_authority=authority,
        ).decision
        == "REVIEW_ANN"
    )

    payload = engineering.model_dump(mode="json")
    payload["source_artifact_sha256"] = "sha256:" + "9" * 64
    with pytest.raises(ValueError, match="attestation digest mismatch"):
        type(engineering).model_validate(payload)
    payload = engineering.model_dump(mode="json")
    payload["attestation_version"] = "unknown-version"
    payload["content_sha256"] = canonical_sha256_v2(
        {key: value for key, value in payload.items() if key != "content_sha256"}
    )
    with pytest.raises(ValueError):
        type(engineering).model_validate(payload)
    with pytest.raises(ValueError, match="positive integers"):
        build_performance_evidence_attestation_v2(
            runner_sha256=authority.runner_sha256,
            component_sha256=authority.component_sha256,
            dataset_sha256=authority.dataset_sha256,
            query_membership_sha256=membership,
            sample_denominators={
                "build": 1,
                "latency": 0,
                "query": 1,
                "recall": 1,
                "storage": 1,
            },
            measurement_started_at="2026-07-29T00:00:00.000Z",
            measurement_completed_at="2026-07-29T00:00:01.000Z",
            measurement_window_seconds=1,
            source_artifact_sha256=authority.source_artifact_sha256,
            production_observation=True,
        )
    with pytest.raises(ValueError, match="incomplete"):
        build_performance_evidence_attestation_v2(
            runner_sha256=authority.runner_sha256,
            component_sha256=authority.component_sha256,
            dataset_sha256=authority.dataset_sha256,
            query_membership_sha256=membership,
            sample_denominators={
                "latency": 1,
                "query": 1,
                "recall": 1,
                "storage": 1,
            },
            measurement_started_at="2026-07-29T00:00:00.000Z",
            measurement_completed_at="2026-07-29T00:00:01.000Z",
            measurement_window_seconds=1,
            source_artifact_sha256=authority.source_artifact_sha256,
            production_observation=True,
        )
    with pytest.raises(ValueError):
        build_performance_evidence_attestation_v2(
            runner_sha256=authority.runner_sha256,
            component_sha256=authority.component_sha256,
            dataset_sha256=authority.dataset_sha256,
            query_membership_sha256=membership,
            sample_denominators={
                "build": 1,
                "latency": 1,
                "query": 1,
                "recall": 1,
                "storage": 1,
            },
            measurement_started_at="2026-07-29T00:00:00.000Z",
            measurement_completed_at="2026-07-29T00:00:01.000Z",
            measurement_window_seconds=math.nan,
            source_artifact_sha256=authority.source_artifact_sha256,
            production_observation=True,
        )
