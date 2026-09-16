from __future__ import annotations

import json
import sqlite3
from dataclasses import FrozenInstanceError, replace
from functools import cache
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.evaluation import cb4
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
PRODUCTION_CB4_RUN_ID = "a22b322a141741ae9a710aa347fbf032"


def _file_state(path: Path) -> tuple[bool, int | None, int | None, str | None]:
    if not path.exists():
        return False, None, None, None
    stat = path.stat()
    return True, stat.st_size, stat.st_mtime_ns, cb4._sha256_file(path)


def _tree_state(path: Path) -> dict[str, tuple[int, int, str]]:
    return {
        item.relative_to(path).as_posix(): (
            item.stat().st_size,
            item.stat().st_mtime_ns,
            cb4._sha256_file(item),
        )
        for item in path.rglob("*")
        if item.is_file()
    }


def _write_gate(root: Path, text: str) -> Path:
    path = root / cb4.C5_GATE_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def prepared_status() -> dict[str, Any]:
    database_before = _file_state(PRODUCTION_DATABASE)
    runs_before = _tree_state(REPOSITORY_RUNS)
    status = cb4.prepare_cb4(root=REPOSITORY_ROOT)
    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before
    return status


def test_preparation_is_non_qualified_and_creates_no_run_or_metrics(
    prepared_status: dict[str, Any],
) -> None:
    package = validate_golden_package(REPOSITORY_ROOT)

    assert prepared_status["status"] == "PREPARED"
    assert prepared_status["qualification_status"] == "NON-QUALIFIED"
    assert prepared_status["execution_status"] == "authorized-ready-for-single-production-run"
    assert prepared_status["treatment_qualified"] is False
    assert prepared_status["run_created"] is False
    assert prepared_status["artifact_created"] is False
    assert prepared_status["metrics_created"] is False
    assert prepared_status["measurement_state"]["metric_values_created"] is False
    assert prepared_status["measurement_state"]["reported_metric_names"] == []
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
    assert prepared_status["execution_policy"]["prepared_only"] is True
    assert prepared_status["execution_policy"]["production_execution_authorized"] is True
    assert prepared_status["development_gate"]["status"] == "authorized-for-development-only"
    assert prepared_status["execution_gate"]["status"] == "authorized"


def test_unique_qualified_anchor_and_all_prior_treatments_are_audit_only(
    prepared_status: dict[str, Any],
) -> None:
    controls = prepared_status["controls"]
    baseline = controls["qualified_baseline"]

    assert baseline["run_id"] == cb4.CB0_QUALIFIED_RUN_ID
    assert baseline["run_id"].endswith("5a92eafdff5d49e6aae8bb55fdc14061")
    assert baseline["treatment_qualified"] is True
    assert baseline["qualification_use"] == "required"

    expected = {
        "cb1_not_qualified_audit": cb4.CB1_AUDIT_RUN_ID,
        "cb2_not_qualified_audit": cb4.CB2_AUDIT_RUN_ID,
        "cb3_not_qualified_audit": cb4.CB3_AUDIT_RUN_ID,
        "cb5_not_qualified_audit": cb4.CB5_AUDIT_RUN_ID,
    }
    for name, run_id in expected.items():
        assert controls[name]["run_id"] == run_id
        assert controls[name]["treatment_qualified"] is False
        assert controls[name]["qualification_use"] == "forbidden"


def test_python_membership_three_arms_and_javascript_typescript_unavailable(
    prepared_status: dict[str, Any],
) -> None:
    package = validate_golden_package(REPOSITORY_ROOT)
    python_case_ids = cb4._python_case_ids(package)
    contract = prepared_status["treatment_contract"]

    assert tuple(contract["order"]) == cb4.TREATMENT_ORDER
    assert set(contract["arms"]) == set(cb4.TREATMENT_ORDER)
    assert contract["same_snapshot_required"] is True
    assert contract["same_ingestion_required"] is True
    assert contract["same_python_case_denominator_required"] is True
    assert contract["denominator"]["case_count"] == 30
    assert tuple(contract["denominator"]["canonical_case_ids"]) == python_case_ids
    assert {
        item["case_membership_hash"] for item in contract["denominator"]["treatments"].values()
    } == {contract["denominator"]["case_membership_hash"]}

    observed = cb4.require_same_python_case_denominator(
        {arm: python_case_ids for arm in cb4.TREATMENT_ORDER},
        expected_case_ids=python_case_ids,
    )
    assert observed == contract["denominator"]

    drifted = {arm: python_case_ids for arm in cb4.TREATMENT_ORDER}
    drifted["scip_semantic"] = python_case_ids[:-1]
    with pytest.raises(cb4.CB4Error, match="30 unique Python cases"):
        cb4.require_same_python_case_denominator(
            drifted,
            expected_case_ids=python_case_ids,
        )

    assert prepared_status["language_scope"]["python"]["eligible_case_count"] == 30
    assert prepared_status["language_scope"]["javascript"]["status"] == "unavailable"
    assert prepared_status["language_scope"]["javascript"]["eligible_case_count"] == 1
    assert prepared_status["language_scope"]["typescript"]["status"] == "unavailable"
    assert prepared_status["language_scope"]["typescript"]["eligible_case_count"] == 2


