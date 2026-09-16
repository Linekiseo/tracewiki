from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from ..dependencies import (
    AccessContext,
    access_context,
    get_runtime,
    mutation_auth,
    require_project_access,
)
from ..runtime import Runtime
from .models import BindingBulkReviewRequest, BindingReviewRequest, BindingScanRequest
from .service import BindingError

router = APIRouter(prefix="/v1/bindings", tags=["cross-source-bindings"])
TRUSTED_BINDING_REVIEWER = "rag-api:binding-review"


@router.get("/stats")
def stats(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return rt.bindings.store.stats(project_id)


@router.post("/scan", dependencies=[Depends(mutation_auth)])
def scan(
    request: BindingScanRequest,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return rt.bindings.scan(request)


@router.post(
    "/scan/background",
    dependencies=[Depends(mutation_auth)],
    status_code=status.HTTP_202_ACCEPTED,
)
def scan_background(
    request: BindingScanRequest,
    background_tasks: BackgroundTasks,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    scan_id = rt.bindings.start_scan(request)
    background_tasks.add_task(rt.bindings.run_scan, scan_id, request)
    return {"scan_id": scan_id, "status": "running", "counts": {}}


@router.get("/scan/status")
def scan_status(
    scan_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    result = rt.bindings.store.get_scan(scan_id)
    if not result:
        raise HTTPException(status_code=404, detail="binding scan not found")
    require_project_access(rt, access, result["project_id"])
    return result


@router.get("/candidates")
def candidates(
    project_id: str = "project-rag",
    review_status: str | None = None,
    thread_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.bindings.store.list_candidates(
        project_id, review_status=review_status, thread_id=thread_id, limit=limit
    )


@router.get("/unmatched")
def unmatched(
    project_id: str = "project-rag",
    limit: int = Query(default=200, ge=1, le=1000),
    rt=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.bindings.store.list_unmatched(project_id, limit)


@router.get("/by-id")
def candidate(
    binding_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    result = rt.bindings.store.get_candidate(binding_id)
    if not result:
        raise HTTPException(status_code=404, detail="binding candidate not found")
    require_project_access(rt, access, result["project_id"])
    return result


@router.post("/review", dependencies=[Depends(mutation_auth)])
def review(
    binding_id: str,
    request: BindingReviewRequest,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    result = rt.bindings.store.get_candidate(binding_id)
    if not result:
        raise HTTPException(status_code=404, detail="binding candidate not found")
    require_project_access(rt, access, result["project_id"])
    # Review provenance is server authority. A browser-supplied reviewer is never
    # trusted, even when the caller is allowed to invoke this mutation.
    trusted_request = request.model_copy(
        update={"reviewer": TRUSTED_BINDING_REVIEWER},
    )
    try:
        return rt.bindings.review(binding_id, trusted_request)
    except BindingError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.post("/review-all", dependencies=[Depends(mutation_auth)])
def review_all(
    request: BindingBulkReviewRequest,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    try:
        return rt.bindings.review_all(
            request.project_id,
            expected_pending=request.expected_pending,
            reviewer=TRUSTED_BINDING_REVIEWER,
            note=request.note,
        )
    except BindingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
