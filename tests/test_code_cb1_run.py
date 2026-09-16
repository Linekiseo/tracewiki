from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.evaluation import cb1
from evidence_rag.evaluation.golden import (
    EXPECTED_CASE_COUNT,
    EXPECTED_ELIGIBLE_COUNT,
    EXPECTED_INELIGIBLE_COUNT,
    PACKAGE_HASH,
    validate_golden_package,
)
from evidence_rag.rag.sources.code import CodeExactSparseRetriever

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_RUNS = REPOSITORY_ROOT / "evals" / "code" / "runs"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _file_state(path: Path) -> tuple[bool, int | None, int | None]:
    if not path.exists():
        return False, None, None
    stat = path.stat()
    return True, stat.st_size, stat.st_mtime_ns


def _tree_state(path: Path) -> dict[str, tuple[int, int]]:
    return {
        item.relative_to(path).as_posix(): (item.stat().st_size, item.stat().st_mtime_ns)
        for item in path.rglob("*")
        if item.is_file()
    }


class _FakeExactSparseRetriever:
    retriever_version = "fake-code-exact-sparse-test-v1"
    fusion = "weighted-rrf-test-v1"
    channels = ("exact", "sparse")
    index_family = "ast-v2"

    def __init__(self, runtime: Any) -> None:
        self.store = runtime.store
        self.requests: list[Any] = []
        self.profiles: list[Any] = []

    def search(self, request: Any, profile: Any) -> dict[str, Any]:
        self.requests.append(request)
        self.profiles.append(profile)
        generations = self.store.active_code_generations(
            project_id=request.scope.project_id,
            repository_ids=request.scope.repository_ids or None,
        )
        return {
            "results": [],
            "relations": [],
            "index_generation": sorted(generations.values()),
            "trace": {
                "duration_ms": 1.25,
                "exact_outcome": "complete_no_match",
                "sparse_outcome": "complete_no_match",
                "exact_candidates": 0,
                "sparse_candidates": 0,
            },
        }


def _fake_factory(runtime: Any) -> _FakeExactSparseRetriever:
    return _FakeExactSparseRetriever(runtime)


@pytest.fixture(scope="module")
def cb1_test_artifact(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, tuple[bool, int | None, int | None], dict[str, tuple[int, int]]]:
    production_database = REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3"
    production_before = _file_state(production_database)
    runs_before = _tree_state(REPOSITORY_RUNS)
    runs_dir = tmp_path_factory.mktemp("code-c-b1-test-runs")
    artifact = cb1.run_cb1(
        root=REPOSITORY_ROOT,
        runs_dir=runs_dir,
        execution_mode="test",
        retriever_factory=_fake_factory,
    )
    assert _file_state(production_database) == production_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before
    return artifact, production_before, runs_before


def test_preparation_contract_is_pending_and_creates_no_run_artifact() -> None:
    before = _tree_state(REPOSITORY_RUNS)
    status = cb1.preparation_status(root=REPOSITORY_ROOT)

    assert status["status"] == "PREPARED"
    assert status["execution_status"] == "pending"
    assert status["treatment_qualified"] is False
    assert status["dataset"] == {
        "id": "code-golden",
        "version": "code-golden-v2",
        "package_hash": PACKAGE_HASH,
        "release_record_id": "code-golden-v2-release-001",
        "case_membership_hash": validate_golden_package(REPOSITORY_ROOT).membership_hash,
        "total_cases": EXPECTED_CASE_COUNT,
        "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
        "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
    }
    assert status["baseline"]["run_id"] == cb1.CB0_QUALIFIED_RUN_ID
    assert status["config_contract"]["rag_code_unit_builder"] == "ast-v2"
    assert status["config_contract"]["rag_code_dense_index"] is False
    assert status["config_contract"]["retriever"]["class"] == "CodeExactSparseRetriever"
    if status["real_retriever"]["status"] == "ready":
        assert status["config_contract"]["retriever"]["version"] == {
            "status": "ready",
            "value": "code-exact-sparse-v1",
            "source": ("evidence_rag.rag.sources.code.retrieval_v2.RETRIEVER_VERSION"),
        }
        assert status["config_contract"]["retriever"]["fusion"]["value"] == (
            "deterministic-weighted-rrf-v1"
        )
    else:
        assert status["config_contract"]["retriever"]["version"]["status"] == "pending"
    assert status["config_contract"]["top_k"] == 20
    assert set(status["required_reporting"]) == set(cb1.REQUIRED_REPORTING)
    assert all(item["status"] == "pending" for item in status["required_reporting"].values())
    assert _tree_state(REPOSITORY_RUNS) == before


