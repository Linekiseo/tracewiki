from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..config import Settings
from ..sources.models import SourceEventInput
from ..sources.service import RawSourceService
from ..workspace.models import RelationCreate
from ..workspace.service import WorkspaceService
from .adapter import NotebookAdapter
from .models import NotebookCompareRequest, NotebookIngestRequest
from .store import NotebookStore


class NotebookError(ValueError):
    pass


class NotebookService:
    def __init__(
        self,
        store: NotebookStore,
        workspace: WorkspaceService,
        sources: RawSourceService,
        settings: Settings,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.sources = sources
        self.settings = settings
        self.adapter = NotebookAdapter()

    def ingest(self, request: NotebookIngestRequest) -> dict[str, Any]:
        self.workspace._require_project(request.project_id)
        path = Path(request.source).expanduser().resolve()
        if not any(path.is_relative_to(root) for root in self.settings.allowed_local_roots):
            raise NotebookError("notebook source is outside allowed local roots")
        if path.suffix.casefold() != ".ipynb" or not path.is_file():
            raise NotebookError("notebook source must be an existing .ipynb file")
        if path.stat().st_size > 50_000_000:
            raise NotebookError("notebook exceeds 50 MB limit")
        payload = path.read_bytes()
        content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        parsed = self.adapter.parse(path)
        template_token = hashlib.sha256(f"{request.project_id}\x1f{path}".encode()).hexdigest()
        run_token = hashlib.sha256(
            f"{template_token}\x1f{request.version}\x1f{content_hash}".encode()
        ).hexdigest()
        template_id = f"notebook://{request.project_id}/template/{template_token}"
        notebook_run_id = f"notebook://{request.project_id}/run/{run_token}"
        raw = self.sources.accept(
            SourceEventInput(
                source_type="notebook",
                source_instance=str(path),
                event_type="notebook.execution.observed",
                source_object_id=template_id,
                source_version=request.version + ":" + content_hash,
                event_time=parsed.get("completed_at") or parsed.get("started_at"),
                project_id=request.project_id,
                acl_ref=request.acl_ref,
                source_uri=str(path),
                payload=payload,
                media_type="application/x-ipynb+json",
                adapter_version=self.adapter.adapter_version,
                schema_version="jupyter-notebook-v1",
            )
        )["raw_object"]
        cells = []
        for cell in parsed["cells"]:
            cell_id = f"{notebook_run_id}/cell/{cell['ordinal']}"
            cells.append(
                {
                    **cell,
                    "id": cell_id,
                    "source_locator": f"{path}#cell={cell['ordinal']}",
                    "outputs": [
                        {**output, "id": f"{cell_id}/output/{output['ordinal']}"}
                        for output in cell["outputs"]
                    ],
                }
            )
        saved = self.store.save(
            {
                "id": template_id,
                "project_id": request.project_id,
                "name": path.stem,
                "source_uri": str(path),
                "content_hash": content_hash,
                "kernel_name": parsed.get("kernel_name"),
                "language": parsed.get("language"),
                "acl_ref": request.acl_ref,
                "metadata": parsed.get("metadata", {}),
            },
            {
                "id": notebook_run_id,
                "project_id": request.project_id,
                "experiment_id": request.experiment_id,
                "run_id": request.run_id,
                "version": request.version,
                "status": parsed["status"],
                "started_at": parsed.get("started_at"),
                "completed_at": parsed.get("completed_at"),
                "parameters": parsed["parameters"],
                "raw_object_id": raw["id"],
                "content_hash": content_hash,
                "metadata": {"adapter_version": self.adapter.adapter_version},
            },
            cells,
        )
        derived = [saved["id"]]
        for cell in saved["cells"]:
            derived.append(cell["id"])
            derived.extend(output["id"] for output in cell["outputs"])
        self.sources.store.link_derivations(
            raw["id"],
            derived,
            kind="notebook_parse",
            generation_id=None,
            derivation_version=self.adapter.adapter_version,
        )
        if request.run_id and self.workspace.store.entity_exists(request.run_id):
            existing = self.workspace.store.list_relations(
                request.project_id, entity_id=request.run_id, limit=200
            )
            if not any(item["target_entity_id"] == saved["id"] for item in existing):
                self.workspace.create_relation(
                    RelationCreate(
                        project_id=request.project_id,
                        source_entity_id=request.run_id,
                        predicate="has_notebook",
                        target_entity_id=saved["id"],
                        evidence_entity_id=saved["id"],
                        derivation="deterministic",
                        confidence=1.0,
                        review_status="confirmed",
                        rule_version="notebook-run-link-v1",
                    )
                )
        return saved

    def compare(self, request: NotebookCompareRequest) -> dict[str, Any]:
        baseline = self.store.get_run(request.baseline_id)
        candidate = self.store.get_run(request.candidate_id)
        if not baseline or not candidate:
            raise NotebookError("notebook run not found")
        changes = []
        length = max(len(baseline["cells"]), len(candidate["cells"]))
        for ordinal in range(length):
            left = baseline["cells"][ordinal] if ordinal < len(baseline["cells"]) else None
            right = candidate["cells"][ordinal] if ordinal < len(candidate["cells"]) else None
            if left is None or right is None:
                changes.append(
                    {"ordinal": ordinal, "change": "added" if left is None else "removed"}
                )
                continue
            if left["source_hash"] != right["source_hash"] or left["outputs"] != right["outputs"]:
                changes.append(
                    {
                        "ordinal": ordinal,
                        "change": "modified",
                        "source_changed": left["source_hash"] != right["source_hash"],
                        "execution_count": [
                            left.get("execution_count"),
                            right.get("execution_count"),
                        ],
                        "output_count": [len(left["outputs"]), len(right["outputs"])],
                    }
                )
        return {
            "baseline_id": baseline["id"],
            "candidate_id": candidate["id"],
            "parameter_changes": self._dict_diff(baseline["parameters"], candidate["parameters"]),
            "cell_changes": changes,
        }

    @staticmethod
    def _dict_diff(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"key": key, "baseline": left.get(key), "candidate": right.get(key)}
            for key in sorted(set(left) | set(right))
            if left.get(key) != right.get(key)
        ]
