"""Offline-first embedding profiles, providers, and indexing for Code Source V2.

This module consumes caller-supplied C2 unit records only.  It deliberately does
not discover units, choose learned models, benchmark models, rerank results, or
perform network I/O.

All provider work and validation completes before persistence starts.  The
current ``CodeV2StoreMixin`` exposes cache and vector upserts as two independent
transactions, so a storage failure during the publish phase cannot be made
atomic across both tables here.  Both writes are idempotent; provider failures,
partial batches, and validation failures are guaranteed to fail before either
write begins.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ....embeddings import LocalHashEmbedding

EMBEDDING_PROFILE_PURPOSES = frozenset({"code_nl", "code_code", "history", "error"})
EMBEDDING_LOCALITIES = frozenset({"local", "remote"})
LOCAL_HASH_MODEL = LocalHashEmbedding.model_id
LOCAL_HASH_MODEL_REVISION = "2"

_QUARANTINED_QUALITY = frozenset({"blocked", "quarantine", "quarantined", "secret"})
_SECRET_FLAG_KEYS = frozenset(
    {
        "contains_secret",
        "has_secret",
        "quarantined",
        "secret_detected",
        "sensitive",
    }
)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(
        r"""(?ix)
        \b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|
        password|passwd|private[_-]?key|secret)\b
        \s*(?:=|:)\s*
        ["'][^"'\r\n]{4,}["']
        """
    ),
)


class CodeEmbeddingError(RuntimeError):
    """Base error for the C3-01 embedding boundary."""


class EmbeddingProviderUnavailableError(CodeEmbeddingError):
    """The configured provider cannot serve the requested batch."""


class EmbeddingBatchError(CodeEmbeddingError):
    """A provider batch failed or returned an incomplete result."""


class EmbeddingDimensionError(CodeEmbeddingError):
    """A provider or cache returned an incompatible vector."""


def _text(
    value: str,
    field: str,
    *,
    allow_empty: bool = False,
) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if "\x00" in normalized:
        raise ValueError(f"{field} must not contain NUL")
    if not allow_empty and not normalized.strip():
        raise ValueError(f"{field} must not be empty")
    return normalized


def _positive_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """Versioned, immutable configuration for one embedding use case."""

    id: str
    purpose: str
    model: str
    revision: str
    dimension: int
    instruction: str
    instruction_revision: str
    max_tokens: int
    batch_size: int
    locality: str
    redaction_policy: str
    normalization_revision: str

    def __post_init__(self) -> None:
        for field in (
            "id",
            "model",
            "revision",
            "instruction_revision",
            "redaction_policy",
            "normalization_revision",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(
            self,
            "instruction",
            _text(self.instruction, "instruction", allow_empty=True),
        )
        purpose = _text(self.purpose, "purpose")
        if purpose not in EMBEDDING_PROFILE_PURPOSES:
            choices = ", ".join(sorted(EMBEDDING_PROFILE_PURPOSES))
            raise ValueError(f"purpose must be one of: {choices}")
        object.__setattr__(self, "purpose", purpose)
        locality = _text(self.locality, "locality")
        if locality not in EMBEDDING_LOCALITIES:
            choices = ", ".join(sorted(EMBEDDING_LOCALITIES))
            raise ValueError(f"locality must be one of: {choices}")
        object.__setattr__(self, "locality", locality)
        object.__setattr__(
            self,
            "dimension",
            _positive_int(self.dimension, "dimension"),
        )
        object.__setattr__(
            self,
            "max_tokens",
            _positive_int(self.max_tokens, "max_tokens"),
        )
        object.__setattr__(
            self,
            "batch_size",
            _positive_int(self.batch_size, "batch_size"),
        )

    def canonical_snapshot(self) -> dict[str, Any]:
        """Return the stable, JSON-compatible profile contract."""

        return {
            "batch_size": self.batch_size,
            "dimension": self.dimension,
            "id": self.id,
            "instruction": self.instruction,
            "instruction_revision": self.instruction_revision,
            "locality": self.locality,
            "max_tokens": self.max_tokens,
            "model": self.model,
            "normalization_revision": self.normalization_revision,
            "purpose": self.purpose,
            "redaction_policy": self.redaction_policy,
            "revision": self.revision,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_snapshot(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class EmbeddingProvenance:
    """Provider-declared identity attached to every batch result."""

    provider: str
    model: str
    revision: str
    dimension: int
    locality: str

    def __post_init__(self) -> None:
        for field in ("provider", "model", "revision"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(
            self,
            "dimension",
            _positive_int(self.dimension, "dimension"),
        )
        locality = _text(self.locality, "locality")
        if locality not in EMBEDDING_LOCALITIES:
            choices = ", ".join(sorted(EMBEDDING_LOCALITIES))
            raise ValueError(f"locality must be one of: {choices}")
        object.__setattr__(self, "locality", locality)

    @property
    def model_revision(self) -> str:
        return self.revision

    def canonical_snapshot(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "locality": self.locality,
            "model": self.model,
            "provider": self.provider,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class EmbeddingBatchResult:
    """One complete ordered provider batch and its provenance."""

    vectors: tuple[bytes, ...]
    provenance: EmbeddingProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "vectors", tuple(self.vectors))
        if not isinstance(self.provenance, EmbeddingProvenance):
            raise TypeError("provenance must be EmbeddingProvenance")
        if not all(isinstance(vector, bytes) for vector in self.vectors):
            raise TypeError("vectors must contain bytes")


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Batch-only provider boundary; providers must not publish partial work."""

    @property
    def provenance(self) -> EmbeddingProvenance: ...

    @property
    def available(self) -> bool: ...

    def embed_batch(
        self,
        texts: Sequence[str],
        *,
        profile: EmbeddingProfile,
    ) -> EmbeddingBatchResult: ...


