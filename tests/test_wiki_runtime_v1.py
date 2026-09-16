from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.rag.multisource_foundation_v2 import MultiSourceCandidateV2
from evidence_rag.rag.wiki.builder_v1 import (
    WikiBuilderDecisionStatusV1,
    WikiPatchActionV1,
    WikiPatchOriginV1,
    build_wiki_builder_patch_v1,
    build_wiki_builder_trial_v1,
    build_wiki_patch_operation_v1,
    build_wiki_query_outcome_v1,
)
from evidence_rag.rag.wiki.compiler_v1 import (
    WIKI_COMPILER_AUTHORITY_SHA256,
    build_wiki_compilation_request_v1,
)
from evidence_rag.rag.wiki.contracts_v1 import (
    WikiPageFragmentV1,
    WikiSourceDomainV1,
    build_source_generation_v1,
    build_wiki_page_fragment_v1,
    canonical_sha256_v1,
)
from evidence_rag.rag.wiki.navigator_v1 import WikiNavigationStopReasonV1
from evidence_rag.rag.wiki.runtime_v1 import WikiRuntimeError, WikiRuntimeV1
from evidence_rag.rag.wiki.store_v1 import WikiPublicationError, WikiStoreV1


def _candidate() -> MultiSourceCandidateV2:
    return MultiSourceCandidateV2(
        candidate_id="candidate-code-runtime",
        entity_id="entity-code-runtime",
        retrieval_unit_id="unit-code-runtime",
        parent_entity_id=None,
        source_instance="project-runtime:code",
        retrieval_domain="code",
        fact_type="code.implementation",
        entity_type="code.CodeSymbol",
        task="implementation",
        title="Wiki runtime navigation",
        snippet="Reviewed Wiki runtime implementation with raw verification evidence.",
        locator="code://project-wiki-runtime/symbol/wiki-runtime",
        stable_version="commit-runtime-v1",
        source_generation="source-code-runtime-v1",
        raw_or_derived="raw_fact",
        derivation="production_adapter",
        review_status="reviewed",
        fact_status="active",
        channel_scores=(("exact", 1.0),),
        calibrated_relevance=0.96,
        calibration_version="reviewed-calibration-v1",
        matched_roles=("implementation",),
        authority=1.0,
        version_alignment="exact",
        acl_ref="team-a",
        token_estimate=32,
        root_provenance="production-code-runtime",
    )


def _request():
    generations = tuple(
        build_source_generation_v1(
            source=source,
            generation_id=(
                "source-code-runtime-v1"
                if source is WikiSourceDomainV1.CODE
                else f"source-{source.value}-runtime-v1"
            ),
            watermark=f"watermark-{source.value}-runtime-v1",
        )
        for source in WikiSourceDomainV1
    )
    return build_wiki_compilation_request_v1(
        request_id="wiki-runtime-compile-1",
        project_id="project-wiki-runtime",
        generation_id="wiki-runtime-generation-1",
        source_generations=generations,
        candidates=(_candidate(),),
        observed_at="2026-08-03T04:00:00Z",
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
    )


def _runtime(tmp_path: Path) -> WikiRuntimeV1:
    store = WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path)
    return WikiRuntimeV1(store=store)


def _outcome(query_id: str, *, complete: bool, numerator: int):
    return build_wiki_query_outcome_v1(
        query_id=query_id,
        evidence_complete=complete,
        obligation_numerator=numerator,
        obligation_denominator=1,
        raw_verify_numerator=numerator,
        raw_verify_denominator=1,
        acl_leakage_count=0,
        unsupported_claim_count=0,
        navigation_steps=4,
        token_count=256,
        latency_ms=25.0,
    )


