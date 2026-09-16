from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from ..dependencies import (
    AccessContext,
    access_context,
    get_runtime,
    mutation_auth,
    require_project_access,
)
from ..rag.wiki.builder_v1 import WikiBuilderError
from ..rag.wiki.compiler_v1 import WikiCompilerError
from ..rag.wiki.live_organization_v1 import (
    WikiLiveOrganizationError,
    preview_live_wiki_organization_v1,
    stage_live_wiki_organization_v1,
)
from ..rag.wiki.navigator_v1 import WikiNavigationError
from ..rag.wiki.runtime_v1 import WikiRuntimeError
from ..rag.wiki.search_v1 import WikiSearchError
from ..rag.wiki.store_v1 import WikiPublicationError, WikiStoreError
from ..runtime import Runtime
from .models import (
    WikiCompileApiRequest,
    WikiNavigateApiRequest,
    WikiOrganizationStageApiRequest,
    WikiPatchEvaluationApiRequest,
    WikiPatchProposalApiRequest,
    WikiPatchStageApiRequest,
    WikiPublishApiRequest,
    WikiRollbackApiRequest,
    WikiSearchApiRequest,
)

router = APIRouter(prefix="/v1/wiki", tags=["agent-native-wiki"])
_WIKI_ERRORS = (
    WikiBuilderError,
    WikiCompilerError,
    WikiNavigationError,
    WikiPublicationError,
    WikiRuntimeError,
    WikiSearchError,
    WikiStoreError,
    WikiLiveOrganizationError,
    ValidationError,
)


