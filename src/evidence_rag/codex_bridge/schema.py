from __future__ import annotations

CODEX_BRIDGE_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS integration_clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'codex-plugin',
    version TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_tokens (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(client_id) REFERENCES integration_clients(id),
    FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_integration_tokens_project
    ON integration_tokens(project_id, client_id, revoked_at);

CREATE TABLE IF NOT EXISTS codex_executions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    client_id TEXT,
    thread_id TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    sandbox TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    base_ref TEXT,
    base_commit TEXT,
    result_commit TEXT,
    prompt TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    validation_json TEXT NOT NULL DEFAULT '[]',
    changed_files_json TEXT NOT NULL DEFAULT '[]',
    context_snapshot_json TEXT NOT NULL DEFAULT '{}',
    artifact_path TEXT,
    error TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(work_item_id) REFERENCES research_work_items(id),
    FOREIGN KEY(client_id) REFERENCES integration_clients(id)
);
CREATE INDEX IF NOT EXISTS idx_codex_executions_project
    ON codex_executions(project_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS codex_execution_events (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    artifact_path TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(execution_id, sequence),
    FOREIGN KEY(execution_id) REFERENCES codex_executions(id)
);
CREATE INDEX IF NOT EXISTS idx_codex_events_execution
    ON codex_execution_events(execution_id, sequence);

CREATE TABLE IF NOT EXISTS approval_requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    execution_id TEXT,
    work_item_id TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_by TEXT NOT NULL,
    decided_by TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(execution_id) REFERENCES codex_executions(id),
    FOREIGN KEY(work_item_id) REFERENCES research_work_items(id)
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_project
    ON approval_requests(project_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS integration_idempotency (
    client_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(client_id, project_id, idempotency_key)
);
"""
