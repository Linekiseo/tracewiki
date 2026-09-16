from __future__ import annotations

from typing import Any

import pytest

from evidence_rag.platform.models import (
    ExperimentAnalysisRequestV2,
    GlobalSearchRequest,
    NotebookComparisonRequestV2,
    NotebookComparisonSelectorV2,
    NotebookQuerySpecV2,
    NotebookQueryTaskV2,
    NotebookSearchFiltersV2,
)
from evidence_rag.rag import multisource_runtime_v2 as runtime_module
from evidence_rag.rag.multisource_foundation_v2 import (
    MultiSourceScopeV2,
    SourceExecutionStatusV2,
    build_default_capability_registry_v2,
    canonical_sha256_v2,
    plan_multisource_query_v2,
)
from evidence_rag.rag.multisource_runtime_v2 import (
    MultiSourceRuntimeRequestV2,
    ProductionSourceRetrieverV2,
    ProductionSourceSnapshotV2,
)


class _Store:
    def entity_acl_refs(self, entity_ids: list[str]) -> dict[str, str]:
        return {entity_id: "acl-project" for entity_id in entity_ids}

    def entity_scopes(self, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
        return {entity_id: {} for entity_id in entity_ids}


class _Platform:
    def __init__(self, *, logical_task: str, honest_response: bool = True) -> None:
        self.logical_task = logical_task
        self.honest_response = honest_response
        self.store = _Store()
        self.requests: list[GlobalSearchRequest] = []

    @staticmethod
    def _in_scope(*_args: Any, **_kwargs: Any) -> bool:
        return True

    def _retrieve_source(
        self,
        *,
        source: str,
        request: GlobalSearchRequest,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        self.requests.append(request)
        notebook_entity_type = {
            "compare": "revision",
            "reproduction": "execution",
        }.get(self.logical_task, self.logical_task)
        row = {
            "entity_id": f"{source}-entity-1",
            "source": source,
            "entity_type": ("ExperimentRun" if source == "experiment" else notebook_entity_type),
            "title": f"{source} typed result",
            "snippet": "Explicitly selected governed evidence.",
            "locator": f"{source}://entity-1",
            "version": f"{source}-version-1",
            "status": "observed",
            "fact_status": "observed",
            "score": 0.9,
            "acl_ref": "acl-project",
        }
        response: dict[str, Any] = {
            "source": source,
            "results": [row],
            "context": {},
            "analysis": [],
            "comparisons": [],
            "selection_trace": {},
            "trace": {
                "status": "complete",
                "engine_used": "v2",
                "duration_ms": 0.1,
                "error_code": None,
            },
        }
        if not self.honest_response:
            return response
        if source == "experiment":
            analysis = request.experiment_analysis
            assert analysis is not None
            result_key = {
                "aggregate": "aggregations",
                "compare": "comparisons",
                "reproduce": "reproduction",
            }[analysis.task]
            response["analysis"] = [{result_key: [{"selected": True}]}]
            response["selection_trace"] = {
                "explicit": True,
                "task": analysis.task,
                "metric": analysis.metric,
                "baseline_run_id": analysis.baseline_run_id,
                "candidate_run_ids": analysis.candidate_run_ids,
                "run_ids": analysis.run_ids,
                "split": analysis.split,
                "aggregation": analysis.aggregation,
                "roles": analysis.roles,
            }
        else:
            spec = request.notebook_query
            assert spec is not None
            response["trace"]["task"] = self.logical_task
            response["selection_trace"] = {
                "requested_filters": spec.filters.model_dump(mode="json"),
                "comparison": (
                    {"requested": spec.comparison.model_dump(mode="json")}
                    if spec.comparison is not None
                    else None
                ),
            }
            if spec.task == "compare":
                response["comparisons"] = [{"selected": True}]
        return response


def _snapshot(source: str) -> ProductionSourceSnapshotV2:
    payload = {
        "source": source,
        "project_id": "project-rag",
        "indexed": True,
        "row_count": 1,
        "index_generation": f"generation-{source}",
        "watermark": "2026-07-31T00:00:00Z",
        "error_code": None,
    }
    return ProductionSourceSnapshotV2(
        **payload,
        content_sha256=canonical_sha256_v2(payload),
    )


def _plan(
    *,
    source: str,
    intent: str,
    experiment_analysis: ExperimentAnalysisRequestV2 | None,
    notebook_query: NotebookQuerySpecV2 | None,
):
    governed_request = MultiSourceRuntimeRequestV2(
        project_id="project-rag",
        request_id="typed-execution-test",
        question="Execute the governed typed source task",
        intent=intent,
        acl_refs=("acl-project",),
        requested_sources=(source,),
        experiment_ids=("experiment-1",) if source == "experiment" else (),
        experiment_analysis=experiment_analysis,
        notebook_query=notebook_query,
    )
    scope = MultiSourceScopeV2(
        project_id="project-rag",
        acl_refs=("acl-project",),
        source_task_parameters=runtime_module._source_task_parameters_v2(governed_request),
    )
    registry = build_default_capability_registry_v2(
        generations={name: f"generation-{name}" for name in runtime_module.MULTISOURCE_DOMAINS},
        watermarks={name: "2026-07-31T00:00:00Z" for name in runtime_module.MULTISOURCE_DOMAINS},
    )
    return scope, plan_multisource_query_v2(
        question="Execute the governed typed source task",
        intent=intent,
        scope=scope,
        registry=registry,
        requested_sources=(source,),
    )


def _retrieve(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: str,
    intent: str,
    logical_task: str,
    experiment_analysis: ExperimentAnalysisRequestV2 | None = None,
    notebook_query: NotebookQuerySpecV2 | None = None,
    honest_response: bool = True,
):
    snapshot = _snapshot(source)
    monkeypatch.setattr(runtime_module, "_source_snapshot", lambda *_args, **_kwargs: snapshot)
    platform = _Platform(logical_task=logical_task, honest_response=honest_response)
    retriever = ProductionSourceRetrieverV2(
        platform=platform,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        snapshot=snapshot,
        project_acl_ref="acl-project",
    )
    scope, plan = _plan(
        source=source,
        intent=intent,
        experiment_analysis=experiment_analysis,
        notebook_query=notebook_query,
    )
    batch = retriever.retrieve(
        plan=plan,
        scope=scope,
        budget=10,
        required_roles=plan.required_roles,
        corrective_round=0,
    )
    return platform, batch


@pytest.mark.parametrize(
    ("intent", "analysis"),
    (
        pytest.param(
            "claim_verification",
            ExperimentAnalysisRequestV2(
                task="aggregate",
                metric="accuracy",
                run_ids=["run-2", "run-1"],
                split="validation",
                aggregation="mean",
            ),
            id="aggregate",
        ),
        pytest.param(
            "reproduction",
            ExperimentAnalysisRequestV2(
                task="reproduce",
                run_ids=["run-1"],
                roles=["command", "seed"],
            ),
            id="reproduce",
        ),
        pytest.param(
            "staleness_check",
            ExperimentAnalysisRequestV2(
                task="compare",
                metric="accuracy",
                baseline_run_id="run-baseline",
                candidate_run_ids=["run-candidate"],
                split="validation",
            ),
            id="compare",
        ),
    ),
)
def test_experiment_contract_compiles_to_explicit_platform_analysis(
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    analysis: ExperimentAnalysisRequestV2,
) -> None:
    platform, batch = _retrieve(
        monkeypatch,
        source="experiment",
        intent=intent,
        logical_task=analysis.task,
        experiment_analysis=analysis,
    )

    assert len(platform.requests) == 1
    sent = platform.requests[0]
    assert sent.experiment_ids == ["experiment-1"]
    assert sent.experiment_analysis == analysis
    assert sent.notebook_query is None
    assert batch.execution.status is SourceExecutionStatusV2.COMPLETE
    assert {candidate.task for candidate in batch.candidates} == {analysis.task}


def _comparison() -> NotebookComparisonRequestV2:
    return NotebookComparisonRequestV2(
        baseline=NotebookComparisonSelectorV2(
            template_id="template-1",
            revision_id="revision-1",
            execution_id="execution-1",
        ),
        candidate=NotebookComparisonSelectorV2(
            template_id="template-1",
            revision_id="revision-2",
            execution_id="execution-2",
        ),
    )


def test_typed_arguments_are_deterministically_bound_into_the_source_contract() -> None:
    analysis = ExperimentAnalysisRequestV2(
        task="aggregate",
        metric="accuracy",
        run_ids=["run-2", "run-1"],
        split="validation",
        aggregation="mean",
    )
    first_scope, first = _plan(
        source="experiment",
        intent="claim_verification",
        experiment_analysis=analysis,
        notebook_query=None,
    )
    second_scope, second = _plan(
        source="experiment",
        intent="claim_verification",
        experiment_analysis=analysis,
        notebook_query=None,
    )

    assert first_scope.source_task_parameters == second_scope.source_task_parameters
    assert first == second
    parameters = first.source_contracts[0].task_parameter_map()
    assert parameters["spec_task"] == ("aggregate",)
    assert parameters["experiment_ids"] == ("experiment-1",)
    assert parameters["run_ids"] == ("run-1", "run-2")
    assert first.source_contracts[0].task_parameters == tuple(
        sorted(first.source_contracts[0].task_parameters)
    )


@pytest.mark.parametrize(
    ("intent", "logical_task", "spec"),
    (
        pytest.param(
            "staleness_check",
            "compare",
            NotebookQuerySpecV2(task="compare", comparison=_comparison()),
            id="compare",
        ),
        pytest.param(
            "reproduction",
            "reproduction",
            NotebookQuerySpecV2(
                task=NotebookQueryTaskV2.REPRODUCTION,
                filters=NotebookSearchFiltersV2(execution_ids=["execution-1"]),
            ),
            id="reproduction",
        ),
        pytest.param(
            "experiment_validation",
            "output",
            NotebookQuerySpecV2(
                task=NotebookQueryTaskV2.OUTPUT,
                filters=NotebookSearchFiltersV2(cell_ids=["cell-1"]),
            ),
            id="output",
        ),
        pytest.param(
            "notebook_debug",
            "error",
            NotebookQuerySpecV2(
                task=NotebookQueryTaskV2.ERROR,
                filters=NotebookSearchFiltersV2(execution_ids=["execution-1"]),
            ),
            id="error",
        ),
    ),
)
def test_notebook_contract_compiles_to_explicit_platform_query(
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    logical_task: str,
    spec: NotebookQuerySpecV2,
) -> None:
    platform, batch = _retrieve(
        monkeypatch,
        source="notebook",
        intent=intent,
        logical_task=logical_task,
        notebook_query=spec,
    )

    assert len(platform.requests) == 1
    sent = platform.requests[0]
    assert sent.experiment_analysis is None
    assert sent.notebook_query is not None
    assert sent.notebook_query.task == logical_task
    assert "governed_notebook_task" not in sent.query
    if logical_task == "compare":
        assert sent.notebook_query == spec
    elif logical_task == "output":
        assert sent.notebook_query.filters.cell_ids == ["cell-1"]
        assert sent.notebook_query.filters.output_types == [
            "binary_omitted",
            "display",
            "error",
            "execute_result",
            "stream",
        ]
    elif logical_task == "error":
        assert sent.notebook_query.filters.execution_ids == ["execution-1"]
        assert sent.notebook_query.filters.statuses == ["failed"]
        assert sent.notebook_query.filters.output_types == ["error"]
    else:
        assert sent.notebook_query.filters.execution_ids == ["execution-1"]
    assert batch.execution.status is SourceExecutionStatusV2.COMPLETE
    assert {candidate.task for candidate in batch.candidates} == {logical_task}


@pytest.mark.parametrize(
    ("source", "intent", "logical_task", "notebook_query"),
    (
        pytest.param("experiment", "claim_verification", "aggregate", None, id="experiment"),
        pytest.param(
            "notebook",
            "experiment_validation",
            "output",
            NotebookQuerySpecV2(task=NotebookQueryTaskV2.OUTPUT),
            id="notebook",
        ),
    ),
)
def test_typed_task_missing_required_arguments_fails_before_source_execution(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    intent: str,
    logical_task: str,
    notebook_query: NotebookQuerySpecV2 | None,
) -> None:
    platform, batch = _retrieve(
        monkeypatch,
        source=source,
        intent=intent,
        logical_task=logical_task,
        notebook_query=notebook_query,
    )

    assert platform.requests == []
    assert batch.candidates == ()
    assert batch.execution.status is SourceExecutionStatusV2.UNAVAILABLE
    assert batch.execution.error_code is not None
    assert batch.execution.error_code.startswith(f"typed_task_unavailable:{source}:")


def test_generic_results_cannot_be_relabelled_as_a_typed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = ExperimentAnalysisRequestV2(
        task="aggregate",
        metric="accuracy",
        run_ids=["run-1"],
        split="validation",
        aggregation="mean",
    )
    platform, batch = _retrieve(
        monkeypatch,
        source="experiment",
        intent="claim_verification",
        logical_task="aggregate",
        experiment_analysis=analysis,
        honest_response=False,
    )

    assert len(platform.requests) == 1
    assert batch.candidates == ()
    assert batch.execution.status is SourceExecutionStatusV2.UNAVAILABLE
    assert batch.execution.error_code == "typed_task_response_mismatch:experiment:aggregate"


def test_generic_notebook_results_cannot_be_relabelled_as_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    platform, batch = _retrieve(
        monkeypatch,
        source="notebook",
        intent="experiment_validation",
        logical_task="output",
        notebook_query=NotebookQuerySpecV2(
            task=NotebookQueryTaskV2.OUTPUT,
            filters=NotebookSearchFiltersV2(cell_ids=["cell-1"]),
        ),
        honest_response=False,
    )

    assert len(platform.requests) == 1
    assert platform.requests[0].notebook_query is not None
    assert platform.requests[0].notebook_query.task is NotebookQueryTaskV2.OUTPUT
    assert batch.candidates == ()
    assert batch.execution.status is SourceExecutionStatusV2.UNAVAILABLE
    assert batch.execution.error_code == "typed_task_response_mismatch:notebook:output"


@pytest.mark.parametrize(
    ("logical_task", "expected_task", "parameters"),
    (
        pytest.param(
            "locate",
            NotebookQueryTaskV2.SEARCH,
            {"spec_task": ("search",)},
            id="search",
        ),
        pytest.param(
            "code",
            NotebookQueryTaskV2.CODE,
            {"spec_task": ("code",), "cell_ids": ("cell-1",)},
            id="code",
        ),
        pytest.param(
            "lineage",
            NotebookQueryTaskV2.LINEAGE,
            {"spec_task": ("lineage",), "cell_ids": ("cell-1",)},
            id="lineage",
        ),
        pytest.param(
            "parameter",
            NotebookQueryTaskV2.PARAMETER,
            {"spec_task": ("parameter",), "parameter_names": ("threshold",)},
            id="parameter",
        ),
    ),
)
def test_notebook_compiler_preserves_the_exact_typed_task(
    logical_task: str,
    expected_task: NotebookQueryTaskV2,
    parameters: dict[str, tuple[str, ...]],
) -> None:
    compiled, error = runtime_module._compile_notebook_task_v2(
        task=logical_task,
        parameters=parameters,
    )

    assert error is None
    assert compiled is not None
    assert compiled.notebook_query is not None
    assert compiled.notebook_query.task is expected_task


@pytest.mark.parametrize(
    ("logical_task", "parameters", "expected_error"),
    (
        pytest.param(
            "output",
            {"cell_ids": ("cell-1",)},
            "explicit_spec_required",
            id="missing-task",
        ),
        pytest.param(
            "output",
            {"spec_task": ("search",), "cell_ids": ("cell-1",)},
            "typed_spec_task_mismatch",
            id="tampered-task",
        ),
        pytest.param(
            "trend",
            {"spec_task": ("trend",)},
            "platform_task_unsupported",
            id="unsupported-task",
        ),
    ),
)
def test_notebook_compiler_rejects_missing_tampered_or_unsupported_task(
    logical_task: str,
    parameters: dict[str, tuple[str, ...]],
    expected_error: str,
) -> None:
    compiled, error = runtime_module._compile_notebook_task_v2(
        task=logical_task,
        parameters=parameters,
    )

    assert compiled is None
    assert error == expected_error


@pytest.mark.parametrize("task", ("trend", "best"))
def test_experiment_tasks_without_platform_model_support_are_unavailable(task: str) -> None:
    compiled, error = runtime_module._compile_experiment_task_v2(
        task=task,
        parameters={},
    )

    assert compiled is None
    assert error == "platform_task_unsupported"
