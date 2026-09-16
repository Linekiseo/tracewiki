from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.evaluation import baseline, cb5
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
    publisher_version = "fake-code-dense-publisher-cb5-test-v1"

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
        assert project_id == cb5.PROJECT_ID
        assert allowed_acl_refs == [cb5.ACL_REF]
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
            "provider": cb5.cb2.PROVIDER_PROVENANCE,
            "publication_version": self.publisher_version,
            "status": "ready",
            "unit_count": len(units),
            "vector_count": result.indexed,
        }
        validation["embedding_profiles"] = profiles
        validation["cb5_fake_test_publication"] = {"isolated_test_only": True}
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
            "provenance": cb5.cb2.PROVIDER_PROVENANCE,
        }


class _FakeHybridV2Retriever:
    retriever_version = "fake-code-hybrid-cb5-test-v1"
    fusion = "fake-exact-sparse-dense-rrf-cb5-test-v1"
    channels = ("exact", "sparse", "dense")
    index_family = "ast-v2"

    def __init__(self, runtime: Any, profile: Any) -> None:
        self.store = runtime.store
        self.profile = profile

    def search(self, request: Any, query_profile: Any) -> dict[str, Any]:
        assert query_profile.budget.dense_candidates == cb5.CHANNEL_BUDGET["dense_candidates"]
        generations = self.store.active_code_generations(
            project_id=request.scope.project_id,
            repository_ids=request.scope.repository_ids or None,
        )
        return {
            "results": [],
            "relations": [],
            "index_generation": sorted(generations.values()),
            "trace": {
                "duration_ms": 0.25,
                "exact_outcome": "complete_no_match",
                "sparse_outcome": "complete_no_match",
                "dense_outcome": "complete_no_match",
                "exact_candidates": 0,
                "sparse_candidates": 0,
                "dense_candidates": 0,
            },
        }


class _FakeCalibrationArtifact:
    version = "fake-calibration-cb5-test-v1"
    status = "provisional"

    def canonical_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "fake-calibration-artifact-test-v1",
            "version": self.version,
            "status": self.status,
            "method": "identity-test-only",
        }


class _FakeRerankedRetriever:
    reranker_version = "fake-code-reranker-cb5-test-v1"
    rerank_policy = "fake-deterministic-test-v1"
    fallback_policy = cb5.FLAG_SNAPSHOT["timeout_fallback"]

    def __init__(self, hybrid: _FakeHybridV2Retriever, calibration: Any) -> None:
        self.hybrid = hybrid
        self.calibration = calibration

    def search(self, request: Any, query_profile: Any) -> dict[str, Any]:
        response = self.hybrid.search(request, query_profile)
        trace = dict(response["trace"])
        trace.update(
            {
                "rerank_outcome": "timeout",
                "rerank_latency_ms": 0.5,
                "fallback_status": "succeeded",
                "explanation_codes": ["timeout_fallback"],
                "negative_reasons": ["deadline_exceeded"],
            }
        )
        return {**response, "trace": trace}


def _fake_publisher_factory(runtime: Any, profile: Any) -> _FakeDenseProfilePublisher:
    assert profile.canonical_snapshot() == cb5.cb2.PROFILE_SNAPSHOT
    return _FakeDenseProfilePublisher(runtime)


def _fake_hybrid_factory(runtime: Any, profile: Any) -> _FakeHybridV2Retriever:
    return _FakeHybridV2Retriever(runtime, profile)


def _fake_calibration_factory(runtime: Any) -> _FakeCalibrationArtifact:
    assert runtime.store is not None
    return _FakeCalibrationArtifact()


def _fake_reranker_factory(
    runtime: Any,
    hybrid: Any,
    calibration: Any,
) -> _FakeRerankedRetriever:
    assert runtime.store is not None
    assert isinstance(hybrid, _FakeHybridV2Retriever)
    assert isinstance(calibration, _FakeCalibrationArtifact)
    return _FakeRerankedRetriever(hybrid, calibration)


