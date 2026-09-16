from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DirectedPathEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=2_000)
    edge_type: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=2_000)
    direction: Literal["outgoing", "incoming"]


class RequiredDirectedPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[str] = Field(min_length=2, max_length=20)
    edges: list[DirectedPathEdge] = Field(min_length=1, max_length=19)

    @model_validator(mode="after")
    def validate_directed_steps(self) -> RequiredDirectedPath:
        if len(self.edges) != len(self.nodes) - 1:
            raise ValueError("required path must have exactly one edge per adjacent node pair")
        for index, edge in enumerate(self.edges):
            left, right = self.nodes[index : index + 2]
            expected = (left, right) if edge.direction == "outgoing" else (right, left)
            if (edge.source, edge.target) != expected:
                raise ValueError("path edge endpoints do not match nodes and traversal direction")
        return self


class AcceptableAlternativeGroup(BaseModel):
    """One required slot that may be satisfied by any explicitly listed alternative."""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1, max_length=120)
    entity_ids: list[str] = Field(default_factory=list, max_length=100)
    retrieval_unit_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_real_alternatives(self) -> AcceptableAlternativeGroup:
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("alternative entity IDs must be unique")
        if len(set(self.retrieval_unit_ids)) != len(self.retrieval_unit_ids):
            raise ValueError("alternative retrieval-unit IDs must be unique")
        if len(self.entity_ids) < 2 and len(self.retrieval_unit_ids) < 2:
            raise ValueError("an acceptable alternative group requires at least two alternatives")
        return self


class CodeEvaluationCaseProfile(BaseModel):
    """Code-specific expectations stored alongside the stable V1 case."""

    model_config = ConfigDict(extra="forbid")

    source_domain: Literal["code"] = "code"
    task: str = Field(min_length=1, max_length=120)
    query_profile: dict[str, Any] = Field(default_factory=dict)
    dataset_id: str | None = Field(default=None, max_length=120)
    dataset_version: str | None = Field(default=None, max_length=120)
    dataset_package_hash: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    expected_unit_ids: list[str] = Field(default_factory=list, max_length=200)
    expected_entity_types: list[str] = Field(default_factory=list, max_length=30)
    expected_locators: list[Any] = Field(default_factory=list, max_length=100)
    required_paths: list[RequiredDirectedPath] = Field(default_factory=list, max_length=100)
    required_edge_types: list[str] = Field(default_factory=list, max_length=100)
    acceptable_alternative_groups: list[AcceptableAlternativeGroup] = Field(
        default_factory=list, max_length=100
    )
    expected_context_roles: list[str] = Field(default_factory=list, max_length=30)
    expected_ref: str | None = Field(default=None, max_length=240)
    expected_answer_mode: Literal["direct", "refuse", "clarify", "missing_evidence"] = "direct"

    @model_validator(mode="after")
    def validate_profile_contract(self) -> CodeEvaluationCaseProfile:
        dataset_values = (
            self.dataset_id,
            self.dataset_version,
            self.dataset_package_hash,
        )
        if any(dataset_values) and not all(dataset_values):
            raise ValueError("dataset_id, dataset_version and dataset_package_hash move together")
        if len(set(self.required_edge_types)) != len(self.required_edge_types):
            raise ValueError("required_edge_types must be unique")
        path_edge_types = {edge.edge_type for path in self.required_paths for edge in path.edges}
        if self.required_paths and path_edge_types != set(self.required_edge_types):
            raise ValueError(
                "required_edge_types must exactly match typed required-path edge types"
            )
        group_ids = [group.group_id for group in self.acceptable_alternative_groups]
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("acceptable alternative group IDs must be unique")
        return self