def test_real_metric_contract_never_fabricates_two_hundred_labels(
    prepared_status: dict[str, Any],
) -> None:
    required = set(prepared_status["reporting_contract"])
    assert {
        "edge_precision",
        "edge_coverage",
        "scip_legal_edge_count",
        "scip_legal_edge_relations",
        "unresolved_reduction",
        "conflict_rate",
        "provenance_merge_rate",
        "required_path_recall",
        "graph_noise_rate@10",
        "graph_harmful_candidate_rate@10",
        "ingest_time_ms",
        "peak_memory_bytes",
        "ingest_latency_ms_p95",
        "retrieval_latency_ms_p95",
        "expected_locator_recall@10",
        "entity_recall@10",
        "mrr@10",
        "ndcg@10",
        "harmful_candidate_rate@10",
    } <= required
    precision = prepared_status["reporting_contract"]["edge_precision"]
    assert precision["status"] == "unavailable"
    assert precision["sample_count"] == 0
    assert precision["fabricated_sample_count_forbidden"] is True
    for name in cb4.ZERO_LABEL_UNAVAILABLE_METRICS:
        contract = prepared_status["reporting_contract"][name]
        measured = prepared_status["measurement_state"]["zero_label_quality_metrics"][name]
        assert contract["status"] == "unavailable"
        assert contract["availability"] == "unavailable"
        assert contract["acceptance_eligible"] is False
        assert measured == {
            "status": "unavailable",
            "value": None,
            "acceptance_eligible": False,
        }
    for name in cb4.STRUCTURAL_OBSERVATIONS:
        contract = prepared_status["reporting_contract"][name]
        observed = prepared_status["measurement_state"]["structural_observations"][name]
        assert contract["quality_metric"] is False
        assert contract["acceptance_eligible"] is False
        assert observed["status"] == "recordable-after-production-execution"
        assert observed["value"] is None
        assert observed["quality_metric"] is False
        assert observed["acceptance_eligible"] is False
    assert prepared_status["acceptance_policy"] == {
        "zero_label_quality_metrics_eligible": False,
        "nonzero_count_only_evidence_forbidden": True,
        "content_addressed_label_records_required": True,
        "label_status_contract": {
            "unavailable": "0 valid real labels",
            "provisional": "1..199 valid real labels",
            "available": "at least 200 valid real labels",
        },
        "required_minimum": 200,
        "maximum_attested_output_count": cb4.PRODUCTION_OUTPUT_EDGE_COUNT,
        "available_reachable": False,
        "provisional_quality_values_recordable": True,
        "provisional_precision_gate_eligible": False,
        "available_required_for_production_quality_qualification": True,
        "structural_observations_are_quality_metrics": False,
        "structural_observations_acceptance_eligible": False,
    }

    attestation = prepared_status["production_output_attestation"]
    no_labels = cb4.edge_precision_sample_plan(
        fixture_edge_count=cb4.PRODUCTION_OUTPUT_EDGE_COUNT,
        human_labeled_edge_count=0,
        production_attestation=attestation,
        root=REPOSITORY_ROOT,
    )
    assert no_labels["status"] == "unavailable"
    assert no_labels["sample_count"] == 0
    assert no_labels["required_minimum"] == 200
    assert no_labels["all_fixture"] is False
    assert no_labels["quality_metrics_recordable"] is False
    assert no_labels["precision_gate_satisfied"] is False
    assert no_labels["label_evidence_ref"]["status"] == "absent"
    assert no_labels["label_evidence_ref"]["dataset_hash"] is None
    with pytest.raises(cb4.CB4Error, match="complete content-addressed records"):
        cb4.edge_precision_sample_plan(
            fixture_edge_count=cb4.PRODUCTION_OUTPUT_EDGE_COUNT,
            human_labeled_edge_count=200,
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )


@cache
def _production_evaluation(
    edge_count: int,
) -> tuple[Any, Any, tuple[Any, ...], Any]:
    production = import_module(cb4.SEMANTIC_EDGE_MODULE)
    contracts = import_module("evidence_rag.rag.sources.code.contracts")
    graph = import_module("evidence_rag.rag.sources.code.graph_v2")
    scope = production.SemanticEdgeScope(
        project_id="project",
        repository_id="repo",
        generation_id="generation",
        stable_version="commit-cb4",
        acl_ref="project:project",
    )

    def endpoint(entity_id: str, entity_type: Any) -> Any:
        return production.SemanticEdgeEndpoint(
            entity_id=entity_id,
            entity_type=entity_type,
            project_id=scope.project_id,
            repository_id=scope.repository_id,
            generation_id=scope.generation_id,
            stable_version=scope.stable_version,
            acl_ref=scope.acl_ref,
            locator=f"code://repo@commit-cb4/src/app.py#entity={entity_id}",
        )

    source = endpoint("file:app", graph.CodeGraphEntityType.FILE_VERSION)
    tree_edges = tuple(
        production.TreeSitterConservativeEdge(
            edge_type=contracts.CodeRelationType.REFERENCES,
            source=source,
            target=endpoint(
                f"symbol:target-{index}",
                graph.CodeGraphEntityType.CODE_SYMBOL,
            ),
            confidence=0.95,
            evidence_locators=(f"code://repo@commit-cb4/src/app.py#L{index + 1}C1-L{index + 1}C2",),
            parser_version="tree-sitter-python-v1",
            resolver_version="conservative-resolver-v1",
        )
        for index in range(edge_count)
    )
    result = production.treat_python_semantic_edges(
        scip_result=None,
        tree_sitter_edges=tree_edges,
        scope=scope,
    )
    labels = tuple(
        production.SemanticEdgePrecisionLabel(
            edge_id=edge.edge_id,
            verdict=production.SemanticEdgePrecisionVerdict.CORRECT,
            reviewer="annotator:reviewer-1",
        )
        for edge in production.sample_semantic_edges(result)
    )
    evaluation = production.evaluate_semantic_edges(result, labels=labels)
    return production, result, labels, evaluation


