from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/backend-ci.yml"
AUTHORITY_ARTIFACT_NAME = (
    "g0-reviewed-admission-${{ github.run_id }}-${{ github.run_attempt }}"
)
AUTHORITY_ARTIFACT_PATH = "${{ runner.temp }}/g0-reviewed-authority"
AUTHORITY_MANIFEST_PATH = "${RUNNER_TEMP}/g0-reviewed-authority/admission.json"


def _workflow() -> dict[str, Any]:
    loaded = yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return loaded


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in job["steps"] if item.get("name") == name)


def _uses_step(job: dict[str, Any], action: str) -> dict[str, Any]:
    return next(item for item in job["steps"] if item.get("uses") == action)


def _download_contract(job: dict[str, Any]) -> None:
    download = _uses_step(job, "actions/download-artifact@v4")
    assert download["with"] == {
        "name": AUTHORITY_ARTIFACT_NAME,
        "path": AUTHORITY_ARTIFACT_PATH,
    }
    transported = _step(job, "Verify transported admission bytes")["run"]
    assert "${{ needs.authority.outputs.manifest-file-sha256 }}" in transported
    assert 'sha256sum "${manifest_path}"' in transported
    assert transported.index("sha256sum") < transported.index("test ")


def test_g0_workflow_covers_every_change_without_path_filter_bypass() -> None:
    workflow = _workflow()
    triggers = workflow["on"]

    assert set(triggers) == {"pull_request", "push", "workflow_dispatch"}
    assert "paths" not in triggers["pull_request"]
    assert "paths" not in triggers["push"]
    assert triggers["push"]["branches"] == ["main"]
    assert workflow["concurrency"]["cancel-in-progress"] == "true"

    dispatch = triggers["workflow_dispatch"]
    assert set(dispatch["inputs"]) == {
        "g0_admission_authority_commit",
        "g0_admission_file_sha256",
    }
    for value in dispatch["inputs"].values():
        assert value["required"] == "false"
        assert value["type"] == "string"


def test_g0_authority_job_fetches_exact_reviewed_manifest_and_publishes_artifact() -> None:
    workflow = _workflow()
    job = workflow["jobs"]["authority"]

    assert job["name"] == "Bind reviewed G0 admission"
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == "10"
    assert job["permissions"] == {"contents": "read"}
    assert job["environment"] == "g0-reviewed-authority"
    assert job["env"] == {
        "G0_ADMISSION_AUTHORITY_COMMIT": (
            "${{ inputs.g0_admission_authority_commit || "
            "vars.G0_ADMISSION_AUTHORITY_COMMIT }}"
        ),
        "G0_ADMISSION_FILE_SHA256": (
            "${{ inputs.g0_admission_file_sha256 || vars.G0_ADMISSION_FILE_SHA256 }}"
        ),
    }
    assert job["outputs"] == {
        "authority-commit": "${{ steps.bind.outputs.authority-commit }}",
        "manifest-file-sha256": "${{ steps.bind.outputs.manifest-file-sha256 }}",
        "artifact-id": "${{ steps.upload.outputs.artifact-id }}",
        "artifact-digest": "${{ steps.upload.outputs.artifact-digest }}",
        "artifact-url": "${{ steps.upload.outputs.artifact-url }}",
    }

    validate = _step(job, "Validate reviewed authority selection")["run"]
    assert "^[0-9a-f]{40}$" in validate
    assert "^[0-9a-f]{64}$" in validate
    assert "authority commit is required" in validate
    assert "manifest SHA-256 is required" in validate

    checkout = _uses_step(job, "actions/checkout@v4")
    assert checkout["with"] == {
        "repository": "${{ github.repository }}",
        "ref": "${{ env.G0_ADMISSION_AUTHORITY_COMMIT }}",
        "path": "authority",
        "fetch-depth": "1",
        "persist-credentials": "false",
    }

    bind = _step(job, "Bind reviewed admission bytes")
    assert bind["id"] == "bind"
    bind_run = bind["run"]
    expected_fragments = [
        "git -C authority rev-parse HEAD",
        "git -C authority ls-tree",
        'test "${object_mode}" = "100644"',
        'test "${object_type}" = "blob"',
        'test "${object_path}" = "admission.json"',
        "${RUNNER_TEMP}/g0-reviewed-authority",
        'sha256sum "${manifest_path}"',
        'test "${actual_sha256}" = "${G0_ADMISSION_FILE_SHA256}"',
        "authority-commit=${G0_ADMISSION_AUTHORITY_COMMIT}",
        "manifest-file-sha256=${actual_sha256}",
    ]
    for fragment in expected_fragments:
        assert fragment in bind_run
    assert "candidate_commit=%s" in bind_run
    assert "${GITHUB_SHA}" in bind_run
    assert "run_id=%s" in bind_run
    assert "${GITHUB_RUN_ID}" in bind_run
    assert "run_attempt=%s" in bind_run
    assert "${GITHUB_RUN_ATTEMPT}" in bind_run
    assert "sha256sum admission.json transport-receipt.txt > SHA256SUMS" in bind_run
    assert "make g0-admission-build" not in bind_run
    assert "latest" not in bind_run.casefold()

    upload = _step(job, "Upload reviewed admission authority")
    assert upload["id"] == "upload"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"] == {
        "name": AUTHORITY_ARTIFACT_NAME,
        "path": AUTHORITY_ARTIFACT_PATH,
        "if-no-files-found": "error",
        "retention-days": "30",
    }
    steps = job["steps"]
    assert steps.index(_step(job, "Validate reviewed authority selection")) < steps.index(checkout)
    assert steps.index(checkout) < steps.index(bind) < steps.index(upload)

    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "make g0-admission-build" not in source
    assert "artifact: latest" not in source.casefold()
    assert not re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", source)


