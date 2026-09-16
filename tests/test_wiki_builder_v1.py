from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.rag.multisource_foundation_v2 import MultiSourceCandidateV2
from evidence_rag.rag.wiki.builder_v1 import (
    WikiBuilderDecisionStatusV1,
    WikiBuilderError,
    WikiPatchActionV1,
    WikiPatchOriginV1,
    apply_reviewed_wiki_patch_v1,
    build_wiki_builder_patch_v1,
    build_wiki_builder_trial_v1,
    build_wiki_patch_operation_v1,
    build_wiki_query_outcome_v1,
    evaluate_wiki_builder_patch_v1,
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
    build_wiki_page_fragment_v1,
    canonical_sha256_v1,
)
from evidence_rag.rag.wiki.store_v1 import WikiStoreV1


def _base():
    generations = tuple(
        build_source_generation_v1(
            source=source,
            generation_id=f"source-{source.value}-generation-1",
            watermark=f"source-{source.value}-watermark-1",
        )
        for source in WikiSourceDomainV1
    )
    candidate = MultiSourceCandidateV2(
        candidate_id="candidate-code-builder",
        entity_id="entity-code-builder",
        retrieval_unit_id="unit-code-builder",
        parent_entity_id=None,
        source_instance="code-primary",
        retrieval_domain="code",
        fact_type="code.fact",
        entity_type="code.CodeSymbol",
        task="implementation",
        title="Wiki Builder implementation",
        snippet="Reviewed implementation evidence for the offline Wiki Builder.",
        locator="code://project-wiki-builder/symbol/builder",
        stable_version="v1",
        source_generation="source-code-generation-1",
        raw_or_derived="raw_fact",
        derivation="production_adapter",
        review_status="reviewed",
        fact_status="active",
        channel_scores=(("exact", 1.0),),
        calibrated_relevance=1.0,
        calibration_version="calibration-v1",
        matched_roles=("implementation",),
        authority=1.0,
        version_alignment="exact",
        acl_ref="team-a",
        token_estimate=48,
        root_provenance="production-code-adapter",
    )
    request = build_wiki_compilation_request_v1(
        request_id="wiki-builder-base-request",
        project_id="project-wiki-builder",
        generation_id="wiki-builder-base-generation",
        source_generations=generations,
        candidates=(candidate,),
        observed_at="2026-08-03T00:00:00Z",
        compiler_authority_sha256=WIKI_COMPILER_AUTHORITY_SHA256,
    )
    return compile_wiki_v1(request)


def _updated_page(base):
    page = next(
        item
        for item in base.records
        if isinstance(item, WikiPageFragmentV1) and item.page_type.value == "capability"
    )
    updated = build_wiki_page_fragment_v1(
        **page.model_dump(mode="python", exclude={"content_sha256", "summary"}),
        summary=(
            "A reviewed cross-source capability with explicit navigation and raw-evidence "
            "verification guidance."
        ),
    )
    return page, updated


def _reviewed_patch(base):
    before, after = _updated_page(base)
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
        affected_query_ids=("wiki-query-affected-1",),
        guard_query_ids=("wiki-query-guard-1",),
        trigger_error_entry_ids=(),
        origin=WikiPatchOriginV1.HUMAN_REVIEWED,
        reviewed=True,
        reviewer_authority_sha256=canonical_sha256_v1("reviewer-authority-v1"),
    )
    return patch, before, after


def _outcome(
    query_id: str,
    *,
    complete: bool,
    obligations: int,
    raw: int,
    leakage: int = 0,
    unsupported: int = 0,
    steps: int = 8,
):
    return build_wiki_query_outcome_v1(
        query_id=query_id,
        evidence_complete=complete,
        obligation_numerator=obligations,
        obligation_denominator=2,
        raw_verify_numerator=raw,
        raw_verify_denominator=2,
        acl_leakage_count=leakage,
        unsupported_claim_count=unsupported,
        navigation_steps=steps,
        token_count=1_000,
        latency_ms=100.0,
    )


def test_reviewed_patch_builds_new_immutable_generation_and_stages_in_store(tmp_path: Path) -> None:
    base = _base()
    patch, before, after = _reviewed_patch(base)
    result = apply_reviewed_wiki_patch_v1(
        base=base,
        patch=patch,
        generation_id="wiki-builder-patched-generation",
        created_at="2026-08-03T02:00:00Z",
    )
    assert result.base_manifest_sha256 == base.manifest.content_sha256
    assert result.manifest.content_sha256 != base.manifest.content_sha256
    assert result.changed_page_count == 1
    assert result.deleted_page_count == 0
    assert any(
        isinstance(item, WikiPageFragmentV1) and item.content_sha256 == after.content_sha256
        for item in result.records
    )
    assert any(
        isinstance(item, WikiPageFragmentV1) and item.content_sha256 == before.content_sha256
        for item in base.records
    )
    store = WikiStoreV1(tmp_path / "wiki.sqlite3", isolated_root=tmp_path)
    store.stage_generation(result.manifest, result.records)
    assert (
        store.verify_generation(
            project_id=result.manifest.project_id,
            generation_id=result.manifest.generation_id,
            expected_manifest_sha256=result.manifest.content_sha256,
        )
        == result.manifest
    )


