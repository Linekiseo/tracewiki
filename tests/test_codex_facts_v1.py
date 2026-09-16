from __future__ import annotations

import hashlib
import importlib
import json
import sqlite3
import types
from collections.abc import Iterable
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.codex import facts_v1 as facts_module
from evidence_rag.rag.sources.codex.contracts import (
    ActionState,
    CodexEventNormalizationResult,
    EventScope,
    NormalizedActionEvent,
    NormalizedValidationEvent,
    ObservableCodexItem,
    ValidationState,
    canonical_codex_locator_v1,
    canonical_sha256,
)
from evidence_rag.rag.sources.codex.event_normalizer import normalize_codex_events_v1
from evidence_rag.rag.sources.codex.facts_v1 import (
    CODEX_DERIVED_FACT_SCHEMA_VERSION,
    CODEX_NORMALIZER_CODE_SHA256,
    CODEX_NORMALIZER_MODULE,
    CODEX_NORMALIZER_SOURCE_SHA256,
    CodexEndpointKind,
    CodexEventPredicate,
    CodexFactPublication,
    CodexFactPublicationDisposition,
    CodexFactPublicationError,
    CodexFactPublicationScope,
    CodexFactScopeError,
    CodexFactValidationError,
    CodexOutputRole,
    SQLiteCodexFactStore,
    build_codex_fact_publication_v1,
    build_codex_output_window_v1,
)


def _event_scope(
    *,
    project: str = "project-a",
    source: str = "codex-source:a",
    generation: str = "generation-a",
    thread: str = "thread-a",
    turn: str = "turn-a",
    acl: str = "project:project-a",
) -> EventScope:
    return EventScope(
        project_id=project,
        source_id=source,
        generation_id=generation,
        thread_id=thread,
        turn_id=turn,
        acl_ref=acl,
    )


def _publication_scope(
    *,
    project: str = "project-a",
    repository: str = "repository-a",
    source: str = "codex-source:a",
    source_version: str = "codex-source-v1",
    generation: str = "generation-a",
    thread: str = "thread-a",
    acl: str = "project:project-a",
) -> CodexFactPublicationScope:
    return CodexFactPublicationScope(
        project_id=project,
        repository_id=repository,
        source_id=source,
        source_version=source_version,
        generation_id=generation,
        thread_id=thread,
        acl_ref=acl,
    )


def _item(
    item_id: str,
    order: int,
    item_type: str,
    *,
    scope: EventScope | None = None,
    **values: object,
) -> ObservableCodexItem:
    exact_scope = scope or _event_scope()
    return ObservableCodexItem(
        item_id=item_id,
        source_locator=canonical_codex_locator_v1(exact_scope, item_id, order),
        scope=exact_scope,
        observable_order=order,
        item_type=item_type,
        **values,
    )


def _command_result(
    *,
    scope: EventScope | None = None,
    call_id: str = "call-a",
    exit_code: int = 0,
    outcome: str | None = None,
    raw_outputs: dict[str, str] | None = None,
) -> tuple[ObservableCodexItem, ObservableCodexItem]:
    exact_scope = scope or _event_scope()
    command = _item(
        "command",
        0,
        "CommandExecution",
        scope=exact_scope,
        call_id=call_id,
        call_type="command",
        command_argv=("pytest", "tests/test_alpha.py"),
        targets=("tests/test_alpha.py",),
    )
    values: dict[str, object] = {
        "call_id": call_id,
        "call_type": "command",
        "payload": {
            "execution": {"exit_code": exit_code},
            **(raw_outputs or {}),
        },
    }
    if outcome is not None:
        values["outcome"] = outcome
    result = _item(
        "result",
        1,
        "CommandResult",
        scope=exact_scope,
        **values,
    )
    return command, result


def _normalization(
    records: Iterable[ObservableCodexItem] | None = None,
) -> CodexEventNormalizationResult:
    return normalize_codex_events_v1(records or _command_result())


def _publication(
    *,
    scope: CodexFactPublicationScope | None = None,
    sources: tuple[ObservableCodexItem, ...] | None = None,
    expected_normalization: CodexEventNormalizationResult | dict[str, object] | None = None,
) -> CodexFactPublication:
    return build_codex_fact_publication_v1(
        scope or _publication_scope(),
        sources if sources is not None else _command_result(),
        expected_normalization=expected_normalization,
    )


