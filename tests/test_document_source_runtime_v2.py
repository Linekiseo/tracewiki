from __future__ import annotations

import json
import shutil
from pathlib import Path

from evidence_rag.documents.adapters.structured import (
    DeterministicPDFParserProvider,
    PDFDeterministicPage,
    PDFLayoutBlock,
    PDFParseRequest,
    PDFProviderStatus,
    UnavailablePDFParserProvider,
)
from evidence_rag.documents.models import DocumentIngestRequest
from evidence_rag.documents.service import DocumentService
from evidence_rag.documents.store import DocumentStore
from evidence_rag.experiments.store import ExperimentStore
from evidence_rag.rag.sources.document import (
    DOCUMENT_SOURCE_DEFAULT_ENGINE,
    DocumentSourceRuntimeV2,
)
from evidence_rag.rag.sources.document.store import DocumentV2Store
from evidence_rag.storage import SQLiteStore
from evidence_rag.workspace.models import ProjectCreate
from evidence_rag.workspace.service import WorkspaceService
from evidence_rag.workspace.store import WorkspaceStore

PDF_FIXTURES = Path(__file__).parent / "fixtures" / "document_parser_v2"


def _pdf_provider(
    name: str,
    *page_numbers: int,
) -> DeterministicPDFParserProvider:
    payload = json.loads((PDF_FIXTURES / "ocr_payloads.json").read_text(encoding="utf-8"))[name]
    selected = set(page_numbers) or {int(value) for value in payload}
    return DeterministicPDFParserProvider(
        {
            int(page_number): PDFDeterministicPage(
                ocr_text=item["ocr_text"],
                status=PDFProviderStatus(item["status"]),
                blocks=tuple(
                    PDFLayoutBlock(
                        text=block["text"],
                        bbox=tuple(block["bbox"]),
                        block_type=block["block_type"],
                        confidence=block["confidence"],
                    )
                    for block in item["blocks"]
                ),
            )
            for page_number, item in payload.items()
            if int(page_number) in selected
        }
    )


def _pdf_ingestion_stack(tmp_path: Path, settings, provider: object):
    database = SQLiteStore(settings.database_path)
    database.initialize()
    workspace_store = WorkspaceStore(database)
    workspace_store.initialize()
    workspace = WorkspaceService(workspace_store)
    workspace.create_project(
        ProjectCreate(
            id="project-document-pdf-runtime",
            name="Document PDF runtime",
            acl_ref="project:document-pdf-runtime",
        )
    )
    documents = DocumentStore(database)
    documents.initialize()
    experiments = ExperimentStore(database)
    experiments.initialize()
    service = DocumentService(
        documents,
        experiments,
        workspace,
        settings,
        pdf_provider=provider,
    )
    index_root = tmp_path / "document-pdf-index"
    index_root.mkdir()
    runtime = DocumentSourceRuntimeV2(
        documents=documents,
        workspace=workspace,
        index=DocumentV2Store(
            index_root / "document-runtime.sqlite3",
            isolated_root=index_root,
        ),
    )
    return service, runtime


def _ingest_fixture(
    service: DocumentService,
    tmp_path: Path,
    *,
    fixture: str,
    title: str,
) -> dict:
    source = tmp_path / fixture
    shutil.copyfile(PDF_FIXTURES / fixture, source)
    return service.ingest(
        DocumentIngestRequest(
            project_id="project-document-pdf-runtime",
            title=title,
            version="v1",
            source=str(source),
        )
    )


