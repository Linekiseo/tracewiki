from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .query.interaction_v2 import ConversationSnapshot


class RepositoryIngestRequest(BaseModel):
    source: str = Field(min_length=1, description="Local folder or remote Git URL")
    branch: str | None = None
    project_id: str = "project-rag"
    acl_ref: str = "project:project-rag"
    ignore: list[str] = Field(default_factory=list)
    history_depth: int = Field(
        default=25,
        ge=1,
        le=500,
        description="Selected Git history depth to index for commit and diff evidence",
    )

    @field_validator("source")
    @classmethod
    def trim_source(cls, value: str) -> str:
        return value.strip()


class CodexIngestRequest(BaseModel):
    source: str | None = Field(
        default=None,
        description="Codex home, sessions directory, or a rollout JSONL file",
    )
    project_path: str | None = Field(
        default=None,
        description="Only include sessions whose cwd/workspace belongs to this project",
    )
    project_id: str = "project-rag"
    acl_ref: str = "project:project-rag"
    include_archived: bool = False
    max_sessions: int = Field(default=200, ge=1, le=2_000)

    @field_validator("source", "project_path")
    @classmethod
    def trim_optional_path(cls, value: str | None) -> str | None:
        stripped = value.strip() if value else ""
        return stripped or None


class CodexProjectSyncRequest(BaseModel):
    force: bool = False
    include_archived: bool = False
    max_sessions: int = Field(default=2_000, ge=1, le=2_000)


class SearchScope(BaseModel):
    project_id: str | None = None
    repository_ids: list[str] = Field(default_factory=list)
    commit: str | None = None
    languages: list[str] = Field(default_factory=list)
    entity_types: list[str] = Field(default_factory=list)
    branch: str | None = None
    experiment_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    thread_ids: list[str] = Field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    source_types: list[
        Literal["code", "codex", "workspace", "experiment", "notebook", "document"]
    ] = Field(default_factory=list)
    allowed_acl_refs: list[str] = Field(default_factory=list, exclude=True)
    enforce_acl: bool = Field(default=False, exclude=True)


class EvidenceSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    scope: SearchScope = Field(default_factory=SearchScope)
    limit: int = Field(default=12, ge=1, le=50)
    include_edges: bool = True


class CodexSearchScope(BaseModel):
    project_id: str | None = None
    thread_ids: list[str] = Field(default_factory=list)
    item_types: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    allowed_acl_refs: list[str] = Field(default_factory=list, exclude=True)
    enforce_acl: bool = Field(default=False, exclude=True)


class CodexSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    scope: CodexSearchScope = Field(default_factory=CodexSearchScope)
    limit: int = Field(default=12, ge=1, le=50)
    include_edges: bool = True


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    scope: SearchScope = Field(default_factory=SearchScope)
    mode: Literal["answer", "evidence"] = "answer"
    max_evidence: int = Field(default=10, ge=1, le=30)
    intent: (
        Literal[
            "current_implementation",
            "historical_implementation",
            "change_trace",
            "rationale",
            "experiment_validation",
            "claim_verification",
            "reproduction",
            "staleness_check",
            "global_synthesis",
        ]
        | None
    ) = None
    as_of: str | None = None
    include: list[Literal["code", "codex", "workspace", "experiment", "notebook", "document"]] = (
        Field(default_factory=list)
    )
    max_hops: int = Field(default=2, ge=0, le=4)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    client_turn_id: str | None = Field(default=None, min_length=1, max_length=128)
    parent_turn_id: str | None = Field(default=None, min_length=1, max_length=128)
    context_revision: int | None = Field(default=None, ge=0)
    conversation_snapshot: ConversationSnapshot | None = None
    clarification_policy: Literal["auto", "always", "never"] | None = None
    deadline_ms: int | None = Field(default=None, ge=100, le=120_000)
    max_context_tokens: int | None = Field(default=None, ge=256, le=100_000)
    answer_format: Literal["concise", "detailed", "evidence_only"] | None = None

    @field_validator("question")
    @classmethod
    def trim_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped

    @field_validator("conversation_id", "client_turn_id", "parent_turn_id")
    @classmethod
    def trim_optional_interaction_id(cls, value: str | None) -> str | None:
        stripped = value.strip() if value else ""
        return stripped or None


@dataclass(slots=True)
class ParsedSymbol:
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    content: str
    calls: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedCodeUnitIR:
    structural_path: str
    node_type: str
    role: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    text: str
    parent_structural_path: str | None
    identifiers: list[str] = field(default_factory=list)
    signature: str | None = None
    doc: str | None = None
    quality: str = "complete"
    parser_version: str = ""


@dataclass(slots=True)
class ParsedFile:
    path: str
    language: str
    content: str
    content_hash: str
    blob_hash: str
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    parse_error: str | None = None
    units: list[ParsedCodeUnitIR] = field(default_factory=list)


@dataclass(slots=True)
class EntityRecord:
    id: str
    repository_id: str
    generation_id: str
    project_id: str
    entity_type: str
    name: str
    qualified_name: str | None
    path: str | None
    language: str | None
    commit_sha: str
    blob_hash: str | None
    content_hash: str
    start_line: int | None
    end_line: int | None
    source_uri: str
    acl_ref: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EdgeRecord:
    id: str
    repository_id: str
    generation_id: str
    source_id: str
    target_id: str
    edge_type: str
    derivation: str = "deterministic"
    confidence: float = 1.0
    evidence_locator: str | None = None


@dataclass(slots=True)
class SearchViewRecord:
    id: str
    entity_id: str
    repository_id: str
    generation_id: str
    project_id: str
    view_type: str
    name: str
    path: str
    language: str
    content: str
    vector: bytes
    embedding_model: str


@dataclass(slots=True)
class CodexThreadRecord:
    id: str
    thread_id: str
    source_id: str
    generation_id: str
    project_id: str
    title: str
    cwd: str | None
    status: str
    started_at: str | None
    updated_at: str | None
    source_file: str
    source_hash: str
    acl_ref: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CodexTurnRecord:
    id: str
    turn_id: str
    thread_id: str
    source_id: str
    generation_id: str
    ordinal: int
    status: str
    started_at: str | None
    completed_at: str | None
    goal: str
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CodexItemRecord:
    id: str
    item_id: str
    thread_id: str
    turn_id: str
    source_id: str
    generation_id: str
    sequence: int
    item_type: str
    role: str | None
    status: str | None
    timestamp: str | None
    name: str
    content: str
    source_locator: str
    acl_ref: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CodexEdgeRecord:
    id: str
    source_id: str
    generation_id: str
    source_entity_id: str
    target_entity_id: str
    edge_type: str
    derivation: str = "deterministic"
    confidence: float = 1.0
    evidence_locator: str | None = None


@dataclass(slots=True)
class CodexSearchViewRecord:
    id: str
    entity_id: str
    thread_id: str
    source_id: str
    generation_id: str
    project_id: str
    view_type: str
    name: str
    content: str
    vector: bytes
    embedding_model: str
