from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from evidence_rag.evaluation.maturity_g0_package_v1 import (
    G0Command,
    MaturityG0PackageError,
    build_g0_package,
    verify_g0_package,
)


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(("git", "-C", str(root), *arguments), check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    (root / "src" / "evidence_rag").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs" / "rag-optimization").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "artifacts").mkdir()
    (root / "src" / "evidence_rag" / "sample.py").write_text("VALUE = 1\n")
    (root / "tests" / "test_sample.py").write_text("def test_value(): pass\n")
    (root / "docs" / "rag-optimization" / "README.md").write_text("# Docs\n")
    (root / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
    (root / "Makefile").write_text("test:\n\ttrue\n")
    (root / "pyproject.toml").write_text("[project]\nname='sample'\n")
    (root / "uv.lock").write_text("version = 1\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")
    return root


def _commands() -> tuple[G0Command, ...]:
    return (
        G0Command("pass-one", ("git", "--version"), "test", timeout_seconds=10),
        G0Command("pass-two", ("git", "status", "--short"), "test", timeout_seconds=10),
    )


def test_g0_package_is_portable_verified_and_holds_release(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "src" / "evidence_rag" / "untracked.py").write_text("VALUE = 2\n")
    output = root / "artifacts" / "rag-maturity" / "g0" / "run-one"
    manifest = build_g0_package(root, output, commands=_commands())

    assert manifest["status"] == "ENGINEERING_PASS"
    assert manifest["decision"] == "QUALITY_HOLD"
    assert manifest["g0_qualified"] is False
    assert manifest["production_authorized"] is False
    verified = verify_g0_package(output)
    assert verified["status"] == "verified"
    assert verified["decision"] == "QUALITY_HOLD"

    copied = tmp_path / "portable-copy"
    shutil.copytree(output, copied)
    assert verify_g0_package(copied) == verified


def test_g0_package_can_qualify_only_from_clean_version_control(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = root / "artifacts" / "rag-maturity" / "g0" / "run-clean"

    manifest = build_g0_package(root, output, commands=_commands())

    assert manifest["g0_qualified"] is True
    assert manifest["decision"] == "G0_ENGINEERING_PASS"
    assert json.loads((output / "results.json").read_text())["version_control_ready"] is True


def test_g0_package_rejects_tamper_failure_and_outside_output(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = root / "artifacts" / "rag-maturity" / "g0" / "run-tamper"
    build_g0_package(root, output, commands=_commands())
    results = json.loads((output / "results.json").read_text())
    results["all_commands_passed"] = False
    (output / "results.json").write_text(
        json.dumps(results, sort_keys=True, separators=(",", ":")) + "\n"
    )
    with pytest.raises(MaturityG0PackageError, match="checksum mismatch"):
        verify_g0_package(output)

    with pytest.raises(MaturityG0PackageError, match="inside the repository"):
        build_g0_package(root, tmp_path / "outside", commands=_commands())

    failed_output = root / "artifacts" / "rag-maturity" / "g0" / "run-failed"
    failed = build_g0_package(
        root,
        failed_output,
        commands=(G0Command("fail", ("git", "not-a-command"), "test", 10),),
    )
    assert failed["status"] == "FAILED"
    assert verify_g0_package(failed_output)["status"] == "verified"
