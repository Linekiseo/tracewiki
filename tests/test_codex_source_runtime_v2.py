from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.models import CodexItemRecord, CodexThreadRecord, CodexTurnRecord
from evidence_rag.rag.sources.codex.runtime_v2 import (
    CodexSourceRuntimeResultV2,
    CodexSourceRuntimeUnavailableV2,
    CodexSourceRuntimeV2,
    CodexSourceScopeErrorV2,
)
from evidence_rag.rag.sources.codex.store_v2 import CodexV2Store
from evidence_rag.sources.service import RawSourceService
from evidence_rag.sources.store import RawSourceStore
from evidence_rag.storage import SQLiteStore
from evidence_rag.workspace.store import WorkspaceStore


def _formal_codex_store(tmp_path: Path) -> tuple[SQLiteStore, str]:
    store = SQLiteStore(tmp_path / "formal.sqlite3")
    store.initialize()
    workspace = WorkspaceStore(store)
    workspace.initialize()
    workspace.create_project(
        {
            "id": "project-runtime",
            "name": "Runtime",
            "description": "",
            "owner": "tests",
            "acl_ref": "project:runtime",
            "classification": "internal",
            "status": "active",
            "settings": {},
        }
    )
    RawSourceStore(store).initialize()

    source_id = "codex-source:runtime"
    generation_id = "codex-generation:runtime"
    thread_id = "codex://thread/runtime"
    turn_id = f"{thread_id}/turn/turn-1"
    common = {
        "thread_id": thread_id,
        "turn_id": turn_id,
        "source_id": source_id,
        "generation_id": generation_id,
        "acl_ref": "project:runtime",
        "timestamp": "2026-07-30T00:00:00Z",
    }
    goal_id = f"{turn_id}/item/UserGoal%3Agoal-1"
    command_id = f"{turn_id}/item/CommandExecution%3Acommand-1"
    validation_id = f"{turn_id}/item/ValidationResult%3Avalidation-1"
    items = (
        CodexItemRecord(
            id=goal_id,
            item_id="goal-1",
            sequence=1,
            item_type="UserGoal",
            role="user",
            status="completed",
            name="goal",
            content="Verify the runtime facade with an isolated test.",
            source_locator=f"{goal_id}#event=1",
            metadata={},
            **common,
        ),
        CodexItemRecord(
            id=command_id,
            item_id="command-1",
            sequence=2,
            item_type="CommandExecution",
            role=None,
            status="completed",
            name="pytest",
            content="$ pytest -q tests/test_runtime.py\n1 passed",
            source_locator=f"{command_id}#event=2",
            metadata={
                "call_id": None,
                "exit_code": 0,
                "paths": ["tests/test_runtime.py"],
                "source_event_type": "command_execution",
            },
            **common,
        ),
        CodexItemRecord(
            id=validation_id,
            item_id="validation-1",
            sequence=3,
            item_type="ValidationResult",
            role=None,
            status="passed",
            name="validation",
            content="Validation passed.",
            source_locator=f"{validation_id}#event=3",
            metadata={
                "call_id": None,
                "derived_from_item": "command-1",
                "exit_code": 0,
                "paths": ["tests/test_runtime.py"],
            },
            **common,
        ),
    )
    turn = CodexTurnRecord(
        id=turn_id,
        turn_id="turn-1",
        thread_id=thread_id,
        source_id=source_id,
        generation_id=generation_id,
        ordinal=1,
        status="completed",
        started_at="2026-07-30T00:00:00Z",
        completed_at="2026-07-30T00:01:00Z",
        goal="Verify the runtime facade.",
        summary="The isolated validation passed.",
        metadata={},
    )
    thread = CodexThreadRecord(
        id=thread_id,
        thread_id="runtime-thread",
        source_id=source_id,
        generation_id=generation_id,
        project_id="project-runtime",
        title="Runtime facade",
        cwd=None,
        status="completed",
        started_at="2026-07-30T00:00:00Z",
        updated_at="2026-07-30T00:01:00Z",
        source_file="internal-session.jsonl",
        source_hash="sha256:" + ("1" * 64),
        acl_ref="project:runtime",
        metadata={},
    )
    store.prepare_codex_generation(
        generation_id=generation_id,
        source={
            "id": source_id,
            "project_id": "project-runtime",
            "source_path": str(tmp_path / "sessions"),
            "project_path": str(tmp_path),
            "acl_ref": "project:runtime",
        },
        adapter_version="test-adapter-v1",
    )
    store.publish_codex_generation(
        source_id=source_id,
        generation_id=generation_id,
        threads=(thread,),
        turns=(turn,),
        items=items,
        edges=(),
        views=(),
        counts={"sessions": 1, "turns": 1, "items": 3},
        validation={"status": "passed"},
    )
    return store, generation_id


