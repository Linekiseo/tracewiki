"""Versioned Notebook retrieval-unit builder."""

from __future__ import annotations

import hashlib

from .contracts import (
    NotebookArtifact,
    NotebookArtifactType,
    NotebookCellExecution,
    NotebookCellType,
    NotebookCellVersion,
    NotebookExecution,
    NotebookParameter,
    NotebookRetrievalUnit,
    NotebookRevision,
    NotebookScope,
    canonical_sha256,
)

NOTEBOOK_UNIT_BUILDER_VERSION = "notebook-unit-builder-v2"


def _id(*parts: str) -> str:
    return "nbunit-" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _unit(
    *,
    revision_id: str,
    execution_id: str | None,
    scope: NotebookScope,
    entity_id: str,
    unit_type: str,
    profile: str,
    content: str,
    locator: str,
) -> NotebookRetrievalUnit:
    normalized = content.strip() or f"{unit_type} evidence"
    content_sha256 = canonical_sha256(
        {
            "builder_version": NOTEBOOK_UNIT_BUILDER_VERSION,
            "unit_type": unit_type,
            "profile": profile,
            "content": normalized,
        }
    )
    return NotebookRetrievalUnit(
        unit_id=_id(
            revision_id,
            execution_id or "revision",
            entity_id,
            unit_type,
            profile,
            content_sha256,
        ),
        revision_id=revision_id,
        execution_id=execution_id,
        scope=scope,
        entity_id=entity_id,
        unit_type=unit_type,
        profile=profile,
        content=normalized,
        content_sha256=content_sha256,
        locator=locator,
    )


def build_notebook_retrieval_units_v2(
    *,
    revision: NotebookRevision,
    execution: NotebookExecution,
    scope: NotebookScope,
    cells: tuple[NotebookCellVersion, ...],
    cell_executions: tuple[NotebookCellExecution, ...],
    parameters: tuple[NotebookParameter, ...],
    artifacts: tuple[NotebookArtifact, ...],
) -> tuple[NotebookRetrievalUnit, ...]:
    execution_by_cell = {item.cell_version_id: item for item in cell_executions}
    cell_by_execution = {
        item.cell_execution_id: next(
            cell for cell in cells if cell.cell_version_id == item.cell_version_id
        )
        for item in cell_executions
    }
    units: list[NotebookRetrievalUnit] = [
        _unit(
            revision_id=revision.revision_id,
            execution_id=None,
            scope=scope,
            entity_id=revision.revision_id,
            unit_type="revision",
            profile="identity-v2",
            content=(
                f"notebook {revision.template_id} revision {revision.source_version} "
                f"nbformat {revision.nbformat}.{revision.nbformat_minor}"
            ),
            locator=revision.locator,
        ),
        _unit(
            revision_id=revision.revision_id,
            execution_id=execution.execution_id,
            scope=scope,
            entity_id=execution.execution_id,
            unit_type="execution",
            profile="execution-v2",
            content=(
                f"notebook {revision.template_id} revision {revision.source_version} "
                f"execution {execution.execution_key} status {execution.status} "
                f"started {execution.started_at or 'unknown'} "
                f"completed {execution.completed_at or 'unknown'}"
            ),
            locator=execution.locator,
        ),
    ]
    for cell in cells:
        observed = execution_by_cell[cell.cell_version_id]
        profile = (
            "code-v2"
            if cell.cell_type == NotebookCellType.CODE
            else "markdown-v2"
            if cell.cell_type == NotebookCellType.MARKDOWN
            else "raw-v2"
        )
        units.append(
            _unit(
                revision_id=revision.revision_id,
                execution_id=None,
                scope=scope,
                entity_id=cell.cell_version_id,
                unit_type="cell",
                profile=profile,
                content=(
                    f"cell {cell.native_cell_id or cell.stable_cell_id} "
                    f"type {cell.cell_type} display_order {cell.display_order} "
                    f"tags {' '.join(cell.tags) or 'none'}\n{cell.source}"
                ),
                locator=cell.locator,
            )
        )
        units.append(
            _unit(
                revision_id=revision.revision_id,
                execution_id=execution.execution_id,
                scope=scope,
                entity_id=observed.cell_execution_id,
                unit_type="cell_execution",
                profile="execution-state-v2",
                content=(
                    f"cell {cell.native_cell_id or cell.stable_cell_id} "
                    f"type {cell.cell_type} state {observed.state} "
                    f"display_order {observed.display_order} "
                    f"execution_count {observed.execution_count} "
                    f"execution_order {observed.execution_order} "
                    f"{'stale' if observed.stale else 'fresh'} "
                    f"diagnostics {' '.join(observed.diagnostics) or 'none'}"
                ),
                locator=observed.locator,
            )
        )
    for parameter in parameters:
        units.append(
            _unit(
                revision_id=revision.revision_id,
                execution_id=execution.execution_id,
                scope=scope,
                entity_id=parameter.parameter_id,
                unit_type="parameter",
                profile="parameter-v2",
                content=(
                    f"parameter {parameter.name} type {parameter.value_type} "
                    f"value {parameter.canonical_value}"
                ),
                locator=parameter.locator,
            )
        )
    for artifact in artifacts:
        producer = cell_by_execution[artifact.cell_execution_id]
        unit_type = "error" if artifact.artifact_type == NotebookArtifactType.ERROR else "output"
        units.append(
            _unit(
                revision_id=revision.revision_id,
                execution_id=execution.execution_id,
                scope=scope,
                entity_id=artifact.artifact_id,
                unit_type=unit_type,
                profile=f"{unit_type}-v2",
                content=(
                    f"{unit_type} from cell "
                    f"{producer.native_cell_id or producer.stable_cell_id} "
                    f"producer {producer.source[:500]} "
                    f"{artifact.error_name or ''} "
                    f"{artifact.error_value or ''} {artifact.text} "
                    "metric_unconfirmed"
                ),
                locator=artifact.locator,
            )
        )
    unique = {item.unit_id: item for item in units}
    return tuple(unique[key] for key in sorted(unique))


__all__ = [
    "NOTEBOOK_UNIT_BUILDER_VERSION",
    "build_notebook_retrieval_units_v2",
]
