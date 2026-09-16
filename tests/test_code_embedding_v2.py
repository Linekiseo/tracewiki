from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.embeddings import LocalHashEmbedding
from evidence_rag.rag.sources.code.embedding_v2 import (
    CodeEmbeddingIndexer,
    EmbeddingBatchError,
    EmbeddingBatchResult,
    EmbeddingDimensionError,
    EmbeddingProfile,
    EmbeddingProvenance,
    EmbeddingProviderUnavailableError,
    LocalHashCodeEmbeddingProvider,
    embedding_cache_key,
)
from evidence_rag.storage import SQLiteStore


def _profile(**changes: Any) -> EmbeddingProfile:
    values = {
        "id": "code-code-v1",
        "purpose": "code_code",
        "model": "local-hash-v2",
        "revision": "profile-r1",
        "dimension": 32,
        "instruction": "",
        "instruction_revision": "instruction-r1",
        "max_tokens": 2048,
        "batch_size": 2,
        "locality": "local",
        "redaction_policy": "deny-secrets-v1",
        "normalization_revision": "normalization-r1",
    }
    values.update(changes)
    return EmbeddingProfile(**values)


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


def _unit(
    unit_id: str,
    content: str,
    *,
    generation_id: str = "generation://embedding",
    acl_ref: str = "acl://engineering",
    quality_status: str = "complete",
    content_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": unit_id,
        "entity_id": "entity://embedding",
        "parent_unit_id": None,
        "repository_id": "repository://embedding",
        "generation_id": generation_id,
        "project_id": "project://embedding",
        "unit_type": "symbol.ast_block",
        "ast_node_type": "function_definition",
        "ordinal": 0,
        "language": "python",
        "path": "src/example.py",
        "qualified_name": unit_id.rsplit("/", 1)[-1],
        "signature": "",
        "identifiers": [],
        "doc": "",
        "body": content,
        "content": content,
        "context_ref": {},
        "start_line": 1,
        "end_line": 1,
        "start_byte": 0,
        "end_byte": len(content.encode()),
        "token_count": 8,
        "content_hash": content_hash or _content_hash(content),
        "builder_version": "c2-test",
        "quality_status": quality_status,
        "acl_ref": acl_ref,
        "metadata": metadata or {},
    }


def _store(tmp_path: Path, units: list[dict[str, Any]]) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "embedding.sqlite3")
    store.initialize()
    generations = sorted({str(unit["generation_id"]) for unit in units})
    with store.transaction() as db:
        db.execute(
            """
            INSERT INTO repositories(
                id, project_id, name, source_type, local_path, head_commit, acl_ref,
                status, active_generation_id, created_at, updated_at
            ) VALUES (
                'repository://embedding', 'project://embedding', 'embedding',
                'local', '/tmp/embedding', 'abc123', 'acl://engineering',
                'ready', ?, 'now', 'now'
            )
            """,
            (generations[0],),
        )
        db.executemany(
            """
            INSERT INTO index_generations(
                id, repository_id, commit_sha, status, started_at, completed_at
            ) VALUES (?, 'repository://embedding', 'abc123', 'published', 'now', 'now')
            """,
            [(generation,) for generation in generations],
        )
        db.executemany(
            """
            INSERT INTO entities(
                id, repository_id, generation_id, project_id, entity_type, name,
                qualified_name, path, language, commit_sha, content_hash, source_uri,
                acl_ref, content
            ) VALUES (
                'entity://embedding', 'repository://embedding', ?,
                'project://embedding', 'CodeSymbol', 'embedding', 'embedding',
                'src/example.py', 'python', 'abc123', 'entity-hash',
                'code://embedding', 'acl://engineering', 'embedding'
            )
            """,
            [(generation,) for generation in generations],
        )
    store.upsert_code_units(units)
    return store


