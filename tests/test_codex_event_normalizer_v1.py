from __future__ import annotations

import itertools
import json
from collections.abc import Iterable

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.codex.contracts import (
    ActionState,
    DerivedEventKind,
    DiagnosticCode,
    EventScope,
    NormalizedActionEvent,
    NormalizedObservationEvent,
    NormalizedPatchEvent,
    NormalizedValidationEvent,
    ObservableCodexItem,
    PatchState,
    ValidationState,
    canonical_codex_locator_v1,
)
from evidence_rag.rag.sources.codex.event_normalizer import normalize_codex_events_v1


def _scope(
    *,
    thread: str = "thread-a",
    turn: str = "turn-a",
    generation: str = "generation-a",
    project: str = "project-a",
    acl: str = "project:project-a",
) -> EventScope:
    return EventScope(
        project_id=project,
        source_id="codex-source:a",
        generation_id=generation,
        thread_id=thread,
        turn_id=turn,
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
    exact_scope = scope or _scope()
    return ObservableCodexItem(
        item_id=item_id,
        source_locator=canonical_codex_locator_v1(exact_scope, item_id, order),
        scope=exact_scope,
        observable_order=order,
        item_type=item_type,
        **values,
    )


def _command(
    *,
    item_id: str = "command",
    order: int = 1,
    scope: EventScope | None = None,
    call_id: str = "call-a",
    argv: tuple[str, ...] = ("pytest", "tests/test_alpha.py"),
    targets: tuple[str, ...] = ("tests/test_alpha.py",),
    shell: bool = False,
    target_project_id: str | None = None,
) -> ObservableCodexItem:
    return _item(
        item_id,
        order,
        "CommandExecution",
        scope=scope,
        call_id=call_id,
        command_argv=argv,
        targets=targets,
        shell=shell,
        target_project_id=target_project_id,
    )


def _result(
    *,
    item_id: str = "result",
    order: int = 2,
    scope: EventScope | None = None,
    call_id: str = "call-a",
    payload: dict[str, object] | None = None,
    outcome: str | None = None,
    success: bool | None = None,
    item_type: str = "ToolResult",
    call_type: str = "command",
    role: str | None = None,
) -> ObservableCodexItem:
    values: dict[str, object] = {
        "call_id": call_id,
        "call_type": call_type,
        "payload": payload or {},
    }
    if role is not None:
        values["role"] = role
    if outcome is not None:
        values["outcome"] = outcome
    if success is not None:
        values["success"] = success
    return _item(item_id, order, item_type, scope=scope, **values)


def _patch_exit_payload(mode: str) -> dict[str, object]:
    if mode == "none":
        return {}
    if mode == "zero":
        return {"exit_code": 0}
    if mode == "one":
        return {"exit_code": 1}
    if mode == "string":
        return {"exit_code": "1"}
    if mode == "bool":
        return {"exit_code": True}
    if mode == "ambiguous":
        return {"left": {"exit_code": 0}, "right": {"exit_code": 1}}
    if mode == "bounded":
        payload: dict[str, object] = {"exit_code": 1}
        for index in range(8):
            payload = {f"layer-{index}": payload}
        return payload
    if mode == "null":
        return {"exit_code": None}
    if mode == "unsupported":
        return {"exitCode": 1}
    raise AssertionError(f"unknown patch exit mode: {mode}")


def _states(
    records: Iterable[ObservableCodexItem],
) -> tuple[list[ActionState], list[PatchState], list[ValidationState]]:
    normalized = normalize_codex_events_v1(records)
    actions = [
        event.state for event in normalized.events if isinstance(event, NormalizedActionEvent)
    ]
    patches = [
        event.state for event in normalized.events if isinstance(event, NormalizedPatchEvent)
    ]
    validations = [
        event.state for event in normalized.events if isinstance(event, NormalizedValidationEvent)
    ]
    return actions, patches, validations


def _diagnostic_codes(records: Iterable[ObservableCodexItem]) -> set[DiagnosticCode]:
    return {diagnostic.code for diagnostic in normalize_codex_events_v1(records).diagnostics}


def test_exact_late_result_pairs_and_advances_action_and_validation() -> None:
    normalized = normalize_codex_events_v1(
        [
            _command(),
            _result(payload={"execution": {"result": {"exit_code": 0}}}),
        ]
    )

    actions, patches, validations = _states(
        [_command(), _result(payload={"execution": {"result": {"exit_code": 0}}})]
    )
    assert actions == [ActionState.COMPLETED]
    assert patches == []
    assert validations == [ValidationState.PASSED]
    validation = next(
        event for event in normalized.events if isinstance(event, NormalizedValidationEvent)
    )
    assert validation.state_history == (
        ValidationState.COMMAND_INVOKED,
        ValidationState.EXIT_OBSERVED,
        ValidationState.PASSED,
    )
    assert validation.exit_code == 0
    assert len(normalized.links) == 1
    assert normalized.links[0].call_item_id == "command"
    assert normalized.links[0].result_item_id == "result"
    assert normalized.summary.false_validated_count == 0


def test_missing_duplicate_and_out_of_order_results_fail_closed() -> None:
    missing = normalize_codex_events_v1([_command()])
    assert _states([_command()]) == (
        [ActionState.INVOKED],
        [],
        [ValidationState.COMMAND_INVOKED],
    )
    assert DiagnosticCode.MISSING_RESULT in {item.code for item in missing.diagnostics}

    multiple_records = [
        _command(),
        _result(item_id="result-a", payload={"exit_code": 0}),
        _result(item_id="result-b", order=3, payload={"exit_code": 1}),
    ]
    multiple = normalize_codex_events_v1(multiple_records)
    assert _states(multiple_records) == (
        [ActionState.INVOKED],
        [],
        [ValidationState.COMMAND_INVOKED],
    )
    assert DiagnosticCode.MULTIPLE_RESULTS in {item.code for item in multiple.diagnostics}
    assert not multiple.links

    duplicate_result_records = [
        _command(),
        _result(payload={"exit_code": 0}),
        _result(payload={"exit_code": 1}),
    ]
    duplicate_result = normalize_codex_events_v1(duplicate_result_records)
    assert DiagnosticCode.DUPLICATE_RESULT in {item.code for item in duplicate_result.diagnostics}
    assert ValidationState.PASSED not in _states(duplicate_result_records)[2]

    out_of_order_records = [
        _command(order=2),
        _result(order=1, payload={"exit_code": 0}),
    ]
    out_of_order = normalize_codex_events_v1(out_of_order_records)
    assert _states(out_of_order_records) == (
        [ActionState.INVOKED],
        [],
        [ValidationState.COMMAND_INVOKED],
    )
    assert DiagnosticCode.OUT_OF_ORDER_RESULT in {item.code for item in out_of_order.diagnostics}
    assert not out_of_order.links


def test_ambiguous_cross_thread_cross_generation_and_type_mismatch_do_not_pair() -> None:
    ambiguous = [
        _command(item_id="command-a"),
        _command(item_id="command-b", order=2),
        _result(order=3, payload={"exit_code": 0}),
    ]
    assert DiagnosticCode.AMBIGUOUS_CALL in _diagnostic_codes(ambiguous)
    assert ValidationState.PASSED not in _states(ambiguous)[2]
    assert DiagnosticCode.AMBIGUOUS_CALL in _diagnostic_codes(ambiguous[:2])

    cross_thread_scope = _scope(thread="thread-b")
    cross_thread = [
        _command(),
        _result(scope=cross_thread_scope, payload={"exit_code": 0}),
    ]
    assert DiagnosticCode.CROSS_THREAD_RESULT in _diagnostic_codes(cross_thread)
    assert ValidationState.PASSED not in _states(cross_thread)[2]

    cross_generation_scope = _scope(generation="generation-b")
    cross_generation = [
        _command(),
        _result(scope=cross_generation_scope, payload={"exit_code": 0}),
    ]
    assert DiagnosticCode.CROSS_GENERATION_RESULT in _diagnostic_codes(cross_generation)
    assert ValidationState.PASSED not in _states(cross_generation)[2]

    wrong_type = [
        _command(),
        _result(call_type="other_tool", payload={"exit_code": 0}),
    ]
    assert DiagnosticCode.CALL_TYPE_MISMATCH in _diagnostic_codes(wrong_type)
    assert ValidationState.PASSED not in _states(wrong_type)[2]


@pytest.mark.parametrize(
    ("payload", "action", "validation", "diagnostic"),
    [
        ({"exit_code": 0}, ActionState.COMPLETED, ValidationState.PASSED, None),
        (
            {"envelope": [{"execution": {"exit_code": 7}}]},
            ActionState.FAILED,
            ValidationState.FAILED,
            None,
        ),
        (
            {},
            ActionState.UNKNOWN,
            ValidationState.COMMAND_INVOKED,
            DiagnosticCode.MISSING_EXIT_CODE,
        ),
        (
            {"exit_code": True},
            ActionState.UNKNOWN,
            ValidationState.COMMAND_INVOKED,
            DiagnosticCode.INVALID_EXIT_CODE,
        ),
        (
            {"exit_code": "0"},
            ActionState.UNKNOWN,
            ValidationState.COMMAND_INVOKED,
            DiagnosticCode.INVALID_EXIT_CODE,
        ),
        (
            {"a": {"exit_code": 0}, "b": {"exit_code": 0}},
            ActionState.UNKNOWN,
            ValidationState.COMMAND_INVOKED,
            DiagnosticCode.AMBIGUOUS_EXIT_CODE,
        ),
    ],
)
def test_only_one_bounded_structured_integer_exit_code_is_truth(
    payload: dict[str, object],
    action: ActionState,
    validation: ValidationState,
    diagnostic: DiagnosticCode | None,
) -> None:
    records = [_command(), _result(payload=payload)]
    actions, _, validations = _states(records)
    assert actions == [action]
    assert validations == [validation]
    if diagnostic is not None:
        assert diagnostic in _diagnostic_codes(records)


def test_excessively_nested_exit_payload_is_bounded_and_not_observed() -> None:
    payload: dict[str, object] = {"exit_code": 0}
    for index in range(8):
        payload = {f"layer_{index}": payload}
    records = [_command(), _result(payload=payload)]

    assert ValidationState.PASSED not in _states(records)[2]
    assert DiagnosticCode.EXIT_PAYLOAD_BOUNDED in _diagnostic_codes(records)


def test_allowlist_no_shell_and_exact_target_are_all_required() -> None:
    exact = [_command(), _result(payload={"exit_code": 0})]
    assert _states(exact)[2] == [ValidationState.PASSED]

    non_allowlisted = [
        _command(argv=("echo", "tests passed")),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(non_allowlisted)[2] == []
    assert DiagnosticCode.COMMAND_NOT_ALLOWLISTED in _diagnostic_codes(non_allowlisted)

    shell = [
        _command(argv=("pytest", "tests/test_alpha.py"), shell=True),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(shell)[2] == []
    assert DiagnosticCode.SHELL_COMMAND_REJECTED in _diagnostic_codes(shell)

    unknown = [
        _command(targets=()),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(unknown)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_ABSENT in _diagnostic_codes(unknown)

    absolute = [
        _command(targets=("/Users/alice/project/tests/test_alpha.py",)),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(absolute)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_REJECTED in _diagnostic_codes(absolute)

    traversal = [
        _command(targets=("../tests/test_alpha.py",)),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(traversal)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_REJECTED in _diagnostic_codes(traversal)

    cross_project = [
        _command(target_project_id="project-b"),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(cross_project)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_PROJECT_MISMATCH in _diagnostic_codes(cross_project)


def test_plan_agent_claim_mention_and_existing_validation_never_upgrade_truth() -> None:
    records = [
        _item("plan", 1, "Plan", role="assistant", payload={"exit_code": 0}),
        _item("claim", 2, "AgentMessage", role="assistant", success=True),
        _item("mention", 3, "ValidationMention", role="assistant"),
        _item(
            "old-validation",
            4,
            "ValidationResult",
            role="assistant",
            payload={"exit_code": 0},
            success=True,
        ),
    ]
    normalized = normalize_codex_events_v1(records)

    assert not [event for event in normalized.events if isinstance(event, NormalizedActionEvent)]
    validations = [
        event for event in normalized.events if isinstance(event, NormalizedValidationEvent)
    ]
    assert [event.state for event in validations] == [ValidationState.MENTIONED]
    observations = [
        event for event in normalized.events if isinstance(event, NormalizedObservationEvent)
    ]
    assert len(observations) == 3
    assert all(event.kind is DerivedEventKind.OBSERVATION for event in observations)
    assert normalized.summary.false_validated_count == 0


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("timeout", ActionState.TIMEOUT),
        ("cancelled", ActionState.CANCELLED),
        ("failed", ActionState.FAILED),
        ("completed", ActionState.COMPLETED),
    ],
)
def test_observed_action_outcomes_cover_timeout_cancel_failure_and_completion(
    outcome: str,
    expected: ActionState,
) -> None:
    tool = _item(
        "tool-call",
        1,
        "ToolCall",
        call_id="tool-call-id",
        tool_name="read_file",
    )
    result = _result(
        item_id="tool-result",
        order=2,
        call_id="tool-call-id",
        call_type="read_file",
        outcome=outcome,
    )
    assert _states([tool, result])[0] == [expected]


def test_patch_proposal_invocation_apply_failure_and_claim_only_are_separate() -> None:
    proposal = _item(
        "patch-proposal",
        1,
        "Patch",
        targets=("src/alpha.py",),
    )
    invoked = _item(
        "patch-invoked",
        2,
        "PatchApply",
        call_id="patch-call",
        call_type="patch_apply",
        targets=("src/alpha.py",),
    )
    missing = normalize_codex_events_v1([proposal, invoked])
    assert [event.state for event in missing.events if isinstance(event, NormalizedPatchEvent)] == [
        PatchState.PATCH_PROPOSED,
        PatchState.APPLY_INVOKED,
    ]

    applied_records = [
        invoked,
        _result(
            item_id="patch-success",
            order=3,
            call_id="patch-call",
            call_type="patch_apply",
            item_type="PatchResult",
            success=True,
        ),
    ]
    assert _states(applied_records)[1] == [PatchState.APPLIED]

    failed_records = [
        invoked,
        _result(
            item_id="patch-failure",
            order=3,
            call_id="patch-call",
            call_type="patch_apply",
            item_type="PatchResult",
            success=False,
        ),
    ]
    assert _states(failed_records)[1] == [PatchState.FAILED]

    failed_exit_records = [
        invoked,
        _result(
            item_id="patch-exit-failure",
            order=3,
            call_id="patch-call",
            call_type="patch_apply",
            item_type="ToolResult",
            payload={"execution": {"exit_code": 2}},
        ),
    ]
    assert _states(failed_exit_records)[1] == [PatchState.FAILED]

    text_claim_records = [
        invoked,
        _result(
            item_id="patch-text",
            order=3,
            call_id="patch-call",
            call_type="patch_apply",
            item_type="PatchResult",
            payload={"output": "Done! Patch applied successfully."},
        ),
    ]
    assert _states(text_claim_records)[1] == [PatchState.APPLY_INVOKED]
    assert DiagnosticCode.PATCH_RESULT_UNKNOWN in _diagnostic_codes(text_claim_records)

    conflicting_records = [
        invoked,
        _result(
            item_id="patch-success",
            order=3,
            call_id="patch-call",
            call_type="patch_apply",
            item_type="PatchResult",
            success=True,
        ),
        _result(
            item_id="patch-failure",
            order=4,
            call_id="patch-call",
            call_type="patch_apply",
            item_type="PatchResult",
            success=False,
        ),
    ]
    assert _states(conflicting_records)[1] == [PatchState.APPLY_INVOKED]
    assert DiagnosticCode.MULTIPLE_RESULTS in _diagnostic_codes(conflicting_records)


def test_patch_success_requires_a_safe_bound_target() -> None:
    invoked = _item(
        "patch-invoked",
        1,
        "PatchApply",
        call_id="patch-call",
        call_type="patch_apply",
        targets=("/private/project/src/alpha.py",),
    )
    result = _result(
        item_id="patch-success",
        order=2,
        call_id="patch-call",
        call_type="patch_apply",
        item_type="PatchResult",
        success=True,
    )
    normalized = normalize_codex_events_v1([invoked, result])

    assert _states([invoked, result])[1] == [PatchState.APPLY_INVOKED]
    assert DiagnosticCode.TARGET_REJECTED in {item.code for item in normalized.diagnostics}
    dumped = json.dumps(normalized.model_dump(mode="json"), sort_keys=True)
    assert "/private/project" not in dumped


def test_validation_target_is_bound_to_exact_canonical_argv_target_set() -> None:
    exact = [
        _command(
            argv=("pytest", "-q", "tests/test_alpha.py::test_a"),
            targets=("tests/test_alpha.py::test_a",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(exact)[2] == [ValidationState.PASSED]

    mismatch = [
        _command(
            argv=("pytest", "tests/test_beta.py::test_b"),
            targets=("tests/test_alpha.py::test_a",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(mismatch)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_MISMATCH in _diagnostic_codes(mismatch)

    no_argv_target = [
        _command(
            argv=("pytest", "-q"),
            targets=("tests/test_alpha.py::test_a",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(no_argv_target)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_ABSENT in _diagnostic_codes(no_argv_target)

    option_value_disguise = [
        _command(
            argv=("pytest", "-k", "tests/test_alpha.py::test_a"),
            targets=("tests/test_alpha.py::test_a",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(option_value_disguise)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_ABSENT in _diagnostic_codes(option_value_disguise)

    selector_mismatch = [
        _command(
            argv=("pytest", "tests/test_alpha.py::test_a"),
            targets=("tests/test_alpha.py::test_b",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(selector_mismatch)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_MISMATCH in _diagnostic_codes(selector_mismatch)

    exact_multiple = [
        _command(
            argv=("pytest", "tests/test_beta.py", "tests/test_alpha.py"),
            targets=("tests/test_alpha.py", "tests/test_beta.py"),
        ),
        _result(payload={"exit_code": 0}),
    ]
    normalized = normalize_codex_events_v1(exact_multiple)
    validation = next(
        event for event in normalized.events if isinstance(event, NormalizedValidationEvent)
    )
    assert validation.state is ValidationState.PASSED
    assert validation.targets == ("tests/test_alpha.py", "tests/test_beta.py")

    subset_mismatch = [
        _command(
            argv=("pytest", "tests/test_alpha.py", "tests/test_beta.py"),
            targets=("tests/test_alpha.py",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(subset_mismatch)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_MISMATCH in _diagnostic_codes(subset_mismatch)


def test_pytest_option_values_are_not_targets_and_unittest_selector_is_bound() -> None:
    pytest_records = [
        _command(
            argv=(
                "pytest",
                "--rootdir",
                "tests",
                "tests/test_alpha.py::test_a",
            ),
            targets=("tests/test_alpha.py::test_a",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(pytest_records)[2] == [ValidationState.PASSED]

    ignored_only = [
        _command(
            argv=("pytest", "--ignore=tests/test_alpha.py"),
            targets=("tests/test_alpha.py",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(ignored_only)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_ABSENT in _diagnostic_codes(ignored_only)

    unknown_option = [
        _command(
            argv=("pytest", "--plugin-target=tests/test_alpha.py"),
            targets=("tests/test_alpha.py",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(unknown_option)[2] == [ValidationState.TARGET_UNKNOWN]
    assert DiagnosticCode.TARGET_ARGUMENT_UNSUPPORTED in _diagnostic_codes(unknown_option)

    unittest_records = [
        _command(
            argv=(
                "python",
                "-m",
                "unittest",
                "tests.test_alpha.TestAlpha.test_a",
            ),
            targets=("tests.test_alpha.TestAlpha.test_a",),
        ),
        _result(payload={"exit_code": 0}),
    ]
    assert _states(unittest_records)[2] == [ValidationState.PASSED]


@pytest.mark.parametrize("result_type", ["ToolResult", "CommandResult", "PatchResult"])
@pytest.mark.parametrize("role", ["assistant", "user", "system", "developer"])
def test_forbidden_result_roles_never_participate_in_pairing(
    result_type: str,
    role: str,
) -> None:
    if result_type == "PatchResult":
        call = _item(
            "patch-invoked",
            1,
            "PatchApply",
            call_id="call-a",
            call_type="patch_apply",
            targets=("src/alpha.py",),
        )
        result = _result(
            item_type=result_type,
            call_type="patch_apply",
            payload={"exit_code": 0},
            success=True,
            role=role,
        )
    else:
        call = _command()
        result = _result(
            item_type=result_type,
            payload={"exit_code": 0},
            role=role,
        )
    normalized = normalize_codex_events_v1([call, result])

    assert not normalized.links
    assert DiagnosticCode.ROLE_PROVENANCE_CONFLICT in {
        diagnostic.code for diagnostic in normalized.diagnostics
    }
    assert any(
        isinstance(event, NormalizedObservationEvent)
        and event.observation.value == "contradictory_provenance"
        for event in normalized.events
    )
    assert ActionState.COMPLETED not in _states([call, result])[0]
    assert PatchState.APPLIED not in _states([call, result])[1]
    assert ValidationState.PASSED not in _states([call, result])[2]


@pytest.mark.parametrize("result_type", ["ToolResult", "CommandResult", "PatchResult"])
@pytest.mark.parametrize("role", [None, "tool"])
def test_none_and_tool_roles_are_observable_result_provenance(
    result_type: str,
    role: str | None,
) -> None:
    if result_type == "PatchResult":
        call = _item(
            "patch-invoked",
            1,
            "PatchApply",
            call_id="call-a",
            call_type="patch_apply",
            targets=("src/alpha.py",),
        )
        result = _result(
            item_type=result_type,
            call_type="patch_apply",
            success=True,
            role=role,
        )
    else:
        call = _command()
        result = _result(
            item_type=result_type,
            payload={"exit_code": 0},
            role=role,
        )
    records = [call, result]
    normalized = normalize_codex_events_v1(records)

    if result_type == "PatchResult":
        assert _states(records)[1] == [PatchState.APPLIED]
    else:
        assert _states(records)[0] == [ActionState.COMPLETED]
        assert _states(records)[2] == [ValidationState.PASSED]
    assert len(normalized.links) == 1
    assert DiagnosticCode.ROLE_PROVENANCE_CONFLICT not in {
        diagnostic.code for diagnostic in normalized.diagnostics
    }


@pytest.mark.parametrize("item_type", ["AgentMessage", "Plan"])
def test_tool_role_weak_item_types_remain_observation_only(item_type: str) -> None:
    weak = _item(
        "weak",
        1,
        item_type,
        role="tool",
        call_id="call-a",
        call_type="command",
        payload={"exit_code": 0},
        success=True,
    )
    normalized = normalize_codex_events_v1([weak])

    assert len(normalized.events) == 1
    assert isinstance(normalized.events[0], NormalizedObservationEvent)
    assert normalized.events[0].observation.value == "contradictory_provenance"
    assert DiagnosticCode.ROLE_PROVENANCE_CONFLICT in {
        diagnostic.code for diagnostic in normalized.diagnostics
    }
    assert not normalized.links


def test_assistant_role_command_invocation_cannot_be_observed_action() -> None:
    command = _item(
        "command",
        1,
        "CommandExecution",
        role="assistant",
        call_id="call-a",
        command_argv=("pytest", "tests/test_alpha.py"),
        targets=("tests/test_alpha.py",),
    )
    records = [command, _result(payload={"exit_code": 0})]

    assert ActionState.COMPLETED not in _states(records)[0]
    assert ValidationState.PASSED not in _states(records)[2]
    assert DiagnosticCode.ROLE_PROVENANCE_CONFLICT in _diagnostic_codes(records)


def test_patch_terminal_signals_require_complete_agreement() -> None:
    matrix_cases = 0
    conflict_cases = 0
    for success, outcome, exit_code in itertools.product(
        (None, True, False),
        (None, "completed", "failed", "timeout", "cancelled"),
        (None, 0, 1),
    ):
        if success is None and outcome is None and exit_code is None:
            continue
        matrix_cases += 1
        invoked = _item(
            "patch-invoked",
            1,
            "PatchApply",
            call_id="patch-call",
            call_type="patch_apply",
            targets=("src/alpha.py",),
        )
        result = _result(
            item_id="patch-result",
            order=2,
            call_id="patch-call",
            call_type="patch_apply",
            item_type="ToolResult",
            success=success,
            outcome=outcome,
            payload={} if exit_code is None else {"exit_code": exit_code},
        )
        normalized = normalize_codex_events_v1([invoked, result])
        state = next(
            event.state for event in normalized.events if isinstance(event, NormalizedPatchEvent)
        )
        diagnostics = {item.code for item in normalized.diagnostics}
        signals: list[bool] = []
        if success is not None:
            signals.append(success)
        if outcome is not None:
            signals.append(outcome == "completed")
        if exit_code is not None:
            signals.append(exit_code == 0)
        conflict = len(set(signals)) > 1
        if conflict:
            conflict_cases += 1
            assert state is PatchState.APPLY_INVOKED
            assert DiagnosticCode.PATCH_TERMINAL_CONFLICT in diagnostics
        elif signals[0]:
            assert state is PatchState.APPLIED
            assert DiagnosticCode.PATCH_TERMINAL_CONFLICT not in diagnostics
        else:
            assert state is PatchState.FAILED
            assert DiagnosticCode.PATCH_TERMINAL_CONFLICT not in diagnostics
            assert state is not PatchState.APPLIED

    assert matrix_cases == 44
    assert conflict_cases > 0


@pytest.mark.parametrize("success", [None, True, False])
@pytest.mark.parametrize(
    "exit_mode",
    ["none", "zero", "one", "string", "bool", "ambiguous", "bounded"],
)
def test_explicit_unknown_outcome_blocks_every_patch_terminal_combination(
    success: bool | None,
    exit_mode: str,
) -> None:
    invoked = _item(
        "patch-invoked",
        1,
        "PatchApply",
        call_id="patch-call",
        call_type="patch_apply",
        targets=("src/alpha.py",),
    )
    result = _result(
        item_id="patch-result",
        order=2,
        call_id="patch-call",
        call_type="patch_apply",
        item_type="ToolResult",
        success=success,
        outcome="unknown",
        payload=_patch_exit_payload(exit_mode),
    )
    normalized = normalize_codex_events_v1([invoked, result])
    diagnostics = {item.code for item in normalized.diagnostics}

    assert _states([invoked, result])[1] == [PatchState.APPLY_INVOKED]
    trusted_signal = success is not None or exit_mode in {"zero", "one"}
    expected = (
        DiagnosticCode.PATCH_TERMINAL_CONFLICT
        if trusted_signal
        else DiagnosticCode.PATCH_RESULT_UNKNOWN
    )
    assert expected in diagnostics


@pytest.mark.parametrize(
    "exit_mode",
    ["string", "bool", "ambiguous", "bounded", "null", "unsupported"],
)
@pytest.mark.parametrize("result_type", ["ToolResult", "PatchResult"])
def test_untrusted_exit_scan_blocks_success_only_patch(
    exit_mode: str,
    result_type: str,
) -> None:
    invoked = _item(
        "patch-invoked",
        1,
        "PatchApply",
        call_id="patch-call",
        call_type="patch_apply",
        targets=("src/alpha.py",),
    )
    result = _result(
        item_id="patch-result",
        order=2,
        call_id="patch-call",
        call_type="patch_apply",
        item_type=result_type,
        success=True,
        payload=_patch_exit_payload(exit_mode),
    )
    normalized = normalize_codex_events_v1([invoked, result])

    assert _states([invoked, result])[1] == [PatchState.APPLY_INVOKED]
    assert DiagnosticCode.PATCH_TERMINAL_CONFLICT in {item.code for item in normalized.diagnostics}


@pytest.mark.parametrize(
    ("success", "outcome", "exit_code"),
    [
        (True, "failed", None),
        (True, None, 1),
        (None, "completed", 1),
        (False, "completed", 0),
    ],
)
def test_named_patch_terminal_conflicts_remain_unknown(
    success: bool | None,
    outcome: str | None,
    exit_code: int | None,
) -> None:
    invoked = _item(
        "patch-invoked",
        1,
        "PatchApply",
        call_id="patch-call",
        call_type="patch_apply",
        targets=("src/alpha.py",),
    )
    result = _result(
        item_id="patch-result",
        order=2,
        call_id="patch-call",
        call_type="patch_apply",
        item_type="ToolResult",
        success=success,
        outcome=outcome,
        payload={} if exit_code is None else {"exit_code": exit_code},
    )
    normalized = normalize_codex_events_v1([invoked, result])

    assert _states([invoked, result])[1] == [PatchState.APPLY_INVOKED]
    assert DiagnosticCode.PATCH_TERMINAL_CONFLICT in {item.code for item in normalized.diagnostics}


def test_locator_is_exactly_rebuilt_from_scope_item_and_observable_order() -> None:
    raw_scope = _scope()
    uri_scope = _scope(
        thread="codex://thread/thread-a",
        turn="codex://thread/thread-a/turn/turn-a",
    )
    raw = _item("item-a", 7, "Plan", scope=raw_scope)
    uri = _item("item-a", 7, "Plan", scope=uri_scope)

    assert raw.source_locator == uri.source_locator
    assert raw.source_locator == ("codex://thread/thread-a/turn/turn-a/item/item-a#event=8")


@pytest.mark.parametrize(
    "malicious_locator",
    [
        "codex://thread/thread-a/turn/turn-a/item//Users/alice/file#event=2",
        "codex://thread/thread-a/turn/turn-a/item/%2FUsers%2Falice%2Ffile#event=2",
        r"codex://thread/thread-a/turn/turn-a/item/C:\\temp\\file#event=2",
        "codex://thread/thread-a/turn/turn-a/item/C%3A%5Ctemp%5Cfile#event=2",
        "codex://thread/thread-a/turn/turn-a/item/../secret#event=2",
        "codex://thread/thread-a/turn/turn-a/item/%2E%2E#event=2",
        "codex://thread/thread-b/turn/turn-a/item/item-a#event=2",
        "codex://thread/thread-a/turn/turn-b/item/item-a#event=2",
        "codex://thread/thread-a/turn/turn-a/item/item-b#event=2",
        "codex://thread/thread-a/turn/turn-a/item/item-a#event=99",
        "codex://thread/thread-a//turn/turn-a/item/item-a#event=2",
        "codex://thread/thread-a/turn/turn-a/item/item-a?query=1#event=2",
        "codex://thread/thread-a/turn/turn-a/item/item-a#event=2#extra",
        (
            "codex://thread/thread-a/turn/turn-a/item/"
            "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456#event=2"
        ),
        (
            "codex://thread/thread-a/turn/turn-a/item/"
            "sk-proj-%41%42%43%44%45%46%47%48%49%4A%4B%4C%4D%4E%4F%50"
            "%51%52%53%54%55%56%57%58%59%5A%31%32%33%34%35%36#event=2"
        ),
    ],
)
def test_malicious_or_mismatched_locator_is_rejected_without_normalization(
    malicious_locator: str,
) -> None:
    payload = _item("item-a", 1, "Plan").model_dump(mode="python")
    payload["source_locator"] = malicious_locator

    with pytest.raises(ValidationError, match="source_locator"):
        ObservableCodexItem.model_validate(payload)


def test_scrambled_input_and_exact_duplicates_produce_identical_facts_and_digest() -> None:
    records = [
        _command(),
        _result(payload={"nested": {"exit_code": 0}}),
        _item("plan", 3, "Plan", role="assistant"),
    ]
    forward = normalize_codex_events_v1(records)
    scrambled = normalize_codex_events_v1([records[2], records[0], records[1]])
    duplicated = normalize_codex_events_v1([*records, records[0], records[1]])

    assert forward.events == scrambled.events == duplicated.events
    assert forward.links == scrambled.links == duplicated.links
    assert forward.diagnostics == scrambled.diagnostics == duplicated.diagnostics
    assert (
        forward.summary.content_sha256
        == scrambled.summary.content_sha256
        == duplicated.summary.content_sha256
    )
    assert duplicated.summary.event_count == forward.summary.event_count
    assert duplicated.summary.unique_input_count == forward.summary.unique_input_count


def test_reasoning_system_developer_and_secrets_are_excluded_from_all_output() -> None:
    secret = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    records = [
        _item("reasoning", 1, "reasoning", payload={"text": secret}),
        _item("agent-reasoning", 2, "agent_reasoning", payload={"text": secret}),
        _item("tokens", 3, "token_count", payload={"value": 999}),
        _item("system", 4, "SystemMessage", role="system", payload={"text": secret}),
        _item("developer", 5, "DeveloperMessage", role="developer", payload={"text": secret}),
        _command(
            item_id="unsafe-command",
            order=6,
            argv=("pytest", f"/Users/alice/{secret}/tests/test_alpha.py"),
            targets=(f"/Users/alice/{secret}/tests/test_alpha.py",),
        ),
    ]
    normalized = normalize_codex_events_v1(records)
    dumped = json.dumps(normalized.model_dump(mode="json"), sort_keys=True)

    assert secret not in dumped
    assert "/Users/alice" not in dumped
    assert normalized.summary.reasoning_units == 0
    assert normalized.summary.agent_reasoning_units == 0
    assert normalized.summary.token_count_units == 0
    assert normalized.summary.system_units == 0
    assert normalized.summary.developer_units == 0
    assert normalized.summary.excluded_input_count == 5


def test_outputs_are_frozen_and_diagnostics_are_bounded() -> None:
    records = [
        _item(f"reasoning-{index}", index, "reasoning", payload={"index": index})
        for index in range(300)
    ]
    normalized = normalize_codex_events_v1(records)

    assert len(normalized.diagnostics) == 256
    assert normalized.diagnostics[-1].code is DiagnosticCode.DIAGNOSTICS_TRUNCATED
    assert normalized.summary.diagnostics_truncated == 45
    with pytest.raises(ValidationError, match="frozen"):
        normalized.summary.event_count = 99


def test_false_validated_exhaustive_truth_table_has_zero_false_positives() -> None:
    pairing_modes = ("exact", "missing", "cross_thread", "cross_generation", "ambiguous")
    exit_values: tuple[object, ...] = (0, 3, None, True, "0")
    target_modes = (
        "exact",
        "unknown",
        "absolute",
        "traversal",
        "mismatch",
        "argv_absent",
        "option_value",
        "selector_mismatch",
    )
    allowlist_modes = (True, False)
    shell_modes = (False, True)
    false_positives = 0
    passed = 0
    cases = 0

    for pairing, exit_value, target_mode, allowlisted, shell in itertools.product(
        pairing_modes,
        exit_values,
        target_modes,
        allowlist_modes,
        shell_modes,
    ):
        cases += 1
        argv = ("pytest", "tests/test_alpha.py::test_case")
        targets = ("tests/test_alpha.py::test_case",)
        if target_mode == "unknown":
            targets = ()
        elif target_mode == "absolute":
            targets = ("/Users/alice/project/tests/test_alpha.py",)
        elif target_mode == "traversal":
            targets = ("../tests/test_alpha.py",)
        elif target_mode == "mismatch":
            targets = ("tests/test_beta.py::test_case",)
        elif target_mode == "argv_absent":
            argv = ("pytest", "-q")
        elif target_mode == "option_value":
            argv = ("pytest", "-k", "tests/test_alpha.py::test_case")
        elif target_mode == "selector_mismatch":
            targets = ("tests/test_alpha.py::other_case",)
        command = _command(
            argv=argv if allowlisted else ("echo", "tests passed"),
            targets=targets,
            shell=shell,
        )
        records = [command]
        if pairing != "missing":
            result_scope = _scope()
            if pairing == "cross_thread":
                result_scope = _scope(thread="thread-b")
            elif pairing == "cross_generation":
                result_scope = _scope(generation="generation-b")
            payload = {} if exit_value is None else {"exit_code": exit_value}
            records.append(_result(scope=result_scope, payload=payload))
        if pairing == "ambiguous":
            records.insert(1, _command(item_id="command-b", order=2))
            records[-1] = _result(order=3, payload=records[-1].payload)

        normalized = normalize_codex_events_v1(records)
        validation_passed = any(
            isinstance(event, NormalizedValidationEvent) and event.state is ValidationState.PASSED
            for event in normalized.events
        )
        valid_truth = (
            pairing == "exact"
            and type(exit_value) is int
            and exit_value == 0
            and target_mode == "exact"
            and allowlisted
            and not shell
        )
        passed += int(validation_passed)
        false_positives += int(validation_passed and not valid_truth)
        assert normalized.summary.false_validated_count == 0
        assert validation_passed is valid_truth

    assert passed == 1
    assert false_positives == 0
    assert cases == 800
