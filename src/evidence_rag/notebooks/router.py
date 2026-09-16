from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import (
    AccessContext,
    access_context,
    get_runtime,
    mutation_auth,
    require_project_access,
)
from .models import NotebookCompareRequest, NotebookIngestRequest
from .service import NotebookError

router = APIRouter(prefix="/v1/notebooks", tags=["notebooks"])


def _call(operation):
    try:
        return operation()
    except NotebookError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/ingest", status_code=201, dependencies=[Depends(mutation_auth)])
def ingest(
    request: NotebookIngestRequest,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(runtime, access, request.project_id)
    return _call(lambda: runtime.notebooks.ingest(request))


@router.get("/runs")
def runs(
    project_id: str = "project-rag",
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(runtime, access, project_id)
    return runtime.notebooks.store.list_runs(
        project_id,
        allowed_acl_refs=access.acl_refs,
        enforce_acl=access.enforced,
    )


@router.get("/runs/by-id")
def run(
    notebook_run_id: str,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = runtime.notebooks.store.get_run(
        notebook_run_id,
        allowed_acl_refs=access.acl_refs,
        enforce_acl=access.enforced,
    )
    if not item:
        raise HTTPException(status_code=404, detail="notebook run not found")
    return item


@router.post("/compare")
def compare(
    request: NotebookCompareRequest,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    for notebook_run_id in (request.baseline_id, request.candidate_id):
        item = runtime.notebooks.store.get_run(
            notebook_run_id,
            allowed_acl_refs=access.acl_refs,
            enforce_acl=access.enforced,
        )
        if not item:
            raise HTTPException(status_code=404, detail="notebook run not found")
    return _call(lambda: runtime.notebooks.compare(request))
