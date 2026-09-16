from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from evidence_rag.api import create_app
from evidence_rag.codex_bridge import store as codex_bridge_store


def _mcp(client: TestClient, token: str, method: str, params: dict | None = None) -> dict:
    response = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            **({"params": params} if params is not None else {}),
        },
    )
    assert response.status_code == 200
    return response.json()


def test_mcp_exposes_structured_tools_and_idempotent_scoped_writes(settings) -> None:
    configured = replace(settings, mcp_token="local-test-token")
    with TestClient(create_app(configured)) as client:
        before_handshake = client.get("/v1/codex-bridge/status").json()
        assert before_handshake["availability"] == "disconnected"
        assert before_handshake["reason"] == "handshake_not_observed"
        assert before_handshake["permissions"]["sandbox"] == "read-only"
        initialize = _mcp(
            client,
            "local-test-token",
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        assert initialize["result"]["serverInfo"]["name"] == "research-project-bridge"
        assert initialize["result"]["serverInfo"]["version"] == "0.2.0"
        after_handshake = client.get("/v1/codex-bridge/status").json()
        assert after_handshake["availability"] == "ready"
        assert after_handshake["reason"] == "connected"
        tools = _mcp(client, "local-test-token", "tools/list")["result"]["tools"]
        tool_names = {item["name"] for item in tools}
        assert {
            "project_get_context",
            "research_create_work",
            "execution_report",
            "review_list_pending",
        } <= tool_names
        assert "delete" not in " ".join(tool_names)
        assert next(item for item in tools if item["name"] == "project_get_context")["annotations"][
            "readOnlyHint"
        ]

        arguments = {
            "project_id": "project-rag",
            "title": "验证多源检索证据",
            "objective": "运行测试并回传证据",
            "kind": "development",
            "assignee_type": "codex",
            "assignee": "Codex",
            "idempotency_key": "stable-create-001",
        }
        first = _mcp(
            client,
            "local-test-token",
            "tools/call",
            {"name": "research_create_work", "arguments": arguments},
        )["result"]["structuredContent"]
        second = _mcp(
            client,
            "local-test-token",
            "tools/call",
            {"name": "research_create_work", "arguments": arguments},
        )["result"]["structuredContent"]
        assert first["id"] == second["id"]
        items = client.get("/v1/research/work-items").json()
        assert [item["id"] for item in items].count(first["id"]) == 1

        blocked = _mcp(
            client,
            "local-test-token",
            "tools/call",
            {
                "name": "project_get_context",
                "arguments": {"project_id": "project-other"},
            },
        )
        assert blocked["result"]["isError"] is True


def test_completion_requires_platform_approval_and_token_revocation_is_immediate(
    settings,
) -> None:
    with TestClient(create_app(settings)) as client:
        other = client.post("/v1/projects", json={"id": "project-other", "name": "Other"}).json()
        assert other["id"] == "project-other"
        work = client.post(
            "/v1/research/work-items",
            json={"title": "待 Codex 验证任务", "status": "review"},
        ).json()
        integration = client.post(
            "/v1/codex-bridge/clients",
            json={"name": "research-project-bridge", "version": "0.1.0"},
        ).json()
        token_record = client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": integration["id"],
                "project_id": "project-rag",
                "scopes": ["read", "write"],
            },
        ).json()
        token = token_record["token"]
        transition = _mcp(
            client,
            token,
            "tools/call",
            {
                "name": "research_transition_work",
                "arguments": {
                    "project_id": "project-rag",
                    "work_item_id": work["id"],
                    "expected_version": work["version"],
                    "status": "done",
                    "summary": "验证已完成",
                    "idempotency_key": "complete-request-001",
                },
            },
        )["result"]["structuredContent"]
        assert transition["status"] == "pending"
        unchanged = client.get(
            "/v1/research/work-items/by-id", params={"work_item_id": work["id"]}
        ).json()
        assert unchanged["status"] == "review"

        approved = client.post(
            "/v1/codex-bridge/approvals/decide",
            params={"approval_id": transition["id"]},
            json={"decision": "approved", "decided_by": "principal-investigator"},
        )
        assert approved.status_code == 200
        completed = client.get(
            "/v1/research/work-items/by-id", params={"work_item_id": work["id"]}
        ).json()
        assert completed["status"] == "done"

        assert client.delete(f"/v1/codex-bridge/tokens/{token_record['id']}").status_code == 200
        denied = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert denied.status_code == 401


