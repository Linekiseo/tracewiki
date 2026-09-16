"""Execute the released 120-case Wiki Golden through the real isolated Navigator."""

from __future__ import annotations

import argparse
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from time import perf_counter_ns

from ..multisource_foundation_v2 import MultiSourceCandidateV2
from .contracts_v1 import (
    WikiDirectoryV1,
    WikiFactAuthorityV1,
    WikiFactStatusV1,
    WikiGenerationStatusV1,
    WikiLinkStatusV1,
    WikiPageFragmentV1,
    WikiPageTypeV1,
    WikiRecordIndexV1,
    WikiRecordKindV1,
    WikiScopeV1,
    WikiSourceDomainV1,
    build_source_generation_v1,
    build_wiki_directory_v1,
    build_wiki_fact_v1,
    build_wiki_generation_manifest_v1,
    build_wiki_link_v1,
    build_wiki_page_fragment_v1,
    build_wiki_source_ref_v1,
    canonical_sha256_v1,
    visibility_partition_v1,
)
from .evaluation_v1 import (
    WikiAnswerModeV1,
    WikiObservationStatusV1,
    WikiReviewedNavigationRowV1,
    build_wiki_golden_release_v1,
    evaluate_reviewed_wiki_navigation_v1,
    evaluate_wiki_navigation_slices_v1,
)
from .navigator_v1 import (
    WikiNavigationActionTypeV1,
    WikiNavigationStopReasonV1,
    WikiObligationStatusV1,
    build_wiki_navigation_request_v1,
)
from .paths_v1 import WIKI_INTENT_DIRECTORIES, wiki_record_physical_key_v1
from .quality_v1 import (
    WikiQualityExecutionV1,
    WikiQualityRunReportV1,
    build_wiki_quality_run_report_v1,
    verify_wiki_quality_artifact_v1,
    write_wiki_quality_artifact_v1,
)
from .runtime_v1 import WikiRuntimeV1
from .search_v1 import WikiQueryClassV1
from .store_v1 import WikiStoreV1

WIKI_QUALITY_FIXTURE_VERSION = "wiki-navigation-golden-fixture-v1"
WIKI_QUALITY_FIXTURE_AUTHORITY_SHA256 = canonical_sha256_v1(
    {
        "golden": "agent-native-wiki-golden-v1/v1",
        "rules": (
            "exact-released-membership",
            "all-positive-paths-and-raw-refs",
            "forbidden-refs-absent",
            "single-governed-acl",
            "real-store-search-navigator-raw-gateway",
        ),
        "version": WIKI_QUALITY_FIXTURE_VERSION,
    }
)


def _page_type(path: str) -> WikiPageTypeV1:
    intent = path.split("/", 2)[1]
    mapping = {
        "architecture": WikiPageTypeV1.ARCHITECTURE,
        "capabilities": WikiPageTypeV1.CAPABILITY,
        "changes": WikiPageTypeV1.CHANGE,
        "components": WikiPageTypeV1.COMPONENT,
        "decisions": WikiPageTypeV1.DECISION,
        "experiments": WikiPageTypeV1.EXPERIMENT,
        "findings": WikiPageTypeV1.FINDING,
        "issues": WikiPageTypeV1.ISSUE,
        "procedures": WikiPageTypeV1.PROCEDURE,
        "requirements": WikiPageTypeV1.REQUIREMENT,
        "sources": WikiPageTypeV1.SOURCE,
    }
    return mapping[intent]


def _source_for_ref(source_ref_id: str) -> WikiSourceDomainV1:
    for source in WikiSourceDomainV1:
        if source_ref_id.startswith(f"raw-{source.value}-"):
            return source
    raise ValueError("released Wiki source-ref ID has no governed source")


