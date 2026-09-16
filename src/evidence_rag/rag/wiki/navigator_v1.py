"""Budgeted search/read/follow/verify Navigator for the agent-native Wiki."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from time import monotonic
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..answer_v2 import (
    EVIDENCE_PACK_VERSION,
    SOURCE_RENDERER_VERSION,
    AnswerModeV2,
    EvidenceCitationV2,
    EvidenceFactStatusV2,
    EvidenceFactV2,
    EvidencePackDecisionV2,
    EvidencePackV2,
    SourceRenderedEvidenceV2,
    UnanswerableReasonV2,
)
from ..multisource_foundation_v2 import MultiSourceCandidateV2, canonical_sha256_v2
from .contracts_v1 import (
    WikiLinkStatusV1,
    WikiPageFragmentV1,
    WikiSourceDomainV1,
    WikiSourceRefV1,
    canonical_sha256_v1,
)
from .search_v1 import (
    WikiHybridSearchV1,
    WikiQueryClassV1,
    WikiSearchHitV1,
    build_wiki_search_request_v1,
    classify_wiki_query_v1,
    tokenize_wiki_text_v1,
)
from .store_v1 import WikiStoreV1

WIKI_NAVIGATOR_VERSION = "agent-native-wiki-navigator-v1"
WIKI_OBLIGATION_PLANNER_VERSION = "wiki-evidence-obligation-planner-v1"
WIKI_RAW_READER_VERSION = "wiki-candidate-raw-reader-v1"

_QUERY_STOP_TERMS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "case",
        "does",
        "for",
        "how",
        "in",
        "is",
        "navigation",
        "of",
        "on",
        "or",
        "reviewed",
        "the",
        "to",
        "what",
        "wiki",
        "why",
        "与",
        "了",
        "什",
        "何",
        "在",
        "如",
        "是",
        "有",
        "的",
    }
)


class WikiNavigationError(ValueError):
    """Raised when navigation authority, budgets, or raw evidence fail closed."""


class WikiObligationStatusV1(StrEnum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    MISSING = "missing"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"


class WikiNavigationActionTypeV1(StrEnum):
    SEARCH = "search"
    READ = "read"
    FOLLOW = "follow"
    VERIFY_RAW = "verify_raw"
    STOP = "stop"


class WikiNavigationStopReasonV1(StrEnum):
    EVIDENCE_COMPLETE = "evidence_complete"
    MISSING_OBLIGATIONS = "missing_obligations"
    MISSING_RAW_EVIDENCE = "missing_raw_evidence"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEADLINE_EXHAUSTED = "deadline_exhausted"
    EMPTY_SEARCH_PATIENCE = "empty_search_patience"
    WIKI_UNAVAILABLE = "wiki_unavailable"


class _FrozenNavigation(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class WikiNavigationRequestV1(_FrozenNavigation):
    request_id: str
    project_id: str
    requester_acl_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=4_000)
    query_class: WikiQueryClassV1 | None = None
    required_roles: tuple[str, ...] = ()
    required_sources: tuple[WikiSourceDomainV1, ...] = ()
    as_of: str | None = None
    max_searches: int = Field(default=8, ge=1, le=32)
    max_page_reads: int = Field(default=48, ge=1, le=256)
    max_link_hops: int = Field(default=3, ge=0, le=6)
    max_raw_reads: int = Field(default=16, ge=1, le=64)
    empty_search_patience: int = Field(default=2, ge=1, le=8)
    retrieval_deadline_ms: int = Field(default=4_000, ge=50, le=120_000)
    top_k_per_search: int = Field(default=8, ge=1, le=25)
    token_budget: int = Field(default=8_000, ge=256, le=64_000)
    require_raw_evidence: bool = True
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiNavigationRequestV1:
        if self.requester_acl_refs != tuple(sorted(set(self.requester_acl_refs))):
            raise WikiNavigationError("Navigator ACL refs must be sorted and unique")
        if self.required_roles != tuple(sorted(set(self.required_roles))):
            raise WikiNavigationError("Navigator roles must be sorted and unique")
        if self.required_sources != tuple(
            sorted(set(self.required_sources), key=lambda item: item.value)
        ):
            raise WikiNavigationError("Navigator sources must be sorted and unique")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiNavigationError("Navigator request digest mismatch")
        return self


class WikiEvidenceObligationV1(_FrozenNavigation):
    obligation_id: str
    role: str
    required_sources: tuple[WikiSourceDomainV1, ...]
    status: WikiObligationStatusV1
    supporting_page_paths: tuple[str, ...]
    supporting_source_ref_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _identity(self) -> WikiEvidenceObligationV1:
        if self.supporting_page_paths != tuple(sorted(set(self.supporting_page_paths))):
            raise WikiNavigationError("obligation page support must be sorted and unique")
        if self.supporting_source_ref_ids != tuple(sorted(set(self.supporting_source_ref_ids))):
            raise WikiNavigationError("obligation raw support must be sorted and unique")
        return self


class WikiNavigationActionV1(_FrozenNavigation):
    ordinal: int = Field(ge=1)
    action: WikiNavigationActionTypeV1
    round: int = Field(ge=0)
    query_sha256: str
    input_ids: tuple[str, ...]
    output_ids: tuple[str, ...]
    diagnostic_code: str | None

    @model_validator(mode="after")
    def _identity(self) -> WikiNavigationActionV1:
        for values in (self.input_ids, self.output_ids):
            if values != tuple(dict.fromkeys(values)):
                raise WikiNavigationError("navigation action IDs must be ordered and unique")
        return self


class WikiNavigationResultV1(_FrozenNavigation):
    request_sha256: str
    generation_id: str
    obligations: tuple[WikiEvidenceObligationV1, ...]
    selected_page_paths: tuple[str, ...]
    verified_source_ref_ids: tuple[str, ...]
    actions: tuple[WikiNavigationActionV1, ...]
    evidence_pack: EvidencePackV2
    stop_reason: WikiNavigationStopReasonV1
    search_count: int = Field(ge=0)
    page_read_count: int = Field(ge=0)
    followed_link_count: int = Field(ge=0)
    raw_read_count: int = Field(ge=0)
    raw_verify_count: int = Field(ge=0)
    empty_search_count: int = Field(ge=0)
    retrieval_deadline_ms: int = Field(ge=50, le=120_000)
    deadline_exhausted: bool
    navigator_version: Literal[WIKI_NAVIGATOR_VERSION] = WIKI_NAVIGATOR_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiNavigationResultV1:
        if tuple(item.ordinal for item in self.actions) != tuple(range(1, len(self.actions) + 1)):
            raise WikiNavigationError("navigation actions are not contiguous")
        if self.search_count != sum(
            item.action is WikiNavigationActionTypeV1.SEARCH for item in self.actions
        ):
            raise WikiNavigationError("navigation search accounting mismatch")
        if self.raw_verify_count != len(self.verified_source_ref_ids):
            raise WikiNavigationError("navigation raw verification accounting mismatch")
        if self.raw_verify_count > self.raw_read_count:
            raise WikiNavigationError("navigation raw reads undercount verified evidence")
        if self.deadline_exhausted != (
            self.stop_reason is WikiNavigationStopReasonV1.DEADLINE_EXHAUSTED
        ):
            raise WikiNavigationError("navigation deadline accounting mismatch")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiNavigationError("navigation result digest mismatch")
        return self


class WikiRawEvidenceReaderV1(Protocol):
    authority_sha256: str

    def read(self, source_ref: WikiSourceRefV1) -> EvidenceFactV2 | None: ...


class CandidateRawEvidenceReaderV1:
    """Exact raw fallback over the same governed candidate batch used by Compiler."""

    def __init__(self, candidates: tuple[MultiSourceCandidateV2, ...]) -> None:
        by_sha: dict[str, MultiSourceCandidateV2] = {}
        for candidate in candidates:
            digest = canonical_sha256_v1(candidate.model_dump(mode="json"))
            if digest in by_sha:
                raise WikiNavigationError("raw reader candidate content identity is duplicated")
            by_sha[digest] = candidate
        self._by_sha = by_sha
        self.authority_sha256 = canonical_sha256_v1(
            {
                "candidate_sha256": tuple(sorted(by_sha)),
                "reader_version": WIKI_RAW_READER_VERSION,
            }
        )

    def read(self, source_ref: WikiSourceRefV1) -> EvidenceFactV2 | None:
        candidate = self._by_sha.get(source_ref.raw_content_sha256)
        if candidate is None:
            return None
        if (
            candidate.retrieval_domain != source_ref.source.value
            or candidate.locator != source_ref.locator
            or candidate.source_generation != source_ref.generation_id
            or candidate.acl_ref not in source_ref.acl_refs
        ):
            raise WikiNavigationError("raw reader source reference identity mismatch")
        status = (
            EvidenceFactStatusV2.OBSERVED
            if candidate.raw_or_derived == "raw_fact"
            else EvidenceFactStatusV2.REPORTED
        )
        payload = {
            "fact_id": "wiki-raw-fact-" + source_ref.raw_content_sha256[7:31],
            "source": candidate.retrieval_domain,
            "entity_id": candidate.entity_id,
            "fact_type": candidate.fact_type,
            "entity_type": candidate.entity_type,
            "retrieval_unit_id": candidate.retrieval_unit_id,
            "status": status,
            "text": candidate.snippet,
            "locator": candidate.locator,
            "stable_version": candidate.stable_version,
            "source_generation": candidate.source_generation,
            "raw_or_derived": candidate.raw_or_derived,
            "derivation": candidate.derivation,
            "review_status": candidate.review_status,
            "fact_status": candidate.fact_status,
            "matched_roles": candidate.matched_roles,
            "root_provenance": candidate.root_provenance,
            "counter_evidence": candidate.counter_evidence,
            "token_estimate": candidate.token_estimate,
        }
        return EvidenceFactV2(
            **payload,
            content_sha256=canonical_sha256_v2({**payload, "status": status.value}),
        )


def build_wiki_navigation_request_v1(**payload: Any) -> WikiNavigationRequestV1:
    normalized = WikiNavigationRequestV1.model_construct(
        **payload, content_sha256="pending"
    ).model_dump(mode="json", exclude={"content_sha256"}, warnings=False)
    return WikiNavigationRequestV1.model_validate(
        {**payload, "content_sha256": canonical_sha256_v1(normalized)}
    )


def _planned_roles(request: WikiNavigationRequestV1) -> tuple[str, ...]:
    if request.required_roles:
        return request.required_roles
    query_class = request.query_class or classify_wiki_query_v1(request.query)
    roles_by_class = {
        WikiQueryClassV1.LOCAL_DETAIL: ("implementation",),
        WikiQueryClassV1.MULTI_HOP: ("decision", "implementation", "validation"),
        WikiQueryClassV1.COMPARISON: ("baseline", "metric", "validation"),
        WikiQueryClassV1.TEMPORAL: ("change", "current_version", "historical_version"),
        WikiQueryClassV1.GLOBAL: ("architecture", "implementation", "validation"),
        WikiQueryClassV1.EXPLORATORY: ("implementation", "related_evidence"),
    }
    return tuple(sorted(roles_by_class[query_class]))


def plan_wiki_obligations_v1(
    request: WikiNavigationRequestV1,
) -> tuple[WikiEvidenceObligationV1, ...]:
    return tuple(
        WikiEvidenceObligationV1(
            obligation_id="wiki-obligation-"
            + canonical_sha256_v1(
                {
                    "request": request.content_sha256,
                    "role": role,
                    "sources": tuple(item.value for item in request.required_sources),
                    "version": WIKI_OBLIGATION_PLANNER_VERSION,
                }
            )[7:31],
            role=role,
            required_sources=request.required_sources,
            status=WikiObligationStatusV1.PENDING,
            supporting_page_paths=(),
            supporting_source_ref_ids=(),
        )
        for role in _planned_roles(request)
    )


def _query_relevance_terms_v1(query: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return hard identifiers and soft semantic terms for retrieval admission.

    Explicit IDs, versions, dates and path-like tokens are hard query constraints:
    evidence for a different identifier must never satisfy an obligation merely
    because it has the right source or role.  Remaining content terms establish a
    bounded lexical relevance floor before graph traversal may expand from a hit.
    """

    tokens = tuple(sorted(set(tokenize_wiki_text_v1(query))))
    identifiers = tuple(
        token
        for token in tokens
        if len(token) >= 4
        and (
            any(character.isdigit() for character in token)
            or any(character in "_@.+-" for character in token)
        )
    )
    semantic = tuple(
        token for token in tokens if token not in _QUERY_STOP_TERMS and token not in identifiers
    )
    return identifiers, semantic


