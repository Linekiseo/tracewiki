from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def _mcp(client: TestClient, token: str, name: str, arguments: dict) -> dict:
    response = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    return response.json()["result"]["structuredContent"]


def test_intelligence_turns_project_records_into_prioritized_research_actions(
    settings,
    sample_repository,
) -> None:
    with TestClient(create_app(settings)) as client:
        empty = client.get("/v1/workspace/intelligence").json()
        assert empty["stage"] == "define"
        assert empty["readiness"]["score"] == 0
        assert empty["next_actions"][0]["id"] == "create_topic"
        assert empty["source_health"]["repositories"]["total"] == 0

        undefined = client.post(
            "/v1/research/topics",
            json={"title": "尚待定义的研究方向"},
        ).json()
        undefined_state = client.get(
            "/v1/workspace/intelligence", params={"topic_id": undefined["id"]}
        ).json()
        assert undefined_state["stage"] == "define"
        assert undefined_state["next_actions"][0]["id"] == "define_research_question"

        topic = client.post(
            "/v1/research/topics",
            json={
                "title": "验证多源证据一致性",
                "problem_statement": "单一来源无法支撑研究结论。",
                "objective": "让代码、实验和文档形成交叉证据。",
                "status": "active",
            },
        ).json()
        iteration = client.post(
            "/v1/research/iterations",
            json={
                "topic_id": topic["id"],
                "title": "构建首轮交叉验证",
                "goal": "形成两类独立证据",
                "hypothesis": "代码与实验结果可以相互验证",
                "status": "active",
            },
        ).json()
        work = client.post(
            "/v1/research/work-items",
            json={
                "topic_id": topic["id"],
                "iteration_id": iteration["id"],
                "title": "执行一致性验证",
                "objective": "运行验证并回传真实产物",
                "acceptance_criteria": ["测试通过", "关联实验结果"],
                "status": "ready",
                "assignee_type": "codex",
                "assignee": "Codex",
            },
        ).json()
        ingestion = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{ingestion['workflow_id']}").json()[
                "status"
            ]
            == "completed"
        )

        executable = client.get(
            "/v1/workspace/intelligence",
            params={"topic_id": topic["id"], "iteration_id": iteration["id"]},
        ).json()
        assert executable["stage"] == "execute"
        assert executable["readiness"]["level"] == "executing"
        assert executable["work"]["states"] == {"ready": 1}
        assert executable["next_actions"][0]["id"] == "continue_work"

        code_entity = client.post(
            "/v1/evidence/search", json={"query": "HybridRanker", "limit": 1}
        ).json()["results"][0]
        code_link = client.post(
            "/v1/research/iteration-links",
            json={
                "iteration_id": iteration["id"],
                "entity_id": code_entity["entity_id"],
                "entity_type": code_entity["entity_type"],
                "source_type": "code",
                "role": "validation_evidence",
                "status": "verified",
            },
        )
        assert code_link.status_code == 201
        experiment = client.post(
            "/v1/experiments",
            json={
                "iteration_id": iteration["id"],
                "title": "交叉验证实验",
                "objective": "验证代码结果",
                "status": "running",
            },
        )
        assert experiment.status_code == 201

        reviewed = client.post(
            "/v1/research/work-items/transition",
            params={"work_item_id": work["id"]},
            json={"expected_version": work["version"], "status": "review"},
        )
        assert reviewed.status_code == 200

        relation = client.post(
            "/v1/relations",
            json={
                "source_entity_id": iteration["id"],
                "predicate": "validated_by",
                "target_entity_id": experiment.json()["id"],
                "evidence_entity_id": experiment.json()["id"],
                "derivation": "deterministic",
                "review_status": "unreviewed",
            },
        )
        assert relation.status_code == 201

        reviewable = client.get(
            "/v1/workspace/intelligence",
            params={"topic_id": topic["id"], "iteration_id": iteration["id"]},
        ).json()
        assert reviewable["stage"] == "review"
        assert reviewable["evidence"]["total"] == 2
        assert reviewable["evidence"]["source_types"] == 2
        assert reviewable["evidence"]["pending_review"] == 1
        action_ids = {item["id"] for item in reviewable["next_actions"]}
        assert {"review_work", "review_relations"} <= action_ids


def test_intelligence_is_available_to_codex_as_read_only_structured_context(
    settings,
) -> None:
    configured = replace(settings, mcp_token="intelligence-test-token")
    with TestClient(create_app(configured)) as client:
        context = _mcp(
            client,
            "intelligence-test-token",
            "project_get_context",
            {"project_id": "project-rag"},
        )
        assert context["intelligence"]["stage"] == "define"
        assert context["intelligence"]["next_actions"][0]["action"] == "create_topic"

        intelligence = _mcp(
            client,
            "intelligence-test-token",
            "project_get_intelligence",
            {"project_id": "project-rag"},
        )
        assert intelligence["readiness"]["level"] == "needs_definition"
        assert intelligence["generated_at"]


def test_intelligence_endpoint_preserves_project_acl(settings) -> None:
    protected = replace(settings, enforce_acl=True)
    with TestClient(create_app(protected)) as client:
        client.post(
            "/v1/projects",
            json={
                "id": "project-private",
                "name": "Private",
                "acl_ref": "team-private",
            },
            headers={"X-RAG-ACL-Refs": "team-private"},
        )
        denied = client.get(
            "/v1/workspace/intelligence",
            params={"project_id": "project-private"},
            headers={"X-RAG-ACL-Refs": "team-other"},
        )
        assert denied.status_code == 404
