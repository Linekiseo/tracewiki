"""Production-store facade for the complete Notebook Source V2 pipeline."""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ....notebooks.store import NotebookStore
from ....sources.service import RawSourceService
from ....workspace.service import WorkspaceService
from .adapter_v2 import NotebookAdapterV2
from .cell_matcher import (
    NotebookComparisonResult,
    NotebookComparisonSelectionError,
    NotebookComparisonSpecV2,
    NotebookPublicationSelectorV2,
    compare_notebook_publications_v2,
    select_notebook_comparison_v2,
)
from .context import build_notebook_context_from_search_v2
from .contracts import NotebookPublication
from .retriever import (
    NotebookCandidate,
    NotebookFilterError,
    NotebookQueryTask,
    NotebookRetrieverV2,
    NotebookSearchFiltersV2,
    NotebookSearchScope,
    filter_notebook_publications_v2,
)
from .store import NotebookV2Store

NOTEBOOK_SOURCE_RUNTIME_VERSION = "notebook-production-runtime-v2"
NOTEBOOK_SOURCE_DEFAULT_ENGINE = "v1"


class NotebookSourceRuntimeError(RuntimeError):
    """A safe fail-closed Notebook V2 preparation or retrieval failure."""


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _snapshot_generation(rows: list[dict[str, Any]]) -> tuple[str, str]:
    payload = [
        {
            "id": str(item.get("id") or ""),
            "template_id": str(item.get("template_id") or ""),
            "content_hash": str(item.get("content_hash") or ""),
            "version": str(item.get("version") or ""),
            "acl_ref": str(item.get("acl_ref") or ""),
            "created_at": str(item.get("created_at") or ""),
        }
        for item in sorted(rows, key=lambda row: str(row.get("id") or ""))
    ]
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    watermark = max((item["created_at"] for item in payload if item["created_at"]), default="")
    return f"notebook-store-snapshot-v2:{digest}", watermark or "unavailable"


def _safe_raw_bytes(raw_sources: RawSourceService, raw: dict[str, Any]) -> bytes:
    storage_value = raw.get("storage_path")
    if not storage_value:
        raise NotebookSourceRuntimeError("Notebook V2 source payload is unavailable")
    root = raw_sources.root.resolve(strict=True)
    candidate = Path(str(storage_value))
    if not candidate.is_absolute():
        raise NotebookSourceRuntimeError("Notebook V2 source payload is outside managed storage")
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise NotebookSourceRuntimeError(
            "Notebook V2 source payload is outside managed storage"
        ) from error
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        try:
            mode = cursor.lstat().st_mode
        except OSError as error:
            raise NotebookSourceRuntimeError("Notebook V2 source payload is unavailable") from error
        if stat.S_ISLNK(mode):
            raise NotebookSourceRuntimeError("Notebook V2 source payload traverses a symlink")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not stat.S_ISREG(resolved.lstat().st_mode):
        raise NotebookSourceRuntimeError("Notebook V2 source payload is not a managed file")
    try:
        payload = resolved.read_bytes()
    except OSError as error:
        raise NotebookSourceRuntimeError("Notebook V2 source payload is unreadable") from error
    if _sha256(payload) != str(raw.get("content_hash") or ""):
        raise NotebookSourceRuntimeError("Notebook V2 source payload digest mismatch")
    return payload


def _formal_identity_map(
    publication: NotebookPublication,
    run: dict[str, Any],
) -> dict[str, str]:
    identities = {
        publication.template.template_id: str(run["template_id"]),
        publication.revision.revision_id: str(run["id"]),
        publication.execution.execution_id: str(run["id"]),
    }
    cells = {int(item["ordinal"]): item for item in run.get("cells") or ()}
    executions_by_version = {item.cell_version_id: item for item in publication.cell_executions}
    for cell in publication.cell_versions:
        formal = cells.get(cell.display_order)
        if formal is None:
            continue
        formal_id = str(formal["id"])
        identities[cell.cell_version_id] = formal_id
        execution = executions_by_version.get(cell.cell_version_id)
        if execution is None:
            continue
        identities[execution.cell_execution_id] = formal_id
        outputs = {int(item["ordinal"]): str(item["id"]) for item in formal.get("outputs") or ()}
        for artifact in publication.artifacts:
            if artifact.cell_execution_id == execution.cell_execution_id:
                identities[artifact.artifact_id] = outputs.get(
                    artifact.ordinal, artifact.artifact_id
                )
    return identities


