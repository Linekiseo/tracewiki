from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class SourceEventInput(BaseModel):
    event_id: str | None = Field(default=None, max_length=240)
    source_type: Literal["git", "codex", "document", "mlflow", "notebook", "dvc", "manual"]
    source_instance: str = Field(min_length=1, max_length=500)
    event_type: str = Field(min_length=1, max_length=160)
    source_object_id: str = Field(min_length=1, max_length=2_000)
    source_version: str = Field(min_length=1, max_length=500)
    event_time: str | None = None
    project_id: str = "project-rag"
    acl_ref: str = "project:project-rag"
    source_uri: str | None = Field(default=None, max_length=4_000)
    payload_ref: str | None = Field(default=None, max_length=4_000)
    payload: Any | None = None
    media_type: str = Field(default="application/json", max_length=200)
    schema_version: str = Field(default="source-envelope-v1", max_length=160)
    adapter_version: str = Field(default="api-v1", max_length=160)
    trace_id: str | None = Field(default=None, max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def payload_or_reference(self) -> SourceEventInput:
        if self.payload is None and not self.payload_ref:
            raise ValueError("provide payload or payload_ref")
        return self


class RawTombstoneRequest(BaseModel):
    raw_object_id: str
    reason: str = Field(min_length=1, max_length=2_000)
    actor: str = Field(default="api-user", max_length=240)
