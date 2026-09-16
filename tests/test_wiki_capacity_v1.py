from __future__ import annotations

import shutil
from collections import OrderedDict
from pathlib import Path

import pytest

from evidence_rag.rag.multisource_foundation_v2 import MultiSourceCandidateV2
from evidence_rag.rag.wiki.capacity_runner_v1 import (
    WikiSBenchmarkConfigV1,
    run_wiki_s_capacity_benchmark_v1,
)
from evidence_rag.rag.wiki.capacity_v1 import (
    WIKI_CAPACITY_FILES,
    WikiCapacityError,
    WikiCapacityObservationV1,
    WikiCapacityOperationV1,
    measure_wiki_capacity_v1,
    verify_wiki_capacity_artifact_v1,
    write_wiki_capacity_artifact_v1,
)
from evidence_rag.rag.wiki.compiler_v1 import (
    WIKI_COMPILER_AUTHORITY_SHA256,
    build_wiki_compilation_request_v1,
    compile_wiki_v1,
)
from evidence_rag.rag.wiki.contracts_v1 import (
    WikiPageFragmentV1,
    WikiSourceDomainV1,
    build_source_generation_v1,
    canonical_sha256_v1,
)
from evidence_rag.rag.wiki.navigator_v1 import build_wiki_navigation_request_v1
from evidence_rag.rag.wiki.runtime_v1 import WikiRuntimeV1
from evidence_rag.rag.wiki.search_v1 import WikiQueryClassV1
from evidence_rag.rag.wiki.store_v1 import WikiStoreV1


def _deterministic_clock(step_ns: int = 1_000_000):
    value = 0

    def clock() -> int:
        nonlocal value
        value += step_ns
        return value

    return clock


def _all_operations():
    return OrderedDict(
        (
            operation,
            lambda operation=operation: WikiCapacityObservationV1(
                result_count=1,
                work_units=20 if operation is WikiCapacityOperationV1.COMPILE else 0,
            ),
        )
        for operation in WikiCapacityOperationV1
    )