def _runtime(tmp_path: Path) -> tuple[DocumentSourceRuntimeV2, str]:
    database = SQLiteStore(tmp_path / "formal.sqlite3")
    database.initialize()
    workspace_store = WorkspaceStore(database)
    workspace_store.initialize()
    workspace = WorkspaceService(workspace_store)
    workspace.create_project(
        ProjectCreate(
            id="project-document-runtime",
            name="Document runtime",
            acl_ref="project:document-runtime",
        )
    )
    documents = DocumentStore(database)
    documents.initialize()
    first_id = "document://project-document-runtime/report/v1"
    for version, recall, status in (
        ("v 1", "82.1 ± 0.4%", "reported"),
        ("v 2", "86.4 ± 0.3%", "verified"),
    ):
        document_id = f"document://project-document-runtime/report/{version.replace(' ', '-')}"
        section_id = f"{document_id}/section/results"
        content = (
            "# Results\n\n"
            f"Claim: Candidate improves Recall to {recall}.\n\n"
            "| Model | Split | Recall |\n"
            "| --- | --- | --- |\n"
            f"| Candidate | Test | {recall} |\n"
        )
        documents.create_document(
            {
                "id": document_id,
                "display_key": "DOC-" + version.replace(" ", "").upper(),
                "project_id": "project-document-runtime",
                "iteration_id": None,
                "title": "Runtime report",
                "version": version,
                "source_type": "markdown",
                "source_uri": "managed:runtime-report.md",
                "content_hash": "sha256:"
                + __import__("hashlib").sha256(content.encode()).hexdigest(),
                "content": content,
                "authors": ["Research team"],
                "tags": ["runtime"],
            },
            [
                {
                    "id": section_id,
                    "ordinal": 1,
                    "level": 1,
                    "title": "Results",
                    "content": content,
                    "start_line": 1,
                    "end_line": len(content.splitlines()),
                    "source_locator": f"document:{version}#section=results",
                }
            ],
            [
                {
                    "id": f"claim://project-document-runtime/report/{version.replace(' ', '-')}",
                    "display_key": "CLM-" + version.replace(" ", "").upper(),
                    "section_id": section_id,
                    "content": f"Candidate improves Recall to {recall}.",
                    "claim_type": "performance",
                    "extraction_method": "explicit_marker",
                    "extraction_confidence": 0.98,
                    "source_locator": f"document:{version}#claim=1",
                    "metadata": (
                        {"validation": {"policy": "formal-review"}} if status == "verified" else {}
                    ),
                    "status": status,
                }
            ],
        )
        if status == "verified":
            claim_id = f"claim://project-document-runtime/report/{version.replace(' ', '-')}"
            documents.update_claim(claim_id, {"status": "verified"})
        if version == "v 1":
            first_id = document_id
    index_root = tmp_path / "document-index"
    index_root.mkdir()
    return (
        DocumentSourceRuntimeV2(
            documents=documents,
            workspace=workspace,
            index=DocumentV2Store(
                index_root / "document-runtime.sqlite3",
                isolated_root=index_root,
            ),
        ),
        first_id,
    )


def test_document_runtime_uses_formal_versions_claims_tables_and_context(
    tmp_path: Path,
) -> None:
    runtime, _ = _runtime(tmp_path)
    result = runtime.search(
        project_id="project-document-runtime",
        query="claim Candidate Recall",
        allowed_acl_refs=["project:document-runtime"],
        enforce_acl=True,
    )
    assert result["results"]
    assert DOCUMENT_SOURCE_DEFAULT_ENGINE == "v1"
    assert result["trace"]["explicit_v2_required"] is True
    assert result["trace"]["fallback_used"] is False
    assert result["trace"]["formal_document_count"] == 2
    assert result["comparisons"]
    assert any(item["status"] == "verified" for item in result["results"])
    assert any(item["metadata"].get("formal_claim_id") for item in result["results"])
    assert result["context"]["blocks"]
    assert result["context"]["reasoning_included"] is False
    assert all("fixture" not in item["locator"] for item in result["results"])

    table = runtime.search(
        project_id="project-document-runtime",
        query="table Candidate Test Recall 86.4",
        allowed_acl_refs=["project:document-runtime"],
        enforce_acl=True,
    )
    assert any(item["entity_type"] == "table_cell_fact" for item in table["results"])


def test_document_runtime_project_acl_and_document_scope_fail_closed(
    tmp_path: Path,
) -> None:
    runtime, first_id = _runtime(tmp_path)
    denied = runtime.search(
        project_id="project-document-runtime",
        query="Recall",
        allowed_acl_refs=["project:other"],
        enforce_acl=True,
    )
    assert denied["results"] == []
    assert denied["trace"]["source_status"] == "acl_denied"

    scoped = runtime.search(
        project_id="project-document-runtime",
        query="Recall",
        document_ids=[first_id],
        allowed_acl_refs=["project:document-runtime"],
        enforce_acl=True,
    )
    assert scoped["trace"]["formal_document_count"] == 1
    assert {item["metadata"]["formal_document_id"] for item in scoped["results"]} <= {first_id}


