from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evidence_rag.evaluation.maturity_g0_admission_v2 import verify_admission_manifest
from evidence_rag.security import secret_findings

CLEANROOM_SCHEMA_VERSION = "rag-maturity-g0-cleanroom-v1"
HISTORY_SCHEMA_VERSION = "code-golden-v2-history-bundle-v1"
RECEIPT_NAME = ".rag-admission-source.json"
HISTORY_DIRECTORY = Path("tests/fixtures/g0_history")
HISTORY_MANIFEST = HISTORY_DIRECTORY / "code-golden-v2-history.json"
SYNTHETIC_BRANCH = "refs/heads/admission-source"
SYNTHETIC_TIMESTAMP = "2026-08-06T00:00:00Z"


class CleanroomError(RuntimeError):
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


def _read_canonical_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CleanroomError(f"cleanroom JSON is unreadable: {path.name}") from exc
    if not isinstance(payload, dict) or raw != _canonical_json_bytes(payload) + b"\n":
        raise CleanroomError(f"cleanroom JSON is not canonical: {path.name}")
    return payload


def _git(
    root: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    env = os.environ.copy()
    if environment:
        env.update(environment)
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        input=input_bytes,
        capture_output=True,
        check=False,
        env=env,
    )
    if result.returncode:
        raise CleanroomError(
            f"cleanroom Git command failed ({result.returncode}): {' '.join(arguments)}"
        )
    return result.stdout


def _validate_receipt(root: Path, admission: Mapping[str, Any]) -> dict[str, Any]:
    receipt = _read_canonical_json(root / RECEIPT_NAME)
    expected = {
        "file_count": len(admission["files"]),
        "manifest_content_sha256": admission["content_sha256"],
        "schema_version": admission["schema_version"],
        "source_snapshot_sha256": admission["source_snapshot_sha256"],
        "status": "materialized-source-snapshot",
    }
    if receipt != expected:
        raise CleanroomError("materialized source receipt does not match admission manifest")
    return receipt


def _validate_history_manifest(root: Path, admission: Mapping[str, Any]) -> dict[str, Any]:
    history_path = root / HISTORY_MANIFEST
    history = _read_canonical_json(history_path)
    if set(history) != {
        "bundle",
        "complete_history",
        "fixed_commit",
        "fixed_tree",
        "generation",
        "object_counts",
        "reachable_object_count",
        "schema_version",
        "security_scan",
        "selected_sparse_paths",
        "status",
    }:
        raise CleanroomError("history manifest field contract is invalid")
    if (
        history["schema_version"] != HISTORY_SCHEMA_VERSION
        or history["complete_history"] is not True
        or history["status"] != "CONTENT_ADDRESSED_COMPLETE_HISTORY"
        or history["generation"].get("network_required") is not False
    ):
        raise CleanroomError("history manifest release boundary is invalid")
    bundle_record = history.get("bundle")
    if not isinstance(bundle_record, dict) or set(bundle_record) != {
        "byte_length",
        "content_sha256",
        "file",
        "source_ref",
        "target_ref",
    }:
        raise CleanroomError("history bundle record is invalid")
    bundle_relative = HISTORY_DIRECTORY / str(bundle_record["file"])
    admitted = {str(item["path"]): item for item in admission["files"]}
    for relative in (HISTORY_MANIFEST, bundle_relative):
        record = admitted.get(relative.as_posix())
        path = root / relative
        if record is None or not path.is_file():
            raise CleanroomError("history fixture is outside the admitted source set")
        raw = path.read_bytes()
        if len(raw) != record["byte_length"] or _sha256_bytes(raw) != record["content_sha256"]:
            raise CleanroomError("history fixture differs from the admitted source bytes")
    bundle = (root / bundle_relative).read_bytes()
    if (
        len(bundle) != bundle_record["byte_length"]
        or _sha256_bytes(bundle) != bundle_record["content_sha256"]
    ):
        raise CleanroomError("history bundle digest does not match its manifest")
    return history


def _history_security_scan(root: Path, history: Mapping[str, Any]) -> dict[str, Any]:
    fixed_commit = str(history["fixed_commit"])
    rows = _git(root, "rev-list", "--objects", fixed_commit).decode(
        "utf-8", errors="surrogateescape"
    )
    object_counts: Counter[str] = Counter()
    findings: dict[str, set[str]] = defaultdict(set)
    binary_blob_count = 0
    text_blob_count = 0
    finding_blob_count = 0
    paths: set[str] = set()
    seen: set[str] = set()
    for row in rows.splitlines():
        oid, *path_part = row.split(" ", 1)
        if oid in seen:
            continue
        seen.add(oid)
        path = path_part[0] if path_part else None
        if path:
            paths.add(path)
        object_type = _git(root, "cat-file", "-t", oid).decode().strip()
        object_counts[object_type] += 1
        if object_type != "blob":
            continue
        content = _git(root, "cat-file", "blob", oid)
        if b"\x00" in content:
            binary_blob_count += 1
            continue
        text_blob_count += 1
        categories = secret_findings(content.decode("utf-8", errors="replace"))
        if categories:
            finding_blob_count += 1
            findings[path or "<unmapped>"].update(categories)

    declared_security = history["security_scan"]
    declared_findings = {
        str(item["path"]): set(item["categories"])
        for item in declared_security.get("findings", [])
    }
    declared_sensitive_paths = {
        str(item["path"])
        for item in declared_security.get("reviewed_sensitive_named_paths", [])
    }
    observed = {
        "binary_blob_count": binary_blob_count,
        "finding_blob_count": finding_blob_count,
        "object_counts": dict(sorted(object_counts.items())),
        "reachable_object_count": len(seen),
        "text_blob_count": text_blob_count,
    }
    expected = {
        "binary_blob_count": declared_security.get("binary_blob_count"),
        "finding_blob_count": declared_security.get("finding_blob_count"),
        "object_counts": history.get("object_counts"),
        "reachable_object_count": history.get("reachable_object_count"),
        "text_blob_count": declared_security.get("text_blob_count"),
    }
    if observed != expected or dict(findings) != declared_findings:
        raise CleanroomError("history bundle security scan differs from the reviewed manifest")
    if not declared_sensitive_paths.issubset(paths):
        raise CleanroomError("reviewed sensitive-named history paths are missing")
    if declared_security.get("status") != "EXPECTED_SYNTHETIC_FIXTURE_LITERALS_ONLY":
        raise CleanroomError("history security disposition is not approved for cleanroom use")
    return observed


