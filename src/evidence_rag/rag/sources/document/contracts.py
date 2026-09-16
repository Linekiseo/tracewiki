"""Frozen contracts for the source-specific Scientific Document V2 pipeline.

The application-wide Document tables remain V1-compatible.  These contracts own an
isolated, additive representation where logical family identity, immutable versions,
source-authored facts, parser/model derivations, and reviewed claims stay distinct.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, Self
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

DOCUMENT_CONTRACT_VERSION = "scientific-document-contract-v2"
DOCUMENT_ADAPTER_VERSION = "scientific-document-adapter-v2"
DOCUMENT_SCHEMA_VERSION = "scientific-document-isolated-schema-v2"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,511}$")


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
    value = unicodedata.normalize("NFC", value)
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier is not canonical")
    return value


def _digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("digest must be canonical sha256")
    return value


def _bounded_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    if len(value) > 300_000:
        raise ValueError("text exceeds contract limit")
    for character in value:
        if unicodedata.category(character).startswith("C") and character not in {"\n", "\r", "\t"}:
            raise ValueError("text contains unsupported control characters")
    return value


def _locator(value: str) -> str:
    if len(value) > 4_000 or "\\" in value or any(character.isspace() for character in value):
        raise ValueError("locator is not canonical")
    parsed = urlsplit(value)
    if parsed.scheme != "document" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("locator must be an absolute document URI")
    if "//" in parsed.path or any(segment in {".", ".."} for segment in parsed.path.split("/")):
        raise ValueError("locator path is not canonical")
    return value


Identifier = Annotated[StrictStr, AfterValidator(_identifier)]
Digest = Annotated[StrictStr, AfterValidator(_digest)]
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


class DocumentSourceKind(StrEnum):
    MARKDOWN = "markdown"
    HTML = "html"
    PDF_TEXT = "pdf_text"
    PDF_SCANNED = "pdf_scanned"
    PDF_MIXED = "pdf_mixed"
    DOCX = "docx"


class DocumentParseQuality(StrEnum):
    EXACT = "exact"
    DEGRADED = "degraded"
    OCR_DERIVED = "ocr_derived"
    UNAVAILABLE = "unavailable"


class DocumentParserProviderStatus(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class DocumentOcrStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class DocumentLayoutBlockType(StrEnum):
    HEADER = "header"
    BODY = "body"
    FOOTER = "footer"
    TABLE = "table"
    FIGURE = "figure"


class DocumentEntityType(StrEnum):
    PAGE = "page"
    LAYOUT_BLOCK = "layout_block"
    SECTION = "section"
    PARAGRAPH = "paragraph"
    CLAIM_CANDIDATE = "claim_candidate"
    CLAIM = "claim"
    TABLE = "table"
    TABLE_ROW = "table_row"
    TABLE_CELL_FACT = "table_cell_fact"
    FIGURE = "figure"
    FORMULA = "formula"
    REFERENCE_WORK = "reference_work"
    CITATION_MENTION = "citation_mention"
    SECTION_SUMMARY = "section_summary"
    VERSION_DIFF = "version_diff"


class DocumentFactAuthority(StrEnum):
    SOURCE_AUTHORED = "source_authored"
    PARSER_DERIVED = "parser_derived"
    MODEL_DERIVED = "model_derived"
    REVIEWED_DERIVED = "reviewed_derived"
    CROSS_SOURCE_CONFIRMED = "cross_source_confirmed"


class DocumentClaimStatus(StrEnum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REPORTED = "reported"
    PARTIALLY_SUPPORTED = "partially_supported"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    POTENTIALLY_STALE = "potentially_stale"
    REVALIDATED = "revalidated"
    SUPERSEDED = "superseded"


class DocumentEdgeType(StrEnum):
    HAS_VERSION = "has_version"
    HAS_PAGE = "has_page"
    HAS_BLOCK = "has_block"
    HAS_SECTION = "has_section"
    HAS_PARAGRAPH = "has_paragraph"
    REPORTS = "reports"
    HAS_TABLE = "has_table"
    HAS_ROW = "has_row"
    HAS_CELL_FACT = "has_cell_fact"
    HAS_FIGURE = "has_figure"
    HAS_FORMULA = "has_formula"
    CITES = "cites"
    RESOLVES_TO = "resolves_to"
    SUPPORTS = "supports"
    REFUTES = "refutes"
    QUALIFIES = "qualifies"
    SUPERSEDES = "supersedes"
    SAME_LOGICAL_ENTITY = "same_logical_entity"
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    MOVED = "moved"
    NEIGHBOR = "neighbor"


class DocumentScope(_FrozenContract):
    project_id: Identifier
    acl_ref: Identifier
    generation_id: Identifier


class DocumentFamily(_FrozenContract):
    family_id: Identifier
    scope: DocumentScope
    source_key: Identifier
    canonical_title: BoundedText
    locator: Locator


class DocumentVersion(_FrozenContract):
    version_id: Identifier
    family_id: Identifier
    scope: DocumentScope
    version_label: Identifier
    source_kind: DocumentSourceKind
    content_sha256: Digest
    parse_quality: DocumentParseQuality
    parser_version: Identifier
    source_text: BoundedText
    entity_ids: tuple[Identifier, ...]
    locator: Locator

    @model_validator(mode="after")
    def _unique_entities(self) -> DocumentVersion:
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("document version entity membership must be unique")
        return self


class DocumentEntity(_FrozenContract):
    entity_id: Identifier
    stable_id: Identifier
    entity_type: DocumentEntityType
    family_id: Identifier
    version_id: Identifier
    scope: DocumentScope
    parent_id: Identifier | None = None
    ordinal: NonNegativeInt
    page_number: PositiveInt | None = None
    source_text: BoundedText = ""
    derived_text: BoundedText = ""
    content_sha256: Digest
    locator: Locator
    authority: DocumentFactAuthority
    status: Identifier
    label: BoundedText | None = None
    row_path: tuple[BoundedText, ...] = ()
    column_path: tuple[BoundedText, ...] = ()
    raw_value: BoundedText | None = None
    numeric_center: float | None = None
    numeric_spread: float | None = None
    unit: Identifier | None = None
    footnotes: tuple[BoundedText, ...] = ()
    target_id: Identifier | None = None
    resolution_confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _typed_contract(self) -> DocumentEntity:
        if self.entity_type == DocumentEntityType.TABLE_CELL_FACT and (
            not self.row_path or not self.column_path or self.raw_value is None
        ):
            raise ValueError("table cell facts require row/column paths and raw value")
        if self.entity_type == DocumentEntityType.CITATION_MENTION and (
            not self.label or not self.target_id
        ):
            raise ValueError("citation mentions require marker and resolved target")
        if (
            self.entity_type == DocumentEntityType.CLAIM_CANDIDATE
            and self.status != DocumentClaimStatus.CANDIDATE
        ):
            raise ValueError("claim candidates cannot be promoted in parser output")
        if self.authority == DocumentFactAuthority.MODEL_DERIVED and not self.metadata.get(
            "derivation_version"
        ):
            raise ValueError("model-derived facts require derivation version")
        return self


class DocumentEdge(_FrozenContract):
    edge_id: Identifier
    version_id: Identifier
    scope: DocumentScope
    source_id: Identifier
    target_id: Identifier
    edge_type: DocumentEdgeType
    authority: DocumentFactAuthority
    confidence: float = Field(ge=0, le=1)
    review_status: Identifier
    locator: Locator


class DocumentRetrievalUnit(_FrozenContract):
    unit_id: Identifier
    version_id: Identifier
    scope: DocumentScope
    entity_id: Identifier
    entity_type: DocumentEntityType
    exact_keys: tuple[BoundedText, ...]
    sparse_text: BoundedText
    dense_source_text: BoundedText
    dense_derived_text: BoundedText
    source_text_sha256: Digest
    derived_text_sha256: Digest
    builder_version: Identifier
    locator: Locator


class DocumentPublication(_FrozenContract):
    publication_id: Identifier
    source_payload_sha256: Digest
    family: DocumentFamily
    version: DocumentVersion
    entities: tuple[DocumentEntity, ...]
    edges: tuple[DocumentEdge, ...]
    retrieval_units: tuple[DocumentRetrievalUnit, ...]
    publication_sha256: Digest

    @model_validator(mode="after")
    def _closed_publication(self) -> DocumentPublication:
        entities = {item.entity_id: item for item in self.entities}
        if len(entities) != len(self.entities):
            raise ValueError("document entity membership must be unique")
        if set(self.version.entity_ids) != set(entities):
            raise ValueError("document version membership must exactly match entities")
        for item in self.entities:
            if (
                item.version_id != self.version.version_id
                or item.family_id != self.family.family_id
                or item.scope != self.family.scope
            ):
                raise ValueError("document entity scope/version is inconsistent")
            if (
                item.parent_id is not None
                and item.parent_id not in entities
                and item.parent_id
                not in {
                    self.version.version_id,
                    self.family.family_id,
                }
            ):
                raise ValueError("document entity parent is outside publication")
        for edge in self.edges:
            if edge.version_id != self.version.version_id or edge.scope != self.family.scope:
                raise ValueError("document edge scope/version is inconsistent")
            if edge.source_id not in entities | {
                self.family.family_id: self.family,
                self.version.version_id: self.version,
            } or edge.target_id not in entities | {
                self.family.family_id: self.family,
                self.version.version_id: self.version,
            }:
                raise ValueError("document edge endpoint is outside publication")
        if {item.entity_id for item in self.retrieval_units} - set(entities):
            raise ValueError("retrieval unit references unknown entity")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"publication_sha256"}))
        if self.publication_sha256 != expected:
            raise ValueError("document publication digest mismatch")
        return self


class DocumentTombstone(_FrozenContract):
    tombstone_id: Identifier
    scope: DocumentScope
    family_id: Identifier
    version_id: Identifier | None = None
    reason: Identifier
    content_sha256: Digest


class DocumentComparison(_FrozenContract):
    comparison_id: Identifier
    scope: DocumentScope
    family_id: Identifier
    baseline_version_id: Identifier
    candidate_version_id: Identifier
    added_ids: tuple[Identifier, ...]
    removed_ids: tuple[Identifier, ...]
    modified_ids: tuple[Identifier, ...]
    moved_ids: tuple[Identifier, ...]
    stale_claim_ids: tuple[Identifier, ...]
    comparison_sha256: Digest


class DocumentSecurityReport(_FrozenContract):
    secret_leakage: NonNegativeInt = 0
    absolute_path_leakage: NonNegativeInt = 0
    unauthorized_leakage: NonNegativeInt = 0
    reasoning_leakage: NonNegativeInt = 0
    parser_script_execution: NonNegativeInt = 0
    binary_embedding: NonNegativeInt = 0
    passed: StrictBool
