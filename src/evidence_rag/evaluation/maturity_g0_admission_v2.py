"""Build, verify, and materialize the refined G0 source-admission set.

V2 keeps source inputs, immutable evaluation anchors, and release evidence while
classifying generated output, unmanifested design images, database sidecars, and
sensitive paths as exclusions.  Evaluation SQLite anchors are hashed as opaque
bytes; this module never opens them as databases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .maturity_g0_admission_v1 import MaturityG0AdmissionError
from .maturity_g0_v1 import _canonical_json_bytes, _canonical_sha256

MATURITY_G0_ADMISSION_VERSION = "rag-maturity-g0-admission-v2"
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
_CACHE_SUFFIXES = frozenset({".pyc", ".tsbuildinfo"})
_DATABASE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
_SIDECAR_SUFFIXES = frozenset({".db-shm", ".db-wal", ".sqlite-shm", ".sqlite-wal", ".sqlite3-shm", ".sqlite3-wal", ".wal"})
_SENSITIVE_SUFFIXES = frozenset({".jks", ".key", ".keystore", ".mobileprovision", ".p12", ".pem"})
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "service-account.json",
    }
)
_GENERATED_EXACT_PATHS = frozenset(
    {
        "frontend/vite.config.d.ts",
        "frontend/vite.config.js",
        "web/.frontend-source.sha256",
        "web/build-contract.json",
        "web/index.html",
    }
)
_GENERATED_PREFIXES = ("frontend/src-tauri/gen/", "web/app/")
_DOCUMENT_ASSET_SUFFIXES = frozenset({".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"})
_HISTORY_FIXTURE_PATHS = {
    "bundle": "tests/fixtures/g0_history/code-golden-v2-history.bundle",
    "cleanroom": "tests/fixtures/g0_history/cleanroom.py",
    "generator": "tests/fixtures/g0_history/build_code_golden_v2_bundle.py",
    "manifest": "tests/fixtures/g0_history/code-golden-v2-history.json",
}
_EVALUATION_SQLITE_RE = re.compile(
    r"^evals/[a-z0-9_-]+/runs/[a-zA-Z0-9_-]+/evaluation\.sqlite3$"
)
_SCOPE_PATTERNS: dict[str, tuple[str, ...]] = {
    "backend": ("src/evidence_rag/**/*",),
    "tests": ("tests/**/*",),
    "documentation": ("docs/**/*",),
    "evaluation": ("evals/**/*",),
    "frontend": ("frontend/**/*", "frontend/.npmrc"),
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
_REASONS = frozenset(
    {
        "cache",
        "database_or_unapproved_sqlite",
        "database_sidecar",
        "generated_build_output",
        "sensitive_path",
        "symlink",
        "unmanifested_document_asset",
    }
)
_PORTABLE_EXCLUSION_REASONS = frozenset({"cache", "generated_build_output"})


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _git_paths(root: Path, *arguments: str) -> frozenset[str]:
    result = subprocess.run(
        ("git", "-C", str(root), *arguments, "-z"),
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise MaturityG0AdmissionError(f"git command failed: {' '.join(arguments)}")
    try:
        return frozenset(
            item.decode("utf-8", errors="surrogateescape")
            for item in result.stdout.split(b"\0")
            if item
        )
    except UnicodeError as exc:  # pragma: no cover - surrogateescape is defensive
        raise MaturityG0AdmissionError("Git returned an undecodable repository path") from exc


def _git_value(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or len(result.stdout.splitlines()) != 1:
        raise MaturityG0AdmissionError(f"git command returned no unique value: {' '.join(arguments)}")
    return result.stdout.strip()


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or path.is_absolute()
        or ".." in path.parts
        or value != path.as_posix()
        or any(part in {"", "."} for part in path.parts)
    ):
        raise MaturityG0AdmissionError("admission path is not a safe repository-relative path")
    return value


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


def _role(relative: str, scope: str) -> str:
    if scope == "evaluation":
        return "evaluation_anchor" if "/runs/" in relative or "/corrections/" in relative else "evaluation_definition"
    return {
        "backend": "runtime_source",
        "delivery": "delivery_source",
        "documentation": "documentation",
        "frontend": "frontend_source",
        "g0_evidence": "release_evidence",
        "native": "native_source",
        "plugins": "plugin_source",
        "tests": "test_source",
        "web": "legacy_web_source",
    }[scope]


def _static_exclusion_reason(relative: str) -> str | None:
    pure = PurePosixPath(relative)
    if any(part in _EXCLUDED_PARTS for part in pure.parts):
        return "cache"
    name = pure.name
    lower_name = name.lower()
    if name == ".DS_Store" or any(lower_name.endswith(suffix) for suffix in _CACHE_SUFFIXES):
        return "cache"
    if relative != "frontend/.npmrc" and (
        lower_name in _SENSITIVE_NAMES
        or lower_name.startswith(".env.")
        or any(lower_name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)
    ):
        return "sensitive_path"
    if relative in _GENERATED_EXACT_PATHS or relative.startswith(_GENERATED_PREFIXES):
        return "generated_build_output"
    if (
        relative.startswith("docs/design/")
        and PurePosixPath(lower_name).suffix in _DOCUMENT_ASSET_SUFFIXES
    ):
        return "unmanifested_document_asset"
    if any(lower_name.endswith(suffix) for suffix in _SIDECAR_SUFFIXES):
        return "database_sidecar"
    if any(lower_name.endswith(suffix) for suffix in _DATABASE_SUFFIXES):
        if _EVALUATION_SQLITE_RE.fullmatch(relative) is not None:
            return None
        return "database_or_unapproved_sqlite"
    return None


def _exclusion_reason(path: Path, *, root: Path) -> str | None:
    relative = path.relative_to(root).as_posix()
    static_reason = _static_exclusion_reason(relative)
    if static_reason is not None:
        return static_reason
    if path.is_symlink():
        return "symlink"
    return None


def _git_state_sets(root: Path) -> dict[str, frozenset[str]]:
    return {
        "conflicts": _git_paths(root, "diff", "--name-only", "--diff-filter=U"),
        "modified": _git_paths(root, "diff", "--name-only"),
        "staged": _git_paths(root, "diff", "--cached", "--name-only"),
        "tracked": _git_paths(root, "ls-files"),
        "untracked": _git_paths(root, "ls-files", "--others", "--exclude-standard"),
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


def _scope_candidates(root: Path, patterns: Iterable[str]) -> tuple[Path, ...]:
    paths: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() or path.is_symlink():
                paths.add(path)
    return tuple(sorted(paths, key=lambda item: item.relative_to(root).as_posix()))


def _source_identity(files: Iterable[Mapping[str, Any]]) -> str:
    return _canonical_sha256(
        tuple(
            {
                "byte_length": item["byte_length"],
                "content_sha256": item["content_sha256"],
                "path": item["path"],
                "role": item["role"],
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
            "role_counts": dict(sorted(Counter(item["role"] for item in scoped).items())),
            "total_bytes": sum(item["byte_length"] for item in scoped),
            "tracked_file_count": sum(item["git_state"] != "untracked" for item in scoped),
            "untracked_file_count": sum(item["git_state"] == "untracked" for item in scoped),
        }
    return summaries


def _history_fixture_policy(files: tuple[dict[str, Any], ...]) -> dict[str, Any] | None:
    by_path = {str(item["path"]): item for item in files}
    present = {
        name: by_path[path]
        for name, path in _HISTORY_FIXTURE_PATHS.items()
        if path in by_path
    }
    if not present:
        return None
    if set(present) != set(_HISTORY_FIXTURE_PATHS):
        raise MaturityG0AdmissionError("self-contained history fixture is only partially admitted")
    if any(item["scope"] != "tests" or item["role"] != "test_source" for item in present.values()):
        raise MaturityG0AdmissionError("self-contained history fixture has an invalid admission role")
    return {
        "complete_bundle_required": True,
        "files": {
            name: {
                "byte_length": item["byte_length"],
                "content_sha256": item["content_sha256"],
                "path": item["path"],
            }
            for name, item in sorted(present.items())
        },
        "network_required": False,
        "ordinary_clone_transfer_required": True,
        "security_rescan_required": True,
        "status": "SELF_CONTAINED_HISTORY_DECLARED",
    }


def _findings(excluded: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    definitions = (
        ("EVALUATION_SIDECAR_EXCLUDED", "database_sidecar", "info", False),
        ("GENERATED_OUTPUT_EXCLUDED", "generated_build_output", "info", False),
        ("SENSITIVE_PATH_EXCLUDED", "sensitive_path", "warning", False),
        ("UNMANIFESTED_DOCUMENT_ASSET_EXCLUDED", "unmanifested_document_asset", "warning", False),
    )
    findings: list[dict[str, Any]] = []
    for code, reason, severity, blocks_release in definitions:
        paths = [item["path"] for item in excluded if item["reason"] == reason]
        if paths:
            findings.append(
                {
                    "blocks_release": blocks_release,
                    "code": code,
                    "count": len(paths),
                    "paths": paths,
                    "severity": severity,
                }
            )
    tracked_runtime = [
        item["path"]
        for item in excluded
        if item["reason"] == "generated_build_output" and item["git_state"] != "untracked"
    ]
    if tracked_runtime:
        findings.append(
            {
                "blocks_release": True,
                "code": "TRACKED_RUNTIME_OUTPUT_OUTSIDE_SOURCE_SET",
                "count": len(tracked_runtime),
                "paths": tracked_runtime,
                "severity": "error",
            }
        )
    tracked_unsafe = [
        item["path"]
        for item in excluded
        if item["reason"] in {"sensitive_path", "symlink"}
        and item["git_state"] != "untracked"
    ]
    if tracked_unsafe:
        findings.append(
            {
                "blocks_release": True,
                "code": "TRACKED_UNSAFE_PATH_EXCLUDED",
                "count": len(tracked_unsafe),
                "paths": tracked_unsafe,
                "severity": "error",
            }
        )
    return sorted(findings, key=lambda item: item["code"])


def _policy_identity() -> str:
    return _canonical_sha256(
        {
            "cache_suffixes": tuple(sorted(_CACHE_SUFFIXES)),
            "database_suffixes": tuple(sorted(_DATABASE_SUFFIXES)),
            "document_asset_suffixes": tuple(sorted(_DOCUMENT_ASSET_SUFFIXES)),
            "evaluation_sqlite_pattern": _EVALUATION_SQLITE_RE.pattern,
            "excluded_parts": tuple(sorted(_EXCLUDED_PARTS)),
            "generated_exact_paths": tuple(sorted(_GENERATED_EXACT_PATHS)),
            "generated_prefixes": _GENERATED_PREFIXES,
            "scope_patterns": _SCOPE_PATTERNS,
            "sensitive_names": tuple(sorted(_SENSITIVE_NAMES)),
            "sensitive_suffixes": tuple(sorted(_SENSITIVE_SUFFIXES)),
            "sidecar_suffixes": tuple(sorted(_SIDECAR_SUFFIXES)),
            "version": MATURITY_G0_ADMISSION_VERSION,
        }
    )


def _collect(
    root: Path,
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    dict[str, int],
]:
    states = _git_state_sets(root)
    ownership: dict[str, str] = {}
    files: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    exclusion_counts: Counter[str] = Counter()
    for scope, patterns in _SCOPE_PATTERNS.items():
        for path in _scope_candidates(root, patterns):
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
            state = _git_state(relative, states)
            reason = _exclusion_reason(path, root=root)
            if reason is not None:
                exclusion_counts[reason] += 1
                # Cache trees can contain tens of thousands of deterministic files.
                # Their full path set adds noise without changing an admission decision.
                if reason != "cache":
                    excluded.append(
                        {
                            "git_state": state,
                            "path": relative,
                            "reason": reason,
                            "scope": scope,
                        }
                    )
                continue
            files.append(
                {
                    "byte_length": path.stat().st_size,
                    "content_sha256": _file_sha256(path),
                    "git_state": state,
                    "path": relative,
                    "role": _role(relative, scope),
                    "scope": scope,
                }
            )
    return (
        tuple(sorted(files, key=lambda item: item["path"])),
        tuple(sorted(excluded, key=lambda item: item["path"])),
        dict(sorted(exclusion_counts.items())),
    )


def build_admission_manifest(root: Path) -> dict[str, Any]:
    """Capture a refined candidate without changing Git or opening any database."""

    resolved = root.resolve()
    if not (resolved / ".git").exists():
        raise MaturityG0AdmissionError("admission root must be a Git worktree root")
    files, excluded, exclusion_counts = _collect(resolved)
    if not files:
        raise MaturityG0AdmissionError("admission source set is empty")
    findings = _findings(excluded)
    state_counts = {
        state: sum(item["git_state"] == state for item in files) for state in sorted(_GIT_STATES)
    }
    payload: dict[str, Any] = {
        "base_head": _git_value(resolved, "rev-parse", "HEAD"),
        "database_policy": {
            "evaluation_sqlite_anchors_hashed_as_opaque_bytes": sum(
                item["path"].endswith("/evaluation.sqlite3") for item in files
            ),
            "formal_application_databases_included": False,
            "formal_database_opened": False,
            "sidecars_included": False,
        },
        "default_engine": "v1",
        "exclusion_counts": exclusion_counts,
        "excluded_files": excluded,
        "files": files,
        "findings": findings,
        "history_fixture_policy": _history_fixture_policy(files),
        "owner_reviewed": False,
        "production_authorized": False,
        "release_blocker_count": sum(item["blocks_release"] for item in findings),
        "remote_ci_observed": False,
        "schema_version": MATURITY_G0_ADMISSION_VERSION,
        "scope_policy_sha256": _policy_identity(),
        "scopes": _scope_summaries(files),
        "source_snapshot_sha256": _source_identity(files),
        "state_counts": state_counts,
        "status": "VERSION_SCOPE_REFINED",
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


def _validate_files(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
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
            "role",
            "scope",
        }:
            raise MaturityG0AdmissionError("admission file entry contract mismatch")
        item = dict(raw_item)
        relative = _safe_relative(str(item["path"]))
        scope = item["scope"]
        if (
            scope not in _SCOPE_PATTERNS
            or item["git_state"] not in _GIT_STATES
            or _declared_scope(relative) != scope
            or _static_exclusion_reason(relative) is not None
            or item["role"] != _role(relative, scope)
        ):
            raise MaturityG0AdmissionError("admission file scope, role, or Git state is invalid")
        if (
            isinstance(item["byte_length"], bool)
            or not isinstance(item["byte_length"], int)
            or item["byte_length"] < 0
            or not isinstance(item["content_sha256"], str)
            or _SHA256_RE.fullmatch(item["content_sha256"]) is None
        ):
            raise MaturityG0AdmissionError("admission file size or digest is invalid")
        files.append(item)
    if tuple(item["path"] for item in files) != tuple(sorted({item["path"] for item in files})):
        raise MaturityG0AdmissionError("admission paths must be sorted and unique")
    return tuple(files)


def _validate_excluded(payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_excluded = payload.get("excluded_files")
    if not isinstance(raw_excluded, list):
        raise MaturityG0AdmissionError("admission exclusion audit is missing")
    excluded: list[dict[str, Any]] = []
    for raw_item in raw_excluded:
        if not isinstance(raw_item, dict) or set(raw_item) != {
            "git_state",
            "path",
            "reason",
            "scope",
        }:
            raise MaturityG0AdmissionError("admission exclusion entry contract mismatch")
        item = dict(raw_item)
        relative = _safe_relative(str(item["path"]))
        if (
            item["scope"] not in _SCOPE_PATTERNS
            or _declared_scope(relative) != item["scope"]
            or item["git_state"] not in _GIT_STATES
            or item["reason"] not in _REASONS
        ):
            raise MaturityG0AdmissionError("admission exclusion entry is invalid")
        expected_reason = _static_exclusion_reason(relative)
        if item["reason"] != "symlink" and item["reason"] != expected_reason:
            raise MaturityG0AdmissionError("admission exclusion does not match policy")
        excluded.append(item)
    if tuple(item["path"] for item in excluded) != tuple(
        sorted({item["path"] for item in excluded})
    ):
        raise MaturityG0AdmissionError("admission exclusion paths must be sorted and unique")
    return tuple(excluded)


def _validate_exclusion_counts(
    payload: Mapping[str, Any], excluded: tuple[dict[str, Any], ...]
) -> dict[str, int]:
    raw_counts = payload.get("exclusion_counts")
    if not isinstance(raw_counts, dict) or any(
        reason not in _REASONS
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        for reason, count in raw_counts.items()
    ):
        raise MaturityG0AdmissionError("admission exclusion denominator is invalid")
    counts = dict(sorted(raw_counts.items()))
    listed_counts = Counter(item["reason"] for item in excluded)
    if any(counts.get(reason, 0) != count for reason, count in listed_counts.items()):
        raise MaturityG0AdmissionError("listed exclusions do not match their denominators")
    if any(reason != "cache" and counts.get(reason, 0) != listed_counts.get(reason, 0) for reason in counts):
        raise MaturityG0AdmissionError("admission exclusion denominator is not fully listed")
    return counts


def _validate_manifest(
    payload: dict[str, Any],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    if payload.get("schema_version") != MATURITY_G0_ADMISSION_VERSION:
        raise MaturityG0AdmissionError("admission manifest schema is unsupported")
    digest = payload.get("content_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "content_sha256"}
    if digest != _canonical_sha256(unsigned):
        raise MaturityG0AdmissionError("admission manifest content digest mismatch")
    if payload.get("scope_policy_sha256") != _policy_identity():
        raise MaturityG0AdmissionError("admission scope policy digest mismatch")
    if (
        payload.get("status") != "VERSION_SCOPE_REFINED"
        or payload.get("default_engine") != "v1"
        or payload.get("owner_reviewed") is not False
        or payload.get("remote_ci_observed") is not False
        or payload.get("production_authorized") is not False
        or not isinstance(payload.get("base_head"), str)
        or _GIT_HASH_RE.fullmatch(payload["base_head"]) is None
    ):
        raise MaturityG0AdmissionError("admission release boundary is invalid")
    files = _validate_files(payload)
    excluded = _validate_excluded(payload)
    _validate_exclusion_counts(payload, excluded)
    if set(item["path"] for item in files) & set(item["path"] for item in excluded):
        raise MaturityG0AdmissionError("admitted and excluded paths overlap")
    if payload.get("source_snapshot_sha256") != _source_identity(files):
        raise MaturityG0AdmissionError("admission source snapshot digest mismatch")
    expected_counts = {
        state: sum(item["git_state"] == state for item in files) for state in sorted(_GIT_STATES)
    }
    if payload.get("state_counts") != expected_counts:
        raise MaturityG0AdmissionError("admission Git state denominator mismatch")
    if payload.get("scopes") != _scope_summaries(files):
        raise MaturityG0AdmissionError("admission scope denominator mismatch")
    if payload.get("history_fixture_policy") != _history_fixture_policy(files):
        raise MaturityG0AdmissionError("admission history fixture policy is invalid")
    findings = _findings(excluded)
    if payload.get("findings") != findings or payload.get("release_blocker_count") != sum(
        item["blocks_release"] for item in findings
    ):
        raise MaturityG0AdmissionError("admission findings do not match exclusions")
    expected_database_policy = {
        "evaluation_sqlite_anchors_hashed_as_opaque_bytes": sum(
            item["path"].endswith("/evaluation.sqlite3") for item in files
        ),
        "formal_application_databases_included": False,
        "formal_database_opened": False,
        "sidecars_included": False,
    }
    if payload.get("database_policy") != expected_database_policy:
        raise MaturityG0AdmissionError("admission database boundary is invalid")
    return files, excluded


def verify_admission_manifest(
    path: Path,
    *,
    root: Path | None = None,
    require_clean: bool = False,
    require_release_ready: bool = False,
) -> dict[str, Any]:
    payload = _read_manifest(path)
    files, excluded = _validate_manifest(payload)
    current_clean: bool | None = None
    current_exclusions_approved: bool | None = None
    current_exclusions_match: bool | None = None
    current_release_ready: bool | None = None
    current_findings: list[dict[str, Any]] | None = None
    current_unapproved_exclusion_addition_count: int | None = None
    if root is not None:
        resolved = root.resolve()
        current, current_excluded, current_exclusion_counts = _collect(resolved)
        if tuple(
            (item["path"], item["role"], item["scope"], item["byte_length"], item["content_sha256"])
            for item in current
        ) != tuple(
            (item["path"], item["role"], item["scope"], item["byte_length"], item["content_sha256"])
            for item in files
        ):
            raise MaturityG0AdmissionError("current source set does not match admission manifest")
        current_exclusion_set = {
            (item["path"], item["reason"], item["scope"]) for item in current_excluded
        }
        admitted_exclusion_set = {
            (item["path"], item["reason"], item["scope"]) for item in excluded
        }
        current_exclusions_match = (
            current_exclusion_set == admitted_exclusion_set
            and current_exclusion_counts == payload["exclusion_counts"]
        )
        unapproved_exclusion_additions = {
            item
            for item in current_exclusion_set - admitted_exclusion_set
            if item[1] not in _PORTABLE_EXCLUSION_REASONS
        }
        current_unapproved_exclusion_addition_count = len(
            unapproved_exclusion_additions
        )
        current_exclusions_approved = not unapproved_exclusion_additions
        states = _git_state_sets(resolved)
        current_clean = not any(
            states[name] for name in ("conflicts", "modified", "staged", "untracked")
        ) and all(item["git_state"] == "clean" for item in current)
        current_findings = _findings(current_excluded)
        current_release_ready = (
            current_clean
            and current_exclusions_approved
            and not any(item["blocks_release"] for item in current_findings)
        )
        if require_clean and not current_clean:
            raise MaturityG0AdmissionError("current worktree is not clean for admission")
        if require_release_ready and not current_release_ready:
            raise MaturityG0AdmissionError(
                "current worktree is not clean and release-ready for admission"
            )
    return {
        "content_sha256": payload["content_sha256"],
        "current_exclusions_approved": current_exclusions_approved,
        "current_exclusions_match": current_exclusions_match,
        "current_release_ready": current_release_ready,
        "current_source_verified": root is not None,
        "current_worktree_clean": current_clean,
        "current_unapproved_exclusion_addition_count": (
            current_unapproved_exclusion_addition_count
        ),
        "excluded_file_count": sum(payload["exclusion_counts"].values()),
        "listed_excluded_file_count": len(excluded),
        "file_count": len(files),
        "release_blocker_count": (
            None
            if current_findings is None
            else sum(item["blocks_release"] for item in current_findings)
        ),
        "schema_version": MATURITY_G0_ADMISSION_VERSION,
        "source_snapshot_sha256": payload["source_snapshot_sha256"],
        "status": "verified-version-scope",
    }


def materialize_admission_snapshot(manifest: Path, root: Path, output: Path) -> dict[str, Any]:
    """Copy only admitted bytes into a new directory outside the source root."""

    resolved_root = root.resolve()
    resolved_output = output.resolve()
    if resolved_output.exists():
        raise MaturityG0AdmissionError("materialized output must not already exist")
    if resolved_output == resolved_root or resolved_root in resolved_output.parents:
        raise MaturityG0AdmissionError("materialized output must stay outside the source root")
    payload = _read_manifest(manifest)
    files, _ = _validate_manifest(payload)
    verify_admission_manifest(manifest, root=resolved_root)
    resolved_output.mkdir(parents=True)
    try:
        for item in files:
            relative = Path(_safe_relative(str(item["path"])))
            source = resolved_root / relative
            if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(
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


def _summary(payload: Mapping[str, Any], *, output: Path | None = None) -> dict[str, Any]:
    result = {
        key: payload[key]
        for key in (
            "content_sha256",
            "schema_version",
            "source_snapshot_sha256",
            "status",
        )
        if key in payload
    }
    if "files" in payload:
        result["file_count"] = len(payload["files"])
        result["excluded_file_count"] = sum(payload["exclusion_counts"].values())
        result["listed_excluded_file_count"] = len(payload["excluded_files"])
        result["release_blocker_count"] = payload["release_blocker_count"]
    if output is not None:
        result["output"] = str(output)
    return result


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
    verify.add_argument("--require-release-ready", action="store_true")
    materialize = commands.add_parser("materialize")
    materialize.add_argument("manifest", type=Path)
    materialize.add_argument("output", type=Path)
    materialize.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "build":
        payload = write_admission_manifest(arguments.root, arguments.output)
        result: Mapping[str, Any] = _summary(payload, output=arguments.output)
    elif arguments.command == "verify":
        result = verify_admission_manifest(
            arguments.manifest,
            root=arguments.root,
            require_clean=arguments.require_clean,
            require_release_ready=arguments.require_release_ready,
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