def _normalization_content(payload: dict[str, object]) -> dict[str, object]:
    return {
        "contract_version": payload["contract_version"],
        "normalizer_version": payload["normalizer_version"],
        "state_machine_version": payload["state_machine_version"],
        "events": payload["events"],
        "links": payload["links"],
        "diagnostics": payload["diagnostics"],
    }


def test_output_window_long_success_has_exact_bounded_head_tail_metadata() -> None:
    content = "\n".join(f"success line {index:03d}" for index in range(100))
    window = build_codex_output_window_v1(
        content,
        max_chars=80,
        role=CodexOutputRole.STDOUT,
        exit_code=0,
    )

    assert window.truncated is True
    assert window.error_tail is False
    assert window.retained_chars == len(window.head) + len(window.tail)
    assert window.retained_chars <= 80
    assert window.omitted_chars == len(content) - window.retained_chars
    assert window.total_chars == len(content)
    assert window.total_utf8_bytes == len(content.encode())
    assert window.content_sha256 == "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    assert window.head.endswith("\n")
    assert window.tail.startswith("success line")


def test_output_window_long_error_reserves_tail_and_keeps_failure() -> None:
    content = "setup\n" + ("progress\n" * 100) + "Traceback:\nFAILURE: database exploded"
    window = build_codex_output_window_v1(
        content,
        max_chars=96,
        role="traceback",
        outcome="failed",
    )

    assert window.error_tail is True
    assert len(window.tail) > len(window.head)
    assert "FAILURE: database exploded" in window.tail
    assert "Traceback:" in window.tail
    assert len(window.head) + len(window.tail) <= window.max_chars


@pytest.mark.parametrize(
    "content",
    [
        "单行成功🙂αβγ" * 20,
        "第一行🙂\n第二行λ\n第三行：失败🔥" * 15,
    ],
)
def test_output_window_unicode_single_and_multiline_are_utf8_safe(content: str) -> None:
    window = build_codex_output_window_v1(content, max_chars=31, error_observed=True)

    assert window.total_chars == len(content)
    assert window.total_utf8_bytes == len(content.encode("utf-8"))
    assert window.retained_chars + window.omitted_chars == len(content)
    window.head.encode("utf-8")
    window.tail.encode("utf-8")


@pytest.mark.parametrize("budget", [1, 2, 3, 4])
def test_output_window_tiny_budget_is_deterministic_and_bounded(budget: int) -> None:
    first = build_codex_output_window_v1(
        "abcdef\nFAIL",
        max_chars=budget,
        exit_code=2,
    )
    second = build_codex_output_window_v1(
        "abcdef\nFAIL",
        max_chars=budget,
        exit_code=2,
    )

    assert first == second
    assert first.retained_chars <= budget
    assert first.tail
    assert first.omitted_chars == first.total_chars - first.retained_chars


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "access_token=abcdefghijklmnopqrstuvwxyz123456",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
    ],
)
def test_output_window_rejects_raw_secret_even_if_caller_claims_redaction(secret: str) -> None:
    with pytest.raises(CodexFactValidationError, match="secret"):
        build_codex_output_window_v1(
            f"prefix {secret} suffix",
            max_chars=32,
            redacted=True,
        )

    redacted = build_codex_output_window_v1(
        "prefix [REDACTED] suffix",
        redacted=True,
    )
    assert redacted.redacted is True
    assert redacted.sanitized is True


@pytest.mark.parametrize(
    "unsafe_output",
    [
        pytest.param(r"FAIL \\server\share\repo\file.py", id="raw-unc"),
        pytest.param("FAIL //server/share/repo/file.py", id="slash-share"),
        pytest.param(
            "FAIL %5C%5Cserver%5Cshare%5Crepo%5Cfile.py",
            id="encoded-unc",
        ),
    ],
)
def test_gate_network_share_reproductions_fail_closed_before_publication(
    unsafe_output: str,
) -> None:
    sources = _command_result(
        exit_code=7,
        raw_outputs={"stderr": unsafe_output},
    )

    with pytest.raises(CodexFactValidationError, match="absolute path") as error:
        _publication(sources=sources)

    assert unsafe_output not in str(error.value)


