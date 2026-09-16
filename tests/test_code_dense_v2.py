from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.models import (
    EntityRecord,
    EvidenceSearchRequest,
    RepositoryIngestRequest,
    SearchScope,
)
from evidence_rag.parser import CodeParser
from evidence_rag.rag.sources.code import (
    DEFAULT_CODE_NL_PROFILE,
    DENSE_MODEL_CANDIDATES,
    CodeChannelOutcomeStatus,
    CodeDenseProfilePublisher,
    CodeDensePublicationError,
    CodeDenseRetriever,
    CodeDualWriteCoordinator,
    CodeHybridV2Retriever,
    CodeRetrievalChannel,
    CodeSourceResult,
    CodeSourceStatus,
    EmbeddingBatchResult,
    EmbeddingProfile,
    EmbeddingProvenance,
    resolve_code_embedding_profile,
)
from evidence_rag.repository import RepositorySnapshot
from evidence_rag.storage import SQLiteStore

PROJECT = "project://dense"
REPOSITORY = "repository://dense"
GENERATION = "generation://dense-active"
COMMIT = "a" * 40
ACL = "acl://dense"


def _source_files() -> dict[str, str]:
    return {
        "src/search.py": (
            "def locate_semantic_code(question: str):\n"
            "    return embedding_search(question)\n\n"
            "def unrelated_counter(value: int):\n"
            "    return value + 1\n"
        ),
        "src/admin/formatting.py": (
            "def format_line(label: str):\n    return '[admin] ' + label\n"
        ),
        "src/checkout/formatting.py": (
            "def format_line(label: str):\n    return '[checkout] ' + label\n"
        ),
        "src/tax/formatting.py": ("def format_line(label: str):\n    return '[tax] ' + label\n"),
    }


