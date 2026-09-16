from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePath
from urllib.parse import urlparse

from .config import Settings
from .parser import language_for_path

DEFAULT_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "target",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".turbo",
    "Pods",
    "DerivedData",
}

REMOTE_RE = re.compile(r"^(?:https?://|ssh://|git@)", re.IGNORECASE)


@dataclass(slots=True)
class RepositorySnapshot:
    id: str
    project_id: str
    name: str
    source_type: str
    source_url: str | None
    local_path: Path
    default_branch: str
    head_commit: str
    base_commit: str | None
    dirty: bool
    acl_ref: str

    def as_storage_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "local_path": str(self.local_path),
            "default_branch": self.default_branch,
            "head_commit": self.head_commit,
            "acl_ref": self.acl_ref,
        }


class RepositoryResolver:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve(
        self,
        source: str,
        *,
        project_id: str,
        acl_ref: str,
        branch: str | None = None,
        history_depth: int = 25,
    ) -> RepositorySnapshot:
        if REMOTE_RE.match(source):
            return self._remote(source, project_id, acl_ref, branch, history_depth)
        return self._local(Path(source), project_id, acl_ref, branch)

    def _remote(
        self,
        source: str,
        project_id: str,
        acl_ref: str,
        branch: str | None,
        history_depth: int,
    ) -> RepositorySnapshot:
        identity = self._remote_identity(source)
        target = self.settings.repository_cache / hashlib.sha256(source.encode()).hexdigest()[:16]
        if (target / ".git").is_dir():
            ref = branch or "HEAD"
            self._git(["fetch", "--depth", str(history_depth), "origin", ref], cwd=target)
            self._git(["checkout", "--detach", "FETCH_HEAD"], cwd=target)
        elif target.exists():
            raise ValueError(f"repository cache path is not a Git checkout: {target}")
        else:
            args = ["clone", "--filter=blob:none", "--depth", str(history_depth)]
            if branch:
                args.extend(["--branch", branch, "--single-branch"])
            args.extend(["--", source, str(target)])
            self._git(args)
        current_branch = (
            branch
            or self._git_text(["branch", "--show-current"], cwd=target, check=False)
            or self._remote_default_branch(target)
            or "HEAD"
        )
        commit = self._git_text(["rev-parse", "HEAD"], cwd=target)
        return RepositorySnapshot(
            id=f"repo://{identity}",
            project_id=project_id,
            name=identity.rsplit("/", 1)[-1],
            source_type="git_remote",
            source_url=source,
            local_path=target.resolve(),
            default_branch=current_branch,
            head_commit=commit,
            base_commit=commit,
            dirty=False,
            acl_ref=acl_ref,
        )

    def _remote_default_branch(self, root: Path) -> str | None:
        remote_head = self._git_text(
            ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            cwd=root,
            check=False,
        )
        if remote_head.startswith("origin/"):
            return remote_head.removeprefix("origin/")
        return remote_head or None

    def _local(
        self, path: Path, project_id: str, acl_ref: str, branch: str | None
    ) -> RepositorySnapshot:
        resolved = path.expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"local repository does not exist or is not a directory: {resolved}")
        if not any(resolved.is_relative_to(root) for root in self.settings.allowed_local_roots):
            allowed = ", ".join(str(root) for root in self.settings.allowed_local_roots)
            raise PermissionError(f"local repository is outside RAG_ALLOWED_LOCAL_ROOTS: {allowed}")

        is_git = (resolved / ".git").exists() or self._is_inside_git(resolved)
        base_commit: str | None = None
        current_branch = "worktree"
        dirty = False
        origin: str | None = None
        if is_git:
            base_commit = self._git_text(["rev-parse", "HEAD"], cwd=resolved)
            current_branch = self._git_text(["branch", "--show-current"], cwd=resolved) or "HEAD"
            if branch and branch != current_branch:
                raise ValueError(
                    f"local worktree is on branch {current_branch!r}, not requested branch {branch!r}"
                )
            dirty = bool(
                self._git_text(["status", "--porcelain", "--untracked-files=normal"], cwd=resolved)
            )
            origin = (
                self._git_text(["config", "--get", "remote.origin.url"], cwd=resolved, check=False)
                or None
            )

        identity = self._remote_identity(origin) if origin and REMOTE_RE.match(origin) else None
        if not identity:
            suffix = hashlib.sha256(str(resolved).encode()).hexdigest()[:8]
            identity = f"local/{resolved.name}-{suffix}"

        # The worktree manifest becomes part of the version whenever content is uncommitted.
        manifest = self._manifest_hash(resolved)
        head = base_commit or f"worktree-{manifest[:16]}"
        if dirty and base_commit:
            head = f"{base_commit}+dirty.{manifest[:12]}"
        return RepositorySnapshot(
            id=f"repo://{identity}",
            project_id=project_id,
            name=resolved.name,
            source_type="git_local" if is_git else "folder",
            source_url=origin,
            local_path=resolved,
            default_branch=current_branch,
            head_commit=head,
            base_commit=base_commit,
            dirty=dirty,
            acl_ref=acl_ref,
        )

    def discover_files(self, snapshot: RepositorySnapshot, ignore: list[str]) -> list[Path]:
        root = snapshot.local_path
        patterns = [*DEFAULT_IGNORES, *ignore]
        if snapshot.source_type.startswith("git"):
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                files_by_relative: dict[str, Path] = {}
                for raw in result.stdout.split(b"\0"):
                    if not raw:
                        continue
                    relative = raw.decode("utf-8", errors="surrogateescape")
                    name = PurePath(relative).name
                    if self._ignored(relative, name, patterns) or not language_for_path(relative):
                        continue
                    source_path = root / relative
                    if source_path.is_symlink():
                        continue
                    candidate = source_path.resolve(strict=False)
                    if not candidate.is_relative_to(root) or not candidate.is_file():
                        continue
                    canonical_relative = candidate.relative_to(root).as_posix()
                    files_by_relative[canonical_relative] = candidate
                return [files_by_relative[key] for key in sorted(files_by_relative)]
        files: list[Path] = []
        for current, directories, names in os.walk(root):
            current_path = Path(current)
            relative_dir = current_path.relative_to(root).as_posix()
            directories[:] = [
                name
                for name in directories
                if not self._ignored(f"{relative_dir}/{name}".lstrip("./"), name, patterns)
            ]
            for name in names:
                candidate = current_path / name
                if candidate.is_symlink():
                    continue
                relative = candidate.relative_to(root).as_posix()
                if self._ignored(relative, name, patterns):
                    continue
                if language_for_path(relative):
                    files.append(current_path / name)
        return sorted(files, key=lambda item: item.relative_to(root).as_posix())

    def read_text(self, path: Path) -> str | None:
        if path.stat().st_size > self.settings.max_file_bytes:
            return None
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")

    def blob_hash(self, snapshot: RepositorySnapshot, path: Path, content: str) -> str:
        if snapshot.base_commit and not snapshot.dirty:
            relative = path.relative_to(snapshot.local_path).as_posix()
            value = self._git_text(
                ["rev-parse", f"HEAD:{relative}"], cwd=snapshot.local_path, check=False
            )
            if value:
                return f"git:{value}"
        return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _manifest_hash(self, root: Path) -> str:
        digest = hashlib.sha256()
        for path in self.discover_files(
            RepositorySnapshot("", "", root.name, "folder", None, root, "", "", None, False, ""),
            [],
        ):
            relative = path.relative_to(root).as_posix()
            digest.update(relative.encode())
            try:
                digest.update(hashlib.sha256(path.read_bytes()).digest())
            except OSError:
                continue
        return digest.hexdigest()

    def _ignored(self, relative: str, name: str, patterns: list[str]) -> bool:
        parts = Path(relative).parts
        return any(
            pattern in parts
            or fnmatch.fnmatch(relative, pattern)
            or fnmatch.fnmatch(name, pattern)
            or fnmatch.fnmatch(relative, f"**/{pattern}/**")
            for pattern in patterns
        )

    def _remote_identity(self, url: str | None) -> str:
        if not url:
            raise ValueError("remote URL is empty")
        if url.startswith("git@"):
            host_path = url[4:].replace(":", "/", 1)
        else:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https", "ssh"}:
                raise ValueError("only HTTP(S), SSH and git@ remote URLs are supported")
            if parsed.username or parsed.password:
                raise ValueError(
                    "credentials in remote URLs are not accepted; use a Git credential helper"
                )
            host_path = f"{parsed.hostname or 'remote'}{parsed.path}"
        return host_path.strip("/").removesuffix(".git")

    def _is_inside_git(self, path: Path) -> bool:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _git(self, args: list[str], cwd: Path | None = None) -> None:
        command = ["git", *(["-C", str(cwd)] if cwd else []), *args]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Git operation timed out after 180 seconds") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "Git command failed").strip()
            raise ValueError(detail[-2_000:]) from exc

    def _git_text(self, args: list[str], cwd: Path, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
