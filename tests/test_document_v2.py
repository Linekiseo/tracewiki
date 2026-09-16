from __future__ import annotations

import json

from fastapi.testclient import TestClient
from release_authority_helpers import install_document_v2_authority

from evidence_rag.api import create_app


def test_document_v2_typed_parent_context_and_filters(settings) -> None:
    app = create_app(settings)
    install_document_v2_authority(app.state.runtime)
    with TestClient(app) as client:
        document = client.post(
            "/v1/documents/ingest",
            json={
                "title": "Latency evaluation",
                "version": "v2",
                "content": (
                    "# Results\n\n"
                    "Claim: Candidate latency is 82 ms and accuracy is 91%.\n\n"
                    "| Metric | Value |\n|---|---:|\n| latency | 82 ms |\n| accuracy | 91% |\n\n"
                    "# Limits\n\nConclusion: Results require a larger validation set."
                ),
                "tags": ["latency", "evaluation"],
            },
        ).json()
        response = client.post(
            "/v1/search",
            headers={"X-RAG-Document-Engine": "v2"},
            json={
                "query": "table latency 82 ms",
                "sources": ["document"],
                "document_ids": [document["id"]],
                "limit": 10,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["trace"]["document_engine"] == "v2"
        assert payload["trace"]["source_engine_routing"]["document"] == {
            "requested": "v2",
            "selected": "v2",
            "served": "v2",
        }
        assert any(item["entity_type"] == "DocumentTableCell" for item in payload["results"])
        assert all(item["metadata"]["document_id"] == document["id"] for item in payload["results"])
        context = payload["evidence_pack"]["source_contexts"]["document"]
        assert context["schema_version"] == "document-evidence-context-v2"
        assert context["parents"][0]["document_id"] == document["id"]
        assert all(
            block["parent_id"]
            for block in context["blocks"]
            if block["entity_type"] != "ScientificDocument"
        )
        assert context["reasoning_included"] is False
        assert "/Users/" not in json.dumps(payload)

        default = client.post(
            "/v1/search",
            json={"query": "latency", "sources": ["document"], "limit": 5},
        ).json()
        assert default["trace"]["document_engine"] == "v1"
        assert default["trace"]["source_engine_routing"]["document"] == {
            "requested": None,
            "selected": "v1",
            "served": "v1",
        }
        assert (
            client.post(
                "/v1/search",
                headers={"X-RAG-Document-Engine": "future"},
                json={"query": "latency", "sources": ["document"]},
            ).status_code
            == 422
        )
