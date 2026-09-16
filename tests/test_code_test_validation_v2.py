from __future__ import annotations

from dataclasses import FrozenInstanceError
from itertools import product

import pytest

import evidence_rag.rag.sources.code as code_exports
from evidence_rag.rag.sources.code import (
    TEST_VALIDATION_BINDER_VERSION,
    TEST_VALIDATION_CONTRACT_VERSION,
    CoverageEvidence,
    RawObjectRef,
    ValidationDiagnosticCode,
    ValidationTreatmentStatus,
)
from evidence_rag.rag.sources.code import test_validation_v2 as validation
from evidence_rag.rag.sources.code.contracts import CodeRelationType
from evidence_rag.rag.sources.code.graph_v2 import (
    CodeGraphEntityType,
    get_edge_spec,
)

ExecutionStatus = validation.TestExecutionStatus
Observation = validation.TestObservation
ResultV2 = validation.TestResultV2
StatusConsistency = validation.TestStatusConsistency
SymbolBindingStatus = validation.TestSymbolBindingStatus
SymbolEvidence = validation.TestSymbolEvidence
SymbolSignal = validation.TestSymbolSignal
SymbolVersion = validation.TestSymbolVersion
TargetVersion = validation.TestTargetVersion
ValidationBinder = validation.TestValidationBinder
ValidationEdgeRole = validation.TestValidationEdgeRole

SHA_A = "a" * 40
SHA_B = "b" * 40
MANIFEST_A = "sha256:" + "1" * 64
MANIFEST_B = "sha256:" + "2" * 64


def _target(
    *,
    sha: str = SHA_A,
    generation: str = "generation-a",
    project: str = "project-a",
    repository: str = "repository-a",
    acl: str = "acl://team-a",
) -> TargetVersion:
    return TargetVersion.commit(
        project_id=project,
        repository_id=repository,
        commit_sha=sha,
        generation_id=generation,
        acl_ref=acl,
    )


def _dirty_target(
    manifest: str,
    *,
    generation: str = "generation-dirty",
) -> TargetVersion:
    return TargetVersion.worktree(
        project_id="project-a",
        repository_id="repository-a",
        commit_sha=SHA_A,
        generation_id=generation,
        acl_ref="acl://team-a",
        dirty_manifest_sha256=manifest,
    )


def _raw(name: str) -> RawObjectRef:
    return RawObjectRef(
        raw_object_id=f"raw-object://{name}",
        content_sha256="sha256:" + f"{len(name):064x}",
        byte_size=len(name),
        source_name=name,
        media_type="text/plain",
    )


def _result(
    target: TargetVersion,
    *,
    status: ExecutionStatus = ExecutionStatus.PASSED,
    exit_code: int | None = 0,
    observation: Observation = Observation.OBSERVED,
    reported_status: ExecutionStatus | None = None,
    coverage_ref: RawObjectRef | None = None,
    locator: str = "test-run://run-1",
) -> ResultV2:
    return ResultV2.create(
        target=target,
        command=("pytest", "-q", "tests/test_service.py::test_run"),
        environment_ref="environment://python-3.12",
        status=status,
        observation=observation,
        framework="pytest",
        framework_parser_version="pytest-result-parser-v2",
        evidence_locator=locator,
        selector="tests/test_service.py::test_run",
        test_cases=("tests/test_service.py::test_run",),
        exit_code=exit_code,
        duration_ms=42,
        stdout_ref=_raw("stdout"),
        stderr_ref=_raw("stderr"),
        coverage_artifact_ref=coverage_ref,
        reported_status=reported_status,
        observed_at="2026-07-28T20:00:00+08:00",
    )


def _symbol(
    target: TargetVersion,
    *,
    symbol_id: str = "symbol-version://service.run",
) -> SymbolVersion:
    return SymbolVersion(
        target=target,
        symbol_version_id=symbol_id,
        entity_id="code-symbol://service.run",
        entity_type=CodeGraphEntityType.CODE_SYMBOL,
        path="src/service.py",
        evidence_locator=f"code://repository-a@{target.stable_version}/src/service.py#L10-L20",
        symbol_name="run",
        qualified_name="service.run",
        start_line=10,
        end_line=20,
    )


