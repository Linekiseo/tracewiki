"""Hybrid path/page retrieval for the agent-native Wiki."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..global_governance_v2 import inspect_untrusted_content_v2
from .contracts_v1 import WikiPageFragmentV1, WikiSourceDomainV1, canonical_sha256_v1
from .store_v1 import WikiStoreV1

WIKI_SEARCH_VERSION = "wiki-hybrid-search-v1"
WIKI_SPARSE_VERSION = "wiki-bm25-fielded-v1"
WIKI_FUSION_VERSION = "wiki-weighted-rrf-v1"
WIKI_DENSE_INTERFACE_VERSION = "wiki-dense-provider-interface-v1"
WIKI_RERANK_INTERFACE_VERSION = "wiki-reranker-interface-v1"

_TOKEN_RE = re.compile(r"[a-z0-9_@.+-]+|[\u3400-\u9fff]")


class WikiSearchError(ValueError):
    """Raised when search authority or provider output is invalid."""


class WikiProviderUnavailableError(WikiSearchError):
    """Typed transient condition that permits an explicit sparse fallback."""


class WikiSearchAvailabilityV1(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class WikiQueryClassV1(StrEnum):
    LOCAL_DETAIL = "local_detail"
    MULTI_HOP = "multi_hop"
    COMPARISON = "comparison"
    TEMPORAL = "temporal"
    GLOBAL = "global"
    EXPLORATORY = "exploratory"


class _FrozenSearch(BaseModel):
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


class WikiSearchRequestV1(_FrozenSearch):
    request_id: str
    project_id: str
    requester_acl_refs: tuple[str, ...] = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=4_000)
    query_class: WikiQueryClassV1
    required_sources: tuple[WikiSourceDomainV1, ...] = ()
    required_roles: tuple[str, ...] = ()
    generation_id: str | None = None
    top_k: int = Field(default=12, ge=1, le=50)
    candidate_limit: int = Field(default=2_000, ge=1, le=20_000)
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiSearchRequestV1:
        if self.requester_acl_refs != tuple(sorted(set(self.requester_acl_refs))):
            raise WikiSearchError("search ACL refs must be sorted and unique")
        if self.required_sources != tuple(
            sorted(set(self.required_sources), key=lambda item: item.value)
        ):
            raise WikiSearchError("search required sources must be sorted and unique")
        if self.required_roles != tuple(sorted(set(self.required_roles))):
            raise WikiSearchError("search required roles must be sorted and unique")
        blocked, reason = inspect_untrusted_content_v2(
            self.query,
            requester_acl_refs=self.requester_acl_refs,
            evidence_acl_ref=self.requester_acl_refs[0],
        )
        if blocked:
            raise WikiSearchError(f"unsafe Wiki search query: {reason}")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiSearchError("Wiki search request digest mismatch")
        return self


class WikiSearchHitV1(_FrozenSearch):
    rank: int = Field(ge=1)
    logical_path: str
    page_sha256: str
    title: str
    page_type: str
    source_domains: tuple[WikiSourceDomainV1, ...]
    evidence_roles: tuple[str, ...]
    exact_score: float = Field(ge=0.0, le=1.0)
    sparse_score: float = Field(ge=0.0)
    dense_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    structural_score: float = Field(ge=0.0, le=1.0)
    fusion_score: float
    rerank_score: float | None
    matched_terms: tuple[str, ...]


class WikiSearchResponseV1(_FrozenSearch):
    request_sha256: str
    generation_id: str
    hits: tuple[WikiSearchHitV1, ...]
    scanned_pages: int = Field(ge=0)
    sparse_availability: WikiSearchAvailabilityV1
    dense_availability: WikiSearchAvailabilityV1
    rerank_availability: WikiSearchAvailabilityV1
    dense_provider_authority_sha256: str | None
    reranker_authority_sha256: str | None
    search_version: Literal[WIKI_SEARCH_VERSION] = WIKI_SEARCH_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiSearchResponseV1:
        if tuple(item.rank for item in self.hits) != tuple(range(1, len(self.hits) + 1)):
            raise WikiSearchError("Wiki search ranks are not contiguous")
        if len({item.logical_path for item in self.hits}) != len(self.hits):
            raise WikiSearchError("Wiki search hits contain duplicate paths")
        if (self.dense_availability is WikiSearchAvailabilityV1.AVAILABLE) != (
            self.dense_provider_authority_sha256 is not None
        ):
            raise WikiSearchError("dense search availability does not bind provider authority")
        if (self.rerank_availability is WikiSearchAvailabilityV1.AVAILABLE) != (
            self.reranker_authority_sha256 is not None
        ):
            raise WikiSearchError("rerank availability does not bind provider authority")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiSearchError("Wiki search response digest mismatch")
        return self


class WikiDenseProviderV1(Protocol):
    provider_id: str
    model_id: str
    dimension: int
    authority_sha256: str

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class WikiRerankerV1(Protocol):
    reranker_id: str
    model_id: str
    authority_sha256: str

    def score(
        self,
        query: str,
        pages: Sequence[WikiPageFragmentV1],
    ) -> tuple[float, ...]: ...


class CallableWikiDenseProviderV1:
    """Reviewed adapter boundary for a real embedding service/model."""

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        dimension: int,
        authority_sha256: str,
        embed: Callable[[Sequence[str]], tuple[tuple[float, ...], ...]],
    ) -> None:
        if dimension < 8 or not authority_sha256.startswith("sha256:"):
            raise WikiSearchError("dense provider authority is incomplete")
        self.provider_id = provider_id
        self.model_id = model_id
        self.dimension = dimension
        self.authority_sha256 = authority_sha256
        self._embed = embed

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        vectors = self._embed(texts)
        if len(vectors) != len(texts):
            raise WikiSearchError("dense provider returned the wrong vector count")
        for vector in vectors:
            if len(vector) != self.dimension or any(not math.isfinite(value) for value in vector):
                raise WikiSearchError("dense provider returned an invalid vector")
        return vectors


class CallableWikiRerankerV1:
    """Reviewed adapter boundary for a cross-encoder or late-interaction reranker."""

    def __init__(
        self,
        *,
        reranker_id: str,
        model_id: str,
        authority_sha256: str,
        score: Callable[[str, Sequence[WikiPageFragmentV1]], tuple[float, ...]],
    ) -> None:
        if not authority_sha256.startswith("sha256:"):
            raise WikiSearchError("reranker authority is incomplete")
        self.reranker_id = reranker_id
        self.model_id = model_id
        self.authority_sha256 = authority_sha256
        self._score = score

    def score(self, query: str, pages: Sequence[WikiPageFragmentV1]) -> tuple[float, ...]:
        scores = self._score(query, pages)
        if len(scores) != len(pages) or any(not math.isfinite(value) for value in scores):
            raise WikiSearchError("reranker returned invalid scores")
        return scores


def build_wiki_search_request_v1(**payload: Any) -> WikiSearchRequestV1:
    normalized = WikiSearchRequestV1.model_construct(
        **payload, content_sha256="pending"
    ).model_dump(mode="json", exclude={"content_sha256"}, warnings=False)
    return WikiSearchRequestV1.model_validate(
        {**payload, "content_sha256": canonical_sha256_v1(normalized)}
    )


def tokenize_wiki_text_v1(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(value.casefold()))


def classify_wiki_query_v1(query: str) -> WikiQueryClassV1:
    tokens = set(tokenize_wiki_text_v1(query))
    lowered = query.casefold()
    if tokens & {"compare", "comparison", "versus", "vs", "对比", "比较"}:
        return WikiQueryClassV1.COMPARISON
    if tokens & {"history", "historical", "as-of", "timeline", "历史", "当时"}:
        return WikiQueryClassV1.TEMPORAL
    if tokens & {"overview", "landscape", "all", "global", "总览", "全部"}:
        return WikiQueryClassV1.GLOBAL
    if tokens & {"why", "cause", "rationale", "chain", "为什么", "原因"}:
        return WikiQueryClassV1.MULTI_HOP
    if any(term in lowered for term in ("explore", "related", "what else", "探索", "相关")):
        return WikiQueryClassV1.EXPLORATORY
    return WikiQueryClassV1.LOCAL_DETAIL


def _page_text(page: WikiPageFragmentV1) -> str:
    return " ".join(
        (
            page.logical_path.replace("/", " "),
            page.title,
            page.summary,
            *page.aliases,
            *page.tags,
            *page.evidence_roles,
        )
    )


def _bm25_scores(
    query_tokens: tuple[str, ...], documents: tuple[tuple[str, ...], ...]
) -> tuple[float, ...]:
    if not query_tokens or not documents:
        return tuple(0.0 for _ in documents)
    document_frequency: Counter[str] = Counter()
    for document in documents:
        document_frequency.update(set(document))
    average_length = sum(len(document) for document in documents) / len(documents) or 1.0
    query_frequency = Counter(query_tokens)
    scores = []
    for document in documents:
        frequencies = Counter(document)
        score = 0.0
        for token, query_count in query_frequency.items():
            frequency = frequencies[token]
            if not frequency:
                continue
            inverse_document = math.log(
                1.0
                + (len(documents) - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.2 * (1.0 - 0.75 + 0.75 * len(document) / average_length)
            score += query_count * inverse_document * frequency * 2.2 / denominator
        scores.append(score)
    return tuple(scores)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _rank_positions(values: Sequence[float]) -> tuple[int, ...]:
    ordered = sorted(range(len(values)), key=lambda index: (-values[index], index))
    positions = [0] * len(values)
    for rank, index in enumerate(ordered, start=1):
        positions[index] = rank
    return tuple(positions)


class WikiHybridSearchV1:
    def __init__(
        self,
        store: WikiStoreV1,
        *,
        dense_provider: WikiDenseProviderV1 | None = None,
        reranker: WikiRerankerV1 | None = None,
    ) -> None:
        self.store = store
        self.dense_provider = dense_provider
        self.reranker = reranker

    def search(self, request: WikiSearchRequestV1) -> WikiSearchResponseV1:
        manifest = self.store.active_manifest(request.project_id)
        generation_id = request.generation_id or (manifest.generation_id if manifest else None)
        if generation_id is None:
            return self._empty_response(request, "unavailable")
        indexed_query = " ".join(
            (
                request.query,
                *request.required_roles,
                *(item.value for item in request.required_sources),
            )
        )
        candidate_paths = self.store.search_page_paths_fts(
            project_id=request.project_id,
            requester_acl_refs=request.requester_acl_refs,
            query=indexed_query,
            generation_id=generation_id,
            limit=min(request.candidate_limit, 2_000),
        )
        pages = self.store.read_pages(
            project_id=request.project_id,
            requester_acl_refs=request.requester_acl_refs,
            logical_paths=candidate_paths,
            generation_id=generation_id,
        )
        if not pages and request.query_class in {
            WikiQueryClassV1.GLOBAL,
            WikiQueryClassV1.EXPLORATORY,
        }:
            pages = self.store.list_pages(
                project_id=request.project_id,
                requester_acl_refs=request.requester_acl_refs,
                generation_id=generation_id,
                limit=min(request.candidate_limit, 512),
            )
        if not pages:
            return self._empty_response(request, generation_id)
        query_tokens = tokenize_wiki_text_v1(request.query)
        document_tokens = tuple(tokenize_wiki_text_v1(_page_text(page)) for page in pages)
        sparse_scores = _bm25_scores(query_tokens, document_tokens)
        exact_scores = []
        structural_scores = []
        matched_terms: list[tuple[str, ...]] = []
        required_sources = set(request.required_sources)
        required_roles = set(request.required_roles)
        for page, tokens in zip(pages, document_tokens, strict=True):
            token_set = set(tokens)
            matched = tuple(sorted(set(query_tokens) & token_set))
            matched_terms.append(matched)
            title_tokens = set(tokenize_wiki_text_v1(page.title))
            path_tokens = set(tokenize_wiki_text_v1(page.logical_path.replace("/", " ")))
            exact_scores.append(
                min(
                    1.0,
                    0.55 * bool(set(query_tokens) & title_tokens)
                    + 0.45 * bool(set(query_tokens) & path_tokens),
                )
            )
            page_sources = {item.source for item in page.source_refs}
            source_coverage = (
                len(required_sources & page_sources) / len(required_sources)
                if required_sources
                else 0.5
            )
            role_coverage = (
                len(required_roles & set(page.evidence_roles)) / len(required_roles)
                if required_roles
                else 0.5
            )
            structural_scores.append((source_coverage + role_coverage) / 2.0)

        max_sparse = max(sparse_scores) or 1.0
        preliminary_order = tuple(
            sorted(
                range(len(pages)),
                key=lambda index: (
                    -(
                        0.38 * exact_scores[index]
                        + 0.42 * (sparse_scores[index] / max_sparse)
                        + 0.2 * structural_scores[index]
                    ),
                    pages[index].logical_path,
                ),
            )
        )
        dense_scores: dict[int, float] = {}
        dense_available = False
        if self.dense_provider is not None:
            dense_window_indices = preliminary_order[: min(512, len(preliminary_order))]
            try:
                vectors = self.dense_provider.embed(
                    (
                        request.query,
                        *(_page_text(pages[index]) for index in dense_window_indices),
                    )
                )
            except WikiProviderUnavailableError:
                vectors = ()
            if vectors:
                dense_available = True
                dense_scores = {
                    index: _cosine(vectors[0], vector)
                    for index, vector in zip(
                        dense_window_indices,
                        vectors[1:],
                        strict=True,
                    )
                }
        sparse_ranks = _rank_positions(sparse_scores)
        exact_ranks = _rank_positions(exact_scores)
        structural_ranks = _rank_positions(structural_scores)
        dense_order = sorted(dense_scores, key=lambda index: (-dense_scores[index], index))
        dense_ranks = {index: rank for rank, index in enumerate(dense_order, start=1)}
        initial_scores = []
        for index in range(len(pages)):
            rrf = (
                1.0 / (60 + sparse_ranks[index])
                + 1.0 / (60 + exact_ranks[index])
                + 1.0 / (60 + structural_ranks[index])
            )
            dense_component = 0.0
            if index in dense_scores:
                rrf += 1.0 / (60 + dense_ranks[index])
                dense_component = (dense_scores[index] + 1.0) / 2.0
            initial_scores.append(
                0.28 * exact_scores[index]
                + 0.27 * (sparse_scores[index] / max_sparse)
                + 0.2 * dense_component
                + 0.15 * structural_scores[index]
                + 6.0 * rrf
            )
        initial_order = sorted(
            range(len(pages)),
            key=lambda index: (-initial_scores[index], pages[index].logical_path),
        )
        rerank_scores: tuple[float, ...] | None = None
        rerank_available = False
        if self.reranker is not None:
            rerank_window = tuple(pages[index] for index in initial_order[: min(100, len(pages))])
            try:
                rerank_window_scores = self.reranker.score(request.query, rerank_window)
            except WikiProviderUnavailableError:
                final_order = initial_order
            else:
                rerank_available = True
                by_index = {
                    index: score
                    for index, score in zip(initial_order, rerank_window_scores, strict=False)
                }
                rerank_scores = tuple(
                    by_index.get(index, float("-inf")) for index in range(len(pages))
                )
                final_order = sorted(
                    initial_order,
                    key=lambda index: (
                        -rerank_scores[index],
                        -initial_scores[index],
                        pages[index].logical_path,
                    ),
                )
        else:
            final_order = initial_order
        hits = []
        for rank, index in enumerate(final_order[: request.top_k], start=1):
            page = pages[index]
            hits.append(
                WikiSearchHitV1(
                    rank=rank,
                    logical_path=page.logical_path,
                    page_sha256=page.content_sha256,
                    title=page.title,
                    page_type=page.page_type.value,
                    source_domains=tuple(
                        sorted(
                            {item.source for item in page.source_refs}, key=lambda item: item.value
                        )
                    ),
                    evidence_roles=page.evidence_roles,
                    exact_score=exact_scores[index],
                    sparse_score=sparse_scores[index],
                    dense_score=dense_scores.get(index),
                    structural_score=structural_scores[index],
                    fusion_score=initial_scores[index],
                    rerank_score=(
                        rerank_scores[index]
                        if rerank_scores is not None and math.isfinite(rerank_scores[index])
                        else None
                    ),
                    matched_terms=matched_terms[index],
                )
            )
        response_payload = {
            "request_sha256": request.content_sha256,
            "generation_id": generation_id,
            "hits": tuple(hits),
            "scanned_pages": len(pages),
            "sparse_availability": WikiSearchAvailabilityV1.AVAILABLE,
            "dense_availability": (
                WikiSearchAvailabilityV1.AVAILABLE
                if dense_available
                else WikiSearchAvailabilityV1.UNAVAILABLE
            ),
            "rerank_availability": (
                WikiSearchAvailabilityV1.AVAILABLE
                if rerank_available
                else WikiSearchAvailabilityV1.UNAVAILABLE
            ),
            "dense_provider_authority_sha256": (
                self.dense_provider.authority_sha256 if dense_available else None
            ),
            "reranker_authority_sha256": (
                self.reranker.authority_sha256 if rerank_available else None
            ),
            "search_version": WIKI_SEARCH_VERSION,
        }
        return WikiSearchResponseV1(
            **response_payload,
            content_sha256=canonical_sha256_v1(
                {
                    **response_payload,
                    "hits": [item.model_dump(mode="json") for item in hits],
                    "sparse_availability": response_payload["sparse_availability"].value,
                    "dense_availability": response_payload["dense_availability"].value,
                    "rerank_availability": response_payload["rerank_availability"].value,
                }
            ),
        )

    @staticmethod
    def _empty_response(request: WikiSearchRequestV1, generation_id: str) -> WikiSearchResponseV1:
        payload = {
            "request_sha256": request.content_sha256,
            "generation_id": generation_id,
            "hits": (),
            "scanned_pages": 0,
            "sparse_availability": WikiSearchAvailabilityV1.UNAVAILABLE,
            "dense_availability": WikiSearchAvailabilityV1.UNAVAILABLE,
            "rerank_availability": WikiSearchAvailabilityV1.UNAVAILABLE,
            "dense_provider_authority_sha256": None,
            "reranker_authority_sha256": None,
            "search_version": WIKI_SEARCH_VERSION,
        }
        return WikiSearchResponseV1(
            **payload,
            content_sha256=canonical_sha256_v1(
                {
                    **payload,
                    "sparse_availability": WikiSearchAvailabilityV1.UNAVAILABLE.value,
                    "dense_availability": WikiSearchAvailabilityV1.UNAVAILABLE.value,
                    "rerank_availability": WikiSearchAvailabilityV1.UNAVAILABLE.value,
                }
            ),
        )
