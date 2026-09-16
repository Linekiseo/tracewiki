from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.evaluation import cb3
from evidence_rag.evaluation.golden import (
    EXPECTED_CASE_COUNT,
    EXPECTED_ELIGIBLE_COUNT,
    EXPECTED_INELIGIBLE_COUNT,
    PACKAGE_HASH,
    validate_golden_package,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_RUNS = REPOSITORY_ROOT / "evals" / "code" / "runs"
PRODUCTION_DATABASE = REPOSITORY_ROOT / "var" / "evidence-rag.sqlite3"


def _file_state(path: Path) -> tuple[bool, int | None, int | None, str | None]:
    if not path.exists():
        return False, None, None, None
    stat = path.stat()
    return True, stat.st_size, stat.st_mtime_ns, cb3._sha256_file(path)


def _tree_state(path: Path) -> dict[str, tuple[int, int]]:
    return {
        item.relative_to(path).as_posix(): (item.stat().st_size, item.stat().st_mtime_ns)
        for item in path.rglob("*")
        if item.is_file()
    }


@pytest.fixture(scope="module")
def prepared_status() -> dict[str, Any]:
    database_before = _file_state(PRODUCTION_DATABASE)
    runs_before = _tree_state(REPOSITORY_RUNS)
    status = cb3.preparation_status(root=REPOSITORY_ROOT)
    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before
    return status


def test_preparation_is_explicitly_non_qualified_and_creates_nothing(
    prepared_status: dict[str, Any],
) -> None:
    package = validate_golden_package(REPOSITORY_ROOT)

    assert prepared_status["status"] == "PREPARED"
    assert prepared_status["qualification_status"] == "NON-QUALIFIED"
    assert prepared_status["execution_status"] == "authorized-production-ready"
    assert prepared_status["treatment_qualified"] is False
    assert prepared_status["run_created"] is False
    assert prepared_status["artifact_created"] is False
    assert prepared_status["metrics_created"] is False
    assert prepared_status["dataset"] == {
        "id": "code-golden",
        "version": "code-golden-v2",
        "package_hash": PACKAGE_HASH,
        "release_record_id": "code-golden-v2-release-001",
        "case_membership_hash": package.membership_hash,
        "total_cases": EXPECTED_CASE_COUNT,
        "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
        "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
    }
    assert all(
        item["status"] == "pending" for item in prepared_status["required_reporting"].values()
    )
    assert prepared_status["gate_authorization"]["decision"] == cb3.C4_GATE_DECISION
    assert prepared_status["gate_authorization"]["status"] == "authorized"
    assert (
        prepared_status["gate_authorization"]["schema_version"]
        == cb3.C4_AUTHORIZATION_SCHEMA_VERSION
    )
    assert prepared_status["gate_authorization"]["snapshot_hash"].startswith("sha256:")
    assert prepared_status["execution_policy"]["prepared_only"] is False
    assert prepared_status["execution_policy"]["production_execution_authorized"] is True
    assert prepared_status["isolation"]["formal_database_guard"]["observed"]["sidecars"] == (
        {"name": "-wal", "exists": False},
        {"name": "-shm", "exists": False},
    )


def test_only_c_b0_is_a_qualified_control(prepared_status: dict[str, Any]) -> None:
    controls = prepared_status["controls"]

    assert controls["qualified_baseline"]["run_id"] == cb3.CB0_QUALIFIED_RUN_ID
    assert controls["qualified_baseline"]["run_id"].endswith("5a92eafdff5d49e6aae8bb55fdc14061")
    assert controls["qualified_baseline"]["treatment_qualified"] is True
    assert controls["qualified_baseline"]["qualification_use"] == "required"
    assert {
        controls["cb1_not_qualified_audit"]["run_id"],
        controls["cb2_not_qualified_audit"]["run_id"],
        controls["cb5_not_qualified_audit"]["run_id"],
    } == {
        cb3.CB1_AUDIT_RUN_ID,
        cb3.CB2_AUDIT_RUN_ID,
        cb3.CB5_AUDIT_RUN_ID,
    }
    for name in (
        "cb1_not_qualified_audit",
        "cb2_not_qualified_audit",
        "cb5_not_qualified_audit",
    ):
        assert controls[name]["treatment_qualified"] is False
        assert controls[name]["qualification_use"] == "forbidden"


def test_four_treatments_share_the_released_33_case_denominator(
    prepared_status: dict[str, Any],
) -> None:
    package = validate_golden_package(REPOSITORY_ROOT)
    expected = tuple(case.canonical_id for case in package.eligible_cases)
    contract = prepared_status["treatment_contract"]

    assert tuple(contract["order"]) == cb3.TREATMENT_ORDER
    assert set(contract["arms"]) == set(cb3.TREATMENT_ORDER)
    assert contract["same_snapshot_required"] is True
    assert contract["same_ingestion_required"] is True
    assert contract["same_33_case_denominator_required"] is True
    assert contract["denominator"]["case_count"] == EXPECTED_ELIGIBLE_COUNT
    assert set(contract["denominator"]["canonical_case_ids"]) == set(expected)
    assert {
        item["case_membership_hash"] for item in contract["denominator"]["treatments"].values()
    } == {contract["denominator"]["case_membership_hash"]}

    observed = cb3.require_same_33_case_denominator(
        {name: expected for name in cb3.TREATMENT_ORDER},
        expected_case_ids=expected,
    )
    assert observed == contract["denominator"]

    drifted = {name: expected for name in cb3.TREATMENT_ORDER}
    drifted["typed_graph"] = expected[:-1]
    with pytest.raises(cb3.CB3Error, match="33 unique cases"):
        cb3.require_same_33_case_denominator(
            drifted,
            expected_case_ids=expected,
        )


def test_delta_requires_same_33_membership_and_metric_denominator(
    prepared_status: dict[str, Any],
) -> None:
    membership_hash = prepared_status["treatment_contract"]["denominator"]["case_membership_hash"]
    control = {
        "status": "available",
        "value": 0.5,
        "denominator": 20,
        "membership_case_count": 33,
        "case_membership_hash": membership_hash,
    }
    treatment = {**control, "value": 0.75}

    delta = cb3.same_denominator_delta(treatment, control, direction="higher")
    assert delta["treatment_minus_control"] == 0.25
    assert delta["directional_delta"] == 0.25
    assert delta["membership_case_count"] == 33

    with pytest.raises(cb3.CB3Error, match="same metric denominator"):
        cb3.same_denominator_delta(
            {**treatment, "denominator": 21},
            control,
            direction="higher",
        )
    with pytest.raises(cb3.CB3Error, match="fixed 33-case membership"):
        cb3.same_denominator_delta(
            {**treatment, "case_membership_hash": "sha256:" + "0" * 64},
            control,
            direction="higher",
        )


class _FakeTypedGraphRetriever:
    retriever_version = "fake-cb3-test-v1"

    def search(self, request: Any, query_profile: Any) -> dict[str, Any]:
        return {"results": [], "trace": {"simulated": True}}


def test_fake_identity_and_missing_gate_fail_before_any_artifact(
    tmp_path: Path,
) -> None:
    database_before = _file_state(PRODUCTION_DATABASE)
    runs_before = _tree_state(REPOSITORY_RUNS)

    with pytest.raises(cb3.CB3Error, match="no test/fake execution mode"):
        cb3.execute_cb3(
            root=REPOSITORY_ROOT,
            runs_dir=tmp_path / "test-runs",
            execution_mode="test",
            typed_graph_factory=_FakeTypedGraphRetriever,
        )
    with pytest.raises(
        cb3.CB3Error,
        match="forbids fake, injected, wrapped, or subclassed graph retrievers",
    ):
        cb3.execute_cb3(
            root=REPOSITORY_ROOT,
            runs_dir=REPOSITORY_RUNS,
            typed_graph_factory=_FakeTypedGraphRetriever,
            c4_02_gate_approved=True,
        )
    with pytest.raises(cb3.CB3Error, match="Gate approval"):
        cb3.execute_cb3(
            root=REPOSITORY_ROOT,
            runs_dir=REPOSITORY_RUNS,
        )
    cb3._validate_execution_policy(
        root=REPOSITORY_ROOT,
        runs_dir=REPOSITORY_RUNS,
        execution_mode="qualified",
        typed_graph_factory=None,
        c4_02_gate_approved=True,
    )
    with pytest.raises(
        cb3.CB3Error,
        match="fake, injected, wrapped, or subclassed graph retriever is forbidden",
    ):
        cb3.require_production_retriever_identity(_FakeTypedGraphRetriever())

    assert not (tmp_path / "test-runs").exists()
    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_explicit_approval_without_an_authorizing_gate_still_fails(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository-without-gate"

    with pytest.raises(cb3.CB3Error, match="Gate authorization report is missing"):
        cb3.execute_cb3(
            root=root,
            c4_02_gate_approved=True,
        )

    assert not (root / "evals" / "code" / "runs").exists()


def test_normalized_authorization_snapshot_is_self_contained_and_hashed() -> None:
    artifact = REPOSITORY_RUNS / "92f8d8e190f449bab9ba253227473419"
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    snapshot = cb3._validate_gate_authorization(REPOSITORY_ROOT)

    verification = cb3._validate_authorization_snapshot(
        snapshot,
        manifest["production_identity"],
    )

    assert verification["mode"] == "normalized-v1"
    assert verification["decision"] == cb3.C4_GATE_DECISION
    assert verification["snapshot_hash"] == snapshot["snapshot_hash"]
    assert snapshot["schema_version"] == cb3.C4_AUTHORIZATION_SCHEMA_VERSION
    assert snapshot["authorization_version"] == cb3.C4_AUTHORIZATION_VERSION
    assert snapshot["evidence"] == cb3.C4_AUTHORIZATION_EVIDENCE


@pytest.mark.parametrize("gate_state", ["appended", "missing-or-moved"])
def test_existing_artifact_verify_does_not_read_current_gate(
    monkeypatch: pytest.MonkeyPatch,
    gate_state: str,
) -> None:
    artifact = REPOSITORY_RUNS / "92f8d8e190f449bab9ba253227473419"

    def fail_if_gate_is_read(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError(f"verify consulted the {gate_state} Gate report")

    monkeypatch.setattr(cb3, "_validate_gate_authorization", fail_if_gate_is_read)
    monkeypatch.setattr(cb3, "_production_component_contract", fail_if_gate_is_read)

    verification = cb3.verify_artifact(artifact, root=REPOSITORY_ROOT)

    assert verification["status"] == "verified"
    assert verification["authorization_mode"] == "strict-legacy-v1"
    assert verification["authorization_decision"] == cb3.C4_GATE_DECISION


def test_authorization_and_production_identity_tampering_fail_closed() -> None:
    artifact = REPOSITORY_RUNS / "92f8d8e190f449bab9ba253227473419"
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    attempt = json.loads((artifact / "attempt-audit.json").read_text(encoding="utf-8"))
    treatments = json.loads((artifact / "treatments.json").read_text(encoding="utf-8"))
    normalized = cb3._validate_gate_authorization(REPOSITORY_ROOT)

    normalized_manifest = deepcopy(manifest)
    normalized_attempt = deepcopy(attempt)
    normalized_manifest["gate_authorization"] = normalized
    normalized_attempt["gate_authorization"] = normalized
    next(
        gate
        for gate in normalized_manifest["acceptance"]["gates"]
        if gate["gate"] == "c4_02_gate_authorized"
    )["evidence"] = normalized
    assert (
        cb3._validate_artifact_authorization(
            normalized_manifest,
            normalized_attempt,
            treatments["production_identity"],
        )["mode"]
        == "normalized-v1"
    )

    decision_tamper = deepcopy(normalized)
    decision_tamper["decision"] = "FAKE AUTHORIZATION"
    decision_manifest = deepcopy(normalized_manifest)
    decision_attempt = deepcopy(normalized_attempt)
    decision_manifest["gate_authorization"] = decision_tamper
    decision_attempt["gate_authorization"] = decision_tamper
    next(
        gate
        for gate in decision_manifest["acceptance"]["gates"]
        if gate["gate"] == "c4_02_gate_authorized"
    )["evidence"] = decision_tamper
    with pytest.raises(cb3.CB3Error, match="not an approved C4-02 decision"):
        cb3._validate_artifact_authorization(
            decision_manifest,
            decision_attempt,
            treatments["production_identity"],
        )

    hash_tamper = deepcopy(normalized)
    hash_tamper["evidence_hash"] = "sha256:" + "0" * 64
    hash_manifest = deepcopy(normalized_manifest)
    hash_attempt = deepcopy(normalized_attempt)
    hash_manifest["gate_authorization"] = hash_tamper
    hash_attempt["gate_authorization"] = hash_tamper
    next(
        gate
        for gate in hash_manifest["acceptance"]["gates"]
        if gate["gate"] == "c4_02_gate_authorized"
    )["evidence"] = hash_tamper
    with pytest.raises(cb3.CB3Error, match="evidence hash is invalid"):
        cb3._validate_artifact_authorization(
            hash_manifest,
            hash_attempt,
            treatments["production_identity"],
        )

    identity_manifest = deepcopy(manifest)
    identity_treatments = deepcopy(treatments["production_identity"])
    identity_manifest["production_identity"]["observed_graph"]["class"] = "FakeGraphRetriever"
    identity_treatments["graph"]["class"] = "FakeGraphRetriever"
    with pytest.raises(cb3.CB3Error, match="observed production identity is invalid"):
        cb3._validate_artifact_authorization(
            identity_manifest,
            attempt,
            identity_treatments,
        )

    legacy_manifest = deepcopy(manifest)
    legacy_attempt = deepcopy(attempt)
    legacy_gate = deepcopy(legacy_manifest["gate_authorization"])
    legacy_gate["decision"] = "FAKE LEGACY AUTHORIZATION"
    legacy_manifest["gate_authorization"] = legacy_gate
    legacy_attempt["gate_authorization"] = legacy_gate
    next(
        gate
        for gate in legacy_manifest["acceptance"]["gates"]
        if gate["gate"] == "c4_02_gate_authorized"
    )["evidence"] = legacy_gate
    with pytest.raises(cb3.CB3Error, match="legacy artifact authorization is not"):
        cb3._validate_artifact_authorization(
            legacy_manifest,
            legacy_attempt,
            treatments["production_identity"],
        )


def test_metric_and_security_contracts_are_complete(
    prepared_status: dict[str, Any],
) -> None:
    required = set(prepared_status["required_reporting"])
    assert {
        "required_path_recall",
        "required_path_precision",
        "graph_only_recovery@10",
        "graph_noise_rate@10",
        "graph_harmful_candidate_rate@10",
        "accepted_path_length",
        "accepted_path_type_accuracy",
        "accepted_path_direction_accuracy",
        "graph_pruned_budget",
        "graph_pruned_cycle",
        "graph_pruned_acl",
        "graph_pruned_version",
        "latency_ms_p95",
        "graph_latency_ms_p95",
        "expected_locator_recall@10",
        "entity_recall@10",
        "mrr@10",
        "ndcg@10",
    } <= required
    policy = prepared_status["artifact_policy"]
    assert policy["canonical_json_required"] is True
    assert policy["portable_sqlite_required"] is True
    assert policy["secret_scan_required"] is True
    assert policy["temporary_path_scan_required"] is True
    assert policy["wal_shm_forbidden"] is True
    assert policy["pyc_and_pycache_forbidden"] is True
    component = prepared_status["production_component"]
    assert component["status"] == "ready"
    assert component["class"] == cb3.REAL_TYPED_GRAPH_CLASS
    assert component["interfaces"] == {
        "GraphTraversalRequest": True,
        "GraphTraversalResult": True,
        "GraphTraversalTrace": True,
    }


def test_published_production_artifact_is_verified_when_present() -> None:
    artifacts = []
    for manifest_path in sorted(REPOSITORY_RUNS.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") == cb3.CB3_SCHEMA_VERSION:
            artifacts.append(manifest_path.parent)
    assert len(artifacts) <= 1
    if not artifacts:
        pytest.skip("production C-B3 has not been executed yet")

    database_before = _file_state(PRODUCTION_DATABASE)
    runs_before = _tree_state(REPOSITORY_RUNS)
    verification = cb3.verify_artifact(artifacts[0], root=REPOSITORY_ROOT)
    manifest = json.loads((artifacts[0] / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((artifacts[0] / "metrics.json").read_text(encoding="utf-8"))
    coverage = json.loads((artifacts[0] / "coverage.json").read_text(encoding="utf-8"))

    assert verification["status"] == "verified"
    assert verification["acceptance_decision"] in {"qualified", "not-qualified"}
    assert manifest["run_ids"]["graph_reranker"] == manifest["run_id"]
    assert set(manifest["run_ids"]) == set(cb3.TREATMENT_ORDER)
    assert len(set(manifest["run_ids"].values())) == 4
    assert metrics["source"] == "production"
    assert metrics["simulated"] is False
    assert set(metrics["arms"]) == set(cb3.TREATMENT_ORDER)
    for arm in cb3.TREATMENT_ORDER:
        assert metrics["arms"][arm]["run_id"] == manifest["run_ids"][arm]
        assert set(metrics["arms"][arm]["metrics"]) == set(cb3.REQUIRED_REPORTING)
    denominator = coverage["same_33_case_denominator"]
    assert {
        tuple(sorted(case_ids)) for case_ids in denominator["case_ids_by_treatment"].values()
    } == {tuple(denominator["canonical_case_ids"])}
    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_prepare_cli_reports_no_run_or_metrics(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    prepared_status: dict[str, Any],
) -> None:
    monkeypatch.setattr(cb3, "preparation_status", lambda **_: prepared_status)
    result = cb3.main(
        [
            "--repository-root",
            str(REPOSITORY_ROOT),
            "prepare",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    payload = json.loads(captured.out)
    assert payload["status"] == "PREPARED"
    assert payload["qualification_status"] == "NON-QUALIFIED"
    assert payload["run_created"] is False
    assert payload["metrics_created"] is False
    assert captured.err == ""
