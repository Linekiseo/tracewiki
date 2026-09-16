from fastapi.testclient import TestClient

from evidence_rag.api import create_app


def test_document_claim_extraction_and_run_validation(settings, sample_repository) -> None:
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
            "/v1/research/topics", json={"title": "Evidence quality", "status": "active"}
        ).json()
        iteration = client.post(
            "/v1/research/iterations",
            json={"topic_id": topic["id"], "title": "Validate reported metrics"},
        ).json()
        experiment = client.post(
            "/v1/experiments",
            json={"iteration_id": iteration["id"], "title": "NQ rerank evaluation"},
        ).json()
        run = client.post(
            "/v1/experiments/runs",
            json={
                "experiment_id": experiment["id"],
                "name": "candidate",
                "repository_id": repository["id"],
                "commit_sha": repository["head_commit"],
                "dataset_id": "dataset://nq",
                "dataset_version": "1.2",
                "metrics": [{"name": "ndcg@10", "value": 0.842}],
            },
        ).json()
        replicate = client.post(
            "/v1/experiments/runs",
            json={
                "experiment_id": experiment["id"],
                "name": "replicate-seed-43",
                "repository_id": repository["id"],
                "commit_sha": repository["head_commit"],
                "dataset_id": "dataset://nq",
                "dataset_version": "1.2",
                "config": {"seed": 43},
                "metrics": [{"name": "ndcg@10", "value": 0.842}],
            },
        ).json()

        document_response = client.post(
            "/v1/documents/ingest",
            json={
                "iteration_id": iteration["id"],
                "title": "Rerank evaluation report",
                "version": "v1.3",
                "content": (
                    "# Results\n\n"
                    "Claim: Cross-encoder reranking reaches nDCG@10 of 0.842 on NQ v1.2.\n\n"
                    "| Metric | Value | Run |\n"
                    "|---|---:|---|\n"
                    f"| nDCG@10 | 0.842 | {run['display_key']} |\n\n"
                    "# Limitations\n\n"
                    "结论：P95 latency increases by 7.4% under the same workload."
                ),
                "authors": ["Research Team"],
                "tags": ["rerank", "evaluation"],
            },
        )
        assert document_response.status_code == 201
        document = document_response.json()
        assert len(document["sections"]) == 2
        assert len(document["claims"]) == 2
        assert len(document["tables"]) == 1
        assert document["tables"][0]["cells"][4]["value"] == "0.842"
        assert all(item["extraction_method"] == "explicit_marker" for item in document["claims"])

        quality_claim = document["claims"][0]
        candidates = client.get(
            "/v1/documents/claims/match-candidates",
            params={"claim_id": quality_claim["id"], "review_status": "unreviewed"},
        ).json()
        candidate = next(item for item in candidates if item["run_id"] == run["id"])
        assert candidate["signals"]["numeric_exact"] is True
        options = client.get(
            "/v1/documents/evidence-options",
            params={"evidence_type": "metric", "query": "ndcg@10"},
        ).json()
        assert options[0]["evidence_type"] == "metric"

        reviewed = client.post(
            "/v1/documents/claims/match-candidates/review",
            params={"candidate_id": candidate["id"]},
            json={
                "decision": "confirmed",
                "relationship": "supports",
                "reviewer": "Evidence Reviewer",
                "note": "Metric, dataset and code version match the report.",
            },
        )
        assert reviewed.status_code == 200
        reviewed_payload = reviewed.json()
        validation = reviewed_payload["evidence"]["validation"]
        assert validation["status"] == "verified"
        assert all(item["passed"] for item in validation["checks"])

        evidence_id = reviewed_payload["evidence"]["evidence"]["id"]
        unlinked = client.post(
            "/v1/documents/claims/evidence/unlink",
            json={
                "claim_id": quality_claim["id"],
                "evidence_id": evidence_id,
                "actor": "Evidence Reviewer",
                "note": "Exercise reversible review history.",
            },
        ).json()
        assert unlinked["validation"]["status"] == "insufficient_evidence"
        relinked = client.post(
            "/v1/documents/claims/match-candidates/review",
            params={"candidate_id": candidate["id"]},
            json={"decision": "confirmed", "reviewer": "Evidence Reviewer"},
        ).json()
        assert relinked["evidence"]["validation"]["status"] == "verified"
        claim_history = client.get(
            "/v1/documents/claims/by-id", params={"claim_id": quality_claim["id"]}
        ).json()
        assert [event["action"] for event in claim_history["evidence_events"]] == [
            "linked",
            "unlinked",
            "linked",
        ]

        table_candidates = client.get(
            "/v1/documents/tables/metric-candidates",
            params={"claim_id": quality_claim["id"], "review_status": "unreviewed"},
        ).json()
        assert len(table_candidates) == 2
        table_candidate = next(item for item in table_candidates if item["run_id"] == run["id"])
        assert table_candidate["metric_name"] == "ndcg@10"
        assert table_candidate["cell_value"] == "0.842"
        assert table_candidate["signals"]["numeric_exact"] is True
        assert table_candidate["signals"]["claim_numeric_exact"] is True
        table_review = client.post(
            "/v1/documents/tables/metric-candidates/review",
            params={"candidate_id": table_candidate["id"]},
            json={
                "decision": "confirmed",
                "reviewer": "Evidence Reviewer",
                "note": "The table cell, metric definition and run identifier agree.",
            },
        )
        assert table_review.status_code == 200
        assert table_review.json()["claim_evidence"]["validation"]["status"] == "verified"
        relations = client.get("/v1/relations").json()
        assert any(
            edge["source_entity_id"] == table_candidate["table_cell_id"]
            and edge["predicate"] == "reports"
            and edge["target_entity_id"] == table_candidate["metric_id"]
            and edge["review_status"] == "confirmed"
            for edge in relations
        )
        assert any(
            edge["source_entity_id"] == run["id"]
            and edge["predicate"] == "reports"
            and edge["target_entity_id"] == table_candidate["metric_id"]
            for edge in relations
        )

        aggregation_response = client.post(
            "/v1/documents/tables/aggregations",
            json={
                "table_cell_id": table_candidate["table_cell_id"],
                "claim_id": quality_claim["id"],
                "metric_ids": [table_candidate["metric_id"], replicate["metrics"][0]["id"]],
                "aggregation_function": "mean",
                "actor": "Evidence Reviewer",
                "note": "Mean across two controlled seeds reported in the table.",
            },
        )
        assert aggregation_response.status_code == 201
        aggregation = aggregation_response.json()["aggregation"]
        assert aggregation["status"] == "verified"
        assert aggregation["sample_count"] == 2
        assert aggregation["computed_value"] == 0.842
        assert aggregation["variance"] == 0.0
        assert len(aggregation["run_ids"]) == 2
        repeated_aggregation = client.post(
            "/v1/documents/tables/aggregations",
            json={
                "table_cell_id": table_candidate["table_cell_id"],
                "claim_id": quality_claim["id"],
                "metric_ids": [table_candidate["metric_id"], replicate["metrics"][0]["id"]],
                "aggregation_function": "mean",
                "actor": "Evidence Reviewer",
            },
        ).json()
        assert repeated_aggregation["created"] is False
        assert repeated_aggregation["aggregation"]["id"] == aggregation["id"]
        saved_aggregations = client.get(
            "/v1/documents/tables/aggregations", params={"claim_id": quality_claim["id"]}
        ).json()
        assert saved_aggregations[0]["id"] == aggregation["id"]
        lineage = client.get(
            "/v1/lineage",
            params={"entity_id": aggregation["id"], "direction": "both", "depth": 2},
        ).json()
        assert {node["type"] for node in lineage["nodes"]} >= {
            "MetricAggregation",
            "Metric",
            "TableCell",
        }

        latency_claim = document["claims"][1]
        contradicted = client.post(
            "/v1/documents/claims/evidence",
            json={
                "claim_id": latency_claim["id"],
                "evidence_entity_id": run["id"],
                "evidence_type": "experiment_run",
                "relationship": "refutes",
                "note": "The linked run does not show the reported latency regression.",
            },
        ).json()
        assert contradicted["validation"]["status"] == "contradicted"

        claims = client.get("/v1/documents/claims").json()
        assert {item["status"] for item in claims} == {"verified", "contradicted"}
        stats = client.get("/v1/documents/stats").json()
        assert stats == {
            "documents": 1,
            "claims": 2,
            "verified": 1,
            "at_risk": 1,
            "evidence_links": 4,
        }

        links = client.get(
            "/v1/research/iteration-links", params={"iteration_id": iteration["id"]}
        ).json()
        assert {item["source_type"] for item in links} == {"document", "experiment"}
