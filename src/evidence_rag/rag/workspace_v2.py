"""Read-only Workspace control-plane retrieval and temporal context projection."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from ..security import redact_secrets
from ..workspace.store import WorkspaceStore
from .multisource import portable_locator

WORKSPACE_ENGINE_HEADER = "X-RAG-Workspace-Engine"
WORKSPACE_V2_VERSION = "workspace-control-plane-retrieval-v2"
WORKSPACE_CONTEXT_VERSION = "workspace-control-plane-context-v2"

_ENGINE_VALUES = frozenset({"v1", "v2"})
_TOKEN_RE = re.compile(r"[\w@./:-]+", re.UNICODE)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ABS_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'`(])(?:/(?:Users|home|private|tmp|var|etc|opt)/|[a-z]:[\\/]|"
    r"\\\\[^\\/\s]+[\\/][^\\/\s]+)"
)
_FILTER_RE = re.compile(
    r"(?P<key>status|owner|assignee|topic|iteration|due|key)\s*[:=]\s*(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)
_STOP = frozenset(
    {
        "a",
        "and",
        "current",
        "in",
        "of",
        "show",
        "the",
        "to",
        "workspace",
        "任务",
        "当前",
        "研究",
        "状态",
    }
)


class WorkspaceEngineOverrideError(ValueError):
    """Raised for an unsupported Workspace engine override."""


def validate_workspace_engine_override(value: str | None) -> Literal["v1", "v2"]:
    normalized = (value or "v1").strip().lower()
    if normalized not in _ENGINE_VALUES:
        raise WorkspaceEngineOverrideError(f"invalid {WORKSPACE_ENGINE_HEADER}; expected v1 or v2")
    return normalized  # type: ignore[return-value]


def _digest(*values: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _tokens(value: Any) -> tuple[str, ...]:
    return tuple(
        token
        for token in (match.casefold() for match in _TOKEN_RE.findall(str(value or "")))
        if len(token) > 1 and token not in _STOP
    )


def _safe(value: Any, limit: int = 1_200) -> tuple[str, tuple[str, ...]]:
    raw = str(value or "")
    redacted, findings = redact_secrets(raw)
    if _ABS_PATH_RE.search(redacted):
        redacted = _ABS_PATH_RE.sub(" [REDACTED_PATH] ", redacted)
        findings = [*findings, "absolute_path"]
    cleaned = _CONTROL_RE.sub(" ", redacted).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: max(1, limit - 1)].rstrip() + "…"
    return cleaned, tuple(sorted(set(findings)))


def _task(query: str) -> str:
    lowered = query.casefold()
    for task, markers in (
        ("blocker", ("block", "blocked", "dependency", "阻塞", "依赖")),
        ("next", ("next", "todo", "ready", "下一步", "待办")),
        ("coverage", ("coverage", "evidence", "link", "覆盖", "证据")),
        ("audit", ("audit", "changed", "history", "审计", "变更", "历史")),
        ("compare", ("compare", "difference", "对比", "差异")),
    ):
        if any(marker in lowered for marker in markers):
            return task
    return "current"


class WorkspaceStructuredRetrieverV2:
    """Search project planning state while preserving authoritative/derived roles."""

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def search(
        self,
        *,
        project_id: str,
        query: str,
        as_of: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        allowed_acl_refs: list[str] | None = None,
        enforce_acl: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        task = _task(query)
        filters = {
            match.group("key").casefold(): match.group("value")
            for match in _FILTER_RE.finditer(query)
        }
        query_tokens = set(_tokens(_FILTER_RE.sub(" ", query)))
        project = self.store.get_project(project_id)
        allowed = set(allowed_acl_refs or ()) | {"public"}
        if project is None or (enforce_acl and str(project.get("acl_ref") or "") not in allowed):
            return self._empty(task, as_of, denied=project is not None)
        topics = self.store.list_topics(project_id)
        iterations = self.store.list_iterations(project_id)
        work_items = self.store.list_work_items(project_id, limit=1_000)
        relations = self.store.list_relations(project_id, review_status="confirmed", limit=1_000)
        audits = [
            item
            for item in self.store.list_audits(project_id, limit=500)
            if (not date_from or str(item.get("created_at") or "") >= date_from)
            and (not date_to or str(item.get("created_at") or "") <= date_to)
            and (not as_of or str(item.get("created_at") or "") <= as_of)
        ]
        links = [
            link
            for iteration in iterations
            for link in self.store.list_iteration_links(str(iteration["id"]))
        ]
        units = [
            self._unit(
                entity=project,
                entity_type="Project",
                title=str(project.get("name") or project_id),
                text=f"{project.get('description') or ''} {project.get('owner') or ''}",
                roles=("authoritative", "project"),
                task=task,
                query_tokens=query_tokens,
                filters=filters,
                metadata={"project_id": project_id},
            )
        ]
        for entity_type, entities, title_field, text_fields, roles in (
            (
                "ResearchTopic",
                topics,
                "title",
                ("problem_statement", "objective", "owner", "status", "display_key"),
                ("authoritative", "topic"),
            ),
            (
                "ResearchIteration",
                iterations,
                "title",
                ("goal", "hypothesis", "summary", "owner", "status", "display_key"),
                ("authoritative", "iteration"),
            ),
            (
                "ResearchWorkItem",
                work_items,
                "title",
                (
                    "objective",
                    "summary",
                    "assignee",
                    "status",
                    "display_key",
                    "acceptance_criteria",
                ),
                ("authoritative", "work_item"),
            ),
        ):
            for entity in entities:
                text = " ".join(
                    json.dumps(entity.get(field), ensure_ascii=False)
                    if isinstance(entity.get(field), (list, dict))
                    else str(entity.get(field) or "")
                    for field in text_fields
                )
                units.append(
                    self._unit(
                        entity=entity,
                        entity_type=entity_type,
                        title=str(entity.get(title_field) or entity["id"]),
                        text=text,
                        roles=roles,
                        task=task,
                        query_tokens=query_tokens,
                        filters=filters,
                        metadata={
                            "project_id": project_id,
                            "topic_id": entity.get("topic_id"),
                            "iteration_id": entity.get("iteration_id"),
                            "due_at": entity.get("due_at") or entity.get("target_at"),
                        },
                    )
                )
        units = [item for item in units if item["filter_match"]]
        units = [item for item in units if item["score"] > 0 or not query_tokens]
        units.sort(
            key=lambda item: (
                -float(item["score"]),
                self._status_order(str(item["status"])),
                str(item["entity_id"]),
            )
        )
        selected = units[: min(100, max(1, limit))]
        selected_ids = {str(item["entity_id"]) for item in selected}
        selected_relations = [
            {
                "id": item["id"],
                "source": item["source_entity_id"],
                "predicate": item["predicate"],
                "target": item["target_entity_id"],
                "confidence": item["confidence"],
                "role": "confirmed",
            }
            for item in relations
            if item["source_entity_id"] in selected_ids or item["target_entity_id"] in selected_ids
        ][: max(20, limit * 3)]
        selected_links = [
            {
                "id": item["id"],
                "iteration_id": item["iteration_id"],
                "entity_id": item["entity_id"],
                "source_type": item["source_type"],
                "role": item["role"],
                "status": item["status"],
            }
            for item in links
            if item["iteration_id"] in selected_ids
        ][: max(20, limit * 3)]
        context = {
            "schema_version": WORKSPACE_CONTEXT_VERSION,
            "task": task,
            "as_of": as_of,
            "temporal_state": "CURRENT" if not as_of else "UNAVAILABLE_WITH_AUDIT_EVIDENCE",
            "blocks": [
                {
                    "entity_id": item["entity_id"],
                    "entity_type": item["entity_type"],
                    "locator": item["locator"],
                    "status": item["status"],
                    "roles": item["roles"],
                    "content_digest": item["content_digest"],
                    "text": item["snippet"],
                }
                for item in selected
            ],
            "confirmed_relations": selected_relations,
            "evidence_links": selected_links,
            "audit_evidence": [
                {
                    "id": item["id"],
                    "action": item["action"],
                    "resource_type": item["resource_type"],
                    "resource_id": item["resource_id"],
                    "created_at": item["created_at"],
                }
                for item in audits[:50]
            ],
            "missing": [] if selected else ["authorized_workspace_evidence"],
            "reasoning_included": False,
        }
        return {
            "results": selected,
            "context": context,
            "trace": {
                "engine": WORKSPACE_V2_VERSION,
                "task": task,
                "candidate_count": len(units),
                "selected_count": len(selected),
                "confirmed_relation_count": len(selected_relations),
                "evidence_link_count": len(selected_links),
                "index_generation": [
                    _digest(
                        project_id,
                        str(project.get("updated_at") or ""),
                        str(len(topics)),
                        str(len(iterations)),
                        str(len(work_items)),
                    )
                ],
                "release": {
                    "decision": "HOLD_DEFAULT_V1",
                    "default_engine": "v1",
                    "quality_qualified": False,
                    "rollback": f"omit {WORKSPACE_ENGINE_HEADER} or set it to v1",
                },
                "reasoning_included": False,
            },
        }

    def _unit(
        self,
        *,
        entity: dict[str, Any],
        entity_type: str,
        title: str,
        text: str,
        roles: tuple[str, ...],
        task: str,
        query_tokens: set[str],
        filters: dict[str, str],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        entity_id = str(entity["id"])
        safe_title, title_findings = _safe(title, 240)
        safe_text, text_findings = _safe(text)
        status = str(entity.get("status") or "active")
        unit_tokens = set(_tokens(f"{safe_title} {safe_text} {entity_type} {status}"))
        overlap = len(query_tokens & unit_tokens)
        coverage = overlap / max(1, len(query_tokens))
        task_boost = (
            0.20
            if (task == "blocker" and status == "blocked")
            or (task == "next" and status in {"ready", "backlog", "planned"})
            or (task == "current" and status in {"active", "running", "review", "validating"})
            else 0.0
        )
        score = min(1.0, 0.08 + coverage * 0.68 + min(overlap, 4) * 0.04 + task_boost)
        filter_match = self._filter_match(entity, filters)
        locator = portable_locator(
            f"workspace://{metadata['project_id']}/{entity_type.casefold()}/{entity_id}",
            entity_id,
        )
        return {
            "entity_id": entity_id,
            "source": "workspace",
            "entity_type": entity_type,
            "title": safe_title,
            "subtitle": f"{status} · {entity.get('owner') or entity.get('assignee') or ''}".strip(
                " ·"
            ),
            "snippet": safe_text,
            "locator": locator,
            "version": str(entity.get("version")) if entity.get("version") is not None else None,
            "status": status,
            "score": round(score, 6),
            "channels": sorted({"workspace_structured", *roles}),
            "roles": list(roles),
            "content_digest": _digest(
                entity_id,
                str(entity.get("updated_at") or ""),
                safe_text,
            ),
            "redactions": sorted(set(title_findings) | set(text_findings)),
            "metadata": metadata,
            "filter_match": filter_match,
        }

    @staticmethod
    def _filter_match(entity: dict[str, Any], filters: dict[str, str]) -> bool:
        for key, expected in filters.items():
            expected_folded = expected.casefold()
            if key == "key":
                actual = entity.get("display_key") or entity.get("id")
            elif key == "topic":
                actual = entity.get("topic_id") or entity.get("topic_key")
            elif key == "iteration":
                actual = entity.get("iteration_id") or entity.get("iteration_key")
            elif key == "due":
                actual = entity.get("due_at") or entity.get("target_at")
            else:
                actual = entity.get(key)
            if expected_folded not in str(actual or "").casefold():
                return False
        return True

    @staticmethod
    def _status_order(status: str) -> int:
        return {
            "active": 0,
            "running": 0,
            "review": 1,
            "validating": 1,
            "ready": 2,
            "blocked": 3,
            "planned": 4,
            "backlog": 5,
            "done": 6,
            "completed": 6,
            "archived": 9,
        }.get(status, 7)

    @staticmethod
    def _empty(task: str, as_of: str | None, *, denied: bool) -> dict[str, Any]:
        return {
            "results": [],
            "context": {
                "schema_version": WORKSPACE_CONTEXT_VERSION,
                "task": task,
                "as_of": as_of,
                "temporal_state": "UNAVAILABLE",
                "blocks": [],
                "confirmed_relations": [],
                "evidence_links": [],
                "audit_evidence": [],
                "missing": ["authorized_workspace_evidence"],
                "reasoning_included": False,
            },
            "trace": {
                "engine": WORKSPACE_V2_VERSION,
                "task": task,
                "candidate_count": 0,
                "selected_count": 0,
                "denied_project": denied,
                "index_generation": [],
                "release": {
                    "decision": "HOLD_DEFAULT_V1",
                    "default_engine": "v1",
                    "quality_qualified": False,
                },
                "reasoning_included": False,
            },
        }