@pytest.mark.parametrize(
    "unsafe_output",
    [
        pytest.param(r"FAIL \\?\UNC\server\share\repo", id="extended-unc"),
        pytest.param(r"FAIL \\.\pipe\codex", id="device-path"),
        pytest.param(r"FAIL \/server\share/repo", id="mixed-separators"),
        pytest.param("FAIL %5c%5CServer%5cShare%5Crepo", id="mixed-case-encoding"),
        pytest.param(
            "FAIL %255C%255Cserver%255Cshare%255Crepo",
            id="double-encoded",
        ),
        pytest.param("FAIL %2F%2Fserver%2Fshare%2Frepo", id="encoded-slashes"),
        pytest.param("FAIL ＼＼server＼share＼repo", id="unicode-normalization"),
    ],
)
def test_output_window_rejects_network_share_variants(unsafe_output: str) -> None:
    with pytest.raises(CodexFactValidationError, match="absolute path") as error:
        build_codex_output_window_v1(
            unsafe_output,
            max_chars=16,
            exit_code=7,
        )

    assert unsafe_output not in str(error.value)


@pytest.mark.parametrize(
    "content",
    [
        "request https://server.example/share/build.log completed",
        "request https%3A%2F%2Fserver.example%2Fshare%2Fbuild.log completed",
        "integer division: 10 // 2",
        "ordinary // comment text",
        "paired slashes in token abc//def",
    ],
)
def test_output_window_network_share_scanner_preserves_non_paths(content: str) -> None:
    window = build_codex_output_window_v1(content, max_chars=256)

    assert window.head == content
    assert window.tail == ""
    assert window.truncated is False


def test_output_window_rejects_network_share_in_omitted_middle_before_truncation() -> None:
    content = (
        "HEAD\n"
        + ("A" * 2_048)
        + r"\\server\share\private.py"
        + ("B" * 2_048)
        + "\nFAIL: exit seven"
    )

    with pytest.raises(CodexFactValidationError, match="absolute path") as error:
        build_codex_output_window_v1(
            content,
            max_chars=64,
            exit_code=7,
        )

    assert "server" not in str(error.value)
    assert "share" not in str(error.value)


def test_output_window_rejects_network_share_in_error_tail() -> None:
    content = ("safe prefix\n" * 100) + r"FAIL \\server\share\private.py"

    with pytest.raises(CodexFactValidationError, match="absolute path") as error:
        build_codex_output_window_v1(
            content,
            max_chars=64,
            exit_code=7,
        )

    assert "server" not in str(error.value)
    assert "share" not in str(error.value)


def test_output_window_cannot_replace_structured_failure_truth() -> None:
    sources = _command_result(
        exit_code=9,
        raw_outputs={"output": "All tests passed according to prose."},
    )
    publication = _publication(sources=sources)

    action = next(fact for fact in publication.facts if fact.kind.value == "action")
    validation = next(fact for fact in publication.facts if fact.kind.value == "validation")
    assert action.state == ActionState.FAILED.value
    assert action.exit_code == 9
    assert validation.state == ValidationState.FAILED.value
    assert validation.exit_code == 9
    assert action.output_windows[0].head.startswith("All tests passed")


def test_fact_builder_requires_exact_contract_digest_and_strict_structure() -> None:
    sources = _command_result()
    normalization = _normalization(sources)
    payload = normalization.model_dump(mode="json")
    payload["summary"]["content_sha256"] = "sha256:" + ("0" * 64)
    with pytest.raises(CodexFactValidationError, match="digest"):
        build_codex_fact_publication_v1(
            _publication_scope(),
            sources,
            expected_normalization=payload,
        )

    payload = normalization.model_dump(mode="json")
    payload["caller_state"] = "passed"
    with pytest.raises(CodexFactValidationError, match="exact frozen"):
        build_codex_fact_publication_v1(
            _publication_scope(),
            sources,
            expected_normalization=payload,
        )

    payload = normalization.model_dump(mode="json")
    action = next(event for event in payload["events"] if event["kind"] == "action")
    action["reason_code"] = "result_failure_observed"
    payload["summary"]["content_sha256"] = canonical_sha256(_normalization_content(payload))
    with pytest.raises(CodexFactValidationError, match="truth shape"):
        build_codex_fact_publication_v1(
            _publication_scope(),
            sources,
            expected_normalization=payload,
        )


