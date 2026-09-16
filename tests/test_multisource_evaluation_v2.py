from __future__ import annotations

import json

import pytest

from evidence_rag.rag.global_governance_v2 import (
    ReleaseStageV2,
    SourceReleaseTruthV2,
    build_rollback_rehearsal_v2,
    build_security_matrix_v2,
    evaluate_global_release_v2,
    evaluate_security_matrix_v2,
)
from evidence_rag.rag.multisource_evaluation_v2 import (
    AvailabilityV2,
    CaseExecutionStateV2,
    build_multisource_bundle_v2,
    build_reviewed_case_v2,
    evaluate_multisource_release_v2,
    serialize_multisource_bundle_v2,
    verify_multisource_bundle_v2,
)
from evidence_rag.rag.multisource_foundation_v2 import (
    MULTISOURCE_DOMAINS,
    build_multisource_golden_v2,
)
from evidence_rag.rag.performance_v2 import build_performance_dashboard_v2


def _unavailable_rows():
    golden = build_multisource_golden_v2()
    return golden, tuple(
        build_reviewed_case_v2(
            case_id=item.case_id,
            execution_state=CaseExecutionStateV2.UNAVAILABLE,
            failure_reason="production_observation_unavailable",
        )
        for item in golden.cases
    )


def test_released_evaluator_freezes_60_membership_and_honest_denominators() -> None:
    golden, rows = _unavailable_rows()
    report = evaluate_multisource_release_v2(golden, rows)
    assert report.case_count == 60
    assert report.reviewed_count == 0
    assert report.unavailable_count == 60
    assert report.qualified is False
    metric = {item.metric: item for item in report.metrics}
    assert metric["source_route_recall"].eligible == 60
    assert metric["source_route_recall"].evaluated == 0
    assert metric["source_route_recall"].availability is AvailabilityV2.UNAVAILABLE
    assert metric["counter_evidence_recall"].eligible == 4
    assert metric["unanswerable_acceptable"].eligible == 6


def test_released_evaluator_rejects_empty_deleted_duplicate_and_reordered_rows() -> None:
    golden, rows = _unavailable_rows()
    for tampered in ((), rows[:-1], (*rows[:-1], rows[0]), tuple(reversed(rows))):
        with pytest.raises(ValueError, match="membership"):
            evaluate_multisource_release_v2(golden, tampered)


def test_reviewed_row_rejects_self_reported_extra_truth_fields() -> None:
    _, rows = _unavailable_rows()
    payload = rows[0].model_dump(mode="json")
    payload["source_route_recall"] = 1.0
    with pytest.raises(ValueError):
        type(rows[0]).model_validate(payload)


def test_reviewed_rows_are_content_addressed_and_tamper_fail_closed() -> None:
    _, rows = _unavailable_rows()
    payload = rows[0].model_dump(mode="json")
    payload["failure_reason"] = "rewritten"
    with pytest.raises(ValueError, match="digest"):
        type(rows[0]).model_validate(json.loads(json.dumps(payload)))


def test_global_bundle_is_canonical_portable_and_tamper_fail_closed() -> None:
    golden, rows = _unavailable_rows()
    evaluation = evaluate_multisource_release_v2(golden, rows)
    security = evaluate_security_matrix_v2(build_security_matrix_v2())
    performance = build_performance_dashboard_v2(
        (),
        active_generations={},
        data_scale={"multisource_cases": 60},
        hardware_profile="isolated-test",
        concurrency=1,
        cache_state="cold",
    )
    source_truth = tuple(
        SourceReleaseTruthV2(
            source=source,
            engineering_complete=True,
            quality_qualified=False,
            disposition="QUALITY_HOLD",
            evidence_uri=None,
            evidence_sha256=None,
        )
        for source in MULTISOURCE_DOMAINS
    )
    release = evaluate_global_release_v2(
        evaluated_stage=ReleaseStageV2.OFFLINE,
        deployed_stage=ReleaseStageV2.OFFLINE,
        source_truth=source_truth,
        multisource_golden_sha256=golden.content_sha256,
        multisource_quality_available=False,
        multisource_quality_qualified=False,
        security_report=security,
        performance_dashboard=performance,
        rollback_rehearsal=build_rollback_rehearsal_v2(ReleaseStageV2.OFFLINE),
    )
    bundle = build_multisource_bundle_v2(
        golden=golden,
        evaluation=evaluation,
        security=security,
        performance=performance,
        release=release,
        retrieval_executed=False,
        production_observation=False,
    )
    encoded = serialize_multisource_bundle_v2(bundle)
    assert verify_multisource_bundle_v2(bytes(encoded)) == bundle
    tampered = json.loads(encoded)
    tampered["default_engine"] = "v2"
    with pytest.raises(ValueError):
        verify_multisource_bundle_v2(
            json.dumps(tampered, sort_keys=True, separators=(",", ":")).encode()
        )