def test_unreviewed_wrong_base_and_dangling_delete_patches_fail_closed() -> None:
    base = _base()
    reviewed, before, after = _reviewed_patch(base)
    unreviewed = build_wiki_builder_patch_v1(
        base_manifest_sha256=base.manifest.content_sha256,
        operations=reviewed.operations,
        affected_query_ids=reviewed.affected_query_ids,
        guard_query_ids=reviewed.guard_query_ids,
        trigger_error_entry_ids=(),
        origin=WikiPatchOriginV1.AGENT_UNREVIEWED,
        reviewed=False,
        reviewer_authority_sha256=None,
    )
    with pytest.raises(WikiBuilderError, match="unreviewed"):
        apply_reviewed_wiki_patch_v1(
            base=base,
            patch=unreviewed,
            generation_id="forbidden-generation",
            created_at="2026-08-03T02:00:00Z",
        )
    wrong_base = build_wiki_builder_patch_v1(
        base_manifest_sha256=canonical_sha256_v1("wrong-base"),
        operations=reviewed.operations,
        affected_query_ids=reviewed.affected_query_ids,
        guard_query_ids=reviewed.guard_query_ids,
        trigger_error_entry_ids=reviewed.trigger_error_entry_ids,
        origin=reviewed.origin,
        reviewed=True,
        reviewer_authority_sha256=reviewed.reviewer_authority_sha256,
    )
    with pytest.raises(WikiBuilderError, match="base manifest"):
        apply_reviewed_wiki_patch_v1(
            base=base,
            patch=wrong_base,
            generation_id="forbidden-generation",
            created_at="2026-08-03T02:00:00Z",
        )
    source_page = next(
        item
        for item in base.records
        if isinstance(item, WikiPageFragmentV1) and item.page_type.value == "source"
    )
    delete = build_wiki_patch_operation_v1(
        action=WikiPatchActionV1.DELETE_PAGE,
        visibility_partition=source_page.scope.visibility_partition,
        logical_path=source_page.logical_path,
        before_sha256=source_page.content_sha256,
        after_page=None,
    )
    delete_patch = build_wiki_builder_patch_v1(
        base_manifest_sha256=base.manifest.content_sha256,
        operations=(delete,),
        affected_query_ids=("wiki-query-delete",),
        guard_query_ids=("wiki-query-guard",),
        trigger_error_entry_ids=(),
        origin=WikiPatchOriginV1.HUMAN_REVIEWED,
        reviewed=True,
        reviewer_authority_sha256=canonical_sha256_v1("reviewer-authority-v1"),
    )
    with pytest.raises(WikiBuilderError, match="dangling"):
        apply_reviewed_wiki_patch_v1(
            base=base,
            patch=delete_patch,
            generation_id="forbidden-delete-generation",
            created_at="2026-08-03T02:00:00Z",
        )


def test_builder_promotes_only_positive_affected_utility_with_guard_non_regression() -> None:
    base = _base()
    patch, _, _ = _reviewed_patch(base)
    affected_before = (_outcome("wiki-query-affected-1", complete=False, obligations=1, raw=1),)
    affected_after = (_outcome("wiki-query-affected-1", complete=True, obligations=2, raw=2),)
    guard = (_outcome("wiki-query-guard-1", complete=True, obligations=2, raw=2),)
    trial = build_wiki_builder_trial_v1(
        patch_sha256=patch.content_sha256,
        initial_manifest_sha256=base.manifest.content_sha256,
        affected_before=affected_before,
        affected_after=affected_after,
        guard_before=guard,
        guard_after=guard,
    )
    decision = evaluate_wiki_builder_patch_v1(patch, trial)
    assert decision.status is WikiBuilderDecisionStatusV1.PROMOTE_TO_STAGING
    assert decision.affected_utility_delta > 0
    assert decision.regressed_guard_queries == ()
    assert decision.hard_guard_failures == ()
    assert decision.production_authorized is False


def test_builder_rejects_guard_regression_acl_leakage_and_unsupported_claims() -> None:
    base = _base()
    patch, _, _ = _reviewed_patch(base)
    affected_before = (_outcome("wiki-query-affected-1", complete=False, obligations=1, raw=1),)
    affected_after = (_outcome("wiki-query-affected-1", complete=True, obligations=2, raw=2),)
    guard_before = (_outcome("wiki-query-guard-1", complete=True, obligations=2, raw=2),)
    guard_after = (
        _outcome(
            "wiki-query-guard-1",
            complete=False,
            obligations=1,
            raw=1,
            leakage=1,
            unsupported=1,
        ),
    )
    trial = build_wiki_builder_trial_v1(
        patch_sha256=patch.content_sha256,
        initial_manifest_sha256=base.manifest.content_sha256,
        affected_before=affected_before,
        affected_after=affected_after,
        guard_before=guard_before,
        guard_after=guard_after,
    )
    decision = evaluate_wiki_builder_patch_v1(patch, trial)
    assert decision.status is WikiBuilderDecisionStatusV1.REJECT
    assert decision.regressed_guard_queries == ("wiki-query-guard-1",)
    assert decision.hard_guard_failures == (
        "guard:wiki-query-guard-1:acl_leakage",
        "guard:wiki-query-guard-1:unsupported_claim",
    )
