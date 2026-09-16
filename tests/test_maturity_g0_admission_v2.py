from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from evidence_rag.evaluation.maturity_g0_admission_v1 import MaturityG0AdmissionError
from evidence_rag.evaluation.maturity_g0_admission_v2 import (
    MATURITY_G0_ADMISSION_VERSION,
    build_admission_manifest,
    materialize_admission_snapshot,
    verify_admission_manifest,
    write_admission_manifest,
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
        "evals/code/runs/run-1/manifest.json": "{}\n",
        "frontend/.npmrc": "engine-strict=true\nstrict-allow-scripts=true\n",
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


def test_v2_admits_explicit_hidden_frontend_supply_chain_policy(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    payload = build_admission_manifest(root)
    files = {item["path"]: item for item in payload["files"]}

    assert files["frontend/.npmrc"]["scope"] == "frontend"
    assert files["frontend/.npmrc"]["role"] == "frontend_source"


def test_v2_separates_sources_anchors_and_generated_outputs(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "web/app/index-12345678.js", "generated\n")
    _write(root, "web/build-contract.json", "{}\n")
    _write(root, "web/index.html", "<main>generated</main>\n")
    _write(root, "frontend/vite.config.js", "generated\n")
    _write(root, "frontend/src-tauri/gen/schema.json", "{}\n")
    _write(root, "docs/design/qa-local.png", b"png")
    _write(root, "evals/code/runs/run-1/evaluation.sqlite3-wal", b"wal")
    _write(root, "frontend/.env.local", "TOKEN=secret\n")

    payload = build_admission_manifest(root)

    assert payload["schema_version"] == MATURITY_G0_ADMISSION_VERSION
    assert payload["status"] == "VERSION_SCOPE_REFINED"
    assert payload["database_policy"] == {
        "evaluation_sqlite_anchors_hashed_as_opaque_bytes": 1,
        "formal_application_databases_included": False,
        "formal_database_opened": False,
        "sidecars_included": False,
    }
    paths = {item["path"]: item for item in payload["files"]}
    assert paths["evals/code/runs/run-1/evaluation.sqlite3"]["role"] == (
        "evaluation_anchor"
    )
    assert "web/assets/app.js" in paths
    assert "web/legacy.html" in paths
    for excluded in (
        "docs/design/qa-local.png",
        "evals/code/runs/run-1/evaluation.sqlite3-wal",
        "frontend/.env.local",
        "frontend/src-tauri/gen/schema.json",
        "frontend/vite.config.js",
        "web/app/index-12345678.js",
        "web/build-contract.json",
        "web/index.html",
    ):
        assert excluded not in paths
    exclusions = {item["path"]: item["reason"] for item in payload["excluded_files"]}
    assert exclusions["evals/code/runs/run-1/evaluation.sqlite3-wal"] == "database_sidecar"
    assert exclusions["frontend/.env.local"] == "sensitive_path"
    assert exclusions["web/index.html"] == "generated_build_output"
    assert payload["owner_reviewed"] is False
    assert payload["production_authorized"] is False
    assert str(root) not in json.dumps(payload)


def test_v2_manifest_is_portable_and_detects_admitted_source_change(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = tmp_path / "admission.json"
    written = write_admission_manifest(root, manifest)

    verified = verify_admission_manifest(manifest, root=root)

    assert verified["content_sha256"] == written["content_sha256"]
    assert verified["current_source_verified"] is True
    assert verified["current_exclusions_approved"] is True
    assert verified["current_worktree_clean"] is True
    assert verified["current_release_ready"] is True
    _write(root, "web/app/chunk-12345678.js", "ignored-generated\n")
    assert verify_admission_manifest(manifest, root=root)["current_source_verified"] is True
    _write(root, "src/evidence_rag/sample.py", "VALUE = 2\n")
    with pytest.raises(MaturityG0AdmissionError, match="does not match"):
        verify_admission_manifest(manifest, root=root)


def test_v2_release_ready_rejects_tracked_generated_runtime_output(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "web/index.html", "<main>generated</main>\n")
    _git(root, "add", "web/index.html")
    _git(root, "commit", "-q", "-m", "tracked generated output")
    manifest = tmp_path / "admission.json"
    payload = write_admission_manifest(root, manifest)

    verified = verify_admission_manifest(manifest, root=root, require_clean=True)

    assert verified["current_worktree_clean"] is True
    assert verified["current_release_ready"] is False
    assert payload["release_blocker_count"] == 1
    assert any(
        item["code"] == "TRACKED_RUNTIME_OUTPUT_OUTSIDE_SOURCE_SET"
        for item in payload["findings"]
    )
    with pytest.raises(MaturityG0AdmissionError, match="release-ready"):
        verify_admission_manifest(manifest, root=root, require_release_ready=True)


def test_v2_release_ready_allows_missing_exclusions_and_ignored_cache(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    _write(root, "docs/design/local-review.png", b"png")
    manifest = tmp_path / "admission.json"
    write_admission_manifest(root, manifest)
    (root / "docs/design/local-review.png").unlink()
    _write(root, "node_modules/cache-only/index.js", "ignored\n")

    verified = verify_admission_manifest(
        manifest,
        root=root,
        require_release_ready=True,
    )

    assert verified["current_exclusions_match"] is False
    assert verified["current_exclusions_approved"] is True
    assert verified["current_unapproved_exclusion_addition_count"] == 0
    assert verified["current_release_ready"] is True


def test_v2_release_ready_rejects_new_ignored_sensitive_exclusion(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    gitignore = root / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8") + "frontend/.env.local\n",
        encoding="utf-8",
    )
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-q", "-m", "ignore local frontend environment")
    manifest = tmp_path / "admission.json"
    write_admission_manifest(root, manifest)
    _write(root, "frontend/.env.local", "TOKEN=secret\n")

    verified = verify_admission_manifest(manifest, root=root)

    assert verified["current_worktree_clean"] is True
    assert verified["current_exclusions_approved"] is False
    assert verified["current_unapproved_exclusion_addition_count"] == 1
    assert verified["current_release_ready"] is False
    with pytest.raises(MaturityG0AdmissionError, match="release-ready"):
        verify_admission_manifest(manifest, root=root, require_release_ready=True)


def test_v2_rejects_tamper_and_materializes_only_admitted_files(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _write(root, "docs/design/unmanifested.png", b"png")
    manifest = tmp_path / "admission.json"
    payload = write_admission_manifest(root, manifest)
    output = tmp_path / "source-copy"

    receipt = materialize_admission_snapshot(manifest, root, output)

    assert receipt["source_snapshot_sha256"] == payload["source_snapshot_sha256"]
    assert (output / "evals/code/runs/run-1/evaluation.sqlite3").is_file()
    assert not (output / "docs/design/unmanifested.png").exists()
    assert json.loads((output / ".rag-admission-source.json").read_text())["status"] == (
        "materialized-source-snapshot"
    )

    tampered = tmp_path / "tampered.json"
    parsed = json.loads(manifest.read_text())
    parsed["excluded_files"][0]["reason"] = "cache"
    tampered.write_text(json.dumps(parsed, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(MaturityG0AdmissionError, match="content digest mismatch"):
        verify_admission_manifest(tampered)
