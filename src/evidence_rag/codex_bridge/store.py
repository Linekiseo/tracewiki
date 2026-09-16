from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ..storage import SQLiteStore, utc_now
from .schema import CODEX_BRIDGE_SCHEMA

CODEX_BRIDGE_OBSERVABILITY_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS integration_capability_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    authority_id TEXT NOT NULL,
    server_version TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    handshake_identity_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL,
    UNIQUE(project_id, authority_id),
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(client_id) REFERENCES integration_clients(id)
);
CREATE INDEX IF NOT EXISTS idx_integration_capability_project
    ON integration_capability_snapshots(project_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS integration_agent_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    client_id TEXT,
    event_type TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(client_id) REFERENCES integration_clients(id)
);
CREATE INDEX IF NOT EXISTS idx_integration_agent_event_project
    ON integration_agent_events(project_id, created_at DESC);
"""

_SECRET_RE = re.compile(
    r"(?i)(?:(?:api[ _-]?key|access[ _-]?token|authorization|credential|token|password|secret)"
    r"\s*(?::|=|\s)\s*[^\s,;]+|(?:bearer|basic)\s+[^\s,;]+"
    r"|gh[pousr]_[a-z0-9]{20,}|sk-[a-z0-9_-]{20,})"
)
_ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s\"'])(?:/(?!/)|[A-Za-z]:[\\/]|~[/\\]|\\\\[^\\\s]+\\)")
_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "token_hash",
        "secret",
        "password",
        "path",
        "prompt",
        "content",
        "summary",
        "thread_id",
    }
)
CAPABILITY_SNAPSHOT_TTL_SECONDS = 15 * 60
_CAPABILITY_CLOCK_SKEW_SECONDS = 5
_AGENT_EVENT_COALESCE_SECONDS = 30
_AGENT_EVENT_RETENTION_PER_CLIENT = 500


def _decoded(row: sqlite3.Row | None, *fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in fields:
        result[field] = json.loads(result.pop(f"{field}_json", None) or "[]")
    return result


class CodexBridgeStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(CODEX_BRIDGE_SCHEMA)
            db.executescript(CODEX_BRIDGE_OBSERVABILITY_SCHEMA)
            snapshot_columns = {
                row["name"]
                for row in db.execute(
                    "PRAGMA table_info(integration_capability_snapshots)"
                ).fetchall()
            }
            if "authority_id" not in snapshot_columns:
                # Preserve pre-authority observations as reviewable, never-active history.
                db.executescript(
                    """CREATE TABLE integration_capability_snapshots_v2 (
                           id TEXT PRIMARY KEY,
                           project_id TEXT NOT NULL,
                           client_id TEXT NOT NULL,
                           authority_id TEXT NOT NULL,
                           server_version TEXT NOT NULL,
                           protocol_version TEXT NOT NULL,
                           capabilities_json TEXT NOT NULL,
                           handshake_identity_json TEXT NOT NULL DEFAULT '{}',
                           observed_at TEXT NOT NULL,
                           UNIQUE(project_id, authority_id),
                           FOREIGN KEY(project_id) REFERENCES projects(id),
                           FOREIGN KEY(client_id) REFERENCES integration_clients(id)
                       );
                       INSERT INTO integration_capability_snapshots_v2
                           (id, project_id, client_id, authority_id, server_version,
                            protocol_version, capabilities_json, handshake_identity_json,
                            observed_at)
                       SELECT id, project_id, client_id, 'legacy-unbound:' || id,
                              server_version, protocol_version, capabilities_json,
                              handshake_identity_json, observed_at
                         FROM integration_capability_snapshots;
                       DROP TABLE integration_capability_snapshots;
                       ALTER TABLE integration_capability_snapshots_v2
                           RENAME TO integration_capability_snapshots;
                       CREATE INDEX IF NOT EXISTS idx_integration_capability_project
                           ON integration_capability_snapshots(project_id, observed_at DESC);"""
                )

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                return None
            return value.astimezone(UTC)
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)

    @classmethod
    def _canonical_timestamp(cls, value: Any) -> str | None:
        if value is None:
            return None
        parsed = cls._timestamp(value)
        if parsed is None:
            raise ValueError("timestamp must be a valid timezone-aware ISO 8601 value")
        return parsed.isoformat().replace("+00:00", "Z")

    @classmethod
    def _token_is_active(cls, token: dict[str, Any], *, now: str | None = None) -> bool:
        current = cls._timestamp(now or utc_now())
        if current is None or token.get("revoked_at") is not None:
            return False
        if token.get("client_status", token.get("status")) != "active":
            return False
        raw_expiry = token.get("expires_at")
        if raw_expiry is None:
            return True
        expires_at = cls._timestamp(raw_expiry)
        return expires_at is not None and current < expires_at

    @classmethod
    def capability_snapshot_is_fresh(
        cls, snapshot: dict[str, Any], *, now: str | None = None
    ) -> bool:
        """Treat a snapshot as authority only during the server-defined TTL."""

        observed = cls._timestamp(snapshot.get("observed_at"))
        current = cls._timestamp(now or utc_now())
        if observed is None or current is None:
            return False
        if observed > current + timedelta(seconds=_CAPABILITY_CLOCK_SKEW_SECONDS):
            return False
        return current < observed + timedelta(seconds=CAPABILITY_SNAPSHOT_TTL_SECONDS)

    @classmethod
    def _with_snapshot_freshness(cls, snapshot: dict[str, Any]) -> dict[str, Any]:
        observed = cls._timestamp(snapshot.get("observed_at"))
        expires_at = (
            (observed + timedelta(seconds=CAPABILITY_SNAPSHOT_TTL_SECONDS)).isoformat()
            if observed is not None
            else None
        )
        return {
            **snapshot,
            "expires_at": expires_at,
            "fresh": cls.capability_snapshot_is_fresh(snapshot),
        }

    @classmethod
    def _safe_observation(cls, value: Any) -> Any:
        """Bound observability data and remove credentials and host-local paths."""

        if isinstance(value, dict):
            return {
                str(key): cls._safe_observation(item)
                for key, item in value.items()
                if str(key).casefold() not in _SENSITIVE_KEYS
                and "token" not in str(key).casefold()
                and "secret" not in str(key).casefold()
                and "path" not in str(key).casefold()
            }
        if isinstance(value, (list, tuple)):
            return [cls._safe_observation(item) for item in value[:200]]
        if isinstance(value, str):
            if _SECRET_RE.search(value) or _ABSOLUTE_PATH_RE.search(value):
                return "[redacted]"
            return value[:512]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:512]

    def record_capability_snapshot(
        self,
        *,
        project_id: str,
        client_id: str,
        authority_id: str,
        server_version: str,
        protocol_version: str,
        capabilities: list[str],
        handshake_identity: dict[str, Any],
        event_type: str,
    ) -> dict[str, Any]:
        """Persist only capabilities offered by this server to an authenticated principal."""

        observed_at = utc_now()
        normalized_capabilities = sorted(
            {
                capability
                for capability in capabilities
                if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", capability)
            }
        )
        safe_identity = self._safe_observation(handshake_identity)
        snapshot_id = (
            "capability-snapshot://sha256:"
            + hashlib.sha256(f"{project_id}\x1f{authority_id}".encode()).hexdigest()
        )
        event_id = f"agent-event://{uuid4().hex}"
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO integration_capability_snapshots
                   (id, project_id, client_id, authority_id, server_version, protocol_version,
                    capabilities_json, handshake_identity_json, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id, authority_id) DO UPDATE SET
                     client_id=excluded.client_id,
                     server_version=excluded.server_version,
                     protocol_version=excluded.protocol_version,
                     capabilities_json=excluded.capabilities_json,
                     handshake_identity_json=excluded.handshake_identity_json,
                     observed_at=excluded.observed_at""",
                (
                    snapshot_id,
                    project_id,
                    client_id,
                    authority_id,
                    server_version,
                    protocol_version,
                    json.dumps(normalized_capabilities),
                    json.dumps(safe_identity, ensure_ascii=False),
                    observed_at,
                ),
            )
            detail_json = json.dumps(
                {
                    "server_version": server_version,
                    "protocol_version": protocol_version,
                    "capability_count": len(normalized_capabilities),
                    "capabilities": normalized_capabilities,
                },
                ensure_ascii=False,
            )
            prior_event = db.execute(
                """SELECT id, created_at FROM integration_agent_events
                   WHERE project_id=? AND client_id=? AND event_type=? AND resource_id=?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (project_id, client_id, event_type, snapshot_id),
            ).fetchone()
            prior_at = self._timestamp(prior_event["created_at"]) if prior_event else None
            current_at = self._timestamp(observed_at)
            coalesce = bool(
                prior_event
                and prior_at is not None
                and current_at is not None
                and prior_at <= current_at
                and current_at - prior_at <= timedelta(seconds=_AGENT_EVENT_COALESCE_SECONDS)
            )
            if coalesce:
                db.execute(
                    """UPDATE integration_agent_events
                       SET detail_json=?, created_at=? WHERE id=?""",
                    (detail_json, observed_at, prior_event["id"]),
                )
            else:
                db.execute(
                    """INSERT INTO integration_agent_events
                       (id, project_id, client_id, event_type, resource_type, resource_id,
                        detail_json, created_at)
                       VALUES (?, ?, ?, ?, 'capability_snapshot', ?, ?, ?)""",
                    (
                        event_id,
                        project_id,
                        client_id,
                        event_type,
                        snapshot_id,
                        detail_json,
                        observed_at,
                    ),
                )
            db.execute(
                """DELETE FROM integration_agent_events
                   WHERE id IN (
                       SELECT id FROM integration_agent_events
                        WHERE project_id=? AND client_id=?
                        ORDER BY created_at DESC, rowid DESC
                        LIMIT -1 OFFSET ?
                   )""",
                (project_id, client_id, _AGENT_EVENT_RETENTION_PER_CLIENT),
            )
        return self.get_capability_snapshot(project_id, authority_id) or {}

    def get_capability_snapshot(self, project_id: str, authority_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT * FROM integration_capability_snapshots
                   WHERE project_id=? AND authority_id=?""",
                (project_id, authority_id),
            ).fetchone()
        snapshot = _decoded(row, "capabilities", "handshake_identity")
        return self._with_snapshot_freshness(snapshot) if snapshot is not None else None

    def list_capability_snapshots(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM integration_capability_snapshots
                   WHERE project_id=? ORDER BY observed_at DESC""",
                (project_id,),
            ).fetchall()
        return [
            self._with_snapshot_freshness(_decoded(row, "capabilities", "handshake_identity") or {})
            for row in rows
        ]

    def upsert_client(self, client_id: str, name: str, version: str | None = None) -> dict:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO integration_clients
                   (id, name, kind, version, status, last_seen_at, created_at, updated_at)
                   VALUES (?, ?, 'codex-plugin', ?, 'active', ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name=excluded.name, version=excluded.version, status='active',
                     last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at""",
                (client_id, name, version, now, now, now),
            )
        return self.get_client(client_id) or {}

    def get_client(self, client_id: str) -> dict | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT * FROM integration_clients WHERE id=?", (client_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_clients(self) -> list[dict]:
        with self.database.connection() as db:
            rows = db.execute(
                "SELECT * FROM integration_clients ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_project_clients(self, project_id: str) -> list[dict[str, Any]]:
        """Return only clients with a token authority for the requested project.

        Token hashes and raw credentials are never returned.  Revoked/expired tokens remain
        visible as audit state but cannot make a client appear connected.
        """

        now = utc_now()
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT c.id, c.name, c.kind, c.version, c.status,
                          c.last_seen_at, c.created_at, c.updated_at,
                          t.id AS token_id, t.scopes_json, t.expires_at,
                          t.revoked_at, t.last_used_at
                   FROM integration_clients c
                   JOIN integration_tokens t ON t.client_id=c.id
                   WHERE t.project_id=?
                   ORDER BY c.updated_at DESC, t.created_at DESC""",
                (project_id,),
            ).fetchall()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            client = grouped.setdefault(
                item["id"],
                {
                    key: item[key]
                    for key in (
                        "id",
                        "name",
                        "kind",
                        "version",
                        "status",
                        "last_seen_at",
                        "created_at",
                        "updated_at",
                    )
                }
                | {"tokens": []},
            )
            active = self._token_is_active(item, now=now)
            parsed_expiry = self._timestamp(item["expires_at"])
            client["tokens"].append(
                {
                    "id": item["token_id"],
                    "scopes": json.loads(item["scopes_json"] or "[]"),
                    "expires_at": (
                        parsed_expiry.isoformat().replace("+00:00", "Z")
                        if parsed_expiry is not None
                        else None
                    ),
                    "revoked_at": item["revoked_at"],
                    "last_used_at": item["last_used_at"],
                    "active": active,
                }
            )
        return list(grouped.values())

    def create_token(
        self,
        *,
        client_id: str,
        project_id: str,
        scopes: list[str],
        expires_at: datetime | str | None,
    ) -> tuple[dict, str]:
        raw_token = secrets.token_urlsafe(32)
        token_hash = self.hash_token(raw_token)
        token_id = f"integration-token://{uuid4().hex}"
        canonical_expiry = self._canonical_timestamp(expires_at)
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO integration_tokens
                   (id, client_id, token_hash, project_id, scopes_json, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    token_id,
                    client_id,
                    token_hash,
                    project_id,
                    json.dumps(sorted(set(scopes))),
                    canonical_expiry,
                    utc_now(),
                ),
            )
        token = self.get_token(token_id) or {}
        return token, raw_token

    def get_token(self, token_id: str) -> dict | None:
        with self.database.connection() as db:
            row = db.execute("SELECT * FROM integration_tokens WHERE id=?", (token_id,)).fetchone()
        token = _decoded(row, "scopes")
        if token is not None and token.get("expires_at") is not None:
            parsed_expiry = self._timestamp(token["expires_at"])
            token["expires_at"] = (
                parsed_expiry.isoformat().replace("+00:00", "Z")
                if parsed_expiry is not None
                else None
            )
        return token

    def authenticate(self, raw_token: str) -> dict | None:
        token_hash = self.hash_token(raw_token)
        now = utc_now()
        with self.database.transaction() as db:
            row = db.execute(
                """SELECT t.*, c.name AS client_name, c.status AS client_status
                   FROM integration_tokens t
                   JOIN integration_clients c ON c.id=t.client_id
                   WHERE t.token_hash=? AND t.revoked_at IS NULL
                     AND c.status='active'""",
                (token_hash,),
            ).fetchone()
            if row and self._token_is_active(dict(row), now=now):
                db.execute(
                    "UPDATE integration_tokens SET last_used_at=? WHERE id=?",
                    (now, row["id"]),
                )
                db.execute(
                    "UPDATE integration_clients SET last_seen_at=?, updated_at=? WHERE id=?",
                    (now, now, row["client_id"]),
                )
            else:
                row = None
        return _decoded(row, "scopes")

    def revoke_token(self, token_id: str) -> bool:
        with self.database.transaction() as db:
            result = db.execute(
                "UPDATE integration_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (utc_now(), token_id),
            )
        return result.rowcount > 0

    def create_execution(self, record: dict[str, Any]) -> dict:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO codex_executions
                   (id, project_id, work_item_id, client_id, mode, status, sandbox,
                    workspace_path, base_ref, base_commit, prompt, context_snapshot_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["project_id"],
                    record["work_item_id"],
                    record.get("client_id"),
                    record["mode"],
                    record["status"],
                    record["sandbox"],
                    record["workspace_path"],
                    record.get("base_ref"),
                    record.get("base_commit"),
                    record.get("prompt", ""),
                    json.dumps(record.get("context_snapshot", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        self.append_event(record["id"], "created", "Codex 执行已创建", {"mode": record["mode"]})
        return self.get_execution(record["id"]) or {}

    def get_execution(self, execution_id: str) -> dict | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT e.*, w.title AS work_item_title, w.display_key AS work_item_key
                   FROM codex_executions e
                   JOIN research_work_items w ON w.id=e.work_item_id
                   WHERE e.id=?""",
                (execution_id,),
            ).fetchone()
        result = _decoded(row, "validation", "changed_files", "context_snapshot")
        if result:
            result["events"] = self.list_events(execution_id)
        return result

    def list_executions(
        self, project_id: str, *, status: str | None = None, limit: int = 200
    ) -> list[dict]:
        clauses = ["e.project_id=?"]
        values: list[Any] = [project_id]
        if status:
            clauses.append("e.status=?")
            values.append(status)
        values.append(limit)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT e.*, w.title AS work_item_title, w.display_key AS work_item_key
                    FROM codex_executions e
                    JOIN research_work_items w ON w.id=e.work_item_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY e.updated_at DESC LIMIT ?""",
                values,
            ).fetchall()
        return [
            _decoded(row, "validation", "changed_files", "context_snapshot") or {} for row in rows
        ]

    def update_execution(self, execution_id: str, changes: dict[str, Any]) -> dict | None:
        allowed = {
            "client_id",
            "thread_id",
            "status",
            "result_commit",
            "summary",
            "validation",
            "changed_files",
            "artifact_path",
            "error",
            "started_at",
            "completed_at",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            column = f"{key}_json" if key in {"validation", "changed_files"} else key
            assignments.append(f"{column}=?")
            values.append(
                json.dumps(value, ensure_ascii=False)
                if key in {"validation", "changed_files"}
                else value
            )
        if not assignments:
            return self.get_execution(execution_id)
        assignments.extend(["version=version+1", "updated_at=?"])
        values.extend([utc_now(), execution_id])
        with self.database.transaction() as db:
            db.execute(
                f"UPDATE codex_executions SET {', '.join(assignments)} WHERE id=?",
                values,
            )
        return self.get_execution(execution_id)

    def append_event(
        self,
        execution_id: str,
        event_type: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        artifact_path: str | None = None,
    ) -> dict:
        safe_event_type = (
            event_type if re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", event_type) else "unknown"
        )
        safe_summary = f"execution.{safe_event_type}"
        safe_payload = {
            key: value
            for key, value in (payload or {}).items()
            if key in {"mode", "status"}
            and isinstance(value, str)
            and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value)
        }
        safe_artifact_path = "[redacted]" if artifact_path else None
        with self.database.transaction() as db:
            sequence = db.execute(
                """SELECT coalesce(max(sequence), 0) + 1
                   FROM codex_execution_events WHERE execution_id=?""",
                (execution_id,),
            ).fetchone()[0]
            event_id = f"codex-event://{uuid4().hex}"
            db.execute(
                """INSERT INTO codex_execution_events
                   (id, execution_id, sequence, event_type, summary, payload_json,
                    artifact_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    execution_id,
                    sequence,
                    event_type,
                    safe_summary,
                    json.dumps(safe_payload, ensure_ascii=False),
                    safe_artifact_path,
                    utc_now(),
                ),
            )
            row = db.execute(
                "SELECT * FROM codex_execution_events WHERE id=?", (event_id,)
            ).fetchone()
        return _decoded(row, "payload") or {}

    def list_events(self, execution_id: str) -> list[dict]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM codex_execution_events WHERE execution_id=?
                   ORDER BY sequence""",
                (execution_id,),
            ).fetchall()
        return [_decoded(row, "payload") or {} for row in rows]

    def create_approval(self, record: dict[str, Any]) -> dict:
        approval_id = f"approval://{uuid4().hex}"
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO approval_requests
                   (id, project_id, execution_id, work_item_id, action, status,
                    requested_by, payload_json, note, created_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (
                    approval_id,
                    record["project_id"],
                    record.get("execution_id"),
                    record.get("work_item_id"),
                    record["action"],
                    record["requested_by"],
                    json.dumps(record.get("payload", {}), ensure_ascii=False),
                    record.get("note", ""),
                    utc_now(),
                ),
            )
        return self.get_approval(approval_id) or {}

    def get_approval(self, approval_id: str) -> dict | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT * FROM approval_requests WHERE id=?", (approval_id,)
            ).fetchone()
        return _decoded(row, "payload")

    def list_approvals(self, project_id: str, status: str | None = None) -> list[dict]:
        clauses = ["project_id=?"]
        values: list[Any] = [project_id]
        if status:
            clauses.append("status=?")
            values.append(status)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT * FROM approval_requests
                    WHERE {" AND ".join(clauses)}
                    ORDER BY created_at DESC""",
                values,
            ).fetchall()
        return [_decoded(row, "payload") or {} for row in rows]

    def decide_approval(
        self, approval_id: str, decision: str, decided_by: str, note: str
    ) -> dict | None:
        with self.database.transaction() as db:
            result = db.execute(
                """UPDATE approval_requests
                   SET status=?, decided_by=?, note=?, decided_at=?
                   WHERE id=? AND status='pending'""",
                (decision, decided_by, note, utc_now(), approval_id),
            )
        if result.rowcount == 0:
            return None
        return self.get_approval(approval_id)

    def idempotent_response(self, client_id: str, project_id: str, key: str) -> dict | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT response_json FROM integration_idempotency
                   WHERE client_id=? AND project_id=? AND idempotency_key=?""",
                (client_id, project_id, key),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def remember_response(
        self,
        client_id: str,
        project_id: str,
        key: str,
        tool_name: str,
        response: dict,
    ) -> None:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO integration_idempotency
                   (client_id, project_id, idempotency_key, tool_name, response_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(client_id, project_id, idempotency_key) DO NOTHING""",
                (
                    client_id,
                    project_id,
                    key,
                    tool_name,
                    json.dumps(response, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def codex_sync_projection(self, project_id: str) -> dict[str, Any]:
        """Project existing Codex persistence without discovering host files or paths."""

        with self.database.connection() as db:
            sources = db.execute(
                """SELECT s.id, s.status, s.active_generation_id, s.updated_at,
                          s.last_error, g.status AS active_generation_status
                   FROM codex_sources s
                   LEFT JOIN codex_generations g ON g.id=s.active_generation_id
                   WHERE s.project_id=? ORDER BY s.updated_at DESC""",
                (project_id,),
            ).fetchall()
            workflows = db.execute(
                """SELECT id, status, request_json, error, updated_at
                   FROM workflows
                   WHERE (id LIKE 'wf-codex-%' OR source LIKE 'codex:%')
                     AND json_valid(request_json)
                     AND json_type(request_json, '$.project_id') = 'text'
                     AND json_extract(request_json, '$.project_id') = ?
                   ORDER BY updated_at DESC LIMIT 100""",
                (project_id,),
            ).fetchall()
            indexed_sessions = db.execute(
                """SELECT count(*)
                   FROM codex_threads t
                   JOIN codex_sources s ON s.id=t.source_id
                   JOIN codex_generations g
                     ON g.id=s.active_generation_id AND g.source_id=s.id
                   WHERE t.project_id=? AND s.project_id=?
                     AND t.project_id=s.project_id AND t.acl_ref=s.acl_ref
                     AND t.generation_id=s.active_generation_id
                     AND s.status='ready' AND g.status='published'""",
                (project_id, project_id),
            ).fetchone()[0]
            building_generation = bool(
                db.execute(
                    """SELECT 1
                         FROM codex_generations g
                         JOIN codex_sources s ON s.id=g.source_id
                        WHERE s.project_id=? AND g.status='building'
                          AND g.id=s.active_generation_id
                        LIMIT 1""",
                    (project_id,),
                ).fetchone()
            )

        project_workflows: list[dict[str, Any]] = []
        for row in workflows:
            item = dict(row)
            try:
                request = json.loads(item.pop("request_json") or "{}")
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("codex workflow request is unavailable") from exc
            if request.get("project_id") == project_id:
                project_workflows.append(item)
        latest_workflow = project_workflows[0] if project_workflows else None
        active_workflow = next(
            (item for item in project_workflows if item.get("status") in {"queued", "running"}),
            None,
        )
        latest_source = dict(sources[0]) if sources else None
        source_failed = any(
            item["status"] == "failed"
            or item["active_generation_status"] == "failed"
            or bool(item["last_error"])
            for item in sources
        )
        workflow_failed = bool(
            latest_workflow
            and (latest_workflow.get("status") == "failed" or latest_workflow.get("error"))
        )
        source_syncing = building_generation or any(
            item["status"] == "indexing" for item in sources
        )
        if active_workflow or source_syncing:
            status = "syncing"
            reason = "active_sync"
        elif source_failed or workflow_failed:
            status = "sync_failed"
            reason = "last_sync_failed"
        elif indexed_sessions:
            status = "ready"
            reason = "indexed_sessions_available"
        else:
            status = "no_data"
            reason = "no_indexed_sessions" if sources else "source_not_configured"
        source_status = str(latest_source.get("status") if latest_source else "unconfigured")
        if source_status not in {"unconfigured", "indexing", "ready", "failed"}:
            source_status = "unknown"
        last_sync_at = (
            max(
                [
                    str(item["updated_at"] or "")
                    for item in [*(dict(row) for row in sources), *project_workflows]
                ],
                default="",
            )
            or None
        )
        return {
            "status": status,
            "reason": reason,
            "source_status": source_status,
            "indexed_sessions": int(indexed_sessions or 0),
            "source_count": len(sources),
            "active_workflow": bool(active_workflow),
            "last_sync_at": last_sync_at,
            "data_available": bool(indexed_sessions),
        }

    def list_agent_events(
        self,
        project_id: str,
        *,
        client_id: str | None = None,
        limit: int = 100,
        configured_authority_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Merge trusted bridge observations and lifecycle rows into a bounded audit view."""

        bounded_limit = max(1, min(500, limit))
        client_clause = " AND client_id=?" if client_id else ""
        client_values: list[Any] = [client_id] if client_id else []
        now = utc_now()
        with self.database.connection() as db:
            observed_rows = db.execute(
                f"""SELECT id, client_id, event_type, resource_type, resource_id,
                            detail_json, created_at, rowid AS storage_order
                     FROM integration_agent_events
                    WHERE project_id=?{client_clause}
                    ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                [project_id, *client_values, bounded_limit],
            ).fetchall()
            execution_rows = db.execute(
                f"""SELECT ev.id, e.client_id, ev.event_type, ev.execution_id,
                            ev.sequence, ev.created_at
                     FROM codex_execution_events ev
                     JOIN codex_executions e ON e.id=ev.execution_id
                    WHERE e.project_id=?{client_clause}
                    ORDER BY ev.created_at DESC, ev.sequence DESC LIMIT ?""",
                [project_id, *client_values, bounded_limit],
            ).fetchall()
            approval_client_clause = " AND e.client_id=?" if client_id else ""
            approval_rows = db.execute(
                f"""SELECT * FROM (
                       SELECT a.id || '#requested' AS event_id, e.client_id,
                              a.id AS approval_id, a.execution_id, a.action,
                              a.status AS approval_status,
                              'approval.requested' AS event_type,
                              a.created_at AS event_at, 0 AS lifecycle_order
                         FROM approval_requests a
                         LEFT JOIN codex_executions e ON e.id=a.execution_id
                        WHERE a.project_id=?{approval_client_clause}
                       UNION ALL
                       SELECT a.id || '#decided' AS event_id, e.client_id,
                              a.id AS approval_id, a.execution_id, a.action,
                              a.status AS approval_status,
                              'approval.decided' AS event_type,
                              a.decided_at AS event_at, 1 AS lifecycle_order
                         FROM approval_requests a
                         LEFT JOIN codex_executions e ON e.id=a.execution_id
                        WHERE a.project_id=?{approval_client_clause}
                          AND a.decided_at IS NOT NULL
                   ) ORDER BY event_at DESC, lifecycle_order DESC LIMIT ?""",
                [
                    project_id,
                    *client_values,
                    project_id,
                    *client_values,
                    bounded_limit,
                ],
            ).fetchall()
            candidate_authorities = db.execute(
                """SELECT t.id
                              , t.expires_at, t.revoked_at, c.status AS client_status
                         FROM integration_tokens t
                         JOIN integration_clients c ON c.id=t.client_id
                        WHERE t.project_id=? AND t.revoked_at IS NULL
                          AND c.status='active'""",
                (project_id,),
            ).fetchall()
            active_authority_ids = {
                row["id"]
                for row in candidate_authorities
                if self._token_is_active(dict(row), now=now)
            }
            snapshot_authorities = {
                row["id"]: row["authority_id"]
                for row in db.execute(
                    """SELECT id, authority_id
                         FROM integration_capability_snapshots
                        WHERE project_id=?""",
                    (project_id,),
                ).fetchall()
            }

        events: list[dict[str, Any]] = []
        for row in observed_rows:
            item = dict(row)
            try:
                detail = json.loads(item.pop("detail_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                detail = {}
            safe_detail = self._safe_observation(detail)
            if item["event_type"] in {"mcp.handshake", "capability.discovery"}:
                snapshot = {"observed_at": item["created_at"]}
                authority_id = snapshot_authorities.get(item["resource_id"], "")
                authority_active = authority_id in active_authority_ids or (
                    configured_authority_id is not None and authority_id == configured_authority_id
                )
                fresh = self.capability_snapshot_is_fresh(snapshot) and authority_active
                observed = self._timestamp(item["created_at"])
                safe_detail["authority_status"] = "current" if fresh else "historical_expired"
                safe_detail["expires_at"] = (
                    (observed + timedelta(seconds=CAPABILITY_SNAPSHOT_TTL_SECONDS)).isoformat()
                    if observed is not None
                    else None
                )
                if not fresh:
                    safe_detail.pop("capabilities", None)
            item["detail"] = safe_detail
            item["_sort_order"] = int(item.pop("storage_order"))
            events.append(item)
        events.extend(
            {
                "id": row["id"],
                "client_id": row["client_id"],
                "event_type": f"execution.{row['event_type']}",
                "resource_type": "execution",
                "resource_id": row["execution_id"],
                "detail": {"sequence": row["sequence"]},
                "created_at": row["created_at"],
                "_sort_order": int(row["sequence"]),
            }
            for row in execution_rows
        )
        events.extend(
            {
                "id": row["event_id"],
                "client_id": row["client_id"],
                "event_type": row["event_type"],
                "resource_type": "approval",
                "resource_id": row["approval_id"],
                "detail": {
                    "action": row["action"],
                    **(
                        {"status": row["approval_status"]}
                        if row["event_type"] == "approval.decided"
                        else {}
                    ),
                },
                "created_at": row["event_at"],
                "_sort_order": int(row["lifecycle_order"]),
            }
            for row in approval_rows
        )
        events.sort(
            key=lambda item: (
                str(item["created_at"]),
                int(item.get("_sort_order", 0)),
                str(item["id"]),
            ),
            reverse=True,
        )
        return [
            {key: value for key, value in item.items() if key != "_sort_order"}
            for item in events[:bounded_limit]
        ]

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
