from __future__ import annotations

import struct

import pytest

from evidence_rag.embeddings import LocalHashEmbedding
from evidence_rag.models import EvidenceSearchRequest
from evidence_rag.retrieval import HybridRetriever


def test_hash_embedding_uses_positive_cosine_without_unrelated_baseline() -> None:
    embedder = LocalHashEmbedding(32)
    first = struct.pack("<32f", 1.0, *([0.0] * 31))
    orthogonal = struct.pack("<32f", 0.0, 1.0, *([0.0] * 30))
    opposite = struct.pack("<32f", -1.0, *([0.0] * 31))

    assert embedder.similarity(first, first) == pytest.approx(1.0)
    assert embedder.similarity(first, orthogonal) == pytest.approx(0.0)
    assert embedder.similarity(first, opposite) == pytest.approx(0.0)


def test_hash_embedding_keeps_legacy_vectors_and_adds_cjk_features() -> None:
    embedder = LocalHashEmbedding(96)
    left = "当前实验的准确率提升"
    right = "分析实验准确率"

    current_left = embedder.embed(left)
    current_right = embedder.embed(right)

    assert "准确率" in set(embedder.tokens(left))
    assert embedder.similarity(current_left, current_right) > 0
    assert embedder.similarity(
        embedder.embed("legacy exact", model_id="local-hash-v1"),
        embedder.embed("legacy exact", model_id="local-hash-v1"),
    ) == pytest.approx(1.0)


def test_long_query_bounds_sparse_terms_without_truncating_dense_input() -> None:
    class Store:
        def __init__(self) -> None:
            self.fts_query = ""

        def lexical_search(self, query, _scope, _limit):
            self.fts_query = query
            return []

        def dense_candidates(self, _scope):
            return []

    query = (
        "当前项目的 Wiki 知识组织和智能查询是如何实现的？"
        "请按架构、检索链路、交互、测试、版本差异和证据缺口组织回答。"
    )
    store = Store()
    result = HybridRetriever(store, LocalHashEmbedding()).search(  # type: ignore[arg-type]
        EvidenceSearchRequest(query=query, limit=8, include_edges=False)
    )

    assert store.fts_query.count(" OR ") + 1 == 12
    assert '"wiki"' in store.fts_query
    assert '"测试"' in store.fts_query
    assert result["trace"]["lexical_query_terms"] == 12
    assert result["trace"]["lexical_query_terms_total"] > 12
    assert result["trace"]["lexical_query_policy"] == "direct-first-bounded-v1"
