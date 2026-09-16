from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.evaluation import baseline
from evidence_rag.evaluation.golden import (
    EXPECTED_CASE_COUNT,
    EXPECTED_ELIGIBLE_COUNT,
    EXPECTED_INELIGIBLE_COUNT,
    PACKAGE_HASH,
    GoldenLoaderError,
    load_golden_cases,
    validate_golden_package,
)
from evidence_rag.evaluation.store import EvaluationStore
from evidence_rag.storage import SQLiteStore

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def golden_package() -> Any:
    return validate_golden_package(REPOSITORY_ROOT)


@pytest.fixture(scope="module")
def prepared_baseline(
    tmp_path_factory: pytest.TempPathFactory,
) -> baseline.PreparedBaseline:
    root = tmp_path_factory.mktemp("code-c0-03-prepare")
    return baseline.prepare_baseline_environment(root / "work", root=REPOSITORY_ROOT)


@pytest.fixture(scope="module")
def baseline_artifact(tmp_path_factory: pytest.TempPathFactory) -> Path:
    runs_dir = tmp_path_factory.mktemp("code-c0-03-runs")
    return baseline.run_baseline(root=REPOSITORY_ROOT, runs_dir=runs_dir)


def _load_into(database_path: Path, package: Any) -> Any:
    database = SQLiteStore(database_path)
    database.initialize()
    store = EvaluationStore(database)
    store.initialize()
    return store, load_golden_cases(store, package)


def test_loader_validates_release_hash_membership_and_round_trip(
    golden_package: Any,
    tmp_path: Path,
) -> None:
    assert golden_package.package_hash == PACKAGE_HASH
    assert golden_package.release_record_id == "code-golden-v2-release-001"
    assert len(golden_package.cases) == EXPECTED_CASE_COUNT
    assert len(golden_package.eligible_cases) == EXPECTED_ELIGIBLE_COUNT
    assert len(golden_package.ineligible_cases) == EXPECTED_INELIGIBLE_COUNT
    assert [case.canonical_id for case in golden_package.cases] == [
        f"code-golden-v2-{index:03d}" for index in range(1, 51)
    ]
    assert all(
        judgment.retrieval_unit_id is None
        for case in golden_package.cases
        for judgment in case.request.candidate_judgments
    )
    assert (
        sum(
            bool(case.request.code_profile and case.request.code_profile.required_paths)
            for case in golden_package.eligible_cases
        )
        == 12
    )

    first_store, first = _load_into(tmp_path / "first.sqlite3", golden_package)
    _, second = _load_into(tmp_path / "second.sqlite3", golden_package)
    assert first.case_ids == second.case_ids
    assert first.canonical_case_ids == second.canonical_case_ids
    assert len(first.case_ids) == EXPECTED_ELIGIBLE_COUNT
    cases = first_store.list_cases("project-code-golden-v2")
    assert len(cases) == EXPECTED_CASE_COUNT
    assert sum(bool(case["enabled"]) for case in cases) == EXPECTED_ELIGIBLE_COUNT
    assert sum(not bool(case["enabled"]) for case in cases) == EXPECTED_INELIGIBLE_COUNT


def test_loader_rejects_same_version_package_drift(tmp_path: Path) -> None:
    drift_root = tmp_path / "drift"
    shutil.copytree(REPOSITORY_ROOT / "evals" / "code", drift_root / "evals" / "code")
    shutil.copytree(
        REPOSITORY_ROOT / "tests" / "fixtures" / "code_golden" / "v2",
        drift_root / "tests" / "fixtures" / "code_golden" / "v2",
    )
    cases = drift_root / "evals" / "code" / "code-golden-v2.cases.yaml"
    cases.write_text(
        cases.read_text(encoding="utf-8") + "\n# forbidden same-version drift\n",
        encoding="utf-8",
    )

    with pytest.raises(GoldenLoaderError, match="failed closed"):
        validate_golden_package(drift_root)


