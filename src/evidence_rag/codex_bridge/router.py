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
from .models import (
    ApprovalDecision,
    ClientCreate,
    ExecutionClaim,
    ExecutionCreate,
    ExecutionReport,
    TokenCreate,
)
from .runner import ExecRunner
from .service import CodexBridgeError

router = APIRouter(prefix="/v1/codex-bridge", tags=["codex-bridge"])
write = [Depends(mutation_auth)]


def _call(operation):
    try:
        return operation()
    except CodexBridgeError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=detail) from exc


@router.get("/status")
def bridge_status(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return rt.codex_bridge.status(project_id)


@router.get("/agents")
def list_agents(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.codex_bridge.agents(project_id)


@router.get("/agents/audit")
def agent_audit(
    project_id: str = "project-rag",
    client_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return rt.codex_bridge.agent_audit(project_id, client_id=client_id, limit=limit)


@router.post("/clients", status_code=201, dependencies=write)
def create_client(request: ClientCreate, rt: Runtime = Depends(get_runtime)) -> dict:
    from uuid import uuid4

    return rt.codex_bridge.store.upsert_client(
        f"client://{uuid4().hex}", request.name, request.version
    )


@router.post("/tokens", status_code=201, dependencies=write)
def create_token(
    request: TokenCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.codex_bridge.issue_token(request))


@router.delete("/tokens/{token_id:path}", dependencies=write)
def revoke_token(
    token_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    token = rt.codex_bridge.store.get_token(token_id)
    if not token:
        raise HTTPException(status_code=404, detail="token not found")
    require_project_access(rt, access, token["project_id"])
    if not rt.codex_bridge.store.revoke_token(token_id):
        raise HTTPException(status_code=404, detail="token not found")
    return {"id": token_id, "revoked": True}


@router.get("/executions")
def list_executions(
    project_id: str = "project-rag",
    execution_status: str | None = None,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.codex_bridge.store.list_executions(project_id, status=execution_status)


@router.post("/executions", status_code=201, dependencies=write)
def create_execution(
    request: ExecutionCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.codex_bridge.create_execution(request))


@router.get("/executions/by-id")
def get_execution(
    execution_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    execution = _call(lambda: rt.codex_bridge._execution(execution_id))
    require_project_access(rt, access, execution["project_id"])
    return execution


@router.post("/executions/claim", dependencies=write)
def claim_execution(
    execution_id: str,
    request: ExecutionClaim,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    execution = _call(lambda: rt.codex_bridge._execution(execution_id))
    require_project_access(rt, access, execution["project_id"])
    return _call(
        lambda: rt.codex_bridge.claim_execution(execution_id, request.thread_id, request.client_id)
    )


@router.post("/executions/report", dependencies=write)
def report_execution(
    execution_id: str,
    request: ExecutionReport,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    execution = _call(lambda: rt.codex_bridge._execution(execution_id))
    require_project_access(rt, access, execution["project_id"])
    return _call(lambda: rt.codex_bridge.report_execution(execution_id, request))


@router.get("/approvals")
def list_approvals(
    project_id: str = "project-rag",
    approval_status: str | None = "pending",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.codex_bridge.store.list_approvals(project_id, approval_status)


@router.post("/approvals/decide", dependencies=write)
def decide_approval(
    approval_id: str,
    request: ApprovalDecision,
    background_tasks: BackgroundTasks,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    approval = rt.codex_bridge.store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="pending approval not found")
    require_project_access(rt, access, approval["project_id"])
    approval, runnable = _call(lambda: rt.codex_bridge.decide_approval(approval_id, request))
    if runnable:
        background_tasks.add_task(ExecRunner(rt.codex_bridge).run, runnable)
    return approval
