from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HistorySyncRequest(BaseModel):
    repository_id: str
    depth: int = Field(default=25, ge=1, le=500)
    include_diffs: bool = True


class TestResultCreate(BaseModel):
    project_id: str = "project-rag"
    repository_id: str
    commit_sha: str = Field(min_length=7, max_length=200)
    command: str = Field(min_length=1, max_length=8_000)
    status: Literal["passed", "failed", "error", "skipped", "cancelled"]
    exit_code: int | None = None
    duration_ms: float | None = Field(default=None, ge=0)
    stdout_ref: str | None = Field(default=None, max_length=4_000)
    stderr_ref: str | None = Field(default=None, max_length=4_000)
    framework: str | None = Field(default=None, max_length=160)
    metadata: dict[str, Any] = Field(default_factory=dict)
    acl_ref: str = "project:project-rag"
