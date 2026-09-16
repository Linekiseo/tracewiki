from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from .models import (
    CodexEdgeRecord,
    CodexItemRecord,
    CodexSearchScope,
    CodexSearchViewRecord,
    CodexThreadRecord,
    CodexTurnRecord,
)

_PATCH_ASSIGNMENT_RE = re.compile(
    r"(?:const|let|var)\s+\w+\s*=\s*(?P<value>\"(?:\\.|[^\"\\])*\")",
    re.DOTALL,
)
_PATCH_FILE_HEADER_RE = re.compile(
    r"^\*\*\* (?P<operation>Add|Update|Delete) File: (?P<path>.+?)\s*$"
)
_UNIFIED_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<section>.*)$"
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _decode_apply_patch(content: str) -> str | None:
    """Recover the exact apply_patch payload from old and current Codex records."""

    candidates = [content]
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, str):
        candidates.insert(0, parsed)
    elif isinstance(parsed, dict):
        candidates[:0] = [value for value in parsed.values() if isinstance(value, str)]

    for candidate in candidates:
        if candidate.lstrip().startswith("*** Begin Patch"):
            start = candidate.find("*** Begin Patch")
            end = candidate.find("*** End Patch", start)
            return candidate[start : end + len("*** End Patch")] if end >= 0 else candidate[start:]
        for match in _PATCH_ASSIGNMENT_RE.finditer(candidate):
            try:
                decoded = json.loads(match.group("value"))
            except json.JSONDecodeError:
                continue
            if not isinstance(decoded, str) or "*** Begin Patch" not in decoded:
                continue
            start = decoded.find("*** Begin Patch")
            end = decoded.find("*** End Patch", start)
            return decoded[start : end + len("*** End Patch")] if end >= 0 else decoded[start:]
    return None


def _find_lines(haystack: list[str], needle: list[str], start: int = 0) -> int | None:
    if not needle:
        return None
    final_start = len(haystack) - len(needle)
    for index in range(max(0, start), final_start + 1):
        if haystack[index : index + len(needle)] == needle:
            return index
    return None


def _normalize_patch_body(
    operation: str,
    body: list[str],
    *,
    after_content: str | None,
) -> tuple[str, int, int, int]:
    """Convert Codex apply_patch syntax into a line-addressable unified patch."""

    relevant = [
        line
        for line in body
        if line not in {"*** End of File", "*** End Patch"} and not line.startswith("*** Move to:")
    ]
    additions = sum(1 for line in relevant if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in relevant if line.startswith("-") and not line.startswith("---"))

    if operation == "Add":
        lines = [line for line in relevant if line.startswith("+")]
        return f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join(lines), additions, 0, 1
    if operation == "Delete":
        lines = [line if line.startswith("-") else f"-{line}" for line in relevant]
        return f"@@ -1,{len(lines)} +0,0 @@\n" + "\n".join(lines), 0, len(lines), 1

    chunks: list[tuple[str, list[str]]] = []
    header = ""
    lines: list[str] = []
    for line in relevant:
        if line.startswith("@@"):
            if header or lines:
                chunks.append((header, lines))
            header, lines = line, []
        else:
            lines.append(line)
    if header or lines:
        chunks.append((header, lines))
    if not chunks:
        return "", additions, deletions, 0

    after_lines = after_content.splitlines() if after_content is not None else []
    search_from = 0
    delta_before = 0
    rendered: list[str] = []
    for raw_header, hunk_lines in chunks:
        numeric = _UNIFIED_HUNK_RE.match(raw_header)
        if numeric:
            old_start = int(numeric.group("old_start"))
            old_count = int(numeric.group("old_count") or "1")
            new_start = int(numeric.group("new_start"))
            new_count = int(numeric.group("new_count") or "1")
            section = numeric.group("section").strip()
        else:
            new_side = [
                line[1:]
                for line in hunk_lines
                if line.startswith((" ", "+")) and not line.startswith("+++")
            ]
            match_index = _find_lines(after_lines, new_side, search_from)
            if match_index is None:
                match_index = _find_lines(after_lines, new_side)
            new_start = (match_index + 1) if match_index is not None else 0
            old_start = max(0, new_start - delta_before) if new_start else 0
            old_count = sum(
                1
                for line in hunk_lines
                if line.startswith((" ", "-")) and not line.startswith("---")
            )
            new_count = sum(
                1
                for line in hunk_lines
                if line.startswith((" ", "+")) and not line.startswith("+++")
            )
            section = raw_header[2:].strip() if raw_header.startswith("@@") else ""
            if match_index is not None:
                search_from = match_index + max(1, len(new_side))
        rendered.append(
            f"@@ -{old_start},{old_count} +{new_start},{new_count} @@"
            + (f" {section}" if section else "")
        )
        rendered.extend(hunk_lines)
        delta_before += sum(
            1 for line in hunk_lines if line.startswith("+") and not line.startswith("+++")
        ) - sum(1 for line in hunk_lines if line.startswith("-") and not line.startswith("---"))
    return "\n".join(rendered), additions, deletions, len(chunks)


