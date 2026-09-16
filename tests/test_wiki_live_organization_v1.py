from __future__ import annotations

from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def test_live_organization_previews_stages_and_publishes_current_project(settings) -> None:
    with TestClient(create_app(settings)) as client:
        preview_response = client.get(
            "/v1/wiki/organization/preview",
            params={"project_id": "project-rag"},
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview["organization_kind"] == "live_project_inventory"
        assert preview["active_snapshot_kind"] == "none"
        assert preview["candidate_count"] >= 1
        assert preview["page_count"] >= 3
        assert [item["source"] for item in preview["source_summaries"]] == [
            "code",
            "codex",
            "experiment",
            "notebook",
            "document",
            "workspace",
        ]
        workspace = preview["source_summaries"][-1]
        assert workspace["availability"] == "AVAILABLE"
        assert workspace["raw_entity_count"] == 1

        repeated = client.get(
            "/v1/wiki/organization/preview",
            params={"project_id": "project-rag"},
        )
        assert repeated.status_code == 200
        assert repeated.json()["generation_id"] == preview["generation_id"]
        assert repeated.json()["content_sha256"] == preview["content_sha256"]

        staged_response = client.post(
            "/v1/wiki/organization/stage",
            json={"project_id": "project-rag"},
        )
        assert staged_response.status_code == 202, staged_response.text
        staged = staged_response.json()
        assert staged["state"] == "STAGED_FOR_REVIEW"
        assert staged["published"] is False
        assert staged["generation_id"] == preview["generation_id"]
        assert (
            client.get("/v1/wiki/status?project_id=project-rag").json()["availability"]
            == "UNAVAILABLE"
        )

        published_response = client.post(
            "/v1/wiki/publish",
            json={
                "project_id": "project-rag",
                "generation_id": staged["generation_id"],
                "expected_manifest_sha256": staged["manifest_sha256"],
                "reviewer_authority_sha256": staged["reviewer_authority_sha256"],
            },
        )
        assert published_response.status_code == 200, published_response.text
        status = client.get("/v1/wiki/status?project_id=project-rag").json()
        assert status["availability"] == "AVAILABLE"
        assert status["active_generation_id"].startswith("wiki-live-")
        assert all(generation.startswith("live-") for _, generation in status["source_generations"])

        pages = client.get("/v1/wiki/pages?project_id=project-rag&limit=200").json()
        assert pages["total"] == preview["page_count"]
        assert any(page["page_type"] == "architecture" for page in pages["pages"])
        assert all(
            source_ref["locator"].startswith(
                (
                    "code://",
                    "codex://",
                    "experiment://",
                    "notebook://",
                    "document://",
                    "workspace://",
                )
            )
            for page in pages["pages"]
            for source_ref in page["source_refs"]
        )


def test_live_organization_rejects_unknown_project_before_staging(settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert (
            client.get(
                "/v1/wiki/organization/preview",
                params={"project_id": "missing"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/v1/wiki/organization/stage",
                json={"project_id": "missing"},
            ).status_code
            == 404
        )


def test_live_organization_consumes_current_code_and_codex_inventories(
    settings,
    sample_repository,
    sample_codex_home,
) -> None:
    with TestClient(create_app(settings)) as client:
        repository_ingest = client.post(
            "/v1/ingestion/repositories",
            json={"source": str(sample_repository)},
        )
        assert repository_ingest.status_code == 202, repository_ingest.text
        repository_workflow = client.get(
            f"/v1/ingestion/workflows/{repository_ingest.json()['workflow_id']}"
        ).json()
        assert repository_workflow["status"] == "completed"

        codex_ingest = client.post(
            "/v1/ingestion/codex",
            json={
                "source": str(sample_codex_home),
                "project_path": str(sample_repository),
            },
        )
        assert codex_ingest.status_code == 202, codex_ingest.text
        codex_workflow = client.get(
            f"/v1/ingestion/workflows/{codex_ingest.json()['workflow_id']}"
        ).json()
        assert codex_workflow["status"] == "completed"

        preview_response = client.get(
            "/v1/wiki/organization/preview",
            params={"project_id": "project-rag"},
        )
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        by_source = {item["source"]: item for item in preview["source_summaries"]}
        assert by_source["code"]["availability"] == "AVAILABLE"
        assert by_source["code"]["raw_entity_count"] == 3
        assert by_source["code"]["candidate_count"] >= 1
        assert by_source["codex"]["availability"] == "AVAILABLE"
        assert by_source["codex"]["raw_entity_count"] == 1
        assert by_source["codex"]["candidate_count"] == 1
        assert preview["candidate_count"] > 2
        assert preview["page_count"] > 4

        staged = client.post(
            "/v1/wiki/organization/stage",
            json={"project_id": "project-rag"},
        )
        assert staged.status_code == 202, staged.text
        assert (
            client.get("/v1/wiki/status?project_id=project-rag").json()["availability"]
            == "UNAVAILABLE"
        )
