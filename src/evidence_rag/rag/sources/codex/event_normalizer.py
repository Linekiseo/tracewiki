"""Deterministic observable-event normalizer for Codex CX1-01.

This module has no storage, runtime, database, clock, or randomness dependency.
It never mutates a source Item and never parses assistant prose as execution
truth.  Only exact-scope call/result pairs can advance an observed state.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

from .contracts import (
    CODEX_EVENT_CONTRACT_VERSION,
    CODEX_EVENT_NORMALIZER_VERSION,
    CODEX_EVENT_STATE_MACHINE_VERSION,
    ActionState,
    CodexEventNormalizationResult,
    DerivedEventKind,
    DerivedEventRole,
    DiagnosticCode,
    DiagnosticSeverity,
    EventReasonCode,
    EventScope,
    EvidenceRef,
    LinkRole,
    NormalizationDiagnostic,
    NormalizationSummary,
    NormalizedActionEvent,
    NormalizedEvent,
    NormalizedEventLink,
    NormalizedObservationEvent,
    NormalizedPatchEvent,
    NormalizedValidationEvent,
    ObservableCodexItem,
    ObservableEvidenceType,
    ObservableItemType,
    ObservableRole,
    ObservationKind,
    ObservedOutcome,
    StateCount,
    ValidationState,
    canonical_sha256,
)
from .state_machine import (
    action_state_history,
    patch_state_history,
    validation_state_history,
)

_MAX_DIAGNOSTICS = 256
_MAX_DIAGNOSTIC_SOURCE_IDS = 8
_EXIT_MAX_DEPTH = 6
_EXIT_MAX_NODES = 128
_CREDENTIAL_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s\"']{8,}))"
)
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_ENCODED_ABSOLUTE_RE = re.compile(
    r"%2f(?:users|home|private|var|tmp|etc)%2f|[A-Za-z]%3a%5c",
    re.IGNORECASE,
)
_SHELL_META_RE = re.compile(r"(?:&&|\|\||[;|`<>]|\$\(|\$\{)")
_SELECTOR_RE = re.compile(r"^[A-Za-z0-9_.*,:+\-\[\]/]+$")

_INVOCATION_TYPES = frozenset(
    {
        ObservableItemType.TOOL_CALL,
        ObservableItemType.COMMAND_EXECUTION,
        ObservableItemType.PATCH_APPLY,
    }
)
_RESULT_TYPES = frozenset(
    {
        ObservableItemType.TOOL_RESULT,
        ObservableItemType.COMMAND_RESULT,
        ObservableItemType.PATCH_RESULT,
    }
)
_NON_EVIDENCE_TYPES = frozenset(
    {
        ObservableItemType.REASONING,
        ObservableItemType.AGENT_REASONING,
        ObservableItemType.TOKEN_COUNT,
        ObservableItemType.SYSTEM_MESSAGE,
        ObservableItemType.DEVELOPER_MESSAGE,
    }
)
_NON_EVIDENCE_ROLES = frozenset({ObservableRole.SYSTEM, ObservableRole.DEVELOPER})


def _scope_key(scope: EventScope) -> tuple[str, ...]:
    return (
        scope.project_id,
        scope.source_id,
        scope.generation_id,
        scope.thread_id,
        scope.turn_id,
        scope.acl_ref,
    )


def _item_key(item: ObservableCodexItem) -> tuple[object, ...]:
    return (
        *_scope_key(item.scope),
        item.observable_order,
        item.item_id,
        item.source_locator,
        item.canonical_sha256(),
    )


def _identity_key(item: ObservableCodexItem) -> tuple[object, ...]:
    return (*_scope_key(item.scope), item.item_id)


def _call_type(item: ObservableCodexItem) -> str:
    if item.item_type is ObservableItemType.COMMAND_EXECUTION:
        return "command"
    if item.item_type in {ObservableItemType.PATCH, ObservableItemType.PATCH_APPLY}:
        return "patch_apply"
    if item.call_type is not None:
        return item.call_type
    if item.tool_name is not None:
        return item.tool_name
    return "tool"


def _is_patch_invocation(item: ObservableCodexItem) -> bool:
    return item.item_type is ObservableItemType.PATCH_APPLY or (
        item.item_type is ObservableItemType.PATCH and item.call_id is not None
    )


def _is_invocation(item: ObservableCodexItem) -> bool:
    return item.item_type in _INVOCATION_TYPES or _is_patch_invocation(item)


def _has_role_provenance_conflict(item: ObservableCodexItem) -> bool:
    if item.item_type in _RESULT_TYPES or _is_invocation(item):
        return item.role not in {None, ObservableRole.TOOL}
    if item.item_type is ObservableItemType.AGENT_MESSAGE:
        return item.role not in {None, ObservableRole.ASSISTANT}
    if item.item_type is ObservableItemType.PLAN:
        return item.role not in {None, ObservableRole.ASSISTANT}
    return False


def _result_compatible(call: ObservableCodexItem, result: ObservableCodexItem) -> bool:
    call_type = _call_type(call)
    if result.item_type is ObservableItemType.COMMAND_RESULT:
        return call_type == "command" and result.call_type in {None, "command"}
    if result.item_type is ObservableItemType.PATCH_RESULT:
        return call_type == "patch_apply" and result.call_type in {None, "patch_apply"}
    if result.item_type is not ObservableItemType.TOOL_RESULT:
        return False
    return result.call_type is None or result.call_type == call_type


def _evidence_type(item: ObservableCodexItem) -> ObservableEvidenceType:
    if _has_role_provenance_conflict(item):
        return ObservableEvidenceType.CONTRADICTORY_PROVENANCE
    return {
        ObservableItemType.ACTION_PROPOSAL: ObservableEvidenceType.ACTION_PROPOSAL,
        ObservableItemType.TOOL_CALL: ObservableEvidenceType.TOOL_INVOCATION,
        ObservableItemType.COMMAND_EXECUTION: ObservableEvidenceType.COMMAND_INVOCATION,
        ObservableItemType.TOOL_RESULT: ObservableEvidenceType.TOOL_RESULT,
        ObservableItemType.COMMAND_RESULT: ObservableEvidenceType.COMMAND_RESULT,
        ObservableItemType.PATCH: (
            ObservableEvidenceType.PATCH_APPLY_INVOCATION
            if _is_patch_invocation(item)
            else ObservableEvidenceType.PATCH_PROPOSAL
        ),
        ObservableItemType.PATCH_APPLY: ObservableEvidenceType.PATCH_APPLY_INVOCATION,
        ObservableItemType.PATCH_RESULT: ObservableEvidenceType.PATCH_RESULT,
        ObservableItemType.FILE_CHANGE: ObservableEvidenceType.FILE_CHANGE,
        ObservableItemType.VALIDATION_MENTION: ObservableEvidenceType.VALIDATION_MENTION,
        ObservableItemType.PLAN: ObservableEvidenceType.PLAN,
        ObservableItemType.AGENT_MESSAGE: ObservableEvidenceType.ASSISTANT_MESSAGE,
        ObservableItemType.VALIDATION_RESULT: (ObservableEvidenceType.EXISTING_DERIVED_VALIDATION),
    }[item.item_type]


def _evidence(*items: ObservableCodexItem) -> tuple[EvidenceRef, ...]:
    refs = {
        (item.source_locator, item.item_id, _evidence_type(item).value): EvidenceRef(
            source_item_id=item.item_id,
            source_locator=item.source_locator,
            evidence_type=_evidence_type(item),
        )
        for item in items
    }
    return tuple(refs[key] for key in sorted(refs))


def _event_id(payload: Mapping[str, object]) -> str:
    return canonical_sha256(
        {
            "contract_version": CODEX_EVENT_CONTRACT_VERSION,
            "normalizer_version": CODEX_EVENT_NORMALIZER_VERSION,
            **payload,
        }
    )


def _diag(
    diagnostics: list[NormalizationDiagnostic],
    code: DiagnosticCode,
    severity: DiagnosticSeverity,
    scope: EventScope,
    *,
    items: Iterable[ObservableCodexItem] = (),
    call_id: str | None = None,
    occurrences: int = 1,
) -> None:
    source_ids = tuple(sorted({item.item_id for item in items}))[:_MAX_DIAGNOSTIC_SOURCE_IDS]
    diagnostics.append(
        NormalizationDiagnostic(
            code=code,
            severity=severity,
            scope=scope,
            source_item_ids=source_ids,
            call_id=call_id,
            occurrences=occurrences,
        )
    )


def _deduplicate_diagnostics(
    diagnostics: Iterable[NormalizationDiagnostic],
) -> tuple[tuple[NormalizationDiagnostic, ...], int]:
    grouped: dict[tuple[object, ...], int] = defaultdict(int)
    examples: dict[tuple[object, ...], NormalizationDiagnostic] = {}
    for diagnostic in diagnostics:
        key = (
            diagnostic.code.value,
            diagnostic.severity.value,
            *_scope_key(diagnostic.scope),
            diagnostic.source_item_ids,
            diagnostic.call_id or "",
        )
        grouped[key] += diagnostic.occurrences
        examples[key] = diagnostic
    ordered = [
        examples[key].model_copy(update={"occurrences": grouped[key]}) for key in sorted(grouped)
    ]
    if len(ordered) <= _MAX_DIAGNOSTICS:
        return tuple(ordered), 0
    kept = ordered[: _MAX_DIAGNOSTICS - 1]
    omitted = len(ordered) - len(kept)
    anchor = ordered[_MAX_DIAGNOSTICS - 1]
    kept.append(
        NormalizationDiagnostic(
            code=DiagnosticCode.DIAGNOSTICS_TRUNCATED,
            severity=DiagnosticSeverity.WARNING,
            scope=anchor.scope,
            occurrences=omitted,
        )
    )
    return tuple(kept), omitted


def _canonical_argv(
    item: ObservableCodexItem,
) -> tuple[tuple[str, ...], DiagnosticCode | None, bool]:
    argv = item.command_argv
    if item.shell:
        return (), DiagnosticCode.SHELL_COMMAND_REJECTED, False
    if not argv:
        return (), DiagnosticCode.COMMAND_NON_CANONICAL, False
    for argument in argv:
        if (
            "\\" in argument
            or _SHELL_META_RE.search(argument)
            or _CREDENTIAL_RE.search(argument)
            or _WINDOWS_ABSOLUTE_RE.match(argument)
            or argument.startswith(("/", "~"))
            or "file://" in argument.casefold()
            or _ENCODED_ABSOLUTE_RE.search(argument)
        ):
            return (), DiagnosticCode.COMMAND_NON_CANONICAL, False
        pathish = argument.split("=", 1)[-1]
        if (
            pathish.startswith(("/", "~"))
            or _WINDOWS_ABSOLUTE_RE.match(pathish)
            or "file://" in pathish.casefold()
            or "/" in pathish
            and ".." in PurePosixPath(pathish).parts
        ):
            return (), DiagnosticCode.COMMAND_NON_CANONICAL, False

    executable = argv[0]
    allowlisted = False
    if (
        executable in {"pytest", "unittest"}
        or executable in {"python", "python3"}
        and argv[1:3] == ("-m", "unittest")
    ):
        allowlisted = True
    elif executable == "uv" and len(argv) >= 3 and argv[1] == "run":
        nested = argv[2:]
        allowlisted = (
            nested[0] in {"pytest", "mypy", "pyright", "eslint", "tsc"}
            or nested[:3] in {("python", "-m", "unittest"), ("python3", "-m", "unittest")}
            or (len(nested) >= 2 and nested[0] == "ruff" and nested[1] in {"check", "format"})
        )
    elif (
        executable == "ruff"
        and len(argv) >= 2
        and argv[1] in {"check", "format"}
        or executable in {"mypy", "pyright", "eslint", "tsc"}
    ):
        allowlisted = True
    elif executable in {"npm", "pnpm", "yarn", "bun"}:
        tail = argv[1:]
        if tail[:1] == ("run",):
            tail = tail[1:]
        allowlisted = bool(tail and tail[0] in {"test", "lint", "typecheck"})
    elif argv[:2] in {("go", "test"), ("cargo", "test")}:
        allowlisted = True
    elif executable in {"mvn", "gradle", "./gradlew"} and len(argv) >= 2:
        allowlisted = argv[1] in {"test", "check"}
    elif executable == "make" and len(argv) >= 2:
        allowlisted = argv[1] in {"test", "check", "lint"}
    elif executable == "xcodebuild":
        allowlisted = "test" in argv[1:]
    return tuple(argv), None, allowlisted


def _canonical_targets(
    item: ObservableCodexItem,
) -> tuple[tuple[str, ...] | None, DiagnosticCode | None]:
    if item.target_project_id is not None and item.target_project_id != item.scope.project_id:
        return None, DiagnosticCode.TARGET_PROJECT_MISMATCH
    if not item.targets:
        return None, DiagnosticCode.TARGET_UNKNOWN
    normalized: list[str] = []
    for target in item.targets:
        canonical = _canonical_target_argument(target)
        if canonical is None:
            return None, DiagnosticCode.TARGET_REJECTED
        normalized.append(canonical)
    return tuple(sorted(set(normalized))), None


def _canonical_target_argument(target: str) -> str | None:
    if (
        "\\" in target
        or target.startswith(("/", "~"))
        or _WINDOWS_ABSOLUTE_RE.match(target)
        or "://" in target
        or _ENCODED_ABSOLUTE_RE.search(target)
        or _CREDENTIAL_RE.search(target)
    ):
        return None
    path_text, separator, selector = target.partition("::")
    path = PurePosixPath(path_text)
    if (
        path.is_absolute()
        or not path.parts
        or path.as_posix() != path_text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return None
    if separator and (
        not selector
        or len(selector) > 512
        or not _SELECTOR_RE.fullmatch(selector)
        or ".." in selector
    ):
        return None
    return target


_PYTEST_FLAGS = frozenset(
    {
        "-q",
        "--quiet",
        "-v",
        "--verbose",
        "-x",
        "--exitfirst",
        "-s",
        "--collect-only",
        "--continue-on-collection-errors",
        "--keep-duplicates",
        "--showlocals",
        "--no-header",
        "--no-summary",
        "--strict-config",
        "--strict-markers",
        "--disable-warnings",
        "--setup-only",
        "--setup-show",
        "--setup-plan",
    }
)
_PYTEST_VALUE_OPTIONS = frozenset(
    {
        "-k",
        "-m",
        "-c",
        "--maxfail",
        "--rootdir",
        "--confcutdir",
        "--basetemp",
        "--capture",
        "--tb",
        "--color",
        "--code-highlight",
        "--import-mode",
        "--durations",
        "--durations-min",
        "--junitxml",
        "--junit-prefix",
        "--ignore",
        "--ignore-glob",
        "--deselect",
    }
)
_UNITTEST_FLAGS = frozenset(
    {
        "-v",
        "--verbose",
        "-q",
        "--quiet",
        "-f",
        "--failfast",
        "-c",
        "--catch",
        "-b",
        "--buffer",
        "--locals",
    }
)
_UNITTEST_VALUE_OPTIONS = frozenset({"-k"})
_RUFF_FLAGS = frozenset(
    {
        "-q",
        "--quiet",
        "-v",
        "--verbose",
        "--fix",
        "--unsafe-fixes",
        "--diff",
        "--watch",
        "--no-cache",
        "--respect-gitignore",
        "--force-exclude",
        "--preview",
        "--silent",
    }
)
_RUFF_VALUE_OPTIONS = frozenset(
    {
        "--config",
        "--select",
        "--ignore",
        "--extend-select",
        "--extend-ignore",
        "--exclude",
        "--extend-exclude",
        "--output-format",
        "--target-version",
        "--line-length",
    }
)


def _positional_targets(
    arguments: tuple[str, ...],
    *,
    flags: frozenset[str],
    value_options: frozenset[str],
) -> tuple[tuple[str, ...] | None, DiagnosticCode | None]:
    positional: list[str] = []
    index = 0
    literal = False
    while index < len(arguments):
        argument = arguments[index]
        if literal:
            positional.append(argument)
            index += 1
            continue
        if argument == "--":
            literal = True
            index += 1
            continue
        if argument in flags or re.fullmatch(r"-[qvx]{2,}", argument):
            index += 1
            continue
        if argument in value_options:
            if index + 1 >= len(arguments):
                return None, DiagnosticCode.TARGET_ARGUMENT_UNSUPPORTED
            index += 2
            continue
        if argument.startswith("--") and "=" in argument:
            option = argument.split("=", 1)[0]
            if option not in value_options:
                return None, DiagnosticCode.TARGET_ARGUMENT_UNSUPPORTED
            index += 1
            continue
        if argument.startswith("-"):
            return None, DiagnosticCode.TARGET_ARGUMENT_UNSUPPORTED
        positional.append(argument)
        index += 1

    if not positional:
        return None, DiagnosticCode.TARGET_ABSENT
    normalized: list[str] = []
    for target in positional:
        canonical = _canonical_target_argument(target)
        if canonical is None:
            return None, DiagnosticCode.TARGET_REJECTED
        normalized.append(canonical)
    return tuple(sorted(set(normalized))), None


def _argv_validation_targets(
    argv: tuple[str, ...],
) -> tuple[tuple[str, ...] | None, DiagnosticCode | None]:
    command = argv
    if command[:2] == ("uv", "run"):
        command = command[2:]
    if not command:
        return None, DiagnosticCode.TARGET_ABSENT
    if command[0] == "pytest":
        return _positional_targets(
            command[1:],
            flags=_PYTEST_FLAGS,
            value_options=_PYTEST_VALUE_OPTIONS,
        )
    if command[0] in {"python", "python3"} and command[1:3] == ("-m", "unittest"):
        command = ("unittest", *command[3:])
    if command[0] == "unittest":
        if command[1:2] == ("discover",):
            return None, DiagnosticCode.TARGET_ARGUMENT_UNSUPPORTED
        return _positional_targets(
            command[1:],
            flags=_UNITTEST_FLAGS,
            value_options=_UNITTEST_VALUE_OPTIONS,
        )
    if command[:2] in {("ruff", "check"), ("ruff", "format")}:
        return _positional_targets(
            command[2:],
            flags=_RUFF_FLAGS,
            value_options=_RUFF_VALUE_OPTIONS,
        )
    if command[0] in {"mypy", "pyright", "eslint", "tsc"}:
        return _positional_targets(command[1:], flags=frozenset(), value_options=frozenset())
    if command[:2] == ("go", "test"):
        return _positional_targets(command[2:], flags=frozenset(), value_options=frozenset())
    if command[:2] == ("cargo", "test"):
        return (("test",), None)
    if command[0] in {"npm", "pnpm", "yarn", "bun"}:
        tail = command[1:]
        if tail[:1] == ("run",):
            tail = tail[1:]
        return ((tail[0],), None) if tail else (None, DiagnosticCode.TARGET_ABSENT)
    if command[0] in {"mvn", "gradle", "./gradlew", "make"} and len(command) >= 2:
        return ((command[1],), None)
    return None, DiagnosticCode.TARGET_ARGUMENT_UNSUPPORTED


def _bound_validation_targets(
    item: ObservableCodexItem,
    argv: tuple[str, ...],
) -> tuple[tuple[str, ...] | None, DiagnosticCode | None]:
    actual, actual_error = _argv_validation_targets(argv)
    if actual_error is not None:
        return None, actual_error
    declared, declared_error = _canonical_targets(item)
    if declared_error is not None:
        if declared_error is DiagnosticCode.TARGET_UNKNOWN:
            return None, DiagnosticCode.TARGET_ABSENT
        return None, declared_error
    if declared != actual:
        return None, DiagnosticCode.TARGET_MISMATCH
    return actual, None


def _observed_exit_code(
    result: ObservableCodexItem,
) -> tuple[int | None, DiagnosticCode | None]:
    accepts_integer_exit = result.item_type in {
        ObservableItemType.TOOL_RESULT,
        ObservableItemType.COMMAND_RESULT,
    }

    candidates: list[int] = []
    invalid = False
    bounded = False
    remaining = _EXIT_MAX_NODES
    unsupported_exit_keys = frozenset({"exit", "exitcode", "returncode", "statuscode"})

    def visit(value: object, depth: int) -> None:
        nonlocal invalid, bounded, remaining
        if remaining <= 0 or depth > _EXIT_MAX_DEPTH:
            bounded = True
            return
        remaining -= 1
        if isinstance(value, dict):
            for key in sorted(value):
                nested = value[key]
                if key == "exit_code":
                    if type(nested) is int and accepts_integer_exit:
                        candidates.append(nested)
                    else:
                        invalid = True
                    continue
                normalized_key = key.casefold().replace("_", "").replace("-", "")
                if normalized_key in unsupported_exit_keys:
                    invalid = True
                    continue
                visit(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value:
                visit(nested, depth + 1)

    visit(result.payload, 0)
    if bounded:
        return None, DiagnosticCode.EXIT_PAYLOAD_BOUNDED
    if len(candidates) > 1:
        return None, DiagnosticCode.AMBIGUOUS_EXIT_CODE
    if invalid:
        return None, DiagnosticCode.INVALID_EXIT_CODE
    if len(candidates) == 1:
        return candidates[0], None
    return None, DiagnosticCode.MISSING_EXIT_CODE


def _outcome_from_result(
    result: ObservableCodexItem,
    exit_code: int | None,
) -> ObservedOutcome:
    if result.outcome is not None:
        return result.outcome
    if result.success is not None:
        return ObservedOutcome.COMPLETED if result.success else ObservedOutcome.FAILED
    if exit_code is not None:
        return ObservedOutcome.COMPLETED if exit_code == 0 else ObservedOutcome.FAILED
    return ObservedOutcome.UNKNOWN


def _patch_terminal_truth(
    result: ObservableCodexItem,
    exit_observation: tuple[int | None, DiagnosticCode | None],
) -> tuple[bool | None, DiagnosticCode | None]:
    signals: list[bool] = []
    if result.success is not None:
        signals.append(result.success)
    if result.outcome is ObservedOutcome.COMPLETED:
        signals.append(True)
    elif result.outcome in {
        ObservedOutcome.FAILED,
        ObservedOutcome.TIMEOUT,
        ObservedOutcome.CANCELLED,
    }:
        signals.append(False)
    exit_code, exit_error = exit_observation
    if exit_code is not None:
        signals.append(exit_code == 0)
    unknown_signal = result.outcome is ObservedOutcome.UNKNOWN or exit_error in {
        DiagnosticCode.INVALID_EXIT_CODE,
        DiagnosticCode.AMBIGUOUS_EXIT_CODE,
        DiagnosticCode.EXIT_PAYLOAD_BOUNDED,
    }
    if unknown_signal:
        diagnostic = (
            DiagnosticCode.PATCH_TERMINAL_CONFLICT
            if signals
            else DiagnosticCode.PATCH_RESULT_UNKNOWN
        )
        return None, diagnostic
    if not signals:
        return None, DiagnosticCode.PATCH_RESULT_UNKNOWN
    if len(set(signals)) != 1:
        return None, DiagnosticCode.PATCH_TERMINAL_CONFLICT
    return signals[0], None


def _action_reason(outcome: ObservedOutcome | None) -> EventReasonCode:
    return {
        None: EventReasonCode.ACTION_INVOCATION_OBSERVED,
        ObservedOutcome.COMPLETED: EventReasonCode.RESULT_COMPLETION_OBSERVED,
        ObservedOutcome.FAILED: EventReasonCode.RESULT_FAILURE_OBSERVED,
        ObservedOutcome.TIMEOUT: EventReasonCode.RESULT_TIMEOUT_OBSERVED,
        ObservedOutcome.CANCELLED: EventReasonCode.RESULT_CANCELLATION_OBSERVED,
        ObservedOutcome.UNKNOWN: EventReasonCode.RESULT_OUTCOME_UNKNOWN,
    }[outcome]


def _observation_event(
    item: ObservableCodexItem,
    *,
    role: DerivedEventRole,
    observation: ObservationKind,
    reason: EventReasonCode,
) -> NormalizedObservationEvent:
    evidence = _evidence(item)
    identity = {
        "kind": DerivedEventKind.OBSERVATION.value,
        "scope": item.scope.model_dump(mode="json"),
        "role": role.value,
        "observation": observation.value,
        "reason_code": reason.value,
        "evidence": [ref.model_dump(mode="json") for ref in evidence],
    }
    return NormalizedObservationEvent(
        event_id=_event_id(identity),
        scope=item.scope,
        role=role,
        observation=observation,
        reason_code=reason,
        evidence=evidence,
    )


def _state_counts(values: Iterable[str]) -> tuple[StateCount, ...]:
    counts = Counter(values)
    return tuple(StateCount(state=state, count=counts[state]) for state in sorted(counts))


def _locator_order(locator: str) -> int:
    return int(locator.rsplit("#event=", 1)[1])


def _event_sort_key(event: NormalizedEvent) -> tuple[object, ...]:
    first = event.evidence[0]
    return (
        *_scope_key(event.scope),
        min(_locator_order(item.source_locator) for item in event.evidence),
        first.source_locator,
        first.source_item_id,
        event.kind.value,
        event.role.value,
        event.event_id,
    )


def normalize_codex_events_v1(
    records: Iterable[ObservableCodexItem | Mapping[str, Any]],
) -> CodexEventNormalizationResult:
    """Normalize sanitized records into immutable, fail-closed derived facts."""

    raw_items = tuple(
        record
        if isinstance(record, ObservableCodexItem)
        else ObservableCodexItem.model_validate(record)
        for record in records
    )
    exact_unique = {item.canonical_sha256(): item for item in raw_items}
    exact_items = tuple(sorted(exact_unique.values(), key=_item_key))
    diagnostics: list[NormalizationDiagnostic] = []

    by_identity: dict[tuple[object, ...], list[ObservableCodexItem]] = defaultdict(list)
    for item in exact_items:
        by_identity[_identity_key(item)].append(item)
    conflicting_ids: set[tuple[object, ...]] = set()
    for identity, values in sorted(by_identity.items()):
        if len(values) <= 1:
            continue
        conflicting_ids.add(identity)
        code = (
            DiagnosticCode.DUPLICATE_RESULT
            if all(value.item_type in _RESULT_TYPES for value in values)
            else DiagnosticCode.DUPLICATE_ITEM_ID
        )
        _diag(
            diagnostics,
            code,
            DiagnosticSeverity.ERROR,
            values[0].scope,
            items=values,
            occurrences=len(values),
        )

    items = tuple(item for item in exact_items if _identity_key(item) not in conflicting_ids)
    excluded: list[ObservableCodexItem] = []
    observable: list[ObservableCodexItem] = []
    for item in items:
        if _has_role_provenance_conflict(item):
            observable.append(item)
            _diag(
                diagnostics,
                DiagnosticCode.ROLE_PROVENANCE_CONFLICT,
                DiagnosticSeverity.ERROR,
                item.scope,
                items=(item,),
                call_id=item.call_id,
            )
        elif item.item_type in _NON_EVIDENCE_TYPES or item.role in _NON_EVIDENCE_ROLES:
            excluded.append(item)
            _diag(
                diagnostics,
                DiagnosticCode.NON_EVIDENCE_EXCLUDED,
                DiagnosticSeverity.INFO,
                item.scope,
                items=(item,),
            )
        else:
            observable.append(item)

    calls = tuple(
        item
        for item in observable
        if _is_invocation(item) and not _has_role_provenance_conflict(item)
    )
    results = tuple(
        item
        for item in observable
        if item.item_type in _RESULT_TYPES and not _has_role_provenance_conflict(item)
    )
    linked_by_call: dict[tuple[object, ...], ObservableCodexItem] = {}
    linked_result_ids: set[tuple[object, ...]] = set()
    candidates_by_call: dict[tuple[object, ...], list[ObservableCodexItem]] = defaultdict(list)
    call_groups: dict[tuple[object, ...], list[ObservableCodexItem]] = defaultdict(list)
    for call in calls:
        if call.call_id is not None:
            call_groups[(*_scope_key(call.scope), call.call_id, _call_type(call))].append(call)
    ambiguous_call_groups = {key for key, values in call_groups.items() if len(values) > 1}
    for key in sorted(ambiguous_call_groups):
        values = call_groups[key]
        _diag(
            diagnostics,
            DiagnosticCode.AMBIGUOUS_CALL,
            DiagnosticSeverity.ERROR,
            values[0].scope,
            items=values,
            call_id=values[0].call_id,
            occurrences=len(values),
        )

    for result in results:
        if result.call_id is None:
            _diag(
                diagnostics,
                DiagnosticCode.MISSING_CALL_ID,
                DiagnosticSeverity.ERROR,
                result.scope,
                items=(result,),
            )
            _diag(
                diagnostics,
                DiagnosticCode.UNLINKED_RESULT,
                DiagnosticSeverity.WARNING,
                result.scope,
                items=(result,),
            )
            continue
        same_scope_id = [
            call for call in calls if call.call_id == result.call_id and call.scope == result.scope
        ]
        compatible = [call for call in same_scope_id if _result_compatible(call, result)]
        if len(compatible) > 1:
            group_keys = {
                (*_scope_key(call.scope), call.call_id, _call_type(call)) for call in compatible
            }
            if not group_keys.issubset(ambiguous_call_groups):
                _diag(
                    diagnostics,
                    DiagnosticCode.AMBIGUOUS_CALL,
                    DiagnosticSeverity.ERROR,
                    result.scope,
                    items=(*compatible, result),
                    call_id=result.call_id,
                )
            continue
        if not compatible:
            global_compatible = [
                call
                for call in calls
                if call.call_id == result.call_id and _result_compatible(call, result)
            ]
            if global_compatible:
                code = DiagnosticCode.SCOPE_MISMATCH
                if any(
                    call.scope.generation_id != result.scope.generation_id
                    for call in global_compatible
                ):
                    code = DiagnosticCode.CROSS_GENERATION_RESULT
                elif any(
                    call.scope.thread_id != result.scope.thread_id for call in global_compatible
                ):
                    code = DiagnosticCode.CROSS_THREAD_RESULT
                _diag(
                    diagnostics,
                    code,
                    DiagnosticSeverity.ERROR,
                    result.scope,
                    items=(*global_compatible, result),
                    call_id=result.call_id,
                )
            elif same_scope_id:
                _diag(
                    diagnostics,
                    DiagnosticCode.CALL_TYPE_MISMATCH,
                    DiagnosticSeverity.ERROR,
                    result.scope,
                    items=(*same_scope_id, result),
                    call_id=result.call_id,
                )
            _diag(
                diagnostics,
                DiagnosticCode.UNLINKED_RESULT,
                DiagnosticSeverity.WARNING,
                result.scope,
                items=(result,),
                call_id=result.call_id,
            )
            continue
        call = compatible[0]
        if result.observable_order <= call.observable_order:
            _diag(
                diagnostics,
                DiagnosticCode.OUT_OF_ORDER_RESULT,
                DiagnosticSeverity.ERROR,
                result.scope,
                items=(call, result),
                call_id=result.call_id,
            )
            _diag(
                diagnostics,
                DiagnosticCode.UNLINKED_RESULT,
                DiagnosticSeverity.WARNING,
                result.scope,
                items=(result,),
                call_id=result.call_id,
            )
            continue
        candidates_by_call[_identity_key(call)].append(result)

    for call in calls:
        key = _identity_key(call)
        candidates = sorted(candidates_by_call.get(key, ()), key=_item_key)
        if len(candidates) > 1:
            _diag(
                diagnostics,
                DiagnosticCode.MULTIPLE_RESULTS,
                DiagnosticSeverity.ERROR,
                call.scope,
                items=(call, *candidates),
                call_id=call.call_id,
                occurrences=len(candidates),
            )
            continue
        if len(candidates) == 1:
            linked_by_call[key] = candidates[0]
            linked_result_ids.add(_identity_key(candidates[0]))

    for call in calls:
        if call.call_id is None:
            _diag(
                diagnostics,
                DiagnosticCode.MISSING_CALL_ID,
                DiagnosticSeverity.ERROR,
                call.scope,
                items=(call,),
            )
        if _identity_key(call) not in linked_by_call:
            _diag(
                diagnostics,
                DiagnosticCode.MISSING_RESULT,
                DiagnosticSeverity.WARNING,
                call.scope,
                items=(call,),
                call_id=call.call_id,
            )

    for result in results:
        if _identity_key(result) in linked_result_ids:
            continue
        if not any(
            diagnostic.code is DiagnosticCode.UNLINKED_RESULT
            and result.item_id in diagnostic.source_item_ids
            for diagnostic in diagnostics
        ):
            _diag(
                diagnostics,
                DiagnosticCode.UNLINKED_RESULT,
                DiagnosticSeverity.WARNING,
                result.scope,
                items=(result,),
                call_id=result.call_id,
            )

    exit_by_result: dict[tuple[object, ...], tuple[int | None, DiagnosticCode | None]] = {
        _identity_key(result): _observed_exit_code(result) for result in results
    }

    events: list[NormalizedEvent] = []
    links: list[NormalizedEventLink] = []
    for item in observable:
        if _has_role_provenance_conflict(item):
            events.append(
                _observation_event(
                    item,
                    role=DerivedEventRole.CONTRADICTORY_PROVENANCE,
                    observation=ObservationKind.CONTRADICTORY_PROVENANCE,
                    reason=EventReasonCode.ROLE_PROVENANCE_CONFLICT,
                )
            )
            continue

        if item.item_type is ObservableItemType.ACTION_PROPOSAL:
            history = action_state_history(proposed=True)
            evidence = _evidence(item)
            identity = {
                "kind": DerivedEventKind.ACTION.value,
                "scope": item.scope.model_dump(mode="json"),
                "role": DerivedEventRole.ACTION_PROPOSAL.value,
                "state_history": [state.value for state in history],
                "evidence": [ref.model_dump(mode="json") for ref in evidence],
            }
            events.append(
                NormalizedActionEvent(
                    event_id=_event_id(identity),
                    scope=item.scope,
                    role=DerivedEventRole.ACTION_PROPOSAL,
                    reason_code=EventReasonCode.ACTION_PROPOSAL_OBSERVED,
                    evidence=evidence,
                    state=history[-1],
                    state_history=history,
                )
            )
            continue

        if item.item_type is ObservableItemType.PATCH and not _is_patch_invocation(item):
            history = patch_state_history(proposed=True)
            targets, target_error = _canonical_targets(item)
            if target_error is not None:
                _diag(
                    diagnostics,
                    target_error,
                    DiagnosticSeverity.WARNING,
                    item.scope,
                    items=(item,),
                )
            evidence = _evidence(item)
            safe_targets = targets or ()
            identity = {
                "kind": DerivedEventKind.PATCH.value,
                "scope": item.scope.model_dump(mode="json"),
                "state_history": [state.value for state in history],
                "targets": list(safe_targets),
                "evidence": [ref.model_dump(mode="json") for ref in evidence],
            }
            events.append(
                NormalizedPatchEvent(
                    event_id=_event_id(identity),
                    scope=item.scope,
                    role=DerivedEventRole.PATCH_CHANGE,
                    reason_code=EventReasonCode.PATCH_PROPOSAL_OBSERVED,
                    evidence=evidence,
                    targets=safe_targets,
                    state=history[-1],
                    state_history=history,
                )
            )
            continue

        if _is_patch_invocation(item):
            result = linked_by_call.get(_identity_key(item))
            targets, target_error = _canonical_targets(item)
            observed_success: bool | None = None
            if result is not None:
                observed_success, terminal_diagnostic = _patch_terminal_truth(
                    result,
                    exit_by_result[_identity_key(result)],
                )
                if terminal_diagnostic is not None:
                    _diag(
                        diagnostics,
                        terminal_diagnostic,
                        (
                            DiagnosticSeverity.ERROR
                            if terminal_diagnostic is DiagnosticCode.PATCH_TERMINAL_CONFLICT
                            else DiagnosticSeverity.WARNING
                        ),
                        item.scope,
                        items=(item, result),
                        call_id=item.call_id,
                    )
            if targets is None and observed_success is True:
                observed_success = None
            if target_error is not None:
                _diag(
                    diagnostics,
                    target_error,
                    DiagnosticSeverity.WARNING,
                    item.scope,
                    items=(item,),
                    call_id=item.call_id,
                )
            history = patch_state_history(
                apply_invoked=True,
                observed_success=observed_success,
            )
            evidence = _evidence(item, *(() if result is None else (result,)))
            reason = EventReasonCode.PATCH_APPLY_INVOCATION_OBSERVED
            if observed_success is True:
                reason = EventReasonCode.PATCH_SUCCESS_OBSERVED
            elif observed_success is False:
                reason = EventReasonCode.PATCH_FAILURE_OBSERVED
            safe_targets = targets or ()
            identity = {
                "kind": DerivedEventKind.PATCH.value,
                "scope": item.scope.model_dump(mode="json"),
                "call_id": item.call_id,
                "call_type": _call_type(item),
                "state_history": [state.value for state in history],
                "targets": list(safe_targets),
                "evidence": [ref.model_dump(mode="json") for ref in evidence],
            }
            events.append(
                NormalizedPatchEvent(
                    event_id=_event_id(identity),
                    scope=item.scope,
                    role=DerivedEventRole.PATCH_CHANGE,
                    reason_code=reason,
                    evidence=evidence,
                    call_id=item.call_id,
                    call_type=_call_type(item),
                    targets=safe_targets,
                    state=history[-1],
                    state_history=history,
                )
            )
            continue

        if item.item_type in {
            ObservableItemType.TOOL_CALL,
            ObservableItemType.COMMAND_EXECUTION,
        }:
            result = linked_by_call.get(_identity_key(item))
            exit_code: int | None = None
            exit_error: DiagnosticCode | None = None
            outcome: ObservedOutcome | None = None
            if result is not None:
                exit_code, exit_error = exit_by_result[_identity_key(result)]
                outcome = _outcome_from_result(result, exit_code)
                if outcome is ObservedOutcome.UNKNOWN:
                    _diag(
                        diagnostics,
                        DiagnosticCode.RESULT_OUTCOME_UNKNOWN,
                        DiagnosticSeverity.WARNING,
                        item.scope,
                        items=(item, result),
                        call_id=item.call_id,
                    )
            history = action_state_history(invoked=True, outcome=outcome)
            argv: tuple[str, ...] = ()
            targets: tuple[str, ...] = ()
            if item.item_type is ObservableItemType.COMMAND_EXECUTION:
                argv, argv_error, allowlisted = _canonical_argv(item)
                if argv_error is None and allowlisted:
                    target_values, _ = _bound_validation_targets(item, argv)
                    targets = target_values or ()
                if argv_error is not None:
                    _diag(
                        diagnostics,
                        argv_error,
                        DiagnosticSeverity.WARNING,
                        item.scope,
                        items=(item,),
                        call_id=item.call_id,
                    )
            evidence = _evidence(item, *(() if result is None else (result,)))
            role = (
                DerivedEventRole.COMMAND_ACTION
                if item.item_type is ObservableItemType.COMMAND_EXECUTION
                else DerivedEventRole.TOOL_ACTION
            )
            identity = {
                "kind": DerivedEventKind.ACTION.value,
                "scope": item.scope.model_dump(mode="json"),
                "role": role.value,
                "call_id": item.call_id,
                "call_type": _call_type(item),
                "state_history": [state.value for state in history],
                "command_argv": list(argv),
                "targets": list(targets),
                "exit_code": exit_code,
                "evidence": [ref.model_dump(mode="json") for ref in evidence],
            }
            events.append(
                NormalizedActionEvent(
                    event_id=_event_id(identity),
                    scope=item.scope,
                    role=role,
                    reason_code=_action_reason(outcome),
                    evidence=evidence,
                    call_id=item.call_id,
                    call_type=_call_type(item),
                    targets=targets,
                    state=history[-1],
                    state_history=history,
                    command_argv=argv,
                    exit_code=exit_code,
                )
            )

            if item.item_type is not ObservableItemType.COMMAND_EXECUTION:
                continue
            argv, argv_error, allowlisted = _canonical_argv(item)
            if argv_error is not None:
                continue
            if not allowlisted:
                _diag(
                    diagnostics,
                    DiagnosticCode.COMMAND_NOT_ALLOWLISTED,
                    DiagnosticSeverity.INFO,
                    item.scope,
                    items=(item,),
                    call_id=item.call_id,
                )
                continue
            target_values, target_error = _bound_validation_targets(item, argv)
            target_bound = target_values is not None
            if target_error is not None:
                _diag(
                    diagnostics,
                    target_error,
                    DiagnosticSeverity.WARNING,
                    item.scope,
                    items=(item,),
                    call_id=item.call_id,
                )
            observed_exit = result is not None and exit_error is None and exit_code is not None
            if result is not None and exit_error is not None:
                _diag(
                    diagnostics,
                    exit_error,
                    DiagnosticSeverity.WARNING,
                    item.scope,
                    items=(item, result),
                    call_id=item.call_id,
                )
            history = validation_state_history(
                command_invoked=True,
                exit_code=exit_code if observed_exit else None,
                exit_observed=observed_exit,
                target_bound=target_bound,
            )
            reason = EventReasonCode.VALIDATION_COMMAND_INVOKED
            if not target_bound:
                reason = EventReasonCode.VALIDATION_TARGET_UNKNOWN
            elif observed_exit and exit_code == 0:
                reason = EventReasonCode.VALIDATION_EXIT_ZERO
            elif observed_exit:
                reason = EventReasonCode.VALIDATION_EXIT_NONZERO
            validation_evidence = _evidence(item, *(() if result is None else (result,)))
            identity = {
                "kind": DerivedEventKind.VALIDATION.value,
                "scope": item.scope.model_dump(mode="json"),
                "call_id": item.call_id,
                "call_type": "command",
                "state_history": [state.value for state in history],
                "command_argv": list(argv),
                "targets": list(target_values or ()),
                "exit_code": exit_code if observed_exit else None,
                "evidence": [ref.model_dump(mode="json") for ref in validation_evidence],
            }
            events.append(
                NormalizedValidationEvent(
                    event_id=_event_id(identity),
                    scope=item.scope,
                    role=DerivedEventRole.COMMAND_VALIDATION,
                    reason_code=reason,
                    evidence=validation_evidence,
                    call_id=item.call_id,
                    call_type="command",
                    targets=target_values or (),
                    state=history[-1],
                    state_history=history,
                    command_argv=argv,
                    exit_code=exit_code if observed_exit else None,
                )
            )
            continue

        if item.item_type is ObservableItemType.VALIDATION_MENTION:
            history = validation_state_history(mentioned=True)
            evidence = _evidence(item)
            identity = {
                "kind": DerivedEventKind.VALIDATION.value,
                "scope": item.scope.model_dump(mode="json"),
                "state_history": [state.value for state in history],
                "evidence": [ref.model_dump(mode="json") for ref in evidence],
            }
            events.append(
                NormalizedValidationEvent(
                    event_id=_event_id(identity),
                    scope=item.scope,
                    role=DerivedEventRole.VALIDATION_MENTION,
                    reason_code=EventReasonCode.VALIDATION_MENTION_OBSERVED,
                    evidence=evidence,
                    state=history[-1],
                    state_history=history,
                )
            )
            continue

        if item.item_type is ObservableItemType.PLAN:
            events.append(
                _observation_event(
                    item,
                    role=DerivedEventRole.PLAN_ONLY,
                    observation=ObservationKind.PLAN_ONLY,
                    reason=EventReasonCode.PLAN_ONLY,
                )
            )
            continue
        if item.item_type is ObservableItemType.AGENT_MESSAGE:
            events.append(
                _observation_event(
                    item,
                    role=DerivedEventRole.ASSISTANT_CLAIM_ONLY,
                    observation=ObservationKind.ASSISTANT_CLAIM_ONLY,
                    reason=EventReasonCode.ASSISTANT_CLAIM_ONLY,
                )
            )
            continue
        if item.item_type is ObservableItemType.FILE_CHANGE:
            events.append(
                _observation_event(
                    item,
                    role=DerivedEventRole.FILE_CHANGE_OBSERVATION,
                    observation=ObservationKind.FILE_CHANGE,
                    reason=EventReasonCode.FILE_CHANGE_OBSERVED,
                )
            )
            continue
        if item.item_type is ObservableItemType.VALIDATION_RESULT:
            events.append(
                _observation_event(
                    item,
                    role=DerivedEventRole.UNTRUSTED_DERIVED_VALIDATION,
                    observation=ObservationKind.EXISTING_DERIVED_VALIDATION,
                    reason=EventReasonCode.EXISTING_DERIVED_VALIDATION_IGNORED,
                )
            )

    for call in calls:
        result = linked_by_call.get(_identity_key(call))
        if result is None or call.call_id is None:
            continue
        call_type = _call_type(call)
        identity = {
            "role": LinkRole.CALL_RESULT.value,
            "scope": call.scope.model_dump(mode="json"),
            "call_id": call.call_id,
            "call_type": call_type,
            "call_item_id": call.item_id,
            "call_locator": call.source_locator,
            "result_item_id": result.item_id,
            "result_locator": result.source_locator,
        }
        links.append(
            NormalizedEventLink(
                link_id=_event_id(identity),
                role=LinkRole.CALL_RESULT,
                scope=call.scope,
                call_id=call.call_id,
                call_type=call_type,
                call_item_id=call.item_id,
                call_locator=call.source_locator,
                result_item_id=result.item_id,
                result_locator=result.source_locator,
            )
        )

    events_tuple = tuple(sorted(events, key=_event_sort_key))
    links_tuple = tuple(
        sorted(
            links,
            key=lambda link: (
                *_scope_key(link.scope),
                _locator_order(link.call_locator),
                _locator_order(link.result_locator),
                link.call_locator,
                link.result_locator,
                link.link_id,
            ),
        )
    )
    diagnostics_tuple, diagnostics_truncated = _deduplicate_diagnostics(diagnostics)
    unlinked_result_count = sum(
        _identity_key(result) not in linked_result_ids for result in results
    )
    false_validated_count = sum(
        1
        for event in events_tuple
        if isinstance(event, NormalizedValidationEvent)
        and event.state is ValidationState.PASSED
        and not (
            event.exit_code == 0
            and event.targets
            and event.command_argv
            and _argv_validation_targets(event.command_argv)[0] == event.targets
            and any(
                evidence.evidence_type
                in {
                    ObservableEvidenceType.TOOL_RESULT,
                    ObservableEvidenceType.COMMAND_RESULT,
                }
                for evidence in event.evidence
            )
        )
    )
    if false_validated_count:
        raise AssertionError("fail-closed invariant violated: false validated fact emitted")

    content = {
        "contract_version": CODEX_EVENT_CONTRACT_VERSION,
        "normalizer_version": CODEX_EVENT_NORMALIZER_VERSION,
        "state_machine_version": CODEX_EVENT_STATE_MACHINE_VERSION,
        "events": [event.model_dump(mode="json") for event in events_tuple],
        "links": [link.model_dump(mode="json") for link in links_tuple],
        "diagnostics": [diagnostic.model_dump(mode="json") for diagnostic in diagnostics_tuple],
    }
    summary = NormalizationSummary(
        input_count=len(raw_items),
        unique_input_count=len(exact_items),
        event_count=len(events_tuple),
        link_count=len(links_tuple),
        diagnostic_count=len(diagnostics_tuple),
        diagnostics_truncated=diagnostics_truncated,
        unlinked_result_count=unlinked_result_count,
        unknown_state_count=sum(
            (isinstance(event, NormalizedActionEvent) and event.state is ActionState.UNKNOWN)
            or (
                isinstance(event, NormalizedValidationEvent)
                and event.state is ValidationState.TARGET_UNKNOWN
            )
            for event in events_tuple
        ),
        false_validated_count=0,
        reasoning_units=0,
        agent_reasoning_units=0,
        token_count_units=0,
        system_units=0,
        developer_units=0,
        excluded_input_count=len(excluded),
        action_states=_state_counts(
            event.state.value for event in events_tuple if isinstance(event, NormalizedActionEvent)
        ),
        patch_states=_state_counts(
            event.state.value for event in events_tuple if isinstance(event, NormalizedPatchEvent)
        ),
        validation_states=_state_counts(
            event.state.value
            for event in events_tuple
            if isinstance(event, NormalizedValidationEvent)
        ),
        content_sha256=canonical_sha256(content),
    )
    return CodexEventNormalizationResult(
        events=events_tuple,
        links=links_tuple,
        diagnostics=diagnostics_tuple,
        summary=summary,
    )


__all__ = [
    "CODEX_EVENT_NORMALIZER_VERSION",
    "normalize_codex_events_v1",
]
