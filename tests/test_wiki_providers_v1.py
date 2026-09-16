from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from evidence_rag.rag.wiki.providers_v1 import (
    HttpWikiRerankerV1,
    OpenAICompatibleWikiEmbeddingProviderV1,
)
from evidence_rag.rag.wiki.search_v1 import WikiProviderUnavailableError, WikiSearchError


def test_embedding_provider_batches_and_binds_exact_order_without_exposing_secret() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        assert request.headers["authorization"] == "Bearer provider-secret"
        payload = __import__("json").loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": [float(index + 1)] * 8}
                    for index, _ in enumerate(payload["input"])
                ]
            },
        )

    provider = OpenAICompatibleWikiEmbeddingProviderV1(
        base_url="http://127.0.0.1:9911/v1",
        api_key="provider-secret",
        model_id="reviewed-embedding-v1",
        dimension=8,
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )
    vectors = provider.embed(("query", "page one", "page two"))
    assert len(vectors) == 3
    assert len(requests) == 2
    assert requests[0]["model"] == "reviewed-embedding-v1"
    assert "provider-secret" not in provider.authority_sha256


def test_reranker_requires_complete_indexed_finite_results() -> None:
    page = SimpleNamespace(
        logical_path="/components/wiki-provider",
        title="Wiki provider",
        summary="Reviewed semantic retrieval provider.",
        evidence_roles=("implementation",),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rerank"
        payload = __import__("json").loads(request.content)
        assert payload["return_documents"] is False
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    provider = HttpWikiRerankerV1(
        base_url="http://localhost:9912",
        api_key=None,
        model_id="cross-encoder-v1",
        transport=httpx.MockTransport(handler),
    )
    assert provider.score("governed evidence", (page,)) == (0.9,)

    broken = HttpWikiRerankerV1(
        base_url="http://localhost:9912",
        api_key=None,
        model_id="cross-encoder-v1",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"results": [{"index": 1, "relevance_score": 0.9}]},
            )
        ),
    )
    with pytest.raises(WikiSearchError, match="authority"):
        broken.score("governed evidence", (page,))


def test_provider_transport_outage_is_typed_for_sparse_fallback() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private transport detail", request=request)

    provider = OpenAICompatibleWikiEmbeddingProviderV1(
        base_url="http://127.0.0.1:9911/v1",
        api_key="secret",
        model_id="reviewed-embedding-v1",
        dimension=8,
        transport=httpx.MockTransport(unavailable),
    )
    with pytest.raises(WikiProviderUnavailableError, match="unavailable") as captured:
        provider.embed(("query",))
    assert "private transport detail" not in str(captured.value)


def test_settings_require_explicit_remote_inference_and_complete_provider_identity(
    settings,
) -> None:
    with pytest.raises(ValueError, match="configured together"):
        replace(settings, wiki_embedding_base_url="http://127.0.0.1:9911/v1")
    with pytest.raises(ValueError, match="external inference approval"):
        replace(
            settings,
            wiki_embedding_base_url="https://embeddings.example.com/v1",
            wiki_embedding_model="reviewed-embedding-v1",
        )
    configured = replace(
        settings,
        wiki_embedding_base_url="https://embeddings.example.com/v1",
        wiki_embedding_model="reviewed-embedding-v1",
        wiki_external_inference_allowed=True,
    )
    assert configured.wiki_embedding_dimension == 1536
