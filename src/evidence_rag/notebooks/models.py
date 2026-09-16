from __future__ import annotations

from pydantic import BaseModel, Field


class NotebookIngestRequest(BaseModel):
    source: str
    project_id: str = "project-rag"
    experiment_id: str | None = None
    run_id: str | None = None
    version: str = Field(default="v1", min_length=1, max_length=200)
    acl_ref: str = "project:project-rag"


class NotebookCompareRequest(BaseModel):
    baseline_id: str
    candidate_id: str
