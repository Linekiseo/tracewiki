from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

from ..platform.models import GlobalSearchRequest
from ..storage import utc_now
from ..workspace.models import (
    IterationLinkCreate,
    WorkItemCreate,
    WorkItemTransition,
    WorkItemUpdate,
)
from ..workspace.service import WorkspaceError, WorkspaceService
from .models import ApprovalDecision, ExecutionCreate, ExecutionReport, TokenCreate
from .store import CodexBridgeStore


class CodexBridgeError(ValueError):
    pass


class CodexBridgeService:
    CAPABILITIES = (
        "project_get_context",
        "project_get_intelligence",
        "research_list_work",
        "research_get_work",
        "evidence_search",
        "wiki_navigation_status",
        "wiki_search",
        "wiki_read",
        "wiki_follow",
        "wiki_navigate",
        "evidence_read",
        "wiki_propose_patch",
        "execution_get",
        "review_list_pending",
        "research_create_work",
        "research_update_work",
        "research_transition_work",
        "evidence_link",
        "execution_claim",
        "execution_report",
        "execution_request_review",
    )
    # 0.2.0 is the first server contract that exposes the governed Agent-Native
    # Wiki tool family.  Keep this independent from the plugin manifest's build
    # metadata (``+wiki.20260803``) while making capability snapshots and MCP
    # handshakes unambiguous to clients.
    MCP_SERVER_VERSION = "0.2.0"
    MCP_PROTOCOL_VERSION = "2025-06-18"

    def __init__(
        self,
        store: CodexBridgeStore,
        workspace: WorkspaceService,
        platform,
        *,
        wiki=None,
        intelligence=None,
        project_root: Path,
        artifact_dir: Path,
        plugin_dir: Path | None = None,
        token_configured: bool = False,
        configured_authority_id: str | None = None,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.platform = platform
        self.wiki = wiki
        self.intelligence = intelligence
        self.project_root = project_root
        self.artifact_dir = artifact_dir
        self.plugin_dir = plugin_dir or Path.home() / "plugins" / "research-project-bridge"
        self.token_configured = token_configured
        self.configured_authority_id = configured_authority_id

    def status(self, project_id: str = "project-rag") -> dict[str, Any]:
        from .runner import resolve_codex_binary

        plugin_manifest = self.plugin_dir / ".codex-plugin" / "plugin.json"
        plugin: dict[str, Any] = {}
        if plugin_manifest.is_file():
            try:
                plugin = json.loads(plugin_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                plugin = {}
        clients = [
            {
                **item,
                "name": self._safe_reported_identity(item.get("name")),
                "version": (
                    self._safe_reported_identity(item.get("version"))
                    if item.get("version") is not None
                    else None
                ),
            }
            for item in self.store.list_project_clients(project_id)
        ]
        snapshots = self.store.list_capability_snapshots(project_id)
        codex_binary = resolve_codex_binary()
        project_default_token = self.token_configured and project_id == "project-rag"
        static_snapshots = (
            [
                item
                for item in snapshots
                if item.get("client_id") == "client://codex-local"
                and item.get("authority_id") == self.configured_authority_id
            ]
            if project_default_token
            else []
        )
        static_snapshot = max(
            static_snapshots, key=lambda item: str(item.get("observed_at") or ""), default=None
        )
        has_active_token = project_default_token or any(
            token.get("active") for client in clients for token in client.get("tokens", [])
        )
        active_authority_ids = {
            token["id"]
            for client in clients
            for token in client.get("tokens", [])
            if token.get("active")
        }
        authorized_snapshots = [
            snapshot
            for snapshot in snapshots
            if snapshot.get("authority_id") in active_authority_ids
        ]
        if static_snapshot is not None:
            authorized_snapshots.append(static_snapshot)
        connected_snapshots = [
            snapshot
            for snapshot in authorized_snapshots
            if self.store.capability_snapshot_is_fresh(snapshot)
        ]
        stale_snapshots = [
            snapshot
            for snapshot in authorized_snapshots
            if not self.store.capability_snapshot_is_fresh(snapshot)
        ]
        has_connected_client = bool(connected_snapshots)
        discovered_capabilities = sorted(
            {
                capability
                for snapshot in connected_snapshots
                for capability in snapshot.get("capabilities", [])
            }
        )
        plugin_installed = plugin_manifest.is_file()
        if has_connected_client:
            availability = "ready"
            reason = "connected"
        elif has_active_token and stale_snapshots:
            availability = "disconnected"
            reason = "capability_snapshot_stale"
        elif has_active_token:
            availability = "disconnected"
            reason = "handshake_not_observed"
        elif plugin_installed:
            availability = "unauthorized"
            reason = "project_token_missing"
        else:
            availability = "unavailable"
            reason = "plugin_missing"
        sync_status = self._sync_status(project_id)
        return {
            "mcp": {
                "endpoint": "http://127.0.0.1:8000/mcp",
                "transport": "streamable-http",
                "status": (
                    "ready"
                    if has_connected_client
                    else "disconnected"
                    if has_active_token
                    else "needs-token"
                ),
                "authentication": "configured" if has_active_token else "missing",
            },
            "plugin": {
                "installed": plugin_installed,
                "location": "configured" if plugin_installed else "not-configured",
                "version": plugin.get("version"),
                "name": plugin.get("name", "research-project-bridge"),
            },
            "codex_cli": {
                "available": bool(codex_binary),
                "location": "available-on-host" if codex_binary else "not-detected",
            },
            "permissions": {
                "read": "automatic",
                "write": "confirmation-required",
                "delete": "unavailable",
                "sandbox": "read-only",
            },
            "project_id": project_id,
            "clients": clients,
            "capabilities": discovered_capabilities,
            "capability_snapshots": [self._public_snapshot(item) for item in connected_snapshots],
            "availability": availability,
            "reason": reason,
            "last_connected_at": max(
                [snapshot.get("observed_at") or "" for snapshot in authorized_snapshots],
                default="",
            )
            or None,
            "sync": sync_status,
        }

    def agents(self, project_id: str) -> list[dict[str, Any]]:
        status = self.status(project_id)
        executions = self.store.list_executions(project_id, limit=200)
        clients = status["clients"]
        snapshots = self.store.list_capability_snapshots(project_id)
        if not clients:
            local_client_id = "client://codex-local"
            local_snapshot = max(
                (
                    item
                    for item in snapshots
                    if self.token_configured
                    and project_id == "project-rag"
                    and item.get("client_id") == local_client_id
                    and item.get("authority_id") == self.configured_authority_id
                ),
                key=lambda item: str(item.get("observed_at") or ""),
                default=None,
            )
            local_snapshot_fresh = bool(
                local_snapshot and self.store.capability_snapshot_is_fresh(local_snapshot)
            )
            return [
                {
                    "id": "agent://codex",
                    "client_id": local_client_id if self.token_configured else None,
                    "name": "Codex",
                    "connector": "research-project-bridge",
                    "version": status["plugin"].get("version"),
                    "availability": status["availability"],
                    "reason": status["reason"],
                    "capabilities": (
                        list(local_snapshot.get("capabilities", [])) if local_snapshot_fresh else []
                    ),
                    "capability_snapshot": (
                        self._public_snapshot(local_snapshot, authoritative=local_snapshot_fresh)
                        if local_snapshot
                        else None
                    ),
                    "scope": {
                        "project_id": project_id,
                        "token_scopes": (
                            ["read", "write"]
                            if self.token_configured and project_id == "project-rag"
                            else []
                        ),
                    },
                    "last_seen_at": local_snapshot.get("observed_at") if local_snapshot else None,
                    "recent_executions": [],
                    "active_execution_count": 0,
                    "session_count": 0,
                    "sync": status["sync"],
                    "recent_events": self.store.list_agent_events(
                        project_id,
                        client_id=local_client_id if self.token_configured else None,
                        limit=10,
                        configured_authority_id=self.configured_authority_id,
                    ),
                }
            ]
        agents: list[dict[str, Any]] = []
        for client in clients:
            client_executions = [
                item for item in executions if item.get("client_id") == client["id"]
            ]
            active_tokens = [token for token in client["tokens"] if token.get("active")]
            scopes = sorted({scope for token in active_tokens for scope in token.get("scopes", [])})
            active_authority_ids = {token["id"] for token in active_tokens}
            snapshot = max(
                (
                    item
                    for item in snapshots
                    if item.get("client_id") == client["id"]
                    and item.get("authority_id") in active_authority_ids
                ),
                key=lambda item: str(item.get("observed_at") or ""),
                default=None,
            )
            last_seen = snapshot.get("observed_at") if snapshot else None
            snapshot_fresh = bool(snapshot and self.store.capability_snapshot_is_fresh(snapshot))
            availability = (
                "ready" if snapshot_fresh else "disconnected" if active_tokens else "unauthorized"
            )
            agents.append(
                {
                    "id": f"agent://codex/{client['id'].removeprefix('client://')}",
                    "client_id": client["id"],
                    "name": client["name"],
                    "connector": client["kind"],
                    "version": client.get("version"),
                    "availability": availability,
                    "reason": (
                        "connected"
                        if snapshot_fresh
                        else "capability_snapshot_stale"
                        if snapshot
                        else "handshake_not_observed"
                        if active_tokens
                        else "project_token_inactive"
                    ),
                    "capabilities": (
                        list(snapshot.get("capabilities", [])) if snapshot_fresh else []
                    ),
                    "capability_snapshot": (
                        self._public_snapshot(snapshot, authoritative=snapshot_fresh)
                        if snapshot
                        else None
                    ),
                    "scope": {"project_id": project_id, "token_scopes": scopes},
                    "last_seen_at": last_seen,
                    "recent_executions": [
                        self._public_execution(item) for item in client_executions[:5]
                    ],
                    "active_execution_count": sum(
                        item.get("status") in {"queued", "running", "awaiting_approval"}
                        for item in client_executions
                    ),
                    "session_count": len(
                        {
                            item.get("thread_id")
                            for item in client_executions
                            if item.get("thread_id")
                        }
                    ),
                    "sync": status["sync"],
                    "recent_events": self.store.list_agent_events(
                        project_id,
                        client_id=client["id"],
                        limit=10,
                        configured_authority_id=self.configured_authority_id,
                    ),
                }
            )
        return agents

    def observe_mcp_capabilities(
        self,
        principal: dict[str, Any],
        *,
        capabilities: list[str],
        event_type: str,
        reported_client: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record server-offered capabilities under bearer-token project authority."""

        client_id = str(principal["client_id"])
        project_id = str(principal["project_id"])
        authority_id = str(principal.get("authority_id") or principal.get("id") or "")
        if not authority_id:
            raise CodexBridgeError("authenticated authority is unavailable")
        current = self.store.get_capability_snapshot(project_id, authority_id) or {}
        identity = dict(current.get("handshake_identity") or {})
        identity.update(
            {
                "authority": "authenticated_bearer_principal",
                "authenticated_client_id": client_id,
            }
        )
        if reported_client is not None:
            reported_fingerprint_source = json.dumps(
                {
                    key: str(reported_client.get(key))
                    for key in ("name", "version")
                    if reported_client.get(key) is not None
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            identity["reported_client"] = {
                "present": True,
                "fingerprint": (
                    "sha256:" + self.store.hash_token(reported_fingerprint_source)[:16]
                ),
            }
        return self.store.record_capability_snapshot(
            project_id=project_id,
            client_id=client_id,
            authority_id=authority_id,
            server_version=self.MCP_SERVER_VERSION,
            protocol_version=self.MCP_PROTOCOL_VERSION,
            capabilities=[
                capability for capability in capabilities if capability in self.CAPABILITIES
            ],
            handshake_identity=identity,
            event_type=(
                event_type
                if event_type in {"mcp.handshake", "capability.discovery"}
                else "capability.discovery"
            ),
        )

    def agent_audit(
        self, project_id: str, *, client_id: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "client_id": client_id,
            "items": self.store.list_agent_events(
                project_id,
                client_id=client_id,
                limit=limit,
                configured_authority_id=self.configured_authority_id,
            ),
        }

    def _sync_status(self, project_id: str) -> dict[str, Any]:
        try:
            return self.store.codex_sync_projection(project_id)
        except Exception:
            return {
                "status": "unavailable",
                "reason": "sync_status_unavailable",
                "source_status": "unknown",
                "indexed_sessions": 0,
                "source_count": 0,
                "active_workflow": False,
                "last_sync_at": None,
                "data_available": False,
            }

    @classmethod
    def _public_snapshot(
        cls, snapshot: dict[str, Any], *, authoritative: bool = True
    ) -> dict[str, Any]:
        public = {
            key: snapshot.get(key)
            for key in (
                "client_id",
                "server_version",
                "protocol_version",
                "capabilities",
                "handshake_identity",
                "observed_at",
                "expires_at",
                "fresh",
            )
        }
        public["authority_status"] = "current" if authoritative else "historical_expired"
        identity = snapshot.get("handshake_identity") or {}
        reported = identity.get("reported_client")
        reported_is_fingerprint = bool(
            isinstance(reported, dict)
            and reported.get("present") is True
            and isinstance(reported.get("fingerprint"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{16}", reported["fingerprint"])
        )
        public["handshake_identity"] = {
            "authority": "authenticated_bearer_principal",
            "authenticated_client_id": snapshot.get("client_id"),
            **(
                {
                    "reported_client": (
                        reported
                        if reported_is_fingerprint
                        else {
                            "present": True,
                            "fingerprint": (
                                "sha256:"
                                + CodexBridgeStore.hash_token(
                                    json.dumps(
                                        reported,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        default=str,
                                    )
                                )[:16]
                            ),
                        }
                    )
                }
                if reported is not None
                else {}
            ),
        }
        if not authoritative:
            public["capabilities"] = []
        return public

    @staticmethod
    def _safe_reported_identity(value: Any) -> str:
        text = str(value or "").strip()[:160]
        safe = CodexBridgeStore._safe_observation(text)
        return safe if isinstance(safe, str) else "[redacted]"

    @staticmethod
    def _public_execution(item: dict[str, Any]) -> dict[str, Any]:
        public = {
            key: item.get(key)
            for key in (
                "id",
                "work_item_id",
                "work_item_title",
                "thread_id",
                "status",
                "mode",
                "summary",
                "error",
                "created_at",
                "updated_at",
            )
        }
        thread_id = public.get("thread_id")
        public["thread_id"] = (
            "thread://sha256:" + CodexBridgeStore.hash_token(str(thread_id))[:24]
            if thread_id
            else None
        )
        for key in ("summary", "error"):
            public[key] = "[redacted]" if public.get(key) else public.get(key)
        return public

    def issue_token(self, request: TokenCreate) -> dict[str, Any]:
        if not self.store.get_client(request.client_id):
            raise CodexBridgeError("integration client not found")
        if not self.workspace.store.get_project(request.project_id):
            raise CodexBridgeError("project not found")
        invalid = set(request.scopes) - {"read", "write"}
        if invalid:
            raise CodexBridgeError("unsupported token scope")
        token, raw = self.store.create_token(
            client_id=request.client_id,
            project_id=request.project_id,
            scopes=request.scopes,
            expires_at=request.expires_at,
        )
        return {**token, "token": raw}

    def create_execution(self, request: ExecutionCreate, client_id: str | None = None) -> dict:
        item = self.workspace.store.get_work_item(request.work_item_id)
        if not item or item["project_id"] != request.project_id:
            raise CodexBridgeError("research work item not found")
        project = self.workspace.store.get_project(request.project_id) or {}
        configured_path = str(project.get("settings", {}).get("workspace_path") or "").strip()
        authority_root = Path(configured_path or self.project_root).resolve()
        requested_path = Path(
            request.workspace_path or item.get("workspace_path") or authority_root
        ).resolve()
        try:
            requested_path.relative_to(authority_root)
        except ValueError as exc:
            raise CodexBridgeError("workspace path is outside the governed project root") from exc
        if not requested_path.is_dir():
            raise CodexBridgeError("governed workspace path is unavailable")
        workspace_path = os.fspath(requested_path)
        prompt = request.prompt.strip() or self.execution_prompt(item)
        execution_id = f"codex-execution://{uuid4().hex}"
        status = "awaiting_approval" if request.mode == "queue" else "queued"
        execution = self.store.create_execution(
            {
                "id": execution_id,
                "project_id": request.project_id,
                "work_item_id": request.work_item_id,
                "client_id": client_id,
                "mode": request.mode,
                "status": status,
                "sandbox": request.sandbox,
                "workspace_path": workspace_path,
                "base_ref": request.base_ref or item.get("base_ref") or "HEAD",
                "prompt": prompt,
                "context_snapshot": {
                    "work_item": item,
                    "project": self.workspace.store.get_project(request.project_id),
                    "intelligence": (
                        self.intelligence.analyze(
                            request.project_id,
                            topic_id=item.get("topic_id"),
                            iteration_id=item.get("iteration_id"),
                        )
                        if self.intelligence
                        else None
                    ),
                },
            }
        )
        from .runner import DeepLinkRunner, ExecRunner

        execution["launch"] = (
            DeepLinkRunner().prepare(execution)
            if request.mode == "interactive"
            else ExecRunner(self).prepare(execution)
        )
        if request.mode == "queue":
            execution["approval"] = self.store.create_approval(
                {
                    "project_id": request.project_id,
                    "execution_id": execution_id,
                    "work_item_id": request.work_item_id,
                    "action": "execution.start",
                    "requested_by": "RAG Core",
                    "payload": {"sandbox": request.sandbox, "workspace_path": workspace_path},
                }
            )
        return execution

    def execution_prompt(self, item: dict[str, Any]) -> str:
        criteria = "\n".join(f"- {value}" for value in item["acceptance_criteria"]) or "- 未设置"
        return (
            "Use $research-project-bridge:execute-research-development.\n"
            f"Work item: {item['id']}\n"
            f"Goal: {item['objective'] or item['title']}\n"
            f"Acceptance criteria:\n{criteria}\n"
            "Read the structured project context through MCP before editing. "
            "Report code changes, validation and evidence through execution_report. "
            "Do not mark the research task complete; request human review."
        )

    def claim_execution(
        self, execution_id: str, thread_id: str, client_id: str | None = None
    ) -> dict:
        execution = self._execution(execution_id)
        if execution["status"] not in {"queued", "running"}:
            raise CodexBridgeError("execution cannot be claimed")
        updated = self.store.update_execution(
            execution_id,
            {
                "thread_id": thread_id,
                "client_id": client_id or execution.get("client_id"),
                "status": "running",
                "started_at": execution.get("started_at") or utc_now(),
            },
        )
        self.store.append_event(
            execution_id, "claimed", "Codex 会话已绑定", {"thread_id": thread_id}
        )
        return updated or {}

    def report_execution(self, execution_id: str, request: ExecutionReport) -> dict:
        self._execution(execution_id)
        changes = request.model_dump()
        if request.status == "review":
            changes["completed_at"] = utc_now()
        updated = self.store.update_execution(execution_id, changes)
        self.store.append_event(
            execution_id,
            "report",
            request.summary or request.status,
            {
                "status": request.status,
                "changed_files": request.changed_files,
                "validation": request.validation,
            },
            request.artifact_path,
        )
        return updated or {}

    def request_review(self, execution_id: str, *, requested_by: str, summary: str = "") -> dict:
        execution = self._execution(execution_id)
        existing = [
            item
            for item in self.store.list_approvals(execution["project_id"], "pending")
            if item["execution_id"] == execution_id and item["action"] == "work_item.complete"
        ]
        if existing:
            return existing[0]
        return self.store.create_approval(
            {
                "project_id": execution["project_id"],
                "execution_id": execution_id,
                "work_item_id": execution["work_item_id"],
                "action": "work_item.complete",
                "requested_by": requested_by,
                "payload": {"summary": summary},
            }
        )

    def decide_approval(
        self, approval_id: str, request: ApprovalDecision
    ) -> tuple[dict, str | None]:
        pending = self.store.get_approval(approval_id)
        if not pending or pending["status"] != "pending":
            raise CodexBridgeError("pending approval not found")
        approval = self.store.decide_approval(
            approval_id, request.decision, request.decided_by, request.note
        )
        if not approval:
            raise CodexBridgeError("approval changed")
        runnable: str | None = None
        if request.decision == "approved" and pending["action"] == "execution.start":
            execution = self._execution(pending["execution_id"])
            self.store.update_execution(execution["id"], {"status": "queued"})
            runnable = execution["id"]
        elif request.decision == "approved" and pending["action"] == "work_item.complete":
            item = self.workspace.store.get_work_item(pending["work_item_id"])
            if item:
                try:
                    self.workspace.transition_work_item(
                        item["id"],
                        WorkItemTransition(
                            expected_version=item["version"],
                            status="done",
                            summary=pending["payload"].get("summary"),
                        ),
                    )
                    if pending.get("execution_id"):
                        self.store.update_execution(
                            pending["execution_id"], {"status": "completed"}
                        )
                except WorkspaceError as exc:
                    raise CodexBridgeError(str(exc)) from exc
        return approval, runnable

    def tool_call(
        self,
        name: str,
        arguments: dict[str, Any],
        principal: dict[str, Any],
    ) -> dict[str, Any]:
        project_id = arguments.get("project_id") or principal["project_id"]
        if project_id != principal["project_id"]:
            raise CodexBridgeError("token is not authorized for this project")
        scopes = set(principal.get("scopes", []))
        write_tool = name in {
            "research_create_work",
            "research_update_work",
            "research_transition_work",
            "evidence_link",
            "execution_claim",
            "execution_report",
            "execution_request_review",
            "wiki_propose_patch",
        }
        if write_tool and "write" not in scopes:
            raise CodexBridgeError("token does not allow writes")
        if not write_tool and "read" not in scopes:
            raise CodexBridgeError("token does not allow reads")
        if write_tool:
            key = str(arguments.get("idempotency_key") or "").strip()
            if not key:
                raise CodexBridgeError("idempotency_key is required")
            prior = self.store.idempotent_response(principal["client_id"], project_id, key)
            if prior is not None:
                return prior
        result = self._dispatch_tool(name, arguments, principal, project_id)
        if write_tool:
            self.store.remember_response(principal["client_id"], project_id, key, name, result)
        return result

    def _dispatch_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        principal: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        if name == "project_get_context":
            return self.project_context(project_id)
        if name == "project_get_intelligence":
            return (
                self.intelligence.analyze(
                    project_id,
                    topic_id=arguments.get("topic_id"),
                    iteration_id=arguments.get("iteration_id"),
                )
                if self.intelligence
                else {"project_id": project_id, "status": "unavailable"}
            )
        if name == "research_list_work":
            return {
                "items": self.workspace.store.list_work_items(
                    project_id,
                    topic_id=arguments.get("topic_id"),
                    status=arguments.get("status"),
                )
            }
        if name == "research_get_work":
            item = self.workspace.store.get_work_item(arguments["work_item_id"])
            if not item or item["project_id"] != project_id:
                raise CodexBridgeError("research work item not found")
            return item
        if name == "evidence_search":
            return self.platform.search(
                GlobalSearchRequest(
                    query=arguments["query"],
                    project_id=project_id,
                    sources=arguments.get("sources", []),
                    limit=arguments.get("limit", 20),
                ),
                record_event=False,
            )
        if name in {
            "wiki_navigation_status",
            "wiki_search",
            "wiki_read",
            "wiki_follow",
            "wiki_navigate",
            "evidence_read",
            "wiki_propose_patch",
        }:
            return self._dispatch_wiki_tool(name, arguments, project_id)
        if name == "execution_get":
            execution = self._execution(arguments["execution_id"])
            if execution["project_id"] != project_id:
                raise CodexBridgeError("execution not found")
            return execution
        if name == "review_list_pending":
            return {"items": self.store.list_approvals(project_id, "pending")}
        if name == "research_create_work":
            payload = {**arguments, "project_id": project_id}
            payload.pop("idempotency_key", None)
            return self.workspace.create_work_item(WorkItemCreate.model_validate(payload))
        if name == "research_update_work":
            item_id = arguments["work_item_id"]
            payload = dict(arguments)
            for key in ("project_id", "work_item_id", "idempotency_key"):
                payload.pop(key, None)
            item = self.workspace.store.get_work_item(item_id)
            if not item or item["project_id"] != project_id:
                raise CodexBridgeError("research work item not found")
            return self.workspace.update_work_item(item_id, WorkItemUpdate.model_validate(payload))
        if name == "research_transition_work":
            item_id = arguments["work_item_id"]
            if arguments["status"] == "done":
                return self.store.create_approval(
                    {
                        "project_id": project_id,
                        "work_item_id": item_id,
                        "action": "work_item.complete",
                        "requested_by": self.actor(principal, arguments),
                        "payload": {
                            "summary": arguments.get("summary", ""),
                            "expected_version": arguments["expected_version"],
                        },
                    }
                )
            return self.workspace.transition_work_item(
                item_id,
                WorkItemTransition(
                    expected_version=arguments["expected_version"],
                    status=arguments["status"],
                    summary=arguments.get("summary"),
                ),
            )
        if name == "evidence_link":
            payload = {
                key: value
                for key, value in arguments.items()
                if key
                in {
                    "iteration_id",
                    "entity_id",
                    "entity_type",
                    "source_type",
                    "role",
                    "status",
                    "metadata",
                }
            }
            return self.workspace.link_iteration(IterationLinkCreate.model_validate(payload))
        if name == "execution_claim":
            return self.claim_execution(
                arguments["execution_id"],
                arguments["thread_id"],
                principal["client_id"],
            )
        if name == "execution_report":
            payload = {
                key: value
                for key, value in arguments.items()
                if key
                in {
                    "status",
                    "summary",
                    "changed_files",
                    "validation",
                    "error",
                    "artifact_path",
                }
            }
            return self.report_execution(
                arguments["execution_id"], ExecutionReport.model_validate(payload)
            )
        if name == "execution_request_review":
            return self.request_review(
                arguments["execution_id"],
                requested_by=self.actor(principal, arguments),
                summary=arguments.get("summary", ""),
            )
        raise CodexBridgeError("unknown tool")

    def _dispatch_wiki_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        from ..rag.wiki.builder_v1 import WikiBuilderPatchV1, WikiPatchOriginV1
        from ..rag.wiki.contracts_v1 import WikiSourceDomainV1
        from ..rag.wiki.search_v1 import WikiQueryClassV1

        if self.wiki is None:
            raise CodexBridgeError("agent-native Wiki runtime is unavailable")
        project = self.workspace.store.get_project(project_id)
        if not project:
            raise CodexBridgeError("project not found")
        requester_acl_refs = (str(project["acl_ref"]),)
        try:
            if name == "wiki_navigation_status":
                status = self.wiki.status(
                    project_id=project_id,
                    requester_acl_refs=requester_acl_refs,
                )
                return status.model_dump(mode="json")
            if name == "wiki_search":
                query_class = arguments.get("query_class")
                response = self.wiki.search(
                    request_id="mcp-wiki-search-" + uuid4().hex,
                    project_id=project_id,
                    requester_acl_refs=requester_acl_refs,
                    query=str(arguments["query"]),
                    query_class=(WikiQueryClassV1(query_class) if query_class else None),
                    required_sources=tuple(
                        sorted(
                            {
                                WikiSourceDomainV1(item)
                                for item in arguments.get("required_sources", [])
                            },
                            key=lambda item: item.value,
                        )
                    ),
                    required_roles=tuple(sorted(set(arguments.get("required_roles", [])))),
                    generation_id=arguments.get("generation_id"),
                    top_k=int(arguments.get("top_k", 12)),
                    candidate_limit=int(arguments.get("candidate_limit", 2_000)),
                )
                return response.model_dump(mode="json")
            if name == "wiki_read":
                record = self.wiki.read(
                    project_id=project_id,
                    requester_acl_refs=requester_acl_refs,
                    logical_path=str(arguments["path"]),
                    generation_id=arguments.get("generation_id"),
                )
                if record is None:
                    raise CodexBridgeError("Wiki path not found")
                return record.model_dump(mode="json")
            if name == "wiki_follow":
                result = self.wiki.follow_links(
                    project_id=project_id,
                    requester_acl_refs=requester_acl_refs,
                    logical_path=str(arguments["path"]),
                    relation=arguments.get("relation"),
                    generation_id=arguments.get("generation_id"),
                    limit=int(arguments.get("limit", 32)),
                )
                return result.model_dump(mode="json")
            if name == "evidence_read":
                result = self.wiki.read_evidence(
                    project_id=project_id,
                    requester_acl_refs=requester_acl_refs,
                    page_path=str(arguments["page_path"]),
                    source_ref_id=str(arguments["source_ref_id"]),
                    generation_id=arguments.get("generation_id"),
                )
                return result.model_dump(mode="json")
            if name == "wiki_navigate":
                query_class = arguments.get("query_class")
                request = self.wiki.build_navigation_request(
                    request_id="mcp-wiki-navigation-" + uuid4().hex,
                    project_id=project_id,
                    requester_acl_refs=requester_acl_refs,
                    query=str(arguments["query"]),
                    query_class=(WikiQueryClassV1(query_class) if query_class else None),
                    required_roles=tuple(sorted(set(arguments.get("required_roles", [])))),
                    required_sources=tuple(
                        sorted(
                            {
                                WikiSourceDomainV1(item)
                                for item in arguments.get("required_sources", [])
                            },
                            key=lambda item: item.value,
                        )
                    ),
                    as_of=arguments.get("as_of"),
                    max_searches=int(arguments.get("max_searches", 8)),
                    max_page_reads=int(arguments.get("max_page_reads", 48)),
                    max_link_hops=int(arguments.get("max_link_hops", 3)),
                    max_raw_reads=int(arguments.get("max_raw_reads", 16)),
                    empty_search_patience=int(arguments.get("empty_search_patience", 2)),
                    retrieval_deadline_ms=int(arguments.get("retrieval_deadline_ms", 4_000)),
                    top_k_per_search=int(arguments.get("top_k_per_search", 8)),
                    token_budget=int(arguments.get("token_budget", 8_000)),
                    require_raw_evidence=bool(arguments.get("require_raw_evidence", True)),
                )
                return self.wiki.navigate(request).model_dump(mode="json")
            if name == "wiki_propose_patch":
                patch = WikiBuilderPatchV1.model_validate(arguments["patch"])
                if (
                    patch.origin is not WikiPatchOriginV1.AGENT_UNREVIEWED
                    or patch.reviewed
                    or patch.reviewer_authority_sha256 is not None
                ):
                    raise CodexBridgeError(
                        "agents may submit only unreviewed Wiki Builder proposals"
                    )
                self.wiki.propose_patch(project_id=project_id, patch=patch)
                return {
                    "patch_id": patch.patch_id,
                    "patch_sha256": patch.content_sha256,
                    "state": "staged_for_human_review",
                    "production_authorized": False,
                }
        except CodexBridgeError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise CodexBridgeError(str(exc)) from exc
        raise CodexBridgeError("unknown Wiki tool")

    def project_context(self, project_id: str) -> dict[str, Any]:
        project = self.workspace.store.get_project(project_id)
        if not project:
            raise CodexBridgeError("project not found")
        context = {
            "project": project,
            "topics": self.workspace.store.list_topics(project_id)[:50],
            "iterations": self.workspace.store.list_iterations(project_id)[:100],
            "work_items": self.workspace.store.list_work_items(project_id, limit=200),
        }
        if self.intelligence:
            context["intelligence"] = self.intelligence.analyze(project_id)
        return context

    def resource_read(self, uri: str, principal: dict[str, Any]) -> dict[str, Any]:
        if uri == f"wiki://projects/{principal['project_id']}/status":
            return self._dispatch_wiki_tool("wiki_navigation_status", {}, principal["project_id"])
        wiki_page_prefix = f"wiki://projects/{principal['project_id']}/pages/"
        if uri.startswith(wiki_page_prefix):
            encoded_path = uri.removeprefix(wiki_page_prefix)
            path = unquote(encoded_path)
            return self._dispatch_wiki_tool(
                "wiki_read",
                {"path": path},
                principal["project_id"],
            )
        if uri.startswith("research://projects/") and uri.endswith("/context"):
            project_id = uri.removeprefix("research://projects/").removesuffix("/context")
            if project_id != principal["project_id"]:
                raise CodexBridgeError("resource not found")
            return self.project_context(project_id)
        if uri.startswith("research://work-items/"):
            item_id = uri.removeprefix("research://work-items/")
            item = self.workspace.store.get_work_item(item_id)
            if not item or item["project_id"] != principal["project_id"]:
                raise CodexBridgeError("resource not found")
            return item
        if uri.startswith("research://executions/"):
            execution_id = uri.removeprefix("research://executions/")
            execution = self._execution(execution_id)
            if execution["project_id"] != principal["project_id"]:
                raise CodexBridgeError("resource not found")
            return execution
        raise CodexBridgeError("resource not found")

    @staticmethod
    def actor(principal: dict[str, Any], arguments: dict[str, Any]) -> str:
        return (
            f"codex:{principal['client_id']}:"
            f"{arguments.get('thread_id') or principal.get('thread_id') or 'unbound'}"
        )

    def _execution(self, execution_id: str) -> dict[str, Any]:
        execution = self.store.get_execution(execution_id)
        if not execution:
            raise CodexBridgeError("execution not found")
        return execution

    def idempotent(
        self,
        principal: dict[str, Any],
        key: str,
        tool_name: str,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        prior = self.store.idempotent_response(principal["client_id"], principal["project_id"], key)
        if prior is not None:
            return prior
        result = operation()
        self.store.remember_response(
            principal["client_id"], principal["project_id"], key, tool_name, result
        )
        return result
