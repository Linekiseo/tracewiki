"""Versioned Codex retrieval units and observable temporal/event graph."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    ActionState,
    CodexEventNormalizationResult,
    NormalizedActionEvent,
    NormalizedEvent,
    NormalizedPatchEvent,
    NormalizedValidationEvent,
    ObservableCodexItem,
    ObservableItemType,
    ObservationKind,
    PatchState,
    ValidationState,
    canonical_sha256,
)
from .episode_v2 import (
    CODEX_EPISODE_CONTRACT_VERSION,
    CodexEpisodeBuildResult,
    CodexEpisodeVersion,
    build_codex_episodes_v2,
)

CODEX_RETRIEVAL_UNIT_CONTRACT_VERSION = "codex-retrieval-unit-contract-v2"
CODEX_RETRIEVAL_UNIT_BUILDER_VERSION = "codex-retrieval-unit-builder-v2"
CODEX_TEMPORAL_GRAPH_VERSION = "codex-temporal-event-graph-v2"

_SECRET_RE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|secret)"
    r"\s*[:=]\s*[^\s\"']{8,}))"
)
_ABSOLUTE_RE = re.compile(
    r"(?:^|[\s\"'=])(?:/(?:Users|home|private|var|tmp|etc)(?:/|\b)|"
    r"[A-Za-z]:[\\/]|[\\/]{2}[^\\/\s]+[\\/][^\\/\s]+)",
    re.IGNORECASE,
)


class _FrozenUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class CodexUnitRole(StrEnum):
    THREAD = "thread"
    EPISODE = "episode"
    GOAL = "goal"
    DECISION = "decision"
    ACTION = "action"
    CHANGE = "change"
    VALIDATION = "validation"
    FAILURE = "failure"
    PLAN = "plan"
    OUTCOME = "outcome"


class CodexEvidenceLevel(StrEnum):
    OBSERVED = "observed"
    CANDIDATE_ONLY = "candidate_only"


class CodexTemporalEdgeType(StrEnum):
    FOLLOWS = "follows"
    EXECUTES = "executes"
    OUTPUT_OF = "output_of"
    APPLIES_CHANGE = "applies_change"
    VALIDATES = "validates"
    RETRIES = "retries"
    SUPERSEDES = "supersedes"


class CodexRetrievalUnitV2(_FrozenUnit):
    unit_id: str
    role: CodexUnitRole
    evidence_level: CodexEvidenceLevel
    project_id: str
    source_id: str
    generation_id: str
    thread_id: str
    acl_ref: str
    episode_id: str | None
    episode_version_id: str | None
    source_item_ids: tuple[str, ...] = Field(min_length=1)
    source_item_sha256s: tuple[str, ...] = Field(min_length=1)
    source_locators: tuple[str, ...] = Field(min_length=1)
    event_ids: tuple[str, ...] = ()
    command_argv: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    state: str
    search_text: str = Field(min_length=1, max_length=16_384)
    ordinal: int = Field(ge=0)
    content_sha256: str
    builder_version: str = CODEX_RETRIEVAL_UNIT_BUILDER_VERSION
    contract_version: str = CODEX_RETRIEVAL_UNIT_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexRetrievalUnitV2:
        cardinality = {
            len(self.source_item_ids),
            len(self.source_item_sha256s),
            len(self.source_locators),
        }
        if len(cardinality) != 1:
            raise ValueError("retrieval unit source identity is incomplete")
        if self.source_item_ids != tuple(
            dict.fromkeys(self.source_item_ids)
        ) or self.event_ids != tuple(sorted(set(self.event_ids))):
            raise ValueError("retrieval unit references must be stable and unique")
        if self.targets != tuple(sorted(set(self.targets))) or self.tool_names != tuple(
            sorted(set(self.tool_names))
        ):
            raise ValueError("retrieval unit fielded metadata must be sorted and unique")
        payload = self.model_dump(
            mode="json",
            exclude={"unit_id", "content_sha256"},
        )
        content = canonical_sha256(payload)
        expected_id = "codexunit-" + canonical_sha256(
            {
                "content_sha256": content,
                "builder_version": self.builder_version,
            }
        ).removeprefix("sha256:")
        if self.content_sha256 != content or self.unit_id != expected_id:
            raise ValueError("retrieval unit identity mismatch")
        if (
            self.role in {CodexUnitRole.PLAN, CodexUnitRole.DECISION}
            and self.evidence_level is not CodexEvidenceLevel.CANDIDATE_ONLY
        ):
            raise ValueError("plan and decision units cannot claim observed truth")
        return self


class CodexTemporalEdgeV2(_FrozenUnit):
    edge_id: str
    edge_type: CodexTemporalEdgeType
    from_unit_id: str
    to_unit_id: str
    project_id: str
    source_id: str
    generation_id: str
    thread_id: str
    acl_ref: str
    confidence: float = Field(ge=0, le=1)
    evidence_item_ids: tuple[str, ...] = Field(min_length=1)
    content_sha256: str
    graph_version: str = CODEX_TEMPORAL_GRAPH_VERSION
    contract_version: str = CODEX_RETRIEVAL_UNIT_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexTemporalEdgeV2:
        if self.from_unit_id == self.to_unit_id:
            raise ValueError("temporal edge cannot be a self-loop")
        if self.evidence_item_ids != tuple(sorted(set(self.evidence_item_ids))):
            raise ValueError("temporal edge evidence must be sorted and unique")
        payload = self.model_dump(
            mode="json",
            exclude={"edge_id", "content_sha256"},
        )
        content = canonical_sha256(payload)
        expected = "codexedge-" + canonical_sha256(
            {"content_sha256": content, "graph_version": self.graph_version}
        ).removeprefix("sha256:")
        if self.content_sha256 != content or self.edge_id != expected:
            raise ValueError("temporal edge identity mismatch")
        return self


class CodexRetrievalPublicationV2(_FrozenUnit):
    source_set_sha256: str
    normalization_sha256: str
    episode_result_sha256: str
    project_id: str
    source_id: str
    generation_id: str
    thread_id: str
    acl_ref: str
    units: tuple[CodexRetrievalUnitV2, ...]
    edges: tuple[CodexTemporalEdgeV2, ...]
    reasoning_units: int = Field(default=0, ge=0, le=0)
    publication_sha256: str
    builder_version: str = CODEX_RETRIEVAL_UNIT_BUILDER_VERSION
    graph_version: str = CODEX_TEMPORAL_GRAPH_VERSION
    episode_contract_version: str = CODEX_EPISODE_CONTRACT_VERSION
    contract_version: str = CODEX_RETRIEVAL_UNIT_CONTRACT_VERSION

    @model_validator(mode="after")
    def _identity(self) -> CodexRetrievalPublicationV2:
        unit_ids = {item.unit_id for item in self.units}
        if len(unit_ids) != len(self.units):
            raise ValueError("retrieval publication contains duplicate units")
        for edge in self.edges:
            if edge.from_unit_id not in unit_ids or edge.to_unit_id not in unit_ids:
                raise ValueError("temporal edge endpoint is not published")
            if (
                edge.project_id,
                edge.source_id,
                edge.generation_id,
                edge.thread_id,
                edge.acl_ref,
            ) != (
                self.project_id,
                self.source_id,
                self.generation_id,
                self.thread_id,
                self.acl_ref,
            ):
                raise ValueError("temporal edge crosses publication authority")
        expected = canonical_sha256(self.model_dump(mode="json", exclude={"publication_sha256"}))
        if self.publication_sha256 != expected:
            raise ValueError("retrieval publication digest mismatch")
        return self


def _safe_search_text(items: tuple[ObservableCodexItem, ...], state: str) -> str:
    parts: list[str] = []
    for item in items:
        parts.extend(
            (
                item.item_type.value,
                item.call_type or "",
                item.tool_name or "",
                " ".join(item.command_argv),
                " ".join(item.targets),
            )
        )
        for key in ("text", "content", "message", "summary", "goal", "decision", "plan"):
            value = item.payload.get(key)
            if isinstance(value, str):
                parts.append(value)
    parts.append(state)
    value = unicodedata.normalize("NFC", " ".join(part for part in parts if part))
    value = " ".join(value.split())
    if not value:
        value = "observable"
    if _SECRET_RE.search(value) or _ABSOLUTE_RE.search(value):
        raise ValueError("retrieval unit searchable text contains private material")
    return value[:16_384]


def _unit(
    *,
    role: CodexUnitRole,
    evidence_level: CodexEvidenceLevel,
    episode: CodexEpisodeVersion | None,
    items: tuple[ObservableCodexItem, ...],
    event_ids: tuple[str, ...] = (),
    state: str,
    ordinal: int,
) -> CodexRetrievalUnitV2:
    first = items[0]
    payload: dict[str, Any] = {
        "role": role,
        "evidence_level": evidence_level,
        "project_id": first.scope.project_id,
        "source_id": first.scope.source_id,
        "generation_id": first.scope.generation_id,
        "thread_id": first.scope.thread_id,
        "acl_ref": first.scope.acl_ref,
        "episode_id": episode.episode_id if episode else None,
        "episode_version_id": episode.episode_version_id if episode else None,
        "source_item_ids": tuple(item.item_id for item in items),
        "source_item_sha256s": tuple(item.canonical_sha256() for item in items),
        "source_locators": tuple(item.source_locator for item in items),
        "event_ids": tuple(sorted(set(event_ids))),
        "command_argv": tuple(value for item in items for value in item.command_argv),
        "targets": tuple(sorted({value for item in items for value in item.targets})),
        "tool_names": tuple(
            sorted(
                {
                    value
                    for item in items
                    for value in (item.tool_name, item.call_type)
                    if value is not None
                }
            )
        ),
        "state": state,
        "search_text": _safe_search_text(items, state),
        "ordinal": ordinal,
        "builder_version": CODEX_RETRIEVAL_UNIT_BUILDER_VERSION,
        "contract_version": CODEX_RETRIEVAL_UNIT_CONTRACT_VERSION,
    }
    content = canonical_sha256(payload)
    unit_id = "codexunit-" + canonical_sha256(
        {
            "content_sha256": content,
            "builder_version": CODEX_RETRIEVAL_UNIT_BUILDER_VERSION,
        }
    ).removeprefix("sha256:")
    return CodexRetrievalUnitV2(
        unit_id=unit_id,
        content_sha256=content,
        **payload,
    )


def _edge(
    edge_type: CodexTemporalEdgeType,
    source: CodexRetrievalUnitV2,
    target: CodexRetrievalUnitV2,
    evidence_item_ids: Iterable[str],
    *,
    confidence: float,
) -> CodexTemporalEdgeV2:
    evidence = tuple(sorted(set(evidence_item_ids)))
    payload = {
        "edge_type": edge_type,
        "from_unit_id": source.unit_id,
        "to_unit_id": target.unit_id,
        "project_id": source.project_id,
        "source_id": source.source_id,
        "generation_id": source.generation_id,
        "thread_id": source.thread_id,
        "acl_ref": source.acl_ref,
        "confidence": confidence,
        "evidence_item_ids": evidence,
        "graph_version": CODEX_TEMPORAL_GRAPH_VERSION,
        "contract_version": CODEX_RETRIEVAL_UNIT_CONTRACT_VERSION,
    }
    content = canonical_sha256(payload)
    edge_id = "codexedge-" + canonical_sha256(
        {"content_sha256": content, "graph_version": CODEX_TEMPORAL_GRAPH_VERSION}
    ).removeprefix("sha256:")
    return CodexTemporalEdgeV2(
        edge_id=edge_id,
        content_sha256=content,
        **payload,
    )


def _event_role(event: NormalizedEvent) -> tuple[CodexUnitRole, str]:
    if isinstance(event, NormalizedActionEvent):
        if event.state in {ActionState.FAILED, ActionState.TIMEOUT, ActionState.CANCELLED}:
            return CodexUnitRole.FAILURE, str(event.state)
        return CodexUnitRole.ACTION, str(event.state)
    if isinstance(event, NormalizedPatchEvent):
        if event.state is PatchState.FAILED:
            return CodexUnitRole.FAILURE, str(event.state)
        return CodexUnitRole.CHANGE, str(event.state)
    if isinstance(event, NormalizedValidationEvent):
        if event.state is ValidationState.FAILED:
            return CodexUnitRole.FAILURE, str(event.state)
        return CodexUnitRole.VALIDATION, str(event.state)
    if event.observation is ObservationKind.PLAN_ONLY:
        return CodexUnitRole.PLAN, str(event.observation)
    if event.observation is ObservationKind.ASSISTANT_CLAIM_ONLY:
        return CodexUnitRole.DECISION, str(event.observation)
    return CodexUnitRole.OUTCOME, str(event.observation)


def build_codex_retrieval_publication_v2(
    source_items: Iterable[ObservableCodexItem],
    normalization: CodexEventNormalizationResult,
    episode_result: CodexEpisodeBuildResult,
    *,
    manual_overrides: Mapping[str, str] | None = None,
    gap_seconds: int = 1_800,
) -> CodexRetrievalPublicationV2:
    """Build source-bound retrieval units without preserving raw or reasoning text."""

    items = tuple(source_items)
    rebuilt = build_codex_episodes_v2(
        items,
        normalization,
        manual_overrides=manual_overrides,
        gap_seconds=gap_seconds,
    )
    if rebuilt != episode_result:
        raise ValueError("episode result is not the exact source-derived publication")
    item_by_id = {item.item_id: item for item in items}
    events_by_item: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in normalization.events:
        for evidence in event.evidence:
            if evidence.source_item_id not in item_by_id:
                raise ValueError("normalized event references unknown source evidence")
            events_by_item[evidence.source_item_id].append(event)

    units: list[CodexRetrievalUnitV2] = []
    ordinal = 0
    visible_items = tuple(
        item_by_id[item_id] for episode in episode_result.episodes for item_id in episode.member_ids
    )
    units.append(
        _unit(
            role=CodexUnitRole.THREAD,
            evidence_level=CodexEvidenceLevel.OBSERVED,
            episode=None,
            items=visible_items,
            state="projected",
            ordinal=ordinal,
        )
    )
    ordinal += 1
    for episode in episode_result.episodes:
        episode_items = tuple(item_by_id[item_id] for item_id in episode.member_ids)
        units.append(
            _unit(
                role=CodexUnitRole.EPISODE,
                evidence_level=CodexEvidenceLevel.OBSERVED,
                episode=episode,
                items=episode_items,
                state=episode.terminal_state,
                ordinal=ordinal,
            )
        )
        ordinal += 1
        for item in episode_items:
            if item.item_type is ObservableItemType.USER_GOAL:
                specs = [(CodexUnitRole.GOAL, "observed", ())]
            else:
                linked_events = events_by_item.get(item.item_id, [])
                specs = [(*_event_role(event), (event.event_id,)) for event in linked_events]
                if not specs and item.item_type is ObservableItemType.PLAN:
                    specs = [(CodexUnitRole.PLAN, "plan_only", ())]
                elif not specs and item.item_type is ObservableItemType.AGENT_MESSAGE:
                    specs = [(CodexUnitRole.DECISION, "claim_only", ())]
                elif not specs and item.item_type in {
                    ObservableItemType.TOOL_RESULT,
                    ObservableItemType.COMMAND_RESULT,
                    ObservableItemType.PATCH_RESULT,
                    ObservableItemType.FILE_CHANGE,
                }:
                    specs = [(CodexUnitRole.OUTCOME, "unlinked", ())]
            seen_specs: set[tuple[CodexUnitRole, str, tuple[str, ...]]] = set()
            for role, state, event_ids in specs:
                spec = (role, state, event_ids)
                if spec in seen_specs:
                    continue
                seen_specs.add(spec)
                level = (
                    CodexEvidenceLevel.CANDIDATE_ONLY
                    if role in {CodexUnitRole.PLAN, CodexUnitRole.DECISION}
                    else CodexEvidenceLevel.OBSERVED
                )
                units.append(
                    _unit(
                        role=role,
                        evidence_level=level,
                        episode=episode,
                        items=(item,),
                        event_ids=event_ids,
                        state=state,
                        ordinal=ordinal,
                    )
                )
                ordinal += 1

    unit_by_item: dict[str, list[CodexRetrievalUnitV2]] = defaultdict(list)
    for unit in units:
        if unit.role not in {CodexUnitRole.THREAD, CodexUnitRole.EPISODE}:
            for item_id in unit.source_item_ids:
                unit_by_item[item_id].append(unit)
    edges: list[CodexTemporalEdgeV2] = []
    sequential = [
        unit for unit in units if unit.role not in {CodexUnitRole.THREAD, CodexUnitRole.EPISODE}
    ]
    for source, target in zip(sequential, sequential[1:], strict=False):
        edges.append(
            _edge(
                CodexTemporalEdgeType.FOLLOWS,
                source,
                target,
                (*source.source_item_ids, *target.source_item_ids),
                confidence=1.0,
            )
        )
    goals = [unit for unit in sequential if unit.role is CodexUnitRole.GOAL]
    for unit in sequential:
        if unit.role is CodexUnitRole.ACTION and goals:
            goal = next(
                (
                    candidate
                    for candidate in reversed(goals)
                    if candidate.ordinal < unit.ordinal and candidate.episode_id == unit.episode_id
                ),
                None,
            )
            if goal:
                edges.append(
                    _edge(
                        CodexTemporalEdgeType.EXECUTES,
                        goal,
                        unit,
                        (*goal.source_item_ids, *unit.source_item_ids),
                        confidence=0.9,
                    )
                )
    for link in normalization.links:
        calls = unit_by_item.get(link.call_item_id, [])
        results = unit_by_item.get(link.result_item_id, [])
        if calls and results:
            edges.append(
                _edge(
                    CodexTemporalEdgeType.OUTPUT_OF,
                    calls[0],
                    results[0],
                    (link.call_item_id, link.result_item_id),
                    confidence=1.0,
                )
            )
    changes = [unit for unit in sequential if unit.role is CodexUnitRole.CHANGE]
    validations = [unit for unit in sequential if unit.role is CodexUnitRole.VALIDATION]
    failures = [unit for unit in sequential if unit.role is CodexUnitRole.FAILURE]
    actions = [unit for unit in sequential if unit.role is CodexUnitRole.ACTION]
    decisions = [unit for unit in sequential if unit.role is CodexUnitRole.DECISION]
    for change in changes:
        predecessor = max(
            (
                unit
                for unit in actions
                if unit.ordinal < change.ordinal
                and unit.episode_id == change.episode_id
                and (
                    not unit.targets
                    or not change.targets
                    or bool(set(unit.targets) & set(change.targets))
                )
            ),
            key=lambda item: item.ordinal,
            default=None,
        )
        if predecessor:
            edges.append(
                _edge(
                    CodexTemporalEdgeType.APPLIES_CHANGE,
                    predecessor,
                    change,
                    (*predecessor.source_item_ids, *change.source_item_ids),
                    confidence=0.8,
                )
            )
    for validation in validations:
        predecessor = max(
            (
                unit
                for unit in (*actions, *changes)
                if unit.ordinal < validation.ordinal
                and unit.episode_id == validation.episode_id
                and (
                    not validation.targets
                    or not unit.targets
                    or bool(set(validation.targets) & set(unit.targets))
                )
            ),
            key=lambda item: item.ordinal,
            default=None,
        )
        if predecessor:
            edges.append(
                _edge(
                    CodexTemporalEdgeType.VALIDATES,
                    predecessor,
                    validation,
                    (*predecessor.source_item_ids, *validation.source_item_ids),
                    confidence=0.8,
                )
            )
    for failure in failures:
        retry = min(
            (unit for unit in actions if unit.ordinal > failure.ordinal),
            key=lambda item: item.ordinal,
            default=None,
        )
        if retry:
            edges.append(
                _edge(
                    CodexTemporalEdgeType.RETRIES,
                    failure,
                    retry,
                    (*failure.source_item_ids, *retry.source_item_ids),
                    confidence=0.75,
                )
            )
    for prior, later in zip(decisions, decisions[1:], strict=False):
        edges.append(
            _edge(
                CodexTemporalEdgeType.SUPERSEDES,
                prior,
                later,
                (*prior.source_item_ids, *later.source_item_ids),
                confidence=0.5,
            )
        )
    deduplicated_edges = tuple(
        sorted({edge.edge_id: edge for edge in edges}.values(), key=lambda item: item.edge_id)
    )
    first = items[0]
    values = {
        "source_set_sha256": episode_result.source_set_sha256,
        "normalization_sha256": episode_result.normalization_sha256,
        "episode_result_sha256": episode_result.result_sha256,
        "project_id": first.scope.project_id,
        "source_id": first.scope.source_id,
        "generation_id": first.scope.generation_id,
        "thread_id": first.scope.thread_id,
        "acl_ref": first.scope.acl_ref,
        "units": tuple(units),
        "edges": deduplicated_edges,
        "reasoning_units": 0,
        "builder_version": CODEX_RETRIEVAL_UNIT_BUILDER_VERSION,
        "graph_version": CODEX_TEMPORAL_GRAPH_VERSION,
        "episode_contract_version": CODEX_EPISODE_CONTRACT_VERSION,
        "contract_version": CODEX_RETRIEVAL_UNIT_CONTRACT_VERSION,
    }
    return CodexRetrievalPublicationV2(
        **values,
        publication_sha256=canonical_sha256(
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
    "CODEX_RETRIEVAL_UNIT_BUILDER_VERSION",
    "CODEX_RETRIEVAL_UNIT_CONTRACT_VERSION",
    "CODEX_TEMPORAL_GRAPH_VERSION",
    "CodexEvidenceLevel",
    "CodexRetrievalPublicationV2",
    "CodexRetrievalUnitV2",
    "CodexTemporalEdgeType",
    "CodexTemporalEdgeV2",
    "CodexUnitRole",
    "build_codex_retrieval_publication_v2",
]
