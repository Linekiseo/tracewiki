from __future__ import annotations

from enum import StrEnum, unique
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ExperimentAggregationNameV2 = Literal["mean", "median", "std", "min", "max", "count"]
ExperimentReproductionRoleNameV2 = Literal[
    "command",
    "code_commit",
    "dataset_version",
    "config_snapshot",
    "environment_snapshot",
    "seed",
    "required_artifacts",
]


class _TypedSourceModelV2(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _canonical_values(values: list[str], label: str, maximum: int = 50) -> list[str]:
    if len(values) > maximum:
        raise ValueError(f"too many {label}")
    if any(not value or len(value) > 512 or "\x00" in value for value in values):
        raise ValueError(f"{label} contain an invalid identity")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    return sorted(values)


class ExperimentAnalysisRequestV2(_TypedSourceModelV2):
    """Explicit E3 operation; no metric, split, group, or baseline is inferred."""

    task: Literal["compare", "aggregate", "reproduce"]
    metric: str | None = Field(default=None, min_length=1, max_length=256)
    baseline_run_id: str | None = Field(default=None, min_length=1, max_length=512)
    candidate_run_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    split: str | None = Field(default=None, min_length=1, max_length=256)
    aggregation: ExperimentAggregationNameV2 | None = None
    roles: list[ExperimentReproductionRoleNameV2] = Field(default_factory=list)
    treatment_config_keys: list[str] = Field(default_factory=list)
    controlled_config_keys: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _explicit_operation(self) -> ExperimentAnalysisRequestV2:
        self.candidate_run_ids = _canonical_values(
            self.candidate_run_ids,
            "candidate run IDs",
        )
        self.run_ids = _canonical_values(self.run_ids, "run IDs")
        self.roles = list(dict.fromkeys(self.roles))
        self.treatment_config_keys = _canonical_values(
            self.treatment_config_keys,
            "treatment config keys",
            64,
        )
        self.controlled_config_keys = _canonical_values(
            self.controlled_config_keys,
            "controlled config keys",
            64,
        )
        overlap = set(self.treatment_config_keys) & set(self.controlled_config_keys)
        if overlap:
            raise ValueError("config keys cannot be both treatment and controlled")
        if self.task == "compare":
            if self.metric is None or self.baseline_run_id is None or self.split is None:
                raise ValueError("compare requires explicit metric, baseline_run_id, and split")
            if not self.candidate_run_ids:
                raise ValueError("compare requires explicit candidate_run_ids")
            if self.baseline_run_id in self.candidate_run_ids:
                raise ValueError("baseline cannot also be a candidate")
            if self.run_ids or self.aggregation is not None or self.roles:
                raise ValueError("compare contains fields for a different E3 operation")
        elif self.task == "aggregate":
            if self.metric is None or self.split is None or self.aggregation is None:
                raise ValueError("aggregate requires explicit metric, split, and aggregation")
            if not self.run_ids:
                raise ValueError("aggregate requires explicit run_ids")
            if self.baseline_run_id is not None or self.candidate_run_ids or self.roles:
                raise ValueError("aggregate contains fields for a different E3 operation")
        else:
            if not self.run_ids or not self.roles:
                raise ValueError("reproduce requires explicit run_ids and roles")
            if (
                self.metric is not None
                or self.split is not None
                or self.aggregation is not None
                or self.baseline_run_id is not None
                or self.candidate_run_ids
                or self.treatment_config_keys
                or self.controlled_config_keys
            ):
                raise ValueError("reproduce contains fields for a different E3 operation")
        return self

    def selected_run_ids(self) -> list[str]:
        if self.task == "compare":
            assert self.baseline_run_id is not None
            return sorted({self.baseline_run_id, *self.candidate_run_ids})
        return list(self.run_ids)


class ExperimentSourceQueryRequestV2(_TypedSourceModelV2):
    schema_version: Literal["experiment-source-query-request-v2"] = (
        "experiment-source-query-request-v2"
    )
    project_id: str = Field(default="project-rag", min_length=1, max_length=512)
    query: str = Field(min_length=1, max_length=2_000)
    experiment_ids: list[str] = Field(default_factory=list)
    analysis: ExperimentAnalysisRequestV2
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def _canonical_scope(self) -> ExperimentSourceQueryRequestV2:
        self.experiment_ids = _canonical_values(self.experiment_ids, "experiment IDs")
        return self


class NotebookSearchFiltersV2(_TypedSourceModelV2):
    template_ids: list[str] = Field(default_factory=list)
    revision_ids: list[str] = Field(default_factory=list)
    execution_ids: list[str] = Field(default_factory=list)
    cell_ids: list[str] = Field(default_factory=list)
    statuses: list[Literal["completed", "failed", "cancelled", "unknown"]] = Field(
        default_factory=list
    )
    parameter_names: list[str] = Field(default_factory=list)
    parameter_ids: list[str] = Field(default_factory=list)
    output_ids: list[str] = Field(default_factory=list)
    output_types: list[
        Literal["stream", "display", "execute_result", "error", "binary_omitted"]
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def _canonical(self) -> NotebookSearchFiltersV2:
        for field_name, label in (
            ("template_ids", "template IDs"),
            ("revision_ids", "revision IDs"),
            ("execution_ids", "execution IDs"),
            ("cell_ids", "cell IDs"),
            ("parameter_names", "parameter names"),
            ("parameter_ids", "parameter IDs"),
            ("output_ids", "output IDs"),
        ):
            setattr(self, field_name, _canonical_values(getattr(self, field_name), label))
        if len(self.statuses) != len(set(self.statuses)):
            raise ValueError("statuses must be unique")
        if len(self.output_types) != len(set(self.output_types)):
            raise ValueError("output types must be unique")
        self.statuses = sorted(self.statuses)
        self.output_types = sorted(self.output_types)
        return self


class NotebookComparisonSelectorV2(_TypedSourceModelV2):
    template_id: str = Field(min_length=1, max_length=512)
    revision_id: str = Field(min_length=1, max_length=512)
    execution_id: str = Field(min_length=1, max_length=512)


class NotebookComparisonRequestV2(_TypedSourceModelV2):
    baseline: NotebookComparisonSelectorV2
    candidate: NotebookComparisonSelectorV2

    @model_validator(mode="after")
    def _different(self) -> NotebookComparisonRequestV2:
        if self.baseline == self.candidate:
            raise ValueError("baseline and candidate must be different")
        return self


@unique
class NotebookQueryTaskV2(StrEnum):
    """Closed Notebook task contract shared by Platform and the source runtime."""

    SEARCH = "search"
    COMPARE = "compare"
    OUTPUT = "output"
    ERROR = "error"
    REPRODUCTION = "reproduction"
    CODE = "code"
    LINEAGE = "lineage"
    PARAMETER = "parameter"


class NotebookQuerySpecV2(_TypedSourceModelV2):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: NotebookQueryTaskV2 = NotebookQueryTaskV2.SEARCH
    filters: NotebookSearchFiltersV2 = Field(default_factory=NotebookSearchFiltersV2)
    comparison: NotebookComparisonRequestV2 | None = None

    @model_validator(mode="after")
    def _comparison_shape(self) -> NotebookQuerySpecV2:
        if self.task is NotebookQueryTaskV2.COMPARE and self.comparison is None:
            raise ValueError("Notebook compare requires explicit baseline and candidate")
        if self.task is not NotebookQueryTaskV2.COMPARE and self.comparison is not None:
            raise ValueError("Notebook comparison is only valid for compare task")
        return self


class NotebookSourceQueryRequestV2(_TypedSourceModelV2):
    schema_version: Literal["notebook-source-query-request-v2"] = "notebook-source-query-request-v2"
    project_id: str = Field(default="project-rag", min_length=1, max_length=512)
    query: str = Field(min_length=1, max_length=2_000)
    spec: NotebookQuerySpecV2 = Field(default_factory=NotebookQuerySpecV2)
    limit: int = Field(default=20, ge=1, le=100)


class ExperimentAnalysisResultEnvelopeV2(_TypedSourceModelV2):
    source_id: str
    numeric: list[dict[str, Any]]
    comparability: list[dict[str, Any]]
    comparisons: list[dict[str, Any]]
    aggregations: list[dict[str, Any]]
    reproduction: list[dict[str, Any]]
    requested_reproduction_roles: list[ExperimentReproductionRoleNameV2]
    warnings: list[str]


class ExperimentAnalysisSelectionTraceV2(_TypedSourceModelV2):
    schema_version: Literal["experiment-analysis-selection-trace-v2"] = (
        "experiment-analysis-selection-trace-v2"
    )
    explicit: bool
    task: Literal["search", "compare", "aggregate", "reproduce"]
    metric: str | None
    baseline_run_id: str | None
    candidate_run_ids: list[str]
    run_ids: list[str]
    split: str | None
    aggregation: ExperimentAggregationNameV2 | None
    roles: list[ExperimentReproductionRoleNameV2]
    selected_run_ids: list[str]
    source_count: int = Field(ge=0)


class NotebookSelectionTraceResponseV2(_TypedSourceModelV2):
    schema_version: Literal["notebook-filter-selection-trace-v2"] = (
        "notebook-filter-selection-trace-v2"
    )
    filter_before_rank: Literal[True] = True
    requested_filters: dict[str, list[str] | tuple[str, ...]]
    publication_count_before_filter: int = Field(ge=0)
    publication_count_after_filter: int = Field(ge=0)
    unit_count_before_filter: int = Field(ge=0)
    unit_count_after_filter: int = Field(ge=0)
    selected_template_ids: list[str] | tuple[str, ...]
    selected_revision_ids: list[str] | tuple[str, ...]
    selected_execution_ids: list[str] | tuple[str, ...]
    comparison: dict[str, Any] | None


class ExperimentSourceQueryResponseV2(_TypedSourceModelV2):
    schema_version: Literal["experiment-source-query-response-v2"] = (
        "experiment-source-query-response-v2"
    )
    source: Literal["experiment"] = "experiment"
    task: Literal["compare", "aggregate", "reproduce"]
    results: list[dict[str, Any]]
    context: dict[str, Any]
    analysis: list[ExperimentAnalysisResultEnvelopeV2]
    selection_trace: ExperimentAnalysisSelectionTraceV2
    trace: dict[str, Any]


class NotebookSourceQueryResponseV2(_TypedSourceModelV2):
    schema_version: Literal["notebook-source-query-response-v2"] = (
        "notebook-source-query-response-v2"
    )
    source: Literal["notebook"] = "notebook"
    task: NotebookQueryTaskV2
    results: list[dict[str, Any]]
    context: dict[str, Any]
    comparisons: list[dict[str, Any]]
    selection_trace: NotebookSelectionTraceResponseV2
    trace: dict[str, Any]


class GlobalSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    project_id: str = "project-rag"
    sources: list[Literal["code", "codex", "workspace", "experiment", "notebook", "document"]] = (
        Field(default_factory=list)
    )
    limit: int = Field(default=20, ge=1, le=100)
    include_lineage: bool = True
    max_hops: int = Field(default=1, ge=0, le=4)
    repository_ids: list[str] = Field(default_factory=list)
    commit: str | None = None
    thread_ids: list[str] = Field(default_factory=list)
    experiment_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    experiment_analysis: ExperimentAnalysisRequestV2 | None = None
    notebook_query: NotebookQuerySpecV2 | None = None
    date_from: str | None = None
    date_to: str | None = None
    as_of: str | None = None
    intent: str | None = None
    # Execution budget, not evidence.  Excluding it keeps content identities stable
    # while allowing interactive callers to reserve a realistic source window.
    deadline_ms: int | None = Field(default=None, ge=100, le=30_000, exclude=True)
    allowed_acl_refs: list[str] = Field(default_factory=list, exclude=True)
    enforce_acl: bool = Field(default=False, exclude=True)


class DriftScanRequest(BaseModel):
    project_id: str = "project-rag"
    update_claim_status: bool = True