class RecordingProvider:
    def __init__(
        self,
        *,
        model: str = "local-hash-v2",
        revision: str = "model-r1",
        dimension: int = 32,
        locality: str = "local",
        available: bool = True,
        fail_on_call: int | None = None,
        mixed_dimension: bool = False,
    ) -> None:
        self._provenance = EmbeddingProvenance(
            provider="recording",
            model=model,
            revision=revision,
            dimension=dimension,
            locality=locality,
        )
        self._available = available
        self.fail_on_call = fail_on_call
        self.mixed_dimension = mixed_dimension
        self.calls: list[tuple[str, ...]] = []

    @property
    def provenance(self) -> EmbeddingProvenance:
        return self._provenance

    @property
    def available(self) -> bool:
        return self._available

    def embed_batch(
        self,
        texts: list[str] | tuple[str, ...],
        *,
        profile: EmbeddingProfile,
    ) -> EmbeddingBatchResult:
        del profile
        items = tuple(texts)
        self.calls.append(items)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("simulated provider failure")
        vectors = [
            struct.pack(
                f"<{self.provenance.dimension}f",
                *([float(index + 1)] + [0.0] * (self.provenance.dimension - 1)),
            )
            for index, _text in enumerate(items)
        ]
        if self.mixed_dimension and len(vectors) > 1:
            vectors[1] = vectors[1][:-4]
        return EmbeddingBatchResult(
            vectors=tuple(vectors),
            provenance=self.provenance,
        )


def _table_counts(store: SQLiteStore) -> tuple[int, int]:
    with store.connection() as db:
        cache = int(db.execute("SELECT count(*) FROM code_unit_embedding_cache").fetchone()[0])
        vectors = int(db.execute("SELECT count(*) FROM code_unit_vectors").fetchone()[0])
    return cache, vectors


def test_profile_is_frozen_strict_and_has_canonical_snapshot() -> None:
    profile = _profile()

    assert json.loads(profile.canonical_json()) == profile.canonical_snapshot()
    assert tuple(profile.canonical_snapshot()) == tuple(sorted(profile.canonical_snapshot()))
    with pytest.raises(FrozenInstanceError):
        profile.dimension = 64  # type: ignore[misc]
    with pytest.raises(ValueError, match="purpose"):
        _profile(purpose="generic")
    with pytest.raises(ValueError, match="positive integer"):
        _profile(batch_size=0)
    with pytest.raises(TypeError, match="string"):
        _profile(revision=1)


def test_local_hash_provider_preserves_existing_binary_format() -> None:
    profile = _profile()
    provider = LocalHashCodeEmbeddingProvider(profile.dimension, model_revision="algorithm-r2")

    result = provider.embed_batch(("def needle(): return value",), profile=profile)

    assert result.provenance.model == "local-hash-v2"
    assert result.provenance.revision == "algorithm-r2"
    assert result.provenance.locality == "local"
    assert result.vectors == (
        LocalHashEmbedding(profile.dimension).embed("def needle(): return value"),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "code-code-v2"),
        ("revision", "profile-r2"),
        ("instruction_revision", "instruction-r2"),
        ("normalization_revision", "normalization-r2"),
        ("model", "other-model"),
    ],
)
def test_cache_key_misses_when_profile_or_revision_axis_changes(
    field: str,
    value: str,
) -> None:
    profile = _profile()
    baseline = embedding_cache_key(
        "sha256:content",
        profile,
        model="local-hash-v2",
        model_revision="model-r1",
    )

    changed = replace(profile, **{field: value})

    assert (
        embedding_cache_key(
            "sha256:content",
            changed,
            model=changed.model,
            model_revision="model-r1",
        )
        != baseline
    )
    assert (
        embedding_cache_key(
            "sha256:content",
            profile,
            model="local-hash-v2",
            model_revision="model-r2",
        )
        != baseline
    )


def test_content_addressed_cache_hit_and_idempotent_vector_publish(
    tmp_path: Path,
) -> None:
    content = "def same_content(): return 1"
    shared_hash = _content_hash(content)
    first = _unit("unit://first", content, content_hash=shared_hash)
    second = _unit("unit://second", content, content_hash=shared_hash)
    store = _store(tmp_path, [first, second])
    provider = RecordingProvider()
    indexer = CodeEmbeddingIndexer(store, provider)

    first_result = indexer.index([first], _profile())
    second_result = indexer.index([second], _profile())
    repeated_result = indexer.index([second], _profile())

    assert (first_result.cache_hits, first_result.cache_misses) == (0, 1)
    assert (second_result.cache_hits, second_result.cache_misses) == (1, 0)
    assert (repeated_result.cache_hits, repeated_result.cache_misses) == (1, 0)
    assert len(provider.calls) == 1
    assert _table_counts(store) == (1, 2)
    assert second_result.provenance_trace[0].generation_id == second["generation_id"]
    assert second_result.provenance_trace[0].acl_ref == second["acl_ref"]


