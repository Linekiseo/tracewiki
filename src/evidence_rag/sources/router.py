from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..dependencies import AccessContext, access_context, get_runtime, mutation_auth
from .models import RawTombstoneRequest, SourceEventInput

router = APIRouter(prefix="/v1/ingestion", tags=["source-events-and-raw"])


@router.post("/events", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(mutation_auth)])
def ingest_event(
    request: SourceEventInput,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    if not access.allows(request.acl_ref):
        raise HTTPException(status_code=404, detail="project not found")
    return runtime.sources.accept(request)


@router.get("/events")
def list_events(
    project_id: str = "project-rag",
    limit: int = Query(default=100, ge=1, le=1000),
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    return [
        item
        for item in runtime.sources.store.list_events(project_id, limit)
        if access.allows(item["acl_ref"])
    ]


@router.get("/raw")
def list_raw(
    project_id: str = "project-rag",
    state: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    return [
        item
        for item in runtime.sources.store.list_objects(project_id, state=state, limit=limit)
        if access.allows(item["acl_ref"])
    ]


@router.get("/raw/by-id")
def raw_by_id(  # noqa: A002
    id: str,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = runtime.sources.store.get_object(id)
    if not item or not access.allows(item["acl_ref"]):
        raise HTTPException(status_code=404, detail="raw object not found")
    return item


@router.post("/raw/tombstone", dependencies=[Depends(mutation_auth)])
def tombstone_raw(
    request: RawTombstoneRequest,
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    raw = runtime.sources.store.get_object(request.raw_object_id)
    if not raw or not access.allows(raw["acl_ref"]):
        raise HTTPException(status_code=404, detail="raw object not found")
    item = runtime.sources.store.tombstone(request.raw_object_id, request.reason, request.actor)
    if not item:
        raise HTTPException(status_code=404, detail="raw object not found")
    return item


@router.get("/raw/stats")
def raw_stats(
    project_id: str = "project-rag",
    runtime=Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    project = runtime.workspace.store.get_project(project_id)
    if not project or not access.allows(project["acl_ref"]):
        return {
            "objects": 0,
            "active": 0,
            "quarantined": 0,
            "tombstoned": 0,
            "bytes": 0,
            "events": 0,
            "blocked_entities": 0,
            "by_type": {},
        }
    return runtime.sources.store.stats(project_id)
