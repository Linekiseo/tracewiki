from __future__ import annotations

import hashlib
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from . import __version__
from .bindings.router import router as bindings_router
from .code_history.router import router as code_history_router
from .codex_bridge.mcp import router as codex_mcp_router
from .codex_bridge.router import router as codex_bridge_router
from .codex_comparisons.router import router as codex_comparisons_router
from .codex_discovery import list_visible_codex_threads, public_repository_binding
from .config import Settings
from .dependencies import (
    AccessContext,
    access_context,
    get_runtime,
    mutation_auth,
    require_project_access,
)
from .documents.router import router as documents_router
from .evaluation.router import router as evaluation_router
from .experiments.router import router as experiments_router
from .models import (
    CodexIngestRequest,
    CodexProjectSyncRequest,
    CodexSearchRequest,
    EvidenceSearchRequest,
    QueryRequest,
    RepositoryIngestRequest,
)
from .notebooks.router import router as notebooks_router
from .platform.models import (
    ExperimentSourceQueryRequestV2,
    GlobalSearchRequest,
    NotebookSourceQueryRequestV2,
)
from .platform.router import router as platform_router
from .platform.service import PlatformTypedRequestError
from .query.provider import LLMProviderConfiguration, LLMProviderProjectRequest
from .rag.codex_v2 import (
    CODEX_CONTEXT_VERSION,
    CODEX_ENGINE_HEADER,
    CODEX_V2_VERSION,
    CodexEngineOverrideError,
    validate_codex_engine_override,
)
from .rag.document_v2 import (
    DOCUMENT_ENGINE_HEADER,
    DocumentEngineOverrideError,
    validate_document_engine_override,
)
from .rag.experiment_v2 import (
    EXPERIMENT_ENGINE_HEADER,
    ExperimentEngineOverrideError,
    validate_experiment_engine_override,
)
from .rag.multisource_runtime_v2 import (
    GLOBAL_ENGINE_HEADER,
    GlobalEngineOverrideError,
    validate_global_engine_override,
)
from .rag.notebook_v2 import (
    NOTEBOOK_ENGINE_HEADER,
    NotebookEngineOverrideError,
    validate_notebook_engine_override,
)
from .rag.release_control_plane_v2 import (
    RuntimeReleaseStatusV2,
    current_runtime_release_status_v2,
)
from .rag.sources.code import CODE_ENGINE_HEADER, CodeEngineOverrideError
from .rag.sources.codex.runtime_v2 import CodexSourceScopeErrorV2
from .rag.wiki.query_v1 import (
    WIKI_QUERY_ENGINE_HEADER,
    WikiQueryError,
    validate_wiki_query_engine_override,
)
from .rag.workspace_v2 import (
    WORKSPACE_ENGINE_HEADER,
    WorkspaceEngineOverrideError,
    validate_workspace_engine_override,
)
from .runtime import Runtime, create_runtime
from .sources.router import router as sources_router
from .wiki.router import router as wiki_router
from .workspace.router import router as workspace_router