def test_pdf_ingestion_to_v2_query_preserves_ocr_layout_and_source_kind(
    tmp_path: Path,
    settings,
) -> None:
    service, runtime = _pdf_ingestion_stack(
        tmp_path,
        settings,
        _pdf_provider("scanned.pdf"),
    )
    ingested = _ingest_fixture(
        service,
        tmp_path,
        fixture="scanned.pdf",
        title="Scanned evaluation",
    )
    assert ingested["source_uri"].startswith("document-source://sha256/")
    assert ingested["pages"][0]["metadata"]["ocr_status"] == "available"

    result = runtime.search(
        project_id="project-document-pdf-runtime",
        query="OCR Recall 88.4 percent",
        document_ids=[ingested["id"]],
        allowed_acl_refs=["project:document-pdf-runtime"],
        enforce_acl=True,
    )
    assert result["results"]
    assert result["trace"]["source_status"] == "complete"
    assert result["trace"]["fallback_used"] is False
    assert result["trace"]["parser"]["scanned_page_count"] == 1
    assert result["trace"]["parser"]["ocr_statuses"] == {"available": 1}
    assert any(item["metadata"]["source_kind"] == "pdf_scanned" for item in result["results"])
    layout = next(item for item in result["results"] if item["entity_type"] == "layout_block")
    assert layout["metadata"]["block_type"] in {"header", "body"}
    assert layout["metadata"]["bbox"] != [0.0, 0.0, 612.0, 792.0]
    assert layout["metadata"]["page_identity"].startswith("sha256:")


def test_pdf_v2_query_reports_partial_unavailable_and_error_without_fabrication(
    tmp_path: Path,
    settings,
) -> None:
    partial_service, runtime = _pdf_ingestion_stack(
        tmp_path,
        settings,
        _pdf_provider("mixed.pdf", 2),
    )
    mixed = _ingest_fixture(
        partial_service,
        tmp_path,
        fixture="mixed.pdf",
        title="Mixed evaluation",
    )
    partial = runtime.search(
        project_id="project-document-pdf-runtime",
        query="Native precision 79.5 percent",
        document_ids=[mixed["id"]],
        allowed_acl_refs=["project:document-pdf-runtime"],
        enforce_acl=True,
    )
    assert partial["results"]
    assert partial["trace"]["source_status"] == "partial"
    assert partial["trace"]["fallback_used"] is True
    assert partial["trace"]["parser"]["ocr_statuses"] == {
        "available": 1,
        "unavailable": 1,
    }
    assert partial["trace"]["parser"]["provider_statuses"] == {"partial": 2}
    assert any(item["metadata"]["source_kind"] == "pdf_mixed" for item in partial["results"])

    unavailable_service = DocumentService(
        partial_service.store,
        partial_service.experiments,
        partial_service.workspace,
        settings,
        pdf_provider=UnavailablePDFParserProvider(),
    )
    unavailable = _ingest_fixture(
        unavailable_service,
        tmp_path,
        fixture="scanned.pdf",
        title="Unavailable scan",
    )
    missing = runtime.search(
        project_id="project-document-pdf-runtime",
        query="88.4 unique hidden OCR value",
        document_ids=[unavailable["id"]],
        allowed_acl_refs=["project:document-pdf-runtime"],
        enforce_acl=True,
    )
    assert missing["results"] == []
    assert missing["trace"]["source_status"] == "unavailable"
    assert missing["trace"]["parser"]["ocr_statuses"] == {"unavailable": 1}

    class ErrorProvider:
        provider_name = "local-error"
        provider_version = "error-v1"

        def parse(self, request: PDFParseRequest) -> object:
            raise RuntimeError(request.artifact_sha256)

    error_service = DocumentService(
        partial_service.store,
        partial_service.experiments,
        partial_service.workspace,
        settings,
        pdf_provider=ErrorProvider(),
    )
    failed = _ingest_fixture(
        error_service,
        tmp_path,
        fixture="scanned.pdf",
        title="Error scan",
    )
    errored = runtime.search(
        project_id="project-document-pdf-runtime",
        query="88.4 unique hidden OCR value",
        document_ids=[failed["id"]],
        allowed_acl_refs=["project:document-pdf-runtime"],
        enforce_acl=True,
    )
    assert errored["results"] == []
    assert errored["trace"]["source_status"] == "unavailable"
    assert errored["trace"]["parser"]["ocr_statuses"] == {"error": 1}
    assert errored["trace"]["parser"]["provider_statuses"] == {"error": 1}