@cache
def _self_consistent_non_golden_label_records(
    edge_count: int,
) -> tuple[cb4.EdgeLabelRecord, ...]:
    production, result, labels, _ = _production_evaluation(edge_count)
    case_ids = cb4._python_case_ids(validate_golden_package(REPOSITORY_ROOT))
    sampled_edges = production.sample_semantic_edges(result)
    return tuple(
        cb4.EdgeLabelRecord.from_production(
            case_id=case_ids[index % len(case_ids)],
            edge=edge,
            label=label,
            annotation_provenance="sha256:" + "a" * 64,
        )
        for index, (edge, label) in enumerate(zip(sampled_edges, labels, strict=True))
    )


@cache
def _production_attestation() -> dict[str, Any]:
    return cb4.build_production_output_attestation(root=REPOSITORY_ROOT)


@cache
def _attested_label_records() -> tuple[cb4.EdgeLabelRecord, ...]:
    return tuple(
        cb4.EdgeLabelRecord.from_attested_output(
            case_id=case["case_id"],
            output=output,
            gold_truth="correct",
            annotator_id="annotator:reviewer-1",
            annotation_provenance="sha256:" + "a" * 64,
        )
        for case in _production_attestation()["cases"]
        for output in case["outputs"]
    )


def test_fixed_python30_production_output_attestation_is_exact_and_bounded(
    prepared_status: dict[str, Any],
) -> None:
    attestation = prepared_status["production_output_attestation"]

    assert attestation == _production_attestation()
    assert cb4._validate_stored_production_output_attestation(attestation) == attestation
    assert attestation["schema_version"] == cb4.PRODUCTION_OUTPUT_ATTESTATION_SCHEMA_VERSION
    assert attestation["pipeline_profile"] == cb4.PRODUCTION_OUTPUT_PIPELINE_PROFILE
    assert attestation["python_case_ids"] == list(cb4.PYTHON_ELIGIBLE_CASE_IDS)
    assert attestation["case_count"] == 30
    assert attestation["total_output_count"] == cb4.PRODUCTION_OUTPUT_EDGE_COUNT == 42
    assert attestation["total_output_count"] < cb4.EDGE_PRECISION_MINIMUM_SAMPLE
    assert attestation["attestation_hash"] == cb4.EXPECTED_PRODUCTION_OUTPUT_ATTESTATION_HASH
    assert attestation["component_identity"] == prepared_status["production_component"]
    assert sum(case["output_count"] for case in attestation["cases"]) == 42
    assert all(
        output["edge_id"].startswith("code-edge-v1:")
        for case in attestation["cases"]
        for output in case["outputs"]
    )


def test_exact_production_three_arm_outputs_share_fixed_case_inputs() -> None:
    attestation = _production_attestation()
    component = cb4._production_component_type()()
    totals = {arm: 0 for arm in cb4.TREATMENT_ORDER}

    for case in attestation["cases"]:
        fixture = case["input_fixture"]
        scope, tree_edges = cb4._production_case_inputs(fixture)
        scip_result = cb4._build_scip_case_fixture(fixture, scope)
        results = {
            "tree_sitter_conservative": component.treat(
                scip_result=None,
                tree_sitter_edges=tree_edges,
                scope=scope,
                language="python",
            ),
            "scip_semantic": component.treat(
                scip_result=scip_result,
                tree_sitter_edges=(),
                scope=scope,
                language="python",
            ),
            "merged_policy": component.treat(
                scip_result=scip_result,
                tree_sitter_edges=tree_edges,
                scope=scope,
                language="python",
            ),
        }
        assert [edge.edge_id for edge in results["tree_sitter_conservative"].edges] == [
            output["edge_id"] for output in case["outputs"]
        ]
        assert (
            {edge.edge_id for edge in results["tree_sitter_conservative"].edges}
            == {edge.edge_id for edge in results["scip_semantic"].edges}
            == {edge.edge_id for edge in results["merged_policy"].edges}
        )
        assert all(len(edge.provenances) == 2 for edge in results["merged_policy"].edges)
        for arm, result in results.items():
            totals[arm] += len(result.edges)

    assert totals == {arm: cb4.PRODUCTION_OUTPUT_EDGE_COUNT for arm in cb4.TREATMENT_ORDER}


@pytest.mark.parametrize(
    ("edge_count", "expected_status"),
    (
        (0, "unavailable"),
        (1, "provisional"),
        (42, "provisional"),
    ),
)
def test_attested_sample_plan_matches_production_evaluator_status(
    edge_count: int,
    expected_status: str,
) -> None:
    _, _, _, evaluation = _production_evaluation(edge_count)
    attestation = _production_attestation()
    records = _attested_label_records()[:edge_count]
    dataset = cb4.build_edge_label_dataset(
        records,
        fixture_edge_count=attestation["total_output_count"],
        production_attestation=attestation,
        root=REPOSITORY_ROOT,
    )
    plan = cb4.edge_precision_sample_plan(
        fixture_edge_count=attestation["total_output_count"],
        label_dataset=dataset,
        production_attestation=attestation,
        root=REPOSITORY_ROOT,
    )

    assert evaluation.status.value == expected_status
    assert plan["status"] == evaluation.status.value
    assert plan["sample_count"] == evaluation.sample_count
    assert plan["required_minimum"] == evaluation.sample_size_target == 200
    assert plan["all_fixture"] is (edge_count == attestation["total_output_count"])
    assert plan["quality_metrics_recordable"] is (
        evaluation.quality_metrics.edge_precision is not None
    )
    assert plan["label_evidence_ref"]["dataset_hash"] == (
        dataset["dataset_hash"] if dataset is not None else None
    )
    if dataset is not None:
        reordered = cb4.build_edge_label_dataset(
            tuple(reversed(records)),
            fixture_edge_count=attestation["total_output_count"],
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )
        assert reordered == dataset