@pytest.fixture(scope="module")
def cb5_test_artifact(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, tuple[bool, int | None, int | None], dict[str, tuple[int, int]]]:
    production_database = REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3"
    production_before = _file_state(production_database)
    runs_before = _tree_state(REPOSITORY_RUNS)
    runs_dir = tmp_path_factory.mktemp("code-c-b5-test-runs")
    artifact = cb5.run_cb5(
        root=REPOSITORY_ROOT,
        runs_dir=runs_dir,
        execution_mode="test",
        publisher_factory=_fake_publisher_factory,
        hybrid_factory=_fake_hybrid_factory,
        calibration_factory=_fake_calibration_factory,
        reranker_factory=_fake_reranker_factory,
    )
    assert _file_state(production_database) == production_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before
    return artifact, production_before, runs_before


def test_preparation_is_fixed_prepared_and_creates_no_artifact() -> None:
    before = _tree_state(REPOSITORY_RUNS)
    status = cb5.preparation_status(root=REPOSITORY_ROOT)
    package = validate_golden_package(REPOSITORY_ROOT)

    assert status["status"] == "PREPARED"
    assert status["execution_status"] == "pending-c3-03-gate"
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
    controls = status["controls"]
    assert controls["qualified_baseline"]["run_id"] == cb5.CB0_QUALIFIED_RUN_ID
    assert controls["qualified_baseline"]["treatment_qualified"] is True
    assert controls["cb1_not_qualified_audit"]["run_id"] == cb5.CB1_AUDIT_RUN_ID
    assert controls["cb2_not_qualified_audit"]["run_id"] == cb5.CB2_AUDIT_RUN_ID
    assert controls["cb1_not_qualified_audit"]["treatment_qualified"] is False
    assert controls["cb2_not_qualified_audit"]["treatment_qualified"] is False
    assert controls["cb1_not_qualified_audit"]["qualification_use"] == "forbidden"
    assert controls["cb2_not_qualified_audit"]["qualification_use"] == "forbidden"
    assert status["config_contract"]["flag_snapshot"] == cb5.FLAG_SNAPSHOT
    assert status["config_contract"]["production_identity"] == cb5.PRODUCTION_IDENTITY
    assert status["config_contract"]["cross_encoder"] == cb5.CROSS_ENCODER_STATUS
    assert all(item["status"] == "pending" for item in status["required_reporting"].values())
    assert _tree_state(REPOSITORY_RUNS) == before


def test_fake_missing_gate_and_formal_runs_are_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(cb5.CB5Error, match="Gate approval"):
        cb5.run_cb5(
            root=REPOSITORY_ROOT,
            runs_dir=tmp_path / "missing-gate",
            calibration_artifact=_FakeCalibrationArtifact(),
        )

    with pytest.raises(cb5.CB5Error, match="forbids component factory injection"):
        cb5.run_cb5(
            root=REPOSITORY_ROOT,
            runs_dir=tmp_path / "fake-qualified",
            publisher_factory=_fake_publisher_factory,
            hybrid_factory=_fake_hybrid_factory,
            calibration_factory=_fake_calibration_factory,
            reranker_factory=_fake_reranker_factory,
            calibration_artifact=_FakeCalibrationArtifact(),
            c3_03_gate_approved=True,
        )

    with pytest.raises(cb5.CB5Error, match="public production CalibrationArtifact"):
        cb5.run_cb5(
            root=REPOSITORY_ROOT,
            runs_dir=tmp_path / "fake-calibration-qualified",
            calibration_artifact=_FakeCalibrationArtifact(),
            c3_03_gate_approved=True,
        )

    with pytest.raises(cb5.CB5Error, match="may not write under evals/code/runs"):
        cb5.run_cb5(
            root=REPOSITORY_ROOT,
            runs_dir=REPOSITORY_RUNS,
            execution_mode="test",
            publisher_factory=_fake_publisher_factory,
            hybrid_factory=_fake_hybrid_factory,
            calibration_factory=_fake_calibration_factory,
            reranker_factory=_fake_reranker_factory,
        )

    with pytest.raises(cb5.CB5Error, match="all four explicit fake factories"):
        cb5.run_cb5(
            root=REPOSITORY_ROOT,
            runs_dir=tmp_path / "partial-fakes",
            execution_mode="test",
            publisher_factory=_fake_publisher_factory,
        )


