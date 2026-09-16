"""Build versioned Notebook symbols and cell-level dependency edges."""

from __future__ import annotations

import hashlib

from .code_analyzer import NotebookCodeAnalysis
from .contracts import (
    NotebookArtifact,
    NotebookCellExecution,
    NotebookCellVersion,
    NotebookEdge,
    NotebookEdgeType,
    NotebookExecution,
    NotebookIdentityConfidence,
    NotebookParameter,
    NotebookRevision,
    NotebookScope,
    NotebookSymbol,
)

NOTEBOOK_DEPENDENCY_GRAPH_VERSION = "notebook-dependency-graph-v2"


def _id(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha256("\x1f".join((prefix, *parts)).encode()).hexdigest()


def build_notebook_dependency_graph_v2(
    *,
    revision_id: str,
    execution_id: str,
    scope: NotebookScope,
    cells: tuple[NotebookCellVersion, ...],
    executions: tuple[NotebookCellExecution, ...],
    analyses: tuple[NotebookCodeAnalysis, ...],
) -> tuple[tuple[NotebookSymbol, ...], tuple[NotebookEdge, ...]]:
    if not (len(cells) == len(executions) == len(analyses)):
        raise ValueError("dependency graph inputs must share exact cell membership")
    execution_by_cell = {item.cell_version_id: item for item in executions}
    symbols: list[NotebookSymbol] = []
    edges: list[NotebookEdge] = []
    latest_definition: dict[str, str] = {}
    for cell, analysis in zip(cells, analyses, strict=True):
        execution = execution_by_cell[cell.cell_version_id]
        confidence = (
            NotebookIdentityConfidence.AMBIGUOUS
            if not analysis.parser_available
            or analysis.diagnostics
            or execution.execution_order is None
            else NotebookIdentityConfidence.DERIVED
        )
        for role, names in (
            ("definition", analysis.definitions),
            ("read", analysis.reads),
            ("import", analysis.imports),
            ("call", analysis.calls),
            ("external", analysis.external_dependency_refs),
        ):
            for name in names:
                symbol_name = (
                    name
                    if name
                    and name[0].isalpha()
                    and all(character.isalnum() or character in "._:@-" for character in name)
                    else "symbol-" + hashlib.sha256(name.encode()).hexdigest()
                )
                symbol_id = _id(
                    "nbsym",
                    revision_id,
                    cell.cell_version_id,
                    role,
                    symbol_name,
                )
                symbols.append(
                    NotebookSymbol(
                        symbol_id=symbol_id,
                        cell_version_id=cell.cell_version_id,
                        revision_id=revision_id,
                        scope=scope,
                        name=symbol_name,
                        role=role,
                        confidence=confidence,
                        locator=f"{cell.locator}/symbol/{symbol_id}",
                    )
                )
        for name in analysis.reads:
            producer = latest_definition.get(name)
            if not producer or producer == cell.cell_version_id:
                continue
            edge_id = _id(
                "nbedge",
                revision_id,
                execution_id,
                producer,
                cell.cell_version_id,
                name,
            )
            edges.append(
                NotebookEdge(
                    edge_id=edge_id,
                    revision_id=revision_id,
                    execution_id=execution_id,
                    scope=scope,
                    source_id=producer,
                    target_id=cell.cell_version_id,
                    edge_type=NotebookEdgeType.DEPENDS_ON,
                    confidence=confidence,
                    locator=f"notebook://{scope.project_id}/edge/{edge_id}",
                )
            )
        for name in analysis.definitions:
            previous = latest_definition.get(name)
            if previous and previous != cell.cell_version_id:
                edge_id = _id(
                    "nbedge",
                    revision_id,
                    execution_id,
                    previous,
                    cell.cell_version_id,
                    "redefines",
                    name,
                )
                edges.append(
                    NotebookEdge(
                        edge_id=edge_id,
                        revision_id=revision_id,
                        execution_id=execution_id,
                        scope=scope,
                        source_id=previous,
                        target_id=cell.cell_version_id,
                        edge_type=NotebookEdgeType.REDEFINES,
                        confidence=NotebookIdentityConfidence.AMBIGUOUS,
                        locator=f"notebook://{scope.project_id}/edge/{edge_id}",
                    )
                )
            latest_definition[name] = cell.cell_version_id
    unique_symbols = {item.symbol_id: item for item in symbols}
    unique_edges = {item.edge_id: item for item in edges}
    return (
        tuple(unique_symbols[key] for key in sorted(unique_symbols)),
        tuple(unique_edges[key] for key in sorted(unique_edges)),
    )


def build_notebook_structural_edges_v2(
    *,
    revision: NotebookRevision,
    execution: NotebookExecution,
    scope: NotebookScope,
    cells: tuple[NotebookCellVersion, ...],
    cell_executions: tuple[NotebookCellExecution, ...],
    parameters: tuple[NotebookParameter, ...],
    artifacts: tuple[NotebookArtifact, ...],
) -> tuple[NotebookEdge, ...]:
    if revision.scope != scope or execution.scope != scope:
        raise ValueError("structural graph roots must share exact scope")
    if {item.cell_version_id for item in cells} != set(revision.cell_version_ids):
        raise ValueError("structural graph revision membership mismatch")
    if {item.cell_execution_id for item in cell_executions} != set(execution.cell_execution_ids):
        raise ValueError("structural graph execution membership mismatch")
    if {item.parameter_id for item in parameters} != set(execution.parameter_ids):
        raise ValueError("structural graph parameter membership mismatch")

    edges: list[NotebookEdge] = []

    def append(
        source_id: str,
        target_id: str,
        edge_type: NotebookEdgeType,
        *,
        execution_bound: bool,
    ) -> None:
        edge_id = _id(
            "nbedge",
            revision.revision_id,
            execution.execution_id if execution_bound else "revision",
            source_id,
            target_id,
            edge_type,
        )
        edges.append(
            NotebookEdge(
                edge_id=edge_id,
                revision_id=revision.revision_id,
                execution_id=execution.execution_id if execution_bound else None,
                scope=scope,
                source_id=source_id,
                target_id=target_id,
                edge_type=edge_type,
                confidence=NotebookIdentityConfidence.DERIVED,
                locator=f"notebook://{scope.project_id}/edge/{edge_id}",
            )
        )

    for cell in cells:
        append(
            revision.revision_id,
            cell.cell_version_id,
            NotebookEdgeType.CONTAINS,
            execution_bound=False,
        )
    append(
        execution.execution_id,
        revision.revision_id,
        NotebookEdgeType.EXECUTES,
        execution_bound=True,
    )
    for observed in cell_executions:
        append(
            execution.execution_id,
            observed.cell_execution_id,
            NotebookEdgeType.CONTAINS,
            execution_bound=True,
        )
        append(
            observed.cell_execution_id,
            observed.cell_version_id,
            NotebookEdgeType.EXECUTES,
            execution_bound=True,
        )
    for parameter in parameters:
        append(
            execution.execution_id,
            parameter.parameter_id,
            NotebookEdgeType.CONTAINS,
            execution_bound=True,
        )
    for artifact in artifacts:
        append(
            artifact.cell_execution_id,
            artifact.artifact_id,
            NotebookEdgeType.PRODUCES,
            execution_bound=True,
        )
    unique = {item.edge_id: item for item in edges}
    return tuple(unique[key] for key in sorted(unique))


__all__ = [
    "NOTEBOOK_DEPENDENCY_GRAPH_VERSION",
    "build_notebook_dependency_graph_v2",
    "build_notebook_structural_edges_v2",
]
