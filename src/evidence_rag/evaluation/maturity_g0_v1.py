"""Sanitized, content-addressed G0 workspace baseline inventory.

The inventory deliberately does not open the formal Evidence RAG or Wiki databases.
It records only repository-relative file identities, Git counts, fail-closed runtime
switches, and already-published Wiki artifact manifests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ..config import Settings

MATURITY_G0_BASELINE_VERSION = "rag-maturity-g0-baseline-v1"
_SHA256_PREFIX = "sha256:"
_SCOPES: dict[str, tuple[str, ...]] = {
    "backend": ("src/evidence_rag/**/*.py",),
    "tests": ("tests/**/*.py",),
    "documentation": ("docs/rag-optimization/**/*.md",),
    "delivery": (
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        "Makefile",
        "pyproject.toml",
        "uv.lock",
    ),
}


class MaturityG0BaselineError(RuntimeError):
    """The sanitized baseline could not be built or verified."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return _SHA256_PREFIX + hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return _SHA256_PREFIX + digest.hexdigest()


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise MaturityG0BaselineError(f"git command failed: {' '.join(arguments)}")
    return result.stdout


def _tracked_files(root: Path) -> frozenset[str]:
    return frozenset(item for item in _git(root, "ls-files").splitlines() if item)


def _scope_paths(root: Path, patterns: Iterable[str]) -> tuple[Path, ...]:
    paths: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts:
                paths.add(path)
    return tuple(sorted(paths, key=lambda item: item.relative_to(root).as_posix()))


def _scope_snapshot(
    root: Path,
    *,
    tracked: frozenset[str],
) -> dict[str, dict[str, Any]]:
    snapshots: dict[str, dict[str, Any]] = {}
    for name, patterns in _SCOPES.items():
        paths = _scope_paths(root, patterns)
        digest = hashlib.sha256()
        tracked_count = 0
        total_bytes = 0
        for path in paths:
            relative = path.relative_to(root).as_posix()
            payload = path.read_bytes()
            total_bytes += len(payload)
            tracked_count += int(relative in tracked)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(payload)
            digest.update(b"\0")
        snapshots[name] = {
            "content_sha256": _SHA256_PREFIX + digest.hexdigest(),
            "file_count": len(paths),
            "total_bytes": total_bytes,
            "tracked_file_count": tracked_count,
            "untracked_file_count": len(paths) - tracked_count,
        }
    return snapshots


def _worktree_snapshot(root: Path) -> dict[str, int]:
    modified_or_staged = 0
    untracked = 0
    conflicts = 0
    for line in _git(root, "status", "--porcelain=v1").splitlines():
        status = line[:2]
        if status == "??":
            untracked += 1
        else:
            modified_or_staged += 1
            if "U" in status or status in {"AA", "DD"}:
                conflicts += 1
    return {
        "conflict_count": conflicts,
        "modified_or_staged_count": modified_or_staged,
        "untracked_count": untracked,
    }


def _runtime_snapshot(root: Path) -> dict[str, Any]:
    try:
        settings = Settings.from_env(base_dir=root)
    except Exception as exc:
        return {
            "configuration_status": "unavailable",
            "error_type": type(exc).__name__,
        }
    return {
        "configuration_status": "available",
        "deployment_mode": settings.deployment_mode,
        "enforce_acl": settings.enforce_acl,
        "global_calibration_configured": bool(
            settings.rag_multisource_calibration_bundle
            and settings.rag_multisource_calibration_sha256
        ),
        "rag_code_context": settings.rag_code_context,
        "rag_code_dense_index": settings.rag_code_dense_index,
        "rag_code_engine": settings.rag_code_engine,
        "rag_code_graph": settings.rag_code_graph,
        "rag_code_reranker": settings.rag_code_reranker,
        "rag_code_unit_builder": settings.rag_code_unit_builder,
        "wiki_dense_configured": bool(
            settings.wiki_embedding_base_url and settings.wiki_embedding_model
        ),
        "wiki_external_inference_allowed": settings.wiki_external_inference_allowed,
        "wiki_reranker_configured": bool(
            settings.wiki_reranker_base_url and settings.wiki_reranker_model
        ),
    }


