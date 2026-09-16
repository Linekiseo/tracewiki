from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from .models import (
    IterationCreate,
    IterationLinkCreate,
    IterationUpdate,
    ProjectCreate,
    ProjectUpdate,
    RelationCreate,
    RelationReview,
    TopicCreate,
    TopicUpdate,
    WorkItemCreate,
    WorkItemTransition,
    WorkItemUpdate,
)
from .store import WorkspaceStore


class WorkspaceError(ValueError):
    pass


class WorkspaceService:
    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def ensure_default_project(self) -> dict[str, Any]:
        existing = self.store.get_project("project-rag")
        if existing:
            return existing
        return self.create_project(
            ProjectCreate(
                id="project-rag",
                name="多源协同 RAG",
                description="代码、Codex 会话、实验与科研文档的统一证据工作台。",
            ),
            audit=False,
        )

    def create_project(self, request: ProjectCreate, *, audit: bool = True) -> dict[str, Any]:
        project_id = request.id or self._project_id(request.name)
        if self.store.get_project(project_id):
            raise WorkspaceError("project id already exists")
        record = request.model_dump()
        record.update(id=project_id, acl_ref=request.acl_ref or f"project:{project_id}")
        result = self.store.create_project(record)
        if audit:
            self._audit(
                project_id, "project.created", "project", project_id, {"name": request.name}
            )
        return result

    def update_project(self, project_id: str, request: ProjectUpdate) -> dict[str, Any]:
        self._require_project(project_id)
        result = self.store.update_project(project_id, request.model_dump(exclude_none=True))
        self._audit(
            project_id,
            "project.updated",
            "project",
            project_id,
            request.model_dump(exclude_none=True),
        )
        return result or {}

    def create_topic(self, request: TopicCreate) -> dict[str, Any]:
        self._require_project(request.project_id)
        token = uuid4().hex
        record = request.model_dump()
        record.update(
            id=f"topic://{request.project_id}/{token}",
            display_key=f"TOPIC-{token[:8].upper()}",
        )
        result = self.store.create_topic(record)
        self._audit(
            request.project_id,
            "topic.created",
            "research_topic",
            result["id"],
            {"title": request.title},
        )
        return result

    def update_topic(self, topic_id: str, request: TopicUpdate) -> dict[str, Any]:
        topic = self._require_topic(topic_id)
        changes = request.model_dump(exclude_none=True)
        result = self.store.update_topic(topic_id, changes)
        self._audit(topic["project_id"], "topic.updated", "research_topic", topic_id, changes)
        return result or {}

    def delete_topic(self, topic_id: str) -> dict[str, Any]:
        """Remove a topic from active planning while preserving its research audit trail."""
        topic = self._require_topic(topic_id)
        iterations = self.store.list_iterations(topic["project_id"], topic_id=topic_id)
        work_items = self.store.list_work_items(topic["project_id"], topic_id=topic_id)
        for iteration in iterations:
            self.store.update_iteration(iteration["id"], {"status": "archived"})
        for item in work_items:
            self.store.update_work_item(item["id"], item["version"], {"status": "cancelled"})
        result = self.store.update_topic(topic_id, {"status": "archived"})
        self._audit(
            topic["project_id"],
            "topic.deleted",
            "research_topic",
            topic_id,
            {"iterations_archived": len(iterations), "work_items_cancelled": len(work_items)},
        )
        return {**(result or topic), "deleted": True}

    def create_iteration(self, request: IterationCreate) -> dict[str, Any]:
        self._require_project(request.project_id)
        topic = self._require_topic(request.topic_id)
        if topic["project_id"] != request.project_id:
            raise WorkspaceError("topic does not belong to project")
        token = uuid4().hex
        record = request.model_dump()
        record.update(
            id=f"iteration://{request.project_id}/{token}",
            display_key=f"ITER-{token[:8].upper()}",
        )
        result = self.store.create_iteration(record)
        self._audit(
            request.project_id,
            "iteration.created",
            "research_iteration",
            result["id"],
            {"title": request.title},
        )
        return result

    def update_iteration(self, iteration_id: str, request: IterationUpdate) -> dict[str, Any]:
        iteration = self._require_iteration(iteration_id)
        changes = request.model_dump(exclude_none=True)
        if changes.get("status") == "completed" and not changes.get("completed_at"):
            from ..storage import utc_now

            changes["completed_at"] = utc_now()
            changes.setdefault("progress", 100)
        result = self.store.update_iteration(iteration_id, changes)
        self._audit(
            iteration["project_id"],
            "iteration.updated",
            "research_iteration",
            iteration_id,
            changes,
        )
        return result or {}

    def delete_iteration(self, iteration_id: str) -> dict[str, Any]:
        """Archive an iteration and cancel only its still-active execution tasks."""
        iteration = self._require_iteration(iteration_id)
        work_items = self.store.list_work_items(iteration["project_id"], iteration_id=iteration_id)
        for item in work_items:
            self.store.update_work_item(item["id"], item["version"], {"status": "cancelled"})
        result = self.store.update_iteration(iteration_id, {"status": "archived"})
        self._audit(
            iteration["project_id"],
            "iteration.deleted",
            "research_iteration",
            iteration_id,
            {"work_items_cancelled": len(work_items)},
        )
        return {**(result or iteration), "deleted": True}

    def create_work_item(self, request: WorkItemCreate) -> dict[str, Any]:
        self._require_project(request.project_id)
        if request.topic_id:
            topic = self._require_topic(request.topic_id)
            if topic["project_id"] != request.project_id:
                raise WorkspaceError("topic does not belong to project")
        if request.iteration_id:
            iteration = self._require_iteration(request.iteration_id)
            if iteration["project_id"] != request.project_id:
                raise WorkspaceError("iteration does not belong to project")
            if request.topic_id and iteration["topic_id"] != request.topic_id:
                raise WorkspaceError("iteration does not belong to topic")
        token = uuid4().hex
        record = request.model_dump()
        record.update(
            id=f"work-item://{request.project_id}/{token}",
            display_key=f"TASK-{token[:8].upper()}",
        )
        result = self.store.create_work_item(record)
        self._audit(
            request.project_id,
            "work_item.created",
            "research_work_item",
            result["id"],
            {"title": request.title, "kind": request.kind},
        )
        return result

    def update_work_item(self, work_item_id: str, request: WorkItemUpdate) -> dict[str, Any]:
        item = self._require_work_item(work_item_id)
        changes = request.model_dump(exclude_none=True)
        expected_version = changes.pop("expected_version")
        result = self.store.update_work_item(work_item_id, expected_version, changes)
        if not result:
            raise WorkspaceError("work item changed; reload before editing")
        self._audit(
            item["project_id"],
            "work_item.updated",
            "research_work_item",
            work_item_id,
            changes,
        )
        return result

    def transition_work_item(
        self, work_item_id: str, request: WorkItemTransition
    ) -> dict[str, Any]:
        item = self._require_work_item(work_item_id)
        changes: dict[str, Any] = {"status": request.status}
        if request.summary is not None:
            changes["summary"] = request.summary
        result = self.store.update_work_item(work_item_id, request.expected_version, changes)
        if not result:
            raise WorkspaceError("work item changed; reload before updating status")
        self._audit(
            item["project_id"],
            "work_item.transitioned",
            "research_work_item",
            work_item_id,
            {"from": item["status"], "to": request.status},
        )
        return result

    def delete_work_item(self, work_item_id: str, expected_version: int) -> dict[str, Any]:
        """Soft-delete an execution task so historical Codex evidence remains addressable."""
        item = self._require_work_item(work_item_id)
        result = self.store.update_work_item(
            work_item_id, expected_version, {"status": "cancelled"}
        )
        if not result:
            raise WorkspaceError("work item changed; reload before deleting")
        self._audit(
            item["project_id"],
            "work_item.deleted",
            "research_work_item",
            work_item_id,
            {"from": item["status"], "to": "cancelled"},
        )
        return {**result, "deleted": True}

    def link_iteration(self, request: IterationLinkCreate) -> dict[str, Any]:
        iteration = self._require_iteration(request.iteration_id)
        if not self.store.entity_exists(request.entity_id):
            raise WorkspaceError("linked entity does not exist in an active source generation")
        record = request.model_dump()
        record.update(id=f"link://{uuid4().hex}", project_id=iteration["project_id"])
        result = self.store.create_iteration_link(record)
        self._audit(
            iteration["project_id"],
            "iteration.evidence_linked",
            "iteration_link",
            result["id"],
            {"iteration_id": request.iteration_id, "entity_id": request.entity_id},
        )
        return result

    def unlink_iteration(self, link_id: str, project_id: str) -> bool:
        removed = self.store.delete_iteration_link(link_id)
        if removed:
            self._audit(project_id, "iteration.evidence_unlinked", "iteration_link", link_id, {})
        return removed

    def create_relation(self, request: RelationCreate) -> dict[str, Any]:
        self._require_project(request.project_id)
        for entity_id in (request.source_entity_id, request.target_entity_id):
            if not self.store.entity_exists(entity_id):
                raise WorkspaceError(f"relation endpoint does not exist: {entity_id}")
        if request.evidence_entity_id and not self.store.entity_exists(request.evidence_entity_id):
            raise WorkspaceError("relation evidence entity does not exist")
        record = request.model_dump()
        record["id"] = f"edge://{uuid4().hex}"
        result = self.store.create_relation(record)
        self._audit(
            request.project_id,
            "relation.created",
            "relation",
            result["id"],
            {"predicate": request.predicate},
        )
        return result

    def review_relation(self, edge_id: str, request: RelationReview) -> dict[str, Any]:
        edge = self.store.get_relation(edge_id)
        if not edge:
            raise WorkspaceError("relation not found")
        result = self.store.review_relation(
            edge_id, request.review_status, request.reviewer, request.note
        )
        self._audit(
            edge["project_id"], "relation.reviewed", "relation", edge_id, request.model_dump()
        )
        return result or {}

    def dashboard(self, project_id: str) -> dict[str, Any]:
        project = self._require_project(project_id)
        topics = self.store.list_topics(project_id)
        iterations = self.store.list_iterations(project_id)
        work_items = self.store.list_work_items(project_id)
        source_stats = self.store.project_source_stats(project_id)
        return {
            "project": {
                **project,
                "topic_count": len(topics),
                "active_iteration_count": sum(
                    item["status"] in {"active", "validating"} for item in iterations
                ),
                "active_work_item_count": sum(
                    item["status"] in {"ready", "running", "review", "blocked"}
                    for item in work_items
                ),
                "current_topic": topics[0]["title"] if topics else None,
                **source_stats,
            },
            "stats": {
                **source_stats,
                "topics": len(topics),
                "active_topics": sum(item["status"] == "active" for item in topics),
                "iterations": len(iterations),
                "active_iterations": sum(
                    item["status"] in {"active", "validating"} for item in iterations
                ),
                "linked_evidence": sum(item["evidence_count"] for item in iterations),
                "work_items": len(work_items),
                "active_work_items": sum(
                    item["status"] in {"ready", "running", "review", "blocked"}
                    for item in work_items
                ),
            },
            "active_iterations": iterations[:8],
            "active_work_items": work_items[:12],
            "recent_topics": topics[:8],
            "recent_activity": self.store.list_audits(project_id, 12),
        }

    def _require_project(self, project_id: str) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if not project:
            raise WorkspaceError("project not found")
        return project

    def _require_topic(self, topic_id: str) -> dict[str, Any]:
        topic = self.store.get_topic(topic_id)
        if not topic:
            raise WorkspaceError("research topic not found")
        return topic

    def _require_iteration(self, iteration_id: str) -> dict[str, Any]:
        iteration = self.store.get_iteration(iteration_id)
        if not iteration:
            raise WorkspaceError("research iteration not found")
        return iteration

    def _require_work_item(self, work_item_id: str) -> dict[str, Any]:
        item = self.store.get_work_item(work_item_id)
        if not item:
            raise WorkspaceError("research work item not found")
        return item

    def _audit(
        self,
        project_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        detail: dict[str, Any],
        actor: str = "RAG Core",
    ) -> None:
        self.store.create_audit(
            {
                "id": f"audit://{uuid4().hex}",
                "project_id": project_id,
                "actor": actor,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "detail": detail,
                "trace_id": uuid4().hex,
            }
        )

    def record_query_history(
        self,
        project_id: str,
        *,
        query_id: str,
        trace_id: str,
        detail: dict[str, Any],
        clarification: bool = False,
    ) -> None:
        """Persist a safe query projection in the existing project audit ledger."""

        self._require_project(project_id)
        self.store.create_audit(
            {
                "id": f"audit://{uuid4().hex}",
                "project_id": project_id,
                "actor": "RAG Query",
                "action": "query.clarification" if clarification else "query.completed",
                "resource_type": "query_interaction",
                "resource_id": query_id,
                "detail": detail,
                "trace_id": trace_id,
            }
        )

    def list_query_history(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self._require_project(project_id)
        return self.store.list_query_history(project_id, limit)

    @staticmethod
    def _project_id(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]
        return f"project-{slug or uuid4().hex[:8]}"