def _coverage(
    result: ResultV2,
    *,
    coverage_id: str = "coverage://run-1/function",
    function: bool = True,
) -> CoverageEvidence:
    artifact = result.coverage_artifact_ref or _raw("coverage")
    return CoverageEvidence(
        coverage_id=coverage_id,
        test_result_id=result.test_result_id,
        target=result.target,
        symbol=_symbol(result.target),
        artifact_ref=artifact,
        parser_version="coverage-py-json-v2",
        evidence_locator=f"coverage-json://run-1/{coverage_id.rsplit('/', 1)[-1]}",
        covered_lines=(12,) if not function else (),
        function_name="service.run" if function else None,
    )


def _symbol_evidence(
    result: ResultV2,
    signal: SymbolSignal,
    *,
    suffix: str | None = None,
    target: TargetVersion | None = None,
) -> SymbolEvidence:
    evidence_target = target or result.target
    suffix = suffix or signal.value
    selector = result.selector if signal is SymbolSignal.EXACT_SELECTOR else None
    symbol = _symbol(evidence_target)
    return SymbolEvidence(
        evidence_id=f"test-symbol-evidence://{suffix}",
        test_result_id=result.test_result_id,
        target=evidence_target,
        test_case_id="test-case://tests/test_service.py::test_run",
        symbol=symbol,
        signal=signal,
        confidence=1.0 if signal.value.startswith("exact") else 0.75,
        parser_version="test-target-parser-v2",
        evidence_locator=f"test-target://{suffix}",
        selector=selector,
        declared_target_symbol_version_id=(
            symbol.symbol_version_id if signal is SymbolSignal.EXACT_TARGET else None
        ),
    )


TRUTH_TABLE_CASES = tuple(
    product(
        (Observation.OBSERVED, Observation.REPORTED),
        (
            ExecutionStatus.PASSED,
            ExecutionStatus.FAILED,
            ExecutionStatus.ERROR,
            ExecutionStatus.UNKNOWN,
        ),
        (None, 0, 1),
        (
            None,
            ExecutionStatus.PASSED,
            ExecutionStatus.FAILED,
            ExecutionStatus.ERROR,
            ExecutionStatus.UNKNOWN,
        ),
    )
)


def _truth_case_id(
    case: tuple[
        Observation,
        ExecutionStatus,
        int | None,
        ExecutionStatus | None,
    ],
) -> str:
    observation, status, exit_code, reported_status = case
    reported = reported_status.value if reported_status is not None else "none"
    return f"{observation.value}-{status.value}-exit={exit_code}-reported={reported}"


def _expected_consistency(
    observation: Observation,
    status: ExecutionStatus,
    exit_code: int | None,
    reported_status: ExecutionStatus | None,
) -> StatusConsistency:
    if observation is Observation.REPORTED:
        return StatusConsistency.REPORTED_ONLY
    if exit_code is None:
        return StatusConsistency.MISSING_EXIT_CODE
    if (status is ExecutionStatus.PASSED and exit_code != 0) or (
        status in {ExecutionStatus.FAILED, ExecutionStatus.ERROR} and exit_code == 0
    ):
        return StatusConsistency.STATUS_EXIT_CONTRADICTION
    if reported_status is not None and reported_status is not status:
        return StatusConsistency.REPORTED_OBSERVED_CONTRADICTION
    return StatusConsistency.CONSISTENT


def _expected_validation_relation(
    consistency: StatusConsistency,
    status: ExecutionStatus,
    exit_code: int | None,
) -> CodeRelationType | None:
    if consistency is not StatusConsistency.CONSISTENT:
        return None
    if status is ExecutionStatus.PASSED and exit_code == 0:
        return CodeRelationType.VALIDATED_BY
    if status in {ExecutionStatus.FAILED, ExecutionStatus.ERROR} or (
        exit_code is not None and exit_code != 0
    ):
        return CodeRelationType.FAILED_VALIDATION
    return None