def test_fake_retriever_cannot_enter_qualified_or_repository_run_paths(
    tmp_path: Path,
) -> None:
    qualified_runs = tmp_path / "qualified"
    with pytest.raises(cb1.CB1Error, match="forbids retriever injection"):
        cb1.run_cb1(
            root=REPOSITORY_ROOT,
            runs_dir=qualified_runs,
            execution_mode="qualified",
            retriever_factory=_fake_factory,
        )
    assert not qualified_runs.exists()

    with pytest.raises(cb1.CB1Error, match="may not write under evals/code/runs"):
        cb1.run_cb1(
            root=REPOSITORY_ROOT,
            runs_dir=REPOSITORY_RUNS,
            execution_mode="test",
            retriever_factory=_fake_factory,
        )


def test_real_retriever_boundary_uses_c2_published_versions() -> None:
    descriptor = cb1._retriever_descriptor(CodeExactSparseRetriever(object()))  # type: ignore[arg-type]

    assert descriptor.module == "evidence_rag.rag.sources.code.retrieval_v2"
    assert descriptor.class_name == "CodeExactSparseRetriever"
    assert descriptor.version == "code-exact-sparse-v1"
    assert descriptor.fusion == "deterministic-weighted-rrf-v1"
    assert descriptor.channels == ("exact", "sparse")
    assert descriptor.index_family == "ast-v2"


