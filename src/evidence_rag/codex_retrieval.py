from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict
from typing import Any

from .embeddings import LocalHashEmbedding
from .models import CodexSearchRequest
from .storage import SQLiteStore


class CodexHybridRetriever:
    def __init__(self, store: SQLiteStore, embedder: LocalHashEmbedding) -> None:
        self.store = store
        self.embedder = embedder

    def search(self, request: CodexSearchRequest) -> dict[str, Any]:
        started = time.perf_counter()
        tokens = list(dict.fromkeys(self.embedder.tokens(request.query)))
        fts_query = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        lexical = (
            self.store.codex_lexical_search(fts_query, request.scope, request.limit * 8)
            if fts_query
            else []
        )
        dense_pool = self.store.codex_dense_candidates(request.scope)
        query_vectors = {
            model_id: self.embedder.embed(request.query, model_id=model_id)
            for model_id in {
                str(row.get("embedding_model") or self.embedder.model_id) for row in dense_pool
            }
            if model_id in self.embedder.supported_model_ids
        }

        scores: dict[str, dict[str, Any]] = {}
        for rank, row in enumerate(lexical, start=1):
            item = scores.setdefault(row["entity_id"], self._item(row))
            item["lexical_score"] = max(item["lexical_score"], 1.0 / (1.0 + 0.15 * (rank - 1)))
            item["channels"].add("lexical")

        dense_ranked = []
        for row in dense_pool:
            model_id = str(row.get("embedding_model") or self.embedder.model_id)
            query_vector = query_vectors.get(model_id)
            if query_vector is None:
                continue
            similarity = self.embedder.similarity(query_vector, row["vector"])
            if similarity > 0.05:
                dense_ranked.append((similarity, row))
        dense_ranked.sort(key=lambda pair: pair[0], reverse=True)
        for similarity, row in dense_ranked[: request.limit * 8]:
            item = scores.setdefault(row["entity_id"], self._item(row))
            item["dense_score"] = max(item["dense_score"], similarity)
            item["channels"].add("dense")

        identifier_query = bool(re.fullmatch(r"[A-Za-z_$][\w.$:/-]*", request.query.strip()))
        lexical_weight = 0.58 if identifier_query else 0.48
        dense_weight = 0.32 if identifier_query else 0.42
        query = request.query.strip().casefold()
        for item in scores.values():
            title_match = 1.0 if query and query in item["thread_title"].casefold() else 0.0
            type_match = 0.5 if query and query in item["item_type"].casefold() else 0.0
            item["score"] = round(
                lexical_weight * item["lexical_score"]
                + dense_weight * item["dense_score"]
                + 0.10 * max(title_match, type_match),
                6,
            )

        ranked = sorted(scores.values(), key=lambda item: item["score"], reverse=True)
        results = ranked[: request.limit]
        entity_ids = [item["entity_id"] for item in results]
        edges = self.store.codex_edges_for_entities(entity_ids) if request.include_edges else []
        edges_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            source = edge["source_entity_id"]
            target = edge["target_entity_id"]
            if source in entity_ids:
                edges_by_entity[source].append(edge)
            if target in entity_ids and target != source:
                edges_by_entity[target].append(edge)
        for item in results:
            item["channels"] = sorted(item["channels"])
            item["edges"] = edges_by_entity.get(item["entity_id"], [])[:20]
            item["snippet"] = self._snippet(item.pop("content"), tokens)

        return {
            "query_id": f"q-codex-{uuid.uuid4()}",
            "query": request.query,
            "resolved_scope": request.scope.model_dump(),
            "index_generation": sorted({item["generation_id"] for item in results}),
            "total": len(results),
            "results": results,
            "trace": {
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "lexical_candidates": len(lexical),
                "dense_candidates": len(dense_pool),
                "dense_matches": len(dense_ranked),
                "fusion": "codex-weighted-hybrid-v2",
                "embedding_model": self.embedder.model_id,
                "queried_embedding_models": sorted(query_vectors),
            },
        }

    def _item(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": row["entity_id"],
            "source_id": row["source_id"],
            "generation_id": row["generation_id"],
            "thread_id": row["raw_thread_id"],
            "thread_entity_id": row["thread_id"],
            "turn_id": row["turn_id"],
            "thread_title": row["thread_title"],
            "item_type": row["item_type"],
            "view_type": row["view_type"],
            "name": row["name"],
            "role": row["role"],
            "status": row["status"],
            "thread_status": row["thread_status"],
            "timestamp": row["timestamp"],
            "cwd": row["cwd"],
            "evidence_locator": row["source_locator"],
            "acl_ref": row["acl_ref"],
            "metadata": row["metadata"],
            "content": row["content"],
            "lexical_score": 0.0,
            "dense_score": 0.0,
            "score": 0.0,
            "channels": set(),
        }

    def _snippet(self, content: str, tokens: list[str], size: int = 700) -> str:
        if len(content) <= size:
            return content
        lowered = content.casefold()
        positions = [lowered.find(token) for token in tokens if lowered.find(token) >= 0]
        center = min(positions) if positions else 0
        start = max(0, center - size // 4)
        end = min(len(content), start + size)
        return ("…" if start else "") + content[start:end] + ("…" if end < len(content) else "")
