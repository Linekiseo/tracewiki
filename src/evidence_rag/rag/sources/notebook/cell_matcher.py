"""Deterministic, fail-closed Notebook revision and execution comparison."""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    NotebookArtifact,
    NotebookCellExecution,
    NotebookCellVersion,
    NotebookComparison,
    NotebookParameter,
    NotebookPublication,
    canonical_sha256,
)

NOTEBOOK_CELL_MATCHER_VERSION = "notebook-cell-matcher-v2"
NOTEBOOK_COMPARISON_SELECTION_VERSION = "notebook-comparison-selection-v2"


class NotebookCellMatchMethod(StrEnum):
    NATIVE_ID = "native_id"
    STABLE_ID = "stable_id"
    SOURCE_HASH = "source_hash"
    SIMILARITY_NEIGHBOR = "similarity_neighbor"
    ORDINAL_FALLBACK = "ordinal_fallback"
    UNMATCHED = "unmatched"


class NotebookCellChangeType(StrEnum):
    UNCHANGED = "unchanged"
    ADDED = "added"
    REMOVED = "removed"
    MOVED = "moved"
    SOURCE_MODIFIED = "source_modified"
    METADATA_MODIFIED = "metadata_modified"
    REEXECUTED_SAME_SOURCE = "reexecuted_same_source"
    OUTPUT_CHANGED = "output_changed"
    EXECUTION_ORDER_CHANGED = "execution_order_changed"
    STALE_OUTPUT_CHANGED = "stale_output_changed"