def test_test_harness_uses_ast_units_and_fixed_released_denominator(
    cb1_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
) -> None:
    artifact, production_before, runs_before = cb1_test_artifact
    manifest = _json(artifact / "manifest.json")
    materialization = _json(artifact / "materialization.json")
    coverage = _json(artifact / "coverage.json")
    attempt = _json(artifact / "attempt-audit.json")
    package = validate_golden_package(REPOSITORY_ROOT)

    assert manifest["execution_mode"] == "test"
    assert manifest["treatment_qualified"] is False
    assert manifest["dataset"]["package_hash"] == PACKAGE_HASH
    assert manifest["dataset"]["case_membership_hash"] == package.membership_hash
    assert manifest["dataset"]["eligible_cases"] == EXPECTED_ELIGIBLE_COUNT
    assert manifest["baseline"]["run_id"] == cb1.CB0_QUALIFIED_RUN_ID
    assert manifest["baseline"]["qualification_record_hash"].startswith("sha256:")
    assert manifest["config"]["rag_code_unit_builder"] == "ast-v2"
    assert manifest["config"]["rag_code_dense_index"] is False
    assert manifest["config"]["schema_version"] == "code-c-b1-config-snapshot-v2"
    assert manifest["config"]["retriever"] == {
        "module": __name__,
        "class": "_FakeExactSparseRetriever",
        "version": "fake-code-exact-sparse-test-v1",
        "fusion": "weighted-rrf-test-v1",
        "channels": ["exact", "sparse"],
        "index_family": "ast-v2",
    }
    assert manifest["config"]["top_k"] == 20
    assert manifest["config"]["sparse_index_version"] == cb1.SPARSE_INDEX_VERSION
    assert manifest["config"]["graph_candidate"] is False
    assert manifest["config"]["embedding"] == "disabled"

    assert materialization["database_isolated"] is True
    assert materialization["production_database_accessed"] is False
    assert materialization["unit_builder"] == "ast-v2"
    assert materialization["ast_units"] > 0
    assert materialization["fts_rows"] == materialization["ast_units"]
    assert materialization["ingest_time_ms"]["status"] == "available"
    assert materialization["ingest_time_ms"]["value"] > 0
    assert materialization["cb0_ingest_time_comparison"]["baseline"]["status"] == ("unavailable")
    assert materialization["cb0_ingest_time_comparison"]["delta"]["status"] == ("unavailable")
    assert set(materialization["sources"]) == {
        "controlled_multilingual",
        "current_project_snapshot",
        "historical_error",
    }
    assert all("path" not in source for source in materialization["sources"].values())
    assert len(materialization["publications"]) == 3
    assert all(
        item["builder"] == cb1.AST_BUILDER_VERSION for item in materialization["publications"]
    )

    assert coverage["total_cases"] == EXPECTED_CASE_COUNT
    assert coverage["eligible_cases"] == EXPECTED_ELIGIBLE_COUNT
    assert coverage["ineligible_cases"] == EXPECTED_INELIGIBLE_COUNT
    assert coverage["result_count"] == EXPECTED_ELIGIBLE_COUNT
    assert len(coverage["enabled_case_names"]) == EXPECTED_ELIGIBLE_COUNT
    assert len(coverage["ineligible"]) == EXPECTED_INELIGIBLE_COUNT
    slice_membership = coverage["required_slice_membership"]
    assert slice_membership["exact"]["case_count"] == 7
    assert slice_membership["identifier"]["case_count"] == 9
    assert slice_membership["low_overlap"]["case_count"] == 7
    assert slice_membership["same_name_hard_negative"]["case_count"] == 4
    assert coverage["denominator_policy"] == {
        "eligible_case_count": EXPECTED_ELIGIBLE_COUNT,
        "case_membership_hash": package.membership_hash,
        "ineligible_cases_are_reported_not_silently_dropped": True,
    }

    assert attempt["execution_mode"] == "test"
    assert attempt["treatment_qualified"] is False
    assert attempt["baseline_run_id"] == cb1.CB0_QUALIFIED_RUN_ID
    assert _file_state(REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3") == production_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_reports_include_required_absolute_values_deltas_and_unavailability(
    cb1_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
) -> None:
    artifact = cb1_test_artifact[0]
    metrics = _json(artifact / "metrics.json")
    slices = _json(artifact / "slices.json")
    errors = _json(artifact / "error-analysis.json")
    latency = _json(artifact / "latency.json")
    storage = _json(artifact / "storage.json")
    comparison = metrics["cb0_comparison"]

    assert comparison["baseline_run_id"] == cb1.CB0_QUALIFIED_RUN_ID
    assert comparison["treatment_run_id"] == metrics["run_id"]
    assert comparison["selectors"]["overall"]["case_count"] == EXPECTED_ELIGIBLE_COUNT
    rows = {(row["scope"], row["metric_name"]): row for row in comparison["comparisons"]}
    required = {
        ("overall", "expected_locator_recall@10"),
        ("overall", "entity_duplicate_rate@10"),
        ("overall", "unit_duplicate_rate@10"),
        ("overall", "latency_p95_ms"),
        ("exact", "entity_recall@10"),
        ("identifier", "entity_recall@10"),
        ("low_overlap", "entity_recall@10"),
        ("same_name_hard_negative", "hard_negative_error@10"),
    }
    assert required <= set(rows)
    for key in required:
        row = rows[key]
        assert row["same_case_membership"] is True
        assert row["baseline"]["membership_case_count"] == row["membership"]["case_count"]
        assert row["treatment"]["membership_case_count"] == row["membership"]["case_count"]
        assert row["delta"]["status"] in {"available", "unavailable"}
    assert rows[("overall", "unit_duplicate_rate@10")]["baseline"]["status"] == "unavailable"
    assert rows[("overall", "unit_duplicate_rate@10")]["delta"]["status"] == "unavailable"
    assert metrics["acceptance"]["decision"] == "not-qualified"
    assert {gate["gate"] for gate in metrics["acceptance"]["gates"]} == {
        "exact_non_regression",
        "identifier_non_regression",
        "locator_non_regression",
    }

    required_slices = {row["scope"] for row in slices["required_treatment_slices"]}
    assert required_slices == {
        "exact",
        "identifier",
        "low_overlap",
        "same_name_hard_negative",
    }
    assert "same_name_hard_negative_slice" in errors
    assert "duplicate_comparison" in errors
    assert len(errors["unavailable_cases"]) == EXPECTED_INELIGIBLE_COUNT
    assert latency["retrieval_p95"]["baseline"]["status"] == "available"
    assert latency["retrieval_p95"]["treatment"]["status"] == "available"
    assert latency["retrieval_p95"]["delta"]["status"] == "available"
    assert len(latency["cases"]) == EXPECTED_ELIGIBLE_COUNT

    assert storage["ast_unit_count"] > 0
    assert storage["fts_row_count"] == storage["ast_unit_count"]
    assert storage["vector_count"] == 0
    assert storage["exact_sparse_index_logical_bytes"] > 0
    assert storage["exact_sparse_index_physical_bytes"]["status"] in {
        "available",
        "unavailable",
    }
    assert storage["cb0_comparison"]["logical_index_bytes_delta"]["status"] == "available"
    assert storage["cb0_comparison"]["physical_index_bytes_delta"]["status"] == "unavailable"


