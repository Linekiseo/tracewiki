from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse

from ..dependencies import (
    AccessContext,
    access_context,
    get_runtime,
    mutation_auth,
    require_project_access,
)
from ..runtime import Runtime
from .aggregations import TableAggregationError
from .models import (
    ClaimCreate,
    ClaimEvidenceCreate,
    ClaimEvidenceUnlink,
    ClaimMatchReview,
    ClaimUpdate,
    DocumentIngestRequest,
    TableMetricAggregationCreate,
    TableMetricMatchReview,
)
from .service import DocumentError
from .table_evidence import TableEvidenceError

router = APIRouter(prefix="/v1/documents", tags=["scientific-documents"])
write = [Depends(mutation_auth)]


def _call(operation):
    try:
        return operation()
    except (DocumentError, TableEvidenceError, TableAggregationError) as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/stats")
def stats(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return rt.documents.store.stats(project_id)


@router.get("")
def documents(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.documents.store.list_documents(project_id)


@router.post("/ingest", status_code=201, dependencies=write)
def ingest(
    request: DocumentIngestRequest,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.documents.ingest(request))


@router.post("/upload-batch", status_code=201, dependencies=write)
async def upload_batch(
    files: list[UploadFile] = File(...),
    project_id: str = Form("project-rag"),
    iteration_id: str | None = Form(None),
    version: str = Form("v1"),
    authors: str = Form(""),
    tags: str = Form(""),
    extract_claims: bool = Form(True),
    manifest: str = Form("[]"),
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    if not files:
        raise HTTPException(status_code=422, detail="select at least one document")
    try:
        manifest_items = json.loads(manifest)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="invalid document upload manifest") from exc
    if not isinstance(manifest_items, list):
        raise HTTPException(status_code=422, detail="document upload manifest must be a list")

    supported = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf", ".docx"}
    batch_id = uuid4().hex
    upload_root = rt.settings.data_dir / "document_uploads" / batch_id
    upload_root.mkdir(parents=True, exist_ok=True)
    author_items = [item.strip() for item in authors.split(",") if item.strip()]
    tag_items = [item.strip() for item in tags.split(",") if item.strip()]
    results: list[dict] = []

    for index, upload in enumerate(files):
        original_name = Path(upload.filename or f"document-{index + 1}").name
        suffix = Path(original_name).suffix.casefold()
        config = manifest_items[index] if index < len(manifest_items) else {}
        if not isinstance(config, dict):
            config = {}
        title = str(config.get("title") or Path(original_name).stem or original_name).strip()
        item_version = str(config.get("version") or version or "v1").strip()
        result = {"filename": original_name, "title": title, "version": item_version}
        target: Path | None = None
        try:
            if suffix not in supported:
                raise DocumentError(f"unsupported document format: {suffix or '<none>'}")
            safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original_name).stem).strip(".-")
            safe_name = f"{safe_stem or 'document'}{suffix}"
            target = upload_root / f"{index + 1:02d}-{safe_name}"
            size = 0
            with target.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    output.write(chunk)
            existed = rt.documents.store.find_version(project_id, title, item_version) is not None
            document = rt.documents.ingest(
                DocumentIngestRequest(
                    project_id=project_id,
                    iteration_id=iteration_id or None,
                    title=title,
                    version=item_version,
                    source=str(target),
                    authors=author_items,
                    tags=tag_items,
                    extract_claims=extract_claims,
                )
            )
            result.update(
                {
                    "status": "existing" if existed else "ingested",
                    "size": size,
                    "document": document,
                    "claim_count": len(document.get("claims", [])),
                }
            )
        except (DocumentError, OSError, ValueError) as exc:
            if target and target.exists():
                target.unlink()
            result.update({"status": "failed", "error": str(exc)})
        finally:
            await upload.close()
        results.append(result)

    succeeded = sum(item["status"] != "failed" for item in results)
    return {
        "batch_id": batch_id,
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "items": results,
    }


@router.get("/by-id")
def document(
    document_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.documents._require_document(document_id))
    require_project_access(rt, access, item["project_id"])
    return item


@router.get("/content")
def document_content(
    document_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> Response:
    item = _call(lambda: rt.documents._require_document(document_id))
    require_project_access(rt, access, item["project_id"])
    if item["source_type"] == "inline_text":
        return Response(
            content=item["content"],
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": 'inline; filename="document.txt"'},
        )

    source = Path(item["source_uri"]).expanduser().resolve()
    allowed_roots = (
        *rt.settings.allowed_local_roots,
        rt.settings.data_dir / "document_uploads",
    )
    if not source.is_file() or not any(
        source.is_relative_to(root.resolve()) for root in allowed_roots
    ):
        raise HTTPException(status_code=404, detail="document content not found")
    media_type = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".markdown": "text/markdown; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
    }.get(source.suffix.casefold(), "application/octet-stream")
    return FileResponse(
        source,
        media_type=media_type,
        filename=source.name,
        content_disposition_type="inline",
    )


