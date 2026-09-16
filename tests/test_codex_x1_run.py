from __future__ import annotations

import hashlib
import json
import re
import shutil
import socket
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from evidence_rag.evaluation import codex_x1
from evidence_rag.evaluation.codex_x1 import (
    CODEX_X1_CANONICAL_ARTIFACT_FILES,
    CODEX_X1_CX1_01_COMPONENT_SET_HASH,
    CODEX_X1_CX1_02_COMPONENT_SET_HASH,
    CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION,
    CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH,
    CODEX_XB0_CORRECTION_URI,
    Availability,
    CodexX1Arm,
    CodexX1LinkPrediction,
    CodexX1MetricName,
    CodexX1RunRequest,
    CodexX1SecurityFinding,
    CodexX1StatePrediction,
    CodexX1TreatmentRecord,
    CodexX1VerificationError,
    build_codex_x1_smoke_artifact_v1,
    cx1_01_component_identities_v1,
    cx1_02_component_identities_v1,
    derive_codex_x1_membership_v1,
    evaluate_codex_x1_records_v1,
    parse_codex_x1_gate_review_v1,
    parse_codex_x1_production_gate_review_v1,
    prepare_codex_x1_v1,
    run_codex_x1_v1,
    verify_codex_x1_artifact_v1,
)
from evidence_rag.rag.sources.codex.evaluation_v1 import load_codex_golden_v1

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "codex_golden_v1"
CORRECTION_ROOT = (
    PROJECT_ROOT / "evals" / "codex" / "corrections" / "1ccecfa1c9667d0f716c709d1175d570"
)
GATE_REVIEW = (
    PROJECT_ROOT
    / "docs"
    / "rag-optimization"
    / "development"
    / "reviews"
    / "10_CODEX_X1_GATE_REVIEW.md"
)
GATE_SOURCE_TASK = "019f9f27-8d88-7b01-b686-3d4acbc5dcf2"
CX1_02_TASK = "019fab58-b0d0-7c52-8526-dd268c877a84"
HARNESS_TASK = "019fab59-24d7-7a20-8395-b2568dbb192a"


def _joint_gate_section(
    number: str,
    *,
    engineering: str,
    preparation: str,
    p0: int,
    p1: int,
    cx1_authorization: str,
    run_authorization: str,
    cx1_task: str = CX1_02_TASK,
    harness_task: str = HARNESS_TASK,
) -> str:
    major = number.split(".", 1)[0]
    return f"""\
## {major}. CX1-02 + X-T1 harness 联合 Engineering Gate

CX1-02 实施任务：`{cx1_task}`
X-T1 harness 实施任务：`{harness_task}`

### {number} 联合 Gate 结论

`CODEX X1 CX1-02 ENGINEERING {engineering}`

`X-T1 HARNESS PREPARATION {preparation}`

- `P0 findings: {p0}`
- `P1 findings: {p1}`
- `CX1-02 {cx1_authorization}`
- `X-T1 ISOLATED PRODUCTION RUN {run_authorization}`
"""


def _synthetic_gate(*sections: str) -> str:
    return f"# Codex X1 joint Gate\n\nGate 来源任务：`{GATE_SOURCE_TASK}`\n\n" + "\n".join(sections)


def _write_ready_gate(path: Path) -> Path:
    current = GATE_REVIEW.read_text(encoding="utf-8")
    current_decision = parse_codex_x1_gate_review_v1(current)
    next_major = int(current_decision.conclusion_number.split(".", 1)[0]) + 1
    ready = _joint_gate_section(
        f"{next_major}.1",
        engineering="PASS",
        preparation="PASS",
        p0=0,
        p1=0,
        cx1_authorization="AUTHORIZED",
        run_authorization="AUTHORIZED",
    )
    path.write_text(current + "\n" + ready, encoding="utf-8")
    return path


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode()


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _rewrite_checksums(artifact: Path) -> None:
    files = {}
    for name in CODEX_X1_CANONICAL_ARTIFACT_FILES[:-1]:
        raw = (artifact / name).read_bytes()
        files[name] = {"sha256": _sha256(raw), "size": len(raw)}
    payload = {
        "schema_version": "codex-x-t1-event-treatment-v1",
        "artifact_id": json.loads((artifact / "run.json").read_text())["artifact_id"],
        "algorithm": "sha256",
        "files": files,
        "artifact_set_hash": _sha256(_canonical_json(files)),
    }
    (artifact / "checksums.json").write_bytes(_canonical_json(payload) + b"\n")


