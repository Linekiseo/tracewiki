from dataclasses import replace

from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def test_golden_question_evaluation_and_audit(settings) -> None:
    with TestClient(create_app(settings)) as client:
        document = client.post(
            "/v1/documents/ingest",
            json={
                "title": "Evaluation report",
                "version": "v2",
                "content": "# Results\n\nClaim: The candidate reaches nDCG@10 of 0.842.",
            },
        ).json()
        claim = document["claims"][0]

        query = client.post(
            "/v1/search",
            json={"query": "nDCG@10 0.842", "sources": ["document"], "limit": 10},
        ).json()
        assert any(item["entity_id"] == claim["id"] for item in query["results"])

        case = client.post(
            "/v1/evaluation/cases",
            json={
                "name": "Reported nDCG value",
                "question": "nDCG@10 0.842",
                "expected_sources": ["document"],
                "expected_entity_ids": [claim["id"]],
                "required_version": "v2",
                "tags": ["claim", "version"],
            },
        ).json()
        assert case["display_key"].startswith("GQ-")

        evaluation = client.post("/v1/evaluation/runs", json={"case_ids": [case["id"]]})
        assert evaluation.status_code == 200
        payload = evaluation.json()
        assert payload["summary"]["pass_rate"] == 1.0
        assert payload["summary"]["version_accuracy"] == 1.0
        assert payload["results"][0]["passed"] == 1
        assert claim["id"] in payload["results"][0]["result_entity_ids"]

        stats = client.get("/v1/evaluation/stats").json()
        assert stats["cases"] == 1
        assert stats["runs"] == 1
        assert stats["avg_pass_rate"] == 1.0
        assert stats["queries"] == 1
        assert stats["query_latency_ms"] >= 0

        project = client.patch(
            "/v1/projects/by-id",
            params={"project_id": "project-rag"},
            json={
                "classification": "confidential",
                "settings": {
                    "retrieval": {"default_limit": 20},
                    "governance": {"require_relation_review": True},
                },
            },
            headers={"x-actor": "governance-test"},
        ).json()
        assert project["classification"] == "confidential"
        assert project["settings"]["governance"]["require_relation_review"] is True

        audits = client.get("/v1/audit/events").json()
        assert any(
            item["action"] == "api.patch"
            and item["actor"] == "governance-test"
            and item["resource_id"] == "/v1/projects/by-id"
            for item in audits
        )


def test_mutation_token_protects_governed_writes(settings) -> None:
    secured = replace(settings, api_token="secret-test-token")
    with TestClient(create_app(secured)) as client:
        denied = client.post("/v1/research/topics", json={"title": "Denied"})
        assert denied.status_code == 401
        allowed = client.post(
            "/v1/research/topics",
            json={"title": "Allowed"},
            headers={"Authorization": "Bearer secret-test-token"},
        )
        assert allowed.status_code == 201
