from __future__ import annotations

import importlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.evaluation import cb2
from evidence_rag.evaluation.golden import (
    EXPECTED_CASE_COUNT,
    EXPECTED_ELIGIBLE_COUNT,
    EXPECTED_INELIGIBLE_COUNT,
    PACKAGE_HASH,
    validate_golden_package,
)
from evidence_rag.rag.sources.code.embedding_v2 import CodeEmbeddingIndexer

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


class _FakeDenseProfilePublisher:
    publisher_version = "fake-code-dense-publisher-test-v1"

    def __init__(self, runtime: Any) -> None:
        self.store = runtime.store

    def publish_profile(
        self,
        *,
        profile: Any,
        project_id: str,
        generation_id: str,
        repository_id: str,
        allowed_acl_refs: list[str],
    ) -> dict[str, Any]:
        assert project_id == cb2.PROJECT_ID
        assert allowed_acl_refs == [cb2.ACL_REF]
        with self.store.connection() as database:
            units = [
                dict(row)
                for row in database.execute(
                    """SELECT * FROM code_retrieval_units
                       WHERE project_id=? AND repository_id=? AND generation_id=?
                       ORDER BY rowid""",
                    (project_id, repository_id, generation_id),
                ).fetchall()
            ]
        result = CodeEmbeddingIndexer(self.store).index(units, profile)
        previous = self.store.get_code_index_publication(
            generation_id,
            repository_id=repository_id,
            active_only=True,
        )
        assert previous is not None
        validation = dict(previous["validation"])
        capabilities = dict(validation["capabilities"])
        capabilities["dense_retrieval"] = True
        validation["capabilities"] = capabilities
        profiles = dict(validation.get("embedding_profiles") or {})
        profiles[profile.id] = {
            "cache_hits": result.cache_hits,
            "cache_misses": result.cache_misses,
            "profile": profile.canonical_snapshot(),
            "provider": cb2.PROVIDER_PROVENANCE,
            "publication_version": self.publisher_version,
            "status": "ready",
            "unit_count": len(units),
            "vector_count": result.indexed,
        }
        validation["embedding_profiles"] = profiles
        validation["cb2_fake_test_publication"] = {"isolated_test_only": True}
        self.store.upsert_code_index_publication(
            {
                "generation_id": generation_id,
                "repository_id": repository_id,
                "project_id": project_id,
                "builder": previous["builder"],
                "sparse": previous["sparse"],
                "embedding": f"{profile.id}@{profile.revision}",
                "graph": previous["graph"],
                "status": "published",
                "validation": validation,
            }
        )
        return {
            "status": "published",
            "generation_id": generation_id,
            "repository_id": repository_id,
            "publication_version": self.publisher_version,
            "cache_hits": result.cache_hits,
            "cache_misses": result.cache_misses,
            "indexed": result.indexed,
            "skipped": result.skipped,
            "fallbacks": result.fallbacks,
            "provenance": cb2.PROVIDER_PROVENANCE,
        }


class _FakeHybridV2Retriever:
    retriever_version = "fake-code-hybrid-test-v1"
    fusion = "fake-exact-sparse-dense-rrf-v1"
    channels = ("exact", "sparse", "dense")
    index_family = "ast-v2"

    def __init__(self, runtime: Any, profile: Any) -> None:
        self.store = runtime.store
        self.profile = profile
        self.requests: list[Any] = []
        self.query_profiles: list[Any] = []

    def search(self, request: Any, query_profile: Any) -> dict[str, Any]:
        self.requests.append(request)
        self.query_profiles.append(query_profile)
        assert query_profile.budget.dense_candidates == cb2.CHANNEL_BUDGET["dense_candidates"]
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
                "dense_outcome": "complete_no_match",
                "exact_candidates": 0,
                "sparse_candidates": 0,
                "dense_candidates": 0,
                "dense_contribution_count": 0,
                "dense_only_contribution_count": 0,
            },
        }


def _fake_publisher_factory(runtime: Any, profile: Any) -> _FakeDenseProfilePublisher:
    assert profile.canonical_snapshot() == cb2.PROFILE_SNAPSHOT
    return _FakeDenseProfilePublisher(runtime)