def test_gate_self_consistent_completed_passed_forgery_fails_against_failed_source() -> None:
    failed_sources = _command_result(exit_code=7)
    forged_sources = _command_result(exit_code=0)
    forged = _normalization(forged_sources)
    assert any(
        isinstance(event, NormalizedActionEvent)
        and event.state is ActionState.COMPLETED
        and event.exit_code == 0
        for event in forged.events
    )
    assert any(
        isinstance(event, NormalizedValidationEvent)
        and event.state is ValidationState.PASSED
        and event.exit_code == 0
        for event in forged.events
    )

    with pytest.raises(
        CodexFactValidationError,
        match="not exact canonical source reconstruction",
    ):
        build_codex_fact_publication_v1(
            _publication_scope(),
            failed_sources,
            expected_normalization=forged,
        )


def test_subclassed_normalization_result_is_rejected() -> None:
    class FakeNormalizationResult(CodexEventNormalizationResult):
        pass

    sources = _command_result()
    fake = FakeNormalizationResult.model_validate(_normalization(sources).model_dump(mode="json"))
    with pytest.raises(CodexFactValidationError, match="subclassed"):
        build_codex_fact_publication_v1(
            _publication_scope(),
            sources,
            expected_normalization=fake,
        )


@pytest.mark.parametrize("patched_export", ["module", "package"])
def test_fake_normalizer_module_or_public_export_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    patched_export: str,
) -> None:
    module = importlib.import_module(CODEX_NORMALIZER_MODULE)
    package = importlib.import_module(CODEX_NORMALIZER_MODULE.rsplit(".", 1)[0])
    original = module.normalize_codex_events_v1

    def wrapper(records: Iterable[ObservableCodexItem]) -> CodexEventNormalizationResult:
        return original(records)

    monkeypatch.setattr(
        module if patched_export == "module" else package,
        "normalize_codex_events_v1",
        wrapper,
    )
    with pytest.raises(CodexFactValidationError, match="module/export identity"):
        _publication()


def test_normalizer_source_code_and_manifest_identity_are_hard_bound() -> None:
    publication = _publication()
    identity = publication.source_authority.normalizer

    assert identity.module == CODEX_NORMALIZER_MODULE
    assert identity.source_sha256 == CODEX_NORMALIZER_SOURCE_SHA256
    assert identity.code_sha256 == CODEX_NORMALIZER_CODE_SHA256
    assert publication.source_set_sha256 == publication.source_authority.source_set_sha256
    assert all(
        fact.source_set_sha256 == publication.source_set_sha256 for fact in publication.facts
    )


def test_normalizer_code_identity_is_independent_of_checkout_path() -> None:
    original = normalize_codex_events_v1
    relocated_code = original.__code__.replace(co_filename="/different/checkout/event_normalizer.py")
    relocated = types.FunctionType(
        relocated_code,
        original.__globals__,
        original.__name__,
        original.__defaults__,
        original.__closure__,
    )
    relocated.__kwdefaults__ = original.__kwdefaults__

    assert facts_module._function_code_digest(original) == CODEX_NORMALIZER_CODE_SHA256
    assert facts_module._function_code_digest(relocated) == CODEX_NORMALIZER_CODE_SHA256


def test_monkeypatched_normalizer_dependency_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(CODEX_NORMALIZER_MODULE)
    original = module._outcome_from_result

    def fake_outcome(*args: object, **kwargs: object) -> object:
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_outcome_from_result", fake_outcome)
    with pytest.raises(CodexFactValidationError, match="binding changed"):
        _publication()


def test_fact_builder_is_order_duplicate_deterministic_and_scope_addressed() -> None:
    command, result = _command_result()
    first = _publication(sources=(command, result))
    second = _publication(sources=(result, command, result, command))

    assert first == second
    assert first.publication_id == second.publication_id
    assert first.content_sha256 == second.content_sha256
    assert tuple(fact.fact_id for fact in first.facts) == tuple(
        fact.fact_id for fact in second.facts
    )

    other_generation = _publication(
        scope=_publication_scope(generation="generation-b"),
        sources=_command_result(
            scope=_event_scope(generation="generation-b"),
        ),
    )
    other_acl = _publication(
        scope=_publication_scope(acl="project:restricted"),
        sources=_command_result(
            scope=_event_scope(acl="project:restricted"),
        ),
    )
    assert {fact.fact_id for fact in first.facts}.isdisjoint(
        fact.fact_id for fact in other_generation.facts
    )
    assert {fact.fact_id for fact in first.facts}.isdisjoint(
        fact.fact_id for fact in other_acl.facts
    )


