from __future__ import annotations

import json
import secrets
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from ..rag.wiki.builder_v1 import WikiBuilderPatchV1
from ..workspace.service import WorkspaceError
from .service import CodexBridgeError

router = APIRouter(tags=["codex-mcp"])


def _schema(
    properties: dict[str, Any],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


PROJECT_ID = {"type": "string", "default": "project-rag"}
IDEMPOTENCY = {
    "type": "string",
    "minLength": 8,
    "description": "Stable caller-generated key for this logical write.",
}

_WIKI_SOURCES = ["code", "codex", "experiment", "notebook", "document", "workspace"]
_WIKI_QUERY_CLASSES = [
    "local_detail",
    "multi_hop",
    "comparison",
    "temporal",
    "global",
    "exploratory",
]


def _wiki_patch_input_schema() -> dict[str, Any]:
    patch_schema = WikiBuilderPatchV1.model_json_schema()
    definitions = patch_schema.pop("$defs", {})
    return {
        "type": "object",
        "properties": {
            "project_id": PROJECT_ID,
            "patch": patch_schema,
            "idempotency_key": IDEMPOTENCY,
        },
        "required": ["patch", "idempotency_key"],
        "additionalProperties": False,
        "$defs": definitions,
    }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "project_get_context",
        "description": "Read the research project, topics, iterations and actionable work.",
        "inputSchema": _schema({"project_id": PROJECT_ID}),
        "annotations": {
            "title": "Read research project context",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "project_get_intelligence",
        "description": (
            "Read the derived research stage, blockers, evidence coverage, source health "
            "and prioritized next actions without changing project state."
        ),
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "topic_id": {"type": "string"},
                "iteration_id": {"type": "string"},
            }
        ),
        "annotations": {
            "title": "Read research project intelligence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "research_list_work",
        "description": "List structured research, development, experiment and review work.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "topic_id": {"type": "string"},
                "status": {"type": "string"},
            }
        ),
        "annotations": {
            "title": "List research work",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "research_get_work",
        "description": "Read one work item with acceptance criteria and source bindings.",
        "inputSchema": _schema(
            {"project_id": PROJECT_ID, "work_item_id": {"type": "string"}},
            ["work_item_id"],
        ),
        "annotations": {
            "title": "Read research work",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "evidence_search",
        "description": "Search code, Codex sessions, experiments, notebooks, documents and workspace evidence.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "query": {"type": "string", "minLength": 1},
                "sources": {
                    "type": "array",
                    "items": {
                        "enum": [
                            "code",
                            "codex",
                            "workspace",
                            "experiment",
                            "notebook",
                            "document",
                        ]
                    },
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            ["query"],
        ),
        "annotations": {
            "title": "Search project evidence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "wiki_navigation_status",
        "description": (
            "Read the active governed Wiki snapshot, provider availability, Error Book, "
            "Builder queue and release hold without exposing query or ACL text."
        ),
        "inputSchema": _schema({"project_id": PROJECT_ID}),
        "annotations": {
            "title": "Read governed Wiki navigation status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "wiki_search",
        "description": (
            "Search the pinned project Wiki with exact, lexical, optional semantic, "
            "structural and reranked channels under the token project ACL."
        ),
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                "query_class": {"enum": _WIKI_QUERY_CLASSES},
                "required_sources": {
                    "type": "array",
                    "items": {"enum": _WIKI_SOURCES},
                    "maxItems": 6,
                    "uniqueItems": True,
                },
                "required_roles": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "maxItems": 32,
                    "uniqueItems": True,
                },
                "generation_id": {"type": "string", "maxLength": 240},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12},
                "candidate_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20000,
                    "default": 2000,
                },
            },
            ["query"],
        ),
        "annotations": {
            "title": "Search the governed project Wiki",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "wiki_read",
        "description": "Read one ACL-visible Wiki page fragment or directory from a pinned snapshot.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "path": {"type": "string", "minLength": 2, "maxLength": 2000},
                "generation_id": {"type": "string", "maxLength": 240},
            },
            ["path"],
        ),
        "annotations": {
            "title": "Read a governed Wiki path",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "wiki_follow",
        "description": (
            "Follow active grounded relationships from one Wiki page and return visible target pages."
        ),
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "path": {"type": "string", "minLength": 2, "maxLength": 2000},
                "relation": {"type": "string", "minLength": 1, "maxLength": 128},
                "generation_id": {"type": "string", "maxLength": 240},
                "limit": {"type": "integer", "minimum": 1, "maximum": 64, "default": 32},
            },
            ["path"],
        ),
        "annotations": {
            "title": "Follow governed Wiki relationships",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "wiki_navigate",
        "description": (
            "Plan evidence obligations, search/read/follow Wiki pages, verify raw evidence and "
            "return a bounded EvidencePack with an explicit stop reason."
        ),
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                "query_class": {"enum": _WIKI_QUERY_CLASSES},
                "required_sources": {
                    "type": "array",
                    "items": {"enum": _WIKI_SOURCES},
                    "maxItems": 6,
                    "uniqueItems": True,
                },
                "required_roles": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "maxItems": 32,
                    "uniqueItems": True,
                },
                "as_of": {"type": "string", "maxLength": 128},
                "max_searches": {"type": "integer", "minimum": 1, "maximum": 32},
                "max_page_reads": {"type": "integer", "minimum": 1, "maximum": 256},
                "max_link_hops": {"type": "integer", "minimum": 0, "maximum": 6},
                "max_raw_reads": {"type": "integer", "minimum": 1, "maximum": 128},
                "empty_search_patience": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 8,
                },
                "retrieval_deadline_ms": {
                    "type": "integer",
                    "minimum": 50,
                    "maximum": 120000,
                },
                "top_k_per_search": {"type": "integer", "minimum": 1, "maximum": 25},
                "token_budget": {"type": "integer", "minimum": 256, "maximum": 64000},
                "require_raw_evidence": {"type": "boolean", "default": True},
            },
            ["query"],
        ),
        "annotations": {
            "title": "Navigate the Wiki and build governed evidence",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "evidence_read",
        "description": (
            "Resolve one Wiki source reference back to the exact ACL- and generation-bound raw fact."
        ),
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "page_path": {"type": "string", "minLength": 2, "maxLength": 2000},
                "source_ref_id": {"type": "string", "minLength": 1, "maxLength": 240},
                "generation_id": {"type": "string", "maxLength": 240},
            },
            ["page_path", "source_ref_id"],
        ),
        "annotations": {
            "title": "Verify exact raw evidence behind a Wiki page",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "wiki_propose_patch",
        "description": (
            "Submit a content-addressed AGENT_UNREVIEWED Wiki Builder patch for human review. "
            "This tool can never review, publish or release a generation."
        ),
        "inputSchema": _wiki_patch_input_schema(),
        "annotations": {
            "title": "Propose a governed Wiki patch for review",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "execution_get",
        "description": "Read one Codex execution and its ordered events.",
        "inputSchema": _schema(
            {"project_id": PROJECT_ID, "execution_id": {"type": "string"}},
            ["execution_id"],
        ),
        "annotations": {
            "title": "Read Codex execution",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "review_list_pending",
        "description": "List project actions waiting for human review or approval.",
        "inputSchema": _schema({"project_id": PROJECT_ID}),
        "annotations": {
            "title": "List pending reviews",
            "readOnlyHint": True,
            "destructiveHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "research_create_work",
        "description": "Create a research work item after the user approves the MCP write.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "topic_id": {"type": "string"},
                "iteration_id": {"type": "string"},
                "repository_id": {"type": "string"},
                "title": {"type": "string", "minLength": 1},
                "objective": {"type": "string"},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "kind": {
                    "enum": ["research", "development", "experiment", "analysis", "review"],
                    "default": "development",
                },
                "status": {
                    "enum": ["backlog", "ready", "running", "review", "blocked"],
                    "default": "backlog",
                },
                "priority": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
                "assignee_type": {"enum": ["human", "codex"], "default": "codex"},
                "assignee": {"type": "string", "default": "Codex"},
                "workspace_path": {"type": "string"},
                "base_ref": {"type": "string"},
                "idempotency_key": IDEMPOTENCY,
            },
            ["title", "idempotency_key"],
        ),
        "annotations": {
            "title": "Create research work",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "research_update_work",
        "description": "Update editable work fields using optimistic concurrency.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "work_item_id": {"type": "string"},
                "expected_version": {"type": "integer", "minimum": 1},
                "title": {"type": "string"},
                "objective": {"type": "string"},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "kind": {"enum": ["research", "development", "experiment", "analysis", "review"]},
                "status": {
                    "enum": ["backlog", "ready", "running", "review", "blocked", "cancelled"]
                },
                "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                "assignee_type": {"enum": ["human", "codex"]},
                "assignee": {"type": "string"},
                "workspace_path": {"type": "string"},
                "base_ref": {"type": "string"},
                "summary": {"type": "string"},
                "idempotency_key": IDEMPOTENCY,
            },
            ["work_item_id", "expected_version", "idempotency_key"],
        ),
        "annotations": {
            "title": "Update research work",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "research_transition_work",
        "description": "Move work through the research lifecycle. Completion always becomes a human approval.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "work_item_id": {"type": "string"},
                "expected_version": {"type": "integer", "minimum": 1},
                "status": {
                    "enum": [
                        "backlog",
                        "ready",
                        "running",
                        "review",
                        "blocked",
                        "done",
                        "cancelled",
                    ]
                },
                "summary": {"type": "string"},
                "thread_id": {"type": "string"},
                "idempotency_key": IDEMPOTENCY,
            },
            ["work_item_id", "expected_version", "status", "idempotency_key"],
        ),
        "annotations": {
            "title": "Transition research work",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "evidence_link",
        "description": "Link an existing evidence entity to a research iteration.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "iteration_id": {"type": "string"},
                "entity_id": {"type": "string"},
                "entity_type": {"type": "string"},
                "source_type": {"enum": ["code", "codex", "experiment", "document", "workspace"]},
                "role": {"type": "string", "default": "evidence"},
                "status": {"type": "string", "default": "linked"},
                "metadata": {"type": "object"},
                "idempotency_key": IDEMPOTENCY,
            },
            ["iteration_id", "entity_id", "source_type", "idempotency_key"],
        ),
        "annotations": {
            "title": "Link research evidence",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "execution_claim",
        "description": "Bind the active Codex thread to a system execution.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "execution_id": {"type": "string"},
                "thread_id": {"type": "string"},
                "idempotency_key": IDEMPOTENCY,
            },
            ["execution_id", "thread_id", "idempotency_key"],
        ),
        "annotations": {
            "title": "Claim Codex execution",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "execution_report",
        "description": "Report structured progress, changes, validation and errors.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "execution_id": {"type": "string"},
                "status": {"enum": ["running", "review", "failed"]},
                "summary": {"type": "string"},
                "changed_files": {"type": "array", "items": {"type": "string"}},
                "validation": {"type": "array", "items": {"type": "object"}},
                "error": {"type": "string"},
                "artifact_path": {"type": "string"},
                "idempotency_key": IDEMPOTENCY,
            },
            ["execution_id", "status", "idempotency_key"],
        ),
        "annotations": {
            "title": "Report Codex execution",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "execution_request_review",
        "description": "Request human review; it never completes research work automatically.",
        "inputSchema": _schema(
            {
                "project_id": PROJECT_ID,
                "execution_id": {"type": "string"},
                "thread_id": {"type": "string"},
                "summary": {"type": "string"},
                "idempotency_key": IDEMPOTENCY,
            },
            ["execution_id", "idempotency_key"],
        ),
        "annotations": {
            "title": "Request execution review",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]


def authenticate(request: Request) -> dict[str, Any]:
    runtime = request.app.state.runtime
    authorization = request.headers.get("authorization", "")
    raw = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    if not raw:
        raise CodexBridgeError("missing bearer token")
    if runtime.settings.mcp_token and secrets.compare_digest(raw, runtime.settings.mcp_token):
        runtime.codex_bridge.store.upsert_client("client://codex-local", "research-project-bridge")
        return {
            "client_id": "client://codex-local",
            "project_id": "project-rag",
            "scopes": ["read", "write"],
            "authority_id": (
                "configured-token://sha256:" + runtime.codex_bridge.store.hash_token(raw)
            ),
        }
    principal = runtime.codex_bridge.store.authenticate(raw)
    if not principal:
        raise CodexBridgeError("invalid or revoked bearer token")
    principal["authority_id"] = principal["id"]
    return principal


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _text_result(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            }
        ],
        "structuredContent": value,
        "isError": False,
    }