def _perfect_records() -> tuple[
    codex_x1.CodexX1Membership,
    tuple[CodexX1TreatmentRecord, ...],
]:
    membership = derive_codex_x1_membership_v1(load_codex_golden_v1(GOLDEN_ROOT))
    case_authority = {case.case_id: case.case_authority_sha256 for case in membership.cases}
    ordered: dict[str, list[str]] = {}
    links: dict[str, list[CodexX1LinkPrediction]] = {}
    patches: dict[str, list[CodexX1StatePrediction]] = {}
    validations: dict[str, list[CodexX1StatePrediction]] = {}
    observed_results: dict[str, list[str]] = {}
    output_windows: dict[str, list[str]] = {}
    error_tails: dict[str, list[str]] = {}
    for unit in membership.units["event_order"]:
        ordered[unit.case_id] = list(unit.item_ids)
    for unit in membership.units["call_result"]:
        links.setdefault(unit.case_id, []).append(
            CodexX1LinkPrediction(
                call_id=unit.expected,
                call_item_id=unit.item_ids[0],
                result_item_id=unit.item_ids[1],
            )
        )
        observed_results.setdefault(unit.case_id, []).append(unit.item_ids[1])
    for unit in membership.units["patch"]:
        patches.setdefault(unit.case_id, []).append(
            CodexX1StatePrediction(item_id=unit.item_ids[0], status=unit.expected)
        )
    for unit in membership.units["validation"]:
        validations.setdefault(unit.case_id, []).append(
            CodexX1StatePrediction(item_id=unit.item_ids[0], status=unit.expected)
        )
    for unit in membership.units["false_validation"]:
        validations.setdefault(unit.case_id, []).append(
            CodexX1StatePrediction(
                item_id=unit.item_ids[0],
                status="target_unknown",
            )
        )
    for unit in membership.units["output_window"]:
        output_windows.setdefault(unit.case_id, []).append(unit.item_ids[0])
    for unit in membership.units["error_tail"]:
        error_tails.setdefault(unit.case_id, []).append(unit.item_ids[0])
        observed_results.setdefault(unit.case_id, []).append(unit.item_ids[0])
    records = tuple(
        CodexX1TreatmentRecord(
            arm=CodexX1Arm.CX1_NORMALIZED,
            case_id=case_id,
            membership_sha256=membership.content_sha256,
            ordered_item_ids=tuple(ordered.get(case_id, ())),
            links=tuple(links.get(case_id, ())),
            patches=tuple(patches.get(case_id, ())),
            validations=tuple(validations.get(case_id, ())),
            observed_result_item_ids=tuple(observed_results.get(case_id, ())),
            output_window_item_ids=tuple(output_windows.get(case_id, ())),
            error_tail_item_ids=tuple(error_tails.get(case_id, ())),
            latency_ms=float(index + 1),
            memory_peak_bytes=4096 + index,
            source_record_sha256=case_authority[case_id],
        )
        for index, case_id in enumerate(membership.eligible_case_ids)
    )
    return membership, records


def _metric(
    evaluation: codex_x1.CodexX1Evaluation,
    name: CodexX1MetricName,
) -> codex_x1.CodexX1MetricResult:
    return next(metric for metric in evaluation.metrics if metric.name == name)


def _runs_snapshot() -> tuple[str, ...]:
    root = PROJECT_ROOT / "evals" / "codex" / "runs"
    if not root.exists():
        return ()
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() or path.is_symlink()
        )
    )


def test_gate_parser_uses_final_exact_conclusion_not_historical_fail() -> None:
    historical_fail = _joint_gate_section(
        "1.1",
        engineering="FAIL",
        preparation="FAIL",
        p0=0,
        p1=3,
        cx1_authorization="NOT_AUTHORIZED",
        run_authorization="NOT_AUTHORIZED",
    )
    final_pass = _joint_gate_section(
        "2.1",
        engineering="PASS",
        preparation="PASS",
        p0=0,
        p1=0,
        cx1_authorization="AUTHORIZED",
        run_authorization="AUTHORIZED",
    )
    text = _synthetic_gate(historical_fail, final_pass)
    gate = parse_codex_x1_gate_review_v1(text)
    assert gate.conclusion_number == "2.1"
    assert gate.engineering_decision == "PASS"
    assert gate.harness_preparation_decision == "PASS"
    assert gate.production_authorized is True
    assert CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION in gate.decision_block