def test_public_c3_03_contract_is_directly_consumable_when_ready(tmp_path: Path) -> None:
    status = cb5.preparation_status(root=REPOSITORY_ROOT)
    if status["production_components"]["status"] != "ready":
        pytest.skip("parallel C3-03 implementation is not ready")
    public = importlib.import_module(cb5.PUBLIC_CODE_MODULE)
    for name in (
        cb5.REAL_PUBLISHER_CLASS,
        cb5.REAL_HYBRID_CLASS,
        cb5.REAL_RERANKER_CLASS,
        cb5.REAL_CALIBRATION_CLASS,
    ):
        assert isinstance(getattr(public, name), type)
    hybrid = public.CodeHybridV2Retriever(object())
    reranker = public.CodeRerankedRetriever(hybrid)
    reranker_descriptor = cb5._reranker_descriptor(reranker)
    assert reranker_descriptor.class_name == cb5.REAL_RERANKER_CLASS
    assert reranker_descriptor.version == "code-deterministic-reranker-v1"
    assert reranker_descriptor.policy == "deterministic-rules-v1"
    assert reranker_descriptor.fallback_policy == cb5.FLAG_SNAPSHOT["timeout_fallback"]

    calibration = public.fit_calibration(
        ({"score": 1.0, "rank": 0, "relevant": True},),
        profile_version="code-query-profile-v2",
        model_version=reranker_descriptor.policy,
        index_version="test-index-v1",
    )
    calibration_descriptor = cb5._calibration_descriptor(calibration)
    assert calibration_descriptor.class_name == cb5.REAL_CALIBRATION_CLASS
    assert calibration_descriptor.version == "code-rerank-calibration-v1"
    assert calibration_descriptor.status == "provisional"
    assert calibration_descriptor.sample_count == 1
    assert calibration_descriptor.provisional is True
    assert calibration_descriptor.profile_version == "code-query-profile-v2"
    assert calibration_descriptor.model_version == reranker_descriptor.policy
    assert calibration_descriptor.index_version == "test-index-v1"
    assert calibration_descriptor.reranker_version == reranker_descriptor.version
    artifact_path = tmp_path / "calibration.json"
    artifact_path.write_bytes(calibration.canonical_json_bytes())
    loaded = cb5._load_calibration_artifact(artifact_path)
    assert cb5._calibration_descriptor(loaded) == calibration_descriptor
    case = validate_golden_package(REPOSITORY_ROOT).eligible_cases[0]
    observations, unavailable = cb5._decision_calibration(
        (
            {
                "accepted": True,
                "rerank_score": 1.0,
                "reranked_rank": 0,
                "card": {
                    "entity_id": case.request.expected_entity_ids[0],
                    "retrieval_unit_id": "",
                },
            },
        ),
        case,
        loaded,
        calibration_descriptor,
        profile_version="code-query-profile-v2",
        model_version=reranker_descriptor.policy,
        index_version="test-index-v1",
        reranker_version=reranker_descriptor.version,
    )
    assert observations == [{"score": 1.0, "label": 1, "grade": 2}]
    assert unavailable == []
    mismatch_observations, mismatch_unavailable = cb5._decision_calibration(
        (
            {
                "accepted": True,
                "rerank_score": 1.0,
                "reranked_rank": 0,
                "card": {
                    "entity_id": case.request.expected_entity_ids[0],
                    "retrieval_unit_id": "",
                },
            },
        ),
        case,
        loaded,
        calibration_descriptor,
        profile_version="code-query-profile-v2",
        model_version=reranker_descriptor.policy,
        index_version="different-index-v1",
        reranker_version=reranker_descriptor.version,
    )
    assert mismatch_observations == []
    assert len(mismatch_unavailable) == 1
    assert "CalibrationArtifactMismatchError" in mismatch_unavailable[0]