@pytest.mark.parametrize("case", TRUTH_TABLE_CASES, ids=_truth_case_id)
def test_complete_status_truth_table_has_zero_false_validations(
    case: tuple[
        Observation,
        ExecutionStatus,
        int | None,
        ExecutionStatus | None,
    ],
) -> None:
    observation, status, exit_code, reported_status = case
    target = _target()
    result = _result(
        target,
        status=status,
        exit_code=exit_code,
        observation=observation,
        reported_status=reported_status,
    )

    treatment = ValidationBinder().bind(result, current_target=target)
    validation_edges = {
        edge.relation_type
        for edge in treatment.edges
        if edge.relation_type in {CodeRelationType.VALIDATED_BY, CodeRelationType.FAILED_VALIDATION}
    }
    consistency = _expected_consistency(
        observation,
        status,
        exit_code,
        reported_status,
    )
    expected_relation = _expected_validation_relation(consistency, status, exit_code)
    expected_treatment = {
        CodeRelationType.VALIDATED_BY: ValidationTreatmentStatus.VALIDATED,
        CodeRelationType.FAILED_VALIDATION: ValidationTreatmentStatus.FAILED_VALIDATION,
        None: ValidationTreatmentStatus.OBSERVATION_ONLY,
    }[expected_relation]

    assert result.status_consistency is consistency
    assert treatment.status is expected_treatment
    assert validation_edges == ({expected_relation} if expected_relation is not None else set())
    assert [edge.role for edge in treatment.edges].count(ValidationEdgeRole.HAS_TEST_RESULT) == 1
    assert (CodeRelationType.VALIDATED_BY in validation_edges) is (
        consistency is StatusConsistency.CONSISTENT
        and status is ExecutionStatus.PASSED
        and exit_code == 0
    )
    if consistency is not StatusConsistency.CONSISTENT:
        expected_diagnostic = {
            StatusConsistency.REPORTED_ONLY: ValidationDiagnosticCode.REPORTED_ONLY,
            StatusConsistency.MISSING_EXIT_CODE: (ValidationDiagnosticCode.MISSING_EXIT_CODE),
            StatusConsistency.STATUS_EXIT_CONTRADICTION: (
                ValidationDiagnosticCode.STATUS_EXIT_CONTRADICTION
            ),
            StatusConsistency.REPORTED_OBSERVED_CONTRADICTION: (
                ValidationDiagnosticCode.REPORTED_OBSERVED_CONTRADICTION
            ),
        }[consistency]
        assert expected_diagnostic in {item.code for item in treatment.diagnostics}


@pytest.mark.parametrize(
    "reported_status",
    (
        ExecutionStatus.FAILED,
        ExecutionStatus.ERROR,
        ExecutionStatus.UNKNOWN,
    ),
)
def test_gate_probe_observed_pass_zero_with_conflicting_report_never_validates(
    reported_status: ExecutionStatus,
) -> None:
    target = _target()
    result = _result(
        target,
        status=ExecutionStatus.PASSED,
        exit_code=0,
        observation=Observation.OBSERVED,
        reported_status=reported_status,
    )

    treatment = ValidationBinder().bind(result, current_target=target)

    assert result.status_consistency is (StatusConsistency.REPORTED_OBSERVED_CONTRADICTION)
    assert treatment.status is ValidationTreatmentStatus.OBSERVATION_ONLY
    assert [edge.relation_type for edge in treatment.edges] == [CodeRelationType.CONTAINS]
    assert {item.code for item in treatment.diagnostics} == {
        ValidationDiagnosticCode.REPORTED_OBSERVED_CONTRADICTION
    }


@pytest.mark.parametrize(
    ("status", "exit_code", "expected_consistency", "diagnostic"),
    (
        (
            ExecutionStatus.PASSED,
            0,
            StatusConsistency.CONSISTENT,
            None,
        ),
        (
            ExecutionStatus.PASSED,
            None,
            StatusConsistency.MISSING_EXIT_CODE,
            ValidationDiagnosticCode.MISSING_EXIT_CODE,
        ),
        (
            ExecutionStatus.PASSED,
            1,
            StatusConsistency.STATUS_EXIT_CONTRADICTION,
            ValidationDiagnosticCode.STATUS_EXIT_CONTRADICTION,
        ),
        (
            ExecutionStatus.FAILED,
            0,
            StatusConsistency.STATUS_EXIT_CONTRADICTION,
            ValidationDiagnosticCode.STATUS_EXIT_CONTRADICTION,
        ),
    ),
)
def test_status_consistency_is_frozen_and_diagnosed(
    status: ExecutionStatus,
    exit_code: int | None,
    expected_consistency: StatusConsistency,
    diagnostic: ValidationDiagnosticCode | None,
) -> None:
    result = _result(_target(), status=status, exit_code=exit_code)
    treatment = ValidationBinder().bind(result, current_target=result.target)

    assert result.status_consistency is expected_consistency
    codes = {item.code for item in treatment.diagnostics}
    assert (diagnostic in codes) if diagnostic is not None else not codes
    with pytest.raises(FrozenInstanceError):
        result.exit_code = 9  # type: ignore[misc]