def test_gate_parser_last_fail_and_not_authorized_never_become_authorized() -> None:
    old_pass = _joint_gate_section(
        "1.1",
        engineering="PASS",
        preparation="PASS",
        p0=0,
        p1=0,
        cx1_authorization="AUTHORIZED",
        run_authorization="AUTHORIZED",
    )
    final_fail = _joint_gate_section(
        "2.1",
        engineering="FAIL",
        preparation="FAIL",
        p0=0,
        p1=1,
        cx1_authorization="NOT_AUTHORIZED",
        run_authorization="NOT_AUTHORIZED",
    )
    text = _synthetic_gate(old_pass, final_fail)
    gate = parse_codex_x1_gate_review_v1(text)
    assert gate.conclusion_number == "2.1"
    assert gate.engineering_decision == "FAIL"
    assert gate.cx1_02_authorization == "NOT_AUTHORIZED"
    assert gate.isolated_production_run_authorization == "NOT_AUTHORIZED"
    assert gate.production_authorized is False

    malformed = text.replace(
        "`X-T1 ISOLATED PRODUCTION RUN NOT_AUTHORIZED`",
        "`X-T1 ISOLATED PRODUCTION RUN MAYBE_" + CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION + "`",
    )
    with pytest.raises(CodexX1VerificationError):
        parse_codex_x1_gate_review_v1(malformed)


def test_gate_decision_digest_ignores_unrelated_append_but_binds_decision() -> None:
    ready = _joint_gate_section(
        "2.1",
        engineering="PASS",
        preparation="PASS",
        p0=0,
        p1=0,
        cx1_authorization="AUTHORIZED",
        run_authorization="AUTHORIZED",
    )
    text = _synthetic_gate(ready)
    gate = parse_codex_x1_production_gate_review_v1(text)
    assert gate.production_authorized is True
    appended = parse_codex_x1_production_gate_review_v1(
        text + "\n## Appendix\n\nUnrelated audit notes.\n"
    )
    assert appended.decision_block_sha256 == gate.decision_block_sha256
    assert appended.decision_block == gate.decision_block

    tampered = parse_codex_x1_gate_review_v1(text.replace("`P1 findings: 0`", "`P1 findings: 1`"))
    assert tampered.production_authorized is False
    assert tampered.decision_block_sha256 != gate.decision_block_sha256


def test_gate_parser_rejects_missing_token_and_tampered_final_identity() -> None:
    ready = _joint_gate_section(
        "3.1",
        engineering="PASS",
        preparation="PASS",
        p0=0,
        p1=0,
        cx1_authorization="AUTHORIZED",
        run_authorization="AUTHORIZED",
    )
    text = _synthetic_gate(ready)
    with pytest.raises(CodexX1VerificationError, match="harness preparation"):
        parse_codex_x1_gate_review_v1(text.replace("`X-T1 HARNESS PREPARATION PASS`\n", ""))
    with pytest.raises(CodexX1VerificationError, match="identity"):
        parse_codex_x1_gate_review_v1(
            text.replace(CX1_02_TASK, "019fab58-b0d0-7c52-8526-dd268c877a85")
        )
    with pytest.raises(CodexX1VerificationError, match="identity"):
        parse_codex_x1_gate_review_v1(
            text.replace(GATE_SOURCE_TASK, "019f9f27-8d88-7b01-b686-3d4acbc5dcf3")
        )


def test_preparation_binds_gate_golden_correction_membership_and_status_truth() -> None:
    preparation = prepare_codex_x1_v1()
    gate_text = GATE_REVIEW.read_text(encoding="utf-8")
    latest_headings = re.findall(
        r"^### (?P<number>[0-9]+\.[0-9]+) [^\r\n]*联合 Gate 结论[^\r\n]*$",
        gate_text,
        re.MULTILINE,
    )
    assert latest_headings
    exact_latest = parse_codex_x1_gate_review_v1(gate_text)
    assert preparation.status == "PREPARED"
    assert preparation.qualification_status == "NON_QUALIFIED"
    assert preparation.gate == exact_latest
    assert preparation.gate.conclusion_number == latest_headings[-1]
    assert preparation.production_execution == (
        "AUTHORIZED" if exact_latest.production_authorized else "NOT_AUTHORIZED"
    )
    assert preparation.run_created is False
    assert preparation.metrics_created is False
    assert preparation.released_treatment_executed is False
    assert preparation.gate.review_canonical_path == (
        "docs/rag-optimization/development/reviews/10_CODEX_X1_GATE_REVIEW.md"
    )
    assert preparation.gate.gate_source_task_id == GATE_SOURCE_TASK
    assert preparation.gate.cx1_02_task_id == CX1_02_TASK
    assert preparation.gate.harness_task_id == HARNESS_TASK
    assert preparation.gate.decision_block_sha256 == _sha256(
        preparation.gate.decision_block.encode()
    )
    assert preparation.golden_package_hash.endswith(
        "aa293d0eb8744ec3ef31faaf5da9b2e00406c666a44b09854e342fbfe8bb12d1"
    )
    assert preparation.correction_uri == CODEX_XB0_CORRECTION_URI
    assert preparation.correction_artifact_set_hash == CODEX_XB0_CORRECTION_ARTIFACT_SET_HASH
    assert len(preparation.membership.eligible_case_ids) == 23
    assert preparation.membership.denominators == {
        "event_order": 3,
        "call_result": 2,
        "patch": 4,
        "validation": 10,
        "false_validation": 5,
        "output_window": 1,
        "error_tail": 2,
    }
    assert {
        anchor.name: (anchor.numerator, anchor.denominator)
        for anchor in preparation.baseline_anchors
    } == {
        "event_order": (1, 3),
        "call_result": (1, 2),
        "patch": (3, 4),
        "validation": (7, 10),
        "false_validation": (1, 5),
    }
    assert [arm.availability for arm in preparation.arms] == [
        Availability.AVAILABLE,
        Availability.PROVISIONAL,
        (
            Availability.AVAILABLE
            if exact_latest.production_authorized
            else Availability.UNAVAILABLE
        ),
    ]
    assert all(
        arm.eligible_case_ids == preparation.membership.eligible_case_ids
        and arm.membership_sha256 == preparation.membership.content_sha256
        for arm in preparation.arms
    )
    assert preparation.cx1_02_component_set_hash == CODEX_X1_CX1_02_COMPONENT_SET_HASH
    assert len(preparation.cx1_02_components) == 26
    assert preparation.cx1_02_identity_status == "MATCH"
    assert preparation.cx1_02_versions.source_authority_version == ("codex-source-authority-v2")