def test_verify_only_is_deterministic_secret_free_and_does_not_need_retriever(
    cb1_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, production_before, runs_before = cb1_test_artifact

    def fail_if_loaded(runtime: Any) -> Any:
        raise AssertionError("verify-only must not instantiate a retriever")

    monkeypatch.setattr(cb1, "_load_real_retriever", fail_if_loaded)
    first = cb1.verify_artifact(
        artifact,
        root=REPOSITORY_ROOT,
        allow_test_artifact=True,
    )
    second = cb1.verify_artifact(
        artifact,
        root=REPOSITORY_ROOT,
        allow_test_artifact=True,
    )

    assert first == second
    assert first["status"] == "verified-test-artifact"
    assert first["treatment_qualified"] is False
    assert first["case_count"] == EXPECTED_ELIGIBLE_COUNT
    assert first["result_count"] == EXPECTED_ELIGIBLE_COUNT
    assert first["baseline_run_id"] == cb1.CB0_QUALIFIED_RUN_ID
    assert first["security_scan"]["clean"] is True
    assert first["security_scan"]["temporary_path_findings"] == 0
    assert first["security_scan"]["credential_assignment_findings"] == 0
    assert not list(artifact.glob("evaluation.sqlite3-*"))
    assert set(path.name for path in artifact.iterdir()) == cb1.EXPECTED_ARTIFACT_ENTRIES
    with pytest.raises(cb1.CB1Error, match="allow_test_artifact"):
        cb1.verify_artifact(artifact, root=REPOSITORY_ROOT)

    database_path = artifact / "evaluation.sqlite3"
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        run_count = int(database.execute("SELECT count(*) FROM evaluation_runs").fetchone()[0])
        result_count = int(
            database.execute("SELECT count(*) FROM evaluation_results").fetchone()[0]
        )
        repository_paths = [
            str(row[0]) for row in database.execute("SELECT local_path FROM repositories")
        ]
    assert run_count == 1
    assert result_count == EXPECTED_ELIGIBLE_COUNT
    assert all("/tmp/" not in path and "/var/folders/" not in path for path in repository_paths)
    assert _file_state(REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3") == production_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_verify_only_rejects_report_tampering(
    cb1_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
    tmp_path: Path,
) -> None:
    artifact = cb1_test_artifact[0]
    tampered = tmp_path / "tampered"
    shutil.copytree(artifact, tampered)
    metrics_path = tampered / "metrics.json"
    metrics_path.write_text(
        metrics_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(cb1.CB1Error, match="file-set fingerprint"):
        cb1.verify_artifact(
            tampered,
            root=REPOSITORY_ROOT,
            allow_test_artifact=True,
        )