def _acl_scope(
    runtime: Runtime,
    access: AccessContext,
    project_id: str,
) -> tuple[str, ...]:
    project = require_project_access(runtime, access, project_id)
    project_acl = str(project["acl_ref"])
    if access.enforced and not access.allows(project_acl):
        raise HTTPException(status_code=404, detail="project not found")
    # Wiki fragments are compiled as single visibility partitions.  Project-scoped
    # reads therefore select the governed project partition, not caller-supplied
    # foreign ACL labels or a synthetic union partition.
    return (project_acl,)


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.get("/status")
def wiki_status(
    project_id: Annotated[str, Query(min_length=1, max_length=240)],
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    acl_refs = _acl_scope(runtime, access, project_id)
    return runtime.wiki.status(
        project_id=project_id,
        requester_acl_refs=acl_refs,
    ).model_dump(mode="json")


@router.get("/organization/preview")
def wiki_organization_preview(
    project_id: Annotated[str, Query(min_length=1, max_length=240)],
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    """Inventory and compile current project knowledge without writing a generation."""

    acl_refs = _acl_scope(runtime, access, project_id)
    try:
        preview, _, _ = preview_live_wiki_organization_v1(
            runtime,
            project_id=project_id,
            acl_ref=acl_refs[0],
        )
        return preview.model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error


@router.post("/organization/stage", status_code=status.HTTP_202_ACCEPTED)
def wiki_organization_stage(
    request: WikiOrganizationStageApiRequest,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
    _: None = Depends(mutation_auth),
) -> dict:
    """Stage a deterministic current-project Wiki; never switch the active pointer."""

    acl_refs = _acl_scope(runtime, access, request.project_id)
    try:
        return stage_live_wiki_organization_v1(
            runtime,
            project_id=request.project_id,
            acl_ref=acl_refs[0],
        ).model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error


@router.post("/search")
def wiki_search(
    request: WikiSearchApiRequest,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    acl_refs = _acl_scope(runtime, access, request.project_id)
    try:
        return runtime.wiki.search(
            request_id=f"wiki-search-{uuid4().hex}",
            project_id=request.project_id,
            requester_acl_refs=acl_refs,
            query=request.query,
            query_class=request.query_class,
            required_sources=tuple(
                sorted(set(request.required_sources), key=lambda item: item.value)
            ),
            required_roles=tuple(sorted(set(request.required_roles))),
            generation_id=request.generation_id,
            top_k=request.top_k,
            candidate_limit=request.candidate_limit,
        ).model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error


@router.post("/navigate")
def wiki_navigate(
    request: WikiNavigateApiRequest,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    acl_refs = _acl_scope(runtime, access, request.project_id)
    try:
        navigation_request = runtime.wiki.build_navigation_request(
            request_id=f"wiki-navigation-{uuid4().hex}",
            project_id=request.project_id,
            requester_acl_refs=acl_refs,
            query=request.query,
            query_class=request.query_class,
            required_roles=tuple(sorted(set(request.required_roles))),
            required_sources=tuple(
                sorted(set(request.required_sources), key=lambda item: item.value)
            ),
            as_of=request.as_of,
            max_searches=request.max_searches,
            max_page_reads=request.max_page_reads,
            max_link_hops=request.max_link_hops,
            max_raw_reads=request.max_raw_reads,
            empty_search_patience=request.empty_search_patience,
            retrieval_deadline_ms=request.retrieval_deadline_ms,
            top_k_per_search=request.top_k_per_search,
            token_budget=request.token_budget,
            require_raw_evidence=request.require_raw_evidence,
        )
        return runtime.wiki.navigate(navigation_request).model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error


@router.get("/pages")
def wiki_pages(
    project_id: Annotated[str, Query(min_length=1, max_length=240)],
    generation_id: Annotated[str | None, Query(max_length=240)] = None,
    q: Annotated[str, Query(max_length=1_000)] = "",
    page_type: Annotated[str | None, Query(max_length=80)] = None,
    source: Annotated[str | None, Query(max_length=80)] = None,
    role: Annotated[str | None, Query(max_length=128)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    acl_refs = _acl_scope(runtime, access, project_id)
    total, pages, selected_generation = runtime.wiki.browse_pages(
        project_id=project_id,
        requester_acl_refs=acl_refs,
        generation_id=generation_id,
        query=q,
        page_type=page_type,
        source=source,
        role=role,
        offset=offset,
        limit=limit,
    )
    return {
        "project_id": project_id,
        "generation_id": selected_generation,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(pages) if offset + len(pages) < total else None,
        "pages": [item.model_dump(mode="json") for item in pages],
    }


@router.get("/read")
def wiki_read(
    project_id: Annotated[str, Query(min_length=1, max_length=240)],
    path: Annotated[str, Query(min_length=1, max_length=2_000)],
    generation_id: Annotated[str | None, Query(max_length=240)] = None,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    acl_refs = _acl_scope(runtime, access, project_id)
    try:
        record = runtime.wiki.read(
            project_id=project_id,
            requester_acl_refs=acl_refs,
            logical_path=path,
            generation_id=generation_id,
        )
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error
    if record is None:
        raise HTTPException(status_code=404, detail="Wiki path not found")
    return record.model_dump(mode="json")


@router.get("/follow")
def wiki_follow(
    project_id: Annotated[str, Query(min_length=1, max_length=240)],
    path: Annotated[str, Query(min_length=1, max_length=2_000)],
    relation: Annotated[str | None, Query(max_length=128)] = None,
    generation_id: Annotated[str | None, Query(max_length=240)] = None,
    limit: Annotated[int, Query(ge=1, le=64)] = 32,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    acl_refs = _acl_scope(runtime, access, project_id)
    try:
        return runtime.wiki.follow_links(
            project_id=project_id,
            requester_acl_refs=acl_refs,
            logical_path=path,
            relation=relation,
            generation_id=generation_id,
            limit=limit,
        ).model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error


@router.get("/evidence")
def wiki_evidence(
    project_id: Annotated[str, Query(min_length=1, max_length=240)],
    page_path: Annotated[str, Query(min_length=1, max_length=2_000)],
    source_ref_id: Annotated[str, Query(min_length=1, max_length=240)],
    generation_id: Annotated[str | None, Query(max_length=240)] = None,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    acl_refs = _acl_scope(runtime, access, project_id)
    try:
        return runtime.wiki.read_evidence(
            project_id=project_id,
            requester_acl_refs=acl_refs,
            page_path=page_path,
            source_ref_id=source_ref_id,
            generation_id=generation_id,
        ).model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error


@router.get("/error-book")
def wiki_error_book(
    project_id: Annotated[str, Query(min_length=1, max_length=240)],
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    _acl_scope(runtime, access, project_id)
    entries = runtime.wiki.store.list_error_book(project_id=project_id)
    return {"total": len(entries), "entries": [item.model_dump(mode="json") for item in entries]}


@router.get("/builder/patches")
def wiki_builder_patches(
    project_id: Annotated[str, Query(min_length=1, max_length=240)],
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    _acl_scope(runtime, access, project_id)
    patches = runtime.wiki.store.list_builder_patches(project_id=project_id)
    return {"total": len(patches), "patches": [item.model_dump(mode="json") for item in patches]}


@router.get("/builder/patches/{patch_id}/decision")
def wiki_builder_patch_decision(
    patch_id: str,
    project_id: Annotated[str, Query(min_length=1, max_length=240)],
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
) -> dict:
    _acl_scope(runtime, access, project_id)
    decision = runtime.wiki.store.read_builder_decision(
        project_id=project_id,
        patch_id=patch_id,
    )
    if decision is None:
        raise HTTPException(status_code=404, detail="Wiki Builder decision not found")
    return decision.model_dump(mode="json")


@router.post("/compile", status_code=status.HTTP_202_ACCEPTED)
def wiki_compile(
    request: WikiCompileApiRequest,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
    _: None = Depends(mutation_auth),
) -> dict:
    _acl_scope(runtime, access, request.project_id)
    try:
        result = runtime.wiki.compile_to_staging(request)
        return result.model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error


@router.post("/publish")
def wiki_publish(
    request: WikiPublishApiRequest,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
    _: None = Depends(mutation_auth),
) -> dict:
    _acl_scope(runtime, access, request.project_id)
    try:
        manifest = runtime.wiki.publish_generation(
            project_id=request.project_id,
            generation_id=request.generation_id,
            expected_manifest_sha256=request.expected_manifest_sha256,
            reviewer_authority_sha256=request.reviewer_authority_sha256,
        )
        return manifest.model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/rollback")
def wiki_rollback(
    request: WikiRollbackApiRequest,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
    _: None = Depends(mutation_auth),
) -> dict:
    _acl_scope(runtime, access, request.project_id)
    try:
        manifest = runtime.wiki.rollback_generation(
            project_id=request.project_id,
            target_generation_id=request.target_generation_id,
            expected_target_manifest_sha256=request.expected_target_manifest_sha256,
            expected_active_generation_id=request.expected_active_generation_id,
            expected_active_manifest_sha256=request.expected_active_manifest_sha256,
            reviewer_authority_sha256=request.reviewer_authority_sha256,
        )
        return manifest.model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/builder/patches", status_code=status.HTTP_202_ACCEPTED)
def wiki_propose_patch(
    request: WikiPatchProposalApiRequest,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
    _: None = Depends(mutation_auth),
) -> dict:
    _acl_scope(runtime, access, request.project_id)
    try:
        runtime.wiki.propose_patch(project_id=request.project_id, patch=request.patch)
        return {"patch_id": request.patch.patch_id, "state": "staged_for_review"}
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error


@router.post("/builder/patches/{patch_id}/evaluate")
def wiki_evaluate_patch(
    patch_id: str,
    request: WikiPatchEvaluationApiRequest,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
    _: None = Depends(mutation_auth),
) -> dict:
    _acl_scope(runtime, access, request.project_id)
    try:
        return runtime.wiki.evaluate_patch(
            project_id=request.project_id,
            patch_id=patch_id,
            trial=request.trial,
        ).model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error


@router.post("/builder/patches/{patch_id}/stage", status_code=status.HTTP_202_ACCEPTED)
def wiki_stage_patch(
    patch_id: str,
    request: WikiPatchStageApiRequest,
    runtime: Runtime = Depends(get_runtime),
    access: AccessContext = Depends(access_context),
    _: None = Depends(mutation_auth),
) -> dict:
    _acl_scope(runtime, access, request.project_id)
    try:
        return runtime.wiki.stage_builder_generation(
            project_id=request.project_id,
            patch_id=patch_id,
            generation_id=request.generation_id,
        ).model_dump(mode="json")
    except _WIKI_ERRORS as error:
        raise _bad_request(error) from error
