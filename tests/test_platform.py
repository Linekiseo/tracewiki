from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from evidence_rag.api import create_app
from evidence_rag.models import CodexSearchScope
from evidence_rag.platform.models import (
    ExperimentAnalysisRequestV2,
    GlobalSearchRequest,
    NotebookComparisonRequestV2,
    NotebookComparisonSelectorV2,
    NotebookQuerySpecV2,
    NotebookQueryTaskV2,
)
from evidence_rag.platform.service import PlatformService, PlatformTypedRequestError
from evidence_rag.rag.sources.codex.runtime_v2 import CodexSourceScopeErrorV2


def test_typed_source_specs_reject_implicit_analysis_and_comparison() -> None:
    with pytest.raises(ValueError, match="baseline_run_id"):
        ExperimentAnalysisRequestV2(
            task="compare",
            metric="accuracy",
            candidate_run_ids=["candidate"],
            split="validation",
        )
    with pytest.raises(ValueError, match="aggregation"):
        ExperimentAnalysisRequestV2(
            task="aggregate",
            metric="accuracy",
            run_ids=["run-1", "run-2"],
            split="validation",
        )
    with pytest.raises(ValueError, match="roles"):
        ExperimentAnalysisRequestV2(task="reproduce", run_ids=["run-1"])
    with pytest.raises(ValueError, match="explicit baseline"):
        NotebookQuerySpecV2(task="compare")


def test_notebook_task_contract_is_closed_and_frozen() -> None:
    assert tuple(task.value for task in NotebookQueryTaskV2) == (
        "search",
        "compare",
        "output",
        "error",
        "reproduction",
        "code",
        "lineage",
        "parameter",
    )
    with pytest.raises(ValueError):
        NotebookQuerySpecV2(task="locate")

    spec = NotebookQuerySpecV2(task=NotebookQueryTaskV2.OUTPUT)
    with pytest.raises(ValidationError, match="frozen"):
        spec.task = NotebookQueryTaskV2.SEARCH


def _notebook_comparison_spec() -> NotebookComparisonRequestV2:
    return NotebookComparisonRequestV2(
        baseline=NotebookComparisonSelectorV2(
            template_id="template-1",
            revision_id="revision-1",
            execution_id="execution-1",
        ),
        candidate=NotebookComparisonSelectorV2(
            template_id="template-1",
            revision_id="revision-2",
            execution_id="execution-2",
        ),
    )


@pytest.mark.parametrize("task", tuple(NotebookQueryTaskV2))
def test_platform_sends_the_real_notebook_task_to_the_source_runtime(
    task: NotebookQueryTaskV2,
) -> None:
    class Facade:
        calls: list[dict[str, Any]] = []

        @classmethod
        def search(cls, **kwargs: Any) -> dict[str, Any]:
            cls.calls.append(kwargs)
            return {
                "results": [],
                "context": {},
                "comparisons": [],
                "selection_trace": {},
                "index_generation": ["notebook-generation-1"],
                "trace": {
                    "source_status": "no_match",
                    "task": kwargs["task"],
                    "index_generation": ["notebook-generation-1"],
                },
            }

    class SourceRuntime:
        @staticmethod
        @contextmanager
        def operation(source: str):
            assert source == "notebook"
            yield Facade()

    service = object.__new__(PlatformService)
    service.source_runtime_v2 = SourceRuntime()
    spec = NotebookQuerySpecV2(
        task=task,
        comparison=(_notebook_comparison_spec() if task is NotebookQueryTaskV2.COMPARE else None),
    )

    response = service._retrieve_source_engine(
        source="notebook",
        request=GlobalSearchRequest(
            query="neutral downstream query",
            sources=["notebook"],
            notebook_query=spec,
        ),
        limit=8,
        engine_requested="v2",
    )

    assert response["trace"]["status"] == "no_match"
    assert len(Facade.calls) == 1
    sent = Facade.calls[0]
    assert sent["task"] is task
    assert "governed_notebook_task" not in sent["query"]


