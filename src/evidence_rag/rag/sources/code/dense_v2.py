"""Offline dense publication, retrieval, and deterministic Code V2 fusion.

The module deliberately stays inside the C3-02 boundary:

* publication consumes a caller-scoped, already-published C2 unit set;
* the only default provider is the local ``local-hash-v2`` baseline;
* retrieval scans the existing scoped Store vector iterator;
* hybrid retrieval reads the existing exact/sparse retriever and applies weighted
  reciprocal-rank fusion without reranking, calibration, graph traversal, or I/O.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ....models import EvidenceSearchRequest
from .contracts import (
    CodeCandidateRole,
    CodeChannelCompleteNoMatch,
    CodeChannelCompletePruned,
    CodeChannelCompleteWithHits,
    CodeChannelDisabled,
    CodeChannelError,
    CodeChannelFailure,
    CodeChannelOutcome,
    CodeChannelRank,
    CodeChannelScore,
    CodeChannelUnavailable,
    CodeDerivation,
    CodeFactStatus,
    CodeQueryProfile,
    CodeRelationNode,
    CodeRelationPath,
    CodeRetrievalCandidate,
    CodeRetrievalChannel,
    CodeReviewStatus,
    CodeSourceResult,
    CodeSourceStatus,
    CodeTask,
    CodeUncalibratedScore,
    CodeVersionAlignment,
)
from .embedding_v2 import (
    LOCAL_HASH_MODEL,
    CodeEmbeddingIndexer,
    CodeEmbeddingIndexResult,
    EmbeddingBatchResult,
    EmbeddingDimensionError,
    EmbeddingProfile,
    EmbeddingProvenance,
    EmbeddingProvider,
    EmbeddingProviderUnavailableError,
    LocalHashCodeEmbeddingProvider,
)
from .retrieval_v2 import (
    CodeExactSparseRetriever,
    CodeExactSparseSearchResult,
)

DENSE_RETRIEVER_VERSION = "code-dense-v2"
HYBRID_RETRIEVER_VERSION = "code-hybrid-v2"
DENSE_PUBLICATION_VERSION = "code-dense-publication-v1"
DENSE_FUSION_POLICY = "deterministic-weighted-rrf-v2"
DENSE_DIVERSIFICATION_POLICY = "entity-aware-hard-exact-first-v1"
DEFAULT_RRF_K = 60
DEFAULT_EXACT_WEIGHT = 2.0
DEFAULT_SPARSE_WEIGHT = 1.0
DEFAULT_DENSE_WEIGHT = 1.0
_NOT_BUILT = frozenset({"", "not-built", "not_built", "disabled", "unavailable"})
_URI_RE = re.compile(r"code://[^\s\"'<>]+")
_FULL_SHA_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{40}|[0-9a-fA-F]{64})(?![0-9a-fA-F])")
_LABELED_REF_RE = re.compile(r"\b(branch|tag)\s*[:=]\s*([^\s,;]+)", re.IGNORECASE)


DEFAULT_CODE_NL_PROFILE = EmbeddingProfile(
    id="code_nl/local-hash-v2",
    purpose="code_nl",
    model=LOCAL_HASH_MODEL,
    revision="1",
    dimension=384,
    instruction="",
    instruction_revision="none-v1",
    max_tokens=8192,
    batch_size=64,
    locality="local",
    redaction_policy="deny-secrets-v1",
    normalization_revision="local-hash-v2",
)


def resolve_code_embedding_profile(
    profile: str,
    *,
    dimension: int = DEFAULT_CODE_NL_PROFILE.dimension,
) -> tuple[EmbeddingProfile, LocalHashCodeEmbeddingProvider]:
    """Resolve the configured production profile without silently substituting a model.

    ``local-hash-v2`` is the only provider bundled with the repository.  Candidate
    model names remain configuration/evaluation metadata until a matching offline
    provider is injected; accepting one here and then publishing local-hash vectors
    under that name would corrupt both cache identity and query provenance.
    """

    if type(profile) is not str or not profile:
        raise ValueError("Code embedding profile must be a non-empty string")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
        raise ValueError("Code embedding dimension must be a positive integer")
    if profile != "local-hash-v2":
        raise ValueError(f"Code embedding profile {profile!r} has no configured offline provider")
    resolved = replace(DEFAULT_CODE_NL_PROFILE, dimension=dimension)
    return resolved, LocalHashCodeEmbeddingProvider(dimension=dimension)


@dataclass(frozen=True, slots=True)
class DenseModelCandidate:
    """Configuration truth for a candidate; never a fabricated benchmark result."""

    purpose: str
    model: str
    locality: str
    availability: str
    benchmark_status: str
    reason: str

    def canonical_snapshot(self) -> dict[str, str]:
        return {
            "availability": self.availability,
            "benchmark_status": self.benchmark_status,
            "locality": self.locality,
            "model": self.model,
            "purpose": self.purpose,
            "reason": self.reason,
        }


DENSE_MODEL_CANDIDATES = (
    DenseModelCandidate(
        purpose="code_nl",
        model=LOCAL_HASH_MODEL,
        locality="local",
        availability="available",
        benchmark_status="not-run",
        reason="deterministic offline baseline; no learned-model quality claim",
    ),
    DenseModelCandidate(
        purpose="code_code",
        model="unselected",
        locality="local",
        availability="unavailable",
        benchmark_status="not-run",
        reason="profile reserved; no local model artifact or provider selected",
    ),
    DenseModelCandidate(
        purpose="history",
        model="unselected",
        locality="local",
        availability="unavailable",
        benchmark_status="not-run",
        reason="profile reserved; history retrieval is outside C3-02",
    ),
    DenseModelCandidate(
        purpose="error",
        model="unselected",
        locality="local",
        availability="unavailable",
        benchmark_status="not-run",
        reason="profile reserved; error-specific model is not selected",
    ),
    DenseModelCandidate(
        purpose="code_nl",
        model="Qwen3-Embedding-0.6B",
        locality="remote",
        availability="unavailable",
        benchmark_status="not-run",
        reason="no configured provider; network access is disabled",
    ),
    DenseModelCandidate(
        purpose="code_nl",
        model="BGE-M3",
        locality="remote",
        availability="unavailable",
        benchmark_status="not-run",
        reason="no configured provider; network access is disabled",
    ),
    DenseModelCandidate(
        purpose="code_nl",
        model="code-specific-embedding",
        locality="remote",
        availability="unavailable",
        benchmark_status="not-run",
        reason="candidate family only; no model or provider selected",
    ),
)


class CodeDenseError(RuntimeError):
    """Base error for the local C3-02 publication/retrieval boundary."""


class CodeDensePublicationError(CodeDenseError):
    """A generation/profile could not be published completely."""


class CodeDenseUnavailableError(CodeDenseError):
    """Dense retrieval is intentionally fail-closed for the resolved scope."""


class CodeDenseStore(Protocol):
    """Existing Store surface used by C3-02."""

    def get_code_index_publication(
        self,
        generation_id: str,
        *,
        repository_id: str | None = None,
        active_only: bool = False,
    ) -> dict[str, Any] | None: ...

    def upsert_code_index_publication(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def iter_code_unit_vectors(self, **scope: Any) -> Iterable[dict[str, Any]]: ...

    def load_code_unit(self, unit_id: str, **scope: Any) -> dict[str, Any] | None: ...

    def connection(self) -> Any: ...

    def transaction(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class CodeDensePublicationResult:
    project_id: str
    repository_id: str
    generation_id: str
    profile: EmbeddingProfile
    provider: EmbeddingProvenance
    unit_count: int
    vector_count: int
    cache_hits: int
    cache_misses: int
    content_hash: str
    vector_hash: str
    publication: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CodeDenseHitTrace:
    retrieval_unit_id: str
    entity_id: str
    project_id: str
    repository_id: str
    generation_id: str
    acl_ref: str
    profile: str
    model: str
    dimension: int
    raw_rank: int
    raw_score: float
    content_hash: str
    locator: str
    explanation: str
    original_rank: int
    diversified_rank: int | None
    representative: bool


@dataclass(frozen=True, slots=True)
class CodeDenseTrace:
    query_id: str
    profile_snapshot: Mapping[str, Any]
    provider: Mapping[str, Any] | None
    generation_scopes: tuple[str, ...]
    channel_hits: tuple[CodeDenseHitTrace, ...]
    similarity: str
    unavailable_reason: str = ""


@dataclass(frozen=True, slots=True)
class CodeDenseSearchResult:
    result: CodeSourceResult
    trace: CodeDenseTrace


@dataclass(frozen=True, slots=True)
class CodeHybridHitTrace:
    retrieval_unit_id: str
    entity_id: str
    locator: str
    raw_ranks: tuple[CodeChannelRank, ...]
    raw_scores: tuple[CodeChannelScore, ...]
    fused_score: float
    exact_hard_signal: bool
    explanation: str


@dataclass(frozen=True, slots=True)
class CodeHybridTrace:
    query_id: str
    exact_sparse: CodeExactSparseSearchResult | None
    dense: CodeDenseSearchResult | None
    generation_scopes: tuple[str, ...]
    channel_hits: tuple[CodeHybridHitTrace, ...]
    fusion_policy: str
    diversification_policy: str
    unavailable_reason: str = ""


@dataclass(frozen=True, slots=True)
class CodeHybridSearchResult:
    result: CodeSourceResult
    trace: CodeHybridTrace


@dataclass(frozen=True, slots=True)
class _DenseScope:
    project_id: str
    repository_id: str
    generation_id: str
    commit_sha: str
    active_only: bool
    publication: Mapping[str, Any]
    profile_state: Mapping[str, Any]

    def store_scope(self, allowed_acl_refs: Sequence[str] | None) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "repository_ids": [self.repository_id],
            "generation_id": self.generation_id,
            "allowed_acl_refs": allowed_acl_refs,
            "active_only": self.active_only,
        }


@dataclass(frozen=True, slots=True)
class _DenseRankedHit:
    row: Mapping[str, Any]
    scope: _DenseScope
    score: float
    raw_rank: int
    locator: str

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.row["generation_id"]), str(self.row["id"]))


class CodeDenseProfilePublisher:
    """Publish one complete embedding profile for one published C2 generation."""

    def __init__(
        self,
        store: CodeDenseStore,
        provider: EmbeddingProvider | None = None,
        *,
        indexer: CodeEmbeddingIndexer | None = None,
    ) -> None:
        if indexer is not None and provider is not None:
            raise ValueError("pass provider or indexer, not both")
        self.store = store
        self.provider = provider
        self.indexer = indexer or CodeEmbeddingIndexer(store, provider)

    def publish(
        self,
        units: Iterable[Mapping[str, Any] | Any],
        profile: EmbeddingProfile = DEFAULT_CODE_NL_PROFILE,
        *,
        project_id: str,
        repository_id: str,
        generation_id: str,
    ) -> CodeDensePublicationResult:
        """Build, verify, and only then mark the profile ready."""

        if not isinstance(profile, EmbeddingProfile):
            raise TypeError("profile must be an EmbeddingProfile")
        normalized = tuple(_unit_record(unit) for unit in units)
        publication = self._validate_source(
            normalized,
            project_id=project_id,
            repository_id=repository_id,
            generation_id=generation_id,
        )
        if not normalized:
            raise CodeDensePublicationError("a dense publication requires at least one C2 unit")

        self._mark_not_ready(
            publication,
            profile,
            state="building",
            reason="profile vectors are being rebuilt",
        )
        self._delete_profile_vectors(generation_id, profile.id)
        try:
            index_result = self.indexer.index(normalized, profile)
            provider = self._validated_index_result(index_result, normalized, profile)
            verified = self._verify_vectors(
                normalized,
                generation_id=generation_id,
                profile=profile,
                provider=provider,
            )
            ready = self._mark_ready(
                publication,
                profile,
                provider,
                index_result,
                unit_count=len(normalized),
                content_hash=verified["content_hash"],
                vector_hash=verified["vector_hash"],
            )
            self._validate_ready_publication(ready, profile, provider, len(normalized))
        except Exception as error:
            self._delete_profile_vectors(generation_id, profile.id)
            try:
                current = (
                    self.store.get_code_index_publication(
                        generation_id,
                        repository_id=repository_id,
                        active_only=False,
                    )
                    or publication
                )
                self._mark_not_ready(
                    current,
                    profile,
                    state="unavailable",
                    reason=f"profile publication failed: {type(error).__name__}",
                )
            except Exception:
                pass
            raise

        return CodeDensePublicationResult(
            project_id=project_id,
            repository_id=repository_id,
            generation_id=generation_id,
            profile=profile,
            provider=provider,
            unit_count=len(normalized),
            vector_count=len(normalized),
            cache_hits=index_result.cache_hits,
            cache_misses=index_result.cache_misses,
            content_hash=str(verified["content_hash"]),
            vector_hash=str(verified["vector_hash"]),
            publication=ready,
        )

    def publish_profile(
        self,
        units: Iterable[Mapping[str, Any] | Any],
        profile: EmbeddingProfile = DEFAULT_CODE_NL_PROFILE,
        **scope: str,
    ) -> CodeDensePublicationResult:
        return self.publish(units, profile, **scope)

    def _validate_source(
        self,
        units: Sequence[Mapping[str, Any]],
        *,
        project_id: str,
        repository_id: str,
        generation_id: str,
    ) -> dict[str, Any]:
        publication = self.store.get_code_index_publication(
            generation_id,
            repository_id=repository_id,
            active_only=False,
        )
        if publication is None:
            raise CodeDensePublicationError("the scoped C2 publication is missing")
        identity = (
            str(publication.get("project_id") or ""),
            str(publication.get("repository_id") or ""),
            str(publication.get("generation_id") or ""),
        )
        if identity != (project_id, repository_id, generation_id):
            raise CodeDensePublicationError("the C2 publication scope does not match the caller")
        if str(publication.get("status") or "") != "published":
            raise CodeDensePublicationError("the scoped C2 publication is not published")

        supplied: dict[str, Mapping[str, Any]] = {}
        for unit in units:
            unit_id = str(unit.get("id") or "")
            if not unit_id or unit_id in supplied:
                raise CodeDensePublicationError("caller units require unique non-empty ids")
            actual_scope = (
                str(unit.get("project_id") or ""),
                str(unit.get("repository_id") or ""),
                str(unit.get("generation_id") or ""),
            )
            if actual_scope != (project_id, repository_id, generation_id):
                raise CodeDensePublicationError("caller unit escapes the publication scope")
            content = unit.get("content")
            content_hash = str(unit.get("content_hash") or "")
            if type(content) is not str:
                raise CodeDensePublicationError("caller unit content must be text")
            if _sha256_text(content) != content_hash:
                raise CodeDensePublicationError("caller unit content hash is invalid")
            if not str(unit.get("acl_ref") or ""):
                raise CodeDensePublicationError("caller unit is missing ACL provenance")
            supplied[unit_id] = unit

        with self.store.connection() as db:
            generation = db.execute(
                """
                SELECT g.status, r.project_id
                FROM index_generations g
                JOIN repositories r ON r.id=g.repository_id
                WHERE g.id=? AND g.repository_id=?
                """,
                (generation_id, repository_id),
            ).fetchone()
            rows = db.execute(
                """
                SELECT id, project_id, repository_id, generation_id, acl_ref,
                       content, content_hash
                FROM code_retrieval_units
                WHERE project_id=? AND repository_id=? AND generation_id=?
                ORDER BY id
                """,
                (project_id, repository_id, generation_id),
            ).fetchall()
        if generation is None or str(generation["project_id"]) != project_id:
            raise CodeDensePublicationError("the scoped generation is missing")
        if str(generation["status"]) != "published":
            raise CodeDensePublicationError("the scoped generation is not published")
        if len(rows) != len(supplied):
            raise CodeDensePublicationError("caller units are not the complete C2 generation")
        for row in rows:
            unit_id = str(row["id"])
            unit = supplied.get(unit_id)
            if unit is None:
                raise CodeDensePublicationError("caller units do not match stored C2 identities")
            stored = (
                str(row["project_id"]),
                str(row["repository_id"]),
                str(row["generation_id"]),
                str(row["acl_ref"]),
                str(row["content"]),
                str(row["content_hash"]),
            )
            caller = (
                str(unit["project_id"]),
                str(unit["repository_id"]),
                str(unit["generation_id"]),
                str(unit["acl_ref"]),
                str(unit["content"]),
                str(unit["content_hash"]),
            )
            if stored != caller:
                raise CodeDensePublicationError("caller unit truth differs from stored C2 truth")
        return publication

    @staticmethod
    def _validated_index_result(
        result: CodeEmbeddingIndexResult,
        units: Sequence[Mapping[str, Any]],
        profile: EmbeddingProfile,
    ) -> EmbeddingProvenance:
        if result.profile != profile:
            raise CodeDensePublicationError("indexer returned a different embedding profile")
        if result.indexed != len(units) or result.skipped:
            raise CodeDensePublicationError("the profile did not index every published C2 unit")
        provenances = {
            trace.provenance for trace in result.provenance_trace if trace.provenance is not None
        }
        if len(provenances) != 1:
            raise CodeDensePublicationError("the indexed profile has inconsistent provenance")
        provider = provenances.pop()
        if provider.model != profile.model:
            raise CodeDensePublicationError("indexed provider model differs from the profile")
        if provider.dimension != profile.dimension:
            raise EmbeddingDimensionError("indexed provider dimension differs from the profile")
        if provider.locality != profile.locality:
            raise CodeDensePublicationError("indexed provider locality differs from the profile")
        return provider

    def _verify_vectors(
        self,
        units: Sequence[Mapping[str, Any]],
        *,
        generation_id: str,
        profile: EmbeddingProfile,
        provider: EmbeddingProvenance,
    ) -> dict[str, str]:
        with self.store.connection() as db:
            rows = db.execute(
                """
                SELECT v.unit_id, v.generation_id, v.profile, v.model, v.dimension,
                       v.vector, v.content_hash, u.content_hash AS unit_content_hash
                FROM code_unit_vectors v
                JOIN code_retrieval_units u
                  ON u.id=v.unit_id AND u.generation_id=v.generation_id
                WHERE v.generation_id=? AND v.profile=?
                ORDER BY v.unit_id, v.model
                """,
                (generation_id, profile.id),
            ).fetchall()
        if len(rows) != len(units):
            raise CodeDensePublicationError("stored vector count differs from the C2 unit count")
        expected = {str(unit["id"]): str(unit["content_hash"]) for unit in units}
        vector_digest = hashlib.sha256()
        for row in rows:
            unit_id = str(row["unit_id"])
            content_hash = str(row["content_hash"])
            if expected.get(unit_id) != content_hash:
                raise CodeDensePublicationError("stored vector content hash differs from its unit")
            if content_hash != str(row["unit_content_hash"]):
                raise CodeDensePublicationError("stored vector and joined unit hashes differ")
            if str(row["generation_id"]) != generation_id:
                raise CodeDensePublicationError("stored vector generation differs from publication")
            if str(row["profile"]) != profile.id or str(row["model"]) != provider.model:
                raise CodeDensePublicationError(
                    "stored vector profile/model differs from publication"
                )
            if int(row["dimension"]) != profile.dimension:
                raise EmbeddingDimensionError("stored vector dimension differs from publication")
            vector = bytes(row["vector"])
            _validate_vector(vector, profile.dimension, source="stored vector")
            vector_digest.update(unit_id.encode())
            vector_digest.update(b"\0")
            vector_digest.update(content_hash.encode())
            vector_digest.update(b"\0")
            vector_digest.update(vector)
            vector_digest.update(b"\0")
        return {
            "content_hash": _generation_content_hash(units),
            "vector_hash": "sha256:" + vector_digest.hexdigest(),
        }

    def _delete_profile_vectors(self, generation_id: str, profile_id: str) -> None:
        with self.store.transaction() as db:
            db.execute(
                "DELETE FROM code_unit_vectors WHERE generation_id=? AND profile=?",
                (generation_id, profile_id),
            )

    def _mark_not_ready(
        self,
        publication: Mapping[str, Any],
        profile: EmbeddingProfile,
        *,
        state: str,
        reason: str,
    ) -> dict[str, Any]:
        validation = _publication_validation(publication)
        profiles = _profile_states(validation)
        profiles[profile.id] = {
            "profile": profile.canonical_snapshot(),
            "reason": reason,
            "status": state,
        }
        validation["embedding_profiles"] = profiles
        capabilities = _capabilities(validation)
        capabilities["dense_retrieval"] = False
        validation["capabilities"] = capabilities
        validation["dense_model_candidates"] = [
            item.canonical_snapshot() for item in DENSE_MODEL_CANDIDATES
        ]
        return self.store.upsert_code_index_publication(
            _publication_record(
                publication,
                embedding="not-built",
                validation=validation,
            )
        )

    def _mark_ready(
        self,
        publication: Mapping[str, Any],
        profile: EmbeddingProfile,
        provider: EmbeddingProvenance,
        index_result: CodeEmbeddingIndexResult,
        *,
        unit_count: int,
        content_hash: str,
        vector_hash: str,
    ) -> dict[str, Any]:
        current = (
            self.store.get_code_index_publication(
                str(publication["generation_id"]),
                repository_id=str(publication["repository_id"]),
                active_only=False,
            )
            or publication
        )
        validation = _publication_validation(current)
        profiles = _profile_states(validation)
        profiles[profile.id] = {
            "cache_hits": index_result.cache_hits,
            "cache_misses": index_result.cache_misses,
            "content_hash": content_hash,
            "profile": profile.canonical_snapshot(),
            "provider": provider.canonical_snapshot(),
            "publication_version": DENSE_PUBLICATION_VERSION,
            "status": "ready",
            "unit_count": unit_count,
            "vector_count": unit_count,
            "vector_hash": vector_hash,
        }
        validation["embedding_profiles"] = profiles
        capabilities = _capabilities(validation)
        capabilities["dense_retrieval"] = True
        validation["capabilities"] = capabilities
        validation["dense_model_candidates"] = [
            item.canonical_snapshot() for item in DENSE_MODEL_CANDIDATES
        ]
        return self.store.upsert_code_index_publication(
            _publication_record(
                current,
                embedding=f"{profile.id}@{profile.revision}",
                validation=validation,
            )
        )

    @staticmethod
    def _validate_ready_publication(
        publication: Mapping[str, Any],
        profile: EmbeddingProfile,
        provider: EmbeddingProvenance,
        expected_count: int,
    ) -> None:
        reason = _profile_unavailable_reason(publication, profile)
        if reason:
            raise CodeDensePublicationError(reason)
        state = _ready_profile_state(publication, profile)
        if state is None:
            raise CodeDensePublicationError("ready profile state disappeared after publication")
        if state.get("provider") != provider.canonical_snapshot():
            raise CodeDensePublicationError("published provider snapshot changed")
        if int(state.get("unit_count", -1)) != expected_count:
            raise CodeDensePublicationError("published unit count changed")
        if int(state.get("vector_count", -1)) != expected_count:
            raise CodeDensePublicationError("published vector count changed")


class CodeDenseRetriever:
    """Scan one ready Code V2 profile with strict scope and provenance checks."""

    def __init__(
        self,
        store: CodeDenseStore,
        embedding_profile: EmbeddingProfile = DEFAULT_CODE_NL_PROFILE,
        provider: EmbeddingProvider | None = None,
    ) -> None:
        if not isinstance(embedding_profile, EmbeddingProfile):
            raise TypeError("embedding_profile must be an EmbeddingProfile")
        self.store = store
        self.embedding_profile = embedding_profile
        self.provider = provider

    def search(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
    ) -> CodeSourceResult:
        return self.search_with_trace(request, profile).result

    def search_with_trace(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
    ) -> CodeDenseSearchResult:
        started = time.perf_counter()
        if not isinstance(request, EvidenceSearchRequest):
            raise TypeError("request must be an EvidenceSearchRequest")
        if profile is not None and not isinstance(profile, CodeQueryProfile):
            raise TypeError("profile must be a CodeQueryProfile")
        query_profile = profile or self.profile_for_request(request)
        if query_profile.budget.dense_candidates == 0:
            raise ValueError("profile must enable dense candidates")
        query_id = _query_id(
            "dense",
            request,
            query_profile,
            self.embedding_profile,
        )

        try:
            scopes = self._resolve_scopes(request, query_profile)
        except CodeDenseUnavailableError as error:
            return self._failed(
                query_id,
                message=str(error),
                unavailable=True,
                scopes=(),
                started=started,
            )
        except Exception as error:
            return self._failed(
                query_id,
                message=_safe_message("Code V2 dense scope resolution failed", error),
                unavailable=False,
                scopes=(),
                started=started,
            )

        try:
            provider = self._query_provider(scopes)
            query_vector = self._embed_query(request.query, provider)
        except (CodeDenseUnavailableError, EmbeddingProviderUnavailableError) as error:
            return self._failed(
                query_id,
                message=str(error),
                unavailable=True,
                scopes=scopes,
                started=started,
            )
        except Exception as error:
            return self._failed(
                query_id,
                message=_safe_message("Code V2 query embedding failed", error),
                unavailable=False,
                scopes=scopes,
                started=started,
            )

        try:
            hits = self._dense_hits(
                request,
                scopes,
                query_vector,
                candidate_limit=query_profile.budget.dense_candidates,
            )
            selected, decisions = _diversify_dense(
                hits,
                top_k=min(
                    request.limit,
                    query_profile.budget.total_candidates,
                    query_profile.budget.dense_candidates,
                ),
            )
            candidates = tuple(
                self._candidate(
                    hit, rank, explicit_version=_has_explicit_version(request, query_profile)
                )
                for rank, hit in enumerate(selected)
            )
        except CodeDenseUnavailableError as error:
            return self._failed(
                query_id,
                message=str(error),
                unavailable=True,
                scopes=scopes,
                started=started,
                provider=provider,
            )
        except Exception as error:
            return self._failed(
                query_id,
                message=_safe_message("Code V2 dense retrieval failed", error),
                unavailable=False,
                scopes=scopes,
                started=started,
                provider=provider,
            )

        outcomes, errors, status = _dense_outcomes(
            candidates=candidates,
            hit_count=len(hits),
        )
        result = CodeSourceResult(
            query_id=query_id,
            status=status,
            channel_outcomes=outcomes,
            candidates=candidates,
            context_blocks=(),
            index_version=_dense_index_version(scopes, self.embedding_profile),
            watermark=_watermark(scopes),
            latency_ms=_elapsed_ms(started),
            errors=errors,
        )
        trace = CodeDenseTrace(
            query_id=query_id,
            profile_snapshot=self.embedding_profile.canonical_snapshot(),
            provider=provider.provenance.canonical_snapshot(),
            generation_scopes=_scope_labels(scopes),
            channel_hits=tuple(_dense_hit_trace(hit, decisions.get(hit.key)) for hit in hits),
            similarity="cosine-v1",
        )
        return CodeDenseSearchResult(result=result, trace=trace)

    def profile_for_request(self, request: EvidenceSearchRequest) -> CodeQueryProfile:
        target_ref = request.scope.commit or request.scope.branch or "current"
        return CodeQueryProfile(
            task=CodeTask.IMPLEMENTATION,
            target_ref=unicodedata.normalize("NFC", target_ref).strip() or "current",
        )

    def _resolve_scopes(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile,
    ) -> tuple[_DenseScope, ...]:
        repositories = tuple(dict.fromkeys(request.scope.repository_ids))
        uri_repository, uri_ref = _query_uri_scope(request.query)
        if uri_repository:
            if repositories and uri_repository not in repositories:
                raise CodeDenseUnavailableError(
                    "Code URI repository is outside the requested scope"
                )
            repositories = (uri_repository,)
        reference = _requested_reference(request, profile, uri_ref)
        rows = self._generation_rows(
            project_id=request.scope.project_id,
            repository_ids=repositories or None,
            reference=reference,
        )
        if not rows:
            raise CodeDenseUnavailableError(
                "No Code V2 generation resolves inside the requested scope"
            )
        if reference is None and repositories:
            found = {str(row["repository_id"]) for row in rows}
            if found != set(repositories):
                raise CodeDenseUnavailableError(
                    "An explicitly scoped repository has no active Code V2 generation"
                )

        scopes: list[_DenseScope] = []
        for row in rows:
            if str(row["generation_status"]) != "published":
                raise CodeDenseUnavailableError("The resolved Code V2 generation is not published")
            repository_id = str(row["repository_id"])
            generation_id = str(row["generation_id"])
            project_id = str(row["project_id"])
            active_only = bool(row["active_only"])
            publication = self.store.get_code_index_publication(
                generation_id,
                repository_id=repository_id,
                active_only=active_only,
            )
            if publication is not None:
                identity = (
                    str(publication.get("project_id") or ""),
                    str(publication.get("repository_id") or ""),
                    str(publication.get("generation_id") or ""),
                )
                if identity != (project_id, repository_id, generation_id):
                    raise CodeDenseUnavailableError(
                        "The Code V2 dense publication provenance does not match its scope"
                    )
            reason = _profile_unavailable_reason(publication, self.embedding_profile)
            if reason:
                raise CodeDenseUnavailableError(reason)
            assert publication is not None
            profile_state = _ready_profile_state(publication, self.embedding_profile)
            assert profile_state is not None
            scopes.append(
                _DenseScope(
                    project_id=project_id,
                    repository_id=repository_id,
                    generation_id=generation_id,
                    commit_sha=str(row["commit_sha"]),
                    active_only=active_only,
                    publication=publication,
                    profile_state=profile_state,
                )
            )
        return tuple(sorted(scopes, key=lambda item: (item.repository_id, item.generation_id)))

    def _generation_rows(
        self,
        *,
        project_id: str | None,
        repository_ids: Sequence[str] | None,
        reference: tuple[str, str] | None,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        values: list[Any] = []
        if project_id is not None:
            clauses.append("r.project_id=?")
            values.append(project_id)
        if repository_ids is not None:
            if not repository_ids:
                return []
            marks = ",".join("?" for _ in repository_ids)
            clauses.append(f"r.id IN ({marks})")
            values.extend(repository_ids)
        with self.store.connection() as db:
            if reference is None:
                rows = db.execute(
                    f"""
                    SELECT r.project_id, r.id AS repository_id,
                           r.active_generation_id AS generation_id,
                           g.commit_sha, g.status AS generation_status,
                           1 AS active_only
                    FROM repositories r
                    JOIN index_generations g
                      ON g.id=r.active_generation_id AND g.repository_id=r.id
                    WHERE r.active_generation_id IS NOT NULL
                      AND {" AND ".join(clauses)}
                    ORDER BY r.id
                    """,
                    values,
                ).fetchall()
                return [dict(row) for row in rows]

            kind, value = reference
            if kind == "tag":
                return []
            if kind == "generation":
                reference_clause = "g.id=?"
                reference_values: list[Any] = [value]
            elif kind == "full_sha":
                reference_clause = "lower(g.commit_sha)=?"
                reference_values = [value.casefold()]
            elif kind == "short_sha":
                reference_clause = "lower(g.commit_sha) LIKE ?"
                reference_values = [value.casefold() + "%"]
            elif kind == "branch":
                heads = _branch_heads(db, value, project_id, repository_ids)
                if not heads:
                    return []
                marks = ",".join("(?, ?)" for _ in heads)
                reference_clause = f"(g.repository_id, lower(g.commit_sha)) IN ({marks})"
                reference_values = [
                    item for repository_id, sha in heads for item in (repository_id, sha.casefold())
                ]
            else:
                return []
            rows = db.execute(
                f"""
                SELECT r.project_id, r.id AS repository_id, g.id AS generation_id,
                       g.commit_sha, g.status AS generation_status,
                       CASE WHEN r.active_generation_id=g.id THEN 1 ELSE 0 END AS active_only
                FROM index_generations g
                JOIN repositories r ON r.id=g.repository_id
                WHERE {" AND ".join(clauses)}
                  AND {reference_clause}
                ORDER BY r.id, g.id
                """,
                [*values, *reference_values],
            ).fetchall()
        decoded = [dict(row) for row in rows]
        if kind in {"full_sha", "short_sha", "generation"} and len(decoded) > 1:
            return []
        return decoded

    def _query_provider(
        self,
        scopes: Sequence[_DenseScope],
    ) -> EmbeddingProvider:
        provider = self.provider
        if provider is None:
            if (
                self.embedding_profile.locality != "local"
                or self.embedding_profile.model != LOCAL_HASH_MODEL
            ):
                raise CodeDenseUnavailableError(
                    "the embedding profile has no configured offline query provider"
                )
            provider = LocalHashCodeEmbeddingProvider(self.embedding_profile.dimension)
        try:
            available = provider.available
            provenance = provider.provenance
        except Exception as error:
            raise CodeDenseUnavailableError(
                "the embedding query provider is unavailable"
            ) from error
        if available is not True:
            raise CodeDenseUnavailableError("the embedding query provider is unavailable")
        if not isinstance(provenance, EmbeddingProvenance):
            raise CodeDenseUnavailableError("the query provider has invalid provenance")
        expected = (
            self.embedding_profile.model,
            self.embedding_profile.dimension,
            self.embedding_profile.locality,
        )
        actual = (provenance.model, provenance.dimension, provenance.locality)
        if actual != expected:
            raise CodeDenseUnavailableError(
                "the query provider model/dimension/locality differs from the profile"
            )
        for scope in scopes:
            if scope.profile_state.get("provider") != provenance.canonical_snapshot():
                raise CodeDenseUnavailableError(
                    "the query provider differs from the published provider snapshot"
                )
        return provider

    def _embed_query(
        self,
        query: str,
        provider: EmbeddingProvider,
    ) -> bytes:
        try:
            batch = provider.embed_batch((query,), profile=self.embedding_profile)
        except EmbeddingProviderUnavailableError:
            raise
        except Exception as error:
            raise CodeDenseError("embedding query provider failed") from error
        if not isinstance(batch, EmbeddingBatchResult) or len(batch.vectors) != 1:
            raise CodeDenseError("embedding query provider returned an incomplete batch")
        if batch.provenance != provider.provenance:
            raise CodeDenseError("embedding query provider provenance changed")
        vector = batch.vectors[0]
        _validate_vector(vector, self.embedding_profile.dimension, source="query vector")
        return vector

    def _dense_hits(
        self,
        request: EvidenceSearchRequest,
        scopes: Sequence[_DenseScope],
        query_vector: bytes,
        *,
        candidate_limit: int,
    ) -> tuple[_DenseRankedHit, ...]:
        allowed_acl_refs = _allowed_acl_refs(request)
        scored: list[tuple[float, dict[str, Any], _DenseScope, str]] = []
        for scope in scopes:
            vectors = list(
                self.store.iter_code_unit_vectors(
                    **scope.store_scope(allowed_acl_refs),
                    profile=self.embedding_profile.id,
                    model=self.embedding_profile.model,
                    limit=None,
                )
            )
            if allowed_acl_refs is None:
                expected = int(scope.profile_state.get("vector_count", -1))
                if len(vectors) != expected:
                    raise CodeDenseUnavailableError(
                        "the published dense profile is incomplete for its generation"
                    )
            for vector_row in vectors:
                _validate_vector_scope(vector_row, scope, self.embedding_profile)
                unit = self.store.load_code_unit(
                    str(vector_row["unit_id"]),
                    project_id=scope.project_id,
                    repository_id=scope.repository_id,
                    generation_id=scope.generation_id,
                    allowed_acl_refs=allowed_acl_refs,
                    active_only=scope.active_only,
                )
                if unit is None:
                    raise CodeDenseUnavailableError(
                        "a scoped vector has no readable retrieval unit"
                    )
                _validate_unit_scope(unit, scope)
                if str(vector_row["content_hash"]) != str(unit["content_hash"]):
                    raise CodeDenseUnavailableError(
                        "a scoped vector content hash differs from its retrieval unit"
                    )
                score = _cosine_similarity(query_vector, bytes(vector_row["vector"]))
                if score <= 0.0:
                    continue
                locator = _evidence_locator(unit, scope)
                scored.append((score, unit, scope, locator))
        scored.sort(
            key=lambda item: (
                -item[0],
                item[2].repository_id,
                str(item[1].get("path") or ""),
                _optional_int(item[1].get("start_line")),
                int(item[1].get("ordinal") or 0),
                str(item[1]["id"]),
                item[2].generation_id,
            )
        )
        limited = scored[:candidate_limit]
        return tuple(
            _DenseRankedHit(
                row=row,
                scope=scope,
                score=float(score),
                raw_rank=rank,
                locator=locator,
            )
            for rank, (score, row, scope, locator) in enumerate(limited)
        )

    def _candidate(
        self,
        hit: _DenseRankedHit,
        source_rank: int,
        *,
        explicit_version: bool,
    ) -> CodeRetrievalCandidate:
        row = hit.row
        scope = hit.scope
        entity_id = str(row["entity_id"])
        metadata = row.get("metadata")
        entity_type = str(row.get("unit_type") or "CodeRetrievalUnit")
        if isinstance(metadata, Mapping):
            projection = metadata.get("entity_projection")
            if isinstance(projection, Mapping) and projection.get("entity_type"):
                entity_type = str(projection["entity_type"])
        stable_version = _stable_version(row, scope)
        if stable_version != scope.commit_sha:
            raise CodeDenseUnavailableError(
                "retrieval unit version conflicts with its published generation"
            )
        relation_path = CodeRelationPath(
            nodes=(
                CodeRelationNode(
                    entity_id=entity_id,
                    repository_id=scope.repository_id,
                    stable_version=stable_version,
                    source_generation=scope.generation_id,
                    locator=hit.locator,
                    acl_ref=str(row["acl_ref"]),
                ),
            ),
            edges=(),
            path_score=0.0,
        )
        return CodeRetrievalCandidate(
            entity_id=entity_id,
            retrieval_unit_id=str(row["id"]),
            repository_id=scope.repository_id,
            entity_type=entity_type,
            stable_version=stable_version,
            source_generation=scope.generation_id,
            raw_channel_scores=(
                CodeChannelScore(channel=CodeRetrievalChannel.DENSE, score=hit.score),
            ),
            raw_channel_ranks=(
                CodeChannelRank(channel=CodeRetrievalChannel.DENSE, rank=hit.raw_rank),
            ),
            within_source_rank=source_rank,
            source_fused_score=hit.score,
            calibrated_relevance=CodeUncalibratedScore(
                status="unavailable",
                reason="calibration is deferred beyond C3-02",
            ),
            version_alignment=(
                CodeVersionAlignment.EXACT if explicit_version else CodeVersionAlignment.COMPATIBLE
            ),
            fact_status=CodeFactStatus.OBSERVED,
            derivation=CodeDerivation.TREE_SITTER,
            review_status=CodeReviewStatus.MACHINE_CONFIRMED,
            role=CodeCandidateRole.TARGET,
            relation_path=relation_path,
            locator=hit.locator,
            token_estimate=max(0, int(row.get("token_count") or 0)),
            acl_ref=str(row["acl_ref"]),
        )

    def _failed(
        self,
        query_id: str,
        *,
        message: str,
        unavailable: bool,
        scopes: Sequence[_DenseScope],
        started: float,
        provider: EmbeddingProvider | None = None,
    ) -> CodeDenseSearchResult:
        outcome: CodeChannelOutcome
        kind: str
        if unavailable:
            outcome = CodeChannelUnavailable(
                channel=CodeRetrievalChannel.DENSE,
                error=message,
            )
            kind = "unavailable"
            status = CodeSourceStatus.UNAVAILABLE
        else:
            outcome = CodeChannelError(
                channel=CodeRetrievalChannel.DENSE,
                error=message,
            )
            kind = "error"
            status = CodeSourceStatus.UNAVAILABLE
        outcomes = tuple(
            outcome
            if channel is CodeRetrievalChannel.DENSE
            else CodeChannelDisabled(
                channel=channel,
                reason=f"{DENSE_RETRIEVER_VERSION} does not implement this channel",
            )
            for channel in CodeRetrievalChannel
        )
        result = CodeSourceResult(
            query_id=query_id,
            status=status,
            channel_outcomes=outcomes,
            candidates=(),
            context_blocks=(),
            index_version=DENSE_RETRIEVER_VERSION,
            watermark=_watermark(scopes),
            latency_ms=_elapsed_ms(started),
            errors=(
                CodeChannelFailure(
                    channel=CodeRetrievalChannel.DENSE,
                    kind=kind,
                    message=message,
                ),
            ),
        )
        trace = CodeDenseTrace(
            query_id=query_id,
            profile_snapshot=self.embedding_profile.canonical_snapshot(),
            provider=(provider.provenance.canonical_snapshot() if provider is not None else None),
            generation_scopes=_scope_labels(scopes),
            channel_hits=(),
            similarity="cosine-v1",
            unavailable_reason=message,
        )
        return CodeDenseSearchResult(result=result, trace=trace)


class CodeHybridV2Retriever:
    """Combine the read-only C2 exact/sparse result with C3-02 dense hits."""

    def __init__(
        self,
        store: CodeDenseStore,
        *,
        dense_retriever: CodeDenseRetriever | None = None,
        exact_sparse_retriever: CodeExactSparseRetriever | None = None,
        rrf_k: int = DEFAULT_RRF_K,
        exact_weight: float = DEFAULT_EXACT_WEIGHT,
        sparse_weight: float = DEFAULT_SPARSE_WEIGHT,
        dense_weight: float = DEFAULT_DENSE_WEIGHT,
    ) -> None:
        if isinstance(rrf_k, bool) or not isinstance(rrf_k, int) or rrf_k < 1:
            raise ValueError("rrf_k must be a positive integer")
        for name, value in (
            ("exact_weight", exact_weight),
            ("sparse_weight", sparse_weight),
            ("dense_weight", dense_weight),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive finite float")
        self.store = store
        self.dense_retriever = dense_retriever or CodeDenseRetriever(store)
        self.exact_sparse_retriever = exact_sparse_retriever or CodeExactSparseRetriever(store)
        self.rrf_k = rrf_k
        self.weights = {
            CodeRetrievalChannel.EXACT: float(exact_weight),
            CodeRetrievalChannel.SPARSE: float(sparse_weight),
            CodeRetrievalChannel.DENSE: float(dense_weight),
        }

    def search(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
    ) -> CodeSourceResult:
        return self.search_with_trace(request, profile).result

    def search_with_trace(
        self,
        request: EvidenceSearchRequest,
        profile: CodeQueryProfile | None = None,
    ) -> CodeHybridSearchResult:
        started = time.perf_counter()
        if not isinstance(request, EvidenceSearchRequest):
            raise TypeError("request must be an EvidenceSearchRequest")
        if profile is not None and not isinstance(profile, CodeQueryProfile):
            raise TypeError("profile must be a CodeQueryProfile")
        query_profile = profile or self.exact_sparse_retriever.profile_for_request(request)
        enabled = (
            query_profile.budget.exact_candidates,
            query_profile.budget.sparse_candidates,
            query_profile.budget.dense_candidates,
        )
        if not any(enabled):
            raise ValueError("profile must enable exact, sparse, or dense candidates")
        pool_limit = max(1, min(50, query_profile.budget.total_candidates))
        expanded = EvidenceSearchRequest(
            query=request.query,
            scope=request.scope,
            limit=pool_limit,
            include_edges=request.include_edges,
        )

        exact_response: CodeExactSparseSearchResult | None = None
        dense_response: CodeDenseSearchResult | None = None
        if query_profile.budget.exact_candidates or query_profile.budget.sparse_candidates:
            exact_response = self.exact_sparse_retriever.search_with_trace(
                expanded,
                query_profile,
            )
        if query_profile.budget.dense_candidates:
            dense_response = self.dense_retriever.search_with_trace(
                expanded,
                query_profile,
            )

        source_candidates = [
            *(exact_response.result.candidates if exact_response is not None else ()),
            *(dense_response.result.candidates if dense_response is not None else ()),
        ]
        candidates, hybrid_hits = self._fuse(
            source_candidates,
            top_k=min(request.limit, query_profile.budget.total_candidates),
            exact_response=exact_response,
        )
        outcomes, errors, status = _hybrid_outcomes(
            query_profile,
            candidates,
            exact_response,
            dense_response,
        )
        query_id = _query_id(
            "hybrid",
            request,
            query_profile,
            self.dense_retriever.embedding_profile,
        )
        scopes = _hybrid_scopes(exact_response, dense_response)
        result = CodeSourceResult(
            query_id=query_id,
            status=status,
            channel_outcomes=outcomes,
            candidates=candidates,
            context_blocks=(),
            index_version=_hybrid_index_version(exact_response, dense_response),
            watermark=_hybrid_watermark(exact_response, dense_response),
            latency_ms=_elapsed_ms(started),
            errors=errors,
        )
        unavailable = "; ".join(
            reason
            for reason in (
                exact_response.trace.unavailable_reason if exact_response else "",
                dense_response.trace.unavailable_reason if dense_response else "",
            )
            if reason
        )
        trace = CodeHybridTrace(
            query_id=query_id,
            exact_sparse=exact_response,
            dense=dense_response,
            generation_scopes=scopes,
            channel_hits=hybrid_hits,
            fusion_policy=(
                f"{DENSE_FUSION_POLICY}:k={self.rrf_k}:"
                f"exact={self.weights[CodeRetrievalChannel.EXACT]:g}:"
                f"sparse={self.weights[CodeRetrievalChannel.SPARSE]:g}:"
                f"dense={self.weights[CodeRetrievalChannel.DENSE]:g}:hard-exact-first"
            ),
            diversification_policy=DENSE_DIVERSIFICATION_POLICY,
            unavailable_reason=unavailable,
        )
        return CodeHybridSearchResult(result=result, trace=trace)

    def _fuse(
        self,
        source_candidates: Sequence[CodeRetrievalCandidate],
        *,
        top_k: int,
        exact_response: CodeExactSparseSearchResult | None,
    ) -> tuple[tuple[CodeRetrievalCandidate, ...], tuple[CodeHybridHitTrace, ...]]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in source_candidates:
            key = (candidate.source_generation, candidate.retrieval_unit_id)
            item = merged.setdefault(
                key,
                {
                    "base": candidate,
                    "ranks": {},
                    "scores": {},
                },
            )
            if any(
                rank.channel is CodeRetrievalChannel.EXACT for rank in candidate.raw_channel_ranks
            ):
                item["base"] = candidate
            for rank in candidate.raw_channel_ranks:
                item["ranks"][rank.channel] = rank.rank
            for score in candidate.raw_channel_scores:
                item["scores"][score.channel] = score.score

        ranked: list[tuple[bool, float, CodeRetrievalCandidate, dict[str, Any]]] = []
        for item in merged.values():
            ranks: dict[CodeRetrievalChannel, int] = item["ranks"]
            fused = sum(
                self.weights[channel] / (self.rrf_k + rank + 1)
                for channel, rank in ranks.items()
                if channel in self.weights
            )
            hard_exact = CodeRetrievalChannel.EXACT in ranks
            ranked.append((hard_exact, float(fused), item["base"], item))
        ranked.sort(
            key=lambda entry: (
                not entry[0],
                entry[3]["ranks"].get(CodeRetrievalChannel.EXACT, 10**9),
                -entry[1],
                entry[3]["ranks"].get(CodeRetrievalChannel.SPARSE, 10**9),
                entry[3]["ranks"].get(CodeRetrievalChannel.DENSE, 10**9),
                entry[2].repository_id,
                entry[2].locator,
                entry[2].retrieval_unit_id,
                entry[2].source_generation,
            )
        )
        selected = _diversify_hybrid(ranked, top_k=top_k)
        exact_explanations = {
            (trace.generation_id, trace.retrieval_unit_id): trace.explanation
            for trace in (exact_response.trace.channel_hits if exact_response is not None else ())
            if trace.channel is CodeRetrievalChannel.EXACT
        }
        candidates: list[CodeRetrievalCandidate] = []
        traces: list[CodeHybridHitTrace] = []
        for source_rank, (hard_exact, fused, base, item) in enumerate(selected):
            ranks = tuple(
                CodeChannelRank(channel=channel, rank=rank)
                for channel, rank in item["ranks"].items()
            )
            scores = tuple(
                CodeChannelScore(channel=channel, score=score)
                for channel, score in item["scores"].items()
            )
            payload = base.model_dump(mode="python", round_trip=True)
            payload.update(
                {
                    "raw_channel_ranks": ranks,
                    "raw_channel_scores": scores,
                    "source_fused_score": fused,
                    "within_source_rank": source_rank,
                }
            )
            candidate = CodeRetrievalCandidate.model_validate(payload)
            candidates.append(candidate)
            explanation = exact_explanations.get(
                (candidate.source_generation, candidate.retrieval_unit_id),
                "deterministic channel-rank fusion with a real source locator",
            )
            traces.append(
                CodeHybridHitTrace(
                    retrieval_unit_id=candidate.retrieval_unit_id,
                    entity_id=candidate.entity_id,
                    locator=candidate.locator,
                    raw_ranks=candidate.raw_channel_ranks,
                    raw_scores=candidate.raw_channel_scores,
                    fused_score=fused,
                    exact_hard_signal=hard_exact,
                    explanation=explanation,
                )
            )
        return tuple(candidates), tuple(traces)


def _unit_record(unit: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    if isinstance(unit, Mapping):
        return unit
    adapter = getattr(unit, "as_store_record", None)
    if not callable(adapter):
        raise TypeError("units must be mappings or expose as_store_record()")
    record = adapter()
    if not isinstance(record, Mapping):
        raise TypeError("as_store_record() must return a mapping")
    return record


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generation_content_hash(units: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for unit in sorted(units, key=lambda item: str(item["id"])):
        digest.update(str(unit["id"]).encode())
        digest.update(b"\0")
        digest.update(str(unit["content_hash"]).encode())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _publication_validation(publication: Mapping[str, Any]) -> dict[str, Any]:
    raw = publication.get("validation")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _capabilities(validation: Mapping[str, Any]) -> dict[str, Any]:
    raw = validation.get("capabilities")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _profile_states(validation: Mapping[str, Any]) -> dict[str, Any]:
    raw = validation.get("embedding_profiles")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _publication_record(
    publication: Mapping[str, Any],
    *,
    embedding: str,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "builder": str(publication["builder"]),
        "embedding": embedding,
        "generation_id": str(publication["generation_id"]),
        "graph": str(publication["graph"]),
        "project_id": str(publication["project_id"]),
        "repository_id": str(publication["repository_id"]),
        "sparse": str(publication["sparse"]),
        "status": str(publication["status"]),
        "validation": dict(validation),
    }


def _ready_profile_state(
    publication: Mapping[str, Any],
    profile: EmbeddingProfile,
) -> Mapping[str, Any] | None:
    validation = publication.get("validation")
    if not isinstance(validation, Mapping):
        return None
    profiles = validation.get("embedding_profiles")
    if not isinstance(profiles, Mapping):
        return None
    state = profiles.get(profile.id)
    return state if isinstance(state, Mapping) else None


def _profile_unavailable_reason(
    publication: Mapping[str, Any] | None,
    profile: EmbeddingProfile,
) -> str:
    if publication is None:
        return "The resolved Code V2 dense publication is missing or inactive"
    if str(publication.get("status") or "") != "published":
        return "The resolved Code V2 publication is not published"
    embedding = str(publication.get("embedding") or "")
    if embedding.casefold() in _NOT_BUILT:
        return "The resolved Code V2 embedding profile is not ready"
    if embedding != f"{profile.id}@{profile.revision}":
        return f"The requested embedding profile {profile.id} is not the active profile"
    validation = publication.get("validation")
    if not isinstance(validation, Mapping):
        return "The resolved Code V2 embedding validation is missing"
    capabilities = validation.get("capabilities")
    if not isinstance(capabilities, Mapping) or capabilities.get("dense_retrieval") is not True:
        return "The resolved Code V2 publication declares dense retrieval unavailable"
    state = _ready_profile_state(publication, profile)
    if state is None:
        return f"The requested embedding profile {profile.id} is missing"
    if state.get("status") != "ready":
        return f"The requested embedding profile {profile.id} is not ready"
    if state.get("profile") != profile.canonical_snapshot():
        return f"The requested embedding profile {profile.id} snapshot does not match"
    provider = state.get("provider")
    if not isinstance(provider, Mapping):
        return f"The requested embedding profile {profile.id} provider is missing"
    if provider.get("model") != profile.model:
        return f"The requested embedding profile {profile.id} model does not match"
    if provider.get("dimension") != profile.dimension:
        return f"The requested embedding profile {profile.id} dimension does not match"
    if provider.get("locality") != profile.locality:
        return f"The requested embedding profile {profile.id} locality does not match"
    if not str(provider.get("provider") or "") or not str(provider.get("revision") or ""):
        return f"The requested embedding profile {profile.id} provenance is incomplete"
    unit_count = state.get("unit_count")
    vector_count = state.get("vector_count")
    if (
        isinstance(unit_count, bool)
        or not isinstance(unit_count, int)
        or isinstance(vector_count, bool)
        or not isinstance(vector_count, int)
        or unit_count < 1
        or vector_count != unit_count
    ):
        return f"The requested embedding profile {profile.id} is incomplete"
    if not all(
        str(state.get(field) or "").startswith("sha256:")
        for field in ("content_hash", "vector_hash")
    ):
        return f"The requested embedding profile {profile.id} hashes are incomplete"
    return ""


def _validate_vector(vector: bytes, dimension: int, *, source: str) -> None:
    if not isinstance(vector, bytes) or len(vector) != dimension * 4:
        raise EmbeddingDimensionError(f"{source} byte length does not match dimension")
    values = struct.unpack(f"<{dimension}f", vector)
    if not all(math.isfinite(value) for value in values):
        raise EmbeddingDimensionError(f"{source} contains non-finite values")


def _cosine_similarity(left: bytes, right: bytes) -> float:
    if len(left) != len(right) or len(left) % 4:
        raise EmbeddingDimensionError("query and stored vector dimensions differ")
    dimension = len(left) // 4
    left_values = struct.unpack(f"<{dimension}f", left)
    right_values = struct.unpack(f"<{dimension}f", right)
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if not left_norm or not right_norm:
        return 0.0
    cosine = sum(
        left_value * right_value
        for left_value, right_value in zip(left_values, right_values, strict=True)
    ) / (left_norm * right_norm)
    return float(max(0.0, min(1.0, cosine)))


def _query_uri_scope(query: str) -> tuple[str, str]:
    repositories: set[str] = set()
    refs: set[str] = set()
    for match in _URI_RE.finditer(query):
        payload = match.group(0).rstrip("),.;]").removeprefix("code://")
        base = payload.partition("#")[0]
        if "@" not in base:
            continue
        owner, remainder = base.rsplit("@", 1)
        ref = remainder.partition("/")[0]
        if owner:
            repositories.add(owner)
        if ref:
            refs.add(ref)
    if len(repositories) > 1:
        raise CodeDenseUnavailableError("Code URI locators span more than one repository")
    if len(refs) > 1:
        raise CodeDenseUnavailableError("Code URI locators span more than one version")
    return next(iter(repositories), ""), next(iter(refs), "")


def _requested_reference(
    request: EvidenceSearchRequest,
    profile: CodeQueryProfile,
    uri_ref: str,
) -> tuple[str, str] | None:
    references: list[tuple[str, str]] = []

    def add(value: str | None, kind: str | None = None) -> None:
        normalized = (value or "").strip()
        if not normalized or normalized == "current":
            return
        if normalized.startswith("refs/heads/"):
            kind = "branch"
            normalized = normalized.removeprefix("refs/heads/")
        elif normalized.startswith("refs/tags/"):
            kind = "tag"
            normalized = normalized.removeprefix("refs/tags/")
        elif normalized.casefold().startswith("branch:"):
            kind = "branch"
            normalized = normalized.split(":", 1)[1]
        elif normalized.casefold().startswith("tag:"):
            kind = "tag"
            normalized = normalized.split(":", 1)[1]
        references.append((kind or _reference_kind(normalized), normalized))

    add(request.scope.commit)
    add(request.scope.branch, "branch" if request.scope.branch else None)
    add(profile.target_ref)
    add(uri_ref)
    for match in _LABELED_REF_RE.finditer(request.query):
        add(match.group(2), match.group(1).casefold())
    for match in _FULL_SHA_RE.finditer(request.query):
        add(match.group(1), "full_sha")
    canonical = {(kind, value.casefold() if "sha" in kind else value) for kind, value in references}
    if not canonical:
        return None
    if len(canonical) != 1:
        raise CodeDenseUnavailableError("Conflicting Code version locators were supplied")
    kind, value = canonical.pop()
    if kind == "tag":
        raise CodeDenseUnavailableError(
            "Tag resolution is unavailable because Code V2 has no tag publication map"
        )
    return kind, value


def _reference_kind(value: str) -> str:
    if value.startswith("generation://"):
        return "generation"
    if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
        return "full_sha"
    if re.fullmatch(r"[0-9a-fA-F]{7,39}", value):
        return "short_sha"
    return "branch"


def _branch_heads(
    db: Any,
    branch: str,
    project_id: str | None,
    repository_ids: Sequence[str] | None,
) -> list[tuple[str, str]]:
    clauses = ["1=1"]
    values: list[Any] = []
    if project_id is not None:
        clauses.append("project_id=?")
        values.append(project_id)
    if repository_ids is not None:
        marks = ",".join("?" for _ in repository_ids)
        clauses.append(f"id IN ({marks})")
        values.extend(repository_ids)
    rows = db.execute(
        f"""
        SELECT id, default_branch, head_commit, active_generation_id
        FROM repositories WHERE {" AND ".join(clauses)}
        ORDER BY id
        """,
        values,
    ).fetchall()
    heads = {
        str(row["id"]): str(row["head_commit"])
        for row in rows
        if str(row["default_branch"] or "") == branch and row["active_generation_id"]
    }
    has_branches = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='git_branches'"
    ).fetchone()
    if has_branches:
        branch_clauses = ["b.name=?"]
        branch_values: list[Any] = [branch]
        if repository_ids is not None:
            marks = ",".join("?" for _ in repository_ids)
            branch_clauses.append(f"b.repository_id IN ({marks})")
            branch_values.extend(repository_ids)
        branches = db.execute(
            f"""
            SELECT b.repository_id, b.head_sha
            FROM git_branches b
            JOIN repositories r ON r.id=b.repository_id
            WHERE {" AND ".join(branch_clauses)}
              AND (? IS NULL OR r.project_id=?)
            ORDER BY b.repository_id
            """,
            [*branch_values, project_id, project_id],
        ).fetchall()
        heads.update((str(row["repository_id"]), str(row["head_sha"])) for row in branches)
    return sorted(heads.items())


def _allowed_acl_refs(request: EvidenceSearchRequest) -> Sequence[str] | None:
    if request.scope.enforce_acl or request.scope.allowed_acl_refs:
        return tuple(dict.fromkeys(request.scope.allowed_acl_refs))
    return None


def _validate_vector_scope(
    row: Mapping[str, Any],
    scope: _DenseScope,
    profile: EmbeddingProfile,
) -> None:
    actual = (
        str(row.get("project_id") or ""),
        str(row.get("repository_id") or ""),
        str(row.get("generation_id") or ""),
    )
    if actual != (scope.project_id, scope.repository_id, scope.generation_id):
        raise CodeDenseUnavailableError("a vector escaped the resolved dense scope")
    if str(row.get("profile") or "") != profile.id:
        raise CodeDenseUnavailableError("a vector escaped the resolved profile")
    if str(row.get("model") or "") != profile.model:
        raise CodeDenseUnavailableError("a vector escaped the resolved model")
    if int(row.get("dimension") or 0) != profile.dimension:
        raise CodeDenseUnavailableError("a vector has the wrong profile dimension")
    if not str(row.get("acl_ref") or ""):
        raise CodeDenseUnavailableError("a vector is missing ACL provenance")
    _validate_vector(bytes(row["vector"]), profile.dimension, source="stored vector")


def _validate_unit_scope(row: Mapping[str, Any], scope: _DenseScope) -> None:
    actual = (
        str(row.get("project_id") or ""),
        str(row.get("repository_id") or ""),
        str(row.get("generation_id") or ""),
    )
    if actual != (scope.project_id, scope.repository_id, scope.generation_id):
        raise CodeDenseUnavailableError("a retrieval unit escaped the resolved dense scope")
    if not str(row.get("acl_ref") or ""):
        raise CodeDenseUnavailableError("a retrieval unit is missing ACL provenance")


def _stable_version(row: Mapping[str, Any], scope: _DenseScope) -> str:
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping):
        ref = str(metadata.get("ref") or "")
        if ref:
            return ref
        lineage = metadata.get("source_lineage")
        if isinstance(lineage, Mapping):
            ref = str(lineage.get("ref") or "")
            if ref:
                return ref
    return scope.commit_sha


def _evidence_locator(row: Mapping[str, Any], scope: _DenseScope) -> str:
    metadata = row.get("metadata")
    source_uri = ""
    if isinstance(metadata, Mapping):
        lineage = metadata.get("source_lineage")
        if isinstance(lineage, Mapping):
            source_uri = str(lineage.get("source_uri") or "")
    if not source_uri:
        source_uri = f"code://{scope.repository_id}@{_stable_version(row, scope)}/{row['path']}"
    source_uri = source_uri.split("#", 1)[0]
    start = row.get("start_line")
    end = row.get("end_line")
    if start is not None:
        return f"{source_uri}#L{int(start)}-L{int(end if end is not None else start)}"
    return f"{source_uri}#unit={row['id']}"


def _diversify_dense(
    hits: Sequence[_DenseRankedHit],
    *,
    top_k: int,
) -> tuple[
    tuple[_DenseRankedHit, ...],
    Mapping[tuple[str, str], tuple[int | None, bool]],
]:
    groups: dict[tuple[str, str, str], list[_DenseRankedHit]] = {}
    for hit in hits:
        key = (
            hit.scope.repository_id,
            hit.scope.generation_id,
            str(hit.row["entity_id"]),
        )
        groups.setdefault(key, []).append(hit)
    representatives = {
        min(
            members,
            key=lambda hit: (
                _unit_tier(hit.row),
                hit.raw_rank,
                str(hit.row["id"]),
            ),
        ).key
        for members in groups.values()
    }
    ordered = [hit for hit in hits if hit.key in representatives]
    ordered.extend(hit for hit in hits if hit.key not in representatives)
    selected = tuple(ordered[:top_k])
    selected_ranks = {hit.key: rank for rank, hit in enumerate(selected)}
    decisions = {hit.key: (selected_ranks.get(hit.key), hit.key in representatives) for hit in hits}
    return selected, decisions


def _unit_tier(row: Mapping[str, Any]) -> int:
    unit_type = str(row.get("unit_type") or "")
    ast_node_type = str(row.get("ast_node_type") or "").casefold()
    if unit_type == "file.surface":
        return 0
    if unit_type == "symbol.ast_block" and (
        "definition" in ast_node_type
        or "declaration" in ast_node_type
        or ast_node_type in {"function_item", "method_definition", "class_specifier"}
    ):
        return 0
    if unit_type == "symbol.ast_block":
        return 1
    return 2


def _dense_hit_trace(
    hit: _DenseRankedHit,
    decision: tuple[int | None, bool] | None,
) -> CodeDenseHitTrace:
    diversified_rank, representative = decision or (None, False)
    return CodeDenseHitTrace(
        retrieval_unit_id=str(hit.row["id"]),
        entity_id=str(hit.row["entity_id"]),
        project_id=hit.scope.project_id,
        repository_id=hit.scope.repository_id,
        generation_id=hit.scope.generation_id,
        acl_ref=str(hit.row["acl_ref"]),
        profile=str(hit.scope.profile_state["profile"]["id"]),
        model=str(hit.scope.profile_state["provider"]["model"]),
        dimension=int(hit.scope.profile_state["provider"]["dimension"]),
        raw_rank=hit.raw_rank,
        raw_score=hit.score,
        content_hash=str(hit.row["content_hash"]),
        locator=hit.locator,
        explanation="scoped cosine similarity over the published profile vector",
        original_rank=hit.raw_rank,
        diversified_rank=diversified_rank,
        representative=representative,
    )


def _dense_outcomes(
    *,
    candidates: Sequence[CodeRetrievalCandidate],
    hit_count: int,
) -> tuple[tuple[CodeChannelOutcome, ...], tuple[CodeChannelFailure, ...], CodeSourceStatus]:
    outcomes: list[CodeChannelOutcome] = []
    for channel in CodeRetrievalChannel:
        if channel is not CodeRetrievalChannel.DENSE:
            outcomes.append(
                CodeChannelDisabled(
                    channel=channel,
                    reason=f"{DENSE_RETRIEVER_VERSION} does not implement this channel",
                )
            )
        elif hit_count == 0:
            outcomes.append(CodeChannelCompleteNoMatch(channel=channel))
        elif candidates:
            outcomes.append(CodeChannelCompleteWithHits(channel=channel, hit_count=hit_count))
        else:
            outcomes.append(CodeChannelCompletePruned(channel=channel, hit_count=hit_count))
    return tuple(outcomes), (), CodeSourceStatus.COMPLETE


def _diversify_hybrid(
    ranked: Sequence[tuple[bool, float, CodeRetrievalCandidate, dict[str, Any]]],
    *,
    top_k: int,
) -> tuple[tuple[bool, float, CodeRetrievalCandidate, dict[str, Any]], ...]:
    representatives: set[tuple[str, str]] = set()
    seen_entities: set[tuple[str, str, str]] = set()
    for entry in ranked:
        candidate = entry[2]
        entity_key = (
            candidate.repository_id,
            candidate.source_generation,
            candidate.entity_id,
        )
        if entity_key not in seen_entities:
            seen_entities.add(entity_key)
            representatives.add((candidate.source_generation, candidate.retrieval_unit_id))
    ordered = [
        entry
        for entry in ranked
        if (entry[2].source_generation, entry[2].retrieval_unit_id) in representatives
    ]
    ordered.extend(
        entry
        for entry in ranked
        if (entry[2].source_generation, entry[2].retrieval_unit_id) not in representatives
    )
    return tuple(ordered[:top_k])


def _hybrid_outcomes(
    profile: CodeQueryProfile,
    candidates: Sequence[CodeRetrievalCandidate],
    exact: CodeExactSparseSearchResult | None,
    dense: CodeDenseSearchResult | None,
) -> tuple[tuple[CodeChannelOutcome, ...], tuple[CodeChannelFailure, ...], CodeSourceStatus]:
    source_outcomes: dict[CodeRetrievalChannel, CodeChannelOutcome] = {}
    if exact is not None:
        source_outcomes.update(
            (outcome.channel, outcome)
            for outcome in exact.result.channel_outcomes
            if outcome.channel in {CodeRetrievalChannel.EXACT, CodeRetrievalChannel.SPARSE}
        )
    if dense is not None:
        source_outcomes.update(
            (outcome.channel, outcome)
            for outcome in dense.result.channel_outcomes
            if outcome.channel is CodeRetrievalChannel.DENSE
        )
    budgets = {
        CodeRetrievalChannel.EXACT: profile.budget.exact_candidates,
        CodeRetrievalChannel.SPARSE: profile.budget.sparse_candidates,
        CodeRetrievalChannel.DENSE: profile.budget.dense_candidates,
    }
    retained_channels = {
        score.channel for candidate in candidates for score in candidate.raw_channel_scores
    }
    outcomes: list[CodeChannelOutcome] = []
    errors: list[CodeChannelFailure] = []
    for channel in CodeRetrievalChannel:
        if channel not in budgets:
            outcomes.append(
                CodeChannelDisabled(
                    channel=channel,
                    reason=f"{HYBRID_RETRIEVER_VERSION} defers this channel",
                )
            )
            continue
        if budgets[channel] == 0:
            outcomes.append(
                CodeChannelDisabled(
                    channel=channel,
                    reason="channel disabled by the resolved query profile budget",
                )
            )
            continue
        source = source_outcomes.get(channel)
        if isinstance(source, CodeChannelUnavailable):
            outcomes.append(source)
            errors.append(
                CodeChannelFailure(
                    channel=channel,
                    kind="unavailable",
                    message=source.error,
                )
            )
        elif isinstance(source, CodeChannelError):
            outcomes.append(source)
            errors.append(CodeChannelFailure(channel=channel, kind="error", message=source.error))
        elif isinstance(source, CodeChannelCompleteWithHits):
            if channel in retained_channels:
                outcomes.append(source)
            else:
                outcomes.append(
                    CodeChannelCompletePruned(
                        channel=channel,
                        hit_count=source.hit_count,
                    )
                )
        elif isinstance(source, CodeChannelCompletePruned):
            outcomes.append(source)
        else:
            outcomes.append(CodeChannelCompleteNoMatch(channel=channel))
    completed = any(
        isinstance(
            outcome,
            (
                CodeChannelCompleteNoMatch,
                CodeChannelCompletePruned,
                CodeChannelCompleteWithHits,
            ),
        )
        for outcome in outcomes
    )
    if completed and errors:
        status = CodeSourceStatus.PARTIAL
    elif errors:
        status = CodeSourceStatus.UNAVAILABLE
    else:
        status = CodeSourceStatus.COMPLETE
    return tuple(outcomes), tuple(errors), status


def _hybrid_scopes(
    exact: CodeExactSparseSearchResult | None,
    dense: CodeDenseSearchResult | None,
) -> tuple[str, ...]:
    values = {
        *(exact.trace.generation_scopes if exact is not None else ()),
        *(dense.trace.generation_scopes if dense is not None else ()),
    }
    return tuple(sorted(values))


def _hybrid_index_version(
    exact: CodeExactSparseSearchResult | None,
    dense: CodeDenseSearchResult | None,
) -> str:
    versions = (
        exact.result.index_version if exact is not None else "exact-sparse-disabled",
        dense.result.index_version if dense is not None else "dense-disabled",
    )
    digest = hashlib.sha256("\0".join(versions).encode()).hexdigest()[:16]
    return f"{HYBRID_RETRIEVER_VERSION}:index-set-{digest}"


def _hybrid_watermark(
    exact: CodeExactSparseSearchResult | None,
    dense: CodeDenseSearchResult | None,
) -> str:
    watermarks = {
        result.result.watermark
        for result in (exact, dense)
        if result is not None and result.result.watermark != "code-v2:unresolved"
    }
    if not watermarks:
        return "code-v2:unresolved"
    if len(watermarks) == 1:
        return watermarks.pop()
    digest = hashlib.sha256("\0".join(sorted(watermarks)).encode()).hexdigest()
    return f"code-v2-hybrid-watermark://sha256/{digest}"


def _query_id(
    kind: str,
    request: EvidenceSearchRequest,
    query_profile: CodeQueryProfile,
    embedding_profile: EmbeddingProfile,
) -> str:
    payload = {
        "embedding_profile": embedding_profile.canonical_snapshot(),
        "limit": request.limit,
        "query": unicodedata.normalize("NFC", request.query),
        "query_profile": query_profile.model_dump(mode="json"),
        "scope": {
            **request.scope.model_dump(mode="json"),
            "allowed_acl_refs": sorted(set(request.scope.allowed_acl_refs)),
            "enforce_acl": request.scope.enforce_acl,
        },
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return f"code-query://{kind}/sha256/{digest}"


def _has_explicit_version(
    request: EvidenceSearchRequest,
    profile: CodeQueryProfile,
) -> bool:
    return bool(
        request.scope.commit
        or request.scope.branch
        or (profile.target_ref and profile.target_ref != "current")
    )


def _dense_index_version(
    scopes: Sequence[_DenseScope],
    profile: EmbeddingProfile,
) -> str:
    versions = sorted(
        {
            (
                str(scope.profile_state["provider"]["model"]),
                str(scope.profile_state["provider"]["revision"]),
                str(scope.profile_state["vector_hash"]),
            )
            for scope in scopes
        }
    )
    payload = json.dumps(
        {
            "profile": profile.canonical_snapshot(),
            "versions": versions,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{DENSE_RETRIEVER_VERSION}:profile-set-{digest}"


def _watermark(scopes: Sequence[_DenseScope]) -> str:
    if not scopes:
        return "code-v2:unresolved"
    payload = "\0".join(
        f"{scope.repository_id}@{scope.commit_sha}#{scope.generation_id}"
        for scope in sorted(scopes, key=lambda item: (item.repository_id, item.generation_id))
    )
    return f"code-v2-watermark://sha256/{hashlib.sha256(payload.encode()).hexdigest()}"


def _scope_labels(scopes: Sequence[_DenseScope]) -> tuple[str, ...]:
    return tuple(
        f"{scope.repository_id}@{scope.commit_sha}#{scope.generation_id}"
        for scope in sorted(scopes, key=lambda item: (item.repository_id, item.generation_id))
    )


def _safe_message(prefix: str, error: Exception) -> str:
    detail = " ".join(str(error).split())
    return f"{prefix}: {detail}" if detail else f"{prefix}: {type(error).__name__}"


def _optional_int(value: Any) -> int:
    return int(value) if value is not None else 2**31 - 1


def _elapsed_ms(started: float) -> float:
    return float(max(0.0, round((time.perf_counter() - started) * 1000.0, 6)))


__all__ = [
    "CodeDenseError",
    "CodeDenseHitTrace",
    "CodeDenseProfilePublisher",
    "CodeDensePublicationError",
    "CodeDensePublicationResult",
    "CodeDenseRetriever",
    "CodeDenseSearchResult",
    "CodeDenseStore",
    "CodeDenseTrace",
    "CodeDenseUnavailableError",
    "CodeHybridHitTrace",
    "CodeHybridSearchResult",
    "CodeHybridTrace",
    "CodeHybridV2Retriever",
    "DEFAULT_CODE_NL_PROFILE",
    "DENSE_MODEL_CANDIDATES",
    "DenseModelCandidate",
    "resolve_code_embedding_profile",
]
