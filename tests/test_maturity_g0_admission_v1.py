from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from evidence_rag.evaluation.maturity_g0_admission_v1 import (
    MATURITY_G0_ADMISSION_VERSION,
    MaturityG0AdmissionError,
    build_admission_manifest,
    materialize_admission_snapshot,
    verify_admission_manifest,
    write_admission_manifest,
)


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(("git", "-C", str(root), *arguments), check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    files = {
        "src/evidence_rag/sample.py": "VALUE = 1\n",
        "tests/test_sample.py": "def test_value(): pass\n",
        "docs/rag-optimization/README.md": "# Docs\n",
        "frontend/src/main.tsx": "export const value = 1\n",
        "native/shared/README.md": "# Shared\n",
        "plugins/sample/README.md": "# Plugin\n",
        "web/index.html": "<main></main>\n",
        ".github/workflows/ci.yml": "name: ci\n",
        ".gitignore": "node_modules/\ntarget/\n",
        "Dockerfile": "FROM scratch\n",
        "Makefile": "test:\n\ttrue\n",
        "README.md": "# Project\n",
        "design-qa.md": "# QA\n",
        "pyproject.toml": "[project]\nname='sample'\n",
        "uv.lock": "version = 1\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (root / "frontend" / "node_modules" / "bad.js").parent.mkdir(parents=True)
    (root / "frontend" / "node_modules" / "bad.js").write_text("generated\n")
    (root / "evals" / "run" / "evaluation.sqlite3").parent.mkdir(parents=True)
    (root / "evals" / "run" / "evaluation.sqlite3").write_bytes(b"database")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")
    return root


def test_admission_manifest_is_exact_content_addressed_and_portable(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    untracked = root / "src" / "evidence_rag" / "untracked.py"
    untracked.write_text("VALUE = 2\n", encoding="utf-8")

    payload = build_admission_manifest(root)

    assert payload["schema_version"] == MATURITY_G0_ADMISSION_VERSION
    assert payload["status"] == "VERSION_SCOPE_IDENTIFIED"
    assert payload["owner_reviewed"] is False
    assert payload["production_authorized"] is False
    assert payload["formal_database_accessed"] is False
    assert payload["state_counts"]["untracked"] == 1
    paths = [item["path"] for item in payload["files"]]
    assert paths == sorted(paths)
    assert "src/evidence_rag/untracked.py" in paths
    assert not any("node_modules" in item for item in paths)
    assert not any(item.endswith("evaluation.sqlite3") for item in paths)
    serialized = json.dumps(payload)
    assert str(root) not in serialized
    assert str(Path.home()) not in serialized

    output = tmp_path / "portable" / "admission.json"
    written = write_admission_manifest(root, output)
    verified = verify_admission_manifest(output, root=root)
    assert verified["content_sha256"] == written["content_sha256"]
    assert verified["current_source_verified"] is True
    assert verified["current_worktree_clean"] is False


def test_admission_manifest_detects_source_change_and_requires_clean(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = tmp_path / "admission.json"
    write_admission_manifest(root, output)

    assert verify_admission_manifest(output, root=root, require_clean=True)[
        "current_worktree_clean"
    ] is True
    (root / "local.tmp").write_text("not admitted\n")
    with pytest.raises(MaturityG0AdmissionError, match="not clean"):
        verify_admission_manifest(output, root=root, require_clean=True)
    (root / "local.tmp").unlink()
    (root / "src" / "evidence_rag" / "sample.py").write_text("VALUE = 3\n")
    with pytest.raises(MaturityG0AdmissionError, match="does not match"):
        verify_admission_manifest(output, root=root)


def test_admission_manifest_rejects_tamper_and_unsafe_path(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = tmp_path / "admission.json"
    write_admission_manifest(root, output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["files"][0]["path"] = "../escape"
    output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(MaturityG0AdmissionError, match="content digest mismatch"):
        verify_admission_manifest(output)


def test_materialized_snapshot_contains_only_verified_manifest_files(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = tmp_path / "admission.json"
    payload = write_admission_manifest(root, manifest)
    output = tmp_path / "source-copy"

    receipt = materialize_admission_snapshot(manifest, root, output)

    assert receipt["source_snapshot_sha256"] == payload["source_snapshot_sha256"]
    assert receipt["file_count"] == len(payload["files"])
    assert (output / "src" / "evidence_rag" / "sample.py").is_file()
    assert not (output / "frontend" / "node_modules").exists()
    assert json.loads((output / ".rag-admission-source.json").read_text())["status"] == (
        "materialized-source-snapshot"
    )
    with pytest.raises(MaturityG0AdmissionError, match="must not already exist"):
        materialize_admission_snapshot(manifest, root, output)