def test_g0_backend_matrix_is_locked_and_read_only() -> None:
    job = _workflow()["jobs"]["verify"]

    assert job["needs"] == "authority"
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == "45"
    assert job["permissions"] == {"contents": "read"}
    assert job["strategy"]["fail-fast"] == "false"
    assert job["strategy"]["matrix"]["python-version"] == ["3.12", "3.13"]
    checkout = _uses_step(job, "actions/checkout@v4")
    assert checkout["with"]["persist-credentials"] == "false"
    _download_contract(job)
    assert _step(job, "Install locked test environment")["run"] == (
        "uv sync --frozen --extra test"
    )
    exact_source = (
        "Verify exact admitted source",
        f'make g0-admission-verify G0_ADMISSION_MANIFEST="{AUTHORITY_MANIFEST_PATH}"',
    )
    named_steps = [
        (item.get("name"), item.get("run")) for item in job["steps"] if item.get("name")
    ]
    assert named_steps.index(exact_source) < named_steps.index(
        ("Verify backend", "make backend-full")
    )
    assert _step(job, "Verify backend")["run"] == "make backend-full"
    uv_setup = _uses_step(job, "astral-sh/setup-uv@v6")
    assert uv_setup["with"]["version"] == "0.11.14"


def test_g0_admission_job_waits_for_authority_and_matrix_and_replays_gate() -> None:
    job = _workflow()["jobs"]["admission"]

    assert job["needs"] == ["authority", "verify"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == "45"
    assert job["permissions"] == {"contents": "read"}
    checkout = _uses_step(job, "actions/checkout@v4")
    assert checkout["with"]["persist-credentials"] == "false"
    _download_contract(job)
    python_setup = _uses_step(job, "actions/setup-python@v5")
    node_setup = _uses_step(job, "actions/setup-node@v4")
    assert python_setup["with"]["python-version"] == "3.13"
    assert node_setup["with"]["node-version"] == "22"
    assert node_setup["with"]["cache-dependency-path"] == "frontend/package-lock.json"
    npm_pin = _step(job, "Pin reviewed npm runtime")["run"].splitlines()
    assert npm_pin == [
        "npm install --global npm@11.17.0 --ignore-scripts",
        'test "$(npm --version)" = "11.17.0"',
    ]
    assert _step(job, "Install locked environments")["run"].splitlines() == [
        "uv sync --frozen --extra test",
        "make frontend-ci",
    ]

    supply_chain = _step(job, "Verify frontend supply chain")["run"]
    assert "npm --version >" in supply_chain
    assert "approve-scripts --allow-scripts-pending --json" in supply_chain
    assert "run verify:install-policy -- --pending" in supply_chain
    assert "npm --prefix frontend audit --audit-level=high --json" in supply_chain
    assert "npm --prefix frontend sbom --sbom-format cyclonedx --json" in supply_chain
    assert "sha256sum frontend/package.json frontend/package-lock.json frontend/.npmrc" in supply_chain
    assert 'exit "${audit_status}"' in supply_chain
    supply_chain_artifact = _step(job, "Upload frontend supply-chain evidence")
    assert supply_chain_artifact["if"] == "always()"
    assert supply_chain_artifact["uses"] == "actions/upload-artifact@v4"
    assert supply_chain_artifact["with"]["if-no-files-found"] == "error"

    expected = [
        (
            "Verify exact admitted source",
            f'make g0-admission-verify G0_ADMISSION_MANIFEST="{AUTHORITY_MANIFEST_PATH}"',
        ),
        (
            "Verify self-contained cleanroom",
            f'make g0-admission-cleanroom G0_ADMISSION_MANIFEST="{AUTHORITY_MANIFEST_PATH}"',
        ),
        ("Verify frontend supply chain", supply_chain),
        ("Verify frontend tests", "npm --prefix frontend test"),
        ("Verify frontend types", "npm --prefix frontend run typecheck"),
        ("Verify frontend production build", "npm --prefix frontend run build"),
        (
            "Verify clean release-ready checkout",
            (
                "make g0-admission-release-verify "
                f'G0_ADMISSION_MANIFEST="{AUTHORITY_MANIFEST_PATH}"'
            ),
        ),
    ]
    named_steps = [
        (item.get("name"), item.get("run")) for item in job["steps"] if item.get("name")
    ]
    assert named_steps.index(
        ("Pin reviewed npm runtime", _step(job, "Pin reviewed npm runtime")["run"])
    ) < named_steps.index(
        ("Install locked environments", _step(job, "Install locked environments")["run"])
    )
    positions = [named_steps.index(item) for item in expected]
    assert positions == sorted(positions)
