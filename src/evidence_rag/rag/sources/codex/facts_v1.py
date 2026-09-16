"""CX1-02 output windows and additive Codex derived-fact publication.

This module is deliberately isolated from the production runtime and V1 stores.
It accepts only the frozen CX1-01 normalization result, materializes bounded
derived units, and can publish them to a temporary or in-memory SQLite database.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sqlite3
import sys
import tempfile
import threading
import types
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import unquote, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from evidence_rag.code_identity_v1 import (
    CodeIdentityError,
    portable_code_payload,
    portable_constant_payload,
    portable_function_payload,
)

from . import event_normalizer as _cx1_normalizer_module
from .contracts import (
    CODEX_EVENT_CONTRACT_VERSION,
    CODEX_EVENT_NORMALIZER_VERSION,
    CODEX_EVENT_STATE_MACHINE_VERSION,
    ActionState,
    CallType,
    CanonicalTarget,
    CodexEventNormalizationResult,
    CommandArgument,
    DerivedEventKind,
    DerivedEventRole,
    EventReasonCode,
    EventScope,
    NormalizedActionEvent,
    NormalizedEvent,
    NormalizedEventLink,
    NormalizedObservationEvent,
    NormalizedPatchEvent,
    NormalizedValidationEvent,
    ObservableCodexItem,
    ObservableEvidenceType,
    ObservableItemType,
    ObservationKind,
    ObservedOutcome,
    PatchState,
    SafeIdentity,
    Sha256,
    SourceLocator,
    ValidationState,
    canonical_codex_locator_v1,
    canonical_sha256,
)

CODEX_OUTPUT_WINDOW_CONTRACT_VERSION = "codex-output-window-contract-v1"
CODEX_DERIVED_FACT_CONTRACT_VERSION = "codex-derived-fact-contract-v1"
CODEX_DERIVED_FACT_BUILDER_VERSION = "codex-derived-fact-builder-v1"
CODEX_DERIVED_FACT_SCHEMA_VERSION = "codex-derived-fact-sqlite-schema-v1"
CODEX_SOURCE_AUTHORITY_VERSION = "codex-source-authority-v2"
CODEX_NORMALIZER_MODULE = "evidence_rag.rag.sources.codex.event_normalizer"
CODEX_NORMALIZER_EXPORT = "normalize_codex_events_v1"
CODEX_NORMALIZER_SOURCE_SHA256 = (
    "sha256:f934cd24260b365f07398c49ea8a262ed227ea8b133bc4d5f3c6ea45787920c5"
)
CODEX_NORMALIZER_CODE_SHA256 = (
    "sha256:b8f548753a287c45019ae7a2f2e4c410a1234a65f4a79357f6fdae3f5410a931"
)
CODEX_PUBLICATION_OUTPUT_WINDOW_MAX_CHARS = 4_096

_MAX_OUTPUT_WINDOW_CHARS = 65_536
_MAX_OUTPUT_BYTES = 64 * 1024 * 1024
_BOUND_CX1_NORMALIZER = _cx1_normalizer_module.normalize_codex_events_v1
_BOUND_CX1_MODULE_BINDINGS = tuple(
    sorted(
        (
            name,
            value,
        )
        for name, value in vars(_cx1_normalizer_module).items()
        if (
            isinstance(value, (types.FunctionType, type))
            or name.startswith("CODEX_")
            or name
            in {
                "_INVOCATION_TYPES",
                "_RESULT_TYPES",
                "_NON_EVIDENCE_TYPES",
                "_NON_EVIDENCE_ROLES",
            }
        )
    )
)
_RESULT_EVIDENCE_TYPES = frozenset(
    {
        ObservableEvidenceType.TOOL_RESULT,
        ObservableEvidenceType.COMMAND_RESULT,
        ObservableEvidenceType.PATCH_RESULT,
    }
)
_SECRET_RE = re.compile(
    r"(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret)"
    r"\s*[:=]\s*[^\s\"']{8,})|"
    r"(?i:authorization\s*:\s*bearer\s+\S{8,})"
    r")"
)
_RAW_ABSOLUTE_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z0-9._~:/-])/(?!/)[A-Za-z0-9._~-]+(?:[/\\][^\s\"'<>]*)?|"
    r"(?<![A-Za-z0-9._-])[A-Za-z]:[\\/])",
    re.IGNORECASE,
)
_NETWORK_SHARE_RE = re.compile(
    r"(?<![A-Za-z0-9._~:+/\\-])"
    r"(?:"
    r"[\\/]{2}\?[\\/]UNC[\\/][^\\/\s\"'<>]+[\\/][^\\/\s\"'<>]+|"
    r"[\\/]{2}\.[\\/][^\\/\s\"'<>]+|"
    r"[\\/]{2}(?![?.][\\/])[^\\/\s\"'<>:]+[\\/][^\\/\s\"'<>]+"
    r")",
    re.IGNORECASE,
)
_UNC_OR_MIXED_NETWORK_SHARE_RE = re.compile(
    r"(?:\\\\|\\/|/\\)"
    r"(?:"
    r"\?[\\/]UNC[\\/][^\\/\s\"'<>]+[\\/][^\\/\s\"'<>]+|"
    r"\.[\\/][^\\/\s\"'<>]+|"
    r"(?![?.][\\/])[^\\/\s\"'<>:]+[\\/][^\\/\s\"'<>]+"
    r")",
    re.IGNORECASE,
)
_MAX_PRIVACY_DECODE_ROUNDS = 4


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_text(value: object) -> str:
    return _canonical_json(value).decode("utf-8")


def _content_sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_nonempty_text(value: str) -> str:
    if (
        not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("text must be non-empty, trimmed, NFC, and control-free")
    return value


NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]


class _FrozenFactContract(BaseModel):
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


class CodexFactError(ValueError):
    """Base class for fail-closed CX1-02 errors."""


class CodexFactValidationError(CodexFactError):
    """The normalized input or derived publication failed exact validation."""


class CodexFactScopeError(CodexFactError):
    """A publication or link crossed its exact authority scope."""


class CodexFactPublicationError(CodexFactError):
    """Atomic SQLite publication failed."""


class CodexOutputRole(StrEnum):
    RESULT = "result"
    OUTPUT = "output"
    STDOUT = "stdout"
    STDERR = "stderr"
    TRACEBACK = "traceback"


_OUTPUT_PATHS = tuple(
    (prefix + (name,), role)
    for prefix in (
        (),
        ("result",),
        ("execution",),
        ("execution", "result"),
    )
    for name, role in (
        ("output", CodexOutputRole.OUTPUT),
        ("stdout", CodexOutputRole.STDOUT),
        ("stderr", CodexOutputRole.STDERR),
        ("traceback", CodexOutputRole.TRACEBACK),
    )
)


def _privacy_scan_views(content: str) -> tuple[str, ...]:
    views: list[str] = []
    seen: set[str] = set()
    candidate = content
    for _ in range(_MAX_PRIVACY_DECODE_ROUNDS + 1):
        normalized = unicodedata.normalize("NFKC", candidate)
        for value in (candidate, normalized):
            if value not in seen:
                seen.add(value)
                views.append(value)
        decoded = unquote(normalized)
        if decoded == normalized:
            break
        candidate = decoded
    return tuple(views)


def _validate_raw_output_privacy(content: str) -> None:
    scan_views = _privacy_scan_views(content)
    if any(_SECRET_RE.search(value) for value in scan_views):
        raise CodexFactValidationError("raw secret material is forbidden in observed output")
    if any(
        _RAW_ABSOLUTE_PATH_RE.search(value)
        or _NETWORK_SHARE_RE.search(value)
        or _UNC_OR_MIXED_NETWORK_SHARE_RE.search(value)
        or "file://" in value.casefold()
        for value in scan_views
    ):
        raise CodexFactValidationError(
            "raw or encoded absolute path is forbidden in observed output"
        )


class CodexOutputWindow(_FrozenFactContract):
    """Bounded source-text evidence with exact truncation metadata."""

    role: CodexOutputRole
    head: StrictStr
    tail: StrictStr
    max_chars: PositiveInt = Field(le=_MAX_OUTPUT_WINDOW_CHARS)
    total_chars: NonNegativeInt
    total_utf8_bytes: NonNegativeInt
    retained_chars: NonNegativeInt
    omitted_chars: NonNegativeInt
    truncated: StrictBool
    error_tail: StrictBool
    redacted: StrictBool
    sanitized: Literal[True] = True
    content_sha256: Sha256
    contract_version: Literal[CODEX_OUTPUT_WINDOW_CONTRACT_VERSION] = (
        CODEX_OUTPUT_WINDOW_CONTRACT_VERSION
    )

    @model_validator(mode="after")
    def _metadata_is_exact(self) -> Self:
        try:
            head_bytes = self.head.encode("utf-8")
            tail_bytes = self.tail.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("window text must be valid UTF-8") from exc
        retained = len(self.head) + len(self.tail)
        if retained != self.retained_chars or retained > self.max_chars:
            raise ValueError("retained character metadata is inconsistent")
        if self.omitted_chars != self.total_chars - retained or self.omitted_chars < 0:
            raise ValueError("omitted character metadata is inconsistent")
        if self.truncated is not (self.omitted_chars > 0):
            raise ValueError("truncation metadata is inconsistent")
        if not self.truncated and self.tail:
            raise ValueError("an untruncated window must use only the head field")
        if self.total_utf8_bytes < len(head_bytes) + len(tail_bytes):
            raise ValueError("UTF-8 byte metadata is inconsistent")
        _validate_raw_output_privacy(self.head)
        _validate_raw_output_privacy(self.tail)
        return self

    @property
    def text(self) -> str:
        """Return a display form without changing the retained-character budget."""

        if not self.truncated:
            return self.head
        if not self.head:
            return self.tail
        if not self.tail:
            return self.head
        return f"{self.head}\n… {self.omitted_chars} chars omitted …\n{self.tail}"


class CodexLocatedOutputWindow(_FrozenFactContract):
    """One bounded output window attached to an exact raw evidence locator."""

    source_item_id: SafeIdentity
    source_locator: SourceLocator
    source_scope: EventScope
    source_item_sha256: Sha256
    window: CodexOutputWindow
    contract_version: Literal[CODEX_DERIVED_FACT_CONTRACT_VERSION] = (
        CODEX_DERIVED_FACT_CONTRACT_VERSION
    )

    @model_validator(mode="after")
    def _exact_source_binding(self) -> Self:
        _require_exact_raw_locator(
            self.source_scope,
            self.source_item_id,
            self.source_locator,
        )
        return self


class CodexNormalizerIdentity(_FrozenFactContract):
    module: Literal[CODEX_NORMALIZER_MODULE] = CODEX_NORMALIZER_MODULE
    export: Literal[CODEX_NORMALIZER_EXPORT] = CODEX_NORMALIZER_EXPORT
    version: Literal[CODEX_EVENT_NORMALIZER_VERSION] = CODEX_EVENT_NORMALIZER_VERSION
    source_sha256: Literal[CODEX_NORMALIZER_SOURCE_SHA256] = CODEX_NORMALIZER_SOURCE_SHA256
    code_sha256: Literal[CODEX_NORMALIZER_CODE_SHA256] = CODEX_NORMALIZER_CODE_SHA256
    authority_version: Literal[CODEX_SOURCE_AUTHORITY_VERSION] = CODEX_SOURCE_AUTHORITY_VERSION


class CodexRawOutputAuthority(_FrozenFactContract):
    role: CodexOutputRole
    content_sha256: Sha256
    total_chars: NonNegativeInt
    total_utf8_bytes: NonNegativeInt
    authority_version: Literal[CODEX_SOURCE_AUTHORITY_VERSION] = CODEX_SOURCE_AUTHORITY_VERSION


class CodexSourceItemAuthority(_FrozenFactContract):
    source_item_id: SafeIdentity
    source_locator: SourceLocator
    source_scope: EventScope
    item_type: ObservableItemType
    source_item_sha256: Sha256
    raw_outputs: tuple[CodexRawOutputAuthority, ...] = Field(default=(), max_length=5)
    authority_version: Literal[CODEX_SOURCE_AUTHORITY_VERSION] = CODEX_SOURCE_AUTHORITY_VERSION

    @field_validator("raw_outputs")
    @classmethod
    def _ordered_outputs(
        cls,
        values: tuple[CodexRawOutputAuthority, ...],
    ) -> tuple[CodexRawOutputAuthority, ...]:
        keys = tuple((value.role.value, value.content_sha256) for value in values)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("raw output authorities must be sorted and unique")
        return values

    @model_validator(mode="after")
    def _exact_locator(self) -> Self:
        _require_exact_raw_locator(
            self.source_scope,
            self.source_item_id,
            self.source_locator,
        )
        return self


class CodexSourceAuthority(_FrozenFactContract):
    source_set_sha256: Sha256
    item_count: NonNegativeInt
    items: tuple[CodexSourceItemAuthority, ...]
    normalizer: CodexNormalizerIdentity
    authority_version: Literal[CODEX_SOURCE_AUTHORITY_VERSION] = CODEX_SOURCE_AUTHORITY_VERSION

    @model_validator(mode="after")
    def _manifest_identity(self) -> Self:
        keys = tuple(_source_authority_item_key(item) for item in self.items)
        if keys != tuple(sorted(set(keys))) or self.item_count != len(self.items):
            raise ValueError("source authority items must be sorted, unique, and counted")
        expected = _source_set_digest(self.items, self.normalizer)
        if self.source_set_sha256 != expected:
            raise ValueError("source authority digest is inconsistent")
        return self


def _line_safe_head(content: str, budget: int) -> str:
    if budget <= 0:
        return ""
    head = content[:budget]
    if len(head) == len(content) or content[len(head) : len(head) + 1] == "\n":
        return head
    boundary = head.rfind("\n")
    return head[: boundary + 1] if boundary >= 0 else head


def _line_safe_tail(content: str, budget: int) -> str:
    if budget <= 0:
        return ""
    start = max(0, len(content) - budget)
    tail = content[start:]
    if start == 0 or content[start - 1 : start] == "\n":
        return tail
    boundary = tail.find("\n")
    return tail[boundary + 1 :] if boundary >= 0 else tail


def build_codex_output_window_v1(
    content: str,
    *,
    max_chars: int = 4_096,
    role: CodexOutputRole | str = CodexOutputRole.OUTPUT,
    outcome: ObservedOutcome | str | None = None,
    exit_code: int | None = None,
    error_observed: bool = False,
    redacted: bool = False,
) -> CodexOutputWindow:
    """Build a deterministic head/tail window without retaining raw secrets."""

    if not isinstance(content, str):
        raise TypeError("content must be a string")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise TypeError("max_chars must be an exact integer")
    if max_chars < 1 or max_chars > _MAX_OUTPUT_WINDOW_CHARS:
        raise ValueError(f"max_chars must be between 1 and {_MAX_OUTPUT_WINDOW_CHARS}")
    if type(error_observed) is not bool or type(redacted) is not bool:
        raise TypeError("error_observed and redacted must be exact booleans")
    if isinstance(exit_code, bool) or (exit_code is not None and not isinstance(exit_code, int)):
        raise TypeError("exit_code must be an exact integer or None")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("content must be valid UTF-8") from exc
    if len(encoded) > _MAX_OUTPUT_BYTES:
        raise ValueError("content exceeds the isolated output safety bound")
    if "\x00" in content:
        raise ValueError("NUL is forbidden in observed output")
    _validate_raw_output_privacy(content)

    exact_role = CodexOutputRole(role)
    exact_outcome = ObservedOutcome(outcome) if outcome is not None else None
    is_error = (
        error_observed
        or exit_code not in {None, 0}
        or exact_outcome
        in {
            ObservedOutcome.FAILED,
            ObservedOutcome.TIMEOUT,
            ObservedOutcome.CANCELLED,
        }
    )
    total_chars = len(content)
    if total_chars <= max_chars:
        head = content
        tail = ""
    else:
        tail_budget = max(1, (max_chars * 2) // 3) if is_error else max(1, max_chars // 2)
        head_budget = max_chars - tail_budget
        head = _line_safe_head(content, head_budget)
        tail = _line_safe_tail(content, tail_budget)
        if not head and head_budget and "\n" in content[:head_budget]:
            head = content[:head_budget]
        if not tail and tail_budget:
            tail = content[-tail_budget:]

    retained = len(head) + len(tail)
    return CodexOutputWindow(
        role=exact_role,
        head=head,
        tail=tail,
        max_chars=max_chars,
        total_chars=total_chars,
        total_utf8_bytes=len(encoded),
        retained_chars=retained,
        omitted_chars=total_chars - retained,
        truncated=retained < total_chars,
        error_tail=is_error,
        redacted=redacted,
        content_sha256=_content_sha256(content),
    )


build_output_window_v1 = build_codex_output_window_v1


class CodexFactPublicationScope(_FrozenFactContract):
    """Complete authority scope for one derived generation publication."""

    project_id: SafeIdentity
    repository_id: SafeIdentity
    thread_id: SafeIdentity
    generation_id: SafeIdentity
    source_id: SafeIdentity
    source_version: SafeIdentity
    acl_ref: SafeIdentity
    contract_version: Literal[CODEX_DERIVED_FACT_CONTRACT_VERSION] = (
        CODEX_DERIVED_FACT_CONTRACT_VERSION
    )


class CodexDerivedFact(_FrozenFactContract):
    """One content-addressed unit of normalized truth."""

    fact_id: Sha256
    content_sha256: Sha256
    source_set_sha256: Sha256
    scope: CodexFactPublicationScope
    kind: DerivedEventKind
    role: DerivedEventRole
    reason_code: EventReasonCode
    state: SafeIdentity | None = None
    state_history: tuple[SafeIdentity, ...] = Field(default=(), max_length=4)
    observation: ObservationKind | None = None
    call_type: CallType | None = None
    command_argv: tuple[CommandArgument, ...] = Field(default=(), max_length=64)
    targets: tuple[CanonicalTarget, ...] = Field(default=(), max_length=64)
    exit_code: StrictInt | None = None
    output_windows: tuple[CodexOutputWindow, ...] = Field(default=(), max_length=8)
    event_contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION
    normalizer_version: Literal[CODEX_EVENT_NORMALIZER_VERSION] = CODEX_EVENT_NORMALIZER_VERSION
    builder_version: Literal[CODEX_DERIVED_FACT_BUILDER_VERSION] = (
        CODEX_DERIVED_FACT_BUILDER_VERSION
    )
    contract_version: Literal[CODEX_DERIVED_FACT_CONTRACT_VERSION] = (
        CODEX_DERIVED_FACT_CONTRACT_VERSION
    )

    @field_validator("state_history")
    @classmethod
    def _nonempty_state_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _validate_nonempty_text(value)
        return values

    @field_validator("targets")
    @classmethod
    def _ordered_targets(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("targets must be sorted and unique")
        return values

    @field_validator("output_windows")
    @classmethod
    def _ordered_windows(
        cls,
        values: tuple[CodexOutputWindow, ...],
    ) -> tuple[CodexOutputWindow, ...]:
        keys = tuple(_window_key(window) for window in values)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("output windows must be sorted and unique")
        return values

    @model_validator(mode="after")
    def _truth_and_identity(self) -> Self:
        allowed_states: set[str]
        if self.kind is DerivedEventKind.ACTION:
            allowed_states = {item.value for item in ActionState}
            if self.observation is not None:
                raise ValueError("action fact cannot carry an observation kind")
        elif self.kind is DerivedEventKind.PATCH:
            allowed_states = {item.value for item in PatchState}
            if self.observation is not None:
                raise ValueError("patch fact cannot carry an observation kind")
        elif self.kind is DerivedEventKind.VALIDATION:
            allowed_states = {item.value for item in ValidationState}
            if self.observation is not None:
                raise ValueError("validation fact cannot carry an observation kind")
        else:
            allowed_states = set()
            if self.observation is None:
                raise ValueError("observation fact requires an observation kind")
        if self.kind is DerivedEventKind.OBSERVATION:
            if self.state is not None or self.state_history:
                raise ValueError("observation fact cannot carry state")
        elif (
            self.state is None
            or not self.state_history
            or self.state_history[-1] != self.state
            or any(state not in allowed_states for state in self.state_history)
        ):
            raise ValueError("fact state must be an exact normalized final state")

        expected_content = canonical_sha256(_fact_content_payload(self))
        if self.content_sha256 != expected_content:
            raise ValueError("derived fact content hash is inconsistent")
        expected_fact_id = canonical_sha256(
            {
                "unit_type": "codex_derived_fact",
                "contract_version": CODEX_DERIVED_FACT_CONTRACT_VERSION,
                "event_contract_version": CODEX_EVENT_CONTRACT_VERSION,
                "normalizer_version": CODEX_EVENT_NORMALIZER_VERSION,
                "builder_version": CODEX_DERIVED_FACT_BUILDER_VERSION,
                "source_set_sha256": self.source_set_sha256,
                "scope": self.scope.model_dump(mode="json"),
                "role": self.role.value,
                "content_sha256": expected_content,
            }
        )
        if self.fact_id != expected_fact_id:
            raise ValueError("derived fact identity is inconsistent")
        return self


class CodexEndpointKind(StrEnum):
    FACT = "fact"
    NORMALIZED_EVENT = "normalized_event"
    RAW_ITEM = "raw_item"


class CodexEventEndpoint(_FrozenFactContract):
    endpoint_id: Sha256
    kind: CodexEndpointKind
    event_scope: EventScope | None = None
    fact_id: Sha256 | None = None
    source_event_id: Sha256 | None = None
    source_item_id: SafeIdentity | None = None
    source_locator: SourceLocator | None = None
    contract_version: Literal[CODEX_DERIVED_FACT_CONTRACT_VERSION] = (
        CODEX_DERIVED_FACT_CONTRACT_VERSION
    )

    @model_validator(mode="after")
    def _exact_endpoint(self) -> Self:
        if self.kind is CodexEndpointKind.FACT:
            if (
                self.fact_id is None
                or self.endpoint_id != self.fact_id
                or any(
                    value is not None
                    for value in (
                        self.event_scope,
                        self.source_event_id,
                        self.source_item_id,
                        self.source_locator,
                    )
                )
            ):
                raise ValueError("fact endpoint fields are inconsistent")
        elif self.kind is CodexEndpointKind.NORMALIZED_EVENT:
            if (
                self.source_event_id is None
                or self.endpoint_id != self.source_event_id
                or self.event_scope is None
                or any(
                    value is not None
                    for value in (self.fact_id, self.source_item_id, self.source_locator)
                )
            ):
                raise ValueError("normalized-event endpoint fields are inconsistent")
        else:
            if (
                self.event_scope is None
                or self.source_item_id is None
                or self.source_locator is None
                or self.fact_id is not None
                or self.source_event_id is not None
            ):
                raise ValueError("raw-item endpoint fields are inconsistent")
            _require_exact_raw_locator(
                self.event_scope,
                self.source_item_id,
                self.source_locator,
            )
            expected = canonical_sha256(
                {
                    "endpoint_kind": CodexEndpointKind.RAW_ITEM.value,
                    "event_scope": self.event_scope.model_dump(mode="json"),
                    "source_item_id": self.source_item_id,
                    "source_locator": self.source_locator,
                }
            )
            if self.endpoint_id != expected:
                raise ValueError("raw endpoint identity is inconsistent")
        return self


class CodexEventPredicate(StrEnum):
    CALL_RESULT = "call_result"
    CONTAINS = "contains"
    DERIVED_FROM = "derived_from"


class CodexDerivedEventLink(_FrozenFactContract):
    """A scoped two-ended link using only CX1-01/CX1-02 registered semantics."""

    link_id: Sha256
    scope: CodexFactPublicationScope
    predicate: CodexEventPredicate
    source: CodexEventEndpoint
    target: CodexEventEndpoint
    call_id: SafeIdentity | None = None
    call_type: CallType | None = None
    event_contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION
    builder_version: Literal[CODEX_DERIVED_FACT_BUILDER_VERSION] = (
        CODEX_DERIVED_FACT_BUILDER_VERSION
    )
    contract_version: Literal[CODEX_DERIVED_FACT_CONTRACT_VERSION] = (
        CODEX_DERIVED_FACT_CONTRACT_VERSION
    )

    @model_validator(mode="after")
    def _semantics_scope_and_identity(self) -> Self:
        kinds = (self.source.kind, self.target.kind)
        if self.predicate is CodexEventPredicate.DERIVED_FROM:
            if kinds != (CodexEndpointKind.FACT, CodexEndpointKind.NORMALIZED_EVENT):
                raise ValueError("derived_from requires fact -> normalized_event")
        elif self.predicate is CodexEventPredicate.CONTAINS:
            if kinds != (CodexEndpointKind.NORMALIZED_EVENT, CodexEndpointKind.RAW_ITEM):
                raise ValueError("contains requires normalized_event -> raw_item")
        elif kinds != (CodexEndpointKind.RAW_ITEM, CodexEndpointKind.RAW_ITEM):
            raise ValueError("call_result requires raw_item -> raw_item")

        if self.predicate is CodexEventPredicate.CALL_RESULT:
            if self.call_id is None or self.call_type is None:
                raise ValueError("call_result requires structured call identity")
        elif self.call_id is not None and self.predicate is CodexEventPredicate.CONTAINS:
            raise ValueError("contains does not carry call identity")
        for endpoint in (self.source, self.target):
            if endpoint.event_scope is not None:
                _require_event_scope(self.scope, endpoint.event_scope)
        expected = canonical_sha256(_link_identity_payload(self))
        if self.link_id != expected:
            raise ValueError("event link identity is inconsistent")
        return self


class CodexFactPublicationDiagnostics(_FrozenFactContract):
    authoritative_source_item_count: NonNegativeInt
    authoritative_result_count: NonNegativeInt
    normalized_event_count: NonNegativeInt
    normalized_link_count: NonNegativeInt
    normalized_diagnostic_count: NonNegativeInt
    derived_fact_count: NonNegativeInt
    deduplicated_fact_count: NonNegativeInt
    derived_link_count: NonNegativeInt
    output_window_count: NonNegativeInt
    missing_raw_output_count: NonNegativeInt
    weak_observation_count: NonNegativeInt
    excluded_input_count: NonNegativeInt
    contract_version: Literal[CODEX_DERIVED_FACT_CONTRACT_VERSION] = (
        CODEX_DERIVED_FACT_CONTRACT_VERSION
    )


class CodexFactPublication(_FrozenFactContract):
    """A complete deterministic stage payload for one generation."""

    publication_id: Sha256
    content_sha256: Sha256
    normalization_content_sha256: Sha256
    source_set_sha256: Sha256
    scope: CodexFactPublicationScope
    source_authority: CodexSourceAuthority
    normalization: CodexEventNormalizationResult
    output_windows: tuple[CodexLocatedOutputWindow, ...] = Field(default=(), max_length=512)
    facts: tuple[CodexDerivedFact, ...]
    links: tuple[CodexDerivedEventLink, ...]
    diagnostics: CodexFactPublicationDiagnostics
    event_contract_version: Literal[CODEX_EVENT_CONTRACT_VERSION] = CODEX_EVENT_CONTRACT_VERSION
    normalizer_version: Literal[CODEX_EVENT_NORMALIZER_VERSION] = CODEX_EVENT_NORMALIZER_VERSION
    state_machine_version: Literal[CODEX_EVENT_STATE_MACHINE_VERSION] = (
        CODEX_EVENT_STATE_MACHINE_VERSION
    )
    builder_version: Literal[CODEX_DERIVED_FACT_BUILDER_VERSION] = (
        CODEX_DERIVED_FACT_BUILDER_VERSION
    )
    schema_version: Literal[CODEX_DERIVED_FACT_SCHEMA_VERSION] = CODEX_DERIVED_FACT_SCHEMA_VERSION
    contract_version: Literal[CODEX_DERIVED_FACT_CONTRACT_VERSION] = (
        CODEX_DERIVED_FACT_CONTRACT_VERSION
    )

    @model_validator(mode="after")
    def _exact_publication(self) -> Self:
        _verify_fact_publication(self)
        return self


class CodexFactPublicationDisposition(StrEnum):
    PUBLISHED = "published"
    REPLAYED = "replayed"


class CodexFactPublicationResult(_FrozenFactContract):
    publication: CodexFactPublication
    disposition: CodexFactPublicationDisposition
    previous_active_generation_id: SafeIdentity | None = None
    active_generation_id: SafeIdentity
    active_publication_id: Sha256
    contract_version: Literal[CODEX_DERIVED_FACT_CONTRACT_VERSION] = (
        CODEX_DERIVED_FACT_CONTRACT_VERSION
    )


def _scope_key(scope: EventScope) -> tuple[str, ...]:
    return (
        scope.project_id,
        scope.source_id,
        scope.generation_id,
        scope.thread_id,
        scope.turn_id,
        scope.acl_ref,
    )


def _publication_scope_key(scope: CodexFactPublicationScope) -> tuple[str, ...]:
    return (
        scope.project_id,
        scope.repository_id,
        scope.thread_id,
        scope.generation_id,
        scope.source_id,
        scope.source_version,
        scope.acl_ref,
    )


def _active_scope_key(scope: CodexFactPublicationScope) -> tuple[str, ...]:
    return (
        scope.project_id,
        scope.repository_id,
        scope.thread_id,
        scope.source_id,
        scope.source_version,
        scope.acl_ref,
    )


def _verify_bound_normalizer() -> CodexNormalizerIdentity:
    module = sys.modules.get(CODEX_NORMALIZER_MODULE)
    package = sys.modules.get(CODEX_NORMALIZER_MODULE.rsplit(".", 1)[0])
    export = getattr(_cx1_normalizer_module, CODEX_NORMALIZER_EXPORT, None)
    package_export = (
        getattr(package, CODEX_NORMALIZER_EXPORT, None) if package is not None else None
    )
    if (
        module is not _cx1_normalizer_module
        or not isinstance(module, types.ModuleType)
        or export is not _BOUND_CX1_NORMALIZER
        or package_export is not _BOUND_CX1_NORMALIZER
        or type(export) is not types.FunctionType
        or export.__module__ != CODEX_NORMALIZER_MODULE
        or export.__qualname__ != CODEX_NORMALIZER_EXPORT
        or inspect.unwrap(export) is not export
        or getattr(module, "CODEX_EVENT_NORMALIZER_VERSION", None) != CODEX_EVENT_NORMALIZER_VERSION
    ):
        raise CodexFactValidationError(
            "canonical CX1-01 normalizer module/export identity is not intact"
        )
    for name, bound in _BOUND_CX1_MODULE_BINDINGS:
        current = getattr(module, name, _MISSING)
        if isinstance(bound, (types.FunctionType, type)):
            intact = current is bound
        else:
            intact = current == bound and type(current) is type(bound)
        if not intact:
            raise CodexFactValidationError(f"canonical CX1-01 normalizer binding changed: {name}")
    source_path = Path(str(module.__file__)).resolve()
    expected_path = Path(__file__).with_name("event_normalizer.py").resolve()
    if source_path != expected_path or not source_path.is_file():
        raise CodexFactValidationError("canonical CX1-01 normalizer source path changed")
    source_sha256 = "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest()
    code_sha256 = _function_code_digest(export)
    if (
        source_sha256 != CODEX_NORMALIZER_SOURCE_SHA256
        or code_sha256 != CODEX_NORMALIZER_CODE_SHA256
    ):
        raise CodexFactValidationError("canonical CX1-01 normalizer source/code digest changed")
    return CodexNormalizerIdentity()


def _source_item_sort_key(item: ObservableCodexItem) -> tuple[object, ...]:
    return (
        *_scope_key(item.scope),
        item.observable_order,
        item.item_id,
        item.source_locator,
        item.canonical_sha256(),
    )


def _canonical_source_items(
    source_items: Iterable[ObservableCodexItem],
    scope: CodexFactPublicationScope,
) -> tuple[ObservableCodexItem, ...]:
    raw = tuple(source_items)
    if not raw:
        raise CodexFactValidationError("authoritative source set must not be empty")
    if any(type(item) is not ObservableCodexItem for item in raw):
        raise CodexFactValidationError(
            "source set requires exact frozen ObservableCodexItem instances"
        )
    unique = {item.canonical_sha256(): item for item in raw}
    canonical = tuple(sorted(unique.values(), key=_source_item_sort_key))
    for item in canonical:
        _require_event_scope(scope, item.scope)
    return canonical


def _payload_value(payload: Mapping[str, Any], path: tuple[str, ...]) -> object:
    current: object = payload
    for segment in path:
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


_MISSING = object()


def _validate_authoritative_output_content(content: str) -> tuple[str, int, int]:
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CodexFactValidationError("authoritative output must be valid UTF-8") from exc
    if len(encoded) > _MAX_OUTPUT_BYTES:
        raise CodexFactValidationError("authoritative output exceeds the isolated safety bound")
    if "\x00" in content:
        raise CodexFactValidationError("NUL is forbidden in authoritative output")
    _validate_raw_output_privacy(content)
    return _content_sha256(content), len(content), len(encoded)


def _authoritative_raw_outputs(
    item: ObservableCodexItem,
) -> tuple[tuple[CodexOutputRole, str], ...]:
    if item.item_type not in {
        ObservableItemType.TOOL_RESULT,
        ObservableItemType.COMMAND_RESULT,
        ObservableItemType.PATCH_RESULT,
    }:
        return ()
    values: dict[CodexOutputRole, str] = {}
    for path in (("result",), ("execution", "result")):
        value = _payload_value(item.payload, path)
        if value is _MISSING or value is None or isinstance(value, Mapping):
            continue
        if not isinstance(value, str):
            raise CodexFactValidationError(
                "authoritative result output must be an exact string or envelope"
            )
        existing = values.get(CodexOutputRole.RESULT)
        if existing is not None and existing != value:
            raise CodexFactValidationError("authoritative result output is ambiguous")
        _validate_authoritative_output_content(value)
        values[CodexOutputRole.RESULT] = value
    for path, role in _OUTPUT_PATHS:
        value = _payload_value(item.payload, path)
        if value is _MISSING or value is None:
            continue
        if not isinstance(value, str):
            raise CodexFactValidationError(
                f"authoritative {role.value} output must be an exact string"
            )
        existing = values.get(role)
        if existing is not None and existing != value:
            raise CodexFactValidationError(f"authoritative {role.value} output is ambiguous")
        _validate_authoritative_output_content(value)
        values[role] = value
    return tuple((role, values[role]) for role in sorted(values, key=lambda item: item.value))


def _source_authority_item_key(item: CodexSourceItemAuthority) -> tuple[str, ...]:
    return (
        *_scope_key(item.source_scope),
        item.source_locator,
        item.source_item_id,
        item.source_item_sha256,
    )


def _source_set_digest(
    items: tuple[CodexSourceItemAuthority, ...],
    normalizer: CodexNormalizerIdentity,
) -> str:
    return canonical_sha256(
        {
            "authority_version": CODEX_SOURCE_AUTHORITY_VERSION,
            "normalizer": normalizer.model_dump(mode="json"),
            "items": [item.model_dump(mode="json") for item in items],
        }
    )


def _build_source_authority(
    source_items: tuple[ObservableCodexItem, ...],
    normalizer: CodexNormalizerIdentity,
) -> CodexSourceAuthority:
    authorities: list[CodexSourceItemAuthority] = []
    for item in source_items:
        raw_outputs = []
        for role, content in _authoritative_raw_outputs(item):
            content_hash, total_chars, total_bytes = _validate_authoritative_output_content(content)
            raw_outputs.append(
                CodexRawOutputAuthority(
                    role=role,
                    content_sha256=content_hash,
                    total_chars=total_chars,
                    total_utf8_bytes=total_bytes,
                )
            )
        authorities.append(
            CodexSourceItemAuthority(
                source_item_id=item.item_id,
                source_locator=item.source_locator,
                source_scope=item.scope,
                item_type=item.item_type,
                source_item_sha256=item.canonical_sha256(),
                raw_outputs=tuple(
                    sorted(
                        raw_outputs,
                        key=lambda output: (
                            output.role.value,
                            output.content_sha256,
                        ),
                    )
                ),
            )
        )
    exact = tuple(sorted(authorities, key=_source_authority_item_key))
    return CodexSourceAuthority(
        source_set_sha256=_source_set_digest(exact, normalizer),
        item_count=len(exact),
        items=exact,
        normalizer=normalizer,
    )


def _run_bound_normalizer(
    source_items: tuple[ObservableCodexItem, ...],
) -> CodexEventNormalizationResult:
    _verify_bound_normalizer()
    result = _BOUND_CX1_NORMALIZER(source_items)
    if type(result) is not CodexEventNormalizationResult:
        raise CodexFactValidationError(
            "canonical CX1-01 normalizer returned a fake or subclassed result"
        )
    _verify_normalization_result(result)
    return result


def _require_event_scope(
    publication_scope: CodexFactPublicationScope,
    event_scope: EventScope,
) -> None:
    if (
        event_scope.project_id != publication_scope.project_id
        or event_scope.source_id != publication_scope.source_id
        or event_scope.generation_id != publication_scope.generation_id
        or event_scope.thread_id != publication_scope.thread_id
        or event_scope.acl_ref != publication_scope.acl_ref
    ):
        raise CodexFactScopeError(
            "event is outside exact project/thread/generation/source/ACL scope"
        )


def _locator_order(locator: str) -> int:
    return int(locator.rsplit("#event=", 1)[1])


def _require_exact_raw_locator(
    scope: EventScope,
    source_item_id: str,
    source_locator: str,
) -> None:
    parsed = urlsplit(source_locator)
    match = re.fullmatch(r"event=([1-9][0-9]*)", parsed.fragment)
    if match is None:
        raise CodexFactScopeError("raw endpoint has a bad locator")
    order = int(match.group(1)) - 1
    expected = canonical_codex_locator_v1(scope, source_item_id, order)
    if source_locator != expected:
        raise CodexFactScopeError("raw endpoint locator does not match its exact identity")
    path_parts = parsed.path.split("/")
    if len(path_parts) != 6 or unquote(path_parts[5]) != source_item_id:
        raise CodexFactScopeError("raw endpoint locator item segment is inconsistent")


def _normalization_payload(result: CodexEventNormalizationResult) -> dict[str, object]:
    return {
        "contract_version": CODEX_EVENT_CONTRACT_VERSION,
        "normalizer_version": CODEX_EVENT_NORMALIZER_VERSION,
        "state_machine_version": CODEX_EVENT_STATE_MACHINE_VERSION,
        "events": [event.model_dump(mode="json") for event in result.events],
        "links": [link.model_dump(mode="json") for link in result.links],
        "diagnostics": [diagnostic.model_dump(mode="json") for diagnostic in result.diagnostics],
    }


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


def _link_sort_key(link: NormalizedEventLink) -> tuple[object, ...]:
    return (
        *_scope_key(link.scope),
        _locator_order(link.call_locator),
        _locator_order(link.result_locator),
        link.call_locator,
        link.result_locator,
        link.link_id,
    )


def _diagnostic_sort_key(diagnostic: Any) -> tuple[object, ...]:
    return (
        diagnostic.code.value,
        diagnostic.severity.value,
        *_scope_key(diagnostic.scope),
        diagnostic.source_item_ids,
        diagnostic.call_id or "",
    )


def _cx1_event_id(identity: Mapping[str, object]) -> str:
    return canonical_sha256(
        {
            "contract_version": CODEX_EVENT_CONTRACT_VERSION,
            "normalizer_version": CODEX_EVENT_NORMALIZER_VERSION,
            **identity,
        }
    )


def _evidence_payload(event: NormalizedEvent) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in event.evidence]


def _expected_normalized_event_id(event: NormalizedEvent) -> str:
    common: dict[str, object] = {
        "kind": event.kind.value,
        "scope": event.scope.model_dump(mode="json"),
    }
    if isinstance(event, NormalizedObservationEvent):
        return _cx1_event_id(
            {
                **common,
                "role": event.role.value,
                "observation": event.observation.value,
                "reason_code": event.reason_code.value,
                "evidence": _evidence_payload(event),
            }
        )
    if isinstance(event, NormalizedActionEvent):
        if event.role is DerivedEventRole.ACTION_PROPOSAL:
            return _cx1_event_id(
                {
                    **common,
                    "role": event.role.value,
                    "state_history": [state.value for state in event.state_history],
                    "evidence": _evidence_payload(event),
                }
            )
        return _cx1_event_id(
            {
                **common,
                "role": event.role.value,
                "call_id": event.call_id,
                "call_type": event.call_type,
                "state_history": [state.value for state in event.state_history],
                "command_argv": list(event.command_argv),
                "targets": list(event.targets),
                "exit_code": event.exit_code,
                "evidence": _evidence_payload(event),
            }
        )
    if isinstance(event, NormalizedPatchEvent):
        if event.state is PatchState.PATCH_PROPOSED:
            return _cx1_event_id(
                {
                    **common,
                    "state_history": [state.value for state in event.state_history],
                    "targets": list(event.targets),
                    "evidence": _evidence_payload(event),
                }
            )
        return _cx1_event_id(
            {
                **common,
                "call_id": event.call_id,
                "call_type": event.call_type,
                "state_history": [state.value for state in event.state_history],
                "targets": list(event.targets),
                "evidence": _evidence_payload(event),
            }
        )
    if event.role is DerivedEventRole.VALIDATION_MENTION:
        return _cx1_event_id(
            {
                **common,
                "state_history": [state.value for state in event.state_history],
                "evidence": _evidence_payload(event),
            }
        )
    return _cx1_event_id(
        {
            **common,
            "call_id": event.call_id,
            "call_type": event.call_type,
            "state_history": [state.value for state in event.state_history],
            "command_argv": list(event.command_argv),
            "targets": list(event.targets),
            "exit_code": event.exit_code,
            "evidence": _evidence_payload(event),
        }
    )


def _validate_normalized_truth_shape(event: NormalizedEvent) -> None:
    if isinstance(event, NormalizedObservationEvent):
        expected = {
            ObservationKind.PLAN_ONLY: (
                DerivedEventRole.PLAN_ONLY,
                EventReasonCode.PLAN_ONLY,
            ),
            ObservationKind.ASSISTANT_CLAIM_ONLY: (
                DerivedEventRole.ASSISTANT_CLAIM_ONLY,
                EventReasonCode.ASSISTANT_CLAIM_ONLY,
            ),
            ObservationKind.FILE_CHANGE: (
                DerivedEventRole.FILE_CHANGE_OBSERVATION,
                EventReasonCode.FILE_CHANGE_OBSERVED,
            ),
            ObservationKind.EXISTING_DERIVED_VALIDATION: (
                DerivedEventRole.UNTRUSTED_DERIVED_VALIDATION,
                EventReasonCode.EXISTING_DERIVED_VALIDATION_IGNORED,
            ),
            ObservationKind.CONTRADICTORY_PROVENANCE: (
                DerivedEventRole.CONTRADICTORY_PROVENANCE,
                EventReasonCode.ROLE_PROVENANCE_CONFLICT,
            ),
        }[event.observation]
        if (event.role, event.reason_code) != expected:
            raise CodexFactValidationError("CX1-01 observation truth shape is inconsistent")
        return

    if isinstance(event, NormalizedActionEvent):
        if event.role is DerivedEventRole.ACTION_PROPOSAL:
            if (
                event.state_history != (ActionState.PROPOSED,)
                or event.reason_code is not EventReasonCode.ACTION_PROPOSAL_OBSERVED
                or event.call_id is not None
                or event.call_type is not None
                or event.command_argv
                or event.targets
                or event.exit_code is not None
            ):
                raise CodexFactValidationError("CX1-01 action proposal shape is inconsistent")
            return
        if event.role not in {
            DerivedEventRole.TOOL_ACTION,
            DerivedEventRole.COMMAND_ACTION,
        }:
            raise CodexFactValidationError("CX1-01 action role is not registered")
        expected_history = (
            (ActionState.INVOKED,)
            if event.state is ActionState.INVOKED
            else (ActionState.INVOKED, event.state)
        )
        expected_reason = {
            ActionState.INVOKED: EventReasonCode.ACTION_INVOCATION_OBSERVED,
            ActionState.COMPLETED: EventReasonCode.RESULT_COMPLETION_OBSERVED,
            ActionState.FAILED: EventReasonCode.RESULT_FAILURE_OBSERVED,
            ActionState.TIMEOUT: EventReasonCode.RESULT_TIMEOUT_OBSERVED,
            ActionState.CANCELLED: EventReasonCode.RESULT_CANCELLATION_OBSERVED,
            ActionState.UNKNOWN: EventReasonCode.RESULT_OUTCOME_UNKNOWN,
        }[event.state]
        if (
            event.state_history != expected_history
            or event.reason_code is not expected_reason
            or event.call_type is None
            or event.role is DerivedEventRole.TOOL_ACTION
            and (event.command_argv or event.targets)
        ):
            raise CodexFactValidationError("CX1-01 action truth shape is inconsistent")
        return

    if isinstance(event, NormalizedPatchEvent):
        if event.role is not DerivedEventRole.PATCH_CHANGE:
            raise CodexFactValidationError("CX1-01 patch role is not registered")
        if event.state is PatchState.PATCH_PROPOSED:
            expected_history = (PatchState.PATCH_PROPOSED,)
            expected_reason = EventReasonCode.PATCH_PROPOSAL_OBSERVED
            exact_call = event.call_id is None and event.call_type is None
        else:
            expected_history = (
                (PatchState.APPLY_INVOKED,)
                if event.state is PatchState.APPLY_INVOKED
                else (PatchState.APPLY_INVOKED, event.state)
            )
            expected_reason = {
                PatchState.APPLY_INVOKED: (EventReasonCode.PATCH_APPLY_INVOCATION_OBSERVED),
                PatchState.APPLIED: EventReasonCode.PATCH_SUCCESS_OBSERVED,
                PatchState.FAILED: EventReasonCode.PATCH_FAILURE_OBSERVED,
            }[event.state]
            exact_call = event.call_type == "patch_apply"
        if (
            event.state_history != expected_history
            or event.reason_code is not expected_reason
            or not exact_call
        ):
            raise CodexFactValidationError("CX1-01 patch truth shape is inconsistent")
        return

    if event.role is DerivedEventRole.VALIDATION_MENTION:
        if (
            event.state_history != (ValidationState.MENTIONED,)
            or event.reason_code is not EventReasonCode.VALIDATION_MENTION_OBSERVED
            or event.call_id is not None
            or event.call_type is not None
            or event.command_argv
            or event.targets
            or event.exit_code is not None
        ):
            raise CodexFactValidationError("CX1-01 validation mention shape is inconsistent")
        return
    if event.role is not DerivedEventRole.COMMAND_VALIDATION or event.call_type != "command":
        raise CodexFactValidationError("CX1-01 validation role is not registered")
    if event.state is ValidationState.COMMAND_INVOKED:
        expected_history = (ValidationState.COMMAND_INVOKED,)
        expected_reason = EventReasonCode.VALIDATION_COMMAND_INVOKED
    elif event.state is ValidationState.TARGET_UNKNOWN:
        expected_history = (
            (
                ValidationState.COMMAND_INVOKED,
                ValidationState.EXIT_OBSERVED,
                ValidationState.TARGET_UNKNOWN,
            )
            if event.exit_code is not None
            else (
                ValidationState.COMMAND_INVOKED,
                ValidationState.TARGET_UNKNOWN,
            )
        )
        expected_reason = EventReasonCode.VALIDATION_TARGET_UNKNOWN
    elif event.state is ValidationState.PASSED:
        expected_history = (
            ValidationState.COMMAND_INVOKED,
            ValidationState.EXIT_OBSERVED,
            ValidationState.PASSED,
        )
        expected_reason = EventReasonCode.VALIDATION_EXIT_ZERO
    elif event.state is ValidationState.FAILED:
        expected_history = (
            ValidationState.COMMAND_INVOKED,
            ValidationState.EXIT_OBSERVED,
            ValidationState.FAILED,
        )
        expected_reason = EventReasonCode.VALIDATION_EXIT_NONZERO
    else:
        raise CodexFactValidationError("CX1-01 validation state is not materializable")
    if event.state_history != expected_history or event.reason_code is not expected_reason:
        raise CodexFactValidationError("CX1-01 validation truth shape is inconsistent")


def _expected_normalized_link_id(link: NormalizedEventLink) -> str:
    return _cx1_event_id(
        {
            "role": link.role.value,
            "scope": link.scope.model_dump(mode="json"),
            "call_id": link.call_id,
            "call_type": link.call_type,
            "call_item_id": link.call_item_id,
            "call_locator": link.call_locator,
            "result_item_id": link.result_item_id,
            "result_locator": link.result_locator,
        }
    )


def _summary_state_counts(events: Iterable[NormalizedEvent], event_type: type) -> tuple:
    counts = Counter(event.state.value for event in events if isinstance(event, event_type))
    return tuple((state, counts[state]) for state in sorted(counts))


def _verify_normalization_result(result: CodexEventNormalizationResult) -> None:
    expected_digest = canonical_sha256(_normalization_payload(result))
    if result.summary.content_sha256 != expected_digest:
        raise CodexFactValidationError("CX1-01 normalization content digest is inconsistent")
    if result.summary.event_count != len(result.events):
        raise CodexFactValidationError("CX1-01 event count is inconsistent")
    if result.summary.link_count != len(result.links):
        raise CodexFactValidationError("CX1-01 link count is inconsistent")
    if result.summary.diagnostic_count != len(result.diagnostics):
        raise CodexFactValidationError("CX1-01 diagnostic count is inconsistent")
    if result.summary.unique_input_count > result.summary.input_count:
        raise CodexFactValidationError("CX1-01 unique input count is inconsistent")
    if tuple(sorted(result.events, key=_event_sort_key)) != result.events:
        raise CodexFactValidationError("CX1-01 events are not in canonical order")
    if tuple(sorted(result.links, key=_link_sort_key)) != result.links:
        raise CodexFactValidationError("CX1-01 links are not in canonical order")
    if tuple(sorted(result.diagnostics, key=_diagnostic_sort_key)) != result.diagnostics:
        raise CodexFactValidationError("CX1-01 diagnostics are not in canonical order")
    if len({event.event_id for event in result.events}) != len(result.events):
        raise CodexFactValidationError("CX1-01 event identities are not unique")
    if len({link.link_id for link in result.links}) != len(result.links):
        raise CodexFactValidationError("CX1-01 link identities are not unique")

    raw_evidence: set[tuple[str, str, tuple[str, ...]]] = set()
    for event in result.events:
        _validate_normalized_truth_shape(event)
        if event.event_id != _expected_normalized_event_id(event):
            raise CodexFactValidationError("CX1-01 event identity is inconsistent")
        evidence_keys = tuple(
            (item.source_locator, item.source_item_id, item.evidence_type.value)
            for item in event.evidence
        )
        if evidence_keys != tuple(sorted(set(evidence_keys))):
            raise CodexFactValidationError("CX1-01 event evidence is not sorted and unique")
        for evidence in event.evidence:
            _require_exact_raw_locator(
                event.scope,
                evidence.source_item_id,
                evidence.source_locator,
            )
            raw_evidence.add(
                (evidence.source_item_id, evidence.source_locator, _scope_key(event.scope))
            )
    for link in result.links:
        if link.link_id != _expected_normalized_link_id(link):
            raise CodexFactValidationError("CX1-01 link identity is inconsistent")
        _require_exact_raw_locator(link.scope, link.call_item_id, link.call_locator)
        _require_exact_raw_locator(link.scope, link.result_item_id, link.result_locator)
        if _locator_order(link.result_locator) <= _locator_order(link.call_locator):
            raise CodexFactValidationError("CX1-01 result endpoint is not after its call")
        call_key = (link.call_item_id, link.call_locator, _scope_key(link.scope))
        result_key = (link.result_item_id, link.result_locator, _scope_key(link.scope))
        if call_key not in raw_evidence or result_key not in raw_evidence:
            raise CodexFactValidationError("CX1-01 call/result link has an orphan endpoint")
        if not any(
            event.scope == link.scope
            and getattr(event, "call_id", None) == link.call_id
            and getattr(event, "call_type", None) == link.call_type
            and {
                (evidence.source_item_id, evidence.source_locator) for evidence in event.evidence
            }.issuperset(
                {
                    (link.call_item_id, link.call_locator),
                    (link.result_item_id, link.result_locator),
                }
            )
            for event in result.events
        ):
            raise CodexFactValidationError(
                "CX1-01 call/result endpoints lack one exact normalized event"
            )
    summary_counts = (
        tuple((item.state, item.count) for item in result.summary.action_states),
        tuple((item.state, item.count) for item in result.summary.patch_states),
        tuple((item.state, item.count) for item in result.summary.validation_states),
    )
    expected_counts = (
        _summary_state_counts(result.events, NormalizedActionEvent),
        _summary_state_counts(result.events, NormalizedPatchEvent),
        _summary_state_counts(result.events, NormalizedValidationEvent),
    )
    if summary_counts != expected_counts:
        raise CodexFactValidationError("CX1-01 summary state counts are inconsistent")
    expected_unknown = sum(
        (isinstance(event, NormalizedActionEvent) and event.state is ActionState.UNKNOWN)
        or (
            isinstance(event, NormalizedValidationEvent)
            and event.state is ValidationState.TARGET_UNKNOWN
        )
        for event in result.events
    )
    if result.summary.unknown_state_count != expected_unknown:
        raise CodexFactValidationError("CX1-01 unknown-state count is inconsistent")


def _window_key(window: CodexOutputWindow) -> tuple[str, ...]:
    return (
        window.role.value,
        window.content_sha256,
        window.head,
        window.tail,
        str(window.max_chars),
        str(window.error_tail),
        str(window.redacted),
    )


def _located_window_key(window: CodexLocatedOutputWindow) -> tuple[str, ...]:
    return (
        *_scope_key(window.source_scope),
        window.source_locator,
        window.source_item_id,
        window.source_item_sha256,
        *_window_key(window.window),
    )


def _coerce_normalization_result(
    result: CodexEventNormalizationResult | Mapping[str, Any],
) -> CodexEventNormalizationResult:
    try:
        exact = (
            result
            if type(result) is CodexEventNormalizationResult
            else CodexEventNormalizationResult.model_validate(result)
        )
    except Exception as exc:
        raise CodexFactValidationError(
            "input must match the exact frozen CX1-01 normalization contract"
        ) from exc
    if type(exact) is not CodexEventNormalizationResult:
        raise CodexFactValidationError(
            "subclassed or wrapped CX1-01 normalization result is forbidden"
        )
    _verify_normalization_result(exact)
    return exact


def _validate_scope_and_windows(
    scope: CodexFactPublicationScope,
    result: CodexEventNormalizationResult,
    windows: tuple[CodexLocatedOutputWindow, ...],
    source_authority: CodexSourceAuthority,
) -> None:
    for event in result.events:
        _require_event_scope(scope, event.scope)
    for link in result.links:
        _require_event_scope(scope, link.scope)
    for diagnostic in result.diagnostics:
        _require_event_scope(scope, diagnostic.scope)

    authority_by_endpoint = {
        (
            item.source_item_id,
            item.source_locator,
            _scope_key(item.source_scope),
        ): item
        for item in source_authority.items
    }
    result_evidence: dict[
        tuple[str, str, tuple[str, ...]],
        list[NormalizedEvent],
    ] = {}
    for event in result.events:
        for evidence in event.evidence:
            if evidence.evidence_type in _RESULT_EVIDENCE_TYPES:
                result_evidence.setdefault(
                    (
                        evidence.source_item_id,
                        evidence.source_locator,
                        _scope_key(event.scope),
                    ),
                    [],
                ).append(event)
    if any(endpoint not in authority_by_endpoint for endpoint in result_evidence):
        raise CodexFactValidationError("normalized result endpoint is absent from source authority")
    seen_windows: set[tuple[tuple[str, str, tuple[str, ...]], str]] = set()
    for located in windows:
        endpoint = (
            located.source_item_id,
            located.source_locator,
            _scope_key(located.source_scope),
        )
        events = result_evidence.get(endpoint)
        if not events:
            raise CodexFactValidationError(
                "output window must reference an exact observed result endpoint"
            )
        authority = authority_by_endpoint.get(endpoint)
        if authority is None or authority.source_item_sha256 != located.source_item_sha256:
            raise CodexFactValidationError("output window lacks exact source-item authority")
        raw_output = next(
            (output for output in authority.raw_outputs if output.role is located.window.role),
            None,
        )
        if (
            raw_output is None
            or raw_output.content_sha256 != located.window.content_sha256
            or raw_output.total_chars != located.window.total_chars
            or raw_output.total_utf8_bytes != located.window.total_utf8_bytes
        ):
            raise CodexFactValidationError(
                "output window metadata does not match raw content authority"
            )
        must_preserve_error_tail = any(_event_is_error(event) for event in events)
        if (
            located.window.error_tail is not must_preserve_error_tail
            or located.window.max_chars != CODEX_PUBLICATION_OUTPUT_WINDOW_MAX_CHARS
            or located.window.redacted
        ):
            raise CodexFactValidationError(
                "output window error/redaction/policy classification is inconsistent"
            )
        seen_windows.add((endpoint, located.window.role.value))
    expected_windows = {
        (endpoint, output.role.value)
        for endpoint in result_evidence
        for output in authority_by_endpoint[endpoint].raw_outputs
    }
    if seen_windows != expected_windows:
        raise CodexFactValidationError(
            "verified output windows do not exactly cover authoritative raw output"
        )


def _build_verified_output_windows(
    source_items: tuple[ObservableCodexItem, ...],
    result: CodexEventNormalizationResult,
) -> tuple[tuple[CodexLocatedOutputWindow, ...], int, int]:
    source_by_endpoint = {
        (item.item_id, item.source_locator, _scope_key(item.scope)): item for item in source_items
    }
    result_events: dict[
        tuple[str, str, tuple[str, ...]],
        list[NormalizedEvent],
    ] = {}
    for event in result.events:
        for evidence in event.evidence:
            if evidence.evidence_type in _RESULT_EVIDENCE_TYPES:
                result_events.setdefault(
                    (
                        evidence.source_item_id,
                        evidence.source_locator,
                        _scope_key(event.scope),
                    ),
                    [],
                ).append(event)
    windows: list[CodexLocatedOutputWindow] = []
    missing = 0
    for endpoint in sorted(result_events):
        item = source_by_endpoint.get(endpoint)
        if item is None:
            raise CodexFactValidationError(
                "normalized result endpoint is absent from source authority"
            )
        raw_outputs = _authoritative_raw_outputs(item)
        if not raw_outputs:
            missing += 1
            continue
        is_error = any(_event_is_error(event) for event in result_events[endpoint])
        for role, content in raw_outputs:
            windows.append(
                CodexLocatedOutputWindow(
                    source_item_id=item.item_id,
                    source_locator=item.source_locator,
                    source_scope=item.scope,
                    source_item_sha256=item.canonical_sha256(),
                    window=build_codex_output_window_v1(
                        content,
                        max_chars=CODEX_PUBLICATION_OUTPUT_WINDOW_MAX_CHARS,
                        role=role,
                        error_observed=is_error,
                        redacted=False,
                    ),
                )
            )
    exact = tuple(sorted(windows, key=_located_window_key))
    return exact, len(result_events), missing


def _event_is_error(event: NormalizedEvent) -> bool:
    state = getattr(event, "state", None)
    exit_code = getattr(event, "exit_code", None)
    return state in {
        ActionState.FAILED,
        ActionState.TIMEOUT,
        ActionState.CANCELLED,
        PatchState.FAILED,
        ValidationState.FAILED,
    } or exit_code not in {None, 0}


def _fact_content_payload(fact: CodexDerivedFact) -> dict[str, object]:
    return {
        "kind": fact.kind.value,
        "role": fact.role.value,
        "reason_code": fact.reason_code.value,
        "state": fact.state,
        "state_history": list(fact.state_history),
        "observation": fact.observation.value if fact.observation is not None else None,
        "call_type": fact.call_type,
        "command_argv": list(fact.command_argv),
        "targets": list(fact.targets),
        "exit_code": fact.exit_code,
        "output_windows": [window.model_dump(mode="json") for window in fact.output_windows],
    }


def _fact_from_event(
    scope: CodexFactPublicationScope,
    source_set_sha256: str,
    event: NormalizedEvent,
    windows_by_locator: Mapping[str, tuple[CodexOutputWindow, ...]],
) -> CodexDerivedFact:
    windows = {
        _window_key(window): window
        for evidence in event.evidence
        for window in windows_by_locator.get(evidence.source_locator, ())
    }
    exact_windows = tuple(windows[key] for key in sorted(windows))
    state = getattr(event, "state", None)
    history = getattr(event, "state_history", ())
    content = {
        "kind": event.kind.value,
        "role": event.role.value,
        "reason_code": event.reason_code.value,
        "state": state.value if state is not None else None,
        "state_history": [item.value for item in history],
        "observation": (
            event.observation.value if isinstance(event, NormalizedObservationEvent) else None
        ),
        "call_type": getattr(event, "call_type", None),
        "command_argv": list(getattr(event, "command_argv", ())),
        "targets": list(getattr(event, "targets", ())),
        "exit_code": getattr(event, "exit_code", None),
        "output_windows": [window.model_dump(mode="json") for window in exact_windows],
    }
    content_hash = canonical_sha256(content)
    fact_id = canonical_sha256(
        {
            "unit_type": "codex_derived_fact",
            "contract_version": CODEX_DERIVED_FACT_CONTRACT_VERSION,
            "event_contract_version": CODEX_EVENT_CONTRACT_VERSION,
            "normalizer_version": CODEX_EVENT_NORMALIZER_VERSION,
            "builder_version": CODEX_DERIVED_FACT_BUILDER_VERSION,
            "source_set_sha256": source_set_sha256,
            "scope": scope.model_dump(mode="json"),
            "role": event.role.value,
            "content_sha256": content_hash,
        }
    )
    return CodexDerivedFact(
        fact_id=fact_id,
        content_sha256=content_hash,
        source_set_sha256=source_set_sha256,
        scope=scope,
        kind=event.kind,
        role=event.role,
        reason_code=event.reason_code,
        state=state.value if state is not None else None,
        state_history=tuple(item.value for item in history),
        observation=(event.observation if isinstance(event, NormalizedObservationEvent) else None),
        call_type=getattr(event, "call_type", None),
        command_argv=getattr(event, "command_argv", ()),
        targets=getattr(event, "targets", ()),
        exit_code=getattr(event, "exit_code", None),
        output_windows=exact_windows,
    )


def _fact_endpoint(fact: CodexDerivedFact) -> CodexEventEndpoint:
    return CodexEventEndpoint(
        endpoint_id=fact.fact_id,
        kind=CodexEndpointKind.FACT,
        fact_id=fact.fact_id,
    )


def _event_endpoint(event: NormalizedEvent) -> CodexEventEndpoint:
    return CodexEventEndpoint(
        endpoint_id=event.event_id,
        kind=CodexEndpointKind.NORMALIZED_EVENT,
        event_scope=event.scope,
        source_event_id=event.event_id,
    )


def _raw_endpoint(
    scope: EventScope,
    source_item_id: str,
    source_locator: str,
) -> CodexEventEndpoint:
    endpoint_id = canonical_sha256(
        {
            "endpoint_kind": CodexEndpointKind.RAW_ITEM.value,
            "event_scope": scope.model_dump(mode="json"),
            "source_item_id": source_item_id,
            "source_locator": source_locator,
        }
    )
    return CodexEventEndpoint(
        endpoint_id=endpoint_id,
        kind=CodexEndpointKind.RAW_ITEM,
        event_scope=scope,
        source_item_id=source_item_id,
        source_locator=source_locator,
    )


def _link_identity_payload(link: CodexDerivedEventLink) -> dict[str, object]:
    return {
        "contract_version": CODEX_DERIVED_FACT_CONTRACT_VERSION,
        "builder_version": CODEX_DERIVED_FACT_BUILDER_VERSION,
        "scope": link.scope.model_dump(mode="json"),
        "predicate": link.predicate.value,
        "source": link.source.model_dump(mode="json"),
        "target": link.target.model_dump(mode="json"),
        "call_id": link.call_id,
        "call_type": link.call_type,
    }


def _make_link(
    *,
    scope: CodexFactPublicationScope,
    predicate: CodexEventPredicate,
    source: CodexEventEndpoint,
    target: CodexEventEndpoint,
    call_id: str | None = None,
    call_type: str | None = None,
) -> CodexDerivedEventLink:
    payload = {
        "contract_version": CODEX_DERIVED_FACT_CONTRACT_VERSION,
        "builder_version": CODEX_DERIVED_FACT_BUILDER_VERSION,
        "scope": scope.model_dump(mode="json"),
        "predicate": predicate.value,
        "source": source.model_dump(mode="json"),
        "target": target.model_dump(mode="json"),
        "call_id": call_id,
        "call_type": call_type,
    }
    return CodexDerivedEventLink(
        link_id=canonical_sha256(payload),
        scope=scope,
        predicate=predicate,
        source=source,
        target=target,
        call_id=call_id,
        call_type=call_type,
    )


def _derive_facts_and_links(
    scope: CodexFactPublicationScope,
    source_authority: CodexSourceAuthority,
    result: CodexEventNormalizationResult,
    windows: tuple[CodexLocatedOutputWindow, ...],
) -> tuple[
    tuple[CodexDerivedFact, ...],
    tuple[CodexDerivedEventLink, ...],
    CodexFactPublicationDiagnostics,
]:
    windows_by_locator: dict[str, list[CodexOutputWindow]] = {}
    for located in windows:
        windows_by_locator.setdefault(located.source_locator, []).append(located.window)
    exact_windows_by_locator = {
        locator: tuple(sorted(values, key=_window_key))
        for locator, values in windows_by_locator.items()
    }

    facts_by_id: dict[str, CodexDerivedFact] = {}
    event_fact: dict[str, CodexDerivedFact] = {}
    for event in result.events:
        fact = _fact_from_event(
            scope,
            source_authority.source_set_sha256,
            event,
            exact_windows_by_locator,
        )
        existing = facts_by_id.get(fact.fact_id)
        if existing is not None and existing != fact:
            raise CodexFactValidationError("derived fact identity collision")
        facts_by_id[fact.fact_id] = fact
        event_fact[event.event_id] = fact

    links_by_id: dict[str, CodexDerivedEventLink] = {}
    for event in result.events:
        fact = event_fact[event.event_id]
        derived = _make_link(
            scope=scope,
            predicate=CodexEventPredicate.DERIVED_FROM,
            source=_fact_endpoint(fact),
            target=_event_endpoint(event),
            call_id=getattr(event, "call_id", None),
            call_type=getattr(event, "call_type", None),
        )
        links_by_id[derived.link_id] = derived
        for evidence in event.evidence:
            contains = _make_link(
                scope=scope,
                predicate=CodexEventPredicate.CONTAINS,
                source=_event_endpoint(event),
                target=_raw_endpoint(
                    event.scope,
                    evidence.source_item_id,
                    evidence.source_locator,
                ),
            )
            links_by_id[contains.link_id] = contains
    for link in result.links:
        call_result = _make_link(
            scope=scope,
            predicate=CodexEventPredicate.CALL_RESULT,
            source=_raw_endpoint(
                link.scope,
                link.call_item_id,
                link.call_locator,
            ),
            target=_raw_endpoint(
                link.scope,
                link.result_item_id,
                link.result_locator,
            ),
            call_id=link.call_id,
            call_type=link.call_type,
        )
        links_by_id[call_result.link_id] = call_result

    facts = tuple(facts_by_id[key] for key in sorted(facts_by_id))
    links = tuple(links_by_id[key] for key in sorted(links_by_id))
    authority_by_endpoint = {
        (
            item.source_item_id,
            item.source_locator,
            _scope_key(item.source_scope),
        ): item
        for item in source_authority.items
    }
    result_endpoints = {
        (
            evidence.source_item_id,
            evidence.source_locator,
            _scope_key(event.scope),
        )
        for event in result.events
        for evidence in event.evidence
        if evidence.evidence_type in _RESULT_EVIDENCE_TYPES
    }
    diagnostics = CodexFactPublicationDiagnostics(
        authoritative_source_item_count=source_authority.item_count,
        authoritative_result_count=len(result_endpoints),
        normalized_event_count=len(result.events),
        normalized_link_count=len(result.links),
        normalized_diagnostic_count=len(result.diagnostics),
        derived_fact_count=len(facts),
        deduplicated_fact_count=len(result.events) - len(facts),
        derived_link_count=len(links),
        output_window_count=len(windows),
        missing_raw_output_count=sum(
            not authority_by_endpoint[endpoint].raw_outputs for endpoint in result_endpoints
        ),
        weak_observation_count=sum(
            isinstance(event, NormalizedObservationEvent) for event in result.events
        ),
        excluded_input_count=result.summary.excluded_input_count,
    )
    return facts, links, diagnostics


def _publication_content_payload(
    *,
    scope: CodexFactPublicationScope,
    source_authority: CodexSourceAuthority,
    normalization_content_sha256: str,
    windows: tuple[CodexLocatedOutputWindow, ...],
    facts: tuple[CodexDerivedFact, ...],
    links: tuple[CodexDerivedEventLink, ...],
    diagnostics: CodexFactPublicationDiagnostics,
) -> dict[str, object]:
    return {
        "contract_version": CODEX_DERIVED_FACT_CONTRACT_VERSION,
        "event_contract_version": CODEX_EVENT_CONTRACT_VERSION,
        "normalizer_version": CODEX_EVENT_NORMALIZER_VERSION,
        "state_machine_version": CODEX_EVENT_STATE_MACHINE_VERSION,
        "builder_version": CODEX_DERIVED_FACT_BUILDER_VERSION,
        "schema_version": CODEX_DERIVED_FACT_SCHEMA_VERSION,
        "scope": scope.model_dump(mode="json"),
        "source_set_sha256": source_authority.source_set_sha256,
        "source_authority": source_authority.model_dump(mode="json"),
        "normalization_content_sha256": normalization_content_sha256,
        "output_windows": [window.model_dump(mode="json") for window in windows],
        "facts": [fact.model_dump(mode="json") for fact in facts],
        "links": [link.model_dump(mode="json") for link in links],
        "diagnostics": diagnostics.model_dump(mode="json"),
    }


def build_codex_fact_publication_v1(
    scope: CodexFactPublicationScope | Mapping[str, Any],
    source_items: Iterable[ObservableCodexItem],
    *,
    expected_normalization: CodexEventNormalizationResult | Mapping[str, Any] | None = None,
) -> CodexFactPublication:
    """Re-run canonical CX1-01 over exact source authority and materialize it."""

    try:
        exact_scope = (
            scope
            if isinstance(scope, CodexFactPublicationScope)
            else CodexFactPublicationScope.model_validate(scope)
        )
    except Exception as exc:
        raise CodexFactScopeError("publication scope is not exact or complete") from exc
    canonical_sources = _canonical_source_items(source_items, exact_scope)
    normalizer_identity = _verify_bound_normalizer()
    source_authority = _build_source_authority(
        canonical_sources,
        normalizer_identity,
    )
    result = _run_bound_normalizer(canonical_sources)
    if expected_normalization is not None:
        expected = _coerce_normalization_result(expected_normalization)
        if expected != result:
            raise CodexFactValidationError(
                "caller normalization is not exact canonical source reconstruction"
            )
    windows, _, _ = _build_verified_output_windows(canonical_sources, result)
    _validate_scope_and_windows(
        exact_scope,
        result,
        windows,
        source_authority,
    )
    facts, links, diagnostics = _derive_facts_and_links(
        exact_scope,
        source_authority,
        result,
        windows,
    )
    content_hash = canonical_sha256(
        _publication_content_payload(
            scope=exact_scope,
            source_authority=source_authority,
            normalization_content_sha256=result.summary.content_sha256,
            windows=windows,
            facts=facts,
            links=links,
            diagnostics=diagnostics,
        )
    )
    publication_id = canonical_sha256(
        {
            "publication_type": "codex_derived_fact_generation",
            "content_sha256": content_hash,
        }
    )
    return CodexFactPublication(
        publication_id=publication_id,
        content_sha256=content_hash,
        normalization_content_sha256=result.summary.content_sha256,
        source_set_sha256=source_authority.source_set_sha256,
        scope=exact_scope,
        source_authority=source_authority,
        normalization=result,
        output_windows=windows,
        facts=facts,
        links=links,
        diagnostics=diagnostics,
    )


def _verify_fact_publication(publication: CodexFactPublication) -> None:
    normalizer_identity = _verify_bound_normalizer()
    if publication.source_authority.normalizer != normalizer_identity:
        raise CodexFactValidationError("publication normalizer authority is inconsistent")
    if publication.source_set_sha256 != publication.source_authority.source_set_sha256:
        raise CodexFactValidationError("publication source-set digest is inconsistent")
    for item in publication.source_authority.items:
        _require_event_scope(publication.scope, item.source_scope)
    _verify_normalization_result(publication.normalization)
    if publication.normalization_content_sha256 != publication.normalization.summary.content_sha256:
        raise CodexFactValidationError("publication normalization digest is inconsistent")
    _validate_scope_and_windows(
        publication.scope,
        publication.normalization,
        publication.output_windows,
        publication.source_authority,
    )
    facts, links, diagnostics = _derive_facts_and_links(
        publication.scope,
        publication.source_authority,
        publication.normalization,
        publication.output_windows,
    )
    if facts != publication.facts:
        raise CodexFactValidationError("publication facts are not exact CX1-01 materialization")
    if links != publication.links:
        raise CodexFactValidationError("publication links are not exact CX1-01 materialization")
    if diagnostics != publication.diagnostics:
        raise CodexFactValidationError("publication diagnostics are inconsistent")
    content_hash = canonical_sha256(
        _publication_content_payload(
            scope=publication.scope,
            source_authority=publication.source_authority,
            normalization_content_sha256=publication.normalization_content_sha256,
            windows=publication.output_windows,
            facts=publication.facts,
            links=publication.links,
            diagnostics=publication.diagnostics,
        )
    )
    if publication.content_sha256 != content_hash:
        raise CodexFactValidationError("publication content hash is inconsistent")
    publication_id = canonical_sha256(
        {
            "publication_type": "codex_derived_fact_generation",
            "content_sha256": content_hash,
        }
    )
    if publication.publication_id != publication_id:
        raise CodexFactValidationError("publication identity is inconsistent")


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS codex_derived_metadata (
    metadata_key TEXT PRIMARY KEY,
    metadata_value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS codex_derived_facts (
    fact_id TEXT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    source_set_sha256 TEXT NOT NULL,
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    fact_json TEXT NOT NULL CHECK(json_valid(fact_json))
);
CREATE TABLE IF NOT EXISTS codex_derived_publications (
    publication_id TEXT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    normalization_content_sha256 TEXT NOT NULL,
    source_set_sha256 TEXT NOT NULL,
    normalizer_source_sha256 TEXT NOT NULL,
    normalizer_code_sha256 TEXT NOT NULL,
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    builder_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    publication_json TEXT NOT NULL CHECK(json_valid(publication_json))
);
CREATE TABLE IF NOT EXISTS codex_event_links (
    publication_id TEXT NOT NULL,
    link_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    predicate TEXT NOT NULL CHECK(predicate IN ('call_result', 'contains', 'derived_from')),
    source_endpoint_id TEXT NOT NULL,
    target_endpoint_id TEXT NOT NULL,
    link_json TEXT NOT NULL CHECK(json_valid(link_json)),
    PRIMARY KEY(publication_id, link_id),
    FOREIGN KEY(publication_id)
        REFERENCES codex_derived_publications(publication_id)
        ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS codex_active_generations (
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    PRIMARY KEY(
        project_id, repository_id, thread_id, source_id, source_version, acl_ref
    ),
    FOREIGN KEY(publication_id)
        REFERENCES codex_derived_publications(publication_id)
        ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS codex_derived_staging (
    publication_id TEXT PRIMARY KEY,
    staged_json TEXT NOT NULL CHECK(json_valid(staged_json))
);
CREATE INDEX IF NOT EXISTS idx_codex_derived_publications_exact
    ON codex_derived_publications(
        project_id, repository_id, thread_id, generation_id,
        source_id, source_version, acl_ref, publication_id
    );
CREATE INDEX IF NOT EXISTS idx_codex_event_links_scope
    ON codex_event_links(
        project_id, repository_id, thread_id, generation_id,
        source_id, source_version, acl_ref, predicate
    );
"""