def test_agents_are_project_scoped_and_redact_host_paths(settings) -> None:
    with TestClient(create_app(settings)) as client:
        client.post("/v1/projects", json={"id": "project-other", "name": "Other"})
        current_client = client.post(
            "/v1/codex-bridge/clients",
            json={"name": "Current project Codex", "version": "0.1.0"},
        ).json()
        other_client = client.post(
            "/v1/codex-bridge/clients",
            json={"name": "Other project Codex", "version": "0.1.0"},
        ).json()
        client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": current_client["id"],
                "project_id": "project-rag",
                "scopes": ["read"],
            },
        )
        client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": other_client["id"],
                "project_id": "project-other",
                "scopes": ["read", "write"],
            },
        )

        response = client.get("/v1/codex-bridge/agents", params={"project_id": "project-rag"})
        assert response.status_code == 200
        agents = response.json()
        assert [agent["name"] for agent in agents] == ["Current project Codex"]
        assert agents[0]["availability"] == "disconnected"
        assert agents[0]["reason"] == "handshake_not_observed"
        assert agents[0]["scope"] == {
            "project_id": "project-rag",
            "token_scopes": ["read"],
        }
        serialized = response.text
        assert str(Path(settings.allowed_local_roots[0]).resolve()) not in serialized
        assert "token_hash" not in serialized
        assert '"token"' not in serialized

        status = client.get("/v1/codex-bridge/status", params={"project_id": "project-rag"}).json()
        assert status["plugin"]["location"] in {"configured", "not-configured"}
        assert status["codex_cli"]["location"] in {
            "available-on-host",
            "not-detected",
        }
        assert "path" not in status["plugin"]
        assert "path" not in status["codex_cli"]


def test_execution_defaults_read_only_and_rejects_outside_workspace(settings) -> None:
    with TestClient(create_app(settings)) as client:
        work = client.post(
            "/v1/research/work-items",
            json={"title": "只读执行", "status": "ready"},
        ).json()
        created = client.post(
            "/v1/codex-bridge/executions",
            json={
                "project_id": "project-rag",
                "work_item_id": work["id"],
                "mode": "interactive",
            },
        )
        assert created.status_code == 201
        assert created.json()["sandbox"] == "read-only"

        outside = client.post(
            "/v1/codex-bridge/executions",
            json={
                "project_id": "project-rag",
                "work_item_id": work["id"],
                "mode": "interactive",
                "workspace_path": "/tmp/outside-governed-project",
            },
        )
        assert outside.status_code == 409
        assert "outside the governed project root" in outside.json()["detail"]


