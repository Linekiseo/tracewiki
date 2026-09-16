from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.rag.sources.codex.context_v2 import (
    CodexContextStatus,
    CodexContextWarning,
    build_codex_task_context_v2,
    project_codex_comparison_v2,
)
from evidence_rag.rag.sources.codex.contracts import (
    EventScope,
    ObservableCodexItem,
    canonical_codex_locator_v1,
)
from evidence_rag.rag.sources.codex.episode_v2 import build_codex_episodes_v2
from evidence_rag.rag.sources.codex.event_normalizer import normalize_codex_events_v1
from evidence_rag.rag.sources.codex.retrieval_v2 import (
    CodexCandidateChannel,
    CodexQueryTask,
    LocalCodexDenseCacheV2,
    build_codex_calibration_v2,
    build_codex_dense_profile_v2,
    build_codex_query_profile_v2,
    retrieve_codex_v2,
)
from evidence_rag.rag.sources.codex.store_v2 import CodexV2Store
from evidence_rag.rag.sources.codex.units_v2 import (
    build_codex_retrieval_publication_v2,
)


def _scope(turn: str = "turn-a") -> EventScope:
    return EventScope(
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
        thread_id="thread-a",
        turn_id=turn,
        acl_ref="project:project-a",
    )


def _item(
    item_id: str,
    order: int,
    item_type: str,
    *,
    turn: str = "turn-a",
    **values: object,
) -> ObservableCodexItem:
    scope = _scope(turn)
    return ObservableCodexItem(
        item_id=item_id,
        source_locator=canonical_codex_locator_v1(scope, item_id, order),
        scope=scope,
        observable_order=order,
        item_type=item_type,
        **values,
    )


def _publication():
    records = (
        _item(
            "goal",
            0,
            "UserGoal",
            role="user",
            payload={"text": "Fix parser validation for the request router"},
        ),
        _item(
            "plan",
            1,
            "Plan",
            role="assistant",
            payload={"text": "Compare the strict parser and retain the safer alternative"},
        ),
        _item(
            "command",
            2,
            "CommandExecution",
            call_id="call-a",
            command_argv=("pytest", "tests/test_parser.py"),
            targets=("tests/test_parser.py",),
            target_project_id="project-a",
        ),
        _item(
            "result",
            3,
            "CommandResult",
            call_id="call-a",
            call_type="command",
            outcome="completed",
            success=True,
            payload={"exit_code": 0, "stdout": "1 passed"},
        ),
        _item(
            "message",
            4,
            "AgentMessage",
            role="assistant",
            payload={"text": "Selected the strict parser after observable validation"},
        ),
    )
    normalized = normalize_codex_events_v1(records)
    episodes = build_codex_episodes_v2(records, normalized)
    publication = build_codex_retrieval_publication_v2(
        records,
        normalized,
        episodes,
    )
    return records, episodes, publication


def test_query_profile_dense_cache_and_calibration_are_versioned() -> None:
    profile = build_codex_query_profile_v2(
        "pytest tests/test_parser.py parser_validation",
        task=CodexQueryTask.VALIDATION,
        final_k=5,
    )
    assert profile.exact_paths == ("tests/test_parser.py",)
    assert "pytest" in profile.exact_commands
    dense = build_codex_dense_profile_v2(dimensions=32)
    cache = LocalCodexDenseCacheV2(
        dense,
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
        acl_ref="project:project-a",
    )
    first = cache.encode("parser validation", unit_id="unit-smoke")
    assert cache.encode("parser validation", unit_id="unit-smoke") == first
    assert cache.size == 1
    calibration = build_codex_calibration_v2(
        CodexQueryTask.VALIDATION,
        ((0.1, 0), (0.2, 0), (0.7, 1), (0.9, 2)),
    )
    assert calibration.threshold == pytest.approx(0.8)

    with pytest.raises(ValueError, match="unsafe"):
        build_codex_query_profile_v2(
            "read /Users/private/file",
            task=CodexQueryTask.PROCESS,
        )


