from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExperimentCreate(BaseModel):
    project_id: str = "project-rag"
    iteration_id: str | None = None
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(default="", max_length=8_000)
    hypothesis: str = Field(default="", max_length=8_000)
    owner: str = Field(default="RAG Core", max_length=160)
    status: Literal["draft", "running", "completed", "archived"] = "draft"
    tags: list[str] = Field(default_factory=list, max_length=30)


class ExperimentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    objective: str | None = Field(default=None, max_length=8_000)
    hypothesis: str | None = Field(default=None, max_length=8_000)
    owner: str | None = Field(default=None, max_length=160)
    status: Literal["draft", "running", "completed", "archived"] | None = None
    tags: list[str] | None = Field(default=None, max_length=30)


class MetricInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    value: float
    unit: str | None = Field(default=None, max_length=60)
    split: str | None = Field(default=None, max_length=100)
    step: int | None = None
    # Metric direction is registry knowledge, not an inference from the metric name.
    # Unknown direction must remain unknown so "best" cannot silently mean maximum.
    higher_is_better: bool | None = None


class ArtifactInput(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    uri: str = Field(min_length=1, max_length=2_000)
    kind: str = Field(default="artifact", max_length=100)
    checksum: str | None = Field(default=None, max_length=200)
    media_type: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunCreate(BaseModel):
    experiment_id: str
    external_id: str | None = Field(default=None, max_length=240)
    name: str = Field(default="Experiment run", min_length=1, max_length=240)
    status: Literal["queued", "running", "completed", "failed", "cancelled"] = "completed"
    repository_id: str | None = None
    commit_sha: str | None = Field(default=None, max_length=200)
    branch: str | None = Field(default=None, max_length=240)
    dataset_id: str | None = Field(default=None, max_length=500)
    dataset_version: str | None = Field(default=None, max_length=240)
    config: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    command: str = Field(default="", max_length=8_000)
    started_at: str | None = None
    completed_at: str | None = None
    metrics: list[MetricInput] = Field(default_factory=list, max_length=500)
    artifacts: list[ArtifactInput] = Field(default_factory=list, max_length=200)
    tags: dict[str, str] = Field(default_factory=dict)


class RunUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=240)
    status: Literal["queued", "running", "completed", "failed", "cancelled"] | None = None
    completed_at: str | None = None
    config: dict[str, Any] | None = None
    environment: dict[str, Any] | None = None
    tags: dict[str, str] | None = None


class ComparisonRequest(BaseModel):
    run_ids: list[str] = Field(min_length=2, max_length=20)
    baseline_run_id: str | None = None
    name: str = Field(default="Run comparison", max_length=240)


class MLflowSyncRequest(BaseModel):
    project_id: str = "project-rag"
    tracking_uri: str = Field(min_length=1, max_length=4_000)
    experiment_ids: list[str] = Field(default_factory=list, max_length=100)
    repository_id: str | None = None
    max_runs: int = Field(default=500, ge=1, le=10_000)
    acl_ref: str = "project:project-rag"
