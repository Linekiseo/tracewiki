from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evidence_rag.security import secret_findings

SCHEMA_VERSION = "code-golden-v2-history-bundle-v1"
FIXED_COMMIT = "bc3326edc761e3bdb42ed78726a21f314ab44974"
FIXED_TREE = "2fe240ebdf1f3d04c252c32b505ca923394171da"
SOURCE_REF = "refs/heads/code-golden-v2-source"
TARGET_REF = "refs/heads/code-golden-v2-history"
SELECTED_PATHS = (
    "src/evidence_rag/code_history/service.py",
    "src/evidence_rag/ingestion.py",
    "src/evidence_rag/repository.py",
)
EXPECTED_SECRET_FINDINGS = {
    "tests/conftest.py": {"github_token"},
    "tests/test_codex_sessions.py": {
        "generic_api_key",
        "github_token",
        "openai_api_key",
        "private_key",
    },
    "tests/test_ingestion_retrieval.py": {"github_token"},
}
SENSITIVE_NAMED_PATHS = (".env.example",)


class HistoryBundleBuildError(RuntimeError):
    pass


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _git(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise HistoryBundleBuildError(
            f"git {' '.join(arguments)} failed with exit code {result.returncode}"
        )
    return result.stdout


def _reachable_objects(root: Path) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    raw_rows = _git(root, "rev-list", "--objects", FIXED_COMMIT).decode(
        "utf-8", errors="surrogateescape"
    )
    objects: list[dict[str, Any]] = []
    findings: dict[str, set[str]] = defaultdict(set)
    seen: set[str] = set()
    for raw_row in raw_rows.splitlines():
        oid, *path_part = raw_row.split(" ", 1)
        if oid in seen:
            continue
        seen.add(oid)
        object_type, raw_size = (
            _git(root, "cat-file", "--batch-check=%(objecttype) %(objectsize)", input_bytes=(oid + "\n").encode())
            .decode()
            .strip()
            .split()
        )
        path = path_part[0] if path_part else None
        record: dict[str, Any] = {
            "byte_length": int(raw_size),
            "object_type": object_type,
            "oid": oid,
            "path": path,
        }
        objects.append(record)
        if object_type != "blob":
            continue
        content = _git(root, "cat-file", "blob", oid)
        if b"\x00" in content:
            record["content_kind"] = "binary"
            record["secret_categories"] = []
            continue
        record["content_kind"] = "text"
        categories = secret_findings(content.decode("utf-8", errors="replace"))
        record["secret_categories"] = sorted(categories)
        if categories:
            findings[path or "<unmapped>"].update(categories)
    return objects, findings


def _security_summary(objects: list[dict[str, Any]], findings: dict[str, set[str]]) -> dict[str, Any]:
    observed = {path: set(categories) for path, categories in findings.items()}
    if observed != EXPECTED_SECRET_FINDINGS:
        raise HistoryBundleBuildError(
            "history credential scan differs from the reviewed synthetic-fixture allowlist"
        )
    path_set = {str(item["path"]) for item in objects if item["path"]}
    if any(path not in path_set for path in SENSITIVE_NAMED_PATHS):
        raise HistoryBundleBuildError("reviewed sensitive-named history path is missing")
    blob_records = [item for item in objects if item["object_type"] == "blob"]
    binary_count = sum(item.get("content_kind") == "binary" for item in blob_records)
    text_count = sum(item.get("content_kind") == "text" for item in blob_records)
    finding_blob_count = sum(bool(item.get("secret_categories")) for item in blob_records)
    return {
        "finding_blob_count": finding_blob_count,
        "findings": [
            {
                "categories": sorted(categories),
                "disposition": "reviewed synthetic redaction/secret-detection fixture literal",
                "path": path,
            }
            for path, categories in sorted(observed.items())
        ],
        "high_confidence_scanner": "evidence_rag.security.secret_findings-v1",
        "reviewed_sensitive_named_paths": [
            {
                "disposition": "environment template with no high-confidence credential",
                "path": path,
            }
            for path in SENSITIVE_NAMED_PATHS
        ],
        "status": "EXPECTED_SYNTHETIC_FIXTURE_LITERALS_ONLY",
        "text_blob_count": text_count,
        "binary_blob_count": binary_count,
    }


def build_bundle(root: Path, bundle_path: Path, manifest_path: Path) -> dict[str, Any]:
    root = root.resolve()
    bundle_path = bundle_path.resolve()
    manifest_path = manifest_path.resolve()
    if bundle_path.exists() or manifest_path.exists():
        raise HistoryBundleBuildError("history bundle outputs must not already exist")
    if _git(root, "rev-parse", FIXED_COMMIT).decode().strip() != FIXED_COMMIT:
        raise HistoryBundleBuildError("frozen Code Golden commit is unavailable")
    if _git(root, "rev-parse", f"{FIXED_COMMIT}^{{tree}}").decode().strip() != FIXED_TREE:
        raise HistoryBundleBuildError("frozen Code Golden tree differs from the reviewed identity")

    objects, findings = _reachable_objects(root)
    type_counts = Counter(str(item["object_type"]) for item in objects)
    security = _security_summary(objects, findings)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="code-golden-history-build-") as temporary:
        staging = Path(temporary) / "repository.git"
        init = subprocess.run(
            ["git", "init", "--bare", "--quiet", str(staging)],
            capture_output=True,
            check=False,
        )
        if init.returncode:
            raise HistoryBundleBuildError("unable to initialize temporary history repository")
        _git(staging, "fetch", "--no-tags", str(root), FIXED_COMMIT)
        _git(staging, "update-ref", SOURCE_REF, FIXED_COMMIT)
        _git(staging, "bundle", "create", str(bundle_path), SOURCE_REF)
    bundle = bundle_path.read_bytes()
    if not bundle:
        raise HistoryBundleBuildError("generated history bundle is empty")
    manifest = {
        "bundle": {
            "byte_length": len(bundle),
            "content_sha256": _sha256_bytes(bundle),
            "file": bundle_path.name,
            "source_ref": SOURCE_REF,
            "target_ref": TARGET_REF,
        },
        "complete_history": True,
        "fixed_commit": FIXED_COMMIT,
        "fixed_tree": FIXED_TREE,
        "generation": {
            "command": (
                "temporary bare fetch <fixed-commit>; git bundle create <output> "
                "refs/heads/code-golden-v2-source"
            ),
            "generator": Path(__file__).name,
            "network_required": False,
        },
        "object_counts": dict(sorted(type_counts.items())),
        "reachable_object_count": len(objects),
        "schema_version": SCHEMA_VERSION,
        "security_scan": security,
        "selected_sparse_paths": list(SELECTED_PATHS),
        "status": "CONTENT_ADDRESSED_COMPLETE_HISTORY",
    }
    manifest_path.write_bytes(_canonical_json_bytes(manifest) + b"\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the frozen Code Golden V2 Git history bundle.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    arguments = parser.parse_args()
    payload = build_bundle(arguments.root, arguments.bundle, arguments.manifest)
    print(
        json.dumps(
            {
                "bundle": payload["bundle"],
                "reachable_object_count": payload["reachable_object_count"],
                "schema_version": payload["schema_version"],
                "security_status": payload["security_scan"]["status"],
                "status": payload["status"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