def _search_codex_source_runtime_v2(
    *,
    runtime: Runtime,
    request: CodexSearchRequest,
    engine_requested: Literal["v1", "v2"] = "v2",
) -> dict:
    """Project the complete Codex source facade onto the additive direct API.

    Platform and the direct endpoint deliberately share the same
    ``_retrieve_source`` path.  That path owns the lazy registry operation, so
    an omitted V2 header still cannot materialize a derived root.
    """

    project_id = request.scope.project_id or "project-rag"
    response = runtime.platform._retrieve_source(
        source="codex",
        request=GlobalSearchRequest(
            query=request.query,
            project_id=project_id,
            sources=["codex"],
            limit=request.limit,
            thread_ids=request.scope.thread_ids,
            date_from=request.scope.date_from,
            date_to=request.scope.date_to,
            allowed_acl_refs=request.scope.allowed_acl_refs,
            enforce_acl=request.scope.enforce_acl,
        ),
        limit=request.limit,
        engine_requested=engine_requested,
        codex_scope=request.scope,
        legacy_fallback=lambda: runtime.codex_retriever.search(request),
    )
    release_route = dict(response.get("trace", {}).get("release_route") or {})
    if release_route.get("served_engine") != "v2":
        return response
    role_to_item_type = {
        "goal": "UserGoal",
        "proposal": "Plan",
        "action": "CommandExecution",
        "result": "ToolResult",
        "change": "FileChange",
        "validation": "ValidationResult",
        "reported_outcome": "AgentMessage",
        "episode": "DevelopmentEpisode",
    }
    results = []
    for raw in response["results"]:
        item = dict(raw)
        role = str(item.get("evidence_role") or item.get("entity_type") or "context")
        typed_episode_id = str(item.get("episode_id") or "")
        if not typed_episode_id.startswith("codex-v2://episode/"):
            compatibility_episode = hashlib.sha256(
                "\x1f".join(
                    (
                        str(item.get("thread_id") or ""),
                        typed_episode_id,
                        str(item.get("entity_id") or ""),
                    )
                ).encode()
            ).hexdigest()
            metadata = dict(item.get("metadata") or {})
            metadata["v2_episode_id"] = typed_episode_id or None
            item["metadata"] = metadata
            item["episode_id"] = f"codex-v2://episode/{compatibility_episode}"
        metadata = dict(item.get("metadata") or {})
        authoritative_item_type = metadata.get("authoritative_item_type")
        item["item_type"] = (
            authoritative_item_type
            if isinstance(authoritative_item_type, str) and authoritative_item_type
            else role_to_item_type.get(role, role)
        )
        item.setdefault("view_type", "codex_source_context_v2")
        item.setdefault("name", item.get("title"))
        item.setdefault("role", role)
        item.setdefault("evidence_locator", item.get("locator"))
        item.setdefault("edges", [])
        results.append(item)
    context = dict(response.get("context") or {})
    context.update(
        {
            "schema_version": CODEX_CONTEXT_VERSION,
            "reasoning_included": False,
        }
    )
    trace = dict(response["trace"])
    trace.setdefault("engine", CODEX_V2_VERSION)
    return {
        "query_id": f"q-codex-source-v2-{secrets.token_hex(16)}",
        "query": request.query,
        "resolved_scope": request.scope.model_dump(),
        "index_generation": list(trace.get("index_generation") or []),
        "total": len(results),
        "results": results,
        "context": context,
        "trace": trace,
    }


