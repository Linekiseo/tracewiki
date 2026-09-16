from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def test_version_bound_runs_and_comparison(settings, sample_repository) -> None:
    with TestClient(create_app(settings)) as client:
        workflow = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{workflow['workflow_id']}").json()["status"]
            == "completed"
        )
        repository = client.get("/v1/repositories").json()[0]

        topic = client.post(
            "/v1/research/topics", json={"title": "Rerank quality", "status": "active"}
        ).json()
        iteration = client.post(
            "/v1/research/iterations",
            json={"topic_id": topic["id"], "title": "Compare top-k", "status": "active"},
        ).json()
        experiment_response = client.post(
            "/v1/experiments",
            json={
                "iteration_id": iteration["id"],
                "title": "Top-k controlled comparison",
                "objective": "Measure ranking quality under a single config change",
                "status": "running",
            },
        )
        assert experiment_response.status_code == 201
        experiment = experiment_response.json()

        common = {
            "experiment_id": experiment["id"],
            "repository_id": repository["id"],
            "commit_sha": repository["head_commit"],
            "dataset_id": "dataset://nq",
            "dataset_version": "1.2",
            "environment": {"python": "3.13", "device": "cpu"},
            "status": "completed",
        }
        baseline = client.post(
            "/v1/experiments/runs",
            json={
                **common,
                "external_id": "mlflow-run-001",
                "name": "baseline",
                "config": {"top_k": 100, "threshold": 0.15},
                "metrics": [
                    {"name": "ndcg@10", "value": 0.8, "split": "test"},
                    {
                        "name": "p95_ms",
                        "value": 116.0,
                        "unit": "ms",
                        "higher_is_better": False,
                    },
                ],
            },
        ).json()
        candidate = client.post(
            "/v1/experiments/runs",
            json={
                **common,
                "external_id": "mlflow-run-002",
                "name": "candidate",
                "config": {"top_k": 200, "threshold": 0.15},
                "metrics": [
                    {"name": "ndcg@10", "value": 0.842, "split": "test"},
                    {
                        "name": "p95_ms",
                        "value": 124.6,
                        "unit": "ms",
                        "higher_is_better": False,
                    },
                ],
                "artifacts": [
                    {
                        "name": "evaluation.json",
                        "uri": "s3://experiments/evaluation.json",
                        "kind": "evaluation",
                        "checksum": "sha256:abc",
                    }
                ],
            },
        ).json()
        assert baseline["commit_entity_id"]
        assert candidate["commit_entity_id"] == baseline["commit_entity_id"]
        assert candidate["artifacts"][0]["checksum"] == "sha256:abc"

        comparison_response = client.post(
            "/v1/experiments/comparisons",
            json={
                "run_ids": [baseline["id"], candidate["id"]],
                "baseline_run_id": baseline["id"],
                "name": "top_k 100 vs 200",
            },
        )
        assert comparison_response.status_code == 201
        comparison = comparison_response.json()
        assert comparison["result"]["version_complete"] is True
        assert all(item["consistent"] for item in comparison["result"]["controls"])
        assert comparison["result"]["config_differences"] == [
            {"key": "top_k", "baseline": 100, "candidates": [200]}
        ]
        ndcg = next(item for item in comparison["result"]["metrics"] if item["name"] == "ndcg@10")
        assert round(ndcg["candidates"][0]["delta"], 3) == 0.042

        stats = client.get("/v1/experiments/stats").json()
        assert stats == {
            "experiments": 1,
            "runs": 2,
            "completed_runs": 2,
            "version_bound_runs": 2,
            "metrics": 4,
        }
        relations = client.get("/v1/relations").json()
        assert len([item for item in relations if item["predicate"] == "uses"]) == 2

        links = client.get(
            "/v1/research/iteration-links", params={"iteration_id": iteration["id"]}
        ).json()
        assert links[0]["entity_id"] == experiment["id"]
        assert links[0]["source_type"] == "experiment"
