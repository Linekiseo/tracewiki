"""Frozen source contracts for Workspace control-plane RAG."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Self
from urllib.parse import quote, unquote

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

WORKSPACE_CONTRACT_VERSION = "workspace-source-contract-v2"
WORKSPACE_SCHEMA_VERSION = "workspace-isolated-schema-v2"
WORKSPACE_VIEW_BUILDER_VERSION = "workspace-view-builder-v2"
WORKSPACE_POLICY_VERSION = "workspace-policy-v2"

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=240)]
BoundedText = Annotated[str, StringConstraints(max_length=12_000)]
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9._:@+-]+")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        default=lambda item: (
            item.model_dump(mode="json") if isinstance(item, BaseModel) else str(item)
        ),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _validate_time(value: str | None) -> None:
    if value is None:
        return
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Workspace times must be timezone-aware")


class _FrozenWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class WorkspaceAuthority(StrEnum):
    AUTHORITATIVE = "A_AUTHORITATIVE"
    OBSERVED_EXTERNAL = "B_OBSERVED_EXTERNAL"
    REVIEWED_RELATION = "C_REVIEWED_RELATION"
    DERIVED_INTELLIGENCE = "D_DERIVED_INTELLIGENCE"
    INFERRED_SUGGESTION = "E_INFERRED_SUGGESTION"


class WorkspaceEntityType(StrEnum):
    PROJECT = "project"
    TOPIC = "topic"
    ITERATION = "iteration"
    WORK_ITEM = "work_item"
    ACCEPTANCE_CRITERION = "acceptance_criterion"
    ACCEPTANCE_CHECK = "acceptance_check"
    DEPENDENCY = "dependency"
    BLOCKER = "blocker"
    RISK = "risk"
    EVIDENCE_REQUIREMENT = "evidence_requirement"
    EVIDENCE_LINK = "evidence_link"
    DECISION = "decision"
    OUTCOME = "outcome"
    TRANSITION = "transition"
    SNAPSHOT = "snapshot"
    INTELLIGENCE_RUN = "intelligence_run"
    POLICY_VERSION = "policy_version"


class WorkspaceEdgeType(StrEnum):
    HAS_TOPIC = "has_topic"
    HAS_ITERATION = "has_iteration"
    HAS_WORK_ITEM = "has_work_item"
    HAS_ACCEPTANCE = "has_acceptance"
    CHECKS = "checks"
    DEPENDS_ON = "depends_on"
    BLOCKED_BY = "blocked_by"
    HAS_RISK = "has_risk"
    HAS_REQUIREMENT = "has_requirement"
    SATISFIED_BY = "satisfied_by"
    HAS_DECISION = "has_decision"
    SUPPORTED_BY = "supported_by"
    PRODUCES_OUTCOME = "produces_outcome"
    TRANSITION_OF = "transition_of"
    SNAPSHOT_CONTAINS = "snapshot_contains"
    DERIVED_FROM = "derived_from"
    SUPERSEDES = "supersedes"


class WorkspaceLinkStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    STALE = "stale"


class WorkspaceCheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    WAIVED = "waived"


class WorkspaceScope(_FrozenWorkspace):
    project_id: Identifier
    acl_ref: Identifier
    generation_id: Identifier
    timezone: Identifier = "UTC"

    def permits(self, allowed_acl_refs: tuple[str, ...], *, enforce_acl: bool) -> bool:
        return not enforce_acl or self.acl_ref in set(allowed_acl_refs)


def workspace_locator(
    *,
    scope: WorkspaceScope,
    entity_type: WorkspaceEntityType,
    stable_id: str,
    version: int,
) -> str:
    return (
        f"workspace://{quote(scope.project_id, safe='')}/"
        f"{entity_type.value}/{quote(stable_id, safe='')}?version={version}"
    )


def parse_workspace_locator(locator: str) -> tuple[str, WorkspaceEntityType, str, int]:
    match = re.fullmatch(
        r"workspace://([^/]+)/([^/]+)/([^?]+)\?version=([1-9][0-9]*)",
        locator,
    )
    if not match:
        raise ValueError("Workspace locator is not canonical")
    project_id, kind, stable_id, version = match.groups()
    decoded_project = unquote(project_id)
    decoded_id = unquote(stable_id)
    entity_type = WorkspaceEntityType(kind)
    if quote(decoded_project, safe="") != project_id or quote(decoded_id, safe="") != stable_id:
        raise ValueError("Workspace locator does not round-trip canonically")
    return decoded_project, entity_type, decoded_id, int(version)


class WorkspaceEntity(_FrozenWorkspace):
    entity_id: Identifier
    stable_id: Identifier
    entity_type: WorkspaceEntityType
    scope: WorkspaceScope
    version: int = Field(ge=1)
    display_key: Identifier
    label: BoundedText
    text: BoundedText = ""
    status: Identifier
    authority: WorkspaceAuthority
    parent_id: Identifier | None = None
    topic_id: Identifier | None = None
    iteration_id: Identifier | None = None
    work_item_id: Identifier | None = None
    owner: BoundedText | None = None
    assignee: BoundedText | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    due_at: str | None = None
    effective_at: str
    expires_at: str | None = None
    current: bool = True
    archived: bool = False
    locator: str
    source_sha256: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str

    def content_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"content_sha256"})

    @model_validator(mode="after")
    def _identity(self) -> WorkspaceEntity:
        if not _SAFE_ID_RE.fullmatch(self.entity_id):
            raise ValueError("Workspace entity_id is not portable")
        if not _SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("Workspace source digest is malformed")
        for value in (self.effective_at, self.due_at, self.expires_at):
            _validate_time(value)
        parsed = parse_workspace_locator(self.locator)
        if parsed != (
            self.scope.project_id,
            self.entity_type,
            self.stable_id,
            self.version,
        ):
            raise ValueError("Workspace locator identity mismatch")
        if self.content_sha256 != canonical_sha256(self.content_payload()):
            raise ValueError("Workspace entity content digest mismatch")
        if self.authority == WorkspaceAuthority.DERIVED_INTELLIGENCE:
            required = {"policy_version", "input_snapshot_sha256", "generated_at"}
            if not required.issubset(self.metadata) or self.expires_at is None:
                raise ValueError("derived intelligence requires policy/input/expiry authority")
        if self.entity_type == WorkspaceEntityType.EVIDENCE_LINK:
            required = {
                "requirement_id",
                "external_entity_id",
                "external_version",
                "external_generation",
                "source_domain",
                "role",
                "link_status",
                "reviewed",
                "fresh",
                "independence_group",
            }
            if not required.issubset(self.metadata):
                raise ValueError("evidence links require pinned reviewed coverage metadata")
        return self


def build_workspace_entity(**payload: Any) -> WorkspaceEntity:
    payload = dict(payload)
    if isinstance(payload.get("scope"), dict):
        payload["scope"] = WorkspaceScope.model_validate(payload["scope"])
    draft = WorkspaceEntity.model_construct(**payload, content_sha256="")
    content_payload = draft.model_dump(mode="json", exclude={"content_sha256"})
    content_payload["content_sha256"] = canonical_sha256(content_payload)
    return WorkspaceEntity.model_validate(content_payload)


class WorkspaceEdge(_FrozenWorkspace):
    edge_id: Identifier
    edge_type: WorkspaceEdgeType
    scope: WorkspaceScope
    source_id: Identifier
    target_id: Identifier
    reviewed: bool = True
    current: bool = True
    effective_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str

    def content_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"content_sha256"})

    @model_validator(mode="after")
    def _edge_identity(self) -> WorkspaceEdge:
        _validate_time(self.effective_at)
        if self.source_id == self.target_id:
            raise ValueError("Workspace edge cannot self-reference")
        if self.content_sha256 != canonical_sha256(self.content_payload()):
            raise ValueError("Workspace edge digest mismatch")
        return self


def build_workspace_edge(**payload: Any) -> WorkspaceEdge:
    payload = dict(payload)
    if isinstance(payload.get("scope"), dict):
        payload["scope"] = WorkspaceScope.model_validate(payload["scope"])
    draft = WorkspaceEdge.model_construct(**payload, content_sha256="")
    content_payload = draft.model_dump(mode="json", exclude={"content_sha256"})
    content_payload["content_sha256"] = canonical_sha256(content_payload)
    return WorkspaceEdge.model_validate(content_payload)


class WorkspaceRetrievalUnit(_FrozenWorkspace):
    unit_id: Identifier
    entity_id: Identifier
    scope: WorkspaceScope
    unit_type: WorkspaceEntityType
    authority: WorkspaceAuthority
    exact_terms: tuple[BoundedText, ...]
    sparse_text: BoundedText
    dense_text: BoundedText
    structured: dict[str, Any]
    locator: str
    content_sha256: str

    def content_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"content_sha256"})

    @model_validator(mode="after")
    def _unit_identity(self) -> WorkspaceRetrievalUnit:
        if self.content_sha256 != canonical_sha256(self.content_payload()):
            raise ValueError("Workspace retrieval unit digest mismatch")
        return self


def build_workspace_retrieval_unit(**payload: Any) -> WorkspaceRetrievalUnit:
    payload = dict(payload)
    if isinstance(payload.get("scope"), dict):
        payload["scope"] = WorkspaceScope.model_validate(payload["scope"])
    draft = WorkspaceRetrievalUnit.model_construct(**payload, content_sha256="")
    content_payload = draft.model_dump(mode="json", exclude={"content_sha256"})
    content_payload["content_sha256"] = canonical_sha256(content_payload)
    return WorkspaceRetrievalUnit.model_validate(content_payload)


class WorkspacePublication(_FrozenWorkspace):
    publication_id: Identifier
    scope: WorkspaceScope
    entities: tuple[WorkspaceEntity, ...]
    edges: tuple[WorkspaceEdge, ...]
    retrieval_units: tuple[WorkspaceRetrievalUnit, ...]
    builder_version: Identifier = WORKSPACE_VIEW_BUILDER_VERSION
    published_at: str
    publication_sha256: str

    def publication_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"publication_sha256"})

    @model_validator(mode="after")
    def _publication(self) -> WorkspacePublication:
        _validate_time(self.published_at)
        if len({item.entity_id for item in self.entities}) != len(self.entities):
            raise ValueError("Workspace publication entity membership is not unique")
        entity_ids = {item.entity_id for item in self.entities}
        if any(item.scope != self.scope for item in self.entities):
            raise ValueError("Workspace publication mixes entity scopes")
        if any(item.scope != self.scope for item in self.edges):
            raise ValueError("Workspace publication mixes edge scopes")
        if any(item.scope != self.scope for item in self.retrieval_units):
            raise ValueError("Workspace publication mixes unit scopes")
        if any(
            edge.source_id not in entity_ids or edge.target_id not in entity_ids
            for edge in self.edges
        ):
            raise ValueError("Workspace edge endpoint is outside publication authority")
        if any(item.entity_id not in entity_ids for item in self.retrieval_units):
            raise ValueError("Workspace unit is outside publication authority")
        if self.publication_sha256 != canonical_sha256(self.publication_payload()):
            raise ValueError("Workspace publication digest mismatch")
        return self


def build_workspace_publication(**payload: Any) -> WorkspacePublication:
    payload = dict(payload)
    if isinstance(payload.get("scope"), dict):
        payload["scope"] = WorkspaceScope.model_validate(payload["scope"])
    draft = WorkspacePublication.model_construct(**payload, publication_sha256="")
    publication_payload = draft.model_dump(mode="json", exclude={"publication_sha256"})
    publication_payload["publication_sha256"] = canonical_sha256(publication_payload)
    return WorkspacePublication.model_validate(publication_payload)


class WorkspaceTombstone(_FrozenWorkspace):
    tombstone_id: Identifier
    scope: WorkspaceScope
    target_entity_id: Identifier
    reason: Identifier
    effective_at: str
    content_sha256: str

    def content_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"content_sha256"})

    @model_validator(mode="after")
    def _tombstone(self) -> WorkspaceTombstone:
        _validate_time(self.effective_at)
        if self.content_sha256 != canonical_sha256(self.content_payload()):
            raise ValueError("Workspace tombstone digest mismatch")
        return self


def build_workspace_tombstone(**payload: Any) -> WorkspaceTombstone:
    payload = dict(payload)
    if isinstance(payload.get("scope"), dict):
        payload["scope"] = WorkspaceScope.model_validate(payload["scope"])
    draft = WorkspaceTombstone.model_construct(**payload, content_sha256="")
    content_payload = draft.model_dump(mode="json", exclude={"content_sha256"})
    content_payload["content_sha256"] = canonical_sha256(content_payload)
    return WorkspaceTombstone.model_validate(content_payload)


__all__ = [
    "WORKSPACE_CONTRACT_VERSION",
    "WORKSPACE_POLICY_VERSION",
    "WORKSPACE_SCHEMA_VERSION",
    "WORKSPACE_VIEW_BUILDER_VERSION",
    "WorkspaceAuthority",
    "WorkspaceCheckStatus",
    "WorkspaceEdge",
    "WorkspaceEdgeType",
    "WorkspaceEntity",
    "WorkspaceEntityType",
    "WorkspaceLinkStatus",
    "WorkspacePublication",
    "WorkspaceRetrievalUnit",
    "WorkspaceScope",
    "WorkspaceTombstone",
    "build_workspace_edge",
    "build_workspace_entity",
    "build_workspace_publication",
    "build_workspace_retrieval_unit",
    "build_workspace_tombstone",
    "canonical_json_bytes",
    "canonical_sha256",
    "parse_workspace_locator",
    "workspace_locator",
]
