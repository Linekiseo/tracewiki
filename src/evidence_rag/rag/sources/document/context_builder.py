"""Task-specific Document comprehension context with stable citations."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from .contracts import canonical_sha256
from .retriever import DocumentCandidate, DocumentQueryTask, DocumentSearchResult

DOCUMENT_CONTEXT_BUILDER_VERSION = "document-context-builder-v2"
_SECRET_RE = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*[^\s,;]+")
_ABS_RE = re.compile(
    r"(?i)(?:/(?:Users|home|private|tmp|var|etc|opt)/|[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])"
)


class DocumentContextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    citation_id: str
    entity_id: str
    entity_type: str
    locator: str
    authority: str
    status: str
    roles: tuple[str, ...]
    heading: str
    body: str
    content_sha256: str


class DocumentContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: str = DOCUMENT_CONTEXT_BUILDER_VERSION
    task: DocumentQueryTask
    availability: str
    blocks: tuple[DocumentContextBlock, ...]
    citation_map: dict[str, str]
    missing: tuple[str, ...]
    truncated: bool
    character_count: int
    context_sha256: str
    reasoning_included: bool = False


def _safe(value: str) -> str:
    if _SECRET_RE.search(value) or _ABS_RE.search(value):
        raise ValueError("document context contains protected secret or absolute path")
    return value


def _heading(candidate: DocumentCandidate) -> str:
    label = str(candidate.metadata.get("label") or candidate.entity_type.value)
    if candidate.entity_type.value == "table_cell_fact":
        row = " > ".join(candidate.metadata.get("row_path") or ())
        column = " > ".join(candidate.metadata.get("column_path") or ())
        return f"TABLE · {row} · {column}"
    return f"{candidate.entity_type.value.upper()} · {label}"


def _body(candidate: DocumentCandidate, task: DocumentQueryTask) -> str:
    source = _safe(candidate.source_text.strip())
    if task == DocumentQueryTask.TABLE:
        return "\n".join(
            (
                f"Raw value: {candidate.metadata.get('raw_value') or source}",
                "Typed value: "
                f"center={candidate.metadata.get('numeric_center')}, "
                f"spread={candidate.metadata.get('numeric_spread')}, "
                f"unit={candidate.metadata.get('unit')}",
                f"Row path: {' > '.join(candidate.metadata.get('row_path') or ())}",
                f"Column path: {' > '.join(candidate.metadata.get('column_path') or ())}",
                f"Footnotes: {', '.join(candidate.metadata.get('footnotes') or ()) or 'none'}",
                "Metric relation: unconfirmed",
            )
        )
    if task == DocumentQueryTask.CLAIM:
        return "\n".join(
            (
                f"Atomic claim: {source}",
                f"Review status: {candidate.status}",
                f"Authority: {candidate.authority}",
                "Support/refute/qualify: missing unless a reviewed edge is present",
            )
        )
    if task == DocumentQueryTask.CITATION:
        return "\n".join(
            (
                f"Source context: {source}",
                f"Resolved target: {candidate.metadata.get('target_id') or 'reference entity'}",
                f"Resolution confidence: {candidate.metadata.get('resolution_confidence') or 'n/a'}",
                "Citation intent: unreviewed",
            )
        )
    if task == DocumentQueryTask.FIGURE_FORMULA:
        return "\n".join(
            (
                f"Author text: {source or 'missing'}",
                f"Description kind: {candidate.metadata.get('description_kind') or 'source'}",
                f"Parse status: {candidate.status}",
            )
        )
    if task == DocumentQueryTask.VERSION:
        return "\n".join(
            (
                f"Versioned source: {source}",
                f"Version entity: {candidate.version_id}",
                f"Status: {candidate.status}",
            )
        )
    return "\n".join(
        (
            f"Source type: {candidate.authority}",
            f"Matched source: {source or _safe(candidate.derived_text)}",
            f"Parent: {candidate.parent_id or 'document version'}",
        )
    )


def build_document_context(
    result: DocumentSearchResult,
    *,
    max_characters: int = 8_000,
    max_block_characters: int = 1_600,
) -> DocumentContext:
    if max_characters < 256 or max_block_characters < 128:
        raise ValueError("document context budget is too small")
    blocks: list[DocumentContextBlock] = []
    used = 0
    truncated = False
    for candidate in result.candidates:
        heading = _heading(candidate)
        body = _body(candidate, result.profile.task)
        if len(body) > max_block_characters:
            cut = body.rfind("\n", 0, max_block_characters - 1)
            if cut < max_block_characters // 3:
                cut = max_block_characters - 1
            body = body[:cut].rstrip() + "…"
            truncated = True
        citation_id = f"DOC-{len(blocks) + 1:03d}"
        block_size = len(heading) + len(body) + len(candidate.locator) + 8
        if used + block_size > max_characters:
            truncated = True
            break
        block_payload = {
            "citation_id": citation_id,
            "entity_id": candidate.entity_id,
            "entity_type": candidate.entity_type.value,
            "locator": candidate.locator,
            "authority": candidate.authority.value,
            "status": candidate.status,
            "roles": candidate.roles,
            "heading": heading,
            "body": body,
        }
        blocks.append(
            DocumentContextBlock(
                **block_payload,
                content_sha256=canonical_sha256(block_payload),
            )
        )
        used += block_size
    missing = []
    if not blocks:
        missing.append("authorized_document_evidence")
    if result.profile.task == DocumentQueryTask.CLAIM and not any(
        item.entity_type == "claim" for item in blocks
    ):
        missing.append("reviewed_claim")
    if result.profile.task == DocumentQueryTask.TABLE and not any(
        item.entity_type == "table_cell_fact" for item in blocks
    ):
        missing.append("typed_table_cell")
    availability = (
        "UNAVAILABLE" if not blocks else "PROVISIONAL" if missing or truncated else "AVAILABLE"
    )
    citation_map = {item.citation_id: item.locator for item in blocks}
    payload: dict[str, Any] = {
        "schema_version": DOCUMENT_CONTEXT_BUILDER_VERSION,
        "task": result.profile.task,
        "availability": availability,
        "blocks": [item.model_dump(mode="json") for item in blocks],
        "citation_map": citation_map,
        "missing": tuple(sorted(set(missing))),
        "truncated": truncated,
        "character_count": used,
        "reasoning_included": False,
    }
    return DocumentContext(
        **payload,
        context_sha256=canonical_sha256(payload),
    )