def handle_message(message: dict[str, Any], request: Request, principal: dict) -> dict | None:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None and method and method.startswith("notifications/"):
        return None
    if method == "initialize":
        params = message.get("params") or {}
        request.app.state.runtime.codex_bridge.observe_mcp_capabilities(
            principal,
            capabilities=[str(tool["name"]) for tool in TOOLS],
            event_type="mcp.handshake",
            reported_client=(
                params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else None
            ),
        )
        return _result(
            request_id,
            {
                "protocolVersion": request.app.state.runtime.codex_bridge.MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}, "resources": {}},
                "serverInfo": {
                    "name": "research-project-bridge",
                    "version": request.app.state.runtime.codex_bridge.MCP_SERVER_VERSION,
                },
                "instructions": (
                    "Use wiki_navigation_status before Wiki work; use wiki_search, wiki_read, "
                    "wiki_follow and evidence_read for bounded governed exploration; use "
                    "wiki_navigate when a question needs multi-hop evidence obligations. Read "
                    "project context automatically. Ask the user before every write. Agents may "
                    "only submit AGENT_UNREVIEWED Wiki patches and can never review, publish or "
                    "release them. Never infer a project outside the token scope and never "
                    "complete critical research work without a platform approval."
                ),
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        request.app.state.runtime.codex_bridge.observe_mcp_capabilities(
            principal,
            capabilities=[str(tool["name"]) for tool in TOOLS],
            event_type="capability.discovery",
        )
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            value = request.app.state.runtime.codex_bridge.tool_call(
                params.get("name", ""), params.get("arguments") or {}, principal
            )
            return _result(request_id, _text_result(value))
        except (CodexBridgeError, ValidationError, KeyError, WorkspaceError) as exc:
            return _result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
    if method == "resources/list":
        project_id = principal["project_id"]
        executions = request.app.state.runtime.codex_bridge.store.list_executions(
            project_id, limit=100
        )
        work_items = request.app.state.runtime.workspace.store.list_work_items(
            project_id, limit=200
        )
        resources = [
            {
                "uri": f"research://projects/{project_id}/context",
                "name": "Current research project context",
                "mimeType": "application/json",
            }
        ]
        wiki_status = request.app.state.runtime.wiki.status(
            project_id=project_id,
            requester_acl_refs=(
                str(request.app.state.runtime.workspace.store.get_project(project_id)["acl_ref"]),
            ),
        )
        resources.append(
            {
                "uri": f"wiki://projects/{project_id}/status",
                "name": "Governed project Wiki navigation status",
                "mimeType": "application/json",
            }
        )
        if wiki_status.active_generation_id is not None:
            pages = request.app.state.runtime.wiki.list_pages(
                project_id=project_id,
                requester_acl_refs=(
                    str(
                        request.app.state.runtime.workspace.store.get_project(project_id)["acl_ref"]
                    ),
                ),
                generation_id=wiki_status.active_generation_id,
                limit=200,
            )
            resources.extend(
                {
                    "uri": (
                        f"wiki://projects/{project_id}/pages/" + quote(item.logical_path, safe="")
                    ),
                    "name": item.title,
                    "description": item.summary,
                    "mimeType": "application/json",
                }
                for item in pages
            )
        resources.extend(
            {
                "uri": f"research://work-items/{item['id']}",
                "name": item["title"],
                "mimeType": "application/json",
            }
            for item in work_items
        )
        resources.extend(
            {
                "uri": f"research://executions/{item['id']}",
                "name": f"Execution · {item['work_item_title']}",
                "mimeType": "application/json",
            }
            for item in executions
        )
        return _result(request_id, {"resources": resources})
    if method == "resources/read":
        try:
            uri = message["params"]["uri"]
            value = request.app.state.runtime.codex_bridge.resource_read(uri, principal)
            return _result(
                request_id,
                {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": json.dumps(value, ensure_ascii=False),
                        }
                    ]
                },
            )
        except (CodexBridgeError, KeyError) as exc:
            return _error(request_id, -32002, str(exc))
    return _error(request_id, -32601, "method not found")


@router.post("/mcp")
async def mcp(request: Request):
    try:
        principal = authenticate(request)
    except CodexBridgeError as exc:
        return JSONResponse(
            status_code=401,
            content=_error(None, -32001, str(exc)),
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(status_code=400, content=_error(None, -32700, "parse error"))
    if isinstance(payload, list):
        responses = [
            response
            for message in payload
            if isinstance(message, dict)
            if (response := handle_message(message, request, principal)) is not None
        ]
        if not responses:
            return Response(status_code=202)
        return JSONResponse(content=responses)
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content=_error(None, -32600, "invalid request"))
    response = handle_message(payload, request, principal)
    if response is None:
        return Response(status_code=202)
    return JSONResponse(content=response)
