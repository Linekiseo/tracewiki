from __future__ import annotations

import json
from pathlib import Path

from evidence_rag.rag.sources.notebook import (
    NotebookAdapterV2,
    NotebookArtifactType,
    NotebookCellExecutionInput,
    NotebookCellExecutionState,
    NotebookMetricStatus,
    NotebookParameterType,
    NotebookV2Store,
    analyze_notebook_code_v2,
    build_notebook_fixture_v1,
    evaluate_reviewed_notebook_retrieval,
    load_notebook_golden_v1,
    parse_notebook_output_v2,
    parse_notebook_parameters_v2,
    perfect_reviewed_rows_v1,
    project_notebook_execution_state_v2,
)


def test_parameter_parser_is_typed_and_never_executes_expressions() -> None:
    result = parse_notebook_parameters_v2(
        "\n".join(
            (
                "none_value = None",
                "enabled = True",
                "seed = 7",
                "ratio = 0.25",
                "name = 'training'",
                "layers = [32, 16]",
                "config = {'split': 'validation'}",
                "unsafe = load_secret()",
                "seed = 99",
                "a, b = (1, 2)",
            )
        )
    )
    values = {item.name: item for item in result.parameters}
    assert {name: item.value_type for name, item in values.items()} == {
        "none_value": NotebookParameterType.NULL,
        "enabled": NotebookParameterType.BOOLEAN,
        "seed": NotebookParameterType.INTEGER,
        "ratio": NotebookParameterType.FLOAT,
        "name": NotebookParameterType.STRING,
        "layers": NotebookParameterType.LIST,
        "config": NotebookParameterType.OBJECT,
        "unsafe": NotebookParameterType.UNRESOLVED,
    }
    assert values["unsafe"].canonical_value == "<unresolved>"
    assert values["enabled"].canonical_value == "true"
    assert values["seed"].canonical_value == "7"
    assert result.diagnostics == (
        "parameter-duplicate-name",
        "parameter-target-unsupported",
    )
    assert parse_notebook_parameters_v2("broken = [").diagnostics == (
        "parameter-syntax-unavailable",
    )


def test_output_parser_sanitizes_all_derived_surfaces_and_never_confirms_metric() -> None:
    html_output = parse_notebook_output_v2(
        {
            "output_type": "display_data",
            "data": {
                "text/html": "<script>secret()</script><b>score=0.9</b>",
                "image/png": "binary-payload",
            },
        }
    )
    assert html_output.artifact_type == NotebookArtifactType.DISPLAY
    assert html_output.text.strip() == "score=0.9"
    assert html_output.binary_omitted is True
    assert html_output.metric_confirmed is False
    assert "binary-payload" not in html_output.text
    assert "html-sanitized" in html_output.diagnostics

    for value in (
        "/Users/alice/private/data.csv",
        r"\\server\share\private.csv",
        "%5C%5Cserver%5Cshare%5Cprivate.csv",
        "AKIAIOSFODNN7EXAMPLE",
    ):
        parsed = parse_notebook_output_v2(
            {"output_type": "stream", "name": "stdout", "text": value}
        )
        assert value not in parsed.text
        assert parsed.metric_confirmed is False

    error = parse_notebook_output_v2(
        {
            "output_type": "error",
            "ename": "ValueError",
            "evalue": "bad /private/var/token.txt",
            "traceback": ["ValueError: bad"],
        }
    )
    assert error.artifact_type == NotebookArtifactType.ERROR
    assert "/private/var/token.txt" not in error.text
    assert error.error_name == "ValueError"


def test_execution_state_separates_display_order_and_fails_closed_on_restart() -> None:
    normal = project_notebook_execution_state_v2(
        (
            NotebookCellExecutionInput(0, "code", 1, False, False),
            NotebookCellExecutionInput(1, "markdown", None, False, False),
            NotebookCellExecutionInput(2, "code", 3, True, False),
            NotebookCellExecutionInput(3, "code", None, True, False),
        )
    )
    assert [item.display_order for item in normal] == [0, 1, 2, 3]
    assert [item.execution_order for item in normal] == [0, None, 1, None]
    assert "execution-count-gap-before" in normal[2].diagnostics
    assert normal[3].stale is True
    assert normal[3].state == NotebookCellExecutionState.NOT_EXECUTED

    restarted = project_notebook_execution_state_v2(
        (
            NotebookCellExecutionInput(0, "code", 8, False, False),
            NotebookCellExecutionInput(1, "code", 1, False, False),
            NotebookCellExecutionInput(2, "code", 1, False, False),
        )
    )
    assert all(item.execution_order is None for item in restarted)
    assert all("kernel-restart-or-reordered" in item.diagnostics for item in restarted)
    assert "duplicate-execution-count" in restarted[1].diagnostics
    assert "duplicate-execution-count" in restarted[2].diagnostics


