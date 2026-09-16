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
from .models import (
    ComparisonRequest,
    ExperimentCreate,
    ExperimentUpdate,
    MLflowSyncRequest,
    RunCreate,
    RunUpdate,
)
from .service import ExperimentError

router = APIRouter(prefix="/v1/experiments", tags=["experiments"])
write = [Depends(mutation_auth)]


def _call(operation):
    try:
        return operation()
    except ExperimentError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/stats")
def stats(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, project_id)
    return rt.experiments.store.stats(project_id)


@router.get("/sources")
def sources(
    project_id: str = "project-rag",
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.experiments.store.list_sources(project_id)


@router.post("/sources/mlflow/sync", dependencies=write)
def sync_mlflow(
    request: MLflowSyncRequest,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.experiments.sync_mlflow(request))


@router.get("")
def experiments(
    project_id: str = "project-rag",
    status: str | None = None,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.experiments.store.list_experiments(project_id, status)


@router.post("", status_code=201, dependencies=write)
def create_experiment(
    request: ExperimentCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    require_project_access(rt, access, request.project_id)
    return _call(lambda: rt.experiments.create_experiment(request))


@router.get("/by-id")
def experiment(
    experiment_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.experiments._require_experiment(experiment_id))
    require_project_access(rt, access, item["project_id"])
    return item


@router.patch("/by-id", dependencies=write)
def update_experiment(
    experiment_id: str,
    request: ExperimentUpdate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.experiments._require_experiment(experiment_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.experiments.update_experiment(experiment_id, request))


@router.get("/runs")
def runs(
    project_id: str = "project-rag",
    experiment_id: str | None = None,
    status: str | None = None,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> list[dict]:
    require_project_access(rt, access, project_id)
    return rt.experiments.store.list_runs(project_id, experiment_id=experiment_id, status=status)


@router.post("/runs", status_code=201, dependencies=write)
def create_run(
    request: RunCreate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    experiment = _call(lambda: rt.experiments._require_experiment(request.experiment_id))
    require_project_access(rt, access, experiment["project_id"])
    return _call(lambda: rt.experiments.create_run(request))


@router.get("/runs/by-id")
def run(
    run_id: str,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.experiments._require_run(run_id))
    require_project_access(rt, access, item["project_id"])
    return item


@router.patch("/runs/by-id", dependencies=write)
def update_run(
    run_id: str,
    request: RunUpdate,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    item = _call(lambda: rt.experiments._require_run(run_id))
    require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.experiments.update_run(run_id, request))


@router.post("/comparisons", status_code=201, dependencies=write)
def compare(
    request: ComparisonRequest,
    rt: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    for run_id in request.run_ids:
        item = _call(lambda run_id=run_id: rt.experiments._require_run(run_id))
        require_project_access(rt, access, item["project_id"])
    return _call(lambda: rt.experiments.compare(request))