def _fixture():
    release = build_wiki_golden_release_v1()
    project_id = release.cases[0].project_id
    generation_id = "wiki-quality-generation-v1"
    source_generations = tuple(
        build_source_generation_v1(
            source=source,
            generation_id=f"wiki-quality-{source.value}-generation-v1",
            watermark=f"wiki-quality-{source.value}-watermark-v1",
        )
        for source in WikiSourceDomainV1
    )
    generation_by_source = {item.source: item for item in source_generations}
    acl_refs = ("team-a",)
    scope = WikiScopeV1(
        project_id=project_id,
        visibility_partition=visibility_partition_v1(acl_refs),
        acl_refs=acl_refs,
        source_generations=source_generations,
        as_of="2026-08-03T00:00:00Z",
    )
    pages: list[WikiPageFragmentV1] = []
    candidates: list[MultiSourceCandidateV2] = []
    for case in release.cases:
        source_refs = []
        for source_ref_id in case.required_source_ref_ids:
            source = _source_for_ref(source_ref_id)
            generation = generation_by_source[source]
            candidate = MultiSourceCandidateV2(
                candidate_id=f"candidate-{source_ref_id}",
                entity_id=f"entity-{source_ref_id}",
                retrieval_unit_id=f"unit-{source_ref_id}",
                parent_entity_id=None,
                source_instance=f"wiki-quality-{source.value}",
                retrieval_domain=source.value,
                fact_type=f"{source.value}.reviewed_quality_fact",
                entity_type=f"{source.value}.ReviewedQualityEvidence",
                task="compare" if source is WikiSourceDomainV1.EXPERIMENT else "current",
                title=f"{case.case_id} reviewed {source.value} evidence",
                snippet=f"Reviewed raw evidence for {case.case_id} and its Wiki obligations.",
                locator=f"{source.value}://{project_id}/quality/{case.case_id}",
                stable_version="quality-fixture-v1",
                source_generation=generation.generation_id,
                raw_or_derived="raw_fact",
                derivation="reviewed_quality_fixture",
                review_status="reviewed",
                fact_status="active",
                channel_scores=(("exact", 1.0), ("sparse", 1.0)),
                calibrated_relevance=1.0,
                calibration_version="quality-fixture-calibration-v1",
                matched_roles=("evidence",),
                authority=1.0,
                version_alignment="exact",
                acl_ref="team-a",
                token_estimate=24,
                root_provenance="reviewed-wiki-quality-fixture",
            )
            candidates.append(candidate)
            raw_digest = canonical_sha256_v1(candidate.model_dump(mode="json"))
            source_refs.append(
                build_wiki_source_ref_v1(
                    source_ref_id=source_ref_id,
                    source=source,
                    entity_type=candidate.entity_type,
                    entity_id=candidate.entity_id,
                    locator=candidate.locator,
                    generation_id=generation.generation_id,
                    watermark=generation.watermark,
                    acl_refs=acl_refs,
                    observed_at="2026-08-03T00:00:00Z",
                    raw_content_sha256=raw_digest,
                )
            )
        ordered_refs = tuple(sorted(source_refs, key=lambda item: item.source_ref_id))
        for page_index, path in enumerate(case.required_page_paths, start=1):
            other_paths = tuple(item for item in case.required_page_paths if item != path)
            fact = build_wiki_fact_v1(
                fact_id=f"fact-{case.case_id}-{page_index}",
                subject_path=path,
                predicate="supports.reviewed_navigation",
                object_text=f"Reviewed support for {case.case_id} evidence obligations.",
                source_ref_ids=tuple(item.source_ref_id for item in ordered_refs),
                authority=WikiFactAuthorityV1.RAW_OBSERVED,
                status=WikiFactStatusV1.ACTIVE,
                confidence=1.0,
                valid_from="2026-08-03T00:00:00Z",
                valid_to=None,
            )
            links = tuple(
                sorted(
                    (
                        build_wiki_link_v1(
                            link_id=f"link-{case.case_id}-{page_index}-{target_index}",
                            source_path=path,
                            target_path=target,
                            relation="reviewed_bridge",
                            inverse_relation="reviewed_bridge",
                            source_ref_ids=tuple(item.source_ref_id for item in ordered_refs),
                            status=WikiLinkStatusV1.ACTIVE,
                            confidence=1.0,
                        )
                        for target_index, target in enumerate(other_paths, start=1)
                    ),
                    key=lambda item: item.link_id,
                )
            )
            pages.append(
                build_wiki_page_fragment_v1(
                    fragment_id=f"fragment-{case.case_id}-{page_index}",
                    logical_path=path,
                    page_type=_page_type(path),
                    title=f"{case.query} page {page_index}",
                    summary=(
                        f"Reviewed {case.slice.value} evidence for {case.case_id}; "
                        f"obligations {' '.join(case.evidence_obligations)}."
                    ),
                    aliases=(case.case_id,),
                    tags=tuple(sorted((case.case_id, case.slice.value))),
                    scope=scope,
                    source_refs=ordered_refs,
                    facts=(fact,),
                    links=links,
                    evidence_roles=case.evidence_obligations,
                )
            )
    by_intent: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        by_intent[page.logical_path.split("/", 2)[1]].append(page.logical_path)
    directories = tuple(
        build_wiki_directory_v1(
            directory_id=f"directory-{intent}-quality-v1",
            logical_path=f"/{intent}",
            scope=scope,
            child_paths=(),
            page_paths=tuple(sorted(by_intent[intent])),
        )
        for intent in WIKI_INTENT_DIRECTORIES
    )
    records = tuple(
        sorted(
            (*directories, *pages),
            key=lambda item: (
                item.scope.visibility_partition,
                item.logical_path,
                (
                    WikiRecordKindV1.DIRECTORY.value
                    if isinstance(item, WikiDirectoryV1)
                    else WikiRecordKindV1.PAGE_FRAGMENT.value
                ),
            ),
        )
    )
    indexes = tuple(
        WikiRecordIndexV1(
            logical_path=record.logical_path,
            record_kind=(
                WikiRecordKindV1.DIRECTORY
                if isinstance(record, WikiDirectoryV1)
                else WikiRecordKindV1.PAGE_FRAGMENT
            ),
            visibility_partition=record.scope.visibility_partition,
            record_sha256=record.content_sha256,
            physical_key=wiki_record_physical_key_v1(
                project_id=project_id,
                generation_id=generation_id,
                visibility_partition=record.scope.visibility_partition,
                logical_path=record.logical_path,
                record_kind=(
                    WikiRecordKindV1.DIRECTORY.value
                    if isinstance(record, WikiDirectoryV1)
                    else WikiRecordKindV1.PAGE_FRAGMENT.value
                ),
            ),
        )
        for record in records
    )
    manifest = build_wiki_generation_manifest_v1(
        snapshot_id="wiki-quality-snapshot-v1",
        project_id=project_id,
        generation_id=generation_id,
        status=WikiGenerationStatusV1.VERIFIED,
        source_generations=source_generations,
        visibility_partitions=(scope.visibility_partition,),
        records=indexes,
        root_paths=tuple(f"/{intent}" for intent in WIKI_INTENT_DIRECTORIES),
        compiler_authority_sha256=WIKI_QUALITY_FIXTURE_AUTHORITY_SHA256,
        created_at="2026-08-03T00:00:00Z",
    )
    return release, manifest, records, tuple(sorted(candidates, key=lambda item: item.candidate_id))


