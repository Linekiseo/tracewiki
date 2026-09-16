"""Repeatable isolated S-layer Wiki capacity benchmark runner."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from collections import OrderedDict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..multisource_foundation_v2 import MultiSourceCandidateV2
from .capacity_v1 import (
    WikiCapacityArtifactVerificationV1,
    WikiCapacityObservationV1,
    WikiCapacityOperationV1,
    WikiCapacityReportV1,
    measure_wiki_capacity_v1,
    verify_wiki_capacity_artifact_v1,
    write_wiki_capacity_artifact_v1,
)
from .compiler_v1 import (
    WIKI_COMPILER_AUTHORITY_SHA256,
    build_wiki_compilation_request_v1,
    compile_wiki_v1,
)
from .contracts_v1 import (
    WikiPageFragmentV1,
    WikiSourceDomainV1,
    build_source_generation_v1,
    canonical_sha256_v1,
)
from .navigator_v1 import build_wiki_navigation_request_v1
from .runtime_v1 import WikiRuntimeV1
from .search_v1 import WikiQueryClassV1
from .store_v1 import WikiStoreV1


class WikiSBenchmarkConfigV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_id: str = Field(pattern=r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}")
    candidate_count: int = Field(default=300, ge=60, le=10_000)
    warmup_iterations: int = Field(default=1, ge=0, le=10)
    measured_iterations: int = Field(default=5, ge=3, le=50)


class WikiSBenchmarkResultV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report: WikiCapacityReportV1
    verification: WikiCapacityArtifactVerificationV1
    portable_copy_verified: bool


def _candidates(version: int, count: int) -> tuple[MultiSourceCandidateV2, ...]:
    sources = tuple(WikiSourceDomainV1)
    roles = ("architecture", "baseline", "decision", "implementation", "metric", "validation")
    return tuple(
        MultiSourceCandidateV2(
            candidate_id=f"candidate-capacity-{index:05d}",
            entity_id=f"entity-capacity-{index:05d}",
            retrieval_unit_id=f"unit-capacity-{index:05d}",
            parent_entity_id=None,
            source_instance=f"capacity-{sources[(index - 1) % len(sources)].value}",
            retrieval_domain=sources[(index - 1) % len(sources)].value,
            fact_type=f"{sources[(index - 1) % len(sources)].value}.capacity",
            entity_type=f"{sources[(index - 1) % len(sources)].value}.CapacityEvidence",
            task=(
                "compare"
                if sources[(index - 1) % len(sources)] is WikiSourceDomainV1.EXPERIMENT
                else "current"
            ),
            title=f"Capacity {roles[(index - 1) % len(roles)]} evidence {index:05d}",
            snippet=(
                f"Reviewed capacity {roles[(index - 1) % len(roles)]} evidence "
                f"{index:05d} for hybrid Wiki navigation."
            ),
            locator=(
                f"{sources[(index - 1) % len(sources)].value}://"
                f"project-wiki-capacity/entity/{index:05d}"
            ),
            stable_version=f"v{version}",
            source_generation=(
                f"capacity-{sources[(index - 1) % len(sources)].value}-generation-{version}"
            ),
            raw_or_derived="raw_fact",
            derivation="production_adapter",
            review_status="reviewed",
            fact_status="active",
            channel_scores=(("exact", 1.0), ("sparse", 0.8)),
            calibrated_relevance=0.9,
            calibration_version="capacity-calibration-v1",
            matched_roles=(roles[(index - 1) % len(roles)],),
            authority=0.95,
            version_alignment="exact",
            acl_ref="team-a",
            token_estimate=24,
            root_provenance=(f"production-{sources[(index - 1) % len(sources)].value}-adapter"),
        )
        for index in range(1, count + 1)
    )


def _request(version: int, candidate_count: int):
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
        candidates=_candidates(version, candidate_count),
        observed_at=f"2026-08-03T00:{version // 60:02d}:{version % 60:02d}Z",
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
    )


def run_wiki_s_capacity_benchmark_v1(
    *,
    config: WikiSBenchmarkConfigV1,
    output_dir: Path,
) -> WikiSBenchmarkResultV1:
    """Execute the complete S-layer workload without touching the service-owned DB."""

    alternate_count = config.warmup_iterations + config.measured_iterations + 1
    with tempfile.TemporaryDirectory(prefix="evidence-rag-wiki-capacity-") as temporary:
        work_root = Path(temporary)
        requests = tuple(
            _request(version, config.candidate_count) for version in range(1, alternate_count + 2)
        )
        results = tuple(compile_wiki_v1(request) for request in requests)
        first = results[0]
        alternates = results[1:]
        store_path = work_root / "wiki-capacity.sqlite3"
        runtime = WikiRuntimeV1(store=WikiStoreV1(store_path, isolated_root=work_root))
        for request, result in zip(requests, results, strict=True):
            runtime.store.stage_generation(
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
            if directory is None:
                raise RuntimeError("reviewed architecture directory is unavailable")
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
            compiled = compile_wiki_v1(requests[1])
            return WikiCapacityObservationV1(
                result_count=len(compiled.records),
                work_units=compiled.compiled_candidate_count,
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
            benchmark_id=config.benchmark_id,
            project_id=first.manifest.project_id,
            operations=operations,
            wiki_page_count=len(pages),
            source_ref_count=sum(len(item.source_refs) for item in pages),
            database_bytes=store_path.stat().st_size,
            warmup_iterations=config.warmup_iterations,
            measured_iterations=config.measured_iterations,
        )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        verification = write_wiki_capacity_artifact_v1(
            report=report,
            samples=samples,
            output_dir=output_dir,
            isolated_root=output_dir.parent,
        )
        portable_copy = work_root / "portable-capacity-copy"
        shutil.copytree(output_dir, portable_copy)
        copied_verification = verify_wiki_capacity_artifact_v1(portable_copy)
        return WikiSBenchmarkResultV1(
            report=report,
            verification=verification,
            portable_copy_verified=copied_verification == verification,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--benchmark-id", default="wiki-s-capacity-20260803-v1")
    parser.add_argument("--candidate-count", type=int, default=300)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--iterations", type=int, default=5)
    arguments = parser.parse_args()
    result = run_wiki_s_capacity_benchmark_v1(
        config=WikiSBenchmarkConfigV1(
            benchmark_id=arguments.benchmark_id,
            candidate_count=arguments.candidate_count,
            warmup_iterations=arguments.warmups,
            measured_iterations=arguments.iterations,
        ),
        output_dir=arguments.output,
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
