from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import uuid
import weakref
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .embeddings import TOKEN_RE, LocalHashEmbedding
from .models import EvidenceSearchRequest
from .storage import SQLiteStore


class LegacyExecutionEvidenceError(ValueError):
    """Raised when internal V1 execution evidence cannot be authenticated."""


_MAX_LEXICAL_QUERY_TERMS = 12


def _bounded_lexical_query_terms(
    embedder: LocalHashEmbedding,
    query: str,
) -> tuple[list[str], int]:
    """Keep sparse retrieval bounded while dense retrieval sees the full question.

    Direct user terms come first so a long CJK question cannot spend its entire
    sparse budget on generated n-grams. Expansion and n-gram terms remain useful
    fallback terms, but the FTS disjunction is capped to avoid scanning large
    posting lists for dozens of broad alternatives.
    """

    all_terms = list(dict.fromkeys(embedder.tokens(query)))
    direct_terms = list(dict.fromkeys(token.casefold() for token in TOKEN_RE.findall(query)))
    bounded = list(dict.fromkeys((*direct_terms, *all_terms)))[:_MAX_LEXICAL_QUERY_TERMS]
    return bounded, len(all_terms)


@dataclass(frozen=True, slots=True)
class LegacyChannelExecutionEvidence:
    channel: str
    hit_count: int
    returned_ranks: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class LegacySearchExecutionEvidence:
    request_tag: bytes
    response_tag: bytes
    channels: tuple[LegacyChannelExecutionEvidence, ...]
    attestation: bytes


class LegacySearchResponse(dict[str, Any]):
    """JSON-compatible legacy response carrying non-serializable process-local evidence."""

    __slots__ = ("__execution_evidence",)

    def __init__(
        self,
        payload: Mapping[str, Any],
        execution_evidence: LegacySearchExecutionEvidence,
    ) -> None:
        super().__init__(payload)
        self.__execution_evidence = execution_evidence

    @property
    def execution_evidence(self) -> LegacySearchExecutionEvidence:
        return self.__execution_evidence


@dataclass(frozen=True, slots=True)
class _HybridAuthority:
    """Process-local authority bound to one real retriever object identity."""

    execution_evidence_key: bytes


@dataclass(frozen=True, slots=True)
class _HybridAuthorityEntry:
    reference: weakref.ReferenceType[object]
    authority: _HybridAuthority


_HYBRID_AUTHORITY_LOCK = threading.RLock()
_HYBRID_AUTHORITIES: dict[int, _HybridAuthorityEntry] = {}
# This boundary prevents public copy/serialization protocols from carrying authority.
# It does not claim isolation from hostile code that imports module-private process state.


def _remove_hybrid_authority(
    identity: int,
    callback_reference: weakref.ReferenceType[object],
) -> None:
    """Remove only the exact dead registration that installed this callback."""

    with _HYBRID_AUTHORITY_LOCK:
        entry = _HYBRID_AUTHORITIES.get(identity)
        if entry is not None and entry.reference is callback_reference:
            _HYBRID_AUTHORITIES.pop(identity, None)


def _register_hybrid_authority(
    retriever: HybridRetriever,
    authority: _HybridAuthority,
) -> None:
    identity = id(retriever)

    def remove(callback_reference: weakref.ReferenceType[object]) -> None:
        _remove_hybrid_authority(identity, callback_reference)

    reference = weakref.ref(retriever, remove)
    entry = _HybridAuthorityEntry(reference=reference, authority=authority)
    with _HYBRID_AUTHORITY_LOCK:
        current = _HYBRID_AUTHORITIES.get(identity)
        if current is not None:
            current_retriever = current.reference()
            if current_retriever is not None:
                raise LegacyExecutionEvidenceError("HybridRetriever authority is unavailable")
        _HYBRID_AUTHORITIES[identity] = entry