def _fake_retriever_factory(
    runtime: Any,
    profile: Any,
) -> _FakeHybridV2Retriever:
    return _FakeHybridV2Retriever(runtime, profile)


@pytest.fixture(scope="module")
def cb2_test_artifact(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, tuple[bool, int | None, int | None], dict[str, tuple[int, int]]]:
    production_database = REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3"
    production_before = _file_state(production_database)
    runs_before = _tree_state(REPOSITORY_RUNS)
    runs_dir = tmp_path_factory.mktemp("code-c-b2-test-runs")
    artifact = cb2.run_cb2(
        root=REPOSITORY_ROOT,
        runs_dir=runs_dir,
        execution_mode="test",
        publisher_factory=_fake_publisher_factory,
        retriever_factory=_fake_retriever_factory,
    )
    assert _file_state(production_database) == production_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before
    return artifact, production_before, runs_before


def test_preparation_contract_is_prepared_and_creates_no_run() -> None:
    before = _tree_state(REPOSITORY_RUNS)
    status = cb2.preparation_status(root=REPOSITORY_ROOT)
    package = validate_golden_package(REPOSITORY_ROOT)

    assert status["status"] == "PREPARED"
    assert status["execution_status"] == "pending-c3-02-gate"
    assert status["treatment_qualified"] is False
    assert status["artifact_created"] is False
    assert status["dataset"] == {
        "id": "code-golden",
        "version": "code-golden-v2",
        "package_hash": PACKAGE_HASH,
        "release_record_id": "code-golden-v2-release-001",
        "case_membership_hash": package.membership_hash,
        "total_cases": EXPECTED_CASE_COUNT,
        "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
        "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
    }
    assert status["qualified_baseline"]["run_id"] == cb2.CB0_QUALIFIED_RUN_ID
    assert status["audit_control"] == {
        "run_id": cb2.CB1_AUDIT_RUN_ID,
        "treatment_qualified": False,
        "manifest_canonical_hash": status["audit_control"]["manifest_canonical_hash"],
        "qualification_use": "forbidden",
    }
    assert status["config_contract"]["profile"] == cb2.PROFILE_SNAPSHOT
    assert status["config_contract"]["provider_provenance"] == cb2.PROVIDER_PROVENANCE
    assert status["config_contract"]["channel_budget"]["dense_candidates"] > 0
    assert all(
        candidate["status"] == "unavailable"
        for candidate in status["config_contract"]["optional_candidates"]
    )
    assert all(item["status"] == "pending" for item in status["required_reporting"].values())
    assert _tree_state(REPOSITORY_RUNS) == before


def test_fake_components_and_missing_gate_cannot_enter_qualified_or_repository_paths(
    tmp_path: Path,
) -> None:
    with pytest.raises(cb2.CB2Error, match="Gate approval"):
        cb2.run_cb2(root=REPOSITORY_ROOT, runs_dir=tmp_path / "missing-gate")

    with pytest.raises(cb2.CB2Error, match="forbids component injection"):
        cb2.run_cb2(
            root=REPOSITORY_ROOT,
            runs_dir=tmp_path / "fake-qualified",
            publisher_factory=_fake_publisher_factory,
            retriever_factory=_fake_retriever_factory,
            c3_02_gate_approved=True,
        )

    with pytest.raises(cb2.CB2Error, match="may not write under evals/code/runs"):
        cb2.run_cb2(
            root=REPOSITORY_ROOT,
            runs_dir=REPOSITORY_RUNS,
            execution_mode="test",
            publisher_factory=_fake_publisher_factory,
            retriever_factory=_fake_retriever_factory,
        )


