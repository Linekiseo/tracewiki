from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evidence_rag.notebooks.store import NotebookStore
from evidence_rag.platform.models import NotebookQueryTaskV2
from evidence_rag.rag.sources.notebook import (
    NOTEBOOK_SOURCE_DEFAULT_ENGINE,
    NotebookRetrieverV2,
    NotebookSourceRuntimeError,
    NotebookSourceRuntimeV2,
    NotebookV2Store,
)
from evidence_rag.sources.service import RawSourceService
from evidence_rag.sources.store import RawSourceStore
from evidence_rag.storage import SQLiteStore
from evidence_rag.workspace.models import ProjectCreate
from evidence_rag.workspace.service import WorkspaceService
from evidence_rag.workspace.store import WorkspaceStore


def _runtime(tmp_path: Path) -> tuple[NotebookSourceRuntimeV2, Path]:
    database = SQLiteStore(tmp_path / "formal.sqlite3")
    database.initialize()
    workspace_store = WorkspaceStore(database)
    workspace_store.initialize()
    workspace = WorkspaceService(workspace_store)
    workspace.create_project(
        ProjectCreate(
            id="project-notebook-runtime",
            name="Notebook runtime",
            acl_ref="project:notebook-runtime",
        )
    )
    raw_store = RawSourceStore(database)
    raw_store.initialize()
    raw_sources = RawSourceService(raw_store, tmp_path / "raw")
    notebook_store = NotebookStore(database)
    notebook_store.initialize()

    payload = json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {
                "kernelspec": {"name": "python3"},
                "language_info": {"name": "python"},
            },
            "cells": [
                {
                    "id": "parameters",
                    "cell_type": "code",
                    "metadata": {"tags": ["parameters"]},
                    "source": ["threshold = 0.8\n"],
                    "execution_count": 1,
                    "outputs": [],
                },
                {
                    "id": "producer",
                    "cell_type": "code",
                    "metadata": {},
                    "source": ["score = threshold + 0.1\n"],
                    "execution_count": 2,
                    "outputs": [
                        {
                            "output_type": "stream",
                            "name": "stdout",
                            "text": ["score=0.9\n"],
                        }
                    ],
                },
                {
                    "id": "failure",
                    "cell_type": "code",
                    "metadata": {},
                    "source": ["raise RuntimeError(score)\n"],
                    "execution_count": 3,
                    "outputs": [
                        {
                            "output_type": "error",
                            "ename": "RuntimeError",
                            "evalue": "0.9",
                            "traceback": ["RuntimeError: 0.9"],
                        }
                    ],
                },
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    raw = raw_sources.persist_bytes(
        project_id="project-notebook-runtime",
        source_type="notebook",
        source_instance="managed-notebook",
        source_object_id="notebook://project-notebook-runtime/template/formal",
        source_version="v1",
        payload=payload,
        source_uri="managed:notebook.ipynb",
        media_type="application/x-ipynb+json",
        acl_ref="notebook:private",
        adapter_version="jupyter-ipynb-v1",
        schema_version="jupyter-notebook-v1",
        content_hash=digest,
    )
    run_id = "notebook://project-notebook-runtime/run/formal-v1"
    notebook_store.save(
        {
            "id": "notebook://project-notebook-runtime/template/formal",
            "project_id": "project-notebook-runtime",
            "name": "formal",
            "source_uri": "managed:notebook.ipynb",
            "content_hash": digest,
            "kernel_name": "python3",
            "language": "python",
            "acl_ref": "notebook:private",
            "metadata": {},
        },
        {
            "id": run_id,
            "project_id": "project-notebook-runtime",
            "version": "v1",
            "status": "failed",
            "parameters": {"threshold": 0.8},
            "raw_object_id": raw["id"],
            "content_hash": digest,
            "metadata": {},
        },
        [
            {
                "id": f"{run_id}/cell/{ordinal}",
                "ordinal": ordinal,
                "cell_type": cell["cell_type"],
                "source": "".join(cell["source"]),
                "source_hash": "sha256:"
                + hashlib.sha256("".join(cell["source"]).encode()).hexdigest(),
                "execution_count": cell["execution_count"],
                "tags": cell["metadata"].get("tags", []),
                "metadata": cell["metadata"],
                "source_locator": f"managed:notebook.ipynb#cell={ordinal}",
                "outputs": [
                    {
                        "id": f"{run_id}/cell/{ordinal}/output/{output_ordinal}",
                        "ordinal": output_ordinal,
                        "output_type": output["output_type"],
                        "text_content": "".join(output.get("text") or []),
                        "data_types": [],
                        "error_name": output.get("ename"),
                        "error_value": output.get("evalue"),
                        "metadata": {},
                    }
                    for output_ordinal, output in enumerate(cell["outputs"])
                ],
            }
            for ordinal, cell in enumerate(json.loads(payload)["cells"])
        ],
    )
    index_root = tmp_path / "notebook-index"
    index_root.mkdir()
    runtime = NotebookSourceRuntimeV2(
        notebooks=notebook_store,
        workspace=workspace,
        raw_sources=raw_sources,
        index=NotebookV2Store(
            index_root / "notebook-runtime.sqlite3",
            isolated_root=index_root,
        ),
    )
    return runtime, Path(str(raw["storage_path"]))


def test_notebook_runtime_uses_formal_raw_acl_and_complete_source_path(
    tmp_path: Path,
) -> None:
    runtime, _ = _runtime(tmp_path)
    denied = runtime.search(
        project_id="project-notebook-runtime",
        query="RuntimeError score",
        task=NotebookQueryTaskV2.ERROR,
        allowed_acl_refs=["project:notebook-runtime"],
        enforce_acl=True,
    )
    assert denied["results"] == []
    assert denied["trace"]["source_status"] == "acl_denied"

    result = runtime.search(
        project_id="project-notebook-runtime",
        query="RuntimeError score failed cell",
        task=NotebookQueryTaskV2.ERROR,
        allowed_acl_refs=["project:notebook-runtime", "notebook:private"],
        enforce_acl=True,
    )
    assert result["results"]
    assert NOTEBOOK_SOURCE_DEFAULT_ENGINE == "v1"
    assert result["trace"]["explicit_v2_required"] is True
    assert result["trace"]["fallback_used"] is False
    assert result["task"] == "error"
    assert result["trace"]["task"] == "error"
    assert result["trace"]["publication_count"] == 1
    assert result["trace"]["index_generation"] == result["index_generation"]
    assert result["watermark"] != "unavailable"
    assert any(item["entity_type"] == "error" for item in result["results"])
    assert any("RuntimeError" in block["content"] for block in result["context"]["blocks"])
    assert result["context"]["reasoning_included"] is False
    assert all("fixture" not in item["locator"] for item in result["results"])


def test_notebook_runtime_fails_closed_when_managed_payload_changes(tmp_path: Path) -> None:
    runtime, raw_path = _runtime(tmp_path)
    raw_path.write_text("{}", encoding="utf-8")
    with pytest.raises(NotebookSourceRuntimeError, match="digest mismatch"):
        runtime.search(
            project_id="project-notebook-runtime",
            query="score",
            task=NotebookQueryTaskV2.SEARCH,
            allowed_acl_refs=["project:notebook-runtime", "notebook:private"],
            enforce_acl=True,
        )


@pytest.mark.parametrize(
    "task",
    (
        NotebookQueryTaskV2.SEARCH,
        NotebookQueryTaskV2.OUTPUT,
        NotebookQueryTaskV2.ERROR,
        NotebookQueryTaskV2.REPRODUCTION,
        NotebookQueryTaskV2.CODE,
        NotebookQueryTaskV2.LINEAGE,
        NotebookQueryTaskV2.PARAMETER,
    ),
)
def test_notebook_runtime_sends_the_explicit_task_to_the_retriever(
    tmp_path: Path,
    task: NotebookQueryTaskV2,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, _ = _runtime(tmp_path)
    sent_tasks: list[NotebookQueryTaskV2] = []
    search = NotebookRetrieverV2.search

    def tracked_search(self, **kwargs):
        sent_tasks.append(kwargs["task_override"])
        return search(self, **kwargs)

    monkeypatch.setattr(NotebookRetrieverV2, "search", tracked_search)

    result = runtime.search(
        project_id="project-notebook-runtime",
        query="neutral sentinel",
        task=task,
        allowed_acl_refs=["project:notebook-runtime", "notebook:private"],
        enforce_acl=True,
    )

    assert sent_tasks == [task]
    assert result["task"] == task
    assert result["trace"]["task"] == task


@pytest.mark.parametrize("task", (None, "locate", "trend", object()))
def test_notebook_runtime_rejects_invalid_task_before_source_io(task: object) -> None:
    class NoSourceIoWorkspace:
        @staticmethod
        def _require_project(_project_id: str) -> dict[str, str]:
            pytest.fail("invalid Notebook task reached source I/O")

    runtime = object.__new__(NotebookSourceRuntimeV2)
    runtime.workspace = NoSourceIoWorkspace()

    with pytest.raises(NotebookSourceRuntimeError, match="notebook_task_invalid"):
        runtime.search(
            project_id="project-notebook-runtime",
            query="neutral sentinel",
            task=task,  # type: ignore[arg-type]
        )


def test_notebook_runtime_rejects_compare_without_selector_before_source_io() -> None:
    class NoSourceIoWorkspace:
        @staticmethod
        def _require_project(_project_id: str) -> dict[str, str]:
            pytest.fail("incomplete Notebook compare reached source I/O")

    runtime = object.__new__(NotebookSourceRuntimeV2)
    runtime.workspace = NoSourceIoWorkspace()

    with pytest.raises(NotebookSourceRuntimeError, match="comparison_spec_required"):
        runtime.search(
            project_id="project-notebook-runtime",
            query="neutral sentinel",
            task=NotebookQueryTaskV2.COMPARE,
        )
