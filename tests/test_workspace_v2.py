from __future__ import annotations

import json

from fastapi.testclient import TestClient
from release_authority_helpers import install_workspace_v2_authority

from evidence_rag.api import create_app


def test_workspace_v2_control_plane_filters_relations_and_as_of(settings) -> None:
    app = create_app(settings)
    install_workspace_v2_authority(app.state.runtime)
    with TestClient(app) as client:
        topic = client.post(
            "/v1/research/topics",
            json={
                "title": "Multi-source retrieval",
                "objective": "Ship deterministic fusion",
                "status": "active",
                "owner": "RAG Core",
            },
        ).json()
        iteration = client.post(
            "/v1/research/iterations",
            json={
                "topic_id": topic["id"],
                "title": "Planner integration",
                "status": "blocked",
                "goal": "Connect all source retrievers",
            },
        ).json()
        work = client.post(
            "/v1/research/work-items",
            json={
                "topic_id": topic["id"],
                "iteration_id": iteration["id"],
                "title": "Resolve planner blocker",
                "objective": "Add missing role evidence",
                "status": "blocked",
                "assignee": "RAG Core",
            },
        ).json()
        relation = client.post(
            "/v1/relations",
            json={
                "source_entity_id": iteration["id"],
                "predicate": "blocked_by",
                "target_entity_id": work["id"],
                "evidence_entity_id": work["id"],
                "derivation": "deterministic",
                "review_status": "confirmed",
            },
        )
        assert relation.status_code == 201

        response = client.post(
            "/v1/search",
            headers={"X-RAG-Workspace-Engine": "v2"},
            json={
                "query": "blocker status:blocked",
                "sources": ["workspace"],
                "limit": 10,
                "as_of": "2099-01-01T00:00:00Z",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["trace"]["workspace_engine"] == "v2"
        assert payload["trace"]["source_engine_routing"]["workspace"] == {
            "requested": "v2",
            "selected": "v2",
            "served": "v2",
        }
        assert {item["status"] for item in payload["results"]} == {"blocked"}
        assert {item["entity_type"] for item in payload["results"]} >= {
            "ResearchIteration",
            "ResearchWorkItem",
        }
        context = payload["evidence_pack"]["source_contexts"]["workspace"]
        assert context["schema_version"] == "workspace-control-plane-context-v2"
        assert context["temporal_state"] == "UNAVAILABLE_WITH_AUDIT_EVIDENCE"
        assert context["confirmed_relations"][0]["predicate"] == "blocked_by"
        assert context["reasoning_included"] is False
        assert "/Users/" not in json.dumps(payload)

        assert (
            client.post(
                "/v1/search",
                headers={"X-RAG-Workspace-Engine": "invalid"},
                json={"query": "current", "sources": ["workspace"]},
            ).status_code
            == 422
        )
        default = client.post(
            "/v1/search",
            json={"query": "current", "sources": ["workspace"]},
        ).json()
        assert default["trace"]["workspace_engine"] == "v1"
        assert default["trace"]["source_engine_routing"]["workspace"] == {
            "requested": None,
            "selected": "v1",
            "served": "v1",
        }


def test_workspace_v2_project_acl_fails_closed(settings) -> None:
    app = create_app(settings)
    denied = app.state.runtime.workspace_v2.search(
        project_id="project-rag",
        query="current",
        allowed_acl_refs=[],
        enforce_acl=True,
    )
    assert denied["results"] == []
    assert denied["trace"]["denied_project"] is True