@router.get("/claims")
def claims(
    project_id: str = "project-rag",
    document_id: str | None = None,
    status: str | None = None,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.documents.store.list_claims(project_id, document_id=document_id, status=status)


@router.get("/claims/match-candidates")
def match_candidates(
    project_id: str = "project-rag",
    claim_id: str | None = None,
    review_status: str | None = None,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.documents.store.list_match_candidates(project_id, claim_id, review_status)


@router.get("/claims/match-candidates/by-id")
def match_candidate(
    candidate_id: str,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = rt.documents.store.get_match_candidate(candidate_id)
    if not item:
        raise HTTPException(status_code=404, detail="claim match candidate not found")
    require_project_access(rt, access, item["project_id"])
    return item


@router.post("/claims/match-scan", dependencies=write)
def scan_claim_matches(
    project_id: str = "project-rag",
    claim_id: str | None = None,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return rt.documents.suggest_matches(project_id, [claim_id] if claim_id else None)


@router.post("/claims/match-candidates/review", dependencies=write)
def review_claim_match(
    candidate_id: str,
    request: ClaimMatchReview,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = rt.documents.store.get_match_candidate(candidate_id)
    if not item:
        raise HTTPException(status_code=404, detail="claim match candidate not found")
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.documents.review_match_candidate(candidate_id, request))


@router.get("/tables/metric-candidates")
def table_metric_candidates(
    project_id: str = "project-rag",
    document_id: str | None = None,
    claim_id: str | None = None,
    review_status: str | None = None,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.documents.store.list_table_metric_candidates(
        project_id,
        document_id=document_id,
        claim_id=claim_id,
        review_status=review_status,
    )


@router.post("/tables/metric-scan", dependencies=write)
def scan_table_metric_matches(
    project_id: str = "project-rag",
    document_id: str | None = None,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return _call(lambda: rt.documents.table_evidence.scan(project_id, document_id))


@router.post("/tables/metric-candidates/review", dependencies=write)
def review_table_metric_match(
    candidate_id: str,
    request: TableMetricMatchReview,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = rt.documents.store.get_table_metric_candidate(candidate_id)
    if not item:
        raise HTTPException(status_code=404, detail="table metric match candidate not found")
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.documents.table_evidence.review(candidate_id, request, rt.documents))


@router.get("/tables/aggregations")
def table_metric_aggregations(
    project_id: str = "project-rag",
    document_id: str | None = None,
    claim_id: str | None = None,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.documents.store.list_table_metric_aggregations(
        project_id, document_id=document_id, claim_id=claim_id
    )


@router.get("/tables/aggregations/by-id")
def table_metric_aggregation(
    aggregation_id: str,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = rt.documents.store.get_table_metric_aggregation(aggregation_id)
    if not item:
        raise HTTPException(status_code=404, detail="table metric aggregation not found")
    require_project_access(rt, access, item["project_id"])
    return item


@router.post("/tables/aggregations", status_code=201, dependencies=write)
def create_table_metric_aggregation(
    request: TableMetricAggregationCreate,
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.documents.table_aggregations.create(request, rt.documents))


@router.get("/evidence-options")
def evidence_options(
    project_id: str = "project-rag",
    evidence_type: str | None = None,
    query: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return _call(lambda: rt.documents.evidence_options(project_id, evidence_type, query, limit))


@router.post("/claims", status_code=201, dependencies=write)
def create_claim(
    request: ClaimCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    document = _call(lambda: rt.documents._require_document(request.document_id))
    require_project_access(rt, access, document["project_id"])
    return _call(lambda: rt.documents.create_claim(request))


@router.get("/claims/by-id")
def claim(
    claim_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.documents._require_claim(claim_id))
    require_project_access(rt, access, item["project_id"])
    return item


@router.patch("/claims/by-id", dependencies=write)
def update_claim(
    claim_id: str,
    request: ClaimUpdate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.documents._require_claim(claim_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.documents.update_claim(claim_id, request))


@router.post("/claims/evidence", status_code=201, dependencies=write)
def add_evidence(
    request: ClaimEvidenceCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.documents._require_claim(request.claim_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.documents.add_evidence(request))


@router.post("/claims/evidence/unlink", dependencies=write)
def unlink_evidence(
    request: ClaimEvidenceUnlink,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.documents._require_claim(request.claim_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.documents.unlink_evidence(request))


@router.post("/claims/validate", dependencies=write)
def validate_claim(
    claim_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.documents._require_claim(claim_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.documents.validate(claim_id))