def test_source_delete_modify_and_subclass_fail_while_reorder_is_canonical(
    tmp_path: Path,
) -> None:
    sources = _command_result(
        exit_code=7,
        raw_outputs={"stderr": "line\nFAILURE seven"},
    )
    publication = _publication(sources=sources)
    store = SQLiteCodexFactStore(tmp_path / "source-authority.sqlite3")
    store.publish(publication, source_items=sources)

    with pytest.raises(CodexFactValidationError):
        store.publish(publication, source_items=(sources[0],))

    modified_result = sources[1].model_copy(
        update={
            "payload": {
                "execution": {"exit_code": 7},
                "stderr": "line\nDIFFERENT failure",
            }
        }
    )
    with pytest.raises(CodexFactValidationError, match="authoritative source"):
        store.publish(publication, source_items=(sources[0], modified_result))

    replay = store.publish(publication, source_items=reversed(sources))
    assert replay.disposition is CodexFactPublicationDisposition.REPLAYED

    class FakeObservableCodexItem(ObservableCodexItem):
        pass

    fake = FakeObservableCodexItem.model_validate(sources[0].model_dump(mode="json"))
    with pytest.raises(CodexFactValidationError, match="exact frozen"):
        build_codex_fact_publication_v1(
            _publication_scope(),
            (fake, sources[1]),
        )
    store.close()


def test_authoritative_raw_output_recomputes_complete_window_and_is_not_persisted_raw() -> None:
    middle = "UNBOUNDED-MIDDLE-CONTENT-" * 400
    content = "begin🙂\n" + middle + "\nTraceback:\nFAILURE: exit seven"
    sources = _command_result(
        exit_code=7,
        raw_outputs={"stderr": content},
    )
    publication = _publication(sources=sources)
    located = publication.output_windows[0]
    expected = build_codex_output_window_v1(
        content,
        max_chars=4_096,
        role="stderr",
        error_observed=True,
    )

    assert located.window == expected
    assert located.source_item_id == sources[1].item_id
    assert located.source_locator == sources[1].source_locator
    assert located.source_scope == sources[1].scope
    assert located.source_item_sha256 == sources[1].canonical_sha256()
    assert publication.diagnostics.authoritative_result_count == 1
    assert publication.diagnostics.output_window_count == 1
    assert publication.diagnostics.missing_raw_output_count == 0
    dumped = json.dumps(publication.model_dump(mode="json"), ensure_ascii=False)
    assert middle not in dumped


@pytest.mark.parametrize(
    ("fake_output", "fake_exit"),
    [
        ("A" * 5_000 + "FORGED-TAIL", 7),
        ("short forged output", 7),
        ("A" * 5_000 + "FAILURE: exit seven", 0),
    ],
)
def test_self_consistent_fake_window_hash_tail_total_or_error_flag_fails_source_rebuild(
    tmp_path: Path,
    fake_output: str,
    fake_exit: int,
) -> None:
    original_content = "A" * 5_000 + "FAILURE: exit seven"
    original_sources = _command_result(
        exit_code=7,
        raw_outputs={"stderr": original_content},
    )
    fake_sources = _command_result(
        exit_code=fake_exit,
        raw_outputs={"stderr": fake_output},
    )
    fake_publication = _publication(sources=fake_sources)
    store = SQLiteCodexFactStore(tmp_path / f"fake-window-{fake_exit}.sqlite3")

    with pytest.raises(CodexFactValidationError):
        store.publish(fake_publication, source_items=original_sources)
    assert store.publication_count() == 0
    assert store.fact_count() == 0
    assert store.link_count() == 0
    store.close()


@pytest.mark.parametrize(
    "unsafe_output",
    [
        "trace /Users/alice/repository/main.py:10",
        "trace /opt/workspace/repository/main.py:10",
        r"trace C:\Users\alice\repository\main.py:10",
        "trace %2FUsers%2Falice%2Frepository%2Fmain.py",
        "trace C%3A%5CUsers%5Calice%5Crepository%5Cmain.py",
        "token sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
    ],
)
def test_authoritative_raw_output_rejects_secret_and_raw_or_encoded_absolute_path(
    unsafe_output: str,
) -> None:
    sources = _command_result(
        exit_code=7,
        raw_outputs={"stderr": unsafe_output},
    )
    with pytest.raises(CodexFactValidationError, match="secret|absolute path"):
        _publication(sources=sources)


def test_missing_authoritative_raw_output_emits_no_window_and_honest_diagnostic() -> None:
    sources = _command_result(exit_code=7)
    publication = _publication(sources=sources)

    assert publication.output_windows == ()
    assert publication.diagnostics.authoritative_result_count == 1
    assert publication.diagnostics.output_window_count == 0
    assert publication.diagnostics.missing_raw_output_count == 1
    assert all(not fact.output_windows for fact in publication.facts)