def _initialize_source_revision(root: Path, admission: Mapping[str, Any]) -> str:
    _git(root, "init", "-q")
    _git(root, "symbolic-ref", "HEAD", SYNTHETIC_BRANCH)
    _git(root, "config", "user.name", "RAG Admission Cleanroom")
    _git(root, "config", "user.email", "rag-admission@local.invalid")
    admitted_paths = sorted(str(item["path"]) for item in admission["files"])
    path_input = b"\0".join(path.encode("utf-8") for path in [*admitted_paths, RECEIPT_NAME]) + b"\0"
    _git(
        root,
        "add",
        "-f",
        "--pathspec-from-file=-",
        "--pathspec-file-nul",
        input_bytes=path_input,
    )
    identity = {
        "GIT_AUTHOR_DATE": SYNTHETIC_TIMESTAMP,
        "GIT_AUTHOR_EMAIL": "rag-admission@local.invalid",
        "GIT_AUTHOR_NAME": "RAG Admission Cleanroom",
        "GIT_COMMITTER_DATE": SYNTHETIC_TIMESTAMP,
        "GIT_COMMITTER_EMAIL": "rag-admission@local.invalid",
        "GIT_COMMITTER_NAME": "RAG Admission Cleanroom",
        "TZ": "UTC",
    }
    _git(
        root,
        "commit",
        "--quiet",
        "--no-gpg-sign",
        "-m",
        "synthetic G0 admitted source",
        environment=identity,
    )
    return _git(root, "rev-parse", "HEAD").decode().strip()


def _install_history(root: Path, history: Mapping[str, Any]) -> None:
    bundle_record = history["bundle"]
    bundle = root / HISTORY_DIRECTORY / str(bundle_record["file"])
    _git(root, "bundle", "verify", str(bundle))
    _git(
        root,
        "fetch",
        "--no-tags",
        str(bundle),
        f"{bundle_record['source_ref']}:{bundle_record['target_ref']}",
    )
    if _git(root, "rev-parse", str(history["fixed_commit"])).decode().strip() != history[
        "fixed_commit"
    ]:
        raise CleanroomError("fixed Code Golden commit is unavailable after bundle import")
    if _git(root, "rev-parse", f"{history['fixed_commit']}^{{tree}}").decode().strip() != history[
        "fixed_tree"
    ]:
        raise CleanroomError("fixed Code Golden tree differs after bundle import")


def _verify_clone_transfer(root: Path, history: Mapping[str, Any]) -> None:
    with tempfile.TemporaryDirectory(prefix="rag-g0-history-clone-") as temporary:
        destination = Path(temporary) / "clone"
        result = subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-local",
                "--no-checkout",
                str(root),
                str(destination),
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise CleanroomError("ordinary clone could not transfer the fixed history bundle")
        fixed = _git(destination, "rev-parse", str(history["fixed_commit"])).decode().strip()
        if fixed != history["fixed_commit"]:
            raise CleanroomError("ordinary clone lost the fixed Code Golden commit")


def prepare_cleanroom(root: Path, admission_manifest: Path) -> dict[str, Any]:
    root = root.resolve()
    admission_manifest = admission_manifest.resolve()
    if not root.is_dir() or (root / ".git").exists():
        raise CleanroomError("cleanroom root must be an uninitialized materialized source directory")
    verify_admission_manifest(admission_manifest)
    admission = _read_canonical_json(admission_manifest)
    _validate_receipt(root, admission)
    history = _validate_history_manifest(root, admission)
    source_commit = _initialize_source_revision(root, admission)
    _install_history(root, history)
    security = _history_security_scan(root, history)
    _verify_clone_transfer(root, history)
    admission_verification = verify_admission_manifest(
        admission_manifest,
        root=root,
        require_release_ready=True,
    )
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise CleanroomError("prepared cleanroom worktree is not clean")
    return {
        "admission_content_sha256": admission["content_sha256"],
        "cleanroom_source_commit": source_commit,
        "file_count": len(admission["files"]),
        "fixed_history_commit": history["fixed_commit"],
        "fixed_history_ref": history["bundle"]["target_ref"],
        "history_bundle_sha256": history["bundle"]["content_sha256"],
        "history_security_scan": security,
        "release_ready": admission_verification["current_release_ready"],
        "schema_version": CLEANROOM_SCHEMA_VERSION,
        "source_snapshot_sha256": admission["source_snapshot_sha256"],
        "status": "SELF_CONTAINED_CLEANROOM_READY",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a self-contained Git cleanroom from a materialized G0 admission snapshot."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    arguments = parser.parse_args()
    result = prepare_cleanroom(arguments.root, arguments.admission)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
