"""Legal Workspace transitions, snapshots, and deterministic as-of projection."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from .contracts import (
    WORKSPACE_POLICY_VERSION,
    WorkspaceAuthority,
    WorkspaceEntity,
    WorkspaceEntityType,
    WorkspaceScope,
    build_workspace_entity,
    canonical_sha256,
    workspace_locator,
)

WORKSPACE_STATE_VERSION = "workspace-state-v2"
WORK_ITEM_TRANSITIONS = {
    "backlog": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"running", "blocked", "cancelled"}),
    "running": frozenset({"review", "blocked", "cancelled"}),
    "blocked": frozenset({"ready", "running", "cancelled"}),
    "review": frozenset({"running", "done", "blocked"}),
    "done": frozenset({"running"}),
    "cancelled": frozenset({"backlog"}),
}
TOPIC_TRANSITIONS = {
    "backlog": frozenset({"active", "archived"}),
    "active": frozenset({"blocked", "completed", "archived"}),
    "blocked": frozenset({"active", "archived"}),
    "completed": frozenset({"active", "archived"}),
    "archived": frozenset(),
}
ITERATION_TRANSITIONS = {
    "planned": frozenset({"active", "blocked", "archived"}),
    "active": frozenset({"validating", "blocked", "archived"}),
    "validating": frozenset({"active", "completed", "blocked", "archived"}),
    "blocked": frozenset({"active", "validating", "archived"}),
    "completed": frozenset({"active", "archived"}),
    "archived": frozenset(),
}


class _FrozenState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkspaceTransitionResult(_FrozenState):
    entity: WorkspaceEntity
    transition: WorkspaceEntity


class WorkspaceAsOfProjection(_FrozenState):
    as_of: str
    status_by_entity: dict[str, str]
    applied_transition_ids: tuple[str, ...]
    state_sha256: str
    exact: bool
    diagnostics: tuple[str, ...]


def parse_workspace_timestamp(value: object, *, label: str) -> datetime:
    """Parse one strict, timezone-aware ISO timestamp and normalize it to UTC."""

    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label}_invalid")
    candidate = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{label}_invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label}_timezone_required")
    try:
        return parsed.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{label}_invalid") from error


def workspace_timestamp(value: object, *, label: str) -> str:
    """Return a canonical UTC ISO timestamp suitable for comparisons and traces."""

    return parse_workspace_timestamp(value, label=label).isoformat().replace("+00:00", "Z")


def _matrix(entity_type: WorkspaceEntityType) -> dict[str, frozenset[str]]:
    if entity_type == WorkspaceEntityType.WORK_ITEM:
        return WORK_ITEM_TRANSITIONS
    if entity_type == WorkspaceEntityType.TOPIC:
        return TOPIC_TRANSITIONS
    if entity_type == WorkspaceEntityType.ITERATION:
        return ITERATION_TRANSITIONS
    raise ValueError("entity type does not support state transitions")


def transition_workspace_entity(
    entity: WorkspaceEntity,
    *,
    target_status: str,
    expected_version: int,
    actor: str,
    event_at: str,
    effective_at: str,
    reason: str,
    done_gate_passed: bool = False,
    restore_approved: bool = False,
) -> WorkspaceTransitionResult:
    if entity.version != expected_version:
        raise ValueError("Workspace optimistic concurrency mismatch")
    if target_status not in _matrix(entity.entity_type).get(entity.status, frozenset()):
        raise ValueError("illegal Workspace state transition")
    if entity.status == "review" and target_status == "done" and not done_gate_passed:
        raise ValueError("WorkItem done transition requires a passed acceptance gate")
    if entity.status == "done" and target_status == "running" and not reason.strip():
        raise ValueError("WorkItem reopen requires a reason")
    if entity.status == "cancelled" and target_status == "backlog" and not restore_approved:
        raise ValueError("cancelled WorkItem restore requires approval")
    next_version = entity.version + 1
    locator = workspace_locator(
        scope=entity.scope,
        entity_type=entity.entity_type,
        stable_id=entity.stable_id,
        version=next_version,
    )
    updated = build_workspace_entity(
        **entity.model_dump(
            mode="python",
            exclude={
                "content_sha256",
                "version",
                "status",
                "effective_at",
                "locator",
                "metadata",
            },
        ),
        version=next_version,
        status=target_status,
        effective_at=effective_at,
        locator=locator,
        metadata={
            **entity.metadata,
            "previous_entity_id": entity.entity_id,
            "previous_content_sha256": entity.content_sha256,
        },
    )
    stable_id = f"transition-{entity.stable_id}-{next_version}"
    transition = build_workspace_entity(
        entity_id=stable_id,
        stable_id=stable_id,
        entity_type=WorkspaceEntityType.TRANSITION,
        scope=entity.scope,
        version=1,
        display_key=f"TRANS-{entity.display_key}-{next_version}",
        label=f"{entity.status} → {target_status}",
        text=reason,
        status="applied",
        authority=WorkspaceAuthority.AUTHORITATIVE,
        parent_id=entity.entity_id,
        topic_id=entity.topic_id,
        iteration_id=entity.iteration_id,
        work_item_id=entity.entity_id
        if entity.entity_type == WorkspaceEntityType.WORK_ITEM
        else entity.work_item_id,
        owner=actor,
        effective_at=effective_at,
        locator=workspace_locator(
            scope=entity.scope,
            entity_type=WorkspaceEntityType.TRANSITION,
            stable_id=stable_id,
            version=1,
        ),
        source_sha256=canonical_sha256(
            {
                "entity_id": entity.entity_id,
                "before": entity.status,
                "after": target_status,
                "event_at": event_at,
                "effective_at": effective_at,
                "actor": actor,
                "reason": reason,
            }
        ),
        metadata={
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "before": entity.status,
            "after": target_status,
            "expected_version": expected_version,
            "resulting_version": next_version,
            "actor": actor,
            "event_at": event_at,
            "effective_at": effective_at,
            "reason": reason,
            "state_version": WORKSPACE_STATE_VERSION,
        },
    )
    return WorkspaceTransitionResult(entity=updated, transition=transition)


def project_workspace_as_of(
    initial_entities: Iterable[WorkspaceEntity],
    transitions: Iterable[WorkspaceEntity],
    *,
    as_of: str,
) -> WorkspaceAsOfProjection:
    cutoff = parse_workspace_timestamp(as_of, label="as_of")
    normalized_as_of = cutoff.isoformat().replace("+00:00", "Z")
    states: dict[str, str] = {}
    initial_effective: dict[str, datetime] = {}
    applied: list[str] = []
    diagnostics: list[str] = []
    for item in initial_entities:
        effective = parse_workspace_timestamp(
            item.effective_at,
            label="initial_entity_effective_at",
        )
        if effective > cutoff:
            continue
        if item.entity_id in states:
            diagnostics.append(f"duplicate-initial:{item.entity_id}")
            continue
        states[item.entity_id] = item.status
        initial_effective[item.entity_id] = effective
    parsed_transitions = tuple(
        (
            parse_workspace_timestamp(
                str(item.metadata.get("effective_at") or item.effective_at),
                label="transition_effective_at",
            ),
            item,
        )
        for item in transitions
    )
    ordered = sorted(
        parsed_transitions,
        key=lambda item: (item[0], item[1].entity_id),
    )
    for effective, transition in ordered:
        if effective > cutoff:
            continue
        target = str(transition.metadata.get("entity_id") or "")
        before = str(transition.metadata.get("before") or "")
        after = str(transition.metadata.get("after") or "")
        if not target or not before or not after:
            diagnostics.append(f"invalid-transition:{transition.entity_id}")
            continue
        if target not in states:
            diagnostics.append(f"missing-initial:{target}")
            continue
        if effective < initial_effective[target]:
            diagnostics.append(f"transition-before-initial:{transition.entity_id}")
            continue
        if states[target] != before:
            diagnostics.append(f"transition-chain-mismatch:{transition.entity_id}")
            continue
        states[target] = after
        applied.append(transition.entity_id)
    payload = {
        "as_of": normalized_as_of,
        "status_by_entity": dict(sorted(states.items())),
        "applied_transition_ids": tuple(applied),
        "policy_version": WORKSPACE_POLICY_VERSION,
    }
    return WorkspaceAsOfProjection(
        as_of=normalized_as_of,
        status_by_entity=payload["status_by_entity"],
        applied_transition_ids=tuple(applied),
        state_sha256=canonical_sha256(payload),
        exact=not diagnostics,
        diagnostics=tuple(diagnostics),
    )


def is_overdue(
    *,
    due_at: str | None,
    observed_at: str,
    project_timezone: str,
    terminal: bool,
) -> bool:
    if due_at is None or terminal:
        return False
    zone = ZoneInfo(project_timezone)
    due = datetime.fromisoformat(due_at.replace("Z", "+00:00")).astimezone(zone)
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).astimezone(zone)
    return observed > due


def build_workspace_snapshot(
    *,
    scope: WorkspaceScope,
    entities: Iterable[WorkspaceEntity],
    as_of: str,
    source_watermarks: dict[str, str],
) -> WorkspaceEntity:
    selected = tuple(
        sorted(
            (
                {
                    "entity_id": item.entity_id,
                    "version": item.version,
                    "status": item.status,
                    "content_sha256": item.content_sha256,
                }
                for item in entities
                if item.current and not item.archived
            ),
            key=lambda item: item["entity_id"],
        )
    )
    state_sha256 = canonical_sha256(
        {
            "as_of": as_of,
            "entities": selected,
            "source_watermarks": source_watermarks,
            "policy_version": WORKSPACE_POLICY_VERSION,
        }
    )
    stable_id = f"snapshot-{state_sha256.removeprefix('sha256:')[:20]}"
    return build_workspace_entity(
        entity_id=stable_id,
        stable_id=stable_id,
        entity_type=WorkspaceEntityType.SNAPSHOT,
        scope=scope,
        version=1,
        display_key=f"SNAP-{stable_id[-8:].upper()}",
        label=f"Workspace snapshot at {as_of}",
        text="Authoritative current state snapshot",
        status="current",
        authority=WorkspaceAuthority.AUTHORITATIVE,
        effective_at=as_of,
        locator=workspace_locator(
            scope=scope,
            entity_type=WorkspaceEntityType.SNAPSHOT,
            stable_id=stable_id,
            version=1,
        ),
        source_sha256=state_sha256,
        metadata={
            "as_of": as_of,
            "state_sha256": state_sha256,
            "entity_versions": selected,
            "source_watermarks": dict(sorted(source_watermarks.items())),
            "policy_version": WORKSPACE_POLICY_VERSION,
        },
    )


__all__ = [
    "ITERATION_TRANSITIONS",
    "TOPIC_TRANSITIONS",
    "WORKSPACE_STATE_VERSION",
    "WORK_ITEM_TRANSITIONS",
    "WorkspaceAsOfProjection",
    "WorkspaceTransitionResult",
    "build_workspace_snapshot",
    "is_overdue",
    "parse_workspace_timestamp",
    "project_workspace_as_of",
    "transition_workspace_entity",
    "workspace_timestamp",
]
