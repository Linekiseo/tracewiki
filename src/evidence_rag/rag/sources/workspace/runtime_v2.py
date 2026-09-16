"""Production-store facade for the complete Research Workspace Source V2 pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from zoneinfo import ZoneInfo

from ....security import secret_findings
from ....workspace.store import WorkspaceStore
from .context_builder import build_workspace_context
from .contracts import (
    WORKSPACE_VIEW_BUILDER_VERSION,
    WorkspaceAuthority,
    WorkspaceEdge,
    WorkspaceEdgeType,
    WorkspaceEntity,
    WorkspaceEntityType,
    WorkspaceLinkStatus,
    WorkspaceRetrievalUnit,
    WorkspaceScope,
    build_workspace_edge,
    build_workspace_entity,
    build_workspace_publication,
    build_workspace_retrieval_unit,
    canonical_sha256,
    workspace_locator,
)
from .evidence_v2 import WorkspaceCoverageResult, evaluate_workspace_coverage
from .intelligence_v2 import derive_workspace_intelligence
from .planning_v2 import evaluate_workspace_done_gate
from .retriever import WorkspaceCandidate, WorkspaceRetrieverV2
from .state_v2 import (
    WorkspaceAsOfProjection,
    build_workspace_snapshot,
    is_overdue,
    parse_workspace_timestamp,
    project_workspace_as_of,
    workspace_timestamp,
)
from .store import WorkspaceV2Store

WORKSPACE_SOURCE_RUNTIME_VERSION = "workspace-production-runtime-v2"
WORKSPACE_SOURCE_DEFAULT_ENGINE = "v1"
_ABS_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'`(])(?:/(?:Users|home|private|tmp|var|etc|opt)/"
    r"|[a-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+)"
)
_RELATION_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
_AUDIT_LIMIT = 10_000


class WorkspaceSourceRuntimeError(RuntimeError):
    """A safe fail-closed Workspace V2 preparation or retrieval failure."""


def _id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _timestamp(value: object, *, fallback: str | None = None) -> str:
    raw = str(value or fallback or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise WorkspaceSourceRuntimeError(
            "Workspace V2 authoritative timestamp is invalid"
        ) from error
    if parsed.tzinfo is None:
        raise WorkspaceSourceRuntimeError("Workspace V2 timestamps must be timezone-aware")
    return parsed.isoformat().replace("+00:00", "Z")


def _timestamp_value(value: object) -> datetime:
    normalized = _timestamp(value)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone(UTC)


def _relation_temporal_state(
    relation: dict[str, Any],
    effective_at: datetime,
) -> str:
    try:
        raw_valid_from = relation.get("valid_from")
        if raw_valid_from == "":
            return "invalid"
        valid_from = _timestamp_value(
            raw_valid_from if raw_valid_from is not None else relation.get("created_at")
        )
        raw_valid_to = relation.get("valid_to")
        if raw_valid_to == "":
            return "invalid"
        valid_to = _timestamp_value(raw_valid_to) if raw_valid_to else None
    except WorkspaceSourceRuntimeError:
        return "invalid"
    if valid_from > effective_at:
        return "inactive"
    if valid_to is not None and effective_at >= valid_to:
        return "inactive"
    return "active"


def _safe_relation_identifier(value: object, *, optional: bool = False) -> str | None:
    normalized = str(value or "")
    if optional and not normalized:
        return ""
    if (
        not _RELATION_IDENTIFIER_RE.fullmatch(normalized)
        or secret_findings(normalized)
        or _ABS_PATH_RE.search(normalized)
    ):
        return None
    return normalized


def _safe_text(*values: object) -> str:
    text = "\n".join(str(value).strip() for value in values if str(value or "").strip())
    if len(text) > 12_000:
        raise WorkspaceSourceRuntimeError("Workspace V2 authoritative text exceeds limits")
    if secret_findings(text) or _ABS_PATH_RE.search(text):
        raise WorkspaceSourceRuntimeError("Workspace V2 authoritative text is protected")
    return text


def _all_rows(store: WorkspaceStore, project_id: str) -> dict[str, Any]:
    topics = [
        item
        for status in ("backlog", "active", "blocked", "completed", "archived")
        for item in store.list_topics(project_id, status=status)
    ]
    iterations = [
        item
        for status in ("planned", "active", "validating", "completed", "blocked", "archived")
        for item in store.list_iterations(project_id, status=status)
    ]
    work_items = [
        item
        for status in ("backlog", "ready", "running", "review", "blocked", "done", "cancelled")
        for item in store.list_work_items(project_id, status=status, limit=10_000)
    ]
    links = [
        link
        for iteration in iterations
        for link in store.list_iteration_links(str(iteration["id"]))
    ]
    return {
        "topics": topics,
        "iterations": iterations,
        "work_items": work_items,
        "links": links,
        "relations": store.list_relations(project_id, limit=10_000),
        "audits": store.list_audits(project_id, limit=10_000),
    }


def _snapshot_generation(
    project: dict[str, Any],
    rows: dict[str, Any],
) -> tuple[str, str]:
    payload = {
        "project": {
            key: project.get(key)
            for key in ("id", "acl_ref", "classification", "status", "updated_at")
        },
        **{
            name: [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "version",
                        "status",
                        "updated_at",
                        "created_at",
                        "review_status",
                        "valid_from",
                        "valid_to",
                    )
                }
                for item in sorted(values, key=lambda row: str(row.get("id") or ""))
            ]
            for name, values in rows.items()
        },
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    times = [
        str(value)
        for item in ([project] + [row for values in rows.values() for row in values])
        for value in (item.get("updated_at"), item.get("created_at"))
        if value
    ]
    watermark = max(times, default="")
    return f"workspace-store-snapshot-v2:{digest}", _timestamp(
        watermark,
        fallback=datetime.now(UTC).isoformat(),
    )


def _entity_id(kind: str, formal_id: str, generation_id: str) -> str:
    return _id(f"ws{kind}", formal_id, generation_id)


def _stable_id(kind: str, formal_id: str) -> str:
    return _id(f"wsstable{kind}", formal_id)


def _source_digest(record: dict[str, Any], *, omitted: tuple[str, ...] = ()) -> str:
    return canonical_sha256(
        {key: value for key, value in record.items() if key not in {*omitted, "workspace_path"}}
    )


def _retrieval_unit(entity: WorkspaceEntity) -> WorkspaceRetrievalUnit:
    metadata = entity.metadata
    exact = tuple(
        dict.fromkeys(
            str(value)
            for value in (
                entity.display_key,
                entity.label,
                metadata.get("formal_entity_id"),
                metadata.get("formal_requirement_id"),
            )
            if value
        )
    )
    text = _safe_text(
        entity.label,
        entity.text,
        entity.status,
        entity.owner,
        entity.assignee,
        metadata.get("role"),
        metadata.get("next_actions"),
        metadata.get("reasons"),
    )
    return build_workspace_retrieval_unit(
        unit_id=_id("wsunit", entity.entity_id, entity.content_sha256),
        entity_id=entity.entity_id,
        scope=entity.scope,
        unit_type=entity.entity_type,
        authority=entity.authority,
        exact_terms=exact,
        sparse_text=text,
        dense_text=text,
        structured={
            "status": entity.status,
            "owner": entity.owner,
            "assignee": entity.assignee,
            "priority": entity.priority,
            "due_at": entity.due_at,
            "current": entity.current,
            "archived": entity.archived,
        },
        locator=entity.locator,
    )


class _WorkspacePublicationBuilder:
    def __init__(
        self,
        *,
        project: dict[str, Any],
        rows: dict[str, Any],
        scope: WorkspaceScope,
        watermark: str,
        source_watermark: str,
        projection_at: datetime,
        allowed_acl_refs: tuple[str, ...],
        enforce_acl: bool,
    ) -> None:
        self.project = project
        self.rows = rows
        self.scope = scope
        self.watermark = watermark
        self.source_watermark = source_watermark
        self.projection_at = projection_at
        self.allowed_acl_refs = allowed_acl_refs
        self.enforce_acl = enforce_acl
        self.entities: list[WorkspaceEntity] = []
        self.edges: list[WorkspaceEdge] = []
        self.formal_to_runtime: dict[str, str] = {}
        self.runtime_to_formal: dict[str, str] = {}
        self.diagnostics: list[str] = []
        self.confirmed_relations: list[dict[str, str]] = []

    def build(self):
        self._control_plane()
        self._acceptance()
        self._relations()
        requirements, links = self._evidence()
        self._transitions()
        coverage = self._coverage(requirements, links)
        self._done_gates()
        snapshot = build_workspace_snapshot(
            scope=self.scope,
            entities=self.entities,
            as_of=self.watermark,
            source_watermarks={"workspace": self.source_watermark},
        )
        self.entities.append(snapshot)
        for entity in tuple(self.entities):
            if (
                entity.entity_id != snapshot.entity_id
                and entity.current
                and not entity.archived
                and entity.entity_type
                in {
                    WorkspaceEntityType.PROJECT,
                    WorkspaceEntityType.TOPIC,
                    WorkspaceEntityType.ITERATION,
                    WorkspaceEntityType.WORK_ITEM,
                }
            ):
                self._edge(
                    WorkspaceEdgeType.SNAPSHOT_CONTAINS,
                    snapshot.entity_id,
                    entity.entity_id,
                    reviewed=True,
                    effective_at=self.watermark,
                )
        intelligence, _summary = derive_workspace_intelligence(
            scope=self.scope,
            snapshot=snapshot,
            entities=self.entities,
            coverage=coverage,
            generated_at=self.watermark,
        )
        self.entities.append(intelligence)
        self._edge(
            WorkspaceEdgeType.DERIVED_FROM,
            intelligence.entity_id,
            snapshot.entity_id,
            reviewed=True,
            effective_at=self.watermark,
        )
        units = tuple(_retrieval_unit(item) for item in self.entities)
        return (
            build_workspace_publication(
                publication_id=_id(
                    "wspub",
                    self.scope.project_id,
                    self.scope.generation_id,
                    *(item.content_sha256 for item in self.entities),
                ),
                scope=self.scope,
                entities=tuple(self.entities),
                edges=tuple(self.edges),
                retrieval_units=units,
                builder_version=WORKSPACE_VIEW_BUILDER_VERSION,
                published_at=self.watermark,
            ),
            tuple(coverage),
            tuple(sorted(set(self.diagnostics))),
            dict(self.runtime_to_formal),
            tuple(self.confirmed_relations),
        )

    def _control_plane(self) -> None:
        project_id = str(self.project["id"])
        runtime_project = self._entity(
            kind="project",
            formal_id=project_id,
            entity_type=WorkspaceEntityType.PROJECT,
            record=self.project,
            display_key="PROJECT-" + hashlib.sha256(project_id.encode()).hexdigest()[:8].upper(),
            label=str(self.project.get("name") or project_id),
            text=_safe_text(self.project.get("description")),
            status=str(self.project.get("status") or "active"),
            owner=str(self.project.get("owner") or "") or None,
            version=1,
            effective_at=self.project.get("updated_at") or self.project.get("created_at"),
            archived=str(self.project.get("status") or "") == "archived",
            metadata={
                "classification": self.project.get("classification"),
                "formal_entity_id": project_id,
            },
        )
        for topic in self.rows["topics"]:
            item = self._entity(
                kind="topic",
                formal_id=str(topic["id"]),
                entity_type=WorkspaceEntityType.TOPIC,
                record=topic,
                display_key=str(topic["display_key"]),
                label=str(topic["title"]),
                text=_safe_text(topic.get("problem_statement"), topic.get("objective")),
                status=str(topic["status"]),
                owner=str(topic.get("owner") or "") or None,
                priority=int(topic["priority"]),
                parent_id=runtime_project.entity_id,
                version=1,
                effective_at=topic.get("updated_at") or topic.get("created_at"),
                archived=str(topic["status"]) == "archived",
                metadata={
                    "tags": tuple(topic.get("tags") or ()),
                    "formal_entity_id": str(topic["id"]),
                },
            )
            self._edge(
                WorkspaceEdgeType.HAS_TOPIC,
                runtime_project.entity_id,
                item.entity_id,
                reviewed=True,
                effective_at=item.effective_at,
            )
        for iteration in self.rows["iterations"]:
            topic_id = self.formal_to_runtime.get(str(iteration["topic_id"]))
            if topic_id is None:
                raise WorkspaceSourceRuntimeError(
                    "Workspace V2 iteration parent is outside project authority"
                )
            item = self._entity(
                kind="iteration",
                formal_id=str(iteration["id"]),
                entity_type=WorkspaceEntityType.ITERATION,
                record=iteration,
                display_key=str(iteration["display_key"]),
                label=str(iteration["title"]),
                text=_safe_text(
                    iteration.get("goal"),
                    iteration.get("hypothesis"),
                    iteration.get("summary"),
                ),
                status=str(iteration["status"]),
                owner=str(iteration.get("owner") or "") or None,
                parent_id=topic_id,
                topic_id=topic_id,
                version=1,
                effective_at=iteration.get("updated_at") or iteration.get("created_at"),
                archived=str(iteration["status"]) == "archived",
                metadata={
                    "progress": int(iteration.get("progress") or 0),
                    "starts_at": iteration.get("starts_at"),
                    "target_at": iteration.get("target_at"),
                    "completed_at": iteration.get("completed_at"),
                    "formal_entity_id": str(iteration["id"]),
                },
            )
            self._edge(
                WorkspaceEdgeType.HAS_ITERATION,
                topic_id,
                item.entity_id,
                reviewed=True,
                effective_at=item.effective_at,
            )
        for work in self.rows["work_items"]:
            topic_id = self.formal_to_runtime.get(str(work.get("topic_id") or ""))
            iteration_id = self.formal_to_runtime.get(str(work.get("iteration_id") or ""))
            parent_id = iteration_id or topic_id or runtime_project.entity_id
            due_at = _timestamp(work["due_at"]) if work.get("due_at") else None
            item = self._entity(
                kind="work",
                formal_id=str(work["id"]),
                entity_type=WorkspaceEntityType.WORK_ITEM,
                record=work,
                display_key=str(work["display_key"]),
                label=str(work["title"]),
                text=_safe_text(
                    work.get("objective"),
                    work.get("summary"),
                    *(work.get("acceptance_criteria") or ()),
                ),
                status=str(work["status"]),
                assignee=str(work.get("assignee") or "") or None,
                priority=int(work["priority"]),
                due_at=due_at,
                parent_id=parent_id,
                topic_id=topic_id,
                iteration_id=iteration_id,
                version=int(work.get("version") or 1),
                effective_at=work.get("updated_at") or work.get("created_at"),
                archived=str(work["status"]) == "cancelled",
                metadata={
                    "kind": work.get("kind"),
                    "assignee_type": work.get("assignee_type"),
                    "base_ref": work.get("base_ref"),
                    "repository_id": work.get("repository_id"),
                    "formal_entity_id": str(work["id"]),
                    "overdue": is_overdue(
                        due_at=due_at,
                        observed_at=self.watermark,
                        project_timezone=self.scope.timezone,
                        terminal=str(work["status"]) in {"done", "cancelled"},
                    ),
                },
            )
            self._edge(
                WorkspaceEdgeType.HAS_WORK_ITEM,
                parent_id,
                item.entity_id,
                reviewed=True,
                effective_at=item.effective_at,
            )

    def _acceptance(self) -> None:
        for work in self.rows["work_items"]:
            work_id = self.formal_to_runtime[str(work["id"])]
            for ordinal, criterion in enumerate(work.get("acceptance_criteria") or (), 1):
                formal_id = f"{work['id']}#criterion={ordinal}"
                item = self._entity(
                    kind="criterion",
                    formal_id=formal_id,
                    entity_type=WorkspaceEntityType.ACCEPTANCE_CRITERION,
                    record={
                        "work_item_id": work["id"],
                        "ordinal": ordinal,
                        "criterion": criterion,
                    },
                    display_key=f"{work['display_key']}-AC-{ordinal}",
                    label=f"Acceptance criterion {ordinal}",
                    text=_safe_text(criterion),
                    status="unknown",
                    parent_id=work_id,
                    topic_id=self.formal_to_runtime.get(str(work.get("topic_id") or "")),
                    iteration_id=self.formal_to_runtime.get(str(work.get("iteration_id") or "")),
                    work_item_id=work_id,
                    version=int(work.get("version") or 1),
                    effective_at=work.get("updated_at") or work.get("created_at"),
                    metadata={
                        "ordinal": ordinal,
                        "formal_entity_id": formal_id,
                    },
                )
                self._edge(
                    WorkspaceEdgeType.HAS_ACCEPTANCE,
                    work_id,
                    item.entity_id,
                    reviewed=True,
                    effective_at=item.effective_at,
                )

    def _relations(self) -> None:
        aliases = {
            "depends_on": WorkspaceEdgeType.DEPENDS_ON,
            "blocked_by": WorkspaceEdgeType.BLOCKED_BY,
            "has_risk": WorkspaceEdgeType.HAS_RISK,
            "has_decision": WorkspaceEdgeType.HAS_DECISION,
            "supported_by": WorkspaceEdgeType.SUPPORTED_BY,
            "supersedes": WorkspaceEdgeType.SUPERSEDES,
        }
        entity_by_id = {item.entity_id: item for item in self.entities}
        for relation in self.rows["relations"]:
            temporal_state = _relation_temporal_state(relation, self.projection_at)
            if temporal_state == "invalid":
                self.diagnostics.append("invalid_relation_temporal")
                continue
            if temporal_state != "active":
                self.diagnostics.append("inactive_relation_pruned")
                continue
            edge_type = aliases.get(str(relation.get("predicate") or "").casefold())
            source_id = self.formal_to_runtime.get(str(relation.get("source_entity_id") or ""))
            target_id = self.formal_to_runtime.get(str(relation.get("target_entity_id") or ""))
            if edge_type is None or source_id is None or target_id is None:
                self.diagnostics.append(f"unmapped_relation:{relation.get('id')}")
                continue
            reviewed = str(relation.get("review_status") or "") == "confirmed"
            effective_at = relation.get("valid_from") or relation.get("created_at")
            source = entity_by_id[source_id]
            target = entity_by_id[target_id]
            formal_id = _safe_relation_identifier(relation.get("id"))
            formal_source = _safe_relation_identifier(relation.get("source_entity_id"))
            formal_target = _safe_relation_identifier(relation.get("target_entity_id"))
            formal_evidence = _safe_relation_identifier(
                relation.get("evidence_entity_id"),
                optional=True,
            )
            rule_version = _safe_relation_identifier(
                relation.get("rule_version"),
                optional=True,
            )
            if (
                formal_id is None
                or formal_source is None
                or formal_target is None
                or formal_evidence is None
                or rule_version is None
            ):
                self.diagnostics.append("invalid_relation_identity")
                continue
            if formal_evidence and formal_evidence not in self.formal_to_runtime:
                self.diagnostics.append("unverified_relation_evidence")
                continue
            if (
                source.scope != self.scope
                or target.scope != self.scope
                or not source.current
                or not target.current
                or source.archived
                or target.archived
                or source.version < 1
                or target.version < 1
            ):
                self.diagnostics.append("invalid_relation_scope_or_version")
                continue
            self._edge(
                edge_type,
                source_id,
                target_id,
                reviewed=reviewed,
                current=not bool(relation.get("valid_to")),
                effective_at=effective_at,
                metadata={
                    "formal_edge_id": relation.get("id"),
                    "derivation": relation.get("derivation"),
                    "confidence": relation.get("confidence"),
                    "review_status": relation.get("review_status"),
                    "rule_version": relation.get("rule_version"),
                },
            )
            if reviewed:
                self.confirmed_relations.append(
                    {
                        "id": formal_id,
                        "predicate": edge_type.value,
                        "source": formal_source,
                        "target": formal_target,
                        "evidence_entity_id": formal_evidence,
                        "review_status": "confirmed",
                    }
                )
            if (
                reviewed
                and source.entity_type == WorkspaceEntityType.WORK_ITEM
                and edge_type in {WorkspaceEdgeType.DEPENDS_ON, WorkspaceEdgeType.BLOCKED_BY}
            ):
                entity_type = (
                    WorkspaceEntityType.DEPENDENCY
                    if edge_type == WorkspaceEdgeType.DEPENDS_ON
                    else WorkspaceEntityType.BLOCKER
                )
                satisfied = target.status in {"done", "completed", "resolved", "dismissed"}
                self._entity(
                    kind=entity_type.value,
                    formal_id=str(relation["id"]),
                    entity_type=entity_type,
                    record=relation,
                    display_key=(
                        ("DEP-" if entity_type == WorkspaceEntityType.DEPENDENCY else "BLK-")
                        + hashlib.sha256(str(relation["id"]).encode()).hexdigest()[:8].upper()
                    ),
                    label=f"{source.display_key} {edge_type.value} {target.display_key}",
                    text=_safe_text(source.label, edge_type.value, target.label),
                    status="satisfied" if satisfied else "unresolved",
                    authority=WorkspaceAuthority.REVIEWED_RELATION,
                    parent_id=source.entity_id,
                    topic_id=source.topic_id,
                    iteration_id=source.iteration_id,
                    work_item_id=source.entity_id,
                    version=1,
                    effective_at=effective_at,
                    metadata={
                        "formal_edge_id": relation["id"],
                        "target_id": target.entity_id,
                        "dependency_status": "satisfied" if satisfied else "unresolved",
                        "review_status": "confirmed",
                    },
                )

    def _evidence(
        self,
    ) -> tuple[tuple[WorkspaceEntity, ...], tuple[WorkspaceEntity, ...]]:
        requirements: dict[str, WorkspaceEntity] = {}
        links: list[WorkspaceEntity] = []
        for link in self.rows["links"]:
            metadata = dict(link.get("metadata") or {})
            formal_requirement = str(metadata.get("requirement_id") or "")
            required_roles = tuple(str(item) for item in metadata.get("required_roles") or ())
            required = {
                "external_version",
                "external_generation",
                "pinned_generation",
                "external_acl_ref",
                "observed_at",
                "independence_group",
                "reviewed",
                "fresh",
                "link_status",
            }
            if not formal_requirement or not required_roles or not required.issubset(metadata):
                self.diagnostics.append(f"unmapped_evidence_link:{link.get('id')}")
                continue
            try:
                link_status = WorkspaceLinkStatus(str(metadata["link_status"]))
            except ValueError:
                self.diagnostics.append(f"invalid_evidence_status:{link.get('id')}")
                continue
            iteration_id = self.formal_to_runtime.get(str(link["iteration_id"]))
            if iteration_id is None:
                raise WorkspaceSourceRuntimeError(
                    "Workspace V2 evidence iteration is outside project authority"
                )
            requirement = requirements.get(formal_requirement)
            if requirement is None:
                requirement = self._entity(
                    kind="requirement",
                    formal_id=formal_requirement,
                    entity_type=WorkspaceEntityType.EVIDENCE_REQUIREMENT,
                    record={
                        "formal_requirement_id": formal_requirement,
                        "required_roles": required_roles,
                        "allowed_source_domains": metadata.get("allowed_source_domains") or (),
                        "min_count": metadata.get("min_count") or 1,
                        "min_independence_groups": metadata.get("min_independence_groups") or 1,
                        "max_age_days": metadata.get("max_age_days") or 36500,
                    },
                    display_key=str(metadata.get("requirement_display_key") or "")[:240]
                    or "REQ-" + hashlib.sha256(formal_requirement.encode()).hexdigest()[:8].upper(),
                    label=str(metadata.get("requirement_label") or formal_requirement),
                    text=_safe_text(metadata.get("requirement_purpose")),
                    status="pending",
                    parent_id=iteration_id,
                    iteration_id=iteration_id,
                    version=int(metadata.get("requirement_version") or 1),
                    effective_at=metadata.get("requirement_effective_at") or link.get("created_at"),
                    metadata={
                        "formal_requirement_id": formal_requirement,
                        "required_roles": required_roles,
                        "allowed_source_domains": tuple(
                            str(item) for item in metadata.get("allowed_source_domains") or ()
                        ),
                        "min_count": int(metadata.get("min_count") or 1),
                        "min_independence_groups": int(
                            metadata.get("min_independence_groups") or 1
                        ),
                        "max_age_days": int(metadata.get("max_age_days") or 36500),
                    },
                )
                requirements[formal_requirement] = requirement
                self._edge(
                    WorkspaceEdgeType.HAS_REQUIREMENT,
                    iteration_id,
                    requirement.entity_id,
                    reviewed=True,
                    effective_at=requirement.effective_at,
                )
            external_acl = str(metadata["external_acl_ref"])
            observed_at = _timestamp(metadata["observed_at"])
            item = self._entity(
                kind="evidence",
                formal_id=str(link["id"]),
                entity_type=WorkspaceEntityType.EVIDENCE_LINK,
                record=link,
                display_key="LINK-"
                + hashlib.sha256(str(link["id"]).encode()).hexdigest()[:8].upper(),
                label=f"{link['role']}: {link['entity_type']}",
                text="",
                status=link_status.value,
                authority=(
                    WorkspaceAuthority.REVIEWED_RELATION
                    if bool(metadata["reviewed"])
                    else WorkspaceAuthority.OBSERVED_EXTERNAL
                ),
                parent_id=requirement.entity_id,
                iteration_id=iteration_id,
                version=1,
                effective_at=link.get("created_at") or observed_at,
                current=link_status
                not in {
                    WorkspaceLinkStatus.REJECTED,
                    WorkspaceLinkStatus.SUPERSEDED,
                },
                archived=link_status
                in {WorkspaceLinkStatus.REJECTED, WorkspaceLinkStatus.SUPERSEDED},
                metadata={
                    "formal_entity_id": str(link["id"]),
                    "requirement_id": requirement.entity_id,
                    "external_entity_id": str(link["entity_id"]),
                    "external_version": str(metadata["external_version"]),
                    "external_generation": str(metadata["external_generation"]),
                    "pinned_generation": str(metadata["pinned_generation"]),
                    "external_acl_ref": external_acl,
                    "source_domain": str(link["source_type"]),
                    "role": str(link["role"]),
                    "link_status": link_status.value,
                    "reviewed": bool(metadata["reviewed"]),
                    "fresh": bool(metadata["fresh"]),
                    "independence_group": str(metadata["independence_group"]),
                    "observed_at": observed_at,
                },
            )
            links.append(item)
            self._edge(
                WorkspaceEdgeType.SATISFIED_BY,
                requirement.entity_id,
                item.entity_id,
                reviewed=bool(metadata["reviewed"]),
                effective_at=item.effective_at,
            )
        return tuple(requirements.values()), tuple(links)

    def _transitions(self) -> None:
        for audit in self.rows["audits"]:
            detail = dict(audit.get("detail") or {})
            action = str(audit.get("action") or "")
            if action not in {"work_item.transitioned", "work_item.deleted"}:
                continue
            target = self.formal_to_runtime.get(str(audit.get("resource_id") or ""))
            if target is None or not detail.get("from") or not detail.get("to"):
                self.diagnostics.append(f"unmapped_transition:{audit.get('id')}")
                continue
            target_entity = next(item for item in self.entities if item.entity_id == target)
            item = self._entity(
                kind="transition",
                formal_id=str(audit["id"]),
                entity_type=WorkspaceEntityType.TRANSITION,
                record=audit,
                display_key="TRANS-"
                + hashlib.sha256(str(audit["id"]).encode()).hexdigest()[:8].upper(),
                label=f"{detail['from']} → {detail['to']}",
                text="",
                status="applied",
                parent_id=target,
                topic_id=target_entity.topic_id,
                iteration_id=target_entity.iteration_id,
                work_item_id=target,
                owner=str(audit.get("actor") or "") or None,
                version=1,
                effective_at=audit.get("created_at"),
                metadata={
                    "formal_entity_id": str(audit["id"]),
                    "entity_id": target,
                    "entity_type": WorkspaceEntityType.WORK_ITEM,
                    "before": str(detail["from"]),
                    "after": str(detail["to"]),
                    "actor": str(audit.get("actor") or ""),
                    "event_at": _timestamp(audit.get("created_at")),
                    "effective_at": _timestamp(audit.get("created_at")),
                    "reason": str(detail.get("summary") or ""),
                    "trace_id": audit.get("trace_id"),
                },
            )
            self._edge(
                WorkspaceEdgeType.TRANSITION_OF,
                item.entity_id,
                target,
                reviewed=True,
                effective_at=item.effective_at,
            )

    def _coverage(
        self,
        requirements: tuple[WorkspaceEntity, ...],
        links: tuple[WorkspaceEntity, ...],
    ) -> tuple[WorkspaceCoverageResult, ...]:
        results = tuple(
            evaluate_workspace_coverage(
                requirement,
                links,
                observed_at=self.watermark,
                allowed_acl_refs=self.allowed_acl_refs,
                enforce_acl=self.enforce_acl,
            )
            for requirement in requirements
        )
        by_id = {item.requirement_id: item for item in results}
        replaced: list[WorkspaceEntity] = []
        for entity in self.entities:
            coverage = by_id.get(entity.entity_id)
            if coverage is None:
                replaced.append(entity)
                continue
            replaced.append(
                build_workspace_entity(
                    **entity.model_dump(
                        mode="python",
                        exclude={"content_sha256", "status", "metadata"},
                    ),
                    status="satisfied" if coverage.satisfied else "missing",
                    metadata={
                        **entity.metadata,
                        "coverage": coverage.model_dump(mode="json"),
                    },
                )
            )
        self.entities = replaced
        return results

    def _done_gates(self) -> None:
        criteria = tuple(
            item
            for item in self.entities
            if item.entity_type == WorkspaceEntityType.ACCEPTANCE_CRITERION
        )
        checks = tuple(
            item
            for item in self.entities
            if item.entity_type == WorkspaceEntityType.ACCEPTANCE_CHECK
        )
        dependencies = tuple(
            item for item in self.entities if item.entity_type == WorkspaceEntityType.DEPENDENCY
        )
        blockers = tuple(
            item for item in self.entities if item.entity_type == WorkspaceEntityType.BLOCKER
        )
        evidence_links = tuple(
            item for item in self.entities if item.entity_type == WorkspaceEntityType.EVIDENCE_LINK
        )
        replaced: list[WorkspaceEntity] = []
        for entity in self.entities:
            if entity.entity_type != WorkspaceEntityType.WORK_ITEM:
                replaced.append(entity)
                continue
            gate = evaluate_workspace_done_gate(
                entity,
                criteria=criteria,
                checks=checks,
                dependencies=dependencies,
                blockers=blockers,
                evidence_links=evidence_links,
            )
            replaced.append(
                build_workspace_entity(
                    **entity.model_dump(mode="python", exclude={"content_sha256", "metadata"}),
                    metadata={
                        **entity.metadata,
                        "done_gate": gate.model_dump(mode="json"),
                    },
                )
            )
        self.entities = replaced

    def _entity(
        self,
        *,
        kind: str,
        formal_id: str,
        entity_type: WorkspaceEntityType,
        record: dict[str, Any],
        display_key: str,
        label: str,
        text: str,
        status: str,
        version: int,
        effective_at: object,
        authority: WorkspaceAuthority = WorkspaceAuthority.AUTHORITATIVE,
        parent_id: str | None = None,
        topic_id: str | None = None,
        iteration_id: str | None = None,
        work_item_id: str | None = None,
        owner: str | None = None,
        assignee: str | None = None,
        priority: int | None = None,
        due_at: str | None = None,
        current: bool = True,
        archived: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceEntity:
        entity_id = _entity_id(kind, formal_id, self.scope.generation_id)
        item = build_workspace_entity(
            entity_id=entity_id,
            stable_id=_stable_id(kind, formal_id),
            entity_type=entity_type,
            scope=self.scope,
            version=max(1, version),
            display_key=display_key[:240],
            label=_safe_text(label),
            text=text,
            status=status,
            authority=authority,
            parent_id=parent_id,
            topic_id=topic_id,
            iteration_id=iteration_id,
            work_item_id=work_item_id,
            owner=_safe_text(owner) if owner else None,
            assignee=_safe_text(assignee) if assignee else None,
            priority=priority,
            due_at=due_at,
            effective_at=_timestamp(effective_at, fallback=self.watermark),
            current=current,
            archived=archived,
            locator=workspace_locator(
                scope=self.scope,
                entity_type=entity_type,
                stable_id=_stable_id(kind, formal_id),
                version=max(1, version),
            ),
            source_sha256=_source_digest(record),
            metadata=metadata or {},
        )
        self.entities.append(item)
        self.formal_to_runtime[formal_id] = item.entity_id
        self.runtime_to_formal[item.entity_id] = formal_id
        return item

    def _edge(
        self,
        edge_type: WorkspaceEdgeType,
        source_id: str,
        target_id: str,
        *,
        reviewed: bool,
        effective_at: object,
        current: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.edges.append(
            build_workspace_edge(
                edge_id=_id(
                    "wsedge",
                    self.scope.generation_id,
                    source_id,
                    target_id,
                    edge_type.value,
                ),
                edge_type=edge_type,
                scope=self.scope,
                source_id=source_id,
                target_id=target_id,
                reviewed=reviewed,
                current=current,
                effective_at=_timestamp(effective_at, fallback=self.watermark),
                metadata=metadata or {},
            )
        )


@dataclass(frozen=True, slots=True)
class _HistoricalRows:
    project: dict[str, Any]
    rows: dict[str, Any]
    projection: WorkspaceAsOfProjection
    provable_from: str
    diagnostics: tuple[str, ...]


class _HistoricalProjectionUnavailable(RuntimeError):
    def __init__(self, reason: str, *diagnostics: str) -> None:
        self.reason = reason
        self.diagnostics = tuple(diagnostics)
        super().__init__(reason)


def _request_timestamp(value: str | None, label: str) -> tuple[str | None, datetime | None]:
    if value is None:
        return None, None
    try:
        normalized = workspace_timestamp(value, label=label)
        parsed = parse_workspace_timestamp(normalized, label=label)
    except ValueError as error:
        raise WorkspaceSourceRuntimeError(str(error)) from error
    return normalized, parsed


def _audit_timestamp(audit: dict[str, Any]) -> datetime:
    try:
        return parse_workspace_timestamp(audit.get("created_at"), label="audit_created_at")
    except ValueError as error:
        raise _HistoricalProjectionUnavailable(
            "historical_audit_invalid",
            f"invalid_audit_timestamp:{audit.get('id')}",
        ) from error


def _row_timestamp(row: dict[str, Any], field: str, row_type: str) -> datetime:
    try:
        return parse_workspace_timestamp(
            row.get(field),
            label=f"{row_type}_{field}",
        )
    except ValueError as error:
        raise _HistoricalProjectionUnavailable(
            "historical_row_invalid",
            f"invalid_{field}:{row_type}:{row.get('id')}",
        ) from error


def _project_formal_rows_as_of(
    *,
    project: dict[str, Any],
    rows: dict[str, Any],
    scope: WorkspaceScope,
    source_watermark: str,
    cutoff: datetime,
    allowed_acl_refs: tuple[str, ...],
    enforce_acl: bool,
) -> _HistoricalRows:
    """Reconstruct only state that formal rows and audit transitions can prove."""

    project_created = _row_timestamp(project, "created_at", "project")
    provable_from = project_created.isoformat().replace("+00:00", "Z")
    if cutoff < project_created:
        raise _HistoricalProjectionUnavailable(
            "historical_snapshot_unavailable",
            "as_of_before_project_creation",
        )
    audits = tuple(rows["audits"])
    if len(audits) >= _AUDIT_LIMIT:
        raise _HistoricalProjectionUnavailable(
            "historical_snapshot_unavailable",
            "audit_history_truncated",
        )
    ordered_audits = tuple(
        sorted(
            ((_audit_timestamp(audit), audit) for audit in audits),
            key=lambda item: (item[0], str(item[1].get("id") or "")),
        )
    )
    future_audits = tuple(item for item in ordered_audits if item[0] > cutoff)
    creation_actions = {
        "project.created",
        "topic.created",
        "iteration.created",
        "work_item.created",
        "relation.created",
    }
    reversible_actions = {"work_item.transitioned", "work_item.deleted"}
    diagnostics: list[str] = []
    links_by_id = {str(item.get("id") or ""): item for item in rows["links"]}
    for _event_at, audit in future_audits:
        action = str(audit.get("action") or "")
        resource_id = str(audit.get("resource_id") or "")
        if action in creation_actions or action in reversible_actions:
            continue
        if action == "iteration.evidence_linked":
            link = links_by_id.get(resource_id)
            link_events = [
                item
                for item in ordered_audits
                if str(item[1].get("action") or "") == action
                and str(item[1].get("resource_id") or "") == resource_id
            ]
            if (
                link is not None
                and _row_timestamp(link, "created_at", "link") <= cutoff
                and len(link_events) > 1
            ):
                raise _HistoricalProjectionUnavailable(
                    "historical_snapshot_unavailable",
                    f"unreconstructable_link_update:{audit.get('id')}",
                )
            continue
        raise _HistoricalProjectionUnavailable(
            "historical_snapshot_unavailable",
            f"unreconstructable_audit:{audit.get('id')}:{action or 'unknown'}",
        )

    mutable_rows = (
        ("topic", rows["topics"]),
        ("iteration", rows["iterations"]),
        ("work_item", rows["work_items"]),
        ("relation", rows["relations"]),
    )
    audits_by_resource: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for event_at, audit in ordered_audits:
        audits_by_resource.setdefault(str(audit.get("resource_id") or ""), []).append(
            (event_at, audit)
        )
    project_updated = _row_timestamp(project, "updated_at", "project")
    if project_updated > cutoff and not any(
        event_at > cutoff
        for event_at, _audit in audits_by_resource.get(str(project.get("id") or ""), ())
    ):
        raise _HistoricalProjectionUnavailable(
            "historical_snapshot_unavailable",
            "unaudited_current_row:project",
        )
    for row_type, values in mutable_rows:
        for row in values:
            created = _row_timestamp(row, "created_at", row_type)
            if created > cutoff:
                continue
            updated = _row_timestamp(row, "updated_at", row_type)
            if updated <= cutoff:
                continue
            resource_events = audits_by_resource.get(str(row.get("id") or ""), ())
            if not any(event_at > cutoff for event_at, _audit in resource_events):
                raise _HistoricalProjectionUnavailable(
                    "historical_snapshot_unavailable",
                    f"unaudited_current_row:{row_type}:{row.get('id')}",
                )

    projection_builder = _WorkspacePublicationBuilder(
        project=project,
        rows=rows,
        scope=scope,
        watermark=source_watermark,
        source_watermark=source_watermark,
        projection_at=cutoff,
        allowed_acl_refs=allowed_acl_refs,
        enforce_acl=enforce_acl,
    )
    projection_builder._control_plane()
    projection_builder._transitions()
    current_work = {
        projection_builder.runtime_to_formal[item.entity_id]: item
        for item in projection_builder.entities
        if item.entity_type == WorkspaceEntityType.WORK_ITEM
    }
    transitions = tuple(
        item
        for item in projection_builder.entities
        if item.entity_type == WorkspaceEntityType.TRANSITION
    )
    transitions_by_target: dict[str, list[WorkspaceEntity]] = {}
    for transition in transitions:
        transitions_by_target.setdefault(
            str(transition.metadata.get("entity_id") or ""),
            [],
        ).append(transition)

    initial_entities: list[WorkspaceEntity] = []
    selected_transition_ids: set[str] = set()
    selected_rows: list[dict[str, Any]] = []
    reconstructed_work_ids: set[str] = set()
    for raw_row in rows["work_items"]:
        row = dict(raw_row)
        created = _row_timestamp(row, "created_at", "work_item")
        if created > cutoff:
            continue
        formal_id = str(row["id"])
        current_entity = current_work[formal_id]
        entity_transitions = sorted(
            transitions_by_target.get(current_entity.entity_id, ()),
            key=lambda item: (
                parse_workspace_timestamp(
                    str(item.metadata.get("effective_at") or item.effective_at),
                    label="transition_effective_at",
                ),
                item.entity_id,
            ),
        )
        mutation_audits = [
            (event_at, audit)
            for event_at, audit in audits_by_resource.get(formal_id, ())
            if str(audit.get("action") or "")
            in {"work_item.updated", "work_item.transitioned", "work_item.deleted"}
        ]
        future_mutations = [
            (event_at, audit) for event_at, audit in mutation_audits if event_at > cutoff
        ]
        expected_version = 1 + len(mutation_audits)
        version_matches_audit = int(row.get("version") or 1) == expected_version
        has_untracked_status_update = any(
            str(audit.get("action") or "") == "work_item.updated"
            and "status" in dict(audit.get("detail") or {})
            for _event_at, audit in mutation_audits
        )
        if not future_mutations and (not version_matches_audit or has_untracked_status_update):
            if _row_timestamp(row, "updated_at", "work_item") > cutoff:
                raise _HistoricalProjectionUnavailable(
                    "historical_snapshot_unavailable",
                    f"unproven_work_item_checkpoint:{formal_id}",
                )
            initial_entities.append(current_entity)
            selected_rows.append(row)
            continue
        if not version_matches_audit:
            raise _HistoricalProjectionUnavailable(
                "historical_snapshot_unavailable",
                f"incomplete_work_item_audit_chain:{formal_id}",
            )
        if has_untracked_status_update:
            raise _HistoricalProjectionUnavailable(
                "historical_snapshot_unavailable",
                f"untracked_status_update:{formal_id}",
            )
        initial_status = (
            str(entity_transitions[0].metadata.get("before") or "")
            if entity_transitions
            else str(row["status"])
        )
        chain_status = initial_status
        seen_effective: set[datetime] = set()
        for transition in entity_transitions:
            effective = parse_workspace_timestamp(
                str(transition.metadata.get("effective_at") or transition.effective_at),
                label="transition_effective_at",
            )
            if effective in seen_effective:
                raise _HistoricalProjectionUnavailable(
                    "historical_snapshot_unavailable",
                    f"ambiguous_transition_order:{formal_id}",
                )
            seen_effective.add(effective)
            before = str(transition.metadata.get("before") or "")
            after = str(transition.metadata.get("after") or "")
            if chain_status != before:
                raise _HistoricalProjectionUnavailable(
                    "historical_snapshot_unavailable",
                    f"transition_chain_mismatch:{formal_id}",
                )
            chain_status = after
            selected_transition_ids.add(transition.entity_id)
        if chain_status != str(row["status"]):
            raise _HistoricalProjectionUnavailable(
                "historical_snapshot_unavailable",
                f"transition_current_state_mismatch:{formal_id}",
            )
        initial_entities.append(
            build_workspace_entity(
                **current_entity.model_dump(
                    mode="python",
                    exclude={
                        "content_sha256",
                        "status",
                        "version",
                        "effective_at",
                        "current",
                        "archived",
                        "locator",
                        "metadata",
                    },
                ),
                status=initial_status,
                version=1,
                effective_at=workspace_timestamp(row["created_at"], label="work_item_created_at"),
                current=True,
                archived=initial_status == "cancelled",
                locator=workspace_locator(
                    scope=scope,
                    entity_type=WorkspaceEntityType.WORK_ITEM,
                    stable_id=current_entity.stable_id,
                    version=1,
                ),
                metadata={
                    **current_entity.metadata,
                    "historical_baseline": "formal_creation_row_plus_audit_chain",
                },
            )
        )
        reconstructed_work_ids.add(formal_id)
        selected_rows.append(row)

    selected_transitions = tuple(
        transition for transition in transitions if transition.entity_id in selected_transition_ids
    )
    normalized_cutoff = cutoff.isoformat().replace("+00:00", "Z")
    projection = project_workspace_as_of(
        initial_entities,
        selected_transitions,
        as_of=normalized_cutoff,
    )
    if not projection.exact:
        raise _HistoricalProjectionUnavailable(
            "historical_snapshot_unavailable",
            *projection.diagnostics,
        )
    full_projection = project_workspace_as_of(
        initial_entities,
        selected_transitions,
        as_of=source_watermark,
    )
    if not full_projection.exact or any(
        full_projection.status_by_entity.get(current_work[str(row["id"])].entity_id)
        != str(row["status"])
        for row in selected_rows
    ):
        raise _HistoricalProjectionUnavailable(
            "historical_snapshot_unavailable",
            "transition_chain_does_not_reach_current_state",
        )

    projected_work_items: list[dict[str, Any]] = []
    for row in selected_rows:
        formal_id = str(row["id"])
        current_entity = current_work[formal_id]
        if formal_id not in reconstructed_work_ids:
            projected_work_items.append(dict(row))
            continue
        created = _row_timestamp(row, "created_at", "work_item")
        projected = dict(row)
        projected["status"] = projection.status_by_entity[current_entity.entity_id]
        applied_mutations = [
            (event_at, audit)
            for event_at, audit in audits_by_resource.get(formal_id, ())
            if event_at <= cutoff
            and str(audit.get("action") or "")
            in {"work_item.updated", "work_item.transitioned", "work_item.deleted"}
        ]
        projected["version"] = 1 + len(applied_mutations)
        projected["updated_at"] = (
            max([created, *(event_at for event_at, _audit in applied_mutations)])
            .isoformat()
            .replace("+00:00", "Z")
        )
        if any(
            event_at > cutoff
            and str(audit.get("action") or "") in {"work_item.transitioned", "work_item.deleted"}
            for event_at, audit in audits_by_resource.get(formal_id, ())
        ):
            projected["summary"] = ""
            diagnostics.append(f"historical_summary_omitted:{formal_id}")
        projected_work_items.append(projected)

    projected_rows = {
        "topics": [
            dict(item)
            for item in rows["topics"]
            if _row_timestamp(item, "created_at", "topic") <= cutoff
        ],
        "iterations": [
            dict(item)
            for item in rows["iterations"]
            if _row_timestamp(item, "created_at", "iteration") <= cutoff
        ],
        "work_items": projected_work_items,
        "links": [
            dict(item)
            for item in rows["links"]
            if _row_timestamp(item, "created_at", "link") <= cutoff
        ],
        "relations": [
            dict(item)
            for item in rows["relations"]
            if _row_timestamp(item, "created_at", "relation") <= cutoff
        ],
        "audits": [dict(audit) for event_at, audit in ordered_audits if event_at <= cutoff],
    }
    return _HistoricalRows(
        project=dict(project),
        rows=projected_rows,
        projection=projection,
        provable_from=provable_from,
        diagnostics=tuple(sorted(set(diagnostics))),
    )


class WorkspaceSourceRuntimeV2:
    """Read formal control-plane state and execute the governed Workspace V2 path."""

    def __init__(self, *, workspace: WorkspaceStore, index: WorkspaceV2Store) -> None:
        self.workspace = workspace
        self.index = index

    def search(
        self,
        *,
        project_id: str,
        query: str,
        as_of: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        allowed_acl_refs: list[str] | tuple[str, ...] | None = None,
        enforce_acl: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not query.strip():
            raise WorkspaceSourceRuntimeError("Workspace V2 query must not be empty")
        normalized_as_of, requested_as_of = _request_timestamp(as_of, "as_of")
        normalized_from, requested_from = _request_timestamp(date_from, "date_from")
        normalized_to, requested_to = _request_timestamp(date_to, "date_to")
        if (
            requested_from is not None
            and requested_to is not None
            and requested_from > requested_to
        ):
            raise WorkspaceSourceRuntimeError("date_range_invalid")
        project = self.workspace.get_project(project_id)
        if project is None:
            raise WorkspaceSourceRuntimeError("Workspace V2 project is unavailable")
        project_acl = str(project.get("acl_ref") or "")
        allowed = tuple(sorted({"public", *(allowed_acl_refs or ())}))
        if enforce_acl and project_acl not in set(allowed):
            return self._empty("acl_denied", "project_acl_denied")
        if not enforce_acl and project_acl:
            allowed = tuple(sorted({*allowed, project_acl}))
        timezone = str((project.get("settings") or {}).get("timezone") or "UTC")
        try:
            ZoneInfo(timezone)
        except Exception as error:
            raise WorkspaceSourceRuntimeError("Workspace V2 project timezone is invalid") from error
        source_rows = _all_rows(self.workspace, project_id)
        generation_id, watermark = _snapshot_generation(project, source_rows)
        source_watermark_at = _timestamp_value(watermark)
        project_created_at = _timestamp_value(project.get("created_at"))
        temporal_scope = {
            "as_of": normalized_as_of,
            "date_from": normalized_from,
            "date_to": normalized_to,
            "date_bounds": "inclusive",
            "date_field": "entity.effective_at",
            "provable_from": project_created_at.isoformat().replace("+00:00", "Z"),
            "source_watermark": watermark,
        }
        if (
            requested_to is not None
            and requested_to < project_created_at
            or requested_from is not None
            and requested_from > source_watermark_at
        ):
            return self._empty(
                "partial",
                "temporal_range_outside_provable_history",
                generation_id=generation_id,
                watermark=watermark,
                error_code="temporal_scope_unavailable",
                trace_extra={
                    "scope_filters": {
                        "as_of": as_of,
                        "date_from": date_from,
                        "date_to": date_to,
                    },
                    "temporal_scope": temporal_scope,
                    "diagnostics": ["date_range_has_no_authoritative_overlap"],
                },
            )
        historical_requested = requested_as_of is not None and requested_as_of < source_watermark_at
        temporal_scope["source_generation"] = generation_id
        temporal_scope["index_generation"] = generation_id
        scope = WorkspaceScope(
            project_id=project_id,
            acl_ref=project_acl,
            generation_id=generation_id,
            timezone=timezone,
        )
        projection_at = requested_as_of or datetime.now(UTC)
        working_project = project
        working_rows = source_rows
        view_watermark = watermark
        historical: _HistoricalRows | None = None
        if historical_requested:
            try:
                historical = _project_formal_rows_as_of(
                    project=project,
                    rows=source_rows,
                    scope=scope,
                    source_watermark=watermark,
                    cutoff=requested_as_of,
                    allowed_acl_refs=allowed,
                    enforce_acl=enforce_acl,
                )
            except _HistoricalProjectionUnavailable as error:
                return self._empty(
                    "partial",
                    error.reason,
                    generation_id=generation_id,
                    watermark=watermark,
                    error_code="temporal_projection_unavailable",
                    trace_extra={
                        "scope_filters": {
                            "as_of": as_of,
                            "date_from": date_from,
                            "date_to": date_to,
                        },
                        "temporal_scope": temporal_scope,
                        "diagnostics": list(error.diagnostics),
                    },
                )
            working_project = historical.project
            working_rows = historical.rows
            view_watermark = normalized_as_of or watermark
            temporal_scope["projection"] = "formal_rows_plus_audit_transitions"
            temporal_scope["projection_exact"] = historical.projection.exact
        else:
            temporal_scope["projection"] = "current_authoritative_rows"
            temporal_scope["projection_exact"] = True
        builder = _WorkspacePublicationBuilder(
            project=working_project,
            rows=working_rows,
            scope=scope,
            watermark=view_watermark,
            source_watermark=watermark,
            projection_at=projection_at,
            allowed_acl_refs=allowed,
            enforce_acl=enforce_acl,
        )
        publication, coverage, diagnostics, runtime_to_formal, confirmed_relations = builder.build()
        if historical is not None:
            with TemporaryDirectory(
                prefix="workspace-history-",
                dir=self.index.root,
            ) as temporary_root:
                root = Path(temporary_root)
                query_index = WorkspaceV2Store(
                    root / "workspace-history.sqlite3",
                    isolated_root=root,
                )
                query_index.publish(publication)
                result = WorkspaceRetrieverV2(query_index).search(
                    scope=scope,
                    query=query,
                    limit=limit,
                    as_of=normalized_as_of,
                    date_from=normalized_from,
                    date_to=normalized_to,
                    allowed_acl_refs=allowed,
                    enforce_acl=enforce_acl,
                )
            temporal_scope["index_materialization"] = "isolated_ephemeral"
        else:
            self.index.publish(publication)
            result = WorkspaceRetrieverV2(self.index).search(
                scope=scope,
                query=query,
                limit=limit,
                as_of=normalized_as_of,
                date_from=normalized_from,
                date_to=normalized_to,
                allowed_acl_refs=allowed,
                enforce_acl=enforce_acl,
            )
            temporal_scope["index_materialization"] = "active_generation"
        context = build_workspace_context(result)
        mapped = [self._candidate(candidate, runtime_to_formal) for candidate in result.candidates]
        intelligence = next(
            (
                item
                for item in publication.entities
                if item.entity_type == WorkspaceEntityType.INTELLIGENCE_RUN
            ),
            None,
        )
        projection_trace = (
            {
                "state_sha256": historical.projection.state_sha256,
                "applied_transition_ids": [
                    runtime_to_formal.get(item, item)
                    for item in historical.projection.applied_transition_ids
                ],
                "exact": historical.projection.exact,
                "diagnostics": list(historical.projection.diagnostics),
            }
            if historical is not None
            else None
        )
        combined_diagnostics = {
            *diagnostics,
            *(historical.diagnostics if historical is not None else ()),
        }
        return {
            "results": mapped,
            "context": context.model_dump(mode="json"),
            "trace": {
                **result.trace,
                "engine": WORKSPACE_SOURCE_RUNTIME_VERSION,
                "source_status": "complete" if mapped else "no_match",
                "index_generation": [generation_id],
                "source_generation": generation_id,
                "watermark": watermark,
                "default_engine": WORKSPACE_SOURCE_DEFAULT_ENGINE,
                "explicit_v2_required": True,
                "fallback_used": False,
                "formal_counts": {key: len(value) for key, value in source_rows.items()},
                "projected_formal_counts": {key: len(value) for key, value in working_rows.items()},
                "scope_filters": {
                    "as_of": as_of,
                    "date_from": date_from,
                    "date_to": date_to,
                },
                "temporal_scope": temporal_scope,
                "historical_projection": projection_trace,
                "diagnostics": sorted(combined_diagnostics),
            },
            "index_generation": [generation_id],
            "watermark": watermark,
            "coverage": [item.model_dump(mode="json") for item in coverage],
            "intelligence": (intelligence.metadata if intelligence is not None else None),
            "confirmed_relations": list(confirmed_relations)[: max(20, limit * 3)],
        }

    @staticmethod
    def _candidate(
        candidate: WorkspaceCandidate,
        runtime_to_formal: dict[str, str],
    ) -> dict[str, Any]:
        metadata = dict(candidate.metadata)
        entity_metadata = dict(metadata.get("entity_metadata") or {})
        return {
            "entity_id": runtime_to_formal.get(candidate.entity_id, candidate.entity_id),
            "source": "workspace",
            "entity_type": candidate.entity_type.value,
            "title": candidate.label,
            "subtitle": candidate.status,
            "snippet": str(metadata.get("text") or candidate.label),
            "locator": candidate.locator,
            "version": str(entity_metadata.get("version") or "") or None,
            "status": candidate.status,
            "score": candidate.score,
            "channels": list(candidate.channels),
            "roles": list(candidate.roles),
            "content_digest": candidate.content_sha256,
            "redactions": [],
            "metadata": {
                **metadata,
                "v2_entity_id": candidate.entity_id,
                "authority": candidate.authority.value,
                "source_entity_ids": list(candidate.source_entity_ids),
            },
        }

    @staticmethod
    def _empty(
        status: str,
        reason: str,
        *,
        generation_id: str = "not-indexed:workspace",
        watermark: str = "unavailable",
        error_code: str | None = None,
        trace_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "results": [],
            "context": {
                "availability": "UNAVAILABLE",
                "blocks": [],
                "citation_map": {},
                "missing": [reason],
                "reasoning_included": False,
                "mutation_applied": False,
            },
            "trace": {
                "engine": WORKSPACE_SOURCE_RUNTIME_VERSION,
                "source_status": status,
                "index_generation": [generation_id],
                "watermark": watermark,
                "error_code": error_code,
                "default_engine": WORKSPACE_SOURCE_DEFAULT_ENGINE,
                "explicit_v2_required": True,
                "fallback_used": False,
                "read_only": True,
                "mutation_applied": False,
                "reasoning_included": False,
                **(trace_extra or {}),
            },
            "index_generation": [generation_id],
            "watermark": watermark,
            "coverage": [],
            "intelligence": None,
            "confirmed_relations": [],
        }


__all__ = [
    "WORKSPACE_SOURCE_DEFAULT_ENGINE",
    "WORKSPACE_SOURCE_RUNTIME_VERSION",
    "WorkspaceSourceRuntimeError",
    "WorkspaceSourceRuntimeV2",
]