def _wiki_artifact_inventory(root: Path) -> tuple[dict[str, Any], ...]:
    inventory: list[dict[str, Any]] = []
    artifact_root = root / "artifacts" / "wiki-rag"
    if not artifact_root.is_dir():
        return ()
    for manifest_path in sorted(artifact_root.glob("*/*/manifest.json")):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            inventory.append(
                {
                    "manifest": manifest_path.relative_to(root).as_posix(),
                    "read_status": "invalid",
                    "error_type": type(exc).__name__,
                }
            )
            continue
        if not isinstance(payload, dict):
            inventory.append(
                {
                    "manifest": manifest_path.relative_to(root).as_posix(),
                    "read_status": "invalid",
                    "error_type": "NonObjectManifest",
                }
            )
            continue
        inventory.append(
            {
                "manifest": manifest_path.relative_to(root).as_posix(),
                "manifest_sha256": _file_sha256(manifest_path),
                "production_authorized": payload.get("production_authorized"),
                "quality_state": payload.get("quality_state"),
                "qualification": payload.get("qualification"),
                "read_status": "available",
                "schema_version": payload.get("schema_version"),
                "status": payload.get("status"),
            }
        )
    return tuple(inventory)


def build_baseline_inventory(root: Path) -> dict[str, Any]:
    """Build a sanitized snapshot without opening the formal application databases."""

    resolved = root.resolve()
    if not (resolved / ".git").exists():
        raise MaturityG0BaselineError("baseline root must be a Git worktree root")
    tracked = _tracked_files(resolved)
    payload: dict[str, Any] = {
        "schema_version": MATURITY_G0_BASELINE_VERSION,
        "status": "QUALITY_HOLD",
        "default_engine": "v1",
        "formal_database_accessed": False,
        "git": {
            "head": _git(resolved, "rev-parse", "HEAD").strip(),
            "worktree": _worktree_snapshot(resolved),
        },
        "runtime": _runtime_snapshot(resolved),
        "scopes": _scope_snapshot(resolved, tracked=tracked),
        "wiki_artifacts": _wiki_artifact_inventory(resolved),
    }
    payload["content_sha256"] = _canonical_sha256(payload)
    return payload


def write_baseline_inventory(root: Path, output: Path) -> dict[str, Any]:
    resolved_output = output.resolve()
    if resolved_output.exists():
        raise MaturityG0BaselineError("baseline output must not already exist")
    payload = build_baseline_inventory(root)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_bytes(_canonical_json_bytes(payload) + b"\n")
    return payload


def verify_baseline_inventory(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaturityG0BaselineError("baseline inventory is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise MaturityG0BaselineError("baseline inventory must be a JSON object")
    if raw != _canonical_json_bytes(payload) + b"\n":
        raise MaturityG0BaselineError("baseline inventory is not canonical JSON")
    if payload.get("schema_version") != MATURITY_G0_BASELINE_VERSION:
        raise MaturityG0BaselineError("baseline inventory schema is unsupported")
    digest = payload.get("content_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "content_sha256"}
    if digest != _canonical_sha256(unsigned):
        raise MaturityG0BaselineError("baseline inventory content digest mismatch")
    if payload.get("formal_database_accessed") is not False:
        raise MaturityG0BaselineError("baseline inventory does not prove formal database isolation")
    return {
        "content_sha256": digest,
        "default_engine": payload.get("default_engine"),
        "formal_database_accessed": False,
        "schema_version": MATURITY_G0_BASELINE_VERSION,
        "status": "verified-quality-hold-baseline",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("output", type=Path)
    build.add_argument("--root", type=Path, default=Path.cwd())
    verify = subparsers.add_parser("verify")
    verify.add_argument("inventory", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "build":
        result: Mapping[str, Any] = write_baseline_inventory(arguments.root, arguments.output)
    else:
        result = verify_baseline_inventory(arguments.inventory)
    print(_canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
