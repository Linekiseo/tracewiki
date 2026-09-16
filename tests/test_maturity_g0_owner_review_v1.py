from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from evidence_rag.evaluation.maturity_g0_admission_v2 import write_admission_manifest
from evidence_rag.evaluation.maturity_g0_owner_review_v1 import (
    MATURITY_G0_OWNER_REVIEW_VERSION,
    MaturityG0OwnerReviewError,
    build_owner_review_packet,
    main,
    verify_owner_review_packet,
    write_owner_review_packet,
)
from evidence_rag.evaluation.maturity_g0_v1 import (
    _canonical_json_bytes,
    _canonical_sha256,
)


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(("git", "-C", str(root), *arguments), check=True, capture_output=True)


def _write(root: Path, relative: str, content: str | bytes = "source\n") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    files: dict[str, str | bytes] = {
        ".github/workflows/ci.yml": "name: ci\n",
        ".gitignore": "node_modules/\ntarget/\nweb/app/\n",
        "Dockerfile": "FROM scratch\n",
        "Makefile": "test:\n\ttrue\n",
        "README.md": "# Project\n",
        "design-qa.md": "# QA\n",
        "docs/rag-optimization/README.md": "# Docs\n",
        "evals/code/code-golden-v2.manifest.json": "{}\n",
        "evals/code/runs/run-1/evaluation.sqlite3": b"immutable-evaluation-database",
        "frontend/.npmrc": "engine-strict=true\nstrict-allow-scripts=true\n",
        "frontend/index.html": "<main>source</main>\n",
        "frontend/package-lock.json": "{}\n",
        "frontend/package.json": "{}\n",
        "frontend/src/main.tsx": "export const value = 1\n",
        "native/shared/README.md": "# Shared\n",
        "plugins/sample/README.md": "# Plugin\n",
        "pyproject.toml": "[project]\nname='sample'\n",
        "src/evidence_rag/sample.py": "VALUE = 1\n",
        "tests/test_sample.py": "def test_value(): pass\n",
        "uv.lock": "version = 1\n",
        "web/assets/app.js": "console.log('legacy')\n",
        "web/index.html": "<main>generated</main>\n",
        "web/legacy.html": "<main>legacy</main>\n",
    }
    for relative, content in files.items():
        _write(root, relative, content)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")
    return root


def _admission(root: Path) -> Path:
    path = root / "artifacts" / "rag-maturity" / "g0" / "admission" / "admission.json"
    write_admission_manifest(root, path)
    return path