def test_test_harness_fixes_identity_denominator_and_isolation(
    cb5_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
) -> None:
    artifact, production_before, runs_before = cb5_test_artifact
    manifest = _json(artifact / "manifest.json")
    materialization = _json(artifact / "materialization.json")
    coverage = _json(artifact / "coverage.json")
    attempt = _json(artifact / "attempt-audit.json")
    package = validate_golden_package(REPOSITORY_ROOT)

    assert manifest["execution_mode"] == "test"
    assert manifest["c3_03_gate_approved"] is False
    assert manifest["treatment_qualified"] is False
    assert manifest["dataset"]["package_hash"] == PACKAGE_HASH
    assert manifest["dataset"]["case_membership_hash"] == package.membership_hash
    assert manifest["dataset"]["eligible_cases"] == EXPECTED_ELIGIBLE_COUNT
    controls = manifest["controls"]
    assert controls["qualified_baseline"]["run_id"] == cb5.CB0_QUALIFIED_RUN_ID
    assert controls["cb1_not_qualified_audit"]["run_id"] == cb5.CB1_AUDIT_RUN_ID
    assert controls["cb2_not_qualified_audit"]["run_id"] == cb5.CB2_AUDIT_RUN_ID
    assert controls["cb1_not_qualified_audit"]["qualification_use"] == "forbidden"
    assert controls["cb2_not_qualified_audit"]["qualification_use"] == "forbidden"

    config = manifest["config"]
    assert config["flag_snapshot"] == cb5.FLAG_SNAPSHOT
    assert config["production_identity"] == cb5.PRODUCTION_IDENTITY
    assert config["publisher"]["class"] == "_FakeDenseProfilePublisher"
    assert config["hybrid_retriever"]["class"] == "_FakeHybridV2Retriever"
    assert config["reranker"]["class"] == "_FakeRerankedRetriever"
    assert config["calibration_artifact"]["class"] == "_FakeCalibrationArtifact"
    assert config["calibration_artifact"]["version"] == _FakeCalibrationArtifact.version
    assert config["cross_encoder"] == cb5.CROSS_ENCODER_STATUS

    assert materialization["database_isolated"] is True
    assert materialization["production_database_accessed"] is False
    assert materialization["unit_builder"] == "ast-v2"
    assert materialization["stage_order"] == cb5.PRODUCTION_IDENTITY["stage_order"]
    assert materialization["flag_snapshot"] == cb5.FLAG_SNAPSHOT
    assert materialization["vectors"]["complete_against_ast_units"] is True
    assert materialization["network_used"] is False
    assert materialization["downloads_performed"] is False

    assert coverage["total_cases"] == EXPECTED_CASE_COUNT
    assert coverage["eligible_cases"] == EXPECTED_ELIGIBLE_COUNT
    assert coverage["ineligible_cases"] == EXPECTED_INELIGIBLE_COUNT
    assert coverage["result_count"] == EXPECTED_ELIGIBLE_COUNT
    assert len(coverage["enabled_case_names"]) == EXPECTED_ELIGIBLE_COUNT
    assert coverage["denominator_policy"] == {
        "eligible_case_count": EXPECTED_ELIGIBLE_COUNT,
        "case_membership_hash": package.membership_hash,
        "ineligible_cases_are_reported_not_silently_dropped": True,
    }

    assert attempt["execution_mode"] == "test"
    assert attempt["c3_03_gate_approved"] is False
    assert attempt["treatment_qualified"] is False
    assert attempt["qualified_baseline_run_id"] == cb5.CB0_QUALIFIED_RUN_ID
    assert attempt["audit_control_run_ids"] == [
        cb5.CB1_AUDIT_RUN_ID,
        cb5.CB2_AUDIT_RUN_ID,
    ]
    assert attempt["audit_controls_are_qualified_baselines"] is False
    assert _file_state(REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3") == production_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_reports_cover_required_quality_contribution_fallback_and_costs(
    cb5_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
) -> None:
    artifact = cb5_test_artifact[0]
    metrics = _json(artifact / "metrics.json")
    slices = _json(artifact / "slices.json")
    errors = _json(artifact / "error-analysis.json")
    latency = _json(artifact / "latency.json")
    storage = _json(artifact / "storage.json")

    comparisons = metrics["comparisons"]
    assert set(comparisons) == {
        "cb0_qualified_baseline",
        "cb1_not_qualified_audit",
        "cb2_not_qualified_audit",
    }
    assert comparisons["cb0_qualified_baseline"]["control_is_qualified_baseline"] is True
    assert comparisons["cb1_not_qualified_audit"]["control_is_qualified_baseline"] is False
    assert comparisons["cb2_not_qualified_audit"]["control_is_qualified_baseline"] is False
    required_rows = {
        (row["scope"], row["metric_name"])
        for comparison in comparisons.values()
        for row in comparison["comparisons"]
    }
    for scope in ("overall", "exact", "identifier", "low_overlap"):
        for metric in (
            "entity_recall@10",
            "expected_locator_recall@10",
            "mrr@10",
            "ndcg@10",
        ):
            assert (scope, metric) in required_rows
    for metric in (
        "hard_negative_error@10",
        "harmful_candidate_rate@10",
        "entity_duplicate_rate@10",
        "unit_duplicate_rate@10",
    ):
        assert ("overall", metric) in required_rows

    timeout = metrics["timeout_fallback"]
    assert timeout["timeout_count"] == EXPECTED_ELIGIBLE_COUNT
    assert timeout["fallback_rate"] == {
        "numerator": EXPECTED_ELIGIBLE_COUNT,
        "denominator": EXPECTED_ELIGIBLE_COUNT,
        "value": 1.0,
    }
    assert timeout["timeout_fallback_rate"] == timeout["fallback_rate"]
    assert timeout["all_timeout_fallbacks_valid"] is True
    assert timeout["timeout_p95_ms"]["status"] == "available"
    assert timeout["timeout_p95_ms"]["value"] == 0.5
    assert metrics["calibration"]["status"] == "unavailable"
    assert metrics["calibration"]["probability_claim_allowed"] is False
    assert metrics["explanations"]["explanation_codes"] == {
        "timeout_fallback": EXPECTED_ELIGIBLE_COUNT
    }
    assert metrics["explanations"]["negative_reasons"] == {
        "deadline_exceeded": EXPECTED_ELIGIBLE_COUNT
    }
    assert metrics["explanations"]["cross_encoder"] == cb5.CROSS_ENCODER_STATUS
    assert set(metrics["contribution"]["totals"]) == {
        "dense_contribution_count",
        "dense_only_contribution_count",
        "rerank_changed_count",
        "rerank_added_count",
        "rerank_demoted_count",
        "rerank_promoted_count",
        "rerank_removed_count",
    }

    assert set(slices["required_treatment_slices"]) == set(comparisons)
    assert set(errors["same_name_hard_negative"]) == set(comparisons)
    assert set(errors["harmful_and_duplicates"]) == set(comparisons)
    assert errors["explanations"]["cross_encoder"] == cb5.CROSS_ENCODER_STATUS
    assert latency["ingest"]["total_ms"] is not None
    assert latency["dense_index"]["total_ms"] is not None
    assert set(latency["query_p95"]) == set(comparisons)
    assert latency["rerank_timeout"] == timeout
    assert storage["cb0_qualified_baseline_comparison"]["run_id"] == cb5.CB0_QUALIFIED_RUN_ID
    assert storage["cb1_not_qualified_audit_comparison"]["run_id"] == cb5.CB1_AUDIT_RUN_ID
    assert storage["cb2_not_qualified_audit_comparison"]["run_id"] == cb5.CB2_AUDIT_RUN_ID


