from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.rag.multisource_foundation_v2 import MultiSourceCandidateV2
from evidence_rag.rag.wiki.compiler_v1 import (
    WIKI_COMPILER_AUTHORITY_SHA256,
    WikiCompilerDiagnosticCodeV1,
    WikiCompilerError,
    build_wiki_compilation_request_v1,
    compile_wiki_incremental_v1,
    compile_wiki_v1,
)
from evidence_rag.rag.wiki.contracts_v1 import (
    WikiDirectoryV1,
    WikiPageFragmentV1,
    WikiSourceDomainV1,
    build_source_generation_v1,
    canonical_sha256_v1,
)
from evidence_rag.rag.wiki.store_v1 import WikiStoreV1


def _source_generations(version: int):
    return tuple(
        build_source_generation_v1(
            source=source,
            generation_id=f"source-{source.value}-generation-{version}",
            watermark=f"source-{source.value}-watermark-{version}",
        )
        for source in WikiSourceDomainV1
    )


def _candidate(
    source: WikiSourceDomainV1,
    *,
    version: int = 1,
    acl_ref: str = "team-a",
    title: str | None = None,
    snippet: str | None = None,
    counter_evidence: bool = False,
) -> MultiSourceCandidateV2:
    ordinal = tuple(WikiSourceDomainV1).index(source) + 1
    return MultiSourceCandidateV2(
        candidate_id=f"candidate-{source.value}-{ordinal:02d}",
        entity_id=f"entity-{source.value}-{ordinal:02d}",
        retrieval_unit_id=f"unit-{source.value}-{ordinal:02d}",
        parent_entity_id=None,
        source_instance=f"{source.value}-primary",
        retrieval_domain=source.value,
        fact_type=f"{source.value}.fact",
        entity_type=f"{source.value}.ReviewedEntity",
        task="compare" if source is WikiSourceDomainV1.EXPERIMENT else "current",
        title=title or f"Reviewed {source.value} evidence",
        snippet=snippet or f"Portable governed {source.value} evidence for Wiki compilation.",
        locator=f"{source.value}://project-wiki-compiler/entity/{ordinal}",
        stable_version=f"v{version}",
        source_generation=f"source-{source.value}-generation-{version}",
        raw_or_derived="raw_fact",
        derivation="production_adapter",
        review_status="reviewed",
        fact_status="active",
        channel_scores=(("exact", 1.0), ("sparse", 0.8)),
        calibrated_relevance=0.9,
        calibration_version="calibration-v1",
        matched_roles=("implementation", "validation") if ordinal % 2 else ("validation",),
        authority=0.95,
        version_alignment="exact",
        acl_ref=acl_ref,
        token_estimate=64,
        root_provenance=f"production-{source.value}-adapter",
        counter_evidence=counter_evidence,
    )


def _request(
    *,
    version: int = 1,
    candidates: tuple[MultiSourceCandidateV2, ...] | None = None,
):
    candidates = candidates or tuple(
        _candidate(source, version=version) for source in WikiSourceDomainV1
    )
    return build_wiki_compilation_request_v1(
        request_id=f"wiki-compile-request-{version}",
        project_id="project-wiki-compiler",
        generation_id=f"wiki-generation-{version}",
        source_generations=_source_generations(version),
        candidates=tuple(sorted(candidates, key=lambda item: item.candidate_id)),
        observed_at=f"2026-08-0{version}T00:00:00Z",
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
    )


def test_compiler_builds_deterministic_cross_source_pages_directories_and_dependencies() -> None:
    request = _request()
    first = compile_wiki_v1(request)
    second = compile_wiki_v1(request)
    assert first == second
    assert first.compiled_candidate_count == 6
    assert first.quarantined_candidate_count == 0
    assert first.manifest.compiler_authority_sha256 == WIKI_COMPILER_AUTHORITY_SHA256
    pages = tuple(item for item in first.records if isinstance(item, WikiPageFragmentV1))
    directories = tuple(item for item in first.records if isinstance(item, WikiDirectoryV1))
    assert len(directories) == 17  # 11 intent roots + six source-domain directories
    assert {item.page_type.value for item in pages}.issuperset(
        {"source", "component", "capability", "experiment"}
    )
    assert any(len(page.source_refs) >= 2 for page in pages if page.page_type.value == "capability")
    assert all(page.facts and page.source_refs for page in pages)
    assert first.dependency_index.source_to_pages
    assert first.dependency_index.page_to_sources
    assert first.content_sha256 == canonical_sha256_v1(
        first.model_dump(mode="json", exclude={"content_sha256"})
    )


def test_compiler_output_is_directly_stageable_in_isolated_store(tmp_path: Path) -> None:
    result = compile_wiki_v1(_request())
    store = WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path)
    store.stage_generation(result.manifest, result.records)
    verified = store.verify_generation(
        project_id=result.manifest.project_id,
        generation_id=result.manifest.generation_id,
        expected_manifest_sha256=result.manifest.content_sha256,
    )
    assert verified == result.manifest
    assert store.active_manifest(result.manifest.project_id) is None