class HybridRetriever:
    _AUTHORITY_COPY_ERROR = "HybridRetriever authority cannot be copied or serialized"
    _AUTHORITY_UNAVAILABLE_ERROR = "HybridRetriever authority is unavailable"

    def __init__(self, store: SQLiteStore, embedder: LocalHashEmbedding) -> None:
        self.store = store
        self.embedder = embedder
        authority = _HybridAuthority(execution_evidence_key=secrets.token_bytes(32))
        _register_hybrid_authority(self, authority)

    def __copy__(self) -> HybridRetriever:
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __deepcopy__(self, memo: dict[int, object]) -> HybridRetriever:
        del memo
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __reduce__(self) -> object:
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __getstate__(self) -> object:
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def __setstate__(self, state: object) -> None:
        del state
        raise TypeError(self._AUTHORITY_COPY_ERROR)

    def search(self, request: EvidenceSearchRequest) -> dict[str, Any]:
        self._authority()
        started = time.perf_counter()
        tokens = list(dict.fromkeys(self.embedder.tokens(request.query)))
        lexical_terms, lexical_terms_total = _bounded_lexical_query_terms(
            self.embedder,
            request.query,
        )
        fts_query = " OR ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"' for token in lexical_terms
        )
        lexical = (
            self.store.lexical_search(fts_query, request.scope, request.limit * 8)
            if fts_query
            else []
        )
        dense_pool = self.store.dense_candidates(request.scope)
        query_vectors = {
            model_id: self.embedder.embed(request.query, model_id=model_id)
            for model_id in {
                str(row.get("embedding_model") or self.embedder.model_id) for row in dense_pool
            }
            if model_id in self.embedder.supported_model_ids
        }

        scores: dict[str, dict[str, Any]] = {}
        lexical_entity_ids = tuple(dict.fromkeys(str(row["entity_id"]) for row in lexical))
        for rank, row in enumerate(lexical, start=1):
            item = scores.setdefault(row["entity_id"], self._item(row))
            item["lexical_score"] = max(item["lexical_score"], 1.0 / (1.0 + 0.15 * (rank - 1)))
            item["channels"].add("lexical")

        dense_ranked: list[tuple[float, dict[str, Any]]] = []
        for row in dense_pool:
            model_id = str(row.get("embedding_model") or self.embedder.model_id)
            query_vector = query_vectors.get(model_id)
            if query_vector is None:
                continue
            similarity = self.embedder.similarity(query_vector, row["vector"])
            if similarity <= 0.05:
                continue
            dense_ranked.append((similarity, row))
        dense_ranked.sort(key=lambda item: item[0], reverse=True)
        dense_entity_ids = tuple(dict.fromkeys(str(row["entity_id"]) for _, row in dense_ranked))
        for similarity, row in dense_ranked[: request.limit * 8]:
            item = scores.setdefault(row["entity_id"], self._item(row))
            item["dense_score"] = max(item["dense_score"], similarity)
            item["channels"].add("dense")

        identifier_query = bool(re.fullmatch(r"[A-Za-z_$][\w.$:/-]*", request.query.strip()))
        lexical_weight = 0.62 if identifier_query else 0.48
        dense_weight = 0.28 if identifier_query else 0.42
        for item in scores.values():
            name = (item["qualified_name"] or item["name"]).casefold()
            query = request.query.strip().casefold()
            exact = 1.0 if name == query else (0.7 if query in name else 0.0)
            path_match = 0.5 if query and query in item["path"].casefold() else 0.0
            entity_score = max(exact, path_match)
            item["score"] = round(
                lexical_weight * item["lexical_score"]
                + dense_weight * item["dense_score"]
                + 0.10 * entity_score,
                6,
            )

        ranked = sorted(scores.values(), key=lambda item: item["score"], reverse=True)
        results = ranked[: request.limit]
        entity_ids = [item["entity_id"] for item in results]
        edges = (
            self.store.edges_for_entities(entity_ids, request.scope.repository_ids or None)
            if request.include_edges
            else []
        )
        edges_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            if edge["source_id"] in entity_ids:
                edges_by_entity[edge["source_id"]].append(edge)
            if edge["target_id"] in entity_ids and edge["target_id"] != edge["source_id"]:
                edges_by_entity[edge["target_id"]].append(edge)
        for item in results:
            item["channels"] = sorted(item["channels"])
            item["edges"] = edges_by_entity.get(item["entity_id"], [])[:20]
            item["snippet"] = self._snippet(item.pop("content"), tokens)

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response = {
            "query_id": f"q-{uuid.uuid4()}",
            "query": request.query,
            "resolved_scope": request.scope.model_dump(),
            "index_generation": sorted({item["generation_id"] for item in results}),
            "total": len(results),
            "results": results,
            "trace": {
                "duration_ms": elapsed_ms,
                "lexical_candidates": len(lexical_entity_ids),
                "lexical_query_terms": len(lexical_terms),
                "lexical_query_terms_total": lexical_terms_total,
                "lexical_query_policy": "direct-first-bounded-v1",
                "dense_candidates": len(dense_pool),
                "dense_matches": len(dense_entity_ids),
                "fusion": "weighted-hybrid-v2",
                "embedding_model": self.embedder.model_id,
                "queried_embedding_models": sorted(query_vectors),
            },
        }
        return self._attest_response(
            request,
            response,
            channel_entity_ids={
                "lexical": lexical_entity_ids,
                "dense": dense_entity_ids,
            },
        )

    def _attest_response(
        self,
        request: EvidenceSearchRequest,
        response: Mapping[str, Any],
        *,
        channel_entity_ids: Mapping[str, Sequence[str]],
    ) -> LegacySearchResponse:
        """Attach immutable channel truth for an execution by this retriever instance."""

        if tuple(sorted(channel_entity_ids)) != ("dense", "lexical"):
            raise LegacyExecutionEvidenceError("execution evidence channels are invalid")
        results = response.get("results")
        if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
            raise LegacyExecutionEvidenceError("execution response results are invalid")
        returned_items = tuple(
            item
            for item in results
            if isinstance(item, Mapping) and type(item.get("entity_id")) is str
        )
        if len(returned_items) != len(results):
            raise LegacyExecutionEvidenceError("execution response result identity is invalid")

        channels: list[LegacyChannelExecutionEvidence] = []
        for channel in ("lexical", "dense"):
            ordered_ids = tuple(channel_entity_ids[channel])
            if any(type(entity_id) is not str or not entity_id for entity_id in ordered_ids):
                raise LegacyExecutionEvidenceError("execution evidence entity identity is invalid")
            if len(ordered_ids) != len(set(ordered_ids)):
                raise LegacyExecutionEvidenceError("execution evidence entities must be unique")
            rank_by_entity = {entity_id: rank for rank, entity_id in enumerate(ordered_ids)}
            returned_ranks = tuple(
                (item["entity_id"], rank_by_entity[item["entity_id"]])
                for item in returned_items
                if channel in item.get("channels", ()) and item["entity_id"] in rank_by_entity
            )
            channels.append(
                LegacyChannelExecutionEvidence(
                    channel=channel,
                    hit_count=len(ordered_ids),
                    returned_ranks=returned_ranks,
                )
            )

        request_tag = self._tag(b"request", _request_execution_bytes(request))
        response_tag = self._tag(b"response", _canonical_json_bytes(response))
        unsigned = {
            "version": 1,
            "request_tag": request_tag.hex(),
            "response_tag": response_tag.hex(),
            "channels": [
                {
                    "channel": channel.channel,
                    "hit_count": channel.hit_count,
                    "returned_ranks": [list(entry) for entry in channel.returned_ranks],
                }
                for channel in channels
            ],
        }
        evidence = LegacySearchExecutionEvidence(
            request_tag=request_tag,
            response_tag=response_tag,
            channels=tuple(channels),
            attestation=self._tag(b"attestation", _canonical_json_bytes(unsigned)),
        )
        return LegacySearchResponse(response, evidence)

    def verify_execution_evidence(
        self,
        request: EvidenceSearchRequest,
        response: Mapping[str, Any],
    ) -> LegacySearchExecutionEvidence:
        """Authenticate V1 channel counts/ranks and bind them to request and response."""

        evidence = getattr(response, "execution_evidence", None)
        if not isinstance(evidence, LegacySearchExecutionEvidence):
            raise LegacyExecutionEvidenceError("legacy execution evidence is missing or invalid")
        expected_request_tag = self._tag(b"request", _request_execution_bytes(request))
        expected_response_tag = self._tag(b"response", _canonical_json_bytes(response))
        if not hmac.compare_digest(evidence.request_tag, expected_request_tag):
            raise LegacyExecutionEvidenceError("legacy execution request attestation failed")
        if not hmac.compare_digest(evidence.response_tag, expected_response_tag):
            raise LegacyExecutionEvidenceError("legacy execution response attestation failed")

        unsigned = {
            "version": 1,
            "request_tag": evidence.request_tag.hex(),
            "response_tag": evidence.response_tag.hex(),
            "channels": [
                {
                    "channel": channel.channel,
                    "hit_count": channel.hit_count,
                    "returned_ranks": [list(entry) for entry in channel.returned_ranks],
                }
                for channel in evidence.channels
            ],
        }
        expected_attestation = self._tag(
            b"attestation",
            _canonical_json_bytes(unsigned),
        )
        if not hmac.compare_digest(evidence.attestation, expected_attestation):
            raise LegacyExecutionEvidenceError("legacy execution evidence attestation failed")
        self._validate_execution_channels(response, evidence.channels)
        return evidence

    def _validate_execution_channels(
        self,
        response: Mapping[str, Any],
        channels: tuple[LegacyChannelExecutionEvidence, ...],
    ) -> None:
        if tuple(channel.channel for channel in channels) != ("lexical", "dense"):
            raise LegacyExecutionEvidenceError("legacy execution channels are invalid")
        results = response.get("results")
        if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
            raise LegacyExecutionEvidenceError("legacy execution results are invalid")
        response_channels: dict[str, set[str]] = {"lexical": set(), "dense": set()}
        for item in results:
            if not isinstance(item, Mapping) or type(item.get("entity_id")) is not str:
                raise LegacyExecutionEvidenceError("legacy execution result identity is invalid")
            item_channels = item.get("channels")
            if not isinstance(item_channels, Sequence) or isinstance(item_channels, (str, bytes)):
                raise LegacyExecutionEvidenceError("legacy execution result channels are invalid")
            for channel in item_channels:
                if channel in response_channels:
                    response_channels[channel].add(item["entity_id"])

        for channel in channels:
            if type(channel.hit_count) is not int or channel.hit_count < 0:
                raise LegacyExecutionEvidenceError("legacy execution hit count is invalid")
            rank_by_entity = dict(channel.returned_ranks)
            if len(rank_by_entity) != len(channel.returned_ranks):
                raise LegacyExecutionEvidenceError("legacy execution ranks repeat an entity")
            if set(rank_by_entity) != response_channels[channel.channel]:
                raise LegacyExecutionEvidenceError("legacy execution returned ranks are incomplete")
            ranks = tuple(rank_by_entity.values())
            if any(
                type(rank) is not int or rank < 0 or rank >= channel.hit_count for rank in ranks
            ):
                raise LegacyExecutionEvidenceError("legacy execution raw rank is invalid")
            if len(ranks) != len(set(ranks)):
                raise LegacyExecutionEvidenceError("legacy execution raw ranks must be unique")

    def _tag(self, purpose: bytes, payload: bytes) -> bytes:
        authority = self._authority()
        return hmac.digest(
            authority.execution_evidence_key,
            purpose + b"\0" + payload,
            "sha256",
        )

    def _authority(self) -> _HybridAuthority:
        """Return registry state; public copy protocols cannot transfer this capability."""

        identity = id(self)
        with _HYBRID_AUTHORITY_LOCK:
            entry = _HYBRID_AUTHORITIES.get(identity)
            registered = None if entry is None else entry.reference()
            if entry is not None and registered is None:
                _HYBRID_AUTHORITIES.pop(identity, None)
        if entry is None or registered is not self:
            raise LegacyExecutionEvidenceError(self._AUTHORITY_UNAVAILABLE_ERROR)
        return entry.authority

    def semantic_links(
        self,
        entity_ids: list[str],
        *,
        threshold: float = 0.16,
        max_neighbors: int = 3,
    ) -> list[dict[str, Any]]:
        """Build a sparse entity-to-entity graph from stored code vectors.

        Search results are intentionally kept sparse: each entity retains only its
        strongest few cosine-similarity connections.  The query-to-result edges and
        deterministic code relations remain separate, so semantic proximity never
        masquerades as a static-analysis fact.
        """

        rows = self.store.vectors_for_entities(entity_ids)
        vectors = {
            str(row["entity_id"]): (
                bytes(row["vector"]),
                str(row.get("embedding_model") or self.embedder.model_id),
            )
            for row in rows
        }
        candidates: list[tuple[float, str, str]] = []
        ordered_ids = [entity_id for entity_id in entity_ids if entity_id in vectors]
        for index, source_id in enumerate(ordered_ids):
            source_vector, source_model = vectors[source_id]
            for target_id in ordered_ids[index + 1 :]:
                target_vector, target_model = vectors[target_id]
                if source_model != target_model:
                    continue
                similarity = self.embedder.similarity(source_vector, target_vector)
                if similarity >= threshold:
                    candidates.append((similarity, source_id, target_id))
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

        degrees: dict[str, int] = defaultdict(int)
        links: list[dict[str, Any]] = []
        for similarity, source_id, target_id in candidates:
            if degrees[source_id] >= max_neighbors or degrees[target_id] >= max_neighbors:
                continue
            digest = hashlib.sha256(f"{source_id}\0{target_id}".encode()).hexdigest()[:24]
            links.append(
                {
                    "id": f"semantic-similar://{digest}",
                    "source": source_id,
                    "predicate": "SEMANTIC_SIMILAR",
                    "target": target_id,
                    "derivation": "vector_cosine",
                    "confidence": round(similarity, 6),
                    "review_status": "retrieved",
                    "domain": "code",
                }
            )
            degrees[source_id] += 1
            degrees[target_id] += 1
        return links

    def _item(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "entity_id": row["entity_id"],
            "repository_id": row["repository_id"],
            "generation_id": row["generation_id"],
            "entity_type": row["entity_type"],
            "view_type": row["view_type"],
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "path": row["path"],
            "language": row["language"],
            "commit": row["commit_sha"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "evidence_locator": row["source_uri"],
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
        prefix = "…" if start else ""
        suffix = "…" if end < len(content) else ""
        return prefix + content[start:end] + suffix


def _request_execution_bytes(request: EvidenceSearchRequest) -> bytes:
    scope = request.scope
    requested_acl_refs = sorted(set(scope.allowed_acl_refs))
    effective_acl_refs = sorted(
        set(requested_acl_refs) | ({"public"} if scope.enforce_acl else set())
    )
    return _canonical_json_bytes(
        {
            "query": request.query,
            "limit": request.limit,
            "include_edges": request.include_edges,
            "scope": {
                "project_id": scope.project_id,
                "repository_ids": list(scope.repository_ids),
                "commit": scope.commit,
                "languages": list(scope.languages),
                "entity_types": list(scope.entity_types),
                "branch": scope.branch,
                "experiment_ids": list(scope.experiment_ids),
                "document_ids": list(scope.document_ids),
                "thread_ids": list(scope.thread_ids),
                "date_from": scope.date_from,
                "date_to": scope.date_to,
                "source_types": list(scope.source_types),
                "enforce_acl": scope.enforce_acl,
                "acl_policy": ("allowlisted-plus-public" if scope.enforce_acl else "unrestricted"),
                "requested_acl_refs": requested_acl_refs,
                "effective_acl_refs": effective_acl_refs,
            },
        }
    )


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _plain_json_value(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise LegacyExecutionEvidenceError(
            "legacy execution payload is not canonical JSON"
        ) from error


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain_json_value(item) for item in value]
    return value
