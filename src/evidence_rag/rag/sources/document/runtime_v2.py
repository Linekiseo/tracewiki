"""Production-store facade for the complete Scientific Document Source V2 pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from ....documents.store import DocumentStore
from ....workspace.service import WorkspaceService
from .adapter_v2 import (
    DocumentAdapterV2,
    DocumentParserProvenanceInput,
    DocumentSourceBlock,
    DocumentSourcePage,
    DocumentSourcePayload,
    canonical_document_source_bytes,
)
from .context_builder import build_document_context
from .contracts import (
    DocumentClaimStatus,
    DocumentEdge,
    DocumentEdgeType,
    DocumentEntity,
    DocumentEntityType,
    DocumentFactAuthority,
    DocumentOcrStatus,
    DocumentPublication,
    DocumentSourceKind,
    canonical_sha256,
)
from .retriever import DocumentCandidate, DocumentRetrieverV2
from .store import DocumentV2Store
from .versioning_v2 import compare_document_versions

DOCUMENT_SOURCE_RUNTIME_VERSION = "document-production-runtime-v2"
DOCUMENT_SOURCE_DEFAULT_ENGINE = "v1"
_PORTABLE_RE = re.compile(r"[^A-Za-z0-9._:@-]+")


class DocumentSourceRuntimeError(RuntimeError):
    """A safe fail-closed Document V2 preparation or retrieval failure."""


def _portable(value: str, *, fallback: str) -> str:
    normalized = _PORTABLE_RE.sub("-", value).strip("-._")
    if normalized and normalized[0].isalnum():
        return normalized[:127]
    return fallback


def _identifier(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _snapshot_generation(rows: list[dict[str, Any]]) -> tuple[str, str]:
    payload = [
        {
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "version": str(item.get("version") or ""),
            "content_hash": str(item.get("content_hash") or ""),
            "status": str(item.get("status") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }
        for item in sorted(rows, key=lambda row: str(row.get("id") or ""))
    ]
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    watermark = max((item["updated_at"] for item in payload if item["updated_at"]), default="")
    return f"document-store-snapshot-v2:{digest}", watermark or "unavailable"


def _source_payload(document: dict[str, Any]) -> DocumentSourcePayload:
    source_type = str(document.get("source_type") or "").casefold()
    kind = {
        "markdown": DocumentSourceKind.MARKDOWN,
        "html": DocumentSourceKind.HTML,
        "docx": DocumentSourceKind.DOCX,
        "inline_text": DocumentSourceKind.MARKDOWN,
        "text": DocumentSourceKind.MARKDOWN,
    }.get(source_type)
    if kind is None and source_type != "pdf":
        raise DocumentSourceRuntimeError("Document V2 source format is unsupported")
    source_key = (
        "formal-"
        + hashlib.sha256(f"{document['project_id']}\x1f{document['title']}".encode()).hexdigest()[
            :32
        ]
    )
    version_label = _portable(
        str(document.get("version") or ""),
        fallback="version-" + hashlib.sha256(str(document["id"]).encode()).hexdigest()[:16],
    )
    pages: tuple[DocumentSourcePage, ...] = ()
    content = str(document.get("content") or "")
    if source_type == "pdf":
        page_rows = tuple(document.get("pages") or ())
        if not page_rows:
            raise DocumentSourceRuntimeError("Document V2 PDF page authority is unavailable")
        scan_flags = [
            bool((item.get("metadata") or {}).get("scan_detected"))
            for item in page_rows
            if "scan_detected" in (item.get("metadata") or {})
        ]
        if scan_flags and any(scan_flags):
            kind = (
                DocumentSourceKind.PDF_SCANNED
                if len(scan_flags) == len(page_rows) and all(scan_flags)
                else DocumentSourceKind.PDF_MIXED
            )
        else:
            kind = DocumentSourceKind.PDF_TEXT
        parsed_pages: list[DocumentSourcePage] = []
        for item in page_rows:
            metadata = item.get("metadata") or {}
            scan_detected = metadata.get("scan_detected")
            ocr_status_value = metadata.get("ocr_status")
            selected_text = str(item.get("content") or "")
            blocks_value = metadata.get("blocks")
            provenance_value = metadata.get("parser_provenance")
            try:
                ocr_status = (
                    DocumentOcrStatus(str(ocr_status_value))
                    if ocr_status_value is not None
                    else None
                )
                parsed_pages.append(
                    DocumentSourcePage(
                        page_number=int(item["page_number"]),
                        text="" if scan_detected is True else selected_text,
                        ocr_text=(
                            selected_text
                            if scan_detected is True
                            and ocr_status
                            in {
                                DocumentOcrStatus.AVAILABLE,
                                DocumentOcrStatus.PARTIAL,
                            }
                            else ""
                        ),
                        width=float(metadata.get("width") or 612.0),
                        height=float(metadata.get("height") or 792.0),
                        page_identity=metadata.get("page_identity"),
                        page_content_sha256=metadata.get("page_content_sha256"),
                        scan_detected=scan_detected,
                        ocr_status=ocr_status,
                        blocks=(
                            tuple(
                                DocumentSourceBlock.model_validate(value) for value in blocks_value
                            )
                            if blocks_value is not None
                            else None
                        ),
                        layout_payload_sha256=metadata.get("layout_payload_sha256"),
                        parser_provenance=(
                            DocumentParserProvenanceInput.model_validate(provenance_value)
                            if provenance_value is not None
                            else None
                        ),
                    )
                )
            except (TypeError, ValueError) as error:
                raise DocumentSourceRuntimeError(
                    "Document V2 PDF parser payload failed verification"
                ) from error
        pages = tuple(parsed_pages)
        content = ""
    content_hash = str(document.get("content_hash") or "")
    page_artifacts = {
        str((item.get("metadata") or {}).get("artifact_sha256") or "")
        for item in document.get("pages") or ()
        if (item.get("metadata") or {}).get("artifact_sha256")
    }
    artifact_candidate = next(iter(page_artifacts)) if len(page_artifacts) == 1 else content_hash
    artifact_sha256 = (
        artifact_candidate if re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_candidate) else None
    )
    try:
        return DocumentSourcePayload(
            source_key=source_key,
            title=str(document["title"]),
            version_label=version_label,
            source_kind=kind,
            content=content,
            pages=pages,
            authors=tuple(str(item) for item in document.get("authors") or ()),
            tags=tuple(str(item) for item in document.get("tags") or ()),
            artifact_sha256=artifact_sha256,
        )
    except (TypeError, ValueError) as error:
        raise DocumentSourceRuntimeError(
            "Document V2 authoritative content is outside parser limits"
        ) from error


def _document_parser_trace(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ocr_statuses: defaultdict[str, int] = defaultdict(int)
    provider_statuses: defaultdict[str, int] = defaultdict(int)
    scanned_pages = 0
    layout_blocks = 0
    pdf_pages = 0
    documents_with_fallback_text = 0
    for document in rows:
        if str(document.get("source_type") or "").casefold() != "pdf":
            continue
        if str(document.get("content") or ""):
            documents_with_fallback_text += 1
        for page in document.get("pages") or ():
            pdf_pages += 1
            metadata = page.get("metadata") or {}
            layout_blocks += len(metadata.get("blocks") or ())
            if metadata.get("scan_detected") is not True:
                continue
            scanned_pages += 1
            ocr_statuses[str(metadata.get("ocr_status") or "unavailable")] += 1
            provenance = metadata.get("parser_provenance") or {}
            provider_statuses[str(provenance.get("provider_status") or "unavailable")] += 1
    ocr_problematic = sum(
        count
        for status, count in ocr_statuses.items()
        if status in {"partial", "unavailable", "error"}
    )
    provider_problematic = sum(
        count
        for status, count in provider_statuses.items()
        if status in {"partial", "unavailable", "error"}
    )
    problematic = max(ocr_problematic, provider_problematic)
    if problematic:
        ingestion_status = (
            "partial"
            if documents_with_fallback_text or problematic < scanned_pages
            else "unavailable"
        )
    else:
        ingestion_status = "complete"
    return {
        "ingestion_status": ingestion_status,
        "pdf_page_count": pdf_pages,
        "scanned_page_count": scanned_pages,
        "layout_block_count": layout_blocks,
        "ocr_statuses": dict(sorted(ocr_statuses.items())),
        "provider_statuses": dict(sorted(provider_statuses.items())),
        "fallback_used": problematic > 0,
    }


def _replace_publication(
    publication: DocumentPublication,
    *,
    entities: tuple[DocumentEntity, ...],
    edges: tuple[DocumentEdge, ...],
) -> DocumentPublication:
    version = publication.version.model_copy(
        update={"entity_ids": tuple(item.entity_id for item in entities)}
    )
    units = tuple(DocumentAdapterV2._unit(item) for item in entities)
    values = {
        "publication_id": _identifier(
            "docpub",
            publication.publication_id,
            *(item.entity_id for item in entities),
        ),
        "source_payload_sha256": publication.source_payload_sha256,
        "family": publication.family,
        "version": version,
        "entities": entities,
        "edges": edges,
        "retrieval_units": units,
    }
    digest = canonical_sha256(
        {
            "publication_id": values["publication_id"],
            "source_payload_sha256": values["source_payload_sha256"],
            "family": publication.family.model_dump(mode="json"),
            "version": version.model_dump(mode="json"),
            "entities": [item.model_dump(mode="json") for item in entities],
            "edges": [item.model_dump(mode="json") for item in edges],
            "retrieval_units": [item.model_dump(mode="json") for item in units],
        }
    )
    return DocumentPublication(**values, publication_sha256=digest)


def _with_formal_claims(
    publication: DocumentPublication,
    document: dict[str, Any],
) -> DocumentPublication:
    entities = list(publication.entities)
    edges = list(publication.edges)
    changed = False
    candidates_by_text = {
        " ".join(item.source_text.casefold().split()): item
        for item in entities
        if item.entity_type == DocumentEntityType.CLAIM_CANDIDATE
    }
    for ordinal, record in enumerate(document.get("claims") or (), 1):
        source_text = str(record.get("content") or "").strip()
        normalized = " ".join(source_text.casefold().split())
        if not source_text:
            continue
        formal_id = str(record["id"])
        candidate = candidates_by_text.get(normalized)
        if candidate is None:
            candidate_id = _identifier("doccandidate", publication.version.version_id, formal_id)
            candidate_locator = f"{publication.version.locator}/formal-claim/{candidate_id}"
            candidate = DocumentEntity(
                entity_id=candidate_id,
                stable_id=_identifier("docstableclaim", publication.family.family_id, source_text),
                entity_type=DocumentEntityType.CLAIM_CANDIDATE,
                family_id=publication.family.family_id,
                version_id=publication.version.version_id,
                scope=publication.family.scope,
                parent_id=publication.version.version_id,
                ordinal=ordinal,
                source_text=source_text,
                content_sha256=canonical_sha256(
                    {
                        "formal_claim_id": formal_id,
                        "content": source_text,
                        "status": record.get("status"),
                        "metadata": record.get("metadata") or {},
                    }
                ),
                locator=candidate_locator,
                authority=DocumentFactAuthority.PARSER_DERIVED,
                status=DocumentClaimStatus.CANDIDATE,
                label=str(record.get("display_key") or "Formal claim"),
                metadata={
                    "formal_claim_id": formal_id,
                    "formal_status": str(record.get("status") or "reported"),
                    "claim_type": str(record.get("claim_type") or "other"),
                    "extraction_method": str(record.get("extraction_method") or "formal-store"),
                    "extraction_confidence": float(record.get("extraction_confidence") or 0.0),
                    "source_locator": str(record.get("source_locator") or ""),
                },
            )
            entities.append(candidate)
            changed = True
            candidates_by_text[normalized] = candidate
            edges.append(
                DocumentEdge(
                    edge_id=_identifier(
                        "docedge",
                        publication.version.version_id,
                        publication.version.version_id,
                        candidate_id,
                    ),
                    version_id=publication.version.version_id,
                    scope=publication.family.scope,
                    source_id=publication.version.version_id,
                    target_id=candidate_id,
                    edge_type=DocumentEdgeType.REPORTS,
                    authority=DocumentFactAuthority.PARSER_DERIVED,
                    confidence=float(record.get("extraction_confidence") or 0.0),
                    review_status="observed",
                    locator=f"{candidate_locator}/reported",
                )
            )
        else:
            # A parser-derived candidate with identical text still needs the
            # formal claim identity.  Without this binding, the full runtime
            # would return an internal candidate id and break provenance joins
            # even though the authoritative claim was present.
            formal_metadata = {
                **candidate.metadata,
                "formal_claim_id": formal_id,
                "formal_status": str(record.get("status") or "reported"),
                "claim_type": str(record.get("claim_type") or "other"),
                "extraction_method": str(record.get("extraction_method") or "formal-store"),
                "extraction_confidence": float(record.get("extraction_confidence") or 0.0),
                "source_locator": str(record.get("source_locator") or ""),
            }
            candidate = candidate.model_copy(
                update={
                    "metadata": formal_metadata,
                    "content_sha256": canonical_sha256(
                        {
                            "parser_content_sha256": candidate.content_sha256,
                            "formal_claim_id": formal_id,
                            "formal_status": formal_metadata["formal_status"],
                            "claim_type": formal_metadata["claim_type"],
                            "extraction_method": formal_metadata["extraction_method"],
                        }
                    ),
                }
            )
            entities = [
                candidate if item.entity_id == candidate.entity_id else item for item in entities
            ]
            candidates_by_text[normalized] = candidate
            changed = True
        candidate_id = candidate.entity_id
        candidate_locator = candidate.locator
        status = DocumentClaimStatus(str(record.get("status") or "reported"))
        if status == DocumentClaimStatus.REPORTED:
            continue
        claim_id = _identifier("docclaim", publication.version.version_id, formal_id, status)
        claim = DocumentEntity(
            entity_id=claim_id,
            stable_id=candidate.stable_id,
            entity_type=DocumentEntityType.CLAIM,
            family_id=publication.family.family_id,
            version_id=publication.version.version_id,
            scope=publication.family.scope,
            parent_id=candidate_id,
            ordinal=ordinal,
            source_text=source_text,
            content_sha256=canonical_sha256(
                {
                    "candidate": candidate.content_sha256,
                    "formal_claim_id": formal_id,
                    "formal_status": status,
                    "validation": (record.get("metadata") or {}).get("validation"),
                }
            ),
            locator=f"{candidate_locator}/reviewed/{claim_id}",
            authority=DocumentFactAuthority.REVIEWED_DERIVED,
            status=status,
            label=candidate.label,
            metadata={
                **candidate.metadata,
                "formal_claim_id": formal_id,
                "formal_status": status.value,
                "validation": (record.get("metadata") or {}).get("validation"),
                "review_authority": "formal-document-service",
            },
        )
        entities.append(claim)
        changed = True
        edges.append(
            DocumentEdge(
                edge_id=_identifier("docedge", candidate_id, claim_id, status),
                version_id=publication.version.version_id,
                scope=publication.family.scope,
                source_id=candidate_id,
                target_id=claim_id,
                edge_type=DocumentEdgeType.REPORTS,
                authority=DocumentFactAuthority.REVIEWED_DERIVED,
                confidence=1.0,
                review_status="confirmed",
                locator=f"{claim.locator}/reported",
            )
        )
    if not changed:
        return publication
    return _replace_publication(
        publication,
        entities=tuple(entities),
        edges=tuple(edges),
    )


class DocumentSourceRuntimeV2:
    """Read formal document rows and execute the typed Document V2 source path."""

    def __init__(
        self,
        *,
        documents: DocumentStore,
        workspace: WorkspaceService,
        index: DocumentV2Store,
    ) -> None:
        self.documents = documents
        self.workspace = workspace
        self.index = index
        self.adapter = DocumentAdapterV2()

    def search(
        self,
        *,
        project_id: str,
        query: str,
        document_ids: list[str] | tuple[str, ...] | None = None,
        allowed_acl_refs: list[str] | tuple[str, ...] | None = None,
        enforce_acl: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not query.strip():
            raise DocumentSourceRuntimeError("Document V2 query must not be empty")
        project = self.workspace._require_project(project_id)
        project_acl = str(project.get("acl_ref") or "")
        allowed = tuple(sorted({"public", *(allowed_acl_refs or ())}))
        if enforce_acl and project_acl not in set(allowed):
            return self._empty("acl_denied", "project_acl_denied")

        summaries = self.documents.list_documents(project_id)
        generation_id, watermark = _snapshot_generation(summaries)
        requested = set(document_ids or ())
        selected_rows = sorted(
            (item for item in summaries if not requested or str(item.get("id") or "") in requested),
            key=lambda item: (
                str(item.get("title") or "").casefold(),
                str(item.get("version") or "").casefold(),
                str(item.get("created_at") or ""),
                str(item.get("id") or ""),
            ),
        )
        if requested - {str(item.get("id") or "") for item in selected_rows}:
            raise DocumentSourceRuntimeError("Document V2 requested scope is unavailable")
        if not selected_rows:
            return self._empty(
                "not_indexed",
                "document_not_indexed",
                generation_id=generation_id,
                watermark=watermark,
            )

        self.index.initialize()
        publications: list[DocumentPublication] = []
        formal_by_version: dict[str, dict[str, Any]] = {}
        formal_documents: list[dict[str, Any]] = []
        families: set[str] = set()
        for summary in selected_rows:
            document = self.documents.get_document(str(summary["id"]))
            if document is None or str(document.get("project_id") or "") != project_id:
                raise DocumentSourceRuntimeError("Document V2 authoritative row changed")
            payload = _source_payload(document)
            publication = self.adapter.parse_bytes(
                canonical_document_source_bytes(payload),
                project_id=project_id,
                acl_ref=project_acl,
                generation_id=generation_id,
            )
            publication = _with_formal_claims(publication, document)
            self.index.publish(publication)
            publications.append(publication)
            formal_documents.append(document)
            formal_by_version[publication.version.version_id] = document
            families.add(publication.family.family_id)

        scope = publications[0].family.scope
        result = DocumentRetrieverV2(self.index).search(
            scope=scope,
            query=query,
            limit=limit,
            family_ids=tuple(sorted(families)) if requested else (),
        )
        context = build_document_context(result)
        comparisons = self._comparisons(publications)
        mapped = [self._candidate(candidate, formal_by_version) for candidate in result.candidates]
        parser_trace = _document_parser_trace(formal_documents)
        source_status = (
            parser_trace["ingestion_status"]
            if parser_trace["ingestion_status"] != "complete"
            else ("complete" if mapped else "no_match")
        )
        return {
            "results": mapped,
            "context": context.model_dump(mode="json"),
            "trace": {
                **result.trace,
                "engine": DOCUMENT_SOURCE_RUNTIME_VERSION,
                "source_status": source_status,
                "index_generation": [generation_id],
                "watermark": watermark,
                "formal_document_count": len(selected_rows),
                "default_engine": DOCUMENT_SOURCE_DEFAULT_ENGINE,
                "explicit_v2_required": True,
                "fallback_used": parser_trace["fallback_used"],
                "parser": parser_trace,
            },
            "index_generation": [generation_id],
            "watermark": watermark,
            "comparisons": [item.model_dump(mode="json") for item in comparisons],
        }

    @staticmethod
    def _comparisons(publications: list[DocumentPublication]) -> tuple[Any, ...]:
        by_family: defaultdict[str, list[DocumentPublication]] = defaultdict(list)
        for publication in publications:
            by_family[publication.family.family_id].append(publication)
        comparisons = []
        for values in by_family.values():
            ordered = sorted(
                values,
                key=lambda item: (item.version.version_label, item.version.version_id),
            )
            for baseline, candidate in zip(ordered, ordered[1:], strict=False):
                comparisons.append(compare_document_versions(baseline, candidate))
        return tuple(comparisons)

    @staticmethod
    def _candidate(
        candidate: DocumentCandidate,
        formal_by_version: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        formal = formal_by_version[candidate.version_id]
        metadata = dict(candidate.metadata)
        formal_claim_id = str(metadata.get("formal_claim_id") or "")
        return {
            "entity_id": formal_claim_id or candidate.entity_id,
            "source": "document",
            "entity_type": candidate.entity_type.value,
            "title": str(
                metadata.get("label") or formal.get("title") or candidate.entity_type.value
            ),
            "subtitle": str(formal.get("version") or ""),
            "snippet": candidate.source_text or candidate.derived_text,
            "locator": candidate.locator,
            "version": str(formal.get("version") or ""),
            "status": candidate.status,
            "score": candidate.score,
            "channels": list(candidate.channels),
            "roles": list(candidate.roles),
            "content_digest": str(
                metadata.get("content_sha256")
                or canonical_sha256(
                    {
                        "entity_id": candidate.entity_id,
                        "source_text": candidate.source_text,
                        "derived_text": candidate.derived_text,
                    }
                )
            ),
            "redactions": [],
            "metadata": {
                **metadata,
                "v2_entity_id": candidate.entity_id,
                "document_id": str(formal["id"]),
                "formal_document_id": str(formal["id"]),
                "parent_id": candidate.parent_id or str(formal["id"]),
                "version_id": candidate.version_id,
                "family_id": candidate.family_id,
                "authority": candidate.authority.value,
            },
        }

    @staticmethod
    def _empty(
        status: str,
        reason: str,
        *,
        generation_id: str = "not-indexed:document",
        watermark: str = "unavailable",
    ) -> dict[str, Any]:
        return {
            "results": [],
            "context": {
                "availability": "UNAVAILABLE",
                "blocks": [],
                "citation_map": {},
                "missing": [reason],
                "reasoning_included": False,
            },
            "trace": {
                "engine": DOCUMENT_SOURCE_RUNTIME_VERSION,
                "source_status": status,
                "index_generation": [generation_id],
                "watermark": watermark,
                "default_engine": DOCUMENT_SOURCE_DEFAULT_ENGINE,
                "explicit_v2_required": True,
                "fallback_used": False,
                "reasoning_included": False,
            },
            "index_generation": [generation_id],
            "watermark": watermark,
            "comparisons": [],
        }


__all__ = [
    "DOCUMENT_SOURCE_DEFAULT_ENGINE",
    "DOCUMENT_SOURCE_RUNTIME_VERSION",
    "DocumentSourceRuntimeError",
    "DocumentSourceRuntimeV2",
]