def _split_apply_patch(
    patch: str,
    *,
    normalize_path: Any,
    after_content_by_path: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Return per-file patches without inventing content absent from the audit record."""

    result: dict[str, dict[str, Any]] = {}
    current_operation = ""
    current_path: str | None = None
    current_body: list[str] = []

    def flush() -> None:
        nonlocal current_body
        if not current_path:
            current_body = []
            return
        normalized, additions, deletions, hunk_count = _normalize_patch_body(
            current_operation,
            current_body,
            after_content=after_content_by_path.get(current_path),
        )
        if normalized:
            truncated = len(normalized) > 50_000
            result[current_path] = {
                "patch": normalized[:50_000],
                "additions": additions,
                "deletions": deletions,
                "hunk_count": hunk_count,
                "patch_truncated": truncated,
                "patch_format": "recovered",
            }
        current_body = []

    for line in patch.splitlines():
        match = _PATCH_FILE_HEADER_RE.match(line)
        if match:
            flush()
            current_operation = match.group("operation")
            current_path = normalize_path(match.group("path"))
            continue
        if line == "*** End Patch":
            break
        if current_path:
            current_body.append(line)
    flush()
    return result


def _patch_stats(patch: str) -> tuple[int, int, int]:
    additions = sum(
        1 for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1 for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")
    )
    hunks = sum(1 for line in patch.splitlines() if line.startswith("@@"))
    return additions, deletions, hunks


class CodexStoreMixin:
    def prepare_codex_generation(
        self,
        *,
        generation_id: str,
        source: dict[str, Any],
        adapter_version: str,
    ) -> None:
        now = utc_now()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO codex_sources
                   (id, project_id, source_path, project_path, acl_ref, status,
                    stats_json, created_at, updated_at, last_error)
                   VALUES (?, ?, ?, ?, ?, 'indexing', '{}', ?, ?, NULL)
                   ON CONFLICT(id) DO UPDATE SET
                     project_id=excluded.project_id,
                     source_path=excluded.source_path,
                     project_path=excluded.project_path,
                     acl_ref=excluded.acl_ref,
                     status='indexing',
                     updated_at=excluded.updated_at,
                     last_error=NULL""",
                (
                    source["id"],
                    source["project_id"],
                    source["source_path"],
                    source.get("project_path"),
                    source["acl_ref"],
                    now,
                    now,
                ),
            )
            db.execute(
                """INSERT INTO codex_generations
                   (id, source_id, status, adapter_version, started_at)
                   VALUES (?, ?, 'building', ?, ?)""",
                (generation_id, source["id"], adapter_version, now),
            )

    def publish_codex_generation(
        self,
        *,
        source_id: str,
        generation_id: str,
        threads: Sequence[CodexThreadRecord],
        turns: Sequence[CodexTurnRecord],
        items: Sequence[CodexItemRecord],
        edges: Sequence[CodexEdgeRecord],
        views: Sequence[CodexSearchViewRecord],
        counts: dict[str, int],
        validation: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self.transaction() as db:
            db.executemany(
                """INSERT INTO codex_threads
                   (id, thread_id, source_id, generation_id, project_id, title, cwd,
                    status, started_at, updated_at, source_file, source_hash, acl_ref,
                    metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.id,
                        item.thread_id,
                        item.source_id,
                        item.generation_id,
                        item.project_id,
                        item.title,
                        item.cwd,
                        item.status,
                        item.started_at,
                        item.updated_at,
                        item.source_file,
                        item.source_hash,
                        item.acl_ref,
                        json.dumps(item.metadata, ensure_ascii=False),
                    )
                    for item in threads
                ],
            )
            db.executemany(
                """INSERT INTO codex_turns
                   (id, turn_id, thread_id, source_id, generation_id, ordinal, status,
                    started_at, completed_at, goal, summary, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.id,
                        item.turn_id,
                        item.thread_id,
                        item.source_id,
                        item.generation_id,
                        item.ordinal,
                        item.status,
                        item.started_at,
                        item.completed_at,
                        item.goal,
                        item.summary,
                        json.dumps(item.metadata, ensure_ascii=False),
                    )
                    for item in turns
                ],
            )
            db.executemany(
                """INSERT INTO codex_items
                   (id, item_id, thread_id, turn_id, source_id, generation_id, sequence,
                    item_type, role, status, timestamp, name, content, source_locator,
                    acl_ref, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.id,
                        item.item_id,
                        item.thread_id,
                        item.turn_id,
                        item.source_id,
                        item.generation_id,
                        item.sequence,
                        item.item_type,
                        item.role,
                        item.status,
                        item.timestamp,
                        item.name,
                        item.content,
                        item.source_locator,
                        item.acl_ref,
                        json.dumps(item.metadata, ensure_ascii=False),
                    )
                    for item in items
                ],
            )
            db.executemany(
                """INSERT INTO codex_edges
                   (id, source_id, generation_id, source_entity_id, target_entity_id,
                    edge_type, derivation, confidence, evidence_locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.id,
                        item.source_id,
                        item.generation_id,
                        item.source_entity_id,
                        item.target_entity_id,
                        item.edge_type,
                        item.derivation,
                        item.confidence,
                        item.evidence_locator,
                    )
                    for item in edges
                ],
            )
            db.executemany(
                """INSERT INTO codex_search_views
                   (id, entity_id, thread_id, source_id, generation_id, project_id,
                    view_type, name, content, vector, embedding_model)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.id,
                        item.entity_id,
                        item.thread_id,
                        item.source_id,
                        item.generation_id,
                        item.project_id,
                        item.view_type,
                        item.name,
                        item.content,
                        item.vector,
                        item.embedding_model,
                    )
                    for item in views
                ],
            )
            db.execute(
                """UPDATE codex_generations
                   SET status='published', counts_json=?, validation_json=?, completed_at=?
                   WHERE id=?""",
                (
                    json.dumps(counts, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False),
                    now,
                    generation_id,
                ),
            )
            db.execute(
                """UPDATE codex_sources
                   SET active_generation_id=?, status='ready', stats_json=?, updated_at=?,
                       last_error=NULL
                   WHERE id=?""",
                (generation_id, json.dumps(counts, ensure_ascii=False), now, source_id),
            )

    def fail_codex_generation(
        self, generation_id: str | None, source_id: str | None, error: str
    ) -> None:
        now = utc_now()
        with self.transaction() as db:
            if generation_id:
                db.execute(
                    """UPDATE codex_generations
                       SET status='failed', error=?, completed_at=? WHERE id=?""",
                    (error, now, generation_id),
                )
            if source_id:
                db.execute(
                    """UPDATE codex_sources
                       SET status=CASE WHEN active_generation_id IS NULL
                                       THEN 'failed' ELSE 'ready' END,
                           last_error=?, updated_at=? WHERE id=?""",
                    (error, now, source_id),
                )

    def list_codex_sources(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                """SELECT s.*, g.completed_at AS indexed_at
                   FROM codex_sources s
                   LEFT JOIN codex_generations g ON g.id=s.active_generation_id
                   ORDER BY s.updated_at DESC"""
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["stats"] = json.loads(item.pop("stats_json"))
            results.append(item)
        return results

    def codex_stats(self) -> dict[str, int]:
        with self.connection() as db:
            row = db.execute(
                """SELECT
                     count(*) AS sources,
                     coalesce(sum(json_extract(stats_json, '$.sessions')), 0) AS sessions,
                     coalesce(sum(json_extract(stats_json, '$.turns')), 0) AS turns,
                     coalesce(sum(json_extract(stats_json, '$.items')), 0) AS items,
                     coalesce(sum(json_extract(stats_json, '$.commands')), 0) AS commands,
                     coalesce(sum(json_extract(stats_json, '$.file_changes')), 0) AS file_changes,
                     coalesce(sum(json_extract(stats_json, '$.episodes')), 0) AS episodes
                   FROM codex_sources WHERE status='ready'"""
            ).fetchone()
        return {key: int(value or 0) for key, value in dict(row).items()}

    def list_codex_threads(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["t.generation_id=s.active_generation_id"]
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
        values.extend([limit, offset])
        with self.connection() as db:
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
        return [self._codex_thread_row(row) for row in rows]

    def _codex_thread_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        return item

    def get_codex_thread(self, thread_identifier: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                """SELECT t.* FROM codex_threads t
                   JOIN codex_sources s ON s.id=t.source_id
                   WHERE (t.thread_id=? OR t.id=?)
                     AND t.generation_id=s.active_generation_id
                   ORDER BY coalesce(t.updated_at, t.started_at) DESC LIMIT 1""",
                (thread_identifier, thread_identifier),
            ).fetchone()
            if not row:
                return None
            turns = db.execute(
                """SELECT * FROM codex_turns
                   WHERE source_id=? AND generation_id=? AND thread_id=?
                   ORDER BY ordinal""",
                (row["source_id"], row["generation_id"], row["id"]),
            ).fetchall()
            items = db.execute(
                """SELECT * FROM codex_items
                   WHERE source_id=? AND generation_id=? AND thread_id=?
                   ORDER BY sequence""",
                (row["source_id"], row["generation_id"], row["id"]),
            ).fetchall()
            prefix = row["id"] + "%"
            edges = db.execute(
                """SELECT * FROM codex_edges
                   WHERE source_id=? AND generation_id=?
                     AND (source_entity_id LIKE ? OR target_entity_id LIKE ?)
                   LIMIT 2000""",
                (row["source_id"], row["generation_id"], prefix, prefix),
            ).fetchall()
        result = self._codex_thread_row(row)
        turn_items: dict[str, list[dict[str, Any]]] = {}
        for item_row in items:
            item = dict(item_row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            turn_items.setdefault(item["turn_id"], []).append(item)
        result["turns"] = []
        for turn_row in turns:
            turn = dict(turn_row)
            turn["metadata"] = json.loads(turn.pop("metadata_json"))
            turn["items"] = turn_items.get(turn["id"], [])
            result["turns"].append(turn)
        result["edges"] = [dict(edge) for edge in edges]
        return result

    def get_codex_thread_timeline(
        self,
        thread_identifier: str,
        *,
        detail_turn_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Return the research-relevant session story without the raw event stream.

        Codex rollout imports may contain repeated adapter representations of the
        same turn.  The workbench should describe the user's goal, implementation
        outcome and validation evidence once instead of rendering thousands of
        tool protocol records.
        """
        with self.connection() as db:
            row = db.execute(
                """SELECT t.* FROM codex_threads t
                   JOIN codex_sources s ON s.id=t.source_id
                   WHERE (t.thread_id=? OR t.id=?)
                     AND t.generation_id=s.active_generation_id
                   ORDER BY coalesce(t.updated_at, t.started_at) DESC LIMIT 1""",
                (thread_identifier, thread_identifier),
            ).fetchone()
            if not row:
                return None
            turns = db.execute(
                """SELECT * FROM codex_turns
                   WHERE source_id=? AND generation_id=? AND thread_id=?
                   ORDER BY ordinal, turn_key""",
                (row["source_id"], row["generation_id"], row["id"]),
            ).fetchall()
            items = db.execute(
                """SELECT id, turn_id, sequence, item_type, status, timestamp, name,
                          content, source_locator, metadata_json
                   FROM codex_items
                   WHERE source_id=? AND generation_id=? AND thread_id=?
                     AND item_type IN (
                       'FileChange', 'Patch', 'CommandExecution', 'ToolCall',
                       'ToolResult', 'ValidationResult'
                     )
                   ORDER BY sequence""",
                (row["source_id"], row["generation_id"], row["id"]),
            ).fetchall()

        cwd = str(row["cwd"] or "").rstrip("/")
        known_files = {
            "Dockerfile",
            "Makefile",
            "LICENSE",
            "README",
            "Procfile",
        }

        def presentable_path(value: Any) -> str | None:
            path = str(value or "").strip().replace("\\", "/")
            if (
                not path
                or len(path) > 260
                or any(token in path for token in ("\n", "/n", "@@", "{", "}", "://"))
                or path.endswith(("/", "."))
            ):
                return None
            if cwd and path.startswith(f"{cwd}/"):
                path = path[len(cwd) + 1 :]
            path = path.removeprefix("./")
            basename = path.rsplit("/", 1)[-1]
            if basename not in known_files and not re.search(
                r"\.[A-Za-z][A-Za-z0-9_-]{0,11}$", basename
            ):
                return None
            if not re.fullmatch(r"[A-Za-z0-9_@+.,/ -]+", path):
                return None
            return path

        file_snapshots: dict[tuple[str, str], list[tuple[int, str]]] = {}
        file_change_details_by_item: dict[str, dict[str, dict[str, Any]]] = {}
        if detail_turn_id:
            for item_row in items:
                if item_row["turn_id"] != detail_turn_id or item_row["item_type"] != "FileChange":
                    continue
                try:
                    snapshot = json.loads(item_row["content"] or "{}")
                except json.JSONDecodeError:
                    continue
                if not isinstance(snapshot, dict):
                    continue
                for raw_path, raw_value in snapshot.items():
                    path = presentable_path(raw_path)
                    if not path or not isinstance(raw_value, dict):
                        continue
                    content = raw_value.get("content")
                    if isinstance(content, str):
                        file_snapshots.setdefault((item_row["turn_id"], path), []).append(
                            (int(item_row["sequence"]), content)
                        )
                    unified_diff = raw_value.get("unified_diff")
                    if not isinstance(unified_diff, str) or not unified_diff.strip():
                        if raw_value.get("type") == "add" and isinstance(content, str):
                            added_lines = content.splitlines()
                            unified_diff = f"@@ -0,0 +1,{len(added_lines)} @@\n" + "\n".join(
                                f"+{line}" for line in added_lines
                            )
                        else:
                            continue
                    additions, deletions, hunk_count = _patch_stats(unified_diff)
                    truncated = len(unified_diff) > 50_000
                    file_change_details_by_item.setdefault(item_row["id"], {})[path] = {
                        "patch": unified_diff[:50_000],
                        "additions": additions,
                        "deletions": deletions,
                        "hunk_count": hunk_count,
                        "patch_truncated": truncated,
                        "patch_format": "unified",
                    }

        patch_details_by_item: dict[str, dict[str, dict[str, Any]]] = {}
        if detail_turn_id:
            for item_row in items:
                if item_row["turn_id"] != detail_turn_id or item_row["item_type"] != "Patch":
                    continue
                patch = _decode_apply_patch(item_row["content"] or "")
                if not patch:
                    continue
                after_content_by_path: dict[str, str] = {}
                item_sequence = int(item_row["sequence"])
                for (turn_id, path), snapshots in file_snapshots.items():
                    if turn_id != item_row["turn_id"]:
                        continue
                    next_snapshot = next(
                        (content for sequence, content in snapshots if sequence > item_sequence),
                        None,
                    )
                    if next_snapshot is not None:
                        after_content_by_path[path] = next_snapshot
                patch_details_by_item[item_row["id"]] = _split_apply_patch(
                    patch,
                    normalize_path=presentable_path,
                    after_content_by_path=after_content_by_path,
                )

        result_by_call: dict[tuple[str, str], tuple[sqlite3.Row, dict[str, Any]]] = {}
        for item_row in items:
            if item_row["item_type"] != "ToolResult":
                continue
            metadata = json.loads(item_row["metadata_json"] or "{}")
            call_id = str(metadata.get("call_id") or "")
            if call_id:
                result_by_call[(item_row["turn_id"], call_id)] = (item_row, metadata)

        def command_parts(value: str) -> tuple[str, str]:
            lines = value.strip().splitlines()
            if not lines:
                return "", ""
            if lines[0].startswith("$ "):
                return lines[0][2:].strip(), "\n".join(lines[1:]).strip()
            return lines[0].strip(), "\n".join(lines[1:]).strip()

        def operation_status(
            raw_status: Any,
            exit_code: Any,
            fallback: str = "completed",
        ) -> str:
            if isinstance(exit_code, int):
                return "passed" if exit_code == 0 else "failed"
            status = str(raw_status or "").strip().lower()
            return status or fallback

        by_turn: dict[str, dict[str, Any]] = {}
        for item_row in items:
            bucket = by_turn.setdefault(
                item_row["turn_id"],
                {
                    "files": [],
                    "file_seen": set(),
                    "file_changes": [],
                    "file_change_seen": set(),
                    "commands": set(),
                    "validations": set(),
                    "operations": [],
                },
            )
            content = (item_row["content"] or "").strip()
            metadata = json.loads(item_row["metadata_json"] or "{}")
            if item_row["item_type"] in {"FileChange", "Patch"}:
                candidates = metadata.get("paths") or []
                if not candidates:
                    candidate = metadata.get("path") or metadata.get("file_path")
                    candidates = [candidate] if candidate else []
                for candidate in candidates:
                    path = presentable_path(candidate)
                    if not path:
                        continue
                    if path not in bucket["file_seen"]:
                        bucket["file_seen"].add(path)
                        bucket["files"].append(path)
                    change_key = (path, item_row["id"])
                    if change_key not in bucket["file_change_seen"]:
                        bucket["file_change_seen"].add(change_key)
                        patch_detail = file_change_details_by_item.get(
                            item_row["id"],
                            {},
                        ).get(path) or patch_details_by_item.get(item_row["id"], {}).get(
                            path,
                            {},
                        )
                        bucket["file_changes"].append(
                            {
                                "id": item_row["id"],
                                "path": path,
                                "change_type": str(
                                    metadata.get("kind")
                                    or metadata.get("change_type")
                                    or ("patch" if item_row["item_type"] == "Patch" else "update")
                                ),
                                "status": (
                                    "completed"
                                    if metadata.get("success") is not False
                                    else "failed"
                                ),
                                "locator": item_row["source_locator"],
                                **patch_detail,
                            }
                        )
            elif item_row["item_type"] == "CommandExecution":
                command, inline_output = command_parts(content)
                normalized = " ".join(command.split())
                if not normalized or normalized in bucket["commands"]:
                    continue
                bucket["commands"].add(normalized)
                call_id = str(metadata.get("call_id") or "")
                linked_result = result_by_call.get((item_row["turn_id"], call_id))
                result_row, result_metadata = linked_result or (None, {})
                output = inline_output
                if not output and result_row is not None:
                    output = str(result_row["content"] or "").strip()
                exit_code = metadata.get("exit_code")
                if exit_code is None:
                    exit_code = result_metadata.get("exit_code")
                is_validation = bool(
                    re.search(
                        r"(^|\s)(pytest|vitest|jest|ruff|mypy|tsc|npm test|pnpm test|cargo test|go test)(\s|$)",
                        normalized,
                        flags=re.IGNORECASE,
                    )
                )
                bucket["operations"].append(
                    {
                        "id": item_row["id"],
                        "kind": "validation" if is_validation else "command",
                        "name": item_row["name"] or "命令执行",
                        "command": command,
                        "output": output[:2400],
                        "status": operation_status(item_row["status"], exit_code),
                        "exit_code": exit_code,
                        "timestamp": item_row["timestamp"],
                        "locator": item_row["source_locator"],
                    }
                )
                if is_validation:
                    bucket["validations"].add(normalized)
            elif item_row["item_type"] == "ValidationResult":
                command = str(metadata.get("command") or content or item_row["name"]).strip()
                normalized = " ".join(command.split())
                if normalized:
                    bucket["validations"].add(normalized)
                exit_code = metadata.get("exit_code")
                bucket["operations"].append(
                    {
                        "id": item_row["id"],
                        "kind": "validation",
                        "name": item_row["name"] or "验证结果",
                        "command": command,
                        "output": content[:2400],
                        "status": operation_status(
                            item_row["status"], exit_code, fallback="recorded"
                        ),
                        "exit_code": exit_code,
                        "timestamp": item_row["timestamp"],
                        "locator": item_row["source_locator"],
                    }
                )
            elif item_row["item_type"] == "ToolCall":
                call_id = str(metadata.get("call_id") or "")
                linked_result = result_by_call.get((item_row["turn_id"], call_id))
                result_row, result_metadata = linked_result or (None, {})
                exit_code = result_metadata.get("exit_code")
                bucket["operations"].append(
                    {
                        "id": item_row["id"],
                        "kind": "tool",
                        "name": item_row["name"] or metadata.get("tool_name") or "工具调用",
                        "command": content,
                        "output": (
                            str(result_row["content"] or "")[:2400]
                            if result_row is not None
                            else ""
                        ),
                        "status": operation_status(item_row["status"], exit_code),
                        "exit_code": exit_code,
                        "timestamp": item_row["timestamp"],
                        "locator": item_row["source_locator"],
                    }
                )

        result = self._codex_thread_row(row)
        compact_turns: list[dict[str, Any]] = []
        seen: set[tuple[int, str, str]] = set()
        for turn_row in turns:
            goal = (turn_row["goal"] or "").strip()
            summary = (turn_row["summary"] or "").strip()
            key = (int(turn_row["ordinal"]), goal, summary)
            if key in seen:
                continue
            seen.add(key)
            evidence = by_turn.get(turn_row["id"], {})
            compact_turns.append(
                {
                    "id": turn_row["id"],
                    "ordinal": int(turn_row["ordinal"]),
                    "status": turn_row["status"],
                    "started_at": turn_row["started_at"],
                    "completed_at": turn_row["completed_at"],
                    "goal": goal,
                    "summary": summary,
                    "files": list(evidence.get("files", []))[:24],
                    "file_changes": list(evidence.get("file_changes", []))[:48],
                    "operations": (
                        list(evidence.get("operations", []))[:80]
                        if detail_turn_id == turn_row["id"]
                        else []
                    ),
                    "command_count": len(evidence.get("commands", set())),
                    "validation_count": len(evidence.get("validations", set())),
                    "locator": turn_row["id"],
                }
            )
        result["turns"] = compact_turns
        result["turn_count"] = len(compact_turns)
        result["file_change_count"] = len(
            {path for item in compact_turns for path in item["files"]}
        )
        result["command_count"] = sum(item["command_count"] for item in compact_turns)
        result["validation_count"] = sum(item["validation_count"] for item in compact_turns)
        return result

    def get_codex_turn_audit(
        self,
        thread_identifier: str,
        turn_identifier: str,
    ) -> dict[str, Any] | None:
        timeline = self.get_codex_thread_timeline(
            thread_identifier,
            detail_turn_id=turn_identifier,
        )
        if timeline is None:
            return None
        for turn in timeline["turns"]:
            if turn["id"] == turn_identifier:
                return {
                    **turn,
                    "thread_id": timeline["thread_id"],
                    "project_id": timeline["project_id"],
                    "acl_ref": timeline["acl_ref"],
                }
        return None

    def _codex_scope_sql(
        self, scope: CodexSearchScope, alias: str = "cv"
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if scope.project_id:
            clauses.append(f"{alias}.project_id=?")
            values.append(scope.project_id)
        if scope.thread_ids:
            marks = ",".join("?" for _ in scope.thread_ids)
            clauses.append(f"ct.thread_id IN ({marks})")
            values.extend(scope.thread_ids)
        if scope.item_types:
            marks = ",".join("?" for _ in scope.item_types)
            clauses.append(f"ci.item_type IN ({marks})")
            values.extend(scope.item_types)
        if scope.statuses:
            marks = ",".join("?" for _ in scope.statuses)
            clauses.append(f"coalesce(ci.status, ct.status) IN ({marks})")
            values.extend(scope.statuses)
        if scope.date_from:
            clauses.append("ci.timestamp>=?")
            values.append(scope.date_from)
        if scope.date_to:
            clauses.append("ci.timestamp<=?")
            values.append(scope.date_to)
        if scope.enforce_acl:
            allowed = list(dict.fromkeys([*scope.allowed_acl_refs, "public"]))
            marks = ",".join("?" for _ in allowed)
            clauses.append(f"ci.acl_ref IN ({marks})")
            values.extend(allowed)
        return clauses, values

    def codex_lexical_search(
        self, fts_query: str, scope: CodexSearchScope, limit: int
    ) -> list[dict[str, Any]]:
        clauses, values = self._codex_scope_sql(scope)
        where = " AND ".join(["codex_search_views_fts MATCH ?", *clauses])
        sql = f"""SELECT cv.*, ci.item_type, ci.role, ci.status, ci.timestamp,
                          ci.source_locator, ci.acl_ref, ci.metadata_json,
                          ci.turn_id, ct.title AS thread_title,
                          ct.thread_id AS raw_thread_id, ct.cwd, ct.status AS thread_status,
                          bm25(codex_search_views_fts, 5.0, 1.0) AS lexical_rank
                   FROM codex_search_views_fts
                   JOIN codex_search_views cv ON cv.rowid=codex_search_views_fts.rowid
                   JOIN codex_sources cs ON cs.id=cv.source_id
                   JOIN codex_items ci ON ci.id=cv.entity_id
                     AND ci.source_id=cv.source_id AND ci.generation_id=cv.generation_id
                   JOIN codex_threads ct ON ct.id=cv.thread_id
                     AND ct.source_id=cv.source_id AND ct.generation_id=cv.generation_id
                   WHERE {where} AND cv.generation_id=cs.active_generation_id
                     AND NOT EXISTS (
                       SELECT 1 FROM blocked_entities b WHERE b.entity_id=ci.id
                     )
                   ORDER BY lexical_rank LIMIT ?"""
        with self.connection() as db:
            rows = db.execute(sql, [fts_query, *values, limit]).fetchall()
        return [self._codex_search_row(row) for row in rows]

    def codex_dense_candidates(
        self, scope: CodexSearchScope, limit: int | None = None
    ) -> list[dict[str, Any]]:
        clauses, values = self._codex_scope_sql(scope)
        where = " AND ".join(["cv.generation_id=cs.active_generation_id", *clauses])
        limit_sql = " LIMIT ?" if limit is not None else ""
        sql = f"""SELECT cv.*, ci.item_type, ci.role, ci.status, ci.timestamp,
                          ci.source_locator, ci.acl_ref, ci.metadata_json,
                          ci.turn_id, ct.title AS thread_title,
                          ct.thread_id AS raw_thread_id, ct.cwd, ct.status AS thread_status
                   FROM codex_search_views cv
                   JOIN codex_sources cs ON cs.id=cv.source_id
                   JOIN codex_items ci ON ci.id=cv.entity_id
                     AND ci.source_id=cv.source_id AND ci.generation_id=cv.generation_id
                   JOIN codex_threads ct ON ct.id=cv.thread_id
                     AND ct.source_id=cv.source_id AND ct.generation_id=cv.generation_id
                   WHERE {where}
                     AND NOT EXISTS (
                       SELECT 1 FROM blocked_entities b WHERE b.entity_id=ci.id
                     )
                   {limit_sql}"""
        params = [*values, limit] if limit is not None else values
        with self.connection() as db:
            rows = db.execute(sql, params).fetchall()
        return [self._codex_search_row(row) for row in rows]

    def _codex_search_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def codex_edges_for_entities(self, entity_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not entity_ids:
            return []
        marks = ",".join("?" for _ in entity_ids)
        with self.connection() as db:
            rows = db.execute(
                f"""SELECT e.* FROM codex_edges e
                    JOIN codex_sources s ON s.id=e.source_id
                    WHERE e.generation_id=s.active_generation_id
                      AND (e.source_entity_id IN ({marks})
                           OR e.target_entity_id IN ({marks}))
                    LIMIT 500""",
                [*entity_ids, *entity_ids],
            ).fetchall()
        return [dict(row) for row in rows]