def test_ready_public_component_contract_matches_fixed_local_profile() -> None:
    status = cb2.preparation_status(root=REPOSITORY_ROOT)
    if status["production_components"]["status"] != "ready":
        pytest.skip("parallel C3-02 implementation is not ready")
    public = importlib.import_module(cb2.PUBLIC_CODE_MODULE)
    publisher = public.CodeDenseProfilePublisher(object())
    retriever = public.CodeHybridV2Retriever(object())

    publisher_descriptor = cb2._publisher_descriptor(publisher)
    retriever_descriptor = cb2._retriever_descriptor(retriever)
    assert publisher_descriptor.class_name == cb2.REAL_PUBLISHER_CLASS
    assert publisher_descriptor.version == "code-dense-publication-v1"
    assert retriever_descriptor.class_name == cb2.REAL_RETRIEVER_CLASS
    assert retriever_descriptor.version == "code-hybrid-v2"
    assert retriever_descriptor.fusion == "deterministic-weighted-rrf-v2"
    assert retriever_descriptor.channels == ("exact", "sparse", "dense")
    assert retriever.dense_retriever.embedding_profile.canonical_snapshot() == cb2.PROFILE_SNAPSHOT


def test_test_harness_fixes_anchors_denominator_and_dense_publication(
    cb2_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
) -> None:
    artifact, production_before, runs_before = cb2_test_artifact
    manifest = _json(artifact / "manifest.json")
    materialization = _json(artifact / "materialization.json")
    coverage = _json(artifact / "coverage.json")
    attempt = _json(artifact / "attempt-audit.json")
    package = validate_golden_package(REPOSITORY_ROOT)

    assert manifest["execution_mode"] == "test"
    assert manifest["c3_02_gate_approved"] is False
    assert manifest["treatment_qualified"] is False
    assert manifest["dataset"]["package_hash"] == PACKAGE_HASH
    assert manifest["dataset"]["case_membership_hash"] == package.membership_hash
    assert manifest["dataset"]["eligible_cases"] == EXPECTED_ELIGIBLE_COUNT
    controls = manifest["controls"]
    assert controls["qualified_baseline"]["run_id"] == cb2.CB0_QUALIFIED_RUN_ID
    assert controls["audit_control"]["run_id"] == cb2.CB1_AUDIT_RUN_ID
    assert controls["audit_control"]["treatment_qualified"] is False
    assert controls["audit_control"]["qualification_use"] == "forbidden"
    assert manifest["config"]["profile"] == cb2.PROFILE_SNAPSHOT
    assert manifest["config"]["provider_provenance"] == cb2.PROVIDER_PROVENANCE
    assert manifest["config"]["publisher"]["class"] == "_FakeDenseProfilePublisher"
    assert manifest["config"]["retriever"]["class"] == "_FakeHybridV2Retriever"
    assert manifest["config"]["retriever"]["channels"] == ["exact", "sparse", "dense"]

    assert materialization["database_isolated"] is True
    assert materialization["production_database_accessed"] is False
    assert materialization["unit_builder"] == "ast-v2"
    assert materialization["profile"] == cb2.PROFILE_SNAPSHOT
    assert materialization["provider_provenance"] == cb2.PROVIDER_PROVENANCE
    assert materialization["network_used"] is False
    assert materialization["downloads_performed"] is False
    assert materialization["vectors"]["complete_against_ast_units"] is True
    assert materialization["vectors"]["rows"] == materialization["ast_units"]
    assert materialization["vectors"]["skipped_reported"] == 0
    assert materialization["embedding_cache"]["misses"] > 0
    assert len(materialization["publications"]) == 3
    assert all(
        item["validation"]["capabilities"]["dense_retrieval"] is True
        for item in materialization["publications"]
    )
    assert materialization["dense_index_time_ms"]["p95"]["status"] == "available"
    assert materialization["compute"]["cpu_logical_count"]["status"] in {
        "available",
        "unavailable",
    }

    assert coverage["total_cases"] == EXPECTED_CASE_COUNT
    assert coverage["eligible_cases"] == EXPECTED_ELIGIBLE_COUNT
    assert coverage["ineligible_cases"] == EXPECTED_INELIGIBLE_COUNT
    assert coverage["result_count"] == EXPECTED_ELIGIBLE_COUNT
    assert len(coverage["enabled_case_names"]) == EXPECTED_ELIGIBLE_COUNT
    assert coverage["denominator_policy"]["case_membership_hash"] == package.membership_hash

    assert attempt["execution_mode"] == "test"
    assert attempt["c3_02_gate_approved"] is False
    assert attempt["treatment_qualified"] is False
    assert attempt["qualified_baseline_run_id"] == cb2.CB0_QUALIFIED_RUN_ID
    assert attempt["audit_control_run_id"] == cb2.CB1_AUDIT_RUN_ID
    assert attempt["audit_control_is_qualified_baseline"] is False
    assert _file_state(REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3") == production_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_reports_cover_dual_controls_dense_effects_latency_and_storage(
    cb2_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
) -> None:
    artifact = cb2_test_artifact[0]
    metrics = _json(artifact / "metrics.json")
    slices = _json(artifact / "slices.json")
    errors = _json(artifact / "error-analysis.json")
    latency = _json(artifact / "latency.json")
    storage = _json(artifact / "storage.json")

    cb0 = metrics["cb0_qualified_baseline_comparison"]
    audit = metrics["cb1_not_qualified_audit_comparison"]
    assert cb0["control_role"] == "qualified-baseline"
    assert cb0["control_is_qualified_baseline"] is True
    assert audit["control_role"] == "not-qualified-audit-control"
    assert audit["control_is_qualified_baseline"] is False
    for comparison in (cb0, audit):
        rows = {(row["scope"], row["metric_name"]): row for row in comparison["comparisons"]}
        required = {
            (scope, metric)
            for scope in ("overall", "exact", "identifier", "low_overlap")
            for metric in (
                "entity_recall@10",
                "expected_locator_recall@10",
                "mrr@10",
                "ndcg@10",
            )
        }
        required |= {
            ("overall", "hard_negative_error@10"),
            ("overall", "harmful_candidate_rate@10"),
            ("overall", "entity_duplicate_rate@10"),
            ("overall", "unit_duplicate_rate@10"),
            ("same_name_hard_negative", "harmful_candidate_rate@10"),
        }
        assert required <= set(rows)
        assert all(rows[key]["same_case_membership"] is True for key in required)

    acceptance = metrics["acceptance"]
    assert acceptance["decision"] == "not-qualified"
    gates = {gate["gate"]: gate for gate in acceptance["gates"]}
    assert gates["production_component_identity"]["passed"] is False
    assert gates["complete_33_case_coverage"]["passed"] is True
    assert gates["artifact_security"]["passed"] is True
    assert gates["cb1_audit_low_overlap_strict_improvement"]["passed"] is False

    assert set(slices["required_treatment_slices"]) == {
        "cb0_qualified_baseline",
        "cb1_not_qualified_audit",
    }
    assert errors["dense_contribution"]["case_count"] == 0
    assert errors["cb1_zero_result_recovery"]["recovered_count"] == 0
    assert errors["low_overlap_non_improvements"]
    assert "same_name_comparison" in errors
    assert "duplicate_comparison" in errors
    assert len(errors["unavailable_cases"]) == EXPECTED_INELIGIBLE_COUNT

    assert latency["ingest"]["p95_ms"]["status"] == "available"
    assert latency["dense_index"]["p95_ms"]["status"] == "available"
    assert latency["query_p95"]["cb0_qualified_baseline"]["delta"]["status"] == "available"
    assert latency["query_p95"]["cb1_not_qualified_audit"]["delta"]["status"] == "available"
    assert len(latency["cases"]) == EXPECTED_ELIGIBLE_COUNT

    assert storage["ast_unit_count"] > 0
    assert storage["fts_row_count"] == storage["ast_unit_count"]
    assert storage["vector_count"] == storage["ast_unit_count"]
    assert storage["vector_index_logical_bytes"] == (
        storage["vector_count"] * cb2.PROFILE_SNAPSHOT["dimension"] * 4
    )
    assert storage["total_retrieval_index_logical_bytes"] == (
        storage["exact_sparse_index_logical_bytes"] + storage["vector_index_logical_bytes"]
    )
    assert storage["cb1_not_qualified_audit_comparison"]["is_qualified_baseline"] is False