def test_runtime_persists_source_authority_and_serves_search_read_navigation(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    result = runtime.compile_to_staging(_request())
    assert (
        runtime.status(
            project_id=result.manifest.project_id, requester_acl_refs=("team-a",)
        ).availability
        == "UNAVAILABLE"
    )
    runtime.publish_generation(
        project_id=result.manifest.project_id,
        generation_id=result.manifest.generation_id,
        expected_manifest_sha256=result.manifest.content_sha256,
        reviewer_authority_sha256=canonical_sha256_v1("human-reviewer"),
        published_at="2026-08-03T04:10:00Z",
    )
    restarted = WikiRuntimeV1(store=WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path))
    status = restarted.status(
        project_id=result.manifest.project_id,
        requester_acl_refs=("team-a",),
    )
    assert status.availability == "AVAILABLE"
    assert status.visible_page_count > 0
    response = restarted.search(
        request_id="wiki-runtime-search-1",
        project_id=result.manifest.project_id,
        requester_acl_refs=("team-a",),
        query="runtime implementation raw verification",
    )
    assert response.hits
    assert (
        restarted.read(
            project_id=result.manifest.project_id,
            requester_acl_refs=("team-a",),
            logical_path=response.hits[0].logical_path,
        )
        is not None
    )
    request = restarted.build_navigation_request(
        request_id="wiki-runtime-navigate-1",
        project_id=result.manifest.project_id,
        requester_acl_refs=("team-a",),
        query="runtime implementation",
        required_roles=("implementation",),
        required_sources=(WikiSourceDomainV1.CODE,),
        max_searches=3,
        max_page_reads=16,
        max_link_hops=2,
        top_k_per_search=8,
        token_budget=2_000,
        require_raw_evidence=True,
    )
    navigation = restarted.navigate(request)
    assert navigation.stop_reason is WikiNavigationStopReasonV1.EVIDENCE_COMPLETE
    assert navigation.raw_verify_count >= 1
    assert navigation.evidence_pack.citations
    assert (
        restarted.list_pages(
            project_id=result.manifest.project_id,
            requester_acl_refs=("other-team",),
        )
        == ()
    )


