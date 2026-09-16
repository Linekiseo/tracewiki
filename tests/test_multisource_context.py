from __future__ import annotations

import json

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.rag.multisource import build_multisource_context, portable_locator


def test_multisource_context_is_deterministic_bounded_and_redacted() -> None:
    results = [
        {
            "entity_id": "run://one",
            "source": "experiment",
            "entity_type": "ExperimentRunV2",
            "title": "best run",
            "snippet": "accuracy=0.91 secret=ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
            "locator": "/private/tmp/run.json",
            "version": "sha256:one",
            "score": 0.9,
            "channels": ["typed", "typed"],
        },
        {
            "entity_id": "document://one",
            "source": "document",
            "entity_type": "Claim",
            "title": "reported result",
            "snippet": "The treatment improved accuracy.",
            "locator": "document://one#claim=2",
            "version": "v1",
            "score": 0.8,
            "channels": ["structured"],
        },
        {
            "entity_id": "run://one",
            "source": "experiment",
            "entity_type": "ExperimentRunV2",
            "title": "duplicate",
            "snippet": "duplicate",
            "locator": "/private/tmp/run.json",
            "version": "sha256:one",
            "score": 0.1,
            "channels": [],
        },
    ]

    first = build_multisource_context(
        project_id="project-rag",
        results=results,
        requested_sources=["experiment", "document", "workspace"],
        budget_chars=1_000,
        per_block_chars=300,
    )
    second = build_multisource_context(
        project_id="project-rag",
        results=results,
        requested_sources=["experiment", "document", "workspace"],
        budget_chars=1_000,
        per_block_chars=300,
    )

    assert first == second
    assert len(first.blocks) == 2
    assert first.char_count <= first.budget_chars
    assert first.blocks[0].locator.startswith("entity://")
    assert first.blocks[0].redactions == ("generic_api_key",)
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890" not in first.rendered
    assert first.source_counts == {"experiment": 1, "document": 1}
    assert first.missing_sources == ("workspace",)
    assert tuple(first.citation_map) == ("E1", "E2")


def test_portable_locator_rejects_encoded_and_windows_absolute_paths() -> None:
    for value in (
        "/Users/alice/private.txt",
        "C:\\Users\\alice\\private.txt",
        "%2Fprivate%2Ftmp%2Fsecret",
        "file:///tmp/secret",
        "../secret",
        "document://doc-1?api_key=ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
    ):
        assert portable_locator(value, "entity-1").startswith("entity://")
    assert portable_locator("document://doc-1#page=2", "doc-1") == ("document://doc-1#page=2")


def test_notebook_is_an_explicit_platform_source_and_context_block(settings, tmp_path) -> None:
    notebook = tmp_path / "analysis.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {
                    "kernelspec": {
                        "display_name": "Python 3",
                        "language": "python",
                        "name": "python3",
                    }
                },
                "cells": [
                    {
                        "cell_type": "code",
                        "id": "cell-1",
                        "metadata": {},
                        "execution_count": 1,
                        "source": ["validation_accuracy = 0.91"],
                        "outputs": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with TestClient(create_app(settings)) as client:
        ingested = client.post("/v1/notebooks/ingest", json={"source": str(notebook)})
        assert ingested.status_code == 201, ingested.text
        response = client.post(
            "/v1/search",
            json={
                "query": "validation_accuracy",
                "sources": ["notebook"],
                "limit": 5,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["results"]
        assert {item["source"] for item in payload["results"]} == {"notebook"}
        assert all(not item["locator"].startswith("/") for item in payload["results"])
        context = payload["evidence_pack"]["context"]
        assert context["source_counts"] == {"notebook": 1}
        assert context["blocks"][0]["source"] == "notebook"
        assert context["citation_map"]["E1"]["entity_id"] == payload["results"][0]["entity_id"]

        query = client.post(
            "/v1/query",
            json={
                "question": "Which notebook cell contains validation_accuracy?",
                "include": ["notebook"],
                "mode": "evidence",
            },
        )
        assert query.status_code == 200, query.text
        evidence_pack = query.json()["evidence_pack"]
        assert evidence_pack["retrieval_context"]["schema_version"] == (
            "retrieval-context-bundle-v1"
        )
        assert evidence_pack["retrieval_context"]["multisource"]["source_counts"] == {"notebook": 1}
        assert evidence_pack["comprehension_context"]["schema_version"] == (
            "comprehension-context-v1"
        )
        assert evidence_pack["comprehension_context"]["reasoning_included"] is False
        assert {item["source"] for item in evidence_pack["comprehension_context"]["evidence"]} == {
            "notebook"
        }


def test_workspace_and_document_share_the_unified_context_contract(settings) -> None:
    with TestClient(create_app(settings)) as client:
        topic = client.post(
            "/v1/research/topics",
            json={
                "title": "Hybrid retrieval quality",
                "problem_statement": "Investigate sparse and dense ranking failures",
                "objective": "Improve hybrid retrieval",
                "status": "active",
            },
        )
        assert topic.status_code == 201, topic.text
        document = client.post(
            "/v1/documents/ingest",
            json={
                "title": "Hybrid retrieval report",
                "version": "v1",
                "content": (
                    "# Results\n\nHybrid retrieval improved ranking quality in the test set."
                ),
                "extract_claims": True,
            },
        )
        assert document.status_code == 201, document.text
        experiment = client.post(
            "/v1/experiments",
            json={
                "title": "Hybrid retrieval quality experiment",
                "objective": "Measure ranking quality",
                "status": "running",
            },
        ).json()
        run = client.post(
            "/v1/experiments/runs",
            json={
                "experiment_id": experiment["id"],
                "name": "Hybrid retrieval quality run",
                "status": "completed",
                "dataset_id": "dataset-a",
                "dataset_version": "v1",
                "environment": {"python": "3.13"},
                "metrics": [
                    {
                        "name": "accuracy",
                        "value": 0.91,
                        "unit": "ratio",
                        "split": "test",
                    }
                ],
            },
        )
        assert run.status_code == 201, run.text

        response = client.post(
            "/v1/search",
            headers={"X-RAG-Experiment-Engine": "v2"},
            json={
                "query": "hybrid retrieval quality",
                "sources": ["workspace", "document", "experiment"],
                "limit": 8,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert {item["source"] for item in payload["results"]} == {
            "workspace",
            "document",
            "experiment",
        }
        context = payload["evidence_pack"]["context"]
        assert context["source_counts"]["workspace"] >= 1
        assert context["source_counts"]["document"] >= 1
        assert context["source_counts"]["experiment"] >= 1
        assert context["missing_sources"] == []
        assert context["char_count"] <= context["budget_chars"]
        assert {block["citation_id"] for block in context["blocks"]} == set(context["citation_map"])

        query = client.post(
            "/v1/query",
            json={
                "question": "总结文档与 workspace 中的 hybrid retrieval 证据",
                "intent": "global_synthesis",
                "include": ["workspace", "document"],
                "mode": "evidence",
                "max_evidence": 8,
            },
        )
        assert query.status_code == 200, query.text
        query_pack = query.json()["evidence_pack"]
        assert set(query_pack["retrieval_context"]["multisource"]["source_counts"]) == {
            "workspace",
            "document",
        }
        comprehension = query_pack["comprehension_context"]
        assert comprehension["derived_only_from_selected_evidence"] is True
        assert comprehension["content_digest"].startswith("sha256:")
        assert {item["source"] for item in comprehension["evidence"]} == {
            "workspace",
            "document",
        }