def test_reported_and_observed_status_contradiction_is_not_success() -> None:
    target = _target()
    result = _result(
        target,
        status=ExecutionStatus.FAILED,
        exit_code=0,
        reported_status=ExecutionStatus.PASSED,
    )

    treatment = ValidationBinder().bind(result, current_target=target)

    assert result.status_consistency is StatusConsistency.STATUS_EXIT_CONTRADICTION
    assert treatment.status is ValidationTreatmentStatus.OBSERVATION_ONLY
    assert {item.code for item in treatment.diagnostics} == {
        ValidationDiagnosticCode.REPORTED_OBSERVED_CONTRADICTION,
        ValidationDiagnosticCode.STATUS_EXIT_CONTRADICTION,
    }
    assert [edge.relation_type for edge in treatment.edges] == [CodeRelationType.CONTAINS]


def test_reported_only_preserves_fact_but_never_validation_edge() -> None:
    target = _target()
    result = _result(
        target,
        observation=Observation.REPORTED,
        status=ExecutionStatus.PASSED,
        exit_code=0,
    )

    treatment = ValidationBinder().bind(result, current_target=target)

    assert result.status_consistency is StatusConsistency.REPORTED_ONLY
    assert [edge.relation_type for edge in treatment.edges] == [CodeRelationType.CONTAINS]
    assert treatment.edges[0].role is ValidationEdgeRole.HAS_TEST_RESULT
    assert {item.code for item in treatment.diagnostics} == {ValidationDiagnosticCode.REPORTED_ONLY}


def test_old_result_is_historical_and_cannot_validate_new_commit() -> None:
    old_target = _target(sha=SHA_A, generation="generation-a")
    current_target = _target(sha=SHA_B, generation="generation-b")
    result = _result(old_target)

    treatment = ValidationBinder().bind(result, current_target=current_target)

    assert treatment.status is ValidationTreatmentStatus.HISTORICAL_VALIDATION
    assert treatment.test_result is result
    assert not treatment.edges
    assert not treatment.bindings
    assert [item.code for item in treatment.diagnostics] == [
        ValidationDiagnosticCode.HISTORICAL_TEST_RESULT
    ]


def test_clean_result_becomes_historical_after_current_dirty_change() -> None:
    clean_target = _target()
    dirty_target = _dirty_target(MANIFEST_A)

    treatment = ValidationBinder().bind(
        _result(clean_target),
        current_target=dirty_target,
    )

    assert treatment.status is ValidationTreatmentStatus.HISTORICAL_VALIDATION
    assert not treatment.edges
    assert treatment.diagnostics[0].code is (ValidationDiagnosticCode.HISTORICAL_TEST_RESULT)


def test_conflicting_dirty_manifests_fail_closed() -> None:
    result_target = _dirty_target(MANIFEST_A)
    current_target = _dirty_target(MANIFEST_B)

    treatment = ValidationBinder().bind(
        _result(result_target),
        current_target=current_target,
    )

    assert treatment.status is ValidationTreatmentStatus.REJECTED
    assert treatment.test_result is None
    assert treatment.trace.test_results_seen == 1
    assert not treatment.edges
    assert treatment.diagnostics[0].code is (ValidationDiagnosticCode.DIRTY_MANIFEST_MISMATCH)


@pytest.mark.parametrize(
    ("changed", "diagnostic"),
    (
        ({"project": "project-b"}, ValidationDiagnosticCode.SCOPE_MISMATCH),
        ({"repository": "repository-b"}, ValidationDiagnosticCode.SCOPE_MISMATCH),
        ({"acl": "acl://team-b"}, ValidationDiagnosticCode.SCOPE_MISMATCH),
        (
            {"generation": "generation-b"},
            ValidationDiagnosticCode.GENERATION_MISMATCH,
        ),
    ),
)
def test_scope_acl_and_generation_conflicts_fail_closed(
    changed: dict[str, str],
    diagnostic: ValidationDiagnosticCode,
) -> None:
    current = _target()
    conflicting = _target(**changed)

    treatment = ValidationBinder().bind(
        _result(conflicting),
        current_target=current,
    )

    assert treatment.status is ValidationTreatmentStatus.REJECTED
    assert treatment.test_result is None
    assert not treatment.edges
    assert not treatment.bindings
    assert [item.code for item in treatment.diagnostics] == [diagnostic]


def test_exact_target_identity_conflict_fails_closed() -> None:
    current = _target()
    conflicting = TargetVersion.worktree(
        project_id=current.project_id,
        repository_id=current.repository_id,
        commit_sha=current.commit_sha,
        generation_id=current.generation_id,
        acl_ref=current.acl_ref,
    )

    treatment = ValidationBinder().bind(
        _result(conflicting),
        current_target=current,
    )

    assert treatment.status is ValidationTreatmentStatus.REJECTED
    assert treatment.diagnostics[0].code is (ValidationDiagnosticCode.TARGET_IDENTITY_MISMATCH)


