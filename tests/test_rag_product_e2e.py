from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from release_authority_helpers import install_document_v2_authority

from evidence_rag.api import create_app
from evidence_rag.evaluation.product_replay_v2 import (
    PRODUCT_REPLAY_CONTRACT_VERSION,
    PRODUCT_REPLAY_LAYER,
    product_replay_qualification,
)
from evidence_rag.rag.multisource_foundation_v2 import (
    CalibrationObservationV2,
    fit_source_calibration_v2,
)
from evidence_rag.rag.multisource_runtime_v2 import (
    build_reviewed_calibration_artifact_v2,
    build_reviewed_calibration_bundle_v2,
)

TOKEN = "product-replay-production-token"
TRUSTED_ACL = "project:project-rag"
RAW_QUERY = (
    "Which experiment run metric supports the product replay report claim "
    "of accuracy 0.91 on dataset version 2026.07?"
)


@pytest.fixture
def production_app(settings) -> FastAPI:
    configured = replace(
        settings,
        deployment_mode="production",
        api_token=TOKEN,
        enforce_acl=True,
        trusted_acl_refs=(TRUSTED_ACL,),
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
    )
    return create_app(configured)


@pytest.fixture
def trusted_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-RAG-ACL-Refs": TRUSTED_ACL,
        "X-Request-ID": "product-replay-request",
    }


@pytest.fixture
def product_client(production_app: FastAPI):
    with TestClient(production_app) as client:
        yield client


