"""Deterministic, versioned goal-aware Codex episode projection."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    CodexEventNormalizationResult,
    EventScope,
    ObservableCodexItem,
    ObservableItemType,
    canonical_sha256,
)
from .facts_v1 import _run_bound_normalizer

CODEX_EPISODE_CONTRACT_VERSION = "codex-goal-episode-contract-v2"
CODEX_EPISODE_BUILDER_VERSION = "codex-goal-episode-builder-v2"
CODEX_EPISODE_DEFAULT_GAP_SECONDS = 1_800

_EXCLUDED_TYPES = {
    ObservableItemType.REASONING,
    ObservableItemType.AGENT_REASONING,
    ObservableItemType.TOKEN_COUNT,
    ObservableItemType.SYSTEM_MESSAGE,
    ObservableItemType.DEVELOPER_MESSAGE,
}


class _FrozenEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CodexEpisodeBoundaryReason(StrEnum):
    THREAD_START = "thread_start"
    NEW_USER_GOAL = "new_user_goal"
    TIME_GAP = "time_gap"
    CWD_OR_PROJECT_CHANGE = "cwd_or_project_change"
    FAILURE_RECOVERY = "failure_recovery"
    MANUAL_SPLIT = "manual_split"
    MANUAL_MERGE = "manual_merge"


class CodexEpisodeMember(_FrozenEpisode):
    episode_id: str
    source_item_id: str
    source_locator: str
    source_item_sha256: str
    turn_id: str
    observable_order: int = Field(ge=0)
    ordinal: int = Field(ge=0)
    item_type: ObservableItemType
    contract_version: str = CODEX_EPISODE_CONTRACT_VERSION


class CodexEpisodeBoundary(_FrozenEpisode):
    before_item_id: str | None
    at_item_id: str
    reason: CodexEpisodeBoundaryReason
    confidence: float = Field(ge=0, le=1)
    manual_override_id: str | None = None
    contract_version: str = CODEX_EPISODE_CONTRACT_VERSION


class CodexEpisodeOverride(_FrozenEpisode):
    override_id: str
    at_item_id: str
    operation: str
    contract_version: str = CODEX_EPISODE_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexEpisodeOverride:
        if self.operation not in {"split", "merge"}:
            raise ValueError("manual episode override must be split or merge")
        expected = (
            "override-"
            + canonical_sha256(
                {
                    "item_id": self.at_item_id,
                    "operation": self.operation,
                }
            ).removeprefix("sha256:")[:24]
        )
        if self.override_id != expected:
            raise ValueError("manual episode override identity mismatch")
        return self


class CodexEpisodeVersion(_FrozenEpisode):
    episode_id: str
    episode_version_id: str
    version: int = Field(ge=1)
    project_id: str
    source_id: str
    generation_id: str
    thread_id: str
    acl_ref: str
    member_ids: tuple[str, ...] = Field(min_length=1)
    member_sha256s: tuple[str, ...] = Field(min_length=1)
    boundary: CodexEpisodeBoundary
    goal_item_ids: tuple[str, ...]
    terminal_state: str
    content_sha256: str
    builder_version: str = CODEX_EPISODE_BUILDER_VERSION
    contract_version: str = CODEX_EPISODE_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexEpisodeVersion:
        if len(self.member_ids) != len(self.member_sha256s):
            raise ValueError("episode membership identity is incomplete")
        payload = self.model_dump(
            mode="json",
            exclude={"episode_id", "episode_version_id", "content_sha256"},
        )
        content = canonical_sha256(payload)
        if self.content_sha256 != content:
            raise ValueError("episode content digest mismatch")
        expected_episode = "codexep-" + canonical_sha256(
            {
                "project_id": self.project_id,
                "source_id": self.source_id,
                "thread_id": self.thread_id,
                "first_member": self.member_ids[0],
                "builder_version": self.builder_version,
            }
        ).removeprefix("sha256:")
        expected_version = "codexepv-" + canonical_sha256(
            {
                "episode_id": expected_episode,
                "content_sha256": content,
                "version": self.version,
            }
        ).removeprefix("sha256:")
        if self.episode_id != expected_episode or self.episode_version_id != expected_version:
            raise ValueError("episode identity mismatch")
        return self


class CodexEpisodeBuildResult(_FrozenEpisode):
    source_set_sha256: str
    normalization_sha256: str
    episodes: tuple[CodexEpisodeVersion, ...]
    members: tuple[CodexEpisodeMember, ...]
    boundaries: tuple[CodexEpisodeBoundary, ...]
    excluded_item_ids: tuple[str, ...]
    manual_overrides: tuple[CodexEpisodeOverride, ...]
    manual_override_ids: tuple[str, ...]
    builder_version: str = CODEX_EPISODE_BUILDER_VERSION
    contract_version: str = CODEX_EPISODE_CONTRACT_VERSION
    result_sha256: str

    @model_validator(mode="after")
    def _result_identity(self) -> CodexEpisodeBuildResult:
        episode_ids = {item.episode_id for item in self.episodes}
        if any(item.episode_id not in episode_ids for item in self.members):
            raise ValueError("episode member is orphaned")
        expected_override_ids = tuple(item.override_id for item in self.manual_overrides)
        if self.manual_override_ids != expected_override_ids:
            raise ValueError("manual episode override audit is incomplete")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"result_sha256"}))
        if self.result_sha256 != expected:
            raise ValueError("episode build result digest mismatch")
        return self


def _scope_key(scope: EventScope) -> tuple[str, ...]:
    return (
        scope.project_id,
        scope.source_id,
        scope.generation_id,
        scope.thread_id,
        scope.acl_ref,
    )


def _event_time(item: ObservableCodexItem) -> datetime | None:
    raw = item.payload.get("event_at") or item.payload.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo is not None else None


def _context_identity(item: ObservableCodexItem) -> str | None:
    value = item.payload.get("cwd_identity") or item.payload.get("project_identity")
    if value is None:
        return None
    if not isinstance(value, str) or not value or "/" in value or "\\" in value:
        raise ValueError("episode context identity must be opaque and portable")
    return value


def _goal_continues(
    item: ObservableCodexItem,
    current: list[ObservableCodexItem],
) -> bool:
    if item.item_type is not ObservableItemType.USER_GOAL:
        return False
    explicit = item.payload.get("continuation")
    continuation_of = item.payload.get("continuation_of")
    current_goal_ids = {
        value.item_id for value in current if value.item_type is ObservableItemType.USER_GOAL
    }
    if explicit is True or (
        isinstance(continuation_of, str) and continuation_of in current_goal_ids
    ):
        return True
    prior = next(
        (value for value in reversed(current) if value.item_type is ObservableItemType.USER_GOAL),
        None,
    )
    if prior is None:
        return False
    current_text = item.payload.get("text") or item.payload.get("goal")
    prior_text = prior.payload.get("text") or prior.payload.get("goal")
    if not isinstance(current_text, str) or not isinstance(prior_text, str):
        return False
    current_tokens = {token.casefold() for token in re.findall(r"[A-Za-z0-9_]+", current_text)}
    prior_tokens = {token.casefold() for token in re.findall(r"[A-Za-z0-9_]+", prior_text)}
    union = current_tokens | prior_tokens
    return (
        len(current_tokens) >= 3
        and len(prior_tokens) >= 3
        and len(current_tokens & prior_tokens) / len(union) >= 0.8
    )


def _terminal_states(
    normalization: CodexEventNormalizationResult,
) -> dict[str, str]:
    terminal: dict[str, str] = {}
    for event in normalization.events:
        state = getattr(event, "state", None)
        if state is None:
            continue
        value = str(state)
        if value in {
            "completed",
            "failed",
            "timeout",
            "cancelled",
            "applied",
            "passed",
        }:
            for evidence in event.evidence:
                terminal[evidence.source_item_id] = value
    return terminal


def _episode(
    *,
    items: tuple[ObservableCodexItem, ...],
    boundary: CodexEpisodeBoundary,
    terminal_states: Mapping[str, str],
    version: int,
) -> tuple[CodexEpisodeVersion, tuple[CodexEpisodeMember, ...]]:
    first = items[0]
    member_ids = tuple(item.item_id for item in items)
    member_sha256s = tuple(item.canonical_sha256() for item in items)
    episode_id = "codexep-" + canonical_sha256(
        {
            "project_id": first.scope.project_id,
            "source_id": first.scope.source_id,
            "thread_id": first.scope.thread_id,
            "first_member": member_ids[0],
            "builder_version": CODEX_EPISODE_BUILDER_VERSION,
        }
    ).removeprefix("sha256:")
    payload: dict[str, Any] = {
        "version": version,
        "project_id": first.scope.project_id,
        "source_id": first.scope.source_id,
        "generation_id": first.scope.generation_id,
        "thread_id": first.scope.thread_id,
        "acl_ref": first.scope.acl_ref,
        "member_ids": member_ids,
        "member_sha256s": member_sha256s,
        "boundary": boundary,
        "goal_item_ids": tuple(
            item.item_id for item in items if item.item_type == ObservableItemType.USER_GOAL
        ),
        "terminal_state": next(
            (
                terminal_states[item.item_id]
                for item in reversed(items)
                if item.item_id in terminal_states
            ),
            "open",
        ),
        "builder_version": CODEX_EPISODE_BUILDER_VERSION,
        "contract_version": CODEX_EPISODE_CONTRACT_VERSION,
    }
    content = canonical_sha256(
        {
            key: value.model_dump(mode="json") if isinstance(value, BaseModel) else value
            for key, value in payload.items()
        }
    )
    episode_version_id = "codexepv-" + canonical_sha256(
        {
            "episode_id": episode_id,
            "content_sha256": content,
            "version": version,
        }
    ).removeprefix("sha256:")
    episode = CodexEpisodeVersion(
        episode_id=episode_id,
        episode_version_id=episode_version_id,
        content_sha256=content,
        **payload,
    )
    members = tuple(
        CodexEpisodeMember(
            episode_id=episode_id,
            source_item_id=item.item_id,
            source_locator=item.source_locator,
            source_item_sha256=item.canonical_sha256(),
            turn_id=item.scope.turn_id,
            observable_order=item.observable_order,
            ordinal=index,
            item_type=item.item_type,
        )
        for index, item in enumerate(items)
    )
    return episode, members


def build_codex_episodes_v2(
    source_items: Iterable[ObservableCodexItem],
    normalization: CodexEventNormalizationResult,
    *,
    manual_overrides: Mapping[str, str] | None = None,
    gap_seconds: int = CODEX_EPISODE_DEFAULT_GAP_SECONDS,
    version: int = 1,
) -> CodexEpisodeBuildResult:
    """Build immutable episodes without changing source Item or Turn identity."""

    if gap_seconds < 1 or version < 1:
        raise ValueError("episode gap/version must be positive")
    all_items = tuple(source_items)
    if not all_items:
        raise ValueError("episode builder requires observable items")
    base_scope = _scope_key(all_items[0].scope)
    if any(_scope_key(item.scope) != base_scope for item in all_items):
        raise ValueError("episode builder cannot cross project/source/generation/thread/ACL")
    authoritative_normalization = _run_bound_normalizer(all_items)
    if authoritative_normalization != normalization:
        raise ValueError("normalization is not the exact production-derived result")
    turn_rank: dict[str, int] = {}
    for item in all_items:
        turn_rank.setdefault(item.scope.turn_id, len(turn_rank))
    ordered = tuple(
        sorted(
            (item for item in all_items if item.item_type not in _EXCLUDED_TYPES),
            key=lambda item: (
                turn_rank[item.scope.turn_id],
                item.observable_order,
                item.item_id,
            ),
        )
    )
    if not ordered:
        raise ValueError("episode builder has no observable evidence")
    if len({(item.scope.turn_id, item.item_id) for item in ordered}) != len(ordered):
        raise ValueError("episode source membership is duplicated")
    overrides = dict(manual_overrides or {})
    if set(overrides) - {item.item_id for item in ordered}:
        raise ValueError("manual episode override references unknown item")
    if any(value not in {"split", "merge"} for value in overrides.values()):
        raise ValueError("manual episode override must be split or merge")
    override_audit = tuple(
        CodexEpisodeOverride(
            override_id="override-"
            + canonical_sha256(
                {
                    "item_id": item_id,
                    "operation": operation,
                }
            ).removeprefix("sha256:")[:24],
            at_item_id=item_id,
            operation=operation,
        )
        for item_id, operation in sorted(overrides.items())
    )
    terminal_states = _terminal_states(normalization)
    chunks: list[list[ObservableCodexItem]] = []
    boundaries: list[CodexEpisodeBoundary] = []
    current: list[ObservableCodexItem] = []
    previous: ObservableCodexItem | None = None
    previous_time: datetime | None = None
    previous_context: str | None = None
    terminal_seen = False
    for item in ordered:
        item_time = _event_time(item)
        item_context = _context_identity(item)
        reason: CodexEpisodeBoundaryReason | None = None
        confidence = 1.0
        override = overrides.get(item.item_id)
        if not current:
            reason = CodexEpisodeBoundaryReason.THREAD_START
        elif override == "split":
            reason = CodexEpisodeBoundaryReason.MANUAL_SPLIT
        elif override != "merge":
            if item.item_type == ObservableItemType.USER_GOAL and not _goal_continues(
                item, current
            ):
                reason = CodexEpisodeBoundaryReason.NEW_USER_GOAL
                confidence = 0.95
            elif (
                item_time is not None
                and previous_time is not None
                and (item_time - previous_time).total_seconds() > gap_seconds
            ):
                reason = CodexEpisodeBoundaryReason.TIME_GAP
                confidence = 0.9
            elif (
                item_context is not None
                and previous_context is not None
                and item_context != previous_context
            ):
                reason = CodexEpisodeBoundaryReason.CWD_OR_PROJECT_CHANGE
                confidence = 0.95
            elif terminal_seen and item.item_type in {
                ObservableItemType.ACTION_PROPOSAL,
                ObservableItemType.TOOL_CALL,
                ObservableItemType.COMMAND_EXECUTION,
                ObservableItemType.PATCH,
                ObservableItemType.PATCH_APPLY,
            }:
                reason = CodexEpisodeBoundaryReason.FAILURE_RECOVERY
                confidence = 0.8
        if reason is not None:
            if current:
                chunks.append(current)
                current = []
            boundaries.append(
                CodexEpisodeBoundary(
                    before_item_id=previous.item_id if previous is not None else None,
                    at_item_id=item.item_id,
                    reason=(reason),
                    confidence=1.0 if override else confidence,
                    manual_override_id=(
                        "override-"
                        + canonical_sha256(
                            {"item_id": item.item_id, "operation": override}
                        ).removeprefix("sha256:")[:24]
                        if override
                        else None
                    ),
                )
            )
        current.append(item)
        terminal_seen = terminal_states.get(item.item_id) in {
            "failed",
            "timeout",
            "cancelled",
        }
        previous = item
        previous_time = item_time or previous_time
        previous_context = item_context or previous_context
    if current:
        chunks.append(current)
    if len(boundaries) != len(chunks):
        raise ValueError("episode boundary/chunk cardinality mismatch")
    episodes: list[CodexEpisodeVersion] = []
    members: list[CodexEpisodeMember] = []
    for chunk, boundary in zip(chunks, boundaries, strict=True):
        episode, episode_members = _episode(
            items=tuple(chunk),
            boundary=boundary,
            terminal_states=terminal_states,
            version=version,
        )
        episodes.append(episode)
        members.extend(episode_members)
    source_set_sha256 = canonical_sha256(
        [
            {
                "item_id": item.item_id,
                "locator": item.source_locator,
                "content_sha256": item.canonical_sha256(),
            }
            for item in ordered
        ]
    )
    normalization_sha256 = normalization.canonical_sha256()
    values = {
        "source_set_sha256": source_set_sha256,
        "normalization_sha256": normalization_sha256,
        "episodes": tuple(episodes),
        "members": tuple(members),
        "boundaries": tuple(boundaries),
        "excluded_item_ids": tuple(
            sorted(item.item_id for item in all_items if item.item_type in _EXCLUDED_TYPES)
        ),
        "manual_overrides": override_audit,
        "manual_override_ids": tuple(item.override_id for item in override_audit),
        "builder_version": CODEX_EPISODE_BUILDER_VERSION,
        "contract_version": CODEX_EPISODE_CONTRACT_VERSION,
    }
    return CodexEpisodeBuildResult(
        **values,
        result_sha256=canonical_sha256(
            {
                key: (
                    [item.model_dump(mode="json") for item in value]
                    if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
                    else value
                )
                for key, value in values.items()
            }
        ),
    )


__all__ = [
    "CODEX_EPISODE_BUILDER_VERSION",
    "CODEX_EPISODE_CONTRACT_VERSION",
    "CODEX_EPISODE_DEFAULT_GAP_SECONDS",
    "CodexEpisodeBoundary",
    "CodexEpisodeBoundaryReason",
    "CodexEpisodeBuildResult",
    "CodexEpisodeMember",
    "CodexEpisodeOverride",
    "CodexEpisodeVersion",
    "build_codex_episodes_v2",
]
