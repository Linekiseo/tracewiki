"""Pure-bytes Scientific Document adapter and typed entity builder."""

from __future__ import annotations

import hashlib
import json
import math
import re
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    DOCUMENT_ADAPTER_VERSION,
    DocumentEdge,
    DocumentEdgeType,
    DocumentEntity,
    DocumentEntityType,
    DocumentFactAuthority,
    DocumentFamily,
    DocumentLayoutBlockType,
    DocumentOcrStatus,
    DocumentParseQuality,
    DocumentParserProviderStatus,
    DocumentPublication,
    DocumentRetrievalUnit,
    DocumentScope,
    DocumentSourceKind,
    DocumentVersion,
    canonical_sha256,
)

DOCUMENT_UNIT_BUILDER_VERSION = "scientific-document-unit-builder-v2"

_SECRET_RE = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*[^\s,;]+")
_ABS_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'`(])(?:/(?:Users|home|private|tmp|var|etc|opt)/"
    r"|[a-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+)"
)
_ENCODED_PATH_MARKERS = ("%2fusers%2f", "%2fhome%2f", "%5c%5c", "%2f%2f")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FIGURE_RE = re.compile(r"(?i)^Figure\s+([A-Za-z0-9.-]+)\s*[:.]\s*(.*)$")
_FORMULA_RE = re.compile(r"(?i)^(?:Equation|Formula)\s*\(?([A-Za-z0-9.-]+)\)?\s*[:.]\s*(.*)$")
_REFERENCE_RE = re.compile(r"(?i)^REF\s+\[([^\]]+)\]\s*(.*?)\s*(?:\|\s*DOI\s*:\s*([^\s|]+))?\s*$")
_CITATION_RE = re.compile(r"\[([0-9A-Za-z.-]+)\]")
_CLAIM_RE = re.compile(r"(?i)^(?:Claim|Conclusion|Finding)\s*:\s*(.+)$")
_NUMBER_RE = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:\s*±\s*([0-9.]+))?\s*(%|percent|ms|s)?"
)


class DocumentAdapterError(ValueError):
    """Raised when raw document input cannot safely form a publication."""


class _FrozenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DocumentSourceBlock(_FrozenInput):
    block_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    ordinal: int = Field(ge=1)
    block_type: DocumentLayoutBlockType
    text: str = Field(min_length=1, max_length=200_000)
    bbox: tuple[float, float, float, float]
    text_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source: Literal["native", "ocr"]
    confidence: float = Field(ge=0, le=1)
    parser_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    parser_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    bbox_method: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

    @model_validator(mode="after")
    def _verified_block(self) -> DocumentSourceBlock:
        if not all(math.isfinite(value) for value in self.bbox):
            raise ValueError("layout block bbox must be finite")
        x0, top, x1, bottom = self.bbox
        if x0 < 0 or top < 0 or x1 <= x0 or bottom <= top:
            raise ValueError("layout block bbox must have positive area")
        if self.text_sha256 != _content_digest(self.text):
            raise ValueError("layout block text digest mismatch")
        return self