def test_missing_approval_decision_returns_not_found(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/codex-bridge/approvals/decide",
            params={"approval_id": "approval://missing"},
            json={"decision": "approved", "decided_by": "reviewer"},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "pending approval not found"


def test_execution_and_approval_mutations_enforce_object_project_acl(settings) -> None:
    configured = replace(
        settings,
        deployment_mode="production",
        api_token="production-token",
        enforce_acl=True,
        trusted_acl_refs=("project:project-rag", "project:project-other"),
    )
    allowed_headers = {
        "Authorization": "Bearer production-token",
        "X-RAG-ACL-Refs": "project:project-rag",
    }
    denied_headers = {
        "Authorization": "Bearer production-token",
        "X-RAG-ACL-Refs": "project:project-other",
    }
    with TestClient(create_app(configured)) as client:
        work = client.post(
            "/v1/research/work-items",
            headers=allowed_headers,
            json={"title": "ACL 执行", "status": "ready"},
        ).json()
        execution = client.post(
            "/v1/codex-bridge/executions",
            headers=allowed_headers,
            json={
                "project_id": "project-rag",
                "work_item_id": work["id"],
                "mode": "queue",
            },
        ).json()

        claim = client.post(
            "/v1/codex-bridge/executions/claim",
            params={"execution_id": execution["id"]},
            headers=denied_headers,
            json={"thread_id": "thread-forbidden"},
        )
        report = client.post(
            "/v1/codex-bridge/executions/report",
            params={"execution_id": execution["id"]},
            headers=denied_headers,
            json={"status": "failed", "error": "must not be written"},
        )
        approval = client.post(
            "/v1/codex-bridge/approvals/decide",
            params={"approval_id": execution["approval"]["id"]},
            headers=denied_headers,
            json={"decision": "approved", "decided_by": "forbidden"},
        )

        assert claim.status_code == 404
        assert report.status_code == 404
        assert approval.status_code == 404
        unchanged = client.get(
            "/v1/codex-bridge/executions/by-id",
            params={"execution_id": execution["id"]},
            headers=allowed_headers,
        ).json()
        assert unchanged["status"] == "awaiting_approval"
        assert unchanged["thread_id"] is None


def test_capability_snapshot_is_server_authoritative_persistent_and_handshake_gated(
    settings,
) -> None:
    configured = replace(settings, mcp_token="snapshot-token")
    with TestClient(create_app(configured)) as client:
        ping = _mcp(client, "snapshot-token", "ping")
        assert ping["result"] == {}
        before = client.get("/v1/codex-bridge/status").json()
        assert before["availability"] == "disconnected"
        assert before["capabilities"] == []
        assert before["capability_snapshots"] == []

        _mcp(
            client,
            "snapshot-token",
            "initialize",
            {
                "protocolVersion": "caller-controlled-version",
                "capabilities": {
                    "admin": True,
                    "scopes": ["root", "delete"],
                    "token": "must-not-survive",
                },
                "clientInfo": {
                    "name": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq",
                    "version": "file:///Users/private/client-version",
                },
            },
        )
        after = client.get("/v1/codex-bridge/status").json()
        assert after["availability"] == "ready"
        assert "project_get_context" in after["capabilities"]
        assert "admin" not in after["capabilities"]
        snapshot = after["capability_snapshots"][0]
        assert snapshot["protocol_version"] == "2025-06-18"
        assert snapshot["server_version"] == "0.2.0"
        identity = snapshot["handshake_identity"]
        assert identity["authority"] == "authenticated_bearer_principal"
        assert identity["authenticated_client_id"] == "client://codex-local"
        assert identity["reported_client"]["present"] is True
        assert identity["reported_client"]["fingerprint"].startswith("sha256:")
        assert set(identity["reported_client"]) == {"present", "fingerprint"}
        serialized = client.get("/v1/codex-bridge/agents").text
        assert "/Users/" not in serialized
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in serialized
        assert "must-not-survive" not in serialized
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq" not in serialized
        assert "file:///Users/private/client-version" not in serialized
        assert '"admin"' not in serialized
        assert '"root"' not in serialized

    with TestClient(create_app(configured)) as reopened:
        persisted = reopened.get("/v1/codex-bridge/status").json()
        assert persisted["availability"] == "ready"
        assert persisted["capability_snapshots"][0]["observed_at"]


def test_configured_token_rotation_does_not_inherit_previous_snapshot(settings) -> None:
    old_settings = replace(settings, mcp_token="configured-old-token")
    with TestClient(create_app(old_settings)) as old_client:
        _mcp(old_client, "configured-old-token", "initialize")
        assert old_client.get("/v1/codex-bridge/status").json()["availability"] == "ready"

    new_settings = replace(settings, mcp_token="configured-new-token")
    with TestClient(create_app(new_settings)) as new_client:
        before_handshake = new_client.get("/v1/codex-bridge/status").json()
        assert before_handshake["availability"] == "disconnected"
        assert before_handshake["reason"] == "handshake_not_observed"
        assert before_handshake["capabilities"] == []
        assert before_handshake["capability_snapshots"] == []
        agent = new_client.get("/v1/codex-bridge/agents").json()[0]
        assert agent["reason"] == "handshake_not_observed"
        assert agent["capabilities"] == []
        historical = new_client.get("/v1/codex-bridge/agents/audit").json()["items"][0]
        assert historical["detail"]["authority_status"] == "historical_expired"

        old_denied = new_client.post(
            "/mcp",
            headers={"Authorization": "Bearer configured-old-token"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert old_denied.status_code == 401
        _mcp(new_client, "configured-new-token", "tools/list")
        assert new_client.get("/v1/codex-bridge/status").json()["availability"] == "ready"


def test_capability_snapshot_ttl_fails_closed_and_tools_list_refreshes(
    settings, monkeypatch
) -> None:
    configured = replace(settings, mcp_token="ttl-token")
    observed_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    clock = {"now": observed_at}
    monkeypatch.setattr(codex_bridge_store, "utc_now", lambda: clock["now"].isoformat())

    with TestClient(create_app(configured)) as client:
        _mcp(client, "ttl-token", "initialize", {"clientInfo": {"name": "ttl-client"}})
        fresh = client.get("/v1/codex-bridge/status").json()
        assert fresh["availability"] == "ready"
        assert fresh["capability_snapshots"][0]["fresh"] is True
        assert (
            fresh["capability_snapshots"][0]["expires_at"]
            == (
                observed_at + timedelta(seconds=codex_bridge_store.CAPABILITY_SNAPSHOT_TTL_SECONDS)
            ).isoformat()
        )

        clock["now"] = observed_at + timedelta(
            seconds=codex_bridge_store.CAPABILITY_SNAPSHOT_TTL_SECONDS + 1
        )
        stale = client.get("/v1/codex-bridge/status").json()
        assert stale["availability"] == "disconnected"
        assert stale["reason"] == "capability_snapshot_stale"
        assert stale["mcp"]["status"] == "disconnected"
        assert stale["capabilities"] == []
        assert stale["capability_snapshots"] == []

        agent = client.get("/v1/codex-bridge/agents").json()[0]
        assert agent["availability"] == "disconnected"
        assert agent["reason"] == "capability_snapshot_stale"
        assert agent["capabilities"] == []
        assert agent["capability_snapshot"]["authority_status"] == "historical_expired"
        assert agent["capability_snapshot"]["capabilities"] == []
        discovery = client.get("/v1/codex-bridge/agents/audit").json()["items"][0]
        assert discovery["detail"]["authority_status"] == "historical_expired"
        assert "capabilities" not in discovery["detail"]

        _mcp(client, "ttl-token", "tools/list")
        refreshed = client.get("/v1/codex-bridge/status").json()
        assert refreshed["availability"] == "ready"
        assert refreshed["capability_snapshots"][0]["fresh"] is True


def test_capability_snapshot_is_bound_to_the_exact_active_token(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        integration = client.post(
            "/v1/codex-bridge/clients", json={"name": "Multi-token client"}
        ).json()
        token_a = client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": integration["id"],
                "project_id": "project-rag",
                "scopes": ["read"],
            },
        ).json()
        _mcp(client, token_a["token"], "initialize")
        assert client.get("/v1/codex-bridge/status").json()["availability"] == "ready"

        token_b = client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": integration["id"],
                "project_id": "project-rag",
                "scopes": ["read"],
            },
        ).json()
        assert client.delete(f"/v1/codex-bridge/tokens/{token_a['id']}").status_code == 200

        inherited = client.get("/v1/codex-bridge/status").json()
        assert inherited["availability"] == "disconnected"
        assert inherited["reason"] == "handshake_not_observed"
        assert inherited["capabilities"] == []
        agent = client.get("/v1/codex-bridge/agents").json()[0]
        assert agent["availability"] == "disconnected"
        assert agent["reason"] == "handshake_not_observed"
        assert agent["capabilities"] == []

        _mcp(client, token_b["token"], "tools/list")
        connected = client.get("/v1/codex-bridge/status").json()
        assert connected["availability"] == "ready"
        assert len(connected["capability_snapshots"]) == 1

    with TestClient(create_app(settings)) as reopened:
        persisted = reopened.get("/v1/codex-bridge/status").json()
        assert persisted["availability"] == "ready"
        assert len(persisted["capability_snapshots"]) == 1


