from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class CodexComparisonCreate(BaseModel):
    project_id: str = "project-rag"
    thread_ids: list[str] = Field(min_length=2, max_length=8)
    baseline_thread_id: str | None = None
    name: str = Field(default="Codex session comparison", min_length=1, max_length=240)
    created_by: str = Field(default="RAG Core", min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_threads(self) -> CodexComparisonCreate:
        self.thread_ids = list(dict.fromkeys(self.thread_ids))
        if len(self.thread_ids) < 2:
            raise ValueError("select at least two distinct Codex threads")
        if self.baseline_thread_id and self.baseline_thread_id not in self.thread_ids:
            raise ValueError("baseline thread must be included in thread_ids")
        return self
