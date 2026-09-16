"""Unified, deterministic context contract for all first-party RAG sources."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field

from ..security import redact_secrets

MULTISOURCE_CONTEXT_VERSION = "multisource-context-v1"
SOURCE_FAMILIES = (
    "code",
    "codex",
    "experiment",
    "notebook",
    "document",
    "workspace",
)

_WINDOWS_ABSOLUTE = re.compile(r"(?i)^[a-z]:[\\/]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UnifiedEvidenceBlock(_FrozenModel):
    citation_id: str
    source: Literal["code", "codex", "experiment", "notebook", "document", "workspace"]
    entity_id: str
    entity_type: str
    title: str
    snippet: str
    locator: str
    version: str | None = None
    score: float = Field(ge=0.0)
    channels: tuple[str, ...]
    content_digest: str
    truncated: bool = False
    redactions: tuple[str, ...] = ()


class MultiSourceContext(_FrozenModel):
    schema_version: Literal["multisource-context-v1"] = MULTISOURCE_CONTEXT_VERSION
    project_id: str
    blocks: tuple[UnifiedEvidenceBlock, ...]
    citation_map: dict[str, dict[str, Any]]
    source_counts: dict[str, int]
    missing_sources: tuple[str, ...]
    rendered: str
    char_count: int
    budget_chars: int
    content_digest: str


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _decoded_variants(value: str) -> tuple[str, ...]:
    variants = [value]
    current = value
    for _ in range(3):
        decoded = unquote(current)
        if decoded == current:
            break
        variants.append(decoded)
        current = decoded
    return tuple(variants)


def portable_locator(locator: Any, entity_id: str) -> str:
    raw = str(locator or "").strip()
    _redacted, secret_findings = redact_secrets(raw)
    unsafe = not raw or bool(_CONTROL.search(raw)) or bool(secret_findings)
    for candidate in _decoded_variants(raw):
        normalized = candidate.replace("\\", "/")
        if (
            normalized.startswith("/")
            or normalized.startswith("//")
            or _WINDOWS_ABSOLUTE.match(candidate)
            or candidate.casefold().startswith("file:")
            or "/../" in f"/{normalized}/"
        ):
            unsafe = True
            break
    if unsafe:
        return "entity://" + _digest(entity_id).split(":", 1)[1]
    return raw[:2_000]


def _bounded_text(value: Any, limit: int) -> tuple[str, bool, tuple[str, ...]]:
    raw = str(value or "")
    redacted, findings = redact_secrets(raw)
    cleaned = _CONTROL.sub(" ", redacted).strip()
    if len(cleaned) <= limit:
        return cleaned, False, tuple(sorted(findings))
    if limit <= 1:
        return cleaned[:limit], True, tuple(sorted(findings))
    return cleaned[: limit - 1].rstrip() + "…", True, tuple(sorted(findings))


def build_multisource_context(
    *,
    project_id: str,
    results: list[dict[str, Any]],
    requested_sources: list[str] | None = None,
    budget_chars: int = 12_000,
    per_block_chars: int = 1_200,
) -> MultiSourceContext:
    budget = min(100_000, max(1_000, int(budget_chars)))
    per_block = min(8_000, max(200, int(per_block_chars)))
    seen: set[tuple[str, str, str, str]] = set()
    blocks: list[UnifiedEvidenceBlock] = []
    rendered_parts: list[str] = []
    citation_map: dict[str, dict[str, Any]] = {}
    remaining = budget

    for item in results:
        source = str(item.get("source") or "")
        if source not in SOURCE_FAMILIES:
            continue
        entity_id = str(item.get("entity_id") or "")
        if not entity_id:
            continue
        locator = portable_locator(item.get("locator"), entity_id)
        version = str(item["version"]) if item.get("version") is not None else None
        identity = (source, entity_id, locator, version or "")
        if identity in seen:
            continue
        seen.add(identity)
        title, title_truncated, title_redactions = _bounded_text(item.get("title"), 240)
        snippet, snippet_truncated, snippet_redactions = _bounded_text(
            item.get("snippet"), per_block
        )
        citation_id = f"E{len(blocks) + 1}"
        rendered = f"[{citation_id}] {source}/{item.get('entity_type') or 'Entity'} {title}\n"
        if snippet:
            rendered += snippet + "\n"
        rendered += f"locator: {locator}"
        if len(rendered) + (2 if rendered_parts else 0) > remaining:
            available = remaining - (2 if rendered_parts else 0)
            if available < 160:
                break
            rendered, forced, extra_redactions = _bounded_text(rendered, available)
            snippet_truncated = snippet_truncated or forced
            snippet_redactions = tuple(sorted(set(snippet_redactions) | set(extra_redactions)))
        content_digest = _digest(
            "\x1f".join(
                (
                    source,
                    entity_id,
                    str(item.get("entity_type") or "Entity"),
                    title,
                    snippet,
                    locator,
                    version or "",
                )
            )
        )
        block = UnifiedEvidenceBlock(
            citation_id=citation_id,
            source=source,
            entity_id=entity_id,
            entity_type=str(item.get("entity_type") or "Entity"),
            title=title,
            snippet=snippet,
            locator=locator,
            version=version,
            score=max(0.0, float(item.get("score") or 0.0)),
            channels=tuple(sorted(set(item.get("channels") or []))),
            content_digest=content_digest,
            truncated=title_truncated or snippet_truncated,
            redactions=tuple(sorted(set(title_redactions) | set(snippet_redactions))),
        )
        blocks.append(block)
        rendered_parts.append(rendered)
        citation_map[citation_id] = {
            "entity_id": entity_id,
            "source": source,
            "locator": locator,
            "version": version,
            "content_digest": content_digest,
        }
        remaining = budget - len("\n\n".join(rendered_parts))
        if remaining < 160:
            break

    rendered_context = "\n\n".join(rendered_parts)
    counts = Counter(block.source for block in blocks)
    expected = tuple(dict.fromkeys(requested_sources if requested_sources else SOURCE_FAMILIES))
    missing = tuple(source for source in expected if counts.get(source, 0) == 0)
    digest_payload = "\x1e".join(
        (project_id, *(block.content_digest for block in blocks), rendered_context)
    )
    return MultiSourceContext(
        project_id=project_id,
        blocks=tuple(blocks),
        citation_map=citation_map,
        source_counts={source: counts[source] for source in SOURCE_FAMILIES if counts[source]},
        missing_sources=missing,
        rendered=rendered_context,
        char_count=len(rendered_context),
        budget_chars=budget,
        content_digest=_digest(digest_payload),
    )


__all__ = [
    "MULTISOURCE_CONTEXT_VERSION",
    "SOURCE_FAMILIES",
    "MultiSourceContext",
    "UnifiedEvidenceBlock",
    "build_multisource_context",
    "portable_locator",
]