def test_duplicate_fact_content_deduplicates_unit_but_preserves_raw_event_identity() -> None:
    turn_a = _event_scope(turn="turn-a")
    turn_b = _event_scope(turn="turn-b")
    records = (
        _item("proposal-a", 0, "ActionProposal", scope=turn_a),
        _item("proposal-b", 0, "ActionProposal", scope=turn_b),
    )
    normalization = _normalization(records)
    publication = _publication(sources=records)

    assert len(normalization.events) == 2
    assert len(publication.facts) == 1
    assert publication.diagnostics.deduplicated_fact_count == 1
    derived_from = [
        link for link in publication.links if link.predicate is CodexEventPredicate.DERIVED_FROM
    ]
    contains = [
        link for link in publication.links if link.predicate is CodexEventPredicate.CONTAINS
    ]
    assert len(derived_from) == 2
    assert len({link.target.source_event_id for link in derived_from}) == 2
    assert len(contains) == 2
    assert {link.target.source_item_id for link in contains} == {"proposal-a", "proposal-b"}
    assert all(link.target.source_locator for link in contains)


def test_reasoning_system_developer_and_token_count_never_generate_facts() -> None:
    records = (
        _item("reasoning", 0, "reasoning", payload={"text": "hidden"}),
        _item("agent-reasoning", 1, "agent_reasoning", payload={"text": "hidden"}),
        _item("tokens", 2, "token_count", payload={"count": 10}),
        _item("system", 3, "SystemMessage", role="system", payload={"text": "hidden"}),
        _item(
            "developer",
            4,
            "DeveloperMessage",
            role="developer",
            payload={"text": "hidden"},
        ),
        _item("proposal", 5, "ActionProposal"),
    )
    publication = _publication(sources=records)

    assert len(publication.facts) == 1
    assert publication.facts[0].role.value == "action_proposal"
    assert publication.diagnostics.excluded_input_count == 5
    dumped = json.dumps(publication.model_dump(mode="json"), sort_keys=True)
    assert "hidden" not in dumped


def test_weak_observations_remain_weak_and_are_never_upgraded() -> None:
    records = (
        _item("plan", 0, "Plan", role="assistant", payload={"exit_code": 0}),
        _item(
            "claim",
            1,
            "AgentMessage",
            role="assistant",
            payload={"status": "passed"},
            success=True,
        ),
        _item("mention", 2, "ValidationMention", role="assistant"),
    )
    publication = _publication(sources=records)

    assert publication.diagnostics.weak_observation_count == 2
    observations = [fact for fact in publication.facts if fact.kind.value == "observation"]
    assert len(observations) == 2
    assert all(fact.state is None for fact in observations)
    validation = next(fact for fact in publication.facts if fact.kind.value == "validation")
    assert validation.state == ValidationState.MENTIONED.value
    assert not any(fact.state == ValidationState.PASSED.value for fact in publication.facts)


def test_builder_rejects_cross_project_generation_acl_and_output_locator() -> None:
    sources = _command_result()
    for scope in (
        _publication_scope(project="project-b"),
        _publication_scope(generation="generation-b"),
        _publication_scope(acl="project:restricted"),
        _publication_scope(source="codex-source:b"),
        _publication_scope(thread="thread-b"),
    ):
        with pytest.raises(CodexFactScopeError):
            build_codex_fact_publication_v1(scope, sources)

    with pytest.raises(TypeError, match="output_windows"):
        build_codex_fact_publication_v1(  # type: ignore[call-arg]
            _publication_scope(),
            sources,
            output_windows=(),
        )


