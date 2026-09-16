"""Structured, temporal, sparse, dense, graph, and reranked Workspace retrieval."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict, deque
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    WorkspaceAuthority,
    WorkspaceEdge,
    WorkspaceEntity,
    WorkspaceEntityType,
    WorkspaceScope,
    canonical_sha256,
)
from .state_v2 import parse_workspace_timestamp
from .store import WorkspaceV2Store

WORKSPACE_QUERY_PROFILE_VERSION = "workspace-query-profile-v2"
WORKSPACE_RETRIEVER_VERSION = "workspace-retriever-v2"
WORKSPACE_RERANKER_VERSION = "workspace-reranker-v2"
WORKSPACE_DENSE_PROFILE_VERSION = "workspace-local-hash-dense-v2"
_TOKEN_RE = re.compile(r"[\w@.+-]+", re.UNICODE)
_DISPLAY_KEY_RE = re.compile(
    r"\b(?:PROJECT|TOPIC|ITER|TASK|AC|CHECK|DEP|BLOCK|RISK|REQ|LINK|DEC|OUT|TRANS|SNAP|INTEL)-[A-Z0-9-]+\b",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(
    r"(?:status\s*[:=]\s*|current\s+|is\s+)"
    r"(backlog|ready|running|review|blocked|done|cancelled|active|validating|"
    r"archived|approved|superseded|open|resolved)\b",
    re.IGNORECASE,
)
_STOP = frozenset(
    {
        "a",
        "all",
        "and",
        "as",
        "at",
        "current",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "or",
        "show",
        "the",
        "to",
        "what",
        "which",
        "who",
        "why",
        "workspace",
    }
)


class WorkspaceQueryTask(StrEnum):
    LOCATE = "locate"
    CURRENT = "current"
    WORK = "work"
    BLOCKER = "blocker"
    COVERAGE = "coverage"
    INTELLIGENCE = "intelligence"
    DECISION = "decision"
    TEMPORAL = "temporal"


class _FrozenRetrieval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class WorkspaceQueryProfile(_FrozenRetrieval):
    task: WorkspaceQueryTask
    exact_keys: tuple[str, ...]
    query_tokens: tuple[str, ...]
    statuses: tuple[str, ...]
    include_history: bool
    as_of: str | None
    date_from: str | None = None
    date_to: str | None = None
    max_hops: int = Field(ge=0, le=3)
    profile_version: str = WORKSPACE_QUERY_PROFILE_VERSION


class WorkspaceCandidate(_FrozenRetrieval):
    entity_id: str
    entity_type: WorkspaceEntityType
    locator: str
    label: str
    status: str
    authority: WorkspaceAuthority
    score: float
    channels: tuple[str, ...]
    roles: tuple[str, ...]
    content_sha256: str
    source_entity_ids: tuple[str, ...]
    metadata: dict[str, Any]


class WorkspaceSearchResult(_FrozenRetrieval):
    scope: WorkspaceScope
    query_sha256: str
    profile: WorkspaceQueryProfile
    candidates: tuple[WorkspaceCandidate, ...]
    graph_edges: tuple[dict[str, Any], ...]
    ambiguous_scope: bool
    missing: tuple[str, ...]
    trace: dict[str, Any]


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in (item.casefold() for item in _TOKEN_RE.findall(value))
        if len(token) > 1 and token not in _STOP
    )


def _trigrams(value: str) -> set[str]:
    normalized = " ".join(_tokens(value))
    if len(normalized) < 3:
        return {normalized} if normalized else set()
    return {normalized[index : index + 3] for index in range(len(normalized) - 2)}


def _dense_similarity(query: str, text: str) -> float:
    left = _trigrams(query)
    right = _trigrams(text)
    if not left or not right:
        return 0.0
    return len(left & right) / math.sqrt(len(left) * len(right))


def workspace_query_profile(
    query: str,
    *,
    as_of: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> WorkspaceQueryProfile:
    lowered = query.casefold()
    task = WorkspaceQueryTask.CURRENT
    for candidate, markers in (
        (
            WorkspaceQueryTask.TEMPORAL,
            ("as-of", "as of", "transition", "history", "historical", "compare"),
        ),
        (WorkspaceQueryTask.DECISION, ("decision", "rationale", "decide", "why keep", "why use")),
        (
            WorkspaceQueryTask.INTELLIGENCE,
            ("next action", "readiness", "intelligence", "recommendation", "progress"),
        ),
        (WorkspaceQueryTask.COVERAGE, ("evidence", "coverage", "requirement", "missing", "stale")),
        (WorkspaceQueryTask.BLOCKER, ("blocker", "blocked", "dependency", "overdue", "risk")),
        (WorkspaceQueryTask.WORK, ("acceptance", "criterion", "check", "work item", "task-")),
        (WorkspaceQueryTask.LOCATE, ("open ", "list all", "named")),
    ):
        if any(marker in lowered for marker in markers):
            task = candidate
            break
    exact_keys = tuple(dict.fromkeys(item.upper() for item in _DISPLAY_KEY_RE.findall(query)))
    statuses = tuple(dict.fromkeys(item.casefold() for item in _STATUS_RE.findall(query)))
    include_history = (
        as_of is not None
        or date_from is not None
        or date_to is not None
        or any(
            marker in lowered
            for marker in ("including archived", "history", "historical", "superseded")
        )
    )
    return WorkspaceQueryProfile(
        task=task,
        exact_keys=exact_keys,
        query_tokens=tuple(dict.fromkeys(_tokens(query))),
        statuses=statuses,
        include_history=include_history,
        as_of=as_of,
        date_from=date_from,
        date_to=date_to,
        max_hops=2,
    )


def _task_types(task: WorkspaceQueryTask) -> frozenset[WorkspaceEntityType]:
    return {
        WorkspaceQueryTask.LOCATE: frozenset(WorkspaceEntityType),
        WorkspaceQueryTask.CURRENT: frozenset(
            {
                WorkspaceEntityType.PROJECT,
                WorkspaceEntityType.TOPIC,
                WorkspaceEntityType.ITERATION,
                WorkspaceEntityType.WORK_ITEM,
                WorkspaceEntityType.SNAPSHOT,
            }
        ),
        WorkspaceQueryTask.WORK: frozenset(
            {
                WorkspaceEntityType.WORK_ITEM,
                WorkspaceEntityType.ACCEPTANCE_CRITERION,
                WorkspaceEntityType.ACCEPTANCE_CHECK,
                WorkspaceEntityType.EVIDENCE_LINK,
            }
        ),
        WorkspaceQueryTask.BLOCKER: frozenset(
            {
                WorkspaceEntityType.WORK_ITEM,
                WorkspaceEntityType.DEPENDENCY,
                WorkspaceEntityType.BLOCKER,
                WorkspaceEntityType.RISK,
            }
        ),
        WorkspaceQueryTask.COVERAGE: frozenset(
            {
                WorkspaceEntityType.EVIDENCE_REQUIREMENT,
                WorkspaceEntityType.EVIDENCE_LINK,
                WorkspaceEntityType.WORK_ITEM,
            }
        ),
        WorkspaceQueryTask.INTELLIGENCE: frozenset(
            {
                WorkspaceEntityType.INTELLIGENCE_RUN,
                WorkspaceEntityType.SNAPSHOT,
                WorkspaceEntityType.BLOCKER,
                WorkspaceEntityType.EVIDENCE_REQUIREMENT,
                WorkspaceEntityType.ITERATION,
            }
        ),
        WorkspaceQueryTask.DECISION: frozenset(
            {
                WorkspaceEntityType.DECISION,
                WorkspaceEntityType.OUTCOME,
                WorkspaceEntityType.EVIDENCE_LINK,
            }
        ),
        WorkspaceQueryTask.TEMPORAL: frozenset(
            {
                WorkspaceEntityType.TRANSITION,
                WorkspaceEntityType.SNAPSHOT,
                WorkspaceEntityType.ITERATION,
                WorkspaceEntityType.WORK_ITEM,
                WorkspaceEntityType.DECISION,
            }
        ),
    }[task]


class WorkspaceRetrieverV2:
    def __init__(self, store: WorkspaceV2Store) -> None:
        self.store = store

    def search(
        self,
        *,
        scope: WorkspaceScope,
        query: str,
        limit: int = 10,
        as_of: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        allowed_acl_refs: tuple[str, ...] = (),
        enforce_acl: bool = False,
    ) -> WorkspaceSearchResult:
        profile = workspace_query_profile(
            query,
            as_of=as_of,
            date_from=date_from,
            date_to=date_to,
        )
        if not scope.permits(allowed_acl_refs, enforce_acl=enforce_acl):
            return self._empty(scope, query, profile, "project_acl_denied")
        unfiltered_entities = self.store.list_entities(
            scope,
            include_history=profile.include_history,
            allowed_acl_refs=allowed_acl_refs,
            enforce_acl=enforce_acl,
        )
        cutoff = parse_workspace_timestamp(as_of, label="as_of") if as_of is not None else None
        lower = (
            parse_workspace_timestamp(date_from, label="date_from")
            if date_from is not None
            else None
        )
        upper = parse_workspace_timestamp(date_to, label="date_to") if date_to is not None else None
        entities: tuple[WorkspaceEntity, ...] = tuple(
            entity
            for entity in unfiltered_entities
            if self._in_temporal_scope(
                entity,
                as_of=cutoff,
                date_from=lower,
                date_to=upper,
            )
        )
        units = {
            item.entity_id: item
            for item in self.store.list_units(
                scope,
                allowed_acl_refs=allowed_acl_refs,
                enforce_acl=enforce_acl,
            )
        }
        if profile.include_history:
            publication = self.store.active_publication(scope.project_id)
            publication_units = (
                {item.entity_id: item for item in publication.retrieval_units}
                if publication is not None
                else {}
            )
            units.update(
                {
                    item.entity_id: publication_units[item.entity_id]
                    for item in entities
                    if item.entity_id not in units and item.entity_id in publication_units
                }
            )
        entity_map = {item.entity_id: item for item in entities}
        candidates: dict[str, tuple[float, set[str], set[str]]] = {}
        query_token_set = set(profile.query_tokens)
        expected_types = _task_types(profile.task)
        for entity in entities:
            if (
                entity.entity_type == WorkspaceEntityType.EVIDENCE_LINK
                and enforce_acl
                and entity.metadata.get("external_acl_ref") not in set(allowed_acl_refs)
            ):
                continue
            unit = units.get(entity.entity_id)
            if unit is None:
                continue
            score = 0.0
            channels: set[str] = set()
            roles = {
                entity.authority.value,
                entity.entity_type.value,
                "current" if entity.current else "historical",
            }
            exact_values = {item.casefold() for item in unit.exact_terms}
            if any(key.casefold() in exact_values for key in profile.exact_keys):
                score += 20.0
                channels.add("exact")
            elif profile.exact_keys:
                continue
            label_match = entity.label.casefold() in query.casefold()
            if label_match:
                score += 12.0
                channels.add("exact_title")
            sparse_tokens = set(_tokens(unit.sparse_text))
            overlap = len(query_token_set & sparse_tokens)
            if overlap:
                score += overlap / max(1, len(query_token_set)) * 5.0
                channels.add("sparse")
            dense = _dense_similarity(query, unit.dense_text)
            if dense:
                score += dense * 2.0
                channels.add("dense")
            if entity.entity_type in expected_types:
                score += 2.5
                channels.add("task")
            if profile.statuses:
                if entity.status.casefold() not in profile.statuses:
                    continue
                score += 5.0
                channels.add("structured_status")
            if entity.current and not entity.archived:
                score += 0.8
                channels.add("current")
            elif not profile.include_history:
                continue
            if entity.authority == WorkspaceAuthority.AUTHORITATIVE:
                score += 0.6
            elif entity.authority == WorkspaceAuthority.DERIVED_INTELLIGENCE:
                score -= 0.2
            if entity.entity_type == WorkspaceEntityType.INTELLIGENCE_RUN and not entity.current:
                score -= 20.0
            if score > 2.5:
                candidates[entity.entity_id] = (score, channels, roles)

        edges = tuple(
            edge
            for edge in self.store.list_edges(
                scope,
                include_history=profile.include_history,
            )
            if edge.edge_type.value in self._task_edge_types(profile.task)
        )
        expanded = self._expand(
            candidate_ids=tuple(candidates),
            edges=edges,
            entity_map=entity_map,
            max_hops=profile.max_hops,
        )
        for entity_id, (distance, edge_types) in expanded.items():
            entity = entity_map.get(entity_id)
            if entity is None:
                continue
            base = candidates.get(entity_id, (0.0, set(), set()))
            score = max(base[0], 5.0 / max(1, distance))
            channels = {*base[1], "graph"}
            roles = {
                *base[2],
                entity.authority.value,
                entity.entity_type.value,
                *(f"edge:{item}" for item in edge_types),
            }
            candidates[entity_id] = (score, channels, roles)
        ranked = sorted(
            candidates.items(),
            key=lambda item: (
                -item[1][0],
                entity_map[item[0]].display_key,
                item[0],
            ),
        )
        selected: list[WorkspaceCandidate] = []
        for entity_id, (score, channels, roles) in ranked[:limit]:
            entity = entity_map[entity_id]
            selected.append(
                WorkspaceCandidate(
                    entity_id=entity.entity_id,
                    entity_type=entity.entity_type,
                    locator=entity.locator,
                    label=entity.label,
                    status=entity.status,
                    authority=entity.authority,
                    score=round(score, 8),
                    channels=tuple(sorted(channels)),
                    roles=tuple(sorted(roles)),
                    content_sha256=entity.content_sha256,
                    source_entity_ids=(entity.entity_id,),
                    metadata={
                        "display_key": entity.display_key,
                        "owner": entity.owner,
                        "assignee": entity.assignee,
                        "due_at": entity.due_at,
                        "effective_at": entity.effective_at,
                        "expires_at": entity.expires_at,
                        "topic_id": entity.topic_id,
                        "iteration_id": entity.iteration_id,
                        "work_item_id": entity.work_item_id,
                        "text": entity.text,
                        "entity_metadata": {
                            **entity.metadata,
                            "version": entity.version,
                        },
                    },
                )
            )
        exact_title_matches = {
            item.entity_id
            for item in entities
            if item.label.casefold() in query.casefold()
            and item.entity_type in {WorkspaceEntityType.TOPIC, WorkspaceEntityType.ITERATION}
        }
        graph_rows = tuple(
            {
                "edge_id": edge.edge_id,
                "edge_type": edge.edge_type,
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "reviewed": edge.reviewed,
            }
            for edge in edges
            if edge.source_id in {item.entity_id for item in selected}
            or edge.target_id in {item.entity_id for item in selected}
        )
        return WorkspaceSearchResult(
            scope=scope,
            query_sha256="sha256:" + hashlib.sha256(query.encode()).hexdigest(),
            profile=profile,
            candidates=tuple(selected),
            graph_edges=graph_rows[: max(20, limit * 4)],
            ambiguous_scope=len(exact_title_matches) > 1,
            missing=() if selected else ("authorized_workspace_evidence",),
            trace={
                "retriever_version": WORKSPACE_RETRIEVER_VERSION,
                "reranker_version": WORKSPACE_RERANKER_VERSION,
                "dense_profile_version": WORKSPACE_DENSE_PROFILE_VERSION,
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "pre_temporal_entity_count": len(unfiltered_entities),
                "temporal_entity_count": len(entities),
                "temporal_filter": {
                    "as_of": as_of,
                    "date_from": date_from,
                    "date_to": date_to,
                    "bounds": "inclusive",
                    "field": "entity.effective_at",
                },
                "source_local_scores_only": True,
                "raw_cross_source_scores_added": False,
                "read_only": True,
                "mutation_applied": False,
                "reasoning_included": False,
                "result_sha256": canonical_sha256(
                    {
                        "scope": scope.model_dump(mode="json"),
                        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                        "temporal_filter": {
                            "as_of": as_of,
                            "date_from": date_from,
                            "date_to": date_to,
                        },
                        "selected": [(item.entity_id, item.content_sha256) for item in selected],
                    }
                ),
            },
        )

    @staticmethod
    def _in_temporal_scope(
        entity: WorkspaceEntity,
        *,
        as_of: datetime | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> bool:
        effective = parse_workspace_timestamp(
            entity.effective_at,
            label="entity_effective_at",
        )
        if as_of is not None and effective > as_of:
            return False
        if date_from is not None and effective < date_from:
            return False
        return not (date_to is not None and effective > date_to)

    @staticmethod
    def _task_edge_types(task: WorkspaceQueryTask) -> frozenset[str]:
        return {
            WorkspaceQueryTask.LOCATE: frozenset({"has_topic", "has_iteration", "has_work_item"}),
            WorkspaceQueryTask.CURRENT: frozenset({"has_topic", "has_iteration", "has_work_item"}),
            WorkspaceQueryTask.WORK: frozenset(
                {"has_work_item", "has_acceptance", "checks", "satisfied_by"}
            ),
            WorkspaceQueryTask.BLOCKER: frozenset({"depends_on", "blocked_by", "has_risk"}),
            WorkspaceQueryTask.COVERAGE: frozenset({"has_requirement", "satisfied_by"}),
            WorkspaceQueryTask.INTELLIGENCE: frozenset(
                {"derived_from", "blocked_by", "has_requirement"}
            ),
            WorkspaceQueryTask.DECISION: frozenset({"has_decision", "supported_by", "supersedes"}),
            WorkspaceQueryTask.TEMPORAL: frozenset(
                {
                    "transition_of",
                    "snapshot_contains",
                    "has_iteration",
                    "has_work_item",
                    "supersedes",
                }
            ),
        }[task]

    @staticmethod
    def _expand(
        *,
        candidate_ids: tuple[str, ...],
        edges: tuple[WorkspaceEdge, ...],
        entity_map: dict[str, WorkspaceEntity],
        max_hops: int,
    ) -> dict[str, tuple[int, set[str]]]:
        adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for edge in edges:
            if not edge.reviewed or not edge.current:
                continue
            adjacency[edge.source_id].append((edge.target_id, edge.edge_type.value))
            adjacency[edge.target_id].append((edge.source_id, edge.edge_type.value))
        expanded: dict[str, tuple[int, set[str]]] = {}
        queue: deque[tuple[str, int, set[str]]] = deque((item, 0, set()) for item in candidate_ids)
        seen = set(candidate_ids)
        while queue:
            current, distance, path_types = queue.popleft()
            if distance >= max_hops:
                continue
            for neighbor, edge_type in sorted(adjacency.get(current, ())):
                if neighbor not in entity_map:
                    continue
                next_types = {*path_types, edge_type}
                existing = expanded.get(neighbor)
                if existing is None or distance + 1 < existing[0]:
                    expanded[neighbor] = (distance + 1, next_types)
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((neighbor, distance + 1, next_types))
        return expanded

    @staticmethod
    def _empty(
        scope: WorkspaceScope,
        query: str,
        profile: WorkspaceQueryProfile,
        reason: str,
    ) -> WorkspaceSearchResult:
        return WorkspaceSearchResult(
            scope=scope,
            query_sha256="sha256:" + hashlib.sha256(query.encode()).hexdigest(),
            profile=profile,
            candidates=(),
            graph_edges=(),
            ambiguous_scope=False,
            missing=(reason,),
            trace={
                "retriever_version": WORKSPACE_RETRIEVER_VERSION,
                "candidate_count": 0,
                "selected_count": 0,
                "read_only": True,
                "mutation_applied": False,
                "reasoning_included": False,
            },
        )


__all__ = [
    "WORKSPACE_DENSE_PROFILE_VERSION",
    "WORKSPACE_QUERY_PROFILE_VERSION",
    "WORKSPACE_RERANKER_VERSION",
    "WORKSPACE_RETRIEVER_VERSION",
    "WorkspaceCandidate",
    "WorkspaceQueryProfile",
    "WorkspaceQueryTask",
    "WorkspaceRetrieverV2",
    "WorkspaceSearchResult",
    "workspace_query_profile",
]
