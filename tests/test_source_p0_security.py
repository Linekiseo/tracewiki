from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.experiments.adapters.mlflow import MLflowAdapter, MLflowAdapterError
from evidence_rag.experiments.models import MLflowSyncRequest
from evidence_rag.runtime import create_runtime


def _notebook(marker: str, output: str) -> dict:
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {
                "cell_type": "code",
                "execution_count": 1,
                "metadata": {},
                "source": [f"{marker} = 42\n"],
                "outputs": [
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": [output],
                    }
                ],
            }
        ],
    }


def test_notebook_object_acl_prefilters_runs_cells_and_default_search(
    settings, tmp_path: Path
) -> None:
    private_path = tmp_path / "private.ipynb"
    public_path = tmp_path / "public.ipynb"
    private_path.write_text(
        json.dumps(_notebook("private_cell_marker", "private-output")),
        encoding="utf-8",
    )
    public_path.write_text(
        json.dumps(_notebook("public_cell_marker", "public-output")),
        encoding="utf-8",
    )
    secured = replace(settings, enforce_acl=True)
    project_headers = {"X-RAG-ACL-Refs": "project:project-rag"}
    private_headers = {"X-RAG-ACL-Refs": "project:project-rag,project:notebook-private"}

    with TestClient(create_app(secured)) as client:
        private = client.post(
            "/v1/notebooks/ingest",
            headers=project_headers,
            json={
                "source": str(private_path),
                "version": "private-v1",
                "acl_ref": "project:notebook-private",
            },
        )
        public = client.post(
            "/v1/notebooks/ingest",
            headers=project_headers,
            json={
                "source": str(public_path),
                "version": "public-v1",
                "acl_ref": "public",
            },
        )
        assert private.status_code == public.status_code == 201
        assert (
            client.post(
                "/v1/notebooks/ingest",
                headers=project_headers,
                json={
                    "source": str(private_path),
                    "version": "private-v1",
                    "acl_ref": "public",
                },
            ).status_code
            == 201
        )

        visible_runs = client.get("/v1/notebooks/runs", headers=project_headers)
        assert visible_runs.status_code == 200
        assert {item["id"] for item in visible_runs.json()} == {public.json()["id"]}
        assert (
            client.get(
                "/v1/notebooks/runs/by-id",
                headers=project_headers,
                params={"notebook_run_id": private.json()["id"]},
            ).status_code
            == 404
        )

        denied = client.post(
            "/v1/search",
            headers=project_headers,
            json={
                "query": "private_cell_marker",
                "sources": ["notebook"],
                "include_lineage": False,
                "max_hops": 0,
            },
        )
        assert denied.status_code == 200
        assert denied.json()["results"] == []
        assert "private-output" not in denied.text

        allowed = client.post(
            "/v1/search",
            headers=private_headers,
            json={
                "query": "private_cell_marker",
                "sources": ["notebook"],
                "include_lineage": False,
                "max_hops": 0,
            },
        )
        assert allowed.status_code == 200
        assert {item["entity_id"] for item in allowed.json()["results"]} == {private.json()["id"]}


def test_mlflow_local_filestore_requires_explicit_root_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "allowed"
    tracking = allowed_root / "mlruns"
    outside = tmp_path / "outside"
    tracking.mkdir(parents=True)
    outside.mkdir()

    with pytest.raises(MLflowAdapterError, match="roots are not configured"):
        MLflowAdapter(str(tracking))

    adapter = MLflowAdapter(str(tracking), allowed_local_roots=(allowed_root,))
    assert adapter.root == tracking.resolve()

    alias = allowed_root / "escape"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(MLflowAdapterError, match="outside explicitly allowed roots"):
        MLflowAdapter(str(alias), allowed_local_roots=(allowed_root,))
    nested_alias = tracking / "escape"
    nested_alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(MLflowAdapterError, match="symlinks are forbidden"):
        adapter._validate_local_path(nested_alias)


@pytest.mark.parametrize(
    "host",
    (
        "127.0.0.1",
        "10.0.0.8",
        "169.254.169.254",
        "192.0.2.10",
        "::1",
    ),
)
def test_mlflow_http_rejects_non_public_literal_even_when_allowlisted(host: str) -> None:
    authority = f"[{host}]" if ":" in host else host
    with pytest.raises(MLflowAdapterError, match="non-public"):
        MLflowAdapter(
            f"https://{authority}/mlflow",
            allowed_http_hosts=(host,),
        )


def test_mlflow_http_rejects_private_dns_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def private_dns(*_args, **_kwargs):
        return [(2, 1, 6, "", ("10.1.2.3", 443))]

    monkeypatch.setattr("socket.getaddrinfo", private_dns)
    with pytest.raises(MLflowAdapterError, match="non-public"):
        MLflowAdapter(
            "https://mlflow.example.test",
            allowed_http_hosts=("mlflow.example.test",),
        )

    def public_dns(*_args, **_kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr("socket.getaddrinfo", public_dns)
    adapter = MLflowAdapter(
        "https://mlflow.example.test",
        allowed_http_hosts=("mlflow.example.test",),
    )

    class RedirectClient:
        @staticmethod
        def request(method: str, path: str, **_kwargs) -> httpx.Response:
            request = httpx.Request(method, f"https://mlflow.example.test{path}")
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1/internal"},
                request=request,
            )

    with pytest.raises(MLflowAdapterError, match="redirects are forbidden"):
        adapter._request(RedirectClient(), "GET", "/redirect")


