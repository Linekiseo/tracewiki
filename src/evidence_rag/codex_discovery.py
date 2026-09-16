from __future__ import annotations

import hashlib
import json
import os
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .codex_adapter import CodexSessionAdapter
from .codex_ingestion import CodexIngestionService
from .repository import RepositoryResolver, RepositorySnapshot
from .storage import SQLiteStore


def _belongs_to_project(candidate: str, project: Path) -> bool:
    path = Path(candidate).expanduser().resolve(strict=False)
    return path == project or path.is_relative_to(project)


def discover_codex_sessions(
    source: Path,
    *,
    include_archived: bool,
    max_sessions: int,
) -> tuple[list[Path], dict[str, Any]]:
    """Enumerate every eligible rollout before applying the project/session limit."""

    diagnostics: dict[str, Any] = {
        "scan_complete": True,
        "candidate_limit_applied": False,
        "requested_session_limit": max_sessions,
        "jsonl_entries": 0,
        "candidate_sessions": 0,
        "active_candidates": 0,
        "archived_candidates": 0,
        "unreadable_entries": 0,
        "unsafe_symlink_entries": 0,
        "traversal_errors": 0,
    }
    candidates: list[tuple[int, str, Path, str]] = []

    def reject(kind: str) -> None:
        diagnostics[kind] += 1
        diagnostics["scan_complete"] = False

    def add(path: Path, group: str) -> None:
        diagnostics["jsonl_entries"] += 1
        try:
            if path.is_symlink():
                reject("unsafe_symlink_entries")
                return
            stat = path.stat()
            if not path.is_file():
                return
            with path.open("rb"):
                pass
        except OSError:
            reject("unreadable_entries")
            return
        candidates.append((stat.st_mtime_ns, path.as_posix(), path, group))

    if source.is_file() or source.is_symlink():
        if source.suffix != ".jsonl":
            raise ValueError("Codex session source file must use .jsonl")
        add(source, "active")
    else:
        roots: list[tuple[Path, str]] = []
        sessions = source / "sessions"
        if sessions.is_dir():
            roots.append((sessions, "active"))
            archived = source / "archived_sessions"
            if include_archived and archived.is_dir():
                roots.append((archived, "archived"))
        else:
            roots.append((source, "active"))

        for root, group in roots:

            def onerror(_error: OSError) -> None:
                reject("traversal_errors")

            for directory, directory_names, file_names in os.walk(
                root, topdown=True, onerror=onerror, followlinks=False
            ):
                base = Path(directory)
                safe_directories: list[str] = []
                for name in directory_names:
                    child = base / name
                    if child.is_symlink():
                        reject("unsafe_symlink_entries")
                    else:
                        safe_directories.append(name)
                directory_names[:] = safe_directories
                for name in file_names:
                    if name.endswith(".jsonl"):
                        add(base / name, group)

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    paths = [item[2] for item in candidates]
    diagnostics["candidate_sessions"] = len(paths)
    diagnostics["active_candidates"] = sum(1 for item in candidates if item[3] == "active")
    diagnostics["archived_candidates"] = sum(1 for item in candidates if item[3] == "archived")
    return paths, diagnostics


def match_codex_session_project(path: Path, project_path: Path | None) -> tuple[bool, str]:
    if path.is_symlink():
        return False, "unreadable"
    if project_path is None:
        return True, "unscoped"
    project = project_path.expanduser().resolve(strict=False)
    saw_binding = False
    try:
        with path.open("rb") as handle:
            for raw in handle:
                # Rollout bodies are mostly messages and tool output.  Avoid a
                # full JSON decode unless the record can carry a workspace
                # binding.  ``\\u`` keeps escaped JSON keys on the safe path.
                if b'"cwd"' not in raw and b'"workspace_roots"' not in raw and b"\\u" not in raw:
                    continue
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload")
                payload = payload if isinstance(payload, dict) else row
                values: list[Any] = [payload.get("cwd")]
                roots = payload.get("workspace_roots")
                if isinstance(roots, list):
                    values.extend(roots)
                row_has_binding = False
                for candidate in values:
                    if not isinstance(candidate, str) or not candidate.strip():
                        continue
                    saw_binding = True
                    row_has_binding = True
                    if _belongs_to_project(candidate, project):
                        return True, "matched"
                row_type = str(row.get("type") or "").strip().casefold()
                if row_has_binding and row_type in {"session_meta", "thread.started"}:
                    return False, "different_project"
    except OSError:
        return False, "unreadable"
    return False, "different_project" if saw_binding else "unbound"