@pytest.mark.parametrize(
    ("edge_count", "expected_status"),
    (
        (0, "unavailable"),
        (1, "provisional"),
        (199, "provisional"),
        (200, "available"),
    ),
)
def test_canonical_label_status_machine_matches_production_evaluator(
    edge_count: int,
    expected_status: str,
) -> None:
    _, _, _, evaluation = _production_evaluation(edge_count)

    assert evaluation.status.value == expected_status
    assert cb4._canonical_label_qualification_status(edge_count).value == expected_status


def test_real_labels_bind_to_exact_case_outputs_and_fake_edges_fail_closed() -> None:
    attestation = _production_attestation()
    records = _attested_label_records()
    dataset = cb4.build_edge_label_dataset(
        records,
        fixture_edge_count=attestation["total_output_count"],
        production_attestation=attestation,
        root=REPOSITORY_ROOT,
    )

    assert dataset is not None
    assert dataset["record_count"] == attestation["total_output_count"] == 42
    assert dataset["production_output_attestation_hash"] == attestation["attestation_hash"]
    assert dataset["dataset_hash"].startswith("sha256:")

    with pytest.raises(cb4.CB4Error, match="not a production output"):
        cb4.build_edge_label_dataset(
            _self_consistent_non_golden_label_records(1),
            fixture_edge_count=attestation["total_output_count"],
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )
    with pytest.raises(cb4.CB4Error, match="cannot exceed the fixture edge count"):
        cb4.build_edge_label_dataset(
            _self_consistent_non_golden_label_records(200),
            fixture_edge_count=attestation["total_output_count"],
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )


def test_invalid_duplicate_cross_case_unknown_and_uncertain_records_are_rejected() -> None:
    production, result, labels, _ = _production_evaluation(1)
    attestation = _production_attestation()
    valid = _attested_label_records()[0]
    with pytest.raises(FrozenInstanceError):
        valid.case_id = "code-golden-v2-002"  # type: ignore[misc]
    with pytest.raises(cb4.CB4Error, match="duplicate"):
        cb4.build_edge_label_dataset(
            (valid, valid),
            fixture_edge_count=attestation["total_output_count"],
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )

    unknown = valid.to_dict()
    unknown["edge_id"] = "code-edge-v1:" + "0" * 64
    with pytest.raises(cb4.CB4Error, match="does not match"):
        cb4.EdgeLabelRecord.from_mapping(unknown)

    membership_mismatch = valid.to_dict()
    membership_mismatch["case_id"] = "code-golden-v2-999"
    mismatched_record = cb4.EdgeLabelRecord.from_mapping(membership_mismatch)
    with pytest.raises(cb4.CB4Error, match="outside released Golden"):
        cb4.build_edge_label_dataset(
            (mismatched_record,),
            fixture_edge_count=attestation["total_output_count"],
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )

    different_case = next(
        case_id for case_id in cb4.PYTHON_ELIGIBLE_CASE_IDS if case_id != valid.case_id
    )
    cross_case = replace(valid, case_id=different_case)
    with pytest.raises(cb4.CB4Error, match="not a production output"):
        cb4.build_edge_label_dataset(
            (cross_case,),
            fixture_edge_count=attestation["total_output_count"],
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )

    wrong_payload_hash = replace(
        valid,
        production_edge_payload_hash="sha256:" + "f" * 64,
    )
    with pytest.raises(cb4.CB4Error, match="payload differs"):
        cb4.build_edge_label_dataset(
            (wrong_payload_hash,),
            fixture_edge_count=attestation["total_output_count"],
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )

    uncertain = production.SemanticEdgePrecisionLabel(
        edge_id=labels[0].edge_id,
        verdict=production.SemanticEdgePrecisionVerdict.UNCERTAIN,
        reviewer="annotator:reviewer-1",
    )
    with pytest.raises(cb4.CB4Error, match="uncertain"):
        cb4.EdgeLabelRecord.from_production(
            case_id=valid.case_id,
            edge=result.edges[0],
            label=uncertain,
            annotation_provenance="sha256:" + "a" * 64,
        )

    unsafe = valid.to_dict()
    unsafe["annotator_id"] = "/tmp/reviewer"
    with pytest.raises(cb4.CB4Error, match="unsafe or non-portable"):
        cb4.EdgeLabelRecord.from_mapping(unsafe)


def test_all_attested_labels_remain_provisional_and_cannot_qualify() -> None:
    attestation = _production_attestation()
    records = _attested_label_records()
    provisional_dataset = cb4.build_edge_label_dataset(
        records,
        fixture_edge_count=attestation["total_output_count"],
        production_attestation=attestation,
        root=REPOSITORY_ROOT,
    )
    assert provisional_dataset is not None
    provisional = cb4.edge_precision_sample_plan(
        fixture_edge_count=attestation["total_output_count"],
        label_dataset=provisional_dataset,
        production_attestation=attestation,
        root=REPOSITORY_ROOT,
    )
    assert provisional["sample_count"] == 42
    assert provisional["all_fixture"] is True
    assert provisional["status"] == "provisional"
    assert cb4.edge_quality_acceptance_gate(
        label_qualification=provisional,
        treatment_qualified=False,
        label_dataset=provisional_dataset,
        production_attestation=attestation,
        root=REPOSITORY_ROOT,
    ) == {
        "label_status": "provisional",
        "label_evidence_ref": provisional["label_evidence_ref"],
        "precision_gate_satisfied": False,
        "production_quality_qualification_eligible": False,
        "treatment_qualified": False,
    }
    with pytest.raises(cb4.CB4Error, match="cannot qualify production quality"):
        cb4.edge_quality_acceptance_gate(
            label_qualification=provisional,
            treatment_qualified=True,
            label_dataset=provisional_dataset,
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )

    with pytest.raises(cb4.CB4Error, match="does not match recomputed"):
        cb4.edge_quality_acceptance_gate(
            label_qualification=provisional,
            treatment_qualified=False,
            label_dataset=None,
            production_attestation=attestation,
            root=REPOSITORY_ROOT,
        )


