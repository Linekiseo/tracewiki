from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def test_research_workspace_lifecycle(settings, sample_repository) -> None:
    with TestClient(create_app(settings)) as client:
        projects = client.get("/v1/projects").json()
        assert projects[0]["id"] == "project-rag"

        topic_response = client.post(
            "/v1/research/topics",
            json={
                "title": "提高代码与会话证据的关联精度",
                "problem_statement": "会话变更和代码 Symbol 尚未形成确定性证据链。",
                "objective": "让检索结果可回溯到研发意图。",
                "status": "active",
                "priority": 1,
                "tags": ["lineage", "codex"],
            },
        )
        assert topic_response.status_code == 201
        topic = topic_response.json()
        assert topic["display_key"].startswith("TOPIC-")

        iteration_response = client.post(
            "/v1/research/iterations",
            json={
                "topic_id": topic["id"],
                "title": "建立第一条确定性链路",
                "goal": "连接迭代与代码实体",
                "hypothesis": "稳定 URI 可以避免代际串线",
                "status": "active",
            },
        )
        assert iteration_response.status_code == 201
        iteration = iteration_response.json()

        ingest = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{ingest['workflow_id']}").json()["status"]
            == "completed"
        )
        entity = client.post(
            "/v1/evidence/search",
            json={"query": "HybridRanker", "limit": 1},
        ).json()["results"][0]

        link_response = client.post(
            "/v1/research/iteration-links",
            json={
                "iteration_id": iteration["id"],
                "entity_id": entity["entity_id"],
                "entity_type": entity["entity_type"],
                "source_type": "code",
                "role": "implementation_evidence",
            },
        )
        assert link_response.status_code == 201
        link = link_response.json()

        relation_response = client.post(
            "/v1/relations",
            json={
                "source_entity_id": iteration["id"],
                "predicate": "implemented_by",
                "target_entity_id": entity["entity_id"],
                "evidence_entity_id": entity["entity_id"],
                "derivation": "deterministic",
                "review_status": "unreviewed",
            },
        )
        assert relation_response.status_code == 201
        relation = relation_response.json()

        reviewed = client.post(
            "/v1/relations/review",
            params={"edge_id": relation["id"]},
            json={"review_status": "confirmed", "reviewer": "test-owner"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["review_status"] == "confirmed"

        updated = client.patch(
            "/v1/research/iterations/by-id",
            params={"iteration_id": iteration["id"]},
            json={"status": "completed", "summary": "链路已验证"},
        ).json()
        assert updated["progress"] == 100
        assert updated["completed_at"]

        dashboard = client.get("/v1/workspace/dashboard").json()
        assert dashboard["stats"]["topics"] == 1
        assert dashboard["stats"]["iterations"] == 1
        assert dashboard["stats"]["linked_evidence"] == 1
        assert dashboard["stats"]["relations"] == 1
        assert dashboard["recent_activity"]

        removed = client.delete(
            "/v1/research/iteration-links",
            params={"link_id": link["id"], "project_id": "project-rag"},
        )
        assert removed.status_code == 204


def test_workspace_rejects_cross_project_iteration(settings) -> None:
    with TestClient(create_app(settings)) as client:
        other = client.post("/v1/projects", json={"id": "project-other", "name": "Other"}).json()
        assert other["id"] == "project-other"
        topic = client.post(
            "/v1/research/topics",
            json={"project_id": "project-other", "title": "Other topic"},
        ).json()
        response = client.post(
            "/v1/research/iterations",
            json={
                "project_id": "project-rag",
                "topic_id": topic["id"],
                "title": "Invalid cross-project iteration",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "topic does not belong to project"


def test_workspace_delete_endpoints_preserve_audit_and_hide_active_records(settings) -> None:
    with TestClient(create_app(settings)) as client:
        topic = client.post(
            "/v1/research/topics", json={"title": "可删除研究主题"}
        ).json()
        iteration = client.post(
            "/v1/research/iterations",
            json={"topic_id": topic["id"], "title": "可删除研究迭代"},
        ).json()
        work_item = client.post(
            "/v1/research/work-items",
            json={
                "topic_id": topic["id"],
                "iteration_id": iteration["id"],
                "title": "可删除执行任务",
            },
        ).json()

        deleted_work = client.delete(
            "/v1/research/work-items/by-id",
            params={"work_item_id": work_item["id"], "expected_version": work_item["version"]},
        )
        assert deleted_work.status_code == 200
        assert deleted_work.json()["deleted"] is True
        assert deleted_work.json()["status"] == "cancelled"
        assert client.get("/v1/research/work-items").json() == []

        deleted_iteration = client.delete(
            "/v1/research/iterations/by-id",
            params={"iteration_id": iteration["id"]},
        )
        assert deleted_iteration.status_code == 200
        assert deleted_iteration.json()["status"] == "archived"
        assert client.get("/v1/research/iterations").json() == []

        deleted_topic = client.delete(
            "/v1/research/topics/by-id", params={"topic_id": topic["id"]}
        )
        assert deleted_topic.status_code == 200
        assert deleted_topic.json()["status"] == "archived"
        assert client.get("/v1/research/topics").json() == []

        actions = {
            event["action"]
            for event in client.get("/v1/audit/events", params={"project_id": "project-rag"}).json()
        }
        assert {"work_item.deleted", "iteration.deleted", "topic.deleted"} <= actions
