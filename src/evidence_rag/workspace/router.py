from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..dependencies import (
    AccessContext,
    access_context,
    get_runtime,
    mutation_auth,
    require_project_access,
)
from ..runtime import Runtime
from .models import (
    IterationCreate,
    IterationLinkCreate,
    IterationUpdate,
    ProjectCreate,
    ProjectUpdate,
    RelationCreate,
    RelationReview,
    TopicCreate,
    TopicUpdate,
    WorkItemCreate,
    WorkItemTransition,
    WorkItemUpdate,
)
from .service import WorkspaceError

router = APIRouter(prefix="/v1", tags=["research-workspace"])
write = [Depends(mutation_auth)]


def _call(operation):
    try:
        return operation()
    except WorkspaceError as exc:
        detail = str(exc)
        code = status.HTTP_404_NOT_FOUND if "not found" in detail else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=detail) from exc


@router.get("/projects")
def list_projects(
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    return [
        project
        for project in rt.workspace.store.list_projects()
        if access.allows(project["acl_ref"])
    ]


@router.post("/projects", status_code=201, dependencies=write)
def create_project(
    request: ProjectCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    if access.enforced and (not request.acl_ref or not access.allows(request.acl_ref)):
        raise HTTPException(status_code=404, detail="project not found")
    return _call(lambda: rt.workspace.create_project(request))


@router.get("/projects/by-id")
def get_project(
    project_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    return require_project_access(rt, access, project_id)


@router.patch("/projects/by-id", dependencies=write)
def update_project(
    project_id: str,
    request: ProjectUpdate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return _call(lambda: rt.workspace.update_project(project_id, request))


@router.get("/workspace/dashboard")
def dashboard(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return _call(lambda: rt.workspace.dashboard(project_id))


@router.get("/workspace/intelligence")
def workspace_intelligence(
    project_id: str = "project-rag",
    topic_id: str | None = None,
    iteration_id: str | None = None,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return _call(
        lambda: rt.intelligence.analyze(
            project_id,
            topic_id=topic_id,
            iteration_id=iteration_id,
        )
    )


@router.get("/research/topics")
def list_topics(
    project_id: str = "project-rag",
    topic_status: str | None = Query(default=None, alias="status"),
    query: str | None = None,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.workspace.store.list_topics(project_id, status=topic_status, query=query)


@router.post("/research/topics", status_code=201, dependencies=write)
def create_topic(
    request: TopicCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.workspace.create_topic(request))


@router.get("/research/topics/by-id")
def get_topic(
    topic_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_topic(topic_id))
    require_project_access(rt, access, item["project_id"])
    return item


@router.patch("/research/topics/by-id", dependencies=write)
def update_topic(
    topic_id: str,
    request: TopicUpdate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_topic(topic_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.workspace.update_topic(topic_id, request))


@router.delete("/research/topics/by-id", dependencies=write)
def delete_topic(
    topic_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_topic(topic_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.workspace.delete_topic(topic_id))


@router.get("/research/iterations")
def list_iterations(
    project_id: str = "project-rag",
    topic_id: str | None = None,
    iteration_status: str | None = Query(default=None, alias="status"),
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.workspace.store.list_iterations(
        project_id, topic_id=topic_id, status=iteration_status
    )


@router.post("/research/iterations", status_code=201, dependencies=write)
def create_iteration(
    request: IterationCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.workspace.create_iteration(request))


@router.get("/research/iterations/by-id")
def get_iteration(
    iteration_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_iteration(iteration_id))
    require_project_access(rt, access, item["project_id"])
    return item


@router.patch("/research/iterations/by-id", dependencies=write)
def update_iteration(
    iteration_id: str,
    request: IterationUpdate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_iteration(iteration_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.workspace.update_iteration(iteration_id, request))


@router.delete("/research/iterations/by-id", dependencies=write)
def delete_iteration(
    iteration_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_iteration(iteration_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.workspace.delete_iteration(iteration_id))


@router.get("/research/work-items")
def list_work_items(
    project_id: str = "project-rag",
    topic_id: str | None = None,
    iteration_id: str | None = None,
    work_status: str | None = Query(default=None, alias="status"),
    assignee_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.workspace.store.list_work_items(
        project_id,
        topic_id=topic_id,
        iteration_id=iteration_id,
        status=work_status,
        assignee_type=assignee_type,
        limit=limit,
    )


@router.post("/research/work-items", status_code=201, dependencies=write)
def create_work_item(
    request: WorkItemCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.workspace.create_work_item(request))


@router.get("/research/work-items/by-id")
def get_work_item(
    work_item_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_work_item(work_item_id))
    require_project_access(rt, access, item["project_id"])
    return item


@router.patch("/research/work-items/by-id", dependencies=write)
def update_work_item(
    work_item_id: str,
    request: WorkItemUpdate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_work_item(work_item_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.workspace.update_work_item(work_item_id, request))


@router.delete("/research/work-items/by-id", dependencies=write)
def delete_work_item(
    work_item_id: str,
    expected_version: Annotated[int, Query(ge=1)],
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_work_item(work_item_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.workspace.delete_work_item(work_item_id, expected_version))


@router.post("/research/work-items/transition", dependencies=write)
def transition_work_item(
    work_item_id: str,
    request: WorkItemTransition,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_work_item(work_item_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.workspace.transition_work_item(work_item_id, request))


@router.get("/research/iteration-links")
def list_iteration_links(
    iteration_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    iteration = _call(lambda: rt.workspace._require_iteration(iteration_id))
    require_project_access(rt, access, iteration["project_id"])
    return rt.workspace.store.list_iteration_links(iteration_id)


@router.post("/research/iteration-links", status_code=201, dependencies=write)
def create_iteration_link(
    request: IterationLinkCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.workspace._require_iteration(request.iteration_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.workspace.link_iteration(request))


@router.delete("/research/iteration-links", status_code=204, dependencies=write)
def delete_iteration_link(
    link_id: str,
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> None:
    require_project_access(rt, access, project_id)
    if not rt.workspace.unlink_iteration(link_id, project_id):
        raise HTTPException(status_code=404, detail="iteration link not found")


@router.get("/relations")
def list_relations(
    project_id: str = "project-rag",
    entity_id: str | None = None,
    review_status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.workspace.store.list_relations(
        project_id, entity_id=entity_id, review_status=review_status, limit=limit
    )


@router.post("/relations", status_code=201, dependencies=write)
def create_relation(
    request: RelationCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.workspace.create_relation(request))


@router.post("/relations/review", dependencies=write)
def review_relation(
    edge_id: str,
    request: RelationReview,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = rt.workspace.store.get_relation(edge_id)
    if not item:
        raise HTTPException(status_code=404, detail="relation not found")
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.workspace.review_relation(edge_id, request))


@router.get("/audit/events")
def audit_events(
    project_id: str | None = "project-rag",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    if project_id:
        require_project_access(rt, access, project_id)
    elif access.enforced:
        raise HTTPException(status_code=404, detail="project not found")
    return rt.workspace.store.list_audits(project_id, limit)
