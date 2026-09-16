from __future__ import annotations

import pytest

from evidence_rag.rag.sources.codex.contracts import (
    ActionState,
    ObservedOutcome,
    PatchState,
    ValidationState,
)
from evidence_rag.rag.sources.codex.state_machine import (
    CodexEventStateError,
    action_state_history,
    patch_state_history,
    validation_state_history,
)


def test_action_state_machine_is_exhaustive_and_observation_driven() -> None:
    assert action_state_history(proposed=True) == (ActionState.PROPOSED,)
    assert action_state_history(invoked=True) == (ActionState.INVOKED,)
    assert action_state_history(invoked=True, outcome=ObservedOutcome.COMPLETED) == (
        ActionState.INVOKED,
        ActionState.COMPLETED,
    )
    assert action_state_history(invoked=True, outcome=ObservedOutcome.FAILED) == (
        ActionState.INVOKED,
        ActionState.FAILED,
    )
    assert action_state_history(invoked=True, outcome=ObservedOutcome.TIMEOUT) == (
        ActionState.INVOKED,
        ActionState.TIMEOUT,
    )
    assert action_state_history(invoked=True, outcome=ObservedOutcome.CANCELLED) == (
        ActionState.INVOKED,
        ActionState.CANCELLED,
    )
    assert action_state_history(invoked=True, outcome=ObservedOutcome.UNKNOWN) == (
        ActionState.INVOKED,
        ActionState.UNKNOWN,
    )


def test_patch_state_machine_never_applies_without_explicit_success() -> None:
    assert patch_state_history(proposed=True) == (PatchState.PATCH_PROPOSED,)
    assert patch_state_history(apply_invoked=True) == (PatchState.APPLY_INVOKED,)
    assert patch_state_history(apply_invoked=True, observed_success=True) == (
        PatchState.APPLY_INVOKED,
        PatchState.APPLIED,
    )
    assert patch_state_history(apply_invoked=True, observed_success=False) == (
        PatchState.APPLY_INVOKED,
        PatchState.FAILED,
    )


def test_validation_state_machine_requires_exact_integer_exit_and_bound_target() -> None:
    assert validation_state_history(mentioned=True) == (ValidationState.MENTIONED,)
    assert validation_state_history(command_invoked=True, target_bound=True) == (
        ValidationState.COMMAND_INVOKED,
    )
    assert validation_state_history(
        command_invoked=True,
        exit_code=0,
        exit_observed=True,
        target_bound=True,
    ) == (
        ValidationState.COMMAND_INVOKED,
        ValidationState.EXIT_OBSERVED,
        ValidationState.PASSED,
    )
    assert validation_state_history(
        command_invoked=True,
        exit_code=2,
        exit_observed=True,
        target_bound=True,
    ) == (
        ValidationState.COMMAND_INVOKED,
        ValidationState.EXIT_OBSERVED,
        ValidationState.FAILED,
    )
    assert validation_state_history(
        command_invoked=True,
        exit_code=0,
        exit_observed=True,
        target_bound=False,
    ) == (
        ValidationState.COMMAND_INVOKED,
        ValidationState.EXIT_OBSERVED,
        ValidationState.TARGET_UNKNOWN,
    )


@pytest.mark.parametrize("bad_exit", [True, False, "0", 0.0])
def test_validation_state_machine_rejects_non_integer_exit_codes(bad_exit: object) -> None:
    with pytest.raises(CodexEventStateError, match="exact integer"):
        validation_state_history(
            command_invoked=True,
            exit_code=bad_exit,  # type: ignore[arg-type]
            exit_observed=True,
            target_bound=True,
        )


def test_impossible_state_combinations_are_rejected() -> None:
    with pytest.raises(CodexEventStateError):
        action_state_history(proposed=True, invoked=True)
    with pytest.raises(CodexEventStateError):
        patch_state_history(proposed=True, observed_success=True)
    with pytest.raises(CodexEventStateError):
        validation_state_history(mentioned=True, command_invoked=True)
    with pytest.raises(CodexEventStateError):
        validation_state_history(command_invoked=True, exit_code=0)
