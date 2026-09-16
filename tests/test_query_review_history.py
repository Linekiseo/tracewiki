from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def test_query_preview_is_zero_retrieval_and_sanitized(settings, monkeypatch) -> None:
    app = create_app(settings)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("preview performed retrieval or scope-store resolution")

    monkeypatch.setattr(app.state.runtime.platform, "search", forbidden)
    monkeypatch.setattr(app.state.runtime.platform, "resolve_scope", forbidden)
    payload = {
        "question": "Inspect /Users/private/repo with api_key=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "intent": "current_implementation",
        "include": ["code", "codex"],
        "scope": {
            "project_id": "project-rag",
            "repository_ids": ["repo-safe", "/workspace/private-repo"],
            "allowed_acl_refs": ["secret-acl"],
        },
        "max_evidence": 12,
        "max_context_tokens": 2048,
        "deadline_ms": 5000,
    }
    with TestClient(app) as client:
        response = client.post("/v1/query/preview", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "query-plan-preview-v1"
    assert body["retrieval_performed"] is False
    assert body["source_waves"] == [
        {
            "wave": 1,
            "execution_mode": "parallel",
            "sources": ["code", "codex"],
            "budgets": {"code": 12, "codex": 12},
        }
    ]
    assert body["required_roles"] == ["current_code", "version", "tests"]
    assert body["generation"]["policy"] == "grounded_only"
    serialized = response.text
    assert "/Users/" not in serialized
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in serialized
    assert "allowed_acl_refs" not in serialized
    assert "question" not in serialized
    assert "/workspace/" not in serialized


def test_query_history_persists_across_runtime_reopen_and_enforces_project_acl(settings) -> None:
    protected = replace(settings, enforce_acl=True)
    allowed = {"X-RAG-ACL-Refs": "project:project-rag"}
    denied = {"X-RAG-ACL-Refs": "project:other"}
    query = {
        "question": "那 /Users/private/repo 的旧版本 token=super-secret 呢？",
        "scope": {"project_id": "project-rag"},
        "conversation_id": "review-conversation",
        "client_turn_id": "turn-2",
    }

    first_app = create_app(protected)
    with TestClient(first_app) as client:
        answer = client.post("/v1/query", json=query, headers=allowed)
        assert answer.status_code == 200
        assert answer.json()["interaction_state"] == "needs_clarification"

    reopened_app = create_app(protected)
    with TestClient(reopened_app) as client:
        denied_response = client.get(
            "/v1/query/history?project_id=project-rag&limit=10", headers=denied
        )
        history = client.get("/v1/query/history?project_id=project-rag&limit=10", headers=allowed)

    assert denied_response.status_code == 404
    assert history.status_code == 200
    body = history.json()
    assert body["contract_version"] == "query-history-v1"
    assert body["project_id"] == "project-rag"
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["interaction_state"] == "needs_clarification"
    assert item["question_digest"].startswith("sha256:")
    assert item["evidence"] == {
        "citation_ids": [],
        "sources": [],
        "entity_ids": [],
        "count": 0,
    }
    serialized = history.text
    assert "/Users/" not in serialized
    assert "super-secret" not in serialized
    assert "allowed_acl_refs" not in serialized
    assert '"question"' not in serialized