def test_mlflow_rest_fetches_full_metric_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    adapter = MLflowAdapter(
        "https://mlflow.example.test",
        allowed_http_hosts=("mlflow.example.test",),
    )

    class HistoryClient:
        @staticmethod
        def request(method: str, path: str, **_kwargs) -> httpx.Response:
            request = httpx.Request(method, f"https://mlflow.example.test{path}")
            return httpx.Response(
                200,
                json={
                    "metrics": [
                        {"key": "quality", "value": 0.7, "step": 0, "timestamp": 1_000},
                        {"key": "quality", "value": 0.8, "step": 1, "timestamp": 2_000},
                        {"key": "quality", "value": 0.9, "step": 2, "timestamp": 3_000},
                    ]
                },
                request=request,
            )

    history = adapter._rest_metric_history(
        HistoryClient(),
        "run-001",
        [{"key": "quality", "value": 0.9, "step": 2}],
    )
    assert [item["step"] for item in history] == [0, 1, 2]
    assert [item["value"] for item in history] == [0.7, 0.8, 0.9]


def _write_mlflow_filestore(root: Path) -> tuple[Path, Path, Path]:
    experiment = root / "7"
    run = experiment / "run-001"
    (run / "metrics").mkdir(parents=True)
    (run / "params").mkdir()
    (run / "tags").mkdir()
    (experiment / "meta.yaml").write_text(
        "experiment_id: '7'\nname: Retrieval quality\nlifecycle_stage: active\n",
        encoding="utf-8",
    )
    run_meta = run / "meta.yaml"
    run_meta.write_text(
        "run_id: run-001\nrun_name: immutable-v1\nstatus: FINISHED\n"
        "lifecycle_stage: active\nstart_time: 1753257600000\n"
        "end_time: 1753257660000\n",
        encoding="utf-8",
    )
    (run / "params" / "top_k").write_text("40", encoding="utf-8")
    metric = run / "metrics" / "quality"
    metric.write_text(
        "1753257640000 0.70 0\n1753257650000 0.80 1\n1753257660000 0.90 2\n",
        encoding="utf-8",
    )
    return experiment, run_meta, metric


def test_mlflow_sync_preserves_history_completed_versions_and_tombstones(
    settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracking = tmp_path / "mlruns"
    experiment_dir, run_meta, metric = _write_mlflow_filestore(tracking)
    monkeypatch.setenv("RAG_MLFLOW_ALLOWED_LOCAL_ROOTS", str(tmp_path))
    runtime = create_runtime(settings)
    request = MLflowSyncRequest(tracking_uri=str(tracking))

    first = runtime.experiments.sync_mlflow(request)
    assert first["stats"]["metrics"] == 3
    run_id = runtime.experiments.store.list_runs("project-rag")[0]["id"]
    original = runtime.experiments.store.get_run(run_id)
    assert original is not None
    assert [item["step"] for item in original["metrics"]] == [0, 1, 2]
    assert [item["value"] for item in original["metrics"]] == [0.7, 0.8, 0.9]

    run_meta.write_text(
        "run_id: run-001\nrun_name: rewritten-v2\nstatus: FINISHED\n"
        "lifecycle_stage: active\nstart_time: 1753257600000\n"
        "end_time: 1753257670000\n",
        encoding="utf-8",
    )
    metric.write_text(
        metric.read_text(encoding="utf-8") + "1753257670000 0.95 3\n",
        encoding="utf-8",
    )
    runtime.experiments.sync_mlflow(request)

    immutable = runtime.experiments.store.get_run(run_id)
    assert immutable is not None
    assert immutable["name"] == "immutable-v1"
    assert [item["step"] for item in immutable["metrics"]] == [0, 1, 2]
    observations = runtime.experiments.store.list_run_observations(run_id)
    assert len(observations) == 2
    assert sorted(len(item["payload"]["metrics"]) for item in observations) == [3, 4]

    (experiment_dir / "meta.yaml").write_text(
        "experiment_id: '7'\nname: Retrieval quality\nlifecycle_stage: deleted\n",
        encoding="utf-8",
    )
    run_meta.write_text(
        "run_id: run-001\nrun_name: rewritten-v2\nstatus: FINISHED\n"
        "lifecycle_stage: deleted\nstart_time: 1753257600000\n"
        "end_time: 1753257670000\n",
        encoding="utf-8",
    )
    runtime.experiments.sync_mlflow(request)

    deleted_run = runtime.experiments.store.get_run(run_id)
    assert deleted_run is not None and deleted_run["status"] == "deleted"
    experiments = runtime.experiments.store.list_experiments("project-rag")
    assert experiments[0]["status"] == "deleted"
    assert len(runtime.experiments.store.list_run_observations(run_id)) == 3
