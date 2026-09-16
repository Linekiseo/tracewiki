from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import evidence_rag.cli as cli_module
from evidence_rag.rag.release_admission_v2 import (
    build_current_repository_quality_hold_v2,
    inspect_current_production_authority_v2,
    write_release_admission_package_v2,
)


def _quality_hold_package(tmp_path: Path) -> Path:
    return write_release_admission_package_v2(
        output_dir=tmp_path / "release-package",
        artifact_root=tmp_path,
        authority=inspect_current_production_authority_v2(),
        admission=build_current_repository_quality_hold_v2(),
    )


def _forbid_runtime(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("verify-only CLI created the application Runtime")


def test_status_command_is_verify_only_and_ignores_runtime_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli_module, "create_runtime", _forbid_runtime)
    monkeypatch.setenv("RAG_CODE_ENGINE", "v2")
    monkeypatch.setenv("RAG_RELEASE_DECISION", "PROMOTE")

    cli_module.main(["status"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["default_engine"] == "v1"
    assert payload["quality_hold"] is True
    assert payload["decision"] == "QUALITY_HOLD"
    assert payload["source_snapshot"]["status"] == "UNAVAILABLE"
    assert payload["rollback_snapshot"]["status"] == "UNAVAILABLE"
    assert payload["performance_snapshot"]["status"] == "UNAVAILABLE"
    assert payload["schema_version"] == "rag-ops-status-v1"
    assert payload["sources"]["workspace"]["v2_stage"] == "NO_RELEASE"
    assert payload["component_switches"]["planner"] is None
    assert payload["performance"]["index"]["availability"] == "UNAVAILABLE"
    assert payload["performance"]["cache"]["availability"] == "UNAVAILABLE"
    assert payload["performance"]["latency"]["availability"] == "UNAVAILABLE"
    assert payload["reviewed_calibration"]["availability"] == "UNAVAILABLE"
    assert payload["verify_only"] is True
    assert "PROMOTE" not in captured.out
    assert "/Users/" not in captured.out
    assert "allowed_acl_refs" not in captured.out
    assert '"query"' not in captured.out


def test_package_verify_is_portable_path_free_and_does_not_create_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = _quality_hold_package(tmp_path)
    copied = tmp_path / "copied-release-package"
    shutil.copytree(package, copied)
    monkeypatch.setattr(cli_module, "create_runtime", _forbid_runtime)

    cli_module.main(["package", "verify", str(package)])
    first = capsys.readouterr()
    cli_module.main(["package", "verify", str(copied)])
    second = capsys.readouterr()

    assert first.err == second.err == ""
    assert json.loads(first.out) == json.loads(second.out)
    payload = json.loads(first.out)
    assert payload["package_kind"] == "release-admission"
    assert payload["portable"] is True
    assert payload["verify_only"] is True
    assert payload["decision"] == "QUALITY_HOLD"
    assert payload["default_engine"] == "v1"
    assert str(package) not in first.out
    assert str(copied) not in second.out


def test_package_verify_failure_is_sanitized_and_does_not_create_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = tmp_path / "private" / "invalid-package"
    package.mkdir(parents=True)
    (package / "query-secret.sqlite3").write_text("not a release package", encoding="utf-8")
    monkeypatch.setattr(cli_module, "create_runtime", _forbid_runtime)

    with pytest.raises(SystemExit) as error:
        cli_module.main(["package", "verify", str(package)])

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "INVALID",
        "verify_only": True,
        "reason_code": "package-verification-failed",
    }
    assert str(package) not in captured.err
    assert "query-secret" not in captured.err
