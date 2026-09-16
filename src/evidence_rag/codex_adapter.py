from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import Settings
from .models import (
    CodexEdgeRecord,
    CodexItemRecord,
    CodexThreadRecord,
    CodexTurnRecord,
)
from .security import redact_secrets

CONTEXT_BLOCK_RE = re.compile(
    r"\n?<(?P<tag>in-app-browser-context|environment_context|recommended_plugins|"
    r"app-context|permissions(?:\s+instructions)?|collaboration_mode|apps_instructions|"
    r"plugins_instructions|skills_instructions|multi_agent_mode|user_context)\b[^>]*>"
    r".*?</(?P=tag)>\n?",
    re.DOTALL | re.IGNORECASE,
)
REQUEST_WRAPPER_RE = re.compile(
    r"(?im)^\s*#{1,6}\s+My request for (?:Codex|ChatGPT)\s*:?\s*$\n?"
)
ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
INTERNAL_CITATION_RE = re.compile(
    r"(?:cite|filecite)[^]+(?:\s*\[wordlim:\s*\d+\])?",
    re.IGNORECASE,
)
TRANSPORT_HEADER_RE = re.compile(
    r"\A(?:"
    r"Script completed|Script running with cell ID[^\n]*|"
    r"Chunk ID:[^\n]*|Original token count:[^\n]*|Exit code:[^\n]*"
    r")\r?\n"
    r"(?:(?:Wall time|Process exited with code|Process running with session ID|"
    r"Final output|Output|Original token count|Warning: truncated output|"
    r"Total output lines)[^\n]*\r?\n)*",
    re.IGNORECASE,
)
EXEC_COMMAND_RE = re.compile(
    r"(?:[\"']?cmd[\"']?)\s*:\s*(?P<value>\"(?:\\.|[^\"\\])*\")",
    re.DOTALL,
)
PATCH_LITERAL_RE = re.compile(
    r"(?:const|let|var)\s+\w+\s*=\s*(?P<value>\"(?:\\.|[^\"\\])*\")",
    re.DOTALL,
)
PATH_RE = re.compile(
    r"(?<![\w.-])(?:[A-Za-z]:[\\/]|/)?(?:[\w.@+-]+[\\/])+[\w.@+,-]+(?:\.[A-Za-z0-9]+)?"
)
REASONING_TYPES = {"reasoning", "agent_reasoning", "token_count"}
IGNORED_ROLES = {"system", "developer"}
VALIDATION_COMMAND_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:"
    r"(?:uv\s+run\s+)?pytest(?:\s|$)|"
    r"(?:python\s+-m\s+)?unittest(?:\s|$)|"
    r"(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|lint|typecheck)(?:\s|$)|"
    r"go\s+test(?:\s|$)|cargo\s+test(?:\s|$)|"
    r"(?:mvn|gradle|\.\/gradlew)\s+(?:test|check)(?:\s|$)|"
    r"(?:uv\s+run\s+)?ruff\s+(?:check|format)(?:\s|$)|"
    r"(?:uv\s+run\s+)?(?:mypy|pyright|eslint|tsc)(?:\s|$)|"
    r"make\s+(?:test|check|lint)(?:\s|$)|"
    r"xcodebuild\b.*\btest\b"
    r")",
    re.IGNORECASE,
)


