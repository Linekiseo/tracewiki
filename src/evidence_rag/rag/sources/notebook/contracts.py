"""Frozen Notebook Source V2 contracts.

The contracts deliberately separate authored revision identity from observed execution
identity.  They are runtime-independent and safe to serialize into an isolated store.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

NOTEBOOK_CONTRACT_VERSION = "notebook-contract-v2"
NOTEBOOK_ADAPTER_VERSION = "jupyter-notebook-adapter-v2"
NOTEBOOK_SCHEMA_VERSION = "notebook-isolated-schema-v2"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,511}$")
_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,127}$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$"
)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _identifier(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError("identifier is not canonical")
    return normalized


def _digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("digest must be canonical sha256")
    return value


def _mime_type(value: str) -> str:
    normalized = value.casefold()
    if not _MIME_RE.fullmatch(normalized):
        raise ValueError("MIME type is not canonical")
    return normalized


def _timestamp(value: str) -> str:
    if not _TIMESTAMP_RE.fullmatch(value):
        raise ValueError("timestamp must be a canonical ISO-8601 value with timezone")
    return value


def _bounded_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized) > 200_000:
        raise ValueError("text exceeds contract limit")
    for character in normalized:
        if unicodedata.category(character).startswith("C") and character not in {"\n", "\r", "\t"}:
            raise ValueError("text contains unsupported control characters")
    return normalized


def _locator(value: str) -> str:
    if len(value) > 4_000 or "\\" in value or any(character.isspace() for character in value):
        raise ValueError("locator is not canonical")
    parsed = urlsplit(value)
    if parsed.scheme != "notebook" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("locator must be an absolute notebook URI without query or fragment")
    segments = parsed.path.split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise ValueError("locator contains a traversal segment")
    if "//" in parsed.path:
        raise ValueError("locator contains an empty path segment")
    return value


Identifier = Annotated[StrictStr, AfterValidator(_identifier)]
Digest = Annotated[StrictStr, AfterValidator(_digest)]
MimeType = Annotated[StrictStr, AfterValidator(_mime_type)]
Timestamp = Annotated[StrictStr, AfterValidator(_timestamp)]
BoundedText = Annotated[StrictStr, AfterValidator(_bounded_text)]
Locator = Annotated[StrictStr, AfterValidator(_locator)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def canonical_json_bytes(self) -> bytes:
        return canonical_json_bytes(self.model_dump(mode="json"))

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class NotebookCellType(StrEnum):
    CODE = "code"
    MARKDOWN = "markdown"
    RAW = "raw"


class NotebookExecutionStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class NotebookCellExecutionState(StrEnum):
    EXECUTED = "executed"
    FAILED = "failed"
    NOT_EXECUTED = "not_executed"
    UNKNOWN = "unknown"


class NotebookParameterType(StrEnum):
    NULL = "null"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    LIST = "list"
    OBJECT = "object"
    UNRESOLVED = "unresolved"


class NotebookArtifactType(StrEnum):
    STREAM = "stream"
    DISPLAY = "display"
    EXECUTE_RESULT = "execute_result"
    ERROR = "error"
    BINARY_OMITTED = "binary_omitted"


class NotebookEdgeType(StrEnum):
    CONTAINS = "contains"
    EXECUTES = "executes"
    DEFINES = "defines"
    READS = "reads"
    DEPENDS_ON = "depends_on"
    REDEFINES = "redefines"
    PRODUCES = "produces"
    RETRY_OF = "retry_of"
    SAME_CELL_AS = "same_cell_as"


class NotebookIdentityConfidence(StrEnum):
    NATIVE = "native"
    DERIVED = "derived"
    AMBIGUOUS = "ambiguous"


class NotebookScope(_FrozenContract):
    project_id: Identifier
    acl_ref: Identifier
    generation_id: Identifier


class NotebookTemplate(_FrozenContract):
    template_id: Identifier
    scope: NotebookScope
    source_key: Identifier
    name: Identifier
    language: Identifier | None = None
    kernel_name: Identifier | None = None
    locator: Locator


class NotebookRevision(_FrozenContract):
    revision_id: Identifier
    template_id: Identifier
    scope: NotebookScope
    source_version: Identifier
    content_sha256: Digest
    metadata_sha256: Digest
    nbformat: PositiveInt
    nbformat_minor: NonNegativeInt
    cell_version_ids: tuple[Identifier, ...]
    locator: Locator

    @model_validator(mode="after")
    def _unique_cells(self) -> NotebookRevision:
        if len(set(self.cell_version_ids)) != len(self.cell_version_ids):
            raise ValueError("revision cell membership must be unique")
        return self


class NotebookExecution(_FrozenContract):
    execution_id: Identifier
    template_id: Identifier
    revision_id: Identifier
    scope: NotebookScope
    execution_key: Identifier
    status: NotebookExecutionStatus
    started_at: Timestamp | None = None
    completed_at: Timestamp | None = None
    content_sha256: Digest
    cell_execution_ids: tuple[Identifier, ...]
    parameter_ids: tuple[Identifier, ...]
    locator: Locator

    @model_validator(mode="after")
    def _unique_membership(self) -> NotebookExecution:
        if len(set(self.cell_execution_ids)) != len(self.cell_execution_ids):
            raise ValueError("execution cell membership must be unique")
        if len(set(self.parameter_ids)) != len(self.parameter_ids):
            raise ValueError("execution parameter membership must be unique")
        return self


class NotebookCellVersion(_FrozenContract):
    cell_version_id: Identifier
    stable_cell_id: Identifier
    revision_id: Identifier
    scope: NotebookScope
    display_order: NonNegativeInt
    cell_type: NotebookCellType
    source: BoundedText
    source_sha256: Digest
    tags: tuple[Identifier, ...] = ()
    native_cell_id: Identifier | None = None
    identity_confidence: NotebookIdentityConfidence
    locator: Locator


class NotebookCellExecution(_FrozenContract):
    cell_execution_id: Identifier
    execution_id: Identifier
    cell_version_id: Identifier
    scope: NotebookScope
    display_order: NonNegativeInt
    execution_count: NonNegativeInt | None = None
    execution_order: NonNegativeInt | None = None
    state: NotebookCellExecutionState
    stale: StrictBool
    output_ids: tuple[Identifier, ...] = ()
    diagnostics: tuple[Identifier, ...] = ()
    locator: Locator

    @model_validator(mode="after")
    def _execution_state(self) -> NotebookCellExecution:
        if self.execution_count is None and self.execution_order is not None:
            raise ValueError("execution order requires an observed execution count")
        if len(set(self.output_ids)) != len(self.output_ids):
            raise ValueError("cell output membership must be unique")
        return self


class NotebookParameter(_FrozenContract):
    parameter_id: Identifier
    execution_id: Identifier
    cell_version_id: Identifier
    scope: NotebookScope
    name: Identifier
    value_type: NotebookParameterType
    canonical_value: BoundedText
    value_sha256: Digest
    locator: Locator


class NotebookSymbol(_FrozenContract):
    symbol_id: Identifier
    cell_version_id: Identifier
    revision_id: Identifier
    scope: NotebookScope
    name: Identifier
    role: Identifier
    confidence: NotebookIdentityConfidence
    locator: Locator


class NotebookArtifact(_FrozenContract):
    artifact_id: Identifier
    execution_id: Identifier
    cell_execution_id: Identifier
    scope: NotebookScope
    ordinal: NonNegativeInt
    artifact_type: NotebookArtifactType
    mime_types: tuple[MimeType, ...] = ()
    text: BoundedText = ""
    error_name: Identifier | None = None
    error_value: BoundedText | None = None
    binary_omitted: StrictBool = False
    metric_confirmed: Literal[False] = False
    content_sha256: Digest
    locator: Locator


class NotebookRetrievalUnit(_FrozenContract):
    unit_id: Identifier
    revision_id: Identifier
    execution_id: Identifier | None = None
    scope: NotebookScope
    entity_id: Identifier
    unit_type: Identifier
    profile: Identifier
    content: BoundedText
    content_sha256: Digest
    locator: Locator


class NotebookEdge(_FrozenContract):
    edge_id: Identifier
    revision_id: Identifier
    execution_id: Identifier | None = None
    scope: NotebookScope
    source_id: Identifier
    target_id: Identifier
    edge_type: NotebookEdgeType
    confidence: NotebookIdentityConfidence
    locator: Locator

    @model_validator(mode="after")
    def _not_self_edge(self) -> NotebookEdge:
        if self.source_id == self.target_id:
            raise ValueError("self edges are not canonical notebook evidence")
        return self


class NotebookComparison(_FrozenContract):
    comparison_id: Identifier
    scope: NotebookScope
    baseline_revision_id: Identifier
    candidate_revision_id: Identifier
    matches_sha256: Digest
    locator: Locator


class NotebookPublication(_FrozenContract):
    publication_id: Identifier
    contract_version: Identifier = NOTEBOOK_CONTRACT_VERSION
    adapter_version: Identifier = NOTEBOOK_ADAPTER_VERSION
    schema_version: Identifier = NOTEBOOK_SCHEMA_VERSION
    source_payload_sha256: Digest
    template: NotebookTemplate
    revision: NotebookRevision
    execution: NotebookExecution
    cell_versions: tuple[NotebookCellVersion, ...]
    cell_executions: tuple[NotebookCellExecution, ...]
    parameters: tuple[NotebookParameter, ...] = ()
    symbols: tuple[NotebookSymbol, ...] = ()
    artifacts: tuple[NotebookArtifact, ...] = ()
    retrieval_units: tuple[NotebookRetrievalUnit, ...] = ()
    edges: tuple[NotebookEdge, ...] = ()
    comparisons: tuple[NotebookComparison, ...] = ()

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"publication_id"})

    @model_validator(mode="after")
    def _validate_graph(self) -> NotebookPublication:
        expected_id = "nbpub-" + canonical_sha256(self.identity_payload()).removeprefix("sha256:")
        if self.publication_id != expected_id:
            raise ValueError("publication id does not match canonical publication content")
        scope = self.template.scope
        if self.revision.scope != scope or self.execution.scope != scope:
            raise ValueError("publication root scopes do not match")
        if self.revision.template_id != self.template.template_id:
            raise ValueError("revision template identity mismatch")
        if (
            self.execution.template_id != self.template.template_id
            or self.execution.revision_id != self.revision.revision_id
        ):
            raise ValueError("execution root identity mismatch")

        cell_versions = {item.cell_version_id: item for item in self.cell_versions}
        cell_executions = {item.cell_execution_id: item for item in self.cell_executions}
        parameters = {item.parameter_id: item for item in self.parameters}
        artifacts = {item.artifact_id: item for item in self.artifacts}
        if len(cell_versions) != len(self.cell_versions):
            raise ValueError("duplicate cell version identity")
        if len(cell_executions) != len(self.cell_executions):
            raise ValueError("duplicate cell execution identity")
        if len(parameters) != len(self.parameters) or len(artifacts) != len(self.artifacts):
            raise ValueError("duplicate publication child identity")
        if (
            tuple(item.cell_version_id for item in self.cell_versions)
            != self.revision.cell_version_ids
        ):
            raise ValueError("revision membership does not match cell versions")
        if (
            tuple(item.cell_execution_id for item in self.cell_executions)
            != self.execution.cell_execution_ids
        ):
            raise ValueError("execution membership does not match cell executions")
        if tuple(item.parameter_id for item in self.parameters) != self.execution.parameter_ids:
            raise ValueError("execution membership does not match parameters")

        child_collections = (
            self.cell_versions,
            self.cell_executions,
            self.parameters,
            self.symbols,
            self.artifacts,
            self.retrieval_units,
            self.edges,
            self.comparisons,
        )
        if any(item.scope != scope for collection in child_collections for item in collection):
            raise ValueError("publication child scope mismatch")
        for item in self.cell_versions:
            if item.revision_id != self.revision.revision_id:
                raise ValueError("cell version revision mismatch")
        for item in self.cell_executions:
            if item.execution_id != self.execution.execution_id:
                raise ValueError("cell execution root mismatch")
            if item.cell_version_id not in cell_versions:
                raise ValueError("cell execution references an unknown cell version")
            if any(output_id not in artifacts for output_id in item.output_ids):
                raise ValueError("cell execution references an unknown output")
        for item in self.parameters:
            if item.execution_id != self.execution.execution_id:
                raise ValueError("parameter execution mismatch")
            if item.cell_version_id not in cell_versions:
                raise ValueError("parameter references an unknown cell version")
        for item in self.symbols:
            if item.revision_id != self.revision.revision_id:
                raise ValueError("symbol revision mismatch")
            if item.cell_version_id not in cell_versions:
                raise ValueError("symbol references an unknown cell version")
        for item in self.artifacts:
            if (
                item.execution_id != self.execution.execution_id
                or item.cell_execution_id not in cell_executions
            ):
                raise ValueError("artifact execution identity mismatch")
        endpoint_ids = {
            self.revision.revision_id,
            self.execution.execution_id,
            *cell_versions,
            *cell_executions,
            *parameters,
            *(item.symbol_id for item in self.symbols),
            *artifacts,
            *(item.unit_id for item in self.retrieval_units),
        }
        for item in self.edges:
            if item.revision_id != self.revision.revision_id:
                raise ValueError("edge revision mismatch")
            if item.execution_id not in {None, self.execution.execution_id}:
                raise ValueError("edge execution mismatch")
            if item.source_id not in endpoint_ids or item.target_id not in endpoint_ids:
                raise ValueError("edge endpoint is outside publication membership")
        return self


__all__ = [
    "NOTEBOOK_ADAPTER_VERSION",
    "NOTEBOOK_CONTRACT_VERSION",
    "NOTEBOOK_SCHEMA_VERSION",
    "NotebookArtifact",
    "NotebookArtifactType",
    "NotebookCellExecution",
    "NotebookCellExecutionState",
    "NotebookCellType",
    "NotebookCellVersion",
    "NotebookComparison",
    "NotebookEdge",
    "NotebookEdgeType",
    "NotebookExecution",
    "NotebookExecutionStatus",
    "NotebookIdentityConfidence",
    "NotebookParameter",
    "NotebookParameterType",
    "NotebookPublication",
    "NotebookRetrievalUnit",
    "NotebookRevision",
    "NotebookScope",
    "NotebookSymbol",
    "NotebookTemplate",
    "canonical_json_bytes",
    "canonical_sha256",
]