def create_app(
    settings: Settings | None = None,
    *,
    defer_runtime: bool = False,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    runtime = None if defer_runtime else create_runtime(resolved_settings)
    if runtime is not None:
        runtime.store.recover_interrupted_workflows()
    runtime_lock = Lock()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            active_runtime = application.state.runtime
            if active_runtime is not None:
                active_runtime.close()

    app = FastAPI(
        title="多源研发证据 RAG",
        version=__version__,
        description="Version-aware code and Codex session evidence ingestion and retrieval API.",
        lifespan=lifespan,
    )
    # Tauri uses an OS webview origin rather than the API's HTTP origin.  Keep
    # this list exact: browser deployments remain same-origin and arbitrary web
    # pages cannot turn a local desktop service into a cross-origin API proxy.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "X-Actor",
            "X-RAG-ACL-Refs",
            "X-Request-ID",
            "X-RAG-Code-Engine",
            "X-RAG-Codex-Engine",
            "X-RAG-Experiment-Engine",
            "X-RAG-Notebook-Engine",
            "X-RAG-Document-Engine",
            "X-RAG-Workspace-Engine",
            "X-RAG-Global-Engine",
            "X-RAG-Wiki-Engine",
        ],
    )
    app.state.runtime = runtime
    app.include_router(workspace_router)
    app.include_router(bindings_router)
    app.include_router(experiments_router)
    app.include_router(documents_router)
    app.include_router(platform_router)
    app.include_router(evaluation_router)
    app.include_router(sources_router)
    app.include_router(code_history_router)
    app.include_router(notebooks_router)
    app.include_router(codex_comparisons_router)
    app.include_router(codex_bridge_router)
    app.include_router(codex_mcp_router)
    app.include_router(wiki_router)

    read_post_paths = {
        "/v1/search",
        "/v1/evidence/search",
        "/v1/codex/search",
        "/v1/experiments/search",
        "/v1/notebooks/search",
        "/v1/query",
        "/v1/wiki/search",
        "/v1/wiki/navigate",
    }

    @app.middleware("http")
    async def audit_mutations(request: Request, call_next):
        request_runtime: Runtime | None = request.app.state.runtime
        if request_runtime is None and request.url.path != "/v1/rag/status":
            with runtime_lock:
                request_runtime = request.app.state.runtime
                if request_runtime is None:
                    request_runtime = create_runtime(resolved_settings)
                    request_runtime.store.recover_interrupted_workflows()
                    request.app.state.runtime = request_runtime
        request_settings = (
            request_runtime.settings if request_runtime is not None else resolved_settings
        )
        if request_settings.deployment_mode == "production" and request.url.path.startswith("/v1/"):
            supplied = request.headers.get("authorization", "")
            if supplied.lower().startswith("bearer "):
                supplied = supplied[7:]
            expected = request_settings.api_token or ""
            if not supplied or not secrets.compare_digest(supplied, expected):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "invalid or missing bearer token"},
                )
        response = await call_next(request)
        is_write = request.method in {"PATCH", "PUT", "DELETE"} or (
            request.method == "POST" and request.url.path not in read_post_paths
        )
        if is_write and request.url.path.startswith("/v1/") and request_runtime is not None:
            try:
                from uuid import uuid4

                request_runtime.workspace.store.create_audit(
                    {
                        "id": f"audit://{uuid4().hex}",
                        "project_id": "project-rag",
                        "actor": request.headers.get("x-actor", "api-user")[:160],
                        "action": f"api.{request.method.lower()}",
                        "resource_type": "api_endpoint",
                        "resource_id": request.url.path,
                        "detail": {"status_code": response.status_code},
                        "trace_id": request.headers.get("x-request-id") or uuid4().hex,
                    }
                )
            except Exception:
                pass
        return response

    @app.get("/health")
    def health(rt: Runtime = Depends(get_runtime)) -> dict[str, str]:
        rt.store.stats()
        return {"status": "ok", "version": __version__}

    @app.get("/v1/stats")
    def stats(rt: Runtime = Depends(get_runtime)) -> dict:
        return rt.store.stats()

    @app.get("/v1/rag/status", response_model=RuntimeReleaseStatusV2)
    def rag_status(request: Request) -> RuntimeReleaseStatusV2:
        runtime_status: Runtime | None = request.app.state.runtime
        return (
            runtime_status.rag_status()
            if runtime_status is not None
            else current_runtime_release_status_v2()
        )

    @app.get("/v1/codex/config")
    def codex_config(rt: Runtime = Depends(get_runtime)) -> dict:
        return rt.codex_ingestion.defaults()

    @app.get("/v1/codex/stats")
    def codex_stats(rt: Runtime = Depends(get_runtime)) -> dict:
        return rt.store.codex_stats()

    @app.post(
        "/v1/ingestion/repositories",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(mutation_auth)],
    )
    def ingest_repository(
        request: RepositoryIngestRequest,
        background_tasks: BackgroundTasks,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict[str, str]:
        if not rt.workspace.store.get_project(request.project_id) or not access.allows(
            request.acl_ref
        ):
            raise HTTPException(status_code=404, detail="project not found")
        workflow_id = rt.ingestion.enqueue(request)
        background_tasks.add_task(rt.ingestion.run, workflow_id, request)
        return {"workflow_id": workflow_id, "status": "queued"}

    @app.post(
        "/v1/ingestion/codex",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(mutation_auth)],
    )
    def ingest_codex(
        request: CodexIngestRequest,
        background_tasks: BackgroundTasks,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict[str, str]:
        if not rt.workspace.store.get_project(request.project_id) or not access.allows(
            request.acl_ref
        ):
            raise HTTPException(status_code=404, detail="project not found")
        workflow_id = rt.codex_ingestion.enqueue(request)
        background_tasks.add_task(rt.codex_ingestion.run, workflow_id, request)
        return {"workflow_id": workflow_id, "status": "queued"}

    @app.get("/v1/ingestion/workflows")
    def list_workflows(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        kind: Literal["repository", "codex"] | None = None,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> list[dict]:
        return [
            item
            for item in rt.store.list_workflows(limit, kind=kind)
            if access.allows(item["request"].get("acl_ref"))
        ]

    @app.post(
        "/v1/ingestion/workflows/{workflow_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(mutation_auth)],
    )
    def retry_workflow(
        workflow_id: str,
        background_tasks: BackgroundTasks,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict[str, str]:
        item = rt.store.get_workflow(workflow_id)
        if not item or not access.allows(item["request"].get("acl_ref")):
            raise HTTPException(status_code=404, detail="workflow not found")
        if item["status"] not in {"failed", "completed"}:
            raise HTTPException(status_code=409, detail="workflow is still active")
        if item["kind"] == "codex":
            request = CodexIngestRequest.model_validate(item["request"])
            replacement_id = rt.codex_ingestion.enqueue(request)
            background_tasks.add_task(rt.codex_ingestion.run, replacement_id, request)
        else:
            request = RepositoryIngestRequest.model_validate(item["request"])
            replacement_id = rt.ingestion.enqueue(request)
            background_tasks.add_task(rt.ingestion.run, replacement_id, request)
        return {
            "workflow_id": replacement_id,
            "status": "queued",
            "retries": workflow_id,
        }

    @app.get("/v1/ingestion/workflows/{workflow_id}")
    def workflow(
        workflow_id: str,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        item = rt.store.get_workflow(workflow_id)
        if not item or not access.allows(item["request"].get("acl_ref")):
            raise HTTPException(status_code=404, detail="workflow not found")
        return item

    @app.get("/v1/repositories")
    def repositories(
        project_id: str | None = None,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> list[dict]:
        if project_id:
            require_project_access(rt, access, project_id)
        return [
            public_repository_binding(item)
            for item in rt.store.list_repositories()
            if access.allows(item["acl_ref"])
            and (project_id is None or item["project_id"] == project_id)
        ]

    @app.delete(
        "/v1/repositories/by-id",
        dependencies=[Depends(mutation_auth)],
    )
    def delete_repository(
        repository_id: str,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        repository = next(
            (item for item in rt.store.list_repositories() if item["id"] == repository_id),
            None,
        )
        if repository is None or not access.allows(repository["acl_ref"]):
            raise HTTPException(status_code=404, detail="repository not found")
        require_project_access(rt, access, repository["project_id"])
        try:
            result = rt.store.delete_repository(repository_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="repository not found")

        local_path = Path(repository["local_path"]).resolve()
        repository_cache = rt.settings.repository_cache.resolve()
        if (
            repository.get("source_url")
            and local_path != repository_cache
            and local_path.is_relative_to(repository_cache)
            and local_path.is_dir()
        ):
            shutil.rmtree(local_path)

        return {
            "id": repository_id,
            "deleted": True,
            "removed": result["removed"],
        }

    @app.get("/v1/codex/sources")
    def codex_sources(
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> list[dict]:
        return [item for item in rt.store.list_codex_sources() if access.allows(item["acl_ref"])]

    @app.get("/v1/codex/projects/{project_id}/sync-status")
    def codex_project_sync_status(
        project_id: str,
        include_archived: bool = False,
        max_sessions: Annotated[int, Query(ge=1, le=2_000)] = 2_000,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        project = require_project_access(rt, access, project_id)
        workspace_path = str(project.get("settings", {}).get("workspace_path") or "").strip()
        return rt.codex_ingestion.project_sync_status(
            project_id,
            project_path=workspace_path or None,
            include_archived=include_archived,
            max_sessions=max_sessions,
        )

    @app.post(
        "/v1/codex/projects/{project_id}/sync",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(mutation_auth)],
    )
    def sync_codex_project(
        project_id: str,
        request: CodexProjectSyncRequest,
        background_tasks: BackgroundTasks,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        project = require_project_access(rt, access, project_id)
        workspace_path = str(project.get("settings", {}).get("workspace_path") or "").strip()
        sync_status = rt.codex_ingestion.project_sync_status(
            project_id,
            project_path=workspace_path or None,
            include_archived=request.include_archived,
            max_sessions=request.max_sessions,
        )
        if sync_status["active_workflow_id"]:
            return {
                **sync_status,
                "workflow_id": sync_status["active_workflow_id"],
                "status": "syncing",
            }
        if not request.force and not sync_status["needs_sync"]:
            return {
                **sync_status,
                "workflow_id": None,
                "status": "up_to_date",
            }

        ingest_request = rt.codex_ingestion.project_sync_request(
            project_id,
            project_path=workspace_path or None,
            include_archived=request.include_archived,
            max_sessions=request.max_sessions,
            acl_ref=project["acl_ref"],
        )
        workflow_id = rt.codex_ingestion.enqueue(ingest_request)
        background_tasks.add_task(rt.codex_ingestion.run, workflow_id, ingest_request)
        return {
            **sync_status,
            "workflow_id": workflow_id,
            "status": "queued",
        }

    @app.get("/v1/codex/sessions")
    def codex_sessions(
        project_id: str | None = None,
        session_status: str | None = Query(default=None, alias="status"),
        query: str | None = None,
        include_subagents: bool = False,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> list[dict]:
        return list_visible_codex_threads(
            rt.store,
            project_id=project_id,
            status=session_status,
            query=query,
            include_subagents=include_subagents,
            acl_enforced=access.enforced,
            acl_refs=access.acl_refs,
            limit=limit,
            offset=offset,
        )

    @app.get("/v1/codex/sessions/{thread_id}")
    def codex_session(
        thread_id: str,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        item = rt.store.get_codex_thread(thread_id)
        if not item or not access.allows(item["acl_ref"]):
            raise HTTPException(status_code=404, detail="Codex session not found")
        return item

    @app.get("/v1/codex/sessions/{thread_id}/timeline")
    def codex_session_timeline(
        thread_id: str,
        turn_offset: Annotated[int, Query(ge=0)] = 0,
        turn_limit: Annotated[int, Query(ge=1, le=50)] = 24,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        item = rt.store.get_codex_thread_timeline(thread_id)
        if not item or not access.allows(item["acl_ref"]):
            raise HTTPException(status_code=404, detail="Codex session not found")
        all_turns = list(item.get("turns") or [])
        newest_first = list(reversed(all_turns))
        selected = list(reversed(newest_first[turn_offset : turn_offset + turn_limit]))
        item["turns"] = selected
        item["visible_turn_count"] = len(selected)
        item["turn_offset"] = turn_offset
        item["turn_limit"] = turn_limit
        item["has_more_turns"] = turn_offset + len(selected) < len(all_turns)
        return item

    @app.get("/v1/codex/sessions/{thread_id}/turn-audit")
    def codex_session_turn_audit(
        thread_id: str,
        turn_id: str,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        item = rt.store.get_codex_turn_audit(thread_id, turn_id)
        if not item or not access.allows(item["acl_ref"]):
            raise HTTPException(status_code=404, detail="Codex session turn not found")
        return item

    @app.post("/v1/codex/search")
    def codex_search(
        request: CodexSearchRequest,
        codex_engine: Annotated[str | None, Header(alias=CODEX_ENGINE_HEADER)] = None,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        request.scope.allowed_acl_refs = list(access.acl_refs)
        request.scope.enforce_acl = access.enforced
        try:
            engine = validate_codex_engine_override(codex_engine)
            return _search_codex_source_runtime_v2(
                runtime=rt,
                request=request,
                engine_requested=engine,
            )
        except (CodexEngineOverrideError, CodexSourceScopeErrorV2) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/experiments/search")
    def experiment_source_search(
        request: ExperimentSourceQueryRequestV2,
        experiment_engine: Annotated[str | None, Header(alias=EXPERIMENT_ENGINE_HEADER)] = None,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        try:
            engine = validate_experiment_engine_override(experiment_engine)
            require_project_access(rt, access, request.project_id)
            return rt.platform.query_experiment_v2(
                request,
                allowed_acl_refs=list(access.acl_refs),
                enforce_acl=access.enforced,
                engine_requested=engine,
            )
        except (ExperimentEngineOverrideError, PlatformTypedRequestError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/notebooks/search")
    def notebook_source_search(
        request: NotebookSourceQueryRequestV2,
        notebook_engine: Annotated[str | None, Header(alias=NOTEBOOK_ENGINE_HEADER)] = None,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        try:
            engine = validate_notebook_engine_override(notebook_engine)
            require_project_access(rt, access, request.project_id)
            return rt.platform.query_notebook_v2(
                request,
                allowed_acl_refs=list(access.acl_refs),
                enforce_acl=access.enforced,
                engine_requested=engine,
            )
        except (NotebookEngineOverrideError, PlatformTypedRequestError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/code/files")
    def files(
        repository_id: str,
        ref: str | None = None,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> list[dict]:
        repository = next(
            (item for item in rt.store.list_repositories() if item["id"] == repository_id), None
        )
        if not repository or not access.allows(repository["acl_ref"]):
            raise HTTPException(status_code=404, detail="repository not found")
        if ref and ref != "HEAD":
            try:
                return rt.code_history.list_tree(repository_id, ref)
            except Exception as exc:
                from .code_history.service import CodeHistoryError

                if isinstance(exc, CodeHistoryError):
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                raise
        return rt.store.list_files(repository_id)

    @app.get("/v1/code/file")
    def file_detail(
        repository_id: str,
        path: str,
        ref: str | None = None,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        if ref and ref != "HEAD":
            repository = next(
                (row for row in rt.store.list_repositories() if row["id"] == repository_id),
                None,
            )
            if not repository or not access.allows(repository["acl_ref"]):
                raise HTTPException(status_code=404, detail="file not found")
            try:
                return rt.code_history.read_file(repository_id, ref, path)
            except Exception as exc:
                from .code_history.service import CodeHistoryError

                if isinstance(exc, CodeHistoryError):
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                raise
        item = rt.store.get_file(repository_id, path)
        if not item or not access.allows(item["acl_ref"]):
            raise HTTPException(status_code=404, detail="file not found")
        return item

    @app.get("/v1/entities/by-id")
    def entity(  # noqa: A002
        id: str,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        item = rt.store.get_entity(id)
        if not item or not access.allows(item["acl_ref"]):
            raise HTTPException(status_code=404, detail="entity not found")
        item["edges"] = rt.store.edges_for_entities([id], [item["repository_id"]])
        return item

    @app.post("/v1/evidence/search")
    def evidence_search(
        request: EvidenceSearchRequest,
        code_engine: Annotated[str | None, Header(alias=CODE_ENGINE_HEADER)] = None,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        request.scope.allowed_acl_refs = list(access.acl_refs)
        request.scope.enforce_acl = access.enforced
        try:
            return rt.code_integration.search(
                request,
                engine_override=code_engine,
                shadow_runner=rt.code_shadow,
            )
        except CodeEngineOverrideError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/query")
    def query(
        request: QueryRequest,
        code_engine: Annotated[str | None, Header(alias=CODE_ENGINE_HEADER)] = None,
        codex_engine: Annotated[str | None, Header(alias=CODEX_ENGINE_HEADER)] = None,
        experiment_engine: Annotated[str | None, Header(alias=EXPERIMENT_ENGINE_HEADER)] = None,
        notebook_engine: Annotated[str | None, Header(alias=NOTEBOOK_ENGINE_HEADER)] = None,
        document_engine: Annotated[str | None, Header(alias=DOCUMENT_ENGINE_HEADER)] = None,
        workspace_engine: Annotated[str | None, Header(alias=WORKSPACE_ENGINE_HEADER)] = None,
        global_engine: Annotated[str | None, Header(alias=GLOBAL_ENGINE_HEADER)] = None,
        wiki_engine: Annotated[str | None, Header(alias=WIKI_QUERY_ENGINE_HEADER)] = None,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        try:
            request.scope.allowed_acl_refs = list(access.acl_refs)
            request.scope.enforce_acl = access.enforced
            validate_wiki_query_engine_override(wiki_engine)
            if wiki_engine is not None:
                if any(
                    item is not None
                    for item in (
                        code_engine,
                        codex_engine,
                        experiment_engine,
                        notebook_engine,
                        document_engine,
                        workspace_engine,
                        global_engine,
                    )
                ):
                    raise WikiQueryError(
                        "Wiki query engine cannot be combined with source or global engine overrides"
                    )
                project_id = request.scope.project_id or "project-rag"
                project = require_project_access(rt, access, project_id)
                return rt.wiki_query.answer(
                    request,
                    requester_acl_refs=(str(project["acl_ref"]),),
                )
            rt.code_integration.validate_override(code_engine)
            validate_codex_engine_override(codex_engine)
            validate_experiment_engine_override(experiment_engine)
            validate_notebook_engine_override(notebook_engine)
            validate_document_engine_override(document_engine)
            validate_workspace_engine_override(workspace_engine)
            validate_global_engine_override(global_engine)
            return rt.answers.answer(
                request,
                code_engine_override=code_engine,
                codex_engine_override=codex_engine,
                experiment_engine_override=experiment_engine,
                notebook_engine_override=notebook_engine,
                document_engine_override=document_engine,
                workspace_engine_override=workspace_engine,
                global_engine_override=global_engine,
            )
        except (
            CodeEngineOverrideError,
            CodexEngineOverrideError,
            ExperimentEngineOverrideError,
            NotebookEngineOverrideError,
            DocumentEngineOverrideError,
            WorkspaceEngineOverrideError,
            GlobalEngineOverrideError,
            WikiQueryError,
        ) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            # Provider exceptions can contain prompts, credentials, ACLs, or absolute paths.
            # The public contract exposes only a stable code; diagnostics remain server-side.
            raise HTTPException(status_code=502, detail="answer_provider_failed") from exc

    @app.post("/v1/query/preview")
    def query_preview(
        request: QueryRequest,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        project_id = request.scope.project_id or "project-rag"
        require_project_access(rt, access, project_id)
        request.scope.allowed_acl_refs = list(access.acl_refs)
        request.scope.enforce_acl = access.enforced
        return rt.answers.preview(request)

    @app.get("/v1/ai/provider")
    def llm_provider_status(
        project_id: str = "project-rag",
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        require_project_access(rt, access, project_id)
        return rt.llm_providers.status(project_id)

    @app.put(
        "/v1/ai/provider",
        dependencies=[Depends(mutation_auth)],
    )
    def configure_llm_provider(
        configuration: LLMProviderConfiguration,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        require_project_access(rt, access, configuration.project_id)
        return rt.llm_providers.configure(configuration)

    @app.delete(
        "/v1/ai/provider",
        dependencies=[Depends(mutation_auth)],
    )
    def clear_llm_provider(
        request: LLMProviderProjectRequest,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        require_project_access(rt, access, request.project_id)
        return rt.llm_providers.clear(request.project_id)

    @app.post(
        "/v1/ai/provider/test",
        dependencies=[Depends(mutation_auth)],
    )
    def test_llm_provider(
        request: LLMProviderProjectRequest,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        require_project_access(rt, access, request.project_id)
        try:
            return rt.llm_providers.test(request.project_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="llm_provider_test_failed") from exc

    @app.get("/v1/query/history")
    def query_history(
        project_id: str = "project-rag",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        rt: Runtime = Depends(get_runtime),
        access: AccessContext = Depends(access_context),
    ) -> dict:
        require_project_access(rt, access, project_id)
        return rt.answers.history(project_id, limit)

    web_dir = resolved_settings.web_dir
    if web_dir.is_dir():
        assets_dir = web_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
        app_assets_dir = web_dir / "app"
        if app_assets_dir.is_dir():
            app.mount("/app", StaticFiles(directory=app_assets_dir), name="app-assets")

        @app.get("/", include_in_schema=False)
        def web_index() -> FileResponse:
            return FileResponse(web_dir / "index.html")

        @app.get("/legacy", include_in_schema=False)
        def legacy_web_index() -> FileResponse:
            legacy_path = web_dir / "legacy.html"
            return FileResponse(legacy_path if legacy_path.is_file() else web_dir / "index.html")

    return app


app = create_app(defer_runtime=True)
