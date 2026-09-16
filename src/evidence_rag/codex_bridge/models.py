from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ExecutionMode = Literal["interactive", "queue"]
ExecutionStatus = Literal[
    "awaiting_approval",
    "queued",
    "running",
    "review",
    "completed",
    "failed",
    "cancelled",
]


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    version: str | None = Field(default=None, max_length=80)


class TokenCreate(BaseModel):
    client_id: str
    project_id: str = "project-rag"
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        return value.astimezone(UTC)


class ExecutionCreate(BaseModel):
    project_id: str = "project-rag"
    work_item_id: str
    mode: ExecutionMode = "interactive"
    workspace_path: str | None = None
    base_ref: str | None = None
    prompt: str = Field(default="", max_length=32_000)
    sandbox: Literal["read-only", "workspace-write"] = "read-only"


class ExecutionClaim(BaseModel):
    thread_id: str = Field(min_length=1, max_length=240)
    client_id: str | None = None


class ExecutionReport(BaseModel):
    status: Literal["running", "review", "failed"]
    summary: str = Field(default="", max_length=32_000)
    changed_files: list[str] = Field(default_factory=list, max_length=2_000)
    validation: list[dict[str, Any]] = Field(default_factory=list, max_length=1_000)
    error: str | None = Field(default=None, max_length=32_000)
    artifact_path: str | None = Field(default=None, max_length=4_000)


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    decided_by: str = Field(default="RAG Core", min_length=1, max_length=160)
    note: str = Field(default="", max_length=4_000)
