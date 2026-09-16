from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import AccessContext, access_context, get_runtime, mutation_auth
from .models import HistorySyncRequest, TestResultCreate
from .service import CodeHistoryError

router = APIRouter(prefix="/v1/code", tags=["code-history"])


def _require_repository(runtime, access: AccessContext, repository_id: str) -> dict:
    repository = next(
        (item for item in runtime.store.list_repositories() if item["id"] == repository_id),
        None,
    )
    if not repository or not access.allows(repository["acl_ref"]):
        raise HTTPException(status_code=404, detail="repository not found")
    return repository


@router.post("/history/sync", dependencies=[Depends(mutation_auth)])
def sync_history(
    request: HistorySyncRequest,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    _require_repository(runtime, access, request.repository_id)
    try:
        return runtime.code_history.sync(
            request.repository_id, request.depth, request.include_diffs
        )
    except CodeHistoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/history")
def list_history(
    repository_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    _require_repository(runtime, access, repository_id)
    return runtime.code_history.store.list_commits(repository_id, limit)


@router.get("/refs")
def refs(
    repository_id: str,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    _require_repository(runtime, access, repository_id)
    try:
        return runtime.code_history.refs(repository_id)
    except CodeHistoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/compare")
def compare(
    repository_id: str,
    base_ref: str,
    target_ref: str,
    path: str | None = None,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    _require_repository(runtime, access, repository_id)
    try:
        return runtime.code_history.compare(repository_id, base_ref, target_ref, path)
    except CodeHistoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/history/commit")
def commit_detail(
    repository_id: str,
    sha: str,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    _require_repository(runtime, access, repository_id)
    item = runtime.code_history.store.commit_detail(repository_id, sha)
    if not item:
        raise HTTPException(status_code=404, detail="commit not found")
    return item


@router.post("/test-results", status_code=201, dependencies=[Depends(mutation_auth)])
def create_test_result(
    request: TestResultCreate,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    _require_repository(runtime, access, request.repository_id)
    try:
        return runtime.code_history.record_test(request)
    except CodeHistoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
