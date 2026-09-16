from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from evidence_rag.evaluation.maturity_g0_v1 import (
    MATURITY_G0_BASELINE_VERSION,
    MaturityG0BaselineError,
    build_baseline_inventory,
    verify_baseline_inventory,
    write_baseline_inventory,
)


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(("git", "-C", str(root), *arguments), check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    (root / "src" / "evidence_rag").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs" / "rag-optimization").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "src" / "evidence_rag" / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "test_sample.py").write_text("def test_value(): pass\n", encoding="utf-8")
    (root / "docs" / "rag-optimization" / "README.md").write_text("# Docs\n", encoding="utf-8")
    (root / ".github" / "workflows" / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    (root / "Makefile").write_text("test:\n\ttrue\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")
    return root


def test_baseline_is_sanitized_content_addressed_and_portable(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    payload = build_baseline_inventory(root)

    assert payload["schema_version"] == MATURITY_G0_BASELINE_VERSION
    assert payload["status"] == "QUALITY_HOLD"
    assert payload["default_engine"] == "v1"
    assert payload["formal_database_accessed"] is False
    assert payload["git"]["worktree"] == {
        "conflict_count": 0,
        "modified_or_staged_count": 0,
        "untracked_count": 0,
    }
    assert payload["scopes"]["backend"]["tracked_file_count"] == 1
    assert payload["scopes"]["backend"]["untracked_file_count"] == 0
    assert str(root) not in json.dumps(payload)
    assert str(Path.home()) not in json.dumps(payload)

    output = tmp_path / "portable" / "baseline.json"
    written = write_baseline_inventory(root, output)
    verified = verify_baseline_inventory(output)
    assert verified["content_sha256"] == written["content_sha256"]
    assert verified["status"] == "verified-quality-hold-baseline"


def test_baseline_reports_untracked_scope_without_exposing_path(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "src" / "evidence_rag" / "untracked.py").write_text("VALUE = 2\n", encoding="utf-8")

    payload = build_baseline_inventory(root)

    assert payload["git"]["worktree"]["untracked_count"] == 1
    assert payload["scopes"]["backend"]["untracked_file_count"] == 1
    assert "untracked.py" not in json.dumps(payload)


def test_baseline_verify_rejects_tamper_and_overwrite(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = tmp_path / "baseline.json"
    write_baseline_inventory(root, output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["default_engine"] = "v2"
    output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(MaturityG0BaselineError, match="digest mismatch"):
        verify_baseline_inventory(output)
    with pytest.raises(MaturityG0BaselineError, match="must not already exist"):
        write_baseline_inventory(root, output)
