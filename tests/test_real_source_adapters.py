from __future__ import annotations

import json

from evidence_rag.documents.models import DocumentIngestRequest
from evidence_rag.experiments.models import MLflowSyncRequest
from evidence_rag.notebooks.models import NotebookCompareRequest, NotebookIngestRequest
from evidence_rag.runtime import create_runtime


def test_local_mlflow_filestore_sync_is_idempotent(settings, tmp_path) -> None:
    tracking = tmp_path / "mlruns"
    experiment = tracking / "7"
    run = experiment / "run-001"
    (run / "metrics").mkdir(parents=True)
    (run / "params").mkdir()
    (run / "tags").mkdir()
    (run / "artifacts").mkdir()
    (experiment / "meta.yaml").write_text(
        "experiment_id: '7'\nname: Retrieval quality\nlifecycle_stage: active\n",
        encoding="utf-8",
    )
    (run / "meta.yaml").write_text(
        "run_id: run-001\nstatus: FINISHED\nstart_time: 1753257600000\nend_time: 1753257660000\n",
        encoding="utf-8",
    )
    (run / "params" / "top_k").write_text("40", encoding="utf-8")
    (run / "tags" / "dataset_id").write_text("dataset://nq", encoding="utf-8")
    (run / "tags" / "dataset_version").write_text("1.2", encoding="utf-8")
    (run / "tags" / "mlflow.runName").write_text("candidate", encoding="utf-8")
    (run / "metrics" / "ndcg@10").write_text("1753257660000 0.842 1\n", encoding="utf-8")
    (run / "artifacts" / "evaluation.json").write_text("{}", encoding="utf-8")

    runtime = create_runtime(settings)
    request = MLflowSyncRequest(tracking_uri=str(tracking))
    first = runtime.experiments.sync_mlflow(request)
    second = runtime.experiments.sync_mlflow(request)
    assert first["stats"] == {
        "experiments": 1,
        "runs": 1,
        "imported": 1,
        "updated": 0,
        "version_bound": 0,
        "metrics": 1,
        "artifacts": 1,
    }
    assert second["stats"]["imported"] == 0
    assert second["stats"]["updated"] == 1
    runs = runtime.experiments.store.list_runs("project-rag")
    detail = runtime.experiments.store.get_run(runs[0]["id"])
    assert detail and detail["external_id"] == "run-001"
    assert detail["metrics"][0]["value"] == 0.842
    assert detail["artifacts"][0]["name"] == "evaluation.json"
    assert runtime.sources.store.stats("project-rag")["by_type"]["mlflow"] == 1


def _notebook(parameter: int, output: int) -> dict:
    return {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": 1,
                "metadata": {"tags": ["parameters"]},
                "source": [f"top_k = {parameter}\n"],
                "outputs": [],
            },
            {
                "cell_type": "code",
                "execution_count": 2,
                "metadata": {},
                "source": ["score = top_k * 2\n", "score"],
                "outputs": [
                    {
                        "output_type": "execute_result",
                        "execution_count": 2,
                        "data": {"text/plain": [str(output)]},
                        "metadata": {},
                    }
                ],
            },
        ],
        "metadata": {
            "kernelspec": {"name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def test_notebook_structure_and_execution_comparison(settings, tmp_path) -> None:
    path = tmp_path / "evaluation.ipynb"
    runtime = create_runtime(settings)
    path.write_text(json.dumps(_notebook(20, 40)), encoding="utf-8")
    baseline = runtime.notebooks.ingest(NotebookIngestRequest(source=str(path), version="baseline"))
    path.write_text(json.dumps(_notebook(40, 80)), encoding="utf-8")
    candidate = runtime.notebooks.ingest(
        NotebookIngestRequest(source=str(path), version="candidate")
    )
    comparison = runtime.notebooks.compare(
        NotebookCompareRequest(baseline_id=baseline["id"], candidate_id=candidate["id"])
    )
    assert comparison["parameter_changes"] == [
        {"key": "top_k", "baseline": "20", "candidate": "40"}
    ]
    assert {item["ordinal"] for item in comparison["cell_changes"]} == {0, 1}
    assert candidate["cells"][1]["outputs"][0]["text_content"] == "80"
    assert runtime.sources.store.get_object(candidate["raw_object_id"])["derived_count"] == 4


def test_structured_markdown_preserves_tables_cells_and_citations(settings, tmp_path) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        "# Results\n\n"
        "Claim: Hybrid reranking reaches nDCG@10 of 0.842 on NQ.\n\n"
        "| Metric | Value |\n|---|---:|\n| nDCG@10 | 0.842 |\n\n"
        "This follows the evaluation protocol [12] and DOI 10.1234/example.2026.\n",
        encoding="utf-8",
    )
    runtime = create_runtime(settings)
    document = runtime.documents.ingest(
        DocumentIngestRequest(title="Ranking report", version="v2", source=str(report))
    )
    assert document["source_type"] == "markdown"
    assert len(document["tables"]) == 1
    assert document["tables"][0]["row_count"] == 2
    assert document["tables"][0]["cells"][3]["value"] == "0.842"
    assert {item["marker"] for item in document["citations"]} == {
        "[12]",
        "10.1234/example.2026.",
    }
    raw = runtime.sources.store.list_objects("project-rag", state="active", limit=10)
    assert any(item["source_type"] == "document" for item in raw)