def _hit_is_query_relevant_v1(
    hit: WikiSearchHitV1,
    *,
    identifiers: tuple[str, ...],
    semantic_terms: tuple[str, ...],
) -> bool:
    matched = set(hit.matched_terms)
    if identifiers and not set(identifiers).issubset(matched):
        return False
    if not semantic_terms:
        return bool(identifiers) or bool(matched)
    required_matches = max(1, (len(semantic_terms) + 3) // 4)
    return len(set(semantic_terms).intersection(matched)) >= required_matches


def _obligation_support(
    obligation: WikiEvidenceObligationV1,
    pages: tuple[WikiPageFragmentV1, ...],
) -> WikiEvidenceObligationV1:
    matching_pages = []
    source_refs: set[str] = set()
    for page in pages:
        page_sources = {item.source for item in page.source_refs}
        role_match = obligation.role in page.evidence_roles or obligation.role in page.tags
        source_match = not obligation.required_sources or set(obligation.required_sources).issubset(
            page_sources
        )
        if role_match and source_match:
            matching_pages.append(page.logical_path)
            source_refs.update(item.source_ref_id for item in page.source_refs)
    return obligation.model_copy(
        update={
            "status": (
                WikiObligationStatusV1.SATISFIED
                if matching_pages
                else WikiObligationStatusV1.PENDING
            ),
            "supporting_page_paths": tuple(sorted(matching_pages)),
            "supporting_source_ref_ids": tuple(sorted(source_refs)),
        }
    )


def _build_evidence_pack(
    *,
    request: WikiNavigationRequestV1,
    generation_id: str,
    obligations: tuple[WikiEvidenceObligationV1, ...],
    pages: tuple[WikiPageFragmentV1, ...],
    raw_facts: tuple[EvidenceFactV2, ...],
    missing_raw: bool,
) -> EvidencePackV2:
    included: list[EvidenceFactV2] = []
    dropped: list[str] = []
    used_tokens = 0
    for fact in raw_facts:
        if used_tokens + fact.token_estimate <= request.token_budget:
            included.append(fact)
            used_tokens += fact.token_estimate
        else:
            dropped.append(fact.fact_id)
    missing_roles = tuple(
        sorted(
            item.role for item in obligations if item.status is not WikiObligationStatusV1.SATISFIED
        )
    )
    citations = tuple(
        EvidenceCitationV2(
            citation_id="wiki-citation-" + fact.content_sha256[7:31],
            fact_id=fact.fact_id,
            source=fact.source,
            fact_type=fact.fact_type,
            entity_type=fact.entity_type,
            locator=fact.locator,
            stable_version=fact.stable_version,
            content_sha256=fact.content_sha256,
        )
        for fact in included
    )
    rendered = []
    for fact in included:
        payload = {
            "fact_id": fact.fact_id,
            "source": fact.source,
            "entity_type": fact.entity_type,
            "heading": f"{fact.source} · {fact.entity_type}",
            "body": fact.text,
            "locator": fact.locator,
            "stable_version": fact.stable_version,
            "renderer_version": SOURCE_RENDERER_VERSION,
        }
        rendered.append(
            SourceRenderedEvidenceV2(
                **payload,
                content_sha256=canonical_sha256_v2(payload),
            )
        )
    if missing_roles:
        decision_status = "partially_supported" if included else "insufficient_evidence"
        reason = UnanswerableReasonV2.MISSING_REQUIRED_ROLE
    elif missing_raw or not included:
        decision_status = "insufficient_evidence"
        reason = UnanswerableReasonV2.SOURCE_UNAVAILABLE
    else:
        decision_status = "supported"
        reason = None
    decision = EvidencePackDecisionV2(
        status=decision_status,
        answer_mode=AnswerModeV2.RETRIEVAL_ONLY,
        unanswerable_reason=reason,
        missing_roles=missing_roles,
        remediation=("verify_missing_raw_evidence",) if missing_raw else (),
    )
    observed = tuple(item for item in included if item.status is EvidenceFactStatusV2.OBSERVED)
    reported = tuple(item for item in included if item.status is EvidenceFactStatusV2.REPORTED)
    verified = tuple(item for item in included if item.status is EvidenceFactStatusV2.VERIFIED)
    inferred = tuple(item for item in included if item.status is EvidenceFactStatusV2.INFERRED)
    ordered_facts = (*verified, *reported, *observed, *inferred)
    # Reorder citations and rendered evidence to the exact status-grouped fact order.
    citation_by_fact = {item.fact_id: item for item in citations}
    rendered_by_fact = {item.fact_id: item for item in rendered}
    pack_payload = {
        "question_sha256": canonical_sha256_v2(request.query),
        "project_id": request.project_id,
        "plan_sha256": request.content_sha256,
        "source_status": tuple(
            (
                source.value,
                "complete"
                if any(fact.source == source.value for fact in included)
                else "no_matching_evidence",
                generation_id,
                None,
            )
            for source in WikiSourceDomainV1
        ),
        "verified_facts": verified,
        "reported_facts": reported,
        "observed_facts": observed,
        "inferred_facts": inferred,
        "counter_evidence": tuple(item.fact_id for item in ordered_facts if item.counter_evidence),
        "qualifiers": (f"wiki_generation={generation_id}",),
        "version_differences": (),
        "path_ids": tuple(
            "wiki-path-" + canonical_sha256_v1(page.logical_path)[7:31] for page in pages
        ),
        "satisfied_roles": tuple(
            sorted(
                item.role for item in obligations if item.status is WikiObligationStatusV1.SATISFIED
            )
        ),
        "missing_roles": missing_roles,
        "stale_roles": (),
        "unauthorized_roles": (),
        "typed_relation_context": tuple(
            sorted(
                f"{link.source_path}|{link.relation}|{link.target_path}"
                for page in pages
                for link in page.links
                if link.status is WikiLinkStatusV1.ACTIVE
            )
        ),
        "rendered_evidence": tuple(rendered_by_fact[item.fact_id] for item in ordered_facts),
        "citations": tuple(citation_by_fact[item.fact_id] for item in ordered_facts),
        "decision": decision,
        "included_fact_ids": tuple(item.fact_id for item in ordered_facts),
        "dropped_fact_ids": tuple(dropped),
        "token_budget": request.token_budget,
        "used_tokens": used_tokens,
        "pack_version": EVIDENCE_PACK_VERSION,
    }
    return EvidencePackV2(
        **pack_payload,
        content_sha256=canonical_sha256_v2(
            {
                **pack_payload,
                "verified_facts": [item.model_dump(mode="json") for item in verified],
                "reported_facts": [item.model_dump(mode="json") for item in reported],
                "observed_facts": [item.model_dump(mode="json") for item in observed],
                "inferred_facts": [item.model_dump(mode="json") for item in inferred],
                "rendered_evidence": [
                    item.model_dump(mode="json") for item in pack_payload["rendered_evidence"]
                ],
                "citations": [item.model_dump(mode="json") for item in pack_payload["citations"]],
                "decision": decision.model_dump(mode="json"),
            }
        ),
    )


class WikiNavigatorV1:
    def __init__(
        self,
        *,
        store: WikiStoreV1,
        search: WikiHybridSearchV1,
        raw_reader: WikiRawEvidenceReaderV1,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.store = store
        self.search = search
        self.raw_reader = raw_reader
        self._clock = clock

    def navigate(self, request: WikiNavigationRequestV1) -> WikiNavigationResultV1:
        started_at = self._clock()

        def deadline_reached() -> bool:
            return (self._clock() - started_at) * 1_000 >= request.retrieval_deadline_ms

        manifest = self.store.active_manifest(request.project_id)
        if manifest is None:
            return self._unavailable(request)
        generation_id = manifest.generation_id
        obligations = plan_wiki_obligations_v1(request)
        pages_by_path: dict[str, WikiPageFragmentV1] = {}
        actions: list[WikiNavigationActionV1] = []
        search_count = 0
        page_read_count = 0
        followed_links = 0
        raw_read_count = 0
        consecutive_empty_searches = 0
        total_empty_searches = 0
        deadline_exhausted = False
        query_sha = canonical_sha256_v1(request.query)
        query_identifiers, query_semantic_terms = _query_relevance_terms_v1(request.query)

        for round_number in range(1, request.max_searches + 1):
            if deadline_reached():
                deadline_exhausted = True
                break
            pending = tuple(
                item for item in obligations if item.status is WikiObligationStatusV1.PENDING
            )
            if not pending or page_read_count >= request.max_page_reads:
                break
            obligation = pending[0]
            search_request = build_wiki_search_request_v1(
                request_id=f"{request.request_id}-search-{round_number}",
                project_id=request.project_id,
                requester_acl_refs=request.requester_acl_refs,
                query=request.query,
                query_class=request.query_class or classify_wiki_query_v1(request.query),
                required_sources=obligation.required_sources,
                required_roles=(obligation.role,),
                generation_id=generation_id,
                top_k=request.top_k_per_search,
                candidate_limit=2_000,
            )
            response = self.search.search(search_request)
            relevant_hits = tuple(
                item
                for item in response.hits
                if _hit_is_query_relevant_v1(
                    item,
                    identifiers=query_identifiers,
                    semantic_terms=query_semantic_terms,
                )
            )
            search_count += 1
            if relevant_hits:
                consecutive_empty_searches = 0
            else:
                consecutive_empty_searches += 1
                total_empty_searches += 1
            actions.append(
                WikiNavigationActionV1(
                    ordinal=len(actions) + 1,
                    action=WikiNavigationActionTypeV1.SEARCH,
                    round=round_number,
                    query_sha256=query_sha,
                    input_ids=(obligation.obligation_id,),
                    output_ids=tuple(item.logical_path for item in relevant_hits),
                    diagnostic_code=(
                        None
                        if relevant_hits
                        else ("query_relevance_not_met" if response.hits else "no_matching_pages")
                    ),
                )
            )
            if deadline_reached():
                deadline_exhausted = True
                break
            if consecutive_empty_searches >= request.empty_search_patience:
                break
            unread = tuple(
                item.logical_path
                for item in relevant_hits
                if item.logical_path not in pages_by_path
            )[: max(0, request.max_page_reads - page_read_count)]
            read_pages = self.store.read_pages(
                project_id=request.project_id,
                requester_acl_refs=request.requester_acl_refs,
                logical_paths=unread,
                generation_id=generation_id,
            )
            for page in read_pages:
                pages_by_path[page.logical_path] = page
            page_read_count += len(read_pages)
            actions.append(
                WikiNavigationActionV1(
                    ordinal=len(actions) + 1,
                    action=WikiNavigationActionTypeV1.READ,
                    round=round_number,
                    query_sha256=query_sha,
                    input_ids=unread,
                    output_ids=tuple(item.logical_path for item in read_pages),
                    diagnostic_code=None,
                )
            )
            pages = tuple(sorted(pages_by_path.values(), key=lambda item: item.logical_path))
            obligations = tuple(_obligation_support(item, pages) for item in obligations)
            if all(item.status is WikiObligationStatusV1.SATISFIED for item in obligations):
                break
            if request.max_link_hops:
                frontier = list(read_pages)
                for hop in range(1, request.max_link_hops + 1):
                    if deadline_reached():
                        deadline_exhausted = True
                        break
                    targets = tuple(
                        dict.fromkeys(
                            link.target_path
                            for page in frontier
                            for link in page.links
                            if link.status is WikiLinkStatusV1.ACTIVE
                            and link.target_path not in pages_by_path
                        )
                    )[: max(0, request.max_page_reads - page_read_count)]
                    if not targets:
                        break
                    linked_pages = self.store.read_pages(
                        project_id=request.project_id,
                        requester_acl_refs=request.requester_acl_refs,
                        logical_paths=targets,
                        generation_id=generation_id,
                    )
                    for page in linked_pages:
                        pages_by_path[page.logical_path] = page
                    page_read_count += len(linked_pages)
                    followed_links += len(linked_pages)
                    actions.append(
                        WikiNavigationActionV1(
                            ordinal=len(actions) + 1,
                            action=WikiNavigationActionTypeV1.FOLLOW,
                            round=round_number,
                            query_sha256=query_sha,
                            input_ids=tuple(item.logical_path for item in frontier),
                            output_ids=tuple(item.logical_path for item in linked_pages),
                            diagnostic_code=f"hop_{hop}",
                        )
                    )
                    pages = tuple(
                        sorted(pages_by_path.values(), key=lambda item: item.logical_path)
                    )
                    obligations = tuple(_obligation_support(item, pages) for item in obligations)
                    if all(item.status is WikiObligationStatusV1.SATISFIED for item in obligations):
                        break
                    frontier = list(linked_pages)
                if deadline_exhausted:
                    break
            pages = tuple(sorted(pages_by_path.values(), key=lambda item: item.logical_path))
            obligations = tuple(_obligation_support(item, pages) for item in obligations)

        pages = tuple(sorted(pages_by_path.values(), key=lambda item: item.logical_path))
        obligations = tuple(
            item
            if item.status is WikiObligationStatusV1.SATISFIED
            else item.model_copy(update={"status": WikiObligationStatusV1.MISSING})
            for item in obligations
        )
        required_ref_ids = {
            ref_id
            for obligation in obligations
            if obligation.status is WikiObligationStatusV1.SATISFIED
            for ref_id in obligation.supporting_source_ref_ids
        }
        source_refs = {
            item.source_ref_id: item
            for page in pages
            for item in page.source_refs
            if item.source_ref_id in required_ref_ids
        }
        raw_facts: list[EvidenceFactV2] = []
        verified_ref_ids: list[str] = []
        missing_raw = False
        for source_ref_id in sorted(source_refs):
            if raw_read_count >= request.max_raw_reads:
                missing_raw = True
                break
            if deadline_reached():
                deadline_exhausted = True
                missing_raw = True
                break
            raw_read_count += 1
            fact = self.raw_reader.read(source_refs[source_ref_id])
            if fact is None:
                missing_raw = True
                continue
            raw_facts.append(fact)
            verified_ref_ids.append(source_ref_id)
            actions.append(
                WikiNavigationActionV1(
                    ordinal=len(actions) + 1,
                    action=WikiNavigationActionTypeV1.VERIFY_RAW,
                    round=search_count,
                    query_sha256=query_sha,
                    input_ids=(source_ref_id,),
                    output_ids=(fact.fact_id,),
                    diagnostic_code=None,
                )
            )
        if any(item.status is WikiObligationStatusV1.MISSING for item in obligations):
            if deadline_exhausted:
                stop_reason = WikiNavigationStopReasonV1.DEADLINE_EXHAUSTED
            elif consecutive_empty_searches >= request.empty_search_patience:
                stop_reason = WikiNavigationStopReasonV1.EMPTY_SEARCH_PATIENCE
            elif search_count >= request.max_searches or page_read_count >= request.max_page_reads:
                stop_reason = WikiNavigationStopReasonV1.BUDGET_EXHAUSTED
            else:
                stop_reason = WikiNavigationStopReasonV1.MISSING_OBLIGATIONS
        elif deadline_exhausted:
            stop_reason = WikiNavigationStopReasonV1.DEADLINE_EXHAUSTED
        elif raw_read_count >= request.max_raw_reads and missing_raw:
            stop_reason = WikiNavigationStopReasonV1.BUDGET_EXHAUSTED
        elif missing_raw or (request.require_raw_evidence and not raw_facts):
            stop_reason = WikiNavigationStopReasonV1.MISSING_RAW_EVIDENCE
        else:
            stop_reason = WikiNavigationStopReasonV1.EVIDENCE_COMPLETE
        actions.append(
            WikiNavigationActionV1(
                ordinal=len(actions) + 1,
                action=WikiNavigationActionTypeV1.STOP,
                round=search_count,
                query_sha256=query_sha,
                input_ids=tuple(item.obligation_id for item in obligations),
                output_ids=tuple(verified_ref_ids),
                diagnostic_code=stop_reason.value,
            )
        )
        pack = _build_evidence_pack(
            request=request,
            generation_id=generation_id,
            obligations=obligations,
            pages=pages,
            raw_facts=tuple(raw_facts),
            missing_raw=missing_raw,
        )
        payload = {
            "request_sha256": request.content_sha256,
            "generation_id": generation_id,
            "obligations": obligations,
            "selected_page_paths": tuple(item.logical_path for item in pages),
            "verified_source_ref_ids": tuple(verified_ref_ids),
            "actions": tuple(actions),
            "evidence_pack": pack,
            "stop_reason": stop_reason,
            "search_count": search_count,
            "page_read_count": page_read_count,
            "followed_link_count": followed_links,
            "raw_read_count": raw_read_count,
            "raw_verify_count": len(verified_ref_ids),
            "empty_search_count": total_empty_searches,
            "retrieval_deadline_ms": request.retrieval_deadline_ms,
            "deadline_exhausted": deadline_exhausted,
            "navigator_version": WIKI_NAVIGATOR_VERSION,
        }
        return WikiNavigationResultV1(
            **payload,
            content_sha256=canonical_sha256_v1(
                {
                    **payload,
                    "obligations": [item.model_dump(mode="json") for item in obligations],
                    "actions": [item.model_dump(mode="json") for item in actions],
                    "evidence_pack": pack.model_dump(mode="json"),
                    "stop_reason": stop_reason.value,
                }
            ),
        )

    def _unavailable(self, request: WikiNavigationRequestV1) -> WikiNavigationResultV1:
        obligations = tuple(
            item.model_copy(update={"status": WikiObligationStatusV1.UNAVAILABLE})
            for item in plan_wiki_obligations_v1(request)
        )
        pack = _build_evidence_pack(
            request=request,
            generation_id="unavailable",
            obligations=obligations,
            pages=(),
            raw_facts=(),
            missing_raw=True,
        )
        action = WikiNavigationActionV1(
            ordinal=1,
            action=WikiNavigationActionTypeV1.STOP,
            round=0,
            query_sha256=canonical_sha256_v1(request.query),
            input_ids=(),
            output_ids=(),
            diagnostic_code=WikiNavigationStopReasonV1.WIKI_UNAVAILABLE.value,
        )
        payload = {
            "request_sha256": request.content_sha256,
            "generation_id": "unavailable",
            "obligations": obligations,
            "selected_page_paths": (),
            "verified_source_ref_ids": (),
            "actions": (action,),
            "evidence_pack": pack,
            "stop_reason": WikiNavigationStopReasonV1.WIKI_UNAVAILABLE,
            "search_count": 0,
            "page_read_count": 0,
            "followed_link_count": 0,
            "raw_read_count": 0,
            "raw_verify_count": 0,
            "empty_search_count": 0,
            "retrieval_deadline_ms": request.retrieval_deadline_ms,
            "deadline_exhausted": False,
            "navigator_version": WIKI_NAVIGATOR_VERSION,
        }
        return WikiNavigationResultV1(
            **payload,
            content_sha256=canonical_sha256_v1(
                {
                    **payload,
                    "obligations": [item.model_dump(mode="json") for item in obligations],
                    "actions": [action.model_dump(mode="json")],
                    "evidence_pack": pack.model_dump(mode="json"),
                    "stop_reason": WikiNavigationStopReasonV1.WIKI_UNAVAILABLE.value,
                }
            ),
        )