class CompleteCodexSessionAdapter(CodexSessionAdapter):
    """Compatibility adapter with complete, fail-closed discovery semantics."""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.fail_on_incomplete: ContextVar[bool] = ContextVar(
            "codex_fail_on_incomplete", default=False
        )
        self.last_discovery: dict[str, Any] = {}
        self.last_match_reasons: dict[str, str] = {}

    def discover(self, source: Path, *, include_archived: bool, max_sessions: int) -> list[Path]:
        paths, diagnostics = discover_codex_sessions(
            source,
            include_archived=include_archived,
            max_sessions=max_sessions,
        )
        self.last_discovery = diagnostics
        self.last_match_reasons = {}
        if self.fail_on_incomplete.get() and not diagnostics["scan_complete"]:
            raise ValueError(
                "refusing to publish a partial generation because Codex discovery was incomplete"
            )
        return paths

    def matches_project(self, path: Path, project_path: Path | None) -> bool:
        matched, reason = match_codex_session_project(path, project_path)
        self.last_match_reasons[path.as_posix()] = reason
        if self.fail_on_incomplete.get() and reason == "unreadable":
            raise ValueError(
                "refusing to publish a partial generation because a Codex session was unreadable"
            )
        return matched


def _thread_inventory(
    store: SQLiteStore,
    *,
    project_id: str,
    source_id: str | None,
) -> dict[str, Any]:
    clauses = [
        "t.generation_id=s.active_generation_id",
        "t.project_id=s.project_id",
        "t.acl_ref=s.acl_ref",
        "t.project_id=?",
    ]
    values: list[Any] = [project_id]
    if source_id:
        clauses.append("t.source_id=?")
        values.append(source_id)
    where = " AND ".join(clauses)
    with store.connection() as db:
        rows = db.execute(
            f"""SELECT t.source_file, t.metadata_json,
                       json_valid(t.metadata_json) AS metadata_valid
                FROM codex_threads t
                JOIN codex_sources s ON s.id=t.source_id
                WHERE {where}""",
            values,
        ).fetchall()
    indexed_files: set[str] = set()
    hidden_subagents = 0
    invalid_metadata = 0
    visible_sessions = 0
    indexed_sessions = 0
    for row in rows:
        if not row["metadata_valid"]:
            invalid_metadata += 1
            continue
        try:
            metadata = json.loads(row["metadata_json"])
        except (json.JSONDecodeError, TypeError):
            invalid_metadata += 1
            continue
        indexed_sessions += 1
        if row["source_file"]:
            indexed_files.add(str(row["source_file"]))
        source = metadata.get("source") if isinstance(metadata, dict) else None
        if isinstance(source, dict) and source.get("subagent"):
            hidden_subagents += 1
        else:
            visible_sessions += 1
    return {
        "indexed_files": indexed_files,
        "indexed_sessions": indexed_sessions,
        "hidden_subagent_sessions": hidden_subagents,
        "visible_sessions": visible_sessions,
        "invalid_metadata_sessions": invalid_metadata,
    }