def test_tampered_orphan_bad_locator_and_unknown_predicate_fail_closed() -> None:
    sources = _command_result()
    normalization = _normalization(sources)
    orphan = normalization.model_dump(mode="json")
    orphan["links"][0]["result_item_id"] = "orphan"
    orphan["links"][0]["result_locator"] = canonical_codex_locator_v1(
        _event_scope(),
        "orphan",
        1,
    )
    orphan_link = orphan["links"][0]
    orphan_link["link_id"] = canonical_sha256(
        {
            "contract_version": orphan["contract_version"],
            "normalizer_version": orphan["normalizer_version"],
            "role": orphan_link["role"],
            "scope": orphan_link["scope"],
            "call_id": orphan_link["call_id"],
            "call_type": orphan_link["call_type"],
            "call_item_id": orphan_link["call_item_id"],
            "call_locator": orphan_link["call_locator"],
            "result_item_id": orphan_link["result_item_id"],
            "result_locator": orphan_link["result_locator"],
        }
    )
    orphan["summary"]["content_sha256"] = canonical_sha256(_normalization_content(orphan))
    with pytest.raises(CodexFactValidationError, match="orphan"):
        build_codex_fact_publication_v1(
            _publication_scope(),
            sources,
            expected_normalization=orphan,
        )

    bad_locator = normalization.model_dump(mode="json")
    bad_locator["events"][0]["evidence"][0]["source_locator"] = (
        "codex://thread/thread-a/turn/turn-a/item/other#event=1"
    )
    bad_locator["summary"]["content_sha256"] = canonical_sha256(_normalization_content(bad_locator))
    with pytest.raises((CodexFactValidationError, CodexFactScopeError)):
        build_codex_fact_publication_v1(
            _publication_scope(),
            sources,
            expected_normalization=bad_locator,
        )

    publication = _publication()
    unknown = publication.model_dump(mode="json")
    unknown["links"][0]["predicate"] = "x2_invented_edge"
    with SQLiteCodexFactStore(":memory:") as store:
        with pytest.raises(CodexFactValidationError):
            store.publish(unknown, source_items=sources)
        assert store.publication_count() == 0
        assert store.fact_count() == 0
        assert store.link_count() == 0
        assert store.staged_count() == 0