def test_legacy_client_only_snapshot_migrates_as_non_authoritative(settings) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    with TestClient(app) as client:
        integration = client.post(
            "/v1/codex-bridge/clients", json={"name": "Legacy snapshot client"}
        ).json()
        client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": integration["id"],
                "project_id": "project-rag",
                "scopes": ["read"],
            },
        )
        with runtime.store.transaction() as db:
            db.execute("DROP TABLE integration_capability_snapshots")
            db.executescript(
                """CREATE TABLE integration_capability_snapshots (
                       id TEXT PRIMARY KEY,
                       project_id TEXT NOT NULL,
                       client_id TEXT NOT NULL,
                       server_version TEXT NOT NULL,
                       protocol_version TEXT NOT NULL,
                       capabilities_json TEXT NOT NULL,
                       handshake_identity_json TEXT NOT NULL DEFAULT '{}',
                       observed_at TEXT NOT NULL,
                       UNIQUE(project_id, client_id)
                   );"""
            )
            db.execute(
                """INSERT INTO integration_capability_snapshots
                   (id, project_id, client_id, server_version, protocol_version,
                    capabilities_json, handshake_identity_json, observed_at)
                   VALUES ('capability-snapshot://legacy', 'project-rag', ?, '0.1.0',
                           '2025-06-18', '["project_get_context"]', '{}', ?)""",
                (integration["id"], codex_bridge_store.utc_now()),
            )

        runtime.codex_bridge.store.initialize()
        migrated = runtime.codex_bridge.store.list_capability_snapshots("project-rag")
        assert migrated[0]["authority_id"].startswith("legacy-unbound:")
        status = client.get("/v1/codex-bridge/status").json()
        assert status["availability"] == "disconnected"
        assert status["reason"] == "handshake_not_observed"
        assert status["capabilities"] == []


def test_token_expiry_uses_absolute_utc_time_and_malformed_rows_fail_closed(
    settings, monkeypatch
) -> None:
    current = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(codex_bridge_store, "utc_now", lambda: current.isoformat())
    app = create_app(settings)
    runtime = app.state.runtime
    with TestClient(app) as client:
        integration = client.post("/v1/codex-bridge/clients", json={"name": "Expiry client"}).json()
        expired = client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": integration["id"],
                "project_id": "project-rag",
                "scopes": ["read"],
                "expires_at": "2026-08-01T13:59:59+14:00",
            },
        )
        assert expired.status_code == 201
        assert expired.json()["expires_at"] == "2026-07-31T23:59:59Z"
        assert (
            client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {expired.json()['token']}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            ).status_code
            == 401
        )

        future = client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": integration["id"],
                "project_id": "project-rag",
                "scopes": ["read"],
                "expires_at": "2026-08-01T10:30:00+08:00",
            },
        )
        assert future.status_code == 201
        assert future.json()["expires_at"] == "2026-08-01T02:30:00Z"
        _mcp(client, future.json()["token"], "initialize")
        assert client.get("/v1/codex-bridge/status").json()["availability"] == "ready"

        with runtime.store.transaction() as db:
            db.execute(
                "UPDATE integration_tokens SET expires_at='legacy-not-a-time' WHERE id=?",
                (future.json()["id"],),
            )
        malformed_denied = client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {future.json()['token']}"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert malformed_denied.status_code == 401
        unavailable = client.get("/v1/codex-bridge/status").json()
        assert unavailable["availability"] in {"unauthorized", "unavailable"}
        assert unavailable["capabilities"] == []
        agent = client.get("/v1/codex-bridge/agents").json()[0]
        assert agent["availability"] == "unauthorized"
        assert agent["capabilities"] == []
        audit = client.get("/v1/codex-bridge/agents/audit").json()["items"][0]
        assert audit["detail"]["authority_status"] == "historical_expired"

        naive = client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": integration["id"],
                "project_id": "project-rag",
                "scopes": ["read"],
                "expires_at": "2026-08-02T00:00:00",
            },
        )
        assert naive.status_code == 422