def test_multichannel_retrieval_context_and_comparison_are_source_bound() -> None:
    _, _, publication = _publication()
    validation = retrieve_codex_v2(
        publication,
        "pytest tests/test_parser.py validation",
        task=CodexQueryTask.VALIDATION,
        final_k=6,
    )
    assert validation.candidates
    assert any(candidate.channels for candidate in validation.candidates)
    assert all(
        candidate.scope_proof_sha256.startswith("sha256:") for candidate in validation.candidates
    )
    assert all(candidate.base_rank >= 1 for candidate in validation.candidates)
    assert any(candidate.role.value == "validation" for candidate in validation.candidates)
    validation_context = build_codex_task_context_v2(
        publication,
        validation,
        budget_chars=1_024,
    )
    assert validation_context.status in {
        CodexContextStatus.AVAILABLE,
        CodexContextStatus.PROVISIONAL,
    }
    assert validation_context.blocks
    assert not validation_context.reasoning_included
    assert all(block.citation_id.startswith("codexcite-") for block in validation_context.blocks)

    rationale = retrieve_codex_v2(
        publication,
        "why strict parser decision alternative",
        task=CodexQueryTask.RATIONALE,
        final_k=6,
    )
    rationale_context = build_codex_task_context_v2(
        publication,
        rationale,
        budget_chars=1_024,
    )
    assert CodexContextWarning.RATIONALE_IS_OBSERVABLE_ONLY in rationale_context.warnings
    comparison = project_codex_comparison_v2(
        {"candidate-a": validation_context, "candidate-b": rationale_context}
    )
    assert len(comparison.entries) == 2
    assert comparison.entries[0].open_issue_count >= 0

    wrong_publication = publication.model_copy(
        update={"publication_sha256": "sha256:" + ("0" * 64)}
    )
    with pytest.raises(ValueError):
        build_codex_task_context_v2(wrong_publication, validation)


def test_retrieval_ablation_and_calibration_are_bound_into_result() -> None:
    _, _, publication = _publication()
    calibration = build_codex_calibration_v2(
        CodexQueryTask.VALIDATION,
        ((-0.2, 0), (0.1, 0), (0.5, 1), (0.9, 2)),
    )
    sparse_only = retrieve_codex_v2(
        publication,
        "pytest tests/test_parser.py validation passed",
        task=CodexQueryTask.VALIDATION,
        final_k=5,
        enabled_channels=frozenset({CodexCandidateChannel.SPARSE}),
        calibration=calibration,
    )
    assert sparse_only.enabled_channels == (CodexCandidateChannel.SPARSE,)
    assert sparse_only.calibration_sha256 == calibration.artifact_sha256
    assert all(
        candidate.channels == (CodexCandidateChannel.SPARSE,)
        for candidate in sparse_only.candidates
    )
    with pytest.raises(ValueError, match="at least one"):
        retrieve_codex_v2(
            publication,
            "validation",
            task=CodexQueryTask.VALIDATION,
            enabled_channels=frozenset(),
        )


def test_dense_cache_and_calibration_persist_only_in_isolated_store(tmp_path: Path) -> None:
    store = CodexV2Store(tmp_path / "cache" / "codex.sqlite3", isolated_root=tmp_path)
    store.initialize()
    _, episodes, publication = _publication()
    store.publish(episodes, publication)
    profile = build_codex_dense_profile_v2(dimensions=32)
    cache = LocalCodexDenseCacheV2(
        profile,
        project_id=publication.project_id,
        source_id=publication.source_id,
        generation_id=publication.generation_id,
        acl_ref=publication.acl_ref,
    )
    entry = cache.encode("parser validation", unit_id=publication.units[0].unit_id)
    assert store.put_dense_cache(entry) == "cached"
    assert store.put_dense_cache(entry) == "already_cached"
    assert (
        store.get_dense_cache(
            project_id=entry.project_id,
            source_id=entry.source_id,
            generation_id=entry.generation_id,
            acl_ref=entry.acl_ref,
            unit_id=entry.unit_id,
            profile_sha256=entry.profile_sha256,
            content_sha256=entry.content_sha256,
        )
        == entry
    )
    store.tombstone(
        project_id=entry.project_id,
        source_id=entry.source_id,
        generation_id=entry.generation_id,
        reason="privacy_request",
    )
    assert (
        store.get_dense_cache(
            project_id=entry.project_id,
            source_id=entry.source_id,
            generation_id=entry.generation_id,
            acl_ref=entry.acl_ref,
            unit_id=entry.unit_id,
            profile_sha256=entry.profile_sha256,
            content_sha256=entry.content_sha256,
        )
        is None
    )
    artifact = build_codex_calibration_v2(
        CodexQueryTask.PROCESS,
        ((0.1, 0), (0.3, 0), (0.6, 1), (0.8, 2)),
    )
    assert store.publish_calibration(artifact) == "published"
    assert store.publish_calibration(artifact) == "already_published"


def test_retrieval_unit_rejects_private_observable_text() -> None:
    record = _item(
        "goal",
        0,
        "UserGoal",
        role="user",
        payload={"text": "password=supersecretvalue"},
    )
    normalization = normalize_codex_events_v1((record,))
    episodes = build_codex_episodes_v2((record,), normalization)
    with pytest.raises(ValueError, match="private material"):
        build_codex_retrieval_publication_v2(
            (record,),
            normalization,
            episodes,
        )