def test_verify_only_is_deterministic_secret_free_and_component_free(
    cb2_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact, production_before, runs_before = cb2_test_artifact

    def fail_if_loaded(runtime: Any, profile: Any) -> Any:
        raise AssertionError("verify-only must not instantiate production components")

    monkeypatch.setattr(cb2, "_load_real_publisher", fail_if_loaded)
    monkeypatch.setattr(cb2, "_load_real_retriever", fail_if_loaded)
    first = cb2.verify_artifact(
        artifact,
        root=REPOSITORY_ROOT,
        allow_test_artifact=True,
    )
    second = cb2.verify_artifact(
        artifact,
        root=REPOSITORY_ROOT,
        allow_test_artifact=True,
    )

    assert first == second
    assert first["status"] == "verified-test-artifact"
    assert first["treatment_qualified"] is False
    assert first["case_count"] == EXPECTED_ELIGIBLE_COUNT
    assert first["result_count"] == EXPECTED_ELIGIBLE_COUNT
    assert first["qualified_baseline_run_id"] == cb2.CB0_QUALIFIED_RUN_ID
    assert first["audit_control_run_id"] == cb2.CB1_AUDIT_RUN_ID
    assert first["audit_control_is_qualified_baseline"] is False
    assert first["security_scan"]["clean"] is True
    assert first["security_scan"]["temporary_path_findings"] == 0
    assert first["security_scan"]["credential_assignment_findings"] == 0
    assert not list(artifact.glob("evaluation.sqlite3-*"))
    assert {path.name for path in artifact.iterdir()} == cb2.EXPECTED_ARTIFACT_ENTRIES
    with pytest.raises(cb2.CB2Error, match="allow_test_artifact"):
        cb2.verify_artifact(artifact, root=REPOSITORY_ROOT)

    database_path = artifact / "evaluation.sqlite3"
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as database:
        run_count = int(database.execute("SELECT count(*) FROM evaluation_runs").fetchone()[0])
        result_count = int(
            database.execute("SELECT count(*) FROM evaluation_results").fetchone()[0]
        )
        vector_count = int(database.execute("SELECT count(*) FROM code_unit_vectors").fetchone()[0])
        unit_count = int(
            database.execute("SELECT count(*) FROM code_retrieval_units").fetchone()[0]
        )
        repository_paths = [
            str(row[0]) for row in database.execute("SELECT local_path FROM repositories")
        ]
    assert run_count == 1
    assert result_count == EXPECTED_ELIGIBLE_COUNT
    assert vector_count == unit_count
    assert all("/tmp/" not in path and "/var/folders/" not in path for path in repository_paths)
    assert _file_state(REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3") == production_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_verify_only_rejects_report_tampering(
    cb2_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
    tmp_path: Path,
) -> None:
    artifact = cb2_test_artifact[0]
    tampered = tmp_path / "tampered"
    shutil.copytree(artifact, tampered)
    metrics_path = tampered / "metrics.json"
    metrics_path.write_text(
        metrics_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(cb2.CB2Error, match="file-set fingerprint"):
        cb2.verify_artifact(
            tampered,
            root=REPOSITORY_ROOT,
            allow_test_artifact=True,
        )


def test_workspace_drift_fails_closed_without_publishing_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs_dir = tmp_path / "drift-runs"
    real_fingerprint = cb2.workspace_input_fingerprint(
        REPOSITORY_ROOT,
        runs_dir,
    )
    observations = iter(
        (
            real_fingerprint,
            {**real_fingerprint, "workspace_state_hash": "sha256:" + ("f" * 64)},
        )
    )
    monkeypatch.setattr(
        cb2,
        "workspace_input_fingerprint",
        lambda root, output: next(observations),
    )

    with pytest.raises(cb2.CB2Error, match="workspace input fingerprint drifted"):
        cb2.run_cb2(
            root=REPOSITORY_ROOT,
            runs_dir=runs_dir,
            execution_mode="test",
            publisher_factory=_fake_publisher_factory,
            retriever_factory=_fake_retriever_factory,
        )
    assert not runs_dir.exists()
