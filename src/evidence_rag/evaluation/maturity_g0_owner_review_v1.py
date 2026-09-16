"""Build and verify a deterministic, always-PENDING G0 owner review packet.

The packet turns a large admission manifest into an explicit review queue. It does
not approve a revision, sign a receipt, mutate Git, access a database, or contact a
remote service. External owner and CI evidence must be produced separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .maturity_g0_admission_v1 import MaturityG0AdmissionError
from .maturity_g0_admission_v2 import (
    _read_manifest,
    _validate_manifest,
    verify_admission_manifest,
)
from .maturity_g0_v1 import _canonical_json_bytes, _canonical_sha256

MATURITY_G0_OWNER_REVIEW_VERSION = "rag-maturity-g0-owner-review-v1"
_DIGEST_DOMAIN = b"rag-maturity-g0-owner-review-v1\0"


class MaturityG0OwnerReviewError(RuntimeError):
    """A G0 owner review packet is invalid or cannot be produced safely."""


def _domain_sha256(label: str, value: object) -> str:
    return "sha256:" + hashlib.sha256(
        _DIGEST_DOMAIN + label.encode("ascii") + b"\0" + _canonical_json_bytes(value)
    ).hexdigest()


def _safe_repository_path(path: Path, *, root: Path, label: str) -> tuple[Path, str]:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise MaturityG0OwnerReviewError(f"{label} must stay inside the repository")
    relative = resolved.relative_to(root).as_posix()
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or ".." in pure.parts:
        raise MaturityG0OwnerReviewError(f"{label} path is unsafe")
    return resolved, relative


def _review_groups(
    files: tuple[dict[str, Any], ...], scopes: Mapping[str, Any]
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for scope in sorted(scopes):
        scoped = tuple(item for item in files if item["scope"] == scope)
        subject = {
            "files": tuple(
                {
                    "byte_length": item["byte_length"],
                    "content_sha256": item["content_sha256"],
                    "path": item["path"],
                    "role": item["role"],
                }
                for item in scoped
            ),
            "scope": scope,
            "scope_content_sha256": scopes[scope]["content_sha256"],
        }
        groups.append(
            {
                "capture_git_state_counts": dict(
                    sorted(Counter(item["git_state"] for item in scoped).items())
                ),
                "file_count": len(scoped),
                "review_status": "PENDING",
                "review_subject_sha256": _domain_sha256("scope-review-v1", subject),
                "role_counts": dict(
                    sorted(Counter(item["role"] for item in scoped).items())
                ),
                "scope": scope,
                "scope_content_sha256": scopes[scope]["content_sha256"],
                "total_bytes": sum(item["byte_length"] for item in scoped),
            }
        )
    return groups


def _review_items(files: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [
        {
            "byte_length": item["byte_length"],
            "capture_git_state": item["git_state"],
            "content_sha256": item["content_sha256"],
            "path": item["path"],
            "review_status": "PENDING",
            "role": item["role"],
            "scope": item["scope"],
        }
        for item in files
    ]


def _exclusion_items(excluded: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    dispositions = {
        "cache": "REGENERATE_OR_IGNORE",
        "database_or_unapproved_sqlite": "REJECT_FROM_ADMISSION",
        "database_sidecar": "REJECT_TRANSIENT_DATABASE_STATE",
        "generated_build_output": "REBUILD_DO_NOT_ADMIT",
        "sensitive_path": "REJECT_AND_SECURITY_REVIEW",
        "symlink": "REJECT_UNSAFE_PATH",
        "unmanifested_document_asset": "OWNER_DECIDE_OUTSIDE_SOURCE_ADMISSION",
    }
    return [
        {
            "capture_git_state": item["git_state"],
            "expected_disposition": dispositions[item["reason"]],
            "path": item["path"],
            "reason": item["reason"],
            "review_status": "PENDING",
            "scope": item["scope"],
        }
        for item in excluded
    ]


def _required_actions(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocker_paths = [
        path
        for finding in payload["findings"]
        if finding["blocks_release"]
        for path in finding["paths"]
    ]
    return [
        {
            "action_id": "G0-OWNER-01",
            "authority": "repository_owner",
            "evidence": "all review_items and review_groups externally reviewed",
            "status": "PENDING",
        },
        {
            "action_id": "G0-OWNER-02",
            "authority": "repository_owner",
            "evidence": "all exclusion_items dispositioned without broad staging",
            "status": "PENDING",
        },
        {
            "action_id": "G0-OWNER-03",
            "authority": "repository_owner",
            "blocker_paths": blocker_paths,
            "evidence": "tracked runtime output removed from version control and rebuilt",
            "status": "PENDING",
        },
        {
            "action_id": "G0-OWNER-04",
            "authority": "protected_revision_control",
            "evidence": "commit/tree bytes equal the admission source snapshot",
            "status": "PENDING",
        },
        {
            "action_id": "G0-OWNER-05",
            "authority": "remote_ci",
            "evidence": "same-revision Python matrix, exact, cleanroom, frontend, supply chain, and release-ready",
            "status": "PENDING",
        },
        {
            "action_id": "G0-OWNER-06",
            "authority": "ui_reviewer",
            "evidence": "LIVE-02 page identity, console, hash navigation, and back/forward",
            "status": "PENDING",
        },
        {
            "action_id": "G0-OWNER-07",
            "authority": "independent_gate_reviewer",
            "evidence": "new immutable G0 package and Gate Review",
            "status": "PENDING",
        },
    ]


def build_owner_review_packet(manifest: Path, *, root: Path) -> dict[str, Any]:
    """Build a deterministic PENDING packet after native admission verification."""

    resolved_root = root.resolve()
    if not (resolved_root / ".git").is_dir():
        raise MaturityG0OwnerReviewError("owner review root must be a Git worktree")
    resolved_manifest, manifest_relative = _safe_repository_path(
        manifest, root=resolved_root, label="admission manifest"
    )
    try:
        verify_admission_manifest(resolved_manifest, root=resolved_root)
        admission = _read_manifest(resolved_manifest)
        files, excluded = _validate_manifest(admission)
    except (OSError, MaturityG0AdmissionError) as exc:
        raise MaturityG0OwnerReviewError("admission manifest verification failed") from exc

    packet: dict[str, Any] = {
        "constraints": {
            "database_accessed": False,
            "default_engine": "v1",
            "git_mutation_performed": False,
            "network_accessed": False,
            "production_authorized": False,
            "receipt_or_signature_generated": False,
        },
        "excluded_file_count": sum(admission["exclusion_counts"].values()),
        "exclusion_counts": admission["exclusion_counts"],
        "exclusion_items": _exclusion_items(excluded),
        "file_count": len(files),
        "g0_admission": {
            "base_head": admission["base_head"],
            "content_sha256": admission["content_sha256"],
            "file_count": len(files),
            "path": manifest_relative,
            "release_blocker_count": admission["release_blocker_count"],
            "schema_version": admission["schema_version"],
            "scope_policy_sha256": admission["scope_policy_sha256"],
            "source_snapshot_sha256": admission["source_snapshot_sha256"],
        },
        "listed_exclusion_count": len(excluded),
        "owner_reviewed": False,
        "remote_ci_observed": False,
        "required_actions": _required_actions(admission),
        "review_groups": _review_groups(files, admission["scopes"]),
        "review_items": _review_items(files),
        "reviewed_revision": None,
        "schema_version": MATURITY_G0_OWNER_REVIEW_VERSION,
        "status": "PENDING_OWNER_REVIEW",
    }
    packet["review_subject_sha256"] = _domain_sha256(
        "owner-review-v1",
        {
            "exclusion_items": packet["exclusion_items"],
            "g0_admission": packet["g0_admission"],
            "required_actions": packet["required_actions"],
            "review_groups": packet["review_groups"],
            "review_items": packet["review_items"],
        }
    )
    packet["content_sha256"] = _canonical_sha256(packet)
    return packet


def write_owner_review_packet(manifest: Path, output: Path, *, root: Path) -> dict[str, Any]:
    resolved_root = root.resolve()
    resolved_output, _ = _safe_repository_path(
        output, root=resolved_root, label="owner review output"
    )
    if resolved_output.exists():
        raise MaturityG0OwnerReviewError("owner review output must not already exist")
    packet = build_owner_review_packet(manifest, root=resolved_root)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_bytes(_canonical_json_bytes(packet) + b"\n")
    return packet


def _read_packet(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        packet = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaturityG0OwnerReviewError("owner review packet is not valid JSON") from exc
    if not isinstance(packet, dict) or raw != _canonical_json_bytes(packet) + b"\n":
        raise MaturityG0OwnerReviewError("owner review packet is not canonical JSON")
    return packet


def verify_owner_review_packet(
    packet_path: Path, manifest: Path, *, root: Path
) -> dict[str, Any]:
    """Verify packet integrity, native admission authority, and current source bytes."""

    packet = _read_packet(packet_path)
    if packet.get("schema_version") != MATURITY_G0_OWNER_REVIEW_VERSION:
        raise MaturityG0OwnerReviewError("owner review packet schema is unsupported")
    content_sha256 = packet.get("content_sha256")
    unsigned = {key: value for key, value in packet.items() if key != "content_sha256"}
    if content_sha256 != _canonical_sha256(unsigned):
        raise MaturityG0OwnerReviewError("owner review packet content digest mismatch")
    if (
        packet.get("status") != "PENDING_OWNER_REVIEW"
        or packet.get("owner_reviewed") is not False
        or packet.get("remote_ci_observed") is not False
        or packet.get("reviewed_revision") is not None
        or packet.get("constraints")
        != {
            "database_accessed": False,
            "default_engine": "v1",
            "git_mutation_performed": False,
            "network_accessed": False,
            "production_authorized": False,
            "receipt_or_signature_generated": False,
        }
    ):
        raise MaturityG0OwnerReviewError("owner review packet authority boundary is invalid")
    expected = build_owner_review_packet(manifest, root=root)
    if packet != expected:
        raise MaturityG0OwnerReviewError(
            "owner review packet does not match the verified admission manifest"
        )
    return {
        "content_sha256": content_sha256,
        "excluded_file_count": packet["excluded_file_count"],
        "file_count": packet["file_count"],
        "listed_exclusion_count": packet["listed_exclusion_count"],
        "owner_reviewed": False,
        "review_group_count": len(packet["review_groups"]),
        "review_subject_sha256": packet["review_subject_sha256"],
        "schema_version": MATURITY_G0_OWNER_REVIEW_VERSION,
        "status": "verified-pending-owner-review",
    }


def _summary(packet: Mapping[str, Any], *, output: Path | None = None) -> dict[str, Any]:
    summary = {
        "content_sha256": packet["content_sha256"],
        "excluded_file_count": packet["excluded_file_count"],
        "file_count": packet["file_count"],
        "listed_exclusion_count": packet["listed_exclusion_count"],
        "owner_reviewed": False,
        "review_group_count": len(packet["review_groups"]),
        "review_subject_sha256": packet["review_subject_sha256"],
        "schema_version": MATURITY_G0_OWNER_REVIEW_VERSION,
        "status": packet["status"],
    }
    if output is not None:
        summary["output"] = str(output)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("output", type=Path)
    build.add_argument("--admission", type=Path, required=True)
    build.add_argument("--root", type=Path, default=Path.cwd())
    verify = commands.add_parser("verify")
    verify.add_argument("packet", type=Path)
    verify.add_argument("--admission", type=Path, required=True)
    verify.add_argument("--root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "build":
        packet = write_owner_review_packet(
            arguments.admission, arguments.output, root=arguments.root
        )
        result = _summary(packet, output=arguments.output)
    else:
        result = verify_owner_review_packet(
            arguments.packet, arguments.admission, root=arguments.root
        )
    print(_canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
