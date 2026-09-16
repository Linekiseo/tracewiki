from __future__ import annotations

import subprocess
from dataclasses import replace

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.code_history.models import TestResultCreate as HistoryTestResultCreate
from evidence_rag.models import EvidenceSearchRequest, RepositoryIngestRequest
from evidence_rag.runtime import create_runtime


def _git(root, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _git_repository(tmp_path):
    root = tmp_path / "history-repository"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Evidence Test")
    module = root / "ranker.py"
    module.write_text("def score(value):\n    return value\n", encoding="utf-8")
    _git(root, "add", "ranker.py")
    _git(root, "commit", "-m", "add scoring primitive")
    first = _git(root, "rev-parse", "HEAD")
    module.write_text("def score(value):\n    return max(0.0, min(1.0, value))\n", encoding="utf-8")
    _git(root, "add", "ranker.py")
    _git(root, "commit", "-m", "clamp ranking score")
    return root, first, _git(root, "rev-parse", "HEAD")


def test_git_history_diff_and_test_result_are_real_evidence(settings, tmp_path) -> None:
    repository_root, first_sha, head_sha = _git_repository(tmp_path)
    runtime = create_runtime(settings)
    request = RepositoryIngestRequest(source=str(repository_root), history_depth=10)
    workflow_id = runtime.ingestion.enqueue(request)
    runtime.ingestion.run(workflow_id, request)

    workflow = runtime.store.get_workflow(workflow_id)
    assert workflow and workflow["status"] == "completed"
    assert workflow["counters"]["commits"] == 2
    assert workflow["counters"]["diff_hunks"] >= 2
    repository = runtime.store.list_repositories()[0]
    commits = runtime.code_history.store.list_commits(repository["id"], 10)
    assert {item["sha"] for item in commits} == {first_sha, head_sha}
    detail = runtime.code_history.store.commit_detail(repository["id"], head_sha)
    assert detail and detail["parent_shas"] == [first_sha]
    assert "max(0.0" in "\n".join(item["patch"] for item in detail["diff_hunks"])

    test_result = runtime.code_history.record_test(
        HistoryTestResultCreate(
            repository_id=repository["id"],
            commit_sha=head_sha,
            command="pytest -q",
            status="passed",
            exit_code=0,
            framework="pytest",
        )
    )
    assert test_result["commit_id"] == detail["id"]
    assert runtime.workspace.store.entity_exists(test_result["id"])


def test_claim_numeric_validation_and_drift_use_indexed_git_diff(settings, tmp_path) -> None:
    repository_root, first_sha, head_sha = _git_repository(tmp_path)
    with TestClient(create_app(settings)) as client:
        workflow = client.post(
            "/v1/ingestion/repositories",
            json={"source": str(repository_root), "history_depth": 10},
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{workflow['workflow_id']}").json()["status"]
            == "completed"
        )
        repository = client.get("/v1/repositories").json()[0]
        experiment = client.post(
            "/v1/experiments", json={"title": "Historical scoring evaluation"}
        ).json()
        run = client.post(
            "/v1/experiments/runs",
            json={
                "experiment_id": experiment["id"],
                "name": "first-commit baseline",
                "repository_id": repository["id"],
                "commit_sha": first_sha,
                "dataset_id": "dataset://ranking",
                "dataset_version": "1",
                "metrics": [{"name": "ndcg@10", "value": 0.842}],
            },
        ).json()
        assert run["commit_entity_id"].endswith(first_sha)

        document = client.post(
            "/v1/documents/ingest",
            json={
                "title": "Historical score report",
                "content": (
                    "# Results\n\n"
                    "Claim: ranker.py score reaches nDCG@10 of 0.900 on ranking dataset v1."
                ),
            },
        ).json()
        claim = document["claims"][0]
        linked = client.post(
            "/v1/documents/claims/evidence",
            json={
                "claim_id": claim["id"],
                "evidence_entity_id": run["id"],
                "evidence_type": "experiment_run",
                "relationship": "supports",
            },
        ).json()
        assert linked["validation"]["status"] == "partially_supported"
        numeric = next(
            item
            for item in linked["validation"]["checks"]
            if item["check"] == "reported_numeric_value_matches"
        )
        assert numeric["passed"] is False

        assessment = client.post("/v1/drift/scan", json={}).json()["assessments"][0]
        assert assessment["status"] == "impacted"
        assert assessment["risk_score"] == 0.9
        assert assessment["metadata"]["commit_path"] == [head_sha]
        assert assessment["metadata"]["changed_paths"] == ["ranker.py"]


def test_raw_tombstone_removes_all_derived_code_candidates(settings, sample_repository) -> None:
    runtime = create_runtime(settings)
    request = RepositoryIngestRequest(source=str(sample_repository))
    workflow_id = runtime.ingestion.enqueue(request)
    runtime.ingestion.run(workflow_id, request)
    before = runtime.retriever.search(EvidenceSearchRequest(query="normalize_score", limit=20))
    target = next(item for item in before["results"] if item["path"] == "src/ranking.py")

    with runtime.store.connection() as db:
        raw = db.execute(
            """SELECT r.id FROM raw_objects r JOIN raw_derivations d ON d.raw_object_id=r.id
               WHERE d.derived_entity_id=?""",
            (target["entity_id"],),
        ).fetchone()
    assert raw
    tombstone = runtime.sources.store.tombstone(raw["id"], "source access revoked", "test")
    assert tombstone and tombstone["blocked_entities"] >= 1
    after = runtime.retriever.search(EvidenceSearchRequest(query="normalize_score", limit=20))
    assert target["entity_id"] not in {item["entity_id"] for item in after["results"]}


def test_acl_is_applied_before_search_and_detail_resolution(settings, sample_repository) -> None:
    protected = replace(settings, enforce_acl=True)
    with TestClient(create_app(protected)) as client:
        headers = {"x-rag-acl-refs": "team:ranking"}
        workflow = client.post(
            "/v1/ingestion/repositories",
            json={"source": str(sample_repository), "acl_ref": "team:ranking"},
            headers=headers,
        ).json()
        assert (
            client.get(
                f"/v1/ingestion/workflows/{workflow['workflow_id']}", headers=headers
            ).json()["status"]
            == "completed"
        )
        assert client.get(f"/v1/ingestion/workflows/{workflow['workflow_id']}").status_code == 404

        assert client.get("/v1/repositories").json() == []
        search = client.post("/v1/search", json={"query": "HybridRanker"}).json()
        assert search["results"] == []
        assert search["trace"]["acl_prefiltered"] is True

        repository = client.get("/v1/repositories", headers=headers).json()[0]
        assert (
            client.get("/v1/code/history", params={"repository_id": repository["id"]}).status_code
            == 404
        )
        assert (
            client.get(
                "/v1/code/history",
                params={"repository_id": repository["id"]},
                headers=headers,
            ).status_code
            == 200
        )
        authorized = client.post(
            "/v1/search", json={"query": "HybridRanker"}, headers=headers
        ).json()
        assert authorized["results"]
        path = next(item["path"] for item in authorized["results"] if item.get("path"))
        assert (
            client.get(
                "/v1/code/file",
                params={"repository_id": repository["id"], "path": path},
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/v1/code/file",
                params={"repository_id": repository["id"], "path": path},
                headers=headers,
            ).status_code
            == 200
        )
