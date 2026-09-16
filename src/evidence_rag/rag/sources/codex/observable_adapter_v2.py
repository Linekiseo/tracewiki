"""Exact production Codex item to observable-event adapter for X2+."""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ....models import CodexItemRecord, CodexTurnRecord
from .contracts import (
    EventScope,
    ObservableCodexItem,
    ObservableItemType,
    ObservedOutcome,
    canonical_codex_locator_v1,
    canonical_sha256,
)

CODEX_OBSERVABLE_ADAPTER_VERSION = "codex-observable-adapter-v2"
_EVENT_RE = re.compile(r"#event=([1-9][0-9]*)$")
_RESULT_TYPES = {
    ObservableItemType.TOOL_RESULT,
    ObservableItemType.COMMAND_RESULT,
    ObservableItemType.PATCH_RESULT,
}
_TEXT_TYPES = {
    ObservableItemType.USER_GOAL,
    ObservableItemType.PLAN,
    ObservableItemType.AGENT_MESSAGE,
    ObservableItemType.ACTION_PROPOSAL,
    ObservableItemType.VALIDATION_MENTION,
}
_KNOWN_OUTCOMES = {item.value for item in ObservedOutcome}


class _FrozenAdapter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CodexObservableThreadV2(_FrozenAdapter):
    thread_id: str
    source_item_ids: tuple[str, ...] = Field(min_length=1)
    observable_items: tuple[ObservableCodexItem, ...] = Field(min_length=1)
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> CodexObservableThreadV2:
        if self.source_item_ids != tuple(item.item_id for item in self.observable_items):
            raise ValueError("observable thread source membership mismatch")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise ValueError("observable thread identity mismatch")
        return self


class CodexObservableAdapterResultV2(_FrozenAdapter):
    project_id: str
    thread_sets: tuple[CodexObservableThreadV2, ...]
    source_record_count: int = Field(ge=1)
    observable_item_count: int = Field(ge=1)
    skipped_episode_count: int = Field(ge=0)
    synthetic_result_count: int = Field(ge=0)
    source_set_sha256: str
    turn_context_sha256: str
    result_sha256: str
    adapter_version: str = CODEX_OBSERVABLE_ADAPTER_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexObservableAdapterResultV2:
        if self.observable_item_count != sum(
            len(item.observable_items) for item in self.thread_sets
        ):
            raise ValueError("observable adapter output count mismatch")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"result_sha256"}))
        if self.result_sha256 != expected:
            raise ValueError("observable adapter result identity mismatch")
        return self


def _adapter_event_number(locator: str) -> int:
    match = _EVENT_RE.search(locator)
    if match is None:
        raise ValueError("adapter item lacks a canonical event locator")
    return int(match.group(1))


def _observable_item_id(adapter_item_id: str) -> str:
    marker = "/item/"
    if marker not in adapter_item_id:
        raise ValueError("adapter item identity is not observable")
    item_id = unquote(adapter_item_id.rsplit(marker, 1)[1])
    if not item_id or "/" in item_id or "\\" in item_id:
        raise ValueError("adapter item identity is not a safe source segment")
    return item_id


def _command_argv(content: str) -> tuple[str, ...]:
    command = content.splitlines()[0].removeprefix("$ ").strip()
    try:
        return tuple(shlex.split(command, posix=True))[:64]
    except ValueError:
        return ()