def test_compiler_partitions_acl_before_page_merge_and_never_cross_links() -> None:
    candidates = tuple(_candidate(source) for source in WikiSourceDomainV1) + (
        _candidate(
            WikiSourceDomainV1.CODE,
            acl_ref="team-b",
            title="Private reviewed code evidence",
            snippet="Private but portable governed evidence.",
        ).model_copy(update={"candidate_id": "candidate-code-private"}),
    )
    result = compile_wiki_v1(_request(candidates=candidates))
    assert len(result.manifest.visibility_partitions) == 2
    pages = tuple(item for item in result.records if isinstance(item, WikiPageFragmentV1))
    for page in pages:
        assert all(page.scope.permits(source_ref.acl_refs) for source_ref in page.source_refs)
        assert {source_ref.acl_refs for source_ref in page.source_refs} == {(page.scope.acl_refs)}
    by_partition = {
        partition: {
            page.logical_path for page in pages if page.scope.visibility_partition == partition
        }
        for partition in result.manifest.visibility_partitions
    }
    assert all(paths for paths in by_partition.values())


def test_compiler_quarantines_full_unsafe_content_without_leaking_it_to_pages() -> None:
    secret = "api_key=abcdefghijklmnop"
    unsafe = _candidate(
        WikiSourceDomainV1.CODE,
        snippet=f"Unsafe payload {secret}",
    )
    safe = tuple(
        _candidate(source) for source in WikiSourceDomainV1 if source is not WikiSourceDomainV1.CODE
    )
    result = compile_wiki_v1(_request(candidates=(unsafe, *safe)))
    assert result.input_candidate_count == 6
    assert result.compiled_candidate_count == 5
    assert result.quarantined_candidate_count == 1
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code is WikiCompilerDiagnosticCodeV1.UNSAFE_CONTENT
    assert len(result.error_book) == 1 and result.error_book[0].occurrences == 1
    serialized = canonical_sha256_v1(result.model_dump(mode="json"))
    assert serialized.startswith("sha256:")
    assert secret not in str(result.model_dump(mode="json"))


def test_error_book_persists_patterns_across_generations_without_raw_content() -> None:
    first_candidates = tuple(
        _candidate(source, snippet="api_key=abcdefghijklmnop")
        if source is WikiSourceDomainV1.CODE
        else _candidate(source)
        for source in WikiSourceDomainV1
    )
    first = compile_wiki_v1(_request(candidates=first_candidates))
    second_candidates = tuple(
        _candidate(source, version=2, snippet="api_key=abcdefghijklmnop")
        if source is WikiSourceDomainV1.CODE
        else _candidate(source, version=2)
        for source in WikiSourceDomainV1
    )
    second = compile_wiki_v1(
        _request(version=2, candidates=second_candidates),
        previous_error_book=first.error_book,
    )
    assert len(second.error_book) == 1
    assert second.error_book[0].occurrences == 2
    assert second.error_book[0].first_seen_generation == "wiki-generation-1"
    assert second.error_book[0].last_seen_generation == "wiki-generation-2"


def test_compilation_request_fails_before_compile_on_generation_or_authority_tamper() -> None:
    candidate = _candidate(WikiSourceDomainV1.CODE)
    mismatched = candidate.model_copy(update={"source_generation": "unknown-generation"})
    candidates = (mismatched,) + tuple(
        _candidate(source) for source in WikiSourceDomainV1 if source is not WikiSourceDomainV1.CODE
    )
    with pytest.raises(ValidationError, match="source generation"):
        _request(candidates=candidates)
    request = _request()
    with pytest.raises(ValidationError, match="digest mismatch"):
        request.model_copy(update={"project_id": "forged-project"})
    with pytest.raises((ValidationError, WikiCompilerError)):
        build_wiki_compilation_request_v1(
            **request.model_dump(
                mode="python",
                exclude={"content_sha256", "compiler_authority_sha256"},
            ),
            compiler_authority_sha256="sha256:" + "0" * 64,
        )


def test_incremental_compiler_reuses_unaffected_content_and_matches_clean_rebuild() -> None:
    previous_request = _request()
    previous = compile_wiki_v1(previous_request)
    source_generations = list(_source_generations(1))
    source_generations[0] = build_source_generation_v1(
        source=WikiSourceDomainV1.CODE,
        generation_id="source-code-generation-2",
        watermark="source-code-watermark-2",
    )
    next_candidates = tuple(
        _candidate(source, version=2) if source is WikiSourceDomainV1.CODE else _candidate(source)
        for source in WikiSourceDomainV1
    )
    request = build_wiki_compilation_request_v1(
        request_id="wiki-compile-request-incremental-2",
        project_id="project-wiki-compiler",
        generation_id="wiki-generation-2",
        source_generations=tuple(source_generations),
        candidates=tuple(sorted(next_candidates, key=lambda item: item.candidate_id)),
        observed_at="2026-08-01T00:00:00Z",
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
    )
    incremental = compile_wiki_incremental_v1(
        previous_request=previous_request,
        previous_result=previous,
        request=request,
        changed_candidate_ids=("candidate-code-01",),
    )
    assert incremental.full_rebuild_equivalent is True
    assert incremental.result == compile_wiki_v1(
        request,
        previous_error_book=previous.error_book,
    )
    assert incremental.reused_records
    assert incremental.rebuilt_records
    assert any(path.startswith("/sources/code/") for path in incremental.affected_page_paths)
    with pytest.raises(WikiCompilerError, match="declared incremental changes"):
        compile_wiki_incremental_v1(
            previous_request=previous_request,
            previous_result=previous,
            request=request,
            changed_candidate_ids=(),
        )