@pytest.mark.parametrize("forged_task", (None, "locate", "trend"))
def test_platform_rejects_forged_notebook_task_before_source_invocation(
    forged_task: object,
) -> None:
    class NoSourceInvocation:
        @staticmethod
        @contextmanager
        def operation(_source: str):
            pytest.fail("forged Notebook task reached the source runtime")
            yield

    spec = NotebookQuerySpecV2()
    object.__setattr__(spec, "task", forged_task)
    service = object.__new__(PlatformService)
    service.source_runtime_v2 = NoSourceInvocation()

    with pytest.raises(PlatformTypedRequestError, match="notebook_task_invalid"):
        service._retrieve_source_engine(
            source="notebook",
            request=GlobalSearchRequest(
                query="neutral downstream query",
                sources=["notebook"],
                notebook_query=spec,
            ),
            limit=8,
            engine_requested="v2",
        )


def test_codex_v2_rejects_forged_direct_scope_projection_before_source_io() -> None:
    class NoIoStore:
        @staticmethod
        def list_codex_threads(**_kwargs):
            pytest.fail("forged scope must fail before production source I/O")

    service = object.__new__(PlatformService)
    request = GlobalSearchRequest(
        query="validation",
        project_id="project-rag",
        sources=["codex"],
        thread_ids=["thread-authorized"],
        date_from="2026-07-30T00:00:00Z",
        date_to="2026-07-30T01:00:00Z",
        allowed_acl_refs=["project:rag"],
        enforce_acl=True,
    )
    forged = CodexSearchScope(
        project_id="project-forged",
        thread_ids=["thread-authorized"],
        item_types=["ValidationResult"],
        statuses=["passed"],
        date_from=request.date_from,
        date_to=request.date_to,
        allowed_acl_refs=request.allowed_acl_refs,
        enforce_acl=request.enforce_acl,
    )

    with pytest.raises(CodexSourceScopeErrorV2) as captured:
        service._search_codex_source_v2_with_facade(
            SimpleNamespace(store=NoIoStore()),
            request,
            limit=8,
            codex_scope=forged,
        )
    assert str(captured.value) == "scope_projection_mismatch"