class DocumentParserProvenanceInput(_FrozenInput):
    provider_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    provider_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    provider_status: DocumentParserProviderStatus
    input_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class DocumentSourcePage(_FrozenInput):
    page_number: int = Field(ge=1)
    text: str = Field(default="", max_length=200_000)
    ocr_text: str = Field(default="", max_length=200_000)
    width: float = Field(default=612.0, gt=0)
    height: float = Field(default=792.0, gt=0)
    page_identity: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    page_content_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    scan_detected: bool | None = None
    ocr_status: DocumentOcrStatus | None = None
    blocks: tuple[DocumentSourceBlock, ...] | None = None
    layout_payload_sha256: str | None = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    parser_provenance: DocumentParserProvenanceInput | None = None

    @model_validator(mode="after")
    def _verified_page(self) -> DocumentSourcePage:
        explicit = any(
            value is not None
            for value in (
                self.page_identity,
                self.page_content_sha256,
                self.scan_detected,
                self.ocr_status,
                self.blocks,
                self.layout_payload_sha256,
                self.parser_provenance,
            )
        )
        if explicit and (
            self.page_identity is None
            or self.page_content_sha256 is None
            or self.scan_detected is None
            or self.ocr_status is None
            or self.blocks is None
            or self.layout_payload_sha256 is None
        ):
            raise ValueError("explicit PDF parser pages require complete provenance")
        if self.blocks is not None:
            if tuple(item.ordinal for item in self.blocks) != tuple(range(1, len(self.blocks) + 1)):
                raise ValueError("layout block order is not canonical")
            if len({item.block_id for item in self.blocks}) != len(self.blocks):
                raise ValueError("layout block identity must be unique")
            if any(
                item.bbox[2] > self.width + 1e-6 or item.bbox[3] > self.height + 1e-6
                for item in self.blocks
            ):
                raise ValueError("layout block bbox is outside its page")
            expected = canonical_sha256(
                {
                    "page_identity": self.page_identity,
                    "blocks": [item.model_dump(mode="json") for item in self.blocks],
                }
            )
            if self.layout_payload_sha256 != expected:
                raise ValueError("layout payload digest mismatch")
        elif self.layout_payload_sha256 is not None:
            raise ValueError("layout digest requires explicit blocks")
        if (
            self.ocr_status in {DocumentOcrStatus.AVAILABLE, DocumentOcrStatus.PARTIAL}
            and not self.ocr_text
        ):
            raise ValueError("available OCR status requires OCR text")
        if (
            self.ocr_status in {DocumentOcrStatus.UNAVAILABLE, DocumentOcrStatus.ERROR}
            and self.ocr_text
        ):
            raise ValueError("unavailable OCR status cannot carry OCR text")
        if self.scan_detected is False and self.ocr_status not in {
            None,
            DocumentOcrStatus.NOT_REQUIRED,
        }:
            raise ValueError("non-scanned pages cannot carry OCR status")
        if self.parser_provenance is not None and self.scan_detected is not True:
            raise ValueError("OCR parser provenance requires a scanned page")
        if self.scan_detected is True and self.parser_provenance is None:
            raise ValueError("scanned pages require parser provenance")
        if self.scan_detected is True and self.ocr_status == DocumentOcrStatus.NOT_REQUIRED:
            raise ValueError("scanned pages cannot mark OCR not required")
        if self.blocks is not None:
            expected_source = "ocr" if self.scan_detected and self.ocr_text else "native"
            if any(item.source != expected_source for item in self.blocks):
                raise ValueError("layout block source does not match page OCR state")
        if self.parser_provenance is not None and self.ocr_status is not None:
            allowed = {
                DocumentOcrStatus.AVAILABLE: {
                    DocumentParserProviderStatus.AVAILABLE,
                    DocumentParserProviderStatus.PARTIAL,
                },
                DocumentOcrStatus.PARTIAL: {DocumentParserProviderStatus.PARTIAL},
                DocumentOcrStatus.UNAVAILABLE: {
                    DocumentParserProviderStatus.UNAVAILABLE,
                    DocumentParserProviderStatus.PARTIAL,
                },
                DocumentOcrStatus.ERROR: {DocumentParserProviderStatus.ERROR},
            }.get(self.ocr_status)
            if allowed is not None and self.parser_provenance.provider_status not in allowed:
                raise ValueError("OCR status does not match parser provider status")
        return self


class DocumentSourcePayload(_FrozenInput):
    source_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,511}$")
    title: str = Field(min_length=1, max_length=500)
    version_label: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
    source_kind: DocumentSourceKind
    content: str = Field(default="", max_length=500_000)
    pages: tuple[DocumentSourcePage, ...] = ()
    authors: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    artifact_sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _source_content(self) -> DocumentSourcePayload:
        if self.source_kind in {
            DocumentSourceKind.PDF_TEXT,
            DocumentSourceKind.PDF_SCANNED,
            DocumentSourceKind.PDF_MIXED,
        }:
            if not self.pages:
                raise ValueError("PDF fixtures require explicit pages")
            numbers = tuple(item.page_number for item in self.pages)
            if numbers != tuple(range(1, len(numbers) + 1)):
                raise ValueError("PDF page membership/order is not canonical")
        elif not self.content:
            raise ValueError("non-PDF fixtures require source content")
        return self


_OPTIONAL_PAGE_FIELDS = (
    "page_identity",
    "page_content_sha256",
    "scan_detected",
    "ocr_status",
    "blocks",
    "layout_payload_sha256",
    "parser_provenance",
)


def _canonical_payload_data(payload: DocumentSourcePayload) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    for page in data["pages"]:
        for field_name in _OPTIONAL_PAGE_FIELDS:
            if page.get(field_name) is None:
                page.pop(field_name, None)
    return data


