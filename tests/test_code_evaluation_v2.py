from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from evidence_rag.api import create_app
from evidence_rag.evaluation.models import (
    CodeEvaluationCaseProfile,
    CodeEvaluationRunRequest,
    EvaluationCandidateJudgment,
    EvaluationCaseCreate,
    EvaluationMetricValue,
)
from evidence_rag.evaluation.service import EvaluationError, EvaluationService
from evidence_rag.evaluation.store import (
    EvaluationRunImmutableError,
    EvaluationStore,
)
from evidence_rag.storage import SQLiteStore

DATASET_ID = "code-golden"
DATASET_VERSION = "v2"
PACKAGE_HASH = "sha256:" + "a" * 64


class _Workspace:
    def _require_project(self, project_id: str) -> dict[str, str]:
        return {"id": project_id}


class _CodeRetriever:
    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        *,
        explicit_graph_toggle: bool = False,
    ) -> None:
        self.responses = responses or {}
        self.requests: list[Any] = []
        self.graph_requests: list[tuple[Any, bool]] = []
        self.error: Exception | None = None
        if explicit_graph_toggle:
            self.search_evaluation = self._search_evaluation
        else:
            self.search_evaluation = None

    def _response(self, query: str, graph_candidate_enabled: bool) -> dict[str, Any]:
        value = self.responses[query]
        if isinstance(value, dict) and set(value) <= {True, False}:
            value = value[graph_candidate_enabled]
        return value

    def search(self, request: Any) -> dict[str, Any]:
        self.requests.append(request)
        if self.error:
            raise self.error
        return self._response(request.query, False)

    def _search_evaluation(self, request: Any, *, graph_candidate_enabled: bool) -> dict[str, Any]:
        self.requests.append(request)
        self.graph_requests.append((request, graph_candidate_enabled))
        if self.error:
            raise self.error
        return self._response(request.query, graph_candidate_enabled)


class _Platform:
    def __init__(self, code: _CodeRetriever) -> None:
        self.code = code


def _service(
    settings,
    responses: dict[str, Any] | None = None,
    *,
    explicit_graph_toggle: bool = False,
):
    database = SQLiteStore(settings.database_path)
    database.initialize()
    store = EvaluationStore(database)
    store.initialize()
    retriever = _CodeRetriever(
        responses,
        explicit_graph_toggle=explicit_graph_toggle,
    )
    service = EvaluationService(store, _Platform(retriever), _Workspace())  # type: ignore[arg-type]
    return service, store, retriever


def _profile(**overrides: Any) -> CodeEvaluationCaseProfile:
    values = {
        "task": "exact",
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "dataset_package_hash": PACKAGE_HASH,
    }
    values.update(overrides)
    return CodeEvaluationCaseProfile(**values)


def _run_request(case_ids: list[str], **overrides: Any) -> CodeEvaluationRunRequest:
    values = {
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
        "package_hash": PACKAGE_HASH,
        "case_ids": case_ids,
    }
    values.update(overrides)
    return CodeEvaluationRunRequest(**values)


