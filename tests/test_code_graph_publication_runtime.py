from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.rag.sources.code import (
    CODE_GRAPH_PUBLICATION_VERSION,
    CodeTypedGraphRetriever,
)


def _commit_repository(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Graph Test"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "graph@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "graph"], check=True)


def test_ast_v2_can_publish_and_use_the_production_typed_graph(
    settings,
    sample_repository: Path,
) -> None:
    _commit_repository(sample_repository)
    app = create_app(
        replace(
            settings,
            rag_code_unit_builder="ast-v2",
            rag_code_graph=True,
            rag_code_context="structured-v2",
        )
    )
    with TestClient(app) as client:
        queued = client.post(
            "/v1/ingestion/repositories",
            json={"source": str(sample_repository)},
        )
        assert queued.status_code == 202
        workflow = client.get(f"/v1/ingestion/workflows/{queued.json()['workflow_id']}").json()
        assert workflow["status"] == "completed", workflow
        repository = next(
            item
            for item in client.get("/v1/repositories").json()
            if item["local_path"] == str(sample_repository.resolve())
        )

    publication = app.state.runtime.store.get_code_index_publication(
        repository["active_generation_id"],
        repository_id=repository["id"],
        active_only=True,
    )
    assert publication is not None
    assert publication["graph"] == CODE_GRAPH_PUBLICATION_VERSION
    assert publication["validation"]["capabilities"]["graph_retrieval"] is True
    graph = publication["validation"]["graph_publication"]
    assert graph["source"] == "production-ingestion-edges"
    assert graph["registered_edge_count"] > 0
    assert (
        graph["registered_edge_count"] + sum(graph["excluded_unregistered_edge_types"].values())
        == graph["edge_count"]
    )
    assert graph["endpoint_project_version_acl_integrity"] is True
    assert type(app.state.runtime.code_integration.v2_pipeline.graph_retriever) is (
        CodeTypedGraphRetriever
    )