def test_code_analyzer_and_dependency_graph_record_uncertainty_without_paths() -> None:
    analysis = analyze_notebook_code_v2(
        "\n".join(
            (
                "import pandas as pd",
                "records = pd.read_csv('/Users/alice/private.csv')",
                "score = len(records)",
                "exec(dynamic_code)",
            )
        ),
        language="python",
    )
    assert analysis.parser_available is True
    assert {"pd", "records", "score"}.issubset(analysis.definitions)
    assert "dynamic_code" in analysis.reads
    assert "pandas" in analysis.imports
    assert {"read_csv", "len", "exec"}.issubset(analysis.calls)
    assert analysis.external_dependency_refs == (
        "external-35afe8f86a48cc71db4f64fa7422b27d6b6404ea1ff90c5f0b341a4c9e2cfab5",
    )
    assert "/Users/alice" not in repr(analysis)
    assert "dynamic-execution-unresolved" in analysis.diagnostics

    magic = analyze_notebook_code_v2("!pip install unsafe", language="python")
    assert magic.parser_available is False
    assert "magic-or-shell-unresolved" in magic.diagnostics
    assert "python-parse-unavailable" in magic.diagnostics
    unsupported = analyze_notebook_code_v2("x = 1", language="r")
    assert unsupported.parser_available is False
    assert unsupported.diagnostics == ("language-unsupported",)

    fixture = build_notebook_fixture_v1()
    retry = fixture.publications[1]
    assert retry.symbols
    assert retry.edges
    cell_ids = {item.cell_version_id for item in retry.cell_versions}
    dependency_edges = tuple(
        item for item in retry.edges if item.edge_type in {"depends_on", "redefines"}
    )
    assert all(
        item.source_id in cell_ids and item.target_id in cell_ids for item in dependency_edges
    )
    assert dependency_edges
    assert {"contains", "executes", "produces"}.issubset({item.edge_type for item in retry.edges})
    assert any(item.role == "definition" and item.name == "score" for item in retry.symbols)


def test_adapter_marks_bool_execution_count_untrusted_and_persists_graph(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {"language_info": {"name": "python"}},
            "cells": [
                {
                    "id": "producer",
                    "cell_type": "code",
                    "metadata": {},
                    "source": "value = 7",
                    "execution_count": 1,
                    "outputs": [],
                },
                {
                    "id": "redefinition",
                    "cell_type": "code",
                    "metadata": {},
                    "source": "value = 8",
                    "execution_count": 2,
                    "outputs": [],
                },
                {
                    "id": "consumer",
                    "cell_type": "code",
                    "metadata": {},
                    "source": "print(value)",
                    "execution_count": True,
                    "outputs": [{"output_type": "stream", "name": "stdout", "text": "7"}],
                },
            ],
        },
        separators=(",", ":"),
    ).encode()
    publication = NotebookAdapterV2().build_publication(
        payload=payload,
        project_id="project-notebook-test",
        acl_ref="public",
        generation_id="nbgen-test",
        source_key="bool-count.ipynb",
        source_version="v1",
        execution_key="execution-1",
    )
    consumer = publication.cell_executions[2]
    assert consumer.execution_count is None
    assert consumer.execution_order is None
    assert consumer.stale is True
    assert consumer.state == NotebookCellExecutionState.NOT_EXECUTED
    assert publication.edges
    assert "redefines" in {item.edge_type for item in publication.edges}

    store = NotebookV2Store(
        tmp_path / "notebook-v2.sqlite3",
        isolated_root=tmp_path,
    )
    store.initialize()
    result = store.publish(publication)
    assert result["counts"]["notebook_symbols"] == len(publication.symbols)
    assert result["counts"]["notebook_edges"] == len(publication.edges)
    assert result["database_sidecars"] == ()


def test_n2_n3_changes_preserve_released_denominators_and_available_perfect_report() -> None:
    released = load_notebook_golden_v1()
    report = evaluate_reviewed_notebook_retrieval(
        perfect_reviewed_rows_v1(released),
        dataset=released,
    )
    assert len(released.cases) == 40
    assert all(item.status == NotebookMetricStatus.AVAILABLE for item in report.metrics)
    assert all(item.numerator == item.eligible_denominator for item in report.metrics)