class _SQLiteConnection:
    def __init__(self, connection: sqlite3.Connection, *, close: bool) -> None:
        self.connection = connection
        self.close_connection = close

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, *_args: object) -> None:
        if self.close_connection:
            self.connection.close()


class SQLiteCodexFactStore:
    """Additive temporary-only SQLite store with atomic generation activation."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        raw_path = str(database_path)
        self._memory = raw_path == ":memory:"
        if self._memory:
            self.database_path = raw_path
        else:
            resolved = Path(database_path).expanduser().resolve()
            temp_root = Path(tempfile.gettempdir()).resolve()
            try:
                resolved.relative_to(temp_root)
            except ValueError as exc:
                raise ValueError(
                    "CX1-02 SQLite store only accepts temporary paths or :memory:"
                ) from exc
            resolved.parent.mkdir(parents=True, exist_ok=True)
            self.database_path = str(resolved)
        self.failpoint = failpoint
        self._lock = threading.RLock()
        self._memory_db: sqlite3.Connection | None = None
        if self._memory:
            self._memory_db = self._new_connection()
        with self._connection() as database:
            database.executescript(_SQLITE_SCHEMA)
            database.execute(
                """INSERT OR IGNORE INTO codex_derived_metadata
                   (metadata_key, metadata_value) VALUES ('schema_version', ?)""",
                (CODEX_DERIVED_FACT_SCHEMA_VERSION,),
            )
            row = database.execute(
                """SELECT metadata_value FROM codex_derived_metadata
                   WHERE metadata_key='schema_version'"""
            ).fetchone()
            if row is None or row["metadata_value"] != CODEX_DERIVED_FACT_SCHEMA_VERSION:
                raise CodexFactPublicationError("incompatible Codex derived schema metadata")

    def _new_connection(self) -> sqlite3.Connection:
        database = sqlite3.connect(
            self.database_path,
            isolation_level=None,
            timeout=5.0,
        )
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA busy_timeout=5000")
        return database

    def _connection(self) -> _SQLiteConnection:
        if self._memory_db is not None:
            return _SQLiteConnection(self._memory_db, close=False)
        return _SQLiteConnection(self._new_connection(), close=True)

    def close(self) -> None:
        with self._lock:
            if self._memory_db is not None:
                self._memory_db.close()
                self._memory_db = None

    def __enter__(self) -> SQLiteCodexFactStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def publish(
        self,
        publication: CodexFactPublication | Mapping[str, Any],
        *,
        source_items: Iterable[ObservableCodexItem],
    ) -> CodexFactPublicationResult:
        """Stage, validate, publish, and activate one exact generation atomically."""

        exact_sources = tuple(source_items)
        try:
            exact = (
                publication
                if type(publication) is CodexFactPublication
                else CodexFactPublication.model_validate(publication)
            )
            exact = CodexFactPublication.model_validate(
                exact.model_dump(mode="python", round_trip=True)
            )
        except Exception as exc:
            raise CodexFactValidationError("publication failed frozen-contract validation") from exc
        rebuilt = build_codex_fact_publication_v1(
            exact.scope,
            exact_sources,
            expected_normalization=exact.normalization,
        )
        if rebuilt != exact:
            raise CodexFactValidationError(
                "publication is not exact authoritative source reconstruction"
            )
        if not exact.facts:
            raise CodexFactValidationError(
                "empty derived generation cannot replace an active generation"
            )
        payload = _json_text(exact.model_dump(mode="json"))

        with self._lock, self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                existing = database.execute(
                    """SELECT publication_json FROM codex_derived_publications
                       WHERE publication_id=?""",
                    (exact.publication_id,),
                ).fetchone()
                if existing is not None:
                    if existing["publication_json"] != payload:
                        raise CodexFactPublicationError(
                            "publication identity collision or non-idempotent replay"
                        )
                    active = self._active_row(database, exact.scope)
                    database.execute("COMMIT")
                    if active is None:
                        raise CodexFactPublicationError(
                            "stored publication has no active-generation record"
                        )
                    return CodexFactPublicationResult(
                        publication=exact,
                        disposition=CodexFactPublicationDisposition.REPLAYED,
                        previous_active_generation_id=active["generation_id"],
                        active_generation_id=active["generation_id"],
                        active_publication_id=active["publication_id"],
                    )

                active_before = self._active_row(database, exact.scope)
                database.execute(
                    """INSERT INTO codex_derived_staging(publication_id, staged_json)
                       VALUES (?, ?)""",
                    (exact.publication_id, payload),
                )
                self._call_failpoint("after_stage")
                staged = database.execute(
                    """SELECT staged_json FROM codex_derived_staging
                       WHERE publication_id=?""",
                    (exact.publication_id,),
                ).fetchone()
                if staged is None:
                    raise CodexFactPublicationError("staged publication disappeared")
                validated = CodexFactPublication.model_validate(json.loads(staged["staged_json"]))
                if validated != exact:
                    raise CodexFactPublicationError("staged publication changed during validation")
                self._call_failpoint("after_validate")

                for fact in exact.facts:
                    fact_payload = _json_text(fact.model_dump(mode="json"))
                    database.execute(
                        """INSERT OR IGNORE INTO codex_derived_facts
                           (fact_id, content_sha256, source_set_sha256, project_id,
                            repository_id, thread_id, generation_id, source_id,
                            source_version, acl_ref, role, kind, fact_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            fact.fact_id,
                            fact.content_sha256,
                            fact.source_set_sha256,
                            fact.scope.project_id,
                            fact.scope.repository_id,
                            fact.scope.thread_id,
                            fact.scope.generation_id,
                            fact.scope.source_id,
                            fact.scope.source_version,
                            fact.scope.acl_ref,
                            fact.role.value,
                            fact.kind.value,
                            fact_payload,
                        ),
                    )
                    stored = database.execute(
                        "SELECT fact_json FROM codex_derived_facts WHERE fact_id=?",
                        (fact.fact_id,),
                    ).fetchone()
                    if stored is None or stored["fact_json"] != fact_payload:
                        raise CodexFactPublicationError(
                            "derived fact identity collision or scope mismatch"
                        )
                self._call_failpoint("after_facts")

                scope_values = _publication_scope_key(exact.scope)
                database.execute(
                    """INSERT INTO codex_derived_publications
                       (publication_id, content_sha256, normalization_content_sha256,
                        source_set_sha256, normalizer_source_sha256,
                        normalizer_code_sha256, project_id, repository_id, thread_id,
                        generation_id, source_id, source_version, acl_ref,
                        contract_version, builder_version, schema_version,
                        publication_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        exact.publication_id,
                        exact.content_sha256,
                        exact.normalization_content_sha256,
                        exact.source_set_sha256,
                        exact.source_authority.normalizer.source_sha256,
                        exact.source_authority.normalizer.code_sha256,
                        *scope_values,
                        exact.contract_version,
                        exact.builder_version,
                        exact.schema_version,
                        payload,
                    ),
                )
                for link in exact.links:
                    database.execute(
                        """INSERT INTO codex_event_links
                           (publication_id, link_id, project_id, repository_id, thread_id,
                            generation_id, source_id, source_version, acl_ref, predicate,
                            source_endpoint_id, target_endpoint_id, link_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            exact.publication_id,
                            link.link_id,
                            *scope_values,
                            link.predicate.value,
                            link.source.endpoint_id,
                            link.target.endpoint_id,
                            _json_text(link.model_dump(mode="json")),
                        ),
                    )
                self._call_failpoint("before_activate")

                active_values = _active_scope_key(exact.scope)
                database.execute(
                    """INSERT INTO codex_active_generations
                       (project_id, repository_id, thread_id, source_id, source_version,
                        acl_ref, generation_id, publication_id, content_sha256)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(
                           project_id, repository_id, thread_id, source_id,
                           source_version, acl_ref
                       ) DO UPDATE SET
                           generation_id=excluded.generation_id,
                           publication_id=excluded.publication_id,
                           content_sha256=excluded.content_sha256""",
                    (
                        *active_values,
                        exact.scope.generation_id,
                        exact.publication_id,
                        exact.content_sha256,
                    ),
                )
                self._call_failpoint("after_activate")
                database.execute(
                    "DELETE FROM codex_derived_staging WHERE publication_id=?",
                    (exact.publication_id,),
                )
                self._call_failpoint("before_commit")
                database.execute("COMMIT")
            except Exception as exc:
                database.execute("ROLLBACK")
                if isinstance(exc, CodexFactError):
                    raise
                raise CodexFactPublicationError("atomic Codex derived publication failed") from exc

        previous = str(active_before["generation_id"]) if active_before is not None else None
        return CodexFactPublicationResult(
            publication=exact,
            disposition=CodexFactPublicationDisposition.PUBLISHED,
            previous_active_generation_id=previous,
            active_generation_id=exact.scope.generation_id,
            active_publication_id=exact.publication_id,
        )

    def _call_failpoint(self, stage: str) -> None:
        if self.failpoint is not None:
            self.failpoint(stage)

    @staticmethod
    def _active_row(
        database: sqlite3.Connection,
        scope: CodexFactPublicationScope,
    ) -> sqlite3.Row | None:
        return database.execute(
            """SELECT generation_id, publication_id, content_sha256
               FROM codex_active_generations
               WHERE project_id=? AND repository_id=? AND thread_id=?
                 AND source_id=? AND source_version=? AND acl_ref=?""",
            _active_scope_key(scope),
        ).fetchone()

    def get_publication(
        self,
        scope: CodexFactPublicationScope | Mapping[str, Any],
        *,
        publication_id: str,
    ) -> CodexFactPublication | None:
        exact_scope = (
            scope
            if isinstance(scope, CodexFactPublicationScope)
            else CodexFactPublicationScope.model_validate(scope)
        )
        with self._lock, self._connection() as database:
            row = database.execute(
                """SELECT publication_json FROM codex_derived_publications
                   WHERE publication_id=? AND project_id=? AND repository_id=?
                     AND thread_id=? AND generation_id=? AND source_id=?
                     AND source_version=? AND acl_ref=?""",
                (publication_id, *_publication_scope_key(exact_scope)),
            ).fetchone()
        if row is None:
            return None
        return CodexFactPublication.model_validate(json.loads(row["publication_json"]))

    def get_active(
        self,
        scope: CodexFactPublicationScope | Mapping[str, Any],
    ) -> CodexFactPublication | None:
        """Read only the active publication for the exact non-generation scope."""

        exact_scope = (
            scope
            if isinstance(scope, CodexFactPublicationScope)
            else CodexFactPublicationScope.model_validate(scope)
        )
        with self._lock, self._connection() as database:
            active = self._active_row(database, exact_scope)
            if active is None or active["generation_id"] != exact_scope.generation_id:
                return None
            row = database.execute(
                """SELECT publication_json FROM codex_derived_publications
                   WHERE publication_id=?""",
                (active["publication_id"],),
            ).fetchone()
        if row is None:
            raise CodexFactPublicationError("active publication endpoint is missing")
        return CodexFactPublication.model_validate(json.loads(row["publication_json"]))

    def active_generation(
        self,
        scope: CodexFactPublicationScope | Mapping[str, Any],
    ) -> str | None:
        exact_scope = (
            scope
            if isinstance(scope, CodexFactPublicationScope)
            else CodexFactPublicationScope.model_validate(scope)
        )
        with self._lock, self._connection() as database:
            active = self._active_row(database, exact_scope)
        return str(active["generation_id"]) if active is not None else None

    def publication_count(self) -> int:
        return self._count("codex_derived_publications")

    def fact_count(self) -> int:
        return self._count("codex_derived_facts")

    def link_count(self) -> int:
        return self._count("codex_event_links")

    def staged_count(self) -> int:
        return self._count("codex_derived_staging")

    def _count(self, table: str) -> int:
        allowed = {
            "codex_derived_publications",
            "codex_derived_facts",
            "codex_event_links",
            "codex_derived_staging",
        }
        if table not in allowed:
            raise ValueError("unknown Codex derived table")
        with self._lock, self._connection() as database:
            return int(database.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _constant_code_payload(value: object) -> object:
    return portable_constant_payload(value)


def _code_object_payload(code: types.CodeType) -> dict[str, object]:
    """Return a checkout-path-independent representation of a function body."""

    return portable_code_payload(code)


def _function_code_digest(value: types.FunctionType) -> str:
    try:
        return canonical_sha256(portable_function_payload(value))
    except CodeIdentityError as exc:
        raise CodexFactValidationError(str(exc)) from exc


__all__ = [
    "CODEX_DERIVED_FACT_BUILDER_VERSION",
    "CODEX_DERIVED_FACT_CONTRACT_VERSION",
    "CODEX_DERIVED_FACT_SCHEMA_VERSION",
    "CODEX_NORMALIZER_CODE_SHA256",
    "CODEX_NORMALIZER_EXPORT",
    "CODEX_NORMALIZER_MODULE",
    "CODEX_NORMALIZER_SOURCE_SHA256",
    "CODEX_OUTPUT_WINDOW_CONTRACT_VERSION",
    "CODEX_PUBLICATION_OUTPUT_WINDOW_MAX_CHARS",
    "CODEX_SOURCE_AUTHORITY_VERSION",
    "CodexDerivedEventLink",
    "CodexDerivedFact",
    "CodexEndpointKind",
    "CodexEventEndpoint",
    "CodexEventPredicate",
    "CodexFactError",
    "CodexFactPublication",
    "CodexFactPublicationDiagnostics",
    "CodexFactPublicationDisposition",
    "CodexFactPublicationError",
    "CodexFactPublicationResult",
    "CodexFactPublicationScope",
    "CodexFactScopeError",
    "CodexFactValidationError",
    "CodexLocatedOutputWindow",
    "CodexNormalizerIdentity",
    "CodexOutputRole",
    "CodexOutputWindow",
    "CodexRawOutputAuthority",
    "CodexSourceAuthority",
    "CodexSourceItemAuthority",
    "SQLiteCodexFactStore",
    "build_codex_fact_publication_v1",
    "build_codex_output_window_v1",
    "build_output_window_v1",
]
