from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.codex_adapter import CodexSessionAdapter
from evidence_rag.config import Settings
from evidence_rag.rag.sources.codex.contracts import (
    EventScope,
    ObservableCodexItem,
    canonical_codex_locator_v1,
)
from evidence_rag.rag.sources.codex.episode_v2 import (
    CodexEpisodeBoundaryReason,
    build_codex_episodes_v2,
)
from evidence_rag.rag.sources.codex.event_normalizer import normalize_codex_events_v1
from evidence_rag.rag.sources.codex.observable_adapter_v2 import (
    adapt_production_codex_items_v2,
)
from evidence_rag.rag.sources.codex.store_v2 import (
    CodexV2PublicationError,
    CodexV2Store,
    CodexV2StorePathError,
)
from evidence_rag.rag.sources.codex.units_v2 import (
    CodexEvidenceLevel,
    CodexTemporalEdgeType,
    CodexUnitRole,
    build_codex_retrieval_publication_v2,
)


def _scope(turn: str = "turn-a", generation: str = "generation-a") -> EventScope:
    return EventScope(
        project_id="project-a",
        source_id="codex-source:a",
        generation_id=generation,
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


def _records() -> tuple[ObservableCodexItem, ...]:
    return (
        _item("goal-a", 0, "UserGoal", payload={"event_at": "2026-01-01T00:00:00Z"}),
        _item(
            "command-a",
            1,
            "CommandExecution",
            call_id="call-a",
            command_argv=("pytest", "tests/test_a.py"),
            targets=("tests/test_a.py",),
            target_project_id="project-a",
            payload={"event_at": "2026-01-01T00:00:01Z"},
        ),
        _item(
            "result-a",
            2,
            "CommandResult",
            call_id="call-a",
            call_type="command",
            outcome="failed",
            success=False,
            payload={"exit_code": 1, "event_at": "2026-01-01T00:00:02Z"},
        ),
        _item(
            "retry-a",
            0,
            "CommandExecution",
            turn="turn-b",
            call_id="call-b",
            command_argv=("pytest", "tests/test_a.py"),
            targets=("tests/test_a.py",),
            target_project_id="project-a",
            payload={"event_at": "2026-01-01T00:00:03Z"},
        ),
        _item(
            "retry-result-a",
            1,
            "CommandResult",
            turn="turn-b",
            call_id="call-b",
            call_type="command",
            outcome="completed",
            success=True,
            payload={"exit_code": 0, "event_at": "2026-01-01T00:00:04Z"},
        ),
        _item(
            "goal-b",
            0,
            "UserGoal",
            turn="turn-c",
            payload={"event_at": "2026-01-01T01:00:00Z"},
        ),
        _item(
            "plan-b",
            1,
            "Plan",
            turn="turn-c",
            role="assistant",
            payload={"event_at": "2026-01-01T01:00:01Z"},
        ),
        _item(
            "reasoning-b",
            2,
            "reasoning",
            turn="turn-c",
            role="assistant",
        ),
    )


def test_goal_episodes_are_source_bound_versioned_and_manual_merge_is_audited() -> None:
    records = _records()
    normalization = normalize_codex_events_v1(records)
    result = build_codex_episodes_v2(records, normalization)

    assert len(result.episodes) == 3
    assert [item.reason for item in result.boundaries] == [
        CodexEpisodeBoundaryReason.THREAD_START,
        CodexEpisodeBoundaryReason.FAILURE_RECOVERY,
        CodexEpisodeBoundaryReason.NEW_USER_GOAL,
    ]
    assert result.excluded_item_ids == ("reasoning-b",)
    assert sum(len(item.member_ids) for item in result.episodes) == 7
    assert all(item.episode_version_id.startswith("codexepv-") for item in result.episodes)

    merged = build_codex_episodes_v2(
        records,
        normalization,
        manual_overrides={"goal-b": "merge"},
    )
    assert len(merged.episodes) == 2
    assert len(merged.manual_overrides) == 1
    assert merged.manual_overrides[0].operation == "merge"
    assert merged.manual_override_ids == (merged.manual_overrides[0].override_id,)

    wrong = normalize_codex_events_v1(records[:-1])
    with pytest.raises(ValueError, match="exact production-derived"):
        build_codex_episodes_v2(records, wrong)

    continuation_records = (
        _item(
            "goal-continued-a",
            0,
            "UserGoal",
            payload={"text": "Fix parser validation for router"},
        ),
        _item(
            "goal-continued-b",
            0,
            "UserGoal",
            turn="turn-b",
            payload={
                "text": "Fix parser validation for router",
                "continuation_of": "goal-continued-a",
            },
        ),
    )
    continuation_normalization = normalize_codex_events_v1(continuation_records)
    continued = build_codex_episodes_v2(
        continuation_records,
        continuation_normalization,
    )
    assert len(continued.episodes) == 1


def test_retrieval_units_exclude_reasoning_and_build_observable_graph() -> None:
    records = _records()
    normalization = normalize_codex_events_v1(records)
    episodes = build_codex_episodes_v2(records, normalization)
    publication = build_codex_retrieval_publication_v2(
        records,
        normalization,
        episodes,
    )

    assert publication.reasoning_units == 0
    assert all("reasoning-b" not in unit.source_item_ids for unit in publication.units)
    assert {CodexUnitRole.THREAD, CodexUnitRole.EPISODE, CodexUnitRole.GOAL} <= {
        item.role for item in publication.units
    }
    assert CodexUnitRole.FAILURE in {item.role for item in publication.units}
    assert CodexTemporalEdgeType.RETRIES in {item.edge_type for item in publication.edges}
    plans = [item for item in publication.units if item.role is CodexUnitRole.PLAN]
    assert plans and plans[0].evidence_level is CodexEvidenceLevel.CANDIDATE_ONLY
    assert all(edge.from_unit_id != edge.to_unit_id for edge in publication.edges)


def test_isolated_store_publishes_rolls_back_and_tombstones_atomically(
    tmp_path: Path,
) -> None:
    records = _records()
    normalization = normalize_codex_events_v1(records)
    episodes = build_codex_episodes_v2(records, normalization)
    publication = build_codex_retrieval_publication_v2(
        records,
        normalization,
        episodes,
    )
    store = CodexV2Store(tmp_path / "isolated" / "codex-v2.sqlite3", isolated_root=tmp_path)
    store.initialize()

    published = store.publish(episodes, publication)
    assert published["operation"] == "published"
    assert published["counts"]["codex_episode_versions_v2"] == 3
    assert store.publish(episodes, publication)["operation"] == "already_published"
    assert store.active_units(
        project_id="project-a",
        source_id="codex-source:a",
        acl_ref="project:project-a",
    )

    rolled_back = store.rollback(
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
    )
    assert rolled_back["status"] == "rolled_back"
    assert not store.active_units(
        project_id="project-a",
        source_id="codex-source:a",
        acl_ref="project:project-a",
    )
    tombstone = store.tombstone(
        project_id="project-a",
        source_id="codex-source:a",
        generation_id="generation-a",
        reason="privacy deletion",
    )
    assert tombstone["status"] == "tombstoned"
    with pytest.raises(CodexV2PublicationError, match="tombstoned"):
        store.publish(episodes, publication)


def test_store_rejects_formal_name_and_symlink_alias(tmp_path: Path) -> None:
    with pytest.raises(CodexV2StorePathError, match="formal"):
        CodexV2Store(tmp_path / "evidence-rag.sqlite3", isolated_root=tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(CodexV2StorePathError, match="symlink"):
        CodexV2Store(alias / "codex.sqlite3", isolated_root=tmp_path)


def test_exact_production_adapter_records_feed_x2_without_golden_labels(
    tmp_path: Path,
) -> None:
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
    parsed = []
    parsed_turns = []
    for path in sorted(
        (
            *fixture.joinpath("sessions").glob("*.jsonl"),
            *fixture.joinpath("archived_sessions").glob("*.jsonl"),
        )
    ):
        session = adapter.parse(
            path,
            source_root=fixture,
            source_id="codex-source:golden-v1",
            generation_id="codex-generation:golden-v1",
            project_id="project-rag",
            project_path=None,
            acl_ref="project:project-rag",
            titles=titles,
        )
        assert session is not None
        parsed.extend(session.items)
        parsed_turns.extend(session.turns)
    assert len(parsed) == 54

    adapted = adapt_production_codex_items_v2(
        parsed,
        project_id="project-rag",
        turns=parsed_turns,
    )
    assert adapted.source_record_count == 54
    assert len(adapted.thread_sets) == 8
    assert adapted.observable_item_count >= 54 - adapted.skipped_episode_count
    for thread in adapted.thread_sets:
        normalization = normalize_codex_events_v1(thread.observable_items)
        episodes = build_codex_episodes_v2(thread.observable_items, normalization)
        publication = build_codex_retrieval_publication_v2(
            thread.observable_items,
            normalization,
            episodes,
        )
        assert publication.reasoning_units == 0
