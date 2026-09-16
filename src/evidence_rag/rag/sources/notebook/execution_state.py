"""Honest display-order versus execution-order projection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .contracts import NotebookCellExecutionState

NOTEBOOK_EXECUTION_STATE_VERSION = "notebook-execution-state-v2"


@dataclass(frozen=True, slots=True)
class NotebookCellExecutionInput:
    display_order: int
    cell_type: str
    execution_count: int | None
    has_outputs: bool
    has_error: bool


@dataclass(frozen=True, slots=True)
class NotebookCellExecutionProjection:
    display_order: int
    execution_count: int | None
    execution_order: int | None
    state: NotebookCellExecutionState
    stale: bool
    diagnostics: tuple[str, ...]


def project_notebook_execution_state_v2(
    cells: tuple[NotebookCellExecutionInput, ...],
) -> tuple[NotebookCellExecutionProjection, ...]:
    if tuple(item.display_order for item in cells) != tuple(range(len(cells))):
        raise ValueError("display order must be contiguous and canonical")
    observed = [
        item.execution_count
        for item in cells
        if item.cell_type == "code" and item.execution_count is not None
    ]
    frequency = Counter(observed)
    has_duplicate = any(count > 1 for count in frequency.values())
    has_decrease = any(left > right for left, right in zip(observed, observed[1:], strict=False))
    sorted_unique = {value: index for index, value in enumerate(sorted(set(observed)))}
    gaps = {
        right
        for left, right in zip(sorted(set(observed)), sorted(set(observed))[1:], strict=False)
        if right - left > 1
    }
    projections: list[NotebookCellExecutionProjection] = []
    for item in cells:
        diagnostics: set[str] = set()
        stale = item.execution_count is None and item.has_outputs
        if stale:
            diagnostics.add("output-without-execution-count")
        if item.execution_count is not None and frequency[item.execution_count] > 1:
            diagnostics.add("duplicate-execution-count")
        if has_decrease:
            diagnostics.add("kernel-restart-or-reordered")
        if item.execution_count in gaps:
            diagnostics.add("execution-count-gap-before")
        trustworthy_order = (
            item.execution_count is not None and not has_duplicate and not has_decrease
        )
        execution_order = sorted_unique[item.execution_count] if trustworthy_order else None
        state = (
            NotebookCellExecutionState.FAILED
            if item.has_error
            else NotebookCellExecutionState.EXECUTED
            if item.cell_type == "code" and item.execution_count is not None
            else NotebookCellExecutionState.NOT_EXECUTED
            if item.cell_type == "code"
            else NotebookCellExecutionState.UNKNOWN
        )
        projections.append(
            NotebookCellExecutionProjection(
                display_order=item.display_order,
                execution_count=item.execution_count,
                execution_order=execution_order,
                state=state,
                stale=stale,
                diagnostics=tuple(sorted(diagnostics)),
            )
        )
    return tuple(projections)


__all__ = [
    "NOTEBOOK_EXECUTION_STATE_VERSION",
    "NotebookCellExecutionInput",
    "NotebookCellExecutionProjection",
    "project_notebook_execution_state_v2",
]
