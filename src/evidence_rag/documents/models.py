from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ClaimStatus = Literal[
    "reported",
    "verified",
    "partially_supported",
    "contradicted",
    "superseded",
    "potentially_stale",
    "insufficient_evidence",
]


class DocumentIngestRequest(BaseModel):
    project_id: str = "project-rag"
    iteration_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    version: str = Field(default="v1", min_length=1, max_length=100)
    source: str | None = None
    content: str | None = Field(default=None, max_length=5_000_000)
    authors: list[str] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=50)
    extract_claims: bool = True

    @model_validator(mode="after")
    def source_or_content(self) -> DocumentIngestRequest:
        if bool(self.source) == bool(self.content):
            raise ValueError("provide exactly one of source or content")
        return self


class ClaimCreate(BaseModel):
    document_id: str
    section_id: str | None = None
    content: str = Field(min_length=1, max_length=8_000)
    claim_type: Literal[
        "performance", "method", "observation", "comparison", "limitation", "other"
    ] = "other"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaimUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=8_000)
    claim_type: (
        Literal["performance", "method", "observation", "comparison", "limitation", "other"] | None
    ) = None
    status: ClaimStatus | None = None
    metadata: dict[str, Any] | None = None


class ClaimEvidenceCreate(BaseModel):
    claim_id: str
    evidence_entity_id: str
    evidence_type: Literal[
        "experiment_run",
        "metric",
        "artifact",
        "code",
        "codex",
        "document",
        "table_cell",
        "figure",
        "metric_aggregation",
    ]
    relationship: Literal["supports", "refutes", "qualifies"] = "supports"
    confidence: float = Field(default=1.0, ge=0, le=1)
    note: str = Field(default="", max_length=4_000)
    actor: str = Field(default="RAG Core", min_length=1, max_length=160)


class ClaimEvidenceUnlink(BaseModel):
    claim_id: str
    evidence_id: str
    actor: str = Field(default="RAG Core", min_length=1, max_length=160)
    note: str = Field(default="", max_length=4_000)


class ClaimMatchReview(BaseModel):
    decision: Literal["confirmed", "rejected"]
    relationship: Literal["supports", "refutes", "qualifies"] = "supports"
    reviewer: str = Field(default="RAG Core", min_length=1, max_length=160)
    note: str = Field(default="", max_length=4_000)


class TableMetricMatchReview(BaseModel):
    decision: Literal["confirmed", "rejected"]
    reviewer: str = Field(default="RAG Core", min_length=1, max_length=160)
    note: str = Field(default="", max_length=4_000)


class TableMetricAggregationCreate(BaseModel):
    project_id: str = "project-rag"
    table_cell_id: str
    claim_id: str | None = None
    metric_ids: list[str] = Field(min_length=2, max_length=200)
    aggregation_function: Literal["mean", "median", "sum", "min", "max"] = "mean"
    excluded_metric_ids: list[str] = Field(default_factory=list, max_length=200)
    tolerance: float = Field(default=1e-6, ge=0, le=1)
    actor: str = Field(default="RAG Core", min_length=1, max_length=160)
    note: str = Field(default="", max_length=4_000)