class _SafeHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "nav"}:
            self._ignored += 1
        elif not self._ignored and tag.casefold() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n" + "#" * int(tag[1]) + " ")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "nav"} and self._ignored:
            self._ignored -= 1
        elif not self._ignored and tag.casefold() in {
            "p",
            "div",
            "section",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "tr",
        }:
            self.parts.append("\n\n" if tag.casefold() == "p" else "\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)


def _identifier(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[
        :32
    ]
    return f"{prefix}-{digest}"


def _content_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_raw_text(value: str) -> str:
    decoded = value
    for _ in range(4):
        candidate = unquote(decoded)
        if candidate == decoded:
            break
        decoded = candidate
    lowered = decoded.casefold()
    if (
        _SECRET_RE.search(decoded)
        or _ABS_PATH_RE.search(decoded)
        or any(marker in lowered for marker in _ENCODED_PATH_MARKERS)
    ):
        raise DocumentAdapterError("source contains protected secret or absolute path")
    return value


def _page_uses_ocr(page: DocumentSourcePage, kind: DocumentSourceKind) -> bool:
    if page.scan_detected is not None:
        return page.scan_detected
    return kind == DocumentSourceKind.PDF_SCANNED


def _selected_page_text(page: DocumentSourcePage, kind: DocumentSourceKind) -> str:
    if _page_uses_ocr(page, kind):
        return page.ocr_text
    return page.text


def _canonical_text(payload: DocumentSourcePayload) -> tuple[str, DocumentParseQuality]:
    if payload.source_kind == DocumentSourceKind.HTML:
        parser = _SafeHTML()
        parser.feed(_safe_raw_text(payload.content))
        return "".join(parser.parts).strip(), (DocumentParseQuality.DEGRADED)
    if payload.source_kind in {
        DocumentSourceKind.PDF_TEXT,
        DocumentSourceKind.PDF_SCANNED,
        DocumentSourceKind.PDF_MIXED,
    }:
        texts = [
            _safe_raw_text(_selected_page_text(page, payload.source_kind)) for page in payload.pages
        ]
        source_text = "\n\n".join(value for value in texts if value)
        if not source_text:
            return "", DocumentParseQuality.UNAVAILABLE
        if payload.source_kind == DocumentSourceKind.PDF_TEXT:
            return source_text, DocumentParseQuality.EXACT
        explicit_statuses = [
            page.ocr_status
            for page in payload.pages
            if _page_uses_ocr(page, payload.source_kind) and page.ocr_status is not None
        ]
        if (
            payload.source_kind == DocumentSourceKind.PDF_SCANNED
            and (
                not explicit_statuses
                or all(status == DocumentOcrStatus.AVAILABLE for status in explicit_statuses)
            )
            and all(
                page.parser_provenance is None
                or page.parser_provenance.provider_status == DocumentParserProviderStatus.AVAILABLE
                for page in payload.pages
            )
        ):
            return source_text, DocumentParseQuality.OCR_DERIVED
        return source_text, DocumentParseQuality.DEGRADED
    return _safe_raw_text(payload.content), DocumentParseQuality.EXACT


def _pipe_row(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def _paragraphs(content: str) -> list[tuple[tuple[str, ...], str, int, int]]:
    lines = content.splitlines()
    path: list[str] = ["Document"]
    blocks: list[tuple[tuple[str, ...], str, int, int]] = []
    start = 1
    buffered: list[str] = []

    def flush(end_line: int) -> None:
        nonlocal buffered, start
        text = "\n".join(buffered).strip()
        if text:
            blocks.append((tuple(path), text, start, max(start, end_line)))
        buffered = []

    in_table = False
    for index, line in enumerate(lines, 1):
        heading = _HEADING_RE.match(line)
        if heading:
            flush(index - 1)
            level = len(heading.group(1))
            path[:] = path[: max(0, level - 1)]
            path.append(heading.group(2).strip())
            start = index + 1
            in_table = False
            continue
        is_table = "|" in line and (in_table or index < len(lines) and "|" in lines[index])
        in_table = is_table
        if not line.strip() or is_table:
            flush(index - 1)
            start = index + 1
        else:
            if not buffered:
                start = index
            buffered.append(line)
    flush(len(lines))
    return blocks


def _markdown_tables(content: str) -> list[tuple[int, list[list[str]], int]]:
    lines = content.splitlines()
    tables: list[tuple[int, list[list[str]], int]] = []
    index = 0
    while index + 1 < len(lines):
        if "|" not in lines[index] or not re.match(r"^\|?\s*:?-+", lines[index + 1].strip()):
            index += 1
            continue
        rows = [_pipe_row(lines[index])]
        cursor = index + 2
        while cursor < len(lines) and "|" in lines[cursor]:
            rows.append(_pipe_row(lines[cursor]))
            cursor += 1
        tables.append((len(tables) + 1, rows, index + 1))
        index = cursor
    return tables


def _typed_number(raw: str) -> tuple[float | None, float | None, str | None]:
    match = _NUMBER_RE.match(raw)
    if not match:
        return None, None, None
    center = float(match.group(1))
    spread = float(match.group(2)) if match.group(2) else None
    suffix = (match.group(3) or "").casefold()
    unit = {"%": "percent", "percent": "percent", "ms": "millisecond", "s": "second"}.get(suffix)
    return center, spread, unit


class DocumentAdapterV2:
    """Build immutable typed evidence from canonical JSON bytes only."""

    adapter_version = DOCUMENT_ADAPTER_VERSION

    def parse_bytes(
        self,
        raw: bytes,
        *,
        project_id: str,
        acl_ref: str,
        generation_id: str,
    ) -> DocumentPublication:
        try:
            decoded = raw.decode("utf-8")
            payload_data = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DocumentAdapterError("document payload must be canonical UTF-8 JSON") from error
        payload = DocumentSourcePayload.model_validate(payload_data)
        if raw != json.dumps(
            _canonical_payload_data(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"):
            raise DocumentAdapterError("document payload must use canonical JSON encoding")
        scope = DocumentScope(
            project_id=project_id,
            acl_ref=acl_ref,
            generation_id=generation_id,
        )
        source_text, parse_quality = _canonical_text(payload)
        source_sha256 = _content_digest(source_text)
        family_id = _identifier("docfam", project_id, payload.source_key)
        version_id = _identifier(
            "docver",
            family_id,
            payload.version_label,
            source_sha256,
            acl_ref,
            generation_id,
        )
        family_locator = f"document://{project_id}/{family_id}"
        version_locator = f"{family_locator}/{version_id}"
        family = DocumentFamily(
            family_id=family_id,
            scope=scope,
            source_key=payload.source_key,
            canonical_title=payload.title,
            locator=family_locator,
        )
        entities, edges = self._entities(
            payload=payload,
            source_text=source_text,
            parse_quality=parse_quality,
            family=family,
            version_id=version_id,
            version_locator=version_locator,
        )
        units = tuple(self._unit(item) for item in entities)
        version = DocumentVersion(
            version_id=version_id,
            family_id=family_id,
            scope=scope,
            version_label=payload.version_label,
            source_kind=payload.source_kind,
            content_sha256=source_sha256,
            parse_quality=parse_quality,
            parser_version=DOCUMENT_ADAPTER_VERSION,
            source_text=source_text,
            entity_ids=tuple(item.entity_id for item in entities),
            locator=version_locator,
        )
        publication_payload: dict[str, Any] = {
            "publication_id": _identifier("docpub", version_id, source_sha256, generation_id),
            "source_payload_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "family": family,
            "version": version,
            "entities": entities,
            "edges": edges,
            "retrieval_units": units,
        }
        digest = canonical_sha256(
            {
                "publication_id": publication_payload["publication_id"],
                "source_payload_sha256": publication_payload["source_payload_sha256"],
                "family": family.model_dump(mode="json"),
                "version": version.model_dump(mode="json"),
                "entities": [item.model_dump(mode="json") for item in entities],
                "edges": [item.model_dump(mode="json") for item in edges],
                "retrieval_units": [item.model_dump(mode="json") for item in units],
            }
        )
        return DocumentPublication(
            **publication_payload,
            publication_sha256=digest,
        )

    def _entities(
        self,
        *,
        payload: DocumentSourcePayload,
        source_text: str,
        parse_quality: DocumentParseQuality,
        family: DocumentFamily,
        version_id: str,
        version_locator: str,
    ) -> tuple[tuple[DocumentEntity, ...], tuple[DocumentEdge, ...]]:
        entities: list[DocumentEntity] = []
        edges: list[DocumentEdge] = []
        scope = family.scope

        def add_entity(
            *,
            entity_type: DocumentEntityType,
            stable_key: str,
            parent_id: str | None,
            ordinal: int,
            source: str = "",
            derived: str = "",
            page: int | None = None,
            authority: DocumentFactAuthority = DocumentFactAuthority.SOURCE_AUTHORED,
            status: str = "observed",
            label: str | None = None,
            **typed: Any,
        ) -> DocumentEntity:
            supplied_metadata = dict(typed.pop("metadata", {}))
            typed["metadata"] = {
                "document_title": payload.title,
                "version_label": payload.version_label,
                "source_kind": payload.source_kind.value,
                **supplied_metadata,
            }
            stable_id = _identifier(f"docstable-{entity_type.value}", family.family_id, stable_key)
            entity_id = _identifier(
                f"doc-{entity_type.value}", version_id, stable_id, source, derived, ordinal
            )
            locator = f"{version_locator}/{entity_type.value}/{entity_id}"
            item = DocumentEntity(
                entity_id=entity_id,
                stable_id=stable_id,
                entity_type=entity_type,
                family_id=family.family_id,
                version_id=version_id,
                scope=scope,
                parent_id=parent_id,
                ordinal=ordinal,
                page_number=page,
                source_text=source,
                derived_text=derived,
                content_sha256=canonical_sha256(
                    {
                        "source": source,
                        "derived": derived,
                        "typed": typed,
                        "status": status,
                    }
                ),
                locator=locator,
                authority=authority,
                status=status,
                label=label,
                **typed,
            )
            entities.append(item)
            return item

        def add_edge(
            source_id: str,
            target_id: str,
            edge_type: DocumentEdgeType,
            *,
            authority: DocumentFactAuthority = DocumentFactAuthority.PARSER_DERIVED,
            confidence: float = 1.0,
            review_status: str = "observed",
        ) -> None:
            edge_id = _identifier("docedge", version_id, source_id, target_id, edge_type.value)
            edges.append(
                DocumentEdge(
                    edge_id=edge_id,
                    version_id=version_id,
                    scope=scope,
                    source_id=source_id,
                    target_id=target_id,
                    edge_type=edge_type,
                    authority=authority,
                    confidence=confidence,
                    review_status=review_status,
                    locator=f"{version_locator}/edge/{edge_id}",
                )
            )

        page_inputs = payload.pages or (DocumentSourcePage(page_number=1, text=source_text),)
        page_ids: list[str] = []
        for page_input in page_inputs:
            page_text = _selected_page_text(page_input, payload.source_kind) or (
                source_text if not payload.pages else ""
            )
            uses_ocr = _page_uses_ocr(page_input, payload.source_kind)
            page_status = parse_quality.value
            if page_input.ocr_status is not None:
                page_status = {
                    DocumentOcrStatus.NOT_REQUIRED: DocumentParseQuality.EXACT.value,
                    DocumentOcrStatus.AVAILABLE: DocumentParseQuality.OCR_DERIVED.value,
                    DocumentOcrStatus.PARTIAL: DocumentParserProviderStatus.PARTIAL.value,
                    DocumentOcrStatus.UNAVAILABLE: DocumentParseQuality.UNAVAILABLE.value,
                    DocumentOcrStatus.ERROR: DocumentParserProviderStatus.ERROR.value,
                }[page_input.ocr_status]
            page_metadata: dict[str, Any] = {
                "width": page_input.width,
                "height": page_input.height,
                "ocr": uses_ocr and bool(page_text),
                "parser_version": DOCUMENT_ADAPTER_VERSION,
            }
            if page_input.page_identity is not None:
                page_metadata.update(
                    {
                        "page_identity": page_input.page_identity,
                        "page_content_sha256": page_input.page_content_sha256,
                        "scan_detected": page_input.scan_detected,
                        "ocr_status": (
                            page_input.ocr_status.value if page_input.ocr_status else None
                        ),
                        "layout_payload_sha256": page_input.layout_payload_sha256,
                        "parser_provenance": (
                            page_input.parser_provenance.model_dump(mode="json")
                            if page_input.parser_provenance
                            else None
                        ),
                    }
                )
            page = add_entity(
                entity_type=DocumentEntityType.PAGE,
                stable_key=f"page-{page_input.page_number}",
                parent_id=version_id,
                ordinal=page_input.page_number,
                page=page_input.page_number,
                source=page_text,
                authority=(
                    DocumentFactAuthority.PARSER_DERIVED
                    if uses_ocr
                    else DocumentFactAuthority.SOURCE_AUTHORED
                ),
                status=page_status,
                label=f"Page {page_input.page_number}",
                metadata=page_metadata,
            )
            page_ids.append(page.entity_id)
            add_edge(version_id, page.entity_id, DocumentEdgeType.HAS_PAGE)
            if page_input.blocks is None:
                # Legacy programmatic fixtures predate explicit layout payloads.
                block = add_entity(
                    entity_type=DocumentEntityType.LAYOUT_BLOCK,
                    stable_key=f"page-{page_input.page_number}-block-1",
                    parent_id=page.entity_id,
                    ordinal=1,
                    page=page_input.page_number,
                    source=page_text,
                    authority=DocumentFactAuthority.PARSER_DERIVED,
                    status=parse_quality.value,
                    metadata={
                        "bbox": [0.0, 0.0, page_input.width, page_input.height],
                        "parser_version": DOCUMENT_ADAPTER_VERSION,
                    },
                )
                add_edge(page.entity_id, block.entity_id, DocumentEdgeType.HAS_BLOCK)
            else:
                for source_block in page_input.blocks:
                    block = add_entity(
                        entity_type=DocumentEntityType.LAYOUT_BLOCK,
                        stable_key=(f"page-{page_input.page_number}-block-{source_block.block_id}"),
                        parent_id=page.entity_id,
                        ordinal=source_block.ordinal,
                        page=page_input.page_number,
                        source=source_block.text,
                        authority=DocumentFactAuthority.PARSER_DERIVED,
                        status=page_status,
                        label=(
                            source_block.text[:160]
                            if source_block.block_type == DocumentLayoutBlockType.HEADER
                            else None
                        ),
                        metadata={
                            "block_id": source_block.block_id,
                            "block_type": source_block.block_type.value,
                            "bbox": list(source_block.bbox),
                            "bbox_space": "pdf-points-top-left",
                            "bbox_confidence": source_block.confidence,
                            "text_sha256": source_block.text_sha256,
                            "text_source": source_block.source,
                            "layout_parser_name": source_block.parser_name,
                            "layout_parser_version": source_block.parser_version,
                            "bbox_method": source_block.bbox_method,
                            "page_identity": page_input.page_identity,
                            "layout_payload_sha256": page_input.layout_payload_sha256,
                            "parser_version": DOCUMENT_ADAPTER_VERSION,
                        },
                    )
                    add_edge(page.entity_id, block.entity_id, DocumentEdgeType.HAS_BLOCK)

        paragraphs = _paragraphs(source_text)
        section_ids: dict[tuple[str, ...], str] = {}
        previous_paragraph: str | None = None
        for ordinal, (path, paragraph_text, start_line, end_line) in enumerate(paragraphs, 1):
            if path not in section_ids:
                section = add_entity(
                    entity_type=DocumentEntityType.SECTION,
                    stable_key=f"section:{'/'.join(path)}:{_content_digest(paragraph_text)[:24]}",
                    parent_id=version_id,
                    ordinal=len(section_ids) + 1,
                    source=path[-1],
                    derived=" > ".join(path),
                    authority=DocumentFactAuthority.PARSER_DERIVED,
                    status=parse_quality.value,
                    label=path[-1],
                    metadata={
                        "heading_path": list(path),
                        "parser_version": DOCUMENT_ADAPTER_VERSION,
                    },
                )
                section_ids[path] = section.entity_id
                add_edge(version_id, section.entity_id, DocumentEdgeType.HAS_SECTION)
                summary = add_entity(
                    entity_type=DocumentEntityType.SECTION_SUMMARY,
                    stable_key=f"summary:{section.stable_id}",
                    parent_id=section.entity_id,
                    ordinal=section.ordinal,
                    source="",
                    derived=paragraph_text[:500],
                    authority=DocumentFactAuthority.MODEL_DERIVED,
                    status="unreviewed",
                    label=path[-1],
                    metadata={
                        "derivation_version": "deterministic-extractive-summary-v1",
                        "input_sha256": _content_digest(paragraph_text),
                    },
                )
                add_edge(section.entity_id, summary.entity_id, DocumentEdgeType.HAS_BLOCK)
            section_id = section_ids[path]
            paragraph = add_entity(
                entity_type=DocumentEntityType.PARAGRAPH,
                stable_key=(f"paragraph:{'/'.join(path)}:{_content_digest(paragraph_text)[:32]}"),
                parent_id=section_id,
                ordinal=ordinal,
                source=paragraph_text,
                status=parse_quality.value,
                metadata={
                    "heading_path": list(path),
                    "start_line": start_line,
                    "end_line": end_line,
                    "parser_version": DOCUMENT_ADAPTER_VERSION,
                },
            )
            add_edge(section_id, paragraph.entity_id, DocumentEdgeType.HAS_PARAGRAPH)
            if previous_paragraph:
                add_edge(previous_paragraph, paragraph.entity_id, DocumentEdgeType.NEIGHBOR)
            previous_paragraph = paragraph.entity_id
            claim = _CLAIM_RE.match(paragraph_text)
            if claim:
                claim_text = claim.group(1).strip()
                candidate = add_entity(
                    entity_type=DocumentEntityType.CLAIM_CANDIDATE,
                    stable_key=f"claim:{_content_digest(claim_text)}",
                    parent_id=paragraph.entity_id,
                    ordinal=ordinal,
                    source=claim_text,
                    authority=DocumentFactAuthority.PARSER_DERIVED,
                    status="candidate",
                    metadata={
                        "source_span_sha256": _content_digest(paragraph_text),
                        "qualifier": "reported_in_source",
                        "extraction_version": "atomic-claim-parser-v2",
                        "extraction_confidence": 1.0,
                    },
                )
                add_edge(paragraph.entity_id, candidate.entity_id, DocumentEdgeType.REPORTS)

        for table_ordinal, rows, line in _markdown_tables(source_text):
            if len(rows) < 2 or len(rows[0]) < 2:
                continue
            header = rows[0]
            table = add_entity(
                entity_type=DocumentEntityType.TABLE,
                stable_key=f"table:{table_ordinal}:{_content_digest(str(rows))[:24]}",
                parent_id=version_id,
                ordinal=table_ordinal,
                source="\n".join(" | ".join(row) for row in rows),
                label=f"Table {table_ordinal}",
                metadata={
                    "headers": header,
                    "source_line": line,
                    "parser_version": DOCUMENT_ADAPTER_VERSION,
                },
            )
            add_edge(version_id, table.entity_id, DocumentEdgeType.HAS_TABLE)
            for row_index, row_values in enumerate(rows[1:], 1):
                padded = [*row_values, *("" for _ in range(max(0, len(header) - len(row_values))))]
                key_columns = {
                    "model",
                    "variant",
                    "dataset",
                    "split",
                    "seed",
                    "run",
                    "configuration",
                }
                row_path = tuple(
                    f"{column}={padded[index]}"
                    for index, column in enumerate(header)
                    if index < len(padded)
                    and (index == 0 or column.casefold() in key_columns)
                    and padded[index]
                )
                row = add_entity(
                    entity_type=DocumentEntityType.TABLE_ROW,
                    stable_key=f"{table.stable_id}:row:{'/'.join(row_path)}",
                    parent_id=table.entity_id,
                    ordinal=row_index,
                    source=" | ".join(padded),
                    label=padded[0],
                    row_path=row_path,
                    metadata={"header": header, "parser_version": DOCUMENT_ADAPTER_VERSION},
                )
                add_edge(table.entity_id, row.entity_id, DocumentEdgeType.HAS_ROW)
                row_footnotes = tuple(
                    marker
                    for marker in ("*", "†", "‡")
                    if any(
                        column.casefold() in {"footnote", "footnotes", "note", "notes"}
                        and index < len(padded)
                        and marker in padded[index]
                        for index, column in enumerate(header)
                    )
                )
                for column_index, raw_value in enumerate(padded[1:], 1):
                    column_name = (
                        header[column_index]
                        if column_index < len(header)
                        else (f"Column {column_index}")
                    )
                    center, spread, unit = _typed_number(raw_value)
                    footnotes = tuple(
                        marker
                        for marker in ("*", "†", "‡")
                        if marker in raw_value or marker in row_footnotes
                    )
                    cell = add_entity(
                        entity_type=DocumentEntityType.TABLE_CELL_FACT,
                        stable_key=(f"{table.stable_id}:{'/'.join(row_path)}:{column_name}"),
                        parent_id=row.entity_id,
                        ordinal=column_index,
                        source=raw_value,
                        label=f"{padded[0]} / {column_name}",
                        row_path=row_path,
                        column_path=(column_name,),
                        raw_value=raw_value,
                        numeric_center=center,
                        numeric_spread=spread,
                        unit=unit,
                        footnotes=footnotes,
                        metadata={
                            "row_index": row_index,
                            "column_index": column_index,
                            "header_path": [column_name],
                            "metric_confirmed": False,
                            "parser_version": DOCUMENT_ADAPTER_VERSION,
                        },
                    )
                    add_edge(row.entity_id, cell.entity_id, DocumentEdgeType.HAS_CELL_FACT)

        references: dict[str, DocumentEntity] = {}
        for line_index, line in enumerate(source_text.splitlines(), 1):
            reference = _REFERENCE_RE.match(line.strip())
            if reference:
                marker, title, doi = reference.groups()
                work = add_entity(
                    entity_type=DocumentEntityType.REFERENCE_WORK,
                    stable_key=f"reference:{doi or title}",
                    parent_id=version_id,
                    ordinal=line_index,
                    source=title,
                    label=marker,
                    metadata={
                        "title": title,
                        "doi": doi,
                        "resolution_version": "local-reference-resolver-v2",
                    },
                )
                references[marker] = work
            figure = _FIGURE_RE.match(line.strip())
            if figure:
                label, caption = figure.groups()
                item = add_entity(
                    entity_type=DocumentEntityType.FIGURE,
                    stable_key=f"figure:{label}",
                    parent_id=version_id,
                    ordinal=line_index,
                    source=caption,
                    label=f"Figure {label}",
                    authority=(
                        DocumentFactAuthority.SOURCE_AUTHORED
                        if caption
                        else DocumentFactAuthority.PARSER_DERIVED
                    ),
                    status="captioned" if caption else "caption_missing",
                    metadata={
                        "artifact_sha256": payload.artifact_sha256,
                        "description_kind": "author_caption" if caption else "missing",
                        "parser_version": DOCUMENT_ADAPTER_VERSION,
                    },
                )
                add_edge(version_id, item.entity_id, DocumentEdgeType.HAS_FIGURE)
            formula = _FORMULA_RE.match(line.strip())
            if formula:
                label, equation = formula.groups()
                item = add_entity(
                    entity_type=DocumentEntityType.FORMULA,
                    stable_key=f"formula:{label}",
                    parent_id=version_id,
                    ordinal=line_index,
                    source=equation,
                    label=f"Equation {label}",
                    metadata={
                        "latex_or_raw": equation,
                        "parser_confidence": 1.0,
                        "parser_version": DOCUMENT_ADAPTER_VERSION,
                    },
                )
                add_edge(version_id, item.entity_id, DocumentEdgeType.HAS_FORMULA)

        citation_ordinal = 0
        for paragraph in tuple(
            item for item in entities if item.entity_type == DocumentEntityType.PARAGRAPH
        ):
            for marker in _CITATION_RE.findall(paragraph.source_text):
                target = references.get(marker)
                if target is None:
                    continue
                citation_ordinal += 1
                citation = add_entity(
                    entity_type=DocumentEntityType.CITATION_MENTION,
                    stable_key=(f"citation:{paragraph.stable_id}:{marker}:{citation_ordinal}"),
                    parent_id=paragraph.entity_id,
                    ordinal=citation_ordinal,
                    source=paragraph.source_text,
                    label=f"[{marker}]",
                    target_id=target.entity_id,
                    resolution_confidence=1.0,
                    metadata={
                        "intent": "unreviewed",
                        "resolver_version": "local-reference-resolver-v2",
                    },
                )
                add_edge(paragraph.entity_id, citation.entity_id, DocumentEdgeType.CITES)
                add_edge(citation.entity_id, target.entity_id, DocumentEdgeType.RESOLVES_TO)

        return tuple(entities), tuple(edges)

    @staticmethod
    def _unit(entity: DocumentEntity) -> DocumentRetrievalUnit:
        keys = tuple(
            sorted(
                {
                    value
                    for value in (
                        entity.label,
                        *entity.row_path,
                        *entity.column_path,
                        entity.raw_value,
                        str(entity.metadata.get("doi") or ""),
                    )
                    if value
                }
            )
        )
        source_text = " ".join(
            value
            for value in (
                str(entity.metadata.get("document_title") or ""),
                str(entity.metadata.get("version_label") or ""),
                entity.label or "",
                entity.source_text,
                " ".join(entity.row_path),
                " ".join(entity.column_path),
                entity.raw_value or "",
            )
            if value
        )
        derived_text = " ".join(
            value
            for value in (
                entity.derived_text,
                str(entity.metadata.get("heading_path") or ""),
                str(entity.metadata.get("qualifier") or ""),
            )
            if value
        )
        return DocumentRetrievalUnit(
            unit_id=_identifier(
                "docunit",
                entity.version_id,
                entity.entity_id,
                DOCUMENT_UNIT_BUILDER_VERSION,
            ),
            version_id=entity.version_id,
            scope=entity.scope,
            entity_id=entity.entity_id,
            entity_type=entity.entity_type,
            exact_keys=keys,
            sparse_text=source_text,
            dense_source_text=source_text,
            dense_derived_text=derived_text,
            source_text_sha256=_content_digest(source_text),
            derived_text_sha256=_content_digest(derived_text),
            builder_version=DOCUMENT_UNIT_BUILDER_VERSION,
            locator=entity.locator,
        )


def canonical_document_source_bytes(payload: DocumentSourcePayload) -> bytes:
    return json.dumps(
        _canonical_payload_data(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