class _FakeSemanticEdgeTreatment:
    def treat(self, **kwargs: Any) -> dict[str, Any]:
        return {"inputs": kwargs, "fake": True}


def test_production_identity_is_delayed_and_fake_components_cannot_qualify(
    prepared_status: dict[str, Any],
    tmp_path: Path,
) -> None:
    component = prepared_status["production_component"]
    assert component["module"] == cb4.SEMANTIC_EDGE_MODULE
    assert component["status"] == "ready"
    assert component["class"] == "SemanticEdgeTreatment"
    assert component["version"] == "c5-python-semantic-edges-v1"
    assert component["method"] == "treat"
    assert component["functional_entry_point"] == "treat_python_semantic_edges"
    assert component["evaluation_entry_point"] == "evaluate_semantic_edges"
    assert component["import_mode"] == "delayed"

    production_module = import_module(cb4.SEMANTIC_EDGE_MODULE)
    production = production_module.SemanticEdgeTreatment()
    assert cb4.require_production_component_identity(production) == component

    class _Subclass(production_module.SemanticEdgeTreatment):
        pass

    with pytest.raises(
        cb4.CB4Error,
        match="fake, injected, wrapped, or subclass semantic-edge component",
    ):
        cb4.require_production_component_identity(_FakeSemanticEdgeTreatment())
    with pytest.raises(
        cb4.CB4Error,
        match="fake, injected, wrapped, or subclass semantic-edge component",
    ):
        cb4.require_production_component_identity(_Subclass())

    with pytest.raises(cb4.CB4Error, match="forbids fake, injected, wrapped, or subclass"):
        cb4.execute_cb4(
            root=REPOSITORY_ROOT,
            runs_dir=REPOSITORY_RUNS,
            semantic_edge_component=_FakeSemanticEdgeTreatment(),
            c5_02_gate_approved=True,
        )

    assert not (tmp_path / "runs").exists()


def test_execute_rejects_before_gate_without_creating_a_directory(tmp_path: Path) -> None:
    database_before = _file_state(PRODUCTION_DATABASE)
    runs_before = _tree_state(REPOSITORY_RUNS)

    missing_flag = tmp_path / "missing-flag"
    with pytest.raises(cb4.CB4Error, match="explicit C5-02 completion Gate approval"):
        cb4.execute_cb4(
            root=tmp_path / "repo",
            runs_dir=missing_flag,
        )
    assert not missing_flag.exists()

    with pytest.raises(cb4.CB4Error, match="no test/fake execution mode"):
        cb4.execute_cb4(
            root=REPOSITORY_ROOT,
            runs_dir=tmp_path / "test-mode",
            execution_mode="test",
        )
    assert not (tmp_path / "test-mode").exists()

    unauthorized_root = tmp_path / "unauthorized-repository"
    unauthorized_runs = unauthorized_root / "evals" / "code" / "runs"
    with pytest.raises(cb4.CB4Error, match="completion Gate has not authorized"):
        cb4.execute_cb4(
            root=unauthorized_root,
            runs_dir=unauthorized_runs,
            c5_02_gate_approved=True,
        )
    assert not unauthorized_runs.exists()

    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_current_gate_final_c5_02_decision_is_authorized() -> None:
    status = cb4._c5_02_execution_gate_status(REPOSITORY_ROOT)

    assert status["status"] == "authorized"
    assert status["completion_status"] == "passed"
    assert status["evidence"] == list(cb4.C5_02_GATE_REQUIRED_EVIDENCE)
    assert status["production_execution_authorized"] is True
    assert status["production_component"]["class"] == cb4.REAL_SEMANTIC_EDGE_CLASS
    authorization = cb4._validate_c5_02_execution_gate(REPOSITORY_ROOT)
    assert authorization["status"] == "authorized"
    assert authorization["decision"] == cb4.C5_02_GATE_DECISION


def test_unique_published_production_run_is_verified_and_not_qualified() -> None:
    artifact = REPOSITORY_RUNS / PRODUCTION_CB4_RUN_ID
    database_before = _file_state(PRODUCTION_DATABASE)
    runs_before = _tree_state(REPOSITORY_RUNS)

    assert cb4._existing_cb4_run_ids(REPOSITORY_RUNS) == (PRODUCTION_CB4_RUN_ID,)
    verified = cb4.verify_artifact(artifact, root=REPOSITORY_ROOT)
    manifest = json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))
    treatments = json.loads((artifact / "treatments.json").read_text(encoding="utf-8"))

    assert verified["run_id"] == (
        "evaluation-run://project-code-golden-v2/" + PRODUCTION_CB4_RUN_ID
    )
    assert verified["status"] == "verified"
    assert verified["treatment_qualified"] is False
    assert verified["label_status"] == "unavailable"
    assert verified["label_sample_count"] == 0
    assert verified["precision_gate_satisfied"] is False
    assert manifest["qualification_status"] == "NOT QUALIFIED"
    assert manifest["production_output_attestation"]["case_count"] == 30
    assert manifest["production_output_attestation"]["total_output_count"] == 42
    assert {
        arm: treatments["aggregate"][arm]["legal_edge_count"] for arm in cb4.TREATMENT_ORDER
    } == {arm: 42 for arm in cb4.TREATMENT_ORDER}
    assert (
        treatments["aggregate"]["merged_policy"]["provenance_counts"]["multi_provenance_edge_count"]
        == 42
    )
    assert not Path(f"{artifact / 'evaluation.sqlite3'}-wal").exists()
    assert not Path(f"{artifact / 'evaluation.sqlite3'}-shm").exists()
    assert _file_state(PRODUCTION_DATABASE) == database_before
    assert _tree_state(REPOSITORY_RUNS) == runs_before


