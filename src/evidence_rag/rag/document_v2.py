"""Read-only typed Document retrieval and evidence context projection."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any, Literal

from ..documents.store import DocumentStore
from ..security import redact_secrets
from .multisource import portable_locator

DOCUMENT_ENGINE_HEADER = "X-RAG-Document-Engine"
DOCUMENT_V2_VERSION = "document-typed-retrieval-v2"
DOCUMENT_CONTEXT_VERSION = "document-evidence-context-v2"

_ENGINE_VALUES = frozenset({"v1", "v2"})
_TOKEN_RE = re.compile(r"[\w@./:%+-]+", re.UNICODE)
_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ABS_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'`(])(?:/(?:Users|home|private|tmp|var|etc|opt)/|[a-z]:[\\/]|"
    r"\\\\[^\\/\s]+[\\/][^\\/\s]+)"
)
_STOP = frozenset(
    {
        "a",
        "and",
        "claim",
        "document",
        "for",
        "in",
        "of",
        "show",
        "table",
        "the",
        "to",
        "文档",
        "表格",
        "论断",
    }
)


class DocumentEngineOverrideError(ValueError):
    """Raised for an unsupported Document engine override."""


def validate_document_engine_override(value: str | None) -> Literal["v1", "v2"]:
    normalized = (value or "v1").strip().lower()
    if normalized not in _ENGINE_VALUES:
        raise DocumentEngineOverrideError(f"invalid {DOCUMENT_ENGINE_HEADER}; expected v1 or v2")
    return normalized  # type: ignore[return-value]


def _digest(*values: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _tokens(value: Any) -> tuple[str, ...]:
    return tuple(
        token
        for token in (match.casefold() for match in _TOKEN_RE.findall(str(value or "")))
        if len(token) > 1 and token not in _STOP
    )


def _safe(value: Any, limit: int = 1_400) -> tuple[str, tuple[str, ...]]:
    raw = str(value or "")
    redacted, findings = redact_secrets(raw)
    if _ABS_PATH_RE.search(redacted):
        redacted = _ABS_PATH_RE.sub(" [REDACTED_PATH] ", redacted)
        findings = [*findings, "absolute_path"]
    cleaned = _CONTROL_RE.sub(" ", redacted).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: max(1, limit - 1)].rstrip() + "…"
    return cleaned, tuple(sorted(set(findings)))


def _task(query: str) -> str:
    lowered = query.casefold()
    for task, markers in (
        ("conflict", ("conflict", "contradict", "refute", "冲突", "矛盾")),
        ("numeric", ("table", "value", "metric", "%", "表格", "数值", "指标")),
        ("citation", ("citation", "reference", "source", "引用", "来源")),
        ("figure", ("figure", "chart", "image", "图", "图表")),
        ("claim", ("claim", "conclusion", "finding", "论断", "结论")),
    ):
        if any(marker in lowered for marker in markers):
            return task
    return "locate"


class DocumentStructuredRetrieverV2:
    """Retrieve typed document units with parent-aware deterministic reranking."""

    def __init__(self, store: DocumentStore) -> None:
        self.store = store

    def search(
        self,
        *,
        project_id: str,
        query: str,
        document_ids: list[str] | None = None,
        allowed_acl_refs: list[str] | None = None,
        enforce_acl: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        task = _task(query)
        query_tokens = set(_tokens(query))
        allowed = set(allowed_acl_refs or ()) | {"public"}
        requested = set(document_ids or ())
        units: list[dict[str, Any]] = []
        denied = 0
        generations: set[str] = set()
        documents: dict[str, dict[str, Any]] = {}
        for summary in self.store.list_documents(project_id):
            document_id = str(summary["id"])
            if requested and document_id not in requested:
                continue
            document = self.store.get_document(document_id)
            if document is None:
                continue
            acl_ref = str(document.get("acl_ref") or f"project:{project_id}")
            if enforce_acl and acl_ref not in allowed:
                denied += 1
                continue
            documents[document_id] = document
            generations.add(str(document.get("content_hash") or ""))
            units.extend(self._document_units(document, task, query_tokens))
        candidates = [item for item in units if item["score"] > 0 or not query_tokens]
        candidates.sort(
            key=lambda item: (
                -float(item["score"]),
                -int(item["entity_type"] == "Claim"),
                str(item["entity_id"]),
            )
        )
        selected = candidates[: min(100, max(1, limit))]
        selected_documents = {str(item["metadata"]["document_id"]) for item in selected}
        parent_documents = [
            self._parent_summary(documents[document_id])
            for document_id in sorted(selected_documents)
            if document_id in documents
        ]
        conflict_claims = [
            {
                "claim_id": item["entity_id"],
                "status": item["status"],
                "locator": item["locator"],
                "evidence_count": item["metadata"].get("evidence_count", 0),
            }
            for item in selected
            if item["entity_type"] == "Claim"
            and item["status"] in {"contradicted", "partially_supported", "potentially_stale"}
        ]
        context = {
            "schema_version": DOCUMENT_CONTEXT_VERSION,
            "task": task,
            "blocks": [
                {
                    "entity_id": item["entity_id"],
                    "entity_type": item["entity_type"],
                    "document_id": item["metadata"]["document_id"],
                    "parent_id": item["metadata"].get("parent_id"),
                    "locator": item["locator"],
                    "roles": item["roles"],
                    "status": item["status"],
                    "content_digest": item["content_digest"],
                    "text": item["snippet"],
                }
                for item in selected
            ],
            "parents": parent_documents,
            "conflicts": conflict_claims,
            "missing": [] if selected else ["authorized_document_evidence"],
            "reasoning_included": False,
        }
        return {
            "results": selected,
            "context": context,
            "trace": {
                "engine": DOCUMENT_V2_VERSION,
                "task": task,
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "denied_documents": denied,
                "index_generation": sorted(value for value in generations if value),
                "parent_expansion": len(parent_documents),
                "release": {
                    "decision": "HOLD_DEFAULT_V1",
                    "default_engine": "v1",
                    "quality_qualified": False,
                    "rollback": f"omit {DOCUMENT_ENGINE_HEADER} or set it to v1",
                },
                "reasoning_included": False,
            },
        }

    def _document_units(
        self, document: dict[str, Any], task: str, query_tokens: set[str]
    ) -> list[dict[str, Any]]:
        document_id = str(document["id"])
        version = str(document.get("version") or "")
        units = [
            self._unit(
                entity_id=document_id,
                entity_type="ScientificDocument",
                title=str(document.get("title") or document_id),
                text=" ".join(
                    (
                        str(document.get("title") or ""),
                        " ".join(document.get("authors") or []),
                        " ".join(document.get("tags") or []),
                    )
                ),
                locator=f"document://{document_id}",
                version=version,
                status=str(document.get("status") or "observed"),
                roles=("document",),
                task=task,
                query_tokens=query_tokens,
                metadata={"document_id": document_id, "parent_id": None},
            )
        ]
        section_by_id = {str(item["id"]): item for item in document.get("sections", [])}
        for section in document.get("sections", []):
            units.append(
                self._unit(
                    entity_id=str(section["id"]),
                    entity_type="DocumentSection",
                    title=str(section.get("heading") or f"Section {section.get('ordinal', 0)}"),
                    text=str(section.get("content") or ""),
                    locator=str(
                        section.get("source_locator")
                        or f"document://{document_id}/section/{section.get('ordinal', 0)}"
                    ),
                    version=version,
                    status="observed",
                    roles=("section", "narrative"),
                    task=task,
                    query_tokens=query_tokens,
                    metadata={"document_id": document_id, "parent_id": document_id},
                )
            )
        for claim in document.get("claims", []):
            evidence_count = len(claim.get("evidence") or ())
            parent_id = str(claim.get("section_id") or document_id)
            parent_heading = str(section_by_id.get(parent_id, {}).get("heading") or "")
            units.append(
                self._unit(
                    entity_id=str(claim["id"]),
                    entity_type="Claim",
                    title=str(claim.get("display_key") or "Claim"),
                    text=f"{parent_heading} {claim.get('content') or ''}",
                    locator=str(
                        claim.get("source_locator")
                        or f"document://{document_id}/claim/{claim['id']}"
                    ),
                    version=version,
                    status=str(claim.get("status") or "reported"),
                    roles=("claim", str(claim.get("claim_type") or "other")),
                    task=task,
                    query_tokens=query_tokens,
                    metadata={
                        "document_id": document_id,
                        "parent_id": parent_id,
                        "claim_type": claim.get("claim_type"),
                        "evidence_count": evidence_count,
                        "extraction_method": claim.get("extraction_method"),
                    },
                )
            )
        for table in document.get("tables", []):
            table_id = str(table["id"])
            units.append(
                self._unit(
                    entity_id=table_id,
                    entity_type="DocumentTable",
                    title=str(table.get("title") or f"Table {table.get('ordinal', 0)}"),
                    text=f"{table.get('title') or ''} {table.get('caption') or ''}",
                    locator=str(
                        table.get("source_locator")
                        or f"document://{document_id}/table/{table.get('ordinal', 0)}"
                    ),
                    version=version,
                    status="observed",
                    roles=("table",),
                    task=task,
                    query_tokens=query_tokens,
                    metadata={"document_id": document_id, "parent_id": document_id},
                )
            )
            for cell in table.get("cells", []):
                value = str(cell.get("value") or "")
                units.append(
                    self._unit(
                        entity_id=str(cell["id"]),
                        entity_type="DocumentTableCell",
                        title=f"Cell {cell.get('row_index', 0)},{cell.get('column_index', 0)}",
                        text=value,
                        locator=str(
                            cell.get("source_locator")
                            or (
                                f"document://{document_id}/table/{table_id}/cell/"
                                f"{cell.get('row_index', 0)},{cell.get('column_index', 0)}"
                            )
                        ),
                        version=version,
                        status="observed",
                        roles=("table", "numeric") if _NUMBER_RE.search(value) else ("table",),
                        task=task,
                        query_tokens=query_tokens,
                        metadata={
                            "document_id": document_id,
                            "parent_id": table_id,
                            "row": cell.get("row_index"),
                            "column": cell.get("column_index"),
                            "numeric_values": _NUMBER_RE.findall(value)[:8],
                        },
                    )
                )
        for entity_type, items, role in (
            ("DocumentFigure", document.get("figures", []), "figure"),
            ("DocumentCitation", document.get("citations", []), "citation"),
            ("DocumentPage", document.get("pages", []), "page"),
        ):
            for item in items:
                text = " ".join(
                    str(item.get(field) or "") for field in ("caption", "context", "target", "text")
                )
                units.append(
                    self._unit(
                        entity_id=str(item["id"]),
                        entity_type=entity_type,
                        title=str(
                            item.get("marker")
                            or item.get("caption")
                            or f"{role.title()} {item.get('ordinal', item.get('page_number', 0))}"
                        ),
                        text=text,
                        locator=str(
                            item.get("source_locator")
                            or f"document://{document_id}/{role}/{item['id']}"
                        ),
                        version=version,
                        status="observed",
                        roles=(role,),
                        task=task,
                        query_tokens=query_tokens,
                        metadata={"document_id": document_id, "parent_id": document_id},
                    )
                )
        return units

    @staticmethod
    def _unit(
        *,
        entity_id: str,
        entity_type: str,
        title: str,
        text: str,
        locator: str,
        version: str,
        status: str,
        roles: tuple[str, ...],
        task: str,
        query_tokens: set[str],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        safe_title, title_findings = _safe(title, 240)
        safe_text, text_findings = _safe(text)
        unit_tokens = set(_tokens(f"{safe_title} {safe_text} {entity_type} {' '.join(roles)}"))
        overlap = len(query_tokens & unit_tokens)
        coverage = overlap / max(1, len(query_tokens))
        exact = int(bool(query_tokens) and query_tokens <= unit_tokens)
        task_boost = 0.18 if task in roles else 0.0
        status_boost = 0.08 if status in {"verified", "contradicted"} else 0.0
        score = min(
            1.0,
            0.07
            + coverage * 0.62
            + min(overlap, 4) * 0.04
            + exact * 0.08
            + task_boost
            + status_boost,
        )
        return {
            "entity_id": entity_id,
            "source": "document",
            "entity_type": entity_type,
            "title": safe_title,
            "subtitle": f"{status} · {version or 'unversioned'}",
            "snippet": safe_text,
            "locator": portable_locator(locator, entity_id),
            "version": version or None,
            "status": status,
            "score": round(score, 6),
            "channels": sorted(
                {"document_exact" if exact else "document_lexical", "parent_aware", *roles}
            ),
            "roles": list(roles),
            "content_digest": _digest(
                entity_id, version, safe_text, repr(sorted(metadata.items()))
            ),
            "redactions": sorted(set(title_findings) | set(text_findings)),
            "metadata": metadata,
        }

    @staticmethod
    def _parent_summary(document: dict[str, Any]) -> dict[str, Any]:
        return {
            "document_id": document["id"],
            "title": document.get("title"),
            "version": document.get("version"),
            "section_count": len(document.get("sections", [])),
            "claim_statuses": dict(
                Counter(
                    str(item.get("status") or "reported") for item in document.get("claims", [])
                )
            ),
        }