def test_no_result_returns_explicit_missing_context_and_trace() -> None:
    treatment = ValidationBinder().bind(None, current_target=_target())

    assert treatment.status is ValidationTreatmentStatus.MISSING_CONTEXT
    assert treatment.test_result is None
    assert treatment.trace.test_results_seen == 0
    assert treatment.trace.registered_edges == 0
    assert [item.code for item in treatment.diagnostics] == [
        ValidationDiagnosticCode.NO_TEST_RESULT
    ]


def test_coverage_selector_scip_and_heuristic_priority_and_truth() -> None:
    target = _target()
    artifact = _raw("coverage")
    result = _result(target, coverage_ref=artifact)
    coverage_function = _coverage(result, coverage_id="coverage://function")
    coverage_line = _coverage(
        result,
        coverage_id="coverage://line",
        function=False,
    )
    signals = (
        SymbolSignal.EXACT_SELECTOR,
        SymbolSignal.EXACT_TARGET,
        SymbolSignal.SCIP,
        SymbolSignal.IMPORT,
        SymbolSignal.CALL,
        SymbolSignal.PATH_NAME_HEURISTIC,
    )
    evidence = tuple(_symbol_evidence(result, signal) for signal in signals)

    treatment = ValidationBinder().bind(
        result,
        current_target=target,
        coverage_evidence=(coverage_line, coverage_function),
        symbol_evidence=reversed(evidence),
    )

    assert [item.signal for item in treatment.bindings] == [
        SymbolSignal.COVERAGE_FUNCTION,
        SymbolSignal.COVERAGE_LINE,
        SymbolSignal.EXACT_SELECTOR,
        SymbolSignal.EXACT_TARGET,
        SymbolSignal.SCIP,
        SymbolSignal.IMPORT,
        SymbolSignal.CALL,
        SymbolSignal.PATH_NAME_HEURISTIC,
    ]
    confirmed = treatment.bindings[:4]
    candidates = treatment.bindings[4:]
    assert all(
        item.status is SymbolBindingStatus.CONFIRMED and not item.review_required
        for item in confirmed
    )
    assert all(
        item.status is SymbolBindingStatus.CANDIDATE
        and item.review_required
        and item.relation_type is CodeRelationType.TESTS
        for item in candidates
    )
    relation_by_evidence = {edge.evidence_id: edge.relation_type for edge in treatment.edges}
    assert relation_by_evidence[coverage_function.coverage_id] is (CodeRelationType.COVERS)
    assert relation_by_evidence[coverage_line.coverage_id] is CodeRelationType.COVERS
    for item in evidence[:2]:
        assert relation_by_evidence[item.evidence_id] is CodeRelationType.TESTS
    for item in evidence[2:]:
        assert item.evidence_id not in relation_by_evidence
    heuristic = next(
        item for item in treatment.bindings if item.signal is SymbolSignal.PATH_NAME_HEURISTIC
    )
    assert heuristic.relation_type is CodeRelationType.TESTS


def test_wrong_version_symbol_and_coverage_evidence_never_bind() -> None:
    current = _target(sha=SHA_B, generation="generation-b")
    old = _target(sha=SHA_A, generation="generation-a")
    result = _result(current)
    old_result = _result(old, locator="test-run://old")
    old_coverage = _coverage(old_result)
    old_selector = _symbol_evidence(
        result,
        SymbolSignal.EXACT_SELECTOR,
        target=old,
    )

    treatment = ValidationBinder().bind(
        result,
        current_target=current,
        coverage_evidence=(old_coverage,),
        symbol_evidence=(old_selector,),
    )

    assert not treatment.bindings
    assert all(
        edge.relation_type not in {CodeRelationType.COVERS, CodeRelationType.TESTS}
        for edge in treatment.edges
    )
    assert {item.code for item in treatment.diagnostics} == {
        ValidationDiagnosticCode.VERSION_MISMATCH
    }