class LocalHashCodeEmbeddingProvider:
    """Offline adapter for the existing deterministic ``local-hash-v2`` model."""

    def __init__(
        self,
        dimension: int = 384,
        *,
        dimensions: int | None = None,
        model_revision: str = LOCAL_HASH_MODEL_REVISION,
    ) -> None:
        if dimensions is not None:
            if dimension != 384:
                raise TypeError("pass only one of dimension or dimensions")
            dimension = dimensions
        self._embedder = LocalHashEmbedding(dimension)
        self._provenance = EmbeddingProvenance(
            provider="local-hash",
            model=LOCAL_HASH_MODEL,
            revision=_text(model_revision, "model_revision"),
            dimension=dimension,
            locality="local",
        )

    @property
    def provenance(self) -> EmbeddingProvenance:
        return self._provenance

    @property
    def available(self) -> bool:
        return True

    @property
    def dimension(self) -> int:
        return self._provenance.dimension

    @property
    def model(self) -> str:
        return self._provenance.model

    @property
    def model_revision(self) -> str:
        return self._provenance.revision

    def embed_batch(
        self,
        texts: Sequence[str],
        *,
        profile: EmbeddingProfile,
    ) -> EmbeddingBatchResult:
        if profile.dimension != self.dimension:
            raise EmbeddingDimensionError("profile dimension does not match local-hash provider")
        items = tuple(texts)
        if len(items) > profile.batch_size:
            raise EmbeddingBatchError("batch exceeds profile batch_size")
        if not all(type(item) is str for item in items):
            raise TypeError("texts must contain strings")
        vectors = tuple(
            self._embedder.embed(
                (f"{profile.instruction}\n{text}" if profile.instruction else text),
                model_id=LOCAL_HASH_MODEL,
            )
            for text in items
        )
        return EmbeddingBatchResult(vectors=vectors, provenance=self.provenance)


