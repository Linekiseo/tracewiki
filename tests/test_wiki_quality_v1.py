from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from evidence_rag.rag.wiki.evaluation_v1 import (
    WikiAnswerModeV1,
    WikiMetricAvailabilityV1,
    WikiObservationStatusV1,
    WikiReviewedNavigationRowV1,
    build_wiki_golden_release_v1,
    evaluate_reviewed_wiki_navigation_v1,
    evaluate_wiki_navigation_slices_v1,
)
from evidence_rag.rag.wiki.quality_runner_v1 import run_wiki_quality_v1
from evidence_rag.rag.wiki.quality_v1 import (
    WikiQualityError,
    WikiQualityHardGateV1,
    verify_wiki_quality_artifact_v1,
)


def _perfect_rows() -> tuple[WikiReviewedNavigationRowV1, ...]:
    release = build_wiki_golden_release_v1()
    return tuple(
        WikiReviewedNavigationRowV1(
            case_id=case.case_id,
            status=WikiObservationStatusV1.AVAILABLE,
            selected_page_paths=case.required_page_paths,
            visited_page_paths=case.required_page_paths,
            followed_link_ids=(),
            retrieved_source_ref_ids=case.required_source_ref_ids,
            cited_source_ref_ids=case.required_source_ref_ids,
            fulfilled_obligations=case.evidence_obligations,
            answer_mode=(
                WikiAnswerModeV1.GROUNDED if case.expected_answerable else WikiAnswerModeV1.REFUSAL
            ),
            refusal_reason=case.refusal_reason,
            unsupported_claim_count=0,
            diagnostic_code=None,
        )
        for case in release.cases
    )


def test_slice_evaluator_recomputes_all_ten_slices_from_full_released_membership() -> None:
    release = build_wiki_golden_release_v1()
    rows = _perfect_rows()
    overall = evaluate_reviewed_wiki_navigation_v1(release, rows)
    slices = evaluate_wiki_navigation_slices_v1(release, rows)
    assert len(slices.slices) == 10
    assert sum(item.case_count for item in slices.slices) == 120
    assert all(
        metric.availability is WikiMetricAvailabilityV1.AVAILABLE and metric.value == 1.0
        for item in slices.slices
        for metric in item.metrics
    )
    assert all(item.value == 1.0 for item in overall.metrics)


def test_released_quality_runner_uses_real_navigator_and_publishes_offline_verifiable_hold(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "wiki-quality-artifact"
    report, verification = run_wiki_quality_v1(
        run_id="wiki-quality-test-v1",
        output_dir=artifact,
    )
    assert report.evaluated_case_count == 120
    assert report.all_hard_gates_passed is True
    refusal_gate = next(
        item for item in report.hard_gates if item.gate is WikiQualityHardGateV1.CORRECT_REFUSAL
    )
    assert refusal_gate.numerator == refusal_gate.denominator == 10
    assert report.qualification == "ENGINEERING_FIXTURE_NON_QUALIFIED"
    assert report.quality_state == "QUALITY_HOLD"
    assert verification.status == "VERIFIED_ENGINEERING_FIXTURE_NON_QUALIFIED"
    copied = tmp_path / "quality-copy"
    shutil.copytree(artifact, copied)
    assert verify_wiki_quality_artifact_v1(copied) == verification
    (copied / "rows.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(WikiQualityError, match="checksum"):
        verify_wiki_quality_artifact_v1(copied)
