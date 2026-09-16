"""Reviewed HTTP provider adapters for semantic Wiki retrieval.

The adapters are optional and never fabricate semantic scores. Transport
unavailability is a typed sparse-fallback condition; malformed or identity-
inconsistent responses remain hard failures.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Final

import httpx

from .contracts_v1 import WikiPageFragmentV1, canonical_sha256_v1
from .search_v1 import WikiProviderUnavailableError, WikiSearchError

WIKI_HTTP_EMBEDDING_PROVIDER_VERSION: Final = "wiki-openai-compatible-embedding-v1"
WIKI_HTTP_RERANKER_PROVIDER_VERSION: Final = "wiki-http-cross-encoder-reranker-v1"
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}")


def _provider_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request_json(
    client: httpx.Client,
    *,
    url: str,
    headers: dict[str, str],
    payload: dict,
) -> dict:
    try:
        response = client.post(url, headers=headers, json=payload)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise WikiProviderUnavailableError("Wiki inference provider is unavailable") from exc
    if response.status_code in {408, 425, 429} or response.status_code >= 500:
        raise WikiProviderUnavailableError("Wiki inference provider is temporarily unavailable")
    if response.status_code < 200 or response.status_code >= 300:
        raise WikiSearchError(
            f"Wiki inference provider rejected the governed request ({response.status_code})"
        )
    try:
        value = response.json()
    except ValueError as exc:
        raise WikiSearchError("Wiki inference provider returned non-JSON output") from exc
    if not isinstance(value, dict):
        raise WikiSearchError("Wiki inference provider returned an invalid envelope")
    return value


class OpenAICompatibleWikiEmbeddingProviderV1:
    """OpenAI-compatible `/embeddings` adapter with bounded deterministic batching."""

    provider_id = WIKI_HTTP_EMBEDDING_PROVIDER_VERSION

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model_id: str,
        dimension: int,
        timeout_seconds: float = 15.0,
        batch_size: int = 64,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not _MODEL_RE.fullmatch(model_id):
            raise WikiSearchError("Wiki embedding model identity is invalid")
        if not 8 <= dimension <= 65_536 or not 1 <= batch_size <= 256:
            raise WikiSearchError("Wiki embedding provider bounds are invalid")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self.batch_size = batch_size
        self._transport = transport
        self.authority_sha256 = canonical_sha256_v1(
            {
                "dimension": dimension,
                "endpoint_sha256": canonical_sha256_v1(self.base_url),
                "model": model_id,
                "provider": self.provider_id,
                "version": WIKI_HTTP_EMBEDDING_PROVIDER_VERSION,
            }
        )

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts or len(texts) > 2_048:
            raise WikiSearchError("Wiki embedding batch size is outside governed bounds")
        normalized = tuple(str(text) for text in texts)
        if any(not text or len(text) > 16_000 for text in normalized):
            raise WikiSearchError("Wiki embedding input is empty or exceeds governed bounds")
        vectors: list[tuple[float, ...]] = []
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            for start in range(0, len(normalized), self.batch_size):
                batch = normalized[start : start + self.batch_size]
                value = _request_json(
                    client,
                    url=self.base_url + "/embeddings",
                    headers=_provider_headers(self.api_key),
                    payload={
                        "model": self.model_id,
                        "input": list(batch),
                        "encoding_format": "float",
                    },
                )
                rows = value.get("data")
                if not isinstance(rows, list) or len(rows) != len(batch):
                    raise WikiSearchError("Wiki embedding provider returned the wrong row count")
                ordered: list[tuple[float, ...] | None] = [None] * len(batch)
                for row in rows:
                    if not isinstance(row, dict) or type(row.get("index")) is not int:
                        raise WikiSearchError("Wiki embedding row identity is invalid")
                    index = row["index"]
                    raw_vector = row.get("embedding")
                    if (
                        not 0 <= index < len(batch)
                        or ordered[index] is not None
                        or not isinstance(raw_vector, list)
                        or len(raw_vector) != self.dimension
                    ):
                        raise WikiSearchError("Wiki embedding row authority is inconsistent")
                    vector = tuple(float(item) for item in raw_vector)
                    if any(not math.isfinite(item) for item in vector):
                        raise WikiSearchError("Wiki embedding vector is non-finite")
                    ordered[index] = vector
                if any(item is None for item in ordered):
                    raise WikiSearchError("Wiki embedding rows are incomplete")
                vectors.extend(item for item in ordered if item is not None)
        return tuple(vectors)


class HttpWikiRerankerV1:
    """Bounded `/rerank` adapter for cross-encoder or late-interaction services."""

    reranker_id = WIKI_HTTP_RERANKER_PROVIDER_VERSION

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model_id: str,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not _MODEL_RE.fullmatch(model_id):
            raise WikiSearchError("Wiki reranker model identity is invalid")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self.authority_sha256 = canonical_sha256_v1(
            {
                "endpoint_sha256": canonical_sha256_v1(self.base_url),
                "model": model_id,
                "provider": self.reranker_id,
                "version": WIKI_HTTP_RERANKER_PROVIDER_VERSION,
            }
        )

    def score(
        self,
        query: str,
        pages: Sequence[WikiPageFragmentV1],
    ) -> tuple[float, ...]:
        if not query or len(query) > 4_000 or not 1 <= len(pages) <= 100:
            raise WikiSearchError("Wiki reranker request exceeds governed bounds")
        documents = [
            "\n".join(
                (
                    page.logical_path,
                    page.title,
                    page.summary,
                    " ".join(page.evidence_roles),
                )
            )
            for page in pages
        ]
        with httpx.Client(
            timeout=self.timeout_seconds,
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            value = _request_json(
                client,
                url=self.base_url + "/rerank",
                headers=_provider_headers(self.api_key),
                payload={
                    "model": self.model_id,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                    "return_documents": False,
                },
            )
        rows = value.get("results")
        if not isinstance(rows, list) or len(rows) != len(documents):
            raise WikiSearchError("Wiki reranker returned the wrong result count")
        scores: list[float | None] = [None] * len(documents)
        for row in rows:
            if not isinstance(row, dict) or type(row.get("index")) is not int:
                raise WikiSearchError("Wiki reranker result identity is invalid")
            index = row["index"]
            raw_score = row.get("relevance_score", row.get("score"))
            if (
                not 0 <= index < len(documents)
                or scores[index] is not None
                or type(raw_score) not in {int, float}
            ):
                raise WikiSearchError("Wiki reranker result authority is inconsistent")
            score = float(raw_score)
            if not math.isfinite(score):
                raise WikiSearchError("Wiki reranker score is non-finite")
            scores[index] = score
        if any(item is None for item in scores):
            raise WikiSearchError("Wiki reranker results are incomplete")
        return tuple(item for item in scores if item is not None)
