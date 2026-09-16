from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BindingScanRequest(BaseModel):
    project_id: str = "project-rag"
    repository_ids: list[str] = Field(default_factory=list, max_length=100)
    thread_ids: list[str] = Field(default_factory=list, max_length=500)


class BindingReviewRequest(BaseModel):
    decision: Literal["confirmed", "rejected"]
    reviewer: str = Field(default="RAG Core", min_length=1, max_length=160)
    note: str = Field(default="", max_length=4_000)
    predicate: str = Field(default="changed_path_maps_to", min_length=1, max_length=120)


class BindingBulkReviewRequest(BaseModel):
    project_id: str = "project-rag"
    decision: Literal["confirmed"] = "confirmed"
    expected_pending: int = Field(ge=1, le=100_000)
    note: str = Field(default="批量确认当前待复核关系", max_length=4_000)