class EvaluationCandidateJudgment(BaseModel):
    """A relevance label for a stable entity, a retrieval unit, or both."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str | None = Field(default=None, max_length=2_000)
    retrieval_unit_id: str | None = Field(default=None, max_length=2_000)
    relevance_grade: Literal[-1, 0, 1, 2]
    necessity_role: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def require_target(self) -> EvaluationCandidateJudgment:
        if not self.entity_id and not self.retrieval_unit_id:
            raise ValueError("a candidate judgment requires entity_id or retrieval_unit_id")
        return self


class EvaluationMetricValue(BaseModel):
    """Persisted V2 metric contract, including honest unavailability."""

    model_config = ConfigDict(extra="forbid")

    evaluation_run_id: str
    case_id: str | None = None
    source_domain: Literal["code"]
    metric_name: str
    slice: dict[str, Any] = Field(default_factory=dict)
    value: float | None = None
    status: Literal["available", "unavailable"] = "available"
    unavailable_reason: str | None = None
    numerator: float | None = None
    denominator: float | None = None
    eligible: bool = True
    total_cases: int | None = Field(default=None, ge=0)
    eligible_cases: int | None = Field(default=None, ge=0)
    available_cases: int | None = Field(default=None, ge=0)
    unavailable_cases: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_availability(self) -> EvaluationMetricValue:
        if self.status == "available" and self.value is None:
            raise ValueError("available metric requires a numeric value")
        if self.status == "unavailable" and not self.unavailable_reason:
            raise ValueError("unavailable metric requires a reason")
        if self.numerator is not None and self.denominator is None:
            raise ValueError("metric numerator requires denominator")
        if self.denominator is not None and self.denominator < 0:
            raise ValueError("metric denominator cannot be negative")
        coverage = (
            self.total_cases,
            self.eligible_cases,
            self.available_cases,
            self.unavailable_cases,
        )
        if any(value is not None for value in coverage):
            if not all(value is not None for value in coverage):
                raise ValueError("aggregate coverage counts move together")
            assert self.total_cases is not None
            assert self.eligible_cases is not None
            assert self.available_cases is not None
            assert self.unavailable_cases is not None
            if self.eligible_cases > self.total_cases:
                raise ValueError("eligible_cases cannot exceed total_cases")
            if self.available_cases + self.unavailable_cases != self.eligible_cases:
                raise ValueError("available_cases plus unavailable_cases must equal eligible_cases")
        return self


class EvaluationCaseCreate(BaseModel):
    project_id: str = "project-rag"
    name: str = Field(min_length=1, max_length=240)
    question: str = Field(min_length=1, max_length=2_000)
    expected_sources: list[Literal["code", "codex", "workspace", "experiment", "document"]] = Field(
        default_factory=list
    )
    expected_entity_ids: list[str] = Field(default_factory=list, max_length=100)
    expected_paths: list[list[str]] = Field(default_factory=list, max_length=100)
    expected_commit_ids: list[str] = Field(default_factory=list, max_length=100)
    forbidden_entity_ids: list[str] = Field(default_factory=list, max_length=100)
    required_version: str | None = Field(default=None, max_length=240)
    tags: list[str] = Field(default_factory=list, max_length=30)
    enabled: bool = True
    code_profile: CodeEvaluationCaseProfile | None = None
    candidate_judgments: list[EvaluationCandidateJudgment] = Field(
        default_factory=list, max_length=500
    )


class EvaluationRunRequest(BaseModel):
    project_id: str = "project-rag"
    case_ids: list[str] = Field(default_factory=list, max_length=500)
    limit_per_query: int = Field(default=20, ge=1, le=100)


class CodeEvaluationRunRequest(EvaluationRunRequest):
    """Offline Code Evaluation V2 run configuration."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    dataset_version: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    package_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    case_ids: list[str] = Field(min_length=1, max_length=500)
    repository_ids: list[str] = Field(default_factory=list, max_length=100)
    commit: str | None = Field(default=None, max_length=240)
    graph_candidate_enabled: bool = False
    paired_graph_off_run_id: str | None = Field(default=None, max_length=2_000)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_pairing(self) -> CodeEvaluationRunRequest:
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("case_ids must be unique")
        if len(set(self.repository_ids)) != len(self.repository_ids):
            raise ValueError("repository_ids must be unique")
        if self.paired_graph_off_run_id and not self.graph_candidate_enabled:
            raise ValueError("paired_graph_off_run_id requires graph_candidate_enabled=true")
        return self