def test_capability_discovery_events_coalesce_and_remain_authority_scoped(
    settings, monkeypatch
) -> None:
    clock = {"now": datetime(2026, 8, 1, 0, 0, tzinfo=UTC)}
    monkeypatch.setattr(codex_bridge_store, "utc_now", lambda: clock["now"].isoformat())
    app = create_app(settings)
    with TestClient(app) as client:
        client.post("/v1/projects", json={"id": "project-other", "name": "Other"})
        first_client = client.post("/v1/codex-bridge/clients", json={"name": "First"}).json()
        second_client = client.post("/v1/codex-bridge/clients", json={"name": "Second"}).json()
        first_token = client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": first_client["id"],
                "project_id": "project-rag",
                "scopes": ["read"],
            },
        ).json()["token"]
        second_token = client.post(
            "/v1/codex-bridge/tokens",
            json={
                "client_id": second_client["id"],
                "project_id": "project-other",
                "scopes": ["read"],
            },
        ).json()["token"]

        for offset in range(25):
            clock["now"] = datetime(2026, 8, 1, 0, 0, offset, tzinfo=UTC)
            _mcp(client, first_token, "tools/list")
        first_latest = clock["now"].isoformat()
        _mcp(client, second_token, "tools/list")

        with app.state.runtime.store.connection() as db:
            rows = db.execute(
                """SELECT project_id, client_id, count(*) AS event_count,
                          max(created_at) AS latest
                     FROM integration_agent_events
                    WHERE event_type='capability.discovery'
                    GROUP BY project_id, client_id"""
            ).fetchall()
        observed = {
            (row["project_id"], row["client_id"]): (row["event_count"], row["latest"])
            for row in rows
        }
        assert observed[("project-rag", first_client["id"])] == (1, first_latest)
        assert observed[("project-other", second_client["id"])][0] == 1


def test_agent_audit_merges_sanitized_server_lifecycle_and_enforces_acl(settings) -> None:
    configured = replace(
        settings,
        deployment_mode="production",
        api_token="production-token",
        enforce_acl=True,
        trusted_acl_refs=("project:project-rag", "project:other"),
    )
    allowed = {
        "Authorization": "Bearer production-token",
        "X-RAG-ACL-Refs": "project:project-rag",
    }
    denied = {
        "Authorization": "Bearer production-token",
        "X-RAG-ACL-Refs": "project:other",
    }
    app = create_app(configured)
    with TestClient(app) as client:
        work = client.post(
            "/v1/research/work-items",
            headers=allowed,
            json={"title": "Agent audit", "status": "ready"},
        ).json()
        integration = client.post(
            "/v1/codex-bridge/clients",
            headers=allowed,
            json={"name": "Audited client", "version": "1"},
        ).json()
        token_record = client.post(
            "/v1/codex-bridge/tokens",
            headers=allowed,
            json={
                "client_id": integration["id"],
                "project_id": "project-rag",
                "scopes": ["read", "write"],
            },
        ).json()
        token = token_record["token"]
        _mcp(
            client,
            token,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "clientInfo": {"name": "audit-client", "version": "1"},
            },
        )
        _mcp(client, token, "tools/list")
        execution = client.post(
            "/v1/codex-bridge/executions",
            headers=allowed,
            json={
                "project_id": "project-rag",
                "work_item_id": work["id"],
                "mode": "interactive",
                "prompt": "password=do-not-store /Users/private/prompt",
            },
        ).json()
        _mcp(
            client,
            token,
            "tools/call",
            {
                "name": "execution_claim",
                "arguments": {
                    "execution_id": execution["id"],
                    "thread_id": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq",
                    "idempotency_key": "audit-claim-001",
                },
            },
        )
        _mcp(
            client,
            token,
            "tools/call",
            {
                "name": "execution_report",
                "arguments": {
                    "execution_id": execution["id"],
                    "status": "review",
                    "summary": "ZYXWVUTSRQPONMLKJIHGFEDCBAabcdefghijklmnopq",
                    "changed_files": ["/Users/private/secret.py"],
                    "error": "file:///Users/private/must-not-survive",
                    "idempotency_key": "audit-report-001",
                },
            },
        )
        approval = _mcp(
            client,
            token,
            "tools/call",
            {
                "name": "execution_request_review",
                "arguments": {
                    "execution_id": execution["id"],
                    "summary": "secret=approval-secret",
                    "idempotency_key": "audit-review-001",
                },
            },
        )["result"]["structuredContent"]
        decided = client.post(
            "/v1/codex-bridge/approvals/decide",
            params={"approval_id": approval["id"]},
            headers=allowed,
            json={"decision": "rejected", "note": "password=hidden"},
        )
        assert decided.status_code == 200
        with app.state.runtime.store.transaction() as db:
            db.execute(
                """UPDATE codex_execution_events
                      SET created_at='2026-08-01T12:00:00+00:00'
                    WHERE execution_id=?""",
                (execution["id"],),
            )

        forbidden = client.get(
            "/v1/codex-bridge/agents/audit",
            params={"project_id": "project-rag", "client_id": integration["id"]},
            headers=denied,
        )
        audit = client.get(
            "/v1/codex-bridge/agents/audit",
            params={
                "project_id": "project-rag",
                "client_id": integration["id"],
                "limit": 50,
            },
            headers=allowed,
        )
        assert forbidden.status_code == 404
        assert audit.status_code == 200
        event_types = {item["event_type"] for item in audit.json()["items"]}
        assert {
            "mcp.handshake",
            "capability.discovery",
            "execution.claimed",
            "execution.report",
            "approval.requested",
            "approval.decided",
        } <= event_types
        execution_sequences = [
            item["detail"]["sequence"]
            for item in audit.json()["items"]
            if item["event_type"].startswith("execution.")
        ]
        assert execution_sequences == sorted(execution_sequences, reverse=True)
        serialized = audit.text
        assert "/Users/" not in serialized
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in serialized
        assert "must-not-survive" not in serialized
        assert "approval-secret" not in serialized
        assert "password=hidden" not in serialized
        assert '"token"' not in serialized
        assert '"prompt"' not in serialized
        agents_response = client.get(
            "/v1/codex-bridge/agents",
            params={"project_id": "project-rag"},
            headers=allowed,
        )
        assert agents_response.status_code == 200
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in agents_response.text
        assert "must-not-survive" not in agents_response.text
        public_execution = agents_response.json()[0]["recent_executions"][0]
        assert public_execution["summary"] == "[redacted]"
        assert public_execution["error"] == "[redacted]"
        assert public_execution["thread_id"].startswith("thread://sha256:")
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq" not in agents_response.text
        assert "ZYXWVUTSRQPONMLKJIHGFEDCBAabcdefghijklmnopq" not in agents_response.text
        with app.state.runtime.store.connection() as db:
            persisted_audit = " ".join(
                row[0]
                for row in db.execute("SELECT detail_json FROM integration_agent_events").fetchall()
            )
            persisted_execution_events = " ".join(
                " ".join(str(value or "") for value in row)
                for row in db.execute(
                    """SELECT summary, payload_json, artifact_path
                       FROM codex_execution_events"""
                ).fetchall()
            )
        persisted = persisted_audit + persisted_execution_events
        assert "/Users/" not in persisted
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in persisted
        assert "must-not-survive" not in persisted
        assert "approval-secret" not in persisted
        with app.state.runtime.store.transaction() as db:
            db.execute(
                """UPDATE approval_requests
                      SET created_at='2020-01-01T00:00:00+00:00',
                          decided_at='2099-01-01T00:00:00+00:00'
                    WHERE id=?""",
                (approval["id"],),
            )
            db.executemany(
                """INSERT INTO approval_requests
                   (id, project_id, execution_id, work_item_id, action, status,
                    requested_by, payload_json, created_at)
                   VALUES (?, 'project-rag', ?, ?, 'noise', 'pending',
                           'test', '{}', '2080-01-01T00:00:00+00:00')""",
                [
                    (f"approval://pending-{index:03d}", execution["id"], work["id"])
                    for index in range(101)
                ],
            )
        limited_audit = client.get(
            "/v1/codex-bridge/agents/audit",
            params={
                "project_id": "project-rag",
                "client_id": integration["id"],
                "limit": 10,
            },
            headers=allowed,
        ).json()["items"]
        assert any(
            item["event_type"] == "approval.decided" and item["resource_id"] == approval["id"]
            for item in limited_audit
        )
        cross_project_revoke = client.delete(
            f"/v1/codex-bridge/tokens/{token_record['id']}", headers=denied
        )
        assert cross_project_revoke.status_code == 404
        assert _mcp(client, token, "ping")["result"] == {}
        authorized_revoke = client.delete(
            f"/v1/codex-bridge/tokens/{token_record['id']}", headers=allowed
        )
        assert authorized_revoke.status_code == 200