def list_visible_codex_threads(
    store: SQLiteStore,
    *,
    project_id: str | None,
    status: str | None,
    query: str | None,
    include_subagents: bool,
    acl_enforced: bool,
    acl_refs: tuple[str, ...],
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Apply integrity, visibility, and ACL predicates before SQL pagination."""

    clauses = [
        "t.generation_id=s.active_generation_id",
        "t.project_id=s.project_id",
        "t.acl_ref=s.acl_ref",
        "json_valid(t.metadata_json)=1",
    ]
    values: list[Any] = []
    if project_id:
        clauses.append("t.project_id=?")
        values.append(project_id)
    if status:
        clauses.append("t.status=?")
        values.append(status)
    if query:
        clauses.append("(t.title LIKE ? OR t.cwd LIKE ? OR t.thread_id LIKE ?)")
        pattern = f"%{query}%"
        values.extend([pattern, pattern, pattern])
    if not include_subagents:
        clauses.append(
            "coalesce(json_extract(t.metadata_json, '$.source.subagent'), 0) IN (0, 'false')"
        )
    if acl_enforced:
        if acl_refs:
            placeholders = ",".join("?" for _ in acl_refs)
            clauses.append(f"(t.acl_ref='public' OR t.acl_ref IN ({placeholders}))")
            values.extend(acl_refs)
        else:
            clauses.append("t.acl_ref='public'")
    values.extend([limit, offset])
    with store.connection() as db:
        rows = db.execute(
            f"""SELECT t.*,
                       (SELECT count(*) FROM codex_turns tr
                        WHERE tr.thread_id=t.id AND tr.source_id=t.source_id
                          AND tr.generation_id=t.generation_id) AS turn_count,
                       (SELECT count(*) FROM codex_items i
                        WHERE i.thread_id=t.id AND i.source_id=t.source_id
                          AND i.generation_id=t.generation_id
                          AND i.item_type NOT IN ('DevelopmentEpisode')) AS item_count,
                       (SELECT count(*) FROM codex_items i
                        WHERE i.thread_id=t.id AND i.source_id=t.source_id
                          AND i.generation_id=t.generation_id
                          AND i.item_type IN ('FileChange', 'Patch')) AS file_change_count,
                       (SELECT count(*) FROM codex_items i
                        WHERE i.thread_id=t.id AND i.source_id=t.source_id
                          AND i.generation_id=t.generation_id
                          AND i.item_type='CommandExecution') AS command_count
                FROM codex_threads t
                JOIN codex_sources s ON s.id=t.source_id
                WHERE {" AND ".join(clauses)}
                ORDER BY coalesce(t.updated_at, t.started_at) DESC
                LIMIT ? OFFSET ?""",
            values,
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        results.append(item)
    return results


class CompleteCodexIngestionService(CodexIngestionService):
    adapter: CompleteCodexSessionAdapter

    def project_sync_status(
        self,
        project_id: str,
        *,
        project_path: str | None = None,
        include_archived: bool = False,
        max_sessions: int = 2_000,
    ) -> dict[str, Any]:
        sources = [
            item for item in self.store.list_codex_sources() if item["project_id"] == project_id
        ]
        configured = next(
            (
                item
                for item in sources
                if not project_path
                or not item.get("project_path")
                or Path(item["project_path"]).resolve(strict=False)
                == Path(project_path).expanduser().resolve(strict=False)
            ),
            sources[0] if sources else None,
        )
        source_path = self.adapter.resolve_source(
            configured.get("source_path") if configured else None
        )
        resolved_project = self._project_path(
            project_path or (configured.get("project_path") if configured else None)
        )
        candidates = self.adapter.discover(
            source_path,
            include_archived=include_archived,
            max_sessions=max_sessions,
        )
        match_counts = {
            "matched_project_sessions": 0,
            "excluded_other_project_sessions": 0,
            "excluded_unbound_sessions": 0,
            "excluded_unreadable_sessions": 0,
        }
        matched: list[Path] = []
        for path in candidates:
            is_match, reason = match_codex_session_project(path, resolved_project)
            if is_match:
                matched.append(path)
                match_counts["matched_project_sessions"] += 1
            elif reason == "different_project":
                match_counts["excluded_other_project_sessions"] += 1
            elif reason == "unreadable":
                match_counts["excluded_unreadable_sessions"] += 1
            else:
                match_counts["excluded_unbound_sessions"] += 1

        selected = matched[:max_sessions]
        indexable: list[Path] = []
        oversized = 0
        unreadable_selected = 0
        for path in selected:
            try:
                if path.stat().st_size > self.adapter.settings.max_codex_session_bytes:
                    oversized += 1
                    continue
            except OSError:
                unreadable_selected += 1
                continue
            indexable.append(path)

        source_root = source_path if source_path.is_dir() else source_path.parent

        def relative_path(path: Path) -> str:
            try:
                return path.relative_to(source_root).as_posix()
            except ValueError:
                return path.name

        inventory = _thread_inventory(
            self.store,
            project_id=project_id,
            source_id=configured.get("id") if configured else None,
        )
        indexed_files = inventory.pop("indexed_files")
        available_files = {relative_path(path) for path in indexable}
        new_files = available_files - indexed_files
        removed_files = indexed_files - available_files
        indexed_at = configured.get("indexed_at") if configured else None
        try:
            indexed_timestamp = (
                datetime.fromisoformat(str(indexed_at).replace("Z", "+00:00"))
                .astimezone(UTC)
                .timestamp()
                if indexed_at
                else 0.0
            )
        except ValueError:
            indexed_timestamp = 0.0
        changed_files: set[str] = set()
        live_files: set[str] = set()
        now_timestamp = datetime.now(UTC).timestamp()
        for path in indexable:
            relative = relative_path(path)
            if relative not in indexed_files:
                continue
            try:
                modified_at = path.stat().st_mtime
            except OSError:
                continue
            if modified_at <= indexed_timestamp + 0.5:
                continue
            if now_timestamp - modified_at < 15:
                live_files.add(relative)
            else:
                changed_files.add(relative)
        active_workflow = next(
            (
                item
                for item in self.store.list_workflows(100, kind="codex")
                if item["status"] in {"queued", "running"}
                and item["request"].get("project_id") == project_id
            ),
            None,
        )
        discovery_complete = bool(self.adapter.last_discovery.get("scan_complete")) and not (
            match_counts["excluded_unreadable_sessions"] or unreadable_selected
        )
        diagnostics = {
            **self.adapter.last_discovery,
            **match_counts,
            "excluded_oversized_sessions": oversized,
            "selected_session_limit": max_sessions,
            "sessions_beyond_selection_limit": max(0, len(matched) - len(selected)),
            "project_match_complete": not bool(
                match_counts["excluded_unreadable_sessions"] or unreadable_selected
            ),
            "complete": discovery_complete,
            "visibility_state": "complete" if discovery_complete else "partial_fail_closed",
        }
        repositories = self.store.list_repositories()
        project_repositories = [
            item for item in repositories if item.get("project_id") == project_id
        ]
        repository_visibility = {
            "complete": True,
            "project_repositories": len(project_repositories),
            "ready_repositories": sum(
                1 for item in project_repositories if item.get("status") == "ready"
            ),
            "physical_repositories": len(
                {str(item["id"]).split("#project=", 1)[0] for item in project_repositories}
            ),
            "legacy_primary_bindings": sum(
                1 for item in project_repositories if "#project=" not in str(item["id"])
            ),
            "project_scoped_bindings": sum(
                1 for item in project_repositories if "#project=" in str(item["id"])
            ),
        }
        needs_sync = bool(new_files or changed_files or removed_files or not configured)
        if not discovery_complete:
            needs_sync = False
            removed_files = set()
        return {
            "project_id": project_id,
            "source_id": configured.get("id") if configured else None,
            "source_path": str(source_path),
            "project_path": str(resolved_project) if resolved_project else None,
            "source_status": configured.get("status") if configured else "unconfigured",
            "last_indexed_at": indexed_at,
            "discovered_sessions": len(selected),
            "indexable_sessions": len(indexable),
            **inventory,
            "new_sessions": len(new_files),
            "changed_sessions": len(changed_files),
            "live_sessions": len(live_files),
            "removed_sessions": len(removed_files),
            "oversized_sessions": oversized,
            "needs_sync": needs_sync,
            "sync_blocked_reason": None if discovery_complete else "discovery_incomplete",
            "active_workflow_id": active_workflow["id"] if active_workflow else None,
            "discovery": diagnostics,
            "repository_visibility": repository_visibility,
        }

    def run(self, workflow_id, request) -> None:
        token = self.adapter.fail_on_incomplete.set(True)
        try:
            super().run(workflow_id, request)
        finally:
            self.adapter.fail_on_incomplete.reset(token)


def repository_binding_id(store: SQLiteStore, physical_id: str, project_id: str) -> str:
    """Preserve a legacy first binding and derive stable IDs for later projects."""

    scoped_prefix = f"{physical_id}#project="
    with store.connection() as db:
        rows = db.execute(
            """SELECT id, project_id FROM repositories
               WHERE id=? OR substr(id, 1, ?)=?
               ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, id""",
            (physical_id, len(scoped_prefix), scoped_prefix, physical_id),
        ).fetchall()
    same_project = next((str(row["id"]) for row in rows if row["project_id"] == project_id), None)
    if same_project:
        return same_project
    if not rows:
        return physical_id
    suffix = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:16]
    candidate = f"{scoped_prefix}{suffix}"
    collision = next((row for row in rows if row["id"] == candidate), None)
    if collision and collision["project_id"] != project_id:
        raise ValueError("repository project binding collision")
    return candidate


class ProjectBindingRepositoryResolver:
    def __init__(self, resolver: RepositoryResolver, store: SQLiteStore) -> None:
        self._resolver = resolver
        self._store = store

    def resolve(self, *args, **kwargs) -> RepositorySnapshot:
        snapshot = self._resolver.resolve(*args, **kwargs)
        snapshot.id = repository_binding_id(self._store, snapshot.id, snapshot.project_id)
        return snapshot

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolver, name)


def public_repository_binding(item: dict[str, Any]) -> dict[str, Any]:
    repository_id = str(item["id"])
    return {
        **item,
        "physical_repository_id": repository_id.split("#project=", 1)[0],
        "binding_scope": "project_scoped" if "#project=" in repository_id else "legacy_primary",
    }
