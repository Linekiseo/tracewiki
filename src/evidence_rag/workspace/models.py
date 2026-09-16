from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ProjectStatus = Literal["active", "paused", "archived"]
TopicStatus = Literal["backlog", "active", "blocked", "completed", "archived"]
IterationStatus = Literal["planned", "active", "validating", "completed", "blocked", "archived"]
WorkItemKind = Literal["research", "development", "experiment", "analysis", "review"]
WorkItemStatus = Literal[
    "backlog", "ready", "running", "review", "blocked", "done", "cancelled"
]
ReviewStatus = Literal["unreviewed", "confirmed", "rejected"]


class ProjectCreate(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4_000)
    owner: str = Field(default="RAG Core", max_length=160)
    acl_ref: str | None = None
    classification: Literal["public", "internal", "confidential", "restricted"] = "internal"
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "name", "owner")
    @classmethod
    def trim_values(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4_000)
    owner: str | None = Field(default=None, max_length=160)
    status: ProjectStatus | None = None
    classification: Literal["public", "internal", "confidential", "restricted"] | None = None
    settings: dict[str, Any] | None = None


class TopicCreate(BaseModel):
    project_id: str = "project-rag"
    title: str = Field(min_length=1, max_length=240)
    problem_statement: str = Field(default="", max_length=8_000)
    objective: str = Field(default="", max_length=8_000)
    status: TopicStatus = "backlog"
    priority: int = Field(default=3, ge=1, le=5)
    owner: str = Field(default="RAG Core", max_length=160)
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title", "problem_statement", "objective", "owner")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()


class TopicUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    problem_statement: str | None = Field(default=None, max_length=8_000)
    objective: str | None = Field(default=None, max_length=8_000)
    status: TopicStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    owner: str | None = Field(default=None, max_length=160)
    tags: list[str] | None = Field(default=None, max_length=30)


class IterationCreate(BaseModel):
    project_id: str = "project-rag"
    topic_id: str
    title: str = Field(min_length=1, max_length=240)
    goal: str = Field(default="", max_length=8_000)
    hypothesis: str = Field(default="", max_length=8_000)
    status: IterationStatus = "planned"
    starts_at: str | None = None
    target_at: str | None = None
    owner: str = Field(default="RAG Core", max_length=160)


class IterationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    goal: str | None = Field(default=None, max_length=8_000)
    hypothesis: str | None = Field(default=None, max_length=8_000)
    status: IterationStatus | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    starts_at: str | None = None
    target_at: str | None = None
    completed_at: str | None = None
    summary: str | None = Field(default=None, max_length=8_000)
    owner: str | None = Field(default=None, max_length=160)


class WorkItemCreate(BaseModel):
    project_id: str = "project-rag"
    topic_id: str | None = None
    iteration_id: str | None = None
    repository_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(default="", max_length=8_000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    kind: WorkItemKind = "development"
    status: WorkItemStatus = "backlog"
    priority: int = Field(default=3, ge=1, le=5)
    assignee_type: Literal["human", "codex"] = "human"
    assignee: str = Field(default="RAG Core", max_length=160)
    workspace_path: str | None = Field(default=None, max_length=4_000)
    base_ref: str | None = Field(default=None, max_length=240)
    due_at: str | None = None

    @field_validator("title", "objective", "assignee")
    @classmethod
    def trim_work_item_text(cls, value: str) -> str:
        return value.strip()


class WorkItemUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    topic_id: str | None = None
    iteration_id: str | None = None
    repository_id: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=240)
    objective: str | None = Field(default=None, max_length=8_000)
    acceptance_criteria: list[str] | None = Field(default=None, max_length=50)
    kind: WorkItemKind | None = None
    status: WorkItemStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    assignee_type: Literal["human", "codex"] | None = None
    assignee: str | None = Field(default=None, max_length=160)
    workspace_path: str | None = Field(default=None, max_length=4_000)
    base_ref: str | None = Field(default=None, max_length=240)
    due_at: str | None = None
    summary: str | None = Field(default=None, max_length=8_000)


class WorkItemTransition(BaseModel):
    expected_version: int = Field(ge=1)
    status: WorkItemStatus
    summary: str | None = Field(default=None, max_length=8_000)


class IterationLinkCreate(BaseModel):
    iteration_id: str
    entity_id: str
    entity_type: str = Field(min_length=1, max_length=100)
    source_type: Literal["code", "codex", "experiment", "document", "workspace"]
    role: str = Field(default="evidence", min_length=1, max_length=100)
    status: str = Field(default="linked", max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelationCreate(BaseModel):
    project_id: str = "project-rag"
    source_entity_id: str
    predicate: str = Field(min_length=1, max_length=120)
    target_entity_id: str
    evidence_entity_id: str | None = None
    derivation: Literal[
        "deterministic",
        "parser_extracted",
        "rule_derived",
        "llm_inferred",
        "human_confirmed",
    ] = "deterministic"
    confidence: float = Field(default=1.0, ge=0, le=1)
    review_status: ReviewStatus = "unreviewed"
    rule_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelationReview(BaseModel):
    review_status: Literal["confirmed", "rejected"]
    reviewer: str = Field(default="RAG Core", min_length=1, max_length=160)
    note: str = Field(default="", max_length=4_000)