def test_final_exact_authorization_accepts_markdown_case_and_whitespace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "authorized"
    _write_gate(
        root,
        """# C5 Gate
## Final decision (2026-07-28 13:30 +0800)

```text
` C5-02    ENGINEERING    pass `
`P0   Findings :   0`
`p1 findings: 0`
`c-b4 production execution AUTHORIZED`
```

## Historical decision (2026-07-28 12:24 +0800)

```text
C5-02 engineering FAIL
P0 findings: 0
P1 findings: 3
C-B4 PRODUCTION EXECUTION NOT AUTHORIZED
```
""",
    )

    parsed = cb4._parse_c5_02_gate_text(
        (root / cb4.C5_GATE_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    assert parsed["status"] == "authorized"
    assert parsed["completion_status"] == "passed"
    assert parsed["production_execution_authorized"] is True
    assert parsed["decision_block_count"] == 2
    assert parsed["selected_block_timestamp"] == "2026-07-28 13:30"

    authorization = cb4._validate_c5_02_execution_gate(root)
    assert authorization["status"] == "authorized"
    assert authorization["completion_status"] == "passed"
    assert authorization["decision"] == cb4.C5_02_GATE_DECISION
    assert authorization["evidence"] == list(cb4.C5_02_GATE_REQUIRED_EVIDENCE)
    assert authorization["production_component"]["class"] == cb4.REAL_SEMANTIC_EDGE_CLASS

    assert not (root / "evals" / "code" / "runs").exists()


def test_not_authorized_is_not_an_authorized_substring(tmp_path: Path) -> None:
    root = tmp_path / "not-authorized"
    _write_gate(
        root,
        """C5-02 engineering PASS
P0 findings: 0
P1 findings: 0
C-B4 PRODUCTION EXECUTION NOT AUTHORIZED
""",
    )

    parsed = cb4._parse_c5_02_gate_text(
        (root / cb4.C5_GATE_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    assert parsed["status"] == "not-authorized"
    assert parsed["execution_values"] == ["not authorized"]
    with pytest.raises(cb4.CB4Error, match="decision is not exact AUTHORIZED"):
        cb4._validate_c5_02_execution_gate(root)


@pytest.mark.parametrize(
    "findings",
    (
        "P1 findings: 0",
        "P0 findings: 0",
        "P0 findings: 0\nP1 findings: 1",
    ),
)
def test_gate_requires_exact_zero_p0_and_p1(tmp_path: Path, findings: str) -> None:
    root = tmp_path / ("missing-findings-" + str(abs(hash(findings))))
    _write_gate(
        root,
        f"""C5-02 engineering PASS
{findings}
C-B4 PRODUCTION EXECUTION AUTHORIZED
""",
    )

    parsed = cb4._parse_c5_02_gate_text(
        (root / cb4.C5_GATE_RELATIVE_PATH).read_text(encoding="utf-8")
    )
    assert parsed["status"] == "not-authorized"
    with pytest.raises(cb4.CB4Error, match="exactly P0=0 and P1=0"):
        cb4._validate_c5_02_execution_gate(root)


def test_gate_requires_exact_production_class_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "fake-production"
    _write_gate(
        root,
        """C5-02 engineering PASS
P0 findings: 0
P1 findings: 0
C-B4 PRODUCTION EXECUTION AUTHORIZED
""",
    )
    fake = dict(cb4._production_component_contract())
    fake["class"] = "FakeSemanticEdgeTreatment"
    monkeypatch.setattr(cb4, "_production_component_contract", lambda: fake)

    status = cb4._c5_02_execution_gate_status(root)
    assert status["status"] == "not-authorized"
    assert "exact production class identity" in status["reason"]
    with pytest.raises(cb4.CB4Error, match="exact production class identity"):
        cb4._validate_c5_02_execution_gate(root)
    assert not (root / "evals" / "code" / "runs").exists()


def _write_canonical_json(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(cb4._pretty_json_bytes(value))


def _portable_artifact(
    directory: Path,
    *,
    prepared_status: dict[str, Any],
    label_count: int,
) -> Path:
    directory.mkdir()
    production_attestation = prepared_status["production_output_attestation"]
    fixture_edge_count = production_attestation["total_output_count"]
    records = _attested_label_records()[:label_count]
    label_dataset = cb4.build_edge_label_dataset(
        records,
        fixture_edge_count=fixture_edge_count,
        production_attestation=production_attestation,
        root=REPOSITORY_ROOT,
    )
    label_qualification = cb4.edge_precision_sample_plan(
        fixture_edge_count=fixture_edge_count,
        label_dataset=label_dataset,
        production_attestation=production_attestation,
        root=REPOSITORY_ROOT,
    )
    treatment_qualified = False
    quality_acceptance = cb4.edge_quality_acceptance_gate(
        label_qualification=label_qualification,
        treatment_qualified=treatment_qualified,
        label_dataset=label_dataset,
        production_attestation=production_attestation,
        root=REPOSITORY_ROOT,
    )
    production_identity = cb4._production_component_contract()
    decision_record = {
        "completion_status": "passed",
        "evidence": list(cb4.C5_02_GATE_REQUIRED_EVIDENCE),
        "production_execution_authorized": True,
    }
    gate_signed = {
        "schema_version": cb4.C5_02_GATE_SCHEMA_VERSION,
        "decision": cb4.C5_02_GATE_DECISION,
        "status": "authorized",
        "completion_status": "passed",
        "evidence": list(cb4.C5_02_GATE_REQUIRED_EVIDENCE),
        "decision_block_hash": cb4._fingerprint(decision_record),
        "production_component": production_identity,
    }
    gate = {
        **gate_signed,
        "snapshot_hash": cb4._fingerprint(gate_signed),
        "report_sha256": "sha256:" + "b" * 64,
        "report": cb4.C5_GATE_RELATIVE_PATH,
    }
    cross_file_label_state = {
        "label_evidence_ref": label_qualification["label_evidence_ref"],
        "label_status": label_qualification["status"],
        "sample_count": label_qualification["sample_count"],
        "quality_acceptance": quality_acceptance,
    }
    _write_canonical_json(
        directory / "attempt-audit.json",
        {
            "gate_authorization": gate,
            **cross_file_label_state,
        },
    )
    _write_canonical_json(directory / "edge-quality.json", cross_file_label_state)
    for name in (
        "treatments.json",
        "coverage.json",
        "retrieval.json",
        "performance.json",
        "security.json",
    ):
        _write_canonical_json(directory / name, {})
    database = sqlite3.connect(directory / "evaluation.sqlite3")
    try:
        database.execute("CREATE TABLE verification_fixture (id INTEGER PRIMARY KEY)")
        database.commit()
    finally:
        database.close()

    manifest = {
        "schema_version": cb4.CB4_SCHEMA_VERSION,
        "status": "completed",
        "execution_mode": "qualified",
        "run_id": f"evaluation-run://project-code-golden-v2/cb4-labels-{label_count}",
        "treatment_qualified": treatment_qualified,
        "gate_authorization": gate,
        "dataset": {
            "id": "code-golden",
            "version": "code-golden-v2",
            "package_hash": PACKAGE_HASH,
            "total_cases": EXPECTED_CASE_COUNT,
            "eligible_cases": EXPECTED_ELIGIBLE_COUNT,
            "ineligible_cases": EXPECTED_INELIGIBLE_COUNT,
            "python_treatment_cases": 30,
        },
        "controls": prepared_status["controls"],
        "production_identity": production_identity,
        "production_output_attestation": production_attestation,
        "label_qualification": label_qualification,
        "quality_acceptance": quality_acceptance,
        "artifact_files": cb4._artifact_file_records(directory),
    }
    if label_dataset is not None:
        manifest["edge_label_dataset"] = label_dataset
    _write_canonical_json(directory / "manifest.json", manifest)
    return directory


@pytest.mark.parametrize(
    ("label_count", "expected_status", "qualified"),
    (
        (0, "unavailable", False),
        (1, "provisional", False),
        (42, "provisional", False),
    ),
)
def test_portable_artifact_recomputes_content_addressed_label_state(
    tmp_path: Path,
    prepared_status: dict[str, Any],
    label_count: int,
    expected_status: str,
    qualified: bool,
) -> None:
    artifact = _portable_artifact(
        tmp_path / f"portable-{label_count}",
        prepared_status=prepared_status,
        label_count=label_count,
    )
    verified = cb4.verify_artifact(artifact, root=tmp_path / "verification-root")

    assert verified["status"] == "verified"
    assert verified["label_status"] == expected_status
    assert verified["label_sample_count"] == label_count
    assert verified["treatment_qualified"] is qualified
    assert verified["precision_gate_satisfied"] is False
    assert (
        verified["production_output_attestation_hash"]
        == cb4.EXPECTED_PRODUCTION_OUTPUT_ATTESTATION_HASH
    )
    if label_count == 0:
        assert verified["label_dataset_hash"] is None
        assert "edge_label_dataset" not in json.loads(
            (artifact / "manifest.json").read_text(encoding="utf-8")
        )
    else:
        assert verified["label_dataset_hash"].startswith("sha256:")


@pytest.mark.parametrize(
    "tamper",
    (
        "record",
        "record_count",
        "dataset_hash",
        "label_set_version",
        "provenance",
        "unknown_edge",
        "membership",
        "duplicate_records",
        "delete_records",
        "delete_dataset",
    ),
)
def test_artifact_rejects_missing_or_tampered_label_content(
    tmp_path: Path,
    prepared_status: dict[str, Any],
    tamper: str,
) -> None:
    artifact = _portable_artifact(
        tmp_path / tamper,
        prepared_status=prepared_status,
        label_count=42,
    )
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset = manifest["edge_label_dataset"]
    if tamper == "record":
        dataset["records"][0]["gold_truth"] = "incorrect"
    elif tamper == "record_count":
        dataset["record_count"] = 41
    elif tamper == "dataset_hash":
        dataset["dataset_hash"] = "sha256:" + "0" * 64
    elif tamper == "label_set_version":
        dataset["label_set_version"] = "invented-v2"
    elif tamper == "provenance":
        dataset["records"][0]["annotation_provenance"] = "sha256:" + "c" * 64
    elif tamper == "unknown_edge":
        dataset["records"][0]["edge_id"] = "code-edge-v1:" + "0" * 64
    elif tamper == "membership":
        dataset["records"][0]["case_id"] = "code-golden-v2-999"
    elif tamper == "duplicate_records":
        dataset["records"] = [dataset["records"][0]] * 42
    elif tamper == "delete_records":
        del dataset["records"]
    elif tamper == "delete_dataset":
        del manifest["edge_label_dataset"]
    _write_canonical_json(manifest_path, manifest)

    with pytest.raises(cb4.CB4Error):
        cb4.verify_artifact(artifact, root=tmp_path / "verification-root")


@pytest.mark.parametrize(
    "tamper",
    (
        "output_set",
        "output_digest",
        "attestation_hash",
        "component_identity",
        "delete_attestation",
    ),
)
def test_artifact_rejects_tampered_production_output_attestation(
    tmp_path: Path,
    prepared_status: dict[str, Any],
    tamper: str,
) -> None:
    artifact = _portable_artifact(
        tmp_path / f"attestation-{tamper}",
        prepared_status=prepared_status,
        label_count=42,
    )
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    attestation = manifest["production_output_attestation"]
    if tamper == "output_set":
        del attestation["cases"][0]["outputs"][0]
    elif tamper == "output_digest":
        attestation["cases"][0]["output_digest"] = "sha256:" + "0" * 64
    elif tamper == "attestation_hash":
        attestation["attestation_hash"] = "sha256:" + "0" * 64
    elif tamper == "component_identity":
        attestation["component_identity"]["class"] = "FakeSemanticEdgeTreatment"
    elif tamper == "delete_attestation":
        del manifest["production_output_attestation"]
    _write_canonical_json(manifest_path, manifest)

    with pytest.raises(cb4.CB4Error):
        cb4.verify_artifact(artifact, root=tmp_path / "verification-root")


def test_artifact_rejects_count_summary_without_records_and_cross_file_mismatch(
    tmp_path: Path,
    prepared_status: dict[str, Any],
) -> None:
    summary_only = _portable_artifact(
        tmp_path / "summary-only",
        prepared_status=prepared_status,
        label_count=42,
    )
    manifest_path = summary_only / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset = manifest["edge_label_dataset"]
    manifest["edge_label_dataset"] = {
        "schema_version": dataset["schema_version"],
        "label_set_version": dataset["label_set_version"],
        "record_count": 200,
        "dataset_hash": dataset["dataset_hash"],
        "validation_mode": "exact-production-label-records",
    }
    _write_canonical_json(manifest_path, manifest)
    with pytest.raises(cb4.CB4Error, match="complete canonical records"):
        cb4.verify_artifact(summary_only, root=tmp_path / "verification-root")

    mismatch = _portable_artifact(
        tmp_path / "cross-file-mismatch",
        prepared_status=prepared_status,
        label_count=42,
    )
    quality_path = mismatch / "edge-quality.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["label_evidence_ref"]["dataset_hash"] = "sha256:" + "d" * 64
    _write_canonical_json(quality_path, quality)
    with pytest.raises(cb4.CB4Error, match="label evidence reference is inconsistent"):
        cb4.verify_artifact(mismatch, root=tmp_path / "verification-root")

    attestation_mismatch = _portable_artifact(
        tmp_path / "attestation-cross-file-mismatch",
        prepared_status=prepared_status,
        label_count=42,
    )
    attempt_path = attestation_mismatch / "attempt-audit.json"
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    attempt["quality_acceptance"]["label_evidence_ref"]["production_output_attestation_hash"] = (
        "sha256:" + "e" * 64
    )
    _write_canonical_json(attempt_path, attempt)
    with pytest.raises(cb4.CB4Error, match="label evidence reference is inconsistent"):
        cb4.verify_artifact(
            attestation_mismatch,
            root=tmp_path / "verification-root",
        )


def test_artifact_and_isolation_contracts_are_canonical_portable_and_secure(
    prepared_status: dict[str, Any],
) -> None:
    policy = prepared_status["artifact_policy"]

    assert policy["expected_entries"] == sorted(cb4.EXPECTED_ARTIFACT_ENTRIES)
    assert policy["label_qualification_schema_version"] == cb4.LABEL_QUALIFICATION_SCHEMA_VERSION
    assert policy["edge_label_record_schema_version"] == cb4.EDGE_LABEL_RECORD_SCHEMA_VERSION
    assert policy["edge_label_dataset_schema_version"] == cb4.EDGE_LABEL_DATASET_SCHEMA_VERSION
    assert policy["edge_label_set_version"] == cb4.EDGE_LABEL_SET_VERSION
    assert (
        policy["production_output_attestation_schema_version"]
        == cb4.PRODUCTION_OUTPUT_ATTESTATION_SCHEMA_VERSION
    )
    assert policy["production_output_attestation_ref"] == cb4._production_output_attestation_ref(
        prepared_status["production_output_attestation"]
    )
    assert policy["maximum_label_records"] == cb4.MAX_EDGE_LABEL_RECORDS
    assert policy["records_stored_once_in_manifest"] is True
    assert policy["dataset_digest_from_canonical_bytes"] is True
    assert policy["dataset_digest_commits_production_attestation"] is True
    assert policy["labels_must_match_case_production_outputs"] is True
    assert policy["complete_production_attestation_stored_in_manifest"] is True
    assert policy["label_qualification_manifest_field_required"] is True
    assert policy["exact_production_label_records_required"] is True
    assert policy["quality_acceptance_manifest_field_required"] is True
    assert policy["cross_files_reference_label_and_production_attestation_hashes"] is True
    assert policy["canonical_json_required"] is True
    assert policy["portable_sqlite_required"] is True
    assert policy["secret_scan_required"] is True
    assert policy["temporary_path_scan_required"] is True
    assert policy["symlinks_forbidden"] is True
    assert policy["wal_shm_forbidden"] is True
    assert policy["pyc_and_pycache_forbidden"] is True

    database_guard = prepared_status["isolation"]["formal_database_guard"]
    runs_guard = prepared_status["isolation"]["repository_runs_guard"]
    assert database_guard["protected"] is True
    assert database_guard["hash_size_mtime_checked"] is True
    assert database_guard["wal_shm_checked"] is True
    assert runs_guard["protected"] is True
    assert runs_guard["hash_size_mtime_checked"] is True
    assert runs_guard["unchanged"] is True


def test_prepare_cli_reports_prepared_without_a_run_or_metric_values(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    prepared_status: dict[str, Any],
) -> None:
    monkeypatch.setattr(cb4, "prepare_cb4", lambda **_: prepared_status)

    result = cb4.main(
        [
            "--repository-root",
            str(REPOSITORY_ROOT),
            "prepare",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert captured.err == ""
    assert payload["status"] == "PREPARED"
    assert payload["qualification_status"] == "NON-QUALIFIED"
    assert payload["run_created"] is False
    assert payload["metrics_created"] is False
    assert payload["measurement_state"]["reported_metric_names"] == []