def test_post_append_reprepare_can_be_exact_ready_without_creating_artifacts(
    tmp_path: Path,
) -> None:
    before = _runs_snapshot()
    current = parse_codex_x1_gate_review_v1(GATE_REVIEW.read_text(encoding="utf-8"))
    expected_major = int(current.conclusion_number.split(".", 1)[0]) + 1
    gate_path = _write_ready_gate(tmp_path / "ready-gate.md")
    preparation = prepare_codex_x1_v1(gate_review=gate_path)
    assert preparation.status == "PREPARED"
    assert preparation.qualification_status == "NON_QUALIFIED"
    assert preparation.production_execution == "AUTHORIZED"
    assert preparation.gate.conclusion_number == f"{expected_major}.1"
    assert preparation.gate.production_authorized is True
    assert preparation.arms[-1].availability == Availability.AVAILABLE
    assert preparation.run_created is False
    assert preparation.metrics_created is False
    assert preparation.released_treatment_executed is False
    assert tuple(tmp_path.iterdir()) == (gate_path,)
    assert _runs_snapshot() == before


def test_membership_is_programmatic_content_addressed_and_not_selectable() -> None:
    dataset = load_codex_golden_v1(GOLDEN_ROOT)
    first = derive_codex_x1_membership_v1(dataset)
    second = derive_codex_x1_membership_v1(dataset)
    assert first == second
    assert first.content_sha256 == second.content_sha256
    assert tuple(first.units) == (
        "event_order",
        "call_result",
        "patch",
        "validation",
        "false_validation",
        "output_window",
        "error_tail",
    )
    assert {unit.case_id for unit in first.units["validation"]} == {
        "codex-v1-025",
        "codex-v1-027",
        "codex-v1-028",
        "codex-v1-029",
        "codex-v1-030",
        "codex-v1-031",
        "codex-v1-032",
        "codex-v1-033",
        "codex-v1-037",
    }
    assert len(first.units["validation"]) == 10
    with pytest.raises(ValueError):
        first.model_copy(
            update={
                "denominators": {
                    **first.denominators,
                    "validation": 9,
                }
            }
        )