def test_sqlite_schema_is_additive_on_empty_and_v1_like_temp_db(tmp_path: Path) -> None:
    database_path = tmp_path / "v1-like.sqlite3"
    with sqlite3.connect(database_path) as database:
        database.executescript(
            """
            CREATE TABLE codex_items (
                item_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            INSERT INTO codex_items(item_id, payload) VALUES ('raw-1', 'keep-me');
            CREATE TRIGGER codex_items_immutable
            BEFORE UPDATE ON codex_items BEGIN SELECT RAISE(ABORT, 'immutable'); END;
            """
        )
        before_sql = tuple(
            database.execute(
                """SELECT type, name, sql FROM sqlite_master
                   WHERE name IN ('codex_items', 'codex_items_immutable')
                   ORDER BY type, name"""
            )
        )
        before_rows = tuple(database.execute("SELECT * FROM codex_items"))

    store = SQLiteCodexFactStore(database_path)
    store.close()

    with sqlite3.connect(database_path) as database:
        after_sql = tuple(
            database.execute(
                """SELECT type, name, sql FROM sqlite_master
                   WHERE name IN ('codex_items', 'codex_items_immutable')
                   ORDER BY type, name"""
            )
        )
        after_rows = tuple(database.execute("SELECT * FROM codex_items"))
        added = {
            row[0]
            for row in database.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name LIKE 'codex_%'"""
            )
        }
        schema_version = database.execute(
            """SELECT metadata_value FROM codex_derived_metadata
               WHERE metadata_key='schema_version'"""
        ).fetchone()[0]
    assert after_sql == before_sql
    assert after_rows == before_rows
    assert {
        "codex_derived_facts",
        "codex_event_links",
        "codex_derived_publications",
        "codex_active_generations",
        "codex_derived_staging",
    }.issubset(added)
    assert schema_version == CODEX_DERIVED_FACT_SCHEMA_VERSION


def test_sqlite_publish_read_exact_scope_and_idempotent_replay(tmp_path: Path) -> None:
    database_path = tmp_path / "facts.sqlite3"
    sources = _command_result()
    publication = _publication(sources=sources)
    store = SQLiteCodexFactStore(database_path)

    first = store.publish(publication, source_items=sources)
    second = store.publish(publication.model_dump(mode="json"), source_items=reversed(sources))

    assert first.disposition is CodexFactPublicationDisposition.PUBLISHED
    assert second.disposition is CodexFactPublicationDisposition.REPLAYED
    assert first.publication.content_sha256 == second.publication.content_sha256
    assert store.publication_count() == 1
    assert store.fact_count() == len(publication.facts)
    assert store.link_count() == len(publication.links)
    assert store.staged_count() == 0
    assert store.get_active(publication.scope) == publication
    assert (
        store.get_publication(
            publication.scope,
            publication_id=publication.publication_id,
        )
        == publication
    )
    wrong_acl = publication.scope.model_copy(update={"acl_ref": "project:restricted"})
    assert store.get_active(wrong_acl) is None
    assert (
        store.get_publication(
            wrong_acl,
            publication_id=publication.publication_id,
        )
        is None
    )
    store.close()

    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()


def test_sqlite_active_generation_switch_and_stale_replay_do_not_roll_back(
    tmp_path: Path,
) -> None:
    store = SQLiteCodexFactStore(tmp_path / "switch.sqlite3")
    sources_a = _command_result()
    generation_a = _publication(sources=sources_a)
    event_scope_b = _event_scope(generation="generation-b")
    sources_b = _command_result(scope=event_scope_b, exit_code=3)
    generation_b = _publication(
        scope=_publication_scope(generation="generation-b"),
        sources=sources_b,
    )

    first = store.publish(generation_a, source_items=sources_a)
    second = store.publish(generation_b, source_items=sources_b)
    stale = store.publish(generation_a, source_items=reversed(sources_a))

    assert first.previous_active_generation_id is None
    assert second.previous_active_generation_id == "generation-a"
    assert store.active_generation(generation_b.scope) == "generation-b"
    assert store.get_active(generation_a.scope) is None
    assert store.get_active(generation_b.scope) == generation_b
    assert stale.disposition is CodexFactPublicationDisposition.REPLAYED
    assert stale.active_generation_id == "generation-b"
    assert stale.active_publication_id == generation_b.publication_id
    assert store.active_generation(generation_a.scope) == "generation-b"
    store.close()


@pytest.mark.parametrize(
    "failure_stage",
    ["after_stage", "after_validate", "after_facts", "before_activate", "after_activate"],
)
def test_sqlite_stage_failure_rolls_back_and_preserves_previous_active(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    store = SQLiteCodexFactStore(tmp_path / f"rollback-{failure_stage}.sqlite3")
    sources_a = _command_result()
    generation_a = _publication(sources=sources_a)
    store.publish(generation_a, source_items=sources_a)
    facts_before = store.fact_count()
    links_before = store.link_count()

    event_scope_b = _event_scope(generation="generation-b")
    sources_b = _command_result(scope=event_scope_b, exit_code=4)
    generation_b = _publication(
        scope=_publication_scope(generation="generation-b"),
        sources=sources_b,
    )

    def failpoint(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError("injected failure")

    store.failpoint = failpoint
    with pytest.raises(CodexFactPublicationError, match="failed"):
        store.publish(generation_b, source_items=sources_b)
    store.failpoint = None

    assert store.active_generation(generation_a.scope) == "generation-a"
    assert store.get_active(generation_a.scope) == generation_a
    assert store.publication_count() == 1
    assert store.fact_count() == facts_before
    assert store.link_count() == links_before
    assert store.staged_count() == 0
    assert (
        store.get_publication(
            generation_b.scope,
            publication_id=generation_b.publication_id,
        )
        is None
    )
    store.close()


def test_empty_publication_cannot_replace_active_generation(tmp_path: Path) -> None:
    store = SQLiteCodexFactStore(tmp_path / "empty.sqlite3")
    active_sources = _command_result()
    active = _publication(sources=active_sources)
    store.publish(active, source_items=active_sources)
    empty_sources = (_item("reasoning", 0, "reasoning", payload={"text": "not materialized"}),)
    empty = _publication(sources=empty_sources)
    assert empty.facts == ()

    with pytest.raises(CodexFactValidationError, match="empty"):
        store.publish(empty, source_items=empty_sources)
    assert store.get_active(active.scope) == active
    assert store.publication_count() == 1
    assert store.staged_count() == 0
    store.close()


def test_store_rejects_non_temporary_database_path() -> None:
    with pytest.raises(ValueError, match="temporary paths"):
        SQLiteCodexFactStore(Path.cwd() / "forbidden-codex-facts.sqlite3")


def test_event_link_contract_rejects_missing_endpoint_and_unknown_predicate() -> None:
    publication = _publication()
    link = publication.links[0]
    with pytest.raises(ValidationError):
        link.model_copy(
            update={
                "predicate": "unknown",
            }
        )
    with pytest.raises(ValidationError):
        link.model_copy(
            update={
                "source": link.source.model_copy(
                    update={
                        "kind": CodexEndpointKind.RAW_ITEM,
                    }
                )
            }
        )
