from __future__ import annotations

import json
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..storage import utc_now

if TYPE_CHECKING:
    from .service import CodexBridgeService


def resolve_codex_binary() -> str | None:
    """Return a working Codex executable, including the desktop-app bundle.

    Some local package-manager shims can remain on PATH after their target was
    removed.  Checking ``--version`` keeps the integration status and the
    background runner from advertising a command that cannot actually start.
    """

    candidates = [
        shutil.which("codex"),
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        str(Path.home() / ".codex" / "plugins" / ".plugin-appserver" / "codex"),
    ]
    for candidate in dict.fromkeys(value for value in candidates if value):
        path = Path(candidate).expanduser()
        if not path.is_file():
            continue
        try:
            result = subprocess.run(
                [str(path), "--version"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return str(path)
    return None


class CodexRunner(ABC):
    @abstractmethod
    def prepare(self, execution: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class DeepLinkRunner(CodexRunner):
    def prepare(self, execution: dict[str, Any]) -> dict[str, Any]:
        workspace = quote(execution["workspace_path"], safe="")
        prompt = quote(execution["prompt"], safe="")
        return {
            "runner": "deep-link",
            "url": f"codex://new?path={workspace}&prompt={prompt}",
            "auto_send": False,
        }


class ExecRunner(CodexRunner):
    def __init__(self, bridge: CodexBridgeService) -> None:
        self.bridge = bridge

    def prepare(self, execution: dict[str, Any]) -> dict[str, Any]:
        codex = resolve_codex_binary()
        return {
            "runner": "codex-exec",
            "available": bool(codex),
            "binary": codex,
            "sandbox": execution["sandbox"],
        }

    def run(self, execution_id: str) -> None:
        execution = self.bridge.store.get_execution(execution_id)
        if not execution:
            return
        codex = resolve_codex_binary()
        if not codex:
            self._fail(execution_id, "Codex CLI 不可用")
            return
        workspace = Path(execution["workspace_path"]).expanduser().resolve()
        if not workspace.is_dir():
            self._fail(execution_id, "工作目录不存在")
            return
        artifact_dir = self.bridge.artifact_dir / execution_id.split("://")[-1]
        artifact_dir.mkdir(parents=True, exist_ok=True)
        event_path = artifact_dir / "events.jsonl"
        command = [
            codex,
            "exec",
            "--json",
            "--sandbox",
            execution["sandbox"],
            execution["prompt"],
        ]
        self.bridge.store.update_execution(
            execution_id, {"status": "running", "started_at": utc_now()}
        )
        self.bridge.store.append_event(
            execution_id,
            "started",
            "Codex 后台执行已启动",
            {"command": ["codex", "exec", "--json", "--sandbox", execution["sandbox"]]},
        )
        thread_id: str | None = None
        final_message = ""
        changed_files: list[str] = []
        try:
            with event_path.open("w", encoding="utf-8") as artifact:
                process = subprocess.Popen(
                    command,
                    cwd=workspace,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                assert process.stdout is not None
                for raw_line in process.stdout:
                    artifact.write(raw_line)
                    artifact.flush()
                    try:
                        event = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    event_type = str(event.get("type") or "event")
                    if event_type == "thread.started":
                        thread_id = event.get("thread_id")
                        self.bridge.store.update_execution(execution_id, {"thread_id": thread_id})
                    item = event.get("item") or {}
                    if item.get("type") == "agent_message":
                        final_message = item.get("text") or final_message
                    if item.get("type") == "file_change":
                        changed_files.extend(
                            change.get("path")
                            for change in item.get("changes", [])
                            if change.get("path")
                        )
                    if event_type in {"turn.failed", "error"}:
                        self.bridge.store.append_event(
                            execution_id,
                            "error",
                            str(event.get("message") or event_type),
                            event,
                        )
                stderr = process.stderr.read() if process.stderr else ""
                return_code = process.wait()
            if return_code:
                self._fail(execution_id, stderr.strip() or f"Codex 退出码 {return_code}")
                return
            self.bridge.store.update_execution(
                execution_id,
                {
                    "status": "review",
                    "thread_id": thread_id,
                    "summary": final_message,
                    "changed_files": sorted(set(changed_files)),
                    "artifact_path": str(event_path),
                    "completed_at": utc_now(),
                },
            )
            self.bridge.store.append_event(
                execution_id,
                "review_requested",
                "执行完成，等待人工复核",
                {"changed_files": sorted(set(changed_files))},
                str(event_path),
            )
            self.bridge.request_review(
                execution_id,
                requested_by=f"codex:runner:{thread_id or 'unknown'}",
                summary=final_message,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._fail(execution_id, str(exc))

    def _fail(self, execution_id: str, error: str) -> None:
        self.bridge.store.update_execution(
            execution_id,
            {"status": "failed", "error": error, "completed_at": utc_now()},
        )
        self.bridge.store.append_event(execution_id, "failed", error)


class AppServerRunner(CodexRunner):
    """Reserved adapter; app-server stays disabled until its protocol is stabilized."""

    def prepare(self, execution: dict[str, Any]) -> dict[str, Any]:
        return {"runner": "app-server", "available": False, "experimental": True}
