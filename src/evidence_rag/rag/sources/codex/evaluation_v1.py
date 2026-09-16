"""Released Golden-v1 contracts and offline evaluator for the Codex source.

This module is intentionally independent of the evaluation service and every
mutable store.  It loads one content-addressed, reviewed fixture package and
evaluates already-reviewed retrieval rows.  It does not run a retriever,
create an evaluation run, or qualify a baseline.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self
from urllib.parse import quote

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

CODEX_GOLDEN_DATASET_ID = "codex-golden-v1"
CODEX_GOLDEN_DATASET_VERSION = "v1"
CODEX_GOLDEN_SCHEMA_VERSION = "codex-evaluation-foundation-v1"
CODEX_GOLDEN_CASE_COUNT = 45
CODEX_GOLDEN_AUTHORITY_HASH = (
    "sha256:b5fc258c94375480b8d76cdc2e5dda6fd86ca49ddc2a0048b5879da2b23d1fe5"
)
CODEX_GOLDEN_PACKAGE_HASH = (
    "sha256:aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1"
)
CODEX_GOLDEN_MANIFEST = "manifest.json"
PROJECT_ROOT_TOKEN = "${PROJECT_ROOT}"

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
_THREAD_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_LOCATOR_RE = re.compile(r"^(codex://thread/[^#]+/item/[^#]+)#event=([1-9][0-9]*)$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ABSOLUTE_PATH_RE = re.compile(
    r'(?:"(?:cwd|project_path|repository_path)"\s*:\s*")'
    r"(?:/|[A-Za-z]:[\\/])|/(?:Users|home|private|var|tmp)/"
)
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])")
_PAYMENT_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_CREDENTIAL_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s\"']{8,}))"
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _validate_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value or value != value.strip() or _CONTROL_RE.search(value):
        raise ValueError("text must be NFC, trimmed, and free of control characters")
    return value


def _validate_identifier(value: str) -> str:
    value = _validate_text(value)
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier is not canonical")
    return value


def _validate_thread_id(value: str) -> str:
    value = _validate_text(value)
    if not _THREAD_RE.fullmatch(value):
        raise ValueError("thread_id is not canonical")
    return value


def _safe_relative_path(value: str) -> str:
    value = _validate_text(value)
    if "\\" in value:
        raise ValueError("paths must use POSIX separators")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != value
    ):
        raise ValueError("path must be canonical and repository relative")
    if value.endswith(("-wal", "-shm", ".pyc")) or "__pycache__" in path.parts:
        raise ValueError("mutable or compiled artifacts are forbidden")
    return value


def _validate_sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("expected a sha256:<lowercase-hex> identity")
    return value


Identifier = Annotated[StrictStr, AfterValidator(_validate_identifier)]
ThreadId = Annotated[StrictStr, AfterValidator(_validate_thread_id)]
SafeRelativePath = Annotated[StrictStr, AfterValidator(_safe_relative_path)]
Sha256 = Annotated[StrictStr, AfterValidator(_validate_sha256)]
ContractText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=8_000),
    AfterValidator(_validate_text),
]
PositiveInt = Annotated[StrictInt, Field(ge=1)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class CodexEvaluationError(ValueError):
    """Fail-closed Golden package or reviewed-row error."""


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
        return _sha256(self.canonical_json_bytes())

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


class CodexGoldenSlice(StrEnum):
    """The eight exhaustive, released Golden-v1 case slices."""

    THREAD_GOAL_LOCATION = "thread/goal location"
    PROCESS_TRACE = "process trace"
    RATIONALE_DECISION = "rationale/decision"
    PATCH_FILE_CHANGE = "patch/file change"
    COMMAND_VALIDATION = "command/validation"
    FAILURE_RETRY = "failure/retry"
    MULTI_THREAD_COMPARISON = "multi-thread comparison"
    UNRESOLVED_UNANSWERABLE_PRIVACY = "unresolved/unanswerable/privacy"


EXPECTED_SLICE_COUNTS: Mapping[CodexGoldenSlice, int] = {
    CodexGoldenSlice.THREAD_GOAL_LOCATION: 6,
    CodexGoldenSlice.PROCESS_TRACE: 6,
    CodexGoldenSlice.RATIONALE_DECISION: 6,
    CodexGoldenSlice.PATCH_FILE_CHANGE: 6,
    CodexGoldenSlice.COMMAND_VALIDATION: 7,
    CodexGoldenSlice.FAILURE_RETRY: 5,
    CodexGoldenSlice.MULTI_THREAD_COMPARISON: 4,
    CodexGoldenSlice.UNRESOLVED_UNANSWERABLE_PRIVACY: 5,
}


class MetricStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVISIONAL = "PROVISIONAL"
    UNAVAILABLE = "UNAVAILABLE"


class CodexMetric(StrEnum):
    THREAD_RECALL_AT_5 = "thread_recall_at_5"
    EPISODE_RECALL_AT_5 = "episode_recall_at_5"
    ITEM_RECALL_AT_10 = "item_recall_at_10"
    MRR_AT_10 = "mrr_at_10"
    GOAL_RECALL_AT_10 = "goal_recall_at_10"
    DECISION_RECALL_AT_10 = "decision_recall_at_10"
    HARMFUL_OLD_ATTEMPT_RATE_AT_10 = "harmful_old_attempt_rate_at_10"
    EVENT_ORDER_ACCURACY_AT_10 = "event_order_accuracy_at_10"
    CALL_RESULT_LINK_ACCURACY_AT_10 = "call_result_link_accuracy_at_10"
    PATCH_ACCURACY_AT_10 = "patch_accuracy_at_10"
    VALIDATION_ACCURACY_AT_10 = "validation_accuracy_at_10"
    FALSE_VALIDATED_RATE_AT_10 = "false_validated_rate_at_10"
    OUTCOME_ACCURACY_AT_10 = "outcome_accuracy_at_10"
    CONTEXT_DUPLICATE_RATE_AT_10 = "context_duplicate_rate_at_10"
    CONTEXT_NOISE_RATE_AT_10 = "context_noise_rate_at_10"


class HardNegativeRoute(StrEnum):
    FAILED_ATTEMPT = "failed_attempt"
    PLAN_ONLY = "plan_only"
    ASSISTANT_CLAIM_WITHOUT_EXIT = "assistant_claim_without_exit"
    PATCH_FAILURE = "patch_failure"
    READ_ONLY_NOT_CHANGE = "read_only_not_change"
    SAME_COMMAND_MULTIPLE_STATUSES = "same_command_multiple_statuses"
    TRUNCATED_TOOL_RESULT = "truncated_tool_result"
    ARCHIVED_DUPLICATE = "archived_duplicate"
    SUBAGENT_SAME_TOPIC = "subagent_same_topic"
    PRIVACY_SENSITIVE = "privacy_sensitive"


EXPECTED_NEGATIVE_ROUTES = frozenset(HardNegativeRoute)


class FalseValidationCategory(StrEnum):
    CLAIM_ONLY = "claim_only"
    MENTIONED_ONLY = "mentioned_only"
    INVOKED_NO_EXIT = "invoked_no_exit"
    FAILED_EXIT = "failed_exit"
    TARGET_UNKNOWN = "target_unknown"


EXPECTED_FALSE_VALIDATION_CATEGORIES = frozenset(FalseValidationCategory)


class EvidenceProvenance(_FrozenContract):
    source_file: SafeRelativePath
    line_start: PositiveInt
    line_end: PositiveInt
    thread_id: ThreadId | None = None
    item_id: ContractText | None = None

    @model_validator(mode="after")
    def _ordered_lines(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        if self.item_id is not None:
            if self.thread_id is None:
                raise ValueError("item provenance requires thread_id")
            prefix = f"codex://thread/{quote(self.thread_id, safe='-._')}/"
            if not self.item_id.startswith(prefix):
                raise ValueError("provenance item is outside its declared thread")
        return self


class ExpectedThread(_FrozenContract):
    thread_id: ThreadId
    locator: ContractText
    source_file: SafeRelativePath
    status: Literal["completed", "failed", "in_progress"]
    episode_id: ContractText | None = None

    @model_validator(mode="after")
    def _canonical_locator(self) -> Self:
        expected = f"codex://thread/{quote(self.thread_id, safe='-._')}"
        if self.locator != expected:
            raise ValueError("thread locator does not match thread_id")
        if self.episode_id is not None and not self.episode_id.startswith(expected + "/turn/"):
            raise ValueError("episode_id is outside the expected thread")
        return self


class ExpectedItem(_FrozenContract):
    thread_id: ThreadId
    turn_id: Identifier
    item_id: ContractText
    locator: ContractText
    item_type: Literal[
        "UserGoal",
        "AgentMessage",
        "CommandExecution",
        "ToolResult",
        "ToolCall",
        "Patch",
        "FileChange",
        "Plan",
        "ValidationResult",
        "DevelopmentEpisode",
    ]
    source_file: SafeRelativePath
    line: PositiveInt
    repository_path: SafeRelativePath | None = None

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        prefix = (
            f"codex://thread/{quote(self.thread_id, safe='-._')}"
            f"/turn/{quote(self.turn_id, safe='-._')}/"
        )
        if not self.item_id.startswith(prefix):
            raise ValueError("item_id is outside its declared thread/turn")
        if self.item_type == "DevelopmentEpisode":
            if self.locator != self.item_id or "/episode/" not in self.item_id:
                raise ValueError("episode locator must equal its item identity")
        else:
            match = _LOCATOR_RE.fullmatch(self.locator)
            if match is None or match.group(1) != self.item_id or int(match.group(2)) != self.line:
                raise ValueError("item locator, item identity, and line disagree")
        return self


class EventTruth(_FrozenContract):
    truth_id: Identifier
    thread_id: ThreadId
    item_id: ContractText
    kind: Literal[
        "goal",
        "plan",
        "tool_call",
        "tool_result",
        "patch",
        "file_change",
        "command",
        "validation",
        "decision",
        "outcome",
    ]
    order: PositiveInt | None = None
    call_id: Identifier | None = None
    status: Literal["completed", "failed", "passed", "reported", "unknown"] | None = None
    exit_code: StrictInt | None = None
    repository_path: SafeRelativePath | None = None
    patch_applied: StrictBool | None = None
    redacted: StrictBool = False
    truncated: StrictBool = False
    warning: ContractText | None = None

    @model_validator(mode="after")
    def _warning_for_incomplete_truth(self) -> Self:
        prefix = f"codex://thread/{quote(self.thread_id, safe='-._')}/"
        if not self.item_id.startswith(prefix):
            raise ValueError("event truth item is outside its declared thread")
        if (self.redacted or self.truncated) and self.warning is None:
            raise ValueError("redacted/truncated truth requires an explicit warning")
        if self.kind == "validation" and self.exit_code is None:
            raise ValueError("validation truth requires an observed exit_code")
        return self


class ItemJudgment(_FrozenContract):
    judgment_id: Identifier
    thread_id: ThreadId
    item_id: ContractText
    locator: ContractText
    relevance: Literal["positive", "supporting", "hard_negative", "neutral"]
    state_label: Identifier
    temporal_label: Identifier
    context_noise: StrictBool
    episode_id: ContractText | None = None
    call_id: Identifier | None = None
    patch_applied: StrictBool | None = None
    validation_status: Literal["passed", "failed"] | None = None
    false_validation_category: FalseValidationCategory | None = None
    false_validated: StrictBool | None = None
    outcome_correct: StrictBool | None = None

    @model_validator(mode="after")
    def _authority_is_complete(self) -> Self:
        prefix = f"codex://thread/{quote(self.thread_id, safe='-._')}/"
        if not self.item_id.startswith(prefix):
            raise ValueError("item judgment is outside its declared thread")
        if self.locator != self.item_id and not self.locator.startswith(self.item_id + "#event="):
            raise ValueError("item judgment locator does not match item")
        if self.episode_id is not None and not self.episode_id.startswith(prefix):
            raise ValueError("judgment episode is outside its declared thread")
        if (self.false_validation_category is None) != (self.false_validated is None):
            raise ValueError("false-validation category and judgment must be present together")
        if self.relevance == "hard_negative" and not self.context_noise:
            raise ValueError("hard-negative judgment must be context noise")
        return self


class HardNegative(_FrozenContract):
    thread_id: ThreadId
    item_id: ContractText
    locator: ContractText
    reason: HardNegativeRoute
    old_attempt: StrictBool = False

    @model_validator(mode="after")
    def _same_identity(self) -> Self:
        thread_prefix = f"codex://thread/{quote(self.thread_id, safe='-._')}/"
        if not self.item_id.startswith(thread_prefix):
            raise ValueError("hard-negative item is outside its declared thread")
        if self.locator != self.item_id and not self.locator.startswith(self.item_id + "#event="):
            raise ValueError("hard-negative locator does not match item_id")
        return self


class CodexGoldenCase(_FrozenContract):
    case_id: Identifier
    slice: CodexGoldenSlice
    query: ContractText
    task: Literal[
        "thread_location",
        "process_trace",
        "rationale_decision",
        "patch_change",
        "command_validation",
        "failure_retry",
        "multi_thread_comparison",
        "unanswerable_privacy",
    ]
    expected_threads: tuple[ExpectedThread, ...] = ()
    expected_items: tuple[ExpectedItem, ...] = ()
    event_truth: tuple[EventTruth, ...] = ()
    hard_negatives: tuple[HardNegative, ...]
    item_judgments: tuple[ItemJudgment, ...]
    eligible_metrics: tuple[CodexMetric, ...]
    state_labels: tuple[Identifier, ...]
    temporal_labels: tuple[Identifier, ...]
    evidence_provenance: tuple[EvidenceProvenance, ...]
    unanswerable: StrictBool = False
    refusal_expected: StrictBool = False
    answer_reason: ContractText | None = None

    @field_validator(
        "expected_threads",
        "expected_items",
        "event_truth",
        "hard_negatives",
        "item_judgments",
        "eligible_metrics",
        "state_labels",
        "temporal_labels",
        "evidence_provenance",
    )
    @classmethod
    def _not_mutable_lists(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(value)

    @model_validator(mode="after")
    def _coherent_truth(self) -> Self:
        if len(self.hard_negatives) == 0:
            raise ValueError("every case requires at least one hard negative")
        if len(self.item_judgments) == 0:
            raise ValueError("every case requires released item judgments")
        if len(self.eligible_metrics) == 0:
            raise ValueError("every case requires released metric eligibility")
        if len(self.state_labels) == 0 or len(self.temporal_labels) == 0:
            raise ValueError("state and temporal labels are required")
        if self.unanswerable:
            if self.expected_threads or self.expected_items or self.event_truth:
                raise ValueError("unanswerable cases cannot carry positive truth")
            if not self.refusal_expected or self.answer_reason is None:
                raise ValueError("unanswerable cases require an explicit refusal reason")
        else:
            if self.refusal_expected:
                raise ValueError("answerable case cannot expect refusal")
            if not self.expected_threads or not self.expected_items:
                raise ValueError("answerable cases require thread and item truth")
            if not self.evidence_provenance:
                raise ValueError("answerable cases require evidence provenance")

        thread_ids = {item.thread_id for item in self.expected_threads}
        if any(item.thread_id not in thread_ids for item in self.expected_items):
            raise ValueError("positive item targets a cross-thread identity")
        positive_ids = [item.item_id for item in self.expected_items]
        if len(positive_ids) != len(set(positive_ids)):
            raise ValueError("duplicate positive item in one case")
        truth_ids = [item.truth_id for item in self.event_truth]
        if len(truth_ids) != len(set(truth_ids)):
            raise ValueError("duplicate event truth id")
        hard_ids = [item.item_id for item in self.hard_negatives]
        if len(hard_ids) != len(set(hard_ids)):
            raise ValueError("duplicate hard-negative item in one case")
        if set(positive_ids) & set(hard_ids):
            raise ValueError("hard-negative and positive truth overlap")
        judgment_ids = [item.judgment_id for item in self.item_judgments]
        judgment_item_ids = [item.item_id for item in self.item_judgments]
        if len(judgment_ids) != len(set(judgment_ids)):
            raise ValueError("duplicate item judgment id")
        if len(judgment_item_ids) != len(set(judgment_item_ids)):
            raise ValueError("duplicate judged item in one case")
        if len(self.eligible_metrics) != len(set(self.eligible_metrics)):
            raise ValueError("duplicate eligible metric")
        judgments = {item.item_id: item for item in self.item_judgments}
        if any(item_id not in judgments for item_id in positive_ids):
            raise ValueError("positive item lacks released judgment authority")
        if any(item_id not in judgments for item_id in hard_ids):
            raise ValueError("hard negative lacks released judgment authority")
        if any(item.item_id not in judgments for item in self.event_truth):
            raise ValueError("event truth lacks released judgment authority")
        if any(judgments[item_id].relevance != "positive" for item_id in positive_ids):
            raise ValueError("positive item judgment has contradictory relevance")
        if any(judgments[item_id].relevance != "hard_negative" for item_id in hard_ids):
            raise ValueError("hard-negative judgment has contradictory relevance")
        if any(item.kind == "reasoning" for item in self.event_truth):
            raise ValueError("reasoning events are never eligible")
        return self


def _expected_eligible_metrics(case: CodexGoldenCase) -> tuple[CodexMetric, ...]:
    eligible = {
        CodexMetric.CONTEXT_DUPLICATE_RATE_AT_10,
        CodexMetric.CONTEXT_NOISE_RATE_AT_10,
    }
    if not case.unanswerable:
        eligible.update(
            {
                CodexMetric.THREAD_RECALL_AT_5,
                CodexMetric.ITEM_RECALL_AT_10,
                CodexMetric.MRR_AT_10,
            }
        )
        if any(item.episode_id is not None for item in case.expected_threads):
            eligible.add(CodexMetric.EPISODE_RECALL_AT_5)
        if any(item.item_type == "UserGoal" for item in case.expected_items):
            eligible.add(CodexMetric.GOAL_RECALL_AT_10)
        if case.slice == CodexGoldenSlice.RATIONALE_DECISION or any(
            item.kind == "decision" for item in case.event_truth
        ):
            eligible.add(CodexMetric.DECISION_RECALL_AT_10)
    if any(item.old_attempt for item in case.hard_negatives):
        eligible.add(CodexMetric.HARMFUL_OLD_ATTEMPT_RATE_AT_10)
    if sum(item.order is not None for item in case.event_truth) >= 2:
        eligible.add(CodexMetric.EVENT_ORDER_ACCURACY_AT_10)
    calls: dict[str, set[str]] = defaultdict(set)
    for truth in case.event_truth:
        if truth.call_id:
            calls[truth.call_id].add(truth.kind)
    if any({"tool_call", "tool_result"} <= kinds for kinds in calls.values()):
        eligible.add(CodexMetric.CALL_RESULT_LINK_ACCURACY_AT_10)
    if any(
        item.kind in {"patch", "file_change"} and item.patch_applied is not None
        for item in case.event_truth
    ):
        eligible.add(CodexMetric.PATCH_ACCURACY_AT_10)
    if any(item.kind == "validation" for item in case.event_truth):
        eligible.add(CodexMetric.VALIDATION_ACCURACY_AT_10)
    if any(item.false_validation_category is not None for item in case.item_judgments):
        eligible.add(CodexMetric.FALSE_VALIDATED_RATE_AT_10)
    if any(item.kind == "outcome" for item in case.event_truth):
        eligible.add(CodexMetric.OUTCOME_ACCURACY_AT_10)
    return tuple(metric for metric in CodexMetric if metric in eligible)


class _FixtureFile(_FrozenContract):
    path: SafeRelativePath
    kind: Literal["golden", "session", "session_index", "program"]
    sha256: Sha256
    size: NonNegativeInt
    case_ids: tuple[Identifier, ...] = ()
    thread_ids: tuple[ThreadId, ...] = ()
    item_ids: tuple[ContractText, ...] = ()

    @model_validator(mode="after")
    def _memberships_are_unique(self) -> Self:
        for name in ("case_ids", "thread_ids", "item_ids"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {name} membership")
        if self.kind == "session":
            if len(self.thread_ids) != 1:
                raise ValueError("each immutable session fixture contains exactly one thread")
            prefix = f"codex://thread/{quote(self.thread_ids[0], safe='-._')}/"
            if any(not item_id.startswith(prefix) for item_id in self.item_ids):
                raise ValueError("session item membership crosses thread identity")
        elif self.thread_ids or self.item_ids:
            raise ValueError("only session fixtures may declare thread/item memberships")
        return self


class _ReleasedManifest(_FrozenContract):
    schema_version: Literal["codex-evaluation-foundation-v1"]
    dataset_id: Literal["codex-golden-v1"]
    dataset_version: Literal["v1"]
    released: Literal[True]
    package_hash: Sha256
    case_count: Literal[45]
    slice_counts: dict[CodexGoldenSlice, PositiveInt]
    negative_route_counts: dict[HardNegativeRoute, PositiveInt]
    eligible_metric_counts: dict[CodexMetric, PositiveInt]
    false_validation_category_counts: dict[FalseValidationCategory, PositiveInt]
    item_judgment_count: PositiveInt
    case_ids: tuple[Identifier, ...]
    files: tuple[_FixtureFile, ...]

    @model_validator(mode="after")
    def _fixed_membership(self) -> Self:
        if self.slice_counts != dict(EXPECTED_SLICE_COUNTS):
            raise ValueError("manifest slice counts differ from released Golden-v1")
        if (
            set(self.negative_route_counts) != EXPECTED_NEGATIVE_ROUTES
            or sum(self.negative_route_counts.values()) != CODEX_GOLDEN_CASE_COUNT
        ):
            raise ValueError("manifest hard-negative route membership is incomplete")
        if set(
            self.false_validation_category_counts
        ) != EXPECTED_FALSE_VALIDATION_CATEGORIES or any(
            count != 1 for count in self.false_validation_category_counts.values()
        ):
            raise ValueError("manifest false-validation membership is incomplete")
        if set(self.eligible_metric_counts) != set(CodexMetric):
            raise ValueError("manifest metric eligibility membership is incomplete")
        if len(self.case_ids) != CODEX_GOLDEN_CASE_COUNT or len(set(self.case_ids)) != len(
            self.case_ids
        ):
            raise ValueError("manifest must contain 45 unique case IDs")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("manifest contains duplicate file paths")
        return self


class GoldenDataset(_FrozenContract):
    dataset_id: Literal["codex-golden-v1"]
    dataset_version: Literal["v1"]
    schema_version: Literal["codex-evaluation-foundation-v1"]
    package_hash: Sha256
    authority_hash: Sha256
    cases: tuple[CodexGoldenCase, ...]
    fixture_thread_ids: tuple[ThreadId, ...]
    fixture_item_ids: tuple[ContractText, ...]
    fixture_item_locators: tuple[ContractText, ...]

    @model_validator(mode="after")
    def _released_shape(self) -> Self:
        if len(self.cases) != CODEX_GOLDEN_CASE_COUNT:
            raise ValueError("Golden-v1 has exactly 45 cases")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("duplicate Golden case_id")
        counts = Counter(case.slice for case in self.cases)
        if counts != Counter(EXPECTED_SLICE_COUNTS):
            raise ValueError("Golden-v1 slice membership is not the released 45-case shape")

        positive_item_ids = [item.item_id for case in self.cases for item in case.expected_items]
        if len(positive_item_ids) != len(set(positive_item_ids)):
            raise ValueError("duplicate positive item membership across Golden cases")
        fixture_threads = set(self.fixture_thread_ids)
        fixture_items = set(self.fixture_item_ids)
        if len(fixture_threads) != len(self.fixture_thread_ids):
            raise ValueError("duplicate fixture thread membership")
        if len(fixture_items) != len(self.fixture_item_ids):
            raise ValueError("duplicate fixture item membership")
        if len(self.fixture_item_locators) != len(self.fixture_item_ids):
            raise ValueError("fixture item/locator membership lengths differ")
        if len(set(self.fixture_item_locators)) != len(self.fixture_item_locators):
            raise ValueError("duplicate fixture item locator")
        for item_id, locator in zip(self.fixture_item_ids, self.fixture_item_locators, strict=True):
            if locator != item_id and not locator.startswith(item_id + "#event="):
                raise ValueError("fixture item/locator identity mismatch")
        negative_routes: Counter[HardNegativeRoute] = Counter()
        false_categories: Counter[FalseValidationCategory] = Counter()
        for case in self.cases:
            if len(case.hard_negatives) != 1:
                raise ValueError("each Golden-v1 case has exactly one hard-negative membership")
            negative_routes.update(item.reason for item in case.hard_negatives)
            if case.eligible_metrics != _expected_eligible_metrics(case):
                raise ValueError("released per-case metric eligibility is inconsistent")
            for expected in case.expected_threads:
                if expected.thread_id not in fixture_threads:
                    raise ValueError("expected thread is absent from fixture membership")
            for expected in case.expected_items:
                if expected.item_id not in fixture_items:
                    raise ValueError("expected item is absent from fixture membership")
            for truth in case.event_truth:
                if truth.thread_id not in fixture_threads or truth.item_id not in fixture_items:
                    raise ValueError("event label points outside the fixture")
            for negative in case.hard_negatives:
                if (
                    negative.thread_id not in fixture_threads
                    or negative.item_id not in fixture_items
                ):
                    raise ValueError("hard-negative label points outside the fixture")
            for judgment in case.item_judgments:
                if (
                    judgment.thread_id not in fixture_threads
                    or judgment.item_id not in fixture_items
                ):
                    raise ValueError("item judgment points outside the fixture")
                if judgment.state_label not in case.state_labels:
                    raise ValueError("item judgment state lacks case authority membership")
                if judgment.temporal_label not in case.temporal_labels:
                    raise ValueError("item judgment temporal label lacks case authority membership")
                if judgment.false_validation_category is not None:
                    false_categories[judgment.false_validation_category] += 1
        if set(negative_routes) != EXPECTED_NEGATIVE_ROUTES:
            raise ValueError("Golden-v1 must cover exactly ten hard-negative design routes")
        if any(count < 4 for count in negative_routes.values()):
            raise ValueError("hard-negative design routes require distributed case coverage")
        if set(false_categories) != EXPECTED_FALSE_VALIDATION_CATEGORIES:
            raise ValueError("Golden-v1 false-validation categories are incomplete")
        if any(count != 1 for count in false_categories.values()):
            raise ValueError("each false-validation category has exactly one eligible label")
        normalized_authority = _sha256(
            b"".join(case.canonical_json_bytes() + b"\n" for case in self.cases)
        )
        if (
            self.authority_hash != CODEX_GOLDEN_AUTHORITY_HASH
            or normalized_authority != CODEX_GOLDEN_AUTHORITY_HASH
        ):
            raise ValueError("Golden-v1 released authority hash mismatch")
        return self

    @property
    def case_membership(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    @property
    def fixture_locator_by_item(self) -> Mapping[str, str]:
        return dict(zip(self.fixture_item_ids, self.fixture_item_locators, strict=True))


class ReviewedRetrievalRow(_FrozenContract):
    dataset_id: Literal["codex-golden-v1"]
    dataset_version: Literal["v1"]
    package_hash: Sha256
    case_id: Identifier
    rank: PositiveInt
    thread_id: ThreadId
    item_id: ContractText
    locator: ContractText
    reviewed: Literal[True]

    @model_validator(mode="after")
    def _row_identity(self) -> Self:
        prefix = f"codex://thread/{quote(self.thread_id, safe='-._')}/"
        if not self.item_id.startswith(prefix):
            raise ValueError("review row item is outside its declared thread")
        if "/episode/" in self.item_id:
            if self.locator != self.item_id:
                raise ValueError("episode review row locator must equal item identity")
        else:
            match = _LOCATOR_RE.fullmatch(self.locator)
            if match is None or match.group(1) != self.item_id:
                raise ValueError("review row locator does not match item")
        return self


class MetricResult(_FrozenContract):
    name: Identifier
    status: MetricStatus
    numerator: StrictFloat | StrictInt | None
    denominator: StrictFloat | StrictInt | None
    value: StrictFloat | None
    unit: Literal["ratio", "mean_reciprocal_rank"]
    reason: ContractText | None = None
    slice: CodexGoldenSlice | Literal["overall"] = "overall"

    @model_validator(mode="after")
    def _status_is_honest(self) -> Self:
        if self.status == MetricStatus.UNAVAILABLE:
            if self.value is not None or self.reason is None:
                raise ValueError("UNAVAILABLE metrics require no value and an explicit reason")
            if self.denominator not in {None, 0}:
                raise ValueError("UNAVAILABLE metric cannot hide a non-zero denominator")
            return self
        if self.denominator is None or float(self.denominator) <= 0:
            raise ValueError("scored metric requires a positive denominator")
        if self.numerator is None or self.value is None:
            raise ValueError("scored metric requires numerator and value")
        expected = float(self.numerator) / float(self.denominator)
        if abs(self.value - expected) > 1e-12:
            raise ValueError("metric value must equal numerator / denominator")
        if self.status == MetricStatus.PROVISIONAL and self.reason is None:
            raise ValueError("PROVISIONAL metric requires a reason")
        return self

    @property
    def metric_name(self) -> str:
        return self.name


class FoundationProbeSummary(_FrozenContract):
    dataset_id: Literal["codex-golden-v1"]
    dataset_version: Literal["v1"]
    schema_version: Literal["codex-evaluation-foundation-v1"]
    package_hash: Sha256
    case_count: Literal[45]
    slice_counts: dict[CodexGoldenSlice, PositiveInt]
    fixture_file_count: PositiveInt
    fixture_thread_count: PositiveInt
    fixture_item_count: PositiveInt
    item_judgment_count: PositiveInt
    hard_negative_route_count: Literal[10]
    false_validation_category_count: Literal[5]
    immutable_package_verified: Literal[True]
    formal_database_accessed: Literal[False] = False
    evaluation_run_created: Literal[False] = False
    baseline_metrics_run: Literal[False] = False
    baseline_qualified: Literal[False] = False
    release_posture: Literal["FOUNDATION_ONLY"] = "FOUNDATION_ONLY"


def _fixture_root() -> Path:
    return Path(__file__).resolve().parents[5] / "tests" / "fixtures" / "codex_golden_v1"


def _require_package_root(root: Path) -> Path:
    try:
        mode = root.lstat().st_mode
    except OSError as exc:
        raise CodexEvaluationError("released fixture directory is missing") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise CodexEvaluationError("released fixture root must be a real directory")
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        raise CodexEvaluationError("released fixture root cannot be resolved") from exc


def _require_self_contained_regular(
    root: Path,
    resolved_root: Path,
    relative: str,
) -> Path:
    candidate = root
    parts = PurePosixPath(relative).parts
    for index, part in enumerate(parts):
        candidate = candidate / part
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            raise CodexEvaluationError(f"package path is missing: {relative}") from exc
        if stat.S_ISLNK(mode):
            raise CodexEvaluationError(f"package symlink alias is forbidden: {relative}")
        final = index == len(parts) - 1
        if final and not stat.S_ISREG(mode):
            raise CodexEvaluationError(f"listed package object is not a regular file: {relative}")
        if not final and not stat.S_ISDIR(mode):
            raise CodexEvaluationError(f"package path component is not a directory: {relative}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CodexEvaluationError(f"listed package object cannot be resolved: {relative}") from exc
    if not resolved.is_relative_to(resolved_root):
        raise CodexEvaluationError(f"listed package object escapes package root: {relative}")
    return candidate


def _scan_release_text(path: str, text: str) -> None:
    if _ABSOLUTE_PATH_RE.search(text):
        raise CodexEvaluationError(f"absolute path is forbidden in released fixture: {path}")
    if _CREDENTIAL_RE.search(text):
        raise CodexEvaluationError(f"credential-shaped content is forbidden: {path}")
    if _EMAIL_RE.search(text):
        raise CodexEvaluationError(f"email address is forbidden: {path}")
    if _PAYMENT_RE.search(text):
        raise CodexEvaluationError(f"payment identifier is forbidden: {path}")


def _read_canonical_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodexEvaluationError(f"invalid JSON: {path.name}") from exc
    if not isinstance(parsed, dict):
        raise CodexEvaluationError(f"expected a JSON object: {path.name}")
    if raw != _canonical_json(parsed) + b"\n":
        raise CodexEvaluationError(f"JSON is not canonical: {path.name}")
    return parsed


def _read_canonical_jsonl(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise CodexEvaluationError(f"canonical JSONL requires a final newline: {path.name}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
        if line == b"\n":
            raise CodexEvaluationError(f"blank JSONL row at {path.name}:{line_number}")
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodexEvaluationError(f"invalid JSONL row at {path.name}:{line_number}") from exc
        if not isinstance(row, dict) or line != _canonical_json(row) + b"\n":
            raise CodexEvaluationError(f"non-canonical JSONL row at {path.name}:{line_number}")
        rows.append(row)
    return rows


def _manifest_hash(manifest: _ReleasedManifest) -> str:
    payload = manifest.model_dump(mode="json")
    payload.pop("package_hash")
    return _sha256(_canonical_json(payload))


def _adapter_fixture_inventory(
    fixture_root: Path,
    manifest: _ReleasedManifest,
) -> tuple[
    dict[str, tuple[str, str, str]],
    dict[str, tuple[str, str, str, str | None, str | None]],
]:
    """Independently derive released identities with the production adapter."""

    from evidence_rag.codex_adapter import CodexSessionAdapter
    from evidence_rag.config import Settings

    adapter = CodexSessionAdapter(
        Settings(
            data_dir=fixture_root,
            database_path=fixture_root / "unused.sqlite3",
            repository_cache=fixture_root,
            web_dir=fixture_root,
            allowed_local_roots=(fixture_root,),
            max_codex_item_chars=16_000,
        )
    )
    titles = adapter.read_titles(fixture_root)
    threads: dict[str, tuple[str, str, str]] = {}
    items: dict[str, tuple[str, str, str, str | None, str | None]] = {}
    for entry in manifest.files:
        if entry.kind != "session":
            continue
        path = fixture_root.joinpath(*PurePosixPath(entry.path).parts)
        parsed = adapter.parse(
            path,
            source_root=fixture_root,
            source_id="codex-source://golden-v1",
            generation_id="codex-generation://golden-v1",
            project_id="project-rag",
            project_path=None,
            acl_ref="project:project-rag",
            titles=titles,
        )
        if parsed is None:
            raise CodexEvaluationError(f"adapter rejected released session: {entry.path}")
        thread_id = parsed.thread.thread_id
        if thread_id in threads:
            raise CodexEvaluationError("adapter produced duplicate fixture thread")
        threads[thread_id] = (entry.path, parsed.thread.id, parsed.thread.status)
        actual_item_ids = tuple(item.id for item in parsed.items)
        if set(actual_item_ids) != set(entry.item_ids) or tuple(entry.thread_ids) != (thread_id,):
            raise CodexEvaluationError(
                f"manifest membership differs from adapter entities: {entry.path}"
            )
        for item in parsed.items:
            if item.id in items:
                raise CodexEvaluationError("adapter produced duplicate fixture item")
            call_id = item.metadata.get("call_id")
            items[item.id] = (
                entry.path,
                item.source_locator,
                item.item_type,
                str(call_id) if call_id else None,
                item.status,
            )
    return threads, items


def load_codex_golden_v1(root: str | Path | None = None) -> GoldenDataset:
    """Load and fully verify only the released ``codex-golden-v1`` package."""

    fixture_root = Path(root) if root is not None else _fixture_root()
    resolved_root = _require_package_root(fixture_root)
    manifest_path = _require_self_contained_regular(
        fixture_root,
        resolved_root,
        CODEX_GOLDEN_MANIFEST,
    )
    try:
        manifest = _ReleasedManifest.model_validate(_read_canonical_json(manifest_path))
    except (OSError, ValueError) as exc:
        if isinstance(exc, CodexEvaluationError):
            raise
        raise CodexEvaluationError("invalid released Codex Golden-v1 manifest") from exc
    if _manifest_hash(manifest) != manifest.package_hash:
        raise CodexEvaluationError("manifest package hash mismatch")

    listed = {CODEX_GOLDEN_MANIFEST}
    for entry in manifest.files:
        listed.add(entry.path)
        path = _require_self_contained_regular(
            fixture_root,
            resolved_root,
            entry.path,
        )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CodexEvaluationError(f"manifest file is missing: {entry.path}") from exc
        if len(raw) != entry.size or _sha256(raw) != entry.sha256:
            raise CodexEvaluationError(f"fixture hash/size mismatch: {entry.path}")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CodexEvaluationError(f"fixture is not UTF-8: {entry.path}") from exc
        _scan_release_text(entry.path, text)
        if entry.path.endswith(".jsonl"):
            _read_canonical_jsonl(path)
        elif entry.path.endswith(".json"):
            _read_canonical_json(path)

    actual: set[str] = set()
    for path in fixture_root.rglob("*"):
        mode = path.lstat().st_mode
        relative = path.relative_to(fixture_root).as_posix()
        if stat.S_ISLNK(mode):
            raise CodexEvaluationError(f"package symlink alias is forbidden: {relative}")
        if stat.S_ISREG(mode):
            actual.add(relative)
        elif not stat.S_ISDIR(mode):
            raise CodexEvaluationError(f"package contains non-regular object: {relative}")
    if actual != listed:
        raise CodexEvaluationError("fixture contains unlisted, missing, or mutable artifacts")
    if manifest.package_hash != CODEX_GOLDEN_PACKAGE_HASH:
        raise CodexEvaluationError("package is not the pinned released Codex Golden-v1")

    adapter_threads, adapter_items = _adapter_fixture_inventory(fixture_root, manifest)

    golden_files = [item for item in manifest.files if item.kind == "golden"]
    if len(golden_files) != 1:
        raise CodexEvaluationError("manifest must identify exactly one Golden JSONL")
    rows = _read_canonical_jsonl(fixture_root / golden_files[0].path)
    try:
        cases = tuple(CodexGoldenCase.model_validate(row) for row in rows)
    except ValueError as exc:
        raise CodexEvaluationError("Golden case contract validation failed") from exc
    case_ids = tuple(case.case_id for case in cases)
    if case_ids != manifest.case_ids or case_ids != golden_files[0].case_ids:
        raise CodexEvaluationError("case membership/order differs across Golden and manifest")
    negative_route_counts = Counter(
        negative.reason for case in cases for negative in case.hard_negatives
    )
    eligible_metric_counts = Counter(metric for case in cases for metric in case.eligible_metrics)
    false_validation_counts = Counter(
        judgment.false_validation_category
        for case in cases
        for judgment in case.item_judgments
        if judgment.false_validation_category is not None
    )
    if (
        negative_route_counts != Counter(manifest.negative_route_counts)
        or eligible_metric_counts != Counter(manifest.eligible_metric_counts)
        or false_validation_counts != Counter(manifest.false_validation_category_counts)
        or sum(len(case.item_judgments) for case in cases) != manifest.item_judgment_count
    ):
        raise CodexEvaluationError(
            "released authority memberships differ across Golden and manifest"
        )

    session_files = [item for item in manifest.files if item.kind == "session"]
    fixture_threads = tuple(thread for item in session_files for thread in item.thread_ids)
    fixture_items = tuple(item_id for item in session_files for item_id in item.item_ids)
    try:
        dataset = GoldenDataset(
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            schema_version=manifest.schema_version,
            package_hash=manifest.package_hash,
            authority_hash=CODEX_GOLDEN_AUTHORITY_HASH,
            cases=cases,
            fixture_thread_ids=fixture_threads,
            fixture_item_ids=fixture_items,
            fixture_item_locators=tuple(adapter_items[item_id][1] for item_id in fixture_items),
        )
    except ValueError as exc:
        raise CodexEvaluationError("released Golden dataset validation failed") from exc
    for case in dataset.cases:
        for expected in case.expected_threads:
            source_file, locator, status = adapter_threads[expected.thread_id]
            if (
                expected.source_file != source_file
                or expected.locator != locator
                or expected.status != status
            ):
                raise CodexEvaluationError("expected thread identity differs from adapter truth")
        for expected in case.expected_items:
            source_file, locator, item_type, _, _ = adapter_items[expected.item_id]
            if (
                expected.source_file != source_file
                or expected.locator != locator
                or expected.item_type != item_type
            ):
                raise CodexEvaluationError("expected item identity differs from adapter truth")
        for truth in case.event_truth:
            if not adapter_items[truth.item_id][1].startswith(truth.item_id):
                raise CodexEvaluationError("event truth locator differs from adapter truth")
        for negative in case.hard_negatives:
            if adapter_items[negative.item_id][1] != negative.locator:
                raise CodexEvaluationError("hard-negative locator differs from adapter truth")
        for judgment in case.item_judgments:
            _, locator, _, call_id, status = adapter_items[judgment.item_id]
            if judgment.locator != locator:
                raise CodexEvaluationError("item judgment locator differs from adapter truth")
            if judgment.call_id is not None and judgment.call_id != call_id:
                raise CodexEvaluationError("item judgment call identity differs from adapter truth")
            if judgment.validation_status is not None and judgment.validation_status != status:
                raise CodexEvaluationError("validation judgment differs from adapter truth")
            if judgment.episode_id is not None:
                episode = adapter_items.get(judgment.episode_id)
                if episode is None or episode[2] != "DevelopmentEpisode":
                    raise CodexEvaluationError("judgment episode differs from adapter truth")
        for evidence in case.evidence_provenance:
            if evidence.thread_id is not None:
                source_file = adapter_threads[evidence.thread_id][0]
                if evidence.source_file != source_file:
                    raise CodexEvaluationError("provenance source differs from adapter truth")
            if evidence.item_id is not None:
                source_file, locator, _, _, _ = adapter_items[evidence.item_id]
                match = re.search(r"#event=([1-9][0-9]*)$", locator)
                if (
                    evidence.source_file != source_file
                    or match is None
                    or not evidence.line_start <= int(match.group(1)) <= evidence.line_end
                ):
                    raise CodexEvaluationError("provenance line differs from adapter truth")
    return dataset


def _replace_project_root(value: Any, project_root: str) -> Any:
    if isinstance(value, str):
        return value.replace(PROJECT_ROOT_TOKEN, project_root)
    if isinstance(value, list):
        return [_replace_project_root(item, project_root) for item in value]
    if isinstance(value, dict):
        return {key: _replace_project_root(item, project_root) for key, item in value.items()}
    return value


def materialize_codex_session_fixture(
    destination: str | Path,
    *,
    project_root: str | Path,
    fixture_root: str | Path | None = None,
) -> Path:
    """Materialize immutable templates into a new temporary Codex home.

    Only session JSONL and ``session_index.jsonl`` are written.  The destination
    must be absent or empty; no existing Codex home or database is accepted.
    """

    source_root = Path(fixture_root) if fixture_root is not None else _fixture_root()
    dataset = load_codex_golden_v1(source_root)
    del dataset
    destination_path = Path(destination)
    if destination_path.exists() and any(destination_path.iterdir()):
        raise CodexEvaluationError("materialization destination must be empty")
    repository = Path(project_root)
    if not repository.is_absolute():
        raise CodexEvaluationError("materialized project_root must be absolute")
    destination_path.mkdir(parents=True, exist_ok=True)
    manifest = _ReleasedManifest.model_validate(
        _read_canonical_json(source_root / CODEX_GOLDEN_MANIFEST)
    )
    resolved_project = str(repository.resolve(strict=False))
    for entry in manifest.files:
        if entry.kind not in {"session", "session_index"}:
            continue
        rows = _read_canonical_jsonl(source_root / entry.path)
        target = destination_path.joinpath(*PurePosixPath(entry.path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = b"".join(
            _canonical_json(_replace_project_root(row, resolved_project)) + b"\n" for row in rows
        )
        target.write_bytes(content)
    return destination_path


def _metric(
    name: str,
    numerator: int | float,
    denominator: int | float,
    *,
    unit: Literal["ratio", "mean_reciprocal_rank"] = "ratio",
    provisional_reason: str | None = None,
) -> MetricResult:
    if denominator <= 0:
        return MetricResult(
            name=name,
            status=MetricStatus.UNAVAILABLE,
            numerator=None,
            denominator=0,
            value=None,
            unit=unit,
            reason="no eligible reviewed labels",
        )
    status = MetricStatus.PROVISIONAL if provisional_reason else MetricStatus.AVAILABLE
    return MetricResult(
        name=name,
        status=status,
        numerator=numerator,
        denominator=denominator,
        value=float(numerator) / float(denominator),
        unit=unit,
        reason=provisional_reason,
    )


def _unavailable(name: str, reason: str, *, unit: str = "ratio") -> MetricResult:
    return MetricResult(
        name=name,
        status=MetricStatus.UNAVAILABLE,
        numerator=None,
        denominator=0,
        value=None,
        unit=unit,
        reason=reason,
    )


def evaluate_reviewed_codex_retrieval(
    dataset: GoldenDataset,
    rows: Iterable[ReviewedRetrievalRow | Mapping[str, Any]],
    *,
    case_membership: Sequence[str] | None = None,
) -> tuple[MetricResult, ...]:
    """Compute foundation metrics from reviewed rows and released labels only."""

    expected_membership = dataset.case_membership
    supplied_membership = expected_membership if case_membership is None else tuple(case_membership)
    if supplied_membership != expected_membership or len(set(supplied_membership)) != 45:
        raise CodexEvaluationError("review membership must equal the ordered 45-case denominator")

    normalized: list[ReviewedRetrievalRow] = []
    try:
        for value in rows:
            row = (
                value
                if isinstance(value, ReviewedRetrievalRow)
                else ReviewedRetrievalRow.model_validate(value)
            )
            normalized.append(row)
    except ValueError as exc:
        raise CodexEvaluationError("invalid reviewed retrieval row") from exc

    cases = {case.case_id: case for case in dataset.cases}
    fixture_items = set(dataset.fixture_item_ids)
    fixture_locators = dataset.fixture_locator_by_item
    judgments_by_case = {
        case.case_id: {item.item_id: item for item in case.item_judgments} for case in dataset.cases
    }
    ranks: set[tuple[str, int]] = set()
    for row in normalized:
        if (
            row.dataset_id != dataset.dataset_id
            or row.dataset_version != dataset.dataset_version
            or row.package_hash != dataset.package_hash
        ):
            raise CodexEvaluationError("review row dataset/package identity mismatch")
        if row.case_id not in cases:
            raise CodexEvaluationError("review row references unknown case")
        if row.item_id not in fixture_items:
            raise CodexEvaluationError("review row item is absent from released fixture")
        if row.locator != fixture_locators[row.item_id]:
            raise CodexEvaluationError("review row locator differs from released fixture")
        judgment = judgments_by_case[row.case_id].get(row.item_id)
        if judgment is None:
            raise CodexEvaluationError("review row has no released case/item judgment authority")
        if row.thread_id != judgment.thread_id or row.locator != judgment.locator:
            raise CodexEvaluationError("review row differs from released judgment identity")
        rank_key = (row.case_id, row.rank)
        if rank_key in ranks:
            raise CodexEvaluationError("duplicate rank in one reviewed case")
        ranks.add(rank_key)

    grouped: dict[str, list[ReviewedRetrievalRow]] = defaultdict(list)
    for row in normalized:
        grouped[row.case_id].append(row)
    for case_rows in grouped.values():
        case_rows.sort(key=lambda item: item.rank)
        if [row.rank for row in case_rows] != list(range(1, len(case_rows) + 1)):
            raise CodexEvaluationError("reviewed ranks must be contiguous from one")

    def eligible(metric: CodexMetric) -> tuple[CodexGoldenCase, ...]:
        return tuple(case for case in dataset.cases if metric in case.eligible_metrics)

    thread_hits = 0
    for case in eligible(CodexMetric.THREAD_RECALL_AT_5):
        threads5 = {row.thread_id for row in grouped.get(case.case_id, ())[:5]}
        expected_threads = {item.thread_id for item in case.expected_threads}
        thread_hits += int(bool(expected_threads & threads5))
    thread_denominator = len(eligible(CodexMetric.THREAD_RECALL_AT_5))

    episode_hits = 0
    for case in eligible(CodexMetric.EPISODE_RECALL_AT_5):
        case_rows = grouped.get(case.case_id, ())[:5]
        judgments = judgments_by_case[case.case_id]
        retrieved_episodes = {
            judgments[row.item_id].episode_id
            for row in case_rows
            if judgments[row.item_id].episode_id is not None
        }
        expected_episodes = {
            item.episode_id for item in case.expected_threads if item.episode_id is not None
        }
        episode_hits += int(bool(expected_episodes & retrieved_episodes))
    episode_denominator = len(eligible(CodexMetric.EPISODE_RECALL_AT_5))

    item_hits = 0
    for case in eligible(CodexMetric.ITEM_RECALL_AT_10):
        items10 = {row.item_id for row in grouped.get(case.case_id, ()) if row.rank <= 10}
        expected_items = {item.item_id for item in case.expected_items}
        item_hits += int(bool(expected_items & items10))
    item_denominator = len(eligible(CodexMetric.ITEM_RECALL_AT_10))

    reciprocal_rank_sum = 0.0
    for case in eligible(CodexMetric.MRR_AT_10):
        expected_items = {item.item_id for item in case.expected_items}
        first = next(
            (
                row.rank
                for row in grouped.get(case.case_id, ())
                if row.rank <= 10 and row.item_id in expected_items
            ),
            None,
        )
        reciprocal_rank_sum += 1.0 / first if first else 0.0
    reciprocal_rank_denominator = len(eligible(CodexMetric.MRR_AT_10))

    goal_hits = 0
    for case in eligible(CodexMetric.GOAL_RECALL_AT_10):
        expected_goals = {
            item.item_id for item in case.expected_items if item.item_type == "UserGoal"
        }
        goal_hits += int(
            any(
                row.rank <= 10 and row.item_id in expected_goals
                for row in grouped.get(case.case_id, ())
            )
        )
    goal_denominator = len(eligible(CodexMetric.GOAL_RECALL_AT_10))

    decision_hits = 0
    for case in eligible(CodexMetric.DECISION_RECALL_AT_10):
        expected_items = {item.item_id for item in case.expected_items}
        decision_hits += int(
            any(
                row.rank <= 10 and row.item_id in expected_items
                for row in grouped.get(case.case_id, ())
            )
        )
    decision_denominator = len(eligible(CodexMetric.DECISION_RECALL_AT_10))

    old_harmful_ids = {
        (case.case_id, item.item_id)
        for case in eligible(CodexMetric.HARMFUL_OLD_ATTEMPT_RATE_AT_10)
        for item in case.hard_negatives
        if item.old_attempt
    }
    retrieved_pairs = {(row.case_id, row.item_id) for row in normalized if row.rank <= 10}
    old_harmful_hits = len(old_harmful_ids & retrieved_pairs)

    order_correct = 0
    order_cases = eligible(CodexMetric.EVENT_ORDER_ACCURACY_AT_10)
    for case in order_cases:
        case_rows = grouped.get(case.case_id, ())
        row_by_item = {row.item_id: row for row in case_rows if row.rank <= 10}
        ordered_truth = sorted(
            (item for item in case.event_truth if item.order is not None),
            key=lambda item: item.order or 0,
        )
        retrieved_ranks = [
            row_by_item[item.item_id].rank for item in ordered_truth if item.item_id in row_by_item
        ]
        order_correct += int(
            len(retrieved_ranks) == len(ordered_truth)
            and retrieved_ranks == sorted(retrieved_ranks)
        )

    call_correct = 0
    call_denominator = 0
    for case in eligible(CodexMetric.CALL_RESULT_LINK_ACCURACY_AT_10):
        row_ids = {row.item_id for row in grouped.get(case.case_id, ()) if row.rank <= 10}
        calls_by_id: dict[str, list[EventTruth]] = defaultdict(list)
        for truth in case.event_truth:
            if truth.call_id:
                calls_by_id[truth.call_id].append(truth)
        for truths in calls_by_id.values():
            kinds = {truth.kind for truth in truths}
            if {"tool_call", "tool_result"} <= kinds:
                call_denominator += 1
                call_correct += int(
                    all(
                        truth.item_id in row_ids
                        for truth in truths
                        if truth.kind in {"tool_call", "tool_result"}
                    )
                )

    patch_correct = 0
    patch_denominator = 0
    for case in eligible(CodexMetric.PATCH_ACCURACY_AT_10):
        row_ids = {row.item_id for row in grouped.get(case.case_id, ()) if row.rank <= 10}
        judgments = judgments_by_case[case.case_id]
        for truth in case.event_truth:
            if truth.kind in {"patch", "file_change"} and truth.patch_applied is not None:
                patch_denominator += 1
                patch_correct += int(
                    truth.item_id in row_ids
                    and judgments[truth.item_id].patch_applied == truth.patch_applied
                )

    validation_correct = 0
    validation_denominator = 0
    for case in eligible(CodexMetric.VALIDATION_ACCURACY_AT_10):
        row_ids = {row.item_id for row in grouped.get(case.case_id, ()) if row.rank <= 10}
        judgments = judgments_by_case[case.case_id]
        for truth in case.event_truth:
            if truth.kind == "validation":
                validation_denominator += 1
                expected_status = "passed" if truth.exit_code == 0 else "failed"
                validation_correct += int(
                    truth.item_id in row_ids
                    and judgments[truth.item_id].validation_status == expected_status
                )

    false_validation_labels = [
        (case.case_id, judgment)
        for case in eligible(CodexMetric.FALSE_VALIDATED_RATE_AT_10)
        for judgment in case.item_judgments
        if judgment.false_validation_category is not None
    ]
    false_validated_hits = sum(
        judgment.false_validated is True and (case_id, judgment.item_id) in retrieved_pairs
        for case_id, judgment in false_validation_labels
    )

    outcome_correct = 0
    outcome_denominator = 0
    for case in eligible(CodexMetric.OUTCOME_ACCURACY_AT_10):
        row_ids = {row.item_id for row in grouped.get(case.case_id, ()) if row.rank <= 10}
        judgments = judgments_by_case[case.case_id]
        for truth in case.event_truth:
            if truth.kind == "outcome":
                outcome_denominator += 1
                outcome_correct += int(
                    truth.item_id in row_ids and judgments[truth.item_id].outcome_correct is True
                )

    duplicate_cases = 0
    duplicate_eligible = eligible(CodexMetric.CONTEXT_DUPLICATE_RATE_AT_10)
    for case in duplicate_eligible:
        seen: set[tuple[str, str]] = set()
        duplicated = False
        for row in grouped.get(case.case_id, ()):
            if row.rank > 10:
                continue
            identity = (row.thread_id, row.item_id)
            if identity in seen:
                duplicated = True
            seen.add(identity)
        duplicate_cases += int(duplicated)

    noise_cases = 0
    noise_eligible = eligible(CodexMetric.CONTEXT_NOISE_RATE_AT_10)
    for case in noise_eligible:
        judgments = judgments_by_case[case.case_id]
        noise_cases += int(
            any(
                row.rank <= 10 and judgments[row.item_id].context_noise
                for row in grouped.get(case.case_id, ())
            )
        )

    values = {
        CodexMetric.THREAD_RECALL_AT_5: _metric(
            CodexMetric.THREAD_RECALL_AT_5.value,
            thread_hits,
            thread_denominator,
        ),
        CodexMetric.EPISODE_RECALL_AT_5: _metric(
            CodexMetric.EPISODE_RECALL_AT_5.value,
            episode_hits,
            episode_denominator,
        ),
        CodexMetric.ITEM_RECALL_AT_10: _metric(
            CodexMetric.ITEM_RECALL_AT_10.value,
            item_hits,
            item_denominator,
        ),
        CodexMetric.MRR_AT_10: _metric(
            CodexMetric.MRR_AT_10.value,
            reciprocal_rank_sum,
            reciprocal_rank_denominator,
            unit="mean_reciprocal_rank",
        ),
        CodexMetric.GOAL_RECALL_AT_10: _metric(
            CodexMetric.GOAL_RECALL_AT_10.value,
            goal_hits,
            goal_denominator,
        ),
        CodexMetric.DECISION_RECALL_AT_10: _metric(
            CodexMetric.DECISION_RECALL_AT_10.value,
            decision_hits,
            decision_denominator,
        ),
        CodexMetric.HARMFUL_OLD_ATTEMPT_RATE_AT_10: _metric(
            CodexMetric.HARMFUL_OLD_ATTEMPT_RATE_AT_10.value,
            old_harmful_hits,
            len(old_harmful_ids),
        ),
        CodexMetric.EVENT_ORDER_ACCURACY_AT_10: _metric(
            CodexMetric.EVENT_ORDER_ACCURACY_AT_10.value,
            order_correct,
            len(order_cases),
        ),
        CodexMetric.CALL_RESULT_LINK_ACCURACY_AT_10: _metric(
            CodexMetric.CALL_RESULT_LINK_ACCURACY_AT_10.value,
            call_correct,
            call_denominator,
        ),
        CodexMetric.PATCH_ACCURACY_AT_10: _metric(
            CodexMetric.PATCH_ACCURACY_AT_10.value,
            patch_correct,
            patch_denominator,
        ),
        CodexMetric.VALIDATION_ACCURACY_AT_10: _metric(
            CodexMetric.VALIDATION_ACCURACY_AT_10.value,
            validation_correct,
            validation_denominator,
        ),
        CodexMetric.FALSE_VALIDATED_RATE_AT_10: _metric(
            CodexMetric.FALSE_VALIDATED_RATE_AT_10.value,
            false_validated_hits,
            len(false_validation_labels),
        ),
        CodexMetric.OUTCOME_ACCURACY_AT_10: _metric(
            CodexMetric.OUTCOME_ACCURACY_AT_10.value,
            outcome_correct,
            outcome_denominator,
        ),
        CodexMetric.CONTEXT_DUPLICATE_RATE_AT_10: _metric(
            CodexMetric.CONTEXT_DUPLICATE_RATE_AT_10.value,
            duplicate_cases,
            len(duplicate_eligible),
        ),
        CodexMetric.CONTEXT_NOISE_RATE_AT_10: _metric(
            CodexMetric.CONTEXT_NOISE_RATE_AT_10.value,
            noise_cases,
            len(noise_eligible),
        ),
    }
    return tuple(values[metric] for metric in CodexMetric)


def probe_codex_evaluation_foundation(
    root: str | Path | None = None,
) -> FoundationProbeSummary:
    """Read-only integrity probe.  It never touches a DB, retriever, or run service."""

    fixture_root = Path(root) if root is not None else _fixture_root()
    dataset = load_codex_golden_v1(fixture_root)
    manifest = _ReleasedManifest.model_validate(
        _read_canonical_json(fixture_root / CODEX_GOLDEN_MANIFEST)
    )
    return FoundationProbeSummary(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        schema_version=dataset.schema_version,
        package_hash=dataset.package_hash,
        case_count=len(dataset.cases),
        slice_counts=dict(Counter(case.slice for case in dataset.cases)),
        fixture_file_count=len(manifest.files),
        fixture_thread_count=len(dataset.fixture_thread_ids),
        fixture_item_count=len(dataset.fixture_item_ids),
        item_judgment_count=manifest.item_judgment_count,
        hard_negative_route_count=len(manifest.negative_route_counts),
        false_validation_category_count=len(manifest.false_validation_category_counts),
        immutable_package_verified=True,
    )