def test_tampered_golden_and_correction_authorities_fail_closed(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    shutil.copytree(GOLDEN_ROOT, golden)
    (golden / "cases.jsonl").write_bytes((golden / "cases.jsonl").read_bytes() + b"\n")
    with pytest.raises(Exception, match="hash|canonical|package|fixture"):
        prepare_codex_x1_v1(golden_root=golden)

    correction = tmp_path / "correction"
    shutil.copytree(CORRECTION_ROOT, correction)
    (correction / "predictions.jsonl").write_bytes(
        (correction / "predictions.jsonl").read_bytes() + b"\n"
    )
    with pytest.raises(Exception, match="checksum|source|correction|file"):
        prepare_codex_x1_v1(correction_root=correction)


def test_exact_cx1_identity_rejects_wrapper_public_export_and_subclass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = cx1_01_component_identities_v1()
    assert len(identities) == 9
    assert (
        codex_x1._canonical_sha256(  # noqa: SLF001
            [identity.model_dump(mode="json") for identity in identities]
        )
        == CODEX_X1_CX1_01_COMPONENT_SET_HASH
    )

    from evidence_rag.rag.sources import codex as codex_package
    from evidence_rag.rag.sources.codex import contracts, event_normalizer

    original = event_normalizer.normalize_codex_events_v1

    def wrapper(records: object) -> object:
        return original(records)

    monkeypatch.setattr(event_normalizer, "normalize_codex_events_v1", wrapper)
    with pytest.raises(CodexX1VerificationError, match="identity|wrapped|digest"):
        cx1_01_component_identities_v1()
    monkeypatch.setattr(event_normalizer, "normalize_codex_events_v1", original)

    monkeypatch.setattr(codex_package, "normalize_codex_events_v1", wrapper)
    with pytest.raises(CodexX1VerificationError, match="package export"):
        cx1_01_component_identities_v1()
    monkeypatch.setattr(codex_package, "normalize_codex_events_v1", original)

    original_class = contracts.ObservableCodexItem

    class FakeObservable(original_class):
        pass

    monkeypatch.setattr(contracts, "ObservableCodexItem", FakeObservable)
    with pytest.raises(CodexX1VerificationError, match="identity|digest"):
        cx1_01_component_identities_v1()


def test_exact_cx1_02_identity_rejects_component_export_module_and_version_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    identities = cx1_02_component_identities_v1()
    assert len(identities) == 26
    assert (
        codex_x1._canonical_sha256(  # noqa: SLF001
            [identity.model_dump(mode="json") for identity in identities]
        )
        == CODEX_X1_CX1_02_COMPONENT_SET_HASH
    )

    from evidence_rag.rag.sources import codex as codex_package
    from evidence_rag.rag.sources.codex import facts_v1

    original_builder = facts_v1.build_codex_fact_publication_v1

    def fake_builder(*args: object, **kwargs: object) -> object:
        return original_builder(*args, **kwargs)

    fake_builder.__module__ = facts_v1.__name__
    fake_builder.__qualname__ = original_builder.__qualname__
    with monkeypatch.context() as patch:
        patch.setattr(facts_v1, "build_codex_fact_publication_v1", fake_builder)
        with pytest.raises(CodexX1VerificationError, match="source|digest"):
            cx1_02_component_identities_v1()
        ready_gate = _write_ready_gate(tmp_path / "ready-gate.md")
        blocked = prepare_codex_x1_v1(gate_review=ready_gate)
        assert blocked.cx1_02_identity_status == "MISMATCH"
        assert blocked.production_execution == "NOT_AUTHORIZED"
        assert blocked.arms[-1].availability == Availability.UNAVAILABLE

    with monkeypatch.context() as patch:
        patch.setattr(
            codex_package,
            "build_codex_fact_publication_v1",
            fake_builder,
        )
        with pytest.raises(CodexX1VerificationError, match="package export"):
            cx1_02_component_identities_v1()

    class FakeDerivedFact(facts_v1.CodexDerivedFact):
        pass

    with monkeypatch.context() as patch:
        patch.setattr(facts_v1, "CodexDerivedFact", FakeDerivedFact)
        with pytest.raises(CodexX1VerificationError, match="identity|digest"):
            cx1_02_component_identities_v1()

    with monkeypatch.context() as patch:
        patch.setattr(
            facts_v1,
            "CODEX_DERIVED_FACT_BUILDER_VERSION",
            "codex-derived-fact-builder-v999",
        )
        with pytest.raises(CodexX1VerificationError, match="version"):
            cx1_02_component_identities_v1()

    fake_module = types.ModuleType(facts_v1.__name__)
    fake_module.__dict__.update(facts_v1.__dict__)
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, facts_v1.__name__, fake_module)
        with pytest.raises(CodexX1VerificationError, match="module|binding"):
            cx1_02_component_identities_v1()