def test_runtime_builder_requires_reviewed_positive_trial_before_staging_and_publish(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    base = runtime.compile_to_staging(_request())
    runtime.publish_generation(
        project_id=base.manifest.project_id,
        generation_id=base.manifest.generation_id,
        expected_manifest_sha256=base.manifest.content_sha256,
        reviewer_authority_sha256=canonical_sha256_v1("compiler-reviewer"),
        published_at="2026-08-03T04:10:00Z",
    )
    before = next(
        item
        for item in base.records
        if isinstance(item, WikiPageFragmentV1) and item.page_type.value == "capability"
    )
    after = build_wiki_page_fragment_v1(
        **before.model_dump(mode="python", exclude={"content_sha256", "summary"}),
        summary="Reviewed Wiki runtime navigation with complete raw-evidence guidance.",
    )
    operation = build_wiki_patch_operation_v1(
        action=WikiPatchActionV1.UPSERT_PAGE,
        visibility_partition=before.scope.visibility_partition,
        logical_path=before.logical_path,
        before_sha256=before.content_sha256,
        after_page=after,
    )
    patch = build_wiki_builder_patch_v1(
        base_manifest_sha256=base.manifest.content_sha256,
        operations=(operation,),
        affected_query_ids=("affected-query",),
        guard_query_ids=("guard-query",),
        trigger_error_entry_ids=(),
        origin=WikiPatchOriginV1.HUMAN_REVIEWED,
        reviewed=True,
        reviewer_authority_sha256=canonical_sha256_v1("patch-reviewer"),
    )
    runtime.propose_patch(project_id=base.manifest.project_id, patch=patch)
    with pytest.raises(WikiRuntimeError, match="patch or decision"):
        runtime.stage_builder_generation(
            project_id=base.manifest.project_id,
            patch_id=patch.patch_id,
            generation_id="wiki-runtime-builder-generation",
        )
    trial = build_wiki_builder_trial_v1(
        patch_sha256=patch.content_sha256,
        initial_manifest_sha256=base.manifest.content_sha256,
        affected_before=(_outcome("affected-query", complete=False, numerator=0),),
        affected_after=(_outcome("affected-query", complete=True, numerator=1),),
        guard_before=(_outcome("guard-query", complete=True, numerator=1),),
        guard_after=(_outcome("guard-query", complete=True, numerator=1),),
    )
    decision = runtime.evaluate_patch(
        project_id=base.manifest.project_id,
        patch_id=patch.patch_id,
        trial=trial,
    )
    assert decision.status is WikiBuilderDecisionStatusV1.PROMOTE_TO_STAGING
    staged = runtime.stage_builder_generation(
        project_id=base.manifest.project_id,
        patch_id=patch.patch_id,
        generation_id="wiki-runtime-builder-generation",
        created_at="2026-08-03T05:00:00Z",
    )
    runtime.publish_generation(
        project_id=base.manifest.project_id,
        generation_id=staged.manifest.generation_id,
        expected_manifest_sha256=staged.manifest.content_sha256,
        reviewer_authority_sha256=canonical_sha256_v1("release-reviewer"),
        published_at="2026-08-03T05:10:00Z",
    )
    assert runtime.store.active_manifest(base.manifest.project_id) == staged.manifest

    first_page = next(
        item
        for item in staged.records
        if isinstance(item, WikiPageFragmentV1) and item.logical_path == after.logical_path
    )
    second_page = build_wiki_page_fragment_v1(
        **first_page.model_dump(mode="python", exclude={"content_sha256", "summary"}),
        summary="Second reviewed Wiki Builder generation preserves chained authority.",
    )
    second_operation = build_wiki_patch_operation_v1(
        action=WikiPatchActionV1.UPSERT_PAGE,
        visibility_partition=first_page.scope.visibility_partition,
        logical_path=first_page.logical_path,
        before_sha256=first_page.content_sha256,
        after_page=second_page,
    )
    second_patch = build_wiki_builder_patch_v1(
        base_manifest_sha256=staged.manifest.content_sha256,
        operations=(second_operation,),
        affected_query_ids=("affected-query-second",),
        guard_query_ids=("guard-query-second",),
        trigger_error_entry_ids=(),
        origin=WikiPatchOriginV1.HUMAN_REVIEWED,
        reviewed=True,
        reviewer_authority_sha256=canonical_sha256_v1("patch-reviewer-second"),
    )
    runtime.propose_patch(project_id=base.manifest.project_id, patch=second_patch)
    second_trial = build_wiki_builder_trial_v1(
        patch_sha256=second_patch.content_sha256,
        initial_manifest_sha256=staged.manifest.content_sha256,
        affected_before=(_outcome("affected-query-second", complete=False, numerator=0),),
        affected_after=(_outcome("affected-query-second", complete=True, numerator=1),),
        guard_before=(_outcome("guard-query-second", complete=True, numerator=1),),
        guard_after=(_outcome("guard-query-second", complete=True, numerator=1),),
    )
    runtime.evaluate_patch(
        project_id=base.manifest.project_id,
        patch_id=second_patch.patch_id,
        trial=second_trial,
    )
    second_staged = runtime.stage_builder_generation(
        project_id=base.manifest.project_id,
        patch_id=second_patch.patch_id,
        generation_id="wiki-runtime-builder-generation-2",
        created_at="2026-08-03T05:20:00Z",
    )
    second_base = runtime.store.read_generation_base(
        project_id=base.manifest.project_id,
        generation_id=second_staged.manifest.generation_id,
    )
    assert second_base is not None
    assert second_base.lineage_depth == 2
    assert second_base.parent_manifest_sha256 == staged.manifest.content_sha256
    assert second_base.applied_patch_sha256 == (
        patch.content_sha256,
        second_patch.content_sha256,
    )
    runtime.publish_generation(
        project_id=base.manifest.project_id,
        generation_id=second_staged.manifest.generation_id,
        expected_manifest_sha256=second_staged.manifest.content_sha256,
        reviewer_authority_sha256=canonical_sha256_v1("release-reviewer-second"),
        published_at="2026-08-03T05:30:00Z",
    )
    with pytest.raises(WikiRuntimeError, match="canonical reviewer"):
        runtime.rollback_generation(
            project_id=base.manifest.project_id,
            target_generation_id=staged.manifest.generation_id,
            expected_target_manifest_sha256=staged.manifest.content_sha256,
            expected_active_generation_id=second_staged.manifest.generation_id,
            expected_active_manifest_sha256=second_staged.manifest.content_sha256,
            reviewer_authority_sha256="unreviewed",
        )
    with pytest.raises(WikiPublicationError, match="active Wiki generation changed"):
        runtime.rollback_generation(
            project_id=base.manifest.project_id,
            target_generation_id=staged.manifest.generation_id,
            expected_target_manifest_sha256=staged.manifest.content_sha256,
            expected_active_generation_id=second_staged.manifest.generation_id,
            expected_active_manifest_sha256=canonical_sha256_v1("stale-active"),
            reviewer_authority_sha256=canonical_sha256_v1("rollback-reviewer"),
        )
    assert runtime.store.active_manifest(base.manifest.project_id) == second_staged.manifest
    restored = runtime.rollback_generation(
        project_id=base.manifest.project_id,
        target_generation_id=staged.manifest.generation_id,
        expected_target_manifest_sha256=staged.manifest.content_sha256,
        expected_active_generation_id=second_staged.manifest.generation_id,
        expected_active_manifest_sha256=second_staged.manifest.content_sha256,
        reviewer_authority_sha256=canonical_sha256_v1("rollback-reviewer"),
        rolled_back_at="2026-08-03T05:40:00Z",
    )
    assert restored == staged.manifest
    assert runtime.store.active_manifest(base.manifest.project_id) == staged.manifest