def test_default_stays_v1_without_reading_v2_source(tmp_path: Path) -> None:
    store, _ = _formal_codex_store(tmp_path)
    runtime = CodexSourceRuntimeV2(store)
    calls = 0

    def legacy() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"engine": "v1"}

    result = runtime.execute(
        engine=None,
        legacy=legacy,
        project_id="not-read",
        allowed_acl_refs=(),
        thread_id="not-read",
        query="not-read",
        task="validation",
    )
    assert result == {"engine": "v1"}
    assert calls == 1


def test_explicit_v2_reads_active_formal_generation_and_full_pipeline(
    tmp_path: Path,
) -> None:
    store, generation_id = _formal_codex_store(tmp_path)
    runtime = CodexSourceRuntimeV2(store)
    result = runtime.execute(
        engine="v2",
        legacy=lambda: pytest.fail("explicit V2 must not execute legacy"),
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        thread_id="runtime-thread",
        query="pytest validation passed",
        task="validation",
        final_k=8,
    )
    assert isinstance(result, CodexSourceRuntimeResultV2)
    assert result.authority.generation_id == generation_id
    assert result.authority.item_count == 3
    assert result.pipeline.retrieval.candidates
    assert result.pipeline.context.blocks
    assert result.pipeline.context.publication_sha256 == (
        result.pipeline.retrieval.publication_sha256
    )
    assert result.pipeline.persisted_dense_entries == 0

    repeated = runtime.query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        thread_id="runtime-thread",
        query="pytest validation passed",
        task="validation",
        expected_generation_id=result.authority.generation_id,
        expected_watermark=result.authority.watermark,
    )
    assert repeated.authority == result.authority


def test_explicit_v2_reuses_published_bundle_and_dense_cache_across_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _ = _formal_codex_store(tmp_path)
    derived = CodexV2Store(
        tmp_path / "derived" / "codex-v2.sqlite3",
        isolated_root=tmp_path,
    )
    derived.initialize()
    runtime = CodexSourceRuntimeV2(store, derived)

    first = runtime.query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        thread_id="runtime-thread",
        query="pytest validation passed",
        task="validation",
        final_k=8,
    )
    first_counts = derived.active_derived_counts(
        project_id=first.authority.project_id,
        source_id=first.authority.source_id,
        generation_id=first.authority.generation_id,
        acl_ref=first.authority.acl_ref,
    )
    assert first.pipeline.persisted_dense_entries > 0
    assert first_counts["bundles"] == 1
    assert first_counts["dense_cache"] == first.pipeline.persisted_dense_entries

    import evidence_rag.rag.sources.codex.runtime_v2 as runtime_module

    monkeypatch.setattr(
        runtime_module,
        "build_codex_thread_pipeline_v2",
        lambda *_args, **_kwargs: pytest.fail("published bundle must be reused"),
    )
    repeated = runtime.query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        thread_id="runtime-thread",
        query="pytest validation passed",
        task="validation",
        final_k=8,
        expected_generation_id=first.authority.generation_id,
        expected_watermark=first.authority.watermark,
    )
    assert repeated.pipeline == first.pipeline
    assert (
        derived.active_derived_counts(
            project_id=first.authority.project_id,
            source_id=first.authority.source_id,
            generation_id=first.authority.generation_id,
            acl_ref=first.authority.acl_ref,
        )
        == first_counts
    )


def test_explicit_v2_filters_authoritative_scope_with_inclusive_dates(
    tmp_path: Path,
) -> None:
    store, _ = _formal_codex_store(tmp_path)
    result = CodexSourceRuntimeV2(store).query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        thread_id="runtime-thread",
        query="validation passed",
        task="validation",
        item_types=("ValidationResult",),
        statuses=("passed",),
        date_from="2026-07-30T08:00:00+08:00",
        date_to="2026-07-30T00:00:00Z",
    )

    assert result.authority.item_count == 1
    assert [item.item_type for item in result.scoped_items] == ["ValidationResult"]
    assert [item.status for item in result.scoped_items] == ["passed"]
    assert {item.timestamp for item in result.scoped_items} == {"2026-07-30T00:00:00Z"}
    assert {
        source_item_id
        for block in result.pipeline.context.blocks
        for source_item_id in block.source_item_ids
    } <= {"validation-1", "ValidationResult:validation-1"}