def test_metrics_recompute_from_records_with_true_denominators() -> None:
    membership, records = _perfect_records()
    evaluation = evaluate_codex_x1_records_v1(
        membership=membership,
        records=records,
    )
    assert evaluation.status == Availability.AVAILABLE
    assert evaluation.qualification_status == "QUALIFIED"
    assert evaluation.hard_gate_failures == ()
    expected_ratios = {
        CodexX1MetricName.CALL_RESULT_PAIRING_PRECISION: (2, 2, 1.0),
        CodexX1MetricName.CALL_RESULT_PAIRING_RECALL: (2, 2, 1.0),
        CodexX1MetricName.PATCH_STATUS_ACCURACY: (4, 4, 1.0),
        CodexX1MetricName.VALIDATION_PRECISION: (10, 10, 1.0),
        CodexX1MetricName.VALIDATION_RECALL: (10, 10, 1.0),
        CodexX1MetricName.VALIDATION_F1: (1.0, 1, 1.0),
        CodexX1MetricName.FALSE_VALIDATED_RATE: (0, 5, 0.0),
        CodexX1MetricName.EVENT_ORDER_ACCURACY: (3, 3, 1.0),
        CodexX1MetricName.OUTPUT_WINDOW_COVERAGE: (1, 1, 1.0),
        CodexX1MetricName.ERROR_TAIL_COVERAGE: (2, 2, 1.0),
    }
    for name, expected in expected_ratios.items():
        metric = _metric(evaluation, name)
        assert (metric.numerator, metric.denominator, metric.value) == expected
    assert (
        _metric(evaluation, CodexX1MetricName.ERROR_TAIL_COVERAGE).status
        == Availability.PROVISIONAL
    )
    assert _metric(evaluation, CodexX1MetricName.UNLINKED_RESULT_COUNT).value == 2
    assert _metric(evaluation, CodexX1MetricName.UNKNOWN_STATE_COUNT).value == 5


def test_denominator_selection_tamper_and_nonzero_hard_gates_cannot_qualify() -> None:
    membership, records = _perfect_records()
    with pytest.raises(CodexX1VerificationError, match="membership"):
        evaluate_codex_x1_records_v1(
            membership=membership,
            records=records[:-1],
        )

    false_case = membership.units["false_validation"][0]
    index = membership.eligible_case_ids.index(false_case.case_id)
    compromised = records[index].model_copy(
        update={
            "validations": (
                CodexX1StatePrediction(
                    item_id=false_case.item_ids[0],
                    status="passed",
                ),
            ),
            "security_findings": (
                CodexX1SecurityFinding(
                    kind="reasoning",
                    evidence_sha256="sha256:" + "1" * 64,
                ),
                CodexX1SecurityFinding(
                    kind="secret",
                    evidence_sha256="sha256:" + "2" * 64,
                ),
                CodexX1SecurityFinding(
                    kind="acl",
                    evidence_sha256="sha256:" + "3" * 64,
                ),
            ),
        }
    )
    evaluation = evaluate_codex_x1_records_v1(
        membership=membership,
        records=(*records[:index], compromised, *records[index + 1 :]),
    )
    assert evaluation.qualification_status == "NON_QUALIFIED"
    assert evaluation.hard_gate_failures == (
        "false_validated_rate",
        "reasoning_leakage_count",
        "secret_leakage_count",
        "acl_leakage_count",
    )
    assert (
        _metric(
            evaluation,
            CodexX1MetricName.FALSE_VALIDATED_RATE,
        ).numerator
        == 1
    )


def test_programmatic_smoke_is_portable_and_does_not_create_run_or_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _runs_snapshot()

    def forbidden_connect(*_: object, **__: object) -> None:
        raise AssertionError("formal SQLite access is forbidden")

    def forbidden_socket(*_: object, **__: object) -> None:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
    monkeypatch.setattr(socket, "socket", forbidden_socket)
    artifact = tmp_path / "smoke"
    result = build_codex_x1_smoke_artifact_v1(artifact)
    assert result.status == "SMOKE_ONLY"
    assert result.run_created is False
    assert result.metrics_created is False
    assert result.production_execution == "NOT_AUTHORIZED"
    assert set(path.name for path in artifact.iterdir()) == set(CODEX_X1_CANONICAL_ARTIFACT_FILES)
    copied = tmp_path / "copied"
    shutil.copytree(artifact, copied)
    verification = verify_codex_x1_artifact_v1(copied)
    assert verification.portable is True
    assert verification.artifact_set_hash == result.artifact_set_hash
    assert json.loads((copied / "metrics.json").read_text())["status"] == "NOT_CREATED"
    assert _runs_snapshot() == before
    assert not any(
        path.suffix in {".db", ".sqlite", ".sqlite3"}
        or path.name.endswith(("-wal", "-shm", ".pyc"))
        for path in artifact.rglob("*")
    )
    with pytest.raises(CodexX1VerificationError, match="fresh"):
        build_codex_x1_smoke_artifact_v1(artifact)


