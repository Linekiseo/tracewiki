from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import (
    AccessContext,
    access_context,
    get_runtime,
    mutation_auth,
    require_project_access,
)
from ..runtime import Runtime
from .models import CodeEvaluationRunRequest, EvaluationCaseCreate, EvaluationRunRequest
from .service import EvaluationError

router = APIRouter(prefix="/v1/evaluation", tags=["evaluation"])


@router.get("/stats")
def stats(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return rt.evaluation.store.stats(project_id)


@router.get("/cases")
def cases(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.evaluation.store.list_cases(project_id)


@router.post("/cases", status_code=201, dependencies=[Depends(mutation_auth)])
def create_case(
    request: EvaluationCaseCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return rt.evaluation.create_case(request)


@router.get("/runs")
def runs(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.evaluation.store.list_runs(project_id)


@router.get("/runs/by-id")
def run(
    run_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    result = rt.evaluation.store.get_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="evaluation run not found")
    require_project_access(rt, access, result["project_id"])
    return result


@router.post("/runs", dependencies=[Depends(mutation_auth)])
def run_evaluation(
    request: EvaluationRunRequest,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    try:
        return rt.evaluation.run(request)
    except EvaluationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/code", dependencies=[Depends(mutation_auth)])
def run_code_evaluation(
    request: CodeEvaluationRunRequest,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    try:
        return rt.evaluation.run_code(
            request,
            allowed_acl_refs=list(access.acl_refs),
            enforce_acl=access.enforced,
        )
    except EvaluationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