def adapt_production_codex_items_v2(
    records: Iterable[CodexItemRecord],
    *,
    project_id: str,
    turns: Iterable[CodexTurnRecord] = (),
) -> CodexObservableAdapterResultV2:
    """Convert exact production records without consulting evaluation labels."""

    source_records = tuple(records)
    if not source_records or any(type(item) is not CodexItemRecord for item in source_records):
        raise TypeError("observable adapter requires exact production CodexItemRecord values")
    turn_records = tuple(turns)
    if any(type(item) is not CodexTurnRecord for item in turn_records):
        raise TypeError("observable adapter requires exact production CodexTurnRecord values")
    turn_context: dict[str, str] = {}
    for turn in turn_records:
        if turn.thread_id not in {item.thread_id for item in source_records}:
            raise ValueError("turn context is outside the source record authority")
        if turn.metadata.get("cwd"):
            turn_context[turn.id] = (
                "cwd-" + canonical_sha256(str(turn.metadata["cwd"])).removeprefix("sha256:")[:32]
            )
    call_types: dict[tuple[str, str, str], str] = {}
    for item in source_records:
        call_id = item.metadata.get("call_id")
        if not call_id:
            continue
        item_type = ObservableItemType(item.item_type)
        if item_type is ObservableItemType.PATCH:
            call_types[(item.thread_id, item.turn_id, str(call_id))] = "patch_apply"
        elif item_type is ObservableItemType.COMMAND_EXECUTION:
            call_types[(item.thread_id, item.turn_id, str(call_id))] = "command"
        elif item_type is ObservableItemType.TOOL_CALL:
            call_types[(item.thread_id, item.turn_id, str(call_id))] = str(
                item.metadata.get("tool_name") or "tool"
            ).casefold()

    by_thread: dict[str, list[ObservableCodexItem]] = defaultdict(list)
    skipped_episode_count = 0
    synthetic_result_count = 0
    for item in source_records:
        if item.item_type == "DevelopmentEpisode":
            skipped_episode_count += 1
            continue
        item_type = ObservableItemType(item.item_type)
        scope = EventScope(
            project_id=project_id,
            source_id=item.source_id,
            generation_id=item.generation_id,
            thread_id=item.thread_id,
            turn_id=item.turn_id,
            acl_ref=item.acl_ref,
        )
        raw_item_id = _observable_item_id(item.id)
        observable_order = (_adapter_event_number(item.source_locator) - 1) * 2
        metadata_call_id = item.metadata.get("call_id")
        call_id = str(metadata_call_id) if metadata_call_id else None
        synthetic_direct_result = (
            item_type is ObservableItemType.COMMAND_EXECUTION
            and item.metadata.get("source_event_type") == "command_execution"
            and type(item.metadata.get("exit_code")) is int
        )
        if synthetic_direct_result and call_id is None:
            call_id = f"adapter-command-{item.item_id}"
        values: dict[str, Any] = {
            "item_id": raw_item_id,
            "source_locator": canonical_codex_locator_v1(
                scope,
                raw_item_id,
                observable_order,
            ),
            "scope": scope,
            "observable_order": observable_order,
            "item_type": item_type,
            "role": item.role,
            "call_id": call_id,
            "targets": tuple(str(value) for value in item.metadata.get("paths", ()) if value),
        }
        initial_payload: dict[str, Any] = {}
        if item.timestamp:
            initial_payload["event_at"] = item.timestamp
        if item.turn_id in turn_context:
            initial_payload["cwd_identity"] = turn_context[item.turn_id]
        initial_payload["project_identity"] = project_id
        values["payload"] = initial_payload
        if item_type is ObservableItemType.COMMAND_EXECUTION:
            values["call_type"] = "command"
            values["command_argv"] = _command_argv(item.content)
            values["target_project_id"] = project_id
        elif item_type is ObservableItemType.PATCH:
            values["call_type"] = "patch_apply"
            values["tool_name"] = "apply_patch"
        elif item_type is ObservableItemType.TOOL_CALL:
            values["call_type"] = str(item.metadata.get("tool_name") or "tool").casefold()
            values["tool_name"] = values["call_type"]
        elif item_type in _RESULT_TYPES:
            exact_call_type = call_types.get((item.thread_id, item.turn_id, call_id or ""))
            if exact_call_type is not None:
                values["call_type"] = exact_call_type
            payload = dict(values.get("payload", {}))
            payload["output"] = item.content
            exit_code = item.metadata.get("exit_code")
            if type(exit_code) is int:
                payload["exit_code"] = exit_code
            values["payload"] = payload
        elif item_type is ObservableItemType.FILE_CHANGE:
            success = item.metadata.get("success")
            if type(success) is bool:
                values["success"] = success
        elif item_type in _TEXT_TYPES:
            payload = dict(values.get("payload", {}))
            payload["text"] = item.content
            values["payload"] = payload
        if item.status and item.status.casefold() in _KNOWN_OUTCOMES:
            values["outcome"] = item.status.casefold()
        observable = ObservableCodexItem(**values)
        by_thread[item.thread_id].append(observable)

        if synthetic_direct_result:
            result_id = f"CommandResult:{item.item_id}"
            payload = {
                "exit_code": item.metadata["exit_code"],
                "output": "\n".join(item.content.splitlines()[1:]),
            }
            if item.timestamp:
                payload["event_at"] = item.timestamp
            result = ObservableCodexItem(
                item_id=result_id,
                source_locator=canonical_codex_locator_v1(
                    scope,
                    result_id,
                    observable_order + 1,
                ),
                scope=scope,
                observable_order=observable_order + 1,
                item_type=ObservableItemType.COMMAND_RESULT,
                call_id=call_id,
                call_type="command",
                payload=payload,
            )
            by_thread[item.thread_id].append(result)
            synthetic_result_count += 1

    thread_sets: list[CodexObservableThreadV2] = []
    for thread_id, items in sorted(by_thread.items()):
        values = {
            "thread_id": thread_id,
            "source_item_ids": tuple(item.item_id for item in items),
            "observable_items": tuple(items),
        }
        thread_sets.append(
            CodexObservableThreadV2(
                **values,
                content_sha256=canonical_sha256(
                    {
                        key: (
                            [item.model_dump(mode="json") for item in value]
                            if key == "observable_items"
                            else value
                        )
                        for key, value in values.items()
                    }
                ),
            )
        )
    if not thread_sets:
        raise ValueError("production adapter records contain no observable items")
    source_set_sha256 = canonical_sha256(
        [
            {
                "id": item.id,
                "item_id": item.item_id,
                "source_locator": item.source_locator,
                "content_sha256": canonical_sha256(item.content),
                "metadata_sha256": canonical_sha256(item.metadata),
            }
            for item in source_records
        ]
    )
    values = {
        "project_id": project_id,
        "thread_sets": tuple(thread_sets),
        "source_record_count": len(source_records),
        "observable_item_count": sum(len(item.observable_items) for item in thread_sets),
        "skipped_episode_count": skipped_episode_count,
        "synthetic_result_count": synthetic_result_count,
        "source_set_sha256": source_set_sha256,
        "turn_context_sha256": canonical_sha256(turn_context),
        "adapter_version": CODEX_OBSERVABLE_ADAPTER_VERSION,
    }
    return CodexObservableAdapterResultV2(
        **values,
        result_sha256=canonical_sha256(
            {
                key: (
                    [item.model_dump(mode="json") for item in value]
                    if key == "thread_sets"
                    else value
                )
                for key, value in values.items()
            }
        ),
    )


__all__ = [
    "CODEX_OBSERVABLE_ADAPTER_VERSION",
    "CodexObservableAdapterResultV2",
    "CodexObservableThreadV2",
    "adapt_production_codex_items_v2",
]