def test_programmatic_adapter_observable_cx1_facts_sqlite_preflight(tmp_path: Path) -> None:
    from evidence_rag.models import CodexItemRecord
    from evidence_rag.rag.sources.codex.contracts import NormalizedValidationEvent
    from evidence_rag.rag.sources.codex.event_normalizer import normalize_codex_events_v1
    from evidence_rag.rag.sources.codex.facts_v1 import (
        CodexFactPublicationScope,
        SQLiteCodexFactStore,
        build_codex_fact_publication_v1,
    )

    thread = "codex://thread/synthetic-thread"
    turn = f"{thread}/turn/synthetic-turn"
    command_id = f"{turn}/item/CommandExecution%3Asynthetic-test"
    validation_id = f"{turn}/item/ValidationResult%3Avalidation-synthetic-test"
    common = {
        "thread_id": thread,
        "turn_id": turn,
        "source_id": "codex-source:synthetic",
        "generation_id": "codex-generation:synthetic",
        "acl_ref": "project:project-rag",
        "timestamp": None,
    }
    command = CodexItemRecord(
        id=command_id,
        item_id="synthetic-test",
        sequence=1,
        item_type="CommandExecution",
        role=None,
        status="completed",
        name="command",
        content="$ pytest -q tests/test_alpha.py\n1 passed",
        source_locator=f"{command_id}#event=4",
        metadata={
            "call_id": None,
            "exit_code": 0,
            "paths": ["tests/test_alpha.py"],
            "source_event_type": "command_execution",
        },
        **common,
    )
    validation = CodexItemRecord(
        id=validation_id,
        item_id="validation-synthetic-test",
        sequence=1,
        item_type="ValidationResult",
        role=None,
        status="passed",
        name="validation",
        content="Validation passed",
        source_locator=f"{validation_id}#event=4",
        metadata={
            "call_id": None,
            "derived_from_item": "synthetic-test",
            "exit_code": 0,
            "paths": ["tests/test_alpha.py"],
        },
        **common,
    )
    sources, _, _, derived = codex_x1._observable_sources_from_adapter(  # noqa: SLF001
        (command, validation)
    )
    items = sources[thread]
    normalized = normalize_codex_events_v1(items)
    terminal = [
        event for event in normalized.events if isinstance(event, NormalizedValidationEvent)
    ]
    assert len(items) == 3
    assert len(terminal) == 1
    assert terminal[0].state.value == "passed"
    assert derived[(thread, "synthetic-test")] == validation_id
    assert normalized.links
    for link in normalized.links:
        CodexX1LinkPrediction(
            call_id=link.call_id,
            call_item_id=link.call_item_id,
            result_item_id=link.result_item_id,
        )

    scope = CodexFactPublicationScope(
        project_id="project-rag",
        repository_id="project-rag-repository",
        source_id="codex-source:synthetic",
        source_version="synthetic-v1",
        generation_id="codex-generation:synthetic",
        thread_id=thread,
        acl_ref="project:project-rag",
    )
    publication = build_codex_fact_publication_v1(
        scope,
        items,
        expected_normalization=normalized,
    )
    database = tmp_path / "facts.sqlite"
    with SQLiteCodexFactStore(database) as store:
        result = store.publish(publication, source_items=items)
        assert result.active_publication_id == publication.publication_id
        assert store.publication_count() == 1
    assert publication.output_windows


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("byte_tamper", "checksum"),
        ("extra", "canonical file"),
        ("secret", "security"),
        ("absolute", "security"),
        ("nonfinite", "nonfinite|canonical|JSON"),
        ("cross_file", "cross-file|membership"),
    ],
)
def test_smoke_artifact_tamper_security_and_cross_file_fail_closed(
    tmp_path: Path,
    mutation: str,
    match: str,
) -> None:
    artifact = tmp_path / "artifact"
    build_codex_x1_smoke_artifact_v1(artifact)
    if mutation == "byte_tamper":
        (artifact / "metrics.json").write_bytes((artifact / "metrics.json").read_bytes() + b" ")
    elif mutation == "extra":
        (artifact / "injected.pyc").write_bytes(b"compiled")
    elif mutation in {"secret", "absolute", "cross_file"}:
        manifest = json.loads((artifact / "manifest.json").read_text())
        if mutation == "secret":
            manifest["note"] = "password=supersecretvalue"
        elif mutation == "absolute":
            manifest["note"] = "/Users/alice/private/source.py"
        else:
            manifest["membership_sha256"] = "sha256:" + "9" * 64
        (artifact / "manifest.json").write_bytes(_canonical_json(manifest) + b"\n")
        _rewrite_checksums(artifact)
    else:
        (artifact / "latency.json").write_bytes(
            b'{"nonfinite":NaN,"schema_version":"codex-x-t1-event-treatment-v1"}\n'
        )
        _rewrite_checksums(artifact)
    with pytest.raises(CodexX1VerificationError, match=match):
        verify_codex_x1_artifact_v1(artifact)