def test_bridge_sync_projection_distinguishes_no_data_failure_and_ready(settings) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    with TestClient(app) as client:
        empty = client.get("/v1/codex-bridge/status").json()["sync"]
        assert empty == {
            "status": "no_data",
            "reason": "source_not_configured",
            "source_status": "unconfigured",
            "indexed_sessions": 0,
            "source_count": 0,
            "active_workflow": False,
            "last_sync_at": None,
            "data_available": False,
        }

        runtime.store.create_workflow(
            "wf-codex-failed-projection",
            "codex:/Users/private/source",
            {"project_id": "project-rag", "source": "/Users/private/source"},
        )
        runtime.store.update_workflow(
            "wf-codex-failed-projection",
            status="failed",
            error="api_key=ABCDEFGHIJKLMNOPQRSTUVWXYZ123456 /Users/private/error",
        )
        failed_response = client.get("/v1/codex-bridge/status")
        assert failed_response.json()["sync"]["status"] == "sync_failed"
        assert failed_response.json()["sync"]["reason"] == "last_sync_failed"
        assert "/Users/" not in failed_response.text
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in failed_response.text

        with runtime.store.transaction() as db:
            db.execute(
                """INSERT INTO codex_sources
                   (id, project_id, source_path, project_path, acl_ref, status,
                    active_generation_id, stats_json, created_at, updated_at, last_error)
                   VALUES ('codex-source://ready', 'project-rag', '/Users/private/source',
                           '/Users/private/project', 'project:project-rag', 'ready',
                           'codex-generation://ready', '{}', '2026-08-01T00:00:00+00:00',
                           '2026-08-01T00:00:01+00:00', NULL)"""
            )
            db.execute(
                """INSERT INTO codex_generations
                   (id, source_id, status, adapter_version, counts_json, validation_json,
                    started_at, completed_at)
                   VALUES ('codex-generation://ready', 'codex-source://ready', 'published',
                           'test', '{}', '{}', '2026-08-01T00:00:00+00:00',
                           '2026-08-01T00:00:01+00:00')"""
            )
            db.execute(
                """INSERT INTO codex_threads
                   (id, thread_id, source_id, generation_id, project_id, title, cwd,
                    status, source_file, source_hash, acl_ref, metadata_json)
                   VALUES ('codex-thread://ready', 'thread-ready', 'codex-source://ready',
                           'codex-generation://ready', 'project-rag', 'Ready thread',
                           '/Users/private/project', 'completed', 'session.jsonl', 'hash',
                           'project:project-rag', '{}')"""
            )
        runtime.store.update_workflow(
            "wf-codex-failed-projection", status="completed", error="cleared"
        )
        with runtime.store.transaction() as db:
            db.execute("UPDATE workflows SET error=NULL WHERE id='wf-codex-failed-projection'")
        ready = client.get("/v1/codex-bridge/status").json()["sync"]
        assert ready["status"] == "ready"
        assert ready["reason"] == "indexed_sessions_available"
        assert ready["indexed_sessions"] == 1
        assert ready["data_available"] is True