def test_wrong_result_selector_and_coverage_artifact_are_diagnosed() -> None:
    target = _target()
    result = _result(target, coverage_ref=_raw("declared-coverage"))
    coverage = CoverageEvidence(
        coverage_id="coverage://wrong-artifact",
        test_result_id=result.test_result_id,
        target=target,
        symbol=_symbol(target),
        artifact_ref=_raw("other-coverage"),
        parser_version="coverage-py-json-v2",
        evidence_locator="coverage-json://wrong-artifact",
        covered_lines=(12,),
    )
    selector = SymbolEvidence(
        evidence_id="test-symbol-evidence://wrong-selector",
        test_result_id=result.test_result_id,
        target=target,
        test_case_id="test-case://wrong",
        symbol=_symbol(target),
        signal=SymbolSignal.EXACT_SELECTOR,
        confidence=1.0,
        parser_version="pytest-selector-v2",
        evidence_locator="pytest-selector://wrong",
        selector="tests/test_other.py::test_other",
    )

    treatment = ValidationBinder().bind(
        result,
        current_target=target,
        coverage_evidence=(coverage,),
        symbol_evidence=(selector,),
    )

    assert not treatment.bindings
    assert {item.code for item in treatment.diagnostics} == {
        ValidationDiagnosticCode.COVERAGE_ARTIFACT_MISMATCH,
        ValidationDiagnosticCode.SELECTOR_MISMATCH,
    }


def test_all_emitted_relations_pass_the_c4_registry() -> None:
    target = _target()
    result = _result(target, coverage_ref=_raw("coverage"))
    treatment = ValidationBinder().bind(
        result,
        current_target=target,
        coverage_evidence=(_coverage(result),),
        symbol_evidence=(_symbol_evidence(result, SymbolSignal.EXACT_SELECTOR),),
    )

    assert {edge.relation_type for edge in treatment.edges} == {
        CodeRelationType.CONTAINS,
        CodeRelationType.COVERS,
        CodeRelationType.TESTS,
        CodeRelationType.VALIDATED_BY,
    }
    for edge in treatment.edges:
        spec = get_edge_spec(edge.relation_type)
        assert spec.accepts_endpoints(edge.source_type, edge.target_type)
        assert edge.relation_type in CodeRelationType
    containment = next(
        edge for edge in treatment.edges if edge.relation_type is CodeRelationType.CONTAINS
    )
    assert containment.role is ValidationEdgeRole.HAS_TEST_RESULT
    assert containment.role.value == "has_test_result"
    assert "HAS_TEST_RESULT" not in {value.value for value in CodeRelationType}


def test_binding_output_is_deterministic_idempotent_and_stably_sorted() -> None:
    target = _target()
    result = _result(target, coverage_ref=_raw("coverage"))
    coverages = (
        _coverage(result, coverage_id="coverage://z", function=False),
        _coverage(result, coverage_id="coverage://a", function=True),
    )
    evidence = (
        _symbol_evidence(result, SymbolSignal.CALL),
        _symbol_evidence(result, SymbolSignal.EXACT_TARGET),
        _symbol_evidence(result, SymbolSignal.SCIP),
    )
    binder = ValidationBinder()

    first = binder.bind(
        result,
        current_target=target,
        coverage_evidence=coverages,
        symbol_evidence=evidence,
    )
    second = binder.bind(
        result,
        current_target=target,
        coverage_evidence=reversed(coverages),
        symbol_evidence=reversed(evidence),
    )

    assert first == second
    assert first.canonical_json_bytes() == second.canonical_json_bytes()
    assert first.canonical_sha256() == second.canonical_sha256()
    assert first.trace.canonical_input_sha256 == second.trace.canonical_input_sha256
    assert first.trace.binder_version == TEST_VALIDATION_BINDER_VERSION
    assert first.contract_version == TEST_VALIDATION_CONTRACT_VERSION


def test_dirty_target_requires_manifest_identity_in_stable_version() -> None:
    with pytest.raises(ValueError, match="dirty identity"):
        TargetVersion(
            project_id="project-a",
            repository_id="repository-a",
            commit_sha=SHA_A,
            stable_version=SHA_A + "+dirty.wrong",
            generation_id="generation-a",
            acl_ref="acl://team-a",
            target_id="worktree://repository-a@wrong",
            target_type=CodeGraphEntityType.WORKTREE,
            dirty_manifest_sha256=MANIFEST_A,
        )


def test_public_contracts_are_lazy_exported() -> None:
    assert code_exports.TestResultV2 is ResultV2
    assert code_exports.TestTargetVersion is TargetVersion
    assert code_exports.CoverageEvidence is CoverageEvidence
    assert code_exports.TestValidationBinder is ValidationBinder
    assert code_exports.TEST_VALIDATION_CONTRACT_VERSION == (TEST_VALIDATION_CONTRACT_VERSION)