def _response(*results: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    value = {
        "results": list(results),
        "relations": [],
        "index_generation": ["generation://code/frozen"],
        "trace": {"duration_ms": 2.0},
    }
    value.update(overrides)
    return value


def _metric(
    run: dict[str, Any],
    name: str,
    *,
    case_id: str | None,
    slice_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_slice = slice_value or {}
    return next(
        item
        for item in run["metric_values"]
        if item["metric_name"] == name
        and item["case_id"] == case_id
        and item["slice"] == expected_slice
    )


def test_case_004_helpful_grade_does_not_expand_required_denominator(settings) -> None:
    required = "code://repo@current/src/charge.py#symbol=charge_impl"
    helpful = "code://repo@current/src/charge.py#symbol=charge_overload"
    service, _, _ = _service(
        settings,
        {
            "case 004": _response(
                {
                    "entity_id": helpful,
                    "retrieval_unit_id": "unit://helpful",
                    "channels": ["lexical"],
                },
                {
                    "entity_id": required,
                    "retrieval_unit_id": "unit://required",
                    "channels": ["lexical"],
                },
                trace={"duration_ms": 2.0, "retrieval_unit_capable": True},
            )
        },
    )
    case = service.create_case(
        EvaluationCaseCreate(
            name="Case 004 overload",
            question="case 004",
            expected_entity_ids=[required],
            code_profile=_profile(expected_unit_ids=["unit://required"]),
            candidate_judgments=[
                EvaluationCandidateJudgment(
                    entity_id=required,
                    retrieval_unit_id="unit://required",
                    relevance_grade=2,
                ),
                EvaluationCandidateJudgment(
                    entity_id=helpful,
                    retrieval_unit_id="unit://helpful",
                    relevance_grade=1,
                    necessity_role="helpful",
                ),
            ],
        )
    )

    run = service.run_code(_run_request([case["id"]]))

    entity_at_1 = _metric(run, "entity_recall@1", case_id=case["id"])
    entity_at_20 = _metric(run, "entity_recall@20", case_id=case["id"])
    unit_at_20 = _metric(run, "unit_recall@20", case_id=case["id"])
    assert entity_at_1["value"] == 0.0
    assert entity_at_20["value"] == 1.0
    assert entity_at_20["denominator"] == 1.0
    assert unit_at_20["value"] == 1.0
    assert unit_at_20["denominator"] == 1.0
    assert _metric(run, "mrr@10", case_id=case["id"])["value"] == 1.0
    assert 0.0 < _metric(run, "ndcg@10", case_id=case["id"])["value"] < 1.0
    assert run["results"][0]["detail"]["required_entity_denominator"] == 1
    aggregate = _metric(
        run,
        "entity_recall@20",
        case_id=None,
        slice_value={"scope": "overall"},
    )
    assert (aggregate["numerator"], aggregate["denominator"]) == (1.0, 1.0)
    assert (
        aggregate["total_cases"],
        aggregate["eligible_cases"],
        aggregate["available_cases"],
        aggregate["unavailable_cases"],
    ) == (1, 1, 1, 0)


def test_explicit_acceptable_alternative_group_is_one_required_slot(settings) -> None:
    first = "code://repo@current/src/a.py#symbol=A"
    second = "code://repo@current/src/a.py#symbol=A_alias"
    service, _, _ = _service(
        settings,
        {
            "alternative": _response(
                {
                    "entity_id": second,
                    "retrieval_unit_id": "unit://second",
                    "channels": ["exact"],
                },
                trace={"duration_ms": 1.0, "retrieval_unit_capable": True},
            )
        },
    )
    case = service.create_case(
        EvaluationCaseCreate(
            name="Alternative",
            question="alternative",
            expected_entity_ids=[first, second],
            code_profile=_profile(
                expected_unit_ids=["unit://first", "unit://second"],
                acceptable_alternative_groups=[
                    {
                        "group_id": "equivalent-overloads",
                        "entity_ids": [first, second],
                        "retrieval_unit_ids": ["unit://first", "unit://second"],
                    }
                ],
            ),
        )
    )

    run = service.run_code(_run_request([case["id"]]))

    for name in ("entity_recall@20", "unit_recall@20"):
        metric = _metric(run, name, case_id=case["id"])
        assert metric["value"] == 1.0
        assert metric["numerator"] == 1.0
        assert metric["denominator"] == 1.0


def test_rich_incoming_path_uses_fact_edge_direction_and_rejects_malformed_paths(
    settings,
) -> None:
    caller = "code://repo@current/src/caller.py#symbol=submit"
    target = "code://repo@current/src/pricing.py#symbol=total"
    correct = _response(
        {"entity_id": target, "channels": ["exact"]},
        {"entity_id": caller, "channels": ["exact"]},
        relations=[{"source": caller, "target": target, "edge_type": "CALLS"}],
    )
    reversed_edge = {
        **correct,
        "relations": [{"source": target, "target": caller, "edge_type": "CALLS"}],
    }
    service, _, _ = _service(
        settings,
        {"incoming correct": correct, "incoming reversed": reversed_edge},
    )
    path = {
        "nodes": [target, caller],
        "edges": [
            {
                "source": caller,
                "target": target,
                "edge_type": "CALLS",
                "direction": "incoming",
            }
        ],
    }
    cases = [
        service.create_case(
            EvaluationCaseCreate(
                name=question,
                question=question,
                expected_entity_ids=[target, caller],
                expected_paths=[[target, caller]],
                code_profile=_profile(
                    required_paths=[path],
                    required_edge_types=["CALLS"],
                ),
            )
        )
        for question in ("incoming correct", "incoming reversed")
    ]

    run = service.run_code(_run_request([case["id"] for case in cases]))

    by_question = {item["question"]: item for item in run["results"]}
    assert (
        _metric(
            run,
            "required_path_recall",
            case_id=by_question["incoming correct"]["case_id"],
        )["value"]
        == 1.0
    )
    assert (
        _metric(
            run,
            "required_path_recall",
            case_id=by_question["incoming reversed"]["case_id"],
        )["value"]
        == 0.0
    )

    with pytest.raises(ValidationError, match="endpoints"):
        _profile(
            required_paths=[
                {
                    "nodes": [target, caller],
                    "edges": [
                        {
                            "source": target,
                            "target": caller,
                            "edge_type": "CALLS",
                            "direction": "incoming",
                        }
                    ],
                }
            ],
            required_edge_types=["CALLS"],
        )
    with pytest.raises(ValidationError, match="at least 1 item|one edge"):
        _profile(
            required_paths=[{"nodes": [target, caller], "edges": []}],
            required_edge_types=[],
        )
    with pytest.raises(ValidationError, match="exactly match"):
        _profile(
            required_paths=[path],
            required_edge_types=["IMPORTS"],
        )
    with pytest.raises(EvaluationError, match="rich typed"):
        service.create_case(
            EvaluationCaseCreate(
                name="Legacy-only path",
                question="legacy",
                expected_paths=[[target, caller]],
                code_profile=_profile(),
            )
        )


def test_graph_only_recovery_requires_same_snapshot_paired_runs(settings) -> None:
    first = "code://repo@current/src/a.py#symbol=A"
    recovered = "code://repo@current/src/b.py#symbol=B"
    service, _, retriever = _service(
        settings,
        {
            "graph pair": {
                False: _response(
                    {"entity_id": first, "channels": ["lexical"]},
                ),
                True: _response(
                    {"entity_id": first, "channels": ["lexical"]},
                    {"entity_id": recovered, "channels": ["graph"]},
                ),
            }
        },
        explicit_graph_toggle=True,
    )
    case = service.create_case(
        EvaluationCaseCreate(
            name="Graph pair",
            question="graph pair",
            expected_entity_ids=[first, recovered],
            code_profile=_profile(),
        )
    )

    graph_off = service.run_code(_run_request([case["id"]], graph_candidate_enabled=False))
    off_metric = _metric(graph_off, "graph_only_recovery@10", case_id=case["id"])
    assert off_metric["status"] == "unavailable"
    graph_on = service.run_code(
        _run_request(
            [case["id"]],
            graph_candidate_enabled=True,
            paired_graph_off_run_id=graph_off["id"],
        )
    )
    on_metric = _metric(graph_on, "graph_only_recovery@10", case_id=case["id"])
    assert on_metric["value"] == 0.5
    assert on_metric["numerator"] == 1.0
    assert on_metric["denominator"] == 2.0
    assert (
        graph_off["snapshot"]["comparison_fingerprint"]
        == (graph_on["snapshot"]["comparison_fingerprint"])
    )
    assert [enabled for _, enabled in retriever.graph_requests] == [False, True]


def test_graph_on_fails_without_real_toggle_and_cannot_self_attribute(settings) -> None:
    service, _, _ = _service(settings, {"no toggle": _response()})
    case = service.create_case(
        EvaluationCaseCreate(
            name="No toggle",
            question="no toggle",
            code_profile=_profile(expected_answer_mode="refuse"),
        )
    )

    with pytest.raises(EvaluationError, match="explicit graph-candidate"):
        service.run_code(_run_request([case["id"]], graph_candidate_enabled=True))


def test_dataset_request_is_strict_and_membership_is_exact(settings) -> None:
    service, store, _ = _service(
        settings,
        {"one": _response(), "two": _response()},
    )
    cases = [
        service.create_case(
            EvaluationCaseCreate(
                name=question,
                question=question,
                code_profile=_profile(expected_answer_mode="refuse"),
            )
        )
        for question in ("one", "two")
    ]

    with pytest.raises(ValidationError, match="extra_forbidden"):
        CodeEvaluationRunRequest.model_validate(
            {
                **_run_request([case["id"] for case in cases]).model_dump(),
                "implementation_commit": "caller-supplied",
            }
        )
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        _run_request([case["id"] for case in cases], package_hash="not-a-hash")
    with pytest.raises(EvaluationError, match="missing dataset cases"):
        service.run_code(_run_request([cases[0]["id"]]))
    with pytest.raises(EvaluationError, match="mismatched"):
        service.run_code(
            _run_request(
                [case["id"] for case in cases],
                package_hash="sha256:" + "b" * 64,
            )
        )
    assert store.list_runs("project-rag") == []


def test_completed_run_results_metrics_context_and_summary_are_immutable(settings) -> None:
    service, store, _ = _service(settings, {"empty": _response()})
    case = service.create_case(
        EvaluationCaseCreate(
            name="Empty",
            question="empty",
            code_profile=_profile(expected_answer_mode="refuse"),
        )
    )
    run = service.run_code(_run_request([case["id"]]))

    with pytest.raises(EvaluationRunImmutableError):
        store.update_run_context(run["id"], snapshot={"tampered": True})
    with pytest.raises(EvaluationRunImmutableError):
        store.add_result({"evaluation_run_id": run["id"]})
    with pytest.raises(EvaluationRunImmutableError):
        store.add_metric_values(
            [
                EvaluationMetricValue(
                    evaluation_run_id=run["id"],
                    source_domain="code",
                    metric_name="tamper",
                    value=1.0,
                ).model_dump()
            ]
        )
    with pytest.raises(EvaluationRunImmutableError):
        store.complete_run(run["id"], {"tampered": True})
    with pytest.raises(EvaluationRunImmutableError):
        store.fail_run(run["id"], RuntimeError("tampered"))

    result_id = run["results"][0]["id"]
    with store.database.connection() as db:
        metric_id = db.execute(
            "SELECT id FROM evaluation_metric_values WHERE evaluation_run_id=?",
            (run["id"],),
        ).fetchone()["id"]
    with (
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
        store.database.transaction() as db,
    ):
        db.execute(
            "UPDATE evaluation_runs SET summary_json='{}' WHERE id=?",
            (run["id"],),
        )
    with (
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
        store.database.transaction() as db,
    ):
        db.execute(
            "UPDATE evaluation_results SET detail_json='{}' WHERE id=?",
            (result_id,),
        )
    with (
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
        store.database.transaction() as db,
    ):
        db.execute(
            "UPDATE evaluation_metric_values SET value=0 WHERE id=?",
            (metric_id,),
        )


def test_answer_modes_acl_scope_and_hard_negatives_are_independent(settings) -> None:
    unauthorized = "code://repo@current/src/private.py#symbol=secret"
    harmful = "code://repo@current/src/old.py#symbol=old"
    responses = {
        "refuse": _response(),
        "clarify": _response(),
        "missing evidence": _response(),
        "acl leak": _response(
            {
                "entity_id": unauthorized,
                "acl_ref": "team:fraud",
                "channels": ["exact"],
            }
        ),
        "hard negative": _response(
            {
                "entity_id": harmful,
                "acl_ref": "team:checkout",
                "channels": ["exact"],
            }
        ),
    }
    service, _, retriever = _service(settings, responses)
    cases = [
        service.create_case(
            EvaluationCaseCreate(
                name=question,
                question=question,
                expected_entity_ids=(
                    ["code://repo@current/src/missing.py#symbol=missing"]
                    if mode == "missing_evidence"
                    else []
                ),
                forbidden_entity_ids=[harmful] if question == "hard negative" else [],
                code_profile=_profile(
                    query_profile={"acl_refs": ["team:checkout"]},
                    expected_answer_mode=mode,
                ),
            )
        )
        for question, mode in (
            ("refuse", "refuse"),
            ("clarify", "clarify"),
            ("missing evidence", "missing_evidence"),
            ("acl leak", "refuse"),
            ("hard negative", "refuse"),
        )
    ]

    run = service.run_code(_run_request([case["id"] for case in cases]))

    by_question = {item["question"]: item for item in run["results"]}
    for question in ("refuse", "clarify", "missing evidence"):
        assert by_question[question]["passed"] == 1
    assert by_question["acl leak"]["passed"] == 0
    assert by_question["hard negative"]["passed"] == 0
    acl_case_id = by_question["acl leak"]["case_id"]
    harmful_case_id = by_question["hard negative"]["case_id"]
    assert _metric(run, "unauthorized_leakage_count@10", case_id=acl_case_id)["value"] == 1.0
    assert _metric(run, "hard_negative_error@10", case_id=acl_case_id)["status"] == "unavailable"
    assert _metric(run, "unauthorized_leakage_count@10", case_id=harmful_case_id)["value"] == 0.0
    assert _metric(run, "hard_negative_error@10", case_id=harmful_case_id)["value"] == 1.0
    assert all(request.scope.enforce_acl for request in retriever.requests)
    assert all(
        request.scope.allowed_acl_refs == ["team:checkout"] for request in retriever.requests
    )


def test_version_metrics_keep_missing_candidates_and_require_exact_validation_ref(
    settings,
) -> None:
    exact = "code://repo@target/src/a.py#symbol=A"
    relabeled = "code://repo@old/src/a.py#symbol=A"
    dirty_ref = "target+dirty.abc"
    dirty = f"code://repo@{dirty_ref}/src/b.py#symbol=B"
    validation = "test-result://repo/test_checkout"
    responses = {
        "relabeled": _response({"entity_id": relabeled, "commit": "target", "channels": ["exact"]}),
        "missing version": _response({"entity_id": exact, "channels": ["exact"]}),
        "dirty": _response({"entity_id": dirty, "commit": dirty_ref, "channels": ["exact"]}),
        "false validation": _response(
            {
                "entity_id": validation,
                "entity_type": "TestResult",
                "commit": "target",
                "validation_target_ref": "old",
                "channels": ["test"],
            }
        ),
        "v1 validation": _response(
            {
                "entity_id": validation,
                "entity_type": "TestResult",
                "commit": "target",
                "channels": ["test"],
            }
        ),
    }
    service, _, _ = _service(settings, responses)
    specifications = (
        ("relabeled", [relabeled], "target", [], []),
        ("missing version", [exact], "target", [], []),
        ("dirty", [dirty], dirty_ref, [], []),
        (
            "false validation",
            [validation],
            "target",
            ["TestResult"],
            ["validation"],
        ),
        (
            "v1 validation",
            [validation],
            "target",
            ["TestResult"],
            ["validation"],
        ),
    )
    cases = [
        service.create_case(
            EvaluationCaseCreate(
                name=question,
                question=question,
                expected_entity_ids=expected_ids,
                required_version=expected_ref,
                code_profile=_profile(
                    expected_ref=expected_ref,
                    expected_entity_types=types,
                    expected_context_roles=roles,
                ),
            )
        )
        for question, expected_ids, expected_ref, types, roles in specifications
    ]

    run = service.run_code(_run_request([case["id"] for case in cases]))

    by_question = {item["question"]: item for item in run["results"]}
    relabeled_id = by_question["relabeled"]["case_id"]
    missing_id = by_question["missing version"]["case_id"]
    dirty_id = by_question["dirty"]["case_id"]
    false_validation_id = by_question["false validation"]["case_id"]
    v1_validation_id = by_question["v1 validation"]["case_id"]
    assert _metric(run, "exact_commit_accuracy", case_id=relabeled_id)["value"] == 0.0
    assert _metric(run, "wrong_version_rate", case_id=relabeled_id)["value"] == 1.0
    missing = _metric(run, "missing_version_rate", case_id=missing_id)
    assert (missing["numerator"], missing["denominator"], missing["value"]) == (
        1.0,
        1.0,
        1.0,
    )
    assert _metric(run, "wrong_version_rate", case_id=missing_id)["status"] == ("unavailable")
    assert _metric(run, "exact_commit_accuracy", case_id=missing_id)["value"] == 0.0
    assert by_question["missing version"]["version_accuracy"] == 0.0
    assert _metric(run, "dirty_snapshot_accuracy", case_id=dirty_id)["value"] == 1.0
    assert _metric(run, "false_validation_rate", case_id=false_validation_id)["value"] == 1.0
    assert (
        _metric(run, "false_validation_rate", case_id=v1_validation_id)["status"] == "unavailable"
    )

    aggregate = _metric(
        run,
        "wrong_version_rate",
        case_id=None,
        slice_value={"scope": "overall"},
    )
    assert aggregate["total_cases"] == 5
    assert aggregate["eligible_cases"] == 5
    assert aggregate["available_cases"] == 4
    assert aggregate["unavailable_cases"] == 1


def test_locator_and_entity_unit_duplicate_metrics_are_distinct(settings) -> None:
    symbol = "code://repo@current/src/a.py#symbol=A"
    service, _, _ = _service(
        settings,
        {
            "locators": _response(
                {
                    "entity_id": symbol,
                    "retrieval_unit_id": "unit://one",
                    "evidence_locator": "src/a.py:10-20",
                },
                {
                    "entity_id": symbol,
                    "retrieval_unit_id": "unit://two",
                    "evidence_locator": "",
                },
                trace={"duration_ms": 1.0, "retrieval_unit_capable": True},
            )
        },
    )
    case = service.create_case(
        EvaluationCaseCreate(
            name="Locators",
            question="locators",
            expected_entity_ids=[symbol],
            code_profile=_profile(expected_locators=["src/a.py:10-20"]),
        )
    )

    run = service.run_code(_run_request([case["id"]]))

    assert _metric(run, "expected_locator_recall@10", case_id=case["id"])["value"] == 1.0
    assert _metric(run, "returned_locator_validity@10", case_id=case["id"])["value"] == 0.5
    assert _metric(run, "entity_duplicate_rate@10", case_id=case["id"])["value"] == 0.5
    assert _metric(run, "unit_duplicate_rate@10", case_id=case["id"])["value"] == 0.0


def test_same_frozen_input_has_stable_snapshot_fingerprint(settings) -> None:
    service, _, _ = _service(settings, {"stable": _response()})
    case = service.create_case(
        EvaluationCaseCreate(
            name="Stable",
            question="stable",
            code_profile=_profile(expected_answer_mode="refuse"),
        )
    )

    first = service.run_code(_run_request([case["id"]]))
    second = service.run_code(_run_request([case["id"]]))

    assert first["id"] != second["id"]
    assert first["snapshot"]["snapshot_id"] == second["snapshot"]["snapshot_id"]
    assert first["snapshot"]["implementation_state"]["head_commit"]
    assert "workspace_dirty" in first["snapshot"]["implementation_state"]
    assert (
        first["snapshot"]["implementation_state"]["repository_root"]
        == "<repository-root>"
    )
    assert first["snapshot"]["dataset"] == {
        "id": DATASET_ID,
        "version": DATASET_VERSION,
        "package_hash": PACKAGE_HASH,
    }


def test_runner_exception_persists_failed_append_only_run(settings) -> None:
    service, store, retriever = _service(settings)
    case = service.create_case(
        EvaluationCaseCreate(
            name="Failure",
            question="explode",
            code_profile=_profile(expected_answer_mode="refuse"),
        )
    )
    retriever.error = RuntimeError("retriever unavailable")

    with pytest.raises(EvaluationError, match="retriever unavailable"):
        service.run_code(_run_request([case["id"]]))

    run = store.list_runs("project-rag")[0]
    assert run["status"] == "failed"
    assert run["completed_at"]
    assert run["failure"] == {
        "type": "RuntimeError",
        "message": "retriever unavailable",
    }
    with pytest.raises(EvaluationRunImmutableError):
        store.update_run_context(run["id"], config={"tampered": True})
    with pytest.raises(EvaluationRunImmutableError):
        store.add_result({"evaluation_run_id": run["id"]})
    with pytest.raises(EvaluationRunImmutableError):
        store.add_metric_values(
            [
                EvaluationMetricValue(
                    evaluation_run_id=run["id"],
                    source_domain="code",
                    metric_name="failed-tamper",
                    value=1.0,
                ).model_dump()
            ]
        )
    with pytest.raises(EvaluationRunImmutableError):
        store.complete_run(run["id"], {"tampered": True})
    with pytest.raises(EvaluationRunImmutableError):
        store.fail_run(run["id"], RuntimeError("tampered"))


def test_v2_schema_migrates_existing_v1_database_additively(settings) -> None:
    database = SQLiteStore(settings.database_path)
    database.initialize()
    with database.transaction() as db:
        db.execute(
            """CREATE TABLE evaluation_runs (
                id TEXT PRIMARY KEY,
                display_key TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                case_count INTEGER NOT NULL,
                summary_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL,
                completed_at TEXT
            )"""
        )

    store = EvaluationStore(database)
    store.initialize()

    with database.connection() as db:
        tables = {
            row["name"]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        run_columns = {row["name"] for row in db.execute("PRAGMA table_info(evaluation_runs)")}
        metric_columns = {
            row["name"] for row in db.execute("PRAGMA table_info(evaluation_metric_values)")
        }
        triggers = {
            row["name"]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()
        }
    assert {
        "evaluation_case_profiles",
        "evaluation_candidate_judgments",
        "evaluation_metric_values",
    } <= tables
    assert {
        "dataset_id",
        "dataset_version",
        "dataset_package_hash",
        "graph_candidate_enabled",
        "snapshot_json",
        "config_json",
        "failure_json",
    } <= run_columns
    assert {
        "numerator",
        "denominator",
        "eligible",
        "total_cases",
        "eligible_cases",
        "available_cases",
        "unavailable_cases",
    } <= metric_columns
    assert {
        "trg_evaluation_runs_terminal_update",
        "trg_evaluation_results_terminal_update",
        "trg_evaluation_metrics_terminal_update",
    } <= triggers


def test_code_v2_api_requires_dataset_and_keeps_v1_route(settings) -> None:
    app = create_app(settings)
    app.state.runtime.platform.code.search = lambda request: _response()
    with TestClient(app) as client:
        case = client.post(
            "/v1/evaluation/cases",
            json={
                "name": "Expected no result",
                "question": "missing symbol",
                "code_profile": {
                    "task": "unanswerable",
                    "dataset_id": DATASET_ID,
                    "dataset_version": DATASET_VERSION,
                    "dataset_package_hash": PACKAGE_HASH,
                    "expected_answer_mode": "refuse",
                },
            },
        )
        assert case.status_code == 201
        missing_dataset = client.post(
            "/v1/evaluation/runs/code",
            json={"case_ids": [case.json()["id"]]},
        )
        assert missing_dataset.status_code == 422
        unknown_field = client.post(
            "/v1/evaluation/runs/code",
            json={
                **_run_request([case.json()["id"]]).model_dump(),
                "implementation_commit": "fake",
            },
        )
        assert unknown_field.status_code == 422
        run = client.post(
            "/v1/evaluation/runs/code",
            json=_run_request([case.json()["id"]]).model_dump(),
        )
        assert run.status_code == 200
        assert run.json()["source_domain"] == "code"
        assert run.json()["results"][0]["passed"] == 1

        no_cases = client.post(
            "/v1/evaluation/runs",
            json={"case_ids": ["evaluation-case://project-rag/missing"]},
        )
        assert no_cases.status_code == 409


def test_code_v2_api_forwards_access_context_to_runner(settings) -> None:
    app = create_app(settings)
    captured: dict[str, Any] = {}

    def capture(request: Any, **kwargs: Any) -> dict[str, Any]:
        captured["request"] = request
        captured.update(kwargs)
        return {"status": "completed"}

    app.state.runtime.evaluation.run_code = capture
    with TestClient(app) as client:
        response = client.post(
            "/v1/evaluation/runs/code",
            headers={"x-rag-acl-refs": "team:checkout,team:review"},
            json=_run_request(["evaluation-case://project-rag/route-only"]).model_dump(),
        )

    assert response.status_code == 200
    assert captured["allowed_acl_refs"] == ["team:checkout", "team:review"]
    assert captured["enforce_acl"] is False