def test_explicit_v2_status_falls_back_from_item_to_authoritative_turn(
    tmp_path: Path,
) -> None:
    store, _ = _formal_codex_store(tmp_path)
    with store.transaction() as db:
        db.execute(
            """UPDATE codex_items SET status=NULL
               WHERE item_type='CommandExecution'"""
        )

    result = CodexSourceRuntimeV2(store).query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        thread_id="runtime-thread",
        query="pytest",
        task="process",
        item_types=("CommandExecution",),
        statuses=("completed",),
    )

    assert len(result.scoped_items) == 1
    assert result.scoped_items[0].item_type == "CommandExecution"
    assert result.scoped_items[0].status is None
    assert result.scoped_turns[0].status == "completed"

    with store.transaction() as db:
        db.execute(
            """UPDATE codex_items SET status=''
               WHERE item_type='CommandExecution'"""
        )
    with pytest.raises(CodexSourceRuntimeUnavailableV2) as captured:
        CodexSourceRuntimeV2(store).query_v2(
            project_id="project-runtime",
            allowed_acl_refs=("project:runtime",),
            thread_id="runtime-thread",
            query="pytest",
            task="process",
            item_types=("CommandExecution",),
            statuses=("completed",),
        )
    assert captured.value.reason_code == "source_scope_no_match"


@pytest.mark.parametrize(
    "scope",
    [
        {"item_types": ("AgentMessage",)},
        {"statuses": ("failed",)},
        {"date_from": "2026-07-30T00:00:00.000001Z"},
        {"date_to": "2026-07-29T23:59:59.999999Z"},
    ],
)
def test_explicit_v2_scope_with_no_authoritative_match_is_empty(
    tmp_path: Path,
    scope: dict[str, object],
) -> None:
    store, _ = _formal_codex_store(tmp_path)
    with pytest.raises(CodexSourceRuntimeUnavailableV2) as captured:
        CodexSourceRuntimeV2(store).query_v2(
            project_id="project-runtime",
            allowed_acl_refs=("project:runtime",),
            thread_id="runtime-thread",
            query="validation",
            task="validation",
            **scope,
        )
    assert captured.value.reason_code == "source_scope_no_match"


@pytest.mark.parametrize(
    ("scope", "reason"),
    [
        ({"date_from": "2026-07-30T00:00:00"}, "date_from_timezone_required"),
        ({"date_to": "not-a-date"}, "date_to_invalid"),
        (
            {
                "date_from": "2026-07-31T00:00:00Z",
                "date_to": "2026-07-30T00:00:00Z",
            },
            "date_range_invalid",
        ),
        ({"item_types": (" ValidationResult",)}, "item_types_invalid"),
        ({"statuses": ("passed ",)}, "statuses_invalid"),
    ],
)
def test_explicit_v2_invalid_scope_fails_before_formal_source_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: dict[str, object],
    reason: str,
) -> None:
    store, _ = _formal_codex_store(tmp_path)
    monkeypatch.setattr(
        store,
        "get_codex_thread",
        lambda *_args, **_kwargs: pytest.fail("invalid scope must fail before source I/O"),
    )

    with pytest.raises(CodexSourceScopeErrorV2) as captured:
        CodexSourceRuntimeV2(store).query_v2(
            project_id="project-runtime",
            allowed_acl_refs=("project:runtime",),
            thread_id="runtime-thread",
            query="validation",
            task="validation",
            **scope,
        )
    assert str(captured.value) == reason


def test_explicit_v2_rejects_naive_authoritative_item_timestamp(
    tmp_path: Path,
) -> None:
    store, _ = _formal_codex_store(tmp_path)
    with store.transaction() as db:
        db.execute(
            """UPDATE codex_items SET timestamp='2026-07-30T00:00:00'
               WHERE item_type='ValidationResult'"""
        )

    with pytest.raises(CodexSourceScopeErrorV2) as captured:
        CodexSourceRuntimeV2(store).query_v2(
            project_id="project-runtime",
            allowed_acl_refs=("project:runtime",),
            thread_id="runtime-thread",
            query="validation",
            task="validation",
            item_types=("ValidationResult",),
            date_from="2026-07-29T00:00:00Z",
        )
    assert str(captured.value) == "source_item_timestamp_timezone_required"