@pytest.fixture
def seeded_product_sources(
    product_client: TestClient,
    trusted_headers: dict[str, str],
) -> dict[str, Any]:
    experiment_response = product_client.post(
        "/v1/experiments",
        headers=trusted_headers,
        json={
            "title": "Product replay qualification",
            "objective": "Validate the product replay report claim against a real metric.",
            "status": "completed",
        },
    )
    assert experiment_response.status_code == 201, experiment_response.text
    experiment = experiment_response.json()

    run_response = product_client.post(
        "/v1/experiments/runs",
        headers=trusted_headers,
        json={
            "experiment_id": experiment["id"],
            "external_id": "product-replay-run-001",
            "name": "product replay candidate",
            "status": "completed",
            "dataset_id": "dataset://product-replay",
            "dataset_version": "2026.07",
            "config": {"top_k": 20},
            "environment": {"runtime": "isolated-test"},
            "metrics": [
                {
                    "name": "accuracy",
                    "value": 0.91,
                    "unit": "ratio",
                    "split": "test",
                }
            ],
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()

    document_response = product_client.post(
        "/v1/documents/ingest",
        headers=trusted_headers,
        json={
            "title": "Product replay report",
            "version": "v1",
            "content": (
                "# Result\n\n"
                "Claim: Product replay accuracy is 0.91 on dataset version 2026.07.\n\n"
                f"The supporting experiment run is {run['display_key']}."
            ),
            "authors": ["Product Replay Test"],
            "extract_claims": True,
        },
    )
    assert document_response.status_code == 201, document_response.text
    document = document_response.json()
    assert document["claims"]
    return {"experiment": experiment, "run": run, "document": document}


def query_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question": RAW_QUERY,
        "intent": "claim_verification",
        "include": ["experiment", "document"],
        "mode": "answer",
        "max_hops": 1,
        "conversation_id": "product-replay-conversation",
        "client_turn_id": "product-replay-turn-1",
        "context_revision": 0,
        "clarification_policy": "auto",
        "deadline_ms": 5_000,
        "max_context_tokens": 2_048,
        "answer_format": "concise",
    }
    payload.update(updates)
    return payload


def search_payload() -> dict[str, Any]:
    return {
        "query": RAW_QUERY,
        "project_id": "project-rag",
        "sources": ["experiment", "document"],
        "limit": 12,
        "include_lineage": True,
        "max_hops": 1,
        "intent": "claim_verification",
    }


def assert_trace_is_request_safe(trace: dict[str, Any]) -> None:
    rendered = json.dumps(trace, ensure_ascii=False, sort_keys=True)
    assert RAW_QUERY not in rendered
    assert TOKEN not in rendered
    assert TRUSTED_ACL not in rendered
    assert "allowed_acl_refs" not in rendered.casefold()


def _write_reviewed_document_calibration(path) -> str:
    profile = fit_source_calibration_v2(
        "document",
        (
            CalibrationObservationV2(source="document", raw_score=0.05, relevant=False),
            CalibrationObservationV2(source="document", raw_score=0.2, relevant=False),
            CalibrationObservationV2(source="document", raw_score=0.7, relevant=True),
            CalibrationObservationV2(source="document", raw_score=0.95, relevant=True),
        ),
        bins=2,
    )
    artifact = build_reviewed_calibration_artifact_v2(
        source="document",
        artifact_id="reviewed-calibration://document/asgi-v1",
        dataset_sha256=profile.provenance_sha256,
        reviewer="product-replay-review-authority",
        reviewed_at="2026-07-30T00:00:00Z",
        profile=profile,
    )
    bundle = build_reviewed_calibration_bundle_v2({"document": artifact})
    path.write_text(
        json.dumps(
            bundle.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return bundle.content_sha256


def test_product_replay_qualification_is_explicitly_unavailable() -> None:
    qualification = product_replay_qualification()

    assert qualification == {
        "contract_version": PRODUCT_REPLAY_CONTRACT_VERSION,
        "layer": PRODUCT_REPLAY_LAYER,
        "status": "UNAVAILABLE",
        "required_trusted_cases": 60,
        "available_trusted_cases": 0,
        "quality_claim_permitted": False,
        "label_derived_candidate_generation_permitted": False,
        "persistent_eval_run_permitted": False,
        "reason": (
            "No independently reviewed 60-case production replay dataset is available. "
            "This contract qualifies only isolated L3 ASGI smoke and HTTP behavior."
        ),
    }


def test_reviewed_calibration_makes_explicit_global_v2_reachable_over_asgi(
    settings,
    trusted_headers: dict[str, str],
    tmp_path,
) -> None:
    calibration_path = (tmp_path / "reviewed-calibration.json").resolve()
    calibration_sha256 = _write_reviewed_document_calibration(calibration_path)
    app = create_app(
        replace(
            settings,
            deployment_mode="production",
            api_token=TOKEN,
            enforce_acl=True,
            trusted_acl_refs=(TRUSTED_ACL,),
            rag_multisource_calibration_bundle=calibration_path,
            rag_multisource_calibration_sha256=calibration_sha256,
        )
    )
    install_document_v2_authority(app.state.runtime)

    with TestClient(app) as client:
        document = client.post(
            "/v1/documents/ingest",
            headers=trusted_headers,
            json={
                "title": "Reviewed global V2 report",
                "content": "# Result\n\nClaim: Orion evidence requires reviewed support.",
                "extract_claims": True,
            },
        )
        assert document.status_code == 201, document.text
        response = client.post(
            "/v1/search",
            headers={**trusted_headers, "X-RAG-Engine": "v2"},
            json={
                "query": "Verify the Orion claim",
                "project_id": "project-rag",
                "sources": ["document"],
                "limit": 10,
                "intent": "claim_verification",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["trace"]["global_engine"]["selected"] == "v2"
    assert payload["trace"]["global_engine"]["served"] == "v2"
    assert payload["trace"]["global_engine"]["fallback"] is None
    assert payload["trace"]["global_engine"]["quality_qualified"] is False
    assert payload["trace"]["release"]["decision"] == "HOLD_DEFAULT_V1"
    assert payload["results"]
    assert {item["source"] for item in payload["results"]} == {"document"}
    assert_trace_is_request_safe(payload["trace"])


def test_production_query_requires_auth_and_trusted_acl(
    product_client: TestClient,
    trusted_headers: dict[str, str],
) -> None:
    unauthenticated = product_client.post("/v1/query", json=query_payload())
    untrusted = product_client.post(
        "/v1/query",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-RAG-ACL-Refs": "project:untrusted",
        },
        json=query_payload(),
    )
    authenticated = product_client.post(
        "/v1/query",
        headers=trusted_headers,
        json=query_payload(question="What evidence exists for product replay?"),
    )

    assert unauthenticated.status_code == 401
    assert untrusted.status_code == 403
    assert authenticated.status_code == 200
    assert authenticated.json()["state"] in {"refused", "retrieval_only"}


def test_clarification_returns_before_real_retrieval(
    production_app: FastAPI,
    trusted_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_spy = Mock(wraps=production_app.state.runtime.platform.search)
    monkeypatch.setattr(production_app.state.runtime.platform, "search", search_spy)

    with TestClient(production_app) as client:
        response = client.post(
            "/v1/query",
            headers=trusted_headers,
            json={
                "question": "那旧版本呢？",
                "conversation_id": "product-replay-conversation",
                "client_turn_id": "product-replay-turn-2",
                "clarification_policy": "auto",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "needs_clarification"
    assert payload["answer"]["status"] == "needs_clarification"
    assert payload["interaction"]["clarification"]["resume_required"] is True
    search_spy.assert_not_called()


def test_real_sources_replay_through_default_v1_query_contract(
    product_client: TestClient,
    trusted_headers: dict[str, str],
    seeded_product_sources: dict[str, Any],
) -> None:
    response = product_client.post(
        "/v1/query",
        headers=trusted_headers,
        json=query_payload(),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["query_id"].startswith("global-query://")
    assert payload["state"] in {"retrieval_only", "refused"}
    assert payload["answer"]["answer_mode"] in {"retrieval_only", "refusal"}
    assert payload["answer"]["refusal"] is (payload["state"] == "refused")
    assert payload["evidence_pack"]["citation_map"]
    assert set(payload["answer"]["citations"]) <= set(payload["evidence_pack"]["citation_map"])
    assert {"experiment", "document"} <= set(payload["source_status"])
    assert all(
        item["status"] in {"complete", "no_matching_evidence"}
        for item in payload["source_status"].values()
    )

    trace = payload["evidence_pack"]["trace"]
    release = trace["platform"]["release"]
    assert release["decision"] == "HOLD_DEFAULT_V1"
    assert set(release["default_engines"].values()) == {"v1"}
    assert trace["generation"]["attempted"] is False
    assert trace["evidence_safety"]["checked_full_string"] is True
    assert trace["evidence_safety"]["generation_blocked"] is False
    assert trace["planner"]
    assert trace["sources"]
    assert "global_engine" not in trace["platform"]

    assert_trace_is_request_safe(trace)


def test_real_source_timeout_is_partial_not_502(
    production_app: FastAPI,
    trusted_headers: dict[str, str],
    seeded_product_sources: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_retrieve = production_app.state.runtime.platform._retrieve_source

    def timeout_document(*args: Any, **kwargs: Any) -> dict[str, Any]:
        source = kwargs.get("source")
        if source == "document":
            raise TimeoutError("document source timed out")
        return original_retrieve(*args, **kwargs)

    monkeypatch.setattr(
        production_app.state.runtime.platform,
        "_retrieve_source",
        timeout_document,
    )
    with TestClient(production_app) as client:
        response = client.post(
            "/v1/query",
            headers=trusted_headers,
            json=query_payload(),
        )

    assert response.status_code == 200, response.text
    assert response.status_code != 502
    payload = response.json()
    assert payload["state"] == "partial"
    assert payload["answer"]["status"] == "partial"
    assert payload["source_status"]["document"]["status"] == "timeout"
    assert payload["source_status"]["experiment"]["status"] in {
        "complete",
        "no_matching_evidence",
    }
    assert payload["evidence_pack"]["trace"]["generation"]["attempted"] is False
    assert "document source timed out" not in json.dumps(
        payload["evidence_pack"]["trace"],
        ensure_ascii=False,
    )


def test_global_engine_header_defaults_to_v1_and_invalid_values_are_422(
    production_app: FastAPI,
    trusted_headers: dict[str, str],
    seeded_product_sources: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_spy = Mock(wraps=production_app.state.runtime.platform.search)
    monkeypatch.setattr(production_app.state.runtime.platform, "search", legacy_spy)

    with TestClient(production_app) as client:
        default_response = client.post(
            "/v1/search",
            headers=trusted_headers,
            json=search_payload(),
        )
        invalid_search = client.post(
            "/v1/search",
            headers={**trusted_headers, "X-RAG-Engine": "latest"},
            json=search_payload(),
        )
        invalid_query = client.post(
            "/v1/query",
            headers={**trusted_headers, "X-RAG-Engine": "latest"},
            json=query_payload(),
        )

    assert default_response.status_code == 200, default_response.text
    default_payload = default_response.json()
    assert default_payload["results"]
    assert "global_engine" not in default_payload["trace"]
    assert default_payload["trace"]["release"]["decision"] == "HOLD_DEFAULT_V1"
    assert set(default_payload["trace"]["release"]["default_engines"].values()) == {"v1"}
    assert_trace_is_request_safe(default_payload["trace"])
    assert invalid_search.status_code == 422
    assert invalid_query.status_code == 422
    assert "X-RAG-Engine" in invalid_search.json()["detail"]
    assert "X-RAG-Engine" in invalid_query.json()["detail"]
    legacy_spy.assert_called_once()


def test_explicit_global_v2_search_fails_closed_to_one_legacy_execution(
    production_app: FastAPI,
    trusted_headers: dict[str, str],
    seeded_product_sources: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_spy = Mock(wraps=production_app.state.runtime.platform.search)
    monkeypatch.setattr(production_app.state.runtime.platform, "search", legacy_spy)

    with TestClient(production_app) as client:
        response = client.post(
            "/v1/search",
            headers={**trusted_headers, "X-RAG-Engine": "v2"},
            json=search_payload(),
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["results"]
    routing = payload["trace"]["global_engine"]
    assert routing["requested"] == "v2"
    assert routing["selected"] == "v1"
    assert routing["fallback"] == "legacy_v1"
    assert routing["quality_qualified"] is False
    assert any(blocker.startswith("calibration_unavailable:") for blocker in routing["blockers"])
    assert_trace_is_request_safe(payload["trace"])
    legacy_spy.assert_called_once()


def test_explicit_global_v2_query_fallback_keeps_trusted_interaction_http_200(
    production_app: FastAPI,
    trusted_headers: dict[str, str],
    seeded_product_sources: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_spy = Mock(wraps=production_app.state.runtime.platform.search)
    monkeypatch.setattr(production_app.state.runtime.platform, "search", legacy_spy)

    with TestClient(production_app) as client:
        response = client.post(
            "/v1/query",
            headers={**trusted_headers, "X-RAG-Engine": "v2"},
            json=query_payload(),
        )

    assert response.status_code == 200, response.text
    assert response.status_code != 502
    payload = response.json()
    assert payload["state"] in {"retrieval_only", "partial"}
    assert payload["answer"]["answer_mode"] in {"retrieval_only", "partial"}
    assert payload["evidence_pack"]["citation_map"]
    assert set(payload["answer"]["citations"]) <= set(payload["evidence_pack"]["citation_map"])
    trace = payload["evidence_pack"]["trace"]
    routing = trace["platform"]["global_engine"]
    assert routing["requested"] == "v2"
    assert routing["selected"] == "v1"
    assert routing["fallback"] == "legacy_v1"
    assert routing["quality_qualified"] is False
    assert any(blocker.startswith("calibration_unavailable:") for blocker in routing["blockers"])
    assert trace["generation"]["attempted"] is False
    assert_trace_is_request_safe(trace)
    legacy_spy.assert_called_once()