def embedding_cache_key(
    content_hash: str,
    profile: EmbeddingProfile,
    *,
    model: str | None = None,
    model_revision: str | None = None,
) -> str:
    """Build the content-addressed, per-profile cache identity."""

    if not isinstance(profile, EmbeddingProfile):
        raise TypeError("profile must be EmbeddingProfile")
    resolved_model = _text(
        profile.model if model is None else model,
        "model",
    )
    resolved_model_revision = _text(
        profile.revision if model_revision is None else model_revision,
        "model_revision",
    )
    instruction_digest = hashlib.sha256(profile.instruction.encode("utf-8")).hexdigest()
    payload = json.dumps(
        {
            "content_hash": _text(content_hash, "content_hash"),
            "instruction_digest": f"sha256:{instruction_digest}",
            "instruction_revision": profile.instruction_revision,
            "model": resolved_model,
            "model_revision": resolved_model_revision,
            "normalization_revision": profile.normalization_revision,
            "profile_id": profile.id,
            "profile_revision": profile.revision,
            "schema": "code-embedding-cache-v1",
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@runtime_checkable
class CodeEmbeddingStore(Protocol):
    """Narrow CodeV2Store surface needed by the embedding indexer."""

    def get_code_embedding_cache(
        self,
        cache_key: str,
        *,
        profile: str,
        model: str,
        touch: bool = False,
    ) -> dict[str, Any] | None: ...

    def upsert_code_embedding_cache(
        self,
        records: Iterable[Mapping[str, Any]],
    ) -> int: ...

    def upsert_code_unit_vectors(
        self,
        records: Iterable[Mapping[str, Any]],
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class CodeEmbeddingTrace:
    """One secret-free unit indexing decision with source scope preserved."""

    unit_id: str
    generation_id: str
    acl_ref: str
    content_hash: str
    cache_key: str | None
    status: str
    reason: str | None
    cache_hit: bool
    fallback: bool
    provenance: EmbeddingProvenance | None

    def canonical_snapshot(self) -> dict[str, Any]:
        return {
            "acl_ref": self.acl_ref,
            "cache_hit": self.cache_hit,
            "cache_key": self.cache_key,
            "content_hash": self.content_hash,
            "fallback": self.fallback,
            "generation_id": self.generation_id,
            "provenance": (
                self.provenance.canonical_snapshot() if self.provenance is not None else None
            ),
            "reason": self.reason,
            "status": self.status,
            "unit_id": self.unit_id,
        }


@dataclass(frozen=True, slots=True)
class CodeEmbeddingIndexResult:
    """Counters and audit trace for one fully validated indexing call."""

    profile: EmbeddingProfile
    cache_hits: int
    cache_misses: int
    indexed: int
    skipped: int
    fallbacks: int
    provenance_trace: tuple[CodeEmbeddingTrace, ...]

    @property
    def cache_hit(self) -> int:
        return self.cache_hits

    @property
    def cache_miss(self) -> int:
        return self.cache_misses

    @property
    def fallback(self) -> bool:
        return self.fallback_used

    @property
    def fallback_count(self) -> int:
        return self.fallbacks

    @property
    def traces(self) -> tuple[CodeEmbeddingTrace, ...]:
        return self.provenance_trace

    @property
    def fallback_used(self) -> bool:
        return self.fallbacks > 0

    def canonical_snapshot(self) -> dict[str, Any]:
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "fallbacks": self.fallbacks,
            "indexed": self.indexed,
            "profile": self.profile.canonical_snapshot(),
            "provenance_trace": [trace.canonical_snapshot() for trace in self.provenance_trace],
            "skipped": self.skipped,
        }


@dataclass(frozen=True, slots=True)
class _UnitInput:
    ordinal: int
    unit_id: str
    generation_id: str
    acl_ref: str
    content: str
    content_hash: str
    token_count: int
    quality_status: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _PreparedAttempt:
    cache_records: tuple[dict[str, Any], ...]
    vector_records: tuple[dict[str, Any], ...]
    traces: tuple[CodeEmbeddingTrace, ...]
    cache_hits: int
    cache_misses: int


class CodeEmbeddingIndexer:
    """Validate, embed, and idempotently publish caller-provided C2 units."""

    def __init__(
        self,
        store: CodeEmbeddingStore,
        provider: EmbeddingProvider | None = None,
        *,
        allow_local_fallback: bool = False,
        fallback_provider: EmbeddingProvider | None = None,
    ) -> None:
        if type(allow_local_fallback) is not bool:
            raise TypeError("allow_local_fallback must be a bool")
        if fallback_provider is not None and not allow_local_fallback:
            raise ValueError("fallback_provider requires allow_local_fallback=True")
        self.store = store
        self.provider = provider
        self.allow_local_fallback = allow_local_fallback
        self.fallback_provider = fallback_provider

    def index(
        self,
        units: Iterable[Mapping[str, Any] | Any],
        profile: EmbeddingProfile,
    ) -> CodeEmbeddingIndexResult:
        """Index one caller-owned batch without discovering any additional units."""

        if not isinstance(profile, EmbeddingProfile):
            raise TypeError("profile must be EmbeddingProfile")
        if _contains_secret(profile.instruction):
            raise ValueError("profile instruction contains secret-like material")

        prepared_units = tuple(
            self._unit_input(unit, ordinal=index) for index, unit in enumerate(units)
        )
        self._validate_unique_units(prepared_units)

        eligible: list[_UnitInput] = []
        skipped_by_ordinal: dict[int, CodeEmbeddingTrace] = {}
        for unit in prepared_units:
            reason = self._skip_reason(unit, profile)
            if reason is None:
                eligible.append(unit)
                continue
            skipped_by_ordinal[unit.ordinal] = CodeEmbeddingTrace(
                unit_id=unit.unit_id,
                generation_id=unit.generation_id,
                acl_ref=unit.acl_ref,
                content_hash=unit.content_hash,
                cache_key=None,
                status="skipped",
                reason=reason,
                cache_hit=False,
                fallback=False,
                provenance=None,
            )

        if not eligible:
            return self._result(
                profile,
                prepared_units,
                skipped_by_ordinal,
                attempt=None,
                fallback=False,
            )

        fallback = False
        try:
            primary = self._primary_provider(profile)
            attempt = self._prepare_attempt(
                eligible,
                profile,
                primary,
                fallback=False,
            )
        except EmbeddingProviderUnavailableError:
            if not self.allow_local_fallback:
                raise
            fallback = True
            attempt = self._prepare_attempt(
                eligible,
                profile,
                self._local_fallback(profile),
                fallback=True,
            )

        # The two Store methods each own a transaction.  Everything that can be
        # checked locally is complete before this idempotent publish phase.
        self.store.upsert_code_embedding_cache(attempt.cache_records)
        self.store.upsert_code_unit_vectors(attempt.vector_records)
        return self._result(
            profile,
            prepared_units,
            skipped_by_ordinal,
            attempt=attempt,
            fallback=fallback,
        )

    def index_units(
        self,
        units: Iterable[Mapping[str, Any] | Any],
        profile: EmbeddingProfile,
    ) -> CodeEmbeddingIndexResult:
        return self.index(units, profile)

    def _primary_provider(
        self,
        profile: EmbeddingProfile,
    ) -> EmbeddingProvider:
        if self.provider is not None:
            return self.provider
        if profile.locality != "local" or profile.model != LOCAL_HASH_MODEL:
            if self.allow_local_fallback:
                raise EmbeddingProviderUnavailableError(
                    "configured embedding provider is unavailable"
                )
            raise EmbeddingProviderUnavailableError("only local-hash-v2 is available by default")
        return LocalHashCodeEmbeddingProvider(profile.dimension)

    def _local_fallback(
        self,
        profile: EmbeddingProfile,
    ) -> EmbeddingProvider:
        provider = self.fallback_provider
        if provider is None:
            provider = LocalHashCodeEmbeddingProvider(profile.dimension)
        provenance = self._provider_provenance(provider)
        if provenance.locality != "local" or provenance.model != LOCAL_HASH_MODEL:
            raise EmbeddingProviderUnavailableError("fallback provider must be local-hash-v2")
        return provider

    def _prepare_attempt(
        self,
        units: Sequence[_UnitInput],
        profile: EmbeddingProfile,
        provider: EmbeddingProvider,
        *,
        fallback: bool,
    ) -> _PreparedAttempt:
        provenance = self._provider_provenance(provider)
        self._validate_provider(profile, provenance, fallback=fallback)

        keys_by_ordinal: dict[int, str] = {}
        vectors_by_key: dict[str, bytes] = {}
        persistent_hits: set[str] = set()
        texts_by_key: dict[str, str] = {}
        for unit in units:
            cache_key = embedding_cache_key(
                unit.content_hash,
                profile,
                model=provenance.model,
                model_revision=provenance.revision,
            )
            keys_by_ordinal[unit.ordinal] = cache_key
            previous_text = texts_by_key.get(cache_key)
            if previous_text is not None and previous_text != unit.content:
                raise CodeEmbeddingError("one cache identity maps to conflicting unit content")
            texts_by_key.setdefault(cache_key, unit.content)
            if cache_key in vectors_by_key or cache_key in persistent_hits:
                continue
            cached = self.store.get_code_embedding_cache(
                cache_key,
                profile=profile.id,
                model=provenance.model,
                touch=False,
            )
            if cached is None:
                continue
            vector = bytes(cached["vector"])
            self._validate_vector(
                vector,
                expected_dimension=profile.dimension,
                declared_dimension=cached.get("dimension"),
                source="embedding cache",
            )
            vectors_by_key[cache_key] = vector
            persistent_hits.add(cache_key)

        missing_keys = [key for key in texts_by_key if key not in vectors_by_key]
        if missing_keys and not self._provider_available(provider):
            raise EmbeddingProviderUnavailableError("configured embedding provider is unavailable")

        for offset in range(0, len(missing_keys), profile.batch_size):
            batch_keys = missing_keys[offset : offset + profile.batch_size]
            batch_texts = tuple(texts_by_key[key] for key in batch_keys)
            try:
                batch = provider.embed_batch(batch_texts, profile=profile)
            except EmbeddingProviderUnavailableError:
                raise
            except Exception as error:
                raise EmbeddingBatchError("embedding provider batch failed") from error
            if not isinstance(batch, EmbeddingBatchResult):
                raise EmbeddingBatchError("embedding provider returned an invalid batch result")
            self._validate_batch_provenance(
                provenance,
                batch.provenance,
            )
            if len(batch.vectors) != len(batch_keys):
                raise EmbeddingBatchError("embedding provider returned a partial batch")
            for cache_key, vector in zip(
                batch_keys,
                batch.vectors,
                strict=True,
            ):
                self._validate_vector(
                    vector,
                    expected_dimension=profile.dimension,
                    declared_dimension=batch.provenance.dimension,
                    source="embedding provider",
                )
                vectors_by_key[cache_key] = vector

        if len(vectors_by_key) != len(texts_by_key):
            raise EmbeddingBatchError("embedding provider did not resolve every cache miss")

        cache_records = tuple(
            {
                "cache_key": cache_key,
                "profile": profile.id,
                "model": provenance.model,
                "dimension": profile.dimension,
                "vector": vectors_by_key[cache_key],
            }
            for cache_key in texts_by_key
        )
        vector_records = tuple(
            {
                "unit_id": unit.unit_id,
                "generation_id": unit.generation_id,
                "profile": profile.id,
                "model": provenance.model,
                "dimension": profile.dimension,
                "vector": vectors_by_key[keys_by_ordinal[unit.ordinal]],
                "content_hash": unit.content_hash,
            }
            for unit in units
        )
        traces = tuple(
            CodeEmbeddingTrace(
                unit_id=unit.unit_id,
                generation_id=unit.generation_id,
                acl_ref=unit.acl_ref,
                content_hash=unit.content_hash,
                cache_key=keys_by_ordinal[unit.ordinal],
                status=(
                    "cache_hit" if keys_by_ordinal[unit.ordinal] in persistent_hits else "indexed"
                ),
                reason=("provider_unavailable_local_hash_fallback" if fallback else None),
                cache_hit=keys_by_ordinal[unit.ordinal] in persistent_hits,
                fallback=fallback,
                provenance=provenance,
            )
            for unit in units
        )
        return _PreparedAttempt(
            cache_records=cache_records,
            vector_records=vector_records,
            traces=traces,
            cache_hits=sum(trace.cache_hit for trace in traces),
            cache_misses=sum(not trace.cache_hit for trace in traces),
        )

    @staticmethod
    def _provider_provenance(
        provider: EmbeddingProvider,
    ) -> EmbeddingProvenance:
        try:
            provenance = provider.provenance
        except Exception as error:
            raise TypeError("embedding provider must expose provenance") from error
        if not isinstance(provenance, EmbeddingProvenance):
            raise TypeError("embedding provider provenance must be EmbeddingProvenance")
        return provenance

    @staticmethod
    def _provider_available(provider: EmbeddingProvider) -> bool:
        try:
            available = provider.available
        except Exception as error:
            raise EmbeddingProviderUnavailableError(
                "embedding provider availability check failed"
            ) from error
        if type(available) is not bool:
            raise TypeError("embedding provider available must be a bool")
        return available

    @staticmethod
    def _validate_provider(
        profile: EmbeddingProfile,
        provenance: EmbeddingProvenance,
        *,
        fallback: bool,
    ) -> None:
        if provenance.dimension != profile.dimension:
            raise EmbeddingDimensionError("provider dimension does not match embedding profile")
        if fallback:
            if provenance.locality != "local":
                raise CodeEmbeddingError("local fallback provider must declare local provenance")
            return
        if provenance.model != profile.model:
            raise CodeEmbeddingError("provider model does not match embedding profile")
        if provenance.locality != profile.locality:
            raise CodeEmbeddingError("provider locality does not match embedding profile")

    @staticmethod
    def _validate_batch_provenance(
        declared: EmbeddingProvenance,
        returned: EmbeddingProvenance,
    ) -> None:
        if returned != declared:
            raise CodeEmbeddingError("embedding batch provenance changed during indexing")

    @staticmethod
    def _validate_vector(
        vector: bytes,
        *,
        expected_dimension: int,
        declared_dimension: Any,
        source: str,
    ) -> None:
        if isinstance(declared_dimension, bool) or not isinstance(declared_dimension, int):
            raise EmbeddingDimensionError(f"{source} dimension is not an integer")
        if declared_dimension != expected_dimension:
            raise EmbeddingDimensionError(f"{source} dimension does not match embedding profile")
        if not isinstance(vector, bytes):
            raise TypeError(f"{source} vector must be bytes")
        if len(vector) != expected_dimension * 4:
            raise EmbeddingDimensionError(
                f"{source} vector byte length does not match its dimension"
            )
        values = struct.unpack(f"<{expected_dimension}f", vector)
        if not all(math.isfinite(value) for value in values):
            raise EmbeddingDimensionError(f"{source} vector contains a non-finite value")

    @staticmethod
    def _unit_input(unit: Mapping[str, Any] | Any, *, ordinal: int) -> _UnitInput:
        if isinstance(unit, Mapping):
            record = unit
        else:
            adapter = getattr(unit, "as_store_record", None)
            if not callable(adapter):
                raise TypeError("units must be mappings or expose as_store_record()")
            record = adapter()
            if not isinstance(record, Mapping):
                raise TypeError("as_store_record() must return a mapping")

        content = _text(record["content"], "content", allow_empty=True)
        raw_token_count = record.get("token_count")
        if raw_token_count is None:
            token_count = (len(content.encode("utf-8")) + 3) // 4
        elif (
            isinstance(raw_token_count, bool)
            or not isinstance(raw_token_count, int)
            or raw_token_count < 0
        ):
            raise ValueError("token_count must be a non-negative integer")
        else:
            token_count = raw_token_count
        raw_metadata = record.get("metadata", {})
        if isinstance(raw_metadata, str):
            try:
                raw_metadata = json.loads(raw_metadata)
            except json.JSONDecodeError as error:
                raise ValueError("metadata must be valid JSON") from error
        if raw_metadata is None:
            raw_metadata = {}
        if not isinstance(raw_metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        return _UnitInput(
            ordinal=ordinal,
            unit_id=_text(record["id"], "unit id"),
            generation_id=_text(record["generation_id"], "generation_id"),
            acl_ref=_text(record["acl_ref"], "acl_ref"),
            content=content,
            content_hash=_text(record["content_hash"], "content_hash"),
            token_count=token_count,
            quality_status=_text(
                record.get("quality_status", "ready"),
                "quality_status",
            ),
            metadata=raw_metadata,
        )

    @staticmethod
    def _validate_unique_units(units: Sequence[_UnitInput]) -> None:
        identities: set[tuple[str, str]] = set()
        for unit in units:
            identity = (unit.unit_id, unit.generation_id)
            if identity in identities:
                raise ValueError("unit ids must be unique within one generation batch")
            identities.add(identity)

    @staticmethod
    def _skip_reason(
        unit: _UnitInput,
        profile: EmbeddingProfile,
    ) -> str | None:
        if unit.quality_status.casefold() in _QUARANTINED_QUALITY:
            return "quarantined"
        for key, value in unit.metadata.items():
            normalized_key = str(key).casefold()
            if normalized_key in _SECRET_FLAG_KEYS and value is True:
                return "quarantined"
            if normalized_key in {"classification", "sensitivity"} and str(value).casefold() in {
                "secret",
                "restricted",
            }:
                return "quarantined"
        if _contains_secret(unit.content):
            return "secret_detected"
        if unit.token_count > profile.max_tokens:
            return "max_tokens_exceeded"
        return None

    @staticmethod
    def _result(
        profile: EmbeddingProfile,
        units: Sequence[_UnitInput],
        skipped_by_ordinal: Mapping[int, CodeEmbeddingTrace],
        *,
        attempt: _PreparedAttempt | None,
        fallback: bool,
    ) -> CodeEmbeddingIndexResult:
        indexed_by_ordinal = (
            {
                unit.ordinal: trace
                for unit, trace in zip(
                    (unit for unit in units if unit.ordinal not in skipped_by_ordinal),
                    attempt.traces,
                    strict=True,
                )
            }
            if attempt is not None
            else {}
        )
        traces = tuple(
            skipped_by_ordinal.get(unit.ordinal) or indexed_by_ordinal[unit.ordinal]
            for unit in units
        )
        indexed = len(attempt.vector_records) if attempt is not None else 0
        return CodeEmbeddingIndexResult(
            profile=profile,
            cache_hits=attempt.cache_hits if attempt is not None else 0,
            cache_misses=attempt.cache_misses if attempt is not None else 0,
            indexed=indexed,
            skipped=len(skipped_by_ordinal),
            fallbacks=indexed if fallback else 0,
            provenance_trace=traces,
        )


def _contains_secret(text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in _SECRET_PATTERNS)


__all__ = [
    "CodeEmbeddingError",
    "CodeEmbeddingIndexResult",
    "CodeEmbeddingIndexer",
    "CodeEmbeddingStore",
    "CodeEmbeddingTrace",
    "EMBEDDING_LOCALITIES",
    "EMBEDDING_PROFILE_PURPOSES",
    "EmbeddingBatchError",
    "EmbeddingBatchResult",
    "EmbeddingDimensionError",
    "EmbeddingProfile",
    "EmbeddingProvider",
    "EmbeddingProviderUnavailableError",
    "EmbeddingProvenance",
    "LOCAL_HASH_MODEL",
    "LOCAL_HASH_MODEL_REVISION",
    "LocalHashCodeEmbeddingProvider",
    "embedding_cache_key",
]
