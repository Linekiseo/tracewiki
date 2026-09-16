from __future__ import annotations

import functools
import importlib
import json
import sys
import types
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.evaluation import cb6

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_RUNS = REPOSITORY_ROOT / "evals/code/runs"
PRODUCTION_DATABASE = REPOSITORY_ROOT / "var/evidence-rag.sqlite3"


def _file_state(path: Path) -> tuple[bool, int | None, int | None, int | None]:
    if path == PRODUCTION_DATABASE or str(path).startswith(f"{PRODUCTION_DATABASE}-"):
        # The formal database and its sidecars are externally owned mutable state.
        # Tests must neither inspect nor claim invariance for them.
        return False, None, None, None
    if not path.exists():
        return False, None, None, None
    stat = path.stat()
    return True, stat.st_size, stat.st_mtime_ns, stat.st_mode


def _tree_state(path: Path) -> dict[str, tuple[int, int, int]]:
    if not path.exists():
        return {}
    return {
        item.relative_to(path).as_posix(): (
            item.stat().st_size,
            item.stat().st_mtime_ns,
            item.stat().st_mode,
        )
        for item in path.rglob("*")
        if item.is_file()
    }


def _result_status(status: cb6.LabelStatus) -> cb6.ResultStatus:
    return "available" if status == "labeled" else status


def _perfect_results(
    labels: tuple[cb6.CanonicalLabelRecord, ...],
) -> tuple[cb6.ArmCaseResult, ...]:
    return tuple(
        cb6.ArmCaseResult(
            case_id=record.case_id,
            old_commit_sha=record.old_commit_sha,
            new_commit_sha=record.new_commit_sha,
            status=_result_status(record.status),
            symbols=record.expected_symbols,
            change_context_hit=True if record.change_context_required else None,
            duration_ns=index * 1_000_000,
            peak_memory_bytes=index * 100,
        )
        for index, record in enumerate(labels, start=1)
    )


@pytest.fixture
def fixture(tmp_path: Path) -> cb6.CB6Fixture:
    return cb6.build_programmatic_fixture(tmp_path / "git-fixture")