@pytest.mark.parametrize(
    ("allowed", "expected_watermark", "reason"),
    [
        ((), None, "project_acl_denied"),
        (("project:runtime",), "sha256:" + ("0" * 64), "expected_watermark_mismatch"),
    ],
)
def test_explicit_v2_fails_closed_for_acl_or_watermark(
    tmp_path: Path,
    allowed: tuple[str, ...],
    expected_watermark: str | None,
    reason: str,
) -> None:
    store, _ = _formal_codex_store(tmp_path)
    runtime = CodexSourceRuntimeV2(store)
    legacy_calls = 0

    def legacy() -> None:
        nonlocal legacy_calls
        legacy_calls += 1

    with pytest.raises(CodexSourceRuntimeUnavailableV2) as captured:
        runtime.execute(
            engine="v2",
            legacy=legacy,
            project_id="project-runtime",
            allowed_acl_refs=allowed,
            thread_id="runtime-thread",
            query="validation",
            task="validation",
            expected_watermark=expected_watermark,
        )
    assert captured.value.reason_code == reason
    assert legacy_calls == 0


@pytest.mark.parametrize(
    ("mutation", "allowed_acl_refs", "reason"),
    [
        (
            "UPDATE codex_sources SET status='deleted' WHERE id='codex-source:runtime'",
            ("project:runtime",),
            "source_not_ready",
        ),
        (
            """UPDATE codex_sources SET acl_ref='project:changed'
               WHERE id='codex-source:runtime'""",
            ("project:runtime", "project:changed"),
            "active_generation_mismatch",
        ),
        (
            """UPDATE codex_sources SET active_generation_id='codex-generation:missing'
               WHERE id='codex-source:runtime'""",
            ("project:runtime",),
            "thread_not_found",
        ),
    ],
)
def test_cached_v2_fails_closed_and_invalidates_deleted_acl_or_generation_scope(
    tmp_path: Path,
    mutation: str,
    allowed_acl_refs: tuple[str, ...],
    reason: str,
) -> None:
    store, _ = _formal_codex_store(tmp_path)
    derived = CodexV2Store(
        tmp_path / "derived" / "codex-v2.sqlite3",
        isolated_root=tmp_path,
    )
    derived.initialize()
    runtime = CodexSourceRuntimeV2(store, derived)
    first = runtime.query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        thread_id="runtime-thread",
        query="validation",
        task="validation",
    )
    with store.transaction() as db:
        db.execute(mutation)

    with pytest.raises(CodexSourceRuntimeUnavailableV2) as captured:
        runtime.query_v2(
            project_id="project-runtime",
            allowed_acl_refs=allowed_acl_refs,
            thread_id="runtime-thread",
            query="validation",
            task="validation",
        )
    assert captured.value.reason_code == reason
    assert not any(
        derived.active_derived_counts(
            project_id=first.authority.project_id,
            source_id=first.authority.source_id,
            generation_id=first.authority.generation_id,
            acl_ref=first.authority.acl_ref,
        ).values()
    )


def test_explicit_v2_rejects_tombstoned_formal_items(tmp_path: Path) -> None:
    store, _ = _formal_codex_store(tmp_path)
    derived = CodexV2Store(
        tmp_path / "derived" / "codex-v2.sqlite3",
        isolated_root=tmp_path,
    )
    derived.initialize()
    runtime = CodexSourceRuntimeV2(store, derived)
    published = runtime.query_v2(
        project_id="project-runtime",
        allowed_acl_refs=("project:runtime",),
        thread_id="runtime-thread",
        query="validation",
        task="validation",
    )
    raw_store = RawSourceStore(store)
    raw_service = RawSourceService(raw_store, tmp_path / "raw")
    with store.connection() as db:
        item_id = str(
            db.execute("SELECT id FROM codex_items ORDER BY sequence LIMIT 1").fetchone()[0]
        )
    raw = raw_service.persist_bytes(
        project_id="project-runtime",
        source_type="codex",
        source_instance="runtime",
        source_object_id="runtime-thread",
        source_version="v1",
        payload=b"observable",
        source_uri=None,
        media_type="text/plain",
        acl_ref="project:runtime",
        adapter_version="test",
        schema_version="test",
    )
    raw_store.link_derivations(
        raw["id"],
        [item_id],
        kind="codex_normalization",
        generation_id="codex-generation:runtime",
        derivation_version="test",
    )
    raw_store.tombstone(raw["id"], "retention", "tests")

    with pytest.raises(CodexSourceRuntimeUnavailableV2) as captured:
        runtime.query_v2(
            project_id="project-runtime",
            allowed_acl_refs=("project:runtime",),
            thread_id="runtime-thread",
            query="validation",
            task="validation",
        )
    assert captured.value.reason_code == "source_contains_tombstoned_items"
    assert not any(
        derived.active_derived_counts(
            project_id=published.authority.project_id,
            source_id=published.authority.source_id,
            generation_id=published.authority.generation_id,
            acl_ref=published.authority.acl_ref,
        ).values()
    )