def test_prepare_uses_three_real_sources_and_an_isolated_database(
    prepared_baseline: baseline.PreparedBaseline,
) -> None:
    assert prepared_baseline.database_path.is_relative_to(prepared_baseline.work_root)
    assert (
        prepared_baseline.database_path.resolve()
        != (REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3").resolve()
    )
    assert set(prepared_baseline.sources) == {
        "controlled_multilingual",
        "current_project_snapshot",
        "historical_error",
    }
    assert all(
        Path(source.path).is_relative_to(prepared_baseline.work_root)
        for source in prepared_baseline.sources.values()
    )
    assert prepared_baseline.observed_views == ("file.raw", "symbol.raw")
    assert prepared_baseline.observed_embedding_models == ("local-hash-v2",)
    with prepared_baseline.runtime.store.connection() as database:
        counts = {
            table: int(database.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("evaluation_cases", "evaluation_case_profiles")
        }
        run_count = int(database.execute("SELECT count(*) FROM evaluation_runs").fetchone()[0])
    assert counts == {
        "evaluation_cases": EXPECTED_CASE_COUNT,
        "evaluation_case_profiles": EXPECTED_CASE_COUNT,
    }
    assert run_count == 0


def test_workspace_fingerprint_guard_fails_closed_on_drift() -> None:
    before = {
        "head_commit": "a",
        "workspace_state_hash": "sha256:" + "1" * 64,
        "package_hash": PACKAGE_HASH,
    }
    baseline.require_unchanged_workspace(before, dict(before))
    after = {**before, "workspace_state_hash": "sha256:" + "2" * 64}
    with pytest.raises(baseline.BaselineError, match="workspace_state_hash"):
        baseline.require_unchanged_workspace(before, after)


def test_completed_baseline_is_immutable_deterministic_and_verifiable(
    baseline_artifact: Path,
) -> None:
    manifest = _json(baseline_artifact / "manifest.json")
    coverage = _json(baseline_artifact / "coverage.json")
    materialization = _json(baseline_artifact / "materialization.json")
    slices = _json(baseline_artifact / "slices.json")
    errors = _json(baseline_artifact / "error-analysis.json")
    latency = _json(baseline_artifact / "latency.json")
    storage = _json(baseline_artifact / "storage.json")
    verified_first = baseline.verify_artifact(baseline_artifact, root=REPOSITORY_ROOT)
    verified_second = baseline.verify_artifact(baseline_artifact, root=REPOSITORY_ROOT)

    assert verified_first == verified_second
    assert verified_first["qualification_record_hash"].startswith("sha256:")
    assert verified_first["manifest_canonical_hash"].startswith("sha256:")
    assert verified_first["security_scan"]["clean"] is True
    assert verified_first["security_scan"]["temporary_path_findings"] == 0
    assert verified_first["security_scan"]["credential_assignment_findings"] == 0
    assert verified_first["case_count"] == EXPECTED_ELIGIBLE_COUNT
    assert verified_first["result_count"] == EXPECTED_ELIGIBLE_COUNT
    assert manifest["status"] == "completed"
    assert manifest["baseline_qualified"] is True
    assert manifest["dataset"]["package_hash"] == PACKAGE_HASH
    assert manifest["implementation"]["input_fingerprint_matched"] is True
    assert manifest["config"] == baseline.BASELINE_CONFIG
    assert coverage["total_cases"] == EXPECTED_CASE_COUNT
    assert coverage["eligible_cases"] == EXPECTED_ELIGIBLE_COUNT
    assert coverage["ineligible_cases"] == EXPECTED_INELIGIBLE_COUNT
    assert len(coverage["ineligible"]) == EXPECTED_INELIGIBLE_COUNT
    assert materialization["database_isolated"] is True
    assert set(materialization["sources"]) == {
        "controlled_multilingual",
        "current_project_snapshot",
        "historical_error",
    }
    assert all("path" not in source for source in materialization["sources"].values())
    derived_slices = slices["derived_language_style_and_golden_slice_aggregates"]
    assert {
        item["slice"]["language"] for item in derived_slices if "language" in item["slice"]
    } == {"en", "zh"}
    assert {
        item["slice"]["query_style"] for item in derived_slices if "query_style" in item["slice"]
    } == {"identifier_heavy", "mixed", "natural_language"}
    assert {
        "top_misses",
        "same_name_errors",
        "wrong_or_missing_version",
        "graph_required_misses",
        "harmful_candidates",
        "duplicate_candidates",
        "zero_results",
        "candidate_channels",
    } <= set(errors)
    assert all(item["passed"] is False for item in errors["top_misses"])
    assert all(item["expected_answer_mode"] == "direct" for item in errors["graph_required_misses"])
    assert all(item["passed"] is True for item in errors["zero_results"])
    assert len(errors["wrong_or_missing_version"]) == 3
    assert all(
        item["wrong_version_status"] == "unavailable" for item in errors["wrong_or_missing_version"]
    )
    assert latency["p50"]["status"] == "available"
    assert latency["p95"]["status"] == "available"
    assert latency["dense_full_scan_time_ms"]["status"] == "unavailable"
    assert storage["sqlite_free_bytes"] >= 0
    assert storage["active_code_index_logical_bytes"] > 0

    database_path = baseline_artifact / "evaluation.sqlite3"
    baseline.assert_terminal_immutability(database_path, manifest["run_id"])
    with sqlite3.connect(database_path) as database:
        repository_paths = [
            str(row[0]) for row in database.execute("SELECT local_path FROM repositories")
        ]
        result_count = int(
            database.execute(
                "SELECT count(*) FROM evaluation_results WHERE evaluation_run_id=?",
                (manifest["run_id"],),
            ).fetchone()[0]
        )
    assert result_count == EXPECTED_ELIGIBLE_COUNT
    assert all(path.startswith("<system-temp>/code-c0-03") for path in repository_paths)


def test_artifact_verifier_rejects_report_tampering(
    baseline_artifact: Path,
    tmp_path: Path,
) -> None:
    tampered = tmp_path / "tampered"
    shutil.copytree(baseline_artifact, tampered)
    metrics_path = tampered / "metrics.json"
    metrics_path.write_text(
        metrics_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(baseline.BaselineError, match="file-set fingerprint"):
        baseline.verify_artifact(
            tampered,
            root=REPOSITORY_ROOT,
            qualification_dir=baseline_artifact.parent / baseline.QUALIFICATION_DIRECTORY,
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("dataset", "total_cases"), 999),
        (("dataset", "eligible_cases"), 999),
        (("dataset", "ineligible_cases"), 0),
        (("dataset", "case_membership_hash"), "sha256:" + ("0" * 64)),
        (("snapshot", "case_membership_hash"), "sha256:" + ("1" * 64)),
        (("snapshot", "observed_index_generations"), []),
        (("snapshot", "observed_retriever"), {"class": "fabricated"}),
        (("snapshot", "repositories"), []),
        (
            ("implementation", "input_fingerprint", "workspace_state_hash"),
            "sha256:" + ("2" * 64),
        ),
        (
            ("implementation", "input_fingerprint_after", "workspace_state_hash"),
            "sha256:" + ("3" * 64),
        ),
        (("started_at",), "1900-01-01T00:00:00+00:00"),
        (("security", "absolute_temporary_paths_included"), True),
    ],
    ids=[
        "total-membership",
        "eligible-membership",
        "ineligible-membership",
        "released-membership-hash",
        "evaluated-membership-hash",
        "generation",
        "retriever",
        "repository",
        "workspace-before",
        "workspace-after",
        "time",
        "security",
    ],
)
def test_manifest_field_tampering_fails_closed(
    baseline_artifact: Path,
    tmp_path: Path,
    path: tuple[str, ...],
    value: Any,
) -> None:
    tampered = tmp_path / "-".join(path)
    shutil.copytree(baseline_artifact, tampered)
    manifest_path = tampered / "manifest.json"
    manifest = _json(manifest_path)
    target = manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(baseline.BaselineError, match="not anchored"):
        baseline.verify_artifact(
            tampered,
            root=REPOSITORY_ROOT,
            qualification_dir=baseline_artifact.parent / baseline.QUALIFICATION_DIRECTORY,
        )


def test_joint_manifest_and_artifact_map_tampering_fails_without_requalification(
    baseline_artifact: Path,
    tmp_path: Path,
) -> None:
    tampered = tmp_path / "joint-tamper"
    shutil.copytree(baseline_artifact, tampered)
    metrics_path = tampered / "metrics.json"
    metrics_path.write_text(
        metrics_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    manifest_path = tampered / "manifest.json"
    manifest = _json(manifest_path)
    manifest["dataset"]["eligible_cases"] = 999
    manifest["artifacts"]["metrics.json"] = {
        "sha256": baseline._sha256_bytes(metrics_path.read_bytes()),
        "bytes": metrics_path.stat().st_size,
    }
    manifest["artifact_set_hash"] = baseline._fingerprint(manifest["artifacts"])
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(baseline.BaselineError, match="not anchored"):
        baseline.verify_artifact(
            tampered,
            root=REPOSITORY_ROOT,
            qualification_dir=baseline_artifact.parent / baseline.QUALIFICATION_DIRECTORY,
        )


def test_qualification_record_is_content_addressed_and_hash_chained(
    baseline_artifact: Path,
) -> None:
    records = baseline._read_qualification_ledger(
        baseline_artifact.parent / baseline.QUALIFICATION_DIRECTORY
    )
    assert len(records) == 1
    record = records[0]
    assert record["decision"] == "qualified"
    assert record["previous_record_hash"] == baseline.QUALIFICATION_GENESIS_HASH
    assert record["record_hash"] == baseline._qualification_record_hash(record)
    assert record["manifest_canonical_hash"] == baseline._fingerprint(
        _json(baseline_artifact / "manifest.json")
    )


def test_security_scanner_covers_hidden_history_text_and_credential_assignments(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "security-artifact"
    artifact.mkdir()
    database_path = artifact / "evaluation.sqlite3"
    with sqlite3.connect(database_path) as database:
        database.execute("CREATE TABLE hidden_history(diff_source TEXT, historical_patch TEXT)")
        database.execute(
            "INSERT INTO hidden_history VALUES (?, ?)",
            (
                "source=/var/folders/ab/hidden-source",
                "old=/private/tmp/hidden-diff password = request.password",
            ),
        )
        database.commit()
    (artifact / "nested.json").write_text(
        json.dumps({"locator": "/tmp/hidden-json", "identifier": "password"}) + "\n",
        encoding="utf-8",
    )

    scan = baseline.scan_artifact_security(artifact)
    assert scan["temporary_path_findings"] == 3
    assert scan["credential_assignment_findings"] == 0

    baseline._sanitize_database(database_path, None)
    (artifact / "nested.json").write_text(
        json.dumps({"locator": "<redacted-temp-path>", "identifier": "password"}) + "\n",
        encoding="utf-8",
    )
    clean = baseline.scan_artifact_security(artifact)
    assert clean["clean"] is True

    with sqlite3.connect(database_path) as database:
        database.execute(
            "UPDATE hidden_history SET historical_patch=?",
            ('password = "actual-secret-value"',),
        )
        database.commit()
    credential_scan = baseline.scan_artifact_security(artifact)
    assert credential_scan["credential_assignment_findings"] == 1


def test_failed_attempt_is_audited_but_not_qualified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "failed-runs"

    def fail_prepare(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("intentional prepare failure")

    monkeypatch.setattr(baseline, "prepare_baseline_environment", fail_prepare)
    with pytest.raises(baseline.BaselineError, match="intentional prepare failure"):
        baseline.run_baseline(root=REPOSITORY_ROOT, runs_dir=runs_dir)

    attempts = [path for path in runs_dir.iterdir() if path.is_dir()]
    assert len(attempts) == 1
    audit = _json(attempts[0] / "attempt-audit.json")
    assert audit["status"] == "failed"
    assert audit["baseline_qualified"] is False
    assert audit["run_id"] is None
    assert not (attempts[0] / "manifest.json").exists()
