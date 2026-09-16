from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from ..dependencies import (
    AccessContext,
    access_context,
    get_runtime,
    mutation_auth,
    require_project_access,
)
from ..rag.codex_v2 import CODEX_ENGINE_HEADER, CodexEngineOverrideError
from ..rag.document_v2 import DOCUMENT_ENGINE_HEADER, DocumentEngineOverrideError
from ..rag.experiment_v2 import EXPERIMENT_ENGINE_HEADER, ExperimentEngineOverrideError
from ..rag.multisource_runtime_v2 import (
    GLOBAL_ENGINE_HEADER,
    GlobalEngineOverrideError,
    validate_global_engine_override,
)
from ..rag.notebook_v2 import NOTEBOOK_ENGINE_HEADER, NotebookEngineOverrideError
from ..rag.sources.code import CODE_ENGINE_HEADER, CodeEngineOverrideError
from ..rag.workspace_v2 import WORKSPACE_ENGINE_HEADER, WorkspaceEngineOverrideError
from ..runtime import Runtime
from .models import DriftScanRequest, GlobalSearchRequest

router = APIRouter(prefix="/v1", tags=["platform"])


@router.post("/search")
def search(
    request: GlobalSearchRequest,
    code_engine: Annotated[str | None, Header(alias=CODE_ENGINE_HEADER)] = None,
    codex_engine: Annotated[str | None, Header(alias=CODEX_ENGINE_HEADER)] = None,
    experiment_engine: Annotated[str | None, Header(alias=EXPERIMENT_ENGINE_HEADER)] = None,
    notebook_engine: Annotated[str | None, Header(alias=NOTEBOOK_ENGINE_HEADER)] = None,
    document_engine: Annotated[str | None, Header(alias=DOCUMENT_ENGINE_HEADER)] = None,
    workspace_engine: Annotated[str | None, Header(alias=WORKSPACE_ENGINE_HEADER)] = None,
    global_engine: Annotated[str | None, Header(alias=GLOBAL_ENGINE_HEADER)] = None,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    request.allowed_acl_refs = list(access.acl_refs)
    request.enforce_acl = access.enforced
    try:
        rt.code_integration.validate_override(code_engine)
        validate_global_engine_override(global_engine)
        return rt.global_v2.search(
            request,
            engine_override=global_engine,
            fallback=lambda: rt.platform.search(
                request,
                code_engine_override=code_engine,
                codex_engine_override=codex_engine,
                experiment_engine_override=experiment_engine,
                notebook_engine_override=notebook_engine,
                document_engine_override=document_engine,
                workspace_engine_override=workspace_engine,
            ),
        )
    except (
        CodeEngineOverrideError,
        CodexEngineOverrideError,
        ExperimentEngineOverrideError,
        NotebookEngineOverrideError,
        DocumentEngineOverrideError,
        WorkspaceEngineOverrideError,
        GlobalEngineOverrideError,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/lineage")
def lineage(
    entity_id: str,
    depth: Annotated[int, Query(ge=1, le=4)] = 2,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    return rt.platform.lineage(
        entity_id,
        depth,
        allowed_acl_refs=list(access.acl_refs),
        enforce_acl=access.enforced,
    )


@router.get("/graph/neighbors")
def graph_neighbors(
    entity_id: str,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    return rt.platform.neighbors(
        entity_id,
        cursor,
        limit,
        allowed_acl_refs=list(access.acl_refs),
        enforce_acl=access.enforced,
    )


@router.get("/graph")
def graph(
    domain: Literal["overview", "code", "codex", "research", "document"] = "overview",
    mode: Literal["relations", "semantic"] = "relations",
    query: str = "",
    project_id: str = "project-rag",
    limit: Annotated[int, Query(ge=1, le=500)] = 36,
    focus_entity_id: str = "",
    relation_types: Annotated[list[str] | None, Query()] = None,
    direction: Literal["both", "incoming", "outgoing"] = "both",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return rt.platform.graph(
        project_id,
        domain,
        mode,
        query,
        limit,
        focus_entity_id=focus_entity_id,
        relation_types=relation_types,
        direction=direction,
        allowed_acl_refs=list(access.acl_refs),
        enforce_acl=access.enforced,
    )


@router.get("/graph/stats")
def graph_stats(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return {
        domain: rt.platform.store.graph_metadata(project_id, domain)
        for domain in ("overview", "code", "codex", "research", "document")
    }


@router.post("/drift/scan", dependencies=[Depends(mutation_auth)])
def scan_drift(
    request: DriftScanRequest,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    assessments = rt.platform.store.scan_drift(request.project_id, request.update_claim_status)
    return {"status": "completed", "count": len(assessments), "assessments": assessments}


@router.get("/drift/assessments")
def drift(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.platform.store.list_drift(project_id)
