from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.codex_adapter import CodexSessionAdapter
from evidence_rag.config import Settings
from evidence_rag.rag.sources.codex.pipeline_v2 import (
    CODEX_PIPELINE_COMPONENT_SET_SHA256,
    build_codex_thread_pipeline_v2,
    publish_codex_thread_pipeline_v2,
    query_codex_thread_pipeline_v2,
)
from evidence_rag.rag.sources.codex.retrieval_v2 import (
    CodexCandidateChannel,
    CodexQueryTask,
)
from evidence_rag.rag.sources.codex.store_v2 import CodexV2Store


def _production_session(
    tmp_path: Path,
    *,
    generation_id: str = "codex-generation:pipeline-smoke",
):
    fixture = Path(__file__).parent / "fixtures" / "codex_golden_v1"
    adapter = CodexSessionAdapter(
        Settings(
            data_dir=tmp_path,
            database_path=tmp_path / "unused.sqlite3",
            repository_cache=tmp_path,
            web_dir=tmp_path,
            allowed_local_roots=(fixture,),
            max_codex_item_chars=16_000,
        )
    )
    titles = adapter.read_titles(fixture)
    path = sorted(fixture.joinpath("sessions").glob("*.jsonl"))[0]
    session = adapter.parse(
        path,
        source_root=fixture,
        source_id="codex-source:pipeline-smoke",
        generation_id=generation_id,
        project_id="project-rag",
        project_path=None,
        acl_ref="project:project-rag",
        titles=titles,
    )
    assert session is not None and session.items
    return session


def test_exact_production_pipeline_publishes_and_queries_without_labels(
    tmp_path: Path,
) -> None:
    session = _production_session(tmp_path)
    thread_id = session.items[0].thread_id
    bundle = build_codex_thread_pipeline_v2(
        session.items,
        project_id="project-rag",
        thread_id=thread_id,
        turns=session.turns,
    )
    assert bundle.component_set_sha256 == CODEX_PIPELINE_COMPONENT_SET_SHA256
    assert bundle.publication.reasoning_units == 0

    store = CodexV2Store(
        tmp_path / "isolated" / "codex-pipeline.sqlite3",
        isolated_root=tmp_path,
    )
    store.initialize()
    receipt = publish_codex_thread_pipeline_v2(store, bundle)
    assert receipt.operation == "published"
    assert publish_codex_thread_pipeline_v2(store, bundle).operation == "already_published"

    result = query_codex_thread_pipeline_v2(
        bundle,
        "validation command failure retry",
        task=CodexQueryTask.FAILURE_RETRY,
        final_k=8,
        enabled_channels=frozenset(CodexCandidateChannel),
        store=store,
    )
    assert result.retrieval.enabled_channels == tuple(sorted(CodexCandidateChannel, key=str))
    assert result.context.publication_sha256 == bundle.publication.publication_sha256
    assert not result.context.reasoning_included
    assert result.persisted_dense_entries > 0


def test_pipeline_rejects_component_and_source_chain_tamper(tmp_path: Path) -> None:
    session = _production_session(tmp_path)
    bundle = build_codex_thread_pipeline_v2(
        session.items,
        project_id="project-rag",
        thread_id=session.items[0].thread_id,
        turns=session.turns,
    )
    tampered = bundle.model_copy(update={"component_set_sha256": "sha256:" + ("0" * 64)})
    with pytest.raises(ValueError, match="component"):
        query_codex_thread_pipeline_v2(
            tampered,
            "validation",
            task=CodexQueryTask.VALIDATION,
        )


def test_generation_publication_is_atomic_and_rollback_restores_lkg(
    tmp_path: Path,
) -> None:
    first_session = _production_session(
        tmp_path,
        generation_id="codex-generation:pipeline-lkg-a",
    )
    second_session = _production_session(
        tmp_path,
        generation_id="codex-generation:pipeline-lkg-b",
    )
    first = build_codex_thread_pipeline_v2(
        first_session.items,
        project_id="project-rag",
        thread_id=first_session.items[0].thread_id,
        turns=first_session.turns,
    )
    second = build_codex_thread_pipeline_v2(
        second_session.items,
        project_id="project-rag",
        thread_id=second_session.items[0].thread_id,
        turns=second_session.turns,
    )
    store = CodexV2Store(
        tmp_path / "lkg" / "codex.sqlite3",
        isolated_root=tmp_path,
    )
    store.initialize()
    publish_codex_thread_pipeline_v2(
        store,
        first,
        authority_watermark="sha256:" + ("1" * 64),
        source_snapshot_sha256="sha256:" + ("2" * 64),
    )
    publish_codex_thread_pipeline_v2(
        store,
        second,
        authority_watermark="sha256:" + ("3" * 64),
        source_snapshot_sha256="sha256:" + ("4" * 64),
    )
    assert {
        item["generation_id"]
        for item in store.active_units(
            project_id=second.publication.project_id,
            source_id=second.publication.source_id,
            acl_ref=second.publication.acl_ref,
        )
    } == {second.publication.generation_id}

    rolled_back = store.rollback(
        project_id=second.publication.project_id,
        source_id=second.publication.source_id,
        generation_id=second.publication.generation_id,
    )
    assert rolled_back["lkg_generation_id"] == first.publication.generation_id
    assert {
        item["generation_id"]
        for item in store.active_units(
            project_id=first.publication.project_id,
            source_id=first.publication.source_id,
            acl_ref=first.publication.acl_ref,
        )
    } == {first.publication.generation_id}
    assert (
        store.lookup_pipeline_bundle(
            project_id=first.publication.project_id,
            source_id=first.publication.source_id,
            generation_id=first.publication.generation_id,
            thread_id=first.publication.thread_id,
            acl_ref=first.publication.acl_ref,
            authority_watermark="sha256:" + ("1" * 64),
            source_snapshot_sha256="sha256:" + ("2" * 64),
            component_set_sha256=first.component_set_sha256,
        )
        == first
    )