class _FrozenComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class NotebookComparisonSelectionError(ValueError):
    """A bounded, public-safe failure for an exact comparison selector."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class NotebookPublicationSelectorV2(_FrozenComparison):
    template_id: str = Field(min_length=1, max_length=512)
    revision_id: str = Field(min_length=1, max_length=512)
    execution_id: str = Field(min_length=1, max_length=512)


class NotebookComparisonSpecV2(_FrozenComparison):
    baseline: NotebookPublicationSelectorV2
    candidate: NotebookPublicationSelectorV2

    @model_validator(mode="after")
    def _different(self) -> NotebookComparisonSpecV2:
        if self.baseline == self.candidate:
            raise ValueError("baseline and candidate selectors must differ")
        return self


class NotebookComparisonSelectionTraceV2(_FrozenComparison):
    selection_version: str = NOTEBOOK_COMPARISON_SELECTION_VERSION
    explicit: bool = True
    baseline: NotebookPublicationSelectorV2
    candidate: NotebookPublicationSelectorV2
    baseline_publication_id: str
    candidate_publication_id: str
    status: str = "selected"


class NotebookCellMatch(_FrozenComparison):
    baseline_cell_version_id: str | None
    candidate_cell_version_id: str | None
    baseline_locator: str | None
    candidate_locator: str | None
    method: NotebookCellMatchMethod
    confidence: float = Field(ge=0.0, le=1.0)
    change_types: tuple[NotebookCellChangeType, ...]

    @model_validator(mode="after")
    def _shape(self) -> NotebookCellMatch:
        if self.baseline_cell_version_id is None and self.candidate_cell_version_id is None:
            raise ValueError("cell match must reference at least one side")
        if self.method == NotebookCellMatchMethod.UNMATCHED:
            if (self.baseline_cell_version_id is None) == (self.candidate_cell_version_id is None):
                raise ValueError("unmatched cell must appear on exactly one side")
        elif self.baseline_cell_version_id is None or self.candidate_cell_version_id is None:
            raise ValueError("matched cell must reference both sides")
        return self


class NotebookParameterChange(_FrozenComparison):
    name: str
    baseline_type: str | None
    baseline_value_sha256: str | None
    candidate_type: str | None
    candidate_value_sha256: str | None
    change: str


class NotebookComparisonResult(_FrozenComparison):
    comparison_id: str
    matcher_version: str = NOTEBOOK_CELL_MATCHER_VERSION
    project_id: str
    acl_ref: str
    generation_id: str
    template_id: str
    baseline_revision_id: str
    candidate_revision_id: str
    baseline_execution_id: str
    candidate_execution_id: str
    matches: tuple[NotebookCellMatch, ...]
    parameter_changes: tuple[NotebookParameterChange, ...]
    baseline_status: str
    candidate_status: str
    matches_sha256: str
    locator: str
    ambiguous_pair_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _identity(self) -> NotebookComparisonResult:
        payload = self.model_dump(
            mode="json",
            exclude={"comparison_id", "matches_sha256"},
        )
        expected_matches = canonical_sha256(
            {
                "matcher_version": self.matcher_version,
                "matches": [item.model_dump(mode="json") for item in self.matches],
                "parameter_changes": [
                    item.model_dump(mode="json") for item in self.parameter_changes
                ],
            }
        )
        if self.matches_sha256 != expected_matches:
            raise ValueError("comparison match digest mismatch")
        expected_id = "nbcompare-" + canonical_sha256(payload).removeprefix("sha256:")
        if self.comparison_id != expected_id:
            raise ValueError("comparison id mismatch")
        return self

    def as_contract(self, baseline: NotebookPublication) -> NotebookComparison:
        return NotebookComparison(
            comparison_id=self.comparison_id,
            scope=baseline.template.scope,
            baseline_revision_id=self.baseline_revision_id,
            candidate_revision_id=self.candidate_revision_id,
            matches_sha256=self.matches_sha256,
            locator=self.locator,
        )


def _group_unique(
    cells: tuple[NotebookCellVersion, ...],
    key: str,
) -> dict[str, NotebookCellVersion]:
    grouped: dict[str, list[NotebookCellVersion]] = defaultdict(list)
    for cell in cells:
        value = getattr(cell, key)
        if value is not None:
            grouped[str(value)].append(cell)
    return {value: items[0] for value, items in grouped.items() if len(items) == 1}


def _normalized_source(cell: NotebookCellVersion) -> str:
    return " ".join(cell.source.split()).casefold()


def _similarity(
    baseline: NotebookCellVersion,
    candidate: NotebookCellVersion,
    *,
    baseline_cells: tuple[NotebookCellVersion, ...],
    candidate_cells: tuple[NotebookCellVersion, ...],
) -> float:
    source = SequenceMatcher(
        None,
        _normalized_source(baseline),
        _normalized_source(candidate),
        autojunk=False,
    ).ratio()
    neighbor = 0.0
    for delta in (-1, 1):
        left_index = baseline.display_order + delta
        right_index = candidate.display_order + delta
        if (
            0 <= left_index < len(baseline_cells)
            and 0 <= right_index < len(candidate_cells)
            and baseline_cells[left_index].source_sha256
            == candidate_cells[right_index].source_sha256
        ):
            neighbor += 0.05
    return min(1.0, source + neighbor)


def _execution_by_cell(
    publication: NotebookPublication,
) -> dict[str, NotebookCellExecution]:
    return {item.cell_version_id: item for item in publication.cell_executions}


def _outputs_by_cell(
    publication: NotebookPublication,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    execution_by_id = {
        item.cell_execution_id: item.cell_version_id for item in publication.cell_executions
    }
    grouped: dict[str, list[NotebookArtifact]] = defaultdict(list)
    for artifact in publication.artifacts:
        grouped[execution_by_id[artifact.cell_execution_id]].append(artifact)
    return {
        cell_id: tuple(
            (
                item.ordinal,
                item.artifact_type,
                item.mime_types,
                item.content_sha256,
                item.error_name,
                item.binary_omitted,
                item.metric_confirmed,
            )
            for item in sorted(items, key=lambda value: (value.ordinal, value.artifact_id))
        )
        for cell_id, items in grouped.items()
    }


def _classify(
    baseline: NotebookCellVersion,
    candidate: NotebookCellVersion,
    *,
    baseline_execution: NotebookCellExecution,
    candidate_execution: NotebookCellExecution,
    baseline_outputs: tuple[tuple[object, ...], ...],
    candidate_outputs: tuple[tuple[object, ...], ...],
    executions_differ: bool,
) -> tuple[NotebookCellChangeType, ...]:
    changes: list[NotebookCellChangeType] = []
    if baseline.display_order != candidate.display_order:
        changes.append(NotebookCellChangeType.MOVED)
    if baseline.source_sha256 != candidate.source_sha256:
        changes.append(NotebookCellChangeType.SOURCE_MODIFIED)
    if baseline.tags != candidate.tags or baseline.cell_type != candidate.cell_type:
        changes.append(NotebookCellChangeType.METADATA_MODIFIED)
    if baseline_outputs != candidate_outputs:
        changes.append(NotebookCellChangeType.OUTPUT_CHANGED)
    if baseline_execution.execution_order != candidate_execution.execution_order:
        changes.append(NotebookCellChangeType.EXECUTION_ORDER_CHANGED)
    if baseline_execution.stale != candidate_execution.stale:
        changes.append(NotebookCellChangeType.STALE_OUTPUT_CHANGED)
    if executions_differ and baseline.source_sha256 == candidate.source_sha256 and not changes:
        changes.append(NotebookCellChangeType.REEXECUTED_SAME_SOURCE)
    return tuple(changes or (NotebookCellChangeType.UNCHANGED,))


def _parameter_changes(
    baseline: tuple[NotebookParameter, ...],
    candidate: tuple[NotebookParameter, ...],
) -> tuple[NotebookParameterChange, ...]:
    left = {item.name: item for item in baseline}
    right = {item.name: item for item in candidate}
    changes: list[NotebookParameterChange] = []
    for name in sorted(set(left) | set(right)):
        before = left.get(name)
        after = right.get(name)
        if before is None:
            change = "added"
        elif after is None:
            change = "removed"
        elif before.value_type == after.value_type and before.value_sha256 == after.value_sha256:
            change = "unchanged"
        else:
            change = "modified"
        changes.append(
            NotebookParameterChange(
                name=name,
                baseline_type=str(before.value_type) if before else None,
                baseline_value_sha256=before.value_sha256 if before else None,
                candidate_type=str(after.value_type) if after else None,
                candidate_value_sha256=after.value_sha256 if after else None,
                change=change,
            )
        )
    return tuple(changes)


def compare_notebook_publications_v2(
    baseline: NotebookPublication,
    candidate: NotebookPublication,
) -> NotebookComparisonResult:
    if baseline.template.template_id != candidate.template.template_id:
        raise ValueError("Notebook comparison requires one logical template")
    if baseline.template.scope != candidate.template.scope:
        raise ValueError("Notebook comparison requires identical governed scope")
    if (
        baseline.revision.revision_id == candidate.revision.revision_id
        and baseline.execution.execution_id == candidate.execution.execution_id
    ):
        raise ValueError("Notebook comparison requires distinct evidence")

    left_cells = tuple(sorted(baseline.cell_versions, key=lambda item: item.display_order))
    right_cells = tuple(sorted(candidate.cell_versions, key=lambda item: item.display_order))
    unmatched_left = {item.cell_version_id: item for item in left_cells}
    unmatched_right = {item.cell_version_id: item for item in right_cells}
    matched: list[
        tuple[
            NotebookCellVersion,
            NotebookCellVersion,
            NotebookCellMatchMethod,
            float,
        ]
    ] = []

    def consume(
        left: NotebookCellVersion,
        right: NotebookCellVersion,
        method: NotebookCellMatchMethod,
        confidence: float,
    ) -> None:
        if left.cell_version_id in unmatched_left and right.cell_version_id in unmatched_right:
            unmatched_left.pop(left.cell_version_id)
            unmatched_right.pop(right.cell_version_id)
            matched.append((left, right, method, confidence))

    for key, method, confidence in (
        ("native_cell_id", NotebookCellMatchMethod.NATIVE_ID, 1.0),
        ("stable_cell_id", NotebookCellMatchMethod.STABLE_ID, 0.99),
        ("source_sha256", NotebookCellMatchMethod.SOURCE_HASH, 0.95),
    ):
        left_unique = _group_unique(tuple(unmatched_left.values()), key)
        right_unique = _group_unique(tuple(unmatched_right.values()), key)
        for value in sorted(set(left_unique) & set(right_unique)):
            consume(left_unique[value], right_unique[value], method, confidence)

    ambiguous_pair_count = 0
    ambiguous_left_ids: set[str] = set()
    for left in tuple(sorted(unmatched_left.values(), key=lambda item: item.display_order)):
        scores = sorted(
            (
                (
                    _similarity(
                        left,
                        right,
                        baseline_cells=left_cells,
                        candidate_cells=right_cells,
                    ),
                    right,
                )
                for right in unmatched_right.values()
                if right.cell_type == left.cell_type
            ),
            key=lambda item: (-item[0], item[1].display_order, item[1].cell_version_id),
        )
        if not scores:
            continue
        best_score, best = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else 0.0
        if best_score >= 0.85 and best_score - runner_up >= 0.10:
            consume(
                left,
                best,
                NotebookCellMatchMethod.SIMILARITY_NEIGHBOR,
                round(best_score, 6),
            )
        elif best_score >= 0.85:
            ambiguous_pair_count += 1
            ambiguous_left_ids.add(left.cell_version_id)

    for left in tuple(sorted(unmatched_left.values(), key=lambda item: item.display_order)):
        if left.cell_version_id in ambiguous_left_ids:
            continue
        possibilities = [
            right
            for right in unmatched_right.values()
            if right.display_order == left.display_order and right.cell_type == left.cell_type
        ]
        if len(possibilities) == 1:
            right = possibilities[0]
            score = _similarity(
                left,
                right,
                baseline_cells=left_cells,
                candidate_cells=right_cells,
            )
            if score >= 0.70:
                consume(
                    left,
                    right,
                    NotebookCellMatchMethod.ORDINAL_FALLBACK,
                    round(min(score, 0.75), 6),
                )

    left_execution = _execution_by_cell(baseline)
    right_execution = _execution_by_cell(candidate)
    left_outputs = _outputs_by_cell(baseline)
    right_outputs = _outputs_by_cell(candidate)
    matches: list[NotebookCellMatch] = []
    for left, right, method, confidence in matched:
        matches.append(
            NotebookCellMatch(
                baseline_cell_version_id=left.cell_version_id,
                candidate_cell_version_id=right.cell_version_id,
                baseline_locator=left.locator,
                candidate_locator=right.locator,
                method=method,
                confidence=confidence,
                change_types=_classify(
                    left,
                    right,
                    baseline_execution=left_execution[left.cell_version_id],
                    candidate_execution=right_execution[right.cell_version_id],
                    baseline_outputs=left_outputs.get(left.cell_version_id, ()),
                    candidate_outputs=right_outputs.get(right.cell_version_id, ()),
                    executions_differ=baseline.execution.execution_id
                    != candidate.execution.execution_id,
                ),
            )
        )
    for left in unmatched_left.values():
        matches.append(
            NotebookCellMatch(
                baseline_cell_version_id=left.cell_version_id,
                candidate_cell_version_id=None,
                baseline_locator=left.locator,
                candidate_locator=None,
                method=NotebookCellMatchMethod.UNMATCHED,
                confidence=1.0,
                change_types=(NotebookCellChangeType.REMOVED,),
            )
        )
    for right in unmatched_right.values():
        matches.append(
            NotebookCellMatch(
                baseline_cell_version_id=None,
                candidate_cell_version_id=right.cell_version_id,
                baseline_locator=None,
                candidate_locator=right.locator,
                method=NotebookCellMatchMethod.UNMATCHED,
                confidence=1.0,
                change_types=(NotebookCellChangeType.ADDED,),
            )
        )
    matches.sort(
        key=lambda item: (
            item.baseline_locator or item.candidate_locator or "",
            item.candidate_locator or "",
        )
    )
    parameters = _parameter_changes(baseline.parameters, candidate.parameters)
    matches_sha256 = canonical_sha256(
        {
            "matcher_version": NOTEBOOK_CELL_MATCHER_VERSION,
            "matches": [item.model_dump(mode="json") for item in matches],
            "parameter_changes": [item.model_dump(mode="json") for item in parameters],
        }
    )
    scope = baseline.template.scope
    locator = (
        f"notebook://{scope.project_id}/template/{baseline.template.template_id}"
        f"/compare/{baseline.revision.revision_id}/{candidate.revision.revision_id}"
    )
    values = {
        "matcher_version": NOTEBOOK_CELL_MATCHER_VERSION,
        "project_id": scope.project_id,
        "acl_ref": scope.acl_ref,
        "generation_id": scope.generation_id,
        "template_id": baseline.template.template_id,
        "baseline_revision_id": baseline.revision.revision_id,
        "candidate_revision_id": candidate.revision.revision_id,
        "baseline_execution_id": baseline.execution.execution_id,
        "candidate_execution_id": candidate.execution.execution_id,
        "matches": tuple(matches),
        "parameter_changes": parameters,
        "baseline_status": str(baseline.execution.status),
        "candidate_status": str(candidate.execution.status),
        "matches_sha256": matches_sha256,
        "locator": locator,
        "ambiguous_pair_count": ambiguous_pair_count,
    }
    draft = NotebookComparisonResult.model_construct(
        comparison_id="nbcompare-" + ("0" * 64),
        **values,
    )
    comparison_id = "nbcompare-" + canonical_sha256(
        draft.model_dump(
            mode="json",
            exclude={"comparison_id", "matches_sha256"},
        )
    ).removeprefix("sha256:")
    return NotebookComparisonResult(comparison_id=comparison_id, **values)


def select_notebook_comparison_v2(
    publications: tuple[NotebookPublication, ...],
    spec: NotebookComparisonSpecV2,
) -> tuple[
    NotebookPublication,
    NotebookPublication,
    NotebookComparisonSelectionTraceV2,
]:
    """Resolve both sides by full identity without a latest-version fallback."""

    def resolve(selector: NotebookPublicationSelectorV2) -> NotebookPublication:
        exact = [
            publication
            for publication in publications
            if publication.template.template_id == selector.template_id
            and publication.revision.revision_id == selector.revision_id
            and publication.execution.execution_id == selector.execution_id
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise NotebookComparisonSelectionError("comparison_selector_ambiguous")
        component_match = any(
            publication.template.template_id == selector.template_id
            or publication.revision.revision_id == selector.revision_id
            or publication.execution.execution_id == selector.execution_id
            for publication in publications
        )
        raise NotebookComparisonSelectionError(
            "comparison_version_mismatch" if component_match else "comparison_identity_not_found"
        )

    baseline = resolve(spec.baseline)
    candidate = resolve(spec.candidate)
    if baseline.publication_id == candidate.publication_id:
        raise NotebookComparisonSelectionError("comparison_sides_identical")
    if baseline.template.template_id != candidate.template.template_id:
        raise NotebookComparisonSelectionError("comparison_template_mismatch")
    if baseline.template.scope != candidate.template.scope:
        raise NotebookComparisonSelectionError("comparison_scope_mismatch")
    trace = NotebookComparisonSelectionTraceV2(
        baseline=spec.baseline,
        candidate=spec.candidate,
        baseline_publication_id=baseline.publication_id,
        candidate_publication_id=candidate.publication_id,
    )
    return baseline, candidate, trace


__all__ = [
    "NOTEBOOK_CELL_MATCHER_VERSION",
    "NOTEBOOK_COMPARISON_SELECTION_VERSION",
    "NotebookCellChangeType",
    "NotebookCellMatch",
    "NotebookCellMatchMethod",
    "NotebookComparisonSelectionError",
    "NotebookComparisonSelectionTraceV2",
    "NotebookComparisonSpecV2",
    "NotebookComparisonResult",
    "NotebookParameterChange",
    "NotebookPublicationSelectorV2",
    "compare_notebook_publications_v2",
    "select_notebook_comparison_v2",
]