def test_capacity_contract_builds_complete_report_and_portable_tamper_evident_artifact(
    tmp_path: Path,
) -> None:
    report, samples = measure_wiki_capacity_v1(
        benchmark_id="wiki-capacity-contract-v1",
        project_id="project-wiki-capacity",
        operations=_all_operations(),
        wiki_page_count=1_000,
        source_ref_count=4_000,
        database_bytes=1_024,
        warmup_iterations=1,
        measured_iterations=5,
        clock_ns=_deterministic_clock(),
    )
    assert report.complete_operation_set is True
    assert report.all_slo_passed is True, [
        (item.operation.value, item.p95_ms, item.target_p95_ms, item.success_count)
        for item in report.metrics
        if not item.meets_slo
    ]
    assert report.qualification == "ENGINEERING_S_CAPACITY_EVIDENCE"
    assert len(samples) == len(WikiCapacityOperationV1) * 5

    artifact = tmp_path / "capacity-artifact"
    verification = write_wiki_capacity_artifact_v1(
        report=report,
        samples=samples,
        output_dir=artifact,
        isolated_root=tmp_path,
    )
    assert verification.status == "VERIFIED_ENGINEERING_CAPACITY"
    assert tuple(sorted(item.name for item in artifact.iterdir())) == tuple(
        sorted(WIKI_CAPACITY_FILES)
    )
    copied = tmp_path / "portable-copy"
    shutil.copytree(artifact, copied)
    assert verify_wiki_capacity_artifact_v1(copied) == verification
    (copied / "report.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(WikiCapacityError, match="checksum"):
        verify_wiki_capacity_artifact_v1(copied)

    unsafe_report, unsafe_samples = measure_wiki_capacity_v1(
        benchmark_id="wiki-capacity-unsafe-v1",
        project_id="/private/tmp/not-portable",
        operations=_all_operations(),
        wiki_page_count=10,
        source_ref_count=10,
        database_bytes=0,
        warmup_iterations=0,
        measured_iterations=1,
        clock_ns=_deterministic_clock(),
    )
    with pytest.raises(WikiCapacityError, match="unsafe string"):
        write_wiki_capacity_artifact_v1(
            report=unsafe_report,
            samples=unsafe_samples,
            output_dir=tmp_path / "unsafe-artifact",
            isolated_root=tmp_path,
        )


def _candidates(version: int, count: int = 180) -> tuple[MultiSourceCandidateV2, ...]:
    sources = tuple(WikiSourceDomainV1)
    roles = ("architecture", "baseline", "decision", "implementation", "metric", "validation")
    candidates = []
    for index in range(1, count + 1):
        source = sources[(index - 1) % len(sources)]
        role = roles[(index - 1) % len(roles)]
        candidates.append(
            MultiSourceCandidateV2(
                candidate_id=f"candidate-capacity-{index:04d}",
                entity_id=f"entity-capacity-{index:04d}",
                retrieval_unit_id=f"unit-capacity-{index:04d}",
                parent_entity_id=None,
                source_instance=f"capacity-{source.value}",
                retrieval_domain=source.value,
                fact_type=f"{source.value}.capacity",
                entity_type=f"{source.value}.CapacityEvidence",
                task="compare" if source is WikiSourceDomainV1.EXPERIMENT else "current",
                title=f"Capacity {role} evidence {index:04d}",
                snippet=(
                    f"Reviewed capacity {role} evidence {index:04d} for hybrid Wiki navigation."
                ),
                locator=f"{source.value}://project-wiki-capacity/entity/{index:04d}",
                stable_version=f"v{version}",
                source_generation=f"capacity-{source.value}-generation-{version}",
                raw_or_derived="raw_fact",
                derivation="production_adapter",
                review_status="reviewed",
                fact_status="active",
                channel_scores=(("exact", 1.0), ("sparse", 0.8)),
                calibrated_relevance=0.9,
                calibration_version="capacity-calibration-v1",
                matched_roles=(role,),
                authority=0.95,
                version_alignment="exact",
                acl_ref="team-a",
                token_estimate=24,
                root_provenance=f"production-{source.value}-adapter",
            )
        )
    return tuple(candidates)


def _compilation_request(version: int):
    candidates = _candidates(version)
    return build_wiki_compilation_request_v1(
        request_id=f"wiki-capacity-compile-{version}",
        project_id="project-wiki-capacity",
        generation_id=f"wiki-capacity-generation-{version}",
        source_generations=tuple(
            build_source_generation_v1(
                source=source,
                generation_id=f"capacity-{source.value}-generation-{version}",
                watermark=f"capacity-{source.value}-watermark-{version}",
            )
            for source in WikiSourceDomainV1
        ),
        candidates=candidates,
        observed_at=f"2026-08-0{version}T00:00:00Z",
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
    )


def test_real_isolated_s_layer_capacity_covers_read_search_navigation_compile_publish_rollback(
    tmp_path: Path,
) -> None:
    request_one = _compilation_request(1)
    alternate_requests = tuple(_compilation_request(version) for version in range(2, 7))
    first = compile_wiki_v1(request_one)
    alternates = tuple(compile_wiki_v1(request) for request in alternate_requests)
    store = WikiStoreV1(tmp_path / "wiki-capacity.sqlite3", isolated_root=tmp_path)
    runtime = WikiRuntimeV1(store=store)
    store.stage_generation(
        first.manifest,
        first.records,
        candidates=request_one.candidates,
        error_book=first.error_book,
        compilation_result=first,
    )
    for request, result in zip(alternate_requests, alternates, strict=True):
        store.stage_generation(
            result.manifest,
            result.records,
            candidates=request.candidates,
            error_book=result.error_book,
            compilation_result=result,
        )
    runtime.publish_generation(
        project_id=first.manifest.project_id,
        generation_id=first.manifest.generation_id,
        expected_manifest_sha256=first.manifest.content_sha256,
        reviewer_authority_sha256=canonical_sha256_v1("capacity-reviewer"),
        published_at="2026-08-03T10:00:00Z",
    )
    pages = tuple(item for item in first.records if isinstance(item, WikiPageFragmentV1))
    path = pages[0].logical_path

    def path_get() -> WikiCapacityObservationV1:
        value = runtime.read(
            project_id=first.manifest.project_id,
            requester_acl_refs=("team-a",),
            logical_path=path,
        )
        return WikiCapacityObservationV1(result_count=int(value is not None))

    def directory_list() -> WikiCapacityObservationV1:
        directory = runtime.read(
            project_id=first.manifest.project_id,
            requester_acl_refs=("team-a",),
            logical_path="/architecture",
        )
        assert directory is not None
        return WikiCapacityObservationV1(
            result_count=len(directory.page_paths),
            work_units=len(directory.child_paths) + len(directory.page_paths),
        )

    def search() -> WikiCapacityObservationV1:
        response = runtime.search(
            request_id="wiki-capacity-search",
            project_id=first.manifest.project_id,
            requester_acl_refs=("team-a",),
            query="capacity implementation validation evidence",
            top_k=12,
        )
        return WikiCapacityObservationV1(
            result_count=len(response.hits), work_units=response.scanned_pages
        )

    def navigate(query_class: WikiQueryClassV1, roles: tuple[str, ...]):
        request = build_wiki_navigation_request_v1(
            request_id=f"wiki-capacity-navigation-{query_class.value}",
            project_id=first.manifest.project_id,
            requester_acl_refs=("team-a",),
            query="capacity architecture implementation validation evidence",
            query_class=query_class,
            required_roles=roles,
            required_sources=(),
            as_of=None,
            max_searches=8,
            max_page_reads=48,
            max_link_hops=3,
            max_raw_reads=16,
            empty_search_patience=2,
            retrieval_deadline_ms=4_000,
            top_k_per_search=8,
            token_budget=8_000,
            require_raw_evidence=True,
        )
        response = runtime.navigate(request)
        return WikiCapacityObservationV1(
            result_count=len(response.selected_page_paths),
            work_units=response.page_read_count + response.raw_read_count,
        )

    def compile_operation() -> WikiCapacityObservationV1:
        compiled = compile_wiki_v1(alternate_requests[0])
        return WikiCapacityObservationV1(
            result_count=len(compiled.records), work_units=compiled.compiled_candidate_count
        )

    publish_index = 0
    active_alternate = alternates[0]

    def publish() -> WikiCapacityObservationV1:
        nonlocal publish_index, active_alternate
        active_alternate = alternates[publish_index]
        publish_index += 1
        runtime.publish_generation(
            project_id=active_alternate.manifest.project_id,
            generation_id=active_alternate.manifest.generation_id,
            expected_manifest_sha256=active_alternate.manifest.content_sha256,
            reviewer_authority_sha256=canonical_sha256_v1("capacity-publish-reviewer"),
            published_at="2026-08-03T10:01:00Z",
        )
        return WikiCapacityObservationV1(result_count=1)

    def rollback() -> WikiCapacityObservationV1:
        runtime.rollback_generation(
            project_id=first.manifest.project_id,
            target_generation_id=first.manifest.generation_id,
            expected_target_manifest_sha256=first.manifest.content_sha256,
            expected_active_generation_id=active_alternate.manifest.generation_id,
            expected_active_manifest_sha256=active_alternate.manifest.content_sha256,
            reviewer_authority_sha256=canonical_sha256_v1("capacity-rollback-reviewer"),
            rolled_back_at="2026-08-03T10:02:00Z",
        )
        return WikiCapacityObservationV1(result_count=1)

    operations = OrderedDict(
        (
            (WikiCapacityOperationV1.PATH_GET, path_get),
            (WikiCapacityOperationV1.DIRECTORY_LIST, directory_list),
            (WikiCapacityOperationV1.HYBRID_SEARCH, search),
            (
                WikiCapacityOperationV1.STANDARD_NAVIGATION,
                lambda: navigate(WikiQueryClassV1.LOCAL_DETAIL, ("implementation",)),
            ),
            (
                WikiCapacityOperationV1.DEEP_NAVIGATION,
                lambda: navigate(
                    WikiQueryClassV1.GLOBAL,
                    ("architecture", "implementation", "validation"),
                ),
            ),
            (WikiCapacityOperationV1.COMPILE, compile_operation),
            (WikiCapacityOperationV1.PUBLISH, publish),
            (WikiCapacityOperationV1.ROLLBACK, rollback),
        )
    )
    report, samples = measure_wiki_capacity_v1(
        benchmark_id="wiki-real-s-capacity-v1",
        project_id=first.manifest.project_id,
        operations=operations,
        wiki_page_count=len(pages),
        source_ref_count=sum(len(item.source_refs) for item in pages),
        database_bytes=(tmp_path / "wiki-capacity.sqlite3").stat().st_size,
        warmup_iterations=1,
        measured_iterations=3,
    )
    assert report.complete_operation_set is True
    assert report.all_slo_passed is True, [
        (item.operation.value, item.p95_ms, item.target_p95_ms, item.success_count)
        for item in report.metrics
        if not item.meets_slo
    ]
    assert report.qualification == "ENGINEERING_S_CAPACITY_EVIDENCE"
    assert next(
        item for item in report.metrics if item.operation is WikiCapacityOperationV1.COMPILE
    ).throughput_per_second
    artifact = tmp_path / "real-capacity-artifact"
    verification = write_wiki_capacity_artifact_v1(
        report=report,
        samples=samples,
        output_dir=artifact,
        isolated_root=tmp_path,
    )
    assert verification.status == "VERIFIED_ENGINEERING_CAPACITY"
    assert runtime.store.active_manifest(first.manifest.project_id) == first.manifest


def test_repeatable_capacity_runner_publishes_one_verified_portable_artifact(
    tmp_path: Path,
) -> None:
    result = run_wiki_s_capacity_benchmark_v1(
        config=WikiSBenchmarkConfigV1(
            benchmark_id="wiki-s-runner-smoke-v1",
            candidate_count=60,
            warmup_iterations=0,
            measured_iterations=3,
        ),
        output_dir=tmp_path / "runner-artifact",
    )
    assert result.report.qualification == "ENGINEERING_S_CAPACITY_EVIDENCE"
    assert result.verification.status == "VERIFIED_ENGINEERING_CAPACITY"
    assert result.portable_copy_verified is True
