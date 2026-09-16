"""Build, verify, and materialize the exact G0 version-admission source set.

This artifact does not stage or commit files.  It turns the current reviewed scope
policy into a repository-relative, content-addressed file set that an owner can
compare with a later clean revision.  Formal application databases are excluded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .maturity_g0_v1 import _canonical_json_bytes, _canonical_sha256

MATURITY_G0_ADMISSION_VERSION = "rag-maturity-g0-admission-v1"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_HASH_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_GIT_STATES = frozenset({"clean", "modified", "staged", "modified_and_staged", "untracked"})
_EXCLUDED_PARTS = frozenset(
    {
        ".build",
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".turbo",
        ".venv",
        "DerivedData",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
    }
)
_EXCLUDED_SUFFIXES = frozenset(
    {
        ".db",
        ".pyc",
        ".sqlite",
        ".sqlite3",
        ".sqlite3-shm",
        ".sqlite3-wal",
        ".tsbuildinfo",
        ".wal",
    }
)
_SCOPE_PATTERNS: dict[str, tuple[str, ...]] = {
    "backend": ("src/evidence_rag/**/*",),
    "tests": ("tests/**/*",),
    "documentation": ("docs/**/*",),
    "evaluation": ("evals/**/*",),
    "frontend": ("frontend/**/*",),
    "native": ("native/**/*",),
    "plugins": ("plugins/**/*",),
    "web": ("web/**/*",),
    "delivery": (
        ".github/workflows/**/*",
        ".gitignore",
        "Dockerfile",
        "Makefile",
        "README.md",
        "design-qa.md",
        "pyproject.toml",
        "uv.lock",
    ),
    "g0_evidence": (
        "artifacts/rag-maturity/g0/baseline-20260806-v1/**/*",
        "artifacts/rag-maturity/g0/g0-engineering-20260806-v1/**/*",
        "artifacts/wiki-rag/**/*",
    ),
}


class MaturityG0AdmissionError(RuntimeError):
    """The G0 admission manifest or materialized source set is invalid."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _git_lines(root: Path, *arguments: str) -> frozenset[str]:
    result = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise MaturityG0AdmissionError(f"git command failed: {' '.join(arguments)}")
    return frozenset(item for item in result.stdout.splitlines() if item)


def _git_value(root: Path, *arguments: str) -> str:
    values = _git_lines(root, *arguments)
    if len(values) != 1:
        raise MaturityG0AdmissionError(f"git command returned no unique value: {' '.join(arguments)}")
    return next(iter(values))


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or value != path.as_posix()
        or any(part in {"", "."} for part in path.parts)
    ):
        raise MaturityG0AdmissionError("admission path is not a safe repository-relative path")
    return value