def test_prepare_is_read_only_and_reports_authorized_or_already_completed_state() -> None:
    database_before = _file_state(PRODUCTION_DATABASE)
    sidecars_before = {
        suffix: _file_state(Path(f"{PRODUCTION_DATABASE}{suffix}")) for suffix in ("-wal", "-shm")
    }
    runs_before = _tree_state(REPOSITORY_RUNS)

    prepared = cb6.prepare_cb6(root=REPOSITORY_ROOT)

    existing = cb6._existing_cb6_run_directories(REPOSITORY_RUNS)
    assert len(existing) <= 1
    if existing:
        assert prepared["status"] == "PRODUCTION RUN PRESENT NON-QUALIFIED"
        assert prepared["preparation_status"] == "COMPLETED"
        assert prepared["qualification_status"] == "PROVISIONAL NOT QUALIFIED"
        assert prepared["execution_status"] == "production-run-already-exists"
    else:
        assert prepared["status"] == "PREPARED NON-QUALIFIED"
        assert prepared["preparation_status"] == "PREPARED"
        assert prepared["qualification_status"] == "NON-QUALIFIED"
        assert prepared["execution_status"] == "authorized-ready-for-single-production-run"
    assert prepared["treatment_qualified"] is False
    assert prepared["run_created"] is bool(existing)
    assert prepared["artifact_created"] is bool(existing)
    assert prepared["metrics_created"] is bool(existing)
    assert prepared["fixture_created"] is False
    assert prepared["execution_policy"]["prepared_only_now"] is (not existing)
    assert prepared["execution_policy"]["production_execution_authorized"] is False
    assert prepared["execution_gate"]["status"] == "not-authorized"
    assert prepared["execution_gate"]["engineering_status"] == "incomplete"
    assert prepared["execution_gate"]["production_execution_authorized"] is False
    assert prepared["execution_gate"]["development_authorization_observed"] is True
    assert (
        prepared["execution_gate"]["development_authorization_is_production_authorization"] is False
    )
    assert prepared["production_component"]["status"] == "ready"
    assert prepared["production_component"]["class"] == "DiffSymbolMapper"
    assert prepared["production_component"]["method"] == "map_hunk"
    assert prepared["production_component"]["version"] == cb6.REAL_DIFF_SYMBOL_VERSION
    assert prepared["production_component"]["identity"] == (
        "evidence_rag.rag.sources.code.diff_symbol_v2."
        "DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2"
    )
    assert prepared["production_component"]["identity_policy"] == (
        "frozen-module-class-method-version-exact"
    )
    assert prepared["production_component"]["method_implementation_sha256"].startswith("sha256:")
    assert all(prepared["production_component"]["identity_checks"].values())
    assert prepared["fixture_contract"]["construction"] == "programmatic-temporary-git-only"
    assert prepared["fixture_contract"]["canonical_labels_created_during_prepare"] is False
    assert prepared["fixture_contract"]["case_count"] == 12
    assert set(prepared["fixture_contract"]["coverage"]) == {
        "python-function-body",
        "python-function-signature",
        "python-class-level",
        "python-file-top-level",
        "rename",
        "add",
        "delete",
        "multi-symbol",
        "whitespace-only",
        "binary",
        "wrong-parent",
        "ambiguous",
    }
    assert prepared["treatment_contract"]["order"] == list(cb6.TREATMENT_ORDER)
    expected_metric_status = "production-observed" if existing else "pending-production-execution"
    assert all(
        contract["status"] == expected_metric_status
        for contract in prepared["metric_contract"].values()
    )
    assert prepared["label_contract"]["quality_values_created"] is bool(existing)
    assert prepared["controls"]["qualified_global_retrieval_baseline"] == {
        "name": "C-B0",
        "run_id": cb6.CB0_QUALIFIED_RUN_ID,
        "treatment_qualified": True,
        "scope": "global-retrieval-anchor-only",
        "replaced_by_cb6": False,
    }
    audits = prepared["controls"]["audit_controls"]
    assert set(audits) == {"C-B1", "C-B2", "C-B3", "C-B4", "C-B5"}
    assert all(item["treatment_qualified"] is False for item in audits.values())
    assert all(item["qualification_use"] == "forbidden" for item in audits.values())
    assert prepared["controls"]["cb6_scope"] == {
        "kind": "independent-diff-binding-quality-audit",
        "replaces_cb0": False,
    }
    if existing:
        assert prepared["non_execution_reasons"] == [
            "final C-B6 production execution decision is not exact AUTHORIZED",
            "the one-shot C-B6 production Run already exists",
        ]
        assert prepared["existing_run_verification"]["status"] == "verified"
    else:
        assert prepared["non_execution_reasons"] == []
        assert prepared["existing_run_verification"] is None

    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert {
        suffix: _file_state(Path(f"{PRODUCTION_DATABASE}{suffix}")) for suffix in ("-wal", "-shm")
    } == sidecars_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_current_post_run_gate_is_not_reusable_and_historical_failure_fails_closed() -> None:
    gate = REPOSITORY_ROOT / cb6.C6_GATE_RELATIVE_PATH
    parsed = cb6.parse_c6_02_authorization(gate.read_text(encoding="utf-8"))

    assert parsed["status"] == "not-authorized"
    assert parsed["engineering_status"] == "incomplete"
    assert parsed["production_execution_authorized"] is False
    assert parsed["decision_block_count"] >= 2
    assert parsed["reason"] == "final C-B6 production execution decision is not exact AUTHORIZED"

    historical_only = f"""
# Historical failure 2026-07-28 17:00
```text
C6-02 engineering FAIL
P0 findings: 0
P1 findings: 1
C-B6 PRODUCTION EXECUTION NOT AUTHORIZED
C-B6 production component identity: {cb6.PRODUCTION_COMPONENT_IDENTITY}
```

# Development authorization 2026-07-28 18:00
```text
C6-01 engineering PASS
P0 findings: 0
P1 findings: 0
C6-02 AUTHORIZED
```
"""
    parsed = cb6.parse_c6_02_authorization(historical_only)
    assert parsed["status"] == "not-authorized"
    assert parsed["engineering_status"] == "failed"
    assert parsed["production_execution_authorized"] is False