def test_provisional_calibration_reports_ece_brier_without_probability_claim() -> None:
    descriptor = cb5._calibration_descriptor(_FakeCalibrationArtifact())
    run = {
        "results": [
            {
                "detail": {
                    "trace": {
                        "calibration_observations": [
                            {"score": 0.9, "label": 1, "grade": 2},
                            {"score": 0.2, "label": 0, "grade": -1},
                        ],
                        "calibration_unavailable_reasons": [],
                    }
                }
            }
        ]
    }
    report = cb5._calibration_report(run, descriptor)
    assert report["status"] == "provisional"
    assert report["observation_count"] == 2
    assert report["ece"]["status"] == "provisional"
    assert report["ece"]["value"] == pytest.approx(0.15)
    assert report["brier"]["status"] == "provisional"
    assert report["brier"]["value"] == pytest.approx(0.025)
    assert report["probability_claim_allowed"] is False
    assert "probability claims are forbidden" in report["interpretation"]


def test_test_artifact_is_opt_in_and_tamper_fails_closed(
    cb5_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
    tmp_path: Path,
) -> None:
    artifact = cb5_test_artifact[0]
    with pytest.raises(cb5.CB5Error, match="allow_test_artifact"):
        cb5.verify_artifact(artifact, root=REPOSITORY_ROOT)
    verified = cb5.verify_artifact(
        artifact,
        root=REPOSITORY_ROOT,
        allow_test_artifact=True,
    )
    assert verified["status"] == "verified-test-artifact"
    assert verified["treatment_qualified"] is False
    assert verified["audit_controls_are_qualified_baselines"] is False

    report_tamper = tmp_path / "report-tamper"
    shutil.copytree(artifact, report_tamper)
    metrics = _json(report_tamper / "metrics.json")
    metrics["run_id"] = "tampered"
    (report_tamper / "metrics.json").write_text(
        json.dumps(metrics, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(cb5.CB5Error, match="fingerprint mismatch"):
        cb5.verify_artifact(
            report_tamper,
            root=REPOSITORY_ROOT,
            allow_test_artifact=True,
        )

    qualification_tamper = tmp_path / "qualification-tamper"
    shutil.copytree(artifact, qualification_tamper)
    manifest = _json(qualification_tamper / "manifest.json")
    manifest["treatment_qualified"] = True
    (qualification_tamper / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(cb5.CB5Error, match="manifest cannot be reconstructed"):
        cb5.verify_artifact(
            qualification_tamper,
            root=REPOSITORY_ROOT,
            allow_test_artifact=True,
        )


def test_secret_scanner_workspace_drift_and_anchor_tamper_are_rejected(
    cb5_test_artifact: tuple[
        Path,
        tuple[bool, int | None, int | None],
        dict[str, tuple[int, int]],
    ],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_artifact = tmp_path / "secret-artifact"
    shutil.copytree(cb5_test_artifact[0], secret_artifact)
    audit = _json(secret_artifact / "attempt-audit.json")
    audit["api_key"] = "sk-test-secret-value-123456"
    (secret_artifact / "attempt-audit.json").write_text(
        json.dumps(audit, sort_keys=True),
        encoding="utf-8",
    )
    scan = baseline.scan_artifact_security(secret_artifact)
    assert scan["clean"] is False
    assert scan["credential_assignment_findings"] > 0

    with pytest.raises(cb5.CB5Error, match="workspace input fingerprint drifted"):
        cb5._require_unchanged({"workspace": "before"}, {"workspace": "after"})

    real_verify = cb5.cb2.verify_artifact

    def _tampered_verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = real_verify(*args, **kwargs)
        return {**result, "run_id": "evaluation-run://tampered"}

    monkeypatch.setattr(cb5.cb2, "verify_artifact", _tampered_verify)
    with pytest.raises(cb5.CB5Error, match="fixed NOT QUALIFIED audit anchor"):
        cb5._load_cb2_audit_anchor(REPOSITORY_ROOT)
