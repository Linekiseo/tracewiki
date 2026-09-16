from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.rag.sources.notebook import (
    NOTEBOOK_CONTEXT_BUILDER_VERSION,
    NOTEBOOK_RELEASE_THRESHOLDS,
    NOTEBOOK_REQUIRED_GUARDRAILS,
    NOTEBOOK_RERANKER_VERSION,
    NOTEBOOK_RETRIEVER_VERSION,
    NotebookEvidenceStatus,
    NotebookReleaseGuardrail,
    NotebookReleaseMetric,
    NotebookReleaseStage,
    build_notebook_release_evidence_v2,
    canonical_sha256,
    evaluate_notebook_release_v2,
)


def _write_notebook(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {
                    "kernelspec": {"name": "python3"},
                    "language_info": {"name": "python"},
                },
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": 1,
                        "metadata": {"tags": ["parameters"]},
                        "source": ["threshold = 0.75\n"],
                        "outputs": [],
                    },
                    {
                        "cell_type": "code",
                        "execution_count": 2,
                        "metadata": {},
                        "source": ["score = threshold + 0.10\n", "print(score)\n"],
                        "outputs": [
                            {
                                "output_type": "stream",
                                "name": "stdout",
                                "text": ["0.85\n"],
                            }
                        ],
                    },
                    {
                        "cell_type": "code",
                        "execution_count": 3,
                        "metadata": {},
                        "source": ["raise ValueError('unsafe /Users/alice/private')\n"],
                        "outputs": [
                            {
                                "output_type": "error",
                                "ename": "ValueError",
                                "evalue": "unsafe /Users/alice/private",
                                "traceback": [],
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _install_notebook_v2_authority(runtime: Any) -> None:
    metrics = tuple(
        NotebookReleaseMetric(
            name=name,
            status=NotebookEvidenceStatus.AVAILABLE,
            value=threshold,
            numerator=1,
            denominator=1,
        )
        for name, (_, threshold) in NOTEBOOK_RELEASE_THRESHOLDS.items()
    )
    guardrails = tuple(
        NotebookReleaseGuardrail(
            name=name,
            status=NotebookEvidenceStatus.AVAILABLE,
            violations=0,
        )
        for name in NOTEBOOK_REQUIRED_GUARDRAILS
    )
    evidence = build_notebook_release_evidence_v2(
        observed_stage=NotebookReleaseStage.CANARY_25,
        proposed_stage=NotebookReleaseStage.OPT_IN_100,
        golden_package_sha256=canonical_sha256({"isolated_notebook_golden": "v2"}),
        artifact_set_sha256=canonical_sha256({"isolated_notebook_artifacts": "v2"}),
        retriever_version=NOTEBOOK_RETRIEVER_VERSION,
        reranker_version=NOTEBOOK_RERANKER_VERSION,
        context_builder_version=NOTEBOOK_CONTEXT_BUILDER_VERSION,
        metrics=metrics,
        guardrails=guardrails,
        production_observation=True,
    )
    runtime.source_runtime_v2.install_release_authority(
        source="notebook",
        project_id="project-rag",
        evidence=evidence,
        decision=evaluate_notebook_release_v2(evidence),
    )


def test_notebook_explicit_v2_without_authority_falls_back_to_v1(
    settings, tmp_path, monkeypatch
) -> None:
    notebook = tmp_path / "analysis.ipynb"
    _write_notebook(notebook)
    app = create_app(settings)
    runtime = app.state.runtime
    engine_calls: list[str] = []
    retrieve_source_engine = runtime.platform._retrieve_source_engine

    def tracked_retrieve_source_engine(**kwargs):
        if kwargs["source"] == "notebook":
            engine_calls.append(kwargs["engine_requested"])
        return retrieve_source_engine(**kwargs)

    monkeypatch.setattr(runtime.platform, "_retrieve_source_engine", tracked_retrieve_source_engine)
    with TestClient(app) as client:
        ingested = client.post(
            "/v1/notebooks/ingest",
            json={"source": str(notebook), "version": "v2-test"},
        )
        assert ingested.status_code == 201

        default = client.post(
            "/v1/search",
            json={"query": "threshold output", "sources": ["notebook"], "limit": 8},
        ).json()
        assert default["trace"]["notebook_engine"] == "v1"
        assert engine_calls == ["v1"]

        engine_calls.clear()
        response = client.post(
            "/v1/search",
            headers={"X-RAG-Notebook-Engine": "v2"},
            json={"query": "threshold output", "sources": ["notebook"], "limit": 8},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["trace"]["notebook_engine"] == "v1"
        assert payload["trace"]["source_engine_routing"]["notebook"] == {
            "requested": "v2",
            "selected": "v1",
            "served": "v1",
        }
        route = payload["trace"]["sources"]["notebook"]["release_route"]
        assert route["stage"] == "off"
        assert route["requested_engine"] == "v2"
        assert route["selected_engine"] == "v1"
        assert route["response_engine"] == "v1"
        assert route["served_engine"] == "v1"
        assert route["execute_v1"] is True
        assert route["execute_v2"] is False
        assert route["fallback_reason"] == "authority_unavailable"
        assert route["authority_sha256"] is None
        assert engine_calls == ["v1"]
        assert client.app.state.runtime.source_runtime_v2.materialized_sources == ()
        assert client.app.state.runtime.source_runtime_v2.derived_root_created is False
        rendered = json.dumps(payload, ensure_ascii=False)
        assert "notebook-execution-context-v2" not in rendered

        invalid = client.post(
            "/v1/search",
            headers={"X-RAG-Notebook-Engine": "v3"},
            json={"query": "threshold", "sources": ["notebook"]},
        )
        assert invalid.status_code == 422


def test_notebook_explicit_v2_with_evaluator_authority_serves_isolated_v2(
    settings, tmp_path, monkeypatch
) -> None:
    notebook = tmp_path / "authorized-analysis.ipynb"
    _write_notebook(notebook)
    app = create_app(settings)
    runtime = app.state.runtime
    _install_notebook_v2_authority(runtime)
    engine_calls: list[str] = []
    retrieve_source_engine = runtime.platform._retrieve_source_engine

    def tracked_retrieve_source_engine(**kwargs):
        if kwargs["source"] == "notebook":
            engine_calls.append(kwargs["engine_requested"])
        return retrieve_source_engine(**kwargs)

    monkeypatch.setattr(runtime.platform, "_retrieve_source_engine", tracked_retrieve_source_engine)
    assert runtime.source_runtime_v2.materialized_sources == ()
    assert runtime.source_runtime_v2.derived_root_created is False

    with TestClient(app) as client:
        ingested = client.post(
            "/v1/notebooks/ingest",
            json={"source": str(notebook), "version": "authorized-v2-test"},
        )
        assert ingested.status_code == 201, ingested.text

        direct_response = client.post(
            "/v1/notebooks/search",
            headers={"X-RAG-Notebook-Engine": "v2"},
            json={"query": "threshold output", "limit": 8},
        )
        assert direct_response.status_code == 200, direct_response.text
        direct_payload = direct_response.json()
        direct_route = direct_payload["trace"]["release_route"]
        assert direct_route["stage"] == "on"
        assert direct_route["requested_engine"] == "v2"
        assert direct_route["selected_engine"] == "v2"
        assert direct_route["served_engine"] == "v2"
        assert direct_route["execute_v1"] is False
        assert direct_route["execute_v2"] is True
        assert direct_route["fallback_reason"] is None
        assert direct_route["authority_sha256"].startswith("sha256:")
        assert direct_payload["context"]["schema_version"] == "notebook-execution-context-v2"
        assert direct_payload["results"]

        response = client.post(
            "/v1/search",
            headers={"X-RAG-Notebook-Engine": "v2"},
            json={"query": "threshold output", "sources": ["notebook"], "limit": 8},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["trace"]["notebook_engine"] == "v2"
        assert payload["trace"]["source_engine_routing"]["notebook"] == {
            "requested": "v2",
            "selected": "v2",
            "served": "v2",
        }
        route = payload["trace"]["sources"]["notebook"]["release_route"]
        assert route["stage"] == "on"
        assert route["requested_engine"] == "v2"
        assert route["selected_engine"] == "v2"
        assert route["served_engine"] == "v2"
        assert route["execute_v1"] is False
        assert route["execute_v2"] is True
        assert route["fallback_reason"] is None
        assert route["authority_sha256"] == direct_route["authority_sha256"]
        assert payload["results"]
        assert payload["evidence_pack"]["source_contexts"]["notebook"]["schema_version"] == (
            "notebook-execution-context-v2"
        )
        assert "notebook-execution-context-v2" in response.text
        assert engine_calls == ["v2", "v2"]
        assert runtime.source_runtime_v2.materialized_sources == ("notebook",)
        assert runtime.source_runtime_v2.derived_root_created is True
        facade = runtime.source_runtime_v2.get("notebook")
        assert facade.index.database_path != settings.database_path
        assert facade.index.database_path.is_relative_to(facade.index.isolated_root)

        default_route = runtime.source_runtime_v2.route(
            source="notebook",
            project_id="project-rag",
            request_id="notebook-default-remains-v1",
            requested_engine=None,
        )
        assert default_route.selected_engine == "v1"
        assert default_route.response_engine == "v1"
        assert default_route.execute_v1 is True
        assert default_route.execute_v2 is False
        assert engine_calls == ["v2", "v2"]

        status = client.get("/v1/rag/status")
        assert status.status_code == 200, status.text
        assert status.json()["decision"] == "QUALITY_HOLD"
        assert status.json()["default_engine"] == "v1"
        assert status.json()["quality_hold"] is True


def test_notebook_v2_acl_fails_closed(settings, tmp_path) -> None:
    notebook = tmp_path / "private.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "metadata": {"language_info": {"name": "python"}},
                "cells": [
                    {
                        "cell_type": "code",
                        "execution_count": 1,
                        "metadata": {},
                        "source": ["classified_value = 42"],
                        "outputs": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert (
            client.post(
                "/v1/notebooks/ingest",
                json={
                    "source": str(notebook),
                    "acl_ref": "project:private",
                },
            ).status_code
            == 201
        )
        denied = app.state.runtime.notebook_v2.search(
            project_id="project-rag",
            query="classified_value",
            allowed_acl_refs=[],
            enforce_acl=True,
        )
        assert denied["results"] == []
        assert denied["trace"]["denied_runs"] == 1