def test_smoke_artifact_rejects_symlink_and_database_sidecar(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    build_codex_x1_smoke_artifact_v1(artifact)
    (artifact / "manifest.json").unlink()
    (artifact / "manifest.json").symlink_to(artifact / "run.json")
    with pytest.raises(CodexX1VerificationError, match="regular"):
        verify_codex_x1_artifact_v1(artifact)

    artifact = tmp_path / "artifact-db"
    build_codex_x1_smoke_artifact_v1(artifact)
    (artifact / "formal.sqlite-wal").write_bytes(b"SQLite format 3\x00")
    with pytest.raises(CodexX1VerificationError, match="canonical file"):
        verify_codex_x1_artifact_v1(artifact)


def test_future_runner_admits_only_exact_ready_preparation_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _runs_snapshot()
    ready_gate = _write_ready_gate(tmp_path / "ready-gate.md")
    ready = prepare_codex_x1_v1(gate_review=ready_gate)

    def forbidden_connect(*_: object, **__: object) -> None:
        raise AssertionError("runner admission cannot open SQLite")

    def forbidden_socket(*_: object, **__: object) -> None:
        raise AssertionError("runner admission cannot use network")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
    monkeypatch.setattr(socket, "socket", forbidden_socket)
    request = CodexX1RunRequest(
        preparation=ready,
        fixture_root=GOLDEN_ROOT.parent,
        golden_root=GOLDEN_ROOT,
        correction_root=CORRECTION_ROOT,
        source_root=PROJECT_ROOT,
        isolated_root=tmp_path,
        output_root=tmp_path / "output",
        raw_sqlite_path=tmp_path / "raw.sqlite",
        gate_review=ready_gate,
        production_authorization=CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION,
    )
    admission = run_codex_x1_v1(request)
    assert admission.status == "AUTHORIZED_READY"
    assert admission.run_created is False
    assert admission.metrics_created is False
    assert admission.artifact_created is False
    assert admission.output_created is False
    assert admission.database_opened is False
    assert not request.output_root.exists()
    assert not request.raw_sqlite_path.exists()
    assert tuple(tmp_path.iterdir()) == (ready_gate,)
    assert _runs_snapshot() == before

    current = prepare_codex_x1_v1()
    assert current.production_execution == "AUTHORIZED"
    current_request = CodexX1RunRequest(
        preparation=current,
        fixture_root=GOLDEN_ROOT.parent,
        golden_root=GOLDEN_ROOT,
        correction_root=CORRECTION_ROOT,
        source_root=PROJECT_ROOT,
        isolated_root=tmp_path,
        output_root=tmp_path / "blocked-output",
        raw_sqlite_path=tmp_path / "blocked.sqlite",
        gate_review=GATE_REVIEW,
        production_authorization=CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION,
    )
    current_admission = run_codex_x1_v1(current_request)
    assert current_admission.status == "AUTHORIZED_READY"
    assert not current_request.output_root.exists()
    assert not current_request.raw_sqlite_path.exists()
    assert tuple(tmp_path.iterdir()) == (ready_gate,)
    assert _runs_snapshot() == before


def test_future_runner_rejects_old_token_reused_output_and_repository_root(
    tmp_path: Path,
) -> None:
    ready_gate = _write_ready_gate(tmp_path / "ready-gate.md")
    ready = prepare_codex_x1_v1(gate_review=ready_gate)
    common = {
        "preparation": ready,
        "fixture_root": GOLDEN_ROOT.parent,
        "golden_root": GOLDEN_ROOT,
        "correction_root": CORRECTION_ROOT,
        "source_root": PROJECT_ROOT,
        "isolated_root": tmp_path,
        "output_root": tmp_path / "output",
        "raw_sqlite_path": tmp_path / "raw.sqlite",
        "gate_review": ready_gate,
    }
    with pytest.raises(ValueError, match="literal"):
        CodexX1RunRequest(
            **common,
            production_authorization="X-T1 PRODUCTION EXECUTION AUTHORIZED",
        )

    (tmp_path / "output").mkdir()
    with pytest.raises(ValueError, match="fresh"):
        CodexX1RunRequest(
            **common,
            production_authorization=CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION,
        )

    with pytest.raises(ValueError, match="isolated_root"):
        CodexX1RunRequest(
            preparation=ready,
            fixture_root=GOLDEN_ROOT.parent,
            golden_root=GOLDEN_ROOT,
            correction_root=CORRECTION_ROOT,
            source_root=PROJECT_ROOT,
            isolated_root=PROJECT_ROOT,
            output_root=PROJECT_ROOT / "forbidden-output",
            raw_sqlite_path=PROJECT_ROOT / "forbidden.sqlite",
            gate_review=ready_gate,
            production_authorization=CODEX_X1_PRODUCTION_EXECUTION_AUTHORIZATION,
        )
