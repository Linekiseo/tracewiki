"""M5 capability registry, 60-case Golden, planner, calibration, and fusion."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MULTISOURCE_CAPABILITY_VERSION = "multisource-capability-registry-v2"
MULTISOURCE_GOLDEN_VERSION = "multisource-golden-v2"
MULTISOURCE_PLANNER_VERSION = "multisource-planner-v2"
MULTISOURCE_CALIBRATION_VERSION = "multisource-calibration-v2"
MULTISOURCE_FUSION_VERSION = "multisource-role-fusion-v2"
MULTISOURCE_RERANK_VERSION = "source-aware-cross-source-rerank-v2"
MULTISOURCE_CALIBRATION_DASHBOARD_VERSION = "multisource-calibration-dashboard-v2"


def canonical_sha256_v2(value: object) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


MULTISOURCE_DOMAINS = (
    "code",
    "codex",
    "experiment",
    "notebook",
    "document",
    "workspace",
)

_SOURCE_TASKS: dict[str, tuple[str, ...]] = {
    "code": (
        "bug_localization",
        "call_path",
        "change_context",
        "exact_location",
        "historical",
        "impact_analysis",
        "implementation",
        "test_validation",
    ),
    "codex": ("failure_retry", "process", "rationale", "validation"),
    "experiment": ("aggregate", "best", "compare", "reproduce", "search", "trend"),
    "notebook": (
        "code",
        "compare",
        "error",
        "lineage",
        "locate",
        "output",
        "parameter",
        "reproduction",
    ),
    "document": (
        "citation",
        "claim",
        "figure_formula",
        "local_fact",
        "locate",
        "summary",
        "table",
        "version",
    ),
    "workspace": (
        "blocker",
        "coverage",
        "current",
        "decision",
        "intelligence",
        "locate",
        "temporal",
        "work",
    ),
}

_SOURCE_ENTITY_TYPES: dict[str, tuple[str, ...]] = {
    "code": (
        "ChangeSet",
        "CodeRetrievalUnit",
        "CodeSymbol",
        "Commit",
        "ConfigKey",
        "Coverage",
        "Dependency",
        "DiffHunk",
        "FileVersion",
        "GitCommit",
        "PackageModule",
        "Repository",
        "TestCase",
        "TestResult",
        "TypeEntity",
        "ValidationTarget",
        "Worktree",
    ),
    "codex": (
        "action",
        "change",
        "decision",
        "episode",
        "failure",
        "goal",
        "outcome",
        "plan",
        "thread",
        "validation",
    ),
    "experiment": (
        "ExperimentArtifactVersionV2",
        "ExperimentConfigSnapshotV2",
        "ExperimentDatasetVersionV2",
        "ExperimentEnvironmentSnapshotV2",
        "ExperimentMetricDefinitionV2",
        "ExperimentMetricObservationV2",
        "ExperimentRun",
        "ExperimentRunSnapshotV2",
        "ExperimentRunV2",
        "MetricObservationV2",
        "artifact.surface",
        "comparison.surface",
        "config.surface",
        "dataset.surface",
        "experiment.surface",
        "metric.definition",
        "run.surface",
    ),
    "notebook": (
        "NotebookCell",
        "NotebookCellExecution",
        "NotebookCellVersion",
        "NotebookError",
        "NotebookExecution",
        "NotebookOutput",
        "NotebookParameter",
        "NotebookRevision",
        "NotebookRun",
        "cell",
        "cell_execution",
        "error",
        "execution",
        "output",
        "parameter",
        "revision",
    ),
    "document": (
        "Claim",
        "DocumentCitation",
        "DocumentFigure",
        "DocumentPage",
        "DocumentSection",
        "DocumentTable",
        "DocumentTableCell",
        "ScientificDocument",
        "citation_mention",
        "claim",
        "claim_candidate",
        "figure",
        "formula",
        "layout_block",
        "page",
        "paragraph",
        "reference_work",
        "section",
        "section_summary",
        "table",
        "table_cell_fact",
        "table_row",
        "version_diff",
    ),
    "workspace": (
        "Project",
        "ResearchIteration",
        "ResearchTopic",
        "ResearchWorkItem",
        "acceptance_check",
        "acceptance_criterion",
        "blocker",
        "decision",
        "dependency",
        "evidence_link",
        "evidence_requirement",
        "intelligence_run",
        "iteration",
        "outcome",
        "policy_version",
        "project",
        "risk",
        "snapshot",
        "topic",
        "transition",
        "work_item",
    ),
}


def source_entity_types_v2(source: str) -> tuple[str, ...]:
    """Return the reviewed entity vocabulary for one canonical source."""

    try:
        return tuple(sorted(_SOURCE_ENTITY_TYPES[source]))
    except KeyError as error:
        raise ValueError("source is unknown") from error


_GOLDEN_SLICES = (
    ("code_codex", 8, ("code", "codex")),
    ("experiment_document", 8, ("experiment", "document")),
    ("experiment_notebook", 7, ("experiment", "notebook")),
    ("code_experiment", 6, ("code", "experiment")),
    ("notebook_code_codex", 5, ("notebook", "code", "codex")),
    ("workspace_evidence", 6, ("workspace", "code")),
    ("three_source_chain", 6, ("document", "experiment", "code")),
    ("four_to_six_source_chain", 4, MULTISOURCE_DOMAINS),
    ("version_conflict_stale", 4, ("document", "experiment", "code")),
    ("unanswerable_source_failure", 3, ("code", "document")),
    ("acl_sensitive", 3, ("workspace", "document")),
)


class MultiSourceComplexityV2(StrEnum):
    SIMPLE = "simple"
    SINGLE_SOURCE = "single_source"
    MULTI_SOURCE = "multi_source"
    MULTI_HOP = "multi_hop"


class SourceExecutionStatusV2(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    NOT_INDEXED = "not_indexed"
    NO_MATCHING_EVIDENCE = "no_matching_evidence"


class _Frozen(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )


class SourceCapabilityV2(_Frozen):
    domain: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    retriever_version: str
    supported_tasks: tuple[str, ...]
    channels: tuple[str, ...]
    entity_types: tuple[str, ...]
    filters: tuple[str, ...]
    time_semantics: str
    version_semantics: str
    supports_as_of: bool
    supports_numeric: bool
    supports_relation_expansion: bool
    calibration_version: str
    index_generation: str
    watermark: str
    acl_model: Literal["inherited", "explicit"]
    health: Literal["ready", "partial", "unavailable"]
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> SourceCapabilityV2:
        for value in (
            self.supported_tasks,
            self.channels,
            self.entity_types,
            self.filters,
        ):
            if value != tuple(sorted(set(value))):
                raise ValueError("capability lists must be sorted and unique")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("capability digest mismatch")
        return self


class CapabilityRegistryV2(_Frozen):
    capabilities: tuple[SourceCapabilityV2, ...] = Field(min_length=6, max_length=6)
    registry_version: str = MULTISOURCE_CAPABILITY_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> CapabilityRegistryV2:
        if tuple(item.domain for item in self.capabilities) != MULTISOURCE_DOMAINS:
            raise ValueError("capability registry must contain six canonical domains")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("capability registry digest mismatch")
        return self


class MultiSourceScopeV2(_Frozen):
    project_id: str
    acl_refs: tuple[str, ...] = Field(min_length=1)
    as_of: str | None = None
    target_version: str | None = None
    source_task_parameters: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = ()

    @model_validator(mode="after")
    def _canonical(self) -> MultiSourceScopeV2:
        if self.acl_refs != tuple(sorted(set(self.acl_refs))):
            raise ValueError("ACL references must be sorted and unique")
        sources = tuple(source for source, _ in self.source_task_parameters)
        if sources != tuple(sorted(set(sources))):
            raise ValueError("source task parameter sources must be sorted and unique")
        if any(source not in MULTISOURCE_DOMAINS for source in sources):
            raise ValueError("source task parameters contain an unknown source")
        for _, parameters in self.source_task_parameters:
            _validate_source_task_parameters_v2(parameters)
        return self

    def task_parameters_for(self, source: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return next(
            (
                parameters
                for parameter_source, parameters in self.source_task_parameters
                if parameter_source == source
            ),
            (),
        )


def _validate_source_task_parameters_v2(
    parameters: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    keys = tuple(key for key, _ in parameters)
    if keys != tuple(sorted(set(keys))):
        raise ValueError("source task parameter keys must be sorted and unique")
    if len(parameters) > 32:
        raise ValueError("too many source task parameters")
    for key, values in parameters:
        if not key or len(key) > 64 or not key.replace("_", "").isalnum():
            raise ValueError("source task parameter key is invalid")
        if values != tuple(sorted(set(values))):
            raise ValueError("source task parameter values must be sorted and unique")
        if len(values) > 100 or any(
            not value or len(value) > 512 or "\x00" in value for value in values
        ):
            raise ValueError("source task parameter values are invalid")


class MultiSourceRouteContractV2(_Frozen):
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    task: str
    fact_type: str
    fact_types: tuple[str, ...] = ()
    entity_types: tuple[str, ...] = Field(min_length=1)
    calibration_version: str
    task_parameters: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @model_validator(mode="after")
    def _typed_contract(self) -> MultiSourceRouteContractV2:
        if self.fact_type != f"{self.source}.fact":
            raise ValueError("route fact type does not match its source")
        if self.fact_types:
            if self.fact_types != tuple(sorted(set(self.fact_types))):
                raise ValueError("route fact types must be sorted and unique")
            if any(not item.startswith(f"{self.source}.") for item in self.fact_types):
                raise ValueError("route fact type vocabulary does not match its source")
        if self.entity_types != tuple(sorted(set(self.entity_types))):
            raise ValueError("route entity types must be sorted and unique")
        _validate_source_task_parameters_v2(self.task_parameters)
        return self

    def task_parameter_map(self) -> dict[str, tuple[str, ...]]:
        return dict(self.task_parameters)

    def accepts_fact_type(self, fact_type: str) -> bool:
        return fact_type == self.fact_type or fact_type in self.fact_types

    def accepts_entity_type(self, entity_type: str) -> bool:
        return entity_type == self.fact_type or entity_type in self.entity_types

    def accepts_task(self, task: str) -> bool:
        return task == self.task or task == "search"


class MultiSourceSubQuestionV2(_Frozen):
    subquestion_id: str
    parent_question_sha256: str
    question: str
    sources: tuple[str, ...] = Field(min_length=1, max_length=1)
    task: str = Field(min_length=1)
    required_roles: tuple[str, ...]
    rewrite_provenance: Literal["deterministic_template"]


class MultiSourcePlanV2(_Frozen):
    question_sha256: str
    intent: str
    complexity: MultiSourceComplexityV2
    scope: MultiSourceScopeV2
    registry_sha256: str
    subquestions: tuple[MultiSourceSubQuestionV2, ...]
    required_roles: tuple[str, ...]
    optional_roles: tuple[str, ...]
    source_routes: tuple[str, ...]
    source_contracts: tuple[MultiSourceRouteContractV2, ...]
    source_budgets: tuple[tuple[str, int], ...]
    graph_templates: tuple[str, ...]
    context_budget_tokens: int = Field(ge=256, le=32_000)
    stop_conditions: tuple[str, ...]
    planning_gaps: tuple[str, ...]
    planner_version: str = MULTISOURCE_PLANNER_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> MultiSourcePlanV2:
        if self.source_routes != tuple(dict.fromkeys(self.source_routes)):
            raise ValueError("source routes must be ordered and unique")
        if tuple(source for source, _ in self.source_budgets) != self.source_routes:
            raise ValueError("source budgets do not match routes")
        if tuple(item.source for item in self.source_contracts) != self.source_routes:
            raise ValueError("source contracts do not match routes")
        if any(
            subquestion.task
            != next(
                item.task for item in self.source_contracts if item.source == subquestion.sources[0]
            )
            for subquestion in self.subquestions
        ):
            raise ValueError("subquestion task does not match its source contract")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("plan digest mismatch")
        return self


class MultiSourceGoldenCaseV2(_Frozen):
    case_id: str
    slice_name: str
    question: str
    intent: str
    complexity: MultiSourceComplexityV2
    required_sources: tuple[str, ...]
    required_roles: tuple[str, ...]
    required_entity_ids: tuple[str, ...]
    acceptable_alternatives: tuple[str, ...]
    required_paths: tuple[str, ...]
    target_version: str | None
    forbidden_entities: tuple[str, ...]
    expected_decision: str
    expected_claim_citations: tuple[tuple[str, tuple[str, ...]], ...]
    fault_injection: str | None
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> MultiSourceGoldenCaseV2:
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("Golden case digest mismatch")
        return self


class MultiSourceGoldenReleaseV2(_Frozen):
    dataset_id: Literal["multisource-golden-v2"] = MULTISOURCE_GOLDEN_VERSION
    cases: tuple[MultiSourceGoldenCaseV2, ...] = Field(min_length=60, max_length=60)
    slice_counts: tuple[tuple[str, int], ...]
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> MultiSourceGoldenReleaseV2:
        if tuple(item.case_id for item in self.cases) != tuple(
            f"multisource-v2-{index:03d}" for index in range(1, 61)
        ):
            raise ValueError("Golden membership is not canonical")
        actual: dict[str, int] = defaultdict(int)
        for case in self.cases:
            actual[case.slice_name] += 1
        if self.slice_counts != tuple((name, count) for name, count, _ in _GOLDEN_SLICES) or dict(
            self.slice_counts
        ) != dict(actual):
            raise ValueError("Golden slice counts are not frozen")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("Golden release digest mismatch")
        return self


class CalibrationObservationV2(_Frozen):
    source: str
    raw_score: float = Field(ge=0, le=1)
    relevant: bool
    task: str = "unspecified"
    entity_type: str = "unspecified"
    review_status: Literal["reviewed", "unreviewed", "rejected"] = "reviewed"


class CalibrationBinV2(_Frozen):
    lower: float
    upper: float
    probability: float = Field(ge=0, le=1)
    count: int = Field(ge=1)
    positives: int = Field(ge=0)


class SourceCalibrationProfileV2(_Frozen):
    source: str
    task: str = "unspecified"
    entity_type: str = "unspecified"
    bins: tuple[CalibrationBinV2, ...] = Field(min_length=1)
    sample_count: int = Field(ge=1)
    reviewed_sample_count: int = Field(ge=1)
    ece: float = Field(ge=0, le=1)
    brier: float = Field(ge=0, le=1)
    provenance_sha256: str
    calibration_version: str = MULTISOURCE_CALIBRATION_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> SourceCalibrationProfileV2:
        if (
            sum(item.count for item in self.bins) != self.sample_count
            or self.reviewed_sample_count != self.sample_count
        ):
            raise ValueError("calibration sample denominator mismatch")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("calibration profile digest mismatch")
        return self

    def probability(self, raw_score: float) -> float:
        for item in self.bins:
            if item.lower <= raw_score <= item.upper:
                return item.probability
        return (
            self.bins[0].probability
            if raw_score < self.bins[0].lower
            else self.bins[-1].probability
        )


class MultiSourceCandidateV2(_Frozen):
    candidate_id: str
    entity_id: str
    retrieval_unit_id: str
    parent_entity_id: str | None
    source_instance: str
    retrieval_domain: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    fact_type: str
    entity_type: str
    task: str
    title: str
    snippet: str
    locator: str
    stable_version: str
    source_generation: str
    raw_or_derived: Literal["raw_fact", "derived_fact"]
    derivation: str
    review_status: str
    fact_status: str
    channel_scores: tuple[tuple[str, float], ...]
    calibrated_relevance: float = Field(ge=0, le=1)
    calibration_version: str
    matched_roles: tuple[str, ...]
    authority: float = Field(ge=0, le=1)
    version_alignment: Literal[
        "exact", "compatible", "historical_target", "mismatch", "unknown", "not_applicable"
    ]
    acl_ref: str
    token_estimate: int = Field(ge=1)
    root_provenance: str
    counter_evidence: bool = False

    @model_validator(mode="before")
    @classmethod
    def _default_fact_envelope(cls, value: object) -> object:
        if isinstance(value, dict) and "fact_type" not in value and value.get("retrieval_domain"):
            return {
                **value,
                "fact_type": f"{value['retrieval_domain']}.fact",
            }
        return value

    @model_validator(mode="after")
    def _typed_candidate(self) -> MultiSourceCandidateV2:
        if not self.fact_type.startswith(f"{self.retrieval_domain}."):
            raise ValueError("candidate fact type does not match retrieval domain")
        if not self.entity_type.strip():
            raise ValueError("candidate entity type is required")
        if len({name for name, _ in self.channel_scores}) != len(self.channel_scores):
            raise ValueError("candidate channel scores must be unique")
        return self


class SourceExecutionV2(_Frozen):
    source: str
    status: SourceExecutionStatusV2
    candidate_count: int = Field(ge=0)
    coverage: float | None = Field(default=None, ge=0, le=1)
    index_generation: str | None
    watermark: str | None
    latency_ms: float = Field(ge=0)
    error_code: str | None = None


class RoleFusionResultV2(_Frozen):
    selected: tuple[MultiSourceCandidateV2, ...]
    satisfied_roles: tuple[str, ...]
    missing_roles: tuple[str, ...]
    source_status: tuple[SourceExecutionV2, ...]
    rejected: tuple[tuple[str, str], ...]
    total_tokens: int
    budget_tokens: int
    fusion_version: str = MULTISOURCE_FUSION_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> RoleFusionResultV2:
        if self.total_tokens != sum(item.token_estimate for item in self.selected):
            raise ValueError("fusion token accounting mismatch")
        if self.total_tokens > self.budget_tokens:
            raise ValueError("fusion exceeds token budget")
        if set(self.satisfied_roles) & set(self.missing_roles):
            raise ValueError("role cannot be both satisfied and missing")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("fusion result digest mismatch")
        return self


class CrossSourceRerankDecisionV2(_Frozen):
    candidate_id: str
    source: str
    task: str
    entity_type: str
    calibrated_relevance: float = Field(ge=0, le=1)
    authority_factor: float = Field(ge=0, le=1)
    role_gain: float = Field(ge=0)
    version_factor: float = Field(ge=0, le=1)
    fact_factor: float = Field(ge=0, le=1)
    score: float
    rank: int = Field(ge=1)


class CrossSourceRerankResultV2(_Frozen):
    ranked: tuple[MultiSourceCandidateV2, ...]
    decisions: tuple[CrossSourceRerankDecisionV2, ...]
    rejected: tuple[tuple[str, str], ...]
    calibration_profile_sha256s: tuple[str, ...]
    rerank_version: str = MULTISOURCE_RERANK_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> CrossSourceRerankResultV2:
        if tuple(item.candidate_id for item in self.ranked) != tuple(
            item.candidate_id for item in self.decisions
        ):
            raise ValueError("rerank decisions do not match ranked candidates")
        if tuple(item.rank for item in self.decisions) != tuple(range(1, len(self.decisions) + 1)):
            raise ValueError("rerank ranks are not contiguous")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("rerank result digest mismatch")
        return self


class CalibrationDashboardRowV2(_Frozen):
    source: str
    task: str
    entity_type: str
    status: Literal["reviewed", "unavailable"]
    sample_count: int = Field(ge=0)
    positives: int = Field(ge=0)
    negatives: int = Field(ge=0)
    ece: float | None = Field(default=None, ge=0, le=1)
    brier: float | None = Field(default=None, ge=0, le=1)
    calibration_version: str | None
    profile_sha256: str | None

    @model_validator(mode="after")
    def _honest_status(self) -> CalibrationDashboardRowV2:
        if self.positives + self.negatives != self.sample_count:
            raise ValueError("calibration dashboard denominator mismatch")
        if self.status == "unavailable" and any(
            value is not None
            for value in (self.ece, self.brier, self.calibration_version, self.profile_sha256)
        ):
            raise ValueError("unavailable calibration cannot publish reviewed metrics")
        if self.status == "reviewed" and (
            self.sample_count < 2
            or self.ece is None
            or self.brier is None
            or self.calibration_version is None
            or self.profile_sha256 is None
        ):
            raise ValueError("reviewed calibration dashboard row is incomplete")
        return self


class CalibrationDashboardV2(_Frozen):
    rows: tuple[CalibrationDashboardRowV2, ...]
    reviewed_profile_count: int = Field(ge=0)
    unavailable_profile_count: int = Field(ge=0)
    dashboard_version: str = MULTISOURCE_CALIBRATION_DASHBOARD_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> CalibrationDashboardV2:
        keys = tuple((item.source, item.task, item.entity_type) for item in self.rows)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("calibration dashboard rows must be sorted and unique")
        if self.reviewed_profile_count != sum(item.status == "reviewed" for item in self.rows):
            raise ValueError("reviewed calibration profile count mismatch")
        if self.unavailable_profile_count != sum(
            item.status == "unavailable" for item in self.rows
        ):
            raise ValueError("unavailable calibration profile count mismatch")
        if self.content_sha256 != canonical_sha256_v2(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise ValueError("calibration dashboard digest mismatch")
        return self


_INTENT = {
    "current_implementation": (
        ("code", "codex"),
        ("current_symbol", "current_version", "validation"),
        ("CURRENT_IMPLEMENTATION_PATH",),
    ),
    "historical_change": (
        ("code", "codex", "workspace"),
        ("goal", "patch_or_diff", "target_symbol", "commit"),
        ("CHANGE_VALIDATION_PATH",),
    ),
    "rationale": (
        ("codex", "workspace", "code", "document"),
        ("decision", "goal", "alternatives", "implementation"),
        ("DECISION_IMPLEMENTATION_PATH",),
    ),
    "experiment_validation": (
        ("experiment", "notebook", "code", "document"),
        ("experiment_run_identity", "metric_definition", "metric_observation", "dataset_version"),
        ("RUN_REPRODUCTION_PATH",),
    ),
    "notebook_debug": (
        ("notebook", "experiment", "codex", "code"),
        ("failing_cell", "error", "parameters", "producer"),
        ("NOTEBOOK_ERROR_PATH",),
    ),
    "claim_verification": (
        ("document", "experiment", "code", "notebook"),
        ("claim", "source_location", "metric_observation", "qualifier"),
        ("CLAIM_SUPPORT_PATH",),
    ),
    "reproduction": (
        ("experiment", "code", "notebook", "codex"),
        (
            "experiment_run_identity",
            "config_snapshot",
            "environment_snapshot",
            "dataset_version",
            "commit",
            "command",
        ),
        ("RUN_REPRODUCTION_PATH",),
    ),
    "staleness_check": (
        ("document", "experiment", "code", "notebook", "codex"),
        ("claim", "original_commit", "diff_or_revalidation", "current_validation"),
        ("CLAIM_SUPPORT_PATH", "VERSION_DRIFT_PATH"),
    ),
    "project_status": (
        ("workspace", "code", "experiment", "codex"),
        ("authoritative_state", "active_work", "evidence_coverage"),
        ("PROJECT_STATUS_PATH",),
    ),
    "global_synthesis": (
        MULTISOURCE_DOMAINS,
        ("implementation", "decision", "validation", "authoritative_state"),
        ("GLOBAL_EVIDENCE_PATH",),
    ),
}

_TASK_CAPABILITY_ALIASES: dict[str, dict[str, str]] = {
    "current_implementation": {"code": "implementation", "codex": "validation"},
    "historical_change": {
        "code": "historical",
        "codex": "process",
        "workspace": "temporal",
    },
    "rationale": {
        "codex": "rationale",
        "workspace": "decision",
        "code": "change_context",
        "document": "citation",
    },
    "experiment_validation": {
        "experiment": "compare",
        "notebook": "output",
        "code": "test_validation",
        "document": "claim",
    },
    "notebook_debug": {
        "notebook": "error",
        "experiment": "compare",
        "codex": "failure_retry",
        "code": "bug_localization",
    },
    "claim_verification": {
        "document": "claim",
        "experiment": "aggregate",
        "code": "test_validation",
        "notebook": "output",
    },
    "reproduction": {
        "experiment": "reproduce",
        "code": "implementation",
        "notebook": "reproduction",
        "codex": "process",
    },
    "staleness_check": {
        "document": "version",
        "experiment": "compare",
        "code": "change_context",
        "notebook": "compare",
        "codex": "validation",
    },
    "project_status": {
        "workspace": "current",
        "code": "implementation",
        "experiment": "aggregate",
        "codex": "process",
    },
    "global_synthesis": {
        "code": "implementation",
        "codex": "process",
        "experiment": "search",
        "notebook": "locate",
        "document": "locate",
        "workspace": "current",
    },
}

_LEGACY_TASK_CAPABILITY_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "current_implementation": {"code": ("search",), "codex": ("state", "search")},
    "historical_change": {
        "code": ("search",),
        "codex": ("state", "search"),
        "workspace": ("timeline", "search"),
    },
    "rationale": {
        "codex": ("rationale", "search"),
        "workspace": ("audit", "search"),
        "code": ("impact", "search"),
        "document": ("citation", "search"),
    },
    "experiment_validation": {
        "experiment": ("compare", "search"),
        "notebook": ("output", "search"),
        "code": ("impact", "search"),
        "document": ("claim", "search"),
    },
    "notebook_debug": {
        "notebook": ("error", "search"),
        "experiment": ("compare", "search"),
        "codex": ("state", "search"),
        "code": ("impact", "search"),
    },
    "claim_verification": {
        "document": ("claim", "search"),
        "experiment": ("aggregate", "search"),
        "code": ("impact", "search"),
        "notebook": ("output", "search"),
    },
    "reproduction": {
        "experiment": ("reproduce", "search"),
        "code": ("search",),
        "notebook": ("dataflow", "search"),
        "codex": ("state", "search"),
    },
    "staleness_check": {
        "document": ("citation", "search"),
        "experiment": ("compare", "search"),
        "code": ("impact", "search"),
        "notebook": ("output", "search"),
        "codex": ("state", "search"),
    },
    "project_status": {
        "workspace": ("status", "search"),
        "code": ("search",),
        "experiment": ("aggregate", "search"),
        "codex": ("state", "search"),
    },
    "global_synthesis": {
        "code": ("search",),
        "codex": ("search",),
        "experiment": ("search",),
        "notebook": ("search",),
        "document": ("search",),
        "workspace": ("search",),
    },
}


def _resolve_source_task(
    intent: str,
    source: str,
    capability: SourceCapabilityV2,
) -> str | None:
    selected_intent = intent if intent in _TASK_CAPABILITY_ALIASES else "global_synthesis"
    # Explicit source selection may add a useful supplemental source that is not
    # part of the intent's default route. Use that source's governed synthesis
    # task instead of raising or silently relabelling another source task.
    preferred = _TASK_CAPABILITY_ALIASES[selected_intent].get(
        source,
        _TASK_CAPABILITY_ALIASES["global_synthesis"][source],
    )
    alternatives = _LEGACY_TASK_CAPABILITY_ALIASES[selected_intent].get(
        source,
        _LEGACY_TASK_CAPABILITY_ALIASES["global_synthesis"].get(source, ()),
    )
    return next(
        (task for task in (preferred, *alternatives) if task in capability.supported_tasks),
        None,
    )


def resolve_source_task_capability_v2(
    *,
    intent: str,
    source: str,
    capability: SourceCapabilityV2,
) -> str | None:
    if source != capability.domain:
        raise ValueError("source does not match capability domain")
    if source not in MULTISOURCE_DOMAINS:
        raise ValueError("source is unknown")
    return _resolve_source_task(intent, source, capability)


def build_default_capability_registry_v2(
    *,
    generations: dict[str, str],
    watermarks: dict[str, str],
) -> CapabilityRegistryV2:
    specifications = {
        "code": (("dense", "exact", "graph", "sparse"), True, False),
        "codex": (("dense", "exact", "graph", "sparse"), True, False),
        "experiment": (("exact", "numeric", "sparse", "structured"), True, True),
        "notebook": (("dense", "exact", "graph", "sparse", "structured"), True, True),
        "document": (("exact", "graph", "sparse", "structured"), True, False),
        "workspace": (("exact", "graph", "structured"), True, False),
    }
    capabilities: list[SourceCapabilityV2] = []
    for domain in MULTISOURCE_DOMAINS:
        channels, relation, numeric = specifications[domain]
        payload = {
            "domain": domain,
            "retriever_version": f"{domain}-source-rag-v2",
            "supported_tasks": _SOURCE_TASKS[domain],
            "channels": tuple(sorted(channels)),
            "entity_types": tuple(sorted(_SOURCE_ENTITY_TYPES[domain])),
            "filters": tuple(sorted(("acl", "project", "version"))),
            "time_semantics": "observed_and_valid_time",
            "version_semantics": "content_addressed_generation",
            "supports_as_of": domain in {"code", "document", "workspace"},
            "supports_numeric": numeric,
            "supports_relation_expansion": relation,
            "calibration_version": f"{domain}-calibration-v2",
            "index_generation": generations[domain],
            "watermark": watermarks[domain],
            "acl_model": "inherited",
            "health": "ready",
        }
        capabilities.append(
            SourceCapabilityV2(
                **payload,
                content_sha256=canonical_sha256_v2(payload),
            )
        )
    payload = {
        "capabilities": [item.model_dump(mode="json") for item in capabilities],
        "registry_version": MULTISOURCE_CAPABILITY_VERSION,
    }
    return CapabilityRegistryV2(
        capabilities=tuple(capabilities),
        content_sha256=canonical_sha256_v2(payload),
    )


def plan_multisource_query_v2(
    *,
    question: str,
    intent: str,
    scope: MultiSourceScopeV2,
    registry: CapabilityRegistryV2,
    requested_sources: tuple[str, ...] = (),
    context_budget_tokens: int = 6_000,
) -> MultiSourcePlanV2:
    if not question.strip() or len(question) > 4_000:
        raise ValueError("question is not bounded")
    resolved_intent = intent if intent in _INTENT else "global_synthesis"
    routes, roles, templates = _INTENT[resolved_intent]
    if requested_sources:
        if any(source not in MULTISOURCE_DOMAINS for source in requested_sources):
            raise ValueError("requested source is unknown")
        routes = tuple(dict.fromkeys(requested_sources))
    capability_by_domain = {item.domain: item for item in registry.capabilities}
    planning_gaps: list[str] = (
        [] if resolved_intent == intent else [f"intent:{intent}:fallback_global_synthesis"]
    )
    accepted: list[str] = []
    selected_tasks: dict[str, str] = {}
    for source in routes:
        capability = capability_by_domain[source]
        task = _resolve_source_task(resolved_intent, source, capability)
        if capability.health == "unavailable":
            planning_gaps.append(f"{source}:unavailable")
        elif task is None:
            planning_gaps.append(f"{source}:task_unsupported")
        else:
            accepted.append(source)
            selected_tasks[source] = task
    complexity = (
        MultiSourceComplexityV2.SIMPLE
        if len(accepted) == 1 and len(roles) <= 2
        else MultiSourceComplexityV2.SINGLE_SOURCE
        if len(accepted) == 1
        else MultiSourceComplexityV2.MULTI_HOP
        if len(templates) > 1 or len(accepted) >= 4
        else MultiSourceComplexityV2.MULTI_SOURCE
    )
    route_tuple = tuple(accepted)
    base_budget = max(4, min(60, 6 + 2 * len(roles)))
    budgets = tuple(
        (
            source,
            min(
                80,
                base_budget + sum(2 for role in roles if source in _default_sources_for_role(role)),
            ),
        )
        for source in route_tuple
    )
    question_sha = canonical_sha256_v2(question.strip())
    source_contracts = tuple(
        MultiSourceRouteContractV2(
            source=source,  # type: ignore[arg-type]
            task=selected_tasks[source],
            fact_type=f"{source}.fact",
            fact_types=tuple(
                sorted(
                    f"{source}.{entity_type}"
                    for entity_type in capability_by_domain[source].entity_types
                )
            ),
            entity_types=capability_by_domain[source].entity_types,
            calibration_version=capability_by_domain[source].calibration_version,
            task_parameters=scope.task_parameters_for(source),
        )
        for source in route_tuple
    )
    task_by_source = {item.source: item.task for item in source_contracts}
    subquestions = tuple(
        MultiSourceSubQuestionV2(
            subquestion_id="subquestion-"
            + hashlib.sha256(
                f"{question_sha}\x1f{source}\x1f{resolved_intent}".encode()
            ).hexdigest(),
            parent_question_sha256=question_sha,
            question=f"{question.strip()} [source={source}]",
            sources=(source,),
            task=task_by_source[source],
            required_roles=tuple(
                role for role in roles if source in _default_sources_for_role(role)
            ),
            rewrite_provenance="deterministic_template",
        )
        for source in route_tuple
    )
    payload = {
        "question_sha256": question_sha,
        "intent": resolved_intent,
        "complexity": complexity,
        "scope": scope,
        "registry_sha256": registry.content_sha256,
        "subquestions": subquestions,
        "required_roles": tuple(roles),
        "optional_roles": (),
        "source_routes": route_tuple,
        "source_contracts": source_contracts,
        "source_budgets": budgets,
        "graph_templates": tuple(templates),
        "context_budget_tokens": context_budget_tokens,
        "stop_conditions": (
            "all_required_roles_satisfied",
            "explicit_unanswerable",
            "budget_exhausted",
        ),
        "planning_gaps": tuple(sorted(planning_gaps)),
        "planner_version": MULTISOURCE_PLANNER_VERSION,
    }
    normalized = MultiSourcePlanV2.model_construct(content_sha256="pending", **payload).model_dump(
        mode="json", exclude={"content_sha256"}
    )
    return MultiSourcePlanV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def build_multisource_golden_v2() -> MultiSourceGoldenReleaseV2:
    cases: list[MultiSourceGoldenCaseV2] = []
    index = 1
    fault_cycle = (
        None,
        "retriever_timeout",
        "retriever_partial",
        "stale_calibration",
        "wrong_generation",
        "snapshot_mismatch",
        "edge_review_downgrade",
        "duplicate_root",
        "reranker_unavailable",
        "graph_cycle",
        "acl_denied_middle",
        "corrective_zero_gain",
    )
    intents = tuple(_INTENT)
    for slice_name, count, sources in _GOLDEN_SLICES:
        for offset in range(count):
            case_id = f"multisource-v2-{index:03d}"
            intent = (
                "staleness_check"
                if "conflict" in slice_name
                else "claim_verification"
                if "document" in sources
                else "reproduction"
                if "experiment" in sources and "notebook" in sources
                else intents[(index - 1) % len(intents)]
            )
            roles = _INTENT[intent][1]
            expected_decision = (
                "unauthorized"
                if slice_name == "acl_sensitive"
                else "insufficient_evidence"
                if slice_name == "unanswerable_source_failure"
                else "potentially_stale"
                if slice_name == "version_conflict_stale"
                else "supported"
            )
            payload = {
                "case_id": case_id,
                "slice_name": slice_name,
                "question": f"Frozen cross-source question {index} for {slice_name}",
                "intent": intent,
                "complexity": (
                    MultiSourceComplexityV2.MULTI_HOP
                    if len(sources) >= 3
                    else MultiSourceComplexityV2.MULTI_SOURCE
                ),
                "required_sources": tuple(sources),
                "required_roles": tuple(roles),
                "required_entity_ids": tuple(f"{source}-entity-{index:03d}" for source in sources),
                "acceptable_alternatives": (),
                "required_paths": tuple(_INTENT[intent][2]),
                "target_version": f"version-{1 + offset % 3}",
                "forbidden_entities": (f"forbidden-{index:03d}",),
                "expected_decision": expected_decision,
                "expected_claim_citations": (
                    (
                        f"claim-{index:03d}",
                        tuple(f"{source}-entity-{index:03d}" for source in sources),
                    ),
                ),
                "fault_injection": fault_cycle[(index - 1) % len(fault_cycle)],
            }
            cases.append(
                MultiSourceGoldenCaseV2(
                    **payload,
                    content_sha256=canonical_sha256_v2(
                        {
                            **payload,
                            "complexity": payload["complexity"].value,
                        }
                    ),
                )
            )
            index += 1
    release_payload = {
        "dataset_id": MULTISOURCE_GOLDEN_VERSION,
        "cases": [item.model_dump(mode="json") for item in cases],
        "slice_counts": [(name, count) for name, count, _ in _GOLDEN_SLICES],
    }
    return MultiSourceGoldenReleaseV2(
        cases=tuple(cases),
        slice_counts=tuple((name, count) for name, count, _ in _GOLDEN_SLICES),
        content_sha256=canonical_sha256_v2(release_payload),
    )


def fit_source_calibration_v2(
    source: str,
    rows: tuple[CalibrationObservationV2, ...],
    *,
    task: str | None = None,
    entity_type: str | None = None,
    bins: int = 10,
) -> SourceCalibrationProfileV2:
    selected = tuple(
        item
        for item in rows
        if item.source == source
        and item.review_status == "reviewed"
        and (task is None or item.task == task)
        and (entity_type is None or item.entity_type == entity_type)
    )
    if (
        len(selected) < 2
        or not any(item.relevant for item in selected)
        or all(item.relevant for item in selected)
    ):
        raise ValueError("calibration needs positive and negative reviewed rows")
    if bins < 2 or bins > 20:
        raise ValueError("calibration bins must be within 2..20")
    ordered = sorted(selected, key=lambda item: (item.raw_score, item.relevant))
    bin_size = max(1, math.ceil(len(ordered) / bins))
    profile_bins: list[CalibrationBinV2] = []
    weighted_error = 0.0
    brier_sum = 0.0
    for start in range(0, len(ordered), bin_size):
        group = ordered[start : start + bin_size]
        positives = sum(item.relevant for item in group)
        probability = positives / len(group)
        profile_bins.append(
            CalibrationBinV2(
                lower=min(item.raw_score for item in group),
                upper=max(item.raw_score for item in group),
                probability=probability,
                count=len(group),
                positives=positives,
            )
        )
        mean_score = sum(item.raw_score for item in group) / len(group)
        weighted_error += len(group) * abs(probability - mean_score)
        brier_sum += sum((probability - float(item.relevant)) ** 2 for item in group)
    payload = {
        "source": source,
        "task": task or "unspecified",
        "entity_type": entity_type or "unspecified",
        "bins": tuple(profile_bins),
        "sample_count": len(selected),
        "reviewed_sample_count": len(selected),
        "ece": weighted_error / len(selected),
        "brier": brier_sum / len(selected),
        "provenance_sha256": canonical_sha256_v2(
            [item.model_dump(mode="json") for item in selected]
        ),
        "calibration_version": f"{source}-calibration-v2",
    }
    normalized = SourceCalibrationProfileV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return SourceCalibrationProfileV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def build_calibration_dashboard_v2(
    profiles: tuple[SourceCalibrationProfileV2, ...],
    *,
    required_slices: tuple[tuple[str, str, str], ...] = (),
) -> CalibrationDashboardV2:
    """Publish reviewed profile metrics and explicit unavailable rows only."""

    by_slice: dict[tuple[str, str, str], SourceCalibrationProfileV2] = {}
    for profile in profiles:
        key = (profile.source, profile.task, profile.entity_type)
        if key in by_slice:
            raise ValueError("duplicate calibration profile slice")
        by_slice[key] = profile
    requested = set(required_slices) | set(by_slice)
    rows: list[CalibrationDashboardRowV2] = []
    for source, task, entity_type in sorted(requested):
        profile = by_slice.get((source, task, entity_type))
        if profile is None:
            rows.append(
                CalibrationDashboardRowV2(
                    source=source,
                    task=task,
                    entity_type=entity_type,
                    status="unavailable",
                    sample_count=0,
                    positives=0,
                    negatives=0,
                    calibration_version=None,
                    profile_sha256=None,
                )
            )
            continue
        positives = sum(item.positives for item in profile.bins)
        rows.append(
            CalibrationDashboardRowV2(
                source=source,
                task=task,
                entity_type=entity_type,
                status="reviewed",
                sample_count=profile.reviewed_sample_count,
                positives=positives,
                negatives=profile.reviewed_sample_count - positives,
                ece=profile.ece,
                brier=profile.brier,
                calibration_version=profile.calibration_version,
                profile_sha256=profile.content_sha256,
            )
        )
    payload = {
        "rows": tuple(rows),
        "reviewed_profile_count": sum(item.status == "reviewed" for item in rows),
        "unavailable_profile_count": sum(item.status == "unavailable" for item in rows),
        "dashboard_version": MULTISOURCE_CALIBRATION_DASHBOARD_VERSION,
    }
    normalized = CalibrationDashboardV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return CalibrationDashboardV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def rerank_cross_source_candidates_v2(
    plan: MultiSourcePlanV2,
    candidates: tuple[MultiSourceCandidateV2, ...],
    calibration_profiles: tuple[SourceCalibrationProfileV2, ...],
) -> CrossSourceRerankResultV2:
    """Strict deterministic rerank over reviewed, source-local calibration outputs."""

    contracts = {item.source: item for item in plan.source_contracts}
    profiles: dict[tuple[str, str, str], SourceCalibrationProfileV2] = {}
    for profile in calibration_profiles:
        key = (profile.source, profile.task, profile.entity_type)
        if key in profiles:
            raise ValueError("duplicate calibration profile slice")
        profiles[key] = profile
    scored: list[tuple[float, MultiSourceCandidateV2, tuple[float, float, float, float]]] = []
    rejected: list[tuple[str, str]] = []
    used_profile_sha256s: set[str] = set()
    candidate_id_counts: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        candidate_id_counts[candidate.candidate_id] += 1
    for candidate in candidates:
        contract = contracts.get(candidate.retrieval_domain)
        reason: str | None = None
        profile = next(
            (
                profiles[key]
                for key in (
                    (candidate.retrieval_domain, candidate.task, candidate.entity_type),
                    (candidate.retrieval_domain, candidate.task, "unspecified"),
                    (candidate.retrieval_domain, "unspecified", candidate.entity_type),
                    (candidate.retrieval_domain, "unspecified", "unspecified"),
                )
                if key in profiles
            ),
            None,
        )
        if candidate_id_counts[candidate.candidate_id] > 1:
            reason = "duplicate_candidate_id"
        elif contract is None:
            reason = "source_not_routed"
        elif not contract.accepts_fact_type(candidate.fact_type):
            reason = "fact_type_mismatch"
        elif not contract.accepts_entity_type(candidate.entity_type):
            reason = "entity_type_unsupported"
        elif not contract.accepts_task(candidate.task):
            reason = "task_mismatch"
        elif candidate.acl_ref not in plan.scope.acl_refs:
            reason = "acl_denied"
        elif candidate.version_alignment == "mismatch":
            reason = "wrong_version"
        elif profile is None:
            reason = "reviewed_calibration_missing"
        elif profile.calibration_version != contract.calibration_version:
            reason = "calibration_contract_mismatch"
        elif candidate.calibration_version != profile.calibration_version:
            reason = "calibration_version_mismatch"
        elif (
            abs(
                candidate.calibrated_relevance
                - profile.probability(
                    max((score for _, score in candidate.channel_scores), default=0)
                )
            )
            > 1e-12
        ):
            reason = "calibrated_relevance_mismatch"
        if reason is not None:
            rejected.append((candidate.candidate_id, reason))
            continue
        used_profile_sha256s.add(profile.content_sha256)
        role_gain = len(set(candidate.matched_roles) & set(plan.required_roles)) / max(
            1, len(plan.required_roles)
        )
        version_factor = {
            "exact": 1.0,
            "historical_target": 1.0,
            "compatible": 0.9,
            "not_applicable": 0.85,
            "unknown": 0.6,
            "mismatch": 0.0,
        }[candidate.version_alignment]
        fact_factor = 1.0 if candidate.fact_status in {"verified", "observed", "accepted"} else 0.7
        authority_factor = candidate.authority
        score = (
            candidate.calibrated_relevance
            * (0.5 + 0.5 * authority_factor)
            * (1.0 + role_gain)
            * version_factor
            * fact_factor
        )
        if candidate.counter_evidence:
            score += 0.05
        scored.append(
            (
                score,
                candidate,
                (authority_factor, role_gain, version_factor, fact_factor),
            )
        )
    scored.sort(
        key=lambda item: (
            -item[0],
            plan.source_routes.index(item[1].retrieval_domain),
            item[1].entity_type,
            item[1].candidate_id,
        )
    )
    ranked = tuple(item[1] for item in scored)
    decisions = tuple(
        CrossSourceRerankDecisionV2(
            candidate_id=candidate.candidate_id,
            source=candidate.retrieval_domain,
            task=candidate.task,
            entity_type=candidate.entity_type,
            calibrated_relevance=candidate.calibrated_relevance,
            authority_factor=factors[0],
            role_gain=factors[1],
            version_factor=factors[2],
            fact_factor=factors[3],
            score=score,
            rank=rank,
        )
        for rank, (score, candidate, factors) in enumerate(scored, start=1)
    )
    payload = {
        "ranked": ranked,
        "decisions": decisions,
        "rejected": tuple(sorted(rejected)),
        "calibration_profile_sha256s": tuple(sorted(used_profile_sha256s)),
        "rerank_version": MULTISOURCE_RERANK_VERSION,
    }
    normalized = CrossSourceRerankResultV2.model_construct(
        content_sha256="pending", **payload
    ).model_dump(mode="json", exclude={"content_sha256"})
    return CrossSourceRerankResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def rerank_multisource_candidates_v2(
    plan: MultiSourcePlanV2,
    candidates: tuple[MultiSourceCandidateV2, ...],
    calibration_profiles: tuple[SourceCalibrationProfileV2, ...],
) -> CrossSourceRerankResultV2:
    return rerank_cross_source_candidates_v2(plan, candidates, calibration_profiles)


def _eligible_fusion_candidates_v2(
    plan: MultiSourcePlanV2,
    candidates: tuple[MultiSourceCandidateV2, ...],
    source_status: tuple[SourceExecutionV2, ...],
) -> tuple[list[MultiSourceCandidateV2], list[tuple[str, str]]]:
    routed = set(plan.source_routes)
    status_by_source = {item.source: item for item in source_status}
    contract_by_source = {item.source: item for item in plan.source_contracts}
    if len(status_by_source) != len(source_status):
        raise ValueError("source execution status contains duplicates")
    if set(status_by_source) != routed:
        raise ValueError("source execution status is incomplete")
    eligible: list[MultiSourceCandidateV2] = []
    rejected: list[tuple[str, str]] = []
    candidate_id_counts: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        candidate_id_counts[candidate.candidate_id] += 1
    for candidate in candidates:
        reason = None
        if candidate_id_counts[candidate.candidate_id] > 1:
            reason = "duplicate_candidate_id"
        elif candidate.retrieval_domain not in routed:
            reason = "source_not_routed"
        elif not contract_by_source[candidate.retrieval_domain].accepts_fact_type(
            candidate.fact_type
        ):
            reason = "fact_type_mismatch"
        elif not contract_by_source[candidate.retrieval_domain].accepts_entity_type(
            candidate.entity_type
        ):
            reason = "entity_type_unsupported"
        elif not contract_by_source[candidate.retrieval_domain].accepts_task(candidate.task):
            reason = "task_mismatch"
        elif candidate.acl_ref not in plan.scope.acl_refs:
            reason = "acl_denied"
        elif candidate.version_alignment == "mismatch":
            reason = "wrong_version"
        elif status_by_source[candidate.retrieval_domain].status in {
            SourceExecutionStatusV2.TIMEOUT,
            SourceExecutionStatusV2.UNAVAILABLE,
            SourceExecutionStatusV2.UNAUTHORIZED,
            SourceExecutionStatusV2.NOT_INDEXED,
        }:
            reason = "source_not_eligible"
        if reason:
            rejected.append((candidate.candidate_id, reason))
        else:
            eligible.append(candidate)
    return eligible, rejected


def fuse_multisource_candidates_v2(
    plan: MultiSourcePlanV2,
    candidates: tuple[MultiSourceCandidateV2, ...],
    source_status: tuple[SourceExecutionV2, ...],
    *,
    token_budget: int | None = None,
) -> RoleFusionResultV2:
    """Greedily maximize required-role utility with root/version/status gates."""

    budget = token_budget or plan.context_budget_tokens
    if budget < 256:
        raise ValueError("fusion token budget is too small")
    eligible, rejected = _eligible_fusion_candidates_v2(
        plan,
        candidates,
        source_status,
    )
    selected: list[MultiSourceCandidateV2] = []
    satisfied: set[str] = set()
    roots: set[str] = set()
    used = 0
    remaining = list(eligible)
    while remaining:
        ranked: list[tuple[float, str, MultiSourceCandidateV2]] = []
        for candidate in remaining:
            new_roles = set(candidate.matched_roles) & set(plan.required_roles) - satisfied
            independent = candidate.root_provenance not in roots
            counter_bonus = 0.4 if candidate.counter_evidence else 0.0
            role_gain = 1.2 * len(new_roles)
            independence_gain = 0.25 if independent else -0.5
            fact_factor = (
                1.0 if candidate.fact_status in {"verified", "observed", "accepted"} else 0.75
            )
            utility = (
                0.55 * candidate.calibrated_relevance
                + 0.25 * candidate.authority
                + role_gain
                + independence_gain
                + counter_bonus
            ) * fact_factor
            utility /= max(1.0, candidate.token_estimate / 256)
            ranked.append((utility, candidate.candidate_id, candidate))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        utility, _, candidate = ranked[0]
        remaining.remove(candidate)
        if candidate.root_provenance in roots and not candidate.counter_evidence:
            rejected.append((candidate.candidate_id, "duplicate_root_provenance"))
            continue
        if used + candidate.token_estimate > budget:
            rejected.append((candidate.candidate_id, "token_budget"))
            continue
        new_roles = set(candidate.matched_roles) & set(plan.required_roles) - satisfied
        if utility <= 0 or (
            not new_roles and candidate.root_provenance in roots and not candidate.counter_evidence
        ):
            rejected.append((candidate.candidate_id, "no_marginal_utility"))
            continue
        selected.append(candidate)
        used += candidate.token_estimate
        satisfied.update(candidate.matched_roles)
        roots.add(candidate.root_provenance)
        if set(plan.required_roles).issubset(satisfied) and not any(
            item.counter_evidence for item in remaining
        ):
            break
    satisfied_roles = tuple(role for role in plan.required_roles if role in satisfied)
    missing_roles = tuple(role for role in plan.required_roles if role not in satisfied)
    payload = {
        "selected": tuple(selected),
        "satisfied_roles": satisfied_roles,
        "missing_roles": missing_roles,
        "source_status": tuple(
            sorted(source_status, key=lambda item: plan.source_routes.index(item.source))
        ),
        "rejected": tuple(sorted(rejected)),
        "total_tokens": used,
        "budget_tokens": budget,
        "fusion_version": MULTISOURCE_FUSION_VERSION,
    }
    normalized = RoleFusionResultV2.model_construct(content_sha256="pending", **payload).model_dump(
        mode="json", exclude={"content_sha256"}
    )
    return RoleFusionResultV2(
        **payload,
        content_sha256=canonical_sha256_v2(normalized),
    )


def evaluate_multisource_plans_v2(
    golden: MultiSourceGoldenReleaseV2,
    plans: tuple[MultiSourcePlanV2, ...],
) -> dict[str, float | int | str]:
    if len(plans) != len(golden.cases):
        raise ValueError("plan evaluation membership mismatch")
    source_tp = source_fp = source_fn = schema_violations = 0
    intent_correct = 0
    for case, plan in zip(golden.cases, plans, strict=True):
        intent_correct += plan.intent == case.intent
        predicted = set(plan.source_routes)
        expected = set(case.required_sources)
        source_tp += len(predicted & expected)
        source_fp += len(predicted - expected)
        source_fn += len(expected - predicted)
        schema_violations += bool(plan.planning_gaps)
    precision = source_tp / max(1, source_tp + source_fp)
    recall = source_tp / max(1, source_tp + source_fn)
    macro_f1 = 2 * precision * recall / max(1e-12, precision + recall)
    return {
        "dataset_sha256": golden.content_sha256,
        "cases": len(golden.cases),
        "intent_accuracy": intent_correct / len(golden.cases),
        "source_route_precision": precision,
        "source_route_recall": recall,
        "source_route_macro_f1": macro_f1,
        "planner_schema_capability_violations": schema_violations,
    }


def _default_sources_for_role(role: str) -> frozenset[str]:
    if role in {"current_symbol", "current_version", "implementation", "commit", "original_commit"}:
        return frozenset({"code"})
    if role in {"goal", "decision", "alternatives"}:
        return frozenset({"codex", "workspace"})
    if role in {
        "experiment_run_identity",
        "metric_definition",
        "metric_observation",
        "dataset_version",
        "config_snapshot",
        "environment_snapshot",
    }:
        return frozenset({"experiment"})
    if role in {"failing_cell", "error", "parameters", "producer", "command"}:
        return frozenset({"notebook", "codex"})
    if role in {"claim", "source_location", "qualifier"}:
        return frozenset({"document"})
    if role in {"authoritative_state", "active_work", "evidence_coverage"}:
        return frozenset({"workspace"})
    return frozenset(MULTISOURCE_DOMAINS)


__all__ = [
    "MULTISOURCE_CALIBRATION_VERSION",
    "MULTISOURCE_CALIBRATION_DASHBOARD_VERSION",
    "MULTISOURCE_CAPABILITY_VERSION",
    "MULTISOURCE_DOMAINS",
    "MULTISOURCE_FUSION_VERSION",
    "MULTISOURCE_GOLDEN_VERSION",
    "MULTISOURCE_PLANNER_VERSION",
    "MULTISOURCE_RERANK_VERSION",
    "CalibrationBinV2",
    "CalibrationDashboardRowV2",
    "CalibrationDashboardV2",
    "CalibrationObservationV2",
    "CapabilityRegistryV2",
    "CrossSourceRerankDecisionV2",
    "CrossSourceRerankResultV2",
    "MultiSourceCandidateV2",
    "MultiSourceComplexityV2",
    "MultiSourceGoldenCaseV2",
    "MultiSourceGoldenReleaseV2",
    "MultiSourcePlanV2",
    "MultiSourceRouteContractV2",
    "MultiSourceScopeV2",
    "MultiSourceSubQuestionV2",
    "RoleFusionResultV2",
    "SourceCalibrationProfileV2",
    "SourceCapabilityV2",
    "SourceExecutionStatusV2",
    "SourceExecutionV2",
    "build_default_capability_registry_v2",
    "build_calibration_dashboard_v2",
    "build_multisource_golden_v2",
    "evaluate_multisource_plans_v2",
    "fit_source_calibration_v2",
    "fuse_multisource_candidates_v2",
    "plan_multisource_query_v2",
    "rerank_cross_source_candidates_v2",
    "rerank_multisource_candidates_v2",
    "resolve_source_task_capability_v2",
    "source_entity_types_v2",
]