def test_profile_and_model_revision_changes_cause_real_cache_misses(
    tmp_path: Path,
) -> None:
    unit = _unit("unit://revision", "def revision(): return 1")
    store = _store(tmp_path, [unit])
    first_provider = RecordingProvider(revision="model-r1")
    first = CodeEmbeddingIndexer(store, first_provider).index([unit], _profile())
    profile_changed_provider = RecordingProvider(revision="model-r1")
    profile_changed = CodeEmbeddingIndexer(store, profile_changed_provider).index(
        [unit],
        _profile(revision="profile-r2"),
    )
    model_changed_provider = RecordingProvider(revision="model-r2")
    model_changed = CodeEmbeddingIndexer(store, model_changed_provider).index(
        [unit],
        _profile(revision="profile-r2"),
    )

    assert first.cache_misses == 1
    assert profile_changed.cache_misses == 1
    assert model_changed.cache_misses == 1
    assert len(first_provider.calls) == 1
    assert len(profile_changed_provider.calls) == 1
    assert len(model_changed_provider.calls) == 1
    assert _table_counts(store) == (3, 1)


def test_mixed_dimension_fails_before_cache_or_vector_write(tmp_path: Path) -> None:
    units = [
        _unit("unit://one", "def one(): return 1"),
        _unit("unit://two", "def two(): return 2"),
    ]
    store = _store(tmp_path, units)
    provider = RecordingProvider(mixed_dimension=True)

    with pytest.raises(EmbeddingDimensionError, match="byte length"):
        CodeEmbeddingIndexer(store, provider).index(units, _profile())

    assert _table_counts(store) == (0, 0)


def test_late_batch_error_has_zero_partial_publication(tmp_path: Path) -> None:
    units = [
        _unit("unit://one", "def one(): return 1"),
        _unit("unit://two", "def two(): return 2"),
    ]
    store = _store(tmp_path, units)
    provider = RecordingProvider(fail_on_call=2)

    with pytest.raises(EmbeddingBatchError, match="batch failed"):
        CodeEmbeddingIndexer(store, provider).index(
            units,
            _profile(batch_size=1),
        )

    assert len(provider.calls) == 2
    assert _table_counts(store) == (0, 0)


def test_secret_and_quarantined_units_never_reach_provider(tmp_path: Path) -> None:
    units = [
        _unit(
            "unit://safe",
            "def safe(): return 1",
        ),
        _unit(
            "unit://secret",
            'api_key = "actual-secret-value"',
        ),
        _unit(
            "unit://quarantine",
            "def harmless(): return 1",
            quality_status="quarantined",
        ),
    ]
    store = _store(tmp_path, units)
    provider = RecordingProvider()

    result = CodeEmbeddingIndexer(store, provider).index(units, _profile())

    assert provider.calls == [("def safe(): return 1",)]
    assert result.indexed == 1
    assert result.skipped == 2
    assert [trace.reason for trace in result.provenance_trace] == [
        None,
        "secret_detected",
        "quarantined",
    ]
    assert _table_counts(store) == (1, 1)


def test_remote_profile_is_fail_closed_without_explicit_fallback(
    tmp_path: Path,
) -> None:
    unit = _unit("unit://remote", "def remote(): return 1")
    store = _store(tmp_path, [unit])

    with pytest.raises(EmbeddingProviderUnavailableError, match="local-hash-v2"):
        CodeEmbeddingIndexer(store).index(
            [unit],
            _profile(model="remote-code-model", locality="remote"),
        )

    assert _table_counts(store) == (0, 0)


def test_unavailable_provider_fallback_is_explicit_and_traced(tmp_path: Path) -> None:
    unit = _unit("unit://fallback", "def fallback(): return 1")
    store = _store(tmp_path, [unit])
    unavailable = RecordingProvider(
        model="remote-code-model",
        revision="remote-r1",
        locality="remote",
        available=False,
    )
    profile = _profile(
        model="remote-code-model",
        locality="remote",
    )

    result = CodeEmbeddingIndexer(
        store,
        unavailable,
        allow_local_fallback=True,
    ).index([unit], profile)

    trace = result.provenance_trace[0]
    assert result.fallback_used is True
    assert result.fallback is True
    assert result.fallbacks == 1
    assert trace.fallback is True
    assert trace.reason == "provider_unavailable_local_hash_fallback"
    assert trace.provenance is not None
    assert trace.provenance.model == "local-hash-v2"
    assert unavailable.calls == []
    assert _table_counts(store) == (1, 1)

    default_unavailable = CodeEmbeddingIndexer(
        store,
        allow_local_fallback=True,
    ).index([unit], profile)
    assert default_unavailable.fallback is True
    assert default_unavailable.cache_hits == 1
