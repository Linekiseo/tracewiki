from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import (
    AccessContext,
    access_context,
    get_runtime,
    mutation_auth,
    require_project_access,
)
from .models import CodexComparisonCreate
from .service import CodexComparisonError

router = APIRouter(prefix="/v1/codex/comparisons", tags=["codex-comparisons"])


@router.post("", status_code=201, dependencies=[Depends(mutation_auth)])
def compare(
    request: CodexComparisonCreate,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(runtime, access, request.project_id)
    for thread_id in request.thread_ids:
        thread = runtime.store.get_codex_thread(thread_id)
        if not thread or not access.allows(thread["acl_ref"]):
            raise HTTPException(status_code=404, detail="Codex thread not found")
    try:
        return runtime.codex_comparisons.compare(request)
    except CodexComparisonError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_comparisons(
    project_id: str = "project-rag",
    limit: int = Query(default=100, ge=1, le=500),
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(runtime, access, project_id)
    return runtime.codex_comparisons.store.list(project_id, limit)


@router.get("/by-id")
def comparison(
    comparison_id: str,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = runtime.codex_comparisons.store.get(comparison_id)
    if not item:
        raise HTTPException(status_code=404, detail="Codex comparison not found")
    require_project_access(runtime, access, item["project_id"])
    return item