def _query_class(case_slice: str) -> WikiQueryClassV1:
    if case_slice == "local_raw_detail":
        return WikiQueryClassV1.LOCAL_DETAIL
    if case_slice in {"bridge_2hop", "high_fan_in"}:
        return WikiQueryClassV1.MULTI_HOP
    if case_slice in {"compare_causal", "incremental_contradiction"}:
        return WikiQueryClassV1.COMPARISON
    if case_slice == "temporal_stale":
        return WikiQueryClassV1.TEMPORAL
    if case_slice == "global_aggregate":
        return WikiQueryClassV1.GLOBAL
    return WikiQueryClassV1.EXPLORATORY


def run_wiki_quality_v1(
    *,
    run_id: str,
    output_dir: Path,
) -> tuple[WikiQualityRunReportV1, object]:
    release, manifest, records, candidates = _fixture()
    with tempfile.TemporaryDirectory(prefix="evidence-rag-wiki-quality-") as temporary:
        root = Path(temporary)
        runtime = WikiRuntimeV1(
            store=WikiStoreV1(root / "wiki-quality.sqlite3", isolated_root=root)
        )
        runtime.store.stage_generation(
            manifest,
            records,
            candidates=candidates,
        )
        runtime.store.publish_generation(
            project_id=manifest.project_id,
            generation_id=manifest.generation_id,
            expected_manifest_sha256=manifest.content_sha256,
            published_at="2026-08-03T00:01:00Z",
        )
        # The source-ref authority is easier and safer to derive from the stored records
        # than from an identifier naming convention.
        fact_to_ref = {
            "wiki-raw-fact-" + ref.raw_content_sha256[7:31]: ref.source_ref_id
            for record in records
            if isinstance(record, WikiPageFragmentV1)
            for ref in record.source_refs
        }
        rows: list[WikiReviewedNavigationRowV1] = []
        executions: list[WikiQualityExecutionV1] = []
        for case in release.cases:
            started = perf_counter_ns()
            try:
                request = build_wiki_navigation_request_v1(
                    request_id=f"wiki-quality-{case.case_id}",
                    project_id=case.project_id,
                    requester_acl_refs=case.requester_acl_refs,
                    query=case.query,
                    query_class=_query_class(case.slice.value),
                    required_roles=case.evidence_obligations,
                    required_sources=case.required_sources,
                    as_of=case.as_of,
                    max_searches=8,
                    max_page_reads=48,
                    max_link_hops=4,
                    max_raw_reads=16,
                    empty_search_patience=2,
                    retrieval_deadline_ms=4_000,
                    top_k_per_search=10,
                    token_budget=8_000,
                    require_raw_evidence=True,
                )
                navigation = runtime.navigate(request)
            except Exception as error:
                duration = max(1, perf_counter_ns() - started)
                diagnostic = type(error).__name__
                rows.append(
                    WikiReviewedNavigationRowV1(
                        case_id=case.case_id,
                        status=WikiObservationStatusV1.ERROR,
                        diagnostic_code=diagnostic,
                    )
                )
                execution_payload = {
                    "case_id": case.case_id,
                    "duration_ns": duration,
                    "navigation_sha256": canonical_sha256_v1(
                        {"case_id": case.case_id, "error": diagnostic}
                    ),
                    "stop_reason": "error",
                    "search_count": 0,
                    "page_read_count": 0,
                    "raw_read_count": 0,
                    "diagnostic_code": diagnostic,
                }
            else:
                duration = max(1, perf_counter_ns() - started)
                visited = tuple(
                    dict.fromkeys(
                        output
                        for action in navigation.actions
                        if action.action
                        in {
                            WikiNavigationActionTypeV1.READ,
                            WikiNavigationActionTypeV1.FOLLOW,
                        }
                        for output in action.output_ids
                    )
                )
                cited_refs = tuple(
                    dict.fromkeys(
                        fact_to_ref[item.fact_id]
                        for item in navigation.evidence_pack.citations
                        if item.fact_id in fact_to_ref
                    )
                )
                grounded = (
                    navigation.stop_reason is WikiNavigationStopReasonV1.EVIDENCE_COMPLETE
                    and navigation.evidence_pack.decision.status == "supported"
                )
                rows.append(
                    WikiReviewedNavigationRowV1(
                        case_id=case.case_id,
                        status=WikiObservationStatusV1.AVAILABLE,
                        selected_page_paths=navigation.selected_page_paths,
                        visited_page_paths=visited,
                        followed_link_ids=(),
                        retrieved_source_ref_ids=navigation.verified_source_ref_ids,
                        cited_source_ref_ids=cited_refs,
                        fulfilled_obligations=tuple(
                            item.role
                            for item in navigation.obligations
                            if item.status is WikiObligationStatusV1.SATISFIED
                        ),
                        answer_mode=(
                            WikiAnswerModeV1.GROUNDED if grounded else WikiAnswerModeV1.REFUSAL
                        ),
                        refusal_reason=(None if grounded else "insufficient_visible_evidence"),
                        unsupported_claim_count=0,
                        diagnostic_code=None,
                    )
                )
                execution_payload = {
                    "case_id": case.case_id,
                    "duration_ns": duration,
                    "navigation_sha256": navigation.content_sha256,
                    "stop_reason": navigation.stop_reason.value,
                    "search_count": navigation.search_count,
                    "page_read_count": navigation.page_read_count,
                    "raw_read_count": navigation.raw_read_count,
                    "diagnostic_code": None,
                }
            executions.append(
                WikiQualityExecutionV1(
                    **execution_payload,
                    content_sha256=canonical_sha256_v1(execution_payload),
                )
            )
        row_tuple = tuple(rows)
        execution_tuple = tuple(executions)
        overall = evaluate_reviewed_wiki_navigation_v1(release, row_tuple)
        slices = evaluate_wiki_navigation_slices_v1(release, row_tuple)
        report = build_wiki_quality_run_report_v1(
            run_id=run_id,
            fixture_manifest_sha256=manifest.content_sha256,
            rows=row_tuple,
            executions=execution_tuple,
            overall=overall,
            slices=slices,
        )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        verification = write_wiki_quality_artifact_v1(
            report=report,
            rows=row_tuple,
            executions=execution_tuple,
            overall=overall,
            slices=slices,
            output_dir=output_dir,
            isolated_root=output_dir.parent,
        )
        portable = root / "portable-quality-copy"
        shutil.copytree(output_dir, portable)
        if verify_wiki_quality_artifact_v1(portable) != verification:
            raise RuntimeError("portable Wiki quality verification differs from original")
        return report, verification


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--run-id", default="wiki-quality-20260803-v1")
    arguments = parser.parse_args()
    report, verification = run_wiki_quality_v1(
        run_id=arguments.run_id,
        output_dir=arguments.output,
    )
    print(
        {
            "report": report.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json"),
        }
    )


if __name__ == "__main__":
    main()
