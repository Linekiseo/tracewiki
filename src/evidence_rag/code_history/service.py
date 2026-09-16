from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from ..parser import CodeParser, language_for_path
from ..sources.service import RawSourceService
from ..storage import utc_now
from .models import TestResultCreate
from .store import CodeHistoryStore

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class CodeHistoryError(ValueError):
    pass


class CodeHistoryService:
    def __init__(self, store: CodeHistoryStore, sources: RawSourceService) -> None:
        self.store = store
        self.sources = sources
        self.parser = CodeParser()

    def sync(
        self,
        repository_id: str,
        depth: int = 25,
        include_diffs: bool = True,
        *,
        fetch_remote: bool = True,
    ) -> dict[str, Any]:
        repository = self.store.repository(repository_id)
        if not repository:
            raise CodeHistoryError("repository not found")
        root = Path(repository["local_path"])
        if not (root / ".git").exists() and not self._git_ok(root, ["rev-parse", "--git-dir"]):
            return {"repository_id": repository_id, "status": "not_git", "commits": 0, "hunks": 0}
        if fetch_remote:
            self._fetch_remote_heads(root, depth)
        default_branch = self._detect_default_branch(root, repository.get("default_branch"))
        if default_branch and default_branch != repository.get("default_branch"):
            self.store.update_default_branch(repository_id, default_branch)
            repository = {**repository, "default_branch": default_branch}
        commits = self._commits(repository, root, depth)
        branches = self._branches(repository, root)
        hunks = self._diffs(repository, root, commits) if include_diffs else []
        self.store.replace_history(repository_id, commits, branches, hunks)
        for hunk in hunks:
            if hunk.get("raw_object_id"):
                self.sources.store.link_derivations(
                    hunk["raw_object_id"],
                    [hunk["id"]],
                    kind="git_diff_parse",
                    generation_id=None,
                    derivation_version="git-diff-v1",
                )
        return {
            "repository_id": repository_id,
            "status": "completed",
            "commits": len(commits),
            "branches": len(branches),
            "hunks": len(hunks),
        }

    def record_test(self, request: TestResultCreate) -> dict[str, Any]:
        repository = self.store.repository(request.repository_id)
        if not repository or repository["project_id"] != request.project_id:
            raise CodeHistoryError("repository not found in project")
        commit = self.store.resolve_commit(request.repository_id, request.commit_sha)
        record = request.model_dump()
        record.update(
            id=f"test-result://{request.project_id}/{uuid4().hex}",
            commit_id=commit["id"] if commit else None,
        )
        return self.store.create_test_result(record)

    def refs(self, repository_id: str) -> dict[str, Any]:
        repository = self._repository(repository_id)
        branches = self.store.list_branches(repository_id)
        default_branch = repository.get("default_branch") or "HEAD"
        return {
            "repository_id": repository_id,
            "head": {
                "name": "HEAD",
                "sha": repository["head_commit"],
                "branch": default_branch,
            },
            "head_sha": repository["head_commit"],
            "default_branch": default_branch,
            "branches": branches,
            "observed_at": max(
                (item["observed_at"] for item in branches),
                default=repository.get("updated_at"),
            ),
        }

    def list_tree(self, repository_id: str, ref: str) -> list[dict[str, Any]]:
        repository = self._repository(repository_id)
        root = Path(repository["local_path"])
        sha = self._resolve_ref(repository, ref)
        output = self._git_bytes(root, ["ls-tree", "-r", "-l", "-z", sha])
        files: list[dict[str, Any]] = []
        for record in output.split(b"\0"):
            if not record or b"\t" not in record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            fields = metadata.decode("utf-8", errors="replace").split()
            if len(fields) < 4 or fields[1] != "blob":
                continue
            path = raw_path.decode("utf-8", errors="replace")
            files.append(
                {
                    "repository_id": repository_id,
                    "path": path,
                    "language": language_for_path(path) or "text",
                    "blob_hash": fields[2],
                    "size": int(fields[3]) if fields[3].isdigit() else None,
                    "commit_sha": sha,
                    "ref": ref,
                    "historical": sha != repository["head_commit"],
                }
            )
        return files

    def read_file(self, repository_id: str, ref: str, path: str) -> dict[str, Any]:
        repository = self._repository(repository_id)
        safe_path = self._safe_path(path)
        root = Path(repository["local_path"])
        sha = self._resolve_ref(repository, ref)
        content = self._git_bytes(root, ["show", f"{sha}:{safe_path}"])
        if b"\0" in content:
            return {
                "repository_id": repository_id,
                "path": safe_path,
                "commit_sha": sha,
                "ref": ref,
                "historical": sha != repository["head_commit"],
                "binary": True,
                "size": len(content),
                "content": None,
                "symbols": [],
            }
        text = content.decode("utf-8", errors="replace")
        parsed = self.parser.parse(safe_path, text)
        return {
            "repository_id": repository_id,
            "path": safe_path,
            "language": parsed.language,
            "content": text,
            "content_hash": parsed.content_hash,
            "blob_hash": parsed.blob_hash,
            "parse_error": parsed.parse_error,
            "symbols": [
                {
                    "name": symbol.name,
                    "qualified_name": symbol.qualified_name,
                    "kind": symbol.kind,
                    "start_line": symbol.start_line,
                    "end_line": symbol.end_line,
                }
                for symbol in parsed.symbols
            ],
            "commit_sha": sha,
            "ref": ref,
            "historical": sha != repository["head_commit"],
            "binary": False,
            "size": len(content),
            "source_uri": f"{repository['id']}@{sha}:{safe_path}",
            "acl_ref": repository["acl_ref"],
        }

    def compare(
        self,
        repository_id: str,
        base_ref: str,
        target_ref: str,
        path: str | None = None,
    ) -> dict[str, Any]:
        repository = self._repository(repository_id)
        root = Path(repository["local_path"])
        base_sha = self._resolve_ref(repository, base_ref)
        target_sha = self._resolve_ref(repository, target_ref)
        names = self._git(
            root,
            [
                "diff",
                "--name-status",
                "--find-renames",
                base_sha,
                target_sha,
                *(["--", self._safe_path(path)] if path else []),
            ],
        )
        changed_files = []
        for line in names.splitlines():
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            code = fields[0]
            old_path = fields[1] if code.startswith(("R", "C")) and len(fields) > 2 else None
            changed_files.append(
                {
                    "status": code,
                    "old_path": old_path,
                    "path": fields[2] if old_path else fields[1],
                }
            )
        result: dict[str, Any] = {
            "repository_id": repository_id,
            "base_ref": base_ref,
            "base_sha": base_sha,
            "target_ref": target_ref,
            "target_sha": target_sha,
            "changed_files": changed_files,
        }
        if path:
            safe_path = self._safe_path(path)
            result["path"] = safe_path
            result["diff"] = self._git(
                root,
                ["diff", "--find-renames", "--unified=5", base_sha, target_sha, "--", safe_path],
            )
            result["base_file"] = self._optional_file(repository_id, base_ref, safe_path)
            result["target_file"] = self._optional_file(repository_id, target_ref, safe_path)
        return result

    def _optional_file(self, repository_id: str, ref: str, path: str) -> dict[str, Any] | None:
        try:
            return self.read_file(repository_id, ref, path)
        except CodeHistoryError:
            return None

    def _repository(self, repository_id: str) -> dict[str, Any]:
        repository = self.store.repository(repository_id)
        if not repository:
            raise CodeHistoryError("repository not found")
        return repository

    def _resolve_ref(self, repository: dict[str, Any], ref: str) -> str:
        candidate = ref.strip()
        if not candidate or candidate in {"HEAD", "current"}:
            return repository["head_commit"]
        branch = next(
            (
                item
                for item in self.store.list_branches(repository["id"])
                if item["name"] == candidate
            ),
            None,
        )
        if branch:
            return branch["head_sha"]
        commit = self.store.resolve_commit(repository["id"], candidate)
        if commit:
            return commit["sha"]
        raise CodeHistoryError("ref is not present in indexed history")

    @staticmethod
    def _safe_path(path: str) -> str:
        item = PurePosixPath(path.replace("\\", "/"))
        if item.is_absolute() or ".." in item.parts or not item.parts or "\0" in path:
            raise CodeHistoryError("invalid repository path")
        return str(item)

    def _commits(self, repository: dict[str, Any], root: Path, depth: int) -> list[dict[str, Any]]:
        separator = "%x1f"
        output = self._git(
            root,
            [
                "log",
                f"--max-count={depth}",
                f"--format=%H{separator}%P{separator}%an{separator}%ae{separator}%aI{separator}%cI{separator}%s{separator}%T",
            ],
        )
        identity = repository_id_part(repository["id"])
        now = utc_now()
        commits = []
        for line in output.splitlines():
            fields = line.split("\x1f")
            if len(fields) != 8:
                continue
            sha, parents, author, email, authored, committed, message, tree_hash = fields
            commits.append(
                {
                    "id": f"git://{identity}/commit/{sha}",
                    "project_id": repository["project_id"],
                    "sha": sha,
                    "tree_hash": tree_hash,
                    "parent_shas": parents.split() if parents else [],
                    "author_name": author,
                    "author_email_hash": "sha256:"
                    + hashlib.sha256(email.casefold().encode()).hexdigest(),
                    "authored_at": authored,
                    "committed_at": committed,
                    "message": message,
                    "source_uri": self._commit_uri(repository, sha),
                    "acl_ref": repository["acl_ref"],
                    "observed_at": now,
                }
            )
        return commits

    def _branches(self, repository: dict[str, Any], root: Path) -> list[dict[str, Any]]:
        output = self._git(
            root,
            [
                "for-each-ref",
                "--format=%(refname:short)\t%(objectname)",
                "refs/heads",
                "refs/remotes/origin",
            ],
        )
        now = utc_now()
        rows = []
        for line in output.splitlines():
            if "\t" not in line:
                continue
            name, sha = line.split("\t", 1)
            if name == "origin" or name.endswith("/HEAD"):
                continue
            rows.append(
                {
                    "id": f"branch://{hashlib.sha256(f'{repository["id"]}:{name}'.encode()).hexdigest()}",
                    "name": name,
                    "head_sha": sha,
                    "is_default": name
                    in {
                        repository.get("default_branch"),
                        f"origin/{repository.get('default_branch')}",
                    },
                    "observed_at": now,
                }
            )
        return rows

    def _detect_default_branch(self, root: Path, configured: str | None) -> str | None:
        refs = {
            line.strip()
            for line in self._git(
                root,
                [
                    "for-each-ref",
                    "--format=%(refname:short)",
                    "refs/heads",
                    "refs/remotes/origin",
                ],
            ).splitlines()
            if line.strip() and line.strip() != "origin" and not line.strip().endswith("/HEAD")
        }
        configured_name = (configured or "").removeprefix("origin/")
        if (
            configured_name
            and configured_name not in {"HEAD", "worktree"}
            and (configured_name in refs or f"origin/{configured_name}" in refs)
        ):
            return configured_name

        remote_head = self._git_optional(
            root,
            ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        ).strip()
        if remote_head.startswith("origin/"):
            remote_head = remote_head.removeprefix("origin/")
        if remote_head and (remote_head in refs or f"origin/{remote_head}" in refs):
            return remote_head

        current = self._git(root, ["branch", "--show-current"]).strip()
        if current and current in refs:
            return current
        for preferred in ("main", "master"):
            if preferred in refs or f"origin/{preferred}" in refs:
                return preferred
        local = sorted(ref for ref in refs if not ref.startswith("origin/"))
        if local:
            return local[0]
        remote = sorted(ref.removeprefix("origin/") for ref in refs)
        return remote[0] if remote else None

    def _diffs(
        self, repository: dict[str, Any], root: Path, commits: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for commit in commits:
            if len(rows) >= 500:
                break
            sha = commit["sha"]
            parent = commit["parent_shas"][0] if commit["parent_shas"] else None
            names = self._git(
                root,
                [
                    "diff-tree",
                    "--root",
                    "--no-commit-id",
                    "--name-status",
                    "-r",
                    "--find-renames",
                    sha,
                ],
            )
            for change in names.splitlines():
                if len(rows) >= 500:
                    break
                fields = change.split("\t")
                if len(fields) < 2:
                    continue
                status = fields[0]
                old_path = fields[1] if status.startswith("R") and len(fields) > 2 else None
                path = fields[2] if old_path else fields[1]
                patch = self._git(
                    root, ["show", "--format=", "--find-renames", "--unified=3", sha, "--", path]
                )
                if not patch:
                    patch = f"{status}\t{old_path + ' -> ' if old_path else ''}{path}"
                patch = patch[:200_000]
                patch_hash = "sha256:" + hashlib.sha256(patch.encode()).hexdigest()
                parsed_hunks = self._split_hunks(patch) or [(None, None, None, None, patch)]
                for ordinal, (old_start, old_count, new_start, new_count, body) in enumerate(
                    parsed_hunks, 1
                ):
                    token = hashlib.sha256(
                        f"{repository['id']}\x1f{sha}\x1f{path}\x1f{ordinal}\x1f{body}".encode()
                    ).hexdigest()
                    hunk_id = f"diff://sha256:{token}"
                    raw = self.sources.persist_bytes(
                        project_id=repository["project_id"],
                        source_type="git",
                        source_instance=repository["id"],
                        source_object_id=hunk_id,
                        source_version=sha,
                        payload=body.encode(),
                        source_uri=f"{self._commit_uri(repository, sha)}#diff-{quote(path, safe='/')}",
                        media_type="text/x-diff; charset=utf-8",
                        acl_ref=repository["acl_ref"],
                        adapter_version="git-cli-v1",
                        schema_version="git-diff-v1",
                        metadata={"path": path, "change_type": status},
                    )
                    rows.append(
                        {
                            "id": hunk_id,
                            "project_id": repository["project_id"],
                            "commit_id": commit["id"],
                            "commit_sha": sha,
                            "parent_sha": parent,
                            "path": path,
                            "old_path": old_path,
                            "change_type": status,
                            "old_start": old_start,
                            "old_count": old_count,
                            "new_start": new_start,
                            "new_count": new_count,
                            "patch": body,
                            "patch_hash": patch_hash,
                            "source_locator": f"{repository['name']}@{sha[:12]}:{path}#diff",
                            "acl_ref": repository["acl_ref"],
                            "affected_symbols": [],
                            "raw_object_id": raw["id"],
                            "observed_at": utc_now(),
                        }
                    )
        return rows

    @staticmethod
    def _split_hunks(patch: str) -> list[tuple[int, int, int, int, str]]:
        lines = patch.splitlines()
        starts = [index for index, line in enumerate(lines) if HUNK_RE.match(line)]
        hunks = []
        for position, start in enumerate(starts):
            match = HUNK_RE.match(lines[start])
            if not match:
                continue
            end = starts[position + 1] if position + 1 < len(starts) else len(lines)
            values = [int(value or 1) for value in match.groups()]
            hunks.append((*values, "\n".join(lines[start:end])))
        return hunks

    @staticmethod
    def _commit_uri(repository: dict[str, Any], sha: str) -> str:
        source = repository.get("source_url") or repository["id"]
        if isinstance(source, str) and source.startswith("http"):
            return source.removesuffix(".git") + "/commit/" + sha
        return f"{repository['id']}@{sha}"

    @staticmethod
    def _git(root: Path, args: list[str]) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        )
        if result.returncode != 0:
            raise CodeHistoryError(result.stderr.strip() or "git command failed")
        return result.stdout

    @staticmethod
    def _git_bytes(root: Path, args: list[str]) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            timeout=120,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        )
        if result.returncode != 0:
            raise CodeHistoryError(
                result.stderr.decode("utf-8", errors="replace").strip() or "git command failed"
            )
        return result.stdout

    @staticmethod
    def _git_optional(root: Path, args: list[str]) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        )
        return result.stdout if result.returncode == 0 else ""

    @staticmethod
    def _git_ok(root: Path, args: list[str]) -> bool:
        return (
            subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            ).returncode
            == 0
        )

    @staticmethod
    def _fetch_remote_heads(root: Path, depth: int) -> None:
        """Best-effort remote-head refresh without checking out or mutating the worktree."""
        if not CodeHistoryService._git_ok(root, ["remote", "get-url", "origin"]):
            return
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "fetch",
                    "--no-tags",
                    f"--depth={depth}",
                    "origin",
                    "+refs/heads/*:refs/remotes/origin/*",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
            )
        except (OSError, subprocess.TimeoutExpired):
            return


def repository_id_part(repository_id: str) -> str:
    return repository_id.removeprefix("repo://")