def stable_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def codex_edge_id(edge_type: str, source_id: str, target_id: str) -> str:
    return f"codex-edge://sha256:{stable_hash(edge_type, source_id, target_id)}"


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"value": value}
        except json.JSONDecodeError:
            return {"value": value}
    return {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                part_type = item.get("type")
                if part_type in {"input_text", "output_text", "text"}:
                    parts.append(str(item.get("text", "")))
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        normalized = "\n".join(part for part in parts if part)
        return normalized or json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (dict, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _preferred_text(value: Any, keys: tuple[str, ...]) -> str:
    mapping = _as_mapping(value)
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return _text(mapping[key])
    return _text(value)


def _decoded_json_string(value: str) -> str | None:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, str) else None


def _tool_call_text(tool_name: str, raw_arguments: Any, raw_content: str) -> str:
    arguments = _as_mapping(raw_arguments)
    if tool_name == "write_stdin":
        chars = arguments.get("chars")
        if not isinstance(chars, str) or not chars:
            return "等待后台命令继续输出"
        if "\x03" in chars:
            return "中止后台命令"
        if "\x04" in chars:
            return "结束后台命令输入"
        if chars in {"\n", "\r", "\r\n"}:
            return "向后台命令发送回车"
        return "向后台命令发送输入"
    if tool_name == "wait":
        return "等待后台任务完成"
    if tool_name == "exec":
        commands = [
            decoded
            for match in EXEC_COMMAND_RE.finditer(raw_content)
            if (decoded := _decoded_json_string(match.group("value")))
        ]
        if commands:
            return "\n".join(commands)
        if "apply_patch" in raw_content:
            for match in PATCH_LITERAL_RE.finditer(raw_content):
                decoded = _decoded_json_string(match.group("value"))
                if decoded and decoded.lstrip().startswith("*** Begin Patch"):
                    return decoded
    if tool_name == "update_plan":
        plan = arguments.get("plan")
        if isinstance(plan, list):
            explanation = arguments.get("explanation")
            lines = [explanation] if isinstance(explanation, str) and explanation.strip() else []
            lines.extend(
                f"{item.get('status', 'pending')} · {item.get('step', '')}".strip()
                for item in plan
                if isinstance(item, dict) and item.get("step")
            )
            if lines:
                return "\n".join(lines)
    if tool_name == "request_user_input":
        questions = arguments.get("questions")
        if isinstance(questions, list):
            labels = [
                str(question.get("question", "")).strip()
                for question in questions
                if isinstance(question, dict) and question.get("question")
            ]
            if labels:
                return "\n".join(labels)
    return raw_content


def _unwrap_tool_result_lines(content: str) -> str:
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return content
    outputs: list[str] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return content
        if not isinstance(item, dict) or not isinstance(item.get("output"), str):
            return content
        outputs.append(TRANSPORT_HEADER_RE.sub("", item["output"]).strip())
    return "\n".join(output for output in outputs if output)


def _clean_codex_text(
    content: str,
    *,
    message: bool = False,
    tool_result: bool = False,
) -> str:
    content = unicodedata.normalize("NFC", content.replace("\r\n", "\n").replace("\r", "\n"))
    if tool_result:
        content = _unwrap_tool_result_lines(content)
    content = ANSI_ESCAPE_RE.sub("", content)
    content = ZERO_WIDTH_RE.sub("", content)
    content = CONTROL_CHAR_RE.sub("", content)
    content = CONTEXT_BLOCK_RE.sub("\n", content)
    content = INTERNAL_CITATION_RE.sub("", content)
    content = TRANSPORT_HEADER_RE.sub("", content)
    if message:
        content = REQUEST_WRAPPER_RE.sub("", content)
    lines = [line.rstrip() for line in content.splitlines()]
    content = "\n".join(lines)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()


def _safe_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return str(Path(value).expanduser().resolve(strict=False))
    except (OSError, RuntimeError):
        return value.strip()


def _belongs_to_project(candidate: str, project: Path) -> bool:
    try:
        path = Path(candidate).expanduser().resolve(strict=False)
        return path == project or path.is_relative_to(project)
    except (OSError, RuntimeError, ValueError):
        return False


@dataclass(slots=True)
class _Item:
    raw_id: str
    sequence: int
    item_type: str
    role: str | None
    status: str | None
    timestamp: str | None
    name: str
    content: str
    line: int
    call_id: str | None = None
    paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _Turn:
    raw_id: str
    ordinal: int
    status: str = "in_progress"
    started_at: str | None = None
    completed_at: str | None = None
    cwd: str | None = None
    model: str | None = None
    items: list[_Item] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedCodexSession:
    thread: CodexThreadRecord
    turns: list[CodexTurnRecord]
    items: list[CodexItemRecord]
    edges: list[CodexEdgeRecord]
    counters: dict[str, int]


class CodexSessionAdapter:
    """Normalize public Codex JSONL events and local rollout JSONL into one model."""

    adapter_version = "codex-jsonl-v2-clean-text"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve_source(self, value: str | None) -> Path:
        default = self.settings.codex_home or Path.home() / ".codex"
        source = Path(value).expanduser() if value else default
        source = source.resolve(strict=False)
        allowed = [root.resolve(strict=False) for root in self.settings.allowed_local_roots]
        if self.settings.codex_home:
            allowed.append(self.settings.codex_home.resolve(strict=False))
        if not any(source == root or source.is_relative_to(root) for root in allowed):
            raise ValueError("Codex session source is outside configured local roots")
        if not source.exists():
            raise ValueError(f"Codex session source does not exist: {source}")
        return source

    def discover(self, source: Path, *, include_archived: bool, max_sessions: int) -> list[Path]:
        if source.is_file():
            if source.suffix != ".jsonl":
                raise ValueError("Codex session source file must use .jsonl")
            return [source]

        roots: list[Path] = []
        sessions = source / "sessions"
        if sessions.is_dir():
            roots.append(sessions)
            archived = source / "archived_sessions"
            if include_archived and archived.is_dir():
                roots.append(archived)
        else:
            roots.append(source)

        candidates = [path for root in roots for path in root.rglob("*.jsonl") if path.is_file()]
        candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        # Project filtering happens from the small metadata prefix before full parsing.
        # Keep enough candidates so a quiet project is not hidden by newer unrelated work.
        return candidates[: max(20, max_sessions * 10)]

    def matches_project(self, path: Path, project_path: Path | None) -> bool:
        if project_path is None:
            return True
        project = project_path.expanduser().resolve(strict=False)
        inspected_bytes = 0
        try:
            with path.open("rb") as handle:
                for _, raw in zip(range(160), handle, strict=False):
                    inspected_bytes += len(raw)
                    if inspected_bytes > 2_000_000:
                        break
                    try:
                        row = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    payload = row.get("payload")
                    payload = payload if isinstance(payload, dict) else row
                    candidates = [payload.get("cwd")]
                    candidates.extend(payload.get("workspace_roots", []) or [])
                    if any(
                        isinstance(candidate, str) and _belongs_to_project(candidate, project)
                        for candidate in candidates
                    ):
                        return True
        except OSError:
            return False
        return False

    def read_titles(self, source: Path) -> dict[str, str]:
        current = source if source.is_dir() else source.parent
        candidates = [current / "session_index.jsonl", current.parent / "session_index.jsonl"]
        index_path = next((path for path in candidates if path.is_file()), None)
        if not index_path:
            return {}
        titles: dict[str, str] = {}
        with index_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                thread_id = row.get("id")
                title = row.get("thread_name")
                if isinstance(thread_id, str) and isinstance(title, str):
                    titles[thread_id] = title.strip()
        return titles

    def parse(
        self,
        path: Path,
        *,
        source_root: Path,
        source_id: str,
        generation_id: str,
        project_id: str,
        project_path: Path | None,
        acl_ref: str,
        titles: dict[str, str],
    ) -> ParsedCodexSession | None:
        size = path.stat().st_size
        if size > self.settings.max_codex_session_bytes:
            raise ValueError(f"session exceeds byte limit: {path.name}")

        rows: list[tuple[int, dict[str, Any]]] = []
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, start=1):
                digest.update(raw)
                try:
                    row = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(row, dict):
                    rows.append((line_number, row))

        thread_id = self._thread_id(rows, path)
        thread_uri = f"codex://thread/{quote(thread_id, safe='-._')}"
        turns: list[_Turn] = []
        turns_by_id: dict[str, _Turn] = {}
        current: _Turn | None = None
        session_meta: dict[str, Any] = {}
        session_format = "public-jsonl"
        candidate_paths: set[str] = set()
        started_at: str | None = None
        updated_at: str | None = None
        excluded_reasoning = 0
        redacted_count = 0
        sequence = 0
        seen_items: set[tuple[str, str, str]] = set()

        def ensure_turn(raw_id: str | None, timestamp: str | None = None) -> _Turn:
            nonlocal current
            normalized = raw_id or f"implicit-{len(turns) + 1}"
            existing = turns_by_id.get(normalized)
            if existing:
                current = existing
                return existing
            turn = _Turn(raw_id=normalized, ordinal=len(turns) + 1, started_at=timestamp)
            turns.append(turn)
            turns_by_id[normalized] = turn
            current = turn
            return turn

        for line_number, row in rows:
            event_type = str(row.get("type", ""))
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
            payload_type = str(payload.get("type", ""))
            timestamp = self._timestamp(row, payload)
            started_at = started_at or timestamp
            updated_at = timestamp or updated_at

            if event_type == "session_meta":
                session_meta = payload
                session_format = "rollout"
                cwd = _safe_path(payload.get("cwd"))
                if cwd:
                    candidate_paths.add(cwd)
                continue
            if event_type == "turn_context":
                turn = ensure_turn(str(payload.get("turn_id") or "") or None, timestamp)
                turn.cwd = _safe_path(payload.get("cwd"))
                turn.model = str(payload.get("model") or "") or None
                if turn.cwd:
                    candidate_paths.add(turn.cwd)
                for root in payload.get("workspace_roots", []) or []:
                    candidate = _safe_path(root)
                    if candidate:
                        candidate_paths.add(candidate)
                continue

            if event_type == "thread.started":
                cwd = _safe_path(payload.get("cwd"))
                if cwd:
                    session_meta["cwd"] = cwd
                    candidate_paths.add(cwd)
                roots = [
                    root
                    for root in payload.get("workspace_roots", []) or []
                    if isinstance(root, str)
                ]
                if roots:
                    session_meta["workspace_roots"] = roots
                    candidate_paths.update(
                        candidate for root in roots if (candidate := _safe_path(root))
                    )
                continue
            if event_type == "turn.started" or payload_type == "task_started":
                raw_turn = row.get("turn_id") or payload.get("turn_id")
                turn = ensure_turn(str(raw_turn or "") or None, timestamp)
                turn.started_at = timestamp or turn.started_at
                continue
            if event_type in {"turn.completed", "turn.failed"} or payload_type in {
                "task_complete",
                "turn_aborted",
            }:
                raw_turn = row.get("turn_id") or payload.get("turn_id")
                turn = ensure_turn(
                    str(raw_turn or "") or (current.raw_id if current else None), timestamp
                )
                failed = event_type == "turn.failed" or payload_type == "turn_aborted"
                turn.status = "failed" if failed else "completed"
                turn.completed_at = timestamp
                if payload.get("duration_ms") is not None:
                    turn.metadata["duration_ms"] = payload["duration_ms"]
                continue

            item_payload: dict[str, Any] | None = None
            item_status: str | None = None
            if event_type in {"item.started", "item.updated", "item.completed"}:
                value = row.get("item")
                item_payload = value if isinstance(value, dict) else None
                item_status = event_type.rsplit(".", 1)[-1]
            elif event_type in {"response_item", "event_msg"}:
                item_payload = payload

            if not item_payload:
                continue
            normalized, excluded = self._normalize_item(
                item_payload,
                line_number=line_number,
                sequence=sequence + 1,
                timestamp=timestamp,
                event_status=item_status,
            )
            if excluded:
                excluded_reasoning += 1
            if not normalized:
                continue

            turn = current
            if turn is None or (turn.status != "in_progress" and normalized.role == "user"):
                turn = ensure_turn(None, timestamp)
            signature = (
                turn.raw_id,
                normalized.item_type,
                stable_hash(normalized.role or "", normalized.content, normalized.call_id or ""),
            )
            if signature in seen_items:
                continue
            seen_items.add(signature)
            sequence += 1
            normalized.sequence = sequence
            if normalized.metadata.get("redacted"):
                redacted_count += 1
            turn.items.append(normalized)

        if project_path:
            project = project_path.expanduser().resolve(strict=False)
            if not candidate_paths or not any(
                _belongs_to_project(candidate, project) for candidate in candidate_paths
            ):
                return None

        if not turns:
            return None

        for turn in turns:
            turn.items.extend(self._validation_items(turn))

        source_hash = f"sha256:{digest.hexdigest()}"
        title = titles.get(thread_id) or self._fallback_title(turns) or f"Codex {thread_id[:8]}"
        cwd = _safe_path(session_meta.get("cwd")) or next(
            (turn.cwd for turn in turns if turn.cwd), None
        )
        last_status = turns[-1].status
        thread_status = (
            "failed"
            if last_status == "failed"
            else ("completed" if last_status == "completed" else "in_progress")
        )
        try:
            source_file = path.relative_to(source_root).as_posix()
        except ValueError:
            source_file = path.name

        thread_record = CodexThreadRecord(
            id=thread_uri,
            thread_id=thread_id,
            source_id=source_id,
            generation_id=generation_id,
            project_id=project_id,
            title=title[:240],
            cwd=cwd,
            status=thread_status,
            started_at=started_at,
            updated_at=updated_at,
            source_file=source_file,
            source_hash=source_hash,
            acl_ref=acl_ref,
            metadata={
                "format": session_format,
                "adapter_version": self.adapter_version,
                "cleaning_version": "codex-clean-text-v3",
                "originator": session_meta.get("originator"),
                "cli_version": session_meta.get("cli_version"),
                "source": session_meta.get("source"),
                "model_provider": session_meta.get("model_provider"),
            },
        )

        turn_records: list[CodexTurnRecord] = []
        item_records: list[CodexItemRecord] = []
        edges: list[CodexEdgeRecord] = []
        call_entities: dict[str, str] = {}
        item_counts: dict[str, int] = {}

        for turn in turns:
            turn_uri = f"{thread_uri}/turn/{quote(turn.raw_id, safe='-._')}"
            user_items = [item for item in turn.items if item.role == "user"]
            assistant_items = [item for item in turn.items if item.role == "assistant"]
            goal = user_items[0].content if user_items else ""
            summary = assistant_items[-1].content if assistant_items else ""
            turn_records.append(
                CodexTurnRecord(
                    id=turn_uri,
                    turn_id=turn.raw_id,
                    thread_id=thread_uri,
                    source_id=source_id,
                    generation_id=generation_id,
                    ordinal=turn.ordinal,
                    status=turn.status,
                    started_at=turn.started_at,
                    completed_at=turn.completed_at,
                    goal=goal[:4_000],
                    summary=summary[:4_000],
                    metadata={"cwd": turn.cwd, "model": turn.model, **turn.metadata},
                )
            )
            edges.append(
                CodexEdgeRecord(
                    id=codex_edge_id("HAS_TURN", thread_uri, turn_uri),
                    source_id=source_id,
                    generation_id=generation_id,
                    source_entity_id=thread_uri,
                    target_entity_id=turn_uri,
                    edge_type="HAS_TURN",
                    evidence_locator=turn_uri,
                )
            )

            raw_entities: dict[str, str] = {}
            for item in turn.items:
                item_key = f"{item.item_type}:{item.raw_id}"
                item_uri = f"{turn_uri}/item/{quote(item_key, safe='-._')}"
                locator = f"{item_uri}#event={item.line}"
                record = CodexItemRecord(
                    id=item_uri,
                    item_id=item.raw_id,
                    thread_id=thread_uri,
                    turn_id=turn_uri,
                    source_id=source_id,
                    generation_id=generation_id,
                    sequence=item.sequence,
                    item_type=item.item_type,
                    role=item.role,
                    status=item.status,
                    timestamp=item.timestamp,
                    name=item.name,
                    content=item.content,
                    source_locator=locator,
                    acl_ref=acl_ref,
                    metadata={"paths": item.paths, "call_id": item.call_id, **item.metadata},
                )
                item_records.append(record)
                item_counts[item.item_type] = item_counts.get(item.item_type, 0) + 1
                edges.append(
                    CodexEdgeRecord(
                        id=codex_edge_id("HAS_ITEM", turn_uri, item_uri),
                        source_id=source_id,
                        generation_id=generation_id,
                        source_entity_id=turn_uri,
                        target_entity_id=item_uri,
                        edge_type="HAS_ITEM",
                        evidence_locator=locator,
                    )
                )
                if item.call_id and item.item_type in {
                    "CommandExecution",
                    "ToolCall",
                    "Patch",
                }:
                    call_entities[item.call_id] = item_uri
                if item.item_type in {"CommandExecution", "ToolCall", "Patch"}:
                    raw_entities[item.raw_id] = item_uri
                parent = None
                if item.item_type == "ValidationResult":
                    parent = call_entities.get(item.call_id or "") or raw_entities.get(
                        str(item.metadata.get("derived_from_item", ""))
                    )
                elif item.call_id and item.item_type not in {
                    "CommandExecution",
                    "ToolCall",
                    "Patch",
                }:
                    parent = call_entities.get(item.call_id)
                if parent:
                    validation = item.item_type == "ValidationResult"
                    edge_type = "VALIDATED_BY" if validation else "OUTPUT_OF"
                    source_entity_id = parent if validation else item_uri
                    target_entity_id = item_uri if validation else parent
                    edges.append(
                        CodexEdgeRecord(
                            id=codex_edge_id(edge_type, source_entity_id, target_entity_id),
                            source_id=source_id,
                            generation_id=generation_id,
                            source_entity_id=source_entity_id,
                            target_entity_id=target_entity_id,
                            edge_type=edge_type,
                            evidence_locator=locator,
                        )
                    )

            episode = self._episode_item(
                thread_uri=thread_uri,
                turn_uri=turn_uri,
                turn=turn,
                goal=goal,
                summary=summary,
                source_id=source_id,
                generation_id=generation_id,
                acl_ref=acl_ref,
            )
            item_records.append(episode)
            item_counts[episode.item_type] = item_counts.get(episode.item_type, 0) + 1
            edges.append(
                CodexEdgeRecord(
                    id=codex_edge_id("HAS_EPISODE", turn_uri, episode.id),
                    source_id=source_id,
                    generation_id=generation_id,
                    source_entity_id=turn_uri,
                    target_entity_id=episode.id,
                    edge_type="HAS_EPISODE",
                    evidence_locator=episode.source_locator,
                )
            )

        counters = {
            "sessions": 1,
            "turns": len(turn_records),
            "items": len(item_records),
            "messages": item_counts.get("UserGoal", 0) + item_counts.get("AgentMessage", 0),
            "commands": item_counts.get("CommandExecution", 0),
            "file_changes": item_counts.get("FileChange", 0) + item_counts.get("Patch", 0),
            "episodes": item_counts.get("DevelopmentEpisode", 0),
            "validations": item_counts.get("ValidationResult", 0),
            "excluded_reasoning": excluded_reasoning,
            "redacted_items": redacted_count,
        }
        return ParsedCodexSession(thread_record, turn_records, item_records, edges, counters)

    def _validation_items(self, turn: _Turn) -> list[_Item]:
        """Create facts only when a validation command has an observable exit code."""
        outputs = {
            item.call_id: item
            for item in turn.items
            if item.call_id and item.item_type == "ToolResult"
        }
        derived: list[_Item] = []
        for command in turn.items:
            if command.item_type != "CommandExecution":
                continue
            command_text = command.content.splitlines()[0].removeprefix("$ ").strip()
            if not VALIDATION_COMMAND_RE.search(command_text):
                continue
            exit_code = command.metadata.get("exit_code")
            output = outputs.get(command.call_id or "")
            if exit_code is None and output:
                exit_code = output.metadata.get("exit_code")
            if isinstance(exit_code, str) and exit_code.lstrip("-").isdigit():
                exit_code = int(exit_code)
            if not isinstance(exit_code, int):
                continue
            status = "passed" if exit_code == 0 else "failed"
            output_text = output.content if output else "\n".join(command.content.splitlines()[1:])
            content = f"Validation {status}\nCommand: {command_text}\nExit code: {exit_code}" + (
                f"\nOutput:\n{output_text}" if output_text else ""
            )
            derived.append(
                _Item(
                    raw_id=f"validation-{command.raw_id}",
                    sequence=command.sequence,
                    item_type="ValidationResult",
                    role=None,
                    status=status,
                    timestamp=(output.timestamp if output else command.timestamp),
                    name=f"Validation · {status}",
                    content=content,
                    line=(output.line if output else command.line),
                    call_id=command.call_id,
                    paths=command.paths,
                    metadata={
                        "command": command_text,
                        "exit_code": exit_code,
                        "framework": self._validation_framework(command_text),
                        "derived_from_item": command.raw_id,
                        "evidence_strength": "observed_exit_code",
                    },
                )
            )
        return derived

    @staticmethod
    def _validation_framework(command: str) -> str:
        normalized = command.casefold()
        for name in (
            "pytest",
            "unittest",
            "ruff",
            "mypy",
            "pyright",
            "eslint",
            "tsc",
            "go test",
            "cargo test",
            "xcodebuild",
        ):
            if name in normalized:
                return name
        if re.search(r"\b(?:npm|pnpm|yarn|bun)\b", normalized):
            return "package-script"
        return "command"

    def _thread_id(self, rows: list[tuple[int, dict[str, Any]]], path: Path) -> str:
        for _, row in rows:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
            if row.get("type") == "session_meta" and payload.get("id"):
                return str(payload["id"])
            if row.get("type") == "thread.started" and row.get("thread_id"):
                return str(row["thread_id"])
            if row.get("thread_id"):
                return str(row["thread_id"])
        match = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27,})", path.stem, re.IGNORECASE)
        return match.group(1) if match else stable_hash(path.name)[:32]

    def _timestamp(self, row: dict[str, Any], payload: dict[str, Any]) -> str | None:
        value = row.get("timestamp") or payload.get("timestamp")
        if not value:
            value = payload.get("completed_at") or payload.get("started_at")
        return str(value) if value else None

    def _normalize_item(
        self,
        payload: dict[str, Any],
        *,
        line_number: int,
        sequence: int,
        timestamp: str | None,
        event_status: str | None,
    ) -> tuple[_Item | None, bool]:
        kind = str(payload.get("type", ""))
        if kind in REASONING_TYPES:
            return None, True
        role = str(payload.get("role", "")) or None
        if role in IGNORED_ROLES:
            return None, False
        raw_id = str(payload.get("id") or payload.get("call_id") or line_number)
        call_id = str(payload.get("call_id") or "") or None
        status = str(payload.get("status") or event_status or "") or None
        name = "事件"
        item_type = "ToolResult"
        content = ""
        paths: list[str] = []
        metadata: dict[str, Any] = {}

        if kind in {"user_message", "message", "agent_message", "agent_message_delta"}:
            if kind == "user_message":
                role = "user"
                content = _text(payload.get("message") or payload.get("content"))
            elif kind.startswith("agent_message"):
                role = "assistant"
                content = _text(
                    payload.get("message") or payload.get("content") or payload.get("text")
                )
            else:
                content = _text(payload.get("content") or payload.get("text"))
            if role == "user":
                item_type, name = "UserGoal", "用户目标"
            elif role == "assistant":
                item_type, name = "AgentMessage", "助手回复"
            else:
                return None, False
        elif kind in {"custom_tool_call", "function_call"}:
            tool_name = str(payload.get("name") or "tool")
            raw_arguments = payload.get("input") or payload.get("arguments")
            raw_content = _preferred_text(
                raw_arguments,
                ("cmd", "command", "query", "path", "value", "input", "prompt"),
            )
            content = _tool_call_text(tool_name, raw_arguments, raw_content)
            metadata["tool_name"] = tool_name
            if tool_name in {"exec_command", "write_stdin"} or (
                tool_name == "exec" and "exec_command" in raw_content
            ):
                item_type, name = "CommandExecution", "命令执行"
            elif tool_name in {"apply_patch", "patch"} or (
                tool_name == "exec" and "apply_patch" in raw_content
            ):
                item_type, name = "Patch", "代码补丁"
            else:
                item_type, name = "ToolCall", tool_name
        elif kind in {"custom_tool_call_output", "function_call_output"}:
            raw_output = payload.get("output") or payload.get("result")
            content = _preferred_text(
                raw_output,
                ("output", "text", "content", "result", "error", "message", "value"),
            )
            item_type, name = "ToolResult", "工具结果"
            exit_code = self._find_exit_code(_as_mapping(raw_output))
            if exit_code is not None:
                metadata["exit_code"] = exit_code
        elif kind == "command_execution":
            command = _text(payload.get("command"))
            output = _text(payload.get("aggregated_output") or payload.get("output"))
            content = f"$ {command}\n{output}".strip()
            item_type, name = "CommandExecution", "命令执行"
            metadata["exit_code"] = payload.get("exit_code")
        elif kind in {"file_change", "patch_apply_end"}:
            changes = payload.get("changes")
            content = _text(changes or payload.get("stdout") or payload)
            paths = self._paths(content, changes)
            item_type, name = "FileChange", "文件修改"
            metadata["success"] = payload.get("success")
        elif kind in {"mcp_tool_call", "mcp_tool_call_end", "web_search_end"}:
            content = _text(payload.get("invocation") or payload.get("query"))
            result = _text(payload.get("result"))
            if result:
                content = f"{content}\n{result}".strip()
            item_type, name = "ToolCall", str(payload.get("name") or kind)
        elif kind in {"todo_list", "plan_update"}:
            content = _text(payload.get("items") or payload.get("plan"))
            item_type, name = "Plan", "执行计划"
        else:
            return None, False

        content = _clean_codex_text(
            content,
            message=item_type in {"UserGoal", "AgentMessage"},
            tool_result=item_type == "ToolResult",
        )
        if not content:
            return None, False
        content, findings = redact_secrets(content)
        truncated = len(content) > self.settings.max_codex_item_chars
        if truncated:
            content = content[: self.settings.max_codex_item_chars] + "\n… [TRUNCATED]"
        if not paths:
            paths = self._paths(content)
        metadata.update(
            {
                "redacted": findings,
                "truncated": truncated,
                "source_event_type": kind,
                "cleaning_version": "codex-clean-text-v2",
            }
        )
        return (
            _Item(
                raw_id=raw_id,
                sequence=sequence,
                item_type=item_type,
                role=role,
                status=status,
                timestamp=timestamp,
                name=name,
                content=content,
                line=line_number,
                call_id=call_id,
                paths=paths,
                metadata=metadata,
            ),
            False,
        )

    @classmethod
    def _find_exit_code(cls, value: Any) -> int | None:
        if isinstance(value, dict):
            for key in ("exit_code", "exitCode", "returncode"):
                candidate = value.get(key)
                if isinstance(candidate, int):
                    return candidate
                if isinstance(candidate, str) and candidate.lstrip("-").isdigit():
                    return int(candidate)
            for nested in value.values():
                found = cls._find_exit_code(nested)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = cls._find_exit_code(nested)
                if found is not None:
                    return found
        return None

    def _paths(self, content: str, changes: Any = None) -> list[str]:
        values: list[str] = []
        if isinstance(changes, dict):
            values.extend(str(key) for key in changes)
        elif isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    path = change.get("path") or change.get("file")
                    if path:
                        values.append(str(path))
        values.extend(match.group(0).replace("\\", "/") for match in PATH_RE.finditer(content))

        def is_evidence_path(value: str) -> bool:
            if not value or len(value) > 500 or "://" in value or value.endswith(".git"):
                return False
            first = value.lstrip("./").split("/", 1)[0]
            return "." not in first or bool(re.fullmatch(r"[A-Za-z]:", first))

        return list(dict.fromkeys(value for value in values if is_evidence_path(value)))[:100]

    def _fallback_title(self, turns: list[_Turn]) -> str:
        for turn in turns:
            for item in turn.items:
                if item.role == "user":
                    return " ".join(item.content.split())[:100]
        return ""

    def _episode_item(
        self,
        *,
        thread_uri: str,
        turn_uri: str,
        turn: _Turn,
        goal: str,
        summary: str,
        source_id: str,
        generation_id: str,
        acl_ref: str,
    ) -> CodexItemRecord:
        commands = [item for item in turn.items if item.item_type == "CommandExecution"]
        changes = [item for item in turn.items if item.item_type in {"FileChange", "Patch"}]
        validations = [item for item in turn.items if item.item_type == "ValidationResult"]
        paths = list(dict.fromkeys(path for item in changes for path in item.paths))
        parts = [f"Goal\n{goal}" if goal else ""]
        if commands:
            parts.append(
                "Commands\n" + "\n".join(item.content.splitlines()[0] for item in commands)
            )
        if paths:
            parts.append("Changed files\n" + "\n".join(paths))
        if validations:
            parts.append(
                "Validation\n"
                + "\n".join(
                    f"{item.status}: {item.metadata.get('command', item.name)}"
                    for item in validations
                )
            )
        if summary:
            parts.append(f"Outcome\n{summary}")
        content = "\n\n".join(part for part in parts if part) or f"Turn {turn.ordinal}"
        content = content[: self.settings.max_codex_item_chars]
        episode_id = f"{turn_uri}/episode/{turn.ordinal}"
        return CodexItemRecord(
            id=episode_id,
            item_id=f"episode-{turn.ordinal}",
            thread_id=thread_uri,
            turn_id=turn_uri,
            source_id=source_id,
            generation_id=generation_id,
            sequence=1_000_000 + turn.ordinal,
            item_type="DevelopmentEpisode",
            role=None,
            status=turn.status,
            timestamp=turn.completed_at or turn.started_at,
            name=f"Episode {turn.ordinal}",
            content=content,
            source_locator=episode_id,
            acl_ref=acl_ref,
            metadata={
                "command_count": len(commands),
                "file_change_count": len(changes),
                "validation_count": len(validations),
                "paths": paths,
                "derived_from": "turn-boundary",
            },
        )
