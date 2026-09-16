from __future__ import annotations

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.rag.planner_v2 import (
    build_global_context,
    build_query_plan,
    calibrate_results,
    detect_conflicts_and_staleness,
)


def test_planner_routes_budgets_calibrates_within_source_and_detects_conflict() -> None:
    plan = build_query_plan(
        query="Compare experiment metric with report claim and notebook output",
        intent="claim_verification",
        requested_sources=[],
        limit=12,
        max_hops=3,
    )
    assert plan["routed_sources"][:3] == ["experiment", "document", "notebook"]
    assert plan["max_hops"] == 3
    assert plan["max_corrective_rounds"] == 2
    assert plan["reasoning_included"] is False
    assert all(plan["source_budgets"][source] >= 4 for source in plan["routed_sources"])

    rows = [
        {
            "entity_id": "doc-a",
            "source": "document",
            "title": "Quality result",
            "score": 100.0,
            "status": "verified",
            "authority": 0.9,
            "version_alignment": "not_applicable",
        },
        {
            "entity_id": "doc-b",
            "source": "document",
            "title": "Other",
            "score": 20.0,
            "status": "reported",
            "authority": 0.9,
            "version_alignment": "not_applicable",
        },
        {
            "entity_id": "run-a",
            "source": "experiment",
            "title": "Quality result",
            "score": 0.8,
            "status": "failed",
            "authority": 1.0,
            "version_alignment": "not_applicable",
        },
    ]
    calibrated = calibrate_results(rows, intent="claim_verification")
    assert all(0 <= item["score"] <= 1 for item in calibrated)
    assert all(
        item["fusion_explanation"]["raw_scores_cross_source_added"] is False for item in calibrated
    )
    assert (
        next(item for item in calibrated if item["entity_id"] == "doc-a")["fusion_explanation"][
            "source_rank"
        ]
        == 1
    )
    conflicts = detect_conflicts_and_staleness(calibrated)
    assert {item["kind"] for item in conflicts} == {
        "counter_or_stale",
        "cross_source_difference",
    }
    context = build_global_context(
        plan=plan,
        results=calibrated,
        source_contexts={},
        conflicts=conflicts,
    )
    assert context["schema_version"] == "global-comprehension-context-v2"
    assert context["reasoning_included"] is False
    assert context["content_digest"].startswith("sha256:")


def test_query_exposes_bounded_plan_corrective_trace_and_grounded_refusal(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/query",
            json={
                "question": "Verify the missing report claim",
                "intent": "claim_verification",
                "include": ["document"],
                "max_hops": 4,
                "mode": "evidence",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        pack = payload["evidence_pack"]
        plan = pack["query_plan"]
        assert plan["routed_sources"] == ["document"]
        assert plan["max_hops"] == 4
        assert pack["retrieval_context"]["global_context"]["plan_digest"] == plan["content_digest"]
        attempts = payload["trace_id"] and pack["trace"]
        assert attempts["fusion"] == "cross-source-diversified-v2"
        assert payload["answer"]["decision_status"] == "insufficient_evidence"
        assert payload["answer"]["refusal"] is True
        assert payload["answer"]["citations"] == []

        search = client.post(
            "/v1/search",
            json={
                "query": "missing report claim",
                "sources": ["document"],
                "max_hops": 4,
            },
        ).json()
        corrective = search["trace"]["planner"]["corrective_attempts"]
        assert len(corrective) <= 2
        # An explicitly requested source that returns no matching evidence is a
        # truthful terminal outcome, not permission to force an unrelated source.
        assert corrective == []
        assert search["trace"]["relation_expansion"]["max_hops"] == 4
        assert search["trace"]["release"]["decision"] == "HOLD_DEFAULT_V1"