def _admissible(path: Path, *, root: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    relative = path.relative_to(root)
    if any(part in _EXCLUDED_PARTS for part in relative.parts):
        return False
    name = relative.name
    if name == ".DS_Store" or any(name.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES):
        return False
    if relative.as_posix() in {"frontend/vite.config.js", "frontend/vite.config.d.ts"}:
        return False
    return relative.parts[:3] != ("frontend", "src-tauri", "gen")


def _scope_paths(root: Path, patterns: Iterable[str]) -> tuple[Path, ...]:
    paths: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if _admissible(path, root=root):
                paths.add(path)
    return tuple(sorted(paths, key=lambda item: item.relative_to(root).as_posix()))


def _declared_scope(relative: str) -> str | None:
    prefixes = {
        "src/evidence_rag/": "backend",
        "tests/": "tests",
        "docs/": "documentation",
        "evals/": "evaluation",
        "frontend/": "frontend",
        "native/": "native",
        "plugins/": "plugins",
        "web/": "web",
        ".github/workflows/": "delivery",
        "artifacts/rag-maturity/g0/baseline-20260806-v1/": "g0_evidence",
        "artifacts/rag-maturity/g0/g0-engineering-20260806-v1/": "g0_evidence",
        "artifacts/wiki-rag/": "g0_evidence",
    }
    for prefix, scope in prefixes.items():
        if relative.startswith(prefix):
            return scope
    if relative in {
        ".gitignore",
        "Dockerfile",
        "Makefile",
        "README.md",
        "design-qa.md",
        "pyproject.toml",
        "uv.lock",
    }:
        return "delivery"
    return None


def _git_state_sets(root: Path) -> dict[str, frozenset[str]]:
    tracked = _git_lines(root, "ls-files")
    staged = _git_lines(root, "diff", "--cached", "--name-only")
    modified = _git_lines(root, "diff", "--name-only")
    conflicts = _git_lines(root, "diff", "--name-only", "--diff-filter=U")
    untracked = _git_lines(root, "ls-files", "--others", "--exclude-standard")
    return {
        "conflicts": conflicts,
        "modified": modified,
        "staged": staged,
        "tracked": tracked,
        "untracked": untracked,
    }


def _git_state(relative: str, states: Mapping[str, frozenset[str]]) -> str:
    if relative in states["untracked"] or relative not in states["tracked"]:
        return "untracked"
    staged = relative in states["staged"]
    modified = relative in states["modified"]
    if staged and modified:
        return "modified_and_staged"
    if staged:
        return "staged"
    if modified:
        return "modified"
    return "clean"


def _source_identity(files: Iterable[Mapping[str, Any]]) -> str:
    return _canonical_sha256(
        tuple(
            {
                "byte_length": item["byte_length"],
                "content_sha256": item["content_sha256"],
                "path": item["path"],
                "scope": item["scope"],
            }
            for item in files
        )
    )


def _scope_summaries(files: tuple[dict[str, Any], ...]) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for scope in _SCOPE_PATTERNS:
        scoped = tuple(item for item in files if item["scope"] == scope)
        summaries[scope] = {
            "content_sha256": _source_identity(scoped),
            "file_count": len(scoped),
            "modified_file_count": sum(
                item["git_state"] in {"modified", "modified_and_staged", "staged"}
                for item in scoped
            ),
            "total_bytes": sum(item["byte_length"] for item in scoped),
            "tracked_file_count": sum(item["git_state"] != "untracked" for item in scoped),
            "untracked_file_count": sum(item["git_state"] == "untracked" for item in scoped),
        }
    return summaries


def _policy_identity() -> str:
    return _canonical_sha256(
        {
            "excluded_parts": tuple(sorted(_EXCLUDED_PARTS)),
            "excluded_suffixes": tuple(sorted(_EXCLUDED_SUFFIXES)),
            "scope_patterns": _SCOPE_PATTERNS,
            "version": MATURITY_G0_ADMISSION_VERSION,
        }
    )


def _collect_files(root: Path) -> tuple[dict[str, Any], ...]:
    states = _git_state_sets(root)
    ownership: dict[str, str] = {}
    files: list[dict[str, Any]] = []
    for scope, patterns in _SCOPE_PATTERNS.items():
        for path in _scope_paths(root, patterns):
            relative = path.relative_to(root).as_posix()
            previous = ownership.setdefault(relative, scope)
            if previous != scope:
                raise MaturityG0AdmissionError(
                    f"admission path belongs to multiple scopes: {relative}"
                )
            if _declared_scope(relative) != scope:
                raise MaturityG0AdmissionError(
                    f"admission path does not match its declared scope: {relative}"
                )
            files.append(
                {
                    "byte_length": path.stat().st_size,
                    "content_sha256": _file_sha256(path),
                    "git_state": _git_state(relative, states),
                    "path": relative,
                    "scope": scope,
                }
            )
    return tuple(sorted(files, key=lambda item: item["path"]))


def build_admission_manifest(root: Path) -> dict[str, Any]:
    """Capture the exact candidate revision without staging or opening formal databases."""

    resolved = root.resolve()
    if not (resolved / ".git").exists():
        raise MaturityG0AdmissionError("admission root must be a Git worktree root")
    files = _collect_files(resolved)
    if not files:
        raise MaturityG0AdmissionError("admission source set is empty")
    state_counts = {
        state: sum(item["git_state"] == state for item in files) for state in sorted(_GIT_STATES)
    }
    scope_summaries = _scope_summaries(files)
    payload: dict[str, Any] = {
        "base_head": _git_value(resolved, "rev-parse", "HEAD"),
        "default_engine": "v1",
        "files": files,
        "formal_database_accessed": False,
        "owner_reviewed": False,
        "production_authorized": False,
        "remote_ci_observed": False,
        "schema_version": MATURITY_G0_ADMISSION_VERSION,
        "scope_policy_sha256": _policy_identity(),
        "scopes": scope_summaries,
        "source_snapshot_sha256": _source_identity(files),
        "state_counts": state_counts,
        "status": "VERSION_SCOPE_IDENTIFIED",
    }
    payload["content_sha256"] = _canonical_sha256(payload)
    return payload


def write_admission_manifest(root: Path, output: Path) -> dict[str, Any]:
    resolved_output = output.resolve()
    if resolved_output.exists():
        raise MaturityG0AdmissionError("admission output must not already exist")
    payload = build_admission_manifest(root)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_bytes(_canonical_json_bytes(payload) + b"\n")
    return payload


def _read_manifest(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaturityG0AdmissionError("admission manifest is not valid JSON") from exc
    if not isinstance(payload, dict) or raw != _canonical_json_bytes(payload) + b"\n":
        raise MaturityG0AdmissionError("admission manifest is not canonical JSON")
    return payload


def _validate_manifest(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if payload.get("schema_version") != MATURITY_G0_ADMISSION_VERSION:
        raise MaturityG0AdmissionError("admission manifest schema is unsupported")
    digest = payload.get("content_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "content_sha256"}
    if digest != _canonical_sha256(unsigned):
        raise MaturityG0AdmissionError("admission manifest content digest mismatch")
    if payload.get("scope_policy_sha256") != _policy_identity():
        raise MaturityG0AdmissionError("admission scope policy digest mismatch")
    if payload.get("formal_database_accessed") is not False:
        raise MaturityG0AdmissionError("admission manifest does not prove database isolation")
    if (
        payload.get("status") != "VERSION_SCOPE_IDENTIFIED"
        or payload.get("default_engine") != "v1"
        or payload.get("owner_reviewed") is not False
        or payload.get("remote_ci_observed") is not False
        or payload.get("production_authorized") is not False
        or not isinstance(payload.get("base_head"), str)
        or _GIT_HASH_RE.fullmatch(payload["base_head"]) is None
    ):
        raise MaturityG0AdmissionError("admission release boundary is invalid")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise MaturityG0AdmissionError("admission manifest file set is empty")
    files: list[dict[str, Any]] = []
    for raw_item in raw_files:
        if not isinstance(raw_item, dict) or set(raw_item) != {
            "byte_length",
            "content_sha256",
            "git_state",
            "path",
            "scope",
        }:
            raise MaturityG0AdmissionError("admission file entry contract mismatch")
        item = dict(raw_item)
        relative = _safe_relative(str(item["path"]))
        if (
            item["scope"] not in _SCOPE_PATTERNS
            or item["git_state"] not in _GIT_STATES
            or _declared_scope(relative) != item["scope"]
            or any(part in _EXCLUDED_PARTS for part in PurePosixPath(relative).parts)
            or any(relative.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES)
        ):
            raise MaturityG0AdmissionError("admission file scope or Git state is invalid")
        if (
            isinstance(item["byte_length"], bool)
            or not isinstance(item["byte_length"], int)
            or item["byte_length"] < 0
            or not isinstance(item["content_sha256"], str)
            or _SHA256_RE.fullmatch(item["content_sha256"]) is None
        ):
            raise MaturityG0AdmissionError("admission file size or digest is invalid")
        files.append(item)
    if tuple(item["path"] for item in files) != tuple(
        sorted({str(item["path"]) for item in files})
    ):
        raise MaturityG0AdmissionError("admission paths must be sorted and unique")
    normalized = tuple(files)
    if payload.get("source_snapshot_sha256") != _source_identity(normalized):
        raise MaturityG0AdmissionError("admission source snapshot digest mismatch")
    expected_counts = {
        state: sum(item["git_state"] == state for item in normalized)
        for state in sorted(_GIT_STATES)
    }
    if payload.get("state_counts") != expected_counts:
        raise MaturityG0AdmissionError("admission Git state denominator mismatch")
    if payload.get("scopes") != _scope_summaries(normalized):
        raise MaturityG0AdmissionError("admission scope denominator mismatch")
    return normalized


def verify_admission_manifest(
    path: Path,
    *,
    root: Path | None = None,
    require_clean: bool = False,
) -> dict[str, Any]:
    payload = _read_manifest(path)
    files = _validate_manifest(payload)
    current_clean: bool | None = None
    if root is not None:
        resolved = root.resolve()
        current = _collect_files(resolved)
        if tuple(
            (item["path"], item["scope"], item["byte_length"], item["content_sha256"])
            for item in current
        ) != tuple(
            (item["path"], item["scope"], item["byte_length"], item["content_sha256"])
            for item in files
        ):
            raise MaturityG0AdmissionError("current source set does not match admission manifest")
        states = _git_state_sets(resolved)
        current_clean = not any(
            states[name] for name in ("conflicts", "modified", "staged", "untracked")
        ) and all(item["git_state"] == "clean" for item in current)
        if require_clean and not current_clean:
            raise MaturityG0AdmissionError("current worktree is not clean for admission")
    return {
        "content_sha256": payload["content_sha256"],
        "current_source_verified": root is not None,
        "current_worktree_clean": current_clean,
        "file_count": len(files),
        "schema_version": MATURITY_G0_ADMISSION_VERSION,
        "source_snapshot_sha256": payload["source_snapshot_sha256"],
        "status": "verified-version-scope",
    }


def materialize_admission_snapshot(manifest: Path, root: Path, output: Path) -> dict[str, Any]:
    """Copy only manifest-owned source files into a new isolated directory."""

    resolved_root = root.resolve()
    resolved_output = output.resolve()
    if resolved_output.exists():
        raise MaturityG0AdmissionError("materialized output must not already exist")
    if resolved_output == resolved_root or resolved_root in resolved_output.parents:
        raise MaturityG0AdmissionError("materialized output must stay outside the source root")
    payload = _read_manifest(manifest)
    files = _validate_manifest(payload)
    verify_admission_manifest(manifest, root=resolved_root)
    resolved_output.mkdir(parents=True)
    try:
        for item in files:
            relative = Path(_safe_relative(str(item["path"])))
            source = resolved_root / relative
            if not _admissible(source, root=resolved_root) or not source.resolve().is_relative_to(
                resolved_root
            ):
                raise MaturityG0AdmissionError("materialized source path is unsafe")
            target = resolved_output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target, follow_symlinks=False)
            if _file_sha256(target) != item["content_sha256"]:
                raise MaturityG0AdmissionError("materialized source digest mismatch")
        receipt = {
            "file_count": len(files),
            "manifest_content_sha256": payload["content_sha256"],
            "schema_version": MATURITY_G0_ADMISSION_VERSION,
            "source_snapshot_sha256": payload["source_snapshot_sha256"],
            "status": "materialized-source-snapshot",
        }
        (resolved_output / ".rag-admission-source.json").write_bytes(
            _canonical_json_bytes(receipt) + b"\n"
        )
        return receipt
    except Exception:
        shutil.rmtree(resolved_output, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("output", type=Path)
    build.add_argument("--root", type=Path, default=Path.cwd())
    verify = commands.add_parser("verify")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--root", type=Path)
    verify.add_argument("--require-clean", action="store_true")
    materialize = commands.add_parser("materialize")
    materialize.add_argument("manifest", type=Path)
    materialize.add_argument("output", type=Path)
    materialize.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "build":
        result: Mapping[str, Any] = write_admission_manifest(arguments.root, arguments.output)
    elif arguments.command == "verify":
        result = verify_admission_manifest(
            arguments.manifest,
            root=arguments.root,
            require_clean=arguments.require_clean,
        )
    else:
        result = materialize_admission_snapshot(
            arguments.manifest,
            arguments.root,
            arguments.output,
        )
    print(_canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