def test_authorization_parser_selects_only_the_final_exact_complete_block() -> None:
    text = f"""
# Historical failure 2026-07-28 17:00
```text
C6-02 engineering FAIL
P0 findings: 0
P1 findings: 2
C-B6 PRODUCTION EXECUTION NOT AUTHORIZED
C-B6 production component identity: {cb6.PRODUCTION_COMPONENT_IDENTITY}
```

# Final C6-02 review 2026-07-29 09:30
```text
C6-02 engineering PASS
P0 findings: 0
P1 findings: 0
C-B6 PRODUCTION EXECUTION AUTHORIZED
C-B6 production component identity: {cb6.PRODUCTION_COMPONENT_IDENTITY}
```
"""
    parsed = cb6.parse_c6_02_authorization(text)
    assert parsed["status"] == "authorized"
    assert parsed["engineering_status"] == "passed"
    assert parsed["production_execution_authorized"] is True
    assert parsed["decision_block_count"] == 2
    assert parsed["selected_block_index"] == 1
    assert parsed["selected_block_timestamp"] == "2026-07-29 09:30"
    assert parsed["decision_record"] == {
        "engineering_decision": cb6.C6_02_ENGINEERING_DECISION,
        "p0_findings": 0,
        "p1_findings": 0,
        "execution_decision": cb6.C6_02_EXECUTION_DECISION,
        "production_component_identity": cb6.PRODUCTION_COMPONENT_IDENTITY,
    }
    assert parsed["decision_block_hash"].startswith("sha256:")

    later_fail = (
        text
        + f"""
# Revoked 2026-07-29 10:30
```text
C6-02 engineering FAIL
P0 findings: 0
P1 findings: 1
C-B6 PRODUCTION EXECUTION NOT AUTHORIZED
C-B6 production component identity: {cb6.PRODUCTION_COMPONENT_IDENTITY}
```
"""
    )
    revoked = cb6.parse_c6_02_authorization(later_fail)
    assert revoked["status"] == "not-authorized"
    assert revoked["selected_block_index"] == 2
    assert revoked["engineering_status"] == "failed"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            """
C6-02 engineering PASS
P0 findings: 0
P1 findings: 0
C-B6 PRODUCTION EXECUTION AUTHORIZED
""",
            "component identity",
        ),
        (
            f"""
C6-02 engineering PASS
P0 findings: 0
P1 findings: 0
C-B6 PRODUCTION EXECUTION AUTHORIZED
C-B6 PRODUCTION EXECUTION AUTHORIZED
C-B6 production component identity: {cb6.PRODUCTION_COMPONENT_IDENTITY}
""",
            "exact AUTHORIZED",
        ),
        (
            """
C6-02 engineering PASS
P0/P1 = 1
C-B6 PRODUCTION EXECUTION AUTHORIZED
C-B6 production component identity: fake.module.FakeBinder
""",
            "P0=0 and P1=0",
        ),
    ],
)
def test_authorization_parser_rejects_missing_duplicate_or_wrong_evidence(
    body: str,
    reason: str,
) -> None:
    parsed = cb6.parse_c6_02_authorization(body)
    assert parsed["status"] == "not-authorized"
    assert reason in parsed["reason"]
    assert parsed["production_execution_authorized"] is False


def test_authorization_parser_accepts_exact_combined_zero_findings() -> None:
    parsed = cb6.parse_c6_02_authorization(
        f"""
C6-02 engineering PASS
P0/P1 = 0
C-B6 PRODUCTION EXECUTION AUTHORIZED
C-B6 production component identity: {cb6.PRODUCTION_COMPONENT_IDENTITY}
"""
    )
    assert parsed["status"] == "authorized"


