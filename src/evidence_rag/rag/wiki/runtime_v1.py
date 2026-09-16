"""Governed product runtime for the agent-native Wiki data plane."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..answer_v2 import EvidenceFactV2
from .builder_v1 import (
    WIKI_BUILDER_AUTHORITY_SHA256,
    WikiBuilderDecisionStatusV1,
    WikiBuilderDecisionV1,
    WikiBuilderPatchV1,
    WikiBuilderTrialV1,
    WikiPatchedGenerationV1,
    apply_reviewed_wiki_patch_v1,
    build_wiki_builder_generation_base_v1,
    evaluate_wiki_builder_patch_v1,
    resolve_wiki_error_book_v1,
)
from .compiler_v1 import (
    WIKI_COMPILER_AUTHORITY_SHA256,
    WikiCompilationRequestV1,
    WikiCompilationResultV1,
    compile_wiki_v1,
)
from .contracts_v1 import (
    WikiDirectoryV1,
    WikiLinkStatusV1,
    WikiLinkV1,
    WikiPageFragmentV1,
    WikiRecordKindV1,
    WikiSourceDomainV1,
    WikiSourceRefV1,
    canonical_sha256_v1,
)
from .evidence_gateway_v1 import StoreRawEvidenceGatewayV1
from .navigator_v1 import (
    WikiNavigationRequestV1,
    WikiNavigationResultV1,
    WikiNavigatorV1,
    build_wiki_navigation_request_v1,
)
from .search_v1 import (
    WikiDenseProviderV1,
    WikiHybridSearchV1,
    WikiQueryClassV1,
    WikiRerankerV1,
    WikiSearchRequestV1,
    WikiSearchResponseV1,
    build_wiki_search_request_v1,
    classify_wiki_query_v1,
)
from .store_v1 import WikiStoreV1

WIKI_RUNTIME_VERSION = "agent-native-wiki-product-runtime-v1"


class WikiRuntimeError(ValueError):
    """Raised when a product operation violates Wiki governance."""


class _FrozenRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class WikiRuntimeStatusV1(_FrozenRuntime):
    project_id: str
    availability: Literal["AVAILABLE", "UNAVAILABLE"]
    active_generation_id: str | None
    manifest_sha256: str | None
    compiler_authority_sha256: str | None
    source_generations: tuple[tuple[str, str], ...]
    visible_page_count: int
    error_book_count: int
    pending_patch_count: int
    sparse_availability: Literal["AVAILABLE"] = "AVAILABLE"
    dense_availability: Literal["AVAILABLE", "UNAVAILABLE"]
    reranker_availability: Literal["AVAILABLE", "UNAVAILABLE"]
    default_engine: Literal["v1"] = "v1"
    wiki_opt_in_engine: Literal["wiki_v1"] = "wiki_v1"
    quality_state: Literal["QUALITY_HOLD"] = "QUALITY_HOLD"
    runtime_version: Literal[WIKI_RUNTIME_VERSION] = WIKI_RUNTIME_VERSION
    content_sha256: str


class WikiFollowTargetV1(_FrozenRuntime):
    link: WikiLinkV1
    page: WikiPageFragmentV1


class WikiFollowResultV1(_FrozenRuntime):
    project_id: str
    generation_id: str
    source_path: str
    targets: tuple[WikiFollowTargetV1, ...]
    unresolved_target_paths: tuple[str, ...]
    content_sha256: str


class WikiRawEvidenceResultV1(_FrozenRuntime):
    project_id: str
    generation_id: str
    page_path: str
    source_ref: WikiSourceRefV1
    evidence: EvidenceFactV2
    content_sha256: str


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class WikiRuntimeV1:
    """Online reads plus explicitly reviewed nearline/offline mutations.

    Search and navigation never compile, mutate, or switch generations.  Compilation,
    Builder replay, and publication are separate methods so an API or Agent cannot
    accidentally turn one read request into an online self-edit.
    """

    def __init__(
        self,
        *,
        store: WikiStoreV1,
        dense_provider: WikiDenseProviderV1 | None = None,
        reranker: WikiRerankerV1 | None = None,
    ) -> None:
        self.store = store
        self.store.initialize()
        self.search_engine = WikiHybridSearchV1(
            store,
            dense_provider=dense_provider,
            reranker=reranker,
        )
        self.dense_provider = dense_provider
        self.reranker = reranker
        self.authority_sha256 = canonical_sha256_v1(
            {
                "builder": WIKI_BUILDER_AUTHORITY_SHA256,
                "compiler": WIKI_COMPILER_AUTHORITY_SHA256,
                "dense": dense_provider.authority_sha256 if dense_provider else None,
                "reranker": reranker.authority_sha256 if reranker else None,
                "runtime": WIKI_RUNTIME_VERSION,
            }
        )

    def status(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
    ) -> WikiRuntimeStatusV1:
        manifest = self.store.active_manifest(project_id)
        pages = (
            self.store.list_pages(
                project_id=project_id,
                requester_acl_refs=requester_acl_refs,
                generation_id=manifest.generation_id,
            )
            if manifest is not None
            else ()
        )
        errors = self.store.list_error_book(project_id=project_id)
        patches = self.store.list_builder_patches(project_id=project_id)
        payload = {
            "project_id": project_id,
            "availability": "AVAILABLE" if manifest is not None else "UNAVAILABLE",
            "active_generation_id": manifest.generation_id if manifest else None,
            "manifest_sha256": manifest.content_sha256 if manifest else None,
            "compiler_authority_sha256": (manifest.compiler_authority_sha256 if manifest else None),
            "source_generations": (
                tuple(
                    (item.source.value, item.generation_id) for item in manifest.source_generations
                )
                if manifest
                else ()
            ),
            "visible_page_count": len(pages),
            "error_book_count": len(errors),
            "pending_patch_count": len(patches),
            "sparse_availability": "AVAILABLE",
            "dense_availability": "AVAILABLE" if self.dense_provider else "UNAVAILABLE",
            "reranker_availability": "AVAILABLE" if self.reranker else "UNAVAILABLE",
            "default_engine": "v1",
            "wiki_opt_in_engine": "wiki_v1",
            "quality_state": "QUALITY_HOLD",
            "runtime_version": WIKI_RUNTIME_VERSION,
        }
        return WikiRuntimeStatusV1(
            **payload,
            content_sha256=canonical_sha256_v1(payload),
        )

    def compile_to_staging(
        self,
        request: WikiCompilationRequestV1,
    ) -> WikiCompilationResultV1:
        result = compile_wiki_v1(
            request,
            previous_error_book=self.store.list_error_book(project_id=request.project_id),
        )
        self.store.stage_generation(
            result.manifest,
            result.records,
            candidates=request.candidates,
            error_book=result.error_book,
            compilation_result=result,
        )
        return result

    def publish_generation(
        self,
        *,
        project_id: str,
        generation_id: str,
        expected_manifest_sha256: str,
        reviewer_authority_sha256: str,
        published_at: str | None = None,
    ) -> object:
        if (
            not reviewer_authority_sha256.startswith("sha256:")
            or len(reviewer_authority_sha256) != 71
        ):
            raise WikiRuntimeError("Wiki publication requires canonical reviewer authority")
        manifest = self.store.verify_generation(
            project_id=project_id,
            generation_id=generation_id,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        if manifest.compiler_authority_sha256 == WIKI_BUILDER_AUTHORITY_SHA256:
            decision = self.store.builder_staging_decision(
                project_id=project_id,
                generation_id=generation_id,
            )
            if (
                decision is None
                or decision.status is not WikiBuilderDecisionStatusV1.PROMOTE_TO_STAGING
                or decision.hard_guard_failures
                or decision.regressed_guard_queries
            ):
                raise WikiRuntimeError("Builder generation lacks a guard-clean reviewed decision")
        elif manifest.compiler_authority_sha256 != WIKI_COMPILER_AUTHORITY_SHA256:
            raise WikiRuntimeError("Wiki generation compiler authority is not reviewed")
        return self.store.publish_generation(
            project_id=project_id,
            generation_id=generation_id,
            expected_manifest_sha256=expected_manifest_sha256,
            published_at=published_at or _now(),
        )

    def rollback_generation(
        self,
        *,
        project_id: str,
        target_generation_id: str,
        expected_target_manifest_sha256: str,
        expected_active_generation_id: str,
        expected_active_manifest_sha256: str,
        reviewer_authority_sha256: str,
        rolled_back_at: str | None = None,
    ) -> object:
        if (
            not reviewer_authority_sha256.startswith("sha256:")
            or len(reviewer_authority_sha256) != 71
        ):
            raise WikiRuntimeError("Wiki rollback requires canonical reviewer authority")
        target = self.store.verify_generation(
            project_id=project_id,
            generation_id=target_generation_id,
            expected_manifest_sha256=expected_target_manifest_sha256,
        )
        if target.compiler_authority_sha256 not in {
            WIKI_COMPILER_AUTHORITY_SHA256,
            WIKI_BUILDER_AUTHORITY_SHA256,
        }:
            raise WikiRuntimeError("Wiki rollback target authority is not reviewed")
        return self.store.rollback_generation(
            project_id=project_id,
            target_generation_id=target_generation_id,
            expected_target_manifest_sha256=expected_target_manifest_sha256,
            expected_active_generation_id=expected_active_generation_id,
            expected_active_manifest_sha256=expected_active_manifest_sha256,
            rolled_back_at=rolled_back_at or _now(),
        )

    def search(
        self,
        *,
        request_id: str,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        query: str,
        query_class: WikiQueryClassV1 | None = None,
        required_sources: tuple[WikiSourceDomainV1, ...] = (),
        required_roles: tuple[str, ...] = (),
        generation_id: str | None = None,
        top_k: int = 12,
        candidate_limit: int = 2_000,
    ) -> WikiSearchResponseV1:
        request: WikiSearchRequestV1 = build_wiki_search_request_v1(
            request_id=request_id,
            project_id=project_id,
            requester_acl_refs=requester_acl_refs,
            query=query,
            query_class=query_class or classify_wiki_query_v1(query),
            required_sources=required_sources,
            required_roles=required_roles,
            generation_id=generation_id,
            top_k=top_k,
            candidate_limit=candidate_limit,
        )
        return self.search_engine.search(request)

    def read(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        logical_path: str,
        generation_id: str | None = None,
    ) -> WikiPageFragmentV1 | WikiDirectoryV1 | None:
        page = self.store.read_record(
            project_id=project_id,
            requester_acl_refs=requester_acl_refs,
            logical_path=logical_path,
            record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
            generation_id=generation_id,
        )
        if page is not None:
            assert isinstance(page, WikiPageFragmentV1)
            return page
        directory = self.store.read_record(
            project_id=project_id,
            requester_acl_refs=requester_acl_refs,
            logical_path=logical_path,
            record_kind=WikiRecordKindV1.DIRECTORY,
            generation_id=generation_id,
        )
        if directory is not None:
            assert isinstance(directory, WikiDirectoryV1)
        return directory

    def list_pages(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        generation_id: str | None = None,
        limit: int = 2_000,
    ) -> tuple[WikiPageFragmentV1, ...]:
        return self.store.list_pages(
            project_id=project_id,
            requester_acl_refs=requester_acl_refs,
            generation_id=generation_id,
            limit=limit,
        )

    def browse_pages(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        generation_id: str | None = None,
        query: str = "",
        page_type: str | None = None,
        source: str | None = None,
        role: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[int, tuple[WikiPageFragmentV1, ...], str | None]:
        return self.store.browse_pages(
            project_id=project_id,
            requester_acl_refs=requester_acl_refs,
            generation_id=generation_id,
            query=query,
            page_type=page_type,
            source=source,
            role=role,
            offset=offset,
            limit=limit,
        )

    def follow_links(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        logical_path: str,
        relation: str | None = None,
        generation_id: str | None = None,
        limit: int = 32,
    ) -> WikiFollowResultV1:
        if limit < 1 or limit > 64:
            raise WikiRuntimeError("Wiki follow limit must be between 1 and 64")
        manifest = self.store.active_manifest(project_id)
        selected_generation = generation_id or (manifest.generation_id if manifest else None)
        if selected_generation is None:
            raise WikiRuntimeError("active Wiki generation is unavailable")
        record = self.read(
            project_id=project_id,
            requester_acl_refs=requester_acl_refs,
            logical_path=logical_path,
            generation_id=selected_generation,
        )
        if not isinstance(record, WikiPageFragmentV1):
            raise WikiRuntimeError("Wiki follow source is not a visible page")
        selected_links = tuple(
            link
            for link in record.links
            if link.status is WikiLinkStatusV1.ACTIVE
            and (relation is None or link.relation == relation)
        )[:limit]
        targets: list[WikiFollowTargetV1] = []
        unresolved: list[str] = []
        for link in selected_links:
            target = self.read(
                project_id=project_id,
                requester_acl_refs=requester_acl_refs,
                logical_path=link.target_path,
                generation_id=selected_generation,
            )
            if isinstance(target, WikiPageFragmentV1):
                targets.append(WikiFollowTargetV1(link=link, page=target))
            else:
                unresolved.append(link.target_path)
        payload = {
            "project_id": project_id,
            "generation_id": selected_generation,
            "source_path": logical_path,
            "targets": tuple(targets),
            "unresolved_target_paths": tuple(sorted(set(unresolved))),
        }
        return WikiFollowResultV1(
            **payload,
            content_sha256=canonical_sha256_v1(
                {
                    **payload,
                    "targets": [item.model_dump(mode="json") for item in targets],
                }
            ),
        )

    def read_evidence(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        page_path: str,
        source_ref_id: str,
        generation_id: str | None = None,
    ) -> WikiRawEvidenceResultV1:
        manifest = self.store.active_manifest(project_id)
        selected_generation = generation_id or (manifest.generation_id if manifest else None)
        if selected_generation is None:
            raise WikiRuntimeError("active Wiki generation is unavailable")
        page = self.read(
            project_id=project_id,
            requester_acl_refs=requester_acl_refs,
            logical_path=page_path,
            generation_id=selected_generation,
        )
        if not isinstance(page, WikiPageFragmentV1):
            raise WikiRuntimeError("raw evidence page is not visible")
        source_ref = next(
            (item for item in page.source_refs if item.source_ref_id == source_ref_id),
            None,
        )
        if source_ref is None:
            raise WikiRuntimeError("source reference is not grounded in the selected Wiki page")
        evidence = StoreRawEvidenceGatewayV1(
            store=self.store,
            project_id=project_id,
            wiki_generation_id=selected_generation,
            requester_acl_refs=requester_acl_refs,
        ).read(source_ref)
        if evidence is None:
            raise WikiRuntimeError("raw evidence is unavailable for the selected source reference")
        payload = {
            "project_id": project_id,
            "generation_id": selected_generation,
            "page_path": page_path,
            "source_ref": source_ref,
            "evidence": evidence,
        }
        return WikiRawEvidenceResultV1(
            **payload,
            content_sha256=canonical_sha256_v1(
                {
                    **payload,
                    "source_ref": source_ref.model_dump(mode="json"),
                    "evidence": evidence.model_dump(mode="json"),
                }
            ),
        )

    def navigate(self, request: WikiNavigationRequestV1) -> WikiNavigationResultV1:
        manifest = self.store.active_manifest(request.project_id)
        if manifest is None:
            return WikiNavigatorV1(
                store=self.store,
                search=self.search_engine,
                raw_reader=StoreRawEvidenceGatewayV1(
                    store=self.store,
                    project_id=request.project_id,
                    wiki_generation_id="unavailable",
                    requester_acl_refs=request.requester_acl_refs,
                ),
            ).navigate(request)
        gateway = StoreRawEvidenceGatewayV1(
            store=self.store,
            project_id=request.project_id,
            wiki_generation_id=manifest.generation_id,
            requester_acl_refs=request.requester_acl_refs,
        )
        return WikiNavigatorV1(
            store=self.store,
            search=self.search_engine,
            raw_reader=gateway,
        ).navigate(request)

    def build_navigation_request(self, **payload: object) -> WikiNavigationRequestV1:
        return build_wiki_navigation_request_v1(**payload)

    def propose_patch(
        self,
        *,
        project_id: str,
        patch: WikiBuilderPatchV1,
        created_at: str | None = None,
    ) -> None:
        self.store.stage_builder_patch(
            project_id=project_id,
            patch=patch,
            created_at=created_at or _now(),
        )

    def evaluate_patch(
        self,
        *,
        project_id: str,
        patch_id: str,
        trial: WikiBuilderTrialV1,
        created_at: str | None = None,
    ) -> WikiBuilderDecisionV1:
        patch = next(
            (
                item
                for item in self.store.list_builder_patches(project_id=project_id)
                if item.patch_id == patch_id
            ),
            None,
        )
        if patch is None:
            raise WikiRuntimeError("Wiki Builder patch not found")
        decision = evaluate_wiki_builder_patch_v1(patch, trial)
        self.store.store_builder_decision(
            project_id=project_id,
            patch_id=patch_id,
            decision=decision,
            created_at=created_at or _now(),
        )
        return decision

    def stage_builder_generation(
        self,
        *,
        project_id: str,
        patch_id: str,
        generation_id: str,
        created_at: str | None = None,
    ) -> WikiPatchedGenerationV1:
        active = self.store.active_manifest(project_id)
        if active is None:
            raise WikiRuntimeError("active Wiki generation is unavailable")
        base = self.store.read_generation_base(
            project_id=project_id,
            generation_id=active.generation_id,
        )
        if base is None:
            raise WikiRuntimeError("active Wiki lacks a reusable reviewed generation base")
        patch = next(
            (
                item
                for item in self.store.list_builder_patches(project_id=project_id)
                if item.patch_id == patch_id
            ),
            None,
        )
        decision = self.store.read_builder_decision(project_id=project_id, patch_id=patch_id)
        if patch is None or decision is None:
            raise WikiRuntimeError("Wiki Builder patch or decision is unavailable")
        if decision.status is not WikiBuilderDecisionStatusV1.PROMOTE_TO_STAGING:
            raise WikiRuntimeError("only a guard-clean promoted patch can enter staging")
        candidates = self.store.list_source_candidates(
            project_id=project_id,
            generation_id=active.generation_id,
        )
        known_candidate_sha256 = {
            canonical_sha256_v1(item.model_dump(mode="json")) for item in candidates
        }
        after_ref_sha256 = {
            ref.raw_content_sha256
            for operation in patch.operations
            if operation.after_page is not None
            for ref in operation.after_page.source_refs
        }
        if not after_ref_sha256.issubset(known_candidate_sha256):
            raise WikiRuntimeError("Builder patch introduces unbound raw source authority")
        timestamp = created_at or _now()
        result = apply_reviewed_wiki_patch_v1(
            base=base,
            patch=patch,
            generation_id=generation_id,
            created_at=timestamp,
        )
        error_book = resolve_wiki_error_book_v1(
            base.error_book,
            resolved_entry_ids=patch.trigger_error_entry_ids,
            promoted_decision=decision,
            generation_id=generation_id,
        )
        next_base = build_wiki_builder_generation_base_v1(
            manifest=result.manifest,
            records=result.records,
            error_book=error_book,
            source_candidate_sha256=base.source_candidate_sha256,
            root_compilation_sha256=base.root_compilation_sha256,
            parent_manifest_sha256=base.manifest.content_sha256,
            applied_patch_sha256=(*base.applied_patch_sha256, patch.content_sha256),
            lineage_depth=base.lineage_depth + 1,
        )
        self.store.stage_generation(
            result.manifest,
            result.records,
            candidates=candidates,
            error_book=error_book,
            builder_base=next_base,
        )
        self.store.register_builder_staging(
            project_id=project_id,
            generation_id=generation_id,
            patch_id=patch_id,
            decision=decision,
            created_at=timestamp,
        )
        return result
