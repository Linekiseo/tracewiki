from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .codex_adapter import CodexSessionAdapter, stable_hash
from .embeddings import LocalHashEmbedding
from .models import CodexIngestRequest, CodexSearchViewRecord
from .sources.models import SourceEventInput
from .sources.service import RawSourceService
from .storage import SQLiteStore


class CodexIngestionService:
    def __init__(
        self,
        store: SQLiteStore,
        adapter: CodexSessionAdapter,
        embedder: LocalHashEmbedding,
        sources: RawSourceService,
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.embedder = embedder
        self.sources = sources

    def defaults(self) -> dict[str, str | None]:
        settings = self.adapter.settings
        return {
            "source": str(settings.codex_home) if settings.codex_home else None,
            "project_path": str(settings.project_root) if settings.project_root else None,
        }

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
        matched: list[Path] = []
        indexable: list[Path] = []
        oversized = 0
        for path in candidates:
            if not self.adapter.matches_project(path, resolved_project):
                continue
            matched.append(path)
            try:
                if path.stat().st_size > self.adapter.settings.max_codex_session_bytes:
                    oversized += 1
                    continue
            except OSError:
                continue
            indexable.append(path)

        source_root = source_path if source_path.is_dir() else source_path.parent

        def relative_path(path: Path) -> str:
            try:
                return path.relative_to(source_root).as_posix()
            except ValueError:
                return path.name

        indexed_threads = [
            item
            for item in self.store.list_codex_threads(
                project_id=project_id,
                limit=max_sessions,
            )
            if not configured or item.get("source_id") == configured["id"]
        ]
        hidden_subagent_sessions = sum(
            1
            for item in indexed_threads
            if isinstance(item.get("metadata", {}).get("source"), dict)
            and item["metadata"]["source"].get("subagent")
        )
        indexed_files = {
            str(item.get("source_file") or "")
            for item in indexed_threads
            if item.get("source_file")
        }
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
            # A running Codex task appends to its rollout continuously.  Wait for
            # a short quiet period before treating it as a stable resync target,
            # otherwise every visit would rebuild the same active session.
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

        return {
            "project_id": project_id,
            "source_id": configured.get("id") if configured else None,
            "source_path": str(source_path),
            "project_path": str(resolved_project) if resolved_project else None,
            "source_status": configured.get("status") if configured else "unconfigured",
            "last_indexed_at": indexed_at,
            "discovered_sessions": len(matched),
            "indexable_sessions": len(indexable),
            "indexed_sessions": len(indexed_threads),
            "hidden_subagent_sessions": hidden_subagent_sessions,
            "new_sessions": len(new_files),
            "changed_sessions": len(changed_files),
            "live_sessions": len(live_files),
            "removed_sessions": len(removed_files),
            "oversized_sessions": oversized,
            "needs_sync": bool(new_files or changed_files or removed_files or not configured),
            "active_workflow_id": active_workflow["id"] if active_workflow else None,
        }

    def project_sync_request(
        self,
        project_id: str,
        *,
        project_path: str | None = None,
        include_archived: bool = False,
        max_sessions: int = 2_000,
        acl_ref: str | None = None,
    ) -> CodexIngestRequest:
        status = self.project_sync_status(
            project_id,
            project_path=project_path,
            include_archived=include_archived,
            max_sessions=max_sessions,
        )
        return CodexIngestRequest(
            source=status["source_path"],
            project_path=status["project_path"],
            project_id=project_id,
            acl_ref=acl_ref or f"project:{project_id}",
            include_archived=include_archived,
            max_sessions=max_sessions,
        )

    def enqueue(self, request: CodexIngestRequest) -> str:
        workflow_id = f"wf-codex-{uuid.uuid4()}"
        source = request.source or str(self.adapter.settings.codex_home or "~/.codex")
        self.store.create_workflow(workflow_id, f"codex:{source}", request.model_dump())
        return workflow_id

    def run(self, workflow_id: str, request: CodexIngestRequest) -> None:
        generation_id: str | None = None
        source_id: str | None = None
        try:
            self.store.update_workflow(workflow_id, status="running", stage="discover", progress=3)
            source_path = self.adapter.resolve_source(request.source)
            project_path = self._project_path(request.project_path)
            source_id = "codex-source://sha256:" + stable_hash(
                str(source_path), str(project_path or "*"), request.project_id
            )
            generation_id = f"codex-gen-{uuid.uuid4()}"
            self.store.prepare_codex_generation(
                generation_id=generation_id,
                source={
                    "id": source_id,
                    "project_id": request.project_id,
                    "source_path": str(source_path),
                    "project_path": str(project_path) if project_path else None,
                    "acl_ref": request.acl_ref,
                },
                adapter_version=self.adapter.adapter_version,
            )
            self.store.update_workflow(
                workflow_id,
                repository_id=source_id,
                generation_id=generation_id,
            )

            paths = self.adapter.discover(
                source_path,
                include_archived=request.include_archived,
                max_sessions=request.max_sessions,
            )
            titles = self.adapter.read_titles(source_path)
            counters = {
                "discovered": len(paths),
                "sessions": 0,
                "turns": 0,
                "items": 0,
                "messages": 0,
                "commands": 0,
                "file_changes": 0,
                "episodes": 0,
                "validations": 0,
                "views": 0,
                "edges": 0,
                "excluded_reasoning": 0,
                "redacted_items": 0,
                "skipped": 0,
                "parse_errors": 0,
                "oversized": 0,
                "raw_objects": 0,
            }
            self.store.update_workflow(workflow_id, stage="capture", progress=10, counters=counters)

            threads = []
            turns = []
            items = []
            edges = []
            raw_items: dict[str, str] = {}
            seen_threads: set[str] = set()
            for index, path in enumerate(paths):
                if len(threads) >= request.max_sessions:
                    break
                if not self.adapter.matches_project(path, project_path):
                    counters["skipped"] += 1
                    continue
                try:
                    if path.stat().st_size > self.adapter.settings.max_codex_session_bytes:
                        counters["oversized"] += 1
                        counters["skipped"] += 1
                        continue
                except OSError:
                    counters["parse_errors"] += 1
                    continue
                try:
                    parsed = self.adapter.parse(
                        path,
                        source_root=source_path if source_path.is_dir() else source_path.parent,
                        source_id=source_id,
                        generation_id=generation_id,
                        project_id=request.project_id,
                        project_path=project_path,
                        acl_ref=request.acl_ref,
                        titles=titles,
                    )
                except (OSError, ValueError):
                    counters["parse_errors"] += 1
                    continue
                if not parsed or parsed.thread.thread_id in seen_threads:
                    counters["skipped"] += 1
                    continue
                seen_threads.add(parsed.thread.thread_id)
                self.sources.accept(
                    SourceEventInput(
                        source_type="codex",
                        source_instance=source_id,
                        event_type="thread.snapshot.observed",
                        source_object_id=parsed.thread.thread_id,
                        source_version=parsed.thread.source_hash,
                        event_time=parsed.thread.updated_at,
                        project_id=request.project_id,
                        acl_ref=request.acl_ref,
                        source_uri=parsed.thread.source_file,
                        payload={
                            "thread_id": parsed.thread.thread_id,
                            "title": parsed.thread.title,
                            "cwd": parsed.thread.cwd,
                            "source_hash": parsed.thread.source_hash,
                        },
                        adapter_version=self.adapter.adapter_version,
                        schema_version="codex-thread-v1",
                        metadata={"workflow_id": workflow_id, "redacted_payload": True},
                    )
                )
                threads.append(parsed.thread)
                turns.extend(parsed.turns)
                items.extend(parsed.items)
                for item in parsed.items:
                    raw = self.sources.persist_bytes(
                        project_id=request.project_id,
                        source_type="codex",
                        source_instance=source_id,
                        source_object_id=item.id,
                        source_version=item.timestamp or f"sequence:{item.sequence}",
                        payload=item.content.encode("utf-8"),
                        source_uri=item.source_locator,
                        media_type="text/plain; charset=utf-8",
                        acl_ref=request.acl_ref,
                        adapter_version=self.adapter.adapter_version,
                        schema_version="codex-item-v1",
                        metadata={
                            "item_type": item.item_type,
                            "thread_id": item.thread_id,
                            "redacted": bool(item.metadata.get("redacted")),
                        },
                    )
                    raw_items[item.id] = raw["id"]
                    counters["raw_objects"] += 1
                edges.extend(parsed.edges)
                for key, value in parsed.counters.items():
                    counters[key] += value
                if index % 10 == 0:
                    progress = 12 + int(43 * (index + 1) / max(1, len(paths)))
                    self.store.update_workflow(
                        workflow_id, stage="parse", progress=progress, counters=counters
                    )

            if not threads:
                raise ValueError(
                    "No matching Codex sessions were indexed; "
                    f"skipped={counters['skipped']}, oversized={counters['oversized']}, "
                    f"parse_errors={counters['parse_errors']}. "
                    "The previously published generation was preserved."
                )

            self.store.update_workflow(
                workflow_id, stage="structure", progress=62, counters=counters
            )
            views = [
                CodexSearchViewRecord(
                    id=f"{item.id}#view={item.item_type.lower()}",
                    entity_id=item.id,
                    thread_id=item.thread_id,
                    source_id=source_id,
                    generation_id=generation_id,
                    project_id=request.project_id,
                    view_type=self._view_type(item.item_type),
                    name=item.name,
                    content=item.content,
                    vector=self.embedder.embed(item.content),
                    embedding_model=self.embedder.model_id,
                )
                for item in items
                if item.content.strip()
            ]
            counters["views"] = len(views)
            counters["edges"] = len(edges)
            validation = {
                "reasoning_excluded": True,
                "source_allowlisted": True,
                "project_scoped": project_path is not None,
                "secrets_redacted": counters["redacted_items"],
                "duplicate_threads_skipped": max(0, counters["skipped"]),
            }
            self.store.update_workflow(workflow_id, stage="index", progress=82, counters=counters)
            self.store.publish_codex_generation(
                source_id=source_id,
                generation_id=generation_id,
                threads=threads,
                turns=turns,
                items=items,
                edges=edges,
                views=views,
                counts=counters,
                validation=validation,
            )
            for entity_id, raw_object_id in raw_items.items():
                self.sources.store.link_derivations(
                    raw_object_id,
                    [entity_id],
                    kind="codex_normalization",
                    generation_id=generation_id,
                    derivation_version=self.adapter.adapter_version,
                )
            self.store.update_workflow(
                workflow_id,
                status="completed",
                stage="publish",
                progress=100,
                counters=counters,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.store.fail_codex_generation(generation_id, source_id, message)
            self.store.update_workflow(
                workflow_id,
                status="failed",
                stage="failed",
                progress=100,
                error=message,
            )

    def _project_path(self, value: str | None) -> Path | None:
        if value:
            return Path(value).expanduser().resolve(strict=False)
        return self.adapter.settings.project_root

    def _view_type(self, item_type: str) -> str:
        return {
            "DevelopmentEpisode": "episode.summary",
            "UserGoal": "message.goal",
            "AgentMessage": "message.agent",
            "CommandExecution": "execution.command",
            "FileChange": "change.file",
            "Patch": "change.patch",
            "ToolResult": "tool.result",
            "ToolCall": "tool.call",
            "ValidationResult": "validation.result",
        }.get(item_type, f"event.{item_type.lower()}")
