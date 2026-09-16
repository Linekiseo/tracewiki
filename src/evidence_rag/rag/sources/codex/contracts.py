"""Frozen contracts for observable Codex event normalization.

These contracts are deliberately independent of persistence, retrieval, and the
production Codex adapter.  Inputs are already-observed, already-redacted records.
Outputs contain only bounded structural evidence references; raw content and
reasoning are never copied into derived facts.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Self
from urllib.parse import quote, unquote, urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

CODEX_EVENT_CONTRACT_VERSION = "codex-observable-event-contract-v1"
CODEX_EVENT_NORMALIZER_VERSION = "codex-observable-event-normalizer-v1"
CODEX_EVENT_STATE_MACHINE_VERSION = "codex-observable-event-state-machine-v1"

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@/-]{0,255}$")
_CALL_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_EVENT_FRAGMENT_RE = re.compile(r"^event=([1-9][0-9]*)$")
_CREDENTIAL_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s\"']{8,}))"
)
_FILESYSTEM_ABSOLUTE_RE = re.compile(
    r"(?:^|[=:\s])(?:/(?:Users|home|private|var|tmp|etc)(?:/|$)|[A-Za-z]:[\\/])",
    re.IGNORECASE,
)
_ENCODED_ABSOLUTE_RE = re.compile(
    r"%2f(?:users|home|private|var|tmp|etc)%2f|[A-Za-z]%3a%5c",
    re.IGNORECASE,
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """Return a canonical, namespaced SHA-256 identity."""

    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _validate_text(value: str) -> str:
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError("text must be NFC")
    if not value or value != value.strip() or _CONTROL_RE.search(value):
        raise ValueError("text must be non-empty, trimmed, and control-free")
    return value


def _validate_safe_identity(value: str) -> str:
    value = _validate_text(value)
    if (
        not _SAFE_NAME_RE.fullmatch(value)
        or _CREDENTIAL_RE.search(value)
        or _FILESYSTEM_ABSOLUTE_RE.search(value)
        or "\\" in value
        or value.startswith(("/", "~"))
        or ".." in PurePosixPath(value.replace("://", "/")).parts
    ):
        raise ValueError("identity is not canonical or safe")
    return value


def _validate_call_type(value: str) -> str:
    value = _validate_text(value)
    if not _CALL_TYPE_RE.fullmatch(value) or _CREDENTIAL_RE.search(value):
        raise ValueError("call_type is not canonical")
    return value


def _validate_locator(value: str) -> str:
    value = _validate_text(value)
    decoded = unquote(value)
    parsed = urlsplit(value)
    path_parts = parsed.path.split("/")
    if len(value) > 2_048 or _CREDENTIAL_RE.search(decoded) or _CONTROL_RE.search(decoded):
        raise ValueError("source_locator is not a safe canonical Codex event locator")
    if (
        parsed.scheme != "codex"
        or parsed.netloc != "thread"
        or parsed.query
        or not _EVENT_FRAGMENT_RE.fullmatch(parsed.fragment)
        or not parsed.path.startswith("/")
        or "//" in parsed.path
        or len(path_parts) != 6
        or path_parts[0] != ""
        or path_parts[2] != "turn"
        or path_parts[4] != "item"
    ):
        raise ValueError("source_locator is not a safe canonical Codex event locator")
    for encoded in (path_parts[1], path_parts[3], path_parts[5]):
        segment = unquote(encoded)
        if (
            not segment
            or segment in {".", ".."}
            or "/" in segment
            or "\\" in segment
            or _CONTROL_RE.search(segment)
            or _CREDENTIAL_RE.search(segment)
            or _WINDOWS_PATH_RE.match(segment)
            or quote(segment, safe="-._~") != encoded
        ):
            raise ValueError("source_locator is not a safe canonical Codex event locator")
    if _FILESYSTEM_ABSOLUTE_RE.search(decoded) or _ENCODED_ABSOLUTE_RE.search(value):
        raise ValueError("source_locator is not a safe Codex event locator")
    return value


_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _validate_locator_segment(value: str, field_name: str) -> str:
    value = _validate_text(value)
    if (
        len(value) > 256
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or _CONTROL_RE.search(value)
        or _CREDENTIAL_RE.search(value)
        or _WINDOWS_PATH_RE.match(value)
        or value.startswith(("~", "/"))
    ):
        raise ValueError(f"{field_name} is not a canonical locator segment")
    return value


def _thread_locator_identity(value: str) -> tuple[str, str]:
    if not value.startswith("codex://"):
        segment = _validate_locator_segment(value, "thread_id")
        return segment, f"codex://thread/{quote(segment, safe='-._~')}"
    parsed = urlsplit(value)
    parts = parsed.path.split("/")
    if (
        parsed.scheme != "codex"
        or parsed.netloc != "thread"
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
        or parts[0] != ""
    ):
        raise ValueError("thread_id is not a canonical Codex thread identity")
    segment = unquote(parts[1])
    _validate_locator_segment(segment, "thread_id")
    canonical = f"codex://thread/{quote(segment, safe='-._~')}"
    if value != canonical:
        raise ValueError("thread_id is not a canonical Codex thread identity")
    return segment, canonical


def _turn_locator_identity(value: str, thread_uri: str) -> tuple[str, str]:
    if not value.startswith("codex://"):
        segment = _validate_locator_segment(value, "turn_id")
        return segment, f"{thread_uri}/turn/{quote(segment, safe='-._~')}"
    prefix = thread_uri + "/turn/"
    if not value.startswith(prefix):
        raise ValueError("turn_id is outside its exact thread identity")
    encoded = value.removeprefix(prefix)
    segment = unquote(encoded)
    _validate_locator_segment(segment, "turn_id")
    canonical = f"{thread_uri}/turn/{quote(segment, safe='-._~')}"
    if value != canonical:
        raise ValueError("turn_id is not a canonical Codex turn identity")
    return segment, canonical


def _validate_argument(value: str) -> str:
    value = _validate_text(value)
    if len(value) > 512:
        raise ValueError("command argument is too long")
    return value


def _validate_untrusted_target(value: str) -> str:
    value = _validate_text(value)
    if len(value) > 1_024:
        raise ValueError("target is too long")
    return value


def _validate_canonical_target(value: str) -> str:
    value = _validate_text(value)
    if (
        len(value) > 1_024
        or _CREDENTIAL_RE.search(value)
        or _FILESYSTEM_ABSOLUTE_RE.search(value)
        or _ENCODED_ABSOLUTE_RE.search(value)
        or "\\" in value
        or "://" in value
    ):
        raise ValueError("target is not safe")
    path_text, separator, selector = value.partition("::")
    path = PurePosixPath(path_text)
    if (
        path.is_absolute()
        or not path.parts
        or path.as_posix() != path_text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("target must be canonical and repository relative")
    if separator and (
        not selector
        or len(selector) > 512
        or not re.fullmatch(r"[A-Za-z0-9_.*,:+\-\[\]/]+", selector)
        or ".." in selector
    ):
        raise ValueError("test selector is not canonical")
    return value


def _validate_json_value(
    value: object,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> None:
    if budget is None:
        budget = [2_048]
    budget[0] -= 1
    if budget[0] < 0 or depth > 32:
        raise ValueError("payload exceeds the input safety bound")
    if value is None or type(value) in {bool, int, float, str}:
        if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
            raise ValueError("payload contains a non-finite float")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item, depth=depth + 1, budget=budget)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("payload keys must be strings")
            _validate_text(key)
            _validate_json_value(item, depth=depth + 1, budget=budget)
        return
    raise ValueError("payload must contain JSON values only")


SafeIdentity = Annotated[
    StrictStr,
    Field(min_length=1, max_length=256),
    AfterValidator(_validate_safe_identity),
]
CallType = Annotated[
    StrictStr,
    Field(min_length=1, max_length=128),
    AfterValidator(_validate_call_type),
]
SourceLocator = Annotated[
    StrictStr,
    Field(min_length=1, max_length=2_048),
    AfterValidator(_validate_locator),
]
CommandArgument = Annotated[
    StrictStr,
    Field(min_length=1, max_length=512),
    AfterValidator(_validate_argument),
]
UntrustedTarget = Annotated[
    StrictStr,
    Field(min_length=1, max_length=1_024),
    AfterValidator(_validate_untrusted_target),
]
CanonicalTarget = Annotated[
    StrictStr,
    Field(min_length=1, max_length=1_024),
    AfterValidator(_validate_canonical_target),
]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
Sha256 = Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def canonical_json_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))

    def canonical_sha256(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_json_bytes()).hexdigest()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        del deep
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(self, **_: Any) -> Self:
        raise TypeError("deprecated copy() is disabled; use validated model_copy()")


class ObservableItemType(StrEnum):
    ACTION_PROPOSAL = "ActionProposal"
    TOOL_CALL = "ToolCall"
    COMMAND_EXECUTION = "CommandExecution"
    PATCH = "Patch"
    PATCH_APPLY = "PatchApply"
    TOOL_RESULT = "ToolResult"
    COMMAND_RESULT = "CommandResult"
    PATCH_RESULT = "PatchResult"
    FILE_CHANGE = "FileChange"
    VALIDATION_MENTION = "ValidationMention"
    VALIDATION_RESULT = "ValidationResult"
    PLAN = "Plan"
    AGENT_MESSAGE = "AgentMessage"
    USER_GOAL = "UserGoal"
    REASONING = "reasoning"
    AGENT_REASONING = "agent_reasoning"
    TOKEN_COUNT = "token_count"
    SYSTEM_MESSAGE = "SystemMessage"
    DEVELOPER_MESSAGE = "DeveloperMessage"


class ObservableRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"
    DEVELOPER = "developer"


class ObservedOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ObservableEvidenceType(StrEnum):
    ACTION_PROPOSAL = "action_proposal"
    TOOL_INVOCATION = "tool_invocation"
    COMMAND_INVOCATION = "command_invocation"
    TOOL_RESULT = "tool_result"
    COMMAND_RESULT = "command_result"
    PATCH_PROPOSAL = "patch_proposal"
    PATCH_APPLY_INVOCATION = "patch_apply_invocation"
    PATCH_RESULT = "patch_result"
    FILE_CHANGE = "file_change"
    VALIDATION_MENTION = "validation_mention"
    PLAN = "plan"
    ASSISTANT_MESSAGE = "assistant_message"
    EXISTING_DERIVED_VALIDATION = "existing_derived_validation"
    CONTRADICTORY_PROVENANCE = "contradictory_provenance"


class DerivedEventKind(StrEnum):
    ACTION = "action"
    PATCH = "patch"
    VALIDATION = "validation"
    OBSERVATION = "observation"


class DerivedEventRole(StrEnum):
    ACTION_PROPOSAL = "action_proposal"
    TOOL_ACTION = "tool_action"
    COMMAND_ACTION = "command_action"
    PATCH_CHANGE = "patch_change"
    COMMAND_VALIDATION = "command_validation"
    VALIDATION_MENTION = "validation_mention"
    PLAN_ONLY = "plan_only"
    ASSISTANT_CLAIM_ONLY = "assistant_claim_only"
    FILE_CHANGE_OBSERVATION = "file_change_observation"
    UNTRUSTED_DERIVED_VALIDATION = "untrusted_derived_validation"
    CONTRADICTORY_PROVENANCE = "contradictory_provenance"


class ActionState(StrEnum):
    PROPOSED = "proposed"
    INVOKED = "invoked"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class PatchState(StrEnum):
    PATCH_PROPOSED = "patch_proposed"
    APPLY_INVOKED = "apply_invoked"
    APPLIED = "applied"
    FAILED = "failed"


class ValidationState(StrEnum):
    MENTIONED = "mentioned"
    COMMAND_INVOKED = "command_invoked"
    EXIT_OBSERVED = "exit_observed"
    PASSED = "passed"
    FAILED = "failed"
    TARGET_UNKNOWN = "target_unknown"


class EventReasonCode(StrEnum):
    ACTION_PROPOSAL_OBSERVED = "action_proposal_observed"
    ACTION_INVOCATION_OBSERVED = "action_invocation_observed"
    RESULT_COMPLETION_OBSERVED = "result_completion_observed"
    RESULT_FAILURE_OBSERVED = "result_failure_observed"
    RESULT_TIMEOUT_OBSERVED = "result_timeout_observed"
    RESULT_CANCELLATION_OBSERVED = "result_cancellation_observed"
    RESULT_OUTCOME_UNKNOWN = "result_outcome_unknown"
    PATCH_PROPOSAL_OBSERVED = "patch_proposal_observed"
    PATCH_APPLY_INVOCATION_OBSERVED = "patch_apply_invocation_observed"
    PATCH_SUCCESS_OBSERVED = "patch_success_observed"
    PATCH_FAILURE_OBSERVED = "patch_failure_observed"
    VALIDATION_MENTION_OBSERVED = "validation_mention_observed"
    VALIDATION_COMMAND_INVOKED = "validation_command_invoked"
    VALIDATION_EXIT_ZERO = "validation_exit_zero"
    VALIDATION_EXIT_NONZERO = "validation_exit_nonzero"
    VALIDATION_TARGET_UNKNOWN = "validation_target_unknown"
    PLAN_ONLY = "plan_only"
    ASSISTANT_CLAIM_ONLY = "assistant_claim_only"
    FILE_CHANGE_OBSERVED = "file_change_observed"
    EXISTING_DERIVED_VALIDATION_IGNORED = "existing_derived_validation_ignored"
    ROLE_PROVENANCE_CONFLICT = "role_provenance_conflict"


class ObservationKind(StrEnum):
    PLAN_ONLY = "plan_only"
    ASSISTANT_CLAIM_ONLY = "assistant_claim_only"
    FILE_CHANGE = "file_change"
    EXISTING_DERIVED_VALIDATION = "existing_derived_validation"
    CONTRADICTORY_PROVENANCE = "contradictory_provenance"


class LinkRole(StrEnum):
    CALL_RESULT = "call_result"


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticCode(StrEnum):
    MISSING_CALL_ID = "missing_call_id"
    MISSING_RESULT = "missing_result"
    UNLINKED_RESULT = "unlinked_result"
    DUPLICATE_ITEM_ID = "duplicate_item_id"
    DUPLICATE_RESULT = "duplicate_result"
    AMBIGUOUS_CALL = "ambiguous_call"
    MULTIPLE_RESULTS = "multiple_results"
    OUT_OF_ORDER_RESULT = "out_of_order_result"
    CROSS_THREAD_RESULT = "cross_thread_result"
    CROSS_GENERATION_RESULT = "cross_generation_result"
    SCOPE_MISMATCH = "scope_mismatch"
    CALL_TYPE_MISMATCH = "call_type_mismatch"
    MISSING_EXIT_CODE = "missing_exit_code"
    INVALID_EXIT_CODE = "invalid_exit_code"
    AMBIGUOUS_EXIT_CODE = "ambiguous_exit_code"
    EXIT_PAYLOAD_BOUNDED = "exit_payload_bounded"
    COMMAND_NOT_ALLOWLISTED = "command_not_allowlisted"
    COMMAND_NON_CANONICAL = "command_non_canonical"
    SHELL_COMMAND_REJECTED = "shell_command_rejected"
    TARGET_UNKNOWN = "target_unknown"
    TARGET_REJECTED = "target_rejected"
    TARGET_PROJECT_MISMATCH = "target_project_mismatch"
    TARGET_ABSENT = "target_absent"
    TARGET_MISMATCH = "target_mismatch"
    TARGET_ARGUMENT_UNSUPPORTED = "target_argument_unsupported"
    PATCH_RESULT_UNKNOWN = "patch_result_unknown"
    PATCH_TERMINAL_CONFLICT = "patch_terminal_conflict"
    RESULT_OUTCOME_UNKNOWN = "result_outcome_unknown"
    ROLE_PROVENANCE_CONFLICT = "role_provenance_conflict"
    NON_EVIDENCE_EXCLUDED = "non_evidence_excluded"
    DIAGNOSTICS_TRUNCATED = "diagnostics_truncated"


class EventScope(_FrozenContract):
    """Exact authority and generation scope inherited by every derived fact."""

    project_id: SafeIdentity
    source_id: SafeIdentity
    generation_id: SafeIdentity
    thread_id: SafeIdentity
    turn_id: SafeIdentity
    acl_ref: SafeIdentity


def canonical_codex_locator_v1(
    scope: EventScope,
    item_id: str,
    observable_order: int,
) -> str:
    """Rebuild the one canonical locator accepted by the V1 observable contract."""

    if isinstance(observable_order, bool) or not isinstance(observable_order, int):
        raise TypeError("observable_order must be an exact integer")
    if observable_order < 0:
        raise ValueError("observable_order must be non-negative")
    _, thread_uri = _thread_locator_identity(scope.thread_id)
    _, turn_uri = _turn_locator_identity(scope.turn_id, thread_uri)
    item_segment = _validate_locator_segment(item_id, "item_id")
    return f"{turn_uri}/item/{quote(item_segment, safe='-._~')}#event={observable_order + 1}"


class ObservableCodexItem(_FrozenContract):
    """One ordered, sanitized observable record.

    ``payload`` is input-only.  It may carry a structured result envelope, but is
    never copied to normalized output.
    """

    item_id: SafeIdentity
    source_locator: SourceLocator
    scope: EventScope
    observable_order: NonNegativeInt
    item_type: ObservableItemType
    role: ObservableRole | None = None
    call_id: SafeIdentity | None = None
    call_type: CallType | None = None
    tool_name: CallType | None = None
    command_argv: tuple[CommandArgument, ...] = Field(default=(), max_length=64)
    shell: StrictBool = False
    targets: tuple[UntrustedTarget, ...] = Field(default=(), max_length=64)
    target_project_id: SafeIdentity | None = None
    outcome: ObservedOutcome | None = None
    success: StrictBool | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    sanitized: Literal[True] = True
    contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION

    @field_validator("payload")
    @classmethod
    def _json_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_json_value(value)
        return value

    @model_validator(mode="after")
    def _locator_scope(self) -> Self:
        expected = canonical_codex_locator_v1(
            self.scope,
            self.item_id,
            self.observable_order,
        )
        if self.source_locator != expected:
            raise ValueError("source_locator does not equal its canonical observable identity")
        return self


class EvidenceRef(_FrozenContract):
    source_item_id: SafeIdentity
    source_locator: SourceLocator
    evidence_type: ObservableEvidenceType


class _DerivedEvent(_FrozenContract):
    event_id: Sha256
    scope: EventScope
    role: DerivedEventRole
    reason_code: EventReasonCode
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=8)
    call_id: SafeIdentity | None = None
    call_type: CallType | None = None
    targets: tuple[CanonicalTarget, ...] = Field(default=(), max_length=64)
    contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION

    @field_validator("evidence")
    @classmethod
    def _ordered_unique_evidence(cls, value: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
        keys = tuple(
            (item.source_locator, item.source_item_id, item.evidence_type.value) for item in value
        )
        if keys != tuple(sorted(set(keys))):
            raise ValueError("evidence must be sorted and unique")
        return value

    @field_validator("targets")
    @classmethod
    def _ordered_unique_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("targets must be sorted and unique")
        return value


class NormalizedActionEvent(_DerivedEvent):
    kind: Literal[DerivedEventKind.ACTION] = DerivedEventKind.ACTION
    state: ActionState
    state_history: tuple[ActionState, ...] = Field(min_length=1, max_length=2)
    command_argv: tuple[CommandArgument, ...] = Field(default=(), max_length=64)
    exit_code: StrictInt | None = None

    @model_validator(mode="after")
    def _final_state(self) -> Self:
        if self.state_history[-1] is not self.state:
            raise ValueError("action state must be the final history state")
        return self


class NormalizedPatchEvent(_DerivedEvent):
    kind: Literal[DerivedEventKind.PATCH] = DerivedEventKind.PATCH
    state: PatchState
    state_history: tuple[PatchState, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def _final_state(self) -> Self:
        if self.state_history[-1] is not self.state:
            raise ValueError("patch state must be the final history state")
        return self


class NormalizedValidationEvent(_DerivedEvent):
    kind: Literal[DerivedEventKind.VALIDATION] = DerivedEventKind.VALIDATION
    state: ValidationState
    state_history: tuple[ValidationState, ...] = Field(min_length=1, max_length=4)
    command_argv: tuple[CommandArgument, ...] = Field(default=(), max_length=64)
    exit_code: StrictInt | None = None

    @model_validator(mode="after")
    def _final_state(self) -> Self:
        if self.state_history[-1] is not self.state:
            raise ValueError("validation state must be the final history state")
        if self.state is ValidationState.PASSED and self.exit_code != 0:
            raise ValueError("passed validation requires an exact zero exit code")
        return self


class NormalizedObservationEvent(_FrozenContract):
    event_id: Sha256
    kind: Literal[DerivedEventKind.OBSERVATION] = DerivedEventKind.OBSERVATION
    scope: EventScope
    role: DerivedEventRole
    observation: ObservationKind
    reason_code: EventReasonCode
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=8)
    contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION


NormalizedEvent = (
    NormalizedActionEvent
    | NormalizedPatchEvent
    | NormalizedValidationEvent
    | NormalizedObservationEvent
)


class NormalizedEventLink(_FrozenContract):
    link_id: Sha256
    role: LinkRole
    scope: EventScope
    call_id: SafeIdentity
    call_type: CallType
    call_item_id: SafeIdentity
    call_locator: SourceLocator
    result_item_id: SafeIdentity
    result_locator: SourceLocator
    contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION


class NormalizationDiagnostic(_FrozenContract):
    code: DiagnosticCode
    severity: DiagnosticSeverity
    scope: EventScope
    source_item_ids: tuple[SafeIdentity, ...] = Field(default=(), max_length=8)
    call_id: SafeIdentity | None = None
    occurrences: PositiveInt = 1
    contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION

    @field_validator("source_item_ids")
    @classmethod
    def _ordered_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("source_item_ids must be sorted and unique")
        return value


class StateCount(_FrozenContract):
    state: SafeIdentity
    count: NonNegativeInt


class NormalizationSummary(_FrozenContract):
    input_count: NonNegativeInt
    unique_input_count: NonNegativeInt
    event_count: NonNegativeInt
    link_count: NonNegativeInt
    diagnostic_count: NonNegativeInt
    diagnostics_truncated: NonNegativeInt
    unlinked_result_count: NonNegativeInt
    unknown_state_count: NonNegativeInt
    false_validated_count: Literal[0] = 0
    reasoning_units: Literal[0] = 0
    agent_reasoning_units: Literal[0] = 0
    token_count_units: Literal[0] = 0
    system_units: Literal[0] = 0
    developer_units: Literal[0] = 0
    excluded_input_count: NonNegativeInt = 0
    action_states: tuple[StateCount, ...] = ()
    patch_states: tuple[StateCount, ...] = ()
    validation_states: tuple[StateCount, ...] = ()
    content_sha256: Sha256
    contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION
    normalizer_version: Literal[CODEX_EVENT_NORMALIZER_VERSION] = CODEX_EVENT_NORMALIZER_VERSION
    state_machine_version: Literal[CODEX_EVENT_STATE_MACHINE_VERSION] = (
        CODEX_EVENT_STATE_MACHINE_VERSION
    )


class CodexEventNormalizationResult(_FrozenContract):
    events: tuple[NormalizedEvent, ...]
    links: tuple[NormalizedEventLink, ...]
    diagnostics: tuple[NormalizationDiagnostic, ...]
    summary: NormalizationSummary
    contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION
    normalizer_version: Literal[CODEX_EVENT_NORMALIZER_VERSION] = CODEX_EVENT_NORMALIZER_VERSION
    state_machine_version: Literal[CODEX_EVENT_STATE_MACHINE_VERSION] = (
        CODEX_EVENT_STATE_MACHINE_VERSION
    )
