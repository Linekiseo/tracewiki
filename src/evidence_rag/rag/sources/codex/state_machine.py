"""Pure fail-closed state transitions for normalized Codex observations."""

from __future__ import annotations

from .contracts import (
    CODEX_EVENT_STATE_MACHINE_VERSION,
    ActionState,
    ObservedOutcome,
    PatchState,
    ValidationState,
)


class CodexEventStateError(ValueError):
    """An impossible or contradictory transition was requested."""


def action_state_history(
    *,
    proposed: bool = False,
    invoked: bool = False,
    outcome: ObservedOutcome | None = None,
) -> tuple[ActionState, ...]:
    """Return the only legal deterministic Action history for observed inputs."""

    if proposed:
        if invoked or outcome is not None:
            raise CodexEventStateError("a proposal cannot also be an observed invocation")
        return (ActionState.PROPOSED,)
    if not invoked:
        raise CodexEventStateError("an action requires a proposal or invocation")
    if outcome is None:
        return (ActionState.INVOKED,)
    final = {
        ObservedOutcome.COMPLETED: ActionState.COMPLETED,
        ObservedOutcome.FAILED: ActionState.FAILED,
        ObservedOutcome.TIMEOUT: ActionState.TIMEOUT,
        ObservedOutcome.CANCELLED: ActionState.CANCELLED,
        ObservedOutcome.UNKNOWN: ActionState.UNKNOWN,
    }[outcome]
    return (ActionState.INVOKED, final)


def patch_state_history(
    *,
    proposed: bool = False,
    apply_invoked: bool = False,
    observed_success: bool | None = None,
) -> tuple[PatchState, ...]:
    """Return a Patch history without treating text or a missing result as success."""

    if proposed:
        if apply_invoked or observed_success is not None:
            raise CodexEventStateError("a patch proposal cannot also be an apply observation")
        return (PatchState.PATCH_PROPOSED,)
    if not apply_invoked:
        raise CodexEventStateError("a patch requires a proposal or apply invocation")
    if observed_success is None:
        return (PatchState.APPLY_INVOKED,)
    final = PatchState.APPLIED if observed_success else PatchState.FAILED
    return (PatchState.APPLY_INVOKED, final)


def validation_state_history(
    *,
    mentioned: bool = False,
    command_invoked: bool = False,
    exit_code: int | None = None,
    exit_observed: bool = False,
    target_bound: bool | None = None,
) -> tuple[ValidationState, ...]:
    """Return a strict Validation history.

    ``bool`` is deliberately rejected even though it is an ``int`` subclass.
    Unknown targets remain ``target_unknown`` and can never become ``passed``.
    """

    if mentioned:
        if command_invoked or exit_observed or exit_code is not None or target_bound is not None:
            raise CodexEventStateError("a mention cannot also be an observed command")
        return (ValidationState.MENTIONED,)
    if not command_invoked:
        raise CodexEventStateError("validation requires a mention or command invocation")
    history = [ValidationState.COMMAND_INVOKED]
    if exit_observed:
        if type(exit_code) is not int:
            raise CodexEventStateError("an observed exit must be an exact integer")
        history.append(ValidationState.EXIT_OBSERVED)
    elif exit_code is not None:
        raise CodexEventStateError("an exit code cannot exist without observed exit evidence")
    if target_bound is False:
        history.append(ValidationState.TARGET_UNKNOWN)
        return tuple(history)
    if not exit_observed:
        return tuple(history)
    history.append(ValidationState.PASSED if exit_code == 0 else ValidationState.FAILED)
    return tuple(history)


__all__ = [
    "CODEX_EVENT_STATE_MACHINE_VERSION",
    "CodexEventStateError",
    "action_state_history",
    "patch_state_history",
    "validation_state_history",
]