def test_bridge_sync_projection_filters_project_before_global_limit(settings) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    with TestClient(app) as client:
        runtime.store.create_workflow(
            "wf-codex-target-failure",
            "codex:target",
            {"project_id": "project-rag"},
        )
        runtime.store.update_workflow(
            "wf-codex-target-failure", status="failed", error="target failed"
        )
        with runtime.store.transaction() as db:
            db.executemany(
                """INSERT INTO workflows
                   (id, source, status, stage, progress, request_json, counters_json,
                    created_at, updated_at)
                   VALUES (?, 'codex:other', 'completed', 'done', 100,
                           '{"project_id":"project-other"}', '{}',
                           '9999-01-01T00:00:00+00:00', '9999-01-01T00:00:00+00:00')""",
                [(f"wf-codex-unrelated-{index:03d}",) for index in range(101)],
            )
            db.execute(
                """INSERT INTO workflows
                   (id, source, status, stage, progress, request_json, counters_json,
                    created_at, updated_at)
                   VALUES ('wf-codex-malformed', 'codex:legacy', 'running', 'discover', 0,
                           '{', '{}', '9999-01-02T00:00:00+00:00',
                           '9999-01-02T00:00:00+00:00')"""
            )

        sync = client.get("/v1/codex-bridge/status").json()["sync"]
        assert sync["status"] == "sync_failed"
        assert sync["reason"] == "last_sync_failed"


def test_bridge_sync_projection_requires_published_ready_generation(settings) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    with TestClient(app) as client:
        with runtime.store.transaction() as db:
            db.execute(
                """INSERT INTO codex_sources
                   (id, project_id, source_path, project_path, acl_ref, status,
                    active_generation_id, stats_json, created_at, updated_at, last_error)
                   VALUES ('codex-source://building', 'project-rag', '/private/source',
                           '/private/project', 'project:project-rag', 'indexing',
                           'codex-generation://building', '{}',
                           '2026-08-01T00:00:00+00:00',
                           '2026-08-01T00:00:01+00:00', NULL)"""
            )
            db.execute(
                """INSERT INTO codex_generations
                   (id, source_id, status, adapter_version, counts_json, validation_json,
                    started_at)
                   VALUES ('codex-generation://building', 'codex-source://building',
                           'building', 'test', '{}', '{}',
                           '2026-08-01T00:00:00+00:00')"""
            )
            db.execute(
                """INSERT INTO codex_threads
                   (id, thread_id, source_id, generation_id, project_id, title, cwd,
                    status, source_file, source_hash, acl_ref, metadata_json)
                   VALUES ('codex-thread://building', 'thread-building',
                           'codex-source://building', 'codex-generation://building',
                           'project-rag', 'Building thread', '/private/project', 'completed',
                           'session.jsonl', 'hash', 'project:project-rag', '{}')"""
            )

        building = client.get("/v1/codex-bridge/status").json()["sync"]
        assert building["status"] == "syncing"
        assert building["indexed_sessions"] == 0
        assert building["data_available"] is False

        with runtime.store.transaction() as db:
            db.execute("UPDATE codex_sources SET status='ready' WHERE id='codex-source://building'")
        generation_not_published = client.get("/v1/codex-bridge/status").json()["sync"]
        assert generation_not_published["status"] == "syncing"
        assert generation_not_published["indexed_sessions"] == 0

        with runtime.store.transaction() as db:
            db.execute(
                """UPDATE codex_generations SET status='published',
                          completed_at='2026-08-01T00:00:02+00:00'
                    WHERE id='codex-generation://building'"""
            )
        published = client.get("/v1/codex-bridge/status").json()["sync"]
        assert published["status"] == "ready"
        assert published["indexed_sessions"] == 1
        assert published["data_available"] is True

        with runtime.store.transaction() as db:
            db.execute(
                "UPDATE codex_generations SET status='failed' WHERE id='codex-generation://building'"
            )
        failed_generation = client.get("/v1/codex-bridge/status").json()["sync"]
        assert failed_generation["status"] == "sync_failed"
        assert failed_generation["indexed_sessions"] == 0
        assert failed_generation["data_available"] is False