def test_global_search_lineage_and_commit_drift(settings, sample_repository) -> None:
    with TestClient(create_app(settings)) as client:
        first_workflow = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{first_workflow['workflow_id']}").json()["status"]
            == "completed"
        )
        repository = client.get("/v1/repositories").json()[0]
        project_repositories = client.get(
            "/v1/repositories", params={"project_id": repository["project_id"]}
        )
        assert project_repositories.status_code == 200
        assert [item["id"] for item in project_repositories.json()] == [repository["id"]]
        original_commit = repository["head_commit"]

        experiment = client.post("/v1/experiments", json={"title": "Ranking evaluation"}).json()
        run = client.post(
            "/v1/experiments/runs",
            json={
                "experiment_id": experiment["id"],
                "name": "NQ candidate",
                "repository_id": repository["id"],
                "commit_sha": original_commit,
                "dataset_id": "dataset://nq",
                "dataset_version": "1.2",
                "metrics": [{"name": "ndcg@10", "value": 0.842}],
            },
        ).json()
        document = client.post(
            "/v1/documents/ingest",
            json={
                "title": "Ranking report",
                "content": "# Results\n\nClaim: Hybrid ranking reaches nDCG@10 of 0.842 on NQ v1.2.",
            },
        ).json()
        claim = document["claims"][0]
        client.post(
            "/v1/documents/claims/evidence",
            json={
                "claim_id": claim["id"],
                "evidence_entity_id": run["id"],
                "evidence_type": "experiment_run",
                "relationship": "supports",
            },
        )

        search = client.post(
            "/v1/search",
            json={
                "query": "nDCG@10 0.842",
                "sources": ["document", "experiment"],
                "limit": 10,
            },
        )
        assert search.status_code == 200
        payload = search.json()
        assert {item["source"] for item in payload["results"]} == {
            "document",
            "experiment",
        }
        assert any(item["entity_id"] == claim["id"] for item in payload["results"])
        assert payload["evidence_pack"]["citation_map"]

        code_graph = client.get("/v1/graph", params={"domain": "code", "limit": 24}).json()
        assert code_graph["nodes"]
        assert code_graph["edges"]
        assert code_graph["metadata"]["vector_views"] > 0
        assert {"Repository", "Commit", "FileVersion"} <= {
            node["type"] for node in code_graph["nodes"]
        }
        structured_code_nodes = [
            node for node in code_graph["nodes"] if node["type"] in {"FileVersion", "CodeSymbol"}
        ]
        assert structured_code_nodes
        assert all(node["repository_id"] == repository["id"] for node in structured_code_nodes)
        assert any(node.get("path") for node in structured_code_nodes)
        assert any(
            node.get("metadata", {}).get("kind")
            for node in structured_code_nodes
            if node["type"] == "CodeSymbol"
        )
        degrees = Counter(
            endpoint
            for edge in code_graph["edges"]
            for endpoint in (edge["source"], edge["target"])
        )
        expandable = next(node_id for node_id, degree in degrees.most_common() if degree > 1)
        first_page = client.get(
            "/v1/graph/neighbors",
            params={"entity_id": expandable, "cursor": 0, "limit": 1},
        ).json()
        assert len(first_page["edges"]) == 1
        assert first_page["has_more"] is True
        assert first_page["next_cursor"] == 1
        second_page = client.get(
            "/v1/graph/neighbors",
            params={
                "entity_id": expandable,
                "cursor": first_page["next_cursor"],
                "limit": 1,
            },
        ).json()
        assert len(second_page["edges"]) == 1
        assert second_page["edges"][0]["id"] != first_page["edges"][0]["id"]
        assert second_page["next_cursor"] == 2
        assert expandable in {node["id"] for node in second_page["nodes"]}
        semantic_graph = client.get(
            "/v1/graph",
            params={
                "domain": "code",
                "mode": "semantic",
                "query": "HybridRanker",
                "limit": 18,
            },
        ).json()
        assert semantic_graph["nodes"][0]["type"] == "SemanticQuery"
        assert any(edge["predicate"] == "SEMANTIC_MATCH" for edge in semantic_graph["edges"])
        assert semantic_graph["metadata"]["semantic_similarity_edges"] >= 0
        assert semantic_graph["metadata"]["semantic_result_nodes"] >= 1
        assert semantic_graph["metadata"]["structural_neighbor_nodes"] >= 1
        semantic_targets = {
            edge["target"]
            for edge in semantic_graph["edges"]
            if edge["predicate"] == "SEMANTIC_MATCH"
        }
        assert len(semantic_targets) == semantic_graph["metadata"]["semantic_result_nodes"]
        semantic_code_nodes = [
            node for node in semantic_graph["nodes"] if node["type"] == "CodeSymbol"
        ]
        assert semantic_code_nodes
        assert all(node.get("repository_id") for node in semantic_code_nodes)
        rank_node = next(node for node in structured_code_nodes if node.get("label") == "rank")
        call_branch = client.get(
            "/v1/graph",
            params=[
                ("domain", "code"),
                ("focus_entity_id", rank_node["id"]),
                ("relation_types", "CALLS"),
                ("direction", "outgoing"),
                ("limit", "24"),
            ],
        ).json()
        assert call_branch["focus_entity_id"] == rank_node["id"]
        assert call_branch["edges"]
        assert {edge["predicate"] for edge in call_branch["edges"]} == {"CALLS"}
        assert all(edge["source"] == rank_node["id"] for edge in call_branch["edges"])
        assert call_branch["metadata"]["direction"] == "outgoing"
        assert call_branch["metadata"]["relation_counts"]["CALLS"] >= 1
        document_graph = client.get("/v1/graph", params={"domain": "document", "limit": 24}).json()
        assert {"ScientificDocument", "DocumentSection", "Claim"} <= {
            node["type"] for node in document_graph["nodes"]
        }
        assert {"CONTAINS_SECTION", "ASSERTS"} <= {
            edge["predicate"] for edge in document_graph["edges"]
        }
        research_graph = client.get("/v1/graph", params={"domain": "research", "limit": 36}).json()
        assert {"ScientificDocument", "Experiment", "ExperimentRun", "Metric"} <= {
            node["type"] for node in research_graph["nodes"]
        }
        assert {"HAS_RUN", "REPORTS_METRIC", "supported_by"} <= {
            edge["predicate"] for edge in research_graph["edges"]
        }
        overview_graph = client.get("/v1/graph", params={"domain": "overview", "limit": 60}).json()
        assert {"code", "experiment", "document"} <= {
            node["domain"] for node in overview_graph["nodes"]
        }
        assert overview_graph["metadata"]["total_nodes"] >= len(overview_graph["nodes"])
        assert "cross_source_edges" in overview_graph["metadata"]
        expanded_overview = client.get("/v1/graph", params={"domain": "overview", "limit": 61})
        assert expanded_overview.status_code == 200
        assert len(expanded_overview.json()["nodes"]) >= len(overview_graph["nodes"])

        lineage = client.get("/v1/lineage", params={"entity_id": claim["id"], "depth": 2}).json()
        node_ids = {item["id"] for item in lineage["nodes"]}
        predicates = {item["predicate"] for item in lineage["edges"]}
        assert {claim["id"], run["id"], run["commit_entity_id"]} <= node_ids
        assert {"supported_by", "uses"} <= predicates

        ranking_file = sample_repository / "src" / "ranking.py"
        ranking_file.write_text(
            ranking_file.read_text(encoding="utf-8")
            + "\n\ndef reciprocal_rank(value: float) -> float:\n    return 1.0 / (1.0 + value)\n",
            encoding="utf-8",
        )
        second_workflow = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{second_workflow['workflow_id']}").json()["status"]
            == "completed"
        )
        current_commit = client.get("/v1/repositories").json()[0]["head_commit"]
        assert current_commit != original_commit

        drift = client.post("/v1/drift/scan", json={}).json()
        assert drift["count"] == 1
        assessment = drift["assessments"][0]
        assert assessment["status"] == "unknown_version"
        assert assessment["original_commit"] == original_commit
        assert assessment["current_commit"] == current_commit
        assert assessment["risk_score"] == 0.65

        updated_claim = client.get(
            "/v1/documents/claims/by-id", params={"claim_id": claim["id"]}
        ).json()
        assert updated_claim["status"] == "potentially_stale"
        stored = client.get("/v1/drift/assessments").json()
        assert stored[0]["metadata"]["historical_diff_available"] is False