def _rewrite_packet(path: Path, mutation: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert callable(mutation)
    mutation(payload)
    unsigned = {key: value for key, value in payload.items() if key != "content_sha256"}
    payload["content_sha256"] = _canonical_sha256(unsigned)
    path.write_bytes(_canonical_json_bytes(payload) + b"\n")


def test_owner_review_packet_is_deterministic_complete_and_pending(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    admission = _admission(root)

    first = build_owner_review_packet(admission, root=root)
    second = build_owner_review_packet(admission, root=root)

    assert first == second
    assert first["schema_version"] == MATURITY_G0_OWNER_REVIEW_VERSION
    assert first["status"] == "PENDING_OWNER_REVIEW"
    assert first["owner_reviewed"] is False
    assert first["remote_ci_observed"] is False
    assert first["reviewed_revision"] is None
    assert first["file_count"] == len(first["review_items"])
    assert first["file_count"] == sum(
        group["file_count"] for group in first["review_groups"]
    )
    assert all(item["review_status"] == "PENDING" for item in first["review_items"])
    assert all(group["review_status"] == "PENDING" for group in first["review_groups"])
    assert first["constraints"] == {
        "database_accessed": False,
        "default_engine": "v1",
        "git_mutation_performed": False,
        "network_accessed": False,
        "production_authorized": False,
        "receipt_or_signature_generated": False,
    }


def test_owner_review_packet_preserves_blocker_and_exclusion_denominators(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    admission = _admission(root)

    packet = build_owner_review_packet(admission, root=root)

    blocker_action = next(
        item for item in packet["required_actions"] if item["action_id"] == "G0-OWNER-03"
    )
    assert blocker_action["blocker_paths"] == ["web/index.html"]
    assert packet["g0_admission"]["release_blocker_count"] == 1
    assert packet["excluded_file_count"] == sum(packet["exclusion_counts"].values())
    assert packet["listed_exclusion_count"] == len(packet["exclusion_items"])
    excluded = {item["path"]: item for item in packet["exclusion_items"]}
    assert excluded["web/index.html"]["expected_disposition"] == (
        "REBUILD_DO_NOT_ADMIT"
    )


def test_owner_review_packet_round_trip_and_copy_verify(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    admission = _admission(root)
    output = root / "artifacts" / "rag-maturity" / "g0" / "owner-review" / "packet.json"

    written = write_owner_review_packet(admission, output, root=root)
    verified = verify_owner_review_packet(output, admission, root=root)

    assert verified["content_sha256"] == written["content_sha256"]
    assert verified["status"] == "verified-pending-owner-review"
    copied = root / "artifacts" / "rag-maturity" / "g0" / "owner-copy" / "packet.json"
    copied.parent.mkdir(parents=True)
    shutil.copyfile(output, copied)
    assert verify_owner_review_packet(copied, admission, root=root) == verified


def test_owner_review_packet_refuses_overwrite_and_outside_output(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    admission = _admission(root)
    output = root / "artifacts" / "rag-maturity" / "g0" / "owner-review" / "packet.json"
    write_owner_review_packet(admission, output, root=root)

    with pytest.raises(MaturityG0OwnerReviewError, match="must not already exist"):
        write_owner_review_packet(admission, output, root=root)
    with pytest.raises(MaturityG0OwnerReviewError, match="inside the repository"):
        write_owner_review_packet(admission, tmp_path / "outside.json", root=root)


def test_owner_review_packet_rejects_authority_escalation_even_when_rehashed(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    admission = _admission(root)
    output = root / "artifacts" / "rag-maturity" / "g0" / "owner-review" / "packet.json"
    write_owner_review_packet(admission, output, root=root)

    def escalate(payload: dict[str, object]) -> None:
        payload["status"] = "APPROVED"
        payload["owner_reviewed"] = True
        payload["reviewed_revision"] = "a" * 40

    _rewrite_packet(output, escalate)
    with pytest.raises(MaturityG0OwnerReviewError, match="authority boundary"):
        verify_owner_review_packet(output, admission, root=root)


def test_owner_review_packet_rejects_scope_tamper_and_manifest_substitution(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    admission = _admission(root)
    output = root / "artifacts" / "rag-maturity" / "g0" / "owner-review" / "packet.json"
    write_owner_review_packet(admission, output, root=root)

    def tamper(payload: dict[str, object]) -> None:
        groups = payload["review_groups"]
        assert isinstance(groups, list)
        groups[0]["file_count"] += 1

    _rewrite_packet(output, tamper)
    with pytest.raises(MaturityG0OwnerReviewError, match="does not match"):
        verify_owner_review_packet(output, admission, root=root)

    write_owner_review_packet(
        admission,
        root / "artifacts" / "rag-maturity" / "g0" / "owner-review-2" / "packet.json",
        root=root,
    )
    substitute = (
        root / "artifacts" / "rag-maturity" / "g0" / "admission-copy" / "admission.json"
    )
    substitute.parent.mkdir(parents=True)
    shutil.copyfile(admission, substitute)
    clean_packet = (
        root / "artifacts" / "rag-maturity" / "g0" / "owner-review-2" / "packet.json"
    )
    with pytest.raises(MaturityG0OwnerReviewError, match="does not match"):
        verify_owner_review_packet(clean_packet, substitute, root=root)


def test_owner_review_packet_rejects_current_source_drift(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    admission = _admission(root)
    output = root / "artifacts" / "rag-maturity" / "g0" / "owner-review" / "packet.json"
    write_owner_review_packet(admission, output, root=root)
    _write(root, "src/evidence_rag/sample.py", "VALUE = 2\n")

    with pytest.raises(MaturityG0OwnerReviewError, match="verification failed"):
        verify_owner_review_packet(output, admission, root=root)


def test_owner_review_cli_build_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repository(tmp_path)
    admission = _admission(root)
    output = root / "artifacts" / "rag-maturity" / "g0" / "owner-review" / "packet.json"

    assert main(["build", str(output), "--admission", str(admission), "--root", str(root)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["status"] == "PENDING_OWNER_REVIEW"
    assert main(
        ["verify", str(output), "--admission", str(admission), "--root", str(root)]
    ) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified-pending-owner-review"