def _integrated_store(
    tmp_path: Path,
) -> tuple[SQLiteStore, list[dict[str, Any]]]:
    store = SQLiteStore(tmp_path / "dense-v2.sqlite3")
    store.initialize()
    parser = CodeParser()
    files = _source_files()
    parsed_files = [
        parser.parse(path, content, blob_hash=f"blob:{index}")
        for index, (path, content) in enumerate(files.items())
    ]
    snapshot = RepositorySnapshot(
        id=REPOSITORY,
        project_id=PROJECT,
        name="dense",
        source_type="git_local",
        source_url=None,
        local_path=tmp_path,
        default_branch="main",
        head_commit=COMMIT,
        base_commit=COMMIT,
        dirty=False,
        acl_ref=ACL,
    )
    request = RepositoryIngestRequest(
        source=str(tmp_path),
        branch="main",
        project_id=PROJECT,
        acl_ref=ACL,
    )
    entities = [
        EntityRecord(
            id=f"entity://dense/file/{index}",
            repository_id=REPOSITORY,
            generation_id=GENERATION,
            project_id=PROJECT,
            entity_type="FileVersion",
            name=parsed.path,
            qualified_name=parsed.path,
            path=parsed.path,
            language=parsed.language,
            commit_sha=COMMIT,
            blob_hash=parsed.blob_hash,
            content_hash=parsed.content_hash,
            start_line=1,
            end_line=max(1, parsed.content.count("\n")),
            source_uri=f"code://{REPOSITORY}@{COMMIT}/{parsed.path}",
            acl_ref=ACL,
            content=parsed.content,
        )
        for index, parsed in enumerate(parsed_files)
    ]
    with store.transaction() as db:
        db.execute(
            """
            INSERT INTO repositories(
                id, project_id, name, source_type, local_path, default_branch,
                head_commit, acl_ref, status, active_generation_id,
                created_at, updated_at
            ) VALUES (?, ?, 'dense', 'local', ?, 'main', ?, ?, 'ready', ?, 'now', 'now')
            """,
            (REPOSITORY, PROJECT, str(tmp_path), COMMIT, ACL, GENERATION),
        )
        db.execute(
            """
            INSERT INTO index_generations(
                id, repository_id, commit_sha, status, started_at, completed_at
            ) VALUES (?, ?, ?, 'published', 'now', 'now')
            """,
            (GENERATION, REPOSITORY, COMMIT),
        )
        db.executemany(
            """
            INSERT INTO entities(
                id, repository_id, generation_id, project_id, entity_type, name,
                qualified_name, path, language, commit_sha, blob_hash, content_hash,
                start_line, end_line, source_uri, acl_ref, content, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            [
                (
                    entity.id,
                    entity.repository_id,
                    entity.generation_id,
                    entity.project_id,
                    entity.entity_type,
                    entity.name,
                    entity.qualified_name,
                    entity.path,
                    entity.language,
                    entity.commit_sha,
                    entity.blob_hash,
                    entity.content_hash,
                    entity.start_line,
                    entity.end_line,
                    entity.source_uri,
                    entity.acl_ref,
                    entity.content,
                )
                for entity in entities
            ],
        )
    dual_write = CodeDualWriteCoordinator(store).write(
        snapshot=snapshot,
        request=request,
        workflow_id="workflow://dense",
        generation_id=GENERATION,
        parsed_files=parsed_files,
        entities=entities,
        raw_object_ids={
            parsed.path: f"raw://dense/{index}" for index, parsed in enumerate(parsed_files)
        },
        snapshot_raw_object_id="raw://dense/snapshot",
    )
    publication = dual_write.publication
    validation = deepcopy(publication["validation"])
    validation["capabilities"]["sparse_retrieval"] = True
    store.upsert_code_index_publication(
        {
            "generation_id": GENERATION,
            "repository_id": REPOSITORY,
            "project_id": PROJECT,
            "builder": publication["builder"],
            "sparse": "fts5-code-v2",
            "embedding": publication["embedding"],
            "graph": publication["graph"],
            "status": publication["status"],
            "validation": validation,
        }
    )
    units = store.exact_code_unit_lookup(
        path="src/search.py",
        project_id=PROJECT,
        repository_ids=[REPOSITORY],
        generation_id=GENERATION,
        active_only=False,
        limit=100,
    )
    for path in (
        "src/admin/formatting.py",
        "src/checkout/formatting.py",
        "src/tax/formatting.py",
    ):
        units.extend(
            store.exact_code_unit_lookup(
                path=path,
                project_id=PROJECT,
                repository_ids=[REPOSITORY],
                generation_id=GENERATION,
                active_only=False,
                limit=100,
            )
        )
    assert len(units) == dual_write.unit_count
    return store, units


def _request(
    query: str,
    *,
    project_id: str = PROJECT,
    repositories: list[str] | None = None,
    acl_refs: list[str] | None = None,
    enforce_acl: bool = True,
    commit: str | None = None,
    limit: int = 8,
) -> EvidenceSearchRequest:
    return EvidenceSearchRequest(
        query=query,
        scope=SearchScope(
            project_id=project_id,
            repository_ids=repositories or [REPOSITORY],
            commit=commit,
            allowed_acl_refs=acl_refs if acl_refs is not None else [ACL],
            enforce_acl=enforce_acl,
        ),
        limit=limit,
    )


def _publish(
    store: SQLiteStore,
    units: list[dict[str, Any]],
) -> Any:
    return CodeDenseProfilePublisher(store).publish(
        units,
        project_id=PROJECT,
        repository_id=REPOSITORY,
        generation_id=GENERATION,
    )


def _counts(store: SQLiteStore) -> tuple[int, int]:
    with store.connection() as db:
        cache = int(db.execute("SELECT count(*) FROM code_unit_embedding_cache").fetchone()[0])
        vectors = int(db.execute("SELECT count(*) FROM code_unit_vectors").fetchone()[0])
    return cache, vectors


def test_parser_dual_write_publisher_and_dense_low_overlap_round_trip(
    tmp_path: Path,
) -> None:
    store, units = _integrated_store(tmp_path)

    first = _publish(store, units)
    second = _publish(store, units)
    response = CodeDenseRetriever(store).search_with_trace(_request("哪里实现向量检索", limit=4))

    assert first.unit_count == first.vector_count == len(units)
    assert first.cache_misses == len(units)
    assert second.cache_hits == len(units)
    assert second.cache_misses == 0
    assert first.publication["embedding"] == "code_nl/local-hash-v2@1"
    state = second.publication["validation"]["embedding_profiles"][DEFAULT_CODE_NL_PROFILE.id]
    assert state["status"] == "ready"
    assert state["profile"] == DEFAULT_CODE_NL_PROFILE.canonical_snapshot()
    assert state["unit_count"] == state["vector_count"] == len(units)
    assert state["content_hash"].startswith("sha256:")
    assert state["vector_hash"].startswith("sha256:")
    assert second.publication["validation"]["capabilities"]["dense_retrieval"] is True

    assert response.result.status is CodeSourceStatus.COMPLETE
    assert response.result.candidates
    assert response.result.candidates[0].locator.startswith(
        f"code://{REPOSITORY}@{COMMIT}/src/search.py#L"
    )
    assert "locate_semantic_code" in response.result.candidates[0].locator or (
        response.result.candidates[0].retrieval_unit_id
        in {unit["id"] for unit in units if unit["path"] == "src/search.py"}
    )
    assert response.trace.channel_hits[0].profile == DEFAULT_CODE_NL_PROFILE.id
    assert response.trace.channel_hits[0].model == "local-hash-v2"
    assert response.trace.channel_hits[0].dimension == DEFAULT_CODE_NL_PROFILE.dimension
    assert (
        CodeSourceResult.model_validate_json(response.result.model_dump_json()) == response.result
    )


def test_publisher_rejects_partial_or_tampered_caller_scope_before_writes(
    tmp_path: Path,
) -> None:
    store, units = _integrated_store(tmp_path)

    with pytest.raises(CodeDensePublicationError, match="complete C2 generation"):
        _publish(store, units[:-1])
    assert _counts(store) == (0, 0)
    unready = CodeDenseRetriever(store).search_with_trace(_request("format_line"))
    assert unready.result.status is CodeSourceStatus.UNAVAILABLE
    assert "not ready" in unready.trace.unavailable_reason
    publication = store.get_code_index_publication(
        GENERATION,
        repository_id=REPOSITORY,
        active_only=False,
    )
    assert publication is not None
    assert publication["embedding"] == "not-built"

    tampered = [dict(unit) for unit in units]
    tampered[0]["content"] += "\n# changed"
    with pytest.raises(CodeDensePublicationError, match="content hash"):
        _publish(store, tampered)
    assert _counts(store) == (0, 0)


def test_cross_transaction_vector_failure_cleans_generation_profile_and_stays_unready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, units = _integrated_store(tmp_path)
    original = store.upsert_code_unit_vectors

    def fail_after_vector_write(records: Any) -> int:
        written = original(records)
        assert written == len(units)
        raise RuntimeError("injected vector transaction boundary failure")

    monkeypatch.setattr(store, "upsert_code_unit_vectors", fail_after_vector_write)

    with pytest.raises(RuntimeError, match="transaction boundary"):
        _publish(store, units)

    cache, vectors = _counts(store)
    assert cache == len(units)
    assert vectors == 0
    publication = store.get_code_index_publication(
        GENERATION,
        repository_id=REPOSITORY,
        active_only=False,
    )
    assert publication is not None
    assert publication["embedding"] == "not-built"
    state = publication["validation"]["embedding_profiles"][DEFAULT_CODE_NL_PROFILE.id]
    assert state["status"] == "unavailable"
    assert publication["validation"]["capabilities"]["dense_retrieval"] is False


class _UnavailableLocalProvider:
    provenance = EmbeddingProvenance(
        provider="local-hash",
        model="local-hash-v2",
        revision="2",
        dimension=DEFAULT_CODE_NL_PROFILE.dimension,
        locality="local",
    )
    available = False

    def embed_batch(
        self,
        texts: Any,
        *,
        profile: EmbeddingProfile,
    ) -> EmbeddingBatchResult:
        del texts, profile
        raise AssertionError("an unavailable provider must not be called")


def test_dense_scope_acl_profile_dimension_and_provider_fail_closed(
    tmp_path: Path,
) -> None:
    store, units = _integrated_store(tmp_path)
    _publish(store, units)
    retriever = CodeDenseRetriever(store)

    wrong_project = retriever.search_with_trace(
        _request("format_line", project_id="project://other")
    )
    assert wrong_project.result.status is CodeSourceStatus.UNAVAILABLE
    assert not wrong_project.result.candidates

    unauthorized = retriever.search_with_trace(_request("format_line", acl_refs=["acl://other"]))
    assert unauthorized.result.status is CodeSourceStatus.COMPLETE
    assert not unauthorized.result.candidates

    wrong_generation = retriever.search_with_trace(_request("format_line", commit="b" * 40))
    assert wrong_generation.result.status is CodeSourceStatus.UNAVAILABLE

    unavailable_provider = CodeDenseRetriever(
        store,
        provider=_UnavailableLocalProvider(),
    ).search_with_trace(_request("format_line"))
    assert unavailable_provider.result.status is CodeSourceStatus.UNAVAILABLE
    assert "provider is unavailable" in unavailable_provider.trace.unavailable_reason

    publication = store.get_code_index_publication(
        GENERATION,
        repository_id=REPOSITORY,
        active_only=False,
    )
    assert publication is not None
    validation = deepcopy(publication["validation"])
    validation["embedding_profiles"][DEFAULT_CODE_NL_PROFILE.id]["provider"]["dimension"] = 32
    store.upsert_code_index_publication(
        {
            "generation_id": GENERATION,
            "repository_id": REPOSITORY,
            "project_id": PROJECT,
            "builder": publication["builder"],
            "sparse": publication["sparse"],
            "embedding": publication["embedding"],
            "graph": publication["graph"],
            "status": publication["status"],
            "validation": validation,
        }
    )
    wrong_dimension = retriever.search_with_trace(_request("format_line"))
    assert wrong_dimension.result.status is CodeSourceStatus.UNAVAILABLE
    assert "dimension" in wrong_dimension.trace.unavailable_reason
    dense_outcome = next(
        outcome
        for outcome in wrong_dimension.result.channel_outcomes
        if outcome.channel is CodeRetrievalChannel.DENSE
    )
    assert dense_outcome.status is CodeChannelOutcomeStatus.UNAVAILABLE


def test_dense_top_k_and_trace_are_deterministic(tmp_path: Path) -> None:
    store, units = _integrated_store(tmp_path)
    _publish(store, units)
    retriever = CodeDenseRetriever(store)
    request = _request("format line label", limit=3)

    first = retriever.search_with_trace(request)
    second = retriever.search_with_trace(request)

    assert first.result.query_id == second.result.query_id
    assert [candidate.retrieval_unit_id for candidate in first.result.candidates] == [
        candidate.retrieval_unit_id for candidate in second.result.candidates
    ]
    assert [candidate.source_fused_score for candidate in first.result.candidates] == [
        candidate.source_fused_score for candidate in second.result.candidates
    ]
    assert [
        (hit.retrieval_unit_id, hit.raw_rank, hit.raw_score, hit.locator)
        for hit in first.trace.channel_hits
    ] == [
        (hit.retrieval_unit_id, hit.raw_rank, hit.raw_score, hit.locator)
        for hit in second.trace.channel_hits
    ]
    assert len(first.result.candidates) == 3
    assert [candidate.within_source_rank for candidate in first.result.candidates] == [
        0,
        1,
        2,
    ]


def test_hybrid_preserves_exact_hard_negatives_paths_and_channel_truth(
    tmp_path: Path,
) -> None:
    store, units = _integrated_store(tmp_path)
    _publish(store, units)
    response = CodeHybridV2Retriever(store).search_with_trace(_request("format_line", limit=3))

    assert response.result.status is CodeSourceStatus.COMPLETE
    assert len(response.result.candidates) == 3
    assert all(
        CodeRetrievalChannel.EXACT in {score.channel for score in candidate.raw_channel_scores}
        for candidate in response.result.candidates
    )
    paths = {
        candidate.locator.split("@", 1)[1].split("/", 1)[1].split("#", 1)[0]
        for candidate in response.result.candidates
    }
    assert paths == {
        "src/admin/formatting.py",
        "src/checkout/formatting.py",
        "src/tax/formatting.py",
    }
    assert all(hit.exact_hard_signal for hit in response.trace.channel_hits)
    assert all("without silent selection" in hit.explanation for hit in response.trace.channel_hits)
    assert all(
        hit.locator.startswith(f"code://{REPOSITORY}@{COMMIT}/")
        for hit in response.trace.channel_hits
    )
    assert "hard-exact-first" in response.trace.fusion_policy
    assert (
        CodeSourceResult.model_validate_json(response.result.model_dump_json()) == response.result
    )


def test_candidate_registry_is_explicit_and_has_no_benchmark_claims() -> None:
    snapshots = [candidate.canonical_snapshot() for candidate in DENSE_MODEL_CANDIDATES]

    assert {item["purpose"] for item in snapshots} >= {
        "code_nl",
        "code_code",
        "history",
        "error",
    }
    assert all(item["benchmark_status"] == "not-run" for item in snapshots)
    remote = [item for item in snapshots if item["locality"] == "remote"]
    assert remote
    assert all(item["availability"] == "unavailable" for item in remote)
    assert all("network" in item["reason"] or "no model" in item["reason"] for item in remote)


def test_configured_embedding_profile_resolves_without_model_substitution() -> None:
    profile, provider = resolve_code_embedding_profile(
        "local-hash-v2",
        dimension=96,
    )

    assert profile.id == DEFAULT_CODE_NL_PROFILE.id
    assert profile.dimension == 96
    assert provider.dimension == 96
    with pytest.raises(ValueError, match="no configured offline provider"):
        resolve_code_embedding_profile("qwen-code.v3", dimension=96)