def test_bridge_sync_projection_ignores_stale_non_active_building_generation(settings) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    with TestClient(app) as client:
        with runtime.store.transaction() as db:
            db.execute(
                """INSERT INTO codex_sources
                   (id, project_id, source_path, project_path, acl_ref, status,
                    active_generation_id, stats_json, created_at, updated_at, last_error)
                   VALUES ('codex-source://ready', 'project-rag', '/private/source',
                           '/private/project', 'project:project-rag', 'ready',
                           'codex-generation://published', '{}',
                           '2026-08-01T00:00:00+00:00',
                           '2026-08-01T00:00:03+00:00', NULL)"""
            )
            db.executemany(
                """INSERT INTO codex_generations
                   (id, source_id, status, adapter_version, counts_json, validation_json,
                    started_at, completed_at)
                   VALUES (?, 'codex-source://ready', ?, 'test', '{}', '{}',
                           '2026-08-01T00:00:00+00:00', ?)""",
                [
                    (
                        "codex-generation://stale-building",
                        "building",
                        None,
                    ),
                    (
                        "codex-generation://published",
                        "published",
                        "2026-08-01T00:00:02+00:00",
                    ),
                ],
            )
            db.execute(
                """INSERT INTO codex_threads
                   (id, thread_id, source_id, generation_id, project_id, title, cwd,
                    status, source_file, source_hash, acl_ref, metadata_json)
                   VALUES ('codex-thread://published', 'thread-published',
                           'codex-source://ready', 'codex-generation://published',
                           'project-rag', 'Published thread', '/private/project', 'completed',
                           'session.jsonl', 'hash', 'project:project-rag', '{}')"""
            )

        sync = client.get("/v1/codex-bridge/status").json()["sync"]
        assert sync["status"] == "ready"
        assert sync["reason"] == "indexed_sessions_available"
        assert sync["indexed_sessions"] == 1
        assert sync["active_workflow"] is False
        assert sync["data_available"] is True


def test_bridge_sync_projection_rejects_cross_project_and_acl_mismatched_threads(
    settings,
) -> None:
    app = create_app(settings)
    runtime = app.state.runtime
    with TestClient(app) as client:
        with runtime.store.transaction() as db:
            db.executemany(
                """INSERT INTO codex_sources
                   (id, project_id, source_path, acl_ref, status, active_generation_id,
                    stats_json, created_at, updated_at)
                   VALUES (?, ?, '/private/source', ?, 'ready', ?, '{}',
                           '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:01+00:00')""",
                [
                    (
                        "codex-source://cross-project",
                        "project-other",
                        "project:project-other",
                        "codex-generation://cross-project",
                    ),
                    (
                        "codex-source://acl-mismatch",
                        "project-rag",
                        "project:project-rag",
                        "codex-generation://acl-mismatch",
                    ),
                ],
            )
            db.executemany(
                """INSERT INTO codex_generations
                   (id, source_id, status, adapter_version, started_at)
                   VALUES (?, ?, 'published', 'test', '2026-08-01T00:00:00+00:00')""",
                [
                    ("codex-generation://cross-project", "codex-source://cross-project"),
                    ("codex-generation://acl-mismatch", "codex-source://acl-mismatch"),
                ],
            )
            db.executemany(
                """INSERT INTO codex_threads
                   (id, thread_id, source_id, generation_id, project_id, title, cwd,
                    status, source_file, source_hash, acl_ref, metadata_json)
                   VALUES (?, ?, ?, ?, 'project-rag', 'Unsafe thread', '/private',
                           'completed', 'session.jsonl', 'hash', ?, '{}')""",
                [
                    (
                        "codex-thread://cross-project",
                        "thread-cross-project",
                        "codex-source://cross-project",
                        "codex-generation://cross-project",
                        "project:project-other",
                    ),
                    (
                        "codex-thread://acl-mismatch",
                        "thread-acl-mismatch",
                        "codex-source://acl-mismatch",
                        "codex-generation://acl-mismatch",
                        "project:other",
                    ),
                ],
            )

        sync = client.get("/v1/codex-bridge/status").json()["sync"]
        assert sync["status"] == "no_data"
        assert sync["indexed_sessions"] == 0
        assert sync["data_available"] is False


def test_bridge_sync_projection_fails_closed_when_persistence_is_unavailable(
    settings, monkeypatch
) -> None:
    app = create_app(settings)

    def unavailable(_project_id: str):
        raise RuntimeError("secret=do-not-leak /Users/private/database")

    monkeypatch.setattr(app.state.runtime.codex_bridge.store, "codex_sync_projection", unavailable)
    with TestClient(app) as client:
        response = client.get("/v1/codex-bridge/status")

    assert response.status_code == 200
    assert response.json()["sync"] == {
        "status": "unavailable",
        "reason": "sync_status_unavailable",
        "source_status": "unknown",
        "indexed_sessions": 0,
        "source_count": 0,
        "active_workflow": False,
        "last_sync_at": None,
        "data_available": False,
    }
    assert "do-not-leak" not in response.text
    assert "/Users/" not in response.text