class _FakeDiffSymbolMapper:
    def map_hunk(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs


class _DiffSymbolMapperWrapper:
    def __init__(self, component: Any) -> None:
        self.component = component

    def map_hunk(self, *args: Any, **kwargs: Any) -> Any:
        return self.component.map_hunk(*args, **kwargs)


def _assert_exact_production_identity_ready() -> tuple[Any, Any, dict[str, Any]]:
    production_module = importlib.import_module(cb6.DIFF_SYMBOL_MODULE)
    public_module = importlib.import_module(cb6.PUBLIC_CODE_MODULE)
    production = production_module.DiffSymbolMapper()
    contract = cb6.production_component_contract()
    required = cb6.require_production_component_identity(production)
    assert required == {
        **contract,
        "bound_method_exact": True,
    }
    assert contract["identity"] == (
        "evidence_rag.rag.sources.code.diff_symbol_v2."
        "DiffSymbolMapper.map_hunk@c6-diff-symbol-mapper-v2"
    )
    assert contract["identity_policy"] == "frozen-module-class-method-version-exact"
    assert contract["method_implementation_sha256"].startswith("sha256:")
    assert all(contract["identity_checks"].values())
    assert required["bound_method_exact"] is True
    assert production.map_hunk.__func__ is production_module.DiffSymbolMapper.map_hunk
    assert public_module.DiffSymbolMapper is production_module.DiffSymbolMapper
    return production_module, public_module, contract


def test_fake_injected_wrapped_and_subclassed_identity_is_rejected(
    tmp_path: Path,
) -> None:
    production_module, _, _ = _assert_exact_production_identity_ready()

    with pytest.raises(cb6.CB6Error, match="fake, injected, wrapped"):
        cb6.require_production_component_identity(_FakeDiffSymbolMapper())
    with pytest.raises(cb6.CB6Error, match="fake, injected, wrapped"):
        cb6.require_production_component_identity(
            _DiffSymbolMapperWrapper(production_module.DiffSymbolMapper())
        )
    with pytest.raises(cb6.CB6Error, match="fake, injected, wrapped"):
        cb6.require_production_component_identity(production_module.DiffSymbolMapper)

    class _Subclass(production_module.DiffSymbolMapper):
        pass

    with pytest.raises(cb6.CB6Error, match="subclassed"):
        cb6.require_production_component_identity(_Subclass())

    output = tmp_path / "runs"
    with pytest.raises(cb6.CB6Error, match="forbids fake, injected, wrapped"):
        cb6.execute_cb6(
            root=REPOSITORY_ROOT,
            runs_dir=output,
            diff_symbol_component=_FakeDiffSymbolMapper(),
            c6_02_gate_approved=True,
        )
    assert not output.exists()


def test_method_replacement_wrapper_and_instance_injection_fail_closed() -> None:
    production_module, _, original_contract = _assert_exact_production_identity_ready()
    component_type = production_module.DiffSymbolMapper
    original_method = component_type.map_hunk

    def replacement(self: Any, *args: Any, **kwargs: Any) -> dict[str, bool]:
        del self, args, kwargs
        return {"fake": True}

    try:
        component_type.map_hunk = replacement
        pending = cb6.production_component_contract()
        assert pending["status"] == "pending"
        assert pending["identity_checks"]["method_function_exact"] is False
        assert pending["identity_checks"]["method_implementation_exact"] is False
        with pytest.raises(cb6.CB6Error, match="map_hunk@version identity"):
            cb6.require_production_component_identity(component_type())
    finally:
        component_type.map_hunk = original_method
    assert cb6.production_component_contract() == original_contract

    @functools.wraps(original_method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        return original_method(self, *args, **kwargs)

    try:
        component_type.map_hunk = wrapped
        pending = cb6.production_component_contract()
        assert pending["status"] == "pending"
        assert pending["identity_checks"]["method_function_exact"] is False
        with pytest.raises(cb6.CB6Error, match="map_hunk@version identity"):
            cb6.require_production_component_identity(component_type())
    finally:
        component_type.map_hunk = original_method
    assert cb6.production_component_contract() == original_contract

    original_code = original_method.__code__
    try:
        original_method.__code__ = replacement.__code__
        pending = cb6.production_component_contract()
        assert pending["status"] == "pending"
        assert pending["identity_checks"]["method_function_exact"] is True
        assert pending["identity_checks"]["method_implementation_exact"] is False
        with pytest.raises(cb6.CB6Error, match="map_hunk@version identity"):
            cb6.require_production_component_identity(component_type())
    finally:
        original_method.__code__ = original_code
    assert cb6.production_component_contract() == original_contract

    instance = component_type()
    try:
        instance.map_hunk = lambda *args, **kwargs: {"fake": True}
        assert cb6.production_component_contract()["status"] == "ready"
        with pytest.raises(cb6.CB6Error, match="map_hunk@version identity"):
            cb6.require_production_component_identity(instance)
    finally:
        del instance.map_hunk
    assert cb6.require_production_component_identity(instance)["bound_method_exact"] is True


def test_same_module_named_class_and_public_export_spoofs_fail_closed() -> None:
    production_module, public_module, original_contract = _assert_exact_production_identity_ready()
    original_class = production_module.DiffSymbolMapper
    original_public_export = public_module.DiffSymbolMapper
    original_method = original_class.map_hunk
    fake_class = type(
        "DiffSymbolMapper",
        (),
        {
            "__module__": cb6.DIFF_SYMBOL_MODULE,
            "mapper_version": cb6.REAL_DIFF_SYMBOL_VERSION,
            "map_hunk": original_method,
        },
    )

    try:
        production_module.DiffSymbolMapper = fake_class
        public_module.DiffSymbolMapper = fake_class
        pending = cb6.production_component_contract()
        assert pending["status"] == "pending"
        assert pending["identity_checks"]["class_object_exact"] is False
        assert pending["identity_checks"]["public_export_exact"] is False
        with pytest.raises(cb6.CB6Error, match="fake, injected, wrapped"):
            cb6.require_production_component_identity(fake_class())
    finally:
        production_module.DiffSymbolMapper = original_class
        public_module.DiffSymbolMapper = original_public_export
    assert cb6.production_component_contract() == original_contract

    try:
        public_module.DiffSymbolMapper = fake_class
        pending = cb6.production_component_contract()
        assert pending["status"] == "pending"
        assert pending["identity_checks"]["class_object_exact"] is True
        assert pending["identity_checks"]["public_export_exact"] is False
        with pytest.raises(cb6.CB6Error, match="map_hunk@version identity"):
            cb6.require_production_component_identity(original_class())
    finally:
        public_module.DiffSymbolMapper = original_public_export
    _assert_exact_production_identity_ready()


def test_module_object_and_version_replacement_fail_closed() -> None:
    production_module, public_module, original_contract = _assert_exact_production_identity_ready()
    component_type = production_module.DiffSymbolMapper
    fake_module = types.ModuleType(cb6.DIFF_SYMBOL_MODULE)
    fake_module.DiffSymbolMapper = component_type
    fake_module.DIFF_SYMBOL_MAPPER_VERSION = cb6.REAL_DIFF_SYMBOL_VERSION
    for name in (
        "DiffHunkVersion",
        "SymbolVersionRef",
        "DiffSymbolTreatment",
        "DiffSymbolEvaluation",
    ):
        setattr(fake_module, name, getattr(production_module, name))

    original_sys_module = sys.modules[cb6.DIFF_SYMBOL_MODULE]
    try:
        sys.modules[cb6.DIFF_SYMBOL_MODULE] = fake_module
        pending = cb6.production_component_contract()
        assert pending["status"] == "pending"
        assert pending["identity_checks"]["module_object_exact"] is False
        with pytest.raises(cb6.CB6Error, match="map_hunk@version identity"):
            cb6.require_production_component_identity(component_type())
    finally:
        sys.modules[cb6.DIFF_SYMBOL_MODULE] = original_sys_module
    assert cb6.production_component_contract() == original_contract

    fake_public_module = types.ModuleType(cb6.PUBLIC_CODE_MODULE)
    fake_public_module.DiffSymbolMapper = component_type
    original_public_sys_module = sys.modules[cb6.PUBLIC_CODE_MODULE]
    try:
        sys.modules[cb6.PUBLIC_CODE_MODULE] = fake_public_module
        pending = cb6.production_component_contract()
        assert pending["status"] == "pending"
        assert pending["identity_checks"]["public_module_object_exact"] is False
        with pytest.raises(cb6.CB6Error, match="map_hunk@version identity"):
            cb6.require_production_component_identity(component_type())
    finally:
        sys.modules[cb6.PUBLIC_CODE_MODULE] = original_public_sys_module
    assert sys.modules[cb6.PUBLIC_CODE_MODULE] is public_module
    assert cb6.production_component_contract() == original_contract

    original_version = production_module.DIFF_SYMBOL_MAPPER_VERSION
    try:
        production_module.DIFF_SYMBOL_MAPPER_VERSION = "c6-diff-symbol-mapper-fake"
        pending = cb6.production_component_contract()
        assert pending["status"] == "pending"
        assert pending["identity_checks"]["module_version_exact"] is False
        with pytest.raises(cb6.CB6Error, match="map_hunk@version identity"):
            cb6.require_production_component_identity(component_type())
    finally:
        production_module.DIFF_SYMBOL_MAPPER_VERSION = original_version
    assert cb6.production_component_contract() == original_contract

    original_class_version = component_type.mapper_version
    try:
        component_type.mapper_version = "c6-diff-symbol-mapper-fake"
        pending = cb6.production_component_contract()
        assert pending["status"] == "pending"
        assert pending["identity_checks"]["class_version_exact"] is False
        with pytest.raises(cb6.CB6Error, match="map_hunk@version identity"):
            cb6.require_production_component_identity(component_type())
    finally:
        component_type.mapper_version = original_class_version
    _assert_exact_production_identity_ready()


def test_execute_rejects_before_gate_and_never_creates_a_run_directory(tmp_path: Path) -> None:
    database_before = _file_state(PRODUCTION_DATABASE)
    runs_before = _tree_state(REPOSITORY_RUNS)
    output = tmp_path / "not-created"

    with pytest.raises(cb6.CB6Error, match="explicit C6-02 completion Gate approval"):
        cb6.execute_cb6(root=REPOSITORY_ROOT, runs_dir=output)
    assert not output.exists()

    with pytest.raises(cb6.CB6Error, match="no test/fake artifact execution mode"):
        cb6.execute_cb6(
            root=REPOSITORY_ROOT,
            runs_dir=output,
            execution_mode="test",
        )
    assert not output.exists()
    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_real_production_three_arm_observations_are_honest_and_share_membership(
    fixture: cb6.CB6Fixture,
) -> None:
    database_before = _file_state(PRODUCTION_DATABASE)
    runs_before = _tree_state(REPOSITORY_RUNS)

    arms, raw_arms, evidence = cb6._execute_production_treatments(fixture)
    metrics = cb6._production_metrics(fixture, arms)
    qualification = cb6._quality_decision(metrics)

    assert tuple(arms) == cb6.TREATMENT_ORDER
    assert tuple(raw_arms) == cb6.TREATMENT_ORDER
    assert evidence["production_component"]["status"] == "ready"
    assert evidence["production_component"]["identity"] == cb6.PRODUCTION_COMPONENT_IDENTITY
    assert evidence["production_component"]["method_implementation_sha256"].startswith("sha256:")
    assert evidence["lineage"]["status"] == "confirmed"
    assert evidence["lineage"]["confirmed_count"] > 0
    assert evidence["lineage"]["rename_hint_count"] == 1
    assert evidence["denominator"]["case_count"] == 12
    assert evidence["denominator"]["same_denominator"] is True
    assert evidence["denominator"]["same_parent_target_membership"] is True
    assert len(set(evidence["denominator"]["arm_membership_digests"].values())) == 1

    for arm in cb6.TREATMENT_ORDER:
        by_case = {result.case_id: result for result in arms[arm]}
        assert len(by_case) == 12
        assert by_case["cb6-whitespace-only"].status == "noise"
        assert by_case["cb6-binary"].status == "unavailable"
        assert by_case["cb6-wrong-parent"].status == "rejected"
        # The canonical label is ambiguous, while the exact production mapper
        # currently emits two FileVersion predictions. Preserve that mismatch.
        assert by_case["cb6-ambiguous"].status == "available"
        raw_by_case = {item["case_id"]: item for item in raw_arms[arm]}
        assert raw_by_case["cb6-wrong-parent"]["production_mapper_called"] is False
        assert all(
            item["production_mapper_called"] is True
            for case_id, item in raw_by_case.items()
            if case_id != "cb6-wrong-parent"
        )
        arm_metrics = metrics["arms"][arm]
        assert arm_metrics["production_result"] is True
        assert arm_metrics["measurement_source"] == "production-cb6-run"
        assert arm_metrics["label_status"]["labeled_case_count"] == 8
        assert arm_metrics["label_status"]["canonical_symbol_label_count"] == 16
        assert arm_metrics["overall"] == {
            "true_positive": 16,
            "false_positive": 2,
            "false_negative": 0,
            "precision": 0.888888888889,
            "recall": 1.0,
            "f1": 0.941176470588,
        }
        assert arm_metrics["change_context"]["recall"] == 1.0
        assert arm_metrics["false_affected_symbol"] == {
            "count": 2,
            "predicted_symbol_count": 18,
            "rate": 0.111111111111,
        }
        statuses = arm_metrics["ambiguity_noise_unavailable"]
        assert statuses["expected_status_counts"] == {
            "ambiguous": 1,
            "labeled": 8,
            "noise": 1,
            "rejected": 1,
            "unavailable": 1,
        }
        assert statuses["observed_status_counts"] == {
            "available": 9,
            "noise": 1,
            "rejected": 1,
            "unavailable": 1,
        }
        assert arm_metrics["performance"]["observation_count"] == 12
        assert arm_metrics["performance"]["latency_ms"]["p50"] >= 0
        assert arm_metrics["performance"]["latency_ms"]["p95"] >= 0
        assert arm_metrics["performance"]["memory_bytes"]["peak"] >= 0

    assert qualification["status"] == "PROVISIONAL NOT QUALIFIED"
    assert qualification["treatment_qualified"] is False
    assert qualification["checks"]["sample_size"] is False
    assert qualification["checks"]["precision"] is False
    assert qualification["checks"]["false_affected_rate"] is False
    assert qualification["engineering_pass_is_quality_qualification"] is False
    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_programmatic_git_fixture_is_reproducible_complete_and_portable(
    tmp_path: Path,
) -> None:
    first = cb6.build_programmatic_fixture(tmp_path / "one")
    second = cb6.build_programmatic_fixture(tmp_path / "two")

    assert dict(first.commits) == dict(second.commits)
    assert first.membership_digest == second.membership_digest
    assert first.membership_digest == cb6.FROZEN_LABEL_MEMBERSHIP_DIGEST
    assert cb6._fingerprint(first.canonical_payload()) == cb6.FROZEN_FIXTURE_DIGEST
    assert [record.to_dict() for record in first.labels] == [
        record.to_dict() for record in second.labels
    ]
    validation = cb6.validate_programmatic_fixture(first)
    assert validation == {
        "status": "valid",
        "commit_count": 3,
        "case_count": 12,
        "labeled_case_count": 8,
        "canonical_symbol_label_count": 16,
        "membership_digest": first.membership_digest,
        "temporary_repository_in_portable_identity": False,
    }

    by_id = {record.case_id: record for record in first.labels}
    assert tuple(sorted(by_id)) == tuple(sorted(cb6.FIXTURE_CASE_IDS))
    assert by_id["cb6-function-body"].expected_symbols[0].qualified_name == ("Calculator.compute")
    assert by_id["cb6-function-signature"].expected_symbols[0].qualified_name == "helper"
    assert by_id["cb6-class-level"].expected_symbols[0].qualified_name == "Calculator"
    assert by_id["cb6-file-top-level"].expected_symbols[0].qualified_name == "<module>"
    assert by_id["cb6-rename"].old_path == "src/legacy.py"
    assert by_id["cb6-rename"].new_path == "src/modern.py"
    assert {item.role for item in by_id["cb6-rename"].expected_symbols} == {"renamed"}
    assert {item.role for item in by_id["cb6-add"].expected_symbols} == {"introduced"}
    assert {item.role for item in by_id["cb6-delete"].expected_symbols} == {"removed"}
    assert len(by_id["cb6-multi-symbol"].expected_symbols) == 4
    assert by_id["cb6-whitespace-only"].status == "noise"
    assert by_id["cb6-binary"].status == "unavailable"
    assert by_id["cb6-wrong-parent"].status == "rejected"
    assert by_id["cb6-wrong-parent"].old_commit_sha != by_id["cb6-wrong-parent"].target_parent_sha
    assert by_id["cb6-ambiguous"].status == "ambiguous"
    assert len(by_id["cb6-ambiguous"].ambiguity_candidates) == 2
    assert all(record.old_commit_sha for record in first.labels)
    assert all(record.new_commit_sha for record in first.labels)

    portable = first.canonical_payload()
    serialized = json.dumps(portable, sort_keys=True)
    assert str(first.repository) not in serialized
    assert str(second.repository) not in serialized
    cb6.validate_portable_payload(portable)


def test_label_membership_digest_detects_tampering(
    fixture: cb6.CB6Fixture,
) -> None:
    assert (
        cb6.validate_label_records(
            fixture.labels,
            expected_digest=fixture.membership_digest,
        )
        == fixture.membership_digest
    )

    tampered = list(fixture.labels)
    tampered[0] = replace(tampered[0], note=tampered[0].note + " tampered")
    with pytest.raises(cb6.CB6Error, match="membership digest mismatch"):
        cb6.validate_label_records(
            tuple(tampered),
            expected_digest=fixture.membership_digest,
        )

    wrong_membership = list(fixture.labels)
    wrong_membership[0] = replace(wrong_membership[0], case_id="cb6-unknown")
    wrong_membership.sort(key=lambda record: record.case_id)
    with pytest.raises(cb6.CB6Error, match="frozen fixture cases"):
        cb6.validate_label_records(tuple(wrong_membership))


def test_all_three_arms_require_the_same_case_and_commit_denominator(
    fixture: cb6.CB6Fixture,
) -> None:
    perfect = _perfect_results(fixture.labels)
    arms = {arm: perfect for arm in cb6.TREATMENT_ORDER}
    denominator = cb6.require_same_denominator(fixture.labels, arms)

    assert denominator["case_count"] == 12
    assert denominator["same_denominator"] is True
    assert denominator["same_parent_target_membership"] is True
    assert len(set(denominator["arm_membership_digests"].values())) == 1

    changed = list(perfect)
    changed[0] = replace(changed[0], old_commit_sha="0" * 40)
    mismatched = {
        cb6.TREATMENT_ORDER[0]: perfect,
        cb6.TREATMENT_ORDER[1]: tuple(changed),
        cb6.TREATMENT_ORDER[2]: perfect,
    }
    with pytest.raises(cb6.CB6Error, match="commit membership drifted"):
        cb6.require_same_denominator(fixture.labels, mismatched)


def test_metric_math_is_derived_from_fixture_labels_and_cannot_claim_production(
    fixture: cb6.CB6Fixture,
) -> None:
    perfect = list(_perfect_results(fixture.labels))
    target_index = next(
        index for index, result in enumerate(perfect) if result.case_id == "cb6-function-body"
    )
    target = perfect[target_index]
    canonical_new = next(symbol for symbol in target.symbols if symbol.side == "new")
    false_old = replace(
        next(symbol for symbol in target.symbols if symbol.side == "old"),
        symbol_id="python:src/calculator.py:unrelated",
        qualified_name="unrelated",
        lineage_id="lineage:unrelated",
    )
    perfect[target_index] = replace(
        target,
        symbols=(canonical_new, false_old),
        change_context_hit=False,
    )

    metrics = cb6.calculate_arm_metrics(
        fixture.labels,
        tuple(perfect),
        arm="line_overlap_legacy",
    )
    assert metrics["measurement_source"] == "unit-test-fixture-values"
    assert metrics["production_result"] is False
    assert metrics["quality_claim"] == "forbidden"
    assert metrics["label_status"] == {
        "record_count": 12,
        "status_counts": {
            "ambiguous": 1,
            "labeled": 8,
            "noise": 1,
            "rejected": 1,
            "unavailable": 1,
        },
        "labeled_case_count": 8,
        "canonical_symbol_label_count": 16,
        "membership_digest": fixture.membership_digest,
        "labels_are_programmatic_fixture_truth": True,
        "labels_are_production_results": False,
    }
    assert metrics["overall"] == {
        "true_positive": 15,
        "false_positive": 1,
        "false_negative": 1,
        "precision": 0.9375,
        "recall": 0.9375,
        "f1": 0.9375,
    }
    assert metrics["by_side"]["old"]["precision"] == 0.875
    assert metrics["by_side"]["old"]["recall"] == 0.875
    assert metrics["by_side"]["new"]["precision"] == 1.0
    assert metrics["by_side"]["new"]["recall"] == 1.0
    assert metrics["by_role"]["introduced"]["f1"] == 1.0
    assert metrics["by_role"]["removed"]["f1"] == 1.0
    assert metrics["by_role"]["renamed"]["f1"] == 1.0
    assert metrics["by_role"]["modified"]["true_positive"] == 11
    assert metrics["change_context"] == {
        "required_case_count": 8,
        "hit_count": 7,
        "recall": 0.875,
    }
    assert metrics["false_affected_symbol"] == {
        "count": 1,
        "predicted_symbol_count": 16,
        "rate": 0.0625,
    }
    status = metrics["ambiguity_noise_unavailable"]
    assert status["ambiguity_case_count"] == 1
    assert status["noise_case_count"] == 1
    assert status["unavailable_case_count"] == 1
    assert status["rejected_case_count"] == 1
    assert status["observed_ambiguity_rate"] == pytest.approx(1 / 12)
    assert status["observed_noise_rate"] == pytest.approx(1 / 12)
    assert status["observed_unavailable_rate"] == pytest.approx(1 / 12)
    assert metrics["performance"] == {
        "observation_count": 12,
        "latency_ms": {"p50": 6.0, "p95": 12.0},
        "memory_bytes": {"p50": 600, "p95": 1200, "peak": 1200},
    }

    with pytest.raises(cb6.CB6Error, match="cannot claim production evidence"):
        cb6.calculate_arm_metrics(
            fixture.labels,
            tuple(perfect),
            arm="line_overlap_legacy",
            measurement_source="production",
        )


def test_three_arm_metric_report_remains_explicitly_non_production(
    fixture: cb6.CB6Fixture,
) -> None:
    perfect = _perfect_results(fixture.labels)
    arms = {arm: perfect for arm in cb6.TREATMENT_ORDER}
    report = cb6.calculate_test_fixture_metrics(fixture.labels, arms)

    assert report["production_result"] is False
    assert report["quality_claim"] == "forbidden"
    assert report["denominator"]["same_denominator"] is True
    assert tuple(report["arms"]) == cb6.TREATMENT_ORDER
    assert all(values["overall"]["f1"] == 1.0 for values in report["arms"].values())
    assert all(values["production_result"] is False for values in report["arms"].values())


@pytest.mark.parametrize(
    "payload",
    [
        {"path": "/Users/developer/private/repo"},
        {"note": "local path is /Users/developer/private/repo"},
        {"path": "C:\\Users\\developer\\repo"},
        {"credential": "api_key=abcdefghijk"},
        {"api_key": "abcdefghijk"},
        {"path": Path("relative/path")},
        {"number": float("nan")},
    ],
)
def test_portable_contract_rejects_paths_secrets_path_objects_and_nonfinite_values(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(cb6.CB6Error, match="portable C-B6"):
        cb6.validate_portable_payload(payload)


def test_verify_is_read_only_and_missing_artifact_stays_missing(tmp_path: Path) -> None:
    artifact = tmp_path / "missing"
    database_before = _file_state(PRODUCTION_DATABASE)
    runs_before = _tree_state(REPOSITORY_RUNS)

    with pytest.raises(cb6.CB6Error, match="non-symlink directory"):
        cb6.verify_artifact(artifact)

    assert not artifact.exists()
    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before