def test_multiterm_structured_search_and_relation_expansion(settings) -> None:
    with TestClient(create_app(settings)) as client:
        experiment = client.post(
            "/v1/experiments",
            json={"title": "Alpha retrieval study", "objective": "Compare ranking quality"},
        ).json()
        run = client.post(
            "/v1/experiments/runs",
            json={
                "experiment_id": experiment["id"],
                "name": "trial-0042",
                "dataset_id": "dataset://alpha",
                "dataset_version": "2.1",
                "config": {"seed": 42, "reranker": "cross-encoder"},
                "metrics": [{"name": "ndcg@10", "value": 0.91}],
            },
        ).json()
        document = client.post(
            "/v1/documents/ingest",
            json={
                "title": "Binding report",
                "content": (
                    "# Conclusion\n\n"
                    "Claim: Phoenix sentinel confirms the qualitative reliability conclusion."
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
        )
        assert linked.status_code == 201

        multiterm = client.post(
            "/v1/search",
            json={
                "query": "please analyze alpha retrieval ndcg@10",
                "sources": ["experiment"],
                "limit": 10,
            },
        ).json()
        assert {"Experiment", "Metric"} <= {item["entity_type"] for item in multiterm["results"]}
        assert all("structured_multi_term" in item["channels"] for item in multiterm["results"])
        assert any(item.get("matched_terms") for item in multiterm["results"])

        search = client.post(
            "/v1/search",
            json={
                "query": "locate Phoenix sentinel conclusion",
                "sources": ["document", "experiment"],
                "limit": 8,
            },
        ).json()
        assert any(item["entity_id"] == claim["id"] for item in search["results"])
        assert any(
            edge["source"] == claim["id"]
            and edge["target"] == run["id"]
            and edge["predicate"] == "supported_by"
            for edge in search["evidence_pack"]["relations"]
        )
        assert any(
            item["entity_id"] == run["id"] for item in search["evidence_pack"]["related_entities"]
        )
        assert any(
            citation["entity_id"] == run["id"] and citation.get("relation_only")
            for citation in search["evidence_pack"]["citation_map"].values()
        )

        answer = client.post(
            "/v1/query",
            json={
                "question": "Does the Phoenix sentinel conclusion have evidence?",
                "intent": "claim_verification",
                "include": ["document", "experiment"],
                "mode": "evidence",
                "max_evidence": 8,
            },
        ).json()
        assert answer["answer"]["decision_status"] == "supported"
        assert "[E" in answer["answer"]["text"]
        assert answer["answer"]["analysis"]["source_coverage"] == {
            "document": 2,
            "experiment": 1,
        }
        assert answer["evidence_pack"]["related_evidence"]

        case = client.post(
            "/v1/evaluation/cases",
            json={
                "name": "Relation expansion leakage guard",
                "question": "locate Phoenix sentinel conclusion",
                "expected_sources": ["document"],
                "expected_entity_ids": [claim["id"]],
                "forbidden_entity_ids": [run["id"]],
            },
        ).json()
        evaluation = client.post("/v1/evaluation/runs", json={"case_ids": [case["id"]]}).json()
        result = evaluation["results"][0]
        assert result["passed"] == 0
        assert result["unauthorized_leakage"] == 1.0
        assert run["id"] in result["detail"]["relation_expanded_entity_ids"]


def test_platform_relation_expansion_honors_valid_interval_and_as_of(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        experiment = client.post(
            "/v1/experiments",
            json={"title": "Temporal relation experiment"},
        ).json()
        run = client.post(
            "/v1/experiments/runs",
            json={
                "experiment_id": experiment["id"],
                "name": "temporal-run",
                "metrics": [{"name": "accuracy", "value": 0.8}],
            },
        ).json()
        document = client.post(
            "/v1/documents/ingest",
            json={
                "title": "Temporal relation report",
                "content": "# Finding\n\nClaim: Temporal sentinel supports this finding.",
            },
        ).json()
        claim = document["claims"][0]
        now = datetime.now(UTC)
        edge_id = "edge://temporal-platform-expansion"
        with app.state.runtime.platform.store.database.transaction() as db:
            db.execute(
                """INSERT INTO platform_edges
                   (id, project_id, source_entity_id, predicate, target_entity_id,
                    evidence_entity_id, derivation, confidence, review_status,
                    valid_from, valid_to, rule_version, metadata_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, '{}', ?, ?)""",
                (
                    edge_id,
                    "project-rag",
                    claim["id"],
                    "supported_by",
                    run["id"],
                    "human_confirmed",
                    1.0,
                    "confirmed",
                    (now + timedelta(days=1)).isoformat(),
                    None,
                    "temporal-relation-v1",
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

        def relation_ids(as_of: datetime) -> set[str]:
            response = client.post(
                "/v1/search",
                json={
                    "query": "Temporal sentinel finding",
                    "sources": ["document", "experiment"],
                    "as_of": as_of.isoformat(),
                    "limit": 8,
                },
            )
            assert response.status_code == 200
            return {str(edge["id"]) for edge in response.json()["evidence_pack"]["relations"]}

        assert edge_id not in relation_ids(now)
        with app.state.runtime.platform.store.database.transaction() as db:
            db.execute(
                "UPDATE platform_edges SET valid_from=?, valid_to=? WHERE id=?",
                (
                    (now - timedelta(hours=1)).isoformat(),
                    (now + timedelta(hours=1)).isoformat(),
                    edge_id,
                ),
            )
        assert edge_id in relation_ids(now)
        assert edge_id not in relation_ids(now + timedelta(hours=2))

        protected = "/Users/private/edge-time"
        with app.state.runtime.platform.store.database.transaction() as db:
            db.execute(
                "UPDATE platform_edges SET valid_from=?, valid_to=NULL WHERE id=?",
                (protected, edge_id),
            )
        response = client.post(
            "/v1/search",
            json={
                "query": "Temporal sentinel finding",
                "sources": ["document", "experiment"],
                "as_of": now.isoformat(),
                "limit": 8,
            },
        )
        assert response.status_code == 200
        assert edge_id not in {
            str(edge["id"]) for edge in response.json()["evidence_pack"]["relations"]
        }
        assert protected not in response.text


def test_governed_experiment_sources_report_partial_without_dropping_success() -> None:
    class SourceStore:
        @staticmethod
        def list_sources(project_id: str) -> list[dict[str, str]]:
            assert project_id == "project-rag"
            return [
                {"id": "source-good", "status": "ready"},
                {"id": "source-fails", "status": "ready"},
            ]

    class SnapshotStore:
        @staticmethod
        def active_snapshots(**scope):
            assert scope["project_id"] == "project-rag"
            return [
                SimpleNamespace(
                    run_snapshot_id="snapshot-good",
                    run_id="pipeline-run-good",
                    status="completed",
                    content_sha256="sha256:" + "1" * 64,
                )
            ]

    context = SimpleNamespace(
        blocks=[
            SimpleNamespace(
                run_snapshot_id="snapshot-good",
                role="metric",
                body="accuracy 0.8",
                citations=["experiment://formal-run-good"],
                content_sha256="sha256:" + "2" * 64,
            )
        ],
        model_dump=lambda **_kwargs: {
            "availability": "AVAILABLE",
            "reasoning_included": False,
        },
    )
    success = SimpleNamespace(
        authority=SimpleNamespace(
            project_id="project-rag",
            pipeline_source_id="pipeline-source-good",
            acl_ref="public",
            source_generation="generation-good",
            watermark="2026-07-30T00:00:00Z",
        ),
        identities=(
            SimpleNamespace(
                pipeline_run_id="pipeline-run-good",
                formal_run_id="formal-run-good",
                formal_experiment_id="formal-experiment-good",
            ),
        ),
        pipeline=SimpleNamespace(
            semantic=SimpleNamespace(
                candidates=(
                    SimpleNamespace(
                        run_snapshot_id="snapshot-good",
                        source_locator="experiment://formal-run-good",
                        rerank_score=0.8,
                        channel="sparse",
                    ),
                )
            )
        ),
        context=context,
        runtime_version="experiment-source-runtime-v2",
    )

    class Facade:
        service = SimpleNamespace(store=SourceStore(), sources=None)
        v2_store = SnapshotStore()

        @staticmethod
        def query_v2(*, source_id: str, **_kwargs):
            if source_id == "source-fails":
                raise ValueError("credential=do-not-leak")
            return success

    service = object.__new__(PlatformService)
    response = service._search_experiment_source_v2_with_facade(
        Facade(),
        GlobalSearchRequest(
            project_id="project-rag",
            query="accuracy",
            sources=["experiment"],
            allowed_acl_refs=["public"],
            enforce_acl=True,
            limit=8,
        ),
        limit=8,
    )
    assert [item["entity_id"] for item in response["results"]] == ["formal-run-good"]
    assert response["trace"]["source_status"] == "partial"
    assert response["trace"]["error_code"] == "experiment_source_partial"
    assert response["trace"]["source_error_codes"] == ["experiment_source_query_failed"]
    assert response["trace"]["source_count"] == 1
    assert response["trace"]["partial_source_failures"] == 1
    assert "do-not-leak" not in str(response)

    Facade.query_v2 = staticmethod(
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("source_unavailable:/private"))
    )
    failed = service._search_experiment_source_v2_with_facade(
        Facade(),
        GlobalSearchRequest(
            project_id="project-rag",
            query="accuracy",
            sources=["experiment"],
            allowed_acl_refs=["public"],
            enforce_acl=True,
            limit=8,
        ),
        limit=8,
    )
    assert failed["results"] == []
    assert failed["trace"]["source_status"] == "unavailable"
    assert failed["trace"]["error_code"] == "experiment_source_unavailable"
    assert "private" not in str(failed)


def test_query_reports_explicitly_requested_source_gaps(settings, sample_repository) -> None:
    with TestClient(create_app(settings)) as client:
        workflow = client.post(
            "/v1/ingestion/repositories", json={"source": str(sample_repository)}
        ).json()
        assert (
            client.get(f"/v1/ingestion/workflows/{workflow['workflow_id']}").json()["status"]
            == "completed"
        )

        response = client.post(
            "/v1/query",
            json={
                "question": ("代码和 Codex 会话如何实现向量检索？有哪些实验指标和文档证据支持？"),
                "intent": "global_synthesis",
                "include": ["code", "codex", "experiment", "document"],
                "mode": "evidence",
            },
        ).json()

        missing = {item["role"] for item in response["evidence_pack"]["missing_evidence"]}
        assert {"source_codex", "source_experiment", "source_document"} <= missing
        assert "source_code" not in missing
        assert response["answer"]["decision_status"] == "insufficient_evidence"
        assert response["evidence_pack"]["decision"]["rule_version"] == "evidence-decision-v4"