class NotebookSourceRuntimeV2:
    """Read authoritative Notebook rows, publish typed V2 units, and retrieve them."""

    def __init__(
        self,
        *,
        notebooks: NotebookStore,
        workspace: WorkspaceService,
        raw_sources: RawSourceService,
        index: NotebookV2Store,
    ) -> None:
        self.notebooks = notebooks
        self.workspace = workspace
        self.raw_sources = raw_sources
        self.index = index
        self.adapter = NotebookAdapterV2()

    def search(
        self,
        *,
        project_id: str,
        query: str,
        allowed_acl_refs: list[str] | tuple[str, ...] | None = None,
        enforce_acl: bool = False,
        limit: int = 20,
        filters: NotebookSearchFiltersV2 | Mapping[str, Any] | None = None,
        comparison: NotebookComparisonSpecV2 | Mapping[str, Any] | None = None,
        task: NotebookQueryTask | str | None,
    ) -> dict[str, Any]:
        if not query.strip():
            raise NotebookSourceRuntimeError("Notebook V2 query must not be empty")
        try:
            requested_task = NotebookQueryTask(task)
        except (TypeError, ValueError) as error:
            raise NotebookSourceRuntimeError("notebook_task_invalid") from error
        if requested_task is NotebookQueryTask.COMPARE and comparison is None:
            raise NotebookSourceRuntimeError("comparison_spec_required")
        if requested_task is not NotebookQueryTask.COMPARE and comparison is not None:
            raise NotebookSourceRuntimeError("comparison_task_mismatch")
        project = self.workspace._require_project(project_id)
        allowed = tuple(sorted({"public", *(allowed_acl_refs or ())}))
        project_acl = str(project.get("acl_ref") or "")
        if enforce_acl and project_acl not in set(allowed):
            return self._empty(
                "acl_denied",
                "project_acl_denied",
                task=requested_task,
            )

        all_rows = self.notebooks.list_runs(project_id)
        generation_id, watermark = _snapshot_generation(all_rows)
        authorized = self.notebooks.list_runs(
            project_id,
            allowed_acl_refs=allowed,
            enforce_acl=enforce_acl,
        )
        if not enforce_acl:
            allowed = tuple(
                sorted(
                    {
                        *allowed,
                        project_acl,
                        *(str(item.get("acl_ref") or "") for item in authorized),
                    }
                    - {""}
                )
            )
        if not authorized:
            status = "acl_denied" if all_rows and enforce_acl else "not_indexed"
            reason = "authorized_notebook_evidence" if all_rows else "notebook_not_indexed"
            return self._empty(
                status,
                reason,
                task=requested_task,
                generation_id=generation_id,
                watermark=watermark,
            )

        self.index.initialize()
        publications: list[NotebookPublication] = []
        identities: dict[str, str] = {}
        bindings: list[tuple[NotebookPublication, dict[str, Any]]] = []
        for summary in authorized:
            run = self.notebooks.get_run(
                str(summary["id"]),
                allowed_acl_refs=allowed,
                enforce_acl=enforce_acl,
            )
            if run is None or str(run.get("project_id") or "") != project_id:
                raise NotebookSourceRuntimeError("Notebook V2 authoritative row changed")
            raw_id = str(run.get("raw_object_id") or "")
            raw = self.raw_sources.store.get_object(raw_id) if raw_id else None
            if (
                raw is None
                or str(raw.get("project_id") or "") != project_id
                or str(raw.get("source_type") or "") != "notebook"
                or str(raw.get("state") or "") != "active"
                or str(raw.get("acl_ref") or "") != str(run.get("acl_ref") or "")
                or str(raw.get("content_hash") or "") != str(run.get("content_hash") or "")
            ):
                raise NotebookSourceRuntimeError(
                    "Notebook V2 authoritative payload binding is invalid"
                )
            payload = _safe_raw_bytes(self.raw_sources, raw)
            source_key = (
                "formal-"
                + hashlib.sha256(str(run["template_id"]).encode()).hexdigest()[:32]
                + ".ipynb"
            )
            publication = self.adapter.build_publication(
                payload=payload,
                project_id=project_id,
                acl_ref=str(run["acl_ref"]),
                generation_id=generation_id,
                source_key=source_key,
                source_version=str(run.get("version") or "unversioned"),
                execution_key="formal-" + hashlib.sha256(str(run["id"]).encode()).hexdigest()[:32],
            )
            self.index.publish(publication)
            publications.append(publication)
            identities.update(_formal_identity_map(publication, run))
            bindings.append((publication, run))

        scope = NotebookSearchScope(
            project_id=project_id,
            allowed_acl_refs=allowed,
            generation_id=generation_id,
        )
        translated_filters = self._translate_filters(filters, bindings)
        selected_publications = filter_notebook_publications_v2(
            tuple(publications),
            translated_filters,
        )
        comparison_result: NotebookComparisonResult | None = None
        comparison_trace: dict[str, Any] | None = None
        if comparison is not None:
            translated_comparison = self._translate_comparison(comparison, bindings)
            baseline, candidate, selected_trace = select_notebook_comparison_v2(
                selected_publications,
                translated_comparison,
            )
            comparison_result = compare_notebook_publications_v2(baseline, candidate)
            comparison_trace = selected_trace.model_dump(mode="json")
            comparison_trace["requested"] = (
                comparison.model_dump(mode="json")
                if isinstance(comparison, NotebookComparisonSpecV2)
                else dict(comparison)
            )
        result = NotebookRetrieverV2(self.index).search(
            scope=scope,
            query=query,
            limit=limit,
            filters=translated_filters,
            task_override=requested_task,
        )
        if result.trace.task is NotebookQueryTask.COMPARE and comparison_result is None:
            raise NotebookComparisonSelectionError("comparison_spec_required")
        context = build_notebook_context_from_search_v2(
            scope=scope,
            search=result,
            comparison=comparison_result,
        )
        context_payload = context.model_dump(mode="json")
        context_payload.pop("allowed_acl_refs", None)
        context_payload["schema_version"] = "notebook-execution-context-v2"
        context_payload["dependencies"] = [
            edge.model_dump(mode="json")
            for publication in publications
            for edge in publication.edges
        ]
        mapped = [
            self._candidate(candidate, identities, publications) for candidate in result.candidates
        ]
        selection_trace = {
            **result.trace.selection_trace.model_dump(mode="json"),
            "comparison": comparison_trace,
        }
        return {
            "task": requested_task,
            "results": mapped,
            "context": context_payload,
            "trace": {
                **result.trace.model_dump(mode="json"),
                "engine": NOTEBOOK_SOURCE_RUNTIME_VERSION,
                "source_status": "complete" if mapped else "no_match",
                "index_generation": [generation_id],
                "watermark": watermark,
                "formal_run_count": len(authorized),
                "default_engine": NOTEBOOK_SOURCE_DEFAULT_ENGINE,
                "explicit_v2_required": True,
                "fallback_used": False,
                "selection_trace": selection_trace,
            },
            "index_generation": [generation_id],
            "watermark": watermark,
            "comparisons": (
                [comparison_result.model_dump(mode="json")] if comparison_result is not None else []
            ),
            "selection_trace": selection_trace,
        }

    @staticmethod
    def _identity(
        requested: str,
        bindings: list[tuple[NotebookPublication, dict[str, Any]]],
        *,
        kind: str,
    ) -> str:
        matches: set[str] = set()
        for publication, run in bindings:
            internal = {
                "template": publication.template.template_id,
                "revision": publication.revision.revision_id,
                "execution": publication.execution.execution_id,
            }[kind]
            formal = str(run["template_id"]) if kind == "template" else str(run["id"])
            if requested in {internal, formal}:
                matches.add(internal)
        if not matches:
            raise NotebookFilterError("notebook_filter_identity_not_found")
        if len(matches) != 1:
            raise NotebookFilterError("notebook_filter_identity_ambiguous")
        return next(iter(matches))

    @classmethod
    def _translate_filters(
        cls,
        filters: NotebookSearchFiltersV2 | Mapping[str, Any] | None,
        bindings: list[tuple[NotebookPublication, dict[str, Any]]],
    ) -> NotebookSearchFiltersV2:
        if filters is None:
            return NotebookSearchFiltersV2()
        resolved = (
            filters
            if isinstance(filters, NotebookSearchFiltersV2)
            else NotebookSearchFiltersV2.model_validate(filters)
        )
        cell_aliases: dict[str, set[str]] = {}
        output_aliases: dict[str, set[str]] = {}
        for publication, run in bindings:
            formal_cells = {
                int(item["ordinal"]): str(item["id"]) for item in run.get("cells") or ()
            }
            for cell in publication.cell_versions:
                formal = formal_cells.get(cell.display_order)
                if formal is not None:
                    cell_aliases.setdefault(formal, set()).add(cell.cell_version_id)
            formal_identities = _formal_identity_map(publication, run)
            artifact_ids = {item.artifact_id for item in publication.artifacts}
            for internal, formal in formal_identities.items():
                if internal in artifact_ids:
                    output_aliases.setdefault(formal, set()).add(internal)
        translated_cells: list[str] = []
        for requested in resolved.cell_ids:
            matches = cell_aliases.get(requested)
            if matches is None:
                translated_cells.append(requested)
            elif len(matches) == 1:
                translated_cells.append(next(iter(matches)))
            else:
                raise NotebookFilterError("notebook_cell_identity_ambiguous")
        translated_outputs: list[str] = []
        for requested in resolved.output_ids:
            matches = output_aliases.get(requested)
            if matches is None:
                translated_outputs.append(requested)
            elif len(matches) == 1:
                translated_outputs.append(next(iter(matches)))
            else:
                raise NotebookFilterError("notebook_output_identity_ambiguous")
        return NotebookSearchFiltersV2(
            template_ids=tuple(
                sorted(
                    cls._identity(item, bindings, kind="template") for item in resolved.template_ids
                )
            ),
            revision_ids=tuple(
                sorted(
                    cls._identity(item, bindings, kind="revision") for item in resolved.revision_ids
                )
            ),
            execution_ids=tuple(
                sorted(
                    cls._identity(item, bindings, kind="execution")
                    for item in resolved.execution_ids
                )
            ),
            cell_ids=tuple(sorted(translated_cells)),
            statuses=resolved.statuses,
            parameter_names=resolved.parameter_names,
            parameter_ids=resolved.parameter_ids,
            output_ids=tuple(sorted(translated_outputs)),
            output_types=resolved.output_types,
        )

    @classmethod
    def _translate_comparison(
        cls,
        comparison: NotebookComparisonSpecV2 | Mapping[str, Any],
        bindings: list[tuple[NotebookPublication, dict[str, Any]]],
    ) -> NotebookComparisonSpecV2:
        resolved = (
            comparison
            if isinstance(comparison, NotebookComparisonSpecV2)
            else NotebookComparisonSpecV2.model_validate(comparison)
        )

        def selector(value: NotebookPublicationSelectorV2) -> NotebookPublicationSelectorV2:
            return NotebookPublicationSelectorV2(
                template_id=cls._identity(value.template_id, bindings, kind="template"),
                revision_id=cls._identity(value.revision_id, bindings, kind="revision"),
                execution_id=cls._identity(value.execution_id, bindings, kind="execution"),
            )

        return NotebookComparisonSpecV2(
            baseline=selector(resolved.baseline),
            candidate=selector(resolved.candidate),
        )

    @staticmethod
    def _candidate(
        candidate: NotebookCandidate,
        identities: dict[str, str],
        publications: list[NotebookPublication],
    ) -> dict[str, Any]:
        publication = next(
            item for item in publications if item.revision.revision_id == candidate.revision_id
        )
        formal_id = identities.get(candidate.entity_id, candidate.entity_id)
        return {
            "entity_id": formal_id,
            "source": "notebook",
            "entity_type": candidate.unit_type,
            "title": candidate.unit_type.replace("_", " ").title(),
            "subtitle": publication.revision.source_version,
            "snippet": candidate.content,
            "locator": candidate.locator,
            "version": publication.revision.source_version,
            "status": (
                "stale"
                if candidate.stale
                else "not_executed"
                if candidate.unexecuted
                else "observed"
            ),
            "score": candidate.score,
            "channels": [str(item) for item in candidate.channels],
            "roles": [candidate.unit_type],
            "content_digest": candidate.content_sha256,
            "redactions": [],
            "metadata": {
                "v2_entity_id": candidate.entity_id,
                "unit_id": candidate.unit_id,
                "template_id": publication.template.template_id,
                "revision_id": candidate.revision_id,
                "execution_id": candidate.execution_id,
                "reasons": list(candidate.reasons),
                "stale": candidate.stale,
                "unexecuted": candidate.unexecuted,
            },
        }

    @staticmethod
    def _empty(
        status: str,
        reason: str,
        *,
        task: NotebookQueryTask,
        generation_id: str = "not-indexed:notebook",
        watermark: str = "unavailable",
    ) -> dict[str, Any]:
        return {
            "task": task,
            "results": [],
            "context": {
                "availability": "UNAVAILABLE",
                "blocks": [],
                "citations": [],
                "missing_evidence": [reason],
                "reasoning_included": False,
            },
            "trace": {
                "engine": NOTEBOOK_SOURCE_RUNTIME_VERSION,
                "task": task,
                "source_status": status,
                "index_generation": [generation_id],
                "watermark": watermark,
                "default_engine": NOTEBOOK_SOURCE_DEFAULT_ENGINE,
                "explicit_v2_required": True,
                "fallback_used": False,
                "reasoning_included": False,
            },
            "index_generation": [generation_id],
            "watermark": watermark,
            "comparisons": [],
            "selection_trace": {
                "schema_version": "notebook-filter-selection-trace-v2",
                "filter_before_rank": True,
                "requested_filters": {},
                "publication_count_before_filter": 0,
                "publication_count_after_filter": 0,
                "unit_count_before_filter": 0,
                "unit_count_after_filter": 0,
                "selected_template_ids": [],
                "selected_revision_ids": [],
                "selected_execution_ids": [],
                "comparison": None,
            },
        }


__all__ = [
    "NOTEBOOK_SOURCE_DEFAULT_ENGINE",
    "NOTEBOOK_SOURCE_RUNTIME_VERSION",
    "NotebookSourceRuntimeError",
    "NotebookSourceRuntimeV2",
]
